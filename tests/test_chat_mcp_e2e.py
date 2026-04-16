"""
E2E test: Full MCP chat round-trip with Thunderbird Insurance query.

Tests the exact flow that happens in the UI:
1. User asks about Thunderbird Insurance
2. Model calls query_entities → gets 21 ticket IDs
3. Model calls query_ticket_classifications(ticket_ids=[...]) → scoped results
4. Model responds with correct Thunderbird-specific answer

Usage:
    python tests/test_chat_mcp_e2e.py
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-25s %(levelname)s %(message)s",
)
logger = logging.getLogger("test_e2e")
logging.getLogger("alma.acp_bridge").setLevel(logging.INFO)

DB_PATH = str(ROOT / "data" / "local_warehouse.db")


def run_test():
    from src.agents.acp_bridge import ACPBridge

    mcp_config = [
        {
            "name": "alma-chat-tools",
            "command": sys.executable,
            "args": ["-m", "src.mcp.chat_mcp_server"],
            "env": [{"name": "ALMA_DB_PATH", "value": DB_PATH}],
        }
    ]

    bridge = ACPBridge(model="gemini-2.5-flash-lite")
    bridge.set_mcp_config(mcp_config)

    try:
        bridge.ensure_running()
        time.sleep(3)

        system_prompt = (
            "You are a data analyst assistant with tools to query a ticket database.\n"
            "IMPORTANT WORKFLOW for entity-specific questions:\n"
            "1. First call query_entities to get the ticket IDs for that entity\n"
            "2. Then call query_ticket_classifications WITH those ticket_ids to get scoped results\n"
            "3. Never report classification counts without scoping to the entity's tickets\n"
            "Be concise. Report the actual ticket IDs from the tool results."
        )
        user_query = "What is the most common issue with Thunderbird Insurance? List the ticket IDs."

        prompt = (
            f"[SYSTEM INSTRUCTIONS]\n{system_prompt}\n"
            f"[END SYSTEM INSTRUCTIONS]\n\n{user_query}"
        )

        tool_calls = []
        content_chunks = []

        def on_token(event):
            if event.type == "tool_call":
                tool_calls.append(event.data)
                logger.info("TOOL CALL: %s args=%s",
                            event.data.get("name", "?"),
                            json.dumps(event.data.get("args", {}))[:200])
            elif event.type == "content":
                delta = event.data.get("delta", "")
                if delta.strip():
                    content_chunks.append(delta)

        logger.info("Sending query: %s", user_query)
        t0 = time.time()
        result = bridge.call_streaming(
            prompt, "e2e_test_1",
            on_token=on_token, timeout=90,
        )
        elapsed = time.time() - t0

        full_text = result.get("full_text", "")
        error = result.get("error")

        print("\n" + "=" * 70)
        print("E2E TEST RESULTS")
        print("=" * 70)
        print(f"Time: {elapsed:.1f}s")
        print(f"Error: {error}")
        print(f"Tool calls: {len(tool_calls)}")
        for i, tc in enumerate(tool_calls):
            name = tc.get("name", "?")
            args = tc.get("args", {})
            has_ticket_ids = "ticket_ids" in args
            print(f"  [{i+1}] {name} (has ticket_ids={has_ticket_ids})")
            if has_ticket_ids:
                ids = args["ticket_ids"]
                print(f"       ticket_ids: {len(ids)} IDs — {ids[:5]}...")

        print(f"\nResponse ({len(full_text)} chars):")
        print(full_text[:800])
        print("=" * 70)

        # Validate
        print("\n--- VALIDATION ---")

        # Known correct Thunderbird IDs
        correct_ids = {'10278', '10323', '10362', '10399', '10454', '10477',
                       '10482', '10506', '10515', '10545', '10654', '10662',
                       '10700', '10721', '10751', '10787', '10830', '10832',
                       '10842', '10853', '10883'}

        # Check if any correct IDs appear in response
        found_correct = [tid for tid in correct_ids if tid in full_text]
        found_wrong = []
        # Check for known wrong IDs (from the global results)
        wrong_ids = {'10856', '10834', '10809'}
        found_wrong = [tid for tid in wrong_ids if tid in full_text]

        print(f"Correct Thunderbird IDs in response: {len(found_correct)}/{len(correct_ids)}")
        print(f"Wrong IDs in response: {found_wrong or 'NONE (good!)'}")
        print(f"Mentions 'EAP' or 'benefit': {'YES' if 'eap' in full_text.lower() or 'benefit' in full_text.lower() else 'NO'}")
        print(f"Mentions '47' (wrong global count): {'YES (BAD)' if '47' in full_text else 'NO (good)'}")

        # Check tool chain
        tool_names = [tc.get("name", "") for tc in tool_calls]
        has_scoped_classification = any(
            tc.get("name") == "query_ticket_classifications" and "ticket_ids" in tc.get("args", {})
            for tc in tool_calls
        )
        print(f"Tool chain: {' → '.join(tool_names) or '(none)'}")
        print(f"Classifications scoped to ticket_ids: {'YES' if has_scoped_classification else 'NO'}")

        if found_correct and not found_wrong and has_scoped_classification:
            print("\n✓ PASS — Correct entity-scoped results")
        elif found_correct and not found_wrong:
            print("\n~ PARTIAL — Correct IDs but tool chain not ideal")
        else:
            print("\n✗ FAIL — Wrong or missing data")

    except Exception as e:
        logger.exception("Test failed: %s", e)
    finally:
        bridge.shutdown()
        time.sleep(2)


if __name__ == "__main__":
    run_test()
