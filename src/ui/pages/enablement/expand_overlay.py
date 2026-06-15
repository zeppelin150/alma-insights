"""Full-window focus/expand overlay for the Workbench draft editor.

A self-contained `QFrame` that, when presented, fills its parent (the
MainWindow content area — the same `content_stack` the DrilldownPanel
parents to) so the draft edit surface takes up almost the entire Alma
Insights window. It hosts its OWN RichTextEditor + markdown-source pair
(NOT a reparent of the inline editor, which would be fragile), bound to
the active draft's markdown.

Pure leaf UI: it owns no DB/store access. On collapse it emits
`committed(draft_id, markdown)`; the Workbench routes that back through
its existing `content_edited` → `update_draft_content` persistence path.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QStackedWidget,
    QVBoxLayout,
)

from src.ui.pages.enablement.rich_editor import RichTextEditor
from src.ui.theme import (
    ALMA_BG_ELEVATED, ALMA_BG_INSET, ALMA_BORDER, ALMA_CREAM, ALMA_GREEN_DARK,
    ALMA_TEXT_DARK, ALMA_TEXT_LIGHT, ALMA_TEXT_MID, ALMA_TEXT_ON_DARK,
    ALMA_WARNING,
)

_TEAL = "#0D7D72"


class ExpandOverlay(QFrame):
    """Distraction-free, near-fullscreen editor overlay over the content area."""

    committed = Signal(int, str, object)   # (draft_id, markdown, clean_html|None)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._draft_id = 0
        self._read_only = False
        self.setObjectName("ExpandOverlay")
        self.setStyleSheet(
            f"#ExpandOverlay{{background:{ALMA_CREAM}; border:none;}}")
        self._build()
        self.hide()
        if parent is not None:
            parent.installEventFilter(self)

    # ── layout ──────────────────────────────────────────────────────
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(48, 28, 48, 32)
        outer.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(10)
        self._title = QLabel("")
        self._title.setStyleSheet(
            f"color:{ALMA_TEXT_DARK}; font-size:20px; font-weight:700; border:none;")
        head.addWidget(self._title)
        self._ro_hint = QLabel("read-only (published)")
        self._ro_hint.setStyleSheet(
            f"color:{ALMA_WARNING}; font-size:11px; font-weight:600; border:none;")
        self._ro_hint.hide()
        head.addWidget(self._ro_hint)
        head.addStretch(1)

        self._rich_btn = self._toggle_btn("Rich text")
        self._rich_btn.clicked.connect(lambda: self._set_mode("rich"))
        head.addWidget(self._rich_btn)
        self._md_btn = self._toggle_btn("Markdown")
        self._md_btn.clicked.connect(lambda: self._set_mode("markdown"))
        head.addWidget(self._md_btn)

        collapse = QPushButton("⤡  Collapse")
        collapse.setCursor(Qt.PointingHandCursor)
        collapse.setStyleSheet(
            f"QPushButton{{background:{ALMA_GREEN_DARK}; color:{ALMA_TEXT_ON_DARK}; "
            f"border:none; border-radius:8px; padding:7px 16px; font-size:12px; "
            f"font-weight:600;}}")
        collapse.clicked.connect(self._collapse)
        head.addWidget(collapse)
        outer.addLayout(head)

        self._rich = RichTextEditor()
        self._source = QPlainTextEdit()
        self._source.setStyleSheet(
            f"QPlainTextEdit{{background:{ALMA_BG_INSET}; border:1px solid {ALMA_BORDER}; "
            f"border-radius:8px; color:{ALMA_TEXT_DARK}; "
            f"font-family:Consolas,monospace; font-size:13px; padding:10px;}}")
        self._stack = QStackedWidget()
        self._stack.addWidget(self._rich)     # 0 rich
        self._stack.addWidget(self._source)   # 1 markdown source
        outer.addWidget(self._stack, 1)

    def _toggle_btn(self, text: str) -> QPushButton:
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        self._style_toggle(b, active=False)
        return b

    def _style_toggle(self, b: QPushButton, *, active: bool):
        bg = "#DCEFEC" if active else ALMA_BG_ELEVATED
        fg = _TEAL if active else ALMA_TEXT_MID
        border = _TEAL if active else ALMA_BORDER
        b.setStyleSheet(
            f"QPushButton{{background:{bg}; color:{fg}; border:1px solid {border}; "
            f"border-radius:7px; padding:5px 13px; font-size:11px; font-weight:600;}}")

    # ── data in / mode ──────────────────────────────────────────────
    def load(self, md: str, draft_id: int, title: str, *, read_only: bool = False):
        self._draft_id = int(draft_id)
        self._read_only = bool(read_only)
        self._title.setText(title or "Draft")
        self._ro_hint.setVisible(self._read_only)
        self._rich.set_markdown(md or "")
        self._source.setPlainText(md or "")
        self._set_mode("rich")

    def _current_markdown(self) -> str:
        """Authoritative markdown using the same precedence as the inline
        editor: the markdown source wins verbatim when it's the active tab,
        else serialize the rich editor."""
        if self._stack.currentWidget() is self._source:
            return self._source.toPlainText()
        return self._rich.to_markdown()

    def _current_html(self):
        """Cleaned rich HTML when the rich tab is active (carries color /
        highlight for Guru publish); None when editing markdown source, so
        publish derives HTML from the markdown."""
        if self._stack.currentWidget() is self._source:
            return None
        return self._rich.to_clean_html()

    def _set_mode(self, mode: str):
        # Commit across the pair before switching, mirroring the inline editor.
        if mode == "markdown":
            self._source.setPlainText(self._rich.to_markdown())
            self._stack.setCurrentWidget(self._source)
        else:
            self._rich.set_markdown(self._source.toPlainText())
            self._stack.setCurrentWidget(self._rich)
        self._style_toggle(self._rich_btn, active=mode == "rich")
        self._style_toggle(self._md_btn, active=mode == "markdown")

    # ── present / dismiss ───────────────────────────────────────────
    def present(self):
        """Fill the parent, bring forward, focus. Falls back to no-op if
        somehow unparented."""
        host = self.parentWidget()
        if host is None:
            self.show()
            return
        self.setGeometry(0, 0, host.width(), host.height())
        self.show()
        self.raise_()        # above the WorkbenchPage and any open drilldown
        self.setFocus()

    def _collapse(self):
        self.committed.emit(self._draft_id, self._current_markdown(), self._current_html())
        self.hide()

    # ── parent-resize tracking + Esc ────────────────────────────────
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
