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

from PySide6.QtCore import QObject

logger = logging.getLogger("alma.agent_chat")


class AgentChatController(QObject):
    """Owns the Agent's ChatEngine + warm client. Expose ``engine`` to the
    bridge and route sends through ``send`` (lazily wires the provider)."""

    def __init__(self, db=None, demo: bool = False, parent=None):
        super().__init__(parent)
        self.db = db
        self.demo = demo
        self._engine = None
        self._warm_bridge = None
        self._claude_client = None
        self._session_id = None
        self._voice = None
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
        is the safe fallback (the approval timestamp is the load-bearing part)."""
        try:
            from src.data.settings_manager import get_section
            en = get_section("enablement", {}) or {}
            return en.get("operator_email") or (en.get("guru") or {}).get("email") or "user"
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
                task_type="enablement_chat",
                tools_enabled=True,
                use_mcp_tools=True,
                db_path=self._db_path(),
                stream=True,   # the Agent surface streams tokens (M-streaming)
            )
            self._engine.bridge_recycle_requested.connect(self._on_recycle)
            # Persist the assistant turn (content + telemetry) to chat_messages so
            # past chats actually have a transcript + a title. The bridge CHAINS
            # this callback (it adds the live meter on top), so both survive.
            self._engine.set_telemetry_callback(self._persist_turn)
        except Exception as exc:  # noqa: BLE001 — chat degrades, the page still loads
            logger.warning("Agent chat engine unavailable: %s", exc)
            self._engine = None

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
