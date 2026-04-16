"""
Session 6B Tests: Source Selector Wiring
=========================================
Verifies that source_id flows from UI pages through workers to engines,
and that db_manager query methods accept and forward source_id.

Run: python -m pytest tests/test_s6b_selector_wiring.py -x -v
"""

import sqlite3
import pytest
from pathlib import Path


# ─── Helpers ───

def _setup_multi_source_db(tmp_path, name="wiring.db"):
    """Create a DB with 2 sources and data in per-source tables."""
    from src.data.db_manager import DatabaseManager
    from src.data.schema_builder import create_source_tables

    db_path = tmp_path / name
    db = DatabaseManager(db_path)
    db.initialize()

    # Create a second source
    create_source_tables(db.conn, "kodif_chat")
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    db.conn.execute(
        "INSERT INTO source_registry (source_id, source_name, source_type, table_prefix, is_default, created_at) "
        "VALUES ('kodif_chat', 'Kodif Chat', 'kodif', 'kodif_chat', 0, ?)",
        (now,)
    )
    db.conn.commit()

    # Insert Zendesk data (zendesk_default per-source tables exist from migration 012)
    for i in range(5):
        db.upsert_ticket({
            "ticket_id": f"ZEN-{i}", "subject": f"Zendesk ticket {i}",
            "trc_code": "AUTH-01", "trc_label": "AUTH-01", "status": "open",
            "priority": "", "channel": "", "csat_score": 4.0,
            "created_at": f"2026-01-{15+i:02d}", "updated_at": "", "solved_at": "",
            "requester_name": "", "requester_email": "", "assignee_name": "",
            "group_name": "", "tags": [], "custom_fields": {},
            "assignment_to_resolution_hours": None,
            "total_resolution_hours": None, "first_reply_hours": None,
        }, table_prefix="zendesk_default")
        db.upsert_conversation({
            "ticket_id": f"ZEN-{i}", "subject": f"Zendesk ticket {i}",
            "trc_code": "AUTH-01", "trc_label": "AUTH-01", "status": "open",
            "csat_score": 4.0, "created_at": f"2026-01-{15+i:02d}", "solved_at": "",
            "message_count": 2, "client_messages": 1, "agent_messages": 1,
            "full_thread": f"Zendesk thread {i}", "thread_preview": f"Zen preview {i}",
            "dataset_id": 0,
        }, table_prefix="zendesk_default")

    # Insert Kodif data
    for i in range(3):
        db.upsert_ticket({
            "ticket_id": f"KOD-{i}", "subject": f"Kodif ticket {i}",
            "trc_code": "BIL-03", "trc_label": "BIL-03", "status": "open",
            "priority": "", "channel": "", "csat_score": 3.0,
            "created_at": f"2026-02-{10+i:02d}", "updated_at": "", "solved_at": "",
            "requester_name": "", "requester_email": "", "assignee_name": "",
            "group_name": "", "tags": [], "custom_fields": {},
            "assignment_to_resolution_hours": None,
            "total_resolution_hours": None, "first_reply_hours": None,
        }, table_prefix="kodif_chat")
        db.upsert_conversation({
            "ticket_id": f"KOD-{i}", "subject": f"Kodif ticket {i}",
            "trc_code": "BIL-03", "trc_label": "BIL-03", "status": "open",
            "csat_score": 3.0, "created_at": f"2026-02-{10+i:02d}", "solved_at": "",
            "message_count": 1, "client_messages": 1, "agent_messages": 0,
            "full_thread": f"Kodif thread {i}", "thread_preview": f"Kod preview {i}",
            "dataset_id": 0,
        }, table_prefix="kodif_chat")

    db.commit()
    db.rebuild_source_fts("zendesk_default")
    db.rebuild_source_fts("kodif_chat")
    return db


# ─── Tests: Worker Constructors ───

class TestWorkerConstructors:

    def test_incident_worker_stores_source_id(self):
        from src.ui.pages.incidents_page import IncidentWorker
        w = IncidentWorker("/tmp/test.db", "2026-01-20", "2026-01-01", source_id="zen")
        assert w.source_id == "zen"

    def test_incident_worker_default_none(self):
        from src.ui.pages.incidents_page import IncidentWorker
        w = IncidentWorker("/tmp/test.db", "2026-01-20")
        assert w.source_id is None

    def test_trending_worker_stores_source_id(self):
        from src.ui.pages.trending_topics import TrendingWorker
        w = TrendingWorker("/tmp/test.db", "2026-01-01", "2026-01-31", None, "Daily", source_id="kod")
        assert w.source_id == "kod"

    def test_pipeline_worker_stores_source_id(self):
        from src.ui.pages.ai_reports import PipelineWorker
        w = PipelineWorker("/tmp/test.db", {}, "2026-01-01", "2026-01-31", "", None, source_id="zen")
        assert w.source_id == "zen"


# ─── Tests: DB Manager Source Filtering ───

class TestDbManagerSourceFilter:

    @pytest.fixture
    def db(self, tmp_path):
        db = _setup_multi_source_db(tmp_path)
        yield db
        db.close()

    def test_get_trc_codes_all_sources(self, db):
        """No source_id → TRCs from all sources."""
        codes = db.get_trc_codes()
        code_set = {c["code"] for c in codes}
        assert "AUTH-01" in code_set
        assert "BIL-03" in code_set

    def test_get_trc_codes_filtered_zendesk(self, db):
        """source_id=zendesk_default → only Zendesk TRCs."""
        codes = db.get_trc_codes(source_id="zendesk_default")
        code_set = {c["code"] for c in codes}
        assert "AUTH-01" in code_set
        assert "BIL-03" not in code_set

    def test_get_trc_codes_filtered_kodif(self, db):
        """source_id=kodif_chat → only Kodif TRCs."""
        codes = db.get_trc_codes(source_id="kodif_chat")
        code_set = {c["code"] for c in codes}
        assert "BIL-03" in code_set
        assert "AUTH-01" not in code_set

    def test_get_date_range_all_sources(self, db):
        """No source_id → date range spans both sources."""
        min_d, max_d = db.get_date_range()
        assert min_d is not None
        assert min_d.startswith("2026-01")  # Zendesk starts Jan
        assert max_d.startswith("2026-02")  # Kodif ends Feb

    def test_get_date_range_filtered(self, db):
        """source_id=kodif_chat → date range only from Kodif."""
        min_d, max_d = db.get_date_range(source_id="kodif_chat")
        assert min_d is not None
        assert min_d.startswith("2026-02")
        assert max_d.startswith("2026-02")


# ─── Tests: Engine Entry Points Accept source_id ───

class TestEngineSourceId:

    def test_run_incident_scan_accepts_source_id(self):
        """run_incident_scan signature accepts source_id kwarg."""
        import inspect
        from src.data.incident_engine import run_incident_scan
        sig = inspect.signature(run_incident_scan)
        assert "source_id" in sig.parameters

    def test_run_full_analysis_accepts_source_id(self):
        """run_full_analysis signature accepts source_id kwarg."""
        import inspect
        from src.data.trending_engine import run_full_analysis
        sig = inspect.signature(run_full_analysis)
        assert "source_id" in sig.parameters

    def test_ai_pipeline_run_accepts_source_id(self):
        """AIReportPipeline.run signature accepts source_id kwarg."""
        import inspect
        from src.data.ai_report_pipeline import AIReportPipeline
        sig = inspect.signature(AIReportPipeline.run)
        assert "source_id" in sig.parameters

    def test_build_data_block_accepts_source_id(self):
        """build_data_block signature accepts source_id kwarg."""
        import inspect
        from src.data.report_builder import build_data_block
        sig = inspect.signature(build_data_block)
        assert "source_id" in sig.parameters
