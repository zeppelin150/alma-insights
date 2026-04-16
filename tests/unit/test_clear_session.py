"""Unit tests for clear_session — ephemeral wipe, persistent survive."""

import sqlite3
import tempfile
import os
import pytest
from pathlib import Path

_MIGRATION_SQL = (
    Path(__file__).resolve().parent.parent.parent / "migrations" / "005_persistence_layer.sql"
).read_text(encoding="utf-8")

# Minimal schema for ephemeral tables needed by the test
_EPHEMERAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (ticket_id TEXT PRIMARY KEY, subject TEXT);
CREATE TABLE IF NOT EXISTS comments (comment_id TEXT PRIMARY KEY, ticket_id TEXT, body TEXT);
CREATE TABLE IF NOT EXISTS conversations (ticket_id TEXT PRIMARY KEY, full_thread TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS conversations_fts USING fts5(ticket_id, subject, trc_label, full_thread);
CREATE TABLE IF NOT EXISTS nlp_batches (batch_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS nlp_ticket_classifications (classification_id TEXT PRIMARY KEY, ticket_id TEXT);
CREATE TABLE IF NOT EXISTS datasets (dataset_id TEXT PRIMARY KEY);
"""


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript(_EPHEMERAL_SCHEMA)
    conn.executescript(_MIGRATION_SQL)

    # Seed ephemeral data
    conn.execute("INSERT INTO tickets VALUES ('T-1', 'Test ticket')")
    conn.execute("INSERT INTO conversations VALUES ('T-1', 'full thread text')")
    conn.execute("INSERT INTO conversations_fts VALUES ('T-1', 'sub', 'lbl', 'thread')")

    # Seed persistent data
    conn.execute(
        """INSERT INTO ticket_index
        (ticket_id, first_seen_scan_id, last_seen_scan_id, first_seen_date, issue_snippet)
        VALUES ('T-1', 's1', 's1', datetime('now'), 'billing issue')"""
    )
    conn.execute(
        """INSERT INTO insight_ledger
        (insight_id, date_identified, title, status)
        VALUES ('ins-1', datetime('now'), 'Test insight', 'new')"""
    )
    conn.commit()
    conn.close()
    yield path
    os.unlink(path)


def test_clear_preserves_permanent_data(db_path):
    """Session 7: conversations clear on Close; tickets/comments preserved."""
    from src.services.clear_session import clear_session_data
    clear_session_data(db_path)

    conn = sqlite3.connect(db_path)
    # Tickets are permanent (Stage 1 + Session 7)
    assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 1
    # Conversations are ephemeral again (Session 7: clear on Close)
    assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0
    conn.close()


def test_clear_preserves_persistent(db_path):
    from src.services.clear_session import clear_session_data
    clear_session_data(db_path)

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM ticket_index").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM insight_ledger").fetchone()[0] == 1
    conn.close()


def test_fts_recreated(db_path):
    from src.services.clear_session import clear_session_data
    clear_session_data(db_path)

    conn = sqlite3.connect(db_path)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "conversations_fts" in tables
    conn.close()
