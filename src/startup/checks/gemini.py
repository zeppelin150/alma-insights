"""
Check 3 — Gemini OAuth.

The Gemini CLI owns its own OAuth flow: credentials live in
~/.gemini/google_accounts.json and the `gemini` binary refreshes tokens
automatically. Originally we ran a live `gemini --prompt ping` probe to
verify the token, but that stalled the splash for ~55s on Windows
whenever the CLI's node.exe grandchildren escaped subprocess's timeout
cleanup (bug-bash 2026-04-17).

Now we just read the OAuth credential file directly:
  * CLI binary resolvable?  (shutil.which / settings-configured path)
  * google_accounts.json exists and carries a non-empty refresh_token?

Runs in microseconds, no subprocess, no cmd.exe flash. This is still
the ToS-safe path: we observe the presence of a token, we never read
or reuse its value for a network call.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # kept for existing tests that monkeypatch this module  # noqa: F401
from pathlib import Path

from src.data import pat_store
from src.startup.checker import CheckResult

# Kept for backwards compatibility with older tests that import this.
_PROBE_PROMPT = "ping"
_TIMEOUT_SECONDS = 3

# Re-authentication is resolved inside the app now (Settings → AI Provider),
# so OAuth failures are non-blocking: the splash lets the user Continue and
# main.py routes them to Settings.
_REMEDIATION_REAUTH = "Open Settings → AI Provider to re-authenticate Gemini."
_REMEDIATION_REINSTALL = (
    "Reinstall Alma Insights to restore the bundled Gemini CLI, "
    "or set the CLI path in Settings → AI Provider."
)


def check_gemini_oauth() -> CheckResult:
    cli_path = _resolve_cli_path()
    if cli_path is None:
        return CheckResult(
            id="gemini_oauth",
            name="Gemini OAuth",
            status="fail",
            message="Gemini CLI not found",
            remediation=_REMEDIATION_REINSTALL,
            critical=False,
        )

    oauth_file = _oauth_file_path()
    if not oauth_file.exists():
        return CheckResult(
            id="gemini_oauth",
            name="Gemini OAuth",
            status="fail",
            message="No Gemini credentials found",
            remediation=_REMEDIATION_REAUTH,
            critical=False,
        )

    try:
        data = json.loads(oauth_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return CheckResult(
            id="gemini_oauth",
            name="Gemini OAuth",
            status="fail",
            message=f"Could not read credentials: {exc}",
            remediation=_REMEDIATION_REAUTH,
            critical=False,
        )

    if not _has_refresh_token(data):
        return CheckResult(
            id="gemini_oauth",
            name="Gemini OAuth",
            status="fail",
            message="Credentials file missing refresh token",
            remediation=_REMEDIATION_REAUTH,
            critical=False,
        )

    return CheckResult(
        id="gemini_oauth",
        name="Gemini OAuth",
        status="pass",
        message="OAuth credentials present",
        critical=True,
    )


def _resolve_cli_path() -> Path | None:
    """Prefer the path saved in settings (written by the installer); fall back to $PATH."""
    configured = pat_store.load_setting("gemini_cli_path")
    if configured and Path(configured).exists():
        return Path(configured)
    found = shutil.which("gemini")
    return Path(found) if found else None


def _oauth_file_path() -> Path:
    """Location of the Gemini CLI's OAuth credential cache. Monkeypatched in tests."""
    return Path.home() / ".gemini" / "google_accounts.json"


def _has_refresh_token(data: object) -> bool:
    """Google's CLI nests the token under per-account keys; accept any
    non-empty `refresh_token` found at depth 1 or 2."""
    if not isinstance(data, dict):
        return False
    token = data.get("refresh_token")
    if isinstance(token, str) and token.strip():
        return True
    for v in data.values():
        if isinstance(v, dict):
            t = v.get("refresh_token")
            if isinstance(t, str) and t.strip():
                return True
    return False


def _looks_unauthenticated(stderr: str) -> bool:
    """Legacy helper kept for tests that still import it."""
    low = (stderr or "").lower()
    return any(tok in low for tok in ("unauthenticated", "not logged in", "auth error"))
