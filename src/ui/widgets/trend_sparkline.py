"""
Alma Insights — Trend Sparkline Widget (Build 11.0)

Custom QWidget that draws a mini line chart via QPainter.
Input: list of (label, value) tuples.
"""

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QPen, QColor, QLinearGradient, QPainterPath

from src.ui.theme import ALMA_GREEN_DARK, ALMA_ERROR, ALMA_WARNING


class TrendSparkline(QWidget):
    """Mini line chart rendered with QPainter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = []  # list of (label, value)
        self._color = QColor(ALMA_GREEN_DARK)
        self.setMinimumHeight(60)
        self.setMaximumHeight(100)

    def set_data(self, data: list[tuple[str, float]], color: str = None):
        """Set chart data and trigger repaint."""
        self._data = data
        if color:
            self._color = QColor(color)
        self.update()

    def set_trend_color(self, direction: str):
        """Set color based on trend direction."""
        colors = {
            "rising": QColor(ALMA_ERROR),
            "falling": QColor(ALMA_GREEN_DARK),
            "stable": QColor(ALMA_WARNING),
        }
        self._color = colors.get(direction, QColor(ALMA_GREEN_DARK))

    def paintEvent(self, event):
        if not self._data or len(self._data) < 2:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        pad = 8

        values = [d[1] for d in self._data]
        vmin = min(values)
        vmax = max(values)
        vrange = vmax - vmin if vmax != vmin else 1

        n = len(values)
        step = (w - 2 * pad) / max(n - 1, 1)

        # Build path
        path = QPainterPath()
        points = []
        for i, v in enumerate(values):
            x = pad + i * step
            y = h - pad - ((v - vmin) / vrange) * (h - 2 * pad)
            points.append((x, y))
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)

        # Draw fill gradient
        fill_path = QPainterPath(path)
        fill_path.lineTo(points[-1][0], h - pad)
        fill_path.lineTo(points[0][0], h - pad)
        fill_path.closeSubpath()

        gradient = QLinearGradient(0, 0, 0, h)
        fill_color = QColor(self._color)
        fill_color.setAlpha(40)
        gradient.setColorAt(0, fill_color)
        fill_color.setAlpha(5)
        gradient.setColorAt(1, fill_color)
        painter.fillPath(fill_path, gradient)

        # Draw line
        pen = QPen(self._color, 2)
        painter.setPen(pen)
        painter.drawPath(path)

        # Draw endpoint dot
        if points:
            painter.setBrush(self._color)
            painter.setPen(Qt.NoPen)
            lx, ly = points[-1]
            painter.drawEllipse(QRectF(lx - 3, ly - 3, 6, 6))

        painter.end()
