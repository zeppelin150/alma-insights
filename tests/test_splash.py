"""
Unit tests for src/startup/splash_window.py + src/startup/splash_row.py

Covers:
  - SplashRow renders for each status
  - SplashWindow constructs without error
  - run() streams results from a Checker into rows
  - Continue button stays disabled when a critical check fails
  - Continue button enables when all critical checks pass
  - Clicking Continue emits continue_clicked and closes the dialog

Uses a real QApplication with processEvents to advance the QTimer
queue. No network, no Gemini CLI, no keyring — all checks are stubs.

Run: python -m pytest tests/test_splash.py -x -v
"""

from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog

from src.startup.checker import CheckResult, Checker
from src.startup.splash_row import SplashRow
from src.startup.splash_window import SplashWindow


# ──────────────────────────────────────────────────────────────────
# Qt fixture (session-scoped — one QApp per test run)
# ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def make_splash(qapp):
    """Factory that builds a SplashWindow and *destroys* it at teardown.

    Plain ``win.close()`` only hides a QDialog — Qt keeps the widget
    alive, which on Windows can leave a stray top-level window visible
    when the test process doesn't fully exit (e.g. pytest worker hung
    by another widget). Setting ``WA_DeleteOnClose`` + explicit
    ``deleteLater()`` + an event-loop pump guarantees the widget
    really goes away.
    """
    from PySide6.QtCore import Qt
    created: list[SplashWindow] = []

    def _make(*args, **kwargs):
        win = SplashWindow(*args, **kwargs)
        win.setAttribute(Qt.WA_DeleteOnClose, True)
        created.append(win)
        return win

    yield _make
    for win in created:
        try:
            win.close()
            win.deleteLater()
        except Exception:  # noqa: BLE001
            pass
    qapp.processEvents()


def _pump(app, deadline_seconds: float = 2.0, until=None):
    """Spin the Qt event loop until `until()` is truthy or deadline passes."""
    end = time.monotonic() + deadline_seconds
    while time.monotonic() < end:
        app.processEvents()
        if until is not None and until():
            return
        time.sleep(0.01)


def _pass(name="ok", critical=True):
    return CheckResult(id=name, name=name, status="pass", message="all good", critical=critical)


def _warn(name="warn"):
    return CheckResult(id=name, name=name, status="warn",
                       message="hmm", remediation="take a look", critical=False)


def _fail(name="fail", critical=True):
    return CheckResult(id=name, name=name, status="fail",
                       message="broken", remediation="fix it", critical=critical)


# ──────────────────────────────────────────────────────────────────
# SplashRow
# ──────────────────────────────────────────────────────────────────

class TestSplashRow:
    def test_row_renders_for_pass(self, qapp):
        row = SplashRow(_pass("ok"))
        assert row.result.status == "pass"
        assert row.layout() is not None  # built

    def test_row_renders_for_warn(self, qapp):
        row = SplashRow(_warn("w"))
        assert row.result.status == "warn"

    def test_row_renders_for_fail(self, qapp):
        row = SplashRow(_fail("f"))
        assert row.result.status == "fail"

    def test_row_subtext_combines_message_and_remediation_on_fail(self, qapp):
        row = SplashRow(_fail("f"))
        assert "broken" in row._subtext()
        assert "fix it" in row._subtext()

    def test_row_subtext_is_message_on_pass(self, qapp):
        row = SplashRow(_pass("ok"))
        assert row._subtext() == "all good"


# ──────────────────────────────────────────────────────────────────
# SplashWindow construction
# ──────────────────────────────────────────────────────────────────

class TestSplashConstruction:
    def test_constructs_without_errors(self, make_splash):
        # mode pinned: without it the splash resolves the dev machine's
        # settings, and a box whose default mode is enablement would brand
        # as the Content Command Center (see TestBranding).
        win = make_splash(app_version="v9.3.0-test", mode="product")
        assert win.windowTitle().startswith("Alma Insights")
        assert win._continue_btn is not None
        assert win._continue_btn.isEnabled() is False

    def test_continue_disabled_before_run(self, make_splash):
        win = make_splash()
        assert not win._continue_btn.isEnabled()


# ──────────────────────────────────────────────────────────────────
# Mode-aware branding (product = classic, enablement = CCC)
# ──────────────────────────────────────────────────────────────────

def _label_texts(win):
    from PySide6.QtWidgets import QLabel
    return [lbl.text() for lbl in win.findChildren(QLabel)]


class TestBranding:
    def test_product_mode_keeps_classic_branding(self, make_splash):
        win = make_splash(app_version="v1", mode="product")
        texts = _label_texts(win)
        assert win.windowTitle() == "Alma Insights — Starting up"
        assert "ALMA INSIGHTS" in texts
        assert "RCM ISSUE ANALYSIS" in texts
        assert "v1 — RCM Operations" in texts

    def test_enablement_mode_brands_as_content_command_center(self, make_splash):
        win = make_splash(app_version="v1", mode="enablement")
        texts = _label_texts(win)
        assert win.windowTitle() == "Content Command Center — Starting up"
        assert "CONTENT COMMAND CENTER" in texts
        assert "v1 — Content Command Center" in texts
        # The RCM header/tagline must be gone in enablement.
        assert "ALMA INSIGHTS" not in texts
        assert "RCM ISSUE ANALYSIS" not in texts
        assert not any("RCM" in t for t in texts)

    def test_unpinned_mode_resolves_via_app_modes(self, make_splash, monkeypatch):
        from src.ui import app_modes
        monkeypatch.setattr(app_modes, "resolve_startup_mode",
                            lambda: "enablement")
        win = make_splash(app_version="v1")
        assert win.windowTitle() == "Content Command Center — Starting up"

    def test_resolution_failure_falls_back_to_product(self, make_splash, monkeypatch):
        from src.ui import app_modes

        def boom():
            raise RuntimeError("settings unreadable")

        monkeypatch.setattr(app_modes, "resolve_startup_mode", boom)
        win = make_splash(app_version="v1")
        assert win.windowTitle() == "Alma Insights — Starting up"

    def test_unknown_mode_string_gets_product_branding(self, make_splash):
        win = make_splash(app_version="v1", mode="bogus")
        assert win.windowTitle() == "Alma Insights — Starting up"


# ──────────────────────────────────────────────────────────────────
# Streaming
# ──────────────────────────────────────────────────────────────────

class TestStreaming:
    def test_run_streams_rows(self, qapp, make_splash):
        checker = Checker([
            ("a", lambda: _pass("a")),
            ("b", lambda: _pass("b")),
            ("c", lambda: _pass("c")),
        ])
        win = make_splash(app_version="v1")
        win.show()
        win.run(checker, step_delay_ms=5)

        _pump(qapp, until=lambda: not checker._checks)
        # Allow the _finish() callback to run
        _pump(qapp, deadline_seconds=0.3)

        # A row is a SplashRow child of the scroll's inner container.
        rows = win.findChildren(SplashRow)
        assert len(rows) == 3

    def test_continue_enabled_when_all_critical_pass(self, qapp, make_splash):
        checker = Checker([
            ("a", lambda: _pass("a", critical=True)),
            ("b", lambda: _pass("b", critical=True)),
            ("c", lambda: _warn("c")),   # non-critical
        ])
        win = make_splash()
        win.show()
        win.run(checker, step_delay_ms=5)

        _pump(qapp, until=lambda: win._continue_btn.isEnabled())
        assert win._continue_btn.isEnabled()

    def test_continue_blocked_by_critical_failure(self, qapp, make_splash):
        checker = Checker([
            ("a", lambda: _pass("a", critical=True)),
            ("b", lambda: _fail("b", critical=True)),
        ])
        win = make_splash()
        win.show()
        win.run(checker, step_delay_ms=5)

        _pump(qapp, until=lambda: not checker._checks)
        _pump(qapp, deadline_seconds=0.3)

        assert not win._continue_btn.isEnabled()


# ──────────────────────────────────────────────────────────────────
# Summary text
# ──────────────────────────────────────────────────────────────────

class TestSummary:
    def test_all_pass_message(self, make_splash):
        win = make_splash()
        text = win._summary_text({"pass": 3, "warn": 0, "fail": 0}, True)
        assert "All systems" in text

    def test_warn_only_message(self, make_splash):
        win = make_splash()
        text = win._summary_text({"pass": 2, "warn": 1, "fail": 0}, True)
        assert "1 warning" in text
        assert "App will continue" in text

    def test_critical_failure_message(self, make_splash):
        win = make_splash()
        text = win._summary_text({"pass": 2, "warn": 0, "fail": 1}, False)
        assert "Cannot continue" in text


# ──────────────────────────────────────────────────────────────────
# Continue button signal
# ──────────────────────────────────────────────────────────────────

class TestContinueSignal:
    def test_click_emits_and_accepts(self, make_splash):
        emitted = []
        win = make_splash()
        win.continue_clicked.connect(lambda: emitted.append(True))
        win._continue_btn.setEnabled(True)
        win._on_continue()
        assert emitted == [True]
        assert win.result() == QDialog.Accepted
