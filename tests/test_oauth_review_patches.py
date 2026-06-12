"""P6 — regression guards for the 9 findings the deep security review
confirmed and we patched."""

import json

import pytest


@pytest.fixture()
def fake_stores(monkeypatch):
    secrets, sections = {}, {}
    import src.data.pat_store as ps
    monkeypatch.setattr(ps, "save_setting",
                        lambda k, v: secrets.__setitem__(k, "" if v is None else str(v)) or True)
    monkeypatch.setattr(ps, "load_setting", lambda k, d=None: secrets.get(k, d))
    import src.data.settings_manager as sm
    monkeypatch.setattr(sm, "get_section",
                        lambda n, d=None: sections.get(n, d if d is not None else {}))
    monkeypatch.setattr(sm, "set_section",
                        lambda n, v: sections.__setitem__(n, dict(v)) or True)
    return secrets, sections


# F3 — non-dict JSON record → None, never an uncaught AttributeError
class TestStoredRecordTypeConfusion:
    @pytest.mark.parametrize("blob", ["null", "42", '"x"', "[1,2]", "true"])
    def test_non_dict_record_returns_none(self, fake_stores, blob):
        import src.data.google_oauth as go
        secrets, _ = fake_stores
        secrets["google_oauth_user"] = blob
        assert go._stored_record() is None
        assert go.reconnect() is None  # must not raise


# F4 — forget() best-effort revokes at Google before deleting
class TestRevokeOnForget:
    def test_forget_calls_revoke(self, fake_stores, monkeypatch):
        import src.data.google_oauth as go
        go.disconnect()
        go.store_credentials({
            "client_id": "c", "client_secret": "s",
            "refresh_token": "1//0gRT", "token_uri": "https://t"})
        revoked = {}
        monkeypatch.setattr(go, "_revoke_at_google",
                            lambda rt: revoked.__setitem__("rt", rt))
        go.forget()
        assert revoked.get("rt") == "1//0gRT"
        assert go.has_stored_credentials() is False

    def test_revoke_never_raises_on_network_error(self, monkeypatch):
        import src.data.google_oauth as go
        import urllib.request
        def boom(*a, **k):
            raise OSError("offline")
        monkeypatch.setattr(urllib.request, "urlopen", boom)
        go._revoke_at_google("1//0gRT")  # must swallow


# F5 — client_config rejects a non-'installed' (web) client
class TestRejectWebClient:
    def test_web_client_refused(self, fake_stores, tmp_path):
        import src.data.google_oauth as go
        web = tmp_path / "web.json"
        web.write_text(json.dumps({"web": {"client_id": "w", "client_secret": "s"}}))
        _, sections = fake_stores
        sections["enablement"] = {"google": {"oauth_client_path": str(web)}}
        assert go.client_config() is None

    def test_installed_client_accepted(self, fake_stores, tmp_path):
        import src.data.google_oauth as go
        good = tmp_path / "installed.json"
        good.write_text(json.dumps({"installed": {"client_id": "i"}}))
        _, sections = fake_stores
        sections["enablement"] = {"google": {"oauth_client_path": str(good)}}
        assert go.client_config()["installed"]["client_id"] == "i"


# F5 — audit_secrets catches GOCSPX- + .gitignore covers oauth client names
class TestSecretGuards:
    def test_audit_pattern_catches_client_secret(self):
        from scripts.audit_secrets import _PATTERNS
        text = "client_secret: GOCSPX-AbCdEf1234567890ZZZZ"
        assert any(p.search(text) for p in _PATTERNS.values())

    def test_gitignore_covers_oauth_client_names(self):
        from pathlib import Path
        gi = (Path(__file__).resolve().parent.parent / ".gitignore").read_text()
        for glob in ("gcp-oauth", "oauth_client", "google_client", "client*secret"):
            assert glob in gi


# F8 — _redact masks GOCSPX + multi-segment / URL-encoded tokens
class TestRedaction:
    def test_redacts_client_secret(self):
        from src.ui.widgets.google_oauth_worker import _redact
        out = _redact("error: client_secret=GOCSPX-SuperSecretValue1234 boom")
        assert "GOCSPX-SuperSecretValue1234" not in out

    def test_redacts_multisegment_access_token(self):
        from src.ui.widgets.google_oauth_worker import _redact
        out = _redact("token ya29.a0AfH6.GhIjKl/mn+op=qr was bad")
        assert "GhIjKl" not in out and "mn+op" not in out

    def test_redacts_url_encoded_refresh(self):
        from src.ui.widgets.google_oauth_worker import _redact
        out = _redact("body refresh_token=1//0g7Q%2FsecretTail%2Bmore&x=1")
        assert "secretTail" not in out


# F9 — Claude key never saves without all 3 BAA acks (authoritative check)
class TestBaaGateAuthoritative:
    def test_direct_slot_call_without_acks_does_not_save(self, monkeypatch):
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        secrets = {}
        import src.data.pat_store as ps
        monkeypatch.setattr(ps, "save_setting",
                            lambda k, v: secrets.__setitem__(k, v) or True)
        monkeypatch.setattr(ps, "load_setting", lambda k, d=None: secrets.get(k, d))
        from src.ui.widgets.credentials_panel import CredentialsPanel
        panel = CredentialsPanel(sections=("llm",))
        # Force-enable the field + set a key, then call the slot directly with
        # zero acknowledgments — the authoritative model check must block it.
        panel._claude_key.setEnabled(True)
        panel._claude_key.setText("sk-ant-bypass")
        panel._on_save_claude()
        assert "anthropic_api_key" not in secrets


# F7 — Drive query single-quote escaping
class TestDriveQueryEscaping:
    def test_quote_escaper(self):
        from src.data.drive_reader import _q
        assert _q("x' or trashed=true or '") == "'x\\' or trashed=true or \\''"
        assert _q("normal") == "'normal'"
