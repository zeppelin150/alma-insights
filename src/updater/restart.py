"""
Alma Insights — Application restart helper.

Single public function: :func:`restart_app`. Used by the splash
"Install & Restart" flow and the Settings → Updates restart prompt to
re-launch the app cleanly so :func:`updater.apply_staged_update` can
swap the staged ``src/`` / ``config/`` / ``migrations/`` directories
into place before any of them are imported.

Why this is its own file
------------------------

* The restart logic is platform-sensitive (Windows needs detached
  process flags; POSIX needs ``os.setsid`` to escape the parent's
  process group).
* Several call sites need it (splash, settings) — putting the logic
  in one place avoids subtle drift between two near-identical Popen
  invocations.
* Easy to mock in tests by patching this module's ``subprocess.Popen``.

Public API
----------

* :func:`restart_app` — spawn a detached child running the same Python
  interpreter + ``main.py`` argv, then call ``QApplication.quit()``.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("alma.updater")

# Windows process-creation flags. Defined here as integers so we don't
# need to import the subprocess constants on POSIX (where they don't
# exist).
_WIN_DETACHED_PROCESS = 0x00000008
_WIN_CREATE_NEW_PROCESS_GROUP = 0x00000200


def restart_app() -> None:
    """Spawn a detached copy of the running app, then exit this one.

    Order matters: spawn FIRST so we know a child is already starting.
    Only after the child has been launched do we ask the current
    QApplication to quit. The child reads our parent's argv verbatim,
    so flags like ``--no-splash`` are preserved.

    Side effects
    ------------

    * Calls ``subprocess.Popen`` with the same ``sys.executable`` and
      ``sys.argv`` as the current process.
    * Calls ``QApplication.quit()`` on the running instance.

    Failure mode: if the spawn fails we log and re-raise. The caller
    should keep the user on the splash / settings page so they can
    retry rather than being left with a dead app and no UI.
    """
    cmd = [sys.executable, *sys.argv]
    logger.info("Restarting application: %s", " ".join(_safe(c) for c in cmd))

    popen_kwargs = _detach_kwargs()
    try:
        subprocess.Popen(cmd, **popen_kwargs)  # noqa: S603 — argv is ours
    except OSError as exc:
        logger.exception("Failed to launch replacement process: %s", exc)
        raise

    # Tell Qt to wind down. We do not call sys.exit() — that bypasses
    # Qt's cleanup (causing zombie scan_server/Gemini subprocesses on
    # Windows). Returning lets main.py's `sys.exit(app.exec())` drain
    # event-loop tasks first.
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.quit()
    except Exception as exc:  # noqa: BLE001 — fallback if Qt unavailable
        logger.warning("QApplication.quit() failed: %s", exc)
        # Last resort. Should never reach here in normal use.
        os._exit(0)


# ──────────────────────────────────────────────────────────────────
# Internals
# ──────────────────────────────────────────────────────────────────

def _detach_kwargs() -> dict:
    """Platform-specific kwargs that detach the child from this process.

    On Windows: ``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`` plus
    ``stdin/stdout/stderr=DEVNULL`` so the child doesn't inherit our
    handles (which would block our exit).

    On POSIX: ``start_new_session=True`` to escape our process group
    so the OS doesn't deliver our SIGTERM to the child when we quit.
    """
    common: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        flags = _WIN_DETACHED_PROCESS | _WIN_CREATE_NEW_PROCESS_GROUP
        common["creationflags"] = flags
    else:
        common["start_new_session"] = True
    return common


def _safe(arg: str) -> str:
    """Format a single argv entry for logging — quotes paths with spaces."""
    if " " in arg or '"' in arg:
        return f'"{arg}"'
    return arg
