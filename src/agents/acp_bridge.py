"""
Alma Insights -- ACP Bridge (Session 1 — ACP Migration)

Drop-in replacement for GeminiBridge that communicates with the Gemini CLI
via its official ACP (Agent Client Protocol) mode (``gemini --acp``).

Transport: JSON-RPC 2.0 over stdio (stdin/stdout).
Auth: OAuth via cached credentials (``~/.gemini/google_accounts.json``).

The public interface is IDENTICAL to GeminiBridge so that all consumers
(ReportBridgeClient, ScanOrchestrator, WorkerAgent, etc.) can swap with
a 2-line import change.

Thread safety: All public methods are thread-safe. The reader thread
dispatches events to per-request queues; callers block on their own queue.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import logging
from collections.abc import Callable
from pathlib import Path
from queue import Queue, Empty

logger = logging.getLogger("alma.acp_bridge")


# ── Error categories (mirror GeminiBridge's classification) ──────────
RECOVERABLE_ERRORS = frozenset({
    "rate_limit", "server_error", "network_timeout",
    "connection_reset", "stall_timeout", "aborted_by_client",
})

FATAL_ERRORS = frozenset({
    "auth_expired", "forbidden", "model_not_found",
    "config_error", "bridge_fatal",
})

# JSON-RPC error codes → our error categories
_JSONRPC_RECOVERABLE_CODES = frozenset({
    -32000,   # server error (generic)
    -32603,   # internal error
    429,      # rate limit (non-standard but possible)
})

_JSONRPC_FATAL_CODES = frozenset({
    -32600,   # invalid request
    -32601,   # method not found
    -32602,   # invalid params
    -32700,   # parse error
})

# Terminal event types (exactly one per call)
TERMINAL_EVENTS = frozenset({"done", "error", "stopped"})


class BridgeEvent:
    """Parsed event from the ACP stdout JSON-RPC stream.

    Preserves the exact same interface as the original BridgeEvent from
    gemini_bridge_wrapper.py so that all on_token callbacks work unchanged.
    """

    __slots__ = ("id", "type", "data")

    def __init__(self, id: str, type: str, data: dict) -> None:
        self.id = id        # request ID this event belongs to
        self.type = type    # content, heartbeat, tool_call, tool_result, done, error, stopped
        self.data = data    # payload dict

    @property
    def is_terminal(self) -> bool:
        return self.type in TERMINAL_EVENTS

    @property
    def is_error(self) -> bool:
        return self.type == "error"

    @property
    def is_recoverable(self) -> bool:
        return self.data.get("recoverable", False)

    @property
    def error_code(self) -> str:
        return self.data.get("error", "")

    def __repr__(self):
        return f"BridgeEvent(id={self.id!r}, type={self.type!r})"


class ACPBridge:
    """
    Python wrapper for ``gemini --acp`` (JSON-RPC 2.0 over stdio).

    Drop-in replacement for GeminiBridge. Identical public interface:
    ensure_running(), call_streaming(), call_blocking(), abort(), ping(),
    probe(), shutdown(), restart(), record_stall(), record_success(),
    set_on_death(), is_alive(), get_stats().

    Usage:
        bridge = ACPBridge()
        bridge.ensure_running()
        result = bridge.call_blocking("Classify these tickets...", "batch_1")
        bridge.shutdown()
    """

    # ── Class-level process registry for atexit cleanup ──
    _all_processes: list["subprocess.Popen"] = []
    _atexit_registered = False
    _zombie_sweep_done = False

    @classmethod
    def _register_atexit(cls):
        """Register a one-time atexit handler to kill all orphaned bridges."""
        if not cls._atexit_registered:
            import atexit
            atexit.register(cls._cleanup_all)
            cls._atexit_registered = True

    @classmethod
    def _sweep_zombies_once(cls) -> None:
        """Pre-boot zombie sweep — fires once per session before the first
        bridge boot. Hardens against the hard-kill / GC-crash leakage path
        that the atexit handler can't catch (memory: zombie_processes.md)."""
        if cls._zombie_sweep_done:
            return
        cls._zombie_sweep_done = True
        try:
            from src.agents.bridge_zombie_sweep import sweep_zombie_bridges
            result = sweep_zombie_bridges()
            if result.total_killed:
                import logging
                logging.getLogger("alma.acp_bridge").info(
                    "pre-boot zombie sweep: killed %d orphan(s); pids=%s",
                    result.total_killed, result.terminated_pids,
                )
        except Exception as exc:
            import logging
            logging.getLogger("alma.acp_bridge").debug(
                "zombie sweep failed (non-fatal): %s", exc,
            )

    @classmethod
    def _cleanup_all(cls):
        """Kill every subprocess spawned by any ACPBridge instance."""
        for proc in cls._all_processes:
            try:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
            except Exception:
                pass
        cls._all_processes.clear()

    def __del__(self):
        """Safety net: kill subprocess if bridge is garbage collected."""
        proc = getattr(self, '_process', None)
        if proc and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=3)
            except Exception:
                pass

    def __init__(
        self,
        bridge_script: str | None = None,
        node_path: str | None = None,
        model: str | None = None,
    ) -> None:
        """
        Args:
            bridge_script: Ignored (backward compat with GeminiBridge).
            node_path: Ignored (backward compat with GeminiBridge).
            model: Gemini model name (e.g. "gemini-2.5-flash"). Passed
                   as --model flag to the ACP subprocess.
        """
        # Backward compat params (accepted but unused)
        self._bridge_script = bridge_script
        self._node_path = node_path
        self._model = model

        self._process: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._lock = threading.Lock()

        # Per-request event queues: jsonrpc_id (int) -> Queue[BridgeEvent]
        self._queues: dict[int, Queue] = {}
        self._queues_lock = threading.Lock()

        # Maps request_id (str) -> jsonrpc id (int) for routing
        self._request_id_map: dict[str, int] = {}
        self._request_id_map_lock = threading.Lock()

        # Serialize stdin writes
        self._send_lock = threading.Lock()

        # JSON-RPC message ID counter
        self._msg_id = 0
        self._msg_id_lock = threading.Lock()

        # ACP session state
        self._session_id: str | None = None
        self._protocol_version: int | None = None
        self._agent_info: dict = {}

        # MCP server config (set via set_mcp_config before session/new)
        self._mcp_servers: list[dict] = []

        # Boot readiness signal
        self._ready_event = threading.Event()

        # Usage tracking (GeminiBridge compat)
        self._usage_tracker = None
        self._scan_id = None

        # Stats
        self._boot_count = 0
        self._last_stderr_lines: list[str] = []
        self._total_calls = 0
        self._last_error: str | None = None

        # Health monitor (identical to GeminiBridge 6.2)
        self._watchdog_thread: threading.Thread | None = None
        self._bridge_healthy = False
        self._death_count = 0
        self._stall_count = 0
        self._consecutive_stalls = 0
        self._last_death_time: str | None = None
        self._boot_time: str | None = None
        self._on_death: Callable | None = None
        self._watchdog_stop = threading.Event()
        self._WATCHDOG_INTERVAL = 3.0
        self._STALL_ESCALATION_THRESHOLD = 3

        # Heartbeat timer state
        self._heartbeat_timer: threading.Timer | None = None
        self._last_event_time: float = 0.0
        self._active_request_id: str | None = None

    # ──────────────────────────────────────────────────────────────────
    # JSON-RPC helpers
    # ──────────────────────────────────────────────────────────────────

    def _next_id(self) -> int:
        with self._msg_id_lock:
            self._msg_id += 1
            return self._msg_id

    def _build_request(self, method: str, params: dict) -> tuple[int, str]:
        """Build a JSON-RPC 2.0 request. Returns (id, json_string)."""
        msg_id = self._next_id()
        msg = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
            "params": params,
        }
        return msg_id, json.dumps(msg, ensure_ascii=False)

    def _send_jsonrpc(self, method: str, params: dict) -> int:
        """Send a JSON-RPC request. Returns the message ID."""
        if not self._process or self._process.poll() is not None:
            raise RuntimeError("ACP process is not running")

        msg_id, line = self._build_request(method, params)
        with self._send_lock:
            try:
                self._process.stdin.write(line + "\n")
                self._process.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                raise RuntimeError(f"ACP stdin write failed: {e}")
        return msg_id

    def _wait_for_response(self, msg_id: int, timeout: float) -> dict:
        """Block until a JSON-RPC response for msg_id arrives."""
        queue = Queue()
        with self._queues_lock:
            self._queues[msg_id] = queue

        try:
            event = queue.get(timeout=timeout)
            return event.data
        except Empty:
            raise TimeoutError(
                f"ACP: no response for message {msg_id} within {timeout}s"
            )
        finally:
            with self._queues_lock:
                self._queues.pop(msg_id, None)

    # ──────────────────────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────────────────────

    def ensure_running(self) -> None:
        """Boot the ACP subprocess if not already alive.

        Sends ``initialize`` + ``session/new`` handshake.

        Pre-boot: runs a one-shot zombie sweep that kills any orphan
        gemini.exe / node.exe processes left behind by hard-killed
        sessions — protects against the SQLite-lock + RAM-exhaustion
        failure mode (memory: zombie_processes.md).
        """
        ACPBridge._sweep_zombies_once()
        with self._lock:
            if self._process and self._process.poll() is None:
                return  # already running

            cli_path = self._find_gemini_cli()
            if not cli_path:
                raise FileNotFoundError(
                    "Gemini CLI not found. Check gemini.cli_path in settings "
                    "or ensure 'gemini' is on PATH."
                )

            logger.info(
                "ACPBridge: booting subprocess (boot #%d, cli=%s)",
                self._boot_count + 1, cli_path,
            )

            env = os.environ.copy()
            api_key = self._get_api_key()
            if api_key:
                env["GEMINI_API_KEY"] = api_key

            cmd = [cli_path, "--acp"]
            if self._model:
                cmd.extend(["--model", self._model])

            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    if sys.platform == "win32" else 0
                ),
            )

            # Register for atexit cleanup (prevents orphaned processes)
            ACPBridge._all_processes.append(self._process)
            ACPBridge._register_atexit()

            self._boot_count += 1
            self._session_id = None

            # Start reader thread
            self._reader_thread = threading.Thread(
                target=self._reader_loop,
                name=f"acp-reader-{self._boot_count}",
                daemon=True,
            )
            self._reader_thread.start()

            # Start stderr drain
            stderr_thread = threading.Thread(
                target=self._stderr_drain,
                name=f"acp-stderr-{self._boot_count}",
                daemon=True,
            )
            stderr_thread.start()

        # ── ACP Handshake (outside _lock to allow reader thread) ──

        # Step 1: initialize
        try:
            init_id = self._send_jsonrpc("initialize", {
                "clientCapabilities": {},
                "protocolVersion": 1,
            })
            init_resp = self._wait_for_response(init_id, timeout=30)
            result = init_resp.get("result", init_resp)
            self._protocol_version = result.get("protocolVersion")
            self._agent_info = result.get("agentInfo", {})
            logger.info(
                "ACPBridge: initialized (protocol=%s, agent=%s)",
                self._protocol_version,
                self._agent_info.get("version", "?"),
            )
        except Exception as e:
            logger.error("ACPBridge: initialize failed: %s", e)
            self._kill_process()
            raise RuntimeError(f"ACP initialize handshake failed: {e}")

        # Step 2: session/new
        try:
            session_params = {
                "cwd": str(Path.cwd()),
                "mcpServers": self._mcp_servers,
            }
            session_id_rpc = self._send_jsonrpc("session/new", session_params)
            session_resp = self._wait_for_response(session_id_rpc, timeout=30)
            result = session_resp.get("result", session_resp)
            self._session_id = result.get("sessionId")
            if not self._session_id:
                raise RuntimeError(
                    f"session/new did not return sessionId: {session_resp}"
                )
            logger.info(
                "ACPBridge: session created (id=%s)", self._session_id[:12],
            )
        except TimeoutError:
            logger.error("ACPBridge: session/new timed out")
            self._kill_process()
            raise RuntimeError("ACP session/new timed out")
        except RuntimeError:
            raise
        except Exception as e:
            logger.error("ACPBridge: session/new failed: %s", e)
            self._kill_process()
            raise RuntimeError(f"ACP session/new failed: {e}")

        # Mark healthy + start watchdog
        self._bridge_healthy = True
        self._boot_time = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._consecutive_stalls = 0
        logger.debug(
            "[HEALTH] ACP alive | boot=#%d pid=%s",
            self._boot_count,
            self._process.pid if self._process else "?",
        )
        self._start_watchdog()

    def set_mcp_config(self, server_config: list[dict]) -> None:
        """Set MCP server config for the next session/new call.

        Args:
            server_config: List of MCP server dicts, e.g.
                [{"name": "alma-tools", "command": "python",
                  "args": ["-m", "src.mcp.alma_mcp_server"], "env": []}]
        """
        self._mcp_servers = server_config

    def new_session(self, mcp_env: dict | None = None) -> str:
        """Create a new ACP session (for per-batch context isolation).

        The ACP process stays alive; only the session rotates.
        If mcp_env is provided, those env vars are injected into the
        MCP server config's ``env`` array so the MCP subprocess inherits
        the batch-specific context.

        Args:
            mcp_env: Dict of environment variables for MCP server context.
                     e.g. {"ALMA_DB_PATH": "/path/to/db", "ALMA_SCAN_ID": "scan_1"}

        Returns:
            str: The new sessionId.
        """
        self.ensure_running()

        # Build MCP config with env vars injected
        mcp_servers = self._mcp_servers
        if mcp_env and mcp_servers:
            mcp_servers = self._inject_mcp_env(mcp_servers, mcp_env)

        session_params = {
            "cwd": str(Path.cwd()),
            "mcpServers": mcp_servers,
        }
        msg_id = self._send_jsonrpc("session/new", session_params)
        resp = self._wait_for_response(msg_id, timeout=30)
        result = resp.get("result", resp)
        self._session_id = result.get("sessionId")
        if not self._session_id:
            raise RuntimeError(f"session/new did not return sessionId: {resp}")
        logger.info("ACPBridge: new session (id=%s)", self._session_id[:12])
        return self._session_id

    @staticmethod
    def _inject_mcp_env(mcp_servers: list[dict], mcp_env: dict) -> list[dict]:
        """Build a copy of MCP server configs with env vars injected.

        Converts ``mcp_env`` dict into the format expected by ACP's Zod
        schema for the ``env`` field.  We try two formats:
        - ``[{"name": "KEY", "value": "VALUE"}, ...]`` (structured)
        - Falls back to ``["KEY=VALUE", ...]`` (flat string)

        The ACP process spawns MCP servers as subprocesses with these env
        vars set, so the MCP server can read them from ``os.environ``.
        """
        import copy
        env_list = [
            {"name": k, "value": str(v)}
            for k, v in mcp_env.items()
            if v is not None
        ]
        result = []
        for server in mcp_servers:
            srv = copy.copy(server)
            srv["env"] = env_list
            result.append(srv)
        return result

    # ──────────────────────────────────────────────────────────────────
    # Watchdog (identical to GeminiBridge 6.2)
    # ──────────────────────────────────────────────────────────────────

    def _start_watchdog(self):
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            return
        self._watchdog_stop.clear()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name=f"acp-watchdog-{self._boot_count}",
            daemon=True,
        )
        self._watchdog_thread.start()
        logger.debug("[HEALTH] watchdog started | interval=%.1fs", self._WATCHDOG_INTERVAL)

    def _watchdog_loop(self):
        while not self._watchdog_stop.is_set():
            self._watchdog_stop.wait(timeout=self._WATCHDOG_INTERVAL)
            if self._watchdog_stop.is_set():
                break
            if self._bridge_healthy and not self.is_alive():
                self._notify_death()
                break

    def _notify_death(self):
        self._bridge_healthy = False
        self._death_count += 1
        self._last_death_time = time.strftime("%Y-%m-%dT%H:%M:%S")
        exit_code = self._process.returncode if self._process else "?"
        last_stderr = " | ".join(self._last_stderr_lines[-3:]) if self._last_stderr_lines else "(no stderr)"
        logger.error(
            "[HEALTH] ACP DIED unexpectedly | deaths=%d exit_code=%s pid=%s | stderr: %s",
            self._death_count, exit_code,
            self._process.pid if self._process else "?",
            last_stderr,
        )
        # Poison all waiting request queues
        with self._queues_lock:
            for q in self._queues.values():
                q.put(BridgeEvent(
                    id="__watchdog__",
                    type="error",
                    data={
                        "error": "bridge_dead",
                        "message": f"ACP process died (exit {exit_code})",
                        "recoverable": True,
                        "bridge_healthy": False,
                    },
                ))
        if self._on_death:
            try:
                self._on_death(self)
            except Exception as e:
                logger.debug("[HEALTH] on_death callback error: %s", e)

    def set_on_death(self, callback: Callable) -> None:
        """Register a callback(bridge) invoked when watchdog detects death."""
        self._on_death = callback

    def record_stall(self) -> bool:
        """Record a stall_timeout event. Returns True if escalation triggered."""
        self._stall_count += 1
        self._consecutive_stalls += 1
        logger.debug(
            "[HEALTH] stall recorded | consecutive=%d total=%d threshold=%d",
            self._consecutive_stalls, self._stall_count,
            self._STALL_ESCALATION_THRESHOLD,
        )
        if self._consecutive_stalls >= self._STALL_ESCALATION_THRESHOLD:
            logger.warning(
                "[HEALTH] stall escalation triggered - %d consecutive stalls, "
                "auto-restarting bridge",
                self._consecutive_stalls,
            )
            self._consecutive_stalls = 0
            return True
        return False

    def record_success(self) -> None:
        """Record a successful call -- resets consecutive stall counter."""
        if self._consecutive_stalls > 0:
            logger.debug(
                "[HEALTH] stall streak broken after %d consecutive stalls",
                self._consecutive_stalls,
            )
        self._consecutive_stalls = 0

    def is_alive(self) -> bool:
        """Check if the ACP process is running."""
        return self._process is not None and self._process.poll() is None

    # ──────────────────────────────────────────────────────────────────
    # Call interfaces
    # ──────────────────────────────────────────────────────────────────

    def call_streaming(
        self,
        prompt: str,
        request_id: str,
        on_token: Callable[[BridgeEvent], None] | None = None,
        timeout: int = 300,
        early_stop: Callable[[], bool] | None = None,
    ) -> dict:
        """
        Send a streaming prompt through ACP.

        Args:
            prompt: Full prompt text.
            request_id: Unique ID for this call (e.g. "batch_1").
            on_token: Optional callback for intermediate events.
            timeout: Max seconds to wait for terminal event.
            early_stop: Optional callable; if it returns True the stream
                is aborted and the accumulated result is returned.
                Checked on each queue poll cycle (~5s).

        Returns:
            dict with keys: full_text, elapsed_ms, turns, events,
            error, message, raw, recoverable, input_tokens, output_tokens.
        """
        self.ensure_running()

        if not self._session_id:
            raise RuntimeError("No ACP session -- call ensure_running() first")

        # Send session/prompt
        msg_id = self._next_id()
        prompt_msg = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": "session/prompt",
            "params": {
                "sessionId": self._session_id,
                "prompt": [{"type": "text", "text": prompt}],
            },
        }

        # Register queue for this request
        queue = Queue()
        with self._queues_lock:
            self._queues[msg_id] = queue

        # Map request_id -> msg_id for routing
        with self._request_id_map_lock:
            self._request_id_map[request_id] = msg_id

        try:
            line = json.dumps(prompt_msg, ensure_ascii=False) + "\n"
            with self._send_lock:
                try:
                    self._process.stdin.write(line)
                    self._process.stdin.flush()
                except (BrokenPipeError, OSError) as e:
                    raise RuntimeError(f"ACP stdin write failed: {e}")

            self._total_calls += 1
            self._active_request_id = request_id
            self._last_event_time = time.time()
            self._start_heartbeat_timer(msg_id)

            # Collect events until terminal
            full_text = ""
            events = []
            t0 = time.time()
            deadline = t0 + timeout

            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    self.abort(request_id)
                    raise TimeoutError(
                        f"ACP call {request_id} timed out after {timeout}s"
                    )

                try:
                    event = queue.get(timeout=min(remaining, 5.0))
                except Empty:
                    if not self.is_alive():
                        raise RuntimeError("ACP process died during call")
                    # Check early_stop on each poll cycle
                    if early_stop:
                        try:
                            if early_stop():
                                logger.info(
                                    "ACPBridge: early_stop triggered for %s, "
                                    "aborting stream", request_id,
                                )
                                self.abort(request_id)
                                elapsed_ms = int((time.time() - t0) * 1000)
                                self._cancel_heartbeat_timer()
                                return {
                                    "full_text": full_text,
                                    "elapsed_ms": elapsed_ms,
                                    "turns": 1,
                                    "events": events,
                                    "error": None,
                                    "input_tokens": 0,
                                    "output_tokens": 0,
                                    "early_stopped": True,
                                }
                        except Exception as e:
                            logger.warning("early_stop callback error: %s", e)
                    continue

                events.append(event)
                self._last_event_time = time.time()

                # ── Event logging ──
                if event.type == "content":
                    delta = event.data.get("delta", "")
                    logger.debug(
                        "ACPBridge[%s]: content delta len=%d total=%d",
                        request_id, len(delta), len(full_text) + len(delta),
                    )
                elif event.type in ("tool_call", "tool_result"):
                    logger.info(
                        "ACPBridge[%s]: %s data_keys=%s",
                        request_id, event.type, list(event.data.keys()),
                    )
                elif event.is_terminal:
                    logger.info(
                        "ACPBridge[%s]: TERMINAL type=%s stopReason=%s",
                        request_id, event.type,
                        event.data.get("stopReason", "N/A"),
                    )

                if event.type == "content":
                    full_text += event.data.get("delta", "")
                    if on_token:
                        try:
                            on_token(event)
                        except Exception as e:
                            logger.warning("on_token callback error: %s", e)

                elif event.type == "heartbeat":
                    if on_token:
                        try:
                            on_token(event)
                        except Exception:
                            pass

                elif event.type == "tool_call":
                    if on_token:
                        try:
                            on_token(event)
                        except Exception:
                            pass

                elif event.type == "tool_result":
                    if on_token:
                        try:
                            on_token(event)
                        except Exception:
                            pass

                elif event.is_terminal:
                    self._cancel_heartbeat_timer()

                    if event.type == "done":
                        bridge_text = event.data.get("full_text", "")
                        if bridge_text:
                            full_text = bridge_text

                    elapsed_ms = int((time.time() - t0) * 1000)

                    result = {
                        "full_text": full_text,
                        "elapsed_ms": elapsed_ms,
                        "turns": event.data.get("turns", 1),
                        "events": events,
                        "error": None,
                        "input_tokens": event.data.get("input_tokens", 0),
                        "output_tokens": event.data.get("output_tokens", 0),
                    }

                    if event.is_error:
                        result["error"] = event.error_code
                        result["message"] = event.data.get("message", "")
                        result["raw"] = event.data.get("raw", "")
                        result["recoverable"] = event.is_recoverable
                        self._last_error = event.error_code

                    if event.type == "stopped":
                        result["error"] = "stopped"
                        result["message"] = event.data.get("message", "")
                        result["recoverable"] = False

                    # Log token usage
                    if self._usage_tracker and not result["error"]:
                        try:
                            tok_in = self._usage_tracker.estimate_tokens(prompt)
                            tok_out = self._usage_tracker.estimate_tokens(full_text)
                            self._usage_tracker.log_call(
                                source="nlp_scan",
                                tokens_in=tok_in,
                                tokens_out=tok_out,
                                scan_id=self._scan_id,
                            )
                        except Exception as _e:
                            logger.debug("Usage tracking failed: %s", _e)

                    return result

        finally:
            self._cancel_heartbeat_timer()
            self._active_request_id = None
            with self._queues_lock:
                self._queues.pop(msg_id, None)
            with self._request_id_map_lock:
                self._request_id_map.pop(request_id, None)

    def call_blocking(self, prompt: str, request_id: str, timeout: int = 300) -> str:
        """
        Blocking call that returns the full response text.

        Args:
            prompt: Full prompt text.
            request_id: Unique ID for this call.
            timeout: Max seconds to wait.

        Returns:
            str: The full response text.

        Raises:
            RuntimeError: If the call failed.
        """
        result = self.call_streaming(prompt, request_id, timeout=timeout)

        if result["error"]:
            raise RuntimeError(
                f"ACP call failed: {result['error']} - "
                f"{result.get('message', '')}"
            )

        return result["full_text"]

    def abort(self, request_id: str) -> None:
        """Cancel an in-flight call.

        ACP may support session/cancel; if not, we kill and restart.
        """
        if not self.is_alive():
            return

        try:
            self._send_jsonrpc("session/cancel", {
                "sessionId": self._session_id or "",
            })
            logger.info("ACPBridge: cancel sent for %s", request_id)
        except Exception as e:
            logger.warning("ACPBridge: cancel failed (%s), will restart", e)

    def ping(self, timeout: int = 10) -> dict | None:
        """Health check. Returns a status dict or None if unhealthy.

        Uses the ACP initialize as a lightweight round-trip since ACP
        doesn't have a native ping method.  For a process that's already
        initialized, we just check is_alive + return synthetic data.
        """
        if not self.is_alive():
            return None

        return {
            "status": "ok",
            "alive": True,
            "session_id": self._session_id,
            "boot_count": self._boot_count,
            "total_calls": self._total_calls,
            "protocol_version": self._protocol_version,
        }

    def probe(self, timeout: int = 30) -> dict | None:
        """API-level canary: session/prompt with "Respond OK".

        Exercises the full path: Python -> ACP -> Google API -> back.
        """
        if not self.is_alive():
            return None

        probe_id = f"probe_{int(time.time() * 1000)}"
        t0 = time.time()
        try:
            result = self.call_streaming(
                "Respond with exactly one word: OK",
                probe_id,
                timeout=timeout,
            )
            latency_ms = int((time.time() - t0) * 1000)
            status = "error" if result.get("error") else "success"
            error = result.get("error")
            logger.debug(
                "[HEALTH] probe complete | latency=%dms status=%s error=%s",
                latency_ms, status, error,
            )
            return {"latency_ms": latency_ms, "status": status, "error": error}
        except TimeoutError:
            latency_ms = int((time.time() - t0) * 1000)
            logger.debug("[HEALTH] probe timeout | latency=%dms", latency_ms)
            return {"latency_ms": latency_ms, "status": "timeout",
                    "error": "probe_timeout"}
        except Exception as e:
            latency_ms = int((time.time() - t0) * 1000)
            logger.debug("[HEALTH] probe error | latency=%dms err=%s", latency_ms, e)
            return {"latency_ms": latency_ms, "status": "error",
                    "error": str(e)}

    # ──────────────────────────────────────────────────────────────────
    # Shutdown / Restart
    # ──────────────────────────────────────────────────────────────────

    def shutdown(self, timeout: int = 10) -> None:
        """Gracefully shutdown the ACP process."""
        self._bridge_healthy = False
        self._watchdog_stop.set()
        self._cancel_heartbeat_timer()
        logger.debug("[HEALTH] shutdown initiated | boot=#%d", self._boot_count)

        with self._lock:
            if not self._process:
                return

            if self._process.poll() is None:
                try:
                    # Close stdin to signal EOF
                    if self._process.stdin:
                        self._process.stdin.close()
                    self._process.wait(timeout=timeout)
                    logger.info("ACPBridge: clean shutdown")
                except subprocess.TimeoutExpired:
                    logger.warning("ACPBridge: shutdown timed out, tree-killing")
                    self._tree_kill(self._process)
                    try:
                        self._process.wait(timeout=5)
                    except Exception:
                        pass
                except Exception as e:
                    logger.warning("ACPBridge: shutdown error: %s", e)
                    self._tree_kill(self._process)

            # Close file handles
            for handle in (self._process.stdin, self._process.stdout,
                           self._process.stderr):
                try:
                    if handle and not handle.closed:
                        handle.close()
                except Exception:
                    pass

            # Remove from class registry (no longer needs atexit cleanup)
            proc_ref = self._process
            self._process = None
            try:
                ACPBridge._all_processes.remove(proc_ref)
            except (ValueError, AttributeError):
                pass
            self._session_id = None

            # Clear waiting queues
            with self._queues_lock:
                for q in self._queues.values():
                    q.put(BridgeEvent(
                        id="__shutdown__",
                        type="error",
                        data={
                            "error": "bridge_shutdown",
                            "message": "ACP bridge was shut down",
                            "recoverable": False,
                            "bridge_healthy": False,
                        },
                    ))
                self._queues.clear()

    def restart(self) -> None:
        """Shutdown and reboot."""
        logger.debug("[HEALTH] ACP restart requested | boot=#%d", self._boot_count)
        self.shutdown()
        self.ensure_running()
        logger.debug("[HEALTH] ACP restart complete | boot=#%d", self._boot_count)

    def _kill_process(self) -> None:
        """Force-kill the ACP process (used during failed handshake)."""
        if self._process:
            self._tree_kill(self._process)
            self._process = None

    @staticmethod
    def _tree_kill(proc) -> None:
        """Kill a process and all its children (needed on Windows where
        kill() doesn't propagate to child processes like MCP servers)."""
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True, timeout=5,
                )
            else:
                proc.kill()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    # ──────────────────────────────────────────────────────────────────
    # Reader thread
    # ──────────────────────────────────────────────────────────────────

    def _reader_loop(self):
        """Background thread: reads stdout, filters noise, routes events."""
        proc = self._process
        if not proc or not proc.stdout:
            return

        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue

                # Parse JSON
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("ACPBridge: non-JSON stdout: %s", line[:200])
                    continue

                # Filter: must be a dict with "jsonrpc" or "method" key
                if not isinstance(data, dict):
                    logger.debug("ACPBridge: non-dict JSON: %s", str(data)[:100])
                    continue

                if "jsonrpc" not in data and "method" not in data:
                    logger.debug("ACPBridge: non-JSONRPC dict: %s", str(data)[:100])
                    continue

                # Log raw message for multi-turn diagnosis
                msg_method = data.get("method", "")
                msg_id_val = data.get("id", "")
                result_keys = list(data.get("result", {}).keys()) if isinstance(data.get("result"), dict) else []
                has_error = "error" in data
                logger.debug(
                    "ACPBridge: RAW method=%r id=%s result_keys=%s has_error=%s",
                    msg_method or "(response)", msg_id_val, result_keys, has_error,
                )

                # Route the message
                self._route_message(data)

        except Exception as e:
            if proc.poll() is None:
                logger.error("ACPBridge: reader error: %s", e)

        logger.debug("ACPBridge: reader thread exiting")

    def _route_message(self, data: dict) -> None:
        """Route a parsed JSON-RPC message to the appropriate queue."""

        # Case 1: JSON-RPC response (has "id" and "result" or "error")
        if "id" in data and ("result" in data or "error" in data):
            msg_id = data["id"]

            # Check if this is a response to a session/prompt call
            # (terminal event — done or error)
            result = data.get("result", {})
            error = data.get("error")

            if error:
                # JSON-RPC error response
                logger.info(
                    "ACPBridge: JSON-RPC ERROR id=%s code=%s msg=%s",
                    msg_id, error.get("code"), str(error.get("message", ""))[:120],
                )
                event = BridgeEvent(
                    id=str(msg_id),
                    type="error",
                    data={
                        "error": self._classify_jsonrpc_error(error),
                        "message": error.get("message", str(error)),
                        "raw": json.dumps(error),
                        "recoverable": error.get("code", 0) in _JSONRPC_RECOVERABLE_CODES,
                    },
                )
            elif isinstance(result, dict) and "stopReason" in result:
                logger.debug(
                    "ACPBridge: TURN COMPLETE id=%s stopReason=%r",
                    msg_id, result.get("stopReason"),
                )
                # Extract token counts from _meta
                meta = result.get("_meta", {})
                quota = meta.get("quota", {})
                token_count = quota.get("token_count", {})

                event = BridgeEvent(
                    id=str(msg_id),
                    type="done",
                    data={
                        "stopReason": result["stopReason"],
                        "input_tokens": token_count.get("input_tokens", 0),
                        "output_tokens": token_count.get("output_tokens", 0),
                    },
                )
            else:
                # Generic response (initialize, session/new, etc.)
                event = BridgeEvent(
                    id=str(msg_id),
                    type="done",
                    data=data,
                )

            with self._queues_lock:
                q = self._queues.get(msg_id)
                if q:
                    q.put(event)
                else:
                    logger.debug(
                        "ACPBridge: unrouted response id=%s", msg_id
                    )
            return

        # Case 2: JSON-RPC notification (has "method" but no "id")
        method = data.get("method", "")
        params = data.get("params", {})

        if method == "session/update":
            self._handle_session_update(params)
            return

        if method == "session/request_permission":
            # ACP asks for permission to use native tools (file read, shell, etc.)
            # Auto-approve all permission requests so the model doesn't hang.
            self._handle_permission_request(data)
            return

        # Unrecognized notification — log and skip
        logger.debug("ACPBridge: unhandled notification method=%s", method)

    def _handle_session_update(self, params: dict) -> None:
        """Handle session/update notifications (streaming content, tool calls).

        ACP notification structure (verified 2026-04-07):
            params.update.sessionUpdate = "agent_message_chunk"
            params.update.content = {type: "text", text: "..."}
        Note: content is nested under params.update, NOT directly under params.
        """
        # Extract the inner update object — ACP nests it under params.update
        update = params.get("update", params)  # fallback to params for compat
        update_type = update.get("sessionUpdate", "")
        logger.debug(
            "ACPBridge: session/update type=%r content_type=%r",
            update_type,
            update.get("content", {}).get("type", "N/A") if isinstance(update.get("content"), dict) else "N/A",
        )

        if update_type == "agent_message_chunk":
            content = update.get("content", {})
            content_type = content.get("type", "")

            if content_type == "text":
                # Text content delta
                text = content.get("text", "")
                event = BridgeEvent(
                    id="__stream__",
                    type="content",
                    data={"delta": text},
                )
                self._dispatch_to_active_queue(event)

            elif content_type in ("function_call", "tool_use"):
                # Tool call (shape TBD — defensive parsing for Session 2)
                event = BridgeEvent(
                    id="__stream__",
                    type="tool_call",
                    data={
                        "name": content.get("name", content.get("tool_name", "")),
                        "args": content.get("args", content.get("arguments", {})),
                    },
                )
                self._dispatch_to_active_queue(event)

            elif content_type == "tool_result":
                event = BridgeEvent(
                    id="__stream__",
                    type="tool_result",
                    data=content,
                )
                self._dispatch_to_active_queue(event)

            else:
                # Unknown content type — log but don't crash
                logger.debug(
                    "ACPBridge: unknown content type in agent_message_chunk: %s",
                    content_type,
                )

        elif update_type == "available_commands_update":
            # Post-session notification — safe to ignore
            pass

        elif update_type in ("tool_call", "tool_call_update", "tool_result"):
            # Distinguish MCP tools (ours) from native CLI tools (filesystem).
            # MCP tool events carry the tool name; dispatch ours to the
            # active queue so WorkerAgent can observe them.
            raw_content = update.get("content", update)
            # ACP may send content as a list of tool calls or a single dict
            items = raw_content if isinstance(raw_content, list) else [raw_content]
            for content in items:
                if not isinstance(content, dict):
                    continue
                name = (
                    content.get("name", "")
                    or content.get("tool_name", "")
                    or ""
                )
                if name in self._MCP_TOOL_NAMES:
                    if update_type == "tool_result":
                        event = BridgeEvent(
                            id="__stream__",
                            type="tool_result",
                            data=content,
                        )
                    else:
                        event = BridgeEvent(
                            id="__stream__",
                            type="tool_call",
                            data={
                                "name": name,
                                "args": content.get("args",
                                                    content.get("arguments", {})),
                            },
                        )
                    self._dispatch_to_active_queue(event)
                # else: native CLI tool — ignore

        elif update_type == "agent_thought_chunk":
            # Model thinking events — ignore for now
            pass

        else:
            logger.debug(
                "ACPBridge: unhandled session/update type=%s", update_type
            )

    # Native CLI tool patterns to DENY (filesystem exploration, shell, etc.)
    _DENY_PATTERNS = frozenset({
        "file", "read", "write", "edit", "shell", "exec",
        "search", "grep", "find", "list_dir", "cat",
    })

    # MCP tool names (our registered tools) — dispatched to active queue
    _MCP_TOOL_NAMES = frozenset({
        # NLP scan tools (alma-tools server)
        "store_classification", "query_taxonomy", "get_stats_context",
        "flag_for_review", "get_full_thread", "report_progress",
        "check_cross_trc",
        # Chat tools (alma-chat-tools server)
        "query_entities", "query_ticket_classifications", "list_tickets",
        "semantic_search", "query_findings", "query_stats",
        "read_thread", "read_threads_batch", "query_report",
    })

    # MCP server names whose tools should be auto-approved.
    # Native CLI tools (codebase_investigator, grep, file read, etc.)
    # are denied to prevent model-initiated hangs.
    _ALLOWED_MCP_SERVERS = frozenset({"alma-tools", "alma-chat-tools"})

    def _handle_permission_request(self, data: dict) -> None:
        """Approve MCP tool calls, deny all native CLI tools.

        ACP permission request payload (Gemini CLI v0.36.0):
            params.toolCall.toolCallId = "mcp_{server}_{tool}-{ts}-{seq}"
            params.options = [{optionId: "proceed_always_server", ...}, ...]

        MCP tool calls carry a toolCallId prefixed with "mcp_{server}_".
        We approve those (using proceed_always_server to auto-approve all
        future calls from that MCP server) and deny everything else.
        This prevents native agents like codebase_investigator from hanging.
        """
        msg_id = data.get("id")
        params = data.get("params", {})

        # Extract tool identity from the ACP permission payload
        tool_call = params.get("toolCall", {})
        tool_call_id = tool_call.get("toolCallId", "") if isinstance(tool_call, dict) else ""

        # Check if this is an MCP tool from an allowed server
        # toolCallId format: mcp_{server}_{tool}-{timestamp}-{seq}
        is_mcp = False
        mcp_server = ""
        if tool_call_id.startswith("mcp_"):
            parts = tool_call_id.split("_", 2)  # ["mcp", "alma-tools", "store_classification-..."]
            if len(parts) >= 2:
                mcp_server = parts[1]
                is_mcp = mcp_server in self._ALLOWED_MCP_SERVERS

        approved = is_mcp

        # Log: INFO for denials (we care about what's blocked),
        # DEBUG for approvals (routine MCP calls)
        if approved:
            logger.debug(
                "ACPBridge: permission APPROVED id=%s server=%r toolCallId=%s",
                msg_id, mcp_server, tool_call_id[:60],
            )
        else:
            # Extract whatever identity info is available for diagnostics
            tool_name = (
                params.get("tool", "") or params.get("name", "") or ""
            ).lower()
            logger.info(
                "ACPBridge: permission DENIED id=%s tool=%r toolCallId=%r",
                msg_id, tool_name or "(native)",
                tool_call_id[:80] or "(none)",
            )

        if msg_id is not None:
            try:
                if approved:
                    # Use proceed_always_server to auto-approve all future
                    # calls from this MCP server (avoids per-call overhead).
                    # Fall back to proceed_once if the option isn't available.
                    options = params.get("options", [])
                    option_ids = {o.get("optionId") for o in options if isinstance(o, dict)}
                    if "proceed_always_server" in option_ids:
                        outcome = {
                            "outcome": "selected",
                            "optionId": "proceed_always_server",
                        }
                    else:
                        outcome = {
                            "outcome": "selected",
                            "optionId": "proceed_once",
                        }
                else:
                    outcome = {"outcome": "cancelled"}

                response = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"outcome": outcome},
                }
                line = json.dumps(response) + "\n"
                with self._send_lock:
                    self._process.stdin.write(line)
                    self._process.stdin.flush()
            except Exception as e:
                logger.warning(
                    "ACPBridge: failed to send permission response: %s", e
                )

    def _dispatch_to_active_queue(self, event: BridgeEvent) -> None:
        """Dispatch a streaming event to the currently active prompt queue.

        ACP notifications don't carry the JSON-RPC id of the prompt request,
        so we route to the most recently sent session/prompt queue.
        """
        with self._queues_lock:
            # Find the highest msg_id queue (most recent prompt)
            if not self._queues:
                return
            # Skip internal queues (__shutdown__, etc.)
            candidate_ids = [
                k for k in self._queues.keys()
                if isinstance(k, int)
            ]
            if not candidate_ids:
                return
            target_id = max(candidate_ids)
            q = self._queues.get(target_id)
            if q:
                q.put(event)

    def _classify_jsonrpc_error(self, error: dict) -> str:
        """Map a JSON-RPC error to our error category string."""
        code = error.get("code", 0)
        message = error.get("message", "").lower()

        if code in _JSONRPC_FATAL_CODES:
            return "config_error"
        if code in _JSONRPC_RECOVERABLE_CODES:
            return "server_error"

        # Heuristic from message text
        if "rate" in message or "quota" in message or "429" in message:
            return "rate_limit"
        if "auth" in message or "credentials" in message:
            return "auth_expired"
        if "timeout" in message:
            return "network_timeout"
        if "not found" in message:
            return "model_not_found"

        return "server_error"

    # ──────────────────────────────────────────────────────────────────
    # Heartbeat timer
    # ──────────────────────────────────────────────────────────────────

    def _start_heartbeat_timer(self, msg_id: int) -> None:
        """Start synthetic heartbeat generation for a streaming call."""
        self._cancel_heartbeat_timer()

        def _heartbeat():
            if not self._active_request_id:
                return
            elapsed = time.time() - self._last_event_time
            if elapsed >= 5.0:
                event = BridgeEvent(
                    id="__heartbeat__",
                    type="heartbeat",
                    data={
                        "elapsed_ms": int((time.time() - self._last_event_time) * 1000),
                        "silence_ms": int(elapsed * 1000),
                    },
                )
                with self._queues_lock:
                    q = self._queues.get(msg_id)
                    if q:
                        q.put(event)
            # Reschedule
            if self._active_request_id:
                self._heartbeat_timer = threading.Timer(5.0, _heartbeat)
                self._heartbeat_timer.daemon = True
                self._heartbeat_timer.start()

        self._heartbeat_timer = threading.Timer(5.0, _heartbeat)
        self._heartbeat_timer.daemon = True
        self._heartbeat_timer.start()

    def _cancel_heartbeat_timer(self) -> None:
        if self._heartbeat_timer:
            self._heartbeat_timer.cancel()
            self._heartbeat_timer = None

    # ──────────────────────────────────────────────────────────────────
    # Stderr drain
    # ──────────────────────────────────────────────────────────────────

    def _stderr_drain(self):
        """Background thread: drains ACP stderr to our logger."""
        proc = self._process
        if not proc or not proc.stderr:
            return

        try:
            for line in proc.stderr:
                line = line.rstrip()
                if line:
                    logger.debug("[acp] %s", line)
                    self._last_stderr_lines.append(line)
                    if len(self._last_stderr_lines) > 10:
                        self._last_stderr_lines.pop(0)
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────────
    # Path resolution
    # ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _find_gemini_cli() -> str | None:
        """Locate the Gemini CLI executable.

        Priority:
        1. data/settings.yaml -> gemini.cli_path
        2. PATH lookup ('gemini' or 'gemini.CMD')
        """
        # Try settings first
        try:
            from src.data.settings_manager import get_section
            gemini_cfg = get_section("gemini", {})
            cli_path = gemini_cfg.get("cli_path", "")
            if cli_path and Path(cli_path).exists():
                return cli_path
        except Exception:
            pass

        # Fallback: PATH
        found = shutil.which("gemini") or shutil.which("gemini.CMD")
        return found

    @staticmethod
    def _get_api_key() -> str:
        """Load saved Gemini API key, if any."""
        try:
            from src.data import pat_store
            return pat_store.load_setting("gemini_api_key") or ""
        except Exception:
            return ""

    # ──────────────────────────────────────────────────────────────────
    # Diagnostics
    # ──────────────────────────────────────────────────────────────────

    @property
    def boot_count(self) -> int:
        return self._boot_count

    @property
    def total_calls(self) -> int:
        return self._total_calls

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def get_stats(self) -> dict:
        """Return bridge stats dict (compatible with GeminiBridge.get_stats)."""
        return {
            "alive": self.is_alive(),
            "healthy": self._bridge_healthy,
            "boot_count": self._boot_count,
            "total_calls": self._total_calls,
            "last_error": self._last_error,
            "death_count": self._death_count,
            "stall_count": self._stall_count,
            "consecutive_stalls": self._consecutive_stalls,
            "last_death_time": self._last_death_time,
            "boot_time": self._boot_time,
            # ACP-specific
            "session_id": self._session_id,
            "protocol_version": self._protocol_version,
            "agent_version": self._agent_info.get("version"),
        }

    def __repr__(self):
        status = "alive" if self.is_alive() else "dead"
        return (
            f"ACPBridge(status={status}, "
            f"boots={self._boot_count}, "
            f"calls={self._total_calls}, "
            f"session={self._session_id[:8] if self._session_id else None})"
        )
