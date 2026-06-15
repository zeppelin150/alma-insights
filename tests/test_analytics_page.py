"""Analytics page widget + page integration (offscreen, demo data)."""

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _page(db):
    from src.ui.pages.enablement import EnablementPage
    return EnablementPage(db, demo=True)


def test_analytics_tab_present(qapp, empty_db):
    page = _page(empty_db)
    assert "analytics" in page._tab_widgets
    page.select_tab("analytics")
    assert page.tabs.currentWidget() is page._tab_widgets["analytics"]


def test_demo_data_renders_every_section(qapp, empty_db):
    page = _page(empty_db)
    page._refresh_analytics()   # demo: seeds on first refresh
    a = page.analytics
    assert a._kpi_row.count() == 4
    assert a._top_layout.count() > 1          # rows + stretch
    assert a._due_layout.count() >= 1
    assert a._comments_layout.count() > 1
    assert "Synced" in a._sync_pill.text()


def test_widget_signals(qapp, empty_db):
    from src.ui.pages.enablement.analytics import AnalyticsPage
    a = AnalyticsPage()
    got = {"refresh": 0, "filters": 0, "comment": [], "update": []}
    a.refresh_requested.connect(lambda: got.__setitem__("refresh", got["refresh"] + 1))
    a.filters_changed.connect(lambda: got.__setitem__("filters", got["filters"] + 1))
    a.comment_task_requested.connect(got["comment"].append)
    a.targeted_update_requested.connect(got["update"].append)

    a.set_data(
        {"states": {"STALE": 1}, "queue_total": 1, "due_soon": 1,
         "open_comments": 1, "last_sync_at": "2026-06-11T10:00:00"},
        [{"card_id": "c1", "title": "Card", "collection": "CX",
          "views": 5, "copies": 1, "events": 6}],
        [{"comment_id": "cm1", "card_id": "c1", "card_title": "Card",
          "author": "a@x.com", "text": "hm", "created_at": "", "task_id": None}],
        [{"card_id": "c1", "title": "Card", "collection": "CX",
          "reason": "unverified", "due_date": "2026-06-12"}],
    )
    # click "Create task" and "Targeted update"
    for btn in a._comments_card.findChildren(QPushButton):
        if btn.text() == "Create task":
            btn.click()
    for btn in a._due_card.findChildren(QPushButton):
        if btn.text() == "Targeted update":
            btn.click()
    assert got["comment"] == ["cm1"]
    assert got["update"] == ["c1"]

    a._days.setCurrentIndex(0)
    assert got["filters"] >= 1


class TestUplift:
    def _a(self):
        from src.ui.pages.enablement.analytics import AnalyticsPage
        return AnalyticsPage()

    _KPIS = {"states": {"TRUSTED": 8, "NEEDS_VERIFICATION": 3, "STALE": 1},
             "queue_total": 4, "due_soon": 2, "open_comments": 1,
             "last_sync_at": "2026-06-11T10:00:00"}

    def test_donut_chart_paints(self, qapp):
        from src.ui.pages.enablement.mini_charts import DonutChart
        d = DonutChart()
        d.set_segments([("Trusted", 8, "#3FA66A"), ("Stale", 1, "#D6603A")], "cards")
        d.resize(160, 160)
        d.grab()   # paintEvent runs without raising

    def test_donut_segments_mapped(self, qapp):
        a = self._a()
        segs = a._donut_segments(self._KPIS)
        labels = {s[0] for s in segs}
        assert "Trusted" in labels and "Needs verification" in labels
        # colours assigned, zero states dropped
        assert all(s[1] > 0 for s in segs)

    def test_set_data_renders_donut_and_legend(self, qapp):
        a = self._a()
        a.set_data(self._KPIS, [{"card_id": "c", "title": "C", "views": 4}], [], [])
        assert a._legend.count() >= 3            # legend rows + stretch
        assert a._donut._segments                # donut has segments

    def test_expand_builds_overlay_with_content(self, qapp):
        from PySide6.QtWidgets import QWidget
        a = self._a()
        a.set_overlay_host(QWidget())
        a.set_data(self._KPIS,
                   [{"card_id": "c", "title": "Top card", "views": 9}], [], [])
        a._open_expand()
        assert a._overlay is not None
        assert a._overlay.content.count() >= 1   # expand content populated
        a._overlay._collapse()
        assert a._overlay.isHidden()


def test_comment_to_task_flow_marks_badge(qapp, empty_db):
    page = _page(empty_db)
    page._refresh_analytics()
    comments = [c for c in page.analytics._comments_card.findChildren(QPushButton)
                if c.text() == "Create task"]
    assert comments
    comments[0].click()
    # after conversion the page reloads; the comment now shows a task badge
    create_btns = [c for c in page.analytics._comments_card.findChildren(QPushButton)
                   if c.text() == "Create task" and c.isVisibleTo(page.analytics)]
    n = page._conn().execute(
        "SELECT COUNT(*) FROM enablement_tasks WHERE kind='card_comment'"
    ).fetchone()[0]
    assert n == 1
    assert len(create_btns) == 3  # one of four converted


def test_targeted_update_lands_in_workbench(qapp, empty_db):
    page = _page(empty_db)
    page._refresh_analytics()
    page._on_targeted_update("demo-card-sso")
    assert page.tabs.currentWidget() is page.workbench
    from src.data import enablement_store as store
    drafts = store.list_drafts(page._conn(), status="pending")
    linked = [d for d in drafts if d.get("card_id") == "demo-card-sso"]
    assert linked, "targeted update should import the card as a linked draft"
