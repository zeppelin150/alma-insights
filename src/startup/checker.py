"""
Alma Insights — Startup Check Framework

A `Check` is a zero-argument callable returning a `CheckResult`. The
`Checker` runs a list of checks sequentially, streaming each result
through a `on_result` callback so the splash UI can render rows as
they complete.

Design tenets:
  * Pure data — CheckResult carries everything the UI needs.
  * Sequential, not parallel — checks can depend on env vars set by
    earlier checks, and the UI reads more naturally top-to-bottom.
  * Never raise — any exception raised by a check is captured and
    converted into a CheckResult with status="fail".
  * Timeable — each check records its wall-clock duration so slow
    checks are visible in crash reports.

Public API:
    CheckResult           dataclass
    CheckStatus           Literal type alias
    Checker               orchestrator
    run_blocking(checks)  convenience helper for tests
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, Literal

CheckStatus = Literal["pass", "warn", "fail"]

CheckFn = Callable[[], "CheckResult"]
AutoFixFn = Callable[[], "CheckResult"]
ResultCallback = Callable[["CheckResult"], None]


@dataclass
class CheckResult:
    """The outcome of a single startup check."""

    id: str                               # stable identifier
    name: str                             # display label
    status: CheckStatus                   # pass / warn / fail
    message: str = ""                     # one-line status detail
    remediation: str = ""                 # user-facing fix instruction
    critical: bool = False                # if True + fail → block Continue
    can_auto_fix: bool = False            # hint the UI to show a "Fix" button
    auto_fix: AutoFixFn | None = field(   # re-run callable for auto-fix
        default=None, repr=False
    )
    duration_ms: int = 0                  # wall-clock time for diagnostics


# ──────────────────────────────────────────────────────────────────
# Orchestrator
# ──────────────────────────────────────────────────────────────────

class Checker:
    """
    Run an ordered list of checks and stream results to an optional
    callback. Collects all results for post-hoc inspection.
    """

    def __init__(self, checks: list[tuple[str, CheckFn]]):
        """
        Parameters
        ----------
        checks
            List of (id, callable) pairs. The `id` is used only if the
            check raises before producing its own CheckResult.
        """
        self._checks = list(checks)
        self._results: list[CheckResult] = []

    # Accessors -----------------------------------------------------

    @property
    def results(self) -> list[CheckResult]:
        return list(self._results)

    @property
    def passed_critical(self) -> bool:
        """True iff every critical check has status == 'pass'."""
        return all(
            r.status == "pass" for r in self._results if r.critical
        )

    @property
    def failed_check_ids(self) -> list[str]:
        """IDs of checks whose status is not 'pass'. Used by the launcher
        to decide post-splash routing (e.g. open Settings when OAuth failed)."""
        return [r.id for r in self._results if r.status != "pass"]

    def summary(self) -> dict[str, int]:
        """Bucket counts: {'pass': n, 'warn': n, 'fail': n}."""
        buckets = {"pass": 0, "warn": 0, "fail": 0}
        for r in self._results:
            buckets[r.status] += 1
        return buckets

    # Execution -----------------------------------------------------

    def run_next(self, on_result: ResultCallback | None = None) -> CheckResult | None:
        """
        Run the next queued check. Returns the result, or None if the
        queue is empty. Safe to call repeatedly from a UI timer.
        """
        if not self._checks:
            return None
        check_id, check_fn = self._checks.pop(0)
        result = _run_one(check_id, check_fn)
        self._results.append(result)
        if on_result is not None:
            on_result(result)
        return result

    def run_all(self, on_result: ResultCallback | None = None) -> list[CheckResult]:
        """Synchronous drain — useful for tests and the --no-splash path."""
        while self._checks:
            self.run_next(on_result)
        return self.results


# ──────────────────────────────────────────────────────────────────
# Internals
# ──────────────────────────────────────────────────────────────────

def _run_one(check_id: str, check_fn: CheckFn) -> CheckResult:
    """Invoke one check with timing + exception trapping."""
    start = time.monotonic()
    try:
        result = check_fn()
    except Exception as exc:  # noqa: BLE001 — must not propagate
        tb = traceback.format_exc()
        return CheckResult(
            id=check_id,
            name=check_id.replace("_", " ").title(),
            status="fail",
            message=f"Check raised {type(exc).__name__}: {exc}",
            remediation=(
                "This is a bug in the check itself. Export a crash "
                "report from Settings → Support."
            ),
            critical=True,
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    result.duration_ms = int((time.monotonic() - start) * 1000)
    return result


# ──────────────────────────────────────────────────────────────────
# Convenience
# ──────────────────────────────────────────────────────────────────

def run_blocking(checks: list[tuple[str, CheckFn]]) -> list[CheckResult]:
    """Shorthand: run all checks and return the results list."""
    return Checker(checks).run_all()
