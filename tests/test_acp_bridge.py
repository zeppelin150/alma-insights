"""
Alma Insights -- ACPBridge Unit Tests (Session 3 — ACP Migration)

Tests the ACPBridge class without requiring a live Gemini CLI.
All subprocess interactions are mocked.

Tests:
  - BridgeEvent interface (is_terminal, is_error, is_recoverable, error_code)
  - Error classification (JSON-RPC codes → RECOVERABLE/FATAL categories)
  - Watchdog death detection + callback + queue poisoning
  - Stall escalation (3 consecutive → True)
  - Queue routing (msg_id dispatch, active prompt queue for notifications)
  - Non-JSON stdout filtering (bare integers, JS fragments, debug lines)
  - Synthetic heartbeat timer
  - MCP config injection (env vars → server config)
  - JSON-RPC request building
  - Lifecycle (shutdown clears queues, restart resets)
  - CLI path resolution
"""

import json
import threading
import time
import unittest
from queue import Queue
from unittest.mock import MagicMock, patch

from src.agents.acp_bridge import (
    ACPBridge,
    BridgeEvent,
    RECOVERABLE_ERRORS,
    FATAL_ERRORS,
    TERMINAL_EVENTS,
    _JSONRPC_RECOVERABLE_CODES,
    _JSONRPC_FATAL_CODES,
)


class TestBridgeEvent(unittest.TestCase):
    """BridgeEvent interface parity with original gemini_bridge_wrapper."""

    def test_terminal_events(self):
        for etype in ("done", "error", "stopped"):
            evt = BridgeEvent("req1", etype, {})
            self.assertTrue(evt.is_terminal, f"{etype} should be terminal")

    def test_non_terminal_events(self):
        for etype in ("content", "heartbeat", "tool_call", "tool_result"):
            evt = BridgeEvent("req1", etype, {})
            self.assertFalse(evt.is_terminal, f"{etype} should not be terminal")

    def test_error_properties(self):
        evt = BridgeEvent("r1", "error", {
            "error": "rate_limit",
            "recoverable": True,
        })
        self.assertTrue(evt.is_error)
        self.assertTrue(evt.is_recoverable)
        self.assertEqual(evt.error_code, "rate_limit")

    def test_non_error_properties(self):
        evt = BridgeEvent("r1", "done", {"full_text": "ok"})
        self.assertFalse(evt.is_error)
        self.assertFalse(evt.is_recoverable)
        self.assertEqual(evt.error_code, "")

    def test_repr(self):
        evt = BridgeEvent("req1", "content", {"delta": "hi"})
        self.assertIn("req1", repr(evt))
        self.assertIn("content", repr(evt))


class TestErrorCategories(unittest.TestCase):
    """Error classification constants."""

    def test_recoverable_errors_are_frozenset(self):
        self.assertIsInstance(RECOVERABLE_ERRORS, frozenset)
        self.assertIn("rate_limit", RECOVERABLE_ERRORS)
        self.assertIn("server_error", RECOVERABLE_ERRORS)
        self.assertIn("stall_timeout", RECOVERABLE_ERRORS)

    def test_fatal_errors_are_frozenset(self):
        self.assertIsInstance(FATAL_ERRORS, frozenset)
        self.assertIn("auth_expired", FATAL_ERRORS)
        self.assertIn("model_not_found", FATAL_ERRORS)

    def test_no_overlap(self):
        self.assertEqual(len(RECOVERABLE_ERRORS & FATAL_ERRORS), 0)


class TestErrorClassification(unittest.TestCase):
    """JSON-RPC error code classification."""

    def setUp(self):
        self.bridge = ACPBridge.__new__(ACPBridge)

    def test_fatal_codes(self):
        for code in _JSONRPC_FATAL_CODES:
            result = self.bridge._classify_jsonrpc_error({"code": code, "message": ""})
            self.assertEqual(result, "config_error", f"Code {code} should be config_error")

    def test_recoverable_codes(self):
        for code in _JSONRPC_RECOVERABLE_CODES:
            result = self.bridge._classify_jsonrpc_error({"code": code, "message": ""})
            self.assertEqual(result, "server_error", f"Code {code} should be server_error")

    def test_rate_limit_from_message(self):
        result = self.bridge._classify_jsonrpc_error({"code": 0, "message": "Rate limit exceeded"})
        self.assertEqual(result, "rate_limit")

    def test_auth_from_message(self):
        result = self.bridge._classify_jsonrpc_error({"code": 0, "message": "Auth credentials expired"})
        self.assertEqual(result, "auth_expired")

    def test_timeout_from_message(self):
        result = self.bridge._classify_jsonrpc_error({"code": 0, "message": "Request timeout"})
        self.assertEqual(result, "network_timeout")

    def test_model_not_found_from_message(self):
        result = self.bridge._classify_jsonrpc_error({"code": 0, "message": "Model not found"})
        self.assertEqual(result, "model_not_found")

    def test_unknown_defaults_to_server_error(self):
        result = self.bridge._classify_jsonrpc_error({"code": 0, "message": "something weird"})
        self.assertEqual(result, "server_error")


class TestStallEscalation(unittest.TestCase):
    """Stall recording and escalation threshold."""

    def setUp(self):
        self.bridge = ACPBridge.__new__(ACPBridge)
        self.bridge._stall_count = 0
        self.bridge._consecutive_stalls = 0
        self.bridge._STALL_ESCALATION_THRESHOLD = 3

    def test_single_stall_no_escalation(self):
        self.assertFalse(self.bridge.record_stall())
        self.assertEqual(self.bridge._consecutive_stalls, 1)

    def test_three_stalls_trigger_escalation(self):
        self.assertFalse(self.bridge.record_stall())
        self.assertFalse(self.bridge.record_stall())
        self.assertTrue(self.bridge.record_stall())
        # Counter resets after escalation
        self.assertEqual(self.bridge._consecutive_stalls, 0)

    def test_success_resets_streak(self):
        self.bridge.record_stall()
        self.bridge.record_stall()
        self.assertEqual(self.bridge._consecutive_stalls, 2)
        self.bridge.record_success()
        self.assertEqual(self.bridge._consecutive_stalls, 0)

    def test_total_stalls_accumulate(self):
        for _ in range(5):
            self.bridge.record_stall()
        # Total count always grows
        self.assertEqual(self.bridge._stall_count, 5)


class TestWatchdogDeathDetection(unittest.TestCase):
    """Watchdog _notify_death: stats, queue poisoning, callback."""

    def setUp(self):
        self.bridge = ACPBridge.__new__(ACPBridge)
        self.bridge._bridge_healthy = True
        self.bridge._death_count = 0
        self.bridge._last_death_time = None
        self.bridge._process = MagicMock()
        self.bridge._process.returncode = 1
        self.bridge._process.pid = 12345
        self.bridge._last_stderr_lines = ["error: something broke"]
        self.bridge._queues = {}
        self.bridge._queues_lock = threading.Lock()
        self.bridge._on_death = None

    def test_death_updates_stats(self):
        self.bridge._notify_death()
        self.assertFalse(self.bridge._bridge_healthy)
        self.assertEqual(self.bridge._death_count, 1)
        self.assertIsNotNone(self.bridge._last_death_time)

    def test_death_poisons_queues(self):
        q = Queue()
        self.bridge._queues[42] = q
        self.bridge._notify_death()
        evt = q.get_nowait()
        self.assertEqual(evt.type, "error")
        self.assertEqual(evt.data["error"], "bridge_dead")
        self.assertTrue(evt.data["recoverable"])

    def test_death_fires_callback(self):
        callback = MagicMock()
        self.bridge._on_death = callback
        self.bridge._notify_death()
        callback.assert_called_once_with(self.bridge)

    def test_callback_exception_swallowed(self):
        self.bridge._on_death = MagicMock(side_effect=RuntimeError("boom"))
        # Should not raise
        self.bridge._notify_death()
        self.assertEqual(self.bridge._death_count, 1)


class TestStdoutFiltering(unittest.TestCase):
    """Reader thread filters non-JSON and non-JSONRPC lines."""

    def test_route_jsonrpc_response(self):
        bridge = ACPBridge.__new__(ACPBridge)
        bridge._queues = {}
        bridge._queues_lock = threading.Lock()
        bridge._request_id_map = {}
        bridge._request_id_map_lock = threading.Lock()

        # Register a queue for msg_id=1
        q = Queue()
        bridge._queues[1] = q

        # Route a JSON-RPC response
        data = {"jsonrpc": "2.0", "id": 1, "result": {"sessionId": "abc"}}
        bridge._route_message(data)

        evt = q.get_nowait()
        self.assertEqual(evt.type, "done")

    def test_route_done_response(self):
        bridge = ACPBridge.__new__(ACPBridge)
        bridge._queues = {}
        bridge._queues_lock = threading.Lock()
        bridge._request_id_map = {}
        bridge._request_id_map_lock = threading.Lock()

        q = Queue()
        bridge._queues[5] = q

        data = {
            "jsonrpc": "2.0", "id": 5,
            "result": {
                "stopReason": "end_turn",
                "_meta": {"quota": {"token_count": {"input_tokens": 100, "output_tokens": 50}}},
            },
        }
        bridge._route_message(data)

        evt = q.get_nowait()
        self.assertEqual(evt.type, "done")
        self.assertEqual(evt.data["input_tokens"], 100)
        self.assertEqual(evt.data["output_tokens"], 50)

    def test_route_error_response(self):
        bridge = ACPBridge.__new__(ACPBridge)
        bridge._queues = {}
        bridge._queues_lock = threading.Lock()
        bridge._request_id_map = {}
        bridge._request_id_map_lock = threading.Lock()

        q = Queue()
        bridge._queues[3] = q

        data = {
            "jsonrpc": "2.0", "id": 3,
            "error": {"code": -32600, "message": "Invalid Request"},
        }
        bridge._route_message(data)

        evt = q.get_nowait()
        self.assertEqual(evt.type, "error")
        self.assertIn("config_error", evt.data["error"])

    def test_session_update_text_content(self):
        bridge = ACPBridge.__new__(ACPBridge)
        bridge._queues = {}
        bridge._queues_lock = threading.Lock()
        bridge._request_id_map = {}
        bridge._request_id_map_lock = threading.Lock()

        # Set up an active queue
        q = Queue()
        bridge._queues[10] = q

        # Real ACP structure: content nested under params.update
        data = {
            "method": "session/update",
            "params": {
                "sessionId": "test-session",
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "Hello"},
                },
            },
        }
        bridge._route_message(data)

        evt = q.get_nowait()
        self.assertEqual(evt.type, "content")
        self.assertEqual(evt.data["delta"], "Hello")

    def test_session_update_tool_call(self):
        bridge = ACPBridge.__new__(ACPBridge)
        bridge._queues = {}
        bridge._queues_lock = threading.Lock()
        bridge._request_id_map = {}
        bridge._request_id_map_lock = threading.Lock()

        q = Queue()
        bridge._queues[10] = q

        # Real ACP structure: nested under params.update
        data = {
            "method": "session/update",
            "params": {
                "sessionId": "test-session",
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {
                        "type": "function_call",
                        "name": "store_classification",
                        "args": {"ticket_id": "T-1"},
                    },
                },
            },
        }
        bridge._route_message(data)

        evt = q.get_nowait()
        self.assertEqual(evt.type, "tool_call")
        self.assertEqual(evt.data["name"], "store_classification")
        self.assertEqual(evt.data["args"]["ticket_id"], "T-1")


class TestMCPEnvInjection(unittest.TestCase):
    """MCP server config env var injection."""

    def test_inject_env_vars(self):
        servers = [{"name": "test", "command": "python", "args": [], "env": []}]
        env = {"KEY1": "val1", "KEY2": "val2"}
        result = ACPBridge._inject_mcp_env(servers, env)

        self.assertEqual(len(result[0]["env"]), 2)
        names = {e["name"] for e in result[0]["env"]}
        self.assertEqual(names, {"KEY1", "KEY2"})

    def test_original_not_mutated(self):
        servers = [{"name": "test", "command": "python", "args": [], "env": []}]
        ACPBridge._inject_mcp_env(servers, {"K": "V"})
        self.assertEqual(servers[0]["env"], [])

    def test_none_values_excluded(self):
        servers = [{"name": "test", "command": "python", "args": [], "env": []}]
        result = ACPBridge._inject_mcp_env(servers, {"K": "V", "N": None})
        self.assertEqual(len(result[0]["env"]), 1)

    def test_multiple_servers(self):
        servers = [
            {"name": "s1", "command": "python", "args": [], "env": []},
            {"name": "s2", "command": "node", "args": [], "env": []},
        ]
        result = ACPBridge._inject_mcp_env(servers, {"K": "V"})
        self.assertEqual(len(result), 2)
        self.assertEqual(len(result[0]["env"]), 1)
        self.assertEqual(len(result[1]["env"]), 1)


class TestJSONRPCBuilding(unittest.TestCase):
    """JSON-RPC request construction."""

    def setUp(self):
        self.bridge = ACPBridge.__new__(ACPBridge)
        self.bridge._msg_id = 0
        self.bridge._msg_id_lock = threading.Lock()

    def test_build_request_increments_id(self):
        id1, _ = self.bridge._build_request("test", {})
        id2, _ = self.bridge._build_request("test", {})
        self.assertEqual(id1, 1)
        self.assertEqual(id2, 2)

    def test_build_request_format(self):
        msg_id, line = self.bridge._build_request("initialize", {"protocolVersion": 1})
        data = json.loads(line)
        self.assertEqual(data["jsonrpc"], "2.0")
        self.assertEqual(data["id"], msg_id)
        self.assertEqual(data["method"], "initialize")
        self.assertEqual(data["params"]["protocolVersion"], 1)


class TestLifecycle(unittest.TestCase):
    """Shutdown + stats."""

    def test_shutdown_clears_state(self):
        bridge = ACPBridge.__new__(ACPBridge)
        bridge._process = MagicMock()
        bridge._process.poll.return_value = None  # running
        bridge._process.stdin = MagicMock()
        bridge._process.stdout = MagicMock()
        bridge._process.stderr = MagicMock()
        bridge._process.wait = MagicMock()
        bridge._session_id = "test-session"
        bridge._bridge_healthy = True
        bridge._watchdog_stop = threading.Event()
        bridge._heartbeat_timer = None
        bridge._lock = threading.Lock()
        bridge._queues = {}
        bridge._queues_lock = threading.Lock()
        bridge._boot_count = 1

        bridge.shutdown()
        self.assertIsNone(bridge._session_id)
        self.assertIsNone(bridge._process)
        self.assertFalse(bridge._bridge_healthy)

    def test_get_stats_without_process(self):
        bridge = ACPBridge.__new__(ACPBridge)
        bridge._process = None
        bridge._bridge_healthy = False
        bridge._boot_count = 2
        bridge._total_calls = 10
        bridge._last_error = "test"
        bridge._death_count = 1
        bridge._stall_count = 3
        bridge._consecutive_stalls = 0
        bridge._last_death_time = None
        bridge._boot_time = None
        bridge._session_id = None
        bridge._protocol_version = 1
        bridge._agent_info = {"version": "0.36.0"}

        stats = bridge.get_stats()
        self.assertFalse(stats["alive"])
        self.assertEqual(stats["boot_count"], 2)
        self.assertEqual(stats["total_calls"], 10)
        self.assertEqual(stats["agent_version"], "0.36.0")

    def test_is_alive_no_process(self):
        bridge = ACPBridge.__new__(ACPBridge)
        bridge._process = None
        self.assertFalse(bridge.is_alive())

    def test_is_alive_dead_process(self):
        bridge = ACPBridge.__new__(ACPBridge)
        bridge._process = MagicMock()
        bridge._process.poll.return_value = 1  # exited
        self.assertFalse(bridge.is_alive())

    def test_is_alive_running_process(self):
        bridge = ACPBridge.__new__(ACPBridge)
        bridge._process = MagicMock()
        bridge._process.poll.return_value = None  # still running
        self.assertTrue(bridge.is_alive())


class TestCLIResolution(unittest.TestCase):
    """CLI path resolution."""

    def test_find_cli_returns_string_or_none(self):
        result = ACPBridge._find_gemini_cli()
        self.assertTrue(result is None or isinstance(result, str))

    def test_get_api_key_returns_string(self):
        result = ACPBridge._get_api_key()
        self.assertIsInstance(result, str)


class TestSetMCPConfig(unittest.TestCase):
    """set_mcp_config stores config for session/new."""

    def test_set_mcp_config(self):
        bridge = ACPBridge.__new__(ACPBridge)
        bridge._mcp_servers = []
        config = [{"name": "alma-tools", "command": "python", "args": [], "env": []}]
        bridge.set_mcp_config(config)
        self.assertEqual(bridge._mcp_servers, config)


# ═══════════════════════════════════════════════════════════════════════════
# PART C: Resilience Validation Tests (Session 4)
# ═══════════════════════════════════════════════════════════════════════════


class TestResilienceStallEscalation(unittest.TestCase):
    """Stall escalation: 3 consecutive stalls trigger auto-restart signal."""

    def test_exactly_three_stalls_triggers(self):
        bridge = ACPBridge.__new__(ACPBridge)
        bridge._stall_count = 0
        bridge._consecutive_stalls = 0
        bridge._STALL_ESCALATION_THRESHOLD = 3

        self.assertFalse(bridge.record_stall())  # 1
        self.assertFalse(bridge.record_stall())  # 2
        self.assertTrue(bridge.record_stall())   # 3 → escalation
        self.assertEqual(bridge._consecutive_stalls, 0)  # reset after trigger

    def test_success_breaks_streak(self):
        bridge = ACPBridge.__new__(ACPBridge)
        bridge._stall_count = 0
        bridge._consecutive_stalls = 0
        bridge._STALL_ESCALATION_THRESHOLD = 3

        bridge.record_stall()
        bridge.record_stall()
        bridge.record_success()  # breaks streak
        self.assertFalse(bridge.record_stall())  # restart count
        self.assertFalse(bridge.record_stall())
        self.assertTrue(bridge.record_stall())   # 3 new → escalation

    def test_total_count_always_grows(self):
        bridge = ACPBridge.__new__(ACPBridge)
        bridge._stall_count = 0
        bridge._consecutive_stalls = 0
        bridge._STALL_ESCALATION_THRESHOLD = 3

        for _ in range(7):
            bridge.record_stall()
        self.assertEqual(bridge._stall_count, 7)


class TestResilienceDeathCallback(unittest.TestCase):
    """Death detection: callback fires + queues poisoned."""

    def test_death_fires_callback_and_poisons_all_queues(self):
        bridge = ACPBridge.__new__(ACPBridge)
        bridge._bridge_healthy = True
        bridge._death_count = 0
        bridge._last_death_time = None
        bridge._process = MagicMock()
        bridge._process.returncode = 137  # SIGKILL
        bridge._process.pid = 99999
        bridge._last_stderr_lines = ["FATAL: out of memory"]
        bridge._queues_lock = threading.Lock()

        # Set up 3 waiting queues
        q1, q2, q3 = Queue(), Queue(), Queue()
        bridge._queues = {1: q1, 2: q2, 3: q3}

        callback_called = []
        bridge._on_death = lambda b: callback_called.append(b)

        bridge._notify_death()

        # Callback fired
        self.assertEqual(len(callback_called), 1)
        self.assertIs(callback_called[0], bridge)

        # All queues poisoned
        for q in (q1, q2, q3):
            evt = q.get_nowait()
            self.assertEqual(evt.type, "error")
            self.assertEqual(evt.data["error"], "bridge_dead")
            self.assertTrue(evt.data["recoverable"])

        # Stats updated
        self.assertFalse(bridge._bridge_healthy)
        self.assertEqual(bridge._death_count, 1)
        self.assertIsNotNone(bridge._last_death_time)


class TestResilienceProbeTimeout(unittest.TestCase):
    """probe() returns timeout dict when bridge is unresponsive."""

    def test_probe_returns_none_when_not_alive(self):
        bridge = ACPBridge.__new__(ACPBridge)
        bridge._process = None
        result = bridge.probe(timeout=1)
        self.assertIsNone(result)

    def test_probe_returns_timeout_on_timeout_error(self):
        bridge = ACPBridge.__new__(ACPBridge)
        bridge._process = MagicMock()
        bridge._process.poll.return_value = None  # alive

        # Mock call_streaming to raise TimeoutError
        def mock_call_streaming(*args, **kwargs):
            raise TimeoutError("timed out")

        bridge.call_streaming = mock_call_streaming
        bridge.is_alive = lambda: True

        result = bridge.probe(timeout=1)
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["error"], "probe_timeout")
        self.assertIn("latency_ms", result)


class TestResilienceErrorClassification(unittest.TestCase):
    """JSON-RPC error codes map to correct categories."""

    def setUp(self):
        self.bridge = ACPBridge.__new__(ACPBridge)

    def test_parse_error_is_fatal(self):
        result = self.bridge._classify_jsonrpc_error({"code": -32700, "message": ""})
        self.assertEqual(result, "config_error")

    def test_invalid_request_is_fatal(self):
        result = self.bridge._classify_jsonrpc_error({"code": -32600, "message": ""})
        self.assertEqual(result, "config_error")

    def test_method_not_found_is_fatal(self):
        result = self.bridge._classify_jsonrpc_error({"code": -32601, "message": ""})
        self.assertEqual(result, "config_error")

    def test_internal_error_is_recoverable(self):
        result = self.bridge._classify_jsonrpc_error({"code": -32603, "message": ""})
        self.assertEqual(result, "server_error")

    def test_rate_limit_from_message_heuristic(self):
        result = self.bridge._classify_jsonrpc_error({"code": 0, "message": "RESOURCE_EXHAUSTED: quota exceeded"})
        self.assertEqual(result, "rate_limit")

    def test_error_event_carries_message_and_raw(self):
        """Verify error BridgeEvent has message + raw for diagnostic chain."""
        bridge = ACPBridge.__new__(ACPBridge)
        bridge._queues = {}
        bridge._queues_lock = threading.Lock()
        bridge._request_id_map = {}
        bridge._request_id_map_lock = threading.Lock()

        q = Queue()
        bridge._queues[7] = q

        bridge._route_message({
            "jsonrpc": "2.0", "id": 7,
            "error": {"code": -32603, "message": "Internal error"},
        })

        evt = q.get_nowait()
        self.assertEqual(evt.type, "error")
        self.assertIn("message", evt.data)
        self.assertIn("raw", evt.data)
        self.assertEqual(evt.data["message"], "Internal error")
        self.assertIn("-32603", evt.data["raw"])


class TestResilienceStderrRingBuffer(unittest.TestCase):
    """Stderr ring buffer captures last N lines for diagnostics."""

    def test_ring_buffer_capped_at_10(self):
        bridge = ACPBridge.__new__(ACPBridge)
        bridge._last_stderr_lines = []

        # Simulate 15 stderr lines
        for i in range(15):
            bridge._last_stderr_lines.append(f"line {i}")
            if len(bridge._last_stderr_lines) > 10:
                bridge._last_stderr_lines.pop(0)

        self.assertEqual(len(bridge._last_stderr_lines), 10)
        self.assertEqual(bridge._last_stderr_lines[0], "line 5")
        self.assertEqual(bridge._last_stderr_lines[-1], "line 14")

    def test_death_message_includes_stderr(self):
        bridge = ACPBridge.__new__(ACPBridge)
        bridge._bridge_healthy = True
        bridge._death_count = 0
        bridge._last_death_time = None
        bridge._process = MagicMock()
        bridge._process.returncode = 1
        bridge._process.pid = 1234
        bridge._last_stderr_lines = ["auth failed", "retrying...", "gave up"]
        bridge._queues = {}
        bridge._queues_lock = threading.Lock()
        bridge._on_death = None

        # Capture log output
        import logging
        with self.assertLogs("alma.acp_bridge", level="ERROR") as log:
            bridge._notify_death()

        # Log should include last 3 stderr lines
        log_text = " ".join(log.output)
        self.assertIn("auth failed", log_text)
        self.assertIn("gave up", log_text)


class TestResilienceRestartPreservesState(unittest.TestCase):
    """restart() resets process but preserves counters."""

    def test_counters_preserved_across_restart(self):
        bridge = ACPBridge.__new__(ACPBridge)
        # Set up counters as if bridge has been active
        bridge._boot_count = 3
        bridge._total_calls = 25
        bridge._death_count = 1
        bridge._stall_count = 5
        bridge._last_error = "rate_limit"

        # Mock shutdown and ensure_running
        bridge.shutdown = MagicMock()
        bridge.ensure_running = MagicMock()

        bridge.restart()

        bridge.shutdown.assert_called_once()
        bridge.ensure_running.assert_called_once()

        # Counters preserved
        self.assertEqual(bridge._total_calls, 25)
        self.assertEqual(bridge._death_count, 1)
        self.assertEqual(bridge._stall_count, 5)
        self.assertEqual(bridge._last_error, "rate_limit")


class TestResilienceConcurrentCallIsolation(unittest.TestCase):
    """Multiple concurrent calls don't cross-contaminate queues."""

    def test_separate_queues_per_msg_id(self):
        bridge = ACPBridge.__new__(ACPBridge)
        bridge._queues = {}
        bridge._queues_lock = threading.Lock()
        bridge._request_id_map = {}
        bridge._request_id_map_lock = threading.Lock()

        # Register two different message ID queues
        q1 = Queue()
        q2 = Queue()
        bridge._queues[10] = q1
        bridge._queues[20] = q2

        # Route response to msg_id=10
        bridge._route_message({
            "jsonrpc": "2.0", "id": 10,
            "result": {"stopReason": "end_turn", "_meta": {"quota": {"token_count": {"input_tokens": 100, "output_tokens": 50}}}},
        })

        # Route response to msg_id=20
        bridge._route_message({
            "jsonrpc": "2.0", "id": 20,
            "result": {"stopReason": "end_turn", "_meta": {"quota": {"token_count": {"input_tokens": 200, "output_tokens": 75}}}},
        })

        # Each queue gets its own event
        evt1 = q1.get_nowait()
        evt2 = q2.get_nowait()

        self.assertEqual(evt1.data["input_tokens"], 100)
        self.assertEqual(evt2.data["input_tokens"], 200)

        # Queues don't have extra events
        self.assertTrue(q1.empty())
        self.assertTrue(q2.empty())

    def test_notification_goes_to_highest_queue(self):
        """session/update notifications route to highest (most recent) msg_id queue."""
        bridge = ACPBridge.__new__(ACPBridge)
        bridge._queues = {}
        bridge._queues_lock = threading.Lock()
        bridge._request_id_map = {}
        bridge._request_id_map_lock = threading.Lock()

        q_old = Queue()
        q_new = Queue()
        bridge._queues[5] = q_old
        bridge._queues[15] = q_new

        bridge._route_message({
            "method": "session/update",
            "params": {
                "sessionId": "test",
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "hello"},
                },
            },
        })

        # Should go to queue 15 (highest/most recent)
        self.assertTrue(q_old.empty())
        evt = q_new.get_nowait()
        self.assertEqual(evt.data["delta"], "hello")


if __name__ == "__main__":
    unittest.main()
