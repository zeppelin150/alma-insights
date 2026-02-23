"""
Alma Insights — Main Application Window
Top bar, sidebar navigation, stacked content pages.
"""

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QStackedWidget, QStatusBar, QSizePolicy,
    QSpacerItem, QApplication
)
from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QIcon, QFont

from src.ui.theme import *
from src.ui.pages.conversation_search import ConversationSearchPage
from src.ui.pages.trc_analytics import TRCAnalyticsPage
from src.ui.pages.trending_topics import TrendingTopicsPage
from src.ui.pages.incidents_page import IncidentsPage
from src.ui.pages.ai_reports import AIReportsPage
from src.ui.pages.ab_compare import ABComparePage
from src.ui.pages.smart_reporting import SmartReportingPage
from src.ui.pages.settings_page import SettingsPage
from src.ui.pages.nlp_scanner_page import NLPScannerPage
from src.ui.dialogs.help_dialog import HelpDialog
from src.data.db_manager import DatabaseManager
from src.data.job_queue import JobQueue, JobDescriptor
from src.ui.widgets.job_overlay import JobOverlay
from src.ui.widgets.drilldown_panel import DrilldownPanel


class MainWindow(QMainWindow):

    PAGE_CONVERSATIONS = 0
    PAGE_DASHBOARD = 1
    PAGE_TRENDING = 2
    PAGE_INCIDENTS = 3
    PAGE_NLP_SCANNER = 4
    PAGE_REPORTS = 5
    PAGE_AB_COMPARE = 6
    PAGE_SMART_REPORTING = 7
    PAGE_SETTINGS = 8

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Alma Insights")
        self.setMinimumSize(1200, 750)
        self.resize(1400, 850)

        # Database
        self.db = DatabaseManager()
        self.db.initialize()

        # Track sidebar buttons
        self._sidebar_buttons = []
        self._active_page = 0

        self._build_ui()

        # Unified job queue and loading overlay
        self._job_queue = JobQueue(self)
        self._job_overlay = JobOverlay(self.content_stack)
        self.content_stack.installEventFilter(self._job_overlay)
        self._wire_job_queue()

        # Universal drill-down panel (overlay drawer)
        self._drilldown = DrilldownPanel(self.content_stack)
        self.conversations_page.set_drilldown_panel(self._drilldown)
        self.dashboard_page.set_drilldown_panel(self._drilldown)
        self.trending_page.set_drilldown_panel(self._drilldown)
        self.incidents_page.set_drilldown_panel(self._drilldown)
        self.nlp_scanner_page.set_drilldown_panel(self._drilldown)
        self.reports_page.set_drilldown_panel(self._drilldown)
        self.ab_compare_page.set_drilldown_panel(self._drilldown)

        self._setup_calendar_sync()
        self._restore_settings()
        self._load_data()

    def _build_ui(self):
        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Top Bar ──
        main_layout.addWidget(self._build_top_bar())

        # ── Body: Sidebar + Content ──
        # Build content area FIRST so content_stack exists when sidebar calls _set_active_page
        content_widget = self._build_content_area()

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        body.addWidget(self._build_sidebar())
        body.addWidget(content_widget, 1)

        body_widget = QWidget()
        body_widget.setLayout(body)
        main_layout.addWidget(body_widget, 1)

        # ── Status Bar ──
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_label = QLabel("Ready")
        self.status_bar.addWidget(self.status_label)

        self.layman_mode_indicator = QLabel("")
        self.layman_mode_indicator.setStyleSheet(
            "font-size: 11px; color: #7A7A7A; padding: 0 8px;"
        )
        self.status_bar.addPermanentWidget(self.layman_mode_indicator)

        self.ticket_count_label = QLabel("")
        self.status_bar.addPermanentWidget(self.ticket_count_label)

    # ═══════════════════════════════════════════
    #  TOP BAR
    # ═══════════════════════════════════════════

    def _build_top_bar(self):
        bar = QFrame()
        bar.setObjectName("TopBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(12)

        # Logo / Title
        title = QLabel("Alma Insights")
        title.setObjectName("TopBarTitle")
        layout.addWidget(title)

        subtitle = QLabel("RCM Issue Analysis")
        subtitle.setObjectName("TopBarSubtitle")
        layout.addWidget(subtitle)

        layout.addStretch()

        # Help button
        help_btn = QPushButton("Help & Docs")
        help_btn.setObjectName("TopBarButton")
        help_btn.setToolTip("Documentation, feedback, and tool overview")
        help_btn.setCursor(Qt.PointingHandCursor)
        help_btn.clicked.connect(self._show_help)
        layout.addWidget(help_btn)

        # Feedback button
        feedback_btn = QPushButton("Feedback")
        feedback_btn.setObjectName("TopBarButton")
        feedback_btn.setToolTip("Submit feedback via Typeform")
        feedback_btn.setCursor(Qt.PointingHandCursor)
        feedback_btn.clicked.connect(self._show_feedback)
        layout.addWidget(feedback_btn)

        return bar

    # ═══════════════════════════════════════════
    #  SIDEBAR
    # ═══════════════════════════════════════════

    def _build_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 12, 0, 12)
        layout.setSpacing(0)

        # ── SOURCES section ──
        section0 = QLabel("SOURCES")
        section0.setObjectName("SidebarSection")
        layout.addWidget(section0)

        layout.addWidget(self._sidebar_btn("\U0001F4AC  Conversations", self.PAGE_CONVERSATIONS))

        # Divider
        div0 = QFrame()
        div0.setObjectName("SidebarDivider")
        layout.addWidget(div0)

        # ── ANALYSIS section ──
        section1 = QLabel("ANALYSIS")
        section1.setObjectName("SidebarSection")
        layout.addWidget(section1)

        layout.addWidget(self._sidebar_btn("\U0001F4CA  TRC Analytics", self.PAGE_DASHBOARD))
        layout.addWidget(self._sidebar_btn("\U0001F4C8  Trending Topics", self.PAGE_TRENDING))
        layout.addWidget(self._sidebar_btn("\U0001F6A8  Incidents", self.PAGE_INCIDENTS))
        layout.addWidget(self._sidebar_btn("\U0001F9E0  NLP Scanner", self.PAGE_NLP_SCANNER))

        # Divider
        div1 = QFrame()
        div1.setObjectName("SidebarDivider")
        layout.addWidget(div1)

        # ── REPORTS section ──
        section2 = QLabel("REPORTS")
        section2.setObjectName("SidebarSection")
        layout.addWidget(section2)

        layout.addWidget(self._sidebar_btn("\U0001F916  AI Reports", self.PAGE_REPORTS))
        layout.addWidget(self._sidebar_btn("\U0001F504  A/B Compare", self.PAGE_AB_COMPARE))
        layout.addWidget(self._sidebar_btn("\u26A1  Smart Reporting", self.PAGE_SMART_REPORTING))

        # Divider
        div2 = QFrame()
        div2.setObjectName("SidebarDivider")
        layout.addWidget(div2)

        # ── SYSTEM section ──
        section3 = QLabel("SYSTEM")
        section3.setObjectName("SidebarSection")
        layout.addWidget(section3)

        layout.addWidget(self._sidebar_btn("\u2699\ufe0f  Settings", self.PAGE_SETTINGS))

        layout.addStretch()

        # Footer
        footer = QLabel("v1.0.0 — RCM Operations")
        footer.setObjectName("SidebarFooter")
        layout.addWidget(footer)

        # Set initial active
        self._set_active_page(self.PAGE_CONVERSATIONS)

        return sidebar

    def _sidebar_btn(self, text, page_index):
        btn = QPushButton(text)
        btn.setObjectName("SidebarButton")
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda: self._set_active_page(page_index))
        self._sidebar_buttons.append((btn, page_index))
        return btn

    def _set_active_page(self, index):
        self._active_page = index
        self.content_stack.setCurrentIndex(index)

        # Close drill-down panel on page navigation
        if hasattr(self, '_drilldown') and self._drilldown.is_open():
            self._drilldown.close_panel()

        # Update button states
        for btn, page_idx in self._sidebar_buttons:
            btn.setProperty("active", "true" if page_idx == index else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    # ═══════════════════════════════════════════
    #  CONTENT AREA
    # ═══════════════════════════════════════════

    def _build_content_area(self):
        content = QFrame()
        content.setObjectName("ContentArea")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.content_stack = QStackedWidget()

        # Page 0: Conversation Search
        self.conversations_page = ConversationSearchPage(self.db)
        self.conversations_page.data_loaded.connect(self._on_data_loaded)
        self.content_stack.addWidget(self.conversations_page)

        # Page 1: TRC Analytics
        self.dashboard_page = TRCAnalyticsPage(self.db)
        self.content_stack.addWidget(self.dashboard_page)

        # Page 2: Trending Topics
        self.trending_page = TrendingTopicsPage(self.db)
        self.content_stack.addWidget(self.trending_page)

        # Page 3: Incidents
        self.incidents_page = IncidentsPage(self.db)
        self.incidents_page.scan_complete.connect(self._update_incident_badge)
        self.content_stack.addWidget(self.incidents_page)

        # Page 4: NLP Scanner
        self.nlp_scanner_page = NLPScannerPage(self.db)
        self.nlp_scanner_page.deep_dive_requested.connect(self._on_nlp_deep_dive)
        self.nlp_scanner_page.view_tickets_requested.connect(self._on_nlp_view_tickets)
        self.content_stack.addWidget(self.nlp_scanner_page)

        # Page 5: AI Reports
        self.reports_page = AIReportsPage(self.db)
        self.content_stack.addWidget(self.reports_page)

        # Page 6: A/B Compare
        self.ab_compare_page = ABComparePage(self.db)
        self.content_stack.addWidget(self.ab_compare_page)

        # Page 7: Smart Reporting
        self.smart_reporting_page = SmartReportingPage(self.db)
        self.content_stack.addWidget(self.smart_reporting_page)

        # Page 8: Settings
        self.settings_page = SettingsPage()
        self.settings_page.set_db_manager(self.db)
        self.settings_page.datasets_changed.connect(self._on_datasets_changed)
        self.settings_page.test_data_changed.connect(self._on_test_data_toggled)
        self.settings_page.debug_mode_changed.connect(self._on_debug_mode_toggled)
        self.settings_page.api_toggle.toggled.connect(self._on_api_toggled)
        self.settings_page.settings_changed.connect(self._on_settings_changed)
        self.content_stack.addWidget(self.settings_page)

        layout.addWidget(self.content_stack)
        return content

    # ═══════════════════════════════════════════
    #  RESTORE PERSISTED SETTINGS
    # ═══════════════════════════════════════════

    def _restore_settings(self):
        """Push persisted settings state to conversations page on startup."""
        api_enabled = self.settings_page.is_api_enabled()
        self.conversations_page.update_api_state(api_enabled)
        self.conversations_page.update_pat(self.settings_page.get_pat())
        self.conversations_page.update_test_mode(self.settings_page.is_test_data_enabled())
        self.conversations_page.update_debug_mode(self.settings_page.is_debug_mode_enabled())
        if api_enabled:
            self.conversations_page.update_datasets(self.settings_page.get_datasets())

    # ═══════════════════════════════════════════
    #  DATA LOADING
    # ═══════════════════════════════════════════

    def _load_data(self):
        """Load demo data on startup if test mode is enabled."""
        count = self.db.get_ticket_count()
        if self.settings_page.is_test_data_enabled():
            if count == 0:
                self.status_label.setText("Loading demo data...")
                QApplication.processEvents()
                self.db.load_demo_data()
                count = self.db.get_ticket_count()
                self.status_label.setText("Demo data loaded")
            self.conversations_page.set_data_source_label("Testing data")
        else:
            label = "No data loaded" if count == 0 else f"{count:,} conversations"
            self.conversations_page.set_data_source_label(label)

        self.ticket_count_label.setText(f"{count} conversations in database")

        # Populate TRC filters on all pages
        self.conversations_page.populate_trc_filter()
        self.dashboard_page.populate_trc_filter()
        self.trending_page.populate_trc_filter()
        self.incidents_page.populate_trc_filter()
        self.reports_page.populate_trc_filter()

        # Rebuild count tables for incident monitoring
        self.db.populate_daily_counts()
        self.db.populate_hourly_counts()

        # Sync date pickers on all pages to match data range
        self.dashboard_page.sync_date_to_data()
        self.trending_page.sync_date_to_data()
        self.incidents_page.sync_date_to_data()
        self.reports_page.sync_date_to_data()

        # Auto-search on launch
        QTimer.singleShot(100, self.conversations_page.run_search)

        # Initialize incident badge
        self._update_incident_badge(0)

    # ═══════════════════════════════════════════
    #  SETTINGS WIRING
    # ═══════════════════════════════════════════

    def _on_datasets_changed(self, datasets):
        """Settings: dataset list changed."""
        self.conversations_page.update_datasets(datasets)

    def _on_api_toggled(self, enabled):
        """Settings: API toggle changed."""
        self.conversations_page.update_api_state(enabled)
        # Push current PAT to conversations page
        self.conversations_page.update_pat(self.settings_page.get_pat())

    def _on_test_data_toggled(self, enabled):
        """Settings: test data toggle changed."""
        self.conversations_page.update_test_mode(enabled)
        if enabled:
            # Load demo data
            count = self.db.get_ticket_count()
            if count == 0:
                self.status_label.setText("Loading demo data...")
                QApplication.processEvents()
                self.db.load_demo_data()
                self.status_label.setText("Demo data loaded")

            count = self.db.get_ticket_count()
            self.ticket_count_label.setText(f"{count} conversations in database")
            self.conversations_page.set_data_source_label("Testing data")
            self.conversations_page.populate_trc_filter()
            self.dashboard_page.populate_trc_filter()
            self.trending_page.populate_trc_filter()
            self.incidents_page.populate_trc_filter()
            self.reports_page.populate_trc_filter()
            self.conversations_page.run_search()
            # Rebuild count tables for incident monitoring
            self.db.populate_daily_counts()
            self.db.populate_hourly_counts()
        else:
            # Clear all data — live mode
            self._clear_all_data()
            self.ticket_count_label.setText("0 conversations in database")
            self.conversations_page.set_data_source_label("No data loaded")
            self.conversations_page.populate_trc_filter()
            self.dashboard_page.populate_trc_filter()
            self.trending_page.populate_trc_filter()
            self.incidents_page.populate_trc_filter()
            self.conversations_page.clear_filters()
            self.status_label.setText("Test data cleared — import or pull live data")

    def _on_data_loaded(self, stats):
        """Conversations page: CSV import or API ingestion completed."""
        count = stats.get("tickets_created", stats.get("conversations", 0))
        self.ticket_count_label.setText(f"{count} conversations in database")
        source = "CSV" if "total_csv_rows" in stats else "Lightdash"
        self.status_label.setText(f"Imported {count:,} conversations from {source}")

        # Refresh TRC filters on analytics pages
        self.dashboard_page.populate_trc_filter()
        self.trending_page.populate_trc_filter()
        self.incidents_page.populate_trc_filter()
        self.reports_page.populate_trc_filter()

        # Sync date pickers on ALL pages to match imported data range
        self.dashboard_page.sync_date_to_data()
        self.trending_page.sync_date_to_data()
        self.incidents_page.sync_date_to_data()
        self.reports_page.sync_date_to_data()
        self.conversations_page._sync_date_filters_to_data()

        # Rebuild count tables for incident monitoring
        self.db.populate_daily_counts()
        self.db.populate_hourly_counts()

        # Queue analysis jobs sequentially (gated by behavior settings)
        self._queue_auto_analysis()

    def _on_debug_mode_toggled(self, enabled):
        """Settings: debug canary toggle changed."""
        self.conversations_page.update_debug_mode(enabled)

    def _on_settings_changed(self, changes: dict):
        """Settings: generic change notification (e.g. Gemini config updated)."""
        if changes.get("gemini_updated"):
            self.reports_page.refresh_gemini_status()
            self.ab_compare_page.refresh_gemini_status()
            self.smart_reporting_page.refresh_gemini_status()
        if changes.get("interventions_updated"):
            # Refresh intervention markers on incident chart
            try:
                interventions = self.db.get_interventions()
                self.incidents_page.control_chart.set_interventions(interventions)
            except Exception:
                pass
        if changes.get("layman_mode_updated"):
            self._update_layman_indicator()
        if changes.get("behavior_updated"):
            self._apply_section_defaults()

    def _update_layman_indicator(self):
        """Update the status bar layman mode indicator."""
        try:
            enabled = self.settings_page.is_layman_mode_enabled()
            if enabled:
                self.layman_mode_indicator.setText("\U0001F4DD Simplified mode")
            else:
                self.layman_mode_indicator.setText("")
        except Exception:
            pass

    # ═══════════════════════════════════════════
    #  JOB QUEUE WIRING
    # ═══════════════════════════════════════════

    def _wire_job_queue(self):
        """Connect JobQueue signals to JobOverlay and status bar."""
        q = self._job_queue
        o = self._job_overlay

        q.job_started.connect(lambda jid, desc: o.update_status(desc))
        q.job_started.connect(lambda jid, desc: o.show_overlay())
        q.job_progress.connect(lambda jid, msg, pct: o.update_status(msg))
        q.queue_changed.connect(lambda jobs: o.update_job_list(jobs))
        q.queue_empty.connect(lambda: o.hide_overlay())

        # Deferred import summary (after all jobs complete, so it doesn't block)
        q.queue_empty.connect(self._show_deferred_import_summary)

        # Status bar
        q.job_started.connect(lambda jid, desc: self.status_label.setText(desc))
        q.job_finished.connect(lambda jid, name: self.status_label.setText(f"✓ {name} complete"))
        q.job_failed.connect(lambda jid, name, err: self.status_label.setText(f"✗ {name} failed"))
        q.queue_empty.connect(lambda: self.status_label.setText("Ready"))

    def _show_deferred_import_summary(self):
        """Show CSV import summary after all jobs have finished."""
        try:
            self.conversations_page.show_deferred_import_summary()
        except Exception:
            pass

    def _queue_auto_analysis(self):
        """Queue sequential analysis jobs gated by behavior settings."""
        aa = self._get_auto_analysis_config()
        jobs = []

        # TRC Analytics
        trc = aa.get("trc_analytics", {})
        if trc.get("enabled", True):
            jobs.append(self._make_analytics_job())

        # Incidents
        inc = aa.get("incidents", {})
        if inc.get("enabled", True):
            if (inc.get("trc_status_grid", True) or inc.get("control_chart", True)
                    or inc.get("open_incidents", True)):
                jobs.append(self._make_incident_scan_job())
            if inc.get("theta_anomaly_scan", True):
                jobs.append(self._make_theta_scan_job())

        # Trending
        trn = aa.get("trending", {})
        if trn.get("enabled", True):
            jobs.append(self._make_trending_job())

        if jobs:
            self._job_queue.submit_batch(jobs)

    # ── Job Factories ────────────────────────────────────────────────

    def _make_analytics_job(self) -> JobDescriptor:
        """Create a JobDescriptor for TRC Analytics refresh."""
        page = self.dashboard_page
        queue = self._job_queue

        def create_worker():
            page.sync_date_to_data()
            page.populate_trc_filter()

            date_start = page.date_from.date().toString("yyyy-MM-dd")
            date_end = page.date_to.date().toString("yyyy-MM-dd") + "T23:59:59"
            trc_filter = page.trc_combo.currentData() or None
            status_filter = page.status_combo.currentText()
            if status_filter == "All":
                status_filter = None

            from src.ui.pages.trc_analytics import AnalyticsWorker
            worker = AnalyticsWorker(
                self.db.db_path, date_start, date_end, trc_filter, status_filter
            )
            page._worker = worker

            worker.finished.connect(page._on_results)
            worker.error.connect(page._on_error)
            return worker

        return JobDescriptor(
            job_id="trc_analytics_refresh",
            name="TRC Analytics",
            description="Computing TRC analytics",
            create_worker=create_worker,
        )

    def _make_incident_scan_job(self) -> JobDescriptor:
        """Create a JobDescriptor for incident anomaly scan."""
        page = self.incidents_page
        queue = self._job_queue

        def create_worker():
            import time
            page.sync_date_to_data()

            try:
                self.db.populate_daily_counts()
                self.db.populate_hourly_counts()
            except Exception:
                pass

            page._scan_start_time = time.time()
            date_from = page.date_from.date().toString("yyyy-MM-dd")
            date_to = page.date_to.date().toString("yyyy-MM-dd")

            from src.ui.pages.incidents_page import IncidentWorker
            worker = IncidentWorker(self.db.db_path, date_to, date_from)
            page._worker = worker

            worker.finished.connect(page._on_scan_results)
            worker.error.connect(page._on_scan_error)
            worker.progress.connect(
                lambda msg, pct: queue.job_progress.emit("incident_scan", msg, pct)
            )
            return worker

        return JobDescriptor(
            job_id="incident_scan",
            name="Incident Scan",
            description="Running incident anomaly scan",
            create_worker=create_worker,
        )

    def _make_theta_scan_job(self) -> JobDescriptor:
        """Create a JobDescriptor for theta (EWMA) anomaly scan."""
        page = self.incidents_page
        queue = self._job_queue

        def create_worker():
            from src.ui.pages.incidents_page import ThetaWorker
            worker = ThetaWorker(self.db.db_path)
            page._theta_worker = worker

            worker.finished.connect(page._on_theta_results)
            worker.error.connect(lambda err: None)  # silent on error for auto-scan
            worker.progress.connect(
                lambda msg: queue.job_progress.emit("theta_scan", msg, -1)
            )
            return worker

        return JobDescriptor(
            job_id="theta_scan",
            name="Theta Anomaly Scan",
            description="Running EWMA anomaly detection",
            create_worker=create_worker,
        )

    def _make_trending_job(self) -> JobDescriptor:
        """Create a JobDescriptor for trending topics analysis."""
        page = self.trending_page
        queue = self._job_queue

        def create_worker():
            import time
            page.sync_date_to_data()
            page.populate_trc_filter()

            page._scan_start_time = time.time()
            date_start = page.date_from.date().toString("yyyy-MM-dd")
            date_end = page.date_to.date().toString("yyyy-MM-dd") + "T23:59:59"
            trc_filter = page.trc_combo.currentData() or None
            window_size = page.window_combo.currentText()
            topic_method = page.method_combo.currentData()

            from src.ui.pages.trending_topics import TrendingWorker
            worker = TrendingWorker(
                self.db.db_path, date_start, date_end, trc_filter,
                window_size, topic_method,
            )
            page._worker = worker

            worker.finished.connect(page._on_results)
            worker.error.connect(page._on_error)
            worker.progress.connect(
                lambda msg: queue.job_progress.emit("trending_analysis", msg, -1)
            )
            return worker

        return JobDescriptor(
            job_id="trending_analysis",
            name="Trending Topics",
            description="Analyzing trending topics",
            create_worker=create_worker,
        )

    # ═══════════════════════════════════════════
    #  BEHAVIOR HELPERS
    # ═══════════════════════════════════════════

    def _get_auto_analysis_config(self):
        """Read behavior.auto_analysis from config/settings.yaml."""
        import yaml
        from pathlib import Path
        config_path = Path(__file__).resolve().parent.parent.parent / "config" / "settings.yaml"
        try:
            if config_path.exists():
                with open(config_path, encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                return cfg.get("behavior", {}).get("auto_analysis", {})
        except Exception:
            pass
        return {}

    def _is_calendar_sync_enabled(self):
        """Check if analysis page calendar sync is enabled."""
        import yaml
        from pathlib import Path
        config_path = Path(__file__).resolve().parent.parent.parent / "config" / "settings.yaml"
        try:
            if config_path.exists():
                with open(config_path, encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                return cfg.get("behavior", {}).get("calendar_sync", {}).get("analysis_pages", False)
        except Exception:
            pass
        return False

    def _is_ai_reports_sync_enabled(self):
        """Check if AI Reports calendar sync is enabled."""
        import yaml
        from pathlib import Path
        config_path = Path(__file__).resolve().parent.parent.parent / "config" / "settings.yaml"
        try:
            if config_path.exists():
                with open(config_path, encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                cs = cfg.get("behavior", {}).get("calendar_sync", {})
                return cs.get("analysis_pages", False) and cs.get("ai_reports", False)
        except Exception:
            pass
        return False

    # ═══════════════════════════════════════════
    #  CALENDAR SYNC
    # ═══════════════════════════════════════════

    def _setup_calendar_sync(self):
        """Connect date_changed signals from all analysis pages for cross-page sync."""
        self._syncing_dates = False  # Guard to prevent re-entry
        self._reanalysis_timer = QTimer(self)
        self._reanalysis_timer.setSingleShot(True)
        self._reanalysis_timer.setInterval(500)
        self._reanalysis_pages_to_refresh = set()

        self._reanalysis_timer.timeout.connect(self._do_reanalysis)

        # TRC Analytics date pickers
        self.dashboard_page.date_from.date_changed.connect(
            lambda d: self._on_analysis_date_changed("trc_analytics", "from", d)
        )
        self.dashboard_page.date_to.date_changed.connect(
            lambda d: self._on_analysis_date_changed("trc_analytics", "to", d)
        )

        # Trending Topics date pickers
        self.trending_page.date_from.date_changed.connect(
            lambda d: self._on_analysis_date_changed("trending", "from", d)
        )
        self.trending_page.date_to.date_changed.connect(
            lambda d: self._on_analysis_date_changed("trending", "to", d)
        )

        # Incidents date pickers
        self.incidents_page.date_from.date_changed.connect(
            lambda d: self._on_analysis_date_changed("incidents", "from", d)
        )
        self.incidents_page.date_to.date_changed.connect(
            lambda d: self._on_analysis_date_changed("incidents", "to", d)
        )

        # AI Reports date pickers
        self.reports_page._date_from.date_changed.connect(
            lambda d: self._on_reports_date_changed("from", d)
        )
        self.reports_page._date_to.date_changed.connect(
            lambda d: self._on_reports_date_changed("to", d)
        )

    def _on_analysis_date_changed(self, source: str, which: str, date):
        """An analysis page date picker was changed by user click."""
        if self._syncing_dates or not self._is_calendar_sync_enabled():
            return

        self._syncing_dates = True
        try:
            # Map of page_key → (page_object, date_from_attr, date_to_attr)
            pages = {
                "trc_analytics": self.dashboard_page,
                "trending": self.trending_page,
                "incidents": self.incidents_page,
            }

            # Propagate date to other analysis pages
            for key, page in pages.items():
                if key == source:
                    continue
                picker = page.date_from if which == "from" else page.date_to
                picker.setDate(date)  # setDate() does NOT emit date_changed

            # Also sync to AI Reports if enabled
            if self._is_ai_reports_sync_enabled():
                rp = self.reports_page
                picker = rp._date_from if which == "from" else rp._date_to
                picker.setDate(date)

            # Schedule re-analysis on synced pages
            self._schedule_reanalysis(exclude=source)
        finally:
            self._syncing_dates = False

    def _on_reports_date_changed(self, which: str, date):
        """AI Reports page date picker was changed by user click."""
        if self._syncing_dates or not self._is_ai_reports_sync_enabled():
            return

        self._syncing_dates = True
        try:
            # Propagate to all analysis pages
            for page in (self.dashboard_page, self.trending_page, self.incidents_page):
                picker = page.date_from if which == "from" else page.date_to
                picker.setDate(date)

            # Schedule re-analysis on all analysis pages
            self._schedule_reanalysis(exclude=None)
        finally:
            self._syncing_dates = False

    def _schedule_reanalysis(self, exclude=None):
        """Debounced re-run of analysis on synced pages.

        exclude: page_key to skip (the page that initiated the change)
        """
        pages_to_refresh = {"trc_analytics", "trending", "incidents"}
        if exclude:
            pages_to_refresh.discard(exclude)
        self._reanalysis_pages_to_refresh |= pages_to_refresh
        self._reanalysis_timer.start()  # restart the 500ms debounce

    def _do_reanalysis(self):
        """Execute deferred re-analysis on synced pages via job queue."""
        pages = self._reanalysis_pages_to_refresh.copy()
        self._reanalysis_pages_to_refresh.clear()

        # Cancel any stale pending analysis jobs
        for pid in ("trc_analytics_refresh", "incident_scan", "theta_scan", "trending_analysis"):
            self._job_queue.cancel_pending(pid)

        jobs = []
        if "trc_analytics" in pages:
            jobs.append(self._make_analytics_job())
        if "incidents" in pages:
            jobs.append(self._make_incident_scan_job())
            jobs.append(self._make_theta_scan_job())
        if "trending" in pages:
            jobs.append(self._make_trending_job())

        if jobs:
            self._job_queue.submit_batch(jobs)

    # ═══════════════════════════════════════════
    #  SECTION DEFAULTS
    # ═══════════════════════════════════════════

    def _apply_section_defaults(self):
        """Apply current section_defaults from settings to all CollapsibleSections."""
        from src.ui.widgets.collapsible_section import CollapsibleSection

        for page in (self.dashboard_page, self.trending_page, self.incidents_page):
            sections = page.findChildren(CollapsibleSection)
            for section in sections:
                key = section.section_key
                if key:
                    should_collapse = CollapsibleSection.get_default_collapsed(key)
                    section.set_collapsed(should_collapse)

    # ═══════════════════════════════════════════
    #  INCIDENT BADGE
    # ═══════════════════════════════════════════

    def _update_incident_badge(self, count_2theta: int = 0):
        """Update sidebar badge for Incidents with 2θ flag count."""
        for btn, page_idx in self._sidebar_buttons:
            if page_idx == self.PAGE_INCIDENTS:
                if count_2theta > 0:
                    btn.setText(f"\U0001F6A8  Incidents ({count_2theta})")
                else:
                    btn.setText("\U0001F6A8  Incidents")
                break

    # ═══════════════════════════════════════════
    #  NLP SCANNER NAVIGATION
    # ═══════════════════════════════════════════

    def _on_nlp_deep_dive(self, finding_id, finding_title):
        """Navigate to AI Reports and trigger drilldown for a finding."""
        self._set_active_page(self.PAGE_REPORTS)
        self.reports_page.load_nlp_finding(finding_id, finding_title)

    def _on_nlp_view_tickets(self, ticket_ids):
        """Navigate to Conversations page filtered to specific tickets."""
        self._set_active_page(self.PAGE_CONVERSATIONS)
        self.conversations_page.filter_by_ticket_ids(ticket_ids)

    # ═══════════════════════════════════════════
    #  DIALOGS
    # ═══════════════════════════════════════════

    def _show_help(self):
        dlg = HelpDialog(self)
        dlg.exec()

    def _show_feedback(self):
        dlg = HelpDialog(self)
        # Switch to feedback tab
        tabs = dlg.findChild(type(dlg.findChildren(type(None))[0]).__class__) if False else None
        from PySide6.QtWidgets import QTabWidget
        for child in dlg.findChildren(QTabWidget):
            child.setCurrentIndex(2)  # Feedback tab
            break
        dlg.exec()

    def _stop_all_workers(self):
        """Stop any running background worker threads before closing."""
        # Cancel the unified job queue
        if hasattr(self, '_job_queue'):
            self._job_queue.cancel_all()

        # Also stop any workers not managed by the queue (HypothesisWorker)
        workers = []
        if hasattr(self, 'trending_page'):
            if getattr(self.trending_page, '_hyp_worker', None):
                workers.append(self.trending_page._hyp_worker)

        for w in workers:
            if w and w.isRunning():
                w.quit()
                w.wait(2000)

    def _clear_all_data(self):
        """Delete imported ticket data and derived analytics, but preserve
        NLP scan results (scan history, findings, sub-taxonomy, classifications).

        NLP scan results represent expensive Gemini API calls that should
        not be lost on session close. They remain useful across imports.

        More reliable than file deletion on Windows where SQLite WAL/SHM
        files may be locked by other processes or lingering handles.
        """
        # Tables to preserve across clear operations
        PRESERVE_TABLES = {
            'nlp_scan_runs',           # Scan history metadata
            'nlp_batches',             # Batch details (raw_response for recovery)
            'nlp_ticket_classifications',  # Classification results
            'nlp_findings',            # Meta-analysis findings
            'sub_patterns',            # Learned sub-taxonomy
            'sub_pattern_ngrams',      # Sub-pattern n-gram indexes
            'sub_pattern_snapshots',   # Sub-pattern history
            'provisional_classifications',  # Provisional classifications
            'prompt_library',          # User-customized prompts
        }

        try:
            conn = self.db.conn

            # 1. Drop the FTS virtual table first (also removes shadow tables)
            try:
                conn.execute("DROP TABLE IF EXISTS conversations_fts")
            except Exception:
                pass

            # 2. Get remaining user tables (skip sqlite internals + FTS shadow leftovers)
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' "
                "AND name NOT LIKE 'conversations_fts%'"
            ).fetchall()

            # 3. Delete from each table individually, skipping preserved NLP tables
            for (table_name,) in tables:
                if table_name in PRESERVE_TABLES:
                    continue
                try:
                    conn.execute(f"DELETE FROM [{table_name}]")
                except Exception:
                    pass

            conn.commit()

            # 4. VACUUM to reclaim disk space (must be outside transaction)
            try:
                conn.execute("VACUUM")
            except Exception:
                pass
        except Exception:
            pass

    def closeEvent(self, event):
        """Prompt to clear session data on close."""
        # Only prompt if there's real (non-test) data loaded
        is_test = self.settings_page.is_test_data_enabled()
        count = self.db.get_ticket_count()

        if count > 0 and not is_test:
            from PySide6.QtWidgets import QMessageBox
            msg = QMessageBox(self)
            msg.setWindowTitle("Close Alma Insights")
            msg.setText("Clear session data?")
            msg.setInformativeText(
                "Imported ticket data will be removed from your local machine. "
                "Any unsaved reports should be exported first.\n\n"
                "This keeps your machine clean and protects data privacy."
            )
            msg.setStandardButtons(
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel
            )
            msg.setDefaultButton(QMessageBox.Yes)
            msg.button(QMessageBox.Yes).setText("Clear && Close")
            msg.button(QMessageBox.No).setText("Keep && Close")
            msg.button(QMessageBox.Cancel).setText("Cancel")

            result = msg.exec()

            if result == QMessageBox.Cancel:
                event.ignore()
                return
            elif result == QMessageBox.Yes:
                # 1. Stop all background workers (they hold DB connections)
                self._stop_all_workers()

                # 2. Clear all data via SQL (reliable on Windows)
                self._clear_all_data()

                # 3. Close the main connection
                self.db.close()

                # 4. Best-effort file cleanup (WAL, SHM, DB)
                import shutil
                from pathlib import Path
                data_dir = Path(self.db.db_path).parent
                try:
                    if data_dir.exists():
                        shutil.rmtree(data_dir, ignore_errors=True)
                except Exception:
                    pass

                event.accept()
                return

        self.db.close()
        event.accept()
