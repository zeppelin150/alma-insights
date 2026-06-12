"""PowerPoint tab — model a deck (editable outline) and export a real .pptx.

A pure view: the host (EnablementPage) feeds decks via `set_decks` and
handles `model_topic_requested` / `outline_saved` / `export_requested` /
`deck_selected` against the demo/live connection + LLM + file dialog
(same host-driven pattern as the Analytics and Workbench tabs).

The outline editor is plain text — `# Slide title` lines and `- bullet`
lines — which round-trips to the {title, slides:[{title,bullets[]}]}
outline. Intuitive to edit, trivial to parse.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from src.ui.pages.enablement._common import badge, card_frame, section_label
from src.ui.theme import (
    ALMA_BG_ELEVATED, ALMA_BG_INSET, ALMA_BORDER, ALMA_CREAM, ALMA_GREEN_DARK,
    ALMA_TEXT_DARK, ALMA_TEXT_LIGHT, ALMA_TEXT_MID, ALMA_TEXT_ON_DARK,
)


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
    model_topic_requested = Signal(str)   # topic text → host models a deck
    deck_selected = Signal(int)           # deck id
    outline_saved = Signal(int, str, dict)  # (deck_id, title, outline)
    export_requested = Signal(int)        # deck id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{ALMA_CREAM};")
        self._active_deck_id = None
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
            f"border-radius:7px; padding:2px 10px; font-size:12px; color:{ALMA_TEXT_DARK}; min-width:240px;}}"
        )
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
            f"border-radius:8px; font-size:12.5px; color:{ALMA_TEXT_DARK};}}"
        )
        self._list.itemClicked.connect(self._on_pick)
        left.addWidget(self._list, 1)
        body.addLayout(left, 2)

        right = QVBoxLayout()
        right.setSpacing(6)
        trow = QHBoxLayout()
        trow.addWidget(section_label("OUTLINE"))
        trow.addStretch(1)
        self._status = QLabel("")
        self._status.setStyleSheet(
            f"color:{ALMA_TEXT_LIGHT}; font-size:11px; border:none; background:transparent;")
        trow.addWidget(self._status)
        right.addLayout(trow)

        edit_card = card_frame()
        ev = QVBoxLayout(edit_card)
        ev.setContentsMargins(14, 12, 14, 12)
        ev.setSpacing(8)
        self._title = QLineEdit()
        self._title.setPlaceholderText("Deck title")
        self._title.setStyleSheet(
            f"QLineEdit{{background:{ALMA_BG_ELEVATED}; border:1px solid {ALMA_BORDER}; "
            f"border-radius:7px; padding:6px 10px; font-size:13px; font-weight:600; color:{ALMA_TEXT_DARK};}}"
        )
        ev.addWidget(self._title)
        hint = QLabel("Use “# Slide title” lines and “- bullet” lines.")
        hint.setStyleSheet(
            f"color:{ALMA_TEXT_LIGHT}; font-size:11px; border:none; background:transparent;")
        ev.addWidget(hint)
        self._outline = QPlainTextEdit()
        self._outline.setStyleSheet(
            f"QPlainTextEdit{{background:{ALMA_BG_INSET}; border:1px solid {ALMA_BORDER}; "
            f"border-radius:8px; color:{ALMA_TEXT_DARK}; font-size:12.5px; padding:8px;}}"
        )
        ev.addWidget(self._outline, 1)

        actions = QHBoxLayout()
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

    def _set_editor_enabled(self, on: bool):
        for w in (self._title, self._outline, self._save_btn, self._export_btn):
            w.setEnabled(on)

    # ── data in ─────────────────────────────────────────────────────
    def set_decks(self, decks: list[dict]):
        self._list.clear()
        for d in decks:
            label = d.get("title", "Untitled")
            item = QListWidgetItem(f"{label}  ·  {d.get('slide_count', 0)} slides")
            item.setData(Qt.UserRole, d.get("id"))
            item.setData(Qt.UserRole + 1, d.get("status"))
            self._list.addItem(item)
        if not decks:
            self._status.setText("No decks yet — model one from a topic or a Workbench doc.")

    def show_deck(self, deck: dict):
        self._active_deck_id = deck.get("id")
        self._title.setText(deck.get("title", ""))
        self._outline.setPlainText(outline_to_text(deck.get("outline", {})))
        self._set_editor_enabled(True)
        st = deck.get("status", "pending")
        self._status.setText("Exported." if st == "exported" else "Draft.")

    def set_status(self, text: str):
        self._status.setText(text)

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
        outline = text_to_outline(self._title.text(), self._outline.toPlainText())
        self.outline_saved.emit(int(self._active_deck_id), outline["title"], outline)

    def _on_export(self):
        if self._active_deck_id is not None:
            self.export_requested.emit(int(self._active_deck_id))
