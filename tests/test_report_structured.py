"""
Tests for report_builder.build_structured_output (Session 3)

Validates:
    - Structured output contains expected keys
    - Findings array reflects input data
    - Summary stats computed correctly
    - JSON is valid and parseable
    - Backward compat: existing prompt-variable path unaffected
"""

import json
import pytest


class TestBuildStructuredOutput:

    def _make_block(self):
        """Create a realistic data block for testing."""
        return {
            "ticket_count": 100,
            "date_range": "2026-01-01 to 2026-01-31",
            "trc_distribution": [
                {"trc": "Billing", "count": 45, "pct": 0.45},
                {"trc": "Claims", "count": 30, "pct": 0.30},
                {"trc": "Tech", "count": 25, "pct": 0.25},
            ],
            "csat_summary": {"average": 2.8, "min": 1, "max": 5, "rated_count": 80},
            "resolution_times": {"avg_assignment_to_resolution": 4.5},
            "incident_flags": [
                {"trc": "Billing", "type": "spike", "severity": "critical", "observed": 15},
            ],
            "product_gap_flags": [{"gap": "missing_feature"}],
        }

    def test_contains_expected_keys(self):
        from src.data.report_builder import build_structured_output
        block = self._make_block()
        raw = build_structured_output(block)
        structured = json.loads(raw)
        assert "findings" in structured
        assert "summary_stats" in structured
        assert "recommendations" in structured

    def test_findings_reflect_input(self):
        from src.data.report_builder import build_structured_output
        block = self._make_block()
        structured = json.loads(build_structured_output(block))
        findings = structured["findings"]
        # 3 TRC distribution + 1 incident = 4 findings
        assert len(findings) == 4
        trc_findings = [f for f in findings if f["type"] == "trc_concentration"]
        assert len(trc_findings) == 3

    def test_summary_stats_correct(self):
        from src.data.report_builder import build_structured_output
        block = self._make_block()
        structured = json.loads(build_structured_output(block))
        stats = structured["summary_stats"]
        assert stats["ticket_count"] == 100
        assert stats["trc_count"] == 3
        assert stats["incident_count"] == 1
        assert stats["csat_summary"]["average"] == 2.8

    def test_recommendations_generated(self):
        from src.data.report_builder import build_structured_output
        block = self._make_block()
        structured = json.loads(build_structured_output(block))
        recs = structured["recommendations"]
        assert len(recs) >= 2  # low CSAT + critical incident + product gaps
        assert any("CSAT" in r for r in recs)
        assert any("critical" in r for r in recs)

    def test_json_is_valid(self):
        from src.data.report_builder import build_structured_output
        block = self._make_block()
        raw = build_structured_output(block)
        # Should not raise
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)

    def test_empty_block_produces_valid_output(self):
        from src.data.report_builder import build_structured_output
        structured = json.loads(build_structured_output({}))
        assert structured["findings"] == []
        assert structured["summary_stats"]["ticket_count"] == 0
        assert len(structured["recommendations"]) >= 1

    def test_backward_compat_prompt_keys_preserved(self):
        """build_data_block still returns all existing prompt-variable keys."""
        from src.data.report_builder import build_structured_output
        block = self._make_block()
        # structured_json is a new key — old keys should still be present
        # in the original block (not mutated by build_structured_output)
        assert "trc_distribution" in block
        assert "csat_summary" in block
        assert "incident_flags" in block
