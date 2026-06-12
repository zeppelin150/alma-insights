"""Enablement Workbench — the hero page.

Renders the active Guru card draft (markdown → rich text), a pending-draft picker,
a subtask/scratch strip, and a Publish & Tools strip (push the active content to
Drive/Guru, drag-drop a file). The Assistant chat lives in the shared DrilldownPanel
(see chat_panel.ChatPanel) — "Open Assistant" requests it from the host.

Data in via set_pending_drafts()/show_draft(); actions out via Qt signals.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QMenu, QPlainTextEdit,
    QPushButton, QSizePolicy, QStackedWidget, QTextBrowser, QVBoxLayout,
    QWidget,
)

from src.ui.theme import (
    ALMA_ACCENT_TEAL, ALMA_BG_ELEVATED, ALMA_BG_INSET, ALMA_BORDER,
    ALMA_BORDER_LIGHT, ALMA_CREAM, ALMA_GREEN_DARK, ALMA_GREEN_LIGHT,
    ALMA_INFO, ALMA_TEXT_DARK, ALMA_TEXT_LIGHT, ALMA_TEXT_MID,
    ALMA_TEXT_ON_DARK, ALMA_WARNING,
)
from src.ui.pages.enablement._common import TINT as _TINT
from src.ui.pages.enablement._common import badge as _badge
from src.ui.pages.enablement._common import card_frame as _card_frame

_TEAL = ALMA_ACCENT_TEAL

_SAMPLE_DRAFTS = [
    {"id": 1, "title": "SSO Setup", "source": "drive"},
    {"id": 2, "title": "Returns Policy", "source": "drive"},
    {"id": 3, "title": "Payments v2", "source": "guru"},
]

_SAMPLE_CARD = {
    "breadcrumb": "GURU › PROVIDER ENABLEMENT",
    "title": "Setting up SSO for Providers",
    "source": "From: SSO Setup.gdoc",
    "markdown": (
        "Providers can now self-serve SSO configuration from the admin console. "
        "This guide covers setup and the June 24 rollout.\n\n"
        "> **Rollout:** June 24, 2026 — enabled for all provider orgs\n\n"
        "## Steps\n"
        "1. Open Admin Console › Security › SSO\n"
        "2. Choose your identity provider (Okta, Azure AD, Google)\n"
        "3. Upload the metadata XML and save\n"
        "4. Test with a pilot org before enabling org-wide\n\n"
        "## FAQ\n"
        "**Does this affect existing logins?**  No — current sessions stay "
        "active until SSO is enabled org-wide."
    ),
}


class _DropZone(QFrame):
    """A drag-and-drop target that emits the dropped file path."""

    dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFixedHeight(36)
        self.setMinimumWidth(250)
        self._idle()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 0)
        lbl = QLabel("Load content — drag & drop a file")
        lbl.setStyleSheet(f"color:{ALMA_TEXT_MID}; font-size:12px; border:none; background:transparent;")
        lay.addWidget(lbl)

    def _idle(self):
        self.setStyleSheet(f"QFrame{{background:{ALMA_BG_INSET}; border:1px dashed {ALMA_BORDER}; border-radius:8px;}}")

    def _hot(self):
        self.setStyleSheet(f"QFrame{{background:#E4EFE9; border:1px dashed {ALMA_GREEN_LIGHT}; border-radius:8px;}}")

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._hot()

    def dragLeaveEvent(self, e):
        self._idle()

    def dropEvent(self, e):
        self._idle()
        urls = e.mimeData().urls()
        if urls:
            self.dropped.emit(urls[0].toLocalFile())


class WorkbenchPage(QWidget):
    """Card render canvas + pending-draft picker + Publish & Tools strip."""

    draft_selected = Signal(int)        # pending-draft picked
    publish_requested = Signal(str)     # destination key → push the active content
    load_file_requested = Signal(str)   # dropped file path → load content
    open_chat_requested = Signal()      # request the Assistant chat in the drilldown
    existing_cards_requested = Signal() # "Existing Guru card" submenu opened → host fetches real cards
    import_requested = Signal(str)      # "drive" | "guru" → host runs the import
    content_edited = Signal(int, str)   # (draft_id, markdown) from the Edit view

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{ALMA_CREAM};")
        self._active_draft_id = 1
        self._current_drafts = []
        self._chip_widgets = []
        self._current_md = ""
        self._build()
        self.set_pending_drafts(_SAMPLE_DRAFTS)
        self.show_draft(_SAMPLE_CARD)

    # ── layout ────────────────────────────────────────────────────
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 18)
        outer.setSpacing(14)
        outer.addLayout(self._build_pending_strip())
        outer.addWidget(self._build_card_panel(), 1)
        outer.addWidget(self._build_tools_panel())

    def _build_pending_strip(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        lbl = QLabel("PENDING DRAFTS")
        lbl.setStyleSheet(f"color:{ALMA_TEXT_LIGHT}; font-size:11px; font-weight:700; letter-spacing:0.7px;")
        row.addWidget(lbl)
        count = QLabel("3")
        count.setFixedSize(18, 18)
        count.setAlignment(Qt.AlignCenter)
        count.setStyleSheet("background:#C41E1E; color:white; border-radius:9px; font-size:10px; font-weight:700;")
        row.addWidget(count)
        self._pending_row = row
        row.addStretch(1)
        chat_btn = QPushButton("Open Assistant ›")
        chat_btn.setCursor(Qt.PointingHandCursor)
        chat_btn.setStyleSheet(
            f"QPushButton{{background:{ALMA_GREEN_DARK}; color:{ALMA_TEXT_ON_DARK}; border:none; "
            f"border-radius:8px; padding:7px 16px; font-size:12px; font-weight:600;}}"
        )
        chat_btn.clicked.connect(self.open_chat_requested.emit)
        row.addWidget(chat_btn)
        return row

    def _draft_chip(self, draft: dict, active: bool) -> QPushButton:
        _, dot = _TINT.get(draft.get("source", "drive"), ("#E4ECF5", ALMA_INFO))
        btn = QPushButton(f"•  {draft['title']}")
        btn.setCursor(Qt.PointingHandCursor)
        border = ALMA_GREEN_LIGHT if active else ALMA_BORDER
        textc = ALMA_GREEN_DARK if active else ALMA_TEXT_MID
        btn.setStyleSheet(
            f"QPushButton{{background:{ALMA_BG_ELEVATED}; color:{textc}; text-align:left; "
            f"border:{'2px' if active else '1px'} solid {border}; border-radius:8px; "
            f"padding:6px 14px; font-size:12px; font-weight:600;}}"
        )
        btn.clicked.connect(lambda: self.draft_selected.emit(draft["id"]))
        return btn

    def set_pending_drafts(self, drafts: list[dict], active_id=None):
        self._current_drafts = list(drafts)
        ids = [d["id"] for d in drafts]
        if active_id is not None:
            self._active_draft_id = active_id
        elif drafts and self._active_draft_id not in ids:
            self._active_draft_id = drafts[0]["id"]
        for chip in self._chip_widgets:
            self._pending_row.removeWidget(chip)
            chip.hide()
            chip.deleteLater()
        self._chip_widgets = []
        for idx, d in enumerate(drafts):
            chip = self._draft_chip(d, active=d["id"] == self._active_draft_id)
            self._pending_row.insertWidget(2 + idx, chip)   # after label+count, before stretch
            self._chip_widgets.append(chip)

    def _build_card_panel(self) -> QFrame:
        card = _card_frame()
        v = QVBoxLayout(card)
        v.setContentsMargins(28, 24, 28, 18)
        v.setSpacing(8)

        self._breadcrumb = QLabel()
        self._breadcrumb.setStyleSheet(f"color:{ALMA_TEXT_LIGHT}; font-size:11px; font-weight:700; letter-spacing:0.8px; border:none;")
        v.addWidget(self._breadcrumb)

        self._title = QLabel()
        self._title.setStyleSheet(f"color:{ALMA_TEXT_DARK}; font-size:23px; font-weight:700; border:none;")
        v.addWidget(self._title)

        meta = QHBoxLayout()
        meta.setSpacing(10)
        meta.addWidget(_badge("Draft", "draft"))
        self._source = QLabel()
        self._source.setStyleSheet(f"color:{ALMA_INFO}; font-size:12px; font-weight:600; border:none;")
        meta.addWidget(self._source)
        _upd = QLabel("•  updated just now")
        _upd.setStyleSheet(f"color:{ALMA_TEXT_LIGHT}; font-size:12px; border:none;")
        meta.addWidget(_upd)
        meta.addStretch(1)
        self._preview_btn = self._view_toggle_btn("Guru preview")
        self._preview_btn.clicked.connect(lambda: self._set_view("preview"))
        meta.addWidget(self._preview_btn)
        self._rich_btn = self._view_toggle_btn("Rich text")
        self._rich_btn.clicked.connect(lambda: self._set_view("rich"))
        meta.addWidget(self._rich_btn)
        self._edit_btn = self._view_toggle_btn("Edit markdown")
        self._edit_btn.clicked.connect(lambda: self._set_view("edit"))
        meta.addWidget(self._edit_btn)
        v.addLayout(meta)

        self._body = QTextBrowser()
        self._body.setOpenExternalLinks(False)
        self._body.setStyleSheet(
            f"QTextBrowser{{background:{ALMA_BG_ELEVATED}; border:none; color:{ALMA_TEXT_DARK}; font-size:13.5px;}}"
        )
        from src.ui.pages.enablement.rich_editor import RichTextEditor
        self._rich = RichTextEditor()
        self._editor = QPlainTextEdit()
        self._editor.setStyleSheet(
            f"QPlainTextEdit{{background:{ALMA_BG_INSET}; border:1px solid {ALMA_BORDER_LIGHT}; "
            f"border-radius:8px; color:{ALMA_TEXT_DARK}; "
            f"font-family:Consolas,monospace; font-size:12.5px; padding:8px;}}"
        )
        self._body_stack = QStackedWidget()
        self._body_stack.addWidget(self._body)     # 0 preview
        self._body_stack.addWidget(self._rich)     # 1 rich text (WYSIWYG)
        self._body_stack.addWidget(self._editor)   # 2 markdown source
        v.addWidget(self._body_stack, 1)

        strip = QFrame()
        strip.setStyleSheet(f"QFrame{{border:none; border-top:1px solid {ALMA_BORDER_LIGHT};}}")
        sl = QHBoxLayout(strip)
        sl.setContentsMargins(0, 8, 0, 0)
        sub = QLabel("Subtasks 2/5")
        sub.setStyleSheet(f"color:{ALMA_TEXT_MID}; font-size:12px; font-weight:600; border:none;")
        sl.addWidget(sub)
        for done in (True, True, False, False, False):
            box = QLabel("")
            box.setFixedSize(15, 15)
            c = ALMA_GREEN_LIGHT if done else ALMA_BG_ELEVATED
            box.setStyleSheet(f"background:{c}; border:1px solid {ALMA_GREEN_LIGHT if done else ALMA_BORDER}; border-radius:4px;")
            sl.addWidget(box)
        note = QLabel("•  Scratch pad: launch date pending")
        note.setStyleSheet(f"color:{ALMA_TEXT_LIGHT}; font-size:12px; border:none;")
        sl.addWidget(note)
        sl.addStretch(1)
        v.addWidget(strip)
        return card

    # ── preview / edit toggle ─────────────────────────────────────
    def _view_toggle_btn(self, text: str) -> QPushButton:
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        self._style_view_btn(b, active=False)
        return b

    def _style_view_btn(self, b: QPushButton, *, active: bool):
        bg = "#DCEFEC" if active else ALMA_BG_ELEVATED
        fg = _TEAL if active else ALMA_TEXT_MID
        border = _TEAL if active else ALMA_BORDER
        b.setStyleSheet(
            f"QPushButton{{background:{bg}; color:{fg}; border:1px solid {border}; "
            f"border-radius:7px; padding:4px 12px; font-size:11px; font-weight:600;}}"
        )

    def _commit_active_editor(self):
        """Read the editor we're leaving back into _current_md, emitting
        content_edited if it changed. Markdown source wins verbatim; the
        rich editor serializes to markdown via toMarkdown()."""
        cur = self._body_stack.currentWidget()
        new_md = None
        if cur is self._editor:
            new_md = self._editor.toPlainText()
        elif cur is self._rich:
            new_md = self._rich.to_markdown()
        if new_md is not None and new_md != self._current_md:
            self._current_md = new_md
            self.content_edited.emit(self._active_draft_id, new_md)

    def _set_view(self, mode: str):
        self._commit_active_editor()
        if mode == "edit":
            self._editor.setPlainText(self._current_md)
            self._body_stack.setCurrentWidget(self._editor)
        elif mode == "rich":
            self._rich.set_markdown(self._current_md)
            self._body_stack.setCurrentWidget(self._rich)
        else:
            self._render_body()
            self._body_stack.setCurrentWidget(self._body)
        self._style_view_btn(self._preview_btn, active=mode == "preview")
        self._style_view_btn(self._rich_btn, active=mode == "rich")
        self._style_view_btn(self._edit_btn, active=mode == "edit")

    def _render_body(self):
        try:
            from src.ui.pages.enablement.guru_preview import render_preview
            render_preview(self._body, self._current_md)
        except Exception:
            self._body.setMarkdown(self._current_md)

    # Demo placeholder Guru cards offered under "Push to Guru › Existing card".
    _DEMO_CARDS = ("Setting up SSO for Providers", "Returns & Refunds Policy", "Payments v2 Overview")

    def _secondary_btn(self, text: str) -> QPushButton:
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(
            f"QPushButton{{background:{ALMA_BG_ELEVATED}; color:{ALMA_GREEN_DARK}; "
            f"border:1px solid {ALMA_BORDER}; border-radius:8px; padding:7px 14px; "
            f"font-size:12px; font-weight:600;}} QPushButton:hover{{border-color:{ALMA_GREEN_LIGHT};}} "
            f"QPushButton::menu-indicator{{subcontrol-position:right center; right:8px;}}"
        )
        return b

    def _build_tools_panel(self) -> QFrame:
        """Publish & tools strip — upload a document, or push the active content out."""
        panel = _card_frame()
        h = QHBoxLayout(panel)
        h.setContentsMargins(18, 12, 18, 12)
        h.setSpacing(10)
        lab = QLabel("PUBLISH & TOOLS")
        lab.setStyleSheet(f"color:{ALMA_TEXT_LIGHT}; font-size:11px; font-weight:700; letter-spacing:0.7px; border:none;")
        h.addWidget(lab)

        # Upload (primary, opens the OS file picker)
        up = QPushButton("Upload document")
        up.setCursor(Qt.PointingHandCursor)
        up.setStyleSheet(
            f"QPushButton{{background:{ALMA_GREEN_DARK}; color:{ALMA_TEXT_ON_DARK}; border:none; "
            f"border-radius:8px; padding:7px 16px; font-size:12px; font-weight:600;}}"
        )
        up.clicked.connect(self._on_upload)
        h.addWidget(up)

        # Import ▾  (Google Doc by URL / existing Guru card in native format)
        imp = self._secondary_btn("Import")
        imenu = QMenu(imp)
        imenu.addAction(
            "Google Doc / Drive URL…",
            lambda: self.import_requested.emit("drive"),
        )
        imenu.addAction(
            "Existing Guru card…",
            lambda: self.import_requested.emit("guru"),
        )
        imp.setMenu(imenu)
        h.addWidget(imp)

        # Push to Guru ▾  (New card / Existing card → inline edit)
        guru = self._secondary_btn("Push to Guru")
        gmenu = QMenu(guru)
        gmenu.addAction("New Guru card", lambda: self.publish_requested.emit("guru_new"))
        self._existing_menu = gmenu.addMenu("Existing Guru card")
        self._existing_menu.aboutToShow.connect(self.existing_cards_requested.emit)
        self._render_existing_cards([{"id": None, "title": c} for c in self._DEMO_CARDS])
        guru.setMenu(gmenu)
        h.addWidget(guru)

        # Save to Drive ▾  (New doc / Update existing doc)
        drive = self._secondary_btn("Save to Drive")
        dmenu = QMenu(drive)
        dmenu.addAction("New Google Doc", lambda: self.publish_requested.emit("drive_new"))
        dmenu.addAction("Update existing doc", lambda: self.publish_requested.emit("drive_update"))
        drive.setMenu(dmenu)
        h.addWidget(drive)

        h.addStretch(1)
        dz = _DropZone()
        dz.dropped.connect(self.load_file_requested)
        h.addWidget(dz)
        return panel

    def _on_upload(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Upload a document", "",
            "Documents (*.pdf *.docx *.doc *.md *.txt);;All files (*)")
        if path:
            self.load_file_requested.emit(path)

    def _render_existing_cards(self, cards: list[dict]):
        self._existing_menu.clear()
        if not cards:
            act = self._existing_menu.addAction("No cards found")
            act.setEnabled(False)
            return
        for c in cards:
            # Live cards carry an id (→ update that card); demo placeholders pass
            # their title (the host's demo branch handles those).
            key = c.get("id") or c.get("title", "card")
            title = c.get("title", "card")
            self._existing_menu.addAction(title, lambda k=key: self.publish_requested.emit(f"guru_existing:{k}"))

    def set_existing_cards(self, cards: list[dict]):
        """Host feeds real Guru cards [{id, title}] for the 'Existing card' submenu."""
        self._render_existing_cards(cards)

    # ── data setters ──────────────────────────────────────────────
    def show_draft(self, card: dict):
        self._breadcrumb.setText(card.get("breadcrumb", ""))
        self._title.setText(card.get("title", ""))
        self._source.setText(card.get("source", ""))
        # Switching draft: drop any in-progress edit of the OLD draft rather
        # than committing it under the new draft id.
        self._current_md = card.get("markdown", "")
        self._render_body()
        self._body_stack.setCurrentWidget(self._body)
        self._style_view_btn(self._preview_btn, active=True)
        self._style_view_btn(self._rich_btn, active=False)
        self._style_view_btn(self._edit_btn, active=False)

    def set_active_draft(self, draft_id):
        self.set_pending_drafts(self._current_drafts, active_id=draft_id)

    @property
    def active_draft_id(self):
        return self._active_draft_id
