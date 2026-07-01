"""AgentChatController — builds and owns a fully-wired enablement ``ChatEngine``
for the standalone Agent page (``src/ui/web``).

It mirrors the enablement Workbench's engine setup so the embedded web chat talks
to the SAME live backend: the ``alma-chat-tools`` MCP server, provider routing
(claude/gemini via ``build_client_for_task``), a persisted chat session, and the
adaptive bridge-recycle. Kept self-contained so the Agent page doesn't depend on
the Workbench page; the shared setup is a candidate to factor out later.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot

logger = logging.getLogger("alma.agent_chat")


class DriveListWorker(QThread):
    """Off-thread Drive folder lister for the M3 picker (the GoogleOAuthWorker
    pattern). DriveReader's HTTP calls block, so they MUST NOT run inside the
    QWebChannel slot (that freezes the Qt event loop per tree expand — invariant
    6). This worker runs ``list_drives()`` for the roots (empty/``root`` parent)
    or ``list_folders(parent_id)`` for a node's children, then emits id+name+
    driveId rows on ``finished`` (queued to the main thread). No file bodies ever
    cross — only folder rows for lazy-tree navigation.
    """

    finished = Signal(str, str, list)   # (request_id, parent_id, folders)
    failed = Signal(str, str)           # (request_id, message — redacted/no PHI)

    def __init__(self, request_id: str, parent_id: str, parent=None):
        super().__init__(parent)
        self._request_id = request_id or ""
        self._parent_id = parent_id or ""

    def run(self):
        try:
            from src.data.drive_reader import DriveReader
            reader = DriveReader.from_settings()
            pid = (self._parent_id or "").strip()
            if pid and pid.lower() != "root":
                rows = reader.list_folders(pid)
            else:
                rows = reader.list_drives()
            folders = [{"id": r.get("id"), "name": r.get("name"),
                        "driveId": r.get("drive_id") or r.get("driveId")}
                       for r in (rows or [])]
            self.finished.emit(self._request_id, self._parent_id, folders)
        except Exception as exc:  # noqa: BLE001 — surface a short, non-PHI message
            self.failed.emit(self._request_id, str(exc)[:160])


class AsanaListWorker(QThread):
    """Off-thread Asana project lister for the M4 picker (mirrors DriveListWorker).

    AsanaClient is stdlib ``urllib`` so it's perfectly safe off-thread, but the
    HTTP still blocks, so it MUST NOT run inside the QWebChannel slot (that freezes
    the Qt event loop while the picker opens — invariant 6). This worker calls the
    live ``list_asana_projects`` impl (shared PAT from the keyring) and emits
    ``[{gid, name}]`` on ``finished`` (queued to the main thread). If no PAT is
    configured it emits ``asana_not_connected`` so the picker can offer a Settings
    hint. No task/board internals ever cross — only gid+name for selection.
    """

    finished = Signal(str, list)        # (request_id, projects [{gid,name}])
    notConnected = Signal(str)          # (request_id) — no shared PAT
    failed = Signal(str, str)           # (request_id, message — short/no PHI)

    def __init__(self, request_id: str, parent=None):
        super().__init__(parent)
        self._request_id = request_id or ""

    def run(self):
        try:
            from src.data.chat_tools.enablement_tools import _list_asana_projects_impl
            res = _list_asana_projects_impl(None) or {}
            if not res.get("ok"):
                if res.get("error") == "asana_not_connected":
                    self.notConnected.emit(self._request_id)
                else:
                    self.failed.emit(self._request_id,
                                     str(res.get("error") or "asana_list_failed")[:160])
                return
            projects = [{"gid": p.get("gid"), "name": p.get("name")}
                        for p in (res.get("projects") or [])]
            self.finished.emit(self._request_id, projects)
        except Exception as exc:  # noqa: BLE001 — surface a short, non-PHI message
            self.failed.emit(self._request_id, str(exc)[:160])


class GuruListWorker(QThread):
    """Off-thread Guru target lister for the M5 publish-target picker (mirrors
    AsanaListWorker).

    Two-level: an EMPTY ``collection_id`` lists the operator's Guru COLLECTIONS;
    a non-empty ``collection_id`` lists THAT collection's FOLDERS. GuruClient does
    blocking HTTP, so it MUST NOT run inside the QWebChannel slot (that freezes the
    Qt event loop while the picker opens — invariant 6). This worker calls the live
    ``_list_guru_collections_impl`` / ``_list_guru_folders_impl`` (REUSED — not
    rebuilt) and emits ``[{id, name}]`` on ``finished`` (queued to the main thread).
    Folder rows flatten ``title`` → ``name`` so the picker has one row shape. If
    Guru creds are missing it emits ``notConnected`` so the picker can offer a
    Settings hint. Guru collection/folder names are OPERATIONAL KB metadata (the
    knowledge base's own structure), NOT patient PHI — same source-aware
    classification as the Asana boards (invariant 13).
    """

    finished = Signal(str, str, str, list)  # (request_id, level, collection_id, items [{id,name}])
    notConnected = Signal(str)              # (request_id) — Guru creds missing
    failed = Signal(str, str)              # (request_id, message — short/no PHI)

    def __init__(self, request_id: str, collection_id: str = "", parent=None):
        super().__init__(parent)
        self._request_id = request_id or ""
        self._collection_id = collection_id or ""

    def run(self):
        try:
            from src.data.chat_tools.enablement_tools import (
                _list_guru_collections_impl, _list_guru_folders_impl)
            cid = (self._collection_id or "").strip()
            if cid:
                res = _list_guru_folders_impl(None, cid) or {}
                level = "folders"
            else:
                res = _list_guru_collections_impl(None) or {}
                level = "collections"
            if not res.get("ok"):
                if res.get("error") == "guru_not_connected":
                    self.notConnected.emit(self._request_id)
                else:
                    self.failed.emit(self._request_id,
                                     str(res.get("error") or "guru_list_failed")[:160])
                return
            if level == "folders":
                # Folder rows expose ``title`` — flatten to ``name`` for one shape.
                items = [{"id": f.get("id"), "name": f.get("title") or f.get("name") or ""}
                         for f in (res.get("folders") or [])]
            else:
                # Collections carry read_only (M8) so the picker can badge
                # Guru-managed collections that can't take a published folder.
                items = [{"id": c.get("id"), "name": c.get("name") or "",
                          "read_only": bool(c.get("read_only"))}
                         for c in (res.get("collections") or [])]
            self.finished.emit(self._request_id, level, cid, items)
        except Exception as exc:  # noqa: BLE001 — surface a short, non-PHI message
            self.failed.emit(self._request_id, str(exc)[:160])


class WriteWorker(QThread):
    """Off-thread executor for a GATED, non-idempotent live write (M7b — the
    Confirm card's Confirm button). Mirrors ``AsanaListWorker``: the GuruClient /
    AsanaClient HTTP blocks, so it MUST run off the Qt thread, NEVER inside the
    QWebChannel slot.

    It dispatches on ``op`` to the JUST-VERIFIED M7a write methods:
      * create_guru_folder → GuruClient.create_folder(collection_id, title, …)
      * rename_guru_folder → GuruClient.rename_folder(folder_id, new_title)
      * create_asana_task  → AsanaClient.create_task(project_gid, name, …)
    and emits ``finished(request_id, ok, result_json)`` or
    ``failed(request_id, message)`` (queued → main thread). The single write runs
    EXACTLY ONCE per worker; the controller has already mark_resolved'd the row
    (single-winner) BEFORE spawning this, so one operator click = one live write.
    """

    finished = Signal(str, bool, str)   # (request_id, ok, result_json)
    failed = Signal(str, str)           # (request_id, message — short/no PHI)

    def __init__(self, request_id: str, op: str, params: dict, parent=None):
        super().__init__(parent)
        self._request_id = request_id or ""
        self._op = op or ""
        self._params = dict(params or {})

    def run(self):
        import json
        try:
            result = self._dispatch()
            self.finished.emit(self._request_id, bool(result.get("ok")),
                               json.dumps(result, default=str))
        except Exception as exc:  # noqa: BLE001 — surface a short, non-PHI message
            self.failed.emit(self._request_id, str(exc)[:200])

    def _dispatch(self) -> dict:
        p = self._params
        if self._op in ("create_guru_folder", "rename_guru_folder"):
            from src.data.guru_client import GuruClient
            email, token = GuruClient.load_credentials()
            if not (email and token):
                return {"ok": False, "error": "guru_not_connected"}
            client = GuruClient(email, token)
            if self._op == "create_guru_folder":
                return client.create_folder(
                    p.get("collection_id"), p.get("title"),
                    parent_folder_id=p.get("parent_folder_id") or None)
            return client.rename_folder(p.get("folder_id"), p.get("new_title"))
        if self._op == "create_asana_task":
            from src.data.asana_client import AsanaClient
            client = AsanaClient.from_store()
            if not client.api_key:
                return {"ok": False, "error": "asana_not_connected"}
            return client.create_task(
                p.get("project_gid"), p.get("name"),
                notes=p.get("notes") or None, due_on=p.get("due_on") or None)
        return {"ok": False, "error": f"unknown_op: {self._op}"}


class AgentChatController(QObject):
    """Owns the Agent's ChatEngine + warm client. Expose ``engine`` to the
    bridge and route sends through ``send`` (lazily wires the provider)."""

    # Connect-Google lifecycle state for the in-chat card (M2). The bridge
    # re-emits this to React: 'connecting' | 'connected' | 'failed'. Payload is a
    # JSON string; STATUS ONLY — never a token, email, or account name (inv. 13).
    googleAuthState = Signal(str)

    # Drive folder picker round-trip (M3). The bridge re-emits both to React.
    #   driveFoldersListed: JSON {request_id, parent_id?, folders:[{id,name,driveId}]}
    #     or {request_id, needs_connect:True} when Google isn't active this session.
    #   actionResolved: JSON {request_id} once an action is resolved → React closes
    #     the picker.
    # driveFoldersListed carries folder NAMES across the bridge for the React
    # confirmation ONLY (in-process, never to the LLM — invariant 6/13).
    driveFoldersListed = Signal(str)
    actionResolved = Signal(str)

    # Asana board picker round-trip (M4). The bridge re-emits this to React.
    #   asanaProjectsListed: JSON {request_id, projects:[{gid,name}]}
    #     or {request_id, asana_not_connected:True} when no shared PAT is set.
    # Board NAMES are OPERATIONAL metadata (project trackers/roadmaps), NOT
    # patient PHI like Drive folder names — so unlike driveFoldersListed they can
    # also flow into the resolve notify (see resolve_asana_board). They still load
    # in-process into React for the picker confirmation.
    asanaProjectsListed = Signal(str)

    # Guru publish-target picker round-trip (M5). The bridge re-emits this to React.
    #   guruTargetsListed: JSON {request_id, level:'collections'|'folders',
    #     collection_id?, items:[{id,name}]} | {request_id, guru_not_connected:True}.
    # Guru collection/folder names are OPERATIONAL KB metadata (the knowledge base's
    # own structure), NOT patient PHI like Drive folder names — so like the Asana
    # boards they can also flow into the resolve notify (see resolve_guru_target).
    # They still load in-process into React for the picker confirmation.
    guruTargetsListed = Signal(str)

    def __init__(self, db=None, demo: bool = False, parent=None):
        super().__init__(parent)
        self.db = db
        self.demo = demo
        self._engine = None
        self._warm_bridge = None
        self._claude_client = None
        self._session_id = None
        self._voice = None
        # M2 connect-Google single-flight guard. One worker per real click; the
        # QWebChannel is the trust boundary (any webview script can call
        # connectGoogle), so this controller-side latch is the enforcement that a
        # double/forged invoke spins up exactly ONE OAuth flow (invariant 16).
        self._google_connect_pending = False
        self._google_worker = None   # stored on self — a GC'd QThread mid-run crashes (inv. 9)
        # M3 Drive-list worker, stored on self for the same no-GC reason (inv. 6/9).
        # list_drive_folders runs the Drive HTTP off-thread; the worker lives here
        # until its finished slot fires on the main thread.
        self._drive_list_worker = None
        # M4 Asana-list worker, stored on self for the same no-GC reason (inv. 6/9).
        # list_asana_projects_for_picker runs the Asana HTTP off-thread; the worker
        # lives here until its finished slot fires on the main thread.
        self._asana_list_worker = None
        # M5 Guru-list worker, stored on self for the same no-GC reason (inv. 6/9).
        # list_guru_targets runs the Guru HTTP off-thread; the worker lives here
        # until its finished slot fires on the main thread.
        self._guru_list_worker = None
        # M7b gated-write worker, stored on self for the same no-GC reason
        # (inv. 6/9). execute_write spawns it AFTER mark_resolved (single-winner)
        # so one operator Confirm click runs the live write EXACTLY ONCE; it lives
        # here until its finished/failed slot fires on the main thread.
        self._write_worker = None
        # M0 busy-queue: triggers that must run as a normal Renn turn but arrived
        # while the engine was mid-turn. ``enqueue_trigger`` sends immediately when
        # idle, else appends here; one is drained on each ``busy_changed(False)``.
        # (Infra for the M3 picker→resolve path; landed + unit-tested now.)
        self._pending_triggers: list[str] = []
        self._setup_engine()
        self._setup_voice()

    @property
    def engine(self):
        return self._engine

    @property
    def voice(self):
        return self._voice

    def _setup_voice(self):
        try:
            from src.services.voice import VoiceController
            self._voice = VoiceController(parent=self)
        except Exception as exc:  # noqa: BLE001 — dictation is optional; chat still works
            logger.debug("voice controller unavailable: %s", exc)
            self._voice = None

    def send(self, text: str):
        """Ensure a session + provider, then send. The ACP bridge boots lazily
        on the first send, so the provider is wired here, not at construction."""
        if self._engine is None:
            return
        self._ensure_session()
        self._persist_message("user", text or "")   # so history has the transcript + a title
        self._prepare_provider()
        self._engine.send(text or "")

    def _persist_turn(self, role, content, telemetry):
        """Telemetry callback (the assistant turn) → persist with model/tokens."""
        tel = telemetry if isinstance(telemetry, dict) else {}
        self._persist_message(
            role, content or "",
            model_used=tel.get("model_used"),
            tokens_in=tel.get("tokens_in"), tokens_out=tel.get("tokens_out"),
            cost_usd=tel.get("cost_usd"), latency_ms=tel.get("latency_ms"))

    def _persist_message(self, role, content, **kw):
        """Append one message row to chat_messages in the session's DB (best-effort)."""
        if not self._session_id:
            return
        conn = self._open_conn(readonly=False)
        if conn is None:
            return
        try:
            from src.services.chat_session import append_message
            append_message(self._session_id, role, content, conn=conn, **kw)
        except Exception as exc:  # noqa: BLE001 — telemetry only; the chat still works
            logger.debug("agent message persist failed: %s", exc)
        finally:
            conn.close()

    def shutdown(self):
        self._teardown_warm_bridge()
        self._teardown_claude_client()

    def recent_tool_calls(self, since_id: int = 0) -> list[dict]:
        """Newly-finished tool executions for this session (the tool-call
        timeline). Reads the same DB the MCP server writes to (``db_path``), so
        it sees tools as the subprocess records them. Best-effort."""
        db_path = self._db_path()
        if not self._session_id or not db_path:
            return []
        try:
            from src.data.connection_factory import get_connection
            conn = get_connection(db_path, readonly=True)
            try:
                rows = conn.execute(
                    "SELECT rowid, tool_name, result_rows, elapsed_ms, error "
                    "FROM chat_tool_executions WHERE session_id=? AND rowid>? "
                    "ORDER BY rowid", (self._session_id, int(since_id))).fetchall()
            finally:
                conn.close()
            return [{"id": r[0], "name": r[1], "rows": r[2],
                     "ms": round(r[3] or 0), "ok": not r[4], "error": r[4]}
                    for r in rows]
        except Exception:  # noqa: BLE001 — the timeline is best-effort, never fatal
            return []

    def recent_jobs(self) -> list[dict]:
        """Current jobs for this session (the sidebar tracker, M4). Reads the
        same DB the MCP server writes jobs to — best-effort, session-scoped."""
        if not self._session_id:
            return []
        conn = self._open_conn(readonly=True)
        if conn is None:
            return []
        try:
            from src.data.agent_jobs import list_jobs
            return list_jobs(conn, session_id=self._session_id, limit=50)
        except Exception:  # noqa: BLE001 — the tracker is best-effort, never fatal
            return []
        finally:
            conn.close()

    # ── action channel (M0) ─────────────────────────────────────────

    def poll_action_requests(self, session_id: str | None = None) -> list[dict]:
        """The ``action_api``: atomically claim this session's unconsumed action
        requests (picker/connect asks Renn minted in the MCP subprocess) and
        return them. Opens its OWN ``get_connection`` (read-write, since the claim
        UPDATEs) on ``_db_path`` — the same DB the resolver tools write to — so it
        sees rows across the subprocess boundary. Best-effort, session-scoped.

        Defaults to the active session so the bridge's free-running timer can call
        it with no args; an explicit ``session_id`` is accepted for tests.
        """
        sid = session_id or self._session_id
        if not sid:
            return []
        conn = self._open_conn(readonly=False)
        if conn is None:
            return []
        try:
            from src.data.chat_action_requests import claim_pending_actions
            return claim_pending_actions(conn, sid)
        except Exception:  # noqa: BLE001 — the action channel is best-effort
            return []
        finally:
            conn.close()

    def enqueue_trigger(self, text: str) -> None:
        """Run ``text`` as a normal Renn turn — now if idle, else queued.

        The picker→resolve path (M3+) calls this to push a ``[SYSTEM: operator
        selected …]`` follow-up after persisting the pick. ``ChatEngine.send``
        silently early-returns while busy, so a naive send during an in-flight
        turn would dead-end the chat; we queue instead and drain one on the next
        ``busy_changed(False)`` (invariant 7).
        """
        if self._engine is None:
            return
        if self._engine.is_busy:
            self._pending_triggers.append(text or "")
            return
        self.send(text or "")

    def _on_busy_changed(self, busy: bool) -> None:
        """Drain ONE queued trigger when the engine goes idle. One-at-a-time so
        each follow-up runs as its own turn (and re-queues correctly if another
        arrives mid-turn)."""
        if busy or not self._pending_triggers:
            return
        text = self._pending_triggers.pop(0)
        self.send(text)

    # ── Drive folder picker round-trip (M3) ─────────────────────────

    def list_drive_folders(self, parent_id: str = "", request_id: str = "") -> None:
        """The ``picker_api``: list the Drive folders under ``parent_id`` for the
        lazy tree, OFF the main thread.

        MUST NOT call DriveReader synchronously here — that would freeze the Qt
        event loop for the duration of the Drive HTTP (invariant 6). Instead:
          1. If Google isn't active this session, emit
             ``{request_id, needs_connect:True}`` and return — NO Drive HTTP, no
             ``_build_service`` (disable-on-launch; the picker offers Connect).
          2. Else spawn a short-lived ``DriveListWorker`` (stored on self so a
             mid-run QThread isn't GC'd) whose ``finished`` slot (queued → main
             thread) emits ``driveFoldersListed`` with id+name+driveId only.
        """
        rid = request_id or ""
        if not self._google_is_active():
            self._emit_drive_folders({"request_id": rid, "needs_connect": True})
            return
        try:
            worker = DriveListWorker(rid, parent_id or "", parent=self)
            worker.finished.connect(self._on_drive_folders_listed,
                                    Qt.ConnectionType.QueuedConnection)
            worker.failed.connect(self._on_drive_list_failed,
                                  Qt.ConnectionType.QueuedConnection)
            self._drive_list_worker = worker
            worker.start()
        except Exception as exc:  # noqa: BLE001 — never crash the chat on a wiring fault
            logger.warning("Drive list worker failed to start: %s", exc)
            self._drive_list_worker = None
            self._emit_drive_folders({"request_id": rid, "folders": [],
                                      "error": "drive_list_failed"})

    def _google_is_active(self) -> bool:
        """Whether Drive read is active this session (disable-on-launch gate).

        Reads ``google_oauth.is_active()`` WITHOUT building a Drive service or
        loading credentials — a cheap session-state check, no Google HTTP."""
        try:
            from src.data import google_oauth
            return bool(google_oauth.is_active())
        except Exception:  # noqa: BLE001 — treat any failure as "not active"
            return False

    @Slot(str, str, list)
    def _on_drive_folders_listed(self, request_id, parent_id, folders) -> None:
        """Main-thread: relay the worker's folder rows out as driveFoldersListed."""
        self._drive_list_worker = None
        self._emit_drive_folders({"request_id": request_id or "",
                                  "parent_id": parent_id or "",
                                  "folders": list(folders or [])})

    @Slot(str, str)
    def _on_drive_list_failed(self, request_id, msg) -> None:
        """Main-thread: surface a Drive-list failure (no PHI; short message)."""
        self._drive_list_worker = None
        self._emit_drive_folders({"request_id": request_id or "",
                                  "folders": [], "error": msg or "drive_list_failed"})

    def _emit_drive_folders(self, payload: dict) -> None:
        import json
        try:
            self.driveFoldersListed.emit(json.dumps(payload, default=str))
        except Exception:  # noqa: BLE001 — best-effort
            pass

    def resolve_drive_folder(self, request_id: str, folder_id: str,
                             folder_name: str = "", drive_id: str = "") -> dict:
        """Commit the operator's Drive folder pick (MAIN thread), in strict order:

          (a) VALIDATE — the request_id row must exist, be a drive_folder_picker,
              AND belong to the CURRENT session (session-switch guard, invariant 8).
          (b) PERSIST FIRST — write ``enablement.drive.active_folders`` via
              ``_set_drive_folder_impl`` (settings is the source of truth — inv. 7).
              On failure, return early WITHOUT resolving so the operator can retry;
              the human-gate stays closed throughout (no TOCTOU window).
          (c) RESOLVE single-winner — ``mark_resolved`` must win ONLY after the
              choice is durable; a double-pick is rejected (already resolved).
          (d) NOTIFY id-only — append a ``[SYSTEM]`` line + enqueue the follow-up
              trigger carrying ONLY the folder_id; the NAME/drive_id never enter the
              transcript or the next prompt (PHI — invariants 7, 13). Busy-queued.
          (e) EMIT actionResolved → React closes the picker.
        """
        rid = (request_id or "").strip()
        fid = (folder_id or "").strip()
        if not rid or not fid:
            return {"ok": False, "error": "request_id_and_folder_id_required"}
        conn = self._open_conn(readonly=False)
        if conn is None:
            return {"ok": False, "error": "no_db"}
        try:
            from src.data.chat_action_requests import get_action_request, mark_resolved
            # (a) validate row / type / session ownership (invariant 8).
            row = get_action_request(conn, rid)
            if row is None or row.get("type") != "drive_folder_picker":
                return {"ok": False, "error": "unknown_request"}
            if row.get("session_id") != self._session_id:
                return {"ok": False, "error": "session_mismatch"}
            # (b) PERSIST FIRST (invariant 7) — settings is the durable source of
            #     truth. The human-gate (has_pending_action -> resolved=0) stays
            #     CLOSED throughout this write, so a concurrent set_drive_folder
            #     cannot slip a guessed id through the window. If persist fails the
            #     row is left resolved=0 so the operator can retry (no ghost).
            from src.data.chat_tools.enablement_tools import _set_drive_folder_impl
            persisted = _set_drive_folder_impl(conn, fid, folder_name or None,
                                               drive_id or None)
            if not persisted.get("ok"):
                return {"ok": False,
                        "error": persisted.get("error", "persist_failed")}
            # (c) only now mark resolved — single-winner dedupe + closes the gate,
            #     AFTER the choice is durable.
            if not mark_resolved(conn, rid):
                return {"ok": False, "error": "already_resolved"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            conn.close()
        # (d) NOTIFY id-only — the name/drive_id MUST NOT appear (invariant 13).
        self._notify_renn(
            f"[SYSTEM: operator selected the active Drive folder (id {fid}); "
            "it is saved.]")
        self.enqueue_trigger("Confirm the active folder is set and ask what to do next.")
        # (e) tell React to close the picker.
        self._emit_action_resolved(rid)
        return {"ok": bool(persisted.get("ok")), "request_id": rid, "folder_id": fid}

    def _emit_action_resolved(self, request_id: str) -> None:
        import json
        try:
            self.actionResolved.emit(json.dumps({"request_id": request_id or ""}))
        except Exception:  # noqa: BLE001 — best-effort
            pass

    # ── Asana board picker round-trip (M4) ──────────────────────────

    def list_asana_projects_for_picker(self, request_id: str = "") -> None:
        """The ``picker_api`` for Asana: list the projects/boards the shared PAT
        can see, OFF the main thread (mirrors ``list_drive_folders``).

        MUST NOT call AsanaClient synchronously here — even though it's stdlib
        urllib, the HTTP blocks and would freeze the Qt event loop while the picker
        opens (invariant 6). Instead spawn a short-lived ``AsanaListWorker`` (stored
        on self so a mid-run QThread isn't GC'd) whose ``finished`` slot (queued →
        main thread) emits ``asanaProjectsListed`` with gid+name only. No Drive-
        style is_active() gate: Asana auth is the shared PAT (no per-session OAuth),
        so the worker itself reports ``asana_not_connected`` when no PAT is set.
        """
        rid = request_id or ""
        try:
            worker = AsanaListWorker(rid, parent=self)
            worker.finished.connect(self._on_asana_projects_listed,
                                    Qt.ConnectionType.QueuedConnection)
            worker.notConnected.connect(self._on_asana_not_connected,
                                        Qt.ConnectionType.QueuedConnection)
            worker.failed.connect(self._on_asana_list_failed,
                                  Qt.ConnectionType.QueuedConnection)
            self._asana_list_worker = worker
            worker.start()
        except Exception as exc:  # noqa: BLE001 — never crash the chat on a wiring fault
            logger.warning("Asana list worker failed to start: %s", exc)
            self._asana_list_worker = None
            self._emit_asana_projects({"request_id": rid, "projects": [],
                                       "error": "asana_list_failed"})

    @Slot(str, list)
    def _on_asana_projects_listed(self, request_id, projects) -> None:
        """Main-thread: relay the worker's project rows out as asanaProjectsListed."""
        self._asana_list_worker = None
        self._emit_asana_projects({"request_id": request_id or "",
                                   "projects": list(projects or [])})

    @Slot(str)
    def _on_asana_not_connected(self, request_id) -> None:
        """Main-thread: the shared PAT isn't set — let the picker offer a Settings hint."""
        self._asana_list_worker = None
        self._emit_asana_projects({"request_id": request_id or "",
                                   "asana_not_connected": True})

    @Slot(str, str)
    def _on_asana_list_failed(self, request_id, msg) -> None:
        """Main-thread: surface an Asana-list failure (no PHI; short message)."""
        self._asana_list_worker = None
        self._emit_asana_projects({"request_id": request_id or "",
                                   "projects": [], "error": msg or "asana_list_failed"})

    def _emit_asana_projects(self, payload: dict) -> None:
        import json
        try:
            self.asanaProjectsListed.emit(json.dumps(payload, default=str))
        except Exception:  # noqa: BLE001 — best-effort
            pass

    def resolve_asana_board(self, request_id: str, project_gid: str,
                            project_name: str = "") -> dict:
        """Commit the operator's Asana board pick (MAIN thread), in the SAME strict
        order as ``resolve_drive_folder`` (the M3 template, post-fix):

          (a) VALIDATE — the request_id row must exist, be an asana_board_picker,
              AND belong to the CURRENT session (session-switch guard, invariant 8).
          (b) PERSIST FIRST — write ``enablement.asana.active_board`` via
              ``_set_asana_board_impl`` (settings is the source of truth — inv. 7).
              On failure, return early WITHOUT mark_resolved so the operator can
              retry; the human-gate stays closed throughout (no TOCTOU window /
              data loss). This deliberate ordering is M3's just-fixed bug fix —
              DO NOT mark_resolved before the persist succeeds.
          (c) RESOLVE single-winner — ``mark_resolved`` wins ONLY after the choice
              is durable; a double-pick is rejected (already resolved).
          (d) NOTIFY + enqueue the follow-up trigger.
          (e) EMIT actionResolved → React closes the picker.

        NOTE ON REDACTION (invariant 13): unlike ``resolve_drive_folder``, the
        [SYSTEM] notify here MAY include the ``project_name``. Asana board names are
        OPERATIONAL metadata (project trackers / roadmaps), NOT patient PHI like
        Drive/Shared-Drive folder names (which can be case/patient names). Including
        the name gives Renn a natural confirmation. This is a deliberate distinction
        from the Drive path — the board gid is still the load-bearing identifier.
        """
        rid = (request_id or "").strip()
        gid = (project_gid or "").strip()
        if not rid or not gid:
            return {"ok": False, "error": "request_id_and_project_gid_required"}
        conn = self._open_conn(readonly=False)
        if conn is None:
            return {"ok": False, "error": "no_db"}
        try:
            from src.data.chat_action_requests import get_action_request, mark_resolved
            # (a) validate row / type / session ownership (invariant 8).
            row = get_action_request(conn, rid)
            if row is None or row.get("type") != "asana_board_picker":
                return {"ok": False, "error": "unknown_request"}
            if row.get("session_id") != self._session_id:
                return {"ok": False, "error": "session_mismatch"}
            # (b) PERSIST FIRST (invariant 7) — settings is the durable source of
            #     truth. The human-gate (has_pending_action -> resolved=0) stays
            #     CLOSED throughout this write. If persist fails the row is left
            #     resolved=0 so the operator can retry (no ghost / no data loss).
            from src.data.chat_tools.enablement_tools import _set_asana_board_impl
            persisted = _set_asana_board_impl(conn, gid, project_name or None)
            if not persisted.get("ok"):
                return {"ok": False,
                        "error": persisted.get("error", "persist_failed")}
            # (c) only now mark resolved — single-winner dedupe + closes the gate,
            #     AFTER the choice is durable.
            if not mark_resolved(conn, rid):
                return {"ok": False, "error": "already_resolved"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            conn.close()
        # (d) NOTIFY — the board NAME is operational metadata (not PHI), so we may
        #     include it for a natural confirmation (see the redaction note above).
        name = (project_name or "").strip()
        if name:
            self._notify_renn(
                f'[SYSTEM: operator set the active Asana board to "{name}" '
                f"(gid {gid}); it is saved.]")
        else:
            self._notify_renn(
                f"[SYSTEM: operator set the active Asana board (gid {gid}); "
                "it is saved.]")
        self.enqueue_trigger(
            "Confirm the active Asana board is set, then list its tasks with "
            "list_asana_tasks and report what's on the board.")
        # (e) tell React to close the picker.
        self._emit_action_resolved(rid)
        return {"ok": bool(persisted.get("ok")), "request_id": rid, "project_gid": gid}

    # ── Guru publish-target picker round-trip (M5) ──────────────────

    def list_guru_targets(self, collection_id: str = "", request_id: str = "") -> None:
        """The ``picker_api`` for Guru: list the operator's publish targets, OFF the
        main thread (mirrors ``list_asana_projects_for_picker``).

        Two-level — an EMPTY ``collection_id`` lists the COLLECTIONS; a non-empty
        one lists THAT collection's FOLDERS (so the React drawer drills in). MUST NOT
        call GuruClient synchronously here — its HTTP blocks and would freeze the Qt
        event loop while the picker opens (invariant 6). Instead spawn a short-lived
        ``GuruListWorker`` (stored on self so a mid-run QThread isn't GC'd) whose
        ``finished`` slot (queued → main thread) emits ``guruTargetsListed`` with
        id+name only. No is_active() gate: Guru auth is shared creds (no per-session
        OAuth), so the worker itself reports ``guru_not_connected`` when creds are
        missing.
        """
        rid = request_id or ""
        cid = collection_id or ""
        try:
            worker = GuruListWorker(rid, cid, parent=self)
            worker.finished.connect(self._on_guru_targets_listed,
                                    Qt.ConnectionType.QueuedConnection)
            worker.notConnected.connect(self._on_guru_not_connected,
                                        Qt.ConnectionType.QueuedConnection)
            worker.failed.connect(self._on_guru_list_failed,
                                  Qt.ConnectionType.QueuedConnection)
            self._guru_list_worker = worker
            worker.start()
        except Exception as exc:  # noqa: BLE001 — never crash the chat on a wiring fault
            logger.warning("Guru list worker failed to start: %s", exc)
            self._guru_list_worker = None
            self._emit_guru_targets({"request_id": rid, "items": [],
                                     "error": "guru_list_failed"})

    @Slot(str, str, str, list)
    def _on_guru_targets_listed(self, request_id, level, collection_id, items) -> None:
        """Main-thread: relay the worker's target rows out as guruTargetsListed."""
        self._guru_list_worker = None
        payload = {"request_id": request_id or "", "level": level or "collections",
                   "items": list(items or [])}
        if collection_id:
            payload["collection_id"] = collection_id
        self._emit_guru_targets(payload)

    @Slot(str)
    def _on_guru_not_connected(self, request_id) -> None:
        """Main-thread: Guru creds aren't set — let the picker offer a Settings hint."""
        self._guru_list_worker = None
        self._emit_guru_targets({"request_id": request_id or "",
                                 "guru_not_connected": True})

    @Slot(str, str)
    def _on_guru_list_failed(self, request_id, msg) -> None:
        """Main-thread: surface a Guru-list failure (no PHI; short message)."""
        self._guru_list_worker = None
        self._emit_guru_targets({"request_id": request_id or "",
                                 "items": [], "error": msg or "guru_list_failed"})

    def _emit_guru_targets(self, payload: dict) -> None:
        import json
        try:
            self.guruTargetsListed.emit(json.dumps(payload, default=str))
        except Exception:  # noqa: BLE001 — best-effort
            pass

    def resolve_guru_target(self, request_id: str, collection_id: str,
                            folder_id: str = "") -> dict:
        """Commit the operator's Guru publish-target pick (MAIN thread), in the SAME
        strict order as ``resolve_asana_board`` / ``resolve_drive_folder`` (the M3
        template, post-fix):

          (a) VALIDATE — the request_id row must exist, be a guru_publish_picker,
              AND belong to the CURRENT session (session-switch guard, invariant 8).
          (b) PERSIST FIRST — write ``enablement.guru.publish_collection_id`` (+
              optional ``publish_folder_id``) via ``_set_guru_publish_target_impl``
              (settings is the source of truth — inv. 7; these are the exact ids
              ``_push_guru_draft_impl`` already reads as its fallback target). On
              failure, return early WITHOUT mark_resolved so the operator can retry;
              the human-gate stays closed throughout (no TOCTOU window / data loss).
              This deliberate ordering is M3's just-fixed bug fix — DO NOT
              mark_resolved before the persist succeeds.
          (c) RESOLVE single-winner — ``mark_resolved`` wins ONLY after the choice
              is durable; a double-pick is rejected (already resolved).
          (d) NOTIFY + enqueue the follow-up trigger.
          (e) EMIT actionResolved → React closes the picker.

        NOTE ON REDACTION (invariant 13, source-aware): unlike
        ``resolve_drive_folder``, the [SYSTEM] notify here MAY include the collection/
        folder ids. Guru collection/folder NAMES are OPERATIONAL KB metadata (the
        knowledge base's own structure), NOT patient PHI like Drive/Shared-Drive
        folder names (which can mirror case/patient files). Same source-aware
        distinction the Asana board path makes — the enablement lane is decoupled
        from the PHI ticket warehouse, so Guru KB structure carries no patient
        records. We notify with the ids (the load-bearing identifiers
        ``_push_guru_draft_impl`` consumes); the picker already showed the names.
        """
        rid = (request_id or "").strip()
        cid = (collection_id or "").strip()
        fid = (folder_id or "").strip()
        if not rid or not cid:
            return {"ok": False, "error": "request_id_and_collection_id_required"}
        conn = self._open_conn(readonly=False)
        if conn is None:
            return {"ok": False, "error": "no_db"}
        try:
            from src.data.chat_action_requests import get_action_request, mark_resolved
            # (a) validate row / type / session ownership (invariant 8).
            row = get_action_request(conn, rid)
            if row is None or row.get("type") != "guru_publish_picker":
                return {"ok": False, "error": "unknown_request"}
            if row.get("session_id") != self._session_id:
                return {"ok": False, "error": "session_mismatch"}
            # (b) PERSIST FIRST (invariant 7) — settings is the durable source of
            #     truth. The human-gate (has_pending_action -> resolved=0) stays
            #     CLOSED throughout this write. If persist fails the row is left
            #     resolved=0 so the operator can retry (no ghost / no data loss).
            from src.data.chat_tools.enablement_tools import _set_guru_publish_target_impl
            persisted = _set_guru_publish_target_impl(conn, cid, fid or None)
            if not persisted.get("ok"):
                return {"ok": False,
                        "error": persisted.get("error", "persist_failed")}
            # (c) only now mark resolved — single-winner dedupe + closes the gate,
            #     AFTER the choice is durable.
            if not mark_resolved(conn, rid):
                return {"ok": False, "error": "already_resolved"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            conn.close()
        # (d) NOTIFY — the collection/folder ids are operational KB metadata (not
        #     PHI), so we include them for a natural confirmation (see the redaction
        #     note above). _push_guru_draft_impl reads exactly these ids.
        if fid:
            self._notify_renn(
                f"[SYSTEM: operator set the Guru publish target to collection "
                f"{cid}, folder {fid}; it is saved.]")
        else:
            self._notify_renn(
                f"[SYSTEM: operator set the Guru publish target to collection "
                f"{cid} (collection level); it is saved.]")
        self.enqueue_trigger(
            "Confirm the Guru publish target is set and ask what to publish next.")
        # (e) tell React to close the picker.
        self._emit_action_resolved(rid)
        return {"ok": bool(persisted.get("ok")), "request_id": rid,
                "collection_id": cid, "folder_id": fid or None}

    # ── gated WRITE channel (M7b) — Confirm/Cancel a non-idempotent write ──

    def execute_write(self, request_id: str) -> dict:
        """Commit a confirm_write the operator clicked Confirm on (MAIN thread).

        ORDER IS THE INVERSE OF THE PICKERS' resolve_*, AND DELIBERATELY SO. A
        picker's resolve persists-to-settings FIRST then mark_resolved, because a
        re-pick of the same folder/board is idempotent — re-writing the setting is
        harmless, so it's safe to do the durable write before claiming the row.
        A confirm_write is the opposite: the write is a NON-IDEMPOTENT live API
        call (creating a Guru folder twice = two folders; creating an Asana task
        twice = two tasks). So we mark_resolved FIRST — that single ``UPDATE …
        WHERE resolved=0`` is the single-winner CLAIM. Only the call that wins the
        claim spawns the worker; a double-confirm (double click / replayed bridge
        call) loses the claim (already_resolved) and NEVER reaches the write. One
        click = one write.

        Steps:
          (a) VALIDATE — the row exists, is a confirm_write, and belongs to the
              CURRENT session (session-switch guard, invariant 8).
          (b) CLAIM FIRST — ``mark_resolved`` (single-winner). If it loses, return
              already_resolved and do NOT execute (the inverse-of-the-picker order
              that makes the non-idempotent write fire exactly once).
          (c) read op + params from the row payload.
          (d) spawn the off-thread WriteWorker; its finished/failed slot notifies
              Renn with the operational result + emits actionResolved.
        """
        rid = (request_id or "").strip()
        if not rid:
            return {"ok": False, "error": "request_id_required"}
        conn = self._open_conn(readonly=False)
        if conn is None:
            return {"ok": False, "error": "no_db"}
        try:
            import json
            from src.data.chat_action_requests import get_action_request, mark_resolved
            # (a) validate row / type / session ownership (invariant 8).
            row = get_action_request(conn, rid)
            if row is None or row.get("type") != "confirm_write":
                return {"ok": False, "error": "unknown_request"}
            if row.get("session_id") != self._session_id:
                return {"ok": False, "error": "session_mismatch"}
            # (b) CLAIM FIRST (single-winner) — BEFORE the non-idempotent write.
            #     A double-confirm loses here and never executes (one click = one
            #     write). This is the inverse of the pickers' persist-first order,
            #     on purpose: the write below cannot be undone or de-duped after the
            #     fact, so the claim must gate it.
            if not mark_resolved(conn, rid):
                return {"ok": False, "error": "already_resolved"}
            # (c) read the op + params from the row payload (set by the propose tool).
            try:
                payload = json.loads(row.get("payload_json") or "{}")
            except (ValueError, TypeError):
                payload = {}
            op = payload.get("op") or ""
            params = payload.get("params") or {}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            conn.close()
        # (d) spawn the off-thread write (the worker is stored on self — a GC'd
        #     QThread mid-run crashes). The finished/failed slot does the notify +
        #     actionResolved on the main thread.
        try:
            worker = WriteWorker(rid, op, params, parent=self)
            worker.finished.connect(self._on_write_finished,
                                    Qt.ConnectionType.QueuedConnection)
            worker.failed.connect(self._on_write_failed,
                                  Qt.ConnectionType.QueuedConnection)
            self._write_worker = worker
            worker.start()
        except Exception as exc:  # noqa: BLE001 — never crash the chat on a wiring fault
            logger.warning("Write worker failed to start: %s", exc)
            self._write_worker = None
            self._notify_renn(
                f"[SYSTEM: the {op or 'write'} could not be started: {str(exc)[:120]}.]")
            self._emit_action_resolved_payload({"request_id": rid, "ok": False})
            return {"ok": False, "error": "write_start_failed", "request_id": rid}
        return {"ok": True, "request_id": rid, "op": op, "started": True}

    @Slot(str, bool, str)
    def _on_write_finished(self, request_id, ok, result_json) -> None:
        """Main-thread: a gated write completed. Notify Renn with the OPERATIONAL
        result (new folder id / task permalink+name / 'renamed'), enqueue any
        follow-up trigger, and tell React to close the card."""
        import json
        self._write_worker = None
        try:
            result = json.loads(result_json or "{}")
        except (ValueError, TypeError):
            result = {}
        op = self._notify_write_result(bool(ok), result)
        # For a new Guru folder, invite Renn to offer setting it as the publish
        # target (so the next push lands there) — the natural next step.
        if ok and op == "create_guru_folder" and result.get("id"):
            self.enqueue_trigger(
                "The new Guru folder was created. Offer to set it as the publish "
                "target with set_guru_publish_target (use the new folder id), then "
                "ask what to do next.")
        self._emit_action_resolved_payload({"request_id": request_id or "", "ok": bool(ok)})

    @Slot(str, str)
    def _on_write_failed(self, request_id, msg) -> None:
        """Main-thread: a gated write raised. Notify Renn the error + close the card."""
        self._write_worker = None
        self._notify_renn(f"[SYSTEM: the write failed: {msg or 'unknown error'}.]")
        self._emit_action_resolved_payload({"request_id": request_id or "", "ok": False})

    def _notify_write_result(self, ok: bool, result: dict) -> str:
        """Inject a [SYSTEM] line describing the operational write result. Returns
        the op so the caller can chain a follow-up. Operational ids/names only
        (KB/board metadata, not PHI — invariant 13, source-aware)."""
        # The worker's result dicts don't carry ``op``; infer it from the shape so
        # the notify reads naturally. (create_folder/rename_folder both carry
        # ok+id+title; create_task carries ok+gid+name+permalink_url.)
        if result.get("error") == "guru_not_connected":
            self._notify_renn("[SYSTEM: the write failed — Guru is not connected. "
                              "Ask the operator to connect Guru in Settings.]")
            return ""
        if result.get("error") == "asana_not_connected":
            self._notify_renn("[SYSTEM: the write failed — Asana is not connected. "
                              "Ask the operator to add a shared Asana token in Settings.]")
            return ""
        if "permalink_url" in result or "gid" in result:   # Asana task create
            if ok:
                self._notify_renn(
                    f'[SYSTEM: operator confirmed — created the Asana task '
                    f'"{result.get("name") or ""}" '
                    f'({result.get("permalink_url") or result.get("gid") or ""}).]')
            else:
                self._notify_renn("[SYSTEM: the Asana task could not be created.]")
            return "create_asana_task"
        # Guru folder create vs rename: a create resolves a *new* id; a rename
        # echoes the same folder id back. We can't perfectly distinguish from the
        # result alone, so report the durable facts (id + title).
        if ok:
            self._notify_renn(
                f'[SYSTEM: operator confirmed — the Guru folder "{result.get("title") or ""}" '
                f'is saved (id {result.get("id") or "unknown"}).]')
            return "create_guru_folder" if result.get("id") else ""
        self._notify_renn("[SYSTEM: the Guru folder write could not be completed.]")
        return ""

    def cancel_write(self, request_id: str) -> dict:
        """The operator clicked Cancel on a Confirm card (MAIN thread).

        Validate session ownership, mark_resolved (so the human-gate re-opens and
        the row can't be re-confirmed), notify Renn it was cancelled, and tell
        React to close the card. NO write ever runs.
        """
        rid = (request_id or "").strip()
        if not rid:
            return {"ok": False, "error": "request_id_required"}
        conn = self._open_conn(readonly=False)
        if conn is None:
            return {"ok": False, "error": "no_db"}
        op = ""
        try:
            import json
            from src.data.chat_action_requests import get_action_request, mark_resolved
            row = get_action_request(conn, rid)
            if row is None or row.get("type") != "confirm_write":
                return {"ok": False, "error": "unknown_request"}
            if row.get("session_id") != self._session_id:
                return {"ok": False, "error": "session_mismatch"}
            try:
                op = (json.loads(row.get("payload_json") or "{}") or {}).get("op") or ""
            except (ValueError, TypeError):
                op = ""
            if not mark_resolved(conn, rid):
                return {"ok": False, "error": "already_resolved"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            conn.close()
        self._notify_renn(f"[SYSTEM: operator cancelled the {op or 'write'}.]")
        self._emit_action_resolved_payload({"request_id": rid, "cancelled": True})
        return {"ok": True, "request_id": rid, "cancelled": True}

    def _emit_action_resolved_payload(self, payload: dict) -> None:
        """Emit actionResolved carrying a richer payload (write outcome) than the
        bare {request_id} the pickers emit — React shows a one-line outcome then
        closes the matching confirm card."""
        import json
        try:
            self.actionResolved.emit(json.dumps(payload, default=str))
        except Exception:  # noqa: BLE001 — best-effort
            pass

    # ── connect Google in chat (M2) ─────────────────────────────────

    def start_google_connect(self) -> None:
        """Begin the in-chat Google OAuth flow (the ConnectGoogleCard button).

        Single-flight (invariant 16): a second call while a flow is in flight is
        ignored, so a forged/double QWebChannel invoke spins up exactly one
        worker. The ``GoogleOAuthWorker`` runs the BLOCKING loopback PKCE flow off
        the UI thread; it is stored on ``self`` so a mid-run QThread isn't GC'd
        and crashed (invariant 9). ``finished``/``error`` are delivered back on
        the MAIN thread (queued connection) where we persist + reconnect + notify.

        Disable-on-launch (invariant 9): there is NO import-time/boot reconnect
        anywhere — ``_active`` is only ever set by the finished handler below,
        i.e. by THIS explicit click.
        """
        if self._google_connect_pending:
            return   # single-flight: one OAuth flow per click
        self._google_connect_pending = True
        self._emit_google_state("connecting")
        try:
            from src.ui.widgets.google_oauth_worker import GoogleOAuthWorker
            worker = GoogleOAuthWorker(mode="connect", parent=self)
            # Queued so the slot bodies run on the main thread (the worker emits
            # from its own QThread); we touch settings/keyring + the engine there.
            worker.finished.connect(self._on_google_connect_finished,
                                    Qt.ConnectionType.QueuedConnection)
            worker.error.connect(self._on_google_connect_error,
                                 Qt.ConnectionType.QueuedConnection)
            self._google_worker = worker
            worker.start()
        except Exception as exc:  # noqa: BLE001 — never crash the chat on a wiring fault
            logger.warning("Google connect worker failed to start: %s", exc)
            self._google_connect_pending = False
            self._google_worker = None
            self._emit_google_state("failed")

    @Slot(dict)
    def _on_google_connect_finished(self, record: dict) -> None:
        """Main-thread: persist the record, activate it THIS session, notify Renn.

        Order: store_credentials (keyring + auth_type=oauth_user) → reconnect
        (sets ``_active`` for this session only) → STATUS-ONLY notify. The
        ``[SYSTEM]`` injection carries NO token, email, or account name
        (invariant 13) — only that Drive read access is now active."""
        self._google_connect_pending = False
        self._google_worker = None
        ok = False
        try:
            from src.data import google_oauth
            if google_oauth.store_credentials(record or {}):
                # reconnect() is the ONLY setter of the live session creds; runs
                # silently (no browser). It's a quick token refresh, fine on the
                # main thread.
                ok = google_oauth.reconnect() is not None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Google connect finalize failed: %s", exc)
            ok = False
        if not ok:
            self._emit_google_state("failed")
            self._notify_renn(
                "[SYSTEM: Google connection failed: the authorization could not be "
                "saved or activated. Ask the operator to try connecting again.]")
            return
        self._emit_google_state("connected")
        # STATUS ONLY — no token, no email, no name (invariant 13).
        self._notify_renn(
            "[SYSTEM: the operator connected their Google account; Drive read "
            "access is now active this session.]")
        self.enqueue_trigger("Confirm Drive is connected and ask which folder to use.")

    @Slot(str)
    def _on_google_connect_error(self, msg: str) -> None:
        """Main-thread: clear the single-flight latch + surface a redacted error.

        The worker already token-redacts ``msg`` (``_redact``), so we inject it
        as-is — no token/secret can leak through this path."""
        self._google_connect_pending = False
        self._google_worker = None
        self._emit_google_state("failed")
        self._notify_renn(f"[SYSTEM: Google connection failed: {msg}]")

    def _notify_renn(self, system_text: str) -> None:
        """Append a [SYSTEM] line to the engine history (so Renn sees the status)
        without persisting it as a user turn / starting a send. Best-effort."""
        if self._engine is None:
            return
        try:
            self._engine.append_to_history("user", system_text)
        except Exception as exc:  # noqa: BLE001 — notify is best-effort
            logger.debug("Google connect notify failed: %s", exc)

    def _emit_google_state(self, state: str, **extra) -> None:
        """Emit the connect-card state as a JSON string. STATUS ONLY — the
        payload never carries a token/email/name (invariant 13)."""
        import json
        payload = {"state": state}
        payload.update(extra)
        try:
            self.googleAuthState.emit(json.dumps(payload, default=str))
        except Exception:  # noqa: BLE001
            pass

    # ── tool-edit review / sign-off (M5) ────────────────────────────

    def pending_drafts(self) -> list[dict]:
        """Card drafts awaiting human sign-off before publishing to Guru (M5),
        each with a precomputed red/green diff + offline pre-flight checks for the
        in-thread review panel. Best-effort, read-only."""
        conn = self._open_conn(readonly=True)
        if conn is None:
            return []
        try:
            from src.data import enablement_store as store
            from src.data.text_diff import diff_rows, change_count
            out = []
            for d in store.list_pending_approvals(conn):
                proposed = d.get("content") or ""
                rows = diff_rows(self._current_card_md(conn, d.get("card_id") or ""), proposed)
                out.append({
                    "draft_id": d.get("id"), "title": d.get("title") or "Untitled",
                    "card_id": d.get("card_id") or "", "status": d.get("status"),
                    "diff": rows, "change_count": change_count(rows),
                    "checks": self._draft_checks(proposed, d.get("card_id") or ""),
                })
            return out
        except Exception:  # noqa: BLE001 — best-effort, never fatal
            return []
        finally:
            conn.close()

    def approve_draft(self, draft_id) -> dict:
        """Record the operator's sign-off, then complete the (now-gated-open) push
        to Guru using the stored target. Returns the push result."""
        conn = self._open_conn(readonly=False)
        if conn is None:
            return {"ok": False, "error": "no_db"}
        try:
            import json
            from src.data import enablement_store as store
            from src.data.chat_tools.enablement_tools import _push_guru_draft_impl
            did = int(draft_id)
            store.approve_draft(conn, did, approved_by=self._approver_identity())
            draft = store.get_draft(conn, did) or {}
            try:
                target = json.loads(draft.get("pending_push_json") or "{}")
            except Exception:  # noqa: BLE001
                target = {}
            result = _push_guru_draft_impl(
                conn, did, target.get("collection_id"), target.get("folder_id"))
            store.clear_push_request(conn, did)
            return {"ok": bool(result.get("ok")), "draft_id": did, "result": result}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            conn.close()

    def reject_draft(self, draft_id) -> dict:
        """Decline a pending publish: drop the push request (the draft stays
        editable). No sign-off recorded, nothing reaches Guru."""
        conn = self._open_conn(readonly=False)
        if conn is None:
            return {"ok": False, "error": "no_db"}
        try:
            from src.data import enablement_store as store
            did = int(draft_id)
            store.clear_push_request(conn, did)
            return {"ok": True, "draft_id": did, "rejected": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            conn.close()

    def _current_card_md(self, conn, card_id: str) -> str:
        if not card_id:
            return ""   # a new card → diff is all-additions
        try:
            row = conn.execute(
                "SELECT content FROM guru_cards WHERE card_id = ?", (card_id,)).fetchone()
        except Exception:  # noqa: BLE001 — no local cache → all-additions
            return ""
        if row is None:
            return ""
        try:
            return (row["content"] if hasattr(row, "keys") else row[0]) or ""
        except Exception:  # noqa: BLE001
            return ""

    def _draft_checks(self, text: str, card_id: str) -> list[dict]:
        try:
            from src.data.enablement_checks import run_checks
            return run_checks(text, card_id=card_id)
        except Exception:  # noqa: BLE001 — checks are advisory
            return []

    def _approver_identity(self) -> str:
        """Reuse the operator's configured identity for the audit trail; 'user'
        is the safe fallback (the approval timestamp is the load-bearing part).

        Delegates to the M1 single source of truth so the detected_email
        precedence tier is honored (no network on this hot path)."""
        try:
            from src.data import enablement_identity as ident
            from src.data.settings_manager import get_section
            who = ident.operator_email(resolve=False)
            if who:
                return who
            en = get_section("enablement", {}) or {}
            return (en.get("guru") or {}).get("email") or "user"
        except Exception:  # noqa: BLE001
            return "user"

    # ── past-chat browser (M3) ──────────────────────────────────────

    def list_sessions(self, limit: int = 30) -> list[dict]:
        """Recent enablement chat sessions for the history drawer. Scoped in SQL
        to ``source_page='enablement'`` so the Agent only surfaces its own chats
        (never product-mode ticket conversations — keeps it decoupled)."""
        conn = self._open_conn(readonly=True)
        if conn is None:
            return []
        try:
            from src.services.chat_session import list_sessions as _list
            return _list(limit=limit, conn=conn, source_page="enablement")
        except Exception:  # noqa: BLE001 — best-effort
            return []
        finally:
            conn.close()

    def search_sessions(self, query: str, limit: int = 20) -> list[dict]:
        """FTS5 search across this Agent's past chat messages. Scoped to
        ``source_page='enablement'`` so search never leaks product-mode content.
        Returns {session_id, session_title, role, content, created_at}."""
        if not (query or "").strip():
            return []
        conn = self._open_conn(readonly=True)
        if conn is None:
            return []
        try:
            from src.services.chat_session import search_messages
            return search_messages(query.strip(), limit=limit, conn=conn,
                                   source_page="enablement")
        except Exception:  # noqa: BLE001 — best-effort
            return []
        finally:
            conn.close()

    def load_session(self, session_id: str) -> dict:
        """Load a past transcript and make it the *active* session so the chat
        continues coherently: restore the engine's history, repoint the engine
        + the tools→session pointer at it. Returns {session_id, messages}."""
        conn = self._open_conn(readonly=True)
        data = {}
        if conn is not None:
            try:
                from src.services.chat_session import load_session as _load
                data = _load(session_id, conn=conn) or {}
            except Exception:  # noqa: BLE001 — best-effort
                data = {}
            finally:
                conn.close()
        # Decoupling guard: only enablement chats may become the active Agent
        # thread. Never load a product-mode (ticket) conversation — even if a
        # caller hands us its id (e.g. a stale search result).
        if data.get("source_page") != "enablement":
            return {"session_id": session_id, "messages": []}
        msgs = [
            {"role": m.get("role"), "content": m.get("content", "")}
            for m in (data.get("messages") or [])
            if m.get("role") in ("user", "assistant")
        ]
        if self._engine is not None:
            try:
                self._engine.set_history([dict(m) for m in msgs])
                self._engine.set_session_id(session_id)
            except Exception:  # noqa: BLE001
                pass
        self._session_id = session_id
        self._write_session_pointer(session_id)
        return {"session_id": session_id, "messages": msgs}

    def delete_session(self, session_id: str) -> bool:
        """Delete a past chat (cascade). If it was the active session, the thread
        resets so the next send starts fresh."""
        conn = self._open_conn(readonly=False)
        if conn is None:
            return False
        try:
            from src.services.chat_session import delete_session as _del
            ok = bool(_del(session_id, conn=conn))
        except Exception:  # noqa: BLE001 — best-effort
            ok = False
        finally:
            conn.close()
        if ok and session_id == self._session_id:
            self.new_session()
        return ok

    def new_session(self) -> None:
        """Start a fresh thread: clear engine history and drop the active session
        so the next send creates a new one; clear the tools→session pointer."""
        self._session_id = None
        if self._engine is not None:
            try:
                self._engine.clear_history()
                self._engine.set_session_id(None)
            except Exception:  # noqa: BLE001
                pass
        self._write_session_pointer("")

    def _open_conn(self, *, readonly: bool):
        """A connection to the DB the MCP server writes to (same target as
        ``recent_tool_calls``)."""
        db_path = self._db_path()
        if not db_path:
            return None
        try:
            from src.data.connection_factory import get_connection
            return get_connection(db_path, readonly=readonly)
        except Exception:  # noqa: BLE001
            return None

    def _write_session_pointer(self, session_id: str) -> None:
        """Point the persistent MCP server's tools at ``session_id`` (or clear)."""
        db_path = self._db_path()
        if not db_path:
            return
        try:
            Path(db_path).parent.joinpath(".current_chat_session").write_text(
                session_id or "", encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    # ── engine ──────────────────────────────────────────────────────

    def _db_path(self) -> str | None:
        if self.demo or self.db is None:
            import os
            import tempfile
            return os.path.join(tempfile.gettempdir(), "alma_enablement_demo.db")
        return str(self.db.db_path) if hasattr(self.db, "db_path") else None

    def _setup_engine(self):
        try:
            from src.services.chat_engine import ChatEngine
            from src.ui.pages.enablement.page import RENN_SYSTEM_PROMPT
            self._engine = ChatEngine(
                system_prompt=RENN_SYSTEM_PROMPT,
                context_provider=self._agent_context,
                task_type="enablement_chat",
                tools_enabled=True,
                use_mcp_tools=True,
                db_path=self._db_path(),
                stream=True,   # the Agent surface streams tokens (M-streaming)
            )
            self._engine.bridge_recycle_requested.connect(self._on_recycle)
            # M0 busy-queue: when a turn finishes, drain one queued trigger (a
            # picker-resolve follow-up that arrived while Renn was busy).
            self._engine.busy_changed.connect(self._on_busy_changed)
            # Persist the assistant turn (content + telemetry) to chat_messages so
            # past chats actually have a transcript + a title. The bridge CHAINS
            # this callback (it adds the live meter on top), so both survive.
            self._engine.set_telemetry_callback(self._persist_turn)
        except Exception as exc:  # noqa: BLE001 — chat degrades, the page still loads
            logger.warning("Agent chat engine unavailable: %s", exc)
            self._engine = None

    def _agent_context(self, user_message, history):
        """Per-turn context for the Agent surface — surfaces the operator identity
        so Renn can confirm 'who am I' (the enablement Qt page injects it via its
        own _chat_context; the Agent engine previously had no context provider)."""
        try:
            from src.data import enablement_identity as ident
            return ident.operator_context_line()
        except Exception:  # noqa: BLE001 — the chat still works without the line
            return ""

    def _build_mcp_config(self) -> list[dict]:
        import sys
        db_path = self._db_path() or ""
        pointer = str(Path(db_path).parent / ".current_chat_session") if db_path else ""
        env = [
            {"name": "ALMA_DB_PATH", "value": db_path},
            {"name": "ALMA_CHAT_SESSION_FILE", "value": pointer},
        ]
        try:
            from src.ui import app_modes
            if app_modes.current_mode() == app_modes.MODE_ENABLEMENT:
                env.append({"name": "ALMA_MCP_EXCLUDE_TOOLS", "value": "semantic_search"})
        except Exception:
            pass
        return [{
            "name": "alma-chat-tools",
            "command": sys.executable,
            "args": ["-m", "src.mcp.chat_mcp_server"],
            "env": env,
        }]

    def _ensure_session(self):
        if self._session_id or self._engine is None:
            return
        # Create the session in the SAME db the engine, MCP tools, pointer, and
        # the M3 history browser all read from (``_db_path()``) — not
        # ``self.db.conn`` — so a chat you just had actually shows up in History
        # (in demo mode those two diverge; see _db_path).
        conn = self._open_conn(readonly=False)
        if conn is None:
            return
        try:
            from src.services.chat_session import create_session
            self._session_id = create_session("enablement", conn=conn)
            self._engine.set_session_id(self._session_id)
            self._write_session_pointer(self._session_id)
        except Exception as exc:  # noqa: BLE001 — telemetry only; tools still work
            logger.debug("agent session create failed: %s", exc)
        finally:
            conn.close()

    # ── provider (mirrors EnablementPage._prepare_provider) ─────────

    def _prepare_provider(self):
        try:
            from src.gemini.client_factory import resolve_provider_for_task
            prov = resolve_provider_for_task("enablement_chat")
        except Exception:
            prov = "gemini"
        if prov == "gemini":
            self._teardown_claude_client()
            self._ensure_warm_bridge()
        else:
            self._teardown_warm_bridge()
            self._ensure_claude_client()

    def _ensure_warm_bridge(self):
        if self._warm_bridge is not None or self._engine is None:
            return
        try:
            from src.agents.report_bridge_client import ReportBridgeClient
            bridge = ReportBridgeClient(model="gemini-2.5-flash")
            bridge.set_mcp_config(self._build_mcp_config())
            self._warm_bridge = bridge
            self._engine.set_client(bridge)
        except Exception as exc:  # noqa: BLE001 — fall back to a per-message client
            logger.warning("Agent warm bridge boot failed: %s", exc)
            self._warm_bridge = None

    def _ensure_claude_client(self):
        if self._engine is None:
            return
        if self._claude_client is not None:
            self._engine.set_client(self._claude_client)
            return
        try:
            from src.gemini.client_factory import build_client_for_task
            client = build_client_for_task("enablement_chat")
            if client is not None and hasattr(client, "set_mcp_config"):
                client.set_mcp_config(self._build_mcp_config())
            self._claude_client = client
            self._engine.set_client(client)
        except Exception as exc:  # noqa: BLE001 — degrade to a per-message client
            logger.warning("Agent Claude client wiring failed: %s", exc)
            self._claude_client = None
            try:
                self._engine.set_client(None)
            except Exception:
                pass

    def _teardown_warm_bridge(self):
        if self._warm_bridge is not None:
            try:
                self._warm_bridge.shutdown()
            except Exception:
                pass
            self._warm_bridge = None

    def _teardown_claude_client(self):
        if self._claude_client is not None:
            try:
                self._claude_client.shutdown()
            except Exception:
                pass
            self._claude_client = None

    def _on_recycle(self):
        # Provider switch / degraded streak: drop the warm client so the next
        # send rebuilds + re-wires the MCP tools.
        self._teardown_warm_bridge()
        self._teardown_claude_client()
