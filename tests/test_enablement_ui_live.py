"""Phase 7 — UI placeholders wired to the live back-end.

7b: selecting an existing Guru card updates THAT card (stamps the draft's card_id
    so publish_draft takes the update branch, not create).
7c: the connection-check worker emits a real status per source (no network when
    credentials are absent — the test path).
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from src.data import enablement_sim as SIM
from src.data import enablement_store as S

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _page(db, demo=False):
    from src.ui.pages.enablement import EnablementPage
    return EnablementPage(db, demo=demo)


def test_existing_card_update_path(qapp, empty_db, mock_guru_client):
    SIM.run_simulation(empty_db.conn, publish=False)
    page = _page(empty_db)
    page.set_guru_client(mock_guru_client)
    did = page.workbench.active_draft_id

    page._on_publish("guru_existing:card-xyz")

    d = S.get_draft(empty_db.conn, int(did))
    assert d["card_id"] == "card-xyz"          # draft now targets the chosen card
    assert d["status"] == "pushed"
    mock_guru_client.update_card.assert_called_once()   # updated, not created
    mock_guru_client.create_card.assert_not_called()


def test_fetch_existing_cards_populates_menu(qapp, empty_db, mock_guru_client):
    page = _page(empty_db)
    page.set_guru_client(mock_guru_client)
    page._fetch_existing_cards()
    assert page._existing_cards_loaded is True
    mock_guru_client.search_cards.assert_called_once()


def test_connection_check_worker_emits_per_source(qapp, empty_db):
    page = _page(empty_db)
    got: dict[str, bool] = {}
    page.connection_status_ready.connect(lambda k, ok, d: got.__setitem__(k, ok))
    page._check_connections_worker()            # synchronous; no creds → no network
    assert set(got.keys()) == {"asana", "guru", "drive"}
    assert got == {"asana": False, "guru": False, "drive": False}


def test_settings_connection_status_setter(qapp):
    from src.ui.pages.enablement.settings import SettingsPage
    s = SettingsPage()
    s.set_connection_status("guru", True)
    dot, lbl, name = s._conn_widgets["guru"]
    assert "connected" in lbl.text()


def test_demo_existing_card_keeps_scripted_flow(qapp, empty_db):
    """Demo mode must NOT hit a real Guru client for existing-card edits."""
    page = _page(empty_db, demo=True)
    # no guru client, no crash — demo branch is a scripted chat
    page._on_publish("guru_existing:Setting up SSO for Providers")
    assert page._guru_client is None


def test_add_drive_folder_writes_monitor_source(qapp, empty_db):
    page = _page(empty_db)
    page._add_drive_folder("folderXYZ", "Product Docs")
    from src.data import enablement_sources as sources
    rows = sources.list_sources(empty_db.conn, "drive")
    match = [r for r in rows if r["source_id"] == "drive:folderXYZ"]
    assert match, "the Drive folder should be persisted to monitor_sources"
    assert match[0]["config"]["folder_id"] == "folderXYZ"
    assert match[0]["display_name"] == "Product Docs"


def test_settings_set_drive_folders_renders(qapp):
    from src.ui.pages.enablement.settings import SettingsPage
    s = SettingsPage()
    s.set_drive_folders([{"display_name": "Docs", "config": {"folder_id": "f1"}, "last_status": "ok"}])
    assert s._drive_list.count() >= 1
