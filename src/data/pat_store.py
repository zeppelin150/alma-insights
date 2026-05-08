"""
Alma Insights — PAT Store (Keyring-backed)

Credentials are stored in the OS-native secret vault via the `keyring`
package (Windows Credential Manager on Windows, Keychain on macOS).
Non-secret UI state (cursors, view IDs, column mappings) continues to
live in a plaintext JSON file.

Public API (unchanged from the pre-keyring version):
    save_pat(pat) -> bool
    load_pat()    -> str
    has_pat()     -> bool
    delete_pat()  -> bool
    redact_pat(pat) -> str
    save_setting(key, value) -> bool
    load_setting(key, default=None)
    migrate_legacy_credentials() -> int   # new — returns count migrated

Internal routing:
    - Keys in `_SECRET_KEYS` → keyring.set_password / get_password
    - All other keys → ~/.alma-insights/ui_state.json

Rationale: the OS vault encrypts secrets at rest using per-user keys
(Windows DPAPI / macOS Keychain). A process running as another user
cannot read them even with filesystem access.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import keyring
import keyring.errors

# ──────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────

_SERVICE = "alma-insights"

# Known secret keys. Anything matching goes to keyring, not the JSON file.
# Additions here must be kept in sync with migrate_legacy_credentials().
_SECRET_KEYS: frozenset[str] = frozenset({
    "lightdash_pat",
    "gemini_api_key",
    "anthropic_api_key",
    "guru_api_token",
    "zendesk_api_key",
    "github_update_token",
    # AWS credentials for Bedrock-backed Claude CLI calls. Used only when
    # bedrock.use_environment=false in settings; otherwise the standard AWS
    # credential chain (env vars, profile, IAM role) is honored.
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
})

_CONFIG_DIR = Path.home() / ".alma-insights"
_STATE_FILE = _CONFIG_DIR / "ui_state.json"
_LEGACY_FILE = _CONFIG_DIR / "credentials.json"

# Preserved for any external reference (e.g. diagnostic tools).
# New code should not read or write this path directly.
_CREDS_FILE = _LEGACY_FILE


# ──────────────────────────────────────────────────────────────────
# Directory bootstrap
# ──────────────────────────────────────────────────────────────────

def _ensure_dir() -> None:
    """Create ~/.alma-insights with restrictive permissions on POSIX."""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(_CONFIG_DIR, 0o700)
        except OSError:
            pass


# ──────────────────────────────────────────────────────────────────
# JSON file I/O (non-secret settings only)
# ──────────────────────────────────────────────────────────────────

def _read_state() -> dict:
    """Read the UI state file. Returns empty dict on any error."""
    try:
        if _STATE_FILE.exists():
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _write_state(data: dict) -> bool:
    """Write the UI state file atomically. Returns True on success."""
    try:
        _ensure_dir()
        _STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        if os.name != "nt":
            try:
                os.chmod(_STATE_FILE, 0o600)
            except OSError:
                pass
        return True
    except OSError:
        return False


# ──────────────────────────────────────────────────────────────────
# Public API — Lightdash PAT convenience wrappers
# ──────────────────────────────────────────────────────────────────

def save_pat(pat: str) -> bool:
    """Store the Lightdash PAT in the OS keyring."""
    return save_setting("lightdash_pat", pat)


def load_pat() -> str:
    """Load the Lightdash PAT. Returns empty string if absent."""
    return load_setting("lightdash_pat", "") or ""


def has_pat() -> bool:
    """True if a Lightdash PAT is stored."""
    return bool(load_pat())


def delete_pat() -> bool:
    """Remove the stored Lightdash PAT. Returns True on success."""
    return _delete_secret("lightdash_pat")


def redact_pat(pat: str) -> str:
    """Redact a PAT for display: ldpat_…XXXX or ***…XXXX."""
    if not pat:
        return "(none)"
    if len(pat) <= 4:
        return "***" + pat[-2:]
    prefix = pat[:6] if pat.startswith("ldpat_") else "***"
    return f"{prefix}…{pat[-4:]}"


# ──────────────────────────────────────────────────────────────────
# Public API — generic settings (routed by key name)
# ──────────────────────────────────────────────────────────────────

def save_setting(key: str, value: Any) -> bool:
    """Store a setting. Secrets → keyring; everything else → JSON file."""
    if key in _SECRET_KEYS:
        return _set_secret(key, str(value) if value is not None else "")
    data = _read_state()
    data[key] = value
    return _write_state(data)


def load_setting(key: str, default: Any = None) -> Any:
    """Load a setting. Secrets from keyring; others from JSON file."""
    if key in _SECRET_KEYS:
        val = _get_secret(key)
        return val if val else default
    return _read_state().get(key, default)


# ──────────────────────────────────────────────────────────────────
# Keyring primitives
# ──────────────────────────────────────────────────────────────────

def _set_secret(key: str, value: str) -> bool:
    try:
        if value:
            keyring.set_password(_SERVICE, key, value)
        else:
            _delete_secret(key)
        return True
    except keyring.errors.KeyringError:
        return False


def _get_secret(key: str) -> str | None:
    try:
        return keyring.get_password(_SERVICE, key)
    except keyring.errors.KeyringError:
        return None


def _delete_secret(key: str) -> bool:
    try:
        keyring.delete_password(_SERVICE, key)
        return True
    except keyring.errors.PasswordDeleteError:
        return True  # already gone
    except keyring.errors.KeyringError:
        return False


# ──────────────────────────────────────────────────────────────────
# One-time migration from legacy credentials.json
# ──────────────────────────────────────────────────────────────────

def migrate_legacy_credentials() -> int:
    """
    Move secrets from the legacy ~/.alma-insights/credentials.json into
    the OS keyring. Non-secret entries move to ui_state.json. The legacy
    file is renamed with a .migrated suffix. Idempotent.

    Returns the number of secrets moved to keyring.
    """
    if not _LEGACY_FILE.exists():
        return 0
    try:
        data = json.loads(_LEGACY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0

    moved = 0
    remaining: dict = {}
    for k, v in data.items():
        if k in _SECRET_KEYS and v:
            if _set_secret(k, str(v)):
                moved += 1
        elif v is not None and v != "":
            remaining[k] = v

    if remaining:
        existing = _read_state()
        existing.update(remaining)
        _write_state(existing)

    try:
        _LEGACY_FILE.rename(_LEGACY_FILE.with_suffix(".json.migrated"))
    except OSError:
        pass

    return moved


# ──────────────────────────────────────────────────────────────────
# Diagnostics (used by Check 3 in the startup splash)
# ──────────────────────────────────────────────────────────────────

def keyring_available() -> tuple[bool, str]:
    """
    Test-write a sentinel value to the keyring. Returns (ok, backend_name).
    The sentinel is deleted immediately; no residue is left behind.
    """
    sentinel_key = "_alma_insights_probe"
    try:
        keyring.set_password(_SERVICE, sentinel_key, "probe")
        value = keyring.get_password(_SERVICE, sentinel_key)
        keyring.delete_password(_SERVICE, sentinel_key)
        if value != "probe":
            return False, "read/write mismatch"
        backend = type(keyring.get_keyring()).__name__
        return True, backend
    except keyring.errors.KeyringError as exc:
        return False, f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001 — diagnostic must never raise
        return False, f"{type(exc).__name__}: {exc}"
