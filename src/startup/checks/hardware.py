"""
Check 6 — Hardware profiling.

Calls hardware.load_or_profile() — cache-aware, recomputes only when
the cached profile is stale. Non-critical: on any failure we fall back
to conservative defaults.
"""

from __future__ import annotations

from src.startup import hardware as hw
from src.startup.checker import CheckResult


def check_hardware(app_version: str = "unknown") -> CheckResult:
    profile = hw.load_or_profile(current_version=app_version)
    ram = profile["ram_gb"]
    cpu = profile["cpu_count"]
    accelerator = profile["accelerator"]
    workers = profile["acp_max_workers"]
    batch = profile["embedding_batch_size"]

    gpu_part = {
        "cuda": f"GPU: {profile.get('gpu_name', 'CUDA')} ({profile['vram_gb']}GB)",
        "mps":  "GPU: Apple Silicon",
        "cpu":  "GPU: none",
    }.get(accelerator, "GPU: unknown")

    message = (
        f"{ram}GB RAM — {cpu} cores — {gpu_part} — "
        f"ACP workers: {workers} — batch: {batch}"
    )

    return CheckResult(
        id="hardware",
        name="Hardware profiling",
        status="pass",
        message=message,
        critical=False,
    )
