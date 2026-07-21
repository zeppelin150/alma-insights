"""Accuracy audit of the in-app Help Center — section ``getting-started``.

Every test below settles ONE falsifiable claim made by one of the five
articles in ``assets/help/getting-started/``:

    what-this-is.md        (getting-started-what-this-is)
    first-15-minutes.md    (getting-started-first-15-minutes)
    reading-this-help.md   (getting-started-reading-this-help)
    demo-vs-live.md        (getting-started-demo-vs-live)
    the-surfaces.md        (getting-started-the-surfaces)

Each test docstring names the article and quotes (or closely paraphrases) the
claim it settles. Where the code does NOT do what the article says, the test
still asserts the ARTICLE's claim and is marked
``@pytest.mark.xfail(strict=True)`` with the real behaviour in the reason — so
the suite stays green while every discrepancy stays visible and tracked.

Headless: no network, no credentials, no QtWebEngine. The web tabs are forced
off (their shipped default) so no Chromium is ever constructed.
"""

from __future__ import annotations

import inspect
import os
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

pytestmark = pytest.mark.ui

_REPO = Path(__file__).resolve().parents[1]
_DEMO_DB_PATH = os.path.join(tempfile.gettempdir(), "alma_enablement_demo.db")

# Captured BEFORE the autouse fixture pins the web tabs off, so the flag test
# below can exercise the genuine implementation.
from src.ui.web import web_flags as _web_flags          # noqa: E402
_REAL_WEB_TABS_MODE = _web_flags.web_tabs_mode


# ── helpers ─────────────────────────────────────────────────────────

def _clicked_receivers(button) -> int:
    """How many slots are connected to a QAbstractButton's ``clicked``."""
    return button.receivers("2clicked(bool)")


def _find_button(root, text: str):
    """The first descendant QPushButton whose label is exactly ``text``."""
    for b in root.findChildren(QPushButton):
        if b.text().strip() == text:
            return b
    return None


def _label_texts(root) -> list[str]:
    return [lbl.text() for lbl in root.findChildren(QLabel)]


def _has_label_containing(root, needle: str) -> bool:
    return any(needle in t for t in _label_texts(root))


# ── fixtures ────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="module", autouse=True)
def _force_native_tabs():
    """Pin the web tabs to their shipped default so no QtWebEngine is built.

    ``page._make_calendar`` / ``_make_workbench`` import this function at call
    time, so patching the module attribute is enough.
    """
    _web_flags.web_tabs_mode = lambda: "off"
    yield
    _web_flags.web_tabs_mode = _REAL_WEB_TABS_MODE


@pytest.fixture(scope="module", autouse=True)
def _no_guru_credentials():
    """Isolate from dev-machine state: report Guru as unconfigured.

    Constructing an EnablementPage kicks off the attention queue's background
    health load, which builds a live GuruClient when credentials are present.
    Returning empty credentials makes that path raise ``_NotConnected`` before
    any network call, so this file is hermetic on any machine.
    """
    from src.data.guru_client import GuruClient
    original = GuruClient.load_credentials
    GuruClient.load_credentials = staticmethod(lambda: ("", ""))
    yield
    GuruClient.load_credentials = original


@pytest.fixture(scope="module")
def help_db(tmp_path_factory):
    """A warehouse with the real bundled help corpus loaded."""
    from src.data.db_manager import DatabaseManager
    from src.data.help import loader
    path = tmp_path_factory.mktemp("help") / "help.db"
    db = DatabaseManager(db_path=path)
    db.initialize()
    loader.load_bundled_help(db.conn)
    yield db
    try:
        db.conn.close()
    except Exception:
        pass


@pytest.fixture(scope="module")
def settings_page(qapp):
    """A standalone SettingsPage with NO host wired to its signals.

    The hosted copy inside EnablementPage routes its buttons into handlers that
    open modal dialogs and start network threads, so widget-level claims are
    settled against an unhosted instance: the wiring under test is the widget's
    own, and the assertions are the signals it emits.
    """
    from src.ui.pages.enablement.settings import SettingsPage
    yield SettingsPage()


@pytest.fixture(scope="module")
def demo_page(qapp):
    """An EnablementPage in demo mode (db=None → the throwaway demo DB)."""
    from src.ui.pages.enablement import EnablementPage
    page = EnablementPage(db=None, demo=True)
    page._greeting_sent = True      # never auto-dispatch a chat turn in tests
    yield page


@pytest.fixture(scope="module")
def live_page(qapp, tmp_path_factory):
    """An EnablementPage in live mode over an empty throwaway warehouse."""
    from src.data.db_manager import DatabaseManager
    from src.ui.pages.enablement import EnablementPage
    path = tmp_path_factory.mktemp("live") / "live.db"
    db = DatabaseManager(db_path=path)
    db.initialize()
    page = EnablementPage(db, demo=False)
    page._greeting_sent = True
    yield page
    try:
        db.conn.close()
    except Exception:
        pass


@pytest.fixture
def draft_conn(tmp_path):
    """A standalone warehouse connection for enablement_store draft tests."""
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager(db_path=tmp_path / "drafts.db")
    db.initialize()
    yield db.conn
    try:
        db.conn.close()
    except Exception:
        pass


class _SpyGuruClient:
    """Records every write the publish path would make against Guru."""

    def __init__(self):
        self.created = []
        self.updated = []

    def create_card(self, collection_id, title, html, folder_ids=None):
        self.created.append((collection_id, title, folder_ids))
        return {"id": "card-spy-1"}

    def update_card(self, card_id, html, title):
        self.updated.append((card_id, title))
        return {"id": card_id}


# ════════════════════════════════════════════════════════════════════
#  what-this-is.md — "What the enablement side is"
# ════════════════════════════════════════════════════════════════════

def test_new_drafts_require_human_approval_by_default(draft_conn):
    """ARTICLE getting-started-what-this-is: "Every publish is gated on a
    human." A freshly saved draft must carry the approval requirement."""
    from src.data import enablement_store as store
    draft_id = store.save_card_draft(
        draft_conn, title="SSO Setup", content="# SSO\n\nBody.")
    draft = store.get_draft(draft_conn, draft_id)
    assert draft["require_approval"] == 1
    assert draft["approved_at"] is None


def test_push_tool_refuses_an_unapproved_draft(draft_conn, monkeypatch):
    """ARTICLE getting-started-what-this-is: "There is no path where content
    reaches Guru without someone approving it." Renn's publish tool must
    refuse an un-signed-off draft and leave it unpublished."""
    from src.data import enablement_store as store
    from src.data.chat_tools import enablement_tools

    reached_publish = []
    monkeypatch.setattr(
        store, "publish_draft",
        lambda *a, **k: reached_publish.append(k) or {"ok": True})

    draft_id = store.save_card_draft(
        draft_conn, title="Refunds", content="# Refunds\n\nBody.")
    result = enablement_tools._push_guru_draft_impl(draft_conn, draft_id)

    assert result["ok"] is False
    assert result["error"] == "approval_required"
    assert reached_publish == [], "publish_draft ran despite no sign-off"
    assert store.get_draft(draft_conn, draft_id)["status"] != "pushed"


def test_approving_a_draft_clears_the_gate(draft_conn):
    """ARTICLE getting-started-what-this-is: the review step is what unblocks
    publish — approval must be recorded on the draft."""
    from src.data import enablement_store as store
    draft_id = store.save_card_draft(
        draft_conn, title="Payments", content="# Payments\n\nBody.")
    store.approve_draft(draft_conn, draft_id, approved_by="tester")
    draft = store.get_draft(draft_conn, draft_id)
    assert draft["approved_at"]
    assert draft["approved_by"] == "tester"


def test_effectiveness_is_measured_from_guru_views_and_comments(draft_conn):
    """ARTICLE getting-started-what-this-is: "effectiveness is tracked
    Guru-natively, using views and comment activity on the card itself" — a
    card with more views and fewer open comments must measure as improved."""
    from src.data.content_update import effectiveness_proxy

    class _Signals:
        def __init__(self, views, comments):
            self._views, self._comments = views, comments

        def card_view_counts(self):
            return {"card-1": self._views}

        def open_comment_count(self, card_id):
            return self._comments

    effectiveness_proxy.snapshot_baseline(
        draft_conn, _Signals(100, 6), "card-1", draft_id=None)
    result = effectiveness_proxy.measure_proxy(
        draft_conn, _Signals(180, 2), "card-1")

    assert result["ok"] is True
    assert result["views_delta"] == 80
    assert result["comments_resolved"] == 4


def test_effectiveness_proxy_reads_no_ticket_table(draft_conn):
    """ARTICLE getting-started-what-this-is: "It performs no ticket, PHI, or
    warehouse reads, ever." The effectiveness path must touch only its own
    enablement-local table."""
    from src.data.content_update import effectiveness_proxy

    class _Signals:
        def card_view_counts(self):
            return {"card-2": 5}

        def open_comment_count(self, card_id):
            return 1

    seen: list[str] = []
    draft_conn.set_trace_callback(seen.append)
    try:
        effectiveness_proxy.snapshot_baseline(
            draft_conn, _Signals(), "card-2", draft_id=None)
        effectiveness_proxy.measure_proxy(draft_conn, _Signals(), "card-2")
    finally:
        draft_conn.set_trace_callback(None)

    forbidden = ("tickets", "conversations", "comments", "ticket_index",
                 "nlp_ticket_classifications")
    hits = [sql for sql in seen
            if any(f" {t}" in sql.lower() or f"from {t}" in sql.lower()
                   for t in forbidden)]
    assert not hits, f"effectiveness path touched warehouse tables: {hits}"


def test_warehouse_tools_are_advertised_only_semantic_search_excluded(
        demo_page, monkeypatch):
    """ARTICLE getting-started-what-this-is (corrected): the separation from
    ticket/warehouse data is "a matter of practice, not enforcement — the
    warehouse tools remain wired". This pins that ACTUAL behaviour: enablement
    mode excludes only 'semantic_search', so the other warehouse tools stay in
    the surface. If a real allow-list is ever added, this test fails and the
    article should be upgraded to claim the enforcement it can then make."""
    from src.mcp.chat_mcp_server import TOOL_SCHEMAS
    from src.ui import app_modes

    monkeypatch.setattr(app_modes, "_current_mode", app_modes.MODE_ENABLEMENT)
    config = demo_page._build_mcp_config()
    env = {e["name"]: e["value"] for e in config[0]["env"]}
    excluded = {n.strip() for n in env.get("ALMA_MCP_EXCLUDE_TOOLS", "").split(",")
                if n.strip()}
    advertised = {t["name"] for t in TOOL_SCHEMAS} - excluded

    # semantic_search IS excluded; the rest of the warehouse surface is not.
    assert "semantic_search" not in advertised
    still_reachable = {"list_tickets", "query_stats", "read_thread"} & advertised
    assert still_reachable, (
        "the warehouse tools are no longer advertised — enablement now enforces "
        "the boundary the article describes as practice-only; upgrade the "
        "article to claim it")


def test_renn_tools_point_at_the_real_warehouse_in_live_mode(live_page):
    """ARTICLE getting-started-what-this-is (supporting evidence for the
    decoupling claim): in live mode Renn's tool process is pointed at the
    product warehouse database, not an enablement-only store."""
    assert live_page._engine_db_path() == str(live_page.db.db_path)
    assert live_page._engine_db_path() != _DEMO_DB_PATH


# ════════════════════════════════════════════════════════════════════
#  first-15-minutes.md — "Your first 15 minutes"
# ════════════════════════════════════════════════════════════════════

def test_providers_tab_has_guru_email_token_and_a_save_button(settings_page, monkeypatch):
    """ARTICLE getting-started-first-15-minutes: "There is a Guru section with
    an email field and an API token field, and a button to save both."""
    from src.data.guru_client import GuruClient
    panel = settings_page.credentials

    saved: list[tuple] = []
    monkeypatch.setattr(GuruClient, "save_credentials",
                        staticmethod(lambda e, t: saved.append((e, t)) or True))

    panel._guru_email.setText("ops@example.com")
    panel._guru_token.setText("token-123")
    button = _find_button(panel, "Save Guru credentials")
    assert button is not None, "no 'Save Guru credentials' button on Providers"
    button.click()

    assert saved == [("ops@example.com", "token-123")]


def test_settings_sub_tabs_are_named_as_the_article_says(settings_page):
    """ARTICLE getting-started-first-15-minutes: the article routes the reader
    to the Providers, Sources and Style Guide tabs of Settings."""
    tabs = settings_page._tabs
    names = [tabs.tabText(i) for i in range(tabs.count())]
    assert names == ["Connections", "Providers", "Sources", "Style Guide"]


def test_google_offers_both_a_service_account_file_and_a_per_user_connect(settings_page):
    """ARTICLE getting-started-first-15-minutes: "There are two separate
    mechanisms and they are not interchangeable" — a service-account
    credentials file and a per-user Google account connect button."""
    panel = settings_page.credentials
    assert hasattr(panel, "_sa_path"), "no service-account file field"
    connect = _find_button(panel, "Connect my Google account")
    assert connect is not None, "no per-user Google connect button"
    assert _clicked_receivers(connect) >= 1, "connect button is not wired"


def test_detect_email_control_exists_and_emits_on_the_providers_tab(settings_page):
    """ARTICLE getting-started-first-15-minutes: "go to the Providers tab and
    let the app detect your email automatically"."""
    seen: list[int] = []
    settings = settings_page
    settings.identity_detect_email_requested.connect(lambda: seen.append(1))
    button = _find_button(settings, "Auto-detect from Google")
    assert button is not None, "no auto-detect control on the Providers tab"
    button.click()
    assert seen, "the detect control does not request email detection"
    assert hasattr(settings, "_identity_email"), "no operator email field"


def test_sources_tab_warns_about_drive_read_access_and_folder_sharing(settings_page):
    """ARTICLE getting-started-first-15-minutes: "Drive reading also requires
    read access to be enabled in settings and each watched folder to be shared
    with the service-account address — the interface says so in a warning strip
    on the Sources tab"."""
    settings = settings_page
    assert _has_label_containing(settings, "drive.readonly")
    assert _has_label_containing(settings, "service-account email")
    assert _has_label_containing(settings, "read_enabled")


def test_style_guide_tab_carries_both_the_guide_and_the_template(settings_page):
    """ARTICLE getting-started-first-15-minutes: "You should also set your style
    guide and card template before generating anything. Both live on the Style
    Guide tab in Settings"."""
    tabs = settings_page._tabs
    style_tab = tabs.widget([tabs.tabText(i) for i in range(tabs.count())]
                            .index("Style Guide"))
    descendants = style_tab.findChildren(type(settings_page._sg_section))
    assert settings_page._sg_section in descendants, "style guide not on the tab"
    assert settings_page._ct_section in descendants, "card template not on the tab"
    assert _has_label_containing(style_tab, "STYLE GUIDE")
    assert _has_label_containing(style_tab, "CARD / ARTICLE TEMPLATE")


def test_guide_sections_accept_a_pasted_or_uploaded_document(qapp):
    """ARTICLE getting-started-first-15-minutes: "both accept a pasted or
    uploaded document"."""
    from src.ui.pages.enablement.settings import _GuideSection
    section = _GuideSection("STYLE GUIDE", "hint")
    actions: list[str] = []
    section.action.connect(actions.append)
    _find_button(section, "Paste…").click()
    _find_button(section, "Upload…").click()
    assert actions == ["paste", "upload"]


def test_connections_strip_shows_one_dot_per_service(settings_page):
    """ARTICLE getting-started-first-15-minutes: "the Connections strip at the
    top of Settings, which shows a dot per service"."""
    widgets = settings_page._conn_widgets
    assert set(widgets) == {"asana", "drive", "guru"}


def test_connection_dot_reflects_a_real_probe_result(settings_page):
    """ARTICLE getting-started-first-15-minutes: the dots "reflect the actual
    state, not what you typed" — a probe result must drive the label."""
    settings = settings_page
    settings.set_connection_status("guru", True)
    _dot, label, _name = settings._conn_widgets["guru"]
    assert label.text() == "Guru connected"
    settings.set_connection_status("guru", False, "auth failed")
    assert label.text() == "Guru auth failed"


def test_connection_probe_is_skipped_in_demo_mode(demo_page, monkeypatch):
    """ARTICLE getting-started-first-15-minutes: the dots are filled by a real
    connection test only in LIVE mode — "In demo mode, which is the shipped
    default, no probe ever runs: the connection test returns immediately."
    check_connections() short-circuits on demo (page.py:1265-1270)."""
    ran: list[int] = []
    monkeypatch.setattr(demo_page, "_check_connections_worker",
                        lambda: ran.append(1))
    demo_page.check_connections()
    assert ran == [], "a probe ran in demo mode — the article says it does not"


def test_connection_probe_runs_in_live_mode(live_page, monkeypatch):
    """ARTICLE getting-started-first-15-minutes (the half that holds): in live
    mode the connection check really does start a probe."""
    ran: list[int] = []
    monkeypatch.setattr(live_page, "_check_connections_worker",
                        lambda: ran.append(1))
    live_page.check_connections()
    for _ in range(200):                     # the probe runs on a daemon thread
        if ran:
            break
        time.sleep(0.01)
    assert ran, "live mode did not start a connection probe"


def test_returning_to_the_settings_tab_does_not_re_run_the_probe(live_page, monkeypatch):
    """ARTICLE getting-started-first-15-minutes: "There is no way to re-run the
    connection test from inside the app... leaving Settings and coming back only
    switches which tab is displayed." select_tab only calls setCurrentWidget
    (page.py:1086-1090); nothing re-invokes check_connections on tab change, so
    the only way to re-probe is to restart the app."""
    calls: list[int] = []
    monkeypatch.setattr(live_page, "check_connections", lambda: calls.append(1))
    # The Settings tab really does switch — proving the navigation happened
    # while still not re-probing.
    live_page.select_tab("calendar")
    live_page.select_tab("settings")
    assert live_page.tabs.currentWidget() is live_page._tab_widgets["settings"]
    assert calls == [], "returning to Settings re-tested — the article says it cannot"


def test_test_connections_button_is_not_wired_to_anything(settings_page):
    """ARTICLE getting-started-first-15-minutes: "The Test connections button
    appears to do nothing. It does nothing — it is not wired to any action."""
    button = _find_button(settings_page, "Test connections")
    assert button is not None, "the Test connections button is gone"
    assert _clicked_receivers(button) == 0, (
        "Test connections is now wired — the article says it is not")


def test_no_source_file_writes_an_asana_api_key():
    """ARTICLE getting-started-first-15-minutes: "that field is not connected
    to anything that saves it, and no other screen in the app writes an Asana
    key either"."""
    writers = []
    for path in (_REPO / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            if "asana_api_key" in line and "save_setting" in line:
                writers.append(f"{path.relative_to(_REPO)}: {line.strip()}")
    assert not writers, f"src/ writes an Asana key: {writers}"


def test_the_asana_key_field_is_not_bound_to_any_attribute(settings_page):
    """ARTICLE getting-started-first-15-minutes: "Pasting an Asana API key into
    the field on the Sources tab does nothing." The widget is a local, so the
    page keeps no handle on it and nothing can read it back."""
    settings = settings_page
    key_attrs = [name for name in vars(settings)
                 if "key" in name.lower() and "asana" in name.lower()]
    assert key_attrs == [], f"an Asana key widget is retained: {key_attrs}"


def test_asana_key_is_read_from_the_encrypted_credential_store(monkeypatch):
    """ARTICLE getting-started-first-15-minutes: "The app reads the key from
    its encrypted credential store, and a key put there another way works
    normally."""
    from src.data import asana_setup

    monkeypatch.setattr("src.data.pat_store.load_setting",
                        lambda key, default="": "pat-xyz" if key == "asana_api_key" else default)
    assert asana_setup.is_asana_connected() is True

    monkeypatch.setattr("src.data.pat_store.load_setting",
                        lambda key, default="": default)
    assert asana_setup.is_asana_connected() is False


def test_asana_discovery_falls_back_to_sample_data_without_a_key(monkeypatch):
    """ARTICLE getting-started-first-15-minutes: without a connected Asana key,
    "Set up with Renn" no longer silently saves a fabricated board. Discovery
    still returns the built-in sample projects so the flow is demonstrable, but
    it now TAGS the result ``mock=True`` — that flag is what lets setup decline
    to persist sample data outside demo mode."""
    from src.data import asana_setup
    monkeypatch.setattr("src.data.pat_store.load_setting",
                        lambda key, default="": default)
    result = asana_setup.discover()
    assert result["mock"] is True, "a keyless discovery must be flagged as sample data"
    names = [p["name"] for p in result["projects"]]
    assert "Enablement Requests" in names
    # The flag is stamped on a per-call copy; the shared constant stays clean.
    assert "mock" not in asana_setup.MOCK_DISCOVERY


def test_the_two_asana_boards_are_hardcoded_sample_rows(settings_page):
    """ARTICLE getting-started-first-15-minutes: "The Asana section shows two
    configured boards you have never seen... The board names, the field
    mappings, and the resolved assignee names in that section are all fixed
    sample text."""
    settings = settings_page
    assert _has_label_containing(settings, "ASANA BOARDS  ·  2 configured")
    assert _has_label_containing(settings, "Enablement Requests")
    assert _has_label_containing(settings, "Launch Coordination")
    assert _has_label_containing(settings, "J. Rivera")


def test_set_up_with_renn_button_asks_renn_rather_than_saving_the_key(settings_page):
    """ARTICLE getting-started-first-15-minutes: Asana setup is delegated to
    Renn — the button requests setup and does not persist the pasted key."""
    seen: list[int] = []
    settings_page.asana_setup_requested.connect(lambda: seen.append(1))
    button = _find_button(settings_page, "Set up with Renn")
    assert button is not None
    button.click()
    assert seen


# ════════════════════════════════════════════════════════════════════
#  reading-this-help.md — "How to read this Help Center"
# ════════════════════════════════════════════════════════════════════

def test_there_are_exactly_three_badges_with_the_documented_labels():
    """ARTICLE getting-started-reading-this-help: "There are three badges" —
    PARTIAL, OFF BY DEFAULT, NOT AVAILABLE YET."""
    from src.ui.pages.enablement.help_tab import _STATUS_BADGE
    labels = {status: badge[0] for status, badge in _STATUS_BADGE.items()}
    assert labels == {
        "partial": "PARTIAL",
        "flag-gated": "OFF BY DEFAULT",
        "not-available": "NOT AVAILABLE YET",
    }


def test_an_available_article_carries_no_badge_and_no_banner():
    """ARTICLE getting-started-reading-this-help: "An article with no badge is
    fully available."""
    from src.data.help import store
    from src.ui.pages.enablement.help_tab import _STATUS_BADGE
    assert "available" in store.STATUSES
    assert "available" not in _STATUS_BADGE
    assert store.status_banner("available") == ""
    for status in ("partial", "flag-gated", "not-available"):
        assert store.status_banner(status), f"{status} has no banner"


def test_the_badge_comes_from_article_metadata_not_prose(help_db):
    """ARTICLE getting-started-reading-this-help: "The badge comes from the
    article's own metadata" — the frontmatter status must reach the store."""
    from src.data.help import store
    article = store.get_article(help_db.conn, "getting-started-first-15-minutes")
    assert article is not None
    assert article["status"] == "partial"
    assert article["banner"] == store.status_banner("partial")


def test_an_unknown_status_is_rejected_rather_than_stored(help_db):
    """ARTICLE getting-started-reading-this-help: the badge "is what stops this
    Help Center from confidently instructing you to click something that cannot
    work" — a typo'd status must not silently become 'available'."""
    from src.data.help import store
    with pytest.raises(ValueError):
        store.upsert_article(help_db.conn, {
            "article_id": "bogus-article", "status": "mostly-works",
            "title": "x", "section": "y", "body": "z"})
    assert store.get_article(help_db.conn, "bogus-article") is None


def test_every_getting_started_article_declares_a_valid_status():
    """ARTICLE getting-started-reading-this-help: every article carries a status
    badge from a known vocabulary."""
    from src.data.help import loader, store
    directory = _REPO / "assets" / "help" / "getting-started"
    articles = [loader.parse_article(p) for p in sorted(directory.glob("*.md"))]
    assert len(articles) == 5
    for article in articles:
        assert article is not None
        assert article["status"] in store.STATUSES, article["article_id"]


def test_badged_articles_are_greyed_in_the_table_of_contents(qapp, help_db):
    """ARTICLE getting-started-reading-this-help: "Articles that carry a badge
    are also greyed in the table of contents"."""
    from PySide6.QtCore import Qt
    from src.ui.pages.enablement.help_tab import _ROLE_ID, _STATUS_BADGE, HelpTab
    from src.data.help import store

    tab = HelpTab(lambda: help_db.conn)
    statuses = {a["article_id"]: a["status"]
                for a in store.list_articles(help_db.conn)}

    grey, plain = [], []
    for i in range(tab._toc.topLevelItemCount()):
        parent = tab._toc.topLevelItem(i)
        for j in range(parent.childCount()):
            child = parent.child(j)
            article_id = child.data(0, _ROLE_ID)
            is_grey = child.foreground(0).color() == Qt.gray
            (grey if is_grey else plain).append(article_id)
            if statuses[article_id] in _STATUS_BADGE:
                assert is_grey, f"{article_id} is badged but not greyed"
                assert child.toolTip(0) == _STATUS_BADGE[statuses[article_id]][0]
            else:
                assert not is_grey, f"{article_id} is available but greyed"
    assert grey and plain, "expected a mix of badged and available articles"


def test_help_content_loads_itself_on_first_view(qapp, tmp_path):
    """ARTICLE getting-started-reading-this-help: "The content ships with the
    app and loads itself into the database on first view"."""
    from src.data.db_manager import DatabaseManager
    from src.data.help import store
    from src.ui.pages.enablement.help_tab import HelpTab

    db = DatabaseManager(db_path=tmp_path / "fresh.db")
    db.initialize()
    assert store.count_articles(db.conn) == 0, "corpus was pre-loaded"

    tab = HelpTab(lambda: db.conn)
    assert store.count_articles(db.conn) > 0, "the Help tab did not self-load"
    assert tab._toc.topLevelItemCount() > 0
    db.conn.close()


def test_search_ranks_a_whole_question_regardless_of_word_order(help_db):
    """ARTICLE getting-started-reading-this-help: "It breaks your question into
    words and ranks articles by title, summary, feature name and body, so word
    order and phrasing do not have to match."""
    from src.data.help import search
    a = search.search_help(help_db.conn, "how do I publish a card to Guru", limit=1)
    b = search.search_help(help_db.conn, "card to Guru publish how do I", limit=1)
    assert a and b
    assert a[0]["article_id"] == b[0]["article_id"]
    assert a[0]["score"] == b[0]["score"]


def test_search_finds_the_demo_mode_article_for_a_plain_question(help_db):
    """ARTICLE getting-started-reading-this-help: search is "built for whole
    questions" — a natural question must reach the right article."""
    from src.data.help import search
    hits = search.search_help(help_db.conn, "what is demo mode", limit=3)
    assert hits
    assert hits[0]["article_id"] == "getting-started-demo-vs-live"


def test_search_matches_word_prefixes_not_only_whole_words(help_db):
    """ARTICLE getting-started-reading-this-help: "It also matches on the start
    of a word, so a truncated query like 'publ card guru' still finds the
    publishing article." But a fragment from the MIDDLE of a word matches
    nothing. search._match_expr builds '"term"*' and _hits() accepts any token
    that startswith the term (search.py:97)."""
    from src.data.help import search
    whole = search.search_help(help_db.conn, "publish card guru", limit=1)
    truncated = search.search_help(help_db.conn, "publ card guru", limit=1)
    assert whole, "the whole-word query found nothing"
    assert truncated, "the truncated query found nothing — prefixes do not match"
    assert truncated[0]["article_id"] == whole[0]["article_id"], (
        "a truncated word reached a different article than its whole form")
    # A mid-word fragment is NOT a prefix of any token, so it must miss.
    mid_word = search.search_help(help_db.conn, "ublish", limit=1)
    assert mid_word == [], (
        f"a middle-of-word fragment matched {mid_word!r} — search is a substring "
        "match, not a prefix match")


def test_a_zero_hit_search_returns_nothing_and_leaves_the_toc_in_place(qapp, help_db):
    """ARTICLE getting-started-reading-this-help: "When a search finds nothing,
    the table of contents stays exactly where it was and a short line under the
    box tells you nothing matched; the article you were reading is left open."
    search_help returns [] on zero hits (search.py) and HelpTab._on_search only
    sets a status line — no section list is handed back."""
    from src.data.help import search
    from src.ui.pages.enablement.help_tab import HelpTab

    # The retrieval layer returns an empty list, not a fallback of sections.
    hits = search.search_help(help_db.conn, "zzzqqqnothingmatches", limit=5)
    assert hits == [], f"a zero-hit search returned something: {hits!r}"

    # The tab reflects that with a status line and an unchanged table of contents.
    tab = HelpTab(lambda: help_db.conn)
    tab.show_article("getting-started-demo-vs-live")
    toc_before = tab._toc.topLevelItemCount()
    tab._search.setText("zzzqqqnothingmatches")   # textChanged → _on_search

    assert "Nothing matched" in tab._status.text()
    assert tab._toc.topLevelItemCount() == toc_before, (
        "the table of contents was replaced on a zero-hit search")


def test_flag_a_bug_remembers_the_article_you_were_reading(qapp, help_db):
    """ARTICLE getting-started-reading-this-help: "Flag a bug ... remembers
    which article you were reading."""
    from src.ui.pages.enablement.help_tab import HelpTab
    tab = HelpTab(lambda: help_db.conn)
    seen: list[str] = []
    tab.flag_bug_requested.connect(seen.append)

    tab.show_article("getting-started-demo-vs-live")
    _find_button(tab, "Flag a bug").click()

    assert seen == ["getting-started-demo-vs-live"]


def test_flag_a_bug_says_so_when_no_form_is_configured(demo_page, monkeypatch):
    """ARTICLE getting-started-reading-this-help: "Flag a bug says no form has
    been configured. Expected on a fresh install."""
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr("src.data.settings_manager.get_section",
                        lambda name, default=None: {} if name == "enablement" else (default or {}))
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: shown.append(a[2])))
    opened: list[str] = []
    monkeypatch.setattr("PySide6.QtGui.QDesktopServices.openUrl",
                        staticmethod(lambda url: opened.append(url.toString())))

    demo_page._open_bug_form("getting-started-what-this-is")

    assert shown and "No bug-report form has been configured" in shown[0]
    assert opened == [], "a bug form was opened despite no configured URL"


def test_flag_a_bug_opens_the_form_unmodified_in_the_system_browser(demo_page, monkeypatch):
    """ARTICLE getting-started-reading-this-help: "It opens your team's bug
    report form in your normal browser, outside the app" — and "The app does
    not attach logs, screenshots, or document text to a report", so the URL is
    handed over exactly as configured, carrying no payload."""
    monkeypatch.setattr(
        "src.data.settings_manager.get_section",
        lambda name, default=None: ({"help": {"bug_form_url": "https://forms.example/bug"}}
                                    if name == "enablement" else (default or {})))
    opened: list[str] = []
    monkeypatch.setattr("PySide6.QtGui.QDesktopServices.openUrl",
                        staticmethod(lambda url: opened.append(url.toString())))

    demo_page._open_bug_form("getting-started-what-this-is")

    assert opened == ["https://forms.example/bug"]
    assert "?" not in opened[0] and "#" not in opened[0], (
        "the app appended context to the bug-report URL")


def test_flag_a_bug_refuses_a_non_web_form_url(demo_page, monkeypatch):
    """ARTICLE getting-started-reading-this-help: bug reporting leaves the app
    — a non-web target must not be launched."""
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        "src.data.settings_manager.get_section",
        lambda name, default=None: ({"help": {"bug_form_url": "file:///C:/evil.exe"}}
                                    if name == "enablement" else (default or {})))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    opened: list[str] = []
    monkeypatch.setattr("PySide6.QtGui.QDesktopServices.openUrl",
                        staticmethod(lambda url: opened.append(url.toString())))

    demo_page._open_bug_form("")

    assert opened == []


# ════════════════════════════════════════════════════════════════════
#  demo-vs-live.md — "Demo mode and live mode"
# ════════════════════════════════════════════════════════════════════

def test_demo_mode_is_the_constructed_default():
    """ARTICLE getting-started-demo-vs-live: "Demo mode is the default. If
    nobody has switched it off, you are in it."""
    from src.ui.pages.enablement import EnablementPage
    demo_param = inspect.signature(EnablementPage.__init__).parameters["demo"]
    assert demo_param.default is True


def test_demo_mode_is_the_default_when_the_setting_is_absent(monkeypatch):
    """ARTICLE getting-started-demo-vs-live: with no demo_mode key present the
    app must behave as if demo mode is on."""
    from src.data.chat_tools import kb_tools
    monkeypatch.setattr("src.data.settings_manager.get_section",
                        lambda name, default=None: {} if name == "enablement" else (default or {}))
    blocked = kb_tools._kb_precheck(conn=None)
    assert blocked is not None
    assert blocked["error"] == "demo_mode"


def test_demo_header_shows_the_demo_label_and_a_demo_scan_button(demo_page):
    """ARTICLE getting-started-demo-vs-live: "Demo mode shows a demo label
    beside the page title, the status line offers to pull documents and draft
    cards, and the scan button is labelled as a demo scan."""
    assert _has_label_containing(demo_page, "DEMO")
    assert "Demo mode" in demo_page._status.text()
    assert "pull docs and draft cards" in demo_page._status.text()
    assert _find_button(demo_page, "Scan all (demo)") is not None


def test_live_header_drops_the_label_and_the_button_simply_scans(live_page):
    """ARTICLE getting-started-demo-vs-live: "Live mode drops the label and the
    button simply scans."

    Note: the live header's opening status text ("Live mode — watching your
    configured sources.") is immediately overwritten by ``_load_live``, so the
    assertion here is that nothing in the live header announces demo mode.
    """
    assert not _has_label_containing(live_page, "DEMO")
    assert "demo" not in live_page._status.text().lower()
    assert _find_button(live_page, "Scan now") is not None
    assert _find_button(live_page, "Scan all (demo)") is None


def test_demo_work_goes_to_a_throwaway_database_in_the_temp_directory(demo_page):
    """ARTICLE getting-started-demo-vs-live: "Demo work is written to a separate
    database file in your system temp directory, not to the warehouse."""
    db = demo_page._ensure_demo_db()
    path = str(db.db_path)
    assert os.path.dirname(path) == tempfile.gettempdir()
    assert demo_page._conn() is db.conn


def test_the_demo_database_is_rebuilt_from_scratch_each_session(qapp, tmp_path,
                                                                monkeypatch):
    """ARTICLE getting-started-demo-vs-live: "It is deleted and rebuilt from
    scratch at the start of every session, so nothing you do in demo mode
    survives a restart."

    The temp directory is redirected so the assertion is about the code and not
    about whichever process happens to hold the shared demo file.
    """
    from src.ui.pages.enablement import EnablementPage
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    seeded = tmp_path / "alma_enablement_demo.db"
    scratch = sqlite3.connect(seeded)
    scratch.execute("CREATE TABLE survivor (x INTEGER)")
    scratch.execute("INSERT INTO survivor VALUES (1)")
    scratch.commit()
    scratch.close()

    page = EnablementPage(db=None, demo=True)
    page._greeting_sent = True
    db = page._ensure_demo_db()

    assert str(db.db_path) == str(seeded), "the demo DB moved off the temp path"
    rows = db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='survivor'"
    ).fetchall()
    assert rows == [], "demo data survived a new session"


def test_the_demo_database_lives_at_one_fixed_shared_path(demo_page):
    """ARTICLE getting-started-demo-vs-live: "a separate database file in your
    system temp directory".

    The path is a fixed constant, not a per-session temp file — which is what
    makes the session wipe a best-effort ``try/except OSError`` (see
    page.py:424-428): another process holding the file silently defeats it.
    """
    assert demo_page._engine_db_path() == _DEMO_DB_PATH
    assert Path(_DEMO_DB_PATH).name == "alma_enablement_demo.db"


def test_demo_publish_is_given_no_guru_client(demo_page):
    """ARTICLE getting-started-demo-vs-live: "No Guru client is handed to the
    publish path... the code that talks to Guru is simply not given a
    connection."""
    demo_page._guru_client = _SpyGuruClient()
    assert demo_page._guru_for_push() is None


def test_live_publish_is_given_the_real_guru_client(live_page):
    """ARTICLE getting-started-demo-vs-live: the demo suppression is what makes
    demo safe — live mode must still hand the client over."""
    client = _SpyGuruClient()
    live_page._guru_client = client
    assert live_page._guru_for_push() is client


def test_publishing_without_a_client_never_calls_guru(draft_conn):
    """ARTICLE getting-started-demo-vs-live: "The draft is marked as published
    locally and the API is never called."""
    from src.data import enablement_store as store

    local_id = store.save_card_draft(draft_conn, title="Local", content="body")
    result = store.publish_draft(draft_conn, local_id, guru_client=None,
                                 collection_id="coll-enablement")
    assert result["ok"] is True
    assert store.get_draft(draft_conn, local_id)["status"] == "pushed"

    # Same call WITH a client proves the API would otherwise be hit.
    spy = _SpyGuruClient()
    live_id = store.save_card_draft(draft_conn, title="Live", content="body")
    store.publish_draft(draft_conn, live_id, guru_client=spy,
                        collection_id="coll-enablement")
    assert spy.created, "the spy client was never called — test is inert"


def test_demo_publish_reports_a_collection_and_appends_a_demo_note(demo_page):
    """ARTICLE getting-started-demo-vs-live: "a successful demo publish reports
    the draft as published to a named collection... The status line appends a
    demo note to make that clear."""
    from src.data import enablement_store as store
    conn = demo_page._conn()
    draft_id = store.save_card_draft(conn, title="Demo card", content="body")
    demo_page._guru_client = _SpyGuruClient()

    demo_page._on_push(draft_id)

    assert "(demo)" in demo_page._status.text()
    assert f"Draft {draft_id} published" in demo_page._status.text()
    assert store.get_draft(conn, draft_id)["status"] == "pushed"
    assert not demo_page._guru_client.created, "demo publish called Guru"
    assert not demo_page._guru_client.updated, "demo publish called Guru"


def test_no_publish_path_can_reach_guru_while_demo_mode_is_on(draft_conn, monkeypatch):
    """ARTICLE getting-started-demo-vs-live: "There is no configuration mistake
    that can make a demo publish reach your real Guru, because there is nothing
    to reach it with."""
    from src.data import enablement_store as store
    from src.data.chat_tools import enablement_tools
    from src.data.guru_client import GuruClient

    monkeypatch.setattr("src.data.settings_manager.get_section",
                        lambda name, default=None: ({"demo_mode": True}
                                                    if name == "enablement" else (default or {})))
    monkeypatch.setattr(GuruClient, "load_credentials",
                        staticmethod(lambda: ("ops@example.com", "real-token")))

    handed: list = []
    monkeypatch.setattr(
        store, "publish_draft",
        lambda conn, did, **kw: handed.append(kw.get("guru_client")) or {"ok": True})

    draft_id = store.save_card_draft(draft_conn, title="Card", content="body")
    store.approve_draft(draft_conn, draft_id)
    enablement_tools._push_guru_draft_impl(draft_conn, draft_id)

    assert handed, "the publish path was never reached — test is inert"
    assert handed[0] is None, (
        f"demo mode handed a live Guru client to the publish path: {handed[0]!r}")


def test_background_work_stays_off_in_demo_mode(monkeypatch):
    """ARTICLE getting-started-demo-vs-live: "Asana sync, the daily briefs, and
    the knowledge base do not run while demo mode is on."""
    from types import SimpleNamespace
    from src.ui.main_window import MainWindow

    monkeypatch.setattr("src.data.settings_manager.get_section",
                        lambda name, default=None: ({"demo_mode": True}
                                                    if name == "enablement" else (default or {})))
    host = SimpleNamespace(db=None, guru_page=None)
    MainWindow._wire_enablement_monitor(host)
    assert getattr(host, "_enablement_monitor", None) is None


def test_kb_tools_decline_while_demo_mode_is_on(monkeypatch, draft_conn):
    """ARTICLE getting-started-demo-vs-live: "Renn refuses a knowledge base
    action and mentions demo mode... The knowledge base tools decline while
    demo mode is on rather than writing into a throwaway folder."""
    from src.data.chat_tools import kb_tools
    monkeypatch.setattr("src.data.settings_manager.get_section",
                        lambda name, default=None: ({"demo_mode": True}
                                                    if name == "enablement" else (default or {})))
    result = kb_tools.handle_index_drive_folder(
        draft_conn, {"folder_id": "folder-1"}, {})
    assert result["ok"] is False
    assert result["error"] == "demo_mode"
    assert "demo mode" in result["message"].lower()


def test_settings_lists_demo_mode_first_among_inactive_reasons(settings_page, monkeypatch):
    """ARTICLE getting-started-demo-vs-live: "The Settings page lists this
    explicitly among the reasons a background capability is inactive" and
    "Demo mode is the first reason it checks for."""
    monkeypatch.setattr("src.data.settings_manager.get_section",
                        lambda name, default=None: ({"demo_mode": True}
                                                    if name == "enablement" else (default or {})))
    settings_page.refresh_kb_status()
    text = settings_page._kb_status.text()
    lines = [ln for ln in text.splitlines() if ln.strip().startswith("•")]
    assert lines, f"no inactive-capability reasons rendered: {text!r}"
    assert "Demo mode is ON" in lines[0]


def test_chat_tools_follow_the_demo_database(demo_page):
    """ARTICLE getting-started-demo-vs-live: "Renn's tools read and write the
    demo database too"."""
    assert demo_page._engine_db_path() == _DEMO_DB_PATH
    config = demo_page._build_mcp_config()
    env = {e["name"]: e["value"] for e in config[0]["env"]}
    assert env["ALMA_DB_PATH"] == _DEMO_DB_PATH


def test_a_demo_scan_seeds_tasks_and_produces_real_drafts(draft_conn):
    """ARTICLE getting-started-demo-vs-live: "Scanning in demo mode seeds a set
    of sample tasks and runs a simulation that produces real drafts you can
    open, edit, diff, and publish."""
    from src.data import enablement_store as store
    from src.data.enablement_sim import run_simulation, seed_demo_tasks

    summary = run_simulation(draft_conn, publish=False)
    tasks = seed_demo_tasks(draft_conn)
    drafts = store.list_drafts(draft_conn, status="pending")

    assert summary["documents"], "no documents were indexed"
    assert summary["drafts"], "no drafts were generated"
    assert summary["published"] == [], "a demo scan published something"
    assert tasks, "no demo tasks were seeded"
    assert drafts and drafts[0]["content"], "drafts have no body to review"


# ════════════════════════════════════════════════════════════════════
#  the-surfaces.md — "Where everything lives"
# ════════════════════════════════════════════════════════════════════

def test_the_enablement_sidebar_has_home_plus_ten_entries():
    """ARTICLE getting-started-the-surfaces: "The enablement sidebar opens with
    Home, then ten more entries"."""
    from src.ui import app_modes
    pages = app_modes.pages_for_mode(app_modes.MODE_ENABLEMENT)
    visible = [p for p in pages if not p.hidden]
    home = [p for p in visible if p.page_id == "home"]
    assert len(home) == 1
    assert len(visible) - 1 == 10, [p.page_id for p in visible]


def test_home_sits_above_the_bands_with_no_heading_of_its_own():
    """ARTICLE getting-started-the-surfaces: "Home sits above the bands and has
    no band heading of its own."""
    from src.ui import app_modes
    spec = app_modes.spec_for("home")
    assert spec is not None
    assert spec.section == ""
    assert app_modes.MODE_ENABLEMENT in spec.modes
    assert app_modes.MODE_PRODUCT in spec.modes


def test_home_is_the_page_the_app_opens_on_in_both_modes():
    """ARTICLE getting-started-the-surfaces: "It is the page the app opens on in
    both product and enablement mode"."""
    from src.ui import app_modes
    assert app_modes.first_page_id(app_modes.MODE_PRODUCT) == "home"
    assert app_modes.first_page_id(app_modes.MODE_ENABLEMENT) == "home"


def test_the_ten_entries_are_grouped_into_the_five_documented_bands():
    """ARTICLE getting-started-the-surfaces: the entries are "grouped into five
    bands" — Assistant / Plan / Create / Insights / System, with the membership
    the article lists."""
    from src.ui import app_modes
    bands: dict[str, list[str]] = {}
    for spec in app_modes.pages_for_mode(app_modes.MODE_ENABLEMENT):
        if spec.page_id == "home" or spec.hidden:
            continue
        bands.setdefault(spec.section, []).append(spec.title)
    assert bands == {
        "ASSISTANT": ["Agent"],
        "PLAN": ["Calendar", "Tasks"],
        "CREATE": ["Workbench", "PowerPoint", "Zendesk"],
        "INSIGHTS": ["Attention Queue", "Guru Analytics"],
        "SYSTEM": ["Help", "Settings"],
    }


def test_every_sidebar_tab_key_resolves_to_a_real_tab(demo_page):
    """ARTICLE getting-started-the-surfaces: "A sidebar entry opens a blank
    page... Blank is not a documented state for any of the ten." Every entry's
    tab key must select a real, non-empty tab."""
    from src.ui import app_modes
    for spec in app_modes.pages_for_mode(app_modes.MODE_ENABLEMENT):
        if not spec.tab_key:
            continue
        assert spec.tab_key in demo_page._tab_widgets, spec.page_id
        demo_page.select_tab(spec.tab_key)
        current = demo_page.tabs.currentWidget()
        assert current is demo_page._tab_widgets[spec.tab_key]
        assert current.findChildren(QLabel) or current.findChildren(QPushButton), (
            f"{spec.page_id} renders an empty tab")


def test_agent_is_the_one_entry_that_is_not_a_page_of_the_enablement_screen():
    """ARTICLE getting-started-the-surfaces: "Nine of the ten entries — everything
    except Agent — select one page of a single enablement screen... Agent is the
    exception. It is not a page of that screen; it is a separate full-screen
    surface." Agent's spec uses factory '_create_agent_page' with tab_key None
    (app_modes.py:79-80); the other nine share '_create_enablement_page' + a
    tab_key."""
    from src.ui import app_modes
    outside = []
    for spec in app_modes.pages_for_mode(app_modes.MODE_ENABLEMENT):
        if spec.page_id == "home":
            continue
        if spec.factory != "_create_enablement_page" or not spec.tab_key:
            outside.append(spec.page_id)

    # Exactly one entry is outside the shared screen, and it is Agent.
    assert outside == ["en_agent"], (
        f"expected only Agent to sit outside the enablement screen, got {outside}")

    agent = app_modes.spec_for("en_agent")
    assert agent.factory == "_create_agent_page"
    assert not agent.tab_key, "Agent unexpectedly became a tab of the screen"


def test_the_attention_queue_is_the_landing_tab_of_the_workspace(demo_page):
    """ARTICLE getting-started-the-surfaces: "It is the default tab within the
    enablement workspace, so it is what you land on when you first move off
    Home." And: "It is the Insights entry in the sidebar."""
    from src.ui import app_modes
    spec = app_modes.spec_for("en_attention")
    assert spec is not None
    assert spec.section == "INSIGHTS"
    assert spec.tab_key == "home"

    landing = demo_page._tab_widgets["home"]
    assert demo_page.attention in landing.findChildren(type(demo_page.attention)) \
        or landing.widget() is demo_page.attention, (
        "the 'home' tab is not the attention queue")
    # A freshly built page lands on it.
    fresh_index = demo_page.tabs.indexOf(landing)
    assert fresh_index == 0, "the attention queue is not the first tab"


def test_the_attention_queue_shows_the_four_documented_buckets():
    """ARTICLE getting-started-the-surfaces: the queue lists "source-changed
    cards, overdue verifications, gaps and duplicates, and drafts you left
    staged"."""
    from src.ui.pages.enablement.attention_queue_tab import _BUCKETS, group_by_bucket
    keys = [key for key, _heading, _label, _kind in _BUCKETS]
    assert keys == ["source_changed", "verification_overdue", "gap_dup", "staged"]
    assert set(group_by_bucket([])) == set(keys)


def test_the_attention_queue_ranks_worst_first():
    """ARTICLE getting-started-what-this-is: "the affected cards are ranked so
    you work on the ones that matter first. That ranking is what the Attention
    Queue shows you."""
    from types import SimpleNamespace
    from src.ui.pages.enablement.attention_queue_tab import group_by_bucket
    cards = [SimpleNamespace(bucket="source_changed", score=0.9, card_id="a"),
             SimpleNamespace(bucket="source_changed", score=0.2, card_id="b"),
             SimpleNamespace(bucket="source_changed", score=0.55, card_id="c")]
    ordered = [c.card_id for c in group_by_bucket(cards)["source_changed"]]
    assert ordered == ["b", "c", "a"]


def test_home_carries_mode_tiles_quick_actions_and_recent_activity(qapp, tmp_path):
    """ARTICLE getting-started-the-surfaces: Home "carries the mode tiles, a few
    at-a-glance counts, quick actions, and your recent activity"."""
    from src.data.db_manager import DatabaseManager
    from src.ui import app_modes
    from src.ui.pages.home_page import HomePage

    db = DatabaseManager(db_path=tmp_path / "home.db")
    db.initialize()
    page = HomePage(db, current_mode=app_modes.MODE_ENABLEMENT)

    assert set(page._tiles) == {app_modes.MODE_PRODUCT, app_modes.MODE_ENABLEMENT}, (
        f"Home does not carry both mode tiles: {list(page._tiles)}")

    seen: list[str] = []
    page.quick_action.connect(seen.append)
    assert page._qa_buttons, "Home has no quick-action chips"
    page._qa_buttons[0].click()
    assert seen, "a Home quick action emitted nothing"

    assert page._recent_activity() == [], "empty warehouse should have no activity"
    assert _has_label_containing(page, "No recent activity")
    db.conn.close()


def test_the_workbench_has_its_own_assistant_panel(demo_page):
    """ARTICLE getting-started-the-surfaces: "The Workbench has its own
    assistant panel that picks up whatever draft you are editing"."""
    from src.ui.pages.enablement.chat_panel import ChatPanel
    assert isinstance(demo_page.chat, ChatPanel)
    assert demo_page.workbench.receivers("2open_chat_requested()") >= 1


def test_the_content_studio_has_no_sidebar_entry_and_no_tab(demo_page):
    """ARTICLE getting-started-the-surfaces: "The Content Studio has no
    screen... there is no sidebar entry and no tab for them."""
    from src.ui import app_modes
    titles = {s.title.lower() for s in app_modes.PAGES}
    assert not any("studio" in t for t in titles)
    assert not any("studio" in key for key in demo_page._tab_widgets)
    tab_titles = {demo_page.tabs.tabText(i).lower()
                  for i in range(demo_page.tabs.count())}
    assert not any("studio" in t or "artifact" in t for t in tab_titles)


def test_the_studio_exists_only_as_renn_tools():
    """ARTICLE getting-started-the-surfaces: "Diagram, quiz, one-pager,
    battle-card and deck generation exist as things Renn can do, and generated
    artifacts are listed and attached through Renn as well"."""
    from src.mcp.chat_mcp_server import TOOL_SCHEMAS
    names = {t["name"] for t in TOOL_SCHEMAS}
    for tool in ("generate_diagram", "generate_quiz", "generate_doc",
                 "generate_deck", "list_artifacts"):
        assert tool in names, f"{tool} is not a Renn tool"


def test_the_podcast_artifact_kind_is_reserved_with_no_implementation():
    """ARTICLE getting-started-the-surfaces: "a podcast artifact type is
    reserved in the data model with no implementation behind it at all —
    asking for a podcast produces nothing."""
    from src.data import artifact_store
    from src.mcp.chat_mcp_server import TOOL_SCHEMAS
    assert "podcast" in artifact_store.KINDS
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert not [n for n in names if "podcast" in n], "a podcast tool now exists"


def test_diagram_and_quiz_previews_are_not_implemented():
    """ARTICLE getting-started-the-surfaces: "Mermaid diagram previews and quiz
    previews are deferred, so generation is ahead of the ability to see the
    result in the app."

    Three independent checks: no mermaid renderer is bundled, no web component
    mentions mermaid, and no UI module reads the artifact store (the only place
    a generated diagram or quiz could be surfaced from).
    """
    import json
    package = json.loads((_REPO / "web" / "package.json").read_text(encoding="utf-8"))
    deps = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
    assert not [d for d in deps if "mermaid" in d.lower()], (
        f"a mermaid renderer is now bundled: {sorted(deps)}")

    web_hits = [str(p.relative_to(_REPO))
                for p in (_REPO / "web" / "src").rglob("*")
                if p.suffix.lower() in (".js", ".jsx", ".ts", ".tsx")
                and "mermaid" in p.read_text(encoding="utf-8", errors="ignore").lower()]
    assert not web_hits, f"a web mermaid preview now exists: {web_hits}"

    ui_hits = [str(p.relative_to(_REPO))
               for p in (_REPO / "src" / "ui").rglob("*.py")
               if "artifact_store" in p.read_text(encoding="utf-8", errors="ignore")]
    assert not ui_hits, f"a UI surface now reads generated artifacts: {ui_hits}"


def test_the_web_calendar_and_workbench_ship_switched_off(monkeypatch):
    """ARTICLE getting-started-the-surfaces: "There are two implementations of
    both, and the newer web versions are behind a setting that ships off"."""
    monkeypatch.setattr("src.data.settings_manager.get_section",
                        lambda name, default=None: {} if name == "enablement" else (default or {}))
    assert _REAL_WEB_TABS_MODE() == "off", "an absent setting must mean off"

    monkeypatch.setattr(
        "src.data.settings_manager.get_section",
        lambda name, default=None: ({"web_tabs": "nonsense"}
                                    if name == "enablement" else (default or {})))
    assert _REAL_WEB_TABS_MODE() == "off", "an unknown value must fail safe"

    monkeypatch.setattr(
        "src.data.settings_manager.get_section",
        lambda name, default=None: ({"web_tabs": "all"}
                                    if name == "enablement" else (default or {})))
    assert _REAL_WEB_TABS_MODE() == "all", "the flag is inert — test is meaningless"


def test_the_shipped_settings_file_does_not_enable_the_web_tabs():
    """ARTICLE getting-started-the-surfaces: the web versions are off unless
    someone turns them on — the settings file must not carry an override."""
    import yaml
    path = _REPO / "data" / "settings.yaml"
    if not path.exists():
        pytest.skip("no local settings.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    enablement = data.get("enablement") or {}
    assert enablement.get("web_tabs", "off") == "off"


def test_the_calendar_and_workbench_tabs_are_the_native_widgets(demo_page):
    """ARTICLE getting-started-the-surfaces: "you are almost certainly seeing
    the standard versions" — with the flag off, the native Qt pages render."""
    from src.ui.pages.enablement.calendar import CalendarPage
    from src.ui.pages.enablement.workbench import WorkbenchPage
    assert isinstance(demo_page.calendar, CalendarPage)
    assert isinstance(demo_page.workbench, WorkbenchPage)
