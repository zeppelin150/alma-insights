"""
Alma Insights — Shared Filter Bar Building Block

Universal filter bar that sits above the QTabWidget on analysis pages.
All filters apply across all tabs — changing a date range or TRC filter
re-queries data for every tab.

Usage:
    bar = SharedFilterBar()
    bar.add_date_range()
    bar.add_trc_filter(["All TRCs", "Billing", "Tech Support"])
    bar.add_action_button("Refresh", on_refresh)
    bar.filters_changed.connect(page._on_filters_changed)
"""

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSizePolicy, QSpacerItem, QFrame,
)
from PySide6.QtCore import Qt, QDate, Signal

from src.ui.theme import (
    ALMA_TEXT_LIGHT, ALMA_BG_ELEVATED, ALMA_BORDER_LIGHT,
    ALMA_GREEN_DARK, ALMA_TEXT_ON_DARK, ALMA_BORDER,
)
from src.ui.widgets.date_picker import ModernDatePicker


class SharedFilterBar(QWidget):
    """Horizontal filter bar with date pickers, combo filters, and action buttons.

    Emits filters_changed(dict) whenever any filter value changes.
    Emits action_triggered(str) when an action button is clicked.
    """

    filters_changed = Signal(dict)
    action_triggered = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("FilterBar")
        self.setStyleSheet(
            f"#FilterBar {{ background: {ALMA_BG_ELEVATED};"
            f" border: none;"
            " border-radius: 10px; }"
            f" #FilterBar QPushButton {{"
            f"   background: {ALMA_GREEN_DARK}; color: {ALMA_TEXT_ON_DARK};"
            "   border: none; border-radius: 8px;"
            "   padding: 8px 20px; font-size: 13px; font-weight: 600;"
            " }"
        )
        self.setFixedHeight(56)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(16, 0, 16, 0)
        self._layout.setSpacing(12)

        # Track filter widgets for get_filters()
        self._date_from: ModernDatePicker | None = None
        self._date_to: ModernDatePicker | None = None
        self._combos: dict[str, QComboBox] = {}
        self._action_buttons: list[QPushButton] = []

        # Spacer between filters and action buttons (added once)
        self._spacer_added = False

    # ═══════════════════════════════════════════
    #  ADD FILTERS
    # ═══════════════════════════════════════════

    def add_primary_action(self, text: str, callback=None, **_kwargs):
        """Add the primary action button at the FIRST position (v2: action-first).

        Call this BEFORE add_date_range() / add_combo_filter() so the button
        appears at the left edge followed by a separator, then the filters.

        Args:
            text: Button label (e.g. 'Refresh', 'Analyze', 'Run Scan')
            callback: Direct callback (also emits action_triggered)
        """
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)

        def _on_click():
            self.action_triggered.emit(text)
            if callback:
                callback()

        btn.clicked.connect(_on_click)
        self._action_buttons.append(btn)
        self._layout.addWidget(btn)

        # Vertical separator between action and filters
        sep = QFrame()
        sep.setObjectName("FilterSep")
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFixedWidth(1)
        self._layout.addWidget(sep)

        return btn

    def add_date_range(self, date_from: QDate = None, date_to: QDate = None):
        """Add from/to date pickers.

        Args:
            date_from: Initial from date (default: 90 days ago)
            date_to: Initial to date (default: today)
        """
        if date_from is None:
            date_from = QDate.currentDate().addDays(-90)
        if date_to is None:
            date_to = QDate.currentDate()

        lbl = QLabel("From")
        lbl.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; font-weight: 600;")
        self._layout.addWidget(lbl)

        self._date_from = ModernDatePicker()
        self._date_from.setDate(date_from)
        self._date_from.date_changed.connect(self._emit_change)
        self._layout.addWidget(self._date_from)

        lbl2 = QLabel("To")
        lbl2.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; font-weight: 600;")
        self._layout.addWidget(lbl2)

        self._date_to = ModernDatePicker()
        self._date_to.setDate(date_to)
        self._date_to.date_changed.connect(self._emit_change)
        self._layout.addWidget(self._date_to)

    def add_combo_filter(self, key: str, label: str, options: list[str]):
        """Add a combo box filter.

        Args:
            key: Filter key for get_filters() dict (e.g. 'trc', 'status')
            label: Display label next to the combo
            options: List of options (first is default)
        """
        lbl = QLabel(label)
        lbl.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; font-weight: 600;")
        self._layout.addWidget(lbl)

        combo = QComboBox()
        combo.addItems(options)
        combo.setMinimumWidth(100)
        combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        combo.currentTextChanged.connect(self._emit_change)
        self._combos[key] = combo
        self._layout.addWidget(combo)

    def add_custom_widget(self, label: str, widget: QWidget):
        """Add any custom widget with a label.

        Args:
            label: Display label
            widget: The widget to add
        """
        lbl = QLabel(label)
        lbl.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; font-weight: 600;")
        self._layout.addWidget(lbl)
        self._layout.addWidget(widget)

    def add_action_button(self, text: str, callback=None, primary: bool = True):
        """Add an action button (Refresh, Analyze, Run Scan, etc.).

        Args:
            text: Button label
            callback: Optional direct callback (also emits action_triggered)
            primary: If True, use primary green style. If False, secondary style.
        """
        if not self._spacer_added:
            self._layout.addStretch()
            self._spacer_added = True

        btn = QPushButton(text)
        if not primary:
            btn.setObjectName("SecondaryButton")
        btn.setCursor(Qt.PointingHandCursor)

        def _on_click():
            self.action_triggered.emit(text)
            if callback:
                callback()

        btn.clicked.connect(_on_click)
        self._action_buttons.append(btn)
        self._layout.addWidget(btn)
        return btn

    # ═══════════════════════════════════════════
    #  GET / SET FILTERS
    # ═══════════════════════════════════════════

    def get_filters(self) -> dict:
        """Return current filter values as a dict."""
        result = {}
        if self._date_from:
            result["date_from"] = self._date_from.date()
        if self._date_to:
            result["date_to"] = self._date_to.date()
        for key, combo in self._combos.items():
            result[key] = combo.currentText()
        return result

    def set_date_range(self, date_from: QDate, date_to: QDate):
        """Programmatically update the date range."""
        if self._date_from:
            self._date_from.setDate(date_from)
        if self._date_to:
            self._date_to.setDate(date_to)

    def set_combo_value(self, key: str, value: str):
        """Programmatically set a combo filter value."""
        combo = self._combos.get(key)
        if combo:
            idx = combo.findText(value)
            if idx >= 0:
                combo.setCurrentIndex(idx)

    def set_combo_items(self, key: str, items: list[str]):
        """Update the options in a combo filter."""
        combo = self._combos.get(key)
        if combo:
            combo.blockSignals(True)
            current = combo.currentText()
            combo.clear()
            combo.addItems(items)
            idx = combo.findText(current)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    def get_combo(self, key: str) -> QComboBox | None:
        """Direct access to a combo widget by key."""
        return self._combos.get(key)

    def get_date_from(self) -> ModernDatePicker | None:
        return self._date_from

    def get_date_to(self) -> ModernDatePicker | None:
        return self._date_to

    # ── Internal ──

    def _emit_change(self, *_args):
        """Emit filters_changed with current filter values."""
        self.filters_changed.emit(self.get_filters())
