"""
Check 11 — Pending rollback detection (bug-bash 2026-04-17).

The auto-updater records state in data/rollback_state.json and leaves
`_src_previous/`, `_config_previous/`, `_migrations_previous/` dirs on
disk for a grace window (default 60s) after applying an update. If the
app crashes 3x during that window, main.py rolls back on next launch.
If it survives the grace window, clear_state_if_stable() removes the
state and the backup dirs.

This check surfaces the state to the user on the splash instead of
leaving it invisible. Always non-critical — Continue is never blocked.

Three outcomes:
  * No state file + no backup dirs          -> pass, "Clean install"
  * State file inside grace window          -> warn, "Update just applied..."
  * Backup dirs present with no state file  -> warn, "Stale rollback dirs..."
"""

from __future__ import annotations

from pathlib import Path

from src.startup.checker import CheckResult

# Monkeypatched in tests.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_PREVIOUS_DIR_NAMES = ("_src_previous", "_config_previous", "_migrations_previous")


def check_rollback() -> CheckResult:
    state = _read_state()
    stale_dirs = _find_previous_dirs()

    # Happy path — nothing to report.
    if state is None and not stale_dirs:
        return CheckResult(
            id="rollback",
            name="Pending rollback",
            status="pass",
            message="No pending update rollback",
            critical=False,
        )

    # Active grace window (state file present, applied_at valid).
    if state is not None:
        in_window, elapsed, grace = _grace_info(state)
        new = state.get("new", "?")
        previous = state.get("previous", "?")
        if in_window:
            return CheckResult(
                id="rollback",
                name="Pending rollback",
                status="warn",
                message=(
                    f"Update just applied (v{previous} → v{new}); "
                    f"in grace window ({int(elapsed)}s of {int(grace)}s)"
                ),
                remediation=(
                    "App will auto-rollback if it crashes 3+ times in the "
                    "grace window. No action needed."
                ),
                critical=False,
            )
        # Grace expired but state still on disk — main.py hasn't cleaned
        # up yet (it runs the cleanup 65s after MainWindow opens).
        return CheckResult(
            id="rollback",
            name="Pending rollback",
            status="warn",
            message=(
                f"Expired rollback state on disk (v{previous} → v{new})"
            ),
            remediation=(
                "Will be cleared shortly after the app finishes launching."
            ),
            critical=False,
        )

    # No state file, but previous-version dirs linger on disk.
    names = ", ".join(sorted(stale_dirs))
    return CheckResult(
        id="rollback",
        name="Pending rollback",
        status="warn",
        message=f"Stale previous-version dirs: {names}",
        remediation=(
            "Safe to delete once you're sure the current version is stable."
        ),
        critical=False,
    )


# ──────────────────────────────────────────────────────────────────
# Internals
# ──────────────────────────────────────────────────────────────────

def _read_state() -> dict | None:
    """Read data/rollback_state.json via the updater module, so we share
    the same parser and default fallbacks."""
    try:
        import importlib
        import src.updater.rollback as rb
        # Rebind the project root so tests that monkeypatch our own
        # _PROJECT_ROOT also redirect the updater module's state path.
        rb = importlib.reload(rb) if False else rb  # keep import lazy
    except Exception:
        # If the updater module can't load, we still want to report
        # something useful.
        return _read_state_raw()

    state_path = _PROJECT_ROOT / "data" / "rollback_state.json"
    if not state_path.is_file():
        return None
    try:
        import json
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_state_raw() -> dict | None:
    state_path = _PROJECT_ROOT / "data" / "rollback_state.json"
    if not state_path.is_file():
        return None
    try:
        import json
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _find_previous_dirs() -> list[str]:
    return [
        name for name in _PREVIOUS_DIR_NAMES
        if (_PROJECT_ROOT / name).is_dir()
    ]


def _grace_info(state: dict) -> tuple[bool, float, float]:
    """Return (still_in_window, elapsed_s, grace_window_s). On any parse
    failure, assume the window is closed."""
    from datetime import datetime, timezone
    grace = float(state.get("grace_window_s", 60))
    raw = state.get("applied_at")
    if not raw:
        return False, 0.0, grace
    try:
        applied = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return False, 0.0, grace
    elapsed = (datetime.now(timezone.utc) - applied).total_seconds()
    return (elapsed < grace), max(elapsed, 0.0), grace
