"""
Alma Insights — Conversation Search & Viewer
Search, filter, and read rebuilt ticket conversation threads.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QFrame, QAbstractItemView,
    QFileDialog, QMessageBox, QProgressDialog, QApplication
)
from PySide6.QtCore import Qt, QDate, Signal
from PySide6.QtGui import QFont
from src.ui.theme import *
from src.ui.widgets.date_picker import ModernDatePicker


# Sentinel values for dataset combo
_CSV_IMPORT_KEY = "__csv_import__"
_API_DISABLED_KEY = "__api_disabled__"


class ConversationSearchPage(QWidget):
    """Full conversation search interface with filters, results table, and thread viewer."""

    # Emitted when a CSV file is imported successfully
    data_loaded = Signal(dict)

    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db = db_manager
        self._current_results = []
        self._api_enabled = False
        self._datasets = []
        self._pat = ""
        self._is_test_mode = True
        self._debug_mode = False
        self._build_ui()
        self._connect_signals()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 16)
        layout.setSpacing(0)

        # ── Page Header Row (title left, data controls right) ──
        header_row = QHBoxLayout()
        header_row.setSpacing(12)

        # Left: title + subtitle
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        header = QLabel("Conversation Search")
        header.setObjectName("PageHeader")
        title_col.addWidget(header)

        subheader = QLabel("Search and browse rebuilt ticket conversations by keyword, TRC, date, or CSAT score")
        subheader.setObjectName("PageSubheader")
        title_col.addWidget(subheader)
        header_row.addLayout(title_col, 1)

        # Right: data source badge + dataset selector + pull button
        controls_row = QHBoxLayout()
        controls_row.setSpacing(8)
        controls_row.setAlignment(Qt.AlignBottom)

        # Data source badge (the "black box" from your sketch)
        self.data_source_badge = QLabel("Testing data")
        self.data_source_badge.setStyleSheet(f"""
            background: {ALMA_GREEN_DARK}; color: {ALMA_TEXT_ON_DARK};
            border-radius: 6px; padding: 6px 14px;
            font-size: 12px; font-weight: 600;
        """)
        controls_row.addWidget(self.data_source_badge)

        # Dataset selector dropdown
        self.dataset_combo = QComboBox()
        self.dataset_combo.setMinimumWidth(200)
        self.dataset_combo.setStyleSheet(f"""
            QComboBox {{
                padding: 6px 12px; font-size: 12px;
            }}
        """)
        self._rebuild_dataset_combo()
        controls_row.addWidget(self.dataset_combo)

        # Pull / Import button (the "green box" from your sketch)
        self.pull_btn = QPushButton("  Pull Data  ")
        self.pull_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_LIGHT}; color: {ALMA_TEXT_ON_DARK};
                border: none; border-radius: 6px; padding: 7px 18px;
                font-size: 12px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_SUBTLE}; }}
        """)
        self.pull_btn.setCursor(Qt.PointingHandCursor)
        controls_row.addWidget(self.pull_btn)

        header_row.addLayout(controls_row)
        layout.addLayout(header_row)
        layout.addSpacing(20)

        # ── Filter Bar ──
        filter_card = QFrame()
        filter_card.setObjectName("ConvFilterCard")
        filter_card.setStyleSheet(f"""
            #ConvFilterCard {{
                background: {ALMA_BG_ELEVATED}; border: 1px solid rgba(214, 210, 202, 0.45);
                border-radius: 12px;
            }}
        """)
        apply_card_shadow(filter_card)
        filter_layout = QVBoxLayout(filter_card)
        filter_layout.setContentsMargins(16, 16, 16, 16)
        filter_layout.setSpacing(12)

        # Row 1: keyword + TRC
        row1 = QHBoxLayout()
        row1.setSpacing(12)

        # Keyword search
        kw_label = QLabel("Keyword")
        kw_label.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {ALMA_TEXT_MID}; letter-spacing: 0.5px;")
        self.keyword_input = QLineEdit()
        self.keyword_input.setPlaceholderText("Search ticket text, subjects, TRC labels...")
        self.keyword_input.setMinimumWidth(280)

        kw_col = QVBoxLayout()
        kw_col.setSpacing(4)
        kw_col.addWidget(kw_label)
        kw_col.addWidget(self.keyword_input)
        row1.addLayout(kw_col, 3)

        # TRC filter
        trc_label = QLabel("TRC Code")
        trc_label.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {ALMA_TEXT_MID}; letter-spacing: 0.5px;")
        self.trc_combo = QComboBox()
        self.trc_combo.addItem("All TRCs", "")
        self.trc_combo.setMinimumWidth(200)

        trc_col = QVBoxLayout()
        trc_col.setSpacing(4)
        trc_col.addWidget(trc_label)
        trc_col.addWidget(self.trc_combo)
        row1.addLayout(trc_col, 2)

        filter_layout.addLayout(row1)

        # Row 2: dates + CSAT + buttons
        row2 = QHBoxLayout()
        row2.setSpacing(12)

        # Date from
        df_label = QLabel("From")
        df_label.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {ALMA_TEXT_MID}; letter-spacing: 0.5px;")
        self.date_from = ModernDatePicker()
        self.date_from.setDate(QDate(2020, 1, 1))

        df_col = QVBoxLayout()
        df_col.setSpacing(4)
        df_col.addWidget(df_label)
        df_col.addWidget(self.date_from)
        row2.addLayout(df_col, 1)

        # Date to
        dt_label = QLabel("To")
        dt_label.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {ALMA_TEXT_MID}; letter-spacing: 0.5px;")
        self.date_to = ModernDatePicker()
        self.date_to.setDate(QDate.currentDate())

        dt_col = QVBoxLayout()
        dt_col.setSpacing(4)
        dt_col.addWidget(dt_label)
        dt_col.addWidget(self.date_to)
        row2.addLayout(dt_col, 1)

        # CSAT filter
        csat_label = QLabel("CSAT")
        csat_label.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {ALMA_TEXT_MID}; letter-spacing: 0.5px;")
        self.csat_combo = QComboBox()
        self.csat_combo.addItem("Any", "")
        self.csat_combo.addItem("1 — Bad", "1")
        self.csat_combo.addItem("2 — Poor", "2")
        self.csat_combo.addItem("3 — OK", "3")
        self.csat_combo.addItem("4 — Good", "4")
        self.csat_combo.addItem("5 — Great", "5")

        cs_col = QVBoxLayout()
        cs_col.setSpacing(4)
        cs_col.addWidget(csat_label)
        cs_col.addWidget(self.csat_combo)
        row2.addLayout(cs_col, 1)

        # Search button
        btn_col = QVBoxLayout()
        btn_col.setSpacing(4)
        btn_col.addWidget(QLabel(""))  # spacer for alignment
        self.search_btn = QPushButton("  Search  ")
        self.search_btn.setMinimumHeight(36)
        btn_col.addWidget(self.search_btn)
        row2.addLayout(btn_col, 1)

        # Clear button
        clr_col = QVBoxLayout()
        clr_col.setSpacing(4)
        clr_col.addWidget(QLabel(""))
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setObjectName("SecondaryButton")
        self.clear_btn.setMinimumHeight(36)
        clr_col.addWidget(self.clear_btn)
        row2.addLayout(clr_col, 1)

        filter_layout.addLayout(row2)
        layout.addWidget(filter_card)
        layout.addSpacing(16)

        # ── Results Count ──
        self.results_label = QLabel("")
        self.results_label.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT}; padding: 0 4px;")
        layout.addWidget(self.results_label)
        layout.addSpacing(8)

        # ── Results Table (full width) ──
        self.results_table = QTableWidget()
        self.results_table.setColumnCount(6)
        self.results_table.setHorizontalHeaderLabels([
            "Ticket ID", "Subject", "TRC", "Status", "CSAT", "Date"
        ])
        self.results_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.results_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.results_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.results_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.results_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.results_table.setColumnWidth(0, 90)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.results_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.results_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.results_table.setAlternatingRowColors(True)
        self.results_table.setStyleSheet(f"""
            QTableWidget {{
                alternate-background-color: rgba(3,40,27,0.02);
            }}
            QTableWidget::item:selected {{
                background: rgba(20, 87, 63, 0.10);
                color: {ALMA_TEXT_DARK};
            }}
        """)
        layout.addWidget(self.results_table, 1)

    def _connect_signals(self):
        self.search_btn.clicked.connect(self.run_search)
        self.clear_btn.clicked.connect(self.clear_filters)
        self.keyword_input.returnPressed.connect(self.run_search)
        self.results_table.currentCellChanged.connect(self._on_row_selected)
        self.pull_btn.clicked.connect(self._on_pull_clicked)

    # ── Drill-down Panel ──

    def set_drilldown_panel(self, panel):
        """Accept a reference to the shared DrilldownPanel from MainWindow."""
        self._drilldown = panel

    # ── Dataset Selector Management ──

    def _rebuild_dataset_combo(self):
        """Rebuild the dataset dropdown based on current state."""
        self.dataset_combo.clear()

        # Always available: CSV import
        self.dataset_combo.addItem("📁  Import CSV…", _CSV_IMPORT_KEY)

        if self._api_enabled and self._datasets:
            # Add configured datasets
            for ds in self._datasets:
                self.dataset_combo.addItem(f"☁  {ds['title']}", ds.get("url", ""))
        else:
            # Greyed-out placeholder
            self.dataset_combo.addItem("☁  API Data Pull", _API_DISABLED_KEY)
            # Disable that item
            model = self.dataset_combo.model()
            item = model.item(self.dataset_combo.count() - 1)
            if item:
                item.setEnabled(False)

    def update_datasets(self, datasets):
        """Called by MainWindow when settings datasets change."""
        self._datasets = datasets
        self._rebuild_dataset_combo()

    def update_api_state(self, enabled):
        """Called by MainWindow when API toggle changes."""
        self._api_enabled = enabled
        self._rebuild_dataset_combo()

    def update_pat(self, pat):
        """Called by MainWindow when PAT changes."""
        self._pat = pat

    def update_test_mode(self, is_test):
        """Called by MainWindow when test data toggle changes."""
        self._is_test_mode = is_test

    def update_debug_mode(self, enabled):
        """Called by MainWindow when debug canary toggle changes."""
        self._debug_mode = enabled

    def set_data_source_label(self, text):
        """Update the data source badge text and color."""
        if "test" in text.lower():
            self.data_source_badge.setStyleSheet(f"""
                background: {ALMA_GREEN_DARK}; color: {ALMA_TEXT_ON_DARK};
                border-radius: 6px; padding: 6px 14px;
                font-size: 12px; font-weight: 600;
            """)
        elif "csv" in text.lower():
            self.data_source_badge.setStyleSheet(f"""
                background: {ALMA_INFO}; color: white;
                border-radius: 6px; padding: 6px 14px;
                font-size: 12px; font-weight: 600;
            """)
        else:
            self.data_source_badge.setStyleSheet(f"""
                background: {ALMA_GREEN_LIGHT}; color: {ALMA_TEXT_ON_DARK};
                border-radius: 6px; padding: 6px 14px;
                font-size: 12px; font-weight: 600;
            """)
        self.data_source_badge.setText(text)

    def filter_by_ticket_ids(self, ticket_ids):
        """Filter conversation list to show only specific ticket IDs.
        Called from NLP Scanner 'View Tickets' button."""
        if not ticket_ids:
            return
        # Clear existing search and show these tickets
        try:
            self.search_box.clear()
            # Build a results list for these specific tickets
            results = []
            for tid in ticket_ids[:200]:  # Cap at 200
                conv = self.db.get_conversation(tid)
                if conv:
                    results.append(dict(conv))
            if results:
                self._display_results(results)
                self.set_data_source_label(f"NLP Finding ({len(results)} tickets)")
        except Exception:
            pass

    def _on_pull_clicked(self):
        """Handle Pull button click based on selected dataset."""
        selected_key = self.dataset_combo.currentData()
        selected_text = self.dataset_combo.currentText()

        # ── Debug canary (only when toggled ON in Settings) ──
        if self._debug_mode:
            QMessageBox.information(
                self, "🐤 Debug Canary",
                f"Selected: {selected_text}\n"
                f"Key: {selected_key!r}\n"
                f"Test mode: {self._is_test_mode}\n"
                f"PAT length: {len(self._pat)}\n"
                f"API enabled: {self._api_enabled}\n"
                f"Datasets: {len(self._datasets)}\n"
                f"Debug mode: {self._debug_mode}"
            )

        try:
            if selected_key == _CSV_IMPORT_KEY:
                self._import_csv()
            elif selected_key == _API_DISABLED_KEY:
                QMessageBox.information(
                    self, "API Not Enabled",
                    "Enable API Data Pull in Settings and configure at least one dataset."
                )
            elif selected_key:
                self._launch_ingestion(chart_url=selected_key)
            else:
                QMessageBox.warning(
                    self, "No Dataset Selected",
                    "Select a dataset from the dropdown, or configure one in Settings."
                )
        except Exception as e:
            import traceback
            QMessageBox.critical(
                self, "Error",
                f"Pull failed:\n\n{traceback.format_exc()}"
            )

    def _launch_ingestion(self, chart_url=""):
        """Open the ingestion dialog for a Lightdash dataset."""
        try:
            from src.ui.dialogs.ingestion_dialog import IngestionDialog

            # Get PAT and test mode from settings (set by MainWindow)
            pat = self._pat or ""
            is_test = self._is_test_mode

            dlg = IngestionDialog(
                db=self.db,
                pat=pat,
                chart_url=chart_url,
                is_test_mode=is_test,
                parent=self,
            )
            dlg.ingestion_complete.connect(self._on_ingestion_complete)
            dlg.exec()
        except Exception as e:
            import traceback
            QMessageBox.critical(
                self, "Ingestion Dialog Error",
                f"Failed to open ingestion dialog:\n\n{traceback.format_exc()}"
            )

    def _on_ingestion_complete(self, stats):
        """Handle completed ingestion from dialog."""
        self.populate_trc_filter()
        self._sync_date_filters_to_data()
        self.run_search()

        convos = stats.get("conversations", 0)
        self.set_data_source_label(f"Lightdash: {convos:,} convos")
        self.data_loaded.emit(stats)

    def _import_csv(self):
        """Open file picker and ingest a Lightdash CSV export."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Lightdash CSV Export",
            "",
            "CSV Files (*.csv);;All Files (*)",
        )
        if not file_path:
            return

        fname = file_path.split("/")[-1].split("\\")[-1]
        self._pending_csv_fname = fname

        main_win = self.window()
        if hasattr(main_win, '_job_queue'):
            self._import_csv_via_queue(file_path, main_win)
        else:
            self._import_csv_legacy(file_path)

    def _import_csv_via_queue(self, file_path, main_win):
        """Route CSV import through the unified job queue."""
        from src.data.job_queue import JobDescriptor, CallableWorker
        db_path = self.db.db_path
        queue = main_win._job_queue

        def create_worker():
            def do_import():
                from src.data.db_manager import DatabaseManager
                from src.data.csv_ingestion import ingest_csv
                db = DatabaseManager(db_path)
                db.initialize()
                try:
                    return ingest_csv(
                        file_path, db,
                        progress_callback=lambda msg, pct:
                            queue.job_progress.emit("csv_import", msg, pct or -1)
                    )
                finally:
                    db.close()

            worker = CallableWorker(do_import)
            worker.finished_result.connect(self._on_csv_import_done)
            worker.error.connect(self._on_csv_import_error)
            return worker

        job = JobDescriptor(
            job_id="csv_import",
            name="CSV Import",
            description=f"Importing {self._pending_csv_fname}",
            create_worker=create_worker,
        )
        main_win._job_queue.submit(job)

    def _on_csv_import_done(self, stats):
        """Handle CSV import completion from the job queue."""
        # Reinitialize DB since the import ran on a separate connection
        self.db.initialize()

        # Update UI
        self.populate_trc_filter()
        self._sync_date_filters_to_data()
        self.run_search()

        fname = getattr(self, '_pending_csv_fname', 'CSV')
        self.set_data_source_label(f"CSV: {fname}")

        # Store stats for deferred summary (shown after all jobs complete)
        self._last_import_stats = stats

        # Notify parent → triggers auto-analysis queue
        self.data_loaded.emit(stats)

    def _on_csv_import_error(self, error_text):
        """Handle CSV import failure — deferred to avoid blocking job queue."""
        self._pending_import_error = error_text

    def show_deferred_import_summary(self):
        """Show import summary after all queued jobs have completed.

        Called by MainWindow when the job queue empties, so it doesn't
        block the overlay or subsequent jobs.
        """
        # Show error if import failed
        if hasattr(self, '_pending_import_error') and self._pending_import_error:
            err = self._pending_import_error
            self._pending_import_error = None
            QMessageBox.critical(
                self, "Import Error",
                f"Failed to import CSV:\n\n{err}"
            )
            return

        # Show success summary
        stats = getattr(self, '_last_import_stats', None)
        if stats:
            self._last_import_stats = None
            QMessageBox.information(
                self, "Import Complete",
                f"Successfully imported data from CSV.\n\n"
                f"Rows: {stats['total_csv_rows']:,}  |  "
                f"Conversations: {stats['tickets_created']:,}  |  "
                f"Comments: {stats['comments_stored']:,}"
            )

    def _import_csv_legacy(self, file_path):
        """Legacy synchronous CSV import (fallback if no job queue)."""
        progress = QProgressDialog("Importing CSV...", "Cancel", 0, 100, self)
        progress.setWindowTitle("Importing Data")
        progress.setMinimumDuration(0)
        progress.setWindowModality(Qt.WindowModal)
        progress.setValue(0)

        def on_progress(msg, pct):
            if progress.wasCanceled():
                return
            progress.setLabelText(msg)
            if pct is not None:
                progress.setValue(pct)
            QApplication.processEvents()

        try:
            from src.data.csv_ingestion import ingest_csv
            stats = ingest_csv(file_path, self.db, progress_callback=on_progress)
            progress.setValue(100)
            progress.close()

            self.populate_trc_filter()
            self._sync_date_filters_to_data()
            self.run_search()

            fname = file_path.split("/")[-1].split("\\")[-1]
            self.set_data_source_label(f"CSV: {fname}")
            self.data_loaded.emit(stats)

            QMessageBox.information(
                self, "Import Complete",
                f"Successfully imported data from CSV.\n\n"
                f"• CSV rows processed: {stats['total_csv_rows']:,}\n"
                f"• Conversations rebuilt: {stats['tickets_created']:,}\n"
                f"• Comments stored: {stats['comments_stored']:,}\n"
                f"• Fields mapped: {', '.join(stats['mapped_fields'])}"
                + (f"\n• Unmapped columns: {', '.join(stats['unmapped_headers'][:5])}" if stats['unmapped_headers'] else "")
            )

        except Exception as e:
            progress.close()
            QMessageBox.critical(
                self, "Import Error",
                f"Failed to import CSV:\n\n{str(e)}"
            )

    def _sync_date_filters_to_data(self):
        """Set date filter range to match the data in the database."""
        try:
            min_date, max_date = self.db.get_date_range()
            if min_date:
                qd = QDate.fromString(min_date[:10], "yyyy-MM-dd")
                if qd.isValid():
                    self.date_from.setDate(qd)
            if max_date:
                qd = QDate.fromString(max_date[:10], "yyyy-MM-dd")
                if qd.isValid():
                    self.date_to.setDate(qd)
        except Exception:
            pass

    def populate_trc_filter(self):
        """Load TRC codes from database into the filter dropdown."""
        self.trc_combo.clear()
        self.trc_combo.addItem("All TRCs", "")
        trcs = self.db.get_trc_codes()
        for trc in trcs:
            self.trc_combo.addItem(f"{trc['code']} — {trc['label']}", trc["code"])

    def run_search(self):
        """Execute search with current filters."""
        keyword = self.keyword_input.text().strip()
        trc_code = self.trc_combo.currentData() or ""
        date_from = self.date_from.date().toString("yyyy-MM-dd")
        date_to = self.date_to.date().toString("yyyy-MM-dd") + "T23:59:59"

        csat_val = self.csat_combo.currentData()
        csat_min = int(csat_val) if csat_val else None
        csat_max = int(csat_val) if csat_val else None

        results = self.db.search_conversations(
            keyword=keyword, trc_code=trc_code,
            date_from=date_from, date_to=date_to,
            csat_min=csat_min, csat_max=csat_max,
        )

        self._current_results = results
        self._populate_table(results)

        count = len(results)
        self.results_label.setText(
            f"{count:,} conversation{'s' if count != 1 else ''} found"
            + (" (showing first 1,000)" if count >= 1000 else "")
        )

        # Log the search
        self.db.log_analysis(
            "conversation_search",
            {"keyword": keyword, "trc": trc_code, "date_from": date_from, "date_to": date_to},
            ticket_count=count,
        )

    def clear_filters(self):
        self.keyword_input.clear()
        self.trc_combo.setCurrentIndex(0)
        self.date_from.setDate(QDate(2020, 1, 1))
        self.date_to.setDate(QDate.currentDate())
        self.csat_combo.setCurrentIndex(0)
        self.results_table.setRowCount(0)
        self.results_label.setText("")
        self._current_results = []
        if hasattr(self, '_drilldown') and self._drilldown.is_open():
            self._drilldown.close_panel()

    def _populate_table(self, results):
        self.results_table.setRowCount(0)
        self.results_table.setRowCount(len(results))

        for i, r in enumerate(results):
            # Ticket ID
            id_item = QTableWidgetItem(r["ticket_id"])
            id_item.setFont(QFont("Consolas", 11))
            self.results_table.setItem(i, 0, id_item)

            # Subject
            self.results_table.setItem(i, 1, QTableWidgetItem(r.get("subject", "")))

            # TRC
            trc_item = QTableWidgetItem(r.get("trc_code", ""))
            trc_item.setToolTip(r.get("trc_label", ""))
            self.results_table.setItem(i, 2, trc_item)

            # Status
            status = r.get("status", "")
            status_item = QTableWidgetItem(status.title())
            if status == "solved":
                status_item.setForeground(Qt.GlobalColor.darkGreen)
            elif status == "open":
                status_item.setForeground(Qt.GlobalColor.darkRed)
            self.results_table.setItem(i, 3, status_item)

            # CSAT
            csat = r.get("csat_score")
            csat_text = str(int(csat)) if csat else "—"
            self.results_table.setItem(i, 4, QTableWidgetItem(csat_text))

            # Date
            date_str = r.get("created_at", "")[:10]
            self.results_table.setItem(i, 5, QTableWidgetItem(date_str))

    def _on_row_selected(self, row, col, prev_row, prev_col):
        if row < 0 or row >= len(self._current_results):
            return
        if not hasattr(self, '_drilldown'):
            return

        conv = self._current_results[row]
        # Open directly to thread view (Level 2) with prev/next across results
        self._drilldown.show_conversation(conv, self._current_results, row)
