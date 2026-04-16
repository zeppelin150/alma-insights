"""Unit tests for alma_query CLI tool."""

import sqlite3
import json
import subprocess
import sys
import tempfile
import os
import pytest
from pathlib import Path

_MIGRATION_SQL = (
    Path(__file__).resolve().parent.parent.parent / "migrations" / "005_persistence_layer.sql"
).read_text(encoding="utf-8")

_EXTRA_TABLES = """
CREATE TABLE IF NOT EXISTS conversations (ticket_id TEXT PRIMARY KEY, full_thread TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS conversations_fts USING fts5(ticket_id, subject, trc_label, full_thread);
"""


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript(_MIGRATION_SQL)
    conn.executescript(_EXTRA_TABLES)

    # Seed ticket_index
    for i in range(10):
        conn.execute(
            """INSERT INTO ticket_index
            (ticket_id, first_seen_scan_id, last_seen_scan_id, first_seen_date,
             ticket_created_date, trc_code, trc_label, subject_sanitized,
             issue_snippet, friction_type, sub_pattern, sentiment_polarity,
             sentiment_intensity, anomaly_flag, csat_score, classification_method)
            VALUES (?, 's1', 's1', datetime('now'), '2025-02-15', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                f"T-{i}",
                "BIL-01" if i < 6 else "TECH-01",
                "Billing" if i < 6 else "Technical",
                f"Subject {i}",
                f"Issue snippet for ticket {i} about billing problem" if i < 6 else f"Tech issue {i}",
                "billing_error" if i < 6 else "access_issue",
                "duplicate_charge" if i < 3 else "copay_error",
                "negative" if i < 4 else "positive",
                0.7 if i < 4 else 0.3,
                "critical" if i == 0 else None,
                3.5 if i < 5 else 4.2,
                "llm",
            ),
        )

    # Seed analysis_runs
    conn.execute(
        """INSERT INTO analysis_runs
        (run_id, run_date, prompt_template, output_text, ticket_count, cost_usd, source)
        VALUES ('r-1', datetime('now'), 'voc_root_cause', '# Report', 100, 0.12, 'manual')"""
    )

    # Seed insight_ledger
    conn.execute(
        """INSERT INTO insight_ledger
        (insight_id, date_identified, insight_type, title, severity, status)
        VALUES ('ins-1', datetime('now'), 'trend', 'New pattern detected', 'moderate', 'new')"""
    )

    conn.commit()
    conn.close()
    yield path
    os.unlink(path)


def _run_query(db_path, *args):
    """Run alma_query as subprocess and return parsed JSON."""
    cmd = [sys.executable, "-m", "src.tools.alma_query", "--db", db_path] + list(args)
    result = subprocess.run(
        cmd, capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent.parent)
    )
    return json.loads(result.stdout) if result.stdout.strip() else None


class TestTickets:
    def test_returns_all(self, db_path):
        data = _run_query(db_path, "tickets", "--limit", "25")
        assert isinstance(data, list)
        assert len(data) == 10

    def test_filter_by_trc(self, db_path):
        data = _run_query(db_path, "tickets", "--trc", "BIL-01")
        assert all(t["trc_code"] == "BIL-01" for t in data)

    def test_filter_by_keyword(self, db_path):
        data = _run_query(db_path, "tickets", "--keyword", "billing problem")
        assert len(data) > 0


class TestTicketDetail:
    def test_found(self, db_path):
        data = _run_query(db_path, "ticket-detail", "--id", "T-0")
        assert data["ticket_id"] == "T-0"
        assert data["full_thread_available"] is False

    def test_not_found(self, db_path):
        data = _run_query(db_path, "ticket-detail", "--id", "T-999")
        assert "error" in data


class TestSearch:
    def test_search_fallback(self, db_path):
        data = _run_query(db_path, "search", "--query", "billing")
        assert isinstance(data, list)
        assert len(data) > 0


class TestInsights:
    def test_list_all(self, db_path):
        data = _run_query(db_path, "insights")
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_filter_status(self, db_path):
        data = _run_query(db_path, "insights", "--status", "new")
        assert all(i["status"] == "new" for i in data)


class TestPastAnalysis:
    def test_list_runs(self, db_path):
        data = _run_query(db_path, "past-analysis")
        assert isinstance(data, list)
        assert len(data) >= 1


class TestValidate:
    def test_validate_ticket_count(self, db_path):
        data = _run_query(db_path, "validate", "--claim", "10 tickets total")
        assert data["claim_valid"] is True
        assert data["actual_value"] == 10
