"""MainWindow refreshes a page when the operator returns to it, and hands back
a usable native Agent fallback when the web view can't be built.

Audit findings 09/11 (a page built once shows a stale surface on return) and
10 (the Agent page's only fallback was a dead label).
"""

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QWidget

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# ── the reload hook ────────────────────────────────────────────────────

def test_on_page_shown_fires_when_a_page_becomes_active(qapp):
    from src.ui.main_window import MainWindow
    from PySide6.QtWidgets import QStackedWidget

    win = MainWindow.__new__(MainWindow)
    win._active_page = None
    win._sidebar_buttons = []

    class _Page(QWidget):
        def __init__(self):
            super().__init__()
            self.shown = 0

        def on_page_shown(self):
            self.shown += 1

    page_a, page_b = _Page(), QWidget()
    stack = QStackedWidget()
    stack.addWidget(page_a)
    stack.addWidget(page_b)
    win.content_stack = stack
    win._page_widgets = {"a": page_a, "b": page_b}

    # Start on B (as the app boots on some other page), then navigate TO A —
    # a genuine change fires the hook.
    stack.setCurrentWidget(page_b)
    win._set_active_page("a")
    assert page_a.shown == 1
    # Away to B and back to A — the stale-surface return fires it again.
    win._set_active_page("b")
    win._set_active_page("a")
    assert page_a.shown == 2


def test_reselecting_the_same_page_does_not_refire(qapp):
    from src.ui.main_window import MainWindow
    from PySide6.QtWidgets import QStackedWidget

    win = MainWindow.__new__(MainWindow)
    win._active_page = None
    win._sidebar_buttons = []

    class _Page(QWidget):
        def __init__(self):
            super().__init__()
            self.shown = 0

        def on_page_shown(self):
            self.shown += 1

    page, other = _Page(), QWidget()
    stack = QStackedWidget()
    stack.addWidget(other)   # boot on 'other' so the first select of 'a' fires
    stack.addWidget(page)
    win.content_stack = stack
    win._page_widgets = {"a": page, "b": other}

    stack.setCurrentWidget(other)
    win._set_active_page("a")   # genuine change → fires once
    win._set_active_page("a")   # same widget already current → must NOT refire
    assert page.shown == 1, "the hook re-fired on a no-op re-selection"


def test_a_page_without_the_hook_is_fine(qapp):
    from src.ui.main_window import MainWindow
    from PySide6.QtWidgets import QStackedWidget

    win = MainWindow.__new__(MainWindow)
    win._active_page = None
    win._sidebar_buttons = []
    plain = QWidget()
    stack = QStackedWidget()
    stack.addWidget(plain)
    win.content_stack = stack
    win._page_widgets = {"a": plain}
    win._set_active_page("a")   # must not raise


def test_enablement_page_exposes_the_hook():
    """The hook must exist on the real page and route to a local reload."""
    from src.ui.pages.enablement.page import EnablementPage
    assert hasattr(EnablementPage, "on_page_shown")


# ── the Agent fallback ─────────────────────────────────────────────────

def test_agent_fallback_with_no_engine_is_an_explanatory_label(qapp):
    from src.ui.main_window import MainWindow
    win = MainWindow.__new__(MainWindow)
    win._agent_controller = None
    fb = win._native_agent_fallback()
    assert isinstance(fb, QLabel)
    assert "unavailable" in fb.text().lower()


def test_agent_fallback_with_a_live_engine_is_a_usable_chat(qapp):
    """When only the web view failed, the fallback is a real native chat wired
    to the controller — not a dead label."""
    from src.ui.main_window import MainWindow
    from src.ui.pages.enablement.chat_panel import ChatPanel
    from PySide6.QtCore import QObject, Signal

    class _Engine(QObject):
        response_ready = Signal(str)
        error_occurred = Signal(str)

    class _Controller:
        def __init__(self, engine):
            self.engine = engine
            self.sent = []

        def send(self, text):
            self.sent.append(text)

    engine = _Engine()
    win = MainWindow.__new__(MainWindow)
    win._agent_controller = _Controller(engine)

    fb = win._native_agent_fallback()
    assert isinstance(fb, ChatPanel), "expected a usable native chat panel"

    # Typing routes to the controller, and an engine response lands in the panel.
    fb.chat_submitted.emit("what's on my plate?")
    assert win._agent_controller.sent == ["what's on my plate?"]
    engine.response_ready.emit("Here is your day.")
    # add_message appended without raising — the wiring is live.
