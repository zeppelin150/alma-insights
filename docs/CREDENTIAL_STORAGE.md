# Credential Storage

**Module:** [src/data/pat_store.py](../src/data/pat_store.py)
**Tests:** [tests/test_pat_store_keyring.py](../tests/test_pat_store_keyring.py)

## Design

Alma Insights keeps two kinds of state in the user's home directory:

| Kind | Example keys | Backend | Encryption |
|------|-------------|---------|------------|
| **Secrets** | `lightdash_pat`, `gemini_api_key`, `anthropic_api_key`, `guru_api_token`, `zendesk_api_key`, `github_update_token` | OS-native secret vault via the `keyring` library | Yes — Windows DPAPI / macOS Keychain |
| **UI state** | cursors, view IDs, column widths, zendesk subject, custom field IDs | `~/.alma-insights/ui_state.json` | No — non-sensitive |

The split is driven by the `_SECRET_KEYS` frozenset in `pat_store.py`. Adding a new credential to that set automatically routes `save_setting(key, value)` / `load_setting(key)` calls to the OS vault with no call-site changes.

## Backends

- **Windows** → Windows Credential Manager (`WinVaultKeyring`). Secrets live in `Control Panel → User Accounts → Credential Manager → Windows Credentials` under the service name `alma-insights`.
- **macOS** → Keychain (`macOSKeyring`). Visible in `Keychain Access.app` under service `alma-insights`.

Both backends encrypt secrets with a key derived from the logged-in user's password. A second OS user on the same machine cannot read them.

## Public API

All call sites already use these 7 functions. Internal routing does the rest.

```python
from src.data import pat_store

# Lightdash PAT convenience wrappers
pat_store.save_pat(pat)            # → keyring
pat_store.load_pat()               # → keyring
pat_store.has_pat()                # → keyring
pat_store.delete_pat()             # → keyring
pat_store.redact_pat(pat)          # display helper — "ldpat_…abcd"

# Generic K/V (routes by key name)
pat_store.save_setting(key, value)
pat_store.load_setting(key, default=None)

# One-time migration (called from startup Check 3)
pat_store.migrate_legacy_credentials()  # -> int (count moved to keyring)

# Diagnostic probe (called from startup Check 3)
ok, backend_name = pat_store.keyring_available()
```

## Migration from the legacy file store

Builds prior to v9.3 wrote everything — secrets and non-secrets — as plaintext JSON to `~/.alma-insights/credentials.json`. On first launch of a keyring-enabled build, `migrate_legacy_credentials()`:

1. Reads `~/.alma-insights/credentials.json`
2. For each key in `_SECRET_KEYS`, moves the value to the OS vault
3. For each other key with a non-empty value, writes it to `~/.alma-insights/ui_state.json`
4. Renames the legacy file to `credentials.json.migrated`

The function is idempotent: re-running it when the legacy file is already renamed is a no-op that returns `0`.

## Failure modes

If the OS vault is unreachable (service disabled, corrupt Keychain, sandboxing, etc.):

- `save_setting` for a secret key returns `False`.
- `load_setting` for a secret key returns the supplied `default`.
- Non-secret settings (JSON file) continue to work unaffected.
- The startup splash's Check 3 calls `keyring_available()` and hard-fails with OS-specific remediation text; the user cannot click Continue until the vault is reachable. No silent file-store fallback.

## Testing

```bash
python -m pytest tests/test_pat_store_keyring.py -x -v
```

Tests use an in-memory `KeyringBackend` subclass so nothing touches the real OS vault. A broken-backend test double covers the failure-mode branches.
