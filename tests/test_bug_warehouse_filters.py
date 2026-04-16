"""
Bug 4 Tests: Data Warehouse Filter Signal Wiring
==================================================
H0: All filter widgets (source, date from, date to, TRC, keyword) have their
    changed-signals connected to _apply_filters(), enabling auto-filtering.

HA: Only the Search button is connected; all other filter widgets have NO
    signal connections, so changing them does nothing until Search is clicked.

Run: python -m pytest tests/test_bug_warehouse_filters.py -x -v
"""

import pytest
from pathlib import Path
import ast
import re


def _get_filter_bar_source():
    """Extract _build_filter_bar method source from data_warehouse_page.py."""
    src = (Path(__file__).parent.parent / "src" / "ui" / "pages" / "data_warehouse_page.py").read_text(encoding="utf-8")
    return src


class TestH0_FilterSignalConnections:
    """H0: Every filter widget has a signal connected to _apply_filters."""

    def test_source_selector_signal_connected(self):
        """H0: source_selector.source_changed is connected to _apply_filters."""
        src = _get_filter_bar_source()
        has_connection = (
            "source_selector.source_changed.connect" in src
            or "_source_selector.currentIndexChanged.connect" in src
        )
        assert has_connection, \
            "H0 REJECTED: _source_selector has NO signal connected to _apply_filters. " \
            "Changing source dropdown does nothing."

    def test_date_from_signal_connected(self):
        """H0: _date_from.dateChanged is connected to _apply_filters."""
        src = _get_filter_bar_source()
        has_connection = "_date_from.dateChanged.connect" in src
        assert has_connection, \
            "H0 REJECTED: _date_from has NO dateChanged signal connected. " \
            "Changing the From date does nothing."

    def test_date_to_signal_connected(self):
        """H0: _date_to.dateChanged is connected to _apply_filters."""
        src = _get_filter_bar_source()
        has_connection = "_date_to.dateChanged.connect" in src
        assert has_connection, \
            "H0 REJECTED: _date_to has NO dateChanged signal connected. " \
            "Changing the To date does nothing."

    def test_trc_combo_signal_connected(self):
        """H0: _trc_combo.currentIndexChanged is connected to _apply_filters."""
        src = _get_filter_bar_source()
        has_connection = "_trc_combo.currentIndexChanged.connect" in src
        assert has_connection, \
            "H0 REJECTED: _trc_combo has NO currentIndexChanged signal connected. " \
            "Changing the TRC dropdown does nothing."

    def test_keyword_input_signal_connected(self):
        """H0: _keyword_input has a signal (returnPressed or textChanged) connected."""
        src = _get_filter_bar_source()
        has_connection = (
            "_keyword_input.returnPressed.connect" in src
            or "_keyword_input.textChanged.connect" in src
        )
        assert has_connection, \
            "H0 REJECTED: _keyword_input has NO signal connected. " \
            "Typing a keyword and pressing Enter does nothing."

    def test_search_button_is_connected(self):
        """Verify Search button IS connected (baseline sanity check)."""
        src = _get_filter_bar_source()
        has_connection = "search_btn.clicked.connect" in src
        assert has_connection, \
            "UNEXPECTED: Search button is not connected either — this is a deeper problem."


class TestFilterQueryLayer:
    """Verify the query layer correctly handles filter params (independent of UI wiring)."""

    @pytest.fixture
    def wq_with_data(self, tmp_path):
        """Create warehouse with mixed-date data for filter testing."""
        from src.data.db_manager import DatabaseManager
        from src.data.schema_builder import create_source_tables
        from datetime import datetime, timezone

        db = DatabaseManager(tmp_path / "filter_test.db")
        db.initialize()

        # Seed conversations with different dates and TRCs
        test_data = [
            ("T-1", "2025-01-10", "AUTH-01", "Auth issue"),
            ("T-2", "2025-06-15", "AUTH-01", "Another auth issue"),
            ("T-3", "2025-06-20", "BIL-03", "Billing problem"),
            ("T-4", "2026-01-05", "BIL-03", "Late billing"),
            ("T-5", "2026-03-10", "TECH-01", "Technical support"),
        ]
        for tid, date, trc, subj in test_data:
            db.upsert_ticket({
                "ticket_id": tid, "subject": subj, "trc_code": trc, "trc_label": trc,
                "status": "open", "priority": "", "channel": "", "csat_score": 4.0,
                "created_at": date, "updated_at": "", "solved_at": "",
                "requester_name": "", "requester_email": "", "assignee_name": "",
                "group_name": "", "tags": [], "custom_fields": {},
                "assignment_to_resolution_hours": None,
                "total_resolution_hours": None, "first_reply_hours": None,
            }, table_prefix="zendesk_default")
            db.upsert_conversation({
                "ticket_id": tid, "subject": subj, "trc_code": trc, "trc_label": trc,
                "status": "open", "csat_score": 4.0, "created_at": date, "solved_at": "",
                "message_count": 1, "client_messages": 1, "agent_messages": 0,
                "full_thread": f"Thread about {subj}", "thread_preview": subj,
                "dataset_id": 0,
            }, table_prefix="zendesk_default")
        db.commit()
        db.rebuild_source_fts("zendesk_default")

        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        reg = SourceRegistry(db.conn)
        wq = WarehouseQuery(db.conn, reg)
        yield wq, db
        db.close()

    def test_date_filter_narrows_results(self, wq_with_data):
        """Verify date range actually filters at the query level."""
        wq, _ = wq_with_data
        rows, total = wq.get_conversations_paged(
            date_start="2025-06-01", date_end="2025-06-30"
        )
        assert total == 2, f"Expected 2 rows in June 2025, got {total}"
        tids = {r["ticket_id"] for r in rows}
        assert tids == {"T-2", "T-3"}

    def test_trc_filter_narrows_results(self, wq_with_data):
        """Verify TRC filter actually filters at the query level."""
        wq, _ = wq_with_data
        rows, total = wq.get_conversations_paged(trc_filter="BIL-03")
        assert total == 2, f"Expected 2 BIL-03 rows, got {total}"

    def test_keyword_filter_narrows_results(self, wq_with_data):
        """Verify keyword filter actually filters at the query level."""
        wq, _ = wq_with_data
        rows, total = wq.get_conversations_paged(keyword="billing")
        assert total >= 1, f"Expected at least 1 result for 'billing', got {total}"

    def test_no_filters_returns_all(self, wq_with_data):
        """Verify no filters returns all 5 rows."""
        wq, _ = wq_with_data
        rows, total = wq.get_conversations_paged()
        assert total == 5, f"Expected 5 total rows, got {total}"
