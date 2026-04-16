"""
Diagnostic tests for Gemini Chat Uplevel — Session 1-4 bug investigation.

Each test targets a specific observed failure from the live app screenshots.
Tests are designed to REPRODUCE the bugs, proving root cause before fixes.

Run: python -m pytest tests/test_chat_uplevel_diagnostics.py -v
"""

import json
import sqlite3
import uuid
import pytest
from pathlib import Path

# Load migration SQL
_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
_MIG_005 = (_MIGRATIONS_DIR / "005_persistence_layer.sql").read_text(encoding="utf-8")
_MIG_006 = (_MIGRATIONS_DIR / "006_hybrid_chat.sql").read_text(encoding="utf-8")
_MIG_009 = (_MIGRATIONS_DIR / "009_chat_data_layer.sql").read_text(encoding="utf-8")


@pytest.fixture
def conn_full():
    """In-memory DB with all migrations (005 + 006 + 009)."""
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(_MIG_005)
    db.executescript(_MIG_006)
    db.executescript(_MIG_009)
    yield db
    db.close()


@pytest.fixture
def conn_pre009():
    """In-memory DB with only migrations 005 + 006 (simulates live DB)."""
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(_MIG_005)
    db.executescript(_MIG_006)
    yield db
    db.close()


# ═══════════════════════════════════════
#  BUG 1: RECENT chips show "gemini_chats" instead of titles
# ═══════════════════════════════════════

class TestBug_RecentChipsShowSourcePage:
    """
    OBSERVED: All 5 recent chips in the header show "gemini_chats" instead
    of meaningful titles like "Alma platform bug tickets".

    ROOT CAUSE: Sessions have title=None. The chip code falls back to
    source_page ("gemini_chats") instead of using first_question.

    On a pre-009 DB, list_sessions() returns the legacy format which
    does NOT include first_question — so there's nothing better to show.

    On a 009+ DB, list_sessions() uses v_session_summary which includes
    first_question, but title is still None.
    """

    def test_pre009_list_sessions_has_no_first_question(self, conn_pre009):
        """Pre-009: list_sessions returns no first_question field."""
        from src.services.chat_session import create_session, list_sessions
        sid = create_session("gemini_chats", conn=conn_pre009)
        # Add a message via legacy blob
        conn_pre009.execute(
            "UPDATE chat_sessions SET messages = ? WHERE session_id = ?",
            (json.dumps([{"role": "user", "content": "Are there billing bugs?"}]), sid),
        )
        conn_pre009.commit()

        sessions = list_sessions(5, conn_pre009)
        assert len(sessions) == 1
        # Pre-009 fallback has no first_question key
        assert "first_question" not in sessions[0]
        # Title is None — chip will fall back to source_page
        assert sessions[0]["title"] is None
        assert sessions[0]["source_page"] == "gemini_chats"

    def test_post009_list_sessions_has_first_question(self, conn_full):
        """Post-009: list_sessions returns first_question from v_session_summary."""
        from src.services.chat_session import create_session, append_message, list_sessions
        sid = create_session("gemini_chats", conn=conn_full)
        append_message(sid, "user", "Are there billing bugs?", conn_full)
        append_message(sid, "assistant", "Yes, I found 3 billing issues.", conn_full)

        sessions = list_sessions(5, conn_full)
        assert len(sessions) == 1
        # v_session_summary includes first_question
        assert sessions[0].get("first_question") == "Are there billing bugs?"

    def test_chip_title_fallback_shows_source_page_when_no_question(self, conn_full):
        """When title AND first_question are both None, chip shows source_page."""
        from src.services.chat_session import create_session, list_sessions
        # Create empty session (no messages)
        sid = create_session("gemini_chats", conn=conn_full)

        sessions = list_sessions(5, conn_full)
        s = sessions[0]
        # Simulating the chip title logic from gemini_chats_page.py:
        title = s.get("title") or s.get("first_question") or s.get("source_page", "Chat")
        assert title == "gemini_chats"  # BUG: shows source_page instead of "Chat"


# ═══════════════════════════════════════
#  BUG 2: Migration 009 not applied on live DB
# ═══════════════════════════════════════

class TestBug_Migration009NotApplied:
    """
    OBSERVED: v_session_summary view, chat_messages table, and chat_projects
    table do not exist on the live database. Only migration 001 was applied.

    ROOT CAUSE: The SchemaMigrator runs during db_manager.initialize(), but
    it only applies migrations that haven't been recorded. The live DB has
    schema_migrations with only 001. Migrations 002-009 should be pending
    but something prevented them from running (likely the tables were created
    by db_manager's inline DDL before the migrator ran, causing conflicts).

    IMPACT: All drilldown panels that query migration-009 tables/views fail
    silently and show empty content.
    """

    def test_list_sessions_falls_back_on_pre009_db(self, conn_pre009):
        """list_sessions should still work on pre-009 DB via fallback."""
        from src.services.chat_session import create_session, list_sessions
        create_session("test", conn=conn_pre009)
        sessions = list_sessions(5, conn_pre009)
        assert len(sessions) == 1
        # Should use legacy format (no message_count, no total_cost)
        assert "session_id" in sessions[0]

    def test_append_message_falls_back_on_pre009_db(self, conn_pre009):
        """append_message should not crash on pre-009 DB."""
        from src.services.chat_session import create_session, append_message
        sid = create_session("test", conn=conn_pre009)
        result = append_message(sid, "user", "Hello", conn_pre009)
        # Returns None because chat_messages doesn't exist
        assert result is None
        # But legacy blob should have the message
        row = conn_pre009.execute(
            "SELECT messages FROM chat_sessions WHERE session_id = ?", (sid,)
        ).fetchone()
        messages = json.loads(row[0])
        assert len(messages) == 1


# ═══════════════════════════════════════
#  BUG 3: Chats drilldown empty — v_session_summary doesn't exist
# ═══════════════════════════════════════

class TestBug_ChatsDrilldownEmpty:
    """
    OBSERVED: The "Recent Chats" section in the Chats drilldown tab is empty.

    ROOT CAUSE: The _refresh_chats() method queries v_session_summary,
    which doesn't exist because migration 009 hasn't been applied.
    The exception is caught silently (logger.debug level).
    """

    def test_refresh_chats_query_fails_on_pre009(self, conn_pre009):
        """v_session_summary query should fail on pre-009 database."""
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            conn_pre009.execute(
                "SELECT session_id, title, message_count FROM v_session_summary"
            )

    def test_refresh_chats_works_on_post009(self, conn_full):
        """v_session_summary query works after migration 009."""
        from src.services.chat_session import create_session, append_message
        sid = create_session("gemini_chats", conn=conn_full)
        append_message(sid, "user", "test question", conn_full)

        rows = conn_full.execute(
            """SELECT session_id, title, message_count, total_cost,
                      last_message_at, first_question
               FROM v_session_summary
               ORDER BY COALESCE(last_message_at, updated_at) DESC
               LIMIT 20"""
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][2] == 1  # message_count
        assert rows[0][5] == "test question"  # first_question


# ═══════════════════════════════════════
#  BUG 4: Monitor shows "No active session"
# ═══════════════════════════════════════

class TestBug_MonitorNoActiveSession:
    """
    OBSERVED: Monitor tab shows "No active session" despite chat being active.

    ROOT CAUSE: ChatDrilldown._session_id is set in _on_explore_mode()
    via set_session_id(), but only AFTER _ensure_chat_drilldown() creates
    the widget. The monitor refresh happens in _switch_tab("monitor")
    which fires during construction. By the time set_session_id() is called
    in _on_explore_mode(), the monitor has already rendered with None.

    ALSO: Even if session_id is correct, on a pre-009 DB, the chat_messages
    query will fail since the table doesn't exist, so monitor would be empty.
    """

    def test_monitor_query_fails_without_chat_messages_table(self, conn_pre009):
        """Monitor queries chat_messages which doesn't exist on pre-009."""
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            conn_pre009.execute(
                "SELECT ordinal, role, content FROM chat_messages WHERE session_id = 'test'"
            )

    def test_monitor_query_works_post009(self, conn_full):
        """Monitor queries work on post-009 database."""
        from src.services.chat_session import create_session, append_message
        sid = create_session("test", conn=conn_full)
        append_message(sid, "user", "Hello", conn_full)
        append_message(sid, "assistant", "Hi!", conn_full,
                       model_used="gemini-3-flash", tokens_in=100, tokens_out=50)

        rows = conn_full.execute(
            """SELECT ordinal, role, content, created_at,
                      tokens_in, tokens_out, cost_usd, latency_ms, model_used
               FROM chat_messages WHERE session_id = ?
               ORDER BY ordinal""",
            (sid,),
        ).fetchall()
        assert len(rows) == 2
        assert rows[1][8] == "gemini-3-flash"  # model_used on assistant msg


# ═══════════════════════════════════════
#  BUG 5: Incidents drilldown — schema mismatch
# ═══════════════════════════════════════

class TestBug_IncidentsSchemaMatch:
    """
    OBSERVED: Incidents tab is empty despite 59 rows in incident_flags.

    ROOT CAUSE: The _refresh_incidents() query uses wrong column names:
      - Queries: id, incident_id, severity, description, ticket_count, active
      - Actual:  flag_id, trc_code, flag_type, theta_level, status, ...
      - No 'active' column (uses 'status' = 'open')
      - No 'severity' column (uses 'theta_level')
      - No 'description' column
      - No 'ticket_count' column
      - No 'incident_id' column

    The WHERE active=1 clause causes OperationalError, caught silently.
    """

    def test_incidents_query_fails_with_wrong_columns(self, conn_pre009):
        """The current incidents query uses wrong column names."""
        # Create incident_flags table with actual schema
        conn_pre009.execute("""
            CREATE TABLE IF NOT EXISTS incident_flags (
                flag_id INTEGER PRIMARY KEY AUTOINCREMENT,
                trc_code TEXT, flag_type TEXT, theta_level TEXT,
                direction TEXT, triggered_at TEXT, triggered_date TEXT,
                triggered_hour INTEGER, observed_value REAL,
                expected_lambda REAL, threshold_value REAL,
                p_value REAL, cusum_value REAL,
                status TEXT DEFAULT 'open', notes TEXT,
                resolved_at TEXT, created_at TEXT
            )
        """)
        conn_pre009.execute("""
            INSERT INTO incident_flags (trc_code, flag_type, theta_level, status, created_at)
            VALUES ('BIL-01', 'spike', 'HIGH', 'open', '2026-04-01')
        """)
        conn_pre009.commit()

        # The query from chat_drilldown._refresh_incidents() will fail
        with pytest.raises(sqlite3.OperationalError):
            conn_pre009.execute(
                """SELECT id, incident_id, trc_code, severity, description,
                          ticket_count, created_at
                   FROM incident_flags
                   WHERE active = 1"""
            )

    def test_correct_incidents_query_works(self, conn_pre009):
        """The corrected query using actual columns should work."""
        conn_pre009.execute("""
            CREATE TABLE IF NOT EXISTS incident_flags (
                flag_id INTEGER PRIMARY KEY AUTOINCREMENT,
                trc_code TEXT, flag_type TEXT, theta_level TEXT,
                direction TEXT, triggered_at TEXT, triggered_date TEXT,
                triggered_hour INTEGER, observed_value REAL,
                expected_lambda REAL, threshold_value REAL,
                p_value REAL, cusum_value REAL,
                status TEXT DEFAULT 'open', notes TEXT,
                resolved_at TEXT, created_at TEXT
            )
        """)
        conn_pre009.execute("""
            INSERT INTO incident_flags (trc_code, flag_type, theta_level, status, created_at)
            VALUES ('BIL-01', 'spike', 'HIGH', 'open', '2026-04-01')
        """)
        conn_pre009.commit()

        # Correct query using actual schema columns
        rows = conn_pre009.execute(
            """SELECT flag_id, trc_code, flag_type, theta_level,
                      direction, triggered_at, status, created_at
               FROM incident_flags
               WHERE status = 'open'
               ORDER BY
                 CASE theta_level WHEN 'HIGH' THEN 1 WHEN 'MED' THEN 2 ELSE 3 END,
                 created_at DESC
               LIMIT 20"""
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][1] == "BIL-01"
        assert rows[0][3] == "HIGH"


# ═══════════════════════════════════════
#  BUG 6: Bridge "getTool" error
# ═══════════════════════════════════════

class TestBug_BridgeGetToolError:
    """
    OBSERVED: "Error: Bridge call failed: unknown - Cannot read properties
    of undefined (reading 'getTool')"

    ROOT CAUSE: The Gemini CLI's Scheduler calls registry.getTool() when
    the model uses native function calling. If the tool registry wasn't
    initialized (config.getToolRegistry() returned undefined), this crashes.

    This is a JavaScript-side error in the Node.js bridge subprocess.
    The bridge_tools.mjs file documents this exact error and has a
    registration mechanism, but it fails when the CLI's Config object
    doesn't expose getToolRegistry().

    DIAGNOSTIC: This test verifies the Python-side error handling path
    works correctly when the bridge returns an error.
    """

    def test_bridge_error_propagates_as_runtime_error(self):
        """ReportBridgeClient.generate() should raise RuntimeError on bridge failure."""
        from unittest.mock import MagicMock, patch

        from src.agents.report_bridge_client import ReportBridgeClient
        client = ReportBridgeClient(model="gemini-3-flash-preview")

        # Mock the bridge to simulate the getTool error
        mock_bridge = MagicMock()
        mock_bridge.call_blocking.side_effect = RuntimeError(
            "Bridge call failed: unknown - Cannot read properties of undefined (reading 'getTool')"
        )
        client._bridge = mock_bridge

        with pytest.raises(RuntimeError, match="getTool"):
            client.generate("test prompt")

    def test_chat_engine_handles_bridge_error_gracefully(self):
        """ChatEngine should emit error_occurred when bridge fails."""
        from unittest.mock import MagicMock
        from src.services.chat_engine import ChatEngine

        mock_client = MagicMock()
        mock_client.generate.side_effect = RuntimeError(
            "Bridge call failed: unknown - Cannot read properties of undefined"
        )

        engine = ChatEngine(system_prompt="test")
        engine.set_client(mock_client)

        errors = []
        engine.error_occurred.connect(lambda e: errors.append(e))

        engine.send("Hello")

        # Wait for worker
        from PySide6.QtCore import QCoreApplication
        import time
        app = QCoreApplication.instance() or QCoreApplication([])
        deadline = time.time() + 5
        while time.time() < deadline and not errors:
            if engine._worker:
                engine._worker.wait(100)
            app.processEvents()

        assert len(errors) == 1
        assert "Cannot read properties" in errors[0]


# ═══════════════════════════════════════
#  SUMMARY: Root causes and fix targets
# ═══════════════════════════════════════

class TestDiagnosticSummary:
    """
    Summary of all bugs and their root causes:

    1. RECENT CHIPS ("gemini_chats"):
       - Live DB has no migration 009 → list_sessions uses legacy fallback
       - Legacy fallback has no first_question field
       - All sessions have title=None → falls back to source_page
       FIX: (a) Apply migration 009 on live DB
            (b) Improve chip fallback — extract first user message from JSON blob

    2. CHATS DRILLDOWN (empty):
       - Queries v_session_summary which doesn't exist (no migration 009)
       - Exception caught silently → empty list
       FIX: (a) Apply migration 009
            (b) Add fallback query to raw chat_sessions for pre-009 DBs

    3. MONITOR ("No active session"):
       - session_id not passed to ChatDrilldown during construction
       - Also: chat_messages table doesn't exist on pre-009 DB
       FIX: (a) Apply migration 009
            (b) Pass session_id during ChatDrilldown construction
            (c) Add fallback for pre-009 DBs

    4. REPORTS (empty):
       - analysis_runs table has 0 rows (correct behavior — no reports generated)
       FIX: Not a bug — expected state. Empty state message shows correctly.

    5. INCIDENTS (empty):
       - Schema mismatch: query uses wrong column names
         (active, severity, description, ticket_count, incident_id)
         vs actual (status, theta_level, flag_type, flag_id)
       - WHERE active=1 fails → OperationalError caught silently
       FIX: Rewrite query to use actual incident_flags schema

    6. BRIDGE "getTool" ERROR:
       - Gemini model uses native function calling but tool registry
         not initialized in bridge subprocess
       - JavaScript TypeError in Scheduler when calling registry.getTool()
       FIX: Ensure bridge tool registration or force fenced-block mode
    """

    def test_summary_placeholder(self):
        """This test class exists for documentation. Always passes."""
        assert True
