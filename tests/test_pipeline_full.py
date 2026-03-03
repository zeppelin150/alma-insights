"""
Alma Insights -- Full Pipeline Test (Pass 5.0)

Unit tests for the agentic NLP pipeline components.
No Gemini API calls required -- all tests use mocks or in-memory SQLite.

Run: python -m pytest tests/test_pipeline_full.py -v
"""

import json
import os
import sqlite3
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root on path
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def create_test_db():
    """Create an in-memory SQLite DB with the required schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # Core tables
    conn.executescript("""
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY,
            ticket_id TEXT,
            trc_code TEXT,
            role TEXT,
            message TEXT,
            created_at TEXT,
            content_hash TEXT,
            full_thread TEXT,
            message_count INTEGER DEFAULT 1,
            status TEXT DEFAULT 'solved',
            thread_preview TEXT
        );

        CREATE TABLE sub_patterns (
            pattern_id TEXT PRIMARY KEY,
            trc TEXT,
            label TEXT,
            description TEXT,
            friction_type TEXT,
            tier TEXT DEFAULT 'active',
            lifetime_tickets INTEGER DEFAULT 0,
            merged_into TEXT
        );

        CREATE TABLE sub_pattern_ngrams (
            ngram_id INTEGER PRIMARY KEY,
            pattern_id TEXT,
            ngram TEXT,
            frequency REAL DEFAULT 0,
            specificity REAL DEFAULT 0.5
        );

        CREATE TABLE incident_flags (
            flag_id INTEGER PRIMARY KEY,
            trc_code TEXT,
            flag_type TEXT,
            flagged_date TEXT,
            description TEXT,
            status TEXT DEFAULT 'open'
        );

        CREATE TABLE anomaly_flags (
            id INTEGER PRIMARY KEY,
            trc_code TEXT,
            metric_type TEXT,
            date TEXT,
            direction TEXT
        );

        CREATE TABLE tfidf_scores (
            id INTEGER PRIMARY KEY,
            trc TEXT,
            term TEXT,
            score REAL
        );

        CREATE TABLE nlp_ticket_classifications (
            classification_id TEXT PRIMARY KEY,
            batch_id TEXT,
            scan_id TEXT,
            ticket_id TEXT,
            trc TEXT,
            sub_cluster TEXT,
            sub_cluster_confidence REAL,
            is_novel INTEGER DEFAULT 0,
            sentiment_intensity INTEGER,
            sentiment_polarity TEXT,
            friction_type TEXT,
            anomaly_flag TEXT,
            anomaly_reason TEXT,
            entities_json TEXT,
            key_phrases TEXT,
            root_cause_hint TEXT,
            summary TEXT,
            raw_classification TEXT,
            created_at TEXT
        );

        CREATE TABLE nlp_scan_runs (
            scan_id TEXT PRIMARY KEY,
            created_at TEXT,
            status TEXT,
            date_range_start TEXT,
            date_range_end TEXT,
            trc_filter TEXT,
            mode TEXT,
            batch_strategy TEXT,
            total_batches INTEGER,
            total_tickets INTEGER,
            completed_batches INTEGER DEFAULT 0,
            estimated_cost_usd REAL,
            actual_cost_usd REAL DEFAULT 0,
            budget_cap_usd REAL,
            config_snapshot TEXT,
            completed_at TEXT
        );

        CREATE TABLE nlp_batches (
            batch_id TEXT PRIMARY KEY,
            scan_id TEXT,
            batch_number INTEGER,
            trc TEXT,
            trc_chunk INTEGER,
            trc_chunk_total INTEGER,
            status TEXT,
            worker_id TEXT,
            created_at TEXT,
            completed_at TEXT
        );

        -- Agent pipeline tables (Phase 1 Step 1)
        CREATE TABLE agent_health (
            agent_id TEXT PRIMARY KEY,
            scan_id TEXT,
            status TEXT DEFAULT 'idle',
            batches_done INTEGER DEFAULT 0,
            tickets_done INTEGER DEFAULT 0,
            tool_calls INTEGER DEFAULT 0,
            parse_rate REAL DEFAULT 1.0,
            avg_confidence REAL DEFAULT 0.0,
            context_tokens INTEGER DEFAULT 0,
            last_progress TEXT,
            updated_at TEXT
        );

        CREATE TABLE scan_progress (
            scan_id TEXT PRIMARY KEY,
            classified INTEGER DEFAULT 0,
            total INTEGER DEFAULT 0,
            tool_calls INTEGER DEFAULT 0,
            batches_complete INTEGER DEFAULT 0,
            est_remaining_seconds REAL,
            est_confidence TEXT DEFAULT 'low',
            updated_at TEXT
        );

        CREATE TABLE review_flags (
            flag_id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            severity TEXT DEFAULT 'medium',
            agent_id TEXT,
            scan_id TEXT,
            created_at TEXT
        );

        CREATE TABLE analyst_reports (
            report_id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id TEXT NOT NULL,
            report_type TEXT NOT NULL,
            content TEXT,
            metrics TEXT,
            created_at TEXT
        );

        CREATE TABLE trc_batch_profiles (
            trc TEXT PRIMARY KEY,
            avg_chars_per_ticket REAL,
            updated_at TEXT
        );
    """)

    return conn


def seed_test_data(conn):
    """Insert sample data for testing."""
    # Sample conversations
    for i in range(10):
        msg = f"I have an issue with billing item {i}"
        full_thread = f"[Client] {msg}\n[Agent] Let me look into billing item {i} for you."
        conn.execute("""
            INSERT INTO conversations
                (ticket_id, trc_code, role, message, created_at,
                 full_thread, message_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            f"T-{1000+i}", "RCM_02", "customer",
            msg,
            f"2025-10-{15+i:02d} 10:00:00",
            full_thread, 2,
        ))

    # Sample sub-patterns
    conn.execute("""
        INSERT INTO sub_patterns (pattern_id, trc, label, description, friction_type, tier, lifetime_tickets)
        VALUES ('p1', 'RCM_02', 'Duplicate charge confusion', 'Customer sees duplicate', 'incorrect_charge', 'active', 45)
    """)
    conn.execute("""
        INSERT INTO sub_pattern_ngrams (pattern_id, ngram, frequency, specificity)
        VALUES ('p1', 'duplicate charge', 0.8, 0.9)
    """)

    # Sample incident flags
    conn.execute("""
        INSERT INTO incident_flags (trc_code, flag_type, flagged_date, description, status)
        VALUES ('RCM_02', 'spike', '2025-10-20', 'Volume spike detected', 'open')
    """)

    # Sample TF-IDF scores
    conn.execute("""
        INSERT INTO tfidf_scores (trc, term, score) VALUES ('RCM_02', 'duplicate', 0.85)
    """)
    conn.execute("""
        INSERT INTO tfidf_scores (trc, term, score) VALUES ('RCM_02', 'billing', 0.72)
    """)

    conn.commit()


# ═══════════════════════════════════════════════════════════════════════════
# RATE GOVERNOR TESTS
# ═══════════════════════════════════════════════════════════════════════════

class TestRateGovernor(unittest.TestCase):
    """Test the token bucket rate limiter."""

    def setUp(self):
        from src.agents.rate_governor import RateGovernor
        self.gov = RateGovernor(min_interval=0.1)  # fast for testing

    def test_acquire_immediate(self):
        """First acquire() should return True immediately."""
        self.assertTrue(self.gov.acquire(timeout=5))

    def test_acquire_blocks(self):
        """Second rapid acquire() should block until interval elapsed."""
        self.gov.acquire(timeout=5)
        start = time.time()
        self.assertTrue(self.gov.acquire(timeout=5))
        elapsed = time.time() - start
        self.assertGreaterEqual(elapsed, 0.05)  # at least some blocking

    def test_backoff_on_rate_limit(self):
        """report_rate_limit() should double the interval."""
        old = self.gov.min_interval
        self.gov.report_rate_limit()
        self.assertAlmostEqual(self.gov.min_interval, old * 2.0, places=3)

    def test_tighten_after_successes(self):
        """5 consecutive successes should tighten interval."""
        self.gov.min_interval = 25.0  # above 15s floor
        for _ in range(5):
            self.gov.report_success(0.5)
        self.assertAlmostEqual(self.gov.min_interval, 22.5, places=3)  # 25 * 0.9

    def test_backoff_cap(self):
        """Interval should cap at 120s."""
        self.gov.min_interval = 100.0
        self.gov.report_rate_limit()
        self.assertEqual(self.gov.min_interval, 120.0)

    def test_tighten_floor(self):
        """Interval should not go below 15s."""
        self.gov.min_interval = 15.0
        for _ in range(5):
            self.gov.report_success(0.5)
        self.assertEqual(self.gov.min_interval, 15.0)

    def test_estimate_completion_heuristic(self):
        """estimate_completion() with no data returns heuristic."""
        est = self.gov.estimate_completion(10)
        self.assertEqual(est["confidence"], "heuristic")
        self.assertGreater(est["seconds"], 0)

    def test_estimate_completion_with_data(self):
        """estimate_completion() with data returns measured estimate."""
        self.gov.min_interval = 0.01
        for _ in range(6):
            self.gov.acquire(timeout=1)
            self.gov.report_success(0.5)

        est = self.gov.estimate_completion(5)
        self.assertIn(est["confidence"], ("high", "medium", "low"))
        self.assertGreater(est["seconds"], 0)

    def test_throughput(self):
        """get_throughput() returns calls/min."""
        tp = self.gov.get_throughput()
        self.assertIsInstance(tp, float)

    def test_error_resets_consecutive(self):
        """report_error() resets consecutive success counter."""
        self.gov.min_interval = 1.0
        for _ in range(4):
            self.gov.report_success(0.5)
        self.gov.report_error()
        # One more success should NOT trigger tightening (counter reset)
        old = self.gov.min_interval
        self.gov.report_success(0.5)
        self.assertEqual(self.gov.min_interval, old)


# ═══════════════════════════════════════════════════════════════════════════
# BATCH PACKER TESTS
# ═══════════════════════════════════════════════════════════════════════════

class TestBatchPacker(unittest.TestCase):
    """Test dynamic batch sizing (5.2: model-adaptive)."""

    def setUp(self):
        self.conn = create_test_db()
        from src.agents.batch_packer import BatchPacker
        # Default model = gemini-2.0-flash (budget=20K, max=25)
        self.packer = BatchPacker(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_default_batch_size(self):
        """Unknown TRC with default model → 25 tickets/batch."""
        size = self.packer.compute_batch_size("UNKNOWN_TRC", 1000)
        self.assertEqual(size, 25)  # 20_000 / 800 = 25, capped at max

    def test_batch_size_clamping_max(self):
        """Batch size should not exceed model's max."""
        size = self.packer.compute_batch_size("UNKNOWN", 10000)
        self.assertLessEqual(size, self.packer._max_batch)

    def test_batch_size_clamping_min(self):
        """Batch size should not go below MIN_BATCH_SIZE."""
        from src.agents.batch_packer import MIN_BATCH_SIZE
        # Force very high chars_per_ticket to get tiny batch
        self.packer._trc_profiles["LARGE_OUTPUT"] = 100000
        size = self.packer.compute_batch_size("LARGE_OUTPUT", 1000)
        self.assertGreaterEqual(size, MIN_BATCH_SIZE)

    def test_respects_ticket_count(self):
        """Batch size should not exceed actual ticket count."""
        size = self.packer.compute_batch_size("UNKNOWN", 10)
        self.assertEqual(size, 10)

    def test_learn_from_results(self):
        """record_result() should adjust future batch sizes."""
        # Record that this TRC produces 1000 chars per ticket
        self.packer.record_result("TRC_BIG", 50, 50000)

        # Now batch size should be smaller
        size = self.packer.compute_batch_size("TRC_BIG", 1000)
        expected_chars = 800 * 0.7 + 1000 * 0.3  # EMA: old=800, new=1000
        expected_size = min(
            int(self.packer._output_budget / expected_chars),
            self.packer._max_batch,
        )
        self.assertEqual(size, expected_size)

    def test_halve_for_retry(self):
        """halve_for_retry() should return half, respecting min."""
        self.assertEqual(self.packer.halve_for_retry(20), 10)
        self.assertEqual(self.packer.halve_for_retry(8), 5)  # min=5

    def test_persistence_save_load(self):
        """Profiles should survive save/load cycle."""
        self.packer.record_result("TRC_TEST", 100, 50000)
        profile = self.packer.get_profile("TRC_TEST")
        self.assertNotEqual(profile, 800)  # should have changed from default

        # Create new packer with same conn (simulates restart)
        from src.agents.batch_packer import BatchPacker
        packer2 = BatchPacker(self.conn)
        profile2 = packer2.get_profile("TRC_TEST")
        self.assertAlmostEqual(profile, profile2, places=1)

    def test_get_all_profiles(self):
        """get_all_profiles() returns dict."""
        self.packer.record_result("A", 10, 5000)
        self.packer.record_result("B", 10, 3000)
        profiles = self.packer.get_all_profiles()
        self.assertIn("A", profiles)
        self.assertIn("B", profiles)

    # ── 5.2 Model-Adaptive Tests ──

    def test_model_25_flash_larger_batches(self):
        """gemini-2.5-flash gets budget=200K, max=75 (5.4 cap)."""
        from src.agents.batch_packer import BatchPacker
        packer = BatchPacker(self.conn, model="gemini-2.5-flash")
        self.assertEqual(packer._output_budget, 200_000)
        self.assertEqual(packer._max_batch, 75)
        self.assertEqual(packer._input_budget, 300_000)
        # 200K / 800 = 250 output, 300K / 3120 = 96 input, clamped to 75
        size = packer.compute_batch_size("UNKNOWN", 500)
        self.assertEqual(size, 75)

    def test_model_25_pro_larger_batches(self):
        """gemini-2.5-pro gets budget=200K, max=100."""
        from src.agents.batch_packer import BatchPacker
        packer = BatchPacker(self.conn, model="gemini-2.5-pro")
        self.assertEqual(packer._output_budget, 200_000)
        self.assertEqual(packer._max_batch, 100)

    def test_model_unknown_falls_back(self):
        """Unknown model uses default budget=20K, max=25."""
        from src.agents.batch_packer import BatchPacker
        packer = BatchPacker(self.conn, model="gemini-99-ultra")
        self.assertEqual(packer._output_budget, 20_000)
        self.assertEqual(packer._max_batch, 25)

    def test_model_20_flash_conservative(self):
        """gemini-2.0-flash uses conservative budget=20K, max=25."""
        from src.agents.batch_packer import BatchPacker
        packer = BatchPacker(self.conn, model="gemini-2.0-flash")
        self.assertEqual(packer._output_budget, 20_000)
        self.assertEqual(packer._max_batch, 25)


# ═══════════════════════════════════════════════════════════════════════════
# STREAM PARSER TESTS
# ═══════════════════════════════════════════════════════════════════════════

class TestStreamParser(unittest.TestCase):
    """Test fenced code block parser."""

    def setUp(self):
        from src.agents.stream_parser import StreamParser
        self.parser = StreamParser()

    def test_parse_tool_call(self):
        """Parse a tool_call fenced block."""
        from src.agents.stream_parser import StreamEvent
        text = '```tool_call\n{"tool": "query_taxonomy", "args": {"trc": "RCM_02"}}\n```'
        events = list(self.parser.feed(text))
        tool_events = [e for e in events if e.event_type == StreamEvent.TOOL_CALL]
        self.assertEqual(len(tool_events), 1)
        self.assertEqual(tool_events[0].data["tool"], "query_taxonomy")

    def test_parse_classification(self):
        """Parse a classification fenced block."""
        from src.agents.stream_parser import StreamEvent
        text = '```classification\n{"ticket_id": "T-1234", "sub_cluster": "billing issue"}\n```'
        events = list(self.parser.feed(text))
        cls_events = [e for e in events if e.event_type == StreamEvent.CLASSIFICATION]
        self.assertEqual(len(cls_events), 1)
        self.assertEqual(cls_events[0].data["ticket_id"], "T-1234")

    def test_parse_batch_complete(self):
        """Parse a batch_complete fenced block."""
        from src.agents.stream_parser import StreamEvent
        text = '```batch_complete\n{"classified": 50, "skipped": 0, "flagged": 2}\n```'
        events = list(self.parser.feed(text))
        bc_events = [e for e in events if e.event_type == StreamEvent.BATCH_COMPLETE]
        self.assertEqual(len(bc_events), 1)
        self.assertEqual(bc_events[0].data["classified"], 50)

    def test_partial_chunks(self):
        """Handle partial chunk feeds correctly."""
        from src.agents.stream_parser import StreamEvent
        # Feed in small chunks (closing ``` must not be split)
        chunks = [
            '```tool',
            '_call\n{"tool": "ping"',
            ', "args": {}}',
            '\n```',
        ]
        all_events = []
        for chunk in chunks:
            all_events.extend(self.parser.feed(chunk))

        tool_events = [e for e in all_events if e.event_type == StreamEvent.TOOL_CALL]
        self.assertEqual(len(tool_events), 1)
        self.assertEqual(tool_events[0].data["tool"], "ping")

    def test_mixed_content(self):
        """Text + fenced blocks interleaved."""
        from src.agents.stream_parser import StreamEvent
        text = (
            "Analyzing tickets...\n"
            '```classification\n{"ticket_id": "T-1"}\n```\n'
            "Moving to next ticket.\n"
            '```classification\n{"ticket_id": "T-2"}\n```'
        )
        events = list(self.parser.feed(text))
        events.extend(self.parser.flush())

        text_events = [e for e in events if e.event_type == StreamEvent.TEXT]
        cls_events = [e for e in events if e.event_type == StreamEvent.CLASSIFICATION]

        self.assertGreaterEqual(len(text_events), 1)
        self.assertEqual(len(cls_events), 2)

    def test_malformed_json_yields_error(self):
        """Malformed JSON in a fenced block should yield ERROR event."""
        from src.agents.stream_parser import StreamEvent
        text = '```classification\n{not valid json}\n```'
        events = list(self.parser.feed(text))
        error_events = [e for e in events if e.event_type == StreamEvent.ERROR]
        self.assertEqual(len(error_events), 1)

    def test_jsonl_multiple_objects(self):
        """Multiple JSON objects per line (JSONL format)."""
        from src.agents.stream_parser import StreamEvent
        text = '```classification\n{"ticket_id": "A"}\n{"ticket_id": "B"}\n```'
        events = list(self.parser.feed(text))
        cls_events = [e for e in events if e.event_type == StreamEvent.CLASSIFICATION]
        self.assertEqual(len(cls_events), 2)

    def test_flush_trailing_text(self):
        """flush() should emit trailing text."""
        from src.agents.stream_parser import StreamEvent
        list(self.parser.feed("Hello world"))
        events = list(self.parser.flush())
        text_events = [e for e in events if e.event_type == StreamEvent.TEXT]
        self.assertEqual(len(text_events), 1)
        self.assertIn("Hello world", text_events[0].raw)

    def test_reset(self):
        """reset() should clear all state."""
        # Must consume the generator to execute feed() logic
        list(self.parser.feed("```tool_call\n{"))
        self.assertTrue(self.parser.is_in_fence)
        self.parser.reset()
        self.assertFalse(self.parser.is_in_fence)

    # ── 5.2 Tool Call Classification Tests ──

    def test_tool_call_store_classification(self):
        """Parse store_classification tool_call blocks (5.2 format)."""
        from src.agents.stream_parser import StreamEvent
        text = (
            '```tool_call\n'
            '{"tool": "store_classification", "args": {'
            '"ticket_id": "T-1234", "sub_cluster": "billing confusion", '
            '"sub_cluster_confidence": 0.85, "is_novel": false, '
            '"friction_type": "incorrect_charge"'
            '}}\n```\n'
            '```tool_call\n'
            '{"tool": "store_classification", "args": {'
            '"ticket_id": "T-5678", "sub_cluster": "payer delay", '
            '"sub_cluster_confidence": 0.72, "is_novel": false, '
            '"friction_type": "process_delay"'
            '}}\n```'
        )
        events = list(self.parser.feed(text))
        events.extend(self.parser.flush())

        tool_events = [e for e in events if e.event_type == StreamEvent.TOOL_CALL]
        self.assertEqual(len(tool_events), 2)
        self.assertEqual(tool_events[0].data["tool"], "store_classification")
        self.assertEqual(tool_events[0].data["args"]["ticket_id"], "T-1234")
        self.assertEqual(tool_events[1].data["args"]["ticket_id"], "T-5678")

    def test_multiple_tool_call_blocks_streamed(self):
        """Tool call blocks arrive across multiple chunks (5.2 streaming)."""
        from src.agents.stream_parser import StreamEvent
        chunks = [
            '```tool_call\n{"tool": "store_clas',
            'sification", "args": {"ticket_id": "T-1", "sub_cluster": "test"}}\n',
            '```\n```tool_call\n{"tool": "store_classification", "args": ',
            '{"ticket_id": "T-2", "sub_cluster": "test2"}}\n```',
        ]
        all_events = []
        for chunk in chunks:
            all_events.extend(self.parser.feed(chunk))
        all_events.extend(self.parser.flush())

        tool_events = [e for e in all_events if e.event_type == StreamEvent.TOOL_CALL]
        self.assertEqual(len(tool_events), 2)


# ═══════════════════════════════════════════════════════════════════════════
# TOOL REGISTRY TESTS
# ═══════════════════════════════════════════════════════════════════════════

class TestToolRegistry(unittest.TestCase):
    """Test tool definitions and SQLite handlers."""

    def setUp(self):
        self.conn = create_test_db()
        seed_test_data(self.conn)
        # Write to temp file for ToolRegistry (it opens its own conn)
        self.db_path = os.path.join(
            os.path.dirname(__file__), "_test_tool_registry.db"
        )
        # Copy in-memory to file
        file_conn = sqlite3.connect(self.db_path)
        self.conn.backup(file_conn)
        file_conn.close()

        from src.agents.tool_registry import ToolRegistry
        self.registry = ToolRegistry(self.db_path)
        self.registry.set_context(
            scan_id="test_scan", batch_id="test_batch",
            trc="RCM_02", agent_id="worker_0"
        )

    def tearDown(self):
        self.registry.close()
        self.conn.close()
        try:
            os.unlink(self.db_path)
        except Exception:
            pass

    def test_query_taxonomy_known_trc(self):
        """query_taxonomy returns patterns for known TRC."""
        result = self.registry.execute(
            "query_taxonomy", {"trc": "RCM_02"}
        )
        self.assertEqual(result["trc"], "RCM_02")
        self.assertGreater(result["pattern_count"], 0)
        self.assertTrue(len(result["patterns"]) > 0)
        self.assertIn("ngrams", result["patterns"][0])

    def test_query_taxonomy_unknown_trc(self):
        """query_taxonomy returns empty for unknown TRC."""
        result = self.registry.execute(
            "query_taxonomy", {"trc": "FAKE_TRC"}
        )
        self.assertEqual(result["pattern_count"], 0)

    def test_get_stats_context(self):
        """get_stats_context returns incident flags and terms."""
        result = self.registry.execute(
            "get_stats_context", {"trc": "RCM_02"}
        )
        self.assertEqual(result["trc"], "RCM_02")
        self.assertTrue(len(result["incident_flags"]) > 0)
        self.assertTrue(len(result["rising_terms"]) > 0)

    def test_store_classification(self):
        """store_classification inserts and validates."""
        result = self.registry.execute("store_classification", {
            "ticket_id": "T-9999",
            "sub_cluster": "test pattern",
            "sub_cluster_confidence": 0.85,
            "is_novel": False,
            "sentiment_intensity": 3,
            "sentiment_polarity": "negative",
            "friction_type": "incorrect_charge",
            "anomaly_flag": "normal",
            "summary": "Test classification",
        })
        self.assertEqual(result["status"], "stored")
        self.assertEqual(result["ticket_id"], "T-9999")

        # Verify in DB
        verify_conn = sqlite3.connect(self.db_path)
        verify_conn.row_factory = sqlite3.Row
        row = verify_conn.execute(
            "SELECT * FROM nlp_ticket_classifications WHERE ticket_id = ?",
            ("T-9999",)
        ).fetchone()
        verify_conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["friction_type"], "incorrect_charge")

    def test_store_classification_validation(self):
        """store_classification clamps invalid values."""
        result = self.registry.execute("store_classification", {
            "ticket_id": "T-BAD",
            "sentiment_intensity": 99,      # should clamp to 5
            "friction_type": "invalid_type", # should default to "other"
            "sentiment_polarity": "banana",  # should default to "neutral"
            "anomaly_flag": "mega_bad",      # should default to None
        })
        self.assertEqual(result["status"], "stored")

        verify_conn = sqlite3.connect(self.db_path)
        verify_conn.row_factory = sqlite3.Row
        row = verify_conn.execute(
            "SELECT * FROM nlp_ticket_classifications WHERE ticket_id = ?",
            ("T-BAD",)
        ).fetchone()
        verify_conn.close()
        self.assertEqual(row["sentiment_intensity"], 5)
        self.assertEqual(row["friction_type"], "other")
        self.assertEqual(row["sentiment_polarity"], "neutral")
        self.assertIsNone(row["anomaly_flag"])

    def test_flag_for_review(self):
        """flag_for_review inserts review flag."""
        result = self.registry.execute("flag_for_review", {
            "ticket_id": "T-1000",
            "reason": "Ambiguous content",
            "severity": "high",
        })
        self.assertEqual(result["status"], "flagged")
        self.assertEqual(result["severity"], "high")

    def test_get_full_thread(self):
        """get_full_thread returns conversation messages."""
        result = self.registry.execute(
            "get_full_thread", {"ticket_id": "T-1000"}
        )
        self.assertEqual(result["ticket_id"], "T-1000")
        self.assertGreater(result["message_count"], 0)

    def test_unknown_tool(self):
        """Unknown tool returns error dict."""
        result = self.registry.execute("nonexistent_tool", {})
        self.assertIn("error", result)
        self.assertIn("available_tools", result)

    def test_check_cross_trc(self):
        """check_cross_trc searches for patterns."""
        result = self.registry.execute("check_cross_trc", {
            "pattern_label": "charge confusion",
            "exclude_trc": "SOME_OTHER_TRC",
        })
        # Should find our "Duplicate charge confusion" pattern
        self.assertGreater(result["match_count"], 0)

    def test_prompt_description(self):
        """get_prompt_description returns tool docs."""
        desc = self.registry.get_prompt_description()
        self.assertIn("query_taxonomy", desc)
        self.assertIn("store_classification", desc)
        self.assertIn("flag_for_review", desc)


# ═══════════════════════════════════════════════════════════════════════════
# SCHEMA TESTS
# ═══════════════════════════════════════════════════════════════════════════

class TestSchema(unittest.TestCase):
    """Test that all required tables exist with correct columns."""

    def setUp(self):
        self.conn = create_test_db()

    def tearDown(self):
        self.conn.close()

    def _get_columns(self, table):
        """Get column names for a table."""
        cursor = self.conn.execute(f"PRAGMA table_info({table})")
        return [row[1] for row in cursor.fetchall()]

    def test_agent_health_table(self):
        cols = self._get_columns("agent_health")
        for expected in ["agent_id", "scan_id", "status", "batches_done",
                         "tickets_done", "parse_rate", "context_tokens"]:
            self.assertIn(expected, cols, f"Missing column: {expected}")

    def test_scan_progress_table(self):
        cols = self._get_columns("scan_progress")
        for expected in ["scan_id", "classified", "total",
                         "est_remaining_seconds", "est_confidence"]:
            self.assertIn(expected, cols, f"Missing column: {expected}")

    def test_review_flags_table(self):
        cols = self._get_columns("review_flags")
        for expected in ["flag_id", "ticket_id", "reason",
                         "severity", "agent_id"]:
            self.assertIn(expected, cols, f"Missing column: {expected}")

    def test_analyst_reports_table(self):
        cols = self._get_columns("analyst_reports")
        for expected in ["report_id", "scan_id", "report_type",
                         "content", "metrics"]:
            self.assertIn(expected, cols, f"Missing column: {expected}")

    def test_trc_batch_profiles_table(self):
        cols = self._get_columns("trc_batch_profiles")
        for expected in ["trc", "avg_chars_per_ticket", "updated_at"]:
            self.assertIn(expected, cols, f"Missing column: {expected}")


# ═══════════════════════════════════════════════════════════════════════════
# BRIDGE WRAPPER TESTS (mock subprocess)
# ═══════════════════════════════════════════════════════════════════════════

class TestBridgeWrapper(unittest.TestCase):
    """Test GeminiBridge wrapper (mocked subprocess)."""

    def test_bridge_event_properties(self):
        """BridgeEvent properties work correctly."""
        from src.agents.gemini_bridge_wrapper import BridgeEvent
        evt = BridgeEvent("req1", "done", {"full_text": "ok", "elapsed_ms": 100})
        self.assertTrue(evt.is_terminal)
        self.assertFalse(evt.is_error)

        err_evt = BridgeEvent("req1", "error", {"error": "rate_limit", "recoverable": True})
        self.assertTrue(err_evt.is_terminal)
        self.assertTrue(err_evt.is_error)
        self.assertTrue(err_evt.is_recoverable)
        self.assertEqual(err_evt.error_code, "rate_limit")

    def test_bridge_find_script(self):
        """_find_bridge_script should locate the bridge."""
        from src.agents.gemini_bridge_wrapper import GeminiBridge
        script = GeminiBridge._find_bridge_script()
        if script:
            self.assertTrue(Path(script).exists())

    def test_bridge_repr(self):
        """repr works on uninitialized bridge."""
        from src.agents.gemini_bridge_wrapper import GeminiBridge
        bridge = GeminiBridge.__new__(GeminiBridge)
        bridge._process = None
        bridge._boot_count = 0
        bridge._total_calls = 0
        self.assertIn("dead", repr(bridge))


# ═══════════════════════════════════════════════════════════════════════════
# SUPERVISOR TESTS (mock workers)
# ═══════════════════════════════════════════════════════════════════════════

class TestSupervisor(unittest.TestCase):
    """Test deterministic supervisor logic."""

    def test_health_check_parse_rate(self):
        """Low parse rate triggers restart."""
        from src.agents.supervisor import Supervisor

        mock_worker = MagicMock()
        mock_worker.agent_id = "worker_0"
        mock_worker.get_health.return_value = {
            "agent_id": "worker_0",
            "status": "active",
            "batches_done": 6,      # must be >= 5 for checks
            "tickets_done": 100,
            "tool_calls": 50,
            "parse_rate": 0.30,     # below threshold (0.50)
            "avg_confidence": 0.80,
            "context_tokens": 50000,
            "last_progress": 10,
        }

        from src.agents.rate_governor import RateGovernor
        gov = RateGovernor()
        db_path = os.path.join(os.path.dirname(__file__), "_test_sup.db")

        # Create minimal DB
        conn = create_test_db()
        file_conn = sqlite3.connect(db_path)
        conn.backup(file_conn)
        file_conn.close()
        conn.close()

        sup = Supervisor([mock_worker], gov, db_path, "test_scan")
        sup._check_health(mock_worker, mock_worker.get_health())
        mock_worker.reset.assert_called_once()

        try:
            os.unlink(db_path)
        except Exception:
            pass

    def test_all_complete(self):
        """_all_complete returns True when batches match."""
        from src.agents.supervisor import Supervisor
        from src.agents.rate_governor import RateGovernor

        sup = Supervisor([], RateGovernor(), ":memory:", "test")
        sup._total_batches = 5
        sup._completed_batches = 5
        self.assertTrue(sup._all_complete())

        sup._completed_batches = 3
        self.assertFalse(sup._all_complete())


# ═══════════════════════════════════════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
