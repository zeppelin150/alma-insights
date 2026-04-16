"""
Alma Insights -- Semaphore Rate Governor (Build 12.0)

Thread-safe concurrent dispatch governor shared across all worker agents.
Replaces the serial token-bucket with a counting semaphore + burst gate.

Architecture:
  - Counting semaphore: allows N concurrent in-flight API calls
  - Burst gate: minimum delay between consecutive dispatches (anti-slam)
  - Dynamic resizing: shrink permits on 429, grow back on sustained success
  - Health monitoring: call_log, throughput, time estimates (unchanged API)

Workers call acquire() before each API call and release() after.
The try/finally pattern guarantees permits are returned even on crash.
"""

from __future__ import annotations

import time
import threading
import logging

logger = logging.getLogger("alma.rate_governor")


class SemaphoreRateGovernor:
    """
    Concurrent dispatch governor for Gemini API calls.

    Shared across all workers. Workers call acquire() before every
    API call and release() after completion.

    Supports priority="chat" for instant bypass (absorbs
    PriorityRateGovernor behavior).
    """

    def __init__(
        self,
        max_concurrent: int = 8,
        burst_delay: float = 0.5,
        min_concurrent: int = 1,
        # Legacy compat: accept min_interval kwarg and ignore
        min_interval: float | None = None,
    ) -> None:
        # Concurrency control
        self._max_concurrent = max_concurrent
        self._current_concurrent = max_concurrent
        self._min_concurrent = max(1, min_concurrent)
        self._semaphore = threading.Semaphore(max_concurrent)
        self._available_permits = max_concurrent

        # Burst protection
        self._burst_delay = burst_delay
        self._configured_burst_delay = burst_delay
        self._dispatch_lock = threading.Lock()
        self._last_dispatch_time = 0.0

        # In-flight tracking
        self._in_flight = 0
        self._in_flight_lock = threading.Lock()

        # Health monitoring (same state as legacy RateGovernor)
        self.lock = threading.Lock()  # protects health state
        self.consecutive_successes = 0
        self.call_log: list[tuple[float, float, bool]] = []
        self._probe_floor = 0.3       # minimum burst_delay from probes
        self._last_rate_limit_time = 0.0

        # Diagnostic counters (F1 instrumentation)
        self._burst_wait_total = 0.0
        self._semaphore_wait_total = 0.0
        self._acquire_count = 0

    # ── Backward-compat property ──────────────────────────────────────

    @property
    def min_interval(self) -> float:
        """Legacy compat: returns burst_delay as serial-equivalent interval."""
        return self._burst_delay

    @min_interval.setter
    def min_interval(self, value: float) -> None:
        """Legacy compat: setting min_interval adjusts burst_delay."""
        self._burst_delay = value

    # ── Core API ──────────────────────────────────────────────────────

    def acquire(self, timeout: float = 120, priority: str = "normal") -> bool:
        """Acquire a dispatch slot.

        1. priority="chat" → bypass immediately
        2. Burst gate → small delay between consecutive dispatches
        3. Semaphore gate → blocks if all permits are in-flight

        Returns True if acquired (caller MUST call release()),
        False if timed out.
        """
        if priority == "chat":
            with self.lock:
                self.call_log.append((time.time(), 0, True))
                self._trim_log()
            return True

        deadline = time.time() + timeout

        # Step 1: Burst gate — P2: check-then-lock (don't hold lock while sleeping)
        burst_start = time.time()
        while True:
            now = time.time()
            if now >= deadline:
                logger.warning(
                    "RateGovernor: acquire timed out in burst gate "
                    "(burst_delay=%.1fs)", self._burst_delay,
                )
                return False

            wait = self._burst_delay - (now - self._last_dispatch_time)
            if wait <= 0:
                # Try to claim this dispatch slot
                with self._dispatch_lock:
                    # Re-check under lock (another worker may have claimed it)
                    if (time.time() - self._last_dispatch_time
                            >= self._burst_delay):
                        self._last_dispatch_time = time.time()
                        break  # claimed
                # Lost the race, short yield and retry
                time.sleep(0.02)
            else:
                # Wait in small increments without holding the lock
                time.sleep(min(wait, 0.1))
        self._burst_wait_total += time.time() - burst_start

        # Step 2: Semaphore gate — block if all permits taken
        sem_start = time.time()
        remaining_timeout = deadline - time.time()
        if remaining_timeout <= 0:
            logger.warning("RateGovernor: acquire timed out before semaphore")
            return False

        acquired = self._semaphore.acquire(timeout=remaining_timeout)
        if not acquired:
            logger.warning(
                "RateGovernor: semaphore acquire timed out "
                "(concurrent=%d/%d, in_flight=%d)",
                self._current_concurrent, self._max_concurrent,
                self._in_flight,
            )
            return False
        self._semaphore_wait_total += time.time() - sem_start

        # Step 3: Track in-flight
        with self._in_flight_lock:
            self._in_flight += 1
            self._available_permits -= 1

        self._acquire_count += 1
        return True

    def release(
        self,
        duration: float | None = None,
        success: bool = True,
        rate_limited: bool = False,
    ) -> None:
        """Release a dispatch slot after batch completion.

        Must be called exactly once for every successful acquire().
        Use try/finally to guarantee this.

        Args:
            duration: Call duration in seconds (None if not measured).
            success: Whether the call succeeded.
            rate_limited: Whether a 429/quota error occurred.
        """
        # Step 1: Decrement in-flight and conditionally return permit
        with self._in_flight_lock:
            self._in_flight = max(0, self._in_flight - 1)
            # Only return permit if we're below target concurrency.
            # If _current_concurrent was shrunk (429), excess permits
            # are consumed here — they simply don't get returned.
            if self._available_permits < self._current_concurrent:
                self._semaphore.release()
                self._available_permits += 1
            # else: permit consumed by dynamic shrink — don't return

        # Step 2: Update health metrics
        with self.lock:
            ts = time.time()
            dur = duration if duration is not None else 0.0
            self.call_log.append((ts, dur, success))
            self._trim_log()

        # Step 3: Route to health handlers
        if rate_limited:
            self._handle_rate_limit()
        elif success:
            if duration is not None:
                self._handle_success(duration)
        else:
            self._handle_error()

    # ── Health handlers (dynamic resizing) ────────────────────────────

    def _handle_success(self, duration: float) -> None:
        """On success: tighten burst_delay and grow permits.

        P1: Time-based fast recovery alongside consecutive-success tiers.
        After 30s clean: restore 50% of lost concurrency.
        After 60s clean: restore to max.
        Burst delay decay accelerated when > 30s since last 429.
        """
        with self.lock:
            self.consecutive_successes += 1
            now = time.time()
            time_since_429 = now - self._last_rate_limit_time

            # ── P1: Time-based fast recovery ──
            if (time_since_429 > 30 and
                    self._current_concurrent < self._max_concurrent):
                lost = self._max_concurrent - self._current_concurrent
                if time_since_429 > 60:
                    restore = lost  # full recovery after 60s clean
                else:
                    restore = max(1, lost // 2)  # 50% after 30s
                old = self._current_concurrent
                self._current_concurrent = min(
                    self._max_concurrent,
                    self._current_concurrent + restore,
                )
                added = self._current_concurrent - old
                for _ in range(added):
                    self._semaphore.release()
                    with self._in_flight_lock:
                        self._available_permits += 1
                if added > 0:
                    logger.info(
                        "RateGovernor: time-based recovery %d -> %d "
                        "(since_429=%.0fs)",
                        old, self._current_concurrent, time_since_429,
                    )

            # Burst delay decay factor: faster when safe
            factor = 0.8 if time_since_429 > 30 else 0.9

            if self.consecutive_successes == 10:
                # Tier 2: aggressive recovery
                # Grow concurrency if below max (additive to time-based)
                if self._current_concurrent < self._max_concurrent:
                    grow = 2 if time_since_429 > 120 else 1
                    old = self._current_concurrent
                    self._current_concurrent = min(
                        self._max_concurrent,
                        self._current_concurrent + grow,
                    )
                    added = self._current_concurrent - old
                    for _ in range(added):
                        self._semaphore.release()
                        with self._in_flight_lock:
                            self._available_permits += 1
                    if added > 0:
                        logger.info(
                            "RateGovernor: grew concurrency %d -> %d "
                            "(since_429=%.0fs)",
                            old, self._current_concurrent, time_since_429,
                        )

                # Tighten burst_delay
                old_delay = self._burst_delay
                self._burst_delay = max(
                    self._configured_burst_delay,
                    self._burst_delay * factor,
                )
                self.consecutive_successes = 0
                if old_delay != self._burst_delay:
                    logger.info(
                        "RateGovernor: tightened burst_delay "
                        "%.2fs -> %.2fs",
                        old_delay, self._burst_delay,
                    )

            elif self.consecutive_successes == 5:
                # Tier 1: standard tightening
                old_delay = self._burst_delay
                self._burst_delay = max(
                    self._configured_burst_delay,
                    self._burst_delay * factor,
                )
                if old_delay != self._burst_delay:
                    logger.info(
                        "RateGovernor: tier1 tightened burst_delay "
                        "%.2fs -> %.2fs",
                        old_delay, self._burst_delay,
                    )

    def _handle_rate_limit(self) -> None:
        """On 429: shrink concurrency and increase burst_delay."""
        with self.lock:
            self.consecutive_successes = 0
            self._last_rate_limit_time = time.time()

            # Shrink concurrency
            old_conc = self._current_concurrent
            self._current_concurrent = max(
                self._min_concurrent,
                self._current_concurrent // 2,
            )
            # Try to drain excess permits immediately
            excess = old_conc - self._current_concurrent
            drained = 0
            for _ in range(excess):
                got = self._semaphore.acquire(blocking=False)
                if got:
                    with self._in_flight_lock:
                        self._available_permits -= 1
                    drained += 1

            # Increase burst_delay
            old_delay = self._burst_delay
            self._burst_delay = min(5.0, self._burst_delay * 1.5)

            logger.warning(
                "RateGovernor: 429 — concurrency %d -> %d "
                "(drained %d permits), burst_delay %.2fs -> %.2fs",
                old_conc, self._current_concurrent, drained,
                old_delay, self._burst_delay,
            )

    def _handle_error(self) -> None:
        """On non-rate-limit error: reset success counter only."""
        with self.lock:
            self.consecutive_successes = 0

    # ── Legacy compat wrappers ────────────────────────────────────────
    # These update metrics without touching the semaphore.
    # Use release() instead in new code.

    def report_success(self, duration_seconds: float) -> None:
        """Legacy compat: update metrics only (no semaphore)."""
        with self.lock:
            self.call_log.append((time.time(), duration_seconds, True))
            self._trim_log()
        self._handle_success(duration_seconds)

    def report_rate_limit(self) -> None:
        """Legacy compat: update metrics only (no semaphore)."""
        with self.lock:
            self.call_log.append((time.time(), 0, False))
            self._trim_log()
        self._handle_rate_limit()

    def report_error(self) -> None:
        """Legacy compat: update metrics only (no semaphore)."""
        with self.lock:
            self.call_log.append((time.time(), 0, False))
            self._trim_log()
        self._handle_error()

    # ── Probe floor ───────────────────────────────────────────────────

    def set_probe_floor(self, floor_seconds: float) -> None:
        """Set minimum burst_delay from canary probe data.

        Translates probe latency (per-call) to burst_delay floor.
        Capped at 2x configured burst_delay to prevent inflated
        probes (e.g. rate-limited retries) from throttling dispatch.
        """
        with self.lock:
            raw_floor = max(0.3, floor_seconds / 30.0)
            # Cap: never let probe inflate floor beyond 2x configured delay
            max_floor = self._configured_burst_delay * 2.0
            self._probe_floor = min(raw_floor, max(0.3, max_floor))
            # Don't let probe raise configured_burst_delay — only floor it
            self._burst_delay = max(self._burst_delay, self._probe_floor)
        logger.info(
            "[HEALTH] rate governor probe floor set: %.2fs "
            "(from latency %.1fms, capped at %.2fs)",
            self._probe_floor, floor_seconds, max_floor,
        )

    # ── Metrics ───────────────────────────────────────────────────────

    def get_throughput(self) -> float:
        """Measured calls per minute over the last 5 minutes."""
        with self.lock:
            cutoff = time.time() - 300
            recent = [c for c in self.call_log if c[0] > cutoff]
            if len(recent) < 2:
                return 0.0
            window = recent[-1][0] - recent[0][0]
            if window == 0:
                return 0.0
            return (len(recent) - 1) / (window / 60.0)

    def estimate_completion(self, remaining_batches: int) -> dict:
        """Time estimate using concurrent throughput model.

        Formula: remaining / min(dispatch_rate, concurrency_rate)
        """
        with self.lock:
            cutoff = time.time() - 600
            successes = [c for c in self.call_log if c[2] and c[0] > cutoff]

            if not successes:
                return {
                    "seconds": remaining_batches * 90,
                    "confidence": "heuristic",
                    "interval": self._burst_delay,
                    "avg_call_duration": 0,
                    "effective_interval": 90,
                }

            avg_duration = sum(c[1] for c in successes) / len(successes)

            # Concurrent throughput: min of dispatch rate and concurrency rate
            dispatch_rate = 1.0 / max(self._burst_delay, 0.1)
            if avg_duration > 0:
                conc_rate = self._current_concurrent / avg_duration
            else:
                conc_rate = dispatch_rate
            effective_rate = min(dispatch_rate, conc_rate)

            if effective_rate > 0:
                est_seconds = remaining_batches / effective_rate
            else:
                est_seconds = remaining_batches * 90

            # Confidence from coefficient of variation
            if len(successes) >= 5:
                mean = avg_duration
                variance = sum(
                    (c[1] - mean) ** 2 for c in successes
                ) / len(successes)
                cv = (variance ** 0.5) / mean if mean > 0 else 1.0
                confidence = (
                    "high" if cv < 0.15 else
                    "medium" if cv < 0.35 else
                    "low"
                )
            else:
                confidence = "low"

            return {
                "seconds": round(est_seconds, 1),
                "confidence": confidence,
                "interval": round(self._burst_delay, 2),
                "avg_call_duration": round(avg_duration, 1),
                "effective_interval": round(
                    1.0 / effective_rate if effective_rate > 0 else 90, 1
                ),
                "concurrency": self._current_concurrent,
                "in_flight": self._in_flight,
            }

    def _trim_log(self):
        """Keep last 100 entries to bound memory."""
        if len(self.call_log) > 100:
            self.call_log = self.call_log[-100:]

    def __repr__(self):
        return (
            f"SemaphoreRateGovernor("
            f"concurrent={self._current_concurrent}/{self._max_concurrent}, "
            f"in_flight={self._in_flight}, "
            f"burst_delay={self._burst_delay:.2f}s, "
            f"throughput={self.get_throughput():.1f}/min)"
        )


# ── Aliases for backward compatibility ────────────────────────────────

RateGovernor = SemaphoreRateGovernor

# Legacy token-bucket governor preserved for reference / rollback.
# Import as: from src.agents.rate_governor import LegacyRateGovernor
LegacyRateGovernor = None  # removed; see git history if needed


class PriorityRateGovernor(SemaphoreRateGovernor):
    """Backward-compatible alias.

    Chat priority bypass is now handled natively in
    SemaphoreRateGovernor.acquire(priority="chat").
    """
    pass
