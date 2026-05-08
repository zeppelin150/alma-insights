"""
E2E NLP smoke test against ClaudeCliBridge.

NOT a pytest file — run directly: ``python tests/e2e_claude_nlp.py``

What this exercises end-to-end:
  1. Factory routing: build_bridge_for_task("nlp_classification") returns ClaudeCliBridge
  2. Bridge boot: locate `claude` CLI, mark healthy
  3. Real subprocess spawn with classification prompt
  4. StreamParser consumes claude stream-json output
  5. NDJSON classification output is parseable by worker layer

Synthetic tickets are used so this runs without the warehouse DB.
Caps at 3 tickets to keep cost ~$0.05–0.20.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.gemini.client_factory import build_bridge_for_task
from src.agents.claude_cli_bridge import ClaudeCliBridge

SYNTHETIC_TICKETS = [
    ("E2E-001", "Refund cash pay invoice OR Charge cancellation fee",
     "Patient was charged a $185 cancellation fee for an appointment they "
     "did not schedule. Need a refund processed for the duplicate charge."),
    ("E2E-002", "Client cannot locate EAP benefit information",
     "Client called asking how to access their EAP benefits. They have a "
     "Lyra plan and don't see any sessions left in the portal."),
    ("E2E-003", "Client portal access issue",
     "User reports the patient portal returns a 500 error after login. "
     "Tried two browsers, cleared cache, same result."),
]

CLASSIFICATION_PROMPT = """You are an RCM ticket classifier. For each ticket, output exactly one NDJSON line with these fields:
  ticket_id, trc_code, sentiment (1-5), friction_type, polarity (positive/negative/neutral)

Tickets:
{tickets}

Output one NDJSON line per ticket. NDJSON only, no markdown fences, no preamble."""


def _format_tickets(tickets):
    return "\n".join(
        f"  [{tid}] TRC: {trc} -- {body}"
        for tid, trc, body in tickets
    )


def _verify_factory_returns_claude_bridge():
    print("[step 1] factory routing check")
    bridge = build_bridge_for_task("nlp_classification")
    print(f"  bridge type: {type(bridge).__name__}")
    if not isinstance(bridge, ClaudeCliBridge):
        raise SystemExit(
            f"FAIL: expected ClaudeCliBridge, got {type(bridge).__name__}. "
            "Is override_all=claude set in settings?"
        )
    return bridge


def _run_classification(bridge, prompt, request_id):
    print("[step 2] spawning claude subprocess")
    t0 = time.time()
    result = bridge.call_streaming(prompt, request_id, timeout=120)
    elapsed = time.time() - t0
    return result, elapsed


def _verify_output(result, num_tickets):
    print("[step 3] verifying output shape")
    text = result.get("full_text", "").strip()
    if not text:
        raise SystemExit("FAIL: empty full_text")
    if result.get("error"):
        raise SystemExit(f"FAIL: bridge error: {result['error']} {result.get('message', '')}")
    lines = [line for line in text.splitlines() if line.strip().startswith("{")]
    print(f"  NDJSON-shaped lines parsed: {len(lines)} (expected ~{num_tickets})")
    return lines


def main():
    print("=" * 60)
    print("E2E NLP smoke test (ClaudeCliBridge)")
    print("=" * 60)

    bridge = _verify_factory_returns_claude_bridge()

    prompt = CLASSIFICATION_PROMPT.format(
        tickets=_format_tickets(SYNTHETIC_TICKETS),
    )
    print(f"  prompt length: {len(prompt)} chars")

    result, elapsed = _run_classification(bridge, prompt, "e2e_nlp_smoke")
    print(f"  elapsed: {elapsed:.1f}s")
    print(f"  tokens: in={result['input_tokens']} out={result['output_tokens']}")

    lines = _verify_output(result, len(SYNTHETIC_TICKETS))

    print()
    print("=== Output ===")
    for line in lines:
        print(f"  {line[:140]}")

    print()
    print("=== Verdict ===")
    print(f"  bridge: {type(bridge).__name__}")
    print(f"  classifications: {len(lines)}")
    print(f"  time: {elapsed:.1f}s")
    print(f"  status: {'PASS' if lines else 'FAIL'}")


if __name__ == "__main__":
    main()
