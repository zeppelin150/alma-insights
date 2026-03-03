"""
Alma Insights -- Token Bucket Rate Governor (Pass 4.2 / 5.0)

Thread-safe rate limiter shared across all worker agents.
Starts conservative (25s between calls), learns the actual limit,
backs off on 429 errors, tightens on sustained success.

This is the component that makes time estimates precise:
by controlling input rate, output rate becomes predictable.
"""

import time
import threading
import logging

logger = logging.getLogger("alma.rate_governor")


class RateGovernor:
    """
    Token bucket rate limiter for Gemini API calls.

    Shared across all workers. Workers call acquire() before every
    API call; it blocks until the governed interval has elapsed.
    """

    def __init__(self, min_interval=25.0):
        self.lock = threading.Lock()
        self.min_interval = min_interval   # seconds between calls
        self.last_call_time = 0.0
        self.consecutive_successes = 0
        self.call_log = []                 # (timestamp, duration, success)
        self._probe_floor = 15.0           # 6.3: minimum from canary probes
        self._last_rate_limit_time = 0.0   # 8.5: tracks time since last 429

    def acquire(self, timeout=120):
        """
        Block until it's safe to make another API call.

        Returns True if acquired, False if timed out.
        Call this BEFORE every Gemini call.
        """
        deadline = time.time() + timeout

        while time.time() < deadline:
            with self.lock:
                elapsed = time.time() - self.last_call_time
                if elapsed >= self.min_interval:
                    self.last_call_time = time.time()
                    return True
                wait_time = self.min_interval - elapsed

            # Sleep in small increments so we don't overshoot
            time.sleep(min(wait_time, 1.0))

        logger.warning(
            f"RateGovernor: acquire timed out after {timeout}s "
            f"(interval={self.min_interval:.1f}s)"
        )
        return False

    def report_success(self, duration_seconds):
        """Call after a successful API response.

        8.5: Two-tier recovery —
          - At 5 successes: 10% tightening (counter keeps counting)
          - At 10 successes: 20-30% aggressive tightening (counter resets)
          - >120s since last rate limit: 30% aggressive at tier 2
          All clamped to probe-derived floor.
        """
        with self.lock:
            self.consecutive_successes += 1
            self.call_log.append((time.time(), duration_seconds, True))
            self._trim_log()

            now = time.time()
            time_since_rate_limit = now - self._last_rate_limit_time

            if self.consecutive_successes == 10:
                # 8.5 Tier 2: Aggressive recovery after sustained success
                if time_since_rate_limit > 120:
                    factor = 0.7   # 30% tightening — safe, no recent 429s
                else:
                    factor = 0.8   # 20% tightening
                old = self.min_interval
                self.min_interval = max(
                    self._probe_floor, self.min_interval * factor
                )
                self.consecutive_successes = 0  # Reset only at tier 2
                if old != self.min_interval:
                    logger.info(
                        f"RateGovernor: aggressive tightening "
                        f"{old:.1f}s -> {self.min_interval:.1f}s "
                        f"(factor={factor}, floor={self._probe_floor:.1f}s, "
                        f"since_429={time_since_rate_limit:.0f}s)"
                    )
            elif self.consecutive_successes == 5:
                # Tier 1: Standard 10% tightening — counter NOT reset
                old = self.min_interval
                self.min_interval = max(
                    self._probe_floor, self.min_interval * 0.9
                )
                # Note: don't reset consecutive_successes — let it count to 10
                if old != self.min_interval:
                    logger.info(
                        f"RateGovernor: tightened interval "
                        f"{old:.1f}s -> {self.min_interval:.1f}s "
                        f"(floor={self._probe_floor:.1f}s)"
                    )

    def report_rate_limit(self):
        """Call when a 429 or quota error is detected."""
        with self.lock:
            self.consecutive_successes = 0
            self._last_rate_limit_time = time.time()  # 8.5: track for recovery
            self.call_log.append((time.time(), 0, False))
            self._trim_log()

            old = self.min_interval
            self.min_interval = min(120.0, self.min_interval * 2.0)
            logger.warning(
                f"RateGovernor: rate limit hit, backed off "
                f"{old:.1f}s -> {self.min_interval:.1f}s"
            )

    def set_probe_floor(self, floor_seconds):
        """Set minimum interval floor from canary probe data (6.3).

        Prevents tightening below what probes measured as baseline latency.
        """
        with self.lock:
            self._probe_floor = max(15.0, floor_seconds)
        logger.info(
            "[HEALTH] rate governor probe floor set: %.1fs",
            self._probe_floor,
        )

    def report_error(self):
        """Call on non-rate-limit errors (don't change interval)."""
        with self.lock:
            self.consecutive_successes = 0
            self.call_log.append((time.time(), 0, False))
            self._trim_log()

    def get_throughput(self):
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

    def estimate_completion(self, remaining_batches):
        """
        Precise time estimate based on measured throughput + governed interval.

        Returns dict with:
          - seconds: estimated seconds remaining
          - confidence: 'high' | 'medium' | 'low'
          - interval: current governed interval
          - avg_call_duration: measured average call time
          - effective_interval: max(call_duration, governed_interval)
        """
        with self.lock:
            cutoff = time.time() - 600  # last 10 minutes
            successes = [c for c in self.call_log if c[2] and c[0] > cutoff]

            if not successes:
                return {
                    "seconds": remaining_batches * 90,
                    "confidence": "heuristic",
                    "interval": self.min_interval,
                    "avg_call_duration": 0,
                    "effective_interval": 90,
                }

            avg_duration = sum(c[1] for c in successes) / len(successes)
            effective_interval = max(avg_duration, self.min_interval)
            est_seconds = remaining_batches * effective_interval

            # Confidence from coefficient of variation
            if len(successes) >= 5:
                mean = avg_duration
                variance = sum(
                    (c[1] - mean) ** 2 for c in successes
                ) / len(successes)
                cv = (variance ** 0.5) / mean if mean > 0 else 1.0
                if cv < 0.15:
                    confidence = "high"
                elif cv < 0.35:
                    confidence = "medium"
                else:
                    confidence = "low"
            else:
                confidence = "low"

            return {
                "seconds": round(est_seconds, 1),
                "confidence": confidence,
                "interval": round(self.min_interval, 1),
                "avg_call_duration": round(avg_duration, 1),
                "effective_interval": round(effective_interval, 1),
            }

    def _trim_log(self):
        """Keep last 100 entries to bound memory."""
        if len(self.call_log) > 100:
            self.call_log = self.call_log[-100:]

    def __repr__(self):
        return (
            f"RateGovernor(interval={self.min_interval:.1f}s, "
            f"throughput={self.get_throughput():.1f}/min)"
        )
