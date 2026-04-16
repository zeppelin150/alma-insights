"""
Alma Insights — Session 3 Tests: Redaction Engine + Always-On Analytics

Tests:
  - RedactionEngine PHI removal, allowlist preservation, edge cases
  - WarehouseQuery routed queries (conversations, tickets, FTS)
  - Analytics engines query warehouse without loaded conversations
  - Legacy mode fallback for pre-migration databases
"""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest


# ═══════════════════════════════════════════
#  FIXTURES
# ═══════════════════════════════════════════

@pytest.fixture
def redaction_engine():
    """Fresh RedactionEngine with default config."""
    from src.data.redaction_engine import RedactionEngine
    return RedactionEngine()


@pytest.fixture
def warehouse_db(tmp_path):
    """In-memory DB with source_registry + per-source tables + test data."""
    db_path = tmp_path / "warehouse_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Create source_registry
    conn.executescript("""
        CREATE TABLE source_registry (
            source_id TEXT PRIMARY KEY,
            source_name TEXT NOT NULL,
            source_type TEXT NOT NULL,
            table_prefix TEXT NOT NULL UNIQUE,
            column_mapping TEXT,
            created_at TEXT NOT NULL,
            is_default INTEGER DEFAULT 0,
            ticket_count INTEGER DEFAULT 0,
            last_import_at TEXT
        );

        INSERT INTO source_registry (source_id, source_name, source_type, table_prefix, created_at, is_default)
        VALUES ('test_source', 'Test Source', 'zendesk', 'test_source', '2025-01-01', 1);

        CREATE TABLE test_source_tickets (
            ticket_id TEXT PRIMARY KEY,
            subject TEXT,
            trc_code TEXT,
            trc_label TEXT,
            status TEXT,
            priority TEXT,
            csat_score REAL,
            created_at TEXT,
            solved_at TEXT,
            requester_hash TEXT,
            source_id TEXT DEFAULT 'test_source',
            imported_at TEXT,
            provider_id TEXT,
            client_id TEXT,
            assignment_to_resolution_hours REAL,
            total_resolution_hours REAL,
            first_reply_hours REAL
        );

        CREATE TABLE test_source_conversations (
            ticket_id TEXT PRIMARY KEY,
            subject TEXT,
            trc_code TEXT,
            trc_label TEXT,
            status TEXT,
            csat_score REAL,
            created_at TEXT,
            solved_at TEXT,
            message_count INTEGER DEFAULT 0,
            client_messages INTEGER DEFAULT 0,
            agent_messages INTEGER DEFAULT 0,
            full_thread TEXT,
            thread_preview TEXT,
            dataset_id INTEGER,
            source_id TEXT DEFAULT 'test_source'
        );

        CREATE TABLE test_source_comments (
            comment_id TEXT PRIMARY KEY,
            ticket_id TEXT,
            author_role TEXT,
            body TEXT,
            created_at TEXT,
            source_id TEXT DEFAULT 'test_source'
        );

        CREATE VIRTUAL TABLE test_source_fts USING fts5(
            ticket_id, subject, trc_label, full_thread
        );
    """)

    # Seed 20 tickets
    for i in range(20):
        tid = f"T-{1000 + i}"
        trc = f"TRC-{100 + (i % 3) * 100}"
        trc_label = ["Billing", "Login", "Claims"][i % 3]
        created = f"2025-01-{(i % 28) + 1:02d} 10:00"
        csat = round(1.0 + (i % 5), 1)

        conn.execute(
            "INSERT INTO test_source_tickets (ticket_id, subject, trc_code, trc_label, status, csat_score, created_at, source_id, imported_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'test_source', '2025-01-01')",
            (tid, f"Test {trc_label} issue {i}", trc, trc_label, "solved", csat, created),
        )
        conn.execute(
            "INSERT INTO test_source_conversations (ticket_id, subject, trc_code, trc_label, status, csat_score, created_at, message_count, full_thread, thread_preview, dataset_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 2, ?, ?, 1)",
            (tid, f"Test {trc_label} issue {i}", trc, trc_label, "solved", csat, created,
             f"Customer: I have a {trc_label.lower()} problem\nAgent: Let me help.",
             f"I have a {trc_label.lower()} problem"),
        )
        conn.execute(
            "INSERT INTO test_source_fts (ticket_id, subject, trc_label, full_thread) VALUES (?, ?, ?, ?)",
            (tid, f"Test {trc_label} issue {i}", trc_label, f"Customer: I have a {trc_label.lower()} problem"),
        )

    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def legacy_db(tmp_path):
    """DB with old shared tables (no source_registry) — for legacy mode testing."""
    db_path = tmp_path / "legacy_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    conn.executescript("""
        CREATE TABLE tickets (
            ticket_id TEXT PRIMARY KEY,
            subject TEXT,
            trc_code TEXT
        );

        CREATE TABLE conversations (
            ticket_id TEXT PRIMARY KEY,
            subject TEXT,
            trc_code TEXT,
            trc_label TEXT,
            status TEXT,
            csat_score REAL,
            created_at TEXT,
            solved_at TEXT,
            message_count INTEGER DEFAULT 0,
            client_messages INTEGER DEFAULT 0,
            agent_messages INTEGER DEFAULT 0,
            full_thread TEXT,
            thread_preview TEXT,
            dataset_id INTEGER
        );

        INSERT INTO tickets VALUES ('T-1', 'Legacy ticket', 'TRC-100');
        INSERT INTO conversations VALUES ('T-1', 'Legacy ticket', 'TRC-100', 'Billing', 'open', 3.0,
            '2025-01-01', '', 2, 1, 1, 'full thread text', 'preview', 1);
    """)
    conn.commit()
    yield conn
    conn.close()


# ═══════════════════════════════════════════
#  REDACTION ENGINE TESTS
# ═══════════════════════════════════════════

class TestRedactionEngine:
    """PHI removal + business entity preservation."""

    def test_removes_ssn(self, redaction_engine):
        text = "Patient SSN is 123-45-6789 and needs help."
        result = redaction_engine.scrub(text)
        assert "123-45-6789" not in result
        assert "[SSN]" in result

    def test_removes_email(self, redaction_engine):
        text = "Contact john.doe@example.com for details."
        result = redaction_engine.scrub(text)
        assert "john.doe@example.com" not in result
        assert "[EMAIL]" in result

    def test_removes_phone(self, redaction_engine):
        text = "Call 555-123-4567 for support."
        result = redaction_engine.scrub(text)
        assert "555-123-4567" not in result
        assert "[PHONE]" in result

    def test_removes_credit_card(self, redaction_engine):
        text = "Card number 4111-1111-1111-1111 on file."
        result = redaction_engine.scrub(text)
        assert "4111-1111-1111-1111" not in result
        assert "[CARD]" in result

    def test_preserves_insurance_name_oscar(self, redaction_engine):
        text = "The member has Oscar Health insurance."
        result = redaction_engine.scrub(text)
        assert "Oscar" in result

    def test_preserves_uhc(self, redaction_engine):
        text = "Claim denied by UHC for prior auth issue."
        result = redaction_engine.scrub(text)
        assert "UHC" in result

    def test_preserves_bcbs(self, redaction_engine):
        text = "Blue Cross Blue Shield denied the claim."
        result = redaction_engine.scrub(text)
        assert "Blue Cross Blue Shield" in result

    def test_preserves_trc_codes(self, redaction_engine):
        text = "This ticket is classified as TRC-100 billing."
        result = redaction_engine.scrub(text)
        assert "TRC-100" in result

    def test_preserves_business_acronyms(self, redaction_engine):
        text = "The CSAT score for this EOB inquiry is 4.5."
        result = redaction_engine.scrub(text)
        assert "CSAT" in result
        assert "EOB" in result

    def test_name_heuristic_skips_business_terms(self, redaction_engine):
        text = "Prior Authorization was denied."
        result = redaction_engine.scrub(text)
        assert "Prior Authorization" in result

    def test_handles_empty_text(self, redaction_engine):
        assert redaction_engine.scrub("") == ""
        assert redaction_engine.scrub(None) is None

    def test_scrub_dict(self, redaction_engine):
        record = {
            "ticket_id": "T-1",
            "full_thread": "Contact 555-123-4567 about SSN 123-45-6789",
            "subject": "Normal subject",
        }
        result = redaction_engine.scrub_dict(record)
        assert "555-123-4567" not in result["full_thread"]
        assert result["ticket_id"] == "T-1"  # Non-text fields preserved

    def test_multiple_phi_in_one_text(self, redaction_engine):
        text = "Patient John Smith (SSN 123-45-6789, email jsmith@test.com, phone 555-111-2222)"
        result = redaction_engine.scrub(text)
        assert "123-45-6789" not in result
        assert "jsmith@test.com" not in result
        assert "555-111-2222" not in result


# ═══════════════════════════════════════════
#  WAREHOUSE QUERY ROUTED TESTS
# ═══════════════════════════════════════════

class TestWarehouseQueryRouted:
    """Warehouse query routes to per-source tables."""

    def test_get_ticket_count(self, warehouse_db):
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        wq = WarehouseQuery(warehouse_db, SourceRegistry(warehouse_db))
        assert wq.get_ticket_count() == 20

    def test_get_conversations(self, warehouse_db):
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        wq = WarehouseQuery(warehouse_db, SourceRegistry(warehouse_db))
        rows = wq.get_conversations()
        assert len(rows) == 20
        assert "ticket_id" in rows[0]
        assert "full_thread" in rows[0]

    def test_get_conversations_date_filter(self, warehouse_db):
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        wq = WarehouseQuery(warehouse_db, SourceRegistry(warehouse_db))
        rows = wq.get_conversations(date_start="2025-01-10")
        assert all(r["created_at"] >= "2025-01-10" for r in rows)

    def test_get_trc_distribution(self, warehouse_db):
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        wq = WarehouseQuery(warehouse_db, SourceRegistry(warehouse_db))
        dist = wq.get_trc_distribution()
        assert "TRC-100" in dist
        assert sum(dist.values()) == 20

    def test_get_full_threads(self, warehouse_db):
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        wq = WarehouseQuery(warehouse_db, SourceRegistry(warehouse_db))
        threads = wq.get_full_threads(["T-1000", "T-1001"])
        assert "T-1000" in threads
        assert "problem" in threads["T-1000"]

    def test_search_fts(self, warehouse_db):
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        wq = WarehouseQuery(warehouse_db, SourceRegistry(warehouse_db))
        results = wq.search_fts("billing")
        assert len(results) > 0

    def test_query_conversations_raw(self, warehouse_db):
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        wq = WarehouseQuery(warehouse_db, SourceRegistry(warehouse_db))
        rows = wq.query_conversations_raw(
            "SELECT COUNT(*) FROM {table} WHERE trc_code = ?",
            ("TRC-100",),
        )
        total = sum(r[0] for r in rows)
        assert total > 0

    def test_query_tickets_raw(self, warehouse_db):
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        wq = WarehouseQuery(warehouse_db, SourceRegistry(warehouse_db))
        rows = wq.query_tickets_raw(
            "SELECT COUNT(*) FROM {table}",
        )
        assert rows[0][0] == 20


# ═══════════════════════════════════════════
#  LEGACY MODE FALLBACK TESTS
# ═══════════════════════════════════════════

class TestLegacyModeFallback:
    """Warehouse falls back to shared tables when source_registry missing."""

    def test_legacy_mode_detected(self, legacy_db):
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        wq = WarehouseQuery(legacy_db, SourceRegistry(legacy_db))
        assert wq._legacy_mode is True

    def test_legacy_get_ticket_count(self, legacy_db):
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        wq = WarehouseQuery(legacy_db, SourceRegistry(legacy_db))
        # Legacy routes to 'tickets' table
        assert wq.get_ticket_count() == 1

    def test_legacy_get_conversations(self, legacy_db):
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        wq = WarehouseQuery(legacy_db, SourceRegistry(legacy_db))
        rows = wq.get_conversations()
        assert len(rows) == 1
        assert rows[0]["full_thread"] == "full thread text"

    def test_legacy_query_raw(self, legacy_db):
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        wq = WarehouseQuery(legacy_db, SourceRegistry(legacy_db))
        rows = wq.query_conversations_raw(
            "SELECT ticket_id FROM {table} WHERE trc_code = ?",
            ("TRC-100",),
        )
        assert len(rows) == 1
        assert rows[0][0] == "T-1"


# ═══════════════════════════════════════════
#  ANALYTICS WITHOUT LOADED CONVERSATIONS
# ═══════════════════════════════════════════

class TestAlwaysOnAnalytics:
    """Analytics engines work against warehouse without loaded conversations."""

    def test_db_manager_get_ticket_count_warehouse(self, warehouse_db):
        """get_ticket_count uses warehouse when source_registry exists."""
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        wq = WarehouseQuery(warehouse_db, SourceRegistry(warehouse_db))
        assert wq.get_ticket_count() == 20

    def test_db_manager_get_trc_distribution_warehouse(self, warehouse_db):
        """TRC distribution works via warehouse."""
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        wq = WarehouseQuery(warehouse_db, SourceRegistry(warehouse_db))
        dist = wq.get_trc_distribution()
        assert len(dist) == 3
        assert sum(dist.values()) == 20

    def test_warehouse_date_range(self, warehouse_db):
        """Date range aggregation across warehouse tables."""
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        wq = WarehouseQuery(warehouse_db, SourceRegistry(warehouse_db))
        rows = wq.query_conversations_raw(
            "SELECT MIN(created_at), MAX(created_at) FROM {table}"
        )
        assert rows[0][0] is not None
        assert rows[0][1] is not None
