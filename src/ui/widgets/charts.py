"""
Alma Insights — Custom Chart Widgets  (Polished Edition)
QPainter-based charts: BarChart, BoxPlot, Heatmap, LineChart, Sparkline.
No external charting libraries required.

Visual polish:
  - Metabase-style rendering: smooth curves, solid grids, gradient fills
  - Crosshair hover with snap-to-point indicators
  - Chart mode switching: Line, Area, Bar, Stepped
  - Hover tooltips with exact values
  - Pagination support (set_page / page_size) on bar, box, heatmap
"""

import math
from enum import Enum, auto
from PySide6.QtWidgets import QWidget, QToolTip, QHBoxLayout, QPushButton
from PySide6.QtCore import Qt, QRectF, QPointF, Signal, QSize
from PySide6.QtGui import (
    QPainter, QPen, QColor, QFont, QFontMetrics, QBrush, QPainterPath,
    QLinearGradient,
)

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_MID, ALMA_GREEN_LIGHT, ALMA_GREEN_SUBTLE,
    ALMA_CREAM, ALMA_WHITE, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_TEXT_ON_DARK, ALMA_BORDER, ALMA_BORDER_LIGHT, ALMA_HOVER_LIGHT,
    ALMA_SUCCESS, ALMA_WARNING, ALMA_ERROR, ALMA_INFO, ALMA_BG_ELEVATED,
    ALMA_CHART_BG, ALMA_CHART_GRID, ALMA_CHART_AXIS, ALMA_CHART_INSET,
    ALMA_CHART_PALETTE,
)


# ═══════════════════════════════════════════
#  SHARED PAINT HELPERS
# ═══════════════════════════════════════════

def _paint_chart_bg(painter: QPainter, rect: QRectF, radius: float = 8):
    """Transparent chart background — lets the parent card show through.

    Previously drew a filled rounded rect + inner border that blocked text
    and created an unwanted "ovular" frame inside the already-shadowed card.
    Now a no-op so chart content paints directly on the card background.
    """
    pass


def _paint_empty_state(painter: QPainter, rect: QRectF, icon: str, message: str):
    """Draw a centered empty-state placeholder with icon + text."""
    # Light tint only for empty state so it doesn't look blank
    painter.setBrush(QBrush(QColor(ALMA_CHART_BG)))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(rect, 8, 8)
    cy = rect.center().y()
    # Icon
    painter.setFont(QFont("Segoe UI", 24))
    painter.setPen(QColor(ALMA_BORDER))
    painter.drawText(
        QRectF(rect.x(), cy - 30, rect.width(), 36),
        Qt.AlignCenter, icon,
    )
    # Message
    painter.setFont(QFont("Segoe UI", 12))
    painter.setPen(QColor(ALMA_TEXT_LIGHT))
    painter.drawText(
        QRectF(rect.x(), cy + 10, rect.width(), 24),
        Qt.AlignCenter, message,
    )


def _dotted_pen(color_hex: str, width: float = 1.0) -> QPen:
    """Create a dotted pen for grid lines."""
    pen = QPen(QColor(color_hex), width)
    pen.setStyle(Qt.DotLine)
    return pen


# ═══════════════════════════════════════════
#  HORIZONTAL BAR CHART
# ═══════════════════════════════════════════

class BarChartWidget(QWidget):
    """Horizontal bar chart with gradient fills, grid lines, and pagination."""

    bar_clicked = Signal(str)  # emits label

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_data = []     # full dataset [(label, value), ...]
        self._data = []         # visible page slice
        self._page = 0
        self._page_size = 10
        self._bar_color = QColor(ALMA_GREEN_LIGHT)
        self._bar_height = 26
        self._bar_spacing = 6
        self._label_width = 180
        self._hovered_index = -1
        self._total_sum = 0
        self.setMouseTracking(True)

    def set_data(self, data, max_items=None):
        """Set full chart data. Sorted descending by value.
        Pagination replaces the old max_items truncation."""
        self._all_data = list(data) if data else []
        self._total_sum = sum(v for _, v in self._all_data) if self._all_data else 0
        self._page = 0
        self._apply_page()

    def set_page(self, page: int, page_size: int = None):
        """Switch to the given page (0-indexed). Called by PaginationBar."""
        if page_size is not None:
            self._page_size = page_size
        self._page = page
        self._apply_page()

    def _apply_page(self):
        start = self._page * self._page_size
        end = start + self._page_size
        self._data = self._all_data[start:end]
        self.setMinimumHeight(self._calc_height())
        self.update()

    def _calc_height(self):
        n = len(self._data)
        h = 4 + n * (self._bar_height + self._bar_spacing) + 4
        return max(h, 60)

    def sizeHint(self):
        return QSize(400, self._calc_height())

    def paintEvent(self, event):
        if not self._data:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            _paint_empty_state(painter, QRectF(self.rect()), "\U0001f4ca", "No volume data available")
            painter.end()
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        h = self.height()

        # Inset background
        _paint_chart_bg(painter, QRectF(0, 0, w, h))

        max_val = max(v for _, v in self._data) if self._data else 1
        if max_val == 0:
            max_val = 1

        y = 4
        label_font = QFont("Segoe UI", 11)
        value_font = QFont("Segoe UI", 10, QFont.Bold)
        fm = QFontMetrics(label_font)

        bar_area_left = self._label_width + 14
        bar_area_width = w - bar_area_left - 64

        # Vertical grid lines (4 ticks)
        for frac in (0.25, 0.50, 0.75, 1.0):
            gx = bar_area_left + frac * bar_area_width
            painter.setPen(_dotted_pen(ALMA_CHART_GRID))
            painter.drawLine(QPointF(gx, 2), QPointF(gx, h - 2))

        for i, (label, value) in enumerate(self._data):
            bar_y = y + i * (self._bar_height + self._bar_spacing)

            # Label
            painter.setFont(label_font)
            painter.setPen(QColor(ALMA_CHART_AXIS))
            elided = fm.elidedText(label, Qt.ElideRight, self._label_width)
            painter.drawText(
                QRectF(8, bar_y, self._label_width, self._bar_height),
                Qt.AlignVCenter | Qt.AlignRight, elided
            )

            # Bar — gradient fill
            bar_w = max(4, (value / max_val) * bar_area_width)
            bar_rect = QRectF(bar_area_left, bar_y + 2, bar_w, self._bar_height - 4)

            grad = QLinearGradient(bar_rect.topLeft(), bar_rect.topRight())
            if i == self._hovered_index:
                grad.setColorAt(0, QColor(ALMA_GREEN_SUBTLE).lighter(115))
                grad.setColorAt(1, QColor(ALMA_GREEN_SUBTLE))
            else:
                grad.setColorAt(0, QColor(ALMA_GREEN_LIGHT).lighter(115))
                grad.setColorAt(1, self._bar_color)

            painter.setBrush(QBrush(grad))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(bar_rect, 4, 4)

            # Value label
            painter.setFont(value_font)
            painter.setPen(QColor(ALMA_TEXT_DARK))
            val_str = f"{value:,.0f}" if isinstance(value, float) else f"{value:,}"
            painter.drawText(
                QRectF(bar_area_left + bar_w + 8, bar_y, 54, self._bar_height),
                Qt.AlignVCenter | Qt.AlignLeft, val_str
            )

        painter.end()

    def mouseMoveEvent(self, event):
        y = 4
        idx = -1
        for i in range(len(self._data)):
            bar_y = y + i * (self._bar_height + self._bar_spacing)
            if bar_y <= event.position().y() <= bar_y + self._bar_height:
                idx = i
                break

        if idx != self._hovered_index:
            self._hovered_index = idx
            self.update()

        # Tooltip
        if 0 <= idx < len(self._data):
            label, val = self._data[idx]
            pct = (val / self._total_sum * 100) if self._total_sum else 0
            val_str = f"{val:,.0f}" if isinstance(val, float) else f"{val:,}"
            QToolTip.showText(
                event.globalPosition().toPoint(),
                f"{label}\n{val_str}  ({pct:.1f}%)"
            )

    def mousePressEvent(self, event):
        if 0 <= self._hovered_index < len(self._data):
            self.bar_clicked.emit(self._data[self._hovered_index][0])

    def leaveEvent(self, event):
        self._hovered_index = -1
        self.update()


# ═══════════════════════════════════════════
#  BOX PLOT WIDGET
# ═══════════════════════════════════════════

class BoxPlotWidget(QWidget):
    """Horizontal box plot chart with gradient fills, outlier dots, and pagination."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_data = {}       # full {label: [values]}
        self._all_labels = []     # full sorted label list
        self._data = {}           # visible page slice
        self._sorted_labels = []  # visible page labels
        self._page = 0
        self._page_size = 10
        self._box_height = 28
        self._box_spacing = 8
        self._label_width = 180
        self._hovered_index = -1
        self.setMouseTracking(True)

    def set_data(self, data, max_items=None, min_samples=5):
        """Set data. Filters to labels with >= min_samples values."""
        if not data:
            self._all_data = {}
            self._all_labels = []
            self._page = 0
            self._apply_page()
            return

        filtered = {k: v for k, v in data.items() if len(v) >= min_samples}
        if not filtered:
            self._all_data = {}
            self._all_labels = []
            self._page = 0
            self._apply_page()
            return

        # Sort by median descending
        try:
            import numpy as np
            self._all_labels = sorted(
                filtered.keys(),
                key=lambda k: np.median(filtered[k]),
                reverse=True
            )
        except ImportError:
            self._all_labels = sorted(
                filtered.keys(),
                key=lambda k: sorted(filtered[k])[len(filtered[k]) // 2],
                reverse=True
            )

        self._all_data = {k: filtered[k] for k in self._all_labels}
        self._page = 0
        self._apply_page()

    def set_page(self, page: int, page_size: int = None):
        if page_size is not None:
            self._page_size = page_size
        self._page = page
        self._apply_page()

    def _apply_page(self):
        start = self._page * self._page_size
        end = start + self._page_size
        self._sorted_labels = self._all_labels[start:end]
        self._data = {k: self._all_data[k] for k in self._sorted_labels}
        self.setMinimumHeight(self._calc_height())
        self.update()

    def _calc_height(self):
        n = len(self._sorted_labels)
        return max(60, 4 + n * (self._box_height + self._box_spacing) + 28)

    def sizeHint(self):
        return QSize(400, self._calc_height())

    def paintEvent(self, event):
        if not self._data:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            _paint_empty_state(painter, QRectF(self.rect()), "\U0001f4e6", "Insufficient data for box plots")
            painter.end()
            return

        try:
            import numpy as np
        except ImportError:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            _paint_empty_state(painter, QRectF(self.rect()), "\u26a0\ufe0f", "NumPy required for box plots")
            painter.end()
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        h = self.height()

        # Inset background
        _paint_chart_bg(painter, QRectF(0, 0, w, h))

        # Find global min/max for X axis
        all_vals = []
        for vals in self._data.values():
            all_vals.extend(vals)
        global_min = min(all_vals) if all_vals else 0
        global_max = max(all_vals) if all_vals else 1
        if global_max == global_min:
            global_max = global_min + 1

        plot_left = self._label_width + 16
        plot_width = w - plot_left - 20
        y_start = 4

        label_font = QFont("Segoe UI", 10)
        small_font = QFont("Segoe UI", 9)
        fm = QFontMetrics(label_font)

        def x_pos(val):
            return plot_left + ((val - global_min) / (global_max - global_min)) * plot_width

        # Vertical grid lines at quartile positions
        for frac in (0.0, 0.25, 0.50, 0.75, 1.0):
            gx = plot_left + frac * plot_width
            painter.setPen(_dotted_pen(ALMA_CHART_GRID))
            painter.drawLine(QPointF(gx, 2), QPointF(gx, h - 28))

        for i, label in enumerate(self._sorted_labels):
            vals = np.array(self._data[label])
            q1, median, q3 = np.percentile(vals, [25, 50, 75])
            iqr = q3 - q1
            whisker_low = max(vals.min(), q1 - 1.5 * iqr)
            whisker_high = min(vals.max(), q3 + 1.5 * iqr)

            cy = y_start + i * (self._box_height + self._box_spacing) + self._box_height / 2

            # Label
            painter.setFont(label_font)
            painter.setPen(QColor(ALMA_CHART_AXIS))
            elided = fm.elidedText(label, Qt.ElideRight, self._label_width)
            painter.drawText(
                QRectF(8, cy - self._box_height / 2, self._label_width, self._box_height),
                Qt.AlignVCenter | Qt.AlignRight, elided
            )

            # Whisker line
            painter.setPen(QPen(QColor(ALMA_TEXT_LIGHT), 1))
            painter.drawLine(
                QPointF(x_pos(whisker_low), cy),
                QPointF(x_pos(whisker_high), cy)
            )

            # Whisker caps
            cap_h = self._box_height * 0.3
            for wv in (whisker_low, whisker_high):
                wx = x_pos(wv)
                painter.drawLine(QPointF(wx, cy - cap_h), QPointF(wx, cy + cap_h))

            # Box (Q1 to Q3) — gradient fill
            box_x = x_pos(q1)
            box_w = max(2, x_pos(q3) - box_x)
            box_rect = QRectF(box_x, cy - self._box_height * 0.35,
                              box_w, self._box_height * 0.7)

            box_grad = QLinearGradient(box_rect.topLeft(), box_rect.bottomLeft())
            box_grad.setColorAt(0, QColor(ALMA_INFO).lighter(155))
            box_grad.setColorAt(1, QColor(ALMA_INFO).lighter(130))
            painter.setBrush(QBrush(box_grad))
            painter.setPen(QPen(QColor(ALMA_INFO), 1))
            painter.drawRoundedRect(box_rect, 3, 3)

            # Median line
            median_x = x_pos(median)
            painter.setPen(QPen(QColor(ALMA_GREEN_DARK), 2))
            painter.drawLine(
                QPointF(median_x, cy - self._box_height * 0.35),
                QPointF(median_x, cy + self._box_height * 0.35)
            )

            # Outlier dots (beyond whiskers)
            outliers = vals[(vals < whisker_low) | (vals > whisker_high)]
            if len(outliers) > 0:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(QColor(ALMA_WARNING).lighter(120)))
                for ov in outliers[:20]:  # cap at 20 dots per row
                    ox = x_pos(ov)
                    painter.drawEllipse(QPointF(ox, cy), 2.5, 2.5)

        # X-axis labels
        painter.setFont(small_font)
        painter.setPen(QColor(ALMA_CHART_AXIS))
        axis_y = y_start + len(self._sorted_labels) * (self._box_height + self._box_spacing) + 4
        for frac in [0, 0.25, 0.5, 0.75, 1.0]:
            val = global_min + frac * (global_max - global_min)
            px = x_pos(val)
            painter.drawText(QRectF(px - 25, axis_y, 50, 16), Qt.AlignCenter, f"{val:.0f}h")

        painter.end()

    def mouseMoveEvent(self, event):
        if not self._sorted_labels:
            return
        try:
            import numpy as np
        except ImportError:
            return

        y_start = 4
        pos_y = event.position().y()
        for i, label in enumerate(self._sorted_labels):
            cy = y_start + i * (self._box_height + self._box_spacing) + self._box_height / 2
            if abs(pos_y - cy) <= self._box_height / 2:
                vals = np.array(self._data[label])
                q1, med, q3 = np.percentile(vals, [25, 50, 75])
                QToolTip.showText(
                    event.globalPosition().toPoint(),
                    f"{label}\nQ1: {q1:.1f}h  Median: {med:.1f}h  Q3: {q3:.1f}h\n"
                    f"Range: {vals.min():.1f}h \u2013 {vals.max():.1f}h  (n={len(vals)})"
                )
                return

    def _paint_empty(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        _paint_empty_state(painter, QRectF(self.rect()), "\U0001f4e6", "Insufficient data for box plots")
        painter.end()


# ═══════════════════════════════════════════
#  HEATMAP WIDGET
# ═══════════════════════════════════════════

class HeatmapWidget(QWidget):
    """Grid heatmap with color gradient, auto-scaling cells, and Y-axis pagination."""

    cell_clicked = Signal(str, str)  # emits (y_label, x_label)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_y_labels = []
        self._x_labels = []
        self._values = {}          # {(y_idx, x_idx): float_value}
        self._y_labels = []        # visible page slice
        self._page = 0
        self._page_size = 10
        self._cell_height = 32
        self._label_width = 140
        self._header_height = 40
        self._hovered_cell = None  # (yi, xi) tuple
        self.setMouseTracking(True)

    def set_data(self, y_labels, x_labels, values):
        """
        values: dict of {(y_idx, x_idx): avg_score} or None for missing.
        y_idx is global (0-indexed into y_labels).
        """
        self._all_y_labels = list(y_labels) if y_labels else []
        self._x_labels = list(x_labels) if x_labels else []
        self._values = values or {}
        self._page = 0
        self._apply_page()

    def set_page(self, page: int, page_size: int = None):
        if page_size is not None:
            self._page_size = page_size
        self._page = page
        self._apply_page()

    def _apply_page(self):
        start = self._page * self._page_size
        end = start + self._page_size
        self._y_labels = self._all_y_labels[start:end]
        self._page_offset = start  # for indexing into self._values
        self.setMinimumHeight(self._calc_height())
        self.update()

    @property
    def _cell_width(self):
        """Auto-scale cell width to fit available width."""
        n_cols = len(self._x_labels)
        if n_cols <= 0:
            return 64
        available = self.width() - self._label_width - 20
        cw = max(36, available / n_cols)
        return min(cw, 72)  # cap at 72px so cells don't get comically wide

    def _calc_height(self):
        return max(60, self._header_height + len(self._y_labels) * self._cell_height + 10)

    def sizeHint(self):
        return QSize(500, self._calc_height())

    def _color_for_score(self, score):
        """Map CSAT score 1-5 to a color."""
        if score is None:
            return QColor(ALMA_BORDER_LIGHT)
        if score <= 2.0:
            return QColor(ALMA_ERROR).lighter(140)
        elif score <= 3.0:
            return QColor(ALMA_WARNING).lighter(140)
        elif score <= 4.0:
            return QColor("#B8A000").lighter(150)
        else:
            return QColor(ALMA_SUCCESS).lighter(140)

    def paintEvent(self, event):
        if not self._y_labels or not self._x_labels:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            _paint_empty_state(painter, QRectF(self.rect()), "\U0001f4ca", "No CSAT data for heatmap")
            painter.end()
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        h = self.height()
        cw = self._cell_width

        # Inset background
        _paint_chart_bg(painter, QRectF(0, 0, w, h))

        label_font = QFont("Segoe UI", 10)
        header_font = QFont("Segoe UI", 9, QFont.Bold)
        value_font = QFont("Segoe UI", 9)
        fm = QFontMetrics(label_font)

        # Color legend (top-right)
        legend_x = w - 160
        legend_y = 8
        legend_w = 120
        legend_h = 12
        legend_grad = QLinearGradient(legend_x, legend_y, legend_x + legend_w, legend_y)
        legend_grad.setColorAt(0.0, QColor(ALMA_ERROR).lighter(140))
        legend_grad.setColorAt(0.33, QColor(ALMA_WARNING).lighter(140))
        legend_grad.setColorAt(0.66, QColor("#B8A000").lighter(150))
        legend_grad.setColorAt(1.0, QColor(ALMA_SUCCESS).lighter(140))
        painter.setBrush(QBrush(legend_grad))
        painter.setPen(QPen(QColor(ALMA_CHART_INSET), 0.5))
        painter.drawRoundedRect(QRectF(legend_x, legend_y, legend_w, legend_h), 3, 3)
        # Legend labels
        painter.setFont(QFont("Segoe UI", 7))
        painter.setPen(QColor(ALMA_CHART_AXIS))
        painter.drawText(QRectF(legend_x - 16, legend_y, 16, legend_h), Qt.AlignVCenter | Qt.AlignRight, "1")
        painter.drawText(QRectF(legend_x + legend_w + 2, legend_y, 16, legend_h), Qt.AlignVCenter | Qt.AlignLeft, "5")

        # Column headers
        painter.setFont(header_font)
        painter.setPen(QColor(ALMA_CHART_AXIS))
        for xi, xlabel in enumerate(self._x_labels):
            x = self._label_width + xi * cw
            painter.drawText(
                QRectF(x, self._header_height - 20, cw, 16),
                Qt.AlignCenter, xlabel
            )

        # Rows (paginated — only visible page)
        for vis_yi, ylabel in enumerate(self._y_labels):
            global_yi = self._page_offset + vis_yi
            y = self._header_height + vis_yi * self._cell_height

            # Row label
            painter.setFont(label_font)
            painter.setPen(QColor(ALMA_CHART_AXIS))
            elided = fm.elidedText(ylabel, Qt.ElideRight, self._label_width - 8)
            painter.drawText(
                QRectF(8, y, self._label_width - 8, self._cell_height),
                Qt.AlignVCenter | Qt.AlignRight, elided
            )

            # Cells
            for xi in range(len(self._x_labels)):
                x = self._label_width + xi * cw
                val = self._values.get((global_yi, xi))
                color = self._color_for_score(val)

                cell_rect = QRectF(x + 1, y + 1, cw - 2, self._cell_height - 2)

                # Hover highlight
                is_hovered = (self._hovered_cell == (vis_yi, xi))

                painter.setBrush(QBrush(color.lighter(108) if is_hovered else color))
                painter.setPen(Qt.NoPen)
                painter.drawRoundedRect(cell_rect, 4, 4)

                # Subtle bottom-right inner shadow
                shadow_pen = QPen(QColor(0, 0, 0, 15), 1)
                painter.setPen(shadow_pen)
                painter.drawLine(
                    QPointF(cell_rect.left() + 4, cell_rect.bottom()),
                    QPointF(cell_rect.right(), cell_rect.bottom()),
                )
                painter.drawLine(
                    QPointF(cell_rect.right(), cell_rect.top() + 4),
                    QPointF(cell_rect.right(), cell_rect.bottom()),
                )

                # Value text
                if val is not None:
                    painter.setFont(value_font)
                    painter.setPen(QColor(ALMA_TEXT_DARK))
                    painter.drawText(cell_rect, Qt.AlignCenter, f"{val:.1f}")

        painter.end()

    def mouseMoveEvent(self, event):
        if not self._y_labels or not self._x_labels:
            return
        cw = self._cell_width
        mx, my = event.position().x(), event.position().y()

        xi = int((mx - self._label_width) / cw) if cw > 0 else -1
        vis_yi = int((my - self._header_height) / self._cell_height)

        if 0 <= xi < len(self._x_labels) and 0 <= vis_yi < len(self._y_labels):
            new_cell = (vis_yi, xi)
            if new_cell != self._hovered_cell:
                self._hovered_cell = new_cell
                self.update()

            global_yi = self._page_offset + vis_yi
            val = self._values.get((global_yi, xi))
            trc = self._y_labels[vis_yi]
            period = self._x_labels[xi]
            val_str = f"{val:.2f}" if val is not None else "No data"
            QToolTip.showText(
                event.globalPosition().toPoint(),
                f"{trc}  |  {period}\nCSAT: {val_str}"
            )
        else:
            if self._hovered_cell is not None:
                self._hovered_cell = None
                self.update()

    def leaveEvent(self, event):
        if self._hovered_cell is not None:
            self._hovered_cell = None
            self.update()

    def mousePressEvent(self, event):
        if self._hovered_cell is not None:
            vis_yi, xi = self._hovered_cell
            if 0 <= vis_yi < len(self._y_labels) and 0 <= xi < len(self._x_labels):
                self.cell_clicked.emit(self._y_labels[vis_yi], self._x_labels[xi])

    def _paint_empty(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        _paint_empty_state(painter, QRectF(self.rect()), "\U0001f4ca", "No CSAT data for heatmap")
        painter.end()


# ═══════════════════════════════════════════
#  CHART MODE ENUM
# ═══════════════════════════════════════════

class ChartMode(Enum):
    """Rendering mode for LineChartWidget and related time-series charts.

    LINE: smooth curve with subtle gradient area below.
    AREA: smooth curve with opaque gradient area (emphasizes volume).
    BAR:  vertical bars at each data point (discrete values).
    """

    LINE = auto()      # Smooth line + subtle gradient area
    AREA = auto()      # Smooth line + opaque gradient area
    BAR = auto()       # Vertical bars at data points
    STEPPED = auto()   # Horizontal-then-vertical step function


# ═══════════════════════════════════════════
#  LINE CHART WIDGET  (Metabase-style)
# ═══════════════════════════════════════════

class LineChartWidget(QWidget):
    """
    Multi-series line chart — Metabase-style rendering.
    Smooth Catmull-Rom curves, solid grids, gradient area fills,
    crosshair hover with snap-to-point indicators.
    Supports LINE / AREA / BAR / STEPPED modes via set_chart_mode().
    Accepts {series_label: [(x_label, y_value), ...]} dict.
    """

    SERIES_COLORS = ALMA_CHART_PALETTE + [
        "#7B61FF", "#E06666", "#6AA84F", "#CC4125",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = {}
        self._x_labels = []
        self._y_min = -1.0
        self._y_max = 1.0
        self._show_zero_line = True
        self._show_bg_tint = True
        self._chart_mode = ChartMode.LINE
        self._hover_x_idx = -1          # crosshair: nearest x index
        self._margin_left = 50
        self._margin_right = 40
        self._margin_top = 20
        self._margin_bottom = 50
        self._legend_height = 0         # Legend now lives in ChartLegendSection
        self.setMinimumHeight(200)
        self.setMouseTracking(True)

    def set_chart_mode(self, mode: ChartMode):
        """Switch between LINE / AREA / BAR / STEPPED rendering."""
        self._chart_mode = mode
        self.update()

    def set_data(self, data, y_min=None, y_max=None, show_zero_line=True, show_bg_tint=False):
        """
        data: {series_label: [(x_label, y_value), ...]}
        All series should share the same x_labels.
        """
        self._data = data
        self._show_zero_line = show_zero_line
        self._show_bg_tint = show_bg_tint

        # Collect all x labels in order (from first series)
        self._x_labels = []
        if data:
            first_series = next(iter(data.values()))
            self._x_labels = [pt[0] for pt in first_series]

        # Legend height always 0 — legend now lives in standalone ChartLegendSection
        self._legend_height = 0

        # Determine Y range
        all_y = []
        for pts in data.values():
            all_y.extend(pt[1] for pt in pts)

        if y_min is not None:
            self._y_min = y_min
        elif all_y:
            self._y_min = min(all_y) - 0.1
        else:
            self._y_min = -1.0

        if y_max is not None:
            self._y_max = y_max
        elif all_y:
            self._y_max = max(all_y) + 0.1
        else:
            self._y_max = 1.0

        if self._y_max == self._y_min:
            self._y_max = self._y_min + 1

        self.update()

    # ── Catmull-Rom spline helpers ──────────────────────────

    @staticmethod
    def _catmull_rom_path(points, tension=0.3):
        """Convert QPointF list → smooth QPainterPath using Catmull-Rom → cubic Bézier."""
        path = QPainterPath()
        n = len(points)
        if n == 0:
            return path
        path.moveTo(points[0])
        if n == 1:
            return path
        if n == 2:
            path.lineTo(points[1])
            return path

        for i in range(n - 1):
            p0 = points[max(i - 1, 0)]
            p1 = points[i]
            p2 = points[min(i + 1, n - 1)]
            p3 = points[min(i + 2, n - 1)]

            cp1x = p1.x() + (p2.x() - p0.x()) * tension / 3.0
            cp1y = p1.y() + (p2.y() - p0.y()) * tension / 3.0
            cp2x = p2.x() - (p3.x() - p1.x()) * tension / 3.0
            cp2y = p2.y() - (p3.y() - p1.y()) * tension / 3.0

            path.cubicTo(cp1x, cp1y, cp2x, cp2y, p2.x(), p2.y())

        return path

    # ── Smart Y-axis label formatting ────────────────────

    @staticmethod
    def _fmt_y(val):
        """Format Y-axis value: omit '.0' on integers, keep 1 decimal otherwise."""
        if val == int(val):
            return str(int(val))
        return f"{val:.1f}"

    # ── Mode-specific series drawing ─────────────────────

    def _draw_smooth_line(self, painter, pts, color, plot_bottom, opaque_area=False):
        """LINE / AREA mode: smooth curve + gradient fill."""
        if len(pts) < 2:
            return
        # Gradient area fill
        curve = self._catmull_rom_path(pts)
        fill_path = QPainterPath(curve)
        fill_path.lineTo(pts[-1].x(), plot_bottom)
        fill_path.lineTo(pts[0].x(), plot_bottom)
        fill_path.closeSubpath()

        grad = QLinearGradient(0, pts[0].y(), 0, plot_bottom)
        alpha_top = 100 if opaque_area else 40
        alpha_bot = 10 if opaque_area else 3
        c_top = QColor(color)
        c_top.setAlpha(alpha_top)
        c_bot = QColor(color)
        c_bot.setAlpha(alpha_bot)
        grad.setColorAt(0.0, c_top)
        grad.setColorAt(1.0, c_bot)
        painter.setBrush(QBrush(grad))
        painter.setPen(Qt.NoPen)
        painter.drawPath(fill_path)

        # Smooth line on top
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(color, 2.0))
        painter.drawPath(curve)

    def _draw_vertical_bars(self, painter, pts, color, plot_bottom, bar_width):
        """BAR mode: vertical bars centered on each data point."""
        half = bar_width / 2.0
        for pt in pts:
            bar_h = plot_bottom - pt.y()
            rect = QRectF(pt.x() - half, pt.y(), bar_width, bar_h)
            grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            c_top = QColor(color)
            c_top.setAlpha(180)
            c_bot = QColor(color)
            c_bot.setAlpha(80)
            grad.setColorAt(0.0, c_top)
            grad.setColorAt(1.0, c_bot)
            painter.setBrush(QBrush(grad))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rect, 3, 3)

    def _draw_stepped_line(self, painter, pts, color, plot_bottom):
        """STEPPED mode: horizontal-then-vertical step function + gradient fill."""
        if len(pts) < 2:
            return
        # Build stepped path
        step_path = QPainterPath()
        step_path.moveTo(pts[0])
        for i in range(1, len(pts)):
            step_path.lineTo(pts[i].x(), pts[i - 1].y())  # horizontal
            step_path.lineTo(pts[i].x(), pts[i].y())       # vertical

        # Fill
        fill_path = QPainterPath(step_path)
        fill_path.lineTo(pts[-1].x(), plot_bottom)
        fill_path.lineTo(pts[0].x(), plot_bottom)
        fill_path.closeSubpath()
        grad = QLinearGradient(0, pts[0].y(), 0, plot_bottom)
        c_top = QColor(color)
        c_top.setAlpha(35)
        c_bot = QColor(color)
        c_bot.setAlpha(3)
        grad.setColorAt(0.0, c_top)
        grad.setColorAt(1.0, c_bot)
        painter.setBrush(QBrush(grad))
        painter.setPen(Qt.NoPen)
        painter.drawPath(fill_path)

        # Stepped line
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(color, 2.0))
        painter.drawPath(step_path)

    # ── Main paint ───────────────────────────────────────

    def paintEvent(self, event):
        if not self._data or not self._x_labels:
            return self._paint_empty(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height() - self._legend_height

        _paint_chart_bg(painter, QRectF(0, 0, w, self.height()))

        plot_left = self._margin_left
        plot_right = w - self._margin_right
        plot_top = self._margin_top
        plot_bottom = h - self._margin_bottom
        plot_w = plot_right - plot_left
        plot_h = plot_bottom - plot_top

        if plot_w < 20 or plot_h < 20:
            painter.end()
            return

        n_pts = len(self._x_labels)

        def x_pos(i):
            if n_pts <= 1:
                return plot_left + plot_w / 2
            return plot_left + (i / (n_pts - 1)) * plot_w

        def y_pos(val):
            ratio = (val - self._y_min) / (self._y_max - self._y_min)
            return plot_bottom - ratio * plot_h

        # Background tint (green above 0, red below 0) — AREA mode only
        # For LINE / BAR / STEPPED the tint is suppressed so lines stay visible.
        if (self._show_bg_tint
                and self._chart_mode == ChartMode.AREA
                and self._y_min < 0 < self._y_max):
            zero_y = y_pos(0)
            green = QColor(ALMA_SUCCESS)
            green.setAlpha(22)
            red = QColor(ALMA_ERROR)
            red.setAlpha(22)
            painter.setBrush(QBrush(green))
            painter.setPen(Qt.NoPen)
            painter.drawRect(QRectF(plot_left, plot_top, plot_w, zero_y - plot_top))
            painter.setBrush(QBrush(red))
            painter.drawRect(QRectF(plot_left, zero_y, plot_w, plot_bottom - zero_y))

        # ── Solid grid lines (Metabase-style) ──
        grid_pen = QPen(QColor(ALMA_CHART_GRID), 0.5)
        painter.setFont(QFont("Segoe UI", 9))
        n_grid = 5
        for gi in range(n_grid + 1):
            frac = gi / n_grid
            val = self._y_min + frac * (self._y_max - self._y_min)
            py = y_pos(val)

            painter.setPen(grid_pen)
            painter.drawLine(QPointF(plot_left, py), QPointF(plot_right, py))

            painter.setPen(QColor(ALMA_CHART_AXIS))
            painter.drawText(
                QRectF(0, py - 8, plot_left - 6, 16),
                Qt.AlignVCenter | Qt.AlignRight,
                self._fmt_y(val),
            )

        # Zero reference line
        if self._show_zero_line and self._y_min < 0 < self._y_max:
            zero_y = y_pos(0)
            painter.setPen(QPen(QColor(ALMA_TEXT_LIGHT), 1.5))
            painter.drawLine(QPointF(plot_left, zero_y), QPointF(plot_right, zero_y))

        # X-axis labels
        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(QColor(ALMA_CHART_AXIS))
        max_labels = max(1, int(plot_w) // 60)
        step = max(1, n_pts // max_labels)
        for i, xl in enumerate(self._x_labels):
            if i % step == 0 or i == n_pts - 1:
                px = x_pos(i)
                painter.drawText(
                    QRectF(px - 30, plot_bottom + 6, 60, 20),
                    Qt.AlignCenter, xl,
                )

        # ── Draw series (mode-branched) ──
        series_list = list(self._data.items())
        n_series = len(series_list)
        bar_width = max(4, (plot_w / max(n_pts, 1)) * 0.6 / max(n_series, 1))

        for si, (series_label, points) in enumerate(series_list):
            color = QColor(self.SERIES_COLORS[si % len(self.SERIES_COLORS)])

            # Build QPointF list
            pts = []
            for pi, (xl, yv) in enumerate(points):
                px = x_pos(pi)
                py = y_pos(yv)
                # For BAR mode with multiple series, offset x
                if self._chart_mode == ChartMode.BAR and n_series > 1:
                    offset = (si - (n_series - 1) / 2.0) * bar_width
                    px += offset
                pts.append(QPointF(px, py))

            if self._chart_mode == ChartMode.LINE:
                self._draw_smooth_line(painter, pts, color, plot_bottom, opaque_area=False)
            elif self._chart_mode == ChartMode.AREA:
                self._draw_smooth_line(painter, pts, color, plot_bottom, opaque_area=True)
            elif self._chart_mode == ChartMode.BAR:
                self._draw_vertical_bars(painter, pts, color, plot_bottom, bar_width)
            elif self._chart_mode == ChartMode.STEPPED:
                self._draw_stepped_line(painter, pts, color, plot_bottom)

        # ── Crosshair overlay on hover ──
        if 0 <= self._hover_x_idx < n_pts:
            hx = x_pos(self._hover_x_idx)
            # Vertical crosshair line
            ch_pen = QPen(QColor(ALMA_TEXT_LIGHT), 1.0, Qt.DashLine)
            painter.setPen(ch_pen)
            painter.drawLine(QPointF(hx, plot_top), QPointF(hx, plot_bottom))

            # Snap-to-point circles for each series
            for si, (series_label, points) in enumerate(series_list):
                if self._hover_x_idx >= len(points):
                    continue
                color = QColor(self.SERIES_COLORS[si % len(self.SERIES_COLORS)])
                yv = points[self._hover_x_idx][1]
                py = y_pos(yv)
                # White-filled circle with colored border
                painter.setBrush(QBrush(QColor(ALMA_BG_ELEVATED)))
                painter.setPen(QPen(color, 2.0))
                painter.drawEllipse(QPointF(hx, py), 4.5, 4.5)

        # Legend now lives in standalone ChartLegendSection (chart_builders.py)

        painter.end()

    # ── Crosshair hover interaction ──────────────────────

    def mouseMoveEvent(self, event):
        """Update crosshair position + show tooltip."""
        if not self._data or not self._x_labels:
            return

        w = self.width()
        h = self.height() - self._legend_height
        plot_left = self._margin_left
        plot_right = w - self._margin_right
        plot_w = plot_right - plot_left
        mx = event.position().x()

        if mx < plot_left or mx > plot_right or len(self._x_labels) <= 1:
            if self._hover_x_idx != -1:
                self._hover_x_idx = -1
                self.update()
            return

        ratio = (mx - plot_left) / plot_w
        idx = round(ratio * (len(self._x_labels) - 1))
        idx = max(0, min(idx, len(self._x_labels) - 1))

        if idx != self._hover_x_idx:
            self._hover_x_idx = idx
            self.update()

        # Tooltip
        lines = [self._x_labels[idx]]
        for si, (label, points) in enumerate(self._data.items()):
            if idx < len(points):
                val = points[idx][1]
                lines.append(f"{label}: {val:+.3f}")
        QToolTip.showText(event.globalPosition().toPoint(), "\n".join(lines))

    def leaveEvent(self, event):
        """Clear crosshair when mouse leaves chart."""
        if self._hover_x_idx != -1:
            self._hover_x_idx = -1
            self.update()

    def _paint_empty(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        _paint_empty_state(painter, QRectF(self.rect()), "\U0001f4c8", "No data for line chart")
        painter.end()


# ═══════════════════════════════════════════
#  CHART MODE SWITCHER (pill-toggle bar)
# ═══════════════════════════════════════════

class ChartModeSwitcher(QWidget):
    """Small horizontal pill-toggle bar for switching chart rendering mode.

    Emits ``mode_changed(ChartMode)`` when the user clicks a mode button.
    ~200px × 28px, suitable for placement above or beside a chart.
    """

    mode_changed = Signal(object)  # emits ChartMode value

    _MODE_LABELS = [
        (ChartMode.LINE, "Line"),
        (ChartMode.AREA, "Area"),
        (ChartMode.BAR, "Bar"),
        (ChartMode.STEPPED, "Step"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buttons = {}
        self._current = ChartMode.LINE

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        for mode, label in self._MODE_LABELS:
            btn = QPushButton(label, self)
            btn.setCheckable(True)
            btn.setChecked(mode == ChartMode.LINE)
            btn.setFixedHeight(26)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked, m=mode: self._on_click(m))
            self._buttons[mode] = btn
            layout.addWidget(btn)

        layout.addStretch()
        self.setFixedHeight(28)
        self._apply_styles()

    def _on_click(self, mode):
        self._current = mode
        for m, btn in self._buttons.items():
            btn.setChecked(m == mode)
        self._apply_styles()
        self.mode_changed.emit(mode)

    def _apply_styles(self):
        for mode, btn in self._buttons.items():
            if mode == self._current:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {ALMA_GREEN_DARK};
                        color: {ALMA_TEXT_ON_DARK};
                        border: none; border-radius: 4px;
                        padding: 4px 14px; font-size: 11px; font-weight: 600;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: transparent;
                        color: {ALMA_TEXT_MID};
                        border: 1px solid {ALMA_BORDER_LIGHT};
                        border-radius: 4px;
                        padding: 4px 14px; font-size: 11px; font-weight: 500;
                    }}
                    QPushButton:hover {{
                        background: {ALMA_HOVER_LIGHT};
                        color: {ALMA_TEXT_DARK};
                    }}
                """)


# ═══════════════════════════════════════════
#  SPARKLINE WIDGET
# ═══════════════════════════════════════════

class SparklineWidget(QWidget):
    """Tiny inline line chart with area fill, ~120x30px."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._values = []
        self._color = QColor(ALMA_GREEN_DARK)
        self.setFixedSize(120, 30)

    def set_data(self, values, color=None):
        """values: list of floats."""
        self._values = values
        if color:
            self._color = QColor(color)
        self.update()

    def sizeHint(self):
        return QSize(120, 30)

    def paintEvent(self, event):
        if len(self._values) < 2:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width() - 4
        h = self.height() - 4
        ox, oy = 2, 2

        vmin = min(self._values)
        vmax = max(self._values)
        if vmax == vmin:
            vmax = vmin + 1

        n = len(self._values)

        # Area fill
        fill_path = QPainterPath()
        fill_path.moveTo(ox, oy + h)
        for i, v in enumerate(self._values):
            x = ox + (i / (n - 1)) * w
            y = oy + h - ((v - vmin) / (vmax - vmin)) * h
            fill_path.lineTo(x, y)
        fill_path.lineTo(ox + w, oy + h)
        fill_path.closeSubpath()
        fill_color = QColor(self._color)
        fill_color.setAlpha(15)
        painter.setBrush(QBrush(fill_color))
        painter.setPen(Qt.NoPen)
        painter.drawPath(fill_path)

        # Line
        path = QPainterPath()
        for i, v in enumerate(self._values):
            x = ox + (i / (n - 1)) * w
            y = oy + h - ((v - vmin) / (vmax - vmin)) * h
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)

        painter.setPen(QPen(self._color, 1.5))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

        # Dot on last point
        last_x = ox + w
        last_y = oy + h - ((self._values[-1] - vmin) / (vmax - vmin)) * h
        painter.setBrush(QBrush(self._color))
        painter.setPen(QPen(QColor(ALMA_BG_ELEVATED), 1))
        painter.drawEllipse(QPointF(last_x, last_y), 2.5, 2.5)

        painter.end()


# ═══════════════════════════════════════════
#  DUAL SPARKLINE (for correlation cards)
# ═══════════════════════════════════════════

class DualSparklineWidget(QWidget):
    """Two-series sparkline with independent Y-axis scaling. 160x40px."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._values_a = []
        self._values_b = []
        self._color_a = QColor(ALMA_INFO)
        self._color_b = QColor(ALMA_WARNING)
        self._label_a = ""
        self._label_b = ""
        self.setFixedSize(160, 40)

    def set_data(self, values_a, values_b, color_a=None, color_b=None,
                 label_a="", label_b=""):
        """Set two series of values for dual sparkline."""
        self._values_a = values_a or []
        self._values_b = values_b or []
        if color_a:
            self._color_a = QColor(color_a)
        if color_b:
            self._color_b = QColor(color_b)
        self._label_a = label_a
        self._label_b = label_b
        self.update()

    def sizeHint(self):
        return QSize(160, 40)

    def paintEvent(self, event):
        if len(self._values_a) < 2 and len(self._values_b) < 2:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width() - 8
        h = self.height() - 8
        ox, oy = 4, 4

        def _draw_series(values, color):
            if len(values) < 2:
                return
            vmin = min(values)
            vmax = max(values)
            if vmax == vmin:
                vmax = vmin + 1

            n = len(values)

            # Area fill
            fill_path = QPainterPath()
            fill_path.moveTo(ox, oy + h)
            for i, v in enumerate(values):
                x = ox + (i / (n - 1)) * w
                y = oy + h - ((v - vmin) / (vmax - vmin)) * h
                fill_path.lineTo(x, y)
            fill_path.lineTo(ox + w, oy + h)
            fill_path.closeSubpath()
            fc = QColor(color)
            fc.setAlpha(12)
            painter.setBrush(QBrush(fc))
            painter.setPen(Qt.NoPen)
            painter.drawPath(fill_path)

            # Line
            path = QPainterPath()
            for i, v in enumerate(values):
                x = ox + (i / (n - 1)) * w
                y = oy + h - ((v - vmin) / (vmax - vmin)) * h
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)

            painter.setPen(QPen(QColor(color), 1.5))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)

            # Endpoint dot
            last_x = ox + w
            last_y = oy + h - ((values[-1] - vmin) / (vmax - vmin)) * h
            painter.setBrush(QBrush(QColor(color)))
            painter.setPen(QPen(QColor(ALMA_BG_ELEVATED), 1))
            painter.drawEllipse(QPointF(last_x, last_y), 2.0, 2.0)

        _draw_series(self._values_a, self._color_a)
        _draw_series(self._values_b, self._color_b)

        # Small legend dots in bottom-right
        legend_x = self.width() - 6
        if self._label_a or self._label_b:
            painter.setBrush(QBrush(self._color_b))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(legend_x, self.height() - 5), 2, 2)
            painter.setBrush(QBrush(self._color_a))
            painter.drawEllipse(QPointF(legend_x - 10, self.height() - 5), 2, 2)

        painter.end()
