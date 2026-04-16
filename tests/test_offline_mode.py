"""
tests/test_offline_mode.py — graceful degradation when the network is gone.

Patches urlopen + socket.create_connection to raise OSError. Every
subsystem that can reach the internet at startup must:

  * Not raise
  * Surface an actionable message (pass/warn/fail result object, log entry,
    or friendly exception)

Covered subsystems:
  - Startup update check  (src/startup/checks/updates.py)
  - Update checker class  (src/updater/update_checker.py)
  - GitHub App token mint (src/updater/github_app_auth.py)
  - Env-guard scan        (src/startup/env_guard.py)
  - Startup integrity     (no network — smoke only)

Run: python -m pytest tests/test_offline_mode.py -x -v
"""

from __future__ import annotations

import socket
import urllib.error
import urllib.request
from unittest.mock import patch

import pytest


# ──────────────────────────────────────────────────────────────────
# Shared patcher: blow up every outbound HTTP + socket connection
# ──────────────────────────────────────────────────────────────────

@pytest.fixture
def offline(monkeypatch):
    """Block urlopen + socket.create_connection for the duration of a test."""

    def urlopen_blocked(*args, **kwargs):
        raise urllib.error.URLError(reason="[offline] network disabled in tests")

    def create_connection_blocked(*args, **kwargs):
        raise OSError("[offline] network disabled in tests")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen_blocked)
    monkeypatch.setattr(socket, "create_connection", create_connection_blocked)
    yield


# ──────────────────────────────────────────────────────────────────
# Startup update check
# ──────────────────────────────────────────────────────────────────

class TestStartupUpdateCheck:
    def test_pat_mode_offline_warns_not_fails(self, offline, monkeypatch):
        from src.startup.checks import updates

        monkeypatch.setattr(
            "src.updater.update_checker._resolve_config_and_token",
            lambda: ("pat", "https://api.github.com/repos/x/y/releases/latest", "tok"),
        )
        result = updates.check_for_update()
        assert result.status == "warn"
        assert result.critical is False
        assert "network" in result.message.lower()

    def test_disabled_mode_offline_still_passes(self, offline, monkeypatch):
        from src.startup.checks import updates

        monkeypatch.setattr(
            "src.updater.update_checker._resolve_config_and_token",
            lambda: ("disabled", "https://example", ""),
        )
        result = updates.check_for_update()
        # Disabled short-circuits before any socket attempt
        assert result.status == "pass"


# ──────────────────────────────────────────────────────────────────
# UpdateChecker (Qt-signal class)
# ──────────────────────────────────────────────────────────────────

class TestUpdateChecker:
    def test_offline_emits_check_failed(self, offline):
        from PySide6.QtWidgets import QApplication
        from src.updater.update_checker import UpdateChecker

        app = QApplication.instance() or QApplication([])

        checker = UpdateChecker(
            releases_url="https://api.github.com/repos/x/y/releases/latest",
            github_pat="tok",
            auth_mode="pat",
        )
        captured: list[str] = []
        checker.check_failed.connect(lambda msg: captured.append(msg))
        checker.update_available.connect(lambda *a: captured.append("AVAIL"))
        checker.up_to_date.connect(lambda: captured.append("UP_TO_DATE"))

        # run synchronously so the test is deterministic
        checker._do_check()

        assert captured, "some signal should have fired"
        assert captured[0].lower().startswith("network") or "error" in captured[0].lower()
        assert "AVAIL" not in captured


# ──────────────────────────────────────────────────────────────────
# GitHub App token mint
# ──────────────────────────────────────────────────────────────────

class TestGitHubAppOffline:
    def test_mint_returns_none_offline(self, offline):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from src.updater import github_app_auth

        # Real keypair so JWT signing works — only the HTTP exchange fails
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")

        token = github_app_auth.mint_installation_token({
            "app_id": "123",
            "installation_id": "456",
            "private_key_pem": pem,
        })
        assert token is None


# ──────────────────────────────────────────────────────────────────
# Env-guard scan is pure — no network involved — but sanity-check
# that it still functions when the network is blocked
# ──────────────────────────────────────────────────────────────────

class TestEnvGuardOffline:
    def test_enforce_and_scan_run_offline(self, offline):
        from src.startup import env_guard
        env_guard.enforce()
        issues = env_guard.scan_for_issues()
        # Issues list is a list[str], never None
        assert isinstance(issues, list)


# ──────────────────────────────────────────────────────────────────
# Integrity check is pure-local too — no network path at all
# ──────────────────────────────────────────────────────────────────

class TestIntegrityOffline:
    def test_integrity_check_runs_without_network(self, offline, tmp_path, monkeypatch):
        from src.startup.checks import integrity

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(integrity, "_CHECKSUMS_FILE",
                            tmp_path / "checksums.json")
        result = integrity.check_integrity()
        assert result.status == "pass"  # absent manifest → "dev checkout" pass


# ──────────────────────────────────────────────────────────────────
# Splash checker (the orchestrator) never raises on offline
# ──────────────────────────────────────────────────────────────────

class TestSplashOffline:
    def test_run_all_checks_offline(self, offline, monkeypatch, tmp_path):
        """Walk every check with the network blocked; no exception bubbles up."""
        from src.startup.checker import Checker
        from src.startup.checks import DEFAULT_CHECKS

        monkeypatch.chdir(tmp_path)
        # Hide legacy creds.json so check_credentials doesn't tamper with test
        # home; we just want the Checker to complete without raising.

        checker = Checker(list(DEFAULT_CHECKS))
        # run_all must never propagate — any check that raised would be
        # converted to a fail-result.
        results = checker.run_all()
        assert len(results) == len(DEFAULT_CHECKS)
