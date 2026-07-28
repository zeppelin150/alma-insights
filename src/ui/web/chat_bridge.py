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
    draftsPending = Signal(str)   # JSON: {items: [ {draft_id, title, diff:[{tag,text}],
                                  #   checks:[…], notice, publish_body,
                                  #   publish_body_available} ]}.
                                  #   ``notice`` names what the diff CANNOT show of the
                                  #   bytes that will be sent (lossy projection / missing
                                  #   baseline); ``publish_body`` IS those exact bytes and
                                  #   is present for every draft shape, notice or no
                                  #   notice — the panel renders it as ESCAPED TEXT behind
                                  #   an always-available disclosure so the operator can
                                  #   read what ships regardless of what any detector
                                  #   concluded. Both are part of the approval surface;
                                  #   this bridge is a pure relay and filters neither.
    draftResolved = Signal(str)   # JSON: {draft_id, ok, rejected?, error?}
    actionRequested = Signal(str)  # JSON per claimed action: {id, request_id, type, payload} (M0)
    googleAuthState = Signal(str)  # JSON: {state: 'connecting'|'connected'|'failed'} (M2) — STATUS ONLY
    driveFoldersListed = Signal(str)  # JSON: {request_id, parent_id?, folders:[{id,name,driveId}]} | {request_id, needs_connect} (M3)
    actionResolved = Signal(str)   # JSON: {request_id} once an action resolves → React closes the picker (M3)
    asanaProjectsListed = Signal(str)  # JSON: {request_id, projects:[{gid,name}]} | {request_id, asana_not_connected} (M4)
    guruTargetsListed = Signal(str)  # JSON: {request_id, level:'collections'|'folders', collection_id?, items:[{id,name}]} | {request_id, guru_not_connected} (M5)
    voiceTranscript = Signal(str)  # a recognized on-device dictation utterance
    voiceState = Signal(str)       # 'listening' | 'transcribing' | 'idle' | 'error' | 'unavailable'
    chatNotice = Signal(str)       # JSON {role, text} — host-pushed notices (scan
                                   # results, publish outcomes) for embedded drawers
                                   # (M5.5); NOT an engine turn, NOT JS-invokable

    def __init__(self, engine, send_fn=None, tool_poll=None, session_api=None,
                 job_poll=None, draft_api=None, voice=None, action_poll=None,
                 connect_fn=None, google_state_signal=None, list_fn=None,
                 resolve_fn=None, drive_folders_signal=None,
                 action_resolved_signal=None, asana_list_fn=None,
                 asana_resolve_fn=None, asana_projects_signal=None,
                 guru_list_fn=None, guru_resolve_fn=None,
                 guru_targets_signal=None, confirm_fn=None, cancel_fn=None,
                 parent=None):
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
        # ``action_poll(session_id|None) -> [ {id, request_id, type, payload}, … ]``
        # atomically claims this session's unconsumed action requests (M0). It runs
        # on a SEPARATE, FREE-RUNNING timer (~500ms) — NOT gated on ``busy`` —
        # because the envelope-bearing resolver turn ends the instant the tool
        # returns; a busy-gated poll + cross-process WAL commit latency would miss
        # the single final poll and the picker would never open (invariant 2). The
        # claim is atomic (single-winner) so the always-on cadence is safe.
        self._action_poll = action_poll
        self._action_timer = QTimer(self)
        self._action_timer.setInterval(500)
        self._action_timer.timeout.connect(self._poll_actions)
        if action_poll is not None:
            self._action_timer.start()
        # ``connect_fn`` (the controller's ``start_google_connect``) runs the
        # in-chat Google OAuth flow (M2). Injected, not imported, so the bridge
        # stays the only JS<->Python boundary and tests can supply a fake. The
        # controller emits its connect-card state on ``google_state_signal``; we
        # re-emit it as ``googleAuthState`` so React drives idle/connecting/
        # connected/failed.
        self._connect_fn = connect_fn
        if google_state_signal is not None:
            try:
                google_state_signal.connect(self.googleAuthState)
            except Exception:  # noqa: BLE001 — best-effort wiring
                pass
        # ── Drive folder picker round-trip (M3) ──
        # ``list_fn(parent_id, request_id)`` lists folders OFF-THREAD (the
        # controller spawns a DriveListWorker and emits ``drive_folders_signal``);
        # ``resolve_fn(request_id, folder_id, folder_name, drive_id)`` commits the
        # pick on the main thread and emits ``action_resolved_signal``. Both are
        # injected (not imported) so the bridge stays the only JS<->Python boundary
        # and tests can supply fakes. We re-emit the controller's signals as the
        # bridge's so React (driveFoldersListed / actionResolved) drives the picker.
        self._list_fn = list_fn
        self._resolve_fn = resolve_fn
        if drive_folders_signal is not None:
            try:
                drive_folders_signal.connect(self.driveFoldersListed)
            except Exception:  # noqa: BLE001 — best-effort wiring
                pass
        if action_resolved_signal is not None:
            try:
                action_resolved_signal.connect(self.actionResolved)
            except Exception:  # noqa: BLE001 — best-effort wiring
                pass
        # ── Asana board picker round-trip (M4) ──
        # ``asana_list_fn(request_id)`` lists the projects/boards OFF-THREAD (the
        # controller spawns an AsanaListWorker and emits ``asana_projects_signal``);
        # ``asana_resolve_fn(request_id, project_gid, project_name)`` commits the
        # pick on the main thread and emits ``action_resolved_signal`` (shared with
        # M3). Both injected (not imported) so the bridge stays the only
        # JS<->Python boundary and tests can supply fakes. We re-emit the
        # controller's project signal as the bridge's so React drives the picker.
        self._asana_list_fn = asana_list_fn
        self._asana_resolve_fn = asana_resolve_fn
        if asana_projects_signal is not None:
            try:
                asana_projects_signal.connect(self.asanaProjectsListed)
            except Exception:  # noqa: BLE001 — best-effort wiring
                pass
        # ── Guru publish-target picker round-trip (M5) ──
        # ``guru_list_fn(collection_id, request_id)`` lists the targets OFF-THREAD
        # (empty collection_id → collections; non-empty → that collection's folders;
        # the controller spawns a GuruListWorker and emits ``guru_targets_signal``);
        # ``guru_resolve_fn(request_id, collection_id, folder_id)`` commits the pick
        # on the main thread and emits ``action_resolved_signal`` (shared with M3/M4).
        # Both injected (not imported) so the bridge stays the only JS<->Python
        # boundary and tests can supply fakes. We re-emit the controller's target
        # signal as the bridge's so React drives the two-level picker.
        self._guru_list_fn = guru_list_fn
        self._guru_resolve_fn = guru_resolve_fn
        if guru_targets_signal is not None:
            try:
                guru_targets_signal.connect(self.guruTargetsListed)
            except Exception:  # noqa: BLE001 — best-effort wiring
                pass
        # ── GATED write confirm channel (M7b) ──
        # ``confirm_fn(request_id)`` (the controller's ``execute_write``) runs the
        # gated, non-idempotent live write on a real operator Confirm click;
        # ``cancel_fn(request_id)`` (``cancel_write``) resolves the row without any
        # write on Cancel. Both injected (not imported) so the bridge stays the only
        # JS<->Python boundary and tests can supply fakes. There is NO direct-execute
        # tool — these slots are the ONLY way a write runs, and only via a click.
        self._confirm_fn = confirm_fn
        self._cancel_fn = cancel_fn
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

    # ── host-pushed notices (Python -> JS only; M5.5) ────────────────

    def push_notice(self, role, text):
        """Relay a host-side chat message (page.py's add_message flow) into any
        embedded web drawer. Plain method, deliberately NOT a Slot — page
        scripts must not be able to forge notices."""
        try:
            self.chatNotice.emit(json.dumps(
                {"role": "u" if role == "u" else "a", "text": str(text or "")}))
        except Exception:  # noqa: BLE001 — notices are best-effort
            pass

    # ── inbound (JS -> Python) ──────────────────────────────────────

    @Slot(str)
    def send(self, text):
        """Send a user message into the chat runtime."""
        self._send_fn(text or "")

    @Slot(result=str)
    def ping(self):
        """Liveness probe for the JS<->Python round-trip (no engine call)."""
        return "pong"

    @Slot(str)
    def openExternal(self, url):
        """Open a rendered-markdown link in the system browser — http/https ONLY
        (a webview link must never navigate the app away or launch a local-scheme
        handler)."""
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            u = QUrl(url or "")
            if u.scheme().lower() in ("http", "https"):
                QDesktopServices.openUrl(u)
        except Exception:  # noqa: BLE001 — never crash the chat
            pass

    # ── connect Google in chat (M2) ─────────────────────────────────

    @Slot()
    def connectGoogle(self):
        """Begin the in-chat Google OAuth flow (the ConnectGoogleCard button).

        QWebChannel is the trust boundary: any webview script could call this, so
        the human-gate is enforced controller-side — ``start_google_connect`` is
        single-flight (one worker per real click). React must drive the button
        from a real click only and never auto-invoke this off a signal."""
        if self._connect_fn is None:
            return
        try:
            self._connect_fn()
        except Exception:  # noqa: BLE001 — never crash the chat
            self._safe_emit_str(self.googleAuthState, "{\"state\": \"failed\"}")

    # ── Drive folder picker round-trip (M3) ─────────────────────────

    @Slot(str, str)
    def driveListFolders(self, parent_id, request_id):
        """Lazy-tree expand: list the Drive folders under ``parent_id`` for the
        picker. Delegates to the controller's OFF-THREAD lister; results arrive
        on ``driveFoldersListed``. QWebChannel is the trust boundary — the
        controller checks ``is_active()`` and never reads Drive when not active."""
        if self._list_fn is None:
            return
        try:
            self._list_fn(parent_id or "", request_id or "")
        except Exception:  # noqa: BLE001 — never crash the chat
            pass

    @Slot(str, str, str, str)
    def driveFolderPicked(self, request_id, folder_id, folder_name, drive_id):
        """The operator chose a folder: commit the pick on the main thread. The
        controller validates session ownership + single-winner resolves, persists
        the id (settings is source of truth), notifies Renn with the id ONLY, and
        emits ``actionResolved`` so React closes the picker."""
        if self._resolve_fn is None:
            return
        try:
            self._resolve_fn(request_id or "", folder_id or "",
                             folder_name or "", drive_id or "")
        except Exception:  # noqa: BLE001 — never crash the chat
            pass

    # ── Asana board picker round-trip (M4) ──────────────────────────

    @Slot(str)
    def asanaListProjects(self, request_id):
        """Open-time fetch: list the Asana projects/boards for the picker.
        Delegates to the controller's OFF-THREAD lister; results arrive on
        ``asanaProjectsListed``. QWebChannel is the trust boundary — the worker
        reports asana_not_connected when no shared PAT is set."""
        if self._asana_list_fn is None:
            return
        try:
            self._asana_list_fn(request_id or "")
        except Exception:  # noqa: BLE001 — never crash the chat
            pass

    @Slot(str, str, str)
    def asanaBoardPicked(self, request_id, project_gid, project_name):
        """The operator chose a board: commit the pick on the main thread. The
        controller validates session ownership + single-winner resolves, persists
        the board (settings is source of truth), notifies Renn, and emits
        ``actionResolved`` so React closes the picker."""
        if self._asana_resolve_fn is None:
            return
        try:
            self._asana_resolve_fn(request_id or "", project_gid or "",
                                   project_name or "")
        except Exception:  # noqa: BLE001 — never crash the chat
            pass

    # ── Guru publish-target picker round-trip (M5) ──────────────────

    @Slot(str, str)
    def guruListTargets(self, collection_id, request_id):
        """Two-level lazy fetch: empty ``collection_id`` lists the Guru collections;
        a non-empty one lists THAT collection's folders. Delegates to the
        controller's OFF-THREAD lister; results arrive on ``guruTargetsListed``.
        QWebChannel is the trust boundary — the worker reports guru_not_connected
        when Guru creds aren't set."""
        if self._guru_list_fn is None:
            return
        try:
            self._guru_list_fn(collection_id or "", request_id or "")
        except Exception:  # noqa: BLE001 — never crash the chat
            pass

    @Slot(str, str, str)
    def guruTargetPicked(self, request_id, collection_id, folder_id):
        """The operator chose a publish target: commit the pick on the main thread.
        The controller validates session ownership + single-winner resolves,
        persists the collection (+ optional folder) ids (settings is source of
        truth, read by push_guru_draft), notifies Renn, and emits ``actionResolved``
        so React closes the picker. folder_id may be empty (collection-level publish)."""
        if self._guru_resolve_fn is None:
            return
        try:
            self._guru_resolve_fn(request_id or "", collection_id or "",
                                  folder_id or "")
        except Exception:  # noqa: BLE001 — never crash the chat
            pass

    # ── gated write confirm channel (M7b) ──────────────────────────

    @Slot(str)
    def confirmWrite(self, request_id):
        """The operator clicked Confirm on a write card: run the gated write on the
        main thread. The controller validates session ownership, mark_resolves FIRST
        (single-winner — one click = one write), then dispatches the non-idempotent
        live write off-thread. QWebChannel is the trust boundary; the controller's
        single-winner claim is the enforcement that a double/forged invoke writes
        exactly once."""
        if self._confirm_fn is None:
            return
        try:
            self._confirm_fn(request_id or "")
        except Exception:  # noqa: BLE001 — never crash the chat
            pass

    @Slot(str)
    def cancelWrite(self, request_id):
        """The operator clicked Cancel on a write card: resolve the row with NO
        write. The controller validates session ownership, mark_resolves (re-opens
        the human-gate), and notifies Renn it was cancelled."""
        if self._cancel_fn is None:
            return
        try:
            self._cancel_fn(request_id or "")
        except Exception:  # noqa: BLE001 — never crash the chat
            pass

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

    # ── action channel — free-running picker/connect poll (M0) ──────

    def _poll_actions(self):
        """Claim any unconsumed action requests for the active session and emit
        ``actionRequested`` per row. Free-running (always on while the page is
        alive); the underlying claim is atomic, so each action emits exactly once
        even though this fires continuously."""
        if self._action_poll is None:
            return
        try:
            rows = self._action_poll(None) or []
        except Exception:  # noqa: BLE001 — the action channel is best-effort
            return
        for r in rows:
            try:
                self.actionRequested.emit(json.dumps(r, default=str))
            except Exception:  # noqa: BLE001
                pass

    # ── in-thread review / sign-off (M5) ────────────────────────────

    def _poll_drafts(self, bind: bool = False):
        """Emit the review list. ``bind`` decides whether this render is allowed
        to MINT an approval binding.

        It defaults to False, and that default is load-bearing. ``_on_busy``
        calls this on every ``busy_changed(False)`` — the end of EVERY Renn turn
        — and the controller used to re-fingerprint each draft here. Since
        ``revise_draft`` runs inside a turn, an injected revision made between
        the operator reading the panel and pressing Approve was silently
        RE-AUTHORIZED by this poll: traced, hostile bytes reached the live Guru
        spy; with only this call's binding removed, the same run refused with
        ``draft_changed_after_review`` and sent nothing.

        So the poll refreshes the LIST and nothing else. Only ``openReview`` —
        the operator opening the panel — mints.
        """
        if self._draft_api is None or not hasattr(self._draft_api, "pending_drafts"):
            return
        try:
            drafts = self._drafts_snapshot(bind)
        except Exception:  # noqa: BLE001 — best-effort
            return
        try:
            payload = json.dumps({"items": drafts}, default=str)
        except Exception:  # noqa: BLE001
            return
        if payload != self._drafts_last:
            self._drafts_last = payload
            self.draftsPending.emit(payload)

    def _drafts_snapshot(self, bind: bool):
        """``pending_drafts(bind=…)``, tolerating an API that predates the flag
        (the duck-typed fakes, and any older controller)."""
        try:
            return self._draft_api.pending_drafts(bind=bind) or []
        except TypeError:
            return self._draft_api.pending_drafts() or []

    @Slot()
    def refreshDrafts(self):
        """On-demand refresh of the review LIST → ``draftsPending``.

        Deliberately non-binding: this slot is page-callable, so it must not be
        able to authorize anything. It shows the operator what is pending; it
        does not decide that they read it."""
        self._drafts_last = None
        self._poll_drafts(bind=False)

    @Slot()
    def openReview(self):
        """The operator OPENED the review panel — the one render that binds.

        Separated from ``refreshDrafts`` so that minting is an act, not a side
        effect of a timer. A page script can of course call this too; that only
        arms a binding, and a binding alone publishes nothing — the native
        confirm in ``approve_draft`` is what proves a human, and it displays the
        exact bytes and target before anything leaves the machine."""
        self._drafts_last = None
        self._poll_drafts(bind=True)

    @Slot(str)
    def approveDraft(self, draft_id):
        """Publish the reviewed draft → ``draftResolved`` (+ refresh the panel).

        Pure relay, as always — the controller owns the decision. It refuses
        when the draft moved between the render that bound the approval and
        this click (the ``revise_draft`` TOCTOU), and its ``message`` is the
        operator-facing sentence for that refusal. Relaying it verbatim is the
        whole reason the refusal is honest on screen: a bare ``ok:false`` would
        look identical to a Guru outage, and the operator would retry into a
        publish of content they never read.

        ``refused`` distinguishes "the gate said no" from "the publish was
        attempted and failed", which ``ok`` alone cannot; the controller's
        ``published`` flag is not re-emitted because ``ok`` already carries it
        on this surface."""
        result = self._draft_call("approve_draft", draft_id) or {}
        payload = {"draft_id": draft_id, "ok": bool(result.get("ok"))}
        if result.get("error"):
            payload["error"] = result["error"]
        if result.get("message"):
            payload["message"] = result["message"]
        if result.get("refused"):
            payload["refused"] = True
        self._safe_emit(self.draftResolved, payload)
        # NON-BINDING. A failed publish re-arms the draft, and re-minting a
        # binding for it here would hand the next click an authorization no
        # human granted.
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


# ═══════════════════════════════════════════════════════════════════════
#  THE NATIVE PUBLISH GATE (2026-07-27)
#
#  The Guru publish is the only LIVE REMOTE WRITE reachable from this chat,
#  and until now it had no native confirmation at all. Both slots that reach
#  it — refreshDrafts() and approveDraft() — are page-callable, and the
#  QWebChannel is the trust boundary: a probe drove those two calls alone and
#  published hostile bytes to a live-shaped Guru client with every signal
#  discarded and no dialog on screen.
#
#  The Zendesk lane found and fixed this defect class for a CLIPBOARD release
#  (page.py::_build_copy_confirm_dialog). This is the same gate, on the lane
#  that performs the real write, and it mirrors that implementation exactly:
#
#    * the payload lives in a VISIBLE, scrollable, read-only QPlainTextEdit —
#      never setDetailedText, which Qt collapses behind a "Show Details…"
#      button nothing here ever presses;
#    * the TARGET gets its own bounded plain-text widget, because the target is
#      half of what the sign-off commits to and it appeared on no surface;
#    * every content-derived string is Qt.PlainText (force_plain_text sweeps
#      the rest) — a draft TITLE is attacker-writable through Renn's propose
#      tools, QLabel defaults to AutoText, and mightBeRichText only scans to
#      the first newline, which is exactly where a title sits;
#    * the default button is CANCEL, so a stray Enter never publishes.
# ═══════════════════════════════════════════════════════════════════════

_PUBLISH_DIALOG_MIN = (760, 560)
_PUBLISH_TARGET_MAX_HEIGHT = 92
_PUBLISH_BYTES_MIN_HEIGHT = 260

PUBLISH_CONFIRM_TITLE = "Publish to the live Guru knowledge base?"

PUBLISH_CONFIRM_HEADING = (
    "This publishes to the LIVE Guru knowledge base. Nothing has been sent "
    "yet.\n"
    "Below is the destination, then the EXACT bytes that will be sent — not a "
    "preview, not a summary, and not the markdown.\n"
    "Read both. If anything is unfamiliar, press Cancel: nothing is published "
    "and the draft stays in the review panel.")


def build_publish_confirm_dialog(parent, payload: dict):
    """Build (do not run) the native Guru publish confirmation.

    Separated from the exec so the properties that matter are testable without
    spinning a modal event loop: the bytes are in a VISIBLE widget rather than
    a collapsed pane, the resolved target is on screen, and the default button
    is Cancel.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel,
                                   QPlainTextEdit, QVBoxLayout)
    from src.ui.pages.enablement._common import (force_plain_text,
                                                 style_native_dialog)

    data = payload or {}
    dlg = QDialog(parent)
    dlg.setObjectName("guruPublishConfirm")
    dlg.setWindowTitle(PUBLISH_CONFIRM_TITLE)
    dlg.setModal(True)
    dlg.setMinimumSize(*_PUBLISH_DIALOG_MIN)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(20, 18, 20, 16)
    lay.setSpacing(12)

    head = QLabel(PUBLISH_CONFIRM_HEADING)
    head.setObjectName("guruPublishHeading")
    # No interpolation here at all — the title and target are attacker-writable
    # and live in their own widgets below, so this disclosure is Python's alone
    # and its line structure cannot be rewritten by a crafted draft.
    head.setTextFormat(Qt.PlainText)
    head.setWordWrap(True)
    head.setTextInteractionFlags(Qt.TextSelectableByMouse)
    lay.addWidget(head)

    target = QPlainTextEdit("\n".join([
        f"Card title: {data.get('title') or ''}",
        f"Destination: {data.get('target_label') or ''}",
        f"card_id: {data.get('card_id') or '(none — a new card)'}",
        f"collection_id: {data.get('collection_id') or '(default)'}",
        f"folder_id: {data.get('folder_id') or '(none)'}",
    ]))
    target.setObjectName("guruPublishTarget")
    target.setReadOnly(True)
    target.setMaximumHeight(_PUBLISH_TARGET_MAX_HEIGHT)
    target.setTabChangesFocus(True)
    target.setStyleSheet(
        "QPlainTextEdit { background:#FFF7ED; color:#8A5A00;"
        " border:1px solid #E5B75A; border-radius:4px; padding:6px;"
        " font-size:12px; }")
    lay.addWidget(target)

    view = QPlainTextEdit(str(data.get("publish_body") or ""))
    view.setObjectName("guruPublishBytes")
    view.setReadOnly(True)
    view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
    view.setMinimumHeight(_PUBLISH_BYTES_MIN_HEIGHT)
    view.setTabChangesFocus(True)
    view.setStyleSheet(
        "QPlainTextEdit { background:#FFFFFF; color:#2F3941;"
        " border:1px solid #C2C8CC; border-radius:4px; padding:8px;"
        " font-family:'Consolas','SFMono-Regular',Menlo,monospace;"
        " font-size:12px; }")
    lay.addWidget(view, 1)

    buttons = QDialogButtonBox(dlg)
    cancel = buttons.addButton("Cancel", QDialogButtonBox.RejectRole)
    publish = buttons.addButton("Publish to Guru", QDialogButtonBox.AcceptRole)
    publish.setAutoDefault(False)
    publish.setDefault(False)
    cancel.setAutoDefault(True)
    cancel.setDefault(True)
    cancel.setFocus()
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    lay.addWidget(buttons)
    style_native_dialog(dlg)
    # Belt and braces: any label a future edit adds here is plain text too.
    force_plain_text(dlg)
    return dlg


class PublishConfirmHost:
    """The object ``AgentChatController`` calls to take the operator's native
    confirmation. Injected from ``MainWindow``; when it is absent the
    controller refuses to publish (see ``AgentChatController._confirm_publish``).

    Deliberately NOT a QObject and NOT registered on any web channel: nothing
    a page script can reach may call it, and nothing about it is a slot.
    """

    def __init__(self, parent_widget=None):
        self._parent = parent_widget

    def confirm_publish(self, payload: dict) -> bool:
        """Show the modal gate. True ONLY on an explicit accept."""
        from PySide6.QtWidgets import QDialog
        dlg = build_publish_confirm_dialog(self._parent, payload)
        try:
            return dlg.exec() == QDialog.Accepted
        finally:
            dlg.deleteLater()
