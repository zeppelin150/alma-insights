"""
Alma Insights — Placeholder Pages
Remaining placeholder(s) for pages not yet built.
TRC Analytics and Trending Topics have been moved to their own modules.
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QFrame
from PySide6.QtCore import Qt
from src.ui.theme import *


class PlaceholderPage(QWidget):
    """Generic placeholder for pages not yet built."""

    def __init__(self, title, subtitle, description, icon="🚧", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 16)
        layout.setSpacing(0)

        header = QLabel(title)
        header.setObjectName("PageHeader")
        layout.addWidget(header)

        sub = QLabel(subtitle)
        sub.setObjectName("PageSubheader")
        layout.addWidget(sub)
        layout.addSpacing(40)

        # Centered placeholder card
        card = QFrame()
        card.setStyleSheet(f"""
            background: {ALMA_WHITE}; border: 2px dashed {ALMA_BORDER};
            border-radius: 12px; padding: 48px;
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setAlignment(Qt.AlignCenter)

        emoji = QLabel(icon)
        emoji.setStyleSheet("font-size: 48px;")
        emoji.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(emoji)

        coming = QLabel("Coming Soon")
        coming.setStyleSheet(f"font-size: 16px; font-weight: 600; color: {ALMA_TEXT_MID}; margin-top: 12px;")
        coming.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(coming)

        desc = QLabel(description)
        desc.setStyleSheet(f"font-size: 13px; color: {ALMA_TEXT_LIGHT}; margin-top: 8px;")
        desc.setAlignment(Qt.AlignCenter)
        desc.setWordWrap(True)
        desc.setMaximumWidth(450)
        card_layout.addWidget(desc)

        layout.addWidget(card)
        layout.addStretch()


