"""
Alma Insights — Token obfuscation primitives.

Tiny, dependency-free helpers used only at build time (to obfuscate
a release PAT before bundling) and at runtime (to deobfuscate it).

What this is for
----------------

Anyone with the installer can read its bytes — that is a fundamental
property of client-shipped software, not a hole we can close. The threat
this module addresses is **passive discovery**:

* `strings(1)` against the bundle won't surface a literal
  ``github_pat_*`` token.
* Naive grep over the source tree won't find the token in plaintext.
* Malware-scanner heuristics that flag GitHub PAT prefixes won't trip.
* The token won't appear in screenshots, log dumps, or stack traces.

A motivated attacker with a debugger gets the token in five minutes.
That is fine: the token's actual security boundary is the GitHub
permissions it grants (``Contents: Read-only`` on a single repo). If
those permissions are scoped correctly, extraction by an attacker buys
them nothing they couldn't already get from the same release feed.

Algorithm
---------

XOR the UTF-8 bytes of the token against a per-build random key,
base64-encode both halves separately, and store as two strings. Decode
is the inverse. Deterministic given the key — perfect for unit tests.

Public API
----------

* :func:`obfuscate(token, key)` — for build scripts (``installer/``).
* :func:`deobfuscate(payload, key)` — for runtime (loaded from the
  bundled ``release_credentials.py``).
* :func:`generate_key(n_bytes=32)` — a fresh per-build random key.
"""

from __future__ import annotations

import base64
import os


# ──────────────────────────────────────────────────────────────────
# Build-time helpers (called by installer/build_release.py)
# ──────────────────────────────────────────────────────────────────

def generate_key(n_bytes: int = 32) -> str:
    """Return a fresh url-safe base64-encoded random key.

    Called once per build. The key is bundled alongside the obfuscated
    token; the pair is meaningless without each other, but neither is
    truly secret — see this module's docstring.
    """
    if n_bytes < 16:
        raise ValueError("Key must be at least 16 bytes")
    return base64.urlsafe_b64encode(os.urandom(n_bytes)).decode("ascii")


def obfuscate(token: str, key: str) -> str:
    """XOR ``token`` (UTF-8) with ``key`` (decoded from urlsafe-b64),
    return the result as a urlsafe-b64 string.

    Pure function: same inputs always produce the same output. This is
    important for build reproducibility — if CI re-runs with the same
    secret, the bundle bytes are identical except for the key (which we
    re-randomize each build, so the token bytes change between builds).
    """
    if not token:
        raise ValueError("Token must be a non-empty string")
    key_bytes = base64.urlsafe_b64decode(_pad_b64(key))
    if not key_bytes:
        raise ValueError("Key decoded to zero bytes")
    token_bytes = token.encode("utf-8")
    cipher = bytes(t ^ key_bytes[i % len(key_bytes)] for i, t in enumerate(token_bytes))
    return base64.urlsafe_b64encode(cipher).decode("ascii")


# ──────────────────────────────────────────────────────────────────
# Runtime helpers (called by src/updater/_bundled_token.py)
# ──────────────────────────────────────────────────────────────────

def deobfuscate(payload: str, key: str) -> str:
    """Inverse of :func:`obfuscate`. Returns the original token string.

    Defensive: any decoding error surfaces as a single ``ValueError``
    so the caller can treat "no usable bundled token" as one case
    instead of catching every binascii / unicode subclass.
    """
    if not payload or not key:
        raise ValueError("Payload and key must both be non-empty")
    try:
        cipher = base64.urlsafe_b64decode(_pad_b64(payload))
        key_bytes = base64.urlsafe_b64decode(_pad_b64(key))
    except Exception as exc:  # noqa: BLE001 — collapse to ValueError
        raise ValueError(f"Could not decode payload/key: {exc}") from exc
    if not key_bytes:
        raise ValueError("Decoded key is empty")
    plain = bytes(c ^ key_bytes[i % len(key_bytes)] for i, c in enumerate(cipher))
    try:
        return plain.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Decoded bytes are not valid UTF-8: {exc}") from exc


def fingerprint(token: str) -> str:
    """Return a non-secret display fingerprint for a token.

    Format: ``{first4}••••{last4}`` (or ``••••`` if the token is too
    short to safely truncate). Safe to log, safe to display in support
    UIs. Intentionally not a hash — we want operators to be able to
    eyeball "yes that's the new bot PAT" against their CI secrets
    panel without reaching for `sha256sum`.
    """
    if not token:
        return "<empty>"
    if len(token) < 12:
        return "••••"
    return f"{token[:4]}••••{token[-4:]}"


# ──────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────

def _pad_b64(s: str) -> str:
    """Add ``=`` padding so urlsafe_b64decode accepts the string."""
    return s + "=" * ((4 - len(s) % 4) % 4)
