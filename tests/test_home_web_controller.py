"""HomeWebController contract tests — viewmodel shape, guards, Qt parity.

The React route renders this viewmodel blind, so its shape is locked here.
Everything a page script can reach is treated as untrusted: forged modes,
unknown quick-action keys and bogus activity kinds must all be silent no-ops.

The parity test is the guard against the one deliberate duplication in the
port — the SQL and presentation constants exist in both home_page.py and
home_web.py (src/services must not import src/ui), so a drift would otherwise
go unnoticed.
"""

import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from src.ui import app_modes
from src.services.home_web import HomeWebController


@pytest.fixture(scope="module")
def qapp():
    # QApplication (not QCoreApplication): the parity test builds a real
    # HomePage widget to compare against.
    app = QApplication.instance() or QApplication([])
    yield app


def _controller(db, mode=app_modes.MODE_PRODUCT, **kw):
    ctrl = HomeWebController(db, current_mode=mode, **kw)
    seen = {"data": [], "modes": [], "actions": [], "activity": []}
    ctrl.home_data.connect(lambda j: seen["data"].append(json.loads(j)))
    ctrl.mode_selected.connect(lambda m: seen["modes"].append(m))
    ctrl.quick_action.connect(lambda a: seen["actions"].append(a))
    ctrl.activity_activated.connect(lambda k: seen["activity"].append(k))
    return ctrl, seen


# ── viewmodel shape ───────────────────────────────────────────────────

def test_viewmodel_shape_is_complete(qapp, empty_db):
    ctrl, seen = _controller(empty_db)
    ctrl.refresh()
    vm = seen["data"][-1]
    assert set(vm) == {"mode", "greeting", "subtitle", "banner",
                       "empty_activity", "tiles", "stats", "quick_actions",
                       "activity"}
    assert vm["mode"] == app_modes.MODE_PRODUCT
    assert vm["banner"] is None          # CCC banner is enablement-only
    assert len(vm["tiles"]) == 2
    assert len(vm["stats"]) == 3
    for tile in vm["tiles"]:
        assert set(tile) == {"key", "title", "desc", "icon", "active"}
    for stat in vm["stats"]:
        assert set(stat) == {"label", "caption", "value", "display",
                             "available"}


def test_banner_present_in_enablement_and_matches_branding(qapp, empty_db):
    from src.branding import (
        CONTENT_COMMAND_CENTER, CONTENT_COMMAND_CENTER_DESCRIPTION)
    ctrl, seen = _controller(empty_db, mode=app_modes.MODE_ENABLEMENT)
    ctrl.refresh()
    banner = seen["data"][-1]["banner"]
    assert banner == {"title": CONTENT_COMMAND_CENTER,
                      "desc": CONTENT_COMMAND_CENTER_DESCRIPTION}
    # Switching back to product drops it.
    ctrl.set_mode(app_modes.MODE_PRODUCT)
    assert seen["data"][-1]["banner"] is None


def test_exactly_one_tile_is_active(qapp, empty_db):
    ctrl, seen = _controller(empty_db, mode=app_modes.MODE_ENABLEMENT)
    ctrl.refresh()
    tiles = seen["data"][-1]["tiles"]
    active = [t["key"] for t in tiles if t["active"]]
    assert active == [app_modes.MODE_ENABLEMENT]


def test_quick_actions_match_mode(qapp, empty_db):
    ctrl, seen = _controller(empty_db)
    ctrl.refresh()
    assert [a["key"] for a in seen["data"][-1]["quick_actions"]] == [
        "search", "reports"]
    ctrl.set_mode(app_modes.MODE_ENABLEMENT)
    assert [a["key"] for a in seen["data"][-1]["quick_actions"]] == [
        "workbench", "calendar", "renn"]


def test_empty_db_yields_zero_stats_and_no_activity(qapp, empty_db):
    ctrl, seen = _controller(empty_db)
    ctrl.refresh()
    vm = seen["data"][-1]
    assert [s["display"] for s in vm["stats"]] == ["0", "0", "0"]
    assert vm["activity"] == []


def test_missing_table_is_zero_and_flagged_unavailable(qapp, empty_db):
    # Enablement stats hit guru_content_drafts / enablement_tasks / pptx_decks;
    # a missing table must degrade to 0, never raise.
    empty_db.conn.execute("DROP TABLE IF EXISTS pptx_decks")
    empty_db.conn.commit()
    ctrl, seen = _controller(empty_db, mode=app_modes.MODE_ENABLEMENT)
    ctrl.refresh()
    decks = [s for s in seen["data"][-1]["stats"] if s["label"] == "Decks"][0]
    assert decks["display"] == "0" and decks["available"] is False


def test_no_conn_yields_empty_sections(qapp):
    ctrl, seen = _controller(SimpleNamespace(conn=None))
    ctrl.refresh()
    vm = seen["data"][-1]
    assert vm["stats"] == [] and vm["activity"] == []


def test_thousands_separator_in_display(qapp, seeded_db):
    ctrl, seen = _controller(seeded_db)
    ctrl.refresh()
    tickets = [s for s in seen["data"][-1]["stats"]
               if s["label"] == "Tickets"][0]
    assert tickets["display"] == f"{tickets['value']:,}"


# ── greeting (pinned clock) ───────────────────────────────────────────

@pytest.mark.parametrize("hour,word", [
    (0, "morning"), (9, "morning"), (11, "morning"),
    (12, "afternoon"), (16, "afternoon"),
    (17, "evening"), (23, "evening"),
])
def test_greeting_boundaries(qapp, empty_db, hour, word):
    ctrl, seen = _controller(
        empty_db, now_fn=lambda: datetime(2026, 7, 20, hour, 0, 0))
    ctrl.refresh()
    assert seen["data"][-1]["greeting"] == f"Good {word}"


# ── activity formatting ───────────────────────────────────────────────

def _seed_activity(db):
    db.conn.execute(
        "INSERT INTO chat_sessions "
        "(session_id, created_at, updated_at, source_page, title) "
        "VALUES (?, ?, ?, ?, ?)",
        ("s-home-1", "2026-07-19T08:30:00", "2026-07-19T08:30:00", "home",
         "x" * 200))
    db.conn.commit()


def test_activity_title_clamped_and_timestamp_formatted(qapp, empty_db):
    _seed_activity(empty_db)
    ctrl, seen = _controller(empty_db)
    ctrl.refresh()
    row = seen["data"][-1]["activity"][0]
    assert len(row["title"]) == 110
    assert row["ts_display"] == "2026-07-19  08:30"
    assert row["kind"] == "Chat"
    assert row["tint_bg"] == "#E8F0FE" and row["tint_fg"] == "#1D4ED8"


def test_activity_capped_at_eight(qapp, empty_db):
    for i in range(12):
        ts = f"2026-07-{i + 1:02d}T00:00:00"
        empty_db.conn.execute(
            "INSERT INTO chat_sessions "
            "(session_id, created_at, updated_at, source_page, title) "
            "VALUES (?, ?, ?, ?, ?)", (f"s{i}", ts, ts, "home", f"s{i}"))
    empty_db.conn.commit()
    ctrl, seen = _controller(empty_db)
    ctrl.refresh()
    assert len(seen["data"][-1]["activity"]) == 8


# ── request_refresh replays the cache ─────────────────────────────────

def test_request_refresh_replays_cached_payload(qapp, empty_db):
    ctrl, seen = _controller(empty_db)
    ctrl.refresh()
    first = len(seen["data"])
    ctrl.request_refresh()
    assert len(seen["data"]) == first + 1
    assert seen["data"][-1] == seen["data"][-2]


def test_request_refresh_builds_when_cold(qapp, empty_db):
    ctrl, seen = _controller(empty_db)
    ctrl.request_refresh()          # never pushed before
    assert len(seen["data"]) == 1


# ── untrusted input: quick actions + activity ─────────────────────────

def test_quick_action_valid_key_emits(qapp, empty_db):
    ctrl, seen = _controller(empty_db)
    ctrl.js_quick_action("search")
    assert seen["actions"] == ["search"]


def test_quick_action_wrong_mode_key_is_silent(qapp, empty_db):
    # 'workbench' is enablement-only; the controller holds product.
    ctrl, seen = _controller(empty_db, mode=app_modes.MODE_PRODUCT)
    ctrl.js_quick_action("workbench")
    assert seen["actions"] == []


@pytest.mark.parametrize("bad", ["", None, "admin", "../reports", 123,
                                 "search ", "SEARCH"])
def test_quick_action_forged_keys_are_silent(qapp, empty_db, bad):
    ctrl, seen = _controller(empty_db)
    ctrl.js_quick_action(bad)
    assert seen["actions"] == []


def test_activity_valid_kinds_emit(qapp, empty_db):
    ctrl, seen = _controller(empty_db)
    for kind in ("Chat", "Report", "Task"):
        ctrl.js_activity_activated(kind)
    assert seen["activity"] == ["Chat", "Report", "Task"]


@pytest.mark.parametrize("bad", ["", None, "chat", "Chat ", "Everything", 0])
def test_activity_forged_kinds_are_silent(qapp, empty_db, bad):
    ctrl, seen = _controller(empty_db)
    ctrl.js_activity_activated(bad)
    assert seen["activity"] == []


# ── the gated action: mode switch ─────────────────────────────────────

def _gated(db, confirm=True, mode=app_modes.MODE_PRODUCT):
    calls = {"confirm": [], "deferred": []}

    def confirm_fn(m):
        calls["confirm"].append(m)
        return confirm(m) if callable(confirm) else confirm

    ctrl, seen = _controller(
        db, mode=mode, confirm_fn=confirm_fn,
        defer_fn=lambda fn: calls["deferred"].append(fn))
    return ctrl, seen, calls


def test_mode_switch_approved_emits_once(qapp, empty_db):
    ctrl, seen, calls = _gated(empty_db)
    ctrl.js_request_mode_switch(app_modes.MODE_ENABLEMENT)
    assert calls["confirm"] == [app_modes.MODE_ENABLEMENT]
    # Deferred, NOT emitted synchronously — switch_mode must never run on the
    # slot's stack frame while it rebuilds the widget hosting the caller.
    assert seen["modes"] == []
    assert len(calls["deferred"]) == 1
    calls["deferred"][0]()
    assert seen["modes"] == [app_modes.MODE_ENABLEMENT]


def test_mode_switch_cancelled_never_emits(qapp, empty_db):
    ctrl, seen, calls = _gated(empty_db, confirm=False)
    ctrl.js_request_mode_switch(app_modes.MODE_ENABLEMENT)
    assert calls["confirm"] == [app_modes.MODE_ENABLEMENT]
    assert calls["deferred"] == [] and seen["modes"] == []


@pytest.mark.parametrize("bad", ["admin", "", None, "PRODUCT", 123,
                                 "product ", "enablement\n"])
def test_mode_switch_forged_values_are_silent(qapp, empty_db, bad):
    ctrl, seen, calls = _gated(empty_db)
    ctrl.js_request_mode_switch(bad)
    assert calls["confirm"] == [] and calls["deferred"] == []
    assert seen["modes"] == []


def test_mode_switch_to_current_mode_is_silent(qapp, empty_db):
    ctrl, seen, calls = _gated(empty_db, mode=app_modes.MODE_PRODUCT)
    ctrl.js_request_mode_switch(app_modes.MODE_PRODUCT)
    assert calls["confirm"] == [] and calls["deferred"] == []


def test_mode_switch_without_confirm_fn_fails_closed(qapp, empty_db):
    ctrl, seen = _controller(empty_db, defer_fn=lambda fn: fn())
    ctrl.js_request_mode_switch(app_modes.MODE_ENABLEMENT)
    assert seen["modes"] == []


def test_mode_switch_confirm_raising_means_no(qapp, empty_db):
    def boom(_m):
        raise RuntimeError("dialog broke")
    ctrl, seen, calls = _gated(empty_db, confirm=boom)
    ctrl.js_request_mode_switch(app_modes.MODE_ENABLEMENT)
    assert calls["deferred"] == [] and seen["modes"] == []


def test_mode_switch_single_winner_under_reentrancy(qapp, empty_db):
    # The native dialog spins a nested event loop, so a second (double-click /
    # forged) invoke can arrive WHILE the confirm is open. The claim is taken
    # before the dialog → the inner call must be a total no-op.
    holder = {}

    def reentrant(_m):
        holder["ctrl"].js_request_mode_switch(app_modes.MODE_ENABLEMENT)
        return True

    ctrl, seen, calls = _gated(empty_db, confirm=reentrant)
    holder["ctrl"] = ctrl
    ctrl.js_request_mode_switch(app_modes.MODE_ENABLEMENT)
    assert len(calls["confirm"]) == 1        # one dialog, ever
    assert len(calls["deferred"]) == 1       # one switch, the outer one


def test_mode_switch_claim_released_after_cancel(qapp, empty_db):
    # A cancelled switch must not wedge the flag — the next attempt still works.
    seq = iter([False, True])
    ctrl, seen, calls = _gated(empty_db, confirm=lambda _m: next(seq))
    ctrl.js_request_mode_switch(app_modes.MODE_ENABLEMENT)
    ctrl.js_request_mode_switch(app_modes.MODE_ENABLEMENT)
    assert len(calls["confirm"]) == 2 and len(calls["deferred"]) == 1


def test_mode_switch_claim_released_after_raise(qapp, empty_db):
    calls_n = {"n": 0}

    def confirm(_m):
        calls_n["n"] += 1
        if calls_n["n"] == 1:
            raise RuntimeError("boom")
        return True

    ctrl, seen, calls = _gated(empty_db, confirm=confirm)
    ctrl.js_request_mode_switch(app_modes.MODE_ENABLEMENT)
    ctrl.js_request_mode_switch(app_modes.MODE_ENABLEMENT)
    assert len(calls["deferred"]) == 1


# ── parity with the native HomePage (anti-drift guard) ────────────────

def test_stats_match_native_home_page(qapp, seeded_db):
    """The SQL is duplicated between home_page.py and home_web.py by design
    (src/services must not import src/ui). This is the guard."""
    from src.ui.pages.home_page import HomePage
    for mode in app_modes.MODES:
        native = HomePage(seeded_db, current_mode=mode)
        ctrl, seen = _controller(seeded_db, mode=mode)
        ctrl.refresh()
        web = [(s["label"], s["value"]) for s in seen["data"][-1]["stats"]]
        assert web == native._stats(), f"stat drift in {mode} mode"


def test_activity_matches_native_home_page(qapp, empty_db):
    _seed_activity(empty_db)
    from src.ui.pages.home_page import HomePage
    native = HomePage(empty_db, current_mode=app_modes.MODE_PRODUCT)
    ctrl, seen = _controller(empty_db)
    ctrl.refresh()
    web = [(r["kind"], r["title"]) for r in seen["data"][-1]["activity"]]
    expected = [(k, t[:110]) for k, t, _ts in native._recent_activity()]
    assert web == expected


def test_tiles_match_native_copy(qapp, empty_db):
    from src.ui.pages import home_page as native_mod
    ctrl, seen = _controller(empty_db)
    ctrl.refresh()
    web = [(t["key"], t["title"], t["desc"]) for t in seen["data"][-1]["tiles"]]
    assert web == [tuple(t) for t in native_mod._MODE_TILES]


def test_banner_matches_native_home_page(qapp, empty_db):
    """Both surfaces render the CCC banner from src.branding; this pins that
    the web payload carries exactly the strings the native widget shows."""
    from PySide6.QtWidgets import QLabel
    from src.ui.pages.home_page import HomePage
    native = HomePage(empty_db, current_mode=app_modes.MODE_ENABLEMENT)
    assert native._ccc_banner.isVisibleTo(native)
    native_texts = [lbl.text()
                    for lbl in native._ccc_banner.findChildren(QLabel)]

    ctrl, seen = _controller(empty_db, mode=app_modes.MODE_ENABLEMENT)
    ctrl.refresh()
    banner = seen["data"][-1]["banner"]
    assert banner["title"].upper() in native_texts
    assert banner["desc"] in native_texts


def test_quick_actions_match_native_copy(qapp, empty_db):
    from src.ui.pages import home_page as native_mod
    for mode in app_modes.MODES:
        ctrl, seen = _controller(empty_db, mode=mode)
        ctrl.refresh()
        web = [(a["key"], a["label"], a["icon"])
               for a in seen["data"][-1]["quick_actions"]]
        assert web == [tuple(a) for a in native_mod._QUICK_ACTIONS[mode]]
