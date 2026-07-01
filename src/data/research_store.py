"""Per-task research manifest store (M6a) — the MD the operator talks to.

Raw-sqlite with explicit commit() (same discipline as agent_jobs): the research
runner writes on a worker thread and reads may come from the MCP subprocess, so
each call opens/uses its own connection and commits explicitly. Enablement lane:
task_research holds ONLY operator-chosen research output — no PHI/warehouse.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

_UPDATABLE = {"title", "summary", "markdown", "status", "job_id",
              "tokens_in", "tokens_out", "cost_usd"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex


def create_research(conn, *, task_id: str, session_id: str | None = None,
                    job_id: str | None = None, title: str | None = None,
                    summary: str | None = None, status: str = "running",
                    created_by: str = "agent") -> str:
    """Insert a research row (defaults to 'running'). Returns the research_id."""
    rid = _uid()
    now = _now()
    conn.execute(
        """INSERT INTO task_research
           (research_id, task_id, session_id, job_id, title, summary, markdown,
            status, created_by, created_at, updated_at)
           VALUES (?,?,?,?,?,?,'',?,?,?,?)""",
        (rid, task_id, session_id, job_id, title, summary, status, created_by, now, now),
    )
    conn.commit()
    return rid


def update_research(conn, research_id: str, **fields) -> bool:
    """Patch whitelisted fields (status/markdown/summary/tokens/...); touches updated_at."""
    sets = {k: v for k, v in fields.items() if k in _UPDATABLE}
    if not sets:
        return False
    cols = ", ".join(f"{k} = ?" for k in sets)
    params = list(sets.values()) + [_now(), research_id]
    cur = conn.execute(
        f"UPDATE task_research SET {cols}, updated_at = ? WHERE research_id = ?", params)
    conn.commit()
    return cur.rowcount > 0


def get_research(conn, research_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM task_research WHERE research_id = ?", (research_id,)).fetchone()
    return dict(row) if row else None


def latest_for_task(conn, task_id: str) -> dict | None:
    """The newest research row for a task — the RAG-next-turn read for Renn."""
    row = conn.execute(
        "SELECT * FROM task_research WHERE task_id = ? "
        "ORDER BY updated_at DESC, rowid DESC LIMIT 1", (task_id,)).fetchone()
    return dict(row) if row else None


def list_research(conn, session_id: str | None = None, limit: int = 50) -> list[dict]:
    if session_id:
        rows = conn.execute(
            "SELECT * FROM task_research WHERE session_id = ? "
            "ORDER BY updated_at DESC LIMIT ?", (session_id, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM task_research ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]
