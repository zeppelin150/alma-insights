"""
Unit tests for src/data/pat_store.py (keyring-backed version).

Uses an in-memory keyring backend so tests never touch the real OS vault.
Covers:
  - Public API round-trips for secret keys (→ keyring)
  - Public API round-trips for non-secret keys (→ JSON file)
  - Lightdash PAT convenience wrappers
  - One-time migration from the legacy credentials.json file
  - Keyring unavailability handling
  - Diagnostic probe

Run: python -m pytest tests/test_pat_store_keyring.py -x -v
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import keyring
import keyring.backend
import keyring.errors
import pytest


# ──────────────────────────────────────────────────────────────────
# In-memory keyring backend (test double)
# ──────────────────────────────────────────────────────────────────

class _InMemoryKeyring(keyring.backend.KeyringBackend):
    """Keyring backend that stores secrets in a class-level dict."""

    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self._store:
            raise keyring.errors.PasswordDeleteError("not found")
        del self._store[(service, username)]


class _BrokenKeyring(keyring.backend.KeyringBackend):
    """Keyring backend that always raises — simulates a disabled vault."""

    priority = 1  # type: ignore[assignment]

    def set_password(self, service, username, password):  # noqa: D401
        raise keyring.errors.KeyringError("vault unavailable")

    def get_password(self, service, username):
        raise keyring.errors.KeyringError("vault unavailable")

    def delete_password(self, service, username):
        raise keyring.errors.KeyringError("vault unavailable")


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect ~/.alma-insights to a fresh tmp directory per test."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    # Force Path.home() to re-evaluate
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    # Re-import the module so the module-level paths pick up the new home
    import importlib
    from src.data import pat_store
    importlib.reload(pat_store)
    yield tmp_path
    # Cleanup: keyring state is per-test-function via the in_memory fixture


@pytest.fixture
def in_memory_keyring():
    """Swap keyring to an in-memory backend for the duration of the test."""
    backend = _InMemoryKeyring()
    original = keyring.get_keyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(original)


@pytest.fixture
def broken_keyring():
    """Swap keyring to a backend that always raises."""
    original = keyring.get_keyring()
    keyring.set_keyring(_BrokenKeyring())
    yield
    keyring.set_keyring(original)


# ──────────────────────────────────────────────────────────────────
# Secret-key path (→ keyring)
# ──────────────────────────────────────────────────────────────────

class TestSecretRouting:
    def test_save_then_load_secret(self, fake_home, in_memory_keyring):
        from src.data import pat_store
        assert pat_store.save_setting("lightdash_pat", "ldpat_abc1234567") is True
        assert pat_store.load_setting("lightdash_pat") == "ldpat_abc1234567"

    def test_secret_does_not_land_in_json(self, fake_home, in_memory_keyring):
        from src.data import pat_store
        pat_store.save_setting("anthropic_api_key", "sk-ant-secret")
        # The UI state file must not exist yet (no non-secrets written)
        # OR if it exists, it must not contain the secret value
        if pat_store._STATE_FILE.exists():
            raw = pat_store._STATE_FILE.read_text()
            assert "sk-ant-secret" not in raw

    def test_missing_secret_returns_default(self, fake_home, in_memory_keyring):
        from src.data import pat_store
        assert pat_store.load_setting("gemini_api_key", default="fallback") == "fallback"
        assert pat_store.load_setting("gemini_api_key") is None

    def test_empty_secret_deletes(self, fake_home, in_memory_keyring):
        from src.data import pat_store
        pat_store.save_setting("lightdash_pat", "ldpat_abc")
        assert pat_store.load_pat() == "ldpat_abc"
        pat_store.save_setting("lightdash_pat", "")
        assert pat_store.load_pat() == ""

    def test_each_secret_key_routes_to_keyring(self, fake_home, in_memory_keyring):
        from src.data import pat_store
        for key in pat_store._SECRET_KEYS:
            pat_store.save_setting(key, f"value_for_{key}")
            assert pat_store.load_setting(key) == f"value_for_{key}"
            # Confirm it's actually in the keyring, not the JSON
            assert in_memory_keyring.get_password(pat_store._SERVICE, key) == f"value_for_{key}"


# ──────────────────────────────────────────────────────────────────
# Non-secret path (→ JSON file)
# ──────────────────────────────────────────────────────────────────

class TestNonSecretRouting:
    def test_save_then_load_string(self, fake_home, in_memory_keyring):
        from src.data import pat_store
        assert pat_store.save_setting("zendesk_subject", "Support Ticket") is True
        assert pat_store.load_setting("zendesk_subject") == "Support Ticket"

    def test_save_then_load_dict(self, fake_home, in_memory_keyring):
        from src.data import pat_store
        payload = {"field_id": 12345, "name": "priority"}
        pat_store.save_setting("zendesk_custom_field", payload)
        assert pat_store.load_setting("zendesk_custom_field") == payload

    def test_non_secret_does_not_land_in_keyring(self, fake_home, in_memory_keyring):
        from src.data import pat_store
        pat_store.save_setting("last_cursor", "abc123")
        assert in_memory_keyring.get_password(pat_store._SERVICE, "last_cursor") is None

    def test_default_returned_when_missing(self, fake_home, in_memory_keyring):
        from src.data import pat_store
        assert pat_store.load_setting("nonexistent", default="d") == "d"


# ──────────────────────────────────────────────────────────────────
# Lightdash PAT convenience wrappers
# ──────────────────────────────────────────────────────────────────

class TestLightdashConvenience:
    def test_save_and_load_pat(self, fake_home, in_memory_keyring):
        from src.data import pat_store
        assert pat_store.save_pat("ldpat_xyz0000ABCD") is True
        assert pat_store.load_pat() == "ldpat_xyz0000ABCD"
        assert pat_store.has_pat() is True

    def test_delete_pat(self, fake_home, in_memory_keyring):
        from src.data import pat_store
        pat_store.save_pat("ldpat_toBeDeleted")
        assert pat_store.delete_pat() is True
        assert pat_store.has_pat() is False
        assert pat_store.load_pat() == ""

    def test_delete_pat_when_absent_is_idempotent(self, fake_home, in_memory_keyring):
        from src.data import pat_store
        assert pat_store.delete_pat() is True  # no PAT stored yet

    def test_redact_pat_forms(self, fake_home, in_memory_keyring):
        from src.data import pat_store
        assert pat_store.redact_pat("") == "(none)"
        assert pat_store.redact_pat("ab") == "***ab"
        assert pat_store.redact_pat("ldpat_abcdefgh1234").startswith("ldpat_")
        assert pat_store.redact_pat("ldpat_abcdefgh1234").endswith("1234")
        assert pat_store.redact_pat("raw_token_value")[:3] == "***"


# ──────────────────────────────────────────────────────────────────
# Migration from legacy credentials.json
# ──────────────────────────────────────────────────────────────────

class TestMigration:
    def _write_legacy(self, home: Path, data: dict) -> Path:
        d = home / ".alma-insights"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "credentials.json"
        f.write_text(json.dumps(data))
        return f

    def test_migrate_moves_secrets_to_keyring(self, fake_home, in_memory_keyring):
        from src.data import pat_store
        self._write_legacy(fake_home, {
            "lightdash_pat": "ldpat_legacy",
            "anthropic_api_key": "sk-legacy",
            "zendesk_subject": "old subject",
        })
        moved = pat_store.migrate_legacy_credentials()
        assert moved == 2
        assert pat_store.load_setting("lightdash_pat") == "ldpat_legacy"
        assert pat_store.load_setting("anthropic_api_key") == "sk-legacy"

    def test_migrate_preserves_non_secrets_in_ui_state(self, fake_home, in_memory_keyring):
        from src.data import pat_store
        self._write_legacy(fake_home, {
            "lightdash_pat": "ldpat_xyz",
            "zendesk_subject": "old subject",
            "column_widths": {"a": 100, "b": 200},
        })
        pat_store.migrate_legacy_credentials()
        assert pat_store.load_setting("zendesk_subject") == "old subject"
        assert pat_store.load_setting("column_widths") == {"a": 100, "b": 200}

    def test_migrate_renames_legacy_file(self, fake_home, in_memory_keyring):
        from src.data import pat_store
        legacy = self._write_legacy(fake_home, {"lightdash_pat": "ldpat"})
        pat_store.migrate_legacy_credentials()
        assert not legacy.exists()
        assert legacy.with_suffix(".json.migrated").exists()

    def test_migrate_idempotent(self, fake_home, in_memory_keyring):
        from src.data import pat_store
        self._write_legacy(fake_home, {"lightdash_pat": "ldpat"})
        assert pat_store.migrate_legacy_credentials() == 1
        assert pat_store.migrate_legacy_credentials() == 0  # file already renamed

    def test_migrate_no_legacy_file_returns_zero(self, fake_home, in_memory_keyring):
        from src.data import pat_store
        assert pat_store.migrate_legacy_credentials() == 0

    def test_migrate_ignores_empty_secret_values(self, fake_home, in_memory_keyring):
        from src.data import pat_store
        self._write_legacy(fake_home, {"lightdash_pat": "", "anthropic_api_key": None})
        assert pat_store.migrate_legacy_credentials() == 0


# ──────────────────────────────────────────────────────────────────
# Keyring unavailability
# ──────────────────────────────────────────────────────────────────

class TestKeyringUnavailable:
    def test_save_secret_returns_false_when_vault_broken(self, fake_home, broken_keyring):
        from src.data import pat_store
        assert pat_store.save_setting("lightdash_pat", "x") is False

    def test_load_secret_returns_default_when_vault_broken(self, fake_home, broken_keyring):
        from src.data import pat_store
        assert pat_store.load_setting("lightdash_pat", "fallback") == "fallback"

    def test_non_secret_still_works_when_vault_broken(self, fake_home, broken_keyring):
        from src.data import pat_store
        assert pat_store.save_setting("ui_theme", "dark") is True
        assert pat_store.load_setting("ui_theme") == "dark"

    def test_probe_reports_failure(self, fake_home, broken_keyring):
        from src.data import pat_store
        ok, backend = pat_store.keyring_available()
        assert ok is False
        assert "KeyringError" in backend or "unavailable" in backend.lower()


# ──────────────────────────────────────────────────────────────────
# Diagnostic probe
# ──────────────────────────────────────────────────────────────────

class TestProbe:
    def test_probe_succeeds_and_cleans_up(self, fake_home, in_memory_keyring):
        from src.data import pat_store
        ok, backend_name = pat_store.keyring_available()
        assert ok is True
        assert "InMemory" in backend_name
        # Sentinel must have been deleted
        assert in_memory_keyring.get_password(pat_store._SERVICE, "_alma_insights_probe") is None
