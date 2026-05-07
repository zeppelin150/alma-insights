"""
Alma Insights — AI Reports Page (Standard Reports)
Single-purpose page for prompt-based report generation via Gemini.
A/B Compare and Smart Reporting are separate sidebar pages.
"""

import json
import time
import yaml
from pathlib import Path

from src.data.settings_manager import load_settings, get_section
from src.data.connection_factory import get_connection

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QFrame, QScrollArea, QTextEdit,
    QFileDialog, QApplication, QSizePolicy, QStackedWidget,
    QMessageBox, QTabWidget,
)
from PySide6.QtCore import Qt, QDate, QThread, QTimer, Signal

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_GREEN_SUBTLE, ALMA_GREEN_MID,
    ALMA_WHITE, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_TEXT_ON_DARK, ALMA_BORDER, ALMA_BORDER_LIGHT,
    ALMA_SUCCESS, ALMA_WARNING, ALMA_ERROR, ALMA_INFO,
    ALMA_BG_ELEVATED, apply_card_shadow, apply_card_shadow_soft,
)
from src.ui.widgets.date_picker import ModernDatePicker
from src.ui.widgets.report_history_summary import ReportHistorySummary
from src.ui.widgets.generation_animation import GenerationAnimationWidget
from src.ui.widgets.chat_widget import ReportChatWidget
from src.ui.widgets.markdown_viewer import MarkdownViewer
from src.ui.widgets.collapsible_section import CollapsibleSection
# R1.8: structured-output canvas replaces the raw markdown viewer for the
# main report panel. The canvas owns its own MarkdownViewer for the legacy
# fallback path, so all existing _output_area.set_markdown() / clear()
# call sites still work — see src/ui/widgets/report_canvas.py.
from src.ui.widgets.report_canvas import ReportCanvas
from src.data.report_schema import Report as _StructuredReport  # R1.8 hydration


# ═══════════════════════════════════════════
#  CANNED PROMPT DEFINITIONS
# ═══════════════════════════════════════════

CANNED_PROMPTS = [
    {
        "name": "General Trend Analysis",
        "description": "Full theme synthesis, anomalies, and recommendations",
        "prompt_file": "general_trend.txt",
        "system_prompt": "You are a Support Analytics engine. Data is provided as pre-computed statistics. You do NOT load files or access URLs.",
    },
    {
        "name": "High-Sentiment Analysis",
        "description": "Sentiment matrix, high-sentiment ngrams, trends by TRC",
        "prompt_file": "sentiment_dive.txt",
        "system_prompt": "You are a Support Analytics engine specializing in sentiment analysis for RCM operations.",
    },
    {
        "name": "RCM Theme Mapping",
        "description": "Maps themes to RCM taxonomy categories",
        "prompt_file": "rcm_themes.txt",
        "system_prompt": "You are a Support Analytics engine specializing in RCM taxonomy.",
    },
    {
        "name": "Executive Summary",
        "description": "1-page: top 5 findings, top 5 recommendations, risk flags",
        "prompt_file": "executive_summary.txt",
        "system_prompt": "You are a Support Analytics engine producing executive summaries.",
    },
    {
        "name": "Incident Summary",
        "description": "Poisson/CUSUM flags, drift analysis, system health",
        "prompt_file": "incident_summary.txt",
        "system_prompt": "You are a Support Analytics engine specializing in incident detection.",
    },
]


def _load_prompt_text(filename):
    """Load prompt text from config/prompts/ file."""
    path = Path(__file__).parent.parent.parent.parent / "config" / "prompts" / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _seed_prompts_if_needed(db):
    """Seed canned prompts on first run."""
    existing = db.get_prompts(category="canned")
    if existing:
        return
    prompts = []
    for p in CANNED_PROMPTS:
        text = _load_prompt_text(p["prompt_file"])
        if text:
            prompts.append({
                "name": p["name"],
                "description": p["description"],
                "prompt_text": text,
                "system_prompt": p["system_prompt"],
            })
    if prompts:
        db.seed_canned_prompts(prompts)


# ═══════════════════════════════════════════
#  REPORT WORKER THREAD
# ═══════════════════════════════════════════

class ReportWorker(QThread):
    """Background thread for report generation using prompt library + report builder."""
    progress = Signal(str)
    finished = Signal(str, str)  # (report_text, data_block_text)
    error = Signal(str)

    def __init__(self, db_path, prompt_data, date_start, date_end,
                 trc_filter, gemini_client, max_conversations=300):
        super().__init__()
        self.db_path = db_path
        self.prompt_data = prompt_data
        self.date_start = date_start
        self.date_end = date_end
        self.trc_filter = trc_filter
        self.gemini_client = gemini_client
        self.max_conversations = max_conversations

    def run(self):
        try:
            from src.data.db_manager import DatabaseManager
            from src.data.report_builder import (
                build_data_block, format_data_block_for_prompt,
                replace_prompt_variables, _validate_prompt_before_send,
            )

            db = DatabaseManager(self.db_path)
            db.initialize()

            self.progress.emit("Building analytics data block...")
            t0 = time.time()

            block = build_data_block(
                db, self.date_start, self.date_end,
                trc_filter=self.trc_filter or None,
            )

            self.progress.emit("Formatting prompt...")
            data_block_text = format_data_block_for_prompt(block)

            # Replace variables in prompt
            prompt_text = self.prompt_data.get("prompt_text", "")
            prompt = replace_prompt_variables(prompt_text, block)

            # Validate
            try:
                _validate_prompt_before_send(prompt)
            except ValueError as ve:
                self.error.emit(f"Prompt validation failed: {ve}")
                db.close()
                return

            self.progress.emit("Sending to Gemini...")
            system_prompt = self.prompt_data.get("system_prompt", "")
            result = self.gemini_client.generate(prompt, system_prompt=system_prompt)

            db.close()
            self.finished.emit(result, data_block_text)
        except Exception as e:
            import traceback
            self.error.emit(traceback.format_exc())


class PipelineWorker(QThread):
    """Background thread using AIReportPipeline (Phase 5.5B).

    Replaces ReportWorker with multi-phase pipeline that includes
    analyst reports and technical summary.
    """
    progress = Signal(str)
    finished = Signal(dict)  # full pipeline result dict
    error = Signal(str)

    def __init__(self, db_path, prompt_data, date_start, date_end,
                 trc_filter, gemini_client, source_id=None):
        super().__init__()
        self.db_path = db_path
        self.prompt_data = prompt_data
        self.date_start = date_start
        self.date_end = date_end
        self.trc_filter = trc_filter
        self.gemini_client = gemini_client
        self.source_id = source_id

    def run(self):
        try:
            from src.data.db_manager import DatabaseManager
            from src.data.ai_report_pipeline import AIReportPipeline

            db = DatabaseManager(self.db_path)
            db.initialize()

            pipeline = AIReportPipeline(
                db,
                gemini_client=self.gemini_client,
                progress_cb=self.progress.emit,
            )
            result = pipeline.run(
                self.prompt_data,
                self.date_start,
                self.date_end,
                trc_filter=self.trc_filter,
                source_id=self.source_id,
            )
            db.close()
            self.finished.emit(result)
        except Exception as e:
            import traceback
            self.error.emit(traceback.format_exc())


# ═══════════════════════════════════════════
#  VOC REPORT WORKER THREAD
# ═══════════════════════════════════════════

class VOCReportWorker(QThread):
    """Background thread for VOC Root Cause Analysis report generation.

    Boots a ReportOrchestrator (3 bridge pool) for parallel Phase 1 TRC
    analysis.  Falls back to sequential if boot fails.
    """
    progress = Signal(str, int)    # (message, percent)
    finished = Signal(dict)        # full results dict
    error = Signal(str)            # traceback

    def __init__(self, db_path, date_start, date_end,
                 trc_filter, gemini_client):
        super().__init__()
        self.db_path = db_path
        self.date_start = date_start
        self.date_end = date_end
        self.trc_filter = trc_filter
        self.gemini_client = gemini_client
        self._builder = None
        self._orchestrator = None

    def run(self):
        try:
            from src.data.db_manager import DatabaseManager
            from src.data.voc_builder import VOCBuilder

            db = DatabaseManager(self.db_path)
            db.initialize()

            # Boot parallel bridge pool for Phase 1
            orchestrator = self._boot_orchestrator()

            self._builder = VOCBuilder(
                db, self.gemini_client,
                progress_callback=lambda msg, pct: self.progress.emit(msg, pct),
                orchestrator=orchestrator,
            )

            result = self._builder.run(
                self.date_start, self.date_end,
                trc_filter=self.trc_filter or None,
            )

            db.close()

            # Shutdown bridge pool
            if orchestrator:
                orchestrator.shutdown()

            self.finished.emit(result)
        except Exception:
            import traceback as tb
            # Ensure cleanup on error
            if self._orchestrator:
                try:
                    self._orchestrator.shutdown()
                except Exception:
                    pass
            self.error.emit(tb.format_exc())

    def _boot_orchestrator(self):
        """Boot a ReportOrchestrator for parallel Phase 1.  Returns None on failure."""
        try:
            from src.agents.report_orchestrator import ReportOrchestrator
            from src.data.settings_manager import get_section

            gemini_cfg = get_section("gemini", {})
            model = gemini_cfg.get("model", "gemini-2.5-flash")
            agents_cfg = get_section("agents", {})
            num_bridges = agents_cfg.get("num_workers", 8)

            self._orchestrator = ReportOrchestrator(
                db_path=self.db_path,
                model=model,
                num_bridges=num_bridges,
            )
            self._orchestrator.boot()
            return self._orchestrator
        except Exception as e:
            import logging
            logging.getLogger("alma.voc").warning(
                "VOCReportWorker: orchestrator boot failed (%s), "
                "falling back to sequential", e
            )
            return None

    def cancel(self):
        """Signal cancellation to the builder and orchestrator."""
        if self._builder:
            self._builder.cancel()
        if self._orchestrator:
            self._orchestrator.cancel()


# ═══════════════════════════════════════════
#  HELPER: Build Gemini Client from config
# ═══════════════════════════════════════════

def _build_gemini_client():
    """Create LLM client for report generation via task-routed factory."""
    from src.gemini.client_factory import build_client_for_task
    return build_client_for_task("report_generation")


# ═══════════════════════════════════════════
#  HELPER: Structured metadata for the EvidencePanel (R1.8)
# ═══════════════════════════════════════════

def _meta_from_report(report, *, fallback_md: str = "") -> dict:
    """Build the dict consumed by EvidencePanel.show_metadata.

    Used on the legacy / parse-failure path where ReportCanvas falls back
    to set_markdown() and the canvas's own metadata signal would otherwise
    not fire. Mirrors the keys ReportCanvas._emit_metadata builds — keep
    the two in sync if you change either.
    """
    scope = report.scope or {}
    bridges = report.bridges_used if report.pipeline_kind == "multi_bridge" else 1
    spec = report.specialist_count if report.pipeline_kind == "multi_bridge" else 0
    return {
        "pipeline":     report.pipeline_kind,
        "specialists":  spec,
        "tickets":      scope.get("ticket_count") or scope.get("tickets") or "-",
        "trc categories": str(scope.get("trc_filter") or "-"),
        "generated":    report.generated_at or "-",
        "cost":         f"${report.cost_usd:.2f}" if report.cost_usd else "-",
        "bridges":      bridges,
        "accuracy_score": report.accuracy_score,
        "accuracy_flags": list(report.accuracy_flags),
        "duration_sec": report.duration_sec,
    }


# ═══════════════════════════════════════════
#  AI REPORTS PAGE
# ═══════════════════════════════════════════

class AIReportsPage(QWidget):
    """AI Reports page: prompt library, report generation, and history.

    Supports three generation paths:
      - Standard reports via `ReportWorker` (single-shot Gemini call).
      - Multi-phase pipeline via `PipelineWorker` (Phase 5.5B pipeline
        with data assembly + analyst reports + tech summary).
      - VOC root cause analysis via `VOCReportWorker` (parallel 3-bridge
        pool for Phase 1 TRC analysis plus accumulator and specialists).

    Tabs: Generate, Prompts (library), History.
    """

    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db = db_manager
        self._worker = None
        self._voc_worker = None
        self._shared_gemini_client = None
        self._current_report_text = ""
        self._current_data_block = ""
        self._current_report: _StructuredReport | None = None  # R1.8 structured payload
        self._drilldown = None
        self._scan_blocked = False

        # Seed canned prompts
        try:
            _seed_prompts_if_needed(self.db)
        except Exception:
            pass

        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Build 11.0: Header + Tab Widget wrapper ──
        header_widget = QWidget()
        header_layout = QVBoxLayout(header_widget)
        header_layout.setContentsMargins(28, 24, 28, 0)
        header_layout.setSpacing(8)

        header = QLabel("AI Reports")
        header.setObjectName("PageHeader")
        header_layout.addWidget(header)

        sub = QLabel("Generate AI-powered analysis reports from ticket data via Gemini")
        sub.setObjectName("PageSubheader")
        header_layout.addWidget(sub)

        outer.addWidget(header_widget)

        # ── Tab Widget ──
        self._tab_widget = QTabWidget()
        self._tab_widget.setStyleSheet(f"""
            QTabWidget::pane {{
                border: none;
                background: transparent;
            }}
            QTabBar::tab {{
                background: transparent;
                color: {ALMA_TEXT_MID};
                padding: 8px 18px;
                margin-right: 4px;
                font-size: 13px;
                font-weight: 600;
                border: none;
                border-bottom: 2px solid transparent;
            }}
            QTabBar::tab:selected {{
                color: {ALMA_GREEN_DARK};
                border-bottom: 2px solid {ALMA_GREEN_DARK};
            }}
            QTabBar::tab:hover {{
                color: {ALMA_GREEN_MID};
            }}
        """)
        outer.addWidget(self._tab_widget)

        # ── Tab 0: Analysis Canvas (existing report generation UI) ──
        canvas_widget = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 12, 28, 24)
        layout.setSpacing(16)

        # Check Gemini
        self._gemini_available = self._check_gemini()
        if not self._gemini_available:
            self._build_setup_guide(layout)

        # Controls card
        controls = QFrame()
        controls.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED}; border: none;
                border-radius: 12px;
            }}
        """)
        apply_card_shadow(controls)
        cl = QVBoxLayout(controls)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(10)

        lbl_style = f"font-size: 11px; font-weight: 600; color: {ALMA_TEXT_MID}; letter-spacing: 0.5px;"
        field_style = f"""
            QComboBox, QDateEdit, QSpinBox {{
                color: {ALMA_TEXT_DARK}; background: {ALMA_WHITE};
                border: none; border-radius: 8px;
                padding: 8px 12px; font-size: 13px;
            }}
        """

        # Row 1: Prompt selector + Manage button
        row1 = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("Prompt")
        lbl.setStyleSheet(lbl_style)
        col.addWidget(lbl)
        self._prompt_combo = QComboBox()
        self._prompt_combo.setStyleSheet(field_style)
        self._populate_prompt_combo()
        col.addWidget(self._prompt_combo)
        row1.addLayout(col, 3)

        col = QVBoxLayout()
        col.setSpacing(4)
        col.addWidget(QLabel(""))  # spacer
        manage_btn = QPushButton("Manage Prompts")
        manage_btn.setCursor(Qt.PointingHandCursor)
        manage_btn.setMinimumHeight(36)
        manage_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_INFO};
                border: none; border-radius: 8px;
                padding: 8px 16px; font-size: 12px; font-weight: 600;
            }}
            QPushButton:hover {{ background: #E8F0FE; }}
        """)
        manage_btn.clicked.connect(self._open_prompt_manager)
        col.addWidget(manage_btn)
        row1.addLayout(col, 1)
        cl.addLayout(row1)

        # Row 2: Source + TRC filter + Date range
        row2 = QHBoxLayout()
        row2.setSpacing(12)

        # Source selector (Session 5)
        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("Source")
        lbl.setStyleSheet(lbl_style)
        col.addWidget(lbl)
        from src.ui.widgets.source_selector import SourceSelector
        self._source_selector = SourceSelector(self)
        self._source_selector.setStyleSheet(field_style)
        col.addWidget(self._source_selector)
        row2.addLayout(col, 1)
        try:
            from src.data.connection_factory import get_connection
            conn = get_connection(self.db.db_path)
            self._source_selector.refresh_sources(conn)
            conn.close()
        except Exception:
            pass

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("TRC Filter")
        lbl.setStyleSheet(lbl_style)
        col.addWidget(lbl)
        self._trc_combo = QComboBox()
        self._trc_combo.addItem("All TRCs", "")
        self._trc_combo.setStyleSheet(field_style)
        col.addWidget(self._trc_combo)
        row2.addLayout(col, 1)

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("From")
        lbl.setStyleSheet(lbl_style)
        col.addWidget(lbl)
        self._date_from = ModernDatePicker()
        self._date_from.setDate(QDate.currentDate().addDays(-90))
        self._date_from.setStyleSheet(field_style)
        col.addWidget(self._date_from)
        row2.addLayout(col, 1)

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("To")
        lbl.setStyleSheet(lbl_style)
        col.addWidget(lbl)
        self._date_to = ModernDatePicker()
        self._date_to.setDate(QDate.currentDate())
        self._date_to.setStyleSheet(field_style)
        col.addWidget(self._date_to)
        row2.addLayout(col, 1)

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("")
        lbl.setStyleSheet(lbl_style)
        col.addWidget(lbl)
        self._generate_btn = QPushButton("  Generate Report  ")
        self._generate_btn.setMinimumHeight(36)
        self._generate_btn.setCursor(Qt.PointingHandCursor)
        self._generate_btn.setEnabled(self._gemini_available)
        self._generate_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_CREAM};
                border: none; border-radius: 8px; padding: 8px 20px;
                font-weight: 600; font-size: 13px;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
            QPushButton:disabled {{ background: {ALMA_BORDER}; color: {ALMA_TEXT_LIGHT}; }}
        """)
        self._generate_btn.clicked.connect(self._on_generate)
        col.addWidget(self._generate_btn)
        row2.addLayout(col, 1)

        cl.addLayout(row2)
        layout.addWidget(controls)

        # Progress
        self._progress_lbl = QLabel("")
        self._progress_lbl.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID};")
        self._progress_lbl.setVisible(False)
        layout.addWidget(self._progress_lbl)

        # Report output card — expanded
        output_card = QFrame()
        output_card.setStyleSheet(f"""
            QFrame {{ background: {ALMA_BG_ELEVATED}; border: none; border-radius: 12px; }}
        """)
        apply_card_shadow(output_card)
        ol = QVBoxLayout(output_card)
        ol.setContentsMargins(16, 14, 16, 14)
        ol.setSpacing(8)

        # Output header + action buttons
        out_row = QHBoxLayout()
        out_title = QLabel("Report Output")
        out_title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        out_row.addWidget(out_title)
        out_row.addStretch()

        btn_style = f"""
            QPushButton {{
                background: {ALMA_WHITE}; color: {ALMA_TEXT_DARK};
                font-size: 11px; font-weight: 600;
                border: none; border-radius: 6px; padding: 4px 12px;
            }}
            QPushButton:hover {{ background: {ALMA_CREAM}; }}
            QPushButton:disabled {{ color: {ALMA_TEXT_LIGHT}; }}
        """

        self._copy_btn = QPushButton("Copy")
        self._copy_btn.setEnabled(False)
        self._copy_btn.setCursor(Qt.PointingHandCursor)
        self._copy_btn.setStyleSheet(btn_style)
        self._copy_btn.clicked.connect(self._copy_report)
        out_row.addWidget(self._copy_btn)

        self._save_md_btn = QPushButton("Save .md")
        self._save_md_btn.setEnabled(False)
        self._save_md_btn.setCursor(Qt.PointingHandCursor)
        self._save_md_btn.setStyleSheet(btn_style)
        self._save_md_btn.clicked.connect(self._save_report_md)
        out_row.addWidget(self._save_md_btn)

        self._save_html_btn = QPushButton("Save .html")
        self._save_html_btn.setEnabled(False)
        self._save_html_btn.setCursor(Qt.PointingHandCursor)
        self._save_html_btn.setStyleSheet(btn_style)
        self._save_html_btn.clicked.connect(self._save_report_html)
        out_row.addWidget(self._save_html_btn)

        self._save_history_btn = QPushButton("Save to History")
        self._save_history_btn.setEnabled(False)
        self._save_history_btn.setCursor(Qt.PointingHandCursor)
        self._save_history_btn.setStyleSheet(btn_style)
        self._save_history_btn.clicked.connect(self._save_to_history)
        out_row.addWidget(self._save_history_btn)

        self._export_drive_btn = QPushButton("Export to Drive")
        self._export_drive_btn.setEnabled(False)
        self._export_drive_btn.setCursor(Qt.PointingHandCursor)
        self._export_drive_btn.setStyleSheet(btn_style)
        self._export_drive_btn.clicked.connect(self._export_to_drive)
        out_row.addWidget(self._export_drive_btn)

        # Follow-Up Chat button — opens in DrilldownPanel
        self._chat_btn = QPushButton("Follow-Up Chat")
        self._chat_btn.setEnabled(False)
        self._chat_btn.setCursor(Qt.PointingHandCursor)
        self._chat_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_GREEN_DARK};
                font-size: 11px; font-weight: 600;
                border: 1px solid {ALMA_GREEN_DARK}; border-radius: 6px; padding: 4px 12px;
            }}
            QPushButton:hover {{ background: {ALMA_CREAM}; }}
            QPushButton:disabled {{ color: {ALMA_TEXT_LIGHT}; border-color: {ALMA_BORDER}; }}
        """)
        self._chat_btn.clicked.connect(self._open_chat_drilldown)
        out_row.addWidget(self._chat_btn)

        ol.addLayout(out_row)

        # Stacked: text output vs animation
        self._output_stack = QStackedWidget()
        # Expanded: fill available space
        self._output_stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._output_stack.setMinimumHeight(450)

        # R1.8: ReportCanvas replaces the bare MarkdownViewer. It exposes
        # set_markdown() / clear() / ticket_clicked for backward compat AND
        # adds set_report() / finding_clicked / metadata_changed for the
        # structured-output rendering path.
        self._output_area = ReportCanvas()
        self._output_stack.addWidget(self._output_area)

        self._gen_animation = GenerationAnimationWidget()
        self._gen_animation.setStyleSheet(f"""
            background: {ALMA_CREAM};
            border: none; border-radius: 8px;
        """)
        self._output_stack.addWidget(self._gen_animation)
        self._output_stack.setCurrentIndex(0)

        ol.addWidget(self._output_stack, 1)
        layout.addWidget(output_card, 1)

        # ── Collapsible: Analyst Reports (Phase 5.5B) ──
        self._analyst_section = CollapsibleSection(
            "Analyst Reports", initially_collapsed=True,
            section_key="ai_reports.analyst_reports",
            show_expand_button=True,
        )
        self._analyst_viewer = MarkdownViewer()
        self._analyst_viewer.setMinimumHeight(100)
        self._analyst_section.add_widget(self._analyst_viewer)
        self._analyst_section.setVisible(False)  # hidden until report runs
        layout.addWidget(self._analyst_section)

        # ── Collapsible: Technical Summary (Phase 5.5B) ──
        self._tech_section = CollapsibleSection(
            "Technical Process Summary", initially_collapsed=True,
            section_key="ai_reports.tech_summary",
            show_expand_button=True,
        )
        self._tech_viewer = MarkdownViewer()
        self._tech_viewer.setMinimumHeight(100)
        self._tech_section.add_widget(self._tech_viewer)
        self._tech_section.setVisible(False)  # hidden until report runs
        layout.addWidget(self._tech_section)

        # Report history (compact summary)
        layout.addSpacing(8)
        self.report_summary = ReportHistorySummary(self.db, "ai_reports")
        self.report_summary.view_all_clicked.connect(self._open_report_history)
        layout.addWidget(self.report_summary)

        # Populate TRC
        self._populate_trc_combo()

        # ── Inline chat input (below report in same scroll) ──
        chat_frame = QFrame()
        chat_frame.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED};
                border: none;
                border-radius: 12px;
            }}
        """)
        chat_layout_inner = QHBoxLayout(chat_frame)
        chat_layout_inner.setContentsMargins(14, 10, 14, 10)
        chat_layout_inner.setSpacing(8)

        self._canvas_chat_input = QComboBox()
        self._canvas_chat_input.setEditable(True)
        self._canvas_chat_input.lineEdit().setPlaceholderText(
            "Ask a follow-up question about this report"
        )
        self._canvas_chat_input.setStyleSheet(f"""
            QComboBox {{
                background: {ALMA_WHITE}; border: none;
                border-radius: 8px; padding: 8px 12px; font-size: 13px;
                color: {ALMA_TEXT_DARK};
            }}
        """)
        chat_layout_inner.addWidget(self._canvas_chat_input, 1)

        chat_send = QPushButton("Send")
        chat_send.setCursor(Qt.PointingHandCursor)
        chat_send.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: white;
                border: none; border-radius: 8px;
                padding: 8px 20px; font-size: 13px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
        """)
        chat_send.clicked.connect(self._on_canvas_chat_send)
        chat_layout_inner.addWidget(chat_send)

        layout.addWidget(chat_frame)

        # Chat widget (used by drilldown and canvas chat)
        self._chat_widget = ReportChatWidget()

        scroll.setWidget(content)

        # ── Build 11.0: QSplitter with Evidence Panel ──
        from PySide6.QtWidgets import QSplitter
        from src.ui.widgets.evidence_panel import EvidencePanel

        canvas_splitter = QSplitter(Qt.Horizontal)
        canvas_splitter.addWidget(scroll)

        self._evidence_panel = EvidencePanel()
        canvas_splitter.addWidget(self._evidence_panel)
        # R1.8 wiring — canvas → evidence panel:
        # - ticket_clicked  → show_ticket  (legacy path, ticket links in body_md)
        # - finding_clicked → show_finding (new, structured cards)
        # - metadata_changed→ show_metadata (new, header chips + accuracy + cost)
        self._output_area.ticket_clicked.connect(self._evidence_panel.show_ticket)
        self._output_area.finding_clicked.connect(self._evidence_panel.show_finding)
        self._output_area.metadata_changed.connect(self._evidence_panel.show_metadata)
        canvas_splitter.setStretchFactor(0, 3)
        canvas_splitter.setStretchFactor(1, 1)
        canvas_splitter.setSizes([700, 300])

        canvas_layout = QVBoxLayout(canvas_widget)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.addWidget(canvas_splitter)

        self._tab_widget.addTab(canvas_widget, "Analysis canvas")

        # ── Tab 1: Report History ──
        from src.ui.pages.ai_reports_history_tab import ReportHistoryTab
        self._history_tab = ReportHistoryTab(self.db)
        self._history_tab.view_report_requested.connect(self._on_view_report)
        self._tab_widget.addTab(self._history_tab, "Report history")

        # ── Tab 2: Manage Prompts ──
        from src.ui.pages.ai_reports_prompts_tab import ManagePromptsTab
        self._prompts_tab = ManagePromptsTab(self.db)
        self._tab_widget.addTab(self._prompts_tab, "Manage prompts")

        # ── Tab 3: A/B Compare ──
        try:
            from src.ui.pages.ab_compare import ABComparePage
            self._ab_tab = ABComparePage(self.db)
            self._tab_widget.addTab(self._ab_tab, "A/B Compare")
        except Exception:
            pass

    # ═══════════════════════════════════════
    #  COMMON HELPERS
    # ═══════════════════════════════════════

    def _check_gemini(self):
        try:
            from src.gemini.gemini_client import GeminiClient
            return GeminiClient().is_available()
        except Exception:
            return False

    def _get_gemini_client(self):
        """Cached bridge client -- avoids repeated 17-20s cold starts."""
        if self._shared_gemini_client is not None:
            return self._shared_gemini_client
        self._shared_gemini_client = _build_gemini_client()
        return self._shared_gemini_client

    def _build_setup_guide(self, layout):
        guide = QFrame()
        guide.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_CREAM}; border: none;
                border-radius: 12px;
            }}
        """)
        apply_card_shadow(guide)
        g = QVBoxLayout(guide)
        g.setContentsMargins(16, 12, 16, 12)
        title = QLabel("Gemini CLI not configured")
        title.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {ALMA_WARNING}; border: none;")
        g.addWidget(title)
        msg = QLabel("Configure Gemini in Settings to enable AI report generation.")
        msg.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID}; border: none;")
        msg.setWordWrap(True)
        g.addWidget(msg)
        layout.addWidget(guide)

    def _populate_trc_combo(self):
        try:
            trcs = self.db.get_all_trc_codes()
            for trc in trcs:
                code = trc if isinstance(trc, str) else trc.get("code", "")
                label = trc if isinstance(trc, str) else trc.get("label", code)
                self._trc_combo.addItem(f"{code}" if code == label else f"{code} -- {label}", code)
        except Exception:
            pass

    def _populate_prompt_combo(self):
        self._prompt_combo.clear()
        try:
            prompts = self.db.get_prompts()
            for p in prompts:
                prefix = "[Canned] " if p["category"] == "canned" else ""
                self._prompt_combo.addItem(f"{prefix}{p['name']}", p["prompt_id"])
            # Add NLP Scan synthesis option
            self._prompt_combo.addItem("[NLP] From NLP Scan", "nlp_scan")
            # Add VOC Root Cause Analysis option
            self._prompt_combo.addItem("[VOC] Root Cause Analysis", "voc_rca")
        except Exception:
            # Fallback if prompts not seeded yet
            self._prompt_combo.addItem("General Trend Analysis", -1)

    def _get_selected_prompt_data(self):
        """Get the prompt dict for the selected combo item."""
        prompt_id = self._prompt_combo.currentData()
        if prompt_id and prompt_id > 0:
            return self.db.get_prompt(prompt_id)
        # Fallback: load from file
        return {
            "prompt_text": _load_prompt_text("general_trend.txt"),
            "system_prompt": "You are a Support Analytics engine.",
        }

    # ═══════════════════════════════════════
    #  REPORT GENERATION
    # ═══════════════════════════════════════

    def _on_generate(self):
        if self._scan_blocked:
            return
        if self._worker and self._worker.isRunning():
            return

        # Check if NLP Scan synthesis is selected
        if self._prompt_combo.currentData() == "nlp_scan":
            self._generate_nlp_synthesis()
            return

        # Check if VOC Root Cause Analysis is selected
        if self._prompt_combo.currentData() == "voc_rca":
            self._generate_voc_report()
            return

        self._generate_btn.setEnabled(False)
        self._generate_btn.setText("  Generating...  ")
        self._copy_btn.setEnabled(False)
        self._save_md_btn.setEnabled(False)
        self._save_html_btn.setEnabled(False)
        self._save_history_btn.setEnabled(False)
        self._export_drive_btn.setEnabled(False)
        self._chat_btn.setEnabled(False)
        self._output_area.clear()
        self._chat_widget.clear()

        self._output_stack.setCurrentIndex(1)
        self._gen_animation.start_animation()

        try:
            gc = _build_gemini_client()
        except Exception as e:
            self._gen_animation.stop_animation()
            self._output_stack.setCurrentIndex(0)
            self._output_area.set_markdown(f"**Gemini client error:** {e}")
            self._generate_btn.setEnabled(True)
            self._generate_btn.setText("  Generate Report  ")
            return

        prompt_data = self._get_selected_prompt_data()
        if not prompt_data or not prompt_data.get("prompt_text"):
            self._gen_animation.stop_animation()
            self._output_stack.setCurrentIndex(0)
            self._output_area.set_markdown("*No prompt selected or prompt text is empty.*")
            self._generate_btn.setEnabled(True)
            self._generate_btn.setText("  Generate Report  ")
            return

        self._chat_widget.set_gemini_client(gc)

        source_id = self._source_selector.selected_source_id() if hasattr(self, '_source_selector') else None
        self._worker = PipelineWorker(
            self.db.db_path, prompt_data,
            self._date_from.date().toString("yyyy-MM-dd"),
            self._date_to.date().toString("yyyy-MM-dd"),
            self._trc_combo.currentData() or "",
            gc,
            source_id=source_id,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_pipeline_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_progress(self, msg):
        self._progress_lbl.setText(msg)
        self._progress_lbl.setVisible(True)
        if hasattr(self, '_gen_animation'):
            self._gen_animation.update_status(msg)

    def _on_error(self, trace):
        self._gen_animation.stop_animation()
        self._output_stack.setCurrentIndex(0)
        self._generate_btn.setEnabled(self._gemini_available)
        self._generate_btn.setText("  Generate Report  ")
        self._progress_lbl.setVisible(False)
        self._output_area.set_markdown(f"## Error\n\n```\n{trace}\n```")

    def _on_finished(self, text, data_block_text):
        """Legacy callback for ReportWorker (kept for NLP synthesis path)."""
        self._gen_animation.stop_animation()
        self._output_stack.setCurrentIndex(0)
        self._generate_btn.setEnabled(self._gemini_available)
        self._generate_btn.setText("  Generate Report  ")
        self._progress_lbl.setVisible(False)
        self._output_area.set_markdown(text)
        self._current_report_text = text
        self._current_data_block = data_block_text
        self._copy_btn.setEnabled(True)
        self._save_md_btn.setEnabled(True)
        self._save_html_btn.setEnabled(True)
        self._save_history_btn.setEnabled(True)
        self._export_drive_btn.setEnabled(True)
        self._chat_btn.setEnabled(True)

        # Set chat context
        self._chat_widget.set_report_context(data_block_text, text)

    def _on_pipeline_finished(self, result):
        """Callback for PipelineWorker (Phase 5.5B multi-phase pipeline)."""
        self._gen_animation.stop_animation()
        self._output_stack.setCurrentIndex(0)
        self._generate_btn.setEnabled(self._gemini_available)
        self._generate_btn.setText("  Generate Report  ")
        self._progress_lbl.setVisible(False)

        report_md = result.get("report_md", "")
        data_block = result.get("data_block", "")
        # R1.7/R1.8: prefer structured Report over raw markdown. The canvas
        # picks the right view (cards vs legacy markdown) and emits the
        # finding_clicked + metadata_changed signals to the evidence panel
        # automatically — no manual show_metadata call needed.
        report_obj = result.get("report")
        if isinstance(report_obj, _StructuredReport) and report_obj.findings:
            self._output_area.set_report(report_obj)
        elif isinstance(report_obj, _StructuredReport):
            # parse failed → carry the legacy markdown forward via raw_markdown
            self._output_area.set_markdown(report_obj.raw_markdown or report_md)
            self._output_area.metadata_changed.emit(
                _meta_from_report(report_obj, fallback_md=report_md)
            )
        else:
            self._output_area.set_markdown(report_md)
        self._current_report = report_obj if isinstance(report_obj, _StructuredReport) else None
        self._current_report_text = report_md
        self._current_data_block = data_block

        # Analyst Reports section
        analyst_md = result.get("analyst_md", "")
        if analyst_md:
            self._analyst_viewer.set_markdown(analyst_md)
            self._analyst_section.setVisible(True)
        else:
            self._analyst_section.setVisible(False)

        # Technical Summary section
        tech_md = result.get("tech_summary_md", "")
        if tech_md:
            self._tech_viewer.set_markdown(tech_md)
            self._tech_section.setVisible(True)
        else:
            self._tech_section.setVisible(False)

        # Enable action buttons
        self._copy_btn.setEnabled(True)
        self._save_md_btn.setEnabled(True)
        self._save_html_btn.setEnabled(True)
        self._save_history_btn.setEnabled(True)
        self._export_drive_btn.setEnabled(True)
        self._chat_btn.setEnabled(True)

        # Set chat context
        self._chat_widget.set_report_context(data_block, report_md)

        # ── Build 11.0: Persist report run to analysis_runs ──
        try:
            from src.services.post_report_persist import persist_report_run
            from src.data.db_manager import DB_PATH
            persist_conn = get_connection(DB_PATH)
            persist_report_run({
                "prompt_template": self._prompt_combo.currentText(),
                "output_text": report_md,
                "trc_filter": self._trc_combo.currentText(),
                "date_start": self._date_from.date().toString("yyyy-MM-dd"),
                "date_end": self._date_to.date().toString("yyyy-MM-dd"),
                "model_used": result.get("model_used"),
                "token_count": result.get("token_count"),
                "cost_usd": result.get("cost_usd"),
                "duration_sec": result.get("duration_sec"),
                "source": "manual",
            }, persist_conn)
            persist_conn.close()
        except Exception as e:
            import logging
            logging.getLogger("alma.ai_reports").warning("Report persist failed: %s", e)

        # Refresh history widgets so new report appears
        self.report_summary.refresh()
        self._history_tab._refresh()

        # R1.8: evidence-panel metadata is now emitted by ReportCanvas
        # via the metadata_changed signal — no manual call needed.

    # ═══════════════════════════════════════════
    #  VOC ROOT CAUSE ANALYSIS
    # ═══════════════════════════════════════════

    def _generate_voc_report(self):
        """Build plan -> show preview -> launch VOC worker if confirmed."""
        from src.data.voc_builder import VOCBuilder

        try:
            gc = self._get_gemini_client()
        except Exception as e:
            self._output_area.set_markdown(f"**Gemini client error:** {e}")
            return

        builder = VOCBuilder(self.db, gc)

        date_start = self._date_from.date().toString("yyyy-MM-dd")
        date_end = self._date_to.date().toString("yyyy-MM-dd")
        trc_filter = self._trc_combo.currentData() or None

        # Run plan (fast, no Gemini calls)
        plan = builder.plan(date_start, date_end, trc_filter)

        if not plan["trc_plans"]:
            self._output_stack.setCurrentIndex(0)
            self._output_area.set_markdown(
                "**No tickets found** in the specified date range.\n\n"
                "Adjust date range or TRC filter and try again."
            )
            return

        # Show plan preview dialog
        if not self._show_voc_plan_preview(plan):
            return  # User cancelled

        # Launch worker
        self._generate_btn.setEnabled(False)
        self._generate_btn.setText("  Generating VOC Report...  ")
        self._copy_btn.setEnabled(False)
        self._save_md_btn.setEnabled(False)
        self._save_html_btn.setEnabled(False)
        self._save_history_btn.setEnabled(False)
        self._export_drive_btn.setEnabled(False)
        self._chat_btn.setEnabled(False)
        self._output_area.clear()
        self._chat_widget.clear()

        self._output_stack.setCurrentIndex(1)
        self._gen_animation.start_animation()
        self._gen_animation.update_status("Planning VOC analysis...")

        self._chat_widget.set_gemini_client(gc)

        self._voc_worker = VOCReportWorker(
            self.db.db_path, date_start, date_end, trc_filter, gc,
        )
        self._voc_worker.progress.connect(self._on_voc_progress)
        self._voc_worker.finished.connect(self._on_voc_finished)
        self._voc_worker.error.connect(self._on_voc_error)
        self._voc_worker.start()

    def _show_voc_plan_preview(self, plan):
        """QMessageBox with TRC count, tickets, est cost/time. Returns True if confirmed."""
        nlp_status = "Yes" if plan["has_nlp_data"] else "No"
        trc_count = len(plan["trc_plans"])

        details = (
            f"<b>VOC Root Cause Analysis Plan</b><br><br>"
            f"<b>TRC Categories:</b> {trc_count}<br>"
            f"<b>Total Tickets:</b> {plan['total_tickets']:,}<br>"
            f"<b>Sampled Tickets:</b> {plan['total_sampled']:,}<br>"
            f"<b>Gemini Calls:</b> {plan['total_gemini_calls']}<br>"
            f"<b>Model:</b> {plan['model']}<br>"
            f"<b>NLP Enrichment:</b> {nlp_status}<br>"
            f"<b>Est. Cost:</b> ${plan['est_cost_usd']:.2f}<br>"
            f"<b>Est. Time:</b> ~{plan['est_time_min']} min<br><br>"
        )

        # Top TRCs summary
        top_trcs = plan["trc_plans"][:10]
        if top_trcs:
            details += "<b>Top TRCs:</b><br>"
            for tp in top_trcs:
                nlp_tag = " [NLP]" if tp["has_nlp"] else ""
                details += (
                    f"&nbsp;&nbsp;{tp['trc']}: {tp['total_tickets']:,} tickets "
                    f"({tp['sampled']:,} sampled){nlp_tag}<br>"
                )
            if trc_count > 10:
                details += f"&nbsp;&nbsp;... and {trc_count - 10} more TRCs<br>"

        reply = QMessageBox.question(
            self,
            "VOC Report Plan",
            details,
            QMessageBox.Ok | QMessageBox.Cancel,
            QMessageBox.Ok,
        )
        return reply == QMessageBox.Ok

    def _on_voc_progress(self, message, percent):
        """Update animation widget with VOC progress."""
        if hasattr(self, '_gen_animation'):
            self._gen_animation.update_status(message)

    def _on_voc_finished(self, result):
        """Display VOC report, enable buttons, set chat context."""
        self._gen_animation.stop_animation()
        self._output_stack.setCurrentIndex(0)
        self._generate_btn.setEnabled(self._gemini_available and not self._scan_blocked)
        self._generate_btn.setText("  Generate Report  ")

        report_text = result.get("report_text", "")
        self._output_area.set_markdown(report_text)
        self._current_report_text = report_text
        self._current_data_block = ""

        # Hide pipeline-specific sections (VOC has its own structure)
        self._analyst_section.setVisible(False)
        self._tech_section.setVisible(False)

        self._copy_btn.setEnabled(True)
        self._save_md_btn.setEnabled(True)
        self._save_html_btn.setEnabled(True)
        self._save_history_btn.setEnabled(True)
        self._export_drive_btn.setEnabled(True)
        self._chat_btn.setEnabled(True)

        # Set chat context (report text serves as context)
        stats = result.get("stats", {})
        context_str = (
            f"VOC Root Cause Analysis | "
            f"{stats.get('total_tickets', 0)} tickets, "
            f"{len(stats.get('trc_plans', []))} TRCs"
        )
        self._chat_widget.set_report_context(context_str, report_text)

        # Live DB access for data-grounded drilldown
        try:
            date_start = self._date_from.date().toString("yyyy-MM-dd")
            date_end = self._date_to.date().toString("yyyy-MM-dd")
            scan = self.db.get_latest_completed_scan()
            scan_id = scan["scan_id"] if scan else None
            self._chat_widget.set_db_context(
                self.db.db_path, date_start, date_end, scan_id=scan_id,
            )
        except Exception:
            pass

        # ── Build 11.0: Persist VOC report run ──
        try:
            from src.services.post_report_persist import persist_report_run
            from src.data.db_manager import DB_PATH
            persist_conn = get_connection(DB_PATH)
            persist_report_run({
                "prompt_template": "voc_root_cause",
                "output_text": report_text,
                "trc_filter": self._trc_combo.currentText(),
                "date_start": self._date_from.date().toString("yyyy-MM-dd"),
                "date_end": self._date_to.date().toString("yyyy-MM-dd"),
                "ticket_count": stats.get("total_tickets"),
                "cost_usd": stats.get("total_cost"),
                "duration_sec": stats.get("duration_sec"),
                "source": "manual",
            }, persist_conn)
            persist_conn.close()
        except Exception as e:
            import logging
            logging.getLogger("alma.ai_reports").warning("VOC report persist failed: %s", e)

    def _on_voc_error(self, trace):
        """Display VOC error, re-enable buttons."""
        self._gen_animation.stop_animation()
        self._output_stack.setCurrentIndex(0)
        self._generate_btn.setEnabled(self._gemini_available and not self._scan_blocked)
        self._generate_btn.setText("  Generate Report  ")
        self._output_area.set_markdown(f"## Error\n\n```\n{trace}\n```")

    def _copy_report(self):
        text = self._current_report_text or self._output_area.toPlainText()
        if text:
            QApplication.clipboard().setText(text)

    def _save_report_md(self):
        text = self._current_report_text or self._output_area.toPlainText()
        if not text:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Report", "", "Markdown (*.md);;Text (*.txt)"
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)

    def _save_report_html(self):
        """Save the current report as a styled .html file."""
        text = self._current_report_text or self._output_area.toPlainText()
        if not text:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Report as HTML", "", "HTML (*.html)"
        )
        if path:
            from src.ui.widgets.markdown_viewer import md_to_html
            html_content = md_to_html(text)
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
            self._save_html_btn.setText("Saved!")
            QTimer.singleShot(2000, lambda: self._save_html_btn.setText("Save .html"))

    def _save_to_history(self):
        if not self._current_report_text:
            return
        prompt_name = self._prompt_combo.currentText()
        chat_history = self._chat_widget.get_chat_history()
        # R1.8: persist the structured Report alongside the legacy markdown so
        # rehydration (Report History → View) can recover findings + accuracy
        # without reparsing the LLM output. See migration 026 for column shape.
        report = getattr(self, "_current_report", None)
        full_results_payload: dict = {"report_text": self._current_report_text}
        findings_json = ""
        pipeline_kind = "single_pass"
        specialist_count = 0
        accuracy_score: float | None = None
        cost_usd = 0.0
        if isinstance(report, _StructuredReport):
            full_results_payload["report_struct"] = report.to_dict()
            findings_json = report.to_json()
            pipeline_kind = report.pipeline_kind
            specialist_count = report.specialist_count
            accuracy_score = report.accuracy_score
            cost_usd = report.cost_usd
        try:
            self.db.save_report(
                page="ai_reports",
                parameters={"prompt": prompt_name,
                             "date_from": self._date_from.date().toString("yyyy-MM-dd"),
                             "date_to": self._date_to.date().toString("yyyy-MM-dd")},
                summary=self._current_report_text[:500],
                full_results=json.dumps(full_results_payload),
                report_type="standard",
                chat_history=chat_history,
                findings_json=findings_json,
                pipeline_kind=pipeline_kind,
                specialist_count=specialist_count,
                accuracy_score=accuracy_score,
                cost_usd=cost_usd,
            )
            self.report_summary.refresh()
            self._save_history_btn.setText("Saved!")
            QTimer.singleShot(2000, lambda: self._save_history_btn.setText("Save to History"))
        except Exception as e:
            QMessageBox.warning(self, "Save Error", str(e))

    def _export_to_drive(self):
        if not self._current_report_text:
            return
        try:
            from src.export.gdrive_export import GoogleDriveExporter
            _cfg = load_settings()
            drive_cfg = _cfg.get("export", {}).get("google_drive", {})
            if not drive_cfg.get("enabled"):
                QMessageBox.information(self, "Not Configured",
                    "Google Drive export is not configured. Set it up in Settings.")
                return
            exporter = GoogleDriveExporter(
                drive_cfg.get("credentials_path", ""),
                drive_cfg.get("folder_id", ""),
            )
            if not exporter.is_configured():
                QMessageBox.warning(self, "Not Configured",
                    "Google Drive credentials or folder ID missing.")
                return
            from datetime import datetime
            filename = f"alma_report_standard_{datetime.now().strftime('%Y%m%d_%H%M')}.md"
            file_id = exporter.upload_report(filename, self._current_report_text)
            self._export_drive_btn.setText("Exported!")
            QTimer.singleShot(2000, lambda: self._export_drive_btn.setText("Export to Drive"))
        except ImportError:
            QMessageBox.information(self, "Missing Dependencies",
                "Install google-api-python-client and google-auth for Drive export.")
        except Exception as e:
            QMessageBox.warning(self, "Export Error", str(e))

    def _on_canvas_chat_send(self):
        """Handle inline chat send from the Analysis Canvas."""
        text = self._canvas_chat_input.currentText().strip()
        if not text:
            return
        self._canvas_chat_input.lineEdit().clear()
        # Open the drilldown chat and send the message
        if self._drilldown and self._current_report_text:
            self._chat_widget.set_report_context(
                self._current_data_block, self._current_report_text
            )
            self._drilldown.show_widget(
                "Follow-Up Chat", "AI Report", self._chat_widget,
            )
            # Send the message into the chat widget
            self._chat_widget.send_message(text)

    def _open_chat_drilldown(self):
        """Open follow-up chat in the DrilldownPanel."""
        if self._drilldown and self._current_report_text:
            self._drilldown.show_widget(
                "Follow-Up Chat",
                "AI Report",
                self._chat_widget,
            )

    def _open_prompt_manager(self):
        from src.ui.dialogs.prompt_editor import PromptEditorDialog
        dlg = PromptEditorDialog(parent=self)
        if dlg.exec():
            data = dlg.get_prompt_data()
            if data.get("name") and data.get("prompt_text"):
                self.db.save_prompt(data)
                self._populate_prompt_combo()

    # ═══════════════════════════════════════
    #  DRILLDOWN REPORT HISTORY
    # ═══════════════════════════════════════

    def set_drilldown_panel(self, panel):
        """Receive the shared DrilldownPanel from main_window."""
        self._drilldown = panel

    def _open_report_history(self):
        """Open the DrilldownPanel with the full report history list."""
        if not self._drilldown:
            return
        reports = self.report_summary.get_reports_for_drilldown()
        count = len(reports)
        self._drilldown.show_reports(
            "AI Reports",
            f"{count} report{'s' if count != 1 else ''}",
            reports,
            detail_callback=self._render_report_detail_html,
            load_callback=self._load_past_report,
        )

    def _render_report_detail_html(self, report_id):
        """Render an AI report as HTML for the DrilldownPanel (Phase 5.5B: markdown)."""
        from src.ui.widgets.markdown_viewer import md_to_html
        report = self.db.get_full_report(report_id)
        if not report:
            return "<p>Report not found.</p>"

        raw = report.get("full_results", "")
        try:
            data = json.loads(raw)
            text = data.get("report_text", raw)
        except (json.JSONDecodeError, TypeError):
            text = raw

        # Convert markdown to styled HTML
        html = md_to_html(text)

        # Append chat history if present
        chat_hist = report.get("chat_history", "")
        if chat_hist:
            try:
                import html as html_mod
                chat_data = json.loads(chat_hist)
                if chat_data:
                    html += "\n<hr>\n<h3>Follow-up Q&amp;A</h3>\n"
                    for entry in chat_data:
                        role = entry.get("role", "")
                        content = html_mod.escape(entry.get("content", ""))
                        if role == "user":
                            html += f"<p style='color: #1B6B4D;'><b>Q:</b> {content}</p>\n"
                        elif role == "assistant":
                            html += f"<p><b>A:</b> {content.replace(chr(10), '<br>')}</p>\n"
            except (json.JSONDecodeError, TypeError):
                pass

        return html

    def _on_view_report(self, run_id):
        """Load a past report from analysis_runs into the canvas."""
        report = self.db.get_report_run(run_id)
        if not report:
            return
        text = report.get("output_text", "")
        self._output_area.set_markdown(text)
        self._current_report_text = text
        self._copy_btn.setEnabled(True)
        self._save_md_btn.setEnabled(True)
        self._save_html_btn.setEnabled(True)
        self._export_drive_btn.setEnabled(True)
        self._chat_btn.setEnabled(True)
        self._tab_widget.setCurrentIndex(0)  # Switch to Analysis Canvas

        # Show metadata in evidence panel
        self._evidence_panel.show_metadata({
            "Pipeline": report.get("prompt_template", ""),
            "Tickets": str(report.get("ticket_count", "")),
            "TRC filter": report.get("trc_filter", "All TRCs"),
            "Generated": report.get("run_date", ""),
            "Duration": f"{report.get('duration_sec', 0) or 0:.1f}s",
        })

    def _load_past_report(self, report_id):
        """Load a past report into the main view."""
        report = self.db.get_full_report(report_id)
        if not report:
            return
        raw = report.get("full_results", "")
        try:
            data = json.loads(raw)
            text = data.get("report_text", raw)
        except (json.JSONDecodeError, TypeError):
            text = raw
        self._output_area.set_markdown(text)
        self._current_report_text = text
        self._copy_btn.setEnabled(True)
        self._save_md_btn.setEnabled(True)
        self._save_html_btn.setEnabled(True)
        self._chat_btn.setEnabled(True)

        # Restore chat history
        chat_hist = report.get("chat_history", "")
        if chat_hist:
            try:
                gc = _build_gemini_client()
                self._chat_widget.set_gemini_client(gc)
            except Exception:
                pass
            self._chat_widget.load_chat_history(chat_hist)

    # ═══════════════════════════════════════
    #  PUBLIC API
    # ═══════════════════════════════════════

    def refresh_gemini_status(self):
        self._gemini_available = self._check_gemini()
        self._generate_btn.setEnabled(self._gemini_available and not self._scan_blocked)

    def set_scan_blocking(self, active):
        """Block/unblock Generate button during NLP scans."""
        self._scan_blocked = active
        if active:
            self._generate_btn.setEnabled(False)
            self._generate_btn.setToolTip("NLP scan in progress — generation disabled")
        else:
            self._generate_btn.setEnabled(self._gemini_available)
            self._generate_btn.setToolTip("")

    def populate_trc_filter(self):
        """Called by main_window when data changes."""
        self._trc_combo.clear()
        self._trc_combo.addItem("All TRCs", "")
        self._populate_trc_combo()

    def sync_date_to_data(self):
        """Set date pickers to match the actual data range in the DB."""
        try:
            from PySide6.QtCore import QDate
            min_d, max_d = self.db.get_date_range()
            if min_d:
                parts = min_d[:10].split("-")
                if len(parts) == 3:
                    self._date_from.setDate(QDate(int(parts[0]), int(parts[1]), int(parts[2])))
            if max_d:
                parts = max_d[:10].split("-")
                if len(parts) == 3:
                    self._date_to.setDate(QDate(int(parts[0]), int(parts[1]), int(parts[2])))
        except Exception:
            pass

    # ═══════════════════════════════════════
    #  NLP SCAN INTEGRATION
    # ═══════════════════════════════════════

    def _generate_nlp_synthesis(self):
        """Generate synthesis report from latest NLP scan."""
        scan = self.db.get_latest_completed_scan()
        if not scan:
            self._output_area.set_markdown(
                "**No completed NLP scans found.**\n\n"
                "Go to NLP Scanner page to run a scan first."
            )
            return

        self._generate_btn.setEnabled(False)
        self._generate_btn.setText("  Synthesizing...  ")
        self._output_area.clear()

        try:
            gc = _build_gemini_client()
            self._chat_widget.set_gemini_client(gc)

            from src.data.nlp_synthesis import NLPSynthesizer
            synth = NLPSynthesizer(self.db, gc)
            result = synth.synthesize_findings(scan["scan_id"])

            self._output_area.set_markdown(result)
            self._current_report_text = result
            self._copy_btn.setEnabled(True)
            self._save_md_btn.setEnabled(True)
            self._save_html_btn.setEnabled(True)
            self._save_history_btn.setEnabled(True)
            self._export_drive_btn.setEnabled(True)
            self._chat_btn.setEnabled(True)

            # Set context for follow-up chat
            self._chat_widget.set_report_context(
                f"NLP Scan synthesis for {scan['date_range_start']} to {scan['date_range_end']}",
                result,
            )

        except Exception as e:
            self._output_area.set_markdown(f"**Synthesis failed:** {e}")
        finally:
            self._generate_btn.setEnabled(True)
            self._generate_btn.setText("  Generate Report  ")

    def load_nlp_finding(self, finding_id, finding_title):
        """
        Called from NLP Scanner 'Deep Dive' -- triggers per-finding
        drilldown via Gemini.
        """
        self._output_area.clear()
        self._output_area.set_markdown(f"*Loading deep dive for: {finding_title}...*")

        try:
            gc = _build_gemini_client()
            self._chat_widget.set_gemini_client(gc)

            from src.data.nlp_synthesis import NLPSynthesizer
            synth = NLPSynthesizer(self.db, gc)
            result = synth.synthesize_single_finding(finding_id)

            self._output_area.set_markdown(result)
            self._current_report_text = result
            self._copy_btn.setEnabled(True)
            self._save_md_btn.setEnabled(True)
            self._save_html_btn.setEnabled(True)
            self._save_history_btn.setEnabled(True)
            self._export_drive_btn.setEnabled(True)
            self._chat_btn.setEnabled(True)

            # Set up chat for follow-up questions
            self._chat_widget.set_report_context(
                f"Deep dive on finding: {finding_title}",
                result,
            )
            # Store finding_id for finding-aware chat
            self._chat_widget._nlp_finding_id = finding_id

        except Exception as e:
            self._output_area.set_markdown(f"**Deep dive failed:** {e}")

    # ═══════════════════════════════════════════
    #  RESOURCE CLEANUP
    # ═══════════════════════════════════════════

    def cleanup(self):
        """Shutdown bridge subprocess and release resources."""
        if self._voc_worker and self._voc_worker.isRunning():
            try:
                self._voc_worker.cancel()
            except Exception:
                pass
        if self._shared_gemini_client is not None:
            try:
                self._shared_gemini_client.shutdown()
            except Exception:
                pass
            self._shared_gemini_client = None

    def closeEvent(self, event):
        """Ensure bridge cleanup on widget close."""
        self.cleanup()
        super().closeEvent(event)
