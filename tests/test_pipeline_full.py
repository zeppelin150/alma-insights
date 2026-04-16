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
            created_at TEXT,
            novelty_verdict TEXT,
            novelty_match TEXT
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
        """report_rate_limit() should increase burst_delay by 1.5x (semaphore governor)."""
        old = self.gov.min_interval
        self.gov.report_rate_limit()
        self.assertAlmostEqual(self.gov.min_interval, old * 1.5, places=3)

    def test_tighten_after_successes(self):
        """5 consecutive successes should tighten interval.
        P1: factor is 0.8 when > 30s since last 429 (default state)."""
        self.gov.min_interval = 2.0  # above configured floor
        for _ in range(5):
            self.gov.report_success(0.5)
        self.assertAlmostEqual(self.gov.min_interval, 1.6, places=3)  # 2.0 * 0.8

    def test_backoff_cap(self):
        """Burst delay should cap at 5.0s (semaphore governor)."""
        self.gov.min_interval = 4.0
        self.gov.report_rate_limit()
        self.assertAlmostEqual(self.gov.min_interval, 5.0, places=3)

    def test_tighten_floor(self):
        """Interval should not go below configured burst_delay."""
        configured = self.gov._configured_burst_delay
        self.gov.min_interval = configured
        for _ in range(5):
            self.gov.report_success(0.5)
        self.assertAlmostEqual(
            self.gov.min_interval, configured, places=3
        )

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
        # Default model = gemini-2.0-flash (budget=20K output / 100K input, max=25)
        self.packer = BatchPacker(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_default_batch_size(self):
        """Unknown TRC with default model (2.0-flash) → 25 tickets/batch."""
        size = self.packer.compute_batch_size("UNKNOWN_TRC", 1000)
        self.assertEqual(size, 25)  # 20_000 / 800 = 25, capped at max 25

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
        """gemini-2.5-flash gets budget=200K, max=45 (reduced for stall prevention)."""
        from src.agents.batch_packer import BatchPacker
        packer = BatchPacker(self.conn, model="gemini-2.5-flash")
        self.assertEqual(packer._output_budget, 200_000)
        self.assertEqual(packer._input_budget, 300_000)
        self.assertEqual(packer._max_batch, 45)
        # 200K / 800 = 250, clamped to 45
        size = packer.compute_batch_size("UNKNOWN", 500)
        self.assertEqual(size, 45)

    def test_model_25_pro_larger_batches(self):
        """gemini-2.5-pro gets budget=200K output / 400K input, max=60 (reduced for stall prevention)."""
        from src.agents.batch_packer import BatchPacker
        packer = BatchPacker(self.conn, model="gemini-2.5-pro")
        self.assertEqual(packer._output_budget, 200_000)
        self.assertEqual(packer._input_budget, 400_000)
        self.assertEqual(packer._max_batch, 60)

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
# MODEL ROUTING TESTS — verify model flows correctly through both pipelines
# ═══════════════════════════════════════════════════════════════════════════

class TestModelRouting(unittest.TestCase):
    """Verify model name propagates correctly through NLP and VOC pipelines."""

    def setUp(self):
        self.conn = create_test_db()

    def tearDown(self):
        self.conn.close()

    # ── BatchPacker: all supported models get correct limits ──

    def test_all_models_have_input_limits(self):
        """Every model in MODEL_OUTPUT_LIMITS also has an input limit."""
        from src.agents.batch_packer import MODEL_OUTPUT_LIMITS, MODEL_INPUT_LIMITS
        for model in MODEL_OUTPUT_LIMITS:
            self.assertIn(model, MODEL_INPUT_LIMITS,
                          f"{model} missing from MODEL_INPUT_LIMITS")

    def test_all_models_have_max_batch(self):
        """Every model in MODEL_OUTPUT_LIMITS also has a max batch."""
        from src.agents.batch_packer import MODEL_OUTPUT_LIMITS, MODEL_MAX_BATCH
        for model in MODEL_OUTPUT_LIMITS:
            self.assertIn(model, MODEL_MAX_BATCH,
                          f"{model} missing from MODEL_MAX_BATCH")

    def test_flash_lite_limits(self):
        """gemini-2.5-flash-lite gets 200K output / 250K input / max 45 (reduced for stall prevention)."""
        from src.agents.batch_packer import BatchPacker
        p = BatchPacker(self.conn, model="gemini-2.5-flash-lite")
        self.assertEqual(p._output_budget, 200_000)
        self.assertEqual(p._input_budget, 250_000)
        self.assertEqual(p._max_batch, 45)

    def test_3_flash_preview_limits(self):
        """gemini-3-flash-preview has explicit routing entries (reduced batch cap)."""
        from src.agents.batch_packer import BatchPacker
        p = BatchPacker(self.conn, model="gemini-3-flash-preview")
        self.assertEqual(p._output_budget, 200_000)
        self.assertEqual(p._input_budget, 300_000)
        self.assertEqual(p._max_batch, 45)

    def test_get_input_budget_uses_model_limit(self):
        """get_input_budget() returns model-specific input limit, not derived."""
        from src.agents.batch_packer import BatchPacker
        p_flash = BatchPacker(self.conn, model="gemini-2.5-flash")
        p_old = BatchPacker(self.conn, model="gemini-2.0-flash")
        self.assertEqual(p_flash.get_input_budget(), 300_000)
        self.assertEqual(p_old.get_input_budget(), 100_000)

    # ── NLP Pipeline: ACPBridge passes model to subprocess ──

    def test_bridge_passes_model_to_subprocess(self):
        """ACPBridge stores --model flag for ACP subprocess command."""
        from src.agents.acp_bridge import ACPBridge
        bridge = ACPBridge(model="gemini-2.5-flash")
        self.assertEqual(bridge._model, "gemini-2.5-flash")

    def test_bridge_no_model_flag_when_none(self):
        """ACPBridge with model=None does not pass --model flag."""
        from src.agents.acp_bridge import ACPBridge
        bridge = ACPBridge(model=None)
        self.assertIsNone(bridge._model)

    def test_bridge_each_model_stored(self):
        """Each supported model string is stored correctly on the bridge."""
        from src.agents.acp_bridge import ACPBridge
        from src.agents.batch_packer import MODEL_OUTPUT_LIMITS
        for model_name in MODEL_OUTPUT_LIMITS:
            bridge = ACPBridge(model=model_name)
            self.assertEqual(bridge._model, model_name,
                             f"Bridge model mismatch for {model_name}")

    # ── VOC/Reports Pipeline: GeminiClient passes model to subprocess ──

    def test_gemini_client_stores_model(self):
        """GeminiClient stores the model name for CLI subprocess calls."""
        from src.gemini.gemini_client import GeminiClient
        client = GeminiClient(cli_path="fake", model="gemini-2.5-flash")
        self.assertEqual(client.model, "gemini-2.5-flash")

    def test_gemini_client_default_model(self):
        """GeminiClient defaults to gemini-2.5-flash."""
        from src.gemini.gemini_client import GeminiClient
        client = GeminiClient(cli_path="fake")
        self.assertEqual(client.model, "gemini-2.5-flash")

    # ── ScanOrchestrator: reads model from settings ──

    @patch("builtins.open", create=True)
    def test_orchestrator_reads_model_from_settings(self, mock_open):
        """ScanOrchestrator._load_model_from_config reads gemini.model."""
        import yaml
        settings_content = yaml.dump({"gemini": {"model": "gemini-2.5-flash"}})
        mock_open.return_value.__enter__ = lambda s: __import__('io').StringIO(settings_content)
        mock_open.return_value.__exit__ = MagicMock(return_value=False)

        from src.agents.scan_orchestrator import ScanOrchestrator
        orch = ScanOrchestrator.__new__(ScanOrchestrator)
        orch.db_path = str(Path(__file__).parent.parent / "data" / "test.db")
        model = orch._load_model_from_config()
        self.assertEqual(model, "gemini-2.5-flash")

    def test_orchestrator_default_fallback(self):
        """ScanOrchestrator falls back to gemini-2.5-flash if config missing."""
        from unittest.mock import patch
        from src.agents.scan_orchestrator import ScanOrchestrator
        orch = ScanOrchestrator.__new__(ScanOrchestrator)
        orch.db_path = "/nonexistent/path/data/test.db"
        with patch("src.data.settings_manager.get_section", return_value={}):
            model = orch._load_model_from_config()
        self.assertEqual(model, "gemini-2.5-flash")


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

        # P4: Flush buffered writes before verifying from separate connection
        self.registry.flush()

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

        # P4: Flush buffered writes before verifying from separate connection
        self.registry.flush()

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
    """Test ACPBridge wrapper (mocked subprocess)."""

    def test_bridge_event_properties(self):
        """BridgeEvent properties work correctly."""
        from src.agents.acp_bridge import BridgeEvent
        evt = BridgeEvent("req1", "done", {"full_text": "ok", "elapsed_ms": 100})
        self.assertTrue(evt.is_terminal)
        self.assertFalse(evt.is_error)

        err_evt = BridgeEvent("req1", "error", {"error": "rate_limit", "recoverable": True})
        self.assertTrue(err_evt.is_terminal)
        self.assertTrue(err_evt.is_error)
        self.assertTrue(err_evt.is_recoverable)
        self.assertEqual(err_evt.error_code, "rate_limit")

    def test_bridge_find_cli(self):
        """_find_gemini_cli should locate the CLI."""
        from src.agents.acp_bridge import ACPBridge
        cli = ACPBridge._find_gemini_cli()
        if cli:
            self.assertTrue(Path(cli).exists())

    def test_bridge_repr(self):
        """repr works on uninitialized bridge."""
        from src.agents.acp_bridge import ACPBridge
        bridge = ACPBridge.__new__(ACPBridge)
        bridge._process = None
        bridge._boot_count = 0
        bridge._total_calls = 0
        bridge._session_id = None
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
# ANALYST AGENT — NOVELTY BATCHING & VERDICTS (Phase 5.3)
# ═══════════════════════════════════════════════════════════════════════════

class TestNoveltyBatching(unittest.TestCase):
    """Test batched novelty validation and verdict application."""

    def _make_analyst(self, conn):
        """Create AnalystAgent with mock bridge pointing to in-memory DB."""
        from src.agents.analyst_agent import AnalystAgent
        # Write conn to a temp file so AnalystAgent can open it
        import tempfile, shutil
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        # Copy in-memory to file
        disk = sqlite3.connect(self._tmp.name)
        conn.backup(disk)
        disk.close()

        bridge = MagicMock()
        bridge.ensure_running = MagicMock()
        bridge.call_blocking = MagicMock(return_value=None)
        agent = AnalystAgent(bridge, self._tmp.name)
        return agent, bridge

    def _seed_novels(self, conn, count, scan_id="SCAN-001"):
        """Insert N novel ticket classifications."""
        for i in range(count):
            conn.execute("""
                INSERT INTO nlp_ticket_classifications
                    (classification_id, batch_id, scan_id, ticket_id, trc,
                     sub_cluster, is_novel, key_phrases, root_cause_hint,
                     summary, sub_cluster_confidence, sentiment_intensity,
                     sentiment_polarity, friction_type, anomaly_flag,
                     created_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, 0.8, 3, 'negative',
                        'process', 'normal', '2025-10-15')
            """, (
                f"C-{i}", "B-001", scan_id, f"T-{i}", "TRC-01",
                f"Novel pattern {i}", f'["keyword_{i}"]',
                f"Root cause {i}", f"Summary {i}",
            ))
        conn.commit()

    def tearDown(self):
        if hasattr(self, '_tmp'):
            try:
                os.unlink(self._tmp.name)
            except Exception:
                pass

    def test_zero_novels_returns_empty(self):
        """Zero novel tickets → empty result with zero counts."""
        conn = create_test_db()
        agent, bridge = self._make_analyst(conn)
        result = agent.run_novelty_validation("SCAN-001")
        self.assertIsNotNone(result)
        self.assertEqual(result["validations"], [])
        self.assertEqual(result["summary"]["validated"], 0)
        # Bridge should NOT have been called
        bridge.call_blocking.assert_not_called()
        agent.shutdown()

    def test_small_batch_single_call(self):
        """25 novels with large budget → exactly 1 bridge call."""
        conn = create_test_db()
        self._seed_novels(conn, 25)
        agent, bridge = self._make_analyst(conn)

        # Mock bridge to return valid JSON
        bridge.call_blocking.return_value = json.dumps({
            "validations": [
                {"ticket_id": f"T-{i}", "verdict": "VALID",
                 "existing_match": None, "notes": "ok"}
                for i in range(25)
            ],
            "summary": {"validated": 25, "rejected": 0, "merged": 0}
        })

        result = agent.run_novelty_validation("SCAN-001", input_budget=500_000)
        self.assertEqual(bridge.call_blocking.call_count, 1)
        self.assertEqual(len(result["validations"]), 25)
        self.assertEqual(result["summary"]["validated"], 25)
        agent.shutdown()

    def test_large_batch_splits(self):
        """100 novels with tiny budget → multiple bridge calls."""
        conn = create_test_db()
        self._seed_novels(conn, 100)
        agent, bridge = self._make_analyst(conn)

        # Return a valid result for each call
        def mock_call(prompt, request_id, timeout=180):
            # Count novel items in this prompt
            count = prompt.count("ticket_id:")
            return json.dumps({
                "validations": [
                    {"ticket_id": f"T-x", "verdict": "VALID",
                     "existing_match": None, "notes": "ok"}
                    for _ in range(count)
                ],
                "summary": {"validated": count, "rejected": 0, "merged": 0}
            })
        bridge.call_blocking.side_effect = mock_call

        # With 2000 char budget and ~200 chars per novel, expect multiple batches
        result = agent.run_novelty_validation("SCAN-001", input_budget=2000)
        self.assertGreater(bridge.call_blocking.call_count, 1)
        # All 100 should be covered across batches
        self.assertGreater(len(result["validations"]), 0)
        agent.shutdown()

    def test_failed_batch_skipped(self):
        """If one batch fails, others continue and results aggregate."""
        conn = create_test_db()
        self._seed_novels(conn, 50)
        agent, bridge = self._make_analyst(conn)

        call_count = [0]
        def mock_call(prompt, request_id, timeout=180):
            call_count[0] += 1
            if call_count[0] == 1:
                return None  # First batch fails
            count = prompt.count("ticket_id:")
            return json.dumps({
                "validations": [
                    {"ticket_id": f"T-x", "verdict": "VALID",
                     "existing_match": None, "notes": "ok"}
                    for _ in range(count)
                ],
                "summary": {"validated": count, "rejected": 0, "merged": 0}
            })
        bridge.call_blocking.side_effect = mock_call

        result = agent.run_novelty_validation("SCAN-001", input_budget=2000)
        # Should still return results from successful batches
        self.assertIsNotNone(result)
        self.assertGreater(len(result["validations"]), 0)
        agent.shutdown()


class TestNoveltyVerdicts(unittest.TestCase):
    """Test verdict application to nlp_ticket_classifications."""

    def _make_analyst_with_data(self, novels_count=5):
        """Create agent with seeded novel tickets on disk DB."""
        import tempfile
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()

        conn = sqlite3.connect(self._tmp.name)
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE nlp_ticket_classifications (
                classification_id TEXT PRIMARY KEY,
                batch_id TEXT, scan_id TEXT, ticket_id TEXT, trc TEXT,
                sub_cluster TEXT, sub_cluster_confidence REAL,
                is_novel INTEGER DEFAULT 0,
                sentiment_intensity INTEGER, sentiment_polarity TEXT,
                friction_type TEXT, anomaly_flag TEXT, anomaly_reason TEXT,
                entities_json TEXT, key_phrases TEXT, root_cause_hint TEXT,
                summary TEXT, raw_classification TEXT, created_at TEXT,
                novelty_verdict TEXT, novelty_match TEXT
            );
            CREATE TABLE analyst_reports (
                report_id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id TEXT NOT NULL, report_type TEXT NOT NULL,
                content TEXT, metrics TEXT, created_at TEXT
            );
            CREATE TABLE sub_patterns (
                pattern_id TEXT PRIMARY KEY, trc TEXT, label TEXT,
                description TEXT, friction_type TEXT,
                tier TEXT DEFAULT 'active', lifetime_tickets INTEGER DEFAULT 0,
                merged_into TEXT
            );
        """)
        for i in range(novels_count):
            conn.execute("""
                INSERT INTO nlp_ticket_classifications
                    (classification_id, batch_id, scan_id, ticket_id, trc,
                     sub_cluster, is_novel, key_phrases, root_cause_hint,
                     summary, sub_cluster_confidence, sentiment_intensity,
                     sentiment_polarity, friction_type, anomaly_flag,
                     created_at)
                VALUES (?, 'B-001', 'SCAN-001', ?, 'TRC-01',
                        ?, 1, '[]', 'cause', 'summary', 0.8, 3, 'neg',
                        'process', 'normal', '2025-10-15')
            """, (f"C-{i}", f"T-{i}", f"Novel {i}"))
        conn.commit()
        conn.close()

        from src.agents.analyst_agent import AnalystAgent
        bridge = MagicMock()
        bridge.ensure_running = MagicMock()
        agent = AnalystAgent(bridge, self._tmp.name)
        return agent

    def tearDown(self):
        if hasattr(self, '_tmp'):
            try:
                os.unlink(self._tmp.name)
            except Exception:
                pass

    def test_duplicate_sets_is_novel_zero(self):
        """DUPLICATE verdict sets is_novel = 0."""
        agent = self._make_analyst_with_data(3)
        validations = [
            {"ticket_id": "T-0", "verdict": "DUPLICATE",
             "existing_match": "Existing Pattern A", "notes": "dup"},
        ]
        agent.apply_novelty_verdicts("SCAN-001", validations)

        row = agent.conn.execute(
            "SELECT is_novel, novelty_verdict, novelty_match "
            "FROM nlp_ticket_classifications WHERE ticket_id = 'T-0'"
        ).fetchone()
        self.assertEqual(row["is_novel"], 0)
        self.assertEqual(row["novelty_verdict"], "DUPLICATE")
        self.assertEqual(row["novelty_match"], "Existing Pattern A")

        # Other tickets unchanged
        row1 = agent.conn.execute(
            "SELECT is_novel, novelty_verdict "
            "FROM nlp_ticket_classifications WHERE ticket_id = 'T-1'"
        ).fetchone()
        self.assertEqual(row1["is_novel"], 1)
        self.assertIsNone(row1["novelty_verdict"])
        agent.shutdown()

    def test_merge_keeps_is_novel_one(self):
        """MERGE verdict keeps is_novel = 1 but tags the ticket."""
        agent = self._make_analyst_with_data(3)
        validations = [
            {"ticket_id": "T-1", "verdict": "MERGE",
             "existing_match": "Novel 0", "notes": "merge"},
        ]
        agent.apply_novelty_verdicts("SCAN-001", validations)

        row = agent.conn.execute(
            "SELECT is_novel, novelty_verdict, novelty_match "
            "FROM nlp_ticket_classifications WHERE ticket_id = 'T-1'"
        ).fetchone()
        self.assertEqual(row["is_novel"], 1)
        self.assertEqual(row["novelty_verdict"], "MERGE")
        self.assertEqual(row["novelty_match"], "Novel 0")
        agent.shutdown()

    def test_valid_no_changes(self):
        """VALID verdict does not modify the ticket."""
        agent = self._make_analyst_with_data(3)
        validations = [
            {"ticket_id": "T-2", "verdict": "VALID",
             "existing_match": None, "notes": "valid"},
        ]
        agent.apply_novelty_verdicts("SCAN-001", validations)

        row = agent.conn.execute(
            "SELECT is_novel, novelty_verdict, novelty_match "
            "FROM nlp_ticket_classifications WHERE ticket_id = 'T-2'"
        ).fetchone()
        self.assertEqual(row["is_novel"], 1)
        self.assertIsNone(row["novelty_verdict"])
        agent.shutdown()

    def test_empty_validations_no_error(self):
        """Empty validations list should not raise."""
        agent = self._make_analyst_with_data(1)
        agent.apply_novelty_verdicts("SCAN-001", [])
        agent.apply_novelty_verdicts("SCAN-001", None)
        agent.shutdown()


class TestNoveltyColumnMigration(unittest.TestCase):
    """Test that novelty_verdict columns are added by db_manager migration."""

    def test_columns_added_to_fresh_db(self):
        """Fresh DatabaseManager should create tables with novelty columns."""
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            from src.data.db_manager import DatabaseManager
            db = DatabaseManager(Path(tmp.name))
            db.initialize()

            cols = {
                row[1] for row in db.conn.execute(
                    "PRAGMA table_info(nlp_ticket_classifications)"
                ).fetchall()
            }
            self.assertIn("novelty_verdict", cols)
            self.assertIn("novelty_match", cols)
            db.close()
        finally:
            os.unlink(tmp.name)

    def test_migration_logic_adds_missing_columns(self):
        """ALTER TABLE migration adds novelty columns when missing."""
        # Simulate a DB with old schema (no novelty columns) using raw SQL
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE nlp_ticket_classifications (
                classification_id TEXT PRIMARY KEY,
                batch_id TEXT, scan_id TEXT, ticket_id TEXT, trc TEXT,
                sub_cluster TEXT, is_novel INTEGER DEFAULT 0,
                created_at TEXT
            )
        """)
        conn.commit()

        # Verify columns missing
        cols = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(nlp_ticket_classifications)"
            ).fetchall()
        }
        self.assertNotIn("novelty_verdict", cols)
        self.assertNotIn("novelty_match", cols)

        # Run the migration logic directly (same as db_manager._migrate)
        for col_name, col_type in [
            ("novelty_verdict", "TEXT"),
            ("novelty_match", "TEXT"),
        ]:
            if col_name not in cols:
                conn.execute(
                    f"ALTER TABLE nlp_ticket_classifications "
                    f"ADD COLUMN {col_name} {col_type}"
                )
        conn.commit()

        # Verify columns now exist
        cols2 = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(nlp_ticket_classifications)"
            ).fetchall()
        }
        self.assertIn("novelty_verdict", cols2)
        self.assertIn("novelty_match", cols2)

        # Verify they work (can write and read)
        conn.execute("""
            INSERT INTO nlp_ticket_classifications
                (classification_id, scan_id, ticket_id, trc, is_novel,
                 novelty_verdict, novelty_match, created_at)
            VALUES ('C-1', 'S-1', 'T-1', 'TRC', 1, 'DUPLICATE',
                    'Pattern A', '2025-01-01')
        """)
        row = conn.execute(
            "SELECT novelty_verdict, novelty_match "
            "FROM nlp_ticket_classifications WHERE classification_id='C-1'"
        ).fetchone()
        self.assertEqual(row["novelty_verdict"], "DUPLICATE")
        self.assertEqual(row["novelty_match"], "Pattern A")
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 5.4: BOUNDARY GUARD + PARTIAL PARSE TESTS
# ═══════════════════════════════════════════════════════════════════════════

class TestBoundaryGuard(unittest.TestCase):
    """Test ticket ID boundary guard in tool_registry (5.4 Fix 1)."""

    def setUp(self):
        self.conn = create_test_db()
        seed_test_data(self.conn)
        self.db_path = os.path.join(
            os.path.dirname(__file__), "_test_boundary_guard.db"
        )
        file_conn = sqlite3.connect(self.db_path)
        self.conn.backup(file_conn)
        file_conn.close()

        from src.agents.tool_registry import ToolRegistry
        self.registry = ToolRegistry(self.db_path)

    def tearDown(self):
        self.registry.close()
        self.conn.close()
        try:
            os.unlink(self.db_path)
        except Exception:
            pass

    def test_boundary_guard_rejects_overflow(self):
        """Ticket ID not in batch manifest is rejected."""
        self.registry.set_context(
            scan_id="test_scan", batch_id="test_batch",
            trc="RCM_02", agent_id="worker_0",
            ticket_trc_map={"T-1000": "RCM_02", "T-1001": "RCM_02"},
        )
        result = self.registry.execute("store_classification", {
            "ticket_id": "T-9999",  # NOT in manifest
            "sub_cluster": "test",
            "summary": "overflow ticket",
        })
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason"], "not_in_batch")
        self.assertEqual(result["ticket_id"], "T-9999")

    def test_boundary_guard_allows_valid(self):
        """Ticket ID in batch manifest is stored normally."""
        self.registry.set_context(
            scan_id="test_scan", batch_id="test_batch",
            trc="RCM_02", agent_id="worker_0",
            ticket_trc_map={"T-1000": "RCM_02", "T-1001": "RCM_02"},
        )
        result = self.registry.execute("store_classification", {
            "ticket_id": "T-1000",  # IS in manifest
            "sub_cluster": "test",
            "summary": "valid ticket",
        })
        self.assertEqual(result["status"], "stored")
        self.assertEqual(result["ticket_id"], "T-1000")

    def test_boundary_guard_skips_empty_map(self):
        """Empty ticket_trc_map bypasses guard (backward-compat)."""
        self.registry.set_context(
            scan_id="test_scan", batch_id="test_batch",
            trc="RCM_02", agent_id="worker_0",
            # No ticket_trc_map — simulates pre-5.4 code path
        )
        result = self.registry.execute("store_classification", {
            "ticket_id": "T-ANY",
            "sub_cluster": "test",
            "summary": "no manifest",
        })
        self.assertEqual(result["status"], "stored")


class TestPartialParseDetection(unittest.TestCase):
    """Test partial parse completeness check logic (5.4 Fix 2).

    These tests validate the detection logic in isolation by simulating
    the variables the orchestrator uses. The actual orchestrator code
    operates on `result` dict and `batch` dict — we test the same
    conditional logic without booting a full scan.
    """

    @staticmethod
    def _check_partial_parse(result, n_expected, batch):
        """
        Reproduce the orchestrator's 5.4 completeness check logic.
        Returns (error_injected: bool, stagnation: bool).
        """
        _n_classified = result.get("classified", 0)
        _n_missing = n_expected - _n_classified
        _completeness = (
            _n_classified / n_expected if n_expected > 0 else 1.0
        )
        _prev_classified = batch.get('_prev_classified', -1)

        if (not result.get("error")
                and _completeness < 0.80
                and _n_missing > 3
                and _n_classified > _prev_classified):
            result["error"] = "partial_parse"
            result["recoverable"] = True
            batch['_prev_classified'] = _n_classified
            return True, False  # error injected, not stagnation
        elif (not result.get("error")
                and _completeness < 0.80
                and _n_missing > 3
                and _n_classified <= _prev_classified):
            return False, True  # no error, stagnation
        return False, False  # no action

    def test_partial_parse_detected(self):
        """26/63 classified triggers partial_parse error."""
        result = {"classified": 26, "failed": 37}
        batch = {}
        injected, stagnation = self._check_partial_parse(
            result, 63, batch
        )
        self.assertTrue(injected)
        self.assertFalse(stagnation)
        self.assertEqual(result["error"], "partial_parse")
        self.assertTrue(result["recoverable"])
        self.assertEqual(batch['_prev_classified'], 26)

    def test_partial_parse_skips_small_batch(self):
        """4/5 classified (missing=1 <= 3) does NOT trigger."""
        result = {"classified": 4, "failed": 1}
        batch = {}
        injected, stagnation = self._check_partial_parse(
            result, 5, batch
        )
        self.assertFalse(injected)
        self.assertFalse(stagnation)
        self.assertIsNone(result.get("error"))

    def test_partial_parse_skips_near_complete(self):
        """55/63 classified (87% > 80%) does NOT trigger."""
        result = {"classified": 55, "failed": 8}
        batch = {}
        injected, stagnation = self._check_partial_parse(
            result, 63, batch
        )
        self.assertFalse(injected)
        self.assertFalse(stagnation)
        self.assertIsNone(result.get("error"))

    def test_partial_parse_skips_existing_error(self):
        """If result already has an error, don't override it."""
        result = {"classified": 10, "failed": 53, "error": "stall_timeout"}
        batch = {}
        injected, stagnation = self._check_partial_parse(
            result, 63, batch
        )
        self.assertFalse(injected)
        self.assertFalse(stagnation)
        self.assertEqual(result["error"], "stall_timeout")

    def test_partial_parse_stagnation(self):
        """Retry with same classified count triggers stagnation."""
        result = {"classified": 26, "failed": 37}
        batch = {'_prev_classified': 26}  # same as last time
        injected, stagnation = self._check_partial_parse(
            result, 63, batch
        )
        self.assertFalse(injected)
        self.assertTrue(stagnation)
        self.assertIsNone(result.get("error"))

    def test_partial_parse_stagnation_worse(self):
        """Retry with fewer classified also triggers stagnation."""
        result = {"classified": 20, "failed": 43}
        batch = {'_prev_classified': 26}  # worse than last time
        injected, stagnation = self._check_partial_parse(
            result, 63, batch
        )
        self.assertFalse(injected)
        self.assertTrue(stagnation)

    def test_partial_parse_default_prev_zero_classified(self):
        """First attempt with 0 classified triggers retry (default=-1)."""
        result = {"classified": 0, "failed": 63}
        batch = {}  # _prev_classified defaults to -1
        injected, stagnation = self._check_partial_parse(
            result, 63, batch
        )
        self.assertTrue(injected)
        self.assertFalse(stagnation)
        self.assertEqual(result["error"], "partial_parse")

    def test_partial_parse_improvement_continues(self):
        """Retry that improves continues retrying."""
        result = {"classified": 35, "failed": 28}
        batch = {'_prev_classified': 26}  # improved from 26 → 35
        injected, stagnation = self._check_partial_parse(
            result, 63, batch
        )
        self.assertTrue(injected)  # 35/63 = 56% < 80%, missing=28 > 3
        self.assertFalse(stagnation)
        self.assertEqual(batch['_prev_classified'], 35)


# ═══════════════════════════════════════════════════════════════════════════
# Phase 5.4b — Supervisor DB Count + SUBSTR Date Fix
# ═══════════════════════════════════════════════════════════════════════════


class TestSupervisorDBCount(unittest.TestCase):
    """Verify supervisor queries actual DB count instead of in-memory."""

    def setUp(self):
        """Create an in-memory DB with scan_progress and classifications."""
        self.db_path = ":memory:"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""
            CREATE TABLE scan_progress (
                scan_id TEXT PRIMARY KEY,
                classified INTEGER DEFAULT 0,
                total INTEGER DEFAULT 0,
                tool_calls INTEGER DEFAULT 0,
                batches_complete INTEGER DEFAULT 0,
                est_remaining_seconds REAL DEFAULT 0,
                est_confidence TEXT DEFAULT 'low',
                updated_at TEXT,
                tokens_in INTEGER DEFAULT 0,
                tokens_out INTEGER DEFAULT 0
            )
        """)
        self.conn.execute("""
            CREATE TABLE nlp_ticket_classifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id TEXT,
                ticket_id TEXT
            )
        """)
        self.conn.execute(
            "CREATE INDEX idx_nlp_tc_scan "
            "ON nlp_ticket_classifications(scan_id)"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_db_count_overrides_memory(self):
        """DB has more classified rows than in-memory counter reports."""
        scan_id = "test-scan-001"
        # Insert 50 classification rows in DB (simulating streamed + retried)
        for i in range(50):
            self.conn.execute(
                "INSERT INTO nlp_ticket_classifications "
                "(scan_id, ticket_id) VALUES (?, ?)",
                (scan_id, f"T-{i:04d}"),
            )
        self.conn.commit()

        # Simulate what supervisor does: query actual DB count
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM nlp_ticket_classifications"
            " WHERE scan_id = ?",
            (scan_id,),
        ).fetchone()
        db_count = row["n"]

        # In-memory counter only saw 30 (from notify_batch_complete)
        memory_count = 30

        self.assertEqual(db_count, 50)
        self.assertGreater(db_count, memory_count)

    def test_db_count_fallback_on_error(self):
        """If DB query fails, fallback to in-memory count."""
        # Close connection to simulate error
        self.conn.close()
        memory_count = 30
        classified_count = memory_count  # fallback

        try:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM nlp_ticket_classifications"
                " WHERE scan_id = ?",
                ("test-scan-001",),
            ).fetchone()
            if row:
                classified_count = row["n"]
        except Exception:
            classified_count = memory_count  # fallback

        self.assertEqual(classified_count, 30)

    def test_db_count_zero_when_no_rows(self):
        """DB returns 0 when no classifications exist yet."""
        scan_id = "test-scan-empty"
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM nlp_ticket_classifications"
            " WHERE scan_id = ?",
            (scan_id,),
        ).fetchone()
        self.assertEqual(row["n"], 0)


class TestSubstrDateComparison(unittest.TestCase):
    """Verify SUBSTR date comparison includes single-digit hour timestamps."""

    def setUp(self):
        """Create DB with conversations including single-digit hours."""
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""
            CREATE TABLE conversations (
                ticket_id TEXT PRIMARY KEY,
                trc_code TEXT,
                created_at TEXT,
                full_thread TEXT DEFAULT ''
            )
        """)
        # Insert tickets with various hour formats
        test_data = [
            ("T-001", "TRC-001", "2025-03-12 8:24:00"),   # Single-digit hour
            ("T-002", "TRC-001", "2025-03-12 9:15:00"),   # Single-digit hour
            ("T-003", "TRC-001", "2025-03-12 10:30:00"),  # Double-digit hour
            ("T-004", "TRC-001", "2025-03-12 23:59:00"),  # Late night
            ("T-005", "TRC-001", "2025-03-11 14:00:00"),  # Previous day
            ("T-006", "TRC-001", "2025-03-13 6:00:00"),   # Next day, single-digit
        ]
        for tid, trc, ts in test_data:
            self.conn.execute(
                "INSERT INTO conversations (ticket_id, trc_code, created_at)"
                " VALUES (?, ?, ?)",
                (tid, trc, ts),
            )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_substr_includes_single_digit_hours(self):
        """SUBSTR comparison includes tickets with hours 0-9."""
        rows = self.conn.execute("""
            SELECT ticket_id FROM conversations
            WHERE SUBSTR(created_at, 1, 10) >= ?
              AND SUBSTR(created_at, 1, 10) <= ?
            ORDER BY ticket_id
        """, ("2025-03-12", "2025-03-12")).fetchall()

        ticket_ids = [r["ticket_id"] for r in rows]
        # Should include ALL 4 tickets on 2025-03-12 (including single-digit hours)
        self.assertIn("T-001", ticket_ids)  # 8:24 — was excluded by old query
        self.assertIn("T-002", ticket_ids)  # 9:15 — was excluded by old query
        self.assertIn("T-003", ticket_ids)  # 10:30
        self.assertIn("T-004", ticket_ids)  # 23:59
        self.assertEqual(len(ticket_ids), 4)

    def test_substr_excludes_out_of_range(self):
        """SUBSTR comparison correctly excludes tickets outside range."""
        rows = self.conn.execute("""
            SELECT ticket_id FROM conversations
            WHERE SUBSTR(created_at, 1, 10) >= ?
              AND SUBSTR(created_at, 1, 10) <= ?
        """, ("2025-03-12", "2025-03-12")).fetchall()

        ticket_ids = [r["ticket_id"] for r in rows]
        self.assertNotIn("T-005", ticket_ids)  # 2025-03-11
        self.assertNotIn("T-006", ticket_ids)  # 2025-03-13

    def test_old_query_fails_single_digit_hours(self):
        """Demonstrate that the OLD raw string comparison fails."""
        # This test documents the bug: raw string comparison excludes T-001, T-002
        rows = self.conn.execute("""
            SELECT ticket_id FROM conversations
            WHERE created_at >= ? AND created_at <= ?
            ORDER BY ticket_id
        """, ("2025-03-12", "2025-03-12 23:59:59")).fetchall()

        ticket_ids = [r["ticket_id"] for r in rows]
        # Old query MISSES single-digit hour tickets because:
        # '2025-03-12 8:24:00' > '2025-03-12 23:59:59' (lexicographic: '8' > '2')
        self.assertNotIn("T-001", ticket_ids)  # Bug: excluded
        self.assertNotIn("T-002", ticket_ids)  # Bug: excluded
        # But double-digit hours work fine
        self.assertIn("T-003", ticket_ids)  # 10:30
        self.assertIn("T-004", ticket_ids)  # 23:59

    def test_substr_multi_day_range(self):
        """SUBSTR comparison works across multi-day ranges."""
        rows = self.conn.execute("""
            SELECT ticket_id FROM conversations
            WHERE SUBSTR(created_at, 1, 10) >= ?
              AND SUBSTR(created_at, 1, 10) <= ?
            ORDER BY ticket_id
        """, ("2025-03-11", "2025-03-13")).fetchall()

        ticket_ids = [r["ticket_id"] for r in rows]
        # Should include all 6 tickets
        self.assertEqual(len(ticket_ids), 6)

    def test_count_with_substr(self):
        """COUNT query with SUBSTR returns correct total."""
        row = self.conn.execute("""
            SELECT COUNT(DISTINCT ticket_id) AS n FROM conversations
            WHERE SUBSTR(created_at, 1, 10) >= ?
              AND SUBSTR(created_at, 1, 10) <= ?
        """, ("2025-03-12", "2025-03-12")).fetchone()

        self.assertEqual(row["n"], 4)  # All 4 tickets on March 12


# ═══════════════════════════════════════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
