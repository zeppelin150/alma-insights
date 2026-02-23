"""
Alma Insights — Sprout Loading Animation

A 6-phase discrete storyboard animation of a growing sprout,
drawn with QPainter.  White strokes/fills on a transparent
background (the overlay supplies the dark green).

Phases cycle at ~200 ms each, looping forever:
  0: Two short horizontal baseline segments meeting at center
  1: Baseline + vertical stem
  2: + two short diagonal branches (half length)
  3: + branches extended to full length
  4: + small teardrop leaves at tips + top
  5: + full/larger leaves + extra mid-branch pair
"""

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QTimer, QPointF
from PySide6.QtGui import QPainter, QPen, QColor, QPainterPath


class SproutAnimation(QWidget):
    """Animated growing-sprout brand symbol for the Alma loading overlay."""

    PHASE_COUNT = 6
    PHASE_INTERVAL_MS = 200  # milliseconds per phase

    def __init__(self, size: int = 80, parent=None):
        super().__init__(parent)
        self._phase = 0
        self._size = size
        self.setFixedSize(size, int(size * 1.3))
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance_phase)

        # Colours
        self._stroke_color = QColor(255, 255, 255)
        self._leaf_fill = QColor(255, 255, 255, 200)
        self._stroke_width = max(2.0, size / 32)

    # ── Public ───────────────────────────────────────────────────────

    def start(self):
        self._phase = 0
        self._timer.start(self.PHASE_INTERVAL_MS)
        self.update()

    def stop(self):
        self._timer.stop()

    def set_size(self, size: int):
        self._size = size
        self._stroke_width = max(2.0, size / 32)
        self.setFixedSize(size, int(size * 1.3))
        self.update()

    # ── Internal ─────────────────────────────────────────────────────

    def _advance_phase(self):
        self._phase = (self._phase + 1) % self.PHASE_COUNT
        self.update()

    # ── Drawing ──────────────────────────────────────────────────────

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        cx = w / 2.0
        s = self._size  # reference size

        # Key Y positions (from bottom up)
        base_y = h * 0.92
        stem_top = h * 0.18
        branch_y = h * 0.52          # fork point on stem
        branch_upper_y = h * 0.35    # upper fork

        # Pen
        pen = QPen(self._stroke_color, self._stroke_width)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)

        phase = self._phase

        # ── Phase 0+: Baseline ───────────────────────────────────────
        bl_half = s * 0.22
        p.drawLine(QPointF(cx - bl_half, base_y), QPointF(cx, base_y))
        p.drawLine(QPointF(cx, base_y), QPointF(cx + bl_half, base_y))

        if phase == 0:
            p.end()
            return

        # ── Phase 1+: Stem ───────────────────────────────────────────
        p.drawLine(QPointF(cx, base_y), QPointF(cx, stem_top))

        if phase == 1:
            p.end()
            return

        # ── Phase 2+: Branches ───────────────────────────────────────
        # Lower branches
        branch_spread = s * 0.28 if phase >= 3 else s * 0.16
        branch_rise = h * 0.18 if phase >= 3 else h * 0.10

        left_tip = QPointF(cx - branch_spread, branch_y - branch_rise)
        right_tip = QPointF(cx + branch_spread, branch_y - branch_rise)
        p.drawLine(QPointF(cx, branch_y), left_tip)
        p.drawLine(QPointF(cx, branch_y), right_tip)

        # Upper sub-branches (smaller, closer to top)
        upper_spread = s * 0.18 if phase >= 3 else s * 0.10
        upper_rise = h * 0.12 if phase >= 3 else h * 0.06

        upper_left = QPointF(cx - upper_spread, branch_upper_y - upper_rise)
        upper_right = QPointF(cx + upper_spread, branch_upper_y - upper_rise)
        p.drawLine(QPointF(cx, branch_upper_y), upper_left)
        p.drawLine(QPointF(cx, branch_upper_y), upper_right)

        if phase <= 3:
            p.end()
            return

        # ── Phase 4+: Leaves ─────────────────────────────────────────
        small = phase == 4
        leaf_size = s * 0.08 if small else s * 0.12
        leaf_size_sm = s * 0.06 if small else s * 0.09

        p.setPen(QPen(self._stroke_color, self._stroke_width * 0.7))
        p.setBrush(self._leaf_fill)

        # Leaves at lower branch tips
        self._draw_leaf(p, left_tip.x(), left_tip.y(), -35, leaf_size)
        self._draw_leaf(p, right_tip.x(), right_tip.y(), 35, leaf_size)

        # Leaves at upper branch tips
        self._draw_leaf(p, upper_left.x(), upper_left.y(), -25, leaf_size_sm)
        self._draw_leaf(p, upper_right.x(), upper_right.y(), 25, leaf_size_sm)

        # Crown leaf at stem top
        self._draw_leaf(p, cx, stem_top, 0, leaf_size)

        # ── Phase 5: Extra mid-branch leaves ─────────────────────────
        if phase >= 5:
            # Mid-point leaves on lower branches
            mid_lx = (cx + left_tip.x()) / 2
            mid_ly = (branch_y + left_tip.y()) / 2
            mid_rx = (cx + right_tip.x()) / 2
            mid_ry = (branch_y + right_tip.y()) / 2
            self._draw_leaf(p, mid_lx, mid_ly, -50, leaf_size_sm)
            self._draw_leaf(p, mid_rx, mid_ry, 50, leaf_size_sm)

            # Tiny leaves flanking the crown
            self._draw_leaf(p, cx - s * 0.06, stem_top + h * 0.04, -20, leaf_size_sm * 0.7)
            self._draw_leaf(p, cx + s * 0.06, stem_top + h * 0.04, 20, leaf_size_sm * 0.7)

        p.end()

    def _draw_leaf(self, painter: QPainter, cx: float, cy: float,
                   angle: float, size: float):
        """Draw a teardrop leaf at (cx, cy), rotated by *angle* degrees."""
        painter.save()
        painter.translate(cx, cy)
        painter.rotate(angle)

        path = QPainterPath()
        # Teardrop: point at origin, bulge upward
        path.moveTo(0, 0)
        path.cubicTo(-size * 0.55, -size * 0.35,
                     -size * 0.45, -size * 0.9,
                     0, -size)
        path.cubicTo(size * 0.45, -size * 0.9,
                     size * 0.55, -size * 0.35,
                     0, 0)

        painter.drawPath(path)
        painter.restore()
