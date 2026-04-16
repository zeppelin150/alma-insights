"""Unit tests for chat_session — CRUD operations."""

import sqlite3
import json
import pytest
from pathlib import Path

_MIGRATION_SQL = (
    Path(__file__).resolve().parent.parent.parent / "migrations" / "005_persistence_layer.sql"
).read_text(encoding="utf-8")


@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    db.executescript(_MIGRATION_SQL)
    yield db
    db.close()


class TestCreateSession:
    def test_creates_session(self, conn):
        from src.services.chat_session import create_session
        sid = create_session("ai_reports", {"report": "test"}, {"trc_filter": "BIL-01"}, conn)
        assert sid is not None

        row = conn.execute("SELECT * FROM chat_sessions WHERE session_id = ?", (sid,)).fetchone()
        assert row is not None
        assert row[4] == "ai_reports"  # source_page


class TestAppendMessage:
    def test_appends_messages(self, conn):
        from src.services.chat_session import create_session, append_message, load_session

        sid = create_session("chat", conn=conn)
        append_message(sid, "user", "Hello", conn)
        append_message(sid, "assistant", "Hi there!", conn)

        session = load_session(sid, conn)
        assert len(session["messages"]) == 2
        assert session["messages"][0]["role"] == "user"
        assert session["messages"][1]["content"] == "Hi there!"

    def test_nonexistent_session_no_crash(self, conn):
        from src.services.chat_session import append_message
        append_message("fake-id", "user", "Hello", conn)  # should not raise


class TestListSessions:
    def test_returns_recent(self, conn):
        from src.services.chat_session import create_session, list_sessions

        create_session("page_a", conn=conn)
        create_session("page_b", conn=conn)

        sessions = list_sessions(10, conn)
        assert len(sessions) == 2
        assert sessions[0]["source_page"] in ("page_a", "page_b")

    def test_respects_limit(self, conn):
        from src.services.chat_session import create_session, list_sessions

        for i in range(5):
            create_session(f"page_{i}", conn=conn)

        sessions = list_sessions(3, conn)
        assert len(sessions) == 3


class TestLoadSession:
    def test_load_with_messages(self, conn):
        from src.services.chat_session import create_session, append_message, load_session

        sid = create_session("ai_reports", conn=conn)
        append_message(sid, "user", "What trends?", conn)

        session = load_session(sid, conn)
        assert session["source_page"] == "ai_reports"
        assert len(session["messages"]) == 1

    def test_load_nonexistent_returns_none(self, conn):
        from src.services.chat_session import load_session
        assert load_session("not-real", conn) is None
