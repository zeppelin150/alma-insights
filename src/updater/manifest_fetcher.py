"""
Alma Insights — Release manifest fetcher.

Glues the GitHub release feed (`assets[]` array) to the manifest schema
defined in :mod:`src.updater.release_manifest`. Single public entrypoint:
:func:`resolve_release_artifact`.

Why this module exists
----------------------

Before Piece 1 (2026-05-07), the Settings → Updates "Install Now" button
called ``Updater.stage(url, expected_sha256="")`` with no checksum,
which the updater's ``require_checksum=True`` guard immediately
rejected. The verification machinery was complete; only the manifest
lookup was missing.

This module fills that gap. Given the assets list from the release JSON
plus an authentication token, it:

  1. Locates the ``manifest.json`` asset's download URL.
  2. Fetches and parses the manifest.
  3. Looks up the platform-specific entry the running OS needs.
  4. Returns ``(download_url, sha256, size)`` ready to feed into
     :meth:`Updater.stage`.

If any of those steps fail, the function raises a single
:class:`ManifestFetchError` with a user-readable message — the Settings
UI surfaces that text directly.

Public API
----------

* :func:`resolve_release_artifact(assets, token)` — main entrypoint.
* :func:`platform_artifact_name()` — name of the zip we want for this
  host.
* :class:`ManifestFetchError` — single error type for all failure modes.
"""

from __future__ import annotations

import json
import logging
import platform
import sys
import urllib.error
import urllib.request

from src.updater.release_manifest import lookup_artifact

logger = logging.getLogger("alma.updater")

# The CI (scripts/make_release_manifest.py + .github/workflows/release.yml)
# uploads the checksum manifest as "release_manifest.json"; hand-cut / older
# releases used "manifest.json". Accept BOTH so a release built by either path
# installs — a mismatch here means the install refuses every CI-built release
# with "missing manifest.json".
_MANIFEST_ASSET_NAME = "manifest.json"
_MANIFEST_ASSET_NAMES = ("manifest.json", "release_manifest.json")
_FETCH_TIMEOUT_SECONDS = 30


# ──────────────────────────────────────────────────────────────────
# Public types
# ──────────────────────────────────────────────────────────────────

class ManifestFetchError(Exception):
    """User-facing failure during manifest fetch / lookup.

    The message is intended to be shown verbatim in the Settings UI;
    keep it actionable ("contact support" / "check your network") and
    free of stack-trace noise.
    """


# ──────────────────────────────────────────────────────────────────
# Platform detection
# ──────────────────────────────────────────────────────────────────

def platform_artifact_name() -> str:
    """Return the canonical zip filename for the running host.

    Mirrors the names produced by ``installer/build_release.py``. We
    intentionally use the same constants, not derived strings, so a
    rename in the build script would surface as a "platform entry
    missing" error instead of silently downloading the wrong artifact.
    """
    if sys.platform == "win32":
        return "AlmaInsights-win64.zip"
    if sys.platform == "darwin":
        # macOS arm64 (Apple Silicon) vs x64 (Intel). M1/M2 production
        # target is arm64; older Intel Macs get the universal x64 build.
        if platform.machine().lower() in ("arm64", "aarch64"):
            return "AlmaInsights-macOS-arm64.zip"
        return "AlmaInsights-macOS-x64.zip"
    if sys.platform.startswith("linux"):
        return "AlmaInsights-linux.zip"
    raise ManifestFetchError(
        f"Unsupported platform: {sys.platform}. Auto-update is currently "
        "limited to Windows and macOS builds."
    )


# ──────────────────────────────────────────────────────────────────
# Main entrypoint
# ──────────────────────────────────────────────────────────────────

def resolve_release_artifact(
    assets: list[dict] | None,
    token: str = "",
    *,
    artifact_name: str | None = None,
) -> tuple[str, str, int | None]:
    """Locate the platform zip + its SHA-256 from a GitHub release.

    Parameters
    ----------
    assets
        The ``assets`` array from the GitHub ``releases/latest`` JSON.
        Each entry must have at least ``name`` and ``browser_download_url``.
    token
        Bearer token used to fetch the manifest (private repos need it;
        public repos accept blank).
    artifact_name
        Override the platform-detected filename. Tests use this to
        validate happy-path behavior on every platform.

    Returns
    -------
    tuple[str, str, int | None]
        ``(download_url, expected_sha256, size_bytes)``. ``size_bytes``
        is informational and may be ``None`` if the manifest omits it.

    Raises
    ------
    ManifestFetchError
        On any of: empty assets list, missing manifest asset, network
        failure fetching the manifest, JSON parse failure, missing
        platform entry.
    """
    if not assets:
        raise ManifestFetchError(
            "Release has no assets attached — contact Alma support."
        )

    name = artifact_name or platform_artifact_name()

    manifest_url = _find_manifest_url(assets)
    manifest = _fetch_manifest_json(manifest_url, token)

    sha, size = lookup_artifact(manifest, name)
    if not sha:
        raise ManifestFetchError(
            f"Manifest does not list a SHA-256 for '{name}'. The release "
            "may have been published before this platform was supported, "
            "or the build pipeline is out of sync. Contact Alma support."
        )

    download_url = _find_artifact_url(assets, name)
    if not download_url:
        raise ManifestFetchError(
            f"Release lists a manifest entry for '{name}' but no matching "
            "download URL — contact Alma support."
        )

    logger.info(
        "Resolved release artifact: name=%s sha256=%s... size=%s",
        name, sha[:12] if sha else "?", size,
    )
    return download_url, sha, size


# ──────────────────────────────────────────────────────────────────
# Internals — each does one thing
# ──────────────────────────────────────────────────────────────────

def _find_manifest_url(assets: list[dict]) -> str:
    """Walk ``assets[]`` and return the manifest's ``browser_download_url``."""
    for asset in assets:
        if asset.get("name") in _MANIFEST_ASSET_NAMES:
            url = asset.get("browser_download_url")
            if url:
                return url
            raise ManifestFetchError(
                "Release attaches manifest.json but its download URL is "
                "missing — contact Alma support."
            )
    raise ManifestFetchError(
        "Release is missing manifest.json — refusing to install an "
        "unverifiable update. Contact Alma support."
    )


def _find_artifact_url(assets: list[dict], name: str) -> str | None:
    """Return the ``browser_download_url`` for an asset by name, or None."""
    for asset in assets:
        if asset.get("name") == name:
            return asset.get("browser_download_url")
    return None


def _fetch_manifest_json(url: str, token: str) -> dict:
    """GET the manifest URL, parse JSON. Single failure mode (raises)."""
    headers = {
        "Accept": "application/vnd.github+json, application/json",
        "User-Agent": "AlmaInsights/manifest-fetcher",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_SECONDS) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise ManifestFetchError(
            f"Could not fetch manifest (HTTP {exc.code}). "
            "If this persists, contact Alma support."
        ) from exc
    except urllib.error.URLError as exc:
        raise ManifestFetchError(
            f"Network error fetching manifest: {exc.reason}."
        ) from exc
    except (OSError, ValueError) as exc:
        raise ManifestFetchError(
            f"Unexpected error fetching manifest: {exc}"
        ) from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ManifestFetchError(
            f"Manifest is not valid JSON: {exc}. Contact Alma support."
        ) from exc
