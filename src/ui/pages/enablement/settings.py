"""Enablement Settings — the Content Command Center's config surface.

Seven sub-tabs: Connections (status + workspace basics), Providers
(identity + the shared CredentialsPanel), Sources (Asana / Drive / the
knowledge base), Style Guide (guide + card template libraries), and the
three system tabs ported from the product side on 2026-07-22 — Updates
(shared UpdatesPanel), Usage (shared CostDashboard, lazily built when a
warehouse is wired) and Maintenance (shared MaintenancePanel).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QScrollArea, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from src.ui.pages.enablement._common import Toggle, card_frame, field, pill, section_label
from src.ui.theme import (
    ALMA_BG_ELEVATED, ALMA_BG_INSET, ALMA_BORDER, ALMA_BORDER_LIGHT, ALMA_CREAM,
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_INFO, ALMA_SUCCESS, ALMA_TEXT_DARK,
    ALMA_TEXT_LIGHT, ALMA_TEXT_MID, ALMA_TEXT_ON_DARK, ALMA_WARNING,
)

from src.ui.theme import ALMA_ACCENT_TEAL as _TEAL  # noqa: E402


class _BoardToggle(Toggle):
    """A shared Toggle that actually reports its flip.

    ``_common.Toggle`` repaints on click and tells nobody — which is exactly how
    the old Asana mockup's two switches could look live while being wired to
    nothing. This subclass emits the new state so the host can persist it.
    """

    changed = Signal(bool)

    def mousePressEvent(self, e):
        super().mousePressEvent(e)
        self.changed.emit(self.on)


class _GuideSection(QFrame):
    """One tagged-guide card (style guide / card-article template): the
    Paste / Upload / Upload folder / From Drive / Clear actions, a rendered
    preview of the ACTIVE document that flips into an inline markdown editor
    (Edit → Save), and the stored-document library (switch / delete)."""

    action = Signal(str)      # "paste" | "upload" | "upload_folder" | "drive" | "clear"
    activate = Signal(str)    # doc_id → make this stored doc active
    delete_doc = Signal(str)  # doc_id → delete this stored doc
    saved = Signal(str)       # edited text saved from the inline editor

    _TAGS = ("[STYLE-GUIDE]", "[CARD-TEMPLATE]")

    def __init__(self, title: str, hint: str, parent=None):
        super().__init__(parent)
        self._raw = ""
        self._editing = False
        # objectName-scoped style so child QFrames (preview inset, library
        # rows) don't inherit the card chrome.
        self.setObjectName("GuideCard")
        self.setStyleSheet(
            f"QFrame#GuideCard{{background:{ALMA_BG_ELEVATED}; "
            f"border:1px solid {ALMA_BORDER_LIGHT}; border-radius:12px;}}")
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(12)

        head = QHBoxLayout()
        head.addWidget(section_label(title))
        head.addStretch(1)
        paste = self._btn("Paste…", kind="outline")
        paste.clicked.connect(lambda: self.action.emit("paste"))
        head.addWidget(paste)
        v.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._status = QLabel("Not set")
        self._status.setStyleSheet(
            f"color:{ALMA_TEXT_LIGHT}; font-size:12.5px; border:none; background:transparent;")
        row.addWidget(self._status)
        row.addStretch(1)
        for label, act in (("Upload…", "upload"),
                           ("Upload folder…", "upload_folder"),
                           ("From Drive…", "drive")):
            b = self._btn(label, kind="elevated")
            b.clicked.connect(lambda _=False, a=act: self.action.emit(a))
            row.addWidget(b)
        clear = self._btn("Clear", kind="quiet")
        clear.clicked.connect(lambda: self.action.emit("clear"))
        row.addWidget(clear)
        v.addLayout(row)

        hint_lbl = QLabel(hint)
        hint_lbl.setWordWrap(True)
        hint_lbl.setStyleSheet(
            f"color:{ALMA_TEXT_LIGHT}; font-size:12.5px; border:none; background:transparent;")
        v.addWidget(hint_lbl)

        # rendered preview ⇄ inline editor of the ACTIVE document
        self._preview = QFrame()
        self._preview.setObjectName("GuidePreview")
        self._preview.setStyleSheet(
            f"QFrame#GuidePreview{{background:{ALMA_BG_INSET}; border:none; "
            f"border-radius:10px;}}")
        pv = QVBoxLayout(self._preview)
        pv.setContentsMargins(14, 10, 14, 12)
        pv.setSpacing(8)
        ph = QHBoxLayout()
        self._preview_cap = QLabel("ACTIVE — RENDERED PREVIEW")
        self._preview_cap.setStyleSheet(
            f"color:{ALMA_TEXT_LIGHT}; font-size:10px; font-weight:700; "
            f"letter-spacing:0.7px; border:none; background:transparent;")
        ph.addWidget(self._preview_cap)
        ph.addStretch(1)
        self._edit_btn = self._btn("Edit", kind="outline")
        self._edit_btn.clicked.connect(self._begin_edit)
        ph.addWidget(self._edit_btn)
        self._save_btn = self._btn("Save", kind="primary")
        self._save_btn.clicked.connect(self._save_edit)
        self._save_btn.hide()
        ph.addWidget(self._save_btn)
        self._cancel_btn = self._btn("Cancel", kind="quiet")
        self._cancel_btn.clicked.connect(self._cancel_edit)
        self._cancel_btn.hide()
        ph.addWidget(self._cancel_btn)
        pv.addLayout(ph)
        self._view = QTextBrowser()
        self._view.setOpenExternalLinks(False)
        self._view.setStyleSheet(
            f"QTextBrowser{{background:transparent; border:none; "
            f"color:{ALMA_TEXT_DARK}; font-size:13px;}}")
        self._view.setMinimumHeight(150)
        self._view.setMaximumHeight(380)
        pv.addWidget(self._view)
        self._editor = QPlainTextEdit()
        self._editor.setStyleSheet(
            f"QPlainTextEdit{{background:{ALMA_BG_ELEVATED}; border:1px solid "
            f"{ALMA_BORDER}; border-radius:7px; padding:8px; "
            f"color:{ALMA_TEXT_DARK}; font-size:12.5px;}}")
        self._editor.setMinimumHeight(220)
        self._editor.setMaximumHeight(380)
        self._editor.hide()
        pv.addWidget(self._editor)
        v.addWidget(self._preview)
        self._preview.hide()

        self._library = QVBoxLayout()
        self._library.setSpacing(4)
        v.addLayout(self._library)

    # ── content / preview state ───────────────────────────────────
    def set_status(self, text: str):
        self._status.setText(text or "Not set")

    def set_content(self, text: str):
        """Show the active document rendered as markdown; empty hides the
        preview block entirely (and always exits edit mode)."""
        self._raw = text or ""
        if not self._raw.strip():
            self._end_edit_mode()
            self._preview.hide()
            return
        self._view.setMarkdown(self._raw)
        self._preview.show()
        self._end_edit_mode()

    def content(self) -> str:
        return self._raw

    def in_edit_mode(self) -> bool:
        return self._editing

    def _begin_edit(self):
        self._editing = True
        self._editor.setPlainText(self._raw)
        self._view.hide()
        self._editor.show()
        self._edit_btn.hide()
        self._save_btn.show()
        self._cancel_btn.show()
        self._preview_cap.setText("EDITING — MARKDOWN SOURCE")

    def _end_edit_mode(self):
        self._editing = False
        self._editor.hide()
        self._view.show()
        self._save_btn.hide()
        self._cancel_btn.hide()
        self._edit_btn.show()
        self._preview_cap.setText("ACTIVE — RENDERED PREVIEW")

    def _cancel_edit(self):
        self.set_content(self._raw)

    def _save_edit(self):
        text = self._editor.toPlainText()
        self.set_content(text)     # optimistic; host refresh re-syncs from the DB
        self.saved.emit(text)

    # ── stored-document library ───────────────────────────────────
    def set_library(self, docs: list):
        while self._library.count():
            item = self._library.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for d in docs or []:
            self._library.addWidget(self._doc_row(d))

    def _doc_row(self, d: dict) -> QWidget:
        doc_id = d.get("doc_id", "")
        name = d.get("name", "") or "Untitled"
        for tag in self._TAGS:
            name = name.replace(tag, "")
        name = name.strip() or "Untitled"
        active = bool(d.get("active"))
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame{{background:{ALMA_BG_ELEVATED}; border:1px solid "
            f"{ALMA_GREEN_LIGHT if active else ALMA_BORDER}; border-radius:7px;}}"
        )
        h = QHBoxLayout(frame)
        h.setContentsMargins(10, 5, 8, 5)
        h.setSpacing(8)
        lbl = QLabel(name)
        lbl.setStyleSheet(
            f"color:{ALMA_TEXT_DARK}; font-size:12px; font-weight:600; border:none;")
        h.addWidget(lbl)
        chars = d.get("chars")
        if chars is not None:
            c = QLabel(f"{int(chars):,} chars")
            c.setStyleSheet(f"color:{ALMA_TEXT_LIGHT}; font-size:10.5px; border:none;")
            h.addWidget(c)
        h.addStretch(1)
        if active:
            badge_lbl = QLabel("Active")
            badge_lbl.setStyleSheet(
                f"background:{ALMA_GREEN_LIGHT}; color:{ALMA_GREEN_DARK}; border:none; "
                f"border-radius:6px; padding:2px 9px; font-size:10.5px; font-weight:700;")
            h.addWidget(badge_lbl)
        else:
            act = QPushButton("Make active")
            act.setCursor(Qt.PointingHandCursor)
            act.setStyleSheet(
                f"QPushButton{{background:transparent; color:{ALMA_GREEN_DARK}; "
                f"border:1px solid {ALMA_BORDER}; border-radius:6px; padding:3px 9px; "
                f"font-size:10.5px; font-weight:600;}}")
            act.clicked.connect(lambda _=False, d_=doc_id: self.activate.emit(d_))
            h.addWidget(act)
        rm = QPushButton("Delete")
        rm.setCursor(Qt.PointingHandCursor)
        rm.setStyleSheet(
            f"QPushButton{{background:transparent; color:{ALMA_TEXT_LIGHT}; border:none; "
            f"font-size:10.5px; font-weight:600;}}")
        rm.clicked.connect(lambda _=False, d_=doc_id: self.delete_doc.emit(d_))
        h.addWidget(rm)
        return frame

    # ── button factory (matches the section-card button chrome) ───
    @staticmethod
    def _btn(label: str, kind: str) -> QPushButton:
        b = QPushButton(label)
        b.setCursor(Qt.PointingHandCursor)
        if kind == "primary":
            css = (f"QPushButton{{background:{ALMA_GREEN_DARK}; color:{ALMA_TEXT_ON_DARK}; "
                   f"border:none; border-radius:7px; padding:5px 14px; "
                   f"font-size:11px; font-weight:600;}}")
        elif kind == "outline":
            css = (f"QPushButton{{background:transparent; color:{ALMA_GREEN_DARK}; "
                   f"border:1px solid {ALMA_BORDER}; border-radius:7px; padding:5px 12px; "
                   f"font-size:11.5px; font-weight:600;}}")
        elif kind == "quiet":
            css = (f"QPushButton{{background:transparent; color:{ALMA_TEXT_LIGHT}; "
                   f"border:none; padding:5px 8px; font-size:11px; font-weight:600;}}")
        else:  # elevated
            css = (f"QPushButton{{background:{ALMA_BG_ELEVATED}; color:{ALMA_TEXT_DARK}; "
                   f"border:1px solid {ALMA_BORDER}; border-radius:7px; padding:5px 12px; "
                   f"font-size:11px; font-weight:600;}}")
        b.setStyleSheet(css)
        return b


class SettingsPage(QWidget):
    """Operator config for the enablement side — sources, providers, system.

    Source config persists to monitor_sources; workspace choices go through
    settings_manager; the system tabs host the same shared panels the product
    Settings page embeds, so behavior and store are identical across modes.
    """

    asana_setup_requested = Signal()       # "Set up with Renn" clicked
    drive_folder_added = Signal(str, str)  # (folder_id, display_name) from "+ Add folder"
    # (folder_id, display_name, drive_id) from the native Browse Drive… picker —
    # the model-independent path to what resolve_drive_folder persists.
    drive_folder_picked = Signal(str, str, str)
    style_guide_action = Signal(str)       # "paste" | "upload" | "upload_folder" | "drive" | "clear"
    style_guide_activate = Signal(str)     # doc_id → make this stored guide active
    style_guide_delete = Signal(str)       # doc_id → delete this stored guide
    style_guide_saved = Signal(str)        # edited text saved from the inline editor
    card_template_action = Signal(str)     # same verbs as style_guide_action
    card_template_activate = Signal(str)   # doc_id → make this stored template active
    card_template_delete = Signal(str)     # doc_id → delete this stored template
    card_template_saved = Signal(str)      # edited text saved from the inline editor
    identity_detect_email_requested = Signal()       # "Auto-detect from Google"
    identity_resolve_asana_gid_requested = Signal()  # "Resolve GID"
    # ── Asana board management (2026-07-28) ──
    # This card used to be a hardcoded mockup: invented board names, invented
    # field mappings and a fabricated resolved-people list, rendered while
    # monitor_sources was empty — so the header count and every board on screen
    # described a configuration the database did not have. Everything here is
    # now fed by the host from asana_setup.board_summary(), and these signals
    # are the only way anything leaves the card.
    board_add_requested = Signal()             # "+ Add board"
    board_sync_toggled = Signal(str, bool)     # (source_id, sync on/off)
    board_calendar_toggled = Signal(str, bool) # (source_id, show on calendar)
    board_remove_requested = Signal(str)       # source_id → host runs the native confirm
    _kb_bootstrap_finished = Signal(str)             # worker thread → main (queued)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{ALMA_CREAM};")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 14, 20, 14)
        outer.setSpacing(10)
        outer.addLayout(self._intro())

        # Shared credentials/LLM panel — Claude+Gemini keys, model, routing,
        # Guru email+PAT, Google service-account + per-user OAuth. Same widget
        # the product Settings embeds, so config carries over between modes via
        # one keyring/settings store.
        from src.ui.widgets.credentials_panel import CredentialsPanel
        self.credentials = CredentialsPanel(sections=("llm", "external"))

        # Sub-tabs give the (formerly one long scroll) settings some order.
        # Icons are navigational, not decorative: with seven tabs the glyphs
        # let the eye find "the tab with the pie chart" without reading.
        from PySide6.QtCore import QSize
        from src.ui.design.icons import icon as design_icon

        self._tabs = QTabWidget()
        self._tabs.setObjectName("AnalysisTab")
        self._tabs.setIconSize(QSize(14, 14))

        def _add(widget, glyph, label):
            self._tabs.addTab(widget, design_icon(glyph, 14, ALMA_TEXT_MID), label)

        _add(self._tab([self._mode_card(), self._connections()]),
             "antenna", "Connections")
        _add(self._tab([self._identity(), self.credentials]), "sliders", "Providers")
        _add(self._tab([self._asana(), self._drive(),
                        self._knowledge_base()]), "database", "Sources")
        _add(self._tab([self._style_guide(), self._card_template()]),
             "pen", "Style Guide")
        # ── System tabs (shared with the product Settings page) ──
        _add(self._tab([self._updates_panel()]), "download", "Updates")
        _add(self._usage_tab(), "pie", "Usage")
        _add(self._tab([self._maintenance_panel()]), "zap", "Maintenance")
        outer.addWidget(self._tabs, 1)
        self._tabs.currentChanged.connect(self._on_tab_selected)
        # Host may wire a connection factory (page._conn) so the KB card can
        # show counts + run the bootstrap; absent, the card degrades to
        # settings-only status with the button disabled.
        self.kb_conn_factory = None
        # Host may wire a DatabaseManager factory so the Usage tab can build
        # the shared CostDashboard; absent, the tab keeps its placeholder.
        self.usage_db_factory = None
        self._kb_bootstrap_finished.connect(self._kb_bootstrap_done)

    # ── operating mode (demo ⇄ live) ─────────────────────────────────
    # The flag (`enablement.demo_mode`, default ON) previously had NO UI
    # writer — going live meant hand-editing settings.yaml, which the first
    # end-user pilot proved untenable. Going live is the authority-bearing
    # direction, so it gets a native confirm; returning to demo never does.
    def _mode_card(self) -> QFrame:
        # Boot-time flag snapshot: pages, the chat DB, and the background
        # monitor were all built against this value, and a settings flip
        # only fully lands after restart — the card must show the halfway
        # state honestly instead of claiming the new mode is in force.
        from src.data.settings_manager import get_section
        try:
            en0 = get_section("enablement", {}) or {}
        except Exception:  # noqa: BLE001
            en0 = {}
        self._boot_demo = bool(en0.get("demo_mode", True))

        card = card_frame()
        v = QVBoxLayout(card)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(10)
        v.addWidget(section_label("OPERATING MODE"))
        self._mode_status = QLabel("")
        self._mode_status.setWordWrap(True)
        self._mode_status.setStyleSheet(
            f"color:{ALMA_TEXT_MID}; font-size:12px; border:none;")
        v.addWidget(self._mode_status)
        row = QHBoxLayout()
        self._mode_toggle_btn = QPushButton("")
        self._mode_toggle_btn.setCursor(Qt.PointingHandCursor)
        self._mode_toggle_btn.setStyleSheet(
            f"QPushButton{{background:{ALMA_BG_ELEVATED}; color:{_TEAL}; "
            f"border:1px solid {_TEAL}; border-radius:7px; padding:6px 12px; "
            f"font-size:12px; font-weight:600;}}")
        self._mode_toggle_btn.clicked.connect(self._on_mode_toggle)
        row.addWidget(self._mode_toggle_btn)
        row.addStretch(1)
        v.addLayout(row)
        self._refresh_mode_card()
        return card

    def _refresh_mode_card(self):
        from src.data.settings_manager import get_section
        try:
            en = get_section("enablement", {}) or {}
        except Exception:  # noqa: BLE001
            en = {}
        demo = bool(en.get("demo_mode", True))
        if demo != self._boot_demo:
            still = ("Background sync started in live mode is STILL RUNNING "
                     "until you restart."
                     if not self._boot_demo else
                     "Pages and background workers stay in demo until you "
                     "restart.")
            self._mode_status.setText(
                f"Mode change saved — restart the app to apply it. {still}")
        elif demo:
            self._mode_status.setText(
                "Demo mode — a practice sandbox. Asana sync, Guru "
                "publishing, KB indexing and briefs are simulated, and "
                "Workbench/calendar work lives in a temporary practice "
                "database that is cleared at every launch. Renn chat still "
                "uses the live AI service.")
        else:
            self._mode_status.setText(
                "Live mode — connected integrations act for real.")
        self._mode_toggle_btn.setText(
            "Go live…" if demo else "Return to demo mode")

    def _on_mode_toggle(self):
        from PySide6.QtWidgets import QMessageBox
        from src.data.settings_manager import get_section, set_section
        demo = bool((get_section("enablement", {}) or {}).get(
            "demo_mode", True))
        if demo:
            resp = QMessageBox.question(
                self, "Leave demo mode?",
                "Live mode lets connected integrations act for real:\n"
                "Guru publishes reach your Guru workspace, Asana updates "
                "real tasks, and the KB indexes your Drive.\n\n"
                "The switch only fully applies after you RESTART the app — "
                "until then, pages and Renn keep parts of demo mode.\n"
                "You can return to demo mode here at any time.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if resp != QMessageBox.Yes:
                return
        # Re-read AFTER the modal: its nested event loop keeps main-thread
        # slots firing (e.g. the chat action poll writes enablement subkeys
        # via whole-section set_section) — writing a pre-dialog snapshot
        # back would silently revert those.
        cfg = dict(get_section("enablement", {}) or {})
        cfg["demo_mode"] = not demo
        if not set_section("enablement", cfg):
            QMessageBox.warning(
                self, "Couldn't save",
                "The mode change could not be written to settings — the "
                "app is still in its previous mode. Check that the data "
                "folder is writable, then try again.")
            return
        self._refresh_mode_card()
        self.refresh_kb_status()
        if not demo:
            # live→demo is the panic direction and has no confirm, so the
            # restart truth has to land right here, not in a status line.
            QMessageBox.information(
                self, "Restart needed",
                "Demo mode is saved but only fully applies after a "
                "restart. Background sync started in live mode is still "
                "running until then.")

    # ── knowledge base (WS2-M7: status + bootstrap + degraded-state) ──
    def _knowledge_base(self) -> QFrame:
        card = card_frame()
        v = QVBoxLayout(card)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(10)
        v.addWidget(section_label("KNOWLEDGE BASE (EC folder in Drive)"))
        self._kb_status = QLabel("")
        self._kb_status.setWordWrap(True)
        self._kb_status.setStyleSheet(
            f"color:{ALMA_TEXT_MID}; font-size:12px; border:none;")
        v.addWidget(self._kb_status)
        row = QHBoxLayout()
        self._kb_toggle_btn = QPushButton("Enable KB")
        self._kb_toggle_btn.setCursor(Qt.PointingHandCursor)
        self._kb_toggle_btn.setStyleSheet(
            f"QPushButton{{background:{ALMA_BG_ELEVATED}; color:{_TEAL}; "
            f"border:1px solid {_TEAL}; border-radius:7px; padding:6px 12px; "
            f"font-size:12px; font-weight:600;}}")
        self._kb_toggle_btn.clicked.connect(self._on_kb_toggle)
        row.addWidget(self._kb_toggle_btn)
        self._kb_bootstrap_btn = QPushButton("Bootstrap EC folder")
        self._kb_bootstrap_btn.setCursor(Qt.PointingHandCursor)
        self._kb_bootstrap_btn.setStyleSheet(
            f"QPushButton{{background:{ALMA_GREEN_DARK}; color:white; border:none; "
            f"border-radius:7px; padding:6px 14px; font-size:12px; font-weight:600;}}")
        self._kb_bootstrap_btn.clicked.connect(self._on_kb_bootstrap)
        row.addWidget(self._kb_bootstrap_btn)
        row.addStretch(1)
        v.addLayout(row)
        self.refresh_kb_status()
        return card

    def refresh_kb_status(self):
        """The ONE degraded-state surface (cross-cutting finding): enumerate
        WHY each background capability is inactive so 'the calendar stopped
        updating' never reads as a silent regression."""
        from src.data.settings_manager import get_section
        try:
            en = get_section("enablement", {}) or {}
        except Exception:  # noqa: BLE001
            en = {}
        kb = en.get("kb") or {}
        lines = []
        demo_now = bool(en.get("demo_mode", True))
        if demo_now != getattr(self, "_boot_demo", demo_now):
            lines.append("• Mode change pending — restart to apply. "
                         "Background monitors still reflect the previous "
                         "mode.")
        if demo_now:
            lines.append("• Demo mode is ON — no live monitor (Asana sync, "
                         "briefs, KB) runs until it's disabled.")
        try:
            from src.data import asana_setup
            if not asana_setup.is_asana_connected():
                lines.append("• Asana: no token — task sync is off.")
        except Exception:  # noqa: BLE001
            pass
        # auth_type-aware: a service-account install is connected without any
        # per-session Reconnect, so is_active() alone would libel it as offline.
        try:
            from src.data.google_access import google_access_ready
            google_ok = google_access_ready()
        except Exception:  # noqa: BLE001
            google_ok = False
        if not google_ok:
            lines.append("• Google: not connected this session — Drive reads, "
                         "KB sync, and uploads wait for Reconnect.")
        if not kb.get("enabled"):
            lines.append("• Knowledge base: disabled.")
        elif not kb.get("ec_folder_id"):
            lines.append("• Knowledge base: enabled but the EC folder isn't "
                         "bootstrapped yet.")
        else:
            counts = ""
            factory = getattr(self, "kb_conn_factory", None)
            if factory is not None:
                try:
                    conn = factory()
                    try:
                        from src.data.kb import store as kb_store
                        n_cards = kb_store.cards_count(conn)
                        n_topics = conn.execute(
                            "SELECT COUNT(*) FROM kb_folders WHERE role='topic' "
                            "AND status='ok'").fetchone()[0]
                        counts = f" — {n_cards} cards across {n_topics} topics"
                    finally:
                        conn.close()
                except Exception:  # noqa: BLE001
                    counts = ""
            lines.append(f"• Knowledge base: active{counts}.")
        self._kb_status.setText("\n".join(lines) or "All background sources active.")
        enabled = bool(kb.get("enabled"))
        self._kb_toggle_btn.setText("Disable KB" if enabled else "Enable KB")
        self._kb_bootstrap_btn.setEnabled(
            enabled and getattr(self, "kb_conn_factory", None) is not None)

    def _on_kb_toggle(self):
        from src.data.settings_manager import get_section, set_section
        cfg = dict(get_section("enablement", {}) or {})
        kb = dict(cfg.get("kb") or {})
        kb["enabled"] = not bool(kb.get("enabled"))
        cfg["kb"] = kb
        set_section("enablement", cfg)
        self.refresh_kb_status()

    def _on_kb_bootstrap(self):
        """Create/verify the EC folder off-thread (requires Google connected)."""
        factory = getattr(self, "kb_conn_factory", None)
        if factory is None:
            return
        import threading
        self._kb_bootstrap_btn.setEnabled(False)
        self._kb_status.setText("Bootstrapping the EC folder…")

        def worker():
            msg = ""
            try:
                from src.data.google_access import google_access_ready
                if not google_access_ready():
                    msg = "Google isn't connected this session — Reconnect first."
                else:
                    conn = factory()
                    try:
                        from src.data.kb import drive_kb
                        res = drive_kb.ensure_ec_root(conn)
                        msg = ("EC folder ready." if res.get("ok")
                               else res.get("message") or res.get("error") or "failed")
                    finally:
                        conn.close()
            except Exception as exc:  # noqa: BLE001
                msg = f"Bootstrap failed: {str(exc)[:120]}"
            # Queued signal — the label update hops back to the Qt thread.
            self._kb_bootstrap_finished.emit(msg)

        threading.Thread(target=worker, daemon=True).start()

    def _kb_bootstrap_done(self, msg: str):
        self._kb_status.setText(msg)
        self._kb_bootstrap_btn.setEnabled(True)
        self.refresh_kb_status()

    def _tab(self, cards: list) -> QScrollArea:
        """Wrap one or more section cards in a top-aligned scroll area so a
        tall sub-tab (Providers, Asana) scrolls instead of compressing."""
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(2, 8, 2, 8)
        v.setSpacing(12)
        for c in cards:
            v.addWidget(c)
        v.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent; border:none;}")
        scroll.setWidget(body)
        return scroll

    # ── operator identity (M1: who is Renn working for) ───────────
    def _identity(self) -> QFrame:
        """Identity card — auto-detect the operator email from Google, override
        here, and resolve the Asana GID for the 'only mine' task filter (M2)."""
        card = card_frame()
        v = QVBoxLayout(card)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(12)
        v.addWidget(section_label("OPERATOR IDENTITY"))
        v.addWidget(self._text(
            "Renn filters your tasks and calendar to you. Auto-detected from your "
            "connected Google account — override here if it's wrong (e.g. a shared "
            "service account).", color=ALMA_TEXT_LIGHT))
        email_row = QHBoxLayout()
        email_row.setSpacing(8)
        email_row.addWidget(self._text("Your email", bold=True))
        self._identity_email = QLineEdit()
        self._identity_email.setPlaceholderText("you@company.com")
        self._identity_email.setFixedHeight(30)
        self._identity_email.setStyleSheet(
            f"QLineEdit{{background:{ALMA_BG_ELEVATED}; border:1px solid {ALMA_BORDER}; "
            f"border-radius:7px; padding:2px 12px; font-size:12.5px; color:{ALMA_TEXT_DARK};}}"
        )
        self._identity_email.editingFinished.connect(self._save_operator_email)
        email_row.addWidget(self._identity_email, 1)
        detect = QPushButton("Auto-detect from Google")
        detect.setCursor(Qt.PointingHandCursor)
        detect.setStyleSheet(
            f"QPushButton{{background:{ALMA_GREEN_DARK}; color:{ALMA_TEXT_ON_DARK}; border:none; "
            f"border-radius:7px; padding:7px 16px; font-size:12px; font-weight:600;}}"
        )
        detect.clicked.connect(self.identity_detect_email_requested.emit)
        email_row.addWidget(detect)
        v.addLayout(email_row)
        gid_row = QHBoxLayout()
        gid_row.setSpacing(8)
        self._identity_asana = self._text("Asana: not resolved", color=ALMA_TEXT_LIGHT)
        gid_row.addWidget(self._identity_asana, 1)
        resolve = QPushButton("Resolve GID")
        resolve.setCursor(Qt.PointingHandCursor)
        resolve.setStyleSheet(
            f"QPushButton{{background:transparent; color:{ALMA_GREEN_DARK}; "
            f"border:1px solid {ALMA_BORDER}; border-radius:7px; padding:6px 14px; "
            f"font-size:12px; font-weight:600;}}"
        )
        resolve.clicked.connect(self.identity_resolve_asana_gid_requested.emit)
        gid_row.addWidget(resolve)
        v.addLayout(gid_row)
        self._identity_you = self._text("", color=ALMA_SUCCESS)
        v.addWidget(self._identity_you)
        self._seed_identity()
        return card

    def _seed_identity(self) -> None:
        from src.data.settings_manager import get_section
        cfg = get_section("enablement", {}) or {}
        email = (cfg.get("operator_email") or cfg.get("detected_email") or "").strip()
        if email:
            self._identity_email.setText(email)
            self._identity_you.setText(f"You are: {email}")
        gid = (cfg.get("operator_asana_gid") or "").strip()
        name = (cfg.get("operator_name") or "").strip()
        if gid:
            self._identity_asana.setText(f"Asana: {name or 'resolved'} · gid {gid}")

    def _save_operator_email(self) -> None:
        from src.data.settings_manager import update_section
        text = self._identity_email.text().strip()
        update_section("enablement", {"operator_email": text})
        self._identity_you.setText(f"You are: {text}" if text else "")

    def set_operator_email(self, email: str) -> None:
        self._identity_email.setText(email or "")
        self._identity_you.setText(f"You are: {email}" if email else "")

    def set_operator_asana_gid(self, gid: str, name: str = "") -> None:
        if gid:
            self._identity_asana.setText(f"Asana: {name or 'resolved'} · gid {gid}")
        else:
            self._identity_asana.setText("Asana: not resolved")

    def operator_email(self) -> str:
        return self._identity_email.text().strip()

    # ── helpers ───────────────────────────────────────────────────
    def _text(self, s, size=12.5, color=None, bold=False):
        lbl = QLabel(s)
        lbl.setStyleSheet(f"color:{color or ALMA_TEXT_MID}; font-size:{size}px; font-weight:{'700' if bold else '400'}; border:none; background:transparent;")
        return lbl

    def _section(self, title, action, on_action=None):
        card = card_frame()
        v = QVBoxLayout(card)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(12)
        head = QHBoxLayout()
        title_lbl = section_label(title)
        card._section_title = title_lbl   # so a derived header can be re-titled
        head.addWidget(title_lbl)
        head.addStretch(1)
        if action:
            b = QPushButton(action)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                f"QPushButton{{background:transparent; color:{ALMA_GREEN_DARK}; border:1px solid {ALMA_BORDER}; "
                f"border-radius:7px; padding:5px 12px; font-size:11.5px; font-weight:600;}}"
            )
            if on_action:
                b.clicked.connect(on_action)
            head.addWidget(b)
        v.addLayout(head)
        return card, v

    def _inset(self):
        f = QFrame()
        f.setStyleSheet(f"QFrame{{background:{ALMA_BG_INSET}; border:none; border-radius:10px;}}")
        return f

    # ── sections ──────────────────────────────────────────────────
    def _intro(self):
        """Page header — title, one-line description, Test connections."""
        row = QHBoxLayout()
        row.setSpacing(10)
        col = QVBoxLayout()
        col.setSpacing(2)
        title = QLabel("Settings")
        title.setStyleSheet(
            f"color:{ALMA_TEXT_DARK}; font-size:19px; font-weight:700; "
            "border:none; background:transparent;")
        col.addWidget(title)
        col.addWidget(self._text(
            "Connections, providers, content sources, and system tools for "
            "the Content Command Center.", color=ALMA_TEXT_LIGHT))
        row.addLayout(col)
        row.addStretch(1)
        b = QPushButton("Test connections")
        b.setStyleSheet(
            f"QPushButton{{background:transparent; color:{ALMA_GREEN_DARK}; border:1px solid {ALMA_BORDER}; "
            f"border-radius:7px; padding:6px 14px; font-size:12px; font-weight:600;}}"
        )
        row.addWidget(b, 0, Qt.AlignVCenter)
        return row

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(
            f"background:{ALMA_BORDER_LIGHT}; border:none;")
        return line

    def _connections(self):
        """Connection health + the workspace basics, one readable card:
        row 1 = live status dots, row 2 = the three workspace choices."""
        card = card_frame()
        v = QVBoxLayout(card)
        v.setContentsMargins(20, 14, 20, 14)
        v.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(section_label("CONNECTIONS"))
        head.addStretch(1)
        head.addWidget(self._text("Poll every"))
        head.addWidget(field("15 min", w=86))
        v.addLayout(head)

        dots = QHBoxLayout()
        dots.setSpacing(10)
        self._conn_widgets: dict[str, tuple] = {}
        for key, name in (("asana", "Asana"), ("drive", "Google Drive"), ("guru", "Guru")):
            dot = QLabel("•")
            dot.setStyleSheet(f"color:{ALMA_TEXT_LIGHT}; font-size:16px; border:none;")
            lbl = self._text(f"{name} —", color=ALMA_TEXT_DARK)
            self._conn_widgets[key] = (dot, lbl, name)
            dots.addWidget(dot)
            dots.addWidget(lbl)
            dots.addSpacing(14)
        dots.addStretch(1)
        v.addLayout(dots)

        v.addWidget(self._divider())

        wk = QHBoxLayout()
        wk.setSpacing(8)
        wk.addWidget(self._text("Assistant provider", bold=True))
        wk.addWidget(self._provider_combo())
        wk.addSpacing(18)
        wk.addWidget(self._text("Start in", bold=True))
        wk.addWidget(self._default_mode_combo())
        wk.addStretch(1)
        v.addLayout(wk)
        return card

    def set_connection_status(self, key: str, ok: bool, detail: str = ""):
        """Host updates a connection dot from a real test_connection result."""
        w = getattr(self, "_conn_widgets", {}).get(key)
        if not w:
            return
        dot, lbl, name = w
        color = ALMA_SUCCESS if ok else ALMA_WARNING
        dot.setStyleSheet(f"color:{color}; font-size:16px; border:none;")
        suffix = "connected" if ok else (detail or "not connected")
        lbl.setText(f"{name} {suffix}")
        lbl.setStyleSheet(f"color:{ALMA_TEXT_DARK}; font-size:12.5px; border:none; background:transparent;")

    def _provider_combo(self):
        combo = QComboBox()
        combo.addItem("Gemini", "gemini")
        combo.addItem("Claude", "claude")
        combo.setFixedHeight(30)
        combo.setStyleSheet(
            f"QComboBox{{background:{ALMA_BG_ELEVATED}; border:1px solid {ALMA_BORDER}; "
            f"border-radius:7px; padding:2px 10px; font-size:12px; color:{ALMA_TEXT_DARK}; min-width:118px;}}"
        )
        from src.data.settings_manager import get_section
        current = (get_section("enablement", {}) or {}).get("provider", "gemini") or "gemini"
        idx = combo.findData(current)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.currentIndexChanged.connect(lambda _i: self._save_provider(combo.currentData()))
        return combo

    def _save_provider(self, provider: str):
        from src.data.settings_manager import get_section, set_section
        cfg = dict(get_section("enablement", {}) or {})
        cfg["provider"] = provider
        set_section("enablement", cfg)

    def _style_guide(self):
        """Style guide card — the tone/formatting input to card generation."""
        self._sg_section = _GuideSection(
            "STYLE GUIDE",
            "Card generation and Renn's revisions follow the active guide. Upload "
            "several — the active one is used; switch or remove them below.",
        )
        self._sg_section.action.connect(self.style_guide_action.emit)
        self._sg_section.activate.connect(self.style_guide_activate.emit)
        self._sg_section.delete_doc.connect(self.style_guide_delete.emit)
        self._sg_section.saved.connect(self.style_guide_saved.emit)
        return self._sg_section

    def _card_template(self):
        """Card/article template card — the heading skeleton every generated
        card follows exactly (alongside the style guide's tone rules)."""
        self._ct_section = _GuideSection(
            "CARD / ARTICLE TEMPLATE",
            "Generated cards reproduce the active template's exact heading "
            "structure — upload the Unified Support Center/Guru article "
            "template (.docx or .md); switch or remove stored templates below.",
        )
        self._ct_section.action.connect(self.card_template_action.emit)
        self._ct_section.activate.connect(self.card_template_activate.emit)
        self._ct_section.delete_doc.connect(self.card_template_delete.emit)
        self._ct_section.saved.connect(self.card_template_saved.emit)
        return self._ct_section

    # host-facing setters (style guide) ─ preserved API
    def set_style_guides(self, guides: list):
        """Render the stored style-guide library (active flagged; switch / delete)."""
        self._sg_section.set_library(guides)

    def set_style_guide_status(self, text: str):
        try:
            self._sg_section.set_status(text)
        except Exception:
            pass

    def set_style_guide_content(self, text: str):
        """Rendered, editable preview of the ACTIVE guide's text."""
        try:
            self._sg_section.set_content(text)
        except Exception:
            pass

    # host-facing setters (card template)
    def set_card_templates(self, templates: list):
        self._ct_section.set_library(templates)

    def set_card_template_status(self, text: str):
        try:
            self._ct_section.set_status(text)
        except Exception:
            pass

    def set_card_template_content(self, text: str):
        try:
            self._ct_section.set_content(text)
        except Exception:
            pass

    def _default_mode_combo(self):
        """Startup-mode choice — same setting as the product Settings page."""
        combo = QComboBox()
        combo.addItem("Product", "product")
        combo.addItem("Enablement", "enablement")
        combo.addItem("Last used", "last")
        combo.setFixedHeight(30)
        combo.setStyleSheet(
            f"QComboBox{{background:{ALMA_BG_ELEVATED}; border:1px solid {ALMA_BORDER}; "
            f"border-radius:7px; padding:2px 10px; font-size:12px; color:{ALMA_TEXT_DARK}; min-width:104px;}}"
        )
        from src.data.settings_manager import get_section
        current = (get_section("app", {}) or {}).get("default_mode", "product")
        idx = combo.findData(current)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.currentIndexChanged.connect(
            lambda _i: self._save_default_mode(combo.currentData())
        )
        return combo

    def _save_default_mode(self, mode: str):
        from src.data.settings_manager import update_section
        try:
            update_section("app", {"default_mode": mode or "product"})
        except Exception:
            pass

    def _asana(self):
        """The Asana board card — rendered ENTIRELY from monitor_sources.

        The header count, every board row, the indicator condition and the field
        mappings come from the host's ``set_asana_boards`` feed. Nothing here is
        a literal describing a board, because a screen that describes a board the
        database does not have is how this feature shipped broken.
        """
        card, v = self._section("ASANA BOARDS", "+ Add board",
                                on_action=self.board_add_requested.emit)
        self._asana_header = getattr(card, "_section_title", None)
        # Connect + AI setup — Renn finds the custom-field / enum-value GIDs for you
        conn_row = QHBoxLayout()
        conn_row.setSpacing(8)
        conn_row.addWidget(self._text("API key", bold=True))
        keyf = QLineEdit()
        keyf.setPlaceholderText("Paste your Asana API key")
        keyf.setEchoMode(QLineEdit.Password)
        keyf.setFixedHeight(30)
        keyf.setStyleSheet(
            f"QLineEdit{{background:{ALMA_BG_ELEVATED}; border:1px solid {ALMA_BORDER}; "
            f"border-radius:7px; padding:2px 12px; font-size:12.5px; color:{ALMA_TEXT_DARK};}}"
        )
        conn_row.addWidget(keyf, 1)
        setup = QPushButton("Set up with Renn")
        setup.setCursor(Qt.PointingHandCursor)
        setup.setStyleSheet(
            f"QPushButton{{background:{ALMA_GREEN_DARK}; color:{ALMA_TEXT_ON_DARK}; border:none; "
            f"border-radius:7px; padding:7px 16px; font-size:12px; font-weight:600;}}"
        )
        setup.clicked.connect(self.asana_setup_requested.emit)
        conn_row.addWidget(setup)
        v.addLayout(conn_row)
        v.addWidget(self._text(
            "Renn finds your projects, custom fields, and enum-value GIDs and fills these in — "
            "no GID hunting. (Renn can only edit these Asana settings.)", color=ALMA_TEXT_LIGHT))
        self._asana_list = QVBoxLayout()
        self._asana_list.setSpacing(10)
        v.addLayout(self._asana_list)
        self._asana_boards: list[dict] = []
        self._render_asana_boards([])
        return card

    # ── Asana boards: host feed → rendered rows ───────────────────
    def set_asana_boards(self, boards: list[dict]):
        """Host feeds ``asana_setup.board_summary(conn)``. The single entry
        point — the card has no other source of board information."""
        self._render_asana_boards(list(boards or []))

    def asana_boards(self) -> list[dict]:
        """What the card is currently showing (tests + host read-back)."""
        return list(self._asana_boards)

    def _render_asana_boards(self, boards: list[dict]):
        self._asana_boards = boards
        self._clear_layout(self._asana_list)
        if self._asana_header is not None:
            n = len(boards)
            self._asana_header.setText(
                "ASANA BOARDS" if not n else
                f"ASANA BOARDS  ·  {n} configured" if n != 1 else
                "ASANA BOARDS  ·  1 configured")
        if not boards:
            # Honest + actionable: an unmapped Asana is the exact state that
            # used to render as "2 configured" over an empty monitor_sources.
            empty = self._text(
                "No Asana boards are mapped, so no Asana tasks will sync — your "
                "Tasks and Calendar stay empty. Press “Set up with Renn” (or "
                "“+ Add board”) to map a board; nothing polls Asana until you do.",
                color=ALMA_TEXT_LIGHT)
            empty.setWordWrap(True)
            self._asana_list.addWidget(empty)
            return
        for b in boards:
            self._asana_list.addWidget(self._asana_board_row(b))

    def _asana_board_row(self, b: dict) -> QFrame:
        sid = str(b.get("source_id") or "")
        box = self._inset()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(16, 12, 16, 12)
        bl.setSpacing(10)

        top = QHBoxLayout()
        top.addWidget(self._text(str(b.get("project_name") or sid), size=14,
                                 color=ALMA_TEXT_DARK, bold=True))
        top.addSpacing(10)
        if b.get("project_url"):
            top.addWidget(self._text(str(b["project_url"]), color=ALMA_INFO))
        top.addStretch(1)
        top.addWidget(self._text("Sync", color=ALMA_TEXT_LIGHT))
        sync = _BoardToggle(bool(b.get("enabled")))
        sync.changed.connect(lambda on, s=sid: self.board_sync_toggled.emit(s, on))
        top.addWidget(sync)
        top.addSpacing(10)
        top.addWidget(self._text("Calendar", color=ALMA_TEXT_LIGHT))
        cal = _BoardToggle(bool(b.get("calendar")))
        cal.changed.connect(lambda on, s=sid: self.board_calendar_toggled.emit(s, on))
        top.addWidget(cal)
        bl.addLayout(top)

        ind = QHBoxLayout()
        ind.setSpacing(10)
        ind.addWidget(self._text("Create task when", bold=True))
        cond = str(b.get("indicator") or "")
        if cond:
            ind.addWidget(pill(cond, "#E4EFE9", ALMA_SUCCESS))
        else:
            ind.addWidget(self._text("no condition mapped — this board creates nothing",
                                     color=ALMA_WARNING))
        ind.addStretch(1)
        bl.addLayout(ind)

        mp = QHBoxLayout()
        mp.setSpacing(8)
        mp.addWidget(self._text("Map fields", bold=True))
        mp.addSpacing(8)
        mp.addWidget(self._text("Priority from"))
        mp.addWidget(field(str(b.get("priority_field") or "—"), w=140))
        mp.addWidget(self._text("Assignee from"))
        mp.addWidget(field(str(b.get("assignee_field") or "—"), w=160))
        mp.addStretch(1)
        bl.addLayout(mp)

        foot = QHBoxLayout()
        foot.setSpacing(10)
        count = int(b.get("task_count") or 0)
        foot.addWidget(self._text(
            f"{count} imported task" + ("" if count == 1 else "s"),
            color=ALMA_TEXT_LIGHT))
        # Ownership is many-to-many: a task multi-homed into two mapped Asana
        # projects is polled — and tracked — by BOTH boards, so it is counted
        # under both and the per-board counts can sum to more than the number
        # of tasks. Say so instead of hiding it behind an arbitrary winner,
        # and say what removal would actually do to those rows.
        shared = int(b.get("shared_task_count") or 0)
        if shared:
            foot.addWidget(self._text(
                f"{shared} also tracked by another board (kept if you remove this one)",
                color=ALMA_TEXT_LIGHT))
        if not b.get("enabled"):
            foot.addWidget(pill("sync paused", "#F6EBDD", ALMA_WARNING))
        if not b.get("calendar"):
            foot.addWidget(self._text("not shown on the calendar", color=ALMA_TEXT_LIGHT))
        if b.get("last_error"):
            foot.addWidget(self._text(f"last error: {b['last_error']}"[:120],
                                      color=ALMA_WARNING))
        foot.addStretch(1)
        rm = QPushButton("Remove")
        rm.setCursor(Qt.PointingHandCursor)
        rm.setStyleSheet(
            f"QPushButton{{background:transparent; color:{ALMA_WARNING}; "
            f"border:1px solid {ALMA_BORDER}; border-radius:7px; padding:4px 12px; "
            f"font-size:11.5px; font-weight:600;}}")
        rm.clicked.connect(lambda _=False, s=sid: self.board_remove_requested.emit(s))
        foot.addWidget(rm)
        bl.addLayout(foot)
        return box

    def _drive(self):
        card, v = self._section("GOOGLE DRIVE FOLDERS  ·  watched", "+ Add folder",
                                on_action=self._on_add_drive_folder)
        warn = QFrame()
        warn.setStyleSheet("QFrame{background:#FBF1E5; border:none; border-radius:7px;}")
        wl = QHBoxLayout(warn)
        wl.setContentsMargins(12, 7, 12, 7)
        bang = QLabel("!")
        bang.setFixedSize(16, 16)
        bang.setStyleSheet(f"background:{ALMA_WARNING}; color:white; border-radius:8px; font-weight:700; font-size:11px;")
        bang.setAlignment(Qt.AlignCenter)
        wl.addWidget(bang)
        wl.addWidget(self._text("Needs drive.readonly — share each folder with the service-account email "
                                "and set enablement.drive.read_enabled (org step).", color=ALMA_WARNING))
        wl.addStretch(1)
        v.addWidget(warn)
        # Model-independent picker entry point: Renn can also open this picker
        # from chat, but that path depends on the model choosing to call the
        # tool — this button works every time.
        browse_row = QHBoxLayout()
        browse_row.setSpacing(10)
        browse = QPushButton("Browse Drive…")
        browse.setCursor(Qt.PointingHandCursor)
        browse.setStyleSheet(
            f"QPushButton{{background:{ALMA_GREEN_DARK}; color:{ALMA_TEXT_ON_DARK}; border:none; "
            f"border-radius:7px; padding:6px 14px; font-size:11.5px; font-weight:600;}}")
        browse.clicked.connect(self._on_browse_drive)
        browse_row.addWidget(browse)
        self._drive_active_lbl = self._text(
            "Active for Renn: none yet — browse and pick a folder.",
            color=ALMA_TEXT_LIGHT)
        browse_row.addWidget(self._drive_active_lbl)
        browse_row.addStretch(1)
        v.addLayout(browse_row)
        self._drive_list = QVBoxLayout()
        self._drive_list.setSpacing(8)
        v.addLayout(self._drive_list)
        self._render_drive_folders([])
        return card

    def _prompt_text(self, title: str, label: str, text: str = ""):
        """A QInputDialog whose OK/Cancel buttons stay legible under this page's
        cascaded cream background (2026-07-22 blank-button fix)."""
        from PySide6.QtWidgets import QInputDialog
        from src.ui.pages.enablement._common import native_dialog_button_qss
        dlg = QInputDialog(self)
        dlg.setWindowTitle(title)
        dlg.setLabelText(label)
        if text:
            dlg.setTextValue(text)
        dlg.setStyleSheet(native_dialog_button_qss())
        ok = dlg.exec()
        return dlg.textValue(), bool(ok)

    def _on_add_drive_folder(self):
        folder_id, ok = self._prompt_text("Add Drive folder", "Google Drive folder ID:")
        folder_id = (folder_id or "").strip()
        if not ok or not folder_id:
            return
        name, _ = self._prompt_text("Add Drive folder", "Display name:", text="Watched folder")
        self.drive_folder_added.emit(folder_id, (name or "Watched folder").strip())

    def _on_browse_drive(self):
        """Open the native Drive folder picker; emit the pick to the host.

        The dialog is cached and reused (not destroyed per open) so a close
        with a listing in flight never tears down a running QThread's parent —
        see DriveFolderPickerDialog's module docstring."""
        from src.ui.dialogs.drive_folder_picker_dialog import DriveFolderPickerDialog
        dlg = getattr(self, "_drive_picker", None)
        if dlg is None:
            dlg = self._drive_picker = DriveFolderPickerDialog(self)
        else:
            dlg.reload()
        from PySide6.QtWidgets import QDialog
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.picked:
            p = dlg.picked
            self.drive_folder_picked.emit(
                p.get("id") or "", p.get("name") or "", p.get("drive_id") or "")

    def set_active_drive_folders(self, folders: list[dict]):
        """Host feeds ``enablement.drive.active_folders`` (the folder(s) Renn
        works from — the state the chat picker's resolve path writes).
        Entries may be dicts OR legacy plain-string ids (hand-wired configs);
        a string entry crashed this label silently on 2026-07-22."""
        norm = []
        for f in (folders or []):
            if isinstance(f, dict):
                if f.get("id"):
                    norm.append(f)
            elif str(f or "").strip():
                norm.append({"id": str(f).strip()})
        folders = norm
        if not folders:
            self._drive_active_lbl.setText(
                "Active for Renn: none yet — browse and pick a folder.")
            return
        shown = ", ".join(
            (f.get("name") or f.get("id")) for f in folders[-2:])
        more = f" (+{len(folders) - 2} more)" if len(folders) > 2 else ""
        self._drive_active_lbl.setText(f"Active for Renn: {shown}{more}")

    def set_drive_folders(self, folders: list[dict]):
        """Host feeds the configured Drive folders (from monitor_sources)."""
        self._render_drive_folders(folders)

    def _render_drive_folders(self, folders: list[dict]):
        self._clear_layout(self._drive_list)
        if not folders:
            self._drive_list.addWidget(self._text(
                "No folders watched yet — click “+ Add folder”.", color=ALMA_TEXT_LIGHT))
            return
        for f in folders:
            row = QFrame()      # QFrame (not bare QWidget) so it won't paint window-gray
            row.setStyleSheet("QFrame{background:transparent; border:none;}")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.addWidget(self._text(f.get("display_name", "folder"), size=13.5, color=ALMA_TEXT_DARK, bold=True))
            rl.addSpacing(10)
            fid = (f.get("config") or {}).get("folder_id", "")
            rl.addWidget(self._text(f"folder · {fid}", color=ALMA_TEXT_LIGHT))
            rl.addStretch(1)
            rl.addWidget(pill(f.get("last_status") or "pending", "#EBEFEA", ALMA_GREEN_DARK))
            self._drive_list.addWidget(row)

    @staticmethod
    def _clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            elif item.layout() is not None:
                SettingsPage._clear_layout(item.layout())

    # ── system tabs (shared widgets, also hosted by product Settings) ──

    def _updates_panel(self):
        """Auto-update / GitHub repo / support & recovery — the shared
        UpdatesPanel, so enablement users can check, install, roll back and
        export crash reports without switching modes."""
        from src.ui.widgets.updates_panel import UpdatesPanel
        self._updates = UpdatesPanel()
        return self._updates

    def _maintenance_panel(self):
        """Memory diagnostics + the database danger zone (typed confirm)."""
        from src.ui.widgets.maintenance_panel import MaintenancePanel
        self._maintenance = MaintenancePanel()
        return self._maintenance

    def _usage_tab(self) -> QWidget:
        """Host for the Renn usage panel.

        Built lazily on first visit once the host wires ``usage_db_factory``
        (a callable returning a DatabaseManager) — a standalone SettingsPage
        keeps the placeholder and never touches a database. The panel shows
        Renn's own metered activity (turns, tokens, CLI-reported cost); the
        scan-centric cost dashboard stays on the product side."""
        host = QWidget()
        v = QVBoxLayout(host)
        v.setContentsMargins(2, 8, 2, 8)
        v.setSpacing(8)
        self._usage_placeholder = QLabel(
            "Usage appears once a warehouse is connected.")
        self._usage_placeholder.setAlignment(Qt.AlignCenter)
        self._usage_placeholder.setStyleSheet(
            f"color:{ALMA_TEXT_LIGHT}; font-size:13px; padding:40px; "
            "border:none; background:transparent;")
        v.addWidget(self._usage_placeholder, 1)
        self._usage_layout = v
        self._usage_dashboard = None
        return host

    def _on_tab_selected(self, index: int):
        if self._tabs.tabText(index) == "Usage":
            self._maybe_build_usage()

    def _maybe_build_usage(self):
        """Swap the Usage placeholder for the live Renn usage panel, once."""
        if self._usage_dashboard is not None:
            return
        factory = getattr(self, "usage_db_factory", None)
        if factory is None:
            return
        try:
            db = factory()
        except Exception:  # noqa: BLE001 — a broken factory keeps the placeholder
            db = None
        if db is None:
            return
        try:
            from src.ui.pages.enablement.usage_tab import RennUsagePanel
            panel = RennUsagePanel(db)
        except Exception as exc:  # noqa: BLE001 — never break Settings over usage
            self._usage_placeholder.setText(
                f"Usage panel unavailable: {str(exc)[:120]}")
            return
        self._usage_placeholder.hide()
        self._usage_layout.addWidget(panel, 1)
        self._usage_dashboard = panel
