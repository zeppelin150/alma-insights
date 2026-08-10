"""Accuracy audit for the Help Center section "Plan — Calendar and Tasks".

Every test settles one falsifiable claim made by an article under
``assets/help/plan/``. Each test's docstring names the article and quotes (or
closely paraphrases) the claim it settles.

Where the article claims X and the code does NOT-X, the test still asserts the
ARTICLE's claim and is marked ``xfail(strict=True)`` with the real behaviour in
the reason string — so the suite stays green while every discrepancy stays
tracked and will start failing loudly the moment the code is corrected.

Articles covered:
  calendar-views.md · card-due-chips.md · tasks-board.md · task-writeback.md
  asana-conflicts.md · asana-setup.md · drag-reschedule.md

No network, no credentials, no QtWebEngine. Run with:
    python -m pytest tests/test_help_claims_plan.py -q
"""

from __future__ import annotations

import datetime as _dt
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton

from src.data import asana_setup, asana_writeback as awb
from src.data import enablement_tasks as et
from src.data import guru_analytics as ga
from src.services.enablement_web import CalendarWebController
from src.ui.pages.enablement import calendar as calendar_mod
from src.ui.pages.enablement import tasks as tasks_mod
from src.ui.pages.enablement.calendar import CalendarPage, _Chip
from src.ui.pages.enablement.page import EnablementPage
from src.ui.pages.enablement.task_detail import TaskDetailPanel
from src.ui.pages.enablement.tasks import TasksPage, _Check
from src.ui.web.calendar_bridge import CalendarBridge
from src.ui.web.web_flags import VALID_MODES, web_tabs_mode

pytestmark = pytest.mark.ui


# ── fixtures / helpers ────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    """Module-scoped QApplication (offscreen; never screenshot-based)."""
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def conn(empty_db):
    """A real warehouse connection with the enablement schema in place."""
    return empty_db.conn


@pytest.fixture(autouse=True)
def _no_real_asana_store(monkeypatch):
    """Never let a test fall through to a real stored Asana key / live client.

    ``asana_writeback._client(None)`` and ``asana_setup.discover()`` both read
    the credential store; pinning them keeps this suite hermetic on a machine
    that happens to have a key installed.
    """
    from src.data import asana_client as ac
    monkeypatch.setattr(ac.AsanaClient, "from_store",
                        classmethod(lambda cls: SimpleNamespace(api_key="")))
    from src.data import pat_store
    monkeypatch.setattr(pat_store, "load_setting", lambda key, default=None: "")


def _make_task(conn, **kw) -> str:
    """Create an enablement task; defaults to an Asana-linked one."""
    params = dict(source="asana", kind="request", title="Test task",
                  source_ref="9001", due_date="2026-07-01", priority="normal")
    params.update(kw)
    return et.create_task(conn, **params)


class FakeAsanaClient:
    """Records calls; returns canned Asana-shaped responses."""

    def __init__(self, *, modified_at="2026-07-01T00:00:00.000Z",
                 fail_on=(), subtask_gid="55501"):
        self.api_key = "test-key"
        self._modified_at = modified_at
        self._fail_on = set(fail_on)
        self._subtask_gid = subtask_gid
        self.calls: list[tuple] = []

    def _maybe_fail(self, name):
        if name in self._fail_on:
            raise RuntimeError(f"asana {name} exploded")

    def get_task(self, gid, opt_fields=None):
        self.calls.append(("get_task", gid))
        self._maybe_fail("get_task")
        return {"gid": gid, "modified_at": self._modified_at}

    def create_subtask(self, parent_gid, text):
        self.calls.append(("create_subtask", parent_gid, text))
        self._maybe_fail("create_subtask")
        return {"gid": self._subtask_gid}

    def add_comment(self, gid, text):
        self.calls.append(("add_comment", gid, text))
        self._maybe_fail("add_comment")
        return {"gid": "77701"}

    def update_due_date(self, gid, due_on):
        self.calls.append(("update_due_date", gid, due_on))
        self._maybe_fail("update_due_date")
        return {"gid": gid, "modified_at": "2026-07-09T00:00:00.000Z"}

    def update_task(self, gid, **fields):
        self.calls.append(("update_task", gid, fields))
        self._maybe_fail("update_task")
        return {"gid": gid, "modified_at": "2026-07-09T00:00:00.000Z"}

    def names(self):
        return [c[0] for c in self.calls]


def _cell_chips(cell) -> list[_Chip]:
    return cell.findChildren(_Chip)


def _day_number(cell) -> str:
    """The day-number label a calendar cell renders (always its first QLabel)."""
    for lbl in cell.findChildren(QLabel):
        if isinstance(lbl, _Chip):
            continue
        return lbl.text()
    return ""


def _grid_cell(page: CalendarPage, row: int, col: int):
    return page._grid_widget.layout().itemAtPosition(row, col).widget()


def _buttons(widget, text_startswith: str) -> list[QPushButton]:
    return [b for b in widget.findChildren(QPushButton)
            if b.text().startswith(text_startswith)]


def _n_receivers(obj, signal_signature: str) -> int:
    """Connected-slot count for a signal (PySide6 wants the '2name()' form)."""
    return obj.receivers(signal_signature)


# ══════════════════════════════════════════════════════════════════════
# calendar-views.md — "The Calendar: month, week, and scope"
# ══════════════════════════════════════════════════════════════════════

class TestCalendarViews:

    def test_month_and_week_toggle_buttons_switch_the_grid(self, qapp):
        """calendar-views.md: "Two buttons at the top right switch the grid
        between a full month and a single week"."""
        page = CalendarPage()
        assert page._mo_btn.text() == "Month" and page._wk_btn.text() == "Week"
        assert page._view == "month"
        month_rows = page._grid_widget.layout().rowCount()

        page._wk_btn.click()
        assert page._view == "week"
        # A week grid is one header row + exactly one day row.
        assert page._grid_widget.layout().rowCount() == 2
        assert month_rows > 2

        page._mo_btn.click()
        assert page._view == "month"

    def test_both_views_start_the_week_on_sunday(self, qapp, monkeypatch):
        """calendar-views.md: "Both start the week on Sunday"."""
        # 2026-07-15 is a Wednesday; 2026-07-01 is also a Wednesday.
        monkeypatch.setattr(calendar_mod, "_today", lambda: _dt.date(2026, 7, 15))
        page = CalendarPage()
        assert calendar_mod.WEEK[0] == "SUN"

        # Month view: July 1 2026 (a Wednesday) must land in column 3 — only
        # true when the first column is Sunday.
        assert _day_number(_grid_cell(page, 1, 3)) == "1"

        # Week view: the row starts on Sunday 2026-07-12 and ends 2026-07-18.
        page._set_view("week")
        assert _day_number(_grid_cell(page, 1, 0)) == "12"
        assert _day_number(_grid_cell(page, 1, 6)) == "18"

    def test_arrows_move_one_month_at_a_time_and_are_unbounded(self, qapp, monkeypatch):
        """calendar-views.md: the arrows "move you a month at a time" and
        "Month navigation should be unbounded"."""
        monkeypatch.setattr(calendar_mod, "_today", lambda: _dt.date(2026, 7, 15))
        page = CalendarPage()
        page._change_month(1)
        assert (page._year, page._month) == (2026, 8)
        page._change_month(-1)
        assert (page._year, page._month) == (2026, 7)

        # Unbounded: page far back and far forward across year boundaries.
        for _ in range(30):
            page._change_month(-1)
        assert (page._year, page._month) == (2024, 1)
        assert page._month_label.text() == "January 2024"
        for _ in range(60):
            page._change_month(1)
        assert (page._year, page._month) == (2029, 1)

    def test_month_navigation_from_week_view_returns_to_month_view(self, qapp):
        """calendar-views.md: "switching months while you are in the week view
        puts you back in the month view"."""
        page = CalendarPage()
        page._set_view("week")
        assert page._view == "week"
        page._change_month(1)
        assert page._view == "month"

    def test_week_view_ignores_the_month_you_paged_to(self, qapp, monkeypatch):
        """calendar-views.md ("If it doesn't"): "the week view is always
        anchored to the real current week and ignores the month you paged to"."""
        monkeypatch.setattr(calendar_mod, "_today", lambda: _dt.date(2026, 7, 15))
        page = CalendarPage()
        page._year, page._month = 2027, 1          # paged far away
        page._set_view("week")
        # Still the real current week (Sun 12 July 2026 .. Sat 18 July 2026).
        assert _day_number(_grid_cell(page, 1, 0)) == "12"
        assert _day_number(_grid_cell(page, 1, 6)) == "18"

    def test_legend_names_the_four_tints(self, qapp):
        """calendar-views.md: "the legend along the top spells out the four
        tints: Drive, Guru, Asana, and one for high priority or due items"."""
        page = CalendarPage()
        texts = {lbl.text() for lbl in page.findChildren(QLabel)}
        assert {"Drive", "Guru", "Asana", "Due / high"} <= texts

    def test_today_is_circled_and_other_days_are_not(self, qapp, monkeypatch):
        """calendar-views.md: "the current day should stay circled when you land
        back on this month"."""
        monkeypatch.setattr(calendar_mod, "_today", lambda: _dt.date(2026, 7, 15))
        page = CalendarPage()

        def circled(cell) -> bool:
            for lbl in cell.findChildren(QLabel):
                if lbl.minimumWidth() == 22 and "border-radius:11px" in lbl.styleSheet():
                    return True
            return False

        today_cell = next(c for c in (_grid_cell(page, r, col)
                                      for r in range(1, page._grid_widget.layout().rowCount())
                                      for col in range(7))
                          if _day_number(c) == "15")
        other_cell = next(c for c in (_grid_cell(page, r, col)
                                      for r in range(1, page._grid_widget.layout().rowCount())
                                      for col in range(7))
                          if _day_number(c) == "16")
        assert circled(today_cell)
        assert not circled(other_cell)

        # Page away and back — today is still circled.
        page._change_month(1)
        page._change_month(-1)
        again = next(c for c in (_grid_cell(page, r, col)
                                 for r in range(1, page._grid_widget.layout().rowCount())
                                 for col in range(7))
                     if _day_number(c) == "15")
        assert circled(again)

    def test_day_cell_holds_three_chips_then_collapses_to_plus_n_more(self, qapp, monkeypatch):
        """calendar-views.md: "A day cell holds three chips. On a busier day you
        see the first two plus a '+N more' chip"."""
        monkeypatch.setattr(calendar_mod, "_today", lambda: _dt.date(2026, 7, 15))
        page = CalendarPage()

        def cell_for(n_tasks):
            page.set_tasks([{"title": f"Task {i}", "due_date": "2026-07-20",
                             "source": "asana", "task_id": f"t{i}"}
                            for i in range(n_tasks)])
            return next(c for c in (_grid_cell(page, r, col)
                                    for r in range(1, page._grid_widget.layout().rowCount())
                                    for col in range(7))
                        if _day_number(c) == "20")

        three = _cell_chips(cell_for(3))
        assert len(three) == 3
        assert not any(c.text().endswith("more") for c in three)

        five = _cell_chips(cell_for(5))
        assert len(five) == 3                      # two tasks + the expander
        assert [c.text() for c in five[:2]] == ["Task 0", "Task 1"]
        assert five[2].text() == "+3 more"

    def test_plus_n_more_emits_the_iso_date_for_the_day_drilldown(self, qapp, monkeypatch):
        """calendar-views.md: clicking "+N more" "opens a list of that day's
        tasks in the side panel"."""
        monkeypatch.setattr(calendar_mod, "_today", lambda: _dt.date(2026, 7, 15))
        page = CalendarPage()
        page.set_tasks([{"title": f"Task {i}", "due_date": "2026-07-20",
                         "source": "asana", "task_id": f"t{i}"} for i in range(5)])
        seen: list[str] = []
        page.day_expanded.connect(seen.append)
        cell = next(c for c in (_grid_cell(page, r, col)
                                for r in range(1, page._grid_widget.layout().rowCount())
                                for col in range(7))
                    if _day_number(c) == "20")
        more = [c for c in _cell_chips(cell) if c.text().endswith("more")][0]
        more.clicked.emit()
        assert seen == ["2026-07-20"]

    def test_day_drilldown_lists_tasks_and_each_entry_opens_its_detail(self, qapp):
        """calendar-views.md: "clicking any entry there opens its detail"."""
        opened: list[dict] = []
        drill = MagicMock()
        stub = SimpleNamespace(
            _drilldown=drill,
            _all_tasks=[{"task_id": "t1", "title": "Alpha", "due_iso": "2026-07-20"},
                        {"task_id": "t2", "title": "Beta", "due_iso": "2026-07-20"},
                        {"task_id": "t3", "title": "Gamma", "due_iso": "2026-07-21"}],
            _show_task_detail=opened.append,
        )
        EnablementPage._on_calendar_day(stub, "2026-07-20")

        title, subtitle, panel = drill.show_widget.call_args[0]
        assert title == "2026-07-20" and subtitle == "2 tasks"
        rows = panel.findChildren(QPushButton)
        assert [b.text() for b in rows] == ["Alpha", "Beta"]
        rows[1].click()
        assert opened and opened[0]["task_id"] == "t2"

    def test_chip_click_opens_the_detail_panel_not_the_workbench(self, qapp):
        """calendar-views.md: "Clicking a chip opens the task detail panel in the
        side panel — not the Workbench"."""
        stub = SimpleNamespace(_show_task_detail=MagicMock(),
                               _on_targeted_update=MagicMock())
        EnablementPage._on_calendar_event_activated(
            stub, {"task_id": "t1", "title": "Alpha", "source": "asana"})
        stub._show_task_detail.assert_called_once()
        stub._on_targeted_update.assert_not_called()

    def test_chip_titles_are_truncated_to_twenty_characters(self, qapp):
        """calendar-views.md: "Chip titles are truncated to fit the cell, so a
        long task title will be cut short"."""
        page = CalendarPage()
        long_title = "A really quite extraordinarily long enablement task title"
        page.set_tasks([{"title": long_title, "due_date": "2026-07-20",
                         "source": "asana", "task_id": "t1"}])
        label = page._events["2026-07-20"][0][0]
        assert label == long_title[:20]
        assert len(label) < len(long_title)

    def test_calendar_is_read_only_and_never_writes_to_asana(self, qapp):
        """calendar-views.md: "It is a read-and-open surface — clicking takes you
        somewhere, it does not change anything in Asana"."""
        import inspect
        src = inspect.getsource(calendar_mod)
        for forbidden in ("asana_writeback", "asana_client", "update_due_in_asana"):
            assert forbidden not in src
        # Every outbound signal is a notification; none carries a write.
        assert set(n for n in dir(CalendarPage) if n.endswith("_clicked")
                   or n.endswith("_changed") or n.endswith("_activated")
                   or n.endswith("_expanded")) == {
            "event_clicked", "event_activated", "scope_changed", "day_expanded"}

    def test_sample_chips_are_june_2026_demo_data_before_real_tasks_load(self, qapp):
        """calendar-views.md: "The Calendar falls back to a small set of sample
        chips before real data loads, which is what demo mode shows" — and the
        sample dates are the June 2026 the article warns about."""
        page = CalendarPage()
        assert page._live is False
        assert page._events, "the calendar starts with sample chips"
        assert all(iso.startswith("2026-06") for iso in page._events)
        page.set_tasks([{"title": "Real", "due_date": "2026-07-20",
                         "source": "asana", "task_id": "t1"}])
        assert page._live is True
        assert list(page._events) == ["2026-07-20"]

    def test_unmatched_chip_opens_a_panel_of_dashes(self, qapp):
        """calendar-views.md ("If it doesn't"): "A chip opens a detail panel with
        no description, no assignee, and dashes everywhere. The panel could not
        match the chip to a real task"."""
        stub = SimpleNamespace(_all_tasks=[{"title": "Something else"}])
        task = EnablementPage._find_task(stub, "Unknown chip")
        assert task["assignee"] == "—" and task["due"] == "—"
        assert task["subtasks"] == [] and "description" not in task

    def test_mine_falls_back_to_all_tasks_when_identity_is_unknown(self, monkeypatch):
        """calendar-views.md: "if the app does not know who you are, Mine shows
        everything rather than showing nothing"."""
        from src.data import enablement_identity as ident
        monkeypatch.setattr(ident, "operator_identity",
                            lambda: {"email": "", "name": "", "asana_gid": ""})
        stub = SimpleNamespace(_task_scope="mine")
        assert EnablementPage._list_filters(stub) == {}, "no identity → no filter"

        # An email-only identity is deliberately NOT turned into a name filter.
        monkeypatch.setattr(ident, "operator_identity",
                            lambda: {"email": "a@b.com", "name": "", "asana_gid": ""})
        assert EnablementPage._list_filters(stub) == {}

        # With an identity, Mine really does filter.
        monkeypatch.setattr(ident, "operator_identity",
                            lambda: {"email": "a@b.com", "name": "Jo Rivera",
                                     "asana_gid": "12345"})
        assert EnablementPage._list_filters(stub) == {
            "assignee_gid": "12345", "assignee": "Jo Rivera"}

    def test_mine_filter_actually_narrows_the_query(self, conn):
        """calendar-views.md: with an identity set, Mine and All differ (the
        fallback is the *only* reason they would look identical)."""
        et.update_task(conn, _make_task(conn, title="Mine by gid"),
                       assignee="Jo Rivera")
        et.update_task(conn, _make_task(conn, title="Mine by gid 2",
                                        source_ref="9002"), assignee_gid="12345")
        et.update_task(conn, _make_task(conn, title="Someone else's",
                                        source_ref="9003"),
                       assignee="Other Person")
        all_rows = et.list_tasks(conn)
        mine = et.list_tasks(conn, assignee_gid="12345", assignee="Jo Rivera")
        assert len(all_rows) == 3
        assert {r["title"] for r in mine} == {"Mine by gid", "Mine by gid 2"}

    def test_scope_is_shared_between_calendar_and_tasks_without_echoing(self, qapp, monkeypatch):
        """calendar-views.md: "switching to Mine on the Calendar switches the
        Tasks tab too" — and tasks-board.md: "Changing it here changes it there"."""
        from src.data import settings_manager
        saved: list[dict] = []
        monkeypatch.setattr(settings_manager, "update_section",
                            lambda section, values: saved.append({section: values}))
        cal, tasks = CalendarPage(), TasksPage()
        echoes: list[str] = []
        cal.scope_changed.connect(echoes.append)
        tasks.scope_changed.connect(echoes.append)

        stub = SimpleNamespace(_task_scope="mine", calendar=cal, tasks=tasks,
                               _load_live=MagicMock())
        EnablementPage._on_scope_changed(stub, "all")

        assert cal._scope == "all" and tasks._scope == "all"
        assert echoes == [], "host set_scope must not echo scope_changed (no loop)"
        assert stub._load_live.called, "the shared feed is re-queried"
        assert saved == [{"enablement": {"tasks_default_scope": "all"}}], \
            "the choice is persisted to settings"

    def test_calendar_and_tasks_are_fed_from_the_same_rows(self, conn, qapp):
        """calendar-views.md: "They are fed from the same query, so ... both are
        showing the same set"; tasks-board.md: "both are read from the same
        records"."""
        tid = _make_task(conn, title="Shared row", due_date="2026-07-20")
        et.add_subtask(conn, tid, "one")
        et.add_subtask(conn, tid, "two", done=True)
        stub = SimpleNamespace(_fmt_due=EnablementPage._fmt_due)
        rows = EnablementPage._tasks_to_rows(stub, et.list_tasks(conn), conn)
        assert len(rows) == 1
        row = rows[0]
        # The one row object is what both surfaces consume.
        assert row["subs"] == "1 / 2"
        assert row["due_iso"] == "2026-07-20" and row["due_date"] == "2026-07-20"
        assert len(row["subtasks"]) == 2


# ══════════════════════════════════════════════════════════════════════
# card-due-chips.md — "Card-due chips: why Guru cards appear on your calendar"
# ══════════════════════════════════════════════════════════════════════

class TestCardDueChips:

    def _verification_row(self, conn, card_id, *, title, state="ACTIVE",
                          next_date="", last_modified=""):
        conn.execute(
            "INSERT INTO guru_card_verification (card_id, title, collection_id, "
            "collection_name, verification_state, verification_reason, "
            "next_verification_date, verification_interval, last_verified_at, "
            "last_modified, comment_count, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (card_id, title, "c1", "Support", state, "", next_date, 90, "",
             last_modified, 0, ""),
        )
        conn.commit()

    def test_unverified_and_stale_cards_are_surfaced(self, conn):
        """card-due-chips.md: reason "Unverified" — "The card is marked as
        needing verification, or stale"."""
        self._verification_row(conn, "c-needs", title="Needs verification",
                               state="NEEDS_VERIFICATION")
        self._verification_row(conn, "c-stale", title="Stale card", state="STALE")
        self._verification_row(conn, "c-fine", title="Healthy card", state="TRUSTED")
        out = {c["card_id"]: c for c in ga.cards_due_for_update(conn)}
        assert set(out) == {"c-needs", "c-stale"}
        assert out["c-needs"]["reason"] == "unverified"
        assert out["c-stale"]["reason"] == "unverified"

    def test_verification_due_horizon_is_two_weeks(self, conn):
        """card-due-chips.md: reason "Verification due" — "Its verification date
        falls within the next two weeks"."""
        assert ga.DUE_SOON_DAYS == 14
        now = _dt.datetime.now(_dt.timezone.utc)
        inside = (now + _dt.timedelta(days=10)).isoformat()
        outside = (now + _dt.timedelta(days=40)).isoformat()
        self._verification_row(conn, "c-soon", title="Due soon", next_date=inside)
        self._verification_row(conn, "c-later", title="Due later", next_date=outside)
        out = {c["card_id"]: c for c in ga.cards_due_for_update(conn)}
        assert set(out) == {"c-soon"}
        assert out["c-soon"]["reason"] == "verification_due"

    def test_stale_but_popular_uses_a_three_month_modification_horizon(self, conn):
        """card-due-chips.md: reason "Stale but popular" — "one of your
        most-viewed cards and has not been modified in about three months"."""
        now = _dt.datetime.now(_dt.timezone.utc)
        old = (now - _dt.timedelta(days=200)).isoformat()
        recent = (now - _dt.timedelta(days=5)).isoformat()
        self._verification_row(conn, "c-hot-old", title="Popular but old",
                               state="TRUSTED", last_modified=old)
        self._verification_row(conn, "c-hot-new", title="Popular and fresh",
                               state="TRUSTED", last_modified=recent)
        for card in ("c-hot-old", "c-hot-new"):
            for i in range(5):
                conn.execute(
                    "INSERT INTO guru_events (event_key, event_type, user_email, "
                    "event_date, card_id, properties, fetched_at) VALUES (?,?,?,?,?,?,?)",
                    (f"{card}-{i}", "card-viewed", "u@x.com",
                     (now - _dt.timedelta(days=1)).isoformat(), card, "{}", ""),
                )
        conn.commit()
        out = {c["card_id"]: c for c in ga.cards_due_for_update(conn)}
        assert "c-hot-old" in out and out["c-hot-old"]["reason"] == "stale_high_traffic"
        assert "c-hot-new" not in out
        # "about three months" — the default horizon.
        import inspect
        assert inspect.signature(
            ga.cards_due_for_update).parameters["stale_days"].default == 90

    def test_a_card_with_several_reasons_appears_once(self, conn):
        """card-due-chips.md: "A card qualifying for more than one reason appears
        once"."""
        now = _dt.datetime.now(_dt.timezone.utc)
        self._verification_row(conn, "c-both", title="Both reasons",
                               state="NEEDS_VERIFICATION",
                               next_date=(now + _dt.timedelta(days=3)).isoformat())
        rows = ga.cards_due_for_update(conn)
        assert [r["card_id"] for r in rows] == ["c-both"]
        assert rows[0]["reason"] == "unverified", "first reason wins"

    def test_the_card_list_is_capped(self, conn):
        """card-due-chips.md: "The list is capped, so it is a queue of the most
        pressing cards rather than an exhaustive audit"."""
        for i in range(45):
            self._verification_row(conn, f"c{i}", title=f"Card {i}",
                                   state="NEEDS_VERIFICATION")
        rows = ga.cards_due_for_update(conn)
        assert len(rows) == 20, "default cap"
        assert len(ga.cards_due_for_update(conn, limit=5)) == 5

    def test_card_chips_are_generated_without_a_live_guru_connection(self, conn):
        """card-due-chips.md: "Card chips can be *generated* from stored card
        health without a live connection"."""
        self._verification_row(conn, "c1", title="Offline card",
                               state="NEEDS_VERIFICATION")
        # No client argument exists at all — the function is DB-only.
        import inspect
        params = list(inspect.signature(ga.cards_due_for_update).parameters)
        assert params[0] == "conn" and "client" not in params
        assert len(ga.cards_due_for_update(conn)) == 1

    def test_no_card_health_data_means_no_card_chips_and_no_error(self, conn):
        """card-due-chips.md: "No card chips appear at all ... a calendar with no
        card chips is normal before Guru analytics have been gathered"."""
        assert ga.cards_due_for_update(conn) == []

    def test_card_chip_titles_are_prefixed_card_due(self, qapp):
        """card-due-chips.md: cards appear "as their own chips, titled 'Card
        due:' followed by the card name"."""
        page = CalendarPage()
        page.set_tasks([{"due_date": "2026-07-20", "source": "guru",
                         "title": "Card due: Payments v2",
                         "kind": "guru_card_due", "card_id": "c1"}])
        label, kind, payload = page._events["2026-07-20"][0]
        assert label.startswith("Card due:")
        assert kind == "guru" and payload["kind"] == "guru_card_due"

    def test_clicking_a_card_chip_starts_a_targeted_update_not_a_task_panel(self, qapp):
        """card-due-chips.md: "Clicking a card chip does not open a task panel.
        It starts a targeted update ... and you land on the Workbench tab"."""
        stub = SimpleNamespace(_show_task_detail=MagicMock(),
                               _on_targeted_update=MagicMock())
        EnablementPage._on_calendar_event_activated(
            stub, {"kind": "guru_card_due", "card_id": "c1",
                   "title": "Card due: Payments v2"})
        stub._on_targeted_update.assert_called_once_with("c1")
        stub._show_task_detail.assert_not_called()

    def test_the_click_path_fires_the_detail_signal_before_the_card_signal(self, qapp):
        """card-due-chips.md ("If it doesn't"): "A task detail panel opens for a
        moment before the Workbench appears. A known cosmetic quirk of the click
        path" — the chip emits event_clicked (which opens a panel) BEFORE
        event_activated (which routes to the Workbench)."""
        page = CalendarPage()
        page.set_tasks([{"due_date": "2026-07-20", "source": "guru",
                         "title": "Card due: Payments v2",
                         "kind": "guru_card_due", "card_id": "c1"}])
        order: list[str] = []
        page.event_clicked.connect(lambda _: order.append("event_clicked"))
        page.event_activated.connect(lambda _: order.append("event_activated"))
        cell = next(c for c in (_grid_cell(page, r, col)
                                for r in range(1, page._grid_widget.layout().rowCount())
                                for col in range(7))
                    if _day_number(c) == "20")
        _cell_chips(cell)[0].clicked.emit()
        assert order == ["event_clicked", "event_activated"]

    def test_card_chips_are_omitted_from_the_day_expander_but_counted_in_the_chip(self, qapp):
        """card-due-chips.md: "card chips are counted in the '+N more' number but
        not shown in the list"; calendar-views.md says the same."""
        page = CalendarPage()
        tasks = [{"title": f"Task {i}", "due_date": "2026-07-20", "source": "asana",
                  "task_id": f"t{i}", "due_iso": "2026-07-20"} for i in range(3)]
        cards = [{"title": f"Card due: {i}", "due_date": "2026-07-20",
                  "source": "guru", "kind": "guru_card_due", "card_id": f"c{i}"}
                 for i in range(2)]
        page.set_tasks(tasks + cards)
        cell = next(c for c in (_grid_cell(page, r, col)
                                for r in range(1, page._grid_widget.layout().rowCount())
                                for col in range(7))
                    if _day_number(c) == "20")
        more = [c for c in _cell_chips(cell) if c.text().endswith("more")][0]
        assert more.text() == "+3 more", "all five entries are counted"

        # The expander is fed from _all_tasks, which never holds card rows.
        drill = MagicMock()
        stub = SimpleNamespace(_drilldown=drill, _all_tasks=tasks,
                               _show_task_detail=MagicMock())
        EnablementPage._on_calendar_day(stub, "2026-07-20")
        _, subtitle, panel = drill.show_widget.call_args[0]
        assert subtitle == "3 tasks"
        assert all(not b.text().startswith("Card due")
                   for b in panel.findChildren(QPushButton))

    def test_card_chip_without_guru_connected_reports_and_imports_nothing(self, qapp):
        """card-due-chips.md: "Nothing happens when you click a card chip, and
        the status line says to connect Guru ... importing the card needs one"."""
        stub = SimpleNamespace(demo=False, _guru_client=None,
                               _set_status=MagicMock(), _run_import=MagicMock())
        EnablementPage._on_targeted_update(stub, "c1")
        stub._run_import.assert_not_called()
        msg = stub._set_status.call_args[0][0]
        assert "Guru" in msg and "Connect" in msg

    def test_clicking_a_card_chip_never_writes_to_guru(self, qapp):
        """card-due-chips.md: "nothing about clicking one changes the card in
        Guru. The change only happens when you publish the draft"."""
        stub = SimpleNamespace(demo=False, _guru_client=object(),
                               _set_status=MagicMock(), _run_import=MagicMock())
        EnablementPage._on_targeted_update(stub, "c1")
        # The only thing the click path does is import (a read) as a draft.
        stub._run_import.assert_called_once_with("guru", "c1")

    def test_the_calendar_chips_and_home_queue_come_from_different_sources(self, conn):
        """card-due-chips.md (corrected): the Calendar's card chips come from
        guru_analytics.cards_due_for_update (local guru_card_verification rows,
        no client needed), while the Home attention queue comes from
        enablement_health.compute_health, which returns [] without a configured
        Guru client. With stored rows present and Guru unconfigured the two
        disagree: the calendar shows chips, the queue is empty."""
        from src.data import enablement_health
        self._verification_row(conn, "c1", title="Needs verification",
                               state="NEEDS_VERIFICATION")
        calendar_cards = {c["card_id"] for c in ga.cards_due_for_update(conn)}
        queue_cards = {c.card_id for c in
                       enablement_health.compute_health(conn, None)}
        assert calendar_cards == {"c1"}, "the calendar surfaces the local row"
        assert queue_cards == set(), "the attention queue needs a live Guru client"
        assert calendar_cards != queue_cards, "the two surfaces can disagree"


# ══════════════════════════════════════════════════════════════════════
# tasks-board.md — "The Tasks board and the detail panel"
# ══════════════════════════════════════════════════════════════════════

class TestTasksBoard:

    def test_the_list_has_the_documented_columns(self, qapp):
        """tasks-board.md: "One row per task: a status dot, the title, and
        columns for source, due date, priority, assignee, and a subtask count"."""
        assert [c[0] for c in tasks_mod.COLS] == [
            "SOURCE", "DUE", "PRIORITY", "ASSIGNEE", "SUBTASKS"]
        page = TasksPage()
        headers = {lbl.text() for lbl in page.findChildren(QLabel)}
        assert {"TASK", "SOURCE", "DUE", "PRIORITY", "ASSIGNEE", "SUBTASKS"} <= headers

    def test_rows_expand_and_collapse_in_place_and_the_first_starts_expanded(self, qapp):
        """tasks-board.md: "Clicking a row expands it in place ... and clicking
        again collapses it. The first row starts expanded"."""
        page = TasksPage()
        page.load_tasks([
            {"status": "open", "title": "First", "source": "asana", "due": "—",
             "priority": "normal", "assignee": "—", "subs": "0 / 0",
             "subtasks": [], "scratch": ""},
            {"status": "open", "title": "Second", "source": "asana", "due": "—",
             "priority": "normal", "assignee": "—", "subs": "0 / 0",
             "subtasks": [], "scratch": ""},
        ])
        # The expand/collapse toggle reads isVisible(), so the widget tree has
        # to be realised (offscreen show — never a screenshot).
        page.show()
        qapp.processEvents()
        try:
            holders = [page._table_v.itemAt(i).widget()
                       for i in range(1, page._table_v.count() - 1)]
            first_panel = holders[0].layout().itemAt(1).widget()
            second_panel = holders[1].layout().itemAt(1).widget()
            assert first_panel.isVisible(), "the first row starts expanded"
            assert not second_panel.isVisible(), "later rows start collapsed"

            second_row = holders[1].layout().itemAt(0).widget()
            second_row.clicked.emit()
            qapp.processEvents()
            assert second_panel.isVisible(), "click expands in place"
            second_row.clicked.emit()
            qapp.processEvents()
            assert not second_panel.isVisible(), "clicking again collapses"
        finally:
            page.hide()

    def test_the_tasks_tab_cannot_open_a_detail_panel(self, qapp):
        """tasks-board.md: "The detail panel ... opens from the Calendar, not
        from this list"; "Clicking a row does not open a detail panel"."""
        from PySide6.QtCore import Signal
        declared = sorted(n for n, v in vars(TasksPage).items()
                          if isinstance(v, type(Signal())))
        assert declared == ["scope_changed"], (
            "the list exposes no task-opened signal, so no host can route it "
            f"to a detail panel; found {declared}")

    def test_the_filter_chips_and_search_box_are_labels_not_inputs(self, qapp):
        """tasks-board.md: "The source, status, due and priority filters ...
        Nothing — they are labels, not dropdowns"; "The search box on this tab
        ... it is a label, not an input"."""
        page = TasksPage()
        texts = {lbl.text(): lbl for lbl in page.findChildren(QLabel)}
        for caption in ("Source: All", "Status: Open", "Due: Any",
                        "Priority: Any", "Search tasks…"):
            assert caption in texts, f"{caption} should be present"
            assert isinstance(texts[caption], QLabel)
        # There is no editable search field anywhere on the filter bar.
        bar_inputs = [e for e in page.findChildren(QLineEdit)
                      if "Search" in (e.placeholderText() or "")]
        assert bar_inputs == []

    def test_the_new_task_button_is_not_connected(self, qapp):
        """tasks-board.md: "The new-task button | Nothing"."""
        page = TasksPage()
        new_btn = _buttons(page, "+ New task")
        assert len(new_btn) == 1
        assert _n_receivers(new_btn[0], "2clicked()") == 0

    def test_the_workbench_button_in_an_expanded_row_is_not_connected(self, qapp):
        """tasks-board.md: "The Workbench button inside an expanded row |
        Nothing"."""
        page = TasksPage()
        openw = _buttons(page, "Open in Workbench")
        assert openw, "the button exists"
        assert all(_n_receivers(b, "2clicked()") == 0 for b in openw)

    def test_the_inline_add_subtask_box_saves_nothing(self, qapp, monkeypatch):
        """tasks-board.md: "The add-subtask box inside an expanded row | Adds a
        row on screen only; nothing is saved"."""
        spy = MagicMock()
        monkeypatch.setattr(et, "add_subtask", spy)
        page = TasksPage()
        page.load_tasks([{"status": "open", "title": "Only", "source": "asana",
                          "due": "—", "priority": "normal", "assignee": "—",
                          "subs": "0 / 0", "subtasks": [], "scratch": ""}])
        holder = page._table_v.itemAt(1).widget()
        panel = holder.layout().itemAt(1).widget()
        before = len(panel.findChildren(_Check))
        box = [e for e in panel.findChildren(QLineEdit)
               if "subtask" in (e.placeholderText() or "").lower()][0]
        box.setText("Persist me please")
        _buttons(panel, "+ Add")[0].click()

        assert len(panel.findChildren(_Check)) == before + 1, "row appears on screen"
        spy.assert_not_called(), "but nothing reaches the task service"
        assert box.text() == "", "the box is cleared, giving the look of a save"

    def test_the_ai_subtask_button_adds_three_fixed_lines_and_calls_no_model(
            self, qapp, monkeypatch):
        """tasks-board.md: "The AI subtask-drafting button inside a row | Adds
        three fixed placeholder lines; no model is called"."""
        from src.gemini import client_factory
        llm_spy = MagicMock()
        monkeypatch.setattr(client_factory, "build_client_for_task", llm_spy)
        page = TasksPage()
        page.load_tasks([{"status": "open", "title": "Only", "source": "asana",
                          "due": "—", "priority": "normal", "assignee": "—",
                          "subs": "0 / 0", "subtasks": [], "scratch": ""}])
        panel = page._table_v.itemAt(1).widget().layout().itemAt(1).widget()
        before = len(panel.findChildren(_Check))
        _buttons(panel, "AI · Draft subtasks")[0].click()
        assert len(panel.findChildren(_Check)) == before + 3
        llm_spy.assert_not_called()

        labels = [lbl.text() for lbl in panel.findChildren(QLabel)]
        assert {"Review with SME", "Add screenshots",
                "Draft announcement"} <= set(labels), "fixed placeholder lines"

        # Fixed, not derived from the task: the same three lines again.
        _buttons(panel, "AI · Draft subtasks")[0].click()
        assert len([t for t in (lbl.text() for lbl in panel.findChildren(QLabel))
                    if t == "Review with SME"]) == 2

    def test_the_scope_toggle_is_the_one_real_filter(self, qapp):
        """tasks-board.md: "the Mine and All toggle, which is the one filter that
        is real"."""
        page = TasksPage()
        emitted: list[str] = []
        page.scope_changed.connect(emitted.append)
        page._all_btn.click()
        assert emitted == ["all"] and page._scope == "all"
        page._mine_btn.click()
        assert emitted == ["all", "mine"] and page._scope == "mine"

    def test_the_list_is_capped_at_two_hundred_tasks(self, conn):
        """tasks-board.md: "The list is capped at a couple of hundred tasks. On a
        busy board, older tasks will not be in the list even though they exist"."""
        import inspect
        assert inspect.signature(et.list_tasks).parameters["limit"].default == 200
        for i in range(205):
            _make_task(conn, title=f"Task {i}", source_ref=f"gid-{i}")
        assert len(et.list_tasks(conn)) == 200
        assert conn.execute(
            "SELECT COUNT(*) FROM enablement_tasks").fetchone()[0] == 205


# ══════════════════════════════════════════════════════════════════════
# task-writeback.md — "Adding subtasks, comments and due dates"
# ══════════════════════════════════════════════════════════════════════

class TestTaskWriteback:

    # ── the four actions exist on the panel ──────────────────────────

    def test_the_panel_exposes_the_four_documented_actions(self, qapp):
        """task-writeback.md: "the panel appears ... with four actions
        available" — subtask, comment, due date, complete/reopen."""
        panel = TaskDetailPanel({"title": "T", "source": "asana",
                                 "task_id": "t1", "status": "open"})
        for signal_name in ("subtask_added", "comment_posted", "due_changed",
                            "completed_changed"):
            assert hasattr(panel, signal_name)

        seen: dict[str, object] = {}
        panel.subtask_added.connect(lambda t: seen.setdefault("sub", t))
        panel.comment_posted.connect(lambda t: seen.setdefault("comment", t))
        panel.due_changed.connect(lambda d: seen.setdefault("due", d))
        panel.completed_changed.connect(lambda d: seen.setdefault("done", d))

        panel._sub_edit.setText("a subtask")
        _buttons(panel, "Add")[0].click()
        panel._comment_edit.setText("a comment")
        _buttons(panel, "Comment")[0].click()
        panel._due_edit.setText("2026-08-01")
        _buttons(panel, "Update due")[0].click()
        panel._complete_btn.click()

        assert seen == {"sub": "a subtask", "comment": "a comment",
                        "due": "2026-08-01", "done": True}

    def test_the_comment_row_appears_only_for_asana_tasks(self, qapp):
        """task-writeback.md: "This row only appears for tasks that came from
        Asana"; "The comment row is not in the panel. That task did not come from
        Asana"."""
        asana = TaskDetailPanel({"title": "T", "source": "asana", "task_id": "t1"})
        drive = TaskDetailPanel({"title": "T", "source": "drive", "task_id": "t2"})
        assert _buttons(asana, "Comment"), "Asana task has the comment row"
        assert hasattr(asana, "_comment_edit")
        assert not _buttons(drive, "Comment"), "non-Asana task does not"
        assert not hasattr(drive, "_comment_edit")

    def test_the_complete_button_flips_state_and_labels_itself(self, qapp):
        """task-writeback.md: "The button in the header row flips the task's
        state"."""
        open_panel = TaskDetailPanel({"title": "T", "source": "asana",
                                      "status": "open"})
        done_panel = TaskDetailPanel({"title": "T", "source": "asana",
                                      "status": "done"})
        assert open_panel._complete_btn.text() == "Mark complete"
        assert done_panel._complete_btn.text() == "Reopen"
        flips: list[bool] = []
        open_panel.completed_changed.connect(flips.append)
        done_panel.completed_changed.connect(flips.append)
        open_panel._complete_btn.click()
        done_panel._complete_btn.click()
        assert flips == [True, False]

    def test_the_due_field_expects_iso_dates_and_is_not_validated_locally(self, qapp):
        """task-writeback.md: "Type a date as `YYYY-MM-DD`"; "The field expects
        `YYYY-MM-DD` and is not forgiving of other formats — a date Asana cannot
        parse will be refused on its end" (i.e. rejection happens at Asana, not
        in the panel)."""
        panel = TaskDetailPanel({"title": "T", "source": "asana"})
        assert panel._due_edit.placeholderText() == "YYYY-MM-DD"
        emitted: list[str] = []
        panel.due_changed.connect(emitted.append)
        panel._due_edit.setText("next Friday")
        _buttons(panel, "Update due")[0].click()
        assert emitted == ["next Friday"], (
            "the panel performs no format validation — the only rejection point "
            "is the Asana API call")

    def test_the_scratch_pad_never_leaves_the_app(self, qapp):
        """task-writeback.md: "The scratch pad in the panel is a local note. It
        never leaves the app"."""
        panel = TaskDetailPanel({"title": "T", "source": "asana",
                                 "scratch": "private note"})
        labels = [lbl.text() for lbl in panel.findChildren(QLabel)]
        assert "private note" in labels, "it is displayed"
        # It is a read-only display with no signal and no editor.
        assert not [e for e in panel.findChildren(QLineEdit)
                    if e.text() == "private note"]
        from PySide6.QtCore import Signal
        declared = {n for n, v in vars(TaskDetailPanel).items()
                    if isinstance(v, type(Signal()))}
        assert declared and not any("scratch" in s for s in declared), (
            "no signal carries the scratch pad off the panel")
        # And no write-back path sends it anywhere either.
        import inspect
        assert "scratchpad" not in inspect.getsource(awb)

    def test_the_four_actions_write_with_no_confirmation_dialog(self, qapp, monkeypatch):
        """task-writeback.md: "These four actions write immediately. Your click
        is the consent — there is no confirmation card"."""
        from PySide6.QtWidgets import QMessageBox
        asked = MagicMock()
        monkeypatch.setattr(QMessageBox, "question", asked)
        monkeypatch.setattr(QMessageBox, "warning", asked)
        panel = TaskDetailPanel({"title": "T", "source": "asana", "status": "open"})
        fired: list[str] = []
        panel.subtask_added.connect(lambda t: fired.append("sub"))
        panel.completed_changed.connect(lambda d: fired.append("done"))
        panel.due_changed.connect(lambda d: fired.append("due"))
        panel._sub_edit.setText("x")
        _buttons(panel, "Add")[0].click()
        panel._complete_btn.click()
        panel._due_edit.setText("2026-08-01")
        _buttons(panel, "Update due")[0].click()
        assert fired == ["sub", "done", "due"]
        asked.assert_not_called()

    # ── the write-back functions themselves ──────────────────────────

    def test_a_subtask_is_saved_locally_before_the_asana_call(self, conn):
        """task-writeback.md: "The subtask is saved locally first, then created
        underneath the parent task in Asana"."""
        tid = _make_task(conn)
        client = FakeAsanaClient()
        res = awb.create_subtask_in_asana(conn, tid, "Do the thing", client=client)
        assert res["ok"] is True and res["synced"] is True
        subs = et.list_subtasks(conn, tid)
        assert [s["text"] for s in subs] == ["Do the thing"]
        assert subs[0]["asana_subtask_gid"] == "55501"
        assert ("create_subtask", "9001", "Do the thing") in client.calls

    def test_a_subtask_survives_an_asana_failure(self, conn):
        """task-writeback.md: "Because it is saved locally first, a subtask is
        never lost even if the Asana call fails"."""
        tid = _make_task(conn)
        client = FakeAsanaClient(fail_on={"create_subtask"})
        res = awb.create_subtask_in_asana(conn, tid, "Survives", client=client)
        assert res["ok"] is True and res["synced"] is False
        assert [s["text"] for s in et.list_subtasks(conn, tid)] == ["Survives"]

    def test_a_comment_is_not_mirrored_locally(self, conn):
        """task-writeback.md: "It is not mirrored locally — Asana owns
        comments"."""
        tid = _make_task(conn)
        client = FakeAsanaClient()
        res = awb.post_comment_to_asana(conn, tid, "hello there", client=client)
        assert res["ok"] is True and res["story_gid"] == "77701"
        assert ("add_comment", "9001", "hello there") in client.calls
        # Nothing about the comment is stored locally.
        row = et.get_task(conn, tid)
        assert "hello there" not in json.dumps(dict(row), default=str)
        assert et.list_subtasks(conn, tid) == []

    def test_a_due_date_change_updates_locally_and_pushes(self, conn):
        """task-writeback.md: "The local due date changes and the change is
        pushed to Asana"."""
        tid = _make_task(conn, due_date="2026-07-01")
        client = FakeAsanaClient()
        res = awb.update_due_in_asana(conn, tid, "2026-08-15", client=client)
        assert res == {"ok": True, "synced": True, "due_on": "2026-08-15"}
        assert et.get_task(conn, tid)["due_date"] == "2026-08-15"
        assert ("update_due_date", "9001", "2026-08-15") in client.calls

    def test_clearing_the_due_date_removes_it(self, conn):
        """task-writeback.md: "Clearing the box and confirming removes the due
        date"."""
        tid = _make_task(conn, due_date="2026-07-01")
        client = FakeAsanaClient()
        res = awb.update_due_in_asana(conn, tid, None, client=client)
        assert res["ok"] is True and res["due_on"] is None
        assert et.get_task(conn, tid)["due_date"] is None
        assert ("update_due_date", "9001", None) in client.calls

    def test_complete_marks_done_locally_then_pushes(self, conn):
        """task-writeback.md: "The task is marked done locally straight away and
        then pushed to Asana"."""
        tid = _make_task(conn, status="open")
        client = FakeAsanaClient()
        res = awb.set_completed_in_asana(conn, tid, True, client=client)
        assert res == {"ok": True, "synced": True, "status": "done"}
        assert et.get_task(conn, tid)["status"] == "done"
        assert ("update_task", "9001", {"completed": True}) in client.calls

    def test_a_rejected_complete_is_rolled_back(self, conn):
        """task-writeback.md: "If Asana rejects it, the local change is rolled
        back"; "You marked a task complete and it flipped back"."""
        tid = _make_task(conn, status="open")
        client = FakeAsanaClient(fail_on={"update_task"})
        res = awb.set_completed_in_asana(conn, tid, True, client=client)
        assert res["ok"] is False and res["reverted"] is True
        assert et.get_task(conn, tid)["status"] == "open", "rolled back"

    # ── the "three of four degrade" claim (focus of this audit) ──────

    def test_subtasks_degrade_gracefully_on_a_non_asana_task(self, conn):
        """task-writeback.md: "Subtasks work on any task. If the task did not
        come from Asana ... the subtask is still saved locally and the status
        line says so"."""
        tid = _make_task(conn, source="drive", source_ref=None)
        res = awb.create_subtask_in_asana(conn, tid, "Local only")
        assert res["ok"] is True and res["synced"] is False
        assert "not linked to Asana" in res["note"]
        assert [s["text"] for s in et.list_subtasks(conn, tid)] == ["Local only"]

    def test_subtasks_degrade_gracefully_when_asana_is_unconfigured(self, conn):
        """task-writeback.md: "... or Asana is not configured, the subtask is
        still saved locally"."""
        tid = _make_task(conn)                      # Asana-linked, but no key
        res = awb.create_subtask_in_asana(conn, tid, "Still saved")
        assert res["ok"] is True and res["synced"] is False
        assert "not configured" in res["note"]
        assert [s["text"] for s in et.list_subtasks(conn, tid)] == ["Still saved"]

    def test_due_dates_degrade_gracefully_on_a_non_asana_task(self, conn):
        """task-writeback.md: "Due dates behave the same way — set locally, with
        a note that it did not reach Asana"."""
        tid = _make_task(conn, source="drive", source_ref=None, due_date="2026-07-01")
        res = awb.update_due_in_asana(conn, tid, "2026-09-09")
        assert res["ok"] is True and res["synced"] is False
        assert "not linked to Asana" in res["note"]
        assert et.get_task(conn, tid)["due_date"] == "2026-09-09"

    def test_due_dates_degrade_gracefully_when_asana_is_unconfigured(self, conn):
        """task-writeback.md: due dates degrade "with a note that it did not
        reach Asana"."""
        tid = _make_task(conn, due_date="2026-07-01")
        res = awb.update_due_in_asana(conn, tid, "2026-09-09")
        assert res["ok"] is True and res["synced"] is False
        assert "not configured" in res["note"]
        assert et.get_task(conn, tid)["due_date"] == "2026-09-09"

    def test_complete_degrades_gracefully_on_a_non_asana_task(self, conn):
        """task-writeback.md: "Complete and reopen also degrade. The status
        change is written locally and reported as not synced, so the button is
        never inert"."""
        tid = _make_task(conn, source="drive", source_ref=None, status="open")
        res = awb.set_completed_in_asana(conn, tid, True)
        assert res["ok"] is True and res["synced"] is False
        assert "not linked to Asana" in res["note"]
        assert et.get_task(conn, tid)["status"] == "done"

    def test_complete_degrades_gracefully_when_asana_is_unconfigured(self, conn):
        """task-writeback.md: complete/reopen "written locally and reported as
        not synced"."""
        tid = _make_task(conn, status="open")
        res = awb.set_completed_in_asana(conn, tid, True)
        assert res["ok"] is True and res["synced"] is False
        assert "not configured" in res["note"]
        assert et.get_task(conn, tid)["status"] == "done"

    def test_only_commenting_is_refused_outright(self, conn):
        """task-writeback.md: "Only commenting is refused outright. A comment on
        a task that did not come from Asana has nowhere to go, so it fails rather
        than being stored locally"."""
        unlinked = _make_task(conn, source="drive", source_ref=None)
        res = awb.post_comment_to_asana(conn, unlinked, "nowhere to go")
        assert res == {"ok": False, "error": "not_an_asana_task"}

        linked = _make_task(conn, source_ref="9002")
        res2 = awb.post_comment_to_asana(conn, linked, "no key")
        assert res2 == {"ok": False, "error": "asana_not_configured"}

        # And nothing was stored locally for either.
        for tid in (unlinked, linked):
            assert "nowhere to go" not in json.dumps(
                dict(et.get_task(conn, tid)), default=str)
            assert et.list_subtasks(conn, tid) == []

    def test_exactly_three_of_the_four_actions_degrade(self, conn):
        """task-writeback.md: "Three of the four degrade gracefully instead of
        failing" — the single assertion that settles the whole table."""
        results = {}
        for name, call in (
            ("subtask", lambda t: awb.create_subtask_in_asana(conn, t, "x")),
            ("due", lambda t: awb.update_due_in_asana(conn, t, "2026-09-09")),
            ("complete", lambda t: awb.set_completed_in_asana(conn, t, True)),
            ("comment", lambda t: awb.post_comment_to_asana(conn, t, "x")),
        ):
            tid = _make_task(conn, source="drive", source_ref=None,
                             title=f"unlinked-{name}")
            results[name] = call(tid)
        degraded = {k for k, v in results.items() if v.get("ok") is True}
        refused = {k for k, v in results.items() if v.get("ok") is False}
        assert degraded == {"subtask", "due", "complete"}
        assert refused == {"comment"}

    # ── the status line ──────────────────────────────────────────────

    def test_the_status_line_narrates_syncing_synced_local_and_error(self, qapp):
        """task-writeback.md: "a syncing message while it runs, then either
        confirmation that it synced, a note that it was saved locally, or an
        error"."""
        # The "syncing" message is set before the worker starts.
        stub = SimpleNamespace(_set_status=MagicMock(),
                               _engine_db_path=lambda: ":memory:",
                               task_action_done=SimpleNamespace(emit=lambda r: None))
        EnablementPage._run_task_writeback(stub, "post_comment_to_asana", "t1", "x")
        assert "Syncing" in stub._set_status.call_args[0][0]

        def status_for(res):
            s = SimpleNamespace(_set_status=MagicMock(), _load_live=MagicMock(),
                                _all_tasks=[], _open_task_id=None,
                                _open_task_title=None,
                                _show_task_detail=MagicMock())
            EnablementPage._on_task_action_done(s, res)
            return s._set_status.call_args[0][0]

        assert "synced to Asana" in status_for({"ok": True, "synced": True})
        assert status_for({"ok": True, "synced": False,
                           "note": "Asana not configured"}).startswith("Saved locally")
        assert "failed" in status_for({"ok": False, "error": "boom"})

    def test_the_views_reload_and_the_panel_reopens_on_the_same_task(self, qapp):
        """task-writeback.md: "When it finishes, the task views reload and the
        panel reopens on the same task with fresh data"."""
        fresh = {"task_id": "t1", "title": "Alpha (updated)"}
        stub = SimpleNamespace(_set_status=MagicMock(), _load_live=MagicMock(),
                               _all_tasks=[fresh], _open_task_id="t1",
                               _open_task_title="Alpha",
                               _show_task_detail=MagicMock())
        EnablementPage._on_task_action_done(stub, {"ok": True, "synced": True})
        stub._load_live.assert_called_once()
        stub._show_task_detail.assert_called_once_with(fresh)

    @pytest.mark.parametrize("fn_name,args", [
        ("post_comment_to_asana", ("hello",)),
        ("set_completed_in_asana", (True,)),
        ("update_due_in_asana", ("2026-08-01",)),
        ("create_subtask_in_asana", ("a subtask",)),
    ])
    def test_writeback_actions_run_off_the_ui_thread(self, qapp, monkeypatch,
                                                     fn_name, args):
        """task-writeback.md: "All four run in the background so the app never
        freezes on a slow network".

        The old test asserted `active_count() >= started`, which could not
        fail — it holds for an inline call too — and exercised only one of the
        four actions. This captures the Thread target and confirms (a) a daemon
        Thread was created rather than running inline, and (b) running that
        target dispatches THIS action's write-back function. Parametrized so
        all four are actually exercised."""
        import threading

        created = {}

        class _FakeThread:
            def __init__(self, target=None, daemon=None, **kw):
                created["target"] = target
                created["daemon"] = daemon

            def start(self):
                created["started"] = True

        monkeypatch.setattr(threading, "Thread", _FakeThread)

        dispatched = {}
        import src.data.asana_writeback as awb
        for name in ("post_comment_to_asana", "set_completed_in_asana",
                     "update_due_in_asana", "create_subtask_in_asana"):
            monkeypatch.setattr(
                awb, name,
                lambda conn, tid, *a, _n=name: dispatched.setdefault(
                    "call", (_n, tid, a)) or {"ok": True})

        stub = SimpleNamespace(
            _set_status=MagicMock(),
            _engine_db_path=lambda: ":memory:",
            task_action_done=SimpleNamespace(emit=lambda r: None))

        EnablementPage._run_task_writeback(stub, fn_name, "t1", *args)

        # (a) work was handed to a daemon thread, not run inline.
        assert created.get("started") is True and created.get("daemon") is True
        assert callable(created.get("target")), "no worker was scheduled"
        # (b) running the worker dispatches this specific write-back.
        created["target"]()
        assert dispatched["call"][0] == fn_name
        assert dispatched["call"][1] == "t1" and dispatched["call"][2] == args


# ══════════════════════════════════════════════════════════════════════
# asana-conflicts.md — "When Asana says the task changed"
# ══════════════════════════════════════════════════════════════════════

class TestAsanaConflicts:

    def test_a_matching_timestamp_lets_the_write_through(self, conn):
        """asana-conflicts.md: "If they match, nobody has touched the task since
        you last synced and the write goes ahead"."""
        tid = _make_task(conn)
        et.update_task(conn, tid, remote_modified_at="2026-07-01T00:00:00.000Z")
        client = FakeAsanaClient(modified_at="2026-07-01T00:00:00.000Z")
        res = awb.update_due_in_asana(conn, tid, "2026-08-15", client=client)
        assert res["ok"] is True and res["synced"] is True
        assert "update_due_date" in client.names()

    def test_a_differing_timestamp_abandons_the_write_before_sending(self, conn):
        """asana-conflicts.md: "If they differ, the write is abandoned before
        anything is sent"."""
        tid = _make_task(conn, due_date="2026-07-01")
        et.update_task(conn, tid, remote_modified_at="2026-07-01T00:00:00.000Z")
        client = FakeAsanaClient(modified_at="2026-07-08T09:00:00.000Z")
        res = awb.update_due_in_asana(conn, tid, "2026-08-15", client=client)
        assert res["ok"] is False and res["conflict"] is True
        assert "update_due_date" not in client.names(), "nothing was sent"

    def test_a_conflict_leaves_the_local_copy_alone(self, conn):
        """asana-conflicts.md: "Your local copy is left alone"."""
        tid = _make_task(conn, due_date="2026-07-01", status="open")
        et.update_task(conn, tid, remote_modified_at="2026-07-01T00:00:00.000Z")
        before = dict(et.get_task(conn, tid))
        client = FakeAsanaClient(modified_at="2026-07-08T09:00:00.000Z")

        awb.update_due_in_asana(conn, tid, "2026-08-15", client=client)
        assert et.get_task(conn, tid)["due_date"] == before["due_date"]
        awb.set_completed_in_asana(conn, tid, True, client=client)
        assert et.get_task(conn, tid)["status"] == before["status"]

    def test_the_guard_covers_due_dates_and_complete_reopen(self, conn):
        """asana-conflicts.md guard table: "Change the due date | Yes";
        "Complete or reopen | Yes"."""
        for fn, args in ((awb.update_due_in_asana, ("2026-08-15",)),
                         (awb.set_completed_in_asana, (True,))):
            tid = _make_task(conn, source_ref=f"gid-{fn.__name__}")
            et.update_task(conn, tid, remote_modified_at="2026-07-01T00:00:00.000Z")
            client = FakeAsanaClient(modified_at="2026-07-08T09:00:00.000Z")
            res = fn(conn, tid, *args, client=client)
            assert res.get("conflict") is True, f"{fn.__name__} must be guarded"
            assert client.names() == ["get_task"], "only the CAS read happened"

    def test_the_guard_skips_subtasks_and_comments(self, conn):
        """asana-conflicts.md guard table: "Add a subtask | No — it only adds";
        "Post a comment | No — it only adds"; and "The message appears when
        adding a subtask or posting a comment ... is a bug"."""
        tid = _make_task(conn)
        et.update_task(conn, tid, remote_modified_at="2026-07-01T00:00:00.000Z")
        client = FakeAsanaClient(modified_at="2026-07-08T09:00:00.000Z")

        sub = awb.create_subtask_in_asana(conn, tid, "adds only", client=client)
        assert sub["ok"] is True and sub["synced"] is True
        assert "conflict" not in sub

        com = awb.post_comment_to_asana(conn, tid, "adds only", client=client)
        assert com["ok"] is True and "conflict" not in com
        assert "get_task" not in client.names(), "no CAS read for append-only verbs"

    def test_an_unreachable_asana_proceeds_rather_than_blocking(self, conn):
        """asana-conflicts.md: "If the app cannot reach Asana to make the
        comparison at all, it proceeds rather than blocking you"."""
        tid = _make_task(conn, due_date="2026-07-01")
        et.update_task(conn, tid, remote_modified_at="2026-07-01T00:00:00.000Z")
        client = FakeAsanaClient(fail_on={"get_task"})
        res = awb.update_due_in_asana(conn, tid, "2026-08-15", client=client)
        assert res["ok"] is True and res["synced"] is True
        assert et.get_task(conn, tid)["due_date"] == "2026-08-15"

    def test_a_first_write_with_no_anchor_stamps_and_proceeds(self, conn):
        """asana-conflicts.md: "The app remembers when each task was last
        modified in Asana" — with nothing remembered yet the write proceeds and
        the anchor is recorded."""
        tid = _make_task(conn, due_date="2026-07-01")
        assert not et.get_task(conn, tid)["remote_modified_at"]
        client = FakeAsanaClient(modified_at="2026-07-05T00:00:00.000Z")
        res = awb.update_due_in_asana(conn, tid, "2026-08-15", client=client)
        assert res["ok"] is True and res["synced"] is True
        assert et.get_task(conn, tid)["remote_modified_at"]

    def test_a_successful_write_restamps_the_anchor(self, conn):
        """asana-conflicts.md: the comparison anchor tracks "when each task was
        last modified in Asana", so a successful write advances it."""
        tid = _make_task(conn)
        et.update_task(conn, tid, remote_modified_at="2026-07-01T00:00:00.000Z")
        client = FakeAsanaClient(modified_at="2026-07-01T00:00:00.000Z")
        awb.update_due_in_asana(conn, tid, "2026-08-15", client=client)
        assert et.get_task(conn, tid)["remote_modified_at"] == \
            "2026-07-09T00:00:00.000Z"

    def test_the_conflict_message_is_a_retry_prompt_not_an_error(self, qapp):
        """asana-conflicts.md: "the status line says the task changed in Asana,
        that it has been refreshed, and asks you to retry. Nothing broke"."""
        stub = SimpleNamespace(_set_status=MagicMock(), _load_live=MagicMock(),
                               _all_tasks=[], _open_task_id=None,
                               _open_task_title=None, _show_task_detail=MagicMock())
        EnablementPage._on_task_action_done(
            stub, {"ok": False, "conflict": True, "error": "task changed"})
        msg = stub._set_status.call_args[0][0]
        assert "changed in Asana" in msg and "retry" in msg.lower()
        assert "failed" not in msg.lower()

    def test_the_panel_reopens_on_the_same_task_after_a_conflict(self, qapp):
        """asana-conflicts.md (corrected): after a conflict "the task views
        reload ... and the panel reopens on the same task" — from the LOCAL
        copy, so it shows the same values as before (not Asana's current
        state)."""
        fresh = {"task_id": "t1", "title": "Alpha"}
        stub = SimpleNamespace(_set_status=MagicMock(), _load_live=MagicMock(),
                               _all_tasks=[fresh], _open_task_id="t1",
                               _open_task_title="Alpha",
                               _show_task_detail=MagicMock())
        EnablementPage._on_task_action_done(stub, {"ok": False, "conflict": True})
        stub._load_live.assert_called_once()
        stub._show_task_detail.assert_called_once_with(fresh)

    def test_a_conflict_does_not_refresh_the_local_row_from_asana(self, conn):
        """asana-conflicts.md (corrected): a conflict abandons the write and does
        NOT pull Asana's current state into the local row. _cas_precheck reads
        Asana's modified_at but returns before persisting it, and the panel
        reload is a re-read of the LOCAL database — so the operator sees the
        pre-conflict values and must open the task in Asana (or wait for the
        poll) to see the current state."""
        tid = _make_task(conn, due_date="2026-07-01")
        et.update_task(conn, tid, remote_modified_at="2026-07-01T00:00:00.000Z")
        client = FakeAsanaClient(modified_at="2026-07-08T09:00:00.000Z")
        res = awb.update_due_in_asana(conn, tid, "2026-08-15", client=client)
        assert res.get("conflict") is True
        row = et.get_task(conn, tid)
        # The anchor is NOT advanced to Asana's newer timestamp...
        assert row["remote_modified_at"] == "2026-07-01T00:00:00.000Z"
        # ...and the local due date is left exactly as it was — the attempted
        # write never landed and Asana's value was never fetched down.
        assert row["due_date"] == "2026-07-01"

    def test_an_immediate_second_attempt_conflicts_identically(self, conn):
        """asana-conflicts.md (corrected): a conflict does not advance the CAS
        anchor, so an immediate retry compares against the SAME stale timestamp
        and conflicts again. The anchor moves only on a successful write
        (_restamp) or the background poll (asana_monitor, ~60s), so the operator
        must wait for the next poll before a retry can go through."""
        tid = _make_task(conn, due_date="2026-07-01")
        et.update_task(conn, tid, remote_modified_at="2026-07-01T00:00:00.000Z")
        client = FakeAsanaClient(modified_at="2026-07-08T09:00:00.000Z")

        first = awb.update_due_in_asana(conn, tid, "2026-08-15", client=client)
        assert first.get("conflict") is True
        # The anchor did not move, so the retry is against the same stale value.
        assert et.get_task(conn, tid)["remote_modified_at"] == \
            "2026-07-01T00:00:00.000Z"
        second = awb.update_due_in_asana(conn, tid, "2026-08-15", client=client)
        assert second.get("conflict") is True, "immediate retry conflicts identically"

        # It goes through only once the anchor has been refreshed to Asana's
        # current value — which is exactly what the background poll does.
        et.update_task(conn, tid, remote_modified_at="2026-07-08T09:00:00.000Z")
        third = awb.update_due_in_asana(conn, tid, "2026-08-15", client=client)
        assert third.get("ok") is True and third.get("synced") is True


# ══════════════════════════════════════════════════════════════════════
# asana-setup.md — "Setting up your Asana board with Renn"
# ══════════════════════════════════════════════════════════════════════

class TestAsanaSetup:

    def test_the_settings_api_key_field_saves_nothing(self, qapp):
        """asana-setup.md: "The API key field in Settings is not connected to
        anything that saves it"; "You pasted a key in Settings and nothing
        happened. Expected — that field saves nothing"."""
        import inspect
        from src.ui.pages.enablement import settings as settings_mod
        src = inspect.getsource(settings_mod)
        assert "asana_api_key" not in src, "Settings never names the store key"
        # The field is a local, never retained on the widget.
        asana_src = inspect.getsource(settings_mod.SettingsPage._asana)
        assert "keyf = QLineEdit()" in asana_src
        assert "self._keyf" not in asana_src and "self.keyf" not in asana_src

    def test_no_code_under_src_ever_writes_the_asana_key(self):
        """asana-setup.md: "the key has to be installed into the credential store
        another way — normally by an administrator using the project's setup
        scripts"."""
        import pathlib
        import re
        root = pathlib.Path(__file__).resolve().parents[1] / "src"
        writes = []
        pattern = re.compile(r"save_setting\(\s*[\"']asana_api_key")
        for path in root.rglob("*.py"):
            if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                writes.append(str(path))
        assert writes == [], f"unexpected in-app writer(s): {writes}"

    def test_discovery_flags_sample_projects_when_no_key(self):
        """asana-setup.md: with no key stored, discovery returns the built-in
        sample projects but TAGS them as sample data (``mock: True``) rather than
        passing them off silently as your real board."""
        result = asana_setup.discover()          # pat_store pinned to "" by fixture
        # The fallback is no longer silent: it carries a real flag the caller
        # gates on, and it is a fresh dict — never the shared constant.
        assert result["mock"] is True
        assert result is not asana_setup.MOCK_DISCOVERY
        assert asana_setup.MOCK_DISCOVERY.get("mock") is None, \
            "the shared constant itself is never stamped"
        # Same sample projects as before, now flagged rather than disguised.
        assert [p["name"] for p in result["projects"]] == [
            "Enablement Requests", "Launch Coordination", "Provider Onboarding"]
        assert asana_setup.is_asana_connected() is False

    def test_setup_saves_only_the_asana_source_config(self, conn):
        """asana-setup.md: "it is tightly scoped: it touches the Asana source
        settings and nothing else"; "setup only reads from Asana and writes local
        settings"."""
        def snapshot():
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in tables}

        before = snapshot()
        res = asana_setup.set_asana_board_config(
            conn, project_gid="111", project_name="Enablement Requests",
            indicator_field_gid="901", indicator_field_name="Assigned Team",
            indicator_value_gid="951", indicator_value_name="Enablement",
            priority_field_gid="902", assignee_field_gid="903")
        after = snapshot()

        assert res["ok"] is True and res["source_id"] == "asana:111"
        changed = {t for t in after if after[t] != before.get(t)}
        assert changed == {"monitor_sources"}, f"also touched: {changed}"

        row = conn.execute(
            "SELECT source_type, config_json FROM monitor_sources "
            "WHERE source_id='asana:111'").fetchone()
        cfg = json.loads(row[1])
        assert row[0] == "asana"
        assert cfg["indicators"][0]["trigger_value_gids"] == ["951"]
        assert cfg["mappings"] == {"priority_field_gid": "902",
                                   "assignee_field_gid": "903"}

    def test_saving_the_board_needs_no_asana_client_at_all(self):
        """asana-setup.md: "Renn claims it changed something in Asana during
        setup. It should not have" — the save path has no way to reach Asana."""
        import inspect
        params = list(inspect.signature(asana_setup.set_asana_board_config).parameters)
        assert "client" not in params and "api_key" not in params
        src = inspect.getsource(asana_setup.set_asana_board_config)
        assert "AsanaClient" not in src and "http" not in src

    def test_discovery_only_reads_from_asana(self, monkeypatch):
        """asana-setup.md: "setup only reads from Asana and writes local
        settings"."""
        called: list[str] = []

        class ReadOnlySpy:
            def __init__(self, key):
                pass

            def discover(self):
                called.append("discover")
                return {"workspace": {"gid": "1", "name": "W"},
                        "projects": [{"gid": "111", "name": "Real Project"}],
                        "custom_fields": {}}

            def __getattr__(self, name):
                raise AssertionError(f"discovery must not call {name!r}")

        from src.data import asana_client as ac
        monkeypatch.setattr(ac, "AsanaClient", ReadOnlySpy)
        out = asana_setup.discover(api_key="a-key")
        assert called == ["discover"]
        assert out["projects"][0]["name"] == "Real Project"

    def test_renn_has_both_a_discover_and_a_save_tool(self):
        """asana-setup.md: "Renn has both steps as tools and will run them from a
        plain request"."""
        from src.data.chat_tools import registry
        names = set(registry.build_registry().keys()) if hasattr(
            registry, "build_registry") else set()
        if not names:                     # registry shape differs — fall back
            import inspect
            src = inspect.getsource(registry)
            assert '_register("asana_discover"' in src
            assert '_register("set_asana_board_config"' in src
        else:
            assert {"asana_discover", "set_asana_board_config"} <= names

    def test_the_setup_button_refuses_without_a_key_but_saves_in_demo_mode(
            self, conn, qapp):
        """asana-setup.md: without an Asana key stored, the setup button DECLINES
        rather than saving fabricated GIDs — discovery is mock, so nothing is
        persisted to monitor_sources and Renn says it is not connected. Demo mode
        is the one exception: it legitimately saves the sample board, still
        picking the first project and the fixed 'Assigned Team'/'Enablement'
        field names."""
        # No key (fixture pins the store to ""), not demo → refuse, save nothing.
        refuse = SimpleNamespace(demo=False, chat=MagicMock(), _conn=lambda: conn,
                                 _chat_say=MagicMock(), _set_status=MagicMock(),
                                 _open_chat=MagicMock())
        EnablementPage._on_asana_setup(refuse)
        assert conn.execute(
            "SELECT COUNT(*) FROM monitor_sources").fetchone()[0] == 0, \
            "no board config is written from the mock's fabricated GIDs"
        assert "not connected" in refuse._set_status.call_args[0][0].lower()
        chat_text = " ".join(str(a) for c in refuse.chat.set_chat.call_args_list
                             for a in c[0])
        assert "API key" in chat_text, "Renn explains it is not connected"

        # Demo mode legitimately shows the sample board and DOES save it.
        demo = SimpleNamespace(demo=True, chat=MagicMock(), _conn=lambda: conn,
                               _chat_say=MagicMock(), _set_status=MagicMock(),
                               _open_chat=MagicMock())
        EnablementPage._on_asana_setup(demo)
        row = conn.execute(
            "SELECT source_id, display_name, config_json FROM monitor_sources "
            "WHERE source_type='asana'").fetchone()
        assert row is not None, "demo mode saves the sample board"
        # MOCK_DISCOVERY's FIRST project, not any of the others.
        assert row[1] == "Enablement Requests"
        assert row[0] == "asana:120420000111"
        cfg = json.loads(row[2])
        assert cfg["indicators"][0]["field_name"] == "Assigned Team"
        assert cfg["indicators"][0]["trigger_value_names"] == ["Enablement"]
        assert cfg["mappings"]["priority_field_gid"] == "120420000901" or \
            cfg["mappings"]["priority_field_gid"] == "120420000902"
        assert cfg["mappings"]["assignee_field_gid"] == "120420000903"

    def test_setup_saves_nothing_and_reports_nothing_when_field_names_differ(
            self, conn, qapp, monkeypatch):
        """asana-setup.md: "Renn lists your projects but nothing gets saved, and
        there is no error ... this is the most common outcome when your board
        does not have the exact field names above"."""
        monkeypatch.setattr(asana_setup, "discover", lambda *a, **k: {
            "workspace": {"gid": "1", "name": "W"},
            "projects": [{"gid": "111", "name": "Ops Board"}],
            "custom_fields": {"111": [
                {"gid": "901", "name": "Squad", "type": "enum",
                 "enum_options": [{"gid": "951", "name": "Enablement"}]}]}})
        stub = SimpleNamespace(chat=MagicMock(), _conn=lambda: conn,
                               _chat_say=MagicMock(), _set_status=MagicMock(),
                               _open_chat=MagicMock())
        EnablementPage._on_asana_setup(stub)

        assert conn.execute(
            "SELECT COUNT(*) FROM monitor_sources").fetchone()[0] == 0
        chat_text = " ".join(str(a) for c in stub.chat.set_chat.call_args_list
                             for a in c[0])
        assert "Ops Board" in chat_text, "projects are still listed"
        errors = [c[0][1] for c in stub._chat_say.call_args_list
                  if "error" in str(c[0][1]).lower()]
        assert errors == [], "no error is surfaced"

    def test_identity_can_be_detected_from_google_and_resolved_from_asana(self, qapp):
        """asana-setup.md: "In the identity area of Settings you can detect your
        email from the connected Google account, and resolve your Asana user from
        your API key"."""
        from src.ui.pages.enablement.settings import SettingsPage
        from PySide6.QtCore import Signal
        for name in ("identity_detect_email_requested",
                     "identity_resolve_asana_gid_requested"):
            attr = vars(SettingsPage).get(name)
            assert isinstance(attr, type(Signal())), f"{name} missing"
        # The host wires both to real workers (page.py _build).
        import inspect
        wiring = inspect.getsource(EnablementPage._build)
        assert ("identity_detect_email_requested.connect"
                "(self._on_detect_operator_email)") in wiring
        assert ("identity_resolve_asana_gid_requested.connect"
                "(self._on_resolve_operator_gid)") in wiring

    def test_resolving_the_asana_user_without_a_key_reports_not_connected(self, qapp):
        """asana-setup.md: "Resolving your Asana user reports that Asana is not
        connected. The identity lookup uses the same API key as everything
        else"."""
        emitted: list[tuple] = []
        stub = SimpleNamespace(
            identity_resolved=SimpleNamespace(emit=lambda *a: emitted.append(a)),
            connection_status_ready=SimpleNamespace(
                emit=lambda *a: emitted.append(a)))
        EnablementPage._resolve_operator_gid_worker(stub)
        assert emitted, "something is reported"
        kind, ok, msg = emitted[0]
        assert kind == "asana" and ok is False
        assert "Connect Asana" in msg

    def test_a_stored_identity_persists_to_the_keys_the_mine_filter_reads(
            self, qapp, monkeypatch):
        """asana-setup.md: "Until at least one of your Asana user ID or display
        name is stored, Mine falls back to showing every task"."""
        from src.data import settings_manager
        written: dict = {}
        monkeypatch.setattr(settings_manager, "update_section",
                            lambda section, values: written.update(values))
        monkeypatch.setattr(settings_manager, "get_section",
                            lambda section, default=None: {})
        stub = SimpleNamespace(settings=MagicMock())
        EnablementPage._on_identity_resolved(stub, "a@b.com", "12345", "Jo Rivera")
        assert written["operator_asana_gid"] == "12345"
        assert written["operator_name"] == "Jo Rivera"

        from src.data import enablement_identity as ident
        monkeypatch.setattr(ident, "operator_identity",
                            lambda: {"email": "a@b.com", "name": "Jo Rivera",
                                     "asana_gid": "12345"})
        assert EnablementPage._list_filters(
            SimpleNamespace(_task_scope="mine")) == {
                "assignee_gid": "12345", "assignee": "Jo Rivera"}


# ══════════════════════════════════════════════════════════════════════
# drag-reschedule.md — "Dragging to reschedule"
# ══════════════════════════════════════════════════════════════════════

class TestDragReschedule:

    @pytest.fixture
    def controller(self, qapp):
        ctl = CalendarWebController()
        ctl.set_tasks([
            {"task_id": "t1", "title": "Movable", "due_date": "2026-07-20",
             "source": "asana"},
            {"task_id": "c1", "title": "Card due: Payments", "due_date": "2026-07-21",
             "source": "guru", "kind": "guru_card_due", "card_id": "c1"},
        ])
        return ctl

    def _wire(self, ctl, *, approve=True, write_raises=False):
        state = {"confirms": [], "writes": [], "resolved": []}

        def confirm(task, date):
            state["confirms"].append((dict(task), date))
            return approve

        def write(task_id, date):
            state["writes"].append((task_id, date))
            if write_raises:
                raise RuntimeError("dispatch failed")

        ctl._confirm_fn = confirm
        ctl._write_fn = write
        ctl.reschedule_resolved.connect(
            lambda payload: state["resolved"].append(json.loads(payload)))
        return state

    def test_the_setting_has_the_three_documented_modes_and_defaults_off(self):
        """drag-reschedule.md: "a single setting, `enablement.web_tabs`, which
        can be off, can enable the browser-rendered Calendar alone, or can enable
        both the Calendar and the Workbench. It defaults to off"."""
        assert VALID_MODES == ("off", "calendar", "all")

    def test_the_setting_is_absent_from_the_shipped_configuration(self):
        """drag-reschedule.md: "it is not written into the shipped settings file
        at all, so off is what you have"; "You cannot do this in the app as it
        ships"."""
        from src.data.settings_manager import get_section
        section = get_section("enablement", {}) or {}
        assert "web_tabs" not in section, (
            "the flag is absent from the settings file as the article states")
        assert web_tabs_mode() == "off"

    def test_an_unrecognised_flag_value_degrades_to_off(self, monkeypatch):
        """drag-reschedule.md: the original Calendar renders unless the flag
        explicitly selects the web one."""
        from src.data import settings_manager
        monkeypatch.setattr(settings_manager, "get_section",
                            lambda section, default=None: {"web_tabs": "yes-please"})
        assert web_tabs_mode() == "off"
        monkeypatch.setattr(settings_manager, "get_section",
                            lambda section, default=None: {"web_tabs": "calendar"})
        assert web_tabs_mode() == "calendar"

    def test_the_native_calendar_chips_are_not_draggable(self, qapp):
        """drag-reschedule.md: "the original Calendar renders instead — where
        chips are not draggable"; "Chips will not drag. Expected"."""
        import inspect
        src = inspect.getsource(calendar_mod)
        for api in ("setAcceptDrops", "dragEnterEvent", "dropEvent",
                    "mouseMoveEvent", "QDrag"):
            assert api not in src, f"native calendar implements {api}"
        page = CalendarPage()
        assert not page.acceptDrops()

    def test_the_page_can_only_ask_never_write(self, qapp):
        """drag-reschedule.md: "The page tells the app which task was dropped and
        on what date. That is all it can do — the page has no ability to
        write"."""
        relayed: list[tuple] = []
        bridge = CalendarBridge(reschedule_fn=lambda t, d: relayed.append((t, d)))
        bridge.requestReschedule("t1", "2026-07-25")
        assert relayed == [("t1", "2026-07-25")]
        import inspect
        src = inspect.getsource(CalendarBridge)
        for forbidden in ("asana", "sqlite", "update_task", "get_connection"):
            assert forbidden not in src.lower(), (
                f"the bridge must be a pure relay; found {forbidden}")

    def test_an_unknown_chip_id_is_dropped_silently(self, controller):
        """drag-reschedule.md: "A chip it does not recognise ... is dropped
        silently"."""
        state = self._wire(controller)
        controller.request_reschedule("nope", "2026-07-25")
        assert state == {"confirms": [], "writes": [], "resolved": []}

    def test_a_malformed_date_is_dropped_silently(self, controller):
        """drag-reschedule.md: "a malformed date ... is dropped silently"."""
        state = self._wire(controller)
        for bad in ("", "25/07/2026", "2026-07-211", "2026-7-5", "tomorrow"):
            controller.request_reschedule("t1", bad)
        assert state["confirms"] == [] and state["writes"] == []
        assert state["resolved"] == []

    def test_a_drop_back_onto_the_same_day_is_dropped_silently(self, controller):
        """drag-reschedule.md: "a drop back onto the same day ... is dropped
        silently"."""
        state = self._wire(controller)
        controller.request_reschedule("t1", "2026-07-20")
        assert state["confirms"] == [] and state["writes"] == []

    def test_a_guru_card_due_chip_cannot_be_rescheduled(self, controller):
        """drag-reschedule.md: "a Guru card-due chip is dropped silently";
        "Guru card-due chips would not be draggable"."""
        state = self._wire(controller)
        controller.request_reschedule("c1", "2026-07-25")
        assert state["confirms"] == [] and state["writes"] == []
        assert state["resolved"] == []

    def test_only_one_move_can_be_in_flight(self, controller):
        """drag-reschedule.md: "Only one move can be in flight at a time, so one
        drag can never become two writes"."""
        state = {"confirms": 0, "writes": []}

        def reentrant_confirm(task, date):
            state["confirms"] += 1
            # A second drag arriving while the modal spins its own event loop.
            controller.request_reschedule("t1", "2026-07-26")
            return True

        controller._confirm_fn = reentrant_confirm
        controller._write_fn = lambda t, d: state["writes"].append((t, d))
        controller.request_reschedule("t1", "2026-07-25")
        assert state["confirms"] == 1, "one drag = at most one dialog"
        assert state["writes"] == [("t1", "2026-07-25")], "= at most one write"

    def test_approval_dispatches_and_reports_dispatched(self, controller):
        """drag-reschedule.md: "Only after you say yes does the app run the
        change"."""
        state = self._wire(controller, approve=True)
        controller.request_reschedule("t1", "2026-07-25")
        assert state["writes"] == [("t1", "2026-07-25")]
        assert len(state["resolved"]) == 1
        payload = state["resolved"][0]
        assert payload["approved"] is True and payload["dispatched"] is True
        assert payload["cancelled"] is False
        assert payload["task_id"] == "t1" and payload["date"] == "2026-07-25"

    def test_declining_writes_nothing_and_reports_cancelled(self, controller):
        """drag-reschedule.md: "The Calendar then shows a short message saying
        the move was cancelled"; "A task moved without a confirmation dialog" is
        the failure the design prevents."""
        state = self._wire(controller, approve=False)
        controller.request_reschedule("t1", "2026-07-25")
        assert state["writes"] == []
        assert state["resolved"][0]["cancelled"] is True
        assert state["resolved"][0]["dispatched"] is False

    def test_a_missing_confirm_gate_fails_closed(self, controller):
        """drag-reschedule.md: "A task moved without a confirmation dialog. Flag
        this immediately ... A silent write is the exact failure the design
        exists to prevent"."""
        writes: list[tuple] = []
        controller._confirm_fn = None
        controller._write_fn = lambda t, d: writes.append((t, d))
        controller.request_reschedule("t1", "2026-07-25")
        assert writes == [], "no confirm possible → no write, ever"

    def test_a_broken_confirm_dialog_counts_as_no(self, controller):
        """drag-reschedule.md: the confirmation "defaults to No" — an unusable
        gate must not become a yes."""
        writes: list[tuple] = []
        controller._confirm_fn = MagicMock(side_effect=RuntimeError("no dialog"))
        controller._write_fn = lambda t, d: writes.append((t, d))
        resolved: list[dict] = []
        controller.reschedule_resolved.connect(
            lambda p: resolved.append(json.loads(p)))
        controller.request_reschedule("t1", "2026-07-25")
        assert writes == []
        assert resolved[0]["approved"] is False

    def test_a_failed_dispatch_is_reported_as_not_started(self, controller):
        """drag-reschedule.md: "You said yes and the message said the move did
        not start. The change was never dispatched"."""
        state = self._wire(controller, approve=True, write_raises=True)
        controller.request_reschedule("t1", "2026-07-25")
        assert state["resolved"][0]["approved"] is True
        assert state["resolved"][0]["dispatched"] is False

    def test_the_confirm_dialog_is_native_names_the_task_and_defaults_to_no(
            self, qapp, monkeypatch):
        """drag-reschedule.md: "A confirmation dialog appears naming the task and
        the new date, and defaults to No. It is a real application dialog, not
        part of the web page"."""
        from PySide6.QtWidgets import QMessageBox
        captured: dict = {}

        def fake_question(parent, title, text, buttons, default):
            captured.update(title=title, text=text, buttons=buttons,
                            default=default)
            return QMessageBox.No

        monkeypatch.setattr(QMessageBox, "question", fake_question)
        stub = SimpleNamespace(_fmt_due=EnablementPage._fmt_due)
        out = EnablementPage._web_reschedule_confirm(
            stub, {"title": "Move me"}, "2026-07-25")

        assert out is False, "No means no"
        assert captured["default"] == QMessageBox.No, "defaults to No"
        assert "Move me" in captured["text"], "names the task"
        assert "2026-07-25" in captured["text"], "names the new date"

        monkeypatch.setattr(QMessageBox, "question",
                            lambda *a, **k: QMessageBox.Yes)
        assert EnablementPage._web_reschedule_confirm(
            stub, {"title": "Move me"}, "2026-07-25") is True

    def test_the_approved_write_uses_the_detail_panel_due_date_path(self, qapp):
        """drag-reschedule.md: "the app run[s] the change, through exactly the
        same guarded path the detail panel's due-date field uses"."""
        import inspect
        src = inspect.getsource(EnablementPage._make_calendar)
        assert 'write_fn=lambda tid, due: self._run_task_writeback(' in src
        assert '"update_due_in_asana", tid, due' in src
        # ... which is the same function name the detail panel's due field wires.
        panel_src = inspect.getsource(EnablementPage._show_task_detail)
        assert '_run_task_writeback("update_due_in_asana"' in panel_src


class TestTaskPanelMirror:
    """task-panel-mirror.md — the Asana-style web task panel."""

    def test_on_by_default_with_the_settings_escape_hatch(self, monkeypatch):
        """task-panel-mirror.md: "The Asana-style panel is on by default …
        set ``enablement.task_web: false`` in the settings file" — and a
        settings error keeps the default rather than killing the surface."""
        import src.data.settings_manager as sm
        from src.ui.web.web_flags import task_web_enabled

        monkeypatch.setattr(sm, "get_section", lambda *_a, **_k: {})
        assert task_web_enabled() is True, "on by default"
        monkeypatch.setattr(sm, "get_section",
                            lambda *_a, **_k: {"task_web": False})
        assert task_web_enabled() is False, "the escape hatch"
        def boom(*_a, **_k):
            raise RuntimeError("settings unreadable")
        monkeypatch.setattr(sm, "get_section", boom)
        assert task_web_enabled() is True, "an unreadable kill switch is not a kill"

    def test_construction_failure_quietly_shows_the_classic_panel(self, monkeypatch):
        """task-panel-mirror.md: "if the new panel cannot be built on your
        machine, the app quietly shows the classic panel instead"."""
        import src.ui.web.task_host as th

        def boom(**_kw):
            raise RuntimeError("no webengine")

        monkeypatch.setattr(th, "build_task_web_triple", boom)
        stub = SimpleNamespace(_run_task_writeback=lambda *a: None,
                               _run_task_refresh=lambda *a: None,
                               _open_source_url=lambda u: None,
                               _set_status=lambda s: None)
        assert EnablementPage._get_task_web_host(stub) == (None, None)
        assert EnablementPage._task_web_available(stub) is False
        # ... and _show_task_detail's web branch precedes the classic panel
        # construction, which remains in place as the fall-through.
        import inspect
        src = inspect.getsource(EnablementPage._show_task_detail)
        assert "_task_web_available" in src
        assert "TaskDetailPanel(task)" in src

    def test_the_writeback_lanes_four_shared_plus_mirror_additions(self):
        """task-panel-mirror.md: "the same four background write-back
        lanes … New with this panel": subtask completion + description
        editing — six lanes total, every one a real asana_writeback
        function."""
        from src.services.task_web import _LANES
        assert set(_LANES.values()) == {
            "set_completed_in_asana", "update_due_in_asana",
            "post_comment_to_asana", "create_subtask_in_asana",
            "set_subtask_completed_in_asana", "update_description_in_asana"}
        for lane in _LANES.values():
            assert callable(getattr(awb, lane)), lane

    def test_description_save_is_remote_first(self):
        """task-panel-mirror.md: "This write is conflict-protected and
        remote-first: if Asana refuses it, nothing changes anywhere"."""
        import inspect
        src = inspect.getsource(awb.update_description_in_asana)
        assert "_cas_precheck" in src, "conflict-protected"
        # the PUT precedes every local mutation in the linked path
        put = src.index("c.update_task(")
        assert put < src.index("set_html_notes")
        assert put < src.rindex("et.update_task(conn, tid, description=")

    def test_only_links_the_task_carries_will_open(self):
        """task-panel-mirror.md: "The panel will only open a link that the
        task actually carries — a link that isn't part of the task's own
        content goes nowhere"."""
        from src.services.task_web import TaskWebController
        opened = []
        ctrl = TaskWebController(open_url_fn=opened.append)
        ctrl.show_task({
            "task_id": "t1", "source": "asana", "source_ref": "9001",
            "title": "x", "source_url": "https://app.asana.com/0/1/9001",
            "extras": {"stories": [
                {"gid": "s1", "subtype": "comment_added", "author": "A",
                 "text": "see https://doc.example/z",
                 "created_at": "2026-07-01T00:00:00Z"}]},
        })
        ctrl.js_open_url("https://evil.example/")
        assert opened == []
        ctrl.js_open_url("https://doc.example/z")
        ctrl.js_open_url("https://app.asana.com/0/1/9001")
        assert opened == ["https://doc.example/z",
                          "https://app.asana.com/0/1/9001"]

    def test_only_a_full_changed_date_is_sent(self):
        """task-panel-mirror.md: "Only a full, changed date is sent"."""
        from src.services.task_web import TaskWebController
        writes = []
        ctrl = TaskWebController(
            write_fn=lambda lane, tid, *a: writes.append((lane, tid) + a))
        ctrl.show_task({"task_id": "t1", "source": "asana",
                        "source_ref": "9001", "title": "x",
                        "due_iso": "2026-09-01"})
        for bad in ("2026-9-1", "tomorrow", "", None, "2026-09-011"):
            ctrl.js_set_due("t1", bad)
        ctrl.js_set_due("t1", "2026-09-01")     # unchanged
        assert writes == []
        ctrl.js_set_due("t1", "2026-09-15")
        assert writes == [("update_due_in_asana", "t1", "2026-09-15")]

    def test_attachments_resolve_fresh_and_open_web_links_only(self):
        """task-panel-mirror.md: "Attachment URLs are never stored. Clicking
        one asks Asana for a fresh link right then, and only web links
        open"."""
        import inspect
        from src.services import task_web
        src = inspect.getsource(task_web.TaskWebController)
        assert "get_attachment" in src, "a fresh per-click resolve"
        assert 'startswith(("http://", "https://"))' in src, "web links only"
        # show_task itself fetches nothing — the panel renders synced data.
        shown = inspect.getsource(task_web.TaskWebController.show_task)
        assert "client_factory" not in shown and "get_attachment" not in shown

    def test_rule_posted_entries_split_from_human_comments(self):
        """task-panel-mirror.md: "Rule-posted entries show with a ⚡ marker
        instead of an avatar" — the split is authorless comment_added
        stories; status changes render as quiet system rows."""
        from src.services.task_vm import story_kind
        assert story_kind({"subtype": "comment_added", "author": "Dana"}) == "comment"
        assert story_kind({"subtype": "comment_added", "author": ""}) == "automation"
        assert story_kind({"subtype": "marked_complete", "author": "Dana"}) == "system"
        assert story_kind({"author": "Dana"}) == "comment", "pre-058 rows stay comments"


class TestTaskPanelParentBreadcrumb:
    """task-panel-mirror.md: "A subtask shows its parent as a breadcrumb …
    clicking it goes back up. A parent tracked here reopens in this same
    panel; one that isn't opens in Asana in your browser"."""

    def test_the_vm_serves_the_parent_and_the_slot_navigates_it(self):
        from PySide6.QtCore import QCoreApplication
        QCoreApplication.instance() or QCoreApplication([])
        from src.services import task_vm
        from src.services.task_web import TaskWebController

        sub = {"task_id": "t1", "source": "asana", "source_ref": "9",
               "title": "Child", "is_subtask": True,
               "parent_task_ref": "800", "parent_title": "Parent card"}
        vm = task_vm.build_task_vm(sub)
        assert vm["header"]["parent"] == {"gid": "800", "title": "Parent card"}

        opened = []
        ctrl = TaskWebController(
            open_parent_fn=lambda tid, gid: opened.append((tid, gid)))
        ctrl.show_task(dict(sub))
        ctrl.js_open_parent("t1")
        assert opened == [("t1", "800")]

    def test_an_untracked_parent_falls_back_to_the_asana_url(self):
        """The article's second sentence, on the page host itself."""
        from types import SimpleNamespace

        from src.ui.pages.enablement.page import EnablementPage

        urls = []
        stub = SimpleNamespace(_all_tasks=[], _show_task_detail=lambda t: None,
                               _open_source_url=lambda u: urls.append(u))
        EnablementPage._open_parent_from_web(stub, "t1", "800")
        assert urls == ["https://app.asana.com/0/0/800/f"]

    def test_a_tracked_parent_reopens_in_the_panel(self):
        from types import SimpleNamespace

        from src.ui.pages.enablement.page import EnablementPage

        shown, urls = [], []
        parent_row = {"task_id": "t9", "source_ref": "800", "title": "Parent"}
        stub = SimpleNamespace(_all_tasks=[parent_row],
                               _show_task_detail=lambda t: shown.append(t),
                               _open_source_url=lambda u: urls.append(u))
        EnablementPage._open_parent_from_web(stub, "t1", "800")
        assert shown == [parent_row] and urls == []
