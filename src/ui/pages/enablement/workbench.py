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

    draft_selected = Signal(int)        # workspace chip picked → switch active draft
    workspace_closed = Signal(int)      # workspace chip's × → close that workspace
    find_task_requested = Signal()      # "+ Find a task" → host opens the task search
    publish_requested = Signal(str)     # destination key → push the active content
    load_file_requested = Signal(str)   # dropped file path → load content
    open_chat_requested = Signal()      # request the Assistant chat in the drilldown
    existing_cards_requested = Signal() # "Existing Guru card" submenu opened → host fetches real cards
    import_requested = Signal(str)      # "drive" | "guru" → host runs the import
    content_edited = Signal(int, str)   # (draft_id, markdown) from the Edit view
    # Passthrough of the rich editor's inline "/"-menu + highlight-to-edit ask
    # (instruction, selection_text). The host runs the revise off-thread and
    # reloads this canvas; the workbench itself stays provider/LLM-free.
    ai_edit_requested = Signal(str, str)

    MAX_WORKSPACES = 4   # work up to 4 cards at once, toggling between chips

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{ALMA_CREAM};")
        self.setAcceptDrops(True)   # drop a doc anywhere on the workbench
        self._active_draft_id = 1
        self._current_drafts = []
        self._chip_widgets = []
        self._ws_state = {}          # draft_id → {md, html, view, cursor} per workspace
        self._current_md = ""
        self._current_html = None    # cleaned rich HTML for Guru publish (or None)
        self._linked_card_md = ""    # current linked-card md, for the Review-changes diff
        self._overlay_host = None    # content_stack, injected by the page
        self._overlay = None         # lazily-built ExpandOverlay
        self._build()
        self._install_workspace_shortcuts()
        self.set_pending_drafts(_SAMPLE_DRAFTS)
        self.show_draft(_SAMPLE_CARD)

    def _install_workspace_shortcuts(self):
        """Ctrl+1..4 jump to the Nth open workspace chip."""
        from PySide6.QtGui import QKeySequence, QShortcut
        for i in range(1, self.MAX_WORKSPACES + 1):
            sc = QShortcut(QKeySequence(f"Ctrl+{i}"), self)
            sc.activated.connect(lambda idx=i - 1: self._switch_to_index(idx))

    def _switch_to_index(self, idx: int):
        if 0 <= idx < len(self._current_drafts):
            self.draft_selected.emit(self._current_drafts[idx]["id"])

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
        lbl = QLabel("WORKSPACES")
        lbl.setStyleSheet(f"color:{ALMA_TEXT_LIGHT}; font-size:11px; font-weight:700; letter-spacing:0.7px;")
        row.addWidget(lbl)
        count = QLabel("0")
        count.setFixedSize(18, 18)
        count.setAlignment(Qt.AlignCenter)
        count.setStyleSheet(f"background:{ALMA_GREEN_DARK}; color:white; border-radius:9px; font-size:10px; font-weight:700;")
        row.addWidget(count)
        self._count_badge = count
        self._pending_row = row
        row.addStretch(1)
        find_btn = QPushButton("+ Find a task")
        find_btn.setCursor(Qt.PointingHandCursor)
        find_btn.setStyleSheet(
            f"QPushButton{{background:{ALMA_BG_ELEVATED}; color:{ALMA_GREEN_DARK}; "
            f"border:1px solid {ALMA_BORDER}; border-radius:8px; padding:6px 13px; "
            f"font-size:12px; font-weight:600;}}"
        )
        find_btn.clicked.connect(self.find_task_requested.emit)
        row.addWidget(find_btn)
        chat_btn = QPushButton("Open Assistant ›")
        chat_btn.setCursor(Qt.PointingHandCursor)
        chat_btn.setStyleSheet(
            f"QPushButton{{background:{ALMA_GREEN_DARK}; color:{ALMA_TEXT_ON_DARK}; border:none; "
            f"border-radius:8px; padding:7px 16px; font-size:12px; font-weight:600;}}"
        )
        chat_btn.clicked.connect(self.open_chat_requested.emit)
        row.addWidget(chat_btn)
        return row

    def _workspace_chip(self, draft: dict, active: bool) -> QFrame:
        """A workspace chip: click the title to switch to it, × to close it."""
        frame = QFrame()
        border = ALMA_GREEN_LIGHT if active else ALMA_BORDER
        frame.setStyleSheet(
            f"QFrame{{background:{ALMA_BG_ELEVATED}; border:{'2px' if active else '1px'} "
            f"solid {border}; border-radius:8px;}}"
        )
        h = QHBoxLayout(frame)
        h.setContentsMargins(12, 3, 6, 3)
        h.setSpacing(6)
        textc = ALMA_GREEN_DARK if active else ALMA_TEXT_MID
        title = QPushButton(f"•  {draft['title']}")
        title.setCursor(Qt.PointingHandCursor)
        title.setStyleSheet(
            f"QPushButton{{background:transparent; color:{textc}; border:none; "
            f"text-align:left; font-size:12px; font-weight:600;}}"
        )
        title.clicked.connect(lambda: self.draft_selected.emit(draft["id"]))
        h.addWidget(title)
        close = QPushButton("×")
        close.setCursor(Qt.PointingHandCursor)
        close.setFixedSize(16, 16)
        close.setStyleSheet(
            f"QPushButton{{background:transparent; color:{ALMA_TEXT_LIGHT}; border:none; "
            f"font-size:15px; font-weight:700;}}QPushButton:hover{{color:{ALMA_GREEN_DARK};}}"
        )
        close.clicked.connect(lambda: self.workspace_closed.emit(draft["id"]))
        h.addWidget(close)
        return frame

    def set_pending_drafts(self, drafts: list[dict], active_id=None):
        """Set the open workspace chips (capped at MAX_WORKSPACES)."""
        drafts = list(drafts)[: self.MAX_WORKSPACES]
        self._current_drafts = drafts
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
            chip = self._workspace_chip(d, active=d["id"] == self._active_draft_id)
            self._pending_row.insertWidget(2 + idx, chip)   # after label+count, before stretch
            self._chip_widgets.append(chip)
        # drop cached state for workspaces that are no longer open
        if getattr(self, "_ws_state", None):
            self._ws_state = {k: v for k, v in self._ws_state.items() if k in ids}
        try:
            self._count_badge.setText(str(len(drafts)))
        except Exception:
            pass

    def open_workspace(self, draft: dict):
        """Open a draft as a workspace: activate it if already open, else add a
        chip (dropping the oldest when already at MAX_WORKSPACES)."""
        cur = list(self._current_drafts)
        ids = [d["id"] for d in cur]
        if draft["id"] in ids:
            self.switch_workspace(draft["id"])      # restore its cached state
            return
        old = self._active_draft_id
        if old is not None and old != draft["id"] and old in ids:
            self._snapshot_workspace(old)           # preserve the outgoing edits
        if len(cur) >= self.MAX_WORKSPACES:
            self._ws_state.pop(cur[0]["id"], None)
            cur = cur[1:]
        cur.append(draft)
        self.set_pending_drafts(cur, active_id=draft["id"])

    # ── per-workspace edit state (preserved across chip switches) ────
    def _current_view_mode(self) -> str:
        cur = self._body_stack.currentWidget()
        if cur is self._editor:
            return "edit"
        if cur is self._rich:
            return "rich"
        return "preview"

    def _snapshot_workspace(self, draft_id):
        """Capture the active workspace's live state (incl. unsaved edits + cursor)."""
        view = self._current_view_mode()
        md, html, cursor = self._current_md, self._current_html, 0
        try:
            if view == "edit":
                md = self._editor.toPlainText()
                cursor = self._editor.textCursor().position()
            elif view == "rich":
                md = self._rich.to_markdown()
                html = self._rich.to_clean_html()
        except Exception:
            pass
        self._ws_state[draft_id] = {"md": md, "html": html, "view": view, "cursor": cursor}

    def _restore_workspace(self, draft_id) -> bool:
        st = self._ws_state.get(draft_id)
        if not st:
            return False
        self._current_md = st["md"]
        self._current_html = st.get("html")
        self._render_body()
        self._set_view(st.get("view", "preview"))
        if st.get("view") == "edit":
            try:
                cur = self._editor.textCursor()
                cur.setPosition(min(int(st.get("cursor", 0)), len(self._editor.toPlainText())))
                self._editor.setTextCursor(cur)
            except Exception:
                pass
        return True

    def switch_workspace(self, draft_id, card=None):
        """Switch to a workspace: snapshot the outgoing one's edit state, restore
        this one's (view + unsaved markdown + cursor) if seen before, else show the
        card. This is how chip clicks + Ctrl+N keep 4 cards in flight at once."""
        old = self._active_draft_id
        if old is not None and old != draft_id:
            self._snapshot_workspace(old)
        self.set_active_draft(draft_id)
        if not self._restore_workspace(draft_id) and card is not None:
            self.show_draft(card)

    def reload_active_canvas(self, card: dict):
        """Re-render the active draft's canvas in place from freshly-stored
        content (an AI revise / chat edit replaced it server-side). Drops the
        stale cached edit state for this workspace so show_draft's content wins,
        and keeps the active id unchanged."""
        self._ws_state.pop(self._active_draft_id, None)
        self.show_draft(card)

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
        self._diff_btn = self._view_toggle_btn("Review changes")
        self._diff_btn.clicked.connect(lambda: self._set_view("diff"))
        meta.addWidget(self._diff_btn)
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
        # Re-emit the editor's inline AI-edit ask up to the host (page.py) so it
        # can route the revise + reload without reaching into the rich editor.
        self._rich.ai_edit_requested.connect(self.ai_edit_requested)
        self._editor = QPlainTextEdit()
        self._editor.setStyleSheet(
            f"QPlainTextEdit{{background:{ALMA_BG_INSET}; border:1px solid {ALMA_BORDER_LIGHT}; "
            f"border-radius:8px; color:{ALMA_TEXT_DARK}; "
            f"font-family:Consolas,monospace; font-size:12.5px; padding:8px;}}"
        )
        from src.ui.pages.enablement.diff_view import DiffView
        self._diff = DiffView()
        self._body_stack = QStackedWidget()
        self._body_stack.addWidget(self._body)     # 0 preview
        self._body_stack.addWidget(self._rich)     # 1 rich text (WYSIWYG)
        self._body_stack.addWidget(self._editor)   # 2 markdown source
        self._body_stack.addWidget(self._diff)     # 3 review-changes diff
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
        elif mode == "diff":
            self._diff.set_diff(self._linked_card_md, self._current_md)
            self._body_stack.setCurrentWidget(self._diff)
        else:
            self._render_body()
            self._body_stack.setCurrentWidget(self._body)
        self._style_view_btn(self._preview_btn, active=mode == "preview")
        self._style_view_btn(self._rich_btn, active=mode == "rich")
        self._style_view_btn(self._edit_btn, active=mode == "edit")
        self._style_view_btn(self._diff_btn, active=mode == "diff")

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
        # New draft: the diff baseline (the linked card md) belongs to this draft
        # and is fed separately by the host; clear the stale one until then.
        self._linked_card_md = card.get("linked_card_md", "")
        self._render_body()
        self._body_stack.setCurrentWidget(self._body)
        self._style_view_btn(self._preview_btn, active=True)
        self._style_view_btn(self._rich_btn, active=False)
        self._style_view_btn(self._edit_btn, active=False)
        self._style_view_btn(self._diff_btn, active=False)

    def set_linked_card_md(self, md: str):
        """Host feeds the current linked-card markdown — the diff baseline the
        Review-changes view compares the active draft's proposed content against.
        If the diff view is showing, re-render it against the new baseline."""
        self._linked_card_md = md or ""
        if self._body_stack.currentWidget() is self._diff:
            self._diff.set_diff(self._linked_card_md, self._current_md)

    def set_active_draft(self, draft_id):
        self.set_pending_drafts(self._current_drafts, active_id=draft_id)

    @property
    def active_draft_id(self):
        return self._active_draft_id
