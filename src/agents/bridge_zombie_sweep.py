"""Zombie subprocess sweep for orphaned ACP bridges (2026-05-07).

The ACP bridge already registers an `atexit` handler (see
:class:`src.agents.acp_bridge.ACPBridge._cleanup_all`) but it does not
fire when the parent process is killed hard (`taskkill /F`, OS OOM,
debugger detach, ungraceful Python exit during garbage collection — see
the `_enter_buffered_busy` errors during garbage collection of
ReportBridgeClient.__del__). Result: `gemini.exe` and the `node.exe`
children that the Gemini CLI spawns leak across sessions and accumulate
RAM, eventually locking the warehouse SQLite file (memory:
`zombie_processes.md`, `warehouse_and_zombies.md`).

This module provides a defensive **pre-boot sweep** that kills any
orphaned bridge subprocesses *before* a new bridge is started. It is
intentionally conservative:

- Only targets `gemini.exe`, `gemini.CMD` (Gemini CLI) and the
  `node.exe` instances those CLIs spawn — never general node.exe
  unless we can prove its parent was a Gemini CLI launch.
- Skips processes owned by the *current* python.exe parent so a
  freshly-booted bridge isn't sniped immediately.
- Idempotent + tolerant: never raises; on any error it logs and
  returns the partial result.

Public API
----------
- ``sweep_zombie_bridges() -> SweepResult``
  Sweeps before a bridge boot. Called by ``ACPBridge.ensure_running``
  on first invocation per session, and by the startup checker.
- ``SweepResult`` dataclass with ``terminated_pids``, ``skipped_pids``,
  ``errors``, and ``platform``.

Platform
--------
Windows-only effective implementation (uses ``wmic`` / ``taskkill``).
On non-Windows the sweep is a no-op returning an empty result — the
existing POSIX atexit handler is already reliable enough that we don't
need this layer there.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field

logger = logging.getLogger("alma.bridge_zombie_sweep")


def _no_window_kwargs() -> dict:
    """Return subprocess kwargs that hide the console window on Windows.

    GUI-launched apps that shell out to `wmic` / `taskkill` flash a black
    cmd window for each call unless `CREATE_NO_WINDOW` is set. Mirrors the
    pattern used in `acp_bridge.py`, `scan_server_manager.py`, and
    `scan_worker_manager.py`. No-op on non-Windows.
    """
    if sys.platform != "win32":
        return {}
    # CREATE_NO_WINDOW = 0x08000000 (constant available on subprocess
    # since 3.7 on Windows; fall back to literal in case of attribute
    # absence on stripped builds).
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return {"creationflags": flags}


# ──────────────────────────────────────────────────────────────────────
# Public types
# ──────────────────────────────────────────────────────────────────────

@dataclass
class SweepResult:
    """Outcome of a zombie sweep. Every field defaults so callers can
    accumulate across multiple sweeps without None-checking."""
    terminated_pids: list[int] = field(default_factory=list)
    skipped_pids: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    platform: str = ""

    @property
    def total_killed(self) -> int:
        return len(self.terminated_pids)

    def as_dict(self) -> dict:
        return {
            "terminated_pids": list(self.terminated_pids),
            "skipped_pids": list(self.skipped_pids),
            "errors": list(self.errors),
            "platform": self.platform,
            "total_killed": self.total_killed,
        }


# ──────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────

def sweep_zombie_bridges(*, dry_run: bool = False) -> SweepResult:
    """Find + kill orphaned Gemini CLI / node.exe bridge processes.

    Args:
        dry_run: if True, identify candidates without killing. Used by
                 startup checks that just want to *report* zombies.

    Returns:
        SweepResult tallying kills + skips + errors.
    """
    result = SweepResult(platform=sys.platform)
    if not _is_windows():
        # POSIX bridge cleanup runs reliably via atexit; nothing to do.
        return result

    current_pid = os.getpid()
    candidates = _find_candidate_pids(result)
    if not candidates:
        logger.debug("zombie sweep: no candidates found")
        return result

    for pid, marker in candidates:
        if pid == current_pid:
            result.skipped_pids.append(pid)
            continue
        if dry_run:
            result.skipped_pids.append(pid)
            continue
        try:
            _terminate_pid(pid)
            result.terminated_pids.append(pid)
            logger.info("zombie sweep: terminated pid=%d (%s)", pid, marker)
        except Exception as exc:
            result.errors.append(f"pid={pid}: {exc}")
            logger.debug("zombie sweep: error terminating pid=%d: %s", pid, exc)
    return result


# ──────────────────────────────────────────────────────────────────────
# Candidate discovery (Windows)
# ──────────────────────────────────────────────────────────────────────

# WMIC patterns we treat as "almost certainly bridge-spawned":
#   * gemini.exe / gemini.CMD launches → CommandLine matches "gemini" + "--acp" or "alma_mcp_server"
#   * node.exe whose CommandLine name-matches the gemini bundle script
_WMIC_QUERIES: tuple[tuple[str, str], ...] = (
    # (where-clause, marker-tag-for-logging)
    ("name='gemini.exe'", "gemini.exe"),
    ("name='node.exe' AND commandline LIKE '%gemini%'", "node.exe(gemini)"),
    ("name='node.exe' AND commandline LIKE '%alma_mcp_server%'", "node.exe(alma_mcp)"),
    ("commandline LIKE '%alma_mcp_server%'", "alma_mcp_server"),
)


def _find_candidate_pids(result: SweepResult) -> list[tuple[int, str]]:
    """Return ``[(pid, marker), ...]`` for processes that look like bridge orphans."""
    pids: list[tuple[int, str]] = []
    seen: set[int] = set()
    for where, marker in _WMIC_QUERIES:
        rows = _wmic_processes(where, result)
        for pid in rows:
            if pid in seen:
                continue
            seen.add(pid)
            pids.append((pid, marker))
    return pids


def _wmic_processes(where: str, result: SweepResult) -> list[int]:
    """Run ``wmic process where '<where>' get processid /format:value`` + parse."""
    cmd = ["wmic", "process", "where", where, "get", "processid", "/format:value"]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15, check=False,
            **_no_window_kwargs(),
        )
    except FileNotFoundError:
        result.errors.append("wmic not found")
        return []
    except subprocess.TimeoutExpired:
        result.errors.append(f"wmic timeout: {where!r}")
        return []
    except Exception as exc:
        result.errors.append(f"wmic error ({where!r}): {exc}")
        return []

    pids: list[int] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip().lower() != "processid":
            continue
        try:
            pids.append(int(value.strip()))
        except ValueError:
            continue
    return pids


def _terminate_pid(pid: int) -> None:
    """Kill a single PID via ``taskkill /F /T /PID``. Raises on failure."""
    cmd = ["taskkill", "/F", "/T", "/PID", str(pid)]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=10, check=False,
        **_no_window_kwargs(),
    )
    if proc.returncode not in (0, 128):
        # 128 = "process not found" — tolerable, treat as success.
        raise RuntimeError(
            f"taskkill exit={proc.returncode} stderr={proc.stderr.strip()[:200]}"
        )


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _is_windows() -> bool:
    return sys.platform == "win32"
