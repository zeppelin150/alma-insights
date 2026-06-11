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
