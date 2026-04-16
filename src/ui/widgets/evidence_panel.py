"""
Alma Insights — Evidence Panel Widget (Build 11.0)

Context-reactive right-side panel for the Analysis Canvas tab.
Displays ticket previews, trend sparklines, and report metadata
in response to Qt signals from the report viewer and chat.
"""

import json
import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QStackedWidget, QScrollArea, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, Slot

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_MID, ALMA_GREEN_LIGHT,
    ALMA_WHITE, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_BORDER, ALMA_BORDER_LIGHT, ALMA_BG_ELEVATED, ALMA_BG_INSET,
    ALMA_SUCCESS, ALMA_WARNING, ALMA_ERROR, ALMA_INFO,
    apply_card_shadow_soft,
)

logger = logging.getLogger("alma.evidence_panel")

# View indices
VIEW_EMPTY = 0
VIEW_TICKET = 1
VIEW_FINDING = 2
VIEW_METADATA = 3


class EvidencePanel(QWidget):
    """Context-reactive evidence panel for the Analysis Canvas."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(280)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QLabel("Evidence panel")
        header.setStyleSheet(f"""
            font-size: 12px; font-weight: 700; color: {ALMA_TEXT_MID};
            letter-spacing: 0.5px; padding: 12px 16px 8px;
        """)
        layout.addWidget(header)

        # Stacked views
        self._stack = QStackedWidget()
        layout.addWidget(self._stack, 1)

        # View 0: Empty state
        empty = QWidget()
        el = QVBoxLayout(empty)
        el.setContentsMargins(16, 40, 16, 16)
        empty_lbl = QLabel("Select a finding or ask a question\nto see supporting evidence.")
        empty_lbl.setAlignment(Qt.AlignCenter)
        empty_lbl.setWordWrap(True)
        empty_lbl.setStyleSheet(f"font-size: 13px; color: {ALMA_TEXT_LIGHT};")
        el.addWidget(empty_lbl)
        el.addStretch()
        self._stack.addWidget(empty)

        # View 1: Ticket detail
        self._ticket_view = self._build_ticket_view()
        self._stack.addWidget(self._ticket_view)

        # View 2: Finding detail
        self._finding_view = self._build_finding_view()
        self._stack.addWidget(self._finding_view)

        # View 3: Report metadata
        self._metadata_view = self._build_metadata_view()
        self._stack.addWidget(self._metadata_view)

        self._stack.setCurrentIndex(VIEW_EMPTY)

    def _build_ticket_view(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(16, 8, 16, 16)
        layout.setSpacing(8)

        self._ticket_id_lbl = QLabel("")
        self._ticket_id_lbl.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK};")
        layout.addWidget(self._ticket_id_lbl)

        self._ticket_severity_lbl = QLabel("")
        self._ticket_severity_lbl.setStyleSheet(f"font-size: 11px; font-weight: 600;")
        layout.addWidget(self._ticket_severity_lbl)

        self._ticket_snippet_lbl = QLabel("")
        self._ticket_snippet_lbl.setWordWrap(True)
        self._ticket_snippet_lbl.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID}; line-height: 1.4;")
        layout.addWidget(self._ticket_snippet_lbl)

        self._ticket_meta_lbl = QLabel("")
        self._ticket_meta_lbl.setWordWrap(True)
        self._ticket_meta_lbl.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT};")
        layout.addWidget(self._ticket_meta_lbl)

        layout.addStretch()
        return w

    def _build_finding_view(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(16, 8, 16, 16)
        layout.setSpacing(8)

        self._finding_title_lbl = QLabel("")
        self._finding_title_lbl.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {ALMA_TEXT_MID}; letter-spacing: 0.5px;")
        layout.addWidget(self._finding_title_lbl)

        self._finding_name_lbl = QLabel("")
        self._finding_name_lbl.setWordWrap(True)
        self._finding_name_lbl.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK};")
        layout.addWidget(self._finding_name_lbl)

        self._finding_detail_lbl = QLabel("")
        self._finding_detail_lbl.setWordWrap(True)
        self._finding_detail_lbl.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID}; line-height: 1.4;")
        layout.addWidget(self._finding_detail_lbl)

        # Placeholder for sparkline widget
        self._sparkline_placeholder = QFrame()
        self._sparkline_placeholder.setMinimumHeight(80)
        self._sparkline_placeholder.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_INSET};
                border: none;
                border-radius: 8px;
            }}
        """)
        layout.addWidget(self._sparkline_placeholder)

        layout.addStretch()
        return w

    def _build_metadata_view(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(16, 8, 16, 16)
        layout.setSpacing(8)

        title = QLabel("REPORT METADATA")
        title.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {ALMA_TEXT_LIGHT}; letter-spacing: 1px;")
        layout.addWidget(title)

        self._meta_labels = {}
        for key in ("Pipeline", "Specialists", "Tickets", "TRC categories", "Generated", "Cost"):
            row = QHBoxLayout()
            k = QLabel(key)
            k.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT}; min-width: 100px;")
            v = QLabel("-")
            v.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_DARK}; font-weight: 600;")
            row.addWidget(k)
            row.addWidget(v, 1)
            layout.addLayout(row)
            self._meta_labels[key] = v

        layout.addStretch()
        return w

    # ── Public slots ──

    @Slot(str)
    def show_ticket(self, ticket_id: str):
        """Display ticket detail from ticket_index."""
        try:
            from src.data.db_manager import DB_PATH
            from src.data.connection_factory import get_connection
            conn = get_connection(DB_PATH)
            row = conn.execute(
                "SELECT * FROM ticket_index WHERE ticket_id = ?", (ticket_id,)
            ).fetchone()
            conn.close()

            if row:
                self._ticket_id_lbl.setText(f"#{row['ticket_id']}")
                severity = row["anomaly_flag"] or "normal"
                color = ALMA_ERROR if severity == "critical" else (ALMA_WARNING if severity == "unusual" else ALMA_TEXT_LIGHT)
                self._ticket_severity_lbl.setText(severity.title())
                self._ticket_severity_lbl.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {color};")
                self._ticket_snippet_lbl.setText(row["issue_snippet"] or "No snippet available")
                meta_parts = []
                if row["trc_label"]:
                    meta_parts.append(f"TRC: {row['trc_label']}")
                if row["friction_type"]:
                    meta_parts.append(f"Friction: {row['friction_type']}")
                if row["sentiment_polarity"]:
                    meta_parts.append(f"Sentiment: {row['sentiment_polarity']}")
                self._ticket_meta_lbl.setText(" | ".join(meta_parts))
        except Exception as e:
            self._ticket_snippet_lbl.setText(f"Error loading ticket: {e}")

        self._stack.setCurrentIndex(VIEW_TICKET)

    @Slot(dict)
    def show_finding(self, finding: dict):
        """Display finding detail."""
        self._finding_title_lbl.setText("SELECTED FINDING")
        self._finding_name_lbl.setText(finding.get("title", ""))
        self._finding_detail_lbl.setText(finding.get("description", ""))
        self._stack.setCurrentIndex(VIEW_FINDING)

    @Slot(dict)
    def show_metadata(self, metadata: dict):
        """Display report metadata."""
        for key, label in self._meta_labels.items():
            label.setText(str(metadata.get(key.lower(), "-")))
        self._stack.setCurrentIndex(VIEW_METADATA)
