"""Tests for Migration 009: Chat Data Layer.

Tests the new chat_messages table, chat_projects, chat_tool_executions,
virtual views, FTS5 search, data migration from JSON blobs, and
the updated CRUD layer in chat_session.py.
"""

import json
import sqlite3
import uuid
import pytest
from pathlib import Path

# Load migration SQL files
_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
_MIG_005 = (_MIGRATIONS_DIR / "005_persistence_layer.sql").read_text(encoding="utf-8")
_MIG_006 = (_MIGRATIONS_DIR / "006_hybrid_chat.sql").read_text(encoding="utf-8")
_MIG_009 = (_MIGRATIONS_DIR / "009_chat_data_layer.sql").read_text(encoding="utf-8")


@pytest.fixture
def conn():
    """In-memory DB with migrations 005 + 006 + 009 applied."""
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(_MIG_005)
    db.executescript(_MIG_006)
    db.executescript(_MIG_009)
    yield db
    db.close()


@pytest.fixture
def conn_pre009():
    """In-memory DB with only migrations 005 + 006 (pre-009)."""
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(_MIG_005)
    db.executescript(_MIG_006)
    yield db
    db.close()


# ═══════════════════════════════════════
#  Migration 009 — Schema Creation
# ═══════════════════════════════════════

class TestMigration009Schema:
    def test_chat_messages_table_exists(self, conn):
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='chat_messages'"
        ).fetchall()
        assert len(rows) == 1

    def test_chat_projects_table_exists(self, conn):
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='chat_projects'"
        ).fetchall()
        assert len(rows) == 1

    def test_chat_tool_executions_table_exists(self, conn):
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='chat_tool_executions'"
        ).fetchall()
        assert len(rows) == 1

    def test_fts5_table_exists(self, conn):
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='chat_messages_fts'"
        ).fetchall()
        assert len(rows) == 1

    def test_views_exist(self, conn):
        views = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='view'"
        ).fetchall()}
        assert "v_session_summary" in views
        assert "v_session_tools" in views
        assert "v_chat_cost_daily" in views

    def test_project_id_column_on_chat_sessions(self, conn):
        cols = {r[1] for r in conn.execute("PRAGMA table_info(chat_sessions)").fetchall()}
        assert "project_id" in cols

    def test_chat_messages_role_check_constraint(self, conn):
        """Inserting an invalid role should fail."""
        conn.execute(
            "INSERT INTO chat_sessions (session_id, created_at, updated_at, messages) "
            "VALUES ('s1', '2026-01-01', '2026-01-01', '[]')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO chat_messages (message_id, session_id, ordinal, role, content) "
                "VALUES ('m1', 's1', 0, 'invalid_role', 'test')"
            )

    def test_chat_messages_unique_ordinal(self, conn):
        """Duplicate (session_id, ordinal) should fail."""
        conn.execute(
            "INSERT INTO chat_sessions (session_id, created_at, updated_at, messages) "
            "VALUES ('s1', '2026-01-01', '2026-01-01', '[]')"
        )
        conn.execute(
            "INSERT INTO chat_messages (message_id, session_id, ordinal, role, content) "
            "VALUES ('m1', 's1', 0, 'user', 'first')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO chat_messages (message_id, session_id, ordinal, role, content) "
                "VALUES ('m2', 's1', 0, 'assistant', 'duplicate ordinal')"
            )


# ═══════════════════════════════════════
#  CRUD: append_message → chat_messages
# ═══════════════════════════════════════

class TestAppendMessageNew:
    def test_inserts_into_chat_messages(self, conn):
        from src.services.chat_session import create_session, append_message
        sid = create_session("gemini_chats", conn=conn)
        mid = append_message(sid, "user", "Hello", conn)

        assert mid is not None
        row = conn.execute(
            "SELECT * FROM chat_messages WHERE message_id = ?", (mid,)
        ).fetchone()
        assert row is not None
        # role is col index 3, content is 4
        assert row[3] == "user"
        assert row[4] == "Hello"

    def test_ordinals_auto_increment(self, conn):
        from src.services.chat_session import create_session, append_message
        sid = create_session("test", conn=conn)
        append_message(sid, "user", "first", conn)
        append_message(sid, "assistant", "second", conn)
        append_message(sid, "user", "third", conn)

        rows = conn.execute(
            "SELECT ordinal, role FROM chat_messages WHERE session_id = ? ORDER BY ordinal",
            (sid,),
        ).fetchall()
        assert [r[0] for r in rows] == [0, 1, 2]
        assert [r[1] for r in rows] == ["user", "assistant", "user"]

    def test_telemetry_fields_stored(self, conn):
        from src.services.chat_session import create_session, append_message
        sid = create_session("test", conn=conn)
        mid = append_message(
            sid, "assistant", "response text", conn,
            model_used="gemini-3-flash-preview",
            tokens_in=500, tokens_out=200,
            cost_usd=0.001, latency_ms=3200,
        )

        row = conn.execute(
            "SELECT model_used, tokens_in, tokens_out, cost_usd, latency_ms "
            "FROM chat_messages WHERE message_id = ?",
            (mid,),
        ).fetchone()
        assert row[0] == "gemini-3-flash-preview"
        assert row[1] == 500
        assert row[2] == 200
        assert abs(row[3] - 0.001) < 1e-6
        assert row[4] == 3200

    def test_tool_calls_stored_as_json(self, conn):
        from src.services.chat_session import create_session, append_message
        sid = create_session("test", conn=conn)
        tools = [{"name": "semantic_search", "args": {"query": "bugs"}}]
        mid = append_message(sid, "assistant", "found it", conn, tool_calls=tools)

        row = conn.execute(
            "SELECT tool_calls FROM chat_messages WHERE message_id = ?", (mid,)
        ).fetchone()
        assert json.loads(row[0]) == tools

    def test_nonexistent_session_returns_none(self, conn):
        from src.services.chat_session import append_message
        result = append_message("fake-id", "user", "Hello", conn)
        assert result is None

    def test_legacy_blob_also_updated(self, conn):
        """The legacy messages JSON blob should also be kept in sync."""
        from src.services.chat_session import create_session, append_message
        sid = create_session("test", conn=conn)
        append_message(sid, "user", "Hello", conn)
        append_message(sid, "assistant", "Hi!", conn)

        row = conn.execute(
            "SELECT messages FROM chat_sessions WHERE session_id = ?", (sid,)
        ).fetchone()
        messages = json.loads(row[0])
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"


# ═══════════════════════════════════════
#  CRUD: load_session → reads chat_messages
# ═══════════════════════════════════════

class TestLoadSessionNew:
    def test_loads_messages_from_chat_messages_table(self, conn):
        from src.services.chat_session import create_session, append_message, load_session
        sid = create_session("test", conn=conn)
        append_message(sid, "user", "What trends?", conn)
        append_message(
            sid, "assistant", "Here are the trends", conn,
            model_used="gemini-3-flash", tokens_in=100, tokens_out=50,
        )

        session = load_session(sid, conn)
        assert session is not None
        assert len(session["messages"]) == 2
        assert session["messages"][0]["role"] == "user"
        assert session["messages"][0]["content"] == "What trends?"
        assert session["messages"][1]["model_used"] == "gemini-3-flash"
        assert session["messages"][1]["tokens_in"] == 100

    def test_load_nonexistent_returns_none(self, conn):
        from src.services.chat_session import load_session
        assert load_session("not-real", conn) is None

    def test_backward_compat_message_structure(self, conn):
        """Loaded messages should have role, content, and timestamp keys."""
        from src.services.chat_session import create_session, append_message, load_session
        sid = create_session("test", conn=conn)
        append_message(sid, "user", "Hello", conn)

        session = load_session(sid, conn)
        msg = session["messages"][0]
        assert "role" in msg
        assert "content" in msg
        assert "timestamp" in msg


# ═══════════════════════════════════════
#  CRUD: list_sessions → v_session_summary
# ═══════════════════════════════════════

class TestListSessionsNew:
    def test_returns_sessions_with_summary(self, conn):
        from src.services.chat_session import create_session, append_message, list_sessions
        sid = create_session("gemini_chats", conn=conn)
        append_message(sid, "user", "Hello", conn)
        append_message(sid, "assistant", "Hi!", conn, tokens_in=50, tokens_out=30)

        sessions = list_sessions(10, conn)
        assert len(sessions) == 1
        s = sessions[0]
        assert s["session_id"] == sid
        assert s["message_count"] == 2
        assert s["user_messages"] == 1
        assert s["assistant_messages"] == 1

    def test_respects_limit(self, conn):
        from src.services.chat_session import create_session, list_sessions
        for i in range(5):
            create_session(f"page_{i}", conn=conn)
        sessions = list_sessions(3, conn)
        assert len(sessions) == 3

    def test_ordered_by_last_message(self, conn):
        from src.services.chat_session import create_session, append_message, list_sessions
        import time
        sid1 = create_session("page_a", conn=conn)
        append_message(sid1, "user", "first", conn)
        time.sleep(0.05)
        sid2 = create_session("page_b", conn=conn)
        append_message(sid2, "user", "second", conn)

        sessions = list_sessions(10, conn)
        assert sessions[0]["session_id"] == sid2  # most recent first


# ═══════════════════════════════════════
#  Projects
# ═══════════════════════════════════════

class TestProjects:
    def test_create_project(self, conn):
        from src.services.chat_session import create_project, list_projects
        pid = create_project("Billing Issues", "All billing chats", conn=conn)
        assert pid is not None

        projects = list_projects(conn=conn)
        assert len(projects) == 1
        assert projects[0]["name"] == "Billing Issues"
        assert projects[0]["session_count"] == 0

    def test_assign_session_to_project(self, conn):
        from src.services.chat_session import (
            create_session, create_project, assign_session_to_project, list_projects,
        )
        pid = create_project("Test Project", conn=conn)
        sid = create_session("gemini_chats", conn=conn)
        assign_session_to_project(sid, pid, conn=conn)

        projects = list_projects(conn=conn)
        assert projects[0]["session_count"] == 1

    def test_unassign_session(self, conn):
        from src.services.chat_session import (
            create_session, create_project, assign_session_to_project, load_session,
        )
        pid = create_project("Test", conn=conn)
        sid = create_session("test", conn=conn)
        assign_session_to_project(sid, pid, conn=conn)
        assign_session_to_project(sid, None, conn=conn)

        # v_session_summary should show no project
        row = conn.execute(
            "SELECT project_id FROM chat_sessions WHERE session_id = ?", (sid,)
        ).fetchone()
        assert row[0] is None


# ═══════════════════════════════════════
#  Virtual Views
# ═══════════════════════════════════════

class TestVirtualViews:
    def test_v_session_summary_aggregates(self, conn):
        from src.services.chat_session import create_session, append_message
        sid = create_session("test", conn=conn)
        append_message(sid, "user", "Q1", conn)
        append_message(sid, "assistant", "A1", conn, tokens_in=100, tokens_out=50, cost_usd=0.001)
        append_message(sid, "user", "Q2", conn)
        append_message(sid, "assistant", "A2", conn, tokens_in=80, tokens_out=40, cost_usd=0.0008)

        row = conn.execute(
            "SELECT message_count, user_messages, assistant_messages, "
            "total_tokens_in, total_tokens_out, total_cost, first_question "
            "FROM v_session_summary WHERE session_id = ?",
            (sid,),
        ).fetchone()

        assert row[0] == 4  # message_count
        assert row[1] == 2  # user_messages
        assert row[2] == 2  # assistant_messages
        assert row[3] == 180  # total_tokens_in
        assert row[4] == 90   # total_tokens_out
        assert abs(row[5] - 0.0018) < 1e-6  # total_cost
        assert row[6] == "Q1"  # first_question

    def test_v_session_tools(self, conn):
        """v_session_tools aggregates tool execution data."""
        sid = "sess-1"
        conn.execute(
            "INSERT INTO chat_sessions (session_id, created_at, updated_at, messages) "
            "VALUES (?, '2026-01-01', '2026-01-01', '[]')",
            (sid,),
        )
        conn.execute(
            "INSERT INTO chat_messages (message_id, session_id, ordinal, role, content) "
            "VALUES ('m1', ?, 0, 'assistant', 'response')", (sid,),
        )
        for i in range(3):
            conn.execute(
                "INSERT INTO chat_tool_executions "
                "(execution_id, message_id, session_id, tool_name, elapsed_ms, result_rows) "
                "VALUES (?, 'm1', ?, 'semantic_search', ?, ?)",
                (str(uuid.uuid4()), sid, 100 + i * 50, 10 + i),
            )

        row = conn.execute(
            "SELECT call_count, total_ms, total_rows FROM v_session_tools "
            "WHERE session_id = ? AND tool_name = 'semantic_search'",
            (sid,),
        ).fetchone()
        assert row[0] == 3   # call_count
        assert row[1] == 450  # total_ms (100+150+200)
        assert row[2] == 33   # total_rows (10+11+12)

    def test_v_chat_cost_daily(self, conn):
        from src.services.chat_session import create_session, append_message
        sid = create_session("test", conn=conn)
        append_message(
            sid, "assistant", "A1", conn,
            model_used="gemini-3-flash", tokens_in=100, tokens_out=50, cost_usd=0.001,
        )
        append_message(
            sid, "assistant", "A2", conn,
            model_used="gemini-3-flash", tokens_in=80, tokens_out=40, cost_usd=0.0008,
        )

        rows = conn.execute("SELECT * FROM v_chat_cost_daily").fetchall()
        assert len(rows) >= 1
        # Should have aggregated both assistant messages
        row = rows[0]
        # sessions col = index 2
        assert row[2] == 1  # 1 session


# ═══════════════════════════════════════
#  FTS5 Search
# ═══════════════════════════════════════

class TestFTS5Search:
    def test_fts5_trigger_populates_on_insert(self, conn):
        from src.services.chat_session import create_session, append_message
        sid = create_session("test", conn=conn)
        append_message(sid, "user", "billing mismatch root cause analysis", conn)
        append_message(sid, "user", "provider sync failure debug", conn)

        rows = conn.execute(
            "SELECT message_id FROM chat_messages_fts WHERE chat_messages_fts MATCH 'billing'"
        ).fetchall()
        assert len(rows) == 1

    def test_search_messages_function(self, conn):
        from src.services.chat_session import create_session, append_message, search_messages
        sid = create_session("test", conn=conn)
        append_message(sid, "user", "Are there billing bugs this quarter?", conn)
        append_message(sid, "assistant", "Found 3 billing issues in March", conn)
        append_message(sid, "user", "Tell me about sync failures", conn)

        results = search_messages("billing", conn=conn)
        assert len(results) == 2  # both mention billing


# ═══════════════════════════════════════
#  Data Migration Post-Hook (JSON → rows)
# ═══════════════════════════════════════

class TestDataMigrationPostHook:
    def test_extracts_json_messages_to_rows(self, conn_pre009):
        """Simulate pre-009 DB with JSON blobs, then run the post-hook."""
        db = conn_pre009
        # Insert sessions with JSON message blobs
        messages = [
            {"role": "user", "content": "Hello", "timestamp": "2026-01-01T10:00:00"},
            {"role": "assistant", "content": "Hi there!", "timestamp": "2026-01-01T10:00:01"},
        ]
        db.execute(
            "INSERT INTO chat_sessions (session_id, created_at, updated_at, messages) "
            "VALUES ('s1', '2026-01-01', '2026-01-01', ?)",
            (json.dumps(messages),),
        )
        db.commit()

        # Now apply migration 009 + post-hook
        db.executescript(_MIG_009)

        from src.updater.schema_migrator import SchemaMigrator
        SchemaMigrator._posthook_009_extract_json_messages(db)

        rows = db.execute(
            "SELECT ordinal, role, content FROM chat_messages "
            "WHERE session_id = 's1' ORDER BY ordinal"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0][1] == "user"
        assert rows[0][2] == "Hello"
        assert rows[1][1] == "assistant"
        assert rows[1][2] == "Hi there!"

    def test_empty_sessions_skipped(self, conn_pre009):
        db = conn_pre009
        db.execute(
            "INSERT INTO chat_sessions (session_id, created_at, updated_at, messages) "
            "VALUES ('s1', '2026-01-01', '2026-01-01', '[]')"
        )
        db.commit()

        db.executescript(_MIG_009)
        from src.updater.schema_migrator import SchemaMigrator
        SchemaMigrator._posthook_009_extract_json_messages(db)

        count = db.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]
        assert count == 0


# ═══════════════════════════════════════
#  Tool Execution Logging
# ═══════════════════════════════════════

class TestToolExecutionLogging:
    def test_dispatch_logs_to_chat_tool_executions(self, conn):
        """dispatch_tool should write a row to chat_tool_executions."""
        sid = "test-session"
        conn.execute(
            "INSERT INTO chat_sessions (session_id, created_at, updated_at, messages) "
            "VALUES (?, '2026-01-01', '2026-01-01', '[]')", (sid,)
        )
        conn.execute(
            "INSERT INTO chat_messages (message_id, session_id, ordinal, role, content) "
            "VALUES ('msg-1', ?, 0, 'assistant', 'response')", (sid,),
        )
        conn.commit()

        from src.data.chat_tools.registry import _persist_tool_execution
        _persist_tool_execution(
            conn=conn,
            message_id="msg-1",
            session_id=sid,
            tool_name="semantic_search",
            args={"query": "billing bugs"},
            result={"tickets": [{"id": 1}, {"id": 2}]},
            result_rows=2,
            tables_touched=["ticket_index"],
            elapsed_ms=340.5,
            error=None,
        )

        rows = conn.execute(
            "SELECT tool_name, args_json, result_rows, elapsed_ms "
            "FROM chat_tool_executions WHERE session_id = ?",
            (sid,),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "semantic_search"
        assert json.loads(rows[0][1])["query"] == "billing bugs"
        assert rows[0][2] == 2
        assert rows[0][3] == 340

    def test_no_session_id_skips_persist(self, conn):
        """Without session_id, no row should be written."""
        from src.data.chat_tools.registry import _persist_tool_execution
        _persist_tool_execution(
            conn=conn, message_id=None, session_id=None,
            tool_name="test", args={}, result=None,
            result_rows=None, tables_touched=None,
            elapsed_ms=10, error=None,
        )
        count = conn.execute("SELECT COUNT(*) FROM chat_tool_executions").fetchone()[0]
        assert count == 0

    def test_result_json_truncated(self, conn):
        """result_json should be truncated to 4KB for PHI safety."""
        sid = "trunc-session"
        conn.execute(
            "INSERT INTO chat_sessions (session_id, created_at, updated_at, messages) "
            "VALUES (?, '2026-01-01', '2026-01-01', '[]')", (sid,)
        )
        conn.execute(
            "INSERT INTO chat_messages (message_id, session_id, ordinal, role, content) "
            "VALUES ('msg-t', ?, 0, 'assistant', 'resp')", (sid,),
        )
        conn.commit()

        from src.data.chat_tools.registry import _persist_tool_execution
        big_result = {"data": "x" * 10000}
        _persist_tool_execution(
            conn=conn, message_id="msg-t", session_id=sid,
            tool_name="test", args={}, result=big_result,
            result_rows=1, tables_touched=None,
            elapsed_ms=10, error=None,
        )

        row = conn.execute(
            "SELECT result_json FROM chat_tool_executions WHERE session_id = ?", (sid,)
        ).fetchone()
        assert len(row[0]) <= 4096


# ═══════════════════════════════════════
#  Backward Compatibility
# ═══════════════════════════════════════

class TestBackwardCompatibility:
    def test_pre009_list_sessions_fallback(self, conn_pre009):
        """list_sessions should work on pre-009 databases via fallback."""
        from src.services.chat_session import create_session, list_sessions
        create_session("page_a", conn=conn_pre009)
        create_session("page_b", conn=conn_pre009)

        sessions = list_sessions(10, conn_pre009)
        assert len(sessions) == 2
        assert "session_id" in sessions[0]

    def test_pre009_load_session_uses_json_blob(self, conn_pre009):
        """load_session should fall back to JSON blob on pre-009 databases."""
        from src.services.chat_session import create_session, load_session
        sid = create_session("test", conn=conn_pre009)
        # Manually inject a message into the JSON blob
        conn_pre009.execute(
            "UPDATE chat_sessions SET messages = ? WHERE session_id = ?",
            (json.dumps([{"role": "user", "content": "Hello", "timestamp": "2026-01-01"}]), sid),
        )
        conn_pre009.commit()

        session = load_session(sid, conn_pre009)
        assert len(session["messages"]) == 1
        assert session["messages"][0]["content"] == "Hello"


# ═══════════════════════════════════════
#  PriorityRateGovernor
# ═══════════════════════════════════════

class TestPriorityRateGovernor:
    def test_chat_priority_always_passes(self):
        from src.agents.rate_governor import PriorityRateGovernor
        gov = PriorityRateGovernor(min_interval=999)  # very slow normal rate
        # Chat should pass instantly even with extreme interval
        assert gov.acquire(priority="chat") is True
        assert gov.acquire(priority="chat") is True
        assert gov.acquire(priority="chat") is True

    def test_normal_priority_uses_rate_limit(self):
        from src.agents.rate_governor import PriorityRateGovernor
        gov = PriorityRateGovernor(min_interval=0.01)
        # First normal call should pass
        assert gov.acquire(priority="normal", timeout=2) is True

    def test_inherits_rate_governor(self):
        from src.agents.rate_governor import PriorityRateGovernor, RateGovernor
        gov = PriorityRateGovernor()
        assert isinstance(gov, RateGovernor)

    def test_chat_logs_to_call_log(self):
        from src.agents.rate_governor import PriorityRateGovernor
        gov = PriorityRateGovernor()
        gov.acquire(priority="chat")
        gov.acquire(priority="chat")
        assert len(gov.call_log) == 2
