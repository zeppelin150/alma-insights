"""
Alma Insights — Skeleton Loading Widget (Build 10.0: T7)

Shimmer loading placeholders for KPI cards, tables, and chart areas.
Uses QTimer-driven gradient sweep in paintEvent for the shimmer effect.
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QSizePolicy
from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QPainter, QColor, QLinearGradient

try:
    from shiboken6 import isValid as _shiboken_is_valid
except ImportError:
    _shiboken_is_valid = None


# Shimmer colors (on cream background)
_BASE = QColor(0, 0, 0, 15)       # ~6% black
_HIGHLIGHT = QColor(0, 0, 0, 26)  # ~10% black
_SHIMMER_MS = 30                   # repaint interval
_SWEEP_SPEED = 4                   # px per frame


class SkeletonRect(QWidget):
    """Single shimmer rectangle — the atomic building block."""

    _shared_offset = 0   # Class-level so all rects shimmer in sync
    _shared_timer = None  # Single timer for all instances

    def __init__(self, width: int = 100, height: int = 20,
                 radius: int = 8, parent=None):
        super().__init__(parent)
        self._radius = radius
        self.setFixedSize(width, height)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        # Start shared timer if not already running
        if SkeletonRect._shared_timer is None:
            SkeletonRect._shared_timer = QTimer()
            SkeletonRect._shared_timer.setInterval(_SHIMMER_MS)
            SkeletonRect._shared_timer.timeout.connect(SkeletonRect._advance)
            SkeletonRect._shared_timer.start()

        # Track all instances for repaint
        if not hasattr(SkeletonRect, '_instances'):
            SkeletonRect._instances = []
        SkeletonRect._instances.append(self)

    def destroy(self, *args, **kwargs):
        if hasattr(SkeletonRect, '_instances'):
            try:
                SkeletonRect._instances.remove(self)
            except ValueError:
                pass
            # Stop timer if no instances left
            if not SkeletonRect._instances and SkeletonRect._shared_timer:
                SkeletonRect._shared_timer.stop()
                SkeletonRect._shared_timer = None
        super().destroy(*args, **kwargs)

    @staticmethod
    def _advance():
        SkeletonRect._shared_offset += _SWEEP_SPEED
        if SkeletonRect._shared_offset > 600:
            SkeletonRect._shared_offset = -200
        if not hasattr(SkeletonRect, '_instances'):
            return

        # Iterate a copy; prune dead C++ refs to prevent crash
        dead = []
        for inst in list(SkeletonRect._instances):
            # Check if the underlying C++ object is still alive
            if _shiboken_is_valid is not None and not _shiboken_is_valid(inst):
                dead.append(inst)
                continue
            try:
                if inst.isVisible():
                    inst.update()
            except (RuntimeError, ReferenceError):
                dead.append(inst)

        # Remove dead references
        for d in dead:
            try:
                SkeletonRect._instances.remove(d)
            except ValueError:
                pass

        # Stop timer if all instances are gone
        if not SkeletonRect._instances and SkeletonRect._shared_timer:
            SkeletonRect._shared_timer.stop()
            SkeletonRect._shared_timer = None

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # Gradient sweep: highlight band moves left-to-right
        grad = QLinearGradient(
            SkeletonRect._shared_offset - 100, 0,
            SkeletonRect._shared_offset + 100, 0,
        )
        grad.setColorAt(0.0, _BASE)
        grad.setColorAt(0.5, _HIGHLIGHT)
        grad.setColorAt(1.0, _BASE)

        p.setBrush(grad)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(
            QRectF(0, 0, self.width(), self.height()),
            self._radius, self._radius,
        )
        p.end()

    @staticmethod
    def reset_shared_state():
        """Reset class-level state — useful for tests."""
        SkeletonRect._shared_offset = 0
        if SkeletonRect._shared_timer:
            SkeletonRect._shared_timer.stop()
            SkeletonRect._shared_timer = None
        if hasattr(SkeletonRect, '_instances'):
            SkeletonRect._instances.clear()


class SkeletonGroup(QWidget):
    """Composable skeleton layout with factory methods for common patterns."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(12)

    @staticmethod
    def kpi_card(parent=None) -> "SkeletonGroup":
        """Skeleton matching a KPI card: label + big number + subtitle."""
        group = SkeletonGroup(parent)
        group.setFixedSize(200, 100)

        group._layout.setContentsMargins(16, 16, 16, 16)
        group._layout.setSpacing(8)

        # Label placeholder
        group._layout.addWidget(SkeletonRect(80, 12, 4, group))
        # Big number
        group._layout.addWidget(SkeletonRect(120, 28, 6, group))
        # Subtitle / delta
        group._layout.addWidget(SkeletonRect(60, 12, 4, group))

        group._layout.addStretch()
        return group

    @staticmethod
    def table_rows(n: int = 5, parent=None) -> "SkeletonGroup":
        """Skeleton matching n table rows with varying widths."""
        group = SkeletonGroup(parent)
        group._layout.setContentsMargins(16, 16, 16, 16)
        group._layout.setSpacing(10)

        widths = [320, 280, 300, 260, 340]  # Vary row width for realism
        for i in range(n):
            w = widths[i % len(widths)]
            group._layout.addWidget(SkeletonRect(w, 16, 4, group))

        return group

    @staticmethod
    def chart_area(parent=None) -> "SkeletonGroup":
        """Skeleton matching a chart area."""
        group = SkeletonGroup(parent)
        group._layout.setContentsMargins(16, 16, 16, 16)
        group._layout.setSpacing(12)

        # Title placeholder
        group._layout.addWidget(SkeletonRect(160, 16, 4, group))

        # Chart body
        group._layout.addWidget(SkeletonRect(400, 200, 8, group))

        # Legend row
        row = QHBoxLayout()
        row.setSpacing(16)
        for _ in range(3):
            row.addWidget(SkeletonRect(60, 12, 4, group))
        row.addStretch()
        group._layout.addLayout(row)

        return group

    @staticmethod
    def kpi_row(count: int = 4, parent=None) -> QWidget:
        """Horizontal row of KPI card skeletons."""
        container = QWidget(parent)
        h_layout = QHBoxLayout(container)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(16)

        for _ in range(count):
            h_layout.addWidget(SkeletonGroup.kpi_card(container))
        h_layout.addStretch()

        return container
