"""Asana board management — per-board flags, LOCAL-only removal, and the
Settings card that finally renders from the database.

The bug this build closes: `SettingsPage._asana()` was a hardcoded mockup. It
printed "2 configured", two invented board names, invented field mappings and a
literal "Resolved: J. Rivera, M. Chen, A. Osei  (+4)" while `monitor_sources`
was empty and `enablement_tasks` had zero rows. The operator read someone else's
example data off a screen that had never touched the database.

The load-bearing invariant here is narrower and harder: removing a board is
destructive to LOCAL rows and is reachable by an LLM, so the removal path must
be structurally incapable of touching Asana, and the model must never be able to
complete it unattended.
"""

from __future__ import annotations

import inspect
import json
import re

import pytest

from src.data import asana_setup, enablement_tasks


# ── helpers ──────────────────────────────────────────────────────────

def _add_board(conn, gid: str, name: str, **kw):
    return asana_setup.set_asana_board_config(
        conn, project_gid=gid, project_name=name,
        indicator_field_gid="F1", indicator_field_name="Assigned Team",
        indicator_value_gid="V1", indicator_value_name="Enablement",
        priority_field_gid="P1", assignee_field_gid="A1",
        priority_field_name="Urgency", assignee_field_name="Assigned People",
        **kw)


def _add_task(conn, gid: str, task_gid: str, title: str, *,
              home_gid: str | None = None, attributed: bool = True,
              also_gids: tuple = ()):
    """A task shaped exactly like asana_monitor's poll writes one.

    ``gid`` is the project gid of the board that CREATED the row — provenance in
    ``board_source_id`` plus the first ``task_board_links`` row. ``also_gids``
    are further boards that poll the same multi-homed task and reconcile the row
    they did not create; each takes a link of its own, because ownership is
    many-to-many and both boards genuinely track the task.

    ``home_gid`` is the project the PERMALINK names, which for a multi-homed
    task is a different project entirely; it defaults to the creating board only
    because single-homed is the common shape. Nothing reads it — it is here so
    fixtures stop encoding the assumption the defect violates.

    ``attributed=False`` produces a pre-054/055 row: no provenance, no link.
    """
    tid = enablement_tasks.create_task(
        conn, source="asana", kind="request", title=title,
        source_ref=task_gid,
        source_url=f"https://app.asana.com/0/{home_gid or gid}/{task_gid}",
        board_source_id=(f"asana:{gid}" if attributed else None),
        created_by="agent")
    if attributed:
        enablement_tasks.link_task_board(conn, tid, f"asana:{gid}")
    for other in also_gids:
        enablement_tasks.link_task_board(conn, tid, f"asana:{other}")
    return tid


# ══════════════════════════════════════════════════════════════════════
# The Asana fence — the removal path can never reach Asana
# ══════════════════════════════════════════════════════════════════════

_ASANA_WRITE_NAMES = ("create_subtask", "add_comment", "update_due_date",
                      "_send", "AsanaClient")


def _removal_path_sources() -> dict:
    from src.ui.pages.enablement.page import EnablementPage
    from src.data.chat_tools import enablement_tools
    return {
        "asana_setup.remove_board": inspect.getsource(asana_setup.remove_board),
        "asana_setup.board_task_ids": inspect.getsource(asana_setup.board_task_ids),
        "asana_setup.board_deletable_task_ids":
            inspect.getsource(asana_setup.board_deletable_task_ids),
        "page._confirm_and_remove_board":
            inspect.getsource(EnablementPage._confirm_and_remove_board),
        "page._on_board_remove_requested":
            inspect.getsource(EnablementPage._on_board_remove_requested),
        "tool.handle_remove_asana_board":
            inspect.getsource(enablement_tools.handle_remove_asana_board),
    }


def test_removal_path_never_references_an_asana_write():
    """Structural: nothing on the removal path names a write verb or the client.

    Not a comment — a test, because the whole safety story is "local rows are
    ours, the Asana project is not" and the cheapest way to break it is a
    convenience import three refactors from now.
    """
    offenders = []
    for where, src in _removal_path_sources().items():
        for name in _ASANA_WRITE_NAMES:
            if re.search(rf"\b{re.escape(name)}\b", src):
                offenders.append(f"{where} references {name}")
    assert offenders == [], offenders


def test_asana_client_still_exposes_no_delete_shaped_method():
    """The capability was never written. If someone adds one, this trips loudly
    rather than the removal quietly gaining a way to reach Asana."""
    from src.data.asana_client import AsanaClient
    banned = re.compile(r"delete|destroy|remove|purge|archive|trash", re.I)
    offenders = [n for n in dir(AsanaClient)
                 if not n.startswith("__") and banned.search(n)]
    assert offenders == [], (
        f"AsanaClient grew a delete-shaped method: {offenders}. Removing a board "
        "is a LOCAL act by owner decision — do not give it a remote counterpart.")


def test_remove_board_makes_zero_calls_on_an_injected_client(empty_db, monkeypatch):
    """Probe: hand the whole module a recording client and assert it is untouched."""
    calls = []

    class _Spy:
        def __init__(self, *a, **kw):
            calls.append(("__init__", a, kw))

        def __getattr__(self, name):
            def _rec(*a, **kw):
                calls.append((name, a, kw))
            calls.append(("getattr", name, {}))
            return _rec

        @classmethod
        def from_store(cls, *a, **kw):
            calls.append(("from_store", a, kw))
            return cls()

    import src.data.asana_client as ac
    monkeypatch.setattr(ac, "AsanaClient", _Spy)

    conn = empty_db.conn
    _add_board(conn, "111", "Board One")
    _add_task(conn, "111", "9001", "Task A")
    res = asana_setup.remove_board(conn, "asana:111")

    assert res["ok"] is True and res["tasks_deleted"] == 1
    assert res["remote_write"] is False
    assert calls == [], f"the removal path talked to Asana: {calls}"


# ══════════════════════════════════════════════════════════════════════
# Per-board flags
# ══════════════════════════════════════════════════════════════════════

def test_a_new_board_is_not_on_the_calendar(empty_db):
    conn = empty_db.conn
    _add_board(conn, "111", "Board One")
    board = asana_setup.board_summary(conn)[0]
    assert board["calendar"] is False
    assert board["enabled"] is True
    assert board["project_gid"] == "111"
    assert board["indicator"] == "Assigned Team = Enablement"
    assert board["priority_field"] == "Urgency"
    assert board["assignee_field"] == "Assigned People"


def test_set_board_calendar_round_trips_and_survives_a_config_re_save(empty_db):
    """Re-running setup must not silently drop an opt-in the operator made."""
    conn = empty_db.conn
    _add_board(conn, "111", "Board One")
    assert asana_setup.set_board_calendar(conn, "asana:111", True)["ok"] is True
    assert asana_setup.board_summary(conn)[0]["calendar"] is True
    _add_board(conn, "111", "Board One renamed")
    assert asana_setup.board_summary(conn)[0]["calendar"] is True
    # …and the explicit off switch still works.
    asana_setup.set_board_calendar(conn, "asana:111", False)
    assert asana_setup.board_summary(conn)[0]["calendar"] is False


def test_sync_toggle_actually_gates_the_poller(empty_db):
    """enabled=0 must remove the board from what poll_once reads — otherwise the
    switch is decoration, which is the failure mode this whole build is about."""
    conn = empty_db.conn
    _add_board(conn, "111", "Board One")
    asana_setup.set_board_enabled(conn, "asana:111", False)
    assert asana_setup.get_asana_config(conn) == []
    assert len(asana_setup.get_asana_config(conn, include_disabled=True)) == 1
    # The Settings panel still shows it (you can't switch back on what you can't see).
    assert asana_setup.board_summary(conn)[0]["enabled"] is False
    asana_setup.set_board_enabled(conn, "asana:111", True)
    assert len(asana_setup.get_asana_config(conn)) == 1


def test_flag_writers_reject_an_unknown_board(empty_db):
    conn = empty_db.conn
    assert asana_setup.set_board_calendar(conn, "asana:nope", True)["error"] == "unknown_board"
    assert asana_setup.set_board_enabled(conn, "asana:nope", True)["error"] == "unknown_board"
    assert asana_setup.remove_board(conn, "asana:nope")["error"] == "unknown_board"


# ══════════════════════════════════════════════════════════════════════
# Calendar inclusion is per-board — and DISPLAY FAILS OPEN
#
# The opposite of the deletion rule, deliberately: a row that cannot be
# attributed is SHOWN, never hidden. Hiding on ambiguity is what produced
# "every board switched on and the calendar still renders nothing".
# ══════════════════════════════════════════════════════════════════════

def test_only_calendar_boards_reach_the_calendar_feed(empty_db):
    conn = empty_db.conn
    _add_board(conn, "111", "Board One")
    _add_board(conn, "222", "Board Two")
    asana_setup.set_board_calendar(conn, "asana:222", True)

    rows = [
        {"source": "asana", "source_ref": "9001",
         "board_source_ids": ["asana:111"], "title": "from one"},
        {"source": "asana", "source_ref": "9002",
         "board_source_ids": ["asana:222"], "title": "from two"},
        {"source": "drive", "source_url": "", "title": "a drive task"},
    ]
    allow = asana_setup.calendar_task_filter(conn)
    assert [r["title"] for r in rows if allow(r)] == ["from two", "a drive task"]


def test_calendar_filter_ignores_the_permalink_entirely(empty_db):
    """A multi-homed task's permalink names its HOME project, not a board that
    tracks it. The filter must judge by the stored links alone."""
    conn = empty_db.conn
    _add_board(conn, "111", "Board One")                    # calendar OFF
    _add_board(conn, "222", "Board Two", calendar=True)
    allow = asana_setup.calendar_task_filter(conn)
    # Tracked by Two (calendar ON), home-projected into One (calendar OFF) → shown.
    assert allow({"source": "asana", "source_ref": "9002",
                  "board_source_ids": ["asana:222"],
                  "source_url": "https://app.asana.com/0/111/9002"}) is True
    # Tracked by One (calendar OFF), home-projected into Two (calendar ON) → hidden.
    assert allow({"source": "asana", "source_ref": "9003",
                  "board_source_ids": ["asana:111"],
                  "source_url": "https://app.asana.com/0/222/9003"}) is False


def test_one_opted_in_board_keeps_a_co_owned_task_on_the_calendar(empty_db):
    """DISPLAY FAILS OPEN over links: hidden only when EVERY linked board is a
    configured board with calendar off. One board saying yes is enough."""
    conn = empty_db.conn
    _add_board(conn, "111", "Board One")                    # calendar OFF
    _add_board(conn, "222", "Board Two", calendar=True)     # calendar ON
    allow = asana_setup.calendar_task_filter(conn)
    both = ["asana:111", "asana:222"]
    assert allow({"source": "asana", "board_source_ids": both}) is True
    assert allow({"source": "asana", "board_source_ids": ["asana:111"]}) is False
    # A link to a board nobody mapped is not evidence of an opt-OUT either.
    assert allow({"source": "asana",
                  "board_source_ids": ["asana:111", "asana:gone"]}) is True
    # …and switching the only opted-in board off finally hides it.
    asana_setup.set_board_calendar(conn, "asana:222", False)
    assert asana_setup.calendar_task_filter(conn)(
        {"source": "asana", "board_source_ids": both}) is False


def test_calendar_filter_reads_links_from_the_db_when_a_row_carries_none(empty_db):
    """A row that only knows its task_id still gets judged by its real links —
    the filter never falls back to the provenance column or the permalink."""
    conn = empty_db.conn
    _add_board(conn, "111", "Board One")                    # calendar OFF
    _add_board(conn, "222", "Board Two", calendar=True)
    tid = _add_task(conn, "111", "9002", "Co-owned", also_gids=("222",))
    solo = _add_task(conn, "111", "9003", "One only")
    allow = asana_setup.calendar_task_filter(conn)
    assert allow({"source": "asana", "task_id": tid}) is True
    assert allow({"source": "asana", "task_id": solo}) is False


def test_calendar_filter_passes_everything_when_no_board_is_mapped(empty_db):
    """With no board there is no flag to honour — hiding rows behind a gate
    nobody can reach would just lose data silently."""
    allow = asana_setup.calendar_task_filter(empty_db.conn)
    assert allow({"source": "asana", "source_url": "", "title": "orphan"}) is True


def test_unattributable_rows_are_shown_not_hidden(empty_db):
    """The old empty-calendar symptom, from the operator's side: two boards,
    Calendar switched ON for BOTH, and rows that cannot be attributed. Every one
    of these was hidden by the permalink heuristic."""
    conn = empty_db.conn
    _add_board(conn, "111", "Board One", calendar=True)
    _add_board(conn, "222", "Board Two", calendar=True)
    allow = asana_setup.calendar_task_filter(conn)
    # no source_url at all
    assert allow({"source": "asana", "source_ref": "9001",
                  "source_url": None, "title": "no link"}) is True
    # permalink naming a project no board is mapped to
    assert allow({"source": "asana", "source_ref": "9002",
                  "source_url": "https://app.asana.com/0/999/9002"}) is True
    # linked only to a board that has since been unmapped
    assert allow({"source": "asana", "source_ref": "9003",
                  "board_source_ids": ["asana:gone"]}) is True
    # pre-055 row: no links at all
    assert allow({"source": "asana", "source_ref": "9004",
                  "board_source_ids": []}) is True


def test_page_calendar_feed_filter_drops_only_opted_out_asana_rows(empty_db):
    """The page-level helper both calendar feed points go through."""
    from src.ui.pages.enablement.page import EnablementPage
    conn = empty_db.conn
    _add_board(conn, "111", "Board One")
    _add_board(conn, "222", "Board Two", calendar=True)
    rows = [
        {"source": "asana", "source_ref": "1", "board_source_ids": ["asana:111"]},
        {"source": "asana", "source_ref": "2", "board_source_ids": ["asana:222"]},
        {"source": "guru", "source_url": ""},
    ]
    out = EnablementPage._calendar_rows(object(), rows, conn)
    assert out == [rows[1], rows[2]]


def test_production_feed_carries_what_the_filter_needs(empty_db):
    """The rows the app actually builds must be the shape the filter judges.

    _tasks_to_rows is the ONLY producer of the calendar feed. When it dropped
    the attribution key, every production row was unattributable — the tests
    passed on hand-written dicts the real feed never produced. It now has to
    carry the LIST of linked boards, not a single winner.
    """
    from src.ui.pages.enablement.page import EnablementPage
    from src.data import enablement_tasks as et
    conn = empty_db.conn
    _add_board(conn, "111", "Board One")                    # calendar OFF
    _add_board(conn, "222", "Board Two", calendar=True)
    _add_task(conn, "111", "9001", "One A")
    _add_task(conn, "222", "9002", "Two A", home_gid="111")  # multi-homed
    _add_task(conn, "111", "9004", "Co-owned", also_gids=("222",))
    _add_task(conn, "333", "9003", "Unmapped board", attributed=False)

    rows = EnablementPage._tasks_to_rows(EnablementPage,
                                         et.list_tasks(conn, source="asana"), conn)
    assert {r["title"]: sorted(r["board_source_ids"]) for r in rows} == {
        "One A": ["asana:111"], "Two A": ["asana:222"],
        "Co-owned": ["asana:111", "asana:222"], "Unmapped board": []}
    assert all("source_ref" in r for r in rows)
    shown = {r["title"] for r in EnablementPage._calendar_rows(object(), rows, conn)}
    assert shown == {"Two A", "Co-owned", "Unmapped board"}


# ══════════════════════════════════════════════════════════════════════
# Attribution is captured by the poll — the one place that knows
# ══════════════════════════════════════════════════════════════════════

class _FakePollClient:
    """Minimal Asana client for the legacy (modified_since) poll path."""

    def __init__(self, tasks):
        self._tasks = tasks

    def list_tasks(self, project_gid, modified_since=None):
        return self._tasks

    def list_subtasks(self, task_gid):
        return []


def test_the_poll_stores_the_polling_board_not_the_permalinks_project(empty_db,
                                                                     monkeypatch):
    """Board Two polls a task whose permalink names Board One's project.

    The permalink is Asana's statement about the task's HOME project and can
    never be a statement about which board imported the row. Only the poll knows
    that, so the poll is what writes it.
    """
    from src.data import asana_monitor as am
    monkeypatch.setattr(am, "_use_events", lambda: False)
    conn = empty_db.conn
    _add_board(conn, "111", "Board One")
    _add_board(conn, "222", "Board Two")
    asana_setup.set_board_enabled(conn, "asana:111", False)   # only Two polls

    client = _FakePollClient([{
        "gid": "9002", "name": "Multi-homed request", "completed": False,
        "modified_at": "2026-07-28T00:00:00Z",
        "permalink_url": "https://app.asana.com/0/111/9002",
        "custom_fields": [{"gid": "F1", "enum_value": {"gid": "V1",
                                                       "name": "Enablement"}}],
    }])
    created = am.poll_once(conn, client=client)
    assert len(created) == 1
    row = conn.execute(
        "SELECT board_source_id, source_url FROM enablement_tasks WHERE task_id=?",
        (created[0],)).fetchone()
    assert row[0] == "asana:222"
    assert "/0/111/" in row[1], "the permalink is stored verbatim — and ignored"
    assert enablement_tasks.task_board_ids(conn, created[0]) == ["asana:222"]
    assert asana_setup.board_task_ids(conn, "asana:222") == [created[0]]
    assert asana_setup.board_task_ids(conn, "asana:111") == []


def test_two_boards_polling_one_multi_homed_task_both_claim_it(empty_db, monkeypatch):
    """THE surviving case, driven through the real poll.

    An Asana library custom field carries ONE gid org-wide, so discovery
    resolves the SAME indicator field/value for both projects — the natural
    setup, not a contrived one. Board One creates the row; Board Two finds it
    already tracked, reconciles it on source_ref, and advances its own cursor
    over it. A single poll cycle therefore reports the task as BOTH created and
    updated: co-ownership is observed right here, and the previous model threw
    it away.
    """
    from src.data import asana_monitor as am
    monkeypatch.setattr(am, "_use_events", lambda: False)
    conn = empty_db.conn
    _add_board(conn, "111", "Launch Coordination")
    _add_board(conn, "222", "Enablement Requests")

    client = _FakePollClient([{
        "gid": "9300", "name": "Multi-homed request", "completed": False,
        "modified_at": "2026-07-28T00:00:00Z",
        "permalink_url": "https://app.asana.com/0/111/9300",   # home = One
        "custom_fields": [{"gid": "F1", "enum_value": {"gid": "V1",
                                                       "name": "Enablement"}}],
    }])
    results: dict = {}
    created = am.poll_once(conn, client=client, results=results)
    assert len(created) == 1
    tid = created[0]
    assert results["updated"] == [tid], (
        "the second board must reconcile the row the first created — that pair "
        "is the co-ownership signal")

    assert enablement_tasks.task_board_ids(conn, tid) == ["asana:111", "asana:222"]
    assert asana_setup.board_task_ids(conn, "asana:111") == [tid]
    assert asana_setup.board_task_ids(conn, "asana:222") == [tid]
    # Neither board could delete it on its own.
    assert asana_setup.board_deletable_task_ids(conn, "asana:111") == []
    assert asana_setup.board_deletable_task_ids(conn, "asana:222") == []
    # Re-polling is idempotent — links do not multiply.
    am.poll_once(conn, client=client)
    assert enablement_tasks.task_board_ids(conn, tid) == ["asana:111", "asana:222"]


def test_co_ownership_does_not_depend_on_which_source_id_sorts_first(empty_db,
                                                                    monkeypatch):
    """Ownership used to fall out of get_asana_config's ORDER BY source_id, so
    the lexicographically smallest board won regardless of mapping order. With
    links there is no winner to pick: both boards claim it either way."""
    from src.data import asana_monitor as am
    monkeypatch.setattr(am, "_use_events", lambda: False)
    conn = empty_db.conn
    _add_board(conn, "999", "Mapped first")
    _add_board(conn, "111", "Mapped second")
    client = _FakePollClient([{
        "gid": "9300", "name": "Multi-homed", "completed": False,
        "modified_at": "2026-07-28T00:00:00Z",
        "permalink_url": "https://app.asana.com/0/111/9300",
        "custom_fields": [{"gid": "F1", "enum_value": {"gid": "V1"}}],
    }])
    tid = am.poll_once(conn, client=client)[0]
    assert enablement_tasks.task_board_ids(conn, tid) == ["asana:111", "asana:999"]


def test_attribution_is_not_editable_after_creation(empty_db):
    """update_task must not be able to re-attribute a row — ownership is a fact
    from the poll, not a field the UI or a tool can set. The provenance column
    is out of _UPDATABLE, and the link table is not reachable through it at all.
    """
    assert "board_source_id" not in enablement_tasks._UPDATABLE
    conn = empty_db.conn
    _add_board(conn, "111", "Board One")
    _add_board(conn, "222", "Board Two")
    tid = _add_task(conn, "222", "9002", "Two A")
    enablement_tasks.update_task(conn, tid, board_source_id="asana:111")
    assert enablement_tasks.task_board_ids(conn, tid) == ["asana:222"]
    assert asana_setup.board_task_ids(conn, "asana:222") == [tid]
    assert asana_setup.board_task_ids(conn, "asana:111") == []


def test_the_provenance_column_is_never_the_authority(empty_db):
    """054's column stays as provenance and MUST NOT be a second source of
    truth: a row carrying board_source_id with no link belongs to no board —
    not counted, not deleted, not hidden."""
    conn = empty_db.conn
    _add_board(conn, "111", "Board One")                    # calendar OFF
    tid = enablement_tasks.create_task(
        conn, source="asana", kind="request", title="Provenance only",
        source_ref="9500", board_source_id="asana:111")
    assert enablement_tasks.task_board_ids(conn, tid) == []
    assert asana_setup.board_task_ids(conn, "asana:111") == []
    assert asana_setup.board_summary(conn)[0]["task_count"] == 0
    assert asana_setup.remove_board(conn, "asana:111")["tasks_deleted"] == 0
    assert enablement_tasks.get_task(conn, tid) is not None


# ══════════════════════════════════════════════════════════════════════
# Removal: exactly this board's rows, and nothing in Asana
# ══════════════════════════════════════════════════════════════════════

def test_remove_deletes_only_that_boards_tasks(empty_db):
    conn = empty_db.conn
    _add_board(conn, "111", "Board One")
    _add_board(conn, "222", "Board Two")
    _add_task(conn, "111", "9001", "One A")
    _add_task(conn, "111", "9002", "One B")
    keep = _add_task(conn, "222", "9003", "Two A")
    manual = enablement_tasks.create_task(
        conn, source="manual", kind="request", title="Hand-written")

    summary = {b["source_id"]: b for b in asana_setup.board_summary(conn)}
    assert summary["asana:111"]["task_count"] == 2
    assert summary["asana:222"]["task_count"] == 1

    res = asana_setup.remove_board(conn, "asana:111")
    assert res == {"ok": True, "source_id": "asana:111",
                   "display_name": "Board One", "tasks_deleted": 2,
                   "tasks_kept": 0, "remote_write": False}

    left = {r[0] for r in conn.execute(
        "SELECT task_id FROM enablement_tasks").fetchall()}
    assert left == {keep, manual}
    assert [b["source_id"] for b in asana_setup.board_summary(conn)] == ["asana:222"]


def test_remove_takes_the_board_row_and_the_tasks_together(empty_db):
    conn = empty_db.conn
    _add_board(conn, "111", "Board One")
    _add_task(conn, "111", "9001", "One A")
    asana_setup.remove_board(conn, "asana:111")
    assert conn.execute(
        "SELECT COUNT(*) FROM monitor_sources WHERE source_id='asana:111'"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM enablement_tasks").fetchone()[0] == 0


def test_removing_a_board_never_deletes_a_multi_homed_task_of_another_board(empty_db):
    """THE data-loss defect, as the operator hit it.

    Board Two polls gid 9002; the task is multi-homed and its permalink names
    Board One's project. Deriving attribution from that permalink said "One owns
    it", so removing Board One deleted Board Two's row — with its scratchpad and
    subtask state — while Board Two stayed mapped and enabled. A re-poll did not
    bring it back: Two's modified_since cursor was already past that task, so
    the loss stood until someone edited the task in Asana.
    """
    conn = empty_db.conn
    _add_board(conn, "111", "Board One")
    _add_board(conn, "222", "Board Two")
    one = _add_task(conn, "111", "9001", "One A")
    multi = _add_task(conn, "222", "9002", "Two A — home-projected into One",
                      home_gid="111")
    enablement_tasks.set_scratchpad(conn, multi, "operator's local notes")
    enablement_tasks.add_subtask(conn, multi, "step one", done=True)

    summary = {b["source_id"]: b for b in asana_setup.board_summary(conn)}
    assert summary["asana:111"]["task_count"] == 1
    assert summary["asana:222"]["task_count"] == 1, (
        "Board Two must own the task it polled, whatever the permalink says")

    res = asana_setup.remove_board(conn, "asana:111")
    assert res["tasks_deleted"] == 1

    left = {r[0] for r in conn.execute(
        "SELECT task_id FROM enablement_tasks").fetchall()}
    assert left == {multi}, "Board Two's multi-homed task was deleted with Board One"
    assert one not in left
    survived = enablement_tasks.get_task(conn, multi)
    assert survived["scratchpad"] == "operator's local notes"
    assert len(enablement_tasks.list_subtasks(conn, multi)) == 1


def test_remove_leaves_an_unattributed_row_alone_when_boards_are_ambiguous(empty_db):
    """Deletion FAILS CLOSED: a row with no stored board belongs to no board."""
    conn = empty_db.conn
    _add_board(conn, "111", "Board One")
    _add_board(conn, "222", "Board Two")
    orphan = enablement_tasks.create_task(
        conn, source="asana", kind="request", title="No permalink",
        source_ref="9999", source_url="")
    res = asana_setup.remove_board(conn, "asana:111")
    assert res["tasks_deleted"] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM enablement_tasks WHERE task_id=?", (orphan,)
    ).fetchone()[0] == 1


def test_remove_leaves_an_unattributed_row_alone_even_as_the_only_board(empty_db):
    """The old "no configured board and exactly one board mapped → it's ours"
    rule is GONE. It was the rule that made stray rows deletable, and "only one
    board is mapped right now" is not evidence about a row created before the
    others were removed. Pre-054 rows (NULL attribution) survive every removal.
    """
    conn = empty_db.conn
    _add_board(conn, "111", "Board One")
    legacy = _add_task(conn, "111", "9999", "Pre-054 row", attributed=False)
    assert asana_setup.board_summary(conn)[0]["task_count"] == 0
    assert asana_setup.remove_board(conn, "asana:111")["tasks_deleted"] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM enablement_tasks WHERE task_id=?", (legacy,)
    ).fetchone()[0] == 1


def test_a_co_owned_task_survives_the_removal_of_one_of_its_boards(empty_db,
                                                                  monkeypatch):
    """THE surviving defect, end to end, from the operator's side.

    Both boards poll gid 9300 (same library custom field → same indicator), so
    both track it. The operator then does LOCAL work on the row: a scratchpad
    note, a ticked subtask, a status, an extras payload. Removing Board One must
    drop only Board One's claim — the row is Board Two's live working state, and
    Board Two is still mapped, still enabled and still tracking the task in
    Asana. A re-poll would not restore it: Two's cursor is already past it.
    """
    from src.data import asana_monitor as am
    monkeypatch.setattr(am, "_use_events", lambda: False)
    conn = empty_db.conn
    _add_board(conn, "111", "Launch Coordination")
    _add_board(conn, "222", "Enablement Requests")
    client = _FakePollClient([{
        "gid": "9300", "name": "Multi-homed request", "completed": False,
        "modified_at": "2026-07-28T00:00:00Z",
        "permalink_url": "https://app.asana.com/0/111/9300",
        "custom_fields": [{"gid": "F1", "enum_value": {"gid": "V1"}}],
    }])
    tid = am.poll_once(conn, client=client)[0]

    enablement_tasks.set_scratchpad(
        conn, tid, "Called payer; waiting on Ana. DO NOT SEND YET.")
    enablement_tasks.add_subtask(conn, tid, "Call the payer", done=True)
    enablement_tasks.update_task(conn, tid, status="in_progress")
    conn.execute("INSERT INTO asana_task_extras (task_id, html_notes, fetched_at) "
                 "VALUES (?,?,?) ON CONFLICT(task_id) DO UPDATE SET "
                 "html_notes=excluded.html_notes",
                 (tid, "<p>intake</p>", "2026-07-28T00:00:00Z"))
    conn.commit()

    res = asana_setup.remove_board(conn, "asana:111")
    assert res["tasks_deleted"] == 0 and res["tasks_kept"] == 1

    survived = enablement_tasks.get_task(conn, tid)
    assert survived is not None, "a still-tracked task was deleted with the board"
    assert survived["scratchpad"] == "Called payer; waiting on Ana. DO NOT SEND YET."
    assert survived["status"] == "in_progress"
    subs = enablement_tasks.list_subtasks(conn, tid)
    assert [(s["text"], bool(s["done"])) for s in subs] == [("Call the payer", True)]
    assert conn.execute("SELECT html_notes FROM asana_task_extras WHERE task_id=?",
                        (tid,)).fetchone()[0] == "<p>intake</p>"
    # …and it is now solely Board Two's.
    assert enablement_tasks.task_board_ids(conn, tid) == ["asana:222"]
    assert asana_setup.board_task_ids(conn, "asana:222") == [tid]


def test_the_second_removal_is_the_one_that_deletes(empty_db, monkeypatch):
    """Remove both boards in sequence: the task goes on the second, not the
    first — and its links go with it."""
    from src.data import asana_monitor as am
    monkeypatch.setattr(am, "_use_events", lambda: False)
    conn = empty_db.conn
    _add_board(conn, "111", "Board One")
    _add_board(conn, "222", "Board Two")
    client = _FakePollClient([{
        "gid": "9300", "name": "Multi-homed", "completed": False,
        "modified_at": "2026-07-28T00:00:00Z",
        "permalink_url": "https://app.asana.com/0/111/9300",
        "custom_fields": [{"gid": "F1", "enum_value": {"gid": "V1"}}],
    }])
    tid = am.poll_once(conn, client=client)[0]
    enablement_tasks.add_subtask(conn, tid, "step one", done=True)

    first = asana_setup.remove_board(conn, "asana:111")
    assert (first["tasks_deleted"], first["tasks_kept"]) == (0, 1)
    assert enablement_tasks.get_task(conn, tid) is not None

    second = asana_setup.remove_board(conn, "asana:222")
    assert (second["tasks_deleted"], second["tasks_kept"]) == (1, 0)
    assert enablement_tasks.get_task(conn, tid) is None
    assert conn.execute("SELECT COUNT(*) FROM task_board_links").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM enablement_subtasks").fetchone()[0] == 0


def test_a_co_owned_task_is_counted_under_both_boards(empty_db):
    """board_summary must not pick a winner. Counts may sum to more than the
    number of tasks — that is what co-ownership means, and the panel says so."""
    conn = empty_db.conn
    _add_board(conn, "111", "Board One")
    _add_board(conn, "222", "Board Two")
    _add_task(conn, "111", "9300", "Co-owned", also_gids=("222",))
    _add_task(conn, "111", "9301", "One's alone")

    summary = {b["source_id"]: b for b in asana_setup.board_summary(conn)}
    assert summary["asana:111"]["task_count"] == 2
    assert summary["asana:222"]["task_count"] == 1
    total = conn.execute("SELECT COUNT(*) FROM enablement_tasks").fetchone()[0]
    assert sum(b["task_count"] for b in summary.values()) > total
    # …and the actionable half of the count is separated out.
    assert summary["asana:111"]["deletable_task_count"] == 1
    assert summary["asana:111"]["shared_task_count"] == 1
    assert summary["asana:222"]["deletable_task_count"] == 0
    assert summary["asana:222"]["shared_task_count"] == 1


def test_the_confirm_count_equals_what_is_deleted(empty_db):
    """The dialog quotes board_summary's deletable_task_count; it has to be the
    same set remove_board acts on, or the operator is told a number that isn't
    true — in the direction that matters, "12 tasks go" when 9 do.

    Loaded with the mix that breaks a naive count: co-owned rows, solely-owned
    rows, and unlinked rows.
    """
    conn = empty_db.conn
    _add_board(conn, "111", "Board One")
    _add_board(conn, "222", "Board Two")
    for i in range(3):
        _add_task(conn, "111", f"90{i}", f"Task {i}")               # solely One's
    _add_task(conn, "111", "9100", "Co-owned", also_gids=("222",))  # both
    _add_task(conn, "222", "9101", "Two's alone", home_gid="111")   # solely Two's
    _add_task(conn, "111", "9200", "Unlinked", attributed=False)    # nobody's

    shown = {b["source_id"]: b for b in asana_setup.board_summary(conn)}
    assert shown["asana:111"]["task_count"] == 4          # what the board tracks
    assert shown["asana:111"]["deletable_task_count"] == 3  # what removal takes
    assert shown["asana:111"]["shared_task_count"] == 1

    before = conn.execute("SELECT COUNT(*) FROM enablement_tasks").fetchone()[0]
    res = asana_setup.remove_board(conn, "asana:111")
    after = conn.execute("SELECT COUNT(*) FROM enablement_tasks").fetchone()[0]
    assert res["tasks_deleted"] == shown["asana:111"]["deletable_task_count"]
    assert res["tasks_deleted"] == before - after
    assert res["tasks_kept"] == shown["asana:111"]["shared_task_count"]


def test_the_dialog_quotes_the_deletable_count_not_the_tracked_count():
    """Structural: the equality above only holds if the dialog reads the right
    field. It read ``task_count`` before, which is now the co-ownership-inclusive
    number and would overstate the deletion."""
    from src.ui.pages.enablement.page import EnablementPage
    src = inspect.getsource(EnablementPage._confirm_and_remove_board)
    assert 'board.get("deletable_task_count")' in src
    assert 'board.get("task_count")' not in src
    assert 'board.get("shared_task_count")' in src, (
        "the dialog must also say plainly that co-owned tasks are kept")


# ══════════════════════════════════════════════════════════════════════
# Renn's tools: propose, never delete
# ══════════════════════════════════════════════════════════════════════

def test_removal_tool_refuses_when_it_cannot_reach_a_dialog(empty_db, monkeypatch):
    from src.data.chat_tools import enablement_tools
    monkeypatch.setattr(enablement_tools, "_BOARD_REMOVAL_CONFIRM", None)
    conn = empty_db.conn
    _add_board(conn, "111", "Board One")
    _add_task(conn, "111", "9001", "One A")

    res = enablement_tools.handle_remove_asana_board(conn, {"source_id": "asana:111"}, {})
    assert res["ok"] is False
    assert res["needs_operator_confirm"] is True
    assert res["proposal"]["local_tasks_to_delete"] == 1
    assert res["proposal"]["deletes_in_asana"] is False
    assert "Settings" in res["message"]
    # …and it deleted nothing.
    assert asana_setup.board_summary(conn)[0]["task_count"] == 1


def test_removal_tool_never_deletes_by_itself_even_with_a_hook(empty_db, monkeypatch):
    """The hook is the ONLY thing that can delete; the tool just calls it. A hook
    that says "no dialog available" (None) must leave the database untouched."""
    from src.data.chat_tools import enablement_tools
    conn = empty_db.conn
    _add_board(conn, "111", "Board One")
    _add_task(conn, "111", "9001", "One A")
    monkeypatch.setattr(enablement_tools, "_BOARD_REMOVAL_CONFIRM", lambda sid: None)
    res = enablement_tools.handle_remove_asana_board(conn, {"project_gid": "111"}, {})
    assert res["ok"] is False and res["needs_operator_confirm"] is True
    assert asana_setup.board_summary(conn)[0]["task_count"] == 1


def test_removal_tool_reports_the_confirmed_removal(empty_db, monkeypatch):
    from src.data.chat_tools import enablement_tools
    conn = empty_db.conn
    _add_board(conn, "111", "Board One")
    _add_task(conn, "111", "9001", "One A")
    monkeypatch.setattr(enablement_tools, "_BOARD_REMOVAL_CONFIRM",
                        lambda sid: asana_setup.remove_board(conn, sid))
    res = enablement_tools.handle_remove_asana_board(conn, {"source_id": "asana:111"}, {})
    assert res["ok"] is True
    assert res["result"]["tasks_deleted"] == 1
    assert asana_setup.board_summary(conn) == []


def test_removal_tool_rejects_an_unknown_board(empty_db, monkeypatch):
    from src.data.chat_tools import enablement_tools
    monkeypatch.setattr(enablement_tools, "_BOARD_REMOVAL_CONFIRM", None)
    res = enablement_tools.handle_remove_asana_board(
        empty_db.conn, {"source_id": "asana:nope"}, {})
    assert res["ok"] is False and res["error"] == "unknown_board"


def test_board_config_tool_defaults_calendar_off_and_says_so(empty_db):
    from src.data.chat_tools import enablement_tools
    conn = empty_db.conn
    args = {"project_gid": "111", "project_name": "Board One",
            "indicator_field_gid": "F1", "indicator_field_name": "Assigned Team",
            "indicator_value_gid": "V1", "indicator_value_name": "Enablement"}
    res = enablement_tools.handle_set_asana_board_config(conn, dict(args), {})
    assert res["calendar"] is False
    assert "OFF" in res["note"]
    res2 = enablement_tools.handle_set_asana_board_config(
        conn, dict(args, project_gid="222", calendar=True), {})
    assert res2["calendar"] is True


def test_list_boards_tool_tells_the_truth_about_an_unmapped_asana(empty_db, monkeypatch):
    """is_asana_connected() only proves a key string exists — which is how the
    old screen could report a healthy Asana while nothing synced."""
    from src.data.chat_tools import enablement_tools
    monkeypatch.setattr(asana_setup, "is_asana_connected", lambda: True)
    res = enablement_tools.handle_list_asana_boards(empty_db.conn, {}, {})
    assert res["key_stored"] is True
    assert res["board_count"] == 0
    assert "nothing syncs" in res["note"]


def test_both_dispatch_paths_expose_the_new_tools():
    from src.data.chat_tools.registry import get_tool_registry
    from src.llm.claude_tools import TOOL_DEFINITIONS, _DISPATCH
    from src.mcp.chat_mcp_server import TOOL_SCHEMAS

    names = {"list_asana_boards", "remove_asana_board"}
    reg = set(get_tool_registry().keys())
    assert names <= reg, f"registry missing {names - reg}"
    assert names <= {t["name"] for t in TOOL_SCHEMAS}
    assert names <= set(_DISPATCH)
    assert names <= {t["name"] for t in TOOL_DEFINITIONS}


def test_removal_tool_schemas_say_it_cannot_delete():
    from src.llm.claude_tools import TOOL_DEFINITIONS  # noqa: PLC0415
    from src.mcp.chat_mcp_server import TOOL_SCHEMAS
    for defs in (TOOL_DEFINITIONS, TOOL_SCHEMAS):
        d = next(t for t in defs if t["name"] == "remove_asana_board")
        assert "does NOT delete" in d["description"]
        assert "NEVER modifies Asana" in d["description"]


# ══════════════════════════════════════════════════════════════════════
# The Settings card renders from data
# ══════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _labels(widget) -> list[str]:
    from PySide6.QtWidgets import QLabel
    return [w.text() for w in widget.findChildren(QLabel)]


@pytest.mark.ui
def test_settings_source_has_no_mockup_literals():
    """The exact strings that shipped as if they were the operator's data."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] /
           "src" / "ui" / "pages" / "enablement" / "settings.py"
           ).read_text(encoding="utf-8")
    # Comment lines are prose about the fix; the ban is on strings the card
    # could actually paint.
    body = "\n".join(ln for ln in src.split("class _BoardToggle", 1)[-1].splitlines()
                     if not ln.lstrip().startswith("#"))
    for literal in ("2 configured", "Enablement Requests", "Launch Coordination",
                    "J. Rivera", "M. Chen", "A. Osei", "Assigned Team  =  Enablement",
                    "app.asana.com/0/120"):
        assert literal not in body, (
            f"settings.py still hardcodes {literal!r} — this card must render "
            "only what the host feeds it")


@pytest.mark.ui
def test_empty_board_list_is_honest_and_actionable(qapp):
    from src.ui.pages.enablement.settings import SettingsPage
    s = SettingsPage()
    s.set_asana_boards([])
    blob = " ".join(_labels(s))
    assert "no Asana tasks will sync" in blob
    assert "ASANA BOARDS" in blob and "configured" not in blob


@pytest.mark.ui
def test_card_renders_the_boards_it_is_fed(qapp, empty_db):
    from src.ui.pages.enablement.settings import SettingsPage
    conn = empty_db.conn
    _add_board(conn, "111", "Provider Onboarding")
    _add_task(conn, "111", "9001", "One A")
    asana_setup.set_board_calendar(conn, "asana:111", True)

    s = SettingsPage()
    s.set_asana_boards(asana_setup.board_summary(conn))
    blob = " ".join(_labels(s))
    assert "ASANA BOARDS  ·  1 configured" in blob
    assert "Provider Onboarding" in blob
    assert "app.asana.com/0/111" in blob
    assert "Assigned Team = Enablement" in blob
    assert "Urgency" in blob and "Assigned People" in blob
    assert "1 imported task" in blob
    assert s.asana_boards()[0]["calendar"] is True


@pytest.mark.ui
def test_toggles_and_remove_emit_for_the_right_board(qapp, empty_db):
    from src.ui.pages.enablement._common import Toggle
    from src.ui.pages.enablement.settings import SettingsPage
    from PySide6.QtWidgets import QPushButton
    conn = empty_db.conn
    _add_board(conn, "111", "Board One")

    s = SettingsPage()
    s.set_asana_boards(asana_setup.board_summary(conn))
    sync, cal, removed = [], [], []
    s.board_sync_toggled.connect(lambda sid, on: sync.append((sid, on)))
    s.board_calendar_toggled.connect(lambda sid, on: cal.append((sid, on)))
    s.board_remove_requested.connect(removed.append)

    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtTest import QTest
    toggles = [t for t in s.findChildren(Toggle) if hasattr(t, "changed")]
    assert len(toggles) == 2, "expected exactly a sync and a calendar switch"
    QTest.mouseClick(toggles[0], _Qt.LeftButton)
    QTest.mouseClick(toggles[1], _Qt.LeftButton)
    assert sync == [("asana:111", False)]
    assert cal == [("asana:111", True)]

    btn = next(b for b in s.findChildren(QPushButton) if b.text() == "Remove")
    btn.click()
    assert removed == ["asana:111"]


@pytest.mark.ui
def test_header_count_is_derived_not_written(qapp, empty_db):
    from src.ui.pages.enablement.settings import SettingsPage
    conn = empty_db.conn
    s = SettingsPage()
    for n, gid in enumerate(("111", "222", "333"), start=1):
        _add_board(conn, gid, f"Board {n}")
        s.set_asana_boards(asana_setup.board_summary(conn))
        assert f"·  {n} configured" in " ".join(_labels(s))
