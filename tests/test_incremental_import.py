"""
Test Module: Incremental Import — Stage 1
Stage: 1
Dependencies: None (Stage 1 is the foundation)

Covers:
  - ImportMode enum values and UI text conversion
  - Import tracker: start/complete/fail runs, dedupe gate
  - CSV import path: additive behavior, no DELETEs
  - Conversation rebuild: additive behavior, no DELETEs
  - Clear session: preserves tickets/conversations/comments
  - Migration 010: import_runs table creation
  - Full database reset via settings

Run:
  - Single file:  python -m pytest tests/test_incremental_import.py -x -v
  - All Stage 1:  python -m pytest tests/ -k "incremental or dedupe or import_mode" -x -v
"""

import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ═══════════════════════════════════════════
#  IMPORT MODE TESTS
# ═══════════════════════════════════════════

class TestImportMode:
    """ImportMode enum values and UI text conversion."""

    def test_enum_values(self):
        from src.data.import_mode import ImportMode
        assert ImportMode.INCREMENTAL.value == "incremental"
        assert ImportMode.FULL_REFRESH.value == "full_refresh"

    def test_mode_from_ui_full_refresh(self):
        from src.data.import_mode import mode_from_ui_text, ImportMode
        assert mode_from_ui_text("Full Refresh") == ImportMode.FULL_REFRESH

    def test_mode_from_ui_incremental(self):
        from src.data.import_mode import mode_from_ui_text, ImportMode
        assert mode_from_ui_text("Incremental") == ImportMode.INCREMENTAL

    def test_mode_from_ui_unknown_defaults(self):
        from src.data.import_mode import mode_from_ui_text, ImportMode
        assert mode_from_ui_text("garbage") == ImportMode.INCREMENTAL
        assert mode_from_ui_text("") == ImportMode.INCREMENTAL


# ═══════════════════════════════════════════
#  IMPORT TRACKER TESTS
# ═══════════════════════════════════════════

class TestImportTracker:
    """Import run lifecycle and dedupe gate logic."""

    @pytest.fixture
    def tracker_db(self, tmp_path):
        """Minimal SQLite with import_runs + tickets tables."""
        db_path = tmp_path / "tracker_test.db"
        conn = sqlite3.connect(str(db_path))
        # Create import_runs table
        migration_path = Path(__file__).parent.parent / "migrations" / "010_import_tracking.sql"
        conn.executescript(migration_path.read_text())
        # Create minimal tickets table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                ticket_id TEXT PRIMARY KEY,
                subject TEXT
            )
        """)
        conn.commit()
        yield conn
        conn.close()

    def test_start_import_run_creates_record(self, tracker_db):
        from src.data.import_tracker import start_import_run
        run_id = start_import_run(tracker_db, "csv", "incremental", "test.csv")
        row = tracker_db.execute(
            "SELECT run_id, source, mode, status FROM import_runs WHERE run_id = ?",
            (run_id,)
        ).fetchone()
        assert row is not None
        assert row[1] == "csv"
        assert row[2] == "incremental"
        assert row[3] == "running"

    def test_start_import_run_unique_id(self, tracker_db):
        from src.data.import_tracker import start_import_run
        id1 = start_import_run(tracker_db, "csv", "incremental")
        id2 = start_import_run(tracker_db, "csv", "incremental")
        assert id1 != id2

    def test_complete_import_run(self, tracker_db):
        from src.data.import_tracker import start_import_run, complete_import_run
        run_id = start_import_run(tracker_db, "csv", "incremental")
        complete_import_run(tracker_db, run_id, {
            "tickets_seen": 100, "tickets_new": 5, "tickets_skipped": 95
        })
        row = tracker_db.execute(
            "SELECT status, tickets_seen, tickets_new, tickets_skipped, completed_at "
            "FROM import_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert row[0] == "completed"
        assert row[1] == 100
        assert row[2] == 5
        assert row[3] == 95
        assert row[4] is not None  # completed_at set

    def test_fail_import_run(self, tracker_db):
        from src.data.import_tracker import start_import_run, fail_import_run
        run_id = start_import_run(tracker_db, "csv", "incremental")
        fail_import_run(tracker_db, run_id, "Connection timeout")
        row = tracker_db.execute(
            "SELECT status, error_message FROM import_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert row[0] == "failed"
        assert row[1] == "Connection timeout"

    def test_complete_nonexistent_run(self, tracker_db):
        """Completing a non-existent run doesn't crash."""
        from src.data.import_tracker import complete_import_run
        complete_import_run(tracker_db, "nonexistent-id", {"tickets_seen": 0})
        # Should not raise

    def test_get_existing_ticket_ids_empty(self, tracker_db):
        from src.data.import_tracker import get_existing_ticket_ids
        ids = get_existing_ticket_ids(tracker_db)
        assert ids == set()

    def test_get_existing_ticket_ids_populated(self, tracker_db):
        from src.data.import_tracker import get_existing_ticket_ids
        tracker_db.execute("INSERT INTO tickets VALUES ('T-1', 'Subject 1')")
        tracker_db.execute("INSERT INTO tickets VALUES ('T-2', 'Subject 2')")
        tracker_db.commit()
        ids = get_existing_ticket_ids(tracker_db)
        assert ids == {"T-1", "T-2"}


# ═══════════════════════════════════════════
#  DEDUPE GATE TESTS
# ═══════════════════════════════════════════

class TestDedupeGate:
    """filter_new_tickets logic — pure function tests."""

    def test_all_new(self):
        from src.data.import_tracker import filter_new_tickets
        rows = [{"ticket_id": f"T-{i}"} for i in range(10)]
        new, skipped = filter_new_tickets(set(), rows)
        assert len(new) == 10
        assert skipped == 0

    def test_all_existing(self):
        from src.data.import_tracker import filter_new_tickets
        existing = {f"T-{i}" for i in range(10)}
        rows = [{"ticket_id": f"T-{i}"} for i in range(10)]
        new, skipped = filter_new_tickets(existing, rows)
        assert len(new) == 0
        assert skipped == 10

    def test_mixed(self):
        from src.data.import_tracker import filter_new_tickets
        existing = {f"T-{i}" for i in range(95)}
        rows = [{"ticket_id": f"T-{i}"} for i in range(100)]
        new, skipped = filter_new_tickets(existing, rows)
        assert len(new) == 5
        assert skipped == 95

    def test_empty_incoming(self):
        from src.data.import_tracker import filter_new_tickets
        new, skipped = filter_new_tickets({"T-1", "T-2"}, [])
        assert len(new) == 0
        assert skipped == 0

    def test_case_sensitive(self):
        from src.data.import_tracker import filter_new_tickets
        existing = {"ABC-123"}
        rows = [{"ticket_id": "abc-123"}, {"ticket_id": "ABC-123"}]
        new, skipped = filter_new_tickets(existing, rows)
        assert len(new) == 1  # abc-123 is NOT matched
        assert skipped == 1

    def test_custom_id_column(self):
        from src.data.import_tracker import filter_new_tickets
        existing = {"T-1"}
        rows = [{"id": "T-1"}, {"id": "T-2"}]
        new, skipped = filter_new_tickets(existing, rows, id_column="id")
        assert len(new) == 1
        assert new[0]["id"] == "T-2"

    def test_performance_10k(self):
        from src.data.import_tracker import filter_new_tickets
        existing = {f"T-{i}" for i in range(10_000)}
        rows = [{"ticket_id": f"T-{i}"} for i in range(5_000, 15_000)]
        start = time.monotonic()
        new, skipped = filter_new_tickets(existing, rows)
        elapsed = time.monotonic() - start
        assert len(new) == 5_000
        assert skipped == 5_000
        assert elapsed < 1.0  # Should be well under 100ms


# ═══════════════════════════════════════════
#  CSV IMPORT — ADDITIVE BEHAVIOR
# ═══════════════════════════════════════════

class TestCSVIncremental:
    """CSV import path: no DELETEs, additive dedupe."""

    def test_csv_import_no_delete_executed(self, seeded_db):
        """Verify the DELETE FROM statements were removed from _write_tickets_to_db."""
        import src.data.csv_ingestion as mod
        import inspect
        source = inspect.getsource(mod._write_tickets_to_db)
        assert "DELETE FROM conversations" not in source
        assert "DELETE FROM comments" not in source
        assert "DELETE FROM tickets" not in source

    def test_csv_write_skips_existing(self, seeded_db):
        """_write_tickets_to_db skips tickets already in the DB."""
        from src.data.csv_ingestion import _write_tickets_to_db

        # seeded_db has 100 tickets (T-1000 through T-1099)
        before = seeded_db.get_ticket_count()
        assert before == 100

        # Build a ticket dict with 95 existing + 5 new
        tickets = {}
        for i in range(1000, 1095):  # 95 existing
            tickets[f"T-{i}"] = _make_ticket(f"T-{i}")
        for i in range(2000, 2005):  # 5 new
            tickets[f"T-{i}"] = _make_ticket(f"T-{i}")

        inserted = _write_tickets_to_db(seeded_db, tickets, None, lambda *a: None)
        assert inserted == 5

        after = seeded_db.get_ticket_count()
        assert after == 105  # 100 original + 5 new

    def test_csv_import_twice_stable(self, seeded_db):
        """Importing the same tickets twice doesn't create duplicates."""
        from src.data.csv_ingestion import _write_tickets_to_db

        before = seeded_db.get_ticket_count()
        tickets = {f"T-{i}": _make_ticket(f"T-{i}") for i in range(1000, 1100)}

        inserted = _write_tickets_to_db(seeded_db, tickets, None, lambda *a: None)
        assert inserted == 0  # All already exist
        assert seeded_db.get_ticket_count() == before

    def test_csv_import_additive_different_tickets(self, seeded_db):
        """Import CSV A (existing) then CSV B (new) -> both present."""
        from src.data.csv_ingestion import _write_tickets_to_db

        batch_a = {f"T-{i}": _make_ticket(f"T-{i}") for i in range(1000, 1100)}
        _write_tickets_to_db(seeded_db, batch_a, None, lambda *a: None)
        count_after_a = seeded_db.get_ticket_count()
        assert count_after_a == 100  # All 100 existed

        batch_b = {f"NEW-{i}": _make_ticket(f"NEW-{i}") for i in range(50)}
        inserted = _write_tickets_to_db(seeded_db, batch_b, None, lambda *a: None)
        assert inserted == 50
        assert seeded_db.get_ticket_count() == 150


# ═══════════════════════════════════════════
#  CONVERSATION REBUILD — ADDITIVE BEHAVIOR
# ═══════════════════════════════════════════

class TestConversationRebuildIncremental:
    """conversation_rebuild.py: no DELETEs, additive dedupe."""

    def test_rebuild_no_delete_in_source(self):
        """Verify DELETE FROM statements were removed."""
        import src.data.conversation_rebuild as mod
        import inspect
        source = inspect.getsource(mod.rebuild_conversations)
        assert "DELETE FROM conversations" not in source
        assert "DELETE FROM comments" not in source
        assert "DELETE FROM tickets" not in source


# ═══════════════════════════════════════════
#  CLEAR SESSION — PRESERVES PERMANENT DATA
# ═══════════════════════════════════════════

class TestClearSessionUpdated:
    """Clear & Close preserves tickets/comments; conversations are ephemeral (Session 7)."""

    def test_ephemeral_tables_no_permanent(self):
        """tickets, comments not in EPHEMERAL_TABLES. conversations IS ephemeral (Session 7)."""
        from src.services.clear_session import EPHEMERAL_TABLES
        assert "tickets" not in EPHEMERAL_TABLES
        assert "conversations" in EPHEMERAL_TABLES  # Session 7: conversations clear on Close
        assert "comments" not in EPHEMERAL_TABLES

    def test_ephemeral_tables_has_staging(self):
        """raw_ingestion_rows and ingestion_chunks are still ephemeral."""
        from src.services.clear_session import EPHEMERAL_TABLES
        assert "raw_ingestion_rows" in EPHEMERAL_TABLES
        assert "ingestion_chunks" in EPHEMERAL_TABLES
        assert "nlp_batches" in EPHEMERAL_TABLES

    def test_clear_preserves_tickets(self, seeded_db):
        """After clear_session_data: tickets table row count unchanged."""
        from src.services.clear_session import clear_session_data
        before = seeded_db.get_ticket_count()
        assert before == 100

        clear_session_data(str(seeded_db.db_path))
        # Need a fresh connection since clear_session_data opens its own
        after_count = seeded_db.conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        assert after_count == before

    def test_clear_clears_conversations(self, seeded_db):
        """Session 7: conversations are cleared on Clear & Close."""
        before = seeded_db.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        assert before == 100

        from src.services.clear_session import clear_session_data
        clear_session_data(str(seeded_db.db_path))

        after = seeded_db.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        assert after == 0, "Conversations should be cleared on Close (Session 7)"

    def test_clear_preserves_comments(self, seeded_db):
        """After clear_session_data: comments table unchanged."""
        before = seeded_db.conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
        assert before > 0

        from src.services.clear_session import clear_session_data
        clear_session_data(str(seeded_db.db_path))

        after = seeded_db.conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
        assert after == before


# ═══════════════════════════════════════════
#  MIGRATION 010 — IMPORT TRACKING TABLE
# ═══════════════════════════════════════════

class TestMigration010:
    """Migration 010 creates import_runs table correctly."""

    @pytest.fixture
    def fresh_db(self, tmp_path):
        db_path = tmp_path / "migration_test.db"
        conn = sqlite3.connect(str(db_path))
        yield conn
        conn.close()

    def test_import_runs_table_created(self, fresh_db):
        migration = Path(__file__).parent.parent / "migrations" / "010_import_tracking.sql"
        fresh_db.executescript(migration.read_text())
        tables = {r[0] for r in fresh_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "import_runs" in tables

    def test_import_runs_columns(self, fresh_db):
        migration = Path(__file__).parent.parent / "migrations" / "010_import_tracking.sql"
        fresh_db.executescript(migration.read_text())
        cols = {r[1] for r in fresh_db.execute("PRAGMA table_info(import_runs)").fetchall()}
        expected = {"run_id", "started_at", "completed_at", "source", "mode",
                    "file_name", "tickets_seen", "tickets_new", "tickets_skipped",
                    "status", "error_message"}
        assert expected == cols

    def test_import_runs_index(self, fresh_db):
        migration = Path(__file__).parent.parent / "migrations" / "010_import_tracking.sql"
        fresh_db.executescript(migration.read_text())
        indexes = {r[1] for r in fresh_db.execute("PRAGMA index_list(import_runs)").fetchall()}
        assert "idx_import_runs_status" in indexes

    def test_migration_idempotent(self, fresh_db):
        migration = Path(__file__).parent.parent / "migrations" / "010_import_tracking.sql"
        sql = migration.read_text()
        fresh_db.executescript(sql)
        fresh_db.executescript(sql)  # Should not error
        count = fresh_db.execute("SELECT COUNT(*) FROM import_runs").fetchone()[0]
        assert count == 0  # No data created by migration

    def test_migration_default_values(self, fresh_db):
        migration = Path(__file__).parent.parent / "migrations" / "010_import_tracking.sql"
        fresh_db.executescript(migration.read_text())
        fresh_db.execute(
            "INSERT INTO import_runs (run_id, started_at, source, mode) "
            "VALUES ('test', '2025-01-01', 'csv', 'incremental')"
        )
        row = fresh_db.execute(
            "SELECT tickets_seen, tickets_new, tickets_skipped, status "
            "FROM import_runs WHERE run_id = 'test'"
        ).fetchone()
        assert row[0] == 0  # tickets_seen default
        assert row[1] == 0  # tickets_new default
        assert row[2] == 0  # tickets_skipped default
        assert row[3] == "running"  # status default


# ═══════════════════════════════════════════
#  MAIN WINDOW — NO DESTRUCTIVE FALLBACK
# ═══════════════════════════════════════════

class TestMainWindowCloseEvent:
    """closeEvent no longer calls _clear_all_data as fallback."""

    def test_no_clear_all_data_fallback_in_close(self):
        """The closeEvent method should NOT call _clear_all_data."""
        import inspect
        from src.ui.main_window import MainWindow
        source = inspect.getsource(MainWindow.closeEvent)
        assert "_clear_all_data" not in source


# ═══════════════════════════════════════════
#  REGRESSION — EXISTING IMPORTS WORK
# ═══════════════════════════════════════════

class TestStage1Regression:
    """Existing module imports and signatures unchanged."""

    def test_csv_ingestion_import(self):
        from src.data.csv_ingestion import ingest_csv
        assert callable(ingest_csv)

    def test_conversation_rebuild_import(self):
        from src.data.conversation_rebuild import rebuild_conversations
        assert callable(rebuild_conversations)

    def test_clear_session_import(self):
        from src.services.clear_session import clear_session_data
        assert callable(clear_session_data)

    def test_import_mode_import(self):
        from src.data.import_mode import ImportMode, mode_from_ui_text
        assert callable(mode_from_ui_text)

    def test_import_tracker_import(self):
        from src.data.import_tracker import (
            start_import_run, complete_import_run, fail_import_run,
            get_existing_ticket_ids, filter_new_tickets
        )
        assert callable(start_import_run)
        assert callable(filter_new_tickets)


# ═══════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════

def _make_ticket(tid):
    """Build a minimal ticket dict for _write_tickets_to_db."""
    return {
        "ticket_id": tid,
        "subject": f"Test ticket {tid}",
        "trc_code": "TRC-100",
        "trc_label": "Billing Issues",
        "status": "solved",
        "priority": "normal",
        "channel": "email",
        "csat_score": 3.0,
        "created_at": "2025-01-15",
        "updated_at": "",
        "solved_at": "2025-01-16",
        "requester_name": "Test User",
        "requester_email": "",
        "assignee_name": "Test Agent",
        "group_name": "Support",
        "tags": [],
        "custom_fields": {},
        "assignment_to_resolution_hours": 5.0,
        "total_resolution_hours": 6.0,
        "first_reply_hours": 1.0,
        "comments": [
            {
                "comment_id": f"{tid}-0",
                "author_name": "Test User",
                "author_role": "customer",
                "body": f"Test comment for {tid}",
                "is_public": True,
                "created_at": "2025-01-15 09:00",
            }
        ],
    }
