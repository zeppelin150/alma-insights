"""
Build 9.0 — VOC Pipeline Unit Tests

Tests for the multi-perspective VOC pipeline:
  T1:   _send_raw() thread-safe locking
  T1.5: Priority queue dispatch + dynamic injection
  T2:   VOCBatchPacker bin-packing
  T3:   run_resilient() single-call resilience
  T4:   Stat context caching
  T5:   Batch response parsing + split-on-failure
  T6:   Pipelined accumulator + specialists
  T7:   Convergence phase
"""

import sys
import json
import time
import threading
from pathlib import Path
from queue import PriorityQueue
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.rate_governor import RateGovernor


# ═══════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════

class MockBridge:
    """Lightweight mock for GeminiBridge."""

    def __init__(self, latency_ms=5000, response_fn=None, delay=0.01):
        self._latency_ms = latency_ms
        self._response_fn = response_fn
        self._delay = delay
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
        self._call_count += 1
        time.sleep(self._delay)
        if self._response_fn:
            return self._response_fn(request_id, prompt)
        return f"Response for {request_id}"

    def record_stall(self):
        self._stall_count += 1
        self._consecutive_stalls += 1
        if self._consecutive_stalls >= self._STALL_ESCALATION_THRESHOLD:
            self._consecutive_stalls = 0
            return True
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
    """Create a ReportOrchestrator with mock bridges."""
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
    orch._rate_governor = RateGovernor(min_interval=0.01)
    return orch


# ═══════════════════════════════════════════════════════════════
#  T1: _send_raw() Locking
# ═══════════════════════════════════════════════════════════════

class TestSendRawLocking:
    """T1: Verify concurrent writes to stdin are serialized."""

    def test_concurrent_writes_serialized(self):
        """Multiple threads writing via _send_jsonrpc don't interleave."""
        from src.agents.acp_bridge import ACPBridge

        wrapper = ACPBridge.__new__(ACPBridge)
        wrapper._send_lock = threading.Lock()
        wrapper._msg_id = 0
        wrapper._msg_id_lock = threading.Lock()

        # Track write order
        writes = []
        write_lock = threading.Lock()

        class FakeProcess:
            poll = MagicMock(return_value=None)

            class stdin:
                @staticmethod
                def write(data):
                    with write_lock:
                        writes.append(data)
                    time.sleep(0.005)  # Slow enough to cause interleaving

                @staticmethod
                def flush():
                    pass

        wrapper._process = FakeProcess()

        def send(method_name):
            wrapper._send_jsonrpc(method_name, {"test": True})

        threads = [threading.Thread(target=send, args=(f"method_{i}",))
                   for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(writes) == 10
        # Each write should be a complete JSON-RPC line (no interleaving)
        for w in writes:
            parsed = json.loads(w.strip())
            assert "jsonrpc" in parsed
            assert "method" in parsed


# ═══════════════════════════════════════════════════════════════
#  T1.5: Priority Queue Dispatch
# ═══════════════════════════════════════════════════════════════

class TestPriorityQueueDispatch:
    """T1.5: Verify PriorityQueue dispatch semantics."""

    def test_priority_0_dispatched_before_priority_1(self):
        """Priority 0 tasks are dispatched before priority 1."""
        dispatch_order = []

        def on_complete(task_id, result):
            dispatch_order.append(task_id)

        bridges = [MockBridge(delay=0.005)
                   for _ in range(1)]  # Single bridge to enforce ordering
        orch = _make_orchestrator(num_bridges=1, bridges=bridges)

        task_queue = PriorityQueue()
        drain_event = threading.Event()

        # Enqueue: priority 1 first, then priority 0
        task_queue.put((1, 0, {"id": "low_1", "prompt": "p", "timeout": 60}, 0))
        task_queue.put((1, 1, {"id": "low_2", "prompt": "p", "timeout": 60}, 0))
        task_queue.put((0, 2, {"id": "high_1", "prompt": "p", "timeout": 60}, 0))
        task_queue.put((0, 3, {"id": "high_2", "prompt": "p", "timeout": 60}, 0))

        drain_event.set()  # No dynamic injection

        results = orch.run_parallel(
            [], task_queue=task_queue, drain_event=drain_event,
            on_complete=on_complete)

        # High priority should be dispatched first
        assert dispatch_order[0] in ("high_1", "high_2")
        assert dispatch_order[1] in ("high_1", "high_2")

    def test_sequence_breaks_ties_within_priority(self):
        """Within the same priority, lower sequence number goes first."""
        dispatch_order = []

        def on_complete(task_id, result):
            dispatch_order.append(task_id)

        bridges = [MockBridge(delay=0.005)]
        orch = _make_orchestrator(num_bridges=1, bridges=bridges)

        task_queue = PriorityQueue()
        drain_event = threading.Event()

        task_queue.put((0, 10, {"id": "seq10", "prompt": "p", "timeout": 60}, 0))
        task_queue.put((0, 5, {"id": "seq5", "prompt": "p", "timeout": 60}, 0))
        task_queue.put((0, 1, {"id": "seq1", "prompt": "p", "timeout": 60}, 0))

        drain_event.set()

        orch.run_parallel(
            [], task_queue=task_queue, drain_event=drain_event,
            on_complete=on_complete)

        assert dispatch_order == ["seq1", "seq5", "seq10"]

    def test_dynamic_injection_via_external_queue(self):
        """Tasks injected after start are still dispatched."""
        task_queue = PriorityQueue()
        drain_event = threading.Event()
        results_collected = {}

        def on_complete(task_id, result):
            results_collected[task_id] = result

        bridges = [MockBridge(delay=0.01) for _ in range(2)]
        orch = _make_orchestrator(num_bridges=2, bridges=bridges)

        # Enqueue initial task
        task_queue.put((0, 0, {"id": "initial", "prompt": "p", "timeout": 60}, 0))

        # Inject after a delay (simulates specialist injector)
        def inject_later():
            time.sleep(0.1)
            task_queue.put(
                (0, 1, {"id": "injected", "prompt": "p", "timeout": 60}, 0))
            time.sleep(0.1)
            drain_event.set()

        injector = threading.Thread(target=inject_later)
        injector.start()

        results = orch.run_parallel(
            [], task_queue=task_queue, drain_event=drain_event,
            on_complete=on_complete)

        injector.join()

        assert "initial" in results
        assert "injected" in results

    def test_drain_event_keeps_workers_alive(self):
        """Workers stay alive until drain_event is set AND queue is empty."""
        task_queue = PriorityQueue()
        drain_event = threading.Event()
        completed = []

        def on_complete(task_id, result):
            completed.append(task_id)

        bridges = [MockBridge(delay=0.01)]
        orch = _make_orchestrator(num_bridges=1, bridges=bridges)

        # Initial task
        task_queue.put((0, 0, {"id": "t1", "prompt": "p", "timeout": 60}, 0))

        # Inject second task after first completes, then signal drain
        def inject_and_drain():
            while "t1" not in completed:
                time.sleep(0.02)
            task_queue.put(
                (0, 1, {"id": "t2", "prompt": "p", "timeout": 60}, 0))
            time.sleep(0.1)
            drain_event.set()

        t = threading.Thread(target=inject_and_drain)
        t.start()

        results = orch.run_parallel(
            [], task_queue=task_queue, drain_event=drain_event,
            on_complete=on_complete)

        t.join()

        assert "t1" in results
        assert "t2" in results


# ═══════════════════════════════════════════════════════════════
#  T1.5 cont: on_complete Callback
# ═══════════════════════════════════════════════════════════════

class TestOnCompleteCallback:
    """Verify on_complete callback behavior."""

    def test_callback_fires_for_each_task(self):
        """on_complete called once per completed task."""
        completed = []

        def on_complete(task_id, result):
            completed.append((task_id, result))

        orch = _make_orchestrator(num_bridges=2)
        tasks = [
            {"id": "a", "prompt": "p", "timeout": 60},
            {"id": "b", "prompt": "p", "timeout": 60},
            {"id": "c", "prompt": "p", "timeout": 60},
        ]

        orch.run_parallel(tasks, on_complete=on_complete)

        assert len(completed) == 3
        completed_ids = {c[0] for c in completed}
        assert completed_ids == {"a", "b", "c"}

    def test_callback_receives_correct_result(self):
        """on_complete receives the actual response text."""
        results = {}

        def response_fn(req_id, prompt):
            return f"result_{req_id}"

        def on_complete(task_id, result):
            results[task_id] = result

        bridges = [MockBridge(response_fn=response_fn)]
        orch = _make_orchestrator(num_bridges=1, bridges=bridges)

        orch.run_parallel(
            [{"id": "x", "prompt": "p", "timeout": 60}],
            on_complete=on_complete)

        # Result contains the request_id (which gets rorch_ prefix)
        assert "x" in results
        assert results["x"].startswith("result_rorch_x_")


# ═══════════════════════════════════════════════════════════════
#  T2: VOCBatchPacker
# ═══════════════════════════════════════════════════════════════

class TestVOCBatchPacker:
    """T2: Verify bin-packing behavior."""

    def test_small_trcs_packed_together(self):
        """Multiple small TRCs fit into a single batch."""
        from src.agents.voc_batch_packer import VOCBatchPacker

        packer = VOCBatchPacker(model="gemini-2.5-flash")
        # Each TRC is small (10K chars)
        trc_sizes = [(f"TRC-{i}", 10_000) for i in range(10)]

        batches = packer.pack_trcs(trc_sizes)

        total_trcs = sum(len(b["trcs"]) for b in batches)
        assert total_trcs == 10
        # Should pack into fewer batches than TRCs
        assert len(batches) < 10

    def test_large_trc_chunked(self):
        """A TRC exceeding budget is split into chunks."""
        from src.agents.voc_batch_packer import VOCBatchPacker

        packer = VOCBatchPacker(model="gemini-2.5-flash")
        budget = packer.input_budget

        # TRC is 3x the budget
        trc_sizes = [("BIG-TRC", budget * 3)]
        trc_ticket_counts = {"BIG-TRC": 300}

        batches = packer.pack_trcs(trc_sizes, trc_ticket_counts)

        # Should be split into 3 chunks
        assert len(batches) >= 3
        for b in batches:
            assert b["trcs"] == ["BIG-TRC"]
            assert "BIG-TRC" in b["chunk_info"]

    def test_mixed_sizes_correct(self):
        """Mix of large and small TRCs are handled correctly."""
        from src.agents.voc_batch_packer import VOCBatchPacker

        packer = VOCBatchPacker(model="gemini-2.5-flash")
        budget = packer.input_budget

        trc_sizes = [
            ("OVERSIZED", budget * 2),
            ("MEDIUM", budget // 2),
            ("SMALL-1", 5_000),
            ("SMALL-2", 5_000),
            ("SMALL-3", 5_000),
        ]
        trc_ticket_counts = {"OVERSIZED": 200}

        batches = packer.pack_trcs(trc_sizes, trc_ticket_counts)

        # OVERSIZED should be chunked
        chunked = [b for b in batches if b["chunk_info"]]
        assert len(chunked) >= 2

        # All TRCs accounted for
        all_trcs = []
        for b in batches:
            all_trcs.extend(b["trcs"])
        for trc in ["OVERSIZED", "MEDIUM", "SMALL-1", "SMALL-2", "SMALL-3"]:
            assert trc in all_trcs

    def test_headroom_enforced(self):
        """25% headroom reserve is applied to the budget."""
        from src.agents.voc_batch_packer import (
            VOCBatchPacker, HEADROOM_FACTOR, BATCH_PROMPT_OVERHEAD)
        from src.agents.batch_packer import MODEL_INPUT_LIMITS

        packer = VOCBatchPacker(model="gemini-2.5-flash")
        raw = MODEL_INPUT_LIMITS["gemini-2.5-flash"]
        expected = int(raw * HEADROOM_FACTOR) - BATCH_PROMPT_OVERHEAD

        assert packer.input_budget == expected
        assert packer.input_budget < raw

    def test_chunk_info_returned(self):
        """chunk_info contains (chunk_n, chunk_total) tuples."""
        from src.agents.voc_batch_packer import VOCBatchPacker

        packer = VOCBatchPacker(model="gemini-2.5-flash")
        budget = packer.input_budget

        trc_sizes = [("BIG", budget * 2 + 1)]
        trc_ticket_counts = {"BIG": 100}

        batches = packer.pack_trcs(trc_sizes, trc_ticket_counts)

        for i, b in enumerate(batches):
            assert "BIG" in b["chunk_info"]
            chunk_n, chunk_total = b["chunk_info"]["BIG"]
            assert chunk_n == i
            assert chunk_total == len(batches)

    def test_empty_input(self):
        """Empty trc_sizes returns empty batch list."""
        from src.agents.voc_batch_packer import VOCBatchPacker

        packer = VOCBatchPacker(model="gemini-2.5-flash")
        assert packer.pack_trcs([]) == []


# ═══════════════════════════════════════════════════════════════
#  T3: run_resilient()
# ═══════════════════════════════════════════════════════════════

class TestRunResilient:
    """T3: Verify single-call resilience wrapper."""

    def test_success(self):
        """Successful call returns response text."""
        orch = _make_orchestrator(num_bridges=1)
        result = orch.run_resilient("test prompt", request_id="test_1")

        assert "test_1" in result  # MockBridge returns "Response for test_1"

    def test_raises_on_exhausted_retries(self):
        """Raises RuntimeError when all retries exhausted."""
        def always_fail(req_id, prompt):
            raise RuntimeError("Bridge call failed: stall_timeout")

        bridges = [MockBridge(response_fn=always_fail)]
        orch = _make_orchestrator(num_bridges=1, bridges=bridges)

        with pytest.raises(RuntimeError):
            orch.run_resilient("failing prompt", request_id="fail_1")


# ═══════════════════════════════════════════════════════════════
#  T4: Stat Context Caching
# ═══════════════════════════════════════════════════════════════

class TestStatContextCaching:
    """T4: Verify stat context cache eliminates redundant calls."""

    def test_cache_hit(self):
        """Second call for same TRC uses cache, bypassing report_builder."""
        from src.data.voc_builder import VOCBuilder

        builder = VOCBuilder.__new__(VOCBuilder)
        builder._stat_context_cache = {}

        call_count = [0]
        original_method = VOCBuilder._build_stat_context_for_trc

        # Pre-populate cache
        builder._stat_context_cache[("TRC-A", "2025-01-01", "2025-01-31")] = "cached_result"

        # Call the actual method — it should return from cache immediately
        result = original_method(builder, "TRC-A", "2025-01-01", "2025-01-31")
        assert result == "cached_result"

    def test_cache_cleared_per_run(self):
        """Cache is cleared at the start of each run()."""
        from src.data.voc_builder import VOCBuilder

        builder = VOCBuilder.__new__(VOCBuilder)
        builder._stat_context_cache = {"old": "data"}
        builder._cancelled = False
        builder._progress = lambda msg, pct: None
        builder.db = MagicMock()
        builder.gemini = MagicMock()
        builder.gemini.model = "gemini-2.5-flash"
        builder._orchestrator = None

        # Mock _build_plan to return no TRCs
        builder.db.get_trc_ticket_counts.return_value = []
        builder.db.get_latest_completed_scan.return_value = None

        result = builder.run("2025-01-01", "2025-01-31")

        assert builder._stat_context_cache == {}


# ═══════════════════════════════════════════════════════════════
#  T5: Batch Response Parsing
# ═══════════════════════════════════════════════════════════════

class TestBatchResponseParsing:
    """T5: Verify multi-TRC response parsing."""

    def _make_builder(self):
        from src.data.voc_builder import VOCBuilder
        builder = VOCBuilder.__new__(VOCBuilder)
        builder._stat_context_cache = {}
        return builder

    def test_all_trcs_extracted(self):
        """All TRC sections are correctly parsed."""
        builder = self._make_builder()

        response = (
            "Some preamble text\n"
            "=== TRC: TRC-001 ===\n"
            "Analysis for TRC-001 here.\n"
            "## FRICTION THEMES\n...\n"
            "=== TRC: TRC-002 ===\n"
            "Analysis for TRC-002 here.\n"
            "## FRICTION THEMES\n...\n"
            "=== TRC: TRC-003 ===\n"
            "Analysis for TRC-003 here.\n"
        )

        parsed, missing = builder._parse_batch_response(
            response, ["TRC-001", "TRC-002", "TRC-003"])

        assert len(parsed) == 3
        assert "TRC-001" in parsed
        assert "TRC-002" in parsed
        assert "TRC-003" in parsed
        assert missing == []

    def test_missing_trcs_detected(self):
        """Missing TRCs are reported."""
        builder = self._make_builder()

        response = (
            "=== TRC: TRC-001 ===\n"
            "Analysis for TRC-001.\n"
        )

        parsed, missing = builder._parse_batch_response(
            response, ["TRC-001", "TRC-002"])

        assert "TRC-001" in parsed
        assert "TRC-002" in missing

    def test_single_trc_no_delimiter(self):
        """Single-TRC batch returns entire response."""
        builder = self._make_builder()

        response = "Full analysis text without delimiters."
        parsed, missing = builder._parse_batch_response(
            response, ["TRC-SOLO"])

        assert parsed == {"TRC-SOLO": response}
        assert missing == []

    def test_error_result_returns_empty(self):
        """Empty/None response returns all as missing."""
        builder = self._make_builder()

        parsed, missing = builder._parse_batch_response(
            None, ["TRC-A", "TRC-B"])

        assert parsed == {}
        assert missing == ["TRC-A", "TRC-B"]


# ═══════════════════════════════════════════════════════════════
#  T5 cont: Oversized TRC Chunking
# ═══════════════════════════════════════════════════════════════

class TestOversizedTRCChunking:
    """T5: Verify TRC chunk merge."""

    def _make_builder(self):
        from src.data.voc_builder import VOCBuilder
        builder = VOCBuilder.__new__(VOCBuilder)
        builder._stat_context_cache = {}
        return builder

    def test_chunks_merged(self):
        """Multiple chunks are merged with clear demarcation."""
        builder = self._make_builder()

        chunk_results = [
            (0, "Chunk 0 analysis"),
            (1, "Chunk 1 analysis"),
            (2, "Chunk 2 analysis"),
        ]

        merged = builder._merge_chunked_trc_results("BIG-TRC", chunk_results)

        assert "MERGED ANALYSIS" in merged
        assert "Chunk 1/3" in merged
        assert "Chunk 2/3" in merged
        assert "Chunk 3/3" in merged
        assert "Chunk 0 analysis" in merged
        assert "Chunk 2 analysis" in merged

    def test_single_chunk_passthrough(self):
        """Single chunk returns the analysis directly."""
        builder = self._make_builder()

        result = builder._merge_chunked_trc_results(
            "SMALL-TRC", [(0, "Only chunk")])

        assert result == "Only chunk"


# ═══════════════════════════════════════════════════════════════
#  T6: Accumulator Parsing
# ═══════════════════════════════════════════════════════════════

class TestAccumulatorParsing:
    """T6: Verify accumulator response parsing."""

    def _make_builder(self):
        from src.data.voc_builder import VOCBuilder
        builder = VOCBuilder.__new__(VOCBuilder)
        builder._stat_context_cache = {}
        return builder

    def test_both_sections_extracted(self):
        """NEW_FINDINGS and RUNNING_SYNTHESIS are both extracted."""
        builder = self._make_builder()

        response = (
            "## NEW_FINDINGS\n"
            "### Finding 1\n"
            "Some finding text.\n"
            "\n"
            "## RUNNING_SYNTHESIS\n"
            "### Priority Findings\n"
            "1. Top finding\n"
        )

        findings, synthesis = builder._parse_accumulator_response(response)

        assert "Finding 1" in findings
        assert "Priority Findings" in synthesis
        assert "Top finding" in synthesis

    def test_malformed_fallback(self):
        """Malformed response without headers treated as findings."""
        builder = self._make_builder()

        response = "Just some raw text without any section headers."
        findings, synthesis = builder._parse_accumulator_response(response)

        assert findings == response.strip()
        assert synthesis == ""

    def test_empty_response(self):
        """Empty response returns empty strings."""
        builder = self._make_builder()
        findings, synthesis = builder._parse_accumulator_response("")
        assert findings == ""
        assert synthesis == ""


# ═══════════════════════════════════════════════════════════════
#  T6 cont: Specialist Compression
# ═══════════════════════════════════════════════════════════════

class TestSpecialistCompression:
    """T6: Verify analysis compression for specialists."""

    def _make_builder(self):
        from src.data.voc_builder import VOCBuilder
        builder = VOCBuilder.__new__(VOCBuilder)
        builder._stat_context_cache = {}
        return builder

    def test_low_volume_trcs_dropped(self):
        """TRCs below MIN_TICKETS threshold are excluded."""
        builder = self._make_builder()

        trc_analyses = {
            "HIGH": "A" * 1000,
            "LOW": "B" * 1000,
        }
        plan = {
            "trc_plans": [
                {"trc": "HIGH", "total_tickets": 100},
                {"trc": "LOW", "total_tickets": 5},  # Below MIN_TICKETS=10
            ]
        }

        result = builder._compress_analyses_for_specialists(
            trc_analyses, plan, target=5000)

        assert "HIGH" in result
        assert "LOW" not in result

    def test_truncation_applied(self):
        """When total exceeds target, analyses are truncated."""
        builder = self._make_builder()

        trc_analyses = {
            f"TRC-{i}": "X" * 5000 for i in range(20)
        }
        plan = {
            "trc_plans": [
                {"trc": f"TRC-{i}", "total_tickets": 100}
                for i in range(20)
            ]
        }

        result = builder._compress_analyses_for_specialists(
            trc_analyses, plan, target=20_000)

        assert len(result) < 100_000  # Original is 100K
        assert "truncated for specialist input" in result


# ═══════════════════════════════════════════════════════════════
#  T7: Convergence
# ═══════════════════════════════════════════════════════════════

class TestConvergence:
    """T7: Verify convergence phase."""

    def test_graceful_degradation(self):
        """When convergence fails, partial report is assembled."""
        from src.data.voc_builder import VOCBuilder

        builder = VOCBuilder.__new__(VOCBuilder)
        builder._stat_context_cache = {}

        accumulator_result = {
            "ledger": "Round 1 findings here",
            "synthesis": "Top priority: billing errors",
            "rounds_completed": 3,
        }
        specialist_results = {
            "pattern": "## 2. TOP FRICTION POINTS\nBilling errors",
            "novelty": "## 3. WHAT'S GETTING WORSE\nNew payment issues",
            "friction": "## 5. ROOT CAUSE MAP\nSystem bugs",
        }

        report = builder._assemble_partial_report(
            accumulator_result, specialist_results)

        assert "VOC Root Cause Analysis Report" in report
        assert "EXECUTIVE SUMMARY" in report
        assert "billing errors" in report
        assert "TOP FRICTION POINTS" in report
        assert "WHAT'S GETTING WORSE" in report
        assert "ROOT CAUSE MAP" in report

    def test_compress_specialist_reports(self):
        """Specialist reports compressed proportionally."""
        from src.data.voc_builder import VOCBuilder

        builder = VOCBuilder.__new__(VOCBuilder)
        builder._stat_context_cache = {}

        specialist_results = {
            "pattern": "P" * 10000,
            "novelty": "N" * 5000,
            "friction": "F" * 5000,
        }

        compressed = builder._compress_specialist_reports(
            specialist_results, target_total=5000)

        total = sum(len(v) for v in compressed.values())
        # Should be roughly at target (with truncation markers)
        assert total < 10000  # Less than original 20K

    def test_empty_specialist_handling(self):
        """Empty specialist results produce minimal report."""
        from src.data.voc_builder import VOCBuilder

        builder = VOCBuilder.__new__(VOCBuilder)
        builder._stat_context_cache = {}

        accumulator_result = {
            "ledger": "",
            "synthesis": "",
            "rounds_completed": 0,
        }
        specialist_results = {"pattern": "", "novelty": "", "friction": ""}

        report = builder._assemble_partial_report(
            accumulator_result, specialist_results)

        assert "Report generation failed" in report


# ═══════════════════════════════════════════════════════════════
#  Backward Compatibility
# ═══════════════════════════════════════════════════════════════

class TestBackwardCompatibility:
    """Verify Build 9.0 doesn't break the legacy API."""

    def test_run_returns_expected_keys(self):
        """run() still returns {report_text, trc_analyses, stats}."""
        from src.data.voc_builder import VOCBuilder

        builder = VOCBuilder.__new__(VOCBuilder)
        builder._stat_context_cache = {}
        builder._cancelled = False
        builder._progress = lambda msg, pct: None
        builder.db = MagicMock()
        builder.gemini = MagicMock()
        builder.gemini.model = "gemini-2.5-flash"
        builder._orchestrator = None

        # Empty plan → immediate return
        builder.db.get_trc_ticket_counts.return_value = []
        builder.db.get_latest_completed_scan.return_value = None

        result = builder.run("2025-01-01", "2025-01-31")

        assert "report_text" in result
        assert "trc_analyses" in result
        assert "stats" in result

    def test_run_parallel_backward_compatible(self):
        """run_parallel() without new params behaves identically."""
        orch = _make_orchestrator(num_bridges=2)

        tasks = [
            {"id": "t1", "prompt": "p1", "timeout": 60},
            {"id": "t2", "prompt": "p2", "timeout": 60},
        ]

        results = orch.run_parallel(tasks)

        assert "t1" in results
        assert "t2" in results
        # Results contain responses (mock bridge embeds rorch_ prefixed ID)
        assert "Response for rorch_t1_" in results["t1"]
        assert "Response for rorch_t2_" in results["t2"]
