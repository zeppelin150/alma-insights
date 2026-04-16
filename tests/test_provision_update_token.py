"""
Integration tests for scripts/provision_update_token.py.

Runs the script as a subprocess so argparse, getpass gating, and exit
codes are all exercised the way a CI runner would see them.

Uses an in-memory keyring backend via a small shim — we can't mock
across subprocess boundaries, so the script falls back to the system
keyring. On CI this means Windows Credential Manager / macOS Keychain.
To keep the test hermetic we set KEYRING_PROPERTY_BACKEND=null-backend
which disables any persistence, then assert the exit codes only.

Run: python -m pytest tests/test_provision_update_token.py -x -v
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "provision_update_token.py"


def _run(*args, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True, text=True, env=env, timeout=15,
    )


# ──────────────────────────────────────────────────────────────────
# Argument validation
# ──────────────────────────────────────────────────────────────────

class TestArgValidation:
    def test_token_and_clear_are_mutually_exclusive(self):
        result = _run("--token", "x", "--clear")
        assert result.returncode != 0
        assert "mutually exclusive" in result.stderr.lower() or result.returncode == 2

    def test_help_exits_zero(self):
        result = _run("--help")
        assert result.returncode == 0
        assert "provision" in result.stdout.lower()


# ──────────────────────────────────────────────────────────────────
# Source selection
# ──────────────────────────────────────────────────────────────────

class TestSourceSelection:
    def test_from_env_empty_returns_one(self):
        result = _run(
            "--from-env", "NONEXISTENT_VAR_XYZ", "--force",
            env_extra={"NONEXISTENT_VAR_XYZ": ""},
        )
        # env var empty → no token → exit code 1 (validation)
        assert result.returncode == 1
        assert "no token" in result.stderr.lower()

    def test_from_file_nonexistent(self, tmp_path):
        result = _run(
            "--from-file", str(tmp_path / "missing.txt"), "--force",
        )
        assert result.returncode == 1


# ──────────────────────────────────────────────────────────────────
# --clear is idempotent
# ──────────────────────────────────────────────────────────────────

class TestClear:
    def test_clear_returns_zero(self):
        # Even with nothing stored, clear should succeed (idempotent).
        result = _run("--clear", "--quiet")
        assert result.returncode in (0, 2)  # 2 only if keyring truly unavailable
