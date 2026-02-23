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

    def _pre_migrate_schemas(self):
        """Drop old-schema tables before CREATE INDEX runs in initialize().

        If the DB has Gaussian-era trc_baselines (hourly_mean) or
        incident_flags (z_score), drop them so initialize() can recreate
        with the Poisson schema.  Safe because these tables are fully
        recomputed from scratch on every scan.
        """
        existing_tables = {
            row[0] for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        if "trc_baselines" in existing_tables:
            cols = {
                row[1] for row in self.conn.execute(
                    "PRAGMA table_info(trc_baselines)"
                ).fetchall()
            }
            if "hourly_mean" in cols and "lambda_daily" not in cols:
                self.conn.execute("DROP TABLE trc_baselines")
                self.conn.commit()

        if "incident_flags" in existing_tables:
            cols = {
                row[1] for row in self.conn.execute(
                    "PRAGMA table_info(incident_flags)"
                ).fetchall()
            }
            if "z_score" in cols and "flag_type" not in cols:
                self.conn.execute("DROP TABLE incident_flags")
                self.conn.commit()

    def initialize(self):
        """Create all tables and indexes."""
        # Pre-flight: drop old-schema tables so CREATE INDEX doesn't fail
        self._pre_migrate_schemas()

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
                first_reply_hours               REAL,
                requester_hash  TEXT DEFAULT ''
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
                dataset_id      INTEGER DEFAULT 0,
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

            CREATE TABLE IF NOT EXISTS daily_counts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                date            TEXT NOT NULL,
                trc_code        TEXT NOT NULL,
                ticket_count    INTEGER NOT NULL DEFAULT 0,
                UNIQUE(date, trc_code)
            );

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
                tier            INTEGER NOT NULL DEFAULT 1,
                lambda_daily    REAL NOT NULL,
                theta_1_daily   INTEGER NOT NULL,
                theta_2_daily   INTEGER NOT NULL,
                cusum_value     REAL NOT NULL DEFAULT 0.0,
                cusum_threshold REAL NOT NULL DEFAULT 5.0,
                cusum_slack     REAL NOT NULL DEFAULT 0.5,
                baseline_days   INTEGER NOT NULL,
                last_updated    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS hourly_baselines (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                trc_code        TEXT NOT NULL,
                hour            INTEGER NOT NULL,
                lambda_hourly   REAL NOT NULL,
                theta_1_hourly  INTEGER NOT NULL,
                theta_2_hourly  INTEGER NOT NULL,
                UNIQUE(trc_code, hour)
            );

            CREATE TABLE IF NOT EXISTS incident_flags (
                flag_id         INTEGER PRIMARY KEY AUTOINCREMENT,
                trc_code        TEXT NOT NULL,
                flag_type       TEXT NOT NULL,
                theta_level     INTEGER NOT NULL,
                direction       TEXT NOT NULL DEFAULT 'above',
                triggered_at    TEXT NOT NULL,
                triggered_date  TEXT NOT NULL,
                triggered_hour  INTEGER DEFAULT NULL,
                observed_value  REAL NOT NULL,
                expected_lambda REAL NOT NULL,
                threshold_value REAL NOT NULL,
                p_value         REAL DEFAULT NULL,
                cusum_value     REAL DEFAULT NULL,
                status          TEXT NOT NULL DEFAULT 'open',
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
                notes           TEXT DEFAULT '',
                report_type     TEXT DEFAULT 'standard',
                chat_history    TEXT DEFAULT '',
                exported_at     TEXT DEFAULT ''
            );

            -- ═══ PROMPT LIBRARY (Pass 3.0) ═══

            CREATE TABLE IF NOT EXISTS prompt_library (
                prompt_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                category        TEXT NOT NULL DEFAULT 'custom',
                description     TEXT DEFAULT '',
                prompt_text     TEXT NOT NULL,
                system_prompt   TEXT DEFAULT '',
                is_active       INTEGER DEFAULT 1,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );

            -- ═══ DATASETS (Pass 3.0) ═══

            CREATE TABLE IF NOT EXISTS datasets (
                dataset_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                label           TEXT NOT NULL DEFAULT 'A',
                file_path       TEXT DEFAULT '',
                imported_at     TEXT NOT NULL,
                ticket_count    INTEGER DEFAULT 0,
                date_min        TEXT DEFAULT '',
                date_max        TEXT DEFAULT '',
                notes           TEXT DEFAULT ''
            );

            -- ═══ INTERVENTIONS (Pass 3.0) ═══

            CREATE TABLE IF NOT EXISTS interventions (
                intervention_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                category        TEXT NOT NULL,
                description     TEXT DEFAULT '',
                event_date      TEXT NOT NULL,
                affected_trcs   TEXT DEFAULT '',
                tags            TEXT DEFAULT '',
                created_by      TEXT DEFAULT '',
                created_at      TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_interventions_date
                ON interventions(event_date);

            -- ═══ TICKET ENTITIES (Pass 3.0) ═══

            CREATE TABLE IF NOT EXISTS ticket_entities (
                ticket_id       TEXT NOT NULL,
                entity_type     TEXT NOT NULL,
                entity_value    TEXT NOT NULL,
                confidence      TEXT DEFAULT 'dictionary',
                extracted_at    TEXT NOT NULL,
                PRIMARY KEY (ticket_id, entity_type, entity_value)
            );

            CREATE INDEX IF NOT EXISTS idx_entities_type_value
                ON ticket_entities(entity_type, entity_value);
            CREATE INDEX IF NOT EXISTS idx_entities_ticket
                ON ticket_entities(ticket_id);

            -- ═══ SMART REPORT RUNS (Pass 3.0) ═══

            CREATE TABLE IF NOT EXISTS smart_report_runs (
                run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at      TEXT NOT NULL,
                completed_at    TEXT DEFAULT '',
                status          TEXT DEFAULT 'running',
                trigger_source  TEXT DEFAULT 'manual',
                ticket_count    INTEGER DEFAULT 0,
                duration_ms     INTEGER DEFAULT 0,
                error_message   TEXT DEFAULT '',
                report_id       INTEGER DEFAULT NULL,
                config_snapshot TEXT NOT NULL
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

            -- ═══ NLP SCAN SYSTEM (Pass 4.0) ═══

            CREATE TABLE IF NOT EXISTS nlp_scan_runs (
                scan_id         TEXT PRIMARY KEY,
                created_at      TEXT NOT NULL,
                status          TEXT NOT NULL,
                date_range_start TEXT NOT NULL,
                date_range_end   TEXT NOT NULL,
                trc_filter      TEXT,
                mode            TEXT NOT NULL,
                batch_strategy  TEXT DEFAULT 'trc',
                total_batches   INTEGER DEFAULT 0,
                completed_batches INTEGER DEFAULT 0,
                total_tickets   INTEGER DEFAULT 0,
                total_comments  INTEGER DEFAULT 0,
                total_input_tokens  INTEGER DEFAULT 0,
                total_output_tokens INTEGER DEFAULT 0,
                estimated_cost_usd  REAL DEFAULT 0.0,
                actual_cost_usd     REAL DEFAULT 0.0,
                budget_cap_usd      REAL DEFAULT 50.0,
                error_log       TEXT,
                config_snapshot TEXT,
                completed_at    TEXT
            );

            CREATE TABLE IF NOT EXISTS nlp_batches (
                batch_id        TEXT PRIMARY KEY,
                scan_id         TEXT NOT NULL,
                batch_number    INTEGER NOT NULL,
                trc             TEXT NOT NULL,
                trc_chunk       INTEGER DEFAULT 1,
                trc_chunk_total INTEGER DEFAULT 1,
                status          TEXT NOT NULL,
                ticket_count    INTEGER DEFAULT 0,
                comment_count   INTEGER DEFAULT 0,
                input_tokens    INTEGER DEFAULT 0,
                output_tokens   INTEGER DEFAULT 0,
                cost_usd        REAL DEFAULT 0.0,
                latency_ms      INTEGER DEFAULT 0,
                prompt_version  TEXT,
                prior_chunks_context TEXT,
                raw_response    TEXT,
                error_message   TEXT,
                retry_count     INTEGER DEFAULT 0,
                worker_id       INTEGER DEFAULT 0,
                created_at      TEXT NOT NULL,
                completed_at    TEXT,
                FOREIGN KEY (scan_id) REFERENCES nlp_scan_runs(scan_id)
            );

            CREATE TABLE IF NOT EXISTS nlp_ticket_classifications (
                classification_id TEXT PRIMARY KEY,
                batch_id        TEXT NOT NULL,
                scan_id         TEXT NOT NULL,
                ticket_id       TEXT NOT NULL,
                trc             TEXT NOT NULL,
                sub_cluster     TEXT,
                sub_cluster_confidence REAL,
                is_novel        INTEGER DEFAULT 0,
                sentiment_intensity INTEGER,
                sentiment_polarity TEXT,
                friction_type   TEXT,
                anomaly_flag    TEXT,
                anomaly_reason  TEXT,
                entities_json   TEXT,
                key_phrases     TEXT,
                root_cause_hint TEXT,
                summary         TEXT,
                raw_classification TEXT,
                created_at      TEXT NOT NULL,
                FOREIGN KEY (batch_id) REFERENCES nlp_batches(batch_id),
                FOREIGN KEY (scan_id) REFERENCES nlp_scan_runs(scan_id)
            );

            CREATE TABLE IF NOT EXISTS sub_patterns (
                pattern_id      TEXT PRIMARY KEY,
                trc             TEXT NOT NULL,
                label           TEXT NOT NULL,
                description     TEXT,
                friction_type   TEXT,
                tier            TEXT DEFAULT 'probationary',
                discovered_scan TEXT NOT NULL,
                discovered_at   TEXT NOT NULL,
                last_seen_scan  TEXT,
                last_seen_at    TEXT,
                lifetime_tickets INTEGER DEFAULT 0,
                lifetime_scans  INTEGER DEFAULT 0,
                merged_into     TEXT,
                UNIQUE(trc, label)
            );

            CREATE TABLE IF NOT EXISTS sub_pattern_ngrams (
                ngram_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_id      TEXT NOT NULL,
                trc             TEXT NOT NULL,
                ngram           TEXT NOT NULL,
                n               INTEGER NOT NULL,
                source          TEXT DEFAULT 'gemini',
                frequency       INTEGER DEFAULT 1,
                ticket_count    INTEGER DEFAULT 1,
                first_seen      TEXT NOT NULL,
                last_seen       TEXT NOT NULL,
                specificity     REAL DEFAULT 0.0,
                UNIQUE(pattern_id, ngram),
                FOREIGN KEY (pattern_id) REFERENCES sub_patterns(pattern_id)
            );

            CREATE TABLE IF NOT EXISTS sub_pattern_snapshots (
                snapshot_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_id      TEXT NOT NULL,
                scan_id         TEXT NOT NULL,
                scan_date_start TEXT NOT NULL,
                scan_date_end   TEXT NOT NULL,
                ticket_count    INTEGER DEFAULT 0,
                pct_of_trc      REAL,
                avg_sentiment   REAL,
                sentiment_dist  TEXT,
                friction_dist   TEXT,
                top_entities    TEXT,
                novel_tickets   INTEGER DEFAULT 0,
                new_ngrams_added INTEGER DEFAULT 0,
                UNIQUE(pattern_id, scan_id),
                FOREIGN KEY (pattern_id) REFERENCES sub_patterns(pattern_id),
                FOREIGN KEY (scan_id) REFERENCES nlp_scan_runs(scan_id)
            );

            CREATE TABLE IF NOT EXISTS provisional_classifications (
                provisional_id  INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id       TEXT NOT NULL,
                trc             TEXT NOT NULL,
                matched_pattern_id TEXT,
                match_score     REAL,
                match_method    TEXT DEFAULT 'ngram',
                is_confirmed    INTEGER DEFAULT 0,
                confirmed_by_scan TEXT,
                confirmed_pattern_id TEXT,
                created_at      TEXT NOT NULL,
                FOREIGN KEY (matched_pattern_id)
                    REFERENCES sub_patterns(pattern_id)
            );

            CREATE TABLE IF NOT EXISTS nlp_findings (
                finding_id      TEXT PRIMARY KEY,
                scan_id         TEXT NOT NULL,
                finding_type    TEXT NOT NULL,
                scope           TEXT,
                title           TEXT NOT NULL,
                description     TEXT,
                ticket_count    INTEGER,
                pct_of_scanned  REAL,
                avg_sentiment_intensity REAL,
                dominant_friction_type TEXT,
                top_trcs        TEXT,
                top_sub_patterns TEXT,
                top_entities    TEXT,
                date_concentration TEXT,
                temporal_trend  TEXT,
                exemplar_ticket_ids TEXT,
                statistical_validation TEXT,
                baseline_comparison TEXT,
                impact_score    REAL,
                created_at      TEXT NOT NULL,
                FOREIGN KEY (scan_id) REFERENCES nlp_scan_runs(scan_id)
            );

            -- ═══ AGENTIC PIPELINE (Pass 5.0) ═══

            CREATE TABLE IF NOT EXISTS agent_health (
                agent_id        TEXT PRIMARY KEY,
                scan_id         TEXT,
                status          TEXT DEFAULT 'idle',
                batches_done    INTEGER DEFAULT 0,
                tickets_done    INTEGER DEFAULT 0,
                tool_calls      INTEGER DEFAULT 0,
                parse_rate      REAL DEFAULT 1.0,
                avg_confidence  REAL DEFAULT 0.0,
                context_tokens  INTEGER DEFAULT 0,
                last_progress   TEXT,
                updated_at      TEXT
            );

            CREATE TABLE IF NOT EXISTS scan_progress (
                scan_id               TEXT PRIMARY KEY,
                classified            INTEGER DEFAULT 0,
                total                 INTEGER DEFAULT 0,
                tool_calls            INTEGER DEFAULT 0,
                batches_complete      INTEGER DEFAULT 0,
                est_remaining_seconds REAL,
                est_confidence        TEXT DEFAULT 'low',
                updated_at            TEXT
            );

            CREATE TABLE IF NOT EXISTS review_flags (
                flag_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id   TEXT NOT NULL,
                reason      TEXT NOT NULL,
                severity    TEXT DEFAULT 'medium',
                agent_id    TEXT,
                scan_id     TEXT,
                created_at  TEXT
            );

            CREATE TABLE IF NOT EXISTS analyst_reports (
                report_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id      TEXT NOT NULL,
                report_type  TEXT NOT NULL,
                content      TEXT,
                metrics      TEXT,
                created_at   TEXT
            );

            CREATE TABLE IF NOT EXISTS trc_batch_profiles (
                trc                  TEXT PRIMARY KEY,
                avg_chars_per_ticket REAL,
                updated_at           TEXT
            );

            -- ═══ SCANNER PAGE RESTRUCTURE (Pass 5.1) ═══

            CREATE TABLE IF NOT EXISTS gemini_usage (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                date         TEXT NOT NULL,
                hour         INTEGER DEFAULT NULL,
                source       TEXT NOT NULL,
                scan_id      TEXT DEFAULT NULL,
                tokens_in    INTEGER NOT NULL DEFAULT 0,
                tokens_out   INTEGER NOT NULL DEFAULT 0,
                cost_usd     REAL NOT NULL DEFAULT 0.0,
                api_calls    INTEGER NOT NULL DEFAULT 1,
                model        TEXT DEFAULT 'gemini-2.5-flash',
                created_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS cost_limits (
                limit_type   TEXT PRIMARY KEY,
                limit_usd    REAL NOT NULL,
                period_type  TEXT DEFAULT NULL,
                updated_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scan_events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id       TEXT NOT NULL,
                timestamp     TEXT NOT NULL,
                event_type    TEXT NOT NULL,
                status        TEXT NOT NULL,
                message       TEXT NOT NULL,
                duration_ms   INTEGER DEFAULT NULL,
                metadata_json TEXT DEFAULT NULL
            );

            -- ═══ CHART LAYOUTS (Pass 3.1 UI Polish) ═══

            CREATE TABLE IF NOT EXISTS chart_layouts (
                page_key    TEXT NOT NULL,
                chart_id    TEXT NOT NULL,
                row_pos     INTEGER NOT NULL DEFAULT 0,
                col_pos     INTEGER NOT NULL DEFAULT 0,
                row_span    INTEGER NOT NULL DEFAULT 1,
                col_span    INTEGER NOT NULL DEFAULT 1,
                is_collapsed INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (page_key, chart_id)
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

            -- NLP scan indexes (Pass 4.0)
            CREATE INDEX IF NOT EXISTS idx_nlp_batch_scan ON nlp_batches(scan_id);
            CREATE INDEX IF NOT EXISTS idx_nlp_batch_trc ON nlp_batches(trc);
            CREATE INDEX IF NOT EXISTS idx_nlp_tc_ticket ON nlp_ticket_classifications(ticket_id);
            CREATE INDEX IF NOT EXISTS idx_nlp_tc_scan ON nlp_ticket_classifications(scan_id);
            CREATE INDEX IF NOT EXISTS idx_nlp_tc_trc ON nlp_ticket_classifications(trc);
            CREATE INDEX IF NOT EXISTS idx_nlp_tc_sub ON nlp_ticket_classifications(sub_cluster);
            CREATE INDEX IF NOT EXISTS idx_nlp_tc_anomaly ON nlp_ticket_classifications(anomaly_flag);
            CREATE INDEX IF NOT EXISTS idx_nlp_tc_friction ON nlp_ticket_classifications(friction_type);
            CREATE INDEX IF NOT EXISTS idx_nlp_tc_novel ON nlp_ticket_classifications(is_novel);
            CREATE INDEX IF NOT EXISTS idx_sp_trc ON sub_patterns(trc);
            CREATE INDEX IF NOT EXISTS idx_sp_tier ON sub_patterns(tier);
            CREATE INDEX IF NOT EXISTS idx_sp_merged ON sub_patterns(merged_into);
            CREATE INDEX IF NOT EXISTS idx_spn_pattern ON sub_pattern_ngrams(pattern_id);
            CREATE INDEX IF NOT EXISTS idx_spn_trc ON sub_pattern_ngrams(trc);
            CREATE INDEX IF NOT EXISTS idx_spn_ngram ON sub_pattern_ngrams(ngram);
            CREATE INDEX IF NOT EXISTS idx_spn_specificity ON sub_pattern_ngrams(specificity);
            CREATE INDEX IF NOT EXISTS idx_sps_pattern ON sub_pattern_snapshots(pattern_id);
            CREATE INDEX IF NOT EXISTS idx_sps_scan ON sub_pattern_snapshots(scan_id);
            CREATE INDEX IF NOT EXISTS idx_pc_ticket ON provisional_classifications(ticket_id);
            CREATE INDEX IF NOT EXISTS idx_pc_pattern ON provisional_classifications(matched_pattern_id);
            CREATE INDEX IF NOT EXISTS idx_pc_confirmed ON provisional_classifications(is_confirmed);
            CREATE INDEX IF NOT EXISTS idx_pc_trc ON provisional_classifications(trc);
            CREATE INDEX IF NOT EXISTS idx_nlp_f_scan ON nlp_findings(scan_id);
            CREATE INDEX IF NOT EXISTS idx_nlp_f_type ON nlp_findings(finding_type);
            CREATE INDEX IF NOT EXISTS idx_nlp_f_impact ON nlp_findings(impact_score);

            -- Incident monitoring indexes
            CREATE INDEX IF NOT EXISTS idx_daily_counts_date ON daily_counts(date);
            CREATE INDEX IF NOT EXISTS idx_daily_counts_trc ON daily_counts(trc_code);
            CREATE INDEX IF NOT EXISTS idx_hourly_date ON hourly_counts(date);
            CREATE INDEX IF NOT EXISTS idx_hourly_trc ON hourly_counts(trc_code);
            CREATE INDEX IF NOT EXISTS idx_incident_flags_status ON incident_flags(status);
            CREATE INDEX IF NOT EXISTS idx_incident_flags_trc ON incident_flags(trc_code);
            CREATE INDEX IF NOT EXISTS idx_incident_flags_date ON incident_flags(triggered_date);
            CREATE INDEX IF NOT EXISTS idx_flags_type ON incident_flags(flag_type);
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
            ("requester_hash", "TEXT DEFAULT ''"),
        ]
        for col_name, col_type in migrations:
            if col_name not in existing:
                self.conn.execute(
                    f"ALTER TABLE tickets ADD COLUMN {col_name} {col_type}"
                )

        # Column migrations for conversations table (Pass 3.0)
        conv_cols = {
            row[1] for row in self.conn.execute("PRAGMA table_info(conversations)").fetchall()
        }
        if "dataset_id" not in conv_cols:
            self.conn.execute(
                "ALTER TABLE conversations ADD COLUMN dataset_id INTEGER DEFAULT 0"
            )
        if "content_hash" not in conv_cols:
            self.conn.execute(
                "ALTER TABLE conversations ADD COLUMN content_hash TEXT"
            )

        # Column migrations for analysis_reports (Pass 3.0)
        report_cols = {
            row[1] for row in self.conn.execute("PRAGMA table_info(analysis_reports)").fetchall()
        }
        for col_name, col_type in [
            ("report_type", "TEXT DEFAULT 'standard'"),
            ("chat_history", "TEXT DEFAULT ''"),
            ("exported_at", "TEXT DEFAULT ''"),
        ]:
            if col_name not in report_cols:
                self.conn.execute(
                    f"ALTER TABLE analysis_reports ADD COLUMN {col_name} {col_type}"
                )

        # Table-level migrations for Pass 1.5 tables
        existing_tables = {
            row[0] for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        pass15_tables = [
            "user_terms", "tfidf_feedback", "discovered_compounds",
            "daily_baselines", "rolling_stats", "anomaly_flags",
            "hourly_counts", "daily_counts", "trc_baselines",
            "hourly_baselines", "incident_flags", "analysis_reports",
        ]
        pass30_tables = [
            "prompt_library", "datasets", "interventions",
            "ticket_entities", "smart_report_runs",
        ]
        pass40_tables = [
            "nlp_scan_runs", "nlp_batches", "nlp_ticket_classifications",
            "sub_patterns", "sub_pattern_ngrams", "sub_pattern_snapshots",
            "provisional_classifications", "nlp_findings",
        ]
        pass50_tables = [
            "agent_health", "scan_progress", "review_flags",
            "analyst_reports", "trc_batch_profiles",
        ]
        pass51_tables = [
            "gemini_usage", "cost_limits", "scan_events",
        ]
        if not all(t in existing_tables for t in pass15_tables):
            self.initialize()
        if not all(t in existing_tables for t in pass30_tables):
            self.initialize()
        if not all(t in existing_tables for t in pass40_tables):
            self.initialize()
        if not all(t in existing_tables for t in pass50_tables):
            self.initialize()
        if not all(t in existing_tables for t in pass51_tables):
            self.initialize()

        # Column migrations for scan_progress (Pass 5.1)
        if "scan_progress" in existing_tables:
            sp_cols = {
                row[1] for row in self.conn.execute(
                    "PRAGMA table_info(scan_progress)"
                ).fetchall()
            }
            for col_name, col_type in [
                ("tokens_in", "INTEGER DEFAULT 0"),
                ("tokens_out", "INTEGER DEFAULT 0"),
            ]:
                if col_name not in sp_cols:
                    self.conn.execute(
                        f"ALTER TABLE scan_progress ADD COLUMN {col_name} {col_type}"
                    )

        # Column migration for nlp_scan_runs.completed_at
        if "nlp_scan_runs" in existing_tables:
            sr_cols = {
                row[1] for row in self.conn.execute(
                    "PRAGMA table_info(nlp_scan_runs)"
                ).fetchall()
            }
            if "completed_at" not in sr_cols:
                self.conn.execute(
                    "ALTER TABLE nlp_scan_runs ADD COLUMN completed_at TEXT"
                )

        # Migrate trc_baselines from Gaussian to Poisson schema
        if "trc_baselines" in existing_tables:
            bl_cols = {
                row[1] for row in self.conn.execute(
                    "PRAGMA table_info(trc_baselines)"
                ).fetchall()
            }
            if "hourly_mean" in bl_cols and "lambda_daily" not in bl_cols:
                self.conn.execute("DROP TABLE trc_baselines")
                self.conn.commit()
                self.initialize()

        # Migrate incident_flags from Gaussian to Poisson schema
        if "incident_flags" in existing_tables:
            fl_cols = {
                row[1] for row in self.conn.execute(
                    "PRAGMA table_info(incident_flags)"
                ).fetchall()
            }
            if "z_score" in fl_cols and "flag_type" not in fl_cols:
                self.conn.execute("DROP TABLE incident_flags")
                self.conn.commit()
                self.initialize()

        # Pass 3.0 indexes (run after column migrations)
        try:
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversations_dataset "
                "ON conversations(dataset_id)"
            )
        except Exception:
            pass

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

    # ─── Count Materialization (Incident Monitoring) ───

    def populate_daily_counts(self):
        """
        Rebuild daily_counts from conversations.created_at.
        Call after data import. Idempotent — deletes and re-inserts.

        Uses SUBSTR instead of DATE() because timestamps may have
        single-digit hours (e.g. '2025-01-01 0:17:00') which DATE()
        cannot parse.
        """
        self.conn.execute("DELETE FROM daily_counts")
        self.conn.execute("""
            INSERT INTO daily_counts (date, trc_code, ticket_count)
            SELECT
                SUBSTR(created_at, 1, 10) as date,
                trc_code,
                COUNT(*) as ticket_count
            FROM conversations
            WHERE trc_code != '' AND created_at IS NOT NULL AND created_at != ''
                  AND LENGTH(created_at) >= 10
            GROUP BY SUBSTR(created_at, 1, 10), trc_code
        """)
        self.conn.commit()

    def get_daily_series(self, trc_code, date_from, date_to):
        """Get daily ticket counts for a TRC in a date range."""
        rows = self.conn.execute("""
            SELECT date, ticket_count
            FROM daily_counts
            WHERE trc_code = ? AND date >= ? AND date <= ?
            ORDER BY date ASC
        """, (trc_code, date_from, date_to)).fetchall()
        return [dict(r) for r in rows]

    def populate_hourly_counts(self):
        """
        Rebuild hourly_counts from conversations.created_at.
        Call after data import. Idempotent — deletes and re-inserts.

        Uses SUBSTR-based extraction instead of DATE()/STRFTIME() because
        timestamps may have single-digit hours that those functions can't parse.
        """
        self.conn.execute("DELETE FROM hourly_counts")
        self.conn.execute("""
            INSERT INTO hourly_counts (date, hour, trc_code, ticket_count)
            SELECT
                SUBSTR(created_at, 1, 10) as date,
                CAST(SUBSTR(created_at,
                    INSTR(created_at, ' ') + 1,
                    INSTR(SUBSTR(created_at, INSTR(created_at, ' ') + 1), ':') - 1
                ) AS INTEGER) as hour,
                trc_code,
                COUNT(*) as ticket_count
            FROM conversations
            WHERE trc_code != '' AND created_at IS NOT NULL AND created_at != ''
                  AND LENGTH(created_at) >= 10 AND INSTR(created_at, ' ') > 0
            GROUP BY SUBSTR(created_at, 1, 10),
                     CAST(SUBSTR(created_at,
                         INSTR(created_at, ' ') + 1,
                         INSTR(SUBSTR(created_at, INSTR(created_at, ' ') + 1), ':') - 1
                     ) AS INTEGER),
                     trc_code
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
                    ticket_count=0, duration_ms=0, notes="",
                    report_type="standard", chat_history=""):
        """Persist an analysis report. Trims to 500 reports per page."""
        self.conn.execute("""
            INSERT INTO analysis_reports
                (page, run_at, parameters, summary, full_results,
                 ticket_count, duration_ms, notes, report_type, chat_history)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            page,
            datetime.now().isoformat(),
            json.dumps(parameters) if isinstance(parameters, dict) else parameters,
            json.dumps(summary) if isinstance(summary, dict) else summary,
            full_results,
            ticket_count,
            duration_ms,
            notes,
            report_type,
            chat_history,
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

    # ─── Prompt Library (Pass 3.0) ───

    def get_prompts(self, category=None):
        """Return prompts, optionally filtered by category."""
        if category:
            rows = self.conn.execute(
                "SELECT * FROM prompt_library WHERE category = ? AND is_active = 1 ORDER BY name",
                (category,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM prompt_library WHERE is_active = 1 ORDER BY category, name"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_prompt(self, prompt_id):
        """Get a single prompt by ID."""
        row = self.conn.execute(
            "SELECT * FROM prompt_library WHERE prompt_id = ?", (prompt_id,)
        ).fetchone()
        return dict(row) if row else None

    def save_prompt(self, data):
        """Insert a new prompt. Returns prompt_id."""
        now = datetime.now().isoformat()
        cur = self.conn.execute("""
            INSERT INTO prompt_library
                (name, category, description, prompt_text, system_prompt,
                 is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
        """, (
            data["name"], data.get("category", "custom"),
            data.get("description", ""), data["prompt_text"],
            data.get("system_prompt", ""), now, now,
        ))
        self.conn.commit()
        return cur.lastrowid

    def update_prompt(self, prompt_id, data):
        """Update an existing prompt."""
        now = datetime.now().isoformat()
        self.conn.execute("""
            UPDATE prompt_library SET
                name = ?, description = ?, prompt_text = ?,
                system_prompt = ?, updated_at = ?
            WHERE prompt_id = ?
        """, (
            data["name"], data.get("description", ""),
            data["prompt_text"], data.get("system_prompt", ""),
            now, prompt_id,
        ))
        self.conn.commit()

    def delete_prompt(self, prompt_id):
        """Delete a custom prompt (soft-delete for canned)."""
        row = self.conn.execute(
            "SELECT category FROM prompt_library WHERE prompt_id = ?", (prompt_id,)
        ).fetchone()
        if row and row["category"] == "canned":
            return False  # Cannot delete canned prompts
        self.conn.execute(
            "DELETE FROM prompt_library WHERE prompt_id = ?", (prompt_id,)
        )
        self.conn.commit()
        return True

    def seed_canned_prompts(self, prompts_list):
        """Insert canned prompts if they don't already exist.
        prompts_list: [{"name": ..., "description": ..., "prompt_text": ..., "system_prompt": ...}, ...]
        """
        now = datetime.now().isoformat()
        existing = {
            row["name"] for row in self.conn.execute(
                "SELECT name FROM prompt_library WHERE category = 'canned'"
            ).fetchall()
        }
        for p in prompts_list:
            if p["name"] not in existing:
                self.conn.execute("""
                    INSERT INTO prompt_library
                        (name, category, description, prompt_text, system_prompt,
                         is_active, created_at, updated_at)
                    VALUES (?, 'canned', ?, ?, ?, 1, ?, ?)
                """, (
                    p["name"], p.get("description", ""),
                    p["prompt_text"], p.get("system_prompt", ""),
                    now, now,
                ))
        self.conn.commit()

    # ─── Datasets (Pass 3.0) ───

    def get_datasets_list(self):
        """Return all datasets."""
        rows = self.conn.execute(
            "SELECT * FROM datasets ORDER BY imported_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def create_dataset(self, data):
        """Create a new dataset entry. Returns dataset_id."""
        cur = self.conn.execute("""
            INSERT INTO datasets
                (name, label, file_path, imported_at, ticket_count,
                 date_min, date_max, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["name"], data.get("label", "A"),
            data.get("file_path", ""), datetime.now().isoformat(),
            data.get("ticket_count", 0), data.get("date_min", ""),
            data.get("date_max", ""), data.get("notes", ""),
        ))
        self.conn.commit()
        return cur.lastrowid

    def delete_dataset(self, dataset_id):
        """Delete a dataset and its associated conversations."""
        self.conn.execute(
            "DELETE FROM conversations WHERE dataset_id = ?", (dataset_id,)
        )
        self.conn.execute(
            "DELETE FROM datasets WHERE dataset_id = ?", (dataset_id,)
        )
        self.conn.commit()

    # ─── Interventions (Pass 3.0) ───

    def get_interventions(self, date_from=None, date_to=None):
        """Return interventions, optionally filtered by date range."""
        conditions = []
        params = []
        if date_from:
            conditions.append("event_date >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("event_date <= ?")
            params.append(date_to)
        where = " AND ".join(conditions) if conditions else "1=1"
        rows = self.conn.execute(
            f"SELECT * FROM interventions WHERE {where} ORDER BY event_date DESC",
            params
        ).fetchall()
        return [dict(r) for r in rows]

    def save_intervention(self, data):
        """Insert a new intervention. Returns intervention_id."""
        cur = self.conn.execute("""
            INSERT INTO interventions
                (name, category, description, event_date,
                 affected_trcs, tags, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["name"], data["category"],
            data.get("description", ""), data["event_date"],
            json.dumps(data.get("affected_trcs", [])),
            json.dumps(data.get("tags", [])),
            data.get("created_by", ""),
            datetime.now().isoformat(),
        ))
        self.conn.commit()
        return cur.lastrowid

    def update_intervention(self, intervention_id, data):
        """Update an existing intervention."""
        self.conn.execute("""
            UPDATE interventions SET
                name = ?, category = ?, description = ?,
                event_date = ?, affected_trcs = ?, tags = ?
            WHERE intervention_id = ?
        """, (
            data["name"], data["category"],
            data.get("description", ""), data["event_date"],
            json.dumps(data.get("affected_trcs", [])),
            json.dumps(data.get("tags", [])),
            intervention_id,
        ))
        self.conn.commit()

    def delete_intervention(self, intervention_id):
        """Delete an intervention."""
        self.conn.execute(
            "DELETE FROM interventions WHERE intervention_id = ?",
            (intervention_id,)
        )
        self.conn.commit()

    # ─── Ticket Entities (Pass 3.0) ───

    def save_ticket_entities(self, ticket_id, entities):
        """Save extracted entities for a ticket.
        entities: {"payers": [...], "product_areas": [...]}
        """
        now = datetime.now().isoformat()
        for entity_type, values in entities.items():
            # Normalize type name: "payers" -> "payer", "product_areas" -> "product_area"
            etype = entity_type.rstrip("s") if entity_type.endswith("s") else entity_type
            for val in values:
                self.conn.execute("""
                    INSERT OR REPLACE INTO ticket_entities
                        (ticket_id, entity_type, entity_value, confidence, extracted_at)
                    VALUES (?, ?, ?, 'dictionary', ?)
                """, (ticket_id, etype, val, now))

    def get_entities_for_ticket(self, ticket_id):
        """Return entities for a specific ticket."""
        rows = self.conn.execute(
            "SELECT * FROM ticket_entities WHERE ticket_id = ?", (ticket_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_entity_distribution(self, entity_type, date_from=None, date_to=None):
        """Get entity value distribution with ticket counts."""
        conditions = ["te.entity_type = ?"]
        params = [entity_type]
        if date_from:
            conditions.append("c.created_at >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("c.created_at <= ?")
            params.append(date_to)
        where = " AND ".join(conditions)
        rows = self.conn.execute(f"""
            SELECT te.entity_value, COUNT(DISTINCT te.ticket_id) as ticket_count
            FROM ticket_entities te
            JOIN conversations c ON te.ticket_id = c.ticket_id
            WHERE {where}
            GROUP BY te.entity_value
            ORDER BY ticket_count DESC
        """, params).fetchall()
        return [dict(r) for r in rows]

    # ─── Smart Report Runs (Pass 3.0) ───

    def start_smart_run(self, config):
        """Record a new smart pipeline run. Returns run_id."""
        cur = self.conn.execute("""
            INSERT INTO smart_report_runs
                (started_at, status, trigger_source, config_snapshot)
            VALUES (?, 'running', ?, ?)
        """, (
            datetime.now().isoformat(),
            config.get("trigger_source", "manual"),
            json.dumps(config),
        ))
        self.conn.commit()
        return cur.lastrowid

    def complete_smart_run(self, run_id, result):
        """Update a smart run with completion status."""
        self.conn.execute("""
            UPDATE smart_report_runs SET
                completed_at = ?, status = ?, ticket_count = ?,
                duration_ms = ?, error_message = ?, report_id = ?
            WHERE run_id = ?
        """, (
            datetime.now().isoformat(),
            result.get("status", "success"),
            result.get("ticket_count", 0),
            result.get("duration_ms", 0),
            result.get("error_message", ""),
            result.get("report_id"),
            run_id,
        ))
        self.conn.commit()

    def get_smart_runs(self, limit=50):
        """Return recent smart pipeline runs."""
        rows = self.conn.execute("""
            SELECT * FROM smart_report_runs
            ORDER BY started_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def update_report_exported(self, report_id):
        """Mark a report as exported to Google Drive."""
        self.conn.execute(
            "UPDATE analysis_reports SET exported_at = ? WHERE report_id = ?",
            (datetime.now().isoformat(), report_id)
        )
        self.conn.commit()

    def get_all_trc_codes(self):
        """Return all distinct TRC codes (convenience alias)."""
        return self.get_trc_codes()

    # ─── Chart Layouts (Pass 3.1) ───

    def get_chart_layout(self, page_key, chart_id):
        """Return saved layout for a chart, or None."""
        row = self.conn.execute(
            "SELECT * FROM chart_layouts WHERE page_key = ? AND chart_id = ?",
            (page_key, chart_id)
        ).fetchone()
        return dict(row) if row else None

    def save_chart_layout(self, page_key, chart_id, row_pos, col_pos,
                          row_span=1, col_span=1, is_collapsed=0):
        """Save or update a chart's grid position."""
        self.conn.execute("""
            INSERT OR REPLACE INTO chart_layouts
                (page_key, chart_id, row_pos, col_pos, row_span, col_span, is_collapsed)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (page_key, chart_id, row_pos, col_pos, row_span, col_span, is_collapsed))
        self.conn.commit()

    def get_page_layouts(self, page_key):
        """Return all saved chart layouts for a page."""
        rows = self.conn.execute(
            "SELECT * FROM chart_layouts WHERE page_key = ? ORDER BY row_pos, col_pos",
            (page_key,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ─── NLP Scan System (Pass 4.0) ───

    def get_trc_ticket_counts(self, date_start, date_end):
        """Return ticket counts per TRC in a date range for batch planning."""
        rows = self.conn.execute("""
            SELECT trc_code AS trc, COUNT(DISTINCT ticket_id) AS n
            FROM conversations
            WHERE created_at >= ? AND created_at <= ?
            GROUP BY trc_code
            ORDER BY n DESC
        """, (date_start, date_end)).fetchall()
        return [dict(r) for r in rows]

    def get_tickets_for_trc(self, trc, date_start, date_end, limit=None):
        """Return tickets + full_thread for a TRC in a date range."""
        query = """
            SELECT c.ticket_id, c.subject, c.trc_code, c.trc_label,
                   c.csat_score, c.created_at, c.full_thread,
                   c.message_count, c.client_messages, c.agent_messages
            FROM conversations c
            WHERE c.trc_code = ? AND c.created_at >= ? AND c.created_at <= ?
            ORDER BY c.created_at ASC
        """
        params = [trc, date_start, date_end]
        if limit:
            query += " LIMIT ?"
            params.append(limit)
        return [dict(r) for r in self.conn.execute(query, params).fetchall()]

    def get_active_sub_patterns(self, trc):
        """Return active + probationary sub-patterns for a TRC."""
        rows = self.conn.execute("""
            SELECT * FROM sub_patterns
            WHERE trc = ? AND tier IN ('active', 'probationary')
              AND merged_into IS NULL
            ORDER BY lifetime_tickets DESC
        """, (trc,)).fetchall()
        return [dict(r) for r in rows]

    def get_sub_pattern_ngrams(self, pattern_id, min_specificity=0.0):
        """Return n-grams for a sub-pattern, optionally filtered by specificity."""
        rows = self.conn.execute("""
            SELECT * FROM sub_pattern_ngrams
            WHERE pattern_id = ? AND specificity >= ?
            ORDER BY (frequency * specificity) DESC
        """, (pattern_id, min_specificity)).fetchall()
        return [dict(r) for r in rows]

    def get_scan_status(self, scan_id):
        """Return scan run status row."""
        row = self.conn.execute(
            "SELECT * FROM nlp_scan_runs WHERE scan_id = ?", (scan_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_scan_findings(self, scan_id, limit=20):
        """Return findings for a scan, ranked by impact."""
        rows = self.conn.execute("""
            SELECT * FROM nlp_findings
            WHERE scan_id = ?
            ORDER BY impact_score DESC
            LIMIT ?
        """, (scan_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def get_scan_history(self, limit=50):
        """Return recent scan runs."""
        rows = self.conn.execute("""
            SELECT * FROM nlp_scan_runs
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_sub_taxonomy_health(self):
        """Return sub-pattern tier counts and stats."""
        rows = self.conn.execute("""
            SELECT tier, COUNT(*) AS cnt
            FROM sub_patterns
            WHERE merged_into IS NULL
            GROUP BY tier
        """).fetchall()
        result = {r["tier"]: r["cnt"] for r in rows}
        # Average n-grams per pattern (all non-merged tiers, not just 'active')
        row = self.conn.execute("""
            SELECT AVG(ngram_count) AS avg_ngrams FROM (
                SELECT sp.pattern_id, COUNT(spn.ngram_id) AS ngram_count
                FROM sub_patterns sp
                LEFT JOIN sub_pattern_ngrams spn ON sp.pattern_id = spn.pattern_id
                WHERE sp.merged_into IS NULL
                GROUP BY sp.pattern_id
            )
        """).fetchone()
        result["avg_ngrams"] = row["avg_ngrams"] if row["avg_ngrams"] else 0
        return result

    def get_latest_completed_scan(self):
        """Return the most recent completed scan run."""
        row = self.conn.execute("""
            SELECT * FROM nlp_scan_runs
            WHERE status IN ('completed', 'scan_complete', 'analysis_complete')
            ORDER BY created_at DESC
            LIMIT 1
        """).fetchone()
        return dict(row) if row else None

    def get_trc_list(self):
        """Return distinct TRC codes from conversations."""
        rows = self.conn.execute("""
            SELECT DISTINCT trc_code FROM conversations
            WHERE trc_code IS NOT NULL AND trc_code != ''
            ORDER BY trc_code
        """).fetchall()
        return [r["trc_code"] for r in rows]

    def get_ticket_count_in_range(self, date_start, date_end):
        """Count distinct tickets in a date range."""
        row = self.conn.execute("""
            SELECT COUNT(DISTINCT ticket_id) AS n FROM conversations
            WHERE created_at >= ? AND created_at <= ?
        """, (date_start, date_end + " 23:59:59")).fetchone()
        return row["n"] if row else 0

    def get_finding_ticket_ids(self, finding_id):
        """Return ticket IDs for a finding's exemplar + classified tickets."""
        row = self.conn.execute(
            "SELECT * FROM nlp_findings WHERE finding_id = ?", (finding_id,)
        ).fetchone()
        if not row:
            return []
        finding = dict(row)
        # Start with exemplars
        import json
        ids = json.loads(finding.get("exemplar_ticket_ids") or "[]")
        # Add all classified tickets for the same sub-patterns + TRCs
        trcs = json.loads(finding.get("top_trcs") or "[]")
        subs = json.loads(finding.get("top_sub_patterns") or "[]")
        if trcs and subs:
            sub_labels = [s.get("label", s) if isinstance(s, dict) else s for s in subs]
            for trc in trcs:
                trc_code = trc.get("trc", trc) if isinstance(trc, dict) else trc
                for label in sub_labels:
                    rows = self.conn.execute("""
                        SELECT ticket_id FROM nlp_ticket_classifications
                        WHERE trc = ? AND sub_cluster = ?
                    """, (trc_code, label)).fetchall()
                    ids.extend(r["ticket_id"] for r in rows)
        return list(set(ids))

    # ─── Agentic Pipeline (Pass 5.0) ───

    def update_agent_health(self, agent_id, **metrics):
        """Upsert agent health metrics for supervisor monitoring."""
        metrics["updated_at"] = datetime.now().isoformat()
        cols = ", ".join(metrics.keys())
        placeholders = ", ".join("?" for _ in metrics)
        updates = ", ".join(f"{k} = ?" for k in metrics)
        vals = list(metrics.values())
        self.conn.execute(f"""
            INSERT INTO agent_health (agent_id, {cols})
            VALUES (?, {placeholders})
            ON CONFLICT(agent_id) DO UPDATE SET {updates}
        """, [agent_id] + vals + vals)
        self.conn.commit()

    def get_agent_health(self, scan_id=None):
        """Return agent health records, optionally filtered by scan."""
        if scan_id:
            rows = self.conn.execute(
                "SELECT * FROM agent_health WHERE scan_id = ?", (scan_id,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM agent_health").fetchall()
        return [dict(r) for r in rows]

    def update_scan_progress(self, scan_id, **metrics):
        """Upsert scan progress for UI polling."""
        metrics["updated_at"] = datetime.now().isoformat()
        cols = ", ".join(metrics.keys())
        placeholders = ", ".join("?" for _ in metrics)
        updates = ", ".join(f"{k} = ?" for k in metrics)
        vals = list(metrics.values())
        self.conn.execute(f"""
            INSERT INTO scan_progress (scan_id, {cols})
            VALUES (?, {placeholders})
            ON CONFLICT(scan_id) DO UPDATE SET {updates}
        """, [scan_id] + vals + vals)
        self.conn.commit()

    def get_scan_progress(self, scan_id):
        """Return scan progress record for UI polling."""
        row = self.conn.execute(
            "SELECT * FROM scan_progress WHERE scan_id = ?", (scan_id,)
        ).fetchone()
        return dict(row) if row else {}

    def insert_review_flag(self, ticket_id, reason, severity="medium",
                           agent_id=None, scan_id=None):
        """Insert a review flag from a worker agent."""
        self.conn.execute("""
            INSERT INTO review_flags (ticket_id, reason, severity, agent_id, scan_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (ticket_id, reason, severity, agent_id, scan_id,
              datetime.now().isoformat()))
        self.conn.commit()

    def insert_analyst_report(self, scan_id, report_type, content, metrics=None):
        """Insert an analyst agent report."""
        import json as _json
        self.conn.execute("""
            INSERT INTO analyst_reports (scan_id, report_type, content, metrics, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (scan_id, report_type, content,
              _json.dumps(metrics) if metrics else None,
              datetime.now().isoformat()))
        self.conn.commit()

    def get_analyst_reports(self, scan_id):
        """Return analyst reports for a scan."""
        rows = self.conn.execute(
            "SELECT * FROM analyst_reports WHERE scan_id = ? ORDER BY created_at",
            (scan_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ─── Gemini Usage / Cost Tracking (Pass 5.1) ───

    def log_gemini_usage(self, date, source, tokens_in, tokens_out,
                         cost_usd, scan_id=None, model="gemini-2.5-flash",
                         hour=None):
        """Insert a gemini_usage row for token/cost tracking."""
        self.conn.execute("""
            INSERT INTO gemini_usage
            (date, hour, source, scan_id, tokens_in, tokens_out,
             cost_usd, api_calls, model, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """, (date, hour, source, scan_id, tokens_in, tokens_out,
              cost_usd, model, datetime.now().isoformat()))
        self.conn.commit()

    def get_usage_totals(self, period="day", date=None, source=None):
        """Aggregate gemini_usage by period.

        period: 'day', 'week', 'month'
        Returns: {'tokens_in': int, 'tokens_out': int, 'cost_usd': float, 'api_calls': int}
        """
        from datetime import timedelta
        ref = date or datetime.now().strftime("%Y-%m-%d")
        if period == "day":
            where = "date = ?"
            params = [ref]
        elif period == "week":
            # Last 7 days
            start = (datetime.strptime(ref, "%Y-%m-%d") - timedelta(days=6)).strftime("%Y-%m-%d")
            where = "date BETWEEN ? AND ?"
            params = [start, ref]
        elif period == "month":
            where = "SUBSTR(date, 1, 7) = SUBSTR(?, 1, 7)"
            params = [ref]
        else:
            where = "1=1"
            params = []

        if source:
            where += " AND source = ?"
            params.append(source)

        row = self.conn.execute(f"""
            SELECT COALESCE(SUM(tokens_in), 0) as tokens_in,
                   COALESCE(SUM(tokens_out), 0) as tokens_out,
                   COALESCE(SUM(cost_usd), 0.0) as cost_usd,
                   COALESCE(SUM(api_calls), 0) as api_calls
            FROM gemini_usage
            WHERE {where}
        """, params).fetchone()
        return dict(row) if row else {"tokens_in": 0, "tokens_out": 0,
                                       "cost_usd": 0.0, "api_calls": 0}

    def get_cost_history_weekly(self, weeks=12):
        """Return weekly cost aggregations for chart.

        Returns list of dicts: [{'week': 'YYYY-WW', 'cost_usd': float, 'api_calls': int}, ...]
        """
        rows = self.conn.execute("""
            SELECT STRFTIME('%Y-%W', date) as week,
                   SUM(cost_usd) as cost_usd,
                   SUM(api_calls) as api_calls,
                   SUM(tokens_in) as tokens_in,
                   SUM(tokens_out) as tokens_out
            FROM gemini_usage
            GROUP BY week
            ORDER BY week DESC
            LIMIT ?
        """, (weeks,)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def get_cost_limits(self):
        """Return all cost limits as a dict keyed by limit_type."""
        rows = self.conn.execute("SELECT * FROM cost_limits").fetchall()
        return {r["limit_type"]: dict(r) for r in rows}

    def set_cost_limit(self, limit_type, limit_usd, period_type=None):
        """Upsert a cost limit."""
        self.conn.execute("""
            INSERT INTO cost_limits (limit_type, limit_usd, period_type, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(limit_type) DO UPDATE SET
                limit_usd = excluded.limit_usd,
                period_type = excluded.period_type,
                updated_at = excluded.updated_at
        """, (limit_type, limit_usd, period_type, datetime.now().isoformat()))
        self.conn.commit()

    def insert_scan_event(self, scan_id, event_type, status, message,
                          duration_ms=None, metadata=None):
        """Insert a scan event for preflight/execution log."""
        import json as _json
        self.conn.execute("""
            INSERT INTO scan_events
            (scan_id, timestamp, event_type, status, message, duration_ms, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (scan_id, datetime.now().isoformat(), event_type, status,
              message, duration_ms,
              _json.dumps(metadata) if metadata else None))
        self.conn.commit()

    def get_scan_events(self, scan_id):
        """Return scan events ordered by timestamp."""
        rows = self.conn.execute(
            "SELECT * FROM scan_events WHERE scan_id = ? ORDER BY timestamp",
            (scan_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ─── Demo Data ───

    def load_demo_data(self):
        """Load sample data for development and demo purposes."""
        from src.data.demo_data import generate_demo_data
        generate_demo_data(self)
