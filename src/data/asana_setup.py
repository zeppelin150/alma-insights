"""Asana setup engine — let the assistant (Renn) discover GIDs and write config.

The whole point: a non-technical enablement user should NEVER hunt for Asana
custom-field GIDs or enum-value GIDs. They paste an Asana API key, ask Renn to
find their projects/fields, and Renn resolves the GIDs and writes the board
config. Renn's ONLY write is set_asana_board_config() — it touches monitor_sources
and nothing else, so the assistant can configure Asana but can't change any other
app setting.

discover() uses the live Asana API when a key is available; otherwise it returns
mock discovery so the flow is demonstrable without credentials.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger("alma.asana_setup")

# Realistic Asana shapes (numeric gids) for the demo / offline flow.
MOCK_DISCOVERY = {
    "workspace": {"gid": "120420000001", "name": "Alma Health"},
    "projects": [
        {"gid": "120420000111", "name": "Enablement Requests"},
        {"gid": "120420000222", "name": "Launch Coordination"},
        {"gid": "120420000333", "name": "Provider Onboarding"},
    ],
    "custom_fields": {
        "120420000111": [
            {"gid": "120420000901", "name": "Assigned Team", "type": "enum",
             "enum_options": [
                 {"gid": "120420000951", "name": "Enablement"},
                 {"gid": "120420000952", "name": "Support"},
                 {"gid": "120420000953", "name": "Engineering"}]},
            {"gid": "120420000902", "name": "Urgency", "type": "enum",
             "enum_options": [
                 {"gid": "120420000961", "name": "Low"},
                 {"gid": "120420000962", "name": "Medium"},
                 {"gid": "120420000963", "name": "High"},
                 {"gid": "120420000964", "name": "Urgent"}]},
            {"gid": "120420000903", "name": "Assigned People", "type": "people"},
        ],
    },
}


def is_asana_connected() -> bool:
    """True if an Asana API key is stored."""
    try:
        from src.data.pat_store import load_setting  # noqa: PLC0415
        return bool(load_setting("asana_api_key", ""))
    except Exception:
        return False


def discover(api_key: str | None = None, project_gid: str | None = None) -> dict:
    """Return projects + custom fields (with gids and enum-value gids).

    Live Asana when a key is present; otherwise the mock. If project_gid is given,
    the result is narrowed to that project's custom fields (fetched live if needed).
    """
    key = api_key
    if not key:
        try:
            from src.data.pat_store import load_setting  # noqa: PLC0415
            key = load_setting("asana_api_key", "")
        except Exception:
            key = ""

    # ``is_mock`` propagates to the caller so nothing persists a board config
    # built from the mock's fabricated GIDs into a real source (finding 14).
    # The mock is legitimate for demo mode; the setup writer gates on this flag.
    data = MOCK_DISCOVERY
    is_mock = True
    if key:
        try:
            from src.data.asana_client import AsanaClient  # noqa: PLC0415
            client = AsanaClient(key)
            data = client.discover()
            if project_gid and project_gid not in data.get("custom_fields", {}):
                data.setdefault("custom_fields", {})[project_gid] = client.get_custom_fields(project_gid)
            is_mock = False
        except Exception as exc:  # noqa: BLE001 — fall back to mock on any client error
            logger.warning("Asana discover failed, using mock: %s", exc)
            data = MOCK_DISCOVERY
            is_mock = True

    if project_gid:
        return {
            "mock": is_mock,
            "workspace": data.get("workspace"),
            "project": next((p for p in data.get("projects", []) if p["gid"] == project_gid), None),
            "custom_fields": data.get("custom_fields", {}).get(project_gid, []),
        }
    # Shallow copy + flag so the shared MOCK_DISCOVERY constant is never stamped.
    return {**data, "mock": is_mock}


def set_asana_board_config(
    conn: sqlite3.Connection,
    *,
    project_gid: str,
    project_name: str,
    indicator_field_gid: str,
    indicator_field_name: str,
    indicator_value_gid: str,
    indicator_value_name: str,
    priority_field_gid: str | None = None,
    assignee_field_gid: str | None = None,
    priority_field_name: str | None = None,
    assignee_field_name: str | None = None,
    calendar: bool = False,
) -> dict:
    """Renn's ONLY write — persist the resolved GIDs into monitor_sources.

    Scoped on purpose: this is the single mutation the assistant is allowed to make.
    It writes one Asana board's config (keyed by gid) and nothing else.

    ``calendar`` is the per-board Calendar-inclusion flag and defaults to OFF for
    a NEWLY added board (owner decision, 2026-07-28) — a board starts syncing
    without silently repainting the operator's calendar. Re-saving the config of
    a board that already has the flag ON never turns it back off: this writer
    replaces config_json wholesale, and an opt-in the operator made in Settings
    must not be dropped by a later re-run of setup. Turning it OFF is
    :func:`set_board_calendar`'s job.
    """
    source_id = f"asana:{project_gid}"
    prior = _board_config(conn, source_id)
    config = {
        "project_gid": project_gid,
        "project_name": project_name,
        "indicators": [{
            "field_gid": indicator_field_gid,
            "field_name": indicator_field_name,
            "trigger_value_gids": [indicator_value_gid],
            "trigger_value_names": [indicator_value_name],
        }],
        "mappings": {
            "priority_field_gid": priority_field_gid,
            "assignee_field_gid": assignee_field_gid,
            "priority_field_name": priority_field_name,
            "assignee_field_name": assignee_field_name,
        },
        "calendar": bool(calendar) or bool((prior or {}).get("calendar")),
    }
    now = datetime.now(timezone.utc).isoformat()
    # Single upsert — plain execute+commit (not atomic()) so this works whether or
    # not the caller's connection is already mid-transaction (e.g. the Gemini
    # dispatch_tool path holds a transaction open for its telemetry write).
    conn.execute(
        """INSERT INTO monitor_sources
             (source_id, source_type, display_name, config_json, enabled, created_at, updated_at)
           VALUES (?, 'asana', ?, ?, 1, ?, ?)
           ON CONFLICT(source_id) DO UPDATE SET
             display_name=excluded.display_name, config_json=excluded.config_json,
             updated_at=excluded.updated_at""",
        (source_id, project_name, json.dumps(config), now, now),
    )
    conn.commit()
    return {"ok": True, "source_id": source_id, "config": config}


def get_asana_config(conn: sqlite3.Connection, *,
                     include_disabled: bool = False) -> list[dict]:
    """The configured Asana boards.

    Disabled boards are omitted by default so the per-board SYNC toggle in
    Settings is a real gate and not decoration: ``poll_once`` reads through
    here, so ``enabled=0`` means the board is genuinely not polled. Pass
    ``include_disabled=True`` for the Settings panel, which has to render the
    boards it is offering to switch back on.
    """
    clause = "" if include_disabled else " AND enabled = 1"
    rows = conn.execute(
        "SELECT source_id, display_name, config_json, enabled "
        f"FROM monitor_sources WHERE source_type='asana'{clause} "
        "ORDER BY source_id"
    ).fetchall()
    return [{"source_id": r[0], "display_name": r[1],
             "config": json.loads(r[2] or "{}"), "enabled": bool(r[3])}
            for r in rows]


# ── per-board flags, attribution, and LOCAL removal ──────────────────
#
# NOTHING below this line may reach Asana. Board removal deletes rows in OUR
# database only; the Asana project, its tasks and its custom fields are never
# modified, and there is no AsanaClient import anywhere in this section — the
# structural guard in tests/test_asana_board_management.py enforces that.


def _board_row(conn: sqlite3.Connection, source_id: str):
    return conn.execute(
        "SELECT source_id, display_name, config_json, enabled, last_status, last_error "
        "FROM monitor_sources WHERE source_id = ? AND source_type = 'asana'",
        (source_id,),
    ).fetchone()


def _board_config(conn: sqlite3.Connection, source_id: str) -> dict | None:
    row = _board_row(conn, source_id)
    if row is None:
        return None
    try:
        return json.loads(row[2] or "{}")
    except (TypeError, ValueError):
        return {}


def _write_config(conn: sqlite3.Connection, source_id: str, config: dict) -> None:
    """Plain execute+commit (never atomic()) — mirrors set_asana_board_config so
    these writers also work on a connection that is already mid-transaction."""
    conn.execute(
        "UPDATE monitor_sources SET config_json = ?, updated_at = ? "
        "WHERE source_id = ? AND source_type = 'asana'",
        (json.dumps(config), datetime.now(timezone.utc).isoformat(), source_id),
    )
    conn.commit()


def set_board_calendar(conn: sqlite3.Connection, source_id: str,
                       enabled: bool) -> dict:
    """Flip ONLY the per-board Calendar-inclusion flag in config_json.

    Governs calendar DISPLAY only — a board with the flag off keeps syncing
    (that is the ``enabled`` column's job) and its tasks stay in the task list.
    """
    config = _board_config(conn, source_id)
    if config is None:
        return {"ok": False, "error": "unknown_board", "source_id": source_id}
    config["calendar"] = bool(enabled)
    _write_config(conn, source_id, config)
    return {"ok": True, "source_id": source_id, "calendar": bool(enabled)}


def set_board_enabled(conn: sqlite3.Connection, source_id: str,
                      enabled: bool) -> dict:
    """Flip the monitor_sources.enabled column — the per-board SYNC toggle.

    Disabling stops the poller reading that board (get_asana_config filters on
    it). Already-imported tasks are left exactly where they are; only
    :func:`remove_board` deletes anything.
    """
    if _board_row(conn, source_id) is None:
        return {"ok": False, "error": "unknown_board", "source_id": source_id}
    conn.execute(
        "UPDATE monitor_sources SET enabled = ?, updated_at = ? "
        "WHERE source_id = ? AND source_type = 'asana'",
        (1 if enabled else 0, datetime.now(timezone.utc).isoformat(), source_id),
    )
    conn.commit()
    return {"ok": True, "source_id": source_id, "enabled": bool(enabled)}


#
# ── attribution: MANY-TO-MANY, stored in task_board_links ───────────
#
# Which boards track a task is read from ``task_board_links`` (migration 055),
# written by BOTH asana_monitor poll sites: the board that creates a row and a
# board that reconciles a row it did not create.
#
# Two earlier models failed here, and neither failure was a bug in the code
# that read them — both were the schema being unable to state the truth:
#
#   1. DERIVED FROM THE PERMALINK. permalink_url names a task's HOME project,
#      and Asana tasks are routinely multi-homed, so a task polled by board B
#      carried board A's link.
#   2. A SINGLE STORED COLUMN (enablement_tasks.board_source_id, migration
#      054). It records a true fact — the creating board — but ownership is
#      genuinely many-to-many: an Asana library custom field has ONE gid
#      org-wide, so discovery resolves the same indicator for every mapped
#      project and two boards routinely poll the same task. The column could
#      hold only one of them, ownership fell to whichever source_id sorted
#      first, and removing that board deleted a row the OTHER, still-mapped
#      board was actively tracking — scratchpad, subtask state and extras with
#      it, unrecoverable because that board's cursor was already past the task.
#
# So: nothing below derives attribution from ``source_url`` or from
# ``board_source_id``. The column survives as immutable provenance only.
#
# The two rules point in OPPOSITE directions, on purpose:
#
#   DELETION FAILS CLOSED — a task is deleted only when EVERY link pointing at
#   it belongs to the board being removed. Co-owned tasks survive; unlinked
#   rows (pre-054/055, manual, demo) survive every removal. The worst case is a
#   stray row an operator can dismiss by hand; the alternative is silent,
#   unrecoverable loss of another board's work.
#
#   DISPLAY FAILS OPEN — a task is hidden only when EVERY board linked to it is
#   a configured board with calendar off. Unlinked rows, rows linked to an
#   unmapped board, and all rows when no board is mapped stay visible. Hiding on
#   ambiguity is what produced the "calendar renders nothing even with every
#   board switched on" symptom.


def board_task_ids(conn: sqlite3.Connection, source_id: str) -> list[str]:
    """Every task LINKED to this board — including tasks another board also
    tracks. This is what the board "has", so it is what the Settings card
    counts: a co-owned task appears under BOTH boards, honestly, rather than
    arbitrarily under whichever source_id sorted first.

    It is deliberately NOT the delete set — see
    :func:`board_deletable_task_ids`.
    """
    from src.data import enablement_tasks as etasks  # noqa: PLC0415
    if _board_row(conn, source_id) is None or not etasks._has_link_table(conn):
        return []
    return [r[0] for r in conn.execute(
        "SELECT l.task_id FROM task_board_links l "
        "JOIN enablement_tasks t ON t.task_id = l.task_id "
        "WHERE l.board_source_id = ? ORDER BY l.task_id",
        (source_id,),
    ).fetchall()]


def board_deletable_task_ids(conn: sqlite3.Connection, source_id: str) -> list[str]:
    """The tasks a removal of this board would ACTUALLY delete.

    Linked to this board and to NO other board. A task another mapped (or even
    unmapped-but-linked) board still tracks is excluded, because deleting it
    would destroy that board's live work — the exact defect this model exists
    to make unrepresentable.

    This list is both what :func:`remove_board` deletes and what the confirm
    dialog counts, so the two can never disagree.
    """
    from src.data import enablement_tasks as etasks  # noqa: PLC0415
    if _board_row(conn, source_id) is None or not etasks._has_link_table(conn):
        return []
    return [r[0] for r in conn.execute(
        "SELECT l.task_id FROM task_board_links l "
        "JOIN enablement_tasks t ON t.task_id = l.task_id "
        "WHERE l.board_source_id = ? AND NOT EXISTS ("
        "  SELECT 1 FROM task_board_links o "
        "  WHERE o.task_id = l.task_id AND o.board_source_id != l.board_source_id) "
        "ORDER BY l.task_id",
        (source_id,),
    ).fetchall()]


def calendar_task_filter(conn: sqlite3.Connection):
    """Predicate deciding whether a task row may appear on the Calendar.

    Non-Asana rows always pass. An Asana row is hidden ONLY when EVERY board
    linked to it is a configured board whose ``calendar`` flag is off — so one
    opted-in board is enough to keep a co-owned task on the calendar, and the
    flag defaulting off still means a freshly mapped board syncs into the task
    list without silently repainting the calendar.

    Everything else passes: an unlinked row (pre-055, demo seed, manual), a row
    linked only to boards that are no longer configured, and every row when no
    board is mapped at all. Display fails OPEN, so a filter can never be the
    reason an operator's calendar is empty.

    Rows may carry their own ``board_source_ids`` list (that is what
    ``EnablementPage._tasks_to_rows`` ships); otherwise the links are looked up
    by ``task_id``. Neither ``source_url`` nor ``board_source_id`` is consulted.
    """
    from src.data import enablement_tasks as etasks  # noqa: PLC0415
    flags: dict[str, bool] = {
        b["source_id"]: bool((b.get("config") or {}).get("calendar"))
        for b in get_asana_config(conn, include_disabled=True)
    }
    links = etasks.task_board_map(conn)

    def _linked_boards(row: dict) -> list[str]:
        carried = row.get("board_source_ids")
        if isinstance(carried, (list, tuple, set)):
            return [str(s) for s in carried if s]
        return list(links.get(str(row.get("task_id") or ""), ()))

    def allow(row: dict) -> bool:
        if (row.get("source") or "") != "asana":
            return True
        boards = _linked_boards(row)
        if not boards:
            return True
        return not all(sid in flags and not flags[sid] for sid in boards)

    return allow


def remove_board(conn: sqlite3.Connection, source_id: str) -> dict:
    """Unmap a board: DROP THIS BOARD'S CLAIM, and delete only what nothing
    else claims.

    Three steps in one transaction, so a board can never survive its links (or
    vice versa):
      1. delete this board's links,
      2. delete the task rows that had no OTHER board's link (subtasks / extras
         / remaining links follow via ON DELETE CASCADE),
      3. delete the monitor_sources row.

    A co-owned task SURVIVES intact — its scratchpad, subtasks, extras and
    status are another board's live working state, and that board is still
    mapped, still enabled and still tracking the task in Asana.

    LOCAL ONLY, and structurally so — this function performs DELETEs against
    our SQLite database and holds no Asana client, no HTTP verb and no task gid
    it could write back with. The Asana project is not modified in any way; the
    same tasks reappear if the board is mapped again and re-polled.
    """
    from src.data.connection_factory import atomic  # noqa: PLC0415
    row = _board_row(conn, source_id)
    if row is None:
        return {"ok": False, "error": "unknown_board",
                "source_id": source_id, "tasks_deleted": 0, "tasks_kept": 0}
    linked = board_task_ids(conn, source_id)
    doomed = board_deletable_task_ids(conn, source_id)
    kept = len(linked) - len(doomed)
    with atomic(conn):
        conn.execute("DELETE FROM task_board_links WHERE board_source_id = ?",
                     (source_id,))
        for tid in doomed:
            conn.execute("DELETE FROM enablement_tasks WHERE task_id = ?", (tid,))
        conn.execute(
            "DELETE FROM monitor_sources WHERE source_id = ? AND source_type = 'asana'",
            (source_id,),
        )
    logger.info(
        "Removed Asana board %s locally (%d tasks deleted, %d kept for other "
        "boards; Asana untouched)", source_id, len(doomed), kept)
    return {"ok": True, "source_id": source_id,
            "display_name": row[1] or source_id,
            "tasks_deleted": len(doomed), "tasks_kept": kept,
            "remote_write": False}


def board_summary(conn: sqlite3.Connection) -> list[dict]:
    """Everything the Settings > Sources panel renders, read from the database.

    One row per configured board — no literals, no sample data.

    Three counts, because with many-to-many ownership one number cannot be both
    honest and actionable:
      ``task_count``      — everything linked to this board. A co-owned task is
                            counted under BOTH boards, so these may sum to more
                            than the total number of tasks. That is correct, and
                            the panel says so rather than hiding it.
      ``shared_task_count`` — how many of those another board also tracks.
      ``deletable_task_count`` — what a removal would actually delete. This is
                            the number the confirm dialog must quote.
    """
    out: list[dict] = []
    for b in get_asana_config(conn, include_disabled=True):
        cfg = b.get("config") or {}
        row = _board_row(conn, b["source_id"])
        ind = (cfg.get("indicators") or [{}])[0]
        field_name = ind.get("field_name") or ""
        values = ind.get("trigger_value_names") or []
        maps = cfg.get("mappings") or {}
        gid = str(cfg.get("project_gid") or "")
        linked = len(board_task_ids(conn, b["source_id"]))
        doomed = len(board_deletable_task_ids(conn, b["source_id"]))
        out.append({
            "source_id": b["source_id"],
            "project_gid": gid,
            "project_name": cfg.get("project_name") or b.get("display_name") or b["source_id"],
            "project_url": f"app.asana.com/0/{gid}" if gid else "",
            "indicator_field_name": field_name,
            "indicator_value_name": ", ".join(v for v in values if v),
            "indicator": (f"{field_name} = {', '.join(v for v in values if v)}"
                          if field_name and values else ""),
            "priority_field": maps.get("priority_field_name") or maps.get("priority_field_gid") or "",
            "assignee_field": maps.get("assignee_field_name") or maps.get("assignee_field_gid") or "",
            "enabled": bool(b.get("enabled")),
            "calendar": bool(cfg.get("calendar")),
            "task_count": linked,
            "shared_task_count": linked - doomed,
            "deletable_task_count": doomed,
            "last_status": (row[4] if row is not None else "") or "",
            "last_error": (row[5] if row is not None else "") or "",
        })
    return out
