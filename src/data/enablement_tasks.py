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
    "status", "priority", "due_date", "summary", "title", "description",
    "assignee", "assignee_gid", "submitter", "scratchpad", "draft_id",
    "source_url", "kind", "remote_modified_at", "parent_task_ref",
    "brief_json", "brief_status", "brief_source_modified_at",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex


def dedup_key(source: str, source_ref: str | None, title: str) -> str:
    """Stable key so re-polling the same source item doesn't spawn a duplicate.

    Non-cryptographic identity hash — collision resistance for dedup only.
    """
    raw = f"{source}|{source_ref or ''}|{title}".encode("utf-8")
    return hashlib.sha1(raw, usedforsecurity=False).hexdigest()


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
    description: str | None = None,
    submitter: str | None = None,
    due_date: str | None = None,
    priority: str = "normal",
    status: str = "open",
    draft_id: int | None = None,
    llm_rationale: str | None = None,
    created_by: str = "agent",
    key: str | None = None,
    board_source_id: str | None = None,
    parent_task_ref: str | None = None,
) -> str:
    """Create a task (idempotent on dedup_key). Returns the task_id.

    If a task with the same dedup_key already exists, its id is returned and no
    new row is created — so a monitor can call this every poll cycle safely.

    ``parent_task_ref`` is the Asana gid of the PARENT task when this row is a
    promoted subtask (migration 056); NULL for normal tasks. A row is a subtask
    iff parent_task_ref IS NOT NULL.

    ``board_source_id`` is IMMUTABLE PROVENANCE — the monitor_sources.source_id
    of the board whose poll created this row (migration 054). It is stored,
    never derived, and deliberately absent from ``_UPDATABLE`` so no later edit,
    tool call or reconcile can rewrite it.

    It is NOT the attribution authority. Ownership is many-to-many (a
    multi-homed Asana task is polled by every board it sits in) and lives in
    ``task_board_links`` (migration 055) — see :func:`link_task_board`. Nothing
    reads this column to decide what a board owns, counts, shows or deletes.
    """
    key = key or dedup_key(source, source_ref, title)
    tid = _uid()
    now = _now()
    with atomic(conn):
        conn.execute(
            """INSERT INTO enablement_tasks
               (task_id, source, source_ref, source_url, kind, title, summary,
                description, submitter, due_date, priority, status, draft_id,
                llm_rationale, dedup_key, created_by, created_at, updated_at,
                board_source_id, parent_task_ref)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(dedup_key) DO NOTHING""",
            (tid, source, source_ref, source_url, kind, title, summary,
             description, submitter, due_date, priority, status, draft_id,
             llm_rationale, key, created_by, now, now, board_source_id,
             parent_task_ref),
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
    assignee_gid: str | None = None,
    assignee: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """List tasks, newest first, with optional filters. Subtask counts included.

    ``assignee_gid``/``assignee`` implement the "only mine" scope: a task matches
    on the Asana GID (unambiguous) or, for legacy/manual rows with no GID, a
    case-insensitive exact match of the display name/email. Pass neither for
    "all" (the show-all toggle) — never string-interpolate, always parameterized.

    Promoted-subtask rows (migration 056) additionally carry ``parent_title`` —
    the tracked parent row's title, joined on the parent's Asana gid; NULL for
    normal tasks and for subtasks whose parent is not tracked.
    """
    where, params = [], []
    if source:
        where.append("t.source = ?"); params.append(source)
    if status:
        where.append("t.status = ?"); params.append(status)
    if kind:
        where.append("t.kind = ?"); params.append(kind)
    if due_before:
        where.append("t.due_date IS NOT NULL AND t.due_date <= ?"); params.append(due_before)
    if assignee_gid and assignee:
        where.append("(t.assignee_gid = ? OR (t.assignee_gid IS NULL AND LOWER(t.assignee) = LOWER(?)))")
        params.append(assignee_gid); params.append(assignee)
    elif assignee_gid:
        where.append("t.assignee_gid = ?"); params.append(assignee_gid)
    elif assignee:
        where.append("LOWER(t.assignee) = LOWER(?)"); params.append(assignee)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    params.append(limit)
    rows = conn.execute(
        f"SELECT t.*, p.title AS parent_title FROM enablement_tasks t "
        f"LEFT JOIN enablement_tasks p "
        f"ON p.source = 'asana' AND p.source_ref = t.parent_task_ref"
        f"{clause} ORDER BY t.created_at DESC LIMIT ?", params
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


# ── board links: the MANY-TO-MANY attribution authority (migration 055) ──
#
# A board→task relationship is not a property of the task. An Asana library
# custom field has one gid org-wide, so two mapped projects resolve the SAME
# indicator and both poll the same multi-homed task: one board creates the row,
# the other reconciles it. Both demonstrably track it, and both must be able to
# say so — a single column could only hold the winner, and removing the winner
# deleted the loser's live work.
#
# Every board that touches a task records a link. Removal drops a board's
# links; the task row itself only goes when no OTHER board's link remains.


def _has_link_table(conn: sqlite3.Connection) -> bool:
    """False on a database that predates migration 055.

    Both asymmetries hold for free in that case: no task is linked, so none is
    deletable by a board removal (closed) and none is hidden (open).
    """
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_board_links'"
        ).fetchone() is not None
    except sqlite3.Error:
        return False


def link_task_board(conn: sqlite3.Connection, task_id: str,
                    board_source_id: str | None) -> bool:
    """Record that ``board_source_id`` tracks ``task_id``. Idempotent.

    Called from BOTH asana_monitor poll sites — the board that creates a row and
    a board that reconciles a row it did not create. ``first_seen_at`` keeps the
    earliest observation (INSERT OR IGNORE), so re-polling never rewrites it.
    """
    if not task_id or not board_source_id or not _has_link_table(conn):
        return False
    with atomic(conn):
        cur = conn.execute(
            "INSERT OR IGNORE INTO task_board_links "
            "(task_id, board_source_id, first_seen_at) VALUES (?,?,?)",
            (task_id, board_source_id, _now()),
        )
    return cur.rowcount > 0


def task_board_ids(conn: sqlite3.Connection, task_id: str) -> list[str]:
    """Every board currently linked to one task (empty for an unlinked row)."""
    if not _has_link_table(conn):
        return []
    return [r[0] for r in conn.execute(
        "SELECT board_source_id FROM task_board_links WHERE task_id = ? "
        "ORDER BY board_source_id", (task_id,)).fetchall()]


def task_board_map(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """task_id → linked board source_ids, for callers judging many rows at once
    (the calendar filter, the page's row builder). Unlinked tasks are absent."""
    if not _has_link_table(conn):
        return {}
    out: dict[str, list[str]] = {}
    for tid, sid in conn.execute(
            "SELECT task_id, board_source_id FROM task_board_links "
            "ORDER BY task_id, board_source_id").fetchall():
        out.setdefault(tid, []).append(sid)
    return out


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
