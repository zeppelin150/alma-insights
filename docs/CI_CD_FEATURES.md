# Alma Insights — CI/CD Feature Inventory

**Audience:** engineering team
**As of:** 2026-04-16
**Build/deploy posture:** **inert-by-default, activation-ready**. Every feature below ships in the codebase today. Items tagged `[needs-github]` stay dormant until the private release repo is live; items tagged `[needs-apple-cert]` stay dormant until the Developer ID is added to Actions secrets.

---

## 1. Credential storage — OS keyring

**Modules:** [src/data/pat_store.py](../src/data/pat_store.py), [docs/CREDENTIAL_STORAGE.md](CREDENTIAL_STORAGE.md)

- Every secret (Lightdash PAT, Gemini/Anthropic/Guru/Zendesk API keys, GitHub update token) stored in Windows Credential Manager / macOS Keychain via the `keyring` package.
- Non-secret UI state routed to `~/.alma-insights/ui_state.json`.
- One-time migration from the pre-Phase-1 `credentials.json` runs on first launch (Check 2).
- Zero call-site changes: 46+ existing `pat_store.load_setting()` / `save_setting()` sites keep working.
- Diagnostic probe: `pat_store.keyring_available() -> (ok, backend_name)`.

**Test coverage:** 24 unit tests (`test_pat_store_keyring.py`).

---

## 2. Hardware profiling

**Modules:** [src/startup/hardware.py](../src/startup/hardware.py), [src/startup/_hw_detect.py](../src/startup/_hw_detect.py), [docs/STARTUP_HARDWARE.md](STARTUP_HARDWARE.md)

- Detects CPU count, RAM, and best-available accelerator (CUDA / MPS / CPU) at first launch.
- Heuristics derive `embedding_batch_size`, `embedding_threads`, `acp_max_workers` per machine.
- Profile cached at `data/hardware_profile.json`; re-runs only on app-version change or corrupt cache.
- Consumer wiring for `embedding/builder.py` hardcoded constants → profile values is listed as a pending follow-up (not done; wiring is a one-line change per call site).

**Verified runs:**
- i7-12700K + RTX 4070 Ti SUPER (64 GB) → batch 120 · 8 workers · CUDA
- MacBook Pro M1 (16 GB) → batch 32 · 6 workers · MPS (expected, not yet run on prod)

**Test coverage:** 27 unit tests (`test_hardware_profile.py`).

---

## 3. Global crash handler

**Module:** [src/core/crash_handler.py](../src/core/crash_handler.py)

- Replaces `sys.excepthook`; every uncaught Python exception produces a structured JSON report at `data/crash_reports/crash_<timestamp>.json`.
- **Local-only.** No telemetry, no network.
- Credential-shaped `key=value` patterns redacted in both traceback and exception message. Full frame/line/path info preserved.
- Chains to the previous hook (preserves `qt_error_guard`'s Qt-scoped tracking).
- Public helpers: `list_reports(limit=20)`, `export_bundle(dest, limit=20)`, `clear(keep=0)`.
- Wired to `Settings → Updates → Support & Recovery → Export Crash Reports`.

**Test coverage:** 22 unit tests (`test_crash_handler.py`).

---

## 4. Eager air-gap environment guard

**Module:** [src/startup/env_guard.py](../src/startup/env_guard.py)

- Sets 10 environment variables at the top of `main.py`, **before any heavy import**:
    - `HF_HUB_DISABLE_TELEMETRY=1`, `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `HF_DATASETS_OFFLINE=1`
    - `SENTENCE_TRANSFORMERS_HOME=<bundle>/data/models`
    - `WANDB_DISABLED=true`, `MLFLOW_TRACKING_URI=""`, `TOKENIZERS_PARALLELISM=false`
    - `PIP_DISABLE_PIP_VERSION_CHECK=1`, `NO_PROXY=*`
- Strips localhost proxy overrides (`HTTP_PROXY=http://localhost:...`) automatically; leaves remote corporate proxies untouched.
- Subprocess-isolated test proves the module imports with only stdlib — critical for the "eager" part.
- Runtime scanner `scan_for_issues(app_root)` detects residual issues (stray proxies, `.env` file).

**Test coverage:** 20 unit tests (`test_env_guard.py`).

---

## 5. Startup splash with 10 health checks

**Modules:** [src/startup/checker.py](../src/startup/checker.py), [src/startup/splash_window.py](../src/startup/splash_window.py), [src/startup/splash_row.py](../src/startup/splash_row.py), [src/startup/checks/](../src/startup/checks/), [docs/STARTUP_SPLASH.md](STARTUP_SPLASH.md)

- Alma-green branded modal that intercedes between `QApplication()` and `MainWindow()`.
- Streams results into rows via `QTimer.singleShot` — UI stays responsive.
- **Continue** button gated on `passed_critical` (all critical checks green).
- `--no-splash` dev flag skips UI and runs checks synchronously to stdout.
- Check order:

| # | Check | Critical? | Purpose |
|---|-------|-----------|---------|
| 1 | Bundle integrity | no | Samples 25 files vs `checksums.json`; > 5 mismatches → fail critical |
| 2 | Python environment | yes | Version + every core dep imports |
| 3 | Credential store | yes | Keyring reachable; legacy migration |
| 4 | Gemini OAuth | yes | `gemini --prompt ping` within 30 s |
| 5 | Update check | no | Auth-mode-aware GitHub poll (5 s timeout) |
| 6 | Environment guard | yes (auto-fix) | Idempotent enforce + proxy/.env scan |
| 7 | Hardware profile | no | CPU/RAM/accelerator detection |
| 8 | Embedding model | no | Qwen3-Embedding-0.6B files present |
| 9 | Database integrity | yes if present | `PRAGMA quick_check` + schema version |
| 10 | Configuration | no | `settings.yaml` parse + required sections |

**Test coverage:** 12 checker + 30 per-check + 14 splash + 6 E2E = 62 tests.

---

## 6. Auto-update with mandatory integrity verification

**Modules:** [src/updater/update_checker.py](../src/updater/update_checker.py), [src/updater/updater.py](../src/updater/updater.py), [src/updater/github_app_auth.py](../src/updater/github_app_auth.py), [src/updater/release_manifest.py](../src/updater/release_manifest.py), [docs/UPDATES.md](UPDATES.md)

**Three auth modes** driven by `settings.yaml → updates.auth_mode`:

| mode | Token source | Phase |
|------|-------------|-------|
| `disabled` (default) | — | shipped, inert |
| `pat` | OS keyring (`github_update_token`) | shipped `[needs-github]` |
| `github_app` | JWT → installation token (8 h TTL) | shipped `[needs-github]` |

**Integrity guarantee:**
- `Updater.stage(url, sha256, version)` **refuses synchronously** without a valid SHA-256. Socket never opens without a checksum.
- `require_checksum=False` is an explicit dev-only opt-out — documented in the docstring, exercised in the test suite.
- `release_manifest.json` published per release pins a SHA for each artifact.

**Auth error translation:** user-facing messages for 401, 403, 404, and network errors.

**Test coverage:** 30 pre-existing + 5 integrity + 13 Phase 3 + 10 GitHub App + 8 check + 5 E2E + 12 manifest + 5 provisioner + 7 offline = 95 tests across the update surface.

---

## 7. GitHub Actions workflows

**Files:** [.github/workflows/ci.yml](../.github/workflows/ci.yml), [.github/workflows/build.yml](../.github/workflows/build.yml), [.github/workflows/release.yml](../.github/workflows/release.yml)

### CI (`ci.yml`)
- Trigger: every push + PR
- Matrix: Ubuntu + Windows + macOS-14
- Installs from `requirements.lock`
- Runs Phase 1 / 2 / 3 / 4 / 5 test groups in sequence (fast-fail)
- Separate secret-scan job runs `scripts/audit_secrets.py` on the full git history

### Build (`build.yml`)
- Trigger: PR touching installer/requirements/src + manual dispatch
- **3-leg matrix:** Win x64 + macOS-14 arm64 + macOS-13 Intel x64
- `actions/cache` on `installer/cache/` + `~/.cache/huggingface` keyed by build script + lockfile hashes → ~70 % time reduction after first warm
- Uploads artifacts with 14-day retention

### Release (`release.yml`) `[needs-github]`
- Trigger: `v*` tag push + manual dispatch with tag input
- Same 3-matrix build + per-leg macOS signing via [installer/ci/sign_macos.sh](../installer/ci/sign_macos.sh) `[needs-apple-cert]`
- Aggregation job builds `release_manifest.json`
- Publishes a GitHub Release with `softprops/action-gh-release@v2` and auto-generated notes

---

## 8. Reproducible builds — exact version pinning

**Files:** [requirements.txt](../requirements.txt), [requirements.lock](../requirements.lock), [installer/build_release.py](../installer/build_release.py)

- All 13 runtime deps pinned with `==`. `numpy` held at 1.26.4 for torch compat.
- `requirements.lock` carries direct pins + documented regen procedure (`pip-compile --generate-hashes` on M1). `--require-hashes` CI enforcement is **ready to turn on** once the lockfile is regenerated with hashes on the prod target.
- `PACKAGES` list in `build_release.py` mirrors `requirements.txt` — tests enforce parity.
- Python 3.12.8 (embeddable on Windows, python-build-standalone on macOS) and Node 22.14.0 pinned.
- Gemini CLI pinned: `@google/gemini-cli@0.36.0` (bridge-validated). Install failure is **fail-fast** (`sys.exit(1)`) instead of the pre-Phase-4 silent continue.
- Qwen3-Embedding-0.6B pre-bundled; model directory skipped from checksums.

**Test coverage:** 7 build tests (`test_phase4_build.py`) — pin enforcement, parity checks, SBOM schema, checksum skip rules.

---

## 9. In-bundle SBOM + checksums

**Generated at build time** by [installer/build_release.py::write_sbom / write_checksums](../installer/build_release.py). Shipped inside every zip at:

- `app/sbom.json` — full `pip list --format=json` snapshot with schema envelope
- `app/checksums.json` — SHA-256 of every `app/**/*.py` (skips `__pycache__`, `data/`)

**Runtime use:** [src/startup/checks/integrity.py](../src/startup/checks/integrity.py) samples 25 files per launch. Mismatch counts map to check status:
- 0 → pass
- 1–5 → warn, lists the changed files
- > 5 → fail critical (blocks the app)

Absent `checksums.json` → "dev checkout" pass (doesn't block development).

**Test coverage:** 9 integrity tests (`test_phase4_integrity.py`).

---

## 10. Uninstaller

**Files:** [installer/uninstall.py](../installer/uninstall.py), [installer/uninstall_win.bat](../installer/uninstall_win.bat), [installer/uninstall_mac.command](../installer/uninstall_mac.command), [docs/BUILD_AND_RELEASE.md](BUILD_AND_RELEASE.md)

- **Preserves user data by default** — `app/data/` is moved to `.AlmaInsights_data_backup/` alongside the install dir.
- Keyring cleanup is opt-in (confirmation prompt, or `--yes`). Only the 6 Alma-scoped secret keys are touched.
- Desktop shortcut removed best-effort.
- Flags: `--install-dir`, `--yes`, `--purge-data`, `--keep-secrets`, `--dry-run`.
- Exit codes: 0 success · 1 declined · 2 install dir missing · 3 filesystem error.
- Shipped in every bundle alongside the install + launcher scripts.

**Test coverage:** 11 tests (`test_uninstaller.py`) including subprocess CLI coverage.

---

## 11. Auto-rollback guard

**Modules:** [src/updater/rollback.py](../src/updater/rollback.py), [docs/HARDENING.md](HARDENING.md)

- `updater.apply_staged_update` now preserves the previous version at `_src_previous/`, `_config_previous/`, `_migrations_previous/` instead of deleting backups.
- `data/rollback_state.json` records apply event + 60 s grace window.
- `main.py` calls `needs_rollback()` **before** importing any `src.*` module — if ≥ 3 crash reports exist newer than `applied_at`, `perform_rollback()` swaps previous dirs back into place and rewrites `src/__init__.py VERSION`.
- 65 s after `MainWindow.show()`, `clear_state_if_stable()` removes `_*_previous/` and state — the update is considered stable.
- Manual rollback button in `Settings → Updates → Support & Recovery`, enabled only during the grace window.

**Test coverage:** 13 rollback + 4 Phase 5 E2E = 17 tests.

---

## 12. First-run EULA

**Modules:** [src/ui/dialogs/eula_dialog.py](../src/ui/dialogs/eula_dialog.py), [docs/HARDENING.md](HARDENING.md)

- Modal `QDialog` shown after `QApplication()` + crash handler install, before the splash.
- **Accept** button gated on consent checkbox.
- Body covers: local-first data, PII redaction, Gemini BAA scope, Anthropic scope, Lightdash, update check scope, crash report scope (local only), keyring credential storage, no-warranty clause.
- Acceptance persisted to `data/settings.yaml → eula.version_accepted / accepted_at / accepted_by`.
- Bumping `EULA_VERSION` constant re-prompts the fleet on next launch.
- Decline = `sys.exit(1)`.

**Test coverage:** 13 tests (`test_eula_dialog.py`).

---

## 13. Secrets-leak scanner

**Modules:** [scripts/audit_secrets.py](../scripts/audit_secrets.py), [docs/HARDENING.md](HARDENING.md)

- 5 regex patterns: Lightdash PAT, Anthropic API key, GitHub fine-grained PAT, Google API key, generic Bearer token.
- Scans working tree + optional git history.
- Skips `.git`, `.claude`, `.venv`, `node_modules`, `__pycache__`, `data/models`, `dist`, `tests/` (fixture tokens), `docs/` (pattern examples), `data/crash_reports`, `scan_server/node_modules`.
- Exit codes: 0 clean · 1 found · 2 invocation error.
- **Wired into CI** — `ci.yml` runs this as a separate blocking job.
- Runnable locally: `python scripts/audit_secrets.py`.

**Finding from dev run:** 5 Google API keys in `.claude/settings.local.json` (Claude Code's local state, gitignored — never committed). Documented in HARDENING.md with rotation recommendation.

**Test coverage:** 14 tests (`test_audit_secrets.py`) — patterns, skip list, CLI exit codes.

---

## 14. Offline-mode resilience

**Module:** [tests/test_offline_mode.py](../tests/test_offline_mode.py)

- Single pytest fixture patches `urlopen` + `socket.create_connection` to raise.
- Exercises every network-touching subsystem: update check (PAT + disabled), `UpdateChecker.check_failed`, `github_app_auth.mint_installation_token`, env guard, integrity, full `Checker.run_all()`.
- **Guarantee:** no subsystem raises; every user-facing path surfaces a readable message.

**Test coverage:** 7 tests.

---

## 15. Install-time token provisioner

**Module:** [scripts/provision_update_token.py](../scripts/provision_update_token.py)

- Seeds the GitHub update PAT into the OS keyring.
- Sources (mutually exclusive): `--token VALUE`, `--from-env VAR`, `--from-file PATH`, interactive `getpass`.
- `--clear` removes the stored token (idempotent). `--force` overwrites existing. `--quiet` for CI.
- Exit codes for `set -e` pipelines: 0 / 1 / 2.

**Test coverage:** 5 subprocess tests (`test_provision_update_token.py`).

---

## 16. Release manifest builder

**Module:** [src/updater/release_manifest.py](../src/updater/release_manifest.py) + [scripts/make_release_manifest.py](../scripts/make_release_manifest.py)

- Post-build step called by `release.yml`: walks `dist/` for `*.zip`/`*.dmg`/`*.exe`/`*.pkg`, computes SHA-256 for each, writes `dist/release_manifest.json`.
- Manifest shipped as a release asset alongside the binaries.
- Updater consumers call `lookup_artifact(manifest, name)` → `(sha, size)` to get the expected SHA before downloading.

**Test coverage:** 12 tests (`test_release_manifest.py`).

---

## 17. macOS signing hooks `[needs-apple-cert]`

**Modules:** [installer/ci/sign_macos.sh](../installer/ci/sign_macos.sh), [.github/workflows/release.yml](../.github/workflows/release.yml)

- `codesign --options runtime --timestamp` + `xcrun notarytool submit --wait` + `xcrun stapler staple`.
- **Inert until secrets present:** exits clean if any of `APPLE_ID`, `APPLE_TEAM_ID`, `APPLE_APP_PASSWORD`, `APPLE_SIGNING_IDENTITY` are missing.
- Runs in-matrix on both macOS legs during `release.yml`.
- **Windows signing deferred** per locked decision (users accept SmartScreen today).

---

## Feature/file dependency map

```
main.py ─────────────┬── env_guard (eager, first)
                     ├── crash_handler.install (early)
                     ├── apply_staged_update (file swap; before src imports)
                     │     └── rollback.record_apply (preserve previous, state file)
                     ├── rollback.needs_rollback → perform_rollback (auto-revert)
                     ├── QApplication + theme + QtErrorGuard
                     ├── eula_dialog.ensure_accepted (block/exit)
                     ├── WAL health check
                     ├── SplashWindow + Checker(DEFAULT_CHECKS)
                     │     └── 10 checks: integrity / environment / credentials /
                     │         gemini_oauth / updates / airgap / hardware /
                     │         model / database / config
                     ├── MainWindow.show()
                     └── QTimer.singleShot(65s, clear_state_if_stable)

Settings → Updates ──┬── Check for Updates (UpdateChecker)
                     ├── Install Now   (Updater.stage — mandatory SHA-256)
                     ├── Restart Now
                     ├── Rollback to Previous Version (perform_rollback)
                     └── Export Crash Reports (crash_handler.export_bundle)
```

---

## Test count by phase

| Phase | Files | Tests |
|-------|-------|-------|
| 1 — Foundation | 5 | 97 |
| 2 — Splash | 4 | 62 |
| 3 — Updater | 7 | 53 |
| 4 — Build / integrity / uninstaller | 3 | 27 |
| 5 — Hardening | 5 | 51 |
| Regression (pre-existing) | 4 | 133 |
| **Total** | **28** | **423** |

---

## Known gaps / future work

| Gap | Impact | Effort to close |
|-----|--------|----------------|
| `requirements.lock` hashes not yet generated (`pip-compile --generate-hashes` on M1) | CI `--require-hashes` still off | 5 min on the prod Mac |
| Windows code signing | SmartScreen warnings on end-user install | $400/yr EV cert + signtool wiring |
| Auto-update ignores `scan_server/` and `node_modules/` | Safe today (those dirs aren't replaced by updates) | Out of scope |
| Update offers the same "bad" version again after auto-rollback | User may re-install a crashing version | Add `data/bad_versions.json` and filter in `UpdateChecker`; ~40 LOC |
| EULA text needs legal review before GA | Current body is technical + HIPAA-aligned but not formal legalese | One PR + `EULA_VERSION` bump |
| No UI toast after auto-rollback | User only sees stderr line `[startup] Auto-rollback …` | ~20 LOC in `MainWindow.__init__` reading `rollback_state.json` |
| `provision_update_token.py` not auto-invoked by installer | Operator must run once per machine | Wire into `install.py` if `ALMA_UPDATE_TOKEN` env var is present; ~15 LOC |
| Gemini OAuth interactive fallback not wired in splash | Check 4 gives remediation text but doesn't launch terminal | ~25 LOC in `checks/gemini.py` auto_fix callable |
| Hardware profile values not consumed by `embedding/builder.py` yet | `_BATCH_SIZE` still hardcoded; profile is informational | One-line change per consumer |
| SBOM not auto-scanned for CVEs | Operator runs `cat app/sbom.json` manually | HIPAA posture says keep local — no change recommended |

---

## Activation checklist (when you're ready)

### Minimum viable (auto-update works):
1. Create private GitHub repo, push code
2. Generate fine-grained PAT (Contents: read-only on the repo)
3. `python scripts/provision_update_token.py --token <pat>` on each machine
4. Add `updates: { auth_mode: pat, github_repo: org/repo }` to `data/settings.yaml`
5. `git tag v9.3.0 && git push origin v9.3.0` → release.yml builds + publishes

### Full production posture:
6. Buy Apple Developer ID (~$99/yr)
7. Add 4 Apple secrets to Actions → macOS signing + notarization activates
8. Regenerate `requirements.lock` on M1 with `pip-compile --generate-hashes`
9. Flip CI to `pip install --require-hashes` for supply-chain lockdown
10. Optional: migrate `auth_mode: pat` → `auth_mode: github_app` for org-centralized auth rotation

**Every item above runs on the shipped code — no further engineering needed.**
