"""Accuracy audit of the in-app Help Center — section: ``insights``.

Every test here settles one falsifiable claim made by an article under
``assets/help/insights/``:

  * ``guru-analytics.md``      — Guru Analytics: filters, refresh, expand overlay
  * ``weak-card-actions.md``   — Turning a weak card into an update or a task
  * ``attention-queue.md``     — The attention queue: card health buckets
  * ``card-health-scoring.md`` — How card health is scored

Each test docstring names the article and quotes the claim it settles. The
seven claims the code once contradicted have been corrected in the articles;
those tests now assert the ACTUAL verified behaviour (no ``xfail`` markers
remain), so a regression in either the code or the prose fails the suite.

Headless: no network, no credentials, no QtWebEngine. Qt widgets are exercised
through their public APIs (never screenshots).
"""

from __future__ import annotations

import inspect
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from src.data import guru_analytics as ga
from src.data.enablement_health import score as score_mod
from src.data.enablement_health.models import CardHealth, CardSignals
from src.data.enablement_health.score import score_card

pytestmark = pytest.mark.ui


# ── fixtures / helpers ────────────────────────────────────────────────


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _iso_ahead(days: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _analytics():
    from src.ui.pages.enablement.analytics import AnalyticsPage
    return AnalyticsPage()


def _kpi_cells(page) -> list[tuple[str, str]]:
    """[(caption, value)] read back off the rendered KPI row."""
    out = []
    for i in range(page._kpi_row.count()):
        w = page._kpi_row.itemAt(i).widget()
        if w is None:
            continue
        labels = w.findChildren(QLabel)
        if len(labels) >= 2:
            out.append((labels[1].text(), labels[0].text()))
    return out


def _labels(widget) -> list[str]:
    return [lbl.text() for lbl in widget.findChildren(QLabel)]


def _buttons(widget) -> list[str]:
    return [b.text() for b in widget.findChildren(QPushButton)]


def _live_labels(root) -> list[str]:
    """Texts of labels not sitting under a hidden ancestor.

    ``_clear_body`` hides widgets and defers deletion, so without an event loop
    the stale ones linger in the object tree — this filters them out.
    """
    out = []
    for lbl in root.findChildren(QLabel):
        w, hidden = lbl, False
        while w is not None and w is not root:
            if w.isHidden():
                hidden = True
                break
            w = w.parentWidget()
        if not hidden:
            out.append(lbl.text())
    return out


def _live_buttons(root) -> list[str]:
    out = []
    for btn in root.findChildren(QPushButton):
        w, hidden = btn, False
        while w is not None and w is not root:
            if w.isHidden():
                hidden = True
                break
            w = w.parentWidget()
        if not hidden:
            out.append(btn.text())
    return out


def _click(widget, text: str) -> bool:
    for btn in widget.findChildren(QPushButton):
        if btn.text() == text:
            btn.click()
            return True
    return False


def _verification_row(conn, card_id, **over):
    row = {
        "title": f"Card {card_id}", "collection_id": "co1",
        "collection_name": "Enablement", "verification_state": "TRUSTED",
        "next_verification_date": "", "last_modified": _iso(1),
    }
    row.update(over)
    conn.execute(
        "INSERT OR REPLACE INTO guru_card_verification "
        "(card_id, title, collection_id, collection_name, verification_state, "
        " next_verification_date, last_modified, fetched_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (card_id, row["title"], row["collection_id"], row["collection_name"],
         row["verification_state"], row["next_verification_date"],
         row["last_modified"], _iso(0)),
    )
    conn.commit()


def _view_event(conn, card_id, days_ago, key):
    conn.execute(
        "INSERT OR IGNORE INTO guru_events "
        "(event_key, event_type, user_email, event_date, card_id, fetched_at) "
        "VALUES (?,?,?,?,?,?)",
        (key, "card-viewed", "a@x.com", _iso(days_ago), card_id, _iso(0)),
    )
    conn.commit()


def _comment_row(conn, comment_id, card_id="c1", status="OPEN",
                 author="jo@x.com", text="Step 3 is wrong", created_at=None,
                 card_title="SSO Setup"):
    conn.execute(
        "INSERT OR REPLACE INTO guru_card_comments "
        "(comment_id, card_id, card_title, author, text, created_at, status, "
        " fetched_at) VALUES (?,?,?,?,?,?,?,?)",
        (comment_id, card_id, card_title, author, text,
         created_at or _iso(1), status, _iso(0)),
    )
    conn.commit()


def _signals(**over) -> CardSignals:
    base = dict(card_id="c1", title="Card", days_overdue=0.0, view_count=0,
                open_comment_count=0, source_changed=False,
                duplicate_of=[], gap=False)
    base.update(over)
    return CardSignals(**base)


def _health(card_id, score, bucket, title=None) -> CardHealth:
    return CardHealth(
        card_id=card_id, score=score, bucket=bucket, components={},
        signals=CardSignals(card_id=card_id, title=title or card_id),
    )


class _FakeGuruSignals:
    """Stands in for the GuruSignals facade gather_signals consumes."""

    def __init__(self, queue=None, views=None, comments=None):
        self._queue = queue or []
        self._views = views or {}
        self._comments = comments or {}

    def verification_queue(self):
        return self._queue

    def card_view_counts(self, from_date=None, to_date=None):
        return self._views

    def open_comment_count(self, card_id):
        return self._comments.get(card_id, 0)


# ══════════════════════════════════════════════════════════════════════
# guru-analytics.md
# ══════════════════════════════════════════════════════════════════════


class TestGuruAnalyticsPage:
    """assets/help/insights/guru-analytics.md"""

    def test_sync_pill_reads_never_synced_before_the_first_sync(self, qapp):
        """ARTICLE: 'Before you have ever synced it reads "Never synced"'."""
        page = _analytics()
        page.set_data({"states": {}, "last_sync_at": ""}, [], [], [])
        assert page._sync_pill.text() == "Never synced"
        page.set_data({"states": {}, "last_sync_at": "2026-07-01T09:30:00"},
                      [], [], [])
        assert page._sync_pill.text().startswith("Synced 2026-07-01")

    def test_every_number_is_zero_on_an_unsynced_database(self, empty_db):
        """ARTICLE: '[before syncing] every number on the page is zero'."""
        kpis = ga.verification_kpis(empty_db.conn)
        assert kpis["queue_total"] == 0
        assert kpis["due_soon"] == 0
        assert kpis["open_comments"] == 0
        assert kpis["states"] == {}
        assert kpis["last_sync_at"] == ""

    def test_refresh_button_emits_a_refresh_request(self, qapp):
        """ARTICLE: 'The button on the right pulls fresh data from Guru'."""
        page = _analytics()
        fired = []
        page.refresh_requested.connect(lambda: fired.append(1))
        assert _click(page, "Refresh from Guru")
        assert fired == [1]

    def test_refresh_runs_the_pull_on_a_background_thread(
            self, qapp, empty_db, monkeypatch):
        """ARTICLE: 'it runs in the background, so the page stays usable'."""
        from src.ui.pages.enablement import EnablementPage
        page = EnablementPage(empty_db, demo=True)
        started = []

        class _FakeThread:
            def __init__(self, target=None, daemon=None, **kw):
                self.target, self.daemon = target, daemon

            def start(self):
                started.append(self)   # captured, never run — no network

        monkeypatch.setattr(threading, "Thread", _FakeThread)
        page.demo = False              # exercise the live branch
        page._run_analytics_sync()
        assert len(started) == 1, "sync did not go to a background thread"
        assert started[0].daemon is True
        assert callable(started[0].target)

    def test_the_four_kpi_captions_match_the_article(self, qapp):
        """ARTICLE: the four figures are 'In verification queue', 'Due in 14
        days', 'Open comments' and 'Needs verification'."""
        page = _analytics()
        page.set_data({"states": {}, "queue_total": 0, "due_soon": 0,
                       "open_comments": 0, "last_sync_at": ""}, [], [], [])
        assert [c for c, _v in _kpi_cells(page)] == [
            "IN VERIFICATION QUEUE", "DUE IN 14 DAYS", "OPEN COMMENTS",
            "NEEDS VERIFICATION",
        ]

    def test_needs_verification_kpi_adds_needs_verification_and_stale(self, qapp):
        """ARTICLE: 'Needs verification | Cards in a "needs verification" or
        "stale" state'."""
        page = _analytics()
        page.set_data(
            {"states": {"NEEDS_VERIFICATION": 4, "STALE": 3, "TRUSTED": 90},
             "queue_total": 97, "due_soon": 0, "open_comments": 0,
             "last_sync_at": ""}, [], [], [])
        cells = dict(_kpi_cells(page))
        assert cells["NEEDS VERIFICATION"] == "7"

    def test_open_comments_kpi_counts_only_open_comments(self, empty_db):
        """ARTICLE: 'Open comments | Card comments still open'."""
        conn = empty_db.conn
        _comment_row(conn, "cm-open", status="OPEN")
        _comment_row(conn, "cm-done", status="RESOLVED")
        assert ga.verification_kpis(conn)["open_comments"] == 1

    def test_due_soon_uses_a_fourteen_day_horizon(self, empty_db):
        """ARTICLE: 'Due in 14 days | Cards whose next verification date falls
        inside 14 days'."""
        assert ga.DUE_SOON_DAYS == 14
        conn = empty_db.conn
        _verification_row(conn, "c-soon", next_verification_date=_iso_ahead(10))
        _verification_row(conn, "c-later", next_verification_date=_iso_ahead(20))
        assert ga.verification_kpis(conn)["due_soon"] == 1

    def test_due_soon_also_counts_cards_already_overdue(self, empty_db):
        """ARTICLE guru-analytics.md: 'Due in 14 days | Cards due for
        verification within the next 14 days — or already past due'.

        The SQL is `next_verification_date <= now+14d` with no lower bound
        (guru_analytics.py:340-344), so a card 100 days past its date is
        counted here too.
        """
        conn = empty_db.conn
        _verification_row(conn, "c-old", next_verification_date=_iso(100))
        assert ga.verification_kpis(conn)["due_soon"] == 1

    def test_queue_total_is_the_sum_of_the_state_counts(self, empty_db):
        """ARTICLE: 'In verification queue | Every card with a verification
        state' — the KPI totals the per-state counts."""
        conn = empty_db.conn
        _verification_row(conn, "c1", verification_state="TRUSTED")
        _verification_row(conn, "c2", verification_state="STALE")
        kpis = ga.verification_kpis(conn)
        assert kpis["queue_total"] == sum(kpis["states"].values()) == 2

    def test_queue_total_counts_every_row_including_a_blank_state(
            self, empty_db):
        """ARTICLE guru-analytics.md: 'In verification queue | Every card the
        verification pull returned, whether or not it carries a verification
        state'.

        verification_kpis groups by COALESCE(verification_state,'') and buckets
        the blank state under 'UNKNOWN', which queue_total then sums
        (guru_analytics.py:331-337,355) — so a row with no state is counted.
        """
        conn = empty_db.conn
        _verification_row(conn, "c-stated", verification_state="TRUSTED")
        _verification_row(conn, "c-blank", verification_state="")
        kpis = ga.verification_kpis(conn)
        assert kpis["queue_total"] == 2
        assert kpis["states"].get("UNKNOWN") == 1

    def test_top_cards_are_ranked_by_views_inside_the_window(self, empty_db):
        """ARTICLE: 'Top used cards — ranked by views in the selected window'."""
        conn = empty_db.conn
        for i in range(3):
            _view_event(conn, "c-hot", 2, f"hot{i}")
        _view_event(conn, "c-cold", 2, "cold0")
        _view_event(conn, "c-ancient", 45, "anc0")
        rows = ga.top_cards(conn, days=7)
        assert [r["card_id"] for r in rows] == ["c-hot", "c-cold"]
        assert rows[0]["views"] == 3
        assert "c-ancient" in [r["card_id"] for r in ga.top_cards(conn, days=90)]

    def test_top_cards_shows_twelve_rows_on_the_page(self, qapp):
        """ARTICLE: 'Twelve rows here, twenty in the expanded view'."""
        page = _analytics()
        top = [{"card_id": f"c{i}", "title": f"Card {i}", "collection": "CX",
                "views": 100 - i} for i in range(25)]
        page.set_data({"states": {}, "last_sync_at": ""}, top, [], [])
        rows = [t for t in _labels(page._top_card) if t.endswith(" views")]
        assert len(rows) == 12

    def test_expanded_view_shows_twenty_rows(self, qapp):
        """ARTICLE: 'Twelve rows here, twenty in the expanded view'."""
        page = _analytics()
        top = [{"card_id": f"c{i}", "title": f"Card {i}", "collection": "CX",
                "views": 100 - i} for i in range(25)]
        page.set_data({"states": {}, "last_sync_at": ""}, top, [], [])
        page._open_expand()
        rows = [t for t in _labels(page._overlay) if t.endswith(" views")]
        assert len(rows) == 20

    def test_top_card_rows_carry_a_usage_bar_a_badge_and_a_view_count(self, qapp):
        """ARTICLE: 'each with a proportional usage bar, a collection badge and
        a view count'."""
        from PySide6.QtWidgets import QFrame
        page = _analytics()
        page.set_data(
            {"states": {}, "last_sync_at": ""},
            [{"card_id": "a", "title": "Top", "collection": "CX", "views": 100},
             {"card_id": "b", "title": "Half", "collection": "CX", "views": 50}],
            [], [])
        texts = _labels(page._top_card)
        assert "100 views" in texts and "50 views" in texts
        assert texts.count("CX") == 2                     # collection badges
        bars = sorted(f.width() for f in page._top_card.findChildren(QFrame)
                      if type(f) is QFrame and f.height() == 5)
        assert len(bars) == 2 and bars[0] < bars[1]       # proportional

    def test_verification_donut_has_a_counted_legend(self, qapp):
        """ARTICLE: 'Verification — a donut of verification states with a
        counted legend.'"""
        page = _analytics()
        page.set_data(
            {"states": {"TRUSTED": 12, "STALE": 3}, "last_sync_at": ""},
            [], [], [])
        texts = _live_labels(page._donut_card)
        assert "Trusted" in texts and "12" in texts
        assert "Stale" in texts and "3" in texts
        segments = page._donut_segments({"states": {"TRUSTED": 12, "STALE": 3}})
        assert [(lbl, n) for lbl, n, _c in segments] == [
            ("Trusted", 12), ("Stale", 3)]

    def test_analytics_sync_without_credentials_reports_not_connected(
            self, qapp, empty_db, monkeypatch):
        """ARTICLE: 'The sync reports that Guru is not connected. The pull needs
        saved Guru credentials.'"""
        from src.data.guru_client import GuruClient
        from src.ui.pages.enablement import EnablementPage
        monkeypatch.setattr(GuruClient, "load_credentials",
                            staticmethod(lambda *a, **kw: ("", "")))
        page = EnablementPage(empty_db, demo=True)
        results = []
        page.analytics_synced.connect(results.append)
        captured = []

        class _FakeThread:
            def __init__(self, target=None, daemon=None, **kw):
                self.target = target

            def start(self):
                captured.append(self.target)

        monkeypatch.setattr(threading, "Thread", _FakeThread)
        page.demo = False
        page._run_analytics_sync()
        captured[0]()                       # run the worker inline, no network
        assert results and results[0]["ok"] is False
        assert "Connect Guru" in results[0]["error"]

    def test_needs_attention_panel_shows_at_most_six_cards(self, qapp):
        """ARTICLE: 'Needs attention — up to six cards'."""
        page = _analytics()
        due = [{"card_id": f"c{i}", "title": f"Card {i}",
                "reason": "unverified", "due_date": "2026-08-01"}
               for i in range(10)]
        page.set_data({"states": {}, "last_sync_at": ""}, [], [], due)
        assert page._due_layout.count() == 6

    def test_open_comments_panel_shows_at_most_eight(self, qapp):
        """ARTICLE: 'Open card comments — up to eight'."""
        page = _analytics()
        comments = [{"comment_id": f"cm{i}", "card_id": "c1",
                     "card_title": "Card", "author": "a@x.com", "text": "hi",
                     "task_id": None} for i in range(12)]
        page.set_data({"states": {}, "last_sync_at": ""}, [], comments, [])
        rendered = [t for t in _labels(page._comments_card)
                    if t.startswith("a@x.com:")]
        assert len(rendered) == 8

    def test_open_comments_are_newest_first(self, empty_db):
        """ARTICLE: 'Open card comments — up to eight, newest first'."""
        conn = empty_db.conn
        _comment_row(conn, "cm-old", created_at=_iso(10))
        _comment_row(conn, "cm-new", created_at=_iso(1))
        assert [c["comment_id"] for c in ga.open_comments(conn)] == [
            "cm-new", "cm-old"]

    def test_comment_rows_show_the_author_and_the_start_of_the_comment(self, qapp):
        """ARTICLE: 'with the author and the start of the comment'."""
        page = _analytics()
        page.set_data(
            {"states": {}, "last_sync_at": ""}, [],
            [{"comment_id": "cm1", "card_id": "c1", "card_title": "SSO Setup",
              "author": "jo@x.com", "text": "X" * 400, "task_id": None}], [])
        snippet = [t for t in _labels(page._comments_card)
                   if t.startswith("jo@x.com:")]
        assert len(snippet) == 1
        assert snippet[0] == "jo@x.com: " + "X" * 120   # truncated preview

    def test_time_window_offers_7_30_90_and_defaults_to_30(self, qapp):
        """ARTICLE: 'a time window of 7, 30 or 90 days (30 by default)'."""
        page = _analytics()
        values = [page._days.itemData(i) for i in range(page._days.count())]
        assert values == [7, 30, 90]
        assert page.days() == 30

    def test_collection_and_card_type_default_to_all(self, qapp):
        """ARTICLE: 'Three dropdowns filter the page: a time window ..., a
        collection, and a card type'."""
        page = _analytics()
        assert page.collection_id() is None and page.domain() is None
        assert page._collection.itemText(0) == "All collections"
        assert page._domain.itemText(0) == "All card types"
        page.set_filters([("co1", "Enablement")], ["how-to"])
        assert page._collection.itemText(1) == "Enablement"
        assert page._domain.itemText(1) == "how-to"

    def test_changing_a_dropdown_emits_filters_changed(self, qapp):
        """ARTICLE: 'Three dropdowns filter the page'."""
        page = _analytics()
        fired = []
        page.filters_changed.connect(lambda: fired.append(1))
        page._days.setCurrentIndex(2)
        assert page.days() == 90 and fired == [1]

    def test_expand_overlay_collapses_on_the_button_and_on_escape(self, qapp):
        """ARTICLE: 'you leave it with the collapse control or the Escape key'."""
        page = _analytics()
        page.set_data({"states": {"TRUSTED": 2}, "last_sync_at": ""}, [], [], [])
        page._open_expand()
        assert not page._overlay.isHidden()
        assert _click(page._overlay, "⤡  Collapse")
        assert page._overlay.isHidden()

        page._open_expand()
        assert not page._overlay.isHidden()
        page._overlay.keyPressEvent(
            QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        assert page._overlay.isHidden()

    def test_expanded_view_carries_only_the_donut_and_the_card_list(self, qapp):
        """ARTICLE: 'The expanded view shows only the donut and the card list
        ... the comments and needs-attention panels are not carried into it'."""
        from src.ui.pages.enablement.mini_charts import DonutChart
        page = _analytics()
        page.set_data(
            {"states": {"STALE": 2}, "last_sync_at": ""},
            [{"card_id": "c1", "title": "Card", "collection": "CX", "views": 9}],
            [{"comment_id": "cm1", "card_id": "c1", "card_title": "Card",
              "author": "a@x.com", "text": "hm", "task_id": None}],
            [{"card_id": "c1", "title": "Card", "reason": "unverified",
              "due_date": "2026-08-01"}])
        page._open_expand()
        assert page._overlay.findChildren(DonutChart)
        assert "9 views" in _labels(page._overlay)
        assert "Create task" not in _buttons(page._overlay)
        assert "Targeted update" not in _buttons(page._overlay)

    def test_the_kpis_and_open_comments_take_no_filter_arguments(self):
        """ARTICLE: the filters 'do not change the four figures at the top, the
        verification donut, or the open-comments panel'."""
        kpi_params = set(inspect.signature(ga.verification_kpis).parameters)
        comment_params = set(inspect.signature(ga.open_comments).parameters)
        assert kpi_params == {"conn"}
        assert comment_params == {"conn", "limit"}
        top_params = set(inspect.signature(ga.top_cards).parameters)
        assert {"days", "collection_id", "domain"} <= top_params

    def test_the_time_window_also_reshapes_the_needs_attention_list(
            self, empty_db):
        """ARTICLE guru-analytics.md: 'The time window re-shapes two panels ...
        it also re-shapes the needs-attention panel.'

        page.py:1802 passes the window into ga.cards_due_for_update(days=...),
        which feeds it to top_cards for the stale_high_traffic reason
        (guru_analytics.py:428) — so a stale card viewed 30 days ago appears
        under a 90-day window but not a 7-day one.
        """
        conn = empty_db.conn
        _verification_row(conn, "c-stale", verification_state="TRUSTED",
                          next_verification_date="", last_modified=_iso(200))
        _view_event(conn, "c-stale", 30, "ev1")
        narrow = ga.cards_due_for_update(conn, days=7)
        wide = ga.cards_due_for_update(conn, days=90)
        assert [c["card_id"] for c in narrow] == []
        assert [c["card_id"] for c in wide] == ["c-stale"]
        assert narrow != wide

    def test_the_open_comments_panel_also_carries_a_per_row_action(self, qapp):
        """ARTICLE guru-analytics.md: 'Two panels offer a per-row action:
        needs-attention starts a targeted update, and each open card comment
        carries a Create task button.'

        The open-comments panel renders a 'Create task' button per
        un-converted comment (analytics.py:448-459).
        """
        page = _analytics()
        page.set_data(
            {"states": {}, "last_sync_at": ""}, [],
            [{"comment_id": "cm1", "card_id": "c1", "card_title": "Card",
              "author": "a@x.com", "text": "hm", "task_id": None}], [])
        assert "Create task" in _buttons(page._comments_card)

    def test_card_type_list_degrades_to_empty_without_the_domain_table(
            self, tmp_path):
        """ARTICLE: 'Card-type data is optional ... the page degrades to an
        empty list rather than failing.'"""
        from src.data.connection_factory import get_connection
        conn = get_connection(tmp_path / "bare.db")
        try:
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("SELECT 1 FROM guru_card_domains").fetchall()
            assert ga.domains(conn) == []
        finally:
            conn.close()

    def test_empty_top_cards_says_there_are_no_usage_events(self, qapp):
        """ARTICLE: 'Top used cards says there are no usage events.'"""
        page = _analytics()
        page.set_data({"states": {}, "last_sync_at": ""}, [], [], [])
        assert any("No usage events" in t for t in _labels(page._top_card))

    def test_the_pull_is_incremental_from_the_last_watermark(self, empty_db):
        """ARTICLE: 'The pull is incremental — it picks up from where the last
        one stopped.'"""
        conn = empty_db.conn
        calls = []

        class _Stub:
            def get_team_id(self):
                return "team-1"

            def get_analytics(self, team_id, from_date=None, **kw):
                calls.append(from_date)
                return [{"type": "card-viewed", "user": "a@x.com",
                         "eventDate": _iso(1),
                         "properties": {"cardId": "c1"}}]

            def get_team_stats(self, team_id):
                return {}

            def list_unverified_cards(self, **kw):
                return []

            def get_card_comments(self, card_id, status=None, **kw):
                return []

        stub = _Stub()
        ga.sync(conn, stub)
        first_watermark = ga._get_state(conn, "events_watermark")
        assert first_watermark
        ga.sync(conn, stub)
        # second pull starts from the watermark (minus the 1-day overlap),
        # not from the full days_back window
        assert calls[1] > calls[0]
        gap = (ga._parse_dt(first_watermark)
               - ga._parse_dt(calls[1])).total_seconds()
        assert 0 < gap <= 90_000

    def test_a_repeat_sync_of_identical_events_inserts_nothing(self, empty_db):
        """ARTICLE: 'Identical numbers after a sync usually mean nothing changed
        in Guru.'"""
        conn = empty_db.conn

        class _Stub:
            events = [{"type": "card-viewed", "user": "a@x.com",
                       "eventDate": _iso(1), "properties": {"cardId": "c1"}}]

            def get_team_id(self):
                return "team-1"

            def get_analytics(self, team_id, from_date=None, **kw):
                return list(self.events)

            def get_team_stats(self, team_id):
                return {}

            def list_unverified_cards(self, **kw):
                return []

            def get_card_comments(self, card_id, status=None, **kw):
                return []

        stub = _Stub()
        assert ga.sync(conn, stub)["sections"]["events"]["inserted"] == 1
        assert ga.sync(conn, stub)["sections"]["events"]["inserted"] == 0


# ══════════════════════════════════════════════════════════════════════
# weak-card-actions.md
# ══════════════════════════════════════════════════════════════════════


class TestWeakCardActions:
    """assets/help/insights/weak-card-actions.md"""

    def test_needs_attention_row_action_emits_the_card_id(self, qapp):
        """ARTICLE: 'Each row ... carries an action that starts a targeted
        update.'"""
        page = _analytics()
        got = []
        page.targeted_update_requested.connect(got.append)
        page.set_data({"states": {}, "last_sync_at": ""}, [], [],
                      [{"card_id": "c-42", "title": "Card", "reason":
                        "unverified", "due_date": "2026-08-01"}])
        assert _click(page._due_card, "Targeted update")
        assert got == ["c-42"]

    def test_needs_attention_reason_tags_match_the_article(self, qapp):
        """ARTICLE: the three tags are 'Unverified', 'Verification due' and
        'High traffic, stale'."""
        page = _analytics()
        page.set_data(
            {"states": {}, "last_sync_at": ""}, [], [],
            [{"card_id": "a", "title": "A", "reason": "unverified",
              "due_date": "2026-08-01"},
             {"card_id": "b", "title": "B", "reason": "verification_due",
              "due_date": "2026-08-02"},
             {"card_id": "c", "title": "C", "reason": "stale_high_traffic",
              "due_date": "2026-08-03"}])
        texts = _labels(page._due_card)
        assert "Unverified" in texts
        assert "Verification due" in texts
        assert "High traffic, stale" in texts

    def test_needs_attention_rows_show_the_due_date(self, qapp):
        """ARTICLE: 'each tagged with why it surfaced ... and its due date'."""
        page = _analytics()
        page.set_data({"states": {}, "last_sync_at": ""}, [], [],
                      [{"card_id": "a", "title": "Alpha",
                        "reason": "unverified", "due_date": "2026-08-01"}])
        texts = _live_labels(page._due_card)
        assert "Alpha" in texts
        assert "due 2026-08-01" in texts

    def test_needs_attention_reads_local_data_with_no_guru_client(self):
        """ARTICLE: 'That panel reads local data, so it will keep showing the
        old state until you sync from Guru again.'"""
        params = inspect.signature(ga.cards_due_for_update).parameters
        assert set(params) == {"conn", "days", "stale_days", "limit"}

    def test_a_card_deleted_in_guru_fails_the_import_rather_than_half_loading(
            self, empty_db):
        """ARTICLE: 'You land in the Workbench but no draft is loaded. The
        import fetched nothing. Confirm the card still exists in Guru.'"""
        from src.data import enablement_store as store

        class _GoneFromGuru:
            def get_card(self, card_id):
                return None

        res = store.import_guru_card_to_draft(
            empty_db.conn, _GoneFromGuru(), "deleted-card")
        assert res["ok"] is False
        assert res["error"].startswith("card_not_found")
        assert store.list_drafts(empty_db.conn) == []

    def test_unverified_tag_covers_needs_verification_and_stale(self, empty_db):
        """ARTICLE: 'Unverified | The card's state is "needs verification" or
        "stale"'."""
        conn = empty_db.conn
        _verification_row(conn, "c-nv", verification_state="NEEDS_VERIFICATION")
        _verification_row(conn, "c-st", verification_state="STALE")
        _verification_row(conn, "c-ok", verification_state="TRUSTED")
        by_id = {c["card_id"]: c for c in ga.cards_due_for_update(conn)}
        assert by_id["c-nv"]["reason"] == "unverified"
        assert by_id["c-st"]["reason"] == "unverified"
        assert "c-ok" not in by_id

    def test_verification_due_tag_uses_the_14_day_horizon(self, empty_db):
        """ARTICLE: 'Verification due | Its next verification date is inside 14
        days'."""
        conn = empty_db.conn
        _verification_row(conn, "c-soon", verification_state="TRUSTED",
                          next_verification_date=_iso_ahead(10))
        _verification_row(conn, "c-later", verification_state="TRUSTED",
                          next_verification_date=_iso_ahead(30))
        by_id = {c["card_id"]: c for c in ga.cards_due_for_update(conn)}
        assert by_id["c-soon"]["reason"] == "verification_due"
        assert "c-later" not in by_id

    def test_high_traffic_stale_tag_needs_about_ninety_days(self, empty_db):
        """ARTICLE: 'High traffic, stale | Heavily viewed, but not modified in
        about 90 days'."""
        assert (inspect.signature(ga.cards_due_for_update)
                .parameters["stale_days"].default == 90)
        conn = empty_db.conn
        _verification_row(conn, "c-old", verification_state="TRUSTED",
                          last_modified=_iso(120))
        _verification_row(conn, "c-fresh", verification_state="TRUSTED",
                          last_modified=_iso(10))
        _view_event(conn, "c-old", 2, "e1")
        _view_event(conn, "c-fresh", 2, "e2")
        by_id = {c["card_id"]: c for c in ga.cards_due_for_update(conn, days=30)}
        assert by_id["c-old"]["reason"] == "stale_high_traffic"
        assert "c-fresh" not in by_id

    def test_targeted_update_import_never_writes_to_guru(self, empty_db):
        """ARTICLE: 'Nothing is written back to Guru at this point — you are
        working on a local draft until you publish it.'"""
        from src.data import enablement_store as store

        class _RecordingGuru:
            def __init__(self):
                self.calls = []

            def __getattr__(self, name):
                def _call(*a, **kw):
                    self.calls.append(name)
                    if name == "get_card":
                        return {"id": a[0], "title": "SSO Setup",
                                "content": "<p>Body</p>"}
                    raise AssertionError(f"unexpected Guru call: {name}")
                return _call

        client = _RecordingGuru()
        res = store.import_guru_card_to_draft(empty_db.conn, client, "card-1")
        assert res["ok"] and res["draft_id"]
        assert client.calls == ["get_card"]   # read only, no write-back

    def test_targeted_update_lands_in_the_workbench_with_the_draft_loaded(
            self, qapp, empty_db):
        """ARTICLE: 'moves you to the Workbench with the draft loaded ... You
        should not have to find the card again yourself.'"""
        from src.ui.pages.enablement import EnablementPage
        page = EnablementPage(empty_db, demo=True)
        page.select_tab("analytics")
        assert page.tabs.currentWidget() is page._tab_widgets["analytics"]
        page._on_targeted_update("demo-card-payments")
        assert page.tabs.currentWidget() is page._tab_widgets["workbench"]
        assert page.workbench.active_draft_id

    def test_targeted_update_without_guru_tells_you_to_connect(
            self, qapp, empty_db):
        """ARTICLE: 'Without saved credentials the app tells you to connect
        Guru rather than opening an empty draft.'"""
        from src.ui.pages.enablement import EnablementPage
        page = EnablementPage(empty_db, demo=True)
        page.demo = False                 # live branch
        page._guru_client = None          # no saved credentials
        before = page.workbench.active_draft_id
        page._on_targeted_update("c-1")
        assert "Connect Guru" in page._status.text()
        assert page.workbench.active_draft_id == before   # no empty draft

    def test_the_sync_outcome_survives_on_the_status_line(
            self, qapp, empty_db):
        """ARTICLE guru-analytics.md: 'The status line shows the outcome of the
        sync ... "Guru analytics synced." is the final status.'

        _on_analytics_synced now calls _load_live FIRST — its last act sets the
        'N tasks · M pending drafts.' status — and writes the sync outcome
        AFTER, so the outcome (success or failure) is what the operator is left
        looking at rather than being clobbered by the reload (finding 21). See
        tests/test_sync_status.py.
        """
        from src.ui.pages.enablement import EnablementPage
        page = EnablementPage(empty_db)
        page._on_analytics_synced({"ok": True})
        assert "synced" in page._status.text().lower()
        assert not page._status.text().endswith("pending drafts.")
        page._on_analytics_synced(
            {"ok": False, "error": "Connect Guru first (Settings → Guru)."})
        assert "Connect Guru" in page._status.text()
        assert not page._status.text().endswith("pending drafts.")

    def test_a_failed_comment_conversion_message_survives_on_the_status_line(
            self, qapp, empty_db):
        """ARTICLE: 'The comment action reports it could not create a task.'

        The failure branch of _on_comment_task does not call _load_live, so
        unlike the sync outcome this message is not overwritten.
        """
        from src.ui.pages.enablement import EnablementPage
        page = EnablementPage(empty_db, demo=True)
        page._on_comment_task("no-such-comment")
        assert "Couldn't create task: comment_not_found" in page._status.text()

    def test_comment_row_offers_create_task_then_shows_a_marker(self, qapp):
        """ARTICLE: 'Once converted, the row shows a marker instead of the
        action.'"""
        page = _analytics()
        got = []
        page.comment_task_requested.connect(got.append)
        base = {"comment_id": "cm1", "card_id": "c1", "card_title": "Card",
                "author": "a@x.com", "text": "hm"}
        page.set_data({"states": {}, "last_sync_at": ""}, [],
                      [dict(base, task_id=None)], [])
        assert "Create task" in _live_buttons(page._comments_card)
        assert _click(page._comments_card, "Create task")
        assert got == ["cm1"]

        page.set_data({"states": {}, "last_sync_at": ""}, [],
                      [dict(base, task_id="t-1")], [])
        assert "Create task" not in _live_buttons(page._comments_card)
        assert "Task created" in _live_labels(page._comments_card)

    def test_comment_conversion_opens_no_dialog(self, qapp, monkeypatch):
        """ARTICLE: 'Comment conversion should be a single click with no
        dialog.'"""
        from PySide6.QtWidgets import QMessageBox
        for name in ("information", "warning", "question", "critical"):
            monkeypatch.setattr(
                QMessageBox, name,
                staticmethod(lambda *a, **kw: pytest.fail("a dialog opened")))
        page = _analytics()
        page.set_data({"states": {}, "last_sync_at": ""}, [],
                      [{"comment_id": "cm1", "card_id": "c1",
                        "card_title": "Card", "author": "a@x.com",
                        "text": "hm", "task_id": None}], [])
        assert _click(page._comments_card, "Create task")

    def test_comment_task_is_titled_after_the_card(self, empty_db):
        """ARTICLE: 'The task is titled after the card the comment is on.'"""
        from src.data import enablement_tasks
        conn = empty_db.conn
        _comment_row(conn, "cm1", card_title="SSO Setup")
        res = ga.create_task_from_comment(conn, "cm1")
        assert res["ok"]
        task = enablement_tasks.get_task(conn, res["task_id"])
        assert "SSO Setup" in task["title"]

    def test_comment_author_and_text_land_in_the_task_summary(self, empty_db):
        """ARTICLE: 'the comment author and text are carried into the task
        summary'."""
        from src.data import enablement_tasks
        conn = empty_db.conn
        _comment_row(conn, "cm1", author="jo@x.com", text="Step 3 is wrong")
        res = ga.create_task_from_comment(conn, "cm1")
        task = enablement_tasks.get_task(conn, res["task_id"])
        assert "jo@x.com" in task["summary"]
        assert "Step 3 is wrong" in task["summary"]

    def test_converting_the_same_comment_twice_returns_the_same_task(
            self, empty_db):
        """ARTICLE: 'Converting the same comment twice does not create a second
        task — the comment remembers the task it produced and returns that
        one.'"""
        conn = empty_db.conn
        _comment_row(conn, "cm1")
        first = ga.create_task_from_comment(conn, "cm1")
        second = ga.create_task_from_comment(conn, "cm1")
        assert second["ok"] and second.get("already") is True
        assert second["task_id"] == first["task_id"]
        n = conn.execute(
            "SELECT COUNT(*) FROM enablement_tasks WHERE kind='card_comment'"
        ).fetchone()[0]
        assert n == 1

    def test_missing_comment_reports_a_failure_rather_than_raising(
            self, empty_db):
        """ARTICLE: 'The comment action reports it could not create a task ...
        the comment is no longer in the local copy.'"""
        res = ga.create_task_from_comment(empty_db.conn, "nope")
        assert res == {"ok": False, "error": "comment_not_found"}

    def test_top_used_card_rows_carry_no_actions(self, qapp):
        """ARTICLE: 'Rows in the top-used-cards list carry no actions ... those
        rows are not clickable.'"""
        page = _analytics()
        page.set_data(
            {"states": {}, "last_sync_at": ""},
            [{"card_id": "c1", "title": "Card", "collection": "CX",
              "views": 9}], [], [])
        assert _buttons(page._top_card) == []
        from PySide6.QtWidgets import QFrame
        # QLabel subclasses QFrame — restrict to the plain row frames
        rows = [f for f in page._top_card.findChildren(QFrame)
                if type(f) is QFrame and f.height() != 5]
        assert rows, "expected rendered rows"
        for row in rows:
            # plain QFrames: no clickable subclass, no python-level handler
            assert type(row) is QFrame
            assert "mousePressEvent" not in type(row).__dict__


# ══════════════════════════════════════════════════════════════════════
# attention-queue.md
# ══════════════════════════════════════════════════════════════════════


def _queue_tab(cards=None, provider=None):
    from src.ui.pages.enablement.attention_queue_tab import AttentionQueueTab
    if provider is None:
        cards = list(cards or [])
        provider = lambda: cards          # noqa: E731
    return AttentionQueueTab(health_provider=provider)


class TestAttentionQueue:
    """assets/help/insights/attention-queue.md"""

    def test_attention_queue_has_its_own_sidebar_entry(self):
        """ARTICLE: 'It has its own sidebar entry, so you can return to it after
        navigating away.'"""
        from src.ui.app_modes import MODE_ENABLEMENT, PAGES
        spec = next(p for p in PAGES if p.page_id == "en_attention")
        assert spec.tab_key == "home"
        assert MODE_ENABLEMENT in spec.modes
        assert spec.hidden is False
        assert spec.title == "Attention Queue"

    def test_the_page_shows_a_computing_message_first(self, qapp):
        """ARTICLE: 'you see a computing message first, then the buckets'."""
        tab = _queue_tab([_health("c1", 0.3, "verification_overdue", "Alpha")])
        assert any("Computing" in t for t in _live_labels(tab))
        assert "Alpha" not in _live_labels(tab)
        tab.reload()
        assert "Alpha" in _live_labels(tab)
        assert not any("Computing" in t for t in _live_labels(tab))

    def test_the_attention_queue_is_the_enablement_landing_page(
            self, qapp, empty_db):
        """ARTICLE: 'The attention queue is the enablement landing page.'"""
        from src.ui.pages.enablement import EnablementPage
        page = EnablementPage(empty_db, demo=True)
        assert page.tabs.currentWidget() is page._tab_widgets["home"]

    def test_the_scoring_pass_runs_in_the_background(self, qapp, monkeypatch):
        """ARTICLE: 'Opening the page starts a scoring pass in the background'
        and 'The scoring pass runs in the background'."""
        from src.ui.pages.enablement.attention_queue_tab import AttentionQueueTab
        started = []

        class _FakeThread:
            def __init__(self, target=None, daemon=None, **kw):
                self.target, self.daemon = target, daemon

            def start(self):
                started.append(self)      # captured, never run — no network

        monkeypatch.setattr(threading, "Thread", _FakeThread)
        tab = AttentionQueueTab()         # no provider → live load path
        tab.reload()
        assert len(started) == 1
        assert started[0].daemon is True
        assert started[0].target == tab._load_worker
        assert any("Computing" in t for t in _live_labels(tab))

    def test_refresh_recomputes_from_scratch(self, qapp):
        """ARTICLE: 'Refreshing recomputes from scratch.'"""
        calls = []

        def provider():
            calls.append(1)
            return [_health("c1", 0.3, "verification_overdue", "Alpha")]

        tab = _queue_tab(provider=provider)
        assert calls == []
        assert _click(tab, "Refresh")
        assert calls == [1]
        assert _click(tab, "Refresh")
        assert calls == [1, 1]

    def test_the_four_bucket_headings_match_the_article(self, qapp):
        """ARTICLE: the four buckets are 'Source changed', 'Verification
        overdue', 'Gap / duplicate' and 'Staged drafts awaiting review'."""
        tab = _queue_tab([
            _health("c1", 0.4, "source_changed", "A"),
            _health("c2", 0.4, "verification_overdue", "B"),
            _health("c3", 0.4, "gap_dup", "C"),
            _health("draft:1", 0.0, "staged", "D"),
        ])
        tab.reload()
        texts = _live_labels(tab)
        for heading in ("SOURCE CHANGED", "VERIFICATION OVERDUE",
                        "GAP / DUPLICATE", "STAGED DRAFTS AWAITING REVIEW"):
            assert heading in texts

    def test_bucket_order_matches_the_article(self):
        """ARTICLE: the buckets are listed source-changed, verification
        overdue, gap/duplicate, staged drafts — 'the worst thing standing
        against it, in the order above'."""
        from src.ui.pages.enablement.attention_queue_tab import _BUCKETS
        assert [b[0] for b in _BUCKETS] == [
            "source_changed", "verification_overdue", "gap_dup", "staged"]

    def test_a_card_appears_in_one_bucket_only(self, qapp):
        """ARTICLE: 'A card that is both overdue and duplicated shows under
        verification overdue and nowhere else.'"""
        from src.ui.pages.enablement.attention_queue_tab import group_by_bucket
        card = score_card(_signals(card_id="c1", days_overdue=10,
                                   duplicate_of=["c2"]))
        assert card.bucket == "verification_overdue"
        groups = group_by_bucket([card])
        assert [c.card_id for c in groups["verification_overdue"]] == ["c1"]
        assert groups["gap_dup"] == []
        assert groups["source_changed"] == []

    def test_worst_score_sorts_to_the_top_of_a_bucket(self, qapp):
        """ARTICLE: 'Within a bucket, the worst score sorts to the top.'"""
        from src.ui.pages.enablement.attention_queue_tab import group_by_bucket
        cards = [_health("good", 0.9, "gap_dup"),
                 _health("bad", 0.2, "gap_dup"),
                 _health("mid", 0.5, "gap_dup")]
        ordered = [c.card_id for c in group_by_bucket(cards)["gap_dup"]]
        assert ordered == ["bad", "mid", "good"]

    def test_each_row_shows_title_score_and_exactly_two_actions(self, qapp):
        """ARTICLE: 'Each row shows the card title, its health score as a
        percentage, and two actions.'"""
        tab = _queue_tab([_health("c1", 0.42, "verification_overdue", "Alpha")])
        tab.reload()
        texts = _live_labels(tab)
        assert "Alpha" in texts
        assert "42%" in texts
        row_buttons = [b for b in _live_buttons(tab) if b != "Refresh"]
        assert row_buttons == ["Open targeted update", "Dismiss"]

    def test_staged_drafts_are_pinned_at_zero_and_sort_first(self, qapp):
        """ARTICLE: 'Staged drafts carry no score of their own, so they sit at
        the top of their section.'"""
        from src.ui.pages.enablement.attention_queue_tab import group_by_bucket
        cards = [_health("draft:2", 0.0, "staged", "Later draft"),
                 _health("draft:1", 0.0, "staged", "Earlier draft")]
        staged = group_by_bucket(cards)["staged"]
        assert all(c.score == 0.0 for c in staged)
        mixed = group_by_bucket(cards + [_health("x", 0.6, "staged")])["staged"]
        assert mixed[-1].card_id == "x"      # scored rows sink below drafts

    def test_staged_rows_are_built_from_pending_local_drafts(self, empty_db):
        """ARTICLE: 'Staged drafts awaiting review | Your own pending drafts,
        not yet published.'"""
        from src.data import enablement_store as store
        from src.ui.pages.enablement.attention_queue_tab import (
            _staged_draft_cards,
        )
        conn = empty_db.conn
        pending_id = store.save_card_draft(
            conn, title="Payments v2 — draft", content="body",
            status="pending")
        store.save_card_draft(conn, title="Already out", content="body",
                              status="published")
        cards = _staged_draft_cards(conn)
        assert [c.card_id for c in cards] == [f"draft:{pending_id}"]
        assert cards[0].bucket == "staged"
        assert cards[0].score == 0.0
        assert cards[0].signals.title == "Payments v2"

    def test_open_action_emits_the_card_id(self, qapp):
        """ARTICLE: 'The first action moves you to the Workbench and opens the
        card.'"""
        tab = _queue_tab([_health("c-7", 0.3, "verification_overdue", "Alpha")])
        tab.reload()
        got = []
        tab.open_update_requested.connect(got.append)
        assert _click(tab, "Open targeted update")
        assert got == ["c-7"]

    def test_open_routes_a_staged_draft_straight_to_its_draft(
            self, qapp, empty_db):
        """ARTICLE: 'A real Guru card is imported as a draft first; a staged
        draft opens directly, since it already exists locally.'"""
        from src.data import enablement_store as store
        from src.ui.pages.enablement import EnablementPage
        page = EnablementPage(empty_db, demo=True)
        draft_id = store.save_card_draft(
            page._conn(), title="Local draft", content="body",
            status="pending")
        page.select_tab("analytics")
        page._on_attention_open(f"draft:{draft_id}")
        assert page.tabs.currentWidget() is page._tab_widgets["workbench"]
        assert page.workbench.active_draft_id == draft_id

    def test_open_routes_a_real_card_through_the_targeted_update_import(
            self, qapp, empty_db):
        """ARTICLE: 'A real Guru card is imported as a draft first.'"""
        from src.ui.pages.enablement import EnablementPage
        page = EnablementPage(empty_db, demo=True)
        page.select_tab("analytics")
        page._on_attention_open("guru-card-1")
        assert page.tabs.currentWidget() is page._tab_widgets["workbench"]
        draft = page.workbench.active_draft_id
        assert draft
        row = page._conn().execute(
            "SELECT source_ref FROM guru_content_drafts WHERE id=?", (draft,)
        ).fetchone()
        assert row[0] == "guru:guru-card-1"

    def test_dismiss_hides_the_row(self, qapp):
        """ARTICLE: 'The second action hides the row.'"""
        tab = _queue_tab([_health("c1", 0.3, "verification_overdue", "Alpha")])
        tab.reload()
        got = []
        tab.dismiss_requested.connect(got.append)
        assert "Alpha" in _live_labels(tab)
        assert _click(tab, "Dismiss")
        assert got == ["c1"]
        assert "Alpha" not in _live_labels(tab)

    def test_dismissal_is_never_written_down(self, qapp):
        """ARTICLE: 'The dismissal is held in memory and is not written down.'"""
        from src.ui.pages.enablement.attention_queue_tab import AttentionQueueTab
        cards = [_health("c1", 0.3, "verification_overdue", "Alpha")]
        tab = _queue_tab(cards)
        tab.reload()
        _click(tab, "Dismiss")
        assert tab._dismissed == {"c1"}
        # a fresh tab (i.e. an app restart) has no memory of the dismissal
        fresh = AttentionQueueTab(health_provider=lambda: list(cards))
        fresh.reload()
        assert fresh._dismissed == set()
        assert "Alpha" in _live_labels(fresh)
        # and nothing in the host persists it
        src = inspect.getsource(AttentionQueueTab._on_dismiss)
        assert "INSERT" not in src.upper()

    def test_a_dismissed_row_returns_after_a_refresh(self, qapp):
        """ARTICLE attention-queue.md: 'Refresh clears a dismissal ... if you
        dismiss a row by mistake, Refresh brings it back.'

        reload() now clears self._dismissed at the start
        (attention_queue_tab.py:120) before recomputing, so a dismissed row
        comes back on an explicit Refresh — the recovery path for a mistaken
        dismiss. See
        tests/test_attention_dismissal.py::test_reload_clears_dismissals_so_a_row_can_return.
        """
        tab = _queue_tab([_health("c1", 0.3, "verification_overdue", "Alpha")])
        tab.reload()
        _click(tab, "Dismiss")
        assert tab._dismissed == {"c1"}
        assert "Alpha" not in _live_labels(tab)
        # an explicit Refresh clears the dismissal and the row returns
        tab.reload()
        assert tab._dismissed == set()
        assert "Alpha" in _live_labels(tab)

    def test_a_dismissed_row_stays_hidden_between_reloads(self, qapp):
        """ARTICLE attention-queue.md: 'Within a session a dismissed row stays
        hidden ... switching to another tab and back leaves it hidden.'

        Between reloads, set_health() filters cards in self._dismissed out of
        the rendered body (attention_queue_tab.py:135-136), so a re-render that
        is NOT a Refresh keeps a dismissed row hidden while a fresh reload is
        what brings it back. See
        tests/test_attention_dismissal.py::test_set_health_still_filters_dismissed_within_a_session.
        """
        cards = [_health("c1", 0.3, "verification_overdue", "Alpha"),
                 _health("c2", 0.4, "verification_overdue", "Beta")]
        tab = _queue_tab(cards)
        tab.reload()
        _click(tab, "Dismiss")                # worst score first → dismisses c1
        assert tab._dismissed == {"c1"}
        assert "Alpha" not in _live_labels(tab)
        # a plain re-render (not a Refresh) keeps the dismissal in force
        tab.set_health(cards)
        assert tab._dismissed == {"c1"}
        assert "Alpha" not in _live_labels(tab)
        assert "Beta" in _live_labels(tab)

    def test_healthy_cards_are_never_displayed(self, qapp):
        """ARTICLE: 'Cards that score as healthy are never displayed.'"""
        from src.ui.pages.enablement.attention_queue_tab import group_by_bucket
        healthy = score_card(_signals(card_id="c-ok", view_count=80))
        assert healthy.bucket == "healthy"
        assert healthy.score == pytest.approx(1.0)
        assert all(not v for v in group_by_bucket([healthy]).values())
        tab = _queue_tab([healthy])
        tab.reload()
        assert "c-ok" not in _live_labels(tab)

    def test_nothing_qualifying_shows_a_message_not_empty_sections(self, qapp):
        """ARTICLE: 'When nothing qualifies, the page says so rather than
        showing empty sections.'"""
        tab = _queue_tab([])
        tab.reload()
        texts = _live_labels(tab)
        assert any("All clear" in t for t in texts)
        assert "SOURCE CHANGED" not in texts

    def test_missing_guru_credentials_ask_you_to_connect(self, qapp, monkeypatch):
        """ARTICLE: 'The page asks you to connect Guru ... Without saved
        credentials there is nothing to score.'"""
        from src.data.guru_client import GuruClient
        from src.ui.pages.enablement.attention_queue_tab import (
            AttentionQueueTab, _NotConnected,
        )
        monkeypatch.setattr(GuruClient, "load_credentials",
                            staticmethod(lambda *a, **kw: ("", "")))
        with pytest.raises(_NotConnected):
            AttentionQueueTab._compute_off_thread()

        tab = _queue_tab([])
        tab._on_failed("not_connected")
        assert any("Connect Guru" in t for t in _live_labels(tab))

    def test_a_load_failure_is_reported_in_place_of_the_buckets(self, qapp):
        """ARTICLE: 'An error message appears in place of the buckets. The page
        reports the failure rather than crashing.'"""
        tab = _queue_tab([_health("c1", 0.3, "verification_overdue", "Alpha")])
        tab.reload()
        tab._on_failed("Guru API error 503")
        texts = _live_labels(tab)
        assert any("Couldn't load attention queue: Guru API error 503" in t
                   for t in texts)
        assert "Alpha" not in texts

    def test_source_changed_and_gap_cannot_fire_with_an_empty_catalog(
            self, empty_db):
        """ARTICLE: 'With an empty or very small catalog neither can ever
        trigger, and only overdue cards and staged drafts will appear.'"""
        from src.data.enablement_health.signals import gather_signals
        queue = [{"id": "c1", "title": "Card 1",
                  "verification_state": "NEEDS_VERIFICATION",
                  "next_verification_date": _iso(5)}]
        signals = gather_signals(empty_db.conn, _FakeGuruSignals(queue=queue))
        assert len(signals) == 1
        assert signals[0].source_changed is False
        assert signals[0].gap is False
        assert signals[0].duplicate_of == []
        assert score_card(signals[0]).bucket == "verification_overdue"

    def test_only_cards_in_the_verification_queue_are_scored(self, empty_db):
        """ARTICLE: 'Only cards in Guru's verification queue are scored. A card
        that carries no verification state is invisible to this page.'"""
        from src.data.content_catalog import upsert_entry
        from src.data.content_catalog.models import CatalogEntry
        from src.data.enablement_health.signals import gather_signals
        conn = empty_db.conn
        for cid in ("c-queued", "c-unqueued"):
            upsert_entry(conn, CatalogEntry(
                item_id=cid, item_type="card", title=cid, source="guru",
                summary="body", topics=["x"], updated_at=_iso(2)))
        queue = [{"id": "c-queued", "title": "Queued",
                  "verification_state": "STALE",
                  "next_verification_date": _iso(3)}]
        signals = gather_signals(conn, _FakeGuruSignals(queue=queue))
        assert [s.card_id for s in signals] == ["c-queued"]


# ══════════════════════════════════════════════════════════════════════
# card-health-scoring.md
# ══════════════════════════════════════════════════════════════════════


class TestCardHealthScoring:
    """assets/help/insights/card-health-scoring.md"""

    def test_component_weights_match_the_article(self):
        """ARTICLE: freshness 40%, demand 25%, open comments 15%, content
        health 20%."""
        assert score_mod.W_FRESHNESS == 0.40
        assert score_mod.W_DEMAND == 0.25
        assert score_mod.W_COMMENTS == 0.15
        assert score_mod.W_CONTENT == 0.20
        total = (score_mod.W_FRESHNESS + score_mod.W_DEMAND
                 + score_mod.W_COMMENTS + score_mod.W_CONTENT)
        assert total == pytest.approx(1.0)

    def test_freshness_is_full_on_time_and_zero_at_thirty_days_overdue(self):
        """ARTICLE: 'Verification freshness | Full marks when not past due |
        Zero when 30 days overdue or more'."""
        comp = lambda d: score_card(_signals(days_overdue=d)).components  # noqa: E731
        assert comp(0)["freshness"] == 1.0
        assert comp(30)["freshness"] == 0.0
        assert comp(90)["freshness"] == 0.0

    def test_freshness_falls_off_in_a_straight_line(self):
        """ARTICLE: 'Freshness falls off in a straight line across those 30
        days, so a card five days overdue keeps most of its freshness.'"""
        comp = lambda d: score_card(_signals(days_overdue=d)).components  # noqa: E731
        assert comp(5)["freshness"] == pytest.approx(1 - 5 / 30)
        assert comp(15)["freshness"] == pytest.approx(0.5)
        # linear: equal steps produce equal drops
        step_a = comp(5)["freshness"] - comp(10)["freshness"]
        step_b = comp(20)["freshness"] - comp(25)["freshness"]
        assert step_a == pytest.approx(step_b)

    def test_demand_is_zero_unviewed_and_full_around_fifty_views(self):
        """ARTICLE: 'Demand | Full marks when around 50 views or more | Zero
        when never viewed'."""
        comp = lambda v: score_card(_signals(view_count=v)).components  # noqa: E731
        assert comp(0)["demand"] == 0.0
        assert comp(50)["demand"] == pytest.approx(1.0)
        assert comp(5000)["demand"] == 1.0      # saturates, never overflows

    def test_demand_rises_quickly_then_flattens(self):
        """ARTICLE: 'the difference between 2 and 20 views matters far more
        than the difference between 200 and 2,000'."""
        d = lambda v: score_card(_signals(view_count=v)).components["demand"]  # noqa: E731
        early = d(20) - d(2)
        late = d(2000) - d(200)
        assert early > late
        assert late == pytest.approx(0.0)       # both already saturated at 1.0

    def test_open_comments_full_when_none_and_zero_at_five(self):
        """ARTICLE: 'Open comments | Full marks when no open comments | Zero
        when five or more open'."""
        comp = lambda n: score_card(_signals(open_comment_count=n)).components  # noqa: E731
        assert comp(0)["comments"] == 1.0
        assert comp(5)["comments"] == 0.0
        assert comp(9)["comments"] == 0.0
        assert comp(1)["comments"] == pytest.approx(0.8)   # linear across five

    def test_content_health_loses_half_per_problem(self):
        """ARTICLE: 'Content health starts full and loses half for being a
        near-duplicate ... and half for leaving a document only partly
        covered' — zero when both."""
        comp = lambda **kw: score_card(_signals(**kw)).components  # noqa: E731
        assert comp()["content_health"] == 1.0
        assert comp(duplicate_of=["c2"])["content_health"] == pytest.approx(0.5)
        assert comp(gap=True)["content_health"] == pytest.approx(0.5)
        assert comp(duplicate_of=["c2"], gap=True)["content_health"] == 0.0

    def test_score_is_a_weighted_blend_of_the_four_components(self):
        """ARTICLE: 'Four components are measured, each scored from 0 to 1,
        then blended by fixed weights.'"""
        health = score_card(_signals(days_overdue=15, view_count=50,
                                     open_comment_count=1, gap=True))
        c = health.components
        expected = (0.40 * c["freshness"] + 0.25 * c["demand"]
                    + 0.15 * c["comments"] + 0.20 * c["content_health"])
        assert health.score == pytest.approx(expected)
        assert set(c) == {"freshness", "demand", "comments", "content_health"}

    def test_score_stays_between_zero_and_one(self):
        """ARTICLE: 'a single health score from 0 to 100 percent'."""
        worst = score_card(_signals(days_overdue=999, view_count=0,
                                    open_comment_count=99,
                                    duplicate_of=["x"], gap=True))
        best = score_card(_signals(view_count=100_000))
        assert worst.score == 0.0
        assert best.score == 1.0

    def test_an_unviewed_but_otherwise_perfect_card_scores_seventy_five(self):
        """ARTICLE: 'a perfectly fresh, uncommented, unduplicated card that has
        never been opened scores about 75 percent, while a heavily-used card in
        the same condition scores 100'."""
        assert score_card(_signals(view_count=0)).score == pytest.approx(0.75)
        assert score_card(_signals(view_count=500)).score == pytest.approx(1.0)

    def test_without_usage_data_scores_compress_into_the_top_quarter(self):
        """ARTICLE: 'With no usage data synced, demand is zero for every card
        and the scores compress into the top three-quarters.'"""
        cards = [score_card(_signals(card_id=f"c{i}", view_count=0,
                                     days_overdue=i * 3,
                                     open_comment_count=i % 3))
                 for i in range(8)]
        assert max(c.score for c in cards) <= 0.75

    def test_two_cards_with_the_same_problem_rank_by_popularity(self):
        """ARTICLE: 'Two cards with the same problem will rank by popularity,
        with the obscure one first.'"""
        obscure = score_card(_signals(card_id="obscure", days_overdue=10,
                                      view_count=0))
        popular = score_card(_signals(card_id="popular", days_overdue=10,
                                      view_count=200))
        assert obscure.bucket == popular.bucket == "verification_overdue"
        assert obscure.score < popular.score

    def test_bucket_priority_is_source_then_overdue_then_gap_dup(self):
        """ARTICLE: 'The bucket is chosen separately, by the first thing that
        applies: a changed source document, then an overdue verification, then
        a duplicate or gap.'"""
        everything = dict(days_overdue=10, duplicate_of=["c2"], gap=True)
        assert score_card(_signals(source_changed=True,
                                   **everything)).bucket == "source_changed"
        assert score_card(_signals(**everything)).bucket == "verification_overdue"
        assert score_card(_signals(duplicate_of=["c2"])).bucket == "gap_dup"
        assert score_card(_signals(gap=True)).bucket == "gap_dup"

    def test_a_card_with_no_problems_is_healthy(self):
        """ARTICLE: 'A card with none of these is healthy and is not shown at
        all.'"""
        assert score_card(_signals()).bucket == "healthy"

    def test_the_score_does_not_decide_the_bucket(self):
        """ARTICLE: 'The score decides the order within a bucket; it does not
        decide which bucket a card lands in.'"""
        high = score_card(_signals(card_id="hi", view_count=500, gap=True))
        low = score_card(_signals(card_id="lo", view_count=0, gap=True))
        assert high.bucket == low.bucket == "gap_dup"
        assert high.score > low.score
        # and a high scorer is still queued rather than filtered out by score
        assert high.score > 0.75

    def test_duplicate_detection_compares_title_summary_and_topics(
            self, empty_db):
        """ARTICLE: 'The comparison is based on title, summary and topics.'"""
        from src.data.content_catalog import upsert_entry
        from src.data.content_catalog.models import CatalogEntry
        from src.data.enablement_health.signals import gather_signals
        conn = empty_db.conn
        wording = "provider payments remittance ledger era matching rollout"
        for cid in ("c-a", "c-b"):
            upsert_entry(conn, CatalogEntry(
                item_id=cid, item_type="card", title="Payments ledger",
                source="guru", summary=wording, topics=["payments", "era"],
                updated_at=_iso(2)))
        upsert_entry(conn, CatalogEntry(
            item_id="c-other", item_type="card", title="Login SSO setup",
            source="guru", summary="single sign on okta saml configuration",
            topics=["auth"], updated_at=_iso(2)))
        queue = [{"id": cid, "title": cid, "verification_state": "TRUSTED",
                  "next_verification_date": ""}
                 for cid in ("c-a", "c-b", "c-other")]
        by_id = {s.card_id: s
                 for s in gather_signals(conn, _FakeGuruSignals(queue=queue))}
        assert by_id["c-a"].duplicate_of == ["c-b"]
        assert by_id["c-b"].duplicate_of == ["c-a"]
        assert by_id["c-other"].duplicate_of == []

    def test_scores_drift_as_the_days_past_due_keep_climbing(self, empty_db):
        """ARTICLE: 'A score changes when you did not touch the card. Normal.
        ... the days past due keep climbing.'"""
        from src.data.enablement_health.signals import gather_signals
        conn = empty_db.conn
        due = datetime(2026, 6, 1, tzinfo=timezone.utc)
        queue = [{"id": "c1", "title": "Card", "verification_state": "STALE",
                  "next_verification_date": due.isoformat()}]
        early = gather_signals(conn, _FakeGuruSignals(queue=queue),
                               now=due + timedelta(days=3))[0]
        later = gather_signals(conn, _FakeGuruSignals(queue=queue),
                               now=due + timedelta(days=20))[0]
        assert later.days_overdue > early.days_overdue
        assert score_card(later).score < score_card(early).score

    def test_duplicate_detection_is_similarity_not_identity(self):
        """ARTICLE: 'it finds things that are alike, not things that are
        identical'."""
        from src.data.enablement_health import signals as sig_mod
        assert 0 < sig_mod._DUP_THRESHOLD < 1.0

    def test_the_row_renders_the_score_as_a_whole_percentage(self, qapp):
        """ARTICLE: 'Every row in the attention queue carries a percentage.'"""
        from src.ui.pages.enablement.attention_queue_tab import AttentionQueueTab
        tab = AttentionQueueTab(health_provider=list)
        assert tab._score_label(0.755).text() == "76%"
        assert tab._score_label(0.0).text() == "0%"
        assert tab._score_label(1.0).text() == "100%"

    def test_staged_drafts_are_not_scored(self):
        """ARTICLE: 'Staged drafts are not scored. They are pinned at zero.'"""
        from src.ui.pages.enablement.attention_queue_tab import (
            _staged_draft_cards,
        )
        src = inspect.getsource(_staged_draft_cards)
        assert "score=0.0" in src
        assert "score_card" not in src

    def test_a_genuine_card_at_worst_case_also_scores_zero(self):
        """ARTICLE card-health-scoring.md: 'a genuine card also reaches exactly
        zero when it is worst-case on every signal at once — 30 or more days
        overdue, never viewed, five or more open comments, and both a duplicate
        and a gap.'

        A real card at worst case scores exactly 0.0 — the same value staged
        drafts are pinned to (score.py:86-92) — so a zero alone cannot be
        treated as proof of a bug.
        """
        worst = score_card(_signals(card_id="real-card", days_overdue=45,
                                    view_count=0, open_comment_count=6,
                                    duplicate_of=["c2"], gap=True))
        assert worst.score == 0.0
