"""
Tests for ``src.agents.claude_cli_stream.StreamParser``.

Pure-parser tests: feed real captured CLI output (in tests/fixtures/claude_cli/)
through ``parse_line`` and assert the resulting state matches expected
values. If Claude Code's stream-json shape changes, these tests fail loudly
and we know to update the parser.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.acp_bridge import BridgeEvent
from src.agents.claude_cli_stream import StreamParser

FIXTURES = Path(__file__).parent / "fixtures" / "claude_cli"


def _drive(fixture_name: str, request_id: str = "test_req"):
    """Feed every line of a fixture into a fresh parser, return parser + events."""
    parser = StreamParser(request_id)
    events: list[BridgeEvent] = []
    path = FIXTURES / fixture_name
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        bev = parser.parse_line(raw_line)
        if bev is not None:
            events.append(bev)
    return parser, events


# ─── Fixture-driven tests ───────────────────────────────────────


class TestSimpleOk:
    """Short OK response — verify text + tokens + no error."""

    def test_full_text(self):
        parser, _events = _drive("simple_ok.jsonl")
        assert parser.full_text.strip() == "OK"

    def test_no_error(self):
        parser, _ = _drive("simple_ok.jsonl")
        assert parser.error is None
        assert parser.error_message == ""

    def test_token_counts(self):
        parser, _ = _drive("simple_ok.jsonl")
        assert parser.input_tokens > 0
        assert parser.output_tokens > 0

    def test_at_least_one_content_event(self):
        _parser, events = _drive("simple_ok.jsonl")
        content_events = [e for e in events if e.type == "content"]
        assert len(content_events) >= 1
        assert all(isinstance(e, BridgeEvent) for e in events)
        assert all(e.id == "test_req" for e in events)

    def test_stop_reason_set(self):
        parser, _ = _drive("simple_ok.jsonl")
        assert parser.stop_reason in ("end_turn", "stop_sequence")

    def test_cost_recorded(self):
        parser, _ = _drive("simple_ok.jsonl")
        assert parser.cost_usd >= 0.0


class TestNDJSONClassification:
    """Multi-line NDJSON classification — verify text contains JSON objects."""

    def test_full_text_has_json(self):
        parser, _ = _drive("classification_ndjson.jsonl")
        assert parser.full_text  # non-empty
        # Either contains {"ticket_id" ...} or has actual classifications
        assert "ticket_id" in parser.full_text or "claim" in parser.full_text.lower()

    def test_no_error(self):
        parser, _ = _drive("classification_ndjson.jsonl")
        assert parser.error is None

    def test_multiple_content_events(self):
        # Multi-token responses produce multiple content_block_delta events
        _parser, events = _drive("classification_ndjson.jsonl")
        content_events = [e for e in events if e.type == "content"]
        assert len(content_events) >= 2


# ─── Synthetic edge-case tests ──────────────────────────────────


class TestParserRobustness:
    """Synthetic inputs covering edge cases not easily captured live."""

    def test_blank_line_returns_none(self):
        p = StreamParser("r1")
        assert p.parse_line("") is None
        assert p.parse_line("   ") is None

    def test_malformed_json_returns_none(self):
        p = StreamParser("r1")
        assert p.parse_line("{not valid json") is None
        # State unchanged
        assert p.full_text == ""

    def test_unknown_event_type_ignored(self):
        p = StreamParser("r1")
        msg = json.dumps({"type": "totally_made_up", "payload": {"x": 1}})
        assert p.parse_line(msg) is None
        assert p.full_text == ""

    def test_content_block_delta_emits_event(self):
        p = StreamParser("r1")
        msg = json.dumps({
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "hello"},
            },
        })
        bev = p.parse_line(msg)
        assert bev is not None
        assert bev.type == "content"
        assert bev.data["delta"] == "hello"
        assert p.full_text == "hello"

    def test_message_delta_updates_stop_reason(self):
        p = StreamParser("r1")
        msg = json.dumps({
            "type": "stream_event",
            "event": {
                "type": "message_delta",
                "delta": {"stop_reason": "max_tokens"},
                "usage": {"output_tokens": 42},
            },
        })
        assert p.parse_line(msg) is None  # state-only, no event
        assert p.stop_reason == "max_tokens"
        assert p.output_tokens == 42

    def test_result_error_sets_parser_error(self):
        p = StreamParser("r1")
        msg = json.dumps({
            "type": "result", "subtype": "error",
            "is_error": True, "error": "rate_limit_exceeded",
            "total_cost_usd": 0.0,
        })
        p.parse_line(msg)
        assert p.error == "claude_cli_error"
        assert "rate_limit" in p.error_message

    def test_result_text_fallback_when_no_deltas(self):
        p = StreamParser("r1")
        # Skip the deltas — go straight to result
        msg = json.dumps({
            "type": "result", "subtype": "success",
            "result": "fallback text", "total_cost_usd": 0.001,
        })
        p.parse_line(msg)
        assert p.full_text == "fallback text"

    def test_result_text_does_not_overwrite_existing(self):
        p = StreamParser("r1")
        p.full_text = "from deltas"
        msg = json.dumps({
            "type": "result", "subtype": "success",
            "result": "different", "total_cost_usd": 0.0,
        })
        p.parse_line(msg)
        assert p.full_text == "from deltas"

    def test_mark_nonzero_exit_does_not_overwrite_parser_error(self):
        p = StreamParser("r1")
        p.error = "claude_cli_error"
        p.error_message = "specific error"
        p.mark_nonzero_exit(1, "stderr blah")
        assert p.error == "claude_cli_error"
        assert p.error_message == "specific error"

    def test_mark_nonzero_exit_sets_error_when_none(self):
        p = StreamParser("r1")
        p.mark_nonzero_exit(2, "auth failed")
        assert p.error == "claude_cli_nonzero_exit"
        assert "exit=2" in p.error_message
        assert "auth failed" in p.error_message
