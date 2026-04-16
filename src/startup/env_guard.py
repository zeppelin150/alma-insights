"""
Alma Insights — Eager Environment Guard

Sets HIPAA air-gap + ML-telemetry kill-switches BEFORE any import that
could reach the network. Must be called first thing in main.py — the
Hugging Face libraries only read these variables once at import time.

Two stages:

1. enforce()             — idempotent, sets the REQUIRED_ENV_VARS and
                           strips localhost proxy overrides. Safe to
                           call before any heavy import (no src.data.*
                           dependency, so torch is not pulled in).

2. scan_for_issues()     — runs later, inside the startup splash
                           (Check 6). Detects .env files, unexpected
                           proxies, and returns a human-readable report.

Public API:
    enforce()                 -> list[str]   fixes applied
    scan_for_issues(root)     -> list[str]   residual issues found

The module is stdlib-only so it can import safely at the top of main.py.
"""

from __future__ import annotations

import os
from pathlib import Path

# ──────────────────────────────────────────────────────────────────
# Required variables (superset of legacy src/data/embedding/air_gap.py)
# ──────────────────────────────────────────────────────────────────

_BUNDLE_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_MODEL_DIR = _BUNDLE_ROOT / "data" / "models"

REQUIRED_ENV_VARS: dict[str, str] = {
    # HIPAA air-gap — Hugging Face stack
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "SENTENCE_TRANSFORMERS_HOME": str(_DEFAULT_MODEL_DIR),

    # Prevent other ML libs from phoning home
    "WANDB_DISABLED": "true",
    "MLFLOW_TRACKING_URI": "",
    "TOKENIZERS_PARALLELISM": "false",

    # Stop pip from contacting PyPI during the bundled python runtime
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",

    # Proxies — belt and braces
    "NO_PROXY": "*",
}

_PROXY_VARS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
)


# ──────────────────────────────────────────────────────────────────
# Stage 1 — call this first thing in main.py
# ──────────────────────────────────────────────────────────────────

def enforce() -> list[str]:
    """
    Set REQUIRED_ENV_VARS and remove localhost proxy overrides.

    Returns a list of human-readable change descriptions. An empty
    list means the environment was already correctly configured.
    """
    changes: list[str] = []

    for key, value in REQUIRED_ENV_VARS.items():
        if os.environ.get(key) != value:
            os.environ[key] = value
            changes.append(f"set {key}")

    for proxy_var in _PROXY_VARS:
        val = os.environ.get(proxy_var, "")
        if val and _is_local_proxy(val):
            os.environ.pop(proxy_var, None)
            changes.append(f"removed local proxy {proxy_var}={val}")

    return changes


def _is_local_proxy(value: str) -> bool:
    """True if the proxy URL points at localhost or 127.0.0.1."""
    lowered = value.lower()
    return "localhost" in lowered or "127.0.0.1" in lowered


# ──────────────────────────────────────────────────────────────────
# Stage 2 — called by startup Check 6 for user-visible diagnostics
# ──────────────────────────────────────────────────────────────────

def scan_for_issues(app_root: Path | None = None) -> list[str]:
    """
    Look for residual problems after enforce() has run. Returns a list
    of issue strings; empty list means all clear.
    """
    issues: list[str] = []
    root = Path(app_root) if app_root else _BUNDLE_ROOT

    if (root / ".env").exists():
        issues.append(f".env file in {root} — may override air-gap vars")

    for key, expected in REQUIRED_ENV_VARS.items():
        actual = os.environ.get(key)
        if actual != expected:
            issues.append(f"{key}={actual!r} (expected {expected!r})")

    for proxy_var in _PROXY_VARS:
        val = os.environ.get(proxy_var, "")
        if val:
            issues.append(f"{proxy_var}={val} still set after enforce()")

    return issues


# ──────────────────────────────────────────────────────────────────
# Introspection
# ──────────────────────────────────────────────────────────────────

def current_state() -> dict[str, str | None]:
    """Return the current environment values for all guarded vars."""
    return {key: os.environ.get(key) for key in REQUIRED_ENV_VARS}
