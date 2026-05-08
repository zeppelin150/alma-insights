"""
E2E VOC analysis smoke test against Claude (via ClaudeCliClient).

Uses the real voc_analysis.txt template and a small synthetic ticket batch.
Verifies the full path: factory routing → ClaudeCliClient.generate →
ClaudeCliBridge subprocess → StreamParser → markdown VOC report.

Run: python tests/e2e_claude_voc.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.gemini.client_factory import build_client_for_task
from src.llm.claude_cli_client import ClaudeCliClient

VOC_TEMPLATE = (Path(__file__).resolve().parent.parent
                / "config" / "prompts" / "voc_analysis.txt").read_text(encoding="utf-8")

SYNTHETIC_TICKETS_JSONL = """{"ticket_id":"V-001","trc":"RCM_REFUND","sentiment":2,"text":"Charged $185 cancellation fee for an appointment I never scheduled. Was on hold 45 minutes to dispute. Need refund processed asap."}
{"ticket_id":"V-002","trc":"RCM_REFUND","sentiment":1,"text":"Filed a refund request 6 weeks ago. No response. Called twice. Each time told someone will follow up. Still nothing."}
{"ticket_id":"V-003","trc":"RCM_REFUND","sentiment":2,"text":"Charged twice for same visit. Duplicate $185 line item on EOB. Need second charge reversed."}
{"ticket_id":"V-004","trc":"RCM_REFUND","sentiment":3,"text":"Cancelled within window per policy but still got hit with the fee. Confused about which cancellation deadline applies."}
{"ticket_id":"V-005","trc":"RCM_REFUND","sentiment":2,"text":"Was told over the phone fee would be waived. Two weeks later got the bill anyway. No record of the verbal waiver."}"""

NLP_CONTEXT = """NLP CLASSIFICATION SUMMARY:
- 5 tickets analyzed
- Friction types: billing_error (3), policy_confusion (1), service_failure (1)
- Avg sentiment: 2.0/5
- Polarity: 4 negative, 1 neutral, 0 positive"""

STATS_CONTEXT = """- Volume vs prior 30d: +18%
- Median resolution time: 11 days (vs 4 day target)
- CSAT for this TRC: 2.1/5 (org avg 3.8/5)"""


def _format_prompt():
    return VOC_TEMPLATE.format(
        trc_code="RCM_REFUND",
        trc_label="Refund cash pay invoice OR Charge cancellation fee",
        date_start="2025-04-01",
        date_end="2025-04-15",
        n_tickets=5,
        total_trc_tickets=215,
        sample_pct="2.3",
        nlp_context=NLP_CONTEXT,
        statistical_context=STATS_CONTEXT,
        ticket_jsonl=SYNTHETIC_TICKETS_JSONL,
    )


def _verify_voc_output(text: str) -> dict:
    """Check the markdown output has the expected section headings."""
    expected_sections = [
        "FRICTION THEMES",
        "SENTIMENT ANALYSIS",
        "ANOMALIES",
        "ROOT CAUSE SUMMARY",
    ]
    found = {s: s in text.upper() for s in expected_sections}
    return found


def main():
    print("=" * 60)
    print("E2E VOC analysis smoke test (Claude CLI)")
    print("=" * 60)

    print("[step 1] factory routing check")
    client = build_client_for_task("voc_analysis")
    print(f"  client type: {type(client).__name__}")
    if not isinstance(client, ClaudeCliClient):
        raise SystemExit(
            f"FAIL: expected ClaudeCliClient, got {type(client).__name__}. "
            "Is override_all=claude set?"
        )

    print("[step 2] formatting VOC prompt from real template")
    prompt = _format_prompt()
    print(f"  prompt length: {len(prompt)} chars")

    print("[step 3] invoking client.generate()")
    t0 = time.time()
    try:
        text = client.generate(prompt, timeout=180)
    except Exception as e:
        raise SystemExit(f"FAIL: client.generate raised: {e}")
    elapsed = time.time() - t0
    print(f"  elapsed: {elapsed:.1f}s")
    print(f"  response length: {len(text)} chars")

    print("[step 4] verifying section structure")
    sections = _verify_voc_output(text)
    for name, present in sections.items():
        flag = "OK" if present else "MISSING"
        print(f"  [{flag}] {name}")

    print()
    print("=== Output preview (first 1200 chars) ===")
    print(text[:1200])
    print("..." if len(text) > 1200 else "")

    print()
    print("=== Verdict ===")
    all_present = all(sections.values())
    print(f"  client: {type(client).__name__}")
    print(f"  sections found: {sum(sections.values())}/{len(sections)}")
    print(f"  time: {elapsed:.1f}s")
    print(f"  status: {'PASS' if all_present else 'PARTIAL'}")


if __name__ == "__main__":
    main()
