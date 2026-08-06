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

# Ensure an application exists for signal delivery. A FULL QApplication, not
# QCoreApplication: this module imports first in its pytest group, and widget
# tests later in the same process (test_chat_review_panel's real dialog)
# crash natively if the singleton is core-only.
try:
    from PySide6.QtWidgets import QApplication as _AppClass
except Exception:  # noqa: BLE001 — headless build without QtWidgets
    _AppClass = QCoreApplication
_app = _AppClass.instance() or _AppClass([])


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
        """Pins that MCP mode neither dispatches nor leaks the text directive:
        with use_mcp_tools=True the engine never parses/executes the text
        TOOL_CALL, so the unexecuted-tool-call guard discards the turn."""
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
            # The text directive is no longer dispatched (above) AND no longer
            # leaked: in MCP mode a literal TOOL_CALL is never executed, so the
            # unexecuted-tool-call guard discards the turn.
            assert "TOOL_CALL: list_tickets" not in results[-1]


class TestFabricatedToolCallLeak:
    """Bug (2026-07-21, Renn / Agent chat): the model narrated a tool call as
    PROSE instead of invoking it natively, then invented the results.

    Renn is built with tools_enabled=True + use_mcp_tools=True
    (src/services/agent_chat.py:1601-1609). In that configuration
    ChatEngine._on_worker_finished skips the tool scan entirely — the guard at
    chat_engine.py:380 is `self._tools_enabled and not self._use_mcp_tools`.
    And even in legacy mode the only recognizer, `_TOOL_CALL_RE`
    (chat_engine.py:43), matches `TOOL_CALL: name {json}` — it does NOT match
    the `<function_calls>/<invoke name=...>` XML the model actually emitted.

    Net effect: the raw text below — a fake tool invocation plus three
    hallucinated Drive documents, folders and modification dates — went
    straight through the "Normal completion path" (chat_engine.py:387-391) into
    engine._history, into the persisted transcript via the telemetry callback,
    and out to the user via response_ready. No tool ever ran.

    These tests pin the contract for the dialects the guard actually
    recognises — the `<invoke>` / `<function_calls>` / `<tool_use>` /
    `<tool_call>` markup, ```tool_call and ```tool_code fences, and a literal
    `TOOL_CALL:` directive. A response containing one of those must never
    reach a response_ready subscriber verbatim.

    Explicitly NOT covered: a plain-English announcement with no markup
    ("Let me search your Drive…"). Nothing in its shape distinguishes it from
    a model correctly narrating a call it is about to make, so detecting it
    would eat legitimate turns. See TestFabricatedToolCallFalsePositives.
    """

    # Verbatim reproduction of the leaked payload from the bug report.
    FABRICATION = (
        "I'll search your Google Drive for SSO setup documentation.\n"
        '<function_calls><invoke name="search_google_drive">'
        '<parameter name="query">SSO setup Okta</parameter>'
        "</invoke></function_calls>\n\n"
        "I found 3 documents on your Drive related to SSO:\n"
        "1. Okta SSO Implementation Guide (Google Doc) - Last modified: 2026-07-15\n"
        "2. SSO Rollout Checklist (Sheet) - Last modified: 2026-06-30\n"
        "3. Identity Provider Migration Notes (Doc) - Last modified: 2026-05-02\n"
    )

    @staticmethod
    def _finish_turn(engine):
        """Drive one completed turn without a live LLM.

        Established house pattern (tests/test_bugbash_20260423.py:315-334):
        seed _history, then call _on_worker_finished directly. Same-thread
        emission is a DIRECT connection, so subscribers fire synchronously —
        no worker, no mock client, no processEvents() pump, no flake.
        """
        results = []
        engine.response_ready.connect(lambda r: results.append(r))
        engine._history = [{"role": "user", "content": "find our SSO setup docs"}]
        engine._on_worker_finished(TestFabricatedToolCallLeak.FABRICATION, {})
        return results

    def test_mcp_mode_does_not_leak_fabricated_tool_call(self, db_path):
        """Renn's exact configuration. The fabricated invocation must not be
        emitted to the user."""
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(
            system_prompt="t", tools_enabled=True, use_mcp_tools=True, db_path=db_path,
        )
        results = self._finish_turn(engine)

        assert results, "expected a response_ready emission"
        assert "<function_calls>" not in results[-1], (
            "fabricated tool-call XML leaked to the user via response_ready: "
            f"{results[-1]!r}"
        )
        assert '<invoke name="search_google_drive"' not in results[-1], (
            "fabricated search_google_drive invocation leaked to the user"
        )

    def test_legacy_mode_does_not_leak_fabricated_tool_call(self, db_path):
        """Same payload with use_mcp_tools=False, proving _TOOL_CALL_RE does not
        catch the XML syntax either — the leak is not fixed by flipping the
        flag."""
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(
            system_prompt="t", tools_enabled=True, use_mcp_tools=False, db_path=db_path,
        )
        results = self._finish_turn(engine)

        assert results, (
            "expected a response_ready emission — if this fails the legacy "
            "regex DID match and re-dispatched instead"
        )
        assert "<function_calls>" not in results[-1], (
            "fabricated tool-call XML leaked to the user via response_ready: "
            f"{results[-1]!r}"
        )


def _run_turn(engine, response: str, telemetry: dict | None = None) -> list[str]:
    """Drive one completed turn with an arbitrary response body.

    Same direct-call pattern as TestFabricatedToolCallLeak._finish_turn, but
    parameterised so the guard's edge cases can share it.
    """
    results = []
    engine.response_ready.connect(lambda r: results.append(r))
    engine._history = [{"role": "user", "content": "q"}]
    engine._on_worker_finished(response, telemetry if telemetry is not None else {})
    return results


class TestFabricatedToolCallFalsePositives:
    """The guard destroys a whole turn, so over-matching is as harmful as
    under-matching: the most likely question the day after this shipped is
    "why did the chat print raw XML at me?", and that answer must survive.
    """

    def _engine(self, db_path):
        from src.services.chat_engine import ChatEngine
        return ChatEngine(
            system_prompt="t", tools_enabled=True, use_mcp_tools=True, db_path=db_path,
        )

    def test_prose_mention_of_an_unterminated_tag_is_not_discarded(self, db_path):
        """`<invoke` mentioned in prose, with a `name=` far later and no `>` in
        between. An unbounded `[^>]*?` swallows the whole span and discards
        this; the regex must stay inside one tag."""
        answer = (
            "I checked the Agent log. The model typed the opening <invoke tag "
            "rather than running the call, and the name= attribute said "
            "search_google_drive. Nothing was actually searched."
        )
        results = _run_turn(self._engine(db_path), answer)
        assert results[-1] == answer, f"legitimate answer was eaten: {results[-1]!r}"

    def test_quoted_syntax_in_a_code_span_is_not_discarded(self, db_path):
        answer = (
            'Yesterday the model printed `<invoke name="search_google_drive">` '
            "as text instead of running it. That is the bug you saw."
        )
        results = _run_turn(self._engine(db_path), answer)
        assert results[-1] == answer, f"legitimate answer was eaten: {results[-1]!r}"

    def test_quoted_syntax_in_a_plain_fence_is_not_discarded(self, db_path):
        """A retrieved doc quoting this repo's own documented directive shape
        (docs/DATA_FLOWS.md) must not nuke a tool-backed answer."""
        answer = (
            "The classification stream uses this shape:\n\n"
            "```\nTOOL_CALL: tool_name {args_json}\n```\n\n"
            "It is documented in docs/DATA_FLOWS.md."
        )
        results = _run_turn(self._engine(db_path), answer)
        assert results[-1] == answer, f"legitimate answer was eaten: {results[-1]!r}"

    def test_captured_name_must_look_like_a_tool_before_it_is_shown(self, db_path):
        """The notice names the tool from a captured attribute value. If the
        capture is not identifier-shaped, use the unnamed wording rather than
        telling the user an English word is a tool."""
        from src.services.chat_engine import _UNEXECUTED_TOOL_CALL_NOTICE
        results = _run_turn(self._engine(db_path), "<invoke name='x'></invoke>")
        assert results[-1] == _UNEXECUTED_TOOL_CALL_NOTICE.format(tool=""), (
            f"non-tool-shaped capture was presented as a tool name: {results[-1]!r}"
        )


class TestFabricatedToolCallDialects:
    """The fabrication harm is dialect-independent — one hand-tuned regex for
    the Anthropic XML form leaves every other call syntax leaking."""

    TAIL = "\n\nI found 3 documents:\n1. Okta SSO Implementation Guide - 2026-07-15\n"

    def _engine(self, db_path, mcp=True):
        from src.services.chat_engine import ChatEngine
        return ChatEngine(
            system_prompt="t", tools_enabled=True, use_mcp_tools=mcp, db_path=db_path,
        )

    @pytest.mark.parametrize("payload", [
        '<tool_use><name>search_google_drive</name></tool_use>',
        '<tool_call name="search_google_drive"></tool_call>',
        "```tool_code\ndefault_api.search_google_drive(query='sso')\n```",
        '```tool_call\n{"name": "search_google_drive", "args": {}}\n```',
        '&lt;invoke name=&quot;search_google_drive&quot;&gt;',
        'TOOL_CALL: search_google_drive',
        'TOOL_CALL: search_google_drive("SSO setup")',
    ])
    def test_dialect_does_not_leak(self, db_path, payload):
        results = _run_turn(self._engine(db_path), payload + self.TAIL)
        assert "Okta SSO Implementation Guide" not in results[-1], (
            f"fabricated results leaked past the guard for {payload!r}: "
            f"{results[-1]!r}"
        )

    def test_bare_directive_also_guarded_in_legacy_mode(self, db_path):
        """A `TOOL_CALL:` with no `{json}` is not dispatched by the legacy text
        loop either, so it is just as unexecuted there."""
        results = _run_turn(
            self._engine(db_path, mcp=False), "TOOL_CALL: search_google_drive" + self.TAIL
        )
        assert "Okta SSO Implementation Guide" not in results[-1]

    def test_scan_stays_linear_on_a_pathological_response(self, db_path):
        """A long response that merely mentions unclosed openers must not
        backtrack from every one of them — this slot runs on the main thread."""
        import time
        from src.services.chat_engine import _detect_unexecuted_tool_call
        payload = "<function_calls>" * 3000 + "y " * 50000 + "<invoke " * 2000
        start = time.perf_counter()
        assert _detect_unexecuted_tool_call(payload, True) is None
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0, f"guard scan took {elapsed:.2f}s — quadratic backtracking"


class TestFabricatedToolCallRecovery:
    """What the user is told, and whether they can get unstuck."""

    BUG = TestFabricatedToolCallLeak.FABRICATION

    def _engine(self, db_path):
        from src.services.chat_engine import ChatEngine
        return ChatEngine(
            system_prompt="t", tools_enabled=True, use_mcp_tools=True, db_path=db_path,
        )

    def test_does_not_claim_nothing_ran_when_tools_actually_ran(self, db_path):
        """In MCP mode the client executes tools natively before this text
        exists. With tool_calls>0 the turn IS partly tool-backed, so the
        categorical "no tool was executed" wording would be false and the real
        results must not be thrown away."""
        results = _run_turn(
            self._engine(db_path), self.BUG,
            {"tool_calls": 2, "tool_names": ["kb_search", "search_google_drive"]},
        )
        assert "no tool was executed" not in results[-1], (
            "asserted nothing ran while telemetry reported 2 tool calls"
        )
        assert "may not be backed by real data" in results[-1], "expected a warning"
        assert "SSO Rollout Checklist" in results[-1], (
            "a tool-backed answer was destroyed instead of flagged"
        )

    def test_retry_is_not_livelocked(self, db_path):
        """The trigger is deterministic in the response, so "send it again"
        produces the same text. The second occurrence must pass through with a
        banner rather than being eaten identically forever."""
        engine = self._engine(db_path)
        results = _run_turn(engine, self.BUG, {"tool_calls": 0})
        engine._on_worker_finished(self.BUG, {"tool_calls": 0})

        assert results[0].startswith("This turn was discarded")
        assert not results[1].startswith("This turn was discarded"), (
            "second identical turn was discarded again — no escape hatch"
        )
        assert "SSO Rollout Checklist" in results[1], (
            "user still cannot see the suppressed text on retry"
        )

    def test_a_clean_turn_rearms_the_guard(self, db_path):
        engine = self._engine(db_path)
        results = _run_turn(engine, self.BUG, {"tool_calls": 0})
        engine._on_worker_finished("There are 412 open tickets.", {"tool_calls": 0})
        engine._on_worker_finished(self.BUG, {"tool_calls": 0})
        assert results[2].startswith("This turn was discarded"), (
            "suppress-once did not reset after a clean turn"
        )

    def test_fabrication_does_not_trigger_a_bridge_recycle(self, db_path):
        """Recycling the bridge cannot stop a model narrating tool calls, and
        the post-recycle turn falls through to build_client_for_task(), which
        never wires MCP config — i.e. the recycle would manufacture exactly the
        tool-less state that provokes the narration."""
        engine = self._engine(db_path)
        recycles = []
        engine.bridge_recycle_requested.connect(lambda: recycles.append(1))
        for _ in range(4):
            engine._on_worker_finished(self.BUG, {"tool_calls": 0})
        assert engine._degraded_streak == 0, (
            f"fabrication fed the degraded streak: {engine._degraded_streak}"
        )
        assert not recycles, "fabrication triggered a bridge recycle"

    def test_response_body_is_not_logged_at_warning(self, db_path, caplog):
        """Chat responses can carry ticket text and redaction runs outbound,
        not inbound. The rest of this module logs sizes, not bodies."""
        import logging
        with caplog.at_level(logging.WARNING, logger="alma.chat_engine"):
            _run_turn(self._engine(db_path), self.BUG, {"tool_calls": 0})
        warnings = "\n".join(
            r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING
        )
        assert warnings, "expected a WARNING for the discarded turn"
        assert "Okta SSO Implementation Guide" not in warnings, (
            f"response body logged at WARNING: {warnings!r}"
        )
        assert "search_google_drive" in warnings, "tool name should still be logged"


# ═══════════════════════════════════════
#  LEDGER-BACKED EVIDENCE — layer 1 rewire + layer 2 shadow
# ═══════════════════════════════════════

_LEDGER_SQL = (
    _PROJECT_ROOT / "migrations" / "009_chat_data_layer.sql"
).read_text(encoding="utf-8")


@pytest.fixture
def ledger_db(tmp_path):
    """A DB carrying the REAL chat_tool_executions table (migration 009).

    WAL is set up front because the live warehouse is WAL and a read-only
    connection cannot switch journal mode — get_connection(readonly=True)
    would raise, which the helpers swallow into UNKNOWN.
    """
    db_file = tmp_path / "ledger.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(_MIGRATION_SQL)
    conn.executescript(_LEDGER_SQL)
    conn.commit()
    conn.close()
    return str(db_file)


def _write_tool_row(db_file, tool_name="search_google_drive", result_json="[]"):
    """Write a ledger row the way the MCP subprocess really does: a RAW
    sqlite3 connection with FK enforcement off and message_id=''. All 143 rows
    in the live warehouse have exactly this shape."""
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "INSERT INTO chat_tool_executions (execution_id, message_id, session_id,"
        " tool_name, result_json) VALUES (?, '', 'adhoc_probe', ?, ?)",
        (f"e-{time.perf_counter_ns()}", tool_name, result_json),
    )
    conn.commit()
    conn.close()


class _ClaudeShapedClient:
    """The SHAPE of the live enablement client.

    ClaudeCliClient / ClaudeCliBridge never define `_last_tool_calls` — it
    exists only in src/agents/report_bridge_client.py — so
    _ChatWorker.run's `getattr(client, "_last_tool_calls", [])` yields [] and
    telemetry["tool_calls"] is STRUCTURALLY ALWAYS 0 on this leg, no matter
    how many tools actually ran. Deliberately NOT a MagicMock: a mock would
    auto-vivify the attribute and hide the whole defect.
    """

    model = "claude-haiku-4-5"

    def __init__(self, response, db_file=None, tool_name="search_google_drive",
                 result_json="[]"):
        self._response = response
        self._db_file = db_file
        self._tool_name = tool_name
        self._result_json = result_json
        self.prompts = []

    def generate(self, prompt, system_prompt="", timeout=180):
        self.prompts.append(prompt)
        if self._db_file is not None:
            # The MCP subprocess commits its row inside dispatch_tool's
            # `finally`, i.e. before the tool result reaches the model and so
            # before this text exists. Writing here reproduces that ordering.
            _write_tool_row(self._db_file, self._tool_name, self._result_json)
        return self._response


def _drive_send(engine, client, message="find our SSO setup docs"):
    """One real end-to-end turn: send() -> worker -> _on_worker_finished."""
    results = []
    telemetry = []
    engine.response_ready.connect(lambda r: results.append(r))
    engine.set_telemetry_callback(
        lambda role, content, tel: telemetry.append(tel)
    )
    engine.set_client(client)
    engine.send(message)
    _wait_for_engine(engine)
    return results, telemetry


class TestLayer1LedgerEvidence:
    """THE REPORTED DEFECT. Layer 1's keep-vs-discard branch read
    telemetry["tool_calls"], which is structurally always 0 on the Claude leg
    (`_last_tool_calls` is never set) and drops ~70 of 86 tools on the Gemini
    leg (acp_bridge._MCP_TOOL_NAMES is a hardcoded 16-name frozenset). So
    keep_text could only ever be True on a REPEAT, and a genuinely tool-backed
    answer was discarded on first occurrence.

    Ground truth is now chat_tool_executions, which is provider-symmetric:
    both legs wire the identical `-m src.mcp.chat_mcp_server` subprocess.
    """

    BUG = TestFabricatedToolCallLeak.FABRICATION

    def _engine(self, db_file):
        from src.services.chat_engine import ChatEngine
        return ChatEngine(
            system_prompt="t", tools_enabled=True, use_mcp_tools=True,
            db_path=db_file,
        )

    def test_ledger_rows_keep_the_text_when_telemetry_reports_zero(self, ledger_db):
        """A Claude-shaped client that really did run a tool. Telemetry says 0
        tool calls; the ledger says otherwise, and the ledger wins."""
        engine = self._engine(ledger_db)
        client = _ClaudeShapedClient(self.BUG, db_file=ledger_db)
        results, telemetry = _drive_send(engine, client)

        assert telemetry[-1]["tool_calls"] == 0, (
            "precondition broken: this client must report zero tool calls, "
            "otherwise the test is not exercising the defect"
        )
        assert not results[-1].startswith("This turn was discarded"), (
            "a tool-backed turn was destroyed because telemetry structurally "
            "cannot report the Claude leg's tool calls"
        )
        assert "SSO Rollout Checklist" in results[-1], (
            "the real answer was not preserved"
        )
        assert "may not be backed by real data" in results[-1], (
            "expected the keep-text banner"
        )

    def test_no_ledger_rows_still_discards(self, ledger_db):
        """Teeth for the test above: with the ledger genuinely empty the turn
        is still discarded, so the fix is not just 'always keep'."""
        engine = self._engine(ledger_db)
        client = _ClaudeShapedClient(self.BUG)   # writes no row
        results, _ = _drive_send(engine, client)
        assert results[-1].startswith("This turn was discarded"), (
            f"unbacked fabrication was kept: {results[-1]!r}"
        )

    def test_a_concurrent_row_is_enough_evidence_to_keep(self, ledger_db):
        """The watermark is on ROWID ALONE with no session filter — 48% of live
        rows carry session_id='adhoc_probe' from the subprocess pointer-file
        fallback, so filtering would read a real tool-backed turn as empty. A
        concurrent session's row instead reads as ran: a false NEGATIVE, which
        is the correct asymmetry for a check that can destroy a turn."""
        engine = self._engine(ledger_db)
        client = _ClaudeShapedClient(self.BUG, db_file=ledger_db,
                                     tool_name="kb_search")
        results, _ = _drive_send(engine, client)
        assert not results[-1].startswith("This turn was discarded")

    def test_unknown_evidence_leaves_layer1_exactly_as_it_was(self, db_path):
        """No watermark (this drives _on_worker_finished directly, so send()
        never ran) => UNKNOWN => the old telemetry-only rule, unchanged."""
        engine = self._engine(db_path)
        assert engine._turn_evidence is None
        results = _run_turn(engine, self.BUG, {"tool_calls": 0})
        assert results[-1].startswith("This turn was discarded")

    def test_positive_telemetry_still_keeps_on_unknown_evidence(self, db_path):
        """Telemetry is honoured POSITIVE-ONLY, and that path is untouched."""
        results = _run_turn(self._engine(db_path), self.BUG, {"tool_calls": 2})
        assert "SSO Rollout Checklist" in results[-1]
        assert "no tool was executed" not in results[-1]

    def test_missing_ledger_table_is_unknown_not_empty(self, db_path):
        """db_path here has no chat_tool_executions at all. A failed read must
        degrade to UNKNOWN (old behaviour), never to 'nothing ran'."""
        engine = self._engine(db_path)
        client = _ClaudeShapedClient(self.BUG)
        results, _ = _drive_send(engine, client)
        assert engine._turn_evidence is not None
        assert engine._turn_evidence.mark is None, (
            "a snapshot succeeded against a DB with no ledger table"
        )
        assert results[-1].startswith("This turn was discarded"), (
            "unmeasurable evidence must behave exactly as before"
        )

    def test_livelock_guard_survives_the_rewire(self, ledger_db):
        engine = self._engine(ledger_db)
        client = _ClaudeShapedClient(self.BUG)
        results, _ = _drive_send(engine, client)
        assert results[-1].startswith("This turn was discarded")
        engine.set_client(client)
        engine.send("again")
        _wait_for_engine(engine)
        assert not results[-1].startswith("This turn was discarded"), (
            "second identical turn discarded again — the livelock escape "
            "hatch was lost"
        )


class TestTurnEvidenceCapture:
    """R7: a detector without its capture is the worst outcome — the corpus
    would be empty and the morning briefing WOULD flag."""

    def _engine(self, db_file, **kw):
        from src.services.chat_engine import ChatEngine
        kw.setdefault("tools_enabled", True)
        kw.setdefault("use_mcp_tools", True)
        return ChatEngine(system_prompt="SYSTEM-MARKER", db_path=db_file, **kw)

    def test_capture_takes_prompt_not_ctx(self, ledger_db):
        """`prompt` carries the injected context AND the replayed history, so
        the corpus survives the ONE-SHOT [TODAY'S PLAN] on turn 2+. A ctx-only
        capture goes blind there and would recant a TRUE briefing."""
        engine = self._engine(
            ledger_db, context_provider=lambda m, h: "[TODAY'S PLAN] CTX-MARKER",
        )
        engine._history = [
            {"role": "user", "content": "morning"},
            {"role": "assistant", "content": "HISTORY-MARKER"},
        ]
        _drive_send(engine, _ClaudeShapedClient("ok"), "recap that")

        corpus = engine._turn_evidence.corpus_text()
        assert "CTX-MARKER" in corpus
        assert "HISTORY-MARKER" in corpus, (
            "history is missing from the corpus — this is the turn-2 blindness"
        )
        assert "recap that" in corpus
        assert "SYSTEM-MARKER" in corpus

    def test_surface_gate_is_off_for_the_legacy_text_loop(self, ledger_db):
        engine = self._engine(ledger_db, use_mcp_tools=False)
        _drive_send(engine, _ClaudeShapedClient("ok"))
        assert engine._turn_evidence.surface_ok is False
        assert engine._turn_evidence.mark is None, (
            "the legacy surface must not even take a watermark"
        )

    def test_surface_gate_is_off_without_a_db_path(self):
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="t", tools_enabled=True,
                            use_mcp_tools=True, db_path=None)
        _drive_send(engine, _ClaudeShapedClient("ok"))
        assert engine._turn_evidence.surface_ok is False

    def test_set_history_disables_tier_a_write_for_the_session(self, ledger_db):
        """`[SYSTEM: operator confirmed …]` lines are never persisted to
        chat_messages, so a restored transcript cannot prove a confirmation
        happened and Tier A-write must self-disable."""
        engine = self._engine(ledger_db)
        assert engine._history_restored is False
        engine.set_history([{"role": "user", "content": "x"}])
        assert engine._history_restored is True
        _drive_send(engine, _ClaudeShapedClient("ok"))
        assert engine._turn_evidence.history_restored is True

    def test_legacy_tool_round_trip_keeps_the_original_evidence(self, db_path):
        """A follow-up round must ADD the tool result, not re-create the
        evidence — re-creating it would drop the turn's original context."""
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(
            system_prompt="t", tools_enabled=True, use_mcp_tools=False,
            db_path=db_path, context_provider=lambda m, h: "CTX-MARKER",
        )
        client = _ClaudeShapedClient(
            'TOOL_CALL: query_tickets {"trc_code": "Billing"}'
        )
        _drive_send(engine, client)
        _wait_for_engine(engine)   # the follow-up round re-launches the worker

        corpus = engine._turn_evidence.corpus_text()
        assert "CTX-MARKER" in corpus, "the turn's original context was lost"
        assert any("T001" in p for p in engine._turn_evidence.tool_payloads), (
            f"tool result was not folded in: {engine._turn_evidence.tool_payloads!r}"
        )


def _shadow(mode="telemetry", tier_c=False):
    """Patch the settings read so a test does not depend on data/settings.yaml."""
    from src.services import turn_grounding as tg
    return patch.object(
        tg, "load_options",
        lambda: tg.GroundingOptions(mode=mode, tier_c_enabled=tier_c),
    )


# A Drive fabrication with no markup at all — layer 1 cannot see it, which is
# exactly why layer 2 exists. 9 ungrounded atoms across an allowlisted subject.
_UNGROUNDED_DRIVE_ANSWER = (
    "I searched your Google Drive and found 3 documents.\n"
    "1. \"Okta SSO Implementation Guide\" in the Identity Rollout folder, "
    "last modified 2026-07-15.\n"
    "2. \"SSO Rollout Checklist 2026\" in the Enablement Archive folder, "
    "last modified 2026-06-30.\n"
    "3. \"Identity Provider Migration Notes\" in the Platform Handover "
    "folder, last modified 2026-05-02.\n"
)


class TestLayer2Shadow:
    """Layer 2 ships in SHADOW: verdicts are recorded, nothing the operator
    sees changes. Flipping to `banner` is a one-key owner call."""

    def _engine(self, db_file):
        from src.services.chat_engine import ChatEngine
        return ChatEngine(
            system_prompt="You are Renn. You can search Google Drive.",
            tools_enabled=True, use_mcp_tools=True, db_path=db_file,
        )

    def test_shadow_mode_changes_nothing_the_user_sees(self, ledger_db):
        engine = self._engine(ledger_db)
        with _shadow("telemetry"):
            results, telemetry = _drive_send(
                engine, _ClaudeShapedClient(_UNGROUNDED_DRIVE_ANSWER),
            )
        assert results[-1] == _UNGROUNDED_DRIVE_ANSWER, (
            "shadow mode mutated the response the operator sees"
        )
        verdict = telemetry[-1]["turn_grounding"]
        assert verdict["state"] == "FLAG", verdict
        assert verdict["tier"] == "B", verdict
        assert verdict["action"] == "telemetry", verdict

    def test_banner_mode_prepends_one_banner(self, ledger_db):
        engine = self._engine(ledger_db)
        with _shadow("banner"):
            results, telemetry = _drive_send(
                engine, _ClaudeShapedClient(_UNGROUNDED_DRIVE_ANSWER),
            )
        assert results[-1].endswith(_UNGROUNDED_DRIVE_ANSWER), (
            "the answer text was altered rather than prefixed"
        )
        assert results[-1].startswith("Heads up"), results[-1][:80]
        assert telemetry[-1]["turn_grounding"]["action"] == "banner"

    def test_off_mode_records_nothing(self, ledger_db):
        engine = self._engine(ledger_db)
        with _shadow("off"):
            results, telemetry = _drive_send(
                engine, _ClaudeShapedClient(_UNGROUNDED_DRIVE_ANSWER),
            )
        assert results[-1] == _UNGROUNDED_DRIVE_ANSWER
        assert "turn_grounding" not in telemetry[-1]

    def test_real_tool_rows_ground_the_answer(self, ledger_db):
        """A tool DID run and its result_json carries the filenames, so the
        same text is clean. 'A tool ran' is never a blanket pass — grounding
        binds to that tool's actual payload."""
        payload = json.dumps({
            "files": [
                {"name": "Okta SSO Implementation Guide",
                 "folder": "Identity Rollout", "modified": "2026-07-15"},
                {"name": "SSO Rollout Checklist 2026",
                 "folder": "Enablement Archive", "modified": "2026-06-30"},
                {"name": "Identity Provider Migration Notes",
                 "folder": "Platform Handover", "modified": "2026-05-02"},
            ]
        })
        engine = self._engine(ledger_db)
        with _shadow("banner"):
            results, telemetry = _drive_send(
                engine,
                _ClaudeShapedClient(_UNGROUNDED_DRIVE_ANSWER, db_file=ledger_db,
                                    result_json=payload),
            )
        assert results[-1] == _UNGROUNDED_DRIVE_ANSWER, (
            "a genuinely tool-backed answer was bannered"
        )
        assert telemetry[-1]["turn_grounding"]["state"] == "CLEAN"

    def test_one_banner_when_both_layers_fire(self, ledger_db):
        """R5: two warnings about one turn and the operator trusts neither.
        Turn 1 is discarded by layer 1; turn 2 (the deterministic retry) is
        kept with layer 1's banner, and layer 2's is suppressed with both
        reasons recorded."""
        both = TestFabricatedToolCallLeak.FABRICATION + _UNGROUNDED_DRIVE_ANSWER
        engine = self._engine(ledger_db)
        client = _ClaudeShapedClient(both)
        with _shadow("banner"):
            results, telemetry = _drive_send(engine, client)
            assert results[-1].startswith("This turn was discarded")
            assert "turn_grounding" not in telemetry[-1], (
                "layer 2 judged a turn whose text layer 1 had already replaced"
            )
            engine.set_client(client)
            engine.send("again")
            _wait_for_engine(engine)

        kept = results[-1]
        assert kept.count("Heads up") == 0, "layer 2 bannered on top of layer 1"
        assert kept.startswith("Warning:"), kept[:80]
        grounding = telemetry[-1]["turn_grounding"]
        assert telemetry[-1]["unexecuted_tool_call"] is True
        assert grounding["state"] == "FLAG", grounding
        assert grounding["banner_suppressed"] == "layer1", grounding

    def test_corpus_never_reaches_a_log_line(self, ledger_db, caplog):
        """R2: the prompt can carry PHI (the AI-Reports packer injects 8000
        chars of report text). Only counts and audit tags are recorded."""
        import logging
        engine = self._engine(ledger_db)
        with _shadow("banner"), caplog.at_level(logging.WARNING,
                                                logger="alma.chat_engine"):
            _drive_send(engine, _ClaudeShapedClient(_UNGROUNDED_DRIVE_ANSWER))
        logged = "\n".join(
            r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING
        )
        assert "Turn grounding flagged" in logged, logged
        assert "Okta SSO Implementation Guide" not in logged, logged
        assert "find our SSO setup docs" not in logged, logged


# --- the REAL morning briefing, end to end through the REAL engine ----------

_BRIEFING_TASKS = [
    {"title": "Rewrite the SSO troubleshooting card", "due_date": "2026-07-17",
     "priority": "high", "source": "asana", "status": "open",
     "assignee": "chris@cambric.ai"},
    {"title": "Verify the Payments v2 overview", "due_date": "2026-07-18",
     "priority": "normal", "source": "asana", "status": "open",
     "assignee": "chris@cambric.ai"},
    {"title": "Draft the ERA auto-matching FAQ", "due_date": "2026-07-21",
     "priority": "high", "source": "asana", "status": "open",
     "assignee": "chris@cambric.ai"},
]


def _real_greeting_block():
    """The VERBATIM output of the REAL build_greeting_block, with list_tasks
    stubbed to a fixed task set — so if the builder's shape changes, this test
    moves with it."""
    from datetime import date
    from src.data import startup_greeting as sg
    from src.data import enablement_tasks as et

    original = et.list_tasks
    try:
        et.list_tasks = lambda conn, **kw: list(_BRIEFING_TASKS)   # type: ignore
        return sg.build_greeting_block(
            None, today=date(2026, 7, 21),
            aliases={"chris@cambric.ai", "chris guffey"},
        )
    finally:
        et.list_tasks = original   # type: ignore


_BRIEFING_ANSWER = (
    "Good morning. Here's where today stands.\n\n"
    "**Overdue** — \"Rewrite the SSO troubleshooting card\" was due 2026-07-17 "
    "and is your highest priority.\n"
    "**Also overdue** — \"Verify the Payments v2 overview\" (due 2026-07-18).\n"
    "**Due today** — \"Draft the ERA auto-matching FAQ\", 2026-07-21, high "
    "priority.\n\n"
    "I'd start with the SSO troubleshooting card since it is the oldest and "
    "highest priority."
)


class _OneShotPlan:
    """agent_chat consumes the [TODAY'S PLAN] block on the first turn."""

    def __init__(self, plan):
        self._plan = plan

    def __call__(self, message, history):
        plan, self._plan = self._plan, ""
        return plan


class TestMorningBriefingEndToEnd:
    """THE NON-NEGOTIABLE CONSTRAINT. The real morning briefing produces ZERO
    tool rows and is ENTIRELY TRUE — its content comes from prompt injection,
    not tool dispatch. Renn once wrongly recanted that briefing as fabricated,
    and that incident is why this feature exists. A check that can flag it is
    worse than no check at all.
    """

    def _engine(self, db_file):
        from src.services.chat_engine import ChatEngine
        plan = _real_greeting_block()
        assert "[TODAY'S PLAN]" in plan, "the real greeting builder produced nothing"
        return ChatEngine(
            system_prompt=(
                "You are Renn. When your turn context includes a [TODAY'S PLAN] "
                "block, that is the operator's REAL task data (equivalent to a "
                "list_tasks result) — present it confidently."
            ),
            tools_enabled=True, use_mcp_tools=True, db_path=db_file,
            # ONE-SHOT, exactly like agent_chat: consumed on the first turn.
            context_provider=_OneShotPlan(plan),
        ), plan

    def test_turn_one_briefing_is_not_flagged(self, ledger_db):
        engine, _ = self._engine(ledger_db)
        with _shadow("banner"):
            results, telemetry = _drive_send(
                engine, _ClaudeShapedClient(_BRIEFING_ANSWER),
                "give me my morning briefing",
            )
        assert results[-1] == _BRIEFING_ANSWER, (
            f"THE BRIEFING WAS FLAGGED: {results[-1][:200]!r}"
        )
        verdict = telemetry[-1]["turn_grounding"]
        assert verdict["state"] != "FLAG", verdict

    def test_turn_two_recap_is_not_flagged_after_the_plan_is_consumed(self, ledger_db):
        """The [TODAY'S PLAN] block is ONE-SHOT. A ctx-only capture goes blind
        here and recants a TRUE briefing; the prompt capture does not, because
        _default_pack replays Renn's own turn-1 message."""
        engine, plan = self._engine(ledger_db)
        recap = (
            "To recap: \"Rewrite the SSO troubleshooting card\" (due "
            "2026-07-17) is still the one to start with, then \"Verify the "
            "Payments v2 overview\" and \"Draft the ERA auto-matching FAQ\"."
        )
        with _shadow("banner"):
            results, telemetry = _drive_send(
                engine, _ClaudeShapedClient(_BRIEFING_ANSWER),
                "give me my morning briefing",
            )
            assert engine._context_provider("x", []) == "", (
                "the plan block was not consumed — turn 2 is not being tested"
            )
            engine.set_client(_ClaudeShapedClient(recap))
            engine.send("recap that briefing")
            _wait_for_engine(engine)

        assert plan not in engine._turn_evidence.prompt, (
            "the one-shot plan is still in ctx; this is not the turn-2 case"
        )
        assert results[-1] == recap, f"THE RECAP WAS FLAGGED: {results[-1][:200]!r}"
        assert telemetry[-1]["turn_grounding"]["state"] != "FLAG"

    #: The shape the adversarial pass flagged end-to-end through this exact
    #: engine: bold section headers over 100%-true plan-block content, closed
    #: with an ordinary retrieval aside. Verdict was
    #: `B:cards:4of6_ungrounded`, and in banner mode BANNER_TIER_B was
    #: prepended to a true briefing — the recantation incident, reproduced.
    _HEADED_BRIEFING_ANSWER = (
        "Good morning, Chris.\n\n"
        "**Overdue Items**\n"
        "\"Rewrite the SSO troubleshooting card\" — due 2026-07-17, high.\n\n"
        "**Due Today**\n"
        "\"Draft the ERA auto-matching FAQ\" — 2026-07-21, high.\n\n"
        "**Top Priority**\n"
        "The SSO card.\n\n"
        "**Suggested First Move**\n"
        "I checked the cards you own — that's the only open one today."
    )

    def test_headed_briefing_with_a_retrieval_closer_is_not_flagged(
            self, ledger_db):
        engine, _ = self._engine(ledger_db)
        with _shadow("banner"):
            results, telemetry = _drive_send(
                engine, _ClaudeShapedClient(self._HEADED_BRIEFING_ANSWER),
                "give me my morning briefing",
            )
        assert results[-1] == self._HEADED_BRIEFING_ANSWER, (
            f"THE BRIEFING WAS FLAGGED: {results[-1][:200]!r}"
        )
        assert telemetry[-1]["turn_grounding"]["state"] != "FLAG", (
            telemetry[-1]["turn_grounding"]
        )

    def test_the_same_pipeline_does_flag_an_invented_drive_answer(self, ledger_db):
        """Teeth: identical engine, identical zero-tool turn — only the subject
        and the grounding differ."""
        engine, _ = self._engine(ledger_db)
        with _shadow("banner"):
            results, telemetry = _drive_send(
                engine, _ClaudeShapedClient(_UNGROUNDED_DRIVE_ANSWER),
                "find our SSO setup docs",
            )
        assert telemetry[-1]["turn_grounding"]["state"] == "FLAG", (
            "the end-to-end pipeline is inert — the briefing tests prove nothing"
        )
        assert results[-1].startswith("Heads up")


class TestAdversarialRegressions:
    """Every case here FAILED before the review pass. Each one is a real
    end-to-end drive of the shipped ChatEngine, not a unit probe."""

    def _engine(self, db_file, **kw):
        from src.services.chat_engine import ChatEngine
        kw.setdefault("tools_enabled", True)
        kw.setdefault("use_mcp_tools", True)
        return ChatEngine(system_prompt="You are Renn.", db_path=db_file, **kw)

    # -- fail-open: a long session must not flip a true turn to FLAG --------

    def test_a_long_session_does_not_flip_a_true_turn_to_flag(self, ledger_db):
        """THE CORPUS TAIL-CAP DEFECT. `prompt = ctx + "\\n\\n" + prompt`, so
        the injected context sits at the HEAD — and CorpusIndex kept only the
        TAIL. Past ~256 KB of corpus the [ENABLEMENT SCOPE] block was the FIRST
        thing evicted, and this byte-identical, 100%-true answer scored CLEAN at
        20 turns and `B:knowledge base:3of3_ungrounded` at 30. Only the session
        length changed."""
        from src.services import turn_grounding as tg

        scope = ("[ENABLEMENT SCOPE] 196 indexed documents, 3 pending card "
                 "drafts. KB cards: \"Unified Remittance Ledger\", \"ERA Line "
                 "Matching\", \"SSO Login Errors\". Active draft: 1180.")
        answer = ("I checked the knowledge base: the cards are \"Unified "
                  "Remittance Ledger\", \"ERA Line Matching\" and \"SSO Login "
                  "Errors\", and draft 1180 is open on your canvas.")

        verdicts = {}
        for turns in (3, 20, 30, 60):
            engine = self._engine(
                ledger_db, context_provider=lambda m, h, s=scope: s)
            engine._history = [
                {"role": "user" if i % 2 == 0 else "assistant",
                 "content": f"turn {i} " + ("filler about things. " * 250)}
                for i in range(turns)
            ]
            with _shadow("banner"):
                results, telemetry = _drive_send(
                    engine, _ClaudeShapedClient(answer), "what's my scope?")
            verdicts[turns] = telemetry[-1]["turn_grounding"]
            assert results[-1] == answer, (
                f"a TRUE zero-tool answer was bannered at {turns} turns: "
                f"{results[-1][:160]!r}"
            )
            corpus = engine._turn_evidence.corpus_text(
                "\n".join(m["content"] for m in engine._history[:-1]))
            index = tg.CorpusIndex(corpus)
            assert index.contains("[enablement scope]"), (
                f"the injected context was evicted from the corpus at {turns} "
                f"turns (truncated={index.truncated})"
            )
            if turns >= 30:
                assert index.truncated, "this leg is not exercising the cap"
                # TEETH: the old tail-only cap really did lose the head.
                assert "[ENABLEMENT SCOPE]" not in corpus[-tg.CORPUS_CAP:], (
                    "precondition broken: a tail-only cap would still have "
                    "kept the context, so this test proves nothing"
                )
        assert all(v["state"] != "FLAG" for v in verdicts.values()), verdicts
        # And the long-session turns abstain explicitly rather than guessing.
        assert verdicts[60]["state"] == "UNKNOWN", verdicts[60]
        assert verdicts[3]["state"] == "CLEAN", verdicts[3]

    # -- layer 1 / telemetry hardening --------------------------------------

    def test_non_dict_telemetry_does_not_raise(self, ledger_db):
        """`{**telemetry, ...}` now runs on EVERY turn (mode defaults to
        telemetry), not only on a fabricated one, so the widened surface has to
        be closed. _ChatWorker.finished is Signal(str, dict) and Qt coerces on
        the normal path — this drives the slot directly."""
        engine = self._engine(ledger_db)
        seen = []
        engine.response_ready.connect(seen.append)
        with _shadow("telemetry"):
            for bad in ([], "nope", 7, None):
                engine._on_worker_finished("plain answer", bad)  # type: ignore
        assert len(seen) == 4

    def test_bad_tool_calls_value_does_not_raise(self, ledger_db):
        engine = self._engine(ledger_db)
        seen = []
        engine.response_ready.connect(seen.append)
        with _shadow("telemetry"):
            engine._on_worker_finished(
                TestFabricatedToolCallLeak.FABRICATION,
                {"tool_calls": "banana"},
            )
        assert seen

    def test_clear_history_re_enables_tier_a_write(self, ledger_db):
        """agent_chat.new_session() calls clear_history(). Without the reset,
        loading ANY past chat killed Tier A-write for the whole process,
        including for brand-new chats started afterwards."""
        engine = self._engine(ledger_db)
        engine.set_history([{"role": "user", "content": "x"}])
        assert engine._history_restored is True
        engine.clear_history()
        assert engine._history_restored is False
        _drive_send(engine, _ClaudeShapedClient("ok"))
        assert engine._turn_evidence.history_restored is False

    def test_excluded_surfaces_get_no_grounding_telemetry(self, ledger_db):
        """S0 excludes the legacy text loop by design; the payload on those
        surfaces should stay exactly as it was."""
        engine = self._engine(ledger_db, use_mcp_tools=False)
        with _shadow("telemetry"):
            _, telemetry = _drive_send(engine, _ClaudeShapedClient("ok"))
        assert "turn_grounding" not in telemetry[-1], telemetry[-1]


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
