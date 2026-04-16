# CI/CD Plan — Phase 4 Complete

**Date:** 2026-04-15
**Parent plan:** [alma-insights-cicd-plan.md](../../Users/Chris/.claude/plans/alma-insights-cicd-plan.md)
**Previous:** [PHASE3_COMPLETE.md](PHASE3_COMPLETE.md)

## Scope

Phase 4 is the build + supply-chain hardening pass. Exact version pinning, fail-fast Gemini CLI, 3-matrix build (Win/macOS-arm64/macOS-x64), build caching, a CI test workflow, in-bundle SBOM + checksums, a runtime integrity check, and a user-data-preserving uninstaller.

| Sub-phase | Deliverable | Location |
|-----------|-------------|----------|
| 4.1 | Exact pins + lockfile | [requirements.txt](../requirements.txt), [requirements.lock](../requirements.lock) |
| 4.2 | Gemini CLI fail-fast + version pin | [installer/build_release.py](../installer/build_release.py) |
| 4.3 | macOS x86_64 matrix leg | [.github/workflows/build.yml](../.github/workflows/build.yml) |
| 4.4 | actions/cache for heavy downloads | [build.yml](../.github/workflows/build.yml), [release.yml](../.github/workflows/release.yml) |
| 4.5 | CI test workflow | [.github/workflows/ci.yml](../.github/workflows/ci.yml) |
| 4.6 | SBOM + checksums + integrity check | [build_release.py](../installer/build_release.py), [checks/integrity.py](../src/startup/checks/integrity.py) |
| 4.7 | Uninstaller | [installer/uninstall.py](../installer/uninstall.py), [uninstall_win.bat](../installer/uninstall_win.bat), [uninstall_mac.command](../installer/uninstall_mac.command) |
| 4.8 | Tests | 3 new test files, 27 new tests |
| 4.9 | Docs + sweep | [BUILD_AND_RELEASE.md](BUILD_AND_RELEASE.md), this file |

**27 new tests. 372 total across Phases 1 + 2 + 3 + 4 + regressions. Zero regressions.**

## Files changed

### New (10)

```
requirements.lock                              # lockfile with transitive-dep regen procedure
installer/uninstall.py                         # cross-platform uninstaller
installer/uninstall_win.bat                    # Windows entry point
installer/uninstall_mac.command                # macOS entry point
.github/workflows/ci.yml                       # fast test gate
src/startup/checks/integrity.py                # runtime checksum verifier
tests/test_phase4_build.py                     # pinning + SBOM + checksums
tests/test_phase4_integrity.py                 # integrity check unit tests
tests/test_uninstaller.py                      # uninstaller logic + CLI
docs/BUILD_AND_RELEASE.md
docs/PHASE4_COMPLETE.md                        # this file
```

### Modified (6)

```
requirements.txt                               # >= → == for every entry
installer/build_release.py                     # pins, fail-fast, write_sbom, write_checksums, uninstaller copy
.github/workflows/build.yml                    # matrix + caching, rewritten
.github/workflows/release.yml                  # cache key now includes requirements.lock
src/startup/checks/__init__.py                 # integrity becomes Check 1 (9 → 10 checks)
tests/test_startup_checks.py                   # updated TestRegistry for 10 checks
```

## What ships

### 4.1 Exact version pinning
- Every entry in `requirements.txt` moved from `>=` to `==`. `numpy` intentionally held at 1.26.4 (torch + sentence-transformers compat).
- `PACKAGES` list in `build_release.py` mirrors the file exactly — `tests/test_phase4_build.py::test_packages_match_requirements_txt` enforces parity.
- `requirements.lock` ships with direct pins + a documented regeneration procedure (`pip-compile --generate-hashes` on M1). CI enforcement of `--require-hashes` is ready to turn on once the lockfile is regenerated there.

### 4.2 Gemini CLI fail-fast
- `GEMINI_CLI_PACKAGE` changed from `@google/gemini-cli` → `@google/gemini-cli@0.36.0` (bridge-validated version).
- `install_gemini_cli()` now `sys.exit(1)` on failure with full stderr/stdout context, instead of silently shipping a broken bundle.

### 4.3 macOS x86_64 matrix leg
- `build.yml` rewrite: one `matrix.include` with Win x64, macOS-14 (arm64), macOS-13 (Intel x64). `release.yml` already had this from Phase 3; both now share the same shape.

### 4.4 actions/cache
- Caches `installer/cache/` and `~/.cache/huggingface`. Key includes `build_release.py` + `requirements.txt` + `requirements.lock` hashes — precise invalidation. Expected ~70 % CI time reduction after the first cache-warming build.

### 4.5 `ci.yml`
- Runs on every push + PR across Ubuntu, Windows, macOS-14.
- Installs from `requirements.lock` + pytest; runs Phase 1 / 2 / 3 / 4 test groups plus a regression gate.
- Secondary job greps the git history for credential-shaped strings (`ldpat_`, `sk-ant-…`, `gh[pousr]_…{36}`) — blocks PRs that leak.

### 4.6 SBOM + checksums + integrity check

**Build-time (in `build_release.py`):**
- `write_sbom(python_exe, staging)` runs `pip list --format=json` in the bundled Python and writes `app/sbom.json` with a small schema envelope.
- `write_checksums(staging)` walks `staging/app/**/*.py`, skipping `__pycache__` and `data/`, and writes `app/checksums.json` with `{file_path: sha256}`.
- Both are called before `verify_manifest()` in both Windows and macOS builds.

**Runtime (new `src/startup/checks/integrity.py`):**
- Added as Check 1 in `DEFAULT_CHECKS`. 10 checks now on the splash.
- Non-critical by default: absent `checksums.json` (dev checkouts) → pass; 1–5 mismatches → warn; > 5 → fail critical.
- Samples 25 random entries per launch so hashing stays under 100 ms on a typical laptop.

### 4.7 Uninstaller
- `installer/uninstall.py` (250 lines, testable) + per-platform launcher.
- **User data preserved by default** — `app/data/` moved to `.AlmaInsights_data_backup` alongside the install dir. `--purge-data` for a clean wipe.
- Keyring cleanup is opt-in (confirmed prompt, or `--yes`). Only the 6 known Alma-scoped secret keys are touched — never the caller's broader keyring.
- `--dry-run` reports every action without changing anything — the CLI test suite exercises this.
- `copy_installer_files()` in `build_release.py` was updated to ship all three new uninstaller files in the bundle.

### 4.8 Tests

| File | Tests | Scope |
|------|-------|-------|
| `test_phase4_build.py` | 7 | Exact pinning enforcement, Gemini CLI pin, SBOM schema, checksums skip rules |
| `test_phase4_integrity.py` | 9 | Entry-point branches (missing/empty/match/warn/fail), sampler, verifier helper |
| `test_uninstaller.py` | 11 | Default dir picker, data preservation, tree removal, keyring clear, CLI surface |

**27 new tests. All green. Old Phase-2 `test_default_checks_has_nine_entries` updated to expect 10.**

## Gate verified

```
python -m pytest tests/test_*.py  (full suite + regressions)
# → 372 passed in 250.19s
```

## What's explicitly deferred

- **Windows code signing**: deferred per locked decision. SmartScreen warnings remain.
- **CI `--require-hashes` enforcement**: `requirements.lock` has direct pins but no hashes yet. Regenerate on M1 with `pip-compile --generate-hashes`, then flip CI. Mechanical task with no code changes.
- **Actual pip-freeze lockfile regen on M1**: needs a physical run on the production target.
- **SBOM auto-scan**: Phase 5 will wire the SBOM through a CVE scanner (or not — HIPAA constraint still says local-only).
- **EULA + first-run consent**, **rollback auto-detection**: Phase 5.

## Ready for Phase 5

Phase 5 (final hardening) now has:
- A runtime integrity check to piggyback rollback-trigger logic on (`crash_count > 3` within 60 s of applying an update → revert).
- A crash handler from Phase 1 that already writes to `data/crash_reports/` — Phase 5 wires the `Export crash reports` button in Settings → Support.
- An uninstaller that can reset the app to a clean slate for the EULA first-run dialog tests.

Phase 5 itself is small: rollback detection, EULA dialog, offline-mode regression tests, a secrets-hygiene audit script. Target: one session.
