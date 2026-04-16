# CI/CD Plan — Phase 1 Complete

**Date:** 2026-04-15
**Branch:** main (direct — low-risk foundation work)
**Parent plan:** [alma-insights-cicd-plan.md](../../Users/Chris/.claude/plans/alma-insights-cicd-plan.md)

## Scope

Phase 1 lays the foundation for the startup splash (Phase 2), auto-update (Phase 3), installer hardening (Phase 4), and final hardening (Phase 5). All four modules are standalone — no cross-phase dependencies.

| Sub-phase | Module | Public API | Tests |
|-----------|--------|-----------|-------|
| 1.1 | [src/data/pat_store.py](../src/data/pat_store.py) | Same 7 public functions + `migrate_legacy_credentials`, `keyring_available` | [tests/test_pat_store_keyring.py](../tests/test_pat_store_keyring.py) — 24 ✓ |
| 1.2 | [src/startup/hardware.py](../src/startup/hardware.py) + [_hw_detect.py](../src/startup/_hw_detect.py) | `profile`, `load_or_profile`, `save`, `load`, `needs_reprofile` | [tests/test_hardware_profile.py](../tests/test_hardware_profile.py) — 27 ✓ |
| 1.3 | [src/core/crash_handler.py](../src/core/crash_handler.py) | `install`, `list_reports`, `export_bundle`, `clear` | [tests/test_crash_handler.py](../tests/test_crash_handler.py) — 22 ✓ |
| 1.4 | [src/startup/env_guard.py](../src/startup/env_guard.py) | `enforce`, `scan_for_issues`, `current_state` | [tests/test_env_guard.py](../tests/test_env_guard.py) — 20 ✓ |
| 1.5 | — | End-to-end composition | [tests/test_phase1_e2e.py](../tests/test_phase1_e2e.py) — 4 ✓ |

**97 new tests, 100 % green. 127 prior tests still pass — zero regressions.**

## Files changed

### New

```
src/core/__init__.py
src/core/crash_handler.py
src/startup/__init__.py
src/startup/hardware.py
src/startup/_hw_detect.py
src/startup/env_guard.py
tests/test_pat_store_keyring.py
tests/test_hardware_profile.py
tests/test_crash_handler.py
tests/test_env_guard.py
tests/test_phase1_e2e.py
docs/CREDENTIAL_STORAGE.md
docs/STARTUP_HARDWARE.md
docs/PHASE1_COMPLETE.md          # this file
```

### Modified

```
main.py                     # added env_guard.enforce() + crash_handler.install() at top
requirements.txt            # added keyring>=24.3.0
installer/build_release.py  # added keyring to PACKAGES list
src/data/pat_store.py       # full rewrite — keyring-backed, same API
```

## What ships now

### 1.1 PAT storage over keyring
- Lightdash PAT, Gemini/Anthropic/Guru/Zendesk API keys, GitHub update token — all route to Windows Credential Manager / macOS Keychain via the `keyring` package.
- Non-secret UI state continues to live in `~/.alma-insights/ui_state.json`.
- One-time migration moves secrets out of the legacy `credentials.json`. Idempotent.
- Zero call-site changes — all 46+ existing call sites keep using `pat_store.load_setting()`.
- See [CREDENTIAL_STORAGE.md](CREDENTIAL_STORAGE.md) for module detail.

### 1.2 Hardware profile
- Detects CPU count, RAM, accelerator (CUDA / MPS / CPU), GPU name, VRAM.
- Derives embedding batch size + thread count + ACP worker count via small pure functions.
- Persists to `data/hardware_profile.json`, re-profiles on app-version change.
- Dev machine (12700K + 4070 Ti SUPER / 64 GB) → batch 120, CUDA, 8 ACP workers.
- M1 / 16 GB → batch 32, MPS, 4 threads, 6 workers.
- See [STARTUP_HARDWARE.md](STARTUP_HARDWARE.md) for module detail.

### 1.3 Global crash handler
- `sys.excepthook` replacement writes structured JSON reports to `data/crash_reports/`.
- Full traceback preserved — file paths, line numbers, frames intact.
- Only credential-shaped `key=value` patterns redacted.
- Chains to the previous hook so `qt_error_guard`'s Qt-scoped tracking keeps working.
- `list_reports()` + `export_bundle(dest)` + `clear(keep=N)` ready for the Settings → Support UI in Phase 5.
- Local-only. No telemetry, no upload.

### 1.4 Eager env guard
- Runs at the very top of `main.py`, before PySide6 / torch / sentence_transformers load.
- Sets 10 variables: HF air-gap + ML telemetry kill-switches + pip silent mode + `NO_PROXY=*`.
- Strips `localhost` proxy overrides, leaves remote corporate proxies alone.
- Subprocess-isolated test confirms the module imports without pulling any heavy dep.
- `scan_for_issues()` provides the diagnostic payload for startup Check 6 (Phase 2).

### 1.5 End-to-end composition
- `tests/test_phase1_e2e.py` replays the full startup sequence in one process: enforce env → install crash handler → migrate credentials → store a PAT → profile hardware → force a crash and verify capture.
- Confirms no import-order conflicts, no shared-state collisions.

## Gate verified

```bash
python -m pytest tests/test_pat_store_keyring.py \
                 tests/test_hardware_profile.py \
                 tests/test_crash_handler.py \
                 tests/test_env_guard.py \
                 tests/test_phase1_e2e.py -v
# → 97 passed in 0.48s

python -m pytest tests/test_source_monitor.py \
                 tests/test_guru_client.py \
                 tests/test_settings_manager.py \
                 tests/test_updater.py
# → 127 passed in 0.92s  (regression gate)
```

Live hardware probe on the dev machine:

```json
{
  "cpu_count": 20,
  "ram_gb": 63,
  "accelerator": "cuda",
  "gpu_name": "NVIDIA GeForce RTX 4070 Ti SUPER",
  "vram_gb": 15,
  "embedding_batch_size": 120,
  "embedding_threads": 1,
  "embedding_device": "cuda",
  "acp_max_workers": 8
}
```

## What's explicitly deferred

- **Consumer wiring** — [src/data/embedding/builder.py](../src/data/embedding/builder.py) still uses hardcoded `_BATCH_SIZE`. Replacing those constants with `hardware.load_or_profile()["embedding_batch_size"]` is a small follow-up; safe to defer until Phase 2 so we don't churn the embedding path twice.
- **Splash UI wiring** — the 4 modules above are standalone. The splash window + check framework that reads from them is Phase 2.
- **GitHub update-token seeding** — `github_update_token` is in `_SECRET_KEYS` but no code writes to it yet. Phase 3 will add the installer-time provisioning step.
- **Settings → Support "Export crash reports" button** — Phase 5 work.
- **Removal of src/data/embedding/air_gap.py** — its `enforce_air_gap()` is now a strict subset of `env_guard.enforce()`. Leaving it in place for defense-in-depth until Phase 2 refactors the embedding call path.

## Ready for Phase 2

The four Phase 1 modules compose cleanly. The splash can now call:

```python
from src.startup import env_guard, hardware
from src.data import pat_store
from src.core import crash_handler

env_guard.enforce()                              # Check 6
ok, backend = pat_store.keyring_available()       # Check 3
profile = hardware.load_or_profile(APP_VERSION)   # Check 7
crash_handler.install()                           # main.py top
```

Next up: `src/startup/checker.py`, `src/startup/splash_window.py`, and the eight individual check modules under `src/startup/checks/`.
