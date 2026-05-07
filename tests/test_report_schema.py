"""Unit tests — src/data/report_schema.py (R1.9).

Round-trip + tolerant-parse coverage for the structured-output schema.
Covers: Severity coercion, Finding/Report dict round-trip, JSON
serialization, schema_for_prompt stability.
"""
from __future__ import annotations

import json

import pytest

from src.data.report_schema import (
    ChipKind, EvidenceChip, Finding, JSON_SCHEMA, Report, Severity,
    TrendPoint, schema_for_prompt,
)


# ──────────────────────────────────────────────────────────────────────
# Severity
# ──────────────────────────────────────────────────────────────────────

class TestSeverity:
    def test_canonical_values(self):
        assert Severity("high") is Severity.HIGH
        assert Severity("low") is Severity.LOW

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("HIGH", Severity.HIGH),
            ("Medium", Severity.MEDIUM),
            ("  low ", Severity.LOW),
            (None, Severity.INFO),
            ("", Severity.INFO),
            ("nonsense", Severity.INFO),
            (Severity.HIGH, Severity.HIGH),
        ],
    )
    def test_from_str_tolerant(self, raw, expected):
        assert Severity.from_str(raw) is expected


# ──────────────────────────────────────────────────────────────────────
# EvidenceChip
# ──────────────────────────────────────────────────────────────────────

class TestEvidenceChip:
    def test_round_trip_preserves_kind(self):
        chip = EvidenceChip(label="Tickets", value="275", kind=ChipKind.METRIC)
        rt = EvidenceChip.from_dict(chip.to_dict())
        assert rt == chip

    def test_default_kind_is_metric(self):
        chip = EvidenceChip.from_dict({"label": "x", "value": "y"})
        assert chip.kind is ChipKind.METRIC

    def test_unknown_kind_falls_back_to_metric(self):
        chip = EvidenceChip.from_dict({"label": "x", "value": "y", "kind": ""})
        assert chip.kind is ChipKind.METRIC


# ──────────────────────────────────────────────────────────────────────
# TrendPoint
# ──────────────────────────────────────────────────────────────────────

class TestTrendPoint:
    def test_value_coerced_to_float(self):
        p = TrendPoint.from_dict({"period": "2026-W14", "value": "12"})
        assert p.value == 12.0
        assert p.period == "2026-W14"


# ──────────────────────────────────────────────────────────────────────
# Finding
# ──────────────────────────────────────────────────────────────────────

class TestFinding:
    def _sample(self) -> Finding:
        return Finding(
            finding_id="f-x-01",
            title="Billing & charge discrepancies",
            summary="Auto-pay caused duplicate charges across Cigna/Humana/BCBS.",
            severity=Severity.HIGH,
            confidence=0.85,
            supporting_ticket_ids=["12345", "12346"],
            evidence_chips=[
                EvidenceChip(label="Tickets", value="275", kind=ChipKind.METRIC),
                EvidenceChip(label="Trend", value="+18% WoW", kind=ChipKind.TREND),
            ],
            trend=[TrendPoint(period="2026-W12", value=200)],
            body_md="## Detail\n\nFoo.",
            trcs_touched=["Billing", "Claims"],
            cohort="Cigna, Humana, BCBS",
        )

    def test_round_trip(self):
        f = self._sample()
        rt = Finding.from_dict(f.to_dict())
        assert rt.to_dict() == f.to_dict()

    def test_severity_round_trips(self):
        f = self._sample()
        assert Finding.from_dict(f.to_dict()).severity is Severity.HIGH

    def test_confidence_invalid_defaults_to_zero(self):
        d = self._sample().to_dict()
        d["confidence"] = "not-a-number"
        # from_dict tolerates with default 0 (parser path enforces clamping)
        f = Finding.from_dict(d)
        assert f.confidence == 0.0


# ──────────────────────────────────────────────────────────────────────
# Report
# ──────────────────────────────────────────────────────────────────────

class TestReport:
    def _sample(self) -> Report:
        return Report(
            report_id="r-test-01",
            title="Executive Summary — Q1",
            executive_summary="Net up. Three drivers identified.",
            findings=[
                Finding(finding_id="f-1", title="A", summary="..", severity=Severity.HIGH),
                Finding(finding_id="f-2", title="B", summary="..", severity=Severity.LOW),
            ],
            generated_at="2026-05-06T12:00:00+00:00",
            scope={"ticket_count": 1788, "date_range": "2026-01-01/2026-04-15"},
            pipeline_kind="multi_bridge",
            specialist_count=3,
            bridges_used=4,
            cost_usd=0.087,
            duration_sec=842.0,
            accuracy_score=0.91,
            accuracy_flags=[],
            raw_markdown="## Fallback",
        )

    def test_to_from_dict_round_trip(self):
        r = self._sample()
        rt = Report.from_dict(r.to_dict())
        assert rt.to_dict() == r.to_dict()

    def test_json_round_trip(self):
        r = self._sample()
        rt = Report.from_json(r.to_json())
        assert rt.title == r.title
        assert len(rt.findings) == 2
        assert rt.accuracy_score == 0.91

    def test_empty_factory_populates_defaults(self):
        r = Report.empty(title="Foo")
        assert r.title == "Foo"
        assert r.report_id.startswith("r-")
        assert r.pipeline_kind == "single_pass"
        assert r.findings == []

    def test_accuracy_score_none_persists(self):
        r = self._sample()
        r.accuracy_score = None
        rt = Report.from_dict(r.to_dict())
        assert rt.accuracy_score is None

    def test_from_json_empty_string_returns_empty(self):
        r = Report.from_json("")
        assert r.findings == []
        assert r.title == "Report"


# ──────────────────────────────────────────────────────────────────────
# JSON_SCHEMA + schema_for_prompt
# ──────────────────────────────────────────────────────────────────────

class TestJsonSchema:
    def test_schema_is_serializable(self):
        s = json.dumps(JSON_SCHEMA)
        assert "findings" in s

    def test_schema_for_prompt_is_indented(self):
        s = schema_for_prompt()
        # Indented + sorted keys ⇒ deterministic across edits
        assert "\n" in s
        assert s.find('"findings"') < s.find('"title"')  # alphabetical
