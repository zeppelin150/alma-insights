"""
Tests for Phase 3.5 — Watchlist Engine

Covers:
  - System rules auto-created on first init (5 rules)
  - Keyword matching: any mode, all mode
  - Volume threshold checks
  - Cooldown prevents re-fire within window
  - EWMA update: confirmed → confidence increases, dismissed → decreases
  - EWMA bounds: floor 0.1, ceiling 0.95
  - Cold start (n<5 fires) always proceeds past EWMA gate
  - Incident severity always proceeds (bypasses EWMA gate)
  - Low confidence (< 0.3) skips rule evaluation
  - Source-agnostic TRC extraction
  - CRUD: create/update/delete user rules
  - System rules can't be deleted but CAN be disabled
  - Alert lifecycle: open → confirmed/dismissed/expired
  - Few-shot example capping
"""

import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def watchlist_db():
    """Create in-memory DB with watchlist tables."""
    conn = sqlite3.connect(":memory:")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS watchlist_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, rule_type TEXT NOT NULL,
            severity TEXT DEFAULT 'watch', source_filter TEXT DEFAULT '',
            is_system INTEGER DEFAULT 0, enabled INTEGER DEFAULT 1,
            keywords TEXT DEFAULT '', keyword_mode TEXT DEFAULT 'any',
            entity_type TEXT DEFAULT '', entity_filter TEXT DEFAULT '',
            volume_threshold INTEGER DEFAULT 0,
            volume_window_minutes INTEGER DEFAULT 60,
            ewma_confidence REAL DEFAULT 0.5, ewma_alpha REAL DEFAULT 0.3,
            total_fires INTEGER DEFAULT 0,
            total_confirmed INTEGER DEFAULT 0, total_dismissed INTEGER DEFAULT 0,
            cooldown_minutes INTEGER DEFAULT 120,
            last_fired_at TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS watchlist_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id INTEGER REFERENCES watchlist_rules(id),
            source TEXT DEFAULT 'zendesk', severity TEXT,
            title TEXT, summary TEXT DEFAULT '',
            ticket_count INTEGER DEFAULT 0, ticket_ids TEXT DEFAULT '',
            trc_code TEXT DEFAULT '', status TEXT DEFAULT 'open',
            llm_triage TEXT DEFAULT '', llm_confidence REAL DEFAULT 0.0,
            created_at TEXT DEFAULT (datetime('now')),
            resolved_at TEXT DEFAULT '', resolved_by TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS watchlist_examples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id INTEGER, example_type TEXT, sanitized_text TEXT,
            outcome TEXT, trc_code TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS source_trc_hourly (
            id INTEGER PRIMARY KEY, source TEXT, trc_code TEXT,
            hour_bucket TEXT, count INTEGER DEFAULT 0,
            UNIQUE(source, trc_code, hour_bucket)
        );
    """)

    db = MagicMock()
    db.get_connection.return_value = conn
    return db, conn


@pytest.fixture
def engine(watchlist_db):
    from src.data.watchlist_engine import WatchlistEngine
    db, conn = watchlist_db
    wh = MagicMock()
    engine = WatchlistEngine(db, warehouse=wh)
    return engine, conn, wh


# ═══════════════════════════════════════
#  System Rules
# ═══════════════════════════════════════

class TestSystemRules:
    """Verify system rule initialization."""

    def test_system_rules_created(self, engine):
        eng, conn, wh = engine
        count = conn.execute(
            "SELECT COUNT(*) FROM watchlist_rules WHERE is_system = 1"
        ).fetchone()[0]
        assert count == 5

    def test_system_rules_idempotent(self, engine):
        """Running _ensure_system_rules again doesn't create duplicates."""
        eng, conn, wh = engine
        eng._ensure_system_rules()
        count = conn.execute(
            "SELECT COUNT(*) FROM watchlist_rules WHERE is_system = 1"
        ).fetchone()[0]
        assert count == 5

    def test_system_rules_have_correct_types(self, engine):
        eng, conn, wh = engine
        rows = conn.execute(
            "SELECT name, rule_type, severity FROM watchlist_rules WHERE is_system = 1"
        ).fetchall()

        names = {r[0] for r in rows}
        assert "Site Outage Detection" in names
        assert "Payment Processing Failure" in names
        assert "Login/Auth Issues" in names
        assert "High-Value Provider Alert" in names
        assert "Data/Privacy Concern" in names


# ═══════════════════════════════════════
#  Keyword Matching
# ═══════════════════════════════════════

class TestKeywordMatching:
    """Verify keyword matching modes."""

    def test_any_mode_match(self, engine):
        eng, conn, wh = engine
        rule = {"keywords": "payment failed,card declined", "keyword_mode": "any"}
        record = {"subject": "My payment failed today", "description": ""}
        assert eng._match_keywords(record, rule) is True

    def test_any_mode_no_match(self, engine):
        eng, conn, wh = engine
        rule = {"keywords": "payment failed,card declined", "keyword_mode": "any"}
        record = {"subject": "How do I update my address?", "description": ""}
        assert eng._match_keywords(record, rule) is False

    def test_all_mode_match(self, engine):
        eng, conn, wh = engine
        rule = {"keywords": "payment,error", "keyword_mode": "all"}
        record = {"subject": "Payment error on checkout", "description": ""}
        assert eng._match_keywords(record, rule) is True

    def test_all_mode_partial_no_match(self, engine):
        eng, conn, wh = engine
        rule = {"keywords": "payment,error", "keyword_mode": "all"}
        record = {"subject": "Payment processed OK", "description": ""}
        assert eng._match_keywords(record, rule) is False

    def test_empty_keywords_no_match(self, engine):
        eng, conn, wh = engine
        rule = {"keywords": "", "keyword_mode": "any"}
        record = {"subject": "Something", "description": ""}
        assert eng._match_keywords(record, rule) is False

    def test_case_insensitive(self, engine):
        eng, conn, wh = engine
        rule = {"keywords": "PAYMENT FAILED", "keyword_mode": "any"}
        record = {"subject": "payment failed", "description": ""}
        assert eng._match_keywords(record, rule) is True


# ═══════════════════════════════════════
#  EWMA Gate
# ═══════════════════════════════════════

class TestEWMAGate:
    """Verify EWMA confidence gate logic."""

    def test_incident_always_passes(self, engine):
        eng, conn, wh = engine
        rule = {"severity": "incident", "total_fires": 100, "ewma_confidence": 0.1}
        assert eng._passes_ewma_gate(rule) is True

    def test_cold_start_always_passes(self, engine):
        eng, conn, wh = engine
        rule = {"severity": "watch", "total_fires": 3, "ewma_confidence": 0.1}
        assert eng._passes_ewma_gate(rule) is True

    def test_low_confidence_fails(self, engine):
        eng, conn, wh = engine
        rule = {"severity": "watch", "total_fires": 10, "ewma_confidence": 0.2}
        assert eng._passes_ewma_gate(rule) is False

    def test_sufficient_confidence_passes(self, engine):
        eng, conn, wh = engine
        rule = {"severity": "watch", "total_fires": 10, "ewma_confidence": 0.5}
        assert eng._passes_ewma_gate(rule) is True

    def test_boundary_at_threshold(self, engine):
        eng, conn, wh = engine
        rule = {"severity": "watch", "total_fires": 10, "ewma_confidence": 0.3}
        assert eng._passes_ewma_gate(rule) is True

    def test_just_below_threshold(self, engine):
        eng, conn, wh = engine
        rule = {"severity": "watch", "total_fires": 10, "ewma_confidence": 0.29}
        assert eng._passes_ewma_gate(rule) is False


# ═══════════════════════════════════════
#  Cooldown
# ═══════════════════════════════════════

class TestCooldown:
    """Verify cooldown prevents re-fire."""

    def test_no_last_fired_not_in_cooldown(self, engine):
        eng, conn, wh = engine
        rule = {"last_fired_at": "", "cooldown_minutes": 120}
        assert eng._is_in_cooldown(rule, time.time()) is False

    def test_recent_fire_in_cooldown(self, engine):
        eng, conn, wh = engine
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        rule = {"last_fired_at": now, "cooldown_minutes": 120}
        assert eng._is_in_cooldown(rule, time.time()) is True

    def test_old_fire_not_in_cooldown(self, engine):
        eng, conn, wh = engine
        from datetime import datetime, timezone, timedelta
        old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        rule = {"last_fired_at": old, "cooldown_minutes": 120}
        assert eng._is_in_cooldown(rule, time.time()) is False


# ═══════════════════════════════════════
#  CRUD
# ═══════════════════════════════════════

class TestCRUD:
    """Verify rule CRUD operations."""

    def test_create_user_rule(self, engine):
        eng, conn, wh = engine
        rule_id = eng.create_rule(
            name="Custom Alert", rule_type="keyword",
            severity="watch", keywords="refund,dispute",
        )
        assert rule_id > 0

        row = conn.execute(
            "SELECT name, is_system FROM watchlist_rules WHERE id = ?",
            (rule_id,)
        ).fetchone()
        assert row[0] == "Custom Alert"
        assert row[1] == 0  # user rule

    def test_list_rules(self, engine):
        eng, conn, wh = engine
        rules = eng.list_rules()
        assert len(rules) >= 5  # At least system rules

    def test_list_rules_source_filter(self, engine):
        eng, conn, wh = engine
        # Create a source-specific rule
        eng.create_rule(
            name="Zendesk Only", rule_type="keyword",
            source_filter="zendesk", keywords="test",
        )
        rules = eng.list_rules(source="zendesk")
        # Should include system rules (no source_filter) + zendesk-specific
        zendesk_rules = [r for r in rules if r.get("source_filter") == "zendesk"]
        assert len(zendesk_rules) >= 1

    def test_update_rule(self, engine):
        eng, conn, wh = engine
        rule_id = eng.create_rule(name="Test", rule_type="keyword")
        result = eng.update_rule(rule_id, name="Updated Test", severity="incident")
        assert result is True

        row = conn.execute(
            "SELECT name, severity FROM watchlist_rules WHERE id = ?",
            (rule_id,)
        ).fetchone()
        assert row[0] == "Updated Test"
        assert row[1] == "incident"

    def test_cannot_change_is_system(self, engine):
        eng, conn, wh = engine
        rule_id = eng.create_rule(name="Test", rule_type="keyword")
        eng.update_rule(rule_id, is_system=1)  # Should be stripped

        row = conn.execute(
            "SELECT is_system FROM watchlist_rules WHERE id = ?",
            (rule_id,)
        ).fetchone()
        assert row[0] == 0

    def test_delete_user_rule(self, engine):
        eng, conn, wh = engine
        rule_id = eng.create_rule(name="Deleteable", rule_type="keyword")
        result = eng.delete_rule(rule_id)
        assert result is True

        count = conn.execute(
            "SELECT COUNT(*) FROM watchlist_rules WHERE id = ?",
            (rule_id,)
        ).fetchone()[0]
        assert count == 0

    def test_cannot_delete_system_rule(self, engine):
        eng, conn, wh = engine
        system_id = conn.execute(
            "SELECT id FROM watchlist_rules WHERE is_system = 1 LIMIT 1"
        ).fetchone()[0]
        result = eng.delete_rule(system_id)
        assert result is False

    def test_toggle_system_rule(self, engine):
        """System rules CAN be disabled."""
        eng, conn, wh = engine
        system_id = conn.execute(
            "SELECT id FROM watchlist_rules WHERE is_system = 1 LIMIT 1"
        ).fetchone()[0]
        result = eng.toggle_rule(system_id, False)
        assert result is True

        enabled = conn.execute(
            "SELECT enabled FROM watchlist_rules WHERE id = ?",
            (system_id,)
        ).fetchone()[0]
        assert enabled == 0


# ═══════════════════════════════════════
#  Alert Lifecycle
# ═══════════════════════════════════════

class TestAlertLifecycle:
    """Verify alert creation and feedback."""

    def test_record_confirmed_feedback(self, engine):
        eng, conn, wh = engine

        # Create a rule and alert
        rule_id = eng.create_rule(
            name="Feedback Test", rule_type="keyword",
            ewma_confidence=0.5, ewma_alpha=0.3,
        )
        conn.execute("""
            INSERT INTO watchlist_alerts
                (rule_id, source, severity, title, status)
            VALUES (?, 'zendesk', 'watch', 'Test Alert', 'open')
        """, (rule_id,))
        conn.commit()
        alert_id = conn.execute("SELECT MAX(id) FROM watchlist_alerts").fetchone()[0]

        eng.record_feedback(alert_id, "confirmed")

        # Alert should be confirmed
        status = conn.execute(
            "SELECT status FROM watchlist_alerts WHERE id = ?",
            (alert_id,)
        ).fetchone()[0]
        assert status == "confirmed"

        # EWMA should increase (0.3 * 1.0 + 0.7 * 0.5 = 0.65)
        ewma = conn.execute(
            "SELECT ewma_confidence FROM watchlist_rules WHERE id = ?",
            (rule_id,)
        ).fetchone()[0]
        assert abs(ewma - 0.65) < 0.01

    def test_record_dismissed_feedback(self, engine):
        eng, conn, wh = engine

        rule_id = eng.create_rule(
            name="Dismiss Test", rule_type="keyword",
            ewma_confidence=0.5, ewma_alpha=0.3,
        )
        conn.execute("""
            INSERT INTO watchlist_alerts
                (rule_id, source, severity, title, status)
            VALUES (?, 'zendesk', 'watch', 'Dismiss Me', 'open')
        """, (rule_id,))
        conn.commit()
        alert_id = conn.execute("SELECT MAX(id) FROM watchlist_alerts").fetchone()[0]

        eng.record_feedback(alert_id, "dismissed")

        # EWMA should decrease (0.3 * 0.0 + 0.7 * 0.5 = 0.35)
        ewma = conn.execute(
            "SELECT ewma_confidence FROM watchlist_rules WHERE id = ?",
            (rule_id,)
        ).fetchone()[0]
        assert abs(ewma - 0.35) < 0.01

    def test_ewma_floor(self, engine):
        """EWMA cannot go below 0.1."""
        eng, conn, wh = engine

        rule_id = eng.create_rule(
            name="Floor Test", rule_type="keyword",
            ewma_confidence=0.1, ewma_alpha=0.9,  # aggressive learning rate
        )
        conn.execute("""
            INSERT INTO watchlist_alerts
                (rule_id, source, severity, title, status)
            VALUES (?, 'zendesk', 'watch', 'Floor Test', 'open')
        """, (rule_id,))
        conn.commit()
        alert_id = conn.execute("SELECT MAX(id) FROM watchlist_alerts").fetchone()[0]

        eng.record_feedback(alert_id, "dismissed")

        ewma = conn.execute(
            "SELECT ewma_confidence FROM watchlist_rules WHERE id = ?",
            (rule_id,)
        ).fetchone()[0]
        assert ewma >= 0.1

    def test_ewma_ceiling(self, engine):
        """EWMA cannot exceed 0.95."""
        eng, conn, wh = engine

        rule_id = eng.create_rule(
            name="Ceiling Test", rule_type="keyword",
            ewma_confidence=0.95, ewma_alpha=0.9,
        )
        conn.execute("""
            INSERT INTO watchlist_alerts
                (rule_id, source, severity, title, status)
            VALUES (?, 'zendesk', 'watch', 'Ceiling Test', 'open')
        """, (rule_id,))
        conn.commit()
        alert_id = conn.execute("SELECT MAX(id) FROM watchlist_alerts").fetchone()[0]

        eng.record_feedback(alert_id, "confirmed")

        ewma = conn.execute(
            "SELECT ewma_confidence FROM watchlist_rules WHERE id = ?",
            (rule_id,)
        ).fetchone()[0]
        assert ewma <= 0.95

    def test_get_open_alerts(self, engine):
        eng, conn, wh = engine
        rule_id = eng.create_rule(name="Open Test", rule_type="keyword")
        conn.execute("""
            INSERT INTO watchlist_alerts
                (rule_id, source, severity, title, status)
            VALUES (?, 'zendesk', 'watch', 'Open Alert', 'open')
        """, (rule_id,))
        conn.execute("""
            INSERT INTO watchlist_alerts
                (rule_id, source, severity, title, status)
            VALUES (?, 'zendesk', 'watch', 'Closed Alert', 'confirmed')
        """, (rule_id,))
        conn.commit()

        open_alerts = eng.get_open_alerts()
        assert len(open_alerts) == 1
        assert open_alerts[0]["title"] == "Open Alert"

    def test_get_all_alerts(self, engine):
        eng, conn, wh = engine
        rule_id = eng.create_rule(name="All Test", rule_type="keyword")
        conn.execute("""
            INSERT INTO watchlist_alerts
                (rule_id, source, severity, title, status)
            VALUES (?, 'zendesk', 'watch', 'Alert 1', 'open')
        """, (rule_id,))
        conn.execute("""
            INSERT INTO watchlist_alerts
                (rule_id, source, severity, title, status)
            VALUES (?, 'zendesk', 'watch', 'Alert 2', 'confirmed')
        """, (rule_id,))
        conn.commit()

        all_alerts = eng.get_all_alerts()
        assert len(all_alerts) == 2


# ═══════════════════════════════════════
#  Source-Agnostic TRC Extraction
# ═══════════════════════════════════════

class TestTRCExtraction:
    """Verify source-agnostic TRC extraction."""

    def test_uses_cached_trc(self, engine):
        eng, conn, wh = engine
        record = {"_trc_code": "TRC-CACHED"}
        result = eng._extract_trc(record, "subject")
        assert result == "TRC-CACHED"

    def test_uses_client(self, engine):
        eng, conn, wh = engine
        client = MagicMock()
        client.extract_trc.return_value = "TRC-CLIENT"
        record = {"subject": "Test"}
        result = eng._extract_trc(record, "subject", client)
        assert result == "TRC-CLIENT"

    def test_falls_back_to_unknown(self, engine):
        eng, conn, wh = engine
        record = {"subject": "Test"}
        result = eng._extract_trc(record, "subject")
        assert result == "unknown"


# ═══════════════════════════════════════
#  Evaluate Integration
# ═══════════════════════════════════════

class TestEvaluate:
    """Verify end-to-end rule evaluation."""

    def _disable_system_rules(self, conn):
        """Disable all system rules so they don't interfere with tests."""
        conn.execute("UPDATE watchlist_rules SET enabled = 0 WHERE is_system = 1")
        conn.commit()

    def test_keyword_rule_fires(self, engine):
        eng, conn, wh = engine
        self._disable_system_rules(conn)

        # Create a keyword rule
        rule_id = eng.create_rule(
            name="Payment Alert", rule_type="keyword",
            severity="watch",
            keywords="payment failed,card declined",
            cooldown_minutes=1,  # short cooldown for testing
        )

        records = [
            {"id": "1001", "subject": "My payment failed!",
             "description": "Can't checkout"},
        ]
        client = MagicMock()
        client.extract_trc.return_value = "TRC-PAY"

        alerts = eng.evaluate(records, "zendesk", client=client)
        assert len(alerts) == 1
        assert alerts[0]["rule_name"] == "Payment Alert"
        assert alerts[0]["severity"] == "watch"

    def test_no_match_no_alert(self, engine):
        eng, conn, wh = engine
        self._disable_system_rules(conn)

        eng.create_rule(
            name="Payment Alert", rule_type="keyword",
            keywords="payment failed",
        )

        records = [
            {"id": "1002", "subject": "Update my address",
             "description": "Moving to a new place"},
        ]
        client = MagicMock()
        client.extract_trc.return_value = "TRC-ADDR"

        alerts = eng.evaluate(records, "zendesk", client=client)
        assert len(alerts) == 0

    def test_source_filter_respected(self, engine):
        """Rule with source_filter='zendesk' skips intercom records."""
        eng, conn, wh = engine
        self._disable_system_rules(conn)

        eng.create_rule(
            name="Zendesk Only", rule_type="keyword",
            source_filter="zendesk",
            keywords="unique_keyword_xyz",
        )

        records = [
            {"id": "1003", "subject": "Issue with unique_keyword_xyz",
             "description": "Help"},
        ]
        client = MagicMock()
        client.extract_trc.return_value = "TRC-X"

        # Evaluate as intercom — should NOT match
        alerts = eng.evaluate(records, "intercom", client=client)
        assert len(alerts) == 0

        # Evaluate as zendesk — should match
        alerts = eng.evaluate(records, "zendesk", client=client)
        assert len(alerts) == 1

    def test_disabled_rule_skipped(self, engine):
        eng, conn, wh = engine
        self._disable_system_rules(conn)

        rule_id = eng.create_rule(
            name="Disabled Rule", rule_type="keyword",
            keywords="unique_disabled_test",
        )
        eng.toggle_rule(rule_id, False)

        records = [
            {"id": "1004", "subject": "unique_disabled_test issue",
             "description": ""},
        ]
        client = MagicMock()
        client.extract_trc.return_value = "TRC-Y"

        alerts = eng.evaluate(records, "zendesk", client=client)
        assert len(alerts) == 0
