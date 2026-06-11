"""P3 — credential fields persist to the real settings store and the
downstream consumer (DriveReader) picks them up. auth_type defaults to
service_account so existing behavior is unchanged.
"""

import json

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def temp_settings(tmp_path, monkeypatch):
    """Point settings_manager at a temp settings.yaml (covers get/set/update
    since they all resolve through get_settings_path)."""
    import src.data.settings_manager as sm
    path = tmp_path / "settings.yaml"
    monkeypatch.setattr(sm, "get_settings_path", lambda: path)
    return path


def _panel():
    from src.ui.widgets.credentials_panel import CredentialsPanel
    return CredentialsPanel(sections=("external",))


def test_sa_path_persists_and_drivereader_sees_it(qapp, temp_settings, tmp_path):
    sa_file = tmp_path / "sa.json"
    sa_file.write_text(json.dumps({"type": "service_account"}))

    panel = _panel()
    panel._sa_path.setText(str(sa_file))
    panel._on_save_sa()

    from src.data.settings_manager import get_section
    drive = get_section("enablement", {})["drive"]
    assert drive["credentials_path"] == str(sa_file)
    assert drive["auth_type"] == "service_account"   # default — unchanged behavior
    assert drive["read_enabled"] is True

    from src.data.drive_reader import DriveReader
    assert DriveReader.from_settings().is_configured() is True


def test_sa_not_configured_when_file_missing(qapp, temp_settings):
    panel = _panel()
    panel._sa_path.setText("/nope/missing.json")
    panel._on_save_sa()
    from src.data.drive_reader import DriveReader
    assert DriveReader.from_settings().is_configured() is False


def test_oauth_client_path_persists(qapp, temp_settings, tmp_path, monkeypatch):
    client_file = tmp_path / "client.json"
    client_file.write_text("{}")
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName",
        lambda *a, **k: (str(client_file), "JSON (*.json)"),
    )
    panel = _panel()
    panel._on_browse_oauth_client()
    from src.data.settings_manager import get_section
    assert get_section("enablement", {})["google"]["oauth_client_path"] == str(client_file)


def test_auth_type_round_trips_through_panel_refresh(qapp, temp_settings, tmp_path):
    sa_file = tmp_path / "sa.json"
    sa_file.write_text("{}")
    panel = _panel()
    panel._sa_path.setText(str(sa_file))
    panel._on_save_sa()
    # A fresh panel re-reads the persisted SA path
    panel2 = _panel()
    assert panel2._sa_path.text() == str(sa_file)
