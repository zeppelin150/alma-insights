"""
Unit tests for src/startup/env_guard.py

Covers:
  - Enforce sets all REQUIRED_ENV_VARS
  - Enforce strips localhost proxies, leaves remote proxies alone
  - Enforce is idempotent (second call returns empty changes list)
  - scan_for_issues detects residual .env files + unset vars + proxies
  - Module imports with only stdlib (no src.data.*)

Run: python -m pytest tests/test_env_guard.py -x -v
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.startup import env_guard


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────

@pytest.fixture
def clean_env(monkeypatch):
    """Remove every guarded variable + proxy var so we start from zero."""
    for key in list(env_guard.REQUIRED_ENV_VARS):
        monkeypatch.delenv(key, raising=False)
    for key in env_guard._PROXY_VARS:
        monkeypatch.delenv(key, raising=False)
    yield


# ──────────────────────────────────────────────────────────────────
# enforce()
# ──────────────────────────────────────────────────────────────────

class TestEnforce:
    def test_sets_all_required_vars(self, clean_env):
        changes = env_guard.enforce()
        for key, expected in env_guard.REQUIRED_ENV_VARS.items():
            assert os.environ.get(key) == expected
        assert len(changes) == len(env_guard.REQUIRED_ENV_VARS)

    def test_idempotent_second_call_is_no_op(self, clean_env):
        env_guard.enforce()
        changes = env_guard.enforce()
        assert changes == []

    def test_removes_localhost_http_proxy(self, clean_env, monkeypatch):
        monkeypatch.setenv("HTTP_PROXY", "http://localhost:8080")
        changes = env_guard.enforce()
        assert "HTTP_PROXY" not in os.environ
        assert any("localhost" in c for c in changes)

    def test_removes_127_0_0_1_proxy(self, clean_env, monkeypatch):
        monkeypatch.setenv("https_proxy", "http://127.0.0.1:3128")
        env_guard.enforce()
        assert "https_proxy" not in os.environ

    def test_leaves_remote_proxy_alone(self, clean_env, monkeypatch):
        # A corporate proxy at a real hostname — don't strip
        monkeypatch.setenv("HTTPS_PROXY", "http://corporate-proxy.internal:8080")
        env_guard.enforce()
        assert os.environ.get("HTTPS_PROXY") == "http://corporate-proxy.internal:8080"

    def test_overwrites_wrong_value(self, clean_env, monkeypatch):
        # Someone set TRANSFORMERS_OFFLINE=0 — enforce should fix it
        monkeypatch.setenv("TRANSFORMERS_OFFLINE", "0")
        changes = env_guard.enforce()
        assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
        assert any("TRANSFORMERS_OFFLINE" in c for c in changes)


# ──────────────────────────────────────────────────────────────────
# scan_for_issues()
# ──────────────────────────────────────────────────────────────────

class TestScan:
    def test_clean_env_reports_no_issues(self, clean_env):
        env_guard.enforce()
        # Use a tmp root so we don't accidentally pick up the real .env
        issues = env_guard.scan_for_issues(app_root=Path("/nonexistent-dir-xyz"))
        assert issues == []

    def test_detects_dot_env_file(self, clean_env, tmp_path):
        env_guard.enforce()
        (tmp_path / ".env").write_text("SECRET=leaked")
        issues = env_guard.scan_for_issues(app_root=tmp_path)
        assert any(".env" in i for i in issues)

    def test_detects_unset_required_var(self, clean_env, monkeypatch):
        env_guard.enforce()
        monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
        issues = env_guard.scan_for_issues(app_root=Path("/nonexistent-dir-xyz"))
        assert any("HF_HUB_OFFLINE" in i for i in issues)

    def test_detects_lingering_proxy(self, clean_env, monkeypatch):
        env_guard.enforce()
        monkeypatch.setenv("HTTP_PROXY", "http://corporate.example.com:8080")
        # enforce() already ran before we set this — scan should catch it
        issues = env_guard.scan_for_issues(app_root=Path("/nonexistent-dir-xyz"))
        assert any("HTTP_PROXY" in i for i in issues)


# ──────────────────────────────────────────────────────────────────
# Helper predicates
# ──────────────────────────────────────────────────────────────────

class TestIsLocalProxy:
    @pytest.mark.parametrize("url,expected", [
        ("http://localhost:8080", True),
        ("https://localhost/", True),
        ("http://127.0.0.1:3128", True),
        ("http://LOCALHOST:9000", True),
        ("http://corporate-proxy.internal:8080", False),
        ("http://10.0.0.5:3128", False),
        ("", False),
    ])
    def test_classification(self, url, expected):
        assert env_guard._is_local_proxy(url) is expected


# ──────────────────────────────────────────────────────────────────
# current_state()
# ──────────────────────────────────────────────────────────────────

class TestCurrentState:
    def test_reports_all_keys(self, clean_env):
        env_guard.enforce()
        state = env_guard.current_state()
        assert set(state.keys()) == set(env_guard.REQUIRED_ENV_VARS.keys())
        assert all(v is not None for v in state.values())

    def test_reports_none_when_missing(self, clean_env, monkeypatch):
        env_guard.enforce()
        monkeypatch.delenv("WANDB_DISABLED", raising=False)
        state = env_guard.current_state()
        assert state["WANDB_DISABLED"] is None


# ──────────────────────────────────────────────────────────────────
# Module must import with only stdlib
# ──────────────────────────────────────────────────────────────────

class TestModuleIsolation:
    def test_no_heavy_imports(self):
        """Re-import env_guard in a fresh subprocess, assert no torch / HF / pyside."""
        import subprocess
        import sys
        import textwrap
        code = textwrap.dedent("""
            import sys
            # Pre-populate a sentinel so we can tell if these get imported
            forbidden = {"torch", "transformers", "sentence_transformers",
                         "huggingface_hub", "PySide6", "pandas", "numpy"}
            import importlib
            env = importlib.import_module("src.startup.env_guard")
            env.enforce()
            loaded = set(sys.modules) & forbidden
            print("LOADED:" + ",".join(sorted(loaded)))
        """)
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=15,
            cwd=Path(__file__).resolve().parent.parent,
        )
        assert result.returncode == 0, result.stderr
        last = [ln for ln in result.stdout.strip().splitlines() if ln.startswith("LOADED:")]
        assert last, result.stdout
        # No heavy modules may have been loaded as a side effect
        assert last[0] == "LOADED:"
