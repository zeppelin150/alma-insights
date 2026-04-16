"""
Alma Insights — Post-update rollback guard.

A newly applied update gets a 60-second "grace window" on the next
launches. If the app crashes three times during that window, the next
launch detects it and rolls back to the previous version.

Two pieces of state on disk:

  data/rollback_state.json   {"previous": "v9.2.0", "new": "v9.3.0",
                               "applied_at": "...", "grace_window_s": 60}
  _src_previous/             one-version-old copy of src/
  _config_previous/          one-version-old copy of config/
  _migrations_previous/      one-version-old copy of migrations/

The rollback_state file is written by record_apply(); the _*_previous
directories are left on disk by updater.apply_staged_update (it moves
_*_backup -> _*_previous on a successful apply instead of deleting).

Public API:
    record_apply(previous_version, new_version, **kwargs)  -> None
    needs_rollback()        -> (should_rollback: bool, reason: str)
    perform_rollback()      -> (success: bool, message: str)
    clear_state_if_stable() -> bool       # called after MainWindow opens
    current_state()         -> dict | None
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("alma.rollback")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ROLLBACK_STATE = _PROJECT_ROOT / "data" / "rollback_state.json"
_CRASH_DIR = _PROJECT_ROOT / "data" / "crash_reports"

_REPLACEABLE_DIRS = ("src", "config", "migrations")
_DEFAULT_GRACE_WINDOW_S = 60
_CRASH_THRESHOLD = 3


# ──────────────────────────────────────────────────────────────────
# State file I/O
# ──────────────────────────────────────────────────────────────────

def current_state() -> dict | None:
    """Read rollback_state.json. Returns None if absent or unreadable."""
    try:
        if _ROLLBACK_STATE.exists():
            return json.loads(_ROLLBACK_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _write_state(state: dict) -> None:
    _ROLLBACK_STATE.parent.mkdir(parents=True, exist_ok=True)
    _ROLLBACK_STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _clear_state() -> None:
    try:
        _ROLLBACK_STATE.unlink(missing_ok=True)
    except OSError:
        pass


# ──────────────────────────────────────────────────────────────────
# Recording a successful apply (called from updater.apply_staged_update)
# ──────────────────────────────────────────────────────────────────

def record_apply(
    previous_version: str,
    new_version: str,
    *,
    grace_window_s: int = _DEFAULT_GRACE_WINDOW_S,
) -> None:
    """Persist the apply event + move _*_backup dirs to _*_previous."""
    _write_state({
        "schema_version": 1,
        "previous": previous_version,
        "new": new_version,
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "grace_window_s": grace_window_s,
    })
    for dirname in _REPLACEABLE_DIRS:
        backup = _PROJECT_ROOT / f"_{dirname}_backup"
        previous = _PROJECT_ROOT / f"_{dirname}_previous"
        if not backup.is_dir():
            continue
        if previous.is_dir():
            shutil.rmtree(previous, ignore_errors=True)
        backup.rename(previous)
    logger.info("Rollback state recorded for %s -> %s", previous_version, new_version)


# ──────────────────────────────────────────────────────────────────
# Detection (called at startup, before the splash)
# ──────────────────────────────────────────────────────────────────

def needs_rollback() -> tuple[bool, str]:
    """Decide whether the current launch should auto-rollback.

    Returns (should_rollback, reason_for_logging).
    """
    state = current_state()
    if state is None:
        return False, "no rollback state"

    applied_at = _parse_ts(state.get("applied_at"))
    if applied_at is None:
        return False, "rollback state has no applied_at"

    elapsed = (datetime.now(timezone.utc) - applied_at).total_seconds()
    if elapsed > state.get("grace_window_s", _DEFAULT_GRACE_WINDOW_S):
        # Grace expired; treat as a stable install and let the caller clean up.
        return False, f"grace window expired ({elapsed:.0f}s elapsed)"

    crashes = _count_crashes_since(applied_at)
    if crashes >= _CRASH_THRESHOLD:
        return True, f"{crashes} crash(es) within grace window"

    return False, f"{crashes} crash(es) — under threshold"


def _count_crashes_since(since: datetime) -> int:
    """Count crash report files newer than `since`."""
    if not _CRASH_DIR.is_dir():
        return 0
    count = 0
    for path in _CRASH_DIR.glob("crash_*.json"):
        try:
            # Use mtime to avoid parsing every JSON
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if mtime >= since:
                count += 1
        except OSError:
            continue
    return count


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


# ──────────────────────────────────────────────────────────────────
# Execution
# ──────────────────────────────────────────────────────────────────

def perform_rollback() -> tuple[bool, str]:
    """Swap _*_previous directories back into place. Idempotent-ish:
    running it twice when no previous exists returns (False, ...)."""
    state = current_state()
    if state is None:
        return False, "no rollback state to act on"

    previous_version = state.get("previous", "unknown")
    restored: list[str] = []

    for dirname in _REPLACEABLE_DIRS:
        live = _PROJECT_ROOT / dirname
        previous = _PROJECT_ROOT / f"_{dirname}_previous"
        if not previous.is_dir():
            continue
        try:
            # Move current into a scratch dir so we can recover if the
            # restore step fails halfway through.
            scratch = _PROJECT_ROOT / f"_{dirname}_rollback_tmp"
            if scratch.is_dir():
                shutil.rmtree(scratch, ignore_errors=True)
            if live.is_dir():
                live.rename(scratch)
            previous.rename(live)
            shutil.rmtree(scratch, ignore_errors=True)
            restored.append(dirname)
        except OSError as exc:
            logger.exception("Rollback of %s failed: %s", dirname, exc)
            return False, f"rollback of {dirname} failed: {exc}"

    _update_version_in_init(previous_version)
    _clear_state()

    if not restored:
        return False, "nothing to restore (no _*_previous directories)"

    logger.info("Rolled back to %s (restored: %s)", previous_version, ", ".join(restored))
    return True, f"rolled back to {previous_version}"


def _update_version_in_init(version: str) -> None:
    """Rewrite VERSION in src/__init__.py to match the rolled-back version."""
    init_py = _PROJECT_ROOT / "src" / "__init__.py"
    if not init_py.is_file():
        return
    try:
        text = init_py.read_text(encoding="utf-8")
        if "VERSION" not in text:
            return
        import re
        new_text = re.sub(
            r'VERSION\s*=\s*"[^"]*"',
            f'VERSION = "{version.lstrip("vV")}"',
            text,
        )
        init_py.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not rewrite VERSION in __init__.py: %s", exc)


# ──────────────────────────────────────────────────────────────────
# Stable-launch cleanup (called after MainWindow has opened)
# ──────────────────────────────────────────────────────────────────

def clear_state_if_stable() -> bool:
    """If the grace window has expired without tripping the crash
    threshold, clear the rollback state + delete _*_previous directories.

    Returns True if cleanup happened.
    """
    state = current_state()
    if state is None:
        return False

    applied_at = _parse_ts(state.get("applied_at"))
    if applied_at is None:
        _clear_state()
        return True

    elapsed = (datetime.now(timezone.utc) - applied_at).total_seconds()
    if elapsed < state.get("grace_window_s", _DEFAULT_GRACE_WINDOW_S):
        return False

    for dirname in _REPLACEABLE_DIRS:
        previous = _PROJECT_ROOT / f"_{dirname}_previous"
        if previous.is_dir():
            shutil.rmtree(previous, ignore_errors=True)
    _clear_state()
    logger.info("Update is stable; cleared rollback state.")
    return True
