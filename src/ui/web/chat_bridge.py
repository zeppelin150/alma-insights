"""QWebChannel bridge — exposes the chat runtime to the embedded web UI.

The web layer is a pure renderer: it calls these ``@Slot`` methods and connects
to these ``Signal``s over QWebChannel (in-process, no network). ALL LLM / CLI /
MCP / redaction logic stays in Python behind the injected ``engine`` — a real
``ChatEngine`` in production, a fake in tests. Swapping the frontend changes
nothing on the backend; this bridge is the only boundary.

The ``engine`` must provide: ``response_ready(str)``, ``error_occurred(str)``,
``busy_changed(bool)``, ``status_update(str)`` signals; ``send(text)``; and
(optionally) ``set_telemetry_callback(cb)`` where ``cb(role, content, dict)``.
"""

from __future__ import annotations

import json

from PySide6.QtCore import QObject, QTimer, Signal, Slot


class ChatBridge(QObject):
    """The single JS<->Python boundary for the Agent chat."""

    responseReady = Signal(str)   # a completed assistant turn
    errorOccurred = Signal(str)
    busyChanged = Signal(bool)    # True while a turn is in flight
    statusUpdate = Signal(str)
    telemetry = Signal(str)       # JSON: {role, model_used, tokens_in, tokens_out, latency_ms, tool_names}
    toolCall = Signal(str)        # JSON per finished tool call: {id, name, rows, ms, ok, error}

    def __init__(self, engine, send_fn=None, tool_poll=None, parent=None):
        super().__init__(parent)
        self._engine = engine
        # ``send_fn`` lets a controller intercept sends (e.g. to lazily wire the
        # provider/MCP on first message); defaults to the engine's own send.
        self._send_fn = send_fn if send_fn is not None else engine.send
        # ``tool_poll(since_id) -> [ {id, name, rows, ms, ok, error}, … ]`` reads
        # newly-finished tool executions (chat_tool_executions) for the live
        # tool-call timeline. Polled while a turn is in flight.
        self._tool_poll = tool_poll
        self._tool_cursor = 0
        self._tool_timer = QTimer(self)
        self._tool_timer.setInterval(300)
        self._tool_timer.timeout.connect(self._poll_tools)
        # Re-emit the engine's signals as the bridge's (signal-to-signal).
        engine.response_ready.connect(self.responseReady)
        engine.error_occurred.connect(self.errorOccurred)
        engine.busy_changed.connect(self.busyChanged)
        engine.busy_changed.connect(self._on_busy)
        engine.status_update.connect(self.statusUpdate)
        if hasattr(engine, "set_telemetry_callback"):
            engine.set_telemetry_callback(self._on_telemetry)

    # ── inbound (JS -> Python) ──────────────────────────────────────

    @Slot(str)
    def send(self, text):
        """Send a user message into the chat runtime."""
        self._send_fn(text or "")

    @Slot(result=str)
    def ping(self):
        """Liveness probe for the JS<->Python round-trip (no engine call)."""
        return "pong"

    # ── live tool-call timeline (tail chat_tool_executions) ─────────

    def _on_busy(self, busy):
        if self._tool_poll is None:
            return
        if busy:
            # Baseline the cursor to the newest existing row so we only surface
            # THIS turn's tool calls, then poll while it runs.
            self._tool_cursor = self._latest_tool_id()
            self._tool_timer.start()
        else:
            self._poll_tools()        # final catch after the turn completes
            self._tool_timer.stop()

    def _latest_tool_id(self) -> int:
        try:
            rows = self._tool_poll(0) or []
            return max((int(r.get("id", 0)) for r in rows), default=0)
        except Exception:  # noqa: BLE001
            return self._tool_cursor

    def _poll_tools(self):
        if self._tool_poll is None:
            return
        try:
            rows = self._tool_poll(self._tool_cursor) or []
        except Exception:  # noqa: BLE001 — the timeline is best-effort
            return
        for r in rows:
            self._tool_cursor = max(self._tool_cursor, int(r.get("id", self._tool_cursor)))
            try:
                self.toolCall.emit(json.dumps(r, default=str))
            except Exception:  # noqa: BLE001
                pass

    # ── telemetry callback (Python -> JS) ───────────────────────────

    def _on_telemetry(self, role, content, telemetry):
        try:
            payload = {"role": role}
            if isinstance(telemetry, dict):
                payload.update(telemetry)
            self.telemetry.emit(json.dumps(payload, default=str))
        except Exception:  # noqa: BLE001 — telemetry is best-effort, never fatal
            pass
