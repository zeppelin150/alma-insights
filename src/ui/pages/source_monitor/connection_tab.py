"""
Connection tab — Zendesk credentials + TRC field mapping.

Functionally identical to the connection tab in the original
``source_monitor_page.py``; this file just isolates it from the rest
of the page so the shell stays small.

Public surface
──────────────
- ``ConnectionTab(parent)`` — the QWidget
- ``ConnectionTab.connection_changed`` Signal — fires after
  Save & Connect succeeds; the page shell connects this through to
  ``MainWindow`` to rewire the live monitor
- ``ConnectionTab.disconnect_requested`` Signal — fires after Disconnect
  is confirmed; the shell stops the monitor
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.ui.pages.source_monitor._styles import (
    CARD_STYLE,
    DANGER_BTN,
    FIELD_STYLE,
    GHOST_BTN,
    LBL_STYLE,
    PRIMARY_BTN,
)
from src.ui.theme import (
    ALMA_BORDER_LIGHT,
    ALMA_ERROR,
    ALMA_SUCCESS,
    ALMA_TEXT_DARK,
    ALMA_TEXT_LIGHT,
    ALMA_TEXT_MID,
    ALMA_WARNING,
    apply_card_shadow,
)

logger = logging.getLogger("alma.source_monitor.connection")


class ConnectionTab(QWidget):
    """Zendesk credentials + TRC field mapping form.

    Signals
    ───────
    - ``connection_changed`` — emitted after successful Save & Connect.
      The page shell forwards this to MainWindow to rewire the monitor.
    """

    connection_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._load_settings()

    # ── UI construction ───────────────────────────────────────────

    def _build_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 12, 0, 12)
        layout.setSpacing(16)

        layout.addWidget(self._build_credentials_card())
        layout.addStretch()

        scroll.setWidget(content)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _build_credentials_card(self) -> QFrame:
        """One card containing both credential fields and TRC mapping."""
        card = QFrame()
        card.setStyleSheet(CARD_STYLE)
        apply_card_shadow(card)
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(20, 16, 20, 16)
        card_lay.setSpacing(14)

        title = QLabel("Zendesk Connection")
        title.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; "
            f"border: none;"
        )
        card_lay.addWidget(title)

        desc = QLabel(
            "Configure your Zendesk Support instance for real-time ticket "
            "monitoring. Requires an API token (Admin → Channels → API)."
        )
        desc.setStyleSheet(
            f"font-size: 12px; color: {ALMA_TEXT_LIGHT}; border: none;"
        )
        desc.setWordWrap(True)
        card_lay.addWidget(desc)

        card_lay.addLayout(self._build_credentials_row1())
        card_lay.addLayout(self._build_credentials_row2())
        card_lay.addLayout(self._build_credentials_row3())

        # Separator before TRC mapping subform
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(
            f"background: {ALMA_BORDER_LIGHT}; max-height: 1px;"
        )
        card_lay.addWidget(sep)

        card_lay.addLayout(self._build_trc_mapping())
        card_lay.addLayout(self._build_action_buttons())

        return card

    def _build_credentials_row1(self) -> QHBoxLayout:
        """Row 1: subdomain + email."""
        row = QHBoxLayout()
        row.setSpacing(16)
        row.addLayout(_make_field_col(
            "SUBDOMAIN",
            self._mk_subdomain_field(),
        ), 1)
        row.addLayout(_make_field_col(
            "EMAIL",
            self._mk_email_field(),
        ), 1)
        return row

    def _build_credentials_row2(self) -> QHBoxLayout:
        """Row 2: view ID + API token."""
        row = QHBoxLayout()
        row.setSpacing(16)

        # View ID column — has a hint label below the field.
        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("VIEW ID")
        lbl.setStyleSheet(LBL_STYLE)
        col.addWidget(lbl)
        self._zd_view_id = QLineEdit()
        self._zd_view_id.setPlaceholderText(
            "e.g. 360012345  (Admin → Views → URL)"
        )
        self._zd_view_id.setStyleSheet(FIELD_STYLE)
        col.addWidget(self._zd_view_id)

        view_hint = QLabel(
            "Optional — scopes monitoring to a specific Zendesk view. "
            "Leave blank to use the incremental export."
        )
        view_hint.setStyleSheet(
            f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; border: none;"
        )
        view_hint.setWordWrap(True)
        col.addWidget(view_hint)
        row.addLayout(col, 1)

        # API token column.
        col2 = QVBoxLayout()
        col2.setSpacing(4)
        lbl2 = QLabel("API TOKEN")
        lbl2.setStyleSheet(LBL_STYLE)
        col2.addWidget(lbl2)
        self._zd_api_key = QLineEdit()
        self._zd_api_key.setPlaceholderText("Enter Zendesk API token")
        self._zd_api_key.setEchoMode(QLineEdit.Password)
        self._zd_api_key.setStyleSheet(FIELD_STYLE)
        col2.addWidget(self._zd_api_key)
        row.addLayout(col2, 1)

        return row

    def _build_credentials_row3(self) -> QHBoxLayout:
        """Row 3: poll interval."""
        row = QHBoxLayout()
        row.setSpacing(16)

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("POLL INTERVAL (seconds)")
        lbl.setStyleSheet(LBL_STYLE)
        col.addWidget(lbl)
        self._zd_interval = QSpinBox()
        self._zd_interval.setRange(30, 600)
        self._zd_interval.setValue(120)
        self._zd_interval.setSuffix("s")
        self._zd_interval.setStyleSheet(FIELD_STYLE)
        col.addWidget(self._zd_interval)
        row.addLayout(col, 1)

        row.addStretch(2)
        return row

    def _build_trc_mapping(self) -> QVBoxLayout:
        """TRC field mapping subform with built-in + custom fields."""
        outer = QVBoxLayout()
        outer.setSpacing(8)

        title = QLabel("TRC Field Mapping")
        title.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {ALMA_TEXT_DARK}; "
            f"border: none;"
        )
        outer.addWidget(title)

        desc = QLabel(
            "Choose which Zendesk field maps to the TRC code for spike "
            "detection. Click 'Fetch Fields' to load custom fields from "
            "your Zendesk instance."
        )
        desc.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none;"
        )
        desc.setWordWrap(True)
        outer.addWidget(desc)

        row = QHBoxLayout()
        row.setSpacing(12)

        # Source field combo
        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("TRC SOURCE FIELD")
        lbl.setStyleSheet(LBL_STYLE)
        col.addWidget(lbl)
        self._trc_field_combo = QComboBox()
        self._trc_field_combo.setStyleSheet(FIELD_STYLE)
        self._trc_field_combo.setMinimumWidth(250)
        self._trc_field_combo.addItem("Subject (default)", "subject")
        self._trc_field_combo.addItem("Tags (all, comma-joined)", "tags")
        self._trc_field_combo.addItem("Tag by prefix (see below)", "tag:")
        self._trc_field_combo.addItem(
            "Type (incident/problem/question/task)", "type"
        )
        self._trc_field_combo.addItem("Priority", "priority")
        self._trc_field_combo.addItem("Status", "status")
        self._trc_field_combo.currentIndexChanged.connect(
            self._on_trc_field_changed
        )
        col.addWidget(self._trc_field_combo)
        row.addLayout(col, 2)

        # Tag prefix subfield (visible only for "tag:" mode)
        col_prefix = QVBoxLayout()
        col_prefix.setSpacing(4)
        lbl_prefix = QLabel("TAG PREFIX")
        lbl_prefix.setStyleSheet(LBL_STYLE)
        col_prefix.addWidget(lbl_prefix)
        self._tag_prefix_input = QLineEdit()
        self._tag_prefix_input.setPlaceholderText("e.g. category")
        self._tag_prefix_input.setStyleSheet(FIELD_STYLE)
        self._tag_prefix_input.setToolTip(
            "Extract only the first tag starting with this prefix.\n"
            "Example: prefix 'category' matches 'category_billing'."
        )
        col_prefix.addWidget(self._tag_prefix_input)
        self._tag_prefix_container = QWidget()
        self._tag_prefix_container.setLayout(col_prefix)
        self._tag_prefix_container.setVisible(False)
        row.addWidget(self._tag_prefix_container, 1)

        # Fetch Fields button
        col_btn = QVBoxLayout()
        col_btn.setSpacing(4)
        col_btn.addWidget(QLabel(" "))  # spacer for vertical alignment
        self._fetch_fields_btn = QPushButton("Fetch Fields")
        self._fetch_fields_btn.setStyleSheet(GHOST_BTN)
        self._fetch_fields_btn.setCursor(Qt.PointingHandCursor)
        self._fetch_fields_btn.setToolTip(
            "Load custom ticket fields from Zendesk"
        )
        self._fetch_fields_btn.clicked.connect(self._on_fetch_fields)
        col_btn.addWidget(self._fetch_fields_btn)
        row.addLayout(col_btn, 1)

        row.addStretch(1)
        outer.addLayout(row)
        return outer

    def _build_action_buttons(self) -> QHBoxLayout:
        """Test / Save / Disconnect button row."""
        row = QHBoxLayout()
        row.setSpacing(12)

        self._zd_test_btn = QPushButton("Test Connection")
        self._zd_test_btn.setStyleSheet(GHOST_BTN)
        self._zd_test_btn.setCursor(Qt.PointingHandCursor)
        self._zd_test_btn.clicked.connect(self._on_test_connection)
        row.addWidget(self._zd_test_btn)

        self._zd_save_btn = QPushButton("Save & Connect")
        self._zd_save_btn.setStyleSheet(PRIMARY_BTN)
        self._zd_save_btn.setCursor(Qt.PointingHandCursor)
        self._zd_save_btn.clicked.connect(self._on_save_connection)
        row.addWidget(self._zd_save_btn)

        self._zd_disconnect_btn = QPushButton("Disconnect")
        self._zd_disconnect_btn.setStyleSheet(DANGER_BTN)
        self._zd_disconnect_btn.setCursor(Qt.PointingHandCursor)
        self._zd_disconnect_btn.clicked.connect(self._on_disconnect)
        self._zd_disconnect_btn.setVisible(False)
        row.addWidget(self._zd_disconnect_btn)

        row.addStretch()

        self._zd_status = QLabel("")
        self._zd_status.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_MID};"
        )
        row.addWidget(self._zd_status)

        return row

    # ── Field factories ──────────────────────────────────────────

    def _mk_subdomain_field(self) -> QLineEdit:
        self._zd_subdomain = QLineEdit()
        self._zd_subdomain.setPlaceholderText(
            "mycompany  (from mycompany.zendesk.com)"
        )
        self._zd_subdomain.setStyleSheet(FIELD_STYLE)
        return self._zd_subdomain

    def _mk_email_field(self) -> QLineEdit:
        self._zd_email = QLineEdit()
        self._zd_email.setPlaceholderText("admin@company.com")
        self._zd_email.setStyleSheet(FIELD_STYLE)
        return self._zd_email

    # ── Settings load / save ─────────────────────────────────────

    def _load_settings(self) -> None:
        """Restore saved Zendesk credentials and TRC mapping on init."""
        try:
            from src.data.zendesk_client import ZendeskClient
            sub, email, key, view_id = ZendeskClient.load_credentials()
            if sub:
                self._zd_subdomain.setText(sub)
            if email:
                self._zd_email.setText(email)
            if view_id:
                self._zd_view_id.setText(view_id)
            if key:
                self._zd_api_key.setText(key)
                self._zd_disconnect_btn.setVisible(True)
                self._zd_status.setText("✓ Credentials saved")

            self._restore_trc_field()
        except Exception as exc:
            logger.debug("Connection settings load failed: %s", exc)

    def _restore_trc_field(self) -> None:
        """Restore the TRC field mapping selection after _load_settings.

        The combo can hold built-in items, "tag:<prefix>", or
        "custom_field:<id>". Each requires a different restoration path.
        """
        try:
            from src.data.zendesk_client import ZendeskClient
            trc_field = ZendeskClient.load_trc_field()
        except Exception:
            return

        if not trc_field:
            return

        idx = self._trc_field_combo.findData(trc_field)
        if idx >= 0:
            self._trc_field_combo.setCurrentIndex(idx)
            return

        if trc_field.startswith("tag:"):
            prefix = trc_field.split(":", 1)[1]
            tag_idx = self._trc_field_combo.findData("tag:")
            if tag_idx >= 0:
                self._trc_field_combo.setCurrentIndex(tag_idx)
            self._tag_prefix_input.setText(prefix)
            self._tag_prefix_container.setVisible(True)
            return

        if trc_field.startswith("custom_field:"):
            field_id = trc_field.split(":", 1)[1]
            self._trc_field_combo.addItem(
                f"Custom Field #{field_id}", trc_field
            )
            self._trc_field_combo.setCurrentIndex(
                self._trc_field_combo.count() - 1
            )

    # ── Action handlers ───────────────────────────────────────────

    def _on_trc_field_changed(self, _index: int) -> None:
        """Show/hide the tag-prefix subfield based on combo selection."""
        data = self._trc_field_combo.currentData()
        self._tag_prefix_container.setVisible(data == "tag:")

    def _on_test_connection(self) -> None:
        """Synchronously test Zendesk credentials and surface the result."""
        self._zd_test_btn.setEnabled(False)
        self._zd_test_btn.setText("Testing…")
        self._zd_status.setText("")

        from src.data.zendesk_client import ZendeskClient
        client = ZendeskClient(
            self._zd_subdomain.text().strip(),
            self._zd_email.text().strip(),
            self._zd_api_key.text().strip(),
        )

        try:
            ok = client.test_connection()
            if ok:
                self._set_status("✓ Connection successful!", ALMA_SUCCESS)
            else:
                self._set_status(
                    "✗ Connection failed — check credentials", ALMA_ERROR
                )
        except Exception as exc:
            self._set_status(f"✗ Error: {exc}", ALMA_ERROR)
        finally:
            self._zd_test_btn.setEnabled(True)
            self._zd_test_btn.setText("Test Connection")

    def _on_save_connection(self) -> None:
        """Persist credentials + TRC field mapping; emit connection_changed."""
        sub = self._zd_subdomain.text().strip()
        email = self._zd_email.text().strip()
        key = self._zd_api_key.text().strip()
        view_id = self._zd_view_id.text().strip()

        if not (sub and email and key):
            QMessageBox.warning(
                self,
                "Missing Fields",
                "Please fill in subdomain, email, and API token.",
            )
            return

        from src.data.zendesk_client import ZendeskClient
        ZendeskClient.save_credentials(sub, email, key, view_id=view_id)

        # Resolve TRC field — special-case the "tag:" prefix mode.
        trc_field = self._trc_field_combo.currentData() or "subject"
        if trc_field == "tag:":
            prefix = self._tag_prefix_input.text().strip()
            trc_field = f"tag:{prefix}" if prefix else "tags"
        ZendeskClient.save_trc_field(trc_field)

        self._zd_disconnect_btn.setVisible(True)
        self._set_status("✓ Saved — starting monitor…", ALMA_SUCCESS)
        self.connection_changed.emit()

    def _on_disconnect(self) -> None:
        """Confirm and clear credentials; the page shell stops the monitor."""
        reply = QMessageBox.question(
            self,
            "Disconnect Zendesk",
            "Remove Zendesk credentials and stop monitoring?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            from src.data.pat_store import save_setting
            save_setting("zendesk_subdomain", "")
            save_setting("zendesk_email", "")
            save_setting("zendesk_api_key", "")
        except Exception as exc:
            logger.warning("Disconnect persistence failed: %s", exc)

        self._zd_subdomain.clear()
        self._zd_email.clear()
        self._zd_api_key.clear()
        self._zd_disconnect_btn.setVisible(False)
        self._set_status("Disconnected", ALMA_TEXT_LIGHT)
        # Connection_changed fires too — the page shell stops the monitor.
        self.connection_changed.emit()

    def _on_fetch_fields(self) -> None:
        """Pull custom ticket fields from Zendesk and add them to the combo."""
        sub = self._zd_subdomain.text().strip()
        email = self._zd_email.text().strip()
        key = self._zd_api_key.text().strip()

        if not (sub and email and key):
            QMessageBox.warning(
                self,
                "Missing Credentials",
                "Enter subdomain, email, and API token before fetching fields.",
            )
            return

        self._fetch_fields_btn.setEnabled(False)
        self._fetch_fields_btn.setText("Fetching…")

        try:
            from src.data.zendesk_client import ZendeskClient
            client = ZendeskClient(sub, email, key)
            fields = client.fetch_ticket_fields()
            self._merge_custom_fields(fields)
        except Exception as exc:
            self._set_status(f"✗ Fetch failed: {exc}", ALMA_ERROR)
            return
        finally:
            self._fetch_fields_btn.setEnabled(True)
            self._fetch_fields_btn.setText("Fetch Fields")

    def _merge_custom_fields(self, fields: list[dict]) -> None:
        """Merge Zendesk custom field list into the TRC combo.

        Strips fields that duplicate built-in selections (subject,
        status, priority, etc.) and inactive fields.
        """
        if not fields:
            self._set_status("No fields returned — check connection",
                             ALMA_WARNING)
            return

        current = self._trc_field_combo.currentData()

        # Drop previous custom rows; keep first 6 built-in items.
        while self._trc_field_combo.count() > 6:
            self._trc_field_combo.removeItem(6)

        self._trc_field_combo.insertSeparator(6)

        builtins = {
            "subject", "status", "priority", "tickettype",
            "description", "group", "assignee",
        }

        added = 0
        for f in fields:
            if not f.get("active", True):
                continue
            ftype = f.get("type", "")
            if ftype in builtins:
                continue
            fid = str(f.get("id", ""))
            title = f.get("title", f"Field {fid}")
            display = f"{title}  ({ftype}, #{fid})"
            self._trc_field_combo.addItem(display, f"custom_field:{fid}")
            added += 1

        if current:
            idx = self._trc_field_combo.findData(current)
            if idx >= 0:
                self._trc_field_combo.setCurrentIndex(idx)

        self._set_status(f"✓ Loaded {added} custom fields", ALMA_SUCCESS)

    def _set_status(self, text: str, color: str) -> None:
        """Helper to set the status label text and color in one step."""
        self._zd_status.setText(text)
        self._zd_status.setStyleSheet(
            f"font-size: 11px; color: {color};"
        )


# ─── Free-function helper ─────────────────────────────────────────

def _make_field_col(label_text: str, field_widget) -> QVBoxLayout:
    """Build a labelled column: small uppercase label above a field."""
    col = QVBoxLayout()
    col.setSpacing(4)
    lbl = QLabel(label_text)
    lbl.setStyleSheet(LBL_STYLE)
    col.addWidget(lbl)
    col.addWidget(field_widget)
    return col
