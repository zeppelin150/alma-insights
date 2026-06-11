"""
Alma Insights — Settings Page
Data source configuration: Lightdash PAT, API datasets, test data toggle.
"""

import webbrowser

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFrame, QScrollArea, QSizePolicy, QMessageBox,
    QComboBox, QFileDialog, QTextEdit, QTabWidget, QProgressBar,
)
from PySide6.QtCore import Qt, Signal, QThread
from src.data.settings_manager import load_settings, save_settings, get_section, set_section
from src.data.connection_factory import get_connection
from src.ui.theme import *

# Maps page → { human title: yaml_key } for behavior toggles
SECTION_KEYS = {
    "trc_analytics": {
        "Volume & Resolution Charts": "volume_resolution_charts",
        "CSAT Heatmap": "csat_heatmap",
        "Metrics by TRC": "metrics_by_trc",
    },
    "incidents": {
        "TRC Status Grid": "trc_status_grid",
        "Control Chart": "control_chart",
        "Open Incidents": "open_incidents",
        "Theta Anomaly Scan": "theta_anomaly_scan",
    },
    "trending": {
        "Sentiment Trend": "sentiment_trend",
        "Cross-TRC Correlation": "cross_trc_correlation",
        "Rising & Cooling Terms": "rising_cooling_terms",
        "Topic Clusters": "topic_clusters",
    },
}

# Human-friendly page names
PAGE_TITLES = {
    "trc_analytics": "TRC Analytics",
    "incidents": "Incidents",
    "trending": "Trending Topics",
}


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
                background: {ALMA_BG_ELEVATED}; border: none;
                border-radius: 10px; padding: 4px;
            }}
        """)
        apply_card_shadow_soft(self)
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


# ═══════════════════════════════════════════
#  GEMINI SETUP WORKER
# ═══════════════════════════════════════════

class EmbeddingRebuildWorker(QThread):
    """Background worker for rebuilding the semantic search index."""
    finished = Signal(int)   # count of embeddings written
    error    = Signal(str)   # human-readable error

    def __init__(self, db_path: str):
        super().__init__()
        self.db_path = db_path

    def run(self):
        try:
            from src.data.embedding.builder import build_embeddings
            conn = get_connection(self.db_path)
            count = build_embeddings(conn, force=True)
            conn.close()
            self.finished.emit(count)
        except Exception as e:
            self.error.emit(str(e))


class GeminiSetupWorker(QThread):
    """Background worker for Gemini CLI detection, installation, and auth verification."""
    log_line = Signal(str)        # live npm output lines
    status   = Signal(str)        # short status message for the UI
    finished = Signal(str, str)   # (new_state, detail_message)
    error    = Signal(str)        # human-readable error

    VALID_MODES = ("check", "install", "verify_auth")

    def __init__(self, mode: str, gemini_path: str = "", npm_path: str = ""):
        super().__init__()
        assert mode in self.VALID_MODES, f"Unknown mode: {mode}"
        self.mode = mode
        self.gemini_path = gemini_path
        self.npm_path = npm_path

    def run(self):
        from src.data import gemini_setup

        try:
            if self.mode == "check":
                self._do_check(gemini_setup)
            elif self.mode == "install":
                self._do_install(gemini_setup)
            elif self.mode == "verify_auth":
                self._do_verify(gemini_setup)
        except Exception as e:
            self.error.emit(str(e))

    def _do_check(self, gs):
        self.status.emit("Checking system...")
        cli = gs.find_gemini_cli()
        if cli:
            ok, version = gs.verify_gemini_auth(cli)
            if ok:
                self.finished.emit("READY", f"{cli}|{version}")
            else:
                self.finished.emit("AUTHENTICATING", cli)
        else:
            self.finished.emit("NOT_CONFIGURED", "")

    def _do_install(self, gs):
        self.status.emit("Starting npm install...")
        success, err = gs.install_gemini_cli(
            self.npm_path,
            progress_callback=lambda line: self.log_line.emit(line),
        )
        if success:
            cli = gs.find_gemini_cli()
            self.finished.emit("AUTHENTICATING", cli or "")
        else:
            self.error.emit(f"Installation failed: {err}")

    def _do_verify(self, gs):
        self.status.emit("Verifying sign-in...")
        path = self.gemini_path
        if not path:
            from src.data.gemini_setup import find_gemini_cli
            path = find_gemini_cli() or ""
        if not path:
            self.error.emit("Gemini CLI not found. Try reinstalling.")
            return
        ok, detail = gs.verify_gemini_auth(path)
        if ok:
            self.finished.emit("READY", f"{path}|{detail}")
        else:
            self.error.emit(f"Sign-in not detected: {detail}\n\nComplete sign-in in the terminal, then try again.")


# ═══════════════════════════════════════════
#  SETTINGS PAGE
# ═══════════════════════════════════════════

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

    def _make_tab_scroll(self):
        """Create a scroll area + inner widget + layout for a settings tab.
        Returns (scroll_area, inner_layout)."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(28, 24, 28, 40)
        layout.setSpacing(0)
        scroll.setWidget(inner)
        return scroll, layout

    def _build_ui(self):
        # ── QTabWidget ──
        self._tab_widget = QTabWidget()
        self._tab_widget.setObjectName("SettingsTab")
        self._tab_widget.setDocumentMode(True)

        # Page header (shared reference for backward compat)
        # Each tab is self-contained; no shared header needed.

        # ════════════════════════════════════
        #  TAB 1: AI Provider
        # ════════════════════════════════════
        ai_scroll, ai_layout = self._make_tab_scroll()

        # State machine vars — set before _build_gemini_section()
        self._gemini_state = "NOT_CONFIGURED"
        self._gemini_cli_path = ""
        self._gemini_setup_worker = None
        self._gemini_api_key_mode = False

        # Claude danger-zone state
        self._claude_pending = False
        self._claude_checks = {"baa": False, "hipaa": False, "legal": False}

        self.layout_inner = ai_layout
        self._build_ai_provider_tab(ai_layout)
        self._tab_widget.addTab(ai_scroll, "AI Provider")

        # ════════════════════════════════════
        #  TAB 2: Integrations
        # ════════════════════════════════════
        int_scroll, int_layout = self._make_tab_scroll()
        self.layout_inner = int_layout
        self._build_integrations_tab(int_layout)
        self._tab_widget.addTab(int_scroll, "Integrations")

        # ════════════════════════════════════
        #  TAB 3: Updates
        # ════════════════════════════════════
        updates_scroll, updates_layout = self._make_tab_scroll()
        self._build_updates_tab(updates_layout)
        self._tab_widget.addTab(updates_scroll, "Updates")

        # ════════════════════════════════════
        #  TAB 4: Display
        # ════════════════════════════════════
        display_scroll, display_layout = self._make_tab_scroll()
        self.layout_inner = display_layout
        self._build_display_tab(display_layout)
        self._tab_widget.addTab(display_scroll, "Display")

        # ════════════════════════════════════
        #  TAB 5: Scanning Costs
        # ════════════════════════════════════
        self._cost_placeholder = QLabel("Scanning Costs will appear after data loads.")
        self._cost_placeholder.setAlignment(Qt.AlignCenter)
        self._cost_placeholder.setStyleSheet(
            f"font-size: 13px; color: {ALMA_TEXT_LIGHT}; padding: 40px;"
        )
        self._tab_widget.addTab(self._cost_placeholder, "Scanning Costs")

        # Outer layout
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._tab_widget)

    # ═══════════════════════════════════════════
    #  TAB 1: AI PROVIDER
    # ═══════════════════════════════════════════

    def _build_ai_provider_tab(self, lay):
        """Build the AI Provider tab: active model, Gemini config, Claude config."""
        # ── Active Model selector ──
        lay.addWidget(self._section_label("ACTIVE MODEL"))
        lay.addSpacing(8)

        model_card = self._card()
        model_card_layout = QVBoxLayout(model_card)
        model_card_layout.setContentsMargins(20, 18, 20, 18)
        model_card_layout.setSpacing(10)

        model_desc = QLabel(
            "Select the LLM model used for AI Reports, NLP scans, and hypothesis testing."
        )
        model_desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        model_desc.setWordWrap(True)
        model_card_layout.addWidget(model_desc)

        model_row = QHBoxLayout()
        model_lbl = QLabel("Model")
        model_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {ALMA_TEXT_MID};")
        model_row.addWidget(model_lbl)

        self._active_model_combo = QComboBox()
        self._active_model_combo.setStyleSheet(f"""
            QComboBox {{
                color: {ALMA_TEXT_DARK}; background: {ALMA_WHITE};
                border: 1px solid {ALMA_BORDER}; border-radius: 8px;
                padding: 8px 12px; font-size: 13px; min-width: 220px;
            }}
        """)
        model_row.addWidget(self._active_model_combo, 1)
        model_card_layout.addLayout(model_row)

        # PII toggle (shared across providers)
        pii_row = QHBoxLayout()
        pii_lbl_col = QVBoxLayout()
        pii_lbl_col.setSpacing(2)
        pii_title = QLabel("PII Redaction")
        pii_title.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {ALMA_TEXT_DARK};")
        pii_desc = QLabel("Aggressive name redaction (base redaction always on)")
        pii_desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        pii_desc.setWordWrap(True)
        pii_lbl_col.addWidget(pii_title)
        pii_lbl_col.addWidget(pii_desc)
        pii_row.addLayout(pii_lbl_col, 1)
        self.pii_toggle = ToggleSwitch(checked=True)
        self.pii_toggle.toggled.connect(self._persist_gemini_settings)
        pii_row.addWidget(self.pii_toggle)
        model_card_layout.addLayout(pii_row)

        lay.addWidget(model_card)
        lay.addSpacing(24)

        # ── Gemini Configuration ──
        lay.addWidget(self._section_label("GEMINI CONFIGURATION"))
        lay.addSpacing(8)
        self._build_gemini_section()
        lay.addSpacing(24)

        # ── Claude Configuration ──
        lay.addWidget(self._section_label("CLAUDE CONFIGURATION"))
        lay.addSpacing(8)
        self._build_claude_section(lay)
        lay.addSpacing(24)

        # ── Hardware Profile ──
        lay.addWidget(self._section_label("HARDWARE PROFILE"))
        lay.addSpacing(8)
        self._build_hardware_profile_section(lay)
        lay.addSpacing(24)

        # ── Task Routing ──
        lay.addWidget(self._section_label("TASK ROUTING"))
        lay.addSpacing(8)
        self._build_task_routing_section(lay)

        lay.addStretch()

        # Populate model dropdown from registry
        self._refresh_model_combo()
        self._active_model_combo.currentIndexChanged.connect(self._on_active_model_changed)

    def _refresh_model_combo(self):
        """Populate active model dropdown from ModelRegistry."""
        try:
            from src.llm.model_registry import ModelRegistry
            registry = ModelRegistry.instance()
            available = registry.available()
            active = registry.active()
        except Exception:
            available = []
            active = None

        self._active_model_combo.blockSignals(True)
        self._active_model_combo.clear()
        for m in available:
            self._active_model_combo.addItem(m.display_name, m.id)
        if active:
            idx = self._active_model_combo.findData(active.id)
            if idx >= 0:
                self._active_model_combo.setCurrentIndex(idx)
        self._active_model_combo.blockSignals(False)

    def _on_active_model_changed(self, idx):
        if self._loading or idx < 0:
            return
        model_id = self._active_model_combo.currentData()
        if not model_id:
            return
        try:
            from src.llm.model_registry import ModelRegistry
            registry = ModelRegistry.instance()
            registry.set_active(model_id)
        except Exception:
            pass
        self._persist_gemini_settings()
        self.settings_changed.emit({"model_changed": True})

    # ── Claude section ──

    def _build_claude_section(self, lay):
        """Build Claude provider card with HIPAA danger zone."""
        self._claude_card = self._card()
        self._claude_inner = QVBoxLayout(self._claude_card)
        self._claude_inner.setContentsMargins(20, 18, 20, 18)
        self._claude_inner.setSpacing(12)
        lay.addWidget(self._claude_card)

        self._render_claude_state()

    def _render_claude_state(self):
        """Render Claude section based on current state."""
        # Clear existing
        while self._claude_inner.count():
            item = self._claude_inner.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout_recursive(item.layout())

        from src.data.pat_store import load_setting
        has_key = bool(load_setting("anthropic_api_key", ""))

        if has_key:
            self._render_claude_connected()
        elif self._claude_pending:
            self._render_claude_danger_zone()
        else:
            self._render_claude_not_configured()

    def _render_claude_not_configured(self):
        """Show 'Connect Claude' prompt."""
        title = QLabel("Connect Claude")
        title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        self._claude_inner.addWidget(title)

        desc = QLabel(
            "Add Anthropic Claude as an AI provider. Requires a valid API key "
            "and a Business Associate Agreement (BAA) for HIPAA compliance."
        )
        desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT}; border: none;")
        desc.setWordWrap(True)
        self._claude_inner.addWidget(desc)

        btn = QPushButton("Enable Claude")
        btn.setStyleSheet(self._primary_btn_style())
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(self._on_claude_enable_clicked)
        self._claude_inner.addWidget(btn)

    def _on_claude_enable_clicked(self):
        self._claude_pending = True
        self._claude_checks = {"baa": False, "hipaa": False, "legal": False}
        self._render_claude_state()

    def _render_claude_danger_zone(self):
        """Show HIPAA compliance danger zone with 3 checkboxes."""
        # Danger header
        header_row = QHBoxLayout()

        icon_frame = QFrame()
        icon_frame.setFixedSize(30, 30)
        icon_frame.setStyleSheet(f"""
            QFrame {{
                background: #fef2f2;
                border: none;
                border-radius: 4px;
            }}
        """)
        icon_lbl = QLabel("⚠")
        icon_lbl.setAlignment(Qt.AlignCenter)
        icon_lbl.setStyleSheet("font-size: 16px; border: none;")
        icon_lay = QVBoxLayout(icon_frame)
        icon_lay.setContentsMargins(0, 0, 0, 0)
        icon_lay.addWidget(icon_lbl)
        header_row.addWidget(icon_frame)

        header_text_col = QVBoxLayout()
        header_text_col.setSpacing(4)
        danger_title = QLabel("Compliance Warning — HIPAA")
        danger_title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_ERROR}; border: none;")
        header_text_col.addWidget(danger_title)

        danger_desc = QLabel(
            "Switching to Claude routes ticket and support data through Anthropic's "
            "infrastructure. This data may include PHI-adjacent fields. A valid "
            "Business Associate Agreement (BAA) between your organization and "
            "Anthropic is required.\n\n"
            "Enabling Claude without a BAA in place may constitute a HIPAA violation. "
            "Confirm with your compliance or legal team before proceeding."
        )
        danger_desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID}; border: none; line-height: 1.6;")
        danger_desc.setWordWrap(True)
        header_text_col.addWidget(danger_desc)
        header_row.addLayout(header_text_col, 1)
        self._claude_inner.addLayout(header_row)

        # Divider
        div = QFrame()
        div.setFixedHeight(1)
        div.setStyleSheet(f"background: {ALMA_ERROR}; border: none;")
        self._claude_inner.addWidget(div)

        # Acknowledgment checkboxes
        ack_label = QLabel("REQUIRED ACKNOWLEDGMENTS")
        ack_label.setStyleSheet(
            f"font-size: 10px; font-weight: 700; color: {ALMA_TEXT_LIGHT}; "
            f"letter-spacing: 1.2px; border: none;"
        )
        self._claude_inner.addWidget(ack_label)

        from PySide6.QtWidgets import QCheckBox
        self._claude_check_widgets = {}
        checks = [
            ("baa", "A signed BAA is in place between my organization and Anthropic"),
            ("hipaa", "I understand enabling Claude without a BAA may violate HIPAA"),
            ("legal", "I have confirmed this configuration with my compliance or legal team"),
        ]
        for key, text in checks:
            cb = QCheckBox(text)
            cb.setChecked(self._claude_checks.get(key, False))
            cb.setStyleSheet(f"""
                QCheckBox {{
                    font-size: 13px; color: {ALMA_TEXT_MID}; spacing: 10px; border: none;
                    padding: 4px 0;
                }}
                QCheckBox::indicator {{ width: 14px; height: 14px; }}
            """)
            cb.toggled.connect(lambda checked, k=key: self._on_claude_check_changed(k, checked))
            self._claude_check_widgets[key] = cb
            self._claude_inner.addWidget(cb)

        all_checked = all(self._claude_checks.values())

        if not all_checked:
            hint = QLabel("All acknowledgments must be checked before connecting.")
            hint.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT}; font-style: italic; border: none;")
            self._claude_inner.addWidget(hint)

        if all_checked:
            # Show API key input
            connect_card = QFrame()
            connect_card.setStyleSheet(f"""
                QFrame {{
                    background: {ALMA_BG_ELEVATED};
                    border: none;
                    border-radius: 5px;
                    padding: 14px 16px;
                }}
            """)
            connect_layout = QVBoxLayout(connect_card)
            connect_layout.setContentsMargins(14, 14, 14, 14)
            connect_layout.setSpacing(8)

            connect_title = QLabel("Connect Claude")
            connect_title.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {ALMA_TEXT_DARK}; border: none;")
            connect_layout.addWidget(connect_title)

            connect_desc = QLabel("Enter your Anthropic API key to enable Claude models.")
            connect_desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT}; border: none;")
            connect_layout.addWidget(connect_desc)

            key_row = QHBoxLayout()
            self._claude_key_input = QLineEdit()
            self._claude_key_input.setEchoMode(QLineEdit.Password)
            self._claude_key_input.setPlaceholderText("sk-ant-api03-...")
            self._claude_key_input.setStyleSheet(f"""
                QLineEdit {{
                    color: {ALMA_TEXT_DARK}; background: {ALMA_WHITE};
                    border: 1px solid {ALMA_BORDER}; border-radius: 8px;
                    padding: 8px 12px; font-size: 13px;
                }}
            """)
            key_row.addWidget(self._claude_key_input, 1)

            save_btn = QPushButton("Save API Key")
            save_btn.setStyleSheet(self._primary_btn_style())
            save_btn.setCursor(Qt.PointingHandCursor)
            save_btn.clicked.connect(self._on_claude_save_key)
            key_row.addWidget(save_btn)
            connect_layout.addLayout(key_row)

            self._claude_inner.addWidget(connect_card)

        # Cancel button
        cancel_btn = QPushButton("← Cancel")
        cancel_btn.setFlat(True)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_TEXT_MID};
                border: none; font-size: 11px; padding: 4px 0;
            }}
            QPushButton:hover {{ color: {ALMA_TEXT_DARK}; }}
        """)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.clicked.connect(self._on_claude_cancel)
        self._claude_inner.addWidget(cancel_btn)

    def _on_claude_check_changed(self, key, checked):
        self._claude_checks[key] = checked
        self._render_claude_state()

    def _on_claude_cancel(self):
        self._claude_pending = False
        self._claude_checks = {"baa": False, "hipaa": False, "legal": False}
        self._render_claude_state()

    def _on_claude_save_key(self):
        key = self._claude_key_input.text().strip()
        if not key:
            return
        from src.data.pat_store import save_setting
        save_setting("anthropic_api_key", key)

        # Enable all Claude models in registry
        try:
            from src.llm.model_registry import ModelRegistry
            registry = ModelRegistry.instance()
            for m in registry.all_models():
                if m.provider == "claude":
                    registry.enable(m.id)
        except Exception:
            pass

        self._claude_pending = False
        self._render_claude_state()
        self._refresh_model_combo()
        self.settings_changed.emit({"claude_connected": True})

    def _render_claude_connected(self):
        """Show connected state with disconnect option."""
        status_row = QHBoxLayout()
        status_lbl = QLabel("🟢  Claude connected — API key configured")
        status_lbl.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {ALMA_SUCCESS}; border: none;")
        status_row.addWidget(status_lbl, 1)

        disconnect_btn = QPushButton("Disconnect")
        disconnect_btn.setStyleSheet(self._ghost_btn_style())
        disconnect_btn.setCursor(Qt.PointingHandCursor)
        disconnect_btn.clicked.connect(self._on_claude_disconnect)
        status_row.addWidget(disconnect_btn)
        self._claude_inner.addLayout(status_row)

        # Show available Claude models
        try:
            from src.llm.model_registry import ModelRegistry
            registry = ModelRegistry.instance()
            claude_models = [m for m in registry.all_models() if m.provider == "claude"]
        except Exception:
            claude_models = []

        if claude_models:
            models_lbl = QLabel("Available Claude models:")
            models_lbl.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID}; border: none;")
            self._claude_inner.addWidget(models_lbl)

            for m in claude_models:
                m_lbl = QLabel(f"  • {m.display_name}")
                m_lbl.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT}; border: none;")
                self._claude_inner.addWidget(m_lbl)

    def _on_claude_disconnect(self):
        reply = QMessageBox.question(
            self, "Disconnect Claude",
            "This will remove your Anthropic API key and disable all Claude models. Continue?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        from src.data.pat_store import save_setting
        save_setting("anthropic_api_key", "")

        # Disable Claude models in registry
        try:
            from src.llm.model_registry import ModelRegistry
            registry = ModelRegistry.instance()
            for m in registry.all_models():
                if m.provider == "claude":
                    registry.disable(m.id)
        except Exception:
            pass

        self._render_claude_state()
        self._refresh_model_combo()
        self.settings_changed.emit({"claude_disconnected": True})

    # ── Task Routing section ──

    def _build_task_routing_section(self, lay):
        """Build Task Routing card: per-task provider mapping + override toggle."""
        from src.gemini.client_factory import get_task_routing, _DEFAULT_ROUTES

        card = self._card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(12)

        desc = QLabel(
            "Route AI tasks to the optimal provider. PHI-bearing tasks "
            "(NLP classification, VOC analysis) are locked to Gemini. "
            "Ops tasks can be routed to Claude when configured."
        )
        desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        desc.setWordWrap(True)
        card_layout.addWidget(desc)

        # Override All toggle
        override_row = QHBoxLayout()
        override_lbl_col = QVBoxLayout()
        override_lbl_col.setSpacing(2)
        override_title = QLabel("Override All")
        override_title.setStyleSheet(
            f"font-size: 14px; font-weight: 600; color: {ALMA_TEXT_DARK};"
        )
        override_desc = QLabel("Force all tasks to a single provider (for single-provider environments)")
        override_desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        override_desc.setWordWrap(True)
        override_lbl_col.addWidget(override_title)
        override_lbl_col.addWidget(override_desc)
        override_row.addLayout(override_lbl_col, 1)

        self._override_combo = QComboBox()
        self._override_combo.addItem("Disabled", "")
        self._override_combo.addItem("Gemini Only", "gemini")
        self._override_combo.addItem("Claude Only", "claude")
        self._override_combo.setStyleSheet(f"""
            QComboBox {{
                color: {ALMA_TEXT_DARK}; background: {ALMA_WHITE};
                border: 1px solid {ALMA_BORDER}; border-radius: 8px;
                padding: 6px 10px; font-size: 12px; min-width: 130px;
            }}
        """)
        override_row.addWidget(self._override_combo)
        card_layout.addLayout(override_row)

        # Divider
        div = QFrame()
        div.setStyleSheet(
            f"background: {ALMA_BORDER_LIGHT}; min-height: 1px; max-height: 1px; margin: 4px 0;"
        )
        card_layout.addWidget(div)

        # Task routing table
        # PHI tasks (locked to Gemini)
        _PHI_TASKS = {"nlp_classification", "voc_analysis", "report_generation"}
        _TASK_LABELS = {
            "nlp_classification": "NLP Classification",
            "voc_analysis": "VOC Analysis",
            "report_generation": "Report Generation",
            "guru_analysis": "Guru Analysis",
            "guru_content_generation": "Guru Content Generation",
            "watchlist_triage": "Watchlist Triage",
            "meta_analytics": "Meta Analytics",
            "ab_comparison": "A/B Comparison",
        }

        routing = get_task_routing()
        routes = routing.get("routes", dict(_DEFAULT_ROUTES))

        self._route_combos = {}

        for task_type in _DEFAULT_ROUTES:
            row = QHBoxLayout()
            row.setContentsMargins(0, 2, 0, 2)

            label_text = _TASK_LABELS.get(task_type, task_type)
            is_phi = task_type in _PHI_TASKS

            lbl = QLabel(label_text)
            lbl.setStyleSheet(
                f"font-size: 13px; color: {ALMA_TEXT_DARK}; font-weight: 500;"
            )
            row.addWidget(lbl, 1)

            if is_phi:
                # PHI lock indicator
                lock_lbl = QLabel("Gemini (PHI)")
                lock_lbl.setStyleSheet(
                    f"font-size: 12px; color: {ALMA_SUCCESS}; font-weight: 600;"
                )
                row.addWidget(lock_lbl)
            else:
                combo = QComboBox()
                combo.addItem("Gemini", "gemini")
                combo.addItem("Claude", "claude")
                combo.setStyleSheet(f"""
                    QComboBox {{
                        color: {ALMA_TEXT_DARK}; background: {ALMA_WHITE};
                        border: 1px solid {ALMA_BORDER}; border-radius: 6px;
                        padding: 4px 8px; font-size: 12px; min-width: 100px;
                    }}
                """)
                current = routes.get(task_type, _DEFAULT_ROUTES[task_type])
                idx = combo.findData(current)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
                combo.currentIndexChanged.connect(self._on_task_route_changed)
                self._route_combos[task_type] = combo
                row.addWidget(combo)

            card_layout.addLayout(row)

        # Load override combo state
        override_val = routing.get("override_all", "")
        oidx = self._override_combo.findData(override_val)
        if oidx >= 0:
            self._override_combo.setCurrentIndex(oidx)
        self._override_combo.currentIndexChanged.connect(self._on_task_route_changed)

        lay.addWidget(card)

    def _on_task_route_changed(self, _idx=None):
        """Persist task routing changes to settings."""
        if self._loading:
            return
        from src.gemini.client_factory import _DEFAULT_ROUTES

        routes = {}
        for task_type in _DEFAULT_ROUTES:
            if task_type in self._route_combos:
                routes[task_type] = self._route_combos[task_type].currentData()
            else:
                routes[task_type] = _DEFAULT_ROUTES[task_type]

        override_all = self._override_combo.currentData() or ""

        from src.data.settings_manager import load_settings, save_settings
        cfg = load_settings()
        if "ai" not in cfg:
            cfg["ai"] = {}
        cfg["ai"]["task_routing"] = {
            "override_all": override_all,
            "routes": routes,
        }
        save_settings(cfg)

    # ═══════════════════════════════════════════
    #  TAB 2: INTEGRATIONS
    # ═══════════════════════════════════════════

    def _build_integrations_tab(self, lay):
        """Build Integrations tab: Lightdash, Datasets, Google Drive, Interventions."""

        # ── Guru + Google credentials (shared panel) ──
        # The same widget the enablement Settings embeds — Guru email+PAT,
        # Google service-account file, and the per-user "Connect my Google
        # account" OAuth flow. LLM provider config stays in the AI Provider
        # tab here; both write the same keyring/settings store.
        from src.ui.widgets.credentials_panel import CredentialsPanel
        self.credentials = CredentialsPanel(sections=("external",))
        self.credentials.settings_changed.connect(self.settings_changed.emit)
        lay.addWidget(self.credentials)
        lay.addSpacing(24)

        # ── Lightdash Connection ──
        lay.addWidget(self._section_label("LIGHTDASH CONNECTION"))
        lay.addSpacing(8)

        lh_card = self._card()
        lh_layout = QVBoxLayout(lh_card)
        lh_layout.setContentsMargins(20, 18, 20, 18)
        lh_layout.setSpacing(16)

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
            "Generate a PAT from your Lightdash profile. "
            "Use the lowest-privilege token possible (viewer access is sufficient)."
        )
        pat_hint.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT};")
        pat_hint.setWordWrap(True)
        lh_layout.addWidget(pat_label)
        lh_layout.addLayout(pat_row)
        lh_layout.addWidget(self.pat_status_label)
        lh_layout.addWidget(pat_hint)

        div = QFrame()
        div.setStyleSheet(f"background: {ALMA_BORDER_LIGHT}; min-height: 1px; max-height: 1px; margin: 4px 0;")
        lh_layout.addWidget(div)

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

        lay.addWidget(lh_card)
        lay.addSpacing(24)

        # ── Datasets ──
        lay.addWidget(self._section_label("DATASETS"))
        lay.addSpacing(8)

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

        self.dataset_list_widget = QWidget()
        self.dataset_list_layout = QVBoxLayout(self.dataset_list_widget)
        self.dataset_list_layout.setContentsMargins(0, 0, 0, 0)
        self.dataset_list_layout.setSpacing(6)
        self.dataset_card_layout.addWidget(self.dataset_list_widget)

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

        lay.addWidget(dataset_card)
        lay.addSpacing(24)

        # ── Google Drive Export ──
        lay.addWidget(self._section_label("GOOGLE DRIVE EXPORT"))
        lay.addSpacing(8)

        gdrive_card = self._card()
        gdrive_layout = QVBoxLayout(gdrive_card)
        gdrive_layout.setContentsMargins(20, 18, 20, 18)
        gdrive_layout.setSpacing(10)

        gdrive_desc = QLabel(
            "Export reports to Google Drive using a service account. "
            "Share the target folder with the service account email."
        )
        gdrive_desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        gdrive_desc.setWordWrap(True)
        gdrive_layout.addWidget(gdrive_desc)

        cred_row = QHBoxLayout()
        cred_lbl = QLabel("Credentials JSON")
        cred_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {ALMA_TEXT_MID};")
        cred_row.addWidget(cred_lbl)
        self._gdrive_cred_input = QLineEdit()
        self._gdrive_cred_input.setPlaceholderText("Path to service-account.json")
        self._gdrive_cred_input.setStyleSheet(f"""
            QLineEdit {{
                color: {ALMA_TEXT_DARK}; background: {ALMA_WHITE};
                border: 1px solid {ALMA_BORDER}; border-radius: 8px;
                padding: 6px 10px; font-size: 12px;
            }}
        """)
        cred_row.addWidget(self._gdrive_cred_input, 1)
        cred_browse = QPushButton("Browse")
        cred_browse.setCursor(Qt.PointingHandCursor)
        cred_browse.setStyleSheet(self._ghost_btn_style())
        cred_browse.clicked.connect(self._browse_gdrive_credentials)
        cred_row.addWidget(cred_browse)
        gdrive_layout.addLayout(cred_row)

        folder_row = QHBoxLayout()
        folder_lbl = QLabel("Folder ID")
        folder_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {ALMA_TEXT_MID};")
        folder_row.addWidget(folder_lbl)
        self._gdrive_folder_input = QLineEdit()
        self._gdrive_folder_input.setPlaceholderText("Google Drive folder ID")
        self._gdrive_folder_input.setStyleSheet(self._gdrive_cred_input.styleSheet())
        folder_row.addWidget(self._gdrive_folder_input, 1)
        gdrive_layout.addLayout(folder_row)

        gdrive_btn_row = QHBoxLayout()
        gdrive_save_btn = QPushButton("Save")
        gdrive_save_btn.setCursor(Qt.PointingHandCursor)
        gdrive_save_btn.setStyleSheet(self._primary_btn_style())
        gdrive_save_btn.clicked.connect(self._save_gdrive_settings)
        gdrive_btn_row.addWidget(gdrive_save_btn)

        gdrive_test_btn = QPushButton("Test Connection")
        gdrive_test_btn.setCursor(Qt.PointingHandCursor)
        gdrive_test_btn.setStyleSheet(self._ghost_btn_style())
        gdrive_test_btn.clicked.connect(self._test_gdrive_connection)
        gdrive_btn_row.addWidget(gdrive_test_btn)

        self._gdrive_status = QLabel("")
        self._gdrive_status.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT};")
        gdrive_btn_row.addWidget(self._gdrive_status)
        gdrive_btn_row.addStretch()
        gdrive_layout.addLayout(gdrive_btn_row)

        lay.addWidget(gdrive_card)
        lay.addSpacing(24)

        # ── Intervention Manager ──
        lay.addWidget(self._section_label("INTERVENTION MANAGER"))
        lay.addSpacing(8)

        iv_card = self._card()
        iv_layout = QVBoxLayout(iv_card)
        iv_layout.setContentsMargins(20, 18, 20, 18)
        iv_layout.setSpacing(10)

        iv_desc = QLabel(
            "Track process changes, payer launches, and other events that may affect ticket patterns. "
            "Interventions appear as markers on incident control charts."
        )
        iv_desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        iv_desc.setWordWrap(True)
        iv_layout.addWidget(iv_desc)

        self._iv_container = QVBoxLayout()
        self._iv_container.setSpacing(6)
        iv_layout.addLayout(self._iv_container)

        self._iv_empty_label = QLabel("No interventions defined.")
        self._iv_empty_label.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; padding: 8px;")
        self._iv_empty_label.setAlignment(Qt.AlignCenter)
        self._iv_container.addWidget(self._iv_empty_label)

        iv_btn_row = QHBoxLayout()
        self._add_iv_btn = QPushButton("+  Add Intervention")
        self._add_iv_btn.setCursor(Qt.PointingHandCursor)
        self._add_iv_btn.setStyleSheet(self._ghost_btn_style())
        self._add_iv_btn.clicked.connect(self._add_intervention)
        iv_btn_row.addWidget(self._add_iv_btn)
        iv_btn_row.addStretch()
        iv_layout.addLayout(iv_btn_row)

        lay.addWidget(iv_card)
        lay.addSpacing(24)

        # ── Disabled state overlay ──
        self._update_api_state(False)

        lay.addSpacing(24)

        # ── Search Index ──
        lay.addWidget(self._section_label("SEARCH INDEX"))
        lay.addSpacing(8)

        si_card = self._card()
        si_layout = QVBoxLayout(si_card)
        si_layout.setContentsMargins(20, 18, 20, 18)
        si_layout.setSpacing(12)

        si_desc = QLabel(
            "Rebuild the semantic search index used by Gemini Chat. "
            "This runs automatically after each NLP scan."
        )
        si_desc.setWordWrap(True)
        si_desc.setStyleSheet(
            f"font-size: 12px; color: {ALMA_TEXT_MID}; line-height: 18px;"
        )
        si_layout.addWidget(si_desc)

        si_btn_row = QHBoxLayout()
        si_btn_row.setSpacing(12)

        self._rebuild_index_btn = QPushButton("Rebuild Search Index")
        self._rebuild_index_btn.setStyleSheet(self._ghost_btn_style())
        self._rebuild_index_btn.setCursor(Qt.PointingHandCursor)
        self._rebuild_index_btn.clicked.connect(self._on_rebuild_search_index)
        si_btn_row.addWidget(self._rebuild_index_btn)

        self._rebuild_status_label = QLabel("")
        self._rebuild_status_label.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_LIGHT};"
        )
        si_btn_row.addWidget(self._rebuild_status_label)
        si_btn_row.addStretch()
        si_layout.addLayout(si_btn_row)

        lay.addWidget(si_card)
        lay.addSpacing(24)

        self._load_rebuild_status()

        # ── Data Sources ──
        lay.addWidget(self._section_label("DATA SOURCES"))
        lay.addSpacing(8)

        ds_card = self._card()
        ds_layout = QVBoxLayout(ds_card)
        ds_layout.setContentsMargins(20, 18, 20, 18)
        ds_layout.setSpacing(10)

        ds_title_row = QHBoxLayout()
        ds_title_col = QVBoxLayout()
        ds_title_col.setSpacing(2)
        ds_title = QLabel("Registered Sources")
        ds_title.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {ALMA_TEXT_DARK};")
        ds_desc = QLabel(
            "Data sources feed tickets into the warehouse. "
            "Each source has its own tables and import history."
        )
        ds_desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        ds_desc.setWordWrap(True)
        ds_title_col.addWidget(ds_title)
        ds_title_col.addWidget(ds_desc)
        ds_title_row.addLayout(ds_title_col, 1)
        ds_layout.addLayout(ds_title_row)

        # Source list container (populated dynamically)
        self._source_list_container = QVBoxLayout()
        ds_layout.addLayout(self._source_list_container)

        # Refresh button
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.setFixedHeight(28)
        refresh_btn.setFixedWidth(90)
        refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_TEXT_MID};
                border: 1px solid {ALMA_BORDER}; border-radius: 6px;
                padding: 4px 12px; font-size: 11px; font-weight: 500;
            }}
            QPushButton:hover {{ background: {ALMA_HOVER_LIGHT}; }}
        """)
        refresh_btn.clicked.connect(self._refresh_source_list)
        ds_layout.addWidget(refresh_btn)

        lay.addWidget(ds_card)
        lay.addSpacing(24)

        lay.addStretch()

    def _refresh_source_list(self):
        """Populate the source list from the source_registry table."""
        # Clear existing items
        while self._source_list_container.count():
            item = self._source_list_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        try:
            from src.data.source_registry import SourceRegistry
            registry = SourceRegistry(self._db.conn)
            sources = registry.list_sources()

            if not sources:
                lbl = QLabel("No sources registered. Import data to auto-register.")
                lbl.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT}; padding: 8px 0;")
                self._source_list_container.addWidget(lbl)
                return

            for src in sources:
                row = QHBoxLayout()
                name_lbl = QLabel(f"{src['source_name']}")
                name_lbl.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {ALMA_TEXT_DARK};")
                row.addWidget(name_lbl)

                type_lbl = QLabel(f"({src['source_type']})")
                type_lbl.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT};")
                row.addWidget(type_lbl)

                count_lbl = QLabel(f"{src.get('ticket_count', 0):,} tickets")
                count_lbl.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_MID};")
                row.addWidget(count_lbl)

                if src.get("is_default"):
                    default_badge = QLabel("DEFAULT")
                    default_badge.setStyleSheet(
                        f"font-size: 9px; font-weight: 700; color: {ALMA_GREEN_DARK}; "
                        f"padding: 2px 6px; border: 1px solid {ALMA_GREEN_DARK}; border-radius: 3px;"
                    )
                    row.addWidget(default_badge)

                row.addStretch()
                container = QWidget()
                container.setLayout(row)
                self._source_list_container.addWidget(container)

        except Exception as e:
            lbl = QLabel(f"Could not load sources: {e}")
            lbl.setStyleSheet(f"font-size: 12px; color: {ALMA_ERROR};")
            self._source_list_container.addWidget(lbl)

    # ═══════════════════════════════════════════
    #  TAB 3: UPDATES
    # ═══════════════════════════════════════════

    def _build_updates_tab(self, lay):
        """Build Updates tab — auto-update status + GitHub repo config."""
        from src import VERSION

        # ── Auto-Update Status ──
        lay.addWidget(self._section_label("AUTO-UPDATE"))
        lay.addSpacing(8)

        card = self._card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(10)

        status_row = QHBoxLayout()
        info_col = QVBoxLayout()
        info_col.setSpacing(4)
        title = QLabel("Auto-Update")
        title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK};")
        desc = QLabel(
            "Updates replace src/ and config/ only. "
            "Your database and credentials are never modified."
        )
        desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        desc.setWordWrap(True)
        info_col.addWidget(title)
        info_col.addWidget(desc)
        status_row.addLayout(info_col, 1)

        self._update_status_pill = QLabel("Up to date")
        self._update_status_pill.setStyleSheet(f"""
            font-size: 11px; font-weight: 600; color: {ALMA_SUCCESS};
            background: rgba(22,163,74,0.1); border-radius: 10px;
            padding: 3px 10px;
        """)
        status_row.addWidget(self._update_status_pill)
        card_layout.addLayout(status_row)

        # Version info
        version_frame = QFrame()
        version_frame.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_CREAM}; border-radius: 5px;
                padding: 10px 14px;
            }}
        """)
        version_layout = QHBoxLayout(version_frame)
        version_layout.setContentsMargins(14, 10, 14, 10)

        v_col = QVBoxLayout()
        v_label = QLabel("Current version")
        v_label.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none;")
        v_value = QLabel(f"v{VERSION}")
        v_value.setStyleSheet(f"font-size: 14px; font-weight: 700; font-family: monospace; color: {ALMA_TEXT_DARK}; border: none;")
        v_col.addWidget(v_label)
        v_col.addWidget(v_value)
        version_layout.addLayout(v_col)
        version_layout.addStretch()

        card_layout.addWidget(version_frame)

        # Update status message (hidden by default)
        self._update_msg = QLabel("")
        self._update_msg.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID};")
        self._update_msg.setWordWrap(True)
        self._update_msg.setVisible(False)
        card_layout.addWidget(self._update_msg)

        btn_row = QHBoxLayout()
        self._check_updates_btn = QPushButton("Check for Updates")
        self._check_updates_btn.setStyleSheet(self._ghost_btn_style())
        self._check_updates_btn.setCursor(Qt.PointingHandCursor)
        self._check_updates_btn.clicked.connect(self._on_check_updates)
        btn_row.addWidget(self._check_updates_btn)

        self._release_notes_btn = QPushButton("View Release Notes")
        self._release_notes_btn.setStyleSheet(self._ghost_btn_style())
        self._release_notes_btn.setCursor(Qt.PointingHandCursor)
        self._release_notes_btn.setEnabled(False)
        self._release_notes_btn.clicked.connect(self._on_view_release_notes)
        btn_row.addWidget(self._release_notes_btn)

        self._install_btn = QPushButton("Install Now")
        self._install_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_CREAM};
                border: none; border-radius: 8px; padding: 8px 20px;
                font-weight: 600; font-size: 13px;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
            QPushButton:disabled {{ background: {ALMA_BORDER}; color: {ALMA_TEXT_LIGHT}; }}
        """)
        self._install_btn.setCursor(Qt.PointingHandCursor)
        self._install_btn.setVisible(False)
        self._install_btn.clicked.connect(self._on_install_update)
        btn_row.addWidget(self._install_btn)

        btn_row.addStretch()
        card_layout.addLayout(btn_row)

        # Progress bar (hidden until download starts)
        self._update_progress = QProgressBar()
        self._update_progress.setRange(0, 100)
        self._update_progress.setValue(0)
        self._update_progress.setVisible(False)
        self._update_progress.setStyleSheet(f"""
            QProgressBar {{
                background: {ALMA_CREAM}; border: 1px solid {ALMA_BORDER};
                border-radius: 6px; height: 18px; text-align: center;
                font-size: 11px; color: {ALMA_TEXT_DARK};
            }}
            QProgressBar::chunk {{
                background: {ALMA_GREEN_DARK}; border-radius: 5px;
            }}
        """)
        card_layout.addWidget(self._update_progress)

        self._progress_label = QLabel("")
        self._progress_label.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_MID};")
        self._progress_label.setVisible(False)
        card_layout.addWidget(self._progress_label)

        # Restart button (shown after staging completes)
        self._restart_btn = QPushButton("Restart Now")
        self._restart_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_CREAM};
                border: none; border-radius: 8px; padding: 10px 28px;
                font-weight: 700; font-size: 14px;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
        """)
        self._restart_btn.setCursor(Qt.PointingHandCursor)
        self._restart_btn.setVisible(False)
        self._restart_btn.clicked.connect(self._on_restart_app)
        card_layout.addWidget(self._restart_btn)

        # Last-checked timestamp
        self._last_checked_label = QLabel("")
        self._last_checked_label.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_LIGHT};"
        )
        card_layout.addWidget(self._last_checked_label)
        self._load_last_checked()

        lay.addWidget(card)
        lay.addSpacing(24)

        # ── GitHub Repository Configuration ──
        lay.addWidget(self._section_label("GITHUB REPOSITORY"))
        lay.addSpacing(8)

        repo_card = self._card()
        repo_layout = QVBoxLayout(repo_card)
        repo_layout.setContentsMargins(20, 18, 20, 18)
        repo_layout.setSpacing(10)

        repo_desc = QLabel(
            "Configure the GitHub repository for update checks. "
            "For private repositories, provide a Personal Access Token (PAT) "
            "with read-only repo access."
        )
        repo_desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        repo_desc.setWordWrap(True)
        repo_layout.addWidget(repo_desc)

        fields_row = QHBoxLayout()
        fields_row.setSpacing(16)

        lbl_style = f"font-size: 11px; font-weight: 600; color: {ALMA_TEXT_MID}; letter-spacing: 0.5px;"
        field_style = f"""
            QLineEdit {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER};
                border-radius: 8px; padding: 8px 12px; font-size: 13px;
                color: {ALMA_TEXT_DARK};
            }}
        """

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("REPO URL")
        lbl.setStyleSheet(lbl_style)
        col.addWidget(lbl)
        self._github_repo_input = QLineEdit()
        self._github_repo_input.setPlaceholderText("owner/repo  (e.g. alma-health/alma-insights)")
        self._github_repo_input.setStyleSheet(field_style)
        col.addWidget(self._github_repo_input)
        fields_row.addLayout(col, 2)

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("GITHUB PAT (optional — for private repos)")
        lbl.setStyleSheet(lbl_style)
        col.addWidget(lbl)
        self._github_pat_input = QLineEdit()
        self._github_pat_input.setPlaceholderText("ghp_xxxxxxxxxxxxxxxxxxxx")
        self._github_pat_input.setEchoMode(QLineEdit.Password)
        self._github_pat_input.setStyleSheet(field_style)
        col.addWidget(self._github_pat_input)
        fields_row.addLayout(col, 2)

        repo_layout.addLayout(fields_row)

        save_row = QHBoxLayout()
        save_btn = QPushButton("Save Repository Settings")
        save_btn.setStyleSheet(self._ghost_btn_style())
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.clicked.connect(self._on_save_github_settings)
        save_row.addWidget(save_btn)

        self._github_status = QLabel("")
        self._github_status.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_MID};")
        save_row.addWidget(self._github_status, 1)
        save_row.addStretch()
        repo_layout.addLayout(save_row)

        lay.addWidget(repo_card)
        lay.addSpacing(24)

        self._build_support_and_recovery(lay)
        lay.addStretch()

    # ═══════════════════════════════════════════
    #  SUPPORT & RECOVERY  (Phase 5)
    # ═══════════════════════════════════════════

    def _build_support_and_recovery(self, lay):
        """Export crash reports + rollback to previous version."""
        from src.updater.rollback import current_state

        lay.addWidget(self._section_label("SUPPORT & RECOVERY"))
        lay.addSpacing(8)

        card = self._card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(10)

        desc = QLabel(
            "Export the last 20 crash reports as a zip for a support ticket, "
            "or roll back the app to the previous installed version."
        )
        desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        desc.setWordWrap(True)
        card_layout.addWidget(desc)

        btn_row = QHBoxLayout()

        self._export_crash_btn = QPushButton("Export Crash Reports")
        self._export_crash_btn.setStyleSheet(self._ghost_btn_style())
        self._export_crash_btn.setCursor(Qt.PointingHandCursor)
        self._export_crash_btn.clicked.connect(self._on_export_crash_reports)
        btn_row.addWidget(self._export_crash_btn)

        self._rollback_btn = QPushButton("Rollback to Previous Version")
        self._rollback_btn.setStyleSheet(self._ghost_btn_style())
        self._rollback_btn.setCursor(Qt.PointingHandCursor)
        self._rollback_btn.clicked.connect(self._on_manual_rollback)
        state = current_state()
        self._rollback_btn.setEnabled(state is not None)
        btn_row.addWidget(self._rollback_btn)

        btn_row.addStretch()
        card_layout.addLayout(btn_row)

        self._support_status = QLabel("")
        self._support_status.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID};")
        self._support_status.setWordWrap(True)
        card_layout.addWidget(self._support_status)

        lay.addWidget(card)

    def _on_export_crash_reports(self):
        """Zip the last 20 crash reports and let the user save them."""
        from datetime import datetime
        from src.core import crash_handler

        reports = crash_handler.list_reports(limit=20)
        if not reports:
            self._support_status.setText("No crash reports found — nothing to export.")
            return

        suggested = f"alma-crash-reports-{datetime.now():%Y%m%d-%H%M%S}.zip"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export crash reports", suggested, "Zip archive (*.zip)"
        )
        if not path:
            return

        try:
            from pathlib import Path
            crash_handler.export_bundle(Path(path), limit=20)
            self._support_status.setText(
                f"Exported {len(reports)} report(s) to {path}"
            )
        except OSError as exc:
            self._support_status.setText(f"Export failed: {exc}")

    def _on_manual_rollback(self):
        """Confirm + perform a user-initiated rollback to the previous version."""
        from src.updater.rollback import current_state, perform_rollback

        state = current_state()
        if state is None:
            self._support_status.setText("No previous version on disk.")
            self._rollback_btn.setEnabled(False)
            return

        previous = state.get("previous", "unknown")
        reply = QMessageBox.question(
            self,
            "Rollback to previous version",
            f"Restore the previous installed version (v{previous})?\n\n"
            "The app will need to restart after rollback.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        ok, message = perform_rollback()
        self._support_status.setText(message)
        if ok:
            self._rollback_btn.setEnabled(False)
            QMessageBox.information(
                self, "Rollback complete",
                "Restart Alma Insights for the change to take effect.",
            )

        # Load saved values
        self._load_github_settings()

    # ── Updates tab helpers ──

    def _load_github_settings(self):
        """Load GitHub repo + PAT from settings/credentials."""
        try:
            update_cfg = get_section("updates", {})
            repo = update_cfg.get("github_repo", "")
            self._github_repo_input.setText(repo)
        except Exception:
            pass
        try:
            from src.data.pat_store import load_setting
            pat = load_setting("github_pat", "")
            if pat:
                self._github_pat_input.setText(pat)
                self._github_status.setText("✓ PAT configured")
        except Exception:
            pass

    def _on_save_github_settings(self):
        """Save GitHub repo URL and PAT."""
        repo = self._github_repo_input.text().strip()
        pat = self._github_pat_input.text().strip()
        try:
            set_section("updates", {"github_repo": repo})
            if pat:
                from src.data.pat_store import save_setting
                save_setting("github_pat", pat)
            self._github_status.setText("✓ Saved")
        except Exception as e:
            self._github_status.setText(f"Error: {e}")

    def _on_check_updates(self):
        """Launch UpdateChecker in background."""
        self._check_updates_btn.setEnabled(False)
        self._check_updates_btn.setText("Checking…")
        self._update_msg.setVisible(False)

        # Build releases URL from saved repo or use default
        releases_url = None
        try:
            update_cfg = get_section("updates", {})
            repo = update_cfg.get("github_repo", "").strip()
            if repo:
                releases_url = f"https://api.github.com/repos/{repo}/releases/latest"
        except Exception:
            pass

        # Load PAT for auth header
        github_pat = None
        try:
            from src.data.pat_store import load_setting
            github_pat = load_setting("github_pat", "")
        except Exception:
            pass

        from src.updater.update_checker import UpdateChecker
        self._update_checker = UpdateChecker(
            parent=self, releases_url=releases_url, github_pat=github_pat
        )
        self._update_checker.update_available.connect(self._on_update_available)
        self._update_checker.up_to_date.connect(self._on_up_to_date)
        self._update_checker.check_failed.connect(self._on_check_failed)
        self._update_checker.check()

    def _on_update_available(self, current, new_ver, url):
        self._save_last_checked()
        self._check_updates_btn.setEnabled(True)
        self._check_updates_btn.setText("Check for Updates")
        self._update_status_pill.setText(f"v{new_ver} available")
        self._update_status_pill.setStyleSheet(f"""
            font-size: 11px; font-weight: 600; color: {ALMA_WARNING};
            background: rgba(245,158,11,0.1); border-radius: 10px;
            padding: 3px 10px;
        """)
        self._update_msg.setText(
            f"Version {new_ver} is available (you have v{current})."
        )
        self._update_msg.setVisible(True)
        self._latest_release_url = url
        self._latest_new_version = new_ver
        self._release_notes_btn.setEnabled(bool(url))
        # Build download URL from release page URL
        # GitHub pattern: html_url ends with /releases/tag/vX.Y.Z
        # Download: /releases/download/vX.Y.Z/alma-insights-vX.Y.Z.zip
        self._latest_download_url = ""
        if url and "github.com" in url:
            # Construct asset download URL
            base = url.rsplit("/releases/", 1)[0] if "/releases/" in url else ""
            if base:
                tag = f"v{new_ver}"
                self._latest_download_url = (
                    f"{base}/releases/download/{tag}/"
                    f"alma-insights-{tag}.zip"
                )
        self._install_btn.setVisible(bool(self._latest_download_url))

    def _on_up_to_date(self):
        self._check_updates_btn.setEnabled(True)
        self._check_updates_btn.setText("Check for Updates")
        self._update_status_pill.setText("Up to date")
        self._update_status_pill.setStyleSheet(f"""
            font-size: 11px; font-weight: 600; color: {ALMA_SUCCESS};
            background: rgba(22,163,74,0.1); border-radius: 10px;
            padding: 3px 10px;
        """)
        self._update_msg.setText("You are running the latest version.")
        self._update_msg.setVisible(True)
        self._save_last_checked()

    def _on_check_failed(self, error_msg):
        self._check_updates_btn.setEnabled(True)
        self._check_updates_btn.setText("Check for Updates")
        self._update_status_pill.setText("Check failed")
        self._update_status_pill.setStyleSheet(f"""
            font-size: 11px; font-weight: 600; color: {ALMA_ERROR};
            background: rgba(220,38,38,0.1); border-radius: 10px;
            padding: 3px 10px;
        """)
        self._update_msg.setText(error_msg)
        self._update_msg.setVisible(True)
        self._save_last_checked()

    def _on_view_release_notes(self):
        url = getattr(self, "_latest_release_url", "")
        if url:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl(url))

    def _on_install_update(self):
        """Download and stage the update.

        2026-05-07 (Piece 1): the install path now resolves the
        platform-specific zip + its SHA-256 from the release's
        ``manifest.json`` before calling :meth:`Updater.stage`. This
        closes the long-standing gap where ``Updater.stage`` was
        invoked with no checksum and the ``require_checksum=True``
        guard immediately refused the install.
        """
        new_ver = getattr(self, "_latest_new_version", "")

        # Show progress + hide install button up front — keeps the UI
        # responsive even if the manifest fetch takes a moment.
        self._install_btn.setVisible(False)
        self._update_progress.setValue(0)
        self._update_progress.setVisible(True)
        self._progress_label.setText("Resolving release manifest...")
        self._progress_label.setVisible(True)

        try:
            url, sha, _size = self._resolve_install_artifact()
        except Exception as exc:  # noqa: BLE001 — surface the message
            self._on_update_failed(str(exc))
            return

        from src.updater.updater import Updater
        self._updater = Updater(parent=self)
        self._updater.progress.connect(self._on_update_progress)
        self._updater.complete.connect(self._on_update_complete)
        self._updater.failed.connect(self._on_update_failed)
        self._progress_label.setText("Starting download...")
        self._updater.stage(url, expected_sha256=sha, new_version=new_ver)

    def _resolve_install_artifact(self):
        """Look up download URL + SHA-256 for the running platform.

        Centralizes the manifest fetch so :meth:`_on_install_update` and
        any future caller (the splash flow, in particular) don't repeat
        themselves.

        Returns ``(download_url, sha256, size_bytes_or_none)``. Raises
        :class:`ManifestFetchError` (or a generic Exception with a
        readable message) on any failure — caller funnels that into
        :meth:`_on_update_failed`.
        """
        from src.updater.manifest_fetcher import resolve_release_artifact

        # Pull the assets array + token straight off the checker that
        # last fired `update_available`. Avoids a redundant GitHub call.
        checker = getattr(self, "_update_checker", None)
        assets = getattr(checker, "last_assets", None) if checker else None
        token = getattr(checker, "last_token", "") if checker else ""

        return resolve_release_artifact(assets, token=token)

    def _on_update_progress(self, pct, msg):
        self._update_progress.setValue(pct)
        self._progress_label.setText(msg)

    def _on_update_complete(self):
        self._update_progress.setVisible(False)
        self._progress_label.setText("Update staged. Restart to apply.")
        self._restart_btn.setVisible(True)

    def _on_update_failed(self, msg):
        self._update_progress.setVisible(False)
        self._progress_label.setVisible(False)
        self._update_msg.setText(f"Update failed: {msg}")
        self._update_msg.setVisible(True)
        self._install_btn.setVisible(True)

    def _on_restart_app(self):
        """Restart the application to apply the staged update.

        2026-05-07 (Piece 2B): switched from a bare ``QApplication.quit()``
        to :func:`src.updater.restart.restart_app`, which spawns the
        replacement process before quitting so the user lands back in
        the running app rather than having to re-launch by hand. We
        also confirm intent via a small modal — relaunching while a
        scan is mid-flight would lose work.
        """
        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self,
            "Restart to apply update",
            "Restart Alma Insights now to apply the staged update?\n\n"
            "Any in-progress work in this session will be discarded.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return

        from src.updater.restart import restart_app
        try:
            restart_app()
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Restart failed",
                f"Could not restart automatically: {exc}\n\n"
                "Please close Alma Insights and reopen it manually — "
                "the staged update will apply on next launch.",
            )

    def _save_last_checked(self):
        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        set_section("updates", {"last_checked": ts})
        self._last_checked_label.setText(f"Last checked: {ts}")

    def _load_last_checked(self):
        section = get_section("updates")
        ts = section.get("last_checked", "")
        if ts:
            self._last_checked_label.setText(f"Last checked: {ts}")

    # ═══════════════════════════════════════════
    #  TAB 4: DISPLAY
    # ═══════════════════════════════════════════

    def _on_default_mode_changed(self):
        """Persist the startup-mode choice (product / enablement / last)."""
        from src.data.settings_manager import update_section
        try:
            value = self._default_mode_combo.currentData() or "product"
            update_section("app", {"default_mode": value})
        except Exception:
            pass

    def _build_display_tab(self, lay):
        """Build Display tab: app mode, test data, display prefs, AI enhancements, behavior settings."""

        # ── App Mode ──
        lay.addWidget(self._section_label("APP MODE"))
        lay.addSpacing(8)

        mode_card = self._card()
        mode_layout = QVBoxLayout(mode_card)
        mode_layout.setContentsMargins(20, 18, 20, 18)
        mode_layout.setSpacing(10)

        mode_row = QHBoxLayout()
        mode_lbl_col = QVBoxLayout()
        mode_lbl_col.setSpacing(2)
        mode_title = QLabel("Default Mode at Startup")
        mode_title.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {ALMA_TEXT_DARK};")
        mode_desc = QLabel(
            "Which workspace the app opens in: Product (full analytics suite) "
            "or Enablement (lightweight workbench). 'Last used' reopens "
            "whichever mode was active when the app closed."
        )
        mode_desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        mode_desc.setWordWrap(True)
        mode_lbl_col.addWidget(mode_title)
        mode_lbl_col.addWidget(mode_desc)
        mode_row.addLayout(mode_lbl_col, 1)

        self._default_mode_combo = QComboBox()
        self._default_mode_combo.addItem("Product", "product")
        self._default_mode_combo.addItem("Enablement", "enablement")
        self._default_mode_combo.addItem("Last used", "last")
        try:
            from src.data.settings_manager import get_section
            _current_mode = (get_section("app", {}) or {}).get("default_mode", "product")
        except Exception:
            _current_mode = "product"
        _mode_idx = self._default_mode_combo.findData(_current_mode)
        if _mode_idx >= 0:
            self._default_mode_combo.setCurrentIndex(_mode_idx)
        self._default_mode_combo.currentIndexChanged.connect(
            self._on_default_mode_changed
        )
        mode_row.addWidget(self._default_mode_combo)
        mode_layout.addLayout(mode_row)

        lay.addWidget(mode_card)
        lay.addSpacing(24)

        # ── Development ──
        lay.addWidget(self._section_label("DEVELOPMENT"))
        lay.addSpacing(8)

        test_card = self._card()
        test_layout = QVBoxLayout(test_card)
        test_layout.setContentsMargins(20, 18, 20, 18)
        test_layout.setSpacing(10)

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

        lay.addWidget(test_card)
        lay.addSpacing(24)

        # ── AI Enhancements ──
        lay.addWidget(self._section_label("AI ENHANCEMENTS"))
        lay.addSpacing(8)

        ai_card = self._card()
        ai_layout = QVBoxLayout(ai_card)
        ai_layout.setContentsMargins(20, 18, 20, 18)
        ai_layout.setSpacing(10)

        smooth_row = QHBoxLayout()
        smooth_lbl_col = QVBoxLayout()
        smooth_lbl_col.setSpacing(2)
        smooth_title = QLabel("AI Cluster Smoothing")
        smooth_title.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {ALMA_TEXT_DARK};")
        smooth_desc = QLabel(
            "Use the active model to refine NMF topic cluster labels. Requires configured AI provider."
        )
        smooth_desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        smooth_desc.setWordWrap(True)
        smooth_lbl_col.addWidget(smooth_title)
        smooth_lbl_col.addWidget(smooth_desc)
        smooth_row.addLayout(smooth_lbl_col, 1)
        self.ai_smoothing_toggle = ToggleSwitch(checked=False)
        self.ai_smoothing_toggle.toggled.connect(self._persist_ai_settings)
        smooth_row.addWidget(self.ai_smoothing_toggle)
        ai_layout.addLayout(smooth_row)

        kw_row = QHBoxLayout()
        kw_lbl_col = QVBoxLayout()
        kw_lbl_col.setSpacing(2)
        kw_title = QLabel("AI Keyword Suggestions")
        kw_title.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {ALMA_TEXT_DARK};")
        kw_desc = QLabel(
            "Suggest noise terms to suppress and new concept map entries after trending analysis."
        )
        kw_desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        kw_desc.setWordWrap(True)
        kw_lbl_col.addWidget(kw_title)
        kw_lbl_col.addWidget(kw_desc)
        kw_row.addLayout(kw_lbl_col, 1)
        self.ai_keywords_toggle = ToggleSwitch(checked=False)
        self.ai_keywords_toggle.toggled.connect(self._persist_ai_settings)
        kw_row.addWidget(self.ai_keywords_toggle)
        ai_layout.addLayout(kw_row)

        lay.addWidget(ai_card)
        lay.addSpacing(24)

        # ── Display Preferences ──
        lay.addWidget(self._section_label("DISPLAY PREFERENCES"))
        lay.addSpacing(8)

        display_card = self._card()
        display_layout = QVBoxLayout(display_card)
        display_layout.setContentsMargins(20, 18, 20, 18)
        display_layout.setSpacing(10)

        layman_row = QHBoxLayout()
        layman_lbl_col = QVBoxLayout()
        layman_lbl_col.setSpacing(2)
        layman_title = QLabel("Simplified Language Mode")
        layman_title.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {ALMA_TEXT_DARK};")
        layman_desc = QLabel(
            'Replace technical metrics with plain-language equivalents.\n'
            'Example: "VADER compound: -0.34" becomes "Customer Mood: Negative (34%)"'
        )
        layman_desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        layman_desc.setWordWrap(True)
        layman_lbl_col.addWidget(layman_title)
        layman_lbl_col.addWidget(layman_desc)
        layman_row.addLayout(layman_lbl_col, 1)

        self.layman_mode_toggle = ToggleSwitch(checked=False)
        self.layman_mode_toggle.toggled.connect(self._persist_display_settings)
        layman_row.addWidget(self.layman_mode_toggle)
        display_layout.addLayout(layman_row)

        lay.addWidget(display_card)
        lay.addSpacing(24)

        # ── Behavior sections (use self.layout_inner trick) ──
        self._build_auto_analysis_section()
        self._build_calendar_sync_section()
        self._build_source_sync_section()
        self._build_section_defaults_section()
        self._build_memory_debug_section()

        # ── Data Management (Danger Zone) ──
        self._build_data_management_section()

        lay.addStretch()

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

    def _build_hardware_profile_section(self, lay):
        """Read-only panel showing detected hardware + active model device.

        Sourced from ``data/hardware_profile.json`` (written by
        ``src.startup.hardware``). Includes a ``Re-profile now`` button
        that forces fresh detection. Power-users who need to override
        can edit the JSON directly — no UI knobs to keep the surface
        area small.
        """
        card = self._card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(20, 18, 20, 18)
        cl.setSpacing(10)

        desc = QLabel(
            "Detected at startup and used by the embedding pipeline. "
            "If you move this install to a different machine, the profile "
            "regenerates automatically on next launch."
        )
        desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        desc.setWordWrap(True)
        cl.addWidget(desc)

        # Two-column key/value grid of detected values
        from PySide6.QtWidgets import QGridLayout
        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(6)

        self._hw_value_labels: dict[str, QLabel] = {}
        rows = [
            ("Accelerator", "accelerator_display"),
            ("Embedding device", "embedding_device_display"),
            ("Batch size", "embedding_batch_size"),
            ("Max sequence length", "embedding_max_seq_length"),
            ("RAM", "ram_display"),
            ("CPU cores", "cpu_count"),
            ("Architecture", "arch"),
        ]
        for r, (label_text, key) in enumerate(rows):
            k_lbl = QLabel(label_text)
            k_lbl.setStyleSheet(
                f"font-size: 12px; font-weight: 600; color: {ALMA_TEXT_MID};"
            )
            v_lbl = QLabel("—")
            v_lbl.setStyleSheet(
                f"font-size: 12px; color: {ALMA_TEXT_DARK}; "
                f"font-family: 'Cascadia Code', Consolas, 'SF Mono', monospace;"
            )
            grid.addWidget(k_lbl, r, 0)
            grid.addWidget(v_lbl, r, 1)
            self._hw_value_labels[key] = v_lbl
        cl.addLayout(grid)

        # Action row
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._hw_status_label = QLabel("")
        self._hw_status_label.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_LIGHT};"
        )
        btn_row.addWidget(self._hw_status_label)

        reprofile_btn = QPushButton("Re-profile now")
        reprofile_btn.setCursor(Qt.PointingHandCursor)
        reprofile_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_GREEN_DARK};
                border: 1px solid {ALMA_GREEN_DARK}; border-radius: 6px;
                padding: 6px 14px; font-size: 12px; font-weight: 600;
            }}
            QPushButton:hover {{ background: rgba(20, 87, 63, 0.06); }}
        """)
        reprofile_btn.setToolTip(
            "Force fresh hardware detection. Writes data/hardware_profile.json "
            "with current CPU / RAM / accelerator values. Effective on next "
            "embedding model load."
        )
        reprofile_btn.clicked.connect(self._on_reprofile_clicked)
        btn_row.addWidget(reprofile_btn)
        cl.addLayout(btn_row)

        lay.addWidget(card)

        # Populate fields from current profile on first build
        self._refresh_hardware_profile_panel()

    def _refresh_hardware_profile_panel(self):
        """Re-read data/hardware_profile.json and populate the labels."""
        if not hasattr(self, "_hw_value_labels"):
            return
        try:
            import json
            from pathlib import Path
            profile_path = Path("data") / "hardware_profile.json"
            if not profile_path.exists():
                self._hw_status_label.setText(
                    "No profile cached — click 'Re-profile now' to detect."
                )
                return
            data = json.loads(profile_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self._hw_status_label.setText(f"Could not read profile: {exc}")
            return

        # Friendly accelerator label with device name + arch
        acc = data.get("accelerator", "unknown")
        arch = data.get("arch", "")
        gpu = data.get("gpu_name") or ""
        if acc == "mps":
            acc_display = f"Apple Silicon (MPS)  {arch}"
        elif acc == "cuda":
            vram = data.get("vram_gb", 0)
            acc_display = f"NVIDIA CUDA — {gpu} ({vram} GB VRAM)"
        else:
            acc_display = f"CPU only  {arch}"

        device = data.get("embedding_device", "cpu")
        fb = data.get("embedding_device_fallback_reason")
        if fb:
            device_display = f"{device}  ({fb})"
        else:
            device_display = device

        ram = data.get("ram_gb")
        ram_display = f"{ram} GB" if ram else "unknown"

        values = {
            "accelerator_display": acc_display,
            "embedding_device_display": device_display,
            "embedding_batch_size": str(data.get("embedding_batch_size", "—")),
            "embedding_max_seq_length": str(data.get("embedding_max_seq_length", "—")),
            "ram_display": ram_display,
            "cpu_count": str(data.get("cpu_count", "—")),
            "arch": data.get("arch", "—"),
        }
        for key, val in values.items():
            lbl = self._hw_value_labels.get(key)
            if lbl is not None:
                lbl.setText(val)

        profiled = data.get("profiled_at", "")
        if profiled:
            self._hw_status_label.setText(f"Profiled {profiled[:19]} UTC")

    def _on_reprofile_clicked(self):
        """Force fresh hardware detection + refresh the panel."""
        try:
            from src.startup import hardware as hw
            fresh = hw.profile(current_version=getattr(self, "_app_version", "unknown"))
            hw.save(fresh)
            # Reset the cached embedding model singleton so next encode picks
            # up the new device on the next call.
            try:
                from src.data.embedding import model_loader
                model_loader._model = None
                model_loader._active_device = None
            except Exception:
                pass
            self._refresh_hardware_profile_panel()
            self._hw_status_label.setText("Re-profiled successfully.")
        except Exception as exc:
            self._hw_status_label.setText(f"Re-profile failed: {exc}")

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
                background: {ALMA_BG_ELEVATED}; border: none;
                border-radius: 12px;
            }}
        """)
        apply_card_shadow(card)
        return card

    def _build_toggle_row(self, title, description, toggle, indent=0):
        """Build a standard toggle row with title, description, and toggle switch.

        Returns the QHBoxLayout so the caller can add it to a parent layout.
        If indent > 0, adds left margin for visual nesting.
        """
        row = QHBoxLayout()
        if indent:
            row.setContentsMargins(indent, 0, 0, 0)
        lbl_col = QVBoxLayout()
        lbl_col.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {ALMA_TEXT_DARK};")
        d = QLabel(description)
        d.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        d.setWordWrap(True)
        lbl_col.addWidget(t)
        lbl_col.addWidget(d)
        row.addLayout(lbl_col, 1)
        row.addWidget(toggle)
        return row

    def _divider_line(self):
        """Return a 1px horizontal divider."""
        div = QFrame()
        div.setFixedHeight(1)
        div.setStyleSheet(f"background: {ALMA_BORDER_LIGHT};")
        return div

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

        # Restore Gemini settings
        self._load_gemini_settings()

        # Restore Google Drive settings
        self._load_gdrive_settings()

        # Restore AI enhancement settings
        self._load_ai_settings()

        # Restore behavior settings (auto-analysis, calendar sync, etc.)
        self._load_behavior_settings()

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

    # ── Search Index Rebuild ──

    def _load_rebuild_status(self):
        """Show last-rebuilt timestamp from embedding metadata."""
        try:
            from src.data.db_manager import DatabaseManager
            conn = DatabaseManager().conn
            row = conn.execute(
                "SELECT COUNT(*), MAX(created_at) FROM ticket_embeddings"
            ).fetchone()
            if row and row[0]:
                self._rebuild_status_label.setText(
                    f"Last rebuilt: {row[1][:16]} · {row[0]} tickets indexed"
                )
            else:
                self._rebuild_status_label.setText("No search index built yet")
        except Exception:
            self._rebuild_status_label.setText("")

    def _on_rebuild_search_index(self):
        """Start background embedding rebuild."""
        from src.data.db_manager import DatabaseManager
        db_mgr = DatabaseManager()
        db_path = str(db_mgr.db_path)

        # Estimate time: ~0.7s per ticket on CPU (based on 888 tickets / ~10 min)
        try:
            ticket_count = db_mgr.conn.execute(
                "SELECT COUNT(*) FROM ticket_index"
            ).fetchone()[0]
            est_min = max(1, round(ticket_count * 0.7 / 60))
            est_text = f"Indexing {ticket_count} tickets \u2014 ~{est_min} min"
        except Exception:
            est_text = "Indexing tickets..."

        self._rebuild_index_btn.setEnabled(False)
        self._rebuild_index_btn.setText("Rebuilding\u2026")
        self._rebuild_status_label.setText(est_text)
        self._rebuild_status_label.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_MID};"
        )

        self._embed_worker = EmbeddingRebuildWorker(db_path)
        self._embed_worker.finished.connect(self._on_rebuild_finished)
        self._embed_worker.error.connect(self._on_rebuild_error)
        self._embed_worker.start()

    def _on_rebuild_finished(self, count: int):
        """Handle successful embedding rebuild."""
        self._rebuild_index_btn.setEnabled(True)
        self._rebuild_index_btn.setText("Rebuild Search Index")
        self._rebuild_status_label.setText(
            f"\u2713 Rebuilt {count} tickets \u00b7 just now"
        )
        self._rebuild_status_label.setStyleSheet(
            f"font-size: 11px; color: {ALMA_GREEN_DARK};"
        )

    def _on_rebuild_error(self, msg: str):
        """Handle embedding rebuild failure."""
        self._rebuild_index_btn.setEnabled(True)
        self._rebuild_index_btn.setText("Rebuild Search Index")
        self._rebuild_status_label.setText(f"Error: {msg[:80]}")
        self._rebuild_status_label.setStyleSheet(
            f"font-size: 11px; color: #c0392b;"
        )

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

    # ═══════════════════════════════════════════
    #  GEMINI CONFIGURATION — State Machine
    # ═══════════════════════════════════════════

    # Shared button style helpers
    def _ghost_btn_style(self):
        return f"""
            QPushButton {{
                background: {ALMA_WHITE}; color: {ALMA_TEXT_DARK};
                border: 1px solid {ALMA_BORDER}; border-radius: 6px;
                padding: 6px 14px; font-size: 12px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_CREAM}; }}
            QPushButton:disabled {{ color: {ALMA_TEXT_LIGHT}; border-color: {ALMA_BORDER_LIGHT}; }}
        """

    def _primary_btn_style(self):
        return f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_TEXT_ON_DARK};
                border: none; border-radius: 6px;
                padding: 8px 18px; font-size: 12px; font-weight: 700;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_LIGHT}; }}
            QPushButton:disabled {{ background: {ALMA_BORDER}; color: {ALMA_TEXT_LIGHT}; }}
        """

    def _build_gemini_section(self):
        """Create the outer Gemini card container and render initial state."""
        self._gemini_card = self._card()
        self._gemini_inner = QVBoxLayout(self._gemini_card)
        self._gemini_inner.setContentsMargins(20, 18, 20, 18)
        self._gemini_inner.setSpacing(12)
        self.layout_inner.addWidget(self._gemini_card)
        self.layout_inner.addSpacing(24)
        self._render_gemini_state(self._gemini_state)

    def _render_gemini_state(self, state: str):
        """Clear and redraw the Gemini card for the given state."""
        self._gemini_state = state
        self._clear_gemini_inner()

        if state == "NOT_CONFIGURED":
            self._render_not_configured()
        elif state == "INSTALLING":
            self._render_installing()
        elif state == "AUTHENTICATING":
            self._render_authenticating()
        elif state == "READY":
            self._render_ready(getattr(self, "_gemini_ready_version", ""))

    def _clear_gemini_inner(self):
        """Remove all widgets AND sub-layouts from the Gemini card interior."""
        while self._gemini_inner.count():
            item = self._gemini_inner.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                # Recursively clear and discard nested layouts
                self._clear_layout_recursive(item.layout())

    @staticmethod
    def _clear_layout_recursive(layout):
        """Recursively delete all widgets inside a layout and the layout itself."""
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
            elif child.layout():
                SettingsPage._clear_layout_recursive(child.layout())
        layout.deleteLater()

    # ─── State: NOT_CONFIGURED ────────────────────────────────────────────────

    def _render_not_configured(self):
        from src.data.gemini_setup import find_node, get_node_version, find_npm, find_gemini_cli, get_node_download_url

        lbl_mid = f"font-size: 12px; font-weight: 600; color: {ALMA_TEXT_MID}; border: none;"
        lbl_light = f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none;"
        lbl_ok = f"font-size: 12px; color: {ALMA_SUCCESS}; border: none;"
        lbl_err = f"font-size: 12px; color: {ALMA_ERROR}; border: none;"

        # Header
        title = QLabel("Set up Gemini AI")
        title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        self._gemini_inner.addWidget(title)

        desc = QLabel("Enable AI Reports and Hypothesis Testing by connecting Gemini.")
        desc.setStyleSheet(lbl_light)
        desc.setWordWrap(True)
        self._gemini_inner.addWidget(desc)

        # ── Step 1: Node.js ──
        node_path = find_node()
        node_version = get_node_version(node_path) if node_path else None
        self._detected_npm = find_npm(node_path) if node_path else None

        step1_row = QHBoxLayout()
        step1_lbl = QLabel("Step 1 — Node.js")
        step1_lbl.setStyleSheet(lbl_mid)
        step1_row.addWidget(step1_lbl)

        if node_version:
            step1_status = QLabel(f"✓  {node_version}")
            step1_status.setStyleSheet(lbl_ok)
        else:
            step1_status = QLabel("✗  Not found")
            step1_status.setStyleSheet(lbl_err)
        step1_row.addWidget(step1_status)
        step1_row.addStretch()

        dl_btn = QPushButton("Download Node.js ↗")
        dl_btn.setStyleSheet(self._ghost_btn_style())
        dl_btn.setCursor(Qt.PointingHandCursor)
        dl_btn.clicked.connect(lambda: webbrowser.open(get_node_download_url()))
        step1_row.addWidget(dl_btn)
        self._gemini_inner.addLayout(step1_row)

        # ── Step 2: Gemini CLI ──
        existing_cli = find_gemini_cli()

        step2_row = QHBoxLayout()
        step2_lbl = QLabel("Step 2 — Gemini CLI")
        step2_lbl.setStyleSheet(lbl_mid)
        step2_row.addWidget(step2_lbl)

        if existing_cli:
            step2_status = QLabel("✓  Installed")
            step2_status.setStyleSheet(lbl_ok)
            self._detected_cli = existing_cli
        else:
            step2_status = QLabel("✗  Not installed")
            step2_status.setStyleSheet(lbl_err)
            self._detected_cli = ""
        step2_row.addWidget(step2_status)
        step2_row.addStretch()

        self._install_btn = QPushButton("Install via npm")
        self._install_btn.setStyleSheet(self._primary_btn_style())
        self._install_btn.setCursor(Qt.PointingHandCursor)
        self._install_btn.setEnabled(bool(node_path) and not existing_cli)
        self._install_btn.clicked.connect(self._on_install_clicked)
        step2_row.addWidget(self._install_btn)
        self._gemini_inner.addLayout(step2_row)

        # ── Step 3: Sign in ──
        step3_row = QHBoxLayout()
        step3_lbl = QLabel("Step 3 — Sign in to Google")
        step3_lbl.setStyleSheet(lbl_mid)
        step3_row.addWidget(step3_lbl)
        step3_row.addStretch()

        self._signin_btn = QPushButton("Sign In with Google")
        self._signin_btn.setStyleSheet(self._primary_btn_style())
        self._signin_btn.setCursor(Qt.PointingHandCursor)
        self._signin_btn.setEnabled(bool(existing_cli))
        self._signin_btn.clicked.connect(self._on_signin_clicked)
        step3_row.addWidget(self._signin_btn)
        self._gemini_inner.addLayout(step3_row)

        # ── Divider ──
        div = QFrame()
        div.setFrameShape(QFrame.HLine)
        div.setStyleSheet(f"background: {ALMA_BORDER_LIGHT}; max-height: 1px; border: none;")
        self._gemini_inner.addWidget(div)

        # ── API key alternative ──
        if not self._gemini_api_key_mode:
            alt_row = QHBoxLayout()
            alt_lbl = QLabel("or")
            alt_lbl.setStyleSheet(lbl_light)
            alt_row.addWidget(alt_lbl)
            alt_btn = QPushButton("Use API key instead")
            alt_btn.setFlat(True)
            alt_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {ALMA_GREEN_DARK};
                    border: none; font-size: 12px; font-weight: 600;
                    padding: 0; text-decoration: underline;
                }}
                QPushButton:hover {{ color: {ALMA_GREEN_LIGHT}; }}
            """)
            alt_btn.setCursor(Qt.PointingHandCursor)
            alt_btn.clicked.connect(self._show_api_key_input)
            alt_row.addWidget(alt_btn)
            alt_row.addStretch()
            self._gemini_inner.addLayout(alt_row)
        else:
            self._render_api_key_input()

    def _show_api_key_input(self):
        self._gemini_api_key_mode = True
        self._render_gemini_state("NOT_CONFIGURED")

    def _render_api_key_input(self):
        """Show API key input within NOT_CONFIGURED state."""
        from src.data.pat_store import load_setting
        saved_key = load_setting("gemini_api_key", "")

        key_lbl = QLabel("Gemini API Key")
        key_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {ALMA_TEXT_MID}; border: none;")
        self._gemini_inner.addWidget(key_lbl)

        key_row = QHBoxLayout()
        self._api_key_input = QLineEdit()
        self._api_key_input.setEchoMode(QLineEdit.Password)
        self._api_key_input.setPlaceholderText("Paste API key from aistudio.google.com/apikey")
        if saved_key:
            self._api_key_input.setPlaceholderText("API key saved — paste new one to update")
        self._api_key_input.setStyleSheet(f"""
            QLineEdit {{
                color: {ALMA_TEXT_DARK}; background: {ALMA_WHITE};
                border: 1px solid {ALMA_BORDER}; border-radius: 8px;
                padding: 8px 12px; font-size: 13px;
            }}
        """)
        key_row.addWidget(self._api_key_input, 1)

        save_key_btn = QPushButton("Save Key")
        save_key_btn.setStyleSheet(self._primary_btn_style())
        save_key_btn.setCursor(Qt.PointingHandCursor)
        save_key_btn.clicked.connect(self._on_save_api_key)
        key_row.addWidget(save_key_btn)
        self._gemini_inner.addLayout(key_row)

        hint = QLabel("Get a free API key at aistudio.google.com/apikey")
        hint.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none;")
        self._gemini_inner.addWidget(hint)

        if saved_key:
            cancel_btn = QPushButton("← Back")
            cancel_btn.setFlat(True)
            cancel_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {ALMA_TEXT_MID};
                    border: none; font-size: 11px; padding: 0;
                }}
                QPushButton:hover {{ color: {ALMA_TEXT_DARK}; }}
            """)
            cancel_btn.setCursor(Qt.PointingHandCursor)
            cancel_btn.clicked.connect(self._cancel_api_key_mode)
            self._gemini_inner.addWidget(cancel_btn)

    def _cancel_api_key_mode(self):
        self._gemini_api_key_mode = False
        self._render_gemini_state("NOT_CONFIGURED")

    def _on_save_api_key(self):
        key = self._api_key_input.text().strip()
        if not key:
            return
        from src.data.pat_store import save_setting
        save_setting("gemini_api_key", key)
        self._gemini_api_key_mode = False
        # With API key, we treat the path as "api_key_mode" sentinel
        self._gemini_cli_path = ""
        self._on_gemini_ready("", "API key configured")

    # ─── State: INSTALLING ────────────────────────────────────────────────────

    def _render_installing(self):
        title = QLabel("Installing Gemini CLI...")
        title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        self._gemini_inner.addWidget(title)

        hint = QLabel("This usually takes 15–60 seconds depending on your connection.")
        hint.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none;")
        self._gemini_inner.addWidget(hint)

        self._install_log = QTextEdit()
        self._install_log.setReadOnly(True)
        self._install_log.setMinimumHeight(140)
        self._install_log.setMaximumHeight(200)
        self._install_log.setStyleSheet(f"""
            QTextEdit {{
                background: #1e1e1e; color: #d4d4d4;
                border: 1px solid {ALMA_BORDER}; border-radius: 6px;
                padding: 8px; font-family: Consolas, monospace; font-size: 11px;
            }}
        """)
        self._gemini_inner.addWidget(self._install_log)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(self._ghost_btn_style())
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.clicked.connect(self._on_install_cancel)
        self._gemini_inner.addWidget(cancel_btn)

    def _on_install_cancel(self):
        if self._gemini_setup_worker and self._gemini_setup_worker.isRunning():
            self._gemini_setup_worker.terminate()
        self._render_gemini_state("NOT_CONFIGURED")

    # ─── State: AUTHENTICATING ────────────────────────────────────────────────

    def _render_authenticating(self):
        title = QLabel("Sign in to Google")
        title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        self._gemini_inner.addWidget(title)

        installed_lbl = QLabel("✓  Gemini CLI is installed")
        installed_lbl.setStyleSheet(f"font-size: 12px; color: {ALMA_SUCCESS}; border: none;")
        self._gemini_inner.addWidget(installed_lbl)

        msg = QLabel(
            "A terminal window has opened — complete Google sign-in there,\n"
            "then click Verify Sign-in below."
        )
        msg.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID}; border: none;")
        msg.setWordWrap(True)
        self._gemini_inner.addWidget(msg)

        tip = QLabel(
            "Tip: If no terminal opened, run  gemini  manually in your terminal "
            "and follow the sign-in prompts."
        )
        tip.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none;")
        tip.setWordWrap(True)
        self._gemini_inner.addWidget(tip)

        self._verify_status_lbl = QLabel("")
        self._verify_status_lbl.setStyleSheet(f"font-size: 12px; color: {ALMA_ERROR}; border: none;")
        self._verify_status_lbl.setVisible(False)
        self._gemini_inner.addWidget(self._verify_status_lbl)

        btn_row = QHBoxLayout()
        verify_btn = QPushButton("✓  Verify Sign-in")
        verify_btn.setStyleSheet(self._primary_btn_style())
        verify_btn.setCursor(Qt.PointingHandCursor)
        verify_btn.clicked.connect(self._on_verify_clicked)
        btn_row.addWidget(verify_btn)

        start_over_btn = QPushButton("Start Over")
        start_over_btn.setStyleSheet(self._ghost_btn_style())
        start_over_btn.setCursor(Qt.PointingHandCursor)
        start_over_btn.clicked.connect(lambda: self._render_gemini_state("NOT_CONFIGURED"))
        btn_row.addWidget(start_over_btn)
        btn_row.addStretch()
        self._gemini_inner.addLayout(btn_row)

    # ─── State: READY ─────────────────────────────────────────────────────────

    def _render_ready(self, version: str = ""):
        lbl_mid = f"font-size: 12px; font-weight: 600; color: {ALMA_TEXT_MID}; border: none;"
        lbl_light = f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none;"

        # Status row
        status_row = QHBoxLayout()
        from src.data.pat_store import load_setting
        api_key_mode = bool(load_setting("gemini_api_key", ""))

        if api_key_mode and not self._gemini_cli_path:
            status_text = "🟢  Gemini ready — API key configured"
        else:
            v = version or ""
            status_text = f"🟢  Gemini ready  {('— ' + v) if v else ''}"

        status_lbl = QLabel(status_text)
        status_lbl.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {ALMA_SUCCESS}; border: none;")
        status_row.addWidget(status_lbl, 1)

        reconfig_btn = QPushButton("Reconfigure")
        reconfig_btn.setStyleSheet(self._ghost_btn_style())
        reconfig_btn.setCursor(Qt.PointingHandCursor)
        reconfig_btn.clicked.connect(self._on_reconfigure)
        status_row.addWidget(reconfig_btn)
        self._gemini_inner.addLayout(status_row)

        # Note: Model selector and PII toggle live in the unified Active Model
        # card at the top of the AI Provider tab (not duplicated here).
        # Gemini model string is derived from the Active Model dropdown.

        # Restore saved PII state to the unified toggle
        self._restore_model_pii()

    def _restore_model_pii(self):
        """Re-apply saved PII setting to the unified toggle."""
        try:
            gemini_cfg = get_section("gemini", {})
            pii = gemini_cfg.get("pii_redaction", True)
            if hasattr(self, "pii_toggle"):
                self.pii_toggle._checked = pii
                self.pii_toggle.update()
        except Exception:
            pass

    # ─── Actions ─────────────────────────────────────────────────────────────

    def _on_install_clicked(self):
        npm = getattr(self, "_detected_npm", None)
        if not npm:
            from src.data.gemini_setup import find_npm
            npm = find_npm()
        if not npm:
            QMessageBox.warning(self, "npm not found",
                "Could not find npm. Please install Node.js first, then restart Alma.")
            return
        self._render_gemini_state("INSTALLING")
        self._gemini_setup_worker = GeminiSetupWorker(mode="install", npm_path=npm)
        self._gemini_setup_worker.log_line.connect(self._on_install_log)
        self._gemini_setup_worker.finished.connect(self._on_worker_finished)
        self._gemini_setup_worker.error.connect(self._on_worker_error)
        self._gemini_setup_worker.start()

    def _on_install_log(self, line: str):
        if hasattr(self, "_install_log"):
            self._install_log.append(line)

    def _on_signin_clicked(self):
        from src.data.gemini_setup import launch_gemini_auth
        cli = getattr(self, "_detected_cli", "") or ""
        if not cli:
            from src.data.gemini_setup import find_gemini_cli
            cli = find_gemini_cli() or ""
        self._gemini_cli_path = cli
        if cli:
            launch_gemini_auth(cli)
        self._render_gemini_state("AUTHENTICATING")

    def _on_verify_clicked(self):
        if self._gemini_setup_worker and self._gemini_setup_worker.isRunning():
            return
        if hasattr(self, "_verify_status_lbl"):
            self._verify_status_lbl.setText("Checking...")
            self._verify_status_lbl.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID}; border: none;")
            self._verify_status_lbl.setVisible(True)

        self._gemini_setup_worker = GeminiSetupWorker(
            mode="verify_auth",
            gemini_path=self._gemini_cli_path,
        )
        self._gemini_setup_worker.finished.connect(self._on_worker_finished)
        self._gemini_setup_worker.error.connect(self._on_verify_error)
        self._gemini_setup_worker.start()

    def _on_verify_error(self, msg: str):
        if hasattr(self, "_verify_status_lbl"):
            self._verify_status_lbl.setText(f"✗  {msg}")
            self._verify_status_lbl.setStyleSheet(f"font-size: 12px; color: {ALMA_ERROR}; border: none;")
            self._verify_status_lbl.setVisible(True)

    def _on_reconfigure(self):
        # Clear saved API key if in api-key mode
        self._gemini_api_key_mode = False
        self._gemini_cli_path = ""
        self._render_gemini_state("NOT_CONFIGURED")

    def _on_worker_finished(self, new_state: str, detail: str):
        if new_state == "READY":
            # detail = "cli_path|version_string"
            parts = detail.split("|", 1)
            cli_path = parts[0] if parts else ""
            version = parts[1] if len(parts) > 1 else ""
            self._on_gemini_ready(cli_path, version)
        elif new_state == "AUTHENTICATING":
            self._gemini_cli_path = detail
            if detail:
                from src.data.gemini_setup import launch_gemini_auth
                launch_gemini_auth(detail)
            self._render_gemini_state("AUTHENTICATING")
        else:
            self._render_gemini_state(new_state)

    def _on_worker_error(self, msg: str):
        # Return to NOT_CONFIGURED and show the error
        self._render_gemini_state("NOT_CONFIGURED")
        QMessageBox.critical(self, "Setup Error", msg)

    def _on_gemini_ready(self, cli_path: str, version: str):
        """Transition to READY state and persist settings."""
        self._gemini_cli_path = cli_path
        self._gemini_ready_version = version  # stash for _render_ready()
        self._render_gemini_state("READY")    # clears old widgets first
        self._persist_gemini_settings()
        self.settings_changed.emit({"gemini_updated": True})

    # ─── Persistence ─────────────────────────────────────────────────────────

    def _persist_gemini_settings(self, *args):
        if self._loading:
            return
        try:
            cfg = load_settings()
            cfg.setdefault("gemini", {})
            cfg["gemini"]["cli_path"] = self._gemini_cli_path
            # Model string comes from the unified Active Model dropdown
            if hasattr(self, "_active_model_combo") and self._active_model_combo.currentData():
                cfg["gemini"]["model"] = self._active_model_combo.currentData()
            # PII toggle is the unified one in the Active Model card
            if hasattr(self, "pii_toggle"):
                cfg["gemini"]["pii_redaction"] = self.pii_toggle.checked
            save_settings(cfg)
            self.settings_changed.emit({"gemini_updated": True})
        except Exception:
            pass

    def _load_gemini_settings(self):
        """Load Gemini settings and determine initial state."""
        from src.data.gemini_setup import find_gemini_cli, verify_gemini_auth
        from src.data.pat_store import load_setting

        # Check API key first
        api_key = load_setting("gemini_api_key", "")
        if api_key:
            self._gemini_cli_path = ""
            self._render_gemini_state("READY")
            self._restore_model_pii()
            return

        # Try saved CLI path from settings
        g = get_section("gemini", {})
        saved_cli = g.get("cli_path", "")
        saved_model = g.get("model", "gemini-2.5-flash")
        saved_pii = g.get("pii_redaction", True)

        # Auto-detect if saved path is empty
        cli_path = saved_cli or find_gemini_cli() or ""
        self._gemini_cli_path = cli_path

        if cli_path:
            ok, version = verify_gemini_auth(cli_path)
            if ok:
                self._gemini_ready_version = version
                self._render_gemini_state("READY")  # clears + renders cleanly
                return
            else:
                # CLI present but not yet authed
                self._render_gemini_state("AUTHENTICATING")
                return

        # Nothing found — show setup flow
        self._render_gemini_state("NOT_CONFIGURED")

    # ═══════════════════════════════════════════
    #  GOOGLE DRIVE SETTINGS
    # ═══════════════════════════════════════════

    def _browse_gdrive_credentials(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Service Account Credentials", "", "JSON Files (*.json)"
        )
        if path:
            self._gdrive_cred_input.setText(path)

    def _save_gdrive_settings(self):
        try:
            cfg = load_settings()
            cfg.setdefault("export", {}).setdefault("google_drive", {})
            cfg["export"]["google_drive"]["credentials_path"] = self._gdrive_cred_input.text().strip()
            cfg["export"]["google_drive"]["folder_id"] = self._gdrive_folder_input.text().strip()
            cfg["export"]["google_drive"]["enabled"] = bool(
                self._gdrive_cred_input.text().strip() and self._gdrive_folder_input.text().strip()
            )
            save_settings(cfg)
            self._gdrive_status.setText("Saved")
            self._gdrive_status.setStyleSheet(f"font-size: 11px; color: {ALMA_SUCCESS};")
        except Exception as e:
            self._gdrive_status.setText(f"Error: {e}")
            self._gdrive_status.setStyleSheet(f"font-size: 11px; color: {ALMA_ERROR};")

    def _test_gdrive_connection(self):
        cred_path = self._gdrive_cred_input.text().strip()
        folder_id = self._gdrive_folder_input.text().strip()
        if not cred_path or not folder_id:
            self._gdrive_status.setText("Enter credentials path and folder ID first")
            self._gdrive_status.setStyleSheet(f"font-size: 11px; color: {ALMA_WARNING};")
            return
        try:
            from src.export.gdrive_export import GoogleDriveExporter
            exporter = GoogleDriveExporter(cred_path, folder_id)
            ok, msg = exporter.test_connection()
            if ok:
                self._gdrive_status.setText(f"✓ {msg}")
                self._gdrive_status.setStyleSheet(f"font-size: 11px; color: {ALMA_SUCCESS};")
            else:
                self._gdrive_status.setText(f"✗ {msg}")
                self._gdrive_status.setStyleSheet(f"font-size: 11px; color: {ALMA_ERROR};")
        except ImportError:
            self._gdrive_status.setText("Missing: pip install google-api-python-client google-auth")
            self._gdrive_status.setStyleSheet(f"font-size: 11px; color: {ALMA_ERROR};")
        except Exception as e:
            self._gdrive_status.setText(f"✗ {e}")
            self._gdrive_status.setStyleSheet(f"font-size: 11px; color: {ALMA_ERROR};")

    def _load_gdrive_settings(self):
        """Restore Google Drive settings from settings manager."""
        try:
            export = get_section("export", {})
            gdrive = export.get("google_drive", {})
            self._gdrive_cred_input.setText(gdrive.get("credentials_path", ""))
            self._gdrive_folder_input.setText(gdrive.get("folder_id", ""))
        except Exception:
            pass

    # ═══════════════════════════════════════════
    #  INTERVENTION MANAGER
    # ═══════════════════════════════════════════

    def set_db_manager(self, db):
        """Set db reference for intervention management and Scanning Costs tab."""
        self._db = db
        self._refresh_interventions()

        # Populate Scanning Costs tab with CostDashboard
        try:
            from src.ui.widgets.cost_dashboard import CostDashboard
            self._cost_dashboard = CostDashboard(db)
            idx = self._tab_widget.indexOf(self._cost_placeholder)
            if idx >= 0:
                self._tab_widget.removeTab(idx)
                self._tab_widget.insertTab(idx, self._cost_dashboard, "Scanning Costs")
        except Exception:
            pass  # CostDashboard may not be available

    def _add_intervention(self):
        from src.ui.dialogs.intervention_dialog import InterventionDialog
        dlg = InterventionDialog(parent=self)
        if dlg.exec():
            data = dlg.get_intervention_data()
            if hasattr(self, "_db") and self._db:
                self._db.save_intervention(data)
                self._refresh_interventions()
                self.settings_changed.emit({"interventions_updated": True})

    def _edit_intervention(self, iv_id):
        if not hasattr(self, "_db") or not self._db:
            return
        from src.ui.dialogs.intervention_dialog import InterventionDialog
        existing = None
        try:
            interventions = self._db.get_interventions()
            for iv in interventions:
                if iv.get("intervention_id") == iv_id:
                    existing = iv
                    break
        except Exception:
            return
        if not existing:
            return
        dlg = InterventionDialog(intervention_data=existing, parent=self)
        if dlg.exec():
            data = dlg.get_intervention_data()
            self._db.update_intervention(iv_id, data)
            self._refresh_interventions()
            self.settings_changed.emit({"interventions_updated": True})

    def _delete_intervention(self, iv_id):
        reply = QMessageBox.question(
            self, "Delete Intervention",
            "Are you sure you want to delete this intervention?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes and hasattr(self, "_db") and self._db:
            self._db.delete_intervention(iv_id)
            self._refresh_interventions()
            self.settings_changed.emit({"interventions_updated": True})

    def _refresh_interventions(self):
        """Reload interventions list from DB."""
        # Clear container
        while self._iv_container.count() > 0:
            item = self._iv_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not hasattr(self, "_db") or not self._db:
            return

        try:
            interventions = self._db.get_interventions()
        except Exception:
            interventions = []

        if not interventions:
            empty = QLabel("No interventions defined.")
            empty.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; padding: 8px;")
            empty.setAlignment(Qt.AlignCenter)
            self._iv_container.addWidget(empty)
            return

        from src.ui.dialogs.intervention_dialog import CATEGORY_COLORS

        for iv in interventions:
            row = QFrame()
            cat = iv.get("category", "other")
            border_color = CATEGORY_COLORS.get(cat, "#6B7280")
            row.setStyleSheet(f"""
                QFrame {{
                    background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT};
                    border-left: 4px solid {border_color};
                    border-radius: 6px; padding: 6px;
                }}
            """)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(10, 6, 6, 6)
            row_layout.setSpacing(8)

            info_col = QVBoxLayout()
            name_lbl = QLabel(f"{iv.get('name', '')}  —  {iv.get('event_date', '')}")
            name_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {ALMA_TEXT_DARK}; border: none;")
            info_col.addWidget(name_lbl)

            cat_lbl = QLabel(f"{cat.replace('_', ' ').title()}: {iv.get('description', '')[:80]}")
            cat_lbl.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; border: none;")
            cat_lbl.setWordWrap(True)
            info_col.addWidget(cat_lbl)
            row_layout.addLayout(info_col, 1)

            edit_btn = QPushButton("Edit")
            edit_btn.setCursor(Qt.PointingHandCursor)
            edit_btn.setFixedWidth(50)
            edit_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {ALMA_INFO};
                    border: 1px solid {ALMA_INFO}; border-radius: 4px;
                    padding: 2px 8px; font-size: 10px;
                }}
            """)
            iv_id = iv.get("intervention_id")
            edit_btn.clicked.connect(lambda checked, iid=iv_id: self._edit_intervention(iid))
            row_layout.addWidget(edit_btn)

            del_btn = QPushButton("✕")
            del_btn.setCursor(Qt.PointingHandCursor)
            del_btn.setFixedSize(24, 24)
            del_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {ALMA_TEXT_LIGHT};
                    font-size: 14px; border-radius: 4px; border: none;
                }}
                QPushButton:hover {{ color: {ALMA_ERROR}; }}
            """)
            del_btn.clicked.connect(lambda checked, iid=iv_id: self._delete_intervention(iid))
            row_layout.addWidget(del_btn)

            self._iv_container.addWidget(row)

    # ═══════════════════════════════════════════
    #  AI ENHANCEMENTS PERSISTENCE
    # ═══════════════════════════════════════════

    def _persist_ai_settings(self, *args):
        if self._loading:
            return
        try:
            cfg = load_settings()
            cfg.setdefault("ai_enhancements", {})
            cfg["ai_enhancements"]["smoothing"] = self.ai_smoothing_toggle.checked
            cfg["ai_enhancements"]["keywords"] = self.ai_keywords_toggle.checked
            save_settings(cfg)
            self.settings_changed.emit({"ai_enhancements_updated": True})
        except Exception:
            pass

    def _load_ai_settings(self):
        """Restore AI enhancement toggles from settings manager."""
        try:
            ai = get_section("ai_enhancements", {})
            self.ai_smoothing_toggle._checked = ai.get("smoothing", False)
            self.ai_smoothing_toggle.update()
            self.ai_keywords_toggle._checked = ai.get("keywords", False)
            self.ai_keywords_toggle.update()
            # Restore display preferences
            display = get_section("display", {})
            self.layman_mode_toggle._checked = display.get("layman_mode", False)
            self.layman_mode_toggle.update()
        except Exception:
            pass

    # ═══════════════════════════════════════════
    #  DISPLAY PREFERENCES PERSISTENCE
    # ═══════════════════════════════════════════

    def _persist_display_settings(self, *args):
        if self._loading:
            return
        try:
            cfg = load_settings()
            cfg.setdefault("display", {})
            cfg["display"]["layman_mode"] = self.layman_mode_toggle.checked
            save_settings(cfg)
            self.settings_changed.emit({"layman_mode_updated": True})
        except Exception:
            pass

    def is_layman_mode_enabled(self):
        return self.layman_mode_toggle.checked

    # ═══════════════════════════════════════════
    #  SECTION 9: AUTO-ANALYSIS ON IMPORT
    # ═══════════════════════════════════════════

    def _build_auto_analysis_section(self):
        self.layout_inner.addWidget(self._section_label("AUTO-ANALYSIS ON IMPORT"))
        self.layout_inner.addSpacing(8)

        card = self._card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(10)

        desc = QLabel(
            "Control which analysis sections automatically run after data import. "
            "Disable individual sections to speed up import or focus on specific analyses."
        )
        desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        desc.setWordWrap(True)
        card_layout.addWidget(desc)

        # Store all auto-analysis toggles: { "page.key": ToggleSwitch }
        self._aa_toggles = {}

        first_group = True
        for page_key, sections in SECTION_KEYS.items():
            if not first_group:
                card_layout.addWidget(self._divider_line())
            first_group = False

            # Master toggle for this page
            master = ToggleSwitch(checked=True)
            master_key = f"aa_master_{page_key}"
            self._aa_toggles[master_key] = master
            master_row = self._build_toggle_row(
                PAGE_TITLES[page_key],
                f"Run all {PAGE_TITLES[page_key]} analyses on import",
                master,
            )
            card_layout.addLayout(master_row)

            # Sub-toggles for each section
            sub_toggles = []
            for title, yaml_key in sections.items():
                sub = ToggleSwitch(checked=True)
                full_key = f"{page_key}.{yaml_key}"
                self._aa_toggles[full_key] = sub
                sub_row = self._build_toggle_row(title, "", sub, indent=28)
                card_layout.addLayout(sub_row)
                sub_toggles.append(sub)

            # Wire master → disable/enable sub-toggles
            def _make_master_handler(subs, m):
                def handler(on):
                    for s in subs:
                        s.setEnabled(on)
                        if not on:
                            s._checked = False
                            s.update()
                    self._persist_behavior_settings()
                return handler

            master.toggled.connect(_make_master_handler(sub_toggles, master))

            # Wire each sub-toggle to persist
            for sub in sub_toggles:
                sub.toggled.connect(self._persist_behavior_settings)

        self.layout_inner.addWidget(card)
        self.layout_inner.addSpacing(24)

    # ═══════════════════════════════════════════
    #  SECTION 10: CALENDAR SYNC
    # ═══════════════════════════════════════════

    def _build_calendar_sync_section(self):
        self.layout_inner.addWidget(self._section_label("CALENDAR SYNC"))
        self.layout_inner.addSpacing(8)

        card = self._card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(10)

        self._cal_sync_analysis = ToggleSwitch(checked=False)
        card_layout.addLayout(self._build_toggle_row(
            "Sync Analysis Page Dates",
            "When a date is changed on any analysis page (TRC Analytics, Incidents, "
            "Trending Topics), automatically update all other analysis pages to match "
            "and re-run their analyses.",
            self._cal_sync_analysis,
        ))

        card_layout.addWidget(self._divider_line())

        self._cal_sync_reports = ToggleSwitch(checked=False)
        self._cal_sync_reports.setEnabled(False)  # Disabled until analysis sync is on
        card_layout.addLayout(self._build_toggle_row(
            "Include AI Reports",
            "Also sync date changes to the AI Reports page. "
            "Requires Analysis Page Sync to be enabled.",
            self._cal_sync_reports,
            indent=28,
        ))

        # Wire: analysis sync master enables reports sub-toggle
        def _on_analysis_sync(on):
            self._cal_sync_reports.setEnabled(on)
            if not on:
                self._cal_sync_reports._checked = False
                self._cal_sync_reports.update()
            self._persist_behavior_settings()

        self._cal_sync_analysis.toggled.connect(_on_analysis_sync)
        self._cal_sync_reports.toggled.connect(self._persist_behavior_settings)

        self.layout_inner.addWidget(card)
        self.layout_inner.addSpacing(24)

    # ═══════════════════════════════════════════
    #  SECTION 11: SOURCE SYNC
    # ═══════════════════════════════════════════

    def _build_source_sync_section(self):
        self.layout_inner.addWidget(self._section_label("SOURCE SYNC"))
        self.layout_inner.addSpacing(8)

        card = self._card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(10)

        self._source_sync_toggle = ToggleSwitch(checked=False)
        card_layout.addLayout(self._build_toggle_row(
            "Sync Data Sources",
            "When enabled, changing the data source on one analysis page "
            "updates all other pages to use the same source. "
            "Currently only Conversations is available.",
            self._source_sync_toggle,
        ))

        self._source_sync_toggle.toggled.connect(self._persist_behavior_settings)

        self.layout_inner.addWidget(card)
        self.layout_inner.addSpacing(24)

    # ═══════════════════════════════════════════
    #  SECTION 12: SECTION DEFAULTS
    # ═══════════════════════════════════════════

    def _build_section_defaults_section(self):
        self.layout_inner.addWidget(self._section_label("SECTION DEFAULTS"))
        self.layout_inner.addSpacing(8)

        card = self._card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(10)

        desc = QLabel(
            "Control whether analysis chart sections start expanded or collapsed. "
            "Choose a preset or customize per-section."
        )
        desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        desc.setWordWrap(True)
        card_layout.addWidget(desc)

        # Preset combo
        preset_row = QHBoxLayout()
        preset_lbl = QLabel("Preset")
        preset_lbl.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {ALMA_TEXT_DARK};")
        preset_row.addWidget(preset_lbl)

        self._sd_preset_combo = QComboBox()
        self._sd_preset_combo.addItem("All Open", "all_open")
        self._sd_preset_combo.addItem("All Collapsed", "all_collapsed")
        self._sd_preset_combo.addItem("Custom", "custom")
        self._sd_preset_combo.setStyleSheet(f"""
            QComboBox {{
                color: {ALMA_TEXT_DARK}; background: {ALMA_WHITE};
                border: 1px solid {ALMA_BORDER}; border-radius: 8px;
                padding: 6px 12px; font-size: 13px; min-width: 160px;
            }}
        """)
        preset_row.addWidget(self._sd_preset_combo)
        preset_row.addStretch()
        card_layout.addLayout(preset_row)

        card_layout.addWidget(self._divider_line())

        # Per-section toggles (shown only in Custom mode)
        self._sd_custom_container = QWidget()
        custom_layout = QVBoxLayout(self._sd_custom_container)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        custom_layout.setSpacing(8)

        custom_hint = QLabel("ON = expanded on open   ·   OFF = collapsed on open")
        custom_hint.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT};")
        custom_hint.setAlignment(Qt.AlignCenter)
        custom_layout.addWidget(custom_hint)

        self._sd_toggles = {}
        first_page = True
        for page_key, sections in SECTION_KEYS.items():
            if not first_page:
                custom_layout.addWidget(self._divider_line())
            first_page = False

            page_lbl = QLabel(PAGE_TITLES[page_key])
            page_lbl.setStyleSheet(
                f"font-size: 12px; font-weight: 700; color: {ALMA_TEXT_MID}; "
                f"letter-spacing: 0.5px; padding-top: 4px;"
            )
            custom_layout.addWidget(page_lbl)

            for title, yaml_key in sections.items():
                toggle = ToggleSwitch(checked=True)
                full_key = f"{page_key}.{yaml_key}"
                self._sd_toggles[full_key] = toggle
                row = self._build_toggle_row(title, "", toggle, indent=16)
                custom_layout.addLayout(row)
                toggle.toggled.connect(self._persist_behavior_settings)

        card_layout.addWidget(self._sd_custom_container)
        self._sd_custom_container.setVisible(False)  # Hidden until "Custom" selected

        # Wire preset combo
        def _on_preset_changed(idx):
            preset = self._sd_preset_combo.currentData()
            self._sd_custom_container.setVisible(preset == "custom")
            self._persist_behavior_settings()

        self._sd_preset_combo.currentIndexChanged.connect(_on_preset_changed)

        self.layout_inner.addWidget(card)
        self.layout_inner.addSpacing(24)

    # ═══════════════════════════════════════════
    #  SECTION 13: MEMORY DIAGNOSTICS
    # ═══════════════════════════════════════════

    def _build_memory_debug_section(self):
        self.layout_inner.addWidget(self._section_label("MEMORY DIAGNOSTICS"))
        self.layout_inner.addSpacing(8)

        card = self._card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(10)

        # Title row
        title_row = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel("Memory Profiler")
        title.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {ALMA_TEXT_DARK};")
        desc = QLabel(
            "Take a memory snapshot to diagnose allocation bloat. "
            "Shows object counts, large containers, conversation dict copies, "
            "and QThread worker lifecycle."
        )
        desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        desc.setWordWrap(True)
        title_col.addWidget(title)
        title_col.addWidget(desc)
        title_row.addLayout(title_col, 1)

        self._mem_snapshot_btn = QPushButton("Take Snapshot")
        self._mem_snapshot_btn.setCursor(Qt.PointingHandCursor)
        self._mem_snapshot_btn.setFixedHeight(32)
        self._mem_snapshot_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_WHITE};
                border: none; border-radius: 8px;
                padding: 6px 18px; font-size: 12px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
        """)
        self._mem_snapshot_btn.clicked.connect(self._on_memory_snapshot)
        title_row.addWidget(self._mem_snapshot_btn)

        self._mem_gc_btn = QPushButton("Force GC")
        self._mem_gc_btn.setCursor(Qt.PointingHandCursor)
        self._mem_gc_btn.setFixedHeight(32)
        self._mem_gc_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_TEXT_MID};
                border: 1px solid {ALMA_BORDER}; border-radius: 8px;
                padding: 6px 14px; font-size: 12px; font-weight: 500;
            }}
            QPushButton:hover {{ background: {ALMA_HOVER_LIGHT}; }}
        """)
        self._mem_gc_btn.clicked.connect(self._on_force_gc)
        title_row.addWidget(self._mem_gc_btn)

        card_layout.addLayout(title_row)

        # Quick stats row (updated on snapshot)
        self._mem_quick_stats = QLabel("No snapshot taken yet")
        self._mem_quick_stats.setStyleSheet(
            f"font-size: 12px; color: {ALMA_TEXT_MID}; padding: 4px 0;"
        )
        card_layout.addWidget(self._mem_quick_stats)

        # Full report area (scrollable text)
        self._mem_report_area = QTextEdit()
        self._mem_report_area.setReadOnly(True)
        self._mem_report_area.setVisible(False)
        self._mem_report_area.setMinimumHeight(300)
        self._mem_report_area.setMaximumHeight(500)
        self._mem_report_area.setStyleSheet(f"""
            QTextEdit {{
                background: {ALMA_WHITE}; color: {ALMA_TEXT_DARK};
                border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 8px;
                padding: 12px; font-family: 'Consolas', 'Courier New', monospace;
                font-size: 11px; line-height: 1.4;
            }}
        """)
        card_layout.addWidget(self._mem_report_area)

        self.layout_inner.addWidget(card)
        self.layout_inner.addSpacing(24)

    def _on_memory_snapshot(self):
        """Take a memory snapshot and display the report."""
        self._mem_snapshot_btn.setText("Analyzing...")
        self._mem_snapshot_btn.setEnabled(False)
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

        try:
            from src.data.memory_profiler import MemoryProfiler
            if not MemoryProfiler.is_started():
                MemoryProfiler.start()
            report = MemoryProfiler.snapshot()
            text = MemoryProfiler.format_report(report)

            # Quick stats summary
            rss = report.get("process_rss_mb")
            rss_str = f"{rss:.0f} MB" if rss else "N/A"
            tracked = report.get("tracemalloc_mb", 0)
            gc_objs = report.get("gc_objects_total", 0)
            alma = report.get("alma_objects", {})
            conv_total = alma.get("conversation_dicts_total", 0)
            conv_thread = alma.get("conversation_dicts_with_full_thread", 0)
            thread_mb = alma.get("full_thread_total_mb", 0)
            result_dicts = alma.get("analysis_result_dicts", 0)
            workers = alma.get("qthread_workers", [])
            worker_count = len(workers)
            running = sum(1 for w in workers if w.get("status") == "running")

            self._mem_quick_stats.setText(
                f"RSS: {rss_str}  |  "
                f"Tracked: {tracked:.1f} MB  |  "
                f"GC Objects: {gc_objs:,}  |  "
                f"Conv Dicts: {conv_total} ({conv_thread} with full_thread = {thread_mb:.1f} MB)  |  "
                f"Result Dicts: {result_dicts}  |  "
                f"Workers: {worker_count} ({running} running)"
            )

            self._mem_report_area.setPlainText(text)
            self._mem_report_area.setVisible(True)

        except Exception as e:
            import traceback
            self._mem_quick_stats.setText(f"Error: {e}")
            self._mem_report_area.setPlainText(traceback.format_exc())
            self._mem_report_area.setVisible(True)
        finally:
            self._mem_snapshot_btn.setText("Take Snapshot")
            self._mem_snapshot_btn.setEnabled(True)

    def _on_force_gc(self):
        """Force garbage collection and show before/after stats."""
        import gc as _gc
        from src.data.memory_profiler import MemoryProfiler

        before_rss = MemoryProfiler._get_process_rss_mb()
        before_objs = len(_gc.get_objects())

        collected = _gc.collect()

        after_rss = MemoryProfiler._get_process_rss_mb()
        after_objs = len(_gc.get_objects())

        before_str = f"{before_rss:.0f}" if before_rss else "?"
        after_str = f"{after_rss:.0f}" if after_rss else "?"
        delta_str = ""
        if before_rss and after_rss:
            delta = after_rss - before_rss
            delta_str = f" ({delta:+.0f} MB)"

        self._mem_quick_stats.setText(
            f"GC collected {collected} objects  |  "
            f"RSS: {before_str} → {after_str} MB{delta_str}  |  "
            f"Objects: {before_objs:,} → {after_objs:,}"
        )

    # ═══════════════════════════════════════════
    #  SECTION 14: DATA MANAGEMENT (DANGER ZONE)
    # ═══════════════════════════════════════════

    def _build_data_management_section(self):
        """Full Database Reset — danger zone in Display tab."""
        self.layout_inner.addWidget(self._section_label("DATA MANAGEMENT"))
        self.layout_inner.addSpacing(8)

        card = self._card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(10)

        title_row = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel("Full Database Reset")
        title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_ERROR};")
        desc = QLabel(
            "Permanently delete ALL data — tickets, conversations, NLP results, "
            "reports, and enrichments. This cannot be undone."
        )
        desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        desc.setWordWrap(True)
        title_col.addWidget(title)
        title_col.addWidget(desc)
        title_row.addLayout(title_col, 1)

        reset_btn = QPushButton("Full Database Reset")
        reset_btn.setCursor(Qt.PointingHandCursor)
        reset_btn.setFixedHeight(32)
        reset_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_ERROR}; color: {ALMA_WHITE};
                border: none; border-radius: 8px;
                padding: 6px 18px; font-size: 12px; font-weight: 600;
            }}
            QPushButton:hover {{ background: #B71C1C; }}
        """)
        reset_btn.setToolTip("Permanently delete ALL data and start fresh. This cannot be undone.")
        reset_btn.clicked.connect(self._full_database_reset)
        title_row.addWidget(reset_btn)

        card_layout.addLayout(title_row)
        self.layout_inner.addWidget(card)
        self.layout_inner.addSpacing(24)

    def _full_database_reset(self):
        """Confirm and execute full database reset."""
        from PySide6.QtWidgets import QMessageBox, QInputDialog

        confirm, ok = QInputDialog.getText(
            self,
            "Full Database Reset",
            'This will permanently delete ALL data.\n\n'
            'Type "DELETE" to confirm:',
        )
        if not ok or confirm.strip() != "DELETE":
            return

        try:
            main_window = self.window()
            if hasattr(main_window, '_clear_all_data'):
                main_window._clear_all_data()
            if hasattr(main_window, 'ticket_count_label'):
                main_window.ticket_count_label.setText("0 tickets in database")
            QMessageBox.information(
                self, "Reset Complete",
                "All data has been deleted. The database is now empty."
            )
        except Exception as e:
            QMessageBox.critical(
                self, "Reset Failed",
                f"Database reset failed: {e}"
            )

    # ═══════════════════════════════════════════
    #  BEHAVIOR SETTINGS PERSISTENCE
    # ═══════════════════════════════════════════

    def _persist_behavior_settings(self, *args):
        """Write all behavior toggles via settings manager."""
        if self._loading:
            return
        try:
            cfg = load_settings()

            beh = cfg.setdefault("behavior", {})

            # ── Auto-Analysis ──
            aa = beh.setdefault("auto_analysis", {})
            for page_key, sections in SECTION_KEYS.items():
                page_aa = aa.setdefault(page_key, {})
                master_key = f"aa_master_{page_key}"
                master_toggle = self._aa_toggles.get(master_key)
                if master_toggle:
                    page_aa["enabled"] = master_toggle.checked
                for title, yaml_key in sections.items():
                    full_key = f"{page_key}.{yaml_key}"
                    sub_toggle = self._aa_toggles.get(full_key)
                    if sub_toggle:
                        page_aa[yaml_key] = sub_toggle.checked

            # ── Calendar Sync ──
            cs = beh.setdefault("calendar_sync", {})
            cs["analysis_pages"] = self._cal_sync_analysis.checked
            cs["ai_reports"] = self._cal_sync_reports.checked

            # ── Source Sync ──
            ss = beh.setdefault("source_sync", {})
            ss["enabled"] = self._source_sync_toggle.checked

            # ── Section Defaults ──
            sd = beh.setdefault("section_defaults", {})
            sd["preset"] = self._sd_preset_combo.currentData() or "all_open"
            custom = sd.setdefault("custom", {})
            for page_key, sections in SECTION_KEYS.items():
                page_custom = custom.setdefault(page_key, {})
                for title, yaml_key in sections.items():
                    full_key = f"{page_key}.{yaml_key}"
                    toggle = self._sd_toggles.get(full_key)
                    if toggle:
                        page_custom[yaml_key] = toggle.checked

            save_settings(cfg)

            self.settings_changed.emit({"behavior_updated": True})
        except Exception:
            pass

    def _load_behavior_settings(self):
        """Restore behavior toggle states from settings manager."""
        try:
            beh = get_section("behavior", {})

            # ── Auto-Analysis ──
            aa = beh.get("auto_analysis", {})
            for page_key, sections in SECTION_KEYS.items():
                page_aa = aa.get(page_key, {})
                master_key = f"aa_master_{page_key}"
                master_toggle = self._aa_toggles.get(master_key)
                if master_toggle:
                    enabled = page_aa.get("enabled", True)
                    master_toggle._checked = enabled
                    master_toggle.update()

                for title, yaml_key in sections.items():
                    full_key = f"{page_key}.{yaml_key}"
                    sub_toggle = self._aa_toggles.get(full_key)
                    if sub_toggle:
                        val = page_aa.get(yaml_key, True)
                        sub_toggle._checked = val
                        sub_toggle.update()
                        # Disable sub if master is off
                        if master_toggle and not master_toggle.checked:
                            sub_toggle.setEnabled(False)

            # ── Calendar Sync ──
            cs = beh.get("calendar_sync", {})
            self._cal_sync_analysis._checked = cs.get("analysis_pages", False)
            self._cal_sync_analysis.update()
            self._cal_sync_reports._checked = cs.get("ai_reports", False)
            self._cal_sync_reports.update()
            self._cal_sync_reports.setEnabled(self._cal_sync_analysis.checked)

            # ── Source Sync ──
            ss = beh.get("source_sync", {})
            self._source_sync_toggle._checked = ss.get("enabled", False)
            self._source_sync_toggle.update()

            # ── Section Defaults ──
            sd = beh.get("section_defaults", {})
            preset = sd.get("preset", "all_open")
            idx = self._sd_preset_combo.findData(preset)
            if idx >= 0:
                self._sd_preset_combo.setCurrentIndex(idx)
            self._sd_custom_container.setVisible(preset == "custom")

            custom = sd.get("custom", {})
            for page_key, sections in SECTION_KEYS.items():
                page_custom = custom.get(page_key, {})
                for title, yaml_key in sections.items():
                    full_key = f"{page_key}.{yaml_key}"
                    toggle = self._sd_toggles.get(full_key)
                    if toggle:
                        toggle._checked = page_custom.get(yaml_key, True)
                        toggle.update()
        except Exception:
            pass
