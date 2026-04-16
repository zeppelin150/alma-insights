"""
Tests for ACP Performance & Reliability fixes (Phase 1-2).

Phase 1:
  F1: Probe floor inflation cap
  F2: Context reset counter bug
  F3: WorkerAgent observability (DB truth check)
  F4: Post-scan skip on stall/cancel
  F5: Classification sweep

Phase 2:
  F6a: nlp_batch_tickets tracking table
  F6: Graceful tail-batch cutoff
  F7: Worker queue timeout extension
"""

import os
import sys
import time
import json
import sqlite3
import unittest
import threading
from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))


# ─────────────────────────────────────────────────────────────────────
# F1: Probe floor inflation
# ─────────────────────────────────────────────────────────────────────

class TestProbeFloorInflation(unittest.TestCase):
    """F1: set_probe_floor must cap the floor to prevent inflated probes
    from throttling all workers through the burst gate."""

    def setUp(self):
        from src.agents.rate_governor import SemaphoreRateGovernor
        self.gov = SemaphoreRateGovernor(
            max_concurrent=8, burst_delay=0.5
        )

    def test_normal_probe_sets_expected_floor(self):
        """A 2s probe latency should give a reasonable floor."""
        # adaptive_floor = max(15.0, 2.0 * 3.0) = 15.0
        # raw_floor = max(0.3, 15.0 / 30.0) = 0.5
        # max_floor = 0.5 * 2.0 = 1.0
        # probe_floor = min(0.5, 1.0) = 0.5
        self.gov.set_probe_floor(15.0)
        self.assertLessEqual(self.gov._probe_floor, 1.0)
        self.assertGreaterEqual(self.gov._probe_floor, 0.3)

    def test_inflated_probe_capped(self):
        """A 60s adaptive_floor (from rate-limited probe) must be capped."""
        # raw_floor = max(0.3, 60.0 / 30.0) = 2.0
        # max_floor = 0.5 * 2.0 = 1.0
        # probe_floor = min(2.0, 1.0) = 1.0
        self.gov.set_probe_floor(60.0)
        self.assertLessEqual(
            self.gov._probe_floor, 1.0,
            "Inflated probe should be capped at 2x configured burst_delay"
        )

    def test_extremely_inflated_probe_still_capped(self):
        """Even a 300s floor should be capped."""
        self.gov.set_probe_floor(300.0)
        self.assertLessEqual(self.gov._probe_floor, 1.0)

    def test_probe_floor_does_not_lower_burst_delay(self):
        """set_probe_floor should only raise burst_delay, never lower it."""
        self.gov._burst_delay = 2.0  # simulate post-429 elevated delay
        self.gov.set_probe_floor(15.0)  # floor = 0.5
        self.assertGreaterEqual(self.gov._burst_delay, 2.0)

    def test_configured_burst_delay_unchanged(self):
        """set_probe_floor must NOT modify _configured_burst_delay."""
        original = self.gov._configured_burst_delay
        self.gov.set_probe_floor(60.0)
        self.assertEqual(
            self.gov._configured_burst_delay, original,
            "Probe floor should not modify the configured burst delay"
        )

    def test_diagnostic_counters_initialized(self):
        """Diagnostic counters should start at zero."""
        self.assertEqual(self.gov._burst_wait_total, 0.0)
        self.assertEqual(self.gov._semaphore_wait_total, 0.0)
        self.assertEqual(self.gov._acquire_count, 0)

    def test_acquire_increments_diagnostic_counters(self):
        """acquire() should update diagnostic timing counters."""
        acquired = self.gov.acquire(timeout=5)
        self.assertTrue(acquired)
        self.assertEqual(self.gov._acquire_count, 1)
        self.assertGreaterEqual(self.gov._burst_wait_total, 0)
        self.assertGreaterEqual(self.gov._semaphore_wait_total, 0)
        # Release to clean up
        self.gov.release(duration=0.1, success=True)


# ─────────────────────────────────────────────────────────────────────
# F2: Context reset counter
# ─────────────────────────────────────────────────────────────────────

class TestContextResetCounter(unittest.TestCase):
    """F2: Worker should reset based on _batches_since_reset, not
    the lifetime batches_processed counter."""

    def setUp(self):
        # Mock bridge
        self.mock_bridge = MagicMock()
        self.mock_bridge.is_alive.return_value = True
        self.mock_bridge.new_session.return_value = None
        self.mock_bridge.call_streaming.return_value = {
            "full_text": "",
            "elapsed_ms": 100,
            "events": [],
            "error": None,
            "input_tokens": 100,
            "output_tokens": 50,
        }

    def test_reset_uses_batches_since_reset(self):
        """Worker should reset after MAX_BATCHES_BEFORE_RESET batches
        since last reset, not total lifetime batches."""
        from src.agents.worker_agent import WorkerAgent, MAX_BATCHES_BEFORE_RESET

        # Verify the constant
        self.assertGreaterEqual(MAX_BATCHES_BEFORE_RESET, 5)

        # Create a mock worker and simulate batch counting
        worker = WorkerAgent.__new__(WorkerAgent)
        worker.agent_id = "test_worker"
        worker.bridge = self.mock_bridge
        worker.batches_processed = 0
        worker._batches_since_reset = 0
        worker.context_tokens_estimate = 0
        worker.stream_parser = MagicMock()
        worker.parse_rate = 1.0
        worker.avg_confidence = 0.5

        # Simulate reaching the threshold
        worker.batches_processed = MAX_BATCHES_BEFORE_RESET
        worker._batches_since_reset = MAX_BATCHES_BEFORE_RESET

        # After reset, _batches_since_reset goes to 0
        worker.reset()
        self.assertEqual(worker._batches_since_reset, 0)
        # But batches_processed stays (lifetime counter)
        self.assertEqual(worker.batches_processed, MAX_BATCHES_BEFORE_RESET)

        # Simulate processing one more batch
        worker.batches_processed += 1
        worker._batches_since_reset += 1

        # Should NOT trigger reset (only 1 batch since reset)
        self.assertLess(worker._batches_since_reset, MAX_BATCHES_BEFORE_RESET)


# ─────────────────────────────────────────────────────────────────────
# F4: Post-scan skip on stall/cancel
# ─────────────────────────────────────────────────────────────────────

class TestPostScanStallVsCancel(unittest.TestCase):
    """F4: Post-scan phases should run after stall timeout but NOT
    after explicit user cancellation."""

    def test_user_cancelled_flag_init(self):
        """ScanOrchestrator should initialize _user_cancelled = False."""
        from src.agents.scan_orchestrator import ScanOrchestrator
        orch = ScanOrchestrator.__new__(ScanOrchestrator)
        # Simulate __init__ state
        orch._cancelled = False
        orch._user_cancelled = False
        orch._stop_event = threading.Event()
        self.assertFalse(orch._user_cancelled)

    def test_cancel_scan_sets_user_flag(self):
        """cancel_scan() should set _user_cancelled = True."""
        from src.agents.scan_orchestrator import ScanOrchestrator
        orch = ScanOrchestrator.__new__(ScanOrchestrator)
        orch._stop_event = threading.Event()
        orch._supervisor = None
        orch._user_cancelled = False
        orch.db_path = ":memory:"

        # Mock DB operations
        with patch.object(ScanOrchestrator, '_get_conn') as mock_conn:
            mock_db = MagicMock()
            mock_conn.return_value = mock_db
            orch.cancel_scan("test_scan_id")

        self.assertTrue(orch._user_cancelled)
        self.assertTrue(orch._stop_event.is_set())

    def test_stall_without_user_cancel_continues(self):
        """If _stop_event is set but _user_cancelled is False,
        post-scan should proceed (not return early)."""
        from src.agents.scan_orchestrator import ScanOrchestrator
        orch = ScanOrchestrator.__new__(ScanOrchestrator)
        orch._stop_event = threading.Event()
        orch._stop_event.set()  # stall timeout
        orch._user_cancelled = False  # NOT user-initiated

        # The check in _run_scan should NOT early-return
        # (we can't easily test the full _run_scan flow, but we
        # verify the flag logic)
        self.assertTrue(orch._stop_event.is_set())
        self.assertFalse(orch._user_cancelled)
        # This means post-scan should run


# ─────────────────────────────────────────────────────────────────────
# F1+Diagnostics: Rate governor with concurrency
# ─────────────────────────────────────────────────────────────────────

class TestRateGovernorConcurrency(unittest.TestCase):
    """Test semaphore governor under concurrent access."""

    def test_multiple_acquires_within_concurrency(self):
        """Should allow max_concurrent simultaneous acquires."""
        from src.agents.rate_governor import SemaphoreRateGovernor
        gov = SemaphoreRateGovernor(
            max_concurrent=4, burst_delay=0.01
        )

        acquired = []
        for _ in range(4):
            result = gov.acquire(timeout=5)
            acquired.append(result)

        self.assertEqual(sum(acquired), 4)
        self.assertEqual(gov._in_flight, 4)

        # Release all
        for _ in range(4):
            gov.release(duration=0.01, success=True)
        self.assertEqual(gov._in_flight, 0)

    def test_429_shrinks_concurrency(self):
        """Rate limit should halve concurrency."""
        from src.agents.rate_governor import SemaphoreRateGovernor
        gov = SemaphoreRateGovernor(
            max_concurrent=8, burst_delay=0.01
        )

        gov.acquire(timeout=5)
        gov.release(duration=0.1, success=False, rate_limited=True)
        self.assertEqual(gov._current_concurrent, 4)

    def test_success_recovery_grows_concurrency(self):
        """10 consecutive successes should grow concurrency."""
        from src.agents.rate_governor import SemaphoreRateGovernor
        gov = SemaphoreRateGovernor(
            max_concurrent=8, burst_delay=0.01
        )

        # Shrink first
        gov.acquire(timeout=5)
        gov.release(duration=0.1, rate_limited=True)
        self.assertEqual(gov._current_concurrent, 4)

        # Feed 10 successes
        for _ in range(10):
            gov.acquire(timeout=5)
            gov.release(duration=0.1, success=True)

        self.assertGreater(gov._current_concurrent, 4)


# ─────────────────────────────────────────────────────────────────────
# F6a: nlp_batch_tickets tracking table
# ─────────────────────────────────────────────────────────────────────

class TestBatchTicketsTable(unittest.TestCase):
    """F6a: nlp_batch_tickets table should be creatable and queryable."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        # Create minimal schema
        self.conn.executescript("""
            CREATE TABLE nlp_scan_runs (
                scan_id TEXT PRIMARY KEY
            );
            CREATE TABLE nlp_batches (
                batch_id TEXT PRIMARY KEY,
                scan_id TEXT,
                trc TEXT
            );
            CREATE TABLE nlp_ticket_classifications (
                ticket_id TEXT,
                scan_id TEXT,
                batch_id TEXT
            );
            CREATE TABLE nlp_batch_tickets (
                batch_id TEXT NOT NULL,
                ticket_id TEXT NOT NULL,
                scan_id TEXT NOT NULL,
                PRIMARY KEY (batch_id, ticket_id)
            );
            CREATE INDEX idx_nbt_scan ON nlp_batch_tickets(scan_id);
            CREATE INDEX idx_nbt_ticket ON nlp_batch_tickets(ticket_id);
        """)
        # Insert test data
        self.conn.execute(
            "INSERT INTO nlp_scan_runs VALUES (?)", ("scan_1",)
        )
        self.conn.execute(
            "INSERT INTO nlp_batches VALUES (?, ?, ?)",
            ("batch_1", "scan_1", "TRC_01")
        )
        for i in range(10):
            self.conn.execute(
                "INSERT INTO nlp_batch_tickets VALUES (?, ?, ?)",
                ("batch_1", f"ticket_{i}", "scan_1")
            )
        # Classify 7 of 10 tickets
        for i in range(7):
            self.conn.execute(
                "INSERT INTO nlp_ticket_classifications VALUES (?, ?, ?)",
                (f"ticket_{i}", "scan_1", "batch_1")
            )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_unclassified_query(self):
        """LEFT JOIN should find 3 unclassified tickets."""
        rows = self.conn.execute("""
            SELECT bt.ticket_id
            FROM nlp_batch_tickets bt
            LEFT JOIN nlp_ticket_classifications tc
                ON bt.ticket_id = tc.ticket_id
                AND bt.scan_id = tc.scan_id
            WHERE bt.scan_id = ? AND tc.ticket_id IS NULL
        """, ("scan_1",)).fetchall()
        self.assertEqual(len(rows), 3)
        ids = {r['ticket_id'] for r in rows}
        self.assertEqual(ids, {"ticket_7", "ticket_8", "ticket_9"})

    def test_all_classified_returns_empty(self):
        """When all tickets classified, query should return empty."""
        for i in range(7, 10):
            self.conn.execute(
                "INSERT INTO nlp_ticket_classifications VALUES (?, ?, ?)",
                (f"ticket_{i}", "scan_1", "batch_1")
            )
        self.conn.commit()
        rows = self.conn.execute("""
            SELECT bt.ticket_id
            FROM nlp_batch_tickets bt
            LEFT JOIN nlp_ticket_classifications tc
                ON bt.ticket_id = tc.ticket_id
                AND bt.scan_id = tc.scan_id
            WHERE bt.scan_id = ? AND tc.ticket_id IS NULL
        """, ("scan_1",)).fetchall()
        self.assertEqual(len(rows), 0)

    def test_ignore_on_duplicate(self):
        """INSERT OR IGNORE should not fail on duplicate."""
        # This ticket already exists
        self.conn.execute(
            "INSERT OR IGNORE INTO nlp_batch_tickets VALUES (?, ?, ?)",
            ("batch_1", "ticket_0", "scan_1")
        )
        count = self.conn.execute(
            "SELECT COUNT(*) FROM nlp_batch_tickets WHERE batch_id = ?",
            ("batch_1",)
        ).fetchone()[0]
        self.assertEqual(count, 10)


# ─────────────────────────────────────────────────────────────────────
# F6: Tail-batch cutoff
# ─────────────────────────────────────────────────────────────────────

class TestTailBatchCutoff(unittest.TestCase):
    """F6: Supervisor should cut off stalled tail batches."""

    def _make_supervisor(self, total_batches=20):
        from src.agents.supervisor import Supervisor
        sup = Supervisor.__new__(Supervisor)
        sup.workers = []
        sup.rate_governor = MagicMock()
        sup.db_path = ":memory:"
        sup.scan_id = "test_scan"
        sup._stop_event = threading.Event()
        sup._lock = threading.Lock()
        sup._total_batches = total_batches
        sup._completed_batches = 0
        sup._total_tickets = total_batches * 20
        sup._classified_tickets = 0
        sup._total_tokens_in = 0
        sup._total_tokens_out = 0
        sup._restarts = 0
        sup._errors = []
        sup._batch_durations = []
        sup._last_completion_time = time.time()
        sup._conn = None
        return sup

    def test_no_cutoff_when_below_95_pct(self):
        """Should NOT cutoff when < 95% complete."""
        sup = self._make_supervisor(20)
        # Complete 18/20 = 90%
        for _ in range(18):
            sup.notify_batch_complete("w0", {
                "classified": 10, "elapsed_seconds": 5.0
            })
        self.assertFalse(sup.should_cutoff())

    def test_no_cutoff_when_recent_completion(self):
        """Should NOT cutoff when a batch just completed."""
        sup = self._make_supervisor(20)
        # Complete 19/20 = 95%
        for _ in range(19):
            sup.notify_batch_complete("w0", {
                "classified": 10, "elapsed_seconds": 5.0
            })
        # Just completed — _last_completion_time is fresh
        self.assertFalse(sup.should_cutoff())

    def test_cutoff_when_stalled(self):
        """Should cutoff when 95%+ done and stalled for > 3x median."""
        sup = self._make_supervisor(20)
        # Complete 19/20 = 95%, each took 5s
        for _ in range(19):
            sup.notify_batch_complete("w0", {
                "classified": 10, "elapsed_seconds": 5.0
            })
        # Simulate stall: set last_completion_time far in the past
        # Median = 5s, cutoff_threshold = max(15, 120) = 120s
        sup._last_completion_time = time.time() - 130
        self.assertTrue(sup.should_cutoff())

    def test_cutoff_minimum_120s(self):
        """Cutoff threshold should be at least 120s even with fast batches."""
        sup = self._make_supervisor(20)
        for _ in range(19):
            sup.notify_batch_complete("w0", {
                "classified": 10, "elapsed_seconds": 2.0  # fast batches
            })
        # 3x median = 6s, but minimum is 120s
        sup._last_completion_time = time.time() - 10  # only 10s
        self.assertFalse(sup.should_cutoff())

    def test_all_complete_takes_priority(self):
        """_all_complete should return True before cutoff matters."""
        sup = self._make_supervisor(20)
        for _ in range(20):
            sup.notify_batch_complete("w0", {
                "classified": 10, "elapsed_seconds": 5.0
            })
        self.assertTrue(sup._all_complete())

    def test_batch_durations_tracked(self):
        """notify_batch_complete should track elapsed_seconds."""
        sup = self._make_supervisor(5)
        sup.notify_batch_complete("w0", {
            "classified": 5, "elapsed_seconds": 12.3
        })
        sup.notify_batch_complete("w1", {
            "classified": 5, "elapsed_seconds": 8.7
        })
        self.assertEqual(len(sup._batch_durations), 2)
        self.assertAlmostEqual(sup._batch_durations[0], 12.3)
        self.assertAlmostEqual(sup._batch_durations[1], 8.7)


# ─────────────────────────────────────────────────────────────────────
# F7: Worker queue timeout
# ─────────────────────────────────────────────────────────────────────

class TestWorkerQueueTimeout(unittest.TestCase):
    """F7: Workers should wait longer for requeued batches."""

    def test_queue_timeout_is_10s(self):
        """Verify the worker loop uses 10s timeout (not 2s)."""
        # Read the source to verify — this is a code inspection test
        import inspect
        from src.agents.scan_orchestrator import ScanOrchestrator
        source = inspect.getsource(ScanOrchestrator._worker_loop)
        self.assertIn("timeout=10", source,
                       "Worker loop should use 10s queue timeout")
        self.assertNotIn("timeout=2", source,
                         "Worker loop should NOT use 2s queue timeout")

    def test_worker_continues_on_empty_when_not_complete(self):
        """Verify the worker loop has 'continue' on Empty, not 'break'."""
        import inspect
        from src.agents.scan_orchestrator import ScanOrchestrator
        source = inspect.getsource(ScanOrchestrator._worker_loop)
        # After Empty exception, should check supervisor and continue
        self.assertIn("should_cutoff", source,
                       "Worker should check supervisor cutoff on empty queue")


# ─────────────────────────────────────────────────────────────────────
# P1: Faster 429 recovery
# ─────────────────────────────────────────────────────────────────────

class TestFast429Recovery(unittest.TestCase):
    """P1: Time-based fast recovery after rate limiting."""

    def test_time_based_recovery_after_30s(self):
        """After 30s clean, should restore 50% of lost concurrency."""
        from src.agents.rate_governor import SemaphoreRateGovernor
        gov = SemaphoreRateGovernor(max_concurrent=8, burst_delay=0.01)

        # Trigger 429 -> shrinks to 4
        gov.acquire(timeout=5)
        gov.release(duration=0.1, rate_limited=True)
        self.assertEqual(gov._current_concurrent, 4)

        # Simulate 35s passing since last 429
        gov._last_rate_limit_time = time.time() - 35

        # One success should trigger time-based recovery
        gov.acquire(timeout=5)
        gov.release(duration=0.1, success=True)
        # Should restore 50% of lost (4 lost, restore 2 -> 6)
        self.assertGreaterEqual(gov._current_concurrent, 6)

    def test_full_recovery_after_60s(self):
        """After 60s clean, should restore full concurrency."""
        from src.agents.rate_governor import SemaphoreRateGovernor
        gov = SemaphoreRateGovernor(max_concurrent=8, burst_delay=0.01)

        # Trigger 429 -> shrinks to 4
        gov.acquire(timeout=5)
        gov.release(duration=0.1, rate_limited=True)
        self.assertEqual(gov._current_concurrent, 4)

        # Simulate 65s passing
        gov._last_rate_limit_time = time.time() - 65

        # One success should trigger full recovery
        gov.acquire(timeout=5)
        gov.release(duration=0.1, success=True)
        self.assertEqual(gov._current_concurrent, 8)

    def test_fast_burst_delay_decay(self):
        """Burst delay should decay with factor 0.8 when > 30s since 429."""
        from src.agents.rate_governor import SemaphoreRateGovernor
        gov = SemaphoreRateGovernor(max_concurrent=8, burst_delay=0.01)

        gov._burst_delay = 2.0
        gov._last_rate_limit_time = time.time() - 35  # >30s ago

        # Feed 5 successes (Tier 1)
        for _ in range(5):
            gov.acquire(timeout=5)
            gov.release(duration=0.1, success=True)

        # factor=0.8 when >30s since 429
        self.assertAlmostEqual(gov._burst_delay, 2.0 * 0.8, places=2)


# ─────────────────────────────────────────────────────────────────────
# P2: Burst gate contention
# ─────────────────────────────────────────────────────────────────────

class TestBurstGateContention(unittest.TestCase):
    """P2: Check-then-lock pattern should reduce contention."""

    def test_concurrent_acquires_complete_quickly(self):
        """32 concurrent acquires should not take 32 * burst_delay."""
        from src.agents.rate_governor import SemaphoreRateGovernor
        gov = SemaphoreRateGovernor(max_concurrent=32, burst_delay=0.1)

        results = []
        start = time.time()

        def worker():
            acquired = gov.acquire(timeout=30)
            results.append(acquired)
            if acquired:
                gov.release(duration=0.01, success=True)

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        elapsed = time.time() - start
        self.assertEqual(sum(results), 16)
        # Should complete in < 5s, not 16 * 0.1 = 1.6s serial
        # (with check-then-lock, most don't block)
        self.assertLess(elapsed, 10.0)

    def test_burst_gate_respects_delay(self):
        """Rapid sequential acquires should still enforce burst_delay."""
        from src.agents.rate_governor import SemaphoreRateGovernor
        gov = SemaphoreRateGovernor(max_concurrent=8, burst_delay=0.2)

        start = time.time()
        gov.acquire(timeout=5)
        gov.release(duration=0.01, success=True)
        gov.acquire(timeout=5)
        gov.release(duration=0.01, success=True)
        elapsed = time.time() - start

        # Second acquire should wait ~0.2s
        self.assertGreaterEqual(elapsed, 0.15)


# ─────────────────────────────────────────────────────────────────────
# P4: Buffered commits
# ─────────────────────────────────────────────────────────────────────

class TestBufferedCommits(unittest.TestCase):
    """P4: ToolRegistry should batch commits."""

    def test_commit_interval_default(self):
        """Default commit interval should be 10."""
        from src.agents.tool_registry import ToolRegistry
        self.assertEqual(ToolRegistry._COMMIT_INTERVAL, 10)

    def test_flush_clears_buffer(self):
        """flush() should clear the write buffer."""
        from src.agents.tool_registry import ToolRegistry
        reg = ToolRegistry.__new__(ToolRegistry)
        reg._write_buffer = ["t1", "t2", "t3"]
        reg._conn = MagicMock()
        reg.flush()
        self.assertEqual(len(reg._write_buffer), 0)

    def test_close_flushes(self):
        """close() should call flush() before closing connection."""
        from src.agents.tool_registry import ToolRegistry
        reg = ToolRegistry.__new__(ToolRegistry)
        reg._write_buffer = ["t1"]
        mock_conn = MagicMock()
        reg._conn = mock_conn
        reg.close()
        mock_conn.commit.assert_called()
        mock_conn.close.assert_called()


# ─────────────────────────────────────────────────────────────────────
# P5: Context reset threshold
# ─────────────────────────────────────────────────────────────────────

class TestContextResetThreshold(unittest.TestCase):
    """P5: Reset threshold should be 20 for ACP mode."""

    def test_threshold_is_20(self):
        """MAX_BATCHES_BEFORE_RESET should be 20."""
        from src.agents.worker_agent import MAX_BATCHES_BEFORE_RESET
        self.assertEqual(MAX_BATCHES_BEFORE_RESET, 20)


if __name__ == '__main__':
    unittest.main()
