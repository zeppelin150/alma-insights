"""
Alma Insights — Clear Session Handler (Build 11.0)

Replaces the old "delete DB file" approach.  Wipes ephemeral tables
(raw ticket text, customer data) while preserving persistent tables
(ticket_index, analysis_runs, insight_ledger, etc.).
"""

import logging

from src.data.connection_factory import get_connection, atomic

logger = logging.getLogger("alma.clear_session")

EPHEMERAL_TABLES = [
    "raw_ingestion_rows",
    "ingestion_chunks",
    "nlp_batches",
    "conversations",  # Session 7: conversations clear on Close; tickets/comments persist
    # nlp_ticket_classifications, ticket_entities, datasets — needed for scan history / entity search
]


def clear_session_data(db_path: str):
    """Wipe all ephemeral tables.  Persistent tables survive intact.

    Steps:
      1. DELETE FROM each ephemeral table (skip if table doesn't exist).
      2. Drop and recreate the FTS5 virtual table (can't DELETE from it).
      3. VACUUM to physically reclaim disk space.
    """
    conn = get_connection(db_path)
    try:
        # Deliberately disable FK for ephemeral table wipes
        conn.execute("PRAGMA foreign_keys = OFF")

        existing = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        with atomic(conn):
            for table in EPHEMERAL_TABLES:
                if table in existing:
                    conn.execute(f"DELETE FROM {table}")  # noqa: S608 — controlled list
                    logger.info("Cleared ephemeral table: %s", table)

            # FTS5 virtual tables can't use DELETE — drop + recreate
            if "conversations_fts" in existing:
                conn.execute("DROP TABLE conversations_fts")
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS conversations_fts USING fts5(
                    ticket_id, subject, trc_label, full_thread
                )
                """
            )

            # NOTE: Per-source tables (*_conversations, *_tickets, *_fts) are PERSISTENT
            # warehouse data — NOT cleared here. Only the shared 'conversations' table
            # (the ephemeral ingestion buffer) is cleared above.

        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("VACUUM")  # Must run outside a transaction
        logger.info("Session data cleared. Persistent tables preserved.")
    finally:
        conn.close()
