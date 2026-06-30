"""Agent jobs — the M4 job spine for the standalone Agent chat.

A "job" is a tracked, multi-phase unit of work the Agent externalizes so the
sidebar can show live progress: a header (``agent_jobs``) + ordered steps
(``agent_job_steps``). Fully local + decoupled — no ticket/warehouse reads. Jobs
are scoped to the chat session that created them.

Writes run inside the MCP subprocess on a *raw* ``sqlite3`` connection (not a
connection_factory one), so we use explicit ``commit()``/``rollback()`` rather
than ``atomic()`` (which assumes an autocommit connection). Reads work on either
connection — both use the ``Row`` factory.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime

_JOB_STATUS = {"running", "done", "error", "cancelled"}
_STEP_STATUS = {"pending", "running", "done", "error", "skipped"}
_TERMINAL = {"done", "error", "cancelled"}

_JOB_FIELDS = {"status", "progress_pct", "summary",
               "tokens_in", "tokens_out", "cost_usd", "error"}
_STEP_FIELDS = {"status", "detail"}


def _now() -> str:
    return datetime.utcnow().isoformat()


def _uid() -> str:
    return uuid.uuid4().hex


def _row_to_dict(row) -> dict:
    if isinstance(row, sqlite3.Row):
        return {k: row[k] for k in row.keys()}
    return dict(row) if hasattr(row, "keys") else {}


def create_job(conn, *, title: str, kind: str | None = None,
               session_id: str | None = None, steps=None,
               created_by: str = "agent") -> str:
    """Create a job (+ optional ordered step names). Returns the job_id."""
    job_id = _uid()
    now = _now()
    try:
        conn.execute(
            """INSERT INTO agent_jobs
               (job_id, session_id, title, kind, status, progress_pct,
                created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'running', 0, ?, ?, ?)""",
            (job_id, session_id, title, kind, created_by, now, now),
        )
        for i, name in enumerate(steps or []):
            conn.execute(
                """INSERT INTO agent_job_steps
                   (step_id, job_id, ordinal, name, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
                (_uid(), job_id, i, str(name), now, now),
            )
        conn.commit()
    except Exception:
        _safe_rollback(conn)
        raise
    return job_id


def get_job(conn, job_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM agent_jobs WHERE job_id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    job = _row_to_dict(row)
    job["steps"] = list_steps(conn, job_id)
    return job


def update_job(conn, job_id: str, **fields) -> bool:
    """Update whitelisted job fields. Auto-touches updated_at; sets completed_at
    when the status becomes terminal (done/error/cancelled)."""
    sets = {k: v for k, v in fields.items() if k in _JOB_FIELDS}
    if not sets:
        return False
    now = _now()
    cols = ", ".join(f"{k} = ?" for k in sets)
    params = list(sets.values())
    extra = ""
    if sets.get("status") in _TERMINAL:
        extra = ", completed_at = ?"
        params.append(now)
    params += [now, job_id]
    try:
        cur = conn.execute(
            f"UPDATE agent_jobs SET {cols}{extra}, updated_at = ? WHERE job_id = ?",
            params)
        conn.commit()
    except Exception:
        _safe_rollback(conn)
        raise
    return cur.rowcount > 0


def add_step(conn, job_id: str, name: str, *, ordinal: int | None = None) -> str:
    now = _now()
    if ordinal is None:
        r = conn.execute(
            "SELECT COALESCE(MAX(ordinal), -1) FROM agent_job_steps WHERE job_id = ?",
            (job_id,)).fetchone()
        ordinal = (r[0] if r else -1) + 1
    step_id = _uid()
    try:
        conn.execute(
            """INSERT INTO agent_job_steps
               (step_id, job_id, ordinal, name, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
            (step_id, job_id, ordinal, str(name), now, now))
        conn.commit()
    except Exception:
        _safe_rollback(conn)
        raise
    return step_id


def update_step(conn, step_id: str, **fields) -> bool:
    """Update a step's status/detail and re-derive the parent job's progress."""
    sets = {k: v for k, v in fields.items() if k in _STEP_FIELDS}
    if not sets:
        return False
    now = _now()
    cols = ", ".join(f"{k} = ?" for k in sets)
    params = list(sets.values()) + [now, step_id]
    try:
        cur = conn.execute(
            f"UPDATE agent_job_steps SET {cols}, updated_at = ? WHERE step_id = ?",
            params)
        if cur.rowcount:
            _recompute_progress(conn, step_id)
        conn.commit()
    except Exception:
        _safe_rollback(conn)
        raise
    return cur.rowcount > 0


def list_steps(conn, job_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM agent_job_steps WHERE job_id = ? ORDER BY ordinal",
        (job_id,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_jobs(conn, *, session_id: str | None = None,
              status: str | None = None, limit: int = 50) -> list[dict]:
    """List jobs (newest-updated first), each with its nested steps."""
    where, params = [], []
    if session_id is not None:
        where.append("session_id = ?")
        params.append(session_id)
    if status is not None:
        where.append("status = ?")
        params.append(status)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    params.append(int(limit))
    rows = conn.execute(
        f"SELECT * FROM agent_jobs{clause} "
        f"ORDER BY updated_at DESC, created_at DESC LIMIT ?", params).fetchall()
    jobs = [_row_to_dict(r) for r in rows]
    for j in jobs:
        j["steps"] = list_steps(conn, j["job_id"])
    return jobs


def _recompute_progress(conn, step_id: str) -> None:
    row = conn.execute(
        "SELECT job_id FROM agent_job_steps WHERE step_id = ?", (step_id,)).fetchone()
    if not row:
        return
    job_id = row[0]
    counts = conn.execute(
        "SELECT COUNT(*), "
        "SUM(CASE WHEN status IN ('done','skipped') THEN 1 ELSE 0 END) "
        "FROM agent_job_steps WHERE job_id = ?", (job_id,)).fetchone()
    total = counts[0] or 0
    done = counts[1] or 0
    pct = int(round(done * 100 / total)) if total else 0
    conn.execute(
        "UPDATE agent_jobs SET progress_pct = ?, updated_at = ? WHERE job_id = ?",
        (pct, _now(), job_id))


def _safe_rollback(conn) -> None:
    try:
        conn.rollback()
    except Exception:  # noqa: BLE001
        pass
