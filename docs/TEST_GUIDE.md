# End-to-End Test Guide — CI/CD Phases 1–5

**Audience:** you (the operator), starting from a fresh checkout and going all the way to a signed GitHub release with auto-update.

**Prereqs:** Windows or macOS dev machine · Python 3.10+ · git · optional: an unused GitHub org for the private release repo.

---

## Part A — Local verification (no GitHub needed, ~15 min)

### A1. Run the full test suite

```bash
cd C:\alma-insights
python -m pytest \
    tests/test_pat_store_keyring.py \
    tests/test_hardware_profile.py \
    tests/test_crash_handler.py \
    tests/test_env_guard.py \
    tests/test_phase1_e2e.py \
    tests/test_checker.py \
    tests/test_startup_checks.py \
    tests/test_splash.py \
    tests/test_phase2_e2e.py \
    tests/test_update_checker_phase3.py \
    tests/test_updater_integrity.py \
    tests/test_github_app_auth.py \
    tests/test_release_manifest.py \
    tests/test_updates_check_phase3.py \
    tests/test_provision_update_token.py \
    tests/test_phase3_e2e.py \
    tests/test_phase4_build.py \
    tests/test_phase4_integrity.py \
    tests/test_uninstaller.py \
    tests/test_rollback.py \
    tests/test_eula_dialog.py \
    tests/test_audit_secrets.py \
    tests/test_offline_mode.py \
    tests/test_phase5_e2e.py \
    tests/test_updater.py \
    tests/test_source_monitor.py \
    tests/test_guru_client.py \
    tests/test_settings_manager.py -q
```

**Expected:** `423 passed in ~30-70s`. A green sweep validates every piece of Phase 1–5 in isolation + integration + regression.

### A2. Smoke-test each CLI in 60 seconds

```bash
# Keyring & audit — should all be clean
python scripts/audit_secrets.py --no-history

# Hardware detection (live, on your machine)
python -c "from src.startup import hardware; import json; print(json.dumps(hardware.profile('test'), indent=2))"

# Token provisioner — just shows help
python scripts/provision_update_token.py --help

# Uninstaller — just shows help
python installer/uninstall.py --help
```

**Expected:** each prints cleanly. Hardware profile prints CPU/RAM/GPU info.

### A3. Build release manifest from a synthetic "dist/"

```bash
mkdir -p /tmp/dist-test
dd if=/dev/urandom of=/tmp/dist-test/AlmaInsights-win64.zip bs=1024 count=4
dd if=/dev/urandom of=/tmp/dist-test/AlmaInsights-macOS-arm64.zip bs=1024 count=4
python scripts/make_release_manifest.py --version v9.3.0-test --artifacts /tmp/dist-test
cat /tmp/dist-test/release_manifest.json
rm -rf /tmp/dist-test
```

**Expected:** JSON with `schema_version: 1`, two artifacts, SHA-256 per file.

### A4. Launch the app in headless check mode

```bash
python main.py --no-splash
```

**Expected:** The 10 health checks print to stdout (`[OK] Python environment …`, `[OK] Keyring accessible …`, `[OK] Hardware profiling …`, etc.), then `MainWindow` opens. Close the window normally.

### A5. Launch the app with the full splash

Rename `data/settings.yaml` → `data/settings.yaml.bak`, then:

```bash
python main.py
```

**Expected flow:**
1. **EULA dialog** appears. Scroll the body, tick the consent checkbox (Accept becomes enabled), click **Accept**.
2. **Splash window** appears with the Alma-green branding.
3. Ten rows animate in, most turn green ✓. One or two may be amber ⚠ (usually "Checking for updates" shows "disabled" which is expected).
4. **Continue** button lights up when critical checks pass.
5. Click **Continue** → splash closes, MainWindow opens.

Then restore: `mv data/settings.yaml.bak data/settings.yaml` and re-launch. The EULA should **not** reappear.

### A6. Verify keyring migration

If you have a pre-Phase-1 `~/.alma-insights/credentials.json` with a `lightdash_pat` key, running main.py once will:
- Rename the file to `credentials.json.migrated`
- Move the secret to Windows Credential Manager (search "alma-insights")
- The splash's Check 2 shows "migrated N, N credential(s) stored"

Verify in Windows:
```
Control Panel → User Accounts → Credential Manager → Windows Credentials
```
Look for entries named `alma-insights`.

### A7. Exercise the rollback guard (simulated)

```python
# Python REPL in the project root:
from src.updater import rollback
rollback.record_apply("1.0.0", "2.0.0")

from pathlib import Path
import json
print(json.loads(Path("data/rollback_state.json").read_text()))

# Then simulate 3 crashes
import datetime, os
crash_dir = Path("data/crash_reports")
crash_dir.mkdir(exist_ok=True)
for i in range(3):
    (crash_dir / f"crash_sim_{i}.json").write_text('{"x":1}')

should, reason = rollback.needs_rollback()
print(f"should_rollback={should} reason={reason}")

# Cleanup
Path("data/rollback_state.json").unlink(missing_ok=True)
for f in crash_dir.glob("crash_sim_*.json"):
    f.unlink()
```

**Expected:** `should_rollback=True reason=3 crash(es) within grace window`.

### A8. Run the secrets audit against a deliberately-dirty file

```bash
mkdir -p /tmp/audit-test
echo "token = 'ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'" > /tmp/audit-test/bad.py
python scripts/audit_secrets.py --root /tmp/audit-test --no-history
echo "exit=$?"
rm -rf /tmp/audit-test
```

**Expected:** `1 finding(s)`, exit code **1**.

---

## Part B — GitHub repo setup (when you're ready, ~30 min)

### B1. Create the private repo

1. Go to github.com → **New repository**.
2. Owner: your org (e.g. `alma-health`). Name: `alma-insights`. **Private**.
3. Do **not** initialize with README or .gitignore — we'll push the existing history.
4. Click **Create**.

### B2. Push the existing code

```bash
cd C:\alma-insights
git remote add origin git@github.com:alma-health/alma-insights.git
git push -u origin main
```

### B3. Create the fine-grained PAT for update checks

1. Go to github.com → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. **Token name:** `alma-insights-update-reader`.
3. **Expiration:** 1 year.
4. **Resource owner:** your org (not your personal account).
5. **Repository access:** *Only selected repositories* → pick `alma-insights`.
6. **Permissions:**
    - Repository → *Contents*: **Read-only**
    - Repository → *Metadata*: **Read-only** (automatic)
7. **Generate token** → copy the `github_pat_…` string somewhere temporary.

### B4. Seed the token into every dev machine's keyring

On each machine that will run the app:

```bash
# Paste the token when prompted; it goes straight into the OS keyring
python scripts/provision_update_token.py --token "github_pat_...."
```

Or, for automation:

```bash
export ALMA_UPDATE_TOKEN="github_pat_...."
python scripts/provision_update_token.py --from-env ALMA_UPDATE_TOKEN
```

Verify:
```bash
python -c "from src.data import pat_store; print(pat_store.load_setting('github_update_token')[:14] + '…')"
```

### B5. Turn on the startup update check

Edit `data/settings.yaml` and add:

```yaml
updates:
  auth_mode: pat
  github_repo: alma-health/alma-insights
```

Relaunch `python main.py`. The splash's Check 4 ("Checking for updates") should now hit GitHub. With no release published yet, it prints `"GitHub API error: 404 … Release repo not found"` as a **warn** — not blocking. Once you ship a release (B7), this becomes `"Up to date"`.

### B6. Cut a dry-run release

```bash
git tag v9.3.0-rc1
git push origin v9.3.0-rc1
```

In GitHub: **Actions tab** → watch **Release** run. It will:
- Build `AlmaInsights-win64.zip`, `AlmaInsights-macOS-arm64.zip`, `AlmaInsights-macOS-x64.zip` in parallel
- (Skip macOS signing — Apple secrets not set yet)
- Build `release_manifest.json` from all three artifacts
- Publish a GitHub Release at `v9.3.0-rc1` with auto-generated notes + all four files attached

**Expected:** green workflow run. Navigate to the new release and verify four assets are attached.

### B7. Verify the end-user upgrade flow

On a dev machine currently running `v9.2.0`:

1. Launch `python main.py` → splash Check 4 now reports `"Update available: v9.3.0-rc1 (current v9.2.0)"` as a **warn**.
2. MainWindow opens. Go to **Settings → Updates → Check for Updates**. The status pill should turn amber: `v9.3.0-rc1 available`.
3. Click **Install Now**. The progress bar runs: download → verify SHA-256 → stage.
4. **Checksum verification** is mandatory. If the release zip sha differs from the manifest, staging is refused with a `"Checksum mismatch"` message.
5. Click **Restart Now**.
6. On relaunch, `apply_staged_update()` swaps in the new code. Previous version is preserved at `_src_previous/`.
7. Splash runs again; the 60 s grace window is now active. If the new version crashes 3 times within 60 s, you'll see on next launch: `[startup] Auto-rollback (3 crash(es) within grace window): rolled back to 9.2.0`.

### B8. Activate macOS signing (when the Apple Developer ID arrives)

1. Purchase Apple Developer ID (~$99/yr).
2. Generate an app-specific password at appleid.apple.com.
3. In GitHub: **Settings → Secrets and variables → Actions → New repository secret**. Add four:

| Name | Value |
|------|-------|
| `APPLE_ID` | your developer email |
| `APPLE_TEAM_ID` | 10-char team identifier from developer.apple.com |
| `APPLE_APP_PASSWORD` | app-specific password |
| `APPLE_SIGNING_IDENTITY` | e.g. `Developer ID Application: Alma Health (TEAMID)` |

4. Push a new tag: `git tag v9.3.0 && git push origin v9.3.0`.
5. The macOS matrix legs now run `installer/ci/sign_macos.sh` which `codesign --options runtime`s the bundle and submits to notarytool. Build takes ~5 min longer.
6. End users on macOS no longer see Gatekeeper warnings.

### B9. Activate GitHub App auth (optional, swap from PAT)

Only needed if you want to rotate auth without re-provisioning every machine:

1. Go to github.com → **Settings → Developer settings → GitHub Apps → New GitHub App**.
2. Single-repo, *Contents: Read-only*, no webhooks.
3. Install the app on the alma-insights repo. Note `app_id`, `installation_id`, download the private key `.pem`.
4. Bundle the `.pem` into the installer, or deploy it to `~/.alma-insights/alma-updater.pem`.
5. Edit `data/settings.yaml`:

```yaml
updates:
  auth_mode: github_app
  github_repo: alma-health/alma-insights
  app_id: "123456"
  installation_id: "7890123"
  private_key_path: "~/.alma-insights/alma-updater.pem"
```

6. Remove the PAT from keyring on dev machines: `python scripts/provision_update_token.py --clear`.
7. Relaunch. The splash's Check 4 now mints a 10-minute JWT → exchanges for an 8-hour installation token on every check.

---

## Part C — Fleet operational drills (post-launch)

### C1. Force a rollback (manual)

Inside a fresh install, open the app and go to **Settings → Updates → Rollback to Previous Version**. Only enabled during the 60 s grace window after an apply. Click, confirm, restart. Previous version is back.

### C2. Export crash reports

1. Force a crash (e.g. edit any `src/*.py` to raise on import, relaunch).
2. Observe the crash report file in `data/crash_reports/`.
3. Go to **Settings → Updates → Support & Recovery → Export Crash Reports**.
4. A Save dialog opens. Choose a location. A zip is written containing the last 20 crash reports.
5. Email the zip manually.

### C3. Uninstall

```bash
# Windows
"Uninstall Alma Insights.bat"

# macOS
./Uninstall\ Alma\ Insights.command
```

Watch for two prompts:
1. *"Proceed with uninstall?"* → yes
2. *"Remove stored credentials?"* → yes (or no to keep keyring entries)

User data (databases, reports) is preserved at `.AlmaInsights_data_backup/` alongside the install dir. Add `--purge-data` to delete it too.

### C4. Smoke test: can an M1 end user still function?

Build on macOS-14 runner via `.github/workflows/release.yml`. Download `AlmaInsights-macOS-arm64.zip`. Install on an M1. Confirm:
- Splash opens (check hardware check: accelerator should say "mps")
- `embedding_batch_size: 32`, `embedding_threads: 4`, `acp_max_workers: 6` (approx)
- Running a small scan uses the MPS backend

---

## Troubleshooting during tests

| Symptom | Check |
|---------|-------|
| Tests fail with `ModuleNotFoundError: keyring` | `pip install -r requirements.txt` |
| `gemini --prompt ping` times out | Run `gemini` interactively once to finish OAuth |
| Splash shows "Keyring unavailable" on Windows | `services.msc` → ensure *Credential Manager* service is Running |
| Splash shows "ML model check" warn | Model not pre-downloaded; re-run the installer |
| Update check warns "GitHub auth failed" | Token expired; re-run `provision_update_token.py --force` |
| Update check warns "Release repo not found" | `updates.github_repo` in settings.yaml has wrong owner/name |
| macOS app won't open ("unidentified developer") | Apple secrets not yet wired in GH Actions (B8) |

**Expected end state after all steps:** private GitHub repo active, `v9.3.0` published and signed, every dev machine auto-updates on launch with SHA-256 integrity + auto-rollback-on-crash-loop, HIPAA-aligned EULA enforced, credentials in OS keyring, no secrets in git history.
