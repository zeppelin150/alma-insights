"""
Tests for GuruWorkbenchPanel helpers and AnalysisWorker logic.

Covers: gap score labels, redline response parsing, prompt assembly,
and data gathering (no Qt widgets required for most tests).
"""

import json
import pytest

from src.ui.widgets.guru_workbench_panel import (
    score_to_gap_label,
    parse_redline_response,
    AnalysisWorker,
)
from src.ui.widgets.guru_card_viewer import GAP_LABELS


# ── Gap Score Labels ─────────────────────────────────────────────

class TestScoreToGapLabel:
    def test_critical_gap(self):
        label, color = score_to_gap_label(0.85)
        assert label == "Critical Gap"

    def test_critical_at_threshold(self):
        label, _ = score_to_gap_label(0.7)
        assert label == "Critical Gap"

    def test_needs_update(self):
        label, _ = score_to_gap_label(0.6)
        assert label == "Needs Update"

    def test_needs_update_at_threshold(self):
        label, _ = score_to_gap_label(0.5)
        assert label == "Needs Update"

    def test_minor_gap(self):
        label, _ = score_to_gap_label(0.4)
        assert label == "Minor Gap"

    def test_minor_at_threshold(self):
        label, _ = score_to_gap_label(0.3)
        assert label == "Minor Gap"

    def test_up_to_date(self):
        label, _ = score_to_gap_label(0.1)
        assert label == "Up to Date"

    def test_zero_score(self):
        label, _ = score_to_gap_label(0.0)
        assert label == "Up to Date"

    def test_returns_color(self):
        """All labels return a valid hex color."""
        for score in (0.0, 0.3, 0.5, 0.7, 1.0):
            _, color = score_to_gap_label(score)
            assert color.startswith("#"), f"Score {score} returned invalid color: {color}"


# ── Redline Response Parsing ─────────────────────────────────────

class TestParseRedlineResponse:
    def test_valid_insert(self):
        data = [{"type": "insert", "html": "<strong>New step</strong>"}]
        result = parse_redline_response(json.dumps(data))
        assert len(result) == 1
        assert result[0]["type"] == "insert"
        assert "New step" in result[0]["html"]

    def test_valid_modify(self):
        data = [{"type": "modify", "title": "Step 5", "old": "old text", "new": "new text"}]
        result = parse_redline_response(json.dumps(data))
        assert len(result) == 1
        assert result[0]["type"] == "modify"
        assert result[0]["old"] == "old text"
        assert result[0]["new"] == "new text"

    def test_valid_warning(self):
        data = [{"type": "warning", "text": "Unconfirmed info"}]
        result = parse_redline_response(json.dumps(data))
        assert len(result) == 1
        assert result[0]["type"] == "warning"

    def test_mixed_types(self):
        data = [
            {"type": "insert", "html": "new content"},
            {"type": "modify", "old": "a", "new": "b"},
            {"type": "warning", "text": "caution"},
        ]
        result = parse_redline_response(json.dumps(data))
        assert len(result) == 3

    def test_markdown_fenced_json(self):
        """Handles ```json ... ``` wrapper from Claude."""
        data = [{"type": "insert", "html": "test"}]
        wrapped = f"```json\n{json.dumps(data)}\n```"
        result = parse_redline_response(wrapped)
        assert len(result) == 1

    def test_invalid_type_filtered(self):
        data = [
            {"type": "insert", "html": "good"},
            {"type": "delete", "html": "bad"},  # invalid type
        ]
        result = parse_redline_response(json.dumps(data))
        assert len(result) == 1
        assert result[0]["type"] == "insert"

    def test_non_dict_items_filtered(self):
        data = [{"type": "insert", "html": "good"}, "not a dict", 42]
        result = parse_redline_response(json.dumps(data))
        assert len(result) == 1

    def test_empty_response(self):
        assert parse_redline_response("") == []

    def test_null_response(self):
        assert parse_redline_response(None) == []

    def test_invalid_json(self):
        assert parse_redline_response("not json at all") == []

    def test_non_array_json(self):
        assert parse_redline_response('{"type": "insert"}') == []

    def test_plain_json_no_fences(self):
        data = [{"type": "warning", "text": "test"}]
        result = parse_redline_response(json.dumps(data))
        assert len(result) == 1


# ── Prompt Assembly ──────────────────────────────────────────────

class TestPromptAssembly:
    """Test the prompt building logic via a mock AnalysisWorker."""

    def _make_worker(self, **kwargs):
        """Create a worker without starting it."""
        defaults = {
            "guru_client": None,
            "db": None,
            "card_ids": ["card-1"],
            "reference_ids": [],
            "trc_codes": ["TRC-001"],
            "context": "",
        }
        defaults.update(kwargs)
        return AnalysisWorker(**defaults)

    def test_basic_prompt_has_card_content(self):
        worker = self._make_worker()
        card = {"title": "Test Card", "content": "Card body here", "collection": "Docs"}
        prompt = worker._build_analysis_prompt(card, [], [])
        assert "Test Card" in prompt
        assert "Card body here" in prompt

    def test_prompt_includes_reference(self):
        worker = self._make_worker()
        card = {"title": "Target", "content": "target body", "collection": "X"}
        refs = [{"title": "Ref Card", "content": "ref body"}]
        prompt = worker._build_analysis_prompt(card, refs, [])
        assert "Ref Card" in prompt
        assert "ref body" in prompt
        assert "Reference Cards" in prompt

    def test_prompt_includes_friction_data(self):
        worker = self._make_worker()
        card = {"title": "Target", "content": "body", "collection": "X"}
        friction = [{"trc": "Billing", "ticket_count": 42,
                      "friction_distribution": {"process": 20, "info": 22}}]
        prompt = worker._build_analysis_prompt(card, [], friction)
        assert "Billing" in prompt
        assert "42 tickets" in prompt

    def test_prompt_includes_context(self):
        worker = self._make_worker(context="Focus on v2.4 changes")
        card = {"title": "Target", "content": "body", "collection": "X"}
        prompt = worker._build_analysis_prompt(card, [], [])
        assert "Focus on v2.4 changes" in prompt
        assert "Additional Context" in prompt

    def test_prompt_output_format(self):
        worker = self._make_worker()
        card = {"title": "T", "content": "c", "collection": "X"}
        prompt = worker._build_analysis_prompt(card, [], [])
        assert "JSON array" in prompt
        assert '"type"' in prompt
