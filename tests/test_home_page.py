"""Home page — mode tiles, quick actions, recent activity (offscreen)."""

import sqlite3
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from src.ui import app_modes

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _home(db, mode="product"):
    from src.ui.pages.home_page import HomePage
    return HomePage(db, current_mode=mode)


def test_constructs_with_empty_db(qapp, empty_db):
    page = _home(empty_db)
    assert page._last_rows == []
    # Empty state renders one widget in the activity card
    assert page._activity_layout.count() == 1


def test_mode_tiles_reflect_active_mode(qapp, empty_db):
    page = _home(empty_db, mode="product")
    prod = page._tiles[app_modes.MODE_PRODUCT]
    enab = page._tiles[app_modes.MODE_ENABLEMENT]
    assert not prod._switch_btn.isVisibleTo(page)
    assert enab._switch_btn.isVisibleTo(page)
    assert prod._active_pill.isVisibleTo(page)
    assert not enab._active_pill.isVisibleTo(page)

    page.set_mode("enablement")
    assert prod._switch_btn.isVisibleTo(page)
    assert not enab._switch_btn.isVisibleTo(page)


def test_mode_selected_signal(qapp, empty_db):
    page = _home(empty_db, mode="product")
    got = []
    page.mode_selected.connect(got.append)
    page._tiles[app_modes.MODE_ENABLEMENT]._switch_btn.click()
    assert got == ["enablement"]


def test_quick_actions_per_mode(qapp, empty_db):
    page = _home(empty_db, mode="product")
    labels = [b.text() for b in page._qa_buttons]
    assert "Run a search" in labels

    got = []
    page.quick_action.connect(got.append)
    page._qa_buttons[0].click()
    assert got == ["search"]

    page.set_mode("enablement")
    labels = [b.text() for b in page._qa_buttons]
    assert "Open Workbench" in labels and "Chat with Renn" in labels


def test_recent_activity_merges_sources(qapp, empty_db):
    conn = empty_db.conn
    conn.execute(
        "INSERT INTO chat_sessions (session_id, created_at, updated_at, title) "
        "VALUES ('s1', '2026-06-10T10:00:00', '2026-06-10T10:00:00', 'VOC deep dive')"
    )
    conn.execute(
        "INSERT INTO analysis_reports (page, run_at, parameters, summary) "
        "VALUES ('trending', '2026-06-11T09:00:00', '{}', 'Weekly trend report')"
    )
    conn.execute(
        "INSERT INTO enablement_tasks (task_id, source, title, status, updated_at) "
        "VALUES ('t1', 'drive', 'Review SSO card', 'open', '2026-06-11T11:00:00')"
    )
    conn.commit()

    page = _home(empty_db)
    kinds = [r[0] for r in page._last_rows]
    assert kinds[0] == "Task"      # newest first
    assert set(kinds) == {"Chat", "Report", "Task"}
    # One row widget per activity entry
    assert page._activity_layout.count() == len(page._last_rows)


def test_activity_survives_missing_tables(qapp):
    bare = SimpleNamespace(conn=sqlite3.connect(":memory:"))
    page = _home(bare)
    assert page._last_rows == []


def test_unknown_mode_ignored(qapp, empty_db):
    page = _home(empty_db, mode="product")
    page.set_mode("bogus")
    assert page._mode == "product"


class TestUplift:
    def test_stat_cards_render(self, qapp, empty_db):
        page = _home(empty_db, mode="enablement")
        assert page._stats_row.count() >= 3      # 3 stat cards (+ stretch)
        page.set_mode("product")
        assert page._stats_row.count() >= 3

    def test_quick_actions_have_icons(self, qapp, empty_db):
        page = _home(empty_db, mode="enablement")
        assert page._qa_buttons
        assert all(not b.icon().isNull() for b in page._qa_buttons)

    def test_activity_rows_are_clickable(self, qapp, empty_db):
        conn = empty_db.conn
        conn.execute(
            "INSERT INTO enablement_tasks (task_id, source, title, status, updated_at) "
            "VALUES ('t9', 'drive', 'Review card', 'open', '2026-06-11T11:00:00')")
        conn.commit()
        page = _home(empty_db)
        from src.ui.pages.home_page import _ClickRow
        rows = [page._activity_layout.itemAt(i).widget()
                for i in range(page._activity_layout.count())]
        rows = [r for r in rows if isinstance(r, _ClickRow)]
        assert rows
        got = []
        page.activity_activated.connect(got.append)
        rows[0].clicked.emit()
        assert got and got[0] in ("Chat", "Report", "Task")
