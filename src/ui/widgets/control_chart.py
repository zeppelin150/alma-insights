"""
Alma Insights — Control Chart Widget
QPainter-based time-series chart with θ₁ and θ₂ control bands.
Visualizes ticket-rate envelopes (Bollinger bands for support velocity).
"""

import math
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QRectF, QPointF, QSize
from PySide6.QtGui import (
    QPainter, QPen, QColor, QFont, QFontMetrics, QBrush, QPainterPath,
)

from src.ui.theme import (
    ALMA_WHITE, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_BORDER_LIGHT, ALMA_SUCCESS, ALMA_WARNING, ALMA_ERROR, ALMA_INFO,
)


class ControlChartWidget(QWidget):
    """
    Time-series control chart with 1θ and 2θ bands.
    Used by the Incidents page to visualize ticket-rate envelopes.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(280)

        self._hourly_data = []      # [(datetime_label, count), ...]
        self._mean = 0.0
        self._std = 0.0
        self._theta_1_upper = 0.0
        self._theta_2_upper = 0.0
        self._theta_1_lower = 0.0
        self._theta_2_lower = 0.0
        self._trc_code = ""
        self._flag_level = 0
        self._flag_direction = "normal"

    def set_data(self, trc_status: dict):
        """
        Accept a single TRC status dict from run_incident_scan().
        Extracts hourly_series, baseline stats, and flag info.
        """
        self._trc_code = trc_status.get("trc_code", "")
        self._mean = trc_status.get("hourly_mean", 0.0)
        self._std = trc_status.get("hourly_std", 0.0)
        self._theta_1_upper = trc_status.get("theta_1_upper", 0.0)
        self._theta_2_upper = trc_status.get("theta_2_upper", 0.0)
        self._theta_1_lower = trc_status.get("theta_1_lower", 0.0)
        self._theta_2_lower = trc_status.get("theta_2_lower", 0.0)
        self._flag_level = trc_status.get("flag_level", 0)
        self._flag_direction = trc_status.get("flag_direction", "normal")

        # Build hourly data as (label, count) tuples
        self._hourly_data = [
            (f"{r['date']} {r['hour']:02d}:00", r["ticket_count"])
            for r in trc_status.get("hourly_series", [])
        ]
        self.update()

    def clear_data(self):
        """Reset the chart to empty state."""
        self._hourly_data = []
        self._trc_code = ""
        self.update()

    def sizeHint(self):
        return QSize(600, 300)

    def paintEvent(self, event):
        if not self._hourly_data:
            return self._paint_empty(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()

        # Margins
        margin_left = 55
        margin_right = 16
        margin_top = 36
        margin_bottom = 44

        plot_left = margin_left
        plot_right = w - margin_right
        plot_top = margin_top
        plot_bottom = h - margin_bottom
        plot_w = plot_right - plot_left
        plot_h = plot_bottom - plot_top

        if plot_w < 40 or plot_h < 40:
            painter.end()
            return

        # Determine Y range — include theta bands and data
        all_values = [v for _, v in self._hourly_data]
        y_max_data = max(all_values) if all_values else 1
        y_min_data = min(all_values) if all_values else 0
        y_max = max(y_max_data, self._theta_2_upper) * 1.1 + 0.5
        y_min = min(y_min_data, self._theta_2_lower) * 0.9
        y_min = max(0, y_min)

        if y_max <= y_min:
            y_max = y_min + 1

        n = len(self._hourly_data)

        def x_pos(i):
            if n <= 1:
                return plot_left + plot_w / 2
            return plot_left + (i / (n - 1)) * plot_w

        def y_pos(val):
            ratio = (val - y_min) / (y_max - y_min)
            return plot_bottom - ratio * plot_h

        # ── Draw theta band fills ──
        self._draw_bands(painter, plot_left, plot_right, y_pos)

        # ── Draw grid lines at theta boundaries ──
        self._draw_grid(painter, plot_left, plot_right, y_pos, y_min, y_max)

        # ── Draw vertical day boundary lines ──
        self._draw_day_boundaries(painter, n, x_pos, plot_top, plot_bottom)

        # ── Draw mean line ──
        mean_y = y_pos(self._mean)
        painter.setPen(QPen(QColor(ALMA_TEXT_LIGHT), 1.5))
        painter.drawLine(QPointF(plot_left, mean_y), QPointF(plot_right, mean_y))

        # ── Draw theta boundary lines (dashed) ──
        self._draw_theta_lines(painter, plot_left, plot_right, y_pos)

        # ── Draw data polyline with zone coloring ──
        self._draw_data_line(painter, n, x_pos, y_pos)

        # ── X-axis labels (date boundaries) ──
        self._draw_x_labels(painter, n, x_pos, plot_bottom)

        # ── Y-axis labels ──
        self._draw_y_labels(painter, plot_left, y_pos, y_min, y_max)

        # ── Title (top-left) ──
        self._draw_title(painter, plot_left, margin_top)

        # ── Legend (top-right) ──
        self._draw_legend(painter, plot_right, margin_top)

        painter.end()

    def _draw_bands(self, painter, x_left, x_right, y_pos):
        """Draw the theta band shaded regions."""
        painter.setPen(Qt.NoPen)
        band_w = x_right - x_left

        # θ₂ danger zone (between θ₁ and θ₂) — upper
        danger_color = QColor(ALMA_ERROR)
        danger_color.setAlpha(38)  # ~15%
        painter.setBrush(QBrush(danger_color))
        t1u_y = y_pos(self._theta_1_upper)
        t2u_y = y_pos(self._theta_2_upper)
        if t1u_y > t2u_y:
            painter.drawRect(QRectF(x_left, t2u_y, band_w, t1u_y - t2u_y))

        # θ₂ danger zone — lower
        t1l_y = y_pos(self._theta_1_lower)
        t2l_y = y_pos(self._theta_2_lower)
        if t2l_y > t1l_y:
            painter.drawRect(QRectF(x_left, t1l_y, band_w, t2l_y - t1l_y))

        # θ₁ warning zone (between mean and θ₁) — upper
        warning_color = QColor(ALMA_WARNING)
        warning_color.setAlpha(25)  # ~10%
        painter.setBrush(QBrush(warning_color))
        mean_y = y_pos(self._mean)
        if mean_y > t1u_y:
            painter.drawRect(QRectF(x_left, t1u_y, band_w, mean_y - t1u_y))

        # θ₁ warning zone — lower
        if t1l_y > mean_y:
            painter.drawRect(QRectF(x_left, mean_y, band_w, t1l_y - mean_y))

        # Normal zone (between θ₁ lower and θ₁ upper) — subtle green
        normal_color = QColor(ALMA_SUCCESS)
        normal_color.setAlpha(12)  # ~5%
        painter.setBrush(QBrush(normal_color))
        # This is already covered by the warning zones above,
        # the normal zone is the area between mean ± nothing visible
        # Actually the spec says between θ₁ lower and θ₁ upper as "normal" zone
        # but the warning fills already cover mean→θ₁. So the green fill is
        # conceptually redundant but let's keep it as a very subtle background
        # over the whole mean±θ₁ range
        painter.drawRect(QRectF(x_left, t1u_y, band_w, t1l_y - t1u_y))

    def _draw_grid(self, painter, x_left, x_right, y_pos, y_min, y_max):
        """Draw horizontal grid lines."""
        pen = QPen(QColor(ALMA_BORDER_LIGHT), 0.5, Qt.DotLine)
        painter.setPen(pen)

        # Grid at theta boundaries
        for val in [self._theta_1_upper, self._theta_2_upper,
                     self._theta_1_lower, self._theta_2_lower, self._mean]:
            if y_min <= val <= y_max:
                y = y_pos(val)
                painter.drawLine(QPointF(x_left, y), QPointF(x_right, y))

    def _draw_day_boundaries(self, painter, n, x_pos, y_top, y_bottom):
        """Draw vertical lines at day boundaries."""
        if n < 2:
            return
        pen = QPen(QColor(ALMA_BORDER_LIGHT), 0.5, Qt.DotLine)
        painter.setPen(pen)

        prev_date = ""
        for i, (label, _) in enumerate(self._hourly_data):
            date_part = label[:10]  # YYYY-MM-DD
            if date_part != prev_date and prev_date:
                x = x_pos(i)
                painter.drawLine(QPointF(x, y_top), QPointF(x, y_bottom))
            prev_date = date_part

    def _draw_theta_lines(self, painter, x_left, x_right, y_pos):
        """Draw dashed θ₁ and θ₂ boundary lines."""
        # θ₁ — dashed warning
        pen = QPen(QColor(ALMA_WARNING), 1.0, Qt.DashLine)
        painter.setPen(pen)
        for val in [self._theta_1_upper, self._theta_1_lower]:
            y = y_pos(val)
            painter.drawLine(QPointF(x_left, y), QPointF(x_right, y))

        # θ₂ — dashed danger
        pen = QPen(QColor(ALMA_ERROR), 1.0, Qt.DashLine)
        painter.setPen(pen)
        for val in [self._theta_2_upper, self._theta_2_lower]:
            y = y_pos(val)
            painter.drawLine(QPointF(x_left, y), QPointF(x_right, y))

    def _draw_data_line(self, painter, n, x_pos, y_pos):
        """Draw the actual data polyline with zone-based coloring."""
        if n < 2:
            if n == 1:
                label, val = self._hourly_data[0]
                color = self._point_color(val)
                painter.setBrush(QBrush(QColor(color)))
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(QPointF(x_pos(0), y_pos(val)), 4, 4)
            return

        # Draw line segments colored by zone
        for i in range(n - 1):
            _, v1 = self._hourly_data[i]
            _, v2 = self._hourly_data[i + 1]

            # Use the "worse" color of the two endpoints
            c1 = self._point_color(v1)
            c2 = self._point_color(v2)
            level1 = self._point_level(v1)
            level2 = self._point_level(v2)
            color = c1 if level1 >= level2 else c2
            width = 2.0
            if max(level1, level2) >= 2:
                width = 3.0
            elif max(level1, level2) >= 1:
                width = 2.5

            painter.setPen(QPen(QColor(color), width))
            painter.drawLine(
                QPointF(x_pos(i), y_pos(v1)),
                QPointF(x_pos(i + 1), y_pos(v2))
            )

        # Draw data points
        for i, (label, val) in enumerate(self._hourly_data):
            color = self._point_color(val)
            level = self._point_level(val)
            px = x_pos(i)
            py = y_pos(val)

            if level >= 2:
                # θ₂ breach: filled circle
                painter.setBrush(QBrush(QColor(color)))
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(QPointF(px, py), 4, 4)
            elif level >= 1:
                # θ₁ breach: small filled circle
                painter.setBrush(QBrush(QColor(color)))
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(QPointF(px, py), 3, 3)
            # Normal points: no marker (line only) for cleaner look

    def _point_color(self, val):
        """Get color for a data point based on its zone."""
        level = self._point_level(val)
        if level >= 2:
            return ALMA_ERROR
        elif level >= 1:
            return ALMA_WARNING
        return ALMA_INFO

    def _point_level(self, val):
        """Get flag level (0, 1, 2) for a value."""
        if self._std < 0.01:
            return 0
        z = abs(val - self._mean) / self._std if self._std > 0 else 0
        if z >= 2.0:
            return 2
        elif z >= 1.0:
            return 1
        return 0

    def _draw_x_labels(self, painter, n, x_pos, y_bottom):
        """Draw date labels on the X axis."""
        if n < 1:
            return

        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(QColor(ALMA_TEXT_LIGHT))

        # Show date boundaries
        seen_dates = set()
        for i, (label, _) in enumerate(self._hourly_data):
            date_part = label[:10]
            if date_part not in seen_dates:
                seen_dates.add(date_part)
                x = x_pos(i)
                # Format: "Feb 10"
                try:
                    from datetime import datetime as dt
                    d = dt.strptime(date_part, "%Y-%m-%d")
                    display = d.strftime("%b %d")
                except (ValueError, TypeError):
                    display = date_part[5:]

                painter.drawText(
                    QRectF(x - 25, y_bottom + 6, 50, 16),
                    Qt.AlignCenter, display
                )

    def _draw_y_labels(self, painter, x_left, y_pos, y_min, y_max):
        """Draw Y axis tick labels."""
        painter.setFont(QFont("Segoe UI", 9))
        painter.setPen(QColor(ALMA_TEXT_LIGHT))

        # Compute nice tick values
        y_range = y_max - y_min
        if y_range <= 0:
            return

        n_ticks = 5
        step = y_range / n_ticks
        # Round step to nice number
        mag = 10 ** math.floor(math.log10(max(step, 0.01)))
        step = math.ceil(step / mag) * mag

        val = math.floor(y_min / step) * step
        while val <= y_max:
            if val >= y_min:
                y = y_pos(val)
                painter.drawText(
                    QRectF(0, y - 8, x_left - 6, 16),
                    Qt.AlignVCenter | Qt.AlignRight,
                    f"{val:.0f}"
                )
            val += step

    def _draw_title(self, painter, x_left, y_top):
        """Draw TRC code and status in top-left."""
        painter.setFont(QFont("Segoe UI", 11, QFont.Bold))
        painter.setPen(QColor(ALMA_TEXT_DARK))

        status_text = ""
        if self._flag_level == 2:
            status_text = "  [2-theta INCIDENT]"
            painter.setPen(QColor(ALMA_ERROR))
        elif self._flag_level == 1:
            status_text = "  [1-theta Watch]"
            painter.setPen(QColor(ALMA_WARNING))
        else:
            status_text = "  [Normal]"
            painter.setPen(QColor(ALMA_SUCCESS))

        painter.drawText(
            QRectF(x_left, 4, 400, 24),
            Qt.AlignVCenter | Qt.AlignLeft,
            f"{self._trc_code}{status_text}"
        )

    def _draw_legend(self, painter, x_right, y_top):
        """Draw legend in top-right corner."""
        painter.setFont(QFont("Segoe UI", 8))
        legend_x = x_right - 180
        legend_y = 4
        spacing = 16

        items = [
            (ALMA_INFO, "Normal"),
            (ALMA_WARNING, "1-theta Watch"),
            (ALMA_ERROR, "2-theta Incident"),
        ]

        for i, (color, label) in enumerate(items):
            y = legend_y + i * spacing
            # Color swatch
            painter.setBrush(QBrush(QColor(color)))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(QRectF(legend_x, y + 2, 10, 10), 2, 2)
            # Label
            painter.setPen(QColor(ALMA_TEXT_MID))
            painter.drawText(
                QRectF(legend_x + 14, y, 100, 14),
                Qt.AlignVCenter | Qt.AlignLeft, label
            )

    def _paint_empty(self, event):
        """Draw empty state."""
        painter = QPainter(self)
        painter.setPen(QColor(ALMA_TEXT_LIGHT))
        painter.setFont(QFont("Segoe UI", 12))
        painter.drawText(self.rect(), Qt.AlignCenter, "Select a TRC to view control chart")
        painter.end()
