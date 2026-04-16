"""
MCP store_classification diagnostic.

Tests:
1. Does store_classification persist to SQLite via MCP?
2. What events does the bridge surface (tool_call, tool_result, content)?
3. Does the WorkerAgent on_token callback see MCP tool events?
"""
import sys, json, sqlite3, os
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(line_buffering=True)

from pathlib import Path
from src.agents.acp_bridge import ACPBridge

import logging
logging.basicConfig(
    level=logging.DEBUG,
    format="[%(name)s] %(message)s",
    stream=sys.stdout,
    force=True,
)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)

DB_PATH = str(Path("data/local_warehouse.db").resolve())
SCAN_ID = "diag_store_test"
BATCH_ID = "diag_store_batch"
TICKET_ID = "DIAG-STORE-001"
TRC = "Diagnostic TRC"


def check_ticket_in_db(db_path, scan_id, ticket_id):
    """Check if the ticket was persisted to SQLite."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM nlp_ticket_classifications "
        "WHERE scan_id = ? AND ticket_id = ?",
        (scan_id, ticket_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def ensure_scan_record(db_path, scan_id, batch_id):
    """Create scan + batch records so FK constraints pass."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO nlp_scan_runs "
            "(scan_id, status, date_range_start, date_range_end, "
            "created_at, mode) "
            "VALUES (?, 'running', '2025-01-01', '2025-01-01', "
            "datetime('now'), 'full')",
            (scan_id,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO nlp_batches "
            "(batch_id, scan_id, batch_number, trc, status, created_at) "
            "VALUES (?, ?, 1, 'Diagnostic TRC', 'running', datetime('now'))",
            (batch_id, scan_id),
        )
        conn.commit()
    except Exception as e:
        print(f"  (setup: {e})", flush=True)
    conn.close()


def cleanup_diag_ticket(db_path, scan_id):
    """Remove diagnostic ticket, batch, and scan records from DB."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM nlp_ticket_classifications WHERE scan_id = ?", (scan_id,))
        conn.execute("DELETE FROM nlp_batches WHERE scan_id = ?", (scan_id,))
        conn.execute("DELETE FROM nlp_scan_runs WHERE scan_id = ?", (scan_id,))
        conn.commit()
    except Exception:
        pass
    conn.close()


def main():
    print("=" * 70, flush=True)
    print("  MCP store_classification DIAGNOSTIC", flush=True)
    print("=" * 70, flush=True)
    print(flush=True)

    # FK records must be pre-created (scan + batch).
    # Run setup manually if needed — see comments at top of file.
    _chk = sqlite3.connect(DB_PATH)
    _s = _chk.execute("SELECT scan_id FROM nlp_scan_runs WHERE scan_id=?", (SCAN_ID,)).fetchone()
    _b = _chk.execute("SELECT batch_id FROM nlp_batches WHERE batch_id=?", (BATCH_ID,)).fetchone()
    # Clean only the classification row, not the FK records
    _chk.execute("DELETE FROM nlp_ticket_classifications WHERE scan_id=? AND ticket_id=?", (SCAN_ID, TICKET_ID))
    _chk.commit()
    _chk.close()
    print(f"  FK records: scan={_s is not None} batch={_b is not None}", flush=True)
    if not _s or not _b:
        print("  [ABORT] Missing FK records. Run setup first.", flush=True)
        return 1
    before = check_ticket_in_db(DB_PATH, SCAN_ID, TICKET_ID)
    print(f"[1] Pre-check: ticket in DB = {before is not None}", flush=True)

    # Boot bridge
    bridge = ACPBridge(model="gemini-2.5-flash")
    bridge.set_mcp_config([{
        "name": "alma-tools",
        "command": sys.executable,
        "args": ["-m", "src.mcp.alma_mcp_server"],
        "env": [],
    }])
    bridge.ensure_running()
    print(f"[2] Bridge booted: {bridge._session_id[:12]}", flush=True)

    # New session with env vars
    mcp_env = {
        "ALMA_DB_PATH": DB_PATH,
        "ALMA_SCAN_ID": SCAN_ID,
        "ALMA_BATCH_ID": BATCH_ID,
        "ALMA_TRC": TRC,
        "ALMA_AGENT_ID": "diag_worker",
        "ALMA_TICKET_TRC_MAP_JSON": json.dumps({TICKET_ID: TRC}),
    }
    sid = bridge.new_session(mcp_env=mcp_env)
    print(f"[3] Session with env: {sid[:12]}", flush=True)

    # Track ALL events by type
    event_log = []

    def on_token(event):
        entry = {
            "type": event.type,
            "data": event.data,
        }
        event_log.append(entry)

        if event.type == "tool_call":
            print(f"  >> TOOL_CALL: name={event.data.get('name')} "
                  f"args={json.dumps(event.data.get('args',{}), default=str)[:150]}",
                  flush=True)
        elif event.type == "tool_result":
            print(f"  >> TOOL_RESULT: {json.dumps(event.data, default=str)[:200]}",
                  flush=True)
        elif event.type == "content":
            delta = event.data.get("delta", "")
            if delta.strip():
                print(f"  >> CONTENT: {delta[:120]}", flush=True)
        elif event.type == "heartbeat":
            pass  # skip
        else:
            print(f"  >> {event.type}: {json.dumps(event.data, default=str)[:150]}",
                  flush=True)

    # Prompt: force a store_classification call
    prompt = f"""You must call the mcp_alma_tools_store_classification tool with these EXACT arguments:
  ticket_id: "{TICKET_ID}"
  sub_cluster: "diagnostic_mcp_test"
  sub_cluster_confidence: 0.99
  is_novel: false
  sentiment_intensity: 1
  sentiment_polarity: "neutral"
  friction_type: "other"
  anomaly_flag: "normal"
  entities: {{}}
  key_phrases: ["mcp_diagnostic"]
  root_cause_hint: "MCP store diagnostic"
  summary: "Testing MCP store_classification persistence"

After calling the tool, report whether it succeeded or failed.
Do NOT read files, do NOT search the codebase, do NOT use shell.
"""

    print(f"[4] Sending store_classification prompt (timeout=90s)...", flush=True)
    print(flush=True)

    try:
        result = bridge.call_streaming(
            prompt, "diag_store_001",
            on_token=on_token,
            timeout=90,
        )
    except Exception as e:
        print(f"  EXCEPTION: {e}", flush=True)
        result = {"error": str(e), "full_text": ""}

    print(flush=True)
    print("=" * 70, flush=True)
    print("  RESULTS", flush=True)
    print("=" * 70, flush=True)
    print(flush=True)

    # Count events by type
    type_counts = {}
    for e in event_log:
        t = e["type"]
        type_counts[t] = type_counts.get(t, 0) + 1

    print(f"  Events by type: {type_counts}", flush=True)
    print(f"  Full text: {repr(result.get('full_text','')[:300])}", flush=True)
    print(f"  Error: {result.get('error')}", flush=True)
    print(flush=True)

    # List all non-heartbeat, non-content events
    interesting = [e for e in event_log if e["type"] not in ("heartbeat", "content")]
    if interesting:
        print("  Non-content events:", flush=True)
        for e in interesting:
            print(f"    {e['type']}: {json.dumps(e['data'], default=str)[:200]}", flush=True)
        print(flush=True)

    # Check DB for persistence
    after = check_ticket_in_db(DB_PATH, SCAN_ID, TICKET_ID)
    print(f"  DB CHECK: ticket persisted = {after is not None}", flush=True)
    if after:
        print(f"  DB ROW: sub_cluster={after.get('sub_cluster')}, "
              f"confidence={after.get('sub_cluster_confidence')}, "
              f"friction={after.get('friction_type')}", flush=True)
    print(flush=True)

    # Diagnosis
    print("=" * 70, flush=True)
    print("  DIAGNOSIS", flush=True)
    print("=" * 70, flush=True)

    tool_calls = [e for e in event_log if e["type"] == "tool_call"]
    tool_results = [e for e in event_log if e["type"] == "tool_result"]

    if after:
        print("  PASS: store_classification PERSISTED to SQLite via MCP", flush=True)
    else:
        print("  FAIL: ticket NOT in database", flush=True)

    if tool_calls:
        print(f"  PASS: {len(tool_calls)} tool_call events observed by bridge", flush=True)
    else:
        print("  INFO: No tool_call events surfaced to bridge callback", flush=True)
        print("        (CLI may handle MCP tools internally)", flush=True)

    if tool_results:
        print(f"  PASS: {len(tool_results)} tool_result events observed", flush=True)
    else:
        print("  INFO: No tool_result events surfaced to bridge callback", flush=True)

    # Clean up
    cleanup_diag_ticket(DB_PATH, SCAN_ID)
    bridge.shutdown()
    print(flush=True)
    print("Done.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
