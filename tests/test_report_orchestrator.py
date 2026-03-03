"""
Build 8.5 — Report Orchestrator Unit Tests

Tests for the resilience features added in Build 8.5:
  T1: Canary probes set adaptive floor
  T2: Stall escalation triggers bridge restart
  T3: Retry budget (requeue on failure, exhaust on max retries)
  T4: Queue-pull dispatch (no head-of-line blocking)
  T5: Rate governor recovery after backoff
  T7: Backward-compatible API
"""

import sys
import time
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.rate_governor import RateGovernor


# ═══════════════════════════════════════════════════════════════
#  Helpers: Mock bridge that simulates GeminiBridge behavior
# ═══════════════════════════════════════════════════════════════

class MockBridge:
    """Lightweight mock for GeminiBridge with health tracking."""

    def __init__(self, latency_ms=5000, fail_pattern=None):
        """
        Args:
            latency_ms: Simulated probe latency.
            fail_pattern: List of bools — True = succeed, False = fail.
                          If None, all calls succeed.
        """
        self._latency_ms = latency_ms
        self._fail_pattern = fail_pattern or []
        self._call_count = 0
        self._alive = True
        self._consecutive_stalls = 0
        self._stall_count = 0
        self._restart_count = 0
        self._STALL_ESCALATION_THRESHOLD = 3

    def ensure_running(self):
        self._alive = True

    def is_alive(self):
        return self._alive

    def kill(self):
        self._alive = False

    def probe(self, timeout=30):
        return {"latency_ms": self._latency_ms, "status": "success",
                "error": None}

    def call_blocking(self, prompt, request_id, timeout=300):
        idx = self._call_count
        self._call_count += 1

        if idx < len(self._fail_pattern) and not self._fail_pattern[idx]:
            raise RuntimeError(
                "Bridge call failed: stall_timeout - No content "
                "received for 90000ms"
            )

        # Simulate small delay
        time.sleep(0.01)
        return f"Response for {request_id}"

    def record_stall(self):
        self._stall_count += 1
        self._consecutive_stalls += 1
        if self._consecutive_stalls >= self._STALL_ESCALATION_THRESHOLD:
            self._consecutive_stalls = 0
            return True  # escalation triggered
        return False

    def record_success(self):
        self._consecutive_stalls = 0

    def restart(self):
        self._restart_count += 1
        self._consecutive_stalls = 0
        self._alive = True

    def shutdown(self):
        self._alive = False


def _make_orchestrator(num_bridges=2, bridges=None, skip_probes=False):
    """Create a ReportOrchestrator with mock bridges (no real subprocesses)."""
    from src.agents.report_orchestrator import ReportOrchestrator

    orch = ReportOrchestrator.__new__(ReportOrchestrator)
    orch.db_path = "test.db"
    orch._model = "test"
    orch._num_bridges = num_bridges
    orch._bridges = bridges or [MockBridge() for _ in range(num_bridges)]
    orch._booted = True
    orch._cancelled = False
    orch._lock = threading.Lock()
    orch._adaptive_call_timeout = 60
    orch._rate_governor = RateGovernor(min_interval=0.05)  # Fast for tests
    return orch


# ═══════════════════════════════════════════════════════════════
#  T1: Canary probes set adaptive floor
# ═══════════════════════════════════════════════════════════════

class TestCanaryProbes:
    """T1: Verify canary probes derive adaptive rate floor and timeout."""

    def test_canary_probes_set_adaptive_floor(self):
        """Probes set the rate governor floor based on P50 latency."""
        bridges = [MockBridge(latency_ms=4000) for _ in range(3)]
        orch = _make_orchestrator(num_bridges=3, bridges=bridges)

        orch._run_canary_probes()

        # P50 of [4000, 4000, 4000] = 4000ms = 4.0s
        # Adaptive floor = max(15.0, 4.0 * 3.0) = 15.0
        # (4s * 3 = 12s, but floor is 15s minimum)
        assert orch._rate_governor._probe_floor == 15.0

    def test_canary_probes_high_latency_floor(self):
        """High latency probes set floor above 15s minimum."""
        bridges = [MockBridge(latency_ms=8000) for _ in range(3)]
        orch = _make_orchestrator(num_bridges=3, bridges=bridges)

        orch._run_canary_probes()

        # P50 = 8000ms = 8.0s, floor = max(15.0, 8.0 * 3.0) = 24.0
        assert orch._rate_governor._probe_floor == 24.0

    def test_canary_probes_set_adaptive_timeout(self):
        """Probes derive adaptive call timeout from P95."""
        bridges = [MockBridge(latency_ms=5000) for _ in range(4)]
        orch = _make_orchestrator(num_bridges=4, bridges=bridges)

        orch._run_canary_probes()

        # P95 = 5000ms = 5.0s
        # Timeout = max(45, min(600, 5.0 * 25 * 1.5)) = max(45, min(600, 187.5)) = 188
        assert orch._adaptive_call_timeout == 188

    def test_canary_probes_all_fail_keeps_defaults(self):
        """All probes failing keeps default thresholds."""
        bridges = [MockBridge() for _ in range(2)]
        for b in bridges:
            b.probe = lambda timeout=30: {"latency_ms": 0, "status": "error",
                                          "error": "timeout"}
        orch = _make_orchestrator(num_bridges=2, bridges=bridges)
        orch._adaptive_call_timeout = 300

        orch._run_canary_probes()

        # Defaults should be preserved
        assert orch._adaptive_call_timeout == 300
        assert orch._rate_governor._probe_floor == 15.0


# ═══════════════════════════════════════════════════════════════
#  T2: Stall escalation triggers restart
# ═══════════════════════════════════════════════════════════════

class TestStallEscalation:
    """T2: Verify stall tracking and bridge restart on escalation."""

    def test_stall_escalation_triggers_restart(self):
        """3 consecutive stalls on a bridge trigger restart."""
        # First 3 calls fail (stall), 4th succeeds
        bridge = MockBridge(fail_pattern=[False, False, False, True])
        orch = _make_orchestrator(num_bridges=1, bridges=[bridge])

        tasks = [
            {"id": f"task_{i}", "prompt": f"prompt {i}", "timeout": 60}
            for i in range(4)
        ]
        results = orch.run_parallel(tasks)

        # Bridge should have been restarted after 3 stalls
        assert bridge._restart_count >= 1

    def test_success_resets_stall_counter(self):
        """A success between stalls prevents escalation."""
        # Stall, succeed, stall, succeed — never hits 3 consecutive
        bridge = MockBridge(fail_pattern=[False, True, False, True])
        orch = _make_orchestrator(num_bridges=1, bridges=[bridge])

        tasks = [
            {"id": f"task_{i}", "prompt": f"prompt {i}", "timeout": 60}
            for i in range(4)
        ]
        results = orch.run_parallel(tasks)

        # No escalation should have triggered from the bridge itself
        # (retries may cause additional calls, but consecutive_stalls resets)
        assert bridge._consecutive_stalls == 0


# ═══════════════════════════════════════════════════════════════
#  T3: Retry budget
# ═══════════════════════════════════════════════════════════════

class TestRetryBudget:
    """T3: Verify retry budget requeues and exhaustion."""

    def test_retry_budget_requeues_on_stall(self):
        """Stall timeout requeues the task, which succeeds on retry."""
        # First call fails, then all succeed
        bridge = MockBridge(fail_pattern=[False, True, True, True])
        orch = _make_orchestrator(num_bridges=1, bridges=[bridge])

        tasks = [{"id": "task_a", "prompt": "test", "timeout": 60}]
        results = orch.run_parallel(tasks)

        # Task should succeed after retry
        assert "task_a" in results
        assert not results["task_a"].startswith("[Error:")

    def test_retry_budget_exhausted(self):
        """After max retries, task is marked as failed."""
        # All calls fail
        bridge = MockBridge(fail_pattern=[False] * 20)
        orch = _make_orchestrator(num_bridges=1, bridges=[bridge])

        tasks = [{"id": "task_fail", "prompt": "test", "timeout": 60}]
        results = orch.run_parallel(tasks)

        # Task should be marked as error after 3 retries (RETRY_BUDGET_STALL)
        assert "task_fail" in results
        assert results["task_fail"].startswith("[Error:")
        # Total calls: 1 original + 3 retries = 4
        assert bridge._call_count == 4

    def test_retry_budget_quota_gets_more_retries(self):
        """Quota/rate-limit errors get 6 retries (vs 3 for stalls)."""
        from src.agents.report_orchestrator import ReportOrchestrator
        assert ReportOrchestrator.RETRY_BUDGET_QUOTA == 6
        assert ReportOrchestrator.RETRY_BUDGET_STALL == 3
        assert ReportOrchestrator.RETRY_BUDGET_OTHER == 1

    def test_capacity_error_classified_as_quota(self):
        """'No capacity available' errors get QUOTA budget (6 retries)."""
        # Bridge that fails with capacity error, then succeeds
        bridge = MockBridge()
        call_count = [0]
        original_call = bridge.call_blocking

        def capacity_then_succeed(prompt, request_id, timeout=300):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise RuntimeError(
                    "Bridge call failed: unknown - No capacity available "
                    "for model gemini-2.5-flash on the server"
                )
            time.sleep(0.01)
            return f"Response for {request_id}"

        bridge.call_blocking = capacity_then_succeed
        orch = _make_orchestrator(num_bridges=1, bridges=[bridge])

        tasks = [{"id": "cap_task", "prompt": "test", "timeout": 60}]
        results = orch.run_parallel(tasks)

        # Task should succeed after retries (capacity → 6 retry budget)
        assert "cap_task" in results
        assert not results["cap_task"].startswith("[Error:")
        # Should have retried: 2 failures + 1 success = 3 calls
        assert call_count[0] == 3

    def test_capacity_error_triggers_rate_limit_backoff(self):
        """'capacity' errors trigger report_rate_limit(), not report_error()."""
        bridge = MockBridge()
        call_count = [0]

        def always_capacity(prompt, request_id, timeout=300):
            call_count[0] += 1
            raise RuntimeError(
                "Bridge call failed: unknown - No capacity available "
                "for model gemini-2.5-flash on the server"
            )

        bridge.call_blocking = always_capacity
        orch = _make_orchestrator(num_bridges=1, bridges=[bridge])

        # Record initial interval
        initial_interval = orch._rate_governor.min_interval

        tasks = [{"id": "cap_rl", "prompt": "test", "timeout": 60}]
        results = orch.run_parallel(tasks)

        # Governor should have backed off (doubled) from capacity errors
        assert orch._rate_governor.min_interval > initial_interval


# ═══════════════════════════════════════════════════════════════
#  T4: Queue-pull (no head-of-line blocking)
# ═══════════════════════════════════════════════════════════════

class TestQueuePullDispatch:
    """T4: Verify queue-pull dispatch eliminates head-of-line blocking."""

    def test_queue_pull_no_head_of_line_blocking(self):
        """Slow bridge doesn't block fast bridges from processing tasks."""
        # Bridge 0 is slow (500ms per call), bridge 1 is fast (10ms)
        # Large gap ensures fast bridge pulls significantly more work
        slow_bridge = MockBridge()
        fast_bridge = MockBridge()

        def slow_call(prompt, request_id, timeout=300):
            time.sleep(0.5)
            return f"slow: {request_id}"

        def fast_call(prompt, request_id, timeout=300):
            time.sleep(0.01)
            return f"fast: {request_id}"

        slow_bridge.call_blocking = slow_call
        fast_bridge.call_blocking = fast_call

        orch = _make_orchestrator(
            num_bridges=2,
            bridges=[slow_bridge, fast_bridge],
        )
        # Minimal rate interval + floor so governor doesn't dominate timing
        orch._rate_governor.min_interval = 0.001
        orch._rate_governor._probe_floor = 0.001

        # 20 tasks — with round-robin each bridge gets 10
        # With queue-pull, fast bridge should handle significantly more
        tasks = [
            {"id": f"t_{i}", "prompt": f"p {i}", "timeout": 60}
            for i in range(20)
        ]
        results = orch.run_parallel(tasks)

        # All tasks should complete
        assert len(results) == 20
        assert all(not v.startswith("[") for v in results.values())

        # Fast bridge should have handled more tasks than slow bridge
        fast_count = sum(1 for v in results.values() if "fast:" in v)
        slow_count = sum(1 for v in results.values() if "slow:" in v)
        assert fast_count > slow_count, (
            f"Fast bridge ({fast_count}) should handle more than "
            f"slow bridge ({slow_count})"
        )

    def test_all_tasks_complete(self):
        """All tasks get results regardless of which worker processes them."""
        orch = _make_orchestrator(num_bridges=3)

        tasks = [
            {"id": f"trc_{i}", "prompt": f"Analyze TRC {i}", "timeout": 60}
            for i in range(20)
        ]
        results = orch.run_parallel(tasks)

        assert len(results) == 20
        for i in range(20):
            assert f"trc_{i}" in results


# ═══════════════════════════════════════════════════════════════
#  T5: Rate governor recovery
# ═══════════════════════════════════════════════════════════════

class TestRateGovernorRecovery:
    """T5: Verify aggressive recovery after rate limit backoff."""

    def test_recovery_after_10_successes(self):
        """Two-tier recovery: 10% at 5, then 30% at 10 (>120s since 429)."""
        gov = RateGovernor(min_interval=40.0)
        gov._probe_floor = 15.0
        gov._last_rate_limit_time = time.time() - 200  # 200s ago

        # Record 10 successes
        for _ in range(10):
            gov.report_success(5.0)

        # Tier 1 at 5: 40.0 * 0.9 = 36.0
        # Tier 2 at 10: 36.0 * 0.7 = 25.2 (30% since >120s)
        assert gov.min_interval == pytest.approx(25.2, abs=0.1)

    def test_recovery_after_5_successes_is_10_percent(self):
        """After 5 successes (but <10), standard 10% tightening."""
        gov = RateGovernor(min_interval=40.0)
        gov._probe_floor = 15.0

        for _ in range(5):
            gov.report_success(5.0)

        # 40.0 * 0.9 = 36.0
        assert gov.min_interval == pytest.approx(36.0, abs=0.1)

    def test_recovery_clamped_to_probe_floor(self):
        """Tightening never goes below probe floor."""
        gov = RateGovernor(min_interval=18.0)
        gov._probe_floor = 17.0
        gov._last_rate_limit_time = time.time() - 200

        for _ in range(10):
            gov.report_success(5.0)

        # 18.0 * 0.7 = 12.6, but clamped to floor 17.0
        assert gov.min_interval == pytest.approx(17.0, abs=0.1)

    def test_backoff_doubles_interval(self):
        """Rate limit doubles the interval."""
        gov = RateGovernor(min_interval=20.0)

        gov.report_rate_limit()

        assert gov.min_interval == pytest.approx(40.0, abs=0.1)

    def test_backoff_capped_at_120(self):
        """Backoff never exceeds 120s."""
        gov = RateGovernor(min_interval=80.0)

        gov.report_rate_limit()

        assert gov.min_interval == pytest.approx(120.0, abs=0.1)

    def test_recovery_recent_rate_limit_uses_20_percent(self):
        """If rate limit was <120s ago, tier 2 uses 20% (not 30%)."""
        gov = RateGovernor(min_interval=40.0)
        gov._probe_floor = 15.0
        gov._last_rate_limit_time = time.time() - 60  # 60s ago (< 120s)

        for _ in range(10):
            gov.report_success(5.0)

        # Tier 1 at 5: 40.0 * 0.9 = 36.0
        # Tier 2 at 10: 36.0 * 0.8 = 28.8 (20% since <120s)
        assert gov.min_interval == pytest.approx(28.8, abs=0.1)

    def test_full_recovery_cycle(self):
        """Simulate: normal → rate limit → backoff → two-tier recovery."""
        gov = RateGovernor(min_interval=20.0)
        gov._probe_floor = 15.0

        # Hit rate limit
        gov.report_rate_limit()
        assert gov.min_interval == pytest.approx(40.0, abs=0.1)

        # Simulate passage of time (>120s)
        gov._last_rate_limit_time = time.time() - 150

        # 10 consecutive successes (two-tier recovery)
        for _ in range(10):
            gov.report_success(5.0)

        # Tier 1 at 5: 40.0 * 0.9 = 36.0
        # Tier 2 at 10: 36.0 * 0.7 = 25.2 (30% since >120s since last 429)
        assert gov.min_interval == pytest.approx(25.2, abs=0.1)

        # Another 10 successes — two more tiers
        for _ in range(10):
            gov.report_success(5.0)

        # Tier 1 at 5: 25.2 * 0.9 = 22.68
        # Tier 2 at 10: 22.68 * 0.7 = 15.876 → clamped to 15.0 (floor)
        assert gov.min_interval == pytest.approx(15.876, abs=0.1)


# ═══════════════════════════════════════════════════════════════
#  T7: Backward-compatible API
# ═══════════════════════════════════════════════════════════════

class TestBackwardCompatibleAPI:
    """Verify existing run_parallel(tasks) interface unchanged."""

    def test_run_parallel_returns_dict(self):
        """run_parallel returns dict mapping task_id → text."""
        orch = _make_orchestrator(num_bridges=2)

        tasks = [
            {"id": "a", "prompt": "test a", "timeout": 60},
            {"id": "b", "prompt": "test b", "timeout": 60},
        ]
        results = orch.run_parallel(tasks)

        assert isinstance(results, dict)
        assert "a" in results
        assert "b" in results
        assert isinstance(results["a"], str)
        assert isinstance(results["b"], str)

    def test_progress_callback_fires(self):
        """Progress callback receives (message, percent) tuples."""
        orch = _make_orchestrator(num_bridges=1)

        progress_calls = []

        def on_progress(message, percent):
            progress_calls.append((message, percent))

        tasks = [
            {"id": f"t_{i}", "prompt": f"p {i}", "timeout": 60}
            for i in range(3)
        ]
        orch.run_parallel(tasks, progress_cb=on_progress)

        assert len(progress_calls) == 3
        # Final call should be at ~80% (3/3 * 80)
        assert progress_calls[-1][1] == 80

    def test_cancel_stops_processing(self):
        """Cancelling the orchestrator stops worker loops."""
        orch = _make_orchestrator(num_bridges=2)

        # Add delay to calls so we can cancel mid-run
        for bridge in orch._bridges:
            original_call = bridge.call_blocking

            def slow_call(prompt, request_id, timeout=300, _orig=original_call):
                time.sleep(0.05)
                return _orig(prompt, request_id, timeout)

            bridge.call_blocking = slow_call

        tasks = [
            {"id": f"t_{i}", "prompt": f"p {i}", "timeout": 60}
            for i in range(50)
        ]

        # Cancel after a short delay
        def cancel_soon():
            time.sleep(0.2)
            orch.cancel()

        cancel_thread = threading.Thread(target=cancel_soon)
        cancel_thread.start()

        results = orch.run_parallel(tasks)
        cancel_thread.join()

        # Not all tasks should have completed
        assert len(results) < 50

    def test_get_stats_includes_adaptive_timeout(self):
        """get_stats() includes the adaptive_timeout field (8.5)."""
        orch = _make_orchestrator()
        stats = orch.get_stats()

        assert "adaptive_timeout" in stats
        assert stats["adaptive_timeout"] == 60  # set in _make_orchestrator


# ═══════════════════════════════════════════════════════════════
#  Run
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
