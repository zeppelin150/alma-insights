"""
Alma Insights — Hardware Profiler

Detects CPU count, RAM, and the best available accelerator
(CUDA / MPS / CPU), then derives tuned defaults for the embedding
pipeline and ACP worker pool.

The profile is persisted to data/hardware_profile.json and re-computed
only when the file is missing, corrupt, or the app version has changed.

Public API:
    profile() -> dict             run detection + heuristics fresh
    load_or_profile() -> dict     read cached profile, recompute if stale
    save(profile: dict) -> Path   persist to data/hardware_profile.json
    needs_reprofile(cached) -> bool

Usage (wired into startup Check 7):
    from src.startup import hardware
    profile = hardware.load_or_profile()
    batch_size   = profile["embedding_batch_size"]
    worker_count = profile["acp_max_workers"]
"""

from __future__ import annotations

import json
import multiprocessing
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.startup import _hw_detect as _detect

# ──────────────────────────────────────────────────────────────────
# Persistence
# ──────────────────────────────────────────────────────────────────

_PROFILE_PATH = Path("data") / "hardware_profile.json"
_SCHEMA_VERSION = 1


def save(profile: dict[str, Any]) -> Path:
    """Write the profile to data/hardware_profile.json."""
    _PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PROFILE_PATH.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    return _PROFILE_PATH


def load() -> dict[str, Any] | None:
    """Read the cached profile. Returns None if missing or unreadable."""
    try:
        if _PROFILE_PATH.exists():
            return json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return None


def needs_reprofile(cached: dict[str, Any] | None, current_version: str) -> bool:
    """
    True if the cached profile is missing, from an older app version, or
    from an older schema version.
    """
    if cached is None:
        return True
    if cached.get("schema_version") != _SCHEMA_VERSION:
        return True
    if cached.get("app_version") != current_version:
        return True
    return False


def load_or_profile(current_version: str = "unknown") -> dict[str, Any]:
    """Read cached profile; run fresh detection if stale. Persists on refresh."""
    cached = load()
    if not needs_reprofile(cached, current_version):
        return cached  # type: ignore[return-value]
    fresh = profile(current_version)
    save(fresh)
    return fresh


# ──────────────────────────────────────────────────────────────────
# Fresh detection
# ──────────────────────────────────────────────────────────────────

def profile(current_version: str = "unknown") -> dict[str, Any]:
    """Run detection + heuristic tuning and return a complete profile dict."""
    cpu_count = multiprocessing.cpu_count()
    ram_gb = _detect.ram_gb()
    accelerator, gpu_name, vram_gb = _detect.accelerator()

    tuning = _tune(cpu_count, ram_gb, accelerator, vram_gb)

    return {
        "schema_version": _SCHEMA_VERSION,
        "app_version": current_version,
        "profiled_at": datetime.now(timezone.utc).isoformat(),
        "cpu_count": cpu_count,
        "ram_gb": ram_gb,
        "accelerator": accelerator,
        "gpu_name": gpu_name,
        "vram_gb": vram_gb,
        "os": platform.system(),
        "arch": platform.machine(),
        **tuning,
    }


# ──────────────────────────────────────────────────────────────────
# Heuristics (small, pure, unit-testable)
# ──────────────────────────────────────────────────────────────────

def _tune(cpu_count: int, ram_gb: int, accelerator: str, vram_gb: int) -> dict[str, Any]:
    """
    Derive embedding + ACP settings from hardware.

    CUDA: GPU dominates — large batch, single thread, many workers
    MPS (M1): balanced — medium batch, some threads
    CPU: conservative — small batch, half the cores
    """
    if accelerator == "cuda":
        batch = min(128, max(32, vram_gb * 8))
        threads = 1
        device = "cuda"
    elif accelerator == "mps":
        batch = min(64, max(16, ram_gb * 2))
        threads = max(2, cpu_count // 2)
        device = "mps"
    else:
        batch = min(32, max(8, ram_gb))
        threads = max(1, cpu_count // 2)
        device = "cpu"

    return {
        "embedding_batch_size": int(batch),
        "embedding_threads": int(threads),
        "embedding_device": device,
        "acp_max_workers": _acp_workers(cpu_count, ram_gb),
    }


def _acp_workers(cpu_count: int, ram_gb: int) -> int:
    """
    Each ACP worker needs ~200 MB RAM and 1 thread.
    Leave 4 GB for the OS + app, 2 cores for UI + OS. Hard cap at 8.
    """
    by_ram = max(1, (ram_gb - 4) // 2)
    by_cpu = max(1, cpu_count - 2)
    return min(by_ram, by_cpu, 8)
