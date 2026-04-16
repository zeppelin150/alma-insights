"""Minimal MCP tool call diagnostic. Logs to stdout, no grep filters."""
import sys, json, os
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent))

# Force unbuffered stdout
sys.stdout.reconfigure(line_buffering=True)

from src.agents.acp_bridge import ACPBridge
from pathlib import Path

import logging
logging.basicConfig(
    level=logging.DEBUG,
    format="[%(name)s] %(message)s",
    stream=sys.stdout,
    force=True,
)

DB_PATH = str(Path("data/local_warehouse.db").resolve())

print("=== MCP TOOL CALL DIAGNOSTIC ===", flush=True)

bridge = ACPBridge(model="gemini-2.5-flash")
bridge.set_mcp_config([{
    "name": "alma-tools",
    "command": sys.executable,
    "args": ["-m", "src.mcp.alma_mcp_server"],
    "env": [],
}])
bridge.ensure_running()
print(f"Boot OK: session={bridge._session_id[:12]}", flush=True)

# New session with env vars
mcp_env = {
    "ALMA_DB_PATH": DB_PATH,
    "ALMA_SCAN_ID": "diag_test",
    "ALMA_BATCH_ID": "diag_batch",
    "ALMA_TRC": "TestTRC",
    "ALMA_AGENT_ID": "diag",
    "ALMA_TICKET_TRC_MAP_JSON": json.dumps({"D-1": "TestTRC"}),
}
sid = bridge.new_session(mcp_env=mcp_env)
print(f"New session: {sid[:12]}", flush=True)

# Track events
seen = []
def on_token(event):
    seen.append(event.type)
    if event.type == "content":
        d = event.data.get("delta", "")
        if d.strip():
            print(f"  TEXT: {d[:120]}", flush=True)
    elif event.type == "heartbeat":
        pass  # skip noise
    else:
        print(f"  EVENT({event.type}): {json.dumps(event.data, default=str)[:200]}", flush=True)

print("Sending tool-use prompt (timeout=90s)...", flush=True)
try:
    result = bridge.call_streaming(
        'Call the mcp_alma_tools_query_taxonomy tool with trc="TestTRC". '
        'Report what the tool returns. Do NOT read files or use shell.',
        "diag_001",
        on_token=on_token,
        timeout=90,
    )
    print(f"\nRESULT: error={result.get('error')}", flush=True)
    print(f"RESULT: text={repr(result.get('full_text','')[:300])}", flush=True)
except Exception as e:
    print(f"\nEXCEPTION: {e}", flush=True)

print(f"\nEvent types seen: {seen}", flush=True)
print(f"Stderr lines: {bridge._last_stderr_lines}", flush=True)
bridge.shutdown()
print("Done.", flush=True)
