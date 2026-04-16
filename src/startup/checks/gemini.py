"""
Check 3 — Gemini OAuth.

The Gemini CLI owns its own OAuth flow: credentials live in
~/.gemini/google_accounts.json and the `gemini` binary refreshes tokens
automatically. We verify the CLI is reachable and authenticated by
running a throwaway prompt with a short timeout.

This is the ToS-safe path: we never read or reuse the OAuth token,
we only observe whether the CLI binary can produce output.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from src.data import pat_store
from src.startup.checker import CheckResult

_PROBE_PROMPT = "ping"
_TIMEOUT_SECONDS = 30


def check_gemini_oauth() -> CheckResult:
    cli_path = _resolve_cli_path()
    if cli_path is None:
        return CheckResult(
            id="gemini_oauth",
            name="Gemini OAuth",
            status="fail",
            message="Gemini CLI not found",
            remediation="Reinstall Alma Insights to restore the bundled Gemini CLI.",
            critical=True,
        )

    try:
        proc = subprocess.run(
            [str(cli_path), "--prompt", _PROBE_PROMPT],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            id="gemini_oauth",
            name="Gemini OAuth",
            status="fail",
            message=f"CLI timed out after {_TIMEOUT_SECONDS}s",
            remediation=(
                "Run `gemini` in a terminal to complete the OAuth login, "
                "then restart Alma Insights."
            ),
            critical=True,
        )
    except (OSError, FileNotFoundError) as exc:
        return CheckResult(
            id="gemini_oauth",
            name="Gemini OAuth",
            status="fail",
            message=f"Failed to invoke CLI: {exc}",
            remediation="Reinstall Alma Insights to restore the bundled Gemini CLI.",
            critical=True,
        )

    if proc.returncode != 0 or _looks_unauthenticated(proc.stderr):
        return CheckResult(
            id="gemini_oauth",
            name="Gemini OAuth",
            status="fail",
            message="CLI reports unauthenticated session",
            remediation=(
                "Run `gemini` in a terminal to complete the OAuth login, "
                "then restart Alma Insights."
            ),
            critical=True,
        )

    return CheckResult(
        id="gemini_oauth",
        name="Gemini OAuth",
        status="pass",
        message="Token valid — CLI responded",
        critical=True,
    )


def _resolve_cli_path() -> Path | None:
    """Prefer the path saved in settings (written by the installer); fall back to $PATH."""
    configured = pat_store.load_setting("gemini_cli_path")
    if configured and Path(configured).exists():
        return Path(configured)
    found = shutil.which("gemini")
    return Path(found) if found else None


def _looks_unauthenticated(stderr: str) -> bool:
    low = (stderr or "").lower()
    return any(tok in low for tok in ("unauthenticated", "not logged in", "auth error"))
