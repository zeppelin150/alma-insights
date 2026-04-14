"""
Phase 1 regression tests — ticket_index_writer enrichment.

Asserts:
  - ensure_ticket_index_row creates a row populated with enrichment columns
  - upsert_ticket_index COALESCEs enrichment instead of clobbering with NULL
  - entity registries populate on first encounter
  - write_ticket_tags is idempotent and normalizes input
"""

from __future__ import annotations

import json

import pytest

from src.data.connection_factory import atomic, get_connection
from src.services.ticket_index_writer import (
    ensure_ticket_index_row,
    upsert_entity_registries,
    upsert_ticket_index,
    write_ticket_tags,
)


@pytest.fixture
def conn(empty_db):
    """Fully initialized DB via db_manager.initialize() + all migrations."""
    c = empty_db.conn
    yield c


class TestEnsureTicketIndexRow:
    def test_creates_row_with_enrichment(self, conn):
        meta = {
            "subject": "Portal broken",
            "trc_code": "AccessIssues",
            "created_at": "2026-03-01",
            "insurance_payer": "Thunderbird Insurance",
            "client_id": "c-000123",
            "provider_id": "p-0000999",
            "agent_id": "a-0042",
            "service_state": "CA",
            "channel": "email",
            "session_date": "2026-02-28",
            "dispute_amount_usd": 120.50,
            "tags": "incident_portal_outage_0301;another_tag",
        }
        with atomic(conn):
            ensure_ticket_index_row("T1", meta, conn)
        row = conn.execute(
            "SELECT insurance_payer, client_id, provider_id, agent_id, "
            "service_state, channel, session_date, dispute_amount_usd "
            "FROM ticket_index WHERE ticket_id = ?", ("T1",)
        ).fetchone()
        assert row is not None
        assert row["insurance_payer"] == "Thunderbird Insurance"
        assert row["client_id"] == "c-000123"
        assert row["provider_id"] == "p-0000999"
        assert row["agent_id"] == "a-0042"
        assert row["service_state"] == "CA"
        assert row["channel"] == "email"
        assert str(row["session_date"]).startswith("2026-02-28")
        assert abs(row["dispute_amount_usd"] - 120.50) < 1e-6

    def test_populates_entity_registries(self, conn):
        with atomic(conn):
            ensure_ticket_index_row("T2", {
                "subject": "x", "insurance_payer": "AcmeHealth",
                "client_id": "c-aaa", "provider_id": "p-bbb", "agent_id": "a-ccc",
            }, conn)
        assert conn.execute(
            "SELECT 1 FROM insurance_payers WHERE payer_id = ?", ("AcmeHealth",)
        ).fetchone() is not None
        assert conn.execute(
            "SELECT 1 FROM clients WHERE client_id = ?", ("c-aaa",)
        ).fetchone() is not None
        assert conn.execute(
            "SELECT 1 FROM providers WHERE provider_id = ?", ("p-bbb",)
        ).fetchone() is not None
        assert conn.execute(
            "SELECT 1 FROM agents WHERE agent_id = ?", ("a-ccc",)
        ).fetchone() is not None

    def test_inserts_tags(self, conn):
        with atomic(conn):
            ensure_ticket_index_row("T3", {
                "subject": "x",
                "tags": "TagA;tag_b;  TagA ;tag_c",
            }, conn)
        rows = conn.execute(
            "SELECT tag FROM ticket_tags WHERE ticket_id = 'T3' ORDER BY tag"
        ).fetchall()
        # normalized to lowercase, deduped
        tags = sorted(r[0] for r in rows)
        assert tags == ["tag_b", "tag_c", "taga"]

    def test_idempotent_on_reapply(self, conn):
        meta = {"subject": "x", "insurance_payer": "AcmeHealth"}
        with atomic(conn):
            ensure_ticket_index_row("T4", meta, conn)
        with atomic(conn):
            ensure_ticket_index_row("T4", meta, conn)
        count = conn.execute(
            "SELECT COUNT(*) FROM ticket_index WHERE ticket_id = 'T4'"
        ).fetchone()[0]
        assert count == 1


class TestEnrichmentCoalesce:
    def test_scan_upsert_does_not_clobber_enrichment(self, conn):
        # Step 1: ingestion-time enrichment
        with atomic(conn):
            ensure_ticket_index_row("T10", {
                "subject": "x", "trc_code": "Billing",
                "insurance_payer": "Thunderbird",
                "service_state": "NY",
            }, conn)
        # Step 2: scan-time upsert with NO enrichment in meta
        classification = {
            "friction_type": "Billing error",
            "sub_cluster": "copay_mismatch",
            "sub_cluster_confidence": 0.9,
        }
        with atomic(conn):
            upsert_ticket_index(
                ticket_id="T10",
                scan_id="scan_2026_04_14",
                classification=classification,
                ticket_meta={"trc_code": "Billing"},
                conn=conn,
            )
        row = conn.execute(
            "SELECT insurance_payer, service_state, friction_type, sub_pattern "
            "FROM ticket_index WHERE ticket_id = 'T10'"
        ).fetchone()
        # Enrichment survives (COALESCE)
        assert row["insurance_payer"] == "Thunderbird"
        assert row["service_state"] == "NY"
        # Classification columns now populated
        assert row["friction_type"] == "Billing error"
        assert row["sub_pattern"] == "copay_mismatch"

    def test_scan_upsert_enrichment_wins_when_given(self, conn):
        # ingestion sets state=NY
        with atomic(conn):
            ensure_ticket_index_row("T11", {
                "subject": "x", "service_state": "NY",
            }, conn)
        # scan meta overrides with TX
        with atomic(conn):
            upsert_ticket_index(
                ticket_id="T11",
                scan_id="s1",
                classification={},
                ticket_meta={"service_state": "TX"},
                conn=conn,
            )
        state = conn.execute(
            "SELECT service_state FROM ticket_index WHERE ticket_id = 'T11'"
        ).fetchone()[0]
        assert state == "TX"


class TestWriteTicketTags:
    def test_normalization_and_dedup(self, conn):
        # Need a ticket_index row first for FK
        with atomic(conn):
            ensure_ticket_index_row("TT1", {"subject": "x"}, conn)
            n = write_ticket_tags("TT1", "A;b; a ;c", conn)
        assert n == 3
        tags = sorted(r[0] for r in conn.execute(
            "SELECT tag FROM ticket_tags WHERE ticket_id = 'TT1'"
        ).fetchall())
        assert tags == ["a", "b", "c"]

    def test_accepts_json_array(self, conn):
        with atomic(conn):
            ensure_ticket_index_row("TT2", {"subject": "x"}, conn)
            write_ticket_tags("TT2", json.dumps(["X", "Y"]), conn)
        tags = sorted(r[0] for r in conn.execute(
            "SELECT tag FROM ticket_tags WHERE ticket_id = 'TT2'"
        ).fetchall())
        assert tags == ["x", "y"]

    def test_empty_input_noop(self, conn):
        with atomic(conn):
            ensure_ticket_index_row("TT3", {"subject": "x"}, conn)
            assert write_ticket_tags("TT3", None, conn) == 0
            assert write_ticket_tags("TT3", "", conn) == 0
            assert write_ticket_tags("TT3", [], conn) == 0

    def test_rejects_invalid_source(self, conn):
        with pytest.raises(ValueError, match="invalid tag source"):
            write_ticket_tags("TT4", ["a"], conn, source="bogus")

    def test_idempotent(self, conn):
        with atomic(conn):
            ensure_ticket_index_row("TT5", {"subject": "x"}, conn)
            write_ticket_tags("TT5", ["a", "b"], conn)
            write_ticket_tags("TT5", ["a", "b"], conn)
        count = conn.execute(
            "SELECT COUNT(*) FROM ticket_tags WHERE ticket_id = 'TT5'"
        ).fetchone()[0]
        assert count == 2


class TestEntityRegistries:
    def test_upsert_is_insert_or_ignore(self, conn):
        meta = {"insurance_payer": "Same Payer", "client_id": "c-1",
                "provider_id": "p-1", "agent_id": "a-1"}
        with atomic(conn):
            upsert_entity_registries("TX1", meta, conn)
            upsert_entity_registries("TX2", meta, conn)
        # Only one row per entity; first_seen_ticket_id sticks to TX1
        row = conn.execute(
            "SELECT first_seen_ticket_id FROM clients WHERE client_id = 'c-1'"
        ).fetchone()
        assert row[0] == "TX1"

    def test_blank_entity_fields_skipped(self, conn):
        with atomic(conn):
            upsert_entity_registries("TX3", {
                "insurance_payer": "   ",
                "client_id": None,
                "provider_id": "",
            }, conn)
        assert conn.execute("SELECT COUNT(*) FROM insurance_payers").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM providers").fetchone()[0] == 0
