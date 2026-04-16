"""
Alma Insights — GitHub App installation-token minter.

Phase-3 alternative to PAT-based update auth. A GitHub App is installed
on the update repo and given *Contents: read* permission. The app ID +
private key travel with the installer (they only grant read-only
single-repo access; even if extracted, an attacker gains no more than
they already have by possessing the installer).

At runtime we mint a short-lived installation access token (8-hour
TTL) and pass it to `UpdateChecker` as a Bearer header, exactly like a
PAT. Nothing else in the stack cares which kind of token it got.

Activation:
  1. Install PyJWT                           (shipped in requirements.txt)
  2. Add to settings.yaml:
       updates:
         auth_mode: github_app
         github_repo: owner/repo
         app_id: "<numeric id>"
         installation_id: "<numeric id>"
         private_key_path: "<absolute path>"  # or private_key_pem inline

Public API:
    mint_installation_token(cfg) -> str | None

Returns None and logs a warning on any failure — the UpdateChecker
treats a blank token as "unauthenticated" and surfaces the result as
the usual `check_failed` signal.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger("alma.updater")

_JWT_TTL_SECONDS = 600        # GitHub caps app JWTs at 10 minutes
_TOKEN_TIMEOUT = 15


# ──────────────────────────────────────────────────────────────────
# Public entrypoint
# ──────────────────────────────────────────────────────────────────

def mint_installation_token(cfg: dict) -> str | None:
    """Mint an installation access token from the provided update config.

    `cfg` is the `updates` section of settings.yaml. Required keys:
        app_id (str | int)
        installation_id (str | int)
        private_key_pem OR private_key_path

    Returns the token string or None on any error.
    """
    try:
        app_id = str(cfg["app_id"]).strip()
        installation_id = str(cfg["installation_id"]).strip()
    except KeyError as exc:
        logger.warning("GitHub App config missing: %s", exc)
        return None

    private_key = _load_private_key(cfg)
    if not private_key:
        return None

    try:
        jwt_token = _sign_app_jwt(app_id, private_key)
    except _JwtUnavailable:
        logger.warning(
            "PyJWT is not installed — cannot use auth_mode=github_app. "
            "Run `pip install PyJWT` or switch to auth_mode=pat."
        )
        return None
    except Exception as exc:  # noqa: BLE001 — signing errors must not crash
        logger.warning("Failed to sign GitHub App JWT: %s", exc)
        return None

    return _exchange_for_installation_token(jwt_token, installation_id)


# ──────────────────────────────────────────────────────────────────
# JWT signing
# ──────────────────────────────────────────────────────────────────

class _JwtUnavailable(RuntimeError):
    """Raised when PyJWT isn't importable."""


def _sign_app_jwt(app_id: str, private_key_pem: str) -> str:
    """Sign the 10-minute JWT that identifies the GitHub App."""
    try:
        import jwt  # type: ignore[import-untyped]
    except ImportError as exc:
        raise _JwtUnavailable(str(exc)) from exc

    now = int(time.time())
    payload = {"iat": now - 30, "exp": now + _JWT_TTL_SECONDS, "iss": app_id}
    return jwt.encode(payload, private_key_pem, algorithm="RS256")


def _load_private_key(cfg: dict) -> str | None:
    """Return the PEM-encoded private key from cfg.private_key_pem or ..._path."""
    inline = cfg.get("private_key_pem")
    if inline:
        return str(inline)

    path_str = cfg.get("private_key_path")
    if not path_str:
        logger.warning("GitHub App config missing private_key_pem / private_key_path")
        return None

    path = Path(path_str).expanduser()
    if not path.is_file():
        logger.warning("GitHub App private key not found at %s", path)
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read GitHub App private key: %s", exc)
        return None


# ──────────────────────────────────────────────────────────────────
# Token exchange
# ──────────────────────────────────────────────────────────────────

def _exchange_for_installation_token(
    app_jwt: str, installation_id: str
) -> str | None:
    """POST to GitHub's /app/installations/{id}/access_tokens."""
    url = (
        "https://api.github.com/app/installations/"
        f"{installation_id}/access_tokens"
    )
    req = urllib.request.Request(
        url,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {app_jwt}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_TOKEN_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        logger.warning("GitHub App token exchange HTTP %s: %s", exc.code, exc.reason)
        return None
    except urllib.error.URLError as exc:
        logger.warning("GitHub App token exchange network error: %s", exc.reason)
        return None
    except (OSError, ValueError) as exc:
        logger.warning("GitHub App token exchange unexpected error: %s", exc)
        return None

    token = body.get("token")
    return str(token) if token else None
