"""Unit tests for context_injector."""

import sqlite3
import tempfile
import os
import pytest
from pathlib import Path

_MIGRATION_SQL = (
    Path(__file__).resolve().parent.parent.parent / "migrations" / "005_persistence_layer.sql"
).read_text(encoding="utf-8")

_EXTRA_TABLES = """
CREATE TABLE IF NOT EXISTS conversations (ticket_id TEXT PRIMARY KEY, full_thread TEXT);
CREATE TABLE IF NOT EXISTS nlp_scan_runs (scan_id TEXT PRIMARY KEY, status TEXT, started_at TEXT);
"""


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript(_MIGRATION_SQL)
    conn.executescript(_EXTRA_TABLES)

    # Seed ticket_index
    for i in range(5):
        conn.execute(
            """INSERT INTO ticket_index
            (ticket_id, first_seen_scan_id, last_seen_scan_id, first_seen_date,
             trc_code, sub_pattern, anomaly_flag)
            VALUES (?, 's1', 's1', datetime('now'), 'BIL-01', 'dup_charge', ?)""",
            (f"T-{i}", "critical" if i == 0 else None),
        )
    conn.commit()
    conn.close()
    yield path
    os.unlink(path)


def test_build_context_returns_string(db_path):
    from src.services.context_injector import build_context
    ctx = build_context({"page": "gemini_chats"}, db_path)
    assert "[SYSTEM CONTEXT]" in ctx
    assert "gemini_chats" in ctx
    assert "5 tickets" in ctx


def test_build_context_with_filters(db_path):
    from src.services.context_injector import build_context
    ctx = build_context(
        {"page": "ai_reports", "trc_filter": "BIL-01", "date_start": "2025-01-01"},
        db_path,
    )
    assert "BIL-01" in ctx
    assert "alma_query" in ctx
