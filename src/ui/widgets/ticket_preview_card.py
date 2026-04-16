"""
Alma Insights — Ticket Preview Card Widget (Build 11.0)

Compact QFrame showing ticket summary information
for use in the Evidence Panel and other contexts.
"""

from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel,
)
from PySide6.QtCore import Qt, Signal

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_BORDER_LIGHT, ALMA_BG_ELEVATED,
    ALMA_SUCCESS, ALMA_WARNING, ALMA_ERROR, ALMA_INFO,
)


_SEVERITY_COLORS = {
    "critical": ALMA_ERROR,
    "unusual": ALMA_WARNING,
    "normal": ALMA_TEXT_LIGHT,
}


class TicketPreviewCard(QFrame):
    """Compact ticket preview card."""

    clicked = Signal(str)  # ticket_id

    def __init__(self, ticket_data: dict, parent=None):
        super().__init__(parent)
        self._ticket_id = ticket_data.get("ticket_id", "")
        self.setCursor(Qt.PointingHandCursor)

        self.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED};
                border: none;
                border-radius: 8px;
            }}
            QFrame:hover {{
                border-color: {ALMA_GREEN_DARK};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        # Top row: ticket ID + severity
        top = QHBoxLayout()
        tid_lbl = QLabel(f"#{self._ticket_id}")
        tid_lbl.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {ALMA_GREEN_DARK};")
        top.addWidget(tid_lbl)

        severity = ticket_data.get("anomaly_flag", "normal") or "normal"
        color = _SEVERITY_COLORS.get(severity, ALMA_TEXT_LIGHT)
        sev_lbl = QLabel(severity.title())
        sev_lbl.setStyleSheet(f"font-size: 10px; font-weight: 600; color: {color};")
        top.addStretch()
        top.addWidget(sev_lbl)
        layout.addLayout(top)

        # Snippet
        snippet = ticket_data.get("issue_snippet", "")
        if len(snippet) > 120:
            snippet = snippet[:120] + "..."
        snip_lbl = QLabel(snippet)
        snip_lbl.setWordWrap(True)
        snip_lbl.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_MID};")
        layout.addWidget(snip_lbl)

        # Metadata row
        meta_parts = []
        if ticket_data.get("trc_label"):
            meta_parts.append(ticket_data["trc_label"])
        if ticket_data.get("friction_type"):
            meta_parts.append(ticket_data["friction_type"])
        if ticket_data.get("sentiment_polarity"):
            meta_parts.append(ticket_data["sentiment_polarity"])

        if meta_parts:
            meta_lbl = QLabel(" · ".join(meta_parts))
            meta_lbl.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT};")
            layout.addWidget(meta_lbl)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._ticket_id)
        super().mousePressEvent(event)
