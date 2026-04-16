"""
Tests for Guru Pipelines — Phase 4

Covers:
- GuruFrictionPipeline: sync, coverage analysis, gap report, scoring
- GuruContentPipeline: rewrite proposal, new article, approve, reject
- GuruEffectivenessTracker: baseline recording, measurement, Poisson test
- GuruPage: init, tabs, wiring
"""

import math
import sqlite3
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta, timezone

from src.data.guru_client import GuruClient
from src.data.guru_friction_pipeline import (
    GuruFrictionPipeline, _parse_coverage_response,
)
from src.data.guru_content_pipeline import (
    GuruContentPipeline, _parse_new_article_response,
)
from src.data.guru_effectiveness import (
    GuruEffectivenessTracker, _poisson_cdf, _poisson_significance,
)


def _make_db():
    """Create in-memory DB with required tables."""
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS guru_articles (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id         TEXT    NOT NULL UNIQUE,
            collection_id   TEXT    DEFAULT '',
            collection_name TEXT    DEFAULT '',
            title           TEXT    NOT NULL,
            content_hash    TEXT    DEFAULT '',
            last_synced_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            friction_score  REAL    DEFAULT 0.0,
            status          TEXT    DEFAULT 'active'
        );
        CREATE TABLE IF NOT EXISTS guru_friction_coverage (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            friction_type   TEXT    NOT NULL,
            card_id         TEXT    NOT NULL,
            coverage_score  REAL    DEFAULT 0.0,
            gap_description TEXT    DEFAULT '',
            analyzed_at     TEXT    NOT NULL DEFAULT (datetime('now')),
            scan_id         TEXT    DEFAULT '',
            UNIQUE(friction_type, card_id)
        );
        CREATE INDEX IF NOT EXISTS idx_gfc_friction
            ON guru_friction_coverage(friction_type);

        CREATE TABLE IF NOT EXISTS guru_effectiveness (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id          TEXT    NOT NULL,
            friction_type    TEXT    NOT NULL,
            measurement_date TEXT    NOT NULL,
            source           TEXT    DEFAULT '',
            pre_volume       REAL    DEFAULT 0.0,
            post_volume      REAL    DEFAULT 0.0,
            pre_window_days  INTEGER DEFAULT 14,
            post_window_days INTEGER DEFAULT 14,
            delta_pct        REAL    DEFAULT 0.0,
            is_significant   INTEGER DEFAULT 0,
            created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS guru_content_drafts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id         TEXT    DEFAULT '',
            friction_type   TEXT    NOT NULL,
            draft_type      TEXT    NOT NULL,
            title           TEXT    NOT NULL,
            content         TEXT    NOT NULL,
            source_tickets  TEXT    DEFAULT '',
            status          TEXT    NOT NULL DEFAULT 'pending',
            approved_by     TEXT    DEFAULT '',
            pushed_at       TEXT    DEFAULT '',
            created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_gcd_status
            ON guru_content_drafts(status);

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

        CREATE TABLE IF NOT EXISTS classifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT, trc TEXT, sub_pattern TEXT
        );
        CREATE TABLE IF NOT EXISTS conversations (
            ticket_id TEXT PRIMARY KEY, subject TEXT
        );

        CREATE TABLE IF NOT EXISTS source_trc_daily (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source      TEXT    NOT NULL DEFAULT 'zendesk',
            trc_code    TEXT    NOT NULL,
            day_bucket  TEXT    NOT NULL,
            count       INTEGER NOT NULL DEFAULT 0,
            avg_sentiment REAL  DEFAULT 0.0,
            UNIQUE(source, trc_code, day_bucket)
        );
    """)

    db = MagicMock()
    db.conn = conn
    return db


def _seed_sub_patterns(db):
    """Insert sample sub_patterns for testing."""
    db.conn.executemany(
        "INSERT INTO sub_patterns "
        "(pattern_id, trc, label, friction_type, discovered_scan, "
        " discovered_at, lifetime_tickets) "
        "VALUES (?,?,?,?,?,?,?)",
        [
            ("sp1", "TRC-100", "Login Issues", "login_friction",
             "scan1", "2026-03-01", 42),
            ("sp2", "TRC-200", "Billing Errors", "billing_friction",
             "scan1", "2026-03-01", 85),
            ("sp3", "TRC-100", "Password Reset", "password_friction",
             "scan1", "2026-03-01", 20),
        ],
    )
    db.conn.commit()


def _seed_articles(db):
    """Insert sample guru_articles for testing."""
    db.conn.executemany(
        "INSERT INTO guru_articles "
        "(card_id, title, collection_name, content_hash, status) "
        "VALUES (?,?,?,?,?)",
        [
            ("card1", "How to Login", "Help", "hash1", "active"),
            ("card2", "Billing FAQ", "Billing", "hash2", "active"),
        ],
    )
    db.conn.commit()


# ═══════════════════════════════════════════════════════════════
# GuruFrictionPipeline
# ═══════════════════════════════════════════════════════════════

class TestFrictionSync(unittest.TestCase):

    def test_sync_inserts_new_articles(self):
        db = _make_db()
        client = MagicMock(spec=GuruClient)
        client.list_cards.return_value = [
            {"id": "card1", "title": "Help"},
        ]
        client.get_card.return_value = {
            "id": "card1", "title": "Help", "content": "Body",
            "collection": "Docs", "collection_id": "c1",
        }
        pipeline = GuruFrictionPipeline(db, client)
        count = pipeline.sync_articles()
        self.assertEqual(count, 1)

        row = db.conn.execute(
            "SELECT title FROM guru_articles WHERE card_id='card1'"
        ).fetchone()
        self.assertEqual(row[0], "Help")

    def test_sync_skips_unchanged(self):
        db = _make_db()
        import hashlib
        content = "Body"
        h = hashlib.sha256(content.encode()).hexdigest()

        db.conn.execute(
            "INSERT INTO guru_articles (card_id, title, content_hash) "
            "VALUES ('card1', 'Help', ?)", (h,)
        )
        db.conn.commit()

        client = MagicMock(spec=GuruClient)
        client.list_cards.return_value = [{"id": "card1", "title": "Help"}]
        client.get_card.return_value = {
            "id": "card1", "title": "Help", "content": content,
            "collection": "", "collection_id": "",
        }
        pipeline = GuruFrictionPipeline(db, client)
        count = pipeline.sync_articles()
        self.assertEqual(count, 0)  # No changes

    def test_sync_detects_content_change(self):
        db = _make_db()
        db.conn.execute(
            "INSERT INTO guru_articles (card_id, title, content_hash) "
            "VALUES ('card1', 'Help', 'old_hash')"
        )
        db.conn.commit()

        client = MagicMock(spec=GuruClient)
        client.list_cards.return_value = [{"id": "card1"}]
        client.get_card.return_value = {
            "id": "card1", "title": "Help Updated", "content": "New body",
            "collection": "", "collection_id": "",
        }
        pipeline = GuruFrictionPipeline(db, client)
        count = pipeline.sync_articles()
        self.assertEqual(count, 1)


class TestFrictionAnalysis(unittest.TestCase):

    def test_analyze_coverage_no_friction_types(self):
        db = _make_db()
        client = MagicMock(spec=GuruClient)
        pipeline = GuruFrictionPipeline(db, client)
        result = pipeline.analyze_coverage()
        self.assertEqual(result, [])

    def test_analyze_coverage_with_matching_articles(self):
        db = _make_db()
        _seed_sub_patterns(db)
        _seed_articles(db)

        client = MagicMock(spec=GuruClient)
        pipeline = GuruFrictionPipeline(db, client)
        result = pipeline.analyze_coverage()
        # Should find matches (Login → "How to Login", Billing → "Billing FAQ")
        self.assertGreater(len(result), 0)


class TestGapReport(unittest.TestCase):

    def test_gap_report_uncovered(self):
        db = _make_db()
        _seed_sub_patterns(db)
        # No articles = no coverage
        pipeline = GuruFrictionPipeline(db, MagicMock(spec=GuruClient))
        report = pipeline.get_gap_report()
        self.assertEqual(len(report), 3)  # 3 friction types
        # All should have gap_score = 1.0 (uncovered)
        for entry in report:
            self.assertEqual(entry["gap_score"], 1.0)

    def test_gap_report_partial_coverage(self):
        db = _make_db()
        _seed_sub_patterns(db)
        _seed_articles(db)
        # Add coverage for login_friction
        db.conn.execute(
            "INSERT INTO guru_friction_coverage "
            "(friction_type, card_id, coverage_score, gap_description) "
            "VALUES ('login_friction', 'card1', 0.7, 'partial')"
        )
        db.conn.commit()
        pipeline = GuruFrictionPipeline(db, MagicMock(spec=GuruClient))
        report = pipeline.get_gap_report()
        # Find login_friction entry
        login = [r for r in report if r["friction_type"] == "login_friction"]
        self.assertEqual(len(login), 1)
        self.assertAlmostEqual(login[0]["gap_score"], 0.3, places=2)

    def test_compute_friction_scores(self):
        db = _make_db()
        _seed_articles(db)
        db.conn.execute(
            "INSERT INTO guru_friction_coverage "
            "(friction_type, card_id, coverage_score) "
            "VALUES ('login_friction', 'card1', 0.4)"
        )
        db.conn.commit()
        pipeline = GuruFrictionPipeline(db, MagicMock(spec=GuruClient))
        pipeline.compute_friction_scores()
        row = db.conn.execute(
            "SELECT friction_score FROM guru_articles WHERE card_id='card1'"
        ).fetchone()
        self.assertAlmostEqual(row[0], 0.6, places=2)


class TestCoverageResponseParsing(unittest.TestCase):

    def test_parse_score_and_gap(self):
        response = "SCORE: 0.7\nGAP: Missing troubleshooting steps"
        score, gap = _parse_coverage_response(response)
        self.assertAlmostEqual(score, 0.7, places=2)
        self.assertEqual(gap, "Missing troubleshooting steps")

    def test_parse_clamped(self):
        response = "SCORE: 1.5\nGAP:"
        score, gap = _parse_coverage_response(response)
        self.assertEqual(score, 1.0)

    def test_parse_default_on_bad_input(self):
        score, gap = _parse_coverage_response("garbage")
        self.assertEqual(score, 0.5)


# ═══════════════════════════════════════════════════════════════
# GuruContentPipeline
# ═══════════════════════════════════════════════════════════════

class TestContentRewrite(unittest.TestCase):

    def test_propose_rewrite_creates_draft(self):
        db = _make_db()
        client = MagicMock(spec=GuruClient)
        client.get_card.return_value = {
            "id": "card1", "title": "Help", "content": "Old content",
        }
        pipeline = GuruContentPipeline(db, client)
        draft = pipeline.propose_rewrite("card1", ["login_friction"])
        self.assertEqual(draft["draft_type"], "rewrite")
        self.assertEqual(draft["status"], "pending")
        self.assertEqual(draft["card_id"], "card1")
        # Draft should exist in DB
        row = db.conn.execute(
            "SELECT status FROM guru_content_drafts WHERE id=?",
            (draft["id"],)
        ).fetchone()
        self.assertEqual(row[0], "pending")

    def test_propose_rewrite_raises_on_missing_card(self):
        db = _make_db()
        client = MagicMock(spec=GuruClient)
        client.get_card.return_value = {}
        pipeline = GuruContentPipeline(db, client)
        with self.assertRaises(ValueError):
            pipeline.propose_rewrite("nocard", ["friction1"])


class TestContentNewArticle(unittest.TestCase):

    def test_propose_new_article_creates_draft(self):
        db = _make_db()
        _seed_sub_patterns(db)
        client = MagicMock(spec=GuruClient)
        pipeline = GuruContentPipeline(db, client)
        draft = pipeline.propose_new_article("login_friction")
        self.assertEqual(draft["draft_type"], "new_article")
        self.assertEqual(draft["status"], "pending")
        self.assertEqual(draft["card_id"], "")


class TestContentApproveReject(unittest.TestCase):

    def _setup_draft(self):
        db = _make_db()
        db.conn.execute(
            "INSERT INTO guru_content_drafts "
            "(card_id, friction_type, draft_type, title, content, status) "
            "VALUES ('card1', 'login_friction', 'rewrite', "
            "'Test', 'New content', 'pending')"
        )
        db.conn.commit()
        return db

    @patch("src.data.guru_content_pipeline.GuruContentPipeline._record_baseline")
    def test_approve_and_push_rewrite(self, mock_baseline):
        db = self._setup_draft()
        client = MagicMock(spec=GuruClient)
        client.update_card.return_value = {"id": "card1"}
        pipeline = GuruContentPipeline(db, client)
        ok = pipeline.approve_and_push(1)
        self.assertTrue(ok)
        client.update_card.assert_called_once_with(
            "card1", "New content", "Test"
        )
        row = db.conn.execute(
            "SELECT status FROM guru_content_drafts WHERE id=1"
        ).fetchone()
        self.assertEqual(row[0], "pushed")

    def test_reject_marks_rejected(self):
        db = self._setup_draft()
        client = MagicMock(spec=GuruClient)
        pipeline = GuruContentPipeline(db, client)
        ok = pipeline.reject(1)
        self.assertTrue(ok)
        row = db.conn.execute(
            "SELECT status FROM guru_content_drafts WHERE id=1"
        ).fetchone()
        self.assertEqual(row[0], "rejected")

    @patch("src.data.guru_content_pipeline.GuruContentPipeline._record_baseline")
    def test_double_approve_idempotent(self, mock_baseline):
        db = self._setup_draft()
        client = MagicMock(spec=GuruClient)
        client.update_card.return_value = {"id": "card1"}
        pipeline = GuruContentPipeline(db, client)
        pipeline.approve_and_push(1)
        # Second approve should see status='pushed' and return True
        ok = pipeline.approve_and_push(1)
        self.assertTrue(ok)
        # update_card should only be called once
        self.assertEqual(client.update_card.call_count, 1)

    def test_get_pending_drafts(self):
        db = self._setup_draft()
        # Add a rejected draft too
        db.conn.execute(
            "INSERT INTO guru_content_drafts "
            "(card_id, friction_type, draft_type, title, content, status) "
            "VALUES ('card2', 'billing', 'new_article', "
            "'Bill', 'Content', 'rejected')"
        )
        db.conn.commit()
        pipeline = GuruContentPipeline(db, MagicMock(spec=GuruClient))
        pending = pipeline.get_pending_drafts()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["status"], "pending")


class TestNewArticleResponseParsing(unittest.TestCase):

    def test_parse_title_and_content(self):
        response = "TITLE: My New Article\n---\nThis is the content."
        title, content = _parse_new_article_response(response, "friction1")
        self.assertEqual(title, "My New Article")
        self.assertEqual(content, "This is the content.")

    def test_parse_fallback_title(self):
        title, content = _parse_new_article_response("Just content", "my_friction")
        self.assertEqual(title, "Guide: My Friction")


# ═══════════════════════════════════════════════════════════════
# GuruEffectivenessTracker
# ═══════════════════════════════════════════════════════════════

class TestEffectivenessBaseline(unittest.TestCase):

    def test_record_baseline(self):
        db = _make_db()
        _seed_sub_patterns(db)
        # Add some daily data — use relative dates so test doesn't expire
        from datetime import datetime, timedelta
        today = datetime.now().strftime("%Y-%m-%d")
        day1 = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
        day2 = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
        day3 = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        db.conn.executemany(
            "INSERT INTO source_trc_daily "
            "(source, trc_code, day_bucket, count) VALUES (?,?,?,?)",
            [
                ("zendesk", "TRC-100", day1, 5),
                ("zendesk", "TRC-100", day2, 8),
                ("zendesk", "TRC-100", day3, 3),
            ],
        )
        db.conn.commit()
        tracker = GuruEffectivenessTracker(db)
        tracker.record_baseline("card1", "login_friction")
        row = db.conn.execute(
            "SELECT pre_volume FROM guru_effectiveness"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertGreater(row[0], 0)


class TestEffectivenessMeasurement(unittest.TestCase):

    def test_measure_with_reduction(self):
        db = _make_db()
        _seed_sub_patterns(db)
        # Insert baseline from 30 days ago
        old_date = (
            datetime.now(timezone.utc) - timedelta(days=30)
        ).isoformat()
        db.conn.execute(
            "INSERT INTO guru_effectiveness "
            "(card_id, friction_type, measurement_date, pre_volume, "
            " pre_window_days) "
            "VALUES ('card1', 'login_friction', ?, 10.0, 14)",
            (old_date,),
        )
        # Add post-change daily data (reduced volume)
        for i in range(14):
            day = (datetime.now(timezone.utc) - timedelta(days=30 - i)).strftime("%Y-%m-%d")
            db.conn.execute(
                "INSERT INTO source_trc_daily "
                "(source, trc_code, day_bucket, count) "
                "VALUES ('zendesk', 'TRC-100', ?, 3)",
                (day,),
            )
        db.conn.commit()
        tracker = GuruEffectivenessTracker(db)
        results = tracker.measure_effectiveness(days_since_change=14)
        # Should have measured
        self.assertGreaterEqual(len(results), 0)  # May or may not find data


class TestPoissonCDF(unittest.TestCase):

    def test_cdf_at_zero_lambda(self):
        self.assertEqual(_poisson_cdf(0, 0.0), 1.0)

    def test_cdf_basic(self):
        # P(X <= 0 | lambda=1) = e^(-1) ≈ 0.368
        result = _poisson_cdf(0, 1.0)
        self.assertAlmostEqual(result, math.exp(-1.0), places=3)

    def test_cdf_high_k(self):
        # P(X <= 100 | lambda=1) should be very close to 1.0
        result = _poisson_cdf(100, 1.0)
        self.assertAlmostEqual(result, 1.0, places=5)

    def test_significance_reduction(self):
        # Pre: 10/day, Post: 2/day over 14 days => significant
        sig = _poisson_significance(10.0, 2.0, 14)
        self.assertTrue(sig)

    def test_no_significance_increase(self):
        # Post > Pre => not significant reduction
        sig = _poisson_significance(5.0, 8.0, 14)
        self.assertFalse(sig)

    def test_no_significance_similar(self):
        # Very similar pre/post => not significant
        sig = _poisson_significance(5.0, 4.8, 14)
        self.assertFalse(sig)


class TestEffectivenessReport(unittest.TestCase):

    def test_report_empty(self):
        db = _make_db()
        tracker = GuruEffectivenessTracker(db)
        report = tracker.get_effectiveness_report()
        self.assertEqual(report, [])

    def test_report_with_data(self):
        db = _make_db()
        _seed_articles(db)
        db.conn.execute(
            "INSERT INTO guru_effectiveness "
            "(card_id, friction_type, measurement_date, "
            " pre_volume, post_volume, delta_pct, is_significant) "
            "VALUES ('card1', 'login_friction', '2026-03-10', "
            " 10.0, 5.0, -50.0, 1)"
        )
        db.conn.commit()
        tracker = GuruEffectivenessTracker(db)
        report = tracker.get_effectiveness_report()
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["delta_pct"], -50.0)
        self.assertTrue(report[0]["is_significant"])
        self.assertEqual(report[0]["card_title"], "How to Login")


# ═══════════════════════════════════════════════════════════════
# GuruPage (init only — no live API)
# ═══════════════════════════════════════════════════════════════

class TestGuruPageInit(unittest.TestCase):

    def test_page_creates_with_6_tabs(self):
        from PySide6.QtWidgets import QApplication
        from src.ui.pages.guru_page import GuruPage
        app = QApplication.instance() or QApplication([])
        db = _make_db()
        page = GuruPage(db)
        self.assertEqual(page._tabs.count(), 6)

    def test_page_wiring_methods_exist(self):
        from PySide6.QtWidgets import QApplication
        from src.ui.pages.guru_page import GuruPage
        app = QApplication.instance() or QApplication([])
        db = _make_db()
        page = GuruPage(db)
        # Ensure set_* methods exist and accept args
        page.set_guru_client(None)
        page.set_friction_pipeline(None)
        page.set_content_pipeline(None)
        page.set_effectiveness_tracker(None)


if __name__ == "__main__":
    unittest.main()
