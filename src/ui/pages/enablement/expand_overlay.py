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
    QTextBrowser, QVBoxLayout,
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
        self._md = ""          # authoritative markdown across mode switches
        self._html = None      # cleaned rich HTML (when last edited in rich)
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

        self._preview_btn = self._toggle_btn("Guru preview")
        self._preview_btn.clicked.connect(lambda: self._set_mode("preview"))
        head.addWidget(self._preview_btn)
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

        self._preview = QTextBrowser()
        self._preview.setOpenExternalLinks(False)
        self._preview.setStyleSheet(
            f"QTextBrowser{{background:{ALMA_BG_ELEVATED}; border:1px solid {ALMA_BORDER}; "
            f"border-radius:8px; color:{ALMA_TEXT_DARK}; font-size:14px; padding:12px 16px;}}")
        self._rich = RichTextEditor()
        self._source = QPlainTextEdit()
        self._source.setStyleSheet(
            f"QPlainTextEdit{{background:{ALMA_BG_INSET}; border:1px solid {ALMA_BORDER}; "
            f"border-radius:8px; color:{ALMA_TEXT_DARK}; "
            f"font-family:Consolas,monospace; font-size:13px; padding:10px;}}")
        self._stack = QStackedWidget()
        self._stack.addWidget(self._preview)   # 0 Guru preview (faithful render)
        self._stack.addWidget(self._rich)      # 1 rich text
        self._stack.addWidget(self._source)    # 2 markdown source
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
    def load(self, md: str, draft_id: int, title: str, *, read_only: bool = False,
             content_html: str | None = None):
        self._draft_id = int(draft_id)
        self._read_only = bool(read_only)
        self._md = md or ""
        # The draft's stored/captured HTML — what a push would actually send
        # when it is present. Carried so the overlay preview renders the
        # publish body, not a markdown-only re-derivation.
        self._html = content_html
        self._title.setText(title or "Draft")
        self._ro_hint.setVisible(self._read_only)
        # Default to the faithful Guru preview — same render as the inline card,
        # so expanding doesn't change the formatting.
        self._set_mode("preview")

    def _capture(self):
        """Pull the editing pane being left back into the authoritative md/html."""
        cur = self._stack.currentWidget()
        if cur is self._rich:
            self._md = self._rich.to_markdown()
            self._html = self._rich.to_clean_html()
        elif cur is self._source:
            self._md = self._source.toPlainText()
            self._html = None      # markdown-only → publish re-derives HTML

    def _current_markdown(self) -> str:
        self._capture()
        return self._md

    def _current_html(self):
        self._capture()
        return self._html

    def _render_preview(self):
        """Same renderer as the inline canvas — the PUBLISH BODY (markdown +
        any captured/stored HTML), so expanding shows what a push sends."""
        try:
            from src.ui.pages.enablement.guru_preview import render_preview
            render_preview(self._preview, self._md, self._html)
        except Exception:
            self._preview.setMarkdown(self._md)

    def _set_mode(self, mode: str):
        self._capture()                 # commit the pane we're leaving
        if mode == "markdown":
            self._source.setPlainText(self._md)
            self._stack.setCurrentWidget(self._source)
        elif mode == "rich":
            self._rich.set_markdown(self._md)
            self._stack.setCurrentWidget(self._rich)
        else:
            self._render_preview()
            self._stack.setCurrentWidget(self._preview)
        self._style_toggle(self._preview_btn, active=mode == "preview")
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
