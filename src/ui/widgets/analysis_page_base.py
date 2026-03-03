"""
Alma Insights — Analysis Page Base Building Block

Base class for all analysis pages (TRC Analytics, Trending Topics, Incidents).
Provides the consistent skeleton: PageHeader → SharedFilterBar → QTabWidget.

Subclasses configure filters, add tabs, and override data-fetching methods.

Usage:
    class TrendingTopicsPage(AnalysisPageBase):
        def __init__(self, db_manager, parent=None):
            super().__init__(db_manager, "Trending Topics",
                             subtitle="Sentiment, terms, clusters", parent=parent)
            self._setup_filters()
            self._setup_tabs()

        def _setup_filters(self):
            self.filter_bar.add_date_range()
            self.filter_bar.add_combo_filter("trc", "TRC", ["All TRCs"])
            self.filter_bar.add_action_button("Analyze")

        def _on_filters_changed(self, filters):
            ...  # re-query data
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTabWidget,
)
from PySide6.QtCore import Qt

from src.ui.widgets.page_header import PageHeader
from src.ui.widgets.shared_filter_bar import SharedFilterBar
from src.ui.theme import ALMA_CREAM


class AnalysisPageBase(QWidget):
    """Base class for tabbed analysis pages.

    Provides:
        - PageHeader (title + subtitle + action area)
        - SharedFilterBar (dates, combos, action buttons)
        - QTabWidget (for content tabs)
        - DrilldownPanel wiring
    """

    def __init__(self, db_manager, page_title: str,
                 subtitle: str = "", parent=None):
        super().__init__(parent)
        self.db = db_manager
        self._drilldown = None

        self.setStyleSheet(f"background: {ALMA_CREAM};")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(28, 20, 28, 0)
        main_layout.setSpacing(12)

        # 1. Page Header
        self._header = PageHeader(page_title, subtitle)
        main_layout.addWidget(self._header)

        # 2. Filter Bar
        self._filter_bar = SharedFilterBar()
        self._filter_bar.filters_changed.connect(self._on_filters_changed)
        self._filter_bar.action_triggered.connect(self._on_action_triggered)
        main_layout.addWidget(self._filter_bar)

        # 3. Tab Widget
        self._tab_widget = QTabWidget()
        self._tab_widget.setObjectName("AnalysisTab")
        self._tab_widget.setDocumentMode(True)
        main_layout.addWidget(self._tab_widget, 1)

    # ═══════════════════════════════════════════
    #  PUBLIC API
    # ═══════════════════════════════════════════

    @property
    def header(self) -> PageHeader:
        return self._header

    @property
    def filter_bar(self) -> SharedFilterBar:
        return self._filter_bar

    @property
    def tab_widget(self) -> QTabWidget:
        return self._tab_widget

    def add_tab(self, widget: QWidget, label: str) -> int:
        """Add a tab and return its index."""
        return self._tab_widget.addTab(widget, label)

    def set_drilldown_panel(self, panel):
        """Wire the DrilldownPanel (called by MainWindow)."""
        self._drilldown = panel

    @property
    def drilldown(self):
        """Access the drilldown panel."""
        return self._drilldown

    # ═══════════════════════════════════════════
    #  SUBCLASS HOOKS
    # ═══════════════════════════════════════════

    def _on_filters_changed(self, filters: dict):
        """Override in subclass to handle filter changes.

        Called when any filter value changes.
        Args:
            filters: dict with keys like 'date_from', 'date_to', 'trc', 'status'
        """
        pass

    def _on_action_triggered(self, action: str):
        """Override in subclass to handle action button clicks.

        Args:
            action: The button text (e.g. 'Refresh', 'Analyze', 'Run Scan')
        """
        pass
