"""
Test: Analyst-quality response from MCP chat.

Verifies the model produces analytical output (patterns, root causes,
ticket citations) rather than just raw counts.
"""

import json
import os
import sys
import time
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)-20s %(levelname)s %(message)s")
logging.getLogger("alma.acp_bridge").setLevel(logging.WARNING)

DB_PATH = str(ROOT / "data" / "local_warehouse.db")


def run():
    from src.agents.acp_bridge import ACPBridge

    bridge = ACPBridge(model="gemini-2.5-flash-lite")
    bridge.set_mcp_config([{
        "name": "alma-chat-tools",
        "command": sys.executable,
        "args": ["-m", "src.mcp.chat_mcp_server"],
        "env": [{"name": "ALMA_DB_PATH", "value": DB_PATH}],
    }])

    try:
        bridge.ensure_running()
        time.sleep(3)

        system_prompt = (
            "You are an expert RCM (Revenue Cycle Management) data analyst. "
            "You have tools to query a support ticket database.\n\n"
            "When analyzing an entity (payer, product area, feature):\n"
            "1. Use query_entities to get tickets + classification breakdowns\n"
            "2. Report the TRC breakdown with counts\n"
            "3. Look for PATTERNS in the issue_snippets — group tickets with similar root causes\n"
            "4. Call out systemic issues (same error appearing in multiple tickets)\n"
            "5. Cite specific ticket IDs as evidence\n\n"
            "Be analytical, not just descriptive. Identify root causes, not just categories."
        )

        prompt = (
            f"[SYSTEM INSTRUCTIONS]\n{system_prompt}\n[END SYSTEM INSTRUCTIONS]\n\n"
            f"Analyze the issues with Thunderbird Insurance. "
            f"What are the main problem areas and are there any systemic patterns?"
        )

        t0 = time.time()
        result = bridge.call_streaming(prompt, "analysis_1", timeout=60)
        elapsed = time.time() - t0

        text = result.get("full_text", "")
        print(f"\n{'='*70}")
        print(f"ANALYSIS RESPONSE ({elapsed:.1f}s, {len(text)} chars)")
        print(f"{'='*70}")
        print(text)
        print(f"{'='*70}")

        # Quality checks
        print("\n--- QUALITY CHECKS ---")
        checks = {
            "Mentions EAP": "eap" in text.lower(),
            "Mentions member ID or mismatch": "member id" in text.lower() or "mismatch" in text.lower(),
            "Cites ticket IDs": any(f"10{x}" in text for x in ["278", "362", "787", "399", "482"]),
            "Has TRC breakdown": "Client cannot locate" in text or "EAP" in text,
            "No wrong '47' count": "47" not in text,
            "Identifies pattern/systemic": "pattern" in text.lower() or "systemic" in text.lower() or "recurring" in text.lower() or "common" in text.lower(),
        }
        for check, passed in checks.items():
            print(f"  {'✓' if passed else '✗'} {check}")

        score = sum(checks.values())
        print(f"\nScore: {score}/{len(checks)}")

    except Exception as e:
        print(f"ERROR: {e}")
    finally:
        bridge.shutdown()
        time.sleep(2)


if __name__ == "__main__":
    run()
