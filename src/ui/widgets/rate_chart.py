"""
Alma Insights — Rate-per-hour Control Chart Widget

QPainter-based time-series chart that renders a foreground rate polyline
(tickets per hour) against a per-bucket trailing baseline band
(``baseline_mean ± 2σ``). Spikes are highlighted with red markers.

Used by the Source Monitor "Live Feed" tab to replace the old ticket-card
scroll. Consumes a ``RateBaseline`` dataclass produced by
``src.data.source_baseline.compute_rate_baseline``.

Why a new widget (not an extension of ControlChartWidget)
─────────────────────────────────────────────────────────
``ControlChartWidget`` consumes a ``theta_engine``-shaped dict with
**scalar** Poisson bands (``lambda_daily``, ``theta_1_daily``,
``theta_2_daily``). Our trailing baseline produces **per-bucket arrays**
(mean and stddev computed per hour-of-day). These are fundamentally
different rendering models — coupling them would force every painter
helper to branch on which mode is active.

So this is a separate, focused widget: ~400 LOC, one painter path,
one data model.

Coordinate-system note
──────────────────────
Qt's Y axis points down (0 at top, height-1 at bottom). All coordinate
helpers in ``paintEvent`` invert the chart-space Y so larger values
appear higher on screen.

Author: 2026-05-07 source-monitor redesign
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QToolTip, QWidget

from src.data.source_baseline import RateBaseline
from src.ui.theme import (
    ALMA_BORDER_LIGHT,
    ALMA_CHART_AXIS,
    ALMA_CHART_BG,
    ALMA_CHART_GRID,
    ALMA_ERROR,
    ALMA_GREEN_DARK,
    ALMA_INFO,
    ALMA_TEXT_DARK,
    ALMA_TEXT_LIGHT,
    ALMA_TEXT_MID,
    ALMA_WARNING,
)

logger = logging.getLogger("alma.rate_chart")


# ─── Internal layout constants ─────────────────────────────────────

#: Pixel margin on each side of the plot area. Generous on the left to
#: fit Y-axis labels (max ~3 digits + small padding).
_MARGIN_LEFT = 50
_MARGIN_RIGHT = 16
_MARGIN_TOP = 36
_MARGIN_BOTTOM = 56

#: Cold-start banner height (painted along the top edge).
_COLD_BANNER_HEIGHT = 22

#: Tooltip hit-test radius around each plotted point, in pixels.
_HIT_RADIUS = 8

#: Tick spacing for X labels (every Nth bucket gets a label).
#: 24-bucket window → label every 4 hours; 48-bucket → every 6.
_X_TICK_EVERY_24H = 4
_X_TICK_EVERY_48H = 6


@dataclass
class _PointRect:
    """One hit-test region for tooltip dispatch.

    Attributes:
        rect: Pixel-space rectangle the cursor must be inside.
        bucket_label: Short-format hour label, e.g. "Wed 14:00".
        tooltip: Multiline body text shown on hover.
    """

    rect: QRect
    bucket_label: str
    tooltip: str


class RateChartWidget(QWidget):
    """Renders a ``RateBaseline`` as a control chart.

    Public API
    ──────────
    - ``set_baseline(baseline)`` — push a new baseline; the widget
      repaints on the next paint cycle.
    - ``clear()`` — drop the current baseline; widget shows "awaiting
      data" empty state.

    Visual elements (in z-order, back to front)
    ───────────────────────────────────────────
    1. Plot background (sage cream from ``ALMA_CHART_BG``)
    2. Filled trailing-baseline band (``ALMA_INFO`` α=46)
    3. Baseline mean polyline (dotted ``ALMA_TEXT_LIGHT``)
    4. Rate polyline (solid ``ALMA_GREEN_DARK``)
    5. Spike markers (red filled circles at ``is_spike`` indices)
    6. X / Y axis labels
    7. Title + computed-at timestamp
    8. Cold-start banner (only when ``baseline.cold_start`` is True)

    Tooltips fire on hover over any plotted point (within
    ``_HIT_RADIUS`` pixels) and show: bucket time, rate, baseline
    mean ± σ, spike flag.

    Threading
    ─────────
    All Qt widget methods must run on the main thread. ``set_baseline``
    is safe to call from a slot; the painter runs on the next paint
    event.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._baseline: RateBaseline | None = None
        self._point_rects: list[_PointRect] = []

        # Mouse tracking enables hover tooltips without requiring a click.
        self.setMouseTracking(True)
        self.setMinimumHeight(280)

    # ── Public API ────────────────────────────────────────────────

    def set_baseline(self, baseline: RateBaseline) -> None:
        """Push a new baseline; widget will repaint on the next event tick.

        Args:
            baseline: A ``RateBaseline`` instance. May have empty arrays
                (the widget will render an "awaiting data" state).
        """
        self._baseline = baseline
        self.update()  # schedule repaint

    def clear(self) -> None:
        """Drop the current baseline; widget shows the empty state."""
        self._baseline = None
        self._point_rects = []
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 — Qt naming convention
        """Preferred size when laid out in a parent."""
        return QSize(720, 320)

    # ── Painting ──────────────────────────────────────────────────

    def paintEvent(self, event) -> None:  # noqa: N802 — Qt naming convention
        """Top-level paint dispatch.

        Resets the point-rect cache (rebuilt on every paint) and
        delegates to either ``_paint_empty`` or ``_paint_chart``.
        """
        # Reset hover hit-test regions; they're rebuilt each paint.
        self._point_rects = []

        bl = self._baseline
        if bl is None or bl.is_empty:
            self._paint_empty()
            return

        self._paint_chart(bl)

    def _paint_empty(self) -> None:
        """Render the empty / awaiting-data placeholder."""
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)

            # Plot background
            painter.fillRect(self.rect(), QColor(ALMA_CHART_BG))

            # Centered "Awaiting data" label
            painter.setPen(QColor(ALMA_TEXT_LIGHT))
            painter.setFont(QFont("Segoe UI", 11))
            painter.drawText(
                self.rect(),
                Qt.AlignCenter,
                "Awaiting ticket data — connect a source on the Connection tab",
            )
        finally:
            painter.end()

    def _paint_chart(self, bl: RateBaseline) -> None:
        """Full chart paint path. Called when baseline has data."""
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)

            # Resolve plot rectangle in pixel space.
            w = self.width()
            h = self.height()
            plot = QRectF(
                _MARGIN_LEFT,
                _MARGIN_TOP,
                max(0, w - _MARGIN_LEFT - _MARGIN_RIGHT),
                max(0, h - _MARGIN_TOP - _MARGIN_BOTTOM),
            )
            if plot.width() < 60 or plot.height() < 60:
                # Window too small to render anything meaningful.
                return

            # Whole-widget background (chart sits on a sage cream pad).
            painter.fillRect(self.rect(), QColor(ALMA_CHART_BG))

            # Y-axis range: max of (rates, upper_band) plus 15% headroom.
            y_max = self._compute_y_max(bl)
            y_min = 0.0

            # Pre-compute pixel positions for every bucket. Reused for
            # band fill, mean line, rate polyline, hit tests.
            n = len(bl.rates)
            x_at = self._x_axis_fn(plot, n)
            y_at = self._y_axis_fn(plot, y_min, y_max)

            # Order matters — back-to-front painting.
            self._draw_band(painter, bl, x_at, y_at)
            self._draw_grid(painter, plot, y_at, y_min, y_max)
            self._draw_baseline_mean_line(painter, bl, x_at, y_at)
            self._draw_rate_polyline(painter, bl, x_at, y_at)
            self._draw_spike_markers(painter, bl, x_at, y_at)
            self._draw_x_labels(painter, bl, plot, x_at)
            self._draw_y_labels(painter, plot, y_at, y_min, y_max)
            self._draw_title(painter, bl, plot)
            self._draw_legend(painter, plot)

            if bl.cold_start:
                self._draw_cold_start_banner(painter, bl, plot)

            # Build hit-test rects last — depends on final pixel positions.
            self._build_point_rects(bl, x_at, y_at)
        finally:
            painter.end()

    # ── Coordinate helpers ────────────────────────────────────────

    @staticmethod
    def _compute_y_max(bl: RateBaseline) -> float:
        """Pick a Y-axis upper bound that contains all data with headroom."""
        # Both rates and upper_band can drive the visible peak.
        peak = 0.0
        if bl.rates:
            peak = max(peak, max(bl.rates))
        if bl.upper_band:
            peak = max(peak, max(bl.upper_band))
        # 15% headroom so the tallest line doesn't kiss the top edge.
        # Floor at 5 so empty-ish charts still have a sensible scale.
        return max(peak * 1.15, 5.0)

    @staticmethod
    def _x_axis_fn(plot: QRectF, n: int):
        """Return ``i -> x_pixel`` for ``n`` evenly-spaced buckets."""

        def x_at(i: int) -> float:
            if n <= 1:
                return plot.left() + plot.width() / 2.0
            return plot.left() + (i / (n - 1)) * plot.width()

        return x_at

    @staticmethod
    def _y_axis_fn(plot: QRectF, y_min: float, y_max: float):
        """Return ``value -> y_pixel`` (Y inverted for Qt's down-axis)."""
        span = y_max - y_min if y_max != y_min else 1.0

        def y_at(val: float) -> float:
            ratio = (val - y_min) / span
            return plot.bottom() - ratio * plot.height()

        return y_at

    # ── Drawing primitives ────────────────────────────────────────

    def _draw_band(self, painter: QPainter, bl: RateBaseline, x_at, y_at) -> None:
        """Fill the trailing-baseline ``mean ± 2σ`` band.

        Builds a closed polygon from the upper-band points along the top
        and the lower-band points (clamped to 0) along the bottom.
        Fills with translucent ALMA_INFO so the polyline reads on top.
        """
        if bl.cold_start:
            # In cold-start, baseline_std is 0 and upper_band is a flat
            # value. Render a dashed flat ceiling rather than a band.
            self._draw_flat_cold_band(painter, bl, x_at, y_at)
            return

        n = len(bl.rates)
        if n < 2:
            return

        path = QPainterPath()

        # Upper edge — left → right.
        for i in range(n):
            px = x_at(i)
            py = y_at(bl.upper_band[i])
            if i == 0:
                path.moveTo(px, py)
            else:
                path.lineTo(px, py)

        # Lower edge — right → left, clamped at 0.
        for i in reversed(range(n)):
            lower = max(bl.baseline_mean[i] - 2.0 * bl.baseline_std[i], 0.0)
            path.lineTo(x_at(i), y_at(lower))

        path.closeSubpath()

        band_color = QColor(ALMA_INFO)
        band_color.setAlpha(46)  # ≈ 18% — readable but doesn't drown the line
        painter.setBrush(QBrush(band_color))
        painter.setPen(Qt.NoPen)
        painter.drawPath(path)

    def _draw_flat_cold_band(
        self, painter: QPainter, bl: RateBaseline, x_at, y_at
    ) -> None:
        """Render the cold-start fallback band as a flat dashed ceiling."""
        n = len(bl.rates)
        if n < 1:
            return
        ceiling = bl.upper_band[0] if bl.upper_band else 0
        # Dashed amber ceiling — visually distinct from the σ band.
        painter.setPen(QPen(QColor(ALMA_WARNING), 1.2, Qt.DashLine))
        y = y_at(ceiling)
        painter.drawLine(QPointF(x_at(0), y), QPointF(x_at(n - 1), y))

    def _draw_grid(
        self,
        painter: QPainter,
        plot: QRectF,
        y_at,
        y_min: float,
        y_max: float,
    ) -> None:
        """Draw light horizontal gridlines at five evenly-spaced ticks."""
        painter.setPen(QPen(QColor(ALMA_CHART_GRID), 0.5, Qt.DotLine))
        for tick in self._y_ticks(y_min, y_max, 5):
            y = y_at(tick)
            painter.drawLine(
                QPointF(plot.left(), y),
                QPointF(plot.right(), y),
            )

    def _draw_baseline_mean_line(
        self, painter: QPainter, bl: RateBaseline, x_at, y_at
    ) -> None:
        """Dotted gray line at ``baseline_mean[i]`` for each bucket.

        Skipped in cold start — the mean is a placeholder there.
        """
        if bl.cold_start:
            return
        n = len(bl.rates)
        if n < 2:
            return
        painter.setPen(QPen(QColor(ALMA_TEXT_LIGHT), 1.5, Qt.DotLine))
        for i in range(n - 1):
            painter.drawLine(
                QPointF(x_at(i), y_at(bl.baseline_mean[i])),
                QPointF(x_at(i + 1), y_at(bl.baseline_mean[i + 1])),
            )

    def _draw_rate_polyline(
        self, painter: QPainter, bl: RateBaseline, x_at, y_at
    ) -> None:
        """Solid green line connecting the per-bucket ticket counts."""
        n = len(bl.rates)
        if n < 2:
            # Single-point fallback — render a dot.
            if n == 1:
                painter.setBrush(QBrush(QColor(ALMA_GREEN_DARK)))
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(
                    QPointF(x_at(0), y_at(bl.rates[0])), 4, 4
                )
            return

        painter.setPen(QPen(QColor(ALMA_GREEN_DARK), 2.0))
        for i in range(n - 1):
            painter.drawLine(
                QPointF(x_at(i), y_at(bl.rates[i])),
                QPointF(x_at(i + 1), y_at(bl.rates[i + 1])),
            )

    def _draw_spike_markers(
        self, painter: QPainter, bl: RateBaseline, x_at, y_at
    ) -> None:
        """Red filled dots at indices where ``is_spike[i]`` is True."""
        painter.setBrush(QBrush(QColor(ALMA_ERROR)))
        painter.setPen(Qt.NoPen)
        for i, flagged in enumerate(bl.is_spike):
            if flagged:
                painter.drawEllipse(
                    QPointF(x_at(i), y_at(bl.rates[i])), 4.5, 4.5
                )

    def _draw_x_labels(
        self,
        painter: QPainter,
        bl: RateBaseline,
        plot: QRectF,
        x_at,
    ) -> None:
        """Hour labels along the X axis with a "Day MMM-DD" boundary tick."""
        painter.setPen(QColor(ALMA_CHART_AXIS))
        painter.setFont(QFont("Segoe UI", 9))

        n = len(bl.hours)
        # Step picks a label cadence that keeps the axis legible.
        step = (
            _X_TICK_EVERY_48H if bl.window_hours >= 48 else _X_TICK_EVERY_24H
        )

        last_day_drawn: str | None = None
        for i in range(n):
            if i % step != 0 and i != n - 1:
                continue

            iso = bl.hours[i]
            hh = iso[11:13]
            day = iso[:10]

            x = x_at(i)
            painter.drawText(
                QRectF(x - 22, plot.bottom() + 4, 44, 14),
                Qt.AlignCenter,
                f"{hh}:00",
            )

            # Add a faint day-boundary marker on the first label of each
            # new calendar date.
            if day != last_day_drawn:
                painter.save()
                painter.setPen(QPen(QColor(ALMA_BORDER_LIGHT), 0.7, Qt.DashLine))
                painter.drawLine(
                    QPointF(x, plot.top()), QPointF(x, plot.bottom())
                )
                painter.restore()

                # Date sub-label below the hour.
                painter.drawText(
                    QRectF(x - 30, plot.bottom() + 18, 60, 14),
                    Qt.AlignCenter,
                    self._short_date(day),
                )
                last_day_drawn = day

    def _draw_y_labels(
        self,
        painter: QPainter,
        plot: QRectF,
        y_at,
        y_min: float,
        y_max: float,
    ) -> None:
        """Numeric Y-axis tick labels (right-aligned to the plot left)."""
        painter.setPen(QColor(ALMA_CHART_AXIS))
        painter.setFont(QFont("Segoe UI", 9))

        for tick in self._y_ticks(y_min, y_max, 5):
            y = y_at(tick)
            text = f"{int(tick)}" if tick.is_integer() else f"{tick:.1f}"
            painter.drawText(
                QRectF(0, y - 7, _MARGIN_LEFT - 4, 14),
                Qt.AlignRight | Qt.AlignVCenter,
                text,
            )

    def _draw_title(
        self, painter: QPainter, bl: RateBaseline, plot: QRectF
    ) -> None:
        """Chart title + 'computed at' timestamp."""
        painter.setPen(QColor(ALMA_TEXT_DARK))
        painter.setFont(QFont("Segoe UI", 11, QFont.Bold))
        scope = bl.trc_code if bl.trc_code else "All TRCs"
        painter.drawText(
            QRectF(plot.left(), 4, plot.width(), 22),
            Qt.AlignLeft | Qt.AlignVCenter,
            f"Tickets per hour — {scope}",
        )

        painter.setPen(QColor(ALMA_TEXT_LIGHT))
        painter.setFont(QFont("Segoe UI", 9))
        painter.drawText(
            QRectF(plot.left(), 4, plot.width(), 22),
            Qt.AlignRight | Qt.AlignVCenter,
            f"Updated {self._short_time(bl.computed_at)}",
        )

    def _draw_legend(self, painter: QPainter, plot: QRectF) -> None:
        """Color-key legend in the top-right of the plot."""
        painter.setFont(QFont("Segoe UI", 9))
        x = plot.right() - 230
        y = plot.top() + 4

        # Legend swatches and labels — single line, fixed widths.
        items = [
            (QColor(ALMA_GREEN_DARK), "Rate"),
            (QColor(ALMA_INFO), "Baseline ±2σ"),
            (QColor(ALMA_ERROR), "Spike"),
        ]
        for swatch_color, label in items:
            painter.setBrush(QBrush(swatch_color))
            painter.setPen(Qt.NoPen)
            painter.drawRect(QRectF(x, y + 4, 10, 6))
            painter.setPen(QColor(ALMA_TEXT_MID))
            metrics = QFontMetrics(painter.font())
            text_w = metrics.horizontalAdvance(label)
            painter.drawText(
                QRectF(x + 14, y, text_w + 4, 16),
                Qt.AlignLeft | Qt.AlignVCenter,
                label,
            )
            # Advance past the swatch + label + small gap.
            x += 14 + text_w + 16

    def _draw_cold_start_banner(
        self, painter: QPainter, bl: RateBaseline, plot: QRectF
    ) -> None:
        """Yellow strip along the top of the plot during cold start."""
        banner = QRectF(
            plot.left(),
            plot.top(),
            plot.width(),
            _COLD_BANNER_HEIGHT,
        )
        bg = QColor(ALMA_WARNING)
        bg.setAlpha(38)
        painter.fillRect(banner, bg)

        painter.setPen(QColor(ALMA_TEXT_DARK))
        painter.setFont(QFont("Segoe UI", 9, QFont.Bold))
        painter.drawText(
            banner,
            Qt.AlignCenter,
            f"Building baseline — day {bl.days_with_data} of 5",
        )

    # ── Hover tooltips ───────────────────────────────────────────

    def _build_point_rects(
        self, bl: RateBaseline, x_at, y_at
    ) -> None:
        """Cache one ``_PointRect`` per bucket for hover dispatch."""
        for i in range(len(bl.rates)):
            cx = int(x_at(i))
            cy = int(y_at(bl.rates[i]))
            rect = QRect(
                cx - _HIT_RADIUS,
                cy - _HIT_RADIUS,
                _HIT_RADIUS * 2,
                _HIT_RADIUS * 2,
            )
            label = self._short_bucket_label(bl.hours[i])
            tooltip = self._format_tooltip(bl, i)
            self._point_rects.append(_PointRect(rect, label, tooltip))

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        """Show a tooltip when hovering a plotted point."""
        pos = event.position().toPoint()
        for pr in self._point_rects:
            if pr.rect.contains(pos):
                QToolTip.showText(self.mapToGlobal(pos), pr.tooltip, self)
                return
        QToolTip.hideText()

    @staticmethod
    def _format_tooltip(bl: RateBaseline, i: int) -> str:
        """Build the multi-line tooltip body for bucket index ``i``."""
        ts = bl.hours[i].replace("T", " ")
        rate = bl.rates[i]
        mean = bl.baseline_mean[i]
        std = bl.baseline_std[i]
        spike = " · SPIKE" if bl.is_spike[i] else ""
        if bl.cold_start:
            return (
                f"{ts}\n"
                f"Rate: {rate}\n"
                f"Cold-start: building baseline ({bl.days_with_data}/5 days)"
            )
        return (
            f"{ts}{spike}\n"
            f"Rate: {rate} tickets/hr\n"
            f"Baseline: {mean:.1f} ± {std:.1f} (2σ)\n"
            f"Upper band: {bl.upper_band[i]:.1f}"
        )

    # ── Tick / label helpers ─────────────────────────────────────

    @staticmethod
    def _y_ticks(y_min: float, y_max: float, n: int) -> list[float]:
        """Return ``n`` evenly-spaced tick values from ``y_min`` to ``y_max``.

        Used for both gridlines and label rendering, so they stay aligned.
        """
        if n < 2:
            return [y_min, y_max]
        step = (y_max - y_min) / (n - 1)
        return [y_min + i * step for i in range(n)]

    @staticmethod
    def _short_date(iso_date: str) -> str:
        """Format ``"2026-05-07"`` as ``"May 07"`` for X-axis labels."""
        try:
            from datetime import datetime as _dt

            dt = _dt.strptime(iso_date, "%Y-%m-%d")
            return dt.strftime("%b %d")
        except Exception:
            # Be forgiving — render the raw key if parsing fails.
            return iso_date

    @staticmethod
    def _short_time(iso: str) -> str:
        """Compact 'HH:MM' display for the title's 'Updated' annotation."""
        # iso may be "2026-05-07T14:00:00" or "...+00:00" — slice to HH:MM.
        if "T" in iso and len(iso) >= 16:
            return iso[11:16]
        return iso

    @staticmethod
    def _short_bucket_label(iso: str) -> str:
        """Produce a 'YYYY-MM-DD HH:00' label for the tooltip header."""
        if "T" in iso:
            return iso.replace("T", " ")[:16]
        return iso
