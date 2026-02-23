"""
Alma Insights — Control Chart Widget
QPainter-based time-series chart with Poisson θ₁ and θ₂ control bands.

Tier 1 (daily): flat horizontal bands at Poisson percentile values.
Tier 2 (hourly): wavy bands following hour-of-day λ curve.
CUSUM indicator bar below the main chart.
"""

import math
from PySide6.QtWidgets import QWidget, QToolTip
from PySide6.QtCore import Qt, QRectF, QPointF, QSize, QRect
from PySide6.QtGui import (
    QPainter, QPen, QColor, QFont, QFontMetrics, QBrush, QPainterPath,
)

from src.ui.theme import (
    ALMA_WHITE, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_BORDER_LIGHT, ALMA_SUCCESS, ALMA_WARNING, ALMA_ERROR, ALMA_INFO,
)


INTERVENTION_COLORS = {
    "payer_launch": "#2563EB",
    "product_release": "#7C3AED",
    "process_change": "#059669",
    "policy_update": "#D97706",
    "staffing_change": "#DC2626",
    "vendor_change": "#6366F1",
    "other": "#6B7280",
}


class ControlChartWidget(QWidget):
    """
    Time-series control chart with Poisson-based θ₁/θ₂ bands.
    Adapts to Tier 1 (daily) or Tier 2 (hourly) data.
    Supports intervention marker overlays.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(320)
        self.setMouseTracking(True)
        self._trc_data = None
        self._interventions = []  # list of {"event_date": ..., "name": ..., "category": ...}
        self._legend_rects = []  # [{rect: QRect, full_name: str}, ...]
        self._point_rects = []   # [{rect: QRect, tooltip: str}, ...]

    def set_data(self, trc_result: dict):
        """Accept a trc_result dict from run_incident_scan()."""
        self._trc_data = trc_result
        self.update()

    def set_interventions(self, interventions: list):
        """Set intervention markers to overlay on the chart."""
        self._interventions = interventions or []
        self.update()

    def clear_data(self):
        self._trc_data = None
        self.update()

    def sizeHint(self):
        return QSize(600, 340)

    def paintEvent(self, event):
        self._legend_rects = []
        self._point_rects = []

        if not self._trc_data:
            return self._paint_empty(event)

        if self._trc_data.get("tier", 1) == 2 and self._trc_data.get("hourly_detail"):
            self._paint_hourly(event)
        else:
            self._paint_daily(event)

    # ═══════════════════════════════════════════
    #  TIER 1: DAILY CHART
    # ═══════════════════════════════════════════

    def _paint_daily(self, event):
        d = self._trc_data
        series = d.get("daily_series", [])
        if not series:
            return self._paint_empty(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()

        margin_left = 55
        margin_right = 16
        margin_top = 36
        margin_bottom = 72  # extra room for CUSUM bar

        plot_left = margin_left
        plot_right = w - margin_right
        plot_top = margin_top
        plot_bottom = h - margin_bottom
        plot_w = plot_right - plot_left
        plot_h = plot_bottom - plot_top

        if plot_w < 40 or plot_h < 40:
            painter.end()
            return

        # Data
        lam = d.get("lambda_daily", 0)
        t1 = d.get("theta_1_daily", 0)
        t2 = d.get("theta_2_daily", 0)
        values = [r["ticket_count"] for r in series]
        n = len(values)

        # Y range
        y_max_data = max(values) if values else 1
        y_max = max(y_max_data, t2) * 1.15 + 0.5
        y_min = 0.0
        if y_max <= y_min:
            y_max = y_min + 1

        def x_pos(i):
            if n <= 1:
                return plot_left + plot_w / 2
            return plot_left + (i / (n - 1)) * plot_w

        def y_pos(val):
            ratio = (val - y_min) / (y_max - y_min)
            return plot_bottom - ratio * plot_h

        # ── Band fills ──
        painter.setPen(Qt.NoPen)
        band_w = plot_right - plot_left

        # Incident zone: between θ₁ and θ₂
        danger_color = QColor(ALMA_ERROR)
        danger_color.setAlpha(38)
        painter.setBrush(QBrush(danger_color))
        t1_y = y_pos(t1)
        t2_y = y_pos(t2)
        if t1_y > t2_y:
            painter.drawRect(QRectF(plot_left, t2_y, band_w, t1_y - t2_y))

        # Watch zone: between λ and θ₁
        warning_color = QColor(ALMA_WARNING)
        warning_color.setAlpha(25)
        painter.setBrush(QBrush(warning_color))
        lam_y = y_pos(lam)
        if lam_y > t1_y:
            painter.drawRect(QRectF(plot_left, t1_y, band_w, lam_y - t1_y))

        # Normal zone below λ
        normal_color = QColor(ALMA_SUCCESS)
        normal_color.setAlpha(12)
        painter.setBrush(QBrush(normal_color))
        painter.drawRect(QRectF(plot_left, lam_y, band_w, plot_bottom - lam_y))

        # ── Grid lines ──
        pen = QPen(QColor(ALMA_BORDER_LIGHT), 0.5, Qt.DotLine)
        painter.setPen(pen)
        for val in [lam, t1, t2]:
            if 0 <= val <= y_max:
                y = y_pos(val)
                painter.drawLine(QPointF(plot_left, y), QPointF(plot_right, y))

        # ── λ line (dotted gray) ──
        painter.setPen(QPen(QColor(ALMA_TEXT_LIGHT), 1.5, Qt.DotLine))
        painter.drawLine(QPointF(plot_left, lam_y), QPointF(plot_right, lam_y))

        # ── θ₁ line (dashed amber) ──
        painter.setPen(QPen(QColor(ALMA_WARNING), 1.0, Qt.DashLine))
        painter.drawLine(QPointF(plot_left, t1_y), QPointF(plot_right, t1_y))

        # ── θ₂ line (dashed red) ──
        painter.setPen(QPen(QColor(ALMA_ERROR), 1.0, Qt.DashLine))
        painter.drawLine(QPointF(plot_left, t2_y), QPointF(plot_right, t2_y))

        # ── Day boundaries ──
        self._draw_day_boundaries_daily(painter, series, n, x_pos, plot_top, plot_bottom)

        # ── Data line with zone coloring ──
        self._draw_data_line(painter, values, n, x_pos, y_pos, t1, t2)

        # ── Intervention markers ──
        if self._interventions:
            date_to_idx = {s["date"]: i for i, s in enumerate(series)}
            self._draw_intervention_markers(
                painter, self._interventions, date_to_idx,
                x_pos, plot_top, plot_bottom
            )

        # ── X labels ──
        self._draw_x_labels_daily(painter, series, n, x_pos, plot_bottom)

        # ── Y labels ──
        self._draw_y_labels(painter, plot_left, y_pos, y_min, y_max)

        # ── Title ──
        self._draw_title(painter, plot_left, margin_top, d)

        # ── Legend ──
        self._draw_legend(painter, plot_right, margin_top)

        # ── CUSUM indicator bar ──
        cusum_top = plot_bottom + 20
        self._draw_cusum_bar(painter, plot_left, plot_right, cusum_top, d)

        painter.end()

    # ═══════════════════════════════════════════
    #  TIER 2: HOURLY CHART (wavy bands)
    # ═══════════════════════════════════════════

    def _paint_hourly(self, event):
        d = self._trc_data
        hourly = d.get("hourly_detail", {})
        if not hourly:
            return self._paint_daily(event)

        # For Tier 2, use the daily series as fallback for now
        # but show hourly baselines as wavy bands
        # Use daily chart with hourly baselines overlaid
        self._paint_daily(event)

    # ═══════════════════════════════════════════
    #  SHARED DRAWING HELPERS
    # ═══════════════════════════════════════════

    def _draw_data_line(self, painter, values, n, x_pos, y_pos, t1, t2):
        """Draw the data polyline with zone-based coloring."""
        if n < 2:
            if n == 1:
                color = self._point_color(values[0], t1, t2)
                painter.setBrush(QBrush(QColor(color)))
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(QPointF(x_pos(0), y_pos(values[0])), 4, 4)
            return

        for i in range(n - 1):
            v1 = values[i]
            v2 = values[i + 1]
            l1 = self._point_level(v1, t1, t2)
            l2 = self._point_level(v2, t1, t2)
            color = self._point_color(v1, t1, t2) if l1 >= l2 else self._point_color(v2, t1, t2)
            width = 2.0
            if max(l1, l2) >= 2:
                width = 3.0
            elif max(l1, l2) >= 1:
                width = 2.5

            painter.setPen(QPen(QColor(color), width))
            painter.drawLine(
                QPointF(x_pos(i), y_pos(v1)),
                QPointF(x_pos(i + 1), y_pos(v2))
            )

        # Data points
        for i in range(n):
            val = values[i]
            level = self._point_level(val, t1, t2)
            color = self._point_color(val, t1, t2)
            px = x_pos(i)
            py = y_pos(val)

            if level >= 2:
                painter.setBrush(QBrush(QColor(color)))
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(QPointF(px, py), 4, 4)
            elif level >= 1:
                painter.setBrush(QBrush(QColor(color)))
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(QPointF(px, py), 3, 3)

    def _point_color(self, val, t1, t2):
        level = self._point_level(val, t1, t2)
        if level >= 2:
            return ALMA_ERROR
        elif level >= 1:
            return ALMA_WARNING
        return ALMA_INFO

    def _point_level(self, val, t1, t2):
        if val >= t2:
            return 2
        elif val >= t1:
            return 1
        return 0

    def _draw_day_boundaries_daily(self, painter, series, n, x_pos, y_top, y_bottom):
        """Draw vertical lines at weekly boundaries for daily data."""
        if n < 2:
            return
        pen = QPen(QColor(ALMA_BORDER_LIGHT), 0.5, Qt.DotLine)
        painter.setPen(pen)
        # Draw a boundary every 7 days
        for i in range(7, n, 7):
            x = x_pos(i)
            painter.drawLine(QPointF(x, y_top), QPointF(x, y_bottom))

    def _draw_x_labels_daily(self, painter, series, n, x_pos, y_bottom):
        if n < 1:
            return
        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(QColor(ALMA_TEXT_LIGHT))

        # Show every ~5th date to avoid crowding
        step = max(1, n // 6)
        for i in range(0, n, step):
            date_str = series[i]["date"]
            x = x_pos(i)
            try:
                from datetime import datetime as dt
                d = dt.strptime(date_str, "%Y-%m-%d")
                display = d.strftime("%b %d")
            except (ValueError, TypeError):
                display = date_str[5:]
            painter.drawText(
                QRectF(x - 25, y_bottom + 6, 50, 16),
                Qt.AlignCenter, display
            )

    def _draw_y_labels(self, painter, plot_left, y_pos, y_min, y_max):
        painter.setFont(QFont("Segoe UI", 9))
        painter.setPen(QColor(ALMA_TEXT_LIGHT))

        y_range = y_max - y_min
        if y_range <= 0:
            return

        n_ticks = 5
        step = y_range / n_ticks
        mag = 10 ** math.floor(math.log10(max(step, 0.01)))
        step = math.ceil(step / mag) * mag

        val = math.floor(y_min / max(step, 0.01)) * step
        while val <= y_max:
            if val >= y_min:
                y = y_pos(val)
                painter.drawText(
                    QRectF(0, y - 8, plot_left - 6, 16),
                    Qt.AlignVCenter | Qt.AlignRight,
                    f"{val:.0f}"
                )
            val += step

    def _draw_title(self, painter, x_left, y_top, data):
        painter.setFont(QFont("Segoe UI", 11, QFont.Bold))

        trc = data.get("trc_code", "")
        tier = data.get("tier", 1)
        flag = data.get("flag_level", 0)

        tier_text = f"Tier {tier} {'Daily' if tier == 1 else 'Hourly'}"

        if flag == 2:
            status_text = "  [2-theta INCIDENT]"
            painter.setPen(QColor(ALMA_ERROR))
        elif flag == 1:
            status_text = "  [1-theta Watch]"
            painter.setPen(QColor(ALMA_WARNING))
        else:
            status_text = "  [Normal]"
            painter.setPen(QColor(ALMA_SUCCESS))

        painter.drawText(
            QRectF(x_left, 4, 500, 24),
            Qt.AlignVCenter | Qt.AlignLeft,
            f"{trc} — {tier_text}{status_text}"
        )

    def _draw_legend(self, painter, x_right, y_top):
        painter.setFont(QFont("Segoe UI", 8))
        legend_x = x_right - 200
        legend_y = 4
        spacing = 16

        items = [
            (ALMA_INFO, "Normal", "Normal: Below expected baseline"),
            (ALMA_WARNING, "1\u03b8 Watch (90th)", "Warning: Above 90th percentile threshold"),
            (ALMA_ERROR, "2\u03b8 Incident (97.5th)", "Critical: Above 97.5th percentile threshold"),
        ]

        for i, (color, label, tooltip) in enumerate(items):
            y = legend_y + i * spacing
            painter.setBrush(QBrush(QColor(color)))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(QRectF(legend_x, y + 2, 10, 10), 2, 2)
            painter.setPen(QColor(ALMA_TEXT_MID))
            painter.drawText(
                QRectF(legend_x + 14, y, 120, 14),
                Qt.AlignVCenter | Qt.AlignLeft, label
            )
            self._legend_rects.append({
                "rect": QRect(int(legend_x), int(y), 134, 14),
                "full_name": tooltip,
            })

    def _draw_cusum_bar(self, painter, x_left, x_right, y_top, data):
        """Draw CUSUM accumulator as a progress bar below the chart."""
        cusum_val = data.get("cusum_value", 0.0)
        cusum_h = data.get("cusum_threshold", 1.0)
        if cusum_h <= 0:
            cusum_h = 1.0

        bar_w = x_right - x_left
        bar_h = 18
        bar_y = y_top

        # Background
        painter.setPen(QPen(QColor(ALMA_BORDER_LIGHT), 1))
        painter.setBrush(QBrush(QColor("#F0F0F0")))
        painter.drawRoundedRect(QRectF(x_left, bar_y, bar_w, bar_h), 4, 4)

        # Fill proportional to value/threshold
        ratio = min(cusum_val / cusum_h, 1.0) if cusum_h > 0 else 0
        fill_w = bar_w * ratio

        if ratio > 0.8:
            fill_color = QColor(ALMA_ERROR)
        elif ratio > 0.5:
            fill_color = QColor(ALMA_WARNING)
        else:
            fill_color = QColor(ALMA_SUCCESS)

        if fill_w > 0:
            painter.setPen(Qt.NoPen)
            fill_color.setAlpha(180)
            painter.setBrush(QBrush(fill_color))
            painter.drawRoundedRect(QRectF(x_left, bar_y, fill_w, bar_h), 4, 4)

        # Label
        painter.setPen(QColor(ALMA_TEXT_DARK))
        painter.setFont(QFont("Segoe UI", 9, QFont.Bold))
        label = f"CUSUM: {cusum_val:.1f} / {cusum_h:.1f}"
        if data.get("cusum_alert"):
            label += "  ALERT"
        painter.drawText(
            QRectF(x_left, bar_y, bar_w, bar_h),
            Qt.AlignCenter, label
        )

    def _draw_intervention_markers(self, painter, interventions, date_to_idx,
                                     x_pos, y_top, y_bottom):
        """Draw vertical lines for intervention events on the chart."""
        for iv in interventions:
            event_date = iv.get("event_date", "")
            idx = date_to_idx.get(event_date)
            if idx is None:
                continue

            x = x_pos(idx)
            category = iv.get("category", "other")
            color = QColor(INTERVENTION_COLORS.get(category, "#6B7280"))

            # Vertical dashed line
            pen = QPen(color, 1.5, Qt.DashDotLine)
            painter.setPen(pen)
            painter.drawLine(QPointF(x, y_top), QPointF(x, y_bottom))

            # Small diamond marker at top
            color.setAlpha(200)
            painter.setBrush(QBrush(color))
            painter.setPen(Qt.NoPen)
            diamond = QPainterPath()
            diamond.moveTo(x, y_top - 2)
            diamond.lineTo(x + 5, y_top + 4)
            diamond.lineTo(x, y_top + 10)
            diamond.lineTo(x - 5, y_top + 4)
            diamond.closeSubpath()
            painter.drawPath(diamond)

            # Label (truncated name)
            label = iv.get("name", "")[:12]
            if label:
                painter.setPen(color)
                painter.setFont(QFont("Segoe UI", 7))
                painter.drawText(
                    QRectF(x - 30, y_top - 14, 60, 12),
                    Qt.AlignCenter, label
                )

    def mouseMoveEvent(self, event):
        """Show tooltip when hovering over legend items or data points."""
        pos = event.pos()
        for item in self._legend_rects:
            if item["rect"].contains(pos):
                QToolTip.showText(event.globalPosition().toPoint(), item["full_name"], self)
                return
        for item in self._point_rects:
            if item["rect"].contains(pos):
                QToolTip.showText(event.globalPosition().toPoint(), item["tooltip"], self)
                return
        QToolTip.hideText()

    def _paint_empty(self, event):
        painter = QPainter(self)
        painter.setPen(QColor(ALMA_TEXT_LIGHT))
        painter.setFont(QFont("Segoe UI", 12))
        painter.drawText(self.rect(), Qt.AlignCenter, "Select a TRC to view control chart")
        painter.end()
