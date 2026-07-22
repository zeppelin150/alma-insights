"""System prompt must reach the claude CLI as a REAL system prompt.

Regression cover for the 2026-07-21 Renn-on-Sonnet incident: the CLI path
embedded the persona in the USER prompt as an ``[SYSTEM INSTRUCTIONS]`` block
(claude_cli_client._prepare_prompt) and ``_build_cmd`` passed no system-prompt
flag — so every ``claude -p`` turn ran under the CLI's default "you are Claude
Code" identity and received a user message containing a fake system block plus
the replayed transcript. Haiku played along (with a measured ~25% tool-skip);
Sonnet correctly pattern-matched its own prompt as a prompt injection: it
narrated tool calls as text instead of invoking the natively-bound MCP tools,
then refused the persona outright ("I'm Claude Code... this is a fabricated
chat transcript"), naming the [SYSTEM INSTRUCTIONS] marker verbatim.

The contract pinned here:
  * ClaudeCliBridge.set_system_prompt(text) stores the persona in a temp FILE
    and _build_cmd() passes ``--system-prompt-file <path>`` (file, not inline
    arg: the Renn persona is >10 KB and Windows caps a command line at 32 767
    chars). Empty text clears the flag.
  * ClaudeCliClient routes system_prompt to the bridge separately — the user
    prompt it sends contains NO [SYSTEM INSTRUCTIONS] marker and NO persona
    text, on the blocking, on_token, and generate_streaming paths alike.
  * PII redaction still applies to the system prompt BEFORE it leaves the
    client (mandatory on ALL LLM calls).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agents.claude_cli_bridge import ClaudeCliBridge
from src.llm.claude_cli_client import ClaudeCliClient

PERSONA = "You are Renn, the enablement chief of staff. Be concise."


# ── bridge: the flag itself ──────────────────────────────────────────


def _bridge() -> ClaudeCliBridge:
    b = ClaudeCliBridge(model="sonnet")
    b._cli_path = "/fake/claude"
    return b


class TestBridgeSystemPromptFlag:
    def test_set_system_prompt_adds_the_file_flag(self, ):
        b = _bridge()
        b.set_system_prompt(PERSONA)
        cmd = b._build_cmd()
        assert "--system-prompt-file" in cmd
        path = cmd[cmd.index("--system-prompt-file") + 1]
        assert Path(path).read_text(encoding="utf-8") == PERSONA

    def test_persona_travels_by_file_never_inline(self):
        """A Renn-sized persona (10KB+) as an inline arg risks the Windows
        32,767-char command-line cap — the TEXT must never appear in cmd."""
        b = _bridge()
        big = "You are Renn.\n" + ("tool guidance line\n" * 3000)
        b.set_system_prompt(big)
        cmd = b._build_cmd()
        assert all(big not in part for part in cmd)
        assert "--system-prompt-file" in cmd

    def test_no_system_prompt_means_no_flag(self):
        b = _bridge()
        cmd = b._build_cmd()
        assert "--system-prompt-file" not in cmd
        assert "--system-prompt" not in cmd

    def test_clearing_removes_the_flag(self):
        b = _bridge()
        b.set_system_prompt(PERSONA)
        b.set_system_prompt("")
        cmd = b._build_cmd()
        assert "--system-prompt-file" not in cmd

    def test_same_text_reuses_the_same_file(self):
        """The persona is constant per session and set on every call — it must
        not mint a new temp file per turn."""
        b = _bridge()
        b.set_system_prompt(PERSONA)
        cmd1 = b._build_cmd()
        b.set_system_prompt(PERSONA)
        cmd2 = b._build_cmd()
        p1 = cmd1[cmd1.index("--system-prompt-file") + 1]
        p2 = cmd2[cmd2.index("--system-prompt-file") + 1]
        assert p1 == p2

    def test_updated_text_lands_in_the_file(self):
        b = _bridge()
        b.set_system_prompt(PERSONA)
        b.set_system_prompt("You are somebody else now.")
        cmd = b._build_cmd()
        path = cmd[cmd.index("--system-prompt-file") + 1]
        assert Path(path).read_text(encoding="utf-8") == "You are somebody else now."


# ── bridge robustness (adversarial-review findings, 2026-07-21) ──────


class TestBridgeRobustness:
    def test_failed_write_leaves_cache_stale_so_retry_rewrites(self, monkeypatch):
        """Review finding: recording the text BEFORE the write means a failed
        write poisons the no-op branch — every retry with the same text would
        no-op and the subprocess would run with stale persona content forever.
        The cache must record only after a durable write."""
        b = _bridge()
        b.set_system_prompt(PERSONA)
        real_write = Path.write_text

        def boom(self, *a, **k):
            raise OSError("sharing violation")

        monkeypatch.setattr(Path, "write_text", boom)
        with pytest.raises(OSError):
            b.set_system_prompt("You are v2.")
        assert b._system_prompt_text == PERSONA, "cache must stay stale on failure"
        monkeypatch.setattr(Path, "write_text", real_write)
        b.set_system_prompt("You are v2.")
        cmd = b._build_cmd()
        path = cmd[cmd.index("--system-prompt-file") + 1]
        assert Path(path).read_text(encoding="utf-8") == "You are v2."

    def test_purged_temp_file_is_recreated_even_for_same_text(self):
        """Review finding: %TEMP% cleaners can purge the file mid-session;
        the same-text no-op must verify the file still exists, not trust the
        cache — else every later turn points --system-prompt-file at nothing."""
        b = _bridge()
        b.set_system_prompt(PERSONA)
        Path(b._system_prompt_path).unlink()
        b.set_system_prompt(PERSONA)
        cmd = b._build_cmd()
        path = cmd[cmd.index("--system-prompt-file") + 1]
        assert Path(path).read_text(encoding="utf-8") == PERSONA

    def test_clear_unlinks_the_file(self):
        """Review finding: the persona text must not persist at rest in %TEMP%
        after the caller clears it."""
        b = _bridge()
        b.set_system_prompt(PERSONA)
        path = b._system_prompt_path
        b.set_system_prompt("")
        assert not Path(path).exists()

    def test_shutdown_unlinks_the_file(self):
        b = _bridge()
        b.set_system_prompt(PERSONA)
        path = b._system_prompt_path
        b.shutdown()
        assert not Path(path).exists()
        assert b._system_prompt_path is None


# ── neutral cwd + MCP module anchor (CLAUDE.md-injection finding) ────


class TestNeutralCwdAndMcpAnchor:
    def test_subprocess_runs_from_a_neutral_cwd_not_the_app_tree(self, monkeypatch):
        """Review finding (major, live-verified): the CLI walks UP from its cwd
        collecting CLAUDE.md files and injects them EVEN under a custom system
        prompt — from the app tree that shipped ~14.6K tokens of internal
        engineering directives into every Renn turn. The subprocess must run
        from a neutral directory outside the app tree."""
        from src.agents.claude_cli_bridge import _APP_ROOT
        captured = {}

        class FakeSub:
            def __init__(self, cmd, env, prompt, cwd=None):
                captured["cwd"] = cwd

            def start(self):
                raise RuntimeError("stop before real spawn")

        monkeypatch.setattr("src.agents.claude_cli_bridge.CliSubprocess", FakeSub)
        b = _bridge()
        with pytest.raises(RuntimeError):
            b._spawn_subprocess("hi")
        assert captured["cwd"], "a neutral cwd must be passed"
        assert not str(captured["cwd"]).startswith(str(_APP_ROOT))

    def test_mcp_env_gains_a_pythonpath_anchor(self):
        """The MCP servers inherit the neutral cwd, so `-m src.mcp...` can no
        longer resolve modules via cwd — the materialized config must anchor
        them to the app root explicitly."""
        import json
        from src.agents.claude_cli_bridge import _APP_ROOT
        b = _bridge()
        b.set_mcp_config([{"name": "alma-chat-tools", "command": "python",
                           "args": ["-m", "src.mcp.chat_mcp_server"],
                           "env": [{"name": "ALMA_DB_PATH", "value": "x.db"}]}])
        cfg = json.loads(Path(b._mcp_config_path).read_text(encoding="utf-8"))
        env = cfg["mcpServers"]["alma-chat-tools"]["env"]
        assert env["PYTHONPATH"] == str(_APP_ROOT)
        assert env["ALMA_DB_PATH"] == "x.db"

    def test_an_explicit_pythonpath_in_the_server_spec_wins(self):
        import json
        b = _bridge()
        b.set_mcp_config([{"name": "s", "command": "python", "args": [],
                           "env": [{"name": "PYTHONPATH", "value": "custom"}]}])
        cfg = json.loads(Path(b._mcp_config_path).read_text(encoding="utf-8"))
        assert cfg["mcpServers"]["s"]["env"]["PYTHONPATH"] == "custom"


# ── client: routing + the injection-shaped user prompt ───────────────


class _Event:
    def __init__(self, type_, data):
        self.type = type_
        self.data = data


class RecorderBridge:
    """Stands in for ClaudeCliBridge: records what the client hands it."""

    def __init__(self):
        self.system_prompts: list[str] = []
        self.prompts: list[str] = []

    def set_system_prompt(self, text):
        self.system_prompts.append(text or "")

    def set_mcp_config(self, cfg):
        pass

    def is_alive(self):
        return True

    def call_blocking(self, prompt, request_id, timeout=120):
        self.prompts.append(prompt)
        return "ok"

    def call_streaming(self, prompt, request_id, on_token=None, timeout=120):
        self.prompts.append(prompt)
        if on_token:
            on_token(_Event("content", {"delta": "ok"}))
        return {"full_text": "ok"}

    def shutdown(self):
        pass


@pytest.fixture()
def client_and_bridge(monkeypatch):
    c = ClaudeCliClient(model="sonnet", pii_redaction=True)
    rec = RecorderBridge()
    monkeypatch.setattr(c, "_ensure_bridge", lambda: rec)
    return c, rec


class TestClientRoutesSystemPromptNatively:
    def test_blocking_path_keeps_the_persona_out_of_the_user_prompt(
            self, client_and_bridge):
        """THE regression: the persona embedded in user content is exactly what
        Sonnet flagged as an injection. It must reach the bridge separately."""
        c, rec = client_and_bridge
        c.generate("hello", system_prompt=PERSONA)
        assert rec.prompts == ["hello"]
        assert "[SYSTEM INSTRUCTIONS]" not in rec.prompts[0]
        assert PERSONA not in rec.prompts[0]
        assert rec.system_prompts and rec.system_prompts[-1] == PERSONA

    def test_on_token_path_routes_it_too(self, client_and_bridge):
        c, rec = client_and_bridge
        seen = []
        c.generate("hello", system_prompt=PERSONA, on_token=seen.append)
        assert seen == ["ok"]
        assert rec.prompts == ["hello"]
        assert rec.system_prompts and rec.system_prompts[-1] == PERSONA

    def test_generate_streaming_routes_it_too(self, client_and_bridge):
        c, rec = client_and_bridge
        out = list(c.generate_streaming("hello", system_prompt=PERSONA))
        assert out == ["ok"]
        assert rec.prompts == ["hello"]
        assert rec.system_prompts and rec.system_prompts[-1] == PERSONA

    def test_empty_system_prompt_clears_the_bridge_persona(
            self, client_and_bridge):
        """A persona-less call after a persona call must not inherit Renn."""
        c, rec = client_and_bridge
        c.generate("first", system_prompt=PERSONA)
        c.generate("second", system_prompt="")
        assert rec.system_prompts[-1] == ""

    def test_system_prompt_is_redacted_before_leaving_the_client(
            self, client_and_bridge):
        """PII redaction is mandatory on ALL LLM calls — moving the persona out
        of the user prompt must not carry it around the redaction layer."""
        c, rec = client_and_bridge
        c.generate("hello",
                   system_prompt="Escalations go to john.doe@example.com.")
        assert rec.system_prompts
        assert "john.doe@example.com" not in rec.system_prompts[-1]

    def test_user_prompt_is_still_redacted(self, client_and_bridge):
        c, rec = client_and_bridge
        c.generate("Patient mail: jane.roe@example.com", system_prompt=PERSONA)
        assert "jane.roe@example.com" not in rec.prompts[0]
