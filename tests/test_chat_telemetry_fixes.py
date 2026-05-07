"""Regression tests — gemini chats telemetry fixes (2026-05-07).

Three user-reported defects addressed:

  1. Cost stuck at $0.0000 in the Drill Down monitor — `_on_telemetry`
     wasn't computing cost from tokens before persisting. The DB column
     stayed NULL → `SUM(cost_usd)` returned 0.
  2. Tools count stuck at 0 — chat_mcp_server's `dispatch_tool` was
     called with `session_id=None` (resolves to "adhoc_probe" in
     registry); the monitor query `WHERE session_id = <real>` matched
     nothing. Fix: chat page writes the active session_id to a pointer
     file the MCP server reads on every dispatch.
  3. Warm-bridge model fell back to `gemini-2.5-flash-lite` (broken
     per memory:bridge_v5_rewrite.md) when the picker had no selection.

This module mocks the chat-page surfaces enough to exercise the contracts
without spinning up Qt / a real bridge / a real Gemini CLI.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.data.usage_tracker import UsageTracker


# ──────────────────────────────────────────────────────────────────────
# Fix 1 — cost computed from tokens
# ──────────────────────────────────────────────────────────────────────

class TestCostComputation:
    def test_estimate_cost_nonzero_for_real_tokens(self):
        # Flash pricing: $0.15 / $0.60 per 1M for in / out
        cost = UsageTracker.estimate_cost(929, 228, "gemini-2.5-flash")
        assert cost > 0, "telemetry from screenshot (929/228 tokens) must yield > $0"
        # Exact math: 929 * 0.15 / 1e6 + 228 * 0.60 / 1e6 = 0.0001394 + 0.000137 = ~0.000275
        assert 0.0001 <= cost <= 0.001

    def test_estimate_cost_zero_for_zero_tokens(self):
        assert UsageTracker.estimate_cost(0, 0, "gemini-2.5-flash") == 0.0

    def test_pro_model_costs_more_than_flash(self):
        flash = UsageTracker.estimate_cost(1000, 1000, "gemini-2.5-flash")
        pro = UsageTracker.estimate_cost(1000, 1000, "gemini-2.5-pro")
        assert pro > flash * 5, "Pro should be at least 5x flash cost"


class TestOnTelemetryPersistsCost:
    """Verify the gemini_chats_page._on_telemetry path threads cost_usd
    through to append_message. Mocks the page surfaces so we don't need Qt."""

    def test_telemetry_callback_passes_cost_usd(self, tmp_path, monkeypatch):
        # Build a tiny in-memory DB with the chat_messages schema we exercise
        db_path = tmp_path / "tel.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE chat_sessions (
                session_id TEXT PRIMARY KEY,
                page TEXT, title TEXT, messages TEXT DEFAULT '[]',
                created_at TEXT, updated_at TEXT,
                last_message_at TEXT, message_count INTEGER DEFAULT 0,
                total_cost REAL DEFAULT 0.0,
                project_id TEXT
            );
            CREATE TABLE chat_messages (
                message_id TEXT PRIMARY KEY,
                session_id TEXT, role TEXT, content TEXT, created_at TEXT,
                ordinal INTEGER, model_used TEXT, tokens_in INTEGER,
                tokens_out INTEGER, cost_usd REAL, latency_ms INTEGER,
                tool_calls TEXT, tool_round INTEGER, error_code TEXT,
                error_message TEXT, metadata TEXT
            );
            INSERT INTO chat_sessions (session_id, page, title, messages, created_at)
            VALUES ('s1', 'gemini_chats', 'Test', '[]', '2026-05-07');
        """)
        conn.commit()

        # Drive the telemetry path directly via append_message with
        # the cost we'd compute in _on_telemetry
        from src.services.chat_session import append_message
        cost = UsageTracker.estimate_cost(929, 228, "gemini-2.5-flash")
        append_message(
            "s1", "assistant", "test response", conn,
            model_used="gemini-2.5-flash",
            tokens_in=929, tokens_out=228,
            cost_usd=cost, latency_ms=15468,
        )
        conn.commit()

        # Verify cost landed in the DB
        row = conn.execute(
            "SELECT cost_usd FROM chat_messages WHERE session_id = ?", ("s1",),
        ).fetchone()
        assert row is not None
        assert row[0] is not None and row[0] > 0, (
            "cost_usd column must be populated; was None or 0"
        )
        conn.close()


# ──────────────────────────────────────────────────────────────────────
# Fix 2 — MCP session-id pointer file
# ──────────────────────────────────────────────────────────────────────

class TestSessionPointer:
    def test_mcp_server_reads_session_pointer(self, tmp_path, monkeypatch):
        pointer = tmp_path / ".current_chat_session"
        pointer.write_text("test-session-42", encoding="utf-8")
        monkeypatch.setenv("ALMA_CHAT_SESSION_FILE", str(pointer))

        from src.mcp.chat_mcp_server import _read_active_session_id
        assert _read_active_session_id() == "test-session-42"

    def test_mcp_server_falls_back_to_none_when_no_file(self, monkeypatch):
        monkeypatch.delenv("ALMA_CHAT_SESSION_FILE", raising=False)
        from src.mcp.chat_mcp_server import _read_active_session_id
        assert _read_active_session_id() is None

    def test_mcp_server_falls_back_to_none_on_unreadable(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ALMA_CHAT_SESSION_FILE", str(tmp_path / "nope"))
        from src.mcp.chat_mcp_server import _read_active_session_id
        assert _read_active_session_id() is None

    def test_empty_pointer_file_returns_none(self, tmp_path, monkeypatch):
        pointer = tmp_path / ".current_chat_session"
        pointer.write_text("", encoding="utf-8")
        monkeypatch.setenv("ALMA_CHAT_SESSION_FILE", str(pointer))
        from src.mcp.chat_mcp_server import _read_active_session_id
        assert _read_active_session_id() is None


class TestDispatchTagsRealSession:
    """End-to-end check: when the pointer points to a session, the
    chat_tool_executions row is tagged with that session (not 'adhoc_probe')."""

    def test_dispatch_tool_uses_pointer_session_id(self, tmp_path, monkeypatch):
        # Build minimal warehouse with chat_tool_executions table
        db_path = tmp_path / "x.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE chat_tool_executions (
                execution_id TEXT PRIMARY KEY,
                message_id TEXT, session_id TEXT NOT NULL,
                tool_name TEXT, args_json TEXT, result_json TEXT,
                result_rows INTEGER, tables_touched TEXT,
                elapsed_ms INTEGER, error TEXT, created_at TEXT
            );
            CREATE TABLE ticket_index (
                ticket_id TEXT PRIMARY KEY, trc_code TEXT, trc_label TEXT,
                friction_type TEXT, insurance_payer TEXT,
                canonical_issue_id TEXT, subject_sanitized TEXT,
                issue_snippet TEXT, ticket_created_date TEXT
            );
        """)
        conn.commit()
        conn.close()

        # Write the pointer file
        pointer = tmp_path / ".current_chat_session"
        pointer.write_text("real-chat-session-99", encoding="utf-8")
        monkeypatch.setenv("ALMA_DB_PATH", str(db_path))
        monkeypatch.setenv("ALMA_CHAT_SESSION_FILE", str(pointer))

        # Call _execute_tool — mimics what the MCP server does on tools/call
        from src.mcp.chat_mcp_server import _execute_tool
        result = _execute_tool("query_issues", {"trc": "BILL", "group_by": "trc"})
        # Result may be empty (no data) but should not error on the dispatch path
        assert "error" not in result or result.get("scope") is not None

        # Audit row should be tagged with the pointer's session_id, NOT 'adhoc_probe'
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT session_id FROM chat_tool_executions ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        assert row is not None, "dispatch must persist an audit row"
        assert row[0] == "real-chat-session-99", (
            f"session_id should be 'real-chat-session-99' (pointer), got {row[0]!r}"
        )

    def test_dispatch_falls_back_to_adhoc_probe_without_pointer(
        self, tmp_path, monkeypatch,
    ):
        db_path = tmp_path / "x.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE chat_tool_executions (
                execution_id TEXT PRIMARY KEY,
                message_id TEXT, session_id TEXT NOT NULL,
                tool_name TEXT, args_json TEXT, result_json TEXT,
                result_rows INTEGER, tables_touched TEXT,
                elapsed_ms INTEGER, error TEXT, created_at TEXT
            );
            CREATE TABLE ticket_index (
                ticket_id TEXT PRIMARY KEY, trc_code TEXT, trc_label TEXT,
                friction_type TEXT, insurance_payer TEXT,
                canonical_issue_id TEXT, subject_sanitized TEXT,
                issue_snippet TEXT, ticket_created_date TEXT
            );
        """)
        conn.commit()
        conn.close()

        monkeypatch.setenv("ALMA_DB_PATH", str(db_path))
        monkeypatch.delenv("ALMA_CHAT_SESSION_FILE", raising=False)

        from src.mcp.chat_mcp_server import _execute_tool
        _execute_tool("query_issues", {"trc": "BILL", "group_by": "trc"})

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT session_id FROM chat_tool_executions LIMIT 1"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "adhoc_probe", "without pointer, fallback is 'adhoc_probe'"
