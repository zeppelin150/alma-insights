"""
Unit tests for src/updater/release_manifest.py

Covers:
  - sha256_of matches hashlib output
  - build_manifest scans a directory and produces the full schema
  - write_manifest + load_manifest round-trip
  - lookup_artifact returns (sha, size) for known and unknown names
  - Missing artifacts directory raises FileNotFoundError
  - Only the configured globs are included

Run: python -m pytest tests/test_release_manifest.py -x -v
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.updater import release_manifest as rm


# ──────────────────────────────────────────────────────────────────
# sha256_of
# ──────────────────────────────────────────────────────────────────

class TestSha256Of:
    def test_matches_hashlib(self, tmp_path):
        blob = b"release artifact bytes"
        f = tmp_path / "x.zip"
        f.write_bytes(blob)
        assert rm.sha256_of(f) == hashlib.sha256(blob).hexdigest()

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.zip"
        f.write_bytes(b"")
        assert rm.sha256_of(f) == hashlib.sha256(b"").hexdigest()


# ──────────────────────────────────────────────────────────────────
# build_manifest
# ──────────────────────────────────────────────────────────────────

class TestBuildManifest:
    def _seed(self, tmp_path, files: dict[str, bytes]) -> Path:
        for name, data in files.items():
            (tmp_path / name).write_bytes(data)
        return tmp_path

    def test_missing_dir_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            rm.build_manifest("v1", tmp_path / "nope")

    def test_scans_zips(self, tmp_path):
        self._seed(tmp_path, {
            "AlmaInsights-win64.zip": b"win",
            "AlmaInsights-macOS-arm64.zip": b"mac-arm",
            "AlmaInsights-macOS-x64.zip":  b"mac-x64",
        })
        manifest = rm.build_manifest("v9.3.0", tmp_path)

        assert manifest["schema_version"] == 1
        assert manifest["version"] == "v9.3.0"
        assert set(manifest["artifacts"]) == {
            "AlmaInsights-win64.zip",
            "AlmaInsights-macOS-arm64.zip",
            "AlmaInsights-macOS-x64.zip",
        }
        for name, entry in manifest["artifacts"].items():
            assert len(entry["sha256"]) == 64
            assert entry["size"] > 0

    def test_ignores_non_matching_files(self, tmp_path):
        self._seed(tmp_path, {
            "release.zip": b"keep",
            "README.txt": b"skip",
            "logs.log": b"skip",
        })
        manifest = rm.build_manifest("v1", tmp_path)
        assert list(manifest["artifacts"]) == ["release.zip"]

    def test_includes_notes_url(self, tmp_path):
        self._seed(tmp_path, {"x.zip": b"x"})
        manifest = rm.build_manifest(
            "v1", tmp_path,
            notes_url="https://github.com/x/y/releases/tag/v1",
        )
        assert manifest["notes_url"].endswith("/v1")

    def test_released_at_is_iso8601(self, tmp_path):
        self._seed(tmp_path, {"x.zip": b"x"})
        manifest = rm.build_manifest("v1", tmp_path)
        # ISO-8601 UTC has a T and a + or Z
        assert "T" in manifest["released_at"]


# ──────────────────────────────────────────────────────────────────
# write_manifest + load_manifest
# ──────────────────────────────────────────────────────────────────

class TestRoundTrip:
    def test_write_then_load(self, tmp_path):
        manifest = {
            "schema_version": 1,
            "version": "v9.3.0",
            "artifacts": {"a.zip": {"sha256": "x" * 64, "size": 123}},
        }
        dest = tmp_path / "manifest.json"
        rm.write_manifest(manifest, dest)
        assert dest.exists()
        loaded = rm.load_manifest(dest)
        assert loaded == manifest

    def test_write_creates_parent_dir(self, tmp_path):
        dest = tmp_path / "nested" / "deeper" / "m.json"
        rm.write_manifest({"schema_version": 1}, dest)
        assert dest.is_file()


# ──────────────────────────────────────────────────────────────────
# lookup_artifact
# ──────────────────────────────────────────────────────────────────

class TestLookup:
    def test_returns_sha_and_size(self):
        manifest = {
            "artifacts": {"a.zip": {"sha256": "deadbeef", "size": 42}},
        }
        sha, size = rm.lookup_artifact(manifest, "a.zip")
        assert sha == "deadbeef"
        assert size == 42

    def test_unknown_artifact_returns_none(self):
        assert rm.lookup_artifact({"artifacts": {}}, "nope.zip") == (None, None)

    def test_empty_manifest_returns_none(self):
        assert rm.lookup_artifact({}, "a.zip") == (None, None)
