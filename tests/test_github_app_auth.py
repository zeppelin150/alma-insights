"""
Unit tests for src/updater/github_app_auth.py

Covers:
  - mint_installation_token happy path (JWT sign + token exchange)
  - Missing private key — returns None with a warning
  - Missing app_id / installation_id — returns None
  - Token exchange HTTP error — returns None
  - PyJWT missing — returns None (gracefully)
  - Private key loaded from path vs inline PEM

Generates an RSA keypair via `cryptography` so signing actually succeeds.

Run: python -m pytest tests/test_github_app_auth.py -x -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.updater import github_app_auth as gaa


# ──────────────────────────────────────────────────────────────────
# Test keypair
# ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def rsa_keypair():
    """Generate an RSA private key PEM once per test session."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    return pem


# ──────────────────────────────────────────────────────────────────
# mint_installation_token happy path
# ──────────────────────────────────────────────────────────────────

class TestMintHappy:
    def test_mints_token_end_to_end(self, rsa_keypair):
        cfg = {
            "app_id": "12345",
            "installation_id": "67890",
            "private_key_pem": rsa_keypair,
        }

        # Stub the HTTP exchange — return a fake token
        fake_response = MagicMock()
        fake_response.__enter__.return_value.read.return_value = json.dumps({
            "token": "ghs_fake_installation_token_xxx",
            "expires_at": "2026-04-16T00:00:00Z",
        }).encode("utf-8")
        fake_response.__exit__.return_value = False

        with patch("urllib.request.urlopen", return_value=fake_response):
            token = gaa.mint_installation_token(cfg)

        assert token == "ghs_fake_installation_token_xxx"

    def test_reads_private_key_from_path(self, rsa_keypair, tmp_path):
        key_file = tmp_path / "app.pem"
        key_file.write_text(rsa_keypair)
        cfg = {
            "app_id": "12345",
            "installation_id": "67890",
            "private_key_path": str(key_file),
        }
        fake_response = MagicMock()
        fake_response.__enter__.return_value.read.return_value = json.dumps(
            {"token": "via_path"}
        ).encode("utf-8")
        fake_response.__exit__.return_value = False

        with patch("urllib.request.urlopen", return_value=fake_response):
            assert gaa.mint_installation_token(cfg) == "via_path"


# ──────────────────────────────────────────────────────────────────
# Bad config
# ──────────────────────────────────────────────────────────────────

class TestMissingConfig:
    def test_missing_app_id_returns_none(self, rsa_keypair):
        cfg = {"installation_id": "123", "private_key_pem": rsa_keypair}
        assert gaa.mint_installation_token(cfg) is None

    def test_missing_installation_id_returns_none(self, rsa_keypair):
        cfg = {"app_id": "123", "private_key_pem": rsa_keypair}
        assert gaa.mint_installation_token(cfg) is None

    def test_missing_key_returns_none(self):
        cfg = {"app_id": "123", "installation_id": "456"}
        assert gaa.mint_installation_token(cfg) is None

    def test_key_path_does_not_exist_returns_none(self, tmp_path):
        cfg = {
            "app_id": "123", "installation_id": "456",
            "private_key_path": str(tmp_path / "missing.pem"),
        }
        assert gaa.mint_installation_token(cfg) is None


# ──────────────────────────────────────────────────────────────────
# HTTP failures
# ──────────────────────────────────────────────────────────────────

class TestHttpFailures:
    def test_http_error_returns_none(self, rsa_keypair):
        import urllib.error
        cfg = {
            "app_id": "12345", "installation_id": "67890",
            "private_key_pem": rsa_keypair,
        }
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("u", 401, "bad", {}, None),
        ):
            assert gaa.mint_installation_token(cfg) is None

    def test_network_error_returns_none(self, rsa_keypair):
        import urllib.error
        cfg = {
            "app_id": "12345", "installation_id": "67890",
            "private_key_pem": rsa_keypair,
        }
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("no network"),
        ):
            assert gaa.mint_installation_token(cfg) is None

    def test_response_without_token_field(self, rsa_keypair):
        cfg = {
            "app_id": "12345", "installation_id": "67890",
            "private_key_pem": rsa_keypair,
        }
        fake = MagicMock()
        fake.__enter__.return_value.read.return_value = b'{"message":"something"}'
        fake.__exit__.return_value = False
        with patch("urllib.request.urlopen", return_value=fake):
            assert gaa.mint_installation_token(cfg) is None


# ──────────────────────────────────────────────────────────────────
# PyJWT unavailable
# ──────────────────────────────────────────────────────────────────

class TestPyJwtMissing:
    def test_returns_none_when_pyjwt_missing(self, rsa_keypair, monkeypatch):
        # Remove jwt from sys.modules, make import fail
        monkeypatch.setitem(sys.modules, "jwt", None)
        cfg = {
            "app_id": "12345", "installation_id": "67890",
            "private_key_pem": rsa_keypair,
        }
        assert gaa.mint_installation_token(cfg) is None
