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


# ──────────────────────────────────────────────────────────────────
# Bug-bash 2026-04-17 — Conversation Search session-visibility flag
#
# Clear & Close wipes the legacy `conversations` ingestion buffer, but
# the Conversation Search page reads from the PERSISTENT per-source
# warehouse tables (`{prefix}_conversations`). Those are not wiped
# (by design — the Data Warehouse page needs them).
#
# To give the user the "Clear & Close empties the Conversation Search
# page without wiping the warehouse" behaviour they asked for, we
# introduce a session-visibility flag: `ui.conversation_search_hidden`.
#   * clear_session_data() sets it True.
#   * ConversationSearch.run_search() returns [] when True.
#   * A successful CSV / Lightdash import clears it back to False.
# ──────────────────────────────────────────────────────────────────


class TestConversationSearchHiddenFlag:
    """The flag lives in a lazy `app_state` table inside the DB itself,
    so tests with isolated tmp DBs never touch the user's real data.
    (Earlier design used settings.yaml — that leaked writes from every
    test that called clear_session_data into the real settings file.)"""

    def test_default_is_false(self, tmp_path):
        from src.services.clear_session import is_conversation_search_hidden
        from src.data.db_manager import DatabaseManager
        db = DatabaseManager(tmp_path / "flag_default.db")
        db.initialize()
        db.close()
        assert is_conversation_search_hidden(str(tmp_path / "flag_default.db")) is False

    def test_set_true_then_read(self, tmp_path):
        from src.services.clear_session import (
            is_conversation_search_hidden,
            set_conversation_search_hidden,
        )
        from src.data.db_manager import DatabaseManager
        db = DatabaseManager(tmp_path / "flag_set.db")
        db.initialize()
        db.close()
        p = str(tmp_path / "flag_set.db")
        set_conversation_search_hidden(p, True)
        assert is_conversation_search_hidden(p) is True

    def test_set_false_then_read(self, tmp_path):
        from src.services.clear_session import (
            is_conversation_search_hidden,
            set_conversation_search_hidden,
        )
        from src.data.db_manager import DatabaseManager
        db = DatabaseManager(tmp_path / "flag_unset.db")
        db.initialize()
        db.close()
        p = str(tmp_path / "flag_unset.db")
        set_conversation_search_hidden(p, True)
        set_conversation_search_hidden(p, False)
        assert is_conversation_search_hidden(p) is False

    def test_clear_session_data_sets_flag(self, seeded_db):
        from src.services.clear_session import (
            clear_session_data,
            is_conversation_search_hidden,
        )
        p = str(seeded_db.db_path)
        assert is_conversation_search_hidden(p) is False, "precondition"
        clear_session_data(p)
        assert is_conversation_search_hidden(p) is True, (
            "Clear & Close must set the session-visibility flag so the "
            "Conversation Search page renders empty while the Data "
            "Warehouse preserves its rows."
        )


class TestConversationSearchHiddenFlagWiredInto:
    """Source-inspection tests: verify the two callers we expect to
    respect / clear the flag actually reference it. Cheaper than spinning
    up a Qt page in a unit test."""

    def test_conversation_search_page_checks_flag(self):
        src = (
            Path(__file__).parent.parent / "src" / "ui" / "pages" / "conversation_search.py"
        ).read_text(encoding="utf-8")
        assert "is_conversation_search_hidden" in src, (
            "Conversation Search page must import and check the session "
            "visibility flag in run_search()."
        )

    def test_csv_ingestion_clears_flag(self):
        src = (
            Path(__file__).parent.parent / "src" / "data" / "csv_ingestion.py"
        ).read_text(encoding="utf-8")
        assert "set_conversation_search_hidden" in src, (
            "CSV ingestion must clear the hidden flag after a successful "
            "import so Conversation Search shows the new data."
        )

    def test_lightdash_clears_flag(self):
        src = (
            Path(__file__).parent.parent / "src" / "data" / "lightdash_client.py"
        ).read_text(encoding="utf-8")
        assert "set_conversation_search_hidden" in src, (
            "Lightdash ingestion must clear the hidden flag after a "
            "successful pull."
        )
