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
    QPushButton, QSizePolicy, QStackedWidget, QTextBrowser, QToolButton,
    QVBoxLayout, QWidget,
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

    def dragMoveEvent(self, e):
        # Windows rejects the drop if the move isn't also accepted, even
        # after dragEnter accepted — keep saying yes for file drags.
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

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
        self.setAcceptDrops(True)   # drop a doc anywhere on the workbench
        self._active_draft_id = 1
        self._current_drafts = []
        self._chip_widgets = []
        self._current_md = ""
        self._current_html = None    # cleaned rich HTML for Guru publish (or None)
        self._overlay_host = None    # content_stack, injected by the page
        self._overlay = None         # lazily-built ExpandOverlay
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
        self._expand_btn = self._view_toggle_btn("⤢  Expand")
        self._expand_btn.clicked.connect(self._open_expand)
        meta.addWidget(self._expand_btn)
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
        rich editor serializes to markdown via toMarkdown() AND captures
        cleaned HTML (carrying color/highlight) for the Guru publish path.
        A markdown-only source edit clears the HTML (publish re-derives)."""
        cur = self._body_stack.currentWidget()
        new_md = None
        new_html = None
        if cur is self._editor:
            new_md = self._editor.toPlainText()      # markdown only → derive HTML
        elif cur is self._rich:
            new_md = self._rich.to_markdown()
            new_html = self._rich.to_clean_html()
        if new_md is not None and new_md != self._current_md:
            self._current_md = new_md
            self._current_html = new_html
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

    # ── expand / focus overlay ────────────────────────────────────
    def set_overlay_host(self, widget):
        """The page injects the content-area widget (content_stack) the
        full-window overlay should parent to. Falls back to window()."""
        self._overlay_host = widget

    def _open_expand(self):
        """Bring a large, near-fullscreen editor forward over the whole
        content area, bound to the active draft's markdown."""
        self._commit_active_editor()
        if self._overlay is None:
            from src.ui.pages.enablement.expand_overlay import ExpandOverlay
            self._overlay = ExpandOverlay(self._overlay_host or self.window())
            self._overlay.committed.connect(self._on_expand_committed)
        self._overlay.load(self._current_md, self._active_draft_id, self._title.text())
        self._overlay.present()

    def _on_expand_committed(self, draft_id: int, md: str, html=None):
        if md == self._current_md:
            return
        # Set _current_md/_current_html FIRST so the inline editors and the
        # _commit_active_editor dedup guard stay consistent and don't re-emit,
        # then route the edit through the existing content_edited path.
        self._current_md = md
        self._current_html = html
        self._editor.setPlainText(md)
        self._rich.set_markdown(md)
        self._render_body()
        self.content_edited.emit(draft_id, md)

    def current_html(self):
        """Cleaned rich HTML for the active draft's last edit (or None if the
        last edit was markdown-only). The page reads this on content_edited to
        persist content_html alongside the markdown."""
        return self._current_html

    # ── drag & drop (drop a doc anywhere on the workbench card) ──────
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
            self.load_file_requested.emit(urls[0].toLocalFile())

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
        """Compact action bar: a primary Upload button + a single Tools
        hamburger that folds Import / Push to Guru / Save to Drive into one
        menu (the old strip-of-buttons, decluttered). All signals preserved."""
        panel = _card_frame()
        h = QHBoxLayout(panel)
        h.setContentsMargins(18, 10, 18, 10)
        h.setSpacing(10)

        # Upload (primary, opens the OS file picker)
        up = QPushButton("Upload document")
        up.setCursor(Qt.PointingHandCursor)
        up.setStyleSheet(
            f"QPushButton{{background:{ALMA_GREEN_DARK}; color:{ALMA_TEXT_ON_DARK}; border:none; "
            f"border-radius:8px; padding:7px 16px; font-size:12px; font-weight:600;}}"
        )
        up.clicked.connect(self._on_upload)
        h.addWidget(up)

        # ── Tools hamburger ──
        tools = QToolButton()
        tools.setText("Tools")
        tools.setPopupMode(QToolButton.InstantPopup)
        tools.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        tools.setCursor(Qt.PointingHandCursor)
        try:
            from src.ui.design.icons import icon as design_icon
            tools.setIcon(design_icon("sliders", 15, ALMA_GREEN_DARK))
        except Exception:
            pass
        tools.setStyleSheet(
            f"QToolButton{{background:{ALMA_BG_ELEVATED}; color:{ALMA_GREEN_DARK}; "
            f"border:1px solid {ALMA_BORDER}; border-radius:8px; padding:7px 14px; "
            f"font-size:12px; font-weight:600;}} QToolButton:hover{{border-color:{ALMA_GREEN_LIGHT};}} "
            f"QToolButton::menu-indicator{{image:none;}}"
        )
        menu = QMenu(tools)

        imp = menu.addMenu("Import")
        imp.addAction("Google Doc / Drive URL…", lambda: self.import_requested.emit("drive"))
        imp.addAction("Existing Guru card…", lambda: self.import_requested.emit("guru"))

        guru = menu.addMenu("Push to Guru")
        guru.addAction("New Guru card", lambda: self.publish_requested.emit("guru_new"))
        self._existing_menu = guru.addMenu("Existing Guru card")
        self._existing_menu.aboutToShow.connect(self.existing_cards_requested.emit)
        self._render_existing_cards([{"id": None, "title": c} for c in self._DEMO_CARDS])

        drive = menu.addMenu("Save to Drive")
        drive.addAction("New Google Doc", lambda: self.publish_requested.emit("drive_new"))
        drive.addAction("Update existing doc", lambda: self.publish_requested.emit("drive_update"))

        menu.addSeparator()
        menu.addAction("Load a file…", self._on_upload)
        tools.setMenu(menu)
        self._tools_menu = menu
        # Retain submenu refs — PySide6's addMenu(str) hands back a QMenu
        # whose C++ object is destroyed (with its actions) when the local
        # wrapper is GC'd, even though it's logically a child of `menu`.
        self._tools_submenus = (imp, guru, drive)
        h.addWidget(tools)

        h.addStretch(1)
        dz = _DropZone()
        dz.dropped.connect(self.load_file_requested)
        h.addWidget(dz)
        return panel

    def _on_upload(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Upload a document", "",
            "Documents (*.docx *.md *.markdown *.txt *.csv);;All files (*)")
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
        # Seed HTML from the draft's stored content_html if present (e.g. an
        # imported Guru card keeps its source HTML); else None → publish derives.
        self._current_html = card.get("content_html")
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
