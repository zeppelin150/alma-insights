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
    """Behavioural contracts for check_gemini_oauth. The subprocess-based
    probe was retired on 2026-04-17 (see TestGeminiOAuthFileBased). These
    tests target the current file-based implementation."""

    def test_cli_missing_fails(self, monkeypatch):
        monkeypatch.setattr(gem_check, "_resolve_cli_path", lambda: None)
        result = gem_check.check_gemini_oauth()
        assert result.status == "fail"
        assert "not found" in result.message.lower()

    def test_unauthenticated_detector(self):
        """_looks_unauthenticated is retained for backwards compat with any
        caller that still imports it."""
        assert gem_check._looks_unauthenticated("Error: not logged in")
        assert gem_check._looks_unauthenticated("auth error: refresh failed")
        assert not gem_check._looks_unauthenticated("fine")
        assert not gem_check._looks_unauthenticated("")

    def test_cli_missing_is_non_critical(self, monkeypatch):
        """Missing CLI should not block Continue — user must be able to reach Settings."""
        monkeypatch.setattr(gem_check, "_resolve_cli_path", lambda: None)
        result = gem_check.check_gemini_oauth()
        assert result.critical is False

    def test_missing_oauth_file_is_non_critical(self, monkeypatch, tmp_path):
        fake_cli = tmp_path / "gemini"
        fake_cli.write_text("")
        monkeypatch.setattr(gem_check, "_resolve_cli_path", lambda: fake_cli)
        monkeypatch.setattr(gem_check, "_oauth_file_path", lambda: tmp_path / "nope.json")
        result = gem_check.check_gemini_oauth()
        assert result.critical is False

    def test_malformed_oauth_file_is_non_critical(self, monkeypatch, tmp_path):
        fake_cli = tmp_path / "gemini"
        fake_cli.write_text("")
        bad = tmp_path / "google_accounts.json"
        bad.write_text("{not-valid-json")
        monkeypatch.setattr(gem_check, "_resolve_cli_path", lambda: fake_cli)
        monkeypatch.setattr(gem_check, "_oauth_file_path", lambda: bad)
        result = gem_check.check_gemini_oauth()
        assert result.status == "fail"
        assert result.critical is False

    def test_probe_timeout_is_short(self):
        """Regression guard: if anyone ever re-introduces a subprocess
        timeout, keep it short."""
        assert gem_check._TIMEOUT_SECONDS <= 5, (
            f"_TIMEOUT_SECONDS={gem_check._TIMEOUT_SECONDS}s is too long."
        )


class TestGeminiOAuthFileBased:
    """Bug-bash 2026-04-17 — the subprocess probe stalled the splash for
    ~55s on every launch because Python's subprocess timeout could not
    reap orphaned node.exe grandchildren of the `gemini --prompt` call.
    Replaced with a file-based check that reads
    ~/.gemini/google_accounts.json directly. Runs in microseconds, no
    cmd.exe flash, no hang risk."""

    def test_no_subprocess_in_check_function(self):
        """Primary contract: the probe must not spawn any subprocess."""
        import inspect
        src = inspect.getsource(gem_check.check_gemini_oauth)
        # We allow `subprocess` to still be imported (module-level) but
        # the check function must not call subprocess.run / Popen.
        assert "subprocess.run" not in src, (
            "check_gemini_oauth must not call subprocess.run — the blocking "
            "probe is what stalled the splash. Use a file-based check."
        )
        assert "subprocess.Popen" not in src, (
            "check_gemini_oauth must not spawn processes."
        )

    def test_missing_cli_still_fails(self, monkeypatch):
        """Keep the CLI-not-found signal — users need to know if the bundled
        CLI is gone."""
        monkeypatch.setattr(gem_check, "_resolve_cli_path", lambda: None)
        result = gem_check.check_gemini_oauth()
        assert result.status == "fail"
        assert "not found" in result.message.lower()
        assert result.critical is False  # Bug #2 — non-critical

    def test_missing_oauth_file_is_warn_or_fail(self, monkeypatch, tmp_path):
        """CLI present, no google_accounts.json → not authenticated."""
        fake_cli = tmp_path / "gemini"
        fake_cli.write_text("")
        monkeypatch.setattr(gem_check, "_resolve_cli_path", lambda: fake_cli)
        monkeypatch.setattr(gem_check, "_oauth_file_path", lambda: tmp_path / "missing.json")
        result = gem_check.check_gemini_oauth()
        assert result.status in ("fail", "warn")
        assert result.critical is False

    def test_empty_oauth_file_is_not_pass(self, monkeypatch, tmp_path):
        """File exists but has no refresh token → not authenticated."""
        fake_cli = tmp_path / "gemini"
        fake_cli.write_text("")
        monkeypatch.setattr(gem_check, "_resolve_cli_path", lambda: fake_cli)
        bad = tmp_path / "google_accounts.json"
        bad.write_text("{}")
        monkeypatch.setattr(gem_check, "_oauth_file_path", lambda: bad)
        result = gem_check.check_gemini_oauth()
        assert result.status != "pass"

    def test_valid_oauth_file_passes(self, monkeypatch, tmp_path):
        """CLI present, google_accounts.json has a refresh token → pass."""
        fake_cli = tmp_path / "gemini"
        fake_cli.write_text("")
        monkeypatch.setattr(gem_check, "_resolve_cli_path", lambda: fake_cli)
        good = tmp_path / "google_accounts.json"
        good.write_text('{"refresh_token": "1//abc123"}')
        monkeypatch.setattr(gem_check, "_oauth_file_path", lambda: good)
        result = gem_check.check_gemini_oauth()
        assert result.status == "pass", (
            f"Expected pass with valid refresh_token; got {result.status} — {result.message}"
        )

    def test_check_completes_in_under_100ms(self, monkeypatch, tmp_path):
        """The whole point of this refactor: no subprocess, no hang."""
        import time
        fake_cli = tmp_path / "gemini"
        fake_cli.write_text("")
        monkeypatch.setattr(gem_check, "_resolve_cli_path", lambda: fake_cli)
        good = tmp_path / "google_accounts.json"
        good.write_text('{"refresh_token": "1//abc123"}')
        monkeypatch.setattr(gem_check, "_oauth_file_path", lambda: good)

        t0 = time.monotonic()
        gem_check.check_gemini_oauth()
        elapsed_ms = (time.monotonic() - t0) * 1000
        assert elapsed_ms < 100, (
            f"File-based probe took {elapsed_ms:.0f}ms — should be <100ms."
        )


class TestCheckerFailedIds:
    """Gate 2 of bug #2 — caller must be able to see which checks failed
    so that main.py can route the user to the right Settings page."""

    def test_failed_check_ids_is_empty_when_all_pass(self):
        from src.startup.checker import Checker, CheckResult

        def ok():
            return CheckResult(id="ok", name="ok", status="pass", critical=True)

        c = Checker([("ok", ok)])
        c.run_all()
        assert c.failed_check_ids == []

    def test_failed_check_ids_lists_warn_and_fail(self):
        from src.startup.checker import Checker, CheckResult

        def ok():
            return CheckResult(id="ok", name="ok", status="pass", critical=True)

        def warned():
            return CheckResult(id="gemini_oauth", name="g", status="warn", critical=False)

        def failed():
            return CheckResult(id="cli", name="cli", status="fail", critical=False)

        c = Checker([("ok", ok), ("gemini_oauth", warned), ("cli", failed)])
        c.run_all()
        assert set(c.failed_check_ids) == {"gemini_oauth", "cli"}


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

    # ── Bug-bash 2026-04-17 ────────────────────────────────────────
    # The check pointed at `data/alma_insights.db` but the real DB is
    # `data/local_warehouse.db` (see connection_factory.py:34 and
    # db_manager.py:16). The check also queried the wrong column on
    # schema_migrations — the actual schema is (filename, applied_at),
    # not (version). Result: check always hit the "no DB yet" branch
    # and silently created a 0-byte stub file.

    def test_db_path_points_at_local_warehouse(self):
        """The default _DB_PATH must match the path the app actually uses."""
        from src.data.connection_factory import DEFAULT_DB_PATH
        assert db_check._DB_PATH.name == DEFAULT_DB_PATH.name, (
            f"database check reads {db_check._DB_PATH.name!r} but the app "
            f"actually uses {DEFAULT_DB_PATH.name!r}. The check is looking "
            "at the wrong file."
        )

    def test_real_schema_migrations_layout(self, tmp_path, monkeypatch):
        """Reads schema version from a filename-based migrations table."""
        db = tmp_path / "local_warehouse.db"
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "CREATE TABLE schema_migrations "
                "(filename TEXT PRIMARY KEY, applied_at TEXT)"
            )
            conn.executemany(
                "INSERT INTO schema_migrations VALUES (?, ?)",
                [
                    ("001_initial_baseline.sql", "2026-04-15"),
                    ("002_source_warehouse.sql", "2026-04-15"),
                    ("016_warehouse_enrichment.sql", "2026-04-15"),
                ],
            )
            conn.execute("CREATE TABLE tickets (id INTEGER)")
            conn.execute("INSERT INTO tickets VALUES (1)")
        monkeypatch.setattr(db_check, "_DB_PATH", db)

        result = db_check.check_database()
        assert result.status == "pass"
        # The check should report the highest migration number (16), not
        # fail silently because the column isn't named `version`.
        assert "16" in result.message, (
            f"Expected the highest migration number (16) in the message, "
            f"got: {result.message!r}"
        )


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
    def test_default_checks_has_twelve_entries(self):
        """2026-04-17 added rollback → 11; 2026-05-07 added zombies → 12."""
        from src.startup.checks import DEFAULT_CHECKS
        assert len(DEFAULT_CHECKS) == 12

    def test_integrity_is_first_check(self):
        from src.startup.checks import DEFAULT_CHECKS
        assert DEFAULT_CHECKS[0][0] == "integrity"

    def test_rollback_is_registered(self):
        from src.startup.checks import DEFAULT_CHECKS
        ids = [cid for cid, _ in DEFAULT_CHECKS]
        assert "rollback" in ids, (
            "Rollback check must be registered so users see pending "
            "rollback state during startup."
        )

    def test_zombies_is_registered(self):
        """2026-05-07: zombies sweep is registered after rollback so the
        splash kills orphan gemini.exe / node.exe before any bridge boot."""
        from src.startup.checks import DEFAULT_CHECKS
        ids = [cid for cid, _ in DEFAULT_CHECKS]
        assert "zombies" in ids, (
            "Zombies sweep must be registered to clean orphan bridge "
            "subprocesses before the user reaches the main window."
        )

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


# ──────────────────────────────────────────────────────────────────
# Check 11 — pending rollback (bug-bash 2026-04-17)
# ──────────────────────────────────────────────────────────────────

class TestRollbackCheck:
    """The rollback module writes `data/rollback_state.json` + keeps
    `_src_previous/` / `_config_previous/` / `_migrations_previous/`
    dirs around after an update. The main app eventually calls
    clear_state_if_stable() 65s after MainWindow opens, but the user
    has no visibility into the state during the splash. This check
    surfaces it."""

    def _import(self):
        from src.startup.checks import rollback as rb_check
        return rb_check

    def test_no_state_passes_clean(self, tmp_path, monkeypatch):
        rb_check = self._import()
        monkeypatch.setattr(rb_check, "_PROJECT_ROOT", tmp_path)
        result = rb_check.check_rollback()
        assert result.status == "pass"
        assert result.critical is False

    def test_stale_previous_dirs_without_state_warn(self, tmp_path, monkeypatch):
        """An earlier update left backup dirs but rollback_state.json was
        already cleaned — surface as warn so the user can manually prune."""
        rb_check = self._import()
        (tmp_path / "_src_previous").mkdir()
        (tmp_path / "data").mkdir()
        monkeypatch.setattr(rb_check, "_PROJECT_ROOT", tmp_path)
        result = rb_check.check_rollback()
        assert result.status == "warn"
        assert result.critical is False
        assert "previous" in result.message.lower()

    def test_active_grace_window_is_warn(self, tmp_path, monkeypatch):
        """A rollback state inside the grace window means the app just
        applied an update and is watching for crashes. Inform, don't block."""
        import json
        from datetime import datetime, timezone
        rb_check = self._import()
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "rollback_state.json").write_text(json.dumps({
            "schema_version": 1,
            "previous": "v1.0.0",
            "new": "v1.0.1",
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "grace_window_s": 60,
        }))
        monkeypatch.setattr(rb_check, "_PROJECT_ROOT", tmp_path)
        result = rb_check.check_rollback()
        assert result.status == "warn"
        assert result.critical is False
        assert "grace" in result.message.lower() or "v1.0.1" in result.message

    def test_check_is_non_critical_always(self, tmp_path, monkeypatch):
        """Rollback check must never block Continue — it's informational."""
        import json
        from datetime import datetime, timezone, timedelta
        rb_check = self._import()
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        expired = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        (data_dir / "rollback_state.json").write_text(json.dumps({
            "applied_at": expired, "grace_window_s": 60,
            "previous": "v1", "new": "v2",
        }))
        (tmp_path / "_src_previous").mkdir()
        monkeypatch.setattr(rb_check, "_PROJECT_ROOT", tmp_path)
        result = rb_check.check_rollback()
        assert result.critical is False
