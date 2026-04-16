"""
Alma Insights — Message Bubble Widget (Chat Uplevel)

Redesigned chat bubbles matching the Cambric mockups:
  - User: ALMA_GREEN_DARK bg, cream text, right-aligned, max 85%
  - Gemini: White bg, 1px border, left-aligned, max 90%
  - Finding sections: left green-border cards with bold headers
  - Ticket IDs: monospace chips with light gray bg, clickable
  - Clean prose rendering (no raw JSON)
"""

import re
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QWidget,
)
from PySide6.QtCore import Qt, Signal

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_GREEN_SUBTLE,
    ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_TEXT_ON_DARK, ALMA_BORDER_LIGHT, ALMA_BG_ELEVATED, ALMA_BG_INSET,
)

# Regex patterns for response parsing
# Finding headers must be bold or have a parenthetical — avoids matching plain numbered lists
# Matches: "1. **Title**", "1. Title (Details):", "1. Title:"
_FINDING_HEADER_RE = re.compile(
    r"^(\d+)\.\s+\*\*(.+?)\*\*\s*:?\s*$"       # "1. **Bold Title**" or "1. **Bold Title**:"
    r"|^(\d+)\.\s+(.+?\(.+?\))\s*:?\s*$"        # "1. Title (Parenthetical):"
)
_TICKET_ID_RE = re.compile(r"#(\d{4,6})")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


class MessageBubble(QFrame):
    """Single chat message bubble with rich Gemini response rendering."""

    ticket_clicked = Signal(str)  # Emits ticket_id when a chip is clicked

    def __init__(self, role: str, content: str, parent=None):
        super().__init__(parent)
        self._role = role
        self._content = content
        is_user = role == "user"

        # Outer frame styling
        if is_user:
            self.setStyleSheet(f"""
                MessageBubble {{
                    background: {ALMA_GREEN_DARK};
                    border: none;
                    border-radius: 12px;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                MessageBubble {{
                    background: {ALMA_BG_ELEVATED};
                    border: 1px solid {ALMA_BORDER_LIGHT};
                    border-radius: 12px;
                }}
            """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 12, 20, 16)
        layout.setSpacing(6)

        # Role label
        role_lbl = QLabel("You" if is_user else "Gemini")
        if is_user:
            role_lbl.setStyleSheet(f"""
                font-size: 12px; font-weight: 600;
                color: {ALMA_TEXT_ON_DARK};
                background: transparent;
            """)
        else:
            role_lbl.setStyleSheet(f"""
                font-size: 12px; font-weight: 600;
                color: {ALMA_GREEN_LIGHT};
                background: transparent;
            """)
        layout.addWidget(role_lbl)

        # Content rendering
        if is_user:
            self._render_user_content(layout, content)
        else:
            self._render_gemini_content(layout, content)

        # Alignment via layout margins
        if is_user:
            self.setMaximumWidth(800)
        else:
            self.setMaximumWidth(900)

    def _render_user_content(self, layout, content):
        """Simple text for user messages."""
        lbl = QLabel(content)
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lbl.setStyleSheet(f"""
            font-size: 14px; color: {ALMA_CREAM};
            line-height: 22px; background: transparent;
        """)
        layout.addWidget(lbl)

    def _render_gemini_content(self, layout, content):
        """Rich rendering for Gemini responses with finding cards and ticket chips."""
        blocks = self._parse_response_blocks(content)

        for block in blocks:
            if block["type"] == "finding":
                self._add_finding_card(layout, block["header"], block["body"])
            elif block["type"] == "prose":
                self._add_prose(layout, block["text"])

    def _parse_response_blocks(self, content: str) -> list[dict]:
        """Parse Gemini response into structured blocks.

        Identifies numbered finding sections with bold headers
        (e.g. "1. **Broken Session & Portal Links**") and separates
        them from regular prose. Conservative matching — only bold
        headers or parenthetical titles trigger finding cards.
        """
        lines = content.split("\n")
        blocks = []
        current_prose = []
        current_finding = None
        empty_line_count = 0

        for line in lines:
            stripped = line.strip()

            # Check for numbered finding header (bold or parenthetical only)
            match = _FINDING_HEADER_RE.match(stripped)
            if match:
                # Flush current prose
                if current_prose:
                    text = "\n".join(current_prose).strip()
                    if text:
                        blocks.append({"type": "prose", "text": text})
                    current_prose = []

                # Flush previous finding
                if current_finding:
                    blocks.append(current_finding)

                # Extract from whichever alternation matched
                num = match.group(1) or match.group(3)
                header = match.group(2) or match.group(4)
                header = header.strip().rstrip("*").rstrip(":")
                current_finding = {
                    "type": "finding",
                    "header": f"{num}. {header}",
                    "body": [],
                }
                empty_line_count = 0
            elif current_finding is not None:
                if stripped:
                    current_finding["body"].append(stripped)
                    empty_line_count = 0
                else:
                    empty_line_count += 1
                    # Two consecutive empty lines = end of finding block
                    if empty_line_count >= 2:
                        blocks.append(current_finding)
                        current_finding = None
                        empty_line_count = 0
                    elif current_finding["body"]:
                        current_finding["body"].append("")
            else:
                current_prose.append(line)

        # Flush remaining
        if current_finding:
            blocks.append(current_finding)
        if current_prose:
            text = "\n".join(current_prose).strip()
            if text:
                blocks.append({"type": "prose", "text": text})

        # If no blocks parsed (simple response), return as single prose
        if not blocks:
            blocks.append({"type": "prose", "text": content})

        return blocks

    def _add_finding_card(self, layout, header: str, body_lines: list):
        """Add a finding section as a card with green left border."""
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_INSET};
                border: none;
                border-left: 3px solid {ALMA_GREEN_LIGHT};
                border-radius: 4px;
                margin: 4px 0px;
            }}
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 10, 14, 10)
        card_layout.setSpacing(6)

        # Section header (bold)
        header_lbl = QLabel(header)
        header_lbl.setWordWrap(True)
        header_lbl.setStyleSheet(f"""
            font-size: 14px; font-weight: 700;
            color: {ALMA_TEXT_DARK}; background: transparent;
        """)
        card_layout.addWidget(header_lbl)

        # Body content with ticket chip rendering
        body_text = "\n".join(body_lines).strip()
        if body_text:
            self._add_body_with_tickets(card_layout, body_text)

        layout.addWidget(card)

    def _add_body_with_tickets(self, layout, text: str):
        """Add body text, rendering ticket IDs as inline chips."""
        # Split into lines and render each
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue

            # Check if line contains ticket IDs
            ticket_ids = _TICKET_ID_RE.findall(line)
            if ticket_ids:
                self._add_line_with_ticket_chips(layout, line, ticket_ids)
            else:
                # Clean up markdown bold
                clean = _BOLD_RE.sub(r"\1", line)
                lbl = QLabel(clean)
                lbl.setWordWrap(True)
                lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
                lbl.setStyleSheet(f"""
                    font-size: 13px; color: {ALMA_TEXT_DARK};
                    line-height: 22px; background: transparent;
                """)
                layout.addWidget(lbl)

    def _add_line_with_ticket_chips(self, layout, line: str, ticket_ids: list[str]):
        """Render a line that contains ticket ID references as chips."""
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(6)

        # Split line by ticket references
        parts = _TICKET_ID_RE.split(line)
        for i, part in enumerate(parts):
            if i % 2 == 0:
                # Text part
                text = _BOLD_RE.sub(r"\1", part).strip()
                if text:
                    lbl = QLabel(text)
                    lbl.setStyleSheet(f"""
                        font-size: 13px; color: {ALMA_TEXT_DARK};
                        background: transparent;
                    """)
                    row.addWidget(lbl)
            else:
                # Ticket ID — render as chip
                chip = QPushButton(f"#{part}")
                chip.setCursor(Qt.PointingHandCursor)
                chip.setStyleSheet(f"""
                    QPushButton {{
                        background: #F0EDE8;
                        color: {ALMA_TEXT_DARK};
                        border: none; border-radius: 8px;
                        padding: 2px 8px;
                        font-size: 12px; font-family: monospace; font-weight: 600;
                    }}
                    QPushButton:hover {{
                        background: #E5E0D8;
                    }}
                """)
                tid = part
                chip.clicked.connect(lambda _, t=tid: self.ticket_clicked.emit(t))
                row.addWidget(chip)

        row.addStretch()
        wrapper = QWidget()
        wrapper.setLayout(row)
        wrapper.setStyleSheet("background: transparent;")
        layout.addWidget(wrapper)

    def _add_prose(self, layout, text: str):
        """Add regular prose text."""
        # Clean up markdown bold markers
        clean = _BOLD_RE.sub(r"\1", text)
        lbl = QLabel(clean)
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lbl.setStyleSheet(f"""
            font-size: 13px; color: {ALMA_TEXT_DARK};
            line-height: 22px; background: transparent;
        """)
        layout.addWidget(lbl)
