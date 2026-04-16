"""
Alma Insights — Explore Dropdown Menu (Chat Uplevel)

Quick-jump menu from the header bar to drilldown panel modes:
All Chats, Projects, Reports, Incidents, Monitor.

Each item shows an icon, label, and live count badge.
Clicking an item opens the drilldown panel in the corresponding mode.
"""

import logging
from PySide6.QtWidgets import (
    QMenu, QWidgetAction, QWidget, QHBoxLayout, QLabel,
    QFrame, QVBoxLayout,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_TEXT_DARK, ALMA_TEXT_MID,
    ALMA_TEXT_LIGHT, ALMA_BORDER_LIGHT, ALMA_BG_ELEVATED, ALMA_HOVER_LIGHT,
)

logger = logging.getLogger("alma.explore_menu")


# ═══════════════════════════════════════
#  Panel definitions
# ═══════════════════════════════════════

EXPLORE_PANELS = {
    "all_chats": {
        "label": "All Chats",
        "icon": "\U0001F4AC",  # speech bubble
        "badge_query": "SELECT COUNT(*) FROM chat_sessions",
        "drilldown_mode": "chat_history",
    },
    "projects": {
        "label": "Projects",
        "icon": "\U0001F4C1",  # folder
        "badge_query": "SELECT COUNT(*) FROM chat_projects",
        "drilldown_mode": "projects",
    },
    "reports": {
        "label": "Reports",
        "icon": "\U0001F4CA",  # bar chart
        "badge_query": "SELECT COUNT(*) FROM analysis_reports",
        "drilldown_mode": "report_browser",
    },
    "incidents": {
        "label": "Incidents",
        "icon": "\U000026A0",  # warning sign
        "badge_query": "SELECT COUNT(*) FROM incident_flags WHERE active=1",
        "drilldown_mode": "incidents",
    },
    "monitor": {
        "label": "Monitor",
        "icon": "\U0001F441",  # eye
        "badge_query": None,
        "drilldown_mode": "observability",
    },
}


class ExploreMenuItem(QFrame):
    """Single row in the Explore dropdown: icon + label + badge."""

    clicked = Signal(str)  # Emits the panel key

    def __init__(self, key: str, icon: str, label: str, count: int | None = None, parent=None):
        super().__init__(parent)
        self._key = key
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(f"""
            ExploreMenuItem {{
                background: {ALMA_BG_ELEVATED};
                border: none;
                padding: 0px;
            }}
            ExploreMenuItem:hover {{
                background: {ALMA_HOVER_LIGHT};
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(12)

        # Icon
        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet("font-size: 16px; background: transparent;")
        icon_lbl.setFixedWidth(24)
        layout.addWidget(icon_lbl)

        # Label
        label_lbl = QLabel(label)
        label_lbl.setStyleSheet(f"""
            font-size: 14px; font-weight: 500;
            color: {ALMA_TEXT_DARK}; background: transparent;
        """)
        layout.addWidget(label_lbl, 1)

        # Count badge (optional)
        if count is not None:
            badge = QLabel(str(count))
            badge.setAlignment(Qt.AlignCenter)
            badge.setStyleSheet(f"""
                background: #F0EDE8;
                color: {ALMA_TEXT_MID};
                border-radius: 10px;
                padding: 2px 10px;
                font-size: 11px; font-weight: 700;
                min-width: 24px;
            """)
            layout.addWidget(badge)

    def mousePressEvent(self, event):
        self.clicked.emit(self._key)
        super().mousePressEvent(event)


class ExploreDropdown(QFrame):
    """Popup dropdown menu triggered by the Explore button.

    Shows panel options with live badge counts.
    Emits mode_selected(drilldown_mode) when an item is clicked.
    """

    mode_selected = Signal(str)  # Emits drilldown_mode string

    def __init__(self, conn=None, parent=None):
        super().__init__(parent)
        self._conn = conn
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setStyleSheet(f"""
            ExploreDropdown {{
                background: {ALMA_BG_ELEVATED};
                border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 8px;
            }}
        """)
        self.setFixedWidth(240)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(4, 8, 4, 8)
        self._layout.setSpacing(0)

        self._build_items()

    def _build_items(self):
        """Build menu items with live badge counts."""
        for key, panel in EXPLORE_PANELS.items():
            count = self._get_count(panel["badge_query"])
            item = ExploreMenuItem(
                key=key,
                icon=panel["icon"],
                label=panel["label"],
                count=count,
            )
            item.clicked.connect(self._on_item_clicked)
            self._layout.addWidget(item)

    def _get_count(self, query: str | None) -> int | None:
        if query is None or self._conn is None:
            return None
        try:
            row = self._conn.execute(query).fetchone()
            return row[0] if row else 0
        except Exception:
            return None

    def _on_item_clicked(self, key: str):
        panel = EXPLORE_PANELS.get(key, {})
        mode = panel.get("drilldown_mode", key)
        self.mode_selected.emit(mode)
        self.close()

    def refresh_counts(self, conn=None):
        """Refresh badge counts (call before showing)."""
        if conn:
            self._conn = conn
        # Clear and rebuild
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._build_items()

    def show_below(self, widget):
        """Position and show the dropdown below the given widget."""
        self.refresh_counts()
        pos = widget.mapToGlobal(widget.rect().bottomLeft())
        pos.setY(pos.y() + 4)
        self.move(pos)
        self.show()
