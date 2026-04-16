"""
Session 6A Tests: Dual-Write Gap Fix
=====================================
Verifies that upsert methods write to both shared AND per-source tables,
the upsert_conversation source-column bug is fixed, per-source dedup works,
and WarehouseQuery exits legacy mode after dual-write populates per-source data.

Run: python -m pytest tests/test_s6a_dual_write.py -x -v
"""

import sqlite3
import pytest
from pathlib import Path


# ─── Helpers ───

def _create_shared_tables(conn):
    """Create the shared tables matching db_manager.py DDL."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tickets (
            ticket_id TEXT PRIMARY KEY, subject TEXT, trc_code TEXT, trc_label TEXT,
            status TEXT, priority TEXT, channel TEXT, csat_score REAL,
            created_at TEXT, updated_at TEXT, solved_at TEXT,
            requester_name TEXT, requester_email TEXT, assignee_name TEXT,
            group_name TEXT, tags TEXT, custom_fields TEXT,
            assignment_to_resolution_hours REAL, total_resolution_hours REAL,
            first_reply_hours REAL, requester_hash TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS comments (
            comment_id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL,
            author_name TEXT, author_role TEXT, body TEXT,
            is_public INTEGER DEFAULT 1, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS conversations (
            ticket_id TEXT PRIMARY KEY, subject TEXT, trc_code TEXT, trc_label TEXT,
            status TEXT, csat_score REAL, created_at TEXT, solved_at TEXT,
            message_count INTEGER, client_messages INTEGER, agent_messages INTEGER,
            full_thread TEXT, thread_preview TEXT, dataset_id INTEGER DEFAULT 0
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS conversations_fts USING fts5(
            ticket_id, subject, trc_label, full_thread
        );
    """)


def _create_source_tables(conn, prefix):
    """Create per-source tables matching schema_builder.py."""
    from src.data.schema_builder import create_source_tables
    create_source_tables(conn, prefix)


def _create_source_registry(conn, source_id="zen_default", prefix="zen_default",
                             name="Zendesk Default"):
    """Create source_registry table with one entry."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS source_registry (
            source_id TEXT PRIMARY KEY, source_name TEXT, source_type TEXT,
            table_prefix TEXT UNIQUE, column_mapping TEXT DEFAULT '{}',
            created_at TEXT, is_default INTEGER DEFAULT 0, ticket_count INTEGER DEFAULT 0,
            last_import_at TEXT
        );
    """)
    conn.execute(
        "INSERT OR IGNORE INTO source_registry (source_id, source_name, source_type, table_prefix, is_default) "
        "VALUES (?, ?, 'zendesk', ?, 1)",
        (source_id, name, prefix),
    )
    conn.commit()


def _sample_ticket(tid="T-100", trc="AUTH-01", subject="Test ticket"):
    return {
        "ticket_id": tid, "subject": subject, "trc_code": trc, "trc_label": trc,
        "status": "open", "priority": "", "channel": "", "csat_score": 4.0,
        "created_at": "2026-01-15", "updated_at": "", "solved_at": "",
        "requester_name": "", "requester_email": "", "assignee_name": "",
        "group_name": "", "tags": [], "custom_fields": {},
        "assignment_to_resolution_hours": None, "total_resolution_hours": None,
        "first_reply_hours": None,
    }


def _sample_comment(tid="T-100", cid="T-100-0"):
    return {
        "comment_id": cid, "ticket_id": tid, "author_name": "Agent",
        "author_role": "agent", "body": "Hello, how can I help?",
        "is_public": True, "created_at": "2026-01-15 10:00",
    }


def _sample_conversation(tid="T-100", trc="AUTH-01"):
    return {
        "ticket_id": tid, "subject": "Test ticket", "trc_code": trc,
        "trc_label": trc, "status": "open", "csat_score": 4.0,
        "created_at": "2026-01-15", "solved_at": "", "message_count": 1,
        "client_messages": 1, "agent_messages": 0,
        "full_thread": "Hello world", "thread_preview": "Hello world",
        "dataset_id": 0,
    }


# ─── Tests: Upsert Dual-Write ───

class TestUpsertDualWrite:

    @pytest.fixture
    def db_with_source(self, tmp_path):
        from src.data.db_manager import DatabaseManager
        db_path = tmp_path / "dual_write.db"
        db = DatabaseManager(db_path)
        db.initialize()
        _create_source_tables(db.conn, "zen_default")
        _create_source_registry(db.conn)
        yield db
        db.close()

    def test_upsert_ticket_shared_only(self, db_with_source):
        """No table_prefix → shared table only (backward compat)."""
        db = db_with_source
        db.upsert_ticket(_sample_ticket())
        db.commit()

        shared = db.conn.execute("SELECT ticket_id FROM tickets").fetchall()
        assert len(shared) == 1
        assert shared[0][0] == "T-100"

        per_source = db.conn.execute("SELECT ticket_id FROM [zen_default_tickets]").fetchall()
        assert len(per_source) == 0

    def test_upsert_ticket_dual_write(self, db_with_source):
        """table_prefix → both shared + per-source populated."""
        db = db_with_source
        db.upsert_ticket(_sample_ticket(), table_prefix="zen_default")
        db.commit()

        shared = db.conn.execute("SELECT ticket_id FROM tickets").fetchall()
        assert len(shared) == 1

        per_source = db.conn.execute("SELECT ticket_id FROM [zen_default_tickets]").fetchall()
        assert len(per_source) == 1
        assert per_source[0][0] == "T-100"

    def test_upsert_ticket_per_source_has_source_id(self, db_with_source):
        """Per-source row has source_id = table_prefix."""
        db = db_with_source
        db.upsert_ticket(_sample_ticket(), table_prefix="zen_default")
        db.commit()

        row = db.conn.execute(
            "SELECT source_id, imported_at FROM [zen_default_tickets] WHERE ticket_id = 'T-100'"
        ).fetchone()
        assert row[0] == "zen_default"
        assert row[1] is not None  # imported_at should be set

    def test_upsert_comment_dual_write(self, db_with_source):
        """Both tables get comment row."""
        db = db_with_source
        db.upsert_ticket(_sample_ticket(), table_prefix="zen_default")
        db.upsert_comment(_sample_comment(), table_prefix="zen_default")
        db.commit()

        shared = db.conn.execute("SELECT comment_id FROM comments").fetchall()
        assert len(shared) == 1

        per_source = db.conn.execute("SELECT comment_id, source_id FROM [zen_default_comments]").fetchall()
        assert len(per_source) == 1
        assert per_source[0][1] == "zen_default"

    def test_upsert_conversation_bug_fix(self, db_with_source):
        """dataset_id column used correctly (not the old 'source' column)."""
        db = db_with_source
        db.upsert_ticket(_sample_ticket())
        conv = _sample_conversation()
        conv["dataset_id"] = 42
        db.upsert_conversation(conv)
        db.commit()

        row = db.conn.execute(
            "SELECT dataset_id FROM conversations WHERE ticket_id = 'T-100'"
        ).fetchone()
        assert row[0] == 42

    def test_upsert_conversation_dual_write(self, db_with_source):
        """Both tables get conversation row."""
        db = db_with_source
        db.upsert_ticket(_sample_ticket(), table_prefix="zen_default")
        db.upsert_conversation(_sample_conversation(), table_prefix="zen_default")
        db.commit()

        shared = db.conn.execute("SELECT ticket_id FROM conversations").fetchall()
        assert len(shared) == 1

        per_source = db.conn.execute(
            "SELECT ticket_id, source_id FROM [zen_default_conversations]"
        ).fetchall()
        assert len(per_source) == 1
        assert per_source[0][1] == "zen_default"


# ─── Tests: Per-Source Dedup ───

class TestPerSourceDedup:

    @pytest.fixture
    def conn(self, tmp_path):
        db_path = tmp_path / "dedup.db"
        conn = sqlite3.connect(str(db_path))
        _create_shared_tables(conn)
        _create_source_tables(conn, "zen_default")
        conn.execute("INSERT INTO tickets (ticket_id) VALUES ('existing-1')")
        conn.execute("INSERT INTO [zen_default_tickets] (ticket_id, source_id) VALUES ('src-only-1', 'zen_default')")
        conn.commit()
        yield conn
        conn.close()

    def test_dedup_queries_shared_by_default(self, conn):
        from src.data.import_tracker import get_existing_ticket_ids
        ids = get_existing_ticket_ids(conn)
        assert "existing-1" in ids
        assert "src-only-1" not in ids  # only in per-source, not shared

    def test_dedup_queries_per_source_table(self, conn):
        from src.data.import_tracker import get_existing_ticket_ids
        ids = get_existing_ticket_ids(conn, table_prefix="zen_default")
        assert "src-only-1" in ids

    def test_dedup_fallback_on_missing_table(self, conn):
        from src.data.import_tracker import get_existing_ticket_ids
        ids = get_existing_ticket_ids(conn, table_prefix="nonexistent_source")
        # Falls back to shared table
        assert "existing-1" in ids


# ─── Tests: FTS Population ───

class TestFtsPopulation:

    @pytest.fixture
    def db_with_source(self, tmp_path):
        from src.data.db_manager import DatabaseManager
        db_path = tmp_path / "fts_test.db"
        db = DatabaseManager(db_path)
        db.initialize()
        _create_source_tables(db.conn, "zen_default")
        _create_source_registry(db.conn)
        yield db
        db.close()

    def test_per_source_fts_populated(self, db_with_source):
        """Per-source FTS table searchable after rebuild."""
        db = db_with_source
        db.upsert_ticket(_sample_ticket(), table_prefix="zen_default")
        db.upsert_conversation(_sample_conversation(), table_prefix="zen_default")
        db.commit()
        db.rebuild_source_fts("zen_default")

        results = db.conn.execute(
            "SELECT ticket_id FROM [zen_default_fts] WHERE [zen_default_fts] MATCH 'Hello'"
        ).fetchall()
        assert len(results) == 1
        assert results[0][0] == "T-100"

    def test_shared_fts_still_works(self, db_with_source):
        """Shared FTS index is unaffected."""
        db = db_with_source
        db.upsert_ticket(_sample_ticket())
        db.upsert_conversation(_sample_conversation())
        db.commit()
        db.rebuild_fts_index()

        results = db.conn.execute(
            "SELECT ticket_id FROM conversations_fts WHERE conversations_fts MATCH 'Hello'"
        ).fetchall()
        assert len(results) == 1


# ─── Tests: Warehouse Legacy Mode Exit ───

class TestWarehouseExitsLegacy:

    @pytest.fixture
    def db_with_dual_data(self, tmp_path):
        """Use zendesk_default prefix — matches what db.initialize() + migration 012 create."""
        from src.data.db_manager import DatabaseManager
        db_path = tmp_path / "legacy_exit.db"
        db = DatabaseManager(db_path)
        db.initialize()
        # db.initialize() runs migration 011 (source_registry with zendesk_default)
        # and migration 012 (creates zendesk_default_* tables).
        # Dual-write a ticket into the existing zendesk_default per-source tables.
        db.upsert_ticket(_sample_ticket(), table_prefix="zendesk_default")
        db.upsert_conversation(_sample_conversation(), table_prefix="zendesk_default")
        db.commit()
        yield db
        db.close()

    def test_legacy_mode_off_after_dual_write(self, db_with_dual_data):
        """WarehouseQuery exits legacy mode when per-source tables have data."""
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery

        db = db_with_dual_data
        reg = SourceRegistry(db.conn)
        wq = WarehouseQuery(db.conn, reg)
        assert not wq._legacy_mode, "Should be in per-source mode after dual-write"

        # Query returns data from per-source table
        count = wq.get_ticket_count()
        assert count >= 1
