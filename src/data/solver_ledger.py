"""Store API for the solver ledger (migration 052).

Per-call / per-tool audit spine for background solver jobs: solver_calls
(one row per Claude CLI invocation, inserted BEFORE the subprocess spawns),
solver_tool_execs (MCP tools run inside a call — the watermark target),
solver_stage_results (+ solver_result_calls provenance join), solver_drafts
(+ solver_citations) in a human-gated state machine, and solver_checks
(every check row names the action its consumer took).

Enablement lane — no PHI/warehouse data.

Raw-sqlite with explicit commit() (same discipline as agent_jobs): callers
pass a connection (get_connection in the main process, or a raw subprocess
sqlite3 connection). We use explicit conn.commit() — never atomic() — so the
same functions work on both connection kinds and never open a nested
transaction.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone

# ── vocabularies (Python-side; no CHECK constraints in the schema) ─────────

STAGES = frozenset({"frame", "research", "review", "sufficiency", "draft", "execute"})
CALL_PURPOSES = frozenset({"worker", "verifier", "repair"})
CALL_STATUSES = frozenset({"spawned", "done", "error", "killed", "interrupted"})
_CALL_TERMINAL = frozenset({"done", "error", "killed", "interrupted"})
ERROR_CLASSES = frozenset({"transport", "contract", "semantic", "environmental"})
RESULT_STATUSES = frozenset({"pass", "fail", "parked"})
CHECK_KINDS = frozenset({"deterministic", "llm_verifier"})
CHECK_VERDICTS = frozenset({"pass", "fail", "unknown"})
CONSUMER_ACTIONS = frozenset({"advanced", "repaired", "parked"})
DRAFT_KINDS = frozenset({"edit", "create", "removal"})
DRAFT_STATUSES = frozenset({"pending", "ready", "executed", "rejected"})
SOURCE_KINDS = frozenset({"drive", "corpus", "zendesk_mirror", "guru", "asana"})
TOOL_RESULT_STATUSES = frozenset({"ok", "error"})

# Allowed draft transitions (executed / rejected are terminal-immutable —
# same shape as zendesk_store's matrix).
_DRAFT_TRANSITIONS = frozenset({
    ("pending", "ready"),
    ("ready", "pending"),
    ("ready", "executed"),
    ("pending", "rejected"),
    ("ready", "rejected"),
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex


def _sha(text: str | None) -> str | None:
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def _safe_rollback(conn: sqlite3.Connection) -> None:
    try:
        conn.rollback()
    except sqlite3.Error:
        pass


def _require(value: str, allowed: frozenset, label: str) -> None:
    if value not in allowed:
        raise ValueError(f"unknown {label} {value!r}")


# ── calls ──────────────────────────────────────────────────────────────────

def begin_call(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    stage: str,
    attempt: int = 1,
    purpose: str = "worker",
    bridge_instance_id: str | None = None,
    request_id: str | None = None,
    model: str | None = None,
    bedrock_active: bool = False,
    prompt_text: str | None = None,
) -> str:
    """Insert the call row BEFORE the subprocess spawns; returns call_id.

    A killed, hung, or crashed call therefore still leaves its trace: rows
    stuck in status 'spawned' are the reaper's signal.
    """
    _require(stage, STAGES, "stage")
    _require(purpose, CALL_PURPOSES, "purpose")
    call_id = _uid()
    conn.execute(
        """INSERT INTO solver_calls
           (call_id, job_id, stage, attempt, purpose, bridge_instance_id,
            request_id, model, bedrock_active, prompt_sha, prompt_text,
            status, started_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'spawned', ?)""",
        (call_id, job_id, stage, int(attempt), purpose, bridge_instance_id,
         request_id, model, 1 if bedrock_active else 0, _sha(prompt_text),
         prompt_text, _now()),
    )
    conn.commit()
    return call_id


def finish_call(
    conn: sqlite3.Connection,
    call_id: str,
    *,
    status: str = "done",
    response_text: str | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    cost_usd: float | None = None,
    duration_ms: int | None = None,
    stop_reason: str | None = None,
    error_class: str | None = None,
    salvage_applied: bool = False,
) -> bool:
    """Single-winner completion: only a 'spawned' row can be finished."""
    _require(status, _CALL_TERMINAL, "terminal call status")
    if error_class is not None:
        _require(error_class, ERROR_CLASSES, "error class")
    cur = conn.execute(
        """UPDATE solver_calls
           SET status = ?, response_sha = ?, response_text = ?, tokens_in = ?,
               tokens_out = ?, cost_usd = ?, duration_ms = ?, stop_reason = ?,
               error_class = ?, salvage_applied = ?, finished_at = ?
           WHERE call_id = ? AND status = 'spawned'""",
        (status, _sha(response_text), response_text, tokens_in, tokens_out,
         cost_usd, duration_ms, stop_reason, error_class,
         1 if salvage_applied else 0, _now(), call_id),
    )
    conn.commit()
    return cur.rowcount == 1


def mark_interrupted_calls(conn: sqlite3.Connection, job_id: str | None = None) -> int:
    """Reaper: flip lingering 'spawned' rows to 'interrupted'; returns count."""
    if job_id is None:
        cur = conn.execute(
            "UPDATE solver_calls SET status = 'interrupted', finished_at = ? "
            "WHERE status = 'spawned'",
            (_now(),),
        )
    else:
        cur = conn.execute(
            "UPDATE solver_calls SET status = 'interrupted', finished_at = ? "
            "WHERE status = 'spawned' AND job_id = ?",
            (_now(), job_id),
        )
    conn.commit()
    return cur.rowcount


def list_calls(conn: sqlite3.Connection, job_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM solver_calls WHERE job_id = ? ORDER BY started_at, rowid",
        (job_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ── tool executions + watermark ────────────────────────────────────────────

def record_tool_exec(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    tool_name: str,
    call_id: str | None = None,
    args_digest: str | None = None,
    result_status: str = "ok",
) -> int:
    """One row per MCP tool the subprocess ran; returns the monotonic exec_id."""
    _require(result_status, TOOL_RESULT_STATUSES, "result status")
    cur = conn.execute(
        """INSERT INTO solver_tool_execs
           (job_id, call_id, tool_name, args_digest, result_status, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (job_id, call_id, tool_name, args_digest, result_status, _now()),
    )
    conn.commit()
    return int(cur.lastrowid)


def snapshot_watermark(conn: sqlite3.Connection, job_id: str) -> dict:
    """Snapshot the tool ledger for a job BEFORE a CLI call spawns.

    Captures (max exec_id, row count, anchor id) so resolve_watermark can
    tell 'no new rows' from 'the ledger changed underneath us'.
    """
    row = conn.execute(
        "SELECT COALESCE(MAX(exec_id), 0) AS max_id, COUNT(*) AS n "
        "FROM solver_tool_execs WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    return {"max_id": int(row["max_id"]), "count": int(row["n"]),
            "anchor_id": int(row["max_id"]) if row["n"] else None}


def resolve_watermark(conn: sqlite3.Connection, job_id: str, mark: dict) -> dict:
    """Resolve a snapshot after the call exits.

    Returns {"status": "confirmed_ran" | "confirmed_empty" | "unknown",
    "rows": [...]}. Any arithmetic or anchor mismatch degrades to "unknown"
    — never a false accusation; the caller decides what unknown means
    (solver gates park on it).
    """
    row = conn.execute(
        "SELECT COALESCE(MAX(exec_id), 0) AS max_id, COUNT(*) AS n "
        "FROM solver_tool_execs WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    cur_max, cur_n = int(row["max_id"]), int(row["n"])
    if cur_n < int(mark.get("count", 0)):
        return {"status": "unknown", "rows": []}
    anchor = mark.get("anchor_id")
    if anchor is not None:
        hit = conn.execute(
            "SELECT 1 FROM solver_tool_execs WHERE job_id = ? AND exec_id = ?",
            (job_id, int(anchor)),
        ).fetchone()
        if hit is None:
            return {"status": "unknown", "rows": []}
    new_rows = conn.execute(
        "SELECT * FROM solver_tool_execs WHERE job_id = ? AND exec_id > ? "
        "ORDER BY exec_id",
        (job_id, int(mark.get("max_id", 0))),
    ).fetchall()
    expected_new = cur_n - int(mark.get("count", 0))
    if len(new_rows) != expected_new:
        return {"status": "unknown", "rows": []}
    if not new_rows:
        return {"status": "confirmed_empty", "rows": []}
    return {"status": "confirmed_ran", "rows": [dict(r) for r in new_rows]}


# ── stage results ──────────────────────────────────────────────────────────

def record_stage_result(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    stage: str,
    attempt: int = 1,
    status: str = "pass",
    payload: dict | None = None,
    call_ids: tuple[str, ...] | list[str] = (),
) -> str:
    """Persist a stage output with provenance to the calls that produced it."""
    _require(stage, STAGES, "stage")
    _require(status, RESULT_STATUSES, "result status")
    result_id = _uid()
    try:
        conn.execute(
            """INSERT INTO solver_stage_results
               (result_id, job_id, stage, attempt, status, payload_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (result_id, job_id, stage, int(attempt), status,
             json.dumps(payload) if payload is not None else None, _now()),
        )
        for cid in call_ids:
            conn.execute(
                "INSERT INTO solver_result_calls (result_id, call_id) VALUES (?, ?)",
                (result_id, cid),
            )
        conn.commit()
    except sqlite3.Error:
        _safe_rollback(conn)
        raise
    return result_id


def has_passing_result(conn: sqlite3.Connection, job_id: str, stage: str) -> bool:
    """Resume's requeue guard: skip stages that already passed."""
    _require(stage, STAGES, "stage")
    row = conn.execute(
        "SELECT 1 FROM solver_stage_results "
        "WHERE job_id = ? AND stage = ? AND status = 'pass' LIMIT 1",
        (job_id, stage),
    ).fetchone()
    return row is not None


def attempts(conn: sqlite3.Connection, job_id: str, stage: str) -> int:
    """Authoritative attempt counter — a query, not program state."""
    _require(stage, STAGES, "stage")
    row = conn.execute(
        "SELECT COALESCE(MAX(attempt), 0) AS n FROM solver_calls "
        "WHERE job_id = ? AND stage = ?",
        (job_id, stage),
    ).fetchone()
    return int(row["n"])


def spend(conn: sqlite3.Connection, job_id: str | None = None) -> float:
    """Summed cost over the ledger (crash-safe budget source of truth)."""
    if job_id is None:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) AS c FROM solver_calls"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) AS c FROM solver_calls "
            "WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    return float(row["c"])


# ── checks ─────────────────────────────────────────────────────────────────

def record_check(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    stage: str,
    name: str,
    verdict: str,
    consumer_action: str,
    attempt: int = 1,
    kind: str = "deterministic",
    call_id: str | None = None,
    reason: str | None = None,
) -> str:
    """A check row cannot be written without the action its consumer took."""
    _require(stage, STAGES, "stage")
    _require(verdict, CHECK_VERDICTS, "verdict")
    _require(consumer_action, CONSUMER_ACTIONS, "consumer action")
    _require(kind, CHECK_KINDS, "check kind")
    check_id = _uid()
    conn.execute(
        """INSERT INTO solver_checks
           (check_id, job_id, stage, attempt, call_id, name, kind, verdict,
            reason, consumer_action, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (check_id, job_id, stage, int(attempt), call_id, name, kind, verdict,
         reason, consumer_action, _now()),
    )
    conn.commit()
    return check_id


def list_checks(conn: sqlite3.Connection, job_id: str, stage: str | None = None) -> list[dict]:
    if stage is None:
        rows = conn.execute(
            "SELECT * FROM solver_checks WHERE job_id = ? ORDER BY created_at, rowid",
            (job_id,),
        ).fetchall()
    else:
        _require(stage, STAGES, "stage")
        rows = conn.execute(
            "SELECT * FROM solver_checks WHERE job_id = ? AND stage = ? "
            "ORDER BY created_at, rowid",
            (job_id, stage),
        ).fetchall()
    return [dict(r) for r in rows]


# ── drafts + citations ─────────────────────────────────────────────────────

def create_draft(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    kind: str,
    title: str,
    content_md: str,
    rationale: str,
    target_kind: str | None = None,
    target_ref: str | None = None,
    summary: str | None = None,
) -> str:
    """Drafts land as 'pending' only; rationale is mandatory."""
    _require(kind, DRAFT_KINDS, "draft kind")
    if not (rationale or "").strip():
        raise ValueError("rationale is mandatory for solver drafts")
    if not (title or "").strip():
        raise ValueError("title is mandatory for solver drafts")
    draft_id = _uid()
    ts = _now()
    conn.execute(
        """INSERT INTO solver_drafts
           (draft_id, job_id, kind, target_kind, target_ref, title, content_md,
            rationale, summary, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (draft_id, job_id, kind, target_kind, target_ref, title, content_md,
         rationale, summary, ts, ts),
    )
    conn.commit()
    return draft_id


def set_draft_status(
    conn: sqlite3.Connection,
    draft_id: str,
    new_status: str,
    *,
    expected: str | None = None,
) -> str:
    """Enforced transition matrix; returns
    'ok' | 'not_found' | 'invalid_transition' | 'status_changed'.

    executed / rejected are immutable. With expected=, a compare-and-set:
    drift since the caller last read the row returns 'status_changed'.
    """
    _require(new_status, DRAFT_STATUSES, "draft status")
    row = conn.execute(
        "SELECT status FROM solver_drafts WHERE draft_id = ?", (draft_id,)
    ).fetchone()
    if row is None:
        return "not_found"
    current = row["status"]
    if expected is not None and current != expected:
        return "status_changed"
    if (current, new_status) not in _DRAFT_TRANSITIONS:
        return "invalid_transition"
    ts = _now()
    if new_status == "executed":
        cur = conn.execute(
            "UPDATE solver_drafts SET status = ?, updated_at = ?, executed_at = ? "
            "WHERE draft_id = ? AND status = ?",
            (new_status, ts, ts, draft_id, current),
        )
    else:
        cur = conn.execute(
            "UPDATE solver_drafts SET status = ?, updated_at = ? "
            "WHERE draft_id = ? AND status = ?",
            (new_status, ts, draft_id, current),
        )
    conn.commit()
    return "ok" if cur.rowcount == 1 else "status_changed"


def get_draft(conn: sqlite3.Connection, draft_id: str) -> dict | None:
    return _row_to_dict(
        conn.execute(
            "SELECT * FROM solver_drafts WHERE draft_id = ?", (draft_id,)
        ).fetchone()
    )


def list_drafts(
    conn: sqlite3.Connection,
    job_id: str | None = None,
    status: str | None = None,
) -> list[dict]:
    if status is not None:
        _require(status, DRAFT_STATUSES, "draft status")
    clauses, params = [], []
    if job_id is not None:
        clauses.append("job_id = ?")
        params.append(job_id)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM solver_drafts {where} ORDER BY created_at, rowid",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def add_citation(
    conn: sqlite3.Connection,
    *,
    draft_id: str,
    source_kind: str,
    source_ref: str,
    quote: str | None = None,
    overlap_score: float | None = None,
) -> str:
    _require(source_kind, SOURCE_KINDS, "source kind")
    if not (source_ref or "").strip():
        raise ValueError("source_ref is mandatory for citations")
    citation_id = _uid()
    conn.execute(
        """INSERT INTO solver_citations
           (citation_id, draft_id, source_kind, source_ref, quote, overlap_score)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (citation_id, draft_id, source_kind, source_ref, quote, overlap_score),
    )
    conn.commit()
    return citation_id


def list_citations(conn: sqlite3.Connection, draft_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM solver_citations WHERE draft_id = ? ORDER BY rowid",
        (draft_id,),
    ).fetchall()
    return [dict(r) for r in rows]
