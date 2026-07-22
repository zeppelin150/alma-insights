"""
Alma Insights -- Claude CLI Bridge

Drop-in replacement for ACPBridge that drives the local ``claude`` CLI in
``-p --output-format stream-json`` mode. Inherits whatever auth the user has
already configured for ``claude`` (OAuth, ANTHROPIC_API_KEY, or — when
``bedrock.enabled`` in settings — Bedrock via env-var injection).

Each ``call_streaming`` spawns a fresh ``claude`` subprocess. The bridge
itself does not hold a persistent session (CLI ``-p`` mode does not support
one); instead it composes:

  - ``CliSubprocess`` for Popen lifecycle and stderr drain
  - ``StreamParser`` for the JSON-line → ``BridgeEvent`` translation
  - ``_build_subprocess_env`` for Bedrock env-var injection

The public interface mirrors ACPBridge so worker_agent / scan_orchestrator /
report_orchestrator all work without changes (see ``bridge_protocol.py``
for the contract).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from src.agents.acp_bridge import BridgeEvent
from src.agents.claude_cli_stream import StreamParser, StreamResult
from src.agents.claude_cli_subprocess import CliSubprocess

logger = logging.getLogger("alma.claude_cli_bridge")

# App root (the directory holding src/) — anchors PYTHONPATH for MCP servers
# once the CLI runs from a neutral cwd. Path(__file__)-based, never cwd-based.
_APP_ROOT = Path(__file__).resolve().parents[2]


class ClaudeCliBridge:
    """Subprocess-per-call bridge wrapping the local ``claude`` CLI."""

    _all_processes: list[subprocess.Popen] = []
    _atexit_registered = False

    @classmethod
    def _register_atexit(cls) -> None:
        if not cls._atexit_registered:
            import atexit
            atexit.register(cls._cleanup_all)
            cls._atexit_registered = True

    @classmethod
    def _cleanup_all(cls) -> None:
        for proc in cls._all_processes:
            try:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
            except Exception:
                pass
        cls._all_processes.clear()

    def __init__(
        self,
        bridge_script: str | None = None,
        node_path: str | None = None,
        model: str | None = None,
    ) -> None:
        # Backward-compat positional args (accepted but unused)
        self._bridge_script = bridge_script
        self._node_path = node_path
        self._model = model or "sonnet"

        self._cli_path: str | None = None
        self._lock = threading.Lock()

        # Active subprocess + request id (for abort routing)
        self._active_proc: subprocess.Popen | None = None
        self._active_request_id: str | None = None
        self._active_proc_lock = threading.Lock()

        self._mcp_servers: list[dict] = []
        self._mcp_config_path: str | None = None
        self._mcp_allowed: str = ""

        # Persona for --system-prompt-file (set_system_prompt). File-backed:
        # the Renn persona is >10 KB and Windows caps a command line at 32,767
        # chars, so the TEXT never rides in argv.
        self._system_prompt_text: str = ""
        self._system_prompt_path: str | None = None

        # Compatibility surface mirroring ACPBridge — death callbacks
        # in scan_orchestrator read these by name. scan_orchestrator assigns
        # _usage_tracker directly; _usage_source defaults to its historical
        # value so that path keeps logging exactly as before. Other hosts
        # (Renn chat) use set_usage_sink to pick their own source.
        self._usage_tracker = None
        self._usage_source = "nlp_scan"
        self._scan_id = None
        self._boot_count = 0
        self._last_stderr_lines: list[str] = []
        self._total_calls = 0
        self._last_error: str | None = None
        self._bridge_healthy = False
        self._death_count = 0
        self._stall_count = 0
        self._consecutive_stalls = 0
        self._last_death_time: str | None = None
        self._boot_time: str | None = None
        self._on_death: Callable | None = None
        self._process = None
        self._session_id: str | None = None
        self._protocol_version: int | None = None
        self._agent_info: dict = {}

    # ─── Lifecycle ──────────────────────────────────────────────

    def ensure_running(self) -> None:
        """Locate the ``claude`` CLI and mark the bridge as healthy."""
        with self._lock:
            if self._cli_path and self._bridge_healthy:
                return
            cli_path = self._find_claude_cli()
            if not cli_path:
                raise FileNotFoundError(
                    "Claude CLI not found. Install via 'npm install -g "
                    "@anthropic-ai/claude-code' or set claude.cli_path in settings."
                )
            self._cli_path = cli_path
            self._boot_count += 1
            self._bridge_healthy = True
            self._boot_time = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._session_id = f"cli-session-{int(time.time())}"
            ClaudeCliBridge._register_atexit()
            logger.info(
                "ClaudeCliBridge: ready (cli=%s, model=%s)",
                cli_path, self._model,
            )

    def _find_claude_cli(self) -> str | None:
        """Locate the ``claude`` binary, preferring an explicit settings path."""
        try:
            from src.data.settings_manager import get_section
            cfg = get_section("claude", {}) or {}
            cli_path = cfg.get("cli_path", "")
            if cli_path and Path(cli_path).exists():
                return cli_path
        except Exception:
            pass
        for name in ("claude", "claude.cmd", "claude.exe"):
            found = shutil.which(name)
            if found:
                return found
        return None

    def set_mcp_config(self, server_config: list[dict]) -> None:
        """Materialize a Claude-CLI mcp-config.json from the ACP-style server list
        and compute the allowed-tools pattern. An empty list disables MCP (the CLI
        then runs with no tools — used by the Gemini / text-loop path).

        Claude rejects text-injected TOOL_RESULTs as prompt injection, so the
        Claude chat path MUST expose tools natively via MCP. This wires the same
        chat_mcp_server the model already speaks.

          ACP-style entry: {"name", "command", "args", "env": [{"name","value"}]}
          CLI mcp-config:  {"mcpServers": {name: {"type":"stdio","command","args","env":{}}}}
        """
        import json
        import re
        import tempfile

        self._mcp_servers = server_config or []
        self._mcp_config_path = None
        self._mcp_allowed = ""
        if not self._mcp_servers:
            return

        servers: dict = {}
        allowed: list[str] = []
        for s in self._mcp_servers:
            name = s["name"]
            env = {e["name"]: e["value"] for e in s.get("env", []) if e.get("name")}
            # The CLI (and therefore the MCP servers it spawns) runs from a
            # NEUTRAL cwd (see _neutral_cwd), so a "-m src.mcp...." server can
            # no longer resolve modules via cwd — anchor module resolution to
            # the app root explicitly. setdefault: an explicit PYTHONPATH in
            # the server spec wins.
            env.setdefault("PYTHONPATH", str(_APP_ROOT))
            servers[name] = {
                "type": "stdio",
                "command": s["command"],
                "args": s.get("args", []),
                "env": env,
            }
            # Claude normalizes non-alphanumerics to '_' in the mcp__ tool prefix
            allowed.append(f"mcp__{re.sub(r'[^A-Za-z0-9_]', '_', name)}__*")

        fd, path = tempfile.mkstemp(prefix="alma_mcp_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"mcpServers": servers}, f)
        self._mcp_config_path = path
        self._mcp_allowed = ",".join(allowed)
        logger.info("ClaudeCliBridge: MCP wired (%d server(s); allow=%s)",
                    len(servers), self._mcp_allowed)

    def set_system_prompt(self, text: str) -> None:
        """Deliver ``text`` as the subprocess's REAL system prompt.

        Without this, ``claude -p`` runs under its default "you are Claude
        Code" identity and the caller's persona arrives as user content — a
        user message claiming system authority plus a replayed transcript,
        which is structurally a prompt injection. Haiku tolerated that frame
        (with a measured ~25% tool-skip); Sonnet correctly refused it
        (2026-07-21: narrated tool calls as text instead of invoking the
        natively-bound MCP tools, then answered as Claude Code). A real
        ``--system-prompt`` REPLACES the default Claude Code preamble, so the
        persona is the model's own frame and the transcript is its own
        conversation. It does NOT stop everything else (live-verified on
        2.1.216): CLAUDE.md ingestion is cwd-driven and survives a custom
        system prompt — that leak is closed by running the subprocess from a
        neutral cwd (see ``_neutral_cwd``) — and the CLI still injects the
        logged-in account's email + today's date on its own.

        File-backed (``--system-prompt-file``), mirroring set_mcp_config:
        argv must never carry the >10 KB persona (Windows 32,767-char cap).
        The persona is constant per session but re-set on every call, so the
        one temp file is REUSED — same text with the file intact is a no-op,
        new text (or a purged file: %TEMP% cleaners run mid-session) rewrites
        it, empty text unlinks it and clears the flag entirely (the CLI then
        keeps its default identity — the Gemini/text-loop path and
        pre-stitched-prompt callers like the report bridge stay byte-identical).
        ``_system_prompt_text`` is recorded only AFTER a durable write: a
        failed write must leave the cache stale so the next call retries
        instead of no-op'ing forever on poisoned on-disk state.
        """
        import tempfile

        text = text or ""
        if not text:
            self._system_prompt_text = ""
            path, self._system_prompt_path = self._system_prompt_path, None
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            return
        if (text == self._system_prompt_text and self._system_prompt_path
                and os.path.isfile(self._system_prompt_path)):
            return  # steady state: file present AND known to hold this text
        if not (self._system_prompt_path
                and os.path.isfile(self._system_prompt_path)):
            fd, path = tempfile.mkstemp(prefix="alma_sysprompt_", suffix=".txt")
            os.close(fd)
            self._system_prompt_path = path
        Path(self._system_prompt_path).write_text(text, encoding="utf-8")
        self._system_prompt_text = text

    def new_session(self, mcp_env: dict | None = None) -> str:
        """Compatibility shim. CLI -p mode has no persistent session;
        return a synthetic id so callers wanting one have something."""
        self._session_id = f"cli-session-{int(time.time() * 1000)}"
        return self._session_id

    def set_on_death(self, callback: Callable | None) -> None:
        self._on_death = callback

    def is_alive(self) -> bool:
        return self._bridge_healthy

    # ─── Subprocess env + cmd construction ──────────────────────

    def _build_subprocess_env(self) -> dict:
        """Construct the env dict for the ``claude`` subprocess.

        Inherits ``os.environ`` and conditionally injects Bedrock variables
        when ``bedrock.enabled=true`` in settings. Returns a fresh dict so
        callers can safely mutate without affecting the parent process.

        Bedrock activation:
          ``CLAUDE_CODE_USE_BEDROCK=1`` flips Claude Code's backend.
          ``AWS_REGION`` is set if configured.
          When ``bedrock.use_environment=false``, AWS credentials are read
          from pat_store and injected; otherwise the standard AWS credential
          chain (env / profile / IAM) is honored.
        """
        env = os.environ.copy()
        try:
            from src.data.settings_manager import get_section
            bedrock_cfg = get_section("bedrock", {}) or {}
        except Exception:
            bedrock_cfg = {}

        if not bedrock_cfg.get("enabled"):
            return env

        env["CLAUDE_CODE_USE_BEDROCK"] = "1"
        region = bedrock_cfg.get("region", "")
        if region:
            env["AWS_REGION"] = region

        if not bedrock_cfg.get("use_environment", True):
            self._inject_aws_credentials(env)

        return env

    def _inject_aws_credentials(self, env: dict) -> None:
        """Read AWS keys from pat_store and inject into env (in-place)."""
        try:
            from src.data.pat_store import load_setting
            for key in ("aws_access_key_id", "aws_secret_access_key",
                        "aws_session_token"):
                val = load_setting(key, "")
                if val:
                    env[key.upper()] = val
        except Exception as e:
            logger.warning("Bedrock credential injection failed: %s", e)

    def _build_cmd(self) -> list[str]:
        """Construct the ``claude -p`` invocation. Stdin carries the prompt
        (--input-format text), avoiding the Windows arg-length limit.

        When an MCP config is wired (set_mcp_config with a non-empty list), the
        chat tools are exposed natively and pre-allowed so the model calls them
        through the genuine tool-use channel — Claude refuses text-injected tool
        results, so native MCP is the only reliable tool path for the CLI."""
        cmd = [
            self._cli_path,
            "-p",
            "--input-format", "text",
            "--output-format", "stream-json",
            "--include-partial-messages",
            "--verbose",  # required by the CLI when -p + stream-json
            "--model", self._model,
            "--tools", "",  # disable built-in tools (Bash/Edit/Read/etc)
            "--no-session-persistence",
        ]
        if self._system_prompt_path:
            # The caller's persona as the REAL system prompt (replaces the
            # default Claude Code identity — see set_system_prompt).
            cmd += ["--system-prompt-file", self._system_prompt_path]
        if self._mcp_config_path:
            cmd += [
                "--strict-mcp-config",            # ignore user/global MCP config
                "--mcp-config", self._mcp_config_path,
                "--allowedTools", self._mcp_allowed,  # only our MCP tools
                # bypassPermissions actually EXECUTES the pre-allowed MCP tools
                # unattended. 'dontAsk' / 'default' / 'acceptEdits' all DENY MCP
                # tool calls in -p mode (the model then fabricates tool results —
                # the original UAT failure). Verified live: only bypassPermissions
                # runs the allow-listed mcp__alma_chat_tools__* tools.
                "--permission-mode", "bypassPermissions",
            ]
        return cmd

    # ─── Call interfaces ────────────────────────────────────────

    def call_streaming(
        self,
        prompt: str,
        request_id: str,
        on_token: Callable[[BridgeEvent], None] | None = None,
        timeout: int = 300,
        early_stop: Callable[[], bool] | None = None,
    ) -> dict:
        """Spawn ``claude -p``, parse stream-json events, emit BridgeEvents.

        Returns dict shaped identically to ACPBridge.call_streaming().
        """
        self.ensure_running()
        sub = self._spawn_subprocess(prompt)
        self._track_active(sub.proc, request_id)
        self._total_calls += 1

        try:
            stream = self._consume_stream(
                sub, request_id, timeout, on_token, early_stop,
            )
        finally:
            self._untrack_active(sub.proc)
            self._unregister_proc(sub.proc)

        result = self._build_result_dict(stream)
        self._emit_terminal_event(stream, request_id, on_token)
        self._log_usage_if_configured(stream, prompt)
        return result

    def call_blocking(self, prompt: str, request_id: str,
                      timeout: int = 300) -> str:
        """Convenience wrapper: call_streaming + raise on error, return text."""
        result = self.call_streaming(prompt, request_id, timeout=timeout)
        if result.get("error"):
            raise RuntimeError(
                f"Claude CLI call failed: {result['error']} - "
                f"{result.get('message', '')}"
            )
        return result["full_text"]

    def abort(self, request_id: str) -> None:
        """Kill the active subprocess if it matches ``request_id``."""
        with self._active_proc_lock:
            if self._active_request_id == request_id and self._active_proc:
                self._kill_proc(self._active_proc)

    # ─── call_streaming helpers ─────────────────────────────────

    @staticmethod
    def _neutral_cwd() -> str | None:
        """A stable empty directory to run the CLI from.

        The claude CLI walks UP from its cwd collecting CLAUDE.md files and
        injects them into the model context EVEN under a custom
        --system-prompt-file (live-verified on 2.1.216: launched from the app
        tree, every turn shipped ~14.6K tokens of this repo's internal
        engineering instructions into the Renn persona's context — the exact
        identity-bleed the system-prompt fix exists to eliminate, plus cost).
        An empty dir under %TEMP% has no CLAUDE.md anywhere above it. The MCP
        servers the CLI spawns inherit this cwd too — which is why
        set_mcp_config anchors them with an explicit PYTHONPATH (settings/DB
        paths are already absolute). Returns None (inherit, legacy behavior)
        only if the dir can't be created.
        """
        import tempfile
        try:
            path = Path(tempfile.gettempdir()) / "alma_cli_neutral"
            path.mkdir(parents=True, exist_ok=True)
            return str(path)
        except OSError:
            return None

    def _spawn_subprocess(self, prompt: str) -> CliSubprocess:
        """Build cmd + env, start subprocess, register for atexit cleanup."""
        cmd = self._build_cmd()
        env = self._build_subprocess_env()
        sub = CliSubprocess(cmd, env, prompt, cwd=self._neutral_cwd()).start()
        ClaudeCliBridge._all_processes.append(sub.proc)
        return sub

    def _track_active(self, proc, request_id: str) -> None:
        with self._active_proc_lock:
            self._active_proc = proc
            self._active_request_id = request_id

    def _untrack_active(self, proc) -> None:
        with self._active_proc_lock:
            if self._active_proc is proc:
                self._active_proc = None
                self._active_request_id = None

    def _unregister_proc(self, proc) -> None:
        try:
            ClaudeCliBridge._all_processes.remove(proc)
        except ValueError:
            pass

    def _consume_stream(
        self,
        sub: CliSubprocess,
        request_id: str,
        timeout: int,
        on_token: Callable[[BridgeEvent], None] | None,
        early_stop: Callable[[], bool] | None,
    ) -> StreamResult:
        """Read stdout lines, dispatch through StreamParser, collect events."""
        parser = StreamParser(request_id)
        events: list[BridgeEvent] = []
        t0 = time.time()
        deadline = t0 + timeout
        early_stopped = False

        for raw_line in sub.stdout_iter():
            if time.time() > deadline:
                sub.kill()
                raise TimeoutError(
                    f"Claude CLI call {request_id} timed out after {timeout}s"
                )
            if early_stop and self._check_early_stop(early_stop):
                sub.kill()
                early_stopped = True
                break

            bev = parser.parse_line(raw_line)
            if bev is None:
                continue
            events.append(bev)
            self._safe_on_token(on_token, bev)

        if not early_stopped:
            self._wait_for_exit(sub, deadline, request_id, timeout, parser)

        elapsed_ms = int((time.time() - t0) * 1000)
        return StreamResult(
            parser=parser, events=events,
            elapsed_ms=elapsed_ms, early_stopped=early_stopped,
        )

    def _wait_for_exit(
        self, sub: CliSubprocess, deadline: float, request_id: str,
        timeout: int, parser: StreamParser,
    ) -> None:
        """Drain process exit and translate non-zero exit into a parser error."""
        try:
            sub.wait(timeout=max(1.0, deadline - time.time()))
        except subprocess.TimeoutExpired:
            sub.kill()
            raise TimeoutError(
                f"Claude CLI call {request_id} timed out after {timeout}s"
            )
        if sub.returncode and sub.returncode != 0:
            parser.mark_nonzero_exit(sub.returncode, sub.stderr_tail())
            self._last_stderr_lines = list(sub.stderr_lines)

    def _check_early_stop(self, early_stop: Callable[[], bool]) -> bool:
        try:
            return bool(early_stop())
        except Exception as e:
            logger.warning("early_stop callback error: %s", e)
            return False

    def _safe_on_token(
        self, on_token: Callable[[BridgeEvent], None] | None,
        event: BridgeEvent,
    ) -> None:
        if on_token is None:
            return
        try:
            on_token(event)
        except Exception as e:
            logger.warning("on_token callback error: %s", e)

    def _build_result_dict(self, stream: StreamResult) -> dict:
        """Translate StreamResult into the dict shape ACPBridge returns."""
        parser = stream.parser
        result = {
            "full_text": parser.full_text,
            "elapsed_ms": stream.elapsed_ms,
            "turns": 1,
            "events": stream.events,
            "error": parser.error,
            "input_tokens": parser.input_tokens,
            "output_tokens": parser.output_tokens,
        }
        if stream.early_stopped:
            result["early_stopped"] = True
            return result
        if parser.error:
            result["message"] = parser.error_message
            result["recoverable"] = parser.error == "claude_cli_nonzero_exit"
            self._last_error = parser.error
        return result

    def _emit_terminal_event(
        self, stream: StreamResult, request_id: str,
        on_token: Callable[[BridgeEvent], None] | None,
    ) -> None:
        """Synthesize and dispatch the terminal done/error event."""
        if stream.early_stopped:
            return
        parser = stream.parser
        terminal = BridgeEvent(
            id=request_id,
            type="done" if parser.error is None else "error",
            data={
                "stopReason": parser.stop_reason,
                "input_tokens": parser.input_tokens,
                "output_tokens": parser.output_tokens,
                "cost_usd": parser.cost_usd,
                "error": parser.error,
                "message": parser.error_message,
                "recoverable": parser.error == "claude_cli_nonzero_exit",
            },
        )
        stream.events.append(terminal)
        self._safe_on_token(on_token, terminal)

    def set_usage_sink(self, sink, source: str = "nlp_scan",
                       scan_id: str | None = None) -> None:
        """Attach a usage sink (UsageTracker / RennUsageRecorder shape).

        ``source`` names the ledger row's origin (e.g. ``renn_chat``), so one
        gemini_usage table serves scan, report and assistant accounting.
        """
        self._usage_tracker = sink
        self._usage_source = source or "nlp_scan"
        if scan_id is not None:
            self._scan_id = scan_id

    def _log_usage_if_configured(
        self, stream: StreamResult, prompt: str,
    ) -> None:
        """Best-effort usage tracking matching ACPBridge semantics.

        Real counts from the CLI's stream/result events win; token estimates
        are only the fallback. ``cost_usd`` passes the CLI's reported
        ``total_cost_usd`` through verbatim (None when unreported — e.g. a
        subscription login — so the sink records 0 rather than a Gemini-priced
        guess for a Claude turn).
        """
        if not self._usage_tracker or stream.parser.error:
            return
        parser = stream.parser
        try:
            self._usage_tracker.log_call(
                source=self._usage_source,
                tokens_in=parser.input_tokens
                or self._usage_tracker.estimate_tokens(prompt),
                tokens_out=parser.output_tokens
                or self._usage_tracker.estimate_tokens(parser.full_text),
                scan_id=self._scan_id,
                model=self._model,
                cost_usd=(parser.cost_usd or None),
            )
        except Exception as e:
            logger.debug("Usage tracking failed: %s", e)

    def _kill_proc(self, proc: subprocess.Popen) -> None:
        try:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
        except Exception:
            pass

    # ─── Health ────────────────────────────────────────────────

    def ping(self, timeout: int = 10) -> dict | None:
        if not self._bridge_healthy:
            return None
        return {
            "status": "ok",
            "alive": True,
            "session_id": self._session_id,
            "boot_count": self._boot_count,
            "total_calls": self._total_calls,
            "protocol_version": None,
        }

    def probe(self, timeout: int = 30) -> dict | None:
        if not self._bridge_healthy:
            return None
        probe_id = f"probe_{int(time.time() * 1000)}"
        t0 = time.time()
        try:
            result = self.call_streaming(
                "Respond with exactly one word: OK",
                probe_id, timeout=timeout,
            )
            latency_ms = int((time.time() - t0) * 1000)
            status = "error" if result.get("error") else "success"
            return {
                "latency_ms": latency_ms,
                "status": status,
                "error": result.get("error"),
            }
        except TimeoutError:
            return {
                "latency_ms": int((time.time() - t0) * 1000),
                "status": "timeout", "error": "probe_timeout",
            }
        except Exception as e:
            return {
                "latency_ms": int((time.time() - t0) * 1000),
                "status": "error", "error": str(e),
            }

    # ─── Shutdown / Restart ────────────────────────────────────

    def shutdown(self, timeout: int = 10) -> None:
        self._bridge_healthy = False
        with self._active_proc_lock:
            if self._active_proc:
                self._kill_proc(self._active_proc)
                self._active_proc = None
                self._active_request_id = None
        # The persona temp file must not outlive the bridge — it holds the
        # (redacted) system-prompt text at rest in %TEMP%.
        path, self._system_prompt_path = self._system_prompt_path, None
        self._system_prompt_text = ""
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass

    def restart(self) -> None:
        # No persistent process; reset healthy flag and re-resolve cli_path
        self.shutdown()
        self._cli_path = None
        self.ensure_running()

    def record_stall(self) -> None:
        self._stall_count += 1
        self._consecutive_stalls += 1

    def record_success(self) -> None:
        self._consecutive_stalls = 0

    def get_stats(self) -> dict:
        return {
            "boot_count": self._boot_count,
            "total_calls": self._total_calls,
            "bridge_healthy": self._bridge_healthy,
            "death_count": self._death_count,
            "stall_count": self._stall_count,
            "consecutive_stalls": self._consecutive_stalls,
            "boot_time": self._boot_time,
            "last_error": self._last_error,
        }

    def __repr__(self):
        return (
            f"ClaudeCliBridge(model={self._model!r}, "
            f"healthy={self._bridge_healthy})"
        )

    def __del__(self):
        try:
            self.shutdown()
        except Exception:
            pass
