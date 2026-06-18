"""
Tests for ClaudeCliBridge native-MCP wiring (Phase B1).

Claude rejects text-injected TOOL_RESULTs as prompt injection, so the Claude CLI
chat path must expose tools natively via MCP. These tests verify the bridge
builds a valid Claude-CLI mcp-config and the right invocation flags. The live
CLI<->MCP round-trip is verified on the Mac, not here.
"""
import json

import pytest

from src.agents.claude_cli_bridge import ClaudeCliBridge

# Mirrors gemini_chats_page._build_mcp_config() output shape.
PAGE_CFG = [{
    "name": "alma-chat-tools",
    "command": "/usr/bin/python3",
    "args": ["-m", "src.mcp.chat_mcp_server"],
    "env": [
        {"name": "ALMA_DB_PATH", "value": "/tmp/alma.db"},
        {"name": "ALMA_CHAT_SESSION_FILE", "value": "/tmp/.session"},
    ],
}]


def _bridge():
    b = ClaudeCliBridge(model="sonnet")
    b._cli_path = "claude"  # skip ensure_running() binary lookup
    return b


def test_mcp_config_written_with_stdio_schema():
    b = _bridge()
    b.set_mcp_config(PAGE_CFG)
    assert b._mcp_config_path, "config path not set"
    with open(b._mcp_config_path, encoding="utf-8") as f:
        cfg = json.load(f)
    srv = cfg["mcpServers"]["alma-chat-tools"]
    assert srv["type"] == "stdio"
    assert srv["command"] == "/usr/bin/python3"
    assert srv["args"] == ["-m", "src.mcp.chat_mcp_server"]
    assert srv["env"] == {"ALMA_DB_PATH": "/tmp/alma.db",
                          "ALMA_CHAT_SESSION_FILE": "/tmp/.session"}


def test_build_cmd_adds_mcp_flags_and_keeps_builtins_off():
    b = _bridge()
    b.set_mcp_config(PAGE_CFG)
    cmd = b._build_cmd()
    assert "--mcp-config" in cmd
    assert cmd[cmd.index("--mcp-config") + 1] == b._mcp_config_path
    assert "--strict-mcp-config" in cmd
    # hyphens in the server name normalize to underscores in the tool prefix
    assert cmd[cmd.index("--allowedTools") + 1] == "mcp__alma_chat_tools__*"
    # bypassPermissions is REQUIRED — dontAsk/default/acceptEdits all DENY the
    # MCP tools in -p mode (model fabricates tool calls; the UAT failure).
    assert cmd[cmd.index("--permission-mode") + 1] == "bypassPermissions"
    # built-in Bash/Edit/Read stay disabled
    assert cmd[cmd.index("--tools") + 1] == ""


def test_empty_config_disables_mcp():
    b = _bridge()
    b.set_mcp_config([])
    cmd = b._build_cmd()
    assert "--mcp-config" not in cmd
    assert "--allowedTools" not in cmd
    assert "--strict-mcp-config" not in cmd
    # text-loop / Gemini path: built-ins still off, no MCP
    assert cmd[cmd.index("--tools") + 1] == ""


def test_multiple_servers_allowlist_joined():
    b = _bridge()
    b.set_mcp_config(PAGE_CFG + [{
        "name": "alma_extra", "command": "/usr/bin/python3",
        "args": ["-m", "x"], "env": [],
    }])
    cmd = b._build_cmd()
    allow = cmd[cmd.index("--allowedTools") + 1]
    assert allow == "mcp__alma_chat_tools__*,mcp__alma_extra__*"


def test_client_stores_and_forwards_mcp_config():
    """ClaudeCliClient.set_mcp_config stores config and forwards to a live bridge,
    so the chat's Claude client wires native tools without touching the bridge."""
    from src.llm.claude_cli_client import ClaudeCliClient
    c = ClaudeCliClient(model="sonnet")
    c.set_mcp_config(PAGE_CFG)
    assert c._mcp_config == PAGE_CFG
    # attach a bridge, re-apply, confirm it materialized the CLI config
    b = ClaudeCliBridge(model="sonnet")
    c._bridge = b
    c.set_mcp_config(PAGE_CFG)
    assert b._mcp_config_path, "config not forwarded to bridge"
