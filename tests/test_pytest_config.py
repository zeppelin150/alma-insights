"""Regression — pytest.ini carries the timeout that prevents Windows
test-suite hangs (memory: zombie_processes.md, CLAUDE.md "Testing notes").

Without ``--timeout``, a test that holds a daemon thread or a stuck QThread
hangs the entire suite. The 2026-05-07 fix added ``--timeout=120
--timeout-method=thread`` to default addopts. This test locks it in.
"""
from __future__ import annotations

import configparser
import shutil
from pathlib import Path

import pytest


_PYTEST_INI = Path(__file__).resolve().parent.parent / "pytest.ini"


@pytest.fixture(scope="module")
def parsed_ini() -> configparser.ConfigParser:
    if not _PYTEST_INI.is_file():
        pytest.skip("pytest.ini missing")
    cp = configparser.ConfigParser()
    cp.read(_PYTEST_INI, encoding="utf-8")
    return cp


def test_addopts_includes_timeout(parsed_ini):
    addopts = parsed_ini.get("pytest", "addopts", fallback="")
    assert "--timeout=" in addopts, (
        "pytest.ini addopts must declare a --timeout= cap to prevent hangs"
    )


def test_timeout_method_thread(parsed_ini):
    addopts = parsed_ini.get("pytest", "addopts", fallback="")
    # thread method is the safe default on Windows; signal-based timeouts
    # don't work because Windows doesn't support SIGALRM.
    assert "--timeout-method=thread" in addopts, (
        "Use --timeout-method=thread on Windows; signal mode is unsupported."
    )


def test_pytest_timeout_installed():
    """The `pytest-timeout` plugin must be importable; otherwise the
    --timeout flag is silently dropped by pytest."""
    pytest.importorskip("pytest_timeout")


def test_known_markers_declared(parsed_ini):
    """CLAUDE.md documents @slow / @e2e / @ui / @live_db markers — ensure
    pytest knows about them so it doesn't warn 'unknown marker' on every
    run. The 2026-05-07 update added `live` to the registry."""
    markers = parsed_ini.get("pytest", "markers", fallback="")
    for required in ("live", "slow", "e2e", "ui"):
        assert required in markers, (
            f"pytest.ini markers section missing '{required}'"
        )
