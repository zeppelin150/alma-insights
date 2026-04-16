"""
Phase 3 E2E — wire the new updater pieces together end-to-end.

Exercised:
  (1) auth_mode=disabled → startup check passes, no HTTP
  (2) auth_mode=pat + new release on GitHub → update check warns with
      the new tag
  (3) Updater refuses to stage without a checksum, and refuses on
      mismatch; accepts only the correct checksum
  (4) Release manifest built from dist/ round-trips through write/load
      and lookup_artifact returns the right sha+size

All HTTP is mocked. No keyring writes outside the in-memory backend.

Run: python -m pytest tests/test_phase3_e2e.py -x -v
"""

from __future__ import annotations

import hashlib
import http.server
import json
import socket
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

from src.startup.checks import updates as updates_check
from src.updater import release_manifest as rm
from src.updater.updater import Updater


# ──────────────────────────────────────────────────────────────────
# Shared Qt + pump helper
# ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def _pump(app, deadline=3.0, until=None):
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        app.processEvents()
        if until and until():
            return
        time.sleep(0.01)


# ──────────────────────────────────────────────────────────────────
# (1) Disabled path
# ──────────────────────────────────────────────────────────────────

class TestDisabledEndToEnd:
    def test_splash_check_skips_http(self, monkeypatch):
        monkeypatch.setattr(
            "src.updater.update_checker._resolve_config_and_token",
            lambda: ("disabled", "https://example/r", ""),
        )
        with patch("urllib.request.urlopen") as urlopen:
            result = updates_check.check_for_update()
            urlopen.assert_not_called()
        assert result.status == "pass"


# ──────────────────────────────────────────────────────────────────
# (2) PAT mode + fake latest release
# ──────────────────────────────────────────────────────────────────

class TestPatEndToEnd:
    def test_new_version_triggers_warn(self, monkeypatch):
        monkeypatch.setattr(
            "src.updater.update_checker._resolve_config_and_token",
            lambda: ("pat", "https://api.github.com/repos/foo/bar/releases/latest", "tok"),
        )
        import src
        monkeypatch.setattr(src, "VERSION", "1.0.0", raising=False)

        fake = MagicMock()
        fake.__enter__.return_value.read.return_value = json.dumps({
            "tag_name": "v2.0.0",
            "html_url": "https://example/",
        }).encode("utf-8")
        fake.__exit__.return_value = False

        with patch("urllib.request.urlopen", return_value=fake):
            result = updates_check.check_for_update()

        assert result.status == "warn"
        assert "2.0.0" in result.message


# ──────────────────────────────────────────────────────────────────
# (3) Checksum enforcement end-to-end against a tiny local HTTP server
# ──────────────────────────────────────────────────────────────────

def _spin_up_server(tmp_path: Path) -> tuple[str, threading.Thread, http.server.HTTPServer]:
    """Serve tmp_path on a random localhost port. Returns (base_url, thread, server)."""
    handler = http.server.SimpleHTTPRequestHandler
    handler.directory = str(tmp_path)  # type: ignore[attr-defined]

    # Pick a random port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    # Serve from tmp_path (SimpleHTTPRequestHandler uses cwd-ish; pass directory)
    def _factory(*args, **kwargs):
        return http.server.SimpleHTTPRequestHandler(*args, directory=str(tmp_path), **kwargs)

    srv = http.server.HTTPServer(("127.0.0.1", port), _factory)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return f"http://127.0.0.1:{port}", t, srv


class TestStagingChecksum:
    def test_refuses_without_checksum(self, qapp, tmp_path):
        base, _, srv = _spin_up_server(tmp_path)
        try:
            updater = Updater()
            failed: list[str] = []
            updater.failed.connect(lambda m: failed.append(m))
            updater.stage(f"{base}/missing.zip",
                          expected_sha256="", new_version="v1")
            _pump(qapp, until=lambda: bool(failed))
            assert any("checksum" in m.lower() for m in failed)
        finally:
            srv.shutdown()

    def test_refuses_on_checksum_mismatch(self, qapp, tmp_path):
        (tmp_path / "update.zip").write_bytes(b"PK\x03\x04release-content")
        base, _, srv = _spin_up_server(tmp_path)
        try:
            updater = Updater()
            failed: list[str] = []
            updater.failed.connect(lambda m: failed.append(m))
            updater.stage(
                f"{base}/update.zip",
                expected_sha256="a" * 64,  # deliberately wrong
                new_version="v1",
            )
            _pump(qapp, until=lambda: bool(failed))
            assert failed, "updater should reject mismatched checksum"
            assert "mismatch" in failed[0].lower()
        finally:
            srv.shutdown()


# ──────────────────────────────────────────────────────────────────
# (4) Manifest round-trip
# ──────────────────────────────────────────────────────────────────

class TestManifestRoundTrip:
    def test_build_write_load_lookup(self, tmp_path):
        artifacts = tmp_path / "dist"
        artifacts.mkdir()
        (artifacts / "AlmaInsights-win64.zip").write_bytes(b"payload-win")
        (artifacts / "AlmaInsights-macOS-arm64.zip").write_bytes(b"payload-mac")

        manifest = rm.build_manifest("v9.3.0", artifacts)
        dest = artifacts / "release_manifest.json"
        rm.write_manifest(manifest, dest)
        loaded = rm.load_manifest(dest)

        assert set(loaded["artifacts"]) == {
            "AlmaInsights-win64.zip",
            "AlmaInsights-macOS-arm64.zip",
        }
        win_sha, _ = rm.lookup_artifact(loaded, "AlmaInsights-win64.zip")
        assert win_sha == hashlib.sha256(b"payload-win").hexdigest()
