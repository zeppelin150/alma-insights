"""
Alma Insights — Settings Page
Data source configuration: Lightdash PAT, API datasets, test data toggle.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFrame, QScrollArea, QSizePolicy, QMessageBox
)
from PySide6.QtCore import Qt, Signal
from src.ui.theme import *


class ToggleSwitch(QWidget):
    """Custom toggle switch widget."""
    toggled = Signal(bool)

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self._checked = checked
        self.setFixedSize(44, 24)
        self.setCursor(Qt.PointingHandCursor)

    @property
    def checked(self):
        return self._checked

    @checked.setter
    def checked(self, val):
        self._checked = val
        self.update()
        self.toggled.emit(val)

    def mousePressEvent(self, e):
        self.checked = not self._checked

    def paintEvent(self, e):
        from PySide6.QtGui import QPainter, QColor, QPen
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # Track
        track_color = QColor(ALMA_GREEN_LIGHT) if self._checked else QColor(ALMA_BORDER)
        p.setPen(Qt.NoPen)
        p.setBrush(track_color)
        p.drawRoundedRect(0, 2, 44, 20, 10, 10)

        # Knob
        p.setBrush(QColor("#FFFFFF"))
        knob_x = 24 if self._checked else 2
        p.drawEllipse(knob_x, 2, 20, 20)
        p.end()


class DatasetRow(QFrame):
    """Single row in the dataset list builder."""
    remove_requested = Signal(object)

    def __init__(self, title="", url="", parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 8px; padding: 4px;
            }}
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 8, 8)
        layout.setSpacing(10)

        # Title input
        self.title_input = QLineEdit(title)
        self.title_input.setPlaceholderText("Dataset title (e.g. RCM TRCs)")
        self.title_input.setMinimumWidth(180)
        self.title_input.setMaximumWidth(240)
        layout.addWidget(self.title_input)

        # URL input
        self.url_input = QLineEdit(url)
        self.url_input.setPlaceholderText("Lightdash saved chart URL")
        layout.addWidget(self.url_input, 1)

        # Remove button
        remove_btn = QPushButton("✕")
        remove_btn.setObjectName("GhostButton")
        remove_btn.setFixedSize(28, 28)
        remove_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {ALMA_TEXT_LIGHT}; font-size: 14px; border-radius: 4px; padding: 0; }}
            QPushButton:hover {{ color: {ALMA_ERROR}; background: rgba(196,30,30,0.08); }}
        """)
        remove_btn.setCursor(Qt.PointingHandCursor)
        remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        layout.addWidget(remove_btn)

    def get_data(self):
        return {
            "title": self.title_input.text().strip(),
            "url": self.url_input.text().strip(),
        }

    def is_valid(self):
        return bool(self.title_input.text().strip())


class SettingsPage(QWidget):
    """Settings page with data source configuration."""

    # Emitted when settings change that affect other pages
    settings_changed = Signal(dict)
    test_data_changed = Signal(bool)
    debug_mode_changed = Signal(bool)
    datasets_changed = Signal(list)

    MAX_DATASETS = 99

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dataset_rows = []
        self._loading = True          # suppress signals during init
        self._build_ui()
        self._connect_signals()
        self._load_saved_pat()
        self._load_persisted_settings()
        self._loading = False

    def _build_ui(self):
        # Outer scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        inner = QWidget()
        self.layout_inner = QVBoxLayout(inner)
        self.layout_inner.setContentsMargins(28, 24, 28, 40)
        self.layout_inner.setSpacing(0)

        # Page header
        header = QLabel("Settings")
        header.setObjectName("PageHeader")
        self.layout_inner.addWidget(header)

        sub = QLabel("Configure data sources, API connections, and display preferences")
        sub.setObjectName("PageSubheader")
        self.layout_inner.addWidget(sub)
        self.layout_inner.addSpacing(28)

        # ════════════════════════════════════
        #  SECTION 1: Test Data
        # ════════════════════════════════════
        self.layout_inner.addWidget(self._section_label("DEVELOPMENT"))
        self.layout_inner.addSpacing(8)

        test_card = self._card()
        test_layout = QVBoxLayout(test_card)
        test_layout.setContentsMargins(20, 18, 20, 18)
        test_layout.setSpacing(10)

        # Toggle row
        test_row = QHBoxLayout()
        test_lbl_col = QVBoxLayout()
        test_lbl_col.setSpacing(2)
        test_title = QLabel("Use Test / Demo Data")
        test_title.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {ALMA_TEXT_DARK};")
        test_desc = QLabel(
            "When ON, the app loads synthetic demo conversations for testing. "
            "Turn OFF for live data mode."
        )
        test_desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        test_desc.setWordWrap(True)
        test_lbl_col.addWidget(test_title)
        test_lbl_col.addWidget(test_desc)
        test_row.addLayout(test_lbl_col, 1)

        self.test_data_toggle = ToggleSwitch(checked=True)
        test_row.addWidget(self.test_data_toggle)
        test_layout.addLayout(test_row)

        # Debug canary toggle
        debug_row = QHBoxLayout()
        debug_lbl_col = QVBoxLayout()
        debug_lbl_col.setSpacing(2)
        debug_title = QLabel("Debug / Canary Mode")
        debug_title.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {ALMA_TEXT_DARK};")
        debug_desc = QLabel(
            "When ON, Pull Data shows a diagnostic popup before each action "
            "with combo state, PAT status, and test-mode flag. Useful for troubleshooting."
        )
        debug_desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        debug_desc.setWordWrap(True)
        debug_lbl_col.addWidget(debug_title)
        debug_lbl_col.addWidget(debug_desc)
        debug_row.addLayout(debug_lbl_col, 1)

        self.debug_toggle = ToggleSwitch(checked=False)
        debug_row.addWidget(self.debug_toggle)
        test_layout.addLayout(debug_row)

        self.layout_inner.addWidget(test_card)
        self.layout_inner.addSpacing(24)

        # ════════════════════════════════════
        #  SECTION 2: Lightdash Connection
        # ════════════════════════════════════
        self.layout_inner.addWidget(self._section_label("LIGHTDASH CONNECTION"))
        self.layout_inner.addSpacing(8)

        lh_card = self._card()
        lh_layout = QVBoxLayout(lh_card)
        lh_layout.setContentsMargins(20, 18, 20, 18)
        lh_layout.setSpacing(16)

        # PAT field
        pat_label = QLabel("Personal Access Token (PAT)")
        pat_label.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {ALMA_TEXT_MID};")

        pat_row = QHBoxLayout()
        pat_row.setSpacing(8)
        self.pat_input = QLineEdit()
        self.pat_input.setPlaceholderText("Paste your Lightdash Personal Access Token here")
        self.pat_input.setEchoMode(QLineEdit.Password)
        pat_row.addWidget(self.pat_input, 1)

        self.pat_save_btn = QPushButton("Save")
        self.pat_save_btn.setFixedWidth(80)
        self.pat_save_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_LIGHT}; color: {ALMA_TEXT_ON_DARK};
                border: none; border-radius: 6px; padding: 6px 12px;
                font-size: 12px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_SUBTLE}; }}
        """)
        self.pat_save_btn.setCursor(Qt.PointingHandCursor)
        self.pat_save_btn.clicked.connect(self._save_pat)
        pat_row.addWidget(self.pat_save_btn)

        self.pat_status_label = QLabel("")
        self.pat_status_label.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT};")

        pat_hint = QLabel(
            "Generate a PAT from your Lightdash profile → Personal Access Tokens. "
            "Use the lowest-privilege token possible (viewer access is sufficient)."
        )
        pat_hint.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT};")
        pat_hint.setWordWrap(True)
        lh_layout.addWidget(pat_label)
        lh_layout.addLayout(pat_row)
        lh_layout.addWidget(self.pat_status_label)
        lh_layout.addWidget(pat_hint)

        # Divider
        div = QFrame()
        div.setStyleSheet(f"background: {ALMA_BORDER_LIGHT}; min-height: 1px; max-height: 1px; margin: 4px 0;")
        lh_layout.addWidget(div)

        # API toggle row
        api_row = QHBoxLayout()
        api_lbl_col = QVBoxLayout()
        api_lbl_col.setSpacing(2)
        api_title = QLabel("Enable API Data Pull")
        api_title.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {ALMA_TEXT_DARK};")
        api_desc = QLabel(
            "When ON, configured Lightdash datasets appear in the Conversations "
            "data source selector. When OFF, only CSV import is available."
        )
        api_desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        api_desc.setWordWrap(True)
        api_lbl_col.addWidget(api_title)
        api_lbl_col.addWidget(api_desc)
        api_row.addLayout(api_lbl_col, 1)

        self.api_toggle = ToggleSwitch(checked=False)
        api_row.addWidget(self.api_toggle)
        lh_layout.addLayout(api_row)

        self.layout_inner.addWidget(lh_card)
        self.layout_inner.addSpacing(24)

        # ════════════════════════════════════
        #  SECTION 3: Dataset List
        # ════════════════════════════════════
        self.layout_inner.addWidget(self._section_label("DATASETS"))
        self.layout_inner.addSpacing(8)

        dataset_card = self._card()
        self.dataset_card_layout = QVBoxLayout(dataset_card)
        self.dataset_card_layout.setContentsMargins(20, 18, 20, 18)
        self.dataset_card_layout.setSpacing(10)

        ds_desc = QLabel(
            "Define Lightdash saved chart datasets to pull. These appear in the "
            "Conversations page data source selector when API is enabled."
        )
        ds_desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        ds_desc.setWordWrap(True)
        self.dataset_card_layout.addWidget(ds_desc)

        # Container for dataset rows
        self.dataset_list_widget = QWidget()
        self.dataset_list_layout = QVBoxLayout(self.dataset_list_widget)
        self.dataset_list_layout.setContentsMargins(0, 0, 0, 0)
        self.dataset_list_layout.setSpacing(6)
        self.dataset_card_layout.addWidget(self.dataset_list_widget)

        # Add button row
        add_row = QHBoxLayout()
        self.add_dataset_btn = QPushButton("+  Add Dataset")
        self.add_dataset_btn.setObjectName("SecondaryButton")
        self.add_dataset_btn.setCursor(Qt.PointingHandCursor)
        self.add_dataset_btn.setFixedWidth(160)
        add_row.addWidget(self.add_dataset_btn)
        add_row.addStretch()

        self.dataset_count_label = QLabel("0 / 99")
        self.dataset_count_label.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT};")
        add_row.addWidget(self.dataset_count_label)
        self.dataset_card_layout.addLayout(add_row)

        self.layout_inner.addWidget(dataset_card)
        self.layout_inner.addSpacing(24)

        # ════════════════════════════════════
        #  SECTION 4: Disabled state overlay
        # ════════════════════════════════════
        self._update_api_state(False)

        self.layout_inner.addStretch()

        scroll.setWidget(inner)

        # Outer layout
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _connect_signals(self):
        self.add_dataset_btn.clicked.connect(lambda: self._add_dataset_row())
        self.api_toggle.toggled.connect(self._update_api_state)
        self.api_toggle.toggled.connect(self._emit_datasets_changed)
        self.api_toggle.toggled.connect(self._persist_api_toggle)
        self.test_data_toggle.toggled.connect(lambda v: self.test_data_changed.emit(v))
        self.test_data_toggle.toggled.connect(self._persist_test_data_toggle)
        self.debug_toggle.toggled.connect(lambda v: self.debug_mode_changed.emit(v))
        self.debug_toggle.toggled.connect(self._persist_debug_toggle)

    # ── Helpers ──

    def _section_label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(f"""
            font-size: 10px; font-weight: 700; color: {ALMA_TEXT_LIGHT};
            letter-spacing: 1.2px; padding: 0 4px;
        """)
        return lbl

    def _card(self):
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 10px;
            }}
        """)
        return card

    # ── Dataset Row Management ──

    def _add_dataset_row(self, title="", url=""):
        if len(self._dataset_rows) >= self.MAX_DATASETS:
            return

        row = DatasetRow(title=title, url=url)
        row.remove_requested.connect(self._remove_dataset_row)
        row.title_input.textChanged.connect(lambda: self._emit_datasets_changed())
        row.url_input.textChanged.connect(lambda: self._emit_datasets_changed())
        self._dataset_rows.append(row)
        self.dataset_list_layout.addWidget(row)
        self._update_dataset_count()
        self._emit_datasets_changed()

    def _remove_dataset_row(self, row):
        if len(self._dataset_rows) <= 1:
            return  # Keep at least 1 row
        self._dataset_rows.remove(row)
        self.dataset_list_layout.removeWidget(row)
        row.deleteLater()
        self._update_dataset_count()
        self._emit_datasets_changed()

    def _update_dataset_count(self):
        n = len(self._dataset_rows)
        self.dataset_count_label.setText(f"{n} / {self.MAX_DATASETS}")
        self.add_dataset_btn.setEnabled(n < self.MAX_DATASETS)

    def _update_api_state(self, enabled):
        """Enable/disable dataset section based on API toggle."""
        self.dataset_list_widget.setEnabled(enabled)
        self.add_dataset_btn.setEnabled(enabled and len(self._dataset_rows) < self.MAX_DATASETS)
        # Only enable PAT input if API is on AND no PAT is already saved
        has_saved = hasattr(self, '_saved_pat') and self._saved_pat
        self.pat_input.setEnabled(enabled and not has_saved)

        # Visual opacity hint
        self.dataset_list_widget.setStyleSheet(
            "opacity: 0.45;" if not enabled else ""
        )

    def _emit_datasets_changed(self, *args):
        self.datasets_changed.emit(self.get_datasets())
        self._persist_datasets()

    # ── PAT Persistence ──

    def _save_pat(self):
        """Save the PAT to local credential store."""
        raw_pat = self.pat_input.text().strip()
        if not raw_pat:
            self.pat_status_label.setText("⚠ No token to save")
            self.pat_status_label.setStyleSheet(f"font-size: 11px; color: {ALMA_WARNING};")
            return

        from src.data.pat_store import save_pat, redact_pat
        if save_pat(raw_pat):
            self.pat_input.clear()
            self.pat_input.setPlaceholderText("PAT in Use")
            self.pat_input.setEnabled(False)
            self.pat_save_btn.setText("Clear")
            self.pat_save_btn.clicked.disconnect()
            self.pat_save_btn.clicked.connect(self._clear_pat)
            self.pat_status_label.setText(f"✓ Saved: {redact_pat(raw_pat)}")
            self.pat_status_label.setStyleSheet(f"font-size: 11px; color: {ALMA_SUCCESS};")
            self._saved_pat = raw_pat
        else:
            self.pat_status_label.setText("✗ Failed to save token")
            self.pat_status_label.setStyleSheet(f"font-size: 11px; color: {ALMA_ERROR};")

    def _clear_pat(self):
        """Remove saved PAT."""
        from src.data.pat_store import delete_pat
        delete_pat()
        self._saved_pat = ""
        self.pat_input.setEnabled(True)
        self.pat_input.setPlaceholderText("Paste your Lightdash Personal Access Token here")
        self.pat_save_btn.setText("Save")
        self.pat_save_btn.clicked.disconnect()
        self.pat_save_btn.clicked.connect(self._save_pat)
        self.pat_status_label.setText("Token cleared")
        self.pat_status_label.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT};")

    def _load_saved_pat(self):
        """Load PAT from credential store on startup."""
        from src.data.pat_store import load_pat, has_pat, redact_pat
        self._saved_pat = ""
        if has_pat():
            self._saved_pat = load_pat()
            self.pat_input.clear()
            self.pat_input.setPlaceholderText("PAT in Use")
            self.pat_input.setEnabled(False)
            self.pat_save_btn.setText("Clear")
            self.pat_save_btn.clicked.disconnect()
            self.pat_save_btn.clicked.connect(self._clear_pat)
            self.pat_status_label.setText(f"✓ Loaded: {redact_pat(self._saved_pat)}")
            self.pat_status_label.setStyleSheet(f"font-size: 11px; color: {ALMA_SUCCESS};")

    # ── Settings Persistence ──

    def _load_persisted_settings(self):
        """Restore toggle states and datasets from credential store."""
        from src.data.pat_store import load_setting

        # Restore toggle states (set _checked directly to avoid emitting signals)
        api_enabled = load_setting("api_enabled", False)
        test_data = load_setting("test_data_enabled", True)
        debug_mode = load_setting("debug_mode", False)

        self.api_toggle._checked = api_enabled
        self.api_toggle.update()
        self._update_api_state(api_enabled)

        self.test_data_toggle._checked = test_data
        self.test_data_toggle.update()

        self.debug_toggle._checked = debug_mode
        self.debug_toggle.update()

        # Restore datasets (or default to 3 empty rows)
        saved_datasets = load_setting("datasets", None)
        if saved_datasets and isinstance(saved_datasets, list):
            for ds in saved_datasets:
                self._add_dataset_row(ds.get("title", ""), ds.get("url", ""))
        else:
            for _ in range(3):
                self._add_dataset_row()

    def _persist_api_toggle(self, enabled):
        if self._loading:
            return
        from src.data.pat_store import save_setting
        save_setting("api_enabled", enabled)

    def _persist_test_data_toggle(self, enabled):
        if self._loading:
            return
        from src.data.pat_store import save_setting
        save_setting("test_data_enabled", enabled)

    def _persist_debug_toggle(self, enabled):
        if self._loading:
            return
        from src.data.pat_store import save_setting
        save_setting("debug_mode", enabled)

    def _persist_datasets(self):
        if self._loading:
            return
        from src.data.pat_store import save_setting
        # Save all rows (including empty ones) to preserve row count
        all_rows = [r.get_data() for r in self._dataset_rows]
        save_setting("datasets", all_rows)

    # ── Public API ──

    def get_datasets(self):
        """Return list of configured dataset dicts with non-empty titles."""
        if not self.api_toggle.checked:
            return []
        return [r.get_data() for r in self._dataset_rows if r.is_valid()]

    def is_api_enabled(self):
        return self.api_toggle.checked

    def is_test_data_enabled(self):
        return self.test_data_toggle.checked

    def is_debug_mode_enabled(self):
        return self.debug_toggle.checked

    def get_pat(self):
        """Return PAT: from saved store first, then from input field."""
        if hasattr(self, '_saved_pat') and self._saved_pat:
            return self._saved_pat
        return self.pat_input.text().strip()
