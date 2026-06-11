"""Animation presets — shared durations and easing curves."""

from PySide6.QtCore import (
    QAbstractAnimation, QEasingCurve, QPropertyAnimation,
)
from PySide6.QtWidgets import QGraphicsOpacityEffect

DUR = {"fast": 120, "base": 200, "slow": 250}

EASE = {
    "out": QEasingCurve.OutCubic,
    "inout": QEasingCurve.InOutCubic,
}


def fade_in(widget, duration=None):
    """Fade a widget from 0 → 1 opacity. Caller must keep the returned
    animation referenced or Qt garbage-collects it mid-flight."""
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity")
    anim.setDuration(duration or DUR["base"])
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(EASE["out"])
    anim.finished.connect(lambda w=widget: w.setGraphicsEffect(None))
    anim.start(QAbstractAnimation.KeepWhenStopped)
    return anim
