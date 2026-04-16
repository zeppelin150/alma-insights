"""
Alma Insights — Incidents Page
TRC ticket-rate anomaly detection with Poisson thresholds + CUSUM drift.

Refactored to use AnalysisPageBase building blocks with 4 tabs:
  Tab 1 - Overview:      KPI cards + TRC Status Grid + Control Chart
  Tab 2 - Open Incidents: KPI cards + incident action cards
  Tab 3 - Anomaly Scan:  θ EWMA scan controls + anomaly flag cards
  Tab 4 - Reports:       ReportsTab (report history)
"""

import json
import time
from datetime import datetime
from src.data.connection_factory import get_connection

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QFrame, QScrollArea, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView,
    QApplication, QSizePolicy,
)
from PySide6.QtCore import Qt, QDate, QThread, Signal
from PySide6.QtGui import QColor, QFont

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT,
    ALMA_WHITE, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_BORDER, ALMA_BORDER_LIGHT,
    ALMA_SUCCESS, ALMA_WARNING, ALMA_ERROR, ALMA_INFO,
    ALMA_BG_ELEVATED, apply_card_shadow, apply_card_shadow_soft,
    configure_table,
)
from src.ui.widgets.control_chart import ControlChartWidget
from src.ui.widgets.pagination_bar import PaginationBar
from src.ui.widgets.empty_state import EmptyState
from src.ui.widgets.skeleton import SkeletonGroup
from src.ui.widgets.analysis_page_base import AnalysisPageBase
from src.ui.widgets.kpi_card import KPICard, KPICardRow
from src.ui.widgets.tab_scroll_content import TabScrollContent
from src.ui.widgets.reports_tab import ReportsTab
from src.ui.layman_mode import (
    is_layman_mode, translate_label, format_pvalue,
    format_lambda, format_zscore, format_cusum, format_theta_level,
)


# ═══════════════════════════════════════════
#  WORKER THREAD
# ═══════════════════════════════════════════

class IncidentWorker(QThread):
    """Background thread that runs the Poisson incident scan.

    Computes daily ticket counts per TRC, builds baselines, detects
    spikes above theta-1/theta-2 thresholds, and writes to `incident_flags`.
    """

    finished = Signal(dict)
    error = Signal(str)
    progress = Signal(str, int)  # message, percent

    def __init__(self, db_path, target_date, date_from=None, source_id=None):
        super().__init__()
        self.db_path = db_path
        self.target_date = target_date
        self.date_from = date_from
        self.source_id = source_id

    def run(self):
        try:
            from src.data.db_manager import DatabaseManager
            db = DatabaseManager(self.db_path)
            db.initialize()

            from src.data.incident_engine import run_incident_scan
            result = run_incident_scan(
                db,
                target_date=self.target_date,
                date_from=self.date_from,
                progress_callback=lambda msg, pct: self.progress.emit(msg, pct),
                source_id=self.source_id,
            )
            db.close()
            self.finished.emit(result)
        except Exception as e:
            import traceback
            self.error.emit(traceback.format_exc())


class ThetaWorker(QThread):
    """Background worker for θ EWMA anomaly detection scan."""
    progress = Signal(str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, db_path):
        super().__init__()
        self.db_path = db_path

    def run(self):
        try:
            conn = get_connection(self.db_path)

            from src.data.theta_engine import run_theta_scan_range

            def on_progress(step, total, msg):
                self.progress.emit(msg)

            result = run_theta_scan_range(conn, progress_callback=on_progress)
            conn.close()
            self.finished.emit(result)
        except Exception as e:
            import traceback
            self.error.emit(traceback.format_exc())


# ═══════════════════════════════════════════
#  INCIDENTS PAGE
# ═══════════════════════════════════════════

class IncidentsPage(AnalysisPageBase):
    """Incidents page: Poisson spike detection and theta anomaly flags.

    Shows open incident flags with theta-1 (warning) and theta-2 (critical)
    tiers, CUSUM drift detection, and per-TRC control charts. Users can
    acknowledge, resolve, or mark flags as false positives.
    """

    scan_complete = Signal(int)  # emits count of 2θ flags for sidebar badge

    def __init__(self, db_manager, parent=None):
        super().__init__(
            db_manager,
            "Incident Monitor",
            subtitle="Statistical anomaly detection: Poisson thresholds + CUSUM drift",
            parent=parent,
        )
        self._worker = None
        self._theta_worker = None
        self._progress = None
        self._scan_start_time = 0
        self._last_result = None
        self._trc_results = []

        self._setup_filters()
        self._setup_tabs()

    # ═══════════════════════════════════════════
    #  BACKWARD-COMPATIBLE PROPERTY ACCESS
    # ═══════════════════════════════════════════

    @property
    def date_from(self):
        return self.filter_bar.get_date_from()

    @property
    def date_to(self):
        return self.filter_bar.get_date_to()

    # ═══════════════════════════════════════════
    #  DRILLDOWN
    # ═══════════════════════════════════════════

    def set_drilldown_panel(self, panel):
        super().set_drilldown_panel(panel)
        self._reports_tab.set_drilldown_panel(
            panel, detail_callback=self._render_report_detail_html)

    # ═══════════════════════════════════════════
    #  FILTER BAR SETUP
    # ═══════════════════════════════════════════

    def _setup_filters(self):
        from src.ui.widgets.source_selector import SourceSelector
        self._source_selector = SourceSelector(self)
        self._source_selector.setFixedWidth(160)
        self.filter_bar.add_custom_widget("Source", self._source_selector)
        self.filter_bar.add_date_range(date_from=QDate.currentDate().addDays(-30))
        self.filter_bar.add_combo_filter("trc", "TRC Code", ["All TRCs"])
        self.filter_bar.add_action_button("Run Scan")
        # Refresh source list
        try:
            from src.data.connection_factory import get_connection
            conn = get_connection(self.db.db_path)
            self._source_selector.refresh_sources(conn)
            conn.close()
        except Exception:
            pass

    # ═══════════════════════════════════════════
    #  ACTION / FILTER HOOKS
    # ═══════════════════════════════════════════

    def _on_filters_changed(self, filters):
        pass  # Don't auto-trigger on filter change

    def _on_action_triggered(self, action):
        if action == "Run Scan":
            self._on_run_scan()

    # ═══════════════════════════════════════════
    #  TAB SETUP
    # ═══════════════════════════════════════════

    def _setup_tabs(self):
        # ── Tab 1: Overview ──
        self._overview_tab = TabScrollContent()
        self._build_overview_tab()
        self.add_tab(self._overview_tab, "Overview")

        # ── Tab 2: Open Incidents ──
        self._incidents_tab = TabScrollContent()
        self._build_incidents_tab()
        self.add_tab(self._incidents_tab, "Open Incidents")

        # ── Tab 3: Anomaly Scan ──
        self._theta_tab = TabScrollContent()
        self._build_theta_tab()
        self.add_tab(self._theta_tab, "Anomaly Scan")

        # ── Tab 4: Reports ──
        self._reports_tab = ReportsTab("incidents", self.db)
        self.add_tab(self._reports_tab, "Reports")

    # ── TAB 1: OVERVIEW ──

    def _build_overview_tab(self):
        layout = self._overview_tab.content_layout

        # KPI Row
        kpi_row = KPICardRow()
        self._kpi_total_trcs = kpi_row.add_card(KPICard("Total TRCs", "\u2014", "scanned codes"))
        self._kpi_flagged = kpi_row.add_card(KPICard("Flagged", "\u2014", "\u03b8\u2081 + \u03b8\u2082"))
        self._kpi_active = kpi_row.add_card(KPICard("Active Incidents", "\u2014", "\u03b8\u2082 only"))
        self._kpi_scan_date = kpi_row.add_card(KPICard("Last Scan", "\u2014", "scan date"))
        layout.addWidget(kpi_row)

        # Skeleton loading placeholder
        self._skeleton = QWidget()
        skel_layout = QVBoxLayout(self._skeleton)
        skel_layout.setContentsMargins(0, 0, 0, 0)
        skel_layout.setSpacing(20)
        skel_layout.addWidget(SkeletonGroup.table_rows(5, self._skeleton))
        skel_layout.addWidget(SkeletonGroup.chart_area(self._skeleton))
        skel_layout.addStretch()
        self._skeleton.setVisible(False)
        layout.addWidget(self._skeleton)

        # Empty state (hidden until no-data scenario)
        self._empty_state = EmptyState(
            icon="data",
            heading="No incidents detected",
            description="Run an incident scan to check for anomalies",
            action_label="Run Scan",
        )
        self._empty_state.action_clicked.connect(self._on_run_scan)
        self._empty_state.setVisible(False)
        layout.addWidget(self._empty_state)

        # TRC Status Grid
        self._status_grid_wrapper = QWidget()
        grid_layout = QVBoxLayout(self._status_grid_wrapper)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setSpacing(8)

        self.status_table = QTableWidget()
        headers = ["TRC", "Tier", "Today", "\u03bb", "\u03b8\u2081", "\u03b8\u2082", "p-value", "CUSUM", "Status"]
        self.status_table.setColumnCount(len(headers))
        self.status_table.setHorizontalHeaderLabels(headers)
        self.status_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.status_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)  # CUSUM col wider
        for i in range(1, len(headers)):
            if i != 7:
                self.status_table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self.status_table.verticalHeader().setVisible(False)
        configure_table(self.status_table)
        self.status_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.status_table.setAlternatingRowColors(True)
        self.status_table.setStyleSheet(
            "QTableWidget { alternate-background-color: rgba(3,40,27,0.02); border: none; }"
        )
        self.status_table.setMinimumHeight(200)
        self.status_table.setMaximumHeight(350)
        self.status_table.currentCellChanged.connect(self._on_trc_row_selected)
        self.status_table.doubleClicked.connect(self._on_trc_row_drilldown)
        grid_layout.addWidget(self.status_table)

        self._grid_pager = PaginationBar(page_size=15)
        self._grid_pager.page_changed.connect(self._on_grid_page_changed)
        grid_layout.addWidget(self._grid_pager)
        self._grid_all_results = []  # full sorted trc_results for pagination

        layout.addWidget(self._status_grid_wrapper)

        # Control Chart
        self._chart_wrapper = QWidget()
        chart_layout = QVBoxLayout(self._chart_wrapper)
        chart_layout.setContentsMargins(0, 0, 0, 0)
        self.control_chart = ControlChartWidget()
        chart_layout.addWidget(self.control_chart)
        layout.addWidget(self._chart_wrapper)

        # Content sections for skeleton toggle
        self._content_sections = [self._status_grid_wrapper, self._chart_wrapper]

        layout.addStretch()

    # ── TAB 2: OPEN INCIDENTS ──

    def _build_incidents_tab(self):
        layout = self._incidents_tab.content_layout

        # KPI Row
        kpi_row = KPICardRow()
        self._kpi_open = kpi_row.add_card(KPICard("Open", "\u2014", "total open"))
        self._kpi_critical = kpi_row.add_card(KPICard("Critical (2\u03b8)", "\u2014", "needs action"))
        self._kpi_watch = kpi_row.add_card(KPICard("Watch (1\u03b8)", "\u2014", "monitoring"))
        self._kpi_ack = kpi_row.add_card(KPICard("Acknowledged", "\u2014", "ack'd"))
        layout.addWidget(kpi_row)

        # Incidents container
        self._incidents_wrapper = QWidget()
        incidents_inner_layout = QVBoxLayout(self._incidents_wrapper)
        incidents_inner_layout.setContentsMargins(0, 0, 0, 0)

        self.incidents_container = QVBoxLayout()
        self.incidents_container.setSpacing(8)
        self._no_incidents_label = QLabel("No open incidents. Run a scan to check for anomalies.")
        self._no_incidents_label.setStyleSheet(f"color: {ALMA_TEXT_LIGHT}; font-size: 12px; border: none; padding: 12px;")
        self._no_incidents_label.setAlignment(Qt.AlignCenter)
        self.incidents_container.addWidget(self._no_incidents_label)
        incidents_inner_layout.addLayout(self.incidents_container)

        layout.addWidget(self._incidents_wrapper)

        # Track for skeleton toggle
        self._content_sections.append(self._incidents_wrapper)

        layout.addStretch()

    # ── TAB 3: ANOMALY SCAN ──

    def _build_theta_tab(self):
        layout = self._theta_tab.content_layout

        # Header row with action buttons
        header_row = QHBoxLayout()

        self._theta_scan_btn = QPushButton("Run \u03b8 Scan")
        self._theta_scan_btn.setCursor(Qt.PointingHandCursor)
        self._theta_scan_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_ERROR};
                font-size: 11px; font-weight: 600; border: 1px solid {ALMA_ERROR};
                border-radius: 6px; padding: 4px 14px;
            }}
            QPushButton:hover {{ background: rgba(196,30,30,0.08); }}
        """)
        self._theta_scan_btn.clicked.connect(self._run_theta_scan)
        header_row.addWidget(self._theta_scan_btn)

        history_btn = QPushButton("View History")
        history_btn.setCursor(Qt.PointingHandCursor)
        history_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_TEXT_DARK};
                font-size: 11px; font-weight: 600; border: 1px solid {ALMA_BORDER};
                border-radius: 6px; padding: 4px 14px;
            }}
            QPushButton:hover {{ background: {ALMA_CREAM}; }}
        """)
        history_btn.clicked.connect(self._toggle_theta_history)
        header_row.addWidget(history_btn)
        header_row.addStretch()

        layout.addLayout(header_row)

        subtitle = QLabel("EWMA baseline anomaly detection \u2014 volume, sentiment, and term frequency per TRC per day")
        subtitle.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none;")
        layout.addWidget(subtitle)

        # Container for anomaly flag cards
        self._theta_container_wrapper = QWidget()
        theta_inner = QVBoxLayout(self._theta_container_wrapper)
        theta_inner.setContentsMargins(0, 0, 0, 0)

        self._theta_container = QVBoxLayout()
        self._theta_container.setSpacing(8)
        theta_inner.addLayout(self._theta_container)

        self._theta_message = QLabel("Run \u03b8 scan to detect anomalies across your ticket data...")
        self._theta_message.setStyleSheet(f"color: {ALMA_TEXT_LIGHT}; font-size: 12px; border: none;")
        self._theta_message.setAlignment(Qt.AlignCenter)
        theta_inner.addWidget(self._theta_message)

        self._theta_pager = PaginationBar(page_size=10)
        self._theta_pager.page_changed.connect(self._on_theta_page_changed)
        theta_inner.addWidget(self._theta_pager)
        self._theta_all_flags = []  # full sorted flags for pagination

        layout.addWidget(self._theta_container_wrapper)

        layout.addStretch()

    # ═══════════════════════════════════════════
    #  SKELETON LOADING (Build 10.0: T12)
    # ═══════════════════════════════════════════

    def _show_loading(self):
        """Show skeleton placeholders, hide real content."""
        self._skeleton.setVisible(True)
        self._empty_state.setVisible(False)
        for w in self._content_sections:
            w.setVisible(False)

    def _show_content(self):
        """Hide skeleton, show real content."""
        self._skeleton.setVisible(False)
        for w in self._content_sections:
            w.setVisible(True)

    # ═══════════════════════════════════════════
    #  TRC FILTER POPULATION
    # ═══════════════════════════════════════════

    def populate_trc_filter(self):
        """Load TRC codes into the filter dropdown."""
        trcs = self.db.get_trc_codes()
        items = ["All TRCs"] + [f"{t['code']} \u2014 {t['label']}" for t in trcs]
        self.filter_bar.set_combo_items("trc", items)

    def sync_date_to_data(self):
        """Set the date pickers to the data range in the DB."""
        try:
            min_d, max_d = self.db.get_date_range()
            if min_d:
                parts = min_d[:10].split("-")
                if len(parts) == 3:
                    qd_min = QDate(int(parts[0]), int(parts[1]), int(parts[2]))
                    self.filter_bar.get_date_from().setMinimumDate(qd_min)
                    self.filter_bar.get_date_to().setMinimumDate(qd_min)
                    self.filter_bar.get_date_from().setDate(qd_min)
            if max_d:
                parts = max_d[:10].split("-")
                if len(parts) == 3:
                    qd_max = QDate(int(parts[0]), int(parts[1]), int(parts[2]))
                    self.filter_bar.get_date_from().setMaximumDate(qd_max)
                    self.filter_bar.get_date_to().setMaximumDate(qd_max)
                    self.filter_bar.get_date_to().setDate(qd_max)
        except Exception:
            pass

    # ═══════════════════════════════════════════
    #  HELPER: Extract TRC filter code from SharedFilterBar
    # ═══════════════════════════════════════════

    def _get_trc_filter_code(self):
        """Extract the TRC code from the filter bar combo text.
        Returns None for 'All TRCs', or the code portion (before ' — ')."""
        filters = self.filter_bar.get_filters()
        trc_text = filters.get("trc", "All TRCs")
        if trc_text == "All TRCs" or not trc_text:
            return None
        # Format is "CODE — Label", extract code before the dash
        if " \u2014 " in trc_text:
            return trc_text.split(" \u2014 ")[0].strip()
        return trc_text.strip()

    # ═══════════════════════════════════════════
    #  SCAN EXECUTION
    # ═══════════════════════════════════════════

    def _on_run_scan(self):
        """Run incident scan via the unified job queue."""
        if self._worker and self._worker.isRunning():
            return

        # Ensure count tables are populated
        try:
            self.db.populate_daily_counts()
            self.db.populate_hourly_counts()
        except Exception:
            pass

        self.sync_date_to_data()
        self._scan_start_time = time.time()
        self._show_loading()  # T12: show skeleton while scanning

        main_win = self.window()
        if hasattr(main_win, '_job_queue'):
            job = main_win._make_incident_scan_job()
            main_win._job_queue.submit(job)
        else:
            self._run_scan_directly()

    def _run_scan_directly(self):
        """Run incident scan without the job queue (fallback)."""
        date_from = self.filter_bar.get_date_from().date().toString("yyyy-MM-dd")
        date_to = self.filter_bar.get_date_to().date().toString("yyyy-MM-dd")
        source_id = self._source_selector.selected_source_id() if hasattr(self, '_source_selector') else None
        self._scan_start_time = time.time()
        self._worker = IncidentWorker(self.db.db_path, date_to, date_from, source_id=source_id)
        self._worker.finished.connect(self._on_scan_results)
        self._worker.error.connect(self._on_scan_error)
        self._worker.start()

    def auto_run_scan(self):
        """Auto-scan triggered after data import. Uses latest data date."""
        self.sync_date_to_data()
        date_from = self.filter_bar.get_date_from().date().toString("yyyy-MM-dd")
        date_to = self.filter_bar.get_date_to().date().toString("yyyy-MM-dd")

        if self._worker and self._worker.isRunning():
            return

        source_id = self._source_selector.selected_source_id() if hasattr(self, '_source_selector') else None
        self._scan_start_time = time.time()
        self._worker = IncidentWorker(self.db.db_path, date_to, date_from, source_id=source_id)
        self._worker.finished.connect(self._on_scan_results)
        self._worker.error.connect(self._on_scan_error)
        self._worker.start()

    def _on_scan_progress(self, msg, pct):
        """Progress callback — forwarded by job queue to overlay."""
        pass  # Handled by unified overlay now

    def _on_scan_results(self, result):
        """Handle scan results."""
        try:
            self.setUpdatesEnabled(False)  # Batch all UI updates
            self._show_content()  # T12: swap skeleton -> real content
            elapsed_ms = int((time.time() - getattr(self, '_scan_start_time', time.time())) * 1000)
            self._last_result = result

            # Apply TRC filter
            trc_filter = self._get_trc_filter_code()
            trc_results = result.get("trc_results", [])
            if trc_filter:
                trc_results = [r for r in trc_results if r.get("trc_code") == trc_filter]

            # Show/hide empty state based on results
            self._empty_state.setVisible(not trc_results)

            # Overview KPIs
            total_trcs = len(trc_results)
            flagged = sum(1 for r in trc_results if r.get("flag_level", 0) > 0)
            critical = sum(1 for r in trc_results if r.get("flag_level", 0) >= 2)
            self._kpi_total_trcs.set_value(str(total_trcs))
            self._kpi_flagged.set_value(str(flagged))
            self._kpi_active.set_value(str(critical))
            scan_date = result.get("scan_date", "")
            if scan_date:
                try:
                    dt = datetime.fromisoformat(scan_date) if "T" in scan_date else datetime.strptime(scan_date, "%Y-%m-%d")
                    self._kpi_scan_date.set_value(dt.strftime("%b %d"))
                    self._kpi_scan_date.set_subtitle(dt.strftime("%Y"))
                except (ValueError, TypeError):
                    self._kpi_scan_date.set_value(scan_date[:10])

            # Populate grid
            self._populate_status_grid(trc_results)

            # Populate open incidents
            self._populate_open_incidents()

            # Select first flagged TRC for chart, or first TRC
            self._auto_select_chart(trc_results)

            self.setUpdatesEnabled(True)  # End batch -- repaint once

            # Save report
            theta_2_count = sum(1 for r in result.get("trc_results", []) if r.get("flag_level", 0) == 2)
            theta_1_count = sum(1 for r in result.get("trc_results", []) if r.get("flag_level", 0) == 1)
            summary = {
                "theta_2_count": theta_2_count,
                "theta_1_count": theta_1_count,
                "trcs_scanned": result.get("trcs_scanned", 0),
                "open_flags_total": result.get("open_flags_total", 0),
            }
            self.db.save_report(
                page="incidents",
                parameters={
                    "scan_date": result.get("scan_date", ""),
                    "date_from": result.get("date_from", ""),
                },
                summary=summary,
                full_results=json.dumps(result, default=str),
                ticket_count=0,
                duration_ms=elapsed_ms,
            )
            self._reports_tab.refresh()

            # Emit badge signal
            self.scan_complete.emit(theta_2_count)
        except Exception:
            import traceback
            traceback.print_exc()
            self.setUpdatesEnabled(True)  # Ensure re-enabled on error
            self._show_content()

    def _on_scan_error(self, error_text):
        # Mark job as failed so queue advances immediately
        main_win = self.window()
        if hasattr(main_win, '_job_queue'):
            main_win._job_queue.mark_job_failed("incident_scan", error_text)
        # Defer error dialog so it doesn't block the job queue
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._show_scan_error(error_text))

    def _show_scan_error(self, error_text):
        self._show_content()  # T12: restore real content on error
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(self, "Scan Error", f"Incident scan failed:\n\n{error_text}")

    # ═══════════════════════════════════════════
    #  STATUS GRID
    # ═══════════════════════════════════════════

    def _populate_status_grid(self, trc_results):
        """Fill the TRC status grid table with Poisson + CUSUM data."""
        # Sort: flagged TRCs first (2θ, then 1θ), then by p_value ascending
        trc_results = sorted(
            trc_results,
            key=lambda r: (-r.get("flag_level", 0), r.get("p_value_daily", 1.0))
        )

        # Store full dataset for pagination & row-click mapping
        self._grid_all_results = trc_results
        self._grid_pager.set_total(len(trc_results))
        self._show_grid_page(0)

    def _on_grid_page_changed(self, page: int):
        """Handle status grid pagination."""
        self._show_grid_page(page)

    def _show_grid_page(self, page: int):
        """Render one page of TRC results into the status grid."""
        ps = self._grid_pager.page_size
        start = page * ps
        end = min(start + ps, len(self._grid_all_results))
        page_results = self._grid_all_results[start:end]

        layman = is_layman_mode()

        # Update headers if layman mode
        if layman:
            headers = ["TRC", "Tier", "Today", "Expected", "Warning", "Critical", "Confidence", "Drift", "Status"]
        else:
            headers = ["TRC", "Tier", "Today", "\u03bb", "\u03b8\u2081", "\u03b8\u2082", "p-value", "CUSUM", "Status"]
        self.status_table.setHorizontalHeaderLabels(headers)

        self.status_table.setSortingEnabled(False)
        self.status_table.setRowCount(len(page_results))
        # _trc_results tracks what's visible for row-click -> control chart
        self._trc_results = page_results

        for i, r in enumerate(page_results):
            # Col 0: TRC
            trc_item = QTableWidgetItem(r.get("trc_code", ""))
            if r.get("flag_level", 0) >= 1:
                font = trc_item.font()
                font.setBold(True)
                trc_item.setFont(font)
            self.status_table.setItem(i, 0, trc_item)

            # Col 1: Tier
            tier = r.get("tier", 1)
            tier_item = QTableWidgetItem(f"T{tier}")
            if tier == 2:
                tier_item.setForeground(QColor(ALMA_INFO))
            self.status_table.setItem(i, 1, tier_item)

            # Col 2: Today (observed)
            obs = r.get("observed_today", 0)
            today_item = QTableWidgetItem(str(obs))
            today_item.setData(Qt.UserRole, obs)
            if r.get("flag_level", 0) >= 2:
                today_item.setForeground(QColor(ALMA_ERROR))
                font = today_item.font()
                font.setBold(True)
                today_item.setFont(font)
            elif r.get("flag_level", 0) >= 1:
                today_item.setForeground(QColor(ALMA_WARNING))
            self.status_table.setItem(i, 2, today_item)

            # Col 3: λ
            lam = r.get("lambda_daily", 0)
            lam_item = QTableWidgetItem(f"{lam:.1f}")
            lam_item.setData(Qt.UserRole, lam)
            lam_item.setForeground(QColor(ALMA_TEXT_LIGHT))
            self.status_table.setItem(i, 3, lam_item)

            # Col 4: θ₁
            t1 = r.get("theta_1_daily", 0)
            t1_item = QTableWidgetItem(str(t1))
            t1_item.setData(Qt.UserRole, t1)
            t1_item.setForeground(QColor(ALMA_WARNING))
            self.status_table.setItem(i, 4, t1_item)

            # Col 5: θ₂
            t2 = r.get("theta_2_daily", 0)
            t2_item = QTableWidgetItem(str(t2))
            t2_item.setData(Qt.UserRole, t2)
            t2_item.setForeground(QColor(ALMA_ERROR))
            self.status_table.setItem(i, 5, t2_item)

            # Col 6: p-value
            p = r.get("p_value_daily", 1.0)
            p_text = format_pvalue(p, layman) if layman else (f"{p:.3f}" if p < 0.1 else f"{p:.2f}")
            p_item = QTableWidgetItem(p_text)
            p_item.setData(Qt.UserRole, p)
            if p < 0.025:
                p_item.setForeground(QColor(ALMA_ERROR))
            elif p < 0.10:
                p_item.setForeground(QColor(ALMA_WARNING))
            else:
                p_item.setForeground(QColor(ALMA_TEXT_LIGHT))
            self.status_table.setItem(i, 6, p_item)

            # Col 7: CUSUM (value/threshold as text + visual hint)
            cusum_v = r.get("cusum_value", 0.0)
            cusum_h = r.get("cusum_threshold", 1.0)
            cusum_text = format_cusum(cusum_v, cusum_h, layman) if layman else f"{cusum_v:.1f}/{cusum_h:.0f}"
            cusum_item = QTableWidgetItem(cusum_text)
            cusum_item.setData(Qt.UserRole, cusum_v)
            ratio = cusum_v / cusum_h if cusum_h > 0 else 0
            if ratio > 0.8:
                cusum_item.setForeground(QColor(ALMA_ERROR))
            elif ratio > 0.5:
                cusum_item.setForeground(QColor(ALMA_WARNING))
            else:
                cusum_item.setForeground(QColor(ALMA_SUCCESS))
            if r.get("cusum_alert"):
                font = cusum_item.font()
                font.setBold(True)
                cusum_item.setFont(font)
            self.status_table.setItem(i, 7, cusum_item)

            # Col 8: Status
            level = r.get("flag_level", 0)
            if level >= 2:
                status_item = QTableWidgetItem(format_theta_level(2, layman) if layman else "2\u03b8")
                status_item.setForeground(QColor(ALMA_ERROR))
                for col in range(9):
                    cell = self.status_table.item(i, col)
                    if cell:
                        cell.setBackground(QColor(ALMA_ERROR).lighter(190))
            elif level >= 1:
                status_item = QTableWidgetItem(format_theta_level(1, layman) if layman else "1\u03b8 Watch")
                status_item.setForeground(QColor(ALMA_WARNING))
                for col in range(9):
                    cell = self.status_table.item(i, col)
                    if cell:
                        cell.setBackground(QColor(ALMA_WARNING).lighter(190))
            else:
                if r.get("insufficient_data"):
                    status_item = QTableWidgetItem("No Data")
                    status_item.setForeground(QColor(ALMA_TEXT_LIGHT))
                else:
                    status_item = QTableWidgetItem("OK")
                    status_item.setForeground(QColor(ALMA_SUCCESS))

            self.status_table.setItem(i, 8, status_item)

        self.status_table.setSortingEnabled(True)

    def _on_trc_row_selected(self, current_row, current_col, prev_row, prev_col):
        """When a TRC row is clicked, update the control chart."""
        if current_row < 0 or current_row >= len(self._trc_results):
            return
        result = self._trc_results[current_row]
        self.control_chart.set_data(result)

    def _on_trc_row_drilldown(self, index):
        """Double-click a TRC row to drill into its tickets."""
        if not self._drilldown:
            return
        row = index.row()
        if row < 0 or row >= len(self._trc_results):
            return
        result = self._trc_results[row]
        trc_code = result.get("trc_code", "")
        date_from = self.filter_bar.get_date_from().date().toString("yyyy-MM-dd")
        date_to = self.filter_bar.get_date_to().date().toString("yyyy-MM-dd") + "T23:59:59"

        tickets = self.db.search_conversations(
            trc_code=trc_code, date_from=date_from, date_to=date_to,
        )
        total = len(tickets)
        self._drilldown.show_tickets(
            f"TRC: {trc_code}",
            f"{total} ticket{'s' if total != 1 else ''}",
            tickets,
        )

    def _drilldown_incident(self, trc_code, target_date):
        """Drill into tickets for an incident flag's TRC and date."""
        if not self._drilldown or not trc_code:
            return
        # Use the incident date as a tight date filter
        date_from = target_date or self.filter_bar.get_date_from().date().toString("yyyy-MM-dd")
        date_to = target_date + "T23:59:59" if target_date else self.filter_bar.get_date_to().date().toString("yyyy-MM-dd") + "T23:59:59"

        tickets = self.db.search_conversations(
            trc_code=trc_code, date_from=date_from, date_to=date_to,
        )
        total = len(tickets)
        self._drilldown.show_tickets(
            f"Incident: {trc_code}",
            f"{target_date} \u2014 {total} ticket{'s' if total != 1 else ''}",
            tickets,
        )

    def _auto_select_chart(self, trc_results):
        """Select the first flagged TRC for the chart, or the first TRC."""
        if not trc_results:
            self.control_chart.clear_data()
            return

        # Find first 2θ, then first 1θ, then highest volume
        target = None
        for r in trc_results:
            if r.get("flag_level", 0) == 2:
                target = r
                break
        if target is None:
            for r in trc_results:
                if r.get("flag_level", 0) == 1:
                    target = r
                    break
        if target is None:
            target = max(trc_results, key=lambda r: r.get("observed_today", 0))

        self.control_chart.set_data(target)

        target_trc = target.get("trc_code", "")
        for i, r in enumerate(trc_results):
            if r.get("trc_code", "") == target_trc:
                self.status_table.selectRow(i)
                break

    # ═══════════════════════════════════════════
    #  OPEN INCIDENTS
    # ═══════════════════════════════════════════

    def _populate_open_incidents(self):
        """Build cards for open/acknowledged incident flags."""
        parent = self.incidents_container.parentWidget() or self
        parent.setUpdatesEnabled(False)
        try:
            self._populate_open_incidents_inner()
        finally:
            parent.setUpdatesEnabled(True)

    def _populate_open_incidents_inner(self):
        # Clear existing cards
        while self.incidents_container.count() > 0:
            item = self.incidents_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        try:
            from src.data.incident_engine import get_open_flags
            flags = get_open_flags(self.db, limit=200)
        except Exception:
            import traceback
            traceback.print_exc()
            flags = []

        self._open_incident_flags = flags

        # Update Open Incidents KPIs
        open_count = len(flags)
        critical_count = sum(1 for f in flags if f.get("theta_level", 0) >= 2 and f.get("status") == "open")
        watch_count = sum(1 for f in flags if f.get("theta_level", 0) == 1 and f.get("status") == "open")
        ack_count = sum(1 for f in flags if f.get("status") == "acknowledged")
        self._kpi_open.set_value(str(open_count))
        self._kpi_critical.set_value(str(critical_count))
        self._kpi_watch.set_value(str(watch_count))
        self._kpi_ack.set_value(str(ack_count))

        if not flags:
            no_label = QLabel("No open incidents.")
            no_label.setStyleSheet(f"color: {ALMA_TEXT_LIGHT}; font-size: 12px; border: none; padding: 12px;")
            no_label.setAlignment(Qt.AlignCenter)
            self.incidents_container.addWidget(no_label)
            return

        # Paginate: show 10 per page to avoid widget bloat
        self._oi_page = getattr(self, "_oi_page", 0)
        page_size = 10
        total_pages = max(1, (len(flags) + page_size - 1) // page_size)
        self._oi_page = max(0, min(self._oi_page, total_pages - 1))
        start = self._oi_page * page_size
        page_flags = flags[start : start + page_size]

        for flag in page_flags:
            try:
                card = self._build_incident_card(flag)
                self.incidents_container.addWidget(card)
            except Exception:
                import traceback
                traceback.print_exc()

        # Pagination controls
        if total_pages > 1:
            page_row = QHBoxLayout()
            page_row.addStretch()
            if self._oi_page > 0:
                prev_btn = QPushButton("\u25c0 Previous")
                prev_btn.setObjectName("IncidentPageBtn")
                prev_btn.setCursor(Qt.PointingHandCursor)
                prev_btn.clicked.connect(lambda: self._oi_go_page(self._oi_page - 1))
                page_row.addWidget(prev_btn)
            page_label = QLabel(f"Page {self._oi_page + 1} of {total_pages}  ({len(flags)} incidents)")
            page_label.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_MID}; border: none; padding: 0 8px;")
            page_row.addWidget(page_label)
            if self._oi_page < total_pages - 1:
                next_btn = QPushButton("Next \u25b6")
                next_btn.setObjectName("IncidentPageBtn")
                next_btn.setCursor(Qt.PointingHandCursor)
                next_btn.clicked.connect(lambda: self._oi_go_page(self._oi_page + 1))
                page_row.addWidget(next_btn)
            page_row.addStretch()
            page_w = QWidget()
            page_w.setLayout(page_row)
            self.incidents_container.addWidget(page_w)

    def _oi_go_page(self, page):
        self._oi_page = page
        self._populate_open_incidents()

    def _build_incident_card(self, flag):
        """Build a single incident card with action buttons."""
        card = QFrame()
        card.setProperty("flag_id", flag.get("flag_id"))
        level = flag.get("theta_level", 1)
        border_color = ALMA_ERROR if level >= 2 else ALMA_WARNING
        bg_tint = "rgba(196, 30, 30, 0.04)" if level >= 2 else "rgba(180, 83, 9, 0.04)"

        card.setStyleSheet(f"""
            QFrame {{
                background: {bg_tint};
                border: none;
                border-left: 4px solid {border_color};
                border-radius: 10px;
                padding: 12px 16px;
            }}
        """)
        # NOTE: apply_card_shadow_soft removed -- 50 QGraphicsDropShadowEffects
        # with blur radius 10 cause massive scroll lag via per-frame GPU blur ops.

        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Description
        severity = "Incident" if level >= 2 else "Watch"
        flag_type = flag.get("flag_type", "poisson_daily")
        observed = flag.get("observed_value", 0)
        lam = flag.get("expected_lambda", 0)
        p_val = flag.get("p_value")

        if flag_type == "cusum":
            cusum_v = flag.get("cusum_value", 0)
            desc = (
                f"{severity}: {flag['trc_code']} \u2014 CUSUM drift detected "
                f"({observed:.0f} tickets, expected ~{lam:.1f})"
            )
        elif flag_type == "poisson_hourly":
            hour = flag.get("triggered_hour", "?")
            desc = (
                f"{severity}: {flag['trc_code']} \u2014 Hour {hour}: "
                f"{observed:.0f} tickets (expected ~{lam:.1f})"
            )
        else:
            desc = (
                f"{severity}: {flag['trc_code']} \u2014 {observed:.0f} tickets today "
                f"(expected ~{lam:.1f})"
            )

        desc_label = QLabel(desc)
        desc_label.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {ALMA_TEXT_DARK}; border: none; background: transparent;")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

        # p-value interpretation
        if p_val is not None and flag_type != "cusum":
            p_text = f"p={p_val:.3f} \u2014 this count or higher occurs ~{p_val*100:.1f}% of days"
            p_label = QLabel(p_text)
            p_label.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_MID}; border: none; background: transparent;")
            layout.addWidget(p_label)
        elif flag_type == "cusum":
            cusum_v = flag.get("cusum_value", 0)
            cusum_text = f"Sustained elevation over recent days (CUSUM accumulator: {cusum_v:.1f})"
            cusum_label = QLabel(cusum_text)
            cusum_label.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_MID}; border: none; background: transparent;")
            layout.addWidget(cusum_label)

        # Trigger time + status
        triggered = flag.get("triggered_at", "")
        try:
            dt = datetime.fromisoformat(triggered)
            time_str = dt.strftime("%b %d %H:%M")
        except (ValueError, TypeError):
            time_str = triggered
        status = flag.get("status", "open")
        meta_text = f"Triggered: {time_str}  |  Type: {flag_type}  |  Status: {status}"
        meta_label = QLabel(meta_text)
        meta_label.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none; background: transparent;")
        layout.addWidget(meta_label)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        flag_id = flag["flag_id"]

        if status == "open":
            ack_btn = QPushButton("Acknowledge")
            ack_btn.setObjectName("IncidentAckBtn")
            ack_btn.setCursor(Qt.PointingHandCursor)
            ack_btn.clicked.connect(lambda checked, fid=flag_id: self._action_acknowledge(fid))
            btn_row.addWidget(ack_btn)

        resolve_btn = QPushButton("Resolve")
        resolve_btn.setObjectName("IncidentResolveBtn")
        resolve_btn.setCursor(Qt.PointingHandCursor)
        resolve_btn.clicked.connect(lambda checked, fid=flag_id: self._action_resolve(fid))
        btn_row.addWidget(resolve_btn)

        fp_btn = QPushButton("False Positive")
        fp_btn.setObjectName("IncidentFPBtn")
        fp_btn.setCursor(Qt.PointingHandCursor)
        fp_btn.clicked.connect(lambda checked, fid=flag_id: self._action_false_positive(fid))
        btn_row.addWidget(fp_btn)

        # View Tickets (drill-down)
        tickets_btn = QPushButton("View Tickets")
        tickets_btn.setObjectName("IncidentViewBtn")
        tickets_btn.setCursor(Qt.PointingHandCursor)
        trc_code = flag.get("trc_code", "")
        flag_date = flag.get("target_date", "")
        tickets_btn.clicked.connect(
            lambda checked, trc=trc_code, dt=flag_date: self._drilldown_incident(trc, dt)
        )
        btn_row.addWidget(tickets_btn)

        layout.addLayout(btn_row)
        return card

    def _action_acknowledge(self, flag_id):
        from src.data.incident_engine import acknowledge_flag
        acknowledge_flag(self.db, flag_id)
        self._remove_incident_card_by_flag(flag_id)
        self._emit_badge_count()

    def _action_resolve(self, flag_id):
        from src.data.incident_engine import resolve_flag
        resolve_flag(self.db, flag_id)
        self._remove_incident_card_by_flag(flag_id)
        self._emit_badge_count()

    def _action_false_positive(self, flag_id):
        from src.data.incident_engine import mark_false_positive
        mark_false_positive(self.db, flag_id)
        self._remove_incident_card_by_flag(flag_id)
        self._emit_badge_count()

    def _remove_incident_card_by_flag(self, flag_id):
        """Remove a single card from the open incidents container (surgical update).
        Falls back to full rebuild if the card is not found."""
        # Remove from our cached flags list
        if hasattr(self, "_open_incident_flags"):
            self._open_incident_flags = [
                f for f in self._open_incident_flags if f.get("flag_id") != flag_id
            ]
        # Walk the layout and remove the card whose sender button matches
        for i in range(self.incidents_container.count()):
            item = self.incidents_container.itemAt(i)
            if item and item.widget():
                w = item.widget()
                # Check if this card has a button connected to the flag_id
                if w.property("flag_id") == flag_id:
                    self.incidents_container.takeAt(i)
                    w.deleteLater()
                    self._emit_badge_count()
                    return
        # Fallback: full rebuild if surgical removal didn't find it
        self._populate_open_incidents()

    def _emit_badge_count(self):
        """Count open 2θ flags and emit signal for sidebar badge."""
        count = self.db.conn.execute(
            "SELECT COUNT(*) as cnt FROM incident_flags WHERE status = 'open' AND theta_level = 2"
        ).fetchone()["cnt"]
        self.scan_complete.emit(count)

    # ═══════════════════════════════════════════
    #  θ EWMA SCAN
    # ═══════════════════════════════════════════

    def _run_theta_scan(self):
        """Launch θ EWMA anomaly detection scan via job queue."""
        if self._theta_worker and self._theta_worker.isRunning():
            return

        self._theta_scan_btn.setEnabled(False)
        self._theta_scan_btn.setText("Scanning...")
        self._theta_message.setText("Running \u03b8 anomaly scan...")
        self._theta_message.show()

        main_win = self.window()
        if hasattr(main_win, '_job_queue'):
            job = main_win._make_theta_scan_job()
            main_win._job_queue.submit(job)
        else:
            self._run_theta_directly()

    def _run_theta_directly(self):
        """Run theta scan without the job queue (fallback)."""
        self._theta_worker = ThetaWorker(self.db.db_path)
        self._theta_worker.progress.connect(self._on_theta_progress)
        self._theta_worker.finished.connect(self._on_theta_results)
        self._theta_worker.error.connect(self._on_theta_error)
        self._theta_worker.start()

    def auto_run_theta_scan(self):
        """Run theta scan silently (called after data import)."""
        if self._theta_worker and self._theta_worker.isRunning():
            return
        self._theta_worker = ThetaWorker(self.db.db_path)
        self._theta_worker.finished.connect(self._on_theta_results)
        self._theta_worker.error.connect(lambda _: None)  # Silent
        self._theta_worker.start()

    def _on_theta_progress(self, msg):
        self._theta_message.setText(msg)

    def _on_theta_results(self, result):
        try:
            self._theta_scan_btn.setEnabled(True)
            self._theta_scan_btn.setText("Run \u03b8 Scan")

            flags = result.get("flags", [])
            theta_2 = result.get("theta_2_count", 0)

            self._theta_date_range = result.get("date_range", "")
            self._theta_days_scanned = result.get("days_scanned", 0)

            self._populate_theta_flags(flags)
        except Exception:
            import traceback
            traceback.print_exc()
            self._theta_scan_btn.setEnabled(True)
            self._theta_scan_btn.setText("Run \u03b8 Scan")

    def _on_theta_error(self, error_text):
        self._theta_scan_btn.setEnabled(True)
        self._theta_scan_btn.setText("Run \u03b8 Scan")
        self._theta_message.setText(f"\u03b8 scan failed: {error_text[:100]}")
        self._theta_message.show()

    def _populate_theta_flags(self, flags):
        """Display EWMA anomaly flags as colored cards with pagination."""
        self._clear_layout(self._theta_container)

        if not flags:
            date_range = getattr(self, "_theta_date_range", "")
            days_scanned = getattr(self, "_theta_days_scanned", 0)
            range_info = f" ({date_range}, {days_scanned} days)" if date_range else ""
            self._theta_message.setText(
                f"No anomalies detected \u2014 all metrics within normal range{range_info}"
            )
            self._theta_message.show()
            self._theta_pager.set_total(0)
            return

        self._theta_message.hide()

        # Sort: 2θ first, then 1θ, then by abs(z_score)
        self._theta_all_flags = sorted(flags, key=lambda f: (-f["theta_level"], -abs(f["z_score"])))
        self._theta_pager.set_total(len(self._theta_all_flags))
        self._show_theta_page(0)

    def _on_theta_page_changed(self, page: int):
        """Handle theta flag card pagination."""
        self._show_theta_page(page)

    def _show_theta_page(self, page: int):
        """Render one page of theta flag cards."""
        parent = self._theta_container.parentWidget() or self
        parent.setUpdatesEnabled(False)
        try:
            self._show_theta_page_inner(page)
        finally:
            parent.setUpdatesEnabled(True)

    def _show_theta_page_inner(self, page: int):
        self._clear_layout(self._theta_container)

        ps = self._theta_pager.page_size
        start = page * ps
        end = min(start + ps, len(self._theta_all_flags))
        page_flags = self._theta_all_flags[start:end]

        for flag in page_flags:
            card = self._make_theta_card(flag)
            self._theta_container.addWidget(card)

        # Footer summary (always shows totals across all pages)
        all_flags = self._theta_all_flags
        theta_2_count = sum(1 for f in all_flags if f["theta_level"] == 2)
        theta_1_count = sum(1 for f in all_flags if f["theta_level"] == 1)
        date_range = getattr(self, "_theta_date_range", "")
        days_scanned = getattr(self, "_theta_days_scanned", 0)
        range_info = f"  |  {date_range} ({days_scanned} days)" if date_range else ""
        footer = QLabel(
            f"Total: {theta_2_count} critical (2\u03b8) + {theta_1_count} watch (1\u03b8) anomalies{range_info}"
        )
        footer.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; border: none; margin-top: 4px;")
        footer.setAlignment(Qt.AlignCenter)
        self._theta_container.addWidget(footer)

    def _make_theta_card(self, flag):
        """Create a single EWMA anomaly flag card."""
        card = QFrame()
        theta = flag.get("theta_level", 1)
        layman = is_layman_mode()
        border_color = ALMA_ERROR if theta == 2 else ALMA_WARNING

        card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED};
                border: none;
                border-left: 4px solid {border_color};
                border-radius: 10px;
                padding: 8px;
            }}
        """)
        # NOTE: apply_card_shadow_soft removed -- per-card shadow effects cause scroll lag

        outer = QHBoxLayout(card)
        outer.setContentsMargins(12, 8, 12, 8)
        outer.setSpacing(12)

        # θ badge
        badge_bg = "rgba(196,30,30,0.15)" if theta == 2 else "rgba(180,83,9,0.15)"
        badge_color = ALMA_ERROR if theta == 2 else ALMA_WARNING
        badge_text = format_theta_level(theta, layman) if layman else f"{theta}\u03b8"
        badge = QLabel(badge_text)
        badge.setStyleSheet(
            f"background: {badge_bg}; color: {badge_color}; "
            f"border-radius: 12px; padding: 4px 10px; "
            f"font-size: 14px; font-weight: 800; border: none; min-width: 36px;"
        )
        badge.setAlignment(Qt.AlignCenter)
        outer.addWidget(badge)

        # Text content
        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        trc = flag.get("trc_code", "")
        metric = flag.get("metric_type", "")
        metric_key = flag.get("metric_key", "")
        flag_date = flag.get("date", "")
        metric_display = {
            "volume": "Volume",
            "volume_per_hour": "Ticket Rate",
            "sentiment": "Sentiment",
            "term_freq": "Term Frequency",
        }.get(metric, metric)
        header_text = f"{trc}  \u2022  {metric_display}"
        if metric_key:
            header_text += f": {metric_key}"
        if flag_date:
            header_text += f"  \u2022  {flag_date}"
        header = QLabel(header_text)
        header.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        text_col.addWidget(header)

        observed = flag.get("observed", 0)
        expected = flag.get("expected_mean", 0)
        std = flag.get("expected_std", 0)
        z = flag.get("z_score", 0)
        if layman:
            z_text = format_zscore(z, True)
            if metric == "volume":
                detail_text = f"{int(observed)} tickets (normally ~{expected:.0f})  |  {z_text}"
            elif metric == "volume_per_hour":
                detail_text = f"{observed:.1f}/hr (normally ~{expected:.1f}/hr)  |  {z_text}"
            elif metric == "sentiment":
                from src.ui.layman_mode import format_sentiment as fmt_sent
                detail_text = f"Mood: {fmt_sent(observed, True)} (normally {fmt_sent(expected, True)})  |  {z_text}"
            else:
                detail_text = f"Observed: {observed:.3f} (normally {expected:.3f})  |  {z_text}"
        else:
            if metric == "volume":
                detail_text = (
                    f"Volume: {int(observed)} tickets  |  "
                    f"Normal: {expected:.0f} \u00b1 {std:.1f}  |  z = {z:+.2f}"
                )
            elif metric == "volume_per_hour":
                detail_text = (
                    f"Rate: {observed:.1f}/hr  |  "
                    f"Normal: {expected:.1f} \u00b1 {std:.1f}/hr  |  z = {z:+.2f}"
                )
            elif metric == "sentiment":
                detail_text = (
                    f"Score: {observed:+.2f}  |  "
                    f"Normal: {expected:+.2f} \u00b1 {std:.2f}  |  z = {z:+.2f}"
                )
            else:
                detail_text = (
                    f"Observed: {observed:.3f}  |  "
                    f"Expected: {expected:.3f} \u00b1 {std:.3f}  |  z = {z:+.2f}"
                )
        detail = QLabel(detail_text)
        detail.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_MID}; border: none;")
        text_col.addWidget(detail)

        interp = QLabel(flag.get("interpretation", ""))
        interp.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; border: none;")
        interp.setWordWrap(True)
        text_col.addWidget(interp)

        outer.addLayout(text_col, 1)

        # Action buttons
        btn_col = QVBoxLayout()
        btn_col.setSpacing(4)

        ack_btn = QPushButton("Acknowledge")
        ack_btn.setObjectName("IncidentAckBtn")
        ack_btn.setCursor(Qt.PointingHandCursor)
        flag_data = dict(flag)
        ack_btn.clicked.connect(lambda _, f=flag_data: self._acknowledge_theta_flag(f))
        btn_col.addWidget(ack_btn)

        fp_btn = QPushButton("False +")
        fp_btn.setObjectName("IncidentFPBtn")
        fp_btn.setCursor(Qt.PointingHandCursor)
        fp_btn.clicked.connect(lambda _, f=flag_data: self._false_positive_theta_flag(f))
        btn_col.addWidget(fp_btn)

        # View Tickets (drill-down)
        tickets_btn = QPushButton("View Tickets")
        tickets_btn.setObjectName("IncidentViewBtn")
        tickets_btn.setCursor(Qt.PointingHandCursor)
        trc_code = flag.get("trc_code", "")
        flag_date = flag.get("date", "")
        tickets_btn.clicked.connect(
            lambda _, trc=trc_code, dt=flag_date: self._drilldown_incident(trc, dt)
        )
        btn_col.addWidget(tickets_btn)

        outer.addLayout(btn_col)

        return card

    def _acknowledge_theta_flag(self, flag):
        """Mark an EWMA theta flag as acknowledged."""
        try:
            conn = get_connection(self.db.db_path)
            from src.data.theta_engine import acknowledge_flag

            row = conn.execute(
                "SELECT flag_id FROM anomaly_flags WHERE date = ? AND trc_code = ? "
                "AND metric_type = ? AND metric_key = ? AND status = 'open' LIMIT 1",
                (flag.get("date", ""), flag.get("trc_code", ""),
                 flag.get("metric_type", ""), flag.get("metric_key", ""))
            ).fetchone()
            if row:
                fid = row["flag_id"] if hasattr(row, "keys") else row[0]
                acknowledge_flag(conn, fid)
            conn.close()
        except Exception:
            pass

    def _false_positive_theta_flag(self, flag):
        """Mark an EWMA theta flag as false positive."""
        try:
            conn = get_connection(self.db.db_path)
            from src.data.theta_engine import mark_false_positive

            row = conn.execute(
                "SELECT flag_id FROM anomaly_flags WHERE date = ? AND trc_code = ? "
                "AND metric_type = ? AND metric_key = ? AND status = 'open' LIMIT 1",
                (flag.get("date", ""), flag.get("trc_code", ""),
                 flag.get("metric_type", ""), flag.get("metric_key", ""))
            ).fetchone()
            if row:
                fid = row["flag_id"] if hasattr(row, "keys") else row[0]
                mark_false_positive(conn, fid)
            conn.close()
        except Exception:
            pass

    def _toggle_theta_history(self):
        """Show a dialog with historical EWMA theta flags."""
        try:
            conn = get_connection(self.db.db_path)
            from src.data.theta_engine import get_flag_history

            flags = get_flag_history(conn, days=30, limit=200)
            conn.close()
        except Exception:
            flags = []

        from PySide6.QtWidgets import QDialog, QDialogButtonBox

        dlg = QDialog(self)
        dlg.setWindowTitle("\u03b8 Flag History \u2014 Last 30 Days")
        dlg.setMinimumSize(800, 500)
        dlg_layout = QVBoxLayout(dlg)

        table = QTableWidget()
        table.setColumnCount(8)
        table.setHorizontalHeaderLabels([
            "Date", "TRC", "Metric", "Key", "\u03b8 Level", "Z-Score", "Status", "Notes"
        ])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        for i in range(4, 8):
            table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeToContents)
        table.verticalHeader().setVisible(False)
        configure_table(table)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        table.setRowCount(len(flags))
        for i, f in enumerate(flags):
            table.setItem(i, 0, QTableWidgetItem(f.get("date", "")))
            table.setItem(i, 1, QTableWidgetItem(f.get("trc_code", "")))
            table.setItem(i, 2, QTableWidgetItem(f.get("metric_type", "")))
            table.setItem(i, 3, QTableWidgetItem(f.get("metric_key", "")))

            theta_item = QTableWidgetItem(f"{f.get('theta_level', '')}\u03b8")
            theta_level = f.get("theta_level", 0)
            theta_item.setForeground(QColor(ALMA_ERROR if theta_level == 2 else ALMA_WARNING))
            table.setItem(i, 4, theta_item)

            z = f.get("z_score", 0)
            table.setItem(i, 5, QTableWidgetItem(f"{z:+.2f}" if z else ""))

            status = f.get("status", "")
            status_item = QTableWidgetItem(status)
            status_colors = {
                "open": ALMA_ERROR, "acknowledged": ALMA_INFO,
                "resolved": ALMA_SUCCESS, "false_positive": ALMA_TEXT_LIGHT,
            }
            status_item.setForeground(QColor(status_colors.get(status, ALMA_TEXT_MID)))
            table.setItem(i, 6, status_item)

            table.setItem(i, 7, QTableWidgetItem(f.get("notes", "")))

        dlg_layout.addWidget(table)

        btn = QDialogButtonBox(QDialogButtonBox.Close)
        btn.rejected.connect(dlg.reject)
        dlg_layout.addWidget(btn)

        dlg.exec()

    @staticmethod
    def _clear_layout(layout):
        """Remove all widgets from a layout."""
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    # ═══════════════════════════════════════════
    #  REPORT HISTORY — RELOAD PAST REPORT
    # ═══════════════════════════════════════════

    def _open_report_history(self):
        """Open the DrilldownPanel with the full report history list."""
        if not hasattr(self, "_drilldown") or not self._drilldown:
            return
        reports = self._reports_tab._history.get_reports_for_drilldown()
        count = len(reports)
        self._drilldown.show_reports(
            "Incident Reports",
            f"{count} report{'s' if count != 1 else ''}",
            reports,
            detail_callback=self._render_report_detail_html,
            load_callback=self._load_past_report,
        )

    def _render_report_detail_html(self, report_id):
        """Render an incident report as HTML for the DrilldownPanel."""
        report = self.db.get_full_report(report_id)
        if not report:
            return "<p>Report not found.</p>"

        raw = report.get("full_results", "")
        try:
            result = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return "<p>Could not parse report data.</p>"

        html = ['<div style="font-family: Segoe UI, sans-serif; font-size: 13px;">']

        # -- Overview --
        flags = result.get("flags", [])
        theta_2 = [f for f in flags if f.get("flag_level") == 2]
        theta_1 = [f for f in flags if f.get("flag_level") == 1]
        html.append("<h3 style='margin: 12px 0 6px;'>Scan Summary</h3>")
        html.append(f"<p>Incidents (\u03b8\u2082): <b>{len(theta_2)}</b> &nbsp;|&nbsp; "
                     f"Watches (\u03b8\u2081): <b>{len(theta_1)}</b></p>")

        # -- TRC Breakdown --
        trc_results = result.get("trc_results", [])
        flagged = [r for r in trc_results if r.get("flag_level", 0) > 0]
        if flagged:
            html.append(f"<h3 style='margin: 12px 0 6px;'>Flagged TRCs ({len(flagged)})</h3>")
            html.append("<table cellpadding='3' style='border-collapse: collapse; width: 100%;'>")
            html.append("<tr style='border-bottom: 1px solid #E8E5DE;'>"
                        "<th align='left'>TRC</th><th align='right'>Observed</th>"
                        "<th align='right'>Expected</th><th align='right'>Level</th></tr>")
            for r in flagged[:25]:
                trc = r.get("trc_code", "")
                obs = r.get("observed", 0)
                exp = r.get("lambda_hat", r.get("expected", 0))
                lvl = r.get("flag_level", 0)
                lvl_str = "\u03b8\u2082 Incident" if lvl == 2 else "\u03b8\u2081 Watch"
                html.append(
                    f"<tr style='border-bottom: 1px solid #F0F0F0;'>"
                    f"<td>{trc}</td><td align='right'>{obs}</td>"
                    f"<td align='right'>{exp:.1f}</td><td align='right'>{lvl_str}</td></tr>"
                )
            html.append("</table>")

        # -- Theta flags --
        if flags:
            html.append(f"<h3 style='margin: 12px 0 6px;'>\u03b8 EWMA Flags ({len(flags)})</h3>")
            for f in flags[:15]:
                trc = f.get("trc_code", "")
                metric = f.get("metric", "")
                z = f.get("z_score", 0)
                lvl = f.get("flag_level", 0)
                html.append(
                    f"<p style='margin: 3px 0;'>"
                    f"<b>{trc}</b> \u2014 {metric}, z={z:.2f} "
                    f"({'\u03b8\u2082' if lvl == 2 else '\u03b8\u2081'})</p>"
                )

        html.append("</div>")
        return "".join(html)

    def _load_past_report(self, report_id):
        """Reload a past scan's results into the main view."""
        report = self.db.get_full_report(report_id)
        if not report or not report.get("full_results"):
            return
        try:
            result = json.loads(report["full_results"])
        except (json.JSONDecodeError, TypeError):
            return

        self._last_result = result
        trc_results = result.get("trc_results", [])

        # Apply TRC filter
        trc_filter = self._get_trc_filter_code()
        if trc_filter:
            trc_results = [r for r in trc_results if r["trc_code"] == trc_filter]

        self._populate_status_grid(trc_results)
        self._auto_select_chart(trc_results)
        # Don't reload open incidents -- those are live state
