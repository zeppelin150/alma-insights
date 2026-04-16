"""
Alma Insights — Ticket Detail Panel Widget
Tabbed detail view for a single ticket: Overview, NLP Data, Timeline, Related.

Session 4: Multi-source persistent database architecture.
"""

import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTabWidget,
    QScrollArea, QFrame, QGridLayout, QSizePolicy,
)
from PySide6.QtCore import Qt

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_CREAM, ALMA_WHITE, ALMA_TEXT_DARK,
    ALMA_TEXT_MID, ALMA_TEXT_LIGHT, ALMA_BORDER_LIGHT, ALMA_BG_ELEVATED,
    ALMA_ERROR, ALMA_INFO,
)

logger = logging.getLogger("alma.ui.ticket_detail_panel")


class TicketDetailPanel(QWidget):
    """Displays detailed information for a selected ticket.

    Has 4 tabs: Overview, NLP Data, Timeline, Related.
    Populated by set_ticket_data(row_dict).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = None
        self._nlp_data = None
        self._build_ui()
        self._show_placeholder()  # Show placeholder until a ticket is selected

    def _show_placeholder(self):
        """Show a placeholder message when no ticket is selected."""
        self._header_label.setText("── Ticket Detail ── Select a ticket above to view details")
        self._tabs.setVisible(False)

    def _show_detail(self):
        """Show the detail tabs when a ticket is selected."""
        self._header_label.setText("── Ticket Detail ──")
        self._tabs.setVisible(True)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(0)

        # Section header
        self._header_label = QLabel("── Ticket Detail ──")
        self._header_label.setStyleSheet(
            f"font-size: 12px; font-weight: 700; color: {ALMA_TEXT_MID}; "
            f"margin-bottom: 4px;"
        )
        layout.addWidget(self._header_label)

        # Tabs
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: 1px solid {ALMA_BORDER_LIGHT};
                background: {ALMA_WHITE};
                border-radius: 4px;
            }}
            QTabBar::tab {{
                padding: 6px 14px;
                font-size: 12px;
                border: none;
                background: transparent;
                color: {ALMA_TEXT_MID};
            }}
            QTabBar::tab:selected {{
                font-weight: 600;
                color: {ALMA_GREEN_DARK};
                border-bottom: 2px solid {ALMA_GREEN_DARK};
            }}
        """)

        self._overview_tab = self._build_overview_tab()
        self._nlp_tab = self._build_nlp_tab()
        self._timeline_tab = self._build_timeline_tab()
        self._related_tab = self._build_related_tab()

        self._tabs.addTab(self._overview_tab, "Overview")
        self._tabs.addTab(self._nlp_tab, "NLP Data")
        self._tabs.addTab(self._timeline_tab, "Timeline")
        self._tabs.addTab(self._related_tab, "Related")

        layout.addWidget(self._tabs)

    # ── Tab Builders ──────────────────────────────

    def _build_overview_tab(self):
        widget = QWidget()
        self._overview_grid = QGridLayout(widget)
        self._overview_grid.setContentsMargins(12, 8, 12, 8)
        self._overview_grid.setSpacing(6)

        # Labels that will be populated
        self._overview_fields = {}
        fields = [
            ("TRC", 0, 0), ("Client", 0, 2),
            ("Classification", 1, 0), ("Provider", 1, 2),
            ("Sentiment", 2, 0), ("Friction", 2, 2),
            ("CSAT", 3, 0), ("Status", 3, 2),
            ("Created", 4, 0), ("Solved", 4, 2),
            ("Messages", 5, 0), ("Source", 5, 2),
        ]
        for name, row, col in fields:
            lbl = QLabel(f"{name}:")
            lbl.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {ALMA_TEXT_LIGHT};")
            val = QLabel("—")
            val.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_DARK};")
            val.setWordWrap(True)
            self._overview_grid.addWidget(lbl, row, col)
            self._overview_grid.addWidget(val, row, col + 1)
            self._overview_fields[name] = val

        return widget

    def _build_nlp_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        self._nlp_label = QLabel("Select a ticket to view NLP data.")
        self._nlp_label.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID};")
        self._nlp_label.setWordWrap(True)
        self._nlp_label.setTextFormat(Qt.RichText)
        layout.addWidget(self._nlp_label)
        layout.addStretch()
        return widget

    def _build_timeline_tab(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        self._timeline_label = QLabel("Select a ticket to view timeline.")
        self._timeline_label.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID};")
        self._timeline_label.setWordWrap(True)
        self._timeline_label.setTextFormat(Qt.RichText)
        layout.addWidget(self._timeline_label)
        layout.addStretch()
        scroll.setWidget(inner)
        return scroll

    def _build_related_tab(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        self._related_label = QLabel("Select a ticket to view related tickets.")
        self._related_label.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID};")
        self._related_label.setWordWrap(True)
        self._related_label.setTextFormat(Qt.RichText)
        layout.addWidget(self._related_label)
        layout.addStretch()
        scroll.setWidget(inner)
        return scroll

    # ── Public API ────────────────────────────────

    def set_ticket_data(self, data: dict, nlp_data: dict | None = None,
                        related_tickets: list | None = None,
                        comments: list | None = None):
        """Populate all tabs with data from a selected ticket row."""
        self._data = data
        self._nlp_data = nlp_data

        if not data or not isinstance(data, dict):
            self._show_placeholder()
            return

        self._show_detail()

        self.show()
        tid = data.get("ticket_id", "")
        self._header_label.setText(f"── Ticket Detail: #{tid} ──")

        # Overview tab
        mapping = {
            "TRC": data.get("trc_code", "—"),
            "Client": data.get("client_id", "—"),
            "Classification": data.get("trc_label", "—"),
            "Provider": data.get("provider_id", "—"),
            "Sentiment": self._format_sentiment(data),
            "Friction": data.get("friction", "—"),
            "CSAT": str(data.get("csat_score", "—")),
            "Status": data.get("status", "—"),
            "Created": str(data.get("created_at", "—"))[:10],
            "Solved": str(data.get("solved_at", "—"))[:10] if data.get("solved_at") else "—",
            "Messages": str(data.get("message_count", "—")),
            "Source": data.get("source_name", "—"),
        }
        for name, val in mapping.items():
            if name in self._overview_fields:
                self._overview_fields[name].setText(str(val) if val else "—")

        # NLP tab
        self._populate_nlp_tab(data, nlp_data)

        # Timeline tab
        self._populate_timeline_tab(data, comments)

        # Related tab
        self._populate_related_tab(data, related_tickets)

    def clear(self):
        """Reset to empty state."""
        self._data = None
        self._nlp_data = None
        self.hide()

    # ── Formatters ────────────────────────────────

    def _format_sentiment(self, data):
        score = data.get("sentiment_score")
        if score is not None:
            try:
                s = float(score)
                label = "Positive" if s > 0.2 else ("Negative" if s < -0.2 else "Neutral")
                return f"{label} ({s:+.2f})"
            except (ValueError, TypeError):
                pass
        return "—"

    def _populate_nlp_tab(self, data, nlp_data):
        parts = []
        if nlp_data:
            if nlp_data.get("ngrams"):
                ngrams = ", ".join(f'"{n}"' for n in nlp_data["ngrams"][:8])
                parts.append(f"<b>Ngrams:</b> {ngrams}")
            if nlp_data.get("issue_types"):
                badges = " ".join(
                    f'<span style="background: #FFEBEE; color: #C62828; padding: 2px 8px; '
                    f'border-radius: 4px; font-size: 11px;">{it}</span>'
                    for it in nlp_data["issue_types"]
                )
                parts.append(f"<b>Issue Types:</b> {badges}")
            if nlp_data.get("entities"):
                parts.append(f"<b>Entities:</b> {', '.join(nlp_data['entities'][:10])}")

        if not parts:
            # Show basic info from the conversation data
            preview = data.get("thread_preview", "")
            if preview:
                parts.append(f"<b>Thread Preview:</b><br>{str(preview)[:500]}")
            else:
                parts.append("No NLP data available for this ticket.")

        self._nlp_label.setText("<br><br>".join(parts))

    def _populate_timeline_tab(self, data, comments=None):
        created = data.get("created_at", "?")
        solved = data.get("solved_at")
        msg_count = data.get("message_count", 0)
        client_msgs = data.get("client_messages", 0)
        agent_msgs = data.get("agent_messages", 0)

        parts = [
            f"<b>Created:</b> {str(created)[:16]}"
        ]
        if solved:
            parts.append(f"<b>Solved:</b> {str(solved)[:16]}")
        parts.append(
            f"<b>Messages:</b> {msg_count} total "
            f"({client_msgs} client, {agent_msgs} agent)"
        )

        if comments:
            parts.append("<br><b>Conversation Timeline:</b><hr>")
            for c in comments:
                author = c.get("author_name", "Unknown")
                role = c.get("author_role", "")
                ts = str(c.get("created_at", ""))[:16]
                body = c.get("body", "")
                if len(body) > 300:
                    body = body[:297] + "\u2026"
                # Color-code by role
                if role and "agent" in role.lower():
                    role_color = ALMA_INFO
                    role_badge = "Agent"
                elif role and ("end" in role.lower() or "client" in role.lower()):
                    role_color = ALMA_GREEN_DARK
                    role_badge = "Client"
                else:
                    role_color = ALMA_TEXT_LIGHT
                    role_badge = role or "Unknown"

                parts.append(
                    f'<span style="color: {role_color}; font-weight: 600;">'
                    f'{author}</span> '
                    f'<span style="background: {role_color}; color: white; '
                    f'padding: 1px 6px; border-radius: 3px; font-size: 10px;">'
                    f'{role_badge}</span> '
                    f'<span style="color: {ALMA_TEXT_LIGHT}; font-size: 11px;">'
                    f'{ts}</span><br>'
                    f'<span style="color: {ALMA_TEXT_DARK}; font-size: 12px;">'
                    f'{body}</span><br>'
                )
        elif msg_count and int(msg_count) > 0:
            parts.append(
                f"<br><i style='color: {ALMA_TEXT_LIGHT};'>"
                f"Comment data not available for this ticket.</i>"
            )

        self._timeline_label.setText("<br>".join(parts))

    def _populate_related_tab(self, data, related_tickets):
        """Show tickets sharing the same service issue (sub_cluster).

        Groups by semantic issue, not TRC — TRC codes can be misclassified.
        """
        tid = data.get("ticket_id", "")
        group_label = data.get("_related_group")  # sub_cluster set by warehouse page
        trc = data.get("trc_code", "?")

        if not related_tickets:
            if group_label:
                self._related_label.setText(
                    f"No other tickets found for issue "
                    f"<b>{group_label}</b>."
                )
            else:
                self._related_label.setText(
                    f"No NLP classification data — unable to find related tickets."
                )
            return

        # Header shows the semantic issue grouping
        if group_label:
            header = (
                f"<b>{len(related_tickets)} semantically similar tickets</b> "
                f"&middot; issue: <b>{group_label}</b>"
            )
        else:
            header = f"<b>{len(related_tickets)} related tickets</b>"

        parts = [header + "<br>"]
        for rt in related_tickets:
            rt_id = rt.get("ticket_id", "?")
            if str(rt_id) == str(tid):
                continue  # Skip self
            rt_date = str(rt.get("created_at", ""))[:10]
            rt_subj = rt.get("subject", "—")
            if len(rt_subj) > 65:
                rt_subj = rt_subj[:62] + "\u2026"
            rt_friction = rt.get("friction", "")
            rt_sentiment = rt.get("sentiment_polarity", "")
            rt_trc = rt.get("trc_code", "")
            sim = rt.get("similarity", 0)

            # Similarity badge with color coding
            if sim >= 0.90:
                sim_color = "#27ae60"  # green — near-identical
            elif sim >= 0.82:
                sim_color = "#2980b9"  # blue — strong match
            else:
                sim_color = ALMA_TEXT_LIGHT  # grey — moderate match
            sim_badge = (
                f'<span style="background: {sim_color}; color: white; '
                f'padding: 1px 6px; border-radius: 3px; font-size: 10px;">'
                f'{sim:.0%}</span>'
            ) if sim else ""

            # Enrichment badges
            badges = []
            if rt_trc:
                badges.append(f'<span style="color: {ALMA_TEXT_LIGHT};">{rt_trc}</span>')
            if rt_friction:
                badges.append(f'<span style="color: {ALMA_ERROR};">{rt_friction}</span>')
            if rt_sentiment:
                badges.append(rt_sentiment)
            badge_str = " &middot; ".join(badges) if badges else ""

            parts.append(
                f'{sim_badge} '
                f'<span style="font-weight: 600;">#{rt_id}</span> '
                f'<span style="color: {ALMA_TEXT_LIGHT};">{rt_date}</span> '
                f'{rt_subj}'
                f'{"<br>" + badge_str if badge_str else ""}<br>'
            )

        self._related_label.setText("".join(parts))
