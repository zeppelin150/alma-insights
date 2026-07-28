"""P2 — shared CredentialsPanel: self-contained (no product SettingsPage),
BAA gate, store routing, signals. Offscreen Qt; settings/pat_store faked."""

import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def stores(monkeypatch):
    """In-memory pat_store + settings_manager so the panel never touches the
    real keyring / settings.yaml."""
    secrets = {}
    sections = {}

    import src.data.pat_store as ps

    def save_setting(key, value):
        secrets[key] = "" if value is None else str(value)
        return True

    def load_setting(key, default=None):
        return secrets.get(key, default)

    monkeypatch.setattr(ps, "save_setting", save_setting)
    monkeypatch.setattr(ps, "load_setting", load_setting)

    import src.data.settings_manager as sm

    def get_section(name, default=None):
        return sections.get(name, default if default is not None else {})

    def set_section(name, value):
        sections[name] = dict(value)
        return True

    def update_section(name, updates):
        cur = dict(sections.get(name, {}))
        cur.update(updates)
        sections[name] = cur
        return True

    for mod in (sm,):
        monkeypatch.setattr(mod, "get_section", get_section)
        monkeypatch.setattr(mod, "set_section", set_section)
        monkeypatch.setattr(mod, "update_section", update_section)
    # client_factory reads routing via its own import of get_section
    import src.gemini.client_factory as cf
    monkeypatch.setattr(cf, "get_section", get_section, raising=False)
    # GuruClient.save_credentials writes via pat_store (already faked)
    return {"secrets": secrets, "sections": sections}


def _panel(sections=("llm", "external")):
    from src.ui.widgets.credentials_panel import CredentialsPanel
    return CredentialsPanel(sections=sections)


def test_builds_without_product_settings_page(qapp, stores):
    # The whole point: no SettingsPage import/construction required.
    panel = _panel()
    assert panel is not None


def test_gemini_key_saves_to_pat_store(qapp, stores):
    panel = _panel()
    flags = []
    panel.settings_changed.connect(flags.append)
    panel._gemini_key.setText("AIzaTESTKEY")
    panel._on_save_gemini()
    assert stores["secrets"]["gemini_api_key"] == "AIzaTESTKEY"
    assert {"gemini_updated": True} in flags


def test_claude_save_blocked_until_three_acks(qapp, stores):
    panel = _panel()
    # Fresh: key field + save disabled
    assert not panel._claude_key.isEnabled()
    assert not panel._claude_save.isEnabled()
    panel._on_claude_ack("baa", True)
    panel._on_claude_ack("hipaa", True)
    assert not panel._claude_save.isEnabled()  # only 2/3
    panel._on_claude_ack("legal", True)
    assert panel._claude_save.isEnabled()
    panel._claude_key.setText("sk-ant-secret")
    panel._on_save_claude()
    assert stores["secrets"]["anthropic_api_key"] == "sk-ant-secret"
    # After save: rendered connected, acks reset
    assert all(v is False for v in panel._claude_acks.values())


def test_routing_override_writes_ai_section(qapp, stores):
    panel = _panel()
    i = panel._route_combo.findData("claude")
    panel._route_combo.setCurrentIndex(i)
    assert stores["sections"]["ai"]["task_routing"]["override_all"] == "claude"


def test_pii_toggle_writes_gemini_section(qapp, stores):
    panel = _panel()
    panel._pii_check.setChecked(False)
    assert stores["sections"]["gemini"]["pii_redaction"] is False


def test_guru_save_round_trips(qapp, stores):
    panel = _panel(sections=("external",))
    panel._guru_email.setText("me@alma.com")
    panel._guru_token.setText("guru-token-xyz")
    panel._on_save_guru()
    assert stores["secrets"]["guru_email"] == "me@alma.com"
    assert stores["secrets"]["guru_api_token"] == "guru-token-xyz"


def test_service_account_path_writes_drive_section(qapp, stores):
    panel = _panel(sections=("external",))
    panel._sa_path.setText("/keys/sa.json")
    panel._on_save_sa()
    drive = stores["sections"]["enablement"]["drive"]
    assert drive["credentials_path"] == "/keys/sa.json"
    assert drive["auth_type"] == "service_account"  # unchanged default


def test_google_oauth_buttons_emit(qapp, stores):
    panel = _panel(sections=("external",))
    got = {"connect": 0, "reconnect": 0, "disconnect": 0}
    panel.google_oauth_requested.connect(lambda: got.__setitem__("connect", 1))
    panel.google_oauth_reconnect_requested.connect(lambda: got.__setitem__("reconnect", 1))
    panel.google_oauth_disconnect_requested.connect(lambda: got.__setitem__("disconnect", 1))
    panel._oauth_connect.click()
    assert got["connect"] == 1


def test_external_only_has_no_llm_controls(qapp, stores):
    panel = _panel(sections=("external",))
    assert not hasattr(panel, "_model_combo")
    assert hasattr(panel, "_guru_email")


# ── Zendesk card ────────────────────────────────────────────────────
# The card lives in the "external" section, so it renders in BOTH hosts:
# enablement settings ("llm", "external") and the product settings page
# ("external",). Credentials only — no Zendesk write path exists to reach.


@pytest.mark.parametrize("sections", [("llm", "external"), ("external",)])
def test_zendesk_card_renders_in_both_hosts(qapp, stores, sections):
    panel = _panel(sections=sections)
    assert panel._zendesk_subdomain is not None
    assert panel._zendesk_email is not None
    assert panel._zendesk_token.echoMode().name == "Password"
    assert panel._zendesk_subdomain.echoMode().name == "Normal"


def test_zendesk_save_round_trips(qapp, stores):
    panel = _panel(sections=("external",))
    flags = []
    panel.settings_changed.connect(flags.append)
    panel._zendesk_subdomain.setText("d3v-acgshelp")
    panel._zendesk_email.setText("me@alma.com")
    panel._zendesk_token.setText("zd-token-xyz")
    panel._on_save_zendesk()
    assert stores["secrets"]["zendesk_subdomain"] == "d3v-acgshelp"
    assert stores["secrets"]["zendesk_email"] == "me@alma.com"
    assert stores["secrets"]["zendesk_api_key"] == "zd-token-xyz"
    assert {"zendesk_updated": True} in flags


def test_zendesk_save_calls_client_with_stored_view_id(qapp, stores, monkeypatch):
    stores["secrets"]["zendesk_view_id"] = "900001"
    calls = []
    from src.data.zendesk_client import ZendeskClient
    monkeypatch.setattr(
        ZendeskClient, "save_credentials",
        staticmethod(lambda *a, **k: calls.append((a, k)) or True))
    panel = _panel(sections=("external",))
    panel._zendesk_subdomain.setText("acme")
    panel._zendesk_email.setText("me@alma.com")
    panel._zendesk_token.setText("tok")
    panel._on_save_zendesk()
    assert calls == [(("acme", "me@alma.com", "tok"), {"view_id": "900001"})]


def test_zendesk_save_requires_all_three_fields(qapp, stores):
    panel = _panel(sections=("external",))
    panel._zendesk_subdomain.setText("acme")
    panel._zendesk_email.setText("")           # missing
    panel._zendesk_token.setText("tok")
    panel._on_save_zendesk()
    assert "zendesk_subdomain" not in stores["secrets"]
    assert "zendesk_api_key" not in stores["secrets"]
    assert "email" in panel._zendesk_status.text()


def test_zendesk_save_preserves_source_monitor_view_id(qapp, stores):
    # The Source Monitor owns the view id. Saving from this card must not
    # blank it — save_credentials writes all four keys.
    stores["secrets"]["zendesk_view_id"] = "900001"
    panel = _panel(sections=("external",))
    panel._zendesk_subdomain.setText("acme")
    panel._zendesk_email.setText("me@alma.com")
    panel._zendesk_token.setText("tok")
    panel._on_save_zendesk()
    assert stores["secrets"]["zendesk_view_id"] == "900001"


def test_zendesk_blank_token_keeps_stored_token(qapp, stores):
    stores["secrets"].update({
        "zendesk_subdomain": "acme",
        "zendesk_email": "old@alma.com",
        "zendesk_api_key": "existing-secret",
    })
    panel = _panel(sections=("external",))
    panel._zendesk_email.setText("new@alma.com")   # token field left blank
    panel._on_save_zendesk()
    assert stores["secrets"]["zendesk_api_key"] == "existing-secret"
    assert stores["secrets"]["zendesk_email"] == "new@alma.com"


def test_zendesk_blank_token_with_none_stored_is_rejected(qapp, stores):
    panel = _panel(sections=("external",))
    panel._zendesk_subdomain.setText("acme")
    panel._zendesk_email.setText("me@alma.com")
    panel._on_save_zendesk()
    assert "zendesk_api_key" not in stores["secrets"]
    assert "API token" in panel._zendesk_status.text()


def test_zendesk_token_field_never_shows_the_secret(qapp, stores):
    stores["secrets"].update({
        "zendesk_subdomain": "acme",
        "zendesk_email": "me@alma.com",
        "zendesk_api_key": "existing-secret",
    })
    panel = _panel(sections=("external",))
    assert panel._zendesk_subdomain.text() == "acme"
    assert panel._zendesk_email.text() == "me@alma.com"
    assert panel._zendesk_token.text() == ""
    assert "Token saved" in panel._zendesk_status.text()
    # And it stays blank after an explicit refresh.
    panel.refresh()
    assert panel._zendesk_token.text() == ""


def test_zendesk_card_states_the_read_only_scope(qapp, stores):
    from PySide6.QtWidgets import QLabel
    panel = _panel(sections=("external",))
    texts = " ".join(w.text() for w in panel.findChildren(QLabel))
    assert "Read-only" in texts
    assert "never writes to Zendesk" in texts
