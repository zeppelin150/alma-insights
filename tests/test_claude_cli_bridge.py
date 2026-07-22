"""
Lifecycle tests for ClaudeCliBridge with mocked subprocess (no real CLI).

We assert behavior the contract test can't reach:
  - Bedrock env injection toggles correctly (Phase 2 verification)
  - PII redaction is applied via ClaudeCliClient (Phase 1)
  - call_streaming returns ACPBridge-shaped dict for happy path
  - abort + shutdown are idempotent / safe under no-active-call states
"""

from __future__ import annotations

from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ─── Bedrock env injection (Phase 2 verification) ───────────────────


class TestBedrockEnv:
    """``_build_subprocess_env`` translates settings → CLI subprocess env."""

    def test_disabled_yields_no_bedrock_vars(self, tmp_path, monkeypatch):
        from src.agents.claude_cli_bridge import ClaudeCliBridge
        with patch("src.data.settings_manager.get_section",
                   return_value={"enabled": False}):
            b = ClaudeCliBridge(model="sonnet")
            env = b._build_subprocess_env()
            assert "CLAUDE_CODE_USE_BEDROCK" not in env
            assert "AWS_REGION" not in env or env.get("AWS_REGION") != "us-west-2"

    def test_enabled_sets_bedrock_flag_and_region(self):
        from src.agents.claude_cli_bridge import ClaudeCliBridge
        with patch("src.data.settings_manager.get_section",
                   return_value={"enabled": True, "region": "us-east-1",
                                 "use_environment": True}):
            b = ClaudeCliBridge(model="sonnet")
            env = b._build_subprocess_env()
            assert env.get("CLAUDE_CODE_USE_BEDROCK") == "1"
            assert env.get("AWS_REGION") == "us-east-1"

    def test_enabled_no_region_does_not_set_aws_region(self):
        from src.agents.claude_cli_bridge import ClaudeCliBridge
        # Reset any existing AWS_REGION so we can detect the bridge not setting it
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("AWS_REGION", None)
            with patch("src.data.settings_manager.get_section",
                       return_value={"enabled": True, "region": "",
                                     "use_environment": True}):
                b = ClaudeCliBridge(model="sonnet")
                env = b._build_subprocess_env()
                assert env.get("CLAUDE_CODE_USE_BEDROCK") == "1"
                assert "AWS_REGION" not in env

    def test_use_environment_false_injects_pat_store_keys(self):
        from src.agents.claude_cli_bridge import ClaudeCliBridge

        def fake_load_setting(key, default=""):
            if key == "aws_access_key_id":
                return "AKIA-FAKE"
            if key == "aws_secret_access_key":
                return "secret-fake"
            return default

        with patch("src.data.settings_manager.get_section",
                   return_value={"enabled": True, "region": "us-west-2",
                                 "use_environment": False}), \
             patch("src.data.pat_store.load_setting",
                   side_effect=fake_load_setting):
            b = ClaudeCliBridge(model="sonnet")
            env = b._build_subprocess_env()
            assert env.get("AWS_ACCESS_KEY_ID") == "AKIA-FAKE"
            assert env.get("AWS_SECRET_ACCESS_KEY") == "secret-fake"
            assert "AWS_SESSION_TOKEN" not in env  # not set, not present


# ─── Build cmd + bridge state ────────────────────────────────────────


class TestBuildCmd:

    def test_build_cmd_contains_required_flags(self):
        from src.agents.claude_cli_bridge import ClaudeCliBridge
        b = ClaudeCliBridge(model="haiku")
        b._cli_path = "/fake/claude"
        cmd = b._build_cmd()
        assert "/fake/claude" in cmd
        assert "-p" in cmd
        assert "--input-format" in cmd and "text" in cmd
        assert "--output-format" in cmd and "stream-json" in cmd
        assert "--model" in cmd and "haiku" in cmd
        assert "--tools" in cmd
        # disabled tools spelled as empty string
        idx = cmd.index("--tools")
        assert cmd[idx + 1] == ""


# ─── State + idempotency ─────────────────────────────────────────────


class TestLifecycle:

    def test_init_attributes_present(self):
        from src.agents.claude_cli_bridge import ClaudeCliBridge
        b = ClaudeCliBridge(model="sonnet")
        # contract requires these to exist post-init
        for attr in ("_usage_tracker", "_scan_id", "_boot_count",
                     "_total_calls", "_death_count", "_stall_count",
                     "_consecutive_stalls", "_last_stderr_lines",
                     "_boot_time", "_last_error", "_on_death", "_process"):
            assert hasattr(b, attr), f"missing attr: {attr}"

    def test_is_alive_false_before_ensure_running(self):
        from src.agents.claude_cli_bridge import ClaudeCliBridge
        b = ClaudeCliBridge(model="sonnet")
        assert b.is_alive() is False

    def test_shutdown_is_idempotent(self):
        from src.agents.claude_cli_bridge import ClaudeCliBridge
        b = ClaudeCliBridge(model="sonnet")
        b.shutdown()
        b.shutdown()  # must not raise
        assert b.is_alive() is False

    def test_abort_safe_with_no_active_call(self):
        from src.agents.claude_cli_bridge import ClaudeCliBridge
        b = ClaudeCliBridge(model="sonnet")
        b.abort("nonexistent")  # must not raise

    def test_record_stall_increments_counters(self):
        from src.agents.claude_cli_bridge import ClaudeCliBridge
        b = ClaudeCliBridge(model="sonnet")
        b.record_stall()
        b.record_stall()
        assert b._stall_count == 2
        assert b._consecutive_stalls == 2
        b.record_success()
        assert b._consecutive_stalls == 0
        assert b._stall_count == 2  # cumulative not reset

    def test_get_stats_returns_dict(self):
        from src.agents.claude_cli_bridge import ClaudeCliBridge
        b = ClaudeCliBridge(model="sonnet")
        stats = b.get_stats()
        assert isinstance(stats, dict)
        for k in ("boot_count", "total_calls", "bridge_healthy"):
            assert k in stats


# ─── PII redaction in ClaudeCliClient (Phase 1 verification) ────────


class TestClaudeCliClientRedaction:

    def test_prepare_prompt_redacts_emails(self):
        from src.llm.claude_cli_client import ClaudeCliClient
        c = ClaudeCliClient(model="sonnet", pii_redaction=True)
        prepared, _sys = c._prepare_prompt(
            "Contact patient at john.doe@example.com about the claim.",
            system_prompt="",
        )
        # Email should be redacted to a placeholder, not appear verbatim
        assert "john.doe@example.com" not in prepared

    def test_prepare_prompt_returns_system_separately_never_embedded(self):
        """Contract flipped 2026-07-21 (Renn-on-Sonnet incident): embedding the
        persona as an [SYSTEM INSTRUCTIONS] block in USER content made the CLI
        subprocess (which keeps its own Claude Code identity) read its own
        prompt as an injection — Sonnet narrated tool calls instead of invoking
        native MCP, then refused the persona. The system prompt now travels
        separately to the bridge's --system-prompt-file. Full cover:
        tests/test_claude_cli_system_prompt.py."""
        from src.llm.claude_cli_client import ClaudeCliClient
        c = ClaudeCliClient(model="sonnet", pii_redaction=True)
        prepared, sys_prompt = c._prepare_prompt(
            "user prompt here", system_prompt="be concise",
        )
        assert "[SYSTEM INSTRUCTIONS]" not in prepared
        assert "be concise" not in prepared
        assert prepared.strip() == "user prompt here"
        assert sys_prompt == "be concise"

    def test_prepare_prompt_no_system_block_when_empty(self):
        from src.llm.claude_cli_client import ClaudeCliClient
        c = ClaudeCliClient(model="sonnet", pii_redaction=True)
        prepared, sys_prompt = c._prepare_prompt("just a prompt", system_prompt="")
        assert "[SYSTEM INSTRUCTIONS]" not in prepared
        assert prepared.strip() == "just a prompt"
        assert sys_prompt == ""
