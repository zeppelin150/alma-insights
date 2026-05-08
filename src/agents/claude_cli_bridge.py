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

        # Compatibility surface mirroring ACPBridge — death callbacks
        # in scan_orchestrator read these by name.
        self._usage_tracker = None
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
        """Store MCP server config. Not yet wired into the CLI invocation —
        Claude CLI uses a different MCP config schema than Gemini's ACP."""
        self._mcp_servers = server_config

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
        (--input-format text), avoiding the Windows arg-length limit."""
        return [
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

    def _spawn_subprocess(self, prompt: str) -> CliSubprocess:
        """Build cmd + env, start subprocess, register for atexit cleanup."""
        cmd = self._build_cmd()
        env = self._build_subprocess_env()
        sub = CliSubprocess(cmd, env, prompt).start()
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

    def _log_usage_if_configured(
        self, stream: StreamResult, prompt: str,
    ) -> None:
        """Best-effort usage tracking matching ACPBridge semantics."""
        if not self._usage_tracker or stream.parser.error:
            return
        parser = stream.parser
        try:
            self._usage_tracker.log_call(
                source="nlp_scan",
                tokens_in=parser.input_tokens
                or self._usage_tracker.estimate_tokens(prompt),
                tokens_out=parser.output_tokens
                or self._usage_tracker.estimate_tokens(parser.full_text),
                scan_id=self._scan_id,
                model=self._model,
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
