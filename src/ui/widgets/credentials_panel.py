"""Shared credentials / LLM-provider panel.

Self-contained: reads and writes `ModelRegistry`, `pat_store`, and
`settings_manager` directly, with NO dependency on the product
`SettingsPage` — critical because in enablement mode the product Settings
page is never constructed (mode-gated mounting). Both Settings pages
embed this widget so the same Claude+Gemini provider config and the same
external-API credentials are editable from either mode and persist to one
shared store.

Sections (selectable via `sections`):
  "llm"      — active model, Gemini API key, Claude API key (behind the
               3-acknowledgment HIPAA/BAA gate), task-routing override, PII.
  "external" — Guru email+PAT, Zendesk subdomain+email+API token, the
               Google service-account file, and the per-user Google OAuth
               "Connect my account" controls.

The panel emits ``settings_changed(dict)`` with flags the host re-emits
(gemini_updated / claude_connected / model_changed / guru_updated /
zendesk_updated / drive_updated / routing_updated / pii_updated) so existing reactions
(e.g. main_window._on_settings_changed → refresh_gemini_status) fire
unchanged. Google OAuth button presses surface as dedicated signals the
host wires to the worker in P5.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from src.ui.theme import (
    ALMA_BG_ELEVATED, ALMA_BORDER, ALMA_BORDER_LIGHT, ALMA_CREAM, ALMA_ERROR,
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_SUCCESS, ALMA_TEXT_DARK,
    ALMA_TEXT_LIGHT, ALMA_TEXT_MID, ALMA_TEXT_ON_DARK, ALMA_WHITE,
)

_CLAUDE_ACKS = [
    ("baa", "A signed BAA is in place between my organization and Anthropic"),
    ("hipaa", "I understand enabling Claude without a BAA may violate HIPAA"),
    ("legal", "I have confirmed this configuration with my compliance or legal team"),
]


class CredentialsPanel(QWidget):
    settings_changed = Signal(dict)
    google_oauth_requested = Signal()            # Connect (interactive)
    google_oauth_reconnect_requested = Signal()  # Reconnect (silent)
    google_oauth_disconnect_requested = Signal()

    def __init__(self, *, sections=("llm", "external"), parent=None):
        super().__init__(parent)
        self._sections = tuple(sections)
        self._claude_acks = {k: False for k, _ in _CLAUDE_ACKS}
        self._oauth_worker = None
        self._sa_probe_worker = None
        self._sa_probed_once = False
        self.setStyleSheet(f"CredentialsPanel {{ background: {ALMA_CREAM}; }}")
        self._build()
        # The panel owns the OAuth worker so the flow works in BOTH Settings
        # hosts without each host re-wiring it.
        if "external" in self._sections:
            self.google_oauth_requested.connect(lambda: self._start_oauth("connect"))
            self.google_oauth_reconnect_requested.connect(
                lambda: self._start_oauth("reconnect"))
            self.google_oauth_disconnect_requested.connect(self._on_oauth_disconnect)
        self.refresh()

    # ── construction ────────────────────────────────────────────────

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(18)
        if "llm" in self._sections:
            outer.addWidget(self._heading("LLM PROVIDERS"))
            outer.addWidget(self._llm_card())
        if "external" in self._sections:
            outer.addWidget(self._heading("EXTERNAL API CREDENTIALS"))
            outer.addWidget(self._guru_card())
            outer.addWidget(self._zendesk_card())
            outer.addWidget(self._google_card())
        outer.addStretch()

    def _heading(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 700; color: {ALMA_TEXT_LIGHT}; "
            "letter-spacing: 1px; background: transparent; border: none;"
        )
        return lbl

    def _card(self) -> QFrame:
        f = QFrame()
        f.setStyleSheet(
            f"QFrame {{ background: {ALMA_BG_ELEVATED}; "
            f"border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 12px; }}"
        )
        return f

    def _field_label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {ALMA_TEXT_MID}; "
            "border: none; background: transparent;"
        )
        return lbl

    def _line_edit(self, *, password=False, placeholder="") -> QLineEdit:
        e = QLineEdit()
        if password:
            e.setEchoMode(QLineEdit.Password)
        e.setPlaceholderText(placeholder)
        e.setStyleSheet(
            f"QLineEdit {{ color: {ALMA_TEXT_DARK}; background: {ALMA_WHITE}; "
            f"border: 1px solid {ALMA_BORDER}; border-radius: 8px; "
            "padding: 8px 12px; font-size: 13px; }}"
        )
        return e

    def _primary_btn(self, text) -> QPushButton:
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(
            f"QPushButton {{ background: {ALMA_GREEN_DARK}; color: {ALMA_TEXT_ON_DARK}; "
            "border: none; border-radius: 8px; padding: 8px 16px; font-size: 12px; "
            "font-weight: 600; } "
            f"QPushButton:disabled {{ background: {ALMA_BORDER}; color: {ALMA_TEXT_LIGHT}; }}"
        )
        return b

    def _secondary_btn(self, text) -> QPushButton:
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(
            f"QPushButton {{ background: {ALMA_BG_ELEVATED}; color: {ALMA_GREEN_DARK}; "
            f"border: 1px solid {ALMA_BORDER}; border-radius: 8px; padding: 7px 14px; "
            "font-size: 12px; font-weight: 600; }"
        )
        return b

    # ── LLM section ─────────────────────────────────────────────────

    def _llm_card(self) -> QFrame:
        card = self._card()
        v = QVBoxLayout(card)
        v.setContentsMargins(20, 18, 20, 18)
        v.setSpacing(14)

        # Active model
        row = QHBoxLayout()
        row.addWidget(self._field_label("Active model"))
        self._model_combo = QComboBox()
        self._model_combo.setStyleSheet(
            f"QComboBox {{ color: {ALMA_TEXT_DARK}; background: {ALMA_WHITE}; "
            f"border: 1px solid {ALMA_BORDER}; border-radius: 8px; padding: 7px 12px; "
            "font-size: 13px; min-width: 220px; }}"
        )
        self._model_combo.currentIndexChanged.connect(self._on_model_changed)
        row.addWidget(self._model_combo, 1)
        v.addLayout(row)

        # Gemini API key
        v.addWidget(self._divider())
        v.addWidget(self._field_label("Gemini API key"))
        grow = QHBoxLayout()
        self._gemini_key = self._line_edit(password=True, placeholder="AIza…")
        grow.addWidget(self._gemini_key, 1)
        gsave = self._primary_btn("Save")
        gsave.clicked.connect(self._on_save_gemini)
        grow.addWidget(gsave)
        v.addLayout(grow)
        self._gemini_status = self._status_label()
        v.addWidget(self._gemini_status)

        # Claude API key (BAA-gated)
        v.addWidget(self._divider())
        self._claude_box = QVBoxLayout()
        self._claude_box.setSpacing(8)
        v.addLayout(self._claude_box)

        # Task-routing override + PII
        v.addWidget(self._divider())
        rr = QHBoxLayout()
        rr.addWidget(self._field_label("Provider routing"))
        self._route_combo = QComboBox()
        self._route_combo.addItem("Auto (per-task defaults)", "")
        self._route_combo.addItem("Force Gemini", "gemini")
        self._route_combo.addItem("Force Claude", "claude")
        self._route_combo.setStyleSheet(self._model_combo.styleSheet())
        self._route_combo.currentIndexChanged.connect(self._on_route_changed)
        rr.addWidget(self._route_combo, 1)
        v.addLayout(rr)

        pr = QHBoxLayout()
        pcol = QVBoxLayout()
        pcol.setSpacing(1)
        pt = self._field_label("Aggressive PII redaction")
        pd = QLabel("Base redaction (emails/phones/SSNs) is always on.")
        pd.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none; background: transparent;"
        )
        pcol.addWidget(pt)
        pcol.addWidget(pd)
        pr.addLayout(pcol, 1)
        self._pii_check = QCheckBox()
        self._pii_check.toggled.connect(self._on_pii_toggled)
        pr.addWidget(self._pii_check)
        v.addLayout(pr)
        return card

    def _render_claude(self):
        while self._claude_box.count():
            item = self._claude_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            elif item.layout() is not None:
                self._clear_layout(item.layout())

        from src.data.pat_store import load_setting
        connected = bool(load_setting("anthropic_api_key", ""))

        head = QHBoxLayout()
        title = QLabel("Claude (Anthropic)")
        title.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {ALMA_TEXT_DARK}; "
            "border: none; background: transparent;"
        )
        head.addWidget(title)
        head.addStretch(1)
        if connected:
            ok = QLabel("Connected")
            ok.setStyleSheet(
                f"font-size: 12px; font-weight: 700; color: {ALMA_SUCCESS}; "
                "border: none; background: transparent;"
            )
            head.addWidget(ok)
            disc = self._secondary_btn("Disconnect")
            disc.clicked.connect(self._on_claude_disconnect)
            head.addWidget(disc)
            self._claude_box.addLayout(head)
            return
        self._claude_box.addLayout(head)

        warn = QLabel(
            "Claude routes support data through Anthropic. A signed BAA is "
            "required for HIPAA. Acknowledge all three to enter a key."
        )
        warn.setWordWrap(True)
        warn.setStyleSheet(
            f"font-size: 12px; color: {ALMA_ERROR}; border: none; background: transparent;"
        )
        self._claude_box.addWidget(warn)

        self._claude_check_widgets = {}
        for key, text in _CLAUDE_ACKS:
            cb = QCheckBox(text)
            cb.setChecked(self._claude_acks.get(key, False))
            cb.setStyleSheet(
                f"QCheckBox {{ font-size: 12px; color: {ALMA_TEXT_MID}; spacing: 9px; "
                "border: none; padding: 2px 0; } "
                "QCheckBox::indicator { width: 14px; height: 14px; }"
            )
            cb.toggled.connect(lambda chk, k=key: self._on_claude_ack(k, chk))
            self._claude_check_widgets[key] = cb
            self._claude_box.addWidget(cb)

        all_checked = all(self._claude_acks.values())
        krow = QHBoxLayout()
        self._claude_key = self._line_edit(password=True, placeholder="sk-ant-api03-…")
        self._claude_key.setEnabled(all_checked)
        krow.addWidget(self._claude_key, 1)
        self._claude_save = self._primary_btn("Save key")
        self._claude_save.setEnabled(all_checked)
        self._claude_save.clicked.connect(self._on_save_claude)
        krow.addWidget(self._claude_save)
        self._claude_box.addLayout(krow)
        if not all_checked:
            hint = QLabel("All acknowledgments must be checked first.")
            hint.setStyleSheet(
                f"font-size: 11px; font-style: italic; color: {ALMA_TEXT_LIGHT}; "
                "border: none; background: transparent;"
            )
            self._claude_box.addWidget(hint)

    # ── External: Guru ──────────────────────────────────────────────

    def _guru_card(self) -> QFrame:
        card = self._card()
        v = QVBoxLayout(card)
        v.setContentsMargins(20, 18, 20, 18)
        v.setSpacing(10)
        v.addWidget(self._field_label("Guru"))
        self._guru_email = self._line_edit(placeholder="you@company.com")
        v.addWidget(self._guru_email)
        self._guru_token = self._line_edit(password=True, placeholder="Guru API token")
        v.addWidget(self._guru_token)
        row = QHBoxLayout()
        self._guru_status = self._status_label()
        row.addWidget(self._guru_status, 1)
        save = self._primary_btn("Save Guru credentials")
        save.clicked.connect(self._on_save_guru)
        row.addWidget(save)
        v.addLayout(row)
        return card

    # ── External: Zendesk ───────────────────────────────────────────

    def _zendesk_card(self) -> QFrame:
        """Zendesk credentials, editable from BOTH modes.

        Before this card the only writer was the product-mode Source Monitor
        connection tab, so an enablement operator had to switch app modes to
        change a Zendesk setting. Deliberately NOT here: the view id and the
        TRC field mapping — both are ticket-ingestion config and stay owned by
        that tab."""
        card = self._card()
        v = QVBoxLayout(card)
        v.setContentsMargins(20, 18, 20, 18)
        v.setSpacing(10)
        v.addWidget(self._field_label("Zendesk"))

        scope = QLabel(
            "Read-only. Used to pull Help Center content; the app never "
            "writes to Zendesk."
        )
        scope.setWordWrap(True)
        scope.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none; background: transparent;"
        )
        v.addWidget(scope)

        self._zendesk_subdomain = self._line_edit(placeholder="d3v-acgshelp")
        v.addWidget(self._zendesk_subdomain)
        self._zendesk_email = self._line_edit(placeholder="you@company.com")
        v.addWidget(self._zendesk_email)
        self._zendesk_token = self._line_edit(
            password=True, placeholder="Zendesk API token")
        v.addWidget(self._zendesk_token)

        row = QHBoxLayout()
        self._zendesk_status = self._status_label()
        self._zendesk_status.setWordWrap(True)
        row.addWidget(self._zendesk_status, 1)
        save = self._primary_btn("Save Zendesk credentials")
        save.clicked.connect(self._on_save_zendesk)
        row.addWidget(save)
        v.addLayout(row)
        return card

    # ── External: Google ────────────────────────────────────────────

    def _google_card(self) -> QFrame:
        card = self._card()
        v = QVBoxLayout(card)
        v.setContentsMargins(20, 18, 20, 18)
        v.setSpacing(10)
        v.addWidget(self._field_label("Google Drive"))

        sa_lbl = QLabel(
            "Service account (shared/admin): a JSON key with drive.readonly, "
            "with folders shared to the service-account email."
        )
        sa_lbl.setWordWrap(True)
        sa_lbl.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none; background: transparent;"
        )
        v.addWidget(sa_lbl)
        sarow = QHBoxLayout()
        self._sa_path = self._line_edit(placeholder="path/to/service-account.json")
        sarow.addWidget(self._sa_path, 1)
        browse = self._secondary_btn("Browse…")
        browse.clicked.connect(self._on_browse_sa)
        sarow.addWidget(browse)
        sasave = self._primary_btn("Save")
        sasave.clicked.connect(self._on_save_sa)
        sarow.addWidget(sasave)
        v.addLayout(sarow)

        # The service account's OWN status. Without this the only status text
        # on the card is the OAuth line below, which reads as a failure on any
        # build without a bundled GCP client — so a perfectly healthy service
        # account looked broken. See DriveProbeWorker.
        self._sa_status = self._status_label()
        self._sa_status.setWordWrap(True)
        v.addWidget(self._sa_status)

        v.addWidget(self._divider())
        oa_lbl = QLabel(
            "Or connect your own Google account (per-user). The authorization "
            "is required again each launch for security."
        )
        oa_lbl.setWordWrap(True)
        oa_lbl.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none; background: transparent;"
        )
        v.addWidget(oa_lbl)

        oarow = QHBoxLayout()
        self._google_status = self._status_label()
        oarow.addWidget(self._google_status, 1)
        self._oauth_connect = self._primary_btn("Connect my Google account")
        self._oauth_connect.clicked.connect(self.google_oauth_requested.emit)
        oarow.addWidget(self._oauth_connect)
        self._oauth_reconnect = self._secondary_btn("Reconnect")
        self._oauth_reconnect.clicked.connect(self.google_oauth_reconnect_requested.emit)
        self._oauth_reconnect.setVisible(False)
        oarow.addWidget(self._oauth_reconnect)
        self._oauth_disconnect = self._secondary_btn("Disconnect")
        self._oauth_disconnect.clicked.connect(self.google_oauth_disconnect_requested.emit)
        self._oauth_disconnect.setVisible(False)
        oarow.addWidget(self._oauth_disconnect)
        v.addLayout(oarow)

        client_row = QHBoxLayout()
        client_row.addWidget(self._field_label("Custom OAuth client (optional)"))
        client_row.addStretch(1)
        client_btn = self._secondary_btn("Use my own GCP client…")
        client_btn.clicked.connect(self._on_browse_oauth_client)
        client_row.addWidget(client_btn)
        v.addLayout(client_row)
        return card

    # ── shared bits ─────────────────────────────────────────────────

    def _divider(self) -> QFrame:
        d = QFrame()
        d.setFixedHeight(1)
        d.setStyleSheet(f"background: {ALMA_BORDER_LIGHT}; border: none;")
        return d

    def _status_label(self) -> QLabel:
        lbl = QLabel("")
        lbl.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none; background: transparent;"
        )
        return lbl

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            elif item.layout() is not None:
                self._clear_layout(item.layout())

    # ── data load ───────────────────────────────────────────────────

    def refresh(self):
        from src.data.pat_store import load_setting
        from src.data.settings_manager import get_section
        if "llm" in self._sections:
            self._refresh_models()
            self._render_claude()
            routing = get_section("ai", {}).get("task_routing", {}) or {}
            idx = self._route_combo.findData(routing.get("override_all", ""))
            self._route_combo.blockSignals(True)
            self._route_combo.setCurrentIndex(idx if idx >= 0 else 0)
            self._route_combo.blockSignals(False)
            pii = (get_section("gemini", {}) or {}).get("pii_redaction", True)
            self._pii_check.blockSignals(True)
            self._pii_check.setChecked(bool(pii))
            self._pii_check.blockSignals(False)
        if "external" in self._sections:
            self._guru_email.setText(load_setting("guru_email", "") or "")
            self._guru_token.setText(load_setting("guru_api_token", "") or "")
            self._load_zendesk()
            drive = (get_section("enablement", {}) or {}).get("drive") or {}
            self._sa_path.setText(drive.get("credentials_path", "") or "")
            stored, active = self._google_state()
            self._render_google_state(stored, active)

    def _load_zendesk(self):
        """Populate the Zendesk card from the store.

        The token field is left BLANK on purpose — the secret is never echoed
        back into a widget. The status line says a token exists instead, and
        `_on_save_zendesk` reads a blank field as "keep what is stored", so the
        blank cannot be saved back over a working token."""
        from src.data.zendesk_client import ZendeskClient
        try:
            sub, email, key, _view_id = ZendeskClient.load_credentials()
        except Exception:
            return
        self._zendesk_subdomain.setText(sub or "")
        self._zendesk_email.setText(email or "")
        self._zendesk_token.clear()
        self._zendesk_status.setText(
            "Token saved — leave blank to keep it." if key else "")

    @staticmethod
    def _google_state() -> tuple[bool, bool]:
        """(stored, active) from google_oauth — guarded so the panel works
        before the OAuth engine module lands / when its deps are absent."""
        try:
            from src.data import google_oauth
            return google_oauth.has_stored_credentials(), google_oauth.is_active()
        except Exception:
            return False, False

    def _refresh_models(self):
        try:
            from src.llm.model_registry import ModelRegistry
            registry = ModelRegistry.instance()
            available = registry.available()
            active = registry.active()
        except Exception:
            available, active = [], None
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        for m in available:
            self._model_combo.addItem(m.display_name, m.id)
        if active:
            i = self._model_combo.findData(active.id)
            if i >= 0:
                self._model_combo.setCurrentIndex(i)
        self._model_combo.blockSignals(False)

    def _render_google_state(self, stored: bool, active: bool):
        if not hasattr(self, "_oauth_connect"):
            return
        if active:
            self._google_status.setText("Connected this session.")
            self._oauth_connect.setVisible(False)
            self._oauth_reconnect.setVisible(False)
            self._oauth_disconnect.setVisible(True)
        elif stored:
            self._google_status.setText("Authorized but inactive — Reconnect to use this session.")
            self._oauth_connect.setVisible(False)
            self._oauth_reconnect.setVisible(True)
            self._oauth_disconnect.setVisible(True)
        else:
            self._google_status.setText("Not connected.")
            self._oauth_connect.setVisible(True)
            self._oauth_reconnect.setVisible(False)
            self._oauth_disconnect.setVisible(False)

    def set_google_status(self, connected: bool, detail: str = ""):
        stored, _ = self._google_state()
        self._render_google_state(stored, connected)
        if detail:
            self._google_status.setText(detail)

    # ── handlers ────────────────────────────────────────────────────

    def _on_model_changed(self, idx):
        if idx < 0:
            return
        model_id = self._model_combo.currentData()
        if not model_id:
            return
        try:
            from src.llm.model_registry import ModelRegistry
            ModelRegistry.instance().set_active(model_id)
        except Exception:
            pass
        self.settings_changed.emit({"model_changed": True})

    def _on_save_gemini(self):
        from src.data.pat_store import save_setting
        key = self._gemini_key.text().strip()
        if not key:
            return
        ok = save_setting("gemini_api_key", key)
        self._gemini_status.setText("Saved." if ok else "Keyring write failed.")
        self.settings_changed.emit({"gemini_updated": True})

    def _on_claude_ack(self, key, checked):
        self._claude_acks[key] = checked
        self._render_claude()

    def _on_save_claude(self):
        from src.data.pat_store import save_setting
        # Authoritative HIPAA/BAA gate lives HERE, on the model state — not
        # only on widget enabled-state — so a direct slot call or a
        # force-enabled field can't persist the key without all three
        # acknowledgments.
        if not all(self._claude_acks.values()):
            self._render_claude()
            return
        key = self._claude_key.text().strip()
        if not key:
            return
        save_setting("anthropic_api_key", key)
        try:
            from src.llm.model_registry import ModelRegistry
            registry = ModelRegistry.instance()
            for m in registry.all_models():
                if m.provider == "claude":
                    registry.enable(m.id)
        except Exception:
            pass
        self._claude_acks = {k: False for k, _ in _CLAUDE_ACKS}
        self._render_claude()
        self._refresh_models()
        self.settings_changed.emit({"claude_connected": True})

    def _on_claude_disconnect(self):
        from src.data.pat_store import save_setting
        save_setting("anthropic_api_key", "")
        try:
            from src.llm.model_registry import ModelRegistry
            registry = ModelRegistry.instance()
            for m in registry.all_models():
                if m.provider == "claude":
                    registry.disable(m.id)
        except Exception:
            pass
        self._render_claude()
        self._refresh_models()
        self.settings_changed.emit({"claude_disconnected": True})

    def _on_route_changed(self, idx):
        from src.data.settings_manager import get_section, update_section
        value = self._route_combo.currentData() or ""
        routing = dict(get_section("ai", {}).get("task_routing", {}) or {})
        routing["override_all"] = value
        update_section("ai", {"task_routing": routing})
        self.settings_changed.emit({"routing_updated": True})

    def _on_pii_toggled(self, checked):
        from src.data.settings_manager import get_section, set_section
        cfg = dict(get_section("gemini", {}) or {})
        cfg["pii_redaction"] = bool(checked)
        set_section("gemini", cfg)
        self.settings_changed.emit({"pii_updated": True})

    def _on_save_guru(self):
        from src.data.guru_client import GuruClient
        email = self._guru_email.text().strip()
        token = self._guru_token.text().strip()
        ok = GuruClient.save_credentials(email, token)
        self._guru_status.setText("Saved." if ok else "Save failed.")
        self.settings_changed.emit({"guru_updated": True})

    def _on_save_zendesk(self):
        from src.data.zendesk_client import ZendeskClient
        sub = self._zendesk_subdomain.text().strip()
        email = self._zendesk_email.text().strip()
        token = self._zendesk_token.text().strip()
        # save_credentials writes all four keys, so the view id has to be read
        # and handed straight back: it belongs to the Source Monitor's ticket
        # ingestion, and passing the default "" from here would silently blank
        # a view an operator configured in product mode.
        _sub, _email, stored_key, view_id = ZendeskClient.load_credentials()
        # A blank token means "leave it alone" — the field is never populated
        # with the stored secret, so treating blank as an erase would destroy a
        # working credential every time someone corrects a typo in the email.
        if not token:
            token = stored_key or ""
        missing = [name for name, value in (
            ("subdomain", sub), ("email", email), ("API token", token),
        ) if not value]
        if missing:
            self._zendesk_status.setText(
                f"Missing {', '.join(missing)} — nothing saved.")
            return
        ok = ZendeskClient.save_credentials(sub, email, token, view_id=view_id)
        self._zendesk_token.clear()
        self._zendesk_status.setText("Saved." if ok else "Save failed.")
        self.settings_changed.emit({"zendesk_updated": True})

    def _on_browse_sa(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select service-account JSON", "", "JSON (*.json);;All files (*)")
        if path:
            self._sa_path.setText(path)

    def _on_save_sa(self):
        from src.data.settings_manager import get_section, set_section
        cfg = dict(get_section("enablement", {}) or {})
        drive = dict(cfg.get("drive") or {})
        drive["credentials_path"] = self._sa_path.text().strip()
        drive.setdefault("read_enabled", True)
        # Assign, do NOT setdefault: an operator who once connected via OAuth
        # has auth_type='oauth_user' persisted, and setdefault would leave it
        # there — DriveReader.is_configured() would then take the OAuth branch
        # and report "not configured" for a service-account key that is fine.
        drive["auth_type"] = "service_account"
        cfg["drive"] = drive
        set_section("enablement", cfg)
        self.settings_changed.emit({"drive_updated": True})
        # Only when the card is on screen: the probe is a rendering affordance,
        # and a headless caller (tests, a programmatic save) must not be left
        # holding a live network QThread it never asked for.
        if self.isVisible():
            self._probe_sa()

    # ── service-account probe (off-thread) ──────────────────────────

    def showEvent(self, event):
        """Probe when the card is actually SHOWN — never from __init__/refresh.

        __init__ calls refresh(), so probing there starts a network QThread on
        every construction. Tests construct this panel without a running event
        loop and the still-live thread crashed the interpreter at teardown
        (0xC0000409). The OAuth worker beside it has always been user-action
        only for the same reason; this follows that pattern."""
        super().showEvent(event)
        if "external" in self._sections and not self._sa_probed_once:
            self._sa_probed_once = True
            self._probe_sa()

    def _probe_sa(self):
        """Kick the off-thread Drive probe and render its verdict. Single-flight."""
        if getattr(self, "_sa_status", None) is None:
            return
        worker = getattr(self, "_sa_probe_worker", None)
        if worker is not None:
            try:
                if worker.isRunning():
                    return
            except RuntimeError:
                # PySide wrapper outliving its deleted C++ object. Belt to
                # _on_sa_probe's braces: signal delivery order is not something
                # to bet a native crash on, so never dereference a stale
                # wrapper — just replace it.
                self._sa_probe_worker = None
        self._sa_status.setText("Checking…")
        from src.ui.widgets.drive_probe_worker import DriveProbeWorker
        # Parented so Qt owns it, and torn down on completion — a QThread that
        # outlives the widget is a native-crash risk, not a leak.
        self._sa_probe_worker = DriveProbeWorker(self)
        self._sa_probe_worker.finished.connect(self._on_sa_probe)
        self._sa_probe_worker.finished.connect(self._sa_probe_worker.deleteLater)
        self._sa_probe_worker.start()

    def _on_sa_probe(self, result: dict):
        # Drop the reference BEFORE the queued deleteLater runs. Holding a
        # Python wrapper around a deleted C++ QThread is what raised
        # "Internal C++ object (DriveProbeWorker) already deleted" on the
        # second probe; connection order puts this slot ahead of deleteLater.
        self._sa_probe_worker = None
        self._sa_status.setText(self._format_sa_status(result))

    @staticmethod
    def _format_sa_status(result: dict) -> str:
        """Render the probe verdict. Pure + static so it is table-testable
        without Qt, a network, or a key file."""
        if not isinstance(result, dict):
            return ""
        account = str(result.get("account") or "")
        if not result.get("ok"):
            detail = str(result.get("detail") or "")
            return f"Not connected — {detail}" if detail else ""
        count = int(result.get("count") or 0)
        if count == 0:
            # The important case: authentication SUCCEEDED and the account can
            # simply see nothing. Naming the address to share to is the whole
            # point — a bare "0 files" reads as a broken key.
            tail = f" Share a folder with {account} to give it access." if account else ""
            return "Connected — service account · no files shared with it yet." + tail
        shown = f"{count}+" if result.get("capped") else str(count)
        noun = "file" if count == 1 and not result.get("capped") else "files"
        tail = f" · {account}" if account else ""
        return f"Connected — service account · {shown} {noun} visible{tail}"

    # ── Google OAuth flow (off-thread) ──────────────────────────────

    def _start_oauth(self, mode: str):
        if self._oauth_worker is not None and self._oauth_worker.isRunning():
            return  # single-flight
        if mode == "connect":
            from src.data import google_oauth
            if not google_oauth.have_client():
                self._google_status.setText(
                    "No OAuth client — add your own GCP client below first.")
                return
        from src.ui.widgets.google_oauth_worker import GoogleOAuthWorker
        self._oauth_connect.setEnabled(False)
        self._oauth_reconnect.setEnabled(False)
        self._google_status.setText(
            "Opening your browser…" if mode == "connect" else "Reconnecting…")
        self._oauth_worker = GoogleOAuthWorker(mode=mode)
        self._oauth_worker.finished.connect(self._on_oauth_finished)
        self._oauth_worker.error.connect(self._on_oauth_error)
        self._oauth_worker.start()

    def _on_oauth_finished(self, record: dict):
        self._oauth_connect.setEnabled(True)
        self._oauth_reconnect.setEnabled(True)
        if record:  # connect → store the new record (also flips _active via reconnect)
            from src.data import google_oauth
            if not google_oauth.store_credentials(record):
                self._google_status.setText("Could not save the authorization.")
                return
            google_oauth.reconnect()  # activate this session
        stored, active = self._google_state()
        self._render_google_state(stored, active)
        self.settings_changed.emit({"drive_updated": True})

    def _on_oauth_error(self, msg: str):
        self._oauth_connect.setEnabled(True)
        self._oauth_reconnect.setEnabled(True)
        self._google_status.setText(msg or "Authorization failed.")

    def _on_oauth_disconnect(self):
        from src.data import google_oauth
        ok = google_oauth.forget()
        stored, active = self._google_state()
        self._render_google_state(stored, active)
        if not ok and stored:
            self._google_status.setText("Disconnect failed — the credential is still stored.")
        self.settings_changed.emit({"drive_updated": True})

    def _on_browse_oauth_client(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select OAuth client JSON", "", "JSON (*.json);;All files (*)")
        if not path:
            return
        from src.data.settings_manager import get_section, set_section
        cfg = dict(get_section("enablement", {}) or {})
        google = dict(cfg.get("google") or {})
        google["oauth_client_path"] = path
        cfg["google"] = google
        set_section("enablement", cfg)
        self.settings_changed.emit({"drive_updated": True})
