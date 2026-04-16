"""
Alma Insights — TRC History Panel Widget
Volume sparkline, top issues bar chart, ngram trends, related TRCs.

Session 4: Multi-source persistent database architecture.
"""

import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QSizePolicy,
)
from PySide6.QtCore import Qt

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_CREAM, ALMA_WHITE,
    ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT, ALMA_BORDER_LIGHT,
    ALMA_SUCCESS, ALMA_WARNING, ALMA_ERROR,
)

logger = logging.getLogger("alma.ui.trc_history_panel")

# Unicode block elements for sparkline
SPARK_CHARS = " ▁▂▃▄▅▆▇█"


def _sparkline(values: list[int]) -> str:
    """Convert a list of counts to a Unicode sparkline string."""
    if not values:
        return ""
    max_val = max(values) if max(values) > 0 else 1
    return "".join(
        SPARK_CHARS[min(int(v / max_val * 8), 8)] for v in values
    )


class TRCHistoryPanel(QWidget):
    """Displays TRC history: volume sparkline, top issues, ngram trends, related TRCs.

    Fed by WarehouseQuery.get_trc_history() data.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._trc_code = None
        self._build_ui()
        self._show_placeholder()

    def _show_placeholder(self):
        """Show placeholder when no TRC selected."""
        self._header_label.setText("── TRC History ── Select a ticket to view TRC history")

    def _show_active(self, trc_code):
        """Show active header with TRC code."""
        self._header_label.setText(f"── TRC History: {trc_code} ──")

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(6)

        # Section header
        self._header_label = QLabel("── TRC History ──")
        self._header_label.setStyleSheet(
            f"font-size: 12px; font-weight: 700; color: {ALMA_TEXT_MID}; "
            f"margin-bottom: 2px;"
        )
        layout.addWidget(self._header_label)

        # Container
        self._container = QFrame()
        self._container.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_WHITE};
                border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 6px;
                padding: 12px;
            }}
        """)
        container_layout = QVBoxLayout(self._container)
        container_layout.setContentsMargins(12, 8, 12, 8)
        container_layout.setSpacing(8)

        # Volume row: sparkline + stats
        volume_row = QHBoxLayout()
        volume_row.setSpacing(16)

        self._volume_label = QLabel("")
        self._volume_label.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_DARK};")
        volume_row.addWidget(self._volume_label)

        self._stats_label = QLabel("")
        self._stats_label.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_MID};")
        volume_row.addWidget(self._stats_label)
        volume_row.addStretch()

        container_layout.addLayout(volume_row)

        # Top issues
        self._issues_header = QLabel("Top Issues:")
        self._issues_header.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {ALMA_TEXT_DARK}; margin-top: 4px;"
        )
        container_layout.addWidget(self._issues_header)

        self._issues_container = QVBoxLayout()
        self._issues_container.setSpacing(3)
        container_layout.addLayout(self._issues_container)

        # Related TRCs
        self._related_label = QLabel("")
        self._related_label.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_MID}; margin-top: 6px;")
        self._related_label.setWordWrap(True)
        container_layout.addWidget(self._related_label)

        layout.addWidget(self._container)

    # ── Public API ────────────────────────────────

    def set_trc_data(self, trc_code: str, history: dict):
        """Populate panel with TRC history data.

        Args:
            trc_code: The TRC code being displayed
            history: Dict from WarehouseQuery.get_trc_history()
        """
        self._trc_code = trc_code

        if not history or history.get("total", 0) == 0:
            self._show_placeholder()
            return

        self._show_active(trc_code)

        # Volume sparkline
        volume = history.get("volume_by_day", [])
        counts = [v[1] for v in volume] if volume else []
        spark = _sparkline(counts)
        self._volume_label.setText(
            f"<b>Volume (last 90 days):</b> "
            f"<span style='font-size: 14px; letter-spacing: 1px;'>{spark}</span>"
        )
        self._volume_label.setTextFormat(Qt.RichText)

        # Stats
        total = history.get("total", 0)
        avg_csat = history.get("avg_csat")
        csat_str = f"{avg_csat}" if avg_csat is not None else "N/A"

        # Determine trend from volume
        trend_str = self._calc_trend(counts)
        self._stats_label.setText(
            f"Total: <b>{total}</b>  |  Avg CSAT: <b>{csat_str}</b>  |  Trend: {trend_str}"
        )
        self._stats_label.setTextFormat(Qt.RichText)

        # Top issues bar chart
        self._clear_layout(self._issues_container)
        top_issues = history.get("top_issues", [])
        if top_issues:
            max_count = top_issues[0][1] if top_issues else 1
            for issue, count in top_issues[:5]:
                pct = count / total * 100 if total > 0 else 0
                bar_width = max(int(count / max_count * 200), 4)
                row = self._make_bar_row(issue, pct, bar_width)
                self._issues_container.addWidget(row)
        else:
            empty = QLabel("No issue breakdown available.")
            empty.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT};")
            self._issues_container.addWidget(empty)

        # Related TRCs
        related = history.get("related_trcs", [])
        if related:
            parts = [f"{code} (co-occurrence: {pct}%)" for code, pct in related]
            self._related_label.setText(f"Related TRCs: {', '.join(parts)}")
            self._related_label.show()
        else:
            self._related_label.hide()

    def clear(self):
        """Reset to empty/hidden state."""
        self._trc_code = None
        self.hide()

    # ── Helpers ────────────────────────────────────

    def _calc_trend(self, counts: list[int]) -> str:
        """Determine trend direction from volume counts."""
        if len(counts) < 4:
            return "insufficient data"
        half = len(counts) // 2
        first_half = sum(counts[:half])
        second_half = sum(counts[half:])

        if first_half == 0 and second_half == 0:
            return "no activity"
        if first_half == 0:
            return f'<span style="color: {ALMA_ERROR};">\u2191 rising</span>'
        ratio = (second_half - first_half) / first_half
        if ratio > 0.15:
            return f'<span style="color: {ALMA_ERROR};">\u2191 rising ({ratio:+.0%})</span>'
        elif ratio < -0.15:
            return f'<span style="color: {ALMA_SUCCESS};">\u2193 declining ({ratio:+.0%})</span>'
        else:
            return '<span style="color: #7A7A7A;">\u2192 stable</span>'

    def _make_bar_row(self, label: str, pct: float, bar_width: int) -> QWidget:
        """Create a horizontal bar chart row."""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        bar = QFrame()
        bar.setFixedSize(bar_width, 14)
        bar.setStyleSheet(
            f"background: {ALMA_GREEN_LIGHT}; border-radius: 3px;"
        )
        layout.addWidget(bar)

        text = QLabel(f"{label} ({pct:.0f}%)")
        text.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_DARK};")
        layout.addWidget(text)
        layout.addStretch()

        return row

    def _clear_layout(self, layout):
        """Remove all widgets from a layout."""
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
