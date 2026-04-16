"""
Phase 1 E2E smoke test — wires all four Phase 1 modules end-to-end.

Exercised:
  1. env_guard.enforce() sets HIPAA air-gap variables before heavy imports
  2. pat_store stores a secret through the OS keyring (mocked in-memory)
  3. hardware.load_or_profile() writes + reads data/hardware_profile.json
  4. crash_handler.install() captures an uncaught exception into a report
  5. All four modules coexist in a single process without import conflicts

This test mirrors the startup sequence main.py will follow, proving the
pieces compose correctly before the splash UI is bolted on in Phase 2.

Run: python -m pytest tests/test_phase1_e2e.py -x -v
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import keyring
import keyring.backend
import keyring.errors
import pytest

from src.core import crash_handler
from src.startup import env_guard, hardware


# ──────────────────────────────────────────────────────────────────
# In-memory keyring backend (copied — kept local to make this file
# runnable in isolation without cross-test imports)
# ──────────────────────────────────────────────────────────────────

class _InMemoryKeyring(keyring.backend.KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self):
        self._store: dict[tuple[str, str], str] = {}

    def set_password(self, service, username, password):
        self._store[(service, username)] = password

    def get_password(self, service, username):
        return self._store.get((service, username))

    def delete_password(self, service, username):
        if (service, username) not in self._store:
            raise keyring.errors.PasswordDeleteError("not found")
        del self._store[(service, username)]


@pytest.fixture
def phase1_environment(tmp_path, monkeypatch):
    """
    Stand up an isolated Phase-1 environment:
      * tmp home directory for pat_store's JSON file
      * in-memory keyring backend
      * tmp crash_reports directory
      * tmp hardware profile path
    """
    # pat_store — redirect ~/.alma-insights
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    # Reimport pat_store so its module-level path constants pick up the new home
    import importlib
    from src.data import pat_store
    importlib.reload(pat_store)

    # keyring — swap to in-memory
    original_backend = keyring.get_keyring()
    keyring.set_keyring(_InMemoryKeyring())

    # crash_reports — tmp dir
    monkeypatch.setattr(crash_handler, "_CRASH_DIR", tmp_path / "crash_reports")
    monkeypatch.setattr(crash_handler, "_installed", False)
    monkeypatch.setattr(crash_handler, "_previous_hook", None)

    # hardware profile — tmp path
    monkeypatch.setattr(hardware, "_PROFILE_PATH", tmp_path / "hardware_profile.json")

    # Stable hardware detection so the test is reproducible across machines
    monkeypatch.setattr(hardware._detect, "accelerator", lambda: ("cpu", None, 0))
    monkeypatch.setattr(hardware._detect, "ram_gb", lambda: 8)

    # Clean env for enforce()
    for key in list(env_guard.REQUIRED_ENV_VARS):
        monkeypatch.delenv(key, raising=False)
    for key in env_guard._PROXY_VARS:
        monkeypatch.delenv(key, raising=False)

    yield {
        "tmp_path": tmp_path,
        "pat_store": pat_store,
    }

    # Teardown
    keyring.set_keyring(original_backend)


# ──────────────────────────────────────────────────────────────────
# E2E sequence
# ──────────────────────────────────────────────────────────────────

class TestPhase1EndToEnd:
    def test_full_startup_sequence(self, phase1_environment):
        """
        Replay what main.py will do at launch:
          (1) enforce env guard
          (2) install crash handler
          (3) migrate legacy credentials + store a new one
          (4) profile hardware
          (5) force a crash; verify it is captured
        """
        pat_store = phase1_environment["pat_store"]
        tmp_path = phase1_environment["tmp_path"]

        # Step 1 — env guard
        changes = env_guard.enforce()
        assert len(changes) >= len(env_guard.REQUIRED_ENV_VARS)
        assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
        assert os.environ["HF_HUB_OFFLINE"] == "1"

        # Step 2 — crash handler
        original_hook = sys.excepthook
        try:
            crash_handler.install()
            assert sys.excepthook is crash_handler._hook

            # Step 3 — keyring credential store
            assert pat_store.save_pat("ldpat_e2etest_abcd") is True
            assert pat_store.load_pat() == "ldpat_e2etest_abcd"
            assert pat_store.has_pat() is True

            ok, backend = pat_store.keyring_available()
            assert ok is True
            assert "InMemory" in backend

            # Step 4 — hardware profile
            profile = hardware.load_or_profile(current_version="phase1-e2e")
            assert profile["schema_version"] == 1
            assert profile["app_version"] == "phase1-e2e"
            assert profile["accelerator"] == "cpu"
            assert (tmp_path / "hardware_profile.json").exists()
            # Reload returns same profile from cache
            profile2 = hardware.load_or_profile(current_version="phase1-e2e")
            assert profile2 == profile

            # Step 5 — crash captured
            try:
                raise RuntimeError("simulated crash: token=supersecret123")
            except RuntimeError:
                crash_handler._hook(*sys.exc_info())

            reports = crash_handler.list_reports()
            assert len(reports) == 1
            report = json.loads(reports[0].read_text())
            assert report["exception_type"] == "RuntimeError"
            # Secret redacted
            assert "supersecret123" not in report["exception_message"]
            # Hardware profile embedded in the crash report
            assert report["hardware_profile"]["accelerator"] == "cpu"

        finally:
            sys.excepthook = original_hook

    def test_legacy_credentials_migration_in_sequence(self, phase1_environment):
        """Legacy credentials.json migrates cleanly when present on disk."""
        pat_store = phase1_environment["pat_store"]
        tmp_path = phase1_environment["tmp_path"]

        # Seed a legacy file with a mix of secrets and settings
        legacy_dir = tmp_path / ".alma-insights"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        legacy_file = legacy_dir / "credentials.json"
        legacy_file.write_text(json.dumps({
            "lightdash_pat": "ldpat_legacy_xxxx",
            "anthropic_api_key": "sk-ant-legacy",
            "zendesk_subject": "Kept Subject",
            "custom_field_id": 42,
        }))

        moved = pat_store.migrate_legacy_credentials()

        assert moved == 2  # lightdash_pat + anthropic_api_key
        assert pat_store.load_pat() == "ldpat_legacy_xxxx"
        assert pat_store.load_setting("anthropic_api_key") == "sk-ant-legacy"
        # Non-secrets survive in ui_state.json
        assert pat_store.load_setting("zendesk_subject") == "Kept Subject"
        assert pat_store.load_setting("custom_field_id") == 42
        # Legacy file renamed
        assert not legacy_file.exists()
        assert legacy_file.with_suffix(".json.migrated").exists()

    def test_env_scan_detects_post_enforce_regressions(self, phase1_environment, monkeypatch):
        """After enforce(), a late-added proxy should surface in scan_for_issues."""
        env_guard.enforce()
        monkeypatch.setenv("HTTP_PROXY", "http://corporate.example.com:8080")
        issues = env_guard.scan_for_issues(app_root=Path("/nonexistent-dir-xyz"))
        assert any("HTTP_PROXY" in i for i in issues)


# ──────────────────────────────────────────────────────────────────
# Module coexistence smoke test
# ──────────────────────────────────────────────────────────────────

class TestModulesCoexist:
    def test_all_phase1_modules_importable(self):
        """Importing all four modules in one process must not clash."""
        from src.core import crash_handler as ch  # noqa: F401
        from src.data import pat_store  # noqa: F401
        from src.startup import env_guard  # noqa: F401
        from src.startup import hardware  # noqa: F401
