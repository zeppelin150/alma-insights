"""
Alma Insights -- Chart Builder Utilities

Standardized helpers for building chart + legend sections.

Widgets:
  - ChartLegendSection   : standalone CollapsibleSection for series legends
  - TRCDropdownSelector  : Lightdash-style dropdown checklist with hover tooltips
  - build_chart_section() : factory that returns chart + legend + mode switcher

Usage:
    from src.ui.widgets.chart_builders import (
        ChartLegendSection, TRCDropdownSelector, build_chart_section,
    )

    # Quick factory:
    section, chart, legend, switcher = build_chart_section(
        "Sentiment Trend", "trending.sentiment_trend",
    )
    layout.addWidget(section)
    layout.addWidget(legend)

    # After updating data:
    chart.set_data(data)
    legend.update_legend(list(data.keys()))
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QCheckBox, QFrame, QScrollArea,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_MID, ALMA_GREEN_SUBTLE,
    ALMA_WHITE, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_TEXT_ON_DARK, ALMA_BORDER, ALMA_BORDER_LIGHT, ALMA_HOVER_LIGHT,
    ALMA_BG_ELEVATED, ALMA_CHART_PALETTE,
    apply_card_shadow_soft,
)
from src.ui.widgets.collapsible_section import CollapsibleSection
from src.ui.widgets.charts import LineChartWidget, ChartModeSwitcher, ChartMode


# Series color palette (same as LineChartWidget)
SERIES_COLORS = ALMA_CHART_PALETTE + [
    "#7B61FF", "#E06666", "#6AA84F", "#CC4125",
]


# ═══════════════════════════════════════════
#  CHART LEGEND SECTION
# ═══════════════════════════════════════════

class ChartLegendSection(CollapsibleSection):
    """Standalone CollapsibleSection displaying a color-coded series legend.

    Place directly above the chart's CollapsibleSection.  The title
    includes the chart name so the user can see which chart the legend
    relates to.

    Usage::

        legend = ChartLegendSection("Sentiment Trend")
        parent_layout.addWidget(legend)

        # After updating chart data:
        legend.update_legend(list(chart_data.keys()))
    """

    def __init__(self, chart_title="Chart", section_key="", parent=None):
        super().__init__(
            f"Legend \u2014 {chart_title}",
            section_key=section_key,
            show_expand_button=False,
            parent=parent,
        )

        # Relationship note
        note = QLabel("\u2193 Legend for the chart section below")
        note.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; "
            "border: none; font-style: italic; padding: 2px 0 6px 0;"
        )
        self.add_widget(note)

        # Legend label (rich-text, word-wrap for auto flow)
        self._legend_label = QLabel()
        self._legend_label.setWordWrap(True)
        self._legend_label.setTextFormat(Qt.RichText)
        self._legend_label.setStyleSheet(
            f"font-size: 12px; color: {ALMA_TEXT_MID}; "
            "border: none; line-height: 2.2; padding: 4px 0 8px 0;"
        )
        self.add_widget(self._legend_label)

    def update_legend(self, series_labels, colors=None):
        """Rebuild the legend with colored dots + series labels.

        Parameters
        ----------
        series_labels : list[str]
            Series names in display order.
        colors : list[str] | None
            Optional list of hex color strings.  Falls back to the
            default chart palette.
        """
        if not series_labels:
            self._legend_label.setText(
                f'<span style="color:{ALMA_TEXT_LIGHT};">No series selected</span>'
            )
            return

        if colors is None:
            colors = SERIES_COLORS

        parts = []
        for i, label in enumerate(series_labels):
            c = colors[i % len(colors)]
            # Use a CSS-styled inline block so items wrap nicely
            parts.append(
                f'<span style="white-space:nowrap;">'
                f'<span style="color:{c}; font-size:13px;">\u25cf</span>'
                f'&nbsp;<span style="color:{ALMA_TEXT_DARK};">{label}</span>'
                f'</span>'
            )

        self._legend_label.setText("&nbsp;&nbsp;&nbsp;&nbsp;".join(parts))


# ═══════════════════════════════════════════
#  TRC DROPDOWN SELECTOR  (Lightdash-style)
# ═══════════════════════════════════════════

class TRCDropdownSelector(QWidget):
    """Lightdash-style dropdown checklist for TRC selection.

    Compact trigger button that expands to reveal a full checklist
    with search, quick-filter buttons, and hover tooltips showing
    a per-TRC sentiment readout.

    Signals
    -------
    selection_changed(object)
        Emitted with a ``set`` of currently-selected TRC codes
        every time the selection changes.
    """

    selection_changed = Signal(object)   # set of selected TRC codes

    def __init__(self, parent=None):
        super().__init__(parent)
        self._expanded = False
        self._checkboxes = {}        # {trc_code: QCheckBox}
        self._cb_rows = {}           # {trc_code: QFrame}  (row widgets)
        self._sentiment_data = {}    # {trc_code: [(window, compound), ...]}
        self._build_ui()

    # ── UI Construction ──────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Trigger row: dropdown button + quick filters ──
        trigger_row = QHBoxLayout()
        trigger_row.setSpacing(6)

        self._trigger_btn = QPushButton("\u25be Select TRCs (0 selected)")
        self._trigger_btn.setCursor(Qt.PointingHandCursor)
        self._trigger_btn.setFixedHeight(32)
        self._trigger_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_WHITE}; color: {ALMA_TEXT_DARK};
                border: 1px solid {ALMA_BORDER}; border-radius: 8px;
                padding: 6px 14px; font-size: 12px; font-weight: 600;
                text-align: left;
            }}
            QPushButton:hover {{
                border-color: {ALMA_GREEN_MID}; background: {ALMA_CREAM};
            }}
        """)
        self._trigger_btn.clicked.connect(self._toggle_dropdown)
        trigger_row.addWidget(self._trigger_btn, 1)

        # Quick-filter buttons (always visible)
        _qf_style = f"""
            QPushButton {{
                background: transparent; color: {ALMA_TEXT_MID};
                border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 6px;
                padding: 4px 10px; font-size: 11px; font-weight: 500;
            }}
            QPushButton:hover {{
                background: rgba(3,40,27,0.06); color: {ALMA_GREEN_DARK};
            }}
        """
        for label, slot in [
            ("Top 10", self._filter_top10),
            ("Top 25", self._filter_top25),
            ("All", self._filter_all),
            ("Clear", self._filter_clear),
        ]:
            btn = QPushButton(label)
            btn.setFixedHeight(32)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(_qf_style)
            btn.clicked.connect(slot)
            trigger_row.addWidget(btn)

        layout.addLayout(trigger_row)

        # ── Dropdown panel (hidden by default) ──
        self._dropdown = QFrame()
        self._dropdown.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_WHITE};
                border: 1px solid {ALMA_BORDER};
                border-radius: 10px;
                margin-top: 4px;
            }}
        """)
        apply_card_shadow_soft(self._dropdown)
        self._dropdown.setVisible(False)

        dd_layout = QVBoxLayout(self._dropdown)
        dd_layout.setContentsMargins(12, 10, 12, 10)
        dd_layout.setSpacing(6)

        # Search box
        self._search = QLineEdit()
        self._search.setPlaceholderText("\U0001f50d Search TRCs...")
        self._search.setFixedHeight(30)
        self._search.setStyleSheet(f"""
            QLineEdit {{
                background: {ALMA_BG_ELEVATED};
                border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 8px; padding: 4px 12px; font-size: 12px;
            }}
            QLineEdit:focus {{ border-color: {ALMA_GREEN_MID}; }}
        """)
        self._search.textChanged.connect(self._on_search_changed)
        dd_layout.addWidget(self._search)

        # Scrollable checkbox list
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setMaximumHeight(260)
        self._scroll.setMinimumHeight(100)
        self._scroll.setStyleSheet(f"""
            QScrollArea {{ border: none; background: transparent; }}
            QScrollBar:vertical {{
                width: 6px; background: transparent;
            }}
            QScrollBar::handle:vertical {{
                background: {ALMA_BORDER}; border-radius: 3px;
                min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
        """)

        self._check_container = QWidget()
        self._check_layout = QVBoxLayout(self._check_container)
        self._check_layout.setContentsMargins(0, 0, 0, 0)
        self._check_layout.setSpacing(1)
        self._scroll.setWidget(self._check_container)
        dd_layout.addWidget(self._scroll)

        layout.addWidget(self._dropdown)

    # ── Public API ───────────────────────────────────────

    def set_trc_data(self, sentiment_data):
        """Set the full TRC sentiment data and rebuild checkboxes.

        Parameters
        ----------
        sentiment_data : dict
            ``{trc_code: [(window_label, avg_compound), ...]}``
        """
        self._sentiment_data = sentiment_data or {}
        self._rebuild_checkboxes()

    def selected_trcs(self):
        """Return the set of currently checked TRC codes."""
        return {trc for trc, cb in self._checkboxes.items() if cb.isChecked()}

    def select_top_n(self, n):
        """Programmatically select the top *n* TRCs by data-point count."""
        self._select_top_n(n)

    # ── Internals ────────────────────────────────────────

    def _rebuild_checkboxes(self):
        """Clear and rebuild TRC checkbox rows sorted by data-point count."""
        while self._check_layout.count():
            item = self._check_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._checkboxes.clear()
        self._cb_rows.clear()

        sorted_trcs = sorted(
            self._sentiment_data.keys(),
            key=lambda t: len(self._sentiment_data[t]),
            reverse=True,
        )

        for trc in sorted_trcs:
            row = self._make_trc_row(trc)
            self._check_layout.addWidget(row)

        self._check_layout.addStretch()

    def _make_trc_row(self, trc_code):
        """Create a single TRC checkbox row with hover tooltip."""
        row = QFrame()
        row.setStyleSheet(f"""
            QFrame {{
                background: transparent; border: none;
                border-radius: 6px;
            }}
            QFrame:hover {{ background: {ALMA_HOVER_LIGHT}; }}
        """)

        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(8, 4, 8, 4)
        row_layout.setSpacing(8)

        cb = QCheckBox(trc_code)
        cb.setStyleSheet(f"""
            QCheckBox {{
                font-size: 12px; color: {ALMA_TEXT_DARK}; spacing: 6px;
            }}
            QCheckBox::indicator {{ width: 15px; height: 15px; }}
        """)
        cb.stateChanged.connect(self._on_checkbox_changed)
        row_layout.addWidget(cb, 1)

        # Data-point count badge
        pts = self._sentiment_data.get(trc_code, [])
        count_lbl = QLabel(f"{len(pts)} pts")
        count_lbl.setStyleSheet(
            f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; border: none;"
        )
        row_layout.addWidget(count_lbl)

        # Build rich tooltip
        tooltip = self._build_trc_tooltip(trc_code)
        row.setToolTip(tooltip)
        cb.setToolTip(tooltip)

        self._checkboxes[trc_code] = cb
        self._cb_rows[trc_code] = row
        return row

    def _build_trc_tooltip(self, trc_code):
        """Build a rich tooltip with sentiment readout for a TRC."""
        pts = self._sentiment_data.get(trc_code, [])
        if not pts:
            return trc_code

        current = pts[-1][1] if pts else 0
        previous = pts[-2][1] if len(pts) >= 2 else 0
        delta = current - previous

        if delta < -0.05:
            trend = "\u2193 Declining"
        elif delta > 0.05:
            trend = "\u2191 Improving"
        else:
            trend = "\u2192 Stable"

        return (
            f"{trc_code}\n"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
            f"Current Sentiment:  {current:+.3f}\n"
            f"Previous:           {previous:+.3f}\n"
            f"Delta:              {delta:+.3f}\n"
            f"Trend:              {trend}\n"
            f"Data Points:        {len(pts)}"
        )

    # ── Toggle / Search / Selection ──────────────────────

    def _toggle_dropdown(self):
        self._expanded = not self._expanded
        self._dropdown.setVisible(self._expanded)
        self._update_trigger_text()
        if self._expanded:
            self._search.setFocus()

    def _update_trigger_text(self):
        selected = sum(1 for cb in self._checkboxes.values() if cb.isChecked())
        total = len(self._checkboxes)
        arrow = "\u25b4" if self._expanded else "\u25be"
        self._trigger_btn.setText(
            f"{arrow} Select TRCs ({selected} of {total} selected)"
        )

    def _on_checkbox_changed(self):
        self._update_trigger_text()
        self.selection_changed.emit(self.selected_trcs())

    def _on_search_changed(self, text):
        text_lower = text.strip().lower()
        for trc, row in self._cb_rows.items():
            row.setVisible(text_lower in trc.lower() if text_lower else True)

    # ── Quick filters ────────────────────────────────────

    def _filter_top10(self):
        self._select_top_n(10)

    def _filter_top25(self):
        self._select_top_n(25)

    def _filter_all(self):
        for cb in self._checkboxes.values():
            cb.blockSignals(True)
            cb.setChecked(True)
            cb.blockSignals(False)
        self._update_trigger_text()
        self.selection_changed.emit(self.selected_trcs())

    def _filter_clear(self):
        for cb in self._checkboxes.values():
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
        self._update_trigger_text()
        self.selection_changed.emit(self.selected_trcs())

    def _select_top_n(self, n):
        sorted_trcs = sorted(
            self._sentiment_data.keys(),
            key=lambda t: len(self._sentiment_data[t]),
            reverse=True,
        )
        top_set = set(sorted_trcs[:n])
        for trc, cb in self._checkboxes.items():
            cb.blockSignals(True)
            cb.setChecked(trc in top_set)
            cb.blockSignals(False)
        self._update_trigger_text()
        self.selection_changed.emit(self.selected_trcs())


# ═══════════════════════════════════════════
#  CHART SECTION FACTORY
# ═══════════════════════════════════════════

def build_chart_section(chart_title, section_key_prefix,
                        chart_min_height=400):
    """Create a complete chart + legend + mode switcher group.

    Returns
    -------
    tuple
        ``(chart_section, chart_widget, legend_section, mode_switcher)``

    Example::

        section, chart, legend, switcher = build_chart_section(
            "Pattern Growth", "taxonomy.pattern_growth",
        )
        parent_layout.addWidget(section)
        parent_layout.addWidget(legend)

        chart.set_data(data)
        legend.update_legend(list(data.keys()))
    """
    # Main chart section
    chart_section = CollapsibleSection(
        chart_title,
        section_key=section_key_prefix,
    )

    mode_switcher = ChartModeSwitcher()
    chart_section.add_widget(mode_switcher)

    chart = LineChartWidget()
    chart.setMinimumHeight(chart_min_height)
    mode_switcher.mode_changed.connect(chart.set_chart_mode)
    chart_section.add_widget(chart)

    # Legend section (placed below chart section by caller)
    legend = ChartLegendSection(
        chart_title=chart_title,
        section_key=f"{section_key_prefix}.legend",
    )

    return chart_section, chart, legend, mode_switcher
