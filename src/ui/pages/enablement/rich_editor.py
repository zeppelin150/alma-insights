"""Google-Docs-style rich-text editor for card drafts.

A `QTextEdit` (WYSIWYG) plus a compact formatting toolbar (bold / italic /
underline / H1–H3 / bullet + numbered lists / link / clear). Round-trips to
the draft markdown via `QTextDocument.setMarkdown` / `toMarkdown` — headings
go through `QTextBlockFormat.setHeadingLevel()` so they survive as `#`
markdown, and bold/italic/lists/links map cleanly. No new dependency
(QtGui rich text ships in PySide6-Essentials).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QTextBlockFormat, QTextCharFormat, QTextCursor, QTextListFormat,
)
from PySide6.QtWidgets import (
    QHBoxLayout, QInputDialog, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from src.ui.theme import (
    ALMA_BG_ELEVATED, ALMA_BG_INSET, ALMA_BORDER, ALMA_BORDER_LIGHT,
    ALMA_GREEN_LIGHT, ALMA_TEXT_DARK, ALMA_TEXT_MID,
)

_TEAL = "#0D7D72"


class RichTextEditor(QWidget):
    """WYSIWYG editor that round-trips to markdown."""

    content_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(4)
        # (label, tooltip, handler, bold-label?)
        specs = [
            ("B", "Bold", self._bold, True),
            ("I", "Italic", self._italic, False),
            ("U", "Underline", self._underline, False),
            ("H1", "Heading 1", lambda: self._heading(1), False),
            ("H2", "Heading 2", lambda: self._heading(2), False),
            ("H3", "Heading 3", lambda: self._heading(3), False),
            ("•", "Bullet list", lambda: self._list(QTextListFormat.ListDisc), False),
            ("1.", "Numbered list", lambda: self._list(QTextListFormat.ListDecimal), False),
            ("Link", "Insert link", self._link, False),
            ("Clear", "Clear formatting", self._clear, False),
        ]
        for label, tip, handler, bold in specs:
            bar.addWidget(self._tool_btn(label, tip, handler, bold))
        bar.addStretch(1)
        outer.addLayout(bar)

        self.editor = QTextEdit()
        self.editor.setAcceptRichText(True)
        self.editor.setStyleSheet(
            f"QTextEdit{{background:{ALMA_BG_ELEVATED}; border:1px solid {ALMA_BORDER_LIGHT}; "
            f"border-radius:8px; color:{ALMA_TEXT_DARK}; font-size:13.5px; padding:10px;}}"
        )
        self.editor.textChanged.connect(self.content_changed.emit)
        outer.addWidget(self.editor, 1)

    def _tool_btn(self, label, tip, handler, bold) -> QPushButton:
        b = QPushButton(label)
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedHeight(28)
        weight = "700" if bold else "600"
        b.setStyleSheet(
            f"QPushButton{{background:{ALMA_BG_INSET}; color:{ALMA_TEXT_MID}; "
            f"border:1px solid {ALMA_BORDER}; border-radius:6px; padding:2px 9px; "
            f"font-size:12px; font-weight:{weight};}} "
            f"QPushButton:hover{{border-color:{_TEAL}; color:{_TEAL};}}"
        )
        b.clicked.connect(handler)
        return b

    # ── formatting actions ──────────────────────────────────────────

    def _merge_char(self, fmt: QTextCharFormat):
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.WordUnderCursor)
        cursor.mergeCharFormat(fmt)
        self.editor.mergeCurrentCharFormat(fmt)

    def _bold(self):
        from PySide6.QtGui import QFont
        fmt = QTextCharFormat()
        make_bold = self.editor.fontWeight() < QFont.Weight.Bold
        fmt.setFontWeight(QFont.Weight.Bold if make_bold else QFont.Weight.Normal)
        self._merge_char(fmt)

    def _italic(self):
        fmt = QTextCharFormat()
        fmt.setFontItalic(not self.editor.fontItalic())
        self._merge_char(fmt)

    def _underline(self):
        fmt = QTextCharFormat()
        fmt.setFontUnderline(not self.editor.fontUnderline())
        self._merge_char(fmt)

    def _heading(self, level: int):
        cursor = self.editor.textCursor()
        cursor.beginEditBlock()
        block = QTextBlockFormat()
        block.setHeadingLevel(level)
        cursor.mergeBlockFormat(block)
        # Heading text scales for visual feedback; toMarkdown uses the level.
        char = QTextCharFormat()
        char.setProperty(QTextCharFormat.FontSizeAdjustment, 4 - level)
        from PySide6.QtGui import QFont
        char.setFontWeight(QFont.Bold)
        cursor.select(QTextCursor.BlockUnderCursor)
        cursor.mergeCharFormat(char)
        cursor.endEditBlock()

    def _list(self, style):
        cursor = self.editor.textCursor()
        fmt = QTextListFormat()
        fmt.setStyle(style)
        cursor.createList(fmt)

    def _link(self):
        url, ok = QInputDialog.getText(self, "Insert link", "URL:")
        if not ok or not url.strip():
            return
        cursor = self.editor.textCursor()
        text = cursor.selectedText() or url.strip()
        fmt = QTextCharFormat()
        fmt.setAnchor(True)
        fmt.setAnchorHref(url.strip())
        fmt.setForeground(Qt.blue)
        fmt.setFontUnderline(True)
        cursor.insertText(text, fmt)

    def _clear(self):
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.BlockUnderCursor)
        cursor.setCharFormat(QTextCharFormat())
        block = QTextBlockFormat()
        block.setHeadingLevel(0)
        cursor.mergeBlockFormat(block)

    # ── markdown round-trip ─────────────────────────────────────────

    def set_markdown(self, md: str):
        self.editor.setMarkdown(md or "")

    def to_markdown(self) -> str:
        from PySide6.QtGui import QTextDocument
        return self.editor.document().toMarkdown(
            QTextDocument.MarkdownFeature.MarkdownDialectGitHub).strip()
