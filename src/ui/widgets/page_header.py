"""
Alma Insights — Page Header Building Block

Standardized page title with optional subtitle and action button area.
Uses ObjectName styling from theme.py (#PageHeader, #PageSubheader).
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy,
)
from PySide6.QtCore import Qt


class PageHeader(QWidget):
    """Page header with title, optional subtitle, and right-aligned action area.

    Usage:
        header = PageHeader("TRC Analytics", "Volume, resolution, CSAT metrics")
        header.add_action(some_button)
    """

    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(16)

        # Left: title + subtitle stacked
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(2)

        self._title = QLabel(title)
        self._title.setObjectName("PageHeader")
        left.addWidget(self._title)

        self._subtitle = QLabel(subtitle)
        self._subtitle.setObjectName("PageSubheader")
        if not subtitle:
            self._subtitle.setVisible(False)
        left.addWidget(self._subtitle)

        outer.addLayout(left)
        outer.addStretch()

        # Right: action area (buttons, etc.)
        self._actions = QHBoxLayout()
        self._actions.setContentsMargins(0, 0, 0, 0)
        self._actions.setSpacing(8)
        outer.addLayout(self._actions)

    def set_title(self, text: str):
        self._title.setText(text)

    def set_subtitle(self, text: str):
        self._subtitle.setText(text)
        self._subtitle.setVisible(bool(text))

    def add_action(self, widget: QWidget):
        """Add a widget (typically a button) to the right-aligned action area."""
        self._actions.addWidget(widget)
