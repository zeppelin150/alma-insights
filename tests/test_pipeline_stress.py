"""
Alma Insights -- Pipeline Stress Tests (Pass 5.0)

Tests for concurrency, resilience, and edge cases.
No Gemini API calls required -- all mocked.

Run: python -m pytest tests/test_pipeline_stress.py -v
"""

import os
import sqlite3
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from tests.test_pipeline_full import create_test_db, seed_test_data


# ═══════════════════════════════════════════════════════════════════════════
# RATE GOVERNOR CONCURRENCY
# ═══════════════════════════════════════════════════════════════════════════

class TestRateGovernorConcurrent(unittest.TestCase):
    """Test rate governor under concurrent access."""

    def test_3_threads_no_collisions(self):
        """3 threads competing for acquire() -- no double-grants."""
        from src.agents.rate_governor import RateGovernor
        gov = RateGovernor(min_interval=0.05)

        acquired_times = []
        lock = threading.Lock()

        def worker():
            for _ in range(3):
                if gov.acquire(timeout=10):
                    with lock:
                        acquired_times.append(time.time())
                    gov.report_success(0.01)

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        # Check that no two acquires happened within min_interval
        acquired_times.sort()
        for i in range(1, len(acquired_times)):
            gap = acquired_times[i] - acquired_times[i-1]
            # Allow some tolerance (0.03s) for timing jitter
            self.assertGreaterEqual(
                gap, 0.02,
                f"Gap {gap:.4f}s too small between acquires "
                f"{i-1} and {i}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# BATCH PACKER EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════

class TestBatchPackerEdge(unittest.TestCase):
    """Test batch packer edge cases."""

    def test_zero_ticket_count(self):
        """compute_batch_size with 0 tickets returns 0."""
        conn = create_test_db()
        from src.agents.batch_packer import BatchPacker
        packer = BatchPacker(conn)
        size = packer.compute_batch_size("TRC", 0)
        self.assertEqual(size, 0)
        conn.close()

    def test_record_zero_tickets(self):
        """record_result with 0 tickets should not crash."""
        conn = create_test_db()
        from src.agents.batch_packer import BatchPacker
        packer = BatchPacker(conn)
        packer.record_result("TRC", 0, 1000)  # should be a no-op
        conn.close()

    def test_extreme_output_size(self):
        """Very large output per ticket clamps to MIN_BATCH_SIZE."""
        conn = create_test_db()
        from src.agents.batch_packer import BatchPacker, MIN_BATCH_SIZE
        packer = BatchPacker(conn)
        # Record extreme output: 10K chars per ticket
        packer.record_result("BIG", 10, 100000)
        size = packer.compute_batch_size("BIG", 1000)
        self.assertGreaterEqual(size, MIN_BATCH_SIZE)
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# STREAM PARSER EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════

class TestStreamParserEdge(unittest.TestCase):
    """Test stream parser edge cases."""

    def test_empty_fence(self):
        """Empty fenced block should not crash."""
        from src.agents.stream_parser import StreamParser
        parser = StreamParser()
        events = list(parser.feed('```classification\n\n```'))
        # Should handle gracefully (no events or text event)

    def test_nested_backticks(self):
        """Backticks inside JSON should be handled."""
        from src.agents.stream_parser import StreamParser, StreamEvent
        parser = StreamParser()
        # The JSON itself doesn't contain backticks, but text around it might
        text = 'Some `code` here\n```classification\n{"ticket_id": "T-1"}\n```'
        events = list(parser.feed(text))
        events.extend(parser.flush())
        cls_events = [e for e in events if e.event_type == StreamEvent.CLASSIFICATION]
        self.assertEqual(len(cls_events), 1)

    def test_very_large_chunk(self):
        """Large text chunks should work."""
        from src.agents.stream_parser import StreamParser, StreamEvent
        parser = StreamParser()
        large_text = "A" * 50000
        events = list(parser.feed(large_text))
        events.extend(parser.flush())
        text_events = [e for e in events if e.event_type == StreamEvent.TEXT]
        total_chars = sum(len(e.raw) for e in text_events)
        self.assertEqual(total_chars, 50000)

    def test_unclosed_fence(self):
        """Unclosed fence at end of stream should flush as text."""
        from src.agents.stream_parser import StreamParser, StreamEvent
        parser = StreamParser()
        list(parser.feed('```classification\n{"ticket_id": "T-1"}'))
        # No closing ``` -- flush should handle it
        events = list(parser.flush())
        text_events = [e for e in events if e.event_type == StreamEvent.TEXT]
        self.assertGreater(len(text_events), 0)

    def test_unknown_fence_type(self):
        """Unknown fence type emitted as text."""
        from src.agents.stream_parser import StreamParser, StreamEvent
        parser = StreamParser()
        text = '```unknown_type\nsome content\n```'
        events = list(parser.feed(text))
        text_events = [e for e in events if e.event_type == StreamEvent.TEXT]
        self.assertGreater(len(text_events), 0)


# ═══════════════════════════════════════════════════════════════════════════
# TOOL REGISTRY EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════

class TestToolRegistryEdge(unittest.TestCase):
    """Test tool registry edge cases."""

    def setUp(self):
        self.conn = create_test_db()
        seed_test_data(self.conn)
        self.db_path = os.path.join(
            os.path.dirname(__file__), "_test_stress_registry.db"
        )
        file_conn = sqlite3.connect(self.db_path)
        self.conn.backup(file_conn)
        file_conn.close()

        from src.agents.tool_registry import ToolRegistry
        self.registry = ToolRegistry(self.db_path)
        self.registry.set_context(
            scan_id="stress_scan", batch_id="stress_batch",
            trc="RCM_02", agent_id="worker_0"
        )

    def tearDown(self):
        self.registry.close()
        self.conn.close()
        try:
            os.unlink(self.db_path)
        except Exception:
            pass

    def test_store_classification_missing_ticket_id(self):
        """store_classification with missing ticket_id returns error."""
        result = self.registry.execute("store_classification", {
            "sub_cluster": "test",
        })
        self.assertIn("error", result)

    def test_flag_missing_reason(self):
        """flag_for_review with missing reason returns error."""
        result = self.registry.execute("flag_for_review", {
            "ticket_id": "T-1",
        })
        self.assertIn("error", result)

    def test_get_thread_nonexistent(self):
        """get_full_thread for nonexistent ticket returns empty."""
        result = self.registry.execute("get_full_thread", {
            "ticket_id": "NONEXISTENT",
        })
        self.assertEqual(result["message_count"], 0)

    def test_mixed_trc_taxonomy(self):
        """query_taxonomy with comma-separated TRCs."""
        result = self.registry.execute("query_taxonomy", {
            "trc": "RCM_02,RCM_03",
        })
        # Should not crash, returns patterns_by_trc
        self.assertIn("trcs", result)

    def test_report_progress_no_scan_id(self):
        """report_progress with no scan_id in context returns error."""
        from src.agents.tool_registry import ToolRegistry
        reg = ToolRegistry(self.db_path)
        # No context set
        result = reg.execute("report_progress", {
            "classified": 10, "total": 50
        })
        self.assertIn("error", result)
        reg.close()


# ═══════════════════════════════════════════════════════════════════════════
# SUPERVISOR EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════

class TestSupervisorEdge(unittest.TestCase):
    """Test supervisor edge cases."""

    def test_stall_detection(self):
        """Worker stalled for > STALL_TIMEOUT triggers restart."""
        from src.agents.supervisor import Supervisor
        from src.agents.rate_governor import RateGovernor

        mock_worker = MagicMock()
        mock_worker.agent_id = "stall_worker"
        health = {
            "agent_id": "stall_worker",
            "status": "active",
            "batches_done": 1,
            "tickets_done": 10,
            "tool_calls": 5,
            "parse_rate": 0.95,
            "avg_confidence": 0.80,
            "context_tokens": 50000,
            "last_progress": 400,  # > 300s STALL_TIMEOUT
        }

        sup = Supervisor([mock_worker], RateGovernor(), ":memory:", "test")
        sup._check_health(mock_worker, health)
        mock_worker.reset.assert_called_once()

    def test_context_overflow(self):
        """Context > 800K tokens triggers reset."""
        from src.agents.supervisor import Supervisor
        from src.agents.rate_governor import RateGovernor

        mock_worker = MagicMock()
        mock_worker.agent_id = "overflow_worker"
        health = {
            "agent_id": "overflow_worker",
            "status": "active",
            "batches_done": 10,
            "tickets_done": 500,
            "tool_calls": 200,
            "parse_rate": 0.95,
            "avg_confidence": 0.80,
            "context_tokens": 900000,  # > 800K
            "last_progress": 5,
        }

        sup = Supervisor([mock_worker], RateGovernor(), ":memory:", "test")
        sup._check_health(mock_worker, health)
        mock_worker.reset.assert_called_once()

    def test_idle_worker_skipped(self):
        """Idle workers should not be checked."""
        from src.agents.supervisor import Supervisor
        from src.agents.rate_governor import RateGovernor

        mock_worker = MagicMock()
        mock_worker.agent_id = "idle_worker"
        health = {
            "agent_id": "idle_worker",
            "status": "idle",
            "batches_done": 0,
            "parse_rate": 0.0,  # would trigger restart if active
        }

        sup = Supervisor([mock_worker], RateGovernor(), ":memory:", "test")
        sup._check_health(mock_worker, health)
        mock_worker.reset.assert_not_called()

    def test_notify_batch_complete(self):
        """notify_batch_complete updates counters."""
        from src.agents.supervisor import Supervisor
        from src.agents.rate_governor import RateGovernor

        sup = Supervisor([], RateGovernor(), ":memory:", "test")
        sup._total_batches = 5
        sup.notify_batch_complete("w0", {"classified": 50})
        sup.notify_batch_complete("w1", {"classified": 30, "error": "timeout"})

        self.assertEqual(sup._completed_batches, 2)
        self.assertEqual(sup._classified_tickets, 80)
        self.assertEqual(len(sup._errors), 1)


# ═══════════════════════════════════════════════════════════════════════════
# WORKER AGENT EDGE CASES (mock bridge)
# ═══════════════════════════════════════════════════════════════════════════

class TestWorkerAgentEdge(unittest.TestCase):
    """Test worker agent edge cases with mock bridge."""

    def test_needs_reset_threshold(self):
        """needs_reset() returns True above 800K tokens."""
        from src.agents.worker_agent import WorkerAgent
        mock_bridge = MagicMock()
        worker = WorkerAgent.__new__(WorkerAgent)
        worker.context_tokens_estimate = 900000
        self.assertTrue(worker.needs_reset())
        worker.context_tokens_estimate = 500000
        self.assertFalse(worker.needs_reset())

    def test_get_health_returns_dict(self):
        """get_health() returns all required fields."""
        from src.agents.worker_agent import WorkerAgent
        worker = WorkerAgent.__new__(WorkerAgent)
        worker.agent_id = "test_worker"
        worker.status = "idle"
        worker.batches_processed = 5
        worker.tickets_classified = 100
        worker.tools_called = 50
        worker.parse_rate = 0.95
        worker.avg_confidence = 0.75
        worker.context_tokens_estimate = 50000
        worker._last_progress_time = time.time()

        health = worker.get_health()
        self.assertEqual(health["agent_id"], "test_worker")
        self.assertEqual(health["batches_done"], 5)
        self.assertIn("parse_rate", health)
        self.assertIn("context_tokens", health)


# ═══════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════

class TestOrchestratorEdge(unittest.TestCase):
    """Test scan orchestrator edge cases."""

    def test_history_empty(self):
        """get_history() on empty DB returns empty list."""
        db_path = os.path.join(
            os.path.dirname(__file__), "_test_orch_edge.db"
        )
        conn = create_test_db()
        file_conn = sqlite3.connect(db_path)
        conn.backup(file_conn)
        file_conn.close()
        conn.close()

        from src.agents.scan_orchestrator import ScanOrchestrator
        orch = ScanOrchestrator(db_path=db_path, num_workers=1)
        history = orch.get_history()
        self.assertIsInstance(history, list)
        self.assertEqual(len(history), 0)

        try:
            os.unlink(db_path)
        except Exception:
            pass

    def test_cancel_unknown_scan(self):
        """cancel_scan on unknown scan doesn't crash."""
        db_path = os.path.join(
            os.path.dirname(__file__), "_test_orch_cancel.db"
        )
        conn = create_test_db()
        file_conn = sqlite3.connect(db_path)
        conn.backup(file_conn)
        file_conn.close()
        conn.close()

        from src.agents.scan_orchestrator import ScanOrchestrator
        orch = ScanOrchestrator(db_path=db_path, num_workers=1)
        result = orch.cancel_scan("nonexistent_scan_id")
        self.assertEqual(result["status"], "cancelled")

        try:
            os.unlink(db_path)
        except Exception:
            pass

    def test_get_status_no_scan(self):
        """get_status with no scan returns error."""
        db_path = os.path.join(
            os.path.dirname(__file__), "_test_orch_status.db"
        )
        conn = create_test_db()
        file_conn = sqlite3.connect(db_path)
        conn.backup(file_conn)
        file_conn.close()
        conn.close()

        from src.agents.scan_orchestrator import ScanOrchestrator
        orch = ScanOrchestrator(db_path=db_path, num_workers=1)
        result = orch.get_status()
        self.assertIn("error", result)

        try:
            os.unlink(db_path)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
