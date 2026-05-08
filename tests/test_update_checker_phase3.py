"""
Unit tests for Phase-3 changes in src/updater/update_checker.py

Covers:
  - _resolve_config_and_token: auth_mode routing
  - _load_token: keyring primary + legacy fallbacks
  - build_default_update_checker factory
  - UpdateChecker honours auth_mode="disabled" without any HTTP call
  - UpdateChecker strips / prefixes versions consistently

Does NOT hit the network — every HTTP call is patched.

Run: python -m pytest tests/test_update_checker_phase3.py -x -v
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.updater import update_checker as uc


# ──────────────────────────────────────────────────────────────────
# _resolve_config_and_token
# ──────────────────────────────────────────────────────────────────

class TestResolveConfig:
    def test_defaults_to_pat_when_no_settings(self, monkeypatch):
        # 2026-05-07: default flipped from "disabled" to "pat" so the
        # bundled-token fallback activates out of the box. Token is
        # still empty here because we haven't populated keyring or
        # bundled in this test.
        monkeypatch.setattr(
            "src.data.settings_manager.get_section",
            lambda section, default=None: default if default is not None else {},
        )
        # Stub out token loaders so the test isolates the mode resolution.
        monkeypatch.setattr(uc, "_load_token", lambda *_a, **_kw: "")
        mode, url, token = uc._resolve_config_and_token()
        assert mode == "pat"
        assert "alma-health/alma-insights" in url
        assert token == ""

    def test_pat_mode_picks_up_keyring_token(self, monkeypatch):
        monkeypatch.setattr(
            "src.data.settings_manager.get_section",
            lambda section, default=None: {
                "auth_mode": "pat",
                "github_repo": "acme/widgets",
            },
        )
        with patch("src.data.pat_store.load_setting") as mock_load:
            mock_load.side_effect = lambda key, default=None: (
                "ghp_secret" if key == "github_update_token" else default
            )
            mode, url, token = uc._resolve_config_and_token()
        assert mode == "pat"
        assert "acme/widgets" in url
        assert token == "ghp_secret"

    def test_pat_mode_falls_back_to_legacy_key(self, monkeypatch):
        monkeypatch.setattr(
            "src.data.settings_manager.get_section",
            lambda section, default=None: {"auth_mode": "pat"},
        )
        with patch("src.data.pat_store.load_setting") as mock_load:
            # github_update_token absent; github_pat present (legacy)
            mock_load.side_effect = lambda key, default=None: (
                "legacy_pat" if key == "github_pat" else ""
            )
            mode, _, token = uc._resolve_config_and_token()
        assert mode == "pat"
        assert token == "legacy_pat"

    def test_github_app_mode_routes_to_mint(self, monkeypatch):
        monkeypatch.setattr(
            "src.data.settings_manager.get_section",
            lambda section, default=None: {
                "auth_mode": "github_app",
                "app_id": "123",
                "installation_id": "456",
                "private_key_pem": "fake",
            },
        )
        with patch("src.updater.github_app_auth.mint_installation_token",
                   return_value="inst_token_xyz"):
            mode, _, token = uc._resolve_config_and_token()
        assert mode == "github_app"
        assert token == "inst_token_xyz"

    def test_unknown_mode_falls_back_to_disabled(self, monkeypatch):
        monkeypatch.setattr(
            "src.data.settings_manager.get_section",
            lambda section, default=None: {"auth_mode": "weird"},
        )
        mode, _, token = uc._resolve_config_and_token()
        assert mode == "disabled"
        assert token == ""

    def test_settings_exception_does_not_break_resolution(self, monkeypatch):
        # When settings fail, we fall through to the new "pat" default.
        # Token resolution is isolated here so a missing keyring on the
        # CI box doesn't accidentally pull in a real value.
        def boom(*a, **kw):
            raise RuntimeError("settings broken")
        monkeypatch.setattr("src.data.settings_manager.get_section", boom)
        monkeypatch.setattr(uc, "_load_token", lambda *_a, **_kw: "")
        mode, url, token = uc._resolve_config_and_token()
        assert mode == "pat"
        assert url  # still a valid URL
        assert token == ""


# ──────────────────────────────────────────────────────────────────
# UpdateChecker.check() short-circuits when disabled
# ──────────────────────────────────────────────────────────────────

class TestDisabledMode:
    def test_disabled_emits_up_to_date_without_http(self, monkeypatch, qapp=None):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])

        checker = uc.UpdateChecker(auth_mode="disabled")
        calls = []
        checker.up_to_date.connect(lambda: calls.append("up"))
        checker.update_available.connect(lambda *a: calls.append("avail"))
        checker.check_failed.connect(lambda m: calls.append("fail"))

        # Patch urlopen — it must NOT be called in disabled mode
        with patch("urllib.request.urlopen") as urlopen:
            checker.check()
            # up_to_date emits synchronously in disabled mode
            urlopen.assert_not_called()
        assert calls == ["up"]


# ──────────────────────────────────────────────────────────────────
# build_default_update_checker factory
# ──────────────────────────────────────────────────────────────────

class TestFactory:
    def test_factory_pulls_from_settings(self, monkeypatch):
        monkeypatch.setattr(
            uc, "_resolve_config_and_token",
            lambda: ("pat", "https://api.github.com/repos/foo/bar/releases/latest", "tok"),
        )
        checker = uc.build_default_update_checker()
        assert checker._auth_mode == "pat"
        assert "foo/bar" in checker._url
        assert checker._pat == "tok"


# ──────────────────────────────────────────────────────────────────
# Version helpers still work (regression coverage for the refactor)
# ──────────────────────────────────────────────────────────────────

class TestVersionParsing:
    def test_parse_basic(self):
        assert uc._parse_version("1.2.3") == (1, 2, 3)
        assert uc._parse_version("v9.3.0") == (9, 3, 0)
        assert uc._parse_version("v9.3.0-rc1") == (9, 3, 0)

    def test_is_newer(self):
        assert uc._is_newer("1.0.0", "1.0.1")
        assert uc._is_newer("v1.0.0", "v2.0.0-alpha")
        assert not uc._is_newer("2.0.0", "1.9.9")


# ──────────────────────────────────────────────────────────────────
# HTTP error translation
# ──────────────────────────────────────────────────────────────────

class TestHttpErrors:
    def test_401_translated(self):
        import urllib.error
        exc = urllib.error.HTTPError("u", 401, "x", {}, None)
        assert "auth" in uc._http_error_message(exc).lower()

    def test_404_translated(self):
        import urllib.error
        exc = urllib.error.HTTPError("u", 404, "x", {}, None)
        assert "not found" in uc._http_error_message(exc).lower()

    def test_403_translated(self):
        import urllib.error
        exc = urllib.error.HTTPError("u", 403, "x", {}, None)
        assert "rate" in uc._http_error_message(exc).lower() or "permission" in uc._http_error_message(exc).lower()
