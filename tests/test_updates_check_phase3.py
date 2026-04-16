"""
Unit tests for the Phase-3 startup update check.

Covers:
  - Disabled mode passes without HTTP
  - Missing token warns
  - Update-available reports warn with current/latest versions
  - Up-to-date passes
  - HTTP 401 / 404 / network errors warn with friendly text
  - The existing stub-test in test_startup_checks.py has been retired here

Run: python -m pytest tests/test_updates_check_phase3.py -x -v
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.startup.checks import updates as updates_check


# ──────────────────────────────────────────────────────────────────
# Disabled mode (default — GitHub not set up yet)
# ──────────────────────────────────────────────────────────────────

class TestDisabledMode:
    def test_disabled_passes_without_http(self, monkeypatch):
        monkeypatch.setattr(
            "src.updater.update_checker._resolve_config_and_token",
            lambda: ("disabled", "https://example/r", ""),
        )
        with patch("urllib.request.urlopen") as urlopen:
            result = updates_check.check_for_update()
            urlopen.assert_not_called()
        assert result.status == "pass"
        assert result.critical is False
        assert "disabled" in result.message.lower()


# ──────────────────────────────────────────────────────────────────
# No token available in PAT mode
# ──────────────────────────────────────────────────────────────────

class TestMissingToken:
    def test_pat_mode_without_token_warns(self, monkeypatch):
        monkeypatch.setattr(
            "src.updater.update_checker._resolve_config_and_token",
            lambda: ("pat", "https://example/r", ""),
        )
        result = updates_check.check_for_update()
        assert result.status == "warn"
        assert "token" in result.message.lower()


# ──────────────────────────────────────────────────────────────────
# Update available
# ──────────────────────────────────────────────────────────────────

class TestUpdateAvailable:
    def test_newer_tag_emits_warn(self, monkeypatch):
        monkeypatch.setattr(
            "src.updater.update_checker._resolve_config_and_token",
            lambda: ("pat", "https://example/r", "tok"),
        )
        # Force current version to something low
        import src
        monkeypatch.setattr(src, "VERSION", "1.0.0", raising=False)

        fake = MagicMock()
        fake.__enter__.return_value.read.return_value = json.dumps(
            {"tag_name": "v9.3.0", "html_url": "https://example/r/v9.3.0"}
        ).encode("utf-8")
        fake.__exit__.return_value = False

        with patch("urllib.request.urlopen", return_value=fake):
            result = updates_check.check_for_update()

        assert result.status == "warn"
        assert "9.3.0" in result.message
        assert result.critical is False

    def test_same_version_passes(self, monkeypatch):
        monkeypatch.setattr(
            "src.updater.update_checker._resolve_config_and_token",
            lambda: ("pat", "https://example/r", "tok"),
        )
        import src
        monkeypatch.setattr(src, "VERSION", "9.3.0", raising=False)

        fake = MagicMock()
        fake.__enter__.return_value.read.return_value = json.dumps(
            {"tag_name": "v9.3.0"}
        ).encode("utf-8")
        fake.__exit__.return_value = False

        with patch("urllib.request.urlopen", return_value=fake):
            result = updates_check.check_for_update()
        assert result.status == "pass"
        assert "up to date" in result.message.lower()


# ──────────────────────────────────────────────────────────────────
# HTTP failures degrade gracefully
# ──────────────────────────────────────────────────────────────────

class TestHttpFailures:
    def _arrange(self, monkeypatch):
        monkeypatch.setattr(
            "src.updater.update_checker._resolve_config_and_token",
            lambda: ("pat", "https://example/r", "tok"),
        )

    def test_401_warns_with_auth_message(self, monkeypatch):
        import urllib.error
        self._arrange(monkeypatch)
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("u", 401, "bad", {}, None),
        ):
            result = updates_check.check_for_update()
        assert result.status == "warn"
        assert "auth" in result.message.lower()

    def test_404_warns_with_repo_message(self, monkeypatch):
        import urllib.error
        self._arrange(monkeypatch)
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("u", 404, "nope", {}, None),
        ):
            result = updates_check.check_for_update()
        assert result.status == "warn"
        assert "not found" in result.message.lower()

    def test_network_error_warns(self, monkeypatch):
        import urllib.error
        self._arrange(monkeypatch)
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("no dns"),
        ):
            result = updates_check.check_for_update()
        assert result.status == "warn"
        assert "network" in result.message.lower()


# ──────────────────────────────────────────────────────────────────
# Tag missing from response
# ──────────────────────────────────────────────────────────────────

class TestMalformedResponse:
    def test_no_tag_name_warns(self, monkeypatch):
        monkeypatch.setattr(
            "src.updater.update_checker._resolve_config_and_token",
            lambda: ("pat", "https://example/r", "tok"),
        )
        fake = MagicMock()
        fake.__enter__.return_value.read.return_value = b'{"message":"empty"}'
        fake.__exit__.return_value = False
        with patch("urllib.request.urlopen", return_value=fake):
            result = updates_check.check_for_update()
        assert result.status == "warn"
        assert "tag_name" in result.message
