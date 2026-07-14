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

_DEFAULT_INTERVAL = 300      # legacy shared default (Drive keeps it)
_ASANA_DEFAULT_INTERVAL = 60  # events-API cadence (enablement.asana.poll_interval_seconds)

# Events-path degradation: after this many consecutive non-412 get_events
# failures a board falls back to the legacy modified_since poll for the rest
# of the session (in-memory — a restart retries events).
_EVENTS_FAILURE_LIMIT = 3
_events_failures: dict = {}

# Baseline re-list page budget (100/page). Hitting the cap means the re-list
# may be TRUNCATED — logged loudly; the un-listed tail surfaces when those
# tasks next change (events path resumes immediately after the baseline).
_BASELINE_MAX_PAGES = 50

# Asana enum value names (urgency-style) → enablement task priority.
_PRIORITY_MAP = {
    "low": "low", "minor": "low", "p3": "low",
    "medium": "normal", "normal": "normal", "p2": "normal",
    "high": "high", "urgent": "high", "critical": "high", "p1": "high", "p0": "high",
}


def _use_events() -> bool:
    """Read the events-path flag (default ON). Settings are readable from the
    MCP subprocess too, so all three poll_once callers agree."""
    try:
        from src.data.settings_manager import get_section
        asana_cfg = (get_section("enablement", {}) or {}).get("asana") or {}
        return bool(asana_cfg.get("use_events", True))
    except Exception:  # noqa: BLE001 — settings trouble must not kill the poll
        return True


# ── poll core (shared by the monitor, the chat tool, and tests) ──────

def poll_once(conn, *, client=None, results: dict | None = None) -> list[str]:
    """Poll every enabled Asana board once; auto-create tasks for indicator
    matches. Returns the created task_ids (idempotent on re-poll).

    ``results`` is an optional out-param: when a dict is passed, it gains
    ``created`` and ``updated`` (reconciled-changed) task_id lists so the Qt
    monitor can emit ``tasks_updated`` — the return contract stays a plain
    created-list for the chat tool / task_sources callers.
    """
    from src.data import asana_setup
    boards = asana_setup.get_asana_config(conn)
    if results is None:
        results = {}
    results.setdefault("created", [])
    results.setdefault("updated", [])
    if not boards:
        return []
    if client is None:
        from src.data.asana_client import AsanaClient
        client = AsanaClient.from_store()
        if not getattr(client, "api_key", ""):
            return []
    for board in boards:
        try:
            _poll_board(conn, client, board, results)
        except Exception as exc:  # noqa: BLE001 — one board's failure can't kill the rest
            logger.warning("Asana board %s poll failed: %s", board.get("source_id"), exc)
            _mark_board(conn, board.get("source_id"), status="error", error=str(exc))
    # Extras refresh (WS1-M3) — AFTER every board's cursor/token advance, so a
    # slow/failed extras pass can never stall sync freshness. Capped per poll;
    # the staleness comparison self-drains any backlog over subsequent cycles.
    try:
        from src.data import asana_extras
        asana_extras.drain_stale(conn, client,
                                 payload_cache=results.get("_payload_cache"))
    except Exception as exc:  # noqa: BLE001 — extras are enrichment, never fatal
        logger.debug("extras drain failed: %s", exc)
    return list(results["created"])


def _poll_board(conn, client, board: dict, results: dict | None = None) -> None:
    if results is None:
        results = {"created": [], "updated": []}
    cfg = board.get("config") or {}
    project_gid = cfg.get("project_gid")
    source_id = board.get("source_id")
    if not project_gid:
        # Mapped-projects-only: tasks are created ONLY from boards that an
        # operator explicitly mapped (a monitor_sources row with a project_gid).
        # An unmapped/misconfigured board is skipped, never scanned.
        logger.info("Asana board %s has no project_gid mapped; skipping", source_id)
        return
    if _use_events() and _events_failures.get(source_id, 0) < _EVENTS_FAILURE_LIMIT:
        try:
            _poll_board_events(conn, client, board, results)
            _events_failures.pop(source_id, None)
            return
        except Exception as exc:  # noqa: BLE001 — degrade, don't die
            n = _events_failures.get(source_id, 0) + 1
            _events_failures[source_id] = n
            logger.warning(
                "Asana events poll failed for %s (%d/%d — %s); using legacy path",
                source_id, n, _EVENTS_FAILURE_LIMIT, exc)
    _poll_board_legacy(conn, client, board, results)


def _poll_board_legacy(conn, client, board: dict, results: dict) -> None:
    """The original single-page modified_since poll — kept verbatim as the
    events-path fallback (flag off / repeated events failures)."""
    cfg = board.get("config") or {}
    project_gid = cfg.get("project_gid")
    source_id = board.get("source_id")
    watermark = _board_cursor(conn, source_id)
    tasks = client.list_tasks(project_gid, modified_since=watermark) or []
    newest = watermark
    for task in tasks:
        modified = task.get("modified_at") or ""
        if modified and (not newest or modified > newest):
            newest = modified
        if task.get("gid"):
            results.setdefault("_payload_cache", {})[task["gid"]] = task
        _process_task(conn, client, board, task, results)
    _mark_board(conn, source_id, status="ok", cursor=newest)


def _poll_board_events(conn, client, board: dict, results: dict) -> None:
    """Events-API diff poll: consume /events since the stored sync token.

    Token lifecycle: no token yet (or Asana answers full_resync) → mint a fresh
    token FIRST via the 412 handshake, THEN run the paged baseline re-list, then
    store the token — changes landing mid-re-list are replayed by the event
    stream (reconcile + dedup_key make the overlap idempotent). The
    modified_since cursor keeps advancing on this path too, so a later fallback
    never re-lists from an ancient watermark.
    """
    cfg = board.get("config") or {}
    project_gid = cfg.get("project_gid")
    source_id = board.get("source_id")
    token = _board_events_sync(conn, source_id)
    if not token:
        handshake = client.get_events(project_gid, None)  # always 412 → fresh token
        if not isinstance(handshake, dict):
            # Malformed/mocked client — degrade to the legacy path rather than
            # silently no-oping on a fake response.
            raise TypeError("get_events returned a non-dict response")
        _run_baseline(conn, client, board, handshake.get("sync") or None, results)
        return

    events: list = []
    resp = client.get_events(project_gid, token)
    if not isinstance(resp, dict):
        raise TypeError("get_events returned a non-dict response")
    if resp.get("full_resync"):
        _run_baseline(conn, client, board, resp.get("sync") or None, results)
        return
    events.extend(resp.get("events") or [])
    while resp.get("has_more"):
        resp = client.get_events(project_gid, resp.get("sync"))
        if resp.get("full_resync"):
            _run_baseline(conn, client, board, resp.get("sync") or None, results)
            return
        events.extend(resp.get("events") or [])
    new_token = resp.get("sync") or token

    changed_gids: list[str] = []
    deleted_gids: list[str] = []
    removed_gids: list[str] = []
    story_dirty: set = set()
    seen: set = set()
    for ev in events:
        res = ev.get("resource") or {}
        rtype = res.get("resource_type")
        action = ev.get("action")
        if rtype == "task":
            gid = res.get("gid")
            if not gid:
                continue
            if action in ("added", "changed", "undeleted"):
                if gid not in seen:
                    seen.add(gid)
                    changed_gids.append(gid)
            elif action == "deleted":
                deleted_gids.append(gid)
            elif action == "removed":
                removed_gids.append(gid)
        elif rtype == "story":
            parent = ev.get("parent") or {}
            if parent.get("resource_type") == "task" and parent.get("gid"):
                story_dirty.add(parent["gid"])

    newest = _board_cursor(conn, source_id)
    for gid in changed_gids:
        try:
            task = client.get_task(gid, opt_fields=_client_task_fields())
        except Exception as exc:  # noqa: BLE001 — deleted between event and fetch etc.
            if _is_http_404(exc):
                _dismiss_by_gid(conn, gid)
                continue
            logger.debug("event task fetch failed for %s: %s", gid, exc)
            continue
        modified = task.get("modified_at") or ""
        if modified and (not newest or modified > newest):
            newest = modified
        results.setdefault("_payload_cache", {})[gid] = task
        _process_task(conn, client, board, task, results)
    for gid in deleted_gids:
        _dismiss_by_gid(conn, gid)
    for gid in removed_gids:
        _handle_removed(conn, client, board, gid)
    if story_dirty:
        _on_stories_dirty(conn, client, sorted(story_dirty), results)
    _mark_board(conn, source_id, status="ok", cursor=newest, events_sync=new_token)


def _run_baseline(conn, client, board: dict, prime_token: str | None,
                  results: dict) -> None:
    """Paged full re-list (modified_since=cursor) after the token handshake.

    Also fixes the legacy >100-modified-tasks silent drop: the baseline pages
    to ``_BASELINE_MAX_PAGES``; hitting the cap is logged loudly (the tail
    surfaces when those tasks next change).
    """
    cfg = board.get("config") or {}
    project_gid = cfg.get("project_gid")
    source_id = board.get("source_id")
    watermark = _board_cursor(conn, source_id)
    tasks = client.list_tasks_paged(project_gid, modified_since=watermark,
                                    max_pages=_BASELINE_MAX_PAGES) or []
    if not isinstance(tasks, list):
        raise TypeError("list_tasks_paged returned a non-list response")
    if len(tasks) >= _BASELINE_MAX_PAGES * 100:
        logger.warning(
            "Asana baseline re-list for %s hit the %d-page cap — possibly "
            "truncated; un-listed tasks surface on their next change",
            source_id, _BASELINE_MAX_PAGES)
    newest = watermark
    for task in tasks:
        modified = task.get("modified_at") or ""
        if modified and (not newest or modified > newest):
            newest = modified
        if task.get("gid"):
            results.setdefault("_payload_cache", {})[task["gid"]] = task
        _process_task(conn, client, board, task, results)
    _mark_board(conn, source_id, status="ok", cursor=newest,
                events_sync=prime_token)


def _process_task(conn, client, board: dict, task: dict, results: dict) -> None:
    """Shared per-task pipeline: reconcile a tracked task, else create on an
    indicator match. Fills results['created'] / results['updated']."""
    cfg = board.get("config") or {}
    indicators = cfg.get("indicators") or []
    mappings = cfg.get("mappings") or {}
    # Two-way read-back: if we already track this task, pull Asana-side
    # changes (due / completion / assignee / subtasks) into the local task
    # and stop — never re-create what we already have.
    tid = _reconcile_existing_task(conn, client, task)
    if tid:
        results["updated"].append(tid)
        return
    if task.get("completed"):
        return
    if not _matches_indicators(task, indicators):
        return
    tid = _create_task_from_asana(conn, board, task, mappings)
    if tid:
        results["created"].append(tid)
        try:
            _pull_subtasks(conn, client, tid, task.get("gid"))
        except Exception as exc:  # noqa: BLE001 — best-effort initial subtask pull
            logger.debug("initial subtask pull failed for %s: %s", task.get("gid"), exc)


def _client_task_fields() -> str:
    from src.data.asana_client import _TASK_FIELDS
    return _TASK_FIELDS


def _is_http_404(exc: Exception) -> bool:
    import urllib.error
    return isinstance(exc, urllib.error.HTTPError) and exc.code == 404


def _dismiss_by_gid(conn, gid: str) -> None:
    """A task deleted in Asana → dismiss the local row (kept resolvable —
    dismissed ≠ deleted, per D1/D7)."""
    from src.data import enablement_tasks as etasks
    row = conn.execute(
        "SELECT task_id FROM enablement_tasks WHERE source='asana' AND source_ref=?",
        (gid,),
    ).fetchone()
    if row:
        etasks.update_task(conn, row[0], status="dismissed")


def _handle_removed(conn, client, board: dict, gid: str) -> None:
    """'removed' fires on multi-home/re-organize too, not just deletion —
    verify with a follow-up get_task and only dismiss when it 404s; a live
    task gets a scratchpad note instead."""
    from src.data import enablement_tasks as etasks
    row = conn.execute(
        "SELECT task_id, scratchpad FROM enablement_tasks "
        "WHERE source='asana' AND source_ref=?",
        (gid,),
    ).fetchone()
    if not row:
        return
    try:
        client.get_task(gid, opt_fields="name,completed")
    except Exception as exc:  # noqa: BLE001
        if _is_http_404(exc):
            etasks.update_task(conn, row[0], status="dismissed")
        return
    name = (board.get("display_name") or board.get("source_id") or "board")
    note = f"[monitor] No longer on {name} (moved in Asana)."
    scratch = row[1] or ""
    if note not in scratch:
        etasks.set_scratchpad(conn, row[0], (scratch + "\n" + note).strip())


def _on_stories_dirty(conn, client, gids: list, results: dict) -> None:
    """Comment/story activity on tracked tasks: force extras staleness so the
    capped drain re-pulls their stories (story events do NOT bump task
    modified_at, so this is the only comment-freshness signal)."""
    try:
        from src.data import asana_extras
        asana_extras.mark_stories_dirty(conn, gids)
    except Exception as exc:  # noqa: BLE001 — enrichment, never fatal
        logger.debug("mark_stories_dirty failed: %s", exc)


def _reconcile_existing_task(conn, client, task: dict):
    """Pull Asana-side changes into the local task we already track for this gid.

    Updates due date / completion / assignee and mirrors subtasks. Returns the
    local task_id when a tracked task was found (so the caller won't try to
    re-create it), else None."""
    from src.data import enablement_tasks as etasks
    gid = task.get("gid")
    if not gid:
        return None
    row = conn.execute(
        "SELECT task_id FROM enablement_tasks WHERE source='asana' AND source_ref=?",
        (gid,),
    ).fetchone()
    if not row:
        return None
    tid = row[0]
    fields: dict = {}
    if task.get("modified_at"):
        # Check-and-set anchor for the WS1-M5/M6 write-back tier.
        fields["remote_modified_at"] = task["modified_at"]
    if "due_on" in task:
        fields["due_date"] = task.get("due_on")        # date, or None to clear
    if task.get("completed"):
        # In-flight guard (WS1-M5): while a panel complete-flip is mid-PUT,
        # Asana's stale completed-state must not revert the user's click.
        from src.data.asana_writeback import is_status_inflight
        if not is_status_inflight(tid):
            fields["status"] = "done"
    assignee = task.get("assignee") or {}
    asg = assignee.get("name")
    if asg:
        fields["assignee"] = asg
    asg_gid = assignee.get("gid")
    if asg_gid:
        fields["assignee_gid"] = asg_gid
    if task.get("notes") is not None:
        fields["description"] = task.get("notes")   # Asana is source-of-truth for the body
    creator = (task.get("created_by") or {}).get("name")
    if creator:
        fields["submitter"] = creator
    if fields:
        etasks.update_task(conn, tid, **fields)
    try:
        _pull_subtasks(conn, client, tid, gid)
    except Exception as exc:  # noqa: BLE001 — subtask read-back is best-effort
        logger.debug("subtask read-back failed for %s: %s", gid, exc)
    return tid


def _pull_subtasks(conn, client, task_id, task_gid) -> None:
    """Mirror Asana subtasks locally: add ones we don't have yet and sync the
    done-state of ones we do, matched by asana_subtask_gid (so our own
    write-backs are recognised and never duplicated)."""
    from src.data import enablement_tasks as etasks
    from src.data.connection_factory import atomic
    subs = client.list_subtasks(task_gid) or []
    existing = {r[0]: r[1] for r in conn.execute(
        "SELECT asana_subtask_gid, subtask_id FROM enablement_subtasks "
        "WHERE task_id=? AND asana_subtask_gid IS NOT NULL AND asana_subtask_gid != ''",
        (task_id,),
    ).fetchall()}
    for s in subs:
        gid = s.get("gid")
        if not gid:
            continue
        if gid in existing:
            etasks.toggle_subtask(conn, existing[gid], s["completed"])
        else:
            sid = etasks.add_subtask(conn, task_id, s["name"],
                                     created_by="asana", done=s["completed"])
            with atomic(conn):
                conn.execute(
                    "UPDATE enablement_subtasks SET asana_subtask_gid=? WHERE subtask_id=?",
                    (gid, sid),
                )


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
    # The task's direct assignee GID — the stable key the "only mine" filter uses.
    assignee_gid = (task.get("assignee") or {}).get("gid")

    tid = etasks.create_task(
        conn,
        source="asana",
        kind="request",
        title=task.get("name") or "Asana task",
        source_ref=task.get("gid"),
        source_url=task.get("permalink_url"),
        description=task.get("notes"),
        submitter=(task.get("created_by") or {}).get("name"),
        due_date=task.get("due_on"),
        priority=priority,
        created_by="agent",
    )
    upd: dict = {}
    if assignee:
        upd["assignee"] = assignee
    if assignee_gid:
        upd["assignee_gid"] = assignee_gid
    if task.get("modified_at"):
        upd["remote_modified_at"] = task["modified_at"]
    if upd:
        etasks.update_task(conn, tid, **upd)
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


def _board_events_sync(conn, source_id) -> str | None:
    row = conn.execute(
        "SELECT events_sync FROM monitor_sources WHERE source_id = ?", (source_id,)
    ).fetchone()
    return (row[0] if row else None) or None


def _mark_board(conn, source_id, *, status="ok", cursor=None, error=None,
                events_sync=None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    sets = ["last_run_at=?", "last_status=?", "last_error=?"]
    params: list = [now, status, error]
    if cursor is not None:
        sets.insert(0, "cursor=?")
        params.insert(0, cursor)
    if events_sync is not None:
        sets.insert(0, "events_sync=?")
        params.insert(0, events_sync)
    try:
        conn.execute(
            f"UPDATE monitor_sources SET {', '.join(sets)} WHERE source_id=?",
            (*params, source_id),
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
    tasks_updated = Signal(list)   # reconciled-changed local task_ids (events/legacy)

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
            results: dict = {}
            try:
                created = poll_once(conn, results=results)
            finally:
                conn.close()
            if created:
                self.tasks_created.emit(created)
                self.records_received.emit(created)
            updated = results.get("updated") or []
            if updated:
                self.tasks_updated.emit(updated)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Asana fetch failed: %s", exc)
        finally:
            self._fetching = False


# ── task-source registration (M4) ────────────────────────────────────
from src.data import task_sources as _task_sources  # noqa: E402

_task_sources.register(_task_sources.TaskSourceSpec(
    name="asana", display_name="Asana", kind="ingest",
    # bare `poll_once` name → resolved from this module's globals at call time,
    # so monkeypatching asana_monitor.poll_once is honored.
    poll=lambda conn, **kw: poll_once(conn, **kw),
))
