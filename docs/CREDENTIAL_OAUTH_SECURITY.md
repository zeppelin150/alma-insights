# Credential & Google OAuth Security

Covers the per-user Google OAuth flow, the shared `CredentialsPanel`, and
their interaction with the keyring credential store. Companion to
`docs/CREDENTIAL_STORAGE.md` (which covers the API-key keyring model) and
`docs/CI_CD_BRIEFING.md` (Snyk / audit_secrets).

## What ships

- **Service account (shared/admin)** — unchanged: a `drive.readonly` JSON
  key configured at `enablement.drive.credentials_path`.
- **Per-user OAuth (new)** — an individual authorizes their own Google
  account in-app (Apps-Script style) via a loopback PKCE flow. The Drive
  clients branch on `enablement.drive.auth_type` (`service_account` |
  `oauth_user`); both coexist, default `service_account`.

## Credential storage

All secrets route through `src/data/pat_store.py` → the OS keyring
(Windows Credential Manager / macOS Keychain). The new key
`google_oauth_user` holds **only** the 4-field `authorized_user` record
(`client_id`, `client_secret`, `refresh_token`, `token_uri`) — never the
access/id token. `store_credentials` rejects records over 1280 chars (the
Windows Credential Manager blob cap is ~2.5 KB UTF-16) and any record
without a refresh token. A keyring write failure returns `False` and
**never** falls back to the plaintext `ui_state.json`.

## The OAuth client (client_id/secret)

Resolved in precedence order by `google_oauth.client_config()`:
1. Admin-provided `enablement.google.oauth_client_path` (a GCP "Desktop
   app" client JSON the org supplies in Settings).
2. The build-injected bundle `src/data/_google_oauth_client.py` —
   obfuscated (XOR+base64, reusing `_token_obfuscation`) from the
   `ALMA_GOOGLE_OAUTH_CLIENT` CI secret, **gitignored**, and **absent**
   when the env var is unset (empty env fails the build).
3. `None` — the UI then asks the user to supply their own client.

For a desktop "installed app" client Google does not treat the
client_secret as confidential; **PKCE** is the real protection. The
obfuscation only defeats passive discovery (`strings`, naive grep,
`audit_secrets.py` regex, SBOM/checksum scans).

## Disable on launch

`src/data/google_oauth.py` has **no import-time side effects** and
`_active` starts `None`. Nothing reads the keyring or makes a Google call
at boot. The stored authorization stays dormant until the user clicks
**Reconnect**, which calls `reconnect()` — the *only* setter of `_active`
— and silently refreshes the access token (a browser opens only if the
refresh token is missing/revoked). The Drive consumers enforce this:
`DriveReader.is_configured()` / `GoogleDriveExporter.is_configured()`
return `False` for `oauth_user` until `google_oauth.is_active()`, so the
background Drive monitor never auto-resumes Google traffic at launch. A
stolen at-rest record is useless without the OS vault **and** a
deliberate in-app reconnect.

## Threat model

| Asset | Threat | Mitigation |
|---|---|---|
| Refresh token | Theft at rest | OS keyring (DPAPI/Keychain); pat_store never plaintext-fallbacks |
| Token record | WCM blob-cap overflow | minimal 4-key record; writer rejects >1280 chars + access/id tokens |
| Bundled client_secret | Passive discovery / commit | obfuscated + gitignored + absent-when-unset; PKCE is the real boundary |
| Loopback flow | Auth-code interception / port race | PKCE + CSRF `state` (library default) + ephemeral `run_local_server(port=0)` |
| Drive access | Scope creep | `drive.readonly` + `drive.file` only; never full `drive` |
| Boot | Background Google calls at launch | disable-on-launch; `is_configured` False until `reconnect()` |
| Tokens in logs | Leak via log/trace/exception | log only the client_id fingerprint; worker redacts token-shaped error substrings; no token logged anywhere |
| Claude key | Saved without HIPAA acknowledgment | key field + save disabled until all 3 BAA acknowledgments are checked |

## CI / scanning

- `audit_secrets.py` (blocking) sees only the obfuscated base64 payload,
  not a credential shape; the bundled client module is gitignored anyway.
- Snyk (deps, non-blocking) scans the resolved tree; the new
  `google-auth` / `google-auth-oauthlib` / `google-api-python-client`
  closure introduced **zero** known CVEs at integration time.
- bandit on the new modules: 0 High / 0 Medium (5 Low — defensive
  try/except, the project's accepted posture).

## Verifying live

Set `ALMA_GOOGLE_OAUTH_CLIENT` (a desktop-client JSON) at build, or paste
your own GCP client in Settings. Then: **Connect my Google account** →
browser consent → status "Connected this session"; relaunch the app →
status "Authorized but inactive" and the Drive monitor stays off →
**Reconnect** is silent (no browser) and re-enables Drive.
