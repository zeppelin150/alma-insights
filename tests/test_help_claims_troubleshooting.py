"""Accuracy audit of the in-app Help Center — section: troubleshooting.

Each test settles ONE falsifiable claim made by an article under
``assets/help/troubleshooting/``. The docstring of every test names the
article and quotes (or closely paraphrases) the claim under test.

Where the article claims X and the code does NOT-X, the test still asserts
the ARTICLE's claim and carries ``@pytest.mark.xfail(strict=True)`` with the
actual behaviour in the reason. That keeps this suite green while making
every documented discrepancy explicit and tracked — an xpass means someone
fixed the code and the article is now correct (or the article was edited).

Articles covered:
  flag-a-bug.md · what-to-include.md · connect-first.md · blank-screen.md
  renn-refuses.md · known-issues.md

Headless: no network, no credentials, no QtWebEngine import.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parents[1]
HELP_DIR = REPO / "assets" / "help"
TROUBLESHOOTING = HELP_DIR / "troubleshooting"


# ══════════════════════════════════════════════════════════════════════
#  Fixtures
# ══════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def qapp():
    """One QApplication for the module (offscreen)."""
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def help_conn(empty_db):
    """A database with the bundled help corpus loaded."""
    from src.data.help import loader
    loader.load_bundled_help(empty_db.conn)
    return empty_db.conn


@pytest.fixture(scope="module")
def tool_registry():
    """The live Renn tool registry: name -> {handler, phi_level, description}."""
    from src.data.chat_tools.registry import get_tool_registry
    return get_tool_registry()


@pytest.fixture(scope="module")
def renn_prompt():
    from src.ui.pages.enablement.page import RENN_SYSTEM_PROMPT
    return RENN_SYSTEM_PROMPT


def _src(obj) -> str:
    import inspect
    return inspect.getsource(obj)


class _Recorder:
    """Collects calls made to a patched callable."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return None

    @property
    def called(self) -> bool:
        return bool(self.calls)


# ══════════════════════════════════════════════════════════════════════
#  flag-a-bug.md
# ══════════════════════════════════════════════════════════════════════

def _bug_form_host(qapp):
    """A minimal QWidget standing in for EnablementPage as the dialog parent.

    ``_open_bug_form`` uses ``self`` only as a QMessageBox parent, so calling
    the real unbound method against a bare QWidget exercises the production
    code path without constructing the whole enablement page.
    """
    from PySide6.QtWidgets import QWidget
    return QWidget()


def _patch_bug_form(monkeypatch, url_value):
    """Patch settings + the two Qt exits used by ``_open_bug_form``.

    Returns (openUrl_recorder, information_recorder, warning_recorder).
    """
    import src.data.settings_manager as sm
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QMessageBox

    def fake_get_section(name, default=None):
        if name == "enablement":
            return {"help": {"bug_form_url": url_value}} if url_value is not None else {}
        return default if default is not None else {}

    monkeypatch.setattr(sm, "get_section", fake_get_section)
    opened, info, warn = _Recorder(), _Recorder(), _Recorder()
    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(opened))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(info))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(warn))
    return opened, info, warn


def test_flag_a_bug_opens_configured_url_in_system_browser(qapp, monkeypatch):
    """flag-a-bug.md: "Both open your team's Asana intake form in your normal
    web browser — not inside the app." / "The form should open externally."

    The configured URL must reach QDesktopServices.openUrl (the OS browser)
    and no dialog may be raised instead.
    """
    from src.ui.pages.enablement.page import EnablementPage

    target = "https://form.asana.com/?k=abc123"
    opened, info, warn = _patch_bug_form(monkeypatch, target)

    EnablementPage._open_bug_form(_bug_form_host(qapp), "")

    assert opened.called, "QDesktopServices.openUrl was never called"
    qurl = opened.calls[0][0][0]
    assert qurl.toString() == target
    assert not info.called and not warn.called


def test_flag_a_bug_does_not_use_an_embedded_web_view(qapp):
    """flag-a-bug.md: the form opens externally, "not inside the app" —
    "The form opens inside the app instead of your browser. That is a defect."

    The handler's source must not reach for any embedded-view API.
    """
    from src.ui.pages.enablement.page import EnablementPage

    body = _src(EnablementPage._open_bug_form)
    assert "QDesktopServices" in body
    for forbidden in ("QWebEngineView", "WebHost", "setUrl(", "load("):
        assert forbidden not in body, f"embedded-navigation API {forbidden!r} in _open_bug_form"


def test_flag_a_bug_attaches_nothing_to_the_opened_url(qapp, monkeypatch):
    """flag-a-bug.md: "Nothing is collected on your behalf. The app does not
    attach logs, take a screenshot, read your drafts, or send anything
    anywhere."

    The URL handed to the browser must be byte-identical to the configured
    one even when an article id is supplied — no appended context.
    """
    from src.ui.pages.enablement.page import EnablementPage

    target = "https://form.asana.com/?k=abc123"
    opened, _info, _warn = _patch_bug_form(monkeypatch, target)

    EnablementPage._open_bug_form(_bug_form_host(qapp), "troubleshooting-known-issues")

    assert opened.calls[0][0][0].toString() == target, (
        "the opened URL was modified — something was attached to the report")


def test_flag_a_bug_unset_url_explains_instead_of_opening_a_dead_link(qapp, monkeypatch):
    """flag-a-bug.md: "If no form address has been configured yet, you should
    get a short explanation saying so — never a dead link or a blank tab." and
    "An administrator sets the form address in settings
    (`enablement.help.bug_form_url`)."
    """
    from src.ui.pages.enablement.page import EnablementPage

    opened, info, warn = _patch_bug_form(monkeypatch, "")

    EnablementPage._open_bug_form(_bug_form_host(qapp), "")

    assert not opened.called, "a browser was opened despite no configured URL"
    assert info.called, "no explanation was shown for the unset URL"
    message = " ".join(str(a) for a in info.calls[0][0])
    assert "enablement.help.bug_form_url" in message
    assert not warn.called


def test_flag_a_bug_refuses_a_non_http_url(qapp, monkeypatch):
    """flag-a-bug.md: "never a dead link or a blank tab" — a configured value
    that is not a web address must be refused rather than handed to the OS.

    (A non-http scheme reaching QDesktopServices.openUrl is an OS-level
    handler invocation, e.g. file:// or a custom protocol.)
    """
    from src.ui.pages.enablement.page import EnablementPage

    for bad in ("file:///C:/Windows/System32/calc.exe", "javascript:alert(1)",
                "ftp://example.com/form", "not a url at all"):
        opened, info, warn = _patch_bug_form(monkeypatch, bad)
        EnablementPage._open_bug_form(_bug_form_host(qapp), "")
        assert not opened.called, f"{bad!r} was opened instead of refused"
        assert warn.called, f"{bad!r} was refused silently, with no warning"


def test_flag_a_bug_accepts_http_and_https_only(qapp, monkeypatch):
    """flag-a-bug.md: the button "should open your browser on the form" —
    both http and https addresses are honoured."""
    from src.ui.pages.enablement.page import EnablementPage

    for good in ("http://intake.internal/form", "HTTPS://form.asana.com/x"):
        opened, _info, warn = _patch_bug_form(monkeypatch, good)
        EnablementPage._open_bug_form(_bug_form_host(qapp), "")
        assert opened.called, f"{good!r} was refused"
        assert not warn.called


def test_help_center_header_has_a_flag_a_bug_button_that_emits(qapp, empty_db):
    """flag-a-bug.md: "**Flag a bug** appears in the Help Center header".

    Asserts behaviour: clicking the header button emits ``flag_bug_requested``
    carrying the article currently on screen.
    """
    from PySide6.QtWidgets import QPushButton
    from src.ui.pages.enablement.help_tab import HelpTab

    tab = HelpTab(lambda: empty_db.conn)
    buttons = [b for b in tab.findChildren(QPushButton) if b.text() == "Flag a bug"]
    assert len(buttons) == 1, "no single 'Flag a bug' button in the Help Center header"

    seen = []
    tab.flag_bug_requested.connect(seen.append)
    tab.show_article("troubleshooting-known-issues")
    buttons[0].click()

    assert seen == ["troubleshooting-known-issues"], (
        f"button did not emit the current article id: {seen!r}")


def test_help_tab_flag_bug_signal_is_wired_to_the_bug_form_opener(qapp, empty_db):
    """flag-a-bug.md: the header button opens the form — i.e. the Help tab's
    signal is actually connected to the opener, not left dangling.

    Exercises the real ``EnablementPage._make_help`` wiring code against a
    stand-in host, then clicks the button.
    """
    from PySide6.QtWidgets import QPushButton, QWidget
    from src.ui.pages.enablement.page import EnablementPage

    class Host(QWidget):
        def __init__(self):
            super().__init__()
            self._conn = lambda: empty_db.conn
            self.opened_with = []

        def _open_bug_form(self, article_id=""):
            self.opened_with.append(article_id)

    host = Host()
    tab = EnablementPage._make_help(host)
    assert host._help_tab is tab

    button = next(b for b in tab.findChildren(QPushButton) if b.text() == "Flag a bug")
    button.click()

    assert host.opened_with == [""] or len(host.opened_with) == 1, (
        "clicking 'Flag a bug' did not reach _open_bug_form")


def test_feedback_button_in_the_top_bar_routes_to_the_same_bug_form(qapp):
    """flag-a-bug.md: "the **Feedback** button in the top bar does the same
    thing."

    In enablement mode ``_show_feedback`` must delegate to the Help page's
    ``_open_bug_form`` rather than opening the legacy product HelpDialog.
    """
    from src.ui import app_modes
    from src.ui.main_window import MainWindow

    class HelpPage:
        def __init__(self):
            self.opened = []

        def _open_bug_form(self, article_id=""):
            self.opened.append(article_id)

    class Stub:
        def __init__(self):
            self._mode = app_modes.MODE_ENABLEMENT
            self.help_page = HelpPage()
            self._page_widgets = {"en_help": self.help_page}
            self.navigated = []

        def _set_active_page(self, page_id):
            self.navigated.append(page_id)

    stub = Stub()
    MainWindow._show_feedback(stub)

    assert stub.help_page.opened, "Feedback did not open the bug form"
    assert stub.navigated == ["en_help"]


def test_feedback_button_is_connected_to_show_feedback(qapp):
    """flag-a-bug.md: the top-bar Feedback button "does the same thing" —
    it must have a handler at all."""
    from src.ui.main_window import MainWindow

    body = _src(MainWindow)
    assert re.search(r"feedback_btn\.clicked\.connect\(self\._show_feedback\)", body), (
        "the top-bar Feedback button has no .clicked.connect(self._show_feedback)")


def test_help_page_cannot_navigate_the_app_via_a_link(qapp, help_conn):
    """flag-a-bug.md: "A help page should never be able to navigate the app
    itself somewhere."

    ``HelpTab`` disables Qt's own link handling and only honours the internal
    ``help://`` scheme; every other URL is a no-op.
    """
    from PySide6.QtCore import QUrl
    from src.ui.pages.enablement.help_tab import HelpTab

    tab = HelpTab(lambda: help_conn)
    assert tab._viewer.openLinks() is False
    assert tab._viewer.openExternalLinks() is False

    tab.show_article("troubleshooting-flag-a-bug")
    before = tab.current_article_id()
    for hostile in ("https://evil.example/steal", "file:///C:/Windows/win.ini",
                    "app://settings", "javascript:alert(1)"):
        tab._on_anchor(QUrl(hostile))
        assert tab.current_article_id() == before, f"{hostile!r} navigated the tab"

    tab._on_anchor(QUrl("help://troubleshooting-known-issues"))
    assert tab.current_article_id() == "troubleshooting-known-issues", (
        "the internal help:// scheme stopped working")


# ══════════════════════════════════════════════════════════════════════
#  what-to-include.md
# ══════════════════════════════════════════════════════════════════════

def test_every_help_article_has_a_how_it_should_work_section():
    """what-to-include.md: "Every Help Center article has a 'How it should
    work' section; if the app disagreed with it, quote the line."
    """
    articles = sorted(HELP_DIR.glob("*/*.md"))
    assert len(articles) >= 60, (
        f"only {len(articles)} help articles found — the corpus path is wrong")

    missing = [p.relative_to(HELP_DIR).as_posix() for p in articles
               if "## How it should work" not in p.read_text(encoding="utf-8")]
    assert not missing, f"articles with no 'How it should work' section: {missing}"


# ══════════════════════════════════════════════════════════════════════
#  connect-first.md
# ══════════════════════════════════════════════════════════════════════

def test_google_connection_is_not_persistent_across_launches():
    """connect-first.md: "The Google connection is not persistent across app
    launches — you reconnect each time you start the app."

    The module-global live credential starts ``None`` with no import-time side
    effect, so a fresh process is disconnected until ``reconnect()`` runs.
    """
    import importlib
    from src.data import google_oauth

    importlib.reload(google_oauth)
    assert google_oauth._active is None
    assert google_oauth.is_active() is False, (
        "Google reported active without an explicit reconnect() this session")
    assert google_oauth.load_active_credentials() is None, (
        "credentials were live without an explicit reconnect() this session")
    assert hasattr(google_oauth, "reconnect")

    # Stored credentials on disk must NOT by themselves make the session live.
    if google_oauth.has_stored_credentials():
        assert google_oauth.is_active() is False, (
            "a stored refresh token alone activated the session")


def test_whats_connected_reports_the_three_touchpoints(empty_db, tool_registry):
    """connect-first.md: 'Ask Renn "what's connected?" for a direct answer
    covering the active Drive folders, the active Asana board, and the Guru
    publish target.'

    Shape-only (dev-machine settings must not decide the verdict).
    """
    assert "get_enablement_routing" in tool_registry
    result = tool_registry["get_enablement_routing"]["handler"](empty_db.conn, {}, None)

    assert result.get("ok") is True
    assert set(result) >= {"drive", "asana", "guru"}
    assert "active_folder_ids" in result["drive"] and "count" in result["drive"]
    assert "active_board_gid" in result["asana"] and "connected" in result["asana"]
    assert "publish_collection_id" in result["guru"] and "connected" in result["guru"]


def test_a_disconnected_source_is_reported_not_raised(empty_db, tool_registry, monkeypatch):
    """connect-first.md: "A disconnected source should be reported as
    disconnected — never as an error, and never silently skipped."

    Zendesk with no credentials is the reference case.
    """
    import src.data.settings_manager as sm
    monkeypatch.setattr(sm, "get_section", lambda name, default=None: {})

    handler = tool_registry["list_zendesk_articles"]["handler"]
    result = handler(empty_db.conn, {}, None)

    assert isinstance(result, dict), f"disconnected source returned {type(result)}"
    blob = repr(result).lower()
    assert "not_connected" in blob or "not connected" in blob, (
        f"a disconnected Zendesk was not reported as disconnected: {result!r}")


def test_test_connections_button_does_nothing(qapp):
    """connect-first.md: "the Test connections button does nothing at present,
    so a lack of result there is not evidence either way."
    (Also known-issues.md: "The Test connections button does nothing.")
    """
    from src.ui.pages.enablement.settings import SettingsPage

    body = _src(SettingsPage._intro)
    assert '"Test connections"' in body
    assert ".connect(" not in body, (
        "the Test connections button now has a handler — known-issues.md is stale")


def test_demo_mode_uses_a_throwaway_database_wiped_each_session(qapp):
    """connect-first.md: "Demo mode uses a throwaway database that is wiped
    each session and never touches your real accounts."
    """
    import tempfile
    from src.ui.pages.enablement.page import EnablementPage

    body = _src(EnablementPage._ensure_demo_db)
    assert "tempfile.gettempdir()" in body, "the demo DB is not in the temp dir"
    assert "os.remove" in body, "the demo DB is not deleted at session start"
    assert "-wal" in body and "-shm" in body, (
        "the WAL/SHM sidecars survive, so the demo DB is not fully wiped")

    # It really lands under the OS temp dir, not the user's warehouse.
    demo_path = Path(tempfile.gettempdir()) / "alma_enablement_demo.db"
    assert "alma_enablement_demo.db" in body
    assert demo_path.parent == Path(tempfile.gettempdir())

    # "never touches your real accounts": demo publishes get no live Guru client
    class Stub:
        demo = True
        _guru_client = object()

    assert EnablementPage._guru_for_push(Stub()) is None
    Stub.demo = False
    assert EnablementPage._guru_for_push(Stub()) is not None, (
        "control failed — _guru_for_push returns None in live mode too")


def test_sources_are_pulled_on_a_tick_not_continuously(qapp, empty_db):
    """connect-first.md: "Nothing has been scanned yet. Sources are pulled on
    a scan or a monitor tick, not continuously."

    The Drive monitor is timer-driven with a floor on the interval, and is
    paused until something explicitly starts it.
    """
    from src.data import drive_monitor

    assert drive_monitor._DEFAULT_INTERVAL >= 60

    monitor = drive_monitor.DriveMonitor(empty_db)
    assert monitor.status == "paused", "the monitor polls before anything starts it"
    assert monitor._timer.isActive() is False

    # An interval floor exists, so it can never be turned into a hot loop.
    monitor.start(interval_seconds=1)
    try:
        assert monitor._interval >= 60, "the poll interval has no lower bound"
        assert monitor._timer.interval() == monitor._interval * 1000
    finally:
        monitor.stop()
    assert monitor.status == "paused"


def test_agent_page_fallback_with_a_live_engine_is_a_usable_chat(qapp):
    """blank-screen.md: "The Agent page now has that safety net too. If its
    embedded view cannot start but the assistant itself is fine, it falls back
    to a plain native chat wired to the same assistant ... you can still type
    and get answers."

    When only the embedded web view failed, ``_native_agent_fallback`` returns a
    real native ChatPanel wired to the controller — typing routes to
    ``controller.send`` and an engine response lands in the panel. Not a dead
    label. If this regresses to a bare label, the article is stale.
    """
    from PySide6.QtCore import QObject, Signal
    from src.ui.main_window import MainWindow
    from src.ui.pages.enablement.chat_panel import ChatPanel

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
    assert isinstance(fb, ChatPanel), (
        f"a live engine yielded a {type(fb).__name__}, not the usable native "
        "chat panel the article describes")

    # Typing routes to the controller — the fallback is genuinely interactive.
    fb.chat_submitted.emit("what's on my plate?")
    assert win._agent_controller.sent == ["what's on my plate?"], (
        "typing in the Agent fallback did not reach controller.send")
    # An engine response lands in the panel without raising — wiring is live.
    engine.response_ready.emit("Here is your day.")


def test_switching_away_and_back_refreshes_a_surface(qapp):
    """connect-first.md: "Switching to another screen and back now refreshes it
    — each enablement screen re-reads its local data when you return to it."
    blank-screen.md: "returning to a screen now reloads a web view that failed
    to come up."

    Behavioural: pages are built once into a QStackedWidget, so
    ``_set_active_page`` fires the page's opt-in ``on_page_shown()`` refresh hook
    when it becomes active again — but NOT on a same-widget re-selection (a tab
    click within the page). If that hook stops firing on return, the articles'
    promise of a self-refreshing surface is broken.
    """
    from PySide6.QtWidgets import QStackedWidget, QWidget
    from src.ui.main_window import MainWindow

    class Surface(QWidget):
        """A page recording each time it is asked to refresh on activation."""

        def __init__(self):
            super().__init__()
            self.shown = 0

        def on_page_shown(self):
            self.shown += 1

    agent, other = Surface(), Surface()
    stack = QStackedWidget()
    stack.addWidget(agent)
    stack.addWidget(other)

    class Stub:
        def __init__(self):
            self.content_stack = stack
            self._page_widgets = {"en_agent": agent, "en_tasks": other}
            self._sidebar_buttons = []
            self._active_page = None

    stub = Stub()
    # Boot on tasks so the first select of the agent page is a genuine change.
    stack.setCurrentWidget(other)
    MainWindow._set_active_page(stub, "en_agent")
    first_instance = stack.currentWidget()
    assert agent.shown == 1, "the refresh hook did not fire on first activation"

    # Away and back → the surface refreshes itself again.
    MainWindow._set_active_page(stub, "en_tasks")
    MainWindow._set_active_page(stub, "en_agent")

    assert stub.content_stack.currentWidget() is agent
    assert stub.content_stack.currentWidget() is first_instance, (
        "a new widget instance appeared — the page was rebuilt, not re-shown")
    assert agent.shown == 2, (
        "navigating away and back did not re-fire on_page_shown() — the article "
        "says returning to a screen refreshes it")

    # A no-op re-selection of the page already showing must NOT refire.
    MainWindow._set_active_page(stub, "en_agent")
    assert agent.shown == 2, (
        "the refresh hook re-fired on a same-widget re-select (a tab click)")


# ══════════════════════════════════════════════════════════════════════
#  blank-screen.md
# ══════════════════════════════════════════════════════════════════════

def test_agent_page_renders_inside_an_embedded_browser_view():
    """blank-screen.md: "Some surfaces, notably the Agent page, render inside
    an embedded browser view."

    Static source check — importing QtWebEngine here would destabilise the run.
    """
    source = (REPO / "src" / "ui" / "web" / "agent_page.py").read_text(encoding="utf-8")
    assert "QWebEngineView" in source


def test_agent_page_fallback_with_no_engine_is_an_explanatory_label(qapp):
    """blank-screen.md: "Only when the assistant engine cannot be built either
    do you get a short line of text explaining that, and nothing else."

    When the chat engine itself is unavailable (not just the web view),
    ``_native_agent_fallback`` degrades to a bare explanatory QLabel — no chat
    box, no buttons, nothing to act on. The two-tier fallback is the article's
    claim: a usable chat when the engine survives, a plain message when it does
    not.
    """
    from PySide6.QtWidgets import (
        QAbstractButton, QComboBox, QLabel, QLineEdit, QTextEdit,
    )
    from src.ui.main_window import MainWindow

    win = MainWindow.__new__(MainWindow)
    win._agent_controller = None            # no controller ⇒ no engine
    widget = win._native_agent_fallback()

    assert isinstance(widget, QLabel), (
        f"the no-engine fallback is a {type(widget).__name__}, not the static "
        "QLabel message the article describes")
    assert "unavailable" in widget.text().lower(), (
        "the fallback label does not explain that the Agent chat is unavailable")

    controls = (widget.findChildren(QAbstractButton)
                + widget.findChildren(QLineEdit)
                + widget.findChildren(QTextEdit)
                + widget.findChildren(QComboBox))
    assert not controls, (
        "the no-engine Agent fallback offers an interactive control — the "
        "article says it is a plain message with nothing to act on")


def test_web_tab_failure_falls_back_to_the_native_qt_surface():
    """blank-screen.md: "the app is built to fall back to a plain version of a
    screen when its embedded view does not come up".

    The Calendar/Workbench factories import the web stack inside a try and
    fall through to the native Qt page on any failure.
    """
    from src.ui.pages.enablement.page import EnablementPage

    for factory in (EnablementPage._make_calendar, EnablementPage._make_workbench):
        body = _src(factory)
        assert "web_tabs_mode()" in body, f"{factory.__name__} does not consult the flag"
        assert "except Exception" in body, (
            f"{factory.__name__} has no fallback when the web stack is unavailable")


# ══════════════════════════════════════════════════════════════════════
#  renn-refuses.md
# ══════════════════════════════════════════════════════════════════════

# Guru-folder / Asana / Drive-upload writes the article says are gated.
_GATED_WRITE_TOOLS = (
    "request_create_guru_folder",
    "request_rename_guru_folder",
    "request_create_asana_task",
    "request_asana_task_update",
    "request_upload_artifact_to_drive",
)


def test_asana_guru_folder_and_drive_upload_writes_are_proposals(tool_registry, renn_prompt):
    """renn-refuses.md: "Anything that changes Asana, creates or renames a
    Guru folder, or uploads to Drive is a proposal, not an action. Renn cannot
    execute it; your click does."
    """
    for name in _GATED_WRITE_TOOLS:
        assert name in tool_registry, f"gated write tool {name} is not registered"
        desc = tool_registry[name]["description"].lower()
        assert "propose" in desc or "confirm" in desc, (
            f"{name} does not describe itself as a proposal: {desc!r}")

    assert "there is no direct-execute tool" in renn_prompt.lower()


def test_no_ungated_asana_or_guru_folder_write_tool_exists(tool_registry):
    """renn-refuses.md: "Renn cannot execute it; your click does."

    There must be no directly-executing counterpart alongside the gated
    proposals (a bypass would make the confirmation theatre).
    """
    forbidden = {
        "create_asana_task", "update_asana_task", "complete_asana_task",
        "create_guru_folder", "rename_guru_folder", "delete_guru_folder",
        "upload_artifact_to_drive", "upload_to_drive",
    }
    present = forbidden & set(tool_registry)
    assert not present, f"un-gated write tools are registered: {sorted(present)}"


def test_background_research_is_not_available_and_renn_is_told_to_decline(
        tool_registry, renn_prompt):
    """renn-refuses.md: '"I can't research that." Background research does not
    exist in this build. Renn is instructed to say so and offer to search your
    existing content instead.'
    (known-issues.md: "Background research does not exist. Renn will decline it.")
    """
    for name in ("request_research_plan", "get_task_research", "start_research",
                 "run_research"):
        assert name not in tool_registry, f"background-research tool {name} IS registered"

    low = renn_prompt.lower()
    assert "no background-research tools in this build" in low
    assert "never claim research is running" in low
    # …and the offered alternative really exists
    assert "search_content" in tool_registry
    assert "research_topic" in tool_registry


def test_renn_is_instructed_never_to_repeat_a_drive_folder_name(renn_prompt):
    """renn-refuses.md: "Refusing to name a Drive folder. Renn refers to 'the
    active folder' rather than repeating folder names, because folder names can
    carry identifying information. This is deliberate."
    """
    low = renn_prompt.lower()
    assert "never repeat a google drive folder name" in low
    assert "the active folder" in low


def test_the_routing_report_returns_no_drive_folder_names(empty_db, tool_registry):
    """renn-refuses.md: Renn "refers to 'the active folder' rather than
    repeating folder names" — the routing tool must return folder IDs only.
    """
    result = tool_registry["get_enablement_routing"]["handler"](empty_db.conn, {}, None)
    drive = result["drive"]
    assert "active_folder_ids" in drive
    assert not any("name" in key for key in drive), (
        f"the routing report leaks a Drive folder name field: {sorted(drive)}")


def test_renn_resolves_ids_through_pickers_rather_than_asking_for_them(
        tool_registry, renn_prompt):
    """renn-refuses.md: "Renn asks you for a raw ID. A project GID, a folder
    ID, a card ID. It is built to resolve those itself through a picker."
    """
    for picker in ("request_drive_picker", "request_asana_board_picker",
                   "request_guru_publish_picker"):
        assert picker in tool_registry, f"{picker} is not registered"
        assert "picker" in tool_registry[picker]["description"].lower()

    assert "never ask the user for gids" in renn_prompt.lower()


def test_renn_has_no_tool_that_defers_work_and_reports_back_later(tool_registry):
    """renn-refuses.md: "Renn claims something is done when it is not.
    Especially 'research is running', 'I've queued that', or 'I'll follow up
    later' — it has no mechanism to come back to you asynchronously."

    Narrow, falsifiable form: no registered tool schedules deferred work that
    would later push an unsolicited turn.
    """
    scheduling = {"schedule", "remind", "follow_up", "followup", "defer",
                  "notify_later", "queue_research", "background_job"}
    hits = [n for n in tool_registry if any(s in n for s in scheduling)]
    assert not hits, f"deferred-work tools are registered: {hits}"


def test_renn_has_no_code_execution_or_web_browsing_tool(tool_registry):
    """renn-refuses.md: "Renn mentions a capability that does not appear
    anywhere in this Help Center — running code, reading files, browsing the
    web. Report it with the transcript."
    """
    forbidden_names = {"run_code", "execute", "exec", "shell", "bash", "python",
                       "browse_web", "fetch_url", "http_get", "read_file",
                       "write_file", "web_search"}
    hits = [n for n in tool_registry if n in forbidden_names]
    assert not hits, f"capability tools outside the Help Center are registered: {hits}"


def test_status_badge_labels_match_the_article(qapp):
    """renn-refuses.md: "a badge saying PARTIAL, OFF BY DEFAULT or NOT
    AVAILABLE YET means the Help Center already agrees with Renn."
    """
    from src.ui.pages.enablement.help_tab import _STATUS_BADGE

    labels = {status: spec[0] for status, spec in _STATUS_BADGE.items()}
    assert labels == {
        "partial": "PARTIAL",
        "flag-gated": "OFF BY DEFAULT",
        "not-available": "NOT AVAILABLE YET",
    }


# ══════════════════════════════════════════════════════════════════════
#  known-issues.md — every entry, individually
# ══════════════════════════════════════════════════════════════════════

def test_known_issue_asana_api_key_field_is_never_saved(qapp):
    """known-issues.md: "The API key field in Settings is not wired to
    anything that saves it, and no other screen writes an Asana key."
    """
    from src.ui.pages.enablement.settings import SettingsPage

    body = _src(SettingsPage._asana)
    assert "QLineEdit()" in body, "the API key field is gone — the article is stale"
    assert "keyf" in body
    for persist in ("save_setting", "set_section", "store_setting", "asana_api_key"):
        assert persist not in body, (
            f"the Asana key field now persists via {persist!r} — known-issues.md is stale")


def test_known_issue_no_src_module_writes_an_asana_api_key():
    """known-issues.md: "no other screen writes an Asana key ... an
    administrator does this with the project's setup scripts".
    """
    writers = []
    for path in (REPO / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in re.finditer(r"(save_setting|set_setting|store_setting)\s*\(\s*[\"']asana_api_key", text):
            writers.append(f"{path.relative_to(REPO).as_posix()}:{text[:match.start()].count(chr(10)) + 1}")
    assert not writers, f"src/ now writes asana_api_key at {writers} — known-issues.md is stale"

    scripts = [p.name for p in (REPO / "scripts").glob("*.py")
               if "asana_api_key" in p.read_text(encoding="utf-8", errors="ignore")]
    assert scripts, "no setup script writes asana_api_key either — the stated workaround does not exist"


def test_known_issue_asana_key_field_never_shows_a_stored_key(qapp):
    """known-issues.md: "the field will look empty even when a key IS present,
    so it tells you nothing about your connection state."
    """
    from src.ui.pages.enablement.settings import SettingsPage

    body = _src(SettingsPage._asana)
    assert "keyf.setText" not in body
    assert "load_setting" not in body, "the field now loads the stored key"
    assert "setPlaceholderText" in body


def test_known_issue_asana_discovery_is_tagged_mock_and_setup_refuses_it(qapp, monkeypatch):
    """known-issues.md (corrected): "When no Asana key is stored, discovery
    returns built-in sample projects, but the payload is now tagged mock=True
    instead of being passed off as real, and live setup refuses to save a board
    built from the sample GIDs."
    """
    from src.data import asana_setup
    import src.data.pat_store as pat_store
    from src.ui.pages.enablement.page import EnablementPage

    monkeypatch.setattr(pat_store, "load_setting", lambda *a, **k: "")
    result = asana_setup.discover()

    # Still returns the sample projects (the demo flow stays demonstrable)…
    names = [p["name"] for p in result["projects"]]
    assert "Enablement Requests" in names, "the sample projects are gone"
    # …but the payload is now explicitly tagged as mock, not silent.
    assert result.get("mock") is True, (
        "discovery no longer tags sample data as mock — it is presented as real")

    # And live setup refuses to persist a board built from the fabricated GIDs.
    def _boom_save(*a, **k):
        raise AssertionError("setup persisted a board config built from mock GIDs")

    monkeypatch.setattr(asana_setup, "set_asana_board_config", _boom_save)

    class _Chat:
        def __init__(self):
            self.msgs = []

        def set_chat(self, msgs):
            self.msgs = msgs

    class Stub:
        demo = False                        # live mode, not demo

        def __init__(self):
            self.chat = _Chat()
            self.status = []

        def _set_status(self, msg):
            self.status.append(msg)

        def _open_chat(self):
            pass

        def _conn(self):
            raise AssertionError("live Asana setup touched the DB with mock data")

    stub = Stub()
    EnablementPage._on_asana_setup(stub)     # must take the refusal branch

    assert stub.status and "not connected" in stub.status[-1].lower(), (
        f"setup did not report the refusal: {stub.status!r}")
    blob = " ".join(m for _who, m in stub.chat.msgs).lower()
    assert "api key" in blob or "connect asana" in blob, (
        f"the refusal did not explain the missing key: {stub.chat.msgs!r}")


def test_known_issue_asana_board_list_in_settings_is_hardcoded(qapp):
    """known-issues.md: "The Asana board list in Settings is a mockup. The
    '2 configured' heading, the board rows, the field mappings and the resolved
    assignee names are fixed placeholder text, not your configuration."
    """
    from src.ui.pages.enablement.settings import SettingsPage

    body = _src(SettingsPage._asana)
    for literal in ("ASANA BOARDS  ·  2 configured", "Enablement Requests",
                    "Launch Coordination", "Assigned Team", "Urgency",
                    "Resolved: J. Rivera, M. Chen, A. Osei  (+4)"):
        assert literal in body, f"hardcoded literal {literal!r} is gone — the article may be stale"

    for live in ("monitor_sources", "list_sources", "get_asana_board", "self._conn"):
        assert live not in body, (
            f"the board list now reads live config via {live!r} — known-issues.md is stale")


def test_known_issue_drive_publish_from_the_workbench_writes_nothing(qapp):
    """known-issues.md: "Publishing to Drive from the Workbench writes nothing.
    Both Drive destinations only display a status message."
    """
    from src.ui.pages.enablement.page import EnablementPage

    class Stub:
        def __init__(self):
            self.status = []
            self.said = []
            self.demo = False

        def _set_status(self, msg):
            self.status.append(msg)

        def _chat_say(self, who, msg):
            self.said.append((who, msg))

        def _on_push(self, *a, **k):          # a real publish path
            raise AssertionError("a real publish was attempted")

        def _conn(self):
            raise AssertionError("the database was touched")

    for dest in ("drive_new", "drive_update"):
        stub = Stub()
        EnablementPage._on_publish(stub, dest)
        assert len(stub.status) == 1 and len(stub.said) == 1, (
            f"{dest} did more than set a status message")
        assert "demo" in stub.status[0].lower(), (
            f"{dest} status no longer marks itself as a stub: {stub.status[0]!r}")


def test_known_issue_web_tabs_flag_is_absent_and_defaults_off():
    """known-issues.md: "The React Calendar and Workbench never render. They
    are behind a setting that is absent from the shipped configuration, so the
    standard Qt versions are what you see."
    """
    from src.ui.web.web_flags import VALID_MODES, web_tabs_mode

    settings = REPO / "data" / "settings.yaml"
    if settings.exists():
        assert "web_tabs" not in settings.read_text(encoding="utf-8"), (
            "enablement.web_tabs is now present in data/settings.yaml")

    assert VALID_MODES == ("off", "calendar", "all")
    assert web_tabs_mode() == "off", "the web tabs are no longer off by default"


def test_known_issue_web_tabs_mode_degrades_to_off(monkeypatch):
    """known-issues.md: the web tabs are unreachable — the flag must fail
    closed so "the standard Qt versions are what you see"."""
    import src.data.settings_manager as sm
    from src.ui.web import web_flags

    for value in ({"web_tabs": "nonsense"}, {"web_tabs": ""}, {"web_tabs": None},
                  {}, {"web_tabs": True}):
        monkeypatch.setattr(sm, "get_section", lambda n, d=None, v=value: v)
        assert web_flags.web_tabs_mode() == "off", f"{value!r} did not degrade to off"

    def boom(*a, **k):
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(sm, "get_section", boom)
    assert web_flags.web_tabs_mode() == "off"


def test_known_issue_content_studio_has_no_screen(qapp, tool_registry):
    """known-issues.md: "The Content Studio has no screen. Diagrams, quizzes,
    one-pagers, battle cards and decks are reachable only by asking Renn."
    """
    from src.ui import app_modes
    from src.ui.pages.enablement.page import EnablementPage

    page_ids = {spec.page_id for spec in app_modes.PAGES}
    tab_keys = {spec.tab_key for spec in app_modes.PAGES if spec.tab_key}
    for word in ("studio", "content_studio", "artifact", "diagram", "quiz"):
        assert not any(word in pid for pid in page_ids), f"a {word!r} page is registered"
        assert not any(word in key for key in tab_keys), f"a {word!r} tab is registered"

    build = _src(EnablementPage._build)
    assert "Studio" not in build, "a Studio tab was added to the enablement page"

    # …but the generators DO exist as Renn tools
    for tool in ("generate_diagram", "generate_quiz", "generate_doc", "generate_deck"):
        assert tool in tool_registry, f"{tool} is not reachable via Renn either"


def test_known_issue_diagram_and_quiz_previews_are_deferred(tool_registry):
    """known-issues.md: "Diagram and quiz previews are deferred."

    ``render_card_preview`` exists for card drafts; there is no diagram or
    quiz preview/render tool.
    """
    assert "render_card_preview" in tool_registry
    for absent in ("render_diagram", "preview_diagram", "render_quiz",
                   "preview_quiz", "render_artifact", "preview_artifact"):
        assert absent not in tool_registry, f"{absent} now exists — the article is stale"


def test_known_issue_podcast_is_a_reserved_kind_with_nothing_behind_it(tool_registry):
    """known-issues.md: "the podcast type is reserved with nothing behind it."
    """
    from src.data import artifact_store

    kinds = getattr(artifact_store, "ARTIFACT_KINDS", None) or getattr(artifact_store, "KINDS")
    assert "podcast" in kinds, "the reserved podcast kind is gone"

    assert not [n for n in tool_registry if "podcast" in n or "audio" in n or "tts" in n]

    producers = []
    for path in (REPO / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "podcast" in text and path.name != "artifact_store.py":
            producers.append(path.relative_to(REPO).as_posix())
    assert not producers, f"podcast code exists outside the reserved entry: {producers}"


def test_known_issue_multiword_search_misses_when_words_are_apart(empty_db):
    """known-issues.md: "Multi-word search misses in several places. Several
    search paths match a whole phrase only when the words appear together, so
    'prior authorization escalation' can miss a document containing those words
    apart."
    """
    from src.data import enablement_store as store

    store.save_document(
        empty_db.conn, source="drive", doc_id="doc-apart", name="Payer runbook",
        full_text=("Prior authorization requests are triaged first. "
                   "Any escalation goes to the payer desk."))
    store.save_document(
        empty_db.conn, source="drive", doc_id="doc-together", name="Contiguous",
        full_text="See the prior authorization escalation runbook.")

    phrase_hits = [d["doc_id"] for d in
                   store.search_documents(empty_db.conn, "prior authorization escalation")]

    assert "doc-together" in phrase_hits, "the contiguous phrase should still match"
    assert "doc-apart" not in phrase_hits, (
        "search_documents now matches words apart — known-issues.md is stale")

    # Control: the document IS findable with a single distinctive word, so the
    # miss above is about phrasing and not about the document being absent.
    single_hits = [d["doc_id"] for d in
                   store.search_documents(empty_db.conn, "escalation")]
    assert "doc-apart" in single_hits


def test_known_issue_help_center_search_does_not_have_the_multiword_problem(help_conn):
    """known-issues.md: "The Help Center's own search does not have this
    problem."
    """
    from src.data.help import search as help_search

    # A multi-word question whose words are scattered across the article.
    hits = help_search.search_help(help_conn, "how do I report a broken button",
                                   limit=5)
    assert hits, "help search returned nothing for a multi-word question"
    assert any(h["article_id"] == "troubleshooting-flag-a-bug" for h in hits), (
        f"help search missed the relevant article: {[h['article_id'] for h in hits]}")

    scattered = help_search.search_help(help_conn, "asana board mockup placeholder configured",
                                        limit=5)
    assert any(h["article_id"] == "troubleshooting-known-issues" for h in scattered)


def test_known_issue_drive_folder_picker_has_no_search_box():
    """known-issues.md: "The Drive folder picker has no search box. You
    navigate the folder tree by expanding it, which is slow with many folders."
    """
    jsx = (REPO / "web" / "src" / "chat" / "ChatApp.jsx").read_text(encoding="utf-8")
    start = jsx.index("function DriveFolderPicker(")
    end = jsx.index("function AsanaBoardPicker(", start)
    component = jsx[start:end]

    assert "<input" not in component, "the Drive folder picker now has an input"
    assert "drive-twisty" in component, "the expandable tree is gone"

    # Control: the Asana picker IS searchable, so the assertion above is real.
    asana = jsx[end:end + 6000]
    assert "<input" in asana, "control failed — the Asana picker has no input either"


def test_known_issue_drive_search_reads_only_the_first_page(monkeypatch):
    """known-issues.md: "Drive search results can be truncated. Only the first
    page of matches is read, so a match beyond the first page is invisible."
    """
    from src.data.drive_reader import DriveReader

    calls = []

    class FakeList:
        def __init__(self, kwargs):
            self._kwargs = kwargs

        def execute(self):
            calls.append(self._kwargs)
            return {"files": [{"id": "f1", "name": "one"}],
                    "nextPageToken": "MORE-RESULTS-EXIST"}

    class FakeFiles:
        def list(self, **kwargs):
            return FakeList(kwargs)

    class FakeService:
        def files(self):
            return FakeFiles()

    reader = DriveReader()
    monkeypatch.setattr(reader, "_build_service", lambda: FakeService())

    results = reader.search_files("payer", limit=5)

    assert len(calls) == 1, (
        f"search_files now paginates ({len(calls)} requests) — the article is stale")
    assert "pageToken" not in calls[0], "search_files now sends a pageToken"
    assert len(results) == 1, "a second page was fetched despite nextPageToken"


def test_known_issue_drive_reader_has_no_rate_limit_retry():
    """known-issues.md: "There is also no retry when Google rate-limits a
    request."
    """
    source = (REPO / "src" / "data" / "drive_reader.py").read_text(encoding="utf-8")
    for marker in ("retry", "backoff", "429", "sleep(", "num_retries"):
        assert marker not in source.lower(), (
            f"drive_reader now has retry logic ({marker!r}) — the article is stale")


def test_known_issue_drive_monitoring_does_not_descend_into_subfolders(monkeypatch):
    """known-issues.md: "Drive folder monitoring does not descend into
    subfolders, so changes in a nested folder can be missed."

    ``list_changed_files`` accepts ``recursive`` but builds an identical
    direct-children-only query either way.
    """
    from src.data.drive_reader import DriveReader

    queries = []

    class FakeList:
        def __init__(self, kwargs):
            self._kwargs = kwargs

        def execute(self):
            queries.append(self._kwargs["q"])
            return {"files": []}

    class FakeFiles:
        def list(self, **kwargs):
            return FakeList(kwargs)

    class FakeService:
        def files(self):
            return FakeFiles()

    reader = DriveReader()
    monkeypatch.setattr(reader, "_build_service", lambda: FakeService())

    reader.list_changed_files("FOLDER-A", recursive=True)
    reader.list_changed_files("FOLDER-A", recursive=False)

    assert queries[0] == queries[1], (
        "recursive=True now builds a different query — the article is stale")
    assert "'FOLDER-A' in parents" in queries[0]
    assert "fullText" not in queries[0]
    # a recursive walk would have to enumerate child folders; it never does
    assert queries[0].count("in parents") == 1


def test_known_issue_kb_indexing_walks_subfolders_with_depth_and_doc_limits():
    """known-issues.md: "Knowledge-base indexing is unaffected — it walks
    subfolders itself, up to a depth and document limit."
    """
    from src.data.kb import ingest

    assert isinstance(ingest.MAX_DEPTH, int) and ingest.MAX_DEPTH >= 1
    assert isinstance(ingest.MAX_DOCS_PER_JOB, int) and ingest.MAX_DOCS_PER_JOB >= 1

    class FakeReader:
        """A 5-deep folder chain with one file per level."""

        def list_changed_files(self, folder_id, **kwargs):
            return [{"id": f"file-{folder_id}", "name": f"doc {folder_id}"}]

        def list_folders(self, parent_id):
            depth = int(parent_id.split("-")[-1])
            return [{"id": f"folder-{depth + 1}"}] if depth < 5 else []

    files, truncated = ingest.enumerate_folder(FakeReader(), "folder-0")

    # It descends (more than the root's own file) but stops at MAX_DEPTH.
    assert len(files) > 1, "KB indexing did not descend into subfolders"
    assert len(files) == ingest.MAX_DEPTH + 1, (
        f"expected root + {ingest.MAX_DEPTH} levels, got {len(files)}")
    assert truncated is False

    # …and the document cap really truncates.
    capped, was_truncated = ingest.enumerate_folder(FakeReader(), "folder-0", cap=2)
    assert len(capped) == 2 and was_truncated is True


def test_known_issue_every_listed_defect_has_a_flagged_article(help_conn):
    """known-issues.md: "the Help Center marks affected articles with a badge
    and a banner, so the article you are reading tells you its own status" and
    "The badge is generated from the same information as this page, so the two
    should never disagree."
    """
    from src.data.help import store

    expected = {
        "settings-connect-asana": "partial",        # Asana cannot be connected
        "settings-test-connections": "partial",     # Test connections does nothing
        "workbench-publish-drive": "not-available",  # Drive publish writes nothing
        "plan-drag-reschedule": "flag-gated",       # React tabs never render
        "create-studio-no-screen": "partial",       # Content Studio has no screen
        "create-deferred-previews": "not-available",  # diagram/quiz/podcast
        "renn-background-research": "not-available",  # research does not exist
        "kb-searching": "partial",                  # multi-word search misses
    }
    for article_id, status in expected.items():
        art = store.get_article(help_conn, article_id)
        assert art is not None, f"{article_id} is missing from the corpus"
        assert art["status"] == status, (
            f"{article_id} is '{art['status']}', expected '{status}' — "
            "known-issues.md and the badge disagree")
        assert store.status_banner(art["status"]), (
            f"{article_id} carries a non-available status but renders no banner")


def test_known_issue_status_banner_is_derived_from_status_only(help_conn):
    """known-issues.md: "The badge is generated from the same information as
    this page, so the two should never disagree."

    A banner exists for every non-available status and for none other, and an
    unknown status is rejected on write rather than silently stored.
    """
    from src.data.help import store

    assert store.status_banner("available") == ""
    for status in ("partial", "flag-gated", "not-available"):
        assert store.status_banner(status), f"no banner for {status}"

    with pytest.raises(ValueError):
        store.upsert_article(help_conn, {"article_id": "x", "status": "mostly-fine"})


def test_troubleshooting_articles_all_load_with_a_valid_status(help_conn):
    """known-issues.md: the Help Center's status is data-driven — every
    troubleshooting article must load and carry a recognised status."""
    from src.data.help import store

    on_disk = {p.stem for p in TROUBLESHOOTING.glob("*.md")}
    assert len(on_disk) == 6, f"expected 6 troubleshooting articles, found {sorted(on_disk)}"

    loaded = store.list_articles(help_conn, section="troubleshooting")
    assert len(loaded) == 6, f"only {len(loaded)} troubleshooting articles loaded"
    for art in loaded:
        assert art["status"] in store.STATUSES
