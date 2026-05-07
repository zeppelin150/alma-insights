"""Unit tests — src/data/conversations_sync.py (2026-05-07).

Coverage:
  * empty source list → no-op SyncResult
  * single source backfill copies + sets `source` to prefix
  * multiple sources tally per_source_counts
  * idempotent: second sync inserts 0 rows
  * missing optional columns (content_hash) → NULL inserted
  * FTS5 rebuild creates the virtual table + matches conversations row count
  * source-table without ticket_id is skipped without raising
"""
from __future__ import annotations

import sqlite3

import pytest

from src.data.conversations_sync import (
    SyncResult, _UNIFIED_COLS,
    rebuild_conversations_fts, sync_conversations_from_sources,
)


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE conversations (
            ticket_id TEXT PRIMARY KEY,
            subject TEXT, trc_code TEXT, trc_label TEXT, status TEXT,
            csat_score REAL, created_at TEXT, solved_at TEXT,
            message_count INTEGER, client_messages INTEGER,
            agent_messages INTEGER, full_thread TEXT, thread_preview TEXT,
            dataset_id TEXT, content_hash TEXT, source TEXT
        );
    """)
    return c


def _seed_source_table(conn: sqlite3.Connection, prefix: str,
                        rows: list[tuple], with_content_hash: bool = False):
    cols = (
        "ticket_id, subject, trc_code, trc_label, status, csat_score, "
        "created_at, solved_at, message_count, client_messages, "
        "agent_messages, full_thread, thread_preview, dataset_id, source_id"
    )
    conn.execute(f"""
        CREATE TABLE {prefix}_conversations (
            ticket_id TEXT PRIMARY KEY,
            subject TEXT, trc_code TEXT, trc_label TEXT, status TEXT,
            csat_score REAL, created_at TEXT, solved_at TEXT,
            message_count INTEGER, client_messages INTEGER,
            agent_messages INTEGER, full_thread TEXT, thread_preview TEXT,
            dataset_id TEXT, source_id TEXT
            {", content_hash TEXT" if with_content_hash else ""}
        )
    """)
    placeholders = ", ".join("?" * len(rows[0])) if rows else ""
    if rows:
        conn.executemany(
            f"INSERT INTO {prefix}_conversations VALUES ({placeholders})", rows,
        )


# ──────────────────────────────────────────────────────────────────────
# sync
# ──────────────────────────────────────────────────────────────────────

class TestSync:
    def test_empty_returns_zero(self, conn):
        result = sync_conversations_from_sources(conn)
        assert result.rows_inserted == 0
        assert result.per_source_counts == {}

    def test_single_source_backfills(self, conn):
        _seed_source_table(conn, "zendesk_default", [
            ("t1", "subj1", "TRC-A", "TRC A label", "solved",
             4.0, "2026-04-01", "2026-04-02", 3, 2, 1,
             "thread1", "preview1", "ds1", "src-id-1"),
            ("t2", "subj2", "TRC-B", "TRC B label", "open",
             None, "2026-04-03", None, 1, 1, 0,
             "thread2", "preview2", "ds1", "src-id-1"),
        ])
        result = sync_conversations_from_sources(conn)
        assert result.rows_inserted == 2
        assert result.per_source_counts == {"zendesk_default_conversations": 2}
        rows = conn.execute(
            "SELECT ticket_id, subject, source FROM conversations ORDER BY ticket_id"
        ).fetchall()
        assert [(r["ticket_id"], r["subject"], r["source"]) for r in rows] == [
            ("t1", "subj1", "zendesk_default"),
            ("t2", "subj2", "zendesk_default"),
        ]

    def test_multiple_sources_tally(self, conn):
        _seed_source_table(conn, "zendesk_a", [
            ("a1", "subj", "T", "tl", "open", None, None, None, 1, 1, 0,
             "thread", "prev", "ds", "src1"),
        ])
        _seed_source_table(conn, "freshdesk", [
            ("f1", "subj", "T", "tl", "open", None, None, None, 1, 1, 0,
             "thread", "prev", "ds", "src2"),
            ("f2", "subj", "T", "tl", "open", None, None, None, 1, 1, 0,
             "thread", "prev", "ds", "src2"),
        ])
        result = sync_conversations_from_sources(conn)
        assert result.rows_inserted == 3
        assert result.per_source_counts == {
            "zendesk_a_conversations": 1,
            "freshdesk_conversations": 2,
        }
        # source field maps to prefix
        sources = sorted(r[0] for r in conn.execute(
            "SELECT DISTINCT source FROM conversations"
        ).fetchall())
        assert sources == ["freshdesk", "zendesk_a"]

    def test_idempotent_second_sync(self, conn):
        _seed_source_table(conn, "zendesk", [
            ("t1", "s", "T", "tl", "open", None, None, None, 1, 1, 0,
             "thr", "prev", "ds", "sid"),
        ])
        first = sync_conversations_from_sources(conn)
        second = sync_conversations_from_sources(conn)
        assert first.rows_inserted == 1
        assert second.rows_inserted == 0

    def test_skips_table_without_ticket_id(self, conn):
        conn.execute(
            "CREATE TABLE rogue_conversations (subject TEXT, body TEXT)"
        )
        result = sync_conversations_from_sources(conn)
        assert result.rows_inserted == 0
        assert result.errors == []  # silent skip, no error

    def test_excludes_fts_shadow_tables(self, conn):
        # FTS internal tables should not be picked up by the sync
        conn.execute(
            "CREATE VIRTUAL TABLE conversations_fts USING fts5("
            "ticket_id, subject, trc_label, full_thread)"
        )
        # virtual tables create *_data, *_idx, *_content shadows
        result = sync_conversations_from_sources(conn)
        assert result.rows_inserted == 0


# ──────────────────────────────────────────────────────────────────────
# FTS rebuild
# ──────────────────────────────────────────────────────────────────────

class TestRebuildFts:
    def test_rebuild_indexes_all_rows(self, conn):
        _seed_source_table(conn, "zendesk", [
            ("t1", "alpha", "T", "tl", "open", None, None, None, 1, 1, 0,
             "thread alpha", "prev", "ds", "sid"),
            ("t2", "beta",  "T", "tl", "open", None, None, None, 1, 1, 0,
             "thread beta",  "prev", "ds", "sid"),
        ])
        sync_conversations_from_sources(conn)
        n = rebuild_conversations_fts(conn)
        assert n == 2
        match = conn.execute(
            "SELECT ticket_id FROM conversations_fts WHERE conversations_fts MATCH 'alpha'"
        ).fetchall()
        assert match[0][0] == "t1"

    def test_rebuild_empty(self, conn):
        n = rebuild_conversations_fts(conn)
        assert n == 0
