"""
Alma Insights — Schema Builder
Dynamic DDL generation for per-source tables.
Creates tickets, conversations, comments, and FTS tables for each registered source.
"""

import logging

logger = logging.getLogger("alma.schema_builder")


def create_source_tables(conn, table_prefix: str):
    """Create the standard table set for a data source.

    Creates:
        - {prefix}_tickets
        - {prefix}_conversations
        - {prefix}_comments
        - {prefix}_fts (FTS5 virtual table)

    All tables use IF NOT EXISTS for idempotency.
    """
    _create_tickets_table(conn, table_prefix)
    _create_conversations_table(conn, table_prefix)
    _create_comments_table(conn, table_prefix)
    _create_fts_table(conn, table_prefix)
    conn.commit()
    logger.info("Created table set for prefix: %s", table_prefix)


def _create_tickets_table(conn, prefix: str):
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS [{prefix}_tickets] (
            ticket_id       TEXT PRIMARY KEY,
            subject         TEXT,
            trc_code        TEXT,
            trc_label       TEXT,
            status          TEXT,
            priority        TEXT,
            channel         TEXT,
            csat_score      REAL,
            created_at      TEXT,
            updated_at      TEXT,
            solved_at       TEXT,
            requester_name  TEXT,
            requester_email TEXT,
            assignee_name   TEXT,
            group_name      TEXT,
            tags            TEXT,
            custom_fields   TEXT,
            assignment_to_resolution_hours  REAL,
            total_resolution_hours          REAL,
            first_reply_hours               REAL,
            requester_hash  TEXT DEFAULT '',
            provider_id     TEXT,
            client_id       TEXT,
            source_id       TEXT NOT NULL DEFAULT '{prefix}',
            imported_at     TEXT,
            metadata        TEXT DEFAULT '{{}}'
        )
    """)


def _create_conversations_table(conn, prefix: str):
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS [{prefix}_conversations] (
            ticket_id       TEXT PRIMARY KEY,
            subject         TEXT,
            trc_code        TEXT,
            trc_label       TEXT,
            status          TEXT,
            csat_score      REAL,
            created_at      TEXT,
            solved_at       TEXT,
            message_count   INTEGER,
            client_messages INTEGER,
            agent_messages  INTEGER,
            full_thread     TEXT,
            thread_preview  TEXT,
            dataset_id      INTEGER DEFAULT 0,
            source_id       TEXT NOT NULL DEFAULT '{prefix}',
            FOREIGN KEY (ticket_id) REFERENCES [{prefix}_tickets](ticket_id)
        )
    """)


def _create_comments_table(conn, prefix: str):
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS [{prefix}_comments] (
            comment_id      TEXT PRIMARY KEY,
            ticket_id       TEXT,
            author_name     TEXT,
            author_role     TEXT,
            body            TEXT,
            is_public       INTEGER DEFAULT 1,
            created_at      TEXT,
            source_id       TEXT NOT NULL DEFAULT '{prefix}',
            FOREIGN KEY (ticket_id) REFERENCES [{prefix}_tickets](ticket_id)
        )
    """)


def _create_fts_table(conn, prefix: str):
    conn.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS [{prefix}_fts] USING fts5(
            ticket_id, subject, trc_label, full_thread
        )
    """)
