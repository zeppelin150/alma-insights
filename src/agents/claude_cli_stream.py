"""
Alma Insights -- Claude CLI Stream Parser

Pure parser for ``claude -p --output-format stream-json`` line output.
Translates a single JSON line into either:
  - A ``BridgeEvent`` when the line carries a text delta or final
  - ``None`` for state-only updates (token counts, stop reason) or ignored
    events (system init, rate_limit_event)

State accumulates on the ``StreamParser`` instance: ``full_text``,
``input_tokens``, ``output_tokens``, ``cost_usd``, ``stop_reason``,
``error``, ``error_message``. The bridge reads these after the stream
terminates to construct the final result dict.

Design:
  - No I/O, no subprocess access — pure transformations.
  - Tolerant of malformed JSON (returns None) so the bridge can simply
    keep iterating instead of crashing on stray output.
  - Unit-testable from string fixtures alone.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from src.agents.acp_bridge import BridgeEvent

logger = logging.getLogger("alma.claude_cli_stream")


@dataclass
class StreamResult:
    """Bundle returned by ClaudeCliBridge._consume_stream.

    ``parser`` carries the accumulated state (text, tokens, error, etc).
    ``events`` is the ordered list of content BridgeEvents emitted.
    ``elapsed_ms`` is wall-clock duration. ``early_stopped`` indicates the
    early_stop callback fired and the stream was aborted cleanly.
    """
    parser: "StreamParser"
    events: list[BridgeEvent] = field(default_factory=list)
    elapsed_ms: int = 0
    early_stopped: bool = False


class StreamParser:
    """Incrementally parses claude CLI stream-json output.

    Usage:
        parser = StreamParser(request_id="batch_1")
        for raw_line in subprocess.stdout:
            bev = parser.parse_line(raw_line)
            if bev is not None:
                events.append(bev)
        # After loop: parser.full_text, parser.input_tokens, etc.
    """

    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        self.full_text: str = ""
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.cost_usd: float = 0.0
        self.stop_reason: str = "end_turn"
        self.error: str | None = None
        self.error_message: str = ""

    def parse_line(self, raw_line: str) -> BridgeEvent | None:
        """Parse one JSON line. Returns a BridgeEvent for content deltas;
        None for state-only updates or unrecognized events.
        """
        line = raw_line.strip()
        if not line:
            return None
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            return None

        msg_type = msg.get("type", "")
        if msg_type == "stream_event":
            return self._handle_stream_event(msg.get("event", {}) or {})
        if msg_type == "assistant":
            self._handle_assistant(msg)
            return None
        if msg_type == "result":
            self._handle_result(msg)
            return None
        # 'system', 'rate_limit_event', and unknown types: ignore
        return None

    def _handle_stream_event(self, inner: dict) -> BridgeEvent | None:
        """Handle the inner event of a stream_event wrapper."""
        inner_type = inner.get("type", "")
        if inner_type == "content_block_delta":
            return self._handle_content_delta(inner)
        if inner_type == "message_delta":
            self._handle_message_delta(inner)
        return None

    def _handle_content_delta(self, inner: dict) -> BridgeEvent | None:
        """Extract a text_delta and emit a content BridgeEvent."""
        delta = inner.get("delta", {}) or {}
        if delta.get("type") != "text_delta":
            return None
        text = delta.get("text", "")
        if not text:
            return None
        self.full_text += text
        return BridgeEvent(
            id=self.request_id, type="content", data={"delta": text},
        )

    def _handle_message_delta(self, inner: dict) -> None:
        """Update output_tokens and stop_reason from message_delta."""
        usage = inner.get("usage", {}) or {}
        if usage.get("output_tokens"):
            self.output_tokens = usage["output_tokens"]
        stop = (inner.get("delta", {}) or {}).get("stop_reason")
        if stop:
            self.stop_reason = stop

    def _handle_assistant(self, msg: dict) -> None:
        """Update token counts; backfill full_text if deltas were missed."""
        message = msg.get("message", {}) or {}
        msg_usage = message.get("usage", {}) or {}
        if msg_usage.get("input_tokens"):
            self.input_tokens = msg_usage["input_tokens"]
        if msg_usage.get("output_tokens"):
            self.output_tokens = msg_usage["output_tokens"]
        if not self.full_text:
            for block in message.get("content", []) or []:
                if block.get("type") == "text":
                    self.full_text += block.get("text", "")

    def _handle_result(self, msg: dict) -> None:
        """Extract cost, error status, and final usage from the terminal
        ``result`` event. Splits into three single-purpose helpers to keep
        each under CC=10."""
        self.cost_usd = float(msg.get("total_cost_usd", 0.0) or 0.0)
        self._handle_result_error(msg)
        self._handle_result_usage(msg)
        self._handle_result_text_fallback(msg)

    def _handle_result_error(self, msg: dict) -> None:
        """Set parser error state if the result event indicates failure."""
        if msg.get("subtype") == "error" or msg.get("is_error"):
            self.error = "claude_cli_error"
            self.error_message = (
                msg.get("error", "") or msg.get("result", "") or "unknown"
            )

    def _handle_result_usage(self, msg: dict) -> None:
        """Backfill input/output tokens from result.usage if present."""
        usage = msg.get("usage", {}) or {}
        if usage.get("input_tokens") is not None:
            self.input_tokens = usage["input_tokens"]
        if usage.get("output_tokens") is not None:
            self.output_tokens = usage["output_tokens"]

    def _handle_result_text_fallback(self, msg: dict) -> None:
        """If we missed all text deltas, take the result.result string."""
        if not self.full_text and isinstance(msg.get("result"), str):
            self.full_text = msg["result"]

    def mark_nonzero_exit(self, returncode: int, stderr_tail: str) -> None:
        """Set error state when the subprocess exited non-zero without
        the parser having seen a result-level error first."""
        if self.error is not None:
            return  # don't overwrite an existing parser-level error
        self.error = "claude_cli_nonzero_exit"
        self.error_message = f"exit={returncode}; stderr={stderr_tail}"
