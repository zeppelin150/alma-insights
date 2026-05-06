"""Opt-in pytest wrapper around tools/audit_taxonomies.py.

Default policy: this test is **skipped** in normal CI runs because the
audit is informational, not a regression. To run it explicitly:

    python -m pytest tests/test_taxonomy_drift.py --run-audit

When the env var ALMA_RUN_AUDIT=1 is set the test runs unconditionally
(useful for the scheduled-task wrapper).

The test passes when:
  - The audit runs without error against the production DB
  - The number of suspected typos is below TYPO_TOLERANCE (default 5)

It surfaces a warning (not a failure) for:
  - Proposed additions
  - Proposed retirements
  - Snapshot-diff new arrivals

Failure modes (FAIL):
  - Audit script raised
  - Suspected typos ≥ TYPO_TOLERANCE — likely classifier regression

The threshold is intentionally generous because the value of the
audit is human review of the markdown report, not pass/fail noise.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


TYPO_TOLERANCE = 5


def pytest_addoption(parser):  # pragma: no cover — pytest hook
    """Allow `--run-audit` to opt in even outside CI."""
    parser.addoption(
        "--run-audit", action="store_true",
        help="Run the taxonomy drift audit (otherwise skipped)",
    )


def _should_run(request) -> bool:
    if os.environ.get("ALMA_RUN_AUDIT") == "1":
        return True
    try:
        return bool(request.config.getoption("--run-audit"))
    except (ValueError, AttributeError):
        return False


@pytest.fixture
def audit_env(request, tmp_path, monkeypatch):
    if not _should_run(request):
        pytest.skip(
            "Taxonomy audit is opt-in. Run with --run-audit or "
            "ALMA_RUN_AUDIT=1 to invoke."
        )
    return tmp_path


def test_audit_runs_against_production_db(audit_env):
    """Smoke: the audit completes against data/local_warehouse.db without
    raising, and writes its three artifacts."""
    from tools.audit_taxonomies import run_audit, REPORTS_DIR, DEFAULT_DB

    if not DEFAULT_DB.exists():
        pytest.skip(f"production DB not present at {DEFAULT_DB}")

    report = run_audit(DEFAULT_DB, days=30)
    assert "columns" in report
    assert len(report["columns"]) >= 3  # friction + sentiment + anomaly minimum
    today = report["date"]
    assert (REPORTS_DIR / f"{today}.md").exists()
    assert (REPORTS_DIR / f"{today}.json").exists()
    assert (REPORTS_DIR / "last_snapshot.json").exists()


def test_typo_count_under_tolerance(audit_env):
    """Treat a sudden flood of typos (vs canonical) as a probable
    classifier regression. Stays informational below TYPO_TOLERANCE."""
    from tools.audit_taxonomies import run_audit, DEFAULT_DB

    if not DEFAULT_DB.exists():
        pytest.skip(f"production DB not present at {DEFAULT_DB}")

    report = run_audit(DEFAULT_DB, days=30)
    total_typos = sum(
        len(c.get("suspected_typos", [])) for c in report["columns"]
    )
    assert total_typos < TYPO_TOLERANCE, (
        f"{total_typos} suspected typos found (≥ {TYPO_TOLERANCE} "
        "tolerance). See tools/audit_reports/<today>.md for details — "
        "likely an NLP classifier regression."
    )
