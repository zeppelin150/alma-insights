"""Accuracy audit — in-app Help Center, section ``settings`` (8 articles).

Every test in this file settles ONE falsifiable claim made by an article in
``assets/help/settings/``. Each test names its article id and quotes (or closely
paraphrases) the claim it settles.

Every article claim here has been reconciled against the code: each test asserts
the ACTUAL behaviour the article now documents, so the file is a live guard that
would fail loudly if either the code or the article drifted from the truth.

Articles covered:
  settings-overview          overview.md
  settings-connect-guru      connect-guru.md
  settings-connect-asana     connect-asana.md
  settings-connect-google    connect-google.md
  settings-test-connections  test-connections.md
  settings-identity          identity.md
  settings-ai-provider       ai-provider.md
  settings-guides            guides.md

Headless: no network, no credentials, no QtWebEngine. Settings and keyring
access are faked so the tests never read or write the developer's real config.
"""

from __future__ import annotations

import os
import types
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QCheckBox, QComboBox, QLabel, QLineEdit, QPushButton,
)

from src.ui.pages.enablement.page import EnablementPage  # noqa: E402
from src.ui.pages.enablement.settings import SettingsPage, _GuideSection  # noqa: E402
from src.ui.widgets.credentials_panel import CredentialsPanel  # noqa: E402


# ── infrastructure ───────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    """One QApplication for the module (offscreen; never screenshotted)."""
    app = QApplication.instance() or QApplication([])
    yield app


class FakeSettings:
    """In-memory stand-in for settings_manager, so no test touches the real
    ``data/settings.yaml``."""

    def __init__(self, initial=None):
        self.store = {k: dict(v) for k, v in (initial or {}).items()}

    def get_section(self, section, default=None):
        val = self.store.get(section)
        if val is None:
            return {} if default is None else default
        return dict(val)

    def set_section(self, section, value):
        self.store[section] = dict(value or {})
        return True

    def update_section(self, section, updates):
        cur = dict(self.store.get(section) or {})
        cur.update(updates or {})
        self.store[section] = cur
        return True


@pytest.fixture
def settings(monkeypatch):
    """Patch every settings entry point the settings surfaces use."""
    fake = FakeSettings()
    import src.data.enablement_identity as ident_mod
    import src.data.settings_manager as sm

    monkeypatch.setattr(sm, "get_section", fake.get_section)
    monkeypatch.setattr(sm, "set_section", fake.set_section)
    monkeypatch.setattr(sm, "update_section", fake.update_section)
    # enablement_identity binds these at module import time.
    monkeypatch.setattr(ident_mod, "get_section", fake.get_section)
    monkeypatch.setattr(ident_mod, "update_section", fake.update_section)
    return fake


@pytest.fixture
def fake_keyring(monkeypatch):
    """Patch pat_store so no test reads or writes the real OS credential vault."""
    import src.data.pat_store as pat_store

    store: dict[str, str] = {}
    monkeypatch.setattr(pat_store, "load_setting",
                        lambda key, default=None: store.get(key, default if default is not None else ""))
    monkeypatch.setattr(pat_store, "save_setting",
                        lambda key, value: (store.__setitem__(key, value), True)[1])
    return store


@pytest.fixture
def no_google(monkeypatch):
    """Google reported as neither stored nor active (deterministic card state)."""
    from src.data import google_oauth

    monkeypatch.setattr(google_oauth, "has_stored_credentials", lambda: False)
    monkeypatch.setattr(google_oauth, "is_active", lambda: False)
    return google_oauth


@pytest.fixture
def page(qapp, settings, fake_keyring, no_google):
    """A real enablement SettingsPage with faked settings/keyring/Google."""
    return SettingsPage()


# ── widget-tree helpers ──────────────────────────────────────────────

def tab_names(p: SettingsPage) -> list[str]:
    return [p._tabs.tabText(i) for i in range(p._tabs.count())]


def tab(p: SettingsPage, name: str):
    for i in range(p._tabs.count()):
        if p._tabs.tabText(i) == name:
            return p._tabs.widget(i)
    raise AssertionError(f"no sub-tab named {name!r}; have {tab_names(p)}")


def label_texts(w) -> list[str]:
    return [lbl.text() for lbl in w.findChildren(QLabel)]


def button_texts(w) -> list[str]:
    return [b.text() for b in w.findChildren(QPushButton)]


def button(w, text: str) -> QPushButton | None:
    for b in w.findChildren(QPushButton):
        if b.text() == text:
            return b
    return None


def clicked_receivers(b: QPushButton) -> int:
    """Number of slots connected to a button's clicked signal."""
    return max(b.receivers("2clicked()"), b.receivers("2clicked(bool)"))


def combo_with(w, *values) -> QComboBox | None:
    """The first combo box carrying all of the given itemData values."""
    for c in w.findChildren(QComboBox):
        data = {c.itemData(i) for i in range(c.count())}
        if set(values).issubset(data):
            return c
    return None


# ══════════════════════════════════════════════════════════════════════
# settings-overview — "The four Settings sub-tabs at a glance"
# ══════════════════════════════════════════════════════════════════════

class TestOverviewTabs:

    def test_settings_has_exactly_four_named_sub_tabs(self, page):
        """settings-overview: 'one page with four sub-tabs' named Connections,
        Providers, Sources and Style Guide."""
        assert tab_names(page) == ["Connections", "Providers", "Sources", "Style Guide"]

    def test_connections_tab_holds_dots_provider_and_startup_mode(self, page):
        """settings-overview: Connections = status dots for Asana/Drive/Guru,
        the AI provider choice, and which mode the app starts in."""
        w = tab(page, "Connections")
        texts = label_texts(w)
        assert "Asana —" in texts and "Google Drive —" in texts and "Guru —" in texts
        assert combo_with(w, "gemini", "claude") is not None, "no provider combo"
        assert combo_with(w, "product", "enablement", "last") is not None, "no start-in combo"

    def test_providers_tab_holds_identity_and_the_credentials_panel(self, page):
        """settings-overview: Providers = 'your identity, plus the full
        credentials panel — model, API keys, routing, Guru login, Google Drive'."""
        w = tab(page, "Providers")
        texts = label_texts(w)
        assert "OPERATOR IDENTITY" in texts
        assert w.findChildren(CredentialsPanel), "credentials panel not on Providers"
        for expected in ("Active model", "Gemini API key", "Provider routing",
                         "Guru", "Google Drive"):
            assert expected in texts, f"{expected!r} missing from Providers tab"

    def test_sources_tab_holds_asana_drive_and_knowledge_base(self, page):
        """settings-overview: Sources = Asana boards, watched Drive folders and
        the knowledge base controls."""
        texts = label_texts(tab(page, "Sources"))
        assert any(t.startswith("ASANA BOARDS") for t in texts)
        assert any(t.startswith("GOOGLE DRIVE FOLDERS") for t in texts)
        assert any(t.startswith("KNOWLEDGE BASE") for t in texts)

    def test_style_guide_tab_holds_guide_and_card_template(self, page):
        """settings-overview: Style Guide = the style guide and the
        card/article template."""
        texts = label_texts(tab(page, "Style Guide"))
        assert "STYLE GUIDE" in texts
        assert "CARD / ARTICLE TEMPLATE" in texts

    def test_intro_row_describes_etl_and_carries_test_connections_button(self, page):
        """settings-overview: 'Above the tabs, a single row describes the page as
        an ETL source config and carries a button labelled Test connections.'"""
        assert any("ETL Sources" in t for t in label_texts(page))
        assert button(page, "Test connections") is not None

    def test_no_reachable_guru_cards_section(self, page):
        """settings-overview: 'There is no reachable Guru cards section on this
        page.' (SettingsPage._guru exists but is never mounted.)"""
        assert hasattr(SettingsPage, "_guru"), "dead _guru builder removed — update the article"
        assert not any("GURU CARDS" in t for t in label_texts(page))
        assert not any("Publish new cards to" in t for t in label_texts(page))

    def test_no_web_tabs_switch_on_the_settings_page(self, page):
        """settings-overview: the React Calendar/Workbench tabs are behind
        `enablement.web_tabs` and 'There is no switch for it on this page.'"""
        surface = " ".join(label_texts(page) + button_texts(page)).lower()
        assert "web_tabs" not in surface
        assert "react" not in surface


class TestOverviewDisplayOnlyControls:

    def test_poll_interval_is_a_read_only_label_not_a_field(self, page):
        """settings-overview: 'the poll interval is a read-only label rather than
        an editable field.'"""
        w = tab(page, "Connections")
        assert "15 min" in label_texts(w), "poll interval is not rendered as a QLabel"
        assert not [e for e in w.findChildren(QLineEdit) if e.text() == "15 min"]

    def test_asana_board_panel_is_fixed_example_content(self, page):
        """settings-connect-asana / settings-overview: 'the board panel below it
        shows fixed example content rather than your real board' — the board
        name, url, indicator and field mappings are hardcoded literals."""
        texts = label_texts(tab(page, "Sources"))
        for literal in ("Enablement Requests", "app.asana.com/0/120…84",
                        "Assigned Team  =  Enablement", "Urgency",
                        "Assigned People", "Launch Coordination"):
            assert literal in texts, f"hardcoded example {literal!r} not found"
        # And the count in the section header is a literal too, with no source.
        assert any(t == "ASANA BOARDS  ·  2 configured" for t in texts)

    def test_asana_board_field_mappings_are_labels_not_inputs(self, page):
        """settings-overview: 'the Asana board mapping fields' are labels, not
        controls that accept input."""
        w = tab(page, "Sources")
        editable = [e.text() for e in w.findChildren(QLineEdit)]
        assert "Urgency" not in editable
        assert "Assigned People" not in editable


class TestOverviewPersistence:
    """settings-overview: 'The AI provider choice, the startup mode choice, your
    identity email, [and] the knowledge base toggle' persist immediately, with
    no save step."""

    def test_provider_choice_persists_immediately(self, page, settings):
        combo = combo_with(tab(page, "Connections"), "gemini", "claude")
        combo.setCurrentIndex(combo.findData("claude"))
        assert settings.store["enablement"]["provider"] == "claude"

    def test_startup_mode_choice_persists_immediately(self, page, settings):
        combo = combo_with(tab(page, "Connections"), "product", "enablement", "last")
        combo.setCurrentIndex(combo.findData("enablement"))
        assert settings.store["app"]["default_mode"] == "enablement"

    def test_identity_email_persists_on_edit_without_a_save_button(self, page, settings):
        page._identity_email.setText("owner@example.com")
        page._identity_email.editingFinished.emit()
        assert settings.store["enablement"]["operator_email"] == "owner@example.com"

    def test_knowledge_base_toggle_persists_immediately(self, page, settings):
        page._kb_toggle_btn.click()
        assert settings.store["enablement"]["kb"]["enabled"] is True
        page._kb_toggle_btn.click()
        assert settings.store["enablement"]["kb"]["enabled"] is False


class TestOverviewWebTabsFlag:

    def test_web_tabs_defaults_to_off_when_the_key_is_absent(self, settings):
        """settings-overview: `enablement.web_tabs` 'is not present in the
        shipped configuration ... the native Qt tabs render instead.'"""
        from src.ui.web.web_flags import web_tabs_mode
        assert "web_tabs" not in settings.get_section("enablement", {})
        assert web_tabs_mode() == "off"

    def test_unrecognised_web_tabs_value_degrades_to_off(self, settings):
        """The native surface can never be taken away by a bad flag value."""
        from src.ui.web.web_flags import web_tabs_mode
        settings.set_section("enablement", {"web_tabs": "nonsense"})
        assert web_tabs_mode() == "off"
        settings.set_section("enablement", {"web_tabs": "all"})
        assert web_tabs_mode() == "all"


# ══════════════════════════════════════════════════════════════════════
# settings-connect-guru — "Connecting Guru"
# ══════════════════════════════════════════════════════════════════════

class TestConnectGuru:

    def test_guru_card_has_email_token_and_one_save_button(self, page):
        """settings-connect-guru: 'There are two fields — an email and a token —
        and a button to save them both.'"""
        w = tab(page, "Providers")
        panel = w.findChildren(CredentialsPanel)[0]
        assert panel._guru_email.echoMode() == QLineEdit.Normal
        assert panel._guru_token.echoMode() == QLineEdit.Password
        save = button(w, "Save Guru credentials")
        assert save is not None and clicked_receivers(save) == 1

    def test_save_button_writes_both_credentials_and_confirms(self, page, monkeypatch):
        """settings-connect-guru: saving stores the credentials and 'the status
        text beside the button should confirm the save'."""
        from src.data.guru_client import GuruClient
        seen = {}
        monkeypatch.setattr(GuruClient, "save_credentials",
                            staticmethod(lambda e, t: (seen.update(email=e, token=t), True)[1]))
        panel = tab(page, "Providers").findChildren(CredentialsPanel)[0]
        panel._guru_email.setText("me@company.com")
        panel._guru_token.setText("guru-token-123")
        button(tab(page, "Providers"), "Save Guru credentials").click()
        assert seen == {"email": "me@company.com", "token": "guru-token-123"}
        assert panel._guru_status.text() == "Saved."

    def test_a_failed_save_is_reported_in_the_status_text(self, page, monkeypatch):
        """settings-connect-guru: 'If the status text reports a failure to save,
        flag it — that points at the credential store.'"""
        from src.data.guru_client import GuruClient
        monkeypatch.setattr(GuruClient, "save_credentials", staticmethod(lambda e, t: False))
        panel = tab(page, "Providers").findChildren(CredentialsPanel)[0]
        panel._guru_email.setText("me@company.com")
        panel._guru_token.setText("t")
        button(tab(page, "Providers"), "Save Guru credentials").click()
        assert panel._guru_status.text() == "Save failed."

    def test_saving_credentials_does_not_test_them(self, page, monkeypatch):
        """settings-connect-guru: 'Saving credentials only stores them. It does
        not test them.'

        The old test relied on an AssertionError raised inside a Qt slot, which
        PySide6 swallows on .click() — so it passed even when the save path DID
        probe Guru (mutation-confirmed by the audit). This spies on
        test_connection and asserts it was not called, evaluated in the test
        body where a failure is real."""
        from src.data.guru_client import GuruClient

        monkeypatch.setattr(GuruClient, "save_credentials",
                            staticmethod(lambda e, t: True))
        probe = MagicMock(return_value=(True, "ok"))
        monkeypatch.setattr(GuruClient, "test_connection", probe)
        panel = tab(page, "Providers").findChildren(CredentialsPanel)[0]
        panel._guru_email.setText("me@company.com")
        panel._guru_token.setText("t")
        button(tab(page, "Providers"), "Save Guru credentials").click()
        probe.assert_not_called()   # saving must not reach out to Guru

    def test_guru_token_is_routed_to_the_os_credential_vault(self):
        """settings-connect-guru: the token is stored 'in the app's encrypted
        credential store, not in a plain settings file'."""
        from src.data import pat_store
        assert "guru_api_token" in pat_store._SECRET_KEYS

    def test_only_the_guru_token_is_encrypted_not_the_email(self):
        """settings-connect-guru: the token 'goes into the app's encrypted
        credential store'; the email 'does not ... written in clear text to the
        app's local state file'. Only guru_api_token is keyring-routed."""
        from src.data import pat_store
        assert "guru_api_token" in pat_store._SECRET_KEYS
        assert "guru_email" not in pat_store._SECRET_KEYS

    def test_existing_card_list_is_fetched_once_and_only_in_live_mode(self):
        """settings-connect-guru: the Workbench existing-card list 'is fetched
        once per session and only in live mode'."""
        calls = []
        client = types.SimpleNamespace(
            search_cards=lambda q: calls.append(q) or [{"id": "c1", "title": "Card"}])

        demo = types.SimpleNamespace(
            _existing_cards_loaded=False, demo=True, _guru_client=client,
            workbench=types.SimpleNamespace(set_existing_cards=lambda rows: rows),
            _chat_say=lambda *a: None)
        EnablementPage._fetch_existing_cards(demo)
        assert calls == [], "demo mode must not query Guru"
        assert demo._existing_cards_loaded is False, "demo must stay refetchable"

        pushed = []
        live = types.SimpleNamespace(
            _existing_cards_loaded=False, demo=False, _guru_client=client,
            workbench=types.SimpleNamespace(set_existing_cards=pushed.append),
            _chat_say=lambda *a: None)
        EnablementPage._fetch_existing_cards(live)
        EnablementPage._fetch_existing_cards(live)
        assert calls == [""], "live fetch must happen exactly once per session"
        assert pushed == [[{"id": "c1", "title": "Card"}]]

    def test_a_guru_failure_still_resolves_the_card_list(self):
        """settings-connect-guru: 'If Guru is connected and the list is still
        empty, flag it' — a failed fetch must hand the submenu an empty result
        rather than hanging."""
        def boom(_q):
            raise RuntimeError("guru down")

        pushed, said = [], []
        stub = types.SimpleNamespace(
            _existing_cards_loaded=False, demo=False,
            _guru_client=types.SimpleNamespace(search_cards=boom),
            workbench=types.SimpleNamespace(set_existing_cards=pushed.append),
            _chat_say=lambda role, msg: said.append(msg))
        EnablementPage._fetch_existing_cards(stub)
        assert pushed == [[]]
        assert said and "Couldn't load Guru cards" in said[0]


# ══════════════════════════════════════════════════════════════════════
# settings-connect-asana — "Connecting Asana"
# ══════════════════════════════════════════════════════════════════════

class TestConnectAsana:

    def test_asana_card_is_on_the_sources_tab_with_key_field_and_setup_button(self, page):
        """settings-connect-asana: 'The Asana card lives in the Sources tab. It
        shows a field labelled for an Asana API key and a button to set up with
        Renn, followed by an example board panel.'"""
        w = tab(page, "Sources")
        assert "API key" in label_texts(w)
        keyf = [e for e in w.findChildren(QLineEdit)
                if e.placeholderText() == "Paste your Asana API key"]
        assert len(keyf) == 1
        assert keyf[0].echoMode() == QLineEdit.Password
        assert button(w, "Set up with Renn") is not None

    def test_the_asana_api_key_field_saves_nothing(self, page, fake_keyring, settings):
        """settings-connect-asana: 'The API key field does not save. Typing a key
        into it and moving on stores nothing.'"""
        w = tab(page, "Sources")
        keyf = [e for e in w.findChildren(QLineEdit)
                if e.placeholderText() == "Paste your Asana API key"][0]
        keyf.setText("1/abc-secret-token")
        keyf.editingFinished.emit()
        keyf.returnPressed.emit()
        assert fake_keyring == {}, "the field wrote to the credential store"
        assert "asana_api_key" not in settings.store.get("enablement", {})
        # No handler at all: nothing observes the field.
        assert keyf.receivers("2editingFinished()") == 0
        assert keyf.receivers("2textChanged(QString)") == 0

    def test_no_code_in_src_ever_writes_the_asana_key(self):
        """settings-connect-asana: 'There is no other credential entry for Asana
        anywhere in the app, so the key has to be provisioned into the credential
        store outside this page.'"""
        import re
        from pathlib import Path
        root = Path(__file__).resolve().parents[1] / "src"
        writer = re.compile(r"save_setting\(\s*[\"']asana_api_key[\"']")
        offenders = [str(p) for p in root.rglob("*.py")
                     if writer.search(p.read_text(encoding="utf-8", errors="ignore"))]
        assert offenders == []

    def test_setup_button_emits_the_setup_request(self, page):
        """settings-connect-asana: the setup button is live (unlike the key
        field) — it raises the Renn setup flow."""
        fired = []
        page.asana_setup_requested.connect(lambda: fired.append(True))
        button(tab(page, "Sources"), "Set up with Renn").click()
        assert fired == [True]

    def test_add_board_button_is_connected_to_nothing(self, page):
        """settings-connect-asana: 'The button offering to add a board is not
        connected to anything.' (Contrast: + Add folder IS wired.)"""
        w = tab(page, "Sources")
        add_board = button(w, "+ Add board")
        add_folder = button(w, "+ Add folder")
        assert add_board is not None and clicked_receivers(add_board) == 0
        assert add_folder is not None and clicked_receivers(add_folder) == 1

    def test_setup_picks_the_first_project_and_the_three_named_fields(
            self, settings, empty_db, monkeypatch):
        """settings-connect-asana: setup 'picks the first project it finds and
        looks for fields named for assigned team, urgency and assigned people',
        then writes a board configuration.

        With no stored key, saving is only allowed in DEMO mode now (finding
        14 — outside demo the mock GIDs must not be persisted), so this drives
        the field-mapping logic in demo mode where the sample board IS saved."""
        from src.data import asana_setup
        import src.data.pat_store as pat_store
        # No stored key → the offline discovery shape (and no network call).
        monkeypatch.setattr(pat_store, "load_setting", lambda k, d=None: "")
        conn = empty_db.conn
        chat, status = [], []
        stub = types.SimpleNamespace(
            demo=True,   # demo mode: the sample board is saved on purpose
            _conn=lambda: conn,
            chat=types.SimpleNamespace(set_chat=lambda msgs: chat.extend(msgs)),
            _chat_say=lambda role, msg: chat.append((role, msg)),
            _set_status=status.append,
            _open_chat=lambda: None)
        EnablementPage._on_asana_setup(stub)

        rows = asana_setup.get_asana_config(conn)
        assert len(rows) == 1, "setup did not write exactly one board config"
        cfg = rows[0]["config"]
        first_project = asana_setup.MOCK_DISCOVERY["projects"][0]
        assert cfg["project_gid"] == first_project["gid"]
        assert cfg["indicators"][0]["field_name"] == "Assigned Team"
        assert cfg["indicators"][0]["trigger_value_names"] == ["Enablement"]
        # Urgency → priority, Assigned People → assignee.
        fields = asana_setup.MOCK_DISCOVERY["custom_fields"][first_project["gid"]]
        by_name = {f["name"]: f["gid"] for f in fields}
        assert cfg["mappings"]["priority_field_gid"] == by_name["Urgency"]
        assert cfg["mappings"]["assignee_field_gid"] == by_name["Assigned People"]

    def test_setup_writes_only_the_asana_source_config(self, settings, empty_db):
        """settings-connect-asana: 'Renn's Asana scope here is deliberately
        narrow — it edits the Asana source configuration and nothing else.'"""
        from src.data import asana_setup
        conn = empty_db.conn
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]

        def snapshot():
            out = {}
            for t in tables:
                try:
                    out[t] = conn.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0]
                except Exception:  # noqa: BLE001 — virtual/fts shadow tables
                    pass
            return out

        before = snapshot()
        asana_setup.set_asana_board_config(
            conn, project_gid="1", project_name="P", indicator_field_gid="2",
            indicator_field_name="Assigned Team", indicator_value_gid="3",
            indicator_value_name="Enablement")
        after = snapshot()
        changed = {t for t in after if after[t] != before.get(t)}
        assert changed == {"monitor_sources"}
        assert settings.store == {}, "board config must not touch app settings"

    def test_setup_upserts_rather_than_duplicating_a_board(self, settings, empty_db):
        """settings-connect-asana: the board config is 'keyed by gid' — re-running
        setup updates the same board rather than adding another."""
        from src.data import asana_setup
        conn = empty_db.conn
        for name in ("First name", "Second name"):
            asana_setup.set_asana_board_config(
                conn, project_gid="777", project_name=name,
                indicator_field_gid="2", indicator_field_name="Assigned Team",
                indicator_value_gid="3", indicator_value_name="Enablement")
        rows = asana_setup.get_asana_config(conn)
        assert len(rows) == 1
        assert rows[0]["display_name"] == "Second name"

    def test_discover_uses_the_live_api_when_a_key_is_stored(self, monkeypatch):
        """settings-connect-asana: setup 'asks Asana for your projects, custom
        fields and enum values' when a key is present."""
        from src.data import asana_setup
        import src.data.asana_client as ac
        import src.data.pat_store as pat_store

        live = {"workspace": {"gid": "9", "name": "Live"},
                "projects": [{"gid": "9001", "name": "Real project"}],
                "custom_fields": {}}
        monkeypatch.setattr(pat_store, "load_setting", lambda k, d=None: "live-key")
        monkeypatch.setattr(ac, "AsanaClient",
                            lambda key: types.SimpleNamespace(discover=lambda: live))
        # discover() now tags a live result with mock=False (finding 14) and
        # otherwise passes the live payload through unchanged.
        result = asana_setup.discover()
        assert result["mock"] is False
        assert result["projects"] == live["projects"]
        assert result["workspace"] == live["workspace"]

    def test_setup_without_a_key_refuses_to_save_outside_demo(
            self, monkeypatch, settings, empty_db):
        """settings-connect-asana (fixed, finding 14): without a stored key and
        outside demo mode, setup must NOT persist a board config built from the
        fabricated sample GIDs — it declines and tells the operator Asana is not
        connected."""
        from src.data import asana_setup
        import src.data.pat_store as pat_store
        monkeypatch.setattr(pat_store, "load_setting", lambda k, d=None: "")
        conn = empty_db.conn
        chat, status = [], []
        stub = types.SimpleNamespace(
            demo=False,   # live mode: mock GIDs must not be written
            _conn=lambda: conn,
            chat=types.SimpleNamespace(set_chat=lambda msgs: chat.extend(msgs)),
            _chat_say=lambda role, msg: chat.append((role, msg)),
            _set_status=status.append,
            _open_chat=lambda: None)
        EnablementPage._on_asana_setup(stub)

        rows = asana_setup.get_asana_config(conn)
        assert rows == [], "setup wrote a fabricated board config with no key"
        blob = " ".join(m[1] for m in chat) + " " + " ".join(status)
        assert "API key" in blob or "not connected" in blob.lower(), (
            "setup did not tell the operator Asana is unconnected")


# ══════════════════════════════════════════════════════════════════════
# settings-connect-google — "Connecting Google Drive"
# ══════════════════════════════════════════════════════════════════════

class TestConnectGoogle:

    def test_both_options_live_in_the_providers_google_card(self, page):
        """settings-connect-google: 'Both options are in the Providers tab, in
        the Google Drive card' — an OAuth connect button, a service-account JSON
        path, and a custom OAuth client option."""
        w = tab(page, "Providers")
        assert "Google Drive" in label_texts(w)
        panel = w.findChildren(CredentialsPanel)[0]
        assert panel._sa_path.placeholderText() == "path/to/service-account.json"
        assert button(w, "Connect my Google account") is not None
        assert button(w, "Use my own GCP client…") is not None

    def test_requested_scopes_are_readonly_plus_drive_file_only(self):
        """settings-connect-google: 'The scopes requested are read-only Drive
        access plus the ability to write files the app itself created. Full Drive
        access is never requested.'"""
        from src.data import google_oauth
        assert google_oauth.scopes() == [
            "https://www.googleapis.com/auth/drive.readonly",
            "https://www.googleapis.com/auth/drive.file",
        ]
        assert "https://www.googleapis.com/auth/drive" not in google_oauth.scopes()

    def test_only_a_minimal_refresh_record_is_stored(self, monkeypatch, settings):
        """settings-connect-google: 'the app stores only a minimal refresh record
        ... never the access token itself.'"""
        import json
        from src.data import google_oauth
        import src.data.pat_store as pat_store

        written = {}
        monkeypatch.setattr(pat_store, "save_setting",
                            lambda k, v: (written.__setitem__(k, v), True)[1])
        ok = google_oauth.store_credentials({
            "client_id": "cid", "client_secret": "sec", "refresh_token": "rt",
            "token_uri": "https://oauth2.googleapis.com/token",
            "token": "ACCESS-TOKEN", "id_token": "ID-TOKEN",
        })
        assert ok is True
        stored = json.loads(written["google_oauth_user"])
        assert set(stored) == {"client_id", "client_secret", "refresh_token", "token_uri"}
        assert "ACCESS-TOKEN" not in written["google_oauth_user"]
        assert "ID-TOKEN" not in written["google_oauth_user"]

    def test_a_record_without_a_refresh_token_is_rejected(self, monkeypatch, settings):
        """settings-connect-google: the stored authorization is the refresh
        record — a record that cannot silently reconnect is refused."""
        from src.data import google_oauth
        import src.data.pat_store as pat_store
        monkeypatch.setattr(pat_store, "save_setting",
                            lambda k, v: pytest.fail("must not persist"))
        assert google_oauth.store_credentials({"client_id": "cid"}) is False

    def test_google_is_inactive_until_an_explicit_reconnect(self):
        """settings-connect-google: 'The app starts every session with Google
        disconnected and performs no Google calls at boot.'"""
        import importlib
        from src.data import google_oauth
        importlib.reload(google_oauth)   # simulate a fresh process import
        assert google_oauth._active is None
        assert google_oauth.is_active() is False
        assert google_oauth.load_active_credentials() is None

    def test_reconnect_never_opens_a_browser(self, monkeypatch, settings):
        """settings-connect-google: 'Authorized but inactive means ... a reconnect
        will be silent — no browser, no re-approval.'"""
        from src.data import google_oauth
        import src.data.pat_store as pat_store
        monkeypatch.setattr(google_oauth, "run_interactive_flow",
                            lambda: pytest.fail("reconnect opened the consent flow"))
        monkeypatch.setattr(pat_store, "load_setting", lambda k, d=None: "")
        assert google_oauth.reconnect() is None
        assert google_oauth.is_active() is False

    def test_drive_stays_idle_until_reconnected_this_session(self, monkeypatch, settings):
        """settings-connect-google: 'Until you explicitly reconnect, Drive reads,
        knowledge base sync and uploads all stay idle.'"""
        from src.data import drive_reader, google_oauth
        settings.set_section("enablement", {
            "drive": {"read_enabled": True, "auth_type": "oauth_user"}})
        reader = drive_reader.DriveReader.from_settings()
        monkeypatch.setattr(google_oauth, "is_active", lambda: False)
        assert reader.is_configured() is False
        monkeypatch.setattr(google_oauth, "is_active", lambda: True)
        assert reader.is_configured() is True

    def test_drive_read_is_also_gated_on_the_org_setting(self, monkeypatch, settings):
        """settings-connect-google / settings-test-connections: 'Drive reading is
        also gated on an organisation-level setting.'"""
        from src.data import drive_reader, google_oauth
        monkeypatch.setattr(google_oauth, "is_active", lambda: True)
        settings.set_section("enablement", {
            "drive": {"read_enabled": False, "auth_type": "oauth_user"}})
        assert drive_reader.DriveReader.from_settings().is_configured() is False

    def test_the_card_shows_three_distinct_states(self, page):
        """settings-connect-google: 'the card shows three different states' —
        not connected / authorized but inactive / connected this session."""
        panel = tab(page, "Providers").findChildren(CredentialsPanel)[0]

        panel._render_google_state(stored=False, active=False)
        assert panel._google_status.text() == "Not connected."
        assert panel._oauth_connect.isVisibleTo(panel)
        assert not panel._oauth_reconnect.isVisibleTo(panel)
        assert not panel._oauth_disconnect.isVisibleTo(panel)

        panel._render_google_state(stored=True, active=False)
        assert "Authorized but inactive" in panel._google_status.text()
        assert not panel._oauth_connect.isVisibleTo(panel)
        assert panel._oauth_reconnect.isVisibleTo(panel)

        panel._render_google_state(stored=True, active=True)
        assert panel._google_status.text() == "Connected this session."
        assert panel._oauth_disconnect.isVisibleTo(panel)
        assert not panel._oauth_reconnect.isVisibleTo(panel)

    def test_disconnect_removes_the_stored_credential(self, page, monkeypatch):
        """settings-connect-google: 'Disconnecting should remove the stored
        credential, not just end the session.'"""
        from src.data import google_oauth
        forgot = []
        monkeypatch.setattr(google_oauth, "forget", lambda: (forgot.append(True), True)[1])
        monkeypatch.setattr(google_oauth, "has_stored_credentials", lambda: False)
        monkeypatch.setattr(google_oauth, "is_active", lambda: False)
        panel = tab(page, "Providers").findChildren(CredentialsPanel)[0]
        panel._on_oauth_disconnect()
        assert forgot == [True], "Disconnect did not clear the stored record"

    def test_forget_deletes_the_keyring_record(self, monkeypatch, settings):
        """settings-connect-google: the removal really deletes the record (and
        best-effort revokes it), rather than only dropping the session."""
        import json
        from src.data import google_oauth
        import src.data.pat_store as pat_store
        record = json.dumps({"client_id": "c", "client_secret": "s",
                             "refresh_token": "rt", "token_uri": "u"})
        written = {}
        monkeypatch.setattr(pat_store, "load_setting", lambda k, d=None: record)
        monkeypatch.setattr(pat_store, "save_setting",
                            lambda k, v: (written.__setitem__(k, v), True)[1])
        revoked = []
        monkeypatch.setattr(google_oauth, "_revoke_at_google", revoked.append)
        assert google_oauth.forget() is True
        assert written["google_oauth_user"] == ""   # empty → delete
        assert revoked == ["rt"]

    def test_a_failed_disconnect_says_the_credential_is_still_stored(self, page, monkeypatch):
        """settings-connect-google: 'Disconnect reports that the credential is
        still stored. The removal failed.'"""
        from src.data import google_oauth
        monkeypatch.setattr(google_oauth, "forget", lambda: False)
        monkeypatch.setattr(google_oauth, "has_stored_credentials", lambda: True)
        monkeypatch.setattr(google_oauth, "is_active", lambda: False)
        panel = tab(page, "Providers").findChildren(CredentialsPanel)[0]
        panel._on_oauth_disconnect()
        assert panel._google_status.text() == (
            "Disconnect failed — the credential is still stored.")

    def test_a_build_without_an_oauth_client_says_so(self, page, monkeypatch):
        """settings-connect-google: 'The card only offers the service account and
        never mentions your own account. The build may not carry an OAuth client.
        Supplying your own Google Cloud client is the documented way around.'"""
        from src.data import google_oauth
        monkeypatch.setattr(google_oauth, "have_client", lambda: False)
        panel = tab(page, "Providers").findChildren(CredentialsPanel)[0]
        panel._start_oauth("connect")
        assert panel._google_status.text() == (
            "No OAuth client — add your own GCP client below first.")
        assert panel._oauth_worker is None, "no consent flow may start"


# ══════════════════════════════════════════════════════════════════════
# settings-test-connections — "Test connections: reading the result"
# ══════════════════════════════════════════════════════════════════════

def _connection_stub():
    """A minimal EnablementPage stand-in that records status emissions."""
    emitted = []
    stub = types.SimpleNamespace(
        demo=False,
        connection_status_ready=types.SimpleNamespace(
            emit=lambda key, ok, detail: emitted.append((key, ok, detail))))
    stub._check_connections_worker = lambda: EnablementPage._check_connections_worker(stub)
    return stub, emitted


class TestTestConnections:

    def test_the_test_connections_button_has_no_handler(self, page):
        """settings-test-connections: 'The button labelled Test connections above
        the tabs is not wired to anything. Clicking it does not re-run the
        check.'"""
        b = button(page, "Test connections")
        assert b is not None
        assert clicked_receivers(b) == 0
        b.click()   # must be a no-op, not a crash

    def test_the_check_is_skipped_entirely_in_demo_mode(self, monkeypatch):
        """settings-test-connections: 'The check is also skipped entirely in demo
        mode, so the dots stay unset there.'"""
        import threading
        started = []

        class FakeThread:
            def __init__(self, *a, **kw):
                self.kw = kw

            def start(self):
                started.append(self.kw.get("target"))

        monkeypatch.setattr(threading, "Thread", FakeThread)
        demo, _ = _connection_stub()
        demo.demo = True
        EnablementPage.check_connections(demo)
        assert started == [], "demo mode ran the connection check"

        live, _ = _connection_stub()
        EnablementPage.check_connections(live)
        assert started == [live._check_connections_worker], (
            "live mode did not start the connection-check worker off the UI thread")

    def test_each_source_reports_independently(self, monkeypatch):
        """settings-test-connections: 'the app checks all three sources ... and
        reports each result independently. Guru failing should not change the
        Asana dot.'"""
        from src.data import asana_setup, drive_reader
        from src.data.asana_client import AsanaClient
        from src.data.guru_client import GuruClient

        monkeypatch.setattr(asana_setup, "is_asana_connected", lambda: True)
        monkeypatch.setattr(AsanaClient, "from_store",
                            classmethod(lambda cls: types.SimpleNamespace(
                                test_connection=lambda: (True, "ok"))))
        monkeypatch.setattr(GuruClient, "load_credentials",
                            staticmethod(lambda: ("me@x.com", "tok")))
        monkeypatch.setattr(GuruClient, "test_connection", lambda self: False)
        monkeypatch.setattr(drive_reader.DriveReader, "from_settings",
                            classmethod(lambda cls: types.SimpleNamespace(
                                is_configured=lambda: False)))
        stub, emitted = _connection_stub()
        EnablementPage._check_connections_worker(stub)
        assert emitted == [
            ("asana", True, ""),
            ("guru", False, "auth failed"),
            ("drive", False, "not configured"),
        ]

    def test_missing_credentials_report_as_no_credentials_not_as_failure(self, monkeypatch):
        """settings-test-connections: 'If no key is stored, it says so rather than
        reporting a failure' — the 'no credentials' vocabulary."""
        from src.data import asana_setup, drive_reader
        from src.data.asana_client import AsanaClient
        from src.data.guru_client import GuruClient

        monkeypatch.setattr(asana_setup, "is_asana_connected", lambda: False)
        monkeypatch.setattr(AsanaClient, "from_store",
                            classmethod(lambda cls: pytest.fail("must not call Asana")))
        monkeypatch.setattr(GuruClient, "load_credentials", staticmethod(lambda: ("", "")))
        monkeypatch.setattr(GuruClient, "test_connection",
                            lambda self: pytest.fail("must not call Guru"))
        monkeypatch.setattr(drive_reader.DriveReader, "from_settings",
                            classmethod(lambda cls: types.SimpleNamespace(
                                is_configured=lambda: False)))
        stub, emitted = _connection_stub()
        EnablementPage._check_connections_worker(stub)
        assert emitted[0] == ("asana", False, "no credentials")
        assert emitted[1] == ("guru", False, "no credentials")
        assert emitted[2] == ("drive", False, "not configured")

    def test_a_dot_shows_connected_or_the_reason(self, page):
        """settings-test-connections: 'A green dot means the app reached that
        service and the service accepted it; anything else carries a short
        reason.'"""
        from src.ui.theme import ALMA_SUCCESS, ALMA_WARNING
        page.set_connection_status("guru", True)
        dot, lbl, _ = page._conn_widgets["guru"]
        assert lbl.text() == "Guru connected"
        assert ALMA_SUCCESS in dot.styleSheet()

        page.set_connection_status("asana", False, "no credentials")
        dot, lbl, _ = page._conn_widgets["asana"]
        assert lbl.text() == "Asana no credentials"
        assert ALMA_WARNING in dot.styleSheet()

    def test_one_dot_failing_does_not_touch_the_others(self, page):
        """settings-test-connections: 'Each dot should reflect that source
        alone.'"""
        page.set_connection_status("asana", True)
        page.set_connection_status("guru", False, "auth failed")
        assert page._conn_widgets["asana"][1].text() == "Asana connected"
        assert page._conn_widgets["drive"][1].text() == "Google Drive —"

    def test_dots_start_neutral_before_the_check_runs(self, page):
        """settings-test-connections: 'A dot that never leaves its neutral state
        means the check has not run yet.'"""
        for key, name in (("asana", "Asana"), ("drive", "Google Drive"), ("guru", "Guru")):
            assert page._conn_widgets[key][1].text() == f"{name} —"

    def test_the_dots_are_not_refreshed_by_a_timer(self, page):
        """settings-test-connections: 'The dots only refresh when the app
        starts' / 'The dots do not refresh mid-session.'"""
        from PySide6.QtCore import QTimer
        assert page.findChildren(QTimer) == []


# ══════════════════════════════════════════════════════════════════════
# settings-identity — "Who you are: identity and your Asana ID"
# ══════════════════════════════════════════════════════════════════════

class TestIdentity:

    def test_identity_card_opens_the_providers_tab(self, page):
        """settings-identity: 'The Providers tab opens with an identity card ...
        an email field, a button that detects your email from your connected
        Google account, and a button that resolves your Asana ID.'"""
        w = tab(page, "Providers")
        assert "OPERATOR IDENTITY" in label_texts(w)
        assert page._identity_email.placeholderText() == "you@company.com"
        for name in ("Auto-detect from Google", "Resolve GID"):
            b = button(w, name)
            assert b is not None and clicked_receivers(b) == 1

    def test_detect_reads_the_email_off_the_connected_google_account(self, settings, monkeypatch):
        """settings-identity: 'Detecting from Google reads the email off the
        account you connected this session and caches it.'"""
        from src.data import google_oauth
        monkeypatch.setattr(google_oauth, "fetch_account_profile",
                            lambda: {"email": "me@company.com", "name": "Me Myself"})
        resolved = []
        stub = types.SimpleNamespace(
            identity_resolved=types.SimpleNamespace(
                emit=lambda *a: resolved.append(a)),
            connection_status_ready=types.SimpleNamespace(
                emit=lambda *a: pytest.fail(f"unexpected failure emit: {a}")))
        EnablementPage._detect_operator_email_worker(stub)
        assert resolved == [("me@company.com", "", "Me Myself")]

    def test_detect_without_google_reports_via_the_drive_status(self, settings, monkeypatch):
        """settings-identity: 'Detecting your email reports that you should
        connect Google first ... this message can appear against the Drive status
        rather than next to the button you pressed.'"""
        from src.data import google_oauth
        monkeypatch.setattr(google_oauth, "fetch_account_profile", lambda: {})
        emitted = []
        stub = types.SimpleNamespace(
            identity_resolved=types.SimpleNamespace(
                emit=lambda *a: pytest.fail("no identity should resolve")),
            connection_status_ready=types.SimpleNamespace(
                emit=lambda *a: emitted.append(a)))
        EnablementPage._detect_operator_email_worker(stub)
        assert emitted == [("drive", False, "Connect your Google account first")]

    def test_resolve_saves_both_the_asana_id_and_display_name(self, monkeypatch, settings):
        """settings-identity: 'Resolving asks Asana who the stored API key belongs
        to and saves both the ID and your Asana display name.'"""
        from src.data.asana_client import AsanaClient
        monkeypatch.setattr(AsanaClient, "from_store",
                            classmethod(lambda cls: types.SimpleNamespace(
                                whoami=lambda: {"gid": "12345", "name": "Jo Rivera",
                                                "email": "jo@company.com"})))
        resolved = []
        worker_stub = types.SimpleNamespace(
            identity_resolved=types.SimpleNamespace(emit=lambda *a: resolved.append(a)),
            connection_status_ready=types.SimpleNamespace(
                emit=lambda *a: pytest.fail(f"unexpected failure emit: {a}")))
        EnablementPage._resolve_operator_gid_worker(worker_stub)
        assert resolved == [("jo@company.com", "12345", "Jo Rivera")]

        shown = []
        page_stub = types.SimpleNamespace(settings=types.SimpleNamespace(
            set_operator_email=lambda e: shown.append(("email", e)),
            set_operator_asana_gid=lambda gid, name: shown.append(("asana", gid, name))))
        EnablementPage._on_identity_resolved(page_stub, *resolved[0])
        assert settings.store["enablement"]["operator_asana_gid"] == "12345"
        assert settings.store["enablement"]["operator_name"] == "Jo Rivera"
        assert ("asana", "12345", "Jo Rivera") in shown

    def test_resolve_without_a_key_reports_via_the_asana_status(self, monkeypatch):
        """settings-identity: 'Resolving your Asana ID reports that you should
        connect Asana first. No Asana key is stored.'"""
        from src.data.asana_client import AsanaClient

        def boom(cls):
            raise RuntimeError("no key")

        monkeypatch.setattr(AsanaClient, "from_store", classmethod(boom))
        emitted = []
        stub = types.SimpleNamespace(
            identity_resolved=types.SimpleNamespace(
                emit=lambda *a: pytest.fail("no identity should resolve")),
            connection_status_ready=types.SimpleNamespace(emit=lambda *a: emitted.append(a)))
        EnablementPage._resolve_operator_gid_worker(stub)
        assert emitted == [("asana", False, "Connect Asana first (paste your API key)")]

    def test_a_typed_email_wins_over_a_detected_one(self, settings):
        """settings-identity: 'A value you type into the email field wins over
        anything detected. The detected email is always cached, but it only fills
        the field when you have not set one yourself.'"""
        settings.set_section("enablement", {"operator_email": "override@company.com"})
        shown = []
        stub = types.SimpleNamespace(settings=types.SimpleNamespace(
            set_operator_email=shown.append,
            set_operator_asana_gid=lambda gid, name: None))
        EnablementPage._on_identity_resolved(stub, "detected@company.com", "", "")
        cfg = settings.store["enablement"]
        assert cfg["operator_email"] == "override@company.com", "override was overwritten"
        assert cfg["detected_email"] == "detected@company.com", "detection was not cached"
        assert shown == ["override@company.com"]

    def test_a_detected_email_fills_an_empty_override(self, settings):
        """settings-identity: the same precedence rule, other direction."""
        shown = []
        stub = types.SimpleNamespace(settings=types.SimpleNamespace(
            set_operator_email=shown.append,
            set_operator_asana_gid=lambda gid, name: None))
        EnablementPage._on_identity_resolved(stub, "detected@company.com", "", "")
        cfg = settings.store["enablement"]
        assert cfg["operator_email"] == "detected@company.com"
        assert shown == ["detected@company.com"]

    def test_mine_filter_matches_asana_id_or_name_never_email(self, settings):
        """settings-identity: 'what actually gets matched is your Asana ID or your
        Asana display name — never your email.'"""
        stub = types.SimpleNamespace(_task_scope="mine")
        settings.set_section("enablement", {"operator_email": "me@company.com"})
        assert EnablementPage._list_filters(stub) == {}, "email alone became a filter"

        settings.set_section("enablement", {
            "operator_email": "me@company.com", "operator_name": "Jo Rivera",
            "operator_asana_gid": "12345"})
        filt = EnablementPage._list_filters(stub)
        assert filt == {"assignee_gid": "12345", "assignee": "Jo Rivera"}
        assert "me@company.com" not in filt.values()

    def test_unknown_identity_falls_back_to_show_all(self, settings):
        """settings-identity: 'When neither an Asana ID nor a name is known, the
        filter falls back to show-all rather than leaving you staring at an empty
        board.'"""
        stub = types.SimpleNamespace(_task_scope="mine")
        assert EnablementPage._list_filters(stub) == {}
        stub_all = types.SimpleNamespace(_task_scope="all")
        settings.set_section("enablement", {"operator_asana_gid": "12345"})
        assert EnablementPage._list_filters(stub_all) == {}, "'all' scope must not filter"

    def test_is_mine_also_matches_the_operator_email(self, settings):
        """settings-identity (nuance): the article says the match is 'never your
        email'. That holds for the Tasks/Calendar list filter, but the shared
        helper enablement_identity.is_mine() ALSO matches the operator email
        against the assignee text. Pinned here so the difference is deliberate."""
        from src.data import enablement_identity as ident
        settings.set_section("enablement", {"operator_email": "me@company.com"})
        assert ident.is_mine("me@company.com") is True
        assert ident.is_mine("Someone Else") is False

    def test_scope_choice_persists_across_relaunches(self, settings):
        """settings-identity: 'Switching between mine and all should persist
        across relaunches.'"""
        calls = []
        stub = types.SimpleNamespace(
            _task_scope="mine",
            tasks=types.SimpleNamespace(set_scope=lambda s: calls.append(("tasks", s))),
            calendar=types.SimpleNamespace(set_scope=lambda s: calls.append(("cal", s))),
            _load_live=lambda: calls.append(("reload",)))
        EnablementPage._on_scope_changed(stub, "all")
        assert settings.store["enablement"]["tasks_default_scope"] == "all"
        assert ("tasks", "all") in calls and ("cal", "all") in calls

    def test_renn_can_answer_who_am_i_without_a_network_call(self, settings, monkeypatch):
        """settings-identity: 'Renn should be able to answer "who am I" from this
        without a network call.'"""
        from src.data import enablement_identity as ident
        from src.data import google_oauth
        monkeypatch.setattr(google_oauth, "fetch_account_email",
                            lambda: pytest.fail("identity made a network call"))
        settings.set_section("enablement", {"operator_email": "me@company.com",
                                            "operator_name": "Jo Rivera"})
        line = ident.operator_context_line()
        assert "me@company.com" in line and "Jo Rivera" in line
        assert ident.operator_identity()["source"] == "settings"
        assert ident.operator_email(resolve=False) == "me@company.com"

    def test_unassigned_tasks_are_excluded_from_the_daily_plan(self):
        """settings-identity: 'Unassigned tasks are excluded from it by
        design.'"""
        from datetime import date
        from src.data.startup_greeting import prioritize_today
        today = date(2026, 7, 20)
        tasks = [
            {"title": "mine", "assignee": "Jo Rivera", "due_date": "2026-07-20", "status": "open"},
            {"title": "unassigned", "assignee": "", "due_date": "2026-07-20", "status": "open"},
            {"title": "theirs", "assignee": "Someone Else", "due_date": "2026-07-20", "status": "open"},
        ]
        plan = prioritize_today(tasks, today=today, operator_aliases={"jo rivera"})
        assert [t["title"] for t in plan.due_today] == ["mine"]


# ══════════════════════════════════════════════════════════════════════
# settings-ai-provider — "Choosing your AI provider"
# ══════════════════════════════════════════════════════════════════════

class TestAiProvider:

    def test_connections_provider_choice_offers_gemini_and_claude(self, page):
        """settings-ai-provider: 'The Connections tab has a provider choice
        offering Gemini or Claude.'"""
        combo = combo_with(tab(page, "Connections"), "gemini", "claude")
        assert combo is not None
        assert [combo.itemText(i) for i in range(combo.count())] == ["Gemini", "Claude"]

    def test_providers_routing_choice_offers_auto_or_forcing_one_provider(self, page):
        """settings-ai-provider: 'The Providers tab has a routing choice offering
        automatic per-task defaults, or forcing everything to one provider.'"""
        panel = tab(page, "Providers").findChildren(CredentialsPanel)[0]
        combo = panel._route_combo
        assert [combo.itemData(i) for i in range(combo.count())] == ["", "gemini", "claude"]
        assert combo.itemText(0) == "Auto (per-task defaults)"

    def test_enablement_choice_scopes_to_enablement_lanes_only(self, settings):
        """settings-ai-provider: the Connections provider 'is scoped to enablement
        work only ... It has no effect on the product side of the app.'"""
        from src.gemini.client_factory import resolve_provider_for_task
        settings.set_section("enablement", {"provider": "claude"})
        assert resolve_provider_for_task("enablement_card_gen") == "claude"
        assert resolve_provider_for_task("enablement_chat") == "claude"
        assert resolve_provider_for_task("nlp_classification") == "gemini"
        assert resolve_provider_for_task("report_generation") == "gemini"

    def test_app_wide_routing_applies_outside_enablement(self, settings):
        """settings-ai-provider: the Providers routing choice 'applies across the
        whole app'."""
        from src.gemini.client_factory import resolve_provider_for_task
        settings.set_section("ai", {"task_routing": {"override_all": "claude"}})
        assert resolve_provider_for_task("nlp_classification") == "claude"
        assert resolve_provider_for_task("report_generation") == "claude"

    def test_enablement_choice_wins_over_app_wide_routing(self, settings):
        """settings-ai-provider: 'The enablement choice is checked first, so it
        wins for enablement work even when the app-wide routing says something
        else.'"""
        from src.gemini.client_factory import resolve_provider_for_task
        settings.set_section("enablement", {"provider": "gemini"})
        settings.set_section("ai", {"task_routing": {"override_all": "claude"}})
        assert resolve_provider_for_task("enablement_card_gen") == "gemini"
        assert resolve_provider_for_task("meta_analytics") == "claude"

    def test_a_provider_change_takes_effect_without_a_relaunch(self, settings):
        """settings-ai-provider: 'Changing either control should take effect on
        your next request without a relaunch' — routing is re-read per call."""
        from src.gemini.client_factory import resolve_provider_for_task
        settings.set_section("enablement", {"provider": "gemini"})
        assert resolve_provider_for_task("enablement_chat") == "gemini"
        settings.set_section("enablement", {"provider": "claude"})
        assert resolve_provider_for_task("enablement_chat") == "claude"

    def test_enablement_claude_always_uses_the_cli_bridge(self, settings, monkeypatch):
        """settings-ai-provider: 'Enablement work always goes through the
        command-line bridge rather than a direct API call.'"""
        from src.gemini.client_factory import build_client_for_task
        from src.llm.claude_cli_client import ClaudeCliClient
        import src.data.pat_store as pat_store
        # A direct Anthropic API key IS present — the CLI must still win.
        monkeypatch.setattr(pat_store, "load_setting",
                            lambda k, d=None: "sk-ant-present" if k == "anthropic_api_key" else "")
        settings.set_section("enablement", {"provider": "claude"})
        client = build_client_for_task("enablement_card_gen")
        assert isinstance(client, ClaudeCliClient)

    def test_the_documented_gemini_fallback_engages_when_no_claude_client_builds(
            self, settings, monkeypatch):
        """settings-ai-provider: the fallback branch itself — when no Claude
        client can be constructed at all, the factory returns a Gemini client
        rather than None."""
        import src.gemini.client_factory as cf
        settings.set_section("enablement", {"provider": "claude"})
        settings.set_section("gemini", {"cli_path": "", "pii_redaction": True})
        monkeypatch.setattr(cf, "_build_claude_client_from_registry",
                            lambda force_cli=False: None)
        sentinel = types.SimpleNamespace(pii_redaction=True)
        monkeypatch.setattr(cf, "_build_gemini_client_internal", lambda use_bridge: sentinel)
        assert cf.build_client_for_task("enablement_card_gen") is sentinel

    def test_unconfigured_claude_still_returns_a_claude_client_no_fallback(
            self, settings, monkeypatch):
        """settings-ai-provider: 'if the command-line tool is present but not
        logged in ... the app builds the client, tries the call, and the call
        fails.' The fallback only engages when no Claude client can be CONSTRUCTED
        (client_factory.py:118). A ClaudeCliClient always constructs, so with
        Claude selected and nothing configured build_client_for_task returns one —
        there is no silent Gemini fallback at build time; failure surfaces as an
        llm_error at call time (llm_gen.py:76-82)."""
        from src.gemini.client_factory import build_client_for_task
        from src.llm.claude_cli_client import ClaudeCliClient
        import src.data.pat_store as pat_store
        monkeypatch.setattr(pat_store, "load_setting", lambda k, d=None: "")
        settings.set_section("enablement", {"provider": "claude"})
        settings.set_section("gemini", {"cli_path": "", "pii_redaction": True})
        client = build_client_for_task("enablement_card_gen")
        assert isinstance(client, ClaudeCliClient), (
            f"expected a ClaudeCliClient (no build-time fallback), got "
            f"{type(client).__name__}")

    def test_enablement_lanes_disable_aggressive_redaction(self, settings, monkeypatch):
        """settings-ai-provider: 'Enablement work also turns off the aggressive
        name-redaction pass.'"""
        import src.gemini.client_factory as cf
        settings.set_section("enablement", {"provider": "gemini"})
        made = types.SimpleNamespace(pii_redaction=True)
        monkeypatch.setattr(cf, "_build_gemini_client_internal", lambda use_bridge: made)
        assert cf.build_client_for_task("enablement_card_gen").pii_redaction is False
        made.pii_redaction = True
        assert cf.build_client_for_task("nlp_classification").pii_redaction is True

    def test_base_redaction_runs_even_with_aggressive_redaction_off(self):
        """settings-ai-provider: 'Base redaction — emails, phone numbers,
        identifiers — always runs regardless, and cannot be turned off.'"""
        from src.llm.claude_cli_client import ClaudeCliClient
        from src.gemini.gemini_client import GeminiClient
        client = ClaudeCliClient(pii_redaction=False)
        out = client._prepare_prompt("write to bob.smith@example.com or 555-123-4567", "")
        assert "bob.smith@example.com" not in out and "[EMAIL]" in out
        assert "555-123-4567" not in out and "[PHONE]" in out
        # ...and the aggressive pass is what got disabled: it rewrites names.
        assert "[NAME]" in GeminiClient._redact_aggressive(None, "Contact John Smith")
        assert "[NAME]" not in out

    def test_claude_key_field_is_disabled_until_all_three_acks(self, page):
        """settings-ai-provider: 'Entering a Claude API key requires acknowledging
        three statements ... The key field stays disabled until all three are
        checked.'"""
        panel = tab(page, "Providers").findChildren(CredentialsPanel)[0]
        assert len(panel._claude_check_widgets) == 3
        assert panel._claude_key.isEnabled() is False
        assert panel._claude_save.isEnabled() is False
        for key in ("baa", "hipaa", "legal"):
            panel._claude_check_widgets[key].setChecked(True)
        assert panel._claude_key.isEnabled() is True
        assert panel._claude_save.isEnabled() is True

    def test_acknowledgements_stay_checked_once_set(self, page):
        """settings-ai-provider: 'The three acknowledgements should stay checked
        once set, and the key field should become usable as soon as the last one
        is checked.'"""
        panel = tab(page, "Providers").findChildren(CredentialsPanel)[0]
        panel._claude_check_widgets["baa"].setChecked(True)
        assert panel._claude_key.isEnabled() is False, "one ack must not unlock the field"
        panel._claude_check_widgets["hipaa"].setChecked(True)
        assert panel._claude_check_widgets["baa"].isChecked() is True, "ack was lost on re-render"
        panel._claude_check_widgets["legal"].setChecked(True)
        assert all(panel._claude_acks.values())
        assert panel._claude_key.isEnabled() is True

    def test_the_ack_gate_is_enforced_on_the_save_path_not_only_the_widget(
            self, page, monkeypatch):
        """settings-ai-provider: 'That gate is intentional' — a force-enabled
        field must still not persist a key."""
        import src.data.pat_store as pat_store
        monkeypatch.setattr(pat_store, "save_setting",
                            lambda k, v: pytest.fail("key persisted without acks"))
        panel = tab(page, "Providers").findChildren(CredentialsPanel)[0]
        panel._claude_key.setEnabled(True)
        panel._claude_key.setText("sk-ant-api03-should-not-store")
        panel._on_save_claude()

    def test_providers_tab_carries_model_choice_and_redaction_toggle(self, page):
        """settings-ai-provider: 'The Providers tab also carries the active model
        choice and a toggle for that aggressive redaction pass on the rest of the
        app.'"""
        w = tab(page, "Providers")
        panel = w.findChildren(CredentialsPanel)[0]
        assert "Active model" in label_texts(w)
        assert isinstance(panel._model_combo, QComboBox)
        assert "Aggressive PII redaction" in label_texts(w)
        assert isinstance(panel._pii_check, QCheckBox)

    def test_the_redaction_toggle_persists_immediately(self, page, settings):
        """settings-ai-provider: the aggressive-redaction toggle writes through to
        settings (it is what client construction reads)."""
        panel = tab(page, "Providers").findChildren(CredentialsPanel)[0]
        panel._pii_check.setChecked(False)
        assert settings.store["gemini"]["pii_redaction"] is False
        panel._pii_check.setChecked(True)
        assert settings.store["gemini"]["pii_redaction"] is True


# ══════════════════════════════════════════════════════════════════════
# settings-guides — "Managing style guides and card templates"
# ══════════════════════════════════════════════════════════════════════

@pytest.fixture
def guide_section(qapp):
    return _GuideSection("STYLE GUIDE", "hint")


class TestGuidesActions:

    def test_each_card_offers_the_five_documented_actions(self, guide_section):
        """settings-guides: 'Each card offers five actions' — Paste, Upload,
        Upload folder, From Drive, Clear."""
        emitted = []
        guide_section.action.connect(emitted.append)
        for label in ("Paste…", "Upload…", "Upload folder…", "From Drive…", "Clear"):
            b = button(guide_section, label)
            assert b is not None, f"missing action button {label!r}"
            b.click()
        assert emitted == ["paste", "upload", "upload_folder", "drive", "clear"]

    def test_both_cards_are_present_with_identical_controls(self, page):
        """settings-guides: 'They are separate documents with identical
        controls.'"""
        w = tab(page, "Style Guide")
        sections = w.findChildren(_GuideSection)
        assert len(sections) == 2
        for s in sections:
            assert {"Paste…", "Upload…", "Upload folder…", "From Drive…", "Clear"} <= set(
                button_texts(s))

    def test_readable_types_are_markdown_text_word_and_html(self):
        """settings-guides: 'Readable file types are markdown, plain text, Word
        documents and HTML.'"""
        assert EnablementPage._GUIDE_EXTS == {
            ".md", ".markdown", ".txt", ".docx", ".html", ".htm"}
        for frag in ("*.md", "*.txt", "*.docx", "*.html"):
            assert frag in EnablementPage._GUIDE_FILE_FILTER

    def test_html_is_converted_to_markdown_when_read(self, tmp_path):
        """settings-guides: 'Word and HTML files are converted to markdown as they
        are stored.'"""
        from src.data.doc_reader import read_document
        f = tmp_path / "guide.html"
        f.write_text(
            "<h1>Tone</h1><p>Use plain language.</p><ul><li>One</li><li>Two</li></ul>",
            encoding="utf-8")
        out = read_document(str(f))
        assert "# Tone" in out, "HTML heading was not converted to a markdown heading"
        assert "- One" in out and "- Two" in out, "HTML list was not converted"
        assert "<h1>" not in out and "<ul>" not in out

    def test_pasting_an_empty_string_clears_rather_than_storing_blank(self, monkeypatch):
        """settings-guides: 'Pasting an empty string clears the document rather
        than storing a blank one.'"""
        from PySide6.QtWidgets import QInputDialog
        stored, cleared, status = [], [], []
        monkeypatch.setattr(QInputDialog, "getMultiLineText",
                            staticmethod(lambda *a, **kw: ("   \n ", True)))
        stub = types.SimpleNamespace(_conn=lambda: None, _set_status=status.append)
        EnablementPage._guide_action(
            stub, "paste", label="Style guide", import_kind="style",
            getter=lambda conn: "old text",
            setter=lambda *a, **kw: stored.append(a),
            clear=lambda: cleared.append(True), slug_prefix="style-guide",
            refresh=lambda: None, demo_loader=lambda conn: None, paste_prompt="p")
        assert stored == [], "a blank document was stored"
        assert cleared == [True]
        assert status == ["Style guide cleared."]

    def test_pasting_text_stores_it(self, monkeypatch):
        """settings-guides: 'Paste — Opens a box pre-filled with the current text;
        saving replaces it.'"""
        from PySide6.QtWidgets import QInputDialog
        seen_default, stored = [], []
        monkeypatch.setattr(QInputDialog, "getMultiLineText",
                            staticmethod(lambda *a, **kw: (seen_default.append(a[-1]),
                                                           ("new guide", True))[1]))
        stub = types.SimpleNamespace(_conn=lambda: None, _set_status=lambda m: None)
        EnablementPage._guide_action(
            stub, "paste", label="Style guide", import_kind="style",
            getter=lambda conn: "current text",
            setter=lambda conn, text, **kw: stored.append(text),
            clear=lambda: pytest.fail("must not clear"), slug_prefix="style-guide",
            refresh=lambda: None, demo_loader=lambda conn: None, paste_prompt="p")
        assert seen_default == ["current text"], "paste box was not pre-filled"
        assert stored == ["new guide"]

    def test_uploading_several_files_stores_all_and_activates_the_last(self):
        """settings-guides: 'Uploading several files at once stores all of them
        and makes the last one active.'"""
        stored, status = [], []
        stub = types.SimpleNamespace(
            _read_local_text=lambda path: f"text of {os.path.basename(path)}",
            _set_status=status.append)
        EnablementPage._store_guide_files(
            stub, None, ["/tmp/first.md", "/tmp/second.md", "/tmp/third.md"],
            lambda conn, text, name=None, doc_id=None: stored.append((name, doc_id)),
            "style-guide", "Style guide")
        assert [n for n, _ in stored] == ["first", "second", "third"]
        assert stored[-1][1] == "style-guide-third"
        assert status == ["3 documents uploaded — “third” is the active style guide."]

    def test_one_unreadable_file_among_several_is_skipped_silently(self):
        """settings-guides: 'A single unreadable file among several is skipped
        silently; the error only appears when nothing at all could be stored.'"""
        def read(path):
            if "bad" in path:
                raise OSError("cannot read")
            return "ok text"

        stored, status = [], []
        stub = types.SimpleNamespace(_read_local_text=read, _set_status=status.append)
        EnablementPage._store_guide_files(
            stub, None, ["/tmp/bad.md", "/tmp/good.md"],
            lambda conn, text, name=None, doc_id=None: stored.append(name),
            "style-guide", "Style guide")
        assert stored == ["good"]
        assert status == ["Style guide “good” uploaded — generation now follows it."]
        assert "cannot read" not in status[0]

    def test_the_error_only_appears_when_nothing_could_be_stored(self):
        """settings-guides: 'Uploading reports that nothing could be stored. The
        files could not be read.'"""
        def read(path):
            raise OSError("cannot read")

        status = []
        stub = types.SimpleNamespace(_read_local_text=read, _set_status=status.append)
        EnablementPage._store_guide_files(
            stub, None, ["/tmp/a.pdf"],
            lambda *a, **kw: pytest.fail("nothing should be stored"),
            "style-guide", "Style guide")
        assert status and status[0].startswith("Could not read those files")

    def test_folder_upload_picks_up_only_supported_documents(self, tmp_path):
        """settings-guides: 'Upload folder — Stores every readable document in a
        folder', and 'The folder contains no files of a supported type' is what
        the empty result means."""
        (tmp_path / "a.md").write_text("a", encoding="utf-8")
        (tmp_path / "b.txt").write_text("b", encoding="utf-8")
        (tmp_path / "c.html").write_text("c", encoding="utf-8")
        (tmp_path / "d.pdf").write_text("d", encoding="utf-8")
        (tmp_path / ".hidden.md").write_text("h", encoding="utf-8")
        (tmp_path / "~$lock.docx").write_text("l", encoding="utf-8")
        found = {os.path.basename(p)
                 for p in EnablementPage._guide_files_in_folder(str(tmp_path))}
        assert found == {"a.md", "b.txt", "c.html"}

        empty = tmp_path / "empty"
        empty.mkdir()
        (empty / "notes.pdf").write_text("x", encoding="utf-8")
        assert EnablementPage._guide_files_in_folder(str(empty)) == []

    def test_subfolder_documents_are_collected_recursively(self, tmp_path):
        """settings-guides: 'the search is fully recursive, so documents in
        subfolders are collected too.' _guide_files_in_folder uses
        Path.rglob('*') (page.py:2319)."""
        (tmp_path / "top.md").write_text("top", encoding="utf-8")
        sub = tmp_path / "nested"
        sub.mkdir()
        (sub / "deep.md").write_text("deep", encoding="utf-8")
        deeper = sub / "deeper"
        deeper.mkdir()
        (deeper / "buried.txt").write_text("buried", encoding="utf-8")
        found = {os.path.basename(p)
                 for p in EnablementPage._guide_files_in_folder(str(tmp_path))}
        assert found == {"top.md", "deep.md", "buried.txt"}

    def test_folder_upload_is_capped(self, tmp_path):
        """settings-guides: 'Stores every readable document in a folder' — with an
        undocumented 50-document cap."""
        for i in range(60):
            (tmp_path / f"doc{i:02d}.md").write_text("x", encoding="utf-8")
        assert len(EnablementPage._guide_files_in_folder(str(tmp_path))) == 50

    def test_drive_import_requires_drive_read_to_be_configured(self, settings, monkeypatch):
        """settings-guides: 'The Drive option needs Drive reading configured and
        Google connected' / 'The Drive import fails saying Drive read is not
        configured.'"""
        from src.data import drive_reader, google_oauth
        monkeypatch.setattr(google_oauth, "is_active", lambda: False)
        settings.set_section("enablement", {
            "drive": {"read_enabled": True, "auth_type": "oauth_user"}})
        assert drive_reader.DriveReader.from_settings().is_configured() is False


class TestGuidesPreviewAndLibrary:

    def test_preview_is_hidden_until_a_document_is_active(self, guide_section):
        """settings-guides: 'the active document shows as a rendered preview'."""
        guide_section.set_content("")
        assert guide_section._preview.isVisibleTo(guide_section) is False
        guide_section.set_content("# Heading\n\nBody text.")
        assert guide_section._preview.isVisibleTo(guide_section) is True
        assert "Heading" in guide_section._view.toPlainText()

    def test_edit_flips_the_preview_into_a_markdown_editor(self, guide_section):
        """settings-guides: 'There is an edit control that flips the preview into
        a plain markdown editor, with save and cancel.'"""
        guide_section.set_content("# Original")
        assert guide_section.in_edit_mode() is False
        button(guide_section, "Edit").click()
        assert guide_section.in_edit_mode() is True
        assert guide_section._editor.toPlainText() == "# Original"
        assert guide_section._editor.isVisibleTo(guide_section) is True
        assert guide_section._view.isVisibleTo(guide_section) is False
        assert button(guide_section, "Save").isVisibleTo(guide_section) is True
        assert button(guide_section, "Cancel").isVisibleTo(guide_section) is True

    def test_saving_an_edit_emits_the_new_text_and_rerenders(self, guide_section):
        """settings-guides: 'Editing in place should save your changes and
        re-render.'"""
        saved = []
        guide_section.saved.connect(saved.append)
        guide_section.set_content("# Original")
        button(guide_section, "Edit").click()
        guide_section._editor.setPlainText("# Edited")
        button(guide_section, "Save").click()
        assert saved == ["# Edited"]
        assert guide_section.content() == "# Edited"
        assert guide_section.in_edit_mode() is False

    def test_cancelling_restores_the_text_and_stores_nothing(self, guide_section):
        """settings-guides: 'Cancelling should restore the previous text and leave
        nothing stored.'"""
        saved = []
        guide_section.saved.connect(saved.append)
        guide_section.set_content("# Original")
        button(guide_section, "Edit").click()
        guide_section._editor.setPlainText("# Discard me")
        button(guide_section, "Cancel").click()
        assert saved == []
        assert guide_section.content() == "# Original"
        assert guide_section.in_edit_mode() is False

    def test_library_rows_show_name_chars_badge_and_actions(self, guide_section):
        """settings-guides: 'the library of stored documents, each showing its name
        and character count, with the active one badged and the others offering to
        become active. Every stored document can be deleted.'"""
        activated, deleted = [], []
        guide_section.activate.connect(activated.append)
        guide_section.delete_doc.connect(deleted.append)
        guide_section.set_library([
            {"doc_id": "sg-a", "name": "[STYLE-GUIDE] Alpha", "chars": 1234, "active": True},
            {"doc_id": "sg-b", "name": "[STYLE-GUIDE] Beta", "chars": 99, "active": False},
        ])
        rows = [guide_section._library.itemAt(i).widget()
                for i in range(guide_section._library.count())]
        assert len(rows) == 2

        active_texts = label_texts(rows[0])
        assert "Alpha" in active_texts, "the tag should be stripped from the name"
        assert "1,234 chars" in active_texts
        assert "Active" in active_texts
        assert button(rows[0], "Make active") is None
        assert button(rows[1], "Make active") is not None
        assert "99 chars" in label_texts(rows[1])

        button(rows[1], "Make active").click()
        assert activated == ["sg-b"]
        for row in rows:
            assert button(row, "Delete") is not None
            button(row, "Delete").click()
        assert deleted == ["sg-a", "sg-b"]


class TestGuidesStorage:
    """Behaviour of the store the Style Guide tab drives (settings faked)."""

    def test_the_last_stored_document_becomes_active(self, settings, empty_db):
        """settings-guides: 'each is stored, the last becomes active'."""
        from src.data import enablement_store as store
        conn = empty_db.conn
        store.set_style_guide(conn, "first text", name="First", doc_id="style-guide-first")
        store.set_style_guide(conn, "second text", name="Second", doc_id="style-guide-second")
        assert store.get_style_guide(conn) == "second text"
        library = store.list_style_guides(conn)
        assert {d["doc_id"] for d in library} == {"style-guide-first", "style-guide-second"}
        assert [d["doc_id"] for d in library if d["active"]] == ["style-guide-second"]

    def test_switching_the_active_document_changes_what_generation_uses(
            self, settings, empty_db):
        """settings-guides: 'Making a different document active should change the
        preview immediately, and the next card generated should follow the newly
        active document. Generation follows the active document only.'"""
        from src.data import enablement_store as store
        conn = empty_db.conn
        store.set_style_guide(conn, "TONE ALPHA", name="Alpha", doc_id="style-guide-alpha")
        store.set_style_guide(conn, "TONE BETA", name="Beta", doc_id="style-guide-beta")
        assert "TONE BETA" in store.style_guide_block(conn)
        assert "TONE ALPHA" not in store.style_guide_block(conn)

        assert store.set_active_style_guide(conn, "style-guide-alpha") is True
        assert store.get_style_guide(conn) == "TONE ALPHA"
        assert "TONE ALPHA" in store.style_guide_block(conn)
        assert "TONE BETA" not in store.style_guide_block(conn)

    def test_the_card_template_is_a_separate_document(self, settings, empty_db):
        """settings-guides: 'The style guide sets tone and phrasing. The card
        template sets the heading structure ... They are separate documents.'"""
        from src.data import enablement_store as store
        conn = empty_db.conn
        store.set_style_guide(conn, "GUIDE TEXT")
        store.set_card_template(conn, "TEMPLATE TEXT")
        assert store.get_style_guide(conn) == "GUIDE TEXT"
        assert store.get_card_template(conn) == "TEMPLATE TEXT"
        assert [d["name"] for d in store.list_style_guides(conn)] == ["[STYLE-GUIDE] Card style guide"]
        assert [d["name"] for d in store.list_card_templates(conn)] == [
            "[CARD-TEMPLATE] Card/article template"]

    def test_deleting_a_document_removes_it_and_promotes_another(self, settings, empty_db):
        """settings-guides: 'Every stored document can be deleted' — and deleting
        the active one promotes the newest remaining."""
        from src.data import enablement_store as store
        conn = empty_db.conn
        store.set_style_guide(conn, "alpha", name="Alpha", doc_id="style-guide-alpha")
        store.set_style_guide(conn, "beta", name="Beta", doc_id="style-guide-beta")
        conn.commit()
        assert store.delete_style_guide(conn, "style-guide-beta") is True
        library = store.list_style_guides(conn)
        assert [d["doc_id"] for d in library] == ["style-guide-alpha"]
        assert store.get_style_guide(conn) == "alpha"

    def test_an_inline_edit_keeps_the_document_name(self, settings, empty_db):
        """settings-guides: an in-place edit rewrites the active document (the
        uploaded name is kept, so the library entry does not change identity)."""
        from src.data import enablement_store as store
        conn = empty_db.conn
        store.set_style_guide(conn, "before", name="Uploaded guide",
                              doc_id="style-guide-uploaded")
        store.update_style_guide_text(conn, "after")
        assert store.get_style_guide(conn) == "after"
        library = store.list_style_guides(conn)
        assert len(library) == 1
        assert library[0]["name"] == "[STYLE-GUIDE] Uploaded guide"

    def test_clear_only_deactivates_and_leaves_the_document_in_the_library(
            self, settings, empty_db):
        """settings-guides: 'Clear does not delete anything. It only drops the
        active pointer ... The document you cleared keeps its row and still
        appears in the library.' _clear_guide pops the active key from settings
        (enablement_store.py:448-453); the row survives."""
        from src.data import enablement_store as store
        conn = empty_db.conn
        store.set_style_guide(conn, "some guide", name="Alpha", doc_id="style-guide-alpha")
        store.clear_style_guide()
        # No active document → generation follows nothing.
        assert store.get_style_guide(conn) == ""
        # ...but the document itself was not removed: it is still in the library
        # and can be made active again.
        library = store.list_style_guides(conn)
        assert [d["doc_id"] for d in library] == ["style-guide-alpha"]
        assert all(d["active"] is False for d in library)
        # Delete is the control that actually removes it.
        assert store.delete_style_guide(conn, "style-guide-alpha") is True
        assert store.list_style_guides(conn) == []

    def test_document_search_matches_the_whole_phrase_as_one_run(self, settings, empty_db):
        """settings-guides: 'Document search matches your whole phrase as a single
        run of text, so a multi-word query often fails where a single distinctive
        word succeeds.'"""
        from src.data import enablement_store as store
        conn = empty_db.conn
        store.save_document(conn, source="manual", name="Onboarding notes",
                            doc_id="doc-1",
                            full_text="alpha beta gamma delta")
        assert [d["doc_id"] for d in store.search_documents(conn, "gamma")] == ["doc-1"]
        assert [d["doc_id"] for d in store.search_documents(conn, "beta gamma")] == ["doc-1"]
        assert store.search_documents(conn, "alpha gamma") == [], (
            "search unexpectedly matched non-adjacent words")
