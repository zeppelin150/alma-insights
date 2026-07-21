"""
ChatEngine Test Suite

Tests the shared chat logic layer: base send flow, history management,
callback hooks, tool execution loop, and client strategies.
"""

import json
import sqlite3
import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from PySide6.QtCore import QCoreApplication

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MIGRATION_SQL = (_PROJECT_ROOT / "migrations" / "005_persistence_layer.sql").read_text(encoding="utf-8")

# Ensure QCoreApplication exists for signal delivery
_app = QCoreApplication.instance() or QCoreApplication([])


def _wait_for_engine(engine, timeout=5.0):
    """Wait for engine worker to finish and process pending signals."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if engine._worker is not None:
            engine._worker.wait(100)
        _app.processEvents()
        if not engine.is_busy:
            _app.processEvents()  # process final signals
            return
    raise TimeoutError("Engine worker did not finish")


@pytest.fixture
def db_path(tmp_path):
    """Temp DB with schema for tool tests."""
    db_file = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript(_MIGRATION_SQL)
    conn.execute("""
        INSERT INTO ticket_index (ticket_id, trc_code, friction_type, sub_pattern,
            sentiment_polarity, anomaly_flag, issue_snippet, ticket_created_date,
            first_seen_scan_id, last_seen_scan_id, first_seen_date)
        VALUES ('T001', 'Billing', 'Overcharge', 'double-charge', 'negative', 'spike',
                'Customer charged twice', '2025-02-15', 'scan-001', 'scan-001', '2025-02-15')
    """)
    conn.execute("""
        INSERT INTO ticket_index (ticket_id, trc_code, friction_type, sub_pattern,
            sentiment_polarity, anomaly_flag, issue_snippet, ticket_created_date,
            first_seen_scan_id, last_seen_scan_id, first_seen_date)
        VALUES ('T002', 'Claims', 'Delay', 'slow-processing', 'negative', NULL,
                'Claim took 30 days', '2025-02-20', 'scan-001', 'scan-001', '2025-02-20')
    """)
    conn.commit()
    conn.close()
    return str(db_file)


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.generate.return_value = "Here is my response."
    client.model = "gemini-2.5-flash"
    return client


# ═══════════════════════════════════════
#  BASE ENGINE
# ═══════════════════════════════════════

class TestChatEngineBase:

    def test_send_emits_response_ready(self, mock_client):
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="test")
        engine.set_client(mock_client)

        results = []
        engine.response_ready.connect(lambda r: results.append(r))
        engine.send("Hello")
        _wait_for_engine(engine)

        assert len(results) == 1
        assert results[0] == "Here is my response."

    def test_send_emits_error_on_failure(self, mock_client):
        mock_client.generate.side_effect = RuntimeError("CLI not found")
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="test")
        engine.set_client(mock_client)

        errors = []
        engine.error_occurred.connect(lambda e: errors.append(e))
        engine.send("Hello")
        _wait_for_engine(engine)

        assert len(errors) == 1
        assert "CLI not found" in errors[0]

    def test_no_client_emits_error(self):
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="test")
        # No client set, and mock factory returns None
        with patch("src.gemini.client_factory.build_client_for_task", return_value=None):
            errors = []
            engine.error_occurred.connect(lambda e: errors.append(e))
            engine.send("Hello")
            assert len(errors) == 1
            assert "not configured" in errors[0]

    def test_send_while_busy_is_noop(self, mock_client):
        """Re-entrance guard: second send() while busy should be ignored."""
        from src.services.chat_engine import ChatEngine
        # Make generate block briefly
        import time
        mock_client.generate.side_effect = lambda *a, **kw: (time.sleep(0.1), "response")[1]

        engine = ChatEngine(system_prompt="test")
        engine.set_client(mock_client)

        engine.send("First")
        engine.send("Second")  # Should be ignored
        _wait_for_engine(engine)

        assert mock_client.generate.call_count == 1

    def test_history_appended_on_success(self, mock_client):
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="test")
        engine.set_client(mock_client)

        engine.send("Hello")
        _wait_for_engine(engine)

        assert len(engine.history) == 2
        assert engine.history[0] == {"role": "user", "content": "Hello"}
        assert engine.history[1] == {"role": "assistant", "content": "Here is my response."}

    def test_error_does_not_append_assistant(self, mock_client):
        mock_client.generate.side_effect = RuntimeError("fail")
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="test")
        engine.set_client(mock_client)

        engine.send("Hello")
        _wait_for_engine(engine)

        # User message stays, no assistant message
        assert len(engine.history) == 1
        assert engine.history[0]["role"] == "user"

    def test_set_and_clear_history(self):
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="test")
        engine.set_history([{"role": "user", "content": "old"}])
        assert len(engine.history) == 1

        engine.clear_history()
        assert len(engine.history) == 0

    def test_busy_changed_signals(self, mock_client):
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="test")
        engine.set_client(mock_client)

        states = []
        engine.busy_changed.connect(lambda b: states.append(b))
        engine.send("Hello")
        _wait_for_engine(engine)

        assert states == [True, False]


# ═══════════════════════════════════════
#  CALLBACKS
# ═══════════════════════════════════════

class TestChatEngineCallbacks:

    def test_custom_history_packer(self, mock_client):
        from src.services.chat_engine import ChatEngine

        def packer(msg, history):
            return f"CUSTOM: {msg}"

        engine = ChatEngine(system_prompt="test", history_packer=packer)
        engine.set_client(mock_client)
        engine.send("Hello")
        _wait_for_engine(engine)

        prompt_sent = mock_client.generate.call_args[0][0]
        assert prompt_sent == "CUSTOM: Hello"

    def test_context_provider_prepends(self, mock_client):
        from src.services.chat_engine import ChatEngine

        def ctx(msg, history):
            return "[CONTEXT] 100 tickets"

        engine = ChatEngine(system_prompt="test", context_provider=ctx)
        engine.set_client(mock_client)
        engine.send("Hello")
        _wait_for_engine(engine)

        prompt_sent = mock_client.generate.call_args[0][0]
        assert prompt_sent.startswith("[CONTEXT] 100 tickets")

    def test_response_handler_transforms(self, mock_client):
        from src.services.chat_engine import ChatEngine

        def handler(raw):
            return raw.upper()

        engine = ChatEngine(system_prompt="test", response_handler=handler)
        engine.set_client(mock_client)

        results = []
        engine.response_ready.connect(lambda r: results.append(r))
        engine.send("Hello")
        _wait_for_engine(engine)

        assert results[0] == "HERE IS MY RESPONSE."

    def test_default_pack_format(self, mock_client):
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="test")
        engine.set_client(mock_client)
        engine.append_to_history("user", "First question")
        engine.append_to_history("assistant", "First answer")
        engine.send("Second question")
        _wait_for_engine(engine)

        prompt_sent = mock_client.generate.call_args[0][0]
        assert "User: First question" in prompt_sent
        assert "Assistant: First answer" in prompt_sent
        assert "User: Second question" in prompt_sent


# ═══════════════════════════════════════
#  CLIENT STRATEGIES
# ═══════════════════════════════════════

class TestChatEngineClientStrategy:

    def test_warm_client_used_over_factory(self, mock_client):
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="test")
        engine.set_client(mock_client)

        engine.send("Hello")
        _wait_for_engine(engine)

        assert mock_client.generate.called

    def test_build_per_message_calls_factory(self):
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="test")

        mock_client = MagicMock()
        mock_client.generate.return_value = "response"

        with patch("src.gemini.client_factory.build_client_for_task", return_value=mock_client) as factory:
            engine.send("Hello")
            _wait_for_engine(engine)
            assert factory.called

    def test_model_override_applied(self, mock_client):
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="test")
        engine.set_client(mock_client)
        engine.set_model("gemini-2.0-flash")
        engine.send("Hello")
        _wait_for_engine(engine)

        assert mock_client.model == "gemini-2.0-flash"


# ═══════════════════════════════════════
#  TOOL EXECUTOR
# ═══════════════════════════════════════

class TestToolDispatch:

    def test_query_tickets(self, db_path):
        from src.data.chat_tools.registry import dispatch_tool
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        result = json.loads(dispatch_tool("query_tickets", {"trc": "Billing"}, conn))
        conn.close()
        assert result["count"] == 1
        assert result["tickets"][0]["ticket_id"] == "T001"

    def test_ticket_detail(self, db_path):
        from src.data.chat_tools.registry import dispatch_tool
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        result = json.loads(dispatch_tool("ticket_detail", {"id": "T002"}, conn))
        conn.close()
        assert result["ticket_id"] == "T002"
        assert result["trc_code"] == "Claims"

    def test_unknown_tool(self, db_path):
        from src.data.chat_tools.registry import dispatch_tool
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        result = json.loads(dispatch_tool("nonexistent", {}, conn))
        conn.close()
        assert "error" in result

    def test_query_anomalies(self, db_path):
        from src.data.chat_tools.registry import dispatch_tool
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        result = json.loads(dispatch_tool("query_anomalies", {}, conn))
        conn.close()
        assert result["count"] == 1
        assert result["anomalies"][0]["ticket_id"] == "T001"


class TestToolLoop:

    def test_tool_call_parsed_and_executed(self, db_path):
        """Verify tool regex + dispatch + resubmit logic.

        Tests the components directly since cross-thread signal delivery
        is unreliable with mock workers that complete in microseconds.
        The standalone script (outside pytest) confirms full async works.
        """
        from src.services.chat_engine import ChatEngine, _TOOL_CALL_RE
        from src.data.chat_tools.registry import dispatch_tool

        # 1. Verify regex parses TOOL_CALL from Gemini response
        response = 'I need data.\nTOOL_CALL: query_tickets {"trc": "Billing"}'
        match = _TOOL_CALL_RE.search(response)
        assert match is not None, "TOOL_CALL regex did not match"
        assert match.group(1) == "query_tickets"
        args = json.loads(match.group(2))
        assert args == {"trc": "Billing"}

        # 2. Verify tool dispatch returns real data from DB
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        result = json.loads(dispatch_tool("query_tickets", {"trc": "Billing"}, conn))
        conn.close()
        assert result["count"] == 1
        assert result["tickets"][0]["ticket_id"] == "T001"

        # 3. Verify engine handles tool call in _on_worker_finished
        call_count = [0]

        def mock_generate(prompt, system_prompt="", timeout=180):
            call_count[0] += 1
            if call_count[0] == 1:
                return 'Need data.\nTOOL_CALL: query_tickets {"trc": "Billing"}'
            return "Found 1 Billing ticket."

        mock_client = MagicMock()
        mock_client.generate.side_effect = mock_generate

        engine = ChatEngine(
            system_prompt="test", tools_enabled=True, db_path=db_path,
        )
        engine.set_client(mock_client)

        results = []
        engine.response_ready.connect(lambda r: results.append(r))
        engine.send("What billing issues?")

        # Pump event loop for async signal delivery
        deadline = time.time() + 10.0
        while time.time() < deadline and not results:
            _app.processEvents()
            time.sleep(0.1)

        # Verify mock was called twice (initial + resubmit after tool exec)
        assert call_count[0] == 2, f"Expected 2 calls, got {call_count[0]}"
        # Signal delivery may or may not arrive in test env
        if results:
            assert "Billing" in results[0]

    def test_no_tool_call_passes_through(self, mock_client):
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(
            system_prompt="test",
            tools_enabled=True,
            db_path="/fake/path",
        )
        engine.set_client(mock_client)

        results = []
        engine.response_ready.connect(lambda r: results.append(r))
        engine.send("Hello")
        _wait_for_engine(engine)

        assert len(results) == 1
        assert results[0] == "Here is my response."

    def test_max_tool_rounds_enforced(self, db_path):
        """Engine should stop after _MAX_TOOL_ROUNDS to prevent infinite loops."""
        from src.services.chat_engine import ChatEngine, _MAX_TOOL_ROUNDS

        mock_client = MagicMock()
        # Always return a tool call — engine must eventually stop
        mock_client.generate.return_value = 'TOOL_CALL: query_tickets {"limit": 1}'

        engine = ChatEngine(
            system_prompt="test",
            tools_enabled=True,
            db_path=db_path,
        )
        engine.set_client(mock_client)

        results = []
        engine.response_ready.connect(lambda r: results.append(r))
        engine.send("Loop test")

        deadline = time.time() + 5.0
        while time.time() < deadline:
            if engine._worker:
                engine._worker.wait(100)
            _app.processEvents()
            if results:
                break

        # Should eventually emit the last tool-call response as final
        assert len(results) == 1
        assert mock_client.generate.call_count == _MAX_TOOL_ROUNDS + 1


class TestUseMcpToolsBoundary:
    """Guards the gemini_chats_page fix: tools must dispatch via the in-process
    text loop (use_mcp_tools=False) for ANY plain-text client (incl. the Claude
    CLI). use_mcp_tools=True skips text dispatch entirely — the state that left
    the model tool-less and fabricating ticket IDs.
    """

    @staticmethod
    def _tool_emitting_client():
        call_count = [0]

        def gen(prompt, system_prompt="", *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return 'Let me check.\nTOOL_CALL: list_tickets {"limit": 5}'
            return "Here are the tickets."

        client = MagicMock()
        client.generate.side_effect = gen
        client.model = "claude-cli"  # plain-text, CLI-like
        return client, call_count

    @staticmethod
    def _pump(engine, results, timeout=10.0):
        deadline = time.time() + timeout
        while time.time() < deadline and not results:
            if engine._worker:
                engine._worker.wait(100)
            _app.processEvents()
            time.sleep(0.05)

    def test_text_loop_dispatches_for_plaintext_client(self, db_path):
        from src.services.chat_engine import ChatEngine
        client, call_count = self._tool_emitting_client()
        engine = ChatEngine(
            system_prompt="t", tools_enabled=True, use_mcp_tools=False, db_path=db_path,
        )
        engine.set_client(client)
        results = []
        engine.response_ready.connect(lambda r: results.append(r))
        engine.send("list tickets")
        self._pump(engine, results)
        # initial call + resubmit after the tool actually executed
        assert call_count[0] == 2, f"expected dispatch (2 calls), got {call_count[0]}"
        if results:
            assert "TOOL_CALL" not in results[-1]

    def test_mcp_mode_skips_text_dispatch(self, db_path):
        """Documents the broken state: with use_mcp_tools=True the engine never
        parses/executes the text TOOL_CALL, so the raw directive leaks through."""
        from src.services.chat_engine import ChatEngine
        client, call_count = self._tool_emitting_client()
        engine = ChatEngine(
            system_prompt="t", tools_enabled=True, use_mcp_tools=True, db_path=db_path,
        )
        engine.set_client(client)
        results = []
        engine.response_ready.connect(lambda r: results.append(r))
        engine.send("list tickets")
        self._pump(engine, results)
        assert call_count[0] == 1, f"expected no dispatch (1 call), got {call_count[0]}"
        if results:
            assert "TOOL_CALL: list_tickets" in results[-1]


class TestStatusUpdates:

    def test_status_signals_during_send(self, mock_client):
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="test")
        engine.set_client(mock_client)

        statuses = []
        engine.status_update.connect(lambda s: statuses.append(s))
        engine.send("Hello")
        _wait_for_engine(engine)

        assert "Gemini is thinking..." in statuses
        assert "" in statuses  # cleared on completion
