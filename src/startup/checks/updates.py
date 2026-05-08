"""
Check 4 — Update availability (Phase 3).

Runs synchronously during the splash with a short timeout. Honours the
`updates.auth_mode` setting:

    disabled     → pass — "Update checks disabled in settings"
    pat          → fetch latest release, compare against current version
    github_app   → same, but mint an installation token first

Non-critical by design: on network failure, misconfiguration, or a
timeout, we warn and let the user continue.

2026-05-07 (Piece 2A): when a newer release is found we stash the
release JSON's ``assets`` array, the auth token, and the new-version
tag in this module's :func:`get_pending_update` cache. The splash
window's "Install & Restart" widget reads that to drive the staging
flow without re-hitting GitHub.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from src.startup.checker import CheckResult

logger = logging.getLogger("alma.updater")

_TIMEOUT_SECONDS = 5

# Populated by check_for_update() iff a newer release was found. Read
# by src.startup.update_action_widget. Reset on every check.
_PENDING_UPDATE: dict | None = None


def get_pending_update() -> dict | None:
    """Return the cached pending-update payload, or ``None`` if no
    update was detected on the last check.

    Payload keys:
        new_version (str)        — tag without leading 'v'
        html_url    (str)        — GitHub release page URL
        assets      (list[dict]) — release ``assets[]`` from the API
        token       (str)        — bearer token used for the original
                                    fetch (so the manifest call can
                                    re-use it for private repos)
    """
    return _PENDING_UPDATE


def check_for_update() -> CheckResult:
    global _PENDING_UPDATE
    _PENDING_UPDATE = None  # always reset so a stale payload can't leak

    try:
        from src.updater.update_checker import (
            _resolve_config_and_token,
            _is_newer,
        )
        from src import VERSION
    except ImportError as exc:
        return _warn(f"Update module unavailable: {exc}")

    mode, releases_url, token = _resolve_config_and_token()

    if mode == "disabled":
        return CheckResult(
            id="update",
            name="Checking for updates",
            status="pass",
            message="Update checks disabled in settings",
            critical=False,
        )

    if not token:
        return _warn(
            "No update token configured — check Settings → Updates",
            remediation="Set updates.auth_mode and add a GitHub token.",
        )

    try:
        latest = _fetch_latest(releases_url, token)
    except _UpdateCheckError as exc:
        return _warn(str(exc), remediation="App will continue in offline mode.")

    tag = latest.get("tag_name", "")
    if not tag:
        return _warn("Release feed missing tag_name")

    if _is_newer(VERSION, tag):
        # Cache for the splash "Install & Restart" widget. Includes the
        # auth token so the manifest fetcher can re-use it for private
        # repos. Lifetime ends on next call to check_for_update.
        _PENDING_UPDATE = {
            "new_version": tag.lstrip("vV"),
            "html_url": latest.get("html_url", ""),
            "assets": latest.get("assets") or [],
            "token": token,
        }
        return CheckResult(
            id="update",
            name="Checking for updates",
            status="warn",
            message=f"Update available: v{tag.lstrip('vV')} (current v{VERSION})",
            remediation="Click 'Install & Restart' below to apply now.",
            critical=False,
        )

    return CheckResult(
        id="update",
        name="Checking for updates",
        status="pass",
        message=f"Up to date (v{VERSION})",
        critical=False,
    )


# ──────────────────────────────────────────────────────────────────
# Internals
# ──────────────────────────────────────────────────────────────────

class _UpdateCheckError(Exception):
    """Internal marker for user-facing update check failures."""


def _fetch_latest(url: str, token: str) -> dict:
    """Synchronous HTTP GET with Bearer auth. Raises _UpdateCheckError."""
    try:
        from src import VERSION
    except ImportError:
        VERSION = "0.0.0"

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"AlmaInsights/{VERSION}",
        "Authorization": f"Bearer {token}",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise _UpdateCheckError("GitHub auth failed — token invalid or expired")
        if exc.code == 404:
            raise _UpdateCheckError("Release repo not found — check updates.github_repo")
        raise _UpdateCheckError(f"GitHub API error: {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise _UpdateCheckError(f"Network unavailable: {exc.reason}") from exc
    except (OSError, ValueError) as exc:
        raise _UpdateCheckError(f"Unexpected response: {exc}") from exc


def _warn(message: str, remediation: str = "") -> CheckResult:
    return CheckResult(
        id="update",
        name="Checking for updates",
        status="warn",
        message=message,
        remediation=remediation or "App will continue in offline mode.",
        critical=False,
    )
