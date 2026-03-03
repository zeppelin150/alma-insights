"""
Alma Insights -- Report Orchestrator (Build 9.0)

Manages a pool of persistent GeminiBridge subprocesses for parallelized
report generation.  Primary use case: VOC multi-phase pipeline (batched
TRC analysis + accumulator + specialists via priority dispatch).

Build 8.5 additions (shared resilience layer from NLP scan orchestrator):
  T1: Canary probes on boot → adaptive rate floor + call timeout
  T2: Stall escalation + bridge restart (3 consecutive → auto-restart)
  T3: Retry budget with requeue (stall=3, quota=6, other=1)
  T4: Queue-pull dispatch (replaces round-robin, eliminates head-of-line blocking)
  T6: Default 4 bridges (up from 3)

Build 9.0 additions:
  T1.5: Priority queue dispatch (PriorityQueue, dynamic task injection,
         on_complete callback, drain_event for long-running dispatch)
  T3: run_resilient() for single calls with full resilience

Also exposes run_single() for one-off calls that don't need parallelism
but still benefit from the persistent bridge (no cold-start).

Thread safety:  All methods are safe to call from any thread.  The bridge
pool is shared; the RateGovernor serializes API calls to avoid quota issues.
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import PriorityQueue, Empty
from pathlib import Path

logger = logging.getLogger("alma.report_orch")


class ReportOrchestrator:
    """Bridge pool for parallelized report generation."""

    # ── 8.5 T3: Retry budgets per error category ──
    RETRY_BUDGET_STALL = 3
    RETRY_BUDGET_QUOTA = 6
    RETRY_BUDGET_OTHER = 1

    def __init__(self, db_path, model="gemini-2.5-flash", num_bridges=4,
                 min_rate_interval=25.0):
        """
        Args:
            db_path: Path to SQLite database (for usage tracking).
            model: Gemini model name.
            num_bridges: Number of persistent bridge subprocesses.
            min_rate_interval: Starting rate governor interval (seconds).
        """
        self.db_path = str(db_path)
        self._model = model
        self._num_bridges = num_bridges
        self._bridges = []
        self._booted = False
        self._cancelled = False
        self._lock = threading.Lock()
        self._adaptive_call_timeout = 300  # 8.5 T1: updated by canary probes

        from src.agents.rate_governor import RateGovernor
        self._rate_governor = RateGovernor(min_interval=min_rate_interval)

    # ═══════════════════════════════════════════════════════════════
    #  LIFECYCLE
    # ═══════════════════════════════════════════════════════════════

    def boot(self):
        """Start the bridge pool.  Idempotent."""
        if self._booted:
            return

        from src.agents.gemini_bridge_wrapper import GeminiBridge

        logger.info(
            "ReportOrchestrator: booting %d bridges (model=%s)",
            self._num_bridges, self._model,
        )

        for i in range(self._num_bridges):
            bridge = GeminiBridge(model=self._model)
            bridge.ensure_running()
            self._bridges.append(bridge)
            logger.info("ReportOrchestrator: bridge_%d ready", i)

        self._booted = True
        logger.info("ReportOrchestrator: all %d bridges ready", self._num_bridges)

        # 8.5 T1: Run canary probes to derive adaptive thresholds
        self._run_canary_probes()

    def _run_canary_probes(self):
        """8.5 T1: Fire a canary probe through each bridge and derive
        adaptive rate floor + call timeout from measured latencies.

        Probes are sequential to avoid rate-limit interference.
        Pattern from scan_orchestrator._run_canary_probes().
        """
        results = []
        for i, bridge in enumerate(self._bridges):
            probe = bridge.probe(timeout=30)
            if probe is None:
                probe = {"latency_ms": 0, "status": "error",
                         "error": "bridge_not_alive"}
            results.append(probe)
            logger.info(
                "ReportOrchestrator: canary probe bridge_%d — "
                "latency=%dms status=%s",
                i, probe.get("latency_ms", 0), probe.get("status"),
            )

        # Compute adaptive thresholds from successful probes
        latencies = sorted(
            r["latency_ms"] for r in results if r["status"] == "success"
        )
        n_ok = len(latencies)

        if n_ok == 0:
            logger.warning(
                "ReportOrchestrator: all canary probes failed — "
                "using default thresholds"
            )
            return

        p50 = latencies[len(latencies) // 2]
        p95_idx = min(int(len(latencies) * 0.95), max(len(latencies) - 1, 0))
        p95 = latencies[p95_idx]

        # Adaptive rate floor: P50 × 3.0, minimum 15s
        p50_seconds = p50 / 1000
        adaptive_floor = max(15.0, p50_seconds * 3.0)
        self._rate_governor.set_probe_floor(adaptive_floor)

        # Adaptive call timeout: P95 × multiplier × safety margin
        # For report tasks (single TRC per call), multiplier = 25
        adaptive_timeout = max(
            45,
            min(600, (p95 / 1000) * 25 * 1.5),
        )
        self._adaptive_call_timeout = round(adaptive_timeout)

        logger.info(
            "ReportOrchestrator: canary probes — %d/%d OK, "
            "P50=%dms P95=%dms, floor=%.1fs, timeout=%ds",
            n_ok, len(results), p50, p95,
            adaptive_floor, self._adaptive_call_timeout,
        )

    def shutdown(self):
        """Kill all bridges.  Safe to call multiple times."""
        for i, bridge in enumerate(self._bridges):
            try:
                bridge.kill()
                logger.info("ReportOrchestrator: bridge_%d killed", i)
            except Exception as e:
                logger.debug("ReportOrchestrator: bridge_%d kill error: %s", i, e)
        self._bridges.clear()
        self._booted = False
        logger.info("ReportOrchestrator: shutdown complete")

    def cancel(self):
        """Signal cancellation.  Workers check this flag between tasks."""
        self._cancelled = True

    # ═══════════════════════════════════════════════════════════════
    #  PARALLEL EXECUTION (8.5: queue-pull + retry + stall escalation)
    # ═══════════════════════════════════════════════════════════════

    # 9.0 T1.5: Sequence counter for PriorityQueue tie-breaking
    _seq_counter = 0
    _seq_lock = threading.Lock()

    def _next_seq(self):
        """Thread-safe sequence number for PriorityQueue tie-breaking."""
        with self._seq_lock:
            seq = self._seq_counter
            ReportOrchestrator._seq_counter += 1
            return seq

    def run_parallel(self, tasks, progress_cb=None, task_queue=None,
                     on_complete=None, drain_event=None):
        """Execute tasks across the bridge pool.

        9.0 T1.5: Upgraded to PriorityQueue with dynamic task injection.

        Args:
            tasks: List of dicts, each with:
                - "id":      Unique task identifier (e.g., TRC code)
                - "prompt":  Full prompt text (already redacted)
                - "timeout": Max seconds per call (default: adaptive timeout)
                - "priority": Optional int (default 0). Lower = higher priority.
            progress_cb: Optional callback(message, percent) for UI updates.
            task_queue: Optional external PriorityQueue for dynamic task injection.
                        Items: (priority, seq_num, task_dict, retry_count).
                        If None, internal queue created from `tasks`.
            on_complete: Optional lightweight callback(task_id, result) called
                         after each task completes. MUST be fast — runs in worker
                         thread. Only store results and set events; no heavy work.
            drain_event: Optional threading.Event. Workers stay alive until this
                         is set AND the queue is empty. If None, workers exit
                         when the queue is empty (backward compatible).

        Returns:
            dict mapping task["id"] → result_text (str) or error string.
        """
        if not self._booted:
            self.boot()

        # 9.0: per-invocation cancelled tracking (not self._cancelled reset)
        invocation_cancelled = [False]

        def is_cancelled():
            return self._cancelled or invocation_cancelled[0]

        results = {}
        total = len(tasks)  # For progress tracking (Phase 1 task count)
        completed_count = [0]
        completed_count_lock = threading.Lock()
        results_lock = threading.Lock()

        # 9.0 T1.5: PriorityQueue dispatch
        if task_queue is None:
            task_queue = PriorityQueue()
            for task in tasks:
                priority = task.get("priority", 0)
                task_queue.put((priority, self._next_seq(), task, 0))

        def _run_task(task, bridge_idx):
            """Execute a single task on the specified bridge.

            Returns:
                (task_id, response_text) — response starts with "[Error:"
                on failure, or "[Cancelled]" on cancellation.
            """
            if is_cancelled():
                return task["id"], "[Cancelled]"

            task_id = task["id"]
            prompt = task["prompt"]
            timeout = task.get("timeout", self._adaptive_call_timeout)
            request_id = f"rorch_{task_id}_{int(time.time())}"

            bridge = self._bridges[bridge_idx]

            try:
                # 8.5 T2: Pre-call health check
                if not bridge.is_alive():
                    logger.warning(
                        "ReportOrchestrator: bridge_%d dead pre-call, "
                        "restarting...", bridge_idx,
                    )
                    try:
                        bridge.restart()
                        time.sleep(3)
                    except Exception as re:
                        logger.error(
                            "ReportOrchestrator: bridge_%d restart failed: %s",
                            bridge_idx, re,
                        )
                        return task_id, "[Error: bridge_restart_failed]"

                # Rate governor: wait for slot
                if not self._rate_governor.acquire(timeout=60):
                    return task_id, "[Error: rate_governor_timeout]"

                if is_cancelled():
                    return task_id, "[Cancelled]"

                # Ensure bridge alive (may have died during acquire wait)
                bridge.ensure_running()

                # Call bridge
                t0 = time.time()
                response = bridge.call_blocking(
                    prompt, request_id, timeout=timeout
                )
                duration = time.time() - t0

                # Report success to rate governor + bridge health (8.5 T2)
                self._rate_governor.report_success(duration)
                bridge.record_success()

                logger.info(
                    "ReportOrchestrator: task=%s done (%.1fs, %d chars)",
                    task_id[:40], duration, len(response or ""),
                )

                return task_id, response

            except RuntimeError as e:
                err_str = str(e)

                # 8.5 T2: Track stalls for escalation
                if "stall_timeout" in err_str:
                    needs_restart = bridge.record_stall()
                    if needs_restart:
                        logger.warning(
                            "ReportOrchestrator: bridge_%d stall escalation "
                            "— auto-restarting", bridge_idx,
                        )
                        try:
                            bridge.restart()
                            time.sleep(3)
                        except Exception as re:
                            logger.error(
                                "ReportOrchestrator: bridge_%d escalation "
                                "restart failed: %s", bridge_idx, re,
                            )

                if "rate_limit" in err_str.lower() or "429" in err_str \
                        or "capacity" in err_str.lower():
                    self._rate_governor.report_rate_limit()
                else:
                    self._rate_governor.report_error()

                logger.error(
                    "ReportOrchestrator: task=%s failed: %s",
                    task_id[:40], err_str[:200],
                )
                return task_id, f"[Error: {err_str[:200]}]"

            except Exception as e:
                self._rate_governor.report_error()
                logger.error(
                    "ReportOrchestrator: task=%s exception: %s",
                    task_id[:40], e,
                )
                return task_id, f"[Error: {e}]"

        def _classify_retry_budget(err_str):
            """8.5 T3: Return max retries based on error category."""
            err_lower = err_str.lower()
            if "stall_timeout" in err_lower:
                return self.RETRY_BUDGET_STALL
            if "rate_limit" in err_lower or "429" in err_lower \
                    or "quota" in err_lower or "capacity" in err_lower:
                return self.RETRY_BUDGET_QUOTA
            return self.RETRY_BUDGET_OTHER

        def _worker_loop(bridge_idx):
            """9.0 T1.5: Priority queue-pull worker.

            Each worker owns one bridge and pulls tasks from the shared
            PriorityQueue.  Lower priority number = dispatched first.
            Supports drain_event for long-running dispatch with dynamic
            task injection.
            """
            while not is_cancelled():
                try:
                    priority, seq, task, retry_count = task_queue.get(timeout=2)
                except Empty:
                    # Queue empty — exit or keep waiting based on drain_event
                    if drain_event is None or drain_event.is_set():
                        break  # No more tasks coming — exit
                    continue  # Drain not signaled — more tasks may arrive

                task_id, result = _run_task(task, bridge_idx)

                # 8.5 T3: Retry budget — requeue on failure
                is_error = (isinstance(result, str)
                            and result.startswith("[Error:"))
                if is_error and not is_cancelled():
                    max_retries = _classify_retry_budget(result)
                    if retry_count < max_retries:
                        logger.info(
                            "ReportOrchestrator: requeueing task=%s "
                            "(retry %d/%d, priority=%d)",
                            task_id[:40], retry_count + 1, max_retries,
                            priority,
                        )
                        task_queue.put((priority, self._next_seq(),
                                        task, retry_count + 1))
                        continue  # Don't record as final result

                # Record final result (success or exhausted retries)
                with results_lock:
                    results[task_id] = result

                # 9.0 T1.5: Lightweight on_complete callback
                if on_complete:
                    try:
                        on_complete(task_id, result)
                    except Exception as cb_err:
                        logger.error(
                            "ReportOrchestrator: on_complete error for %s: %s",
                            task_id[:40], cb_err,
                        )

                with completed_count_lock:
                    completed_count[0] += 1

                if progress_cb and total > 0:
                    with completed_count_lock:
                        cnt = completed_count[0]
                    pct = int(cnt / total * 80)
                    try:
                        progress_cb(
                            f"Analyzed {cnt}/{total}: {task_id[:50]}",
                            pct,
                        )
                    except Exception:
                        pass

        # ── 9.0 T1.5: Dispatch one worker per bridge ──
        with ThreadPoolExecutor(max_workers=self._num_bridges) as executor:
            futures = [
                executor.submit(_worker_loop, i)
                for i in range(self._num_bridges)
            ]
            for future in as_completed(futures):
                try:
                    future.result()  # propagate exceptions
                except Exception as e:
                    logger.error(
                        "ReportOrchestrator: worker exception: %s", e,
                    )

        logger.info(
            "ReportOrchestrator: parallel run complete — "
            "%d/%d tasks, %d errors",
            sum(1 for v in results.values() if not str(v).startswith("[")),
            len(results),
            sum(1 for v in results.values() if str(v).startswith("[")),
        )

        return results

    # ═══════════════════════════════════════════════════════════════
    #  SINGLE CALL (convenience)
    # ═══════════════════════════════════════════════════════════════

    def run_single(self, prompt, request_id=None, timeout=300):
        """Single blocking call through bridge[0].

        Args:
            prompt: Full prompt text (already redacted).
            request_id: Optional custom request ID.
            timeout: Max seconds to wait.

        Returns:
            str: Full response text.

        Raises:
            RuntimeError: On bridge call failure.
        """
        if not self._booted:
            self.boot()

        bridge = self._bridges[0]
        bridge.ensure_running()

        rid = request_id or f"rorch_single_{int(time.time())}"
        return bridge.call_blocking(prompt, rid, timeout=timeout)

    # ═══════════════════════════════════════════════════════════════
    #  RESILIENT SINGLE CALL (9.0 T3)
    # ═══════════════════════════════════════════════════════════════

    def run_resilient(self, prompt, request_id=None, timeout=None):
        """Single call with full resilience (rate governor, retry budget,
        stall escalation).

        Routes through run_parallel([single_task]) so the call benefits from
        all 8.5 resilience machinery (retry budgets, stall escalation, bridge
        restart, rate governor feedback).

        Args:
            prompt: Full prompt text (already redacted).
            request_id: Optional custom request ID.
            timeout: Max seconds per call (default: adaptive timeout).

        Returns:
            str: Full response text.

        Raises:
            RuntimeError: If all retries exhausted or fatal error.
        """
        task_id = request_id or f"resilient_{int(time.time())}"
        task = {
            "id": task_id,
            "prompt": prompt,
            "timeout": timeout or self._adaptive_call_timeout,
        }
        results = self.run_parallel([task])
        result = results.get(task_id, "[Error: no result]")
        if isinstance(result, str) and result.startswith("[Error:"):
            raise RuntimeError(result)
        return result

    # ═══════════════════════════════════════════════════════════════
    #  STATS
    # ═══════════════════════════════════════════════════════════════

    def get_stats(self):
        """Return orchestrator health stats."""
        return {
            "booted": self._booted,
            "num_bridges": len(self._bridges),
            "bridges_alive": sum(1 for b in self._bridges if b.is_alive()),
            "cancelled": self._cancelled,
            "rate_interval": round(self._rate_governor.min_interval, 1),
            "throughput_per_min": round(self._rate_governor.get_throughput(), 1),
            "adaptive_timeout": self._adaptive_call_timeout,
        }
