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
                custom_fields   TEXT,
                assignment_to_resolution_hours  REAL,
                total_resolution_hours          REAL,
                first_reply_hours               REAL
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

            -- ═══ TERM INTELLIGENCE (Pass 1.5) ═══

            CREATE TABLE IF NOT EXISTS user_terms (
                term_id         INTEGER PRIMARY KEY AUTOINCREMENT,
                term            TEXT NOT NULL,
                canonical_form  TEXT NOT NULL,
                action          TEXT NOT NULL,
                weight_modifier REAL DEFAULT 1.0,
                alias_of        TEXT DEFAULT '',
                notes           TEXT DEFAULT '',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                UNIQUE(term, action)
            );

            CREATE TABLE IF NOT EXISTS tfidf_feedback (
                feedback_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                term            TEXT NOT NULL,
                feedback_type   TEXT NOT NULL,
                context         TEXT DEFAULT '',
                merge_target    TEXT DEFAULT '',
                created_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS discovered_compounds (
                compound_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                phrase          TEXT NOT NULL UNIQUE,
                normalized      TEXT NOT NULL,
                frequency       INTEGER DEFAULT 0,
                pmi_score       REAL DEFAULT 0.0,
                status          TEXT DEFAULT 'candidate',
                first_seen      TEXT NOT NULL,
                last_seen       TEXT NOT NULL
            );

            -- ═══ INCIDENT MONITORING ═══

            CREATE TABLE IF NOT EXISTS hourly_counts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                date            TEXT NOT NULL,
                hour            INTEGER NOT NULL,
                trc_code        TEXT NOT NULL,
                ticket_count    INTEGER NOT NULL DEFAULT 0,
                UNIQUE(date, hour, trc_code)
            );

            CREATE TABLE IF NOT EXISTS trc_baselines (
                trc_code        TEXT PRIMARY KEY,
                hourly_mean     REAL NOT NULL,
                hourly_std      REAL NOT NULL,
                theta_1_upper   REAL NOT NULL,
                theta_2_upper   REAL NOT NULL,
                theta_1_lower   REAL NOT NULL,
                theta_2_lower   REAL NOT NULL,
                sample_hours    INTEGER NOT NULL,
                window_days     INTEGER NOT NULL,
                last_updated    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS incident_flags (
                flag_id         INTEGER PRIMARY KEY AUTOINCREMENT,
                trc_code        TEXT NOT NULL,
                theta_level     INTEGER NOT NULL,
                direction       TEXT NOT NULL,
                triggered_at    TEXT NOT NULL,
                triggered_date  TEXT NOT NULL,
                triggered_hour  INTEGER NOT NULL,
                observed_rate   REAL NOT NULL,
                expected_mean   REAL NOT NULL,
                expected_std    REAL NOT NULL,
                z_score         REAL NOT NULL,
                status          TEXT NOT NULL DEFAULT 'open',
                acknowledged_by TEXT DEFAULT '',
                notes           TEXT DEFAULT '',
                resolved_at     TEXT DEFAULT '',
                created_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS analysis_reports (
                report_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                page            TEXT NOT NULL,
                run_at          TEXT NOT NULL,
                parameters      TEXT NOT NULL,
                summary         TEXT NOT NULL,
                full_results    TEXT DEFAULT '',
                ticket_count    INTEGER NOT NULL DEFAULT 0,
                duration_ms     INTEGER NOT NULL DEFAULT 0,
                notes           TEXT DEFAULT ''
            );

            -- ═══ THETA ANOMALY DETECTION (Pass 1.5) ═══

            CREATE TABLE IF NOT EXISTS daily_baselines (
                baseline_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                date            TEXT NOT NULL,
                trc_code        TEXT NOT NULL,
                metric_type     TEXT NOT NULL,
                metric_key      TEXT DEFAULT '',
                value           REAL NOT NULL,
                UNIQUE(date, trc_code, metric_type, metric_key)
            );

            CREATE TABLE IF NOT EXISTS rolling_stats (
                stat_id         INTEGER PRIMARY KEY AUTOINCREMENT,
                trc_code        TEXT NOT NULL,
                metric_type     TEXT NOT NULL,
                metric_key      TEXT DEFAULT '',
                rolling_mean    REAL NOT NULL,
                rolling_std     REAL NOT NULL,
                sample_count    INTEGER NOT NULL,
                last_updated    TEXT NOT NULL,
                UNIQUE(trc_code, metric_type, metric_key)
            );

            CREATE TABLE IF NOT EXISTS anomaly_flags (
                flag_id         INTEGER PRIMARY KEY AUTOINCREMENT,
                date            TEXT NOT NULL,
                trc_code        TEXT NOT NULL,
                metric_type     TEXT NOT NULL,
                metric_key      TEXT DEFAULT '',
                observed_value  REAL NOT NULL,
                expected_mean   REAL NOT NULL,
                expected_std    REAL NOT NULL,
                z_score         REAL NOT NULL,
                theta_level     INTEGER NOT NULL,
                status          TEXT DEFAULT 'open',
                notes           TEXT DEFAULT '',
                created_at      TEXT NOT NULL,
                resolved_at     TEXT DEFAULT ''
            );

            -- ═══ INDEXES ═══
            CREATE INDEX IF NOT EXISTS idx_comments_ticket ON comments(ticket_id);
            CREATE INDEX IF NOT EXISTS idx_tickets_trc ON tickets(trc_code);
            CREATE INDEX IF NOT EXISTS idx_tickets_created ON tickets(created_at);
            CREATE INDEX IF NOT EXISTS idx_conversations_trc ON conversations(trc_code);
            CREATE INDEX IF NOT EXISTS idx_conversations_created ON conversations(created_at);
            CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);

            -- Pass 1.5 indexes
            CREATE INDEX IF NOT EXISTS idx_user_terms_action ON user_terms(action);
            CREATE INDEX IF NOT EXISTS idx_feedback_term ON tfidf_feedback(term);
            CREATE INDEX IF NOT EXISTS idx_discovered_status ON discovered_compounds(status);
            CREATE INDEX IF NOT EXISTS idx_baselines_date ON daily_baselines(date);
            CREATE INDEX IF NOT EXISTS idx_baselines_trc ON daily_baselines(trc_code);
            CREATE INDEX IF NOT EXISTS idx_anomalies_date ON anomaly_flags(date);
            CREATE INDEX IF NOT EXISTS idx_anomalies_status ON anomaly_flags(status);
            CREATE INDEX IF NOT EXISTS idx_anomalies_theta ON anomaly_flags(theta_level);

            -- Incident monitoring indexes
            CREATE INDEX IF NOT EXISTS idx_hourly_date ON hourly_counts(date);
            CREATE INDEX IF NOT EXISTS idx_hourly_trc ON hourly_counts(trc_code);
            CREATE INDEX IF NOT EXISTS idx_incident_flags_status ON incident_flags(status);
            CREATE INDEX IF NOT EXISTS idx_incident_flags_trc ON incident_flags(trc_code);
            CREATE INDEX IF NOT EXISTS idx_incident_flags_date ON incident_flags(triggered_date);
            CREATE INDEX IF NOT EXISTS idx_reports_page ON analysis_reports(page);
            CREATE INDEX IF NOT EXISTS idx_reports_date ON analysis_reports(run_at);
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
        self._migrate()

    def _migrate(self):
        """Add columns/tables that may be missing in older databases."""
        # Column migrations for tickets table
        existing = {
            row[1] for row in self.conn.execute("PRAGMA table_info(tickets)").fetchall()
        }
        migrations = [
            ("assignment_to_resolution_hours", "REAL"),
            ("total_resolution_hours", "REAL"),
            ("first_reply_hours", "REAL"),
        ]
        for col_name, col_type in migrations:
            if col_name not in existing:
                self.conn.execute(
                    f"ALTER TABLE tickets ADD COLUMN {col_name} {col_type}"
                )

        # Table-level migrations for Pass 1.5 tables
        # (CREATE TABLE IF NOT EXISTS handles this in initialize(), but if an older
        # DB was opened directly without initialize(), ensure tables exist.)
        existing_tables = {
            row[0] for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        pass15_tables = [
            "user_terms", "tfidf_feedback", "discovered_compounds",
            "daily_baselines", "rolling_stats", "anomaly_flags",
            "hourly_counts", "trc_baselines", "incident_flags", "analysis_reports",
        ]
        if not all(t in existing_tables for t in pass15_tables):
            # Re-run initialize to create missing tables
            # (initialize uses IF NOT EXISTS, safe to re-run)
            self.initialize()

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
                 requester_name, requester_email, assignee_name, group_name, tags, custom_fields,
                 assignment_to_resolution_hours, total_resolution_hours, first_reply_hours)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
            ticket.get("assignment_to_resolution_hours"),
            ticket.get("total_resolution_hours"),
            ticket.get("first_reply_hours"),
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
                              csat_min=None, csat_max=None, limit=1000):
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

    # ─── User Terms (Pass 1.5) ───

    def get_user_terms(self, action=None):
        """Return user term overrides, optionally filtered by action."""
        if action:
            rows = self.conn.execute(
                "SELECT * FROM user_terms WHERE action = ? ORDER BY updated_at DESC", (action,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM user_terms ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_user_term(self, term, canonical_form, action,
                         weight_modifier=1.0, alias_of="", notes=""):
        """Insert or update a user term override."""
        now = datetime.now().isoformat()
        self.conn.execute("""
            INSERT INTO user_terms
                (term, canonical_form, action, weight_modifier, alias_of, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(term, action) DO UPDATE SET
                canonical_form = excluded.canonical_form,
                weight_modifier = excluded.weight_modifier,
                alias_of = excluded.alias_of,
                notes = excluded.notes,
                updated_at = excluded.updated_at
        """, (term, canonical_form, action, weight_modifier, alias_of, notes, now, now))
        self.conn.commit()

    def delete_user_term(self, term, action=None):
        """Remove a user term override."""
        if action:
            self.conn.execute(
                "DELETE FROM user_terms WHERE term = ? AND action = ?", (term, action)
            )
        else:
            self.conn.execute("DELETE FROM user_terms WHERE term = ?", (term,))
        self.conn.commit()

    # ─── TF-IDF Feedback (Pass 1.5) ───

    def log_tfidf_feedback(self, term, feedback_type, context="", merge_target=""):
        """Record a feedback action in the tfidf_feedback log."""
        self.conn.execute("""
            INSERT INTO tfidf_feedback (term, feedback_type, context, merge_target, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (term, feedback_type, context, merge_target, datetime.now().isoformat()))
        self.conn.commit()

    def get_feedback_summary(self):
        """Aggregate feedback counts by term and type."""
        rows = self.conn.execute("""
            SELECT term, feedback_type, COUNT(*) as cnt
            FROM tfidf_feedback
            GROUP BY term, feedback_type
            ORDER BY cnt DESC
        """).fetchall()
        return [dict(r) for r in rows]

    # ─── Discovered Compounds (Pass 1.5) ───

    def upsert_discovered_compound(self, phrase, normalized, frequency, pmi_score):
        """Insert or update a discovered compound term (doesn't change status)."""
        now = datetime.now().isoformat()
        existing = self.conn.execute(
            "SELECT compound_id, status FROM discovered_compounds WHERE phrase = ?",
            (phrase,)
        ).fetchone()
        if existing:
            self.conn.execute("""
                UPDATE discovered_compounds
                SET frequency = ?, pmi_score = ?, last_seen = ?
                WHERE compound_id = ?
            """, (frequency, pmi_score, now, existing["compound_id"]))
        else:
            self.conn.execute("""
                INSERT INTO discovered_compounds
                    (phrase, normalized, frequency, pmi_score, status, first_seen, last_seen)
                VALUES (?, ?, ?, ?, 'candidate', ?, ?)
            """, (phrase, normalized, frequency, pmi_score, now, now))
        self.conn.commit()

    def get_discovered_compounds(self, status=None):
        """Return discovered compounds, optionally filtered by status."""
        if status:
            rows = self.conn.execute(
                "SELECT * FROM discovered_compounds WHERE status = ? ORDER BY pmi_score DESC",
                (status,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM discovered_compounds ORDER BY pmi_score DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def update_compound_status(self, phrase, status):
        """Activate or reject a discovered compound."""
        self.conn.execute(
            "UPDATE discovered_compounds SET status = ? WHERE phrase = ?",
            (status, phrase)
        )
        self.conn.commit()

    # ─── Daily Baselines (Pass 1.5) ───

    def upsert_daily_baseline(self, date, trc_code, metric_type, metric_key, value):
        """Insert or replace a daily baseline row."""
        self.conn.execute("""
            INSERT OR REPLACE INTO daily_baselines
                (date, trc_code, metric_type, metric_key, value)
            VALUES (?, ?, ?, ?, ?)
        """, (date, trc_code, metric_type, metric_key, value))

    def get_daily_baselines(self, trc_code, days_back=90):
        """Return recent daily baselines for a TRC."""
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        rows = self.conn.execute("""
            SELECT * FROM daily_baselines
            WHERE trc_code = ? AND date >= ?
            ORDER BY date ASC
        """, (trc_code, cutoff)).fetchall()
        return [dict(r) for r in rows]

    # ─── Rolling Stats (Pass 1.5) ───

    def upsert_rolling_stats(self, trc_code, metric_type, metric_key,
                              rolling_mean, rolling_std, sample_count):
        """Insert or replace rolling EWMA stats."""
        self.conn.execute("""
            INSERT OR REPLACE INTO rolling_stats
                (trc_code, metric_type, metric_key, rolling_mean, rolling_std,
                 sample_count, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (trc_code, metric_type, metric_key, rolling_mean, rolling_std,
              sample_count, datetime.now().isoformat()))

    def get_rolling_stats(self, trc_code=None):
        """Return rolling stats, optionally for a specific TRC."""
        if trc_code is not None:
            rows = self.conn.execute(
                "SELECT * FROM rolling_stats WHERE trc_code = ?", (trc_code,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM rolling_stats").fetchall()
        return [dict(r) for r in rows]

    # ─── Anomaly Flags (Pass 1.5) ───

    def insert_anomaly_flag(self, date, trc_code, metric_type, metric_key,
                             observed_value, expected_mean, expected_std,
                             z_score, theta_level):
        """Insert a new anomaly flag."""
        existing = self.conn.execute("""
            SELECT flag_id FROM anomaly_flags
            WHERE date = ? AND trc_code = ? AND metric_type = ? AND metric_key = ?
        """, (date, trc_code, metric_type, metric_key)).fetchone()
        if existing:
            self.conn.execute("""
                UPDATE anomaly_flags
                SET observed_value = ?, expected_mean = ?, expected_std = ?,
                    z_score = ?, theta_level = ?
                WHERE flag_id = ?
            """, (observed_value, expected_mean, expected_std,
                  z_score, theta_level, existing["flag_id"]))
        else:
            self.conn.execute("""
                INSERT INTO anomaly_flags
                    (date, trc_code, metric_type, metric_key, observed_value,
                     expected_mean, expected_std, z_score, theta_level, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
            """, (date, trc_code, metric_type, metric_key, observed_value,
                  expected_mean, expected_std, z_score, theta_level,
                  datetime.now().isoformat()))
        self.conn.commit()

    def get_anomaly_flags(self, status=None, theta_level=None, limit=100):
        """Return anomaly flags with optional filters."""
        conditions = []
        params = []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if theta_level is not None:
            conditions.append("theta_level = ?")
            params.append(theta_level)
        where = " AND ".join(conditions) if conditions else "1=1"
        query = f"""
            SELECT * FROM anomaly_flags
            WHERE {where}
            ORDER BY date DESC, theta_level DESC, abs(z_score) DESC
            LIMIT ?
        """
        params.append(limit)
        return [dict(r) for r in self.conn.execute(query, params).fetchall()]

    def update_anomaly_status(self, flag_id, status, notes=""):
        """Update status of an anomaly flag."""
        resolved_at = datetime.now().isoformat() if status in ("resolved", "false_positive") else ""
        self.conn.execute("""
            UPDATE anomaly_flags
            SET status = ?, notes = ?, resolved_at = ?
            WHERE flag_id = ?
        """, (status, notes, resolved_at, flag_id))
        self.conn.commit()

    def get_open_flag_count(self, theta_level=None):
        """Count open anomaly flags. Used for sidebar badge."""
        if theta_level is not None:
            row = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM anomaly_flags WHERE status = 'open' AND theta_level = ?",
                (theta_level,)
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM anomaly_flags WHERE status = 'open'"
            ).fetchone()
        return row["cnt"]

    # ─── Hourly Counts (Incident Monitoring) ───

    def populate_hourly_counts(self):
        """
        Rebuild hourly_counts from conversations.created_at.
        Call after data import. Idempotent — deletes and re-inserts.
        """
        self.conn.execute("DELETE FROM hourly_counts")
        self.conn.execute("""
            INSERT INTO hourly_counts (date, hour, trc_code, ticket_count)
            SELECT
                DATE(created_at) as date,
                CAST(STRFTIME('%H', created_at) AS INTEGER) as hour,
                trc_code,
                COUNT(*) as ticket_count
            FROM conversations
            WHERE trc_code != '' AND created_at IS NOT NULL AND created_at != ''
            GROUP BY DATE(created_at), CAST(STRFTIME('%H', created_at) AS INTEGER), trc_code
        """)
        self.conn.commit()

    def get_hourly_series(self, trc_code, date_from, date_to):
        """Get hourly ticket counts for a TRC in a date range."""
        rows = self.conn.execute("""
            SELECT date, hour, ticket_count
            FROM hourly_counts
            WHERE trc_code = ? AND date >= ? AND date <= ?
            ORDER BY date ASC, hour ASC
        """, (trc_code, date_from, date_to)).fetchall()
        return [dict(r) for r in rows]

    # ─── Report History ───

    def save_report(self, page, parameters, summary, full_results="",
                    ticket_count=0, duration_ms=0, notes=""):
        """Persist an analysis report. Trims to 500 reports per page."""
        self.conn.execute("""
            INSERT INTO analysis_reports
                (page, run_at, parameters, summary, full_results,
                 ticket_count, duration_ms, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            page,
            datetime.now().isoformat(),
            json.dumps(parameters) if isinstance(parameters, dict) else parameters,
            json.dumps(summary) if isinstance(summary, dict) else summary,
            full_results,
            ticket_count,
            duration_ms,
            notes,
        ))

        # Trim: keep only the latest 500 per page
        self.conn.execute("""
            DELETE FROM analysis_reports
            WHERE page = ? AND report_id NOT IN (
                SELECT report_id FROM analysis_reports
                WHERE page = ?
                ORDER BY run_at DESC
                LIMIT 500
            )
        """, (page, page))

        self.conn.commit()

    def get_reports(self, page, limit=100, offset=0):
        """Get past reports for a page, newest first (no full_results)."""
        rows = self.conn.execute("""
            SELECT report_id, run_at, parameters, summary, ticket_count, duration_ms, notes
            FROM analysis_reports
            WHERE page = ?
            ORDER BY run_at DESC
            LIMIT ? OFFSET ?
        """, (page, limit, offset)).fetchall()
        return [dict(r) for r in rows]

    def get_report_count(self, page):
        """Total reports for a page."""
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM analysis_reports WHERE page = ?", (page,)
        ).fetchone()
        return row["cnt"]

    def get_full_report(self, report_id):
        """Get a single report with full results blob."""
        row = self.conn.execute(
            "SELECT * FROM analysis_reports WHERE report_id = ?", (report_id,)
        ).fetchone()
        return dict(row) if row else None

    # ─── Demo Data ───

    def load_demo_data(self):
        """Load sample data for development and demo purposes."""
        from src.data.demo_data import generate_demo_data
        generate_demo_data(self)
