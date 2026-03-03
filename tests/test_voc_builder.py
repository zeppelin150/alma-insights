"""
Build 6.0 — VOC Root Cause Analysis Tests

Tests the VOCBuilder pipeline: planning, sampling, JSONL packaging,
NLP aggregation, prompt assembly, and UI integration.
"""

import sys
import os
import json
import tempfile
import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from src.data.db_manager import DatabaseManager
from src.data.voc_builder import VOCBuilder


def _make_db():
    """Create a temp DB with schema initialized."""
    tmp = tempfile.mkdtemp()
    db_path = Path(tmp) / "test_voc.db"
    db = DatabaseManager(db_path)
    db.initialize()
    return db, tmp


def _cleanup(tmp):
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


def _seed_tickets(db, trc="TEST-TRC", n=50, date_prefix="2025-01-"):
    """Insert n dummy tickets into tickets + conversations tables."""
    for i in range(n):
        day = (i % 28) + 1
        tid = f"T-{trc}-{i:04d}"
        created = f"{date_prefix}{day:02d} 10:00:00"
        # Parent ticket row (foreign key target)
        db.conn.execute("""
            INSERT OR IGNORE INTO tickets
                (ticket_id, subject, trc_code, trc_label, status,
                 csat_score, created_at)
            VALUES (?, ?, ?, ?, 'solved', ?, ?)
        """, (tid, f"Test subject {i} for {trc}", trc,
              f"Test TRC Label ({trc})", (i % 5) + 1, created))
        # Conversation row
        db.conn.execute("""
            INSERT OR IGNORE INTO conversations
                (ticket_id, subject, trc_code, trc_label, csat_score,
                 created_at, full_thread, message_count, client_messages, agent_messages)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            tid,
            f"Test subject {i} for {trc}",
            trc,
            f"Test TRC Label ({trc})",
            (i % 5) + 1,  # CSAT 1-5
            created,
            f"Customer message: I have an issue #{i} with {trc}. "
            f"This is a test thread with enough content to verify truncation works correctly. "
            f"The thread includes various details about the problem encountered.",
            3,
            2,
            1,
        ))
    db.conn.commit()


def _seed_nlp_data(db, trc="TEST-TRC", scan_id="scan-001", n=30):
    """Insert dummy NLP scan + classifications."""
    # Create scan run
    db.conn.execute("""
        INSERT OR IGNORE INTO nlp_scan_runs
            (scan_id, status, date_range_start, date_range_end,
             total_tickets, mode, created_at)
        VALUES (?, 'completed', '2025-01-01', '2025-01-31', ?, 'agentic', ?)
    """, (scan_id, n, "2025-01-15T10:00:00"))

    # Create batch
    db.conn.execute("""
        INSERT OR IGNORE INTO nlp_batches
            (batch_id, scan_id, batch_number, trc, status, ticket_count,
             created_at)
        VALUES (?, ?, 0, ?, 'completed', ?, '2025-01-15T10:00:00')
    """, (f"batch-{trc}-001", scan_id, trc, n))

    friction_types = ["access_blocked", "policy_confusion", "billing_error",
                      "portal_issue", "documentation_gap"]
    sentiments = ["negative", "mixed", "positive", "neutral"]
    anomaly_flags = ["none", "none", "none", "critical", "unusual"]

    for i in range(n):
        db.conn.execute("""
            INSERT OR IGNORE INTO nlp_ticket_classifications
                (classification_id, batch_id, scan_id, ticket_id, trc,
                 sub_cluster, friction_type, sentiment_polarity,
                 sentiment_intensity, anomaly_flag, root_cause_hint,
                 created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            f"cls-{trc}-{i:04d}",
            f"batch-{trc}-001",
            scan_id,
            f"T-{trc}-{i:04d}",
            trc,
            f"Sub-pattern {i % 3}",
            friction_types[i % len(friction_types)],
            sentiments[i % len(sentiments)],
            (i % 5) + 1,
            anomaly_flags[i % len(anomaly_flags)],
            f"Root cause hint {i % 5}",
            "2025-01-15T10:00:00",
        ))
    db.conn.commit()


# ═══════════════════════════════════════════
#  PLAN TESTS
# ═══════════════════════════════════════════

def test_plan_structure():
    """plan() returns expected keys without calling Gemini."""
    db, tmp = _make_db()
    _seed_tickets(db, "TRC-A", 100)
    _seed_tickets(db, "TRC-B", 50)

    gc = MagicMock()
    gc.model = "gemini-2.5-flash"
    builder = VOCBuilder(db, gc)

    plan = builder.plan("2025-01-01", "2025-01-31")

    assert "trc_plans" in plan
    assert "total_tickets" in plan
    assert "total_sampled" in plan
    assert "total_batches" in plan
    assert "total_gemini_calls" in plan
    assert "est_cost_usd" in plan
    assert "est_time_min" in plan
    assert "has_nlp_data" in plan
    assert "model" in plan

    assert plan["total_tickets"] == 150
    assert len(plan["trc_plans"]) == 2
    assert plan["model"] == "gemini-2.5-flash"

    # Gemini should NOT have been called
    gc.generate.assert_not_called()

    _cleanup(tmp)


def test_plan_with_trc_filter():
    """plan() respects trc_filter parameter."""
    db, tmp = _make_db()
    _seed_tickets(db, "TRC-A", 100)
    _seed_tickets(db, "TRC-B", 50)

    gc = MagicMock()
    gc.model = "gemini-2.5-flash"
    builder = VOCBuilder(db, gc)

    plan = builder.plan("2025-01-01", "2025-01-31", trc_filter="TRC-A")
    assert len(plan["trc_plans"]) == 1
    assert plan["trc_plans"][0]["trc"] == "TRC-A"

    _cleanup(tmp)


# ═══════════════════════════════════════════
#  SAMPLING TESTS
# ═══════════════════════════════════════════

def test_sampling_below_threshold():
    """All tickets included for small TRCs (below SAMPLE_ALL_THRESHOLD)."""
    db, tmp = _make_db()
    _seed_tickets(db, "SMALL", 50)

    gc = MagicMock()
    gc.model = "gemini-2.5-flash"
    builder = VOCBuilder(db, gc)
    builder.SAMPLE_ALL_THRESHOLD = 200

    plan = builder.plan("2025-01-01", "2025-01-31")
    trc_plan = plan["trc_plans"][0]
    assert trc_plan["sampled"] == 50  # all included

    _cleanup(tmp)


def test_sampling_above_threshold():
    """Large TRCs sampled down to SAMPLE_LARGE_MAX."""
    db, tmp = _make_db()
    # Insert 1500 tickets (above 1000 threshold)
    for batch in range(15):
        _seed_tickets(db, "BIG", 100, date_prefix=f"2025-01-")

    gc = MagicMock()
    gc.model = "gemini-2.5-flash"
    builder = VOCBuilder(db, gc)

    plan = builder.plan("2025-01-01", "2025-01-31")
    trc_plan = [p for p in plan["trc_plans"] if p["trc"] == "BIG"][0]

    # Should be capped at SAMPLE_LARGE_MAX (300)
    assert trc_plan["sampled"] <= builder.SAMPLE_LARGE_MAX

    _cleanup(tmp)


def test_stratified_sampling_csat():
    """CSAT-stratified sampling covers all quartiles."""
    db, tmp = _make_db()
    _seed_tickets(db, "STRAT", 500)

    gc = MagicMock()
    gc.model = "gemini-2.5-flash"
    builder = VOCBuilder(db, gc)

    tickets = db.get_tickets_for_trc("STRAT", "2025-01-01", "2025-01-31")
    sampled = builder._stratified_sample_csat(tickets, 100)

    assert len(sampled) == 100
    # Should have tickets from different CSAT scores
    csat_values = {t["csat_score"] for t in sampled if t.get("csat_score")}
    assert len(csat_values) > 1  # At least 2 different scores

    _cleanup(tmp)


def test_stratified_sampling_nlp():
    """NLP-stratified sampling with anomaly over-sampling."""
    db, tmp = _make_db()
    _seed_tickets(db, "NLP-TRC", 300)
    _seed_nlp_data(db, "NLP-TRC", "scan-001", 300)

    gc = MagicMock()
    gc.model = "gemini-2.5-flash"
    builder = VOCBuilder(db, gc)

    tickets = db.get_tickets_for_trc("NLP-TRC", "2025-01-01", "2025-01-31")
    sampled = builder._stratified_sample_nlp(
        tickets, 100, "NLP-TRC", "scan-001"
    )

    assert sampled is not None
    assert len(sampled) == 100

    _cleanup(tmp)


# ═══════════════════════════════════════════
#  JSONL PACKAGING TESTS
# ═══════════════════════════════════════════

def test_jsonl_truncation():
    """Threads truncated to ANALYSIS_TRUNCATION."""
    db, tmp = _make_db()

    gc = MagicMock()
    gc.model = "gemini-2.5-flash"
    builder = VOCBuilder(db, gc)
    builder.ANALYSIS_TRUNCATION = 100  # short for testing

    ticket = {
        "ticket_id": "T-001",
        "trc_code": "TEST",
        "created_at": "2025-01-15",
        "subject": "Test subject",
        "csat_score": 3,
        "full_thread": "A" * 5000,  # way longer than 100
    }

    line = builder._package_ticket_jsonl(ticket)
    record = json.loads(line)

    assert len(record["thread"]) <= 100

    _cleanup(tmp)


def test_jsonl_redaction():
    """PII patterns replaced in JSONL output."""
    db, tmp = _make_db()

    gc = MagicMock()
    gc.model = "gemini-2.5-flash"
    builder = VOCBuilder(db, gc)

    ticket = {
        "ticket_id": "T-001",
        "trc_code": "TEST",
        "created_at": "2025-01-15",
        "subject": "Email from test@example.com about SSN 123-45-6789",
        "csat_score": 2,
        "full_thread": "Call me at 555-123-4567 or email test@example.com",
    }

    line = builder._package_ticket_jsonl(ticket)
    record = json.loads(line)

    # PII should be redacted
    assert "test@example.com" not in record["subject"]
    assert "123-45-6789" not in record["subject"]
    assert "555-123-4567" not in record["thread"]
    assert "test@example.com" not in record["thread"]

    _cleanup(tmp)


def test_jsonl_nlp_enrichment():
    """JSONL includes NLP fields when scan data available."""
    db, tmp = _make_db()
    _seed_tickets(db, "NLP-E", 5)
    _seed_nlp_data(db, "NLP-E", "scan-001", 5)

    gc = MagicMock()
    gc.model = "gemini-2.5-flash"
    builder = VOCBuilder(db, gc)

    ticket = db.get_tickets_for_trc("NLP-E", "2025-01-01", "2025-01-31")[0]
    line = builder._package_ticket_jsonl(ticket, scan_id="scan-001")
    record = json.loads(line)

    assert "nlp_friction" in record
    assert "nlp_sentiment" in record
    assert "nlp_root_cause" in record

    _cleanup(tmp)


# ═══════════════════════════════════════════
#  NLP AGGREGATE TESTS
# ═══════════════════════════════════════════

def test_nlp_aggregate_query():
    """get_nlp_aggregate_for_trc returns correct distribution."""
    db, tmp = _make_db()
    _seed_tickets(db, "AGG", 30)
    _seed_nlp_data(db, "AGG", "scan-001", 30)

    agg = db.get_nlp_aggregate_for_trc("AGG", "scan-001")
    assert agg is not None
    assert agg["total_classified"] == 30
    assert "friction_distribution" in agg
    assert "sentiment_distribution" in agg
    assert "anomaly_counts" in agg
    assert "top_sub_clusters" in agg
    assert "top_root_cause_hints" in agg

    # Friction types should be populated
    assert len(agg["friction_distribution"]) > 0

    _cleanup(tmp)


def test_nlp_aggregate_no_data():
    """get_nlp_aggregate_for_trc returns None when no NLP data."""
    db, tmp = _make_db()
    _seed_tickets(db, "EMPTY", 10)

    agg = db.get_nlp_aggregate_for_trc("EMPTY")
    assert agg is None

    _cleanup(tmp)


def test_nlp_context_formatting():
    """_build_nlp_context_for_trc formats aggregates correctly."""
    db, tmp = _make_db()
    _seed_tickets(db, "FMT", 30)
    _seed_nlp_data(db, "FMT", "scan-001", 30)

    gc = MagicMock()
    gc.model = "gemini-2.5-flash"
    builder = VOCBuilder(db, gc)

    context = builder._build_nlp_context_for_trc("FMT", "scan-001")
    assert "NLP SCAN CONTEXT" in context
    assert "Classified: 30 tickets" in context
    assert "Friction distribution:" in context

    _cleanup(tmp)


# ═══════════════════════════════════════════
#  PROMPT ASSEMBLY TESTS
# ═══════════════════════════════════════════

def test_prompt_variables_replaced():
    """No unreplaced {tokens} in analysis prompt."""
    db, tmp = _make_db()

    gc = MagicMock()
    gc.model = "gemini-2.5-flash"
    builder = VOCBuilder(db, gc)

    prompt = builder._assemble_analysis_prompt(
        trc="TEST",
        trc_label="Test Label",
        date_start="2025-01-01",
        date_end="2025-01-31",
        n_tickets=50,
        total_trc_tickets=100,
        nlp_context="NLP context here",
        statistical_context="Stats here",
        ticket_jsonl='{"ticket_id":"T-001"}',
    )

    # Check no unreplaced tokens
    import re
    unreplaced = re.findall(r'\{[a-z_]+\}', prompt)
    assert len(unreplaced) == 0, f"Unreplaced tokens: {unreplaced}"

    _cleanup(tmp)


def test_synthesis_prompt_assembly():
    """All TRC analyses present in synthesis input."""
    db, tmp = _make_db()

    gc = MagicMock()
    gc.model = "gemini-2.5-flash"
    builder = VOCBuilder(db, gc)

    trc_analyses = {
        "TRC-A": "Analysis for TRC-A: friction themes...",
        "TRC-B": "Analysis for TRC-B: different themes...",
    }
    formatted = builder._format_trc_analyses_for_synthesis(trc_analyses)

    assert "TRC: TRC-A" in formatted
    assert "TRC: TRC-B" in formatted
    assert "Analysis for TRC-A" in formatted
    assert "Analysis for TRC-B" in formatted

    _cleanup(tmp)


# ═══════════════════════════════════════════
#  COST ESTIMATE TEST
# ═══════════════════════════════════════════

def test_cost_estimate_reasonable():
    """Estimate in expected range for typical workload."""
    db, tmp = _make_db()
    for trc_code in ["A", "B", "C", "D", "E"]:
        _seed_tickets(db, trc_code, 200)

    gc = MagicMock()
    gc.model = "gemini-2.5-flash"
    builder = VOCBuilder(db, gc)

    plan = builder.plan("2025-01-01", "2025-01-31")
    # 5 TRCs + 1 synthesis = 6 calls
    assert plan["total_gemini_calls"] == 6
    assert plan["est_cost_usd"] > 0
    assert plan["est_cost_usd"] < 10.0  # should be well under $10
    assert plan["est_time_min"] > 0

    _cleanup(tmp)


# ═══════════════════════════════════════════
#  CANCEL TEST
# ═══════════════════════════════════════════

def test_cancel_stops_pipeline():
    """Cancel flag checked between TRC analyses."""
    db, tmp = _make_db()
    _seed_tickets(db, "CAN-A", 50)
    _seed_tickets(db, "CAN-B", 50)

    gc = MagicMock()
    gc.model = "gemini-2.5-flash"

    builder = VOCBuilder(db, gc)

    # Use side_effect to cancel after first Gemini call
    call_count = [0]
    def cancel_after_first(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] >= 1:
            builder.cancel()
        return "Mock analysis response"

    gc.generate.side_effect = cancel_after_first
    result = builder.run("2025-01-01", "2025-01-31")

    assert "cancelled" in result["report_text"].lower()
    # Should have processed fewer TRCs than total (2)
    assert len(result["trc_analyses"]) < 2

    _cleanup(tmp)


# ═══════════════════════════════════════════
#  END-TO-END TEST (MOCKED GEMINI)
# ═══════════════════════════════════════════

def test_end_to_end_mock_gemini():
    """Full pipeline with mocked generate() returns report structure."""
    db, tmp = _make_db()
    _seed_tickets(db, "E2E-A", 30)
    _seed_tickets(db, "E2E-B", 20)

    gc = MagicMock()
    gc.model = "gemini-2.5-flash"
    gc.generate.return_value = "## FRICTION THEMES\n\nMock analysis output..."

    builder = VOCBuilder(db, gc)
    result = builder.run("2025-01-01", "2025-01-31")

    assert "report_text" in result
    assert "trc_analyses" in result
    assert "stats" in result
    assert len(result["trc_analyses"]) == 2
    assert "E2E-A" in result["trc_analyses"]
    assert "E2E-B" in result["trc_analyses"]

    # Gemini should have been called:
    # 2 analysis calls + 1 synthesis = 3
    assert gc.generate.call_count == 3

    _cleanup(tmp)


# ═══════════════════════════════════════════
#  UI INTEGRATION TESTS
# ═══════════════════════════════════════════

def test_ui_prompt_combo_has_voc():
    """AI Reports dropdown includes VOC entry."""
    db, tmp = _make_db()
    from src.ui.pages.ai_reports import AIReportsPage

    page = AIReportsPage(db)
    found = False
    for i in range(page._prompt_combo.count()):
        if page._prompt_combo.itemData(i) == "voc_rca":
            found = True
            break
    assert found, "VOC Root Cause Analysis not found in prompt combo"

    _cleanup(tmp)


def test_voc_worker_class_exists():
    """VOCReportWorker class is importable and has expected signals."""
    from src.ui.pages.ai_reports import VOCReportWorker
    assert hasattr(VOCReportWorker, "progress")
    assert hasattr(VOCReportWorker, "finished")
    assert hasattr(VOCReportWorker, "error")
    assert hasattr(VOCReportWorker, "cancel")


def test_main_window_has_voc_job_factory():
    """MainWindow has _make_voc_report_job method."""
    from src.ui.main_window import MainWindow
    assert hasattr(MainWindow, "_make_voc_report_job")


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "--tb=short"])
