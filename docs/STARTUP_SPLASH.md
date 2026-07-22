# Startup Splash

**Modules:**
- [src/startup/checker.py](../src/startup/checker.py) — CheckResult + Checker
- [src/startup/splash_window.py](../src/startup/splash_window.py) — QDialog UI
- [src/startup/splash_row.py](../src/startup/splash_row.py) — per-check row widget
- [src/startup/checks/](../src/startup/checks/) — 9 individual check modules

**Tests:**
- [tests/test_checker.py](../tests/test_checker.py) — framework (12)
- [tests/test_startup_checks.py](../tests/test_startup_checks.py) — each check module (30)
- [tests/test_splash.py](../tests/test_splash.py) — UI (14)
- [tests/test_phase2_e2e.py](../tests/test_phase2_e2e.py) — end-to-end (6)

## What the user sees

Before the main window opens, a dark Alma-green splash runs the 9 health checks in order, streaming results into the UI. The **Continue** button only activates when every *critical* check has passed. A **Submit support ticket** button is always available for mid-launch failures.

**Mode-aware branding (2026-07-22).** The header, window title and footer follow the mode the app is about to open in, resolved via `app_modes.resolve_startup_mode()` (the `--mode` CLI override is parsed in `main.py` before the splash, so it is honored here too). Product keeps the classic "ALMA INSIGHTS / RCM ISSUE ANALYSIS"; enablement brands as **CONTENT COMMAND CENTER** (strings in [src/branding.py](../src/branding.py) and `splash_window._ENABLEMENT_BRANDING`). Any resolution failure falls back to the product branding — the splash never crashes over a label.

## Check matrix

| # | id | Check module | Critical? | Purpose |
|---|----|-------------|-----------|---------|
| 1 | `environment` | [environment.py](../src/startup/checks/environment.py) | ✓ | Python version + every core import succeeds |
| 2 | `credentials` | [credentials.py](../src/startup/checks/credentials.py) | ✓ | OS keyring reachable; auto-migrate legacy `credentials.json` |
| 3 | `gemini_oauth` | [gemini.py](../src/startup/checks/gemini.py) | ✓ | `gemini --prompt ping` returns 0 within 30 s |
| 4 | `update` | [updates.py](../src/startup/checks/updates.py) | — | Stub in Phase 2; real check lands in Phase 3 |
| 5 | `env_guard` | [airgap.py](../src/startup/checks/airgap.py) | ✓ | `enforce()` idempotent re-check; scans for stray proxies / `.env` |
| 6 | `hardware` | [hardware.py](../src/startup/checks/hardware.py) | — | Loads or refreshes `data/hardware_profile.json` |
| 7 | `embedding_model` | [model.py](../src/startup/checks/model.py) | — | Qwen3-Embedding-0.6B files present under `data/models/` |
| 8 | `database` | [database.py](../src/startup/checks/database.py) | ✓ (if DB exists) | `PRAGMA quick_check` + schema version + ticket count |
| 9 | `config` | [config.py](../src/startup/checks/config.py) | — | `data/settings.yaml` parses + required sections present |

Check 4 is intentionally a non-critical stub this phase. Phase 3 replaces it with the GitHub update poll.

## Architecture

```
SplashWindow (QDialog)
    ├── _build_header     mode-aware title + tagline + version
    │                     (product: "ALMA INSIGHTS" / "RCM ISSUE ANALYSIS";
    │                      enablement: "CONTENT COMMAND CENTER")
    ├── _build_rows_scroll
    │       └── SplashRow[]         (one per CheckResult)
    └── _build_footer
            ├── summary label       "N warnings, M non-critical failures …"
            ├── Submit support ticket button
            └── Continue button     (disabled until passed_critical is True)

Checker
    ├── run_next(on_result=add_row)   # called from a QTimer.singleShot chain
    └── passed_critical               # gates the Continue button
```

The splash runs on the main thread. Between checks it yields via `QTimer.singleShot(delay_ms, ...)` so the UI remains responsive and each row paints before the next check starts. Typical total splash time is ~5–8 s on first launch (dominated by the Python environment import step), then ~1 s on warm launches thanks to OS file caching.

## The `--no-splash` dev flag

When running from a checkout, `python main.py --no-splash` skips the UI entirely. The 9 checks run synchronously, results are printed to stdout, and the process aborts with exit code 1 if any critical check fails. This is a dev convenience — it does not ship to end users.

```text
[OK] Checking Python environment: Python 3.12.8 — 8 dependencies loaded
[OK] Checking credential store: Keyring accessible — 3 credential(s) stored
[OK] Gemini OAuth: Token valid — CLI responded
...
```

## Adding a new check

1. Create `src/startup/checks/your_check.py` exporting `check_<id>() -> CheckResult`.
2. Append `("your_id", check_your_check)` to `DEFAULT_CHECKS` in [checks/__init__.py](../src/startup/checks/__init__.py).
3. Add a class to `tests/test_startup_checks.py` covering the pass/warn/fail branches.

Keep each check module under ~100 lines. Never raise — the `Checker` traps exceptions and converts them to fail results, but the resulting row contains less useful remediation text. Return a `CheckResult` with a clear `remediation` string in every branch.

## Auto-fix wiring (Phase 2 placeholder)

`CheckResult.can_auto_fix` + `.auto_fix` are shipped in the dataclass but not yet surfaced in the UI. Phase 5 adds a small "Fix" button next to any row that carries an `auto_fix` callable.

## Matching the reference mock

The mock in `ALMA_INSIGHTS_CICD_PLAN.md` shows:
- Alma-green card on a slightly darker background ✓
- Centred wordmark, tagline, version ✓
- 9 rows with status icons + primary text + subtext ✓
- Footer summary line in amber when there are warnings ✓
- "Submit support ticket" (left) and "Continue" (right) buttons ✓

Differences from the mock (intentional):
- Mock shows `Python 3.12.4 — venv active`. We ship bundled Python with no venv, so the subtext reads `Python {major}.{minor}.{patch} — N dependencies loaded`.
- Mock labels the model row `all-MiniLM-L6-v2`. We ship `Qwen3-Embedding-0.6B`; the check uses the correct name.
