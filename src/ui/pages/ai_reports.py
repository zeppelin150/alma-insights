"""
Alma Insights — AI Reports Page (Standard Reports)
Single-purpose page for prompt-based report generation via Gemini.
A/B Compare and Smart Reporting are separate sidebar pages.
"""

import json
import time
import yaml
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QFrame, QScrollArea, QTextEdit,
    QFileDialog, QApplication, QSizePolicy, QStackedWidget,
    QMessageBox,
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


# ═══════════════════════════════════════════
#  HELPER: Build Gemini Client from config
# ═══════════════════════════════════════════

def _build_gemini_client():
    """Create GeminiClient from settings.yaml."""
    from src.gemini.gemini_client import GeminiClient
    config_path = Path(__file__).parent.parent.parent.parent / "config" / "settings.yaml"
    gemini_cfg = {}
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        gemini_cfg = cfg.get("gemini", {})
    return GeminiClient(
        cli_path=gemini_cfg.get("cli_path", ""),
        model=gemini_cfg.get("model", "gemini-2.5-flash"),
        temperature=gemini_cfg.get("temperature", 0.2),
        pii_redaction=gemini_cfg.get("pii_redaction", True),
    )


# ═══════════════════════════════════════════
#  AI REPORTS PAGE
# ═══════════════════════════════════════════

class AIReportsPage(QWidget):

    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db = db_manager
        self._worker = None
        self._current_report_text = ""
        self._current_data_block = ""
        self._drilldown = None

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

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        # Header
        header = QLabel("AI Reports")
        header.setObjectName("PageHeader")
        layout.addWidget(header)

        sub = QLabel("Generate AI-powered analysis reports from ticket data via Gemini")
        sub.setObjectName("PageSubheader")
        layout.addWidget(sub)

        # Check Gemini
        self._gemini_available = self._check_gemini()
        if not self._gemini_available:
            self._build_setup_guide(layout)

        # Controls card
        controls = QFrame()
        controls.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED}; border: 1px solid rgba(214, 210, 202, 0.45);
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
                border: 1px solid {ALMA_BORDER}; border-radius: 8px;
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
                border: 1px solid {ALMA_INFO}; border-radius: 8px;
                padding: 8px 16px; font-size: 12px; font-weight: 600;
            }}
            QPushButton:hover {{ background: #E8F0FE; }}
        """)
        manage_btn.clicked.connect(self._open_prompt_manager)
        col.addWidget(manage_btn)
        row1.addLayout(col, 1)
        cl.addLayout(row1)

        # Row 2: TRC filter + Date range
        row2 = QHBoxLayout()
        row2.setSpacing(12)

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
            QFrame {{ background: {ALMA_BG_ELEVATED}; border: 1px solid rgba(214, 210, 202, 0.45); border-radius: 12px; }}
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
                border: 1px solid {ALMA_BORDER}; border-radius: 6px; padding: 4px 12px;
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

        self._output_area = QTextEdit()
        self._output_area.setReadOnly(True)
        self._output_area.setStyleSheet(f"""
            QTextEdit {{
                background: {ALMA_CREAM}; color: {ALMA_TEXT_DARK};
                border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 8px;
                padding: 14px; font-size: 13px; line-height: 1.5;
            }}
        """)
        self._output_stack.addWidget(self._output_area)

        self._gen_animation = GenerationAnimationWidget()
        self._gen_animation.setStyleSheet(f"""
            background: {ALMA_CREAM};
            border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 8px;
        """)
        self._output_stack.addWidget(self._gen_animation)
        self._output_stack.setCurrentIndex(0)

        ol.addWidget(self._output_stack, 1)
        layout.addWidget(output_card, 1)

        # Report history (compact summary)
        layout.addSpacing(8)
        self.report_summary = ReportHistorySummary(self.db, "ai_reports")
        self.report_summary.view_all_clicked.connect(self._open_report_history)
        layout.addWidget(self.report_summary)

        # Populate TRC
        self._populate_trc_combo()

        # Chat widget (not in layout — lives in DrilldownPanel when open)
        self._chat_widget = ReportChatWidget()

        scroll.setWidget(content)
        outer.addWidget(scroll)

    # ═══════════════════════════════════════
    #  COMMON HELPERS
    # ═══════════════════════════════════════

    def _check_gemini(self):
        try:
            from src.gemini.gemini_client import GeminiClient
            return GeminiClient().is_available()
        except Exception:
            return False

    def _build_setup_guide(self, layout):
        guide = QFrame()
        guide.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_CREAM}; border: 1px solid {ALMA_WARNING};
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
        if self._worker and self._worker.isRunning():
            return

        # Check if NLP Scan synthesis is selected
        if self._prompt_combo.currentData() == "nlp_scan":
            self._generate_nlp_synthesis()
            return

        self._generate_btn.setEnabled(False)
        self._generate_btn.setText("  Generating...  ")
        self._copy_btn.setEnabled(False)
        self._save_md_btn.setEnabled(False)
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
            self._output_area.setPlainText(f"Gemini client error: {e}")
            self._generate_btn.setEnabled(True)
            self._generate_btn.setText("  Generate Report  ")
            return

        prompt_data = self._get_selected_prompt_data()
        if not prompt_data or not prompt_data.get("prompt_text"):
            self._gen_animation.stop_animation()
            self._output_stack.setCurrentIndex(0)
            self._output_area.setPlainText("No prompt selected or prompt text is empty.")
            self._generate_btn.setEnabled(True)
            self._generate_btn.setText("  Generate Report  ")
            return

        self._chat_widget.set_gemini_client(gc)

        self._worker = ReportWorker(
            self.db.db_path, prompt_data,
            self._date_from.date().toString("yyyy-MM-dd"),
            self._date_to.date().toString("yyyy-MM-dd"),
            self._trc_combo.currentData() or "",
            gc,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
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
        self._output_area.setPlainText(f"Error:\n\n{trace}")

    def _on_finished(self, text, data_block_text):
        self._gen_animation.stop_animation()
        self._output_stack.setCurrentIndex(0)
        self._generate_btn.setEnabled(self._gemini_available)
        self._generate_btn.setText("  Generate Report  ")
        self._progress_lbl.setVisible(False)
        self._output_area.setPlainText(text)
        self._current_report_text = text
        self._current_data_block = data_block_text
        self._copy_btn.setEnabled(True)
        self._save_md_btn.setEnabled(True)
        self._save_history_btn.setEnabled(True)
        self._export_drive_btn.setEnabled(True)
        self._chat_btn.setEnabled(True)

        # Set chat context
        self._chat_widget.set_report_context(data_block_text, text)

    def _copy_report(self):
        text = self._output_area.toPlainText()
        if text:
            QApplication.clipboard().setText(text)

    def _save_report_md(self):
        text = self._output_area.toPlainText()
        if not text:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Report", "", "Markdown (*.md);;Text (*.txt)"
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)

    def _save_to_history(self):
        if not self._current_report_text:
            return
        prompt_name = self._prompt_combo.currentText()
        chat_history = self._chat_widget.get_chat_history()
        try:
            self.db.save_report(
                page="ai_reports",
                parameters={"prompt": prompt_name,
                             "date_from": self._date_from.date().toString("yyyy-MM-dd"),
                             "date_to": self._date_to.date().toString("yyyy-MM-dd")},
                summary=self._current_report_text[:500],
                full_results=json.dumps({"report_text": self._current_report_text}),
                report_type="standard",
                chat_history=chat_history,
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
            config_path = Path(__file__).parent.parent.parent.parent / "config" / "settings.yaml"
            with open(config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            drive_cfg = cfg.get("export", {}).get("google_drive", {})
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
        """Render an AI report as HTML for the DrilldownPanel."""
        report = self.db.get_full_report(report_id)
        if not report:
            return "<p>Report not found.</p>"

        raw = report.get("full_results", "")
        try:
            data = json.loads(raw)
            text = data.get("report_text", raw)
        except (json.JSONDecodeError, TypeError):
            text = raw

        # Convert plain text to HTML, preserving line breaks
        import html as html_mod
        escaped = html_mod.escape(text)
        paragraphs = escaped.split("\n\n")
        html_parts = ['<div style="font-family: Segoe UI, sans-serif; font-size: 13px;">']
        for p in paragraphs:
            p = p.strip()
            if p:
                if p.startswith("#"):
                    p = p.lstrip("#").strip()
                    html_parts.append(f"<h3 style='margin: 12px 0 6px;'>{p}</h3>")
                else:
                    html_parts.append(f"<p style='margin: 6px 0;'>{p.replace(chr(10), '<br>')}</p>")

        # Chat history
        chat_hist = report.get("chat_history", "")
        if chat_hist:
            try:
                chat_data = json.loads(chat_hist)
                if chat_data:
                    html_parts.append("<h3 style='margin: 16px 0 6px; border-top: 1px solid #E8E5DE; padding-top: 12px;'>Follow-up Q&A</h3>")
                    for entry in chat_data:
                        role = entry.get("role", "")
                        content = html_mod.escape(entry.get("content", ""))
                        if role == "user":
                            html_parts.append(f"<p style='margin: 6px 0; color: #1B6B4D;'><b>Q:</b> {content}</p>")
                        elif role == "assistant":
                            html_parts.append(f"<p style='margin: 6px 0;'><b>A:</b> {content.replace(chr(10), '<br>')}</p>")
            except (json.JSONDecodeError, TypeError):
                pass

        html_parts.append("</div>")
        return "".join(html_parts)

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
        self._output_area.setPlainText(text)
        self._current_report_text = text
        self._copy_btn.setEnabled(True)
        self._save_md_btn.setEnabled(True)
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
        self._generate_btn.setEnabled(self._gemini_available)

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
            self._output_area.setPlainText(
                "No completed NLP scans found.\n\n"
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

            self._output_area.setPlainText(result)
            self._current_report_text = result
            self._copy_btn.setEnabled(True)
            self._save_md_btn.setEnabled(True)
            self._save_history_btn.setEnabled(True)
            self._export_drive_btn.setEnabled(True)
            self._chat_btn.setEnabled(True)

            # Set context for follow-up chat
            self._chat_widget.set_report_context(
                f"NLP Scan synthesis for {scan['date_range_start']} to {scan['date_range_end']}",
                result,
            )

        except Exception as e:
            self._output_area.setPlainText(f"Synthesis failed: {e}")
        finally:
            self._generate_btn.setEnabled(True)
            self._generate_btn.setText("  Generate Report  ")

    def load_nlp_finding(self, finding_id, finding_title):
        """
        Called from NLP Scanner 'Deep Dive' -- triggers per-finding
        drilldown via Gemini.
        """
        self._output_area.clear()
        self._output_area.setPlainText(f"Loading deep dive for: {finding_title}...")

        try:
            gc = _build_gemini_client()
            self._chat_widget.set_gemini_client(gc)

            from src.data.nlp_synthesis import NLPSynthesizer
            synth = NLPSynthesizer(self.db, gc)
            result = synth.synthesize_single_finding(finding_id)

            self._output_area.setPlainText(result)
            self._current_report_text = result
            self._copy_btn.setEnabled(True)
            self._save_md_btn.setEnabled(True)
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
            self._output_area.setPlainText(f"Deep dive failed: {e}")
