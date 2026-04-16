"""
Unit tests for installer/uninstall.py

Covers:
  - Default install-dir picker returns platform-appropriate path
  - preserve_user_data moves app/data to a sibling backup
  - preserve_user_data is a no-op when data/ doesn't exist
  - remove_install_dir deletes the tree, returns False on error
  - clear_keyring_entries only touches Alma-scoped keys
  - --dry-run performs no filesystem writes

Runs the uninstaller as an imported module; a few tests also invoke
the CLI surface via subprocess to exercise argparse.

Run: python -m pytest tests/test_uninstaller.py -x -v
"""

from __future__ import annotations

import importlib.util
import os
import platform
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent


# ──────────────────────────────────────────────────────────────────
# Load uninstall.py as a module (not in a package)
# ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def uninstaller():
    spec = importlib.util.spec_from_file_location(
        "uninstall",
        _REPO_ROOT / "installer" / "uninstall.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# ──────────────────────────────────────────────────────────────────
# default_install_dir
# ──────────────────────────────────────────────────────────────────

class TestDefaultDir:
    def test_reasonable_on_current_platform(self, uninstaller):
        d = uninstaller.default_install_dir()
        assert "AlmaInsights" in str(d) or ".almainsights" in str(d).lower()
        # Must be under the user's home on every platform
        assert str(Path.home()) in str(d) or "AppData" in str(d)


# ──────────────────────────────────────────────────────────────────
# preserve_user_data
# ──────────────────────────────────────────────────────────────────

class TestPreserveUserData:
    def test_moves_data_dir_to_backup(self, uninstaller, tmp_path):
        install = tmp_path / "AlmaInsights"
        (install / "app" / "data").mkdir(parents=True)
        (install / "app" / "data" / "alma.db").write_text("stub")

        backup = uninstaller.preserve_user_data(install, dry_run=False)

        assert backup is not None
        assert backup.is_dir()
        assert (backup / "alma.db").read_text() == "stub"
        assert not (install / "app" / "data").exists()

    def test_no_data_returns_none(self, uninstaller, tmp_path):
        install = tmp_path / "AlmaInsights"
        install.mkdir()
        assert uninstaller.preserve_user_data(install, dry_run=False) is None

    def test_dry_run_does_not_move(self, uninstaller, tmp_path):
        install = tmp_path / "AlmaInsights"
        (install / "app" / "data").mkdir(parents=True)
        (install / "app" / "data" / "x.db").write_text("keep me")
        uninstaller.preserve_user_data(install, dry_run=True)
        assert (install / "app" / "data" / "x.db").exists()


# ──────────────────────────────────────────────────────────────────
# remove_install_dir
# ──────────────────────────────────────────────────────────────────

class TestRemoveInstall:
    def test_removes_the_tree(self, uninstaller, tmp_path):
        install = tmp_path / "AlmaInsights"
        install.mkdir()
        (install / "marker").write_text("x")
        assert uninstaller.remove_install_dir(install, dry_run=False) is True
        assert not install.exists()

    def test_dry_run_keeps_tree(self, uninstaller, tmp_path):
        install = tmp_path / "AlmaInsights"
        install.mkdir()
        uninstaller.remove_install_dir(install, dry_run=True)
        assert install.exists()


# ──────────────────────────────────────────────────────────────────
# clear_keyring_entries
# ──────────────────────────────────────────────────────────────────

class TestClearKeyring:
    """
    Patch the real pat_store's functions directly. `patch.dict(sys.modules, ...)`
    doesn't help here because src.data.pat_store is typically already imported
    by earlier tests; Python's `from src.data import pat_store` returns the
    cached attribute on `src.data`, not the sys.modules override.
    """

    def test_only_deletes_known_secret_keys(self, uninstaller):
        from src.data import pat_store
        calls: list[str] = []

        def load_setting(key, default=None):
            return "value" if key == "lightdash_pat" else ""

        def delete_secret(key):
            calls.append(key)
            return True

        with patch.object(pat_store, "load_setting", side_effect=load_setting), \
             patch.object(pat_store, "_delete_secret", side_effect=delete_secret):
            removed = uninstaller.clear_keyring_entries(dry_run=False)

        assert removed == 1
        assert calls == ["lightdash_pat"]

    def test_dry_run_counts_without_deleting(self, uninstaller):
        from src.data import pat_store
        delete_mock = MagicMock()
        with patch.object(pat_store, "load_setting", lambda k, d=None: "x"), \
             patch.object(pat_store, "_delete_secret", delete_mock):
            removed = uninstaller.clear_keyring_entries(dry_run=True)
        assert removed == len(uninstaller._SECRET_KEYS)
        delete_mock.assert_not_called()


# ──────────────────────────────────────────────────────────────────
# CLI surface
# ──────────────────────────────────────────────────────────────────

class TestCli:
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(_REPO_ROOT / "installer" / "uninstall.py"), *args],
            capture_output=True, text=True, timeout=10,
        )

    def test_help_exits_zero(self):
        result = self._run("--help")
        assert result.returncode == 0
        assert "uninstall" in result.stdout.lower()

    def test_missing_install_dir_exits_2(self, tmp_path):
        missing = tmp_path / "nowhere"
        result = self._run("--install-dir", str(missing), "--yes")
        assert result.returncode == 2

    def test_dry_run_removes_nothing(self, tmp_path):
        install = tmp_path / "AlmaInsights"
        (install / "app" / "data").mkdir(parents=True)
        (install / "marker").write_text("x")
        result = self._run(
            "--install-dir", str(install), "--yes",
            "--keep-secrets", "--dry-run",
        )
        assert result.returncode == 0
        assert install.exists()  # still there
        assert (install / "marker").exists()
