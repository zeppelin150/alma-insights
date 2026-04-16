"""
Check 1 — Python environment.

Verifies the Python version + every core dependency imports without error.
Critical: if any heavy import fails, the app cannot run.
"""

from __future__ import annotations

import sys

from src.startup.checker import CheckResult

_MIN_PYTHON = (3, 10)

_CORE_IMPORTS = [
    "PySide6",
    "pandas",
    "numpy",
    "yaml",
    "keyring",
    "sentence_transformers",
    "sklearn",
    "scipy",
]


def check_environment() -> CheckResult:
    version = sys.version_info
    if (version.major, version.minor) < _MIN_PYTHON:
        return CheckResult(
            id="environment",
            name="Checking Python environment",
            status="fail",
            message=f"Python {version.major}.{version.minor} — need >= 3.10",
            remediation="Reinstall Alma Insights to get the bundled Python 3.12.",
            critical=True,
        )

    missing = _find_missing_imports()
    if missing:
        return CheckResult(
            id="environment",
            name="Checking Python environment",
            status="fail",
            message=f"Missing modules: {', '.join(missing)}",
            remediation="Reinstall Alma Insights to restore the bundled runtime.",
            critical=True,
        )

    py_str = f"Python {version.major}.{version.minor}.{version.micro}"
    return CheckResult(
        id="environment",
        name="Checking Python environment",
        status="pass",
        message=f"{py_str} — {len(_CORE_IMPORTS)} dependencies loaded",
        critical=True,
    )


def _find_missing_imports() -> list[str]:
    """Return module names that fail to import."""
    missing = []
    for name in _CORE_IMPORTS:
        try:
            __import__(name)
        except ImportError:
            missing.append(name)
    return missing
