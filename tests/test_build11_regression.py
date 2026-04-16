"""
Build 11.0 Regression Test Suite

Validates all Build 11.0 changes: migration, services,
UI tabs, new page, CLI tool, and widgets.
"""

import sqlite3
import json
import subprocess
import sys
import tempfile
import os
import pytest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MIGRATION_SQL = (_PROJECT_ROOT / "migrations" / "005_persistence_layer.sql").read_text(encoding="utf-8")


@pytest.fixture
def db_conn():
    db = sqlite3.connect(":memory:")
    db.executescript(_MIGRATION_SQL)
    yield db
    db.close()


# ═══════════════════════════════════════
#  MIGRATION
# ═══════════════════════════════════════

class TestMigration005:
    def test_all_tables_exist(self, db_conn):
        tables = {
            r[0] for r in db_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        expected = {
            "ticket_index", "scan_category_snapshots", "analysis_runs",
            "trend_snapshots", "insight_ledger", "chat_sessions",
            "report_definitions",
        }
        assert expected.issubset(tables)

    def test_ticket_index_has_correct_columns(self, db_conn):
        cols = {
            r[1] for r in db_conn.execute("PRAGMA table_info(ticket_index)").fetchall()
        }
        assert "ticket_id" in cols
        assert "issue_snippet" in cols
        assert "classification_confidence" in cols
        assert "entities_json" in cols


# ═══════════════════════════════════════
#  SERVICES
# ═══════════════════════════════════════

class TestTicketIndexService:
    def test_upsert_and_dedup(self, db_conn):
        from src.services.ticket_index_writer import (
            should_classify_ticket, upsert_ticket_index, update_scan_reference,
        )
        # New ticket
        assert should_classify_ticket("T-1", "s1", db_conn) == "classify"

        # Upsert it
        upsert_ticket_index("T-1", "s1", {"summary": "Test", "sub_cluster_confidence": 0.9}, {}, db_conn)
        db_conn.commit()

        # Same scan → skip
        assert should_classify_ticket("T-1", "s1", db_conn) == "skip"

        # Different scan, high confidence → update
        assert should_classify_ticket("T-1", "s2", db_conn) == "update_scan_id"


class TestClearSession:
    def test_clear_preserves_ticket_index(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(path)
        conn.executescript(_MIGRATION_SQL)
        conn.execute("CREATE TABLE IF NOT EXISTS tickets (ticket_id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO tickets VALUES ('T-1')")
        conn.execute(
            "INSERT INTO ticket_index (ticket_id, first_seen_scan_id, last_seen_scan_id, first_seen_date) "
            "VALUES ('T-1', 's1', 's1', datetime('now'))"
        )
        conn.commit()
        conn.close()

        from src.services.clear_session import clear_session_data
        clear_session_data(path)

        conn = sqlite3.connect(path)
        # Stage 1: tickets are now permanent — clear_session preserves them
        assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM ticket_index").fetchone()[0] == 1
        conn.close()
        os.unlink(path)


class TestChatSession:
    def test_full_lifecycle(self, db_conn):
        from src.services.chat_session import (
            create_session, append_message, load_session, list_sessions,
        )
        sid = create_session("test_page", conn=db_conn)
        append_message(sid, "user", "Hello", db_conn)
        append_message(sid, "assistant", "Hi!", db_conn)

        session = load_session(sid, db_conn)
        assert len(session["messages"]) == 2

        sessions = list_sessions(10, db_conn)
        assert len(sessions) == 1


class TestPostReportPersist:
    def test_persist_and_retrieve(self, db_conn):
        from src.services.post_report_persist import persist_report_run
        run_id = persist_report_run(
            {"prompt_template": "test", "output_text": "report", "ticket_count": 42},
            db_conn,
        )
        row = db_conn.execute(
            "SELECT ticket_count FROM analysis_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert row[0] == 42


# ═══════════════════════════════════════
#  IMPORTS (verify no syntax/import errors)
# ═══════════════════════════════════════

class TestImports:
    def test_services_import(self):
        from src.services.ticket_index_writer import should_classify_ticket
        from src.services.clear_session import clear_session_data
        from src.services.post_scan_persist import run_post_scan_persistence
        from src.services.post_report_persist import persist_report_run
        from src.services.chat_session import create_session
        from src.services.context_injector import build_context

    def test_tools_import(self):
        from src.tools.alma_query import cmd_tickets, cmd_insights, main

    def test_widgets_import(self):
        from src.ui.widgets.evidence_panel import EvidencePanel
        from src.ui.widgets.report_section_renderer import ReportSectionRenderer
        from src.ui.widgets.trend_sparkline import TrendSparkline
        from src.ui.widgets.ticket_preview_card import TicketPreviewCard

    def test_new_pages_import(self):
        from src.ui.pages.gemini_chats_page import GeminiChatsPage
        from src.ui.pages.ai_reports_history_tab import ReportHistoryTab
        from src.ui.pages.ai_reports_prompts_tab import ManagePromptsTab


# ═══════════════════════════════════════
#  ALMA_QUERY CLI
# ═══════════════════════════════════════

class TestAlmaQueryCLI:
    def test_tickets_empty_db(self):
        result = subprocess.run(
            [sys.executable, "-m", "src.tools.alma_query", "tickets", "--limit", "5"],
            capture_output=True, text=True, cwd=str(_PROJECT_ROOT),
        )
        data = json.loads(result.stdout)
        assert isinstance(data, list)

    def test_insights_empty_db(self):
        result = subprocess.run(
            [sys.executable, "-m", "src.tools.alma_query", "insights"],
            capture_output=True, text=True, cwd=str(_PROJECT_ROOT),
        )
        data = json.loads(result.stdout)
        assert isinstance(data, list)


# ═══════════════════════════════════════
#  REPORT DEFINITIONS
# ═══════════════════════════════════════

class TestReportDefinitions:
    def test_voc_root_cause_json_valid(self):
        path = _PROJECT_ROOT / "src" / "data" / "report_definitions" / "voc_root_cause.json"
        assert path.exists()
        config = json.loads(path.read_text(encoding="utf-8"))
        assert config["name"] == "VOC Root Cause Analysis"
        assert len(config["specialists"]) == 3
        assert len(config["convergence"]["output_sections"]) == 7
