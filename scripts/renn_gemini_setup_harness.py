#!/usr/bin/env python
"""Live harness: Renn's Asana setup through the Gemini MCP tool server.

This drives the SAME path the Gemini chat uses — the stdio MCP server
(`src.mcp.chat_mcp_server`) that Gemini calls for tools — and walks the exact
JSON-RPC sequence Renn performs:

    initialize → tools/list → tools/call asana_discover → tools/call
    set_asana_board_config → verify the scoped write in monitor_sources.

Run it:
    python scripts/renn_gemini_setup_harness.py

By default Asana discovery returns mock projects/fields/GIDs (no key needed), so
this proves the full tool wiring. To test against LIVE Asana, store a key first:
    python -c "from src.data.pat_store import save_setting; save_setting('asana_api_key','<PAT>')"
then re-run — `asana_discover` will hit the real Asana API.

To put the real Gemini MODEL in the loop, point the gemini CLI at this same MCP
server and prompt "set up my Asana board for enablement" — Gemini will choose to
call these two tools itself. This harness validates the tool surface it consumes.
"""

import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)


def _rpc(proc, msg):
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    if not line:
        raise RuntimeError("MCP server closed stdout.\nstderr:\n" + proc.stderr.read())
    return json.loads(line)


def _call(proc, msg_id, name, arguments):
    resp = _rpc(proc, {"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
                       "params": {"name": name, "arguments": arguments}})
    text = resp["result"]["content"][0]["text"]
    return json.loads(text)


def main() -> int:
    # 1) fresh temp DB with all migrations (incl. 027 → monitor_sources)
    tmpdir = tempfile.mkdtemp(prefix="renn_harness_")
    db_path = os.path.join(tmpdir, "renn_harness.db")
    from src.data.db_manager import DatabaseManager
    DatabaseManager(db_path=db_path).initialize()
    print(f"[1] temp DB initialized: {db_path}")

    # 2) launch the Gemini MCP tool server with that DB
    env = dict(os.environ, ALMA_DB_PATH=db_path)
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.mcp.chat_mcp_server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, env=env, cwd=REPO,
    )
    try:
        init = _rpc(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        print(f"[2] MCP server up: {init['result']['serverInfo']['name']}")

        # 3) tools/list — confirm Renn's Asana tools are exposed to Gemini
        tl = _rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in tl["result"]["tools"]}
        assert "asana_discover" in names, "asana_discover not exposed!"
        assert "set_asana_board_config" in names, "set_asana_board_config not exposed!"
        print("[3] tools/list exposes: asana_discover, set_asana_board_config")

        # 4) asana_discover — Renn finds the GIDs (live Asana if a key is stored)
        disc = _call(proc, 3, "asana_discover", {})
        proj = disc["projects"][0]
        fields = disc["custom_fields"][proj["gid"]]
        team = next(f for f in fields if f["name"] == "Assigned Team")
        enab = next(o for o in team["enum_options"] if o["name"] == "Enablement")
        urg = next((f for f in fields if f["name"] == "Urgency"), None)
        ppl = next((f for f in fields if f["name"] == "Assigned People"), None)
        print(f"[4] discovered project '{proj['name']}' ({proj['gid']})")
        print(f"      Assigned Team field gid: {team['gid']}")
        print(f"      'Enablement' enum-value gid: {enab['gid']}")
        if urg:
            print(f"      Urgency field gid: {urg['gid']}")

        # 5) set_asana_board_config — Renn's single scoped write
        res = _call(proc, 4, "set_asana_board_config", {
            "project_gid": proj["gid"], "project_name": proj["name"],
            "indicator_field_gid": team["gid"], "indicator_field_name": team["name"],
            "indicator_value_gid": enab["gid"], "indicator_value_name": enab["name"],
            "priority_field_gid": (urg["gid"] if urg else None),
            "assignee_field_gid": (ppl["gid"] if ppl else None),
        })
        assert res.get("ok"), f"write failed: {res}"
        print(f"[5] set_asana_board_config wrote source: {res['source_id']}")
    finally:
        try:
            proc.stdin.close()
            proc.terminate()
        except Exception:
            pass

    # 6) verify the scoped write actually landed in monitor_sources
    from src.data.connection_factory import get_connection
    conn = get_connection(db_path, readonly=True)
    row = conn.execute(
        "SELECT source_id, config_json FROM monitor_sources WHERE source_type='asana'"
    ).fetchone()
    conn.close()
    assert row is not None, "no asana row in monitor_sources!"
    cfg = json.loads(row["config_json"])
    assert cfg["indicators"][0]["field_gid"] == team["gid"]
    assert cfg["indicators"][0]["trigger_value_gids"] == [enab["gid"]]
    print(f"[6] VERIFIED in monitor_sources: {row['source_id']}")
    print(f"      indicator field_gid={cfg['indicators'][0]['field_gid']}  "
          f"trigger_value_gids={cfg['indicators'][0]['trigger_value_gids']}")

    mode = "LIVE Asana" if disc.get("workspace", {}).get("gid", "").startswith("1204200") is False else "mock Asana"
    print(f"\nPASS — Renn's Asana setup works end-to-end through the Gemini MCP tool server ({mode}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
