"""
Alma Insights — Global Python Crash Handler

Captures unhandled exceptions reaching sys.excepthook, writes a
structured JSON report to data/crash_reports/, and chains to the
previous hook (so qt_error_guard's tracking keeps working).

Design tenets:
  * Local-only. No network, no telemetry, no upload.
  * Full traceback preserved — file paths, line numbers, frames all intact.
  * Only credential-shaped `key=value` patterns are redacted.
  * Handler must never itself raise: a failure here would replace the
    real exception with a less useful one.

Public API:
    install()                   -> None
    list_reports(limit=20)      -> list[Path]   (newest first)
    export_bundle(dest: Path)   -> Path         (zip the last N)
    clear(keep=0)               -> int          (prune old reports)
"""

from __future__ import annotations

import json
import re
import sys
import traceback
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

# ──────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────

_CRASH_DIR = Path("data") / "crash_reports"

# Redact only credential-shaped k=v or k:v patterns. Everything else in
# the traceback (file paths, line numbers, variable names) stays intact.
_REDACT_PATTERN = re.compile(
    r"(pat|token|key|secret|password)"           # sensitive-looking key
    r"([\"']?\s*[:=]\s*[\"']?)"                   # separator
    r"([^\s\"',\n]+)",                            # the value
    re.IGNORECASE,
)


# ──────────────────────────────────────────────────────────────────
# Installation
# ──────────────────────────────────────────────────────────────────

_installed = False
_previous_hook: Callable | None = None


def install() -> None:
    """Install the global excepthook. Idempotent."""
    global _installed, _previous_hook
    if _installed:
        return
    _previous_hook = sys.excepthook
    sys.excepthook = _hook
    _installed = True


def _hook(exc_type, exc_value, exc_tb) -> None:
    """sys.excepthook replacement. Writes report, then chains to previous."""
    try:
        _write_report(exc_type, exc_value, exc_tb)
    except Exception:  # noqa: BLE001 — handler must never raise
        pass
    if _previous_hook is not None:
        _previous_hook(exc_type, exc_value, exc_tb)


# ──────────────────────────────────────────────────────────────────
# Report writing
# ──────────────────────────────────────────────────────────────────

def _write_report(exc_type, exc_value, exc_tb) -> Path:
    """Serialise an exception to a JSON report under data/crash_reports/."""
    _CRASH_DIR.mkdir(parents=True, exist_ok=True)
    tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    tb_text = _redact(tb_text)
    stamp = datetime.now(timezone.utc)
    report = {
        "timestamp": stamp.isoformat(),
        "version": _app_version(),
        "exception_type": getattr(exc_type, "__name__", str(exc_type)),
        "exception_message": _redact(str(exc_value)),
        "traceback": tb_text,
        "hardware_profile": _load_hardware_profile(),
    }
    path = _CRASH_DIR / f"crash_{stamp.strftime('%Y%m%d_%H%M%S_%f')}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def _redact(text: str) -> str:
    """Mask credential-looking key=value pairs. Leaves everything else."""
    return _REDACT_PATTERN.sub(r"\1\2***REDACTED***", text)


def _app_version() -> str:
    try:
        import src
        return getattr(src, "__version__", "unknown")
    except Exception:  # noqa: BLE001
        return "unknown"


def _load_hardware_profile() -> dict | None:
    """Best-effort read of data/hardware_profile.json; None on any failure."""
    try:
        from src.startup import hardware
        return hardware.load()
    except Exception:  # noqa: BLE001
        return None


# ──────────────────────────────────────────────────────────────────
# Report management (used by Settings → Support)
# ──────────────────────────────────────────────────────────────────

def list_reports(limit: int = 20) -> list[Path]:
    """Return up to `limit` crash report paths, newest first."""
    if not _CRASH_DIR.exists():
        return []
    reports = sorted(_CRASH_DIR.glob("crash_*.json"), reverse=True)
    return reports[:limit]


def export_bundle(dest: Path, limit: int = 20) -> Path:
    """Zip the last `limit` reports to `dest`. Returns the dest path."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for report_path in list_reports(limit):
            zf.write(report_path, arcname=report_path.name)
    return dest


def clear(keep: int = 0) -> int:
    """
    Delete crash reports, retaining the `keep` most recent.
    Returns the number of reports deleted.
    """
    reports = list_reports(limit=10_000)
    deleted = 0
    for r in reports[keep:]:
        try:
            r.unlink()
            deleted += 1
        except OSError:
            pass
    return deleted
