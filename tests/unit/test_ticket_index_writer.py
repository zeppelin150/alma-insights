"""Unit tests for ticket_index_writer — dedup gate + upsert logic."""

import sqlite3
import pytest
from pathlib import Path

# Run migration SQL to create tables
_MIGRATION_SQL = (
    Path(__file__).resolve().parent.parent.parent / "migrations" / "005_persistence_layer.sql"
).read_text(encoding="utf-8")


@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    db.executescript(_MIGRATION_SQL)
    yield db
    db.close()


def _seed_ticket(conn, ticket_id, scan_id, confidence=0.9):
    conn.execute(
        """INSERT INTO ticket_index
        (ticket_id, first_seen_scan_id, last_seen_scan_id, first_seen_date,
         classification_confidence)
        VALUES (?, ?, ?, datetime('now'), ?)""",
        (ticket_id, scan_id, scan_id, confidence),
    )
    conn.commit()


class TestShouldClassifyTicket:
    def test_new_ticket_returns_classify(self, conn):
        from src.services.ticket_index_writer import should_classify_ticket
        assert should_classify_ticket("T-999", "scan-1", conn) == "classify"

    def test_same_scan_returns_skip(self, conn):
        from src.services.ticket_index_writer import should_classify_ticket
        _seed_ticket(conn, "T-100", "scan-1")
        assert should_classify_ticket("T-100", "scan-1", conn) == "skip"

    def test_high_confidence_returns_update(self, conn):
        from src.services.ticket_index_writer import should_classify_ticket
        _seed_ticket(conn, "T-100", "scan-1", confidence=0.85)
        assert should_classify_ticket("T-100", "scan-2", conn) == "update_scan_id"

    def test_low_confidence_returns_classify(self, conn):
        from src.services.ticket_index_writer import should_classify_ticket
        _seed_ticket(conn, "T-100", "scan-1", confidence=0.5)
        assert should_classify_ticket("T-100", "scan-2", conn) == "classify"

    def test_null_confidence_returns_classify(self, conn):
        from src.services.ticket_index_writer import should_classify_ticket
        _seed_ticket(conn, "T-100", "scan-1", confidence=None)
        assert should_classify_ticket("T-100", "scan-2", conn) == "classify"


class TestUpdateScanReference:
    def test_updates_last_seen(self, conn):
        from src.services.ticket_index_writer import update_scan_reference
        _seed_ticket(conn, "T-100", "scan-1")
        update_scan_reference("T-100", "scan-2", conn)
        conn.commit()
        row = conn.execute(
            "SELECT last_seen_scan_id FROM ticket_index WHERE ticket_id = 'T-100'"
        ).fetchone()
        assert row[0] == "scan-2"


class TestUpsertTicketIndex:
    def test_insert_new(self, conn):
        from src.services.ticket_index_writer import upsert_ticket_index
        classification = {
            "summary": "Billing issue with charge",
            "friction_type": "billing_error",
            "sub_cluster": "duplicate_charge",
            "sub_cluster_confidence": 0.92,
            "sentiment_polarity": "negative",
            "sentiment_intensity": 0.8,
            "anomaly_flag": None,
            "key_phrases": ["duplicate charge", "billing"],
            "is_novel": False,
        }
        meta = {
            "trc": "BIL-01",
            "trc_label": "Billing",
            "subject": "Help with my bill",
            "created_at": "2025-01-15",
            "csat": 2.5,
            "message_count": 4,
            "dataset_id": "ds-1",
        }
        upsert_ticket_index("T-200", "scan-1", classification, meta, conn)
        conn.commit()

        row = conn.execute("SELECT * FROM ticket_index WHERE ticket_id = 'T-200'").fetchone()
        assert row is not None
        # issue_snippet comes from summary
        assert row[8] == "Billing issue with charge"
        # friction_type
        assert row[9] == "billing_error"

    def test_upsert_updates_on_conflict(self, conn):
        from src.services.ticket_index_writer import upsert_ticket_index
        c1 = {"summary": "First", "friction_type": "a", "sub_cluster_confidence": 0.7}
        upsert_ticket_index("T-300", "scan-1", c1, {}, conn)
        conn.commit()

        c2 = {"summary": "Updated", "friction_type": "b", "sub_cluster_confidence": 0.95}
        upsert_ticket_index("T-300", "scan-2", c2, {}, conn)
        conn.commit()

        row = conn.execute("SELECT issue_snippet, friction_type FROM ticket_index WHERE ticket_id = 'T-300'").fetchone()
        assert row[0] == "Updated"
        assert row[1] == "b"

    def test_idempotent_same_scan(self, conn):
        from src.services.ticket_index_writer import upsert_ticket_index
        c = {"summary": "Test", "sub_cluster_confidence": 0.8}
        upsert_ticket_index("T-400", "scan-1", c, {}, conn)
        conn.commit()
        upsert_ticket_index("T-400", "scan-1", c, {}, conn)
        conn.commit()

        count = conn.execute("SELECT COUNT(*) FROM ticket_index WHERE ticket_id = 'T-400'").fetchone()[0]
        assert count == 1
