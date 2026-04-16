"""
Tests for src.data.chat_tools (Session 2)

Validates:
    - Fast-path tools: valid filters → expected result shape
    - Session filter merging: tool args override session defaults
    - read_thread: scope validation, PII redaction, 8K truncation
    - read_threads_batch: 5-ticket / 20K caps
    - query_report: structured JSON sections
    - run_report: propose-only, confirm deferred
    - dispatch_tool: unknown tool → error
    - Tool prompt validation: names match registry, examples parse
    - Legacy tool backward compat
"""

import json
import re
import sqlite3
import pytest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MIG_005 = (_PROJECT_ROOT / "migrations" / "005_persistence_layer.sql").read_text(encoding="utf-8")
_MIG_006 = (_PROJECT_ROOT / "migrations" / "006_hybrid_chat.sql").read_text(encoding="utf-8")

# Base DDL: tables created by db_manager.initialize() (not in migrations)
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
    """Seed test data."""
    # ticket_index
    for i, (trc, friction, sentiment, anomaly) in enumerate([
        ("Billing", "incorrect_charge", "negative", "critical"),
        ("Billing", "duplicate_billing", "negative", None),
        ("Claims", "slow_processing", "neutral", None),
        ("Claims", "denied_claim", "negative", "warning"),
        ("Tech", "login_failure", "negative", "critical"),
    ]):
        tid = f"T-{i+1}"
        conn.execute(
            "INSERT INTO ticket_index "
            "(ticket_id, first_seen_scan_id, last_seen_scan_id, "
            "first_seen_date, ticket_created_date, trc_code, "
            "friction_type, sub_pattern, sentiment_polarity, "
            "anomaly_flag, anomaly_reason, entities_json, dataset_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tid, "s1", "s1", "2026-01-01",
             f"2026-01-{10+i:02d}", trc, friction, friction,
             sentiment, anomaly, f"reason_{i}" if anomaly else None,
             json.dumps({"payer": "BlueCross"}), 1),
        )

    # conversations
    for i in range(5):
        tid = f"T-{i+1}"
        email = f"user{i}@example.com"
        phone = "555-123-4567"
        ssn = "123-45-6789"
        thread = f"Thread for {tid}. Contact: {email}, phone {phone}, SSN {ssn}. Issue about billing."
        conn.execute(
            "INSERT INTO conversations "
            "(ticket_id, subject, trc_code, created_at, full_thread, "
            "thread_preview, message_count, dataset_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (tid, f"Subject {i+1}",
             ["Billing", "Billing", "Claims", "Claims", "Tech"][i],
             f"2026-01-{10+i:02d}", thread, f"Preview {i+1}", 5, 1),
        )

    # nlp_scan_runs + findings
    conn.execute(
        "INSERT INTO nlp_scan_runs (scan_id, started_at, model) "
        "VALUES (?, ?, ?)",
        ("scan1", "2026-01-01", "gemini-2.5-flash"),
    )
    conn.execute(
        "INSERT INTO nlp_findings "
        "(finding_id, scan_id, finding_type, title, ticket_count, "
        "impact_score, dominant_friction_type, top_trcs, "
        "top_sub_patterns, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("f1", "scan1", "within_trc", "Billing errors rising", 42,
         0.85, "incorrect_charge", '["Billing"]',
         '["incorrect_charge"]', "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO nlp_findings "
        "(finding_id, scan_id, finding_type, title, ticket_count, "
        "impact_score, dominant_friction_type, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("f2", "scan1", "cross_trc", "Cross-TRC friction", 10,
         0.4, "slow_processing", "2026-01-01"),
    )

    # analysis_runs (for report tools)
    conn.execute(
        "INSERT INTO analysis_runs "
        "(run_id, run_date, prompt_template, trc_filter, date_start, "
        "date_end, ticket_count, output_text, output_structured) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("rpt1", "2026-01-15", "general_trend", "Billing",
         "2026-01-01", "2026-01-31", 100,
         "Report text here...",
         json.dumps({"findings": [{"title": "test"}],
                     "summary_stats": {"total": 100},
                     "recommendations": ["Fix billing"]})),
    )

    # enriched_trends
    conn.execute(
        "INSERT INTO enriched_trends "
        "(scan_id, dimension, dimension_value, period, ticket_count, "
        "pct_of_total, velocity) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("scan1", "friction_type", "incorrect_charge", "2026-01-06",
         15, 0.35, 0.1),
    )

    conn.commit()


# ═══════════════════════════════════════
#  REGISTRY + DISPATCH
# ═══════════════════════════════════════

class TestRegistry:

    def test_registry_has_all_new_tools(self):
        from src.data.chat_tools.registry import get_tool_registry
        reg = get_tool_registry()
        expected = {
            "query_ticket_classifications", "list_tickets",
            "query_findings", "query_stats",
            "read_thread", "read_threads_batch",
            "query_report", "run_report",
        }
        assert expected.issubset(set(reg.keys()))

    def test_registry_has_legacy_tools(self):
        from src.data.chat_tools.registry import get_tool_registry
        reg = get_tool_registry()
        legacy = {
            "query_tickets", "ticket_detail", "query_trends",
            "query_anomalies", "compare_periods",
            "query_insights", "search_conversations",
        }
        assert legacy.issubset(set(reg.keys()))

    def test_dispatch_unknown_tool(self, db):
        from src.data.chat_tools.registry import dispatch_tool
        result = json.loads(dispatch_tool("nonexistent", {}, db))
        assert "error" in result
        assert "Unknown tool" in result["error"]


# ═══════════════════════════════════════
#  FAST-PATH TOOLS
# ═══════════════════════════════════════

class TestFastPath:

    def test_query_classifications_by_trc(self, db):
        from src.data.chat_tools.fast_path import handle_query_classifications
        result = handle_query_classifications(db, {"group_by": "trc_code"}, {})
        assert result["total"] == 5
        assert len(result["groups"]) == 3
        assert result["group_by"] == "trc_code"

    def test_query_classifications_invalid_group(self, db):
        from src.data.chat_tools.fast_path import handle_query_classifications
        result = handle_query_classifications(db, {"group_by": "bogus"}, {})
        assert "error" in result

    def test_query_classifications_with_session_filter(self, db):
        from src.data.chat_tools.fast_path import handle_query_classifications
        result = handle_query_classifications(
            db, {"group_by": "friction_type"},
            {"trc_codes": ["Billing"]},
        )
        assert result["total"] == 2

    def test_list_tickets_default(self, db):
        from src.data.chat_tools.fast_path import handle_list_tickets
        result = handle_list_tickets(db, {}, {})
        assert result["count"] == 5
        assert len(result["tickets"]) == 5

    def test_list_tickets_with_limit(self, db):
        from src.data.chat_tools.fast_path import handle_list_tickets
        result = handle_list_tickets(db, {"limit": 2}, {})
        assert result["count"] == 2

    def test_list_tickets_session_filter_applied(self, db):
        from src.data.chat_tools.fast_path import handle_list_tickets
        result = handle_list_tickets(
            db, {}, {"trc_codes": ["Claims"]},
        )
        assert result["count"] == 2
        assert all("Claims" in str(t) for t in result["tickets"])

    def test_query_findings_default(self, db):
        from src.data.chat_tools.fast_path import handle_query_findings
        result = handle_query_findings(db, {}, {})
        assert result["count"] == 2

    def test_query_findings_by_type(self, db):
        from src.data.chat_tools.fast_path import handle_query_findings
        result = handle_query_findings(db, {"finding_type": "cross_trc"}, {})
        assert result["count"] == 1

    def test_query_findings_min_impact(self, db):
        from src.data.chat_tools.fast_path import handle_query_findings
        result = handle_query_findings(db, {"min_impact": 0.5}, {})
        assert result["count"] == 1
        assert result["findings"][0].get("finding_id") == "f1"

    def test_query_stats_anomalies(self, db):
        from src.data.chat_tools.fast_path import handle_query_stats
        result = handle_query_stats(db, {"stat_type": "anomalies"}, {})
        assert result["count"] == 3  # 2 critical + 1 warning

    def test_query_stats_trends(self, db):
        from src.data.chat_tools.fast_path import handle_query_stats
        result = handle_query_stats(db, {"stat_type": "trends"}, {})
        assert result["count"] >= 1

    def test_query_stats_invalid_type(self, db):
        from src.data.chat_tools.fast_path import handle_query_stats
        result = handle_query_stats(db, {"stat_type": "bogus"}, {})
        assert "error" in result

    def test_session_filter_overrides(self, db):
        """Tool args.filters override session_filters."""
        from src.data.chat_tools.registry import dispatch_tool
        result = json.loads(dispatch_tool(
            "list_tickets",
            {"filters": {"trc_codes": ["Tech"]}},
            db,
            session_filters={"trc_codes": ["Billing"]},
        ))
        # Tech filter from args should override Billing from session
        assert result["count"] == 1


# ═══════════════════════════════════════
#  THREAD TOOLS
# ═══════════════════════════════════════

class TestThreadTools:

    def test_read_thread_basic(self, db):
        from src.data.chat_tools.thread_tools import handle_read_thread
        result = handle_read_thread(db, {"ticket_id": "T-1"}, {})
        assert result["ticket_id"] == "T-1"
        assert "thread" in result

    def test_read_thread_missing_id(self, db):
        from src.data.chat_tools.thread_tools import handle_read_thread
        result = handle_read_thread(db, {}, {})
        assert "error" in result

    def test_read_thread_not_found(self, db):
        from src.data.chat_tools.thread_tools import handle_read_thread
        result = handle_read_thread(db, {"ticket_id": "NONEXISTENT"}, {})
        assert "error" in result

    def test_read_thread_pii_redacted(self, db):
        from src.data.chat_tools.thread_tools import handle_read_thread
        result = handle_read_thread(db, {"ticket_id": "T-1"}, {})
        thread = result["thread"]
        assert "[EMAIL-REDACTED]" in thread
        assert "[PHONE-REDACTED]" in thread
        assert "[SSN-REDACTED]" in thread
        assert "user0@example.com" not in thread

    def test_read_thread_scope_validation(self, db):
        """Ticket outside session scope returns error."""
        from src.data.chat_tools.thread_tools import handle_read_thread
        result = handle_read_thread(
            db, {"ticket_id": "T-1"},
            {"trc_codes": ["Claims"]},  # T-1 is Billing, not Claims
        )
        assert "error" in result
        assert "not in current session scope" in result["error"]

    def test_read_threads_batch_basic(self, db):
        from src.data.chat_tools.thread_tools import handle_read_threads_batch
        result = handle_read_threads_batch(
            db, {"ticket_ids": ["T-1", "T-2"]}, {},
        )
        assert result["count"] == 2

    def test_read_threads_batch_over_limit(self, db):
        from src.data.chat_tools.thread_tools import handle_read_threads_batch
        result = handle_read_threads_batch(
            db, {"ticket_ids": ["T-1", "T-2", "T-3", "T-4", "T-5", "T-6"]}, {},
        )
        assert "error" in result
        assert "Max 5" in result["error"]

    def test_read_threads_batch_empty(self, db):
        from src.data.chat_tools.thread_tools import handle_read_threads_batch
        result = handle_read_threads_batch(db, {"ticket_ids": []}, {})
        assert "error" in result


# ═══════════════════════════════════════
#  REPORT TOOLS
# ═══════════════════════════════════════

class TestReportTools:

    def test_query_report_latest(self, db):
        from src.data.chat_tools.report_tools import handle_query_report
        result = handle_query_report(db, {}, {})
        assert result["report_id"] == "rpt1"
        assert "structured" in result

    def test_query_report_by_section(self, db):
        from src.data.chat_tools.report_tools import handle_query_report
        result = handle_query_report(db, {"section": "findings"}, {})
        assert result["section"] == "findings"
        assert "data" in result

    def test_query_report_invalid_section(self, db):
        from src.data.chat_tools.report_tools import handle_query_report
        result = handle_query_report(db, {"section": "bogus"}, {})
        assert "error" in result

    def test_query_report_not_found(self, db):
        from src.data.chat_tools.report_tools import handle_query_report
        result = handle_query_report(db, {"report_id": "nonexistent"}, {})
        assert "error" in result

    def test_run_report_propose_only(self, db):
        from src.data.chat_tools.report_tools import handle_run_report
        result = handle_run_report(db, {"template": "general_trend"}, {})
        assert result["action"] == "confirm_required"
        assert result["template"] == "general_trend"

    def test_run_report_with_confirm_submits(self, db):
        """confirm=True submits to executor (no queue → no_queue status)."""
        from src.data.chat_tools.report_tools import handle_run_report
        result = handle_run_report(
            db, {"template": "general_trend", "confirm": True}, {},
        )
        assert result["status"] == "no_queue"
        assert "report_id" in result

    def test_run_report_invalid_template(self, db):
        from src.data.chat_tools.report_tools import handle_run_report
        result = handle_run_report(db, {"template": "bogus"}, {})
        assert "error" in result

    def test_run_report_merges_session_filters(self, db):
        from src.data.chat_tools.report_tools import handle_run_report
        result = handle_run_report(
            db, {"template": "general_trend"},
            {"trc_codes": ["Billing"]},
        )
        assert result["proposed_params"]["filters"]["trc_codes"] == ["Billing"]


# ═══════════════════════════════════════
#  TOOL PROMPT VALIDATION
# ═══════════════════════════════════════

class TestToolPrompts:

    # Use greedy match that stops at end of line to handle nested braces
    _TOOL_CALL_RE = re.compile(r"TOOL_CALL:\s*(\w+)\s+(\{[^\n]+\})")

    def test_all_new_tools_in_prompt(self):
        from src.data.chat_tools.tool_prompts import TOOL_PROMPT_ADDENDUM, NEW_TOOL_NAMES
        for name in NEW_TOOL_NAMES:
            assert name in TOOL_PROMPT_ADDENDUM, f"Tool {name} missing from prompt"

    def test_all_legacy_tools_in_prompt(self):
        from src.data.chat_tools.tool_prompts import TOOL_PROMPT_ADDENDUM, LEGACY_TOOL_NAMES
        for name in LEGACY_TOOL_NAMES:
            assert name in TOOL_PROMPT_ADDENDUM, f"Legacy tool {name} missing from prompt"

    def test_all_examples_match_regex(self):
        from src.data.chat_tools.tool_prompts import TOOL_PROMPT_ADDENDUM
        examples = re.findall(r"Example: (TOOL_CALL: \w+ \{.*?\})", TOOL_PROMPT_ADDENDUM)
        assert len(examples) >= 9, f"Expected >= 9 examples, got {len(examples)}"
        for ex in examples:
            match = self._TOOL_CALL_RE.search(ex)
            assert match is not None, f"Example doesn't match regex: {ex}"

    def test_all_example_args_parse_as_json(self):
        from src.data.chat_tools.tool_prompts import TOOL_PROMPT_ADDENDUM
        for match in self._TOOL_CALL_RE.finditer(TOOL_PROMPT_ADDENDUM):
            args_str = match.group(2)
            try:
                json.loads(args_str)
            except json.JSONDecodeError:
                pytest.fail(f"Invalid JSON in example: {match.group(0)}")

    def test_prompt_tool_names_match_registry(self):
        from src.data.chat_tools.tool_prompts import TOOL_PROMPT_ADDENDUM, ALL_TOOL_NAMES
        from src.data.chat_tools.registry import get_tool_registry
        registry = get_tool_registry()
        # Every example tool name should be in registry
        for match in self._TOOL_CALL_RE.finditer(TOOL_PROMPT_ADDENDUM):
            tool_name = match.group(1)
            assert tool_name in registry, f"Example tool {tool_name} not in registry"


# ═══════════════════════════════════════
#  CHAT SESSION (Migration 006 properties)
# ═══════════════════════════════════════

class TestChatSessionExtensions:

    def _make_session_db(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(_MIG_005)
        conn.executescript(_MIG_006)
        return conn

    def test_set_and_get_session_filters(self):
        from src.services.chat_session import (
            create_session, get_session_filters, set_session_filters,
        )
        conn = self._make_session_db()
        sid = create_session("test_page", conn=conn)
        set_session_filters(sid, {"trc_codes": ["Billing"]}, conn=conn)
        filters = get_session_filters(sid, conn=conn)
        assert filters == {"trc_codes": ["Billing"]}
        conn.close()

    def test_set_session_ticket_count(self):
        from src.services.chat_session import (
            create_session, set_session_ticket_count, load_session,
        )
        conn = self._make_session_db()
        sid = create_session("test_page", conn=conn)
        set_session_ticket_count(sid, 42, conn=conn)
        session = load_session(sid, conn=conn)
        assert session["ticket_count"] == 42
        conn.close()

    def test_active_report_ids(self):
        from src.services.chat_session import (
            create_session, add_active_report_id, get_active_report_ids,
        )
        conn = self._make_session_db()
        sid = create_session("test_page", conn=conn)
        add_active_report_id(sid, "rpt_001", conn=conn)
        add_active_report_id(sid, "rpt_002", conn=conn)
        ids = get_active_report_ids(sid, conn=conn)
        assert ids == ["rpt_001", "rpt_002"]
        conn.close()

    def test_load_session_includes_new_fields(self):
        from src.services.chat_session import (
            create_session, set_session_filters, load_session,
        )
        conn = self._make_session_db()
        sid = create_session("test_page", conn=conn)
        set_session_filters(sid, {"sentiment": "negative"}, conn=conn)
        session = load_session(sid, conn=conn)
        assert session["filter_json"] == {"sentiment": "negative"}
        assert session["active_report_ids"] == []
        conn.close()
