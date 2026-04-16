# Auto-Update

**Modules:**
- [src/updater/update_checker.py](../src/updater/update_checker.py) — GitHub release poller
- [src/updater/updater.py](../src/updater/updater.py) — stage-and-apply with SHA-256 enforcement
- [src/updater/github_app_auth.py](../src/updater/github_app_auth.py) — optional GitHub App token minter
- [src/updater/release_manifest.py](../src/updater/release_manifest.py) — per-release SHA manifest
- [src/updater/schema_migrator.py](../src/updater/schema_migrator.py) — DB migrations

**CI:**
- [.github/workflows/build.yml](../.github/workflows/build.yml) — per-PR bundles (Phase 4 hardens this)
- [.github/workflows/release.yml](../.github/workflows/release.yml) — tag-triggered release
- [installer/ci/sign_macos.sh](../installer/ci/sign_macos.sh) — codesign + notarytool wrapper

**Scripts:**
- [scripts/make_release_manifest.py](../scripts/make_release_manifest.py) — post-build manifest builder
- [scripts/provision_update_token.py](../scripts/provision_update_token.py) — install-time keyring seeder

## Status today

GitHub isn't configured yet. The default `updates.auth_mode` is **disabled**, so:
- The startup splash's update check passes instantly with "Update checks disabled in settings".
- No network calls are made.
- The existing Settings → Updates UI still works once a user manually enters credentials.

Everything below describes how to activate it when the repo is ready.

## Activating auto-update

1. **Create the private GitHub repo** for release hosting. Decide between two auth modes — detail in the next section.

2. **Add the `updates` section to `data/settings.yaml`:**
   ```yaml
   updates:
     auth_mode: pat              # or github_app
     github_repo: alma-health/alma-insights
   ```

3. **Provision the update token** on every installed machine (run once per user after first launch):
   ```bash
   python scripts/provision_update_token.py --from-env ALMA_UPDATE_TOKEN
   ```
   The installer can call this during its post-extract step — see [installer/README.md](../installer/README.md).

4. **Ship a release.** Tag the commit as `v9.3.0` and push. `.github/workflows/release.yml` builds 3 bundles (Win x64, macOS arm64, macOS x64), generates the manifest, and publishes to GitHub Releases.

5. **Next-launch splash** on every installed machine calls `check_for_update()`, which:
   - Pulls the token from keyring
   - Fetches `https://api.github.com/repos/<owner>/<repo>/releases/latest`
   - Compares `tag_name` against the running version
   - Surfaces a **warn** row ("Update available: v9.3.0 (current v9.2.0)") or passes silently

## Auth modes

| Mode | How it works | Setup effort |
|------|-------------|--------------|
| `disabled` | No HTTP calls. Default. | None |
| `pat` (recommended) | Fine-grained PAT in the OS keyring. Read-only `Contents` scope on the update repo. | Generate PAT in GitHub → `scripts/provision_update_token.py` |
| `github_app` | JWT-minted installation token (8 h TTL). App ID + private key stored in `data/settings.yaml` (or private key file path). | Register a GitHub App, install on the repo, record IDs |

Both modes produce the same Bearer header — nothing downstream of `UpdateChecker` knows which was used. Switching later is a one-line settings change.

### GitHub App config shape

```yaml
updates:
  auth_mode: github_app
  github_repo: alma-health/alma-insights
  app_id: "123456"
  installation_id: "7890123"
  private_key_path: "~/.alma-insights/alma-updater.pem"
  # OR, inline (less safe, but occasionally convenient):
  # private_key_pem: |
  #   -----BEGIN PRIVATE KEY-----
  #   ...
```

## Integrity verification

Every release publishes `release_manifest.json` alongside the three bundle zips:

```json
{
  "schema_version": 1,
  "version": "v9.3.0",
  "released_at": "2026-04-15T20:45:00+00:00",
  "artifacts": {
    "AlmaInsights-win64.zip":      { "sha256": "...", "size": 1234567 },
    "AlmaInsights-macOS-arm64.zip":{ "sha256": "...", "size": 1234567 },
    "AlmaInsights-macOS-x64.zip":  { "sha256": "...", "size": 1234567 }
  },
  "notes_url": "https://github.com/.../releases/tag/v9.3.0"
}
```

The updater now **refuses to stage any download without a matching SHA-256**. Call sites that want to bypass verification must pass `require_checksum=False` explicitly — a signal to reviewers that the code path is dev-only.

## macOS signing + notarization

`installer/ci/sign_macos.sh` is invoked by `release.yml` on the macOS matrix legs. It's a no-op if any of the four Apple secrets (`APPLE_ID`, `APPLE_TEAM_ID`, `APPLE_APP_PASSWORD`, `APPLE_SIGNING_IDENTITY`) are missing — so CI runs from forks or pre-signing-cert builds don't fail.

Once the Apple Developer ID is purchased and the secrets are added to the repo's Actions settings, signing + notarization runs automatically.

## Rollback

`Updater._rollback()` restores from `_src_backup/`, `_config_backup/`, and `_migrations_backup/` if apply fails mid-flight. Phase 5 adds:
- Crash-triggered auto-rollback (3 crashes within 60 s after applying a new version)
- A "Rollback to previous version" button in Settings → Updates

## When `disabled` is the right answer

If the user is running a dev checkout (no `git` branch gating, code is changing constantly) they probably don't want a release poll interrupting work. Keep `auth_mode: disabled` in the dev `settings.yaml`. The Phase-3 build leaves `disabled` as the default precisely because nothing breaks when GitHub isn't there yet.
