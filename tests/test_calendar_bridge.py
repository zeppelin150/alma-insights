"""CalendarWebController + CalendarBridge contract tests (no WebEngine).

Everything a page script can invoke is treated as untrusted here: forged ids,
bad scopes, and raising brief lookups must all be safe no-ops. The viewmodel
shape is locked because the React route renders it blind.
"""

import json

import pytest
from PySide6.QtCore import QCoreApplication

from src.services.enablement_web import CalendarWebController
from src.ui.web.calendar_bridge import CalendarBridge


@pytest.fixture(scope="module", autouse=True)
def _qt_app():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


SAMPLE_TASKS = [
    {"task_id": "t1", "title": "Triage: bulk SSO requests from providers",
     "source": "asana", "status": "open", "priority": "high",
     "assignee": "Chris", "due_date": "2026-07-18", "subs": "1 / 3",
     "description": "d" * 500},
    {"task_id": "t2", "title": "Draft: SSO card", "source": "drive",
     "status": "in_progress", "due_date": "2026-07-18T09:00:00"},
    {"task_id": "t3", "title": "No due date", "source": "asana"},
    {"kind": "guru_card_due", "card_id": "c9", "source": "guru",
     "title": "Card due: Payments v2", "due_date": "2026-07-20"},
    {"title": "no id at all", "due_date": "2026-07-21"},
    "not-a-dict",
]


def _controller(**kw):
    ctrl = CalendarWebController(today_fn=lambda: "2026-07-14", **kw)
    seen = {"data": [], "briefs": [], "scopes": [], "activated": []}
    ctrl.calendar_data.connect(lambda j: seen["data"].append(json.loads(j)))
    ctrl.brief_ready.connect(lambda j: seen["briefs"].append(json.loads(j)))
    ctrl.scope_changed.connect(lambda s: seen["scopes"].append(s))
    ctrl.event_activated.connect(lambda t: seen["activated"].append(t))
    return ctrl, seen


# ── viewmodel shape ───────────────────────────────────────────────────

def test_viewmodel_shape_and_kinds():
    ctrl, seen = _controller()
    ctrl.set_tasks(SAMPLE_TASKS)
    assert len(seen["data"]) == 1
    vm = seen["data"][0]
    assert vm["scope"] == "mine" and vm["today"] == "2026-07-14"
    events = {e["id"]: e for e in vm["events"]}
    # dated rows only: t3 (no due) and the id-less row are not drawn
    assert set(events) == {"t1", "t2", "guru:c9"}
    assert events["t1"]["kind"] == "asana"
    assert events["t2"]["kind"] == "drive"
    assert events["guru:c9"]["kind"] == "guru"
    assert events["guru:c9"]["is_card_due"] is True
    # datetime due dates reduce to ISO date
    assert events["t2"]["date"] == "2026-07-18"


def test_full_title_preserved_no_truncation():
    ctrl, seen = _controller()
    ctrl.set_tasks(SAMPLE_TASKS)
    titles = [e["title"] for e in seen["data"][0]["events"]]
    assert "Triage: bulk SSO requests from providers" in titles


def test_description_capped():
    ctrl, seen = _controller()
    ctrl.set_tasks(SAMPLE_TASKS)
    e = [x for x in seen["data"][0]["events"] if x["id"] == "t1"][0]
    assert len(e["description"]) == 240


def test_unknown_source_becomes_normal_kind():
    ctrl, seen = _controller()
    ctrl.set_tasks([{"task_id": "x", "source": "weird", "title": "t",
                     "due_date": "2026-07-15"}])
    assert seen["data"][0]["events"][0]["kind"] == "normal"


# ── openTask: forged/stale ids are no-ops ─────────────────────────────

def test_open_task_resolves_known_id():
    ctrl, seen = _controller()
    ctrl.set_tasks(SAMPLE_TASKS)
    ctrl.open_task("t1")
    assert len(seen["activated"]) == 1
    assert seen["activated"][0]["task_id"] == "t1"
    assert seen["activated"][0]["description"] == "d" * 500  # FULL row, uncapped


def test_open_task_resolves_undated_task():
    # t3 has no due date (never drawn) but is still a real task — openable.
    ctrl, seen = _controller()
    ctrl.set_tasks(SAMPLE_TASKS)
    ctrl.open_task("t3")
    assert seen["activated"][0]["title"] == "No due date"


def test_open_task_forged_id_is_noop():
    ctrl, seen = _controller()
    ctrl.set_tasks(SAMPLE_TASKS)
    ctrl.open_task("nope")
    ctrl.open_task("")
    ctrl.open_task(None)
    assert seen["activated"] == []


def test_open_task_stale_id_after_refeed_is_noop():
    ctrl, seen = _controller()
    ctrl.set_tasks(SAMPLE_TASKS)
    ctrl.set_tasks([{"task_id": "only", "title": "x", "due_date": "2026-07-15"}])
    ctrl.open_task("t1")
    assert seen["activated"] == []


def test_guru_card_due_synthetic_id_resolves():
    ctrl, seen = _controller()
    ctrl.set_tasks(SAMPLE_TASKS)
    ctrl.open_task("guru:c9")
    assert seen["activated"][0]["kind"] == "guru_card_due"
    assert seen["activated"][0]["card_id"] == "c9"


# ── scope: validation + no echo loop ──────────────────────────────────

def test_request_scope_emits_and_repushes():
    ctrl, seen = _controller()
    ctrl.set_tasks(SAMPLE_TASKS)
    ctrl.request_scope("all")
    assert seen["scopes"] == ["all"]
    assert seen["data"][-1]["scope"] == "all"
    # events preserved across the scope-only repush (host reload follows)
    assert len(seen["data"][-1]["events"]) == 3


def test_request_scope_invalid_or_same_is_noop():
    ctrl, seen = _controller()
    ctrl.request_scope("mine")     # same as default
    ctrl.request_scope("everything")
    ctrl.request_scope("")
    assert seen["scopes"] == [] and seen["data"] == []


def test_set_scope_never_echoes_scope_changed():
    ctrl, seen = _controller()
    ctrl.set_tasks(SAMPLE_TASKS)
    ctrl.set_scope("all")
    assert seen["scopes"] == []            # the no-loop contract
    assert seen["data"][-1]["scope"] == "all"


# ── briefs: lazy, unknown-id-gated, fail-soft ─────────────────────────

def test_request_brief_returns_stored_brief():
    brief = {"ask": "Do X", "deliverable": "Card", "links": [],
             "stakeholders": ["Ops"], "effective_date": "2026-08-01"}
    ctrl, seen = _controller(brief_lookup=lambda tid: brief if tid == "t1" else None)
    ctrl.set_tasks(SAMPLE_TASKS)
    ctrl.request_brief("t1")
    assert seen["briefs"] == [{"task_id": "t1", "brief": brief}]


def test_request_brief_unknown_id_is_noop():
    ctrl, seen = _controller(brief_lookup=lambda tid: {"ask": "x"})
    ctrl.set_tasks(SAMPLE_TASKS)
    ctrl.request_brief("forged")
    assert seen["briefs"] == []


def test_request_brief_lookup_error_yields_null_brief():
    def boom(_tid):
        raise RuntimeError("db unavailable")
    ctrl, seen = _controller(brief_lookup=boom)
    ctrl.set_tasks(SAMPLE_TASKS)
    ctrl.request_brief("t1")
    assert seen["briefs"] == [{"task_id": "t1", "brief": None}]


def test_request_brief_without_lookup_yields_null():
    ctrl, seen = _controller()
    ctrl.set_tasks(SAMPLE_TASKS)
    ctrl.request_brief("t1")
    assert seen["briefs"] == [{"task_id": "t1", "brief": None}]


# ── M2 gated reschedule: the write invariants ─────────────────────────

def _gated(confirm=None, write=None):
    calls = {"confirm": [], "write": [], "resolved": [], "dispatched": []}

    def confirm_fn(task, date):
        calls["confirm"].append((task, date))
        return confirm(task, date) if callable(confirm) else bool(confirm)

    def write_fn(tid, date):
        calls["write"].append((tid, date))
        if callable(write):
            write(tid, date)

    ctrl = CalendarWebController(today_fn=lambda: "2026-07-14",
                                 confirm_fn=confirm_fn, write_fn=write_fn)
    ctrl.reschedule_resolved.connect(lambda j: calls["resolved"].append(json.loads(j)))
    ctrl.reschedule_dispatched.connect(lambda t, d: calls["dispatched"].append((t, d)))
    ctrl.set_tasks(SAMPLE_TASKS)
    return ctrl, calls


def test_reschedule_approved_writes_once_and_resolves():
    ctrl, calls = _gated(confirm=True)
    ctrl.request_reschedule("t1", "2026-07-21")
    assert calls["confirm"] == [(calls["confirm"][0][0], "2026-07-21")]
    assert calls["write"] == [("t1", "2026-07-21")]
    assert calls["dispatched"] == [("t1", "2026-07-21")]
    r = calls["resolved"][0]
    assert r["task_id"] == "t1" and r["date"] == "2026-07-21"
    assert r["approved"] is True and r["dispatched"] is True and r["cancelled"] is False


def test_reschedule_cancelled_never_writes():
    ctrl, calls = _gated(confirm=False)
    ctrl.request_reschedule("t1", "2026-07-21")
    assert calls["confirm"] and calls["write"] == [] and calls["dispatched"] == []
    r = calls["resolved"][0]
    assert r["cancelled"] is True and r["dispatched"] is False


def test_reschedule_forged_or_stale_id_is_silent():
    ctrl, calls = _gated(confirm=True)
    ctrl.request_reschedule("nope", "2026-07-21")
    ctrl.request_reschedule("", "2026-07-21")
    ctrl.request_reschedule(None, "2026-07-21")
    assert calls["confirm"] == [] and calls["write"] == [] and calls["resolved"] == []


def test_reschedule_card_due_chip_is_silent():
    ctrl, calls = _gated(confirm=True)
    ctrl.request_reschedule("guru:c9", "2026-07-21")
    assert calls["confirm"] == [] and calls["resolved"] == []


def test_reschedule_malformed_dates_are_silent():
    ctrl, calls = _gated(confirm=True)
    for bad in ("2026-7-1", "not-a-date", "javascript:x", "", None,
                "2026-07-211", "21-07-2026"):
        ctrl.request_reschedule("t1", bad)
    assert calls["confirm"] == [] and calls["write"] == []


def test_reschedule_same_date_is_silent():
    ctrl, calls = _gated(confirm=True)
    ctrl.request_reschedule("t1", "2026-07-18")   # t1 already due 2026-07-18
    assert calls["confirm"] == [] and calls["resolved"] == []


def test_reschedule_without_confirm_fn_fails_closed():
    ctrl = CalendarWebController(today_fn=lambda: "2026-07-14",
                                 write_fn=lambda *_: None)
    resolved = []
    ctrl.reschedule_resolved.connect(lambda j: resolved.append(json.loads(j)))
    ctrl.set_tasks(SAMPLE_TASKS)
    ctrl.request_reschedule("t1", "2026-07-21")
    assert resolved and resolved[0]["cancelled"] is True
    assert resolved[0]["dispatched"] is False


def test_reschedule_confirm_raising_means_no():
    def boom(_t, _d):
        raise RuntimeError("dialog broke")
    ctrl, calls = _gated(confirm=boom)
    ctrl.request_reschedule("t1", "2026-07-21")
    assert calls["write"] == []
    assert calls["resolved"][0]["cancelled"] is True


def test_reschedule_write_raising_resolves_undispatched():
    def bad_write(_t, _d):
        raise RuntimeError("db locked")
    ctrl, calls = _gated(confirm=True, write=bad_write)
    ctrl.request_reschedule("t1", "2026-07-21")
    r = calls["resolved"][0]
    assert r["approved"] is True and r["dispatched"] is False
    assert calls["dispatched"] == []


def test_reschedule_single_winner_under_reentrancy():
    # The native dialog spins a nested event loop, so a second (double-click /
    # forged) invoke can arrive WHILE the confirm is open. The inflight claim
    # is taken before the dialog → the inner call must be a total no-op.
    ctrl_holder = {}

    def reentrant_confirm(_task, _date):
        ctrl_holder["ctrl"].request_reschedule("t2", "2026-07-25")  # mid-dialog
        return True

    ctrl, calls = _gated(confirm=reentrant_confirm)
    ctrl_holder["ctrl"] = ctrl
    ctrl.request_reschedule("t1", "2026-07-21")
    assert len(calls["confirm"]) == 1          # one dialog, ever
    assert calls["write"] == [("t1", "2026-07-21")]   # one write, the outer one
    assert len(calls["resolved"]) == 1


def test_bridge_reschedule_relay():
    ctrl, calls = _gated(confirm=True)
    bridge = CalendarBridge(reschedule_fn=ctrl.request_reschedule,
                            resolved_signal=ctrl.reschedule_resolved)
    got = []
    bridge.rescheduleResolved.connect(lambda j: got.append(json.loads(j)))
    bridge.requestReschedule("t1", "2026-07-21")
    assert calls["write"] == [("t1", "2026-07-21")]
    assert got and got[0]["dispatched"] is True
    bridge2 = CalendarBridge()                 # nothing injected → inert
    bridge2.requestReschedule("t1", "2026-07-21")


# ── refresh replay ────────────────────────────────────────────────────

def test_refresh_replays_last_payload():
    ctrl, seen = _controller()
    ctrl.set_tasks(SAMPLE_TASKS)
    ctrl.request_refresh()
    assert len(seen["data"]) == 2
    assert seen["data"][0] == seen["data"][1]


def test_refresh_before_any_feed_is_silent():
    ctrl, seen = _controller()
    ctrl.request_refresh()
    assert seen["data"] == []


# ── bridge: pure relay ────────────────────────────────────────────────

def test_bridge_relays_signals_and_slots():
    ctrl, _seen = _controller()
    bridge = CalendarBridge(
        data_signal=ctrl.calendar_data, brief_signal=ctrl.brief_ready,
        refresh_fn=ctrl.request_refresh, scope_fn=ctrl.request_scope,
        open_fn=ctrl.open_task, brief_fn=ctrl.request_brief)
    got = {"data": [], "briefs": []}
    bridge.calendarData.connect(lambda j: got["data"].append(json.loads(j)))
    bridge.briefReady.connect(lambda j: got["briefs"].append(json.loads(j)))
    ctrl.set_tasks(SAMPLE_TASKS)
    assert len(got["data"]) == 1
    bridge.setScope("all")
    assert got["data"][-1]["scope"] == "all"
    bridge.requestBrief("t1")
    assert got["briefs"][0]["task_id"] == "t1"
    bridge.refresh()
    assert len(got["data"]) >= 3
    assert bridge.ping() == "pong"


def test_bridge_with_nothing_injected_is_inert():
    bridge = CalendarBridge()
    bridge.refresh()
    bridge.setScope("all")
    bridge.openTask("t1")
    bridge.requestBrief("t1")
    assert bridge.ping() == "pong"


def test_bridge_swallows_raising_callables():
    def boom(*_a):
        raise RuntimeError("nope")
    bridge = CalendarBridge(refresh_fn=boom, scope_fn=boom, open_fn=boom,
                            brief_fn=boom)
    bridge.refresh()
    bridge.setScope("all")
    bridge.openTask("x")
    bridge.requestBrief("x")   # none of these may raise


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
