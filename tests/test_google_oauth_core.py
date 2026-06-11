"""P4 — google_oauth engine: client precedence, minimal bounded record,
silent refresh, forget, disable-on-launch. Fully mocked; no network, no
real keyring, no browser."""

import importlib
import json
import sys

import pytest


@pytest.fixture()
def fake_stores(monkeypatch):
    secrets = {}
    sections = {}
    import src.data.pat_store as ps
    monkeypatch.setattr(ps, "save_setting",
                        lambda k, v: secrets.__setitem__(k, "" if v is None else str(v)) or True)
    monkeypatch.setattr(ps, "load_setting", lambda k, d=None: secrets.get(k, d))
    import src.data.settings_manager as sm
    monkeypatch.setattr(sm, "get_section",
                        lambda n, d=None: sections.get(n, d if d is not None else {}))
    monkeypatch.setattr(sm, "set_section",
                        lambda n, v: sections.__setitem__(n, dict(v)) or True)
    return {"secrets": secrets, "sections": sections}


@pytest.fixture()
def go(fake_stores):
    """Fresh google_oauth module with _active reset."""
    import src.data.google_oauth as go
    importlib.reload(go)
    go.disconnect()
    return go


VALID_RECORD = {
    "client_id": "123.apps.googleusercontent.com",
    "client_secret": "GOCSPX-secret",
    "refresh_token": "1//0gREFRESH",
    "token_uri": "https://oauth2.googleapis.com/token",
}


class TestDisableOnLaunch:
    def test_import_has_no_side_effects(self, fake_stores, monkeypatch):
        # Importing must not read keyring or settings or set _active.
        calls = []
        import src.data.pat_store as ps
        monkeypatch.setattr(ps, "load_setting",
                            lambda k, d=None: calls.append(k) or d)
        sys.modules.pop("src.data.google_oauth", None)
        mod = importlib.import_module("src.data.google_oauth")
        assert mod._active is None
        assert calls == []  # no keyring reads at import

    def test_not_active_until_reconnect(self, go, fake_stores):
        go.store_credentials(VALID_RECORD)
        assert go.has_stored_credentials() is True
        assert go.is_active() is False           # stored, but inactive
        assert go.load_active_credentials() is None


class TestStore:
    def test_minimal_record_round_trips(self, go, fake_stores):
        assert go.store_credentials(VALID_RECORD) is True
        raw = fake_stores["secrets"]["google_oauth_user"]
        assert set(json.loads(raw)) == set(go._RECORD_KEYS)
        assert fake_stores["sections"]["enablement"]["drive"]["auth_type"] == "oauth_user"

    def test_rejects_record_without_refresh_token(self, go, fake_stores):
        assert go.store_credentials({"client_id": "x"}) is False

    def test_strips_access_and_id_token(self, go, fake_stores):
        rec = dict(VALID_RECORD, access_token="ya29.SECRET", id_token="eyJ" + "x" * 1500)
        assert go.store_credentials(rec) is True
        stored = json.loads(fake_stores["secrets"]["google_oauth_user"])
        assert "access_token" not in stored
        assert "id_token" not in stored

    def test_rejects_oversized_record(self, go, fake_stores):
        huge = dict(VALID_RECORD, refresh_token="x" * 4000)
        assert go.store_credentials(huge) is False
        assert "google_oauth_user" not in fake_stores["secrets"]


class TestClientPrecedence:
    def test_admin_override_wins(self, go, fake_stores, tmp_path, monkeypatch):
        client = tmp_path / "client.json"
        client.write_text(json.dumps({"installed": {"client_id": "admin"}}))
        fake_stores["sections"]["enablement"] = {"google": {"oauth_client_path": str(client)}}
        monkeypatch.setattr(go, "_load_bundled_client",
                            lambda: {"installed": {"client_id": "bundled"}})
        assert go.client_config()["installed"]["client_id"] == "admin"

    def test_falls_back_to_bundled(self, go, fake_stores, monkeypatch):
        monkeypatch.setattr(go, "_load_bundled_client",
                            lambda: {"installed": {"client_id": "bundled"}})
        assert go.client_config()["installed"]["client_id"] == "bundled"

    def test_none_when_neither(self, go, fake_stores, monkeypatch):
        monkeypatch.setattr(go, "_load_bundled_client", lambda: None)
        assert go.client_config() is None
        assert go.have_client() is False

    def test_interactive_flow_requires_client(self, go, fake_stores, monkeypatch):
        monkeypatch.setattr(go, "client_config", lambda: None)
        with pytest.raises(RuntimeError, match="No Google OAuth client"):
            go.run_interactive_flow()


class TestReconnect:
    def test_reconnect_activates_with_valid_creds(self, go, fake_stores, monkeypatch):
        go.store_credentials(VALID_RECORD)

        class FakeCreds:
            valid = True
        monkeypatch.setattr(
            "google.oauth2.credentials.Credentials.from_authorized_user_info",
            classmethod(lambda cls, info, scopes: FakeCreds()),
        )
        creds = go.reconnect()
        assert creds is not None
        assert go.is_active() is True
        assert go.load_active_credentials() is creds

    def test_reconnect_refreshes_when_invalid(self, go, fake_stores, monkeypatch):
        go.store_credentials(VALID_RECORD)
        refreshed = {"n": 0}

        class FakeCreds:
            valid = False
            def refresh(self, _request):
                refreshed["n"] += 1
                self.valid = True
        monkeypatch.setattr(
            "google.oauth2.credentials.Credentials.from_authorized_user_info",
            classmethod(lambda cls, info, scopes: FakeCreds()),
        )
        monkeypatch.setattr("google.auth.transport.requests.Request",
                            lambda: object())
        go.reconnect()
        assert refreshed["n"] == 1
        assert go.is_active() is True

    def test_reconnect_none_when_no_record(self, go, fake_stores):
        assert go.reconnect() is None
        assert go.is_active() is False

    def test_reconnect_handles_revoked_token(self, go, fake_stores, monkeypatch):
        go.store_credentials(VALID_RECORD)
        def boom(cls, info, scopes):
            raise ValueError("invalid_grant")
        monkeypatch.setattr(
            "google.oauth2.credentials.Credentials.from_authorized_user_info",
            classmethod(boom),
        )
        assert go.reconnect() is None
        assert go.is_active() is False


class TestForget:
    def test_forget_clears(self, go, fake_stores, monkeypatch):
        go.store_credentials(VALID_RECORD)

        class FakeCreds:
            valid = True
        monkeypatch.setattr(
            "google.oauth2.credentials.Credentials.from_authorized_user_info",
            classmethod(lambda cls, info, scopes: FakeCreds()),
        )
        go.reconnect()
        assert go.is_active() is True
        go.forget()
        assert go.is_active() is False
        assert go.has_stored_credentials() is False
        assert fake_stores["sections"]["enablement"]["drive"]["auth_type"] == "service_account"


class TestScopes:
    def test_least_privilege(self, go):
        s = go.scopes()
        assert s == [
            "https://www.googleapis.com/auth/drive.readonly",
            "https://www.googleapis.com/auth/drive.file",
        ]
        assert "https://www.googleapis.com/auth/drive" not in s  # never full drive
