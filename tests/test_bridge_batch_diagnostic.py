"""
Diagnostic test: send a real batch classification prompt through the bridge
and trace every step of the pipeline.

Validates:
1. Bridge boots and streams correctly
2. Model returns fenced tool_call blocks
3. StreamParser extracts them
4. Classified IDs are populated

Run: python -m pytest tests/test_bridge_batch_diagnostic.py -xvs
"""

import json
import time
import logging
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def bridge():
    """Boot a single bridge for the test module."""
    from src.agents.acp_bridge import ACPBridge
    b = ACPBridge(model="gemini-2.5-flash")
    b.ensure_running()
    assert b.is_alive(), "Bridge failed to boot"
    yield b
    b.shutdown()


@pytest.fixture
def parser():
    from src.agents.stream_parser import StreamParser
    return StreamParser()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINI_BATCH_PROMPT = """You are classifying support tickets for an RCM (Revenue Cycle Management) healthcare platform. All tickets in this batch belong to the same Ticket Reason Code (TRC):

TRC: Provider payout rate dissatisfaction

MANIFEST:
- Tickets in this batch: 3
- Comments: 0
- Date range: 2025-01-01 to 2025-03-12
- Chunk: 1 of 1

TICKETS:
{"ticket_id": "T-001", "subject": "Payout is less than contracted rate", "body": "We were told our rate would be $150 per visit but only received $120."}
{"ticket_id": "T-002", "subject": "Missing payment for January claims", "body": "Our January claims show as processed but we have not received payment yet."}
{"ticket_id": "T-003", "subject": "Rate change without notification", "body": "Our payout rate was reduced from $200 to $175 and nobody told us."}

For EACH ticket, call the store_classification tool using a fenced code block. Output one block per ticket, with NO text before, between, or after:

```tool_call
{{"tool": "store_classification", "args": {{
  "ticket_id": "<ticket_id>",
  "sub_cluster": "<behavioral sub-pattern, <8 words>",
  "sub_cluster_confidence": <0.0-1.0>,
  "is_novel": false,
  "novel_justification": null,
  "sentiment_intensity": <1-5>,
  "sentiment_polarity": "<positive|negative|mixed|neutral>",
  "friction_type": "<one of: incorrect_charge, missing_information, policy_confusion, communication_gap, other>",
  "anomaly_flag": "normal",
  "anomaly_reason": null,
  "entities": {{"payer": null, "product_area": null, "feature": null}},
  "key_phrases": ["<phrase1>", "<phrase2>"],
  "root_cause_hint": "<one sentence hypothesis>",
  "summary": "<1-2 sentence de-identified summary>"
}}}}
```

You MUST classify ALL 3 tickets. Output exactly 3 tool_call blocks.
No JSON arrays. No preamble. No explanation. No text between blocks.
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBridgeBatchDiagnostic:
    """End-to-end batch classification diagnostic."""

    def test_01_bridge_alive(self, bridge):
        """Bridge booted and responds to ping."""
        status = bridge.ping(timeout=10)
        assert status is not None, "Bridge did not respond to ping"
        logger.info("Bridge ping: %s", json.dumps(status, indent=2))
        assert status.get("status") == "alive"

    def test_02_raw_streaming_response(self, bridge):
        """Send mini-batch prompt, collect raw streaming events."""
        request_id = f"diag_{int(time.time())}"
        events_log = []
        content_chunks = []

        def on_token(event):
            events_log.append({
                "type": event.type,
                "data_keys": list(event.data.keys()),
                "delta_preview": str(event.data.get("delta", ""))[:100],
            })
            if event.type == "content":
                content_chunks.append(event.data.get("delta", ""))

        result = bridge.call_streaming(
            MINI_BATCH_PROMPT, request_id,
            on_token=on_token,
            timeout=120,
        )

        # Log results
        logger.info("=== STREAMING EVENTS (%d total) ===", len(events_log))
        for i, evt in enumerate(events_log[:20]):
            logger.info("  Event %d: type=%s keys=%s delta=%.80s",
                        i, evt["type"], evt["data_keys"], evt["delta_preview"])

        logger.info("=== RESULT ===")
        logger.info("  error: %s", result.get("error"))
        logger.info("  message: %s", result.get("message", ""))
        logger.info("  elapsed_ms: %s", result.get("elapsed_ms"))
        logger.info("  turns: %s", result.get("turns"))
        logger.info("  input_tokens: %s", result.get("input_tokens"))
        logger.info("  output_tokens: %s", result.get("output_tokens"))

        full_text = result.get("full_text", "")
        logger.info("  full_text length: %d", len(full_text))
        logger.info("  full_text preview (first 500):\n%s", full_text[:500])
        logger.info("  full_text tail (last 200):\n%s", full_text[-200:] if len(full_text) > 200 else full_text)

        # Assertions
        assert result.get("error") is None, (
            f"Bridge returned error: {result.get('error')} — {result.get('message', '')}"
        )
        assert len(full_text) > 50, (
            f"Response too short ({len(full_text)} chars), model may not be generating"
        )

        # Check if response contains tool_call fences
        has_fences = "```tool_call" in full_text
        has_json = '"ticket_id"' in full_text
        logger.info("  has ```tool_call fences: %s", has_fences)
        logger.info("  has ticket_id in JSON: %s", has_json)

        assert has_fences or has_json, (
            f"Response contains neither tool_call fences nor ticket_id JSON. "
            f"Model output may be in unexpected format. Preview: {full_text[:300]}"
        )

    def test_03_stream_parser_extracts(self, bridge, parser):
        """Send prompt, feed streaming deltas to parser, check extraction."""
        request_id = f"parse_{int(time.time())}"
        parsed_events = []

        def on_token(event):
            if event.type == "content":
                delta = event.data.get("delta", "")
                for parsed in parser.feed(delta):
                    parsed_events.append(parsed)

        result = bridge.call_streaming(
            MINI_BATCH_PROMPT, request_id,
            on_token=on_token,
            timeout=120,
        )

        # Flush remaining
        for parsed in parser.flush():
            parsed_events.append(parsed)

        logger.info("=== PARSED EVENTS (%d total) ===", len(parsed_events))
        classified_ids = set()
        for i, evt in enumerate(parsed_events):
            logger.info("  Parsed %d: type=%s", i, evt.get("type", "?"))
            if evt.get("type") == "TOOL_CALL":
                args = evt.get("args", {})
                tid = args.get("ticket_id", "?")
                classified_ids.add(tid)
                logger.info("    ticket_id=%s sub_cluster=%s",
                            tid, args.get("sub_cluster", "?")[:50])
            elif evt.get("type") == "CLASSIFICATION":
                data = evt.get("data", {})
                tid = data.get("ticket_id", "?")
                classified_ids.add(tid)
                logger.info("    ticket_id=%s", tid)
            elif evt.get("type") == "ERROR":
                logger.warning("    Parser ERROR: %s", evt.get("message", "?"))

        logger.info("=== CLASSIFIED IDS: %s ===", classified_ids)

        # If parser found nothing, log the full text for diagnosis
        if not classified_ids:
            full_text = result.get("full_text", "")
            logger.error(
                "PARSER FOUND NOTHING. Full response (%d chars):\n%s",
                len(full_text), full_text[:2000]
            )

        assert len(classified_ids) >= 1, (
            f"Parser extracted 0 classifications from {len(parsed_events)} events. "
            f"Check logs for full response text."
        )

    def test_04_error_classification(self, bridge):
        """Send a prompt that triggers an error, verify it's classified properly."""
        request_id = f"err_{int(time.time())}"

        # Send empty prompt — should get a response (possibly short) not an unknown error
        result = bridge.call_streaming(
            "Say exactly: test", request_id,
            timeout=30,
        )

        logger.info("Error test result: error=%s message=%s full_text=%s",
                     result.get("error"), result.get("message", ""),
                     result.get("full_text", "")[:100])

        # This should succeed, not return "unknown"
        if result.get("error"):
            assert result["error"] != "unknown", (
                f"Got unclassified 'unknown' error: {result.get('message', '')}"
            )
