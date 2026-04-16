# CI/CD Plan — Phase 3 Complete

**Date:** 2026-04-15
**Parent plan:** [alma-insights-cicd-plan.md](../../Users/Chris/.claude/plans/alma-insights-cicd-plan.md)
**Previous:** [PHASE2_COMPLETE.md](PHASE2_COMPLETE.md)

## Scope

Phase 3 wires the existing updater stack to keyring-backed auth, makes integrity verification mandatory, adds an optional GitHub App auth path, ships the release workflow, and replaces the Phase-2 stub in the splash. **GitHub is not yet configured, so the default `updates.auth_mode` is `disabled` — every new code path is inert at launch until the repo is set up.**

| Sub-phase | Deliverable | Location |
|-----------|-------------|----------|
| 3.1 | Config-driven checker + keyring token | [update_checker.py](../src/updater/update_checker.py) |
| 3.2 | Mandatory SHA-256 in Updater | [updater.py](../src/updater/updater.py) |
| 3.3 | GitHub App auth (PyJWT, optional) | [github_app_auth.py](../src/updater/github_app_auth.py) |
| 3.4 | Real update check in splash | [checks/updates.py](../src/startup/checks/updates.py) |
| 3.5 | Release manifest + builder script | [release_manifest.py](../src/updater/release_manifest.py), [scripts/make_release_manifest.py](../scripts/make_release_manifest.py) |
| 3.6 | Installer token provisioner | [scripts/provision_update_token.py](../scripts/provision_update_token.py) |
| 3.7 | Tag-triggered release workflow | [.github/workflows/release.yml](../.github/workflows/release.yml), [installer/ci/sign_macos.sh](../installer/ci/sign_macos.sh) |
| 3.8 | Test suite | 6 new test files, 53 new tests |
| 3.9 | Docs + regression sweep | [UPDATES.md](UPDATES.md), this file |

**53 new tests. 344 total across Phases 1 + 2 + 3 + regressions. Zero regressions.**

## Files changed

### New (13)

```
src/updater/github_app_auth.py
src/updater/release_manifest.py
src/startup/checks/updates.py          (rewritten from the Phase-2 stub)

scripts/make_release_manifest.py
scripts/provision_update_token.py

.github/workflows/release.yml
installer/ci/sign_macos.sh

tests/test_update_checker_phase3.py
tests/test_updater_integrity.py
tests/test_github_app_auth.py
tests/test_release_manifest.py
tests/test_updates_check_phase3.py
tests/test_provision_update_token.py
tests/test_phase3_e2e.py

docs/UPDATES.md
docs/PHASE3_COMPLETE.md                 (this file)
```

### Modified (4)

```
src/updater/update_checker.py       # config-driven, keyring-backed, auth_mode-aware
src/updater/updater.py              # checksum mandatory; require_checksum=False opt-out
tests/test_startup_checks.py        # TestUpdates rewritten for disabled-default
requirements.txt                    # added PyJWT>=2.8.0
installer/build_release.py          # added PyJWT to PACKAGES
```

## What ships now

### 3.1 Keyring-backed UpdateChecker
- `auth_mode` ∈ `{disabled, pat, github_app}`. Default **disabled**.
- Repo owner + name read from `updates.github_repo` in `settings.yaml`.
- Token pulled from keyring under `github_update_token` (already in `_SECRET_KEYS` from Phase 1). Legacy `github_pat` is honoured as a fallback.
- `build_default_update_checker()` factory reads settings + keyring in one call.
- `_resolve_config_and_token()` is the single config source; tested against every branch.

### 3.2 Mandatory SHA-256
- `Updater.stage(url, expected_sha256, version)` refuses synchronously if `expected_sha256` is empty (no socket opened). Test `test_stage_without_checksum_never_opens_socket` confirms this.
- `require_checksum=False` is an explicit dev opt-out — documented in the docstring, exercised in the test suite so reviewers can spot any accidental use at a glance.
- Mismatch produces `"Checksum mismatch… refusing to stage untrusted update."`.

### 3.3 GitHub App auth
- `mint_installation_token(cfg)` signs a 10-minute JWT and exchanges it for an 8-hour installation token.
- Requires PyJWT; returns `None` with a logged warning if PyJWT is unimportable or any config field is missing.
- Private key from `private_key_pem` (inline) or `private_key_path` (file).
- Not activated anywhere by default — `auth_mode: github_app` in `settings.yaml` is what turns it on.

### 3.4 Real splash update check
- `src/startup/checks/updates.py` was a one-line Phase-2 stub; it's now a 110-line synchronous check with a 5 s HTTP timeout.
- Auth flow honours `disabled / pat / github_app`. Missing token in PAT mode warns instead of silently failing.
- HTTP 401 / 404 / network errors each get a user-friendly message.

### 3.5 Release manifest
- `release_manifest.py` — `build_manifest`, `write_manifest`, `load_manifest`, `lookup_artifact`, `sha256_of`.
- `scripts/make_release_manifest.py` is the CI entrypoint: walks `dist/`, computes SHAs, writes `release_manifest.json`.
- Manifest schema versioned (`schema_version: 1`) so future changes are explicit.

### 3.6 Install-time token provisioning
- `scripts/provision_update_token.py` seeds `github_update_token` into keyring.
- Sources: `--token`, `--from-env VAR`, `--from-file PATH`, interactive `getpass`. Mutually exclusive.
- `--clear` removes the stored token. `--force` overwrites an existing value.
- Exit codes suitable for CI `set -e` pipelines: 0 success, 1 validation error, 2 keyring unavailable.

### 3.7 Release workflow
- `.github/workflows/release.yml` — 3-matrix build (Win x64 + macOS arm64 + macOS x64) with `actions/cache` on Python embeddable + HF snapshots.
- macOS signing + notarization run in-matrix via `installer/ci/sign_macos.sh`; the script exits cleanly when Apple secrets aren't set, so the workflow doesn't fail on forks or pre-cert runs.
- Aggregation job downloads every matrix artifact, builds the manifest, and calls `softprops/action-gh-release@v2` with `generate_release_notes: true`.
- Manually triggerable via `workflow_dispatch` for dry runs.

### 3.8 Test coverage

| File | Tests | Scope |
|------|-------|-------|
| `test_update_checker_phase3.py` | 13 | Config resolution, disabled short-circuit, factory, error translation |
| `test_updater_integrity.py` | 5 | Mandatory checksum, dev opt-out, SHA helper |
| `test_github_app_auth.py` | 10 | JWT sign + token exchange + missing deps; uses real RSA keypair via `cryptography` |
| `test_release_manifest.py` | 12 | Build/write/load/lookup, SHA correctness, glob filtering |
| `test_updates_check_phase3.py` | 8 | Splash check disabled/pat branches + HTTP failures |
| `test_provision_update_token.py` | 5 | Subprocess-driven CLI tests |
| `test_phase3_e2e.py` | 5 | End-to-end: disabled, PAT flow, real local HTTP server for checksum enforcement, manifest round-trip |

**53 new tests. All green. No existing tests broken.**

## Gate verified

```
python -m pytest \
    tests/test_pat_store_keyring.py tests/test_hardware_profile.py \
    tests/test_crash_handler.py tests/test_env_guard.py tests/test_phase1_e2e.py \
    tests/test_checker.py tests/test_startup_checks.py tests/test_splash.py \
    tests/test_phase2_e2e.py \
    tests/test_update_checker_phase3.py tests/test_updater_integrity.py \
    tests/test_github_app_auth.py tests/test_release_manifest.py \
    tests/test_updates_check_phase3.py tests/test_provision_update_token.py \
    tests/test_phase3_e2e.py \
    tests/test_updater.py tests/test_source_monitor.py tests/test_guru_client.py \
    tests/test_settings_manager.py
# → 344 passed in 45.37s
```

## What's explicitly deferred

- **GitHub repo**: not created yet. Every new code path defaults to `disabled` so nothing breaks. When the repo is ready, flip `updates.auth_mode: pat` in `settings.yaml` and run `provision_update_token.py`.
- **Rollback auto-detection** (Phase 5): `Updater._rollback()` runs on mid-apply failure, but the 3-crashes-in-60 s detection + "Rollback to previous version" UI button land in Phase 5.
- **Windows code signing**: intentionally out of scope for Phase 3. Deferred until user complaints per the locked decision.
- **Apple Developer ID usage**: signing script is wired in but inert until the four Apple secrets are added to Actions. User has said they'll purchase the cert in Phase 4.

## Ready for Phase 4

Phase 4 is the installer/build hardening pass: `==` version pinning, `requirements.lock`, Gemini CLI fail-fast, SBOM, `checksums.json`, macOS x86_64 matrix leg (already in the release workflow), Apple signing activation, uninstaller. No external dependencies on Phase 3 other than PyJWT already being in `requirements.txt`.

## One-line activation when GitHub is ready

```bash
# Once per org (CI):  add ALMA_UPDATE_TOKEN to repo Actions secrets.
# Once per machine:
python scripts/provision_update_token.py --from-env ALMA_UPDATE_TOKEN
# Edit data/settings.yaml:
#   updates:
#     auth_mode: pat
#     github_repo: alma-health/alma-insights
# That's it. Next launch, splash row 4 shows "Up to date (v9.3.0)".
```
