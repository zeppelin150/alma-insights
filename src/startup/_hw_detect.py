"""
Alma Insights — Hardware detection primitives

Platform-specific helpers for probing RAM and available accelerators.
Each function is tiny, pure, and defensive: it returns a conservative
fallback on any error rather than raising.

Public helpers:
    ram_gb()       -> int   total physical RAM in GB
    accelerator()  -> (name, gpu_name, vram_gb)
                     name ∈ {"cuda", "mps", "cpu"}
"""

from __future__ import annotations

import platform
import subprocess

_DEFAULT_RAM_GB = 8  # conservative fallback


# ──────────────────────────────────────────────────────────────────
# RAM detection
# ──────────────────────────────────────────────────────────────────

def ram_gb() -> int:
    """Total physical RAM in whole GB. Falls back to 8 on any error."""
    system = platform.system()
    try:
        if system == "Windows":
            return _ram_windows()
        if system == "Darwin":
            return _ram_macos()
        if system == "Linux":
            return _ram_linux()
    except Exception:  # noqa: BLE001 — detection must never raise
        pass
    return _DEFAULT_RAM_GB


def _ram_windows() -> int:
    """Read total RAM via GlobalMemoryStatusEx."""
    import ctypes

    class _Memstat(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    stat = _Memstat()
    stat.dwLength = ctypes.sizeof(stat)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
    return int(stat.ullTotalPhys // (1024 ** 3))


def _ram_macos() -> int:
    """Read total RAM via sysctl hw.memsize."""
    out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], timeout=5)
    return int(int(out.strip()) // (1024 ** 3))


def _ram_linux() -> int:
    """Read total RAM from /proc/meminfo."""
    with open("/proc/meminfo", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("MemTotal:"):
                kb = int(line.split()[1])
                return kb // (1024 ** 2)
    return _DEFAULT_RAM_GB


# ──────────────────────────────────────────────────────────────────
# Accelerator detection
# ──────────────────────────────────────────────────────────────────

def accelerator() -> tuple[str, str | None, int]:
    """
    Return (name, gpu_name, vram_gb).

    name is one of:
      * "cuda" — NVIDIA GPU via torch.cuda
      * "mps"  — Apple Silicon via torch.backends.mps
      * "cpu"  — fallback

    On CPU or MPS, gpu_name is None (or the MPS device name) and vram_gb is 0.
    """
    try:
        import torch  # noqa: WPS433 — torch is a top-level bundled dep
    except ImportError:
        return ("cpu", None, 0)

    if _safe(lambda: torch.cuda.is_available()):
        name = _safe(lambda: torch.cuda.get_device_name(0)) or "cuda"
        total_bytes = _safe(lambda: torch.cuda.get_device_properties(0).total_memory) or 0
        return ("cuda", name, int(total_bytes // (1024 ** 3)))

    if _safe(lambda: torch.backends.mps.is_available()):
        return ("mps", "Apple Silicon GPU", 0)

    return ("cpu", None, 0)


def _safe(fn):
    """Run fn(); return None on any exception."""
    try:
        return fn()
    except Exception:  # noqa: BLE001 — detection must never raise
        return None
