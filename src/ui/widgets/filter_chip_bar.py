"""
Alma Insights — Filter Chip Bar (Build 10.0: T8)

Horizontal flow of active-filter pills with dismiss buttons.
Shows active filters as compact chips; emits filter_removed when user clicks ✕.
Auto-hides when no filters are active.
"""

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QToolButton, QPushButton,
    QSizePolicy,
)
from PySide6.QtCore import Qt, Signal


class FilterChipBar(QWidget):
    """
    Bar of active filter pills. Each chip shows filter label + value and
    a dismiss (✕) button.

    Usage:
        bar = FilterChipBar()
        bar.set_filters({"TRC": "Provider payout", "Date": "Jan – Mar"})
        bar.filter_removed.connect(self._on_filter_cleared)
    """

    filter_removed = Signal(str)   # emits the filter key when ✕ clicked
    all_cleared = Signal()          # emits when "Clear all" is clicked

    def __init__(self, parent=None):
        super().__init__(parent)
        self._filters: dict[str, str] = {}
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(36)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)

        # Placeholder for dynamic chips
        self._chip_widgets: list[QWidget] = []

        # "Clear all" link (ghost button style, right-aligned)
        self._clear_btn = QPushButton("Clear all")
        self._clear_btn.setObjectName("GhostButton")
        self._clear_btn.setCursor(Qt.PointingHandCursor)
        self._clear_btn.setFixedHeight(28)
        self._clear_btn.clicked.connect(self._on_clear_all)
        self._clear_btn.setVisible(False)

        self.setVisible(False)  # hidden until filters are set

    # ── Public API ────────────────────────────────────────────

    def set_filters(self, filters: dict[str, str]):
        """
        Replace the current filter set.
        filters: {"TRC": "Provider payout...", "Date": "Jan–Mar", "Status": "Open"}
        Empty or None values are ignored.
        """
        self._filters = {k: v for k, v in (filters or {}).items() if v}
        self._rebuild()

    def clear_all(self):
        """Clear all filters and hide the bar."""
        self._filters.clear()
        self._rebuild()
        self.all_cleared.emit()

    def add_filter(self, key: str, value: str):
        """Add or update a single filter chip."""
        if value:
            self._filters[key] = value
            self._rebuild()

    def remove_filter(self, key: str):
        """Remove a single filter chip."""
        if key in self._filters:
            del self._filters[key]
            self._rebuild()

    # ── Internal ──────────────────────────────────────────────

    def _rebuild(self):
        """Tear down and recreate all chip widgets."""
        # Remove old chips
        for w in self._chip_widgets:
            self._layout.removeWidget(w)
            w.deleteLater()
        self._chip_widgets.clear()

        # Remove clear button from layout (we'll re-add at end)
        self._layout.removeWidget(self._clear_btn)

        if not self._filters:
            self.setVisible(False)
            self._clear_btn.setVisible(False)
            return

        self.setVisible(True)

        for key, value in self._filters.items():
            chip = self._make_chip(key, value)
            self._layout.addWidget(chip)
            self._chip_widgets.append(chip)

        # Spacer + clear all
        self._layout.addStretch()
        self._layout.addWidget(self._clear_btn)
        self._clear_btn.setVisible(True)

    def _make_chip(self, key: str, value: str) -> QWidget:
        """Create a single filter chip widget."""
        chip = QWidget()
        chip.setObjectName("FilterChip")
        chip.setFixedHeight(28)

        h = QHBoxLayout(chip)
        h.setContentsMargins(10, 0, 4, 0)
        h.setSpacing(4)

        # Label: "Key: Value"
        display = f"{key}: {value}" if len(value) <= 30 else f"{key}: {value[:27]}…"
        label = QLabel(display)
        label.setStyleSheet(
            "font-size: 12px; font-weight: 500; color: #03281B; background: transparent;"
        )
        h.addWidget(label)

        # Dismiss button (✕)
        close_btn = QToolButton()
        close_btn.setText("✕")
        close_btn.setFixedSize(18, 18)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet(
            "QToolButton { background: transparent; border: none; color: #03281B;"
            " font-size: 11px; font-weight: 700; border-radius: 9px; }"
            "QToolButton:hover { background: rgba(3,40,27,0.15); }"
        )
        close_btn.clicked.connect(lambda checked, k=key: self._on_remove(k))
        h.addWidget(close_btn)

        return chip

    def _on_remove(self, key: str):
        """Handle chip dismiss."""
        if key in self._filters:
            del self._filters[key]
            self._rebuild()
            self.filter_removed.emit(key)

    def _on_clear_all(self):
        """Handle 'Clear all' click."""
        self.clear_all()
