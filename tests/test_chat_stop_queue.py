"""Stop-the-run + queue-context-while-busy (Renn chat, 2026-08-05).

Covers the three layers of the feature:
  - ChatEngine.stop(): abort route via the client's abort_active, surfacing as
    run_stopped (not error_occurred), with graceful degradation for idle
    engines and abort-less clients (the Gemini ReportBridgeClient).
  - AgentChatController.queue_user_message / stop_run: the busy-queue reused
    from enqueue_trigger, with the DEFERRED drain (queued dispatch) and its
    still-busy re-check.
  - ChatBridge stopRun / queueMessage slots: thin, exception-safe relays plus
    the runStopped signal re-emit.

Review-wave regressions (2026-08-05):
  - FIX 1: a session switch (load_session / new_session) DROPS the busy-queue —
    a queued message referred to the old conversation and must never replay
    into the newly-active session.
  - FIX 2a: killing the CLI proc walks the WHOLE tree on Windows (taskkill /T)
    so the MCP-server child dies with it; one choke point (kill_process_tree).
  - FIX 2b: one delayed catch-up poll after busy(False) so a tool row that
    lands after the final polls is not swallowed forever by the next
    busy(True) cursor re-baseline.
  - FIX 2c: run_stopped resyncs the enablement page (panel line + the same
    refresh the response path runs) and persists the stop marker on the Agent
    surface (one row per stopped turn).
  - FIX 3: stop() returns False AND clears _stop_requested when abort_active
    raises or reports nothing killed — a later worker error is then a REAL
    error (error_occurred + degraded streak), never a fake user stop.
  - FIX 4: the drained text is announced (queued_dispatched / the bridge's
    queuedDispatched) so JS un-badges exactly the bubble that ran, never
    inferred from busyChanged(True).
  - FIX 5: a drawer message queued during a publish-confirm modal is KEPT
    (peek-then-pop drain + one armed ~1s retry), not silently dropped.
"""

import json
import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QObject, Signal

# Ensure an application exists for signal delivery + zero-timers. A FULL
# QApplication (same rationale as test_chat_engine): widget tests running
# later in the same pytest process crash if the singleton is core-only.
try:
    from PySide6.QtWidgets import QApplication as _AppClass
except Exception:  # noqa: BLE001 — headless build without QtWidgets
    _AppClass = QCoreApplication
_app = _AppClass.instance() or _AppClass([])

STOP_MARKER = "[Response stopped by the user before completion.]"


def _pump(seconds=0.2):
    """Process events (incl. QTimer.singleShot(0) callbacks) for a while."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        _app.processEvents()
        time.sleep(0.01)


def _wait_for_engine(engine, timeout=10.0):
    """Wait for the engine worker to finish and process pending signals."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if engine._worker is not None:
            engine._worker.wait(100)
        _app.processEvents()
        if not engine.is_busy:
            _app.processEvents()  # process final signals
            return
    raise TimeoutError("Engine worker did not finish")


def _wait_until_busy(engine, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if engine.is_busy:
            return
        _app.processEvents()
        time.sleep(0.01)
    raise TimeoutError("Engine never became busy")


class _AbortableClient:
    """Blocks in generate() until abort_active fires, then raises — the shape
    of a ClaudeCliClient whose subprocess was killed mid-call. abort_active
    reports True (matched + killed), per the FIX-3 bool contract."""

    model = "claude-cli"

    def __init__(self):
        self._killed = threading.Event()
        self.abort_calls = 0

    def generate(self, prompt, system_prompt="", timeout=180):
        if not self._killed.wait(timeout=10):
            return "completed normally"
        raise RuntimeError(
            "Claude CLI call failed: claude_cli_nonzero_exit - killed")

    def abort_active(self):
        self.abort_calls += 1
        self._killed.set()
        return True


class _FalseAbortClient:
    """abort_active reports False — the bridge matched nothing (the call
    already finished, or the id was lost); the run itself keeps going."""

    model = "claude-cli"

    def __init__(self):
        self.release = threading.Event()
        self.abort_calls = 0

    def generate(self, prompt, system_prompt="", timeout=180):
        self.release.wait(timeout=10)
        return "full answer"

    def abort_active(self):
        self.abort_calls += 1
        return False


class _RaisingAbortClient:
    """abort_active raises (the proc is already gone); the run later dies on
    its own — which must then surface as a REAL error, not a user stop."""

    model = "claude-cli"

    def __init__(self):
        self.release = threading.Event()

    def generate(self, prompt, system_prompt="", timeout=180):
        if not self.release.wait(timeout=10):
            return "never released"
        raise RuntimeError("bridge crashed")

    def abort_active(self):
        raise RuntimeError("proc already gone")


class _NoAbortClient:
    """A client with NO abort_active (the Gemini ReportBridgeClient shape).
    Deliberately not a MagicMock — a mock would auto-vivify abort_active."""

    model = "gemini-bridge"

    def __init__(self):
        self.release = threading.Event()

    def generate(self, prompt, system_prompt="", timeout=180):
        self.release.wait(timeout=10)
        return "full answer"


class _GatedClient:
    """Returns instantly once released; blocks until then."""

    model = "claude-cli"

    def __init__(self):
        self.release = threading.Event()
        self.calls = []

    def generate(self, prompt, system_prompt="", timeout=180):
        self.calls.append(prompt)
        if not self.release.wait(timeout=10):
            raise RuntimeError("test client never released")
        return "done"


def _engine_signals(engine):
    seen = {"stopped": [], "errors": [], "busy": [], "responses": []}
    engine.run_stopped.connect(lambda: seen["stopped"].append(1))
    engine.error_occurred.connect(seen["errors"].append)
    engine.busy_changed.connect(seen["busy"].append)
    engine.response_ready.connect(seen["responses"].append)
    return seen


# ═══════════════════════════════════════
#  ChatEngine.stop()
# ═══════════════════════════════════════

class TestChatEngineStop:

    def test_stop_kills_the_run_and_emits_run_stopped(self):
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="t")
        client = _AbortableClient()
        engine.set_client(client)
        seen = _engine_signals(engine)

        engine.send("kill this")
        _wait_until_busy(engine)
        streak_before = engine._degraded_streak

        assert engine.stop() is True
        assert client.abort_calls == 1
        _wait_for_engine(engine)

        assert seen["stopped"] == [1], "run_stopped did not fire"
        assert seen["errors"] == [], "the stop surfaced as an error"
        assert seen["responses"] == []
        assert seen["busy"] == [True, False]
        assert engine._degraded_streak == streak_before, (
            "a user stop fed the degraded streak"
        )
        assert engine._stop_requested is False
        assert engine.history[-1] == {
            "role": "assistant", "content": STOP_MARKER,
        }, "the stop marker is missing from history"

    def test_stop_when_idle_is_a_refused_noop(self):
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="t")
        engine.set_client(_AbortableClient())
        seen = _engine_signals(engine)

        assert engine.stop() is False
        assert seen["stopped"] == []
        assert seen["busy"] == []
        assert engine._stop_requested is False

    def test_stop_with_abortless_client_lets_the_run_complete(self):
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="t")
        client = _NoAbortClient()
        engine.set_client(client)
        seen = _engine_signals(engine)

        engine.send("hello")
        _wait_until_busy(engine)
        assert engine.stop() is False, (
            "stop claimed success against a client with no abort_active"
        )
        assert engine._stop_requested is False
        client.release.set()
        _wait_for_engine(engine)

        assert seen["responses"] == ["full answer"], "the run did not complete"
        assert seen["stopped"] == []
        assert seen["errors"] == []

    def test_completion_that_raced_the_kill_is_a_normal_turn(self):
        """The run finished before the kill landed: _on_worker_finished must
        clear the flag so nothing later misreads a plain error as a stop."""
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="t")
        seen = _engine_signals(engine)
        engine._history = [{"role": "user", "content": "q"}]
        engine._stop_requested = True

        engine._on_worker_finished("made it", {})

        assert engine._stop_requested is False
        assert seen["responses"] == ["made it"]
        assert seen["stopped"] == []

    def test_a_real_error_after_a_cleared_stop_still_errors(self):
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="t")
        seen = _engine_signals(engine)
        streak_before = engine._degraded_streak

        engine._on_worker_error("bridge crashed")

        assert seen["errors"] == ["bridge crashed"]
        assert seen["stopped"] == []
        assert engine._degraded_streak == streak_before + 1

    # ── FIX 3: stop() must not claim success it cannot prove ──────────

    def test_stop_false_when_abort_reports_nothing_killed(self):
        """abort_active() -> False means NOTHING was aborted: stop() must
        return False and clear _stop_requested so the run completes as a
        normal turn (not a fake user stop)."""
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="t")
        client = _FalseAbortClient()
        engine.set_client(client)
        seen = _engine_signals(engine)

        engine.send("hello")
        _wait_until_busy(engine)
        assert engine.stop() is False, (
            "stop claimed success when abort_active matched nothing"
        )
        assert client.abort_calls == 1
        assert engine._stop_requested is False, (
            "_stop_requested survived a refused abort"
        )
        client.release.set()
        _wait_for_engine(engine)

        assert seen["responses"] == ["full answer"], "the run did not complete"
        assert seen["stopped"] == []
        assert seen["errors"] == []

    def test_stop_false_when_abort_raises_and_a_later_error_is_real(self):
        """abort_active() raising is a refused stop: False, flag cleared —
        and when the worker later errors on its own, that surfaces as
        error_occurred + a degraded-streak increment, NOT run_stopped."""
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="t")
        client = _RaisingAbortClient()
        engine.set_client(client)
        seen = _engine_signals(engine)

        engine.send("hello")
        _wait_until_busy(engine)
        streak_before = engine._degraded_streak
        assert engine.stop() is False, "stop claimed success when abort raised"
        assert engine._stop_requested is False

        client.release.set()          # the run now dies for its own reasons
        _wait_for_engine(engine)

        assert seen["errors"] == ["bridge crashed"], (
            "the real worker error was not surfaced"
        )
        assert seen["stopped"] == [], "a refused stop still ate the error"
        assert engine._degraded_streak == streak_before + 1, (
            "the real error did not feed the degraded streak"
        )


# ═══════════════════════════════════════
#  ClaudeCliClient.abort_active
# ═══════════════════════════════════════

def _bare_cli_client():
    from src.llm.claude_cli_client import ClaudeCliClient
    client = ClaudeCliClient.__new__(ClaudeCliClient)
    client.model = "sonnet"
    client.pii_redaction = False
    client._bridge = None
    client._call_counter = 0
    client._mcp_config = []
    client._active_request_id = None
    return client


class TestClaudeCliClientAbort:

    def test_generate_scopes_the_active_request_id(self, monkeypatch):
        client = _bare_cli_client()
        seen = {}

        class _Bridge:
            def set_system_prompt(self, s):
                pass

            def call_blocking(self, prompt, request_id, timeout=300):
                seen["during"] = client._active_request_id
                seen["request_id"] = request_id
                return "ok"

        bridge = _Bridge()
        monkeypatch.setattr(client, "_ensure_bridge", lambda: bridge)
        monkeypatch.setattr(client, "_prepare_prompt", lambda p, s: (p, s))
        assert client.generate("hi") == "ok"
        assert seen["during"] == seen["request_id"], (
            "the request id was not recorded before the bridge call"
        )
        assert client._active_request_id is None, (
            "the request id was not cleared in the finally"
        )

    def test_request_id_clears_even_when_the_call_raises(self, monkeypatch):
        client = _bare_cli_client()

        class _Bridge:
            def set_system_prompt(self, s):
                pass

            def call_blocking(self, prompt, request_id, timeout=300):
                raise RuntimeError("killed")

        monkeypatch.setattr(client, "_ensure_bridge", lambda: _Bridge())
        monkeypatch.setattr(client, "_prepare_prompt", lambda p, s: (p, s))
        with pytest.raises(RuntimeError):
            client.generate("hi")
        assert client._active_request_id is None

    def test_abort_active_forwards_the_recorded_id_and_reports_the_kill(self):
        client = _bare_cli_client()
        aborted = []

        class _Bridge:
            def abort(self, request_id):
                aborted.append(request_id)
                return True

        client._bridge = _Bridge()
        client._active_request_id = "r9"
        assert client.abort_active() is True
        assert aborted == ["r9"]

    def test_abort_active_reports_false_when_the_bridge_matched_nothing(self):
        client = _bare_cli_client()

        class _Bridge:
            def abort(self, request_id):
                return False

        client._bridge = _Bridge()
        client._active_request_id = "r9"
        assert client.abort_active() is False

    def test_abort_active_with_nothing_in_flight_is_a_refused_noop(self):
        client = _bare_cli_client()
        aborted = []

        class _Bridge:
            def abort(self, request_id):
                aborted.append(request_id)
                return True

        client._bridge = _Bridge()
        client._active_request_id = None
        assert client.abort_active() is False    # no id
        client._bridge = None
        client._active_request_id = "r1"
        assert client.abort_active() is False    # no bridge
        assert aborted == []

    def test_abort_active_swallows_bridge_errors_and_reports_false(self):
        client = _bare_cli_client()

        class _Bridge:
            def abort(self, request_id):
                raise RuntimeError("proc gone")

        client._bridge = _Bridge()
        client._active_request_id = "r1"
        assert client.abort_active() is False    # must not raise either


# ═══════════════════════════════════════
#  Controller: queue_user_message + deferred drain + stop_run
# ═══════════════════════════════════════

@pytest.fixture
def harness(empty_db, monkeypatch):
    """A stripped AgentChatController (test_drive_folder_picker precedent)
    over a REAL ChatEngine + gated client, with real message persistence into
    the fixture DB."""
    from src.data.connection_factory import get_connection
    from src.services.agent_chat import AgentChatController
    from src.services.chat_engine import ChatEngine
    from src.services.chat_session import create_session

    sid = create_session("agent", conn=empty_db.conn)

    client = _GatedClient()
    engine = ChatEngine(system_prompt="t")
    engine.set_client(client)

    ctrl = AgentChatController.__new__(AgentChatController)
    QObject.__init__(ctrl)
    ctrl._engine = engine
    ctrl._session_id = sid
    ctrl._pending_triggers = []
    db_path = str(empty_db.db_path)
    monkeypatch.setattr(
        ctrl, "_open_conn",
        lambda readonly=False: get_connection(db_path, readonly=readonly))
    monkeypatch.setattr(ctrl, "_prepare_provider", lambda: None)
    monkeypatch.setattr(ctrl, "_write_session_pointer", lambda sid: None)
    engine.busy_changed.connect(ctrl._on_busy_changed)

    yield ctrl, engine, client, sid, empty_db.conn

    client.release.set()
    try:
        _wait_for_engine(engine)
    except TimeoutError:
        pass


def _user_rows(conn, sid):
    return [r[0] for r in conn.execute(
        "SELECT content FROM chat_messages WHERE session_id=? AND role='user' "
        "ORDER BY ordinal", (sid,)).fetchall()]


class TestQueueUserMessage:

    def test_idle_send_reports_sent_and_persists_once(self, harness):
        ctrl, engine, client, sid, conn = harness
        assert ctrl.queue_user_message("hello") == "sent"
        client.release.set()
        _wait_for_engine(engine)
        assert _user_rows(conn, sid) == ["hello"]

    def test_busy_send_queues_then_drains_on_idle(self, harness):
        ctrl, engine, client, sid, conn = harness
        assert ctrl.queue_user_message("one") == "sent"
        _wait_until_busy(engine)

        assert ctrl.queue_user_message("two") == "queued"
        assert ctrl._pending_triggers == ["two"]
        assert _user_rows(conn, sid) == ["one"], (
            "a queued message was persisted before its turn started"
        )

        client.release.set()
        deadline = time.time() + 10
        while time.time() < deadline:
            _pump(0.05)
            if len(client.calls) >= 2 and not engine.is_busy:
                break
        _wait_for_engine(engine)

        assert len(client.calls) == 2, "the queued message never ran"
        assert ctrl._pending_triggers == []
        rows = _user_rows(conn, sid)
        assert rows == ["one", "two"]
        assert rows.count("two") == 1, "double-persisted the queued text"

    def test_stale_idle_dispatch_requeues_at_head(self, harness):
        """busy_changed(False) delivered while the engine is ALREADY busy
        again (the drain re-check): the trigger stays at the head, nothing is
        sent, nothing is lost, nothing double-persists."""
        ctrl, engine, client, sid, conn = harness
        ctrl.queue_user_message("one")
        _wait_until_busy(engine)
        assert ctrl.queue_user_message("two") == "queued"

        ctrl._on_busy_changed(False)   # stale — the engine is still busy
        _pump(0.3)                     # let the deferred drain run

        assert ctrl._pending_triggers == ["two"], "the trigger was lost"
        assert len(client.calls) == 1, "the drain sent into a busy engine"
        assert _user_rows(conn, sid) == ["one"]

        client.release.set()
        deadline = time.time() + 10
        while time.time() < deadline:
            _pump(0.05)
            if len(client.calls) >= 2 and not engine.is_busy:
                break
        _wait_for_engine(engine)

        assert len(client.calls) == 2
        assert ctrl._pending_triggers == []
        assert _user_rows(conn, sid) == ["one", "two"]

    def test_engine_none_reports_error(self, harness):
        ctrl, engine, client, sid, conn = harness
        ctrl._engine = None
        assert ctrl.queue_user_message("x") == "error"

    def test_stop_run_delegates_to_the_engine(self, harness):
        ctrl, engine, client, sid, conn = harness
        assert ctrl.stop_run() is False   # idle
        abortable = _AbortableClient()
        engine.set_client(abortable)
        ctrl.queue_user_message("kill this")
        _wait_until_busy(engine)
        assert ctrl.stop_run() is True
        assert abortable.abort_calls == 1
        _wait_for_engine(engine)

    def test_stop_run_with_no_engine_is_false(self, harness):
        ctrl, engine, client, sid, conn = harness
        ctrl._engine = None
        assert ctrl.stop_run() is False


# ═══════════════════════════════════════
#  FIX 1: a session switch drops the busy-queue
# ═══════════════════════════════════════

class TestSessionSwitchDropsQueue:

    def test_queued_message_does_not_follow_a_new_session(self, harness):
        """Probe-style repro: queue during busy, start a NEW session, release.
        The queued text referred to the OLD conversation — it must be dropped,
        not replayed into the fresh thread."""
        ctrl, engine, client, sid, conn = harness
        ctrl.queue_user_message("one")
        _wait_until_busy(engine)
        assert ctrl.queue_user_message("for the old thread") == "queued"

        ctrl.new_session()
        assert ctrl._pending_triggers == [], (
            "new_session left the busy-queue armed"
        )

        client.release.set()
        _wait_for_engine(engine)
        _pump(0.3)   # let any (wrongly surviving) deferred drain run

        assert len(client.calls) == 1, (
            "a queued message replayed after the session switch"
        )
        assert _user_rows(conn, sid) == ["one"]

    def test_queued_message_does_not_follow_a_load_session(self, harness):
        """Probe-style repro: queue during busy, LOAD another session, release.
        The other session's transcript must show zero contamination and the
        queue must be empty."""
        from src.services.chat_session import create_session
        ctrl, engine, client, sid, conn = harness
        other = create_session("enablement", conn=conn)

        ctrl.queue_user_message("one")
        _wait_until_busy(engine)
        assert ctrl.queue_user_message("stale context") == "queued"

        data = ctrl.load_session(other)
        assert data["session_id"] == other
        assert ctrl._session_id == other
        assert ctrl._pending_triggers == [], (
            "load_session left the busy-queue armed"
        )

        client.release.set()
        _wait_for_engine(engine)
        _pump(0.3)

        assert len(client.calls) == 1, (
            "a queued message replayed after the session switch"
        )
        assert _user_rows(conn, other) == [], (
            "the freshly-loaded session was contaminated by the old queue"
        )

    def test_a_guarded_load_keeps_the_queue(self, harness):
        """The decoupling guard (non-enablement session) does NOT switch the
        active session — so it must not drop the queue either."""
        from src.services.chat_session import create_session
        ctrl, engine, client, sid, conn = harness
        product = create_session("trc_analytics", conn=conn)

        ctrl.queue_user_message("one")
        _wait_until_busy(engine)
        assert ctrl.queue_user_message("two") == "queued"

        ctrl.load_session(product)          # refused: not an enablement chat
        assert ctrl._session_id == sid, "the guard let a product chat in"
        assert ctrl._pending_triggers == ["two"], (
            "a refused load still dropped the queue"
        )

        client.release.set()
        deadline = time.time() + 10
        while time.time() < deadline:
            _pump(0.05)
            if len(client.calls) >= 2 and not engine.is_busy:
                break
        _wait_for_engine(engine)
        assert len(client.calls) == 2
        assert _user_rows(conn, sid) == ["one", "two"]


# ═══════════════════════════════════════
#  FIX 4: the drain announces the dispatched text
# ═══════════════════════════════════════

class TestQueuedDispatchedAnnouncement:

    def test_drain_announces_the_dispatched_text(self, harness):
        ctrl, engine, client, sid, conn = harness
        seen = []
        ctrl.queued_dispatched.connect(seen.append)

        ctrl.queue_user_message("one")
        _wait_until_busy(engine)
        assert ctrl.queue_user_message("two") == "queued"
        assert seen == [], "announced before the drain actually dispatched"

        client.release.set()
        deadline = time.time() + 10
        while time.time() < deadline:
            _pump(0.05)
            if len(client.calls) >= 2 and not engine.is_busy:
                break
        _wait_for_engine(engine)

        assert len(client.calls) == 2
        assert seen == ["two"], "the drained text was not announced"

    def test_an_immediate_send_is_not_announced(self, harness):
        ctrl, engine, client, sid, conn = harness
        seen = []
        ctrl.queued_dispatched.connect(seen.append)
        assert ctrl.queue_user_message("now") == "sent"
        client.release.set()
        _wait_for_engine(engine)
        assert seen == [], "an idle send is not a queued dispatch"


# ═══════════════════════════════════════
#  FIX 2c: run_stopped persists the marker on the Agent surface
# ═══════════════════════════════════════

class TestAgentStopPersistence:

    def test_stopped_turn_persists_exactly_one_marker_row(self, harness):
        ctrl, engine, client, sid, conn = harness
        engine.run_stopped.connect(ctrl._on_run_stopped)
        abortable = _AbortableClient()
        engine.set_client(abortable)

        ctrl.queue_user_message("kill this")
        _wait_until_busy(engine)
        assert ctrl.stop_run() is True
        _wait_for_engine(engine)

        rows = [r[0] for r in conn.execute(
            "SELECT content FROM chat_messages WHERE session_id=? AND "
            "role='assistant' ORDER BY ordinal", (sid,)).fetchall()]
        assert rows == [STOP_MARKER], (
            "expected exactly one persisted stop-marker row, got %r" % rows
        )
        # …and it matches what the engine stamped into in-memory history, so a
        # reloaded transcript replays coherently.
        assert engine.history[-1]["content"] == STOP_MARKER

    def test_setup_engine_wires_run_stopped(self):
        import inspect
        from src.services.agent_chat import AgentChatController
        src = inspect.getsource(AgentChatController._setup_engine)
        assert "run_stopped" in src and "_on_run_stopped" in src, (
            "the Agent controller does not persist stopped turns"
        )

    def test_marker_constant_matches_the_engine_history_marker(self):
        from src.services.chat_engine import RUN_STOPPED_MARKER
        assert RUN_STOPPED_MARKER == STOP_MARKER


# ═══════════════════════════════════════
#  ChatBridge: stopRun / queueMessage / runStopped
# ═══════════════════════════════════════

class _FakeEngine(QObject):
    response_ready = Signal(str)
    error_occurred = Signal(str)
    busy_changed = Signal(bool)
    status_update = Signal(str)
    run_stopped = Signal()

    def __init__(self):
        super().__init__()
        self.sent = []

    def send(self, text):
        self.sent.append(text)


class _LegacyEngine(QObject):
    """No run_stopped signal — the hasattr guard must tolerate it."""
    response_ready = Signal(str)
    error_occurred = Signal(str)
    busy_changed = Signal(bool)
    status_update = Signal(str)

    def send(self, text):
        pass


class TestChatBridgeStopQueueSlots:

    def test_stop_run_relays_the_injected_fn(self):
        from src.ui.web.chat_bridge import ChatBridge
        bridge = ChatBridge(_FakeEngine(), stop_fn=lambda: True)
        assert bridge.stopRun() is True
        bridge = ChatBridge(_FakeEngine(), stop_fn=lambda: False)
        assert bridge.stopRun() is False

    def test_stop_run_without_fn_is_false(self):
        from src.ui.web.chat_bridge import ChatBridge
        bridge = ChatBridge(_FakeEngine())
        assert bridge.stopRun() is False

    def test_stop_run_swallows_exceptions(self):
        from src.ui.web.chat_bridge import ChatBridge

        def boom():
            raise RuntimeError("no")

        bridge = ChatBridge(_FakeEngine(), stop_fn=boom)
        assert bridge.stopRun() is False

    def test_queue_message_relays_the_injected_fn(self):
        from src.ui.web.chat_bridge import ChatBridge
        calls = []

        def queue(text):
            calls.append(text)
            return "queued"

        bridge = ChatBridge(_FakeEngine(), queue_fn=queue)
        assert bridge.queueMessage("more context") == "queued"
        assert calls == ["more context"]

    def test_queue_message_without_fn_degrades_to_send(self):
        from src.ui.web.chat_bridge import ChatBridge
        engine = _FakeEngine()
        bridge = ChatBridge(engine)
        assert bridge.queueMessage("hi") == "sent"
        assert engine.sent == ["hi"]

    def test_queue_message_swallows_exceptions(self):
        from src.ui.web.chat_bridge import ChatBridge

        def boom(text):
            raise RuntimeError("no")

        bridge = ChatBridge(_FakeEngine(), queue_fn=boom)
        assert bridge.queueMessage("hi") == "error"

    def test_run_stopped_is_re_emitted(self):
        from src.ui.web.chat_bridge import ChatBridge
        engine = _FakeEngine()
        bridge = ChatBridge(engine)
        seen = []
        bridge.runStopped.connect(lambda: seen.append(1))
        engine.run_stopped.emit()
        assert seen == [1]

    def test_engine_without_run_stopped_still_builds(self):
        from src.ui.web.chat_bridge import ChatBridge
        bridge = ChatBridge(_LegacyEngine())
        assert bridge.stopRun() is False


# ═══════════════════════════════════════
#  FIX 2a: kill the WHOLE subprocess tree (Windows taskkill /T)
# ═══════════════════════════════════════

class _FakeProc:
    """A Popen stand-in. NEVER hand a real PID to these tests — the Windows
    path shells out to taskkill, which is monkeypatched here."""

    def __init__(self, alive=True, pid=424242):
        self.pid = pid
        self._alive = alive
        self.killed = 0
        self.waited = []

    def poll(self):
        return None if self._alive else 0

    def kill(self):
        self.killed += 1
        self._alive = False

    def wait(self, timeout=None):
        self.waited.append(timeout)
        self._alive = False
        return 0


class TestKillProcessTree:

    def test_windows_kill_walks_the_tree_via_taskkill(self, monkeypatch):
        import src.agents.claude_cli_subprocess as sub
        calls = []

        class _Done:
            returncode = 0

        monkeypatch.setattr(sub.sys, "platform", "win32")
        monkeypatch.setattr(
            sub.subprocess, "run",
            lambda cmd, **kw: (calls.append((cmd, kw)), _Done())[1])

        proc = _FakeProc()
        sub.kill_process_tree(proc)

        assert calls, "taskkill was never invoked on Windows"
        cmd, kw = calls[0]
        assert cmd == ["taskkill", "/PID", "424242", "/T", "/F"]
        assert kw.get("capture_output") is True
        assert kw.get("timeout") == 10
        assert proc.killed == 0, "taskkill succeeded but bare kill ran too"
        assert proc.waited == [5], "the proc was not reaped after the kill"

    def test_windows_kill_falls_back_when_taskkill_raises(self, monkeypatch):
        import src.agents.claude_cli_subprocess as sub
        monkeypatch.setattr(sub.sys, "platform", "win32")

        def _boom(cmd, **kw):
            raise OSError("taskkill missing")

        monkeypatch.setattr(sub.subprocess, "run", _boom)
        proc = _FakeProc()
        sub.kill_process_tree(proc)
        assert proc.killed == 1, "no fallback kill after a taskkill failure"

    def test_windows_kill_falls_back_on_nonzero_exit(self, monkeypatch):
        import src.agents.claude_cli_subprocess as sub
        monkeypatch.setattr(sub.sys, "platform", "win32")

        class _Failed:
            returncode = 128

        monkeypatch.setattr(sub.subprocess, "run", lambda cmd, **kw: _Failed())
        proc = _FakeProc()
        sub.kill_process_tree(proc)
        assert proc.killed == 1, "no fallback kill after taskkill exit 128"

    def test_posix_path_is_the_plain_kill(self, monkeypatch):
        import src.agents.claude_cli_subprocess as sub
        monkeypatch.setattr(sub.sys, "platform", "darwin")

        def _never(cmd, **kw):
            raise AssertionError("taskkill invoked on POSIX")

        monkeypatch.setattr(sub.subprocess, "run", _never)
        proc = _FakeProc()
        sub.kill_process_tree(proc)
        assert proc.killed == 1
        assert proc.waited == [5]

    def test_dead_proc_and_none_are_noops(self, monkeypatch):
        import src.agents.claude_cli_subprocess as sub

        def _never(cmd, **kw):
            raise AssertionError("taskkill invoked for a dead proc")

        monkeypatch.setattr(sub.subprocess, "run", _never)
        proc = _FakeProc(alive=False)
        sub.kill_process_tree(proc)
        sub.kill_process_tree(None)
        assert proc.killed == 0 and proc.waited == []

    def test_cli_subprocess_kill_routes_through_the_tree_kill(self, monkeypatch):
        import src.agents.claude_cli_subprocess as sub
        seen = []
        monkeypatch.setattr(sub, "kill_process_tree", seen.append)
        cs = sub.CliSubprocess(["claude"], {}, "prompt")
        proc = _FakeProc()
        cs.proc = proc
        cs.kill()
        assert seen == [proc], (
            "CliSubprocess.kill (timeout/early_stop path) bypassed the tree kill"
        )

    def test_bridge_kill_proc_routes_through_the_tree_kill(self, monkeypatch):
        import src.agents.claude_cli_bridge as bmod
        seen = []
        monkeypatch.setattr(bmod, "kill_process_tree", seen.append)
        bridge = bmod.ClaudeCliBridge()
        proc = _FakeProc()
        bridge._kill_proc(proc)
        assert seen == [proc], (
            "the bridge's abort/shutdown choke point bypassed the tree kill"
        )


# ═══════════════════════════════════════
#  FIX 3 (bridge leg): abort reports whether it matched + killed
# ═══════════════════════════════════════

class TestBridgeAbortReportsBool:

    def test_abort_is_true_only_when_the_id_matches(self, monkeypatch):
        import src.agents.claude_cli_bridge as bmod
        killed = []
        monkeypatch.setattr(bmod, "kill_process_tree", killed.append)
        bridge = bmod.ClaudeCliBridge()

        assert bridge.abort("r1") is False          # nothing in flight
        proc = _FakeProc()
        bridge._track_active(proc, "r1")
        assert bridge.abort("other") is False       # wrong id
        assert killed == []
        assert bridge.abort("r1") is True           # matched + killed
        assert killed == [proc]
        bridge._untrack_active(proc)
        assert bridge.abort("r1") is False          # already untracked


# ═══════════════════════════════════════
#  FIX 2b: the delayed catch-up poll after busy(False)
# ═══════════════════════════════════════

class TestLateToolRowCatchup:

    def _bridge_with_store(self):
        from src.ui.web.chat_bridge import ChatBridge
        store = []

        def tool_poll(since_id):
            return [r for r in store if r["id"] > int(since_id)]

        engine = _FakeEngine()
        bridge = ChatBridge(engine, tool_poll=tool_poll)
        got = []
        bridge.toolCall.connect(lambda j: got.append(json.loads(j)))
        return engine, bridge, store, got

    def test_a_row_landing_after_the_final_polls_is_caught_up(self):
        """Repro: busy True→False with the tool row committing AFTER the final
        busy(False) polls. Without the delayed catch-up the next busy(True)
        re-baselines the cursor past it and it never renders."""
        engine, bridge, store, got = self._bridge_with_store()
        engine.busy_changed.emit(True)     # baselines the cursor at 0
        engine.busy_changed.emit(False)    # final polls see nothing yet
        assert got == []
        store.append({"id": 1, "name": "late_tool", "ok": True})
        _pump(1.6)                         # the ~1200 ms catch-up fires
        assert [r["name"] for r in got] == ["late_tool"], (
            "the late tool row was swallowed"
        )

    def test_catchup_skips_when_a_new_turn_already_started(self):
        engine, bridge, store, got = self._bridge_with_store()
        engine.busy_changed.emit(True)
        engine.busy_changed.emit(False)
        store.append({"id": 1, "name": "late_tool", "ok": True})
        engine.is_busy = True              # a new turn owns the cursor now
        bridge._late_catchup()
        assert got == [], (
            "the catch-up polled into a running turn (accepted residual window)"
        )

    def test_catchup_polls_directly_when_idle(self):
        engine, bridge, store, got = self._bridge_with_store()
        engine.busy_changed.emit(True)
        engine.busy_changed.emit(False)
        store.append({"id": 1, "name": "late_tool", "ok": True})
        bridge._late_catchup()
        assert [r["name"] for r in got] == ["late_tool"]


# ═══════════════════════════════════════
#  FIX 4 (bridge leg): queuedDispatched relay
# ═══════════════════════════════════════

class _QueueSignalApi(QObject):
    """The controller shape: injected as session_api, carrying the
    queued_dispatched signal the bridge re-emits."""
    queued_dispatched = Signal(str)


class TestChatBridgeQueuedDispatched:

    def test_controller_signal_re_emits_as_queued_dispatched(self):
        from src.ui.web.chat_bridge import ChatBridge
        api = _QueueSignalApi()
        bridge = ChatBridge(_FakeEngine(), session_api=api)
        got = []
        bridge.queuedDispatched.connect(got.append)
        api.queued_dispatched.emit("parked text")
        assert got == ["parked text"]

    def test_notify_queued_dispatched_emits_directly(self):
        from src.ui.web.chat_bridge import ChatBridge
        bridge = ChatBridge(_FakeEngine())
        got = []
        bridge.queuedDispatched.connect(got.append)
        bridge.notify_queued_dispatched("drawer text")
        assert got == ["drawer text"]

    def test_session_api_without_the_signal_still_builds(self):
        from src.ui.web.chat_bridge import ChatBridge

        class _PlainApi:
            def list_sessions(self):
                return []

        bridge = ChatBridge(_FakeEngine(), session_api=_PlainApi())
        assert bridge.stopRun() is False   # alive and functional


# ═══════════════════════════════════════
#  FIX 5 + FIX 2c: the enablement page (drawer queue + run_stopped resync)
# ═══════════════════════════════════════

class _FakePanel:
    def __init__(self):
        self.rows = []

    def add_message(self, role, text):
        self.rows.append((role, text))


def _page_stub():
    """Plain host stand-in for the page helpers, called unbound
    (test_web_chat_drawer precedent — an uninitialized QWidget from __new__
    has broken shiboken attribute access)."""
    import src.ui.pages.enablement.page as pg

    class _Inst:
        pass

    inst = _Inst()
    inst.chat = _FakePanel()
    return pg, inst


class TestDrawerPublishRefusalRequeue:

    def _wire_send(self, pg, inst):
        sent = []
        inst._dispatch_chat = sent.append
        inst._mirror_user_turn = lambda t: None
        inst._web_chat_send = lambda t: pg.EnablementPage._web_chat_send(inst, t)
        return sent

    def test_web_chat_send_reports_dispatch_and_refusal(self):
        pg, inst = _page_stub()
        inst.workbench = SimpleNamespace(is_publish_inflight=True)
        sent = self._wire_send(pg, inst)
        assert inst._web_chat_send("blocked") is False
        assert sent == []
        inst.workbench.is_publish_inflight = False
        assert inst._web_chat_send("through") is True
        assert sent == ["through"]

    def test_refused_drain_keeps_the_message_then_sends_after_the_modal(self):
        """Repro: a message queued while a publish-confirm modal is open was
        popped, refused, and silently LOST. It must stay at the head and go
        out once the modal clears."""
        pg, inst = _page_stub()
        inst.workbench = SimpleNamespace(is_publish_inflight=True)
        inst._engine = SimpleNamespace(is_busy=False)
        inst._web_chat_pending = ["held message"]
        inst._web_chat_bridge = None
        sent = self._wire_send(pg, inst)
        retries = []
        inst._arm_web_drain_retry = lambda: retries.append(1)

        pg.EnablementPage._drain_web_chat_pending(inst)
        assert inst._web_chat_pending == ["held message"], (
            "the queued message was dropped during the publish confirm"
        )
        assert sent == []
        assert retries == [1], "no retry was armed for the refused drain"

        inst.workbench.is_publish_inflight = False     # the modal closed
        pg.EnablementPage._drain_web_chat_pending(inst)
        assert sent == ["held message"], "the message never went out"
        assert inst._web_chat_pending == []

    def test_successful_drain_notifies_the_bridge(self):
        from src.ui.web.chat_bridge import ChatBridge
        pg, inst = _page_stub()
        inst.workbench = SimpleNamespace(is_publish_inflight=False)
        inst._engine = SimpleNamespace(is_busy=False)
        inst._web_chat_pending = ["queued text"]
        bridge = ChatBridge(_FakeEngine())
        inst._web_chat_bridge = bridge
        got = []
        bridge.queuedDispatched.connect(got.append)
        self._wire_send(pg, inst)
        inst._arm_web_drain_retry = lambda: None

        pg.EnablementPage._drain_web_chat_pending(inst)
        assert got == ["queued text"], (
            "the drawer drain did not announce the dispatched text"
        )
        assert inst._web_chat_pending == []

    def test_retry_is_single_flight_and_fires_once(self):
        pg, inst = _page_stub()
        calls = []
        inst._drain_web_chat_pending = lambda: calls.append(1)

        pg.EnablementPage._arm_web_drain_retry(inst)
        assert inst._web_drain_retry_armed is True
        pg.EnablementPage._arm_web_drain_retry(inst)   # second arm: no-op
        _pump(1.4)
        assert calls == [1], "the armed retry stacked or never fired"
        assert inst._web_drain_retry_armed is False


class TestPageRunStopped:

    def test_on_engine_stopped_writes_the_panel_and_refreshes(self):
        pg, inst = _page_stub()
        inst.workbench = SimpleNamespace(active_draft_id=7)
        loads, reloads = [], []
        inst._load_live = lambda prefer_draft_id=None: loads.append(prefer_draft_id)
        inst._reload_active_draft_canvas = lambda: reloads.append(1)
        inst._set_status = lambda s: None

        pg.EnablementPage._on_engine_stopped(inst)
        assert ("a", "Run stopped.") in inst.chat.rows, (
            "the native ChatPanel never heard about the stop"
        )
        assert loads == [7] and reloads == [1], (
            "the post-stop refresh (same as the response path) did not run"
        )

    def test_on_engine_stopped_survives_refresh_errors(self):
        pg, inst = _page_stub()
        inst.workbench = SimpleNamespace(active_draft_id=None)

        def _boom(prefer_draft_id=None):
            raise RuntimeError("db locked")

        inst._load_live = _boom
        inst._reload_active_draft_canvas = lambda: None
        statuses = []
        inst._set_status = statuses.append

        pg.EnablementPage._on_engine_stopped(inst)   # must not raise
        assert ("a", "Run stopped.") in inst.chat.rows
        assert statuses and "Refresh error" in statuses[0]

    def test_setup_engine_wires_run_stopped(self):
        import inspect
        import src.ui.pages.enablement.page as pg
        src = inspect.getsource(pg.EnablementPage._setup_engine)
        assert "run_stopped" in src and "_on_engine_stopped" in src, (
            "the page never resyncs after a stopped run"
        )

    def test_on_engine_stopped_writes_the_panel_directly(self):
        """Doubling guard (same rule as _on_engine_response): the bridge
        already relays runStopped into the drawer, so the page writes the
        panel DIRECTLY — a _chat_say notice would double the line there."""
        import inspect
        import src.ui.pages.enablement.page as pg
        src = inspect.getsource(pg.EnablementPage._on_engine_stopped)
        assert "_chat_say" not in src and "add_message" in src
