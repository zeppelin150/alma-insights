"""
E2E Chat smoke test against Claude (via ClaudeCliClient).

Exercises the same build-per-message path that ChatEngine uses (default
task_type=report_generation) without spinning up Qt machinery.

Sends a multi-turn conversation with a system prompt + ticket-context block,
verifies the response is non-empty, on-topic, and within timeout.

Run: python tests/e2e_claude_chat.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.gemini.client_factory import build_client_for_task
from src.llm.claude_cli_client import ClaudeCliClient

SYSTEM_PROMPT = """You are an Alma Insights assistant. The user is an RCM operations analyst asking questions about their support ticket data.

Answer using only the context provided. Be concise (under 200 words). If the context is insufficient, say so plainly rather than guessing."""

CONTEXT_BLOCK = """RECENT TICKET DATA (last 30 days):
- TRC: Refund cash pay invoice OR Charge cancellation fee — 215 tickets, +18% vs prior 30d, avg sentiment 2.1/5
- TRC: Client cannot locate EAP benefit information — 184 tickets, -3% vs prior 30d, avg sentiment 3.0/5
- TRC: Client portal access issue — 135 tickets, +6% vs prior 30d, avg sentiment 2.4/5

Top friction themes for refund TRC:
- Erroneous charges for unscheduled appointments (~60% of tickets)
- Long resolution time (median 11 days vs 4-day target)
- Verbal waivers not honored when bills arrive

CSAT for refund TRC: 2.1/5 (org avg 3.8/5)."""

USER_TURN_1 = "Which TRC saw the biggest week-over-week increase, and what's driving it?"


def _format_chat_prompt(context: str, user: str) -> str:
    """Mirror ChatEngine's USER-side stitching (context + packed turn).

    The system prompt is deliberately NOT embedded here: since the 2026-07-21
    fix, ChatEngine hands it to client.generate(system_prompt=...) and the CLI
    path delivers it via --system-prompt-file. Embedding it as an
    [SYSTEM INSTRUCTIONS] block in user content reproduces the retired
    injection-shaped packaging that made Sonnet refuse the persona."""
    return (
        f"[CONTEXT]\n{context}\n[END CONTEXT]\n\n"
        f"User: {user}"
    )


def _verify_response(text: str, user_msg: str) -> dict:
    """Sanity checks: non-empty, addresses the question, within length budget."""
    text_l = text.lower()
    return {
        "non_empty": bool(text.strip()),
        "mentions_refund_or_trc": any(
            term in text_l for term in ("refund", "trc", "cancellation", "18%")
        ),
        "reasonable_length": 50 <= len(text) <= 4000,
    }


def main():
    print("=" * 60)
    print("E2E Chat smoke test (Claude CLI)")
    print("=" * 60)

    print("[step 1] factory routing check (chat uses task_type=report_generation)")
    client = build_client_for_task("report_generation")
    print(f"  client type: {type(client).__name__}")
    if not isinstance(client, ClaudeCliClient):
        raise SystemExit(
            f"FAIL: expected ClaudeCliClient, got {type(client).__name__}. "
            "Is override_all=claude set?"
        )

    print("[step 2] formatting chat prompt (system prompt travels separately)")
    prompt = _format_chat_prompt(CONTEXT_BLOCK, USER_TURN_1)
    print(f"  prompt length: {len(prompt)} chars")
    print(f"  user message: {USER_TURN_1!r}")

    print("[step 3] invoking client.generate() — 1st turn")
    t0 = time.time()
    try:
        response = client.generate(prompt, system_prompt=SYSTEM_PROMPT, timeout=90)
    except Exception as e:
        raise SystemExit(f"FAIL: client.generate raised: {e}")
    elapsed = time.time() - t0
    print(f"  elapsed: {elapsed:.1f}s")
    print(f"  response length: {len(response)} chars")

    print("[step 4] sanity checking response")
    checks = _verify_response(response, USER_TURN_1)
    for name, ok in checks.items():
        flag = "OK" if ok else "FAIL"
        print(f"  [{flag}] {name}")

    print()
    print("=== Response ===")
    print(response)

    print()
    print("=== Verdict ===")
    all_ok = all(checks.values())
    print(f"  client: {type(client).__name__}")
    print(f"  checks passed: {sum(checks.values())}/{len(checks)}")
    print(f"  time: {elapsed:.1f}s")
    print(f"  status: {'PASS' if all_ok else 'PARTIAL'}")


if __name__ == "__main__":
    main()
