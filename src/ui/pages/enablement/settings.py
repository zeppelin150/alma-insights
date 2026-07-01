"""Enablement Settings — the ETL source config (Asana / Drive / Guru)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QTabWidget, QVBoxLayout, QWidget,
)

from src.ui.pages.enablement._common import card_frame, field, pill, section_label, toggle
from src.ui.theme import (
    ALMA_BG_ELEVATED, ALMA_BG_INSET, ALMA_BORDER, ALMA_CREAM, ALMA_GREEN_DARK,
    ALMA_GREEN_LIGHT, ALMA_INFO, ALMA_SUCCESS, ALMA_TEXT_DARK, ALMA_TEXT_LIGHT, ALMA_TEXT_MID,
    ALMA_TEXT_ON_DARK, ALMA_WARNING,
)

from src.ui.theme import ALMA_ACCENT_TEAL as _TEAL  # noqa: E402


class SettingsPage(QWidget):
    """Operator config for the Extract sources — persisted to monitor_sources."""

    asana_setup_requested = Signal()       # "Set up with Renn" clicked
    drive_folder_added = Signal(str, str)  # (folder_id, display_name) from "+ Add folder"
    style_guide_action = Signal(str)       # "paste" | "upload" | "drive" | "clear"
    style_guide_activate = Signal(str)     # doc_id → make this stored guide active
    style_guide_delete = Signal(str)       # doc_id → delete this stored guide
    identity_detect_email_requested = Signal()       # "Auto-detect from Google"
    identity_resolve_asana_gid_requested = Signal()  # "Resolve GID"

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
        self._tabs = QTabWidget()
        self._tabs.setObjectName("AnalysisTab")
        self._tabs.addTab(self._tab([self._connections()]), "Connections")
        self._tabs.addTab(self._tab([self._identity(), self.credentials]), "Providers")
        self._tabs.addTab(self._tab([self._asana(), self._drive()]), "Sources")
        self._tabs.addTab(self._tab([self._style_guide()]), "Style Guide")
        outer.addWidget(self._tabs, 1)

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
        head.addWidget(section_label(title))
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
        row = QHBoxLayout()
        row.addWidget(self._text("ETL Sources — Extract › Transform › Load.  Configure where tasks come from.", color=ALMA_TEXT_LIGHT))
        row.addStretch(1)
        b = QPushButton("Test connections")
        b.setStyleSheet(
            f"QPushButton{{background:transparent; color:{ALMA_GREEN_DARK}; border:1px solid {ALMA_BORDER}; "
            f"border-radius:7px; padding:6px 14px; font-size:12px; font-weight:600;}}"
        )
        row.addWidget(b)
        return row

    def _connections(self):
        card = card_frame()
        h = QHBoxLayout(card)
        h.setContentsMargins(20, 14, 20, 14)
        h.setSpacing(10)
        h.addWidget(section_label("CONNECTIONS"))
        h.addSpacing(12)
        self._conn_widgets: dict[str, tuple] = {}
        for key, name in (("asana", "Asana"), ("drive", "Google Drive"), ("guru", "Guru")):
            dot = QLabel("•")
            dot.setStyleSheet(f"color:{ALMA_TEXT_LIGHT}; font-size:16px; border:none;")
            lbl = self._text(f"{name} —", color=ALMA_TEXT_DARK)
            self._conn_widgets[key] = (dot, lbl, name)
            h.addWidget(dot)
            h.addWidget(lbl)
            h.addSpacing(14)
        h.addStretch(1)
        h.addWidget(self._text("Poll every"))
        h.addWidget(field("15 min", w=86))
        h.addWidget(self._text("Provider"))
        h.addWidget(self._provider_combo())
        h.addWidget(self._text("Start in"))
        h.addWidget(self._default_mode_combo())
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
        """Style guide card — the first-class input to card generation."""
        card, v = self._section(
            "STYLE GUIDE", "Paste…",
            lambda: self.style_guide_action.emit("paste"),
        )
        row = QHBoxLayout()
        row.setSpacing(8)
        self._style_guide_status = self._text("Not set", color=ALMA_TEXT_LIGHT)
        row.addWidget(self._style_guide_status)
        row.addStretch(1)
        _btn_css = (
            f"QPushButton{{background:{ALMA_BG_ELEVATED}; color:{ALMA_TEXT_DARK}; "
            f"border:1px solid {ALMA_BORDER}; border-radius:7px; padding:5px 12px; "
            f"font-size:11px; font-weight:600;}}"
        )
        upload = QPushButton("Upload…")
        upload.setCursor(Qt.PointingHandCursor)
        upload.setStyleSheet(_btn_css)
        upload.clicked.connect(lambda: self.style_guide_action.emit("upload"))
        row.addWidget(upload)
        from_drive = QPushButton("From Drive…")
        from_drive.setCursor(Qt.PointingHandCursor)
        from_drive.setStyleSheet(_btn_css)
        from_drive.clicked.connect(lambda: self.style_guide_action.emit("drive"))
        row.addWidget(from_drive)
        clear = QPushButton("Clear")
        clear.setCursor(Qt.PointingHandCursor)
        clear.setStyleSheet(
            f"QPushButton{{background:transparent; color:{ALMA_TEXT_LIGHT}; "
            f"border:none; padding:5px 8px; font-size:11px; font-weight:600;}}"
        )
        clear.clicked.connect(lambda: self.style_guide_action.emit("clear"))
        row.addWidget(clear)
        v.addLayout(row)
        hint = self._text(
            "Card generation and Renn's revisions follow the active guide. Upload "
            "several — the active one is used; switch or remove them below.",
            color=ALMA_TEXT_LIGHT,
        )
        v.addWidget(hint)
        self._sg_library = QVBoxLayout()
        self._sg_library.setSpacing(4)
        v.addLayout(self._sg_library)
        return card

    def set_style_guides(self, guides: list):
        """Render the stored style-guide library (active flagged; switch / delete)."""
        lib = getattr(self, "_sg_library", None)
        if lib is None:
            return
        while lib.count():
            item = lib.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for g in guides or []:
            lib.addWidget(self._sg_row(g))

    def _sg_row(self, g: dict) -> QWidget:
        doc_id = g.get("doc_id", "")
        name = (g.get("name", "") or "Untitled").replace("[STYLE-GUIDE]", "").strip()
        active = bool(g.get("active"))
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
        chars = g.get("chars")
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
            act.clicked.connect(lambda _=False, d=doc_id: self.style_guide_activate.emit(d))
            h.addWidget(act)
        rm = QPushButton("Delete")
        rm.setCursor(Qt.PointingHandCursor)
        rm.setStyleSheet(
            f"QPushButton{{background:transparent; color:{ALMA_TEXT_LIGHT}; border:none; "
            f"font-size:10.5px; font-weight:600;}}")
        rm.clicked.connect(lambda _=False, d=doc_id: self.style_guide_delete.emit(d))
        h.addWidget(rm)
        return frame

    def set_style_guide_status(self, text: str):
        try:
            self._style_guide_status.setText(text)
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
        card, v = self._section("ASANA BOARDS  ·  2 configured", "+ Add board")
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
        # expanded board
        b1 = self._inset()
        bl = QVBoxLayout(b1)
        bl.setContentsMargins(16, 12, 16, 12)
        bl.setSpacing(10)
        top = QHBoxLayout()
        top.addWidget(self._text("Enablement Requests", size=14, color=ALMA_TEXT_DARK, bold=True))
        top.addSpacing(10)
        top.addWidget(self._text("app.asana.com/0/120…84", color=ALMA_INFO))
        top.addStretch(1)
        top.addWidget(toggle(True))
        bl.addLayout(top)
        ind = QHBoxLayout()
        ind.setSpacing(10)
        ind.addWidget(self._text("Create task when", bold=True))
        ind.addWidget(pill("Assigned Team  =  Enablement", "#E4EFE9", ALMA_SUCCESS))
        ind.addWidget(self._text("+ add condition", color=ALMA_TEXT_LIGHT))
        ind.addStretch(1)
        bl.addLayout(ind)
        mp = QHBoxLayout()
        mp.setSpacing(8)
        mp.addWidget(self._text("Map fields", bold=True))
        mp.addSpacing(8)
        mp.addWidget(self._text("Priority from"))
        mp.addWidget(field("Urgency", w=110))
        mp.addWidget(self._text("Assignee from"))
        mp.addWidget(field("Assigned People", w=150))
        mp.addWidget(self._text("Resolved: J. Rivera, M. Chen, A. Osei  (+4)", color=ALMA_TEXT_LIGHT))
        mp.addStretch(1)
        bl.addLayout(mp)
        v.addWidget(b1)
        # collapsed board
        b2 = QHBoxLayout()
        b2.addWidget(self._text("Launch Coordination", size=13.5, color=ALMA_TEXT_DARK, bold=True))
        b2.addSpacing(12)
        b2.addWidget(pill("Assigned Team = Enablement", "#EBEFEA", ALMA_GREEN_DARK))
        b2.addWidget(pill("Urgency to priority", "#EBEFEA", ALMA_GREEN_DARK))
        b2.addStretch(1)
        b2.addWidget(toggle(True))
        v.addLayout(b2)
        return card

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
        self._drive_list = QVBoxLayout()
        self._drive_list.setSpacing(8)
        v.addLayout(self._drive_list)
        self._render_drive_folders([])
        return card

    def _on_add_drive_folder(self):
        from PySide6.QtWidgets import QInputDialog
        folder_id, ok = QInputDialog.getText(self, "Add Drive folder", "Google Drive folder ID:")
        folder_id = (folder_id or "").strip()
        if not ok or not folder_id:
            return
        name, _ = QInputDialog.getText(self, "Add Drive folder", "Display name:", text="Watched folder")
        self.drive_folder_added.emit(folder_id, (name or "Watched folder").strip())

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

    def _guru(self):
        card, v = self._section("GURU CARDS  ·  watch & publish", "+ Add card")
        v.addWidget(self._text("Watch cards (product-update posts via GitHub Action)", bold=True))
        chips = QHBoxLayout()
        chips.setSpacing(8)
        for cid in ("card 4a1f…", "card 9c2b…", "card 77fa…"):
            chips.addWidget(pill(cid, "#DCEFEC", _TEAL))
        chips.addStretch(1)
        v.addLayout(chips)
        pub = QHBoxLayout()
        pub.setSpacing(8)
        pub.addWidget(self._text("Publish new cards to", bold=True))
        pub.addWidget(field("Provider Enablement", w=200, strong=True))
        pub.addSpacing(16)
        pub.addWidget(self._text("Draft generator", bold=True))
        pub.addWidget(field("enablement_card_gen", w=180))
        pub.addStretch(1)
        v.addLayout(pub)
        return card
