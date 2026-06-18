"""Asana write-back — push local enablement changes back into Asana.

Qt-free on purpose: the chat tools call these from the headless chat_mcp_server
process (which must not import PySide6, unlike asana_monitor.py). Each function
acts ONLY on tasks whose ``source='asana'`` — the parent Asana task GID lives in
``enablement_tasks.source_ref`` — and degrades gracefully (local-only) when the
task isn't from Asana or Asana isn't configured, so nothing ever crashes the chat.

Transaction note: like every chat-tool path, callers use a fresh connection per
tool call. Each helper opens at most sequential (never nested) ``atomic()``
blocks via the task service, and any direct write here commits before returning.
"""

from __future__ import annotations

import logging

from src.data.connection_factory import atomic

logger = logging.getLogger("alma.asana_writeback")


def _asana_task_gid(conn, task_id: str) -> str | None:
    """The Asana task GID for a local task, or None if it isn't an Asana task."""
    row = conn.execute(
        "SELECT source, source_ref FROM enablement_tasks WHERE task_id = ?",
        (str(task_id),),
    ).fetchone()
    if not row:
        return None
    source, ref = row[0], row[1]
    return ref if (source == "asana" and ref) else None


def _client(client):
    if client is not None:
        return client
    from src.data.asana_client import AsanaClient
    c = AsanaClient.from_store()
    return c if getattr(c, "api_key", "") else None


def create_subtask_in_asana(conn, task_id: str, text: str, *,
                            client=None, created_by: str = "user") -> dict:
    """Add a subtask locally AND create it under the parent Asana task.

    Always creates the local subtask; syncs to Asana only when the parent task
    came from Asana and Asana is configured. Stores the returned Asana subtask
    GID so a re-sync is idempotent.
    """
    from src.data import enablement_tasks as et
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "text_required"}
    sid = et.add_subtask(conn, str(task_id), text, created_by=created_by)

    parent_gid = _asana_task_gid(conn, task_id)
    if not parent_gid:
        return {"ok": True, "subtask_id": sid, "synced": False,
                "note": "saved locally — this task is not linked to Asana"}
    c = _client(client)
    if c is None:
        return {"ok": True, "subtask_id": sid, "synced": False,
                "note": "saved locally — Asana not configured"}
    try:
        res = c.create_subtask(parent_gid, text)
        gid = (res or {}).get("gid")
    except Exception as exc:  # noqa: BLE001 — local subtask still exists
        logger.warning("Asana subtask write-back failed: %s", exc)
        return {"ok": True, "subtask_id": sid, "synced": False, "error": str(exc)}
    if gid:
        with atomic(conn):
            conn.execute(
                "UPDATE enablement_subtasks SET asana_subtask_gid = ? WHERE subtask_id = ?",
                (gid, sid),
            )
    return {"ok": True, "subtask_id": sid, "synced": True, "asana_subtask_gid": gid}


def post_comment_to_asana(conn, task_id: str, text: str, *, client=None) -> dict:
    """Post a comment on the linked Asana task (no local mirror — Asana owns it)."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "text_required"}
    gid = _asana_task_gid(conn, task_id)
    if not gid:
        return {"ok": False, "error": "not_an_asana_task"}
    c = _client(client)
    if c is None:
        return {"ok": False, "error": "asana_not_configured"}
    try:
        res = c.add_comment(gid, text)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "task_id": str(task_id), "story_gid": (res or {}).get("gid")}


def update_due_in_asana(conn, task_id: str, due_on: str | None, *, client=None) -> dict:
    """Set the local due date AND push it to the linked Asana task."""
    from src.data import enablement_tasks as et
    et.update_task(conn, str(task_id), due_date=due_on)
    gid = _asana_task_gid(conn, task_id)
    if not gid:
        return {"ok": True, "synced": False, "due_on": due_on,
                "note": "updated locally — this task is not linked to Asana"}
    c = _client(client)
    if c is None:
        return {"ok": True, "synced": False, "due_on": due_on,
                "note": "updated locally — Asana not configured"}
    try:
        c.update_due_date(gid, due_on)
    except Exception as exc:  # noqa: BLE001 — local update still applied
        logger.warning("Asana due-date write-back failed: %s", exc)
        return {"ok": True, "synced": False, "due_on": due_on, "error": str(exc)}
    return {"ok": True, "synced": True, "due_on": due_on}
