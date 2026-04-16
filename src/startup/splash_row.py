"""
Alma Insights — Splash check row widget.

Renders a single CheckResult as a row in the startup splash:

    [icon]  Checking Python environment
            Python 3.12.4 — venv active

Status → icon + colour:
    pass  → filled green circle  (✓)
    warn  → filled amber circle  (⚠)
    fail  → filled red circle    (✗)

No external dependencies beyond PySide6 and the colour constants from
src.ui.theme. Used by src.startup.splash_window.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from src.startup.checker import CheckResult
from src.ui.theme import (
    ALMA_ERROR,
    ALMA_SUCCESS,
    ALMA_TEXT_ON_DARK,
    ALMA_WARNING,
)

_STATUS_COLOURS = {
    "pass": ALMA_SUCCESS,
    "warn": ALMA_WARNING,
    "fail": ALMA_ERROR,
}
_STATUS_GLYPHS = {
    "pass": "✓",
    "warn": "!",
    "fail": "✗",
}


class _StatusIcon(QWidget):
    """A 20 px circle with a glyph drawn inside — small, paint-only widget."""

    def __init__(self, status: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._status = status
        self.setFixedSize(20, 20)

    def paintEvent(self, _event):  # noqa: N802 — Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        colour = QColor(_STATUS_COLOURS.get(self._status, ALMA_WARNING))
        painter.setBrush(colour)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(0, 0, 20, 20)

        painter.setPen(QColor("#FFFFFF"))
        font = QFont()
        font.setBold(True)
        font.setPointSize(10)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignCenter, _STATUS_GLYPHS.get(self._status, "?"))


class SplashRow(QFrame):
    """A single check row on the splash. Immutable once constructed."""

    def __init__(self, result: CheckResult, parent: QWidget | None = None):
        super().__init__(parent)
        self._result = result
        self.setObjectName("splashRow")
        self._build()

    # ----- introspection (used by tests) -----

    @property
    def result(self) -> CheckResult:
        return self._result

    # ----- construction -----

    def _build(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 8)
        outer.setSpacing(14)

        outer.addWidget(_StatusIcon(self._result.status), 0, Qt.AlignTop)

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(2)

        name = QLabel(self._result.name)
        name_colour = _STATUS_COLOURS.get(self._result.status, ALMA_TEXT_ON_DARK)
        name.setStyleSheet(
            f"color: {name_colour}; font-weight: 600; font-size: 13px;"
        )
        text_column.addWidget(name)

        subtext = self._subtext()
        if subtext:
            detail = QLabel(subtext)
            detail.setStyleSheet(
                f"color: {ALMA_TEXT_ON_DARK}; opacity: 0.75; font-size: 11px;"
            )
            detail.setWordWrap(True)
            text_column.addWidget(detail)

        outer.addLayout(text_column, 1)

    def _subtext(self) -> str:
        """Message first; remediation appended on warn/fail when present."""
        if self._result.status == "pass":
            return self._result.message
        if self._result.remediation:
            if self._result.message:
                return f"{self._result.message}. {self._result.remediation}"
            return self._result.remediation
        return self._result.message
