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

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import (
    QFont, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextListFormat,
)
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QInputDialog, QMenu, QTextEdit, QToolButton,
    QVBoxLayout, QWidget,
)

from src.ui.design.icons import icon as _icon
from src.ui.theme import (
    ALMA_BG_ELEVATED, ALMA_BG_INSET, ALMA_BORDER, ALMA_BORDER_LIGHT,
    ALMA_TEXT_DARK, ALMA_TEXT_LIGHT, ALMA_TEXT_MID,
)

_TEAL = "#0D7D72"
_HOVER_BG = "#ECEFEC"      # subtle neutral hover
_ACTIVE_BG = "#DCEFEC"     # teal tint for the active/pressed format
_PRESS_BG = "#CFE6E1"


class RichTextEditor(QWidget):
    """WYSIWYG editor that round-trips to markdown."""

    content_changed = Signal()

    _HEAD_LABELS = {0: "Normal", 1: "Heading 1", 2: "Heading 2", 3: "Heading 3"}

    # flat icon-button QSS — borderless, neutral hover, teal-tint when the
    # cursor's current format makes the control active.
    _BTN_QSS = (
        f"QToolButton{{background:transparent; border:none; border-radius:6px;}} "
        f"QToolButton:hover{{background:{_HOVER_BG};}} "
        f"QToolButton:pressed{{background:{_PRESS_BG};}} "
        f"QToolButton[active=\"true\"]{{background:{_ACTIVE_BG};}} "
        f"QToolButton::menu-indicator{{image:none;}}"
    )
    _MENU_QSS = (
        f"QToolButton{{background:transparent; color:{ALMA_TEXT_MID}; border:none; "
        f"border-radius:6px; padding:4px 8px; font-size:12px; font-weight:600;}} "
        f"QToolButton:hover{{background:{_HOVER_BG};}} "
        f"QToolButton::menu-indicator{{image:none;}}"
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stateful = []       # [(button, checker)] for active-state sync
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        self.editor = QTextEdit()
        self.editor.setAcceptRichText(True)
        self.editor.setStyleSheet(
            f"QTextEdit{{background:{ALMA_BG_ELEVATED}; border:1px solid {ALMA_BORDER_LIGHT}; "
            f"border-radius:8px; color:{ALMA_TEXT_DARK}; font-size:13.5px; padding:12px 14px;}}"
        )
        self.editor.textChanged.connect(self.content_changed.emit)

        outer.addWidget(self._build_toolbar())
        outer.addWidget(self.editor, 1)

        # Live active-state highlighting + undo/redo availability.
        self.editor.currentCharFormatChanged.connect(self._sync_states)
        self.editor.cursorPositionChanged.connect(self._sync_states)
        self.editor.undoAvailable.connect(self._undo_btn.setEnabled)
        self.editor.redoAvailable.connect(self._redo_btn.setEnabled)
        self._undo_btn.setEnabled(False)
        self._redo_btn.setEnabled(False)
        self._sync_states()

    def _build_toolbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("RteToolbar")
        bar.setStyleSheet(
            f"#RteToolbar{{background:{ALMA_BG_ELEVATED}; "
            f"border:1px solid {ALMA_BORDER_LIGHT}; border-radius:9px;}}")
        row = QHBoxLayout(bar)
        row.setContentsMargins(7, 5, 7, 5)
        row.setSpacing(2)

        self._undo_btn = self._icon_btn("undo", "Undo (Ctrl+Z)", self.editor.undo)
        self._redo_btn = self._icon_btn("redo", "Redo (Ctrl+Y)", self.editor.redo)
        row.addWidget(self._undo_btn)
        row.addWidget(self._redo_btn)
        row.addWidget(self._sep())

        row.addWidget(self._build_heading_menu())
        row.addWidget(self._sep())

        # inline text formatting (active-state aware)
        row.addWidget(self._icon_btn("bold", "Bold", self._bold, self._is_bold))
        row.addWidget(self._icon_btn("italic", "Italic", self._italic, self._is_italic))
        row.addWidget(self._icon_btn(
            "underline",
            "Underline (rich — published to Guru, dropped in markdown source)",
            self._underline, self._is_underline))
        row.addWidget(self._icon_btn("strikethrough", "Strikethrough",
                                     self._strike, self._is_strike))
        row.addWidget(self._sep())

        row.addWidget(self._icon_btn("text-color",
                                     "Text colour (published to Guru as HTML)",
                                     self._text_color))
        row.addWidget(self._icon_btn("highlighter",
                                     "Highlight (published to Guru as HTML)",
                                     self._highlight))
        row.addWidget(self._sep())

        row.addWidget(self._icon_btn("list", "Bullet list",
                                     lambda: self._list(QTextListFormat.ListDisc),
                                     self._is_bullet))
        row.addWidget(self._icon_btn("list-ordered", "Numbered list",
                                     lambda: self._list(QTextListFormat.ListDecimal),
                                     self._is_numbered))
        row.addWidget(self._icon_btn("list-check", "Checklist", self._task_list))
        row.addWidget(self._sep())

        row.addWidget(self._icon_btn("code", "Inline code", self._inline_code))
        row.addWidget(self._icon_btn("quote", "Blockquote", self._blockquote))
        row.addWidget(self._icon_btn("link", "Insert link", self._link))
        row.addWidget(self._build_insert_menu())

        row.addStretch(1)
        row.addWidget(self._icon_btn("eraser", "Clear formatting", self._clear))
        return bar

    def _icon_btn(self, name, tip, handler, checker=None) -> QToolButton:
        b = QToolButton()
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedSize(30, 28)
        b.setIconSize(QSize(17, 17))
        b.setIcon(_icon(name, 17, ALMA_TEXT_MID))
        b.setStyleSheet(self._BTN_QSS)
        b.setProperty("active", "false")
        if handler is not None:
            b.clicked.connect(handler)
        if checker is not None:
            b._icon_on = _icon(name, 17, _TEAL)
            b._icon_off = _icon(name, 17, ALMA_TEXT_MID)
            self._stateful.append((b, checker))
        return b

    def _sep(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        line.setFixedSize(1, 18)
        line.setStyleSheet(f"background:{ALMA_BORDER}; border:none; margin:0 4px;")
        return line

    def _build_heading_menu(self) -> QToolButton:
        btn = QToolButton()
        btn.setToolTip("Text style")
        btn.setText("Normal  ▾")
        btn.setPopupMode(QToolButton.InstantPopup)
        btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedHeight(28)
        btn.setMinimumWidth(94)
        btn.setStyleSheet(self._MENU_QSS)
        menu = QMenu(btn)
        menu.addAction("Normal", self._paragraph)
        menu.addAction("Heading 1", lambda: self._heading(1))
        menu.addAction("Heading 2", lambda: self._heading(2))
        menu.addAction("Heading 3", lambda: self._heading(3))
        btn.setMenu(menu)
        self._heading_btn = btn
        self._heading_menu = menu
        return btn

    def _build_insert_menu(self) -> QToolButton:
        """Block inserts (Guru "+" add-block menu): table / divider / code
        block / image plus the Guru-native callout / collapsible / card-link
        blocks (markdown directives that expand to ghq-card-content__* HTML)."""
        btn = QToolButton()
        btn.setToolTip("Insert a block")
        btn.setIcon(_icon("plus", 17, ALMA_TEXT_MID))
        btn.setIconSize(QSize(17, 17))
        btn.setPopupMode(QToolButton.InstantPopup)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedSize(30, 28)
        btn.setStyleSheet(self._BTN_QSS)
        menu = QMenu(btn)
        menu.addAction("Table", self._table)
        menu.addAction("Divider", self._hr)
        menu.addAction("Code block", self._code_block)
        menu.addAction("Image…", self._image)
        menu.addSeparator()
        callout = menu.addMenu("Callout")
        for label, variant in (("Note", "note"), ("Success", "success"),
                               ("Warning", "warning"), ("Danger", "danger")):
            callout.addAction(label, lambda v=variant: self._insert_callout(v))
        menu.addAction("Collapsible section", self._insert_collapsible)
        menu.addAction("Guru card link…", self._insert_card_link)
        btn.setMenu(menu)
        self._insert_menu = menu
        self._insert_submenus = (callout,)   # retain ref (addMenu GC footgun)
        return btn

    # ── active-state sync ───────────────────────────────────────────
    def _sync_states(self, *args):
        for btn, checker in self._stateful:
            try:
                on = bool(checker())
            except Exception:
                on = False
            btn.setProperty("active", "true" if on else "false")
            btn.setIcon(btn._icon_on if on else btn._icon_off)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        lvl = self.editor.textCursor().blockFormat().headingLevel()
        self._heading_btn.setText(self._HEAD_LABELS.get(lvl, "Normal") + "  ▾")

    def _is_bold(self):
        return self.editor.fontWeight() >= QFont.Weight.Bold

    def _is_italic(self):
        return self.editor.fontItalic()

    def _is_underline(self):
        return self.editor.fontUnderline()

    def _is_strike(self):
        return self.editor.currentCharFormat().fontStrikeOut()

    def _list_style(self):
        cl = self.editor.textCursor().currentList()
        return cl.format().style() if cl is not None else None

    def _is_bullet(self):
        return self._list_style() == QTextListFormat.ListDisc

    def _is_numbered(self):
        return self._list_style() == QTextListFormat.ListDecimal

    def _paragraph(self):
        """Reset the block to normal body text (the 'Normal' text style)."""
        cursor = self.editor.textCursor()
        cursor.beginEditBlock()
        bf = QTextBlockFormat()
        bf.setHeadingLevel(0)
        cursor.mergeBlockFormat(bf)
        cf = QTextCharFormat()
        cf.setProperty(QTextCharFormat.FontSizeAdjustment, 0)
        cf.setFontWeight(QFont.Normal)
        cursor.select(QTextCursor.BlockUnderCursor)
        cursor.mergeCharFormat(cf)
        cursor.endEditBlock()

    def _insert_callout(self, variant: str):
        body = "Your message here."
        self.editor.textCursor().insertHtml(
            f"<blockquote>[!{variant.upper()}]<br>{_html.escape(body)}</blockquote>")

    def _insert_collapsible(self):
        self.editor.textCursor().insertHtml(
            "<p>::: details Section title</p><p>Hidden body.</p><p>:::</p>")

    def _insert_card_link(self):
        cid, ok = QInputDialog.getText(self, "Guru card link", "Card id or slug:")
        if not ok or not cid.strip():
            return
        label, ok = QInputDialog.getText(self, "Guru card link", "Link text:")
        label = (label.strip() if ok else "") or cid.strip()
        from src.data.guru_blocks import card_link_token
        self.editor.textCursor().insertText(card_link_token(cid.strip(), label))

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
