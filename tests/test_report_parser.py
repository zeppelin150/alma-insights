"""Unit tests — src/data/report_parser.py (R1.9).

Coverage:
  * happy path — JSON-fenced block parses into a populated Report
  * bare top-level JSON parses (no fence)
  * empty / whitespace input falls back gracefully
  * malformed JSON → legacy fallback (raw_markdown carries text)
  * schema-failure (missing required) → legacy fallback
  * confidence clamped to [0,1]
  * fence followed by extra commentary still parses
  * report_id stability when caller supplies one
"""
from __future__ import annotations

import json

import pytest

from src.data.report_parser import (
    extract_json_block, parse_report,
)
from src.data.report_schema import Severity


def _good_block(title: str = "Test Report", n: int = 2) -> str:
    findings = [
        {
            "title": f"Finding {i}",
            "summary": "summary",
            "severity": "high" if i == 1 else "medium",
            "confidence": 0.7,
            "supporting_ticket_ids": [f"t{i}"],
            "evidence_chips": [{"label": "Tickets", "value": str(i * 10)}],
            "body_md": "detail",
        }
        for i in range(1, n + 1)
    ]
    return json.dumps({"title": title, "executive_summary": "summary", "findings": findings})


def _wrap_fence(body: str, lang: str = "json") -> str:
    return f"Some preamble.\n\n```{lang}\n{body}\n```\n\nTrailing commentary."


# ──────────────────────────────────────────────────────────────────────
# extract_json_block
# ──────────────────────────────────────────────────────────────────────

class TestExtractJsonBlock:
    def test_finds_fenced_json(self):
        text = _wrap_fence(_good_block())
        assert extract_json_block(text) is not None

    def test_finds_fenced_json_uppercase_lang(self):
        text = _wrap_fence(_good_block(), lang="JSON")
        assert extract_json_block(text) is not None

    def test_finds_unfenced_json_with_findings_key(self):
        body = _good_block()
        assert extract_json_block(body) == body

    def test_no_block_returns_none(self):
        assert extract_json_block("just plain text") is None
        assert extract_json_block("") is None


# ──────────────────────────────────────────────────────────────────────
# parse_report — happy paths
# ──────────────────────────────────────────────────────────────────────

class TestParseReport:
    def test_fenced_block_round_trips(self):
        text = _wrap_fence(_good_block(title="Hello", n=3))
        report = parse_report(text, fallback_title="Hello")
        assert report.title == "Hello"
        assert len(report.findings) == 3
        assert report.findings[0].severity is Severity.HIGH
        assert "legacy_unparseable" not in report.accuracy_flags

    def test_bare_json_parses(self):
        report = parse_report(_good_block(n=1))
        assert len(report.findings) == 1

    def test_pipeline_kind_propagates(self):
        text = _wrap_fence(_good_block())
        report = parse_report(text, pipeline_kind="multi_bridge")
        assert report.pipeline_kind == "multi_bridge"

    def test_caller_report_id_preserved(self):
        text = _wrap_fence(_good_block())
        report = parse_report(text, report_id="r-fixed")
        assert report.report_id == "r-fixed"

    def test_finding_id_auto_assigned_when_missing(self):
        text = _wrap_fence(_good_block(n=2))
        report = parse_report(text, report_id="r-fixed")
        ids = [f.finding_id for f in report.findings]
        assert ids[0].startswith("f-r-fixed-")
        assert len(set(ids)) == len(ids)

    def test_confidence_clamped(self):
        body = json.dumps({
            "title": "X",
            "findings": [
                {"title": "f", "summary": "s", "severity": "low", "confidence": 5.0},
            ],
        })
        report = parse_report(_wrap_fence(body))
        assert report.findings[0].confidence == 1.0

    def test_invalid_chip_skipped(self):
        body = json.dumps({
            "title": "X",
            "findings": [
                {"title": "f", "summary": "s", "severity": "info",
                 "evidence_chips": ["not-a-dict", {"label": "ok", "value": "v"}]},
            ],
        })
        report = parse_report(_wrap_fence(body))
        chips = report.findings[0].evidence_chips
        assert len(chips) == 1
        assert chips[0].label == "ok"

    def test_extra_commentary_ignored(self):
        text = (
            "Here is your report:\n\n"
            f"```json\n{_good_block()}\n```\n\n"
            "Hope this helps!"
        )
        report = parse_report(text)
        assert len(report.findings) == 2


# ──────────────────────────────────────────────────────────────────────
# parse_report — fallback paths
# ──────────────────────────────────────────────────────────────────────

class TestParseReportFallback:
    def test_empty_input_yields_empty_report(self):
        report = parse_report("")
        assert report.findings == []
        assert "empty_response" in report.accuracy_flags

    def test_no_json_block_yields_legacy_fallback(self):
        report = parse_report("# Just markdown\n\nNo JSON here.")
        assert report.findings == []
        assert "legacy_unparseable" in report.accuracy_flags
        assert "# Just markdown" in report.raw_markdown

    def test_malformed_json_yields_legacy_fallback(self):
        bad = "```json\n{not: valid, json: }}\n```"
        report = parse_report(bad)
        assert "legacy_unparseable" in report.accuracy_flags
        assert "raw_markdown" not in report.accuracy_flags  # sanity

    def test_schema_failure_yields_legacy_fallback(self):
        # Missing required 'findings' key
        body = json.dumps({"title": "x", "executive_summary": "y"})
        report = parse_report(_wrap_fence(body))
        assert "legacy_unparseable" in report.accuracy_flags

    def test_findings_array_empty_yields_legacy_fallback(self):
        body = json.dumps({"title": "x", "findings": []})
        report = parse_report(_wrap_fence(body))
        assert "legacy_unparseable" in report.accuracy_flags

    def test_finding_missing_required_field_yields_legacy_fallback(self):
        body = json.dumps({
            "title": "x",
            "findings": [{"title": "no severity", "summary": "x"}],
        })
        report = parse_report(_wrap_fence(body))
        assert "legacy_unparseable" in report.accuracy_flags
