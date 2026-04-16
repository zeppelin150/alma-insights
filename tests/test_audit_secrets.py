"""
Unit tests for scripts/audit_secrets.py

Covers:
  - Regex patterns fire on realistic credential shapes
  - scan_working_tree finds matches in plain files
  - Skip list excludes data/models, .git, .claude, tests/, docs/
  - CLI returns exit code 0 on a clean tree, 1 on findings

Run: python -m pytest tests/test_audit_secrets.py -x -v
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "audit_secrets.py"


@pytest.fixture(scope="session")
def audit():
    spec = importlib.util.spec_from_file_location("audit_secrets", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# ──────────────────────────────────────────────────────────────────
# Regex patterns
# ──────────────────────────────────────────────────────────────────

class TestPatterns:
    def test_lightdash_pat(self, audit):
        p = audit._PATTERNS["lightdash_pat"]
        assert p.search("pat = ldpat_AbCdEf1234567890")
        assert not p.search("ldpat_short")

    def test_anthropic_api_key(self, audit):
        p = audit._PATTERNS["anthropic_api_key"]
        assert p.search("sk-ant-0123456789abcdefABCDEF01")
        assert not p.search("sk-ant-tooShort")

    def test_github_fine_grained(self, audit):
        p = audit._PATTERNS["github_fine_grained"]
        assert p.search("ghp_" + "A" * 36)
        assert p.search("gho_" + "B" * 36)
        assert not p.search("ghp_short")

    def test_google_api_key(self, audit):
        p = audit._PATTERNS["google_api_key"]
        # AIza + exactly 35 base64url chars
        sample = "AIza" + "A" * 35
        assert p.search(sample)
        assert not p.search("AIzaTooShort")

    def test_generic_bearer(self, audit):
        p = audit._PATTERNS["generic_bearer"]
        assert p.search("Authorization: Bearer " + "x" * 50)
        assert not p.search("Bearer short")


# ──────────────────────────────────────────────────────────────────
# scan_working_tree
# ──────────────────────────────────────────────────────────────────

class TestScanTree:
    def test_clean_tree_is_empty(self, audit, tmp_path):
        (tmp_path / "ok.txt").write_text("nothing suspicious here")
        assert audit.scan_working_tree(tmp_path) == []

    def test_finds_leaked_key(self, audit, tmp_path):
        (tmp_path / "leak.py").write_text(
            'token = "ghp_' + "A" * 36 + '"\n'
        )
        findings = audit.scan_working_tree(tmp_path)
        assert len(findings) == 1
        src, pattern, line_no, _ = findings[0]
        assert src == "leak.py"
        assert pattern == "github_fine_grained"

    def test_skips_tests_directory(self, audit, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "fixtures.py").write_text(
            'FAKE = "sk-ant-' + "a" * 40 + '"\n'
        )
        assert audit.scan_working_tree(tmp_path) == []

    def test_skips_docs_directory(self, audit, tmp_path):
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "UPDATES.md").write_text(
            "example token shape: ghp_" + "X" * 36 + "\n"
        )
        assert audit.scan_working_tree(tmp_path) == []

    def test_skips_claude_directory(self, audit, tmp_path):
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "settings.local.json").write_text(
            'AIza' + "y" * 35
        )
        assert audit.scan_working_tree(tmp_path) == []

    def test_skips_binary_suffixes(self, audit, tmp_path):
        (tmp_path / "blob.zip").write_bytes(b"ghp_" + b"Z" * 36)
        assert audit.scan_working_tree(tmp_path) == []


# ──────────────────────────────────────────────────────────────────
# CLI entrypoint
# ──────────────────────────────────────────────────────────────────

class TestCli:
    def _run(self, tmp_path: Path):
        return subprocess.run(
            [sys.executable, str(_SCRIPT), "--root", str(tmp_path), "--no-history"],
            capture_output=True, text=True, timeout=10,
        )

    def test_clean_tree_exits_zero(self, tmp_path):
        (tmp_path / "fine.py").write_text("print('hi')")
        result = self._run(tmp_path)
        assert result.returncode == 0
        assert "No credential-shaped" in result.stdout

    def test_dirty_tree_exits_one(self, tmp_path):
        (tmp_path / "leak.py").write_text(
            "token = 'ghp_" + "A" * 36 + "'\n"
        )
        result = self._run(tmp_path)
        assert result.returncode == 1
        assert "finding" in result.stdout.lower()

    def test_missing_root_exits_two(self, tmp_path):
        missing = tmp_path / "does-not-exist"
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "--root", str(missing), "--no-history"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 2
