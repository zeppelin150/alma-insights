"""P5 — DriveReader / GoogleDriveExporter branch on auth_type; OAuth path is
launch-gated (not configured until reconnect); worker emits + redacts; panel
stores the record on finish."""

import pytest


@pytest.fixture()
def settings(monkeypatch):
    state = {}
    import src.data.settings_manager as sm
    monkeypatch.setattr(sm, "get_section",
                        lambda n, d=None: state.get(n, d if d is not None else {}))
    monkeypatch.setattr(sm, "set_section",
                        lambda n, v: state.__setitem__(n, dict(v)) or True)
    return state


@pytest.fixture()
def reset_oauth():
    import src.data.google_oauth as go
    go.disconnect()
    yield go
    go.disconnect()


class TestDriveReaderBranch:
    def test_oauth_user_not_configured_until_reconnect(self, settings, reset_oauth):
        settings["enablement"] = {"drive": {"auth_type": "oauth_user", "read_enabled": True}}
        from src.data.drive_reader import DriveReader
        reader = DriveReader.from_settings()
        assert reader._auth_type == "oauth_user"
        # launch state: inactive → not configured (monitor won't auto-start)
        assert reader.is_configured() is False
        # simulate an explicit reconnect this session
        reset_oauth._active = object()
        assert reader.is_configured() is True

    def test_service_account_unchanged(self, settings, tmp_path):
        sa = tmp_path / "sa.json"
        sa.write_text("{}")
        settings["enablement"] = {"drive": {"credentials_path": str(sa),
                                            "read_enabled": True}}
        from src.data.drive_reader import DriveReader
        reader = DriveReader.from_settings()
        assert reader._auth_type == "service_account"
        assert reader.is_configured() is True

    def test_build_service_oauth_requires_active(self, settings, reset_oauth, monkeypatch):
        settings["enablement"] = {"drive": {"auth_type": "oauth_user", "read_enabled": True}}
        from src.data.drive_reader import DriveReader
        reader = DriveReader.from_settings()
        # google libs may be absent → ImportError is the first guard; the
        # OAuth "not connected" RuntimeError is what we assert when present.
        with pytest.raises((RuntimeError, ImportError)):
            reader._build_service()


class TestExporterBranch:
    def test_oauth_launch_gated(self, settings, reset_oauth):
        settings["enablement"] = {"drive": {"auth_type": "oauth_user"}}
        from src.export.gdrive_export import GoogleDriveExporter
        exp = GoogleDriveExporter.from_settings(folder_id="f1")
        assert exp.is_configured() is False     # inactive at launch
        reset_oauth._active = object()
        assert exp.is_configured() is True


class TestWorker:
    def test_redacts_tokens_in_errors(self):
        from src.ui.widgets.google_oauth_worker import _redact
        out = _redact("bad refresh 1//0gSECRETvalue and ya29.LIVEtoken plus eyJabcdefgh")
        assert "1//0gSECRETvalue" not in out
        assert "ya29.LIVEtoken" not in out
        assert "1//<redacted>" in out
        assert "ya29.<redacted>" in out


class TestPanelStoreFlow:
    def test_finish_stores_record_and_activates(self, settings, reset_oauth, monkeypatch):
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        # fake keyring through pat_store
        secrets = {}
        import src.data.pat_store as ps
        monkeypatch.setattr(ps, "save_setting",
                            lambda k, v: secrets.__setitem__(k, "" if v is None else str(v)) or True)
        monkeypatch.setattr(ps, "load_setting", lambda k, d=None: secrets.get(k, d))
        # avoid the real reconnect (no google libs / network)
        monkeypatch.setattr(reset_oauth, "reconnect", lambda: object())

        from src.ui.widgets.credentials_panel import CredentialsPanel
        panel = CredentialsPanel(sections=("external",))
        record = {"client_id": "c", "client_secret": "s",
                  "refresh_token": "1//0gR", "token_uri": "https://t"}
        panel._on_oauth_finished(record)
        assert "google_oauth_user" in secrets
        assert settings["enablement"]["drive"]["auth_type"] == "oauth_user"
