"""Enablement task + subtask service — the task spine for the Enablement Workbench.

Every monitor and manual entry creates tasks through here so dedup (no duplicate
task when a source is re-polled) is enforced in one place. All functions take a
``sqlite3.Connection`` from ``connection_factory.get_connection()``.

See migrations/027_enablement.sql and
~/.claude/plans/guru-enablement-redesign.md (§5, §6).
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from datetime import datetime, timezone

from src.data.connection_factory import atomic

VALID_STATUS = {"open", "in_progress", "done", "dismissed"}
VALID_PRIORITY = {"low", "normal", "high"}

# Columns update_task() is allowed to set (guards against SQL injection via **fields).
_UPDATABLE = {
    "status", "priority", "due_date", "summary", "title",
    "assignee", "scratchpad", "draft_id", "source_url", "kind",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex


def dedup_key(source: str, source_ref: str | None, title: str) -> str:
    """Stable key so re-polling the same source item doesn't spawn a duplicate."""
    raw = f"{source}|{source_ref or ''}|{title}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


# ── tasks ────────────────────────────────────────────────────────────

def create_task(
    conn: sqlite3.Connection,
    *,
    source: str,
    kind: str,
    title: str,
    source_ref: str | None = None,
    source_url: str | None = None,
    summary: str | None = None,
    due_date: str | None = None,
    priority: str = "normal",
    status: str = "open",
    draft_id: int | None = None,
    llm_rationale: str | None = None,
    created_by: str = "agent",
    key: str | None = None,
) -> str:
    """Create a task (idempotent on dedup_key). Returns the task_id.

    If a task with the same dedup_key already exists, its id is returned and no
    new row is created — so a monitor can call this every poll cycle safely.
    """
    key = key or dedup_key(source, source_ref, title)
    tid = _uid()
    now = _now()
    with atomic(conn):
        conn.execute(
            """INSERT INTO enablement_tasks
               (task_id, source, source_ref, source_url, kind, title, summary,
                due_date, priority, status, draft_id, llm_rationale, dedup_key,
                created_by, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(dedup_key) DO NOTHING""",
            (tid, source, source_ref, source_url, kind, title, summary,
             due_date, priority, status, draft_id, llm_rationale, key,
             created_by, now, now),
        )
    row = conn.execute(
        "SELECT task_id FROM enablement_tasks WHERE dedup_key = ?", (key,)
    ).fetchone()
    return row[0] if row else tid


def get_task(conn: sqlite3.Connection, task_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM enablement_tasks WHERE task_id = ?", (task_id,)
    ).fetchone()
    return dict(row) if row else None


def update_task(conn: sqlite3.Connection, task_id: str, **fields) -> bool:
    """Update whitelisted task fields. status='done' also stamps completed_at."""
    sets = {k: v for k, v in fields.items() if k in _UPDATABLE}
    if not sets:
        return False
    now = _now()
    cols = ", ".join(f"{k} = ?" for k in sets)
    params = list(sets.values())
    extra = ", updated_at = ?"
    params.append(now)
    if sets.get("status") == "done":
        extra += ", completed_at = ?"
        params.append(now)
    params.append(task_id)
    with atomic(conn):
        cur = conn.execute(
            f"UPDATE enablement_tasks SET {cols}{extra} WHERE task_id = ?", params
        )
    return cur.rowcount > 0


def list_tasks(
    conn: sqlite3.Connection,
    *,
    source: str | None = None,
    status: str | None = None,
    kind: str | None = None,
    due_before: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """List tasks, newest first, with optional filters. Subtask counts included."""
    where, params = [], []
    if source:
        where.append("source = ?"); params.append(source)
    if status:
        where.append("status = ?"); params.append(status)
    if kind:
        where.append("kind = ?"); params.append(kind)
    if due_before:
        where.append("due_date IS NOT NULL AND due_date <= ?"); params.append(due_before)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM enablement_tasks{clause} "
        f"ORDER BY created_at DESC LIMIT ?", params
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        counts = conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(done),0) AS done "
            "FROM enablement_subtasks WHERE task_id = ?", (d["task_id"],)
        ).fetchone()
        d["subtask_total"] = counts["total"]
        d["subtask_done"] = counts["done"]
        out.append(d)
    return out


def set_scratchpad(conn: sqlite3.Connection, task_id: str, text: str) -> bool:
    return update_task(conn, task_id, scratchpad=text)


# ── subtasks ─────────────────────────────────────────────────────────

def add_subtask(
    conn: sqlite3.Connection,
    task_id: str,
    text: str,
    *,
    created_by: str = "user",
    done: bool = False,
) -> str:
    """Append a checklist item to a task. Returns the subtask_id."""
    sid = _uid()
    now = _now()
    with atomic(conn):
        nxt = conn.execute(
            "SELECT COALESCE(MAX(ordinal), -1) + 1 FROM enablement_subtasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO enablement_subtasks
               (subtask_id, task_id, text, done, ordinal, created_by, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (sid, task_id, text, 1 if done else 0, nxt, created_by, now, now),
        )
    return sid


def draft_subtasks(
    conn: sqlite3.Connection, task_id: str, items: list[str], *, created_by: str = "agent"
) -> list[str]:
    """Bulk-insert a checklist (e.g. an AI-drafted task breakdown). Returns ids."""
    return [add_subtask(conn, task_id, t, created_by=created_by) for t in items if t.strip()]


def toggle_subtask(conn: sqlite3.Connection, subtask_id: str, done: bool) -> bool:
    with atomic(conn):
        cur = conn.execute(
            "UPDATE enablement_subtasks SET done = ?, updated_at = ? WHERE subtask_id = ?",
            (1 if done else 0, _now(), subtask_id),
        )
    return cur.rowcount > 0


def list_subtasks(conn: sqlite3.Connection, task_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM enablement_subtasks WHERE task_id = ? ORDER BY ordinal ASC",
        (task_id,),
    ).fetchall()
    return [dict(r) for r in rows]
