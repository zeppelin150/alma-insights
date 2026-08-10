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
import threading

from src.data.connection_factory import atomic

logger = logging.getLogger("alma.asana_writeback")

# ── In-flight status-write guard (WS1-M5) ────────────────────────────
# The optimistic complete-flip races the 60s reconcile: without a guard the
# poll can read Asana's stale completed=false between our local flip and the
# PUT, revert the flip, and the user's click visually undoes itself for up to
# a minute. Poll and write-back run in the SAME main process, so an in-memory
# guard suffices — the reconcile skips the status field for task_ids here.
_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT_STATUS: dict[str, str] = {}   # task_id -> pending local status


def is_status_inflight(task_id: str) -> bool:
    with _INFLIGHT_LOCK:
        return str(task_id) in _INFLIGHT_STATUS


def _set_inflight(task_id: str, status: str) -> None:
    with _INFLIGHT_LOCK:
        _INFLIGHT_STATUS[str(task_id)] = status


def _clear_inflight(task_id: str) -> None:
    with _INFLIGHT_LOCK:
        _INFLIGHT_STATUS.pop(str(task_id), None)


def _cas_precheck(conn, task_id: str, gid: str, client) -> dict | None:
    """Check-and-set for destructive verbs (complete/reopen/due): compare
    Asana's live modified_at to our stored remote_modified_at anchor.

    Returns None to proceed, or a ``{"ok": False, "conflict": True}`` result.
    NULL anchor (first write after migration 043) → fetch-and-stamp, proceed.
    A FAILED CAS read also proceeds (CAS is protection, not a gate — the
    append-only verbs skip it entirely). Asana has no If-Match, so a
    sub-second GET→PUT race remains; the poll reconcile self-heals it.
    """
    from src.data import enablement_tasks as et
    row = conn.execute(
        "SELECT remote_modified_at FROM enablement_tasks WHERE task_id = ?",
        (str(task_id),),
    ).fetchone()
    anchor = (row[0] if row else None) or ""
    try:
        remote = (client.get_task(gid, opt_fields="modified_at") or {}).get("modified_at") or ""
    except Exception as exc:  # noqa: BLE001 — best-effort protection
        logger.debug("CAS read failed for %s: %s", gid, exc)
        return None
    if not anchor:
        if remote:
            et.update_task(conn, str(task_id), remote_modified_at=remote)
        return None
    if remote and remote != anchor:
        return {"ok": False, "conflict": True,
                "error": "task changed in Asana since it was last synced"}
    return None


def _restamp(conn, task_id: str, response: dict | None) -> None:
    """Record the PUT response's modified_at as the new CAS anchor. For
    content-neutral verbs (complete/reopen/due — the only restamped ones) it
    also advances brief_source_modified_at so our own click doesn't burn a
    Haiku brief rebuild (WS1-M4 self-write suppression) — but only when a
    brief already exists; a still-pending first build keeps its slot."""
    from src.data import enablement_tasks as et
    modified = (response or {}).get("modified_at") or ""
    if not modified:
        return
    fields = {"remote_modified_at": modified}
    row = conn.execute(
        "SELECT brief_status FROM enablement_tasks WHERE task_id = ?",
        (str(task_id),),
    ).fetchone()
    if row and (row[0] or "pending") != "pending":
        fields["brief_source_modified_at"] = modified
    et.update_task(conn, str(task_id), **fields)


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


def set_subtask_completed_in_asana(conn, task_id: str, subtask_gid: str,
                                   done: bool, *, client=None) -> dict:
    """Complete/reopen ONE subtask locally AND in Asana (WS-D-WEB mirror verb).

    ``task_id`` is the PARENT's local id (the caller validated the subtask
    belongs to it); ``subtask_gid`` is the Asana subtask GID, which is itself
    a task for the PUT. Local-first like the sibling verbs: the checklist row
    (enablement_subtasks) and any PROMOTED row (mig 056 — an enablement_tasks
    row whose source_ref is this gid) both flip before the API call, and an
    API failure REVERTS both — a diverged done-state misleads exactly like a
    parent's would. No CAS: checklist rows carry no remote_modified_at
    anchor; the poll's subtask re-read self-heals the sub-second race."""
    from src.data import enablement_tasks as et
    gid = str(subtask_gid or "").strip()
    if not gid:
        return {"ok": False, "error": "subtask_gid_required"}
    row = conn.execute(
        "SELECT subtask_id FROM enablement_subtasks "
        "WHERE task_id = ? AND asana_subtask_gid = ?",
        (str(task_id), gid),
    ).fetchone()
    promoted = conn.execute(
        "SELECT task_id, status FROM enablement_tasks "
        "WHERE source_ref = ? AND parent_task_ref IS NOT NULL",
        (gid,),
    ).fetchone()
    if row is None and promoted is None:
        return {"ok": False, "error": "subtask_not_found"}

    new_status = "done" if done else "open"

    def _flip(to_done: bool, status: str):
        if row is not None:
            et.toggle_subtask(conn, row[0], to_done)
        if promoted is not None:
            et.update_task(conn, promoted[0], status=status)

    _flip(bool(done), new_status)
    c = _client(client)
    if c is None:
        return {"ok": True, "synced": False, "done": bool(done),
                "note": "updated locally — Asana not configured"}
    if promoted is not None:
        _set_inflight(promoted[0], new_status)
    try:
        try:
            c.update_task(gid, completed=bool(done))
        except Exception as exc:  # noqa: BLE001 — revert, the flip would mislead
            prev_status = (promoted[1] if promoted is not None else "open") or "open"
            _flip(not bool(done), prev_status)
            logger.warning("Asana subtask write-back failed: %s", exc)
            return {"ok": False, "error": str(exc), "reverted": True}
        return {"ok": True, "synced": True, "done": bool(done)}
    finally:
        if promoted is not None:
            _clear_inflight(promoted[0])


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
    """Set the local due date AND push it to the linked Asana task.

    WS1-M5: guarded by check-and-set — a due date can clobber a teammate's
    concurrent change, so a stale anchor blocks the write (conflict:True; the
    caller refreshes and the operator retries against fresh data). Unlinked/
    unconfigured tasks keep the local-only degrade exactly as before.
    """
    from src.data import enablement_tasks as et
    gid = _asana_task_gid(conn, task_id)
    if not gid:
        et.update_task(conn, str(task_id), due_date=due_on)
        return {"ok": True, "synced": False, "due_on": due_on,
                "note": "updated locally — this task is not linked to Asana"}
    c = _client(client)
    if c is None:
        et.update_task(conn, str(task_id), due_date=due_on)
        return {"ok": True, "synced": False, "due_on": due_on,
                "note": "updated locally — Asana not configured"}
    conflict = _cas_precheck(conn, task_id, gid, c)
    if conflict:
        return conflict
    et.update_task(conn, str(task_id), due_date=due_on)
    try:
        res = c.update_due_date(gid, due_on)
    except Exception as exc:  # noqa: BLE001 — local update still applied
        logger.warning("Asana due-date write-back failed: %s", exc)
        return {"ok": True, "synced": False, "due_on": due_on, "error": str(exc)}
    _restamp(conn, task_id, res)
    return {"ok": True, "synced": True, "due_on": due_on}


def set_completed_in_asana(conn, task_id: str, done: bool, *, client=None) -> dict:
    """Complete/reopen a task locally AND in Asana (WS1-M5 panel verb).

    Order: CAS precheck (a read) → optimistic local flip under the in-flight
    guard (the reconcile skips status for guarded tasks) → PUT → restamp.
    Unlike comments/subtasks, a diverged done-state is actively misleading, so
    an API failure REVERTS the local flip — but only if the status still holds
    our optimistic value (a mid-flight reconcile of fresher state wins).
    """
    from src.data import enablement_tasks as et
    tid = str(task_id)
    task = et.get_task(conn, tid)
    if not task:
        return {"ok": False, "error": "task_not_found"}
    new_status = "done" if done else "open"
    prev_status = task.get("status") or "open"

    gid = _asana_task_gid(conn, tid)
    if not gid:
        et.update_task(conn, tid, status=new_status)
        return {"ok": True, "synced": False, "status": new_status,
                "note": "updated locally — this task is not linked to Asana"}
    c = _client(client)
    if c is None:
        et.update_task(conn, tid, status=new_status)
        return {"ok": True, "synced": False, "status": new_status,
                "note": "updated locally — Asana not configured"}
    conflict = _cas_precheck(conn, tid, gid, c)
    if conflict:
        return conflict

    _set_inflight(tid, new_status)
    try:
        et.update_task(conn, tid, status=new_status)   # optimistic flip
        try:
            res = c.update_task(gid, completed=bool(done))
        except Exception as exc:  # noqa: BLE001 — revert, the flip would mislead
            current = (et.get_task(conn, tid) or {}).get("status")
            if current == new_status:                  # nothing fresher landed
                et.update_task(conn, tid, status=prev_status)
            logger.warning("Asana complete write-back failed: %s", exc)
            return {"ok": False, "error": str(exc), "reverted": True}
        _restamp(conn, tid, res)
        return {"ok": True, "synced": True, "status": new_status}
    finally:
        _clear_inflight(tid)
