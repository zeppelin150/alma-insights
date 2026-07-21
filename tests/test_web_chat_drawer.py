"""M5.5 web Renn drawer plumbing (no WebEngine): the chatNotice relay, the
page.py _chat_say / _web_chat_send helpers, and the shared-bridge factory.

The invariant under test: the drawer is the SAME assistant as the Qt
ChatPanel — user turns mirror into the panel, host notices mirror into the
drawer, and engine turns flow ONLY through the bridge (never doubled)."""

import json

import pytest
from PySide6.QtCore import QCoreApplication, QObject, Signal

from src.ui.web.chat_bridge import ChatBridge


@pytest.fixture(scope="module", autouse=True)
def _qt_app():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


class _FakeEngine(QObject):
    response_ready = Signal(str)
    error_occurred = Signal(str)
    busy_changed = Signal(bool)
    status_update = Signal(str)

    def send(self, text):
        self.response_ready.emit("PONG:" + text)


# ── chatNotice relay ─────────────────────────────────────────────────

def test_push_notice_emits_role_and_text():
    bridge = ChatBridge(_FakeEngine())
    got = []
    bridge.chatNotice.connect(lambda j: got.append(json.loads(j)))
    bridge.push_notice("a", "Scan complete — 12 docs.")
    bridge.push_notice("u", "quick action prompt")
    assert got == [{"role": "a", "text": "Scan complete — 12 docs."},
                   {"role": "u", "text": "quick action prompt"}]


def test_push_notice_normalizes_roles_and_tolerates_junk():
    bridge = ChatBridge(_FakeEngine())
    got = []
    bridge.chatNotice.connect(lambda j: got.append(json.loads(j)))
    bridge.push_notice("system", "weird role")
    bridge.push_notice(None, None)
    assert got[0]["role"] == "a"
    assert got[1] == {"role": "a", "text": ""}


# ── page.py helpers (via __new__, no heavy __init__) ─────────────────

class _FakePanel:
    def __init__(self):
        self.rows = []

    def add_message(self, role, text):
        self.rows.append((role, text))


class _Inst:
    """Plain host stand-in: the page helpers are called unbound, so ordinary
    attribute semantics suffice (an uninitialized QWidget from __new__ has
    broken shiboken attribute access — don't use it here)."""


def _page_inst():
    import src.ui.pages.enablement.page as pg
    inst = _Inst()
    inst.chat = _FakePanel()
    return pg, inst


def test_chat_say_reaches_panel_and_drawer():
    pg, inst = _page_inst()
    bridge = ChatBridge(_FakeEngine())
    inst._web_chat_bridge = bridge
    got = []
    bridge.chatNotice.connect(lambda j: got.append(json.loads(j)))
    pg.EnablementPage._chat_say(inst, "a", "Published the draft.")
    assert inst.chat.rows == [("a", "Published the draft.")]
    assert got == [{"role": "a", "text": "Published the draft."}]


def test_chat_say_without_web_bridge_is_panel_only():
    pg, inst = _page_inst()
    inst._web_chat_bridge = None
    pg.EnablementPage._chat_say(inst, "a", "hello")
    assert inst.chat.rows == [("a", "hello")]


def test_chat_say_survives_a_broken_panel():
    pg, inst = _page_inst()

    class Boom:
        def add_message(self, *_a):
            raise RuntimeError("panel gone")

    inst.chat = Boom()
    bridge = ChatBridge(_FakeEngine())
    inst._web_chat_bridge = bridge
    got = []
    bridge.chatNotice.connect(lambda j: got.append(json.loads(j)))
    pg.EnablementPage._chat_say(inst, "a", "still relayed")
    assert got and got[0]["text"] == "still relayed"


def test_web_chat_send_mirrors_user_turn_to_panel_and_drawers_then_dispatches():
    pg, inst = _page_inst()
    sent = []
    inst._dispatch_chat = lambda t: sent.append(t)
    bridge = ChatBridge(_FakeEngine())
    inst._web_chat_bridge = bridge
    inst._mirror_user_turn = lambda t: pg.EnablementPage._mirror_user_turn(inst, t)
    notices = []
    bridge.chatNotice.connect(lambda j: notices.append(json.loads(j)))
    pg.EnablementPage._web_chat_send(inst, "revise the intro")
    # canonical Qt panel gets the user turn
    assert inst.chat.rows == [("u", "revise the intro")]
    # and it is mirrored to the drawers as a "u" notice (the originating drawer
    # dedupes its own optimistic echo; a second drawer renders it) — this is
    # what keeps the transcript coherent across surfaces (review fix #7)
    assert notices == [{"role": "u", "text": "revise the intro"}]
    assert sent == ["revise the intro"]


def test_on_chat_mirrors_qt_panel_turn_to_drawers():
    pg, inst = _page_inst()
    sent = []
    inst._dispatch_chat = lambda t: sent.append(t)
    bridge = ChatBridge(_FakeEngine())
    inst._web_chat_bridge = bridge
    inst._mirror_user_turn = lambda t: pg.EnablementPage._mirror_user_turn(inst, t)
    notices = []
    bridge.chatNotice.connect(lambda j: notices.append(json.loads(j)))
    pg.EnablementPage._on_chat(inst, "typed in the Qt panel")
    # the Qt panel already rendered its own bubble (not re-added here); the
    # turn is mirrored to any open web drawer and dispatched
    assert notices == [{"role": "u", "text": "typed in the Qt panel"}]
    assert sent == ["typed in the Qt panel"]


def test_mirror_user_turn_without_bridge_is_safe():
    pg, inst = _page_inst()
    inst._web_chat_bridge = None
    pg.EnablementPage._mirror_user_turn(inst, "no drawer")   # must not raise


def test_tool_poll_without_session_is_empty():
    pg, inst = _page_inst()
    inst._chat_session_id = None
    inst._engine_db_path = lambda: None
    assert pg.EnablementPage._web_chat_tool_poll(inst) == []


def test_get_web_chat_bridge_none_without_engine_and_cached_with():
    import src.ui.pages.enablement.page as pg
    # the factory parents the bridge to the page, so the stand-in must be a
    # real QObject here
    inst = QObject()
    inst.chat = _FakePanel()
    inst._engine = None
    assert pg.EnablementPage._get_web_chat_bridge(inst) is None
    inst._web_chat_bridge = None
    inst._engine = _FakeEngine()
    inst._web_chat_send = lambda t: None
    inst._web_chat_tool_poll = lambda since_id=0: []
    b1 = pg.EnablementPage._get_web_chat_bridge(inst)
    b2 = pg.EnablementPage._get_web_chat_bridge(inst)
    assert isinstance(b1, ChatBridge) and b1 is b2


def test_engine_setup_precedes_build_so_almabridge_registers():
    """M6 review fix #1: _setup_engine() MUST run before _build(). _build()
    constructs the web tabs, which register the shared chat bridge on their
    channels — if the engine doesn't exist yet, _get_web_chat_bridge() returns
    None and the Renn drawer is dead for the page's lifetime (WebHost registers
    channel objects exactly once). Guard the ordering directly."""
    import inspect
    import src.ui.pages.enablement.page as pg
    src_init = inspect.getsource(pg.EnablementPage.__init__)
    setup_at = src_init.find("self._setup_engine()")
    build_at = src_init.find("self._build()")
    assert setup_at != -1 and build_at != -1
    assert setup_at < build_at, \
        "_setup_engine() must precede _build() or almaBridge never registers"


def test_get_web_chat_bridge_caches_the_bridge_object():
    """The factory must cache — else a second call rebuilds and the two tabs
    would register DIFFERENT bridge objects (or the None short-circuit would
    leave _web_chat_bridge unset)."""
    import src.ui.pages.enablement.page as pg
    from PySide6.QtCore import QObject
    inst = QObject()
    inst.chat = _FakePanel()
    inst._web_chat_bridge = None
    inst._engine = _FakeEngine()
    inst._web_chat_send = lambda t: None
    inst._web_chat_tool_poll = lambda since_id=0: []
    b = pg.EnablementPage._get_web_chat_bridge(inst)
    assert b is not None and inst._web_chat_bridge is b
    assert pg.EnablementPage._get_web_chat_bridge(inst) is b   # cached, same object


def test_engine_turns_flow_only_through_the_bridge():
    """The doubling guard: engine responses reach the drawer via responseReady;
    _on_engine_response's panel write must NOT also relay a notice."""
    import inspect
    import src.ui.pages.enablement.page as pg
    src_resp = inspect.getsource(pg.EnablementPage._on_engine_response)
    src_err = inspect.getsource(pg.EnablementPage._on_engine_error)
    assert "_chat_say" not in src_resp and "add_message" in src_resp
    assert "_chat_say" not in src_err and "add_message" in src_err
    # and the send mirror likewise writes the panel directly
    src_send = inspect.getsource(pg.EnablementPage._web_chat_send)
    assert "_chat_say" not in src_send


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
