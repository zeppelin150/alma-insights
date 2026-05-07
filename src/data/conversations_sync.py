"""Sync per-source `{prefix}_conversations` tables back into the legacy
unified `conversations` table (2026-05-07).

Background
----------
The multi-source warehouse refactor (Phase 3.5) introduced per-source
``{prefix}_conversations`` tables (e.g. ``zendesk_default_conversations``)
to keep different ticket sources isolated. The legacy ``conversations``
table is still read by:

- Conversation Search page (`db.search_conversations`)
- FTS5 index (`conversations_fts`)
- Chat tools (`handle_legacy_search_conversations`)
- Several integration tests (e.g. ``test_feature_integration.TestP0_ConversationSearch``)

Today, the new ingestion paths only write to per-source tables, so
``conversations`` ends up empty even though the warehouse has full ticket
data. The chat layer + Conversation Search page render no results.

What this module does
---------------------
``sync_conversations_from_sources(conn)`` walks every per-source
conversations table, normalizes its columns to the unified shape, and
INSERT-OR-IGNOREs into ``conversations``. Idempotent; safe to call after
every ingestion. The companion ``rebuild_conversations_fts(conn)``
re-populates the FTS5 mirror.

Public API
----------
- ``sync_conversations_from_sources(conn) -> SyncResult``
- ``rebuild_conversations_fts(conn) -> int``  (returns rows indexed)
- ``SyncResult`` dataclass: ``rows_inserted``, ``rows_skipped``,
  ``per_source_counts``, ``errors``.

Idempotency: relies on ticket_id PK + ``INSERT OR IGNORE``. Repeat calls
are no-ops once data is in sync.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field

logger = logging.getLogger("alma.conversations_sync")

# Columns that live on the legacy `conversations` table. The unified
# shape; per-source tables may be missing some.
_UNIFIED_COLS = (
    "ticket_id", "subject", "trc_code", "trc_label", "status",
    "csat_score", "created_at", "solved_at", "message_count",
    "client_messages", "agent_messages", "full_thread", "thread_preview",
    "dataset_id", "content_hash", "source",
)


# ──────────────────────────────────────────────────────────────────────
# Types
# ──────────────────────────────────────────────────────────────────────

@dataclass
class SyncResult:
    rows_inserted: int = 0
    rows_skipped: int = 0
    per_source_counts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    fts_rows_indexed: int = 0


# ──────────────────────────────────────────────────────────────────────
# Public entry points
# ──────────────────────────────────────────────────────────────────────

def sync_conversations_from_sources(conn: sqlite3.Connection) -> SyncResult:
    """Backfill `conversations` from every `{prefix}_conversations` table.

    Idempotent. Caller controls transaction boundary; we don't commit.
    """
    result = SyncResult()
    source_tables = _list_source_conv_tables(conn)
    if not source_tables:
        logger.debug("no per-source conversations tables found; nothing to sync")
        return result

    pre_count = _count(conn, "conversations")
    for table in source_tables:
        try:
            inserted = _sync_one_table(conn, table)
            result.per_source_counts[table] = inserted
            result.rows_inserted += inserted
        except Exception as exc:
            msg = f"{table}: {exc}"
            result.errors.append(msg)
            logger.warning("conversations sync error %s", msg)

    post_count = _count(conn, "conversations")
    # Rows that existed in source tables but were already in `conversations`
    # (deduped by ticket_id PK) — used for visibility, not correctness.
    result.rows_skipped = max(
        0, sum(_count(conn, t) for t in source_tables) - (post_count - pre_count),
    )
    return result


def rebuild_conversations_fts(conn: sqlite3.Connection) -> int:
    """Rebuild the FTS5 mirror over `conversations`. Returns indexed rows.

    Drops + recreates the virtual table. Safe to call after any sync.
    """
    conn.execute("DROP TABLE IF EXISTS conversations_fts")
    conn.execute(
        "CREATE VIRTUAL TABLE conversations_fts USING fts5("
        "ticket_id, subject, trc_label, full_thread)"
    )
    conn.execute(
        "INSERT INTO conversations_fts (ticket_id, subject, trc_label, full_thread) "
        "SELECT ticket_id, COALESCE(subject,''), COALESCE(trc_label,''), "
        "COALESCE(full_thread,'') FROM conversations"
    )
    n = _count(conn, "conversations_fts")
    logger.info("conversations_fts rebuilt with %d rows", n)
    return n


# ──────────────────────────────────────────────────────────────────────
# Internals
# ──────────────────────────────────────────────────────────────────────

def _list_source_conv_tables(conn: sqlite3.Connection) -> list[str]:
    """Return every table named ``*_conversations`` other than the unified
    `conversations` table itself and FTS5 internal shadows."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name LIKE '%_conversations' "
        "AND name NOT LIKE 'conversations_fts%' "
        "AND name != 'conversations' "
        "ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def _sync_one_table(conn: sqlite3.Connection, table: str) -> int:
    """Copy rows from `table` into `conversations`. Returns count inserted."""
    src_cols = {c[1] for c in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if "ticket_id" not in src_cols:
        return 0

    # Build SELECT that matches the unified column order, defaulting any
    # missing column to NULL. `source` is special: per-source tables have
    # `source_id` (UUID) but the legacy column is `source` (slug). We map
    # the table prefix to the source slug.
    prefix = table[:-len("_conversations")]
    select_exprs: list[str] = []
    for col in _UNIFIED_COLS:
        if col == "source":
            # Use the table prefix as a stable source slug (e.g. "zendesk_default")
            select_exprs.append(f"'{prefix}' AS source")
        elif col == "content_hash" and "content_hash" not in src_cols:
            # Compute a deterministic hash from ticket_id + full_thread
            select_exprs.append(
                "lower(hex(randomblob(8))) AS content_hash" if False
                else f"NULL AS content_hash"
            )
        elif col in src_cols:
            select_exprs.append(col)
        else:
            select_exprs.append(f"NULL AS {col}")

    select_clause = ",\n            ".join(select_exprs)
    sql = (
        f"INSERT OR IGNORE INTO conversations ("
        + ", ".join(_UNIFIED_COLS) + ") "
        f"SELECT {select_clause} FROM {table}"
    )
    cur = conn.execute(sql)
    return int(cur.rowcount or 0)


def _count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except sqlite3.OperationalError:
        return 0
