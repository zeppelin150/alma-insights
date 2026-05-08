"""
Restart-helper tests (Piece 2A, 2026-05-07)
============================================

Verifies :func:`src.updater.restart.restart_app` spawns a detached
child with the right argv, on every supported platform, and calls
``QApplication.quit()`` afterward.

Run: ``python -m pytest tests/test_restart_helper.py -x -v``
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.updater import restart


# ── Argv passthrough ──────────────────────────────────────────────


class TestArgvPassthrough:
    def test_uses_current_executable_and_argv(self, monkeypatch):
        monkeypatch.setattr("sys.executable", "/usr/bin/python3.13")
        monkeypatch.setattr("sys.argv", ["main.py", "--no-splash"])
        captured = {}

        def fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return MagicMock()

        with patch("subprocess.Popen", side_effect=fake_popen), \
             patch("PySide6.QtWidgets.QApplication.instance", return_value=None):
            restart.restart_app()

        assert captured["cmd"][0] == "/usr/bin/python3.13"
        assert captured["cmd"][1:] == ["main.py", "--no-splash"]


# ── Platform-specific detach flags ────────────────────────────────


class TestDetachKwargs:
    def test_windows_uses_creationflags(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "win32")
        kwargs = restart._detach_kwargs()
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP = 0x208
        assert kwargs.get("creationflags") == 0x208
        assert kwargs.get("stdin") is not None
        assert kwargs.get("stdout") is not None
        assert kwargs.get("stderr") is not None

    def test_posix_uses_start_new_session(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        kwargs = restart._detach_kwargs()
        assert kwargs.get("start_new_session") is True
        assert "creationflags" not in kwargs

    def test_macos_uses_start_new_session(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "darwin")
        kwargs = restart._detach_kwargs()
        assert kwargs.get("start_new_session") is True


# ── QApplication.quit() ordering ──────────────────────────────────


class TestQuitOrdering:
    def test_spawn_happens_before_quit(self, monkeypatch):
        order: list[str] = []

        def fake_popen(*_a, **_kw):
            order.append("spawn")
            return MagicMock()

        fake_app = MagicMock()
        fake_app.quit = lambda: order.append("quit")

        with patch("subprocess.Popen", side_effect=fake_popen), \
             patch("PySide6.QtWidgets.QApplication.instance", return_value=fake_app):
            restart.restart_app()

        assert order == ["spawn", "quit"]

    def test_quit_skipped_when_no_qapp(self):
        with patch("subprocess.Popen", return_value=MagicMock()), \
             patch("PySide6.QtWidgets.QApplication.instance", return_value=None):
            # Should not raise even though there's no QApplication
            restart.restart_app()


# ── Failure modes ────────────────────────────────────────────────


class TestFailureModes:
    def test_popen_failure_reraises(self):
        with patch("subprocess.Popen", side_effect=OSError("boom")):
            with pytest.raises(OSError, match="boom"):
                restart.restart_app()

    def test_safe_quotes_paths_with_spaces(self):
        assert restart._safe("/path with space/x") == '"/path with space/x"'
        assert restart._safe("nospace") == "nospace"
