"""
MCP Tool E2E Diagnostic — Why does store_classification fail in ACP mode?

This test isolates the MCP tool call path:
1. Boot an ACP bridge with MCP server configured
2. Create a session with batch-specific env vars
3. Send a prompt that MUST use the store_classification tool
4. Capture all events (tool_call, tool_result, content, errors)
5. Log stderr at DEBUG level to see MCP server output

Hypotheses being tested:
  A: Env vars never reach the MCP subprocess (schema mismatch)
  B: MCP server not re-spawned on session/new (stale empty env)

Usage:
  python tests/test_mcp_tool_e2e.py
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import time
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# DEBUG level to capture MCP server stderr
logging.basicConfig(
    level=logging.DEBUG,
    format="  [%(name)s] %(levelname)s %(message)s",
    stream=sys.stdout,
    force=True,
)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)

DB_PATH = str(Path("data/local_warehouse.db").resolve())


def main():
    from src.agents.acp_bridge import ACPBridge

    print("=" * 70)
    print("  MCP TOOL E2E DIAGNOSTIC")
    print("=" * 70)
    print()

    # ── Step 1: Verify CLI exists ──
    cli_path = ACPBridge._find_gemini_cli()
    if not cli_path:
        print("[ABORT] Gemini CLI not found")
        return 1
    print(f"[1] CLI: {cli_path}")

    # ── Step 2: Boot bridge with MCP config ──
    print()
    print("[2] Booting bridge with MCP server config...")
    bridge = ACPBridge(model="gemini-2.5-flash")
    bridge.set_mcp_config([{
        "name": "alma-tools",
        "command": sys.executable,
        "args": ["-m", "src.mcp.alma_mcp_server"],
        "env": [],
    }])
    bridge.ensure_running()
    print(f"    Bridge alive: {bridge.is_alive()}")
    print(f"    Session: {bridge._session_id}")

    # ── Step 3: Log the MCP config we're about to send ──
    mcp_env = {
        "ALMA_DB_PATH": DB_PATH,
        "ALMA_SCAN_ID": "test_mcp_diagnostic",
        "ALMA_BATCH_ID": "batch_diag_001",
        "ALMA_TRC": "Test TRC",
        "ALMA_AGENT_ID": "diag_worker",
        "ALMA_TICKET_TRC_MAP_JSON": json.dumps({"DIAG-001": "Test TRC"}),
    }

    injected = bridge._inject_mcp_env(bridge._mcp_servers, mcp_env)
    print()
    print("[3] MCP config being sent to session/new:")
    print(f"    {json.dumps(injected, indent=2)}")

    # ── Step 4: Create session with env vars ──
    print()
    print("[4] Creating new session with MCP env vars...")
    session_id = bridge.new_session(mcp_env=mcp_env)
    print(f"    New session: {session_id}")

    # ── Step 5: Send a prompt that forces tool use ──
    print()
    print("[5] Sending prompt that requests store_classification tool call...")
    print("    (Watching for tool_call, tool_result, and content events)")
    print()

    prompt = (
        "You have ONE task. Call the store_classification MCP tool with "
        "these exact arguments:\n"
        "  ticket_id: \"DIAG-001\"\n"
        "  sub_cluster: \"diagnostic_test\"\n"
        "  sub_cluster_confidence: 0.99\n"
        "  is_novel: false\n"
        "  sentiment_intensity: 1\n"
        "  sentiment_polarity: \"neutral\"\n"
        "  friction_type: \"other\"\n"
        "  anomaly_flag: \"normal\"\n"
        "  entities: {}\n"
        "  key_phrases: [\"diagnostic\"]\n"
        "  root_cause_hint: \"MCP diagnostic test\"\n"
        "  summary: \"Diagnostic test ticket\"\n\n"
        "Do NOT output JSON text. ONLY call the store_classification tool. "
        "Do NOT use any other tools. Do NOT read files."
    )

    events_seen = {
        "content": [],
        "tool_call": [],
        "tool_result": [],
        "error": [],
        "other": [],
    }
    full_text = ""

    def on_token(event):
        nonlocal full_text
        etype = event.type
        if etype == "content":
            delta = event.data.get("delta", "")
            full_text += delta
            events_seen["content"].append(delta[:80])
        elif etype == "tool_call":
            events_seen["tool_call"].append(event.data)
            print(f"    >> TOOL_CALL: {json.dumps(event.data)[:200]}")
        elif etype == "tool_result":
            events_seen["tool_result"].append(event.data)
            print(f"    >> TOOL_RESULT: {json.dumps(event.data)[:200]}")
        elif etype == "error":
            events_seen["error"].append(event.data)
            print(f"    >> ERROR: {json.dumps(event.data)[:200]}")
        else:
            events_seen["other"].append({"type": etype, "data": str(event.data)[:100]})

    try:
        result = bridge.call_streaming(
            prompt, "mcp_diag_001",
            on_token=on_token,
            timeout=120,
        )
    except Exception as e:
        print(f"    >> EXCEPTION: {e}")
        result = {"error": str(e)}

    # ── Step 6: Print results ──
    print()
    print("=" * 70)
    print("  RESULTS")
    print("=" * 70)
    print()
    print(f"  tool_call events:   {len(events_seen['tool_call'])}")
    print(f"  tool_result events: {len(events_seen['tool_result'])}")
    print(f"  content events:     {len(events_seen['content'])}")
    print(f"  error events:       {len(events_seen['error'])}")
    print(f"  other events:       {len(events_seen['other'])}")
    print()

    if events_seen["tool_call"]:
        print("  TOOL CALLS:")
        for tc in events_seen["tool_call"]:
            print(f"    {json.dumps(tc)[:300]}")
        print()

    if events_seen["tool_result"]:
        print("  TOOL RESULTS:")
        for tr in events_seen["tool_result"]:
            print(f"    {json.dumps(tr)[:300]}")
        print()

    if events_seen["error"]:
        print("  ERRORS:")
        for err in events_seen["error"]:
            print(f"    {json.dumps(err)[:300]}")
        print()

    if events_seen["other"]:
        print("  OTHER EVENTS:")
        for oth in events_seen["other"]:
            print(f"    {oth}")
        print()

    if full_text:
        print(f"  FULL TEXT RESPONSE ({len(full_text)} chars):")
        print(f"    {full_text[:500]}")
        print()

    # Result from call_streaming
    print(f"  call_streaming result: {json.dumps(result, default=str)[:300]}")
    print()

    # ── Step 7: Check bridge stderr for MCP server messages ──
    print("  BRIDGE STDERR (last 10 lines):")
    for line in bridge._last_stderr_lines:
        print(f"    {line}")
    print()

    # ── Step 8: Diagnosis ──
    print("=" * 70)
    print("  DIAGNOSIS")
    print("=" * 70)

    if events_seen["tool_call"]:
        print("  MCP tool calls WERE dispatched by the model.")
        if events_seen["tool_result"]:
            # Check if results contain errors
            for tr in events_seen["tool_result"]:
                text = json.dumps(tr)
                if "error" in text.lower() or "-32603" in text:
                    print(f"  BUT tool_result contained ERROR: {text[:200]}")
                    print("  => Hypothesis A likely: env vars not reaching MCP server")
                else:
                    print(f"  Tool result looks OK: {text[:200]}")
                    print("  => MCP tools ARE working!")
        else:
            print("  BUT no tool_result events seen.")
            print("  => MCP server may not be running or not responding")
    else:
        print("  NO tool_call events dispatched.")
        if "store_classification" in full_text.lower() or "error" in full_text.lower():
            print("  Model mentioned tool errors in text response.")
            print("  => Model tried tools, got errors, fell back to text.")
            print("  => Check env var injection and MCP server startup.")
        else:
            print("  Model didn't attempt tool use at all.")
            print("  => Prompt directive may be insufficient or model can't see tools.")

    print()

    # Cleanup
    bridge.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
