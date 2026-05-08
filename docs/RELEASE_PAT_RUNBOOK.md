# Release PAT runbook

How to mint, bundle, rotate, and revoke the GitHub Personal Access Token that
Alma Insights ships with so end users get auto-updates without ever pasting a
token themselves.

This runbook covers both the **short-term setup** (PAT generated from a personal
GitHub account during UAT) and the **long-term setup** (PAT or GitHub App
installed on a service account once IT provisioning lands). The day-of
procedure is identical; only the GitHub account changes.

> **Threat model reminder.** Anyone with the installer can extract the bundled
> PAT in minutes. The token's actual security boundary is its GitHub
> permissions: *Contents: Read-only* on a single repo. Treat the token as if
> it were public — meaningful only because of its scope, not its secrecy.

---

## 1 · Minting a PAT

1. Sign in as the account that should own this token. **For UAT this is
   your personal Alma GitHub account; once a bot account exists, switch to
   that.**
2. **Settings → Developer settings → Personal access tokens → Fine-grained
   tokens → Generate new token.**
3. Fill in:
   * **Token name:** `alma-insights-release-reader-{YYYY-MM}` (e.g.
     `alma-insights-release-reader-2026-05`). The date suffix is what
     reminds future-you when you minted it.
   * **Resource owner:** `alma-health` (the org).
   * **Expires:** **30 days** for UAT, **1 year** maximum once a bot
     account is in place. Classic PATs ("never expires") are not allowed.
   * **Repository access:** *Only select repositories* → tick
     `alma-health/alma-insights` (and only that repo).
   * **Permissions → Repository permissions → Contents:** *Read-only*.
     Leave every other permission at *No access*.
4. **Generate token.** Copy the value (`github_pat_…`) — you will not see
   it again.

> **Don't paste the token into chat, screenshots, ticket bodies, or
> commit messages.** It only ever lands in two places: the CI secret
> manager and the local clipboard while you're transferring it.

## 2 · Bundling the PAT into a build

The build script reads `ALMA_RELEASE_BOT_PAT` from the environment and
obfuscates it before writing
`src/updater/_release_credentials.py`. The runtime loader at
`src/updater/_bundled_token.py` unwraps it transparently.

**Local build (UAT):**

```powershell
$env:ALMA_RELEASE_BOT_PAT = "github_pat_<your_token>"
python installer/build_release.py --platform windows
```

**CI build:**

1. Repo → *Settings → Secrets and variables → Actions* → New
   repository secret.
2. Name: `ALMA_RELEASE_BOT_PAT`. Value: the token from step 1.
3. The build workflow already references this secret — no YAML edits
   needed once the secret name is in place.

If you start a build without the secret, the bundle will still build
but with no bundled token; end-user installs will fall back to the
keyring path (which means they won't auto-update unless an admin
pastes a PAT in Settings → Updates → Advanced).

If the secret is set but **empty**, the build refuses to proceed —
this protects against the silent "looks fine, doesn't work" failure
mode where a CI variable was created without a value.

## 3 · Verifying the bundle

After a build, `_release_credentials.py` should exist inside the
staged `app/src/updater/` directory and contain four constants:

```python
SCHEMA = 1
BUILD_VERSION = "1.0.1"
TOKEN_KEY = "..."         # ~44-char base64
TOKEN_PAYLOAD = "..."     # base64 of XOR'd token
TOKEN_FINGERPRINT = "gith••••XYZ9"
```

To confirm the runtime loader can decode it:

```powershell
python -c "from src.updater._bundled_token import bundled_token_fingerprint; print(bundled_token_fingerprint())"
```

Should print `gith••••XYZ9` (matching the build-time fingerprint), not
`<no bundled token>`.

When an associate launches the bundle, the first update check logs
`Update auth: using bundled token (gith••••XYZ9)` at INFO. That line is
the support diagnostic — when "updates aren't working" surfaces, ask
for the log and grep for it.

## 4 · Rotating the PAT (every 30 days during UAT, every year afterward)

1. Mint a fresh PAT following step 1. **Don't revoke the old one yet.**
2. Update the CI secret (`ALMA_RELEASE_BOT_PAT`) to the new value.
3. Bump the version in `src/__init__.py`, push a release tag (`vX.Y.Z`).
   CI builds + publishes; every existing installation picks up the new
   PAT through the auto-update path on next check.
4. **Wait 7 days.** Monitor the release feed analytics or sample logs
   from a few users to confirm the new release reached them — `Update
   auth: using bundled token (<new fingerprint>)` appearing in their
   logs is the all-clear.
5. **Now** revoke the old PAT in GitHub Settings → Developer settings
   → Personal access tokens → click the old token → Revoke.

The 7-day overlap protects you from the case where one user hasn't
launched the app yet and is still on the old PAT — revoking
prematurely would leave them stranded.

## 5 · Emergency revoke (token leaked)

If you suspect the PAT has leaked:

1. **Revoke immediately** in GitHub Settings (above). Existing
   installations stop polling for updates the moment GitHub
   invalidates the token — they don't crash, but `auth_mode=pat` will
   start surfacing "GitHub auth failed" warnings on the splash.
2. Mint a replacement PAT.
3. Cut and publish a new release ASAP. Users on the old PAT will see
   warnings until they get the new bundle.
4. If users can't auto-update because their old PAT is dead and the
   new release hasn't propagated yet, send them the new bundle out of
   band (Slack / email link to the GitHub release page) — they
   download it manually, install it once, and the bundled-token flow
   resumes.

> The leak is bounded: the token's only privilege is reading release
> binaries from one repo. It cannot push code, modify settings,
> manage members, or access any other repository.

## 6 · Migrating to a service account

When IT provisions `alma-insights-releases-bot` (or your equivalent):

1. Add the bot user as a *Triage* collaborator on
   `alma-health/alma-insights` (read-only is enough; Triage works).
2. Sign in as the bot, mint a PAT following step 1, this time with a
   1-year expiration.
3. Update the CI secret. Cut a release. Wait 7 days. Revoke the old
   personal-account PAT.
4. Update this runbook to reflect that the bot account, not your
   personal account, is the source of truth for the release PAT.

That's the only difference between "UAT bridge" and "production"
auth. No code changes are required — the build script reads the same
env var either way.

## 7 · What end users see

Nothing. The installer ships, they double-click it, and on first
launch the splash check #4 reads `auth_mode=pat` (the new default),
finds the bundled token, polls
`https://api.github.com/repos/alma-health/alma-insights/releases/latest`,
and either reports "Up to date" or shows an "Install & Restart" button
right on the splash. Click → wait ~30s → app relaunches on the new
version. No PAT, no copy/paste, no GitHub navigation.

## 8 · Override path (for IT admins)

An IT admin who wants to use a different token (e.g. theirs, scoped
differently) can paste it in *Settings → Updates → Advanced* (it's
stored in the OS keyring under `github_update_token`). The keyring
token wins over the bundled one. To revert to the bundled token, the
admin clears the keyring entry and the bundled fallback resumes
automatically.

This override exists for unusual setups (corporate networks where
read-only PATs aren't acceptable, or org-wide pinning to a single
token) and is **not** a pathway most users should ever interact with.

---

## Appendix A — Adding a third-party reviewer

If a contractor or external reviewer needs to test pre-release
builds without seeing the bundled PAT in cleartext:

1. Build them a one-off bundle with a separately-minted, time-boxed
   PAT (7-day expiration). Don't reuse the production secret.
2. After they confirm they're done, revoke that PAT.

This avoids the situation where a leaked one-off bundle keeps working
after the engagement is over.

## Appendix B — File reference

| File | Role |
|---|---|
| `src/updater/_token_obfuscation.py` | Pure XOR + base64 helpers. |
| `src/updater/_bundled_token.py` | Runtime loader. Returns the PAT or None. |
| `src/updater/_release_credentials.py` | Build-injected. Gitignored. Contains the obfuscated token. |
| `src/updater/update_checker.py:_load_pat_token` | Three-tier fallback (keyring → legacy → bundled). |
| `installer/build_release.py:_inject_release_credentials` | Reads `ALMA_RELEASE_BOT_PAT`, writes `_release_credentials.py`. |
| `tests/test_bundled_token.py` | Round-trip + fallback tests. |
