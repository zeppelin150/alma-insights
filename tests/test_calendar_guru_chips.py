"""Calendar guru chips — payload signal + due-card feed (offscreen)."""

from datetime import date

import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_event_activated_carries_payload(qapp):
    from src.ui.pages.enablement.calendar import CalendarPage, _Chip
    cal = CalendarPage()
    today = date.today().isoformat()
    task = {"due_date": today, "source": "guru",
            "title": "Card due: SSO Setup", "kind": "guru_card_due",
            "card_id": "c-sso"}
    cal.set_tasks([task])

    got = []
    cal.event_activated.connect(got.append)
    chips = [c for c in cal.findChildren(_Chip)
             if "Card due" in c.text()]
    assert chips, "the guru due chip should render on today's cell"
    chips[0].clicked.emit()
    assert got and got[0]["card_id"] == "c-sso"
    assert got[0]["kind"] == "guru_card_due"


def test_subtask_chip_carries_marker(qapp):
    from src.ui.pages.enablement.calendar import CalendarPage, _Chip
    cal = CalendarPage()
    today = date.today().isoformat()
    cal.set_tasks([
        {"due_date": today, "source": "asana", "title": "Sub work",
         "is_subtask": True, "parent_title": "Parent"},
        {"due_date": today, "source": "asana", "title": "Top work"},
    ])
    texts = [c.text() for c in cal.findChildren(_Chip)]
    assert "↳ Sub work" in texts
    assert "Top work" in texts


def test_sample_tuples_still_render(qapp):
    """Pre-scan sample events are (label, kind) 2-tuples — must not crash."""
    from src.ui.pages.enablement.calendar import CalendarPage
    cal = CalendarPage()
    cal._rebuild_grid()
    assert cal._grid_widget is not None


def test_page_feeds_due_cards_to_calendar(qapp, empty_db):
    from src.ui.pages.enablement import EnablementPage
    page = EnablementPage(empty_db, demo=True)
    page._refresh_analytics()      # seeds demo analytics
    page._load_live()              # composes calendar feed incl. due cards
    all_events = [e for evs in page.calendar._events.values() for e in evs]
    guru_due = [e for e in all_events
                if len(e) > 2 and e[2].get("kind") == "guru_card_due"]
    assert guru_due, "due cards should surface as calendar events"


def test_calendar_activation_routes_to_targeted_update(qapp, empty_db):
    from src.ui.pages.enablement import EnablementPage
    page = EnablementPage(empty_db, demo=True)
    page._refresh_analytics()
    page._on_calendar_event_activated(
        {"kind": "guru_card_due", "card_id": "demo-card-returns"}
    )
    assert page.tabs.currentWidget() is page.workbench
    from src.data import enablement_store as store
    drafts = store.list_drafts(page._conn(), status="pending")
    assert any(d.get("card_id") == "demo-card-returns" for d in drafts)
