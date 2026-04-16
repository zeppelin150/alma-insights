"""
ACP Protocol Probe v4 — Corrected parameter formats.

Findings from v3:
  - mcpServers is REQUIRED (array), even if empty
  - prompt must be an array, not a string
  - sessionId is REQUIRED on session/prompt

This probe sends messages sequentially via subprocess.run() per step.
"""

import json
import subprocess
import sys
import time
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


def run_acp(gemini_path, messages, label, timeout=30):
    """Send messages to gemini --acp and capture output."""
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")

    stdin_blob = "\n".join(json.dumps(m) for m in messages) + "\n"

    for i, m in enumerate(messages):
        print(f"  >>> [{m.get('id','-')}] {m['method']}")
        # Show params compactly
        params = m.get("params", {})
        for k, v in params.items():
            val_str = json.dumps(v) if not isinstance(v, str) else v
            if len(val_str) > 80:
                val_str = val_str[:80] + "..."
            print(f"      {k}: {val_str}")

    try:
        result = subprocess.run(
            [gemini_path, "--acp"],
            input=stdin_blob.encode("utf-8"),
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"  TIMEOUT after {timeout}s")
        return

    # Parse stdout
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")

    print(f"\n  exit={result.returncode}  stdout={len(result.stdout)}B  stderr={len(result.stderr)}B")

    # Show stderr summary (first few relevant lines)
    if stderr.strip():
        print(f"\n  STDERR (first 5 lines):")
        for line in stderr.strip().split("\n")[:5]:
            print(f"    {line.rstrip()}")

    # Parse and show JSON objects
    print(f"\n  RESPONSES:")
    for line in stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            # Skip bare integers, arrays, strings — only dicts are JSON-RPC
            if not isinstance(obj, dict):
                continue  # silently skip experiment IDs etc
            rid = obj.get("id", "?")
            if "result" in obj:
                print(f"\n  [id={rid}] SUCCESS:")
                pretty = json.dumps(obj["result"], indent=2)
                if len(pretty) > 1500:
                    pretty = pretty[:1500] + "\n...(truncated)"
                for pl in pretty.split("\n"):
                    print(f"    {pl}")
            elif "error" in obj:
                print(f"\n  [id={rid}] ERROR:")
                print(f"    {json.dumps(obj['error'], indent=2)}")
            elif "method" in obj:
                method = obj["method"]
                params = obj.get("params", {})
                params_str = json.dumps(params)
                if len(params_str) > 500:
                    params_str = params_str[:500] + "..."
                print(f"\n  [notification] {method}: {params_str}")
            else:
                print(f"\n  [unknown] {json.dumps(obj)[:300]}")
        except json.JSONDecodeError:
            # Only print non-JSON if it's not debug noise
            if not any(skip in line for skip in [
                "Ignore file", "Hook ", "DEBUG", "Experiments loaded",
                "experimentIds", "]", "}"
            ]):
                print(f"\n  [non-JSON] {line[:200]}")


def main():
    gemini_path = _find_gemini_cli()
    if not gemini_path:
        print("ERROR: gemini CLI not found")
        sys.exit(1)
    print(f"CLI: {gemini_path}\n")

    cwd = str(Path.cwd())

    # ══════════════════════════════════════════════════════
    # TEST 1: Initialize + session/new with mcpServers=[]
    # ══════════════════════════════════════════════════════
    run_acp(gemini_path, [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"clientCapabilities": {}, "protocolVersion": 1}},
        {"jsonrpc": "2.0", "id": 2, "method": "session/new",
         "params": {"cwd": cwd, "mcpServers": []}},
    ], "TEST 1: init + session/new (mcpServers=[])")

    # ══════════════════════════════════════════════════════
    # TEST 2: prompt as array of strings
    # ══════════════════════════════════════════════════════
    run_acp(gemini_path, [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"clientCapabilities": {}, "protocolVersion": 1}},
        {"jsonrpc": "2.0", "id": 2, "method": "session/new",
         "params": {"cwd": cwd, "mcpServers": []}},
        {"jsonrpc": "2.0", "id": 3, "method": "session/prompt",
         "params": {"sessionId": "test-1", "prompt": ["Respond with one word: OK"]}},
    ], "TEST 2: prompt as array of strings", timeout=30)

    # ══════════════════════════════════════════════════════
    # TEST 3: prompt as array of content parts
    # ══════════════════════════════════════════════════════
    run_acp(gemini_path, [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"clientCapabilities": {}, "protocolVersion": 1}},
        {"jsonrpc": "2.0", "id": 2, "method": "session/new",
         "params": {"cwd": cwd, "mcpServers": []}},
        {"jsonrpc": "2.0", "id": 3, "method": "session/prompt",
         "params": {"sessionId": "test-1",
                    "prompt": [{"type": "text", "text": "Respond with one word: OK"}]}},
    ], "TEST 3: prompt as content parts [{type,text}]", timeout=30)

    # ══════════════════════════════════════════════════════
    # TEST 4: prompt as array of message objects
    # ══════════════════════════════════════════════════════
    run_acp(gemini_path, [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"clientCapabilities": {}, "protocolVersion": 1}},
        {"jsonrpc": "2.0", "id": 2, "method": "session/new",
         "params": {"cwd": cwd, "mcpServers": []}},
        {"jsonrpc": "2.0", "id": 3, "method": "session/prompt",
         "params": {"sessionId": "test-1",
                    "prompt": [{"role": "user", "content": "Respond with one word: OK"}]}},
    ], "TEST 4: prompt as messages [{role,content}]", timeout=30)

    # ══════════════════════════════════════════════════════
    # TEST 5: Use sessionId from actual session/new response
    # We need to capture the real sessionId. Since subprocess.run
    # closes stdin immediately, the prompt goes in the same batch.
    # Try a dummy sessionId to see the error shape.
    # ══════════════════════════════════════════════════════

    # ══════════════════════════════════════════════════════
    # TEST 6: MCP server config in session/new
    # ══════════════════════════════════════════════════════
    run_acp(gemini_path, [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"clientCapabilities": {}, "protocolVersion": 1}},
        {"jsonrpc": "2.0", "id": 2, "method": "session/new",
         "params": {"cwd": cwd, "mcpServers": [
             {"name": "test-tools", "type": "stdio",
              "command": "python", "args": ["-c", "print('hello')"]}
         ]}},
    ], "TEST 6: session/new with MCP server (stdio)")

    # ══════════════════════════════════════════════════════
    # TEST 7: MCP server as HTTP
    # ══════════════════════════════════════════════════════
    run_acp(gemini_path, [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"clientCapabilities": {}, "protocolVersion": 1}},
        {"jsonrpc": "2.0", "id": 2, "method": "session/new",
         "params": {"cwd": cwd, "mcpServers": [
             {"name": "test-http", "type": "sse",
              "url": "http://localhost:9999/mcp"}
         ]}},
    ], "TEST 7: session/new with MCP server (sse)")

    print(f"\n{'='*60}")
    print("  ALL TESTS COMPLETE — paste everything above")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
