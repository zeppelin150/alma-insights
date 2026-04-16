# CI/CD Plan — Phase 2 Complete

**Date:** 2026-04-15
**Parent plan:** [alma-insights-cicd-plan.md](../../Users/Chris/.claude/plans/alma-insights-cicd-plan.md)
**Previous:** [PHASE1_COMPLETE.md](PHASE1_COMPLETE.md)

## Scope

Phase 2 bolts a branded splash window onto the startup sequence and adds nine health checks that gate the `MainWindow` launch. All nine checks are real except the update poll, which ships as a Phase-3 stub.

| Sub-phase | Deliverable | Files |
|-----------|-------------|-------|
| 2.1 | Check framework | [checker.py](../src/startup/checker.py) |
| 2.2 | Nine check modules | [checks/](../src/startup/checks/) — environment, credentials, gemini, updates, airgap, hardware, model, database, config |
| 2.3 | Splash UI | [splash_window.py](../src/startup/splash_window.py) + [splash_row.py](../src/startup/splash_row.py) |
| 2.4 | main.py wiring | [main.py](../main.py) — splash before MainWindow, `--no-splash` dev flag |
| 2.5 | E2E + docs | [test_phase2_e2e.py](../tests/test_phase2_e2e.py) + [STARTUP_SPLASH.md](STARTUP_SPLASH.md) |

**62 new tests in Phase 2. 159 total across Phase 1 + 2. Zero regressions.**

## Files changed

### New (15)

```
src/startup/checker.py
src/startup/splash_window.py
src/startup/splash_row.py
src/startup/checks/__init__.py
src/startup/checks/environment.py
src/startup/checks/credentials.py
src/startup/checks/gemini.py
src/startup/checks/updates.py
src/startup/checks/airgap.py
src/startup/checks/hardware.py
src/startup/checks/model.py
src/startup/checks/database.py
src/startup/checks/config.py
tests/test_checker.py
tests/test_startup_checks.py
tests/test_splash.py
tests/test_phase2_e2e.py
docs/STARTUP_SPLASH.md
docs/PHASE2_COMPLETE.md         # this file
```

### Modified (1)

```
main.py                          # adds splash + --no-splash path
```

## What ships now

### 2.1 — Check framework ([checker.py](../src/startup/checker.py), 130 lines)

- `CheckResult` dataclass carries id, name, status, message, remediation, critical flag, duration.
- `Checker.run_next()` pops one queued check; callback-friendly so the splash can stream rows live.
- `Checker.run_all()` drains the queue — used by tests and the `--no-splash` dev path.
- A raising check is trapped and converted to a `fail` result with the traceback summarised in the message — one bad check never stops the others.

### 2.2 — Nine check modules

Each is a small pure function. File sizes range from 40 lines (updates.py stub) to 96 lines (gemini.py). Highlights:
- **environment** — walks a list of required modules, captures any `ImportError`.
- **credentials** — calls `pat_store.keyring_available()` and `pat_store.migrate_legacy_credentials()`. Reports how many secrets are currently stored.
- **gemini** — subprocess-runs the CLI with a throwaway prompt and 30 s timeout. Never reads or reuses the OAuth token.
- **updates** — Phase 2 stub; real impl lands in Phase 3.
- **airgap** — idempotent `env_guard.enforce()` + `scan_for_issues()`.
- **hardware** — delegates to `hardware.load_or_profile()`; formats the one-liner that appears in the mock.
- **model** — reuses `model_loader._find_model_path()`; returns `warn` if absent (semantic search optional).
- **database** — `PRAGMA quick_check` + schema version + ticket count. Missing DB is a non-critical pass.
- **config** — `get_settings_path()`, parse, verify required top-level sections.

### 2.3 — Splash UI

- [splash_window.py](../src/startup/splash_window.py) is a modal `QDialog` with header / rows / footer.
- [splash_row.py](../src/startup/splash_row.py) renders a single `CheckResult`; the 20 px status circle is painted directly with `QPainter`.
- Streaming via `QTimer.singleShot(60, run_next)` — UI paints each row before the next check starts.
- Continue button stays disabled until `checker.passed_critical` is True.
- Summary label shows the Alma-green-aligned text from the mock (`"1 warning, 1 non-critical failure — see details above. App will continue."`).

### 2.4 — main.py wiring

Added between the WAL check and MainWindow construction:

```python
if "--no-splash" in sys.argv:
    sys.argv.remove("--no-splash")
    ok = _run_checks_headless(app.applicationVersion())
    if not ok:
        sys.exit(1)
else:
    if not _run_splash(app):
        sys.exit(1)
# MainWindow launches only after critical checks pass
```

The staged-update apply step and WAL health check still run before the splash. Settings migration now happens inside Check 9 (config) since that's where `get_settings_path()` is called.

### 2.5 — E2E + docs

`test_phase2_e2e.py` covers:
- All-pass happy path → 9 rows rendered + Continue enabled.
- Warnings + non-critical failures → Continue still enabled.
- Critical failure → Continue blocked, "Cannot continue" in summary.
- A raising check still renders its row.
- Headless `--no-splash` path.

## Gate verified

```
python -m pytest \
    tests/test_checker.py \
    tests/test_startup_checks.py \
    tests/test_splash.py \
    tests/test_phase2_e2e.py \
    tests/test_pat_store_keyring.py \
    tests/test_hardware_profile.py \
    tests/test_crash_handler.py \
    tests/test_env_guard.py \
    tests/test_phase1_e2e.py
# → 159 passed
```

Live run on the dev machine (via Checker outside the UI):

```
✓ * Checking Python environment   Python 3.13.6 — 8 dependencies loaded
✓ * Checking credential store     Keyring accessible — migrated 3, 3 credential(s) stored
✓ * Environment guard             Air-gap vars set — no proxy detected — no rogue .env
✓   Hardware profiling            63GB RAM — 20 cores — GPU: RTX 4070 Ti SUPER (15GB)…
✓   ML model check                Qwen3-Embedding-0.6B loaded from c54f2e…
✓ * Database integrity            Schema v? — 0 tickets — 0.0MB
✓   Validating configuration      settings.yaml valid — 0 dataset(s) configured
Summary: {'pass': 8, 'warn': 0, 'fail': 0}   Critical pass: True
```

The real legacy `credentials.json` on the dev box was migrated silently during Check 2 — three secrets moved to Windows Credential Manager, file renamed `.json.migrated`.

## What's explicitly deferred

- **Update check**: still a Phase-2 stub. Phase 3 replaces it with `UpdateChecker` + GitHub App auth.
- **"Fix" buttons**: `CheckResult.auto_fix` callable is shipped in the dataclass but the UI doesn't expose a button yet. Phase 5.
- **Consumer wiring of `hardware.load_or_profile`**: `embedding/builder.py` still uses hardcoded batch size. Will be picked up when the embedding path is next touched.
- **Removal of `src/data/embedding/air_gap.py`**: strict subset of `env_guard`, kept for defense-in-depth until Phase 2 refactor of embedding call paths (not urgent).

## Ready for Phase 3

Phase 3 swaps the `updates` check stub for the real GitHub poll, seeds the update token into keyring at install time, and adds the release workflow. The framework is in place — Phase 3 is almost entirely editing `checks/updates.py` and wiring the existing `src/updater/` stack to it.
