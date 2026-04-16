"""
Test: Structured tool calling for Gemini Chat via ACP + MCP.

Boots an ACP bridge with a chat-specific MCP server that exposes
query_entities, list_tickets, etc. as native MCP tools. Gemini
selects and calls tools through its trained function calling
mechanism — no text-based TOOL_CALL regex parsing.

Usage:
    python tests/test_chat_mcp_structured.py

Requires:
    - Gemini CLI installed and authenticated
    - Local warehouse DB at data/local_warehouse.db
"""

import json
import os
import sys
import time
import logging
from pathlib import Path

# Ensure project root is on path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(name)-25s %(levelname)s %(message)s",
)
logger = logging.getLogger("test_chat_mcp")

# Show everything from bridge
logging.getLogger("alma.acp_bridge").setLevel(logging.DEBUG)


def run_test():
    """Boot ACP with chat MCP tools, ask about Thunderbird Insurance."""

    server_path = ROOT / "src" / "mcp" / "chat_mcp_server.py"
    if not server_path.exists():
        logger.error("Chat MCP server not found: %s", server_path)
        return

    db_path = str(ROOT / "data" / "local_warehouse.db")
    if not os.path.exists(db_path):
        logger.error("Database not found: %s", db_path)
        return

    from src.agents.acp_bridge import ACPBridge

    # MCP server config — chat tools with DB path
    mcp_config = [
        {
            "name": "alma-chat-tools",
            "command": sys.executable,
            "args": ["-m", "src.mcp.chat_mcp_server"],
            "env": [
                {"name": "ALMA_DB_PATH", "value": db_path},
            ],
        }
    ]

    model = "gemini-2.5-flash-lite"

    bridge = ACPBridge(model=model)
    bridge.set_mcp_config(mcp_config)

    try:
        logger.info("=" * 60)
        logger.info("Booting ACP bridge with chat MCP server...")
        logger.info("Model: %s", model)
        logger.info("MCP: alma-chat-tools (DB: %s)", db_path)
        logger.info("=" * 60)

        bridge.ensure_running()
        logger.info("Bridge running (pid=%s, session=%s)",
                     bridge._process.pid if bridge._process else "?",
                     bridge._session_id[:12] if bridge._session_id else "?")

        # Wait for MCP server to register with ACP
        logger.info("Waiting 5s for MCP server initialization...")
        time.sleep(5)

        # Simple prompt — Gemini should see MCP tools natively
        system_prompt = (
            "You are a data analyst assistant. You have tools to query a "
            "ticket database. Use the query_entities tool when asked about "
            "a specific payer or insurance company. Summarize findings concisely."
        )
        user_query = "What is the most common issue with Thunderbird Insurance?"

        full_prompt = (
            f"[SYSTEM INSTRUCTIONS]\n{system_prompt}\n"
            f"[END SYSTEM INSTRUCTIONS]\n\n{user_query}"
        )

        logger.info("Prompt: %s", user_query)
        logger.info("Using call_streaming() with on_token to trace all events...")

        def on_token(event):
            """Log every event from the stream."""
            logger.info(
                "STREAM EVENT: type=%s data=%s",
                event.type,
                json.dumps(event.data, default=str)[:300],
            )

        t0 = time.time()
        result = bridge.call_streaming(
            full_prompt, "chat_test_1",
            on_token=on_token, timeout=90,
        )
        elapsed = time.time() - t0

        print("\n" + "=" * 60)
        print("STREAMING RESULT")
        print("=" * 60)
        print(f"Time: {elapsed:.1f}s")
        print(f"Error: {result.get('error')}")
        print(f"Message: {result.get('message', '')}")
        print(f"Events: {len(result.get('events', []))}")
        print(f"Full text length: {len(result.get('full_text', ''))}")
        print()

        # Show event type breakdown
        event_types = {}
        for evt in result.get("events", []):
            event_types[evt.type] = event_types.get(evt.type, 0) + 1
        print(f"Event breakdown: {event_types}")
        print()

        full_text = result.get("full_text", "")
        if full_text:
            print("Response (first 1000 chars):")
            print(full_text[:1000])
        else:
            print("(empty response)")
        print("=" * 60)

        if full_text:
            has_thunderbird = "thunderbird" in full_text.lower()
            print(f"\nMentions Thunderbird: {'YES' if has_thunderbird else 'NO'}")

    except TimeoutError as e:
        print(f"\nTIMEOUT: {e}")
    except RuntimeError as e:
        print(f"\nRUNTIME ERROR: {e}")
    except Exception as e:
        logger.exception("Test failed: %s", e)
    finally:
        logger.info("Shutting down bridge...")
        bridge.shutdown()
        logger.info("Done.")


if __name__ == "__main__":
    run_test()
