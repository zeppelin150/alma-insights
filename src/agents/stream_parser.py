"""
Alma Insights -- Stream Parser (Pass 5.0)

Parses the LLM's streaming text output for structured events encoded
as fenced code blocks. Workers instruct Gemini to output classifications
and tool calls inside fenced blocks; this parser extracts them from the
interleaved text stream.

Protocol (output by the LLM, parsed by us):
    ```tool_call
    {"tool": "query_taxonomy", "args": {"trc": "RCM_02"}}
    ```

    ```classification
    {"ticket_id": "T-1234", "friction_type": "billing_confusion", ...}
    ```

    ```batch_complete
    {"classified": 50, "skipped": 0, "flagged": 2}
    ```

Plain text between fences is collected as TEXT events.

The parser handles:
- Partial chunk feeds (LLM may split a fence across multiple content deltas)
- Nested backticks inside JSON (rare but possible in ticket text)
- Malformed JSON (returns ERROR event with raw content)
- Multiple fenced blocks in a single chunk
"""

import json
import logging
from enum import Enum

logger = logging.getLogger("alma.stream_parser")


class StreamEvent(Enum):
    """Event types extracted from the LLM output stream."""
    TEXT = "text"                        # Plain text between fenced blocks
    TOOL_CALL = "tool_call"             # Tool invocation request
    CLASSIFICATION = "classification"   # Single ticket classification
    BATCH_COMPLETE = "batch_complete"   # End-of-batch summary
    ERROR = "error"                     # Malformed block or parse failure


class ParsedEvent:
    """A single parsed event from the stream."""

    __slots__ = ("event_type", "data", "raw")

    def __init__(self, event_type, data=None, raw=""):
        self.event_type = event_type  # StreamEvent enum
        self.data = data              # Parsed dict (for structured events)
        self.raw = raw                # Raw text content

    def __repr__(self):
        if self.event_type == StreamEvent.TEXT:
            preview = self.raw[:50] + "..." if len(self.raw) > 50 else self.raw
            return f"ParsedEvent(TEXT, {preview!r})"
        return f"ParsedEvent({self.event_type.name}, keys={list(self.data.keys()) if self.data else None})"


# ── Fence type to StreamEvent mapping ──
FENCE_MAP = {
    "tool_call": StreamEvent.TOOL_CALL,
    "classification": StreamEvent.CLASSIFICATION,
    "batch_complete": StreamEvent.BATCH_COMPLETE,
}


class StreamParser:
    """
    Stateful parser for fenced code block events in streaming text.

    Feed chunks of text as they arrive from the LLM. The parser
    maintains internal buffer state across feeds and yields events
    as complete blocks are recognized.

    Usage:
        parser = StreamParser()
        for chunk in streaming_response:
            for event in parser.feed(chunk):
                if event.event_type == StreamEvent.CLASSIFICATION:
                    store_classification(event.data)
                elif event.event_type == StreamEvent.TOOL_CALL:
                    result = execute_tool(event.data)
    """

    def __init__(self):
        self.reset()

    def reset(self):
        """Reset parser state for a new batch."""
        self._buffer = ""
        self._in_fence = False
        self._fence_type = None     # "tool_call", "classification", etc.
        self._fence_content = ""
        self._text_accumulator = ""

    def feed(self, chunk):
        """
        Feed a text chunk and yield parsed events.

        Args:
            chunk: String of text from the LLM output stream.

        Yields:
            ParsedEvent objects as complete events are recognized.
        """
        self._buffer += chunk

        while self._buffer:
            if self._in_fence:
                # Look for closing fence
                close_idx = self._buffer.find("```")
                if close_idx == -1:
                    # No closing fence yet -- accumulate and wait
                    self._fence_content += self._buffer
                    self._buffer = ""
                    break

                # Found closing fence
                self._fence_content += self._buffer[:close_idx]
                self._buffer = self._buffer[close_idx + 3:]

                # Strip trailing newline from fence content
                content = self._fence_content.strip()
                self._in_fence = False

                # Parse the fenced content
                event_type = FENCE_MAP.get(self._fence_type)
                if event_type and content:
                    yield from self._parse_fenced_block(
                        event_type, content, self._fence_type
                    )
                elif content:
                    # Unknown fence type -- emit as text
                    yield ParsedEvent(
                        StreamEvent.TEXT,
                        raw=f"```{self._fence_type}\n{content}\n```"
                    )

                self._fence_type = None
                self._fence_content = ""

            else:
                # Look for opening fence
                fence_idx = self._buffer.find("```")
                if fence_idx == -1:
                    # No fence -- accumulate text
                    self._text_accumulator += self._buffer
                    self._buffer = ""

                    # Flush text if it's substantial
                    if len(self._text_accumulator) > 200:
                        yield from self._flush_text()
                    break

                # Text before the fence
                if fence_idx > 0:
                    self._text_accumulator += self._buffer[:fence_idx]

                # Flush any accumulated text
                yield from self._flush_text()

                # Parse fence header
                after_fence = self._buffer[fence_idx + 3:]

                # Find the end of the fence header line
                newline_idx = after_fence.find("\n")
                if newline_idx == -1:
                    # Incomplete header -- wait for more data
                    # But keep the ``` in the buffer
                    self._buffer = self._buffer[fence_idx:]
                    break

                # Extract fence type (e.g., "tool_call", "classification")
                self._fence_type = after_fence[:newline_idx].strip().lower()
                self._buffer = after_fence[newline_idx + 1:]
                self._in_fence = True
                self._fence_content = ""

    def flush(self):
        """
        Flush any remaining buffered content as events.
        Call this when the stream ends to get any trailing text.

        Yields:
            ParsedEvent objects.
        """
        # If we're inside an unclosed fence, treat it as text
        if self._in_fence and self._fence_content:
            self._text_accumulator += (
                f"```{self._fence_type}\n{self._fence_content}"
            )
            self._in_fence = False
            self._fence_type = None
            self._fence_content = ""

        if self._buffer:
            self._text_accumulator += self._buffer
            self._buffer = ""

        yield from self._flush_text()

    def _flush_text(self):
        """Yield accumulated text as a TEXT event, if any."""
        if self._text_accumulator.strip():
            yield ParsedEvent(
                StreamEvent.TEXT,
                raw=self._text_accumulator
            )
        self._text_accumulator = ""

    def _parse_fenced_block(self, event_type, content, fence_label):
        """
        Parse a fenced block's content as JSON.

        For CLASSIFICATION events, the content may contain multiple
        JSON objects (one per line) for batch classifications.

        Yields:
            ParsedEvent objects.
        """
        # Try single JSON object first
        try:
            data = json.loads(content)
            yield ParsedEvent(event_type, data=data, raw=content)
            return
        except json.JSONDecodeError:
            pass

        # Try JSON array
        try:
            items = json.loads(f"[{content}]")
            if isinstance(items, list) and items:
                for item in items:
                    if isinstance(item, dict):
                        yield ParsedEvent(event_type, data=item, raw=content)
                return
        except json.JSONDecodeError:
            pass

        # Try line-by-line JSONL (one JSON object per line)
        lines = [l.strip() for l in content.split("\n") if l.strip()]
        parsed_any = False
        for line in lines:
            # Skip lines that look like separators or comments
            if line.startswith("//") or line.startswith("#") or line == "---":
                continue
            try:
                data = json.loads(line)
                if isinstance(data, dict):
                    yield ParsedEvent(event_type, data=data, raw=line)
                    parsed_any = True
            except json.JSONDecodeError:
                continue

        if parsed_any:
            return

        # Nothing parsed -- emit ERROR with raw content
        logger.warning(
            f"StreamParser: failed to parse {fence_label} block "
            f"({len(content)} chars): {content[:200]}"
        )
        yield ParsedEvent(
            StreamEvent.ERROR,
            data={"fence_type": fence_label, "parse_error": "invalid_json"},
            raw=content,
        )

    @property
    def is_in_fence(self):
        """True if parser is currently inside a fenced block."""
        return self._in_fence

    @property
    def pending_fence_type(self):
        """The fence type currently being parsed, or None."""
        return self._fence_type

    def __repr__(self):
        state = "in_fence" if self._in_fence else "text"
        return (
            f"StreamParser(state={state}, "
            f"fence_type={self._fence_type}, "
            f"buffer_len={len(self._buffer)})"
        )
