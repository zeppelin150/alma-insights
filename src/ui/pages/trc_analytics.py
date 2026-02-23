"""
Alma Insights — TRC Analytics Dashboard
Volume charts, resolution times, CSAT heatmaps, and filterable metrics table.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QFrame, QScrollArea, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView,
    QApplication, QSizePolicy,
)
from PySide6.QtCore import Qt, QDate, QThread, Signal
from PySide6.QtGui import QFont, QColor

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_GREEN_SUBTLE,
    ALMA_WHITE, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_TEXT_ON_DARK, ALMA_BORDER, ALMA_BORDER_LIGHT,
    ALMA_SUCCESS, ALMA_WARNING, ALMA_ERROR, ALMA_INFO,
    ALMA_BG_ELEVATED, apply_card_shadow, apply_card_shadow_soft,
)
from src.ui.widgets.date_picker import ModernDatePicker
from src.ui.widgets.charts import BarChartWidget, BoxPlotWidget, HeatmapWidget
from src.ui.widgets.collapsible_section import CollapsibleSection
from src.ui.widgets.pagination_bar import PaginationBar
from src.ui.widgets.report_history_summary import ReportHistorySummary
from src.ui.layman_mode import is_layman_mode, translate_label


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
        self._drilldown = None
        self._worker = None
        self._scan_start_time = 0
        self._build_ui()

    def set_drilldown_panel(self, panel):
        """Accept a reference to the shared DrilldownPanel from MainWindow."""
        self._drilldown = panel

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
                background: {ALMA_BG_ELEVATED}; border: 1px solid rgba(214, 210, 202, 0.45);
                border-radius: 12px;
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
        apply_card_shadow(card)
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
        self.date_from = ModernDatePicker()
        self.date_from.setDate(QDate.currentDate().addDays(-90))
        col.addWidget(self.date_from)
        row.addLayout(col, 1)

        # Date To
        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("To")
        lbl.setStyleSheet(label_style)
        col.addWidget(lbl)
        self.date_to = ModernDatePicker()
        self.date_to.setDate(QDate.currentDate())
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
            background: {ALMA_BG_ELEVATED}; border: 1px solid rgba(214, 210, 202, 0.45);
            border-radius: 12px;
        """)
        apply_card_shadow_soft(frame)
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
        section = CollapsibleSection("Volume & Resolution Charts", section_key="trc_analytics.volume_resolution_charts")

        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(16)

        # Volume by TRC
        vol_frame = QFrame()
        vol_frame.setObjectName("VolPanel")
        vol_frame.setStyleSheet(f"#VolPanel {{ background: {ALMA_BG_ELEVATED}; border: 1px solid rgba(214, 210, 202, 0.45); border-radius: 12px; }}")
        apply_card_shadow_soft(vol_frame)
        vol_layout = QVBoxLayout(vol_frame)
        vol_layout.setContentsMargins(16, 14, 16, 14)
        vol_title = QLabel("Volume by TRC")
        vol_title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        vol_layout.addWidget(vol_title)
        self.bar_chart = BarChartWidget()
        self.bar_chart.setMinimumHeight(300)
        self.bar_chart.bar_clicked.connect(self._on_volume_bar_clicked)
        vol_layout.addWidget(self.bar_chart)
        self._bar_pager = PaginationBar(page_size=10)
        self._bar_pager.page_changed.connect(self.bar_chart.set_page)
        vol_layout.addWidget(self._bar_pager)
        row.addWidget(vol_frame, 1)

        # Resolution time distribution
        res_frame = QFrame()
        res_frame.setObjectName("ResPanel")
        res_frame.setStyleSheet(f"#ResPanel {{ background: {ALMA_BG_ELEVATED}; border: 1px solid rgba(214, 210, 202, 0.45); border-radius: 12px; }}")
        apply_card_shadow_soft(res_frame)
        res_layout = QVBoxLayout(res_frame)
        res_layout.setContentsMargins(16, 14, 16, 14)
        res_title = QLabel("Resolution Time Distribution")
        res_title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        res_layout.addWidget(res_title)
        self.box_chart = BoxPlotWidget()
        self.box_chart.setMinimumHeight(300)
        res_layout.addWidget(self.box_chart)
        self._box_pager = PaginationBar(page_size=10)
        self._box_pager.page_changed.connect(self.box_chart.set_page)
        res_layout.addWidget(self._box_pager)
        row.addWidget(res_frame, 1)

        section.add_widget(row_widget)
        self._layout.addWidget(section)
        self._layout.addSpacing(16)

    # ── CSAT HEATMAP ──

    def _build_heatmap(self):
        section = CollapsibleSection("CSAT Heatmap", section_key="trc_analytics.csat_heatmap")

        self.heatmap = HeatmapWidget()
        self.heatmap.setMinimumHeight(200)
        self.heatmap.cell_clicked.connect(self._on_heatmap_cell_clicked)
        section.add_widget(self.heatmap)
        self._heatmap_pager = PaginationBar(page_size=10)
        self._heatmap_pager.page_changed.connect(self.heatmap.set_page)
        section.add_widget(self._heatmap_pager)

        self._layout.addWidget(section)
        self._layout.addSpacing(16)

    # ── METRICS TABLE ──

    def _build_metrics_table(self):
        section = CollapsibleSection("Metrics by TRC", section_key="trc_analytics.metrics_by_trc")

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
        section.add_widget(self.metrics_table)
        self._metrics_pager = PaginationBar(page_size=15)
        self._metrics_pager.page_changed.connect(self._on_metrics_page_changed)
        section.add_widget(self._metrics_pager)
        self._metrics_all_rows = []  # store full row list for pagination

        self._layout.addWidget(section)

    def _display_results(self, result):
        """Populate all panels from a results dict (fresh or from history)."""
        layman = is_layman_mode()
        summary = result["summary"]

        total = summary["total_tickets"]
        self.kpi_total["value"].setText(f"{total:,}" if total else "\u2014")

        avg_res = summary["avg_resolution"]
        if layman and avg_res is not None:
            if avg_res < 1:
                self.kpi_resolution["value"].setText(f"{avg_res*60:.0f}min")
            elif avg_res > 24:
                self.kpi_resolution["value"].setText(f"{avg_res/24:.1f}d")
            else:
                self.kpi_resolution["value"].setText(f"{avg_res:.1f}h")
        else:
            self.kpi_resolution["value"].setText(f"{avg_res:.1f}h" if avg_res is not None else "\u2014")

        avg_frt = summary["avg_first_reply"]
        if layman and avg_frt is not None:
            if avg_frt < 1:
                self.kpi_first_reply["value"].setText(f"{avg_frt*60:.0f}min")
            else:
                self.kpi_first_reply["value"].setText(f"{avg_frt:.1f}h")
        else:
            self.kpi_first_reply["value"].setText(f"{avg_frt:.1f}h" if avg_frt is not None else "\u2014")

        avg_csat = summary["avg_csat"]
        if layman and avg_csat is not None:
            stars = "\u2605" * int(round(avg_csat))
            self.kpi_csat["value"].setText(f"{avg_csat:.1f} {stars}")
        else:
            self.kpi_csat["value"].setText(f"{avg_csat:.1f}/5" if avg_csat is not None else "\u2014")

        # ── Volume bar chart + paginator ──
        vol_data = result["volume_by_trc"]
        self.bar_chart.set_data(vol_data)
        self._bar_pager.set_total(len(vol_data) if vol_data else 0)

        # ── Resolution box plot + paginator ──
        res_data = result["resolution_by_trc"]
        self.box_chart.set_data(res_data, min_samples=5)
        # Count labels that survived the min_samples filter
        self._box_pager.set_total(len(self.box_chart._all_labels))

        # ── CSAT Heatmap + paginator ──
        hm = result["csat_heatmap"]
        self.heatmap.set_data(hm["y_labels"], hm["x_labels"], hm["values"])
        self._heatmap_pager.set_total(len(hm["y_labels"]) if hm.get("y_labels") else 0)

        # ── Metrics table + paginator ──
        self._metrics_all_rows = result["metrics_table"] or []
        self._metrics_pager.set_total(len(self._metrics_all_rows))
        self._show_metrics_page(0)

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

    def sync_date_to_data(self):
        """Set date pickers to match the actual data range in the DB."""
        try:
            min_d, max_d = self.db.get_date_range()
            if min_d:
                parts = min_d[:10].split("-")
                if len(parts) == 3:
                    self.date_from.setDate(QDate(int(parts[0]), int(parts[1]), int(parts[2])))
            if max_d:
                parts = max_d[:10].split("-")
                if len(parts) == 3:
                    self.date_to.setDate(QDate(int(parts[0]), int(parts[1]), int(parts[2])))
        except Exception:
            pass

    def auto_refresh(self):
        """Sync dates to data range, then run analysis."""
        self.sync_date_to_data()
        self._on_refresh()

    # ═══════════════════════════════════════════
    #  REFRESH / COMPUTE
    # ═══════════════════════════════════════════

    def _on_refresh(self):
        """Run analytics computation via the unified job queue."""
        import time
        self._scan_start_time = time.time()

        main_win = self.window()
        if hasattr(main_win, '_job_queue'):
            job = main_win._make_analytics_job()
            main_win._job_queue.submit(job)
        else:
            # Fallback: run directly (e.g. standalone testing)
            self._run_directly()

    def _run_directly(self):
        """Run analytics without the job queue (fallback)."""
        import time
        self.populate_trc_filter()
        date_start = self.date_from.date().toString("yyyy-MM-dd")
        date_end = self.date_to.date().toString("yyyy-MM-dd") + "T23:59:59"
        trc_filter = self.trc_combo.currentData() or None
        status_filter = self.status_combo.currentText()
        if status_filter == "All":
            status_filter = None

        self._scan_start_time = time.time()
        self._worker = AnalyticsWorker(self.db.db_path, date_start, date_end, trc_filter, status_filter)
        self._worker.finished.connect(self._on_results)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_results(self, result):
        """Populate all panels with computed data."""
        import time
        import json

        elapsed_ms = int((time.time() - getattr(self, '_scan_start_time', time.time())) * 1000)

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
        self.report_summary.refresh()

    # ── DRILL-DOWN HANDLERS ──

    def _on_volume_bar_clicked(self, label: str):
        """Drill into tickets for the clicked TRC bar."""
        if not self._drilldown:
            return
        # label is typically "TRC-xxx — Some Label"; extract the code
        trc_code = label.split(" —")[0].strip() if " —" in label else label.strip()
        date_from = self.date_from.date().toString("yyyy-MM-dd")
        date_to = self.date_to.date().toString("yyyy-MM-dd") + "T23:59:59"

        tickets = self.db.search_conversations(
            trc_code=trc_code, date_from=date_from, date_to=date_to,
        )
        total = len(tickets)
        self._drilldown.show_tickets(
            f"TRC: {trc_code}",
            f"{total} ticket{'s' if total != 1 else ''}",
            tickets,
        )

    def _on_heatmap_cell_clicked(self, y_label: str, x_label: str):
        """Drill into tickets for the clicked heatmap cell (TRC × period)."""
        if not self._drilldown:
            return
        # y_label is TRC code/label, x_label is period label
        trc_code = y_label.split(" —")[0].strip() if " —" in y_label else y_label.strip()
        date_from = self.date_from.date().toString("yyyy-MM-dd")
        date_to = self.date_to.date().toString("yyyy-MM-dd") + "T23:59:59"

        tickets = self.db.search_conversations(
            trc_code=trc_code, date_from=date_from, date_to=date_to,
        )
        total = len(tickets)
        self._drilldown.show_tickets(
            f"TRC: {trc_code} — {x_label}",
            f"{total} ticket{'s' if total != 1 else ''}",
            tickets,
        )

    # ── REPORT HISTORY ──

    def _build_report_history(self):
        """Add compact Report History summary at bottom of scroll content."""
        self.report_summary = ReportHistorySummary(self.db, "trc_analytics")
        self.report_summary.view_all_clicked.connect(self._open_report_history)
        self._layout.addWidget(self.report_summary)

    def _open_report_history(self):
        """Open the DrilldownPanel with the full report history list."""
        if not self._drilldown:
            return
        reports = self.report_summary.get_reports_for_drilldown()
        count = len(reports)
        self._drilldown.show_reports(
            "TRC Analytics Reports",
            f"{count} report{'s' if count != 1 else ''}",
            reports,
            detail_callback=self._render_report_detail_html,
            load_callback=self._load_past_report,
        )

    def _render_report_detail_html(self, report_id):
        """Render a TRC analytics report as HTML for the DrilldownPanel."""
        import json
        report = self.db.get_full_report(report_id)
        if not report:
            return "<p>Report not found.</p>"

        raw = report.get("full_results", "")
        try:
            result = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return "<p>Could not parse report data.</p>"

        html = ['<div style="font-family: Segoe UI, sans-serif; font-size: 13px;">']

        # ── Summary KPIs ──
        summary = result.get("summary", {})
        html.append("<h3 style='margin: 12px 0 6px;'>Summary</h3>")
        total = summary.get("total_tickets", 0)
        avg_csat = summary.get("avg_csat")
        avg_res = summary.get("avg_resolution_hours")
        html.append(f"<p>Total tickets: <b>{total}</b></p>")
        if avg_csat:
            html.append(f"<p>Average CSAT: <b>{avg_csat:.2f}</b></p>")
        if avg_res:
            html.append(f"<p>Avg resolution: <b>{avg_res:.1f}h</b></p>")

        # ── Metrics table ──
        metrics = result.get("metrics_table", [])
        if metrics:
            html.append(f"<h3 style='margin: 12px 0 6px;'>TRC Breakdown ({len(metrics)})</h3>")
            html.append("<table cellpadding='3' style='border-collapse: collapse; width: 100%;'>")
            html.append("<tr style='border-bottom: 1px solid #E8E5DE;'>"
                        "<th align='left'>TRC</th><th align='right'>Tickets</th>"
                        "<th align='right'>CSAT</th></tr>")
            for row in metrics[:30]:
                trc = row.get("trc_code", "")
                count_t = row.get("ticket_count", 0)
                csat = row.get("avg_csat")
                csat_str = f"{csat:.2f}" if csat else "—"
                html.append(
                    f"<tr style='border-bottom: 1px solid #F0F0F0;'>"
                    f"<td>{trc}</td><td align='right'>{count_t}</td>"
                    f"<td align='right'>{csat_str}</td></tr>"
                )
            html.append("</table>")

        html.append("</div>")
        return "".join(html)

    def _load_past_report(self, report_id):
        """Reload a past TRC analytics report into the main view."""
        import json
        report = self.db.get_full_report(report_id)
        if not report or not report.get("full_results"):
            return
        try:
            result = json.loads(report["full_results"])
        except (json.JSONDecodeError, TypeError):
            return
        self._display_results(result)

    def _on_error(self, error_text):
        # Mark job as failed so queue advances immediately
        main_win = self.window()
        if hasattr(main_win, '_job_queue'):
            main_win._job_queue.mark_job_failed("trc_analytics_refresh", error_text)
        # Defer error dialog so it doesn't block the job queue
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._show_error(error_text))

    def _show_error(self, error_text):
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(self, "Analytics Error", f"Computation failed:\n\n{error_text}")

    def _populate_metrics_table(self, rows):
        """Fill the metrics table with per-TRC data."""
        layman = is_layman_mode()
        if layman:
            headers = [
                "TRC Code", "Tickets", "Avg Resolution (hrs)", "Median Resolution (hrs)",
                "Worst-Case (hrs)", "Avg First Reply (hrs)", "Avg Satisfaction",
                "% Solved", "Avg Messages", "Agent:Customer"
            ]
        else:
            headers = [
                "TRC Code", "Tickets", "Avg Res (hrs)", "Median Res (hrs)",
                "P95 Res (hrs)", "Avg First Reply (hrs)", "Avg CSAT",
                "% Solved", "Avg Messages", "Agent:Customer"
            ]
        self.metrics_table.setHorizontalHeaderLabels(headers)

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

    def _on_metrics_page_changed(self, page: int):
        """Handle metrics table pagination."""
        self._show_metrics_page(page)

    def _show_metrics_page(self, page: int):
        """Display a single page of metrics rows."""
        ps = self._metrics_pager.page_size
        start = page * ps
        end = min(start + ps, len(self._metrics_all_rows))
        page_rows = self._metrics_all_rows[start:end]
        self._populate_metrics_table(page_rows)
