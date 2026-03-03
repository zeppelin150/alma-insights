"""
Alma Insights — Toast Notifications (Build 10.0: T9)

Slide-in toast notifications anchored to the bottom-right of the main window.
Supports success, error, info, and warning types. Auto-dismisses after duration.
Multiple toasts stack vertically.

Pattern follows existing job_overlay.py animation approach.
"""

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QGraphicsOpacityEffect, QToolButton,
)
from PySide6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint,
    QAbstractAnimation,
)
from PySide6.QtGui import QColor


# Toast configuration
_TOAST_WIDTH = 340
_TOAST_HEIGHT = 52
_MARGIN = 20           # px from window edge
_STACK_GAP = 8         # px between stacked toasts
_SLIDE_DURATION = 200  # ms
_FADE_DURATION = 300   # ms

# Type → (left-border color, icon)
_TYPE_CONFIG = {
    "success": ("#16763A", "✓"),
    "error":   ("#C41E1E", "✗"),
    "info":    ("#1D6FA5", "ℹ"),
    "warning": ("#B45309", "⚠"),
}


class ToastWidget(QWidget):
    """Single toast notification — slides in, auto-dismisses."""

    def __init__(self, message: str, icon: str = "✓",
                 toast_type: str = "success", duration_ms: int = 4000,
                 parent=None):
        super().__init__(parent)
        self.setFixedSize(_TOAST_WIDTH, _TOAST_HEIGHT)
        self.setAttribute(Qt.WA_TranslucentBackground, False)

        border_color, default_icon = _TYPE_CONFIG.get(toast_type, _TYPE_CONFIG["info"])
        icon = icon or default_icon

        # Styling
        self.setStyleSheet(
            f"ToastWidget {{"
            f"  background: #FFFFFF;"
            f"  border: 1px solid rgba(214, 210, 202, 0.45);"
            f"  border-left: 4px solid {border_color};"
            f"  border-radius: 10px;"
            f"}}"
        )

        # Layout
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 8, 8)
        layout.setSpacing(8)

        # Icon
        icon_label = QLabel(icon)
        icon_label.setFixedWidth(20)
        icon_label.setStyleSheet(
            f"font-size: 16px; color: {border_color}; background: transparent; border: none;"
        )
        icon_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon_label)

        # Message
        msg_label = QLabel(message)
        msg_label.setWordWrap(True)
        msg_label.setStyleSheet(
            "font-size: 13px; font-weight: 500; color: #1A1A1A;"
            " background: transparent; border: none;"
        )
        layout.addWidget(msg_label, 1)

        # Dismiss button
        close_btn = QToolButton()
        close_btn.setText("✕")
        close_btn.setFixedSize(20, 20)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet(
            "QToolButton { background: transparent; border: none; color: #7A7A7A;"
            " font-size: 11px; border-radius: 10px; }"
            "QToolButton:hover { background: rgba(0,0,0,0.06); }"
        )
        close_btn.clicked.connect(self._dismiss)
        layout.addWidget(close_btn)

        # Opacity effect for fade-out
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity)

        # Auto-dismiss timer
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.setInterval(duration_ms)
        self._auto_timer.timeout.connect(self._dismiss)

        # Animations (created on demand)
        self._slide_anim = None
        self._fade_anim = None
        self._manager = None  # set by ToastManager

    def slide_in(self, target_pos: QPoint):
        """Animate from off-screen right to target position."""
        start = QPoint(target_pos.x() + _TOAST_WIDTH + 20, target_pos.y())
        self.move(start)
        self.show()
        self.raise_()

        self._slide_anim = QPropertyAnimation(self, b"pos")
        self._slide_anim.setDuration(_SLIDE_DURATION)
        self._slide_anim.setStartValue(start)
        self._slide_anim.setEndValue(target_pos)
        self._slide_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._slide_anim.start(QAbstractAnimation.KeepWhenStopped)

        self._auto_timer.start()

    def _dismiss(self):
        """Fade out and remove."""
        self._auto_timer.stop()

        self._fade_anim = QPropertyAnimation(self._opacity, b"opacity")
        self._fade_anim.setDuration(_FADE_DURATION)
        self._fade_anim.setStartValue(1.0)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.setEasingCurve(QEasingCurve.InCubic)
        self._fade_anim.finished.connect(self._on_fade_done)
        self._fade_anim.start(QAbstractAnimation.KeepWhenStopped)

    def _on_fade_done(self):
        """Clean up after fade-out."""
        if self._manager:
            self._manager._remove_toast(self)
        self.hide()
        self.deleteLater()


class ToastManager:
    """
    Manages toast notifications for a parent window.

    Usage:
        self._toasts = ToastManager(self)  # 'self' is the main window
        self._toasts.show_toast("Scan complete", toast_type="success")
        self._toasts.show_toast("Import failed", icon="✗", toast_type="error")
    """

    def __init__(self, parent_widget: QWidget):
        self._parent = parent_widget
        self._active: list[ToastWidget] = []

    def show_toast(self, message: str, icon: str = None,
                   duration_ms: int = 4000, toast_type: str = "success"):
        """Show a new toast notification."""
        toast = ToastWidget(
            message=message,
            icon=icon,
            toast_type=toast_type,
            duration_ms=duration_ms,
            parent=self._parent,
        )
        toast._manager = self
        self._active.append(toast)

        # Calculate position: bottom-right, stacking upward
        pos = self._calc_position(len(self._active) - 1)
        toast.slide_in(pos)

    def _calc_position(self, index: int) -> QPoint:
        """Calculate position for toast at given stack index."""
        parent_rect = self._parent.rect()
        x = parent_rect.width() - _TOAST_WIDTH - _MARGIN
        y = parent_rect.height() - _MARGIN - (
            (index + 1) * (_TOAST_HEIGHT + _STACK_GAP)
        )
        return QPoint(x, y)

    def _remove_toast(self, toast: ToastWidget):
        """Remove a dismissed toast and reposition remaining."""
        if toast in self._active:
            self._active.remove(toast)
            # Reposition remaining toasts
            for i, t in enumerate(self._active):
                new_pos = self._calc_position(i)
                anim = QPropertyAnimation(t, b"pos")
                anim.setDuration(150)
                anim.setEndValue(new_pos)
                anim.setEasingCurve(QEasingCurve.OutCubic)
                anim.start(QAbstractAnimation.DeleteWhenStopped)
