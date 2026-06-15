"""Small, on-brand QPainter charts for the enablement Analytics page.

`DonutChart` — a ring of coloured segments with a total in the centre and a
legend, used for the verification-state breakdown.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from src.ui.theme import ALMA_BG_INSET, ALMA_TEXT_DARK, ALMA_TEXT_LIGHT


class DonutChart(QWidget):
    """segments = [(label, value, '#hex'), …]; draws a donut + centre total."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._segments: list[tuple] = []
        self._center_label = ""
        self.setMinimumHeight(150)

    def set_segments(self, segments: list[tuple], center_label: str = ""):
        self._segments = [(str(l), max(0, int(v)), c) for l, v, c in (segments or [])]
        self._center_label = center_label
        self.update()

    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(180, 160)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        total = sum(v for _, v, _ in self._segments) or 0

        size = min(self.width(), self.height()) - 12
        size = max(size, 10)
        x = 6
        y = (self.height() - size) / 2
        ring = max(14, int(size * 0.18))
        rect = QRectF(x + ring / 2, y + ring / 2, size - ring, size - ring)

        if total == 0:
            pen = QPen(QColor(ALMA_BG_INSET), ring)
            pen.setCapStyle(Qt.FlatCap)
            p.setPen(pen)
            p.drawArc(rect, 0, 360 * 16)
        else:
            start = 90 * 16   # 12 o'clock
            for _, value, color in self._segments:
                if value <= 0:
                    continue
                span = -int(360 * 16 * value / total)
                pen = QPen(QColor(color), ring)
                pen.setCapStyle(Qt.FlatCap)
                p.setPen(pen)
                p.drawArc(rect, start, span)
                start += span

        # centre total
        p.setPen(QColor(ALMA_TEXT_DARK))
        f = QFont(); f.setPointSize(18); f.setBold(True)
        p.setFont(f)
        p.drawText(rect, Qt.AlignCenter, str(total))
        if self._center_label:
            p.setPen(QColor(ALMA_TEXT_LIGHT))
            lf = QFont(); lf.setPointSize(7); lf.setBold(True)
            p.setFont(lf)
            below = QRectF(rect.x(), rect.center().y() + 14, rect.width(), 16)
            p.drawText(below, Qt.AlignHCenter | Qt.AlignTop,
                       self._center_label.upper())
        p.end()
