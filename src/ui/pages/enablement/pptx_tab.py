"""PowerPoint tab — model a deck, preview the slides, and export a .pptx.

Workbench-grade: a live Slides preview / Outline-edit toggle, an
Expand/focus overlay, drag-and-drop a document to model a deck from it, and
real .pptx export. A pure view: the host (EnablementPage) feeds decks via
`set_decks` and handles the signals against the demo/live connection.

The outline editor is plain text — `# Slide title` lines and `- bullet`
lines — round-tripping to {title, slides:[{title, bullets[]}]}.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout,
    QWidget,
)

from src.ui.pages.enablement._common import card_frame, section_label
from src.ui.pages.enablement.slide_view import render_slides
from src.ui.theme import (
    ALMA_BG_ELEVATED, ALMA_BG_INSET, ALMA_BORDER, ALMA_CREAM, ALMA_GREEN_DARK,
    ALMA_GREEN_LIGHT, ALMA_TEXT_DARK, ALMA_TEXT_LIGHT, ALMA_TEXT_MID,
    ALMA_TEXT_ON_DARK,
)

_TEAL = "#0D7D72"


def outline_to_text(outline: dict) -> str:
    lines = []
    for s in (outline or {}).get("slides", []):
        lines.append(f"# {s.get('title', '')}")
        for b in s.get("bullets", []):
            lines.append(f"- {b}")
        lines.append("")
    return "\n".join(lines).strip()


def text_to_outline(title: str, text: str) -> dict:
    slides = []
    cur = None
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if line.startswith("# "):
            cur = {"title": line[2:].strip(), "bullets": []}
            slides.append(cur)
        elif line.startswith("- "):
            if cur is None:
                cur = {"title": "Slide", "bullets": []}
                slides.append(cur)
            cur["bullets"].append(line[2:].strip())
    return {"title": title.strip() or "Untitled deck", "slides": slides}


class PptxPage(QWidget):
    model_topic_requested = Signal(str)
    deck_selected = Signal(int)
    outline_saved = Signal(int, str, dict)
    export_requested = Signal(int)
    doc_dropped = Signal(str)             # a doc dropped → model a deck from it

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{ALMA_CREAM};")
        self.setAcceptDrops(True)
        self._active_deck_id = None
        self._overlay_host = None
        self._overlay = None
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 18)
        outer.setSpacing(12)

        head = QHBoxLayout()
        head.addWidget(section_label("POWERPOINT DECKS"))
        head.addStretch(1)
        self._topic = QLineEdit()
        self._topic.setPlaceholderText("Model a deck from a topic…")
        self._topic.setFixedHeight(30)
        self._topic.setStyleSheet(
            f"QLineEdit{{background:{ALMA_BG_ELEVATED}; border:1px solid {ALMA_BORDER}; "
            f"border-radius:7px; padding:2px 10px; font-size:12px; color:{ALMA_TEXT_DARK}; "
            f"min-width:240px;}}")
        self._topic.returnPressed.connect(self._on_model_topic)
        head.addWidget(self._topic)
        model_btn = self._primary("Model deck")
        model_btn.clicked.connect(self._on_model_topic)
        head.addWidget(model_btn)
        outer.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(6)
        left.addWidget(section_label("DECKS"))
        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget{{background:{ALMA_BG_ELEVATED}; border:1px solid {ALMA_BORDER}; "
            f"border-radius:8px; font-size:12.5px; color:{ALMA_TEXT_DARK};}}")
        self._list.itemClicked.connect(self._on_pick)
        left.addWidget(self._list, 1)
        hint = QLabel("Tip: drag a doc here to model a deck from it.")
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"color:{ALMA_TEXT_LIGHT}; font-size:11px; border:none; background:transparent;")
        left.addWidget(hint)
        body.addLayout(left, 2)

        right = QVBoxLayout()
        right.setSpacing(6)
        trow = QHBoxLayout()
        trow.addWidget(section_label("DECK"))
        trow.addStretch(1)
        self._slides_btn = self._toggle("Slides")
        self._slides_btn.clicked.connect(lambda: self._set_view("slides"))
        trow.addWidget(self._slides_btn)
        self._outline_btn = self._toggle("Outline")
        self._outline_btn.clicked.connect(lambda: self._set_view("outline"))
        trow.addWidget(self._outline_btn)
        self._expand_btn = self._toggle("⤢  Expand")
        self._expand_btn.clicked.connect(self._open_expand)
        trow.addWidget(self._expand_btn)
        right.addLayout(trow)

        edit_card = card_frame()
        ev = QVBoxLayout(edit_card)
        ev.setContentsMargins(14, 12, 14, 12)
        ev.setSpacing(8)
        self._title = QLineEdit()
        self._title.setPlaceholderText("Deck title")
        self._title.setStyleSheet(
            f"QLineEdit{{background:{ALMA_BG_ELEVATED}; border:1px solid {ALMA_BORDER}; "
            f"border-radius:7px; padding:6px 10px; font-size:13px; font-weight:600; "
            f"color:{ALMA_TEXT_DARK};}}")
        ev.addWidget(self._title)

        # slides preview (scrollable) ↔ outline editor
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet("QScrollArea{border:none; background:transparent;}")
        holder = QWidget()
        holder.setStyleSheet("background:transparent;")
        self._slides_layout = QVBoxLayout(holder)
        self._slides_layout.setContentsMargins(0, 0, 6, 0)
        self._slides_layout.setSpacing(10)
        self._scroll.setWidget(holder)

        self._outline = QPlainTextEdit()
        self._outline.setStyleSheet(
            f"QPlainTextEdit{{background:{ALMA_BG_INSET}; border:1px solid {ALMA_BORDER}; "
            f"border-radius:8px; color:{ALMA_TEXT_DARK}; font-size:12.5px; padding:8px;}}")
        self._stack = QStackedWidget()
        self._stack.addWidget(self._scroll)    # 0 slides preview
        self._stack.addWidget(self._outline)   # 1 outline edit
        ev.addWidget(self._stack, 1)

        actions = QHBoxLayout()
        self._status = QLabel("")
        self._status.setStyleSheet(
            f"color:{ALMA_TEXT_LIGHT}; font-size:11px; border:none; background:transparent;")
        actions.addWidget(self._status)
        actions.addStretch(1)
        self._save_btn = self._secondary("Save outline")
        self._save_btn.clicked.connect(self._on_save)
        actions.addWidget(self._save_btn)
        self._export_btn = self._primary("Export .pptx")
        self._export_btn.clicked.connect(self._on_export)
        actions.addWidget(self._export_btn)
        ev.addLayout(actions)
        right.addWidget(edit_card, 1)
        body.addLayout(right, 3)

        outer.addLayout(body, 1)
        self._set_editor_enabled(False)
        self._set_view("slides")

    # ── buttons ─────────────────────────────────────────────────────
    def _primary(self, text) -> QPushButton:
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(
            f"QPushButton{{background:{ALMA_GREEN_DARK}; color:{ALMA_TEXT_ON_DARK}; border:none; "
            f"border-radius:8px; padding:7px 14px; font-size:12px; font-weight:600;}} "
            f"QPushButton:disabled{{background:{ALMA_BORDER}; color:{ALMA_TEXT_LIGHT};}}")
        return b

    def _secondary(self, text) -> QPushButton:
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(
            f"QPushButton{{background:{ALMA_BG_ELEVATED}; color:{ALMA_GREEN_DARK}; "
            f"border:1px solid {ALMA_BORDER}; border-radius:8px; padding:7px 14px; "
            f"font-size:12px; font-weight:600;}} QPushButton:disabled{{color:{ALMA_TEXT_LIGHT};}}")
        return b

    def _toggle(self, text) -> QPushButton:
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        self._style_toggle(b, active=False)
        return b

    def _style_toggle(self, b, *, active: bool):
        bg = "#DCEFEC" if active else ALMA_BG_ELEVATED
        fg = _TEAL if active else ALMA_TEXT_MID
        border = _TEAL if active else ALMA_BORDER
        b.setStyleSheet(
            f"QPushButton{{background:{bg}; color:{fg}; border:1px solid {border}; "
            f"border-radius:7px; padding:4px 12px; font-size:11px; font-weight:600;}}")

    def _set_editor_enabled(self, on: bool):
        for w in (self._title, self._outline, self._save_btn, self._export_btn,
                  self._slides_btn, self._outline_btn, self._expand_btn):
            w.setEnabled(on)

    # ── view toggle ─────────────────────────────────────────────────
    def _current_outline(self) -> dict:
        return text_to_outline(self._title.text(), self._outline.toPlainText())

    def _set_view(self, mode: str):
        if mode == "outline":
            self._stack.setCurrentWidget(self._outline)
        else:
            render_slides(self._slides_layout, self._current_outline())
            self._stack.setCurrentWidget(self._scroll)
        self._style_toggle(self._slides_btn, active=mode == "slides")
        self._style_toggle(self._outline_btn, active=mode == "outline")

    # ── data in ─────────────────────────────────────────────────────
    def set_decks(self, decks: list[dict]):
        self._list.clear()
        for d in decks:
            item = QListWidgetItem(f"{d.get('title', 'Untitled')}  ·  "
                                   f"{d.get('slide_count', 0)} slides")
            item.setData(Qt.UserRole, d.get("id"))
            self._list.addItem(item)
        if not decks:
            self._status.setText("No decks yet — model one from a topic or a doc.")

    def show_deck(self, deck: dict):
        self._active_deck_id = deck.get("id")
        self._title.setText(deck.get("title", ""))
        self._outline.setPlainText(outline_to_text(deck.get("outline", {})))
        self._set_editor_enabled(True)
        self._set_view("slides")
        self._status.setText("Exported." if deck.get("status") == "exported" else "Draft.")

    def set_status(self, text: str):
        self._status.setText(text)

    def set_overlay_host(self, widget):
        self._overlay_host = widget

    # ── handlers ────────────────────────────────────────────────────
    def _on_model_topic(self):
        topic = self._topic.text().strip()
        if topic:
            self.model_topic_requested.emit(topic)
            self._topic.clear()

    def _on_pick(self, item):
        deck_id = item.data(Qt.UserRole)
        if deck_id is not None:
            self.deck_selected.emit(int(deck_id))

    def _on_save(self):
        if self._active_deck_id is None:
            return
        outline = self._current_outline()
        self.outline_saved.emit(int(self._active_deck_id), outline["title"], outline)

    def _on_export(self):
        if self._active_deck_id is not None:
            self.export_requested.emit(int(self._active_deck_id))

    def _open_expand(self):
        if self._active_deck_id is None:
            return
        if self._overlay is None:
            from src.ui.pages.enablement.pptx_expand import PptxExpandOverlay
            self._overlay = PptxExpandOverlay(self._overlay_host or self.window())
            self._overlay.committed.connect(self._on_expand_committed)
        self._overlay.load({"id": self._active_deck_id, "title": self._title.text(),
                            "outline": self._current_outline()})
        self._overlay.present()

    def _on_expand_committed(self, deck_id: int, title: str, outline: dict):
        self._title.setText(title)
        self._outline.setPlainText(outline_to_text(outline))
        self._set_view("slides")
        self.outline_saved.emit(int(deck_id), title, outline)

    # ── drag & drop a document to model a deck ──────────────────────
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        if urls:
            e.acceptProposedAction()
            self.doc_dropped.emit(urls[0].toLocalFile())
