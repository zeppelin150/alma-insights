"""Generic full-window focus/expand overlay base.

Factors the plumbing shared by the enablement expand overlays: parents to
the content area, fills it, raises above the page, tracks parent resizes,
and dismisses on Collapse / Esc. Subclasses (or callers) add widgets to
``self.content`` and connect ``collapsed``.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from src.ui.theme import (
    ALMA_CREAM, ALMA_GREEN_DARK, ALMA_TEXT_DARK, ALMA_TEXT_ON_DARK,
)


class FocusOverlay(QFrame):
    collapsed = Signal()

    def __init__(self, parent=None, *, title: str = ""):
        super().__init__(parent)
        self.setObjectName("FocusOverlay")
        self.setStyleSheet(f"#FocusOverlay{{background:{ALMA_CREAM}; border:none;}}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(48, 28, 48, 32)
        outer.setSpacing(12)

        head = QHBoxLayout()
        self._title = QLabel(title)
        self._title.setStyleSheet(
            f"color:{ALMA_TEXT_DARK}; font-size:20px; font-weight:700; border:none;")
        head.addWidget(self._title)
        head.addStretch(1)
        collapse = QPushButton("⤡  Collapse")
        collapse.setCursor(Qt.PointingHandCursor)
        collapse.setStyleSheet(
            f"QPushButton{{background:{ALMA_GREEN_DARK}; color:{ALMA_TEXT_ON_DARK}; "
            f"border:none; border-radius:8px; padding:7px 16px; font-size:12px; "
            f"font-weight:600;}}")
        collapse.clicked.connect(self._collapse)
        head.addWidget(collapse)
        outer.addLayout(head)

        self.content = QVBoxLayout()
        self.content.setSpacing(12)
        outer.addLayout(self.content, 1)

        self.hide()
        if parent is not None:
            parent.installEventFilter(self)

    def set_title(self, text: str):
        self._title.setText(text or "")

    def present(self):
        host = self.parentWidget()
        if host is None:
            self.show()
            return
        self.setGeometry(0, 0, host.width(), host.height())
        self.show()
        self.raise_()
        self.setFocus()

    def _collapse(self):
        self.collapsed.emit()
        self.hide()

    def eventFilter(self, obj, event):
        if (obj is self.parentWidget() and event.type() == QEvent.Resize
                and self.isVisible()):
            self.setGeometry(0, 0, obj.width(), obj.height())
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._collapse()
            return
        super().keyPressEvent(event)
