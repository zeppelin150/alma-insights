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
from src.ui.pages.placeholders import ReportsPage
from src.ui.pages.settings_page import SettingsPage
from src.ui.dialogs.help_dialog import HelpDialog
from src.data.db_manager import DatabaseManager


class MainWindow(QMainWindow):

    PAGE_CONVERSATIONS = 0
    PAGE_DASHBOARD = 1
    PAGE_TRENDING = 2
    PAGE_INCIDENTS = 3
    PAGE_REPORTS = 4
    PAGE_SETTINGS = 5

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

        # ── ANALYSIS section ──
        section1 = QLabel("ANALYSIS")
        section1.setObjectName("SidebarSection")
        layout.addWidget(section1)

        layout.addWidget(self._sidebar_btn("🔍  Conversations", self.PAGE_CONVERSATIONS))
        layout.addWidget(self._sidebar_btn("📊  TRC Analytics", self.PAGE_DASHBOARD))
        layout.addWidget(self._sidebar_btn("📈  Trending Topics", self.PAGE_TRENDING))
        layout.addWidget(self._sidebar_btn("🚨  Incidents", self.PAGE_INCIDENTS))

        # Divider
        div1 = QFrame()
        div1.setObjectName("SidebarDivider")
        layout.addWidget(div1)

        # ── REPORTS section ──
        section2 = QLabel("REPORTS")
        section2.setObjectName("SidebarSection")
        layout.addWidget(section2)

        layout.addWidget(self._sidebar_btn("🤖  AI Reports", self.PAGE_REPORTS))

        # Divider
        div2 = QFrame()
        div2.setObjectName("SidebarDivider")
        layout.addWidget(div2)

        # ── SYSTEM section ──
        section3 = QLabel("SYSTEM")
        section3.setObjectName("SidebarSection")
        layout.addWidget(section3)

        layout.addWidget(self._sidebar_btn("⚙️  Settings", self.PAGE_SETTINGS))

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

        # Page 4: Reports (placeholder)
        self.reports_page = ReportsPage(self.db)
        self.content_stack.addWidget(self.reports_page)

        # Page 5: Settings
        self.settings_page = SettingsPage()
        self.settings_page.datasets_changed.connect(self._on_datasets_changed)
        self.settings_page.test_data_changed.connect(self._on_test_data_toggled)
        self.settings_page.debug_mode_changed.connect(self._on_debug_mode_toggled)
        self.settings_page.api_toggle.toggled.connect(self._on_api_toggled)
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

        # Rebuild hourly counts for incident monitoring
        self.db.populate_hourly_counts()

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
            self.conversations_page.run_search()
            # Rebuild hourly counts for incident monitoring
            self.db.populate_hourly_counts()
        else:
            # Clear all data — live mode
            self.db.conn.execute("DELETE FROM conversations")
            self.db.conn.execute("DELETE FROM comments")
            self.db.conn.execute("DELETE FROM tickets")
            self.db.conn.commit()
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
        # Rebuild hourly counts and auto-run incident scan
        self.db.populate_hourly_counts()
        self.incidents_page.auto_run_scan()
        # Auto-run θ EWMA scan after data import
        self.incidents_page.auto_run_theta_scan()

    def _on_debug_mode_toggled(self, enabled):
        """Settings: debug canary toggle changed."""
        self.conversations_page.update_debug_mode(enabled)

    # ═══════════════════════════════════════════
    #  INCIDENT BADGE
    # ═══════════════════════════════════════════

    def _update_incident_badge(self, count_2theta: int = 0):
        """Update sidebar badge for Incidents with 2θ flag count."""
        for btn, page_idx in self._sidebar_buttons:
            if page_idx == self.PAGE_INCIDENTS:
                if count_2theta > 0:
                    btn.setText(f"🚨  Incidents ({count_2theta})")
                else:
                    btn.setText("🚨  Incidents")
                break

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
                # Wipe the data directory
                import shutil
                from pathlib import Path
                data_dir = Path(self.db.db_path).parent
                self.db.close()
                if data_dir.exists():
                    shutil.rmtree(data_dir, ignore_errors=True)
                event.accept()
                return

        self.db.close()
        event.accept()
