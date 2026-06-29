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
        self._setup_engine()

    @property
    def engine(self):
        return self._engine

    def send(self, text: str):
        """Ensure a session + provider, then send. The ACP bridge boots lazily
        on the first send, so the provider is wired here, not at construction."""
        if self._engine is None:
            return
        self._ensure_session()
        self._prepare_provider()
        self._engine.send(text or "")

    def shutdown(self):
        self._teardown_warm_bridge()
        self._teardown_claude_client()

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
            )
            self._engine.bridge_recycle_requested.connect(self._on_recycle)
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
        try:
            from src.services.chat_session import create_session
            conn = self.db.conn if (self.db is not None and hasattr(self.db, "conn")) else None
            self._session_id = create_session("enablement", conn=conn)
            self._engine.set_session_id(self._session_id)
            db_path = self._db_path()
            if db_path:
                Path(db_path).parent.joinpath(".current_chat_session").write_text(
                    self._session_id, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 — telemetry only; tools still work
            logger.debug("agent session create failed: %s", exc)

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
