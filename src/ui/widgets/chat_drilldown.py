"""
Alma Insights — Chat Drilldown (Multi-Mode Panel)

Embedded inside the existing DrilldownPanel via show_widget().
Provides 5 tab modes matching the Cambric mockups:

  Monitor  — Live tool calls, token usage, cost (dark bg, color-coded cards)
  Chats    — Session details + recent chat list
  Projects — Project folders with session counts
  Reports  — Cross-app report browser (AI, Smart, VOC)
  Incidents — Active incidents for investigation

The tab bar is drawn as small rounded buttons at the top.
Each tab swaps the content area below.
"""

import json
import logging
from datetime import datetime, timedelta

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QLineEdit, QStackedWidget, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QTimer

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_MID, ALMA_GREEN_LIGHT, ALMA_GREEN_SUBTLE,
    ALMA_WHITE, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_TEXT_ON_DARK, ALMA_BORDER, ALMA_BORDER_LIGHT, ALMA_HOVER_LIGHT,
    ALMA_BG_ELEVATED, ALMA_BG_INSET,
    ALMA_INFO, ALMA_WARNING, ALMA_SUCCESS, ALMA_ERROR,
)

logger = logging.getLogger("alma.chat_drilldown")

# Tab definitions
_TABS = [
    ("monitor", "Monitor"),
    ("chats", "Chats"),
    ("projects", "Projects"),
    ("reports", "Reports"),
    ("incidents", "Incidents"),
]

# Monitor event colors (left border)
_EVENT_COLORS = {
    "user": ALMA_GREEN_LIGHT,
    "context": ALMA_INFO,
    "tool": ALMA_WARNING,
    "response": ALMA_GREEN_SUBTLE,
}


def _relative_time(dt_str: str | None) -> str:
    """Convert ISO datetime to relative time string."""
    if not dt_str:
        return ""
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        now = datetime.utcnow()
        delta = now - dt.replace(tzinfo=None)
        if delta < timedelta(minutes=1):
            return "just now"
        if delta < timedelta(hours=1):
            m = int(delta.total_seconds() / 60)
            return f"{m}m ago"
        if delta < timedelta(days=1):
            h = int(delta.total_seconds() / 3600)
            return f"{h}h ago"
        if delta < timedelta(days=7):
            d = delta.days
            return f"{d}d ago"
        return dt.strftime("%b %d")
    except Exception:
        return ""


class ChatDrilldown(QWidget):
    """Multi-mode drilldown content widget for Gemini Chats.

    Signals:
        session_selected(session_id): user clicked a session to load
        report_selected(report_id): user clicked a report to chat about
        incident_selected(incident_id): user clicked an incident
    """

    session_selected = Signal(str)
    report_selected = Signal(str)
    incident_selected = Signal(str)

    def __init__(self, conn=None, parent=None):
        super().__init__(parent)
        self._conn = conn
        self._session_id = None  # current session for Monitor
        self._current_tab = "monitor"

        self._build_ui()

    def set_connection(self, conn):
        self._conn = conn

    def set_session_id(self, session_id: str | None):
        """Set the active session for Monitor tab."""
        self._session_id = session_id
        if self._current_tab == "monitor":
            self._refresh_monitor()

    # ═══════════════════════════════════════
    #  BUILD UI
    # ═══════════════════════════════════════

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Tab bar ──
        tab_bar = QWidget()
        tab_bar.setStyleSheet(f"background: {ALMA_BG_ELEVATED};")
        tab_layout = QHBoxLayout(tab_bar)
        tab_layout.setContentsMargins(8, 8, 8, 4)
        tab_layout.setSpacing(4)

        self._tab_buttons: dict[str, QPushButton] = {}
        for key, label in _TABS:
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setCheckable(True)
            btn.setStyleSheet(self._tab_style(False))
            btn.clicked.connect(lambda _, k=key: self._switch_tab(k))
            tab_layout.addWidget(btn)
            self._tab_buttons[key] = btn

        tab_layout.addStretch()
        layout.addWidget(tab_bar)

        # Separator
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {ALMA_BORDER_LIGHT};")
        layout.addWidget(sep)

        # ── Stacked content ──
        self._stack = QStackedWidget()
        self._panels: dict[str, QWidget] = {}

        self._panels["monitor"] = self._build_monitor_panel()
        self._panels["chats"] = self._build_chats_panel()
        self._panels["projects"] = self._build_projects_panel()
        self._panels["reports"] = self._build_reports_panel()
        self._panels["incidents"] = self._build_incidents_panel()

        for key in ["monitor", "chats", "projects", "reports", "incidents"]:
            self._stack.addWidget(self._panels[key])

        layout.addWidget(self._stack, 1)

        # Default to monitor tab
        self._switch_tab("monitor")

    def _tab_style(self, active: bool) -> str:
        if active:
            return f"""
                QPushButton {{
                    background: {ALMA_GREEN_DARK}; color: {ALMA_TEXT_ON_DARK};
                    border: none; border-radius: 10px;
                    padding: 4px 10px; font-size: 11px; font-weight: 600;
                }}
            """
        return f"""
            QPushButton {{
                background: transparent; color: {ALMA_TEXT_MID};
                border: none; border-radius: 10px;
                padding: 4px 10px; font-size: 11px; font-weight: 500;
            }}
            QPushButton:hover {{ background: {ALMA_HOVER_LIGHT}; }}
        """

    def _switch_tab(self, key: str):
        """Switch to the given tab."""
        self._current_tab = key
        for k, btn in self._tab_buttons.items():
            btn.setStyleSheet(self._tab_style(k == key))
            btn.setChecked(k == key)

        idx = list(self._panels.keys()).index(key)
        self._stack.setCurrentIndex(idx)

        # Refresh content
        if key == "monitor":
            self._refresh_monitor()
        elif key == "chats":
            self._refresh_chats()
        elif key == "projects":
            self._refresh_projects()
        elif key == "reports":
            self._refresh_reports()
        elif key == "incidents":
            self._refresh_incidents()

    def show_mode(self, mode: str):
        """External API — switch to a mode by drilldown_mode string."""
        mode_to_tab = {
            "observability": "monitor",
            "chat_history": "chats",
            "projects": "projects",
            "report_browser": "reports",
            "incidents": "incidents",
        }
        tab = mode_to_tab.get(mode, mode)
        if tab in self._tab_buttons:
            self._switch_tab(tab)

    # ═══════════════════════════════════════
    #  MONITOR PANEL
    # ═══════════════════════════════════════

    def _build_monitor_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header stats row
        self._monitor_header = QWidget()
        self._monitor_header.setStyleSheet(f"background: {ALMA_GREEN_DARK};")
        header_layout = QHBoxLayout(self._monitor_header)
        header_layout.setContentsMargins(12, 8, 12, 8)
        header_layout.setSpacing(16)

        self._monitor_stat_labels = {}
        for key, label in [("tokens_in", "Tokens In"), ("tokens_out", "Tokens Out"),
                           ("cost", "Cost"), ("tools", "Tools")]:
            stat_w = QVBoxLayout()
            stat_w.setSpacing(0)
            val = QLabel("0")
            val.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_CREAM}; background: transparent;")
            val.setAlignment(Qt.AlignCenter)
            stat_w.addWidget(val)
            lbl = QLabel(label)
            lbl.setStyleSheet(f"font-size: 9px; color: {ALMA_TEXT_LIGHT}; background: transparent;")
            lbl.setAlignment(Qt.AlignCenter)
            stat_w.addWidget(lbl)
            header_layout.addLayout(stat_w)
            self._monitor_stat_labels[key] = val

        layout.addWidget(self._monitor_header)

        # "LIVE" indicator
        live_bar = QWidget()
        live_bar.setStyleSheet(f"background: {ALMA_GREEN_DARK};")
        live_layout = QHBoxLayout(live_bar)
        live_layout.setContentsMargins(12, 2, 12, 6)
        live_lbl = QLabel("\u25cf LIVE")
        live_lbl.setStyleSheet(f"font-size: 10px; font-weight: 700; color: {ALMA_SUCCESS}; background: transparent;")
        live_layout.addWidget(live_lbl)
        live_layout.addStretch()
        layout.addWidget(live_bar)

        # Event feed (scrollable)
        self._monitor_scroll = QScrollArea()
        self._monitor_scroll.setWidgetResizable(True)
        self._monitor_scroll.setFrameShape(QFrame.NoFrame)
        self._monitor_scroll.setStyleSheet(f"""
            QScrollArea {{ background: {ALMA_GREEN_DARK}; border: none; }}
        """)

        self._monitor_feed = QWidget()
        self._monitor_feed.setStyleSheet(f"background: {ALMA_GREEN_DARK};")
        self._monitor_feed_layout = QVBoxLayout(self._monitor_feed)
        self._monitor_feed_layout.setContentsMargins(8, 4, 8, 8)
        self._monitor_feed_layout.setSpacing(6)
        self._monitor_feed_layout.addStretch()

        self._monitor_scroll.setWidget(self._monitor_feed)
        layout.addWidget(self._monitor_scroll, 1)

        return panel

    def _refresh_monitor(self):
        """Refresh the Monitor tab with current session data."""
        # Clear feed
        while self._monitor_feed_layout.count() > 1:
            item = self._monitor_feed_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._conn or not self._session_id:
            self._add_monitor_empty("No active session")
            return

        try:
            # Load messages + tool executions
            msgs = self._conn.execute(
                """SELECT ordinal, role, content, created_at,
                          tokens_in, tokens_out, cost_usd, latency_ms, model_used
                   FROM chat_messages WHERE session_id = ?
                   ORDER BY ordinal""",
                (self._session_id,),
            ).fetchall()

            tools = self._conn.execute(
                """SELECT message_id, tool_name, args_json, result_rows,
                          elapsed_ms, tables_touched, created_at
                   FROM chat_tool_executions WHERE session_id = ?
                   ORDER BY created_at""",
                (self._session_id,),
            ).fetchall()

            # Aggregate stats
            total_in = sum(r[4] or 0 for r in msgs)
            total_out = sum(r[5] or 0 for r in msgs)
            total_cost = sum(r[6] or 0 for r in msgs)
            total_tools = len(tools)

            self._monitor_stat_labels["tokens_in"].setText(f"{total_in:,}")
            self._monitor_stat_labels["tokens_out"].setText(f"{total_out:,}")
            self._monitor_stat_labels["cost"].setText(f"${total_cost:.4f}")
            self._monitor_stat_labels["tools"].setText(str(total_tools))

            # Build event feed
            tool_idx = 0
            for msg in msgs:
                role = msg[1]
                content = msg[2]
                ts = msg[3]
                time_str = ts[11:19] if ts and len(ts) > 18 else ""

                if role == "user":
                    preview = (content[:60] + "...") if len(content) > 60 else content
                    self._add_monitor_event("user", time_str, "USER", [
                        f'"{preview}"',
                    ])

                elif role == "assistant":
                    tokens_in = msg[4] or 0
                    tokens_out = msg[5] or 0
                    cost = msg[6] or 0
                    latency = msg[7] or 0
                    model = msg[8] or ""

                    # Insert tool events that occurred before this response
                    while tool_idx < len(tools):
                        self._add_monitor_event("tool", "", f"TOOL CALL \u203a {tools[tool_idx][1]}", [
                            f"result: {tools[tool_idx][3] or 0} rows",
                            f"elapsed: {tools[tool_idx][4] or 0}ms",
                        ])
                        tool_idx += 1

                    self._add_monitor_event("response", time_str, "RESPONSE", [
                        f"tokens_in: {tokens_in:,}",
                        f"tokens_out: {tokens_out:,}",
                        f"cost: ${cost:.4f}",
                        f"latency: {latency:,}ms",
                        f"model: {model}",
                    ])

        except Exception as e:
            self._add_monitor_empty(f"Error: {e}")

    def _add_monitor_event(self, event_type: str, time_str: str, header: str, details: list[str]):
        """Add a color-coded event card to the Monitor feed."""
        color = _EVENT_COLORS.get(event_type, ALMA_TEXT_LIGHT)
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background: rgba(255,255,255,0.05);
                border-left: 3px solid {color};
                border-radius: 4px;
                margin: 0px;
            }}
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 6, 8, 6)
        card_layout.setSpacing(2)

        # Header row
        header_row = QHBoxLayout()
        if time_str:
            ts_lbl = QLabel(time_str)
            ts_lbl.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; background: transparent;")
            header_row.addWidget(ts_lbl)
        h_lbl = QLabel(header)
        h_lbl.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {color}; background: transparent;")
        header_row.addWidget(h_lbl)
        header_row.addStretch()
        card_layout.addLayout(header_row)

        # Detail lines
        for detail in details:
            d_lbl = QLabel(detail)
            d_lbl.setWordWrap(True)
            d_lbl.setStyleSheet(f"font-size: 11px; color: {ALMA_CREAM}; background: transparent;")
            card_layout.addWidget(d_lbl)

        self._monitor_feed_layout.insertWidget(
            self._monitor_feed_layout.count() - 1, card
        )

    def _add_monitor_empty(self, message: str):
        lbl = QLabel(message)
        lbl.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT}; background: transparent; padding: 20px;")
        lbl.setAlignment(Qt.AlignCenter)
        self._monitor_feed_layout.insertWidget(
            self._monitor_feed_layout.count() - 1, lbl
        )

    # ═══════════════════════════════════════
    #  CHATS PANEL
    # ═══════════════════════════════════════

    def _build_chats_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # FTS5 search bar
        search_w = QWidget()
        search_w.setStyleSheet(f"background: {ALMA_BG_ELEVATED};")
        search_layout = QHBoxLayout(search_w)
        search_layout.setContentsMargins(8, 8, 8, 4)

        self._chats_search = QLineEdit()
        self._chats_search.setPlaceholderText("Search chats...")
        self._chats_search.setStyleSheet(f"""
            QLineEdit {{
                background: {ALMA_BG_INSET};
                border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 14px;
                padding: 6px 14px;
                font-size: 12px;
                color: {ALMA_TEXT_DARK};
            }}
            QLineEdit:focus {{
                border-color: {ALMA_GREEN_MID};
            }}
        """)
        self._chats_search.textChanged.connect(self._on_chat_search)
        search_layout.addWidget(self._chats_search)
        layout.addWidget(search_w)

        # Session info header (when a session is active)
        self._chats_info = QWidget()
        self._chats_info.setStyleSheet(f"background: {ALMA_BG_ELEVATED};")
        info_layout = QVBoxLayout(self._chats_info)
        info_layout.setContentsMargins(12, 10, 12, 10)
        info_layout.setSpacing(4)

        info_title = QLabel("Chat Details")
        info_title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; background: transparent;")
        info_layout.addWidget(info_title)

        self._chats_detail_labels: dict[str, QLabel] = {}
        for key, label in [("chat_id", "Chat ID"), ("started", "Started"),
                           ("messages", "Messages"), ("model", "Model"),
                           ("corpus", "Corpus"), ("tokens", "Token Usage")]:
            row = QHBoxLayout()
            k_lbl = QLabel(f"{label}:")
            k_lbl.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; background: transparent; min-width: 70px;")
            row.addWidget(k_lbl)
            v_lbl = QLabel("")
            v_lbl.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_DARK}; font-weight: 500; background: transparent;")
            row.addWidget(v_lbl, 1)
            info_layout.addLayout(row)
            self._chats_detail_labels[key] = v_lbl

        layout.addWidget(self._chats_info)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {ALMA_BORDER_LIGHT};")
        layout.addWidget(sep)

        # Recent chats heading
        heading = QLabel("Recent Chats")
        heading.setStyleSheet(f"""
            font-size: 12px; font-weight: 700; color: {ALMA_TEXT_DARK};
            padding: 10px 12px 6px 12px; background: transparent;
        """)
        layout.addWidget(heading)

        # Session list (scrollable)
        self._chats_scroll = QScrollArea()
        self._chats_scroll.setWidgetResizable(True)
        self._chats_scroll.setFrameShape(QFrame.NoFrame)
        self._chats_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self._chats_list = QWidget()
        self._chats_list_layout = QVBoxLayout(self._chats_list)
        self._chats_list_layout.setContentsMargins(8, 0, 8, 8)
        self._chats_list_layout.setSpacing(6)
        self._chats_list_layout.addStretch()

        self._chats_scroll.setWidget(self._chats_list)
        layout.addWidget(self._chats_scroll, 1)

        return panel

    def _on_chat_search(self, query: str):
        """Handle FTS5 search in the Chats panel."""
        query = query.strip()
        if not query or not self._conn:
            self._refresh_chats()
            return

        # Clear current list
        while self._chats_list_layout.count() > 1:
            item = self._chats_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        try:
            from src.services.chat_session import search_messages
            results = search_messages(query, limit=20, conn=self._conn)
            if not results:
                empty = QLabel("No matches found.")
                empty.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT}; padding: 20px;")
                empty.setAlignment(Qt.AlignCenter)
                self._chats_list_layout.insertWidget(0, empty)
                return

            # Group by session, show first match per session
            seen_sessions = set()
            for r in results:
                sid = r["session_id"]
                if sid in seen_sessions:
                    continue
                seen_sessions.add(sid)
                title = r.get("session_title") or "Untitled"
                if len(title) > 35:
                    title = title[:32] + "..."
                preview = r["content"][:80] + "..." if len(r["content"]) > 80 else r["content"]

                card = self._make_session_card(sid, title, 0, 0, "")
                self._chats_list_layout.insertWidget(
                    self._chats_list_layout.count() - 1, card
                )
        except Exception as e:
            logger.debug("Chat search failed: %s", e)

    def _refresh_chats(self):
        """Refresh the Chats tab with session details and recent list."""
        # Update session details if active
        if self._conn and self._session_id:
            self._refresh_chats_detail()

        # Refresh recent chats list
        while self._chats_list_layout.count() > 1:
            item = self._chats_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._conn:
            return

        # Try v_session_summary (post-009), fall back to raw chat_sessions
        try:
            rows = self._conn.execute(
                """SELECT session_id, title, message_count, total_cost,
                          last_message_at, first_question
                   FROM v_session_summary
                   ORDER BY COALESCE(last_message_at, updated_at) DESC
                   LIMIT 20""",
            ).fetchall()
        except Exception:
            rows = None

        if rows is None:
            # Pre-009 fallback: raw chat_sessions
            try:
                rows = self._conn.execute(
                    """SELECT session_id, title, NULL, NULL, updated_at, NULL
                       FROM chat_sessions
                       ORDER BY updated_at DESC LIMIT 20""",
                ).fetchall()
            except Exception as e:
                logger.debug("Recent chats fallback failed: %s", e)
                return

        for row in rows:
            sid = row[0]
            title = row[1] or row[5] or self._extract_first_question(sid) or "Untitled"
            if len(title) > 35:
                title = title[:32] + "..."
            count = row[2] or 0
            cost = row[3] or 0
            time_str = _relative_time(str(row[4])) if row[4] else ""

            card = self._make_session_card(sid, title, count, cost, time_str)
            self._chats_list_layout.insertWidget(
                self._chats_list_layout.count() - 1, card
            )

    def _refresh_chats_detail(self):
        """Populate Chat Details section for the active session."""
        try:
            row = self._conn.execute(
                "SELECT * FROM v_session_summary WHERE session_id = ?",
                (self._session_id,),
            ).fetchone()
        except Exception:
            row = None

        if row:
            self._chats_detail_labels["chat_id"].setText(
                str(row[0])[:12] + "..." if row[0] else ""
            )
            self._chats_detail_labels["started"].setText(
                _relative_time(str(row[4])) if row[4] else ""
            )
            self._chats_detail_labels["messages"].setText(str(row[8] or 0))
            try:
                model_row = self._conn.execute(
                    "SELECT model_used FROM chat_messages WHERE session_id = ? AND model_used IS NOT NULL ORDER BY ordinal DESC LIMIT 1",
                    (self._session_id,),
                ).fetchone()
                self._chats_detail_labels["model"].setText(str(model_row[0]) if model_row else "")
            except Exception:
                self._chats_detail_labels["model"].setText("")
            try:
                from src.data.source_registry import SourceRegistry
                from src.data.warehouse_query import WarehouseQuery
                _wq = WarehouseQuery(self._conn, SourceRegistry(self._conn))
                convo_count = _wq.get_ticket_count()
                self._chats_detail_labels["corpus"].setText(f"ticket_db ({convo_count} conversations)")
            except Exception:
                self._chats_detail_labels["corpus"].setText("")
            total_in = row[11] or 0 if len(row) > 11 else 0
            total_out = row[12] or 0 if len(row) > 12 else 0
            self._chats_detail_labels["tokens"].setText(f"{total_in + total_out:,} total")
        else:
            # Pre-009 fallback: basic info from chat_sessions
            try:
                srow = self._conn.execute(
                    "SELECT session_id, created_at, source_page FROM chat_sessions WHERE session_id = ?",
                    (self._session_id,),
                ).fetchone()
                if srow:
                    self._chats_detail_labels["chat_id"].setText(str(srow[0])[:12] + "...")
                    self._chats_detail_labels["started"].setText(_relative_time(str(srow[1])) if srow[1] else "")
            except Exception:
                pass

    def _extract_first_question(self, session_id: str) -> str | None:
        """Extract first user message from JSON blob (pre-009 fallback)."""
        try:
            row = self._conn.execute(
                "SELECT messages FROM chat_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row and row[0]:
                import json
                msgs = json.loads(row[0])
                for m in msgs:
                    if isinstance(m, dict) and m.get("role") == "user":
                        content = m.get("content", "")
                        return content[:40] + "..." if len(content) > 40 else content
        except Exception:
            pass
        return None

    def _make_session_card(self, session_id: str, title: str, msg_count: int,
                           cost: float, time_str: str) -> QFrame:
        card = QFrame()
        card.setCursor(Qt.PointingHandCursor)
        card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED};
                border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 6px;
            }}
            QFrame:hover {{ background: {ALMA_HOVER_LIGHT}; }}
        """)
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(10, 8, 10, 8)
        card_layout.setSpacing(8)

        left = QVBoxLayout()
        left.setSpacing(2)
        t_lbl = QLabel(title)
        t_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {ALMA_TEXT_DARK}; background: transparent;")
        left.addWidget(t_lbl)

        meta = QLabel(f"{msg_count} messages" + (f" \u00b7 ${cost:.3f}" if cost > 0 else ""))
        meta.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; background: transparent;")
        left.addWidget(meta)

        card_layout.addLayout(left, 1)

        time_lbl = QLabel(time_str)
        time_lbl.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; background: transparent;")
        card_layout.addWidget(time_lbl)

        card.mousePressEvent = lambda e, sid=session_id: self.session_selected.emit(sid)
        return card

    # ═══════════════════════════════════════
    #  PROJECTS PANEL
    # ═══════════════════════════════════════

    def _build_projects_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._projects_scroll = QScrollArea()
        self._projects_scroll.setWidgetResizable(True)
        self._projects_scroll.setFrameShape(QFrame.NoFrame)
        self._projects_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self._projects_list = QWidget()
        self._projects_list_layout = QVBoxLayout(self._projects_list)
        self._projects_list_layout.setContentsMargins(8, 8, 8, 8)
        self._projects_list_layout.setSpacing(6)
        self._projects_list_layout.addStretch()

        self._projects_scroll.setWidget(self._projects_list)
        layout.addWidget(self._projects_scroll, 1)

        return panel

    def _refresh_projects(self):
        """Refresh the Projects tab."""
        while self._projects_list_layout.count() > 1:
            item = self._projects_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._conn:
            return

        try:
            from src.services.chat_session import list_projects
            projects = list_projects(conn=self._conn)

            if not projects:
                empty = QLabel("No projects yet.\nUse the Explore menu to create one.")
                empty.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT}; padding: 20px;")
                empty.setAlignment(Qt.AlignCenter)
                empty.setWordWrap(True)
                self._projects_list_layout.insertWidget(0, empty)
                return

            for proj in projects:
                card = QFrame()
                card.setStyleSheet(f"""
                    QFrame {{
                        background: {ALMA_BG_ELEVATED};
                        border: 1px solid {ALMA_BORDER_LIGHT};
                        border-radius: 6px;
                    }}
                    QFrame:hover {{ background: {ALMA_HOVER_LIGHT}; }}
                """)
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(12, 10, 12, 10)
                card_layout.setSpacing(4)

                name_lbl = QLabel(proj["name"])
                name_lbl.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {ALMA_TEXT_DARK}; background: transparent;")
                card_layout.addWidget(name_lbl)

                count = proj.get("session_count", 0)
                last = _relative_time(proj.get("last_active"))
                meta = QLabel(f"{count} chat{'s' if count != 1 else ''}" +
                              (f" \u00b7 Updated {last}" if last else ""))
                meta.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; background: transparent;")
                card_layout.addWidget(meta)

                self._projects_list_layout.insertWidget(
                    self._projects_list_layout.count() - 1, card
                )
        except Exception as e:
            logger.debug("Projects refresh failed: %s", e)

    # ═══════════════════════════════════════
    #  REPORTS PANEL
    # ═══════════════════════════════════════

    def _build_reports_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._reports_scroll = QScrollArea()
        self._reports_scroll.setWidgetResizable(True)
        self._reports_scroll.setFrameShape(QFrame.NoFrame)
        self._reports_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self._reports_list = QWidget()
        self._reports_list_layout = QVBoxLayout(self._reports_list)
        self._reports_list_layout.setContentsMargins(8, 8, 8, 8)
        self._reports_list_layout.setSpacing(6)
        self._reports_list_layout.addStretch()

        self._reports_scroll.setWidget(self._reports_list)
        layout.addWidget(self._reports_scroll, 1)

        return panel

    def _refresh_reports(self):
        """Refresh the Reports tab with cross-app report list."""
        while self._reports_list_layout.count() > 1:
            item = self._reports_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._conn:
            return

        try:
            rows = self._conn.execute(
                """SELECT ar.run_id, ar.run_date, ar.prompt_template, ar.trc_filter,
                          ar.ticket_count, ar.cost_usd, ar.model_used, ar.source
                   FROM analysis_runs ar
                   ORDER BY ar.run_date DESC
                   LIMIT 30""",
            ).fetchall()

            if not rows:
                empty = QLabel("No reports generated yet.")
                empty.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT}; padding: 20px;")
                empty.setAlignment(Qt.AlignCenter)
                self._reports_list_layout.insertWidget(0, empty)
                return

            for row in rows:
                run_id = row[0]
                date_str = str(row[1])[:10] if row[1] else ""
                template = row[2] or "Report"
                trc = row[3] or ""
                ticket_count = row[4] or 0
                cost = row[5] or 0

                card = QFrame()
                card.setCursor(Qt.PointingHandCursor)
                card.setStyleSheet(f"""
                    QFrame {{
                        background: {ALMA_BG_ELEVATED};
                        border: 1px solid {ALMA_BORDER_LIGHT};
                        border-radius: 6px;
                    }}
                    QFrame:hover {{ background: {ALMA_HOVER_LIGHT}; }}
                """)
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(12, 8, 12, 8)
                card_layout.setSpacing(4)

                # Title with type badge
                title_row = QHBoxLayout()
                badge = QLabel(template[:20])
                badge.setStyleSheet(f"""
                    background: {ALMA_BG_INSET}; color: {ALMA_TEXT_MID};
                    border-radius: 6px; padding: 2px 6px;
                    font-size: 9px; font-weight: 700;
                """)
                title_row.addWidget(badge)
                title_row.addStretch()
                date_lbl = QLabel(date_str)
                date_lbl.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; background: transparent;")
                title_row.addWidget(date_lbl)
                card_layout.addLayout(title_row)

                meta = QLabel(
                    (f"TRC: {trc} \u00b7 " if trc else "") +
                    f"{ticket_count} tickets" +
                    (f" \u00b7 ${cost:.3f}" if cost > 0 else "")
                )
                meta.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; background: transparent;")
                card_layout.addWidget(meta)

                card.mousePressEvent = lambda e, rid=run_id: self.report_selected.emit(rid)
                self._reports_list_layout.insertWidget(
                    self._reports_list_layout.count() - 1, card
                )
        except Exception as e:
            logger.debug("Reports refresh failed: %s", e)

    # ═══════════════════════════════════════
    #  INCIDENTS PANEL
    # ═══════════════════════════════════════

    def _build_incidents_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._incidents_scroll = QScrollArea()
        self._incidents_scroll.setWidgetResizable(True)
        self._incidents_scroll.setFrameShape(QFrame.NoFrame)
        self._incidents_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self._incidents_list = QWidget()
        self._incidents_list_layout = QVBoxLayout(self._incidents_list)
        self._incidents_list_layout.setContentsMargins(8, 8, 8, 8)
        self._incidents_list_layout.setSpacing(6)
        self._incidents_list_layout.addStretch()

        self._incidents_scroll.setWidget(self._incidents_list)
        layout.addWidget(self._incidents_scroll, 1)

        return panel

    def _refresh_incidents(self):
        """Refresh the Incidents tab with active incidents.

        Uses actual incident_flags schema: flag_id, trc_code, flag_type,
        theta_level (as severity), direction, status (not 'active').
        """
        while self._incidents_list_layout.count() > 1:
            item = self._incidents_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._conn:
            return

        try:
            rows = self._conn.execute(
                """SELECT flag_id, trc_code, flag_type, theta_level,
                          direction, observed_value, status, created_at
                   FROM incident_flags
                   WHERE status = 'open'
                   ORDER BY
                     CASE theta_level WHEN 'HIGH' THEN 1 WHEN 'MED' THEN 2 ELSE 3 END,
                     created_at DESC
                   LIMIT 20""",
            ).fetchall()

            if not rows:
                empty = QLabel("No active incidents.")
                empty.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT}; padding: 20px;")
                empty.setAlignment(Qt.AlignCenter)
                self._incidents_list_layout.insertWidget(0, empty)
                return

            severity_colors = {
                "HIGH": ALMA_ERROR,
                "MED": ALMA_WARNING,
                "LOW": ALMA_INFO,
            }

            for row in rows:
                flag_id = str(row[0])
                trc = row[1] or ""
                flag_type = row[2] or "flag"
                theta_level = row[3] or "LOW"
                direction = row[4] or ""
                observed = row[5]
                time_str = _relative_time(str(row[7])) if row[7] else ""

                sev_color = severity_colors.get(theta_level, ALMA_INFO)

                # Build description from flag_type + direction
                desc_text = f"{flag_type.replace('_', ' ').title()}"
                if direction:
                    desc_text += f" ({direction})"

                card = QFrame()
                card.setCursor(Qt.PointingHandCursor)
                card.setStyleSheet(f"""
                    QFrame {{
                        background: {ALMA_BG_ELEVATED};
                        border: 1px solid {ALMA_BORDER_LIGHT};
                        border-left: 3px solid {sev_color};
                        border-radius: 6px;
                    }}
                    QFrame:hover {{ background: {ALMA_HOVER_LIGHT}; }}
                """)
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(12, 8, 12, 8)
                card_layout.setSpacing(4)

                # Title row: description + severity badge
                title_row = QHBoxLayout()
                desc_lbl = QLabel(desc_text)
                desc_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {ALMA_TEXT_DARK}; background: transparent;")
                title_row.addWidget(desc_lbl, 1)

                badge = QLabel(theta_level)
                badge.setStyleSheet(f"""
                    background: {sev_color}; color: white;
                    border-radius: 6px; padding: 2px 8px;
                    font-size: 9px; font-weight: 700;
                """)
                title_row.addWidget(badge)
                card_layout.addLayout(title_row)

                meta_parts = [trc]
                if observed is not None:
                    meta_parts.append(f"value: {observed:.1f}")
                if time_str:
                    meta_parts.append(time_str)
                meta = QLabel(" \u00b7 ".join(p for p in meta_parts if p))
                meta.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; background: transparent;")
                card_layout.addWidget(meta)

                card.mousePressEvent = lambda e, iid=flag_id: self.incident_selected.emit(iid)
                self._incidents_list_layout.insertWidget(
                    self._incidents_list_layout.count() - 1, card
                )
        except Exception as e:
            logger.debug("Incidents refresh failed: %s", e)
