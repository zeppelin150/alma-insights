"""
Unit tests for src/startup/checks/integrity.py

Covers:
  - Missing checksums.json → pass with "dev checkout" note
  - Empty file entries → warn
  - All hashes match → pass
  - A few mismatches (<= threshold) → warn naming the files
  - Many mismatches (> threshold) → fail critical
  - The `_verify_sample` helper correctly identifies mismatches
  - Sampling respects the _SAMPLE_SIZE cap

Run: python -m pytest tests/test_phase4_integrity.py -x -v
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.startup.checks import integrity


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────

@pytest.fixture
def staged_tree(tmp_path, monkeypatch):
    """
    Build a tiny tree of .py files plus a matching checksums.json.
    The checksums file sits at tmp_path/checksums.json to mirror the
    production layout (integrity._CHECKSUMS_FILE resolves relative to
    the app's cwd, which we redirect with monkeypatch).
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(integrity, "_CHECKSUMS_FILE", Path("checksums.json"))

    files = {
        "main.py": b"print('hi')\n",
        "src/__init__.py": b"VERSION = '1.0.0'\n",
        "src/a.py": b"x = 1\n",
        "src/b.py": b"y = 2\n",
    }
    for rel, data in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    manifest = {
        "schema_version": 1,
        "algorithm": "sha256",
        "files": {rel: hashlib.sha256(data).hexdigest() for rel, data in files.items()},
    }
    (tmp_path / "checksums.json").write_text(json.dumps(manifest))

    return tmp_path, files, manifest


# ──────────────────────────────────────────────────────────────────
# check_integrity entrypoint
# ──────────────────────────────────────────────────────────────────

class TestEntrypoint:
    def test_missing_manifest_passes_quietly(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(integrity, "_CHECKSUMS_FILE", Path("nope.json"))
        result = integrity.check_integrity()
        assert result.status == "pass"
        assert "dev checkout" in result.message.lower()
        assert result.critical is False

    def test_empty_entries_warn(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(integrity, "_CHECKSUMS_FILE", Path("checksums.json"))
        (tmp_path / "checksums.json").write_text(json.dumps({"files": {}}))
        result = integrity.check_integrity()
        assert result.status == "warn"

    def test_all_match_passes(self, staged_tree):
        result = integrity.check_integrity()
        assert result.status == "pass"
        assert result.critical is False

    def test_single_mismatch_warns_with_filename(self, staged_tree):
        tmp_path, _, _ = staged_tree
        # Tamper with one file on disk
        (tmp_path / "src" / "a.py").write_bytes(b"# tampered\n")
        result = integrity.check_integrity()
        assert result.status == "warn"
        assert "src/a.py" in result.message

    def test_many_mismatches_fail_critical(self, staged_tree, monkeypatch):
        tmp_path, files, _ = staged_tree
        # Tamper with every tracked file
        for rel in files:
            (tmp_path / rel).write_bytes(b"# tampered\n")
        # Force the sample to cover all 4 files
        monkeypatch.setattr(integrity, "_SAMPLE_SIZE", 10)
        monkeypatch.setattr(integrity, "_FAIL_THRESHOLD", 2)
        result = integrity.check_integrity()
        assert result.status == "fail"
        assert result.critical is True


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────

class TestHelpers:
    def test_sample_entries_caps_at_sample_size(self, monkeypatch):
        monkeypatch.setattr(integrity, "_SAMPLE_SIZE", 3)
        files = {f"f{i}.py": "deadbeef" for i in range(100)}
        sample = integrity._sample_entries(files)
        assert len(sample) == 3

    def test_sample_entries_returns_all_when_fewer_than_cap(self, monkeypatch):
        monkeypatch.setattr(integrity, "_SAMPLE_SIZE", 10)
        files = {"a.py": "x", "b.py": "y"}
        sample = integrity._sample_entries(files)
        assert len(sample) == 2

    def test_verify_sample_identifies_mismatches(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "ok.py").write_bytes(b"okdata")
        (tmp_path / "bad.py").write_bytes(b"wrong_on_disk")
        sample = [
            ("ok.py",  hashlib.sha256(b"okdata").hexdigest()),
            ("bad.py", hashlib.sha256(b"expected_something_else").hexdigest()),
        ]
        mismatches = integrity._verify_sample(sample)
        assert mismatches == ["bad.py"]

    def test_sha256_of_returns_none_for_missing(self, tmp_path):
        assert integrity._sha256_of(tmp_path / "nope.py") is None
