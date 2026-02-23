"""
Alma Insights — Smart Reporting Page
Full pipeline execution, scheduling, CLI reference, and run history.
Modern card-based layout with visual polish.
"""

import json
import time
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QFrame, QScrollArea, QSpinBox, QDoubleSpinBox, QCheckBox,
    QTimeEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QMessageBox, QSizePolicy, QApplication,
)
from PySide6.QtCore import Qt, QTime, QThread, QTimer, Signal
from PySide6.QtGui import QFont

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_MID, ALMA_GREEN_LIGHT, ALMA_GREEN_SUBTLE,
    ALMA_WHITE, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_TEXT_ON_DARK, ALMA_BORDER, ALMA_BORDER_LIGHT,
    ALMA_SUCCESS, ALMA_WARNING, ALMA_ERROR, ALMA_INFO,
    ALMA_BG_ELEVATED, ALMA_BG_INSET,
    apply_card_shadow, apply_card_shadow_soft,
)


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

    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db = db_manager
        self._smart_worker = None
        self._schedule_timer = None
        self._gemini_available = self._check_gemini()

        self._build_ui()

    def _check_gemini(self):
        try:
            from src.gemini.gemini_client import GeminiClient
            return GeminiClient().is_available()
        except Exception:
            return False

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

        sub = QLabel("Automated end-to-end analysis pipeline with scheduling and export")
        sub.setObjectName("PageSubheader")
        layout.addWidget(sub)

        # ── Pipeline Config Card ──
        config_card = QFrame()
        config_card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED};
                border: 1px solid rgba(214, 210, 202, 0.45);
                border-radius: 12px;
            }}
        """)
        apply_card_shadow(config_card)
        cc = QVBoxLayout(config_card)
        cc.setContentsMargins(20, 16, 20, 16)
        cc.setSpacing(14)

        config_title = QLabel("Pipeline Configuration")
        config_title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        cc.addWidget(config_title)

        field_style = f"""
            QComboBox, QSpinBox {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER};
                border-radius: 8px; padding: 8px 12px; font-size: 13px;
                color: {ALMA_TEXT_DARK};
            }}
        """
        lbl_style = f"font-size: 11px; font-weight: 600; color: {ALMA_TEXT_MID}; letter-spacing: 0.5px;"

        row = QHBoxLayout()
        row.setSpacing(16)

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("PROMPT")
        lbl.setStyleSheet(lbl_style)
        col.addWidget(lbl)
        self._smart_prompt_combo = QComboBox()
        self._smart_prompt_combo.setStyleSheet(field_style)
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
        lbl.setStyleSheet(lbl_style)
        col.addWidget(lbl)
        self._smart_days_spin = QSpinBox()
        self._smart_days_spin.setRange(7, 365)
        self._smart_days_spin.setValue(30)
        self._smart_days_spin.setStyleSheet(field_style)
        col.addWidget(self._smart_days_spin)
        row.addLayout(col, 1)

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
        lbl.setStyleSheet(lbl_style)
        col.addWidget(lbl)
        self._nlp_budget = QDoubleSpinBox()
        self._nlp_budget.setRange(1.0, 500.0)
        self._nlp_budget.setValue(50.0)
        self._nlp_budget.setPrefix("$")
        self._nlp_budget.setDecimals(2)
        self._nlp_budget.setStyleSheet(field_style)
        col.addWidget(self._nlp_budget)
        nlp_row.addLayout(col, 1)

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("NLP WORKERS")
        lbl.setStyleSheet(lbl_style)
        col.addWidget(lbl)
        self._nlp_workers = QSpinBox()
        self._nlp_workers.setRange(1, 3)
        self._nlp_workers.setValue(1)
        self._nlp_workers.setToolTip("Parallel worker subprocesses (1-3)")
        self._nlp_workers.setStyleSheet(field_style)
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
        layout.addWidget(config_card)

        # ── Two-column: Schedule + CLI ──
        two_col = QHBoxLayout()
        two_col.setSpacing(16)

        # Schedule Card
        sched_card = QFrame()
        sched_card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED};
                border: 1px solid rgba(214, 210, 202, 0.45);
                border-radius: 12px;
            }}
        """)
        apply_card_shadow_soft(sched_card)
        sc = QVBoxLayout(sched_card)
        sc.setContentsMargins(16, 14, 16, 14)
        sc.setSpacing(10)

        sched_header_row = QHBoxLayout()
        sched_icon = QLabel("  ")
        sched_icon.setStyleSheet(f"font-size: 11px; color: {ALMA_INFO}; border: none;")
        sched_header_row.addWidget(sched_icon)
        sched_title = QLabel("Scheduled Runs")
        sched_title.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        sched_header_row.addWidget(sched_title)
        sched_header_row.addStretch()
        sc.addLayout(sched_header_row)

        sched_desc = QLabel(
            "Automatic pipeline runs while the app is running. "
            "Reports are saved to history."
        )
        sched_desc.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none;")
        sched_desc.setWordWrap(True)
        sc.addWidget(sched_desc)

        # Enable toggle
        self._sched_enabled = QCheckBox("Enable scheduled runs")
        self._sched_enabled.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_DARK}; font-weight: 600;")
        self._sched_enabled.toggled.connect(self._on_schedule_toggled)
        sc.addWidget(self._sched_enabled)

        # Interval row
        sched_fields = QHBoxLayout()
        sched_fields.setSpacing(8)

        col = QVBoxLayout()
        col.setSpacing(2)
        lbl = QLabel("Repeat every")
        lbl.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_MID}; border: none;")
        col.addWidget(lbl)
        interval_row = QHBoxLayout()
        interval_row.setSpacing(4)
        self._sched_interval = QSpinBox()
        self._sched_interval.setRange(1, 168)
        self._sched_interval.setValue(24)
        self._sched_interval.setStyleSheet(field_style)
        self._sched_interval.setFixedWidth(65)
        interval_row.addWidget(self._sched_interval)
        self._sched_unit = QComboBox()
        self._sched_unit.addItems(["hours", "minutes"])
        self._sched_unit.setStyleSheet(field_style)
        self._sched_unit.setFixedWidth(85)
        interval_row.addWidget(self._sched_unit)
        col.addLayout(interval_row)
        sched_fields.addLayout(col)

        col = QVBoxLayout()
        col.setSpacing(2)
        lbl = QLabel("First run at")
        lbl.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_MID}; border: none;")
        col.addWidget(lbl)
        self._sched_time = QTimeEdit()
        self._sched_time.setDisplayFormat("hh:mm AP")
        self._sched_time.setTime(QTime(6, 0))
        self._sched_time.setStyleSheet(field_style)
        col.addWidget(self._sched_time)
        sched_fields.addLayout(col)

        sc.addLayout(sched_fields)

        # Auto-export + status
        export_row = QHBoxLayout()
        self._sched_export = QCheckBox("Auto-export to Drive")
        self._sched_export.setToolTip("Automatically export reports to configured destination")
        self._sched_export.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_DARK};")
        export_row.addWidget(self._sched_export)
        export_row.addStretch()

        self._sched_status = QLabel("")
        self._sched_status.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_MID}; border: none;")
        export_row.addWidget(self._sched_status)
        sc.addLayout(export_row)

        two_col.addWidget(sched_card, 1)

        # CLI Reference Card
        cli_card = QFrame()
        cli_card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_INSET};
                border: 1px solid {ALMA_BORDER_LIGHT};
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

        cli_desc = QLabel("Run the pipeline from the command line for automation via Task Scheduler or cron.")
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
        copy_cli.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_GREEN_DARK};
                border: 1px solid {ALMA_GREEN_DARK}; border-radius: 6px;
                padding: 4px 12px; font-size: 11px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_CREAM}; }}
        """)
        copy_cli.clicked.connect(lambda: QApplication.clipboard().setText(
            "python -m src.data.smart_pipeline --export-drive"
        ))
        cl.addWidget(copy_cli)
        cl.addStretch()

        two_col.addWidget(cli_card, 1)
        layout.addLayout(two_col)

        # ── Run History Card ──
        history_card = QFrame()
        history_card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED};
                border: 1px solid rgba(214, 210, 202, 0.45);
                border-radius: 12px;
            }}
        """)
        apply_card_shadow_soft(history_card)
        hc = QVBoxLayout(history_card)
        hc.setContentsMargins(16, 14, 16, 14)
        hc.setSpacing(8)

        hist_header = QHBoxLayout()
        hist_title = QLabel("Run History")
        hist_title.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
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
        self._smart_history_table.setColumnCount(5)
        self._smart_history_table.setHorizontalHeaderLabels(
            ["Started", "Status", "Tickets", "Duration", "Source"]
        )
        self._smart_history_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._smart_history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._smart_history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._smart_history_table.setMinimumHeight(180)
        self._smart_history_table.verticalHeader().setVisible(False)
        self._smart_history_table.verticalHeader().setDefaultSectionSize(32)
        self._smart_history_table.setStyleSheet(f"""
            QTableWidget {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT};
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
        hc.addWidget(self._smart_history_table)
        layout.addWidget(history_card)

        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

        self._refresh_smart_history()

    # ═══════════════════════════════════════
    #  PIPELINE ACTIONS
    # ═══════════════════════════════════════

    def _on_smart_run(self):
        if self._smart_worker and self._smart_worker.isRunning():
            return

        self._smart_run_btn.setEnabled(False)
        self._smart_run_btn.setText("  Running Pipeline...  ")
        self._smart_status.setText("Initializing...")

        prompt_id = self._smart_prompt_combo.currentData()
        prompt_data = self.db.get_prompt(prompt_id) if prompt_id else None

        config = {
            "prompt_data": prompt_data or {
                "prompt_text": _load_prompt_text("general_trend.txt"),
                "system_prompt": "You are a Support Analytics engine.",
            },
            "lookback_days": self._smart_days_spin.value(),
            "trigger_source": "manual",
            "db_path": str(self.db.db_path),
            "nlp_scan": self._nlp_enabled.isChecked(),
            "nlp_budget_cap": self._nlp_budget.value(),
            "nlp_workers": self._nlp_workers.value(),
        }

        self._smart_worker = SmartPipelineWorker(config)
        self._smart_worker.progress.connect(
            lambda msg: self._smart_status.setText(msg)
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

    def _on_smart_error(self, trace):
        self._smart_run_btn.setEnabled(self._gemini_available)
        self._smart_run_btn.setText("  Run Full Pipeline  ")
        self._smart_status.setText("Pipeline failed")
        QMessageBox.warning(self, "Pipeline Error", trace[:500])

    def _refresh_smart_history(self):
        try:
            runs = self.db.get_smart_runs(limit=20)
        except Exception:
            runs = []
        self._smart_history_table.setRowCount(len(runs))
        for row, run in enumerate(runs):
            self._smart_history_table.setItem(row, 0, QTableWidgetItem(
                run.get("started_at", "")[:19]
            ))
            status = run.get("status", "")
            item = QTableWidgetItem(status)
            if status == "success":
                item.setForeground(QFont().defaultFamily() and Qt.darkGreen or Qt.darkGreen)
                from PySide6.QtGui import QColor
                item.setForeground(QColor(ALMA_SUCCESS))
            elif status == "failed":
                from PySide6.QtGui import QColor
                item.setForeground(QColor(ALMA_ERROR))
            self._smart_history_table.setItem(row, 1, item)
            self._smart_history_table.setItem(row, 2, QTableWidgetItem(
                str(run.get("ticket_count", 0))
            ))
            dur = run.get("duration_ms", 0)
            self._smart_history_table.setItem(row, 3, QTableWidgetItem(
                f"{dur/1000:.1f}s" if dur else ""
            ))
            self._smart_history_table.setItem(row, 4, QTableWidgetItem(
                run.get("trigger_source", "")
            ))

    # ═══════════════════════════════════════
    #  SCHEDULE MANAGEMENT
    # ═══════════════════════════════════════

    def _on_schedule_toggled(self, enabled):
        if enabled:
            if not self._gemini_available:
                self._sched_enabled.setChecked(False)
                QMessageBox.warning(
                    self, "Gemini Required",
                    "Gemini must be configured before scheduling reports."
                )
                return
            self._start_schedule_timer()
        else:
            self._stop_schedule_timer()

    def _start_schedule_timer(self):
        self._stop_schedule_timer()

        interval_val = self._sched_interval.value()
        unit = self._sched_unit.currentText()
        if unit == "hours":
            interval_ms = interval_val * 3600 * 1000
        else:
            interval_ms = interval_val * 60 * 1000

        from PySide6.QtCore import QDateTime
        now = QDateTime.currentDateTime()
        first_run = QDateTime(now.date(), self._sched_time.time())
        if first_run <= now:
            first_delay_ms = interval_ms
        else:
            first_delay_ms = now.msecsTo(first_run)

        self._schedule_interval_ms = interval_ms
        self._schedule_timer = QTimer(self)
        self._schedule_timer.setSingleShot(True)
        self._schedule_timer.timeout.connect(self._on_scheduled_run)
        self._schedule_timer.start(first_delay_ms)

        if first_delay_ms < interval_ms:
            next_time = first_run.toString("hh:mm AP")
        else:
            next_dt = now.addMSecs(interval_ms)
            next_time = next_dt.toString("hh:mm AP")
        interval_text = f"{interval_val} {unit}"
        self._sched_status.setText(f"Next: {next_time}  |  Every {interval_text}")
        self._sched_status.setStyleSheet(f"font-size: 11px; color: {ALMA_SUCCESS}; border: none;")

    def _stop_schedule_timer(self):
        if self._schedule_timer:
            self._schedule_timer.stop()
            self._schedule_timer = None
        self._sched_status.setText("")
        self._sched_status.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_MID}; border: none;")

    def _on_scheduled_run(self):
        if not (self._smart_worker and self._smart_worker.isRunning()):
            prompt_id = self._smart_prompt_combo.currentData()
            prompt_data = self.db.get_prompt(prompt_id) if prompt_id else None

            config = {
                "prompt_data": prompt_data or {
                    "prompt_text": _load_prompt_text("general_trend.txt"),
                    "system_prompt": "You are a Support Analytics engine.",
                },
                "lookback_days": self._smart_days_spin.value(),
                "trigger_source": "scheduled",
                "db_path": str(self.db.db_path),
                "auto_export": self._sched_export.isChecked(),
                "nlp_scan": self._nlp_enabled.isChecked(),
                "nlp_budget_cap": self._nlp_budget.value(),
                "nlp_workers": self._nlp_workers.value(),
            }

            self._smart_worker = SmartPipelineWorker(config)
            self._smart_worker.progress.connect(
                lambda msg: self._smart_status.setText(f"[Scheduled] {msg}")
            )
            self._smart_worker.finished.connect(self._on_smart_finished)
            self._smart_worker.error.connect(self._on_smart_error)
            self._smart_worker.start()

        # Re-arm the repeating timer
        if self._sched_enabled.isChecked() and hasattr(self, "_schedule_interval_ms"):
            from PySide6.QtCore import QDateTime
            self._schedule_timer = QTimer(self)
            self._schedule_timer.setSingleShot(True)
            self._schedule_timer.timeout.connect(self._on_scheduled_run)
            self._schedule_timer.start(self._schedule_interval_ms)

            next_dt = QDateTime.currentDateTime().addMSecs(self._schedule_interval_ms)
            next_time = next_dt.toString("hh:mm AP")
            interval_val = self._sched_interval.value()
            unit = self._sched_unit.currentText()
            self._sched_status.setText(f"Next: {next_time}  |  Every {interval_val} {unit}")
            self._sched_status.setStyleSheet(
                f"font-size: 11px; color: {ALMA_SUCCESS}; border: none;"
            )

    # ═══════════════════════════════════════
    #  PUBLIC API
    # ═══════════════════════════════════════

    def refresh_gemini_status(self):
        self._gemini_available = self._check_gemini()
        self._smart_run_btn.setEnabled(self._gemini_available)
