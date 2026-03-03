"""
Alma Insights — TRC Analytics Dashboard
Volume charts, resolution times, CSAT heatmaps, and filterable metrics table.

Refactored to use AnalysisPageBase building blocks with 5 tabs:
  Tab 1 - Overview:     KPI cards + volume bar chart + resolution box plot
  Tab 2 - NLP Scanner:  Full scan config, controls, live monitor, history
  Tab 3 - CSAT Heatmaps: CSAT KPI row + heatmap + pagination + metrics table
  Tab 4 - SubTaxonomy:  TaxonomyBrowser (signals wired to drilldown)
  Tab 5 - Reports:      ReportsTab with report history
"""

import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QSizePolicy, QComboBox, QApplication,
)
from PySide6.QtCore import Qt, QDate, QThread, Signal, QTimer
from PySide6.QtGui import QColor

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_MID, ALMA_GREEN_SUBTLE,
    ALMA_WHITE, ALMA_CREAM,
    ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT, ALMA_TEXT_ON_DARK,
    ALMA_BORDER, ALMA_BORDER_LIGHT,
    ALMA_WARNING, ALMA_ERROR, ALMA_SUCCESS,
    ALMA_BG_ELEVATED, apply_card_shadow, apply_card_shadow_soft, configure_table,
)
from src.ui.widgets.charts import BarChartWidget, BoxPlotWidget, HeatmapWidget
from src.ui.widgets.pagination_bar import PaginationBar
from src.ui.widgets.skeleton import SkeletonGroup
from src.ui.widgets.filter_chip_bar import FilterChipBar
from src.ui.widgets.analysis_page_base import AnalysisPageBase
from src.ui.widgets.kpi_card import KPICard, KPICardRow
from src.ui.widgets.tab_scroll_content import TabScrollContent
from src.ui.widgets.reports_tab import ReportsTab
from src.ui.widgets.scan_monitor import ScanMonitorWidget, ScanStatusPanel
from src.ui.widgets.date_picker import ModernDatePicker
from src.ui.widgets.collapsible_section import CollapsibleSection
from src.ui.layman_mode import is_layman_mode

logger = logging.getLogger("alma.trc_analytics")


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

class TRCAnalyticsPage(AnalysisPageBase):

    # NLP Scanner signals (forwarded from embedded widgets)
    scan_active_changed = Signal(bool)
    deep_dive_requested = Signal(str, str)
    view_tickets_requested = Signal(list)  # ticket_ids list

    def __init__(self, db_manager, parent=None):
        super().__init__(
            db_manager,
            "TRC Analytics Dashboard",
            subtitle="Ticket volume, resolution times, CSAT, and trends by TRC code",
            parent=parent,
        )
        self._worker = None
        self._scan_start_time = 0
        self._scan_mgr = None
        self._poll_timer = None
        self._active_scan_id = None

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

    @property
    def trc_combo(self):
        return self.filter_bar.get_combo("trc")

    @property
    def status_combo(self):
        return self.filter_bar.get_combo("status")

    # ═══════════════════════════════════════════
    #  DRILLDOWN
    # ═══════════════════════════════════════════

    def set_drilldown_panel(self, panel):
        super().set_drilldown_panel(panel)
        self._reports_tab.set_drilldown_panel(panel)

    # ═══════════════════════════════════════════
    #  FILTER BAR SETUP
    # ═══════════════════════════════════════════

    def _setup_filters(self):
        # v2: Action-first layout — primary button before filters
        self.filter_bar.add_primary_action(
            "Refresh",
            auto_refresh_options=["Auto-refresh off", "Every 30s", "Every 5m"],
        )
        self.filter_bar.add_date_range()
        self.filter_bar.add_combo_filter("trc", "TRC Code", ["All TRCs"])
        self.filter_bar.add_combo_filter(
            "status", "Status",
            ["All", "Open", "Pending", "Solved", "Closed"],
        )

        # TRC combo needs data role — re-populate with proper data
        trc_combo = self.filter_bar.get_combo("trc")
        trc_combo.clear()
        trc_combo.addItem("All TRCs", "")
        trc_combo.setMinimumWidth(160)

    # ═══════════════════════════════════════════
    #  ACTION / FILTER HOOKS
    # ═══════════════════════════════════════════

    def _on_filters_changed(self, filters):
        if hasattr(self, '_chip_bar'):
            self._sync_chips()
        # Guard: don't refresh during __init__ (tabs not built yet)
        if hasattr(self, '_skeleton'):
            self._on_refresh()

    def _on_action_triggered(self, action):
        if action == "Refresh":
            self._on_refresh()

    # ═══════════════════════════════════════════
    #  TAB SETUP
    # ═══════════════════════════════════════════

    def _setup_tabs(self):
        # Tab 1: Overview
        self._overview_tab = TabScrollContent()
        self._build_overview_tab()
        self.add_tab(self._overview_tab, "Overview")

        # Tab 2: NLP Scanner
        self._build_scanner_tab()

        # Tab 3: CSAT Heatmaps
        self._csat_tab = TabScrollContent()
        self._build_csat_tab()
        self.add_tab(self._csat_tab, "CSAT Heatmaps")

        # Tab 4: SubTaxonomy
        self._build_taxonomy_tab()

        # Tab 5: Reports
        self._reports_tab = ReportsTab("trc_analytics", self.db)
        self.add_tab(self._reports_tab, "Reports")

        # Tab switch handler — refresh scanner/taxonomy on switch
        self._tab_widget.currentChanged.connect(self._on_tab_changed)

    # ── TAB 1: OVERVIEW ──────────────────────────────

    def _build_overview_tab(self):
        layout = self._overview_tab.content_layout

        # KPI Card Row
        self._kpi_row = KPICardRow()
        self._kpi_total = self._kpi_row.add_card(
            KPICard("Total Tickets", "\u2014", "in selected range")
        )
        self._kpi_resolution = self._kpi_row.add_card(
            KPICard("Avg Resolution", "\u2014", "assignment \u2192 resolution")
        )
        self._kpi_first_reply = self._kpi_row.add_card(
            KPICard("Avg First Reply", "\u2014", "first agent response")
        )
        self._kpi_csat = self._kpi_row.add_card(
            KPICard("Avg CSAT", "\u2014", "satisfaction score")
        )
        self._kpi_row.apply_accent_cycle()
        layout.addWidget(self._kpi_row)

        # Filter Chip Bar
        self._chip_bar = FilterChipBar()
        self._chip_bar.filter_removed.connect(self._on_chip_removed)
        self._chip_bar.all_cleared.connect(self._on_chip_clear_all)
        layout.addWidget(self._chip_bar)

        # Skeleton loading placeholder
        self._build_skeleton(layout)

        # Charts row: Volume bar chart + Resolution box plot side by side
        self._charts_row = QWidget()
        charts_layout = QHBoxLayout(self._charts_row)
        charts_layout.setContentsMargins(0, 0, 0, 0)
        charts_layout.setSpacing(16)

        # Volume by TRC
        vol_frame = QFrame()
        vol_frame.setObjectName("VolPanel")
        vol_frame.setStyleSheet(
            f"#VolPanel {{ background: {ALMA_BG_ELEVATED};"
            " border: none; border-radius: 12px; }}"
        )
        apply_card_shadow_soft(vol_frame)
        vol_layout = QVBoxLayout(vol_frame)
        vol_layout.setContentsMargins(12, 8, 12, 8)
        vol_layout.setSpacing(2)
        vol_title = QLabel("Volume by TRC")
        vol_title.setAlignment(Qt.AlignCenter)
        vol_title.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK};"
            " background: transparent; border: none;"
        )
        vol_layout.addWidget(vol_title)
        self.bar_chart = BarChartWidget()
        self.bar_chart.setMinimumHeight(300)
        self.bar_chart.bar_clicked.connect(self._on_volume_bar_clicked)
        vol_layout.addWidget(self.bar_chart)
        self._bar_pager = PaginationBar(page_size=10)
        self._bar_pager.page_changed.connect(self.bar_chart.set_page)
        vol_layout.addWidget(self._bar_pager)
        charts_layout.addWidget(vol_frame, 1)

        # Resolution time distribution
        res_frame = QFrame()
        res_frame.setObjectName("ResPanel")
        res_frame.setStyleSheet(
            f"#ResPanel {{ background: {ALMA_BG_ELEVATED};"
            " border: none; border-radius: 12px; }}"
        )
        apply_card_shadow_soft(res_frame)
        res_layout = QVBoxLayout(res_frame)
        res_layout.setContentsMargins(12, 8, 12, 8)
        res_layout.setSpacing(2)
        res_title = QLabel("Resolution Time Distribution")
        res_title.setAlignment(Qt.AlignCenter)
        res_title.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK};"
            " background: transparent; border: none;"
        )
        res_layout.addWidget(res_title)
        self.box_chart = BoxPlotWidget()
        self.box_chart.setMinimumHeight(300)
        res_layout.addWidget(self.box_chart)
        self._box_pager = PaginationBar(page_size=10)
        self._box_pager.page_changed.connect(self.box_chart.set_page)
        res_layout.addWidget(self._box_pager)
        charts_layout.addWidget(res_frame, 1)

        layout.addWidget(self._charts_row)

        # Content sections for skeleton toggle
        self._content_sections = [self._kpi_row, self._charts_row]

        layout.addStretch()

    # ── TAB 2: NLP SCANNER ──────────────────────────────

    def _build_scanner_tab(self):
        self._scanner_tab = TabScrollContent()
        layout = self._scanner_tab.content_layout

        # Info panel
        self._scan_info_panel = self._build_scan_info_panel()
        layout.addWidget(self._scan_info_panel)

        # Scan config card (date range, TRC filter, budget, workers, estimator)
        self._scan_config_card = self._build_scan_config_card()
        layout.addWidget(self._scan_config_card)

        # Status panel (idle estimates / running 3-column stats)
        self._scan_status_panel = ScanStatusPanel()
        layout.addWidget(self._scan_status_panel)

        # Control buttons (Start, Pause, Resume, Cancel, Retry)
        self._scan_control_frame = self._build_scan_control_buttons()
        layout.addWidget(self._scan_control_frame)

        # Live scan monitor (hidden until scan starts)
        self._scan_monitor = ScanMonitorWidget(self.db)
        self._scan_monitor.scan_completed.connect(self._on_nlp_scan_completed)
        layout.addWidget(self._scan_monitor)

        # Scan history (collapsible)
        layout.addWidget(self._build_scan_history_section())

        # Docs panel (collapsible)
        self._scan_docs_section = self._build_scan_docs_panel()
        layout.addWidget(self._scan_docs_section)

        layout.addStretch()
        self.add_tab(self._scanner_tab, "NLP Scanner")

    # ── TAB 3: CSAT HEATMAPS ──────────────────────────────

    def _build_csat_tab(self):
        layout = self._csat_tab.content_layout

        # CSAT KPI Card Row
        self._csat_kpi_row = KPICardRow()
        self._csat_kpi_avg = self._csat_kpi_row.add_card(
            KPICard("Avg CSAT", "\u2014", "satisfaction score")
        )
        self._csat_kpi_low = self._csat_kpi_row.add_card(
            KPICard("Low CSAT TRCs", "\u2014", "below 3.0 threshold")
        )
        self._csat_kpi_range = self._csat_kpi_row.add_card(
            KPICard("CSAT Range", "\u2014", "min \u2192 max")
        )
        self._csat_kpi_coverage = self._csat_kpi_row.add_card(
            KPICard("CSAT Coverage", "\u2014", "TRCs with scores")
        )
        self._csat_kpi_row.apply_accent_cycle()
        layout.addWidget(self._csat_kpi_row)

        # CSAT Heatmap
        heatmap_frame = QFrame()
        heatmap_frame.setObjectName("HeatmapPanel")
        heatmap_frame.setStyleSheet(
            f"#HeatmapPanel {{ background: {ALMA_BG_ELEVATED};"
            " border: none; border-radius: 12px; }}"
        )
        apply_card_shadow_soft(heatmap_frame)
        hm_layout = QVBoxLayout(heatmap_frame)
        hm_layout.setContentsMargins(12, 8, 12, 8)
        hm_layout.setSpacing(2)
        hm_title = QLabel("CSAT Heatmap")
        hm_title.setAlignment(Qt.AlignCenter)
        hm_title.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK};"
            " background: transparent; border: none;"
        )
        hm_layout.addWidget(hm_title)
        self.heatmap = HeatmapWidget()
        self.heatmap.setMinimumHeight(200)
        self.heatmap.cell_clicked.connect(self._on_heatmap_cell_clicked)
        hm_layout.addWidget(self.heatmap)
        self._heatmap_pager = PaginationBar(page_size=10)
        self._heatmap_pager.page_changed.connect(self.heatmap.set_page)
        hm_layout.addWidget(self._heatmap_pager)
        layout.addWidget(heatmap_frame)

        # Metrics table
        metrics_frame = QFrame()
        metrics_frame.setObjectName("MetricsPanel")
        metrics_frame.setStyleSheet(
            f"#MetricsPanel {{ background: {ALMA_BG_ELEVATED};"
            " border: none; border-radius: 12px; }}"
        )
        apply_card_shadow_soft(metrics_frame)
        mt_layout = QVBoxLayout(metrics_frame)
        mt_layout.setContentsMargins(12, 8, 12, 8)
        mt_layout.setSpacing(2)
        mt_title = QLabel("Metrics by TRC")
        mt_title.setAlignment(Qt.AlignCenter)
        mt_title.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK};"
            " background: transparent; border: none;"
        )
        mt_layout.addWidget(mt_title)

        self.metrics_table = QTableWidget()
        headers = [
            "TRC Code", "Tickets", "Avg Res (hrs)", "Median Res (hrs)",
            "P95 Res (hrs)", "Avg First Reply (hrs)", "Avg CSAT",
            "% Solved", "Avg Messages", "Agent:Customer"
        ]
        self.metrics_table.setColumnCount(len(headers))
        self.metrics_table.setHorizontalHeaderLabels(headers)
        self.metrics_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch
        )
        for i in range(1, len(headers)):
            self.metrics_table.horizontalHeader().setSectionResizeMode(
                i, QHeaderView.ResizeToContents
            )
        self.metrics_table.verticalHeader().setVisible(False)
        configure_table(self.metrics_table)
        self.metrics_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.metrics_table.setAlternatingRowColors(True)
        self.metrics_table.setStyleSheet(
            "QTableWidget { alternate-background-color: rgba(3,40,27,0.02); border: none; }"
        )
        self.metrics_table.setSortingEnabled(True)
        self.metrics_table.setMinimumHeight(250)
        mt_layout.addWidget(self.metrics_table)

        self._metrics_pager = PaginationBar(page_size=15)
        self._metrics_pager.page_changed.connect(self._on_metrics_page_changed)
        mt_layout.addWidget(self._metrics_pager)
        self._metrics_all_rows = []

        layout.addWidget(metrics_frame)

        # Content sections for CSAT tab skeleton toggle
        self._csat_content_sections = [
            self._csat_kpi_row, heatmap_frame, metrics_frame,
        ]

        layout.addStretch()

    # ── TAB 4: SUBTAXONOMY ──────────────────────────────

    def _build_taxonomy_tab(self):
        from src.ui.widgets.taxonomy_browser import TaxonomyBrowser
        self._taxonomy = TaxonomyBrowser(self.db)
        self._taxonomy.deep_dive_requested.connect(self.deep_dive_requested.emit)
        self._taxonomy.view_tickets_requested.connect(self._on_view_tickets)
        self._taxonomy.pattern_selected.connect(self._on_pattern_selected)
        self.add_tab(self._taxonomy, "SubTaxonomy")

    # ═══════════════════════════════════════════
    #  SKELETON LOADING
    # ═══════════════════════════════════════════

    def _build_skeleton(self, layout):
        """Build shimmer loading placeholder matching the page layout."""
        self._skeleton = QWidget()
        skel_layout = QVBoxLayout(self._skeleton)
        skel_layout.setContentsMargins(0, 0, 0, 0)
        skel_layout.setSpacing(20)

        # KPI row skeleton
        skel_layout.addWidget(SkeletonGroup.kpi_row(4, self._skeleton))

        # Charts row skeleton (two side by side)
        charts_skel = QWidget(self._skeleton)
        h = QHBoxLayout(charts_skel)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(16)
        h.addWidget(SkeletonGroup.chart_area(charts_skel))
        h.addWidget(SkeletonGroup.chart_area(charts_skel))
        skel_layout.addWidget(charts_skel)

        # Table skeleton
        skel_layout.addWidget(SkeletonGroup.table_rows(5, self._skeleton))

        skel_layout.addStretch()
        self._skeleton.setVisible(False)
        layout.addWidget(self._skeleton)

    def _show_loading(self):
        """Show skeleton placeholders, hide real content."""
        self._skeleton.setVisible(True)
        for w in self._content_sections:
            w.setVisible(False)

    def _show_content(self):
        """Hide skeleton, show real content."""
        self._skeleton.setVisible(False)
        for w in self._content_sections:
            w.setVisible(True)

    # ═══════════════════════════════════════════
    #  FILTER CHIP BAR
    # ═══════════════════════════════════════════

    def _sync_chips(self):
        """Sync filter chip bar to current filter state."""
        active = {}
        trc_combo = self.trc_combo
        if trc_combo:
            trc = trc_combo.currentData()
            if trc:
                text = trc_combo.currentText()
                active["TRC"] = text.split(" \u2014 ")[0] if " \u2014 " in text else text
        status_combo = self.status_combo
        if status_combo:
            status = status_combo.currentText()
            if status != "All":
                active["Status"] = status
        self._chip_bar.set_filters(active)

    def _on_chip_removed(self, key):
        if key == "TRC":
            trc_combo = self.trc_combo
            if trc_combo:
                trc_combo.setCurrentIndex(0)
        elif key == "Status":
            status_combo = self.status_combo
            if status_combo:
                status_combo.setCurrentIndex(0)
        self._on_refresh()

    def _on_chip_clear_all(self):
        trc_combo = self.trc_combo
        if trc_combo:
            trc_combo.setCurrentIndex(0)
        status_combo = self.status_combo
        if status_combo:
            status_combo.setCurrentIndex(0)
        self._on_refresh()

    # ═══════════════════════════════════════════
    #  DISPLAY RESULTS
    # ═══════════════════════════════════════════

    def _display_results(self, result):
        """Populate all panels from a results dict (fresh or from history)."""
        self._show_content()
        self._sync_chips()
        layman = is_layman_mode()
        summary = result["summary"]

        # Overview KPIs
        total = summary["total_tickets"]
        self._kpi_total.set_value(f"{total:,}" if total else "\u2014")

        avg_res = summary["avg_resolution"]
        if layman and avg_res is not None:
            if avg_res < 1:
                self._kpi_resolution.set_value(f"{avg_res*60:.0f}min")
            elif avg_res > 24:
                self._kpi_resolution.set_value(f"{avg_res/24:.1f}d")
            else:
                self._kpi_resolution.set_value(f"{avg_res:.1f}h")
        else:
            self._kpi_resolution.set_value(
                f"{avg_res:.1f}h" if avg_res is not None else "\u2014"
            )

        avg_frt = summary["avg_first_reply"]
        if layman and avg_frt is not None:
            if avg_frt < 1:
                self._kpi_first_reply.set_value(f"{avg_frt*60:.0f}min")
            else:
                self._kpi_first_reply.set_value(f"{avg_frt:.1f}h")
        else:
            self._kpi_first_reply.set_value(
                f"{avg_frt:.1f}h" if avg_frt is not None else "\u2014"
            )

        avg_csat = summary["avg_csat"]
        if layman and avg_csat is not None:
            stars = "\u2605" * int(round(avg_csat))
            self._kpi_csat.set_value(f"{avg_csat:.1f} {stars}")
        else:
            self._kpi_csat.set_value(
                f"{avg_csat:.1f}/5" if avg_csat is not None else "\u2014"
            )

        # CSAT tab KPIs
        if avg_csat is not None:
            self._csat_kpi_avg.set_value(f"{avg_csat:.1f}/5")
        else:
            self._csat_kpi_avg.set_value("\u2014")

        # Count low-CSAT TRCs from metrics table
        metrics = result.get("metrics_table") or []
        low_csat_count = sum(
            1 for r in metrics
            if r.get("avg_csat") is not None and r["avg_csat"] < 3.0
        )
        self._csat_kpi_low.set_value(str(low_csat_count) if metrics else "\u2014")

        # CSAT range
        csat_vals = [
            r["avg_csat"] for r in metrics
            if r.get("avg_csat") is not None
        ]
        if csat_vals:
            self._csat_kpi_range.set_value(
                f"{min(csat_vals):.1f} \u2192 {max(csat_vals):.1f}"
            )
            self._csat_kpi_coverage.set_value(
                f"{len(csat_vals)}/{len(metrics)}"
            )
        else:
            self._csat_kpi_range.set_value("\u2014")
            self._csat_kpi_coverage.set_value("\u2014")

        # Volume bar chart + paginator
        vol_data = result["volume_by_trc"]
        self.bar_chart.set_data(vol_data)
        self._bar_pager.set_total(len(vol_data) if vol_data else 0)

        # Resolution box plot + paginator
        res_data = result["resolution_by_trc"]
        self.box_chart.set_data(res_data, min_samples=5)
        self._box_pager.set_total(len(self.box_chart._all_labels))

        # CSAT Heatmap + paginator
        hm = result["csat_heatmap"]
        self.heatmap.set_data(hm["y_labels"], hm["x_labels"], hm["values"])
        self._heatmap_pager.set_total(
            len(hm["y_labels"]) if hm.get("y_labels") else 0
        )

        # Metrics table + paginator
        self._metrics_all_rows = result["metrics_table"] or []
        self._metrics_pager.set_total(len(self._metrics_all_rows))
        self._show_metrics_page(0)

    # ═══════════════════════════════════════════
    #  TRC FILTER POPULATION
    # ═══════════════════════════════════════════

    def populate_trc_filter(self):
        """Load TRC codes into the filter dropdown."""
        trcs = self.db.get_trc_codes()
        items = ["All TRCs"] + [f"{t['code']} \u2014 {t['label']}" for t in trcs]
        self.filter_bar.set_combo_items("trc", items)

        # Re-populate with data roles for backward compat
        trc_combo = self.trc_combo
        if trc_combo:
            trc_combo.blockSignals(True)
            current = trc_combo.currentText()
            trc_combo.clear()
            trc_combo.addItem("All TRCs", "")
            for t in trcs:
                trc_combo.addItem(f"{t['code']} \u2014 {t['label']}", t["code"])
            # Restore selection
            idx = trc_combo.findText(current)
            if idx >= 0:
                trc_combo.setCurrentIndex(idx)
            trc_combo.blockSignals(False)

    def sync_date_to_data(self):
        """Set date pickers to match the actual data range in the DB."""
        try:
            min_d, max_d = self.db.get_date_range()
            if min_d:
                parts = min_d[:10].split("-")
                if len(parts) == 3:
                    date_from = self.date_from
                    if date_from:
                        date_from.setDate(
                            QDate(int(parts[0]), int(parts[1]), int(parts[2]))
                        )
            if max_d:
                parts = max_d[:10].split("-")
                if len(parts) == 3:
                    date_to = self.date_to
                    if date_to:
                        date_to.setDate(
                            QDate(int(parts[0]), int(parts[1]), int(parts[2]))
                        )
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
        self._show_loading()

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

        filters = self.filter_bar.get_filters()
        date_start = filters["date_from"].toString("yyyy-MM-dd")
        date_end = filters["date_to"].toString("yyyy-MM-dd") + "T23:59:59"
        trc_text = filters.get("trc", "All TRCs")
        trc_filter = None if trc_text == "All TRCs" else trc_text.split(" \u2014")[0].strip()
        status_text = filters.get("status", "All")
        status_filter = None if status_text == "All" else status_text

        self._scan_start_time = time.time()
        self._worker = AnalyticsWorker(
            self.db.db_path, date_start, date_end, trc_filter, status_filter
        )
        self._worker.finished.connect(self._on_results)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_results(self, result):
        """Populate all panels with computed data."""
        import time
        import json

        elapsed_ms = int(
            (time.time() - getattr(self, '_scan_start_time', time.time())) * 1000
        )

        # Display results
        self._display_results(result)

        # Save report
        summary = result["summary"]
        report_summary = {
            "trc_count": len(result.get("metrics_table", [])),
            "avg_csat": summary.get("avg_csat"),
            "total_tickets": summary.get("total_tickets", 0),
        }

        filters = self.filter_bar.get_filters()
        date_from = filters["date_from"].toString("yyyy-MM-dd")
        date_to = filters["date_to"].toString("yyyy-MM-dd")
        trc_combo = self.trc_combo
        trc_filter = (trc_combo.currentData() or "All") if trc_combo else "All"

        # Make a JSON-safe copy — heatmap values use (yi, xi) tuple keys
        json_result = dict(result)
        hm = json_result.get("csat_heatmap")
        if hm and isinstance(hm.get("values"), dict):
            hm = dict(hm)
            hm["values"] = {f"{k[0]},{k[1]}": v for k, v in hm["values"].items()}
            json_result["csat_heatmap"] = hm

        self.db.save_report(
            page="trc_analytics",
            parameters={
                "date_from": date_from,
                "date_to": date_to,
                "trc_filter": trc_filter,
            },
            summary=report_summary,
            full_results=json.dumps(json_result, default=str),
            ticket_count=summary.get("total_tickets", 0),
            duration_ms=elapsed_ms,
        )
        self._reports_tab.refresh()

    # ═══════════════════════════════════════════
    #  DRILL-DOWN HANDLERS
    # ═══════════════════════════════════════════

    def _on_volume_bar_clicked(self, label: str):
        """Drill into tickets for the clicked TRC bar."""
        if not self._drilldown:
            return
        trc_code = label.split(" \u2014")[0].strip() if " \u2014" in label else label.strip()
        filters = self.filter_bar.get_filters()
        date_from = filters["date_from"].toString("yyyy-MM-dd")
        date_to = filters["date_to"].toString("yyyy-MM-dd") + "T23:59:59"

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
        """Drill into tickets for the clicked heatmap cell (TRC x period)."""
        if not self._drilldown:
            return
        trc_code = (
            y_label.split(" \u2014")[0].strip()
            if " \u2014" in y_label else y_label.strip()
        )
        filters = self.filter_bar.get_filters()
        date_from = filters["date_from"].toString("yyyy-MM-dd")
        date_to = filters["date_to"].toString("yyyy-MM-dd") + "T23:59:59"

        tickets = self.db.search_conversations(
            trc_code=trc_code, date_from=date_from, date_to=date_to,
        )
        total = len(tickets)
        self._drilldown.show_tickets(
            f"TRC: {trc_code} \u2014 {x_label}",
            f"{total} ticket{'s' if total != 1 else ''}",
            tickets,
        )

    def _on_view_tickets(self, ticket_ids):
        """Open drilldown panel showing the requested tickets."""
        if not self._drilldown or not ticket_ids:
            return
        tickets = []
        for tid in ticket_ids[:50]:
            conv = self.db.get_conversation(str(tid))
            if conv:
                tickets.append(conv)
        if not tickets:
            return
        total = len(tickets)
        self._drilldown.show_tickets(
            f"Finding Tickets",
            f"{total} ticket{'s' if total != 1 else ''}",
            tickets,
        )

    # ═══════════════════════════════════════════
    #  REPORT HISTORY (via ReportsTab)
    # ═══════════════════════════════════════════

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

        # Summary KPIs
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

        # Metrics table
        metrics = result.get("metrics_table", [])
        if metrics:
            html.append(
                f"<h3 style='margin: 12px 0 6px;'>TRC Breakdown ({len(metrics)})</h3>"
            )
            html.append(
                "<table cellpadding='3' style='border-collapse: collapse; width: 100%;'>"
            )
            html.append(
                "<tr style='border-bottom: 1px solid #E8E5DE;'>"
                "<th align='left'>TRC</th><th align='right'>Tickets</th>"
                "<th align='right'>CSAT</th></tr>"
            )
            for row in metrics[:30]:
                trc = row.get("trc_code", "")
                count_t = row.get("ticket_count", 0)
                csat = row.get("avg_csat")
                csat_str = f"{csat:.2f}" if csat else "\u2014"
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

    # ═══════════════════════════════════════════
    #  ERROR HANDLING
    # ═══════════════════════════════════════════

    def _on_error(self, error_text):
        # Mark job as failed so queue advances immediately
        main_win = self.window()
        if hasattr(main_win, '_job_queue'):
            main_win._job_queue.mark_job_failed("trc_analytics_refresh", error_text)
        # Defer error dialog so it doesn't block the job queue
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._show_error(error_text))

    def _show_error(self, error_text):
        self._show_content()
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(
            self, "Analytics Error",
            f"Computation failed:\n\n{error_text}",
        )

    # ═══════════════════════════════════════════
    #  METRICS TABLE
    # ═══════════════════════════════════════════

    def _populate_metrics_table(self, rows):
        """Fill the metrics table with per-TRC data."""
        layman = is_layman_mode()
        if layman:
            headers = [
                "TRC Code", "Tickets", "Avg Resolution (hrs)",
                "Median Resolution (hrs)", "Worst-Case (hrs)",
                "Avg First Reply (hrs)", "Avg Satisfaction",
                "% Solved", "Avg Messages", "Agent:Customer",
            ]
        else:
            headers = [
                "TRC Code", "Tickets", "Avg Res (hrs)", "Median Res (hrs)",
                "P95 Res (hrs)", "Avg First Reply (hrs)", "Avg CSAT",
                "% Solved", "Avg Messages", "Agent:Customer",
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

                # Right-align numeric columns
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

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

    # ═══════════════════════════════════════════
    #  NLP SCANNER — UI BUILDERS
    # ═══════════════════════════════════════════

    def _scan_card_frame(self):
        """Styled QFrame for scanner config cards."""
        f = QFrame()
        f.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED}; border: 1px solid rgba(214, 210, 202, 0.45);
                border-radius: 12px;
            }}
        """)
        apply_card_shadow(f)
        return f

    def _build_scan_info_panel(self):
        """'What is NLP Scanning?' brief intro panel."""
        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background: rgba(20, 87, 63, 0.04);
                border: 1px solid rgba(20, 87, 63, 0.15);
                border-radius: 10px;
            }
        """)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 12, 16, 12)
        cl.setSpacing(6)

        title_row = QHBoxLayout()
        icon = QLabel("?")
        icon.setStyleSheet(
            f"background: {ALMA_GREEN_DARK}; color: {ALMA_CREAM}; "
            "border-radius: 10px; font-weight: 700; font-size: 11px; "
            "min-width: 20px; max-width: 20px; min-height: 20px; max-height: 20px; "
            "text-align: center;"
        )
        icon.setAlignment(Qt.AlignCenter)
        title_row.addWidget(icon)
        lbl = QLabel("What is NLP Scanning?")
        lbl.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {ALMA_GREEN_DARK};")
        title_row.addWidget(lbl)
        title_row.addStretch()
        cl.addLayout(title_row)

        desc = QLabel(
            "NLP scanning sends every ticket in your date range to Gemini for "
            "classification \u2014 friction type, anomaly category, sentiment polarity, "
            "root cause, and sub-pattern assignment. Results power the Findings, "
            "Sub-Taxonomy, and AI Reports features."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_MID}; line-height: 1.4;")
        cl.addWidget(desc)

        return card

    def _build_scan_config_card(self):
        """New Scan config: date range, TRC filter, settings callout, cost estimator."""
        LBL = (
            f"font-size: 11px; font-weight: 600; color: {ALMA_TEXT_MID}; "
            "letter-spacing: 0.5px;"
        )
        FIELD = f"""
            QComboBox, QDateEdit {{
                color: {ALMA_TEXT_DARK}; background: {ALMA_WHITE};
                border: 1px solid {ALMA_BORDER}; border-radius: 8px;
                padding: 8px 12px; font-size: 13px;
            }}
        """

        card = self._scan_card_frame()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(10)

        # Title row with mode badge
        title_row = QHBoxLayout()
        title = QLabel("New Scan")
        title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK};")
        title_row.addWidget(title)

        self._scan_mode_badge = QLabel("AGENTIC")
        self._scan_mode_badge.setStyleSheet(
            f"background: {ALMA_GREEN_DARK}; color: {ALMA_CREAM}; "
            "border-radius: 4px; padding: 2px 8px; font-size: 10px; "
            "font-weight: 700; letter-spacing: 0.5px;"
        )
        self._scan_mode_badge.setFixedHeight(20)
        title_row.addWidget(self._scan_mode_badge)
        title_row.addStretch()
        cl.addLayout(title_row)

        # Row 1: Date range + TRC filter
        row1 = QHBoxLayout()

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("DATE RANGE")
        lbl.setStyleSheet(LBL)
        col.addWidget(lbl)
        date_row = QHBoxLayout()
        self._scan_date_start = ModernDatePicker()
        self._scan_date_end = ModernDatePicker()
        self._scan_date_start.setStyleSheet(FIELD)
        self._scan_date_end.setStyleSheet(FIELD)
        date_row.addWidget(self._scan_date_start)
        date_row.addWidget(QLabel("to"))
        date_row.addWidget(self._scan_date_end)
        col.addLayout(date_row)
        row1.addLayout(col, 3)

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("TRC FILTER")
        lbl.setStyleSheet(LBL)
        col.addWidget(lbl)
        self._scan_trc_combo = QComboBox()
        self._scan_trc_combo.setStyleSheet(FIELD)
        self._scan_trc_combo.addItem("All TRCs", None)
        col.addWidget(self._scan_trc_combo)
        row1.addLayout(col, 2)
        cl.addLayout(row1)

        # Row 2: Model selector
        row2 = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("MODEL")
        lbl.setStyleSheet(LBL)
        col.addWidget(lbl)
        self._scan_model_combo = QComboBox()
        self._scan_model_combo.setStyleSheet(FIELD)
        _models = [
            ("gemini-2.5-flash", "Gemini 2.5 Flash (recommended)"),
            ("gemini-2.5-flash-lite", "Gemini 2.5 Flash Lite (faster)"),
            ("gemini-2.5-pro", "Gemini 2.5 Pro (highest quality)"),
            ("gemini-2.0-flash", "Gemini 2.0 Flash (legacy)"),
        ]
        for model_id, model_label in _models:
            self._scan_model_combo.addItem(model_label, model_id)
        # Read current model from settings
        _current_model = "gemini-2.5-flash"
        try:
            import yaml
            with open("config/settings.yaml") as f:
                _cfg = yaml.safe_load(f) or {}
            _current_model = _cfg.get("gemini", {}).get("model", "gemini-2.5-flash")
        except Exception:
            pass
        for i in range(self._scan_model_combo.count()):
            if self._scan_model_combo.itemData(i) == _current_model:
                self._scan_model_combo.setCurrentIndex(i)
                break
        self._scan_model_combo.currentIndexChanged.connect(self._on_scan_model_changed)
        col.addWidget(self._scan_model_combo)
        row2.addLayout(col, 2)
        row2.addStretch(1)
        cl.addLayout(row2)

        # Row 3: Budget cap callout + workers info
        budget_callout = QFrame()
        budget_callout.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_GREEN_SUBTLE}; border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 8px;
            }}
        """)
        bc_layout = QHBoxLayout(budget_callout)
        bc_layout.setContentsMargins(12, 8, 12, 8)
        bc_layout.setSpacing(8)

        # Read budget cap from settings
        self._scan_budget_cap_value = 50.0
        try:
            import yaml
            with open("config/settings.yaml") as f:
                _cfg = yaml.safe_load(f) or {}
            self._scan_budget_cap_value = _cfg.get("nlp_scan", {}).get("budget_cap", 50.0)
        except Exception:
            pass

        bc_icon = QLabel("\U0001f6e1\ufe0f")
        bc_icon.setStyleSheet("font-size: 16px; border: none; background: transparent;")
        bc_layout.addWidget(bc_icon)

        bc_text = QLabel(
            f"<b>Budget Cap:</b> ${self._scan_budget_cap_value:.2f} per scan &nbsp;|&nbsp; "
            f"<b>Workers:</b> 3 (dynamic batching) &nbsp;|&nbsp; "
            f"<span style='color:{ALMA_TEXT_LIGHT}; font-size:10px;'>"
            f"Configured in Settings</span>"
        )
        bc_text.setStyleSheet(
            f"font-size: 12px; color: {ALMA_TEXT_DARK}; border: none; "
            "background: transparent;"
        )
        bc_text.setWordWrap(True)
        bc_layout.addWidget(bc_text, 1)
        cl.addWidget(budget_callout)

        # Cost estimator box
        est_frame = QFrame()
        est_frame.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_CREAM}; border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 8px;
            }}
        """)
        est_layout = QVBoxLayout(est_frame)
        est_layout.setContentsMargins(12, 8, 12, 8)
        est_layout.setSpacing(4)

        self._scan_est_label = QLabel("")
        self._scan_est_label.setStyleSheet(
            f"font-size: 12px; color: {ALMA_TEXT_MID}; padding: 2px 0;"
        )
        est_layout.addWidget(self._scan_est_label)

        bg_info = QLabel(
            "Scan runs in the background as a detached process. "
            "You can close the app and it will continue."
        )
        bg_info.setWordWrap(True)
        bg_info.setStyleSheet(
            f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; font-style: italic;"
        )
        est_layout.addWidget(bg_info)

        cl.addWidget(est_frame)

        # Connect date change to cost estimate update
        self._scan_date_start.date_changed.connect(self._update_scan_cost_estimate)
        self._scan_date_end.date_changed.connect(self._update_scan_cost_estimate)

        return card

    def _build_scan_control_buttons(self):
        """Start, Pause, Resume, Cancel, Retry buttons row."""
        container = QWidget()
        btn_layout = QHBoxLayout(container)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(10)

        # Start
        self._scan_start_btn = QPushButton("Start Scan")
        self._scan_start_btn.setCursor(Qt.PointingHandCursor)
        self._scan_start_btn.setMinimumHeight(40)
        self._scan_start_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_TEXT_ON_DARK};
                border: none; border-radius: 8px; font-weight: 700;
                font-size: 14px; padding: 10px 24px;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
            QPushButton:disabled {{ background: {ALMA_BORDER}; color: {ALMA_TEXT_LIGHT}; }}
        """)
        self._scan_start_btn.clicked.connect(self._start_nlp_scan)
        btn_layout.addWidget(self._scan_start_btn)

        # Pause
        self._scan_pause_btn = QPushButton("Pause")
        self._scan_pause_btn.setCursor(Qt.PointingHandCursor)
        self._scan_pause_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_WARNING}; color: white; border: none;
                border-radius: 8px; padding: 10px 20px; font-weight: 600;
                font-size: 13px;
            }}
            QPushButton:hover {{ background: #E0A800; }}
        """)
        self._scan_pause_btn.clicked.connect(self._pause_nlp_scan)
        self._scan_pause_btn.setVisible(False)
        btn_layout.addWidget(self._scan_pause_btn)

        # Resume
        self._scan_resume_btn = QPushButton("Resume")
        self._scan_resume_btn.setCursor(Qt.PointingHandCursor)
        self._scan_resume_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_SUCCESS}; color: white; border: none;
                border-radius: 8px; padding: 10px 20px; font-weight: 600;
                font-size: 13px;
            }}
            QPushButton:hover {{ background: #1B8A2A; }}
        """)
        self._scan_resume_btn.clicked.connect(self._resume_nlp_scan)
        self._scan_resume_btn.setVisible(False)
        btn_layout.addWidget(self._scan_resume_btn)

        # Cancel
        self._scan_cancel_btn = QPushButton("Cancel")
        self._scan_cancel_btn.setCursor(Qt.PointingHandCursor)
        self._scan_cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_ERROR}; color: white; border: none;
                border-radius: 8px; padding: 10px 20px; font-weight: 600;
                font-size: 13px;
            }}
            QPushButton:hover {{ background: #B71C1C; }}
        """)
        self._scan_cancel_btn.clicked.connect(self._cancel_nlp_scan)
        self._scan_cancel_btn.setVisible(False)
        btn_layout.addWidget(self._scan_cancel_btn)

        # Retry Failed Batches
        self._scan_retry_btn = QPushButton("Retry Failed Batches")
        self._scan_retry_btn.setCursor(Qt.PointingHandCursor)
        self._scan_retry_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_WARNING}; color: white; border: none;
                border-radius: 8px; padding: 10px 20px; font-weight: 600;
                font-size: 13px;
            }}
            QPushButton:hover {{ background: #E67E00; }}
        """)
        self._scan_retry_btn.clicked.connect(self._retry_nlp_failed_batches)
        self._scan_retry_btn.setVisible(False)
        btn_layout.addWidget(self._scan_retry_btn)

        btn_layout.addStretch()
        return container

    def _build_scan_history_section(self):
        """Collapsible scan history table."""
        self._scan_history_section = CollapsibleSection(
            "Scan History", initially_collapsed=True,
            section_key="trc_analytics.scan_history"
        )

        container = QWidget()
        cl = QVBoxLayout(container)
        cl.setContentsMargins(8, 8, 8, 8)
        cl.setSpacing(6)

        self._scan_history_table = QTableWidget(0, 6)
        self._scan_history_table.setHorizontalHeaderLabels([
            "Date", "Range", "Tickets", "Cost", "Findings", "Status"
        ])
        self._scan_history_table.horizontalHeader().setStretchLastSection(True)
        self._scan_history_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self._scan_history_table.verticalHeader().setVisible(False)
        configure_table(self._scan_history_table)
        self._scan_history_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._scan_history_table.setAlternatingRowColors(True)
        self._scan_history_table.setMaximumHeight(250)
        self._scan_history_table.setStyleSheet(f"""
            QTableWidget {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 8px; font-size: 11px; color: {ALMA_TEXT_DARK};
            }}
            QHeaderView::section {{
                background: {ALMA_CREAM}; color: {ALMA_TEXT_MID}; font-weight: 600;
                font-size: 10px; padding: 6px; border: none;
                border-bottom: 1px solid {ALMA_BORDER_LIGHT};
                letter-spacing: 0.5px;
            }}
            QTableWidget::item {{ padding: 4px 8px; }}
            QTableWidget::item:alternate {{ background: rgba(20, 87, 63, 0.02); }}
            QScrollBar:vertical {{ width: 6px; background: transparent; }}
            QScrollBar::handle:vertical {{
                background: {ALMA_BORDER}; border-radius: 3px; min-height: 30px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar:horizontal {{ height: 0; }}
            QTableCornerButton::section {{
                background: {ALMA_CREAM}; border: none;
                border-bottom: 1px solid {ALMA_BORDER_LIGHT};
                color: {ALMA_TEXT_MID};
            }}
        """)
        cl.addWidget(self._scan_history_table)

        self._scan_history_section.add_widget(container)
        return self._scan_history_section

    def _build_scan_docs_panel(self):
        """'How Scanning Works & Limits' collapsible docs."""
        section = CollapsibleSection(
            "How Scanning Works & Limits", initially_collapsed=True,
            section_key="trc_analytics.scan_docs"
        )

        container = QWidget()
        cl = QVBoxLayout(container)
        cl.setContentsMargins(8, 8, 8, 8)
        cl.setSpacing(12)

        how_title = QLabel("How Scanning Works")
        how_title.setStyleSheet(
            f"font-size: 12px; font-weight: 700; color: {ALMA_TEXT_DARK};"
        )
        cl.addWidget(how_title)

        how_text = QLabel(
            "1. Tickets are grouped by TRC code and split into batches "
            "(~700 tickets each).\n"
            "2. Agentic pipeline boots: bridge \u2192 supervisor \u2192 workers "
            "\u2192 rate governor.\n"
            "3. Workers send batches to Gemini for classification: friction type, "
            "anomaly category, polarity, root cause, sub-pattern.\n"
            "4. Results are stored in SQLite. After all batches complete, "
            "meta-analysis identifies cross-TRC findings.\n"
            "5. Workers are detached processes \u2014 they continue running "
            "even if you close the app.\n"
            "6. All communication is via SQLite (WAL mode). No network server needed."
        )
        how_text.setWordWrap(True)
        how_text.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_MID}; line-height: 1.5;"
        )
        cl.addWidget(how_text)

        cap_title = QLabel("Capacity & Limits")
        cap_title.setStyleSheet(
            f"font-size: 12px; font-weight: 700; color: {ALMA_TEXT_DARK};"
        )
        cl.addWidget(cap_title)

        cap_text = QLabel(
            "- CLI mode limit: 50,000 tickets per scan\n"
            "- 3 workers with dynamic batching (optimized automatically)\n"
            "- Budget cap: pauses scan when Gemini cost reaches the limit\n"
            "  (configured in Settings)\n"
            "- Batch retry: failed batches retry up to 3 times\n"
            "- Gemini 2.5 Flash pricing: $0.15/M input + $0.60/M output tokens\n"
            "- Typical cost: ~$0.01 per 100 tickets\n"
            "- Typical speed: ~90 seconds per batch (700 tickets)"
        )
        cap_text.setWordWrap(True)
        cap_text.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_MID}; line-height: 1.5;"
        )
        cl.addWidget(cap_text)

        section.add_widget(container)
        return section

    # ═══════════════════════════════════════════
    #  NLP SCANNER — LIFECYCLE
    # ═══════════════════════════════════════════

    def is_scan_active(self):
        """Return True if an NLP scan is currently running."""
        return self._active_scan_id is not None

    def _reset_scan_ui(self):
        """Reset all scan UI controls to idle state."""
        self._scan_start_btn.setEnabled(True)
        self._scan_pause_btn.setVisible(False)
        self._scan_resume_btn.setVisible(False)
        self._scan_cancel_btn.setVisible(False)
        self._scan_retry_btn.setVisible(False)
        self._scan_model_combo.setEnabled(True)
        self._scan_status_panel.show_idle()

    def _ensure_scan_manager(self):
        """Lazy-init scan orchestrator (agentic pipeline 5.0).

        Falls back to ScanWorkerManager if agentic pipeline
        fails to import (e.g. Node.js not available).
        """
        if self._scan_mgr is None:
            try:
                from src.agents.scan_orchestrator import ScanOrchestrator
                self._scan_mgr = ScanOrchestrator(
                    db_path=str(self.db.db_path)
                )
            except ImportError:
                from src.data.scan_worker_manager import ScanWorkerManager
                self._scan_mgr = ScanWorkerManager(
                    db_path=str(self.db.db_path)
                )

    def _start_nlp_scan(self):
        """Start a new NLP scan."""
        try:
            self._ensure_scan_manager()

            ds = self._scan_date_start.get_date_string()
            de = self._scan_date_end.get_date_string()
            if not ds or not de:
                self._scan_est_label.setText("Select a date range first")
                return

            # TRC filter
            trc_filter = None
            trc_data = self._scan_trc_combo.currentData()
            if trc_data:
                trc_filter = [trc_data]

            # Read budget cap + workers from settings (not UI controls)
            _budget = self._scan_budget_cap_value
            _workers = 3
            try:
                import yaml
                with open("config/settings.yaml") as f:
                    _s = yaml.safe_load(f) or {}
                _nlp = _s.get("nlp_scan", {})
                _budget = _nlp.get("budget_cap", 50.0)
                _workers = _nlp.get("parallel_workers", 3)
            except Exception:
                pass

            result = self._scan_mgr.start_scan(
                date_start=ds,
                date_end=de,
                trc_filter=trc_filter,
                budget_cap=_budget,
                parallel_workers=_workers,
            )

            if "error" in result:
                self._scan_est_label.setText(f"Error: {result['error']}")
                return

            self._active_scan_id = result.get("scan_id")
            self.scan_active_changed.emit(True)

            # Show running state — lock config controls
            self._scan_start_btn.setEnabled(False)
            self._scan_pause_btn.setVisible(True)
            self._scan_cancel_btn.setVisible(True)
            self._scan_model_combo.setEnabled(False)

            # Switch status panel to running mode
            self._scan_status_panel.show_running()

            # Start the live monitor
            self._scan_monitor.start_monitoring(self._active_scan_id)

            # Start polling for scan status
            self._start_scan_polling()

            logger.info(
                f"Scan started | {result.get('total_batches', 0)} batches | "
                f"{result.get('total_tickets', 0):,} tickets"
            )

        except Exception as e:
            self._scan_est_label.setText(f"Error: {e}")
            logger.error(f"Start scan failed: {e}")

    def _pause_nlp_scan(self):
        """Pause the active scan."""
        if not self._active_scan_id or not self._scan_mgr:
            return
        try:
            self._scan_mgr.pause_scan(self._active_scan_id)
            self._scan_pause_btn.setVisible(False)
            self._scan_resume_btn.setVisible(True)
        except Exception as e:
            logger.error(f"Pause failed: {e}")

    def _resume_nlp_scan(self):
        """Resume the paused scan."""
        if not self._active_scan_id or not self._scan_mgr:
            return
        try:
            _budget = self._scan_budget_cap_value
            try:
                import yaml
                with open("config/settings.yaml") as f:
                    _s = yaml.safe_load(f) or {}
                _budget = _s.get("nlp_scan", {}).get("budget_cap", 50.0)
            except Exception:
                pass
            self._scan_mgr.resume_scan(
                self._active_scan_id,
                budget_cap=_budget
            )
            self._scan_pause_btn.setVisible(True)
            self._scan_resume_btn.setVisible(False)
            self._start_scan_polling()
        except Exception as e:
            logger.error(f"Resume failed: {e}")

    def _cancel_nlp_scan(self):
        """Cancel the active scan."""
        if not self._active_scan_id or not self._scan_mgr:
            return
        try:
            self._scan_mgr.cancel_scan(self._active_scan_id)
            self._stop_scan_polling()
            self._scan_monitor.stop_monitoring()
            self._active_scan_id = None
            self.scan_active_changed.emit(False)
            self._reset_scan_ui()
        except Exception as e:
            logger.error(f"Cancel failed: {e}")

    def _retry_nlp_failed_batches(self):
        """Retry only the failed batches from the last scan."""
        if not self._active_scan_id or not self._scan_mgr:
            return
        try:
            self._scan_retry_btn.setVisible(False)
            self._scan_start_btn.setEnabled(False)
            self._scan_cancel_btn.setVisible(True)
            self.scan_active_changed.emit(True)

            import threading
            t = threading.Thread(
                target=self._scan_mgr.retry_failed_batches,
                args=(self._active_scan_id,),
                daemon=True,
            )
            t.start()
            self._start_scan_polling()
        except Exception as e:
            logger.error(f"Retry failed batches: {e}")
            self._reset_scan_ui()
            self._scan_retry_btn.setVisible(True)

    # ═══════════════════════════════════════════
    #  NLP SCANNER — POLLING
    # ═══════════════════════════════════════════

    def _start_scan_polling(self):
        """Start 5-second polling timer for scan status."""
        if self._poll_timer:
            self._poll_timer.stop()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_scan_status)
        self._poll_timer.start(5000)

    def _stop_scan_polling(self):
        """Stop the scan polling timer."""
        if self._poll_timer:
            self._poll_timer.stop()
            self._poll_timer = None

    def _poll_scan_status(self):
        """Poll SQLite for active scan status (CLI mode — no server)."""
        if not self._scan_mgr:
            # scan_mgr lost (e.g. model change) — reset UI to idle
            logger.warning("Poll: scan_mgr is None — resetting to idle")
            self._stop_scan_polling()
            self._scan_monitor.stop_monitoring()
            self._active_scan_id = None
            self.scan_active_changed.emit(False)
            self._reset_scan_ui()
            self._refresh_scanner_tab()
            return

        # Worker thread may not have set _active_scan_id yet
        if not self._active_scan_id:
            self._poll_wait_count = getattr(self, '_poll_wait_count', 0) + 1
            if self._poll_wait_count > 12:
                self._stop_scan_polling()
            return

        self._poll_wait_count = 0

        try:
            status = self._scan_mgr.get_status(self._active_scan_id)
            if "error" in status:
                return

            completed = status.get("completed_batches", 0) or 0
            total = status.get("total_batches", 1) or 1
            budget = status.get("budget_cap_usd", 50) or 50
            scan_status = status.get("status", "")

            terminal = (
                "scan_complete", "analysis_complete",
                "completed", "completed_with_errors",
                "failed", "cancelled",
                "budget_exceeded", "quota_exhausted",
            )

            if scan_status == "paused":
                self._scan_pause_btn.setVisible(False)
                self._scan_resume_btn.setVisible(True)

            if scan_status in terminal:
                self._stop_scan_polling()
                self._scan_monitor.stop_monitoring()
                self._active_scan_id = None
                self.scan_active_changed.emit(False)
                self._reset_scan_ui()

                if scan_status == "completed_with_errors":
                    try:
                        failed = self.db.get_failed_batches_for_scan(
                            status.get("scan_id", ""))
                        if len(failed) > 0:
                            self._scan_retry_btn.setText(
                                f"Retry {len(failed)} Failed Batch(es)")
                            self._scan_retry_btn.setVisible(True)
                            self._active_scan_id = status.get("scan_id")
                    except Exception:
                        pass
                    self._scan_est_label.setText(
                        f"Scan completed with errors | "
                        f"{completed}/{total} batches succeeded"
                    )
                    self._scan_est_label.setStyleSheet(
                        f"font-size: 12px; color: {ALMA_WARNING}; font-weight: 600;"
                    )
                    QApplication.processEvents()
                    self._run_post_scan_analysis()
                elif scan_status in ("scan_complete", "completed"):
                    QApplication.processEvents()
                    self._run_post_scan_analysis()
                elif scan_status == "analysis_complete":
                    self._refresh_scanner_tab()
                elif scan_status == "budget_exceeded":
                    self._scan_est_label.setText(
                        f"Scan paused: budget cap (${budget:.2f}) reached. "
                        f"Resume with higher budget to continue."
                    )
                    self._active_scan_id = status.get("scan_id")
                    self._scan_resume_btn.setVisible(True)
                    self._scan_cancel_btn.setVisible(True)
                elif scan_status == "quota_exhausted":
                    self._scan_est_label.setText(
                        "Scan stopped: Gemini API quota exhausted. "
                        "Wait a few minutes, then Resume."
                    )
                    self._scan_est_label.setStyleSheet(
                        f"font-size: 12px; color: {ALMA_ERROR}; font-weight: 600;"
                    )
                    self._active_scan_id = status.get("scan_id")
                    self._scan_resume_btn.setVisible(True)
                    self._scan_cancel_btn.setVisible(True)
                elif scan_status == "failed":
                    self._scan_est_label.setText(
                        f"Scan failed | {completed}/{total} batches completed"
                    )
                    self._scan_est_label.setStyleSheet(
                        f"font-size: 12px; color: {ALMA_ERROR};"
                    )
                    self._scan_monitor.set_error_state("Scan failed")
                    if completed > 0:
                        QApplication.processEvents()
                        self._run_post_scan_analysis()
                    else:
                        self._refresh_scanner_tab()
                else:
                    self._refresh_scanner_tab()

        except Exception as e:
            logger.error(f"Poll failed: {e}")

    def _run_post_scan_analysis(self):
        """Run meta-analysis after scan completes, then refresh tabs."""
        try:
            scan = self.db.get_latest_completed_scan()
            if not scan:
                return

            from src.data.nlp_meta_analyzer import NLPMetaAnalyzer
            analyzer = NLPMetaAnalyzer(self.db)
            analyzer.run_analysis(scan["scan_id"])

            self._scan_est_label.setText("Scan completed \u2014 findings ready")
            self._refresh_scanner_tab()
            # Refresh taxonomy since scan results power it
            if hasattr(self, '_taxonomy'):
                self._taxonomy.refresh()
        except Exception as e:
            logger.error(f"Post-scan analysis failed: {e}")
            self._scan_est_label.setText(f"Meta-analysis failed: {e}")

    def _on_nlp_scan_completed(self):
        """Handle scan_completed signal from ScanMonitorWidget."""
        self._stop_scan_polling()
        self._scan_monitor.stop_monitoring()
        self._active_scan_id = None
        self.scan_active_changed.emit(False)
        self._reset_scan_ui()

        self._refresh_scanner_tab()
        if hasattr(self, '_taxonomy'):
            self._taxonomy.refresh()

    # ═══════════════════════════════════════════
    #  NLP SCANNER — DATA / REFRESH
    # ═══════════════════════════════════════════

    def _update_scan_cost_estimate(self):
        """Update cost estimate when date range or config changes."""
        try:
            ds = self._scan_date_start.get_date_string()
            de = self._scan_date_end.get_date_string()
            if not ds or not de:
                return

            ticket_count = self.db.get_ticket_count_in_range(ds, de)
            batches = max(1, (ticket_count + 19) // 20) if ticket_count else 0

            # Gemini 2.0 Flash: $0.10/M input, $0.40/M output
            input_tokens = ticket_count * 600
            output_tokens = ticket_count * 200
            cost = (input_tokens / 1e6) * 0.10 + (output_tokens / 1e6) * 0.40

            est_seconds = (batches * 10) / 3  # fixed 3 workers via dynamic batching
            est_min = est_seconds / 60

            est_text = (
                f"Est. Tickets: {ticket_count:,} | Batches: ~{batches} | "
                f"Est. Cost: ${cost:.2f} | Est. Time: ~{est_min:.0f} min"
            )

            # 50K CLI cap warning
            MAX_CLI = 50_000
            if ticket_count > MAX_CLI:
                est_text = (
                    f"  {ticket_count:,} tickets exceeds CLI limit "
                    f"({MAX_CLI:,}). Narrow the date range or use a TRC filter."
                )
                self._scan_est_label.setStyleSheet(
                    f"font-size: 12px; color: {ALMA_ERROR}; "
                    "padding: 4px 0; font-weight: 600;"
                )
                self._scan_start_btn.setEnabled(False)
            else:
                self._scan_est_label.setStyleSheet(
                    f"font-size: 12px; color: {ALMA_TEXT_MID}; padding: 4px 0;"
                )
                self._scan_start_btn.setEnabled(True)

            self._scan_est_label.setText(est_text)

            # Update status panel idle estimates
            self._scan_status_panel.set_estimates(
                tickets=ticket_count,
                time_min=est_min,
                tokens=input_tokens + output_tokens,
                cost=cost,
            )
        except Exception:
            self._scan_est_label.setText("")

    def _refresh_scanner_tab(self):
        """Refresh all scanner tab sections."""
        self._populate_scan_trc_combo()
        self._sync_scan_date_range()
        self._update_scan_cost_estimate()
        self._refresh_scan_history()

    def _populate_scan_trc_combo(self):
        """Populate scanner TRC filter dropdown."""
        current = self._scan_trc_combo.currentData()
        self._scan_trc_combo.clear()
        self._scan_trc_combo.addItem("All TRCs", None)
        try:
            trcs = self.db.get_trc_list()
            for trc in trcs:
                self._scan_trc_combo.addItem(trc, trc)
        except Exception:
            pass

    def _sync_scan_date_range(self):
        """Sync scanner date pickers to actual data range."""
        try:
            row = self.db.conn.execute(
                "SELECT MIN(created_at) AS mn, MAX(created_at) AS mx "
                "FROM conversations"
            ).fetchone()
            if row and row["mn"] and row["mx"]:
                mn = row["mn"][:10].split("-")
                mx = row["mx"][:10].split("-")
                if len(mn) == 3 and len(mx) == 3:
                    self._scan_date_start.set_date(
                        QDate(int(mn[0]), int(mn[1]), int(mn[2]))
                    )
                    self._scan_date_end.set_date(
                        QDate(int(mx[0]), int(mx[1]), int(mx[2]))
                    )
        except Exception:
            pass

    def _refresh_scan_history(self):
        """Populate scan history table."""
        try:
            scans = self.db.get_scan_history(limit=20)
            self._scan_history_table.setRowCount(len(scans))

            for i, s in enumerate(scans):
                date_str = s.get("created_at", "")[:10]
                range_str = (
                    f"{s.get('date_range_start', '')[:10]} to "
                    f"{s.get('date_range_end', '')[:10]}"
                )
                tickets = f"{s.get('total_tickets', 0):,}"
                cost = f"${s.get('actual_cost_usd', 0):.2f}"
                findings_count = len(
                    self.db.get_scan_findings(s["scan_id"], limit=100)
                )
                status = s.get("status", "unknown")

                self._scan_history_table.setItem(
                    i, 0, QTableWidgetItem(date_str))
                self._scan_history_table.setItem(
                    i, 1, QTableWidgetItem(range_str))
                self._scan_history_table.setItem(
                    i, 2, QTableWidgetItem(tickets))
                self._scan_history_table.setItem(
                    i, 3, QTableWidgetItem(cost))
                self._scan_history_table.setItem(
                    i, 4, QTableWidgetItem(str(findings_count)))

                status_display = {
                    'scan_complete': 'completed',
                    'analysis_complete': 'completed',
                    'completed_with_errors': 'partial',
                    'quota_exhausted': 'quota limit',
                    'budget_exceeded': 'budget limit',
                }.get(status, status)

                status_item = QTableWidgetItem(status_display)
                if status in ("completed", "scan_complete", "analysis_complete"):
                    status_item.setForeground(Qt.darkGreen)
                elif status == "completed_with_errors":
                    status_item.setForeground(QColor(ALMA_WARNING))
                elif status in ("failed", "cancelled", "budget_exceeded",
                                "quota_exhausted"):
                    status_item.setForeground(Qt.red)
                elif status in ("running", "paused"):
                    status_item.setForeground(QColor(ALMA_WARNING))
                self._scan_history_table.setItem(i, 5, status_item)
        except Exception:
            pass

    # ═══════════════════════════════════════════
    #  TAB SWITCHING & PATTERN DRILLDOWN
    # ═══════════════════════════════════════════

    def _on_tab_changed(self, index):
        """Refresh the active tab's content on switch."""
        if index == 1:
            # NLP Scanner tab
            self._refresh_scanner_tab()
        elif index == 3:
            # SubTaxonomy tab
            self._taxonomy.refresh()

    def _on_scan_model_changed(self, index):
        """Write selected model to settings.yaml when user changes it."""
        model_id = self._scan_model_combo.itemData(index)
        if not model_id:
            return
        # Block model changes while a scan is active (prevents poll breakage)
        if self.is_scan_active():
            logger.warning("Model change blocked — scan is active")
            return
        try:
            import yaml
            from pathlib import Path
            cfg_path = Path("config/settings.yaml")
            with open(cfg_path) as f:
                cfg = yaml.safe_load(f) or {}
            cfg.setdefault("gemini", {})["model"] = model_id
            with open(cfg_path, "w") as f:
                yaml.safe_dump(cfg, f, default_flow_style=False, sort_keys=True)
            logger.info(f"Scan model changed to: {model_id}")
            # Force re-init scan manager on next scan to pick up new model
            self._scan_mgr = None
        except Exception as e:
            logger.error(f"Failed to save model setting: {e}")

    def _on_pattern_selected(self, pattern_data):
        """Handle taxonomy row click — show pattern in drilldown panel."""
        if self._drilldown:
            try:
                self._drilldown.show_pattern(pattern_data)
            except Exception as e:
                logger.error(f"Pattern drilldown failed: {e}")
