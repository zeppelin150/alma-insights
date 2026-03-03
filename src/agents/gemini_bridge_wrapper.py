"""
Alma Insights -- Gemini Bridge Wrapper (Pass 5.0)

Python wrapper for the persistent Node.js streaming bridge (gemini_bridge.mjs).
Manages subprocess lifecycle, JSON-line protocol over stdin/stdout,
reader thread for async event routing, and automatic crash recovery.

The bridge boots the Gemini CLI's internal modules ONCE (~1.5s), then stays
alive accepting JSON prompts. This eliminates the ~17s cold-start overhead
per subprocess.run() call that the old scan_worker.py architecture had.

Thread safety: All public methods are thread-safe. The reader thread
dispatches events to per-request queues; callers block on their own queue.
"""

import json
import os
import sys
import subprocess
import threading
import time
import logging
from pathlib import Path
from queue import Queue, Empty

logger = logging.getLogger("alma.bridge")


# ── Error categories (mirror bridge's classifyError) ──
RECOVERABLE_ERRORS = frozenset({
    "rate_limit", "server_error", "network_timeout",
    "connection_reset", "stall_timeout", "aborted_by_client",
})

FATAL_ERRORS = frozenset({
    "auth_expired", "forbidden", "model_not_found",
    "config_error", "bridge_fatal",
})

# Terminal event types (exactly one per call from the bridge)
TERMINAL_EVENTS = frozenset({"done", "error", "stopped"})


class BridgeEvent:
    """Parsed event from the bridge's stdout JSON stream."""

    __slots__ = ("id", "type", "data")

    def __init__(self, id, type, data):
        self.id = id        # request ID this event belongs to
        self.type = type    # content, heartbeat, tool_call, tool_result, done, error, stopped
        self.data = data    # full parsed dict from the bridge

    @property
    def is_terminal(self):
        return self.type in TERMINAL_EVENTS

    @property
    def is_error(self):
        return self.type == "error"

    @property
    def is_recoverable(self):
        return self.data.get("recoverable", False)

    @property
    def error_code(self):
        return self.data.get("error", "")

    def __repr__(self):
        return f"BridgeEvent(id={self.id!r}, type={self.type!r})"


class GeminiBridge:
    """
    Python wrapper for gemini_bridge.mjs.

    Manages the Node.js subprocess lifecycle, routes events by request ID,
    and provides blocking + streaming call interfaces.

    Usage:
        bridge = GeminiBridge()
        bridge.ensure_running()

        # Blocking call (accumulates all content, returns full text)
        result = bridge.call_blocking("Classify these tickets...", "batch_1")

        # Streaming call (on_token fires for each content delta)
        result = bridge.call_streaming("...", "batch_2", on_token=my_callback)

        bridge.shutdown()
    """

    def __init__(self, bridge_script=None, node_path=None, model=None):
        """
        Args:
            bridge_script: Path to gemini_bridge.mjs. Auto-detected if None.
            node_path: Path to node executable. Auto-detected if None.
            model: Gemini model name (e.g. "gemini-2.5-flash"). Passed
                   as --model flag to the bridge subprocess so the CLI
                   uses the specified model instead of its default.
        """
        self._bridge_script = bridge_script or self._find_bridge_script()
        self._node_path = node_path or self._find_node()
        self._model = model

        self._process = None
        self._reader_thread = None
        self._lock = threading.Lock()

        # Per-request event queues: request_id -> Queue[BridgeEvent]
        self._queues = {}
        self._queues_lock = threading.Lock()

        # 9.0 T1: Serialize stdin writes to prevent JSON protocol corruption
        self._send_lock = threading.Lock()

        # Bridge-level fatal event callback
        self._on_bridge_fatal = None

        # Boot readiness signal (set by stderr drain when "Bridge ready" seen)
        self._ready_event = threading.Event()

        # Usage tracking (set externally to enable token/cost logging)
        self._usage_tracker = None    # Optional UsageTracker instance
        self._scan_id = None          # Current scan ID for usage attribution

        # Stats
        self._boot_count = 0
        self._total_calls = 0
        self._last_error = None

        # ── Build 6.2: Bridge health monitor ──
        self._watchdog_thread = None
        self._bridge_healthy = False          # True once ensure_running succeeds
        self._death_count = 0                 # total unexpected deaths detected
        self._stall_count = 0                 # total stall_timeout errors observed
        self._consecutive_stalls = 0          # resets on any non-stall event
        self._last_death_time = None          # ISO timestamp of last death
        self._boot_time = None                # ISO timestamp of last successful boot
        self._on_death = None                 # callback(bridge) on unexpected death
        self._watchdog_stop = threading.Event()
        self._WATCHDOG_INTERVAL = 3.0         # seconds between is_alive polls
        self._STALL_ESCALATION_THRESHOLD = 3  # consecutive stalls → auto-restart

    # ──────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────────────────────────

    def ensure_running(self):
        """Boot the bridge subprocess if not already alive."""
        with self._lock:
            if self._process and self._process.poll() is None:
                return  # already running

            if not self._bridge_script or not Path(self._bridge_script).exists():
                raise FileNotFoundError(
                    f"Bridge script not found: {self._bridge_script}"
                )
            if not self._node_path:
                raise FileNotFoundError(
                    "Node.js not found. Install Node.js to use the bridge."
                )

            logger.info(
                f"GeminiBridge: booting subprocess "
                f"(boot #{self._boot_count + 1})"
            )

            # Build environment with API key if available
            env = os.environ.copy()
            api_key = self._get_api_key()
            if api_key:
                env["GEMINI_API_KEY"] = api_key

            cmd = [self._node_path, self._bridge_script]
            if self._model:
                cmd.extend(['--model', self._model])

            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding="utf-8",
                errors="replace",
                bufsize=1,       # line buffered
                env=env,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    if sys.platform == "win32" else 0
                ),
            )

            self._boot_count += 1

            # Start reader thread
            self._reader_thread = threading.Thread(
                target=self._reader_loop,
                name=f"bridge-reader-{self._boot_count}",
                daemon=True,
            )
            self._reader_thread.start()

            # Reset readiness event for this boot cycle
            self._ready_event.clear()

            # Start stderr drain thread (logs to our logger, signals readiness)
            stderr_thread = threading.Thread(
                target=self._stderr_drain,
                name=f"bridge-stderr-{self._boot_count}",
                daemon=True,
            )
            stderr_thread.start()

            # Wait for "Bridge ready" signal from stderr (up to 30s)
            # The bridge outputs "Bridge ready. Send JSON on stdin."
            # once it has loaded the Gemini CLI modules and is accepting input.
            boot_timeout = 30
            if self._ready_event.wait(timeout=boot_timeout):
                logger.info(
                    "GeminiBridge: subprocess ready (signal received)"
                )
            else:
                # Timeout -- check if process died
                if self._process.poll() is not None:
                    raise RuntimeError(
                        f"Bridge process died during boot "
                        f"(exit code {self._process.returncode})"
                    )
                # Process alive but no ready signal -- try ping as fallback
                logger.warning(
                    "GeminiBridge: no ready signal after "
                    f"{boot_timeout}s, trying ping fallback"
                )
                ping_ok = False
                for attempt in range(3):
                    try:
                        result = self.ping(timeout=5)
                        if result:
                            ping_ok = True
                            break
                    except Exception:
                        pass
                    time.sleep(1)

                if ping_ok:
                    logger.info(
                        "GeminiBridge: subprocess ready (ping fallback)"
                    )
                else:
                    logger.warning(
                        "GeminiBridge: no ready signal or ping response, "
                        "proceeding anyway (bridge may still be booting)"
                    )

            # ── 6.2: Mark healthy + start watchdog ──
            self._bridge_healthy = True
            self._boot_time = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._consecutive_stalls = 0
            logger.debug(
                "[HEALTH] bridge alive | boot=#%d pid=%s",
                self._boot_count,
                self._process.pid if self._process else "?",
            )
            self._start_watchdog()

    # ── Build 6.2: Watchdog ─────────────────────────────────────────────

    def _start_watchdog(self):
        """Start the background watchdog thread (idempotent)."""
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            return  # already running
        self._watchdog_stop.clear()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name=f"bridge-watchdog-{self._boot_count}",
            daemon=True,
        )
        self._watchdog_thread.start()
        logger.debug("[HEALTH] watchdog started | interval=%.1fs", self._WATCHDOG_INTERVAL)

    def _watchdog_loop(self):
        """Periodic is_alive check. Fires _notify_death on unexpected exit."""
        while not self._watchdog_stop.is_set():
            self._watchdog_stop.wait(timeout=self._WATCHDOG_INTERVAL)
            if self._watchdog_stop.is_set():
                break
            # Only check if we believe the bridge should be alive
            if self._bridge_healthy and not self.is_alive():
                self._notify_death()
                break  # one notification per death; next boot starts a new watchdog

    def _notify_death(self):
        """Handle unexpected bridge death: update stats, notify queues, fire callback."""
        self._bridge_healthy = False
        self._death_count += 1
        self._last_death_time = time.strftime("%Y-%m-%dT%H:%M:%S")
        exit_code = self._process.returncode if self._process else "?"
        logger.error(
            "[HEALTH] bridge DIED unexpectedly | deaths=%d exit_code=%s pid=%s",
            self._death_count, exit_code,
            self._process.pid if self._process else "?",
        )
        # Poison all waiting request queues so callers unblock immediately
        with self._queues_lock:
            for q in self._queues.values():
                q.put(BridgeEvent(
                    id="__watchdog__",
                    type="error",
                    data={
                        "error": "bridge_dead",
                        "message": f"Bridge process died (exit {exit_code})",
                        "recoverable": True,
                        "bridge_healthy": False,
                    },
                ))
        # Fire external callback (used by orchestrator for scan events)
        if self._on_death:
            try:
                self._on_death(self)
            except Exception as e:
                logger.debug("[HEALTH] on_death callback error: %s", e)

    def set_on_death(self, callback):
        """Register a callback(bridge) invoked when watchdog detects death."""
        self._on_death = callback

    def record_stall(self):
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
                "[HEALTH] stall escalation triggered — %d consecutive stalls, "
                "auto-restarting bridge",
                self._consecutive_stalls,
            )
            self._consecutive_stalls = 0
            return True  # caller should restart
        return False

    def record_success(self):
        """Record a successful (non-stall) call — resets consecutive stall counter."""
        if self._consecutive_stalls > 0:
            logger.debug(
                "[HEALTH] stall streak broken after %d consecutive stalls",
                self._consecutive_stalls,
            )
        self._consecutive_stalls = 0

    def is_alive(self):
        """Check if the bridge process is running."""
        return self._process is not None and self._process.poll() is None

    def shutdown(self, timeout=10):
        """Gracefully shutdown the bridge."""
        # Stop watchdog first so it doesn't fire death during intentional shutdown
        self._bridge_healthy = False
        self._watchdog_stop.set()
        logger.debug("[HEALTH] shutdown initiated | boot=#%d", self._boot_count)
        with self._lock:
            if not self._process:
                return

            if self._process.poll() is None:
                try:
                    self._send_raw({"command": "quit"})
                    self._process.wait(timeout=timeout)
                    logger.info("GeminiBridge: clean shutdown")
                except subprocess.TimeoutExpired:
                    logger.warning("GeminiBridge: quit timed out, killing")
                    self._process.kill()
                    self._process.wait(timeout=5)
                except Exception as e:
                    logger.warning(f"GeminiBridge: shutdown error: {e}")
                    try:
                        self._process.kill()
                    except Exception:
                        pass

            # Close subprocess file handles to avoid ResourceWarning
            for handle in (self._process.stdin,
                           self._process.stdout,
                           self._process.stderr):
                try:
                    if handle:
                        handle.close()
                except Exception:
                    pass

            self._process = None

            # Clear any waiting queues
            with self._queues_lock:
                for q in self._queues.values():
                    q.put(BridgeEvent(
                        id="__shutdown__",
                        type="error",
                        data={
                            "error": "bridge_shutdown",
                            "message": "Bridge was shut down",
                            "recoverable": False,
                            "bridge_healthy": False,
                        }
                    ))
                self._queues.clear()

    def restart(self):
        """Shutdown and reboot."""
        logger.debug("[HEALTH] bridge restart requested | boot=#%d", self._boot_count)
        self.shutdown()
        self.ensure_running()
        logger.debug("[HEALTH] bridge restart complete | boot=#%d", self._boot_count)

    # ──────────────────────────────────────────────────────────────────────
    # Call interfaces
    # ──────────────────────────────────────────────────────────────────────

    def call_streaming(self, prompt, request_id, on_token=None, timeout=300):
        """
        Send a streaming prompt to the bridge.

        Args:
            prompt: Full prompt text (system + user combined).
            request_id: Unique ID for this call (e.g. "batch_1").
            on_token: Optional callback(BridgeEvent) for intermediate events
                      (content deltas, heartbeats, tool calls).
            timeout: Max seconds to wait for terminal event.

        Returns:
            dict with keys:
              - full_text: Accumulated response text
              - elapsed_ms: Bridge-reported elapsed time
              - turns: Number of model turns
              - events: List of all BridgeEvent objects
              - error: Error code if failed (None if success)

        Raises:
            TimeoutError: If no terminal event within timeout.
            RuntimeError: If bridge is not running.
        """
        self.ensure_running()

        queue = self._register_queue(request_id)
        try:
            # Send request
            self._send_raw({
                "id": request_id,
                "prompt": prompt,
                "stream": True,
            })
            self._total_calls += 1

            # Collect events until terminal
            full_text = ""
            events = []
            deadline = time.time() + timeout

            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    self.abort(request_id)
                    raise TimeoutError(
                        f"Bridge call {request_id} timed out after {timeout}s"
                    )

                try:
                    event = queue.get(timeout=min(remaining, 5.0))
                except Empty:
                    # Check if bridge died
                    if not self.is_alive():
                        raise RuntimeError(
                            "Bridge process died during call"
                        )
                    continue

                events.append(event)

                if event.type == "content":
                    full_text += event.data.get("delta", "")
                    if on_token:
                        try:
                            on_token(event)
                        except Exception as e:
                            logger.warning(
                                f"on_token callback error: {e}"
                            )

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
                    # done, error, or stopped
                    if event.type == "done":
                        # Use bridge's full_text if available
                        bridge_text = event.data.get("full_text", "")
                        if bridge_text:
                            full_text = bridge_text

                    result = {
                        "full_text": full_text,
                        "elapsed_ms": event.data.get("elapsed_ms", 0),
                        "turns": event.data.get("turns", 1),
                        "events": events,
                        "error": None,
                        "input_tokens": event.data.get("input_tokens", 0),
                        "output_tokens": event.data.get("output_tokens", 0),
                    }

                    if event.is_error:
                        result["error"] = event.error_code
                        result["message"] = event.data.get("message", "")
                        result["recoverable"] = event.is_recoverable
                        self._last_error = event.error_code

                    if event.type == "stopped":
                        result["error"] = "stopped"
                        result["message"] = event.data.get("message", "")
                        result["recoverable"] = False

                    # Log token usage if tracker configured
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
                            logger.debug(f"Usage tracking failed: {_e}")

                    return result

        finally:
            self._unregister_queue(request_id)

    def call_blocking(self, prompt, request_id, timeout=300):
        """
        Blocking call that returns the full response text.

        Convenience wrapper around call_streaming() that discards
        intermediate events and returns just the text.

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
                f"Bridge call failed: {result['error']} - "
                f"{result.get('message', '')}"
            )

        return result["full_text"]

    def abort(self, request_id):
        """Cancel an in-flight call."""
        if self.is_alive():
            try:
                self._send_raw({"command": "abort", "id": request_id})
                logger.info(f"GeminiBridge: abort sent for {request_id}")
            except Exception as e:
                logger.warning(f"GeminiBridge: abort failed: {e}")

    def ping(self, timeout=10):
        """
        Health check. Returns ping response dict or None if unhealthy.

        Response includes: status, uptime_ms, calls, boot_ms, active_calls
        """
        if not self.is_alive():
            return None

        ping_id = f"ping_{int(time.time() * 1000)}"
        queue = self._register_queue(ping_id)
        try:
            self._send_raw({"command": "ping", "id": ping_id})

            try:
                event = queue.get(timeout=timeout)
                return event.data
            except Empty:
                return None
        except Exception:
            return None
        finally:
            self._unregister_queue(ping_id)

    def probe(self, timeout=30):
        """API-level canary: send a minimal prompt through the full Gemini
        round-trip and measure latency.

        Unlike ping() which only tests the local stdin/stdout pipe, probe()
        exercises the full path: Python → Node.js → HTTPS → Google → back.

        Token cost: ~10 in, ~2 out (negligible).

        Returns:
            dict with {latency_ms, status, error} or None if bridge not alive.
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
            logger.debug(
                "[HEALTH] probe timeout | latency=%dms", latency_ms,
            )
            return {"latency_ms": latency_ms, "status": "timeout",
                    "error": "probe_timeout"}
        except Exception as e:
            latency_ms = int((time.time() - t0) * 1000)
            logger.debug(
                "[HEALTH] probe error | latency=%dms err=%s", latency_ms, e,
            )
            return {"latency_ms": latency_ms, "status": "error",
                    "error": str(e)}

    # ──────────────────────────────────────────────────────────────────────
    # Internal: Reader thread
    # ──────────────────────────────────────────────────────────────────────

    def _reader_loop(self):
        """
        Background thread: reads JSON lines from bridge stdout,
        parses them, and routes to the correct request queue.
        """
        proc = self._process
        if not proc or not proc.stdout:
            return

        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug(
                        f"GeminiBridge: non-JSON stdout: {line[:200]}"
                    )
                    continue

                # Skip non-dict JSON (bare ints, arrays, strings)
                # from Gemini CLI boot output (experiment IDs, etc.)
                if not isinstance(data, dict):
                    logger.debug(
                        f"GeminiBridge: non-dict JSON: {str(data)[:100]}"
                    )
                    continue

                # Bridge-level fatal (no request ID)
                if data.get("type") == "bridge_fatal":
                    logger.error(
                        f"GeminiBridge: FATAL - code={data.get('code')} "
                        f"reason={data.get('reason')}"
                    )
                    if self._on_bridge_fatal:
                        try:
                            self._on_bridge_fatal(data)
                        except Exception:
                            pass
                    # Notify all waiting queues
                    with self._queues_lock:
                        for q in self._queues.values():
                            q.put(BridgeEvent(
                                id="__fatal__",
                                type="error",
                                data={
                                    "error": "bridge_fatal",
                                    "message": data.get("reason", "fatal"),
                                    "recoverable": False,
                                    "bridge_healthy": False,
                                }
                            ))
                    continue

                # Route by request ID
                req_id = data.get("id", "")
                event_type = data.get("type", data.get("status", "unknown"))

                event = BridgeEvent(
                    id=req_id,
                    type=event_type,
                    data=data,
                )

                with self._queues_lock:
                    q = self._queues.get(req_id)
                    if q:
                        q.put(event)
                    else:
                        # Might be a ping response or orphaned event
                        logger.debug(
                            f"GeminiBridge: unrouted event "
                            f"id={req_id} type={event_type}"
                        )

        except Exception as e:
            if proc.poll() is None:
                logger.error(f"GeminiBridge: reader error: {e}")
            # else: process exited, expected

        logger.debug("GeminiBridge: reader thread exiting")

    def _stderr_drain(self):
        """
        Background thread: drains bridge stderr to our logger.

        Also watches for the "Bridge ready" signal from the bridge
        and sets _ready_event when detected.
        """
        proc = self._process
        if not proc or not proc.stderr:
            return

        try:
            for line in proc.stderr:
                line = line.rstrip()
                if line:
                    logger.debug(f"[bridge] {line}")
                    # Detect bridge readiness signal
                    if "Bridge ready" in line:
                        self._ready_event.set()
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────────────
    # Internal: Queue management
    # ──────────────────────────────────────────────────────────────────────

    def _register_queue(self, request_id):
        """Create an event queue for a request ID."""
        q = Queue()
        with self._queues_lock:
            self._queues[request_id] = q
        return q

    def _unregister_queue(self, request_id):
        """Remove an event queue."""
        with self._queues_lock:
            self._queues.pop(request_id, None)

    # ──────────────────────────────────────────────────────────────────────
    # Internal: Send
    # ──────────────────────────────────────────────────────────────────────

    def _send_raw(self, obj):
        """Send a JSON line to the bridge's stdin.

        9.0 T1: Serialized via _send_lock to prevent interleaved writes
        when multiple threads access the same bridge concurrently.
        """
        if not self._process or self._process.poll() is not None:
            raise RuntimeError("Bridge process is not running")

        line = json.dumps(obj, ensure_ascii=False) + "\n"
        with self._send_lock:
            try:
                self._process.stdin.write(line)
                self._process.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                raise RuntimeError(f"Bridge stdin write failed: {e}")

    # ──────────────────────────────────────────────────────────────────────
    # Internal: Path resolution
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _find_bridge_script():
        """Locate gemini_bridge.mjs relative to this file."""
        # This file: src/agents/gemini_bridge_wrapper.py
        # Bridge:    src/gemini/gemini_bridge.mjs
        candidates = [
            Path(__file__).parent.parent / "gemini" / "gemini_bridge.mjs",
            Path.cwd() / "src" / "gemini" / "gemini_bridge.mjs",
        ]
        for p in candidates:
            if p.exists():
                return str(p.resolve())
        return None

    @staticmethod
    def _find_node():
        """Locate the Node.js executable."""
        import shutil
        # Try common Windows locations first
        if sys.platform == "win32":
            for path in [
                Path(os.environ.get("ProgramFiles", "")) / "nodejs" / "node.exe",
                Path(os.environ.get("APPDATA", "")) / "nvm" / "current" / "node.exe",
            ]:
                if path.exists():
                    return str(path)
        return shutil.which("node") or shutil.which("node.exe")

    @staticmethod
    def _get_api_key():
        """Load saved Gemini API key, if any."""
        try:
            from src.data import pat_store
            return pat_store.load_setting("gemini_api_key") or ""
        except Exception:
            return ""

    # ──────────────────────────────────────────────────────────────────────
    # Diagnostics
    # ──────────────────────────────────────────────────────────────────────

    @property
    def boot_count(self):
        return self._boot_count

    @property
    def total_calls(self):
        return self._total_calls

    @property
    def last_error(self):
        return self._last_error

    def get_stats(self):
        """Return bridge stats dict (enhanced in 6.2 with health monitor fields)."""
        ping_data = {}
        if self.is_alive():
            try:
                ping_data = self.ping(timeout=5) or {}
            except Exception:
                pass

        return {
            "alive": self.is_alive(),
            "healthy": self._bridge_healthy,
            "boot_count": self._boot_count,
            "total_calls": self._total_calls,
            "last_error": self._last_error,
            # 6.2 health monitor fields
            "death_count": self._death_count,
            "stall_count": self._stall_count,
            "consecutive_stalls": self._consecutive_stalls,
            "last_death_time": self._last_death_time,
            "boot_time": self._boot_time,
            # Bridge-side ping data
            "uptime_ms": ping_data.get("uptime_ms"),
            "bridge_calls": ping_data.get("calls"),
            "active_calls": ping_data.get("active_calls"),
        }

    def __repr__(self):
        status = "alive" if self.is_alive() else "dead"
        return (
            f"GeminiBridge(status={status}, "
            f"boots={self._boot_count}, "
            f"calls={self._total_calls})"
        )
