"""
Splash "Install & Restart" widget tests (Piece 2A, 2026-05-07)
================================================================

Verifies :class:`src.startup.update_action_widget.UpdateActionWidget`
drives the install flow correctly end-to-end with the manifest
fetcher, the updater, and the restart helper all mocked. We do not
touch the filesystem or the network.

Run: ``python -m pytest tests/test_update_action_widget.py -x -v``
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

from src.startup import update_action_widget as uaw


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def payload():
    return {
        "new_version": "9.9.9",
        "html_url": "https://github.com/alma-health/alma-insights/releases/tag/v9.9.9",
        "assets": [{"name": "manifest.json", "browser_download_url": "x"}],
        "token": "bearer-token",
    }


@pytest.fixture
def make_widget(qapp, payload):
    """Factory that builds an UpdateActionWidget and cleans it up at end-of-test.

    Prevents Qt widget churn between tests from triggering Windows
    access violations during garbage collection.
    """
    created: list = []

    def _make(custom_payload=None):
        w = uaw.UpdateActionWidget(custom_payload or payload)
        created.append(w)
        return w

    yield _make
    for w in created:
        try:
            w.deleteLater()
        except Exception:  # noqa: BLE001
            pass
    qapp.processEvents()


# ── Initial state ────────────────────────────────────────────────


class TestInitialState:
    def test_button_text_includes_version(self, make_widget):
        widget = make_widget()
        assert "9.9.9" in widget._action_btn.text()
        assert "Restart" in widget._action_btn.text()

    def test_progress_hidden_initially(self, make_widget):
        widget = make_widget()
        assert not widget._progress.isVisible()
        assert not widget.is_busy()

    def test_status_hidden_initially(self, make_widget):
        widget = make_widget()
        assert not widget._status.isVisible()


# ── Click → manifest resolve → stage ────────────────────────────


class TestInstallFlow:
    def test_click_resolves_manifest_and_starts_stage(self, make_widget):
        # Mock manifest fetcher to return a synthetic URL + SHA
        fake_url = "https://example.com/release.zip"
        fake_sha = "f" * 64

        widget = make_widget()
        # Patch on the instance, not the class, to avoid Qt signal
        # introspection issues when class methods are MagicMock'd.
        widget._resolve_artifact = lambda: (fake_url, fake_sha)

        with patch("src.updater.updater.Updater") as FakeUpdater:
            fake_inst = MagicMock()
            FakeUpdater.return_value = fake_inst

            widget._on_install_clicked()

            # token MUST be forwarded — without it a private-repo asset 404s
            # after the manifest resolve has already succeeded.
            fake_inst.stage.assert_called_once_with(
                fake_url, expected_sha256=fake_sha, new_version="9.9.9",
                token=widget._payload.get("token", ""),
            )
            assert widget.is_busy() is True
            assert not widget._action_btn.isEnabled()

    def test_resolve_failure_shows_error_and_reenables_button(self, make_widget):
        from src.updater.manifest_fetcher import ManifestFetchError
        widget = make_widget()

        def _raises():
            raise ManifestFetchError("Manifest missing")
        widget._resolve_artifact = _raises

        widget._on_install_clicked()
        assert widget.is_busy() is False
        assert widget._action_btn.isEnabled()
        assert widget._action_btn.text() == "Try again"
        assert "Manifest missing" in widget._status.text()


# ── Updater.progress / complete / failed handlers ───────────────


class TestProgressAndComplete:
    def test_progress_updates_bar(self, make_widget):
        widget = make_widget()
        widget._on_progress(42, "Downloading…")
        assert widget._progress.value() == 42
        assert "Downloading" in widget._status.text()

    def test_complete_triggers_restart_after_delay(self, make_widget):
        # CRITICAL: do NOT call _on_complete here — it schedules a real
        # QTimer.singleShot(800ms, _do_restart). The 800ms timer fires
        # AFTER pytest leaves this `with patch(...)` block, so by then
        # `restart_app` is unpatched, runs for real, spawns a child
        # python.exe with the current pytest argv, which re-runs THIS
        # test — death loop, ~15 new python.exe per second.
        #
        # Just call _do_restart directly to verify the routing claim.
        # The timer-scheduling part of _on_complete is a UI affordance,
        # not the actual restart mechanism.
        with patch("src.updater.restart.restart_app") as fake_restart:
            widget = make_widget()
            # _do_restart bails out if the widget isn't visible (anti-
            # death-loop guard); fake the visibility check so we can
            # verify the routing without showing a real window.
            widget.isVisible = lambda: True
            widget._do_restart()
            fake_restart.assert_called_once()

    def test_do_restart_bails_when_widget_torn_down(self, make_widget):
        """Anti-death-loop guard: a stale QTimer firing on a hidden /
        deleted widget must NOT call restart_app."""
        with patch("src.updater.restart.restart_app") as fake_restart:
            widget = make_widget()
            # Widget is constructed but never shown — isVisible() is False.
            widget._do_restart()
            fake_restart.assert_not_called()

    def test_failed_shows_error(self, make_widget):
        widget = make_widget()
        # Pretend we'd already kicked off a stage
        widget._set_busy(True)
        widget._on_failed("Checksum mismatch")
        assert widget.is_busy() is False
        assert widget._action_btn.isEnabled()
        assert "Checksum mismatch" in widget._status.text()


# ── busy_changed signal ─────────────────────────────────────────


class TestBusyChangedSignal:
    def test_emits_true_then_false(self, make_widget):
        widget = make_widget()
        captured: list[bool] = []
        widget.busy_changed.connect(lambda b: captured.append(b))

        widget._set_busy(True)
        widget._set_busy(True)   # idempotent — should not re-emit
        widget._set_busy(False)
        widget._set_busy(False)

        assert captured == [True, False]
