"""
ACP Prompt Probe — Captures a real prompt response with correct parameters.

Uses Popen with a background timer to kill after collecting output.
Sends messages sequentially with delays to ensure ordering.
"""

import json
import subprocess
import sys
import time
import threading
import os
from pathlib import Path


def _find_gemini_cli():
    try:
        import yaml
        sp = Path(__file__).parent.parent / "data" / "settings.yaml"
        if sp.exists():
            with open(sp, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            cp = cfg.get("gemini", {}).get("cli_path", "")
            if cp and Path(cp).exists():
                return cp
    except Exception:
        pass
    candidates = [
        Path(os.environ.get("APPDATA", "")) / "npm" / "gemini.CMD",
        Path(os.environ.get("APPDATA", "")) / "npm" / "gemini.cmd",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    import shutil
    return shutil.which("gemini") or shutil.which("gemini.cmd")


def send(proc, obj):
    line = json.dumps(obj) + "\n"
    proc.stdin.write(line.encode("utf-8"))
    proc.stdin.flush()
    print(f"  >>> {obj.get('method', '?')} (id={obj.get('id','?')})", flush=True)


def main():
    print("=" * 60)
    print("  ACP PROMPT PROBE — capturing real prompt response")
    print("=" * 60)

    gemini_path = _find_gemini_cli()
    if not gemini_path:
        print("ERROR: gemini CLI not found")
        sys.exit(1)

    proc = subprocess.Popen(
        [gemini_path, "--acp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=os.environ.copy(),
    )

    # Collect all stdout in background
    all_stdout = []
    def read_stdout():
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip()
                all_stdout.append(line)
        except Exception:
            pass

    # Collect stderr
    all_stderr = []
    def read_stderr():
        try:
            for raw in proc.stderr:
                line = raw.decode("utf-8", errors="replace").rstrip()
                all_stderr.append(line)
        except Exception:
            pass

    threading.Thread(target=read_stdout, daemon=True).start()
    threading.Thread(target=read_stderr, daemon=True).start()

    # Kill process after 45 seconds no matter what
    def kill_later():
        time.sleep(45)
        try:
            proc.terminate()
        except Exception:
            pass
    threading.Thread(target=kill_later, daemon=True).start()

    # ── Step 1: Initialize ──
    time.sleep(1)
    send(proc, {
        "jsonrpc": "2.0", "id": 1,
        "method": "initialize",
        "params": {"clientCapabilities": {}, "protocolVersion": 1}
    })

    # Wait for initialize response
    print("  ... waiting 5s for initialize", flush=True)
    time.sleep(5)

    # ── Step 2: session/new ──
    send(proc, {
        "jsonrpc": "2.0", "id": 2,
        "method": "session/new",
        "params": {"cwd": str(Path.cwd()), "mcpServers": []}
    })

    # Wait for session/new and extract sessionId
    print("  ... waiting 8s for session/new", flush=True)
    time.sleep(8)

    # Parse sessionId from collected stdout
    session_id = None
    for line in all_stdout:
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and obj.get("id") == 2 and "result" in obj:
                session_id = obj["result"].get("sessionId")
                break
        except (json.JSONDecodeError, TypeError):
            pass

    if not session_id:
        print("\n  ERROR: Could not extract sessionId from session/new response")
        print(f"  Collected {len(all_stdout)} stdout lines")
        for line in all_stdout[:10]:
            print(f"    {line[:200]}")
        proc.terminate()
        return

    print(f"  Got sessionId: {session_id}", flush=True)

    # ── Step 3: Simple prompt ──
    send(proc, {
        "jsonrpc": "2.0", "id": 3,
        "method": "session/prompt",
        "params": {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": "Respond with exactly one word: OK"}]
        }
    })

    # Wait for prompt response (model needs time)
    print("  ... waiting 20s for prompt response", flush=True)
    time.sleep(20)

    # ── Step 4: Close stdin to signal we're done ──
    try:
        proc.stdin.close()
    except Exception:
        pass

    # Wait a bit more for any trailing output
    time.sleep(2)

    # Kill
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        proc.kill()

    # ── Print results ──
    print(f"\n{'='*60}")
    print(f"  RESULTS")
    print(f"{'='*60}")
    print(f"  Stdout lines: {len(all_stdout)}")
    print(f"  Stderr lines: {len(all_stderr)}")

    if all_stderr:
        print(f"\n  STDERR (first 3):")
        for s in all_stderr[:3]:
            print(f"    {s}")

    # Filter to just JSON-RPC dict messages
    print(f"\n  JSON-RPC MESSAGES:")
    msg_count = 0
    for line in all_stdout:
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict):
                continue
            # Skip if it's clearly noise (no jsonrpc field, no method, no id)
            if "jsonrpc" not in obj and "method" not in obj and "id" not in obj:
                continue
            msg_count += 1

            rid = obj.get("id", "-")
            method = obj.get("method", "")

            if method:
                # Notification
                params = obj.get("params", {})
                # For session/update, show the important parts
                if method == "session/update":
                    update = params.get("update", {})
                    session_update = update.get("sessionUpdate", "")
                    if session_update == "available_commands_update":
                        print(f"\n  [{msg_count}] session/update: available_commands_update (skipped)")
                        continue

                    # Print the FULL update for prompt responses
                    print(f"\n  [{msg_count}] session/update:")
                    pretty = json.dumps(params, indent=2)
                    if len(pretty) > 2000:
                        pretty = pretty[:2000] + "\n...(truncated)"
                    for pl in pretty.split("\n"):
                        print(f"    {pl}")
                else:
                    params_str = json.dumps(params)
                    if len(params_str) > 300:
                        params_str = params_str[:300] + "..."
                    print(f"\n  [{msg_count}] {method}: {params_str}")

            elif "result" in obj:
                print(f"\n  [{msg_count}] response id={rid} SUCCESS:")
                result = obj["result"]
                # For initialize, just show version
                if isinstance(result, dict) and "agentInfo" in result:
                    print(f"    (initialize response — version {result['agentInfo']['version']})")
                # For session/new, show sessionId
                elif isinstance(result, dict) and "sessionId" in result:
                    print(f"    sessionId: {result['sessionId']}")
                    print(f"    mode: {result.get('modes', {}).get('currentModeId', '?')}")
                else:
                    pretty = json.dumps(result, indent=2)
                    if len(pretty) > 1000:
                        pretty = pretty[:1000] + "\n...(truncated)"
                    for pl in pretty.split("\n"):
                        print(f"    {pl}")

            elif "error" in obj:
                print(f"\n  [{msg_count}] response id={rid} ERROR:")
                print(f"    {json.dumps(obj['error'], indent=2)}")

        except (json.JSONDecodeError, TypeError):
            pass

    print(f"\n  Total JSON-RPC messages: {msg_count}")
    print(f"\n{'='*60}")
    print("  DONE — paste everything above")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
