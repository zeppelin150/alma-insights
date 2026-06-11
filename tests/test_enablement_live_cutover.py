"""Phase 0 — demo→real cutover for the Enablement Workbench.

Verifies that EnablementPage(demo=False) reads the REAL warehouse (tasks +
pending drafts) instead of the throwaway demo DB, and that "Push to Guru"
routes through a real GuruClient in live mode while staying local in demo mode.

Enablement data is seeded headlessly via the simulation pipeline (empty_db has
migration 027 applied); the Guru client is mocked (conftest.mock_guru_client).
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


def _seed(db):
    """Populate the enablement tables the way a real scan would."""
    SIM.run_simulation(db.conn, publish=False)
    SIM.seed_demo_tasks(db.conn)


def test_live_load_populates_pages(qapp, empty_db):
    """demo=False reads tasks + pending drafts from the supplied warehouse."""
    _seed(empty_db)
    from src.ui.pages.enablement import EnablementPage
    page = EnablementPage(empty_db, demo=False)
    assert page._all_tasks, "Task list/Calendar should load real tasks on init"
    assert page._drafts, "Workbench should load pending drafts on init"
    # The active draft was rendered from the live conn, not a demo DB.
    assert page._demo_db is None


def test_push_hits_guru_in_live_mode(qapp, empty_db, mock_guru_client):
    """_on_push in live mode calls the real GuruClient and records the card id."""
    _seed(empty_db)
    from src.ui.pages.enablement import EnablementPage
    page = EnablementPage(empty_db, demo=False)
    page.set_guru_client(mock_guru_client)

    did = next(iter(page._drafts))
    page._on_push(did)

    mock_guru_client.create_card.assert_called_once()
    pushed = S.get_draft(empty_db.conn, did)
    assert pushed["status"] == "pushed"
    assert pushed["card_id"] == "card-new"   # from mock_guru_client.create_card


def test_guru_client_gated_by_mode(qapp, empty_db):
    """The real Guru client is only used in live mode — demo pushes stay local."""
    from src.ui.pages.enablement import EnablementPage
    live = EnablementPage(empty_db, demo=False)
    live.set_guru_client("CLIENT")
    assert live._guru_for_push() == "CLIENT"

    demo = EnablementPage(empty_db, demo=True)
    demo.set_guru_client("CLIENT")
    assert demo._guru_for_push() is None
