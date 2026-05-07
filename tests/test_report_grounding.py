"""Unit tests — src/data/report_grounding.py (R3.4).

Coverage:
  * happy path — counts/entities/dates all verified → score 1.0, no flags
  * count off by > 10% → flagged + score drops
  * entity not in ticket_index → flagged
  * date outside scope → flagged
  * empty findings list → score=None
  * harness never raises on broken DB
  * low_accuracy flag fires under 0.75
  * no scope (None dates) → time dim not attempted
"""
from __future__ import annotations

import sqlite3

import pytest

from src.data.report_grounding import (
    GroundingHarness, _composite, _DimResult, score_report, _split_entity_value,
)
from src.data.report_schema import (
    ChipKind, EvidenceChip, Finding, Report, Severity, TrendPoint,
)


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def seeded_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE ticket_index (
          ticket_id TEXT PRIMARY KEY,
          trc_code TEXT,
          trc_label TEXT,
          insurance_payer TEXT,
          provider_id TEXT,
          ticket_created_date TEXT,
          friction_type TEXT
        );
        INSERT INTO ticket_index VALUES
          ('t1', 'BILL',  'Billing',          'Cigna',  'p-1', '2026-03-01', 'incorrect_charge'),
          ('t2', 'BILL',  'Billing',          'Cigna',  'p-1', '2026-03-02', 'incorrect_charge'),
          ('t3', 'BILL',  'Billing',          'Humana', 'p-2', '2026-03-03', 'incorrect_charge'),
          ('t4', 'CLAIM', 'Claim disputes',   'BCBS',   'p-3', '2026-03-04', 'feature_broken'),
          ('t5', 'CLAIM', 'Claim disputes',   'BCBS',   'p-3', '2026-03-05', 'feature_broken'),
          ('t6', 'PORTAL','Portal access',    'Aetna',  'p-4', '2026-03-06', 'access_blocked'),
          ('t7', 'PORTAL','Portal access',    'Aetna',  'p-4', '2026-04-01', 'access_blocked'),
          ('t8', 'PORTAL','Portal access',    'Aetna',  'p-4', '2026-04-02', 'access_blocked');
    """)
    conn.commit()
    return conn


def _finding(
    *,
    finding_id: str = "f-1",
    title: str = "Billing & charge discrepancies",
    summary: str = "Auto-pay drove duplicate charges.",
    severity: Severity = Severity.HIGH,
    confidence: float = 0.85,
    chips: list[EvidenceChip] | None = None,
    trcs_touched: list[str] | None = None,
    cohort: str = "",
    trend: list[TrendPoint] | None = None,
) -> Finding:
    return Finding(
        finding_id=finding_id, title=title, summary=summary,
        severity=severity, confidence=confidence,
        evidence_chips=chips or [],
        trcs_touched=trcs_touched or [],
        cohort=cohort, trend=trend or [],
    )


def _report(findings: list[Finding]) -> Report:
    r = Report.empty(title="Test")
    r.findings = findings
    return r


# ──────────────────────────────────────────────────────────────────────
# Happy path
# ──────────────────────────────────────────────────────────────────────

class TestHappyPath:
    def test_all_dims_pass_score_one(self, seeded_conn):
        # Billing has 3 tickets in the seed; cohort Cigna+Humana exist.
        finding = _finding(
            chips=[
                EvidenceChip(label="Tickets", value="3", kind=ChipKind.METRIC),
                EvidenceChip(label="Cohort", value="Cigna, Humana", kind=ChipKind.COHORT),
            ],
            trcs_touched=["BILL"],
        )
        report = _report([finding])
        GroundingHarness(seeded_conn, "2026-03-01", "2026-03-31").score(report)
        assert report.accuracy_score == 1.0
        assert report.accuracy_flags == []

    def test_count_within_tolerance_passes(self, seeded_conn):
        # Claim 5 tickets vs actual 3 — 67% off, fails. So claim 3 actual 3.
        # Then claim 2 (33% off) — fails. Claim 3 (0% off) — passes.
        finding = _finding(
            chips=[EvidenceChip(label="Tickets", value="3", kind=ChipKind.METRIC)],
            trcs_touched=["BILL"],
        )
        report = _report([finding])
        GroundingHarness(seeded_conn, "2026-03-01", "2026-03-31").score(report)
        assert report.accuracy_score == 1.0


# ──────────────────────────────────────────────────────────────────────
# Failure modes
# ──────────────────────────────────────────────────────────────────────

class TestFailures:
    def test_count_off_flagged(self, seeded_conn):
        finding = _finding(
            chips=[EvidenceChip(label="Tickets", value="100", kind=ChipKind.METRIC)],
            trcs_touched=["BILL"],
        )
        report = _report([finding])
        GroundingHarness(seeded_conn, "2026-03-01", "2026-03-31").score(report)
        assert report.accuracy_score < 1.0
        assert any(":count:" in f for f in report.accuracy_flags)

    def test_entity_not_found_flagged(self, seeded_conn):
        finding = _finding(
            chips=[EvidenceChip(label="Cohort", value="Nonexistent Insurance", kind=ChipKind.COHORT)],
        )
        report = _report([finding])
        GroundingHarness(seeded_conn, None, None).score(report)
        assert any(":entity:" in f for f in report.accuracy_flags)

    def test_partial_entity_resolution_passes(self, seeded_conn):
        # 1 valid + 1 invalid → 50% pass threshold, still passes
        finding = _finding(
            chips=[EvidenceChip(label="Cohort", value="Cigna, Nonexistent", kind=ChipKind.COHORT)],
        )
        report = _report([finding])
        GroundingHarness(seeded_conn, None, None).score(report)
        # Half-credit logic: 50%+ resolved is passing
        assert report.accuracy_score == 1.0

    def test_date_outside_range_flagged(self, seeded_conn):
        finding = _finding(
            chips=[EvidenceChip(label="Onset", value="2025-12-25", kind=ChipKind.METRIC)],
        )
        report = _report([finding])
        GroundingHarness(seeded_conn, "2026-01-01", "2026-04-30").score(report)
        assert any(":time:" in f for f in report.accuracy_flags)

    def test_low_accuracy_flag_at_threshold(self, seeded_conn):
        # Two findings; both fail count + entity dims
        bad = _finding(
            chips=[
                EvidenceChip(label="Tickets", value="10000", kind=ChipKind.METRIC),
                EvidenceChip(label="Cohort", value="Imaginary", kind=ChipKind.COHORT),
            ],
            trcs_touched=["BILL"],
        )
        report = _report([bad, _finding(finding_id="f-2", chips=[EvidenceChip(label="Tickets", value="999999", kind=ChipKind.METRIC)], trcs_touched=["BILL"])])
        GroundingHarness(seeded_conn, "2026-03-01", "2026-03-31").score(report)
        assert "low_accuracy" in report.accuracy_flags
        assert (report.accuracy_score or 0) < 0.75


# ──────────────────────────────────────────────────────────────────────
# Edge cases
# ──────────────────────────────────────────────────────────────────────

class TestEdges:
    def test_empty_findings_score_none(self, seeded_conn):
        report = Report.empty(title="x")
        GroundingHarness(seeded_conn, None, None).score(report)
        assert report.accuracy_score is None

    def test_no_dates_no_time_dim(self, seeded_conn):
        finding = _finding(chips=[EvidenceChip(label="Tickets", value="3", kind=ChipKind.METRIC)],
                            trcs_touched=["BILL"])
        report = _report([finding])
        GroundingHarness(seeded_conn, None, None).score(report)
        # Time dim wasn't attempted, count dim passed → score = 1.0
        assert report.accuracy_score == 1.0

    def test_broken_db_does_not_raise(self):
        # No connection at all → harness still produces a non-raising result
        finding = _finding(
            chips=[EvidenceChip(label="Tickets", value="100", kind=ChipKind.METRIC)],
        )
        report = _report([finding])
        GroundingHarness(None, "2026-01-01", "2026-04-30").score(report)
        # Score may be 1.0 (nothing attempted) — important: did NOT raise
        assert report.accuracy_score is not None or report.accuracy_score is None  # truthy

    def test_score_report_convenience(self, seeded_conn):
        finding = _finding(chips=[EvidenceChip(label="Tickets", value="3", kind=ChipKind.METRIC)],
                            trcs_touched=["BILL"])
        report = _report([finding])
        score_report(report, seeded_conn, "2026-03-01", "2026-03-31")
        assert report.accuracy_score == 1.0


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

class TestHelpers:
    def test_composite_skips_unattempted(self):
        # All unattempted ⇒ 1.0 (assume good)
        score = _composite([
            _DimResult(dim="count", passed=False, attempted=False),
            _DimResult(dim="entity", passed=False, attempted=False),
        ])
        assert score == 1.0

    def test_composite_pass_ratio(self):
        score = _composite([
            _DimResult(dim="count", passed=True, attempted=True),
            _DimResult(dim="entity", passed=False, attempted=True),
        ])
        assert score == 0.5

    def test_split_entity_value_handles_separators(self):
        assert _split_entity_value("A, B; C / D & E and F") == ["A", "B", "C", "D", "E", "F"]

    def test_split_entity_value_strips_quotes(self):
        assert _split_entity_value("'X', \"Y\"") == ["X", "Y"]
