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
import logging
import multiprocessing
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.startup import _hw_detect as _detect

logger = logging.getLogger("alma.hardware")

# ──────────────────────────────────────────────────────────────────
# Persistence
# ──────────────────────────────────────────────────────────────────

_PROFILE_PATH = Path("data") / "hardware_profile.json"
# v2 (2026-05-07): added `embedding_max_seq_length`; tightened MPS tiers for
# 8 GB unified-memory M1/M2; embedding pipeline now consumes the profile
# (model_loader + builder). Bumping forces a one-time re-profile for every
# install so stale CPU-fallback profiles disappear.
_SCHEMA_VERSION = 2


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
    True if the cached profile is missing, stale, or from a different
    machine than the current host.

    Reasons to reprofile (any one triggers):
      * No cached profile at all (fresh install, deleted JSON).
      * Schema bumped — forces every install to re-detect once.
      * App version changed — typically follows a deploy.
      * Live machine state diverges from cached state — the install was
        moved across machines (Windows dev → M1 work, etc.). Detection
        cost is microseconds when torch is already imported.
    """
    if cached is None:
        return True
    if cached.get("schema_version") != _SCHEMA_VERSION:
        return True
    if cached.get("app_version") != current_version:
        return True
    if _machine_changed(cached):
        return True
    return False


def _machine_changed(cached: dict[str, Any]) -> bool:
    """Compare cached cpu/ram/accelerator/arch to current detection."""
    try:
        fresh_cpu = multiprocessing.cpu_count()
        fresh_ram = _detect.ram_gb()
        fresh_acc, _, _ = _detect.accelerator()
        fresh_arch = platform.machine()
    except Exception:
        # If detection itself fails, don't loop forever rewriting the
        # profile — keep the cached one.
        return False
    return (
        cached.get("cpu_count") != fresh_cpu
        or cached.get("ram_gb") != fresh_ram
        or cached.get("accelerator") != fresh_acc
        or cached.get("arch") != fresh_arch
    )


def load_or_profile(current_version: str = "unknown") -> dict[str, Any]:
    """Read cached profile; run fresh detection if stale. Persists on refresh."""
    cached = load()
    if not needs_reprofile(cached, current_version):
        return cached  # type: ignore[return-value]
    fresh = profile(current_version)
    save(fresh)
    _log_acceleration_summary(fresh)
    return fresh


def _log_acceleration_summary(p: dict[str, Any]) -> None:
    """Emit a single-line confirmation of the active accelerator.

    Useful both as an audit line for support tickets ("did Apple Silicon
    actually engage?") and as a grep target for the live-verification
    checklist on M1 machines.
    """
    acc = p.get("accelerator")
    arch = p.get("arch")
    ram = p.get("ram_gb")
    if acc == "mps" and arch == "arm64":
        logger.info(
            "Apple Silicon detected — embeddings will run on Metal Performance "
            "Shaders. Profile: ram=%sGB, batch=%s, max_seq=%s, threads=%s, "
            "acp_workers=%s.",
            ram,
            p.get("embedding_batch_size"),
            p.get("embedding_max_seq_length"),
            p.get("embedding_threads"),
            p.get("acp_max_workers"),
        )
    elif acc == "cuda":
        logger.info(
            "CUDA GPU detected — embeddings will run on %s (%sGB VRAM). "
            "Profile: batch=%s, threads=%s.",
            p.get("gpu_name"),
            p.get("vram_gb"),
            p.get("embedding_batch_size"),
            p.get("embedding_threads"),
        )
    else:
        logger.info(
            "No GPU acceleration available — embeddings will run on CPU. "
            "Profile: cpu=%s cores, ram=%sGB, batch=%s, threads=%s.",
            p.get("cpu_count"),
            ram,
            p.get("embedding_batch_size"),
            p.get("embedding_threads"),
        )


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

    CUDA: GPU dominates — large batch, single thread, many workers.
    MPS (Apple Silicon): three-tier by unified memory.
      * ≤8 GB (e.g. base M1 MacBook Air/Pro) — batch=12, max_seq=1024.
        Aggressive activation-memory ceiling; macOS + app + model leave
        only ~2 GB headroom.
      * 9-16 GB — batch=32, max_seq=2048. Middle ground.
      * ≥17 GB (M1/M2/M3 Pro/Max) — batch=64, max_seq=2048.
    CPU: conservative — small batch, half the cores.
    """
    if accelerator == "cuda":
        batch = min(128, max(32, vram_gb * 8))
        threads = 1
        max_seq = 2048
        device = "cuda"
    elif accelerator == "mps":
        if ram_gb <= 8:
            batch = 12
            max_seq = 1024
            threads = 4   # M1 has 4 perf cores; saturate them, leave E-cores for OS
        elif ram_gb <= 16:
            batch = 32
            max_seq = 2048
            threads = max(2, cpu_count // 2)
        else:  # M1/M2/M3 Pro / Max — 24+ GB
            batch = 64
            max_seq = 2048
            threads = max(2, cpu_count // 2)
        device = "mps"
    else:
        batch = min(32, max(8, ram_gb))
        threads = max(1, cpu_count // 2)
        max_seq = 2048
        device = "cpu"

    return {
        "embedding_batch_size": int(batch),
        "embedding_threads": int(threads),
        "embedding_device": device,
        "embedding_max_seq_length": int(max_seq),
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
