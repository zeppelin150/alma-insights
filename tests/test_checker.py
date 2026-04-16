"""
Unit tests for src/startup/checker.py

Covers:
  - CheckResult dataclass
  - Checker orchestrator: run_next / run_all
  - on_result callback fires per check
  - Exception in a check becomes a fail-result (does not propagate)
  - passed_critical + summary aggregation
  - duration_ms is populated

Run: python -m pytest tests/test_checker.py -x -v
"""

from __future__ import annotations

import time

import pytest

from src.startup.checker import CheckResult, Checker, run_blocking


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────

def _passing(name: str, critical: bool = False) -> CheckResult:
    return CheckResult(id=name, name=name, status="pass", critical=critical)


def _failing(name: str, critical: bool = True) -> CheckResult:
    return CheckResult(
        id=name, name=name, status="fail",
        message="nope", remediation="fix it", critical=critical,
    )


def _warning(name: str) -> CheckResult:
    return CheckResult(id=name, name=name, status="warn", message="hmm")


# ──────────────────────────────────────────────────────────────────
# run_next / run_all
# ──────────────────────────────────────────────────────────────────

class TestOrchestrator:
    def test_run_next_pops_one_at_a_time(self):
        c = Checker([
            ("a", lambda: _passing("a")),
            ("b", lambda: _passing("b")),
        ])
        first = c.run_next()
        assert first.id == "a"
        assert len(c.results) == 1
        second = c.run_next()
        assert second.id == "b"
        assert c.run_next() is None  # queue drained

    def test_run_all_drains_queue(self):
        c = Checker([(str(i), lambda i=i: _passing(str(i))) for i in range(4)])
        results = c.run_all()
        assert [r.id for r in results] == ["0", "1", "2", "3"]

    def test_on_result_callback_fires_per_check(self):
        calls: list[str] = []
        c = Checker([
            ("a", lambda: _passing("a")),
            ("b", lambda: _passing("b")),
        ])
        c.run_all(on_result=lambda r: calls.append(r.id))
        assert calls == ["a", "b"]


# ──────────────────────────────────────────────────────────────────
# Exception handling
# ──────────────────────────────────────────────────────────────────

class TestExceptionTrap:
    def test_raising_check_becomes_fail_result(self):
        def boom():
            raise RuntimeError("kablooey")

        c = Checker([("boom_check", boom)])
        results = c.run_all()
        assert len(results) == 1
        assert results[0].status == "fail"
        assert results[0].critical is True
        assert "RuntimeError" in results[0].message
        assert "kablooey" in results[0].message

    def test_one_raising_check_does_not_stop_others(self):
        c = Checker([
            ("ok_a", lambda: _passing("ok_a")),
            ("boom", lambda: (_ for _ in ()).throw(ValueError("x"))),
            ("ok_c", lambda: _passing("ok_c")),
        ])
        results = c.run_all()
        assert [r.id for r in results] == ["ok_a", "boom", "ok_c"]
        assert results[1].status == "fail"


# ──────────────────────────────────────────────────────────────────
# Aggregation
# ──────────────────────────────────────────────────────────────────

class TestAggregation:
    def test_summary_buckets(self):
        c = Checker([
            ("a", lambda: _passing("a")),
            ("b", lambda: _warning("b")),
            ("c", lambda: _failing("c", critical=False)),
            ("d", lambda: _passing("d")),
        ])
        c.run_all()
        assert c.summary() == {"pass": 2, "warn": 1, "fail": 1}

    def test_passed_critical_all_pass(self):
        c = Checker([
            ("a", lambda: _passing("a", critical=True)),
            ("b", lambda: _passing("b", critical=True)),
        ])
        c.run_all()
        assert c.passed_critical is True

    def test_passed_critical_non_critical_fail_ignored(self):
        c = Checker([
            ("a", lambda: _passing("a", critical=True)),
            ("b", lambda: _failing("b", critical=False)),
        ])
        c.run_all()
        assert c.passed_critical is True  # b wasn't critical

    def test_passed_critical_critical_fail_blocks(self):
        c = Checker([
            ("a", lambda: _passing("a", critical=True)),
            ("b", lambda: _failing("b", critical=True)),
        ])
        c.run_all()
        assert c.passed_critical is False


# ──────────────────────────────────────────────────────────────────
# Timing
# ──────────────────────────────────────────────────────────────────

class TestTiming:
    def test_duration_ms_populated(self):
        def slow():
            time.sleep(0.02)
            return _passing("slow")
        c = Checker([("slow", slow)])
        c.run_all()
        assert c.results[0].duration_ms >= 15

    def test_fast_check_has_small_duration(self):
        c = Checker([("fast", lambda: _passing("fast"))])
        c.run_all()
        assert c.results[0].duration_ms < 50  # generous on shared CI


# ──────────────────────────────────────────────────────────────────
# run_blocking convenience
# ──────────────────────────────────────────────────────────────────

class TestBlocking:
    def test_run_blocking_returns_results_list(self):
        results = run_blocking([
            ("a", lambda: _passing("a")),
            ("b", lambda: _warning("b")),
        ])
        assert [r.id for r in results] == ["a", "b"]
