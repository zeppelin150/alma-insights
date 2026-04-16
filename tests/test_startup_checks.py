"""
Unit tests for the 9 check modules under src/startup/checks/.

Each check is tested against its pass, warn, and fail branches.
The tests patch minimal dependencies rather than running real
subprocesses, databases, or keyring backends.

Run: python -m pytest tests/test_startup_checks.py -x -v
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import keyring
import keyring.backend
import keyring.errors
import pytest

# ──────────────────────────────────────────────────────────────────
# Check 1 — environment
# ──────────────────────────────────────────────────────────────────

from src.startup.checks import environment as env_check


class TestEnvironment:
    def test_happy_path_reports_pass(self):
        result = env_check.check_environment()
        assert result.status == "pass"
        assert result.critical is True
        assert "Python" in result.message

    def test_missing_import_fails(self, monkeypatch):
        monkeypatch.setattr(
            env_check,
            "_CORE_IMPORTS",
            ["PySide6", "not_a_real_module_xyz123"],
        )
        result = env_check.check_environment()
        assert result.status == "fail"
        assert "not_a_real_module_xyz123" in result.message

    def test_old_python_fails(self, monkeypatch):
        monkeypatch.setattr(env_check, "_MIN_PYTHON", (99, 0))
        result = env_check.check_environment()
        assert result.status == "fail"
        assert "need" in result.message.lower()


# ──────────────────────────────────────────────────────────────────
# Check 2 — credentials
# ──────────────────────────────────────────────────────────────────

from src.startup.checks import credentials as cred_check


class _MemKeyring(keyring.backend.KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self):
        self._store = {}

    def set_password(self, s, u, p):
        self._store[(s, u)] = p

    def get_password(self, s, u):
        return self._store.get((s, u))

    def delete_password(self, s, u):
        if (s, u) not in self._store:
            raise keyring.errors.PasswordDeleteError()
        del self._store[(s, u)]


class _BrokenKeyring(keyring.backend.KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def set_password(self, *a, **k):
        raise keyring.errors.KeyringError("nope")

    def get_password(self, *a, **k):
        raise keyring.errors.KeyringError("nope")

    def delete_password(self, *a, **k):
        raise keyring.errors.KeyringError("nope")


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    import importlib
    from src.data import pat_store
    importlib.reload(pat_store)
    yield tmp_path


@pytest.fixture
def mem_keyring():
    original = keyring.get_keyring()
    keyring.set_keyring(_MemKeyring())
    yield
    keyring.set_keyring(original)


@pytest.fixture
def broken_keyring():
    original = keyring.get_keyring()
    keyring.set_keyring(_BrokenKeyring())
    yield
    keyring.set_keyring(original)


class TestCredentials:
    def test_keyring_available_passes(self, fake_home, mem_keyring):
        result = cred_check.check_credentials()
        assert result.status == "pass"
        assert result.critical is True
        assert "Keyring accessible" in result.message

    def test_keyring_broken_fails(self, fake_home, broken_keyring):
        result = cred_check.check_credentials()
        assert result.status == "fail"
        assert result.critical is True
        assert "unavailable" in result.message.lower()
        assert result.remediation  # platform-specific hint

    def test_reports_stored_credential_count(self, fake_home, mem_keyring):
        from src.data import pat_store
        pat_store.save_setting("lightdash_pat", "ldpat_x")
        pat_store.save_setting("anthropic_api_key", "sk-ant-x")
        result = cred_check.check_credentials()
        assert "2 credential" in result.message

    def test_migrates_legacy_file(self, fake_home, mem_keyring):
        legacy = fake_home / ".alma-insights"
        legacy.mkdir(parents=True, exist_ok=True)
        (legacy / "credentials.json").write_text(json.dumps({
            "lightdash_pat": "ldpat_abc",
            "anthropic_api_key": "sk-ant-xyz",
        }))
        result = cred_check.check_credentials()
        assert "migrated 2" in result.message


# ──────────────────────────────────────────────────────────────────
# Check 3 — Gemini OAuth
# ──────────────────────────────────────────────────────────────────

from src.startup.checks import gemini as gem_check


class TestGeminiOAuth:
    def test_cli_missing_fails(self, monkeypatch):
        monkeypatch.setattr(gem_check, "_resolve_cli_path", lambda: None)
        result = gem_check.check_gemini_oauth()
        assert result.status == "fail"
        assert "not found" in result.message.lower()

    def test_cli_timeout_fails(self, monkeypatch, tmp_path):
        fake_cli = tmp_path / "gemini"
        fake_cli.write_text("")
        monkeypatch.setattr(gem_check, "_resolve_cli_path", lambda: fake_cli)
        def raise_timeout(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="gemini", timeout=1)
        monkeypatch.setattr(gem_check.subprocess, "run", raise_timeout)
        result = gem_check.check_gemini_oauth()
        assert result.status == "fail"
        assert "timed out" in result.message.lower()

    def test_cli_nonzero_fails(self, monkeypatch, tmp_path):
        fake_cli = tmp_path / "gemini"
        fake_cli.write_text("")
        monkeypatch.setattr(gem_check, "_resolve_cli_path", lambda: fake_cli)
        result_proc = MagicMock(returncode=1, stderr="unauthenticated", stdout="")
        monkeypatch.setattr(gem_check.subprocess, "run", lambda *a, **k: result_proc)
        result = gem_check.check_gemini_oauth()
        assert result.status == "fail"
        assert "unauthenticated" in result.message.lower()

    def test_cli_ok_passes(self, monkeypatch, tmp_path):
        fake_cli = tmp_path / "gemini"
        fake_cli.write_text("")
        monkeypatch.setattr(gem_check, "_resolve_cli_path", lambda: fake_cli)
        result_proc = MagicMock(returncode=0, stderr="", stdout="pong")
        monkeypatch.setattr(gem_check.subprocess, "run", lambda *a, **k: result_proc)
        result = gem_check.check_gemini_oauth()
        assert result.status == "pass"

    def test_unauthenticated_detector(self):
        assert gem_check._looks_unauthenticated("Error: not logged in")
        assert gem_check._looks_unauthenticated("auth error: refresh failed")
        assert not gem_check._looks_unauthenticated("fine")
        assert not gem_check._looks_unauthenticated("")


# ──────────────────────────────────────────────────────────────────
# Check 4 — updates (Phase 2 stub)
# ──────────────────────────────────────────────────────────────────

from src.startup.checks import updates as updates_check


class TestUpdates:
    def test_disabled_by_default_passes(self, monkeypatch):
        """Phase 3 default: auth_mode=disabled → pass without HTTP call."""
        monkeypatch.setattr(
            "src.updater.update_checker._resolve_config_and_token",
            lambda: ("disabled", "https://example/r", ""),
        )
        result = updates_check.check_for_update()
        assert result.status == "pass"
        assert result.critical is False
        assert "disabled" in result.message.lower()


# ──────────────────────────────────────────────────────────────────
# Check 5 — env guard
# ──────────────────────────────────────────────────────────────────

from src.startup.checks import airgap as airgap_check


class TestEnvironmentGuard:
    def test_no_issues_passes(self, monkeypatch):
        monkeypatch.setattr(airgap_check.env_guard, "enforce", lambda: [])
        monkeypatch.setattr(airgap_check.env_guard, "scan_for_issues", lambda **kw: [])
        result = airgap_check.check_environment_guard()
        assert result.status == "pass"

    def test_residual_issues_fail(self, monkeypatch):
        monkeypatch.setattr(airgap_check.env_guard, "enforce", lambda: [])
        monkeypatch.setattr(
            airgap_check.env_guard, "scan_for_issues",
            lambda **kw: ["HTTP_PROXY=corp.example.com still set"],
        )
        result = airgap_check.check_environment_guard()
        assert result.status == "fail"
        assert "HTTP_PROXY" in result.remediation


# ──────────────────────────────────────────────────────────────────
# Check 6 — hardware
# ──────────────────────────────────────────────────────────────────

from src.startup.checks import hardware as hw_check


class TestHardware:
    def test_cuda_machine_reports_gpu(self, monkeypatch):
        monkeypatch.setattr(hw_check.hw, "load_or_profile", lambda current_version: {
            "cpu_count": 20, "ram_gb": 64, "accelerator": "cuda",
            "gpu_name": "RTX 4070 Ti SUPER", "vram_gb": 16,
            "acp_max_workers": 8, "embedding_batch_size": 128,
        })
        result = hw_check.check_hardware("v1")
        assert result.status == "pass"
        assert "RTX 4070 Ti SUPER" in result.message
        assert "64GB" in result.message
        assert "20 cores" in result.message

    def test_m1_machine_reports_apple_silicon(self, monkeypatch):
        monkeypatch.setattr(hw_check.hw, "load_or_profile", lambda current_version: {
            "cpu_count": 8, "ram_gb": 16, "accelerator": "mps",
            "gpu_name": "Apple Silicon GPU", "vram_gb": 0,
            "acp_max_workers": 6, "embedding_batch_size": 32,
        })
        result = hw_check.check_hardware("v1")
        assert result.status == "pass"
        assert "Apple Silicon" in result.message

    def test_cpu_fallback_reports_none(self, monkeypatch):
        monkeypatch.setattr(hw_check.hw, "load_or_profile", lambda current_version: {
            "cpu_count": 4, "ram_gb": 8, "accelerator": "cpu",
            "gpu_name": None, "vram_gb": 0,
            "acp_max_workers": 2, "embedding_batch_size": 8,
        })
        result = hw_check.check_hardware("v1")
        assert result.status == "pass"
        assert "GPU: none" in result.message


# ──────────────────────────────────────────────────────────────────
# Check 7 — embedding model
# ──────────────────────────────────────────────────────────────────

from src.startup.checks import model as model_check


class TestEmbeddingModel:
    def test_missing_warns(self, monkeypatch):
        monkeypatch.setattr(model_check, "_find_model_path", lambda: None)
        result = model_check.check_embedding_model()
        assert result.status == "warn"
        assert result.critical is False
        assert "semantic search unavailable" in result.message.lower()

    def test_present_passes(self, monkeypatch, tmp_path):
        fake_path = tmp_path / "Qwen3-Embedding-0.6B"
        fake_path.mkdir()
        monkeypatch.setattr(model_check, "_find_model_path", lambda: fake_path)
        result = model_check.check_embedding_model()
        assert result.status == "pass"


# ──────────────────────────────────────────────────────────────────
# Check 8 — database
# ──────────────────────────────────────────────────────────────────

from src.startup.checks import database as db_check


@pytest.fixture
def fake_db(tmp_path, monkeypatch):
    """Create a minimal SQLite DB and point the check at it."""
    db = tmp_path / "alma_insights.db"
    with sqlite3.connect(str(db)) as conn:
        conn.execute("CREATE TABLE schema_migrations (version INTEGER)")
        conn.execute("INSERT INTO schema_migrations VALUES (18)")
        conn.execute("CREATE TABLE tickets (id INTEGER)")
        conn.executemany("INSERT INTO tickets VALUES (?)", [(1,), (2,), (3,)])
    monkeypatch.setattr(db_check, "_DB_PATH", db)
    yield db


class TestDatabase:
    def test_missing_db_is_noncritical(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db_check, "_DB_PATH", tmp_path / "not_there.db")
        result = db_check.check_database()
        assert result.status == "pass"
        assert result.critical is False

    def test_healthy_db_passes(self, fake_db):
        result = db_check.check_database()
        assert result.status == "pass"
        assert result.critical is True
        assert "Schema v18" in result.message
        assert "3 tickets" in result.message

    def test_corrupt_db_fails(self, tmp_path, monkeypatch):
        bad = tmp_path / "bad.db"
        bad.write_bytes(b"not a sqlite file")
        monkeypatch.setattr(db_check, "_DB_PATH", bad)
        result = db_check.check_database()
        assert result.status == "fail"
        assert result.critical is True


# ──────────────────────────────────────────────────────────────────
# Check 9 — config
# ──────────────────────────────────────────────────────────────────

from src.startup.checks import config as cfg_check


class TestConfig:
    def test_missing_file_warns(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cfg_check, "get_settings_path", lambda: tmp_path / "none.yaml")
        result = cfg_check.check_config()
        assert result.status == "warn"
        assert result.critical is False

    def test_valid_config_passes(self, tmp_path, monkeypatch):
        f = tmp_path / "settings.yaml"
        f.write_text("gemini:\n  model: x\nbehavior: {}\ndisplay: {}\ndatasets: [a, b, c]\n")
        monkeypatch.setattr(cfg_check, "get_settings_path", lambda: f)
        result = cfg_check.check_config()
        assert result.status == "pass"
        assert "3 dataset" in result.message

    def test_parse_error_fails(self, tmp_path, monkeypatch):
        f = tmp_path / "settings.yaml"
        f.write_text("not : valid : yaml :::")
        monkeypatch.setattr(cfg_check, "get_settings_path", lambda: f)
        result = cfg_check.check_config()
        assert result.status == "fail"

    def test_missing_sections_warn(self, tmp_path, monkeypatch):
        f = tmp_path / "settings.yaml"
        f.write_text("gemini: {}\n")  # missing behavior, display
        monkeypatch.setattr(cfg_check, "get_settings_path", lambda: f)
        result = cfg_check.check_config()
        assert result.status == "warn"
        assert "behavior" in result.message
        assert "display" in result.message


# ──────────────────────────────────────────────────────────────────
# Integration — DEFAULT_CHECKS registry
# ──────────────────────────────────────────────────────────────────

class TestRegistry:
    def test_default_checks_has_ten_entries(self):
        """Phase 4 added integrity as Check 1 → 10 total."""
        from src.startup.checks import DEFAULT_CHECKS
        assert len(DEFAULT_CHECKS) == 10

    def test_integrity_is_first_check(self):
        from src.startup.checks import DEFAULT_CHECKS
        assert DEFAULT_CHECKS[0][0] == "integrity"

    def test_all_ids_unique(self):
        from src.startup.checks import DEFAULT_CHECKS
        ids = [cid for cid, _ in DEFAULT_CHECKS]
        assert len(ids) == len(set(ids))

    def test_all_callables_produce_check_results(self):
        from src.startup.checks import DEFAULT_CHECKS
        from src.startup.checker import CheckResult
        for cid, fn in DEFAULT_CHECKS:
            try:
                r = fn()
                assert isinstance(r, CheckResult), f"{cid} did not return CheckResult"
            except TypeError:
                # some (e.g. hardware) take an optional app_version arg
                r = fn("test-version")
                assert isinstance(r, CheckResult)
