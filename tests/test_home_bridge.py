"""HomeBridge contract tests — pure relay, zero authority (no WebEngine).

The bridge is the only JS<->Python boundary for the ``#/home`` route, so the
contract proven here is: every slot is callable by an untrusted page script,
none of them do work themselves, and a bridge with nothing injected is inert
rather than broken.

The gated-slot proof for ``requestModeSwitch`` lives at the end — this file is
the ``GATED_SLOT_TESTS`` registration for ``src/ui/web/home_bridge.py``
(tests/test_web_guardrails.py).
"""

import json

import pytest
from PySide6.QtCore import QCoreApplication, QObject, Signal

from src.ui import app_modes
from src.ui.web.home_bridge import HomeBridge


@pytest.fixture(scope="module", autouse=True)
def _qt_app():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


class _FakeController(QObject):
    """Stands in for HomeWebController — records what the bridge forwards."""
    home_data = Signal(str)

    def __init__(self):
        super().__init__()
        self.calls = {"refresh": 0, "quick": [], "activity": [], "switch": []}

    def request_refresh(self):
        self.calls["refresh"] += 1

    def js_quick_action(self, key):
        self.calls["quick"].append(key)

    def js_activity_activated(self, kind):
        self.calls["activity"].append(kind)

    def js_request_mode_switch(self, mode):
        self.calls["switch"].append(mode)


def _wired():
    ctrl = _FakeController()
    bridge = HomeBridge(
        data_signal=ctrl.home_data,
        refresh_fn=ctrl.request_refresh,
        quick_action_fn=ctrl.js_quick_action,
        activity_fn=ctrl.js_activity_activated,
        mode_switch_fn=ctrl.js_request_mode_switch,
    )
    return bridge, ctrl


# ── inert when nothing is injected ────────────────────────────────────

def test_bare_bridge_is_inert_not_broken():
    bridge = HomeBridge()
    emitted = []
    bridge.homeData.connect(emitted.append)
    bridge.refresh()
    bridge.quickAction("search")
    bridge.activityActivated("Chat")
    bridge.requestModeSwitch("enablement")
    assert bridge.ping() == "pong"
    assert emitted == []


def test_raising_callables_are_swallowed():
    def boom(*_a):
        raise RuntimeError("controller exploded")
    bridge = HomeBridge(refresh_fn=boom, quick_action_fn=boom,
                        activity_fn=boom, mode_switch_fn=boom)
    bridge.refresh()
    bridge.quickAction("search")
    bridge.activityActivated("Chat")
    bridge.requestModeSwitch("enablement")   # must not take the boot page down


# ── forwarding ────────────────────────────────────────────────────────

def test_each_slot_forwards_once_unchanged():
    bridge, ctrl = _wired()
    bridge.refresh()
    bridge.quickAction("search")
    bridge.activityActivated("Report")
    bridge.requestModeSwitch("enablement")
    assert ctrl.calls == {"refresh": 1, "quick": ["search"],
                          "activity": ["Report"], "switch": ["enablement"]}


def test_none_arguments_normalize_to_empty_string():
    bridge, ctrl = _wired()
    bridge.quickAction(None)
    bridge.activityActivated(None)
    bridge.requestModeSwitch(None)
    assert ctrl.calls["quick"] == [""]
    assert ctrl.calls["activity"] == [""]
    assert ctrl.calls["switch"] == [""]


def test_ping_round_trip():
    bridge, _ = _wired()
    assert bridge.ping() == "pong"


def test_controller_data_signal_re_emits_as_home_data():
    bridge, ctrl = _wired()
    seen = []
    bridge.homeData.connect(seen.append)
    ctrl.home_data.emit(json.dumps({"mode": "product"}))
    assert len(seen) == 1 and json.loads(seen[0])["mode"] == "product"


# ── the bridge holds no logic of its own ──────────────────────────────

def test_bridge_imports_nothing_from_services():
    """Pure-relay invariant: authority lives in the controller, so the bridge
    must not be able to reach it except through injected callables."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "src" / "ui" / "web" / "home_bridge.py").read_text(encoding="utf-8")
    assert "src.services" not in src
    assert "import json" not in src        # no viewmodel building here
    assert "sqlite3" not in src


# ── gated slot: requestModeSwitch, end to end through the real controller ──

def _gated(db, confirm=True):
    """Bridge wired to the REAL controller — proves the gate is reachable
    through the same path a page script would take."""
    from src.services.home_web import HomeWebController
    calls = {"confirm": [], "deferred": [], "emitted": []}

    def confirm_fn(m):
        calls["confirm"].append(m)
        return confirm(m) if callable(confirm) else confirm

    ctrl = HomeWebController(
        db, current_mode=app_modes.MODE_PRODUCT, confirm_fn=confirm_fn,
        defer_fn=lambda fn: calls["deferred"].append(fn))
    ctrl.mode_selected.connect(lambda m: calls["emitted"].append(m))
    calls["quick"] = []
    calls["activity"] = []
    ctrl.quick_action.connect(lambda a: calls["quick"].append(a))
    ctrl.activity_activated.connect(lambda k: calls["activity"].append(k))
    bridge = HomeBridge(data_signal=ctrl.home_data,
                        refresh_fn=ctrl.request_refresh,
                        quick_action_fn=ctrl.js_quick_action,
                        activity_fn=ctrl.js_activity_activated,
                        mode_switch_fn=ctrl.js_request_mode_switch)
    return bridge, calls


def test_gated_switch_approved_defers_then_emits(empty_db):
    bridge, calls = _gated(empty_db)
    bridge.requestModeSwitch(app_modes.MODE_ENABLEMENT)
    assert calls["confirm"] == [app_modes.MODE_ENABLEMENT]
    assert calls["emitted"] == []            # never on the slot's stack frame
    calls["deferred"][0]()
    assert calls["emitted"] == [app_modes.MODE_ENABLEMENT]


def test_gated_switch_cancelled_never_emits(empty_db):
    bridge, calls = _gated(empty_db, confirm=False)
    bridge.requestModeSwitch(app_modes.MODE_ENABLEMENT)
    assert calls["deferred"] == [] and calls["emitted"] == []


@pytest.mark.parametrize("forged", ["admin", "root", "", "PRODUCT",
                                    "enablement ", "../product"])
def test_gated_switch_forged_mode_never_reaches_confirm(empty_db, forged):
    bridge, calls = _gated(empty_db)
    bridge.requestModeSwitch(forged)
    assert calls["confirm"] == [] and calls["deferred"] == []


def test_gated_switch_same_mode_is_silent(empty_db):
    bridge, calls = _gated(empty_db)
    bridge.requestModeSwitch(app_modes.MODE_PRODUCT)   # already product
    assert calls["confirm"] == []


def test_gated_switch_fails_closed_without_confirm(empty_db):
    from src.services.home_web import HomeWebController
    emitted = []
    ctrl = HomeWebController(empty_db, current_mode=app_modes.MODE_PRODUCT,
                             defer_fn=lambda fn: fn())
    ctrl.mode_selected.connect(emitted.append)
    bridge = HomeBridge(mode_switch_fn=ctrl.js_request_mode_switch)
    bridge.requestModeSwitch(app_modes.MODE_ENABLEMENT)
    assert emitted == []


def test_gated_switch_single_winner_through_the_bridge(empty_db):
    holder = {}

    def reentrant(_m):
        # A forged second invoke arriving while the modal spins its nested
        # event loop must find the claim already taken.
        holder["bridge"].requestModeSwitch(app_modes.MODE_ENABLEMENT)
        return True

    bridge, calls = _gated(empty_db, confirm=reentrant)
    holder["bridge"] = bridge
    bridge.requestModeSwitch(app_modes.MODE_ENABLEMENT)
    assert len(calls["confirm"]) == 1
    assert len(calls["deferred"]) == 1


def test_gated_quick_action_wrong_mode_key_blocked_through_bridge(empty_db):
    """The bridge forwards blindly; the controller is what refuses. Proven
    end to end so a future 'helpful' validation in the bridge can't mask it."""
    bridge, calls = _gated(empty_db)          # controller holds product mode
    bridge.quickAction("workbench")           # enablement-only key
    assert calls["quick"] == []
    bridge.quickAction("search")              # valid for product
    assert calls["quick"] == ["search"]


def test_gated_activity_forged_kind_blocked_through_bridge(empty_db):
    bridge, calls = _gated(empty_db)
    bridge.activityActivated("Everything")
    bridge.activityActivated("chat")          # case-sensitive on purpose
    assert calls["activity"] == []
    bridge.activityActivated("Chat")
    assert calls["activity"] == ["Chat"]
