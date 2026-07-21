"""The analytics-sync outcome must survive on the status line.

Audit finding 21: _on_analytics_synced set the outcome, then called _load_live
whose last act overwrote the status with "N tasks · M pending drafts.", so the
operator never saw whether the sync succeeded. The outcome must be the last
thing written.
"""

import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _page(qapp, empty_db):
    from src.ui.pages.enablement.page import EnablementPage
    return EnablementPage(empty_db)


def test_successful_sync_outcome_is_the_final_status(qapp, empty_db):
    page = _page(qapp, empty_db)
    page._on_analytics_synced({"ok": True})
    text = page._status.text()
    assert "synced" in text.lower(), (
        f"the sync outcome was overwritten by the reload; status was {text!r}")


def test_failed_sync_outcome_is_the_final_status(qapp, empty_db):
    page = _page(qapp, empty_db)
    page._on_analytics_synced(
        {"ok": False, "error": "guru unreachable", "sections": {}})
    text = page._status.text()
    assert "issue" in text.lower() or "guru unreachable" in text.lower(), (
        f"the failure outcome was overwritten by the reload; status was {text!r}")


def test_reload_still_ran(qapp, empty_db):
    """Reordering must not skip the reload — the panels must still refresh."""
    page = _page(qapp, empty_db)
    called = {"n": 0}
    orig = page._load_live
    page._load_live = lambda *a, **k: (called.__setitem__("n", called["n"] + 1),
                                       orig(*a, **k))[1]
    page._on_analytics_synced({"ok": True})
    assert called["n"] == 1, "the reload was dropped when the order changed"
