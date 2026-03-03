"""
Alma Insights — Tab Scroll Content Building Block

Scrollable container used as the body of each tab in analysis pages.
Provides consistent margins, background color, and scroll behavior.
"""

from PySide6.QtWidgets import QScrollArea, QWidget, QVBoxLayout
from PySide6.QtCore import Qt

from src.ui.theme import ALMA_CREAM


class TabScrollContent(QScrollArea):
    """Scrollable content area for analysis page tabs.

    Usage:
        tab = TabScrollContent()
        tab.content_layout.addWidget(my_chart)
        tab.content_layout.addWidget(my_table)
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        self.setStyleSheet(f"QScrollArea {{ background: {ALMA_CREAM}; border: none; }}")

        container = QWidget()
        container.setStyleSheet(f"background: {ALMA_CREAM};")
        self._content_layout = QVBoxLayout(container)
        self._content_layout.setContentsMargins(24, 20, 24, 24)
        self._content_layout.setSpacing(16)
        self.setWidget(container)

    @property
    def content_layout(self) -> QVBoxLayout:
        """The layout to add widgets to."""
        return self._content_layout

    def scroll_to_top(self):
        """Scroll back to the top of the content."""
        self.verticalScrollBar().setValue(0)

    def add_stretch(self):
        """Add a stretch at the bottom to push content up."""
        self._content_layout.addStretch()
