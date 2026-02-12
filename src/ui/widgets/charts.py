"""
Alma Insights — Custom Chart Widgets
QPainter-based charts: BarChart, BoxPlot, Heatmap, LineChart, Sparkline.
No external charting libraries required.
"""

import math
from PySide6.QtWidgets import QWidget, QToolTip
from PySide6.QtCore import Qt, QRectF, QPointF, Signal, QSize
from PySide6.QtGui import (
    QPainter, QPen, QColor, QFont, QFontMetrics, QBrush, QPainterPath,
    QLinearGradient,
)

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_MID, ALMA_GREEN_LIGHT, ALMA_GREEN_SUBTLE,
    ALMA_CREAM, ALMA_WHITE, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_BORDER, ALMA_BORDER_LIGHT, ALMA_SUCCESS, ALMA_WARNING, ALMA_ERROR,
    ALMA_INFO,
)


# ═══════════════════════════════════════════
#  HORIZONTAL BAR CHART
# ═══════════════════════════════════════════

class BarChartWidget(QWidget):
    """Horizontal bar chart. Accepts list of (label, value) tuples."""

    bar_clicked = Signal(str)  # emits label

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = []  # [(label, value), ...]
        self._max_items = 20
        self._overflow_count = 0
        self._bar_color = QColor(ALMA_GREEN_LIGHT)
        self._bar_height = 24
        self._bar_spacing = 6
        self._label_width = 140
        self._hovered_index = -1
        self.setMouseTracking(True)

    def set_data(self, data, max_items=20):
        """Set chart data. Data should be sorted descending by value."""
        self._max_items = max_items
        if len(data) > max_items:
            self._overflow_count = len(data) - max_items
            self._data = data[:max_items]
        else:
            self._overflow_count = 0
            self._data = data
        self.setMinimumHeight(self._calc_height())
        self.update()

    def _calc_height(self):
        n = len(self._data)
        h = 30 + n * (self._bar_height + self._bar_spacing) + 10
        if self._overflow_count:
            h += 24
        return max(h, 60)

    def sizeHint(self):
        return QSize(400, self._calc_height())

    def paintEvent(self, event):
        if not self._data:
            return self._paint_empty(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w = self.width()

        max_val = max(v for _, v in self._data) if self._data else 1
        if max_val == 0:
            max_val = 1

        # Title area
        y = 10
        label_font = QFont("Segoe UI", 11)
        value_font = QFont("Segoe UI", 10, QFont.Bold)
        fm = QFontMetrics(label_font)

        bar_area_left = self._label_width + 12
        bar_area_width = w - bar_area_left - 60  # leave room for value label

        for i, (label, value) in enumerate(self._data):
            bar_y = y + i * (self._bar_height + self._bar_spacing)

            # Label
            painter.setFont(label_font)
            painter.setPen(QColor(ALMA_TEXT_MID))
            elided = fm.elidedText(label, Qt.ElideRight, self._label_width)
            painter.drawText(
                QRectF(4, bar_y, self._label_width, self._bar_height),
                Qt.AlignVCenter | Qt.AlignRight, elided
            )

            # Bar
            bar_w = max(2, (value / max_val) * bar_area_width)
            bar_rect = QRectF(bar_area_left, bar_y + 2, bar_w, self._bar_height - 4)

            color = self._bar_color if i != self._hovered_index else QColor(ALMA_GREEN_SUBTLE)
            painter.setBrush(QBrush(color))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(bar_rect, 4, 4)

            # Value label
            painter.setFont(value_font)
            painter.setPen(QColor(ALMA_TEXT_DARK))
            painter.drawText(
                QRectF(bar_area_left + bar_w + 6, bar_y, 50, self._bar_height),
                Qt.AlignVCenter | Qt.AlignLeft,
                f"{value:,.0f}" if isinstance(value, float) else f"{value:,}"
            )

        # Overflow label
        if self._overflow_count:
            overflow_y = y + len(self._data) * (self._bar_height + self._bar_spacing) + 4
            painter.setFont(QFont("Segoe UI", 10))
            painter.setPen(QColor(ALMA_TEXT_LIGHT))
            painter.drawText(
                QRectF(0, overflow_y, w, 20),
                Qt.AlignCenter,
                f"and {self._overflow_count} more..."
            )

        painter.end()

    def _paint_empty(self, event):
        painter = QPainter(self)
        painter.setPen(QColor(ALMA_TEXT_LIGHT))
        painter.setFont(QFont("Segoe UI", 12))
        painter.drawText(self.rect(), Qt.AlignCenter, "No data")
        painter.end()

    def mouseMoveEvent(self, event):
        y = 10
        idx = -1
        for i in range(len(self._data)):
            bar_y = y + i * (self._bar_height + self._bar_spacing)
            if bar_y <= event.position().y() <= bar_y + self._bar_height:
                idx = i
                break
        if idx != self._hovered_index:
            self._hovered_index = idx
            self.update()

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
    """Horizontal box plot chart. Accepts {label: [values]} dict."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = {}  # {label: [values]}
        self._sorted_labels = []
        self._box_height = 28
        self._box_spacing = 8
        self._label_width = 140

    def set_data(self, data, max_items=10, min_samples=5):
        """Set data. Filters to labels with >= min_samples values."""
        filtered = {k: v for k, v in data.items() if len(v) >= min_samples}
        # Sort by median descending
        import numpy as np
        self._sorted_labels = sorted(
            filtered.keys(),
            key=lambda k: np.median(filtered[k]),
            reverse=True
        )[:max_items]
        self._data = {k: filtered[k] for k in self._sorted_labels}
        self.setMinimumHeight(self._calc_height())
        self.update()

    def _calc_height(self):
        n = len(self._sorted_labels)
        return max(60, 20 + n * (self._box_height + self._box_spacing) + 30)

    def sizeHint(self):
        return QSize(400, self._calc_height())

    def paintEvent(self, event):
        if not self._data:
            return self._paint_empty(event)

        import numpy as np
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w = self.width()

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
        y_start = 10

        label_font = QFont("Segoe UI", 10)
        small_font = QFont("Segoe UI", 9)
        fm = QFontMetrics(label_font)

        def x_pos(val):
            return plot_left + ((val - global_min) / (global_max - global_min)) * plot_width

        for i, label in enumerate(self._sorted_labels):
            vals = np.array(self._data[label])
            q1, median, q3 = np.percentile(vals, [25, 50, 75])
            iqr = q3 - q1
            whisker_low = max(vals.min(), q1 - 1.5 * iqr)
            whisker_high = min(vals.max(), q3 + 1.5 * iqr)

            cy = y_start + i * (self._box_height + self._box_spacing) + self._box_height / 2

            # Label
            painter.setFont(label_font)
            painter.setPen(QColor(ALMA_TEXT_MID))
            elided = fm.elidedText(label, Qt.ElideRight, self._label_width)
            painter.drawText(
                QRectF(4, cy - self._box_height / 2, self._label_width, self._box_height),
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

            # Box (Q1 to Q3)
            box_x = x_pos(q1)
            box_w = max(2, x_pos(q3) - box_x)
            box_rect = QRectF(box_x, cy - self._box_height * 0.35,
                              box_w, self._box_height * 0.7)
            painter.setBrush(QBrush(QColor(ALMA_INFO).lighter(140)))
            painter.setPen(QPen(QColor(ALMA_INFO), 1))
            painter.drawRoundedRect(box_rect, 3, 3)

            # Median line
            median_x = x_pos(median)
            painter.setPen(QPen(QColor(ALMA_GREEN_DARK), 2))
            painter.drawLine(
                QPointF(median_x, cy - self._box_height * 0.35),
                QPointF(median_x, cy + self._box_height * 0.35)
            )

        # X-axis labels
        painter.setFont(small_font)
        painter.setPen(QColor(ALMA_TEXT_LIGHT))
        axis_y = y_start + len(self._sorted_labels) * (self._box_height + self._box_spacing) + 4
        for frac in [0, 0.25, 0.5, 0.75, 1.0]:
            val = global_min + frac * (global_max - global_min)
            px = x_pos(val)
            painter.drawText(QRectF(px - 25, axis_y, 50, 16), Qt.AlignCenter, f"{val:.0f}h")

        painter.end()

    def _paint_empty(self, event):
        painter = QPainter(self)
        painter.setPen(QColor(ALMA_TEXT_LIGHT))
        painter.setFont(QFont("Segoe UI", 12))
        painter.drawText(self.rect(), Qt.AlignCenter, "Insufficient data for box plots")
        painter.end()


# ═══════════════════════════════════════════
#  HEATMAP WIDGET
# ═══════════════════════════════════════════

class HeatmapWidget(QWidget):
    """Grid heatmap with color gradient. Axes: Y=labels, X=time periods."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._y_labels = []
        self._x_labels = []
        self._values = {}  # {(y_idx, x_idx): float_value}
        self._cell_width = 64
        self._cell_height = 32
        self._label_width = 140
        self._header_height = 40

    def set_data(self, y_labels, x_labels, values):
        """
        values: dict of {(y_idx, x_idx): avg_score} or None for missing.
        """
        self._y_labels = y_labels
        self._x_labels = x_labels
        self._values = values
        self.setMinimumHeight(self._calc_height())
        self.setMinimumWidth(self._calc_width())
        self.update()

    def _calc_height(self):
        return max(60, self._header_height + len(self._y_labels) * self._cell_height + 10)

    def _calc_width(self):
        return max(300, self._label_width + len(self._x_labels) * self._cell_width + 20)

    def sizeHint(self):
        return QSize(self._calc_width(), self._calc_height())

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
            return self._paint_empty(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        label_font = QFont("Segoe UI", 10)
        header_font = QFont("Segoe UI", 9, QFont.Bold)
        value_font = QFont("Segoe UI", 9)
        fm = QFontMetrics(label_font)

        # Column headers
        painter.setFont(header_font)
        painter.setPen(QColor(ALMA_TEXT_MID))
        for xi, xlabel in enumerate(self._x_labels):
            x = self._label_width + xi * self._cell_width
            painter.drawText(
                QRectF(x, 4, self._cell_width, self._header_height - 8),
                Qt.AlignCenter, xlabel
            )

        # Rows
        for yi, ylabel in enumerate(self._y_labels):
            y = self._header_height + yi * self._cell_height

            # Row label
            painter.setFont(label_font)
            painter.setPen(QColor(ALMA_TEXT_MID))
            elided = fm.elidedText(ylabel, Qt.ElideRight, self._label_width - 8)
            painter.drawText(
                QRectF(4, y, self._label_width - 4, self._cell_height),
                Qt.AlignVCenter | Qt.AlignRight, elided
            )

            # Cells
            for xi in range(len(self._x_labels)):
                x = self._label_width + xi * self._cell_width
                val = self._values.get((yi, xi))
                color = self._color_for_score(val)

                cell_rect = QRectF(x + 1, y + 1, self._cell_width - 2, self._cell_height - 2)
                painter.setBrush(QBrush(color))
                painter.setPen(Qt.NoPen)
                painter.drawRoundedRect(cell_rect, 4, 4)

                # Value text
                if val is not None:
                    painter.setFont(value_font)
                    text_color = QColor(ALMA_TEXT_DARK)
                    painter.setPen(text_color)
                    painter.drawText(cell_rect, Qt.AlignCenter, f"{val:.1f}")

        painter.end()

    def _paint_empty(self, event):
        painter = QPainter(self)
        painter.setPen(QColor(ALMA_TEXT_LIGHT))
        painter.setFont(QFont("Segoe UI", 12))
        painter.drawText(self.rect(), Qt.AlignCenter, "No CSAT data for heatmap")
        painter.end()


# ═══════════════════════════════════════════
#  LINE CHART WIDGET
# ═══════════════════════════════════════════

class LineChartWidget(QWidget):
    """
    Multi-series line chart.
    Accepts {series_label: [(x_label, y_value), ...]} dict.
    """

    SERIES_COLORS = [
        ALMA_GREEN_DARK, ALMA_INFO, ALMA_WARNING, ALMA_ERROR, ALMA_GREEN_SUBTLE,
        "#7B61FF", "#E06666", "#6AA84F", "#674EA7", "#CC4125",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = {}
        self._x_labels = []
        self._y_min = -1.0
        self._y_max = 1.0
        self._show_zero_line = True
        self._show_bg_tint = True
        self._margin_left = 50
        self._margin_right = 20
        self._margin_top = 20
        self._margin_bottom = 50
        self._legend_height = 30
        self.setMinimumHeight(200)

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

    def paintEvent(self, event):
        if not self._data or not self._x_labels:
            return self._paint_empty(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height() - self._legend_height

        plot_left = self._margin_left
        plot_right = w - self._margin_right
        plot_top = self._margin_top
        plot_bottom = h - self._margin_bottom
        plot_w = plot_right - plot_left
        plot_h = plot_bottom - plot_top

        if plot_w < 20 or plot_h < 20:
            painter.end()
            return

        def x_pos(i):
            if len(self._x_labels) <= 1:
                return plot_left + plot_w / 2
            return plot_left + (i / (len(self._x_labels) - 1)) * plot_w

        def y_pos(val):
            ratio = (val - self._y_min) / (self._y_max - self._y_min)
            return plot_bottom - ratio * plot_h

        # Background tint (green above 0, red below 0)
        if self._show_bg_tint and self._y_min < 0 < self._y_max:
            zero_y = y_pos(0)
            # Green above
            painter.setBrush(QBrush(QColor(ALMA_SUCCESS).lighter(190)))
            painter.setPen(Qt.NoPen)
            painter.drawRect(QRectF(plot_left, plot_top, plot_w, zero_y - plot_top))
            # Red below
            painter.setBrush(QBrush(QColor(ALMA_ERROR).lighter(190)))
            painter.drawRect(QRectF(plot_left, zero_y, plot_w, plot_bottom - zero_y))

        # Grid lines and Y-axis labels
        painter.setFont(QFont("Segoe UI", 9))
        n_grid = 5
        for gi in range(n_grid + 1):
            frac = gi / n_grid
            val = self._y_min + frac * (self._y_max - self._y_min)
            py = y_pos(val)

            # Grid line
            painter.setPen(QPen(QColor(ALMA_BORDER_LIGHT), 1, Qt.DashLine))
            painter.drawLine(QPointF(plot_left, py), QPointF(plot_right, py))

            # Label
            painter.setPen(QColor(ALMA_TEXT_LIGHT))
            painter.drawText(
                QRectF(0, py - 8, plot_left - 6, 16),
                Qt.AlignVCenter | Qt.AlignRight,
                f"{val:.1f}"
            )

        # Zero reference line
        if self._show_zero_line and self._y_min < 0 < self._y_max:
            zero_y = y_pos(0)
            painter.setPen(QPen(QColor(ALMA_TEXT_LIGHT), 1.5))
            painter.drawLine(QPointF(plot_left, zero_y), QPointF(plot_right, zero_y))

        # X-axis labels
        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(QColor(ALMA_TEXT_LIGHT))
        max_labels = max(1, plot_w // 60)
        step = max(1, len(self._x_labels) // max_labels)
        for i, xl in enumerate(self._x_labels):
            if i % step == 0 or i == len(self._x_labels) - 1:
                px = x_pos(i)
                painter.drawText(
                    QRectF(px - 30, plot_bottom + 6, 60, 20),
                    Qt.AlignCenter, xl
                )

        # Draw series
        series_list = list(self._data.items())
        for si, (series_label, points) in enumerate(series_list):
            color = QColor(self.SERIES_COLORS[si % len(self.SERIES_COLORS)])
            pen = QPen(color, 2)
            painter.setPen(pen)

            path = QPainterPath()
            for pi, (xl, yv) in enumerate(points):
                px = x_pos(pi)
                py = y_pos(yv)
                if pi == 0:
                    path.moveTo(px, py)
                else:
                    path.lineTo(px, py)

            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)

            # Data points
            painter.setBrush(QBrush(color))
            for pi, (xl, yv) in enumerate(points):
                px = x_pos(pi)
                py = y_pos(yv)
                painter.drawEllipse(QPointF(px, py), 3, 3)

        # Legend (below chart)
        legend_y = h
        legend_x = plot_left
        painter.setFont(QFont("Segoe UI", 9))
        for si, (series_label, _) in enumerate(series_list):
            color = QColor(self.SERIES_COLORS[si % len(self.SERIES_COLORS)])
            # Color swatch
            painter.setBrush(QBrush(color))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(QRectF(legend_x, legend_y + 4, 12, 12), 2, 2)
            # Label
            painter.setPen(QColor(ALMA_TEXT_MID))
            text_rect = QRectF(legend_x + 16, legend_y, 100, 20)
            painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, series_label)
            legend_x += 120

        painter.end()

    def _paint_empty(self, event):
        painter = QPainter(self)
        painter.setPen(QColor(ALMA_TEXT_LIGHT))
        painter.setFont(QFont("Segoe UI", 12))
        painter.drawText(self.rect(), Qt.AlignCenter, "No data for line chart")
        painter.end()


# ═══════════════════════════════════════════
#  SPARKLINE WIDGET
# ═══════════════════════════════════════════

class SparklineWidget(QWidget):
    """Tiny inline line chart, ~120x30px."""

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

        path = QPainterPath()
        n = len(self._values)
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

            path = QPainterPath()
            n = len(values)
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
            painter.drawEllipse(QPointF(last_x, last_y), 2.0, 2.0)

        _draw_series(self._values_a, self._color_a)
        _draw_series(self._values_b, self._color_b)

        # Small legend dots in bottom-right
        legend_x = self.width() - 6
        if self._label_a or self._label_b:
            font = QFont("Segoe UI", 6)
            painter.setFont(font)
            # Series B dot
            painter.setBrush(QBrush(self._color_b))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(legend_x, self.height() - 5), 2, 2)
            # Series A dot
            painter.setBrush(QBrush(self._color_a))
            painter.drawEllipse(QPointF(legend_x - 10, self.height() - 5), 2, 2)

        painter.end()
