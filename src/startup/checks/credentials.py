"""
Check 2 — Credential store.

Probes the OS keyring, migrates any legacy plaintext credentials, and
reports how many secrets are currently stored. Critical: if the keyring
is unreachable, we cannot load API keys or the Lightdash PAT.
"""

from __future__ import annotations

import platform

from src.data import pat_store
from src.startup.checker import CheckResult


def check_credentials() -> CheckResult:
    ok, backend = pat_store.keyring_available()
    if not ok:
        return CheckResult(
            id="credentials",
            name="Checking credential store",
            status="fail",
            message=f"Keyring unavailable — {backend}",
            remediation=_remediation_for_platform(),
            critical=True,
        )

    migrated = pat_store.migrate_legacy_credentials()
    stored = _count_stored_secrets()

    if migrated:
        message = f"Keyring accessible — migrated {migrated}, {stored} credential(s) stored"
    else:
        message = f"Keyring accessible — {stored} credential(s) stored"

    return CheckResult(
        id="credentials",
        name="Checking credential store",
        status="pass",
        message=message,
        critical=True,
    )


def _count_stored_secrets() -> int:
    """Count how many of the known secret keys have a non-empty value."""
    count = 0
    for key in pat_store._SECRET_KEYS:
        if pat_store.load_setting(key):
            count += 1
    return count


def _remediation_for_platform() -> str:
    system = platform.system()
    if system == "Windows":
        return (
            "Windows Credential Manager is unreachable. Run `services.msc` "
            "and confirm the Credential Manager service is Running."
        )
    if system == "Darwin":
        return (
            "macOS Keychain is locked or unavailable. Open Keychain Access "
            "and unlock the login keychain, then relaunch."
        )
    return "Install a Secret Service–compatible keyring backend."
