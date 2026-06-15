"""Full-window focus/expand overlay for a slide deck.

Mirrors the Workbench ExpandOverlay: a self-contained QFrame that fills the
content area, hosting a Slides-preview / Outline-edit pair for the active
deck. On collapse it emits `committed(deck_id, title, outline)`; the
PowerPoint page persists it.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from src.ui.pages.enablement.slide_view import render_slides
from src.ui.theme import (
    ALMA_BG_ELEVATED, ALMA_BG_INSET, ALMA_BORDER, ALMA_CREAM, ALMA_GREEN_DARK,
    ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_ON_DARK,
)

_TEAL = "#0D7D72"


class PptxExpandOverlay(QFrame):
    committed = Signal(int, str, dict)   # (deck_id, title, outline)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._deck_id = 0
        self._outline = {"title": "", "slides": []}
        self.setObjectName("PptxExpandOverlay")
        self.setStyleSheet(f"#PptxExpandOverlay{{background:{ALMA_CREAM}; border:none;}}")
        self._build()
        self.hide()
        if parent is not None:
            parent.installEventFilter(self)

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(48, 28, 48, 32)
        outer.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(10)
        self._title = QLineEdit()
        self._title.setStyleSheet(
            f"QLineEdit{{background:{ALMA_BG_ELEVATED}; border:1px solid {ALMA_BORDER}; "
            f"border-radius:7px; padding:6px 12px; font-size:18px; font-weight:700; "
            f"color:{ALMA_TEXT_DARK}; min-width:320px;}}")
        head.addWidget(self._title)
        head.addStretch(1)
        self._slides_btn = self._toggle("Slides")
        self._slides_btn.clicked.connect(lambda: self._set_mode("slides"))
        head.addWidget(self._slides_btn)
        self._outline_btn = self._toggle("Outline")
        self._outline_btn.clicked.connect(lambda: self._set_mode("outline"))
        head.addWidget(self._outline_btn)
        collapse = QPushButton("⤡  Collapse")
        collapse.setCursor(Qt.PointingHandCursor)
        collapse.setStyleSheet(
            f"QPushButton{{background:{ALMA_GREEN_DARK}; color:{ALMA_TEXT_ON_DARK}; "
            f"border:none; border-radius:8px; padding:7px 16px; font-size:12px; "
            f"font-weight:600;}}")
        collapse.clicked.connect(self._collapse)
        head.addWidget(collapse)
        outer.addLayout(head)

        # slides preview (scrollable)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet("QScrollArea{border:none; background:transparent;}")
        holder = QWidget()
        holder.setStyleSheet("background:transparent;")
        self._slides_layout = QVBoxLayout(holder)
        self._slides_layout.setContentsMargins(0, 0, 8, 0)
        self._slides_layout.setSpacing(10)
        self._scroll.setWidget(holder)

        self._source = QPlainTextEdit()
        self._source.setStyleSheet(
            f"QPlainTextEdit{{background:{ALMA_BG_INSET}; border:1px solid {ALMA_BORDER}; "
            f"border-radius:8px; color:{ALMA_TEXT_DARK}; "
            f"font-family:Consolas,monospace; font-size:13px; padding:12px;}}")

        self._stack = QStackedWidget()
        self._stack.addWidget(self._scroll)    # 0 slides
        self._stack.addWidget(self._source)     # 1 outline
        outer.addWidget(self._stack, 1)

    def _toggle(self, text: str) -> QPushButton:
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        self._style(b, active=False)
        return b

    def _style(self, b, *, active: bool):
        bg = "#DCEFEC" if active else ALMA_BG_ELEVATED
        fg = _TEAL if active else ALMA_TEXT_MID
        border = _TEAL if active else ALMA_BORDER
        b.setStyleSheet(
            f"QPushButton{{background:{bg}; color:{fg}; border:1px solid {border}; "
            f"border-radius:7px; padding:5px 14px; font-size:11px; font-weight:600;}}")

    # ── data / mode ─────────────────────────────────────────────────
    def load(self, deck: dict):
        from src.data.pptx_store import _normalize_outline
        self._deck_id = int(deck.get("id") or 0)
        self._outline = _normalize_outline(deck.get("outline") or {})
        self._title.setText(deck.get("title") or self._outline.get("title", ""))
        self._set_mode("slides")

    def _outline_to_text(self, outline: dict) -> str:
        from src.ui.pages.enablement.pptx_tab import outline_to_text
        return outline_to_text(outline)

    def _capture(self):
        if self._stack.currentWidget() is self._source:
            from src.ui.pages.enablement.pptx_tab import text_to_outline
            self._outline = text_to_outline(self._title.text(),
                                            self._source.toPlainText())

    def _current_outline(self) -> dict:
        self._capture()
        out = dict(self._outline)
        out["title"] = self._title.text().strip() or out.get("title", "Untitled deck")
        return out

    def _set_mode(self, mode: str):
        self._capture()
        if mode == "outline":
            self._source.setPlainText(self._outline_to_text(self._outline))
            self._stack.setCurrentWidget(self._source)
        else:
            render_slides(self._slides_layout, self._outline)
            self._stack.setCurrentWidget(self._scroll)
        self._style(self._slides_btn, active=mode == "slides")
        self._style(self._outline_btn, active=mode == "outline")

    # ── present / dismiss ───────────────────────────────────────────
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
        out = self._current_outline()
        self.committed.emit(self._deck_id, out["title"], out)
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
