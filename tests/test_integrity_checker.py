"""Tests for post-scan data integrity checker."""

import json
import sqlite3
import pytest
from src.data.integrity_checker import (
    check_data_integrity,
    run_post_scan_integrity,
    IntegrityIssue,
)


@pytest.fixture
def conn(tmp_path):
    """Minimal DB with the tables integrity_checker inspects."""
    db = tmp_path / "test.db"
    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE ticket_index (
            ticket_id TEXT PRIMARY KEY,
            trc_code TEXT,
            sub_pattern TEXT,
            anomaly_flag TEXT,
            ticket_created_date TEXT,
            issue_snippet TEXT
        );
        CREATE TABLE ticket_embeddings (
            ticket_id TEXT PRIMARY KEY,
            embedding BLOB,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE conversations (
            ticket_id TEXT,
            subject TEXT,
            trc_label TEXT,
            full_thread TEXT
        );
        CREATE VIRTUAL TABLE conversations_fts USING fts5(
            ticket_id, subject, trc_label, full_thread
        );
        CREATE TABLE nlp_ticket_classifications (
            ticket_id TEXT,
            scan_id TEXT,
            entities_json TEXT
        );
        CREATE TABLE nlp_scan_runs (
            scan_id TEXT PRIMARY KEY,
            status TEXT,
            started_at TEXT
        );
    """)
    yield c
    c.close()


def _seed_healthy(conn, n=10):
    """Insert n consistent rows across all tables."""
    for i in range(n):
        tid = f"T{i:04d}"
        conn.execute(
            "INSERT INTO ticket_index (ticket_id, trc_code) VALUES (?, ?)",
            (tid, "TRC-A"),
        )
        conn.execute(
            "INSERT INTO ticket_embeddings (ticket_id, embedding) VALUES (?, ?)",
            (tid, b"\x00" * 64),
        )
        conn.execute(
            "INSERT INTO conversations (ticket_id, subject, trc_label, full_thread) "
            "VALUES (?, ?, ?, ?)",
            (tid, f"Subject {i}", "TRC-A", f"Thread {i}"),
        )
        conn.execute(
            "INSERT INTO conversations_fts (ticket_id, subject, trc_label, full_thread) "
            "VALUES (?, ?, ?, ?)",
            (tid, f"Subject {i}", "TRC-A", f"Thread {i}"),
        )
        conn.execute(
            "INSERT INTO nlp_ticket_classifications (ticket_id, scan_id, entities_json) "
            "VALUES (?, ?, ?)",
            (tid, "scan-1", json.dumps({"payer": "Acme Corp", "product_area": "Billing"})),
        )
    conn.execute(
        "INSERT INTO nlp_scan_runs (scan_id, status, started_at) "
        "VALUES ('scan-1', 'complete', '2026-04-01T12:00:00')",
    )
    conn.commit()


class TestHealthyState:
    def test_all_checks_pass(self, conn):
        _seed_healthy(conn)
        issues = check_data_integrity(conn)
        errors = [i for i in issues if i.severity == "error"]
        assert errors == [], f"Unexpected errors: {errors}"


class TestMissingEmbeddings:
    def test_flags_divergence(self, conn):
        _seed_healthy(conn, n=10)
        # Delete some embeddings to create divergence
        conn.execute("DELETE FROM ticket_embeddings WHERE ticket_id IN ('T0005','T0006','T0007','T0008','T0009')")
        conn.commit()
        issues = check_data_integrity(conn)
        names = [i.check_name for i in issues]
        assert "embedding_count_divergence" in names


class TestStaleEmbeddings:
    def test_flags_freshness(self, conn):
        _seed_healthy(conn)
        # Make embeddings look old
        conn.execute("UPDATE ticket_embeddings SET created_at = '2026-01-01T00:00:00'")
        conn.commit()
        issues = check_data_integrity(conn)
        names = [i.check_name for i in issues]
        assert "embedding_freshness" in names


class TestFTSDesync:
    def test_flags_count_mismatch(self, conn):
        _seed_healthy(conn)
        # Add conversations without FTS entries
        for i in range(10, 20):
            tid = f"T{i:04d}"
            conn.execute(
                "INSERT INTO conversations (ticket_id, subject, trc_label, full_thread) "
                "VALUES (?, ?, ?, ?)",
                (tid, f"Subject {i}", "TRC-B", f"Thread {i}"),
            )
        conn.commit()
        issues = check_data_integrity(conn)
        names = [i.check_name for i in issues]
        assert "fts_count_mismatch" in names


class TestOrphanedClassifications:
    def test_flags_orphans(self, conn):
        _seed_healthy(conn)
        # Insert classification for nonexistent ticket
        conn.execute(
            "INSERT INTO nlp_ticket_classifications (ticket_id, scan_id, entities_json) "
            "VALUES ('ORPHAN-999', 'scan-1', '{}')",
        )
        conn.commit()
        issues = check_data_integrity(conn)
        names = [i.check_name for i in issues]
        assert "orphaned_classifications" in names


class TestCaseInconsistency:
    def test_flags_case_variants(self, conn):
        _seed_healthy(conn)
        # Add a classification with lowercase "billing" (existing has "Billing")
        conn.execute(
            "INSERT INTO nlp_ticket_classifications (ticket_id, scan_id, entities_json) "
            "VALUES ('T0000', 'scan-2', ?)",
            (json.dumps({"product_area": "billing"}),),
        )
        conn.commit()
        issues = check_data_integrity(conn)
        case_issues = [i for i in issues if i.check_name == "entity_case_inconsistency"]
        assert len(case_issues) > 0


class TestEmptyTicketIndex:
    def test_flags_as_error(self, conn):
        # No data seeded — ticket_index is empty
        issues = check_data_integrity(conn)
        errors = [i for i in issues if i.severity == "error"]
        assert any(i.check_name == "ticket_index_empty" for i in errors)


class TestRunPostScanIntegrity:
    def test_returns_correct_structure(self, conn):
        _seed_healthy(conn)
        result = run_post_scan_integrity(conn, "test-scan-123")
        assert "passed" in result
        assert "issues" in result
        assert "checked_at" in result
        assert "scan_id" in result
        assert result["scan_id"] == "test-scan-123"
        assert isinstance(result["issues"], list)
        assert result["passed"] is True
