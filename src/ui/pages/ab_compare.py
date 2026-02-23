"""
Alma Insights — A/B Compare Page
Side-by-side dataset comparison with Gemini-powered analysis.
"""

import json
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QFrame, QScrollArea, QTextEdit,
    QFileDialog, QSizePolicy, QMessageBox,
)
from PySide6.QtCore import Qt, QThread, Signal

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_MID, ALMA_GREEN_LIGHT,
    ALMA_WHITE, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_BORDER, ALMA_BORDER_LIGHT,
    ALMA_SUCCESS, ALMA_INFO, ALMA_BG_ELEVATED,
    apply_card_shadow, apply_card_shadow_soft,
)
from src.ui.widgets.chat_widget import ReportChatWidget
from src.ui.widgets.report_history_summary import ReportHistorySummary


def _load_prompt_text(filename):
    path = Path(__file__).parent.parent.parent.parent / "config" / "prompts" / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _build_gemini_client():
    import yaml
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


class ABCompareWorker(QThread):
    """Background thread for A/B dataset comparison."""
    progress = Signal(str)
    finished = Signal(str, str)
    error = Signal(str)

    def __init__(self, db_path, prompt_data, config, gemini_client):
        super().__init__()
        self.db_path = db_path
        self.prompt_data = prompt_data
        self.config = config
        self.gemini_client = gemini_client

    def run(self):
        try:
            from src.data.db_manager import DatabaseManager
            from src.data.ab_analysis import (
                compute_dataset_stats, compare_datasets, build_ab_data_block,
            )
            from src.data.report_builder import (
                replace_prompt_variables, _validate_prompt_before_send,
                format_data_block_for_prompt,
            )

            db = DatabaseManager(self.db_path)
            db.initialize()

            self.progress.emit("Computing Dataset A statistics...")
            stats_a = compute_dataset_stats(
                db, self.config["dataset_a_id"],
                self.config.get("date_start_a", ""),
                self.config.get("date_end_a", ""),
            )

            self.progress.emit("Computing Dataset B statistics...")
            stats_b = compute_dataset_stats(
                db, self.config["dataset_b_id"],
                self.config.get("date_start_b", ""),
                self.config.get("date_end_b", ""),
            )

            self.progress.emit("Running statistical comparisons...")
            comparison = compare_datasets(stats_a, stats_b)

            self.progress.emit("Building comparison data block...")
            block = build_ab_data_block(stats_a, stats_b, comparison)

            prompt_text = self.prompt_data.get("prompt_text", "")
            prompt = replace_prompt_variables(prompt_text, block)

            try:
                _validate_prompt_before_send(prompt)
            except ValueError as ve:
                self.error.emit(f"Prompt validation failed: {ve}")
                db.close()
                return

            self.progress.emit("Sending to Gemini...")
            system_prompt = self.prompt_data.get("system_prompt", "")
            result = self.gemini_client.generate(prompt, system_prompt=system_prompt)

            data_block_text = format_data_block_for_prompt(block)
            db.close()
            self.finished.emit(result, data_block_text)
        except Exception as e:
            import traceback
            self.error.emit(traceback.format_exc())


class ABComparePage(QWidget):

    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db = db_manager
        self._ab_worker = None
        self._drilldown = None
        self._gemini_available = self._check_gemini()

        self._ab_dataset_a_id = None
        self._ab_dataset_b_id = None
        self._current_report_text = ""
        self._current_data_block = ""

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
        header = QLabel("A/B Compare")
        header.setObjectName("PageHeader")
        layout.addWidget(header)

        sub = QLabel(
            "Compare two datasets side by side. Import separate CSVs or use "
            "date-based splits from intervention markers."
        )
        sub.setObjectName("PageSubheader")
        layout.addWidget(sub)

        # Dataset cards row
        cards_row = QHBoxLayout()
        cards_row.setSpacing(16)

        # Dataset A
        card_a = QFrame()
        card_a.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED}; border: 1px solid rgba(214, 210, 202, 0.45);
                border-radius: 12px;
            }}
        """)
        apply_card_shadow(card_a)
        ca = QVBoxLayout(card_a)
        ca.setContentsMargins(16, 14, 16, 14)
        ca.setSpacing(8)

        lbl_a = QLabel("Dataset A")
        lbl_a.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {ALMA_INFO}; border: none;")
        ca.addWidget(lbl_a)
        self._ab_status_a = QLabel("No data -- use current data or import CSV")
        self._ab_status_a.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT}; border: none;")
        self._ab_status_a.setWordWrap(True)
        ca.addWidget(self._ab_status_a)

        btn_row_a = QHBoxLayout()
        btn_row_a.setSpacing(8)
        self._ab_use_current_a = QPushButton("Use Current Data")
        self._ab_use_current_a.setCursor(Qt.PointingHandCursor)
        self._ab_use_current_a.setMinimumHeight(34)
        self._ab_use_current_a.setStyleSheet(self._dataset_btn_style(ALMA_INFO))
        self._ab_use_current_a.clicked.connect(lambda: self._ab_use_current("A"))
        btn_row_a.addWidget(self._ab_use_current_a)
        self._ab_import_a = QPushButton("Import CSV")
        self._ab_import_a.setCursor(Qt.PointingHandCursor)
        self._ab_import_a.setMinimumHeight(34)
        self._ab_import_a.setStyleSheet(self._dataset_btn_style(ALMA_TEXT_MID))
        self._ab_import_a.clicked.connect(lambda: self._ab_import_csv("A"))
        btn_row_a.addWidget(self._ab_import_a)
        ca.addLayout(btn_row_a)
        cards_row.addWidget(card_a)

        # Dataset B
        card_b = QFrame()
        card_b.setStyleSheet(card_a.styleSheet())
        apply_card_shadow(card_b)
        cb = QVBoxLayout(card_b)
        cb.setContentsMargins(16, 14, 16, 14)
        cb.setSpacing(8)

        lbl_b = QLabel("Dataset B")
        lbl_b.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {ALMA_SUCCESS}; border: none;")
        cb.addWidget(lbl_b)
        self._ab_status_b = QLabel("No data -- import CSV")
        self._ab_status_b.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT}; border: none;")
        self._ab_status_b.setWordWrap(True)
        cb.addWidget(self._ab_status_b)

        btn_row_b = QHBoxLayout()
        btn_row_b.setSpacing(8)
        self._ab_import_b = QPushButton("Import CSV")
        self._ab_import_b.setCursor(Qt.PointingHandCursor)
        self._ab_import_b.setMinimumHeight(34)
        self._ab_import_b.setStyleSheet(self._dataset_btn_style(ALMA_TEXT_MID))
        self._ab_import_b.clicked.connect(lambda: self._ab_import_csv("B"))
        btn_row_b.addWidget(self._ab_import_b)
        cb.addLayout(btn_row_b)
        cards_row.addWidget(card_b)

        layout.addLayout(cards_row)

        # Comparison controls card
        comp_card = QFrame()
        comp_card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED}; border: 1px solid rgba(214, 210, 202, 0.45);
                border-radius: 12px;
            }}
        """)
        apply_card_shadow_soft(comp_card)
        comp_layout = QVBoxLayout(comp_card)
        comp_layout.setContentsMargins(16, 14, 16, 14)
        comp_layout.setSpacing(10)

        comp_title = QLabel("Comparison Settings")
        comp_title.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        comp_layout.addWidget(comp_title)

        comp_row = QHBoxLayout()
        comp_row.setSpacing(12)

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("Comparison Prompt")
        lbl.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {ALMA_TEXT_MID};")
        col.addWidget(lbl)
        self._ab_prompt_combo = QComboBox()
        self._ab_prompt_combo.setStyleSheet(f"""
            QComboBox {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER};
                border-radius: 8px; padding: 8px 12px; font-size: 13px;
            }}
        """)
        self._ab_prompt_combo.addItem("A/B Comparison (Default)", "ab_default")
        try:
            for p in self.db.get_prompts():
                self._ab_prompt_combo.addItem(p["name"], p["prompt_id"])
        except Exception:
            pass
        col.addWidget(self._ab_prompt_combo)
        comp_row.addLayout(col, 2)

        col = QVBoxLayout()
        col.setSpacing(4)
        col.addWidget(QLabel(""))
        self._ab_run_btn = QPushButton("  Run Comparison  ")
        self._ab_run_btn.setMinimumHeight(38)
        self._ab_run_btn.setCursor(Qt.PointingHandCursor)
        self._ab_run_btn.setEnabled(False)
        self._ab_run_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_CREAM};
                border: none; border-radius: 8px; padding: 8px 24px;
                font-weight: 600; font-size: 13px;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
            QPushButton:disabled {{ background: {ALMA_BORDER}; color: {ALMA_TEXT_LIGHT}; }}
        """)
        self._ab_run_btn.clicked.connect(self._on_ab_compare)
        col.addWidget(self._ab_run_btn)
        comp_row.addLayout(col, 1)

        comp_layout.addLayout(comp_row)
        layout.addWidget(comp_card)

        # Progress
        self._progress_lbl = QLabel("")
        self._progress_lbl.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID};")
        self._progress_lbl.setVisible(False)
        layout.addWidget(self._progress_lbl)

        # Output card
        output_card = QFrame()
        output_card.setStyleSheet(f"""
            QFrame {{ background: {ALMA_BG_ELEVATED}; border: 1px solid rgba(214, 210, 202, 0.45); border-radius: 12px; }}
        """)
        apply_card_shadow(output_card)
        ol = QVBoxLayout(output_card)
        ol.setContentsMargins(16, 14, 16, 14)
        ol.setSpacing(8)

        out_row = QHBoxLayout()
        out_title = QLabel("Comparison Results")
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

        # Follow-up Chat button
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

        self._ab_output = QTextEdit()
        self._ab_output.setReadOnly(True)
        self._ab_output.setPlaceholderText("Comparison results will appear here...")
        self._ab_output.setStyleSheet(f"""
            QTextEdit {{
                background: {ALMA_CREAM}; color: {ALMA_TEXT_DARK};
                border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 8px;
                padding: 12px; font-size: 13px;
            }}
        """)
        self._ab_output.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._ab_output.setMinimumHeight(400)
        ol.addWidget(self._ab_output, 1)
        layout.addWidget(output_card, 1)

        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

        # Chat widget (not in layout — lives in DrilldownPanel)
        self._chat_widget = ReportChatWidget()

    def _dataset_btn_style(self, color):
        return f"""
            QPushButton {{
                background: transparent; color: {color};
                border: 1px solid {color}; border-radius: 8px;
                padding: 6px 16px; font-size: 12px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_CREAM}; }}
        """

    # ── Actions ──

    def _ab_use_current(self, label):
        if label == "A":
            self._ab_dataset_a_id = 0
            count = self.db.get_ticket_count()
            self._ab_status_a.setText(f"Using current data ({count} tickets)")
        self._ab_run_btn.setEnabled(
            self._ab_dataset_a_id is not None and
            self._ab_dataset_b_id is not None and
            self._gemini_available
        )

    def _ab_import_csv(self, label):
        path, _ = QFileDialog.getOpenFileName(
            self, f"Import CSV for Dataset {label}", "", "CSV Files (*.csv)"
        )
        if not path:
            return
        try:
            from src.data.csv_ingestion import ingest_csv
            ds_name = Path(path).stem
            ds_id = self.db.create_dataset({
                "name": ds_name, "label": label, "file_path": path,
            })
            stats = ingest_csv(self.db, path, dataset_id=ds_id)
            count = stats.get("tickets_created", 0)
            if label == "A":
                self._ab_dataset_a_id = ds_id
                self._ab_status_a.setText(f"{ds_name}: {count} tickets")
            else:
                self._ab_dataset_b_id = ds_id
                self._ab_status_b.setText(f"{ds_name}: {count} tickets")
        except Exception as e:
            QMessageBox.warning(self, "Import Error", str(e))
            return

        self._ab_run_btn.setEnabled(
            self._ab_dataset_a_id is not None and
            self._ab_dataset_b_id is not None and
            self._gemini_available
        )

    def _on_ab_compare(self):
        if self._ab_worker and self._ab_worker.isRunning():
            return
        self._ab_run_btn.setEnabled(False)
        self._ab_run_btn.setText("  Running...  ")
        self._ab_output.clear()
        self._chat_btn.setEnabled(False)
        self._copy_btn.setEnabled(False)
        self._progress_lbl.setVisible(True)

        try:
            gc = _build_gemini_client()
        except Exception as e:
            self._ab_output.setPlainText(f"Gemini error: {e}")
            self._ab_run_btn.setEnabled(True)
            self._ab_run_btn.setText("  Run Comparison  ")
            self._progress_lbl.setVisible(False)
            return

        prompt_key = self._ab_prompt_combo.currentData()
        if prompt_key == "ab_default":
            prompt_data = {
                "prompt_text": _load_prompt_text("ab_comparison.txt"),
                "system_prompt": "You are a Support Analytics engine comparing two datasets.",
            }
        else:
            prompt_id = self._ab_prompt_combo.currentData()
            prompt_data = self.db.get_prompt(prompt_id) if prompt_id else None
            if not prompt_data:
                prompt_data = {
                    "prompt_text": _load_prompt_text("ab_comparison.txt"),
                    "system_prompt": "You are a Support Analytics engine comparing two datasets.",
                }

        self._chat_widget.set_gemini_client(gc)
        self._chat_widget.clear()

        self._ab_worker = ABCompareWorker(
            self.db.db_path, prompt_data,
            {
                "dataset_a_id": self._ab_dataset_a_id,
                "dataset_b_id": self._ab_dataset_b_id,
            },
            gc,
        )
        self._ab_worker.progress.connect(self._on_progress)
        self._ab_worker.finished.connect(self._on_ab_finished)
        self._ab_worker.error.connect(self._on_ab_error)
        self._ab_worker.start()

    def _on_progress(self, msg):
        self._progress_lbl.setText(msg)

    def _on_ab_error(self, trace):
        self._ab_output.setPlainText(f"Error:\n{trace}")
        self._ab_run_btn.setEnabled(True)
        self._ab_run_btn.setText("  Run Comparison  ")
        self._progress_lbl.setVisible(False)

    def _on_ab_finished(self, text, data_block_text):
        self._ab_output.setPlainText(text)
        self._current_report_text = text
        self._current_data_block = data_block_text
        self._ab_run_btn.setEnabled(True)
        self._ab_run_btn.setText("  Run Comparison  ")
        self._progress_lbl.setVisible(False)
        self._copy_btn.setEnabled(True)
        self._chat_btn.setEnabled(True)
        self._chat_widget.set_report_context(data_block_text, text)

    def _copy_report(self):
        from PySide6.QtWidgets import QApplication
        text = self._ab_output.toPlainText()
        if text:
            QApplication.clipboard().setText(text)

    def _open_chat_drilldown(self):
        if self._drilldown and self._current_report_text:
            self._drilldown.show_widget(
                "Follow-Up Chat",
                "A/B Comparison",
                self._chat_widget,
            )

    # ── Public API ──

    def set_drilldown_panel(self, panel):
        self._drilldown = panel

    def refresh_gemini_status(self):
        self._gemini_available = self._check_gemini()
