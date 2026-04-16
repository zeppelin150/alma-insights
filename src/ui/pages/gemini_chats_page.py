"""
Alma Insights — Gemini Chats Page (Build 12.0 — Chat Uplevel)

Redesigned conversational UI matching Cambric mockups:
  - Header: RECENT chips, model selector, Explore button
  - User bubbles: ALMA_GREEN_DARK bg, cream text, right-aligned
  - Gemini bubbles: white bg + border, finding cards, ticket chips
  - Status bar: Ready + conversation count
  - Telemetry: tokens, cost, latency per message via chat_messages

Powered by the shared ChatEngine (src/services/chat_engine.py).
"""

import json
import logging
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QSizePolicy, QLineEdit, QComboBox,
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_MID, ALMA_GREEN_LIGHT, ALMA_GREEN_SUBTLE,
    ALMA_WHITE, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_TEXT_ON_DARK,
    ALMA_BORDER, ALMA_BORDER_LIGHT, ALMA_BG_ELEVATED, ALMA_BG_INSET,
    ALMA_INFO, ALMA_SUCCESS, ALMA_WARNING, ALMA_HOVER_LIGHT,
    apply_card_shadow, apply_card_shadow_soft,
)
from src.ui.widgets.message_bubble import MessageBubble

logger = logging.getLogger("alma.gemini_chats")

# Models ordered by MCP compatibility — flash-lite and pro work reliably
# with native MCP tool calling. flash/3.x models spawn native agents that stall.
_DEFAULT_MODELS = [
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-3-flash-preview",
    "gemini-3.1-pro-preview",
]


def _get_cached_models() -> list[str]:
    """Read cached model list from settings. Fast, no subprocess."""
    try:
        from src.data.settings_manager import get_section
        cfg = get_section("gemini", {})
        cached = cfg.get("available_models", [])
        if cached and isinstance(cached, list):
            return cached
    except Exception:
        pass
    return _DEFAULT_MODELS


def discover_and_cache_models():
    """Cache the known model list to settings."""
    try:
        from src.data.settings_manager import get_section, set_section
        cfg = get_section("gemini", {})
        cfg["available_models"] = list(_DEFAULT_MODELS)
        set_section("gemini", cfg)
        logger.info("Cached %d models to settings", len(_DEFAULT_MODELS))
        return list(_DEFAULT_MODELS)
    except Exception as e:
        logger.warning("Model cache write failed: %s", e)
    return list(_DEFAULT_MODELS)


# ═══════════════════════════════════════════
#  GEMINI CHATS PAGE
# ═══════════════════════════════════════════

class GeminiChatsPage(QWidget):
    """Full-width conversational chat page powered by ChatEngine."""

    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db = db_manager
        self._session_id = None
        self._drilldown = None
        self._active_chip = None  # Currently highlighted recent chip
        self._chip_buttons: list[QPushButton] = []
        self._explore_dropdown = None
        self._last_response_persisted = False

        # Warm bridge (lazy-booted on first send, reused for all messages)
        self._warm_bridge = None

        # ChatEngine — warm-client path, MCP native tools
        from src.services.chat_engine import ChatEngine
        self._engine = ChatEngine(
            system_prompt=(
                "You are an expert RCM (Revenue Cycle Management) data analyst. "
                "You have tools to query a support ticket database.\n\n"
                "When analyzing an entity (payer, product area, feature):\n"
                "1. Use query_entities to get tickets + classification breakdowns\n"
                "2. Report the TRC breakdown with counts\n"
                "3. Look for PATTERNS in the issue_snippets — group tickets with similar root causes\n"
                "4. Call out systemic issues (same error appearing in multiple tickets)\n"
                "5. Cite specific ticket IDs as evidence\n\n"
                "When the user asks for tickets or details, list ticket IDs with their "
                "TRC code and issue summary.\n\n"
                "Be analytical, not just descriptive. Identify root causes, not just categories."
            ),
            task_type="report_generation",
            context_provider=self._provide_context,
            tools_enabled=True,
            use_mcp_tools=True,
            db_path=str(self.db.db_path) if hasattr(self.db, "db_path") else None,
        )
        self._engine.response_ready.connect(self._on_response)
        self._engine.error_occurred.connect(self._on_error)
        self._engine.busy_changed.connect(self._on_busy_changed)
        self._engine.status_update.connect(self._on_status_update)

        # Wire telemetry callback for chat_messages storage
        self._engine.set_telemetry_callback(self._on_telemetry)

        self._build_ui()

        # Thinking dots animation
        self._dots_timer = QTimer(self)
        self._dots_timer.timeout.connect(self._animate_dots)
        self._dots_count = 0

    def _provide_context(self, user_message, history):
        """Lightweight data scope context — NO data injection."""
        try:
            from src.data.connection_factory import get_connection
            db_path = str(self.db.db_path)
            conn = get_connection(db_path)
            ticket_count = conn.execute(
                "SELECT COUNT(*) FROM tickets"
            ).fetchone()[0]
            date_range = conn.execute(
                "SELECT MIN(created_at), MAX(created_at) FROM tickets"
            ).fetchone()
            trc_count = conn.execute(
                "SELECT COUNT(DISTINCT trc_code) FROM tickets"
            ).fetchone()[0]
            from src.data.source_registry import SourceRegistry
            from src.data.warehouse_query import WarehouseQuery
            _wq = WarehouseQuery(conn, SourceRegistry(conn))
            convo_count = _wq.get_ticket_count()
            conn.close()

            return (
                f"[DATA SCOPE] This database contains {ticket_count} tickets "
                f"across {trc_count} TRC categories, "
                f"date range: {date_range[0] or 'unknown'} to {date_range[1] or 'unknown'}. "
                f"It also contains {convo_count} full conversation threads (searchable). "
                f"Use query_tickets for structured data (dates, TRCs, status). "
                f"Use search_conversations for text search across thread content."
            )
        except Exception as e:
            logger.warning("Context scope failed: %s", e)
            return ""

    def set_drilldown_panel(self, panel):
        self._drilldown = panel

    # ═══════════════════════════════════════
    #  BUILD UI
    # ═══════════════════════════════════════

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Header ──
        header_w = QWidget()
        header_w.setStyleSheet(f"background: {ALMA_BG_ELEVATED};")
        header_layout = QVBoxLayout(header_w)
        header_layout.setContentsMargins(28, 20, 28, 12)
        header_layout.setSpacing(10)

        # Row 1: Title + New Chat + Model + Explore
        top_row = QHBoxLayout()
        top_row.setSpacing(12)

        title = QLabel("Chat")
        title.setStyleSheet(f"""
            font-size: 26px; font-weight: 700;
            color: {ALMA_TEXT_DARK}; background: transparent;
        """)
        top_row.addWidget(title)

        self._new_chat_btn = QPushButton("+ New chat")
        self._new_chat_btn.setCursor(Qt.PointingHandCursor)
        self._new_chat_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_TEXT_ON_DARK};
                border: none; border-radius: 14px;
                padding: 6px 16px; font-size: 12px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
        """)
        self._new_chat_btn.clicked.connect(self._on_new_chat)
        top_row.addWidget(self._new_chat_btn)

        top_row.addStretch()

        # Model selector (pill style)
        self._model_combo = QComboBox()
        self._model_combo.setStyleSheet(f"""
            QComboBox {{
                background: {ALMA_WHITE}; color: {ALMA_TEXT_DARK};
                border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 14px;
                padding: 5px 12px; font-size: 11px; min-width: 170px;
            }}
            QComboBox::drop-down {{ border: none; }}
        """)
        self._model_combo.currentIndexChanged.connect(self._on_model_changed)
        top_row.addWidget(self._model_combo)

        # Explore button
        self._explore_btn = QPushButton("\u2630  Explore")
        self._explore_btn.setCursor(Qt.PointingHandCursor)
        self._explore_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_TEXT_ON_DARK};
                border: none; border-radius: 14px;
                padding: 6px 16px; font-size: 12px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
        """)
        self._explore_btn.clicked.connect(self._on_explore_clicked)
        top_row.addWidget(self._explore_btn)

        header_layout.addLayout(top_row)

        # Row 2: RECENT label + chips
        chips_row = QHBoxLayout()
        chips_row.setSpacing(8)

        recent_lbl = QLabel("RECENT")
        recent_lbl.setStyleSheet(f"""
            font-size: 11px; font-weight: 600; letter-spacing: 1px;
            color: {ALMA_TEXT_LIGHT}; background: transparent;
        """)
        chips_row.addWidget(recent_lbl)

        self._chips_container = QHBoxLayout()
        self._chips_container.setSpacing(6)
        chips_row.addLayout(self._chips_container)

        chips_row.addStretch()
        header_layout.addLayout(chips_row)

        outer.addWidget(header_w)

        # ── Separator ──
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {ALMA_BORDER_LIGHT};")
        outer.addWidget(sep)

        # ── Chat messages area ──
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self._messages_container = QWidget()
        self._messages_layout = QVBoxLayout(self._messages_container)
        self._messages_layout.setContentsMargins(28, 16, 28, 16)
        self._messages_layout.setSpacing(16)
        self._messages_layout.addStretch()

        self._scroll.setWidget(self._messages_container)
        outer.addWidget(self._scroll, 1)

        # ── Status label (thinking indicator) ──
        self._status_label = QLabel("")
        self._status_label.setStyleSheet(f"""
            font-size: 11px; color: {ALMA_TEXT_LIGHT}; font-style: italic;
            padding: 0px 28px;
        """)
        self._status_label.setVisible(False)
        outer.addWidget(self._status_label)

        # ── Input bar ──
        input_frame = QFrame()
        input_frame.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED};
                border-top: 1px solid {ALMA_BORDER_LIGHT};
            }}
        """)
        input_layout = QHBoxLayout(input_frame)
        input_layout.setContentsMargins(28, 12, 28, 12)
        input_layout.setSpacing(8)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Ask anything about your ticket data...")
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: {ALMA_WHITE};
                border: 1px solid {ALMA_BORDER};
                border-radius: 20px;
                padding: 10px 18px;
                font-size: 13px;
                color: {ALMA_TEXT_DARK};
            }}
            QLineEdit:focus {{
                border-color: {ALMA_GREEN_MID};
            }}
        """)
        self._input.returnPressed.connect(self._on_send)
        input_layout.addWidget(self._input, 1)

        self._send_btn = QPushButton("Send")
        self._send_btn.setCursor(Qt.PointingHandCursor)
        self._send_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_TEXT_ON_DARK};
                border: none; border-radius: 20px;
                padding: 10px 28px; font-size: 13px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
            QPushButton:disabled {{ background: {ALMA_BORDER}; color: {ALMA_TEXT_LIGHT}; }}
        """)
        self._send_btn.clicked.connect(self._on_send)
        input_layout.addWidget(self._send_btn)

        outer.addWidget(input_frame)

        # ── Status bar ──
        status_bar = QFrame()
        status_bar.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED};
                border-top: 1px solid {ALMA_BORDER_LIGHT};
            }}
        """)
        status_layout = QHBoxLayout(status_bar)
        status_layout.setContentsMargins(28, 6, 28, 6)

        self._ready_label = QLabel("Ready")
        self._ready_label.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT};")
        status_layout.addWidget(self._ready_label)

        status_layout.addStretch()

        self._convo_count_label = QLabel("")
        self._convo_count_label.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT};")
        status_layout.addWidget(self._convo_count_label)

        outer.addWidget(status_bar)

        # Load recent sessions as chips
        self._load_session_chips()
        self._update_convo_count()

    # ── Model selector ────────────────────────────────────

    def _populate_model_combo(self):
        """Populate model combo from cached settings."""
        self._model_combo.blockSignals(True)
        self._model_combo.clear()

        models = _get_cached_models()
        for m in models:
            self._model_combo.addItem(m, m)

        try:
            from src.data.settings_manager import get_section
            cfg = get_section("gemini", {})
            current = cfg.get("model", "gemini-2.5-flash")
            idx = self._model_combo.findData(current)
            if idx >= 0:
                self._model_combo.setCurrentIndex(idx)
        except Exception:
            pass

        self._model_combo.blockSignals(False)

    def _on_model_changed(self, idx):
        if idx < 0:
            return
        model_id = self._model_combo.currentData()
        if model_id:
            self._engine.set_model(model_id)
            # Model switch requires bridge restart (ACP subprocess is model-locked)
            if self._warm_bridge:
                logger.info("Model changed to %s, restarting bridge", model_id)
                self._warm_bridge.shutdown()
                self._warm_bridge = None  # Will re-boot on next send

    # ── Warm bridge ──────────────────────────────────────

    def _build_mcp_config(self) -> list[dict]:
        """Build MCP server config for chat tools."""
        import sys
        db_path = str(self.db.db_path) if hasattr(self.db, "db_path") else ""
        return [
            {
                "name": "alma-chat-tools",
                "command": sys.executable,
                "args": ["-m", "src.mcp.chat_mcp_server"],
                "env": [
                    {"name": "ALMA_DB_PATH", "value": db_path},
                ],
            }
        ]

    def _ensure_warm_bridge(self):
        """Boot the warm bridge on first use, reuse thereafter.

        Boots with MCP chat tools for native function calling.
        Falls back to build-per-message if bridge cannot boot.
        """
        if self._warm_bridge is not None:
            return

        try:
            from src.agents.report_bridge_client import ReportBridgeClient
            model = self._model_combo.currentData() or "gemini-2.5-flash-lite"
            self._warm_bridge = ReportBridgeClient(model=model)
            self._warm_bridge.set_mcp_config(self._build_mcp_config())
            self._engine.set_client(self._warm_bridge)
            logger.info("Warm bridge booted for chat with MCP (model=%s)", model)
        except Exception as e:
            logger.warning("Warm bridge boot failed, using build-per-message: %s", e)
            self._warm_bridge = None

    # ── Session chips ────────────────────────────────────

    def _load_session_chips(self):
        """Load recent sessions as clickable rounded pills."""
        # Clear existing chips
        self._chip_buttons.clear()
        while self._chips_container.count():
            item = self._chips_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        try:
            from src.services.chat_session import list_sessions
            sessions = list_sessions(5, self.db.conn)
            for i, s in enumerate(sessions):
                title = s.get("title") or s.get("first_question")
                # Pre-009 fallback: extract first user message from JSON blob
                if not title:
                    title = self._extract_chip_title(s.get("session_id"))
                if not title:
                    title = "Chat"
                # Truncate long titles
                if len(title) > 25:
                    title = title[:22] + "..."

                chip = QPushButton(title)
                chip.setCursor(Qt.PointingHandCursor)
                chip.setCheckable(True)
                chip.setStyleSheet(self._chip_style(False))
                sid = s["session_id"]
                chip.clicked.connect(lambda _, sid=sid, btn=chip: self._on_chip_clicked(sid, btn))
                self._chips_container.addWidget(chip)
                self._chip_buttons.append(chip)
        except Exception as e:
            logger.warning("Session list load failed: %s", e)

    def _extract_chip_title(self, session_id: str | None) -> str | None:
        """Extract first user message from JSON blob for chip title (pre-009)."""
        if not session_id:
            return None
        try:
            row = self.db.conn.execute(
                "SELECT messages FROM chat_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row:
                raw = row[0] if isinstance(row, tuple) else row["messages"]
                if raw:
                    msgs = json.loads(raw)
                    for m in msgs:
                        if isinstance(m, dict) and m.get("role") == "user":
                            content = m.get("content", "")
                            return content[:40] if content else None
        except Exception:
            pass
        return None

    def _chip_style(self, active: bool) -> str:
        if active:
            return f"""
                QPushButton {{
                    background: {ALMA_GREEN_DARK}; color: {ALMA_TEXT_ON_DARK};
                    border: none; border-radius: 15px;
                    padding: 5px 14px; font-size: 12px; font-weight: 500;
                }}
                QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
            """
        else:
            return f"""
                QPushButton {{
                    background: {ALMA_BG_ELEVATED}; color: {ALMA_TEXT_MID};
                    border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 15px;
                    padding: 5px 14px; font-size: 12px; font-weight: 500;
                }}
                QPushButton:hover {{ background: {ALMA_HOVER_LIGHT}; }}
            """

    def _on_chip_clicked(self, session_id, btn):
        """Load session and highlight chip."""
        # Update chip styling
        for chip in self._chip_buttons:
            chip.setStyleSheet(self._chip_style(chip is btn))
        self._active_chip = btn
        self._load_session(session_id)

    # ── Explore dropdown ─────────────────────────────────

    def _on_explore_clicked(self):
        """Show the Explore dropdown below the button."""
        from src.ui.widgets.explore_menu import ExploreDropdown

        if self._explore_dropdown is None:
            self._explore_dropdown = ExploreDropdown(conn=self.db.conn)
            self._explore_dropdown.mode_selected.connect(self._on_explore_mode)

        self._explore_dropdown.show_below(self._explore_btn)

    def _on_explore_mode(self, mode: str):
        """Handle Explore dropdown selection — open drilldown in the requested mode."""
        logger.info("Explore mode selected: %s", mode)
        if self._drilldown:
            self._ensure_chat_drilldown()
            self._chat_drilldown.set_session_id(self._session_id)
            self._chat_drilldown.show_mode(mode)
            self._drilldown.show_widget("Drill Down", "", self._chat_drilldown)

    def _ensure_chat_drilldown(self):
        """Lazily create the ChatDrilldown widget."""
        if not hasattr(self, "_chat_drilldown") or self._chat_drilldown is None:
            from src.ui.widgets.chat_drilldown import ChatDrilldown
            self._chat_drilldown = ChatDrilldown(conn=self.db.conn)
            self._chat_drilldown.session_selected.connect(self._on_drilldown_session_selected)
            self._chat_drilldown.report_selected.connect(self._on_drilldown_report_selected)

    def _on_drilldown_session_selected(self, session_id: str):
        """User clicked a session in the Chats drilldown — load it."""
        self._load_session(session_id)
        # Update chips
        for chip in self._chip_buttons:
            chip.setStyleSheet(self._chip_style(False))

    def _on_drilldown_report_selected(self, report_id: str):
        """User clicked a report — start a chat grounded in that report."""
        logger.info("Report selected for chat: %s", report_id)

    # ── Session management ────────────────────────────────

    def _on_new_chat(self):
        """Start a fresh chat session."""
        self._session_id = None
        self._engine.clear_history()
        self._engine.set_session_id(None)
        self._clear_messages_ui()
        self._input.setFocus()

        # Deselect all chips
        for chip in self._chip_buttons:
            chip.setStyleSheet(self._chip_style(False))
        self._active_chip = None

        welcome = MessageBubble(
            "assistant",
            "Welcome to Gemini Chats. Ask anything about your ticket data \u2014 "
            "I can query trends, anomalies, ticket details, and help investigate issues."
        )
        self._messages_layout.insertWidget(
            self._messages_layout.count() - 1, welcome
        )

    def _clear_messages_ui(self):
        while self._messages_layout.count() > 1:
            item = self._messages_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _load_session(self, session_id):
        try:
            from src.services.chat_session import load_session
            session = load_session(session_id, self.db.conn)
            if not session:
                return
            self._session_id = session_id
            self._engine.set_session_id(session_id)
            self._engine.set_history(session.get("messages", []))
            self._clear_messages_ui()
            for msg in self._engine.history:
                bubble = MessageBubble(msg["role"], msg["content"])
                self._messages_layout.insertWidget(
                    self._messages_layout.count() - 1, bubble
                )
            QTimer.singleShot(100, self._scroll_to_bottom)
        except Exception as e:
            logger.warning("Failed to load session: %s", e)

    # ── Status bar ────────────────────────────────────────

    def _update_convo_count(self):
        """Update the conversation count in the status bar."""
        try:
            count = self.db.conn.execute(
                "SELECT COUNT(*) FROM chat_sessions"
            ).fetchone()[0]
            self._convo_count_label.setText(
                f"{count} conversation{'s' if count != 1 else ''} in database"
            )
        except Exception:
            self._convo_count_label.setText("")

    # ── Send / receive ────────────────────────────────────

    def _on_send(self):
        text = self._input.text().strip()
        if not text or self._engine.is_busy:
            return

        self._input.clear()

        # Boot warm bridge on first send (lazy — avoids cold start on page show)
        self._ensure_warm_bridge()

        # Create session if needed
        if not self._session_id:
            try:
                from src.services.chat_session import create_session
                self._session_id = create_session("gemini_chats", conn=self.db.conn)
                self._engine.set_session_id(self._session_id)
            except Exception as e:
                logger.warning("Session create failed: %s", e)

        # Add user bubble immediately
        bubble = MessageBubble("user", text)
        self._messages_layout.insertWidget(
            self._messages_layout.count() - 1, bubble
        )

        # Persist user message
        try:
            from src.services.chat_session import append_message
            append_message(self._session_id, "user", text, self.db.conn)
        except Exception as e:
            logger.warning("User message persist failed: %s", e)

        # Send to engine
        self._engine.send(text)
        QTimer.singleShot(100, self._scroll_to_bottom)

    def _on_response(self, response_text):
        bubble = MessageBubble("assistant", response_text)
        self._messages_layout.insertWidget(
            self._messages_layout.count() - 1, bubble
        )

        # Message persistence is handled by _on_telemetry (fires before this signal).
        # If telemetry callback didn't fire (edge case), persist without telemetry.
        if not self._last_response_persisted:
            try:
                from src.services.chat_session import append_message
                append_message(self._session_id, "assistant", response_text, self.db.conn)
            except Exception as e:
                logger.warning("Response persist fallback failed: %s", e)
        self._last_response_persisted = False

        # Refresh chips and count after new message
        self._load_session_chips()
        self._update_convo_count()

        QTimer.singleShot(100, self._scroll_to_bottom)

    def _on_telemetry(self, role: str, content: str, telemetry: dict):
        """Telemetry callback from ChatEngine — persist message WITH telemetry in one write.

        This fires BEFORE response_ready, so we write the message here
        with full telemetry and set a flag so _on_response skips the duplicate write.
        """
        if not self._session_id:
            return
        try:
            from src.services.chat_session import append_message
            # Build tool_calls list for persistence
            tool_names = telemetry.get("tool_names", [])
            tool_calls_data = [{"name": n} for n in tool_names] if tool_names else None

            append_message(
                self._session_id, role, content, self.db.conn,
                model_used=telemetry.get("model_used"),
                tokens_in=telemetry.get("tokens_in"),
                tokens_out=telemetry.get("tokens_out"),
                latency_ms=telemetry.get("latency_ms"),
                tool_calls=tool_calls_data,
            )
            self._last_response_persisted = True

            # Persist MCP tool calls to chat_tool_executions for the monitor
            if tool_names:
                self._persist_mcp_tool_calls(tool_names, telemetry)

        except Exception as e:
            logger.debug("Telemetry persist failed: %s", e)
            self._last_response_persisted = False

    def _persist_mcp_tool_calls(self, tool_names: list, telemetry: dict):
        """Write MCP tool call records to chat_tool_executions for drill-down monitor."""
        import uuid
        from datetime import datetime
        try:
            latency_per_tool = (telemetry.get("latency_ms", 0) // max(len(tool_names), 1))
            now = datetime.utcnow().isoformat()
            for name in tool_names:
                self.db.conn.execute(
                    """INSERT INTO chat_tool_executions
                       (execution_id, session_id, tool_name, elapsed_ms, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (str(uuid.uuid4()), self._session_id, name,
                     latency_per_tool, now),
                )
            self.db.conn.commit()
        except Exception as e:
            logger.debug("MCP tool persist failed: %s", e)

    def _on_error(self, error_text):
        error_msg = f"Error: {error_text[:200]}"
        bubble = MessageBubble("assistant", error_msg)
        self._messages_layout.insertWidget(
            self._messages_layout.count() - 1, bubble
        )

    # ── Thinking indicator ────────────────────────────────

    def _on_busy_changed(self, busy):
        self._send_btn.setEnabled(not busy)
        self._input.setEnabled(not busy)
        if busy:
            self._dots_count = 0
            self._dots_timer.start(400)
            self._ready_label.setText("Thinking...")
        else:
            self._dots_timer.stop()
            self._status_label.setVisible(False)
            self._ready_label.setText("Ready")

    def _on_status_update(self, text):
        if text:
            self._status_label.setText(text)
            self._status_label.setVisible(True)
        else:
            self._status_label.setVisible(False)

    def _animate_dots(self):
        self._dots_count = (self._dots_count + 1) % 4
        dots = "." * (self._dots_count + 1)
        base = self._status_label.text().rstrip(".")
        if not base:
            base = "Gemini is thinking"
        self._status_label.setText(f"{base}{dots}")

    def _scroll_to_bottom(self):
        vbar = self._scroll.verticalScrollBar()
        vbar.setValue(vbar.maximum())

    def showEvent(self, event):
        super().showEvent(event)
        self._populate_model_combo()
        self._load_session_chips()
        self._update_convo_count()
