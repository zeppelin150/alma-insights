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

from PySide6.QtCore import QObject, Signal, Slot


class ChatBridge(QObject):
    """The single JS<->Python boundary for the Agent chat."""

    responseReady = Signal(str)   # a completed assistant turn
    errorOccurred = Signal(str)
    busyChanged = Signal(bool)    # True while a turn is in flight
    statusUpdate = Signal(str)
    telemetry = Signal(str)       # JSON: {role, model_used, tokens_in, tokens_out, latency_ms, tool_names}

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self._engine = engine
        # Re-emit the engine's signals as the bridge's (signal-to-signal).
        engine.response_ready.connect(self.responseReady)
        engine.error_occurred.connect(self.errorOccurred)
        engine.busy_changed.connect(self.busyChanged)
        engine.status_update.connect(self.statusUpdate)
        if hasattr(engine, "set_telemetry_callback"):
            engine.set_telemetry_callback(self._on_telemetry)

    # ── inbound (JS -> Python) ──────────────────────────────────────

    @Slot(str)
    def send(self, text):
        """Send a user message into the chat runtime."""
        self._engine.send(text or "")

    @Slot(result=str)
    def ping(self):
        """Liveness probe for the JS<->Python round-trip (no engine call)."""
        return "pong"

    # ── telemetry callback (Python -> JS) ───────────────────────────

    def _on_telemetry(self, role, content, telemetry):
        try:
            payload = {"role": role}
            if isinstance(telemetry, dict):
                payload.update(telemetry)
            self.telemetry.emit(json.dumps(payload, default=str))
        except Exception:  # noqa: BLE001 — telemetry is best-effort, never fatal
            pass
