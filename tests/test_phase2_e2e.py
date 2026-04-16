"""
Phase 2 E2E — wire all nine checks into the splash with a real Checker
and verify UX gating.

Uses a real QApplication with processEvents to drive the QTimer queue.
No Gemini CLI, no keyring backend, no real DB — external deps are
patched via the check functions themselves (checks live in their own
module and we inject stubs at the DEFAULT_CHECKS level).

Run: python -m pytest tests/test_phase2_e2e.py -x -v
"""

from __future__ import annotations

import time

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from src.startup.checker import CheckResult, Checker
from src.startup.splash_row import SplashRow
from src.startup.splash_window import SplashWindow


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def _pump(app, deadline=3.0, until=None):
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        app.processEvents()
        if until and until():
            return
        time.sleep(0.01)


def _result(name, status, critical=False, remediation=""):
    return CheckResult(
        id=name, name=name, status=status,
        message=f"{name} says {status}",
        remediation=remediation,
        critical=critical,
    )


def _mk_check(name, status, critical=False, remediation=""):
    return (name, lambda: _result(name, status, critical, remediation))


# ──────────────────────────────────────────────────────────────────
# All-pass happy path
# ──────────────────────────────────────────────────────────────────

class TestHappyPath:
    def test_all_critical_pass_enables_continue(self, qapp):
        checks = [
            _mk_check("environment", "pass", critical=True),
            _mk_check("credentials", "pass", critical=True),
            _mk_check("gemini_oauth", "pass", critical=True),
            _mk_check("update", "pass", critical=False),
            _mk_check("env_guard", "pass", critical=True),
            _mk_check("hardware", "pass", critical=False),
            _mk_check("embedding_model", "pass", critical=False),
            _mk_check("database", "pass", critical=True),
            _mk_check("config", "pass", critical=False),
        ]
        checker = Checker(checks)
        win = SplashWindow(app_version="v9.3.0-e2e")
        win.show()
        win.run(checker, step_delay_ms=5)

        _pump(qapp, until=lambda: win._continue_btn.isEnabled())

        assert win._continue_btn.isEnabled()
        assert len(win.findChildren(SplashRow)) == 9
        assert checker.passed_critical is True
        win.close()


# ──────────────────────────────────────────────────────────────────
# Warnings on non-critical still allow Continue
# ──────────────────────────────────────────────────────────────────

class TestWarningsContinue:
    def test_model_missing_warns_but_unblocks(self, qapp):
        """Mirror the mock — ML model missing is a warn, user continues."""
        checks = [
            _mk_check("environment", "pass", critical=True),
            _mk_check("credentials", "pass", critical=True),
            _mk_check("gemini_oauth", "pass", critical=True),
            _mk_check("update", "warn", remediation="offline mode"),
            _mk_check("env_guard", "pass", critical=True),
            _mk_check("hardware", "pass"),
            _mk_check("embedding_model", "fail", critical=False,
                       remediation="all-MiniLM-L6-v2 not found"),
            _mk_check("database", "pass", critical=True),
            _mk_check("config", "pass"),
        ]
        checker = Checker(checks)
        win = SplashWindow(app_version="v9.2.0")
        win.show()
        win.run(checker, step_delay_ms=5)

        _pump(qapp, until=lambda: win._continue_btn.isEnabled())

        assert win._continue_btn.isEnabled()
        assert checker.summary()["warn"] == 1
        assert checker.summary()["fail"] == 1
        assert checker.passed_critical is True  # fail is non-critical
        assert "warning" in win._summary_label.text().lower()
        assert "non-critical" in win._summary_label.text().lower()
        win.close()


# ──────────────────────────────────────────────────────────────────
# Critical failures block continue
# ──────────────────────────────────────────────────────────────────

class TestCriticalFailure:
    def test_keyring_failure_blocks_continue(self, qapp):
        checks = [
            _mk_check("environment", "pass", critical=True),
            _mk_check("credentials", "fail", critical=True,
                       remediation="keyring unreachable"),
            _mk_check("gemini_oauth", "pass", critical=True),
            _mk_check("update", "pass"),
            _mk_check("env_guard", "pass", critical=True),
            _mk_check("hardware", "pass"),
            _mk_check("embedding_model", "pass"),
            _mk_check("database", "pass", critical=True),
            _mk_check("config", "pass"),
        ]
        checker = Checker(checks)
        win = SplashWindow()
        win.show()
        win.run(checker, step_delay_ms=5)

        _pump(qapp, until=lambda: not checker._checks)
        _pump(qapp, deadline=0.3)

        assert not win._continue_btn.isEnabled()
        assert "Cannot continue" in win._summary_label.text()
        win.close()


# ──────────────────────────────────────────────────────────────────
# A raising check is captured and doesn't crash the splash
# ──────────────────────────────────────────────────────────────────

class TestExceptionResilience:
    def test_raising_check_still_renders_row(self, qapp):
        def raiser():
            raise RuntimeError("boom")

        checks = [
            _mk_check("environment", "pass", critical=True),
            ("bad_check", raiser),
            _mk_check("config", "pass"),
        ]
        checker = Checker(checks)
        win = SplashWindow()
        win.show()
        win.run(checker, step_delay_ms=5)

        _pump(qapp, until=lambda: not checker._checks)
        _pump(qapp, deadline=0.3)

        # All three rows must render, even though one check raised
        assert len(win.findChildren(SplashRow)) == 3
        bad = [r for r in checker.results if r.id == "bad_check"][0]
        assert bad.status == "fail"
        assert "RuntimeError" in bad.message
        win.close()


# ──────────────────────────────────────────────────────────────────
# Headless / --no-splash path
# ──────────────────────────────────────────────────────────────────

class TestHeadlessPath:
    def test_run_all_headless_returns_passed_critical(self, qapp):
        """Simulate `python main.py --no-splash`: pure Checker, no UI."""
        checks = [
            _mk_check("environment", "pass", critical=True),
            _mk_check("credentials", "pass", critical=True),
            _mk_check("config", "pass"),
        ]
        checker = Checker(checks)
        results = checker.run_all()
        assert len(results) == 3
        assert checker.passed_critical is True

    def test_headless_flags_critical_failure(self, qapp):
        checks = [
            _mk_check("database", "fail", critical=True),
        ]
        checker = Checker(checks)
        checker.run_all()
        assert checker.passed_critical is False
