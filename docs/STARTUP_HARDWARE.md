# Hardware Profile

**Modules:** [src/startup/hardware.py](../src/startup/hardware.py), [src/startup/_hw_detect.py](../src/startup/_hw_detect.py)
**Tests:** [tests/test_hardware_profile.py](../tests/test_hardware_profile.py)

## Purpose

On first launch (and whenever the cached profile is stale), Alma Insights detects the host machine's CPU, RAM, and best-available ML accelerator, then writes tuned defaults to `data/hardware_profile.json`. Downstream consumers (embedding builder, ACP worker pool) read those defaults instead of hardcoded constants, so the same build auto-adapts to:

- **Production:** MacBook Pro M1 / 16 GB — MPS backend, batch 32, 4 embedding threads, 6 ACP workers
- **Dev:** i7-12700K + RTX 4070 Ti SUPER / 64 GB — CUDA backend, batch 120, 1 embedding thread, 8 ACP workers
- **Fallback:** any machine — CPU backend, conservative batch + half the cores

## Profile schema

```json
{
  "schema_version": 1,
  "app_version": "v9.3.0",
  "profiled_at": "2026-04-15T20:49:25+00:00",

  "cpu_count": 20,
  "ram_gb": 63,
  "accelerator": "cuda",
  "gpu_name": "NVIDIA GeForce RTX 4070 Ti SUPER",
  "vram_gb": 15,
  "os": "Windows",
  "arch": "AMD64",

  "embedding_batch_size": 120,
  "embedding_threads": 1,
  "embedding_device": "cuda",
  "acp_max_workers": 8
}
```

## Heuristics

| Accelerator | batch_size | threads | rationale |
|-------------|-----------|---------|-----------|
| CUDA | `clamp(vram_gb × 8, 32, 128)` | `1` | GPU dominates; more CPU threads just add overhead |
| MPS (M1) | `clamp(ram_gb × 2, 16, 64)` | `cpu_count / 2` | Unified memory — scale with RAM, leave half the cores for the app |
| CPU | `clamp(ram_gb, 8, 32)` | `cpu_count / 2` | Conservative — embeddings are the slowest path |

ACP worker count:

```
acp_max_workers = min((ram_gb - 4) / 2,   # each worker ≈ 200 MB
                      cpu_count - 2,       # leave 2 cores for UI + OS
                      8)                   # hard cap
```

## Re-profile triggers

`load_or_profile(current_version)` returns the cached profile unless any of these hold:

1. `data/hardware_profile.json` is missing or corrupt.
2. The cached `schema_version` differs from `_SCHEMA_VERSION` in `hardware.py`.
3. The cached `app_version` differs from the current app version.

On any trigger, fresh detection runs and the profile is re-persisted.

## Consumer wiring

Downstream code should call once at startup and cache the result:

```python
from src.startup import hardware

profile = hardware.load_or_profile(current_version=APP_VERSION)
batch_size   = profile["embedding_batch_size"]
worker_count = profile["acp_max_workers"]
device       = profile["embedding_device"]
```

See the future "Consumer migration" task for replacing hardcoded constants in `src/data/embedding/builder.py` and `src/agents/scan_orchestrator.py`.

## Failure modes

Every detection helper returns a conservative fallback on any exception rather than raising:

- `ram_gb()` → 8 GB on unknown OS or failed syscall
- `accelerator()` → `("cpu", None, 0)` if `torch` is unimportable or both `cuda.is_available()` and `mps.is_available()` raise

So `profile()` never fails; worst case it returns the CPU defaults.

## Testing

```bash
python -m pytest tests/test_hardware_profile.py -x -v
```

27 tests cover: tuning heuristics (CUDA/MPS/CPU with edge values), persistence round-trips, reprofile triggers, and fallback paths when `torch` is absent.
