"""
Integration tests for the Hybrid Chat + DB Sprint (Sessions 1-5)

End-to-end tests that exercise the full stack:
    - Migration 006 → filter engine → tool dispatch → result
    - Session filter merging through full stack
    - read_thread with PII redaction through full stack
    - run_report propose → confirm flow
    - Report builder structured output backward compat
    - Tool prompt round-trip: every example parses and dispatches
    - Embedding compat shims route correctly
"""

import json
import re
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MIG_005 = (_PROJECT_ROOT / "migrations" / "005_persistence_layer.sql").read_text(encoding="utf-8")
_MIG_006 = (_PROJECT_ROOT / "migrations" / "006_hybrid_chat.sql").read_text(encoding="utf-8")

_BASE_DDL = """
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id TEXT PRIMARY KEY, subject TEXT, trc_code TEXT,
    trc_label TEXT, status TEXT, priority TEXT, channel TEXT,
    csat_score REAL, created_at TEXT, updated_at TEXT,
    solved_at TEXT, requester_name TEXT, requester_email TEXT,
    assignee_name TEXT, group_name TEXT, tags TEXT,
    custom_fields TEXT, assignment_to_resolution_hours REAL,
    total_resolution_hours REAL, first_reply_hours REAL,
    requester_hash TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS conversations (
    ticket_id TEXT PRIMARY KEY, subject TEXT, trc_code TEXT,
    trc_label TEXT, status TEXT, csat_score REAL, created_at TEXT,
    solved_at TEXT, message_count INTEGER, client_messages INTEGER,
    agent_messages INTEGER, full_thread TEXT, thread_preview TEXT,
    dataset_id INTEGER DEFAULT 0,
    FOREIGN KEY (ticket_id) REFERENCES tickets(ticket_id)
);
CREATE VIRTUAL TABLE IF NOT EXISTS conversations_fts USING fts5(
    ticket_id, subject, trc_label, full_thread,
    content=conversations, content_rowid=rowid
);
CREATE TABLE IF NOT EXISTS nlp_scan_runs (
    scan_id TEXT PRIMARY KEY, started_at TEXT, completed_at TEXT,
    status TEXT, model TEXT, ticket_count INTEGER, batch_count INTEGER,
    config_snapshot TEXT
);
CREATE TABLE IF NOT EXISTS nlp_findings (
    finding_id TEXT PRIMARY KEY, scan_id TEXT NOT NULL,
    finding_type TEXT NOT NULL, scope TEXT, title TEXT NOT NULL,
    description TEXT, ticket_count INTEGER, pct_of_scanned REAL,
    avg_sentiment_intensity REAL, dominant_friction_type TEXT,
    top_trcs TEXT, top_sub_patterns TEXT, top_entities TEXT,
    date_concentration TEXT, temporal_trend TEXT,
    exemplar_ticket_ids TEXT, statistical_validation TEXT,
    baseline_comparison TEXT, impact_score REAL, created_at TEXT NOT NULL,
    FOREIGN KEY (scan_id) REFERENCES nlp_scan_runs(scan_id)
);
CREATE TABLE IF NOT EXISTS trc_baselines (
    trc_code TEXT PRIMARY KEY, baseline_value REAL, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS incident_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT, trc_code TEXT,
    flag_type TEXT, severity TEXT, created_at TEXT
);
"""


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_BASE_DDL)
    conn.executescript(_MIG_005)
    conn.executescript(_MIG_006)
    _seed(conn)
    yield conn
    conn.close()


def _seed(conn):
    for i, (trc, friction, sent) in enumerate([
        ("Billing", "incorrect_charge", "negative"),
        ("Billing", "duplicate_billing", "neutral"),
        ("Claims", "slow_processing", "negative"),
        ("Claims", "denied_claim", "positive"),
        ("Tech", "login_failure", "negative"),
    ]):
        tid = f"T-{i+1}"
        conn.execute(
            "INSERT INTO ticket_index "
            "(ticket_id, first_seen_scan_id, last_seen_scan_id, "
            "first_seen_date, ticket_created_date, trc_code, "
            "friction_type, sub_pattern, sentiment_polarity, "
            "entities_json, dataset_id, issue_snippet) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tid, "s1", "s1", "2026-01-01", f"2026-01-{10+i:02d}",
             trc, friction, friction, sent,
             json.dumps({"payer": "BlueCross"}), 1,
             f"Issue snippet {i}"),
        )
        email = f"user{i}@test.com"
        thread = f"Thread for {tid}. Email: {email}. SSN: 123-45-6789."
        conn.execute(
            "INSERT INTO conversations "
            "(ticket_id, subject, trc_code, created_at, full_thread, "
            "thread_preview, message_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tid, f"Subject {i}", trc, f"2026-01-{10+i:02d}",
             thread, f"Preview {i}", 3),
        )

    conn.execute(
        "INSERT INTO analysis_runs "
        "(run_id, run_date, prompt_template, output_text, output_structured) "
        "VALUES (?, ?, ?, ?, ?)",
        ("rpt1", "2026-01-15", "general_trend", "Report text",
         json.dumps({"findings": [{"title": "test"}],
                     "summary_stats": {"total": 100},
                     "recommendations": ["Fix it"]})),
    )
    conn.commit()


# ═══════════════════════════════════════
#  E2E: Session → Filters → Tool → Result
# ═══════════════════════════════════════

class TestE2ESessionToTool:

    def test_create_session_set_filters_dispatch_tool(self, db):
        """Full chain: create session → set filters → dispatch → verify."""
        from src.services.chat_session import create_session, set_session_filters
        from src.data.chat_tools.registry import dispatch_tool

        sid = create_session("test_page", conn=db)
        set_session_filters(sid, {"trc_codes": ["Billing"]}, conn=db)

        result = json.loads(dispatch_tool(
            "list_tickets", {}, db,
            session_filters={"trc_codes": ["Billing"]},
        ))
        assert result["count"] == 2
        for t in result["tickets"]:
            assert t["trc_code"] == "Billing"

    def test_session_filter_merge_override(self, db):
        """Tool args override session filters."""
        from src.data.chat_tools.registry import dispatch_tool

        result = json.loads(dispatch_tool(
            "query_ticket_classifications",
            {"group_by": "trc_code", "filters": {"trc_codes": ["Tech"]}},
            db,
            session_filters={"trc_codes": ["Billing"]},
        ))
        assert result["total"] == 1
        assert result["groups"][0]["value"] == "Tech"

    def test_full_chain_migration_to_filter_to_dispatch(self, db):
        """Migration 006 tables → filter engine → tool → result."""
        # Seed theme tags (migration 006 table)
        db.execute(
            "INSERT INTO ticket_theme_tags "
            "(ticket_id, theme_id, scan_id, tagged_at) VALUES (?, ?, ?, ?)",
            ("T-1", "theme_billing", "s1", "2026-01-01"),
        )
        db.commit()

        from src.data.chat_tools.registry import dispatch_tool
        result = json.loads(dispatch_tool(
            "list_tickets", {},
            db, session_filters={"theme_ids": ["theme_billing"]},
        ))
        assert result["count"] == 1
        assert result["tickets"][0]["ticket_id"] == "T-1"


# ═══════════════════════════════════════
#  E2E: Thread with PII Redaction
# ═══════════════════════════════════════

class TestE2EThreadRedaction:

    def test_read_thread_pii_redacted_e2e(self, db):
        from src.data.chat_tools.registry import dispatch_tool
        result = json.loads(dispatch_tool("read_thread", {"ticket_id": "T-1"}, db))
        assert "[EMAIL-REDACTED]" in result["thread"]
        assert "[SSN-REDACTED]" in result["thread"]
        assert "user0@test.com" not in result["thread"]


# ═══════════════════════════════════════
#  E2E: Report Propose → Confirm Flow
# ═══════════════════════════════════════

class TestE2EReportFlow:

    def test_propose_then_confirm(self, db):
        from src.data.chat_tools.registry import dispatch_tool

        # Step 1: Propose
        result = json.loads(dispatch_tool(
            "run_report", {"template": "general_trend"}, db,
        ))
        assert result["action"] == "confirm_required"

        # Step 2: Confirm (no job queue → no_queue response)
        result = json.loads(dispatch_tool(
            "run_report", {"template": "general_trend", "confirm": True}, db,
        ))
        assert result["status"] == "no_queue"
        assert "report_id" in result

    def test_query_report_retrieval(self, db):
        from src.data.chat_tools.registry import dispatch_tool
        result = json.loads(dispatch_tool(
            "query_report", {"section": "findings"}, db,
        ))
        assert result["report_id"] == "rpt1"
        assert result["section"] == "findings"


# ═══════════════════════════════════════
#  TOOL PROMPT ROUND-TRIP
# ═══════════════════════════════════════

class TestToolPromptRoundTrip:

    _TOOL_CALL_RE = re.compile(r"TOOL_CALL:\s*(\w+)\s+(\{[^\n]+\})")

    def test_every_example_dispatches_without_crash(self, db):
        """Every TOOL_CALL example in the prompt should dispatch without error."""
        from src.data.chat_tools.tool_prompts import TOOL_PROMPT_ADDENDUM
        from src.data.chat_tools.registry import dispatch_tool

        for match in self._TOOL_CALL_RE.finditer(TOOL_PROMPT_ADDENDUM):
            tool_name = match.group(1)
            args = json.loads(match.group(2))
            result = json.loads(dispatch_tool(tool_name, args, db))
            # Should not crash — may return error for missing data but not exception
            assert isinstance(result, dict), f"Tool {tool_name} returned non-dict"


# ═══════════════════════════════════════
#  REPORT BUILDER BACKWARD COMPAT
# ═══════════════════════════════════════

class TestReportBuilderCompat:

    def test_structured_json_in_data_block(self):
        """build_data_block still returns all original keys + structured_json."""
        from src.data.report_builder import build_structured_output
        block = {
            "ticket_count": 50,
            "trc_distribution": [{"trc": "Billing", "count": 50, "pct": 1.0}],
            "csat_summary": {"average": 4.0},
            "resolution_times": {},
            "incident_flags": [],
            "product_gap_flags": [],
        }
        raw = build_structured_output(block)
        structured = json.loads(raw)
        # New structured output works
        assert "findings" in structured
        assert "summary_stats" in structured
        # Original block not mutated
        assert "trc_distribution" in block
        assert "csat_summary" in block


# ═══════════════════════════════════════
#  EMBEDDING COMPAT
# ═══════════════════════════════════════

class TestEmbeddingCompat:

    def test_compat_verify_bundled_routes(self):
        with patch("src.data.embedding.model_loader.is_available", return_value=True):
            from src.data.embedding.compat import verify_model_bundled
            assert verify_model_bundled() is True

    def test_compat_is_available_routes(self):
        with patch("src.data.embedding.model_loader.is_available", return_value=False):
            from src.data.embedding.compat import is_available
            assert is_available() is False


# ═══════════════════════════════════════
#  REPORT EXECUTOR
# ═══════════════════════════════════════

class TestReportExecutor:

    def test_submit_without_queue(self, db):
        from src.data.chat_tools.report_executor import submit_report_job
        result = submit_report_job(db, {}, "general_trend")
        assert result["status"] == "no_queue"
        assert "report_id" in result

    def test_submit_with_mock_queue(self, db):
        from src.data.chat_tools.report_executor import submit_report_job
        mock_queue = MagicMock()
        result = submit_report_job(
            db, {"trc_codes": ["Billing"]}, "general_trend",
            session_id=None, job_queue=mock_queue,
        )
        assert result["status"] == "queued"
        assert "report_id" in result
        mock_queue.submit.assert_called_once()

    def test_submit_failure_returns_error(self, db):
        from src.data.chat_tools.report_executor import submit_report_job
        mock_queue = MagicMock()
        mock_queue.submit.side_effect = RuntimeError("Queue full")
        result = submit_report_job(
            db, {}, "general_trend", job_queue=mock_queue,
        )
        assert "error" in result
