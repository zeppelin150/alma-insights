"""Attention-queue dismissal is a session 'not now', cleared by an explicit
refresh — not a hide-until-restart.

Audit finding 24: _dismissed survived reload() but not restart — a confusing
halfway state that matched neither the old article nor a clean design. Chosen
semantics: dismiss hides the row now; Refresh (reload) brings it back, which is
the recovery path for a mistaken dismissal.
"""

import pytest
from PySide6.QtWidgets import QApplication, QFrame

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _Card:
    def __init__(self, card_id, bucket, title="t"):
        self.card_id = card_id
        self.bucket = bucket
        self.title = title
        self.score = 0.2
        self.reasons = []


def _tab_with(qapp, cards):
    from src.ui.pages.enablement.attention_queue_tab import AttentionQueueTab
    tab = AttentionQueueTab(health_provider=lambda: cards)
    return tab


def test_dismiss_hides_the_row_this_session(qapp):
    from src.ui.pages.enablement import attention_queue_tab as aq
    # Use a bucket key the tab actually renders.
    bucket = aq._BUCKETS[0][0]
    cards = [_Card("c1", bucket), _Card("c2", bucket)]
    tab = _tab_with(qapp, cards)
    tab.reload()

    tab._on_dismiss("c1", QFrame())
    assert "c1" in tab._dismissed
    # Re-render from the same set within the session keeps c1 hidden.
    tab.set_health(cards)
    assert "c1" in tab._dismissed


def test_reload_clears_dismissals_so_a_row_can_return(qapp):
    from src.ui.pages.enablement import attention_queue_tab as aq
    bucket = aq._BUCKETS[0][0]
    cards = [_Card("c1", bucket)]
    tab = _tab_with(qapp, cards)
    tab.reload()
    tab._on_dismiss("c1", QFrame())
    assert "c1" in tab._dismissed

    # An explicit refresh is the recovery path — the dismissal is cleared.
    tab.reload()
    assert "c1" not in tab._dismissed, (
        "reload must clear dismissals so Refresh brings back a mistaken dismiss")


def test_set_health_still_filters_dismissed_within_a_session(qapp):
    """Between reloads, a dismissed card stays hidden even if health is
    re-rendered (e.g. another card is dismissed)."""
    from src.ui.pages.enablement import attention_queue_tab as aq
    bucket = aq._BUCKETS[0][0]
    cards = [_Card("c1", bucket), _Card("c2", bucket)]
    tab = _tab_with(qapp, cards)
    tab.reload()
    tab._on_dismiss("c1", QFrame())
    tab.set_health(cards)
    # c1 is filtered out of the rendered body; c2 remains.
    assert "c1" in tab._dismissed and "c2" not in tab._dismissed
