"""
Alma Insights — Empty State Widget
Shows a muted icon + message when a chart/table has no data.
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide6.QtCore import Qt
from src.ui.theme import ALMA_TEXT_LIGHT, ALMA_BORDER_LIGHT


class EmptyState(QWidget):
    """
    A muted placeholder shown when a chart or table has no data.

    Usage:
        empty = EmptyState("No data for this date range", icon="chart")
        layout.addWidget(empty)
    """

    ICONS = {
        "chart": "\U0001F4CA",
        "table": "\U0001F4CB",
        "search": "\U0001F50D",
        "data": "\U0001F4C2",
        "warning": "\u26a0\ufe0f",
    }

    def __init__(self, message: str = "No data available",
                 icon: str = "chart", parent=None):
        super().__init__(parent)
        self.setMinimumHeight(120)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(8)

        icon_text = self.ICONS.get(icon, self.ICONS["chart"])
        icon_label = QLabel(icon_text)
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setStyleSheet("font-size: 32px; color: #D6D2CA;")
        layout.addWidget(icon_label)

        msg_label = QLabel(message)
        msg_label.setAlignment(Qt.AlignCenter)
        msg_label.setStyleSheet(
            f"font-size: 13px; color: {ALMA_TEXT_LIGHT}; font-weight: 500;"
        )
        msg_label.setWordWrap(True)
        layout.addWidget(msg_label)
