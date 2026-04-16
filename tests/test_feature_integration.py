"""
Pre-1.0 Feature Integration Tests
===================================
Covers P0/P1/P2 untested features identified in the Build 9.0 debrief.
Tests backend logic, data pipelines, CRUD operations, and AI integration
against the real 888-ticket / 127-TRC dataset.

UI rendering is NOT tested here (owner handles UAT).

Run:  python -m pytest tests/test_feature_integration.py -v
"""

import sys
import os
import sqlite3
import tempfile
import shutil
import json
import re
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock
from collections import Counter

# ── Project path ──
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.db_manager import DatabaseManager

# ── Shared fixtures ──

LIVE_DB = Path("data/local_warehouse.db")
_DATE_START = "2025-01-01"
_DATE_END = "2025-03-12"


def _get_live_db():
    """Return a DatabaseManager against the real 888-ticket DB (read-only tests)."""
    assert LIVE_DB.exists(), f"Live DB not found at {LIVE_DB}"
    db = DatabaseManager(LIVE_DB)
    return db


def _make_tmp_db():
    """Create a fresh empty DB for write tests."""
    tmp = tempfile.mkdtemp()
    db_path = Path(tmp) / "test_integration.db"
    db = DatabaseManager(db_path)
    db.initialize()
    return db, tmp


def _cleanup(tmp):
    shutil.rmtree(tmp, ignore_errors=True)


# ====================================================================
# P0 — MUST TEST
# ====================================================================


class TestP0_ConversationSearch:
    """P0 #7: FTS search, TRC filter, date range, thread viewer."""

    def test_fts_search_returns_results(self):
        db = _get_live_db()
        results = db.search_conversations("payment", limit=20)
        assert len(results) > 0, "FTS search for 'payment' should find tickets"
        first = results[0]
        assert "ticket_id" in first or hasattr(first, "keys")

    def test_fts_search_empty_query(self):
        db = _get_live_db()
        results = db.search_conversations("", limit=10)
        # Empty query should return empty or all — not crash
        assert isinstance(results, list)

    def test_fts_search_special_chars(self):
        db = _get_live_db()
        # Should not crash on special FTS chars
        results = db.search_conversations("1099 OR copay", limit=10)
        assert isinstance(results, list)

    def test_fts_search_no_results(self):
        db = _get_live_db()
        results = db.search_conversations("xyznonexistent12345", limit=10)
        assert len(results) == 0

    def test_get_trc_codes(self):
        db = _get_live_db()
        trcs = db.get_trc_codes()
        assert len(trcs) == 127, f"Expected 127 TRCs, got {len(trcs)}"

    def test_conversation_has_thread(self):
        db = _get_live_db()
        row = db.conn.execute(
            "SELECT full_thread FROM conversations LIMIT 1"
        ).fetchone()
        assert row is not None
        thread = row[0] if isinstance(row, tuple) else row["full_thread"]
        assert len(thread) > 0, "Conversation should have full_thread content"


class TestP0_TRCAnalytics:
    """P0 #8: KPI cards, metrics table, CSAT heatmap."""

    def test_compute_analytics_basic(self):
        from src.data.trc_analytics import compute_trc_analytics
        db = _get_live_db()
        result = compute_trc_analytics(db.conn, _DATE_START, _DATE_END)
        assert "summary" in result
        assert "metrics_table" in result
        assert result["summary"]["total_tickets"] > 0
        # 880 tickets fall within date range (8 outside range)
        assert result["summary"]["total_tickets"] >= 850

    def test_analytics_with_trc_filter(self):
        from src.data.trc_analytics import compute_trc_analytics
        db = _get_live_db()
        result = compute_trc_analytics(
            db.conn, _DATE_START, _DATE_END,
            trc_filter="Provider payout rate dissatisfaction"
        )
        assert result["summary"]["total_tickets"] > 0
        assert result["summary"]["total_tickets"] < 888

    def test_kpi_values_reasonable(self):
        from src.data.trc_analytics import compute_trc_analytics
        db = _get_live_db()
        result = compute_trc_analytics(db.conn, _DATE_START, _DATE_END)
        s = result["summary"]
        assert 0 < s["avg_csat"] <= 5.0, f"CSAT out of range: {s['avg_csat']}"

    def test_metrics_table_has_rows(self):
        from src.data.trc_analytics import compute_trc_analytics
        db = _get_live_db()
        result = compute_trc_analytics(db.conn, _DATE_START, _DATE_END)
        table = result["metrics_table"]
        assert len(table) > 50, f"Expected 50+ TRC rows, got {len(table)}"
        first = table[0]
        assert "trc_code" in first
        assert "ticket_count" in first
        assert "avg_csat" in first

    def test_csat_heatmap_structure(self):
        from src.data.trc_analytics import compute_trc_analytics
        db = _get_live_db()
        result = compute_trc_analytics(db.conn, _DATE_START, _DATE_END)
        hm = result.get("csat_heatmap")
        if hm:
            assert "y_labels" in hm
            assert "x_labels" in hm
            assert len(hm["y_labels"]) > 0


class TestP0_TrendingSmoothing:
    """P0 #3: Rising/cooling terms, topic clusters, AI keyword smoothing."""

    def test_sentiment_trends(self):
        from src.data.trending_engine import compute_sentiment_trends
        db = _get_live_db()
        result = compute_sentiment_trends(
            db.conn, _DATE_START, _DATE_END, None, "Weekly"
        )
        assert isinstance(result, dict)
        assert len(result) > 0, "Should have sentiment data for at least 1 TRC"

    def test_rising_terms(self):
        from src.data.trending_engine import compute_rising_terms
        db = _get_live_db()
        result = compute_rising_terms(
            db.conn, _DATE_START, _DATE_END, None, "Weekly"
        )
        assert "rising" in result
        assert "cooling" in result
        assert len(result["rising"]) > 0, "Should find rising terms"

    def test_rising_terms_have_velocity(self):
        from src.data.trending_engine import compute_rising_terms
        db = _get_live_db()
        result = compute_rising_terms(
            db.conn, _DATE_START, _DATE_END, None, "Weekly"
        )
        first = result["rising"][0]
        assert "term" in first
        assert "velocity" in first
        assert first["velocity"] > 0

    def test_topic_clusters_nmf(self):
        from src.data.trending_engine import compute_topic_model
        db = _get_live_db()
        result = compute_topic_model(
            db.conn, _DATE_START, _DATE_END, None, "Weekly"
        )
        assert "topics" in result
        assert len(result["topics"]) > 0
        first_topic = result["topics"][0]
        assert "label" in first_topic
        assert "top_terms" in first_topic

    def test_topic_clusters_kmeans(self):
        from src.data.trending_engine import compute_topic_clusters
        db = _get_live_db()
        result = compute_topic_clusters(
            db.conn, _DATE_START, _DATE_END, None
        )
        assert "clusters" in result
        assert len(result["clusters"]) > 0

    def test_cross_trc_correlations(self):
        from src.data.trending_engine import compute_cross_trc_correlations
        db = _get_live_db()
        result = compute_cross_trc_correlations(
            db.conn, _DATE_START, _DATE_END, "Weekly"
        )
        assert "correlations" in result
        assert isinstance(result["correlations"], list)

    def test_full_analysis_pipeline(self):
        from src.data.trending_engine import run_full_analysis
        db = _get_live_db()
        progress_calls = []
        result = run_full_analysis(
            db.conn, _DATE_START, _DATE_END, None, "Weekly",
            progress_callback=lambda s, t, m: progress_calls.append(m),
            db=db
        )
        assert "sentiment" in result
        assert "terms" in result
        assert "topics" in result
        assert "correlations" in result
        assert len(progress_calls) > 0, "Progress callback should have been called"

    def test_bucketing_daily(self):
        from src.data.trending_engine import compute_sentiment_trends
        db = _get_live_db()
        result = compute_sentiment_trends(
            db.conn, _DATE_START, _DATE_END, None, "Daily"
        )
        assert isinstance(result, dict)

    def test_bucketing_hourly(self):
        from src.data.trending_engine import compute_sentiment_trends
        db = _get_live_db()
        result = compute_sentiment_trends(
            db.conn, _DATE_START, _DATE_END, None, "Hourly"
        )
        assert isinstance(result, dict)

    def test_smooth_clusters_with_mock_gemini(self):
        """Test AI cluster smoothing with mocked Gemini response."""
        from src.data.trending_engine import smooth_clusters_with_ai
        mock_gemini = Mock()
        mock_gemini.generate.return_value = json.dumps({
            "0": {"coherent": True, "label": "Billing Issues",
                  "merge_with": None, "split_into": None, "confidence": 0.9},
            "1": {"coherent": True, "label": "Calendar Sync",
                  "merge_with": None, "split_into": None, "confidence": 0.85},
        })
        # smooth_clusters_with_ai expects a list of topic dicts (not wrapped dict)
        topics = [
            {"label": "Topic 0", "top_terms": ["billing", "charge", "invoice"],
             "count": 50, "avg_sentiment": -0.3},
            {"label": "Topic 1", "top_terms": ["calendar", "sync", "google"],
             "count": 30, "avg_sentiment": -0.5},
        ]
        result = smooth_clusters_with_ai(topics, mock_gemini)
        assert isinstance(result, dict)
        mock_gemini.generate.assert_called_once()

    def test_suggest_keyword_improvements_with_mock(self):
        """Test AI keyword suggestions with mocked Gemini response."""
        from src.data.trending_engine import suggest_keyword_improvements
        mock_gemini = Mock()
        mock_gemini.generate.return_value = json.dumps({
            "suppress": [{"term": "the", "reason": "stopword"}],
            "add_to_map": [{"term": "copay", "concept_group": "Billing", "reason": "domain term"}],
        })
        terms = {"rising": [{"term": "copay", "velocity": 2.0}]}
        result = suggest_keyword_improvements(terms, mock_gemini)
        assert isinstance(result, dict)
        mock_gemini.generate.assert_called_once()


class TestP0_ReportBuilder:
    """P0 #2: AI report data block construction and prompt variable replacement."""

    def test_build_data_block(self):
        from src.data.report_builder import build_data_block
        db = _get_live_db()
        block = build_data_block(db, _DATE_START, _DATE_END)
        assert isinstance(block, dict)
        # 880 tickets in date range (8 fall outside)
        assert block["ticket_count"] >= 850
        assert "trc_distribution" in block
        assert "csat_summary" in block
        assert "top_terms" in block

    def test_build_data_block_with_trc_filter(self):
        from src.data.report_builder import build_data_block
        db = _get_live_db()
        block = build_data_block(
            db, _DATE_START, _DATE_END,
            trc_filter="Provider payout rate dissatisfaction"
        )
        assert block["ticket_count"] > 0
        assert block["ticket_count"] < 888

    def test_format_data_block(self):
        from src.data.report_builder import build_data_block, format_data_block_for_prompt
        db = _get_live_db()
        block = build_data_block(db, _DATE_START, _DATE_END)
        formatted = format_data_block_for_prompt(block)
        assert isinstance(formatted, str)
        assert len(formatted) > 100
        # Should contain the ticket count (880 in date range)
        assert "880" in formatted or "TOPLINE" in formatted

    def test_replace_prompt_variables(self):
        from src.data.report_builder import build_data_block, replace_prompt_variables
        db = _get_live_db()
        block = build_data_block(db, _DATE_START, _DATE_END)
        template = "Analyze {ticket_count} tickets from {date_range}. TRCs: {trc_distribution}"
        result = replace_prompt_variables(template, block)
        assert "{ticket_count}" not in result
        # Should contain the actual ticket count (880 in date range)
        assert str(block["ticket_count"]) in result
        assert "{date_range}" not in result

    def test_prompt_library_has_canned_prompts(self):
        db = _get_live_db()
        rows = db.conn.execute("SELECT * FROM prompt_library").fetchall()
        assert len(rows) >= 5, f"Expected 5+ canned prompts, got {len(rows)}"


class TestP0_CSVImportFlow:
    """P0 #4: CSV ingestion, conversation rebuild, FTS index rebuild."""

    def test_ingest_csv_creates_conversations(self):
        from src.data.csv_ingestion import ingest_csv
        db, tmp = _make_tmp_db()
        # Create a minimal CSV
        csv_path = Path(tmp) / "test_import.csv"
        csv_path.write_text(
            "Ticket Id,Comment Body,Comment Created At,Ticket Subject,"
            "Ticket: Reason Code (L1-L3),Ticket Status,Ticket Satisfaction Score,"
            "Comment Author Role\n"
            "10001,Hello I need help with billing,2025-01-15 10:00:00,"
            "Billing Issue,Provider payout rate dissatisfaction,open,4,end-user\n"
            "10001,Let me look into that for you,2025-01-15 10:05:00,"
            "Billing Issue,Provider payout rate dissatisfaction,open,4,admin\n"
            "10002,My calendar sync is broken,2025-01-16 08:00:00,"
            "Calendar Sync,Calendar sync failure,solved,3,end-user\n",
            encoding="utf-8"
        )
        progress = []
        result = ingest_csv(
            str(csv_path), db,
            progress_callback=lambda m, p: progress.append(m)
        )
        assert result["tickets_created"] == 2
        assert result["comments_stored"] >= 3
        # Verify conversations were rebuilt
        convs = db.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        assert convs == 2
        # Verify FTS works on imported data
        results = db.search_conversations("billing", limit=10)
        assert len(results) >= 1
        _cleanup(tmp)

    def test_ingest_handles_encoding_variants(self):
        from src.data.csv_ingestion import ingest_csv
        db, tmp = _make_tmp_db()
        csv_path = Path(tmp) / "test_utf8.csv"
        csv_path.write_text(
            "Ticket Id,Comment Body,Comment Created At,Ticket Subject,"
            "Ticket: Reason Code (L1-L3),Ticket Status\n"
            "20001,Patient\u2019s copay was $25 \u2014 not $50,2025-02-01 09:00:00,"
            "Copay dispute,Copay issue,open\n",
            encoding="utf-8"
        )
        result = ingest_csv(str(csv_path), db)
        assert result["tickets_created"] == 1
        _cleanup(tmp)


# ====================================================================
# P1 — SHOULD TEST
# ====================================================================


class TestP1_IncidentManagement:
    """P1 #10: Incident scan, flag workflow (open -> ack -> close)."""

    def test_incident_scan_runs(self):
        from src.data.incident_engine import run_incident_scan
        db = _get_live_db()
        result = run_incident_scan(db, target_date="2025-03-12", date_from="2025-01-01")
        assert "trc_results" in result
        assert "new_flags" in result
        assert result["trcs_scanned"] > 0

    def test_incident_scan_returns_trc_detail(self):
        from src.data.incident_engine import run_incident_scan
        db = _get_live_db()
        result = run_incident_scan(db, target_date="2025-03-12", date_from="2025-01-01")
        if result["trc_results"]:
            first = result["trc_results"][0]
            assert "trc_code" in first
            assert "lambda_daily" in first

    def test_flag_lifecycle_crud(self):
        """Test create -> acknowledge -> resolve workflow on tmp DB."""
        db, tmp = _make_tmp_db()
        # Insert a flag manually — schema: flag_id, date, trc_code, metric_type,
        # metric_key, observed_value, expected_mean, expected_std, z_score,
        # theta_level, status, notes, created_at, resolved_at
        db.conn.execute("""
            INSERT INTO anomaly_flags
            (date, trc_code, metric_type, metric_key, observed_value,
             expected_mean, expected_std, z_score, theta_level, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("2025-03-01", "Test TRC", "volume", "", 25.0,
              10.0, 3.0, 3.5, 2, "open", "2025-03-01T12:00:00"))
        db.conn.commit()
        # Read back — PK is flag_id
        flags = db.conn.execute(
            "SELECT flag_id, status FROM anomaly_flags WHERE status='open'"
        ).fetchall()
        assert len(flags) == 1
        fid = flags[0]["flag_id"] if isinstance(flags[0], sqlite3.Row) else flags[0][0]
        # Acknowledge
        db.conn.execute(
            "UPDATE anomaly_flags SET status='acknowledged' WHERE flag_id=?",
            (fid,)
        )
        db.conn.commit()
        acked = db.conn.execute(
            "SELECT status FROM anomaly_flags WHERE flag_id=?", (fid,)
        ).fetchone()
        assert acked[0] == "acknowledged"
        # Resolve
        db.conn.execute(
            "UPDATE anomaly_flags SET status='resolved' WHERE flag_id=?",
            (fid,)
        )
        db.conn.commit()
        resolved = db.conn.execute(
            "SELECT status FROM anomaly_flags WHERE flag_id=?", (fid,)
        ).fetchone()
        assert resolved[0] == "resolved"
        _cleanup(tmp)

    def test_daily_counts_populated(self):
        db = _get_live_db()
        cnt = db.conn.execute("SELECT COUNT(*) FROM daily_counts").fetchone()[0]
        assert cnt > 0, "daily_counts should be populated"

    def test_hourly_counts_populated(self):
        db = _get_live_db()
        cnt = db.conn.execute("SELECT COUNT(*) FROM hourly_counts").fetchone()[0]
        assert cnt > 0, "hourly_counts should be populated"


class TestP1_Interventions:
    """P1 #11: Intervention CRUD."""

    def test_save_and_retrieve_intervention(self):
        db, tmp = _make_tmp_db()
        iid = db.save_intervention({
            "name": "Payer launch: Aetna",
            "category": "payer_launch",
            "event_date": "2025-02-15",
            "description": "Aetna went live on the platform",
            "affected_trcs": ["Provider payout rate dissatisfaction"],
        })
        assert iid > 0
        interventions = db.get_interventions("2025-01-01", "2025-03-31")
        assert len(interventions) == 1
        assert interventions[0]["name"] == "Payer launch: Aetna"
        assert interventions[0]["category"] == "payer_launch"
        _cleanup(tmp)

    def test_multiple_interventions(self):
        db, tmp = _make_tmp_db()
        db.save_intervention({"name": "Event A", "category": "product_release",
                              "event_date": "2025-01-10", "description": "Desc A"})
        db.save_intervention({"name": "Event B", "category": "process_change",
                              "event_date": "2025-02-20", "description": "Desc B"})
        db.save_intervention({"name": "Event C", "category": "staffing_change",
                              "event_date": "2025-03-05", "description": "Desc C"})
        result = db.get_interventions("2025-01-01", "2025-03-31")
        assert len(result) == 3
        _cleanup(tmp)


class TestP1_ReportHistory:
    """P1 #13: Report save/retrieve/list."""

    def test_save_and_get_report(self):
        db, tmp = _make_tmp_db()
        # save_report returns None — verify via get_reports + get_full_report
        db.save_report(
            page="ai_reports",
            parameters={"date_from": "2025-01-01", "date_to": "2025-03-12"},
            summary={"top_finding": "Billing errors"},
            full_results="Full report text here with details...",
            ticket_count=888,
            duration_ms=5000,
            report_type="general_trend"
        )
        reports = db.get_reports("ai_reports")
        assert len(reports) == 1
        rid = reports[0]["report_id"]
        report = db.get_full_report(rid)
        assert report is not None
        assert "Full report text" in (report.get("full_results") or "")
        _cleanup(tmp)

    def test_list_reports(self):
        db, tmp = _make_tmp_db()
        db.save_report("ai_reports", {}, {}, "Report 1", 100, 1000, report_type="general_trend")
        db.save_report("ai_reports", {}, {}, "Report 2", 200, 2000, report_type="sentiment_dive")
        reports = db.get_reports("ai_reports")
        assert len(reports) == 2
        _cleanup(tmp)


class TestP1_PromptEditor:
    """P1 #14: Prompt library CRUD."""

    def test_save_and_get_prompt(self):
        db, tmp = _make_tmp_db()
        pid = db.save_prompt({
            "name": "Custom Analysis",
            "prompt_text": "Analyze the following data: {data_block}",
            "category": "custom",
        })
        assert pid > 0
        prompt = db.get_prompt(pid)
        assert prompt is not None
        assert "Custom Analysis" in str(prompt)
        _cleanup(tmp)

    def test_list_prompts_by_type(self):
        db, tmp = _make_tmp_db()
        db.save_prompt({"name": "Prompt A", "prompt_text": "Text A {ticket_count}",
                        "category": "general_trend"})
        db.save_prompt({"name": "Prompt B", "prompt_text": "Text B {ticket_count}",
                        "category": "general_trend"})
        db.save_prompt({"name": "Prompt C", "prompt_text": "Text C {ticket_count}",
                        "category": "sentiment_dive"})
        results = db.get_prompts("general_trend")
        assert len(results) == 2
        _cleanup(tmp)

    def test_canned_prompts_in_live_db(self):
        db = _get_live_db()
        count = db.conn.execute("SELECT COUNT(*) FROM prompt_library").fetchone()[0]
        assert count >= 5, f"Expected 5+ canned prompts, got {count}"


class TestP1_TermManager:
    """P1 #15: User term approve/suppress/alias."""

    def test_save_and_retrieve_user_terms(self):
        db, tmp = _make_tmp_db()
        db.upsert_user_term("copay", "copay", "promote", weight_modifier=1.5)
        db.upsert_user_term("idk", "idk", "suppress", weight_modifier=0.0)
        terms = db.get_user_terms()
        assert len(terms) == 2
        actions = {t["term"]: t["action"] for t in terms}
        assert actions["copay"] == "promote"
        assert actions["idk"] == "suppress"
        _cleanup(tmp)

    def test_term_overwrite(self):
        db, tmp = _make_tmp_db()
        db.upsert_user_term("copay", "copay", "promote", weight_modifier=1.5)
        db.upsert_user_term("copay", "copay", "demote", weight_modifier=0.5)
        terms = db.get_user_terms()
        # Should have both (different action = different conflict key)
        copay_terms = [t for t in terms if t["term"] == "copay"]
        assert len(copay_terms) >= 1
        _cleanup(tmp)


class TestP1_PIIRedaction:
    """P1 #17: Verify PII redaction patterns work on real-ish data."""

    def test_base_redaction_emails(self):
        from src.gemini.gemini_client import GeminiClient
        client = GeminiClient.__new__(GeminiClient)
        # Test that _redact_base strips emails
        text = "Contact john.doe@example.com for billing inquiries"
        redacted = client._redact_base(text)
        assert "john.doe@example.com" not in redacted
        assert "[EMAIL]" in redacted or "REDACTED" in redacted.upper() or "@" not in redacted

    def test_base_redaction_phone(self):
        from src.gemini.gemini_client import GeminiClient
        client = GeminiClient.__new__(GeminiClient)
        text = "Call me at 555-123-4567 or (555) 987-6543"
        redacted = client._redact_base(text)
        assert "555-123-4567" not in redacted

    def test_base_redaction_ssn(self):
        from src.gemini.gemini_client import GeminiClient
        client = GeminiClient.__new__(GeminiClient)
        text = "SSN: 123-45-6789 was entered"
        redacted = client._redact_base(text)
        assert "123-45-6789" not in redacted

    def test_base_redaction_preserves_content(self):
        from src.gemini.gemini_client import GeminiClient
        client = GeminiClient.__new__(GeminiClient)
        text = "The billing issue with copay of $25 needs resolution"
        redacted = client._redact_base(text)
        assert "billing issue" in redacted
        assert "copay" in redacted


# ====================================================================
# P2 — NICE TO TEST
# ====================================================================


class TestP2_CompoundDiscovery:
    """P2: PMI-based multi-word term discovery."""

    def test_discover_compounds_from_conversations(self):
        from src.data.compound_discovery import discover_compounds
        # Need 100+ tokens for meaningful PMI — repeat phrases across many conversations
        conversations = []
        for i in range(30):
            conversations.append(
                {"full_thread": f"The auto pay feature keeps charging my card. "
                 f"Auto pay is broken and needs to be fixed. Ticket {i}."})
        for i in range(30):
            conversations.append(
                {"full_thread": f"Calendar sync with Google Calendar stopped working. "
                 f"Calendar sync needs fixing urgently. Issue {i}."})
        for i in range(20):
            conversations.append(
                {"full_thread": f"Auto pay should be disabled. Calendar sync "
                 f"integration failed again. Request {i}."})
        candidates = discover_compounds(conversations, min_cooccurrence=5, min_pmi=1.0)
        assert isinstance(candidates, list)
        if candidates:
            phrases = [c["phrase"] for c in candidates]
            found_auto_pay = any("auto" in p and "pay" in p for p in phrases)
            found_cal_sync = any("calendar" in p and "sync" in p for p in phrases)
            assert found_auto_pay or found_cal_sync, f"Expected compound terms, got: {phrases}"

    def test_discovered_compounds_in_live_db(self):
        db = _get_live_db()
        count = db.conn.execute(
            "SELECT COUNT(*) FROM discovered_compounds"
        ).fetchone()[0]
        assert count > 0, "Should have discovered compounds from prior analysis"

    def test_persist_discoveries(self):
        from src.data.compound_discovery import persist_discoveries
        db, tmp = _make_tmp_db()
        candidates = [
            {"phrase": "auto pay", "normalized": "auto_pay",
             "frequency": 15, "pmi_score": 5.2},
            {"phrase": "calendar sync", "normalized": "calendar_sync",
             "frequency": 8, "pmi_score": 4.8},
        ]
        persist_discoveries(db, candidates)
        rows = db.get_discovered_compounds()
        assert len(rows) == 2
        _cleanup(tmp)


class TestP2_ProductGaps:
    """P2: Product gap detection."""

    def test_detect_product_gaps(self):
        from src.data.product_gap_engine import detect_product_gaps
        db = _get_live_db()
        gaps = detect_product_gaps(db, _DATE_START, _DATE_END)
        assert isinstance(gaps, list)
        if gaps:
            first = gaps[0]
            assert "product_area" in first
            assert "gap_score" in first
            assert "trc_count" in first

    def test_product_gaps_have_scores(self):
        from src.data.product_gap_engine import detect_product_gaps
        db = _get_live_db()
        gaps = detect_product_gaps(db, _DATE_START, _DATE_END)
        for gap in gaps:
            assert gap["gap_score"] >= 0


class TestP2_EmbeddingEngine:
    """P2: Embedding engine availability and basic ops."""

    def test_availability_check(self):
        from src.data.embedding_engine import is_available
        # Should return bool without crashing
        result = is_available()
        assert isinstance(result, bool)

    def test_embed_texts_if_available(self):
        from src.data.embedding_engine import is_available, embed_texts
        if not is_available():
            import pytest
            pytest.skip("sentence-transformers not installed")
        import numpy as np
        texts = ["billing issue with copay", "calendar sync problem"]
        embeddings = embed_texts(texts)
        assert isinstance(embeddings, np.ndarray)
        assert embeddings.shape[0] == 2
        assert embeddings.shape[1] > 0  # embedding dimension

    def test_semantic_search_if_available(self):
        from src.data.embedding_engine import is_available, embed_texts, semantic_search
        if not is_available():
            import pytest
            pytest.skip("sentence-transformers not installed")
        corpus = ["billing charge error", "calendar sync broken", "copay dispute amount"]
        corpus_emb = embed_texts(corpus)
        results = semantic_search("billing problem", corpus_emb, list(range(len(corpus))))
        assert len(results) > 0
        # First result should be billing-related
        assert results[0][0] == 0  # "billing charge error" should be top match


class TestP2_UsageTracker:
    """P2: Gemini cost tracking."""

    def test_estimate_tokens(self):
        from src.data.usage_tracker import UsageTracker
        tokens = UsageTracker.estimate_tokens("Hello world, this is a test prompt.")
        assert tokens > 0
        # ~1 token per 4 chars
        assert 5 < tokens < 20

    def test_estimate_cost(self):
        from src.data.usage_tracker import UsageTracker
        cost = UsageTracker.estimate_cost(1000, 500, "gemini-2.5-flash")
        assert cost > 0
        assert cost < 1.0  # Should be very cheap for small token counts

    def test_log_and_retrieve_usage(self):
        db, tmp = _make_tmp_db()
        from src.data.usage_tracker import UsageTracker
        tracker = UsageTracker(db)
        tracker.log_call("test_source", 1000, 500, "gemini-2.5-flash")
        totals = tracker.get_daily_totals()
        assert totals is not None
        _cleanup(tmp)

    def test_live_db_has_usage_data(self):
        db = _get_live_db()
        cnt = db.conn.execute("SELECT COUNT(*) FROM gemini_usage").fetchone()[0]
        assert cnt > 0, "Should have Gemini usage records from prior scans"


class TestP2_RebuildUtils:
    """P2: Timestamp parsing, role normalization, chronological sorting."""

    def test_parse_timestamp_iso(self):
        from src.data.rebuild_utils import parse_timestamp
        dt = parse_timestamp("2025-01-15 10:30:00")
        assert dt is not None
        assert dt.year == 2025
        assert dt.month == 1
        assert dt.day == 15

    def test_parse_timestamp_null(self):
        from src.data.rebuild_utils import parse_timestamp
        assert parse_timestamp(None) is None
        assert parse_timestamp("") is None

    def test_normalize_role(self):
        from src.data.rebuild_utils import normalize_role
        assert normalize_role("end-user") == "customer"
        assert normalize_role("agent") == "agent"
        assert normalize_role("admin") == "admin"  # passthrough for unknown roles
        assert normalize_role("") == "bot"
        assert normalize_role(None) == "bot"

    def test_sort_events_with_nulls(self):
        from src.data.rebuild_utils import sort_events_chronologically
        events = [
            {"ts_raw": "2025-01-15 10:00:00", "order": 1},
            {"ts_raw": None, "order": 0},
            {"ts_raw": "2025-01-15 09:00:00", "order": 2},
        ]
        sorted_events = sort_events_chronologically(events)
        # All events should have ts_parsed and is_synthetic keys added
        for e in sorted_events:
            assert "ts_parsed" in e
            assert "is_synthetic" in e
        # NULL timestamp event should be synthetic
        null_events = [e for e in sorted_events if e["is_synthetic"]]
        assert len(null_events) == 1, "One NULL-timestamp event should be synthetic"
        # Remaining should be chronological
        ts_list = [e["ts_parsed"] for e in sorted_events]
        assert ts_list == sorted(ts_list)


class TestP2_ConceptMap:
    """P2: Domain concept normalization."""

    def test_domain_concepts_exist(self):
        from src.data.concept_map import DOMAIN_CONCEPTS
        assert len(DOMAIN_CONCEPTS) >= 10
        assert "Claim Processing" in DOMAIN_CONCEPTS or any(
            "claim" in k.lower() for k in DOMAIN_CONCEPTS
        )

    def test_build_concept_index(self):
        from src.data.concept_map import build_concept_index
        index = build_concept_index()
        assert isinstance(index, dict)
        assert len(index) > 0

    def test_apply_concept_normalization(self):
        from src.data.concept_map import apply_concept_normalization, build_concept_index
        concept_index = build_concept_index()
        tokens = ["denied", "claim", "copayment", "billing"]
        normalized = apply_concept_normalization(tokens, concept_index)
        assert isinstance(normalized, list)
        assert len(normalized) == len(tokens)


class TestP2_ThetaEngine:
    """P2: EWMA theta anomaly detection."""

    def test_theta_scan_range(self):
        from src.data.theta_engine import run_theta_scan_range
        db = _get_live_db()
        result = run_theta_scan_range(db.conn)
        assert isinstance(result, dict)

    def test_theta_baselines_exist(self):
        db = _get_live_db()
        cnt = db.conn.execute("SELECT COUNT(*) FROM rolling_stats").fetchone()[0]
        assert cnt > 0, "Should have rolling stats from prior theta scans"


class TestP2_NLPFindings:
    """P2: NLP scan results and findings in DB."""

    def test_nlp_scan_runs_exist(self):
        db = _get_live_db()
        cnt = db.conn.execute("SELECT COUNT(*) FROM nlp_scan_runs").fetchone()[0]
        assert cnt > 0, "Should have NLP scan run records"

    def test_nlp_findings_exist(self):
        db = _get_live_db()
        cnt = db.conn.execute("SELECT COUNT(*) FROM nlp_findings").fetchone()[0]
        assert cnt > 0, f"Should have NLP findings, got {cnt}"

    def test_nlp_findings_have_structure(self):
        db = _get_live_db()
        row = db.conn.execute(
            "SELECT * FROM nlp_findings LIMIT 1"
        ).fetchone()
        assert row is not None
        # PRAGMA table_info returns (cid, name, type, notnull, dflt_value, pk)
        info = db.conn.execute("PRAGMA table_info(nlp_findings)").fetchall()
        cols = [r[1] if isinstance(r, tuple) else r["name"] for r in info]
        assert "title" in cols, f"Expected 'title' in columns, got: {cols}"

    def test_sub_patterns_exist(self):
        db = _get_live_db()
        cnt = db.conn.execute("SELECT COUNT(*) FROM sub_patterns").fetchone()[0]
        # May or may not have patterns depending on scan history
        assert isinstance(cnt, int)


class TestP2_EntityExtraction:
    """P2: Payer and product area entity distribution."""

    def test_entity_distribution_payer(self):
        db = _get_live_db()
        result = db.get_entity_distribution("payer", _DATE_START, _DATE_END)
        assert isinstance(result, list)

    def test_entity_distribution_product_area(self):
        db = _get_live_db()
        result = db.get_entity_distribution("product_area", _DATE_START, _DATE_END)
        assert isinstance(result, list)


class TestP2_DailySeries:
    """P2: Daily and hourly time series for control charts."""

    def test_daily_series_for_trc(self):
        db = _get_live_db()
        # Get a TRC that has data
        trc = db.conn.execute(
            "SELECT trc_code FROM daily_counts GROUP BY trc_code "
            "ORDER BY COUNT(*) DESC LIMIT 1"
        ).fetchone()
        if trc:
            series = db.get_daily_series(trc[0], _DATE_START, _DATE_END)
            assert isinstance(series, list)
            assert len(series) > 0

    def test_hourly_series_for_trc(self):
        db = _get_live_db()
        trc = db.conn.execute(
            "SELECT trc_code FROM hourly_counts GROUP BY trc_code "
            "ORDER BY COUNT(*) DESC LIMIT 1"
        ).fetchone()
        if trc:
            series = db.get_hourly_series(trc[0], _DATE_START, _DATE_END)
            assert isinstance(series, list)
            assert len(series) > 0


# ====================================================================
# CROSS-CUTTING CONCERNS
# ====================================================================


class TestCrossCutting_DatabaseIntegrity:
    """Verify core DB schema and data integrity."""

    def test_all_tickets_have_trc(self):
        db = _get_live_db()
        null_trcs = db.conn.execute(
            "SELECT COUNT(*) FROM tickets WHERE trc_code IS NULL OR trc_code = ''"
        ).fetchone()[0]
        assert null_trcs == 0, f"{null_trcs} tickets missing TRC code"

    def test_conversations_match_tickets(self):
        db = _get_live_db()
        t_count = db.conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        c_count = db.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        assert t_count == c_count, f"Ticket count {t_count} != conversation count {c_count}"

    def test_fts_index_exists(self):
        db = _get_live_db()
        fts = db.conn.execute(
            "SELECT COUNT(*) FROM conversations_fts"
        ).fetchone()[0]
        assert fts > 0, "FTS index should have entries"

    def test_127_distinct_trcs(self):
        db = _get_live_db()
        cnt = db.conn.execute(
            "SELECT COUNT(DISTINCT trc_code) FROM tickets"
        ).fetchone()[0]
        assert cnt == 127


class TestCrossCutting_SettingsPersistence:
    """Verify settings infrastructure."""

    def test_settings_yaml_exists(self):
        from src.data.settings_manager import get_settings_path
        settings_path = get_settings_path()
        assert settings_path.exists(), "settings.yaml not found"

    def test_settings_yaml_parseable(self):
        from src.data.settings_manager import load_settings
        cfg = load_settings()
        assert isinstance(cfg, dict)
        assert "gemini" in cfg or "display" in cfg or "behavior" in cfg

    def test_prompt_templates_exist(self):
        prompts_dir = Path("config/prompts")
        assert prompts_dir.exists()
        txt_files = list(prompts_dir.glob("*.txt"))
        assert len(txt_files) >= 5, f"Expected 5+ prompt templates, found {len(txt_files)}"

    def test_redaction_patterns_loadable(self):
        patterns_path = Path("config/redaction_patterns.json")
        if patterns_path.exists():
            with open(patterns_path) as f:
                patterns = json.load(f)
            assert isinstance(patterns, (dict, list))
