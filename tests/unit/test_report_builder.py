"""
Alma Insights -- Unit tests for report_builder.py

Tests the per-section formatters (pure functions, no DB) and
build_data_block (requires seeded_db fixture).
"""

import json
from datetime import datetime, timedelta

import pytest

from src.data.report_builder import (
    _fmt_correlations,
    _fmt_csat_summary,
    _fmt_entity_distributions,
    _fmt_incident_flags,
    _fmt_interventions,
    _fmt_product_gap_flags,
    _fmt_redacted_samples,
    _fmt_resolution_times,
    _fmt_rising_terms,
    _fmt_sentiment_by_trc,
    _fmt_top_terms,
    _fmt_topline,
    _fmt_trc_distribution,
    format_data_block_for_prompt,
)


# ── Helpers ──────────────────────────────────────────────────────────

def _make_full_block():
    """Construct a realistic data block dict with all sections populated."""
    return {
        "ticket_count": 250,
        "date_range": "2026-02-01 to 2026-02-28",
        "trc_distribution": [
            {"trc": "TRC-100", "count": 120, "avg_csat": 3.45, "avg_messages": 4.2},
            {"trc": "TRC-200", "count": 80, "avg_csat": 2.90, "avg_messages": 6.1},
            {"trc": "TRC-300", "count": 50, "avg_csat": None, "avg_messages": 3.0},
        ],
        "csat_summary": {
            "average": 3.25, "min": 1.0, "max": 5.0, "rated_count": 200,
        },
        "resolution_times": {
            "avg_assignment_to_resolution": 12.5,
            "avg_total_resolution": 18.3,
            "avg_first_reply": 1.2,
        },
        "top_terms": [
            {"term": "billing", "score": 0.85},
            {"term": "refund", "score": 0.72},
            {"term": "payment", "score": 0.65},
        ],
        "rising_terms": [
            {"term": "outage", "velocity": 3.2},
            {"term": "downtime", "velocity": 2.1},
        ],
        "sentiment_by_trc": {
            "TRC-100": [("2026-W05", -0.3), ("2026-W06", -0.1)],
            "TRC-200": 0.45,
        },
        "correlations": [
            {"trc_a": "TRC-100", "trc_b": "TRC-200", "correlation": 0.78},
        ],
        "incident_flags": [
            {"trc": "TRC-100", "type": "spike", "observed": 45,
             "expected": 20, "p_value": 0.001},
        ],
        "intervention_context": [
            {"event_date": "2026-02-15", "name": "Billing fix deployed",
             "category": "Engineering", "affected_trcs": '["TRC-100"]'},
        ],
        "payer_distribution": [
            {"entity_value": "Aetna", "ticket_count": 60},
            {"entity_value": "BCBS", "ticket_count": 40},
        ],
        "product_area_distribution": [
            {"entity_value": "Claims Portal", "ticket_count": 90},
        ],
        "product_gap_flags": [
            {"product_area": "Claims Portal", "trc_count": 5,
             "volume_velocity": 0.35, "sentiment_delta": -0.120,
             "gap_score": 7.2, "is_flagged": True},
        ],
        "redacted_samples": [
            "Customer said they cannot log in after password reset.",
            "Agent resolved the billing dispute successfully.",
        ],
    }


# ═══════════════════════════════════════════════════════════════
#  Per-section formatter tests
# ═══════════════════════════════════════════════════════════════

class TestFmtTopline:
    def test_with_data(self):
        result = _fmt_topline({"ticket_count": 42, "date_range": "2026-01-01 to 2026-01-31"})
        assert "42 tickets" in result
        assert "2026-01-01 to 2026-01-31" in result

    def test_missing_keys_uses_defaults(self):
        result = _fmt_topline({})
        assert "0 tickets" in result
        assert "N/A" in result


class TestFmtTrcDistribution:
    def test_with_data(self):
        block = {"trc_distribution": [
            {"trc": "TRC-100", "count": 50, "avg_csat": 3.5, "avg_messages": 4.0},
            {"trc": "TRC-200", "count": 30, "avg_csat": None, "avg_messages": 2.0},
        ]}
        result = _fmt_trc_distribution(block)
        assert "TRC DISTRIBUTION:" in result
        assert "TRC-100: 50 tickets" in result
        assert "CSAT=3.5" in result
        # TRC-200 has no CSAT so it should NOT show CSAT
        assert "TRC-200: 30 tickets" in result
        line_200 = [l for l in result.split("\n") if "TRC-200" in l][0]
        assert "CSAT" not in line_200

    def test_empty_returns_empty(self):
        assert _fmt_trc_distribution({}) == ""
        assert _fmt_trc_distribution({"trc_distribution": []}) == ""

    def test_caps_at_15(self):
        block = {"trc_distribution": [
            {"trc": f"TRC-{i}", "count": i, "avg_csat": None, "avg_messages": None}
            for i in range(20)
        ]}
        result = _fmt_trc_distribution(block)
        # Header + 15 data lines
        assert len(result.strip().split("\n")) == 16


class TestFmtCsatSummary:
    def test_with_data(self):
        block = {"csat_summary": {"average": 3.25, "min": 1, "max": 5, "rated_count": 100}}
        result = _fmt_csat_summary(block)
        assert "avg=3.25" in result
        assert "range=1-5" in result
        assert "rated=100" in result

    def test_no_average_returns_empty(self):
        assert _fmt_csat_summary({"csat_summary": {"average": None}}) == ""
        assert _fmt_csat_summary({}) == ""


class TestFmtResolutionTimes:
    def test_with_data(self):
        block = {"resolution_times": {
            "avg_assignment_to_resolution": 12.5,
            "avg_total_resolution": 18.0,
            "avg_first_reply": 1.1,
        }}
        result = _fmt_resolution_times(block)
        assert "12.5" in result
        assert "18.0" in result  # avg_total_resolution
        assert "1.1" in result

    def test_all_none_returns_empty(self):
        block = {"resolution_times": {
            "avg_assignment_to_resolution": None,
            "avg_total_resolution": None,
            "avg_first_reply": None,
        }}
        assert _fmt_resolution_times(block) == ""


class TestFmtTopTerms:
    def test_dict_terms(self):
        block = {"top_terms": [{"term": "billing", "score": 0.85}]}
        result = _fmt_top_terms(block)
        assert "billing" in result
        assert "0.85" in result

    def test_tuple_terms(self):
        block = {"top_terms": [("refund", 0.7234)]}
        result = _fmt_top_terms(block)
        assert "refund" in result
        assert "0.7234" in result

    def test_plain_string_terms(self):
        block = {"top_terms": ["outage"]}
        result = _fmt_top_terms(block)
        assert "outage" in result

    def test_empty_returns_empty(self):
        assert _fmt_top_terms({}) == ""
        assert _fmt_top_terms({"top_terms": []}) == ""


class TestFmtRisingTerms:
    def test_dict_terms(self):
        block = {"rising_terms": [{"term": "outage", "velocity": 3.2}]}
        result = _fmt_rising_terms(block)
        assert "RISING TERMS" in result
        assert "outage" in result
        assert "3.2" in result

    def test_tuple_terms(self):
        block = {"rising_terms": [("spike", 1.5678)]}
        result = _fmt_rising_terms(block)
        assert "spike" in result
        assert "1.5678" in result

    def test_empty_returns_empty(self):
        assert _fmt_rising_terms({"rising_terms": []}) == ""


class TestFmtSentimentByTrc:
    def test_list_data_windows(self):
        block = {"sentiment_by_trc": {
            "TRC-100": [("W1", -0.3), ("W2", -0.1)],
        }}
        result = _fmt_sentiment_by_trc(block)
        assert "TRC-100" in result
        # avg of -0.3 and -0.1 = -0.200
        assert "-0.200" in result
        assert "2 windows" in result

    def test_scalar_data(self):
        block = {"sentiment_by_trc": {"TRC-200": 0.45}}
        result = _fmt_sentiment_by_trc(block)
        assert "TRC-200" in result
        assert "0.450" in result

    def test_empty_returns_empty(self):
        assert _fmt_sentiment_by_trc({}) == ""
        assert _fmt_sentiment_by_trc({"sentiment_by_trc": {}}) == ""


class TestFmtCorrelations:
    def test_dict_correlations(self):
        block = {"correlations": [
            {"trc_a": "TRC-100", "trc_b": "TRC-200", "correlation": 0.78},
        ]}
        result = _fmt_correlations(block)
        assert "TRC-100 <-> TRC-200" in result
        assert "0.78" in result

    def test_tuple_correlations(self):
        block = {"correlations": [("TRC-A", "TRC-B", 0.654)]}
        result = _fmt_correlations(block)
        assert "TRC-A <-> TRC-B" in result
        assert "0.654" in result

    def test_empty_returns_empty(self):
        assert _fmt_correlations({"correlations": []}) == ""


class TestFmtIncidentFlags:
    def test_with_p_value(self):
        block = {"incident_flags": [
            {"trc": "TRC-100", "type": "spike", "observed": 45,
             "expected": 20, "p_value": 0.001},
        ]}
        result = _fmt_incident_flags(block)
        assert "TRC-100" in result
        assert "spike" in result
        assert "observed=45" in result
        assert "expected=20" in result
        assert "p=0.0010" in result

    def test_without_p_value(self):
        block = {"incident_flags": [
            {"trc": "TRC-X", "type": "drop", "observed": 5, "expected": 15, "p_value": None},
        ]}
        result = _fmt_incident_flags(block)
        assert "TRC-X" in result
        assert "p=" not in result

    def test_empty_returns_empty(self):
        assert _fmt_incident_flags({"incident_flags": []}) == ""


class TestFmtInterventions:
    def test_json_affected_trcs(self):
        block = {"intervention_context": [
            {"event_date": "2026-02-15", "name": "Fix deployed",
             "category": "Eng", "affected_trcs": '["TRC-100", "TRC-200"]'},
        ]}
        result = _fmt_interventions(block)
        assert "Fix deployed" in result
        assert "TRC-100, TRC-200" in result

    def test_list_affected_trcs(self):
        block = {"intervention_context": [
            {"event_date": "2026-02-10", "name": "Hotfix",
             "category": "Ops", "affected_trcs": ["TRC-300"]},
        ]}
        result = _fmt_interventions(block)
        assert "TRC-300" in result

    def test_empty_affected_trcs_shows_all(self):
        block = {"intervention_context": [
            {"event_date": "2026-02-01", "name": "Global update",
             "category": "Product", "affected_trcs": ""},
        ]}
        result = _fmt_interventions(block)
        assert "all TRCs" in result

    def test_empty_returns_empty(self):
        assert _fmt_interventions({"intervention_context": []}) == ""


class TestFmtEntityDistributions:
    def test_both_populated(self):
        block = {
            "payer_distribution": [{"entity_value": "Aetna", "ticket_count": 60}],
            "product_area_distribution": [{"entity_value": "Claims", "ticket_count": 90}],
        }
        result = _fmt_entity_distributions(block)
        assert "PAYER DISTRIBUTION:" in result
        assert "Aetna: 60 tickets" in result
        assert "PRODUCT AREA DISTRIBUTION:" in result
        assert "Claims: 90 tickets" in result

    def test_only_payers(self):
        block = {"payer_distribution": [{"entity_value": "X", "ticket_count": 1}],
                 "product_area_distribution": []}
        result = _fmt_entity_distributions(block)
        assert "PAYER" in result
        assert "PRODUCT AREA" not in result

    def test_empty_returns_empty(self):
        result = _fmt_entity_distributions({})
        assert result == ""


class TestFmtProductGapFlags:
    def test_flagged_gap(self):
        block = {"product_gap_flags": [
            {"product_area": "Claims Portal", "trc_count": 5,
             "volume_velocity": 0.35, "sentiment_delta": -0.120,
             "gap_score": 7.2, "is_flagged": True},
        ]}
        result = _fmt_product_gap_flags(block)
        assert "Claims Portal" in result
        assert "FLAGGED" in result
        assert "+35%" in result
        assert "-0.120" in result

    def test_unflagged_gap_excluded(self):
        block = {"product_gap_flags": [
            {"product_area": "Billing", "trc_count": 2,
             "volume_velocity": 0.05, "sentiment_delta": 0.01,
             "gap_score": 1.0, "is_flagged": False},
        ]}
        result = _fmt_product_gap_flags(block)
        # Only unflagged items -> header only -> returns ""
        assert result == ""

    def test_empty_returns_empty(self):
        assert _fmt_product_gap_flags({"product_gap_flags": []}) == ""


class TestFmtRedactedSamples:
    def test_with_samples(self):
        block = {"redacted_samples": ["Sample one text.", "Sample two text."]}
        result = _fmt_redacted_samples(block)
        assert "SAMPLE CONVERSATIONS" in result
        assert "[1] Sample one text." in result
        assert "[2] Sample two text." in result

    def test_long_sample_truncated(self):
        block = {"redacted_samples": ["A" * 500]}
        result = _fmt_redacted_samples(block)
        # Should be truncated to 300 chars inside the formatter
        sample_line = result.split("\n")[1]
        assert len(sample_line) <= 310  # "[1] " prefix + 300

    def test_empty_returns_empty(self):
        assert _fmt_redacted_samples({"redacted_samples": []}) == ""


# ═══════════════════════════════════════════════════════════════
#  Orchestrator test
# ═══════════════════════════════════════════════════════════════

class TestFormatDataBlockForPrompt:
    def test_full_block_all_sections_present(self):
        block = _make_full_block()
        result = format_data_block_for_prompt(block)
        assert "TOPLINE:" in result
        assert "TRC DISTRIBUTION:" in result
        assert "CSAT SUMMARY:" in result
        assert "RESOLUTION TIMES:" in result
        assert "TOP TERMS" in result
        assert "RISING TERMS" in result
        assert "SENTIMENT BY TRC:" in result
        assert "CROSS-TRC CORRELATIONS:" in result
        assert "INCIDENT FLAGS:" in result
        assert "RECENT INTERVENTIONS:" in result
        assert "PAYER DISTRIBUTION:" in result
        assert "PRODUCT GAP FLAGS:" in result
        assert "SAMPLE CONVERSATIONS" in result

    def test_empty_block_only_topline(self):
        result = format_data_block_for_prompt({})
        # Topline always emits (with defaults)
        assert "TOPLINE:" in result
        # Empty sections should be omitted, not produce headers
        assert "TRC DISTRIBUTION:" not in result
        assert "CSAT SUMMARY:" not in result

    def test_sections_separated_by_double_newline(self):
        block = _make_full_block()
        result = format_data_block_for_prompt(block)
        # Sections are joined with \n\n
        assert "\n\n" in result


# ═══════════════════════════════════════════════════════════════
#  build_data_block (DB integration)
# ═══════════════════════════════════════════════════════════════

class TestBuildDataBlock:
    def test_returns_ticket_count_and_date_range(self, seeded_db):
        from src.data.report_builder import build_data_block

        today = datetime.now()
        d_start = (today - timedelta(days=30)).strftime("%Y-%m-%d")
        d_end = (today - timedelta(days=1)).strftime("%Y-%m-%d")

        block = build_data_block(seeded_db, d_start, d_end)
        assert block["ticket_count"] >= 1
        assert block["date_range"] == f"{d_start} to {d_end}"
        assert isinstance(block["trc_distribution"], list)
        assert isinstance(block["csat_summary"], dict)
        assert isinstance(block["resolution_times"], dict)

    def test_trc_filter_narrows_results(self, seeded_db):
        from src.data.report_builder import build_data_block

        today = datetime.now()
        d_start = (today - timedelta(days=30)).strftime("%Y-%m-%d")
        d_end = (today - timedelta(days=1)).strftime("%Y-%m-%d")

        all_block = build_data_block(seeded_db, d_start, d_end)
        filtered = build_data_block(seeded_db, d_start, d_end, trc_filter="TRC-100")

        assert filtered["ticket_count"] <= all_block["ticket_count"]
        assert filtered["ticket_count"] >= 1
        # Filtered distribution should only contain TRC-100
        for t in filtered["trc_distribution"]:
            assert t["trc"] == "TRC-100"

    def test_graceful_fallback_keys(self, seeded_db):
        """Even when trending/incident engines fail, block has all expected keys."""
        from src.data.report_builder import build_data_block

        today = datetime.now()
        d_start = (today - timedelta(days=30)).strftime("%Y-%m-%d")
        d_end = (today - timedelta(days=1)).strftime("%Y-%m-%d")

        block = build_data_block(seeded_db, d_start, d_end)
        expected_keys = [
            "ticket_count", "date_range", "trc_distribution", "csat_summary",
            "resolution_times", "top_terms", "rising_terms", "sentiment_by_trc",
            "correlations", "incident_flags", "intervention_context",
            "payer_distribution", "product_area_distribution", "redacted_samples",
            "product_gap_flags",
        ]
        for key in expected_keys:
            assert key in block, f"Missing key: {key}"
