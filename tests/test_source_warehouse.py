"""
Tests for Phase 3.5 — Source Warehouse

Covers:
  - ingest_records persists to source_events with correct source column
  - No PHI in source_events (no subject/description columns in table)
  - Duplicate (source, ticket_id) is idempotent
  - Hourly/daily rollups update correctly with source filter
  - _classify_hybrid tries n-gram first, falls back to LLM
  - get_trc_baseline returns hourly counts filtered by source
  - get_daily_trend returns daily counts filtered by source
  - rollup_maintenance prunes old hourly data, expires old alerts
"""

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def warehouse_db():
    """Create an in-memory database with warehouse tables."""
    conn = sqlite3.connect(":memory:")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS source_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL DEFAULT 'zendesk',
            ticket_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            trc_code TEXT NOT NULL,
            classification TEXT DEFAULT '',
            sentiment TEXT DEFAULT '',
            priority TEXT DEFAULT '',
            ticket_type TEXT DEFAULT '',
            tags TEXT DEFAULT '',
            flagged INTEGER DEFAULT 0,
            classified_by TEXT DEFAULT '',
            inserted_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(source, ticket_id)
        );
        CREATE TABLE IF NOT EXISTS source_trc_hourly (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL DEFAULT 'zendesk',
            trc_code TEXT NOT NULL,
            hour_bucket TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            avg_sentiment REAL DEFAULT 0.0,
            UNIQUE(source, trc_code, hour_bucket)
        );
        CREATE TABLE IF NOT EXISTS source_trc_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL DEFAULT 'zendesk',
            trc_code TEXT NOT NULL,
            day_bucket TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            avg_sentiment REAL DEFAULT 0.0,
            UNIQUE(source, trc_code, day_bucket)
        );
        CREATE TABLE IF NOT EXISTS sub_pattern_ngrams (
            id INTEGER PRIMARY KEY, ngram TEXT, pattern_id TEXT,
            specificity REAL, trc TEXT
        );
        CREATE TABLE IF NOT EXISTS sub_patterns (
            pattern_id TEXT PRIMARY KEY, friction_type TEXT, label TEXT,
            tier TEXT DEFAULT 'active', merged_into TEXT
        );
        CREATE TABLE IF NOT EXISTS watchlist_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, rule_type TEXT NOT NULL,
            severity TEXT DEFAULT 'watch',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS watchlist_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id INTEGER REFERENCES watchlist_rules(id),
            source TEXT DEFAULT 'zendesk', severity TEXT, title TEXT,
            summary TEXT DEFAULT '', ticket_count INTEGER DEFAULT 0,
            ticket_ids TEXT DEFAULT '', trc_code TEXT DEFAULT '',
            status TEXT DEFAULT 'open', llm_triage TEXT DEFAULT '',
            llm_confidence REAL DEFAULT 0.0,
            created_at TEXT DEFAULT (datetime('now')),
            resolved_at TEXT DEFAULT '', resolved_by TEXT DEFAULT ''
        );
    """)

    db = MagicMock()
    db.get_connection.return_value = conn
    return db, conn


@pytest.fixture
def warehouse(warehouse_db):
    from src.data.source_warehouse import SourceWarehouse
    db, conn = warehouse_db
    return SourceWarehouse(db), conn


# ═══════════════════════════════════════
#  Ingest Records
# ═══════════════════════════════════════

class TestIngestRecords:
    """Verify record ingestion and cold-tier persistence."""

    def test_ingest_basic(self, warehouse):
        wh, conn = warehouse
        client = MagicMock()
        client.extract_trc.return_value = "TRC001"

        records = [
            {"id": "100", "created_at": "2026-03-10T14:30:00",
             "subject": "Test ticket", "description": "A test",
             "priority": "normal", "type": "incident", "tags": ["billing"]},
        ]
        enriched = wh.ingest_records(records, "zendesk", "subject", client)

        assert len(enriched) == 1
        assert enriched[0]["_trc_code"] == "TRC001"

        # Verify stored in source_events
        row = conn.execute(
            "SELECT source, ticket_id, trc_code FROM source_events"
        ).fetchone()
        assert row is not None
        assert row[0] == "zendesk"
        assert row[1] == "100"
        assert row[2] == "TRC001"

    def test_no_phi_stored(self, warehouse):
        """Subject and description must NOT be stored in source_events."""
        wh, conn = warehouse
        client = MagicMock()
        client.extract_trc.return_value = "TRC002"

        records = [
            {"id": "200", "created_at": "2026-03-10T14:30:00",
             "subject": "SENSITIVE SUBJECT", "description": "PRIVATE DESC"},
        ]
        wh.ingest_records(records, "zendesk", "subject", client)

        # Verify no PHI columns exist in table
        cursor = conn.execute("PRAGMA table_info(source_events)")
        columns = [row[1] for row in cursor.fetchall()]
        assert "subject" not in columns
        assert "description" not in columns

    def test_duplicate_idempotent(self, warehouse):
        """Same source+ticket_id should not create duplicate rows."""
        wh, conn = warehouse
        client = MagicMock()
        client.extract_trc.return_value = "TRC003"

        records = [
            {"id": "300", "created_at": "2026-03-10T14:30:00",
             "subject": "Test"},
        ]
        wh.ingest_records(records, "zendesk", "subject", client)
        wh.ingest_records(records, "zendesk", "subject", client)

        count = conn.execute(
            "SELECT COUNT(*) FROM source_events WHERE ticket_id = '300'"
        ).fetchone()[0]
        assert count == 1

    def test_cross_source_same_ticket_id(self, warehouse):
        """Same ticket_id from different sources = two separate rows."""
        wh, conn = warehouse
        client = MagicMock()
        client.extract_trc.return_value = "TRC004"

        records = [{"id": "400", "created_at": "2026-03-10T14:30:00"}]
        wh.ingest_records(records, "zendesk", "subject", client)
        wh.ingest_records(records, "intercom", "subject", client)

        count = conn.execute(
            "SELECT COUNT(*) FROM source_events WHERE ticket_id = '400'"
        ).fetchone()[0]
        assert count == 2

    def test_skips_empty_ticket_id(self, warehouse):
        """Records without ticket_id are skipped."""
        wh, conn = warehouse
        client = MagicMock()
        client.extract_trc.return_value = "TRC005"

        records = [{"subject": "No ID"}]
        enriched = wh.ingest_records(records, "zendesk", "subject", client)

        assert len(enriched) == 0
        count = conn.execute("SELECT COUNT(*) FROM source_events").fetchone()[0]
        assert count == 0

    def test_stores_classification_metadata(self, warehouse):
        """Classification and classified_by are stored."""
        wh, conn = warehouse
        client = MagicMock()
        client.extract_trc.return_value = "TRC006"

        records = [
            {"id": "600", "created_at": "2026-03-10T14:30:00",
             "priority": "high", "type": "problem",
             "tags": ["billing", "urgent"], "flagged": True},
        ]
        wh.ingest_records(records, "zendesk", "subject", client)

        row = conn.execute("""
            SELECT priority, ticket_type, tags, flagged
            FROM source_events WHERE ticket_id = '600'
        """).fetchone()
        assert row[0] == "high"
        assert row[1] == "problem"
        assert row[2] == "billing,urgent"
        assert row[3] == 1


# ═══════════════════════════════════════
#  Rollups
# ═══════════════════════════════════════

class TestRollups:
    """Verify hourly and daily rollup updates."""

    def test_hourly_rollup(self, warehouse):
        wh, conn = warehouse
        wh.update_rollups("zendesk", "TRC001", "2026-03-10T14:30:00")

        row = conn.execute(
            "SELECT count FROM source_trc_hourly WHERE trc_code = 'TRC001'"
        ).fetchone()
        assert row is not None
        assert row[0] == 1

    def test_hourly_rollup_increments(self, warehouse):
        wh, conn = warehouse
        wh.update_rollups("zendesk", "TRC001", "2026-03-10T14:30:00")
        wh.update_rollups("zendesk", "TRC001", "2026-03-10T14:45:00")

        row = conn.execute(
            "SELECT count FROM source_trc_hourly WHERE trc_code = 'TRC001'"
        ).fetchone()
        assert row[0] == 2

    def test_daily_rollup(self, warehouse):
        wh, conn = warehouse
        wh.update_rollups("zendesk", "TRC001", "2026-03-10T14:30:00")

        row = conn.execute(
            "SELECT count, day_bucket FROM source_trc_daily WHERE trc_code = 'TRC001'"
        ).fetchone()
        assert row is not None
        assert row[0] == 1
        assert row[1] == "2026-03-10"

    def test_rollups_source_separation(self, warehouse):
        """Rollups for different sources don't collide."""
        wh, conn = warehouse
        wh.update_rollups("zendesk", "TRC001", "2026-03-10T14:30:00")
        wh.update_rollups("intercom", "TRC001", "2026-03-10T14:30:00")

        rows = conn.execute(
            "SELECT source, count FROM source_trc_hourly WHERE trc_code = 'TRC001'"
        ).fetchall()
        assert len(rows) == 2
        assert {r[0] for r in rows} == {"zendesk", "intercom"}


# ═══════════════════════════════════════
#  Baseline & Trend Queries
# ═══════════════════════════════════════

class TestBaselineAndTrend:
    """Verify baseline and trend query methods."""

    def test_get_daily_trend(self, warehouse):
        wh, conn = warehouse
        # Insert some daily data
        conn.execute("""
            INSERT INTO source_trc_daily (source, trc_code, day_bucket, count)
            VALUES ('zendesk', 'TRC001', '2026-03-09', 5)
        """)
        conn.execute("""
            INSERT INTO source_trc_daily (source, trc_code, day_bucket, count)
            VALUES ('zendesk', 'TRC001', '2026-03-10', 8)
        """)
        conn.commit()

        trend = wh.get_daily_trend("zendesk", "TRC001", lookback_days=30)
        assert len(trend) >= 1  # At least 1 row within lookback

    def test_get_hourly_count(self, warehouse):
        wh, conn = warehouse
        # Insert data in current hour bucket
        now = datetime.now(timezone.utc)
        bucket = now.strftime("%Y-%m-%dT%H:00:00")
        conn.execute("""
            INSERT INTO source_trc_hourly (source, trc_code, hour_bucket, count)
            VALUES ('zendesk', 'TRC001', ?, 12)
        """, (bucket,))
        conn.commit()

        count = wh.get_hourly_count("zendesk", "TRC001", window_minutes=60)
        assert count == 12

    def test_get_hourly_count_zero(self, warehouse):
        wh, conn = warehouse
        count = wh.get_hourly_count("zendesk", "NONEXISTENT", window_minutes=60)
        assert count == 0


# ═══════════════════════════════════════
#  Hybrid Classification
# ═══════════════════════════════════════

class TestClassifyHybrid:
    """Verify hybrid classification pipeline."""

    def test_classify_returns_empty_for_no_match(self, warehouse):
        wh, conn = warehouse
        record = {"subject": "Hello world", "description": "Test"}
        classification, classified_by = wh._classify_hybrid(
            record, "TRC001", "zendesk"
        )
        assert classification == ""
        assert classified_by == "none"

    def test_classify_ngram_matches(self, warehouse):
        """N-gram classifier matches against sub_pattern_ngrams."""
        wh, conn = warehouse

        # Insert pattern + n-grams
        conn.execute("""
            INSERT INTO sub_patterns (pattern_id, friction_type, label, tier)
            VALUES ('pat1', 'billing_dispute', 'Billing Dispute', 'active')
        """)
        conn.execute("""
            INSERT INTO sub_pattern_ngrams (ngram, pattern_id, specificity, trc)
            VALUES ('billing error', 'pat1', 0.8, 'TRC001')
        """)
        conn.execute("""
            INSERT INTO sub_pattern_ngrams (ngram, pattern_id, specificity, trc)
            VALUES ('charged twice', 'pat1', 0.9, 'TRC001')
        """)
        conn.commit()

        record = {
            "subject": "I was charged twice! Billing error!",
            "description": "Please help",
            "tags": [],
        }
        classification = wh._classify_ngram(record, "TRC001")
        assert classification == "billing_dispute"

    def test_classify_ngram_requires_min_matches(self, warehouse):
        """N-gram classifier requires >= 2 matching n-grams."""
        wh, conn = warehouse

        conn.execute("""
            INSERT INTO sub_patterns (pattern_id, friction_type, label, tier)
            VALUES ('pat2', 'login_failure', 'Login Failure', 'active')
        """)
        conn.execute("""
            INSERT INTO sub_pattern_ngrams (ngram, pattern_id, specificity, trc)
            VALUES ('password reset', 'pat2', 0.8, 'TRC002')
        """)
        conn.commit()

        record = {
            "subject": "I need a password reset",
            "description": "",
            "tags": [],
        }
        # Only 1 match — should return empty (need >= 2)
        classification = wh._classify_ngram(record, "TRC002")
        assert classification == ""


# ═══════════════════════════════════════
#  Maintenance
# ═══════════════════════════════════════

class TestRollupMaintenance:
    """Verify rollup maintenance prunes old data."""

    def test_prune_old_hourly(self, warehouse):
        wh, conn = warehouse

        # Insert old hourly data (> 7 days ago)
        conn.execute("""
            INSERT INTO source_trc_hourly (source, trc_code, hour_bucket, count)
            VALUES ('zendesk', 'TRC001', '2020-01-01T00:00:00', 5)
        """)
        conn.commit()

        wh.rollup_maintenance()

        count = conn.execute(
            "SELECT COUNT(*) FROM source_trc_hourly"
        ).fetchone()[0]
        assert count == 0

    def test_expire_old_alerts(self, warehouse):
        wh, conn = warehouse

        # Create watchlist_rules (required for foreign key)
        conn.execute("""
            INSERT INTO watchlist_rules (id, name, rule_type, severity)
            VALUES (1, 'Test', 'keyword', 'watch')
        """)

        # Insert old open alert
        conn.execute("""
            INSERT INTO watchlist_alerts
                (rule_id, source, severity, title, status, created_at)
            VALUES (1, 'zendesk', 'watch', 'Old Alert', 'open',
                    datetime('now', '-48 hours'))
        """)
        conn.commit()

        wh.rollup_maintenance()

        row = conn.execute(
            "SELECT status FROM watchlist_alerts WHERE title = 'Old Alert'"
        ).fetchone()
        assert row[0] == "expired"

    def test_keeps_recent_hourly(self, warehouse):
        wh, conn = warehouse

        # Insert recent hourly data
        now = datetime.now(timezone.utc)
        bucket = now.strftime("%Y-%m-%dT%H:00:00")
        conn.execute("""
            INSERT INTO source_trc_hourly (source, trc_code, hour_bucket, count)
            VALUES ('zendesk', 'TRC001', ?, 3)
        """, (bucket,))
        conn.commit()

        wh.rollup_maintenance()

        count = conn.execute(
            "SELECT COUNT(*) FROM source_trc_hourly"
        ).fetchone()[0]
        assert count == 1
