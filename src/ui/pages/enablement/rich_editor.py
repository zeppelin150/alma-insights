"""Google-Docs-style rich-text editor for card drafts.

A `QTextEdit` (WYSIWYG) plus a grouped formatting toolbar that mirrors the
Guru card editor's markdown-representable controls: bold / italic /
underline / strikethrough, H1–H3, bullet + numbered + checklist, inline
code / code block / blockquote / table / divider, link / image, clear.
Round-trips to the draft markdown via `QTextDocument.setMarkdown` /
`toMarkdown` (GitHub dialect).

Every control here was empirically verified to survive the Qt markdown
round-trip (setMarkdown ↔ toMarkdown) — strikethrough via FontStrikeOut,
inline code via a fixed-pitch font, task lists via `QTextBlockFormat.
setMarker(MarkerType.Unchecked)`, and blockquote/code-block/divider/image
via `insertHtml` (Qt re-emits valid GFM for each). No new dependency
(QtGui rich text ships in PySide6-Essentials).

Not representable in GitHub markdown — so deliberately NOT offered as
controls (they would silently vanish on save): text color, highlight,
paragraph alignment, callout banners, collapsible sections, embeds,
@mentions, card-to-card links. Underline is kept for in-session WYSIWYG
but is itself lossy on save (GFM has no underline token) — its tooltip
says so. Reaching true 1:1 with Guru needs an HTML/block-JSON sidecar
(future path); the markdown body stays canonical.
"""

from __future__ import annotations

import html as _html

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QTextBlockFormat, QTextCharFormat, QTextCursor, QTextListFormat,
)
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QInputDialog, QPushButton, QTextEdit, QVBoxLayout,
    QWidget,
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

        # Grouped toolbar: (label, tooltip, handler, bold-label?)
        groups = [
            [
                ("B", "Bold", self._bold, True),
                ("I", "Italic", self._italic, False),
                ("U", "Underline (rich — published to Guru, dropped in markdown source)",
                 self._underline, False),
                ("S", "Strikethrough", self._strike, False),
                ("Color", "Text color (rich — published to Guru as HTML)",
                 self._text_color, False),
                ("Highlight", "Highlight (rich — published to Guru as HTML)",
                 self._highlight, False),
            ],
            [
                ("H1", "Heading 1", lambda: self._heading(1), False),
                ("H2", "Heading 2", lambda: self._heading(2), False),
                ("H3", "Heading 3", lambda: self._heading(3), False),
            ],
            [
                ("•", "Bullet list", lambda: self._list(QTextListFormat.ListDisc), False),
                ("1.", "Numbered list", lambda: self._list(QTextListFormat.ListDecimal), False),
                ("Todo", "Checklist", self._task_list, False),
            ],
            [
                ("`", "Inline code", self._inline_code, False),
                ("Code", "Code block", self._code_block, False),
                ("Quote", "Blockquote", self._blockquote, False),
                ("Table", "Insert table", self._table, False),
                ("HR", "Divider", self._hr, False),
            ],
            [
                ("Link", "Insert link", self._link, False),
                ("Image", "Insert image", self._image, False),
            ],
            [
                ("Clear", "Clear formatting", self._clear, False),
            ],
        ]

        bar = QHBoxLayout()
        bar.setSpacing(4)
        for gi, group in enumerate(groups):
            if gi:
                bar.addWidget(self._separator())
            for label, tip, handler, bold in group:
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

    def _separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        line.setFixedHeight(20)
        line.setStyleSheet(f"color:{ALMA_BORDER}; background:{ALMA_BORDER}; max-width:1px;")
        return line

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

    def _strike(self):
        cursor = self.editor.textCursor()
        struck = cursor.charFormat().fontStrikeOut()
        fmt = QTextCharFormat()
        fmt.setFontStrikeOut(not struck)
        self._merge_char(fmt)

    def _inline_code(self):
        # Qt has no inline-code flag; a fixed-pitch font makes toMarkdown
        # emit backticks. Round-trips, but the "code" semantic is font-driven.
        fmt = QTextCharFormat()
        fmt.setFontFixedPitch(True)
        fmt.setFontFamilies(["monospace"])
        self._merge_char(fmt)

    def _text_color(self):
        # Rich-only: color has no markdown token, but Guru stores text color
        # as inline style HTML, so it survives via the cleaned-HTML payload.
        from PySide6.QtWidgets import QColorDialog
        col = QColorDialog.getColor(parent=self, title="Text color")
        if col.isValid():
            fmt = QTextCharFormat()
            fmt.setForeground(col)
            self._merge_char(fmt)

    def _highlight(self):
        from PySide6.QtWidgets import QColorDialog
        col = QColorDialog.getColor(parent=self, title="Highlight color")
        if col.isValid():
            fmt = QTextCharFormat()
            fmt.setBackground(col)
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

    def _task_list(self):
        # A bulleted list whose blocks carry an unchecked marker → "- [ ]".
        cursor = self.editor.textCursor()
        cursor.beginEditBlock()
        if cursor.currentList() is None:
            lst = QTextListFormat()
            lst.setStyle(QTextListFormat.ListDisc)
            cursor.createList(lst)
        bf = QTextBlockFormat()
        bf.setMarker(QTextBlockFormat.MarkerType.Unchecked)
        cursor.mergeBlockFormat(bf)
        cursor.endEditBlock()

    def _blockquote(self):
        cursor = self.editor.textCursor()
        sel = cursor.selection().toPlainText().strip() or "Quoted text"
        cursor.insertHtml(f"<blockquote>{_html.escape(sel)}</blockquote>")

    def _code_block(self):
        cursor = self.editor.textCursor()
        sel = cursor.selection().toPlainText().strip() or "code"
        cursor.insertHtml(f"<pre>{_html.escape(sel)}</pre>")

    def _table(self):
        rows, ok = QInputDialog.getInt(self, "Insert table", "Rows:", 2, 1, 50)
        if not ok:
            return
        cols, ok = QInputDialog.getInt(self, "Insert table", "Columns:", 2, 1, 20)
        if not ok:
            return
        table = self.editor.textCursor().insertTable(rows, cols)
        # Seed the header row: an all-empty Qt table serializes to "|||" with
        # no delimiter row, which is NOT valid GFM and won't render. Header
        # text makes toMarkdown emit a proper "|-|-|" so it round-trips.
        for c in range(cols):
            cell = table.cellAt(0, c).firstCursorPosition()
            cell.insertText(f"Column {c + 1}")

    def _hr(self):
        self.editor.textCursor().insertHtml("<hr/>")

    def _image(self):
        url, ok = QInputDialog.getText(self, "Insert image", "Image URL:")
        if not ok or not url.strip():
            return
        alt, ok = QInputDialog.getText(self, "Insert image", "Alt text (optional):")
        alt = alt.strip() if ok else ""
        cursor = self.editor.textCursor()
        cursor.insertHtml(
            f'<img src="{_html.escape(url.strip(), quote=True)}" '
            f'alt="{_html.escape(alt, quote=True)}">')

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
        block.setMarker(QTextBlockFormat.MarkerType.NoMarker)
        cursor.mergeBlockFormat(block)

    # ── markdown round-trip ─────────────────────────────────────────

    def set_markdown(self, md: str):
        self.editor.setMarkdown(md or "")

    def to_markdown(self) -> str:
        from PySide6.QtGui import QTextDocument
        return self.editor.document().toMarkdown(
            QTextDocument.MarkdownFeature.MarkdownDialectGitHub).strip()

    def to_clean_html(self) -> str:
        """Portable HTML for the Guru publish payload — carries color /
        highlight / underline that markdown cannot represent. Guru's
        `content` field is HTML, so this is the high-fidelity submit form."""
        from src.data.html_markdown import qt_html_to_clean_html
        return qt_html_to_clean_html(self.editor.document().toHtml())
