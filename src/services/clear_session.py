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


# ──────────────────────────────────────────────────────────────────
# Conversation-Search session-visibility flag (bug-bash 2026-04-17)
#
# The Conversation Search page reads from the per-source warehouse
# tables, which are intentionally preserved across Clear & Close (the
# Data Warehouse page relies on them). To give the user the "Clear &
# Close empties the Conversation Search page" behaviour without
# destroying the warehouse, we track a flag in a lazy `app_state`
# table inside the DB itself — NOT in settings.yaml.
#
# Storing the flag in the DB (rather than settings.yaml) means any
# test that uses a tmp DB is automatically isolated; the earlier
# settings.yaml approach leaked writes from every test that called
# clear_session_data() into the user's real settings file.
#
# Set to True by clear_session_data(), read by the Conversation Search
# page, and cleared to False by a successful CSV or Lightdash import.
# ──────────────────────────────────────────────────────────────────

_FLAG_KEY = "conversation_search_hidden"


def _ensure_app_state(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS app_state "
        "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )


def is_conversation_search_hidden(db_path: str) -> bool:
    """True if the user has just run Clear & Close on this DB and no
    new import has loaded data yet. Conversation Search should render
    empty. Returns False for any read failure (safest default)."""
    try:
        conn = get_connection(db_path, readonly=True)
    except Exception:
        return False
    try:
        row = conn.execute(
            "SELECT value FROM app_state WHERE key = ?", (_FLAG_KEY,)
        ).fetchone()
        return bool(row and row[0] == "1")
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def set_conversation_search_hidden(db_path: str, hidden: bool) -> None:
    """Persist the flag in the DB. Import paths call this with False
    after a successful load; clear_session_data() calls it with True."""
    try:
        conn = get_connection(db_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not open DB to set flag: %s", exc)
        return
    try:
        _ensure_app_state(conn)
        with atomic(conn):
            conn.execute(
                "INSERT INTO app_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (_FLAG_KEY, "1" if hidden else "0"),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not persist %s=%s: %s", _FLAG_KEY, hidden, exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass


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

    # Hide the Conversation Search page until the next import. Warehouse
    # tables (what the Data Warehouse page reads) are untouched.
    set_conversation_search_hidden(db_path, True)
