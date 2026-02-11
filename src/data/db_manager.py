"""
Alma Insights — Database Manager
SQLite backend for ticket storage, conversation rebuilding, and full-text search.
"""

import sqlite3
import os
import json
from datetime import datetime
from pathlib import Path


DB_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DB_PATH = DB_DIR / "local_warehouse.db"


class DatabaseManager:
    """Manages the local SQLite database for ticket and conversation data."""

    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = None
        # Restrict DB file permissions on Unix
        self._secure_permissions()

    @property
    def conn(self):
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def initialize(self):
        """Create all tables and indexes."""
        self.conn.executescript("""
            -- ═══ TICKETS ═══
            CREATE TABLE IF NOT EXISTS tickets (
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
                custom_fields   TEXT
            );

            -- ═══ COMMENTS ═══
            CREATE TABLE IF NOT EXISTS comments (
                comment_id      TEXT PRIMARY KEY,
                ticket_id       TEXT NOT NULL,
                author_name     TEXT,
                author_role     TEXT,
                body            TEXT,
                is_public       INTEGER DEFAULT 1,
                created_at      TEXT,
                FOREIGN KEY (ticket_id) REFERENCES tickets(ticket_id)
            );

            -- ═══ REBUILT CONVERSATIONS ═══
            CREATE TABLE IF NOT EXISTS conversations (
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
                FOREIGN KEY (ticket_id) REFERENCES tickets(ticket_id)
            );

            -- ═══ ANALYSIS LOG ═══
            CREATE TABLE IF NOT EXISTS analysis_log (
                log_id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       TEXT NOT NULL,
                action          TEXT NOT NULL,
                parameters      TEXT,
                ticket_count    INTEGER,
                gemini_invoked  INTEGER DEFAULT 0,
                notes           TEXT
            );

            -- ═══ INDEXES ═══
            CREATE INDEX IF NOT EXISTS idx_comments_ticket ON comments(ticket_id);
            CREATE INDEX IF NOT EXISTS idx_tickets_trc ON tickets(trc_code);
            CREATE INDEX IF NOT EXISTS idx_tickets_created ON tickets(created_at);
            CREATE INDEX IF NOT EXISTS idx_conversations_trc ON conversations(trc_code);
            CREATE INDEX IF NOT EXISTS idx_conversations_created ON conversations(created_at);
        """)

        # FTS5 for full-text search on conversations
        try:
            self.conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS conversations_fts USING fts5(
                    ticket_id,
                    subject,
                    trc_label,
                    full_thread,
                    content=conversations,
                    content_rowid=rowid
                )
            """)
        except sqlite3.OperationalError:
            pass  # FTS5 may already exist

        self.conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def _secure_permissions(self):
        """Restrict file permissions on the DB directory and file (Unix only)."""
        if os.name == "nt":
            return
        try:
            os.chmod(self.db_path.parent, 0o700)
            if self.db_path.exists():
                os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    # ─── Ticket Operations ───

    def upsert_ticket(self, ticket: dict):
        self.conn.execute("""
            INSERT OR REPLACE INTO tickets
                (ticket_id, subject, trc_code, trc_label, status, priority, channel,
                 csat_score, created_at, updated_at, solved_at,
                 requester_name, requester_email, assignee_name, group_name, tags, custom_fields)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            str(ticket.get("ticket_id", "")),
            ticket.get("subject", ""),
            ticket.get("trc_code", ""),
            ticket.get("trc_label", ""),
            ticket.get("status", ""),
            ticket.get("priority", ""),
            ticket.get("channel", ""),
            ticket.get("csat_score"),
            ticket.get("created_at", ""),
            ticket.get("updated_at", ""),
            ticket.get("solved_at", ""),
            ticket.get("requester_name", ""),
            ticket.get("requester_email", ""),
            ticket.get("assignee_name", ""),
            ticket.get("group_name", ""),
            json.dumps(ticket.get("tags", [])),
            json.dumps(ticket.get("custom_fields", {})),
        ))

    def upsert_comment(self, comment: dict):
        self.conn.execute("""
            INSERT OR REPLACE INTO comments
                (comment_id, ticket_id, author_name, author_role, body, is_public, created_at)
            VALUES (?,?,?,?,?,?,?)
        """, (
            str(comment.get("comment_id", "")),
            str(comment.get("ticket_id", "")),
            comment.get("author_name", ""),
            comment.get("author_role", "client"),
            comment.get("body", ""),
            1 if comment.get("is_public", True) else 0,
            comment.get("created_at", ""),
        ))

    def upsert_conversation(self, conv: dict):
        self.conn.execute("""
            INSERT OR REPLACE INTO conversations
                (ticket_id, subject, trc_code, trc_label, status, csat_score,
                 created_at, solved_at, message_count, client_messages, agent_messages,
                 full_thread, thread_preview)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            str(conv["ticket_id"]),
            conv.get("subject", ""),
            conv.get("trc_code", ""),
            conv.get("trc_label", ""),
            conv.get("status", ""),
            conv.get("csat_score"),
            conv.get("created_at", ""),
            conv.get("solved_at", ""),
            conv.get("message_count", 0),
            conv.get("client_messages", 0),
            conv.get("agent_messages", 0),
            conv.get("full_thread", ""),
            conv.get("thread_preview", ""),
        ))

    def commit(self):
        self.conn.commit()

    def rebuild_fts_index(self):
        """Rebuild FTS5 index after bulk inserts."""
        try:
            self.conn.execute("INSERT INTO conversations_fts(conversations_fts) VALUES('rebuild')")
            self.conn.commit()
        except sqlite3.OperationalError:
            pass

    # ─── Query Operations ───

    def get_trc_codes(self):
        """Return distinct TRC codes with labels."""
        rows = self.conn.execute(
            "SELECT DISTINCT trc_code, trc_label FROM conversations WHERE trc_code != '' ORDER BY trc_label"
        ).fetchall()
        return [{"code": r["trc_code"], "label": r["trc_label"]} for r in rows]

    def get_date_range(self):
        """Return min and max created_at dates."""
        row = self.conn.execute(
            "SELECT MIN(created_at) as min_date, MAX(created_at) as max_date FROM conversations"
        ).fetchone()
        return row["min_date"], row["max_date"]

    def search_conversations(self, keyword="", trc_code="", date_from="", date_to="",
                              csat_min=None, csat_max=None, limit=200):
        """Search conversations with filters."""
        conditions = []
        params = []

        if keyword:
            # Use FTS5 for keyword search
            conditions.append(
                "c.ticket_id IN (SELECT ticket_id FROM conversations_fts WHERE conversations_fts MATCH ?)"
            )
            params.append(keyword)

        if trc_code:
            conditions.append("c.trc_code = ?")
            params.append(trc_code)

        if date_from:
            conditions.append("c.created_at >= ?")
            params.append(date_from)

        if date_to:
            conditions.append("c.created_at <= ?")
            params.append(date_to)

        if csat_min is not None:
            conditions.append("c.csat_score >= ?")
            params.append(csat_min)

        if csat_max is not None:
            conditions.append("c.csat_score <= ?")
            params.append(csat_max)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        query = f"""
            SELECT c.ticket_id, c.subject, c.trc_code, c.trc_label, c.status,
                   c.csat_score, c.created_at, c.solved_at,
                   c.message_count, c.client_messages, c.agent_messages,
                   c.thread_preview, c.full_thread
            FROM conversations c
            WHERE {where_clause}
            ORDER BY c.created_at DESC
            LIMIT ?
        """
        params.append(limit)
        return [dict(r) for r in self.conn.execute(query, params).fetchall()]

    def get_conversation(self, ticket_id):
        """Get a single conversation with full thread."""
        row = self.conn.execute(
            "SELECT * FROM conversations WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_ticket_count(self):
        row = self.conn.execute("SELECT COUNT(*) as cnt FROM conversations").fetchone()
        return row["cnt"]

    def get_trc_stats(self):
        """Aggregate stats by TRC code."""
        return [dict(r) for r in self.conn.execute("""
            SELECT trc_code, trc_label,
                   COUNT(*) as ticket_count,
                   AVG(csat_score) as avg_csat,
                   AVG(message_count) as avg_messages
            FROM conversations
            WHERE trc_code != ''
            GROUP BY trc_code, trc_label
            ORDER BY ticket_count DESC
        """).fetchall()]

    def log_analysis(self, action, parameters=None, ticket_count=0, gemini_invoked=False, notes=""):
        self.conn.execute("""
            INSERT INTO analysis_log (timestamp, action, parameters, ticket_count, gemini_invoked, notes)
            VALUES (?,?,?,?,?,?)
        """, (
            datetime.now().isoformat(),
            action,
            json.dumps(parameters) if parameters else None,
            ticket_count,
            1 if gemini_invoked else 0,
            notes,
        ))
        self.conn.commit()

    # ─── Demo Data ───

    def load_demo_data(self):
        """Load sample data for development and demo purposes."""
        from src.data.demo_data import generate_demo_data
        generate_demo_data(self)
