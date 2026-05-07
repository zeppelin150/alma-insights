"""Check 12 — Pre-flight zombie process sweep (added 2026-05-07).

Runs the bridge zombie sweep proactively on splash. Any orphan
``gemini.exe`` / ``node.exe`` left behind by a hard-killed prior
session is terminated before the user reaches the main window — so
a fresh bridge boot starts on a clean slate (memory:
``zombie_processes.md``, ``warehouse_and_zombies.md``).

Behavior:
  * No zombies found → ``pass``
  * Some terminated  → ``warn`` (informational; no action needed)
  * Sweep errored    → ``warn`` (e.g. ``wmic`` missing on a stripped
                                 Windows install — non-blocking)

Always non-critical. The bridge boot path runs the same sweep
defensively, so this check is largely about visibility/observability.
"""

from __future__ import annotations

from src.startup.checker import CheckResult


def check_zombies() -> CheckResult:
    try:
        from src.agents.bridge_zombie_sweep import sweep_zombie_bridges
    except Exception as exc:  # pragma: no cover — import failure is bizarre but tolerable
        return CheckResult(
            id="zombies",
            name="Bridge zombies",
            status="warn",
            message=f"Sweep module unavailable: {exc}",
            critical=False,
        )

    result = sweep_zombie_bridges()
    if result.errors and not result.terminated_pids:
        return CheckResult(
            id="zombies",
            name="Bridge zombies",
            status="warn",
            message=f"Sweep encountered errors: {result.errors[0]}",
            remediation=(
                "Run `taskkill /F /IM node.exe` and `taskkill /F /IM gemini.exe` "
                "manually before launching a scan."
            ),
            critical=False,
        )

    if result.total_killed:
        pids = ", ".join(str(p) for p in result.terminated_pids[:6])
        if len(result.terminated_pids) > 6:
            pids += "…"
        return CheckResult(
            id="zombies",
            name="Bridge zombies",
            status="warn",
            message=f"Cleared {result.total_killed} orphan bridge process(es) [{pids}]",
            remediation="Bridge boot will start clean; no action needed.",
            critical=False,
        )

    return CheckResult(
        id="zombies",
        name="Bridge zombies",
        status="pass",
        message="No orphan bridge subprocesses",
        critical=False,
    )
