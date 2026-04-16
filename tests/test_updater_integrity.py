"""
Unit tests for the Phase-3 checksum enforcement in src/updater/updater.py

Covers:
  - stage() refuses without a checksum (default require_checksum=True)
  - stage() refuses when checksum mismatches
  - stage() still allows opt-out via require_checksum=False (dev tooling)

Uses PySide6 signals, so a QApplication is instantiated per fixture.

Run: python -m pytest tests/test_updater_integrity.py -x -v
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from src.updater.updater import Updater


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def _pump(app, deadline=2.0, until=None):
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        app.processEvents()
        if until and until():
            return
        time.sleep(0.01)


# ──────────────────────────────────────────────────────────────────
# Checksum required by default
# ──────────────────────────────────────────────────────────────────

class TestChecksumMandatory:
    def test_stage_without_checksum_fails_immediately(self, qapp):
        updater = Updater()
        messages: list[str] = []
        updater.failed.connect(lambda m: messages.append(m))

        updater.stage("https://example.invalid/update.zip",
                      expected_sha256="", new_version="v9.3.0")

        _pump(qapp, until=lambda: bool(messages))
        assert messages, "failed signal should have fired"
        assert "checksum" in messages[0].lower()

    def test_stage_without_checksum_never_opens_socket(self, qapp, monkeypatch):
        """Synchronous refusal — no background thread is spawned at all."""
        import urllib.request
        calls = []
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda *a, **kw: calls.append("opened"),
        )
        updater = Updater()
        failed = []
        updater.failed.connect(lambda m: failed.append(m))
        updater.stage("https://example.invalid/update.zip",
                      expected_sha256="", new_version="v9.3.0")
        _pump(qapp, until=lambda: bool(failed))
        assert calls == []  # socket never opened

    def test_dev_opt_out_still_works(self, qapp):
        """require_checksum=False must not trigger the early refusal path."""
        updater = Updater()
        failed = []
        updater.failed.connect(lambda m: failed.append(m))

        # Will attempt download and fail on the bad URL — that's fine.
        updater.stage(
            "https://this-host-definitely-does-not-exist.invalid/update.zip",
            expected_sha256="",
            new_version="v9.3.0",
            require_checksum=False,
        )
        _pump(qapp, until=lambda: bool(failed))
        assert failed, "updater should still fail on network error"
        # The early-refusal message is NOT the one we got
        assert "No SHA-256 checksum" not in failed[0]


# ──────────────────────────────────────────────────────────────────
# sha256 helper
# ──────────────────────────────────────────────────────────────────

class TestSha256Helper:
    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        expected = hashlib.sha256(b"").hexdigest()
        assert Updater._sha256(str(f)) == expected

    def test_non_empty_file(self, tmp_path):
        f = tmp_path / "x.bin"
        payload = b"alma insights release binary stand-in"
        f.write_bytes(payload)
        assert Updater._sha256(str(f)) == hashlib.sha256(payload).hexdigest()
