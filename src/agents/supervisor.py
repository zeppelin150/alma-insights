"""
Alma Insights -- Supervisor (Pass 5.0)

Deterministic Python health monitor. No LLM. No latency.
All decisions are threshold-based.

The supervisor runs on the main thread (or its own thread) and:
  1. Polls worker health every POLL_INTERVAL seconds
  2. Detects degradation (low parse rate, low confidence, stalls)
  3. Restarts degraded workers
  4. Updates scan_progress table for UI polling
  5. Provides precise time estimates via RateGovernor
  6. Signals completion when all batches are done

Design philosophy: The supervisor is a "deterministic control plane"
-- no LLM calls, no ambiguity, no latency. Every decision is a
simple threshold comparison that completes in microseconds.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from src.data.connection_factory import get_connection

logger = logging.getLogger("alma.supervisor")


class Supervisor:
    """
    Deterministic health monitor for worker agents.

    Runs a poll loop checking worker health metrics against thresholds.
    All decisions are threshold-based -- no LLM involvement.

    Usage:
        supervisor = Supervisor(workers, rate_governor, db_path, scan_id)
        supervisor.run()      # blocks until scan complete or stopped
        supervisor.stop()     # signal graceful stop
    """

    # ── Thresholds ──
    POLL_INTERVAL = 5.0         # seconds between health checks
    PARSE_RATE_THRESHOLD = 0.50 # restart worker if below this
    CONFIDENCE_FLOOR = 0.15     # restart worker if below this
    STALL_TIMEOUT = 300         # seconds with no progress = stalled
    CONTEXT_TOKEN_LIMIT = 800_000  # trigger reset above this

    def __init__(self, workers, rate_governor, db_path, scan_id=None):
        """
        Args:
            workers: list[WorkerAgent] to monitor
            rate_governor: RateGovernor instance for time estimates
            db_path: Path to SQLite database
            scan_id: Current scan ID (set before run())
        """
        self.workers = workers
        self.rate_governor = rate_governor
        self.db_path = db_path
        self.scan_id = scan_id

        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # Scan state
        self._total_batches = 0
        self._completed_batches = 0
        self._total_tickets = 0
        self._classified_tickets = 0
        self._total_tokens_in = 0
        self._total_tokens_out = 0
        self._restarts = 0
        self._errors = []

        # F6: Tail-batch cutoff tracking
        self._batch_durations: list[float] = []
        self._last_completion_time = time.time()

        # DB connection (lazy)
        self._conn = None

    @property
    def conn(self):
        if self._conn is None:
            self._conn = get_connection(self.db_path)
        return self._conn

    def set_scan(self, scan_id, total_batches, total_tickets):
        """Configure for a new scan."""
        self.scan_id = scan_id
        self._total_batches = total_batches
        self._total_tickets = total_tickets
        self._completed_batches = 0
        self._classified_tickets = 0
        self._total_tokens_in = 0
        self._total_tokens_out = 0
        self._restarts = 0
        self._errors = []
        self._batch_durations = []
        self._last_completion_time = time.time()

    def run(self):
        """
        Main supervisor loop. Blocks until scan complete or stopped.

        Call this from the orchestrator thread after starting workers.
        """
        logger.info(
            f"Supervisor: starting for scan {self.scan_id} "
            f"({self._total_batches} batches, "
            f"{self._total_tickets} tickets, "
            f"{len(self.workers)} workers)"
        )

        while not self._stop_event.is_set():
            try:
                self._poll_cycle()
            except Exception as e:
                logger.error(f"Supervisor: poll error: {e}")
                self._errors.append(str(e))

            if self._all_complete():
                logger.info(
                    f"Supervisor: scan {self.scan_id} complete "
                    f"({self._classified_tickets}/{self._total_tickets} "
                    f"classified, {self._restarts} restarts)"
                )
                self._update_scan_progress()
                break

            # F6: Tail stall cutoff — if 95%+ done and remaining stalled
            if self.should_cutoff():
                remaining = self._total_batches - self._completed_batches
                logger.warning(
                    f"Supervisor: tail cutoff — {remaining} batch(es) "
                    f"stalled past 3x median, proceeding to post-scan"
                )
                self._update_scan_progress()
                break

            self._stop_event.wait(timeout=self.POLL_INTERVAL)

        # Close DB
        if self._conn:
            self._conn.close()
            self._conn = None

    def stop(self):
        """Signal the supervisor to stop."""
        self._stop_event.set()

    def notify_batch_complete(self, worker_id, result):
        """
        Called by the orchestrator when a batch finishes.

        Args:
            worker_id: ID of the worker that finished
            result: dict from WorkerAgent.classify_batch()
        """
        with self._lock:
            self._completed_batches += 1
            self._classified_tickets += result.get("classified", 0)
            self._total_tokens_in += result.get("input_tokens", 0)
            self._total_tokens_out += result.get("output_tokens", 0)

            # F6: Track batch durations for tail cutoff
            elapsed = result.get("elapsed_seconds", 0.0)
            if elapsed > 0:
                self._batch_durations.append(elapsed)
            self._last_completion_time = time.time()

            if result.get("error"):
                self._errors.append(
                    f"{worker_id}: {result['error']}"
                )

    # ──────────────────────────────────────────────────────────────────────
    # Poll cycle
    # ──────────────────────────────────────────────────────────────────────

    def _poll_cycle(self):
        """One health check cycle."""
        for worker in self.workers:
            health = worker.get_health()
            logger.debug(
                "[HEALTH] poll | agent=%s status=%s bridge_alive=%s "
                "stalls=%s deaths=%s parse=%.2f",
                health.get("agent_id", "?"),
                health.get("status", "?"),
                health.get("bridge_alive", "?"),
                health.get("bridge_consecutive_stalls", 0),
                health.get("bridge_deaths", 0),
                health.get("parse_rate", 0),
            )
            self._check_health(worker, health)
            self._write_agent_health(health)

        self._update_scan_progress()

    def _check_health(self, worker, health):
        """
        Check a single worker's health and take action if degraded.

        Threshold checks (all deterministic):
          - Bridge dead -> restart (6.2)
          - Bridge stall escalation -> restart bridge (6.2)
          - Parse rate < 70% -> restart
          - Avg confidence < 30% -> restart (only after 3+ batches)
          - Stall > 300s -> restart
          - Context tokens > 800K -> reset (not restart)
        """
        status = health.get("status", "idle")
        if status == "idle":
            return  # not active, skip checks

        agent_id = health.get("agent_id", "?")
        batches_done = health.get("batches_done", 0)

        # ── 6.2: Bridge-dead check (highest priority) ──
        bridge_alive = health.get("bridge_alive", True)
        if not bridge_alive:
            logger.warning(
                "[HEALTH] Supervisor: %s bridge DEAD — restarting worker",
                agent_id,
            )
            self._restart_worker(worker, "bridge_dead")
            return

        # ── 6.2: Bridge stall escalation check ──
        consecutive_stalls = health.get("bridge_consecutive_stalls", 0)
        if consecutive_stalls >= 3:
            logger.warning(
                "[HEALTH] Supervisor: %s has %d consecutive bridge stalls "
                "— restarting worker to clear stalled bridge",
                agent_id, consecutive_stalls,
            )
            self._restart_worker(worker, "stall_escalation")
            return

        # Parse rate check (only after 5+ batches for stable signal)
        parse_rate = health.get("parse_rate", 1.0)
        if batches_done >= 5 and parse_rate < self.PARSE_RATE_THRESHOLD:
            logger.warning(
                f"Supervisor: {agent_id} parse rate {parse_rate:.2f} "
                f"< {self.PARSE_RATE_THRESHOLD} -- restarting"
            )
            self._restart_worker(worker, "low_parse_rate")
            return

        # Confidence floor check (only after 5+ batches)
        avg_confidence = health.get("avg_confidence", 1.0)
        if batches_done >= 5 and avg_confidence < self.CONFIDENCE_FLOOR:
            logger.warning(
                f"Supervisor: {agent_id} avg confidence "
                f"{avg_confidence:.2f} < {self.CONFIDENCE_FLOOR} "
                f"-- restarting"
            )
            self._restart_worker(worker, "low_confidence")
            return

        # Stall detection
        last_progress = health.get("last_progress", 0)
        if status == "active" and last_progress > self.STALL_TIMEOUT:
            logger.warning(
                f"Supervisor: {agent_id} stalled for "
                f"{last_progress:.0f}s -- restarting"
            )
            self._restart_worker(worker, "stalled")
            return

        # Context overflow (reset, not restart)
        context_tokens = health.get("context_tokens", 0)
        if context_tokens > self.CONTEXT_TOKEN_LIMIT:
            logger.info(
                f"Supervisor: {agent_id} context {context_tokens} > "
                f"{self.CONTEXT_TOKEN_LIMIT} -- resetting"
            )
            try:
                worker.reset()
            except Exception as e:
                logger.error(
                    f"Supervisor: {agent_id} reset failed: {e}"
                )

    def _restart_worker(self, worker, reason):
        """Restart a degraded worker."""
        self._restarts += 1
        logger.debug(
            "[HEALTH] Supervisor restart | agent=%s reason=%s restarts=%d",
            worker.agent_id, reason, self._restarts,
        )
        try:
            worker.reset()
            logger.info(
                f"Supervisor: {worker.agent_id} restarted "
                f"(reason={reason}, total restarts={self._restarts})"
            )
        except Exception as e:
            logger.error(
                f"Supervisor: {worker.agent_id} restart failed: {e}"
            )
            self._errors.append(
                f"restart failed: {worker.agent_id}: {e}"
            )

    # ──────────────────────────────────────────────────────────────────────
    # DB writes
    # ──────────────────────────────────────────────────────────────────────

    def _write_agent_health(self, health):
        """Write worker health to agent_health table."""
        try:
            self.conn.execute("""
                INSERT INTO agent_health
                    (agent_id, scan_id, status, batches_done,
                     tickets_done, tool_calls, parse_rate,
                     avg_confidence, context_tokens,
                     last_progress, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    scan_id = excluded.scan_id,
                    status = excluded.status,
                    batches_done = excluded.batches_done,
                    tickets_done = excluded.tickets_done,
                    tool_calls = excluded.tool_calls,
                    parse_rate = excluded.parse_rate,
                    avg_confidence = excluded.avg_confidence,
                    context_tokens = excluded.context_tokens,
                    last_progress = excluded.last_progress,
                    updated_at = excluded.updated_at
            """, (
                health.get("agent_id", ""),
                self.scan_id,
                health.get("status", "idle"),
                health.get("batches_done", 0),
                health.get("tickets_done", 0),
                health.get("tool_calls", 0),
                health.get("parse_rate", 1.0),
                health.get("avg_confidence", 0.0),
                health.get("context_tokens", 0),
                str(health.get("last_progress", "")),
                datetime.utcnow().isoformat(),
            ))
            self.conn.commit()
        except Exception as e:
            logger.debug(f"Supervisor: agent_health write failed: {e}")

    def _update_scan_progress(self):
        """Write scan progress for UI polling."""
        remaining = self._total_batches - self._completed_batches
        estimate = self.rate_governor.estimate_completion(remaining)

        # ── 5.4b: Query actual DB count instead of in-memory accumulator ──
        # The in-memory _classified_tickets only accumulates from
        # notify_batch_complete() calls. When a batch streams classifications
        # via tool_call before its bridge fails, those rows exist in DB but
        # the retry reports only its own new classifications. The in-memory
        # counter falls behind. Querying the DB gives the true count.
        # ── NDJSON pivot fix: dedup-skipped batches report classified > 0
        # but don't insert rows under the current scan_id. Use max of DB
        # count and in-memory accumulator to handle both scenarios.
        classified_count = self._classified_tickets  # fallback
        in_flight_batches = 0
        try:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM nlp_ticket_classifications"
                " WHERE scan_id = ?",
                (self.scan_id,),
            ).fetchone()
            if row:
                classified_count = max(row["n"], self._classified_tickets)
            # In-flight batches (MCP tools persist immediately, so DB is
            # the authoritative source for real-time progress)
            row2 = self.conn.execute(
                "SELECT COUNT(*) AS n FROM nlp_batches"
                " WHERE scan_id = ? AND status = 'running'",
                (self.scan_id,),
            ).fetchone()
            if row2:
                in_flight_batches = row2["n"]
        except Exception as e:
            logger.debug(f"Supervisor: DB progress query failed: {e}")

        try:
            self.conn.execute("""
                INSERT INTO scan_progress
                    (scan_id, classified, total, tool_calls,
                     batches_complete, est_remaining_seconds,
                     est_confidence, updated_at,
                     tokens_in, tokens_out)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scan_id) DO UPDATE SET
                    classified = excluded.classified,
                    total = excluded.total,
                    tool_calls = excluded.tool_calls,
                    batches_complete = excluded.batches_complete,
                    est_remaining_seconds = excluded.est_remaining_seconds,
                    est_confidence = excluded.est_confidence,
                    updated_at = excluded.updated_at,
                    tokens_in = excluded.tokens_in,
                    tokens_out = excluded.tokens_out
            """, (
                self.scan_id,
                classified_count,
                self._total_tickets,
                sum(w.tools_called for w in self.workers),
                self._completed_batches,
                estimate.get("seconds", 0),
                estimate.get("confidence", "low"),
                datetime.utcnow().isoformat(),
                self._total_tokens_in,
                self._total_tokens_out,
            ))
            self.conn.commit()
        except Exception as e:
            logger.debug(f"Supervisor: scan_progress write failed: {e}")

    # ──────────────────────────────────────────────────────────────────────
    # Status
    # ──────────────────────────────────────────────────────────────────────

    def should_cutoff(self) -> bool:
        """F6: Check if remaining batches should be abandoned (tail stall).

        Returns True when 95%+ batches are done and no new completion
        has arrived within 3x the median batch duration (min 120s).
        """
        with self._lock:
            if self._total_batches == 0:
                return False

            completion_pct = self._completed_batches / self._total_batches
            if completion_pct < 0.95:
                return False

            if len(self._batch_durations) < 3:
                return False

            durations = sorted(self._batch_durations)
            median = durations[len(durations) // 2]
            cutoff_threshold = max(median * 3.0, 120.0)  # at least 2 min

            time_since_last = time.time() - self._last_completion_time
            return time_since_last > cutoff_threshold

    def _all_complete(self):
        """Check if all batches have been processed."""
        with self._lock:
            return (
                self._completed_batches >= self._total_batches
                and self._total_batches > 0
            )

    def get_time_estimate(self):
        """
        Get precise time estimate from RateGovernor.

        Returns dict with seconds, confidence, interval, etc.
        """
        remaining = self._total_batches - self._completed_batches
        return self.rate_governor.estimate_completion(remaining)

    def get_status(self):
        """Return current supervisor status dict."""
        estimate = self.get_time_estimate()

        return {
            "scan_id": self.scan_id,
            "total_batches": self._total_batches,
            "completed_batches": self._completed_batches,
            "total_tickets": self._total_tickets,
            "classified_tickets": self._classified_tickets,
            "restarts": self._restarts,
            "errors": self._errors[-10:],  # last 10 errors
            "est_remaining_seconds": estimate.get("seconds", 0),
            "est_confidence": estimate.get("confidence", "low"),
            "workers": [w.get_health() for w in self.workers],
        }

    def __repr__(self):
        return (
            f"Supervisor(scan={self.scan_id}, "
            f"batches={self._completed_batches}/{self._total_batches}, "
            f"tickets={self._classified_tickets}/{self._total_tickets}, "
            f"restarts={self._restarts})"
        )
