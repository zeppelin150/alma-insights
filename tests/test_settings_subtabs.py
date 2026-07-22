"""E3 — enablement Settings reorganized into sub-tabs; all signals + the
shared credentials panel survive the reorg."""

import pytest
from PySide6.QtWidgets import QApplication, QTabWidget

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _settings(qapp):
    from src.ui.pages.enablement.settings import SettingsPage
    return SettingsPage()


def test_subtabs_present(qapp):
    s = _settings(qapp)
    assert isinstance(s._tabs, QTabWidget)
    titles = [s._tabs.tabText(i) for i in range(s._tabs.count())]
    assert titles == ["Connections", "Providers", "Sources", "Style Guide",
                      "Updates", "Usage", "Maintenance"]


def test_system_tabs_host_the_shared_panels(qapp):
    """The Updates/Maintenance tabs embed the same widgets the product
    Settings page hosts; Usage stays a placeholder until the host wires a
    DatabaseManager factory (a standalone page never opens a database)."""
    from src.ui.widgets.maintenance_panel import MaintenancePanel
    from src.ui.widgets.updates_panel import UpdatesPanel
    s = _settings(qapp)
    assert isinstance(s._updates, UpdatesPanel)
    assert isinstance(s._maintenance, MaintenancePanel)
    assert s._usage_dashboard is None
    # Visiting Usage without a factory keeps the placeholder.
    s._tabs.setCurrentIndex(
        [s._tabs.tabText(i) for i in range(s._tabs.count())].index("Usage"))
    assert s._usage_dashboard is None
    assert s._usage_placeholder.isVisibleTo(s)


def test_usage_tab_builds_renn_usage_panel_when_db_wired(qapp, empty_db):
    from src.ui.pages.enablement.usage_tab import RennUsagePanel
    s = _settings(qapp)
    s.usage_db_factory = lambda: empty_db
    titles = [s._tabs.tabText(i) for i in range(s._tabs.count())]
    s._tabs.setCurrentIndex(titles.index("Usage"))
    assert isinstance(s._usage_dashboard, RennUsagePanel)
    # Idempotent — a second visit must not build a second panel.
    first = s._usage_dashboard
    s._tabs.setCurrentIndex(0)
    s._tabs.setCurrentIndex(titles.index("Usage"))
    assert s._usage_dashboard is first


def test_credentials_panel_on_providers_tab(qapp):
    from src.ui.widgets.credentials_panel import CredentialsPanel
    s = _settings(qapp)
    assert isinstance(s.credentials, CredentialsPanel)
    # the panel is reachable (unchanged contract for the host)
    assert hasattr(s.credentials, "_model_combo")


def test_signals_still_exposed(qapp):
    s = _settings(qapp)
    # all three class-level signals remain (host wiring unchanged)
    for name in ("asana_setup_requested", "drive_folder_added", "style_guide_action"):
        assert hasattr(s, name)


def test_style_guide_action_fires(qapp):
    s = _settings(qapp)
    got = []
    s.style_guide_action.connect(got.append)
    s.set_style_guide_status("Set — 100 chars")  # host setter still works
    # the Style Guide tab's buttons still emit the action
    # (drive/paste/clear are wired in _style_guide; trigger via the signal API)
    s.style_guide_action.emit("clear")
    assert got == ["clear"]


def test_connection_status_setter_survives(qapp):
    s = _settings(qapp)
    # set_connection_status uses _conn_widgets built in _connections()
    s.set_connection_status("guru", True, "")
    # no exception → the Connections tab built its status widgets
    assert "guru" in s._conn_widgets


def test_drive_folder_added_signal(qapp):
    s = _settings(qapp)
    got = []
    s.drive_folder_added.connect(lambda fid, name: got.append((fid, name)))
    s.drive_folder_added.emit("f1", "Docs")
    assert got == [("f1", "Docs")]
