"""
Alma Insights — Bundled release token loader.

Reads the build-time-injected ``installer/release_credentials.py`` (if
present) and returns the deobfuscated GitHub token used for auto-update
release-feed polling. The file is **never** committed — it is generated
by ``installer/build_release.py`` from a CI secret on each release.

Why this exists
---------------

End users (RCM associates) cannot reasonably be expected to navigate
GitHub's PAT generation flow. The bundled-token approach trades a
small amount of reverse-engineering risk (defended by
:mod:`src.updater._token_obfuscation`) for zero-friction onboarding.

Design notes
------------

* The bundled token is the **lowest-priority** source. If the user has
  pasted a personal/admin PAT in Settings (stored in the OS keyring),
  that wins. See :func:`src.updater.update_checker._load_token`.
* Dev checkouts (running ``python main.py`` from a ``git clone``) have
  no ``release_credentials.py`` and ``get_bundled_token()`` returns
  ``None`` — the existing PAT-or-disabled paths still apply.
* The schema field on the credentials file lets us evolve the format
  later (e.g. add a manifest-signature pubkey) without breaking
  installs that ship an older shape.

Public API
----------

* :func:`get_bundled_token` — returns the token or ``None``.
* :func:`bundled_token_fingerprint` — a safe-to-log display string.
* :func:`have_bundled_token` — fast yes/no for UI checks.
"""

from __future__ import annotations

import logging

from src.updater._token_obfuscation import deobfuscate, fingerprint

logger = logging.getLogger("alma.updater")

_CURRENT_SCHEMA = 1


# ──────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────

def get_bundled_token() -> str | None:
    """Return the deobfuscated bundled GitHub PAT, or ``None``.

    ``None`` is returned (silently) in any of these cases:

    * ``installer/release_credentials.py`` is absent (dev checkout).
    * The file's schema version is unknown — refuse to interpret a
      newer-format bundle on an older runtime.
    * Deobfuscation fails (file got truncated, key/payload mismatch,
      etc.). Logged at WARNING.

    The caller is :func:`update_checker._load_token`, which already
    treats a falsy token as "unauthenticated, fall through."
    """
    creds = _load_credentials_module()
    if creds is None:
        return None

    schema = getattr(creds, "SCHEMA", None)
    if schema != _CURRENT_SCHEMA:
        logger.warning(
            "release_credentials.py schema=%r unsupported (expected %d); "
            "ignoring bundled token", schema, _CURRENT_SCHEMA,
        )
        return None

    payload = getattr(creds, "TOKEN_PAYLOAD", None)
    key = getattr(creds, "TOKEN_KEY", None)
    if not payload or not key:
        logger.warning("release_credentials.py missing TOKEN_PAYLOAD or TOKEN_KEY")
        return None

    try:
        return deobfuscate(payload, key)
    except ValueError as exc:
        logger.warning("Could not deobfuscate bundled token: %s", exc)
        return None


def have_bundled_token() -> bool:
    """Cheap yes/no for UI surfaces ('Using bundled token' label etc.)."""
    return get_bundled_token() is not None


def bundled_token_fingerprint() -> str:
    """Return a logger-safe fingerprint for the bundled token.

    Returns ``"<no bundled token>"`` if the file is absent. Suitable
    for support tickets — answers "which release PAT is this install
    using?" without leaking the token.
    """
    token = get_bundled_token()
    if token is None:
        return "<no bundled token>"
    return fingerprint(token)


# ──────────────────────────────────────────────────────────────────
# Internals
# ──────────────────────────────────────────────────────────────────

def _load_credentials_module():
    """Import ``src.updater._release_credentials`` if it exists.

    The file is build-injected next to this loader for cohesion and to
    ride along with the existing ``src/`` copy in
    ``installer/build_release.py``. It is ``.gitignore``-d so dev
    checkouts never have it.

    Returns the module (or ``None`` if the file is missing). Any other
    import error is logged and treated as missing — we never want a
    malformed credentials file to crash app startup.
    """
    try:
        from src.updater import _release_credentials  # type: ignore
        return _release_credentials
    except ImportError:
        # Expected on dev checkouts — quiet by design.
        return None
    except Exception as exc:  # noqa: BLE001 — defensive; never fatal
        logger.warning("_release_credentials.py present but unreadable: %s", exc)
        return None
