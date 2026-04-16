"""
Alma Insights — Smart Reporting Page (Phase 5.5C)

Full pipeline execution, persistent DB-backed scheduling, CLI reference,
enhanced run history with report viewer, and collapsible analyst/tech summaries.

Key changes from pre-5.5C:
  - Ephemeral QTimer scheduling → persistent DB-backed schedules via ScheduleManager
  - Plain text history → MarkdownViewer for report viewing
  - 5-column history → 7-column with Cost and clickable "View" report
  - New scheduling card: Add/Delete schedules, repeat type, day picker, timezone
  - Collapsible Analyst Reports + Technical Summary sections
"""

import json
import logging
import time
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QFrame, QScrollArea, QSpinBox, QDoubleSpinBox, QCheckBox,
    QTimeEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QMessageBox, QSizePolicy, QApplication,
    QLineEdit, QFileDialog,
)
from PySide6.QtCore import Qt, QTime, QThread, QTimer, Signal
from PySide6.QtGui import QFont, QColor

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_MID, ALMA_GREEN_LIGHT, ALMA_GREEN_SUBTLE,
    ALMA_WHITE, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_TEXT_ON_DARK, ALMA_BORDER, ALMA_BORDER_LIGHT,
    ALMA_SUCCESS, ALMA_WARNING, ALMA_ERROR, ALMA_INFO,
    ALMA_BG_ELEVATED, ALMA_BG_INSET,
    apply_card_shadow, apply_card_shadow_soft,
)

logger = logging.getLogger("alma.smart_reporting")

# Reusable styles
_FIELD_STYLE = f"""
    QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit, QTimeEdit {{
        background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER};
        border-radius: 8px; padding: 8px 12px; font-size: 13px;
        color: {ALMA_TEXT_DARK};
    }}
"""
_LBL_STYLE = f"font-size: 11px; font-weight: 600; color: {ALMA_TEXT_MID}; letter-spacing: 0.5px;"

_OUTLINE_BTN_STYLE = f"""
    QPushButton {{
        background: transparent; color: {ALMA_GREEN_DARK};
        border: 1px solid {ALMA_GREEN_DARK}; border-radius: 6px;
        padding: 4px 12px; font-size: 11px; font-weight: 600;
    }}
    QPushButton:hover {{ background: {ALMA_CREAM}; }}
"""

_CARD_STYLE = f"""
    QFrame {{
        background: {ALMA_BG_ELEVATED};
        border: none;
        border-radius: 12px;
    }}
"""


def _load_prompt_text(filename):
    path = Path(__file__).parent.parent.parent.parent / "config" / "prompts" / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


class SmartPipelineWorker(QThread):
    """Background thread for full smart reporting pipeline."""
    progress = Signal(str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, config):
        super().__init__()
        self.config = config

    def run(self):
        try:
            from src.data.smart_pipeline import run_pipeline
            result = run_pipeline(self.config, progress_cb=self._on_progress)
            self.finished.emit(result)
        except Exception as e:
            import traceback
            self.error.emit(traceback.format_exc())

    def _on_progress(self, msg):
        self.progress.emit(msg)


class SmartReportingPage(QWidget):
    """Smart Reporting page: automated adaptive reports on a schedule.

    Defines recurring report runs (daily, weekly, monthly) via
    `report_definitions`. Uses `src.data.smart_pipeline.SmartPipeline`
    to assemble and generate reports without human input.
    """

    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db = db_manager
        self._smart_worker = None
        self._schedule_manager = None
        self._gemini_available = self._check_gemini()

        self._build_ui()

    def _check_gemini(self):
        try:
            from src.gemini.client_factory import build_client_for_task
            client = build_client_for_task("report_generation")
            return client is not None and client.is_available()
        except Exception:
            try:
                from src.gemini.gemini_client import GeminiClient
                return GeminiClient().is_available()
            except Exception:
                return False

    # ═══════════════════════════════════════
    #  UI CONSTRUCTION
    # ═══════════════════════════════════════

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        # Header
        header = QLabel("Smart Reporting")
        header.setObjectName("PageHeader")
        layout.addWidget(header)

        sub = QLabel("Automated end-to-end analysis pipeline with persistent scheduling and export")
        sub.setObjectName("PageSubheader")
        layout.addWidget(sub)

        # ── Build 11.0: Report Pipeline Cards ──
        layout.addWidget(self._build_pipeline_cards_section())

        # ── Pipeline Config Card ──
        layout.addWidget(self._build_pipeline_config_card())

        # ── Auto-Import Card ──
        layout.addWidget(self._build_auto_import_card())

        # ── Two-column: Schedules + CLI ──
        two_col = QHBoxLayout()
        two_col.setSpacing(16)
        two_col.addWidget(self._build_schedule_card(), 1)
        two_col.addWidget(self._build_cli_card(), 1)
        layout.addLayout(two_col)

        # ── Report Viewer (collapsed by default) ──
        layout.addWidget(self._build_report_viewer_card())

        # ── Run History Card ──
        layout.addWidget(self._build_history_card())

        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

        self._refresh_smart_history()
        self._refresh_schedule_list()

    # ── Build 11.0: Report Pipeline Cards ──

    def _build_pipeline_cards_section(self):
        """Build the report pipeline cards row (from report_definitions table)."""
        section = QFrame()
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(8)

        lbl = QLabel("Report pipelines")
        lbl.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK};")
        section_layout.addWidget(lbl)

        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)

        # Load report definitions from DB
        pipelines = self._load_report_definitions()
        if not pipelines:
            # Show default cards even without DB data
            pipelines = [
                {"name": "VOC Root Cause", "description": "Full 3-bridge analysis with Pattern, Friction, Novelty specialists", "is_active": True},
                {"name": "Monthly Billing", "description": "Billing VOC analysis with charge discrepancy focus", "is_active": False},
                {"name": "Eng Bug Tracker", "description": "Platform bug analysis for engineering triage", "is_active": False},
            ]

        for p in pipelines[:4]:  # Max 4 cards
            card = self._build_single_pipeline_card(p)
            cards_row.addWidget(card)
        cards_row.addStretch()

        section_layout.addLayout(cards_row)
        return section

    def _load_report_definitions(self):
        """Load report definitions from the database."""
        try:
            rows = self.db.conn.execute(
                "SELECT definition_id, name, description, is_active, last_run_date, schedule "
                "FROM report_definitions ORDER BY name"
            ).fetchall()
            return [
                {
                    "definition_id": r[0],
                    "name": r[1],
                    "description": r[2],
                    "is_active": bool(r[3]),
                    "last_run_date": r[4],
                    "schedule": r[5],
                }
                for r in rows
            ]
        except Exception:
            return []

    def _build_single_pipeline_card(self, pipeline):
        """Build a single pipeline card widget."""
        card = QFrame()
        card.setMinimumWidth(220)
        card.setMaximumWidth(300)

        is_active = pipeline.get("is_active", False)
        border_color = ALMA_GREEN_MID if is_active else ALMA_BORDER_LIGHT

        card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED};
                border: 1px solid {border_color};
                border-radius: 10px;
            }}
        """)
        apply_card_shadow_soft(card)

        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(6)

        # Header row
        hdr = QHBoxLayout()
        name_lbl = QLabel(pipeline.get("name", ""))
        name_lbl.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {ALMA_TEXT_DARK};")
        hdr.addWidget(name_lbl)
        hdr.addStretch()

        status_lbl = QLabel("Enabled" if is_active else "Disabled")
        status_lbl.setStyleSheet(f"""
            font-size: 10px; font-weight: 600;
            color: {ALMA_SUCCESS if is_active else ALMA_TEXT_LIGHT};
            background: {'rgba(52,168,83,0.1)' if is_active else 'transparent'};
            border-radius: 8px; padding: 2px 8px;
        """)
        hdr.addWidget(status_lbl)
        cl.addLayout(hdr)

        # Description
        desc = QLabel(pipeline.get("description", ""))
        desc.setWordWrap(True)
        desc.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_MID};")
        cl.addWidget(desc)

        # Last run
        last_run = pipeline.get("last_run_date")
        if last_run:
            run_lbl = QLabel(f"Last run: {str(last_run)[:10]}")
            run_lbl.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT};")
            cl.addWidget(run_lbl)

        # Actions
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        run_btn = QPushButton("Run Now")
        run_btn.setCursor(Qt.PointingHandCursor)
        run_btn.setStyleSheet(_OUTLINE_BTN_STYLE)
        run_btn.setEnabled(is_active)
        btn_row.addWidget(run_btn)

        edit_btn = QPushButton("Edit")
        edit_btn.setCursor(Qt.PointingHandCursor)
        edit_btn.setStyleSheet(_OUTLINE_BTN_STYLE)
        btn_row.addWidget(edit_btn)

        btn_row.addStretch()
        cl.addLayout(btn_row)

        return card

    # ── Pipeline Config Card ──

    def _build_pipeline_config_card(self):
        config_card = QFrame()
        config_card.setStyleSheet(_CARD_STYLE)
        apply_card_shadow(config_card)
        cc = QVBoxLayout(config_card)
        cc.setContentsMargins(20, 16, 20, 16)
        cc.setSpacing(14)

        config_title = QLabel("Pipeline Configuration")
        config_title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        cc.addWidget(config_title)

        row = QHBoxLayout()
        row.setSpacing(16)

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("PROMPT")
        lbl.setStyleSheet(_LBL_STYLE)
        col.addWidget(lbl)
        self._smart_prompt_combo = QComboBox()
        self._smart_prompt_combo.setStyleSheet(_FIELD_STYLE)
        try:
            for p in self.db.get_prompts():
                self._smart_prompt_combo.addItem(p["name"], p["prompt_id"])
        except Exception:
            pass
        col.addWidget(self._smart_prompt_combo)
        row.addLayout(col, 2)

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("LOOKBACK DAYS")
        lbl.setStyleSheet(_LBL_STYLE)
        col.addWidget(lbl)
        self._smart_days_spin = QSpinBox()
        self._smart_days_spin.setRange(7, 365)
        self._smart_days_spin.setValue(30)
        self._smart_days_spin.setStyleSheet(_FIELD_STYLE)
        col.addWidget(self._smart_days_spin)
        row.addLayout(col, 1)

        # Source selector (Session 5)
        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("SOURCE")
        lbl.setStyleSheet(_LBL_STYLE)
        col.addWidget(lbl)
        from src.ui.widgets.source_selector import SourceSelector
        self._source_selector = SourceSelector(self)
        self._source_selector.setStyleSheet(_FIELD_STYLE)
        col.addWidget(self._source_selector)
        row.addLayout(col, 1)
        try:
            from src.data.connection_factory import get_connection
            conn = get_connection(self.db.db_path)
            self._source_selector.refresh_sources(conn)
            conn.close()
        except Exception:
            pass

        cc.addLayout(row)

        # ── NLP Scan Options ──
        nlp_row = QHBoxLayout()
        nlp_row.setSpacing(16)

        col = QVBoxLayout()
        col.setSpacing(4)
        self._nlp_enabled = QCheckBox("Include NLP Scan")
        self._nlp_enabled.setToolTip(
            "Run full-population NLP classification before report generation. "
            "Adds ~5-60 min depending on ticket count."
        )
        self._nlp_enabled.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_DARK}; font-weight: 600;")
        col.addWidget(self._nlp_enabled)
        nlp_row.addLayout(col, 2)

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("NLP BUDGET CAP")
        lbl.setStyleSheet(_LBL_STYLE)
        col.addWidget(lbl)
        self._nlp_budget = QDoubleSpinBox()
        self._nlp_budget.setRange(1.0, 500.0)
        self._nlp_budget.setValue(50.0)
        self._nlp_budget.setPrefix("$")
        self._nlp_budget.setDecimals(2)
        self._nlp_budget.setStyleSheet(_FIELD_STYLE)
        col.addWidget(self._nlp_budget)
        nlp_row.addLayout(col, 1)

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("NLP WORKERS")
        lbl.setStyleSheet(_LBL_STYLE)
        col.addWidget(lbl)
        self._nlp_workers = QSpinBox()
        self._nlp_workers.setRange(1, 3)
        self._nlp_workers.setValue(1)
        self._nlp_workers.setToolTip("Parallel worker subprocesses (1-3)")
        self._nlp_workers.setStyleSheet(_FIELD_STYLE)
        col.addWidget(self._nlp_workers)
        nlp_row.addLayout(col, 1)

        cc.addLayout(nlp_row)

        # Run button row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        self._smart_run_btn = QPushButton("  Run Full Pipeline  ")
        self._smart_run_btn.setMinimumHeight(42)
        self._smart_run_btn.setCursor(Qt.PointingHandCursor)
        self._smart_run_btn.setEnabled(self._gemini_available)
        self._smart_run_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_CREAM};
                border: none; border-radius: 10px; padding: 10px 28px;
                font-weight: 600; font-size: 14px;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
            QPushButton:disabled {{ background: {ALMA_BORDER}; color: {ALMA_TEXT_LIGHT}; }}
        """)
        self._smart_run_btn.clicked.connect(self._on_smart_run)
        btn_row.addWidget(self._smart_run_btn)

        self._smart_status = QLabel("")
        self._smart_status.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID};")
        btn_row.addWidget(self._smart_status, 1)

        cc.addLayout(btn_row)
        return config_card

    # ── Auto-Import Card ──

    def _build_auto_import_card(self):
        """Build the Auto-Import configuration card (Phase 2).

        Allows enabling automatic Zendesk/Lightdash data import before
        pipeline runs.  Persists to settings.yaml ``auto_import`` section.
        """
        card = QFrame()
        card.setStyleSheet(_CARD_STYLE)
        apply_card_shadow(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(14)

        # Title row with toggle
        title_row = QHBoxLayout()
        title_lbl = QLabel("Auto-Import")
        title_lbl.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;"
        )
        title_row.addWidget(title_lbl)
        title_row.addStretch()

        self._auto_import_toggle = QCheckBox("Enable pre-run import")
        self._auto_import_toggle.setToolTip(
            "When enabled, automatically pull fresh data from the configured "
            "source before running the analysis pipeline."
        )
        self._auto_import_toggle.setStyleSheet(
            f"font-size: 12px; color: {ALMA_TEXT_DARK}; font-weight: 600;"
        )
        self._auto_import_toggle.toggled.connect(self._on_auto_import_toggled)
        title_row.addWidget(self._auto_import_toggle)
        lay.addLayout(title_row)

        # Config fields container (hidden until toggle is on)
        self._auto_import_fields = QWidget()
        fields_lay = QVBoxLayout(self._auto_import_fields)
        fields_lay.setContentsMargins(0, 0, 0, 0)
        fields_lay.setSpacing(10)

        # Row 1: Source + Import Mode
        row1 = QHBoxLayout()
        row1.setSpacing(16)

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("SOURCE")
        lbl.setStyleSheet(_LBL_STYLE)
        col.addWidget(lbl)
        self._auto_import_source = QComboBox()
        self._auto_import_source.addItems(["Lightdash (CSV API)", "Zendesk (Incremental)"])
        self._auto_import_source.setStyleSheet(_FIELD_STYLE)
        self._auto_import_source.setToolTip("Data source for automatic import")
        self._auto_import_source.currentIndexChanged.connect(
            lambda: self._save_auto_import_settings()
        )
        col.addWidget(self._auto_import_source)
        row1.addLayout(col, 2)

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("IMPORT MODE")
        lbl.setStyleSheet(_LBL_STYLE)
        col.addWidget(lbl)
        self._auto_import_mode = QComboBox()
        self._auto_import_mode.addItems(["Full Refresh", "Incremental"])
        self._auto_import_mode.setStyleSheet(_FIELD_STYLE)
        self._auto_import_mode.setToolTip(
            "Full Refresh: delete + reimport all data.\n"
            "Incremental: only fetch new records since last import."
        )
        self._auto_import_mode.currentIndexChanged.connect(
            lambda: self._save_auto_import_settings()
        )
        col.addWidget(self._auto_import_mode)
        row1.addLayout(col, 1)

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("LOOKBACK DAYS")
        lbl.setStyleSheet(_LBL_STYLE)
        col.addWidget(lbl)
        self._auto_import_lookback = QSpinBox()
        self._auto_import_lookback.setRange(1, 365)
        self._auto_import_lookback.setValue(30)
        self._auto_import_lookback.setStyleSheet(_FIELD_STYLE)
        self._auto_import_lookback.setToolTip(
            "How many days of data to fetch (for Full Refresh mode)"
        )
        self._auto_import_lookback.valueChanged.connect(
            lambda: self._save_auto_import_settings()
        )
        col.addWidget(self._auto_import_lookback)
        row1.addLayout(col, 1)

        fields_lay.addLayout(row1)

        # Status label
        self._auto_import_status = QLabel("")
        self._auto_import_status.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_MID}; font-style: italic;"
        )
        fields_lay.addWidget(self._auto_import_status)

        self._auto_import_fields.setVisible(False)
        lay.addWidget(self._auto_import_fields)

        # Load saved state
        self._load_auto_import_settings()

        return card

    def _on_auto_import_toggled(self, checked):
        """Show/hide auto-import config fields and persist toggle state."""
        self._auto_import_fields.setVisible(checked)
        self._save_auto_import_settings()

    def _load_auto_import_settings(self):
        """Load auto-import configuration from settings.yaml."""
        try:
            from src.data.settings_manager import get_section
            ai_cfg = get_section("auto_import")
            self._auto_import_toggle.setChecked(ai_cfg.get("enabled", False))
            source_idx = ai_cfg.get("source_index", 0)
            if 0 <= source_idx < self._auto_import_source.count():
                self._auto_import_source.setCurrentIndex(source_idx)
            mode_idx = ai_cfg.get("mode_index", 0)
            if 0 <= mode_idx < self._auto_import_mode.count():
                self._auto_import_mode.setCurrentIndex(mode_idx)
            self._auto_import_lookback.setValue(ai_cfg.get("lookback_days", 30))
        except Exception:
            pass

    def _save_auto_import_settings(self):
        """Persist auto-import configuration to settings.yaml."""
        try:
            from src.data.settings_manager import set_section
            set_section("auto_import", {
                "enabled": self._auto_import_toggle.isChecked(),
                "source_index": self._auto_import_source.currentIndex(),
                "mode_index": self._auto_import_mode.currentIndex(),
                "lookback_days": self._auto_import_lookback.value(),
            })
        except Exception:
            pass

    def _get_auto_import_config(self) -> dict | None:
        """Return auto-import config if enabled, else None."""
        if not self._auto_import_toggle.isChecked():
            return None
        return {
            "source": self._auto_import_source.currentText(),
            "mode": self._auto_import_mode.currentText(),
            "lookback_days": self._auto_import_lookback.value(),
        }

    # ── Schedule Card (DB-backed persistent schedules) ──

    def _build_schedule_card(self):
        sched_card = QFrame()
        sched_card.setStyleSheet(_CARD_STYLE)
        apply_card_shadow_soft(sched_card)
        sc = QVBoxLayout(sched_card)
        sc.setContentsMargins(16, 14, 16, 14)
        sc.setSpacing(10)

        # Header
        sched_header_row = QHBoxLayout()
        sched_title = QLabel("Scheduled Runs")
        sched_title.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        sched_header_row.addWidget(sched_title)
        sched_header_row.addStretch()

        add_sched_btn = QPushButton("+ Add Schedule")
        add_sched_btn.setCursor(Qt.PointingHandCursor)
        add_sched_btn.setStyleSheet(_OUTLINE_BTN_STYLE)
        add_sched_btn.clicked.connect(self._show_add_schedule_form)
        sched_header_row.addWidget(add_sched_btn)
        sc.addLayout(sched_header_row)

        sched_desc = QLabel(
            "Persistent schedules survive app restarts. "
            "Pipeline runs automatically when the app is open and a schedule is due."
        )
        sched_desc.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none;")
        sched_desc.setWordWrap(True)
        sc.addWidget(sched_desc)

        # ── Add Schedule Form (hidden by default) ──
        self._sched_form_frame = QFrame()
        self._sched_form_frame.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_INSET};
                border: none;
                border-radius: 8px;
            }}
        """)
        self._sched_form_frame.setVisible(False)
        form_layout = QVBoxLayout(self._sched_form_frame)
        form_layout.setContentsMargins(12, 10, 12, 10)
        form_layout.setSpacing(8)

        # Name
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        lbl = QLabel("Name")
        lbl.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_MID}; border: none; font-weight: 600;")
        lbl.setFixedWidth(50)
        name_row.addWidget(lbl)
        self._sched_name_edit = QLineEdit()
        self._sched_name_edit.setPlaceholderText("e.g. Weekly Monday Report")
        self._sched_name_edit.setStyleSheet(_FIELD_STYLE)
        name_row.addWidget(self._sched_name_edit)
        form_layout.addLayout(name_row)

        # Repeat type + Day + Time + Timezone
        fields_row = QHBoxLayout()
        fields_row.setSpacing(8)

        col = QVBoxLayout()
        col.setSpacing(2)
        lbl = QLabel("REPEAT")
        lbl.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_MID}; border: none;")
        col.addWidget(lbl)
        self._sched_repeat_combo = QComboBox()
        self._sched_repeat_combo.addItems(["Daily", "Weekly", "Biweekly", "Monthly"])
        self._sched_repeat_combo.setCurrentIndex(1)  # Weekly default
        self._sched_repeat_combo.setStyleSheet(_FIELD_STYLE)
        self._sched_repeat_combo.currentIndexChanged.connect(self._on_repeat_type_changed)
        col.addWidget(self._sched_repeat_combo)
        fields_row.addLayout(col)

        col = QVBoxLayout()
        col.setSpacing(2)
        self._sched_day_label = QLabel("DAY")
        self._sched_day_label.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_MID}; border: none;")
        col.addWidget(self._sched_day_label)
        self._sched_day_combo = QComboBox()
        self._sched_day_combo.setStyleSheet(_FIELD_STYLE)
        self._populate_day_combo("weekly")
        col.addWidget(self._sched_day_combo)
        fields_row.addLayout(col)

        col = QVBoxLayout()
        col.setSpacing(2)
        lbl = QLabel("TIME")
        lbl.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_MID}; border: none;")
        col.addWidget(lbl)
        self._sched_time_edit = QTimeEdit()
        self._sched_time_edit.setDisplayFormat("hh:mm AP")
        self._sched_time_edit.setTime(QTime(6, 0))
        self._sched_time_edit.setStyleSheet(_FIELD_STYLE)
        col.addWidget(self._sched_time_edit)
        fields_row.addLayout(col)

        col = QVBoxLayout()
        col.setSpacing(2)
        lbl = QLabel("TIMEZONE")
        lbl.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_MID}; border: none;")
        col.addWidget(lbl)
        self._sched_tz_combo = QComboBox()
        self._sched_tz_combo.setStyleSheet(_FIELD_STYLE)
        try:
            from src.data.schedule_manager import SUPPORTED_TIMEZONES, TIMEZONE_LABELS
            for tz in SUPPORTED_TIMEZONES:
                self._sched_tz_combo.addItem(TIMEZONE_LABELS.get(tz, tz), tz)
        except ImportError:
            self._sched_tz_combo.addItem("Eastern (ET)", "America/New_York")
            self._sched_tz_combo.addItem("UTC", "UTC")
        col.addWidget(self._sched_tz_combo)
        fields_row.addLayout(col)

        form_layout.addLayout(fields_row)

        # Auto-export option
        self._sched_export_cb = QCheckBox("Auto-export to Drive")
        self._sched_export_cb.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_DARK};")
        form_layout.addWidget(self._sched_export_cb)

        # Save / Cancel
        form_btns = QHBoxLayout()
        form_btns.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_TEXT_MID};
                border: 1px solid {ALMA_BORDER}; border-radius: 6px;
                padding: 5px 14px; font-size: 11px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_CREAM}; }}
        """)
        cancel_btn.clicked.connect(lambda: self._sched_form_frame.setVisible(False))
        form_btns.addWidget(cancel_btn)

        save_btn = QPushButton("Save Schedule")
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_CREAM};
                border: none; border-radius: 6px;
                padding: 5px 14px; font-size: 11px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
        """)
        save_btn.clicked.connect(self._save_schedule)
        form_btns.addWidget(save_btn)
        form_layout.addLayout(form_btns)

        sc.addWidget(self._sched_form_frame)

        # ── Active Schedules List ──
        self._sched_list_frame = QFrame()
        self._sched_list_frame.setStyleSheet("border: none; background: transparent;")
        self._sched_list_layout = QVBoxLayout(self._sched_list_frame)
        self._sched_list_layout.setContentsMargins(0, 0, 0, 0)
        self._sched_list_layout.setSpacing(6)
        sc.addWidget(self._sched_list_frame)

        # Status label
        self._sched_status = QLabel("")
        self._sched_status.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_MID}; border: none;")
        sc.addWidget(self._sched_status)

        return sched_card

    # ── CLI Reference Card ──

    def _build_cli_card(self):
        cli_card = QFrame()
        cli_card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_INSET};
                border: none;
                border-radius: 12px;
            }}
        """)
        apply_card_shadow_soft(cli_card)
        cl = QVBoxLayout(cli_card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(10)

        cli_header_row = QHBoxLayout()
        cli_icon = QLabel(">_")
        cli_icon.setStyleSheet(f"""
            font-size: 11px; font-weight: 700; color: {ALMA_GREEN_DARK};
            background: rgba(20, 87, 63, 0.08); border-radius: 4px;
            padding: 2px 6px; border: none;
        """)
        cli_header_row.addWidget(cli_icon)
        cli_title = QLabel("CLI Usage")
        cli_title.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        cli_header_row.addWidget(cli_title)
        cli_header_row.addStretch()
        cl.addLayout(cli_header_row)

        cli_desc = QLabel(
            "Run the pipeline from the command line for automation via Task Scheduler or cron."
        )
        cli_desc.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none;")
        cli_desc.setWordWrap(True)
        cl.addWidget(cli_desc)

        # Code block
        code_frame = QFrame()
        code_frame.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_GREEN_DARK}; border-radius: 8px; border: none;
            }}
        """)
        code_layout = QVBoxLayout(code_frame)
        code_layout.setContentsMargins(14, 10, 14, 10)

        cli_cmd = QLabel("python -m src.data.smart_pipeline --export-drive")
        cli_cmd.setStyleSheet(f"""
            font-family: 'Cascadia Code', Consolas, 'SF Mono', monospace;
            font-size: 12px; color: {ALMA_CREAM}; border: none;
        """)
        cli_cmd.setTextInteractionFlags(Qt.TextSelectableByMouse)
        code_layout.addWidget(cli_cmd)
        cl.addWidget(code_frame)

        copy_cli = QPushButton("Copy Command")
        copy_cli.setCursor(Qt.PointingHandCursor)
        copy_cli.setStyleSheet(_OUTLINE_BTN_STYLE)
        copy_cli.clicked.connect(lambda: QApplication.clipboard().setText(
            "python -m src.data.smart_pipeline --export-drive"
        ))
        cl.addWidget(copy_cli)
        cl.addStretch()

        return cli_card

    # ── Report Viewer Card ──

    def _build_report_viewer_card(self):
        from src.ui.widgets.collapsible_section import CollapsibleSection
        from src.ui.widgets.markdown_viewer import MarkdownViewer

        self._report_viewer_section = CollapsibleSection(
            "Report Viewer",
            initially_collapsed=True,
            show_expand_button=True,
        )

        self._report_viewer = MarkdownViewer()
        self._report_viewer.setMinimumHeight(200)
        self._report_viewer_section.add_widget(self._report_viewer)

        # Analyst Reports sub-section
        self._analyst_section = CollapsibleSection(
            "Analyst Reports",
            initially_collapsed=True,
            show_expand_button=False,
        )
        self._analyst_viewer = MarkdownViewer()
        self._analyst_viewer.setMinimumHeight(100)
        self._analyst_section.add_widget(self._analyst_viewer)
        self._analyst_section.setVisible(False)
        self._report_viewer_section.add_widget(self._analyst_section)

        # Technical Summary sub-section
        self._tech_section = CollapsibleSection(
            "Technical Summary",
            initially_collapsed=True,
            show_expand_button=False,
        )
        self._tech_viewer = MarkdownViewer()
        self._tech_viewer.setMinimumHeight(100)
        self._tech_section.add_widget(self._tech_viewer)
        self._tech_section.setVisible(False)
        self._report_viewer_section.add_widget(self._tech_section)

        # Export action bar (Copy / Save .md / Save .html)
        export_row = QHBoxLayout()
        export_row.setSpacing(8)
        export_row.addStretch()

        _export_btn_style = f"""
            QPushButton {{
                background: transparent; color: {ALMA_TEXT_MID};
                font-size: 11px; font-weight: 600;
                border: 1px solid {ALMA_BORDER}; border-radius: 6px; padding: 4px 12px;
            }}
            QPushButton:hover {{ background: {ALMA_CREAM}; }}
        """

        self._export_copy_btn = QPushButton("Copy")
        self._export_copy_btn.setCursor(Qt.PointingHandCursor)
        self._export_copy_btn.setStyleSheet(_export_btn_style)
        self._export_copy_btn.clicked.connect(self._export_copy_report)
        export_row.addWidget(self._export_copy_btn)

        self._export_md_btn = QPushButton("Save .md")
        self._export_md_btn.setCursor(Qt.PointingHandCursor)
        self._export_md_btn.setStyleSheet(_export_btn_style)
        self._export_md_btn.clicked.connect(self._export_save_md)
        export_row.addWidget(self._export_md_btn)

        self._export_html_btn = QPushButton("Save .html")
        self._export_html_btn.setCursor(Qt.PointingHandCursor)
        self._export_html_btn.setStyleSheet(_export_btn_style)
        self._export_html_btn.clicked.connect(self._export_save_html)
        export_row.addWidget(self._export_html_btn)

        export_frame = QFrame()
        export_frame.setLayout(export_row)
        export_frame.setStyleSheet("border: none; background: transparent;")
        self._report_viewer_section.add_widget(export_frame)

        # Hide entire viewer until a report is selected
        self._report_viewer_section.setVisible(False)

        return self._report_viewer_section

    # ── Run History Card ──

    def _build_history_card(self):
        history_card = QFrame()
        history_card.setStyleSheet(_CARD_STYLE)
        apply_card_shadow_soft(history_card)
        hc = QVBoxLayout(history_card)
        hc.setContentsMargins(16, 14, 16, 14)
        hc.setSpacing(8)

        hist_header = QHBoxLayout()
        hist_title = QLabel("Run History")
        hist_title.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;"
        )
        hist_header.addWidget(hist_title)
        hist_header.addStretch()

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_TEXT_MID};
                font-size: 11px; font-weight: 600;
                border: 1px solid {ALMA_BORDER}; border-radius: 6px; padding: 3px 10px;
            }}
            QPushButton:hover {{ background: {ALMA_CREAM}; }}
        """)
        refresh_btn.clicked.connect(self._refresh_smart_history)
        hist_header.addWidget(refresh_btn)
        hc.addLayout(hist_header)

        self._smart_history_table = QTableWidget()
        self._smart_history_table.setColumnCount(7)
        self._smart_history_table.setHorizontalHeaderLabels(
            ["Started", "Status", "Tickets", "Duration", "Cost", "Source", "Report"]
        )
        self._smart_history_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._smart_history_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Fixed)
        self._smart_history_table.setColumnWidth(6, 70)
        self._smart_history_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Fixed)
        self._smart_history_table.setColumnWidth(4, 70)
        self._smart_history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._smart_history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._smart_history_table.setMinimumHeight(200)
        self._smart_history_table.verticalHeader().setVisible(False)
        self._smart_history_table.verticalHeader().setDefaultSectionSize(32)
        self._smart_history_table.setStyleSheet(f"""
            QTableWidget {{
                background: {ALMA_WHITE}; border: none;
                border-radius: 8px; font-size: 12px;
            }}
            QHeaderView::section {{
                background: {ALMA_CREAM}; font-weight: 600; font-size: 11px;
                color: {ALMA_TEXT_MID};
                border: none; padding: 8px 6px;
            }}
            QTableWidget::item {{
                padding: 4px 8px;
            }}
        """)
        self._smart_history_table.cellClicked.connect(self._on_history_cell_clicked)
        hc.addWidget(self._smart_history_table)

        return history_card

    # ═══════════════════════════════════════
    #  PIPELINE ACTIONS
    # ═══════════════════════════════════════

    def _build_pipeline_config(self, trigger_source="manual"):
        """Build pipeline config dict from current UI state."""
        prompt_id = self._smart_prompt_combo.currentData()
        prompt_data = self.db.get_prompt(prompt_id) if prompt_id else None

        source_id = self._source_selector.selected_source_id() if hasattr(self, '_source_selector') else None
        cfg = {
            "prompt_data": prompt_data or {
                "prompt_text": _load_prompt_text("general_trend.txt"),
                "system_prompt": "You are a Support Analytics engine.",
            },
            "lookback_days": self._smart_days_spin.value(),
            "trigger_source": trigger_source,
            "db_path": str(self.db.db_path),
            "nlp_scan": self._nlp_enabled.isChecked(),
            "nlp_budget_cap": self._nlp_budget.value(),
            "nlp_workers": self._nlp_workers.value(),
            "source_id": source_id,
        }
        # Attach auto-import config if enabled (Phase 2)
        auto_import = self._get_auto_import_config()
        if auto_import:
            cfg["auto_import"] = auto_import
        return cfg

    def _on_smart_run(self):
        if self._smart_worker and self._smart_worker.isRunning():
            return

        self._smart_run_btn.setEnabled(False)
        self._smart_run_btn.setText("  Running Pipeline...  ")
        self._smart_status.setText("Initializing...")

        config = self._build_pipeline_config("manual")
        self._start_pipeline_worker(config)

    def _start_pipeline_worker(self, config):
        """Start a SmartPipelineWorker with the given config."""
        self._smart_worker = SmartPipelineWorker(config)
        prefix = "[Scheduled] " if config.get("trigger_source") == "scheduled" else ""
        self._smart_worker.progress.connect(
            lambda msg: self._smart_status.setText(f"{prefix}{msg}")
        )
        self._smart_worker.finished.connect(self._on_smart_finished)
        self._smart_worker.error.connect(self._on_smart_error)
        self._smart_worker.start()

    def _on_smart_finished(self, result):
        self._smart_run_btn.setEnabled(self._gemini_available)
        self._smart_run_btn.setText("  Run Full Pipeline  ")
        status = result.get("status", "success")
        duration = result.get("duration_ms", 0)
        self._smart_status.setText(
            f"Pipeline complete: {status} ({duration}ms)"
        )
        self._refresh_smart_history()

        # Auto-open the report if we have one
        report_id = result.get("report_id")
        if report_id:
            self._view_report(report_id)

    def _on_smart_error(self, trace):
        self._smart_run_btn.setEnabled(self._gemini_available)
        self._smart_run_btn.setText("  Run Full Pipeline  ")
        self._smart_status.setText("Pipeline failed")
        QMessageBox.warning(self, "Pipeline Error", trace[:500])

    # ═══════════════════════════════════════
    #  RUN HISTORY
    # ═══════════════════════════════════════

    def _refresh_smart_history(self):
        try:
            # Prefer JOIN query with cost data; fall back to basic query
            if hasattr(self.db, 'get_smart_runs_with_reports'):
                runs = self.db.get_smart_runs_with_reports(limit=20)
            else:
                runs = self.db.get_smart_runs(limit=20)
        except Exception:
            runs = []

        self._smart_history_table.setRowCount(len(runs))
        for row, run in enumerate(runs):
            # Col 0: Started
            self._smart_history_table.setItem(row, 0, QTableWidgetItem(
                run.get("started_at", "")[:19]
            ))

            # Col 1: Status with color
            status = run.get("status", "")
            item = QTableWidgetItem(status)
            if status == "success":
                item.setForeground(QColor(ALMA_SUCCESS))
            elif status == "failed":
                item.setForeground(QColor(ALMA_ERROR))
            elif status == "running":
                item.setForeground(QColor(ALMA_WARNING))
            self._smart_history_table.setItem(row, 1, item)

            # Col 2: Tickets
            self._smart_history_table.setItem(row, 2, QTableWidgetItem(
                str(run.get("ticket_count", 0))
            ))

            # Col 3: Duration
            dur = run.get("duration_ms", 0)
            self._smart_history_table.setItem(row, 3, QTableWidgetItem(
                f"{dur/1000:.1f}s" if dur else ""
            ))

            # Col 4: Cost (from JOIN query gemini_usage aggregation)
            cost = run.get("run_cost_usd", 0)
            cost_item = QTableWidgetItem(f"${cost:.2f}" if cost else "—")
            if not cost:
                cost_item.setForeground(QColor(ALMA_TEXT_LIGHT))
            self._smart_history_table.setItem(row, 4, cost_item)

            # Col 5: Source
            self._smart_history_table.setItem(row, 5, QTableWidgetItem(
                run.get("trigger_source", "")
            ))

            # Col 6: Report link
            report_id = run.get("report_id")
            if report_id and status == "success":
                link_item = QTableWidgetItem("View")
                link_item.setForeground(QColor(ALMA_GREEN_DARK))
                link_item.setData(Qt.UserRole, report_id)
                link_item.setToolTip("Click to view this report")
            else:
                link_item = QTableWidgetItem("—")
                link_item.setForeground(QColor(ALMA_TEXT_LIGHT))
            self._smart_history_table.setItem(row, 6, link_item)

    def _on_history_cell_clicked(self, row, col):
        """Handle click on history table — column 6 = View report."""
        if col == 6:
            item = self._smart_history_table.item(row, 6)
            if item:
                report_id = item.data(Qt.UserRole)
                if report_id:
                    self._view_report(report_id)

    def _view_report(self, report_id):
        """Load a report by ID and display in the report viewer."""
        try:
            report = self.db.get_full_report(report_id)
        except Exception as e:
            logger.debug("Failed to load report %s: %s", report_id, e)
            return

        if not report:
            return

        # Show the viewer section
        self._report_viewer_section.setVisible(True)
        if self._report_viewer_section.is_collapsed():
            self._report_viewer_section.toggle()

        # Set main report content
        report_text = report.get("results_blob", "") or report.get("summary", "")
        self._report_viewer.set_markdown(report_text)

        # Try to load analyst reports for the latest scan
        try:
            from src.data.analyst_report_formatter import get_latest_analyst_summary
            analyst_md, _ = get_latest_analyst_summary(self.db)
            if analyst_md:
                self._analyst_viewer.set_markdown(analyst_md)
                self._analyst_section.setVisible(True)
            else:
                self._analyst_section.setVisible(False)
        except Exception:
            self._analyst_section.setVisible(False)

        # Try to load tech summary
        try:
            from src.data.tech_summary_builder import (
                build_tech_summary, format_tech_summary_as_markdown,
            )
            summary = build_tech_summary(self.db)
            if summary.get("gemini_usage") or summary.get("scan_stats"):
                tech_md = format_tech_summary_as_markdown(summary)
                self._tech_viewer.set_markdown(tech_md)
                self._tech_section.setVisible(True)
            else:
                self._tech_section.setVisible(False)
        except Exception:
            self._tech_section.setVisible(False)

    # ═══════════════════════════════════════
    #  EXPORT ACTIONS (Report Viewer bar)
    # ═══════════════════════════════════════

    def _export_copy_report(self):
        """Copy the current report markdown to clipboard."""
        md = self._report_viewer.get_markdown()
        if md:
            QApplication.clipboard().setText(md)
            self._export_copy_btn.setText("Copied!")
            QTimer.singleShot(2000, lambda: self._export_copy_btn.setText("Copy"))

    def _export_save_md(self):
        """Save current report as .md file."""
        md = self._report_viewer.get_markdown()
        if not md:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Report", "", "Markdown (*.md);;Text (*.txt)"
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(md)
            self._export_md_btn.setText("Saved!")
            QTimer.singleShot(2000, lambda: self._export_md_btn.setText("Save .md"))

    def _export_save_html(self):
        """Save current report as styled .html file."""
        md = self._report_viewer.get_markdown()
        if not md:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Report as HTML", "", "HTML (*.html)"
        )
        if path:
            from src.ui.widgets.markdown_viewer import md_to_html
            html_content = md_to_html(md)
            # Wrap in full HTML document with Alma styling
            full_html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Alma Insights Report</title>
<style>
  body {{ font-family: 'Segoe UI', sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; color: #333; }}
  h1, h2, h3 {{ color: #14573F; }}
  table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
  th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
  th {{ background: #f5f5f0; font-weight: 600; }}
  code {{ background: #f5f5f0; padding: 2px 6px; border-radius: 3px; font-family: 'Cascadia Code', Consolas, monospace; }}
  pre {{ background: #f5f5f0; padding: 12px; border-radius: 6px; overflow-x: auto; }}
  blockquote {{ border-left: 3px solid #5BA888; margin: 12px 0; padding: 8px 16px; color: #555; }}
</style>
</head><body>
{html_content}
</body></html>"""
            with open(path, "w", encoding="utf-8") as f:
                f.write(full_html)
            self._export_html_btn.setText("Saved!")
            QTimer.singleShot(2000, lambda: self._export_html_btn.setText("Save .html"))

    # ═══════════════════════════════════════
    #  SCHEDULE MANAGEMENT (DB-backed)
    # ═══════════════════════════════════════

    def _show_add_schedule_form(self):
        """Show the inline schedule creation form."""
        if not self._gemini_available:
            QMessageBox.warning(
                self, "Gemini Required",
                "Gemini must be configured before scheduling reports."
            )
            return
        self._sched_name_edit.clear()
        self._sched_repeat_combo.setCurrentIndex(1)  # Weekly
        self._sched_time_edit.setTime(QTime(6, 0))
        self._sched_tz_combo.setCurrentIndex(0)
        self._sched_export_cb.setChecked(False)
        self._sched_form_frame.setVisible(True)

    def _on_repeat_type_changed(self, index):
        """Update the day combo based on repeat type selection."""
        repeat_type = self._sched_repeat_combo.currentText().lower()
        self._populate_day_combo(repeat_type)

    def _populate_day_combo(self, repeat_type):
        """Populate the day combo for the given repeat type."""
        self._sched_day_combo.clear()
        if repeat_type == "daily":
            self._sched_day_combo.addItem("Every day", 0)
            self._sched_day_combo.setEnabled(False)
            self._sched_day_label.setText("DAY")
        elif repeat_type in ("weekly", "biweekly"):
            days = ["Monday", "Tuesday", "Wednesday", "Thursday",
                    "Friday", "Saturday", "Sunday"]
            for i, d in enumerate(days):
                self._sched_day_combo.addItem(d, i)
            self._sched_day_combo.setEnabled(True)
            self._sched_day_label.setText("DAY OF WEEK")
        elif repeat_type == "monthly":
            for d in range(1, 29):
                suffix = "th"
                if d in (1, 21): suffix = "st"
                elif d in (2, 22): suffix = "nd"
                elif d in (3, 23): suffix = "rd"
                self._sched_day_combo.addItem(f"{d}{suffix}", d)
            self._sched_day_combo.setEnabled(True)
            self._sched_day_label.setText("DAY OF MONTH")

    def _save_schedule(self):
        """Save a new schedule to the database."""
        name = self._sched_name_edit.text().strip()
        if not name:
            name = f"{self._sched_repeat_combo.currentText()} Report"

        repeat_type = self._sched_repeat_combo.currentText().lower()
        repeat_day = self._sched_day_combo.currentData() or 0
        time_val = self._sched_time_edit.time()
        repeat_time = f"{time_val.hour():02d}:{time_val.minute():02d}"
        timezone = self._sched_tz_combo.currentData() or "America/New_York"

        # Build config from current pipeline settings
        config = self._build_pipeline_config("scheduled")
        config["auto_export"] = self._sched_export_cb.isChecked()

        # Compute first next_run
        try:
            from src.data.schedule_manager import compute_next_run
            next_run = compute_next_run(repeat_type, repeat_day, repeat_time, timezone)
        except Exception:
            from datetime import datetime, timedelta
            next_run = (datetime.now() + timedelta(hours=24)).isoformat()

        try:
            self.db.save_schedule(
                name=name,
                page="smart_reporting",
                config_json=config,
                timezone=timezone,
                repeat_type=repeat_type,
                repeat_day=repeat_day,
                repeat_time=repeat_time,
                next_run_at=next_run,
            )
        except Exception as e:
            QMessageBox.warning(self, "Save Failed", f"Could not save schedule: {e}")
            return

        self._sched_form_frame.setVisible(False)
        self._refresh_schedule_list()
        logger.info("Schedule '%s' saved (next: %s)", name, next_run)

    def _refresh_schedule_list(self):
        """Refresh the active schedules display from DB."""
        # Clear existing cards
        while self._sched_list_layout.count():
            item = self._sched_list_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        try:
            schedules = self.db.get_schedules()
        except Exception:
            schedules = []

        if not schedules:
            empty = QLabel("No schedules configured")
            empty.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none;")
            self._sched_list_layout.addWidget(empty)
            self._sched_status.setText("")
            return

        active_count = sum(1 for s in schedules if s.get("enabled"))
        self._sched_status.setText(
            f"{active_count} active schedule{'s' if active_count != 1 else ''}"
        )
        self._sched_status.setStyleSheet(
            f"font-size: 11px; color: {ALMA_SUCCESS if active_count > 0 else ALMA_TEXT_MID}; border: none;"
        )

        for sched in schedules:
            card = self._build_schedule_mini_card(sched)
            self._sched_list_layout.addWidget(card)

    def _build_schedule_mini_card(self, sched):
        """Build a mini card widget for one schedule."""
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_WHITE};
                border: none;
                border-radius: 6px;
            }}
        """)
        row = QHBoxLayout(card)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(8)

        # Enable toggle
        toggle = QCheckBox()
        toggle.setChecked(bool(sched.get("enabled", 0)))
        schedule_id = sched["schedule_id"]
        toggle.toggled.connect(
            lambda checked, sid=schedule_id: self._toggle_schedule(sid, checked)
        )
        row.addWidget(toggle)

        # Name + next run info
        info_col = QVBoxLayout()
        info_col.setSpacing(1)
        name_lbl = QLabel(sched.get("name", "Unnamed"))
        name_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {ALMA_TEXT_DARK}; border: none;")
        info_col.addWidget(name_lbl)

        repeat_type = sched.get("repeat_type", "weekly").capitalize()
        repeat_time = sched.get("repeat_time", "06:00")
        tz = sched.get("timezone", "UTC")

        try:
            from src.data.schedule_manager import TIMEZONE_LABELS
            tz_label = TIMEZONE_LABELS.get(tz, tz)
        except ImportError:
            tz_label = tz

        next_run = sched.get("next_run_at", "")
        try:
            from src.data.schedule_manager import format_next_run
            next_label = format_next_run(next_run)
        except ImportError:
            next_label = next_run[:16] if next_run else ""

        detail_text = f"{repeat_type} at {repeat_time} {tz_label}"
        if next_label:
            detail_text += f"  |  Next: {next_label}"

        detail_lbl = QLabel(detail_text)
        detail_lbl.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_MID}; border: none;")
        info_col.addWidget(detail_lbl)
        row.addLayout(info_col, 1)

        # Delete button
        del_btn = QPushButton("Delete")
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_ERROR};
                border: 1px solid {ALMA_ERROR}; border-radius: 4px;
                padding: 2px 8px; font-size: 10px; font-weight: 600;
            }}
            QPushButton:hover {{ background: rgba(235, 87, 87, 0.08); }}
        """)
        del_btn.clicked.connect(
            lambda _, sid=schedule_id, nm=sched.get("name", ""): self._delete_schedule(sid, nm)
        )
        row.addWidget(del_btn)

        return card

    def _toggle_schedule(self, schedule_id, enabled):
        """Enable/disable a schedule."""
        try:
            self.db.update_schedule(schedule_id, enabled=1 if enabled else 0)
        except Exception as e:
            logger.error("Failed to toggle schedule %d: %s", schedule_id, e)
        self._refresh_schedule_list()

    def _delete_schedule(self, schedule_id, name):
        """Delete a schedule after confirmation."""
        reply = QMessageBox.question(
            self, "Delete Schedule",
            f"Delete schedule '{name}'? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            try:
                self.db.delete_schedule(schedule_id)
            except Exception as e:
                logger.error("Failed to delete schedule %d: %s", schedule_id, e)
            self._refresh_schedule_list()

    # ═══════════════════════════════════════
    #  SCHEDULE MANAGER INTEGRATION
    # ═══════════════════════════════════════

    def set_schedule_manager(self, manager):
        """Wire the ScheduleManager to this page's pipeline execution.

        Called by main_window after ScheduleManager is instantiated.
        """
        self._schedule_manager = manager
        manager.run_triggered.connect(self._on_schedule_triggered)

    def _on_schedule_triggered(self, config):
        """Handle a scheduled run from ScheduleManager."""
        if self._smart_worker and self._smart_worker.isRunning():
            logger.info("Scheduled run skipped: pipeline already running")
            return

        logger.info(
            "Scheduled run triggered: %s",
            config.get("schedule_name", "unknown")
        )
        self._smart_run_btn.setEnabled(False)
        self._smart_run_btn.setText("  Running Pipeline...  ")
        self._smart_status.setText(
            f"[Scheduled: {config.get('schedule_name', '')}] Initializing..."
        )

        # Ensure db_path is set
        config.setdefault("db_path", str(self.db.db_path))
        self._start_pipeline_worker(config)

    # ═══════════════════════════════════════
    #  PUBLIC API
    # ═══════════════════════════════════════

    def refresh_gemini_status(self):
        self._gemini_available = self._check_gemini()
        self._smart_run_btn.setEnabled(self._gemini_available)
