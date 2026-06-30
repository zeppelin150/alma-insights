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
    tokenStreamed = Signal(str)   # a single text delta as the assistant turn streams
    sessionsListed = Signal(str)  # JSON: {mode: 'recent'|'search', items: [...]}
    historyLoaded = Signal(str)   # JSON: {session_id, messages: [{role, content}], new?}
    sessionDeleted = Signal(str)  # JSON: {session_id, ok}
    jobsListed = Signal(str)      # JSON: {items: [ {job_id, title, status, progress_pct, steps:[…]} ]}
    draftsPending = Signal(str)   # JSON: {items: [ {draft_id, title, diff:[{tag,text}], checks:[…]} ]}
    draftResolved = Signal(str)   # JSON: {draft_id, ok, rejected?, error?}
    voiceTranscript = Signal(str)  # a recognized on-device dictation utterance
    voiceState = Signal(str)       # 'listening' | 'transcribing' | 'idle' | 'error' | 'unavailable'

    def __init__(self, engine, send_fn=None, tool_poll=None, session_api=None,
                 job_poll=None, draft_api=None, voice=None, parent=None):
        super().__init__(parent)
        self._engine = engine
        self._prior_telemetry = None   # a controller's persistence callback, if any
        # ``session_api`` powers the past-chat browser (M3). Duck-typed; any
        # subset of: ``list_sessions()``, ``search_sessions(q)``,
        # ``load_session(id)``, ``delete_session(id)``, ``new_session()``.
        # Injected (not imported) so the bridge stays the only JS<->Python
        # boundary and tests can supply a fake.
        self._session_api = session_api
        # ``send_fn`` lets a controller intercept sends (e.g. to lazily wire the
        # provider/MCP on first message); defaults to the engine's own send.
        self._send_fn = send_fn if send_fn is not None else engine.send
        # ``tool_poll(since_id) -> [ {id, name, rows, ms, ok, error}, … ]`` reads
        # newly-finished tool executions (chat_tool_executions) for the live
        # tool-call timeline. Polled while a turn is in flight.
        self._tool_poll = tool_poll
        self._tool_cursor = 0
        # ``job_poll() -> [jobs]`` returns the current session's jobs (with steps)
        # for the live sidebar (M4). Polled on the same cadence as the tool
        # timeline; emitted only when the serialized list changes.
        self._job_poll = job_poll
        self._jobs_last = None
        # ``draft_api`` powers the in-thread review/sign-off panel (M5). Duck-typed:
        # ``pending_drafts()``, ``approve_draft(id)``, ``reject_draft(id)``.
        self._draft_api = draft_api
        self._drafts_last = None
        # ``voice`` is a VoiceController (on-device dictation, M6). Its signals
        # re-emit through the bridge; absent → the mic stays disabled in the UI.
        self._voice = voice
        if voice is not None:
            if hasattr(voice, "transcript"):
                voice.transcript.connect(self.voiceTranscript)
            if hasattr(voice, "state_changed"):
                voice.state_changed.connect(self.voiceState)
        self._tool_timer = QTimer(self)
        self._tool_timer.setInterval(300)
        self._tool_timer.timeout.connect(self._poll_tools)
        self._tool_timer.timeout.connect(self._poll_jobs)
        self._tool_timer.timeout.connect(self._poll_drafts)
        # Re-emit the engine's signals as the bridge's (signal-to-signal).
        engine.response_ready.connect(self.responseReady)
        engine.error_occurred.connect(self.errorOccurred)
        engine.busy_changed.connect(self.busyChanged)
        engine.busy_changed.connect(self._on_busy)
        engine.status_update.connect(self.statusUpdate)
        if hasattr(engine, "token_streamed"):
            engine.token_streamed.connect(self.tokenStreamed)
        if hasattr(engine, "set_telemetry_callback"):
            # Chain — don't clobber. A controller may already have registered a
            # telemetry callback (e.g. to persist the turn to chat_messages); we
            # call it first, then add the live meter on top.
            self._prior_telemetry = getattr(engine, "_telemetry_callback", None)
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

    # ── past-chat browser (M3) ──────────────────────────────────────

    @Slot()
    def listSessions(self):
        """Recent chat sessions for the history drawer → ``sessionsListed``."""
        self._emit_sessions("recent", self._call_api("list_sessions"))

    @Slot(str)
    def searchSessions(self, query):
        """FTS search across past chats → ``sessionsListed`` (mode='search')."""
        self._emit_sessions("search", self._call_api("search_sessions", query or ""))

    @Slot(str)
    def loadSession(self, session_id):
        """Load a past transcript + make it active → ``historyLoaded``."""
        data = self._call_api("load_session", session_id) or {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("session_id", session_id)
        data.setdefault("messages", [])
        self._safe_emit(self.historyLoaded, data)

    @Slot(str)
    def deleteSession(self, session_id):
        """Delete a past chat (cascade) → ``sessionDeleted``."""
        ok = bool(self._call_api("delete_session", session_id))
        self._safe_emit(self.sessionDeleted, {"session_id": session_id, "ok": ok})

    @Slot()
    def newSession(self):
        """Start a fresh thread → ``historyLoaded`` with an empty transcript."""
        self._call_api("new_session")
        self._safe_emit(self.historyLoaded,
                        {"session_id": None, "messages": [], "new": True})

    @Slot()
    def listJobs(self):
        """On-demand refresh of the job sidebar → ``jobsListed``."""
        self._jobs_last = None   # force an emit even if unchanged
        self._poll_jobs()

    def _call_api(self, method, *args):
        api = self._session_api
        if api is None or not hasattr(api, method):
            return None
        try:
            return getattr(api, method)(*args)
        except Exception:  # noqa: BLE001 — the browser is best-effort, never fatal
            return None

    def _emit_sessions(self, mode, items):
        self._safe_emit(self.sessionsListed, {"mode": mode, "items": items or []})

    def _safe_emit(self, signal, payload):
        try:
            signal.emit(json.dumps(payload, default=str))
        except Exception:  # noqa: BLE001
            pass

    # ── live tool-call timeline (tail chat_tool_executions) ─────────

    def _on_busy(self, busy):
        if self._tool_poll is None and self._job_poll is None and self._draft_api is None:
            return
        if busy:
            # Baseline the tool cursor to the newest existing row so we only
            # surface THIS turn's tool calls; force a fresh job/draft emit. Then
            # poll all three while the turn runs.
            if self._tool_poll is not None:
                self._tool_cursor = self._latest_tool_id()
            self._jobs_last = None
            self._drafts_last = None
            self._tool_timer.start()
        else:
            self._poll_tools()        # final catch after the turn completes
            self._poll_jobs()
            self._poll_drafts()
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

    # ── live job sidebar (M4) ───────────────────────────────────────

    def _poll_jobs(self):
        if self._job_poll is None:
            return
        try:
            jobs = self._job_poll() or []
        except Exception:  # noqa: BLE001 — the sidebar is best-effort
            return
        try:
            payload = json.dumps({"items": jobs}, default=str)
        except Exception:  # noqa: BLE001
            return
        if payload != self._jobs_last:   # only emit on change (cheap dedupe)
            self._jobs_last = payload
            self.jobsListed.emit(payload)

    # ── in-thread review / sign-off (M5) ────────────────────────────

    def _poll_drafts(self):
        if self._draft_api is None or not hasattr(self._draft_api, "pending_drafts"):
            return
        try:
            drafts = self._draft_api.pending_drafts() or []
        except Exception:  # noqa: BLE001 — best-effort
            return
        try:
            payload = json.dumps({"items": drafts}, default=str)
        except Exception:  # noqa: BLE001
            return
        if payload != self._drafts_last:
            self._drafts_last = payload
            self.draftsPending.emit(payload)

    @Slot()
    def refreshDrafts(self):
        """On-demand refresh of the review panel → ``draftsPending``."""
        self._drafts_last = None
        self._poll_drafts()

    @Slot(str)
    def approveDraft(self, draft_id):
        """Record sign-off + publish → ``draftResolved`` (+ refresh the panel)."""
        result = self._draft_call("approve_draft", draft_id) or {}
        payload = {"draft_id": draft_id, "ok": bool(result.get("ok"))}
        if result.get("error"):
            payload["error"] = result["error"]
        self._safe_emit(self.draftResolved, payload)
        self.refreshDrafts()

    @Slot(str)
    def rejectDraft(self, draft_id):
        """Decline a pending publish → ``draftResolved`` (+ refresh the panel)."""
        result = self._draft_call("reject_draft", draft_id) or {}
        self._safe_emit(self.draftResolved,
                        {"draft_id": draft_id, "ok": bool(result.get("ok")), "rejected": True})
        self.refreshDrafts()

    def _draft_call(self, method, *args):
        api = self._draft_api
        if api is None or not hasattr(api, method):
            return None
        try:
            return getattr(api, method)(*args)
        except Exception:  # noqa: BLE001 — never fatal
            return None

    # ── on-device voice dictation (M6) ──────────────────────────────

    @Slot(result=bool)
    def voiceAvailable(self):
        """Whether on-device dictation can run here (lets the UI enable the mic)."""
        return bool(self._voice is not None and getattr(self._voice, "available", False))

    @Slot()
    def startVoice(self):
        """Begin push-to-talk capture; results arrive via ``voiceTranscript``."""
        if self._voice is not None:
            try:
                self._voice.start()
            except Exception:  # noqa: BLE001 — dictation never crashes the chat
                self._safe_emit_str(self.voiceState, "error")

    @Slot()
    def stopVoice(self):
        """End push-to-talk capture."""
        if self._voice is not None:
            try:
                self._voice.stop()
            except Exception:  # noqa: BLE001
                pass

    def _safe_emit_str(self, signal, value):
        try:
            signal.emit(value)
        except Exception:  # noqa: BLE001
            pass

    # ── telemetry callback (Python -> JS) ───────────────────────────

    def _on_telemetry(self, role, content, telemetry):
        if self._prior_telemetry is not None:   # run the controller's persistence first
            try:
                self._prior_telemetry(role, content, telemetry)
            except Exception:  # noqa: BLE001
                pass
        try:
            payload = {"role": role}
            if isinstance(telemetry, dict):
                payload.update(telemetry)
            self.telemetry.emit(json.dumps(payload, default=str))
        except Exception:  # noqa: BLE001 — telemetry is best-effort, never fatal
            pass
