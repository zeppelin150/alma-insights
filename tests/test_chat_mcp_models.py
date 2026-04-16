"""
Test: MCP chat tool round-trip across all Gemini models.

Boots ACP with the chat MCP server for each model in the chat page's
default model list, sends a tool-requiring prompt, and reports whether
the full round-trip completes (prompt → tool_call → content → done).

Usage:
    python tests/test_chat_mcp_models.py [model_name]

If model_name is provided, only that model is tested.
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
logger = logging.getLogger("test_models")
logging.getLogger("alma.acp_bridge").setLevel(logging.INFO)

ALL_MODELS = [
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-3-flash-preview",
    "gemini-3.1-pro-preview",
]

DB_PATH = str(ROOT / "data" / "local_warehouse.db")
MCP_CONFIG = [
    {
        "name": "alma-chat-tools",
        "command": sys.executable,
        "args": ["-m", "src.mcp.chat_mcp_server"],
        "env": [{"name": "ALMA_DB_PATH", "value": DB_PATH}],
    }
]

PROMPT = (
    "[SYSTEM INSTRUCTIONS]\n"
    "You are a data analyst assistant. You have tools to query a "
    "ticket database. Use the query_entities tool when asked about "
    "a specific payer or insurance company. Summarize findings concisely.\n"
    "[END SYSTEM INSTRUCTIONS]\n\n"
    "What is the most common issue with Thunderbird Insurance?"
)


def test_model(model: str) -> dict:
    """Test a single model. Returns result dict."""
    from src.agents.acp_bridge import ACPBridge

    result = {
        "model": model,
        "status": "UNKNOWN",
        "time_s": 0,
        "tool_calls": 0,
        "content_len": 0,
        "has_thunderbird": False,
        "error": None,
        "events": {},
    }

    bridge = ACPBridge(model=model)
    bridge.set_mcp_config(MCP_CONFIG)

    try:
        logger.info("=" * 60)
        logger.info("Testing model: %s", model)
        logger.info("=" * 60)

        bridge.ensure_running()
        time.sleep(3)  # MCP server init

        event_counts = {}

        def on_token(event):
            event_counts[event.type] = event_counts.get(event.type, 0) + 1
            if event.type == "content":
                delta = event.data.get("delta", "")
                if delta.strip():
                    logger.info("[%s] content: %s", model, delta[:100])
            elif event.type == "tool_call":
                logger.info("[%s] tool_call: %s", model, event.data.get("name", "?"))

        t0 = time.time()
        resp = bridge.call_streaming(
            PROMPT, f"test_{model}", on_token=on_token, timeout=60,
        )
        elapsed = time.time() - t0

        result["time_s"] = round(elapsed, 1)
        result["events"] = event_counts
        result["tool_calls"] = event_counts.get("tool_call", 0)
        result["content_len"] = len(resp.get("full_text", ""))
        result["has_thunderbird"] = "thunderbird" in resp.get("full_text", "").lower()

        if resp.get("error"):
            result["status"] = "ERROR"
            result["error"] = f"{resp['error']}: {resp.get('message', '')}"
        elif result["content_len"] > 0 and result["has_thunderbird"]:
            result["status"] = "PASS"
        elif result["content_len"] > 0:
            result["status"] = "PARTIAL"  # Got content but no Thunderbird mention
        else:
            result["status"] = "EMPTY"

    except TimeoutError:
        result["status"] = "TIMEOUT"
        result["time_s"] = 60
        result["error"] = "Timed out after 60s"
    except RuntimeError as e:
        result["status"] = "ERROR"
        result["error"] = str(e)[:200]
    except Exception as e:
        result["status"] = "ERROR"
        result["error"] = str(e)[:200]
    finally:
        try:
            bridge.shutdown()
        except Exception:
            pass
        # Kill any stragglers
        time.sleep(2)

    return result


def main():
    if not os.path.exists(DB_PATH):
        logger.error("Database not found: %s", DB_PATH)
        return

    # Optional: test a single model
    models = ALL_MODELS
    if len(sys.argv) > 1:
        models = [sys.argv[1]]

    results = []
    for model in models:
        r = test_model(model)
        results.append(r)
        logger.info("Result: %s — %s (%.1fs, %d tool calls, %d chars)",
                     r["model"], r["status"], r["time_s"],
                     r["tool_calls"], r["content_len"])

    # Summary table
    print("\n" + "=" * 80)
    print("MODEL COMPATIBILITY MATRIX")
    print("=" * 80)
    print(f"{'Model':<28} {'Status':<10} {'Time':<8} {'Tools':<7} {'Chars':<7} {'Thunderbird'}")
    print("-" * 80)
    for r in results:
        print(f"{r['model']:<28} {r['status']:<10} {r['time_s']:<8} "
              f"{r['tool_calls']:<7} {r['content_len']:<7} "
              f"{'YES' if r['has_thunderbird'] else 'NO'}")
        if r["error"]:
            print(f"  ERROR: {r['error']}")
    print("=" * 80)


if __name__ == "__main__":
    main()
