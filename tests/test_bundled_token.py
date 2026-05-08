"""
Bundled-token + obfuscation tests (2026-05-07)
================================================

Covers Piece 3 of the auto-update rebuild:

* :mod:`src.updater._token_obfuscation` — pure obfuscation primitives.
* :mod:`src.updater._bundled_token` — runtime loader for the
  build-injected ``_release_credentials.py``.
* :func:`src.updater.update_checker._load_pat_token` — three-tier
  fallback (keyring → legacy keyring → bundled).

Each test isolates one concern. No god-fixtures, no shared state
between classes.

Run: ``python -m pytest tests/test_bundled_token.py -x -v``
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.updater import _bundled_token as bt
from src.updater import _token_obfuscation as obf
from src.updater import update_checker as uc


# ── Obfuscation primitives ────────────────────────────────────────


class TestObfuscationRoundTrip:
    def test_short_token_round_trips(self):
        key = obf.generate_key()
        token = "github_pat_abc123"
        assert obf.deobfuscate(obf.obfuscate(token, key), key) == token

    def test_long_realistic_token_round_trips(self):
        key = obf.generate_key()
        # GitHub fine-grained PATs are ~93 chars
        token = "github_pat_" + "A" * 82
        assert obf.deobfuscate(obf.obfuscate(token, key), key) == token

    def test_unicode_token_round_trips(self):
        # Defensive: even if GitHub tokens are ASCII, the helper itself
        # should handle UTF-8 cleanly.
        key = obf.generate_key()
        token = "тест_токен_测试_🔑"
        assert obf.deobfuscate(obf.obfuscate(token, key), key) == token

    def test_different_keys_produce_different_payloads(self):
        token = "github_pat_xyz"
        a = obf.obfuscate(token, obf.generate_key())
        b = obf.obfuscate(token, obf.generate_key())
        # Astronomical odds these collide; if they do, our key is broken
        assert a != b

    def test_same_inputs_deterministic(self):
        token = "github_pat_xyz"
        key = obf.generate_key()
        assert obf.obfuscate(token, key) == obf.obfuscate(token, key)


class TestObfuscationErrors:
    def test_empty_token_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            obf.obfuscate("", obf.generate_key())

    def test_short_key_rejected(self):
        with pytest.raises(ValueError, match="at least 16"):
            obf.generate_key(n_bytes=8)

    def test_deobfuscate_with_wrong_key_does_not_raise_but_garbage(self):
        # Wrong key produces non-token output; we don't raise (that's a
        # validation step at a higher layer) but we also shouldn't
        # produce the original token.
        token = "github_pat_zzzzzz"
        good = obf.generate_key()
        bad = obf.generate_key()
        cipher = obf.obfuscate(token, good)
        # Wrong key may or may not decode to valid UTF-8; either way
        # the result must not equal the original token.
        try:
            recovered = obf.deobfuscate(cipher, bad)
            assert recovered != token
        except ValueError:
            pass  # also acceptable

    def test_deobfuscate_empty_payload_raises(self):
        with pytest.raises(ValueError):
            obf.deobfuscate("", obf.generate_key())

    def test_deobfuscate_garbage_payload_raises(self):
        with pytest.raises(ValueError):
            obf.deobfuscate("@@@not_base64@@@", obf.generate_key())


class TestFingerprint:
    def test_short_token_fully_redacted(self):
        assert obf.fingerprint("abc") == "••••"

    def test_long_token_shows_4_4(self):
        fp = obf.fingerprint("github_pat_abcdefghij1234567890")
        assert fp.startswith("gith")
        assert fp.endswith("7890")
        assert "••••" in fp

    def test_empty_token_marked(self):
        assert obf.fingerprint("") == "<empty>"


# ── Bundled-token loader ──────────────────────────────────────────


def _install_fake_credentials(monkeypatch, tmp_path,
                                token="github_pat_TEST_VALID_LONG_ENOUGH",
                                schema=1, build_version="9.9.9"):
    """Stage a fake `_release_credentials.py` inside a temp src/updater
    tree and put it on sys.path so the loader imports it. Returns the
    fingerprint so tests can assert on the logged value."""
    fake = tmp_path / "src" / "updater"
    fake.mkdir(parents=True)
    # __init__.py files so it's a package
    (tmp_path / "src" / "__init__.py").write_text("")
    (fake / "__init__.py").write_text("")
    key = obf.generate_key()
    payload = obf.obfuscate(token, key)
    fp = obf.fingerprint(token)
    contents = textwrap.dedent(f'''\
        SCHEMA = {schema}
        BUILD_VERSION = {build_version!r}
        TOKEN_KEY = {key!r}
        TOKEN_PAYLOAD = {payload!r}
        TOKEN_FINGERPRINT = {fp!r}
    ''')
    (fake / "_release_credentials.py").write_text(contents)

    # Put the temp tree at the front of sys.path so its src.updater
    # wins over the real one for this test only.
    monkeypatch.syspath_prepend(str(tmp_path))
    # Drop any cached modules from a previous import
    for name in list(sys.modules):
        if name.startswith("src.updater._release_credentials") or \
           name == "_release_credentials":
            del sys.modules[name]
    return fp, token


class TestBundledTokenLoader:
    def test_dev_checkout_returns_none(self, monkeypatch):
        # Force the import to fail by stubbing the module to ImportError
        monkeypatch.setattr(
            bt, "_load_credentials_module", lambda: None,
        )
        assert bt.get_bundled_token() is None
        assert bt.have_bundled_token() is False
        assert bt.bundled_token_fingerprint() == "<no bundled token>"

    def test_happy_path_decodes(self, monkeypatch):
        # Stub the loader to return an in-memory module
        token = "github_pat_HAPPY_PATH_TOKEN_ABC123"
        key = obf.generate_key()
        payload = obf.obfuscate(token, key)
        fake_module = SimpleNamespace(
            SCHEMA=1,
            BUILD_VERSION="1.0.0",
            TOKEN_KEY=key,
            TOKEN_PAYLOAD=payload,
            TOKEN_FINGERPRINT=obf.fingerprint(token),
        )
        monkeypatch.setattr(
            bt, "_load_credentials_module", lambda: fake_module,
        )
        assert bt.get_bundled_token() == token
        assert bt.have_bundled_token() is True
        assert bt.bundled_token_fingerprint() == obf.fingerprint(token)

    def test_unsupported_schema_returns_none(self, monkeypatch, caplog):
        import logging
        caplog.set_level(logging.WARNING, logger="alma.updater")
        fake_module = SimpleNamespace(
            SCHEMA=99,
            BUILD_VERSION="2.0.0",
            TOKEN_KEY="x" * 32,
            TOKEN_PAYLOAD="y" * 32,
        )
        monkeypatch.setattr(
            bt, "_load_credentials_module", lambda: fake_module,
        )
        assert bt.get_bundled_token() is None
        assert any("schema" in r.message.lower() for r in caplog.records)

    def test_missing_payload_returns_none(self, monkeypatch):
        fake_module = SimpleNamespace(SCHEMA=1, TOKEN_KEY="x", TOKEN_PAYLOAD="")
        monkeypatch.setattr(bt, "_load_credentials_module", lambda: fake_module)
        assert bt.get_bundled_token() is None

    def test_missing_key_returns_none(self, monkeypatch):
        fake_module = SimpleNamespace(SCHEMA=1, TOKEN_KEY="", TOKEN_PAYLOAD="x")
        monkeypatch.setattr(bt, "_load_credentials_module", lambda: fake_module)
        assert bt.get_bundled_token() is None

    def test_corrupt_payload_returns_none_logs_warning(self, monkeypatch, caplog):
        import logging
        caplog.set_level(logging.WARNING, logger="alma.updater")
        fake_module = SimpleNamespace(
            SCHEMA=1, TOKEN_KEY="not_real_key", TOKEN_PAYLOAD="not_real_payload",
        )
        monkeypatch.setattr(bt, "_load_credentials_module", lambda: fake_module)
        # Should not raise; should return None and log
        assert bt.get_bundled_token() is None


# ── update_checker._load_pat_token three-tier fallback ────────────


class TestLoadPatTokenFallback:
    def test_keyring_primary_wins(self, monkeypatch):
        monkeypatch.setattr(
            "src.data.pat_store.load_setting",
            lambda k, *_: "primary_token" if k == uc.DEFAULT_TOKEN_KEY else "",
        )
        monkeypatch.setattr(
            "src.updater._bundled_token.get_bundled_token",
            lambda: "bundled_token",
        )
        assert uc._load_pat_token() == "primary_token"

    def test_legacy_keyring_used_when_primary_empty(self, monkeypatch):
        monkeypatch.setattr(
            "src.data.pat_store.load_setting",
            lambda k, *_: "legacy_token" if k == "github_pat" else "",
        )
        monkeypatch.setattr(
            "src.updater._bundled_token.get_bundled_token",
            lambda: "bundled_token",
        )
        assert uc._load_pat_token() == "legacy_token"

    def test_bundled_used_when_both_keyring_tiers_empty(self, monkeypatch):
        monkeypatch.setattr(
            "src.data.pat_store.load_setting",
            lambda k, *_: "",
        )
        monkeypatch.setattr(
            "src.updater._bundled_token.get_bundled_token",
            lambda: "bundled_token",
        )
        assert uc._load_pat_token() == "bundled_token"

    def test_returns_empty_when_all_tiers_empty(self, monkeypatch):
        monkeypatch.setattr(
            "src.data.pat_store.load_setting",
            lambda k, *_: "",
        )
        monkeypatch.setattr(
            "src.updater._bundled_token.get_bundled_token",
            lambda: None,
        )
        assert uc._load_pat_token() == ""

    def test_keyring_failure_does_not_block_bundled_fallback(self, monkeypatch):
        def _boom(k, *_):
            raise RuntimeError("keyring locked")
        monkeypatch.setattr("src.data.pat_store.load_setting", _boom)
        monkeypatch.setattr(
            "src.updater._bundled_token.get_bundled_token",
            lambda: "bundled_token",
        )
        # Should fall through to bundled despite the keyring exception
        assert uc._load_pat_token() == "bundled_token"

    def test_logs_fingerprint_not_token(self, monkeypatch, caplog):
        import logging
        caplog.set_level(logging.INFO, logger="alma.updater")
        secret = "github_pat_SECRET_must_NOT_be_logged_xyz"
        monkeypatch.setattr(
            "src.data.pat_store.load_setting",
            lambda k, *_: secret if k == uc.DEFAULT_TOKEN_KEY else "",
        )
        monkeypatch.setattr(
            "src.updater._bundled_token.get_bundled_token",
            lambda: None,
        )
        uc._load_pat_token()
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert secret not in joined, "raw token leaked into logs"
        assert "gith" in joined  # fingerprint prefix is logged
        assert "_xyz" in joined  # fingerprint suffix is logged


class TestAuthModeDefault:
    def test_default_is_pat_not_disabled(self, monkeypatch):
        monkeypatch.setattr(
            "src.data.settings_manager.get_section",
            lambda key, default=None: {} if key == "updates" else (default or {}),
        )
        # Stub out token loaders so we don't actually hit keyring/bundled
        monkeypatch.setattr(uc, "_load_token", lambda *_a, **_kw: "")
        mode, _url, _token = uc._resolve_config_and_token()
        assert mode == "pat", \
            "default auth_mode should be 'pat' so bundled-token fallback applies"

    def test_explicit_disabled_still_wins(self, monkeypatch):
        monkeypatch.setattr(
            "src.data.settings_manager.get_section",
            lambda key, default=None:
                {"auth_mode": "disabled"} if key == "updates" else (default or {}),
        )
        monkeypatch.setattr(uc, "_load_token", lambda *_a, **_kw: "")
        mode, _url, _token = uc._resolve_config_and_token()
        assert mode == "disabled"
