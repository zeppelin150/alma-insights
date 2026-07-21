"""
Test Module: Stage 4 — Data Warehouse Page, Virtual Scroll, Ticket Detail, TRC History
Stage: 4
Dependencies: Stage 1-3 complete

Covers:
  - WarehouseQuery.get_conversations_paged() — pagination, filtering, total count
  - WarehouseQuery.get_trc_history() — volume, top issues, related TRCs
  - WarehouseTableModel — lazy loading, fetchMore, canFetchMore, filter reset
  - TicketDetailPanel — population from row data, empty/hidden state
  - TRCHistoryPanel — sparkline generation, trend calculation, bar chart
  - DataWarehousePage — page init, filter bar, empty state
  - Sidebar integration — PAGE_DATA_WAREHOUSE constant, _page_widgets

Run:
  - Single file:  python -m pytest tests/test_stage4_data_warehouse.py -x -v
"""

import sqlite3
import json
from pathlib import Path

import pytest


# ═══════════════════════════════════════════
#  FIXTURES
# ═══════════════════════════════════════════

@pytest.fixture
def warehouse_db(tmp_path):
    """Create a test DB with source_registry and per-source tables seeded with data."""
    db_path = tmp_path / "wh_test.db"
    conn = sqlite3.connect(str(db_path))

    # Create source_registry (must match real schema from migration 011)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS source_registry (
            source_id TEXT PRIMARY KEY,
            source_name TEXT NOT NULL,
            source_type TEXT NOT NULL,
            table_prefix TEXT NOT NULL UNIQUE,
            column_mapping TEXT,
            created_at TEXT NOT NULL,
            is_default INTEGER DEFAULT 0,
            ticket_count INTEGER DEFAULT 0,
            last_import_at TEXT
        );
        INSERT OR IGNORE INTO source_registry (source_id, source_name, source_type, table_prefix, created_at, is_default, ticket_count)
        VALUES ('zendesk_default', 'Zendesk Support', 'zendesk', 'zendesk_default', datetime('now'), 1, 0);
    """)

    # Create per-source conversation table
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS [zendesk_default_conversations] (
            ticket_id TEXT,
            subject TEXT,
            trc_code TEXT,
            trc_label TEXT,
            status TEXT DEFAULT 'closed',
            csat_score REAL,
            created_at TEXT,
            solved_at TEXT,
            message_count INTEGER DEFAULT 1,
            client_messages INTEGER DEFAULT 0,
            agent_messages INTEGER DEFAULT 0,
            full_thread TEXT,
            thread_preview TEXT,
            dataset_id TEXT
        );
        CREATE TABLE IF NOT EXISTS [zendesk_default_tickets] (
            ticket_id TEXT PRIMARY KEY,
            subject TEXT,
            status TEXT,
            created_at TEXT,
            provider_id TEXT,
            client_id TEXT
        );
    """)

    # Seed 250 conversations
    for i in range(250):
        trc = f"TRC-{(i % 5) + 1:03d}"
        trc_label = f"Issue Type {(i % 5) + 1}"
        day = f"2026-03-{(i % 28) + 1:02d}"
        csat = round(1.0 + (i % 50) / 10.0, 1)
        conn.execute(
            "INSERT INTO [zendesk_default_conversations] "
            "(ticket_id, subject, trc_code, trc_label, status, csat_score, "
            "created_at, solved_at, message_count, client_messages, agent_messages, "
            "full_thread, thread_preview, dataset_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"T-{i:04d}", f"Test ticket {i}", trc, trc_label, "closed", csat,
             day, day, 3, 1, 2, f"Thread {i}", f"Preview {i}", "ds1"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO [zendesk_default_tickets] "
            "(ticket_id, subject, status, created_at) VALUES (?,?,?,?)",
            (f"T-{i:04d}", f"Test ticket {i}", "closed", day),
        )

    conn.execute("UPDATE source_registry SET ticket_count = 250 WHERE source_id = 'zendesk_default'")
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def wq(warehouse_db):
    """Create a WarehouseQuery instance."""
    from src.data.source_registry import SourceRegistry
    from src.data.warehouse_query import WarehouseQuery
    registry = SourceRegistry(warehouse_db)
    return WarehouseQuery(warehouse_db, registry)


@pytest.fixture
def empty_db(tmp_path):
    """DB with source_registry but empty per-source tables."""
    db_path = tmp_path / "empty_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE source_registry (
            source_id TEXT PRIMARY KEY,
            source_name TEXT NOT NULL,
            source_type TEXT NOT NULL,
            table_prefix TEXT NOT NULL UNIQUE,
            column_mapping TEXT,
            created_at TEXT NOT NULL,
            is_default INTEGER DEFAULT 0,
            ticket_count INTEGER DEFAULT 0,
            last_import_at TEXT
        );
        INSERT INTO source_registry (source_id, source_name, source_type, table_prefix, created_at, is_default, ticket_count)
        VALUES ('zendesk_default', 'Zendesk Support', 'zendesk', 'zendesk_default', datetime('now'), 1, 0);
        CREATE TABLE [zendesk_default_conversations] (
            ticket_id TEXT, subject TEXT, trc_code TEXT, trc_label TEXT,
            status TEXT, csat_score REAL, created_at TEXT, solved_at TEXT,
            message_count INTEGER, client_messages INTEGER, agent_messages INTEGER,
            full_thread TEXT, thread_preview TEXT, dataset_id TEXT
        );
        CREATE TABLE [zendesk_default_tickets] (
            ticket_id TEXT PRIMARY KEY, subject TEXT, status TEXT, created_at TEXT
        );
    """)
    conn.commit()
    yield conn
    conn.close()


# ═══════════════════════════════════════════
#  WAREHOUSE QUERY PAGINATED TESTS
# ═══════════════════════════════════════════

class TestGetConversationsPaged:
    """Tests for WarehouseQuery.get_conversations_paged()."""

    def test_first_page(self, wq):
        rows, total = wq.get_conversations_paged(offset=0, limit=100)
        assert len(rows) == 100
        assert total == 250

    def test_second_page(self, wq):
        rows, total = wq.get_conversations_paged(offset=100, limit=100)
        assert len(rows) == 100
        assert total == 250

    def test_last_page(self, wq):
        rows, total = wq.get_conversations_paged(offset=200, limit=100)
        assert len(rows) == 50
        assert total == 250

    def test_beyond_end(self, wq):
        rows, total = wq.get_conversations_paged(offset=300, limit=100)
        assert len(rows) == 0
        assert total == 250

    def test_rows_are_dicts(self, wq):
        rows, _ = wq.get_conversations_paged(offset=0, limit=5)
        assert isinstance(rows[0], dict)
        assert "ticket_id" in rows[0]
        assert "subject" in rows[0]
        assert "source_name" in rows[0]

    def test_filter_by_trc(self, wq):
        rows, total = wq.get_conversations_paged(trc_filter="TRC-001")
        assert total == 50  # 250/5 TRC codes
        assert all(r["trc_code"] == "TRC-001" for r in rows)

    def test_filter_by_date(self, wq):
        rows, total = wq.get_conversations_paged(
            date_start="2026-03-01", date_end="2026-03-05"
        )
        assert total > 0
        for r in rows:
            assert "2026-03-01" <= r["created_at"] <= "2026-03-05"

    def test_filter_by_keyword(self, wq):
        rows, total = wq.get_conversations_paged(keyword="ticket 10")
        assert total > 0
        for r in rows:
            assert "ticket 10" in r["subject"].lower() or "ticket 10" in str(r.get("thread_preview", "")).lower()

    def test_no_duplicate_rows(self, wq):
        page1, _ = wq.get_conversations_paged(offset=0, limit=100)
        page2, _ = wq.get_conversations_paged(offset=100, limit=100)
        ids1 = {r["ticket_id"] for r in page1}
        ids2 = {r["ticket_id"] for r in page2}
        assert ids1.isdisjoint(ids2), "Pages should not share ticket IDs"

    def test_empty_warehouse(self, empty_db):
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        registry = SourceRegistry(empty_db)
        wq = WarehouseQuery(empty_db, registry)
        rows, total = wq.get_conversations_paged()
        assert rows == []
        assert total == 0

    def test_source_name_populated(self, wq):
        rows, _ = wq.get_conversations_paged(limit=1)
        assert rows[0]["source_name"] != "Unknown"

    def test_combined_filters(self, wq):
        rows, total = wq.get_conversations_paged(
            trc_filter="TRC-001",
            date_start="2026-03-01",
            date_end="2026-03-10",
        )
        for r in rows:
            assert r["trc_code"] == "TRC-001"
            assert "2026-03-01" <= r["created_at"] <= "2026-03-10"


# ═══════════════════════════════════════════
#  TRC HISTORY TESTS
# ═══════════════════════════════════════════

class TestGetTrcHistory:
    """Tests for WarehouseQuery.get_trc_history()."""

    # The fixture seeds fixed 2026-03 dates, but get_trc_history's default
    # 90-day window is anchored to wall-clock 'now' and ages them out.
    # Pin a window wide enough to always cover the seeded range.
    DAYS_COVERING_SEEDED_DATA = 100_000

    def test_returns_dict(self, wq):
        result = wq.get_trc_history("TRC-001")
        assert isinstance(result, dict)
        assert "volume_by_day" in result
        assert "top_issues" in result
        assert "total" in result
        assert "avg_csat" in result
        assert "related_trcs" in result

    def test_volume_by_day(self, wq):
        result = wq.get_trc_history("TRC-001", days=self.DAYS_COVERING_SEEDED_DATA)
        assert len(result["volume_by_day"]) > 0
        # Each entry is (date_str, count)
        for d, c in result["volume_by_day"]:
            assert isinstance(d, str)
            assert isinstance(c, int)
            assert c > 0

    def test_total_count(self, wq):
        result = wq.get_trc_history("TRC-001", days=self.DAYS_COVERING_SEEDED_DATA)
        assert result["total"] == 50  # 250/5 TRC codes

    def test_avg_csat(self, wq):
        result = wq.get_trc_history("TRC-001", days=self.DAYS_COVERING_SEEDED_DATA)
        assert result["avg_csat"] is not None
        assert 0 < result["avg_csat"] < 10

    def test_top_issues(self, wq):
        result = wq.get_trc_history("TRC-001", days=self.DAYS_COVERING_SEEDED_DATA)
        # All 50 tickets for TRC-001 have the same trc_label
        assert len(result["top_issues"]) >= 1

    def test_nonexistent_trc(self, wq):
        result = wq.get_trc_history("NONEXISTENT")
        assert result["total"] == 0
        assert result["volume_by_day"] == []

    def test_none_trc(self, wq):
        result = wq.get_trc_history(None)
        assert result["total"] == 0

    def test_related_trcs(self, wq):
        # With our test data, ticket_ids for TRC-001 also appear only once
        # so co-occurrence is 0. That's OK — test the shape.
        result = wq.get_trc_history("TRC-001")
        assert isinstance(result["related_trcs"], list)


# ═══════════════════════════════════════════
#  VIRTUAL SCROLL TABLE MODEL TESTS
# ═══════════════════════════════════════════

class TestWarehouseTableModel:
    """Tests for the WarehouseTableModel (QAbstractTableModel)."""

    @pytest.fixture
    def model(self, wq):
        from src.ui.widgets.virtual_scroll_table import WarehouseTableModel
        m = WarehouseTableModel(wq)
        return m

    def test_initial_state(self, model):
        assert model.rowCount() == 0
        assert model.columnCount() == 7  # COLUMNS length

    def test_load_initial(self, model):
        model.load_initial()
        assert model.rowCount() == 100  # PAGE_SIZE
        assert model.total_count == 250

    def test_can_fetch_more(self, model):
        model.load_initial()
        assert model.canFetchMore()

    def test_fetch_more(self, model):
        model.load_initial()
        assert model.rowCount() == 100
        model.fetchMore()
        assert model.rowCount() == 200

    def test_fetch_all(self, model):
        model.load_initial()
        model.fetchMore()
        model.fetchMore()
        assert model.rowCount() == 250
        assert not model.canFetchMore()

    def test_no_duplicate_rows_across_fetches(self, model):
        model.load_initial()
        ids_after_first = {model.get_row_data(i)["ticket_id"] for i in range(model.rowCount())}
        model.fetchMore()
        all_ids = {model.get_row_data(i)["ticket_id"] for i in range(model.rowCount())}
        # All first-page IDs still present, and more added
        assert ids_after_first.issubset(all_ids)
        assert len(all_ids) == 200

    def test_get_row_data(self, model):
        model.load_initial()
        data = model.get_row_data(0)
        assert isinstance(data, dict)
        assert "ticket_id" in data

    def test_get_row_data_out_of_range(self, model):
        model.load_initial()
        assert model.get_row_data(999) is None
        assert model.get_row_data(-1) is None

    def test_set_filters_resets(self, model):
        model.load_initial()
        assert model.rowCount() == 100
        model.set_filters(trc_filter="TRC-001")
        assert model.total_count == 50
        assert model.rowCount() <= 50

    def test_header_data(self, model):
        from PySide6.QtCore import Qt
        assert model.headerData(0, Qt.Horizontal) == "ID"
        assert model.headerData(3, Qt.Horizontal) == "Subject"

    def test_data_returns_string(self, model):
        from PySide6.QtCore import Qt
        model.load_initial()
        from PySide6.QtCore import QModelIndex
        idx = model.index(0, 0)
        val = model.data(idx, Qt.DisplayRole)
        assert isinstance(val, str)

    def test_empty_model(self, empty_db):
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        from src.ui.widgets.virtual_scroll_table import WarehouseTableModel
        registry = SourceRegistry(empty_db)
        wq = WarehouseQuery(empty_db, registry)
        m = WarehouseTableModel(wq)
        m.load_initial()
        assert m.rowCount() == 0
        assert not m.canFetchMore()


# ═══════════════════════════════════════════
#  SPARKLINE UTILITY TESTS
# ═══════════════════════════════════════════

class TestSparkline:
    """Test the _sparkline utility function."""

    def test_empty(self):
        from src.ui.widgets.trc_history_panel import _sparkline
        assert _sparkline([]) == ""

    def test_uniform(self):
        from src.ui.widgets.trc_history_panel import _sparkline
        result = _sparkline([5, 5, 5, 5])
        assert len(result) == 4

    def test_increasing(self):
        from src.ui.widgets.trc_history_panel import _sparkline
        result = _sparkline([0, 2, 4, 6, 8])
        # Should produce ascending characters
        assert len(result) == 5

    def test_single_value(self):
        from src.ui.widgets.trc_history_panel import _sparkline
        result = _sparkline([10])
        assert len(result) == 1


# ═══════════════════════════════════════════
#  TRC HISTORY PANEL TREND CALCULATION TESTS
# ═══════════════════════════════════════════

class TestTRCHistoryTrend:
    """Test trend calculation logic (extracted to avoid QWidget instantiation)."""

    def _calc_trend(self, counts):
        """Replicate TRCHistoryPanel._calc_trend logic without QWidget."""
        from src.ui.theme import ALMA_SUCCESS, ALMA_ERROR
        if len(counts) < 4:
            return "insufficient data"
        half = len(counts) // 2
        first_half = sum(counts[:half])
        second_half = sum(counts[half:])
        if first_half == 0 and second_half == 0:
            return "no activity"
        if first_half == 0:
            return "rising"
        ratio = (second_half - first_half) / first_half
        if ratio > 0.15:
            return "rising"
        elif ratio < -0.15:
            return "declining"
        else:
            return "stable"

    def test_rising_trend(self):
        result = self._calc_trend([1, 1, 1, 1, 5, 5, 5, 5])
        assert "rising" in result

    def test_declining_trend(self):
        result = self._calc_trend([5, 5, 5, 5, 1, 1, 1, 1])
        assert "declining" in result

    def test_stable_trend(self):
        result = self._calc_trend([5, 5, 5, 5, 5, 5, 5, 5])
        assert "stable" in result

    def test_insufficient_data(self):
        result = self._calc_trend([5, 5])
        assert "insufficient" in result


# ═══════════════════════════════════════════
#  TICKET DETAIL PANEL TESTS
# ═══════════════════════════════════════════

class TestTicketDetailPanel:
    """Test TicketDetailPanel population and state management.

    Uses a shared QApplication to avoid segfaults from multiple Qt app instances.
    """

    @pytest.fixture(scope="class")
    def qapp(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if not app:
            app = QApplication([])
        return app

    @pytest.fixture
    def panel(self, qapp):
        from src.ui.widgets.ticket_detail_panel import TicketDetailPanel
        return TicketDetailPanel()

    def test_initial_hidden(self, panel):
        assert panel.isHidden()

    def test_set_data_shows_panel(self, panel):
        panel.set_ticket_data({"ticket_id": "T-001", "trc_code": "TRC-001"})
        assert not panel.isHidden()

    def test_clear_hides_panel(self, panel):
        panel.set_ticket_data({"ticket_id": "T-001"})
        panel.clear()
        assert panel.isHidden()

    def test_header_shows_ticket_id(self, panel):
        panel.set_ticket_data({"ticket_id": "T-1234", "trc_code": "TRC-001"})
        assert "T-1234" in panel._header_label.text()

    def test_overview_fields_populated(self, panel):
        panel.set_ticket_data({
            "ticket_id": "T-001",
            "trc_code": "TRC-001",
            "status": "closed",
            "csat_score": 3.5,
            "created_at": "2026-03-15T10:00:00",
            "message_count": 5,
            "source_name": "Zendesk Support",
        })
        assert panel._overview_fields["TRC"].text() == "TRC-001"
        assert panel._overview_fields["Status"].text() == "closed"
        assert panel._overview_fields["CSAT"].text() == "3.5"
        assert panel._overview_fields["Source"].text() == "Zendesk Support"

    def test_nlp_data_displayed(self, panel):
        panel.set_ticket_data(
            {"ticket_id": "T-001"},
            nlp_data={
                "ngrams": ["prior auth", "peer review"],
                "issue_types": ["Authorization Delay"],
                "entities": [],
            }
        )
        text = panel._nlp_label.text()
        assert "prior auth" in text
        assert "Authorization Delay" in text


# ═══════════════════════════════════════════
#  SIDEBAR INTEGRATION TESTS
# ═══════════════════════════════════════════

class TestSidebarIntegration:
    """Test that Data Warehouse is properly registered in main_window."""

    def test_page_constant_exists(self):
        from src.ui.main_window import MainWindow
        assert hasattr(MainWindow, "PAGE_DATA_WAREHOUSE")
        assert MainWindow.PAGE_DATA_WAREHOUSE == 11

    def test_page_constant_unique(self):
        from src.ui.main_window import MainWindow
        constants = [
            MainWindow.PAGE_CONVERSATIONS, MainWindow.PAGE_DASHBOARD,
            MainWindow.PAGE_TRENDING, MainWindow.PAGE_INCIDENTS,
            MainWindow.PAGE_REPORTS, MainWindow.PAGE_AB_COMPARE,
            MainWindow.PAGE_SMART_REPORTING, MainWindow.PAGE_SETTINGS,
            MainWindow.PAGE_SOURCE_MONITOR, MainWindow.PAGE_GURU,
            MainWindow.PAGE_GEMINI_CHATS, MainWindow.PAGE_DATA_WAREHOUSE,
        ]
        assert len(constants) == len(set(constants)), "PAGE constants must be unique"

    def test_other_constants_unchanged(self):
        """Verify existing page constants were NOT shifted."""
        from src.ui.main_window import MainWindow
        assert MainWindow.PAGE_CONVERSATIONS == 0
        assert MainWindow.PAGE_DASHBOARD == 1
        assert MainWindow.PAGE_TRENDING == 2
        assert MainWindow.PAGE_INCIDENTS == 3
        assert MainWindow.PAGE_REPORTS == 4
        assert MainWindow.PAGE_AB_COMPARE == 5
        assert MainWindow.PAGE_SMART_REPORTING == 6
        assert MainWindow.PAGE_SETTINGS == 7
        assert MainWindow.PAGE_SOURCE_MONITOR == 8
        assert MainWindow.PAGE_GURU == 9
        assert MainWindow.PAGE_GEMINI_CHATS == 10


# ═══════════════════════════════════════════
#  DATA WAREHOUSE PAGE INIT TESTS
# ═══════════════════════════════════════════

class TestDataWarehousePageInit:
    """Test DataWarehousePage component structure (no DB required)."""

    @pytest.fixture(scope="class")
    def qapp(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if not app:
            app = QApplication([])
        return app

    @pytest.fixture
    def fake_db(self):
        class FakeDB:
            db_path = ":memory:"
            conn = None
        return FakeDB()

    def test_page_creates(self, qapp, fake_db):
        from src.ui.pages.data_warehouse_page import DataWarehousePage
        page = DataWarehousePage(fake_db)
        assert page is not None

    def test_page_has_filter_widgets(self, qapp, fake_db):
        from src.ui.pages.data_warehouse_page import DataWarehousePage
        page = DataWarehousePage(fake_db)
        assert hasattr(page, "_source_selector")
        assert hasattr(page, "_date_from")
        assert hasattr(page, "_date_to")
        assert hasattr(page, "_trc_combo")
        assert hasattr(page, "_keyword_input")

    def test_page_has_panels(self, qapp, fake_db):
        from src.ui.pages.data_warehouse_page import DataWarehousePage
        page = DataWarehousePage(fake_db)
        assert hasattr(page, "_detail_panel")
        assert hasattr(page, "_trc_panel")
        assert hasattr(page, "_table_widget")
        assert hasattr(page, "_empty_state")


# ═══════════════════════════════════════════
#  SOURCE SELECTOR REGRESSION
# ═══════════════════════════════════════════

class TestSourceSelectorWidget:
    """Verify source_selector.py still works correctly."""

    @pytest.fixture(scope="class")
    def qapp(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if not app:
            app = QApplication([])
        return app

    def test_default_all_sources(self, qapp):
        from src.ui.widgets.source_selector import SourceSelector
        sel = SourceSelector()
        assert sel.count() == 1
        assert sel.selected_source_id() is None

    def test_refresh_populates(self, qapp, warehouse_db):
        from src.ui.widgets.source_selector import SourceSelector
        sel = SourceSelector()
        sel.refresh_sources(warehouse_db)
        assert sel.count() >= 2  # "All Sources" + at least one source

    def test_signal_emitted(self, qapp, warehouse_db):
        from src.ui.widgets.source_selector import SourceSelector
        sel = SourceSelector()
        sel.refresh_sources(warehouse_db)
        signals = []
        sel.source_changed.connect(lambda s: signals.append(s))
        sel.setCurrentIndex(1)  # Switch to first real source
        assert len(signals) == 1


# ═══════════════════════════════════════════
#  REGRESSION TESTS
# ═══════════════════════════════════════════

class TestStage4Regressions:
    """Ensure Session 4 changes don't break existing functionality."""

    def test_warehouse_query_get_conversations_still_works(self, wq):
        """Original get_conversations() still returns data."""
        rows = wq.get_conversations(limit=10)
        assert len(rows) > 0
        assert isinstance(rows[0], dict)

    def test_warehouse_query_get_ticket_count(self, wq):
        count = wq.get_ticket_count()
        assert count == 250

    def test_warehouse_query_get_trc_distribution(self, wq):
        dist = wq.get_trc_distribution()
        assert len(dist) == 5  # 5 TRC codes

    def test_warehouse_query_search_fts_unaffected(self, warehouse_db):
        """FTS should still work (or fail gracefully without FTS table)."""
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        registry = SourceRegistry(warehouse_db)
        wq = WarehouseQuery(warehouse_db, registry)
        # No FTS table in our test fixture, should return empty gracefully
        results = wq.search_fts("test")
        assert isinstance(results, list)

    def test_import_still_works(self):
        """ImportMode enum still accessible."""
        from src.data.import_mode import ImportMode
        assert ImportMode.INCREMENTAL is not None
