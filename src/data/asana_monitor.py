"""Asana sync monitor — the missing 'poll a board and create tasks' half.

Hybrid autonomy: Asana matches AUTO-create enablement tasks (Drive/Guru go to a
review queue instead). For each enabled board in monitor_sources, poll the
project's tasks, keep the ones whose indicator custom-field is set to a trigger
value, map priority/assignee from the configured fields, and create an
``enablement_tasks`` row (idempotent on dedup_key).

The poll body is the module-level ``poll_once(conn)`` so three callers share one
code path: the Qt ``AsanaMonitor`` (background timer), the ``run_monitor_now``
chat tool (which runs in the separate MCP-server process and can't reach the Qt
object), and the unit tests.

Connection discipline: the monitor opens a FRESH connection on its poll thread
and writes with plain execute+commit / the task service's own atomic() — never
wrap the poll loop in atomic() (Qt-thread + nesting safety).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from threading import Thread

from PySide6.QtCore import QTimer, Signal

from src.data.source_types import SourceMonitor

logger = logging.getLogger("alma.asana_monitor")

_DEFAULT_INTERVAL = 300  # seconds

# Asana enum value names (urgency-style) → enablement task priority.
_PRIORITY_MAP = {
    "low": "low", "minor": "low", "p3": "low",
    "medium": "normal", "normal": "normal", "p2": "normal",
    "high": "high", "urgent": "high", "critical": "high", "p1": "high", "p0": "high",
}


# ── poll core (shared by the monitor, the chat tool, and tests) ──────

def poll_once(conn, *, client=None) -> list[str]:
    """Poll every enabled Asana board once; auto-create tasks for indicator
    matches. Returns the created/seen task_ids (idempotent on re-poll)."""
    from src.data import asana_setup
    boards = asana_setup.get_asana_config(conn)
    if not boards:
        return []
    if client is None:
        from src.data.asana_client import AsanaClient
        client = AsanaClient.from_store()
        if not getattr(client, "api_key", ""):
            return []
    created: list[str] = []
    for board in boards:
        try:
            created.extend(_poll_board(conn, client, board))
        except Exception as exc:  # noqa: BLE001 — one board's failure can't kill the rest
            logger.warning("Asana board %s poll failed: %s", board.get("source_id"), exc)
            _mark_board(conn, board.get("source_id"), status="error", error=str(exc))
    return created


def _poll_board(conn, client, board: dict) -> list[str]:
    cfg = board.get("config") or {}
    project_gid = cfg.get("project_gid")
    source_id = board.get("source_id")
    if not project_gid:
        return []
    watermark = _board_cursor(conn, source_id)
    tasks = client.list_tasks(project_gid, modified_since=watermark) or []
    indicators = cfg.get("indicators") or []
    mappings = cfg.get("mappings") or {}
    created: list[str] = []
    newest = watermark
    for task in tasks:
        modified = task.get("modified_at") or ""
        if modified and (not newest or modified > newest):
            newest = modified
        if task.get("completed"):
            continue
        if not _matches_indicators(task, indicators):
            continue
        tid = _create_task_from_asana(conn, board, task, mappings)
        if tid:
            created.append(tid)
    _mark_board(conn, source_id, status="ok", cursor=newest)
    return created


def _matches_indicators(task: dict, indicators: list[dict]) -> bool:
    """True if ANY indicator's enum custom-field on the task is set to a trigger
    value (match_mode defaults to 'any')."""
    if not indicators:
        return False
    by_gid = {cf.get("gid"): cf for cf in (task.get("custom_fields") or [])}
    for ind in indicators:
        cf = by_gid.get(ind.get("field_gid"))
        if not cf:
            continue
        enum_value = cf.get("enum_value") or {}
        if enum_value.get("gid") and enum_value["gid"] in (ind.get("trigger_value_gids") or []):
            return True
    return False


def _create_task_from_asana(conn, board: dict, task: dict, mappings: dict) -> str | None:
    from src.data import enablement_tasks as etasks
    by_gid = {cf.get("gid"): cf for cf in (task.get("custom_fields") or [])}

    # Priority from the mapped enum field (e.g. Urgency).
    priority = "normal"
    pf = mappings.get("priority_field_gid")
    if pf and pf in by_gid:
        enum_value = by_gid[pf].get("enum_value") or {}
        name = (enum_value.get("name") or by_gid[pf].get("display_value") or "").strip().lower()
        priority = _PRIORITY_MAP.get(name, "normal")

    # Assignee from the mapped people field (resolved name), else the task assignee.
    assignee = None
    af = mappings.get("assignee_field_gid")
    if af and af in by_gid:
        assignee = _resolve_people(conn, board, by_gid[af])
    if not assignee:
        assignee = (task.get("assignee") or {}).get("name")

    tid = etasks.create_task(
        conn,
        source="asana",
        kind="request",
        title=task.get("name") or "Asana task",
        source_ref=task.get("gid"),
        source_url=task.get("permalink_url"),
        due_date=task.get("due_on"),
        priority=priority,
        created_by="agent",
    )
    if assignee:
        etasks.update_task(conn, tid, assignee=assignee)
    return tid


def _resolve_people(conn, board: dict, cf: dict) -> str | None:
    """Read names from an Asana people custom-field, caching gid→name in
    asana_users (the field is requested with people_value.name expanded)."""
    people = cf.get("people_value")
    if not isinstance(people, list) or not people:
        return None
    _cache_users(conn, board, people)
    names = [p.get("name") for p in people if p.get("name")]
    return ", ".join(names) if names else None


def _cache_users(conn, board: dict, people: list[dict]) -> None:
    ws = (board.get("config") or {}).get("workspace_gid", "")
    now = datetime.now(timezone.utc).isoformat()
    try:
        for p in people:
            if p.get("gid"):
                conn.execute(
                    "INSERT INTO asana_users (gid, name, email, workspace_gid, cached_at) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(gid) DO UPDATE SET "
                    "name=excluded.name, cached_at=excluded.cached_at",
                    (p["gid"], p.get("name"), p.get("email"), ws, now),
                )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — caching is best-effort
        logger.debug("asana_users cache failed: %s", exc)


def _board_cursor(conn, source_id) -> str | None:
    row = conn.execute(
        "SELECT cursor FROM monitor_sources WHERE source_id = ?", (source_id,)
    ).fetchone()
    return (row[0] if row else None) or None


def _mark_board(conn, source_id, *, status="ok", cursor=None, error=None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    try:
        if cursor is not None:
            conn.execute(
                "UPDATE monitor_sources SET cursor=?, last_run_at=?, last_status=?, "
                "last_error=? WHERE source_id=?",
                (cursor, now, status, error, source_id),
            )
        else:
            conn.execute(
                "UPDATE monitor_sources SET last_run_at=?, last_status=?, last_error=? "
                "WHERE source_id=?",
                (now, status, error, source_id),
            )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("monitor_sources mark failed: %s", exc)


# ── Qt monitor wrapper (background timer; drives poll_once off-thread) ──

class AsanaMonitor(SourceMonitor):
    """Polls configured Asana boards and emits the created task_ids.

    SourceMonitor provides records_received(list) + status_changed(str). All DB
    writes happen on the poll thread via a fresh connection; only signals cross
    back to the UI thread (Qt queues them).
    """

    tasks_created = Signal(list)

    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db = db_manager
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._status = "paused"
        self._fetching = False
        self._interval = _DEFAULT_INTERVAL

    @property
    def source_name(self) -> str:
        return "asana"

    @property
    def status(self) -> str:
        return self._status

    def start(self, interval_seconds: int = _DEFAULT_INTERVAL):
        self._interval = max(60, interval_seconds)
        self._status = "live"
        self.status_changed.emit("live")
        self._timer.start(self._interval * 1000)
        self._on_tick()

    def stop(self):
        self._timer.stop()
        self._status = "paused"
        self.status_changed.emit("paused")

    def scan_now(self):
        """One-off manual poll (out of band of the timer)."""
        self._on_tick()

    def _on_tick(self):
        if self._fetching:
            return
        self._fetching = True
        Thread(target=self._do_fetch, daemon=True).start()

    def _do_fetch(self):
        try:
            from src.data.connection_factory import get_connection
            conn = get_connection(str(self.db.db_path))
            try:
                created = poll_once(conn)
            finally:
                conn.close()
            if created:
                self.tasks_created.emit(created)
                self.records_received.emit(created)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Asana fetch failed: %s", exc)
        finally:
            self._fetching = False
