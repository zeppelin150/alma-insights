"""
Check 5 — Environment guard.

Runs scan_for_issues() from src.startup.env_guard to verify air-gap
variables are still set and no stray proxies / .env files are present.

Critical: any residual proxy or unset HF_HUB_OFFLINE variable means a
future embedding call could reach the network. The check itself
auto-fixes what it can (enforce() is idempotent) before reporting.
"""

from __future__ import annotations

from pathlib import Path

from src.startup import env_guard
from src.startup.checker import CheckResult

_APP_ROOT = Path(__file__).resolve().parents[3]


def check_environment_guard() -> CheckResult:
    # Re-run enforce() — idempotent. Fixes anything that drifted since main.py.
    fixes = env_guard.enforce()
    issues = env_guard.scan_for_issues(app_root=_APP_ROOT)

    if not issues:
        return CheckResult(
            id="env_guard",
            name="Environment guard",
            status="pass",
            message="Air-gap vars set — no proxy detected — no rogue .env",
            critical=True,
        )

    # If enforce() cleaned things up and the only issues are the ones it
    # just fixed, surface as a warn rather than fail.
    if fixes and not issues:
        return CheckResult(
            id="env_guard",
            name="Environment guard",
            status="warn",
            message=f"Auto-fixed {len(fixes)} drift(s)",
            remediation="; ".join(fixes),
            critical=True,
            can_auto_fix=True,
        )

    return CheckResult(
        id="env_guard",
        name="Environment guard",
        status="fail",
        message=f"{len(issues)} residual issue(s) after enforce()",
        remediation="; ".join(issues[:3]),  # cap UI noise
        critical=True,
    )
