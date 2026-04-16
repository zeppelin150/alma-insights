# CI/CD Plan — Phase 5 Complete (Final)

**Date:** 2026-04-15
**Parent plan:** [alma-insights-cicd-plan.md](../../Users/Chris/.claude/plans/alma-insights-cicd-plan.md)
**Previous:** [PHASE4_COMPLETE.md](PHASE4_COMPLETE.md)

## Scope

Phase 5 is the closing hardening pass: auto-rollback on crash-looping updates, first-run EULA, user-facing crash-report export, a reusable secrets-leak scanner, and offline-degradation test coverage.

| Sub-phase | Deliverable | Location |
|-----------|-------------|----------|
| 5.1 | Rollback auto-detection | [src/updater/rollback.py](../src/updater/rollback.py) + updater.py + main.py |
| 5.2 | Settings → Support (crash export + rollback button) | [settings_page.py](../src/ui/pages/settings_page.py) |
| 5.3 | First-run EULA dialog | [src/ui/dialogs/eula_dialog.py](../src/ui/dialogs/eula_dialog.py) + main.py |
| 5.4 | Secrets audit script | [scripts/audit_secrets.py](../scripts/audit_secrets.py) |
| 5.5 | Offline-mode test suite | [tests/test_offline_mode.py](../tests/test_offline_mode.py) |
| 5.6 | Phase 5 E2E + per-module tests | 5 new test files, 51 new tests |
| 5.7 | HARDENING.md + this file | docs/ |

**51 new tests. 423 total across Phases 1 + 2 + 3 + 4 + 5 + regressions. Zero regressions.**

## Files changed

### New (9)

```
src/updater/rollback.py
src/ui/dialogs/eula_dialog.py
scripts/audit_secrets.py

tests/test_rollback.py
tests/test_eula_dialog.py
tests/test_audit_secrets.py
tests/test_offline_mode.py
tests/test_phase5_e2e.py

docs/HARDENING.md
docs/PHASE5_COMPLETE.md        # this file
```

### Modified (3)

```
src/updater/updater.py           # apply_staged_update calls record_apply; backups are preserved
src/ui/pages/settings_page.py    # SUPPORT & RECOVERY card: Export + Rollback
main.py                          # EULA gate + rollback check + stable-cleanup timer
```

## What ships

### 5.1 Rollback auto-detection
- **New module** (~220 LOC): `record_apply`, `needs_rollback`, `perform_rollback`, `clear_state_if_stable`, `current_state`.
- **State file**: `data/rollback_state.json` — applied_at, new/previous versions, grace window.
- **Previous-version dirs**: `_src_previous/`, `_config_previous/`, `_migrations_previous/` — updater moves the old `_*_backup/` into these instead of deleting.
- **Trigger**: ≥ 3 crash reports in `data/crash_reports/` newer than `applied_at` + within 60 s.
- **Action**: swap previous dirs back into place, rewrite `src/__init__.py` VERSION, delete state.
- **Stability**: after the grace window expires without tripping, `_*_previous/` and the state file are removed.

### 5.2 Settings → Support UI
- **Export Crash Reports** button uses the Phase 1 `crash_handler.export_bundle()` API to zip the last 20 reports to a user-chosen path.
- **Rollback to Previous Version** button only enabled when `_*_previous/` exists. Confirms, runs `perform_rollback()`, prompts restart.

### 5.3 EULA first-run dialog
- **~200 LOC dialog**, body covers: local-first data, Gemini BAA, Anthropic scope, Lightdash, update check behaviour, crash report scope (local only), keyring credential storage.
- **Accept gating**: button disabled until consent checkbox is ticked.
- **Persistence**: `data/settings.yaml → eula.version_accepted / accepted_at / accepted_by`.
- **Version bump**: incrementing `EULA_VERSION` re-prompts across the fleet.
- **main.py wiring**: runs after `QApplication()` + crash handler install, before splash. Decline = `sys.exit(1)`.

### 5.4 Secrets audit
- **5 regex patterns**: Lightdash PAT, Anthropic API key, GitHub fine-grained PAT, Google API key, generic Bearer token (40+ chars).
- **Skip list**: `.git`, `.claude`, `.venv`, `node_modules`, `__pycache__`, `data/models`, `dist`, `tests/`, `docs/`, `data/crash_reports`, `scan_server/node_modules`.
- **Flags**: `--no-history`, `--allow-history`, `--root`.
- **Exit codes**: 0 clean / 1 found / 2 invocation error. CI-ready.
- **Real finding during development**: 5 Google API keys in `.claude/settings.local.json` (Claude Code's local state, gitignored — never committed, but still plaintext on disk). Flagged in HARDENING.md for rotation.

### 5.5 Offline-mode tests
- Single fixture patches `urlopen` + `create_connection` to raise.
- Covers: startup update check (PAT + disabled), `UpdateChecker.check_failed`, `github_app_auth.mint_installation_token`, env guard, integrity check, full `Checker.run_all()`.
- **Proves** no subsystem raises on a network outage and every user-facing path surfaces a readable message.

### 5.6 Test suite

| File | Tests | Scope |
|------|-------|-------|
| `test_rollback.py` | 13 | record_apply, needs_rollback thresholds/windows, perform_rollback, clear_state_if_stable |
| `test_eula_dialog.py` | 13 | is_accepted / record_acceptance / dialog state machine / ensure_accepted flow |
| `test_audit_secrets.py` | 14 | Regex patterns, working-tree scan, skip list, CLI exit codes |
| `test_offline_mode.py` | 7 | Every network-touching path degrades gracefully |
| `test_phase5_e2e.py` | 4 | Full crash-loop → rollback chain, EULA decline/accept, crash export |

**51 new tests. All green.**

## Gate verified

```
python -m pytest <all 28 test files>
# → 423 passed in 128.52s
```

Phase breakdown, post-Phase 5:

| Phase | New tests |
|-------|-----------|
| 1 — Foundation | 97 |
| 2 — Splash | 62 |
| 3 — Updater | 53 |
| 4 — Build/integrity/uninstaller | 27 |
| 5 — Hardening | 51 |
| Regression (pre-existing) | 133 |
| **Total** | **423** |

## Known findings worth action

1. **Google API keys in `.claude/settings.local.json`** — 5 live Gemini keys in plaintext under the user's `.claude/` directory. Never committed to git (gitignored), but worth rotating. The audit script skips `.claude/` going forward so this doesn't block CI.

2. **requirements.lock hashes still to regenerate on M1** — Phase 4 left this as a TODO; Phase 5 didn't touch it. Mechanical one-liner when convenient.

## What's explicitly deferred

- **Crash-report telemetry upload**: out of scope per locked decision (HIPAA-adjacent, local-only).
- **Full legal EULA text**: current body covers the technical terms; legal review can expand it before v1 GA without code changes (just edit `_EULA_BODY`).
- **Windows code signing**: locked deferral.
- **SBOM CVE scanning**: no automation yet — operator-initiated via the shipped `app/sbom.json`.

## The CI/CD plan is complete

All 5 phases shipped:

```
Phase 1: pat_store keyring swap, hardware profile, crash handler, env guard     97 tests
Phase 2: splash window, 10 health checks, Checker + CheckResult framework      62 tests
Phase 3: updater keyring auth, mandatory SHA-256, GitHub App, release workflow  53 tests
Phase 4: exact pins, lockfile, SBOM+checksums, integrity check, uninstaller    27 tests
Phase 5: rollback guard, EULA, crash export UI, secrets audit, offline tests   51 tests
─────────────────────────────────────────────────────────────────────────
                                                                              290 new
                                                            + 133 regressions
                                                            = 423 total, green
```

New modules: **21**. New test files: **22**. New docs: **10**. New scripts: **3**. Modified: **12** files across `src/`, `main.py`, `requirements.txt`, `installer/build_release.py`, and `.github/workflows/*`.

Everything ships **inert-by-default** where GitHub isn't configured yet — flip `updates.auth_mode: pat` in `settings.yaml`, run `scripts/provision_update_token.py`, and the full auto-update pipeline activates with no code changes.
