"""Tests for entity normalization pipeline."""

import json
import sqlite3
import pytest
from src.data.entity_normalizer import (
    normalize_entities_for_scan,
    query_by_entity,
    get_entity_distribution,
    _normalize_value,
)
from src.data.chat_tools.fast_path import handle_query_entities


@pytest.fixture
def conn(tmp_path):
    """DB with nlp_ticket_classifications + ticket_entities_normalized."""
    db = tmp_path / "test.db"
    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE nlp_ticket_classifications (
            ticket_id TEXT,
            scan_id TEXT,
            entities_json TEXT
        );
        CREATE TABLE ticket_entities_normalized (
            ticket_id    TEXT NOT NULL,
            entity_type  TEXT NOT NULL,
            entity_value TEXT NOT NULL,
            scan_id      TEXT,
            created_at   TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (ticket_id, entity_type)
        );
        CREATE INDEX idx_tent_type_value
            ON ticket_entities_normalized(entity_type, entity_value);
    """)
    yield c
    c.close()


def _seed(conn, scan_id="scan-1"):
    """Insert test classifications."""
    data = [
        ("T001", {"payer": "Centaur Benefits", "product_area": "Billing"}),
        ("T002", {"payer": "Centaur Benefits", "product_area": "Claims"}),
        ("T003", {"payer": "Griffin Health Group", "product_area": "billing"}),
        ("T004", {"payer": "Pegasus PPO", "product_area": "Billing"}),
        ("T005", {}),  # empty entities
    ]
    for tid, entities in data:
        conn.execute(
            "INSERT INTO nlp_ticket_classifications (ticket_id, scan_id, entities_json) "
            "VALUES (?, ?, ?)",
            (tid, scan_id, json.dumps(entities)),
        )
    # One with NULL entities_json
    conn.execute(
        "INSERT INTO nlp_ticket_classifications (ticket_id, scan_id, entities_json) "
        "VALUES ('T006', ?, NULL)",
        (scan_id,),
    )
    conn.commit()


class TestNormalize:
    def test_extracts_payer(self, conn):
        _seed(conn)
        count = normalize_entities_for_scan(conn, "scan-1")
        assert count > 0
        rows = conn.execute(
            "SELECT ticket_id FROM ticket_entities_normalized "
            "WHERE entity_type = 'payer' AND entity_value = 'Centaur Benefits'"
        ).fetchall()
        assert len(rows) == 2

    def test_case_normalizes_billing(self, conn):
        _seed(conn)
        normalize_entities_for_scan(conn, "scan-1")
        # "billing" (lowercase) should be normalized to "Billing"
        rows = conn.execute(
            "SELECT entity_value FROM ticket_entities_normalized "
            "WHERE ticket_id = 'T003' AND entity_type = 'product_area'"
        ).fetchall()
        assert rows[0][0] == "Billing"

    def test_handles_null_entities(self, conn):
        _seed(conn)
        # Should not crash on NULL or empty entities
        count = normalize_entities_for_scan(conn, "scan-1")
        assert count >= 0

    def test_idempotent(self, conn):
        _seed(conn)
        count1 = normalize_entities_for_scan(conn, "scan-1")
        count2 = normalize_entities_for_scan(conn, "scan-1")
        assert count1 == count2
        total = conn.execute(
            "SELECT COUNT(*) FROM ticket_entities_normalized"
        ).fetchone()[0]
        # INSERT OR REPLACE means same rows, not doubled
        assert total == count1


class TestQueryByEntity:
    def test_returns_correct_ids(self, conn):
        _seed(conn)
        normalize_entities_for_scan(conn, "scan-1")
        ids = query_by_entity(conn, "payer", "Centaur Benefits")
        assert set(ids) == {"T001", "T002"}

    def test_case_insensitive_lookup(self, conn):
        _seed(conn)
        normalize_entities_for_scan(conn, "scan-1")
        # Query with lowercase should still find "Billing" records
        ids = query_by_entity(conn, "product_area", "billing")
        assert len(ids) >= 3  # T001, T003, T004 all have Billing

    def test_empty_for_nonexistent(self, conn):
        _seed(conn)
        normalize_entities_for_scan(conn, "scan-1")
        ids = query_by_entity(conn, "payer", "Nonexistent Corp")
        assert ids == []


class TestGetEntityDistribution:
    def test_sorted_by_count(self, conn):
        _seed(conn)
        normalize_entities_for_scan(conn, "scan-1")
        dist = get_entity_distribution(conn, "payer")
        assert len(dist) > 0
        # Should be sorted descending
        counts = [c for _, c in dist]
        assert counts == sorted(counts, reverse=True)

    def test_reflects_normalization(self, conn):
        _seed(conn)
        normalize_entities_for_scan(conn, "scan-1")
        dist = get_entity_distribution(conn, "product_area")
        values = [v for v, _ in dist]
        # "billing" should have been normalized to "Billing"
        assert "billing" not in values
        assert "Billing" in values


class TestChatToolHandler:
    def test_returns_correct_structure(self, conn):
        _seed(conn)
        normalize_entities_for_scan(conn, "scan-1")
        result = handle_query_entities(
            conn, {"entity_type": "payer", "entity_value": "Centaur Benefits"}, {}
        )
        assert "ticket_ids" in result
        assert "total_matches" in result
        assert result["total_matches"] == 2

    def test_list_values_mode(self, conn):
        _seed(conn)
        normalize_entities_for_scan(conn, "scan-1")
        result = handle_query_entities(
            conn, {"entity_type": "product_area", "list_values": True}, {}
        )
        assert "values" in result
        assert "total_unique" in result
