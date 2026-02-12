"""
Alma Insights — TRC Analytics Dashboard
Volume charts, resolution times, CSAT heatmaps, and filterable metrics table.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QDateEdit, QFrame, QScrollArea, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QProgressDialog,
    QApplication, QSizePolicy,
)
from PySide6.QtCore import Qt, QDate, QThread, Signal
from PySide6.QtGui import QFont, QColor

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_GREEN_SUBTLE,
    ALMA_WHITE, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_TEXT_ON_DARK, ALMA_BORDER, ALMA_BORDER_LIGHT,
    ALMA_SUCCESS, ALMA_WARNING, ALMA_ERROR, ALMA_INFO,
)
from src.ui.widgets.charts import BarChartWidget, BoxPlotWidget, HeatmapWidget
from src.ui.widgets.report_history import ReportHistoryWidget


# ═══════════════════════════════════════════
#  WORKER THREAD
# ═══════════════════════════════════════════

class AnalyticsWorker(QThread):
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, db_path, date_start, date_end, trc_filter, status_filter):
        super().__init__()
        self.db_path = db_path
        self.date_start = date_start
        self.date_end = date_end
        self.trc_filter = trc_filter
        self.status_filter = status_filter

    def run(self):
        try:
            import sqlite3
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row

            from src.data.trc_analytics import compute_trc_analytics
            result = compute_trc_analytics(
                conn, self.date_start, self.date_end,
                self.trc_filter, self.status_filter
            )
            conn.close()
            self.finished.emit(result)
        except Exception as e:
            import traceback
            self.error.emit(traceback.format_exc())


# ═══════════════════════════════════════════
#  TRC ANALYTICS PAGE
# ═══════════════════════════════════════════

class TRCAnalyticsPage(QWidget):

    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db = db_manager
        self._worker = None
        self._scan_start_time = 0
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        scroll_content = QWidget()
        self._layout = QVBoxLayout(scroll_content)
        self._layout.setContentsMargins(28, 24, 28, 16)
        self._layout.setSpacing(0)

        self._build_header()
        self._build_filter_bar()
        self._build_kpi_cards()
        self._build_charts_row()
        self._build_heatmap()
        self._build_metrics_table()
        self._build_report_history()

        self._layout.addStretch()
        scroll.setWidget(scroll_content)
        outer.addWidget(scroll)

    # ── HEADER ──

    def _build_header(self):
        header = QLabel("TRC Analytics Dashboard")
        header.setObjectName("PageHeader")
        self._layout.addWidget(header)

        sub = QLabel("Ticket volume, resolution times, CSAT, and trends by TRC code")
        sub.setObjectName("PageSubheader")
        self._layout.addWidget(sub)
        self._layout.addSpacing(20)

    # ── FILTER BAR ──

    def _build_filter_bar(self):
        card = QFrame()
        card.setObjectName("TRCFilterCard")
        card.setStyleSheet(f"""
            #TRCFilterCard {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 10px;
            }}
            #TRCFilterCard QLabel {{
                color: {ALMA_TEXT_MID}; border: none; background: transparent;
            }}
            #TRCFilterCard QComboBox {{
                color: {ALMA_TEXT_DARK}; background: {ALMA_WHITE};
                border: 1px solid {ALMA_BORDER}; border-radius: 8px;
                padding: 8px 12px; font-size: 13px;
            }}
            #TRCFilterCard QDateEdit {{
                color: {ALMA_TEXT_DARK}; background: {ALMA_WHITE};
                border: 1px solid {ALMA_BORDER}; border-radius: 8px;
                padding: 8px 12px; font-size: 13px;
            }}
        """)
        row = QHBoxLayout(card)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(12)

        label_style = f"font-size: 11px; font-weight: 600; color: {ALMA_TEXT_MID}; letter-spacing: 0.5px;"

        # Date From
        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("From")
        lbl.setStyleSheet(label_style)
        col.addWidget(lbl)
        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDate(QDate.currentDate().addDays(-90))
        self.date_from.setDisplayFormat("MMM d, yyyy")
        col.addWidget(self.date_from)
        row.addLayout(col, 1)

        # Date To
        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("To")
        lbl.setStyleSheet(label_style)
        col.addWidget(lbl)
        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDate(QDate.currentDate())
        self.date_to.setDisplayFormat("MMM d, yyyy")
        col.addWidget(self.date_to)
        row.addLayout(col, 1)

        # TRC filter
        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("TRC Code")
        lbl.setStyleSheet(label_style)
        col.addWidget(lbl)
        self.trc_combo = QComboBox()
        self.trc_combo.addItem("All TRCs", "")
        self.trc_combo.setMinimumWidth(160)
        col.addWidget(self.trc_combo)
        row.addLayout(col, 1)

        # Status filter
        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("Status")
        lbl.setStyleSheet(label_style)
        col.addWidget(lbl)
        self.status_combo = QComboBox()
        for s in ["All", "Open", "Pending", "Solved", "Closed"]:
            self.status_combo.addItem(s, s)
        col.addWidget(self.status_combo)
        row.addLayout(col, 1)

        # Refresh button
        col = QVBoxLayout()
        col.setSpacing(4)
        col.addWidget(QLabel(""))  # alignment spacer
        self.refresh_btn = QPushButton("  Refresh  ")
        self.refresh_btn.setMinimumHeight(36)
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.clicked.connect(self._on_refresh)
        col.addWidget(self.refresh_btn)
        row.addLayout(col, 1)

        self._layout.addWidget(card)
        self._layout.addSpacing(20)

    # ── KPI CARDS ──

    def _build_kpi_cards(self):
        row = QHBoxLayout()
        row.setSpacing(12)

        self.kpi_total = self._make_kpi_card("Total Tickets", "\u2014", "in selected range")
        self.kpi_resolution = self._make_kpi_card("Avg Resolution", "\u2014", "assignment \u2192 resolution")
        self.kpi_first_reply = self._make_kpi_card("Avg First Reply", "\u2014", "first agent response")
        self.kpi_csat = self._make_kpi_card("Avg CSAT", "\u2014", "satisfaction score")

        for card in (self.kpi_total, self.kpi_resolution, self.kpi_first_reply, self.kpi_csat):
            row.addWidget(card["frame"], 1)

        self._layout.addLayout(row)
        self._layout.addSpacing(20)

    def _make_kpi_card(self, title, value, subtitle):
        frame = QFrame()
        frame.setStyleSheet(f"""
            background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT};
            border-radius: 10px;
        """)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {ALMA_TEXT_LIGHT}; letter-spacing: 0.5px; border: none;")
        layout.addWidget(title_lbl)

        value_lbl = QLabel(value)
        value_lbl.setStyleSheet(f"font-size: 26px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        layout.addWidget(value_lbl)

        sub_lbl = QLabel(subtitle)
        sub_lbl.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none;")
        layout.addWidget(sub_lbl)

        return {"frame": frame, "value": value_lbl, "subtitle": sub_lbl}

    # ── CHARTS ROW ──

    def _build_charts_row(self):
        row = QHBoxLayout()
        row.setSpacing(16)

        # Volume by TRC
        vol_frame = QFrame()
        vol_frame.setObjectName("VolPanel")
        vol_frame.setStyleSheet(f"#VolPanel {{ background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 10px; }}")
        vol_layout = QVBoxLayout(vol_frame)
        vol_layout.setContentsMargins(16, 14, 16, 14)
        vol_title = QLabel("Volume by TRC")
        vol_title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        vol_layout.addWidget(vol_title)
        self.bar_chart = BarChartWidget()
        self.bar_chart.setMinimumHeight(300)
        vol_layout.addWidget(self.bar_chart)
        row.addWidget(vol_frame, 1)

        # Resolution time distribution
        res_frame = QFrame()
        res_frame.setObjectName("ResPanel")
        res_frame.setStyleSheet(f"#ResPanel {{ background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 10px; }}")
        res_layout = QVBoxLayout(res_frame)
        res_layout.setContentsMargins(16, 14, 16, 14)
        res_title = QLabel("Resolution Time Distribution")
        res_title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        res_layout.addWidget(res_title)
        self.box_chart = BoxPlotWidget()
        self.box_chart.setMinimumHeight(300)
        res_layout.addWidget(self.box_chart)
        row.addWidget(res_frame, 1)

        self._layout.addLayout(row)
        self._layout.addSpacing(20)

    # ── CSAT HEATMAP ──

    def _build_heatmap(self):
        frame = QFrame()
        frame.setObjectName("HeatmapPanel")
        frame.setStyleSheet(f"#HeatmapPanel {{ background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 10px; }}")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)

        title = QLabel("CSAT Heatmap (TRC \u00D7 Period)")
        title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        layout.addWidget(title)

        self.heatmap = HeatmapWidget()
        self.heatmap.setMinimumHeight(200)
        layout.addWidget(self.heatmap)

        self._layout.addWidget(frame)
        self._layout.addSpacing(20)

    # ── METRICS TABLE ──

    def _build_metrics_table(self):
        frame = QFrame()
        frame.setObjectName("MetricsPanel")
        frame.setStyleSheet(f"#MetricsPanel {{ background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 10px; }}")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)

        title = QLabel("Metrics by TRC")
        title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        layout.addWidget(title)

        self.metrics_table = QTableWidget()
        headers = [
            "TRC Code", "Tickets", "Avg Res (hrs)", "Median Res (hrs)",
            "P95 Res (hrs)", "Avg First Reply (hrs)", "Avg CSAT",
            "% Solved", "Avg Messages", "Agent:Customer"
        ]
        self.metrics_table.setColumnCount(len(headers))
        self.metrics_table.setHorizontalHeaderLabels(headers)
        self.metrics_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, len(headers)):
            self.metrics_table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self.metrics_table.verticalHeader().setVisible(False)
        self.metrics_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.metrics_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.metrics_table.setAlternatingRowColors(True)
        self.metrics_table.setStyleSheet(f"""
            QTableWidget {{ alternate-background-color: rgba(3,40,27,0.02); border: none; }}
        """)
        self.metrics_table.setSortingEnabled(True)
        self.metrics_table.setMinimumHeight(250)
        layout.addWidget(self.metrics_table)

        self._layout.addWidget(frame)

    # ── REPORT HISTORY ──

    def _build_report_history(self):
        divider = QFrame()
        divider.setObjectName("SectionDivider")
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {ALMA_BORDER_LIGHT};")
        self._layout.addWidget(divider)

        self.report_history = ReportHistoryWidget(self.db, "trc_analytics")
        self.report_history.report_selected.connect(self._load_past_report)
        self._layout.addWidget(self.report_history)

    def _load_past_report(self, report_id):
        """Reload a past report's results."""
        report = self.db.get_full_report(report_id)
        if not report or not report.get("full_results"):
            return
        import json
        try:
            result = json.loads(report["full_results"])
        except (json.JSONDecodeError, TypeError):
            return
        self._display_results(result)

    def _display_results(self, result):
        """Populate all panels from a results dict (fresh or from history)."""
        summary = result["summary"]

        total = summary["total_tickets"]
        self.kpi_total["value"].setText(f"{total:,}" if total else "\u2014")

        avg_res = summary["avg_resolution"]
        self.kpi_resolution["value"].setText(f"{avg_res:.1f}h" if avg_res is not None else "\u2014")

        avg_frt = summary["avg_first_reply"]
        self.kpi_first_reply["value"].setText(f"{avg_frt:.1f}h" if avg_frt is not None else "\u2014")

        avg_csat = summary["avg_csat"]
        self.kpi_csat["value"].setText(f"{avg_csat:.1f}/5" if avg_csat is not None else "\u2014")

        self.bar_chart.set_data(result["volume_by_trc"], max_items=20)
        self.box_chart.set_data(result["resolution_by_trc"], max_items=10, min_samples=5)

        hm = result["csat_heatmap"]
        self.heatmap.set_data(hm["y_labels"], hm["x_labels"], hm["values"])

        self._populate_metrics_table(result["metrics_table"])

    # ═══════════════════════════════════════════
    #  TRC FILTER POPULATION
    # ═══════════════════════════════════════════

    def populate_trc_filter(self):
        """Load TRC codes into the filter dropdown."""
        current = self.trc_combo.currentData()
        self.trc_combo.clear()
        self.trc_combo.addItem("All TRCs", "")
        trcs = self.db.get_trc_codes()
        for trc in trcs:
            self.trc_combo.addItem(f"{trc['code']} \u2014 {trc['label']}", trc["code"])
        # Try to restore previous selection
        if current:
            idx = self.trc_combo.findData(current)
            if idx >= 0:
                self.trc_combo.setCurrentIndex(idx)

    # ═══════════════════════════════════════════
    #  REFRESH / COMPUTE
    # ═══════════════════════════════════════════

    def _on_refresh(self):
        """Run analytics computation in a background thread."""
        import time
        self.populate_trc_filter()

        date_start = self.date_from.date().toString("yyyy-MM-dd")
        date_end = self.date_to.date().toString("yyyy-MM-dd") + "T23:59:59"
        trc_filter = self.trc_combo.currentData() or None
        status_filter = self.status_combo.currentText()

        if status_filter == "All":
            status_filter = None

        # Progress dialog
        self._progress = QProgressDialog("Computing TRC analytics...", None, 0, 0, self)
        self._progress.setWindowTitle("Analyzing")
        self._progress.setWindowModality(Qt.WindowModal)
        self._progress.setMinimumDuration(0)
        self._progress.show()
        QApplication.processEvents()

        self._scan_start_time = time.time()
        self._worker = AnalyticsWorker(self.db.db_path, date_start, date_end, trc_filter, status_filter)
        self._worker.finished.connect(self._on_results)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_results(self, result):
        """Populate all panels with computed data."""
        import time
        import json

        if hasattr(self, "_progress"):
            self._progress.close()

        elapsed_ms = int((time.time() - self._scan_start_time) * 1000)

        # Display results
        self._display_results(result)

        # Save report
        summary = result["summary"]
        report_summary = {
            "trc_count": len(result.get("metrics_table", [])),
            "avg_csat": summary.get("avg_csat"),
            "total_tickets": summary.get("total_tickets", 0),
        }
        date_from = self.date_from.date().toString("yyyy-MM-dd")
        date_to = self.date_to.date().toString("yyyy-MM-dd")
        trc_filter = self.trc_combo.currentData() or "All"
        self.db.save_report(
            page="trc_analytics",
            parameters={"date_from": date_from, "date_to": date_to, "trc_filter": trc_filter},
            summary=report_summary,
            full_results=json.dumps(result, default=str),
            ticket_count=summary.get("total_tickets", 0),
            duration_ms=elapsed_ms,
        )
        self.report_history.refresh()

    def _on_error(self, error_text):
        if hasattr(self, "_progress"):
            self._progress.close()
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(self, "Analytics Error", f"Computation failed:\n\n{error_text}")

    def _populate_metrics_table(self, rows):
        """Fill the metrics table with per-TRC data."""
        self.metrics_table.setSortingEnabled(False)
        self.metrics_table.setRowCount(len(rows))

        for i, r in enumerate(rows):
            self.metrics_table.setItem(i, 0, QTableWidgetItem(r["trc_code"]))

            # Ticket count — use numeric sort item
            item = QTableWidgetItem()
            item.setData(Qt.DisplayRole, r["ticket_count"])
            self.metrics_table.setItem(i, 1, item)

            # Numeric columns
            num_cols = [
                (2, "avg_resolution"),
                (3, "median_resolution"),
                (4, "p95_resolution"),
                (5, "avg_first_reply"),
                (6, "avg_csat"),
                (7, "pct_solved"),
                (8, "avg_messages"),
                (9, "agent_customer_ratio"),
            ]

            for col_idx, key in num_cols:
                val = r.get(key)
                item = QTableWidgetItem()
                if val is not None:
                    if key == "pct_solved":
                        item.setText(f"{val:.1f}%")
                        item.setData(Qt.UserRole, val)
                    elif key == "agent_customer_ratio":
                        item.setText(f"{val:.2f}")
                        item.setData(Qt.UserRole, val)
                    else:
                        item.setText(f"{val:.1f}")
                        item.setData(Qt.UserRole, val)
                else:
                    item.setText("\u2014")
                    item.setData(Qt.UserRole, -999)

                # Conditional formatting
                if key == "avg_csat" and val is not None and val < 3.0:
                    item.setBackground(QColor(ALMA_ERROR).lighter(180))
                if key == "p95_resolution" and val is not None and val > 48:
                    item.setBackground(QColor(ALMA_WARNING).lighter(180))

                self.metrics_table.setItem(i, col_idx, item)

        self.metrics_table.setSortingEnabled(True)
