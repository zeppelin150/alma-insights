"""
Alma Insights — Report Section Renderer (Build 11.0)

Parses markdown report output into structured QWidget section cards.
Each section has a header, severity badge, and clickable ticket IDs.
"""

import re
import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_MID,
    ALMA_WHITE, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_BORDER_LIGHT, ALMA_BG_ELEVATED,
    ALMA_SUCCESS, ALMA_WARNING, ALMA_ERROR, ALMA_INFO,
    apply_card_shadow_soft,
)

logger = logging.getLogger("alma.report_renderer")

_SEVERITY_COLORS = {
    "deteriorating": ALMA_ERROR,
    "critical": ALMA_ERROR,
    "warning": ALMA_WARNING,
    "stable": ALMA_SUCCESS,
    "improving": ALMA_SUCCESS,
    "new": ALMA_INFO,
}

_TICKET_PATTERN = re.compile(r"#(\d{4,7})")


class ReportSectionCard(QFrame):
    """A single rendered section of a report."""

    finding_clicked = Signal(dict)
    ticket_clicked = Signal(str)

    def __init__(self, title: str, content: str, severity: str = "", parent=None):
        super().__init__(parent)
        self._title = title
        self._content = content
        self._severity = severity.lower()

        self.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED};
                border: none;
                border-radius: 10px;
            }}
        """)
        apply_card_shadow_soft(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        # Header row
        hdr = QHBoxLayout()

        if self._severity:
            badge = QLabel(severity.upper())
            color = _SEVERITY_COLORS.get(self._severity, ALMA_TEXT_LIGHT)
            badge.setStyleSheet(f"""
                font-size: 10px; font-weight: 700; color: {color};
                background: {color}20; border-radius: 8px;
                padding: 2px 8px;
            """)
            hdr.addWidget(badge)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {ALMA_TEXT_DARK};")
        hdr.addWidget(title_lbl, 1)

        layout.addLayout(hdr)

        # Content with clickable ticket IDs
        content_lbl = QLabel(self._linkify_tickets(content))
        content_lbl.setWordWrap(True)
        content_lbl.setTextFormat(Qt.RichText)
        content_lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
        content_lbl.linkActivated.connect(self._on_link)
        content_lbl.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID}; line-height: 1.5;")
        layout.addWidget(content_lbl)

    def _linkify_tickets(self, text):
        """Convert #12345 patterns to clickable links."""
        return _TICKET_PATTERN.sub(
            r'<a href="ticket:\1" style="color: ' + ALMA_GREEN_DARK + r';">#\1</a>',
            text,
        )

    def _on_link(self, url):
        if url.startswith("ticket:"):
            tid = url.replace("ticket:", "")
            self.ticket_clicked.emit(tid)


class ReportSectionRenderer(QWidget):
    """Parses markdown into a list of ReportSectionCard widgets."""

    finding_clicked = Signal(dict)
    ticket_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(10)

    def render(self, markdown_text: str):
        """Parse markdown and create section cards."""
        # Clear existing
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        sections = self._parse_sections(markdown_text)

        for section in sections:
            card = ReportSectionCard(
                title=section["title"],
                content=section["content"],
                severity=section.get("severity", ""),
            )
            card.ticket_clicked.connect(self.ticket_clicked.emit)
            card.finding_clicked.connect(self.finding_clicked.emit)
            self._layout.addWidget(card)

    def _parse_sections(self, text: str) -> list[dict]:
        """Split markdown into sections by ## headings."""
        sections = []
        lines = text.split("\n")
        current_title = ""
        current_lines = []

        for line in lines:
            if line.startswith("## "):
                if current_title or current_lines:
                    sections.append({
                        "title": current_title,
                        "content": "\n".join(current_lines).strip(),
                        "severity": self._detect_severity(current_title, current_lines),
                    })
                current_title = line[3:].strip()
                current_lines = []
            else:
                current_lines.append(line)

        # Last section
        if current_title or current_lines:
            sections.append({
                "title": current_title,
                "content": "\n".join(current_lines).strip(),
                "severity": self._detect_severity(current_title, current_lines),
            })

        return sections

    def _detect_severity(self, title: str, lines: list) -> str:
        """Infer severity from section content."""
        text = (title + " ".join(lines)).lower()
        if "deteriorat" in text or "critical" in text or "worse" in text:
            return "deteriorating"
        if "improv" in text or "better" in text or "resolv" in text:
            return "improving"
        if "stable" in text:
            return "stable"
        return ""
