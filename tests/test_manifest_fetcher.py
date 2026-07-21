"""
Manifest fetcher tests (Piece 1, 2026-05-07)
=============================================

Covers :mod:`src.updater.manifest_fetcher` — the bridge between
GitHub's release-feed ``assets[]`` array and the manifest-defined
SHA-256 the updater needs.

Each test exercises one path; failure modes are split into their own
class so the happy path stays small.

Run: ``python -m pytest tests/test_manifest_fetcher.py -x -v``
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from src.updater.manifest_fetcher import (
    ManifestFetchError,
    platform_artifact_name,
    resolve_release_artifact,
)


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def good_assets():
    return [
        {
            "name": "AlmaInsights-win64.zip",
            "browser_download_url":
                "https://example.com/releases/AlmaInsights-win64.zip",
        },
        {
            "name": "AlmaInsights-macOS-arm64.zip",
            "browser_download_url":
                "https://example.com/releases/AlmaInsights-macOS-arm64.zip",
        },
        {
            "name": "manifest.json",
            "browser_download_url":
                "https://example.com/releases/manifest.json",
        },
    ]


@pytest.fixture
def good_manifest_body():
    return json.dumps({
        "schema_version": 1,
        "version": "v1.0.1",
        "released_at": "2026-05-07T12:00:00+00:00",
        "artifacts": {
            "AlmaInsights-win64.zip": {
                "sha256": "a" * 64, "size": 12345,
            },
            "AlmaInsights-macOS-arm64.zip": {
                "sha256": "b" * 64, "size": 23456,
            },
        },
    }).encode("utf-8")


def _stub_urlopen(body: bytes):
    """Return a context-manager stub mimicking urlopen for unit tests."""
    class _Resp:
        def __init__(self, b): self._b = b
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return self._b
    return _Resp(body)


# ── Platform name detection ───────────────────────────────────────


class TestPlatformArtifactName:
    def test_windows(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "win32")
        assert platform_artifact_name() == "AlmaInsights-win64.zip"

    def test_macos_arm64(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "darwin")
        monkeypatch.setattr("platform.machine", lambda: "arm64")
        assert platform_artifact_name() == "AlmaInsights-macOS-arm64.zip"

    def test_macos_x64(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "darwin")
        monkeypatch.setattr("platform.machine", lambda: "x86_64")
        assert platform_artifact_name() == "AlmaInsights-macOS-x64.zip"

    def test_linux(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        assert platform_artifact_name() == "AlmaInsights-linux.zip"

    def test_unsupported_raises(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "freebsd9")
        with pytest.raises(ManifestFetchError, match="Unsupported"):
            platform_artifact_name()


# ── Happy path ────────────────────────────────────────────────────


class TestHappyPath:
    def test_resolves_url_sha_size(self, good_assets, good_manifest_body):
        with patch(
            "urllib.request.urlopen",
            return_value=_stub_urlopen(good_manifest_body),
        ):
            url, sha, size = resolve_release_artifact(
                good_assets, token="bearer123",
                artifact_name="AlmaInsights-win64.zip",
            )
        assert url == "https://example.com/releases/AlmaInsights-win64.zip"
        assert sha == "a" * 64
        assert size == 12345

    def test_picks_arm64_on_apple_silicon(
        self, good_assets, good_manifest_body, monkeypatch,
    ):
        monkeypatch.setattr("sys.platform", "darwin")
        monkeypatch.setattr("platform.machine", lambda: "arm64")
        with patch(
            "urllib.request.urlopen",
            return_value=_stub_urlopen(good_manifest_body),
        ):
            url, sha, _ = resolve_release_artifact(good_assets, token="x")
        assert "arm64" in url
        assert sha == "b" * 64

    def test_token_passed_in_header(self, good_assets, good_manifest_body):
        captured = {}

        def fake_urlopen(req, *_a, **_kw):
            captured["headers"] = dict(req.header_items())
            return _stub_urlopen(good_manifest_body)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            resolve_release_artifact(
                good_assets, token="my-bearer-token",
                artifact_name="AlmaInsights-win64.zip",
            )
        # Header keys are title-cased by urllib
        assert captured["headers"].get("Authorization") == "Bearer my-bearer-token"

    def test_blank_token_omits_auth_header(
        self, good_assets, good_manifest_body,
    ):
        captured = {}

        def fake_urlopen(req, *_a, **_kw):
            captured["headers"] = dict(req.header_items())
            return _stub_urlopen(good_manifest_body)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            resolve_release_artifact(
                good_assets, token="",
                artifact_name="AlmaInsights-win64.zip",
            )
        assert "Authorization" not in captured["headers"]


# ── Failure modes — each gets a user-readable error ───────────────


class TestFailureModes:
    def test_empty_assets(self):
        with pytest.raises(ManifestFetchError, match="no assets"):
            resolve_release_artifact([], token="")

    def test_none_assets(self):
        with pytest.raises(ManifestFetchError, match="no assets"):
            resolve_release_artifact(None, token="")

    def test_missing_manifest_asset(self):
        assets = [
            {"name": "AlmaInsights-win64.zip",
             "browser_download_url": "https://example.com/file.zip"},
        ]
        with pytest.raises(ManifestFetchError, match="missing manifest.json"):
            resolve_release_artifact(
                assets, token="",
                artifact_name="AlmaInsights-win64.zip",
            )

    def test_manifest_asset_with_blank_url(self):
        assets = [{"name": "manifest.json", "browser_download_url": ""}]
        with pytest.raises(ManifestFetchError, match="download URL"):
            resolve_release_artifact(
                assets, token="",
                artifact_name="AlmaInsights-win64.zip",
            )

    def test_network_error_during_fetch(self, good_assets):
        import urllib.error
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            with pytest.raises(ManifestFetchError, match="Network error"):
                resolve_release_artifact(
                    good_assets, token="",
                    artifact_name="AlmaInsights-win64.zip",
                )

    def test_http_error_during_fetch(self, good_assets):
        import urllib.error
        err = urllib.error.HTTPError(
            "https://example.com/manifest.json", 403,
            "forbidden", {}, None,
        )
        with patch("urllib.request.urlopen", side_effect=err):
            with pytest.raises(ManifestFetchError, match="403"):
                resolve_release_artifact(
                    good_assets, token="",
                    artifact_name="AlmaInsights-win64.zip",
                )

    def test_invalid_manifest_json(self, good_assets):
        with patch(
            "urllib.request.urlopen",
            return_value=_stub_urlopen(b"{not valid json"),
        ):
            with pytest.raises(ManifestFetchError, match="not valid JSON"):
                resolve_release_artifact(
                    good_assets, token="",
                    artifact_name="AlmaInsights-win64.zip",
                )

    def test_manifest_missing_platform_entry(self, good_assets):
        body = json.dumps({
            "schema_version": 1, "version": "v1.0.1",
            "artifacts": {
                # Only macOS, no Windows
                "AlmaInsights-macOS-arm64.zip": {"sha256": "a" * 64, "size": 1},
            },
        }).encode("utf-8")
        with patch(
            "urllib.request.urlopen",
            return_value=_stub_urlopen(body),
        ):
            with pytest.raises(ManifestFetchError, match="does not list"):
                resolve_release_artifact(
                    good_assets, token="",
                    artifact_name="AlmaInsights-win64.zip",
                )

    def test_manifest_present_but_zip_url_missing(self, good_manifest_body):
        # Manifest lists the platform's SHA, but the assets array doesn't
        # include the zip itself (CI bug). We must NOT fall back to a
        # synthesized URL — fail explicitly.
        assets = [
            {"name": "manifest.json",
             "browser_download_url": "https://example.com/manifest.json"},
            # No .zip entries at all
        ]
        with patch(
            "urllib.request.urlopen",
            return_value=_stub_urlopen(good_manifest_body),
        ):
            with pytest.raises(ManifestFetchError, match="no matching"):
                resolve_release_artifact(
                    assets, token="",
                    artifact_name="AlmaInsights-win64.zip",
                )


# ── manifest asset name: CI ships release_manifest.json, older builds manifest.json ──

class TestManifestAssetNaming:
    """The release CI (make_release_manifest.py) uploads 'release_manifest.json',
    but the fetcher historically only matched 'manifest.json'. Both must resolve,
    or every CI-built release fails to install."""

    def test_accepts_release_manifest_json(self):
        from src.updater.manifest_fetcher import _find_manifest_url
        assets = [
            {"name": "AlmaInsights-win64.zip", "browser_download_url": "https://x/z.zip"},
            {"name": "release_manifest.json",
             "browser_download_url": "https://x/release_manifest.json"},
        ]
        assert _find_manifest_url(assets) == "https://x/release_manifest.json"

    def test_still_accepts_manifest_json(self):
        from src.updater.manifest_fetcher import _find_manifest_url
        assets = [{"name": "manifest.json",
                   "browser_download_url": "https://x/manifest.json"}]
        assert _find_manifest_url(assets) == "https://x/manifest.json"

    def test_missing_either_name_still_raises(self):
        from src.updater.manifest_fetcher import _find_manifest_url, ManifestFetchError
        with pytest.raises(ManifestFetchError, match="missing manifest"):
            _find_manifest_url([{"name": "notes.txt", "browser_download_url": "https://x/n"}])
