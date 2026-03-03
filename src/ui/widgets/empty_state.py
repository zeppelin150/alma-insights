"""
Alma Insights — Empty State Widget (Build 10.0: T6)

Shows a muted icon + message when a chart/table has no data.
Upgraded: optional heading, description, and CTA button for richer empty states.
Backward-compatible with existing callers.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal


class EmptyState(QWidget):
    """
    A muted placeholder shown when a chart or table has no data.

    Basic usage (backward-compatible):
        empty = EmptyState("No data for this date range", icon="chart")

    Enhanced usage (Build 10.0):
        empty = EmptyState(
            message="No data for this date range",
            icon="chart",
            heading="No analytics data",
            description="Import data or adjust date range to see metrics",
            action_label="Import Data",
        )
        empty.action_clicked.connect(self._on_import)
    """

    action_clicked = Signal()

    ICONS = {
        "chart": "\U0001F4CA",
        "table": "\U0001F4CB",
        "search": "\U0001F50D",
        "data": "\U0001F4C2",
        "warning": "\u26a0\ufe0f",
        "report": "\U0001F4DD",
        "empty": "\U0001F4ED",
    }

    def __init__(
        self,
        message: str = "No data available",
        icon: str = "chart",
        heading: str | None = None,
        description: str | None = None,
        action_label: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setMinimumHeight(160 if heading else 120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(8)
        layout.setContentsMargins(24, 32, 24, 32)

        # ── Icon ──
        icon_text = self.ICONS.get(icon, self.ICONS["chart"])
        icon_label = QLabel(icon_text)
        icon_label.setObjectName("EmptyStateIcon")
        icon_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon_label)

        if heading:
            # ── Enhanced 3-tier layout ──
            layout.addSpacing(4)

            heading_label = QLabel(heading)
            heading_label.setObjectName("EmptyStateTitle")
            heading_label.setAlignment(Qt.AlignCenter)
            heading_label.setWordWrap(True)
            layout.addWidget(heading_label)

            # Description (use message as fallback if no explicit description)
            desc_text = description or message
            desc_label = QLabel(desc_text)
            desc_label.setObjectName("EmptyStateDescription")
            desc_label.setAlignment(Qt.AlignCenter)
            desc_label.setWordWrap(True)
            desc_label.setMaximumWidth(400)
            layout.addWidget(desc_label, alignment=Qt.AlignCenter)

            # ── CTA button (optional) ──
            if action_label:
                layout.addSpacing(8)
                cta = QPushButton(action_label)
                cta.setObjectName("SecondaryButton")
                cta.setCursor(Qt.PointingHandCursor)
                cta.setMaximumWidth(200)
                cta.clicked.connect(self.action_clicked.emit)
                layout.addWidget(cta, alignment=Qt.AlignCenter)

        else:
            # ── Legacy layout (backward-compatible) ──
            msg_label = QLabel(message)
            msg_label.setObjectName("EmptyStateDescription")
            msg_label.setAlignment(Qt.AlignCenter)
            msg_label.setWordWrap(True)
            layout.addWidget(msg_label)
