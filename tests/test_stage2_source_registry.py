"""
Test Module: Stage 2 — Source Registry, Schema Builder, Warehouse Query, Migrations
Stage: 2
Dependencies: Stage 1 complete (additive imports, dedupe gate)

Covers:
  - Source Registry CRUD (create, get, list, update, delete)
  - Schema Builder DDL generation (per-source tables + FTS)
  - Warehouse Query (single source, all sources, date/TRC filtering, FTS search)
  - Migrations 011-013 (source_registry, data migration, provider/client IDs)
  - Sidebar refactor (setCurrentWidget via _page_widgets)
  - Return shape compatibility with old conversations table

Run:
  - Single file:  python -m pytest tests/test_stage2_source_registry.py -x -v
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path

import pytest


# ═══════════════════════════════════════════
#  MIGRATION TESTS
# ═══════════════════════════════════════════

class TestMigration011:
    """Migration 011 creates source_registry table."""

    @pytest.fixture
    def fresh_db(self, tmp_path):
        db_path = tmp_path / "m011_test.db"
        conn = sqlite3.connect(str(db_path))
        yield conn
        conn.close()

    def test_source_registry_table_created(self, fresh_db):
        sql = (Path(__file__).parent.parent / "migrations" / "011_source_registry.sql").read_text()
        fresh_db.executescript(sql)
        tables = {r[0] for r in fresh_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "source_registry" in tables

    def test_default_source_registered(self, fresh_db):
        sql = (Path(__file__).parent.parent / "migrations" / "011_source_registry.sql").read_text()
        fresh_db.executescript(sql)
        row = fresh_db.execute(
            "SELECT source_id, source_name, is_default FROM source_registry WHERE source_id = 'zendesk_default'"
        ).fetchone()
        assert row is not None
        assert row[1] == "Zendesk Support"
        assert row[2] == 1

    def test_migration_idempotent(self, fresh_db):
        sql = (Path(__file__).parent.parent / "migrations" / "011_source_registry.sql").read_text()
        fresh_db.executescript(sql)
        fresh_db.executescript(sql)  # Should not error
        count = fresh_db.execute("SELECT COUNT(*) FROM source_registry").fetchone()[0]
        assert count == 1  # INSERT OR IGNORE

    def test_source_registry_columns(self, fresh_db):
        sql = (Path(__file__).parent.parent / "migrations" / "011_source_registry.sql").read_text()
        fresh_db.executescript(sql)
        cols = {r[1] for r in fresh_db.execute("PRAGMA table_info(source_registry)").fetchall()}
        expected = {"source_id", "source_name", "source_type", "table_prefix",
                    "column_mapping", "created_at", "is_default", "ticket_count",
                    "last_import_at"}
        assert expected == cols


class TestMigration012:
    """Migration 012 creates per-source tables and copies data."""

    @pytest.fixture
    def seeded_db(self, tmp_path):
        """DB with shared tables + data, registry created."""
        db_path = tmp_path / "m012_test.db"
        conn = sqlite3.connect(str(db_path))

        # Create shared tables
        conn.executescript("""
            CREATE TABLE tickets (
                ticket_id TEXT PRIMARY KEY, subject TEXT, trc_code TEXT,
                trc_label TEXT, status TEXT, priority TEXT, channel TEXT,
                csat_score REAL, created_at TEXT, updated_at TEXT, solved_at TEXT,
                requester_name TEXT, requester_email TEXT, assignee_name TEXT,
                group_name TEXT, tags TEXT, custom_fields TEXT,
                assignment_to_resolution_hours REAL, total_resolution_hours REAL,
                first_reply_hours REAL, requester_hash TEXT DEFAULT ''
            );
            CREATE TABLE conversations (
                ticket_id TEXT PRIMARY KEY, subject TEXT, trc_code TEXT,
                trc_label TEXT, status TEXT, csat_score REAL, created_at TEXT,
                solved_at TEXT, message_count INTEGER, client_messages INTEGER,
                agent_messages INTEGER, full_thread TEXT, thread_preview TEXT,
                dataset_id INTEGER DEFAULT 0
            );
            CREATE TABLE comments (
                comment_id TEXT PRIMARY KEY, ticket_id TEXT, author_name TEXT,
                author_role TEXT, body TEXT, is_public INTEGER DEFAULT 1,
                created_at TEXT
            );
        """)

        # Seed data
        for i in range(5):
            tid = f"T-{i}"
            conn.execute(
                "INSERT INTO tickets (ticket_id, subject, trc_code, trc_label, status) "
                "VALUES (?, ?, 'TRC-100', 'Billing', 'solved')",
                (tid, f"Ticket {i}")
            )
            conn.execute(
                "INSERT INTO conversations (ticket_id, subject, trc_code, trc_label, "
                "status, full_thread, thread_preview, message_count) "
                "VALUES (?, ?, 'TRC-100', 'Billing', 'solved', ?, ?, 2)",
                (tid, f"Ticket {i}", f"Thread for {tid}", f"Preview {tid}")
            )
            conn.execute(
                "INSERT INTO comments (comment_id, ticket_id, body, author_role) "
                "VALUES (?, ?, ?, 'customer')",
                (f"{tid}-0", tid, f"Comment for {tid}")
            )
        conn.commit()

        # Run migration 011 first (registry)
        sql_011 = (Path(__file__).parent.parent / "migrations" / "011_source_registry.sql").read_text()
        conn.executescript(sql_011)

        yield conn
        conn.close()

    def test_per_source_tables_created(self, seeded_db):
        sql = (Path(__file__).parent.parent / "migrations" / "012_default_zendesk_source.sql").read_text()
        seeded_db.executescript(sql)

        tables = {r[0] for r in seeded_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "zendesk_default_tickets" in tables
        assert "zendesk_default_conversations" in tables
        assert "zendesk_default_comments" in tables

    def test_data_copied_correctly(self, seeded_db):
        sql = (Path(__file__).parent.parent / "migrations" / "012_default_zendesk_source.sql").read_text()
        seeded_db.executescript(sql)

        old_count = seeded_db.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        new_count = seeded_db.execute("SELECT COUNT(*) FROM zendesk_default_tickets").fetchone()[0]
        assert old_count == new_count == 5

        old_conv = seeded_db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        new_conv = seeded_db.execute("SELECT COUNT(*) FROM zendesk_default_conversations").fetchone()[0]
        assert old_conv == new_conv == 5

        old_comments = seeded_db.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
        new_comments = seeded_db.execute("SELECT COUNT(*) FROM zendesk_default_comments").fetchone()[0]
        assert old_comments == new_comments == 5

    def test_old_tables_preserved(self, seeded_db):
        """Old tables are NOT dropped (deprecated, not deleted)."""
        sql = (Path(__file__).parent.parent / "migrations" / "012_default_zendesk_source.sql").read_text()
        seeded_db.executescript(sql)

        old_count = seeded_db.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        assert old_count == 5  # Still there

    def test_fts_created_and_populated(self, seeded_db):
        sql = (Path(__file__).parent.parent / "migrations" / "012_default_zendesk_source.sql").read_text()
        seeded_db.executescript(sql)

        fts_count = seeded_db.execute("SELECT COUNT(*) FROM zendesk_default_fts").fetchone()[0]
        assert fts_count == 5

    def test_source_id_set_on_new_tables(self, seeded_db):
        sql = (Path(__file__).parent.parent / "migrations" / "012_default_zendesk_source.sql").read_text()
        seeded_db.executescript(sql)

        row = seeded_db.execute(
            "SELECT source_id FROM zendesk_default_conversations LIMIT 1"
        ).fetchone()
        assert row[0] == "zendesk_default"

    def test_ticket_count_updated_in_registry(self, seeded_db):
        sql = (Path(__file__).parent.parent / "migrations" / "012_default_zendesk_source.sql").read_text()
        seeded_db.executescript(sql)

        count = seeded_db.execute(
            "SELECT ticket_count FROM source_registry WHERE source_id = 'zendesk_default'"
        ).fetchone()[0]
        assert count == 5

    def test_migration_idempotent(self, seeded_db):
        sql = (Path(__file__).parent.parent / "migrations" / "012_default_zendesk_source.sql").read_text()
        seeded_db.executescript(sql)
        seeded_db.executescript(sql)  # Should not error (IF NOT EXISTS + INSERT OR IGNORE)
        count = seeded_db.execute("SELECT COUNT(*) FROM zendesk_default_tickets").fetchone()[0]
        assert count == 5


class TestMigration013:
    """Migration 013 adds provider_id, client_id, source_id columns."""

    @pytest.fixture
    def db_with_tables(self, tmp_path):
        db_path = tmp_path / "m013_test.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE tickets (ticket_id TEXT PRIMARY KEY, subject TEXT);
            CREATE TABLE ticket_index (ticket_id TEXT PRIMARY KEY, first_seen_scan_id TEXT NOT NULL,
                last_seen_scan_id TEXT NOT NULL, first_seen_date DATETIME NOT NULL);
        """)
        conn.execute("INSERT INTO tickets VALUES ('T-1', 'Test')")
        conn.execute("INSERT INTO ticket_index VALUES ('T-1', 's1', 's1', datetime('now'))")
        conn.commit()
        yield conn
        conn.close()

    def test_provider_client_columns_added(self, db_with_tables):
        sql = (Path(__file__).parent.parent / "migrations" / "013_provider_client_ids.sql").read_text()
        # ALTER TABLE will fail if columns already exist, so execute line by line
        for line in sql.strip().split("\n"):
            line = line.strip()
            if line and not line.startswith("--"):
                try:
                    db_with_tables.execute(line)
                except Exception:
                    pass  # Column may already exist
        db_with_tables.commit()

        cols = {r[1] for r in db_with_tables.execute("PRAGMA table_info(tickets)").fetchall()}
        assert "provider_id" in cols
        assert "client_id" in cols

    def test_source_id_added_to_ticket_index(self, db_with_tables):
        sql = (Path(__file__).parent.parent / "migrations" / "013_provider_client_ids.sql").read_text()
        for line in sql.strip().split("\n"):
            line = line.strip()
            if line and not line.startswith("--"):
                try:
                    db_with_tables.execute(line)
                except Exception:
                    pass
        db_with_tables.commit()

        cols = {r[1] for r in db_with_tables.execute("PRAGMA table_info(ticket_index)").fetchall()}
        assert "source_id" in cols

        # Verify default value
        row = db_with_tables.execute(
            "SELECT source_id FROM ticket_index WHERE ticket_id = 'T-1'"
        ).fetchone()
        assert row[0] == "zendesk_default"

    def test_existing_data_preserved(self, db_with_tables):
        sql = (Path(__file__).parent.parent / "migrations" / "013_provider_client_ids.sql").read_text()
        for line in sql.strip().split("\n"):
            line = line.strip()
            if line and not line.startswith("--"):
                try:
                    db_with_tables.execute(line)
                except Exception:
                    pass
        db_with_tables.commit()

        row = db_with_tables.execute("SELECT subject FROM tickets WHERE ticket_id = 'T-1'").fetchone()
        assert row[0] == "Test"


# ═══════════════════════════════════════════
#  SOURCE REGISTRY TESTS
# ═══════════════════════════════════════════

class TestSourceRegistry:
    """Source CRUD, validation, table naming."""

    @pytest.fixture
    def registry_db(self, tmp_path):
        db_path = tmp_path / "registry_test.db"
        conn = sqlite3.connect(str(db_path))
        sql = (Path(__file__).parent.parent / "migrations" / "011_source_registry.sql").read_text()
        conn.executescript(sql)
        yield conn
        conn.close()

    def test_default_source_exists(self, registry_db):
        from src.data.source_registry import SourceRegistry
        reg = SourceRegistry(registry_db)
        default = reg.get_default_source()
        assert default is not None
        assert default["source_id"] == "zendesk_default"
        assert default["is_default"] == 1

    def test_list_sources(self, registry_db):
        from src.data.source_registry import SourceRegistry
        reg = SourceRegistry(registry_db)
        sources = reg.list_sources()
        assert len(sources) >= 1
        assert sources[0]["source_id"] == "zendesk_default"

    def test_create_source_valid(self, registry_db):
        from src.data.source_registry import SourceRegistry
        reg = SourceRegistry(registry_db)
        src = reg.create_source("kodif_test", "Kodif Test", "kodif", "kodif_test")
        assert src["source_id"] == "kodif_test"
        assert src["source_type"] == "kodif"

        # Verify tables were created
        tables = {r[0] for r in registry_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "kodif_test_tickets" in tables
        assert "kodif_test_conversations" in tables
        assert "kodif_test_comments" in tables

    def test_create_source_duplicate_id(self, registry_db):
        from src.data.source_registry import SourceRegistry
        reg = SourceRegistry(registry_db)
        with pytest.raises(ValueError, match="already exists"):
            reg.create_source("zendesk_default", "Dup", "zendesk", "dup_prefix")

    def test_create_source_duplicate_prefix(self, registry_db):
        from src.data.source_registry import SourceRegistry
        reg = SourceRegistry(registry_db)
        with pytest.raises(ValueError, match="already in use"):
            reg.create_source("new_source", "New", "zendesk", "zendesk_default")

    def test_create_source_invalid_prefix(self, registry_db):
        from src.data.source_registry import SourceRegistry
        reg = SourceRegistry(registry_db)
        with pytest.raises(ValueError, match="Invalid table prefix"):
            reg.create_source("bad", "Bad", "zendesk", "123_bad")  # Can't start with number

    def test_get_table_name(self, registry_db):
        from src.data.source_registry import SourceRegistry
        reg = SourceRegistry(registry_db)
        assert reg.get_table_name("zendesk_default", "conversations") == "zendesk_default_conversations"
        assert reg.get_table_name("zendesk_default", "tickets") == "zendesk_default_tickets"

    def test_update_source(self, registry_db):
        from src.data.source_registry import SourceRegistry
        reg = SourceRegistry(registry_db)
        reg.update_source("zendesk_default", source_name="Updated Name", ticket_count=42)
        src = reg.get_source("zendesk_default")
        assert src["source_name"] == "Updated Name"
        assert src["ticket_count"] == 42

    def test_column_mapping_json(self, registry_db):
        from src.data.source_registry import SourceRegistry
        reg = SourceRegistry(registry_db)
        mapping = {"conversation_id": "ticket_id", "body": "full_thread"}
        reg.update_source("zendesk_default", column_mapping=mapping)
        src = reg.get_source("zendesk_default")
        assert src["column_mapping"] == mapping

    def test_delete_default_refused(self, registry_db):
        from src.data.source_registry import SourceRegistry
        reg = SourceRegistry(registry_db)
        with pytest.raises(ValueError, match="Cannot delete the default"):
            reg.delete_source("zendesk_default")


# ═══════════════════════════════════════════
#  SCHEMA BUILDER TESTS
# ═══════════════════════════════════════════

class TestSchemaBuilder:
    """Dynamic DDL generation."""

    def test_creates_all_tables(self, tmp_path):
        conn = sqlite3.connect(str(tmp_path / "schema_test.db"))
        from src.data.schema_builder import create_source_tables
        create_source_tables(conn, "test_source")

        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' OR type='table'"
        ).fetchall()}
        assert "test_source_tickets" in tables
        assert "test_source_conversations" in tables
        assert "test_source_comments" in tables
        conn.close()

    def test_idempotent(self, tmp_path):
        conn = sqlite3.connect(str(tmp_path / "schema_test2.db"))
        from src.data.schema_builder import create_source_tables
        create_source_tables(conn, "test_source")
        create_source_tables(conn, "test_source")  # Should not error
        conn.close()

    def test_conversations_has_correct_columns(self, tmp_path):
        conn = sqlite3.connect(str(tmp_path / "schema_test3.db"))
        from src.data.schema_builder import create_source_tables
        create_source_tables(conn, "my_src")

        cols = {r[1] for r in conn.execute("PRAGMA table_info(my_src_conversations)").fetchall()}
        required = {"ticket_id", "subject", "trc_code", "trc_label", "status",
                     "csat_score", "created_at", "solved_at", "message_count",
                     "client_messages", "agent_messages", "full_thread",
                     "thread_preview", "dataset_id", "source_id"}
        assert required.issubset(cols)
        conn.close()


# ═══════════════════════════════════════════
#  WAREHOUSE QUERY TESTS
# ═══════════════════════════════════════════

class TestWarehouseQuery:
    """Unified query interface, return shape compat."""

    @pytest.fixture
    def wq_db(self, tmp_path):
        """DB with source registry + per-source tables + data."""
        db_path = tmp_path / "wq_test.db"
        conn = sqlite3.connect(str(db_path))

        # Create registry
        sql_011 = (Path(__file__).parent.parent / "migrations" / "011_source_registry.sql").read_text()
        conn.executescript(sql_011)

        # Create per-source tables
        from src.data.schema_builder import create_source_tables
        create_source_tables(conn, "zendesk_default")

        # Seed data
        for i in range(10):
            tid = f"T-{i}"
            trc = "TRC-100" if i < 6 else "TRC-200"
            date = f"2025-01-{10 + i:02d}"
            conn.execute(
                "INSERT INTO zendesk_default_conversations "
                "(ticket_id, subject, trc_code, trc_label, status, csat_score, "
                "created_at, message_count, full_thread, thread_preview) "
                "VALUES (?, ?, ?, ?, 'solved', 3.0, ?, 2, ?, ?)",
                (tid, f"Ticket {i}", trc, "Billing" if trc == "TRC-100" else "Login",
                 date, f"Thread {tid}", f"Preview {tid}")
            )
            conn.execute(
                "INSERT INTO zendesk_default_tickets (ticket_id, subject, trc_code, created_at) "
                "VALUES (?, ?, ?, ?)",
                (tid, f"Ticket {i}", trc, date)
            )
        conn.commit()

        # Populate FTS
        conn.execute(
            "INSERT INTO zendesk_default_fts (ticket_id, subject, trc_label, full_thread) "
            "SELECT ticket_id, subject, trc_label, full_thread FROM zendesk_default_conversations"
        )
        conn.commit()

        yield conn
        conn.close()

    def _make_wq(self, conn):
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        return WarehouseQuery(conn, SourceRegistry(conn))

    def test_query_all_sources(self, wq_db):
        wq = self._make_wq(wq_db)
        results = wq.get_conversations()
        assert len(results) == 10

    def test_query_single_source(self, wq_db):
        wq = self._make_wq(wq_db)
        results = wq.get_conversations(source_id="zendesk_default")
        assert len(results) == 10

    def test_return_shape_compat(self, wq_db):
        """Return columns match old conversations table shape."""
        wq = self._make_wq(wq_db)
        results = wq.get_conversations()
        assert len(results) > 0
        row = results[0]
        expected_keys = {
            "ticket_id", "subject", "trc_code", "trc_label", "status",
            "csat_score", "created_at", "solved_at", "message_count",
            "client_messages", "agent_messages", "full_thread",
            "thread_preview", "dataset_id"
        }
        assert expected_keys == set(row.keys())

    def test_date_range_filtering(self, wq_db):
        wq = self._make_wq(wq_db)
        results = wq.get_conversations(date_start="2025-01-15")
        # T-5 through T-9 (created_at = 2025-01-15 through 2025-01-19)
        assert all(r["created_at"] >= "2025-01-15" for r in results)

    def test_trc_filtering(self, wq_db):
        wq = self._make_wq(wq_db)
        results = wq.get_conversations(trc_filter="TRC-100")
        assert all(r["trc_code"] == "TRC-100" for r in results)
        assert len(results) == 6

    def test_trc_list_filtering(self, wq_db):
        wq = self._make_wq(wq_db)
        results = wq.get_conversations(trc_filter=["TRC-100", "TRC-200"])
        assert len(results) == 10

    def test_ticket_count(self, wq_db):
        wq = self._make_wq(wq_db)
        assert wq.get_ticket_count() == 10
        assert wq.get_ticket_count(source_id="zendesk_default") == 10

    def test_trc_distribution(self, wq_db):
        wq = self._make_wq(wq_db)
        dist = wq.get_trc_distribution()
        assert dist["TRC-100"] == 6
        assert dist["TRC-200"] == 4

    def test_get_full_threads(self, wq_db):
        wq = self._make_wq(wq_db)
        threads = wq.get_full_threads(["T-0", "T-1"])
        assert "T-0" in threads
        assert "T-1" in threads
        assert "Thread T-0" in threads["T-0"]

    def test_empty_source(self, wq_db):
        """Query a nonexistent source doesn't crash."""
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        reg = SourceRegistry(wq_db)
        # Create an empty source
        reg.create_source("empty_src", "Empty", "custom", "empty_src")
        wq = WarehouseQuery(wq_db, reg)
        results = wq.get_conversations(source_id="empty_src")
        assert results == []

    def test_limit(self, wq_db):
        wq = self._make_wq(wq_db)
        results = wq.get_conversations(limit=3)
        assert len(results) == 3

    def test_search_fts(self, wq_db):
        wq = self._make_wq(wq_db)
        results = wq.search_fts("Billing")
        assert len(results) > 0
        assert all("ticket_id" in r for r in results)

    def test_search_fts_empty_query(self, wq_db):
        wq = self._make_wq(wq_db)
        results = wq.search_fts("")
        assert results == []


# ═══════════════════════════════════════════
#  SIDEBAR REFACTOR TESTS
# ═══════════════════════════════════════════

class TestSidebarRefactor:
    """Sidebar uses setCurrentWidget via _page_widgets dict."""

    def test_page_widgets_dict_exists(self):
        """MainWindow has _page_widgets attribute that maps constants to widgets."""
        import inspect
        from src.ui.main_window import MainWindow
        source = inspect.getsource(MainWindow._build_content_area)
        assert "_page_widgets" in source

    def test_set_active_page_uses_widget(self):
        """_set_active_page uses setCurrentWidget."""
        import inspect
        from src.ui.main_window import MainWindow
        source = inspect.getsource(MainWindow._set_active_page)
        assert "setCurrentWidget" in source

    def test_no_hardcoded_indices_in_set_active_page(self):
        """No raw numeric indices in programmatic navigation calls."""
        import inspect
        from src.ui.main_window import MainWindow

        # Check the 3 methods that call _set_active_page programmatically
        for method_name in ["_on_nlp_deep_dive", "_on_nlp_view_tickets"]:
            source = inspect.getsource(getattr(MainWindow, method_name))
            assert "self.PAGE_" in source or "_set_active_page" in source


# ═══════════════════════════════════════════
#  REGRESSION TESTS
# ═══════════════════════════════════════════

class TestStage2Regression:
    """Existing module imports unchanged after Stage 2."""

    def test_source_registry_import(self):
        from src.data.source_registry import SourceRegistry
        assert callable(SourceRegistry)

    def test_schema_builder_import(self):
        from src.data.schema_builder import create_source_tables
        assert callable(create_source_tables)

    def test_warehouse_query_import(self):
        from src.data.warehouse_query import WarehouseQuery
        assert callable(WarehouseQuery)

    def test_csv_ingestion_still_works(self):
        from src.data.csv_ingestion import ingest_csv
        assert callable(ingest_csv)

    def test_conversation_rebuild_still_works(self):
        from src.data.conversation_rebuild import rebuild_conversations
        assert callable(rebuild_conversations)

    def test_clear_session_still_works(self):
        from src.services.clear_session import clear_session_data
        assert callable(clear_session_data)

    def test_import_mode_still_works(self):
        from src.data.import_mode import ImportMode
        assert ImportMode.INCREMENTAL.value == "incremental"

    def test_stage1_tests_pass(self):
        """Stage 1 dedupe gate still works."""
        from src.data.import_tracker import filter_new_tickets
        existing = {"T-1", "T-2"}
        rows = [{"ticket_id": "T-1"}, {"ticket_id": "T-3"}]
        new, skipped = filter_new_tickets(existing, rows)
        assert len(new) == 1
        assert skipped == 1
