"""
Bug 1 Tests: Clear & Close Conversations Behavior
===================================================
H0 (Null Hypothesis): 'conversations' IS in EPHEMERAL_TABLES and is cleared
    by clear_session_data(), so Clear & Close properly wipes conversations.

HA (Alternative): 'conversations' was removed from EPHEMERAL_TABLES by Session 1,
    so clear_session_data() preserves conversations — Clear & Close does NOT wipe them.

If H0 is REJECTED, the bug is confirmed: conversations persist when users expect
them to clear, causing import validation issues on subsequent imports.

Run: python -m pytest tests/test_bug_clear_close.py -x -v
"""

import sqlite3
import pytest
from pathlib import Path


@pytest.fixture
def seeded_db(tmp_path):
    """Create a DB with conversations data, matching production schema."""
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager(tmp_path / "clear_test.db")
    db.initialize()
    # Seed 10 conversations
    for i in range(10):
        db.upsert_ticket({
            "ticket_id": f"T-{i}", "subject": f"Test {i}",
            "trc_code": "AUTH-01", "trc_label": "AUTH-01", "status": "open",
            "priority": "", "channel": "", "csat_score": 4.0,
            "created_at": "2026-01-15", "updated_at": "", "solved_at": "",
            "requester_name": "", "requester_email": "", "assignee_name": "",
            "group_name": "", "tags": [], "custom_fields": {},
            "assignment_to_resolution_hours": None,
            "total_resolution_hours": None, "first_reply_hours": None,
        })
        db.upsert_conversation({
            "ticket_id": f"T-{i}", "subject": f"Test {i}",
            "trc_code": "AUTH-01", "trc_label": "AUTH-01", "status": "open",
            "csat_score": 4.0, "created_at": "2026-01-15", "solved_at": "",
            "message_count": 2, "client_messages": 1, "agent_messages": 1,
            "full_thread": f"Thread {i}", "thread_preview": f"Preview {i}",
            "dataset_id": 0,
        })
        db.upsert_comment({
            "comment_id": f"T-{i}-0", "ticket_id": f"T-{i}",
            "author_name": "Agent", "author_role": "agent",
            "body": f"Comment on T-{i}", "is_public": True,
            "created_at": "2026-01-15 10:00",
        })
    db.commit()
    yield db
    db.close()


class TestEphemeralTableClassification:
    """Verify which tables are ephemeral vs permanent after Session 7."""

    def test_conversations_is_ephemeral(self):
        """Session 7: conversations IS in EPHEMERAL_TABLES (clears on Close)."""
        from src.services.clear_session import EPHEMERAL_TABLES
        assert "conversations" in EPHEMERAL_TABLES

    def test_tickets_is_permanent(self):
        """tickets is NOT in EPHEMERAL_TABLES (persists across Close)."""
        from src.services.clear_session import EPHEMERAL_TABLES
        assert "tickets" not in EPHEMERAL_TABLES

    def test_comments_is_permanent(self):
        """comments is NOT in EPHEMERAL_TABLES (persists across Close)."""
        from src.services.clear_session import EPHEMERAL_TABLES
        assert "comments" not in EPHEMERAL_TABLES


class TestClearBehavior:
    """Verify actual clear behavior regardless of hypothesis."""

    def test_conversations_cleared_after_session_clear(self, seeded_db):
        """H0 predicts conversations count = 0 after clear."""
        before = seeded_db.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        assert before == 10, "Precondition: 10 conversations seeded"

        from src.services.clear_session import clear_session_data
        clear_session_data(str(seeded_db.db_path))

        # Re-open (clear_session_data closes its own conn)
        after = seeded_db.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        assert after == 0, \
            f"H0 REJECTED: {after} conversations remain after clear (expected 0)"

    def test_tickets_preserved_after_session_clear(self, seeded_db):
        """Session 7: tickets persist after Clear & Close."""
        before = seeded_db.conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        assert before == 10

        from src.services.clear_session import clear_session_data
        clear_session_data(str(seeded_db.db_path))

        after = seeded_db.conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        assert after == 10, f"Tickets should persist, but {after} remain (expected 10)"

    def test_comments_preserved_after_session_clear(self, seeded_db):
        """Session 7: comments persist after Clear & Close."""
        before = seeded_db.conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
        assert before == 10

        from src.services.clear_session import clear_session_data
        clear_session_data(str(seeded_db.db_path))

        after = seeded_db.conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
        assert after == 10, f"Comments should persist, but {after} remain (expected 10)"

    def test_raw_ingestion_still_cleared(self, seeded_db):
        """Ephemeral staging tables should always clear."""
        # Create the table if it doesn't exist (it's created by Lightdash import, not initialize)
        seeded_db.conn.execute("""
            CREATE TABLE IF NOT EXISTS raw_ingestion_rows (
                rowid INTEGER PRIMARY KEY, ticket_id TEXT, body TEXT
            )
        """)
        seeded_db.conn.execute(
            "INSERT INTO raw_ingestion_rows (ticket_id, body) VALUES ('test', 'body')"
        )
        seeded_db.conn.commit()

        from src.services.clear_session import clear_session_data
        clear_session_data(str(seeded_db.db_path))

        count = seeded_db.conn.execute(
            "SELECT COUNT(*) FROM raw_ingestion_rows"
        ).fetchone()[0]
        assert count == 0, "raw_ingestion_rows should always clear"

    def test_nlp_tables_preserved_after_clear(self, seeded_db):
        """NLP enrichment tables should survive clear (expensive to regenerate)."""
        # Seed a sub_pattern
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        seeded_db.conn.execute(
            "INSERT OR IGNORE INTO sub_patterns "
            "(pattern_id, trc, label, friction_type, tier, discovered_scan, discovered_at) "
            "VALUES ('p1', 'AUTH', 'test', 'auth_delay', 'confirmed', 'scan-1', ?)",
            (now,)
        )
        seeded_db.conn.commit()

        from src.services.clear_session import clear_session_data
        clear_session_data(str(seeded_db.db_path))

        count = seeded_db.conn.execute("SELECT COUNT(*) FROM sub_patterns").fetchone()[0]
        assert count == 1, "sub_patterns should survive clear"
