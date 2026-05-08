"""
Alma Insights — Trending Topics Page
Sentiment trends, rising/cooling terms (TF-IDF velocity), topic clusters,
and term management (Pass 1.5).

Refactored to use AnalysisPageBase building blocks with 4 tabs:
  Tab 1 - Overview:  KPI cards + sentiment chart + Current Mood table
  Tab 2 - Deep Dive: Correlations + rising/cooling terms + topic clusters + AI tools
  Tab 3 - Hypothesis Test
  Tab 4 - Reports
"""

import json

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QFrame, QScrollArea,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QApplication, QSizePolicy, QTextBrowser,
    QGridLayout, QMenu, QInputDialog, QTabWidget, QTextEdit,
)
from PySide6.QtCore import Qt, QDate, QThread, Signal
from PySide6.QtGui import QFont, QColor

from src.data.settings_manager import load_settings, get_section
from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_GREEN_SUBTLE,
    ALMA_WHITE, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_TEXT_ON_DARK, ALMA_BORDER, ALMA_BORDER_LIGHT,
    ALMA_SUCCESS, ALMA_WARNING, ALMA_ERROR, ALMA_INFO,
    ALMA_BG_ELEVATED, apply_card_shadow, apply_card_shadow_soft,
    configure_table,
)
from src.ui.widgets.date_picker import ModernDatePicker
from src.ui.widgets.charts import LineChartWidget, SparklineWidget, DualSparklineWidget, ChartModeSwitcher
from src.ui.widgets.chart_builders import ChartLegendSection, TRCDropdownSelector
from src.ui.widgets.report_history_summary import ReportHistorySummary
from src.ui.widgets.smoothing_panel import SmoothingReviewPanel
from src.ui.widgets.keyword_panel import KeywordReviewPanel
from src.ui.widgets.term_manager_panel import TermManagerPanel
from src.ui.widgets.collapsible_section import CollapsibleSection
from src.ui.widgets.pagination_bar import PaginationBar
from src.ui.widgets.empty_state import EmptyState
from src.ui.widgets.skeleton import SkeletonGroup
from src.ui.widgets.analysis_page_base import AnalysisPageBase
from src.ui.widgets.kpi_card import KPICard, KPICardRow
from src.ui.widgets.tab_scroll_content import TabScrollContent
from src.ui.widgets.reports_tab import ReportsTab
from src.ui.widgets.filter_chip_bar import FilterChipBar
from src.ui.layman_mode import is_layman_mode, translate_label, format_sentiment, format_tfidf


# ═══════════════════════════════════════════
#  WORKER THREAD
# ═══════════════════════════════════════════

class TrendingWorker(QThread):
    """Background thread running the 12-step trending engine analysis.

    Runs `trending_engine.run_analysis()` which produces TF-IDF rising
    terms, VADER sentiment, topic clusters, CUSUM drift, and z-score
    anomalies. Results are emitted via `finished(dict)`.
    """

    progress = Signal(str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, db_path, date_start, date_end, trc_filter, window_size,
                 topic_method="nmf", source_id=None):
        super().__init__()
        self.db_path = db_path
        self.date_start = date_start
        self.date_end = date_end
        self.trc_filter = trc_filter
        self.window_size = window_size
        self.topic_method = topic_method
        self.source_id = source_id

    def run(self):
        try:
            from src.data.db_manager import DatabaseManager
            from src.data.trending_engine import run_full_analysis

            db = DatabaseManager(self.db_path)
            db.initialize()

            def on_progress(step, total, msg):
                self.progress.emit(msg)

            result = run_full_analysis(
                db.conn, self.date_start, self.date_end,
                self.trc_filter, self.window_size,
                topic_method=self.topic_method,
                progress_callback=on_progress,
                db=db,
                source_id=self.source_id,
            )

            db.close()
            self.finished.emit(result)
        except Exception as e:
            import traceback
            self.error.emit(traceback.format_exc())


# ═══════════════════════════════════════════
#  AI ENHANCEMENT WORKER THREAD
# ═══════════════════════════════════════════

class AIEnhancementWorker(QThread):
    """Background thread for Gemini AI enhancements (smoothing + keywords).

    Runs the synchronous Gemini calls off the main thread so the UI
    stays responsive.  Emits results via signals for the main thread
    to apply.
    """

    smoothing_ready = Signal(list)   # list of suggestion dicts
    keywords_ready = Signal(dict)    # {"suppress": [...], "add_to_map": [...]}
    finished_all = Signal()          # emitted when both tasks are done
    error = Signal(str)

    def __init__(self, result: dict, smoothing_enabled: bool, keywords_enabled: bool):
        super().__init__()
        self._result = result
        self._smoothing_enabled = smoothing_enabled
        self._keywords_enabled = keywords_enabled

    def run(self):
        try:
            from src.gemini.client_factory import build_client_for_task
            client = build_client_for_task("voc_analysis")
            if client is None or not client.is_available():
                self.finished_all.emit()
                return

            topics = self._result.get("topics", {})
            terms = self._result.get("terms", {})

            # ── AI Cluster Smoothing ──────────────────────────────
            if self._smoothing_enabled and topics:
                topic_list = topics.get("topics", topics.get("clusters", []))
                if topic_list:
                    from src.data.trending_engine import smooth_clusters_with_ai
                    ai_smoothing = smooth_clusters_with_ai(topic_list, client)
                    if ai_smoothing:
                        suggestions = []
                        for idx, s in ai_smoothing.items():
                            stat_label = ""
                            if idx < len(topic_list):
                                t = topic_list[idx]
                                top_terms = t.get("top_terms", [])
                                stat_label = ", ".join(
                                    (tt["term"] if isinstance(tt, dict) else str(tt))
                                    for tt in top_terms[:4]
                                )
                            suggestions.append({
                                "cluster_id": idx,
                                "statistical_label": stat_label,
                                "ai_label": s.get("label", ""),
                                "confidence": s.get("confidence", 0),
                                "action": (
                                    "merge" if s.get("merge_with") is not None
                                    else "split" if s.get("split_into")
                                    else "relabel"
                                ),
                                "merge_with": s.get("merge_with"),
                                "split_into": s.get("split_into"),
                            })
                        self.smoothing_ready.emit(suggestions)

            # ── AI Keyword Suggestions ────────────────────────────
            if self._keywords_enabled and terms:
                from src.data.trending_engine import suggest_keyword_improvements
                kw_result = suggest_keyword_improvements(terms, client)
                suppress = kw_result.get("suppress", [])
                add_to_map = kw_result.get("add_to_map", [])
                if suppress or add_to_map:
                    self.keywords_ready.emit({
                        "suppress": suppress, "add_to_map": add_to_map,
                    })

            self.finished_all.emit()

        except Exception:
            import traceback
            self.error.emit(traceback.format_exc())
            self.finished_all.emit()


# ═══════════════════════════════════════════
#  HYPOTHESIS WORKER THREAD
# ═══════════════════════════════════════════

class HypothesisWorker(QThread):
    """Background thread that tests a user-entered hypothesis via Gemini.

    Builds evidence (matching tickets, temporal patterns, correlations)
    and asks Gemini to evaluate whether the hypothesis is supported
    by the data. Uses `config/prompts/hypothesis.txt`.
    """

    progress = Signal(str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, db_path, hypothesis, date_start, date_end, window_size, gemini_client=None):
        super().__init__()
        self.db_path = db_path
        self.hypothesis = hypothesis
        self.date_start = date_start
        self.date_end = date_end
        self.window_size = window_size
        self.gemini_client = gemini_client

    def run(self):
        try:
            from src.data.db_manager import DatabaseManager
            from src.data.trending_engine import test_hypothesis

            db = DatabaseManager(self.db_path)
            db.initialize()

            def on_progress(step, total, msg):
                self.progress.emit(msg)

            result = test_hypothesis(
                db.conn, self.hypothesis,
                self.date_start, self.date_end,
                window_size=self.window_size,
                progress_callback=on_progress,
                gemini_client=self.gemini_client,
            )
            db.close()
            self.finished.emit(result)
        except Exception as e:
            import traceback
            self.error.emit(traceback.format_exc())


# ═══════════════════════════════════════════
#  TRENDING TOPICS PAGE
# ═══════════════════════════════════════════

class TrendingTopicsPage(AnalysisPageBase):
    """Trending Topics page: TF-IDF, sentiment, forecasting, hypothesis testing.

    Tabs: Trending (rising/cooling terms, sentiment, topic clusters) and
    Hypothesis (user-driven Gemini-powered hypothesis testing with
    evidence assembly and statistical validation).
    """

    def __init__(self, db_manager, parent=None):
        super().__init__(
            db_manager,
            "Trending Topics",
            subtitle="Sentiment trends, rising terms, and topic clusters",
            parent=parent,
        )
        self._worker = None
        self._current_clusters = []
        self._scan_start_time = 0
        self._last_analysis_result = {}

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
    def window_combo(self):
        return self.filter_bar.get_combo("window")

    @property
    def method_combo(self):
        return self.filter_bar.get_combo("method")

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
        # v2: Action-first layout — primary button before filters
        self.filter_bar.add_primary_action("Analyze")
        from src.ui.widgets.source_selector import SourceSelector
        self._source_selector = SourceSelector(self)
        self._source_selector.setFixedWidth(160)
        self.filter_bar.add_custom_widget("Source", self._source_selector)
        try:
            from src.data.connection_factory import get_connection
            conn = get_connection(self.db.db_path)
            self._source_selector.refresh_sources(conn)
            conn.close()
        except Exception:
            pass
        self.filter_bar.add_date_range()
        self.filter_bar.add_combo_filter("trc", "TRC", ["All TRCs"])
        self.filter_bar.add_combo_filter(
            "window", "Window",
            ["Hourly", "Daily", "Weekly", "Biweekly", "Monthly"],
        )
        self.filter_bar.set_combo_value("window", "Weekly")
        self.filter_bar.add_combo_filter(
            "method", "Topic Method",
            ["NMF Topics (overlap)", "K-Means Clusters"],
        )
        # The method combo needs data roles for topic_method lookup.
        # SharedFilterBar adds text-only items, so we re-populate with data.
        method_combo = self.filter_bar.get_combo("method")
        method_combo.clear()
        method_combo.addItem("NMF Topics (overlap)", "nmf")
        method_combo.addItem("K-Means Clusters", "kmeans")

        # TRC combo needs data role too — re-populate with proper data
        trc_combo = self.filter_bar.get_combo("trc")
        trc_combo.clear()
        trc_combo.addItem("All TRCs", "")
        trc_combo.setMinimumWidth(160)

        # Manage Terms moved to its own tab (Tab 5)

    # ═══════════════════════════════════════════
    #  ACTION / FILTER HOOKS
    # ═══════════════════════════════════════════

    def _on_action_triggered(self, action):
        if action == "Analyze":
            self._on_analyze()

    def _on_filters_changed(self, filters):
        """Sync filter chip bar when filters change."""
        self._sync_chips()

    def _sync_chips(self):
        """Update the chip bar to reflect current filter state."""
        if not hasattr(self, '_chip_bar') or self._chip_bar is None:
            return  # Not yet initialized
        chips = {}
        trc = self.trc_combo.currentText() if self.trc_combo else ""
        if trc and trc != "All TRCs":
            chips["TRC"] = trc
        window = self.window_combo.currentText() if self.window_combo else ""
        if window and window != "Weekly":
            chips["Window"] = window
        method = self.method_combo.currentText() if self.method_combo else ""
        if method and "NMF" not in method:
            chips["Method"] = method
        self._chip_bar.set_filters(chips)

    def _on_chip_removed(self, key):
        """Reset filter when a chip is removed."""
        if key == "TRC":
            self.trc_combo.setCurrentIndex(0)
        elif key == "Window":
            self.filter_bar.set_combo_value("window", "Weekly")
        elif key == "Method":
            self.filter_bar.set_combo_value("method", "NMF Topics (overlap)")

    def _on_chip_clear_all(self):
        """Reset all filters to defaults."""
        self.trc_combo.setCurrentIndex(0)
        self.filter_bar.set_combo_value("window", "Weekly")
        self.filter_bar.set_combo_value("method", "NMF Topics (overlap)")

    # ═══════════════════════════════════════════
    #  TAB SETUP
    # ═══════════════════════════════════════════

    def _setup_tabs(self):
        # ── Tab 1: Overview ──
        self._overview_tab = TabScrollContent()
        self._build_overview_tab()
        self.add_tab(self._overview_tab, "Overview")

        # ── Tab 2: Deep Dive ──
        self._deep_dive_tab = TabScrollContent()
        self._build_deep_dive_tab()
        self.add_tab(self._deep_dive_tab, "Deep Dive")

        # ── Tab 3: Hypothesis Test ──
        self._hypothesis_tab = self._build_hypothesis_tab()
        self.add_tab(self._hypothesis_tab, "Hypothesis Test")

        # ── Tab 4: Reports ──
        self._reports_tab = ReportsTab("trending_topics", self.db)
        self.add_tab(self._reports_tab, "Reports")

        # ── Tab 5: Manage Terms ──
        self._terms_tab = TabScrollContent()
        terms_layout = self._terms_tab.content_layout
        self._term_panel = TermManagerPanel(self.db)
        self._term_panel.terms_changed.connect(self._on_terms_changed)
        terms_layout.addWidget(self._term_panel)
        self._terms_tab.add_stretch()
        self.add_tab(self._terms_tab, "Manage Terms")

        # Store scroll ref for dynamic chart height
        self._scroll = self._overview_tab

    # ── TAB 1: OVERVIEW ──────────────────────────────

    def _build_overview_tab(self):
        layout = self._overview_tab.content_layout

        # ── KPI Card Row ──
        self._kpi_row = KPICardRow()
        self._kpi_sentiment = self._kpi_row.add_card(
            KPICard("Sentiment Avg", "\u2014", "average polarity")
        )
        self._kpi_trc_count = self._kpi_row.add_card(
            KPICard("TRC Count", "\u2014", "unique TRC codes")
        )
        self._kpi_range = self._kpi_row.add_card(
            KPICard("Sentiment Range", "\u2014", "min → max for period")
        )
        self._kpi_period = self._kpi_row.add_card(
            KPICard("Analysis Period", "\u2014", "date range")
        )
        self._kpi_row.apply_accent_cycle()
        layout.addWidget(self._kpi_row)

        # ── Filter Chip Bar ──
        self._chip_bar = FilterChipBar()
        self._chip_bar.filter_removed.connect(self._on_chip_removed)
        self._chip_bar.all_cleared.connect(self._on_chip_clear_all)
        layout.addWidget(self._chip_bar)

        # ── Skeleton loading ──
        self._build_skeleton(layout)

        # ── Empty state ──
        self._empty_state = EmptyState(
            icon="chart",
            heading="No trending data",
            description="Run an analysis to see rising and cooling terms",
            action_label="Run Analysis",
        )
        self._empty_state.action_clicked.connect(self._on_analyze)
        self._empty_state.setVisible(False)
        layout.addWidget(self._empty_state)

        # ── Sentiment legend + chart + mood table ──
        self._build_sentiment_panel(layout)

        self._overview_tab.add_stretch()

        # Content sections for skeleton toggle (mood is independent)
        self._content_sections = [
            self._sentiment_legend, self._sentiment_section,
        ]

    # ── TAB 2: DEEP DIVE ─────────────────────────────

    def _build_deep_dive_tab(self):
        layout = self._deep_dive_tab.content_layout

        self._build_correlation_panel(layout)
        self._build_terms_panel(layout)
        self._build_topics_panel(layout)
        self._build_ai_tools_bar(layout)

        self._deep_dive_tab.add_stretch()

    # ═══════════════════════════════════════════
    #  SKELETON LOADING (Build 10.0: T12)
    # ═══════════════════════════════════════════

    def _build_skeleton(self, parent_layout):
        """Build shimmer loading placeholder matching the page layout."""
        self._skeleton = QWidget()
        skel_layout = QVBoxLayout(self._skeleton)
        skel_layout.setContentsMargins(0, 0, 0, 0)
        skel_layout.setSpacing(20)

        # Sentiment chart skeleton
        skel_layout.addWidget(SkeletonGroup.chart_area(self._skeleton))

        # Terms skeleton (two columns of rows)
        skel_layout.addWidget(SkeletonGroup.table_rows(4, self._skeleton))

        # Topic clusters skeleton
        skel_layout.addWidget(SkeletonGroup.kpi_row(3, self._skeleton))

        skel_layout.addStretch()
        self._skeleton.setVisible(False)
        parent_layout.addWidget(self._skeleton)

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
    #  SENTIMENT PANEL (Overview tab)
    # ═══════════════════════════════════════════

    def _build_sentiment_panel(self, parent_layout):
        # ── Standalone legend section (own card ABOVE chart) ──
        self._sentiment_legend = ChartLegendSection(
            chart_title="Sentiment Trend",
            section_key="trending.sentiment_trend.legend",
        )
        parent_layout.addWidget(self._sentiment_legend)

        # ── Chart section: card-wrapped TRC dropdown + mode switcher + chart ──
        chart_frame = QFrame()
        chart_frame.setObjectName("SentChartPanel")
        chart_frame.setStyleSheet(
            f"#SentChartPanel {{ background: {ALMA_BG_ELEVATED};"
            " border: none; border-radius: 12px; }}"
        )
        apply_card_shadow_soft(chart_frame)
        chart_frame_layout = QVBoxLayout(chart_frame)
        chart_frame_layout.setContentsMargins(12, 8, 12, 8)
        chart_frame_layout.setSpacing(8)

        self._sentiment_section = chart_frame

        # ── TRC dropdown selector (Lightdash-style) ──
        self._trc_dropdown = TRCDropdownSelector()
        self._trc_dropdown.selection_changed.connect(self._on_trc_selection_changed)
        chart_frame_layout.addWidget(self._trc_dropdown)

        # ── Chart mode switcher ──
        self._sentiment_mode_switcher = ChartModeSwitcher()
        chart_frame_layout.addWidget(self._sentiment_mode_switcher)

        # ── Chart (capped height — fits within the card) ──
        self.sentiment_chart = LineChartWidget()
        self.sentiment_chart.setMinimumHeight(220)
        self.sentiment_chart.setMaximumHeight(350)
        self._sentiment_mode_switcher.mode_changed.connect(self.sentiment_chart.set_chart_mode)
        chart_frame_layout.addWidget(self.sentiment_chart)

        parent_layout.addWidget(chart_frame)

        # ── Current Mood section: standalone card with table + pagination ──
        mood_frame = QFrame()
        mood_frame.setObjectName("MoodPanel")
        mood_frame.setStyleSheet(
            f"#MoodPanel {{ background: {ALMA_BG_ELEVATED};"
            " border: none; border-radius: 12px; }}"
        )
        apply_card_shadow_soft(mood_frame)
        self._mood_section = mood_frame
        mood_layout = QVBoxLayout(mood_frame)
        mood_layout.setContentsMargins(12, 8, 12, 8)
        mood_layout.setSpacing(8)

        mood_title = QLabel("Current Mood")
        mood_title.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK};"
            " background: transparent; border: none;"
        )
        mood_layout.addWidget(mood_title)

        self.sentiment_table = QTableWidget()
        self.sentiment_table.setColumnCount(5)
        self.sentiment_table.setHorizontalHeaderLabels([
            "TRC", "Current Window", "Previous Window", "\u0394 Change", "Trend"
        ])
        # TRC column: stretches to fill available width (names are most important)
        self.sentiment_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        # Data columns: size to content so values are fully readable
        for i in range(1, 4):
            self.sentiment_table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeToContents)
        # Trend column: fixed width (labels like "Getting Worse", "Getting Better")
        self.sentiment_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Fixed)
        self.sentiment_table.horizontalHeader().resizeSection(4, 180)
        self.sentiment_table.verticalHeader().setVisible(False)
        configure_table(self.sentiment_table)
        self.sentiment_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.sentiment_table.verticalHeader().setDefaultSectionSize(32)
        self.sentiment_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.sentiment_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.sentiment_table.setWordWrap(False)
        self.sentiment_table.setTextElideMode(Qt.ElideRight)
        self.sentiment_table.setMouseTracking(True)
        self.sentiment_table.setStyleSheet(
            "QTableWidget { border: none; background: transparent; }"
            " QToolTip { background: #333; color: #fff; border: 1px solid #555;"
            " padding: 4px 8px; font-size: 12px; }"
        )
        mood_layout.addWidget(self.sentiment_table)

        self._mood_page_size = 10
        self._sentiment_pager = PaginationBar(page_size=self._mood_page_size)
        self._sentiment_pager.page_changed.connect(self._on_sentiment_page_changed)
        mood_layout.addWidget(self._sentiment_pager)

        parent_layout.addWidget(self._mood_section)

        # State
        self._sentiment_all_data = {}      # full sentiment data (all TRCs)
        self._selected_trcs = set()        # currently checked TRC codes

    # ═══════════════════════════════════════════
    #  CROSS-TRC CORRELATION PANEL (Deep Dive tab)
    # ═══════════════════════════════════════════

    def _build_correlation_panel(self, parent_layout):
        corr_section = QFrame()
        corr_section.setObjectName("CorrCard")
        corr_section.setStyleSheet(
            f"#CorrCard {{ background: {ALMA_BG_ELEVATED};"
            " border: none; border-radius: 12px; }}"
        )
        apply_card_shadow_soft(corr_section)
        layout = QVBoxLayout(corr_section)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        title = QLabel("Cross-TRC Correlation Signals")
        title.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK};"
            " border: none; background: transparent;"
        )
        layout.addWidget(title)

        subtitle = QLabel("Statistically significant correlations between TRC metrics across time")
        subtitle.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_LIGHT};"
            " border: none; background: transparent;"
        )
        layout.addWidget(subtitle)

        self._correlation_container = QVBoxLayout()
        self._correlation_container.setSpacing(8)
        layout.addLayout(self._correlation_container)

        self._correlation_message = QLabel("Run analysis to detect cross-TRC correlations")
        self._correlation_message.setStyleSheet(f"color: {ALMA_TEXT_LIGHT}; font-size: 12px; border: none;")
        self._correlation_message.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._correlation_message)

        self._corr_pager = PaginationBar(page_size=8)
        self._corr_pager.page_changed.connect(self._on_corr_page_changed)
        layout.addWidget(self._corr_pager)
        self._corr_all = []  # full sorted correlations for pagination

        self._correlation_frame = corr_section
        parent_layout.addWidget(corr_section)

    # ═══════════════════════════════════════════
    #  RISING TERMS PANEL (Deep Dive tab)
    # ═══════════════════════════════════════════

    def _build_terms_panel(self, parent_layout):
        terms_section = QFrame()
        terms_section.setObjectName("TermsCard")
        terms_section.setStyleSheet(
            f"#TermsCard {{ background: {ALMA_BG_ELEVATED};"
            " border: none; border-radius: 12px; }}"
        )
        apply_card_shadow_soft(terms_section)
        layout = QVBoxLayout(terms_section)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        title = QLabel("Rising & Cooling Terms")
        title.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK};"
            " border: none; background: transparent;"
        )
        layout.addWidget(title)

        columns = QHBoxLayout()
        columns.setSpacing(20)

        # Rising terms
        rising_col = QVBoxLayout()
        rising_title = QLabel("\u2191 Rising")
        rising_title.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {ALMA_SUCCESS}; border: none;")
        rising_col.addWidget(rising_title)
        self._rising_container = QVBoxLayout()
        self._rising_container.setSpacing(4)
        rising_col.addLayout(self._rising_container)
        rising_col.addStretch()
        columns.addLayout(rising_col, 1)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet(f"background: {ALMA_BORDER_LIGHT}; max-width: 1px;")
        columns.addWidget(sep)

        # Cooling terms
        cooling_col = QVBoxLayout()
        cooling_title = QLabel("\u2193 Cooling")
        cooling_title.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {ALMA_ERROR}; border: none;")
        cooling_col.addWidget(cooling_title)
        self._cooling_container = QVBoxLayout()
        self._cooling_container.setSpacing(4)
        cooling_col.addLayout(self._cooling_container)
        cooling_col.addStretch()
        columns.addLayout(cooling_col, 1)

        layout.addLayout(columns)

        self._terms_frame = terms_section
        parent_layout.addWidget(terms_section)

    # ═══════════════════════════════════════════
    #  TOPIC CLUSTERS PANEL (Deep Dive tab)
    # ═══════════════════════════════════════════

    def _build_topics_panel(self, parent_layout):
        topics_section = QFrame()
        topics_section.setObjectName("TopicsCard")
        topics_section.setStyleSheet(
            f"#TopicsCard {{ background: {ALMA_BG_ELEVATED};"
            " border: none; border-radius: 12px; }}"
        )
        apply_card_shadow_soft(topics_section)
        layout = QVBoxLayout(topics_section)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        self._topics_title = QLabel("Topic Clusters")
        self._topics_title.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK};"
            " border: none; background: transparent;"
        )
        layout.addWidget(self._topics_title)

        self._clusters_grid = QGridLayout()
        self._clusters_grid.setSpacing(12)
        layout.addLayout(self._clusters_grid)

        self._clusters_message = QLabel("")
        self._clusters_message.setStyleSheet(f"color: {ALMA_TEXT_LIGHT}; font-size: 12px; border: none;")
        self._clusters_message.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._clusters_message)

        # Multi-topic tickets section (NMF only)
        self._multi_topic_label = QLabel("")
        self._multi_topic_label.setStyleSheet(f"color: {ALMA_TEXT_MID}; font-size: 11px; border: none; margin-top: 8px;")
        self._multi_topic_label.setWordWrap(True)
        self._multi_topic_label.hide()
        layout.addWidget(self._multi_topic_label)

        self._clusters_frame = topics_section
        parent_layout.addWidget(topics_section)

        # ── AI Review Panels (shown in DrilldownPanel) ──
        self._smoothing_panel = SmoothingReviewPanel()
        self._smoothing_panel.suggestions_applied.connect(self._on_smoothing_applied)
        self._smoothing_panel.dismissed.connect(self._on_panel_dismissed)

        self._keyword_panel = KeywordReviewPanel()
        self._keyword_panel.keywords_applied.connect(self._on_keywords_applied)
        self._keyword_panel.dismissed.connect(self._on_panel_dismissed)

        # _term_panel created in _setup_tabs (Tab 5)

        # Track AI result counts for badge display
        self._smoothing_count = 0
        self._keyword_count = 0

    # ═══════════════════════════════════════════
    #  AI TOOLS BAR (Deep Dive tab)
    # ═══════════════════════════════════════════

    def _build_ai_tools_bar(self, parent_layout):
        """Compact notification bar with buttons to open AI panels in the DrilldownPanel."""
        self._ai_tools_bar = QFrame()
        self._ai_tools_bar.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED};
                border: none;
                border-left: 3px solid {ALMA_GREEN_DARK};
                border-radius: 8px;
            }}
        """)
        apply_card_shadow_soft(self._ai_tools_bar)
        self._ai_tools_bar.setFixedHeight(44)
        self._ai_tools_bar.setVisible(False)  # Hidden until analysis runs

        bar_layout = QHBoxLayout(self._ai_tools_bar)
        bar_layout.setContentsMargins(14, 0, 10, 0)
        bar_layout.setSpacing(8)

        # Icon + label
        icon_lbl = QLabel("\u2726")
        icon_lbl.setStyleSheet(f"font-size: 14px; color: {ALMA_GREEN_DARK}; border: none; background: transparent;")
        icon_lbl.setFixedWidth(18)
        bar_layout.addWidget(icon_lbl)

        title_lbl = QLabel("AI Tools")
        title_lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none; background: transparent;"
        )
        bar_layout.addWidget(title_lbl)
        bar_layout.addStretch()

        _btn_style = f"""
            QPushButton {{
                background: transparent; color: {ALMA_GREEN_DARK};
                border: none; border-radius: 6px;
                padding: 4px 12px; font-size: 11px; font-weight: 600;
            }}
            QPushButton:hover {{
                background: {ALMA_CREAM}; border-color: {ALMA_GREEN_LIGHT};
            }}
            QPushButton:disabled {{
                color: {ALMA_TEXT_LIGHT}; border-color: {ALMA_BORDER_LIGHT};
            }}
        """

        # Smoothing button (hidden until AI results arrive)
        self._smoothing_btn = QPushButton("Smoothing")
        self._smoothing_btn.setCursor(Qt.PointingHandCursor)
        self._smoothing_btn.setStyleSheet(_btn_style)
        self._smoothing_btn.setVisible(False)
        self._smoothing_btn.clicked.connect(self._open_smoothing_drilldown)
        bar_layout.addWidget(self._smoothing_btn)

        # Keywords button (hidden until AI results arrive)
        self._keywords_btn = QPushButton("Keywords")
        self._keywords_btn.setCursor(Qt.PointingHandCursor)
        self._keywords_btn.setStyleSheet(_btn_style)
        self._keywords_btn.setVisible(False)
        self._keywords_btn.clicked.connect(self._open_keywords_drilldown)
        bar_layout.addWidget(self._keywords_btn)

        # Manage Terms button (always visible once bar shown)
        self._terms_btn = QPushButton("\U0001f4cb Manage Terms")
        self._terms_btn.setCursor(Qt.PointingHandCursor)
        self._terms_btn.setStyleSheet(_btn_style)
        self._terms_btn.clicked.connect(self._open_terms_drilldown)
        bar_layout.addWidget(self._terms_btn)

        parent_layout.addWidget(self._ai_tools_bar)

    # ═══════════════════════════════════════════
    #  TRC FILTER POPULATION
    # ═══════════════════════════════════════════

    def populate_trc_filter(self):
        current = self.trc_combo.currentData()
        self.trc_combo.clear()
        self.trc_combo.addItem("All TRCs", "")
        trcs = self.db.get_trc_codes()
        for trc in trcs:
            self.trc_combo.addItem(f"{trc['code']} \u2014 {trc['label']}", trc["code"])
        if current:
            idx = self.trc_combo.findData(current)
            if idx >= 0:
                self.trc_combo.setCurrentIndex(idx)

    def sync_date_to_data(self):
        """Force date pickers to match the actual data range in the DB."""
        try:
            min_date, max_date = self.db.get_date_range()
        except Exception:
            return
        if not min_date or not max_date:
            return

        data_min = QDate.fromString(str(min_date)[:10], "yyyy-MM-dd")
        data_max = QDate.fromString(str(max_date)[:10], "yyyy-MM-dd")
        if not data_min.isValid() or not data_max.isValid():
            return

        self.date_from.setDate(data_min)
        self.date_to.setDate(data_max)
        # Also sync hypothesis test dates
        if hasattr(self, "_hyp_date_from"):
            self._hyp_date_from.setDate(data_min)
        if hasattr(self, "_hyp_date_to"):
            self._hyp_date_to.setDate(data_max)

    def _auto_fit_dates(self):
        """Adjust date pickers to match actual data range if defaults miss all data."""
        try:
            min_date, max_date = self.db.get_date_range()
        except Exception:
            return
        if not min_date or not max_date:
            return

        data_min = QDate.fromString(str(min_date)[:10], "yyyy-MM-dd")
        data_max = QDate.fromString(str(max_date)[:10], "yyyy-MM-dd")
        if not data_min.isValid() or not data_max.isValid():
            return

        ui_from = self.date_from.date()
        ui_to = self.date_to.date()

        # If the UI date range doesn't overlap with data range at all, auto-adjust
        if ui_from > data_max or ui_to < data_min:
            self.date_from.setDate(data_min)
            self.date_to.setDate(data_max)

    def auto_refresh(self):
        """Sync dates to data range, then run analysis."""
        self.sync_date_to_data()
        self._on_analyze()

    # ═══════════════════════════════════════════
    #  DYNAMIC CHART HEIGHT
    # ═══════════════════════════════════════════

    def resizeEvent(self, event):
        """Let the chart size itself within min/max constraints."""
        super().resizeEvent(event)

    # ═══════════════════════════════════════════
    #  ANALYZE
    # ═══════════════════════════════════════════

    def _on_analyze(self):
        import time
        self._scan_start_time = time.time()

        self.populate_trc_filter()
        self._auto_fit_dates()

        # Quick data-existence check before launching worker
        count = self.db.get_ticket_count()
        if count == 0:
            self._empty_state.setVisible(True)
            self._skeleton.setVisible(False)
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, "No Data",
                "No conversations found in the database.\n\n"
                "Use the Conversations page to pull data from Lightdash or import a CSV first."
            )
            return

        self._show_loading()  # T12: show skeleton while analyzing

        main_win = self.window()
        if hasattr(main_win, '_job_queue'):
            job = main_win._make_trending_job()
            main_win._job_queue.submit(job)
        else:
            self._run_directly()

    def _run_directly(self):
        """Run trending analysis without the job queue (fallback)."""
        import time
        self._scan_start_time = time.time()

        date_start = self.date_from.date().toString("yyyy-MM-dd")
        date_end = self.date_to.date().toString("yyyy-MM-dd") + "T23:59:59"
        trc_filter = self.trc_combo.currentData() or None
        window_size = self.window_combo.currentText()
        topic_method = self.method_combo.currentData()

        source_id = self._source_selector.selected_source_id() if hasattr(self, '_source_selector') else None
        self._worker = TrendingWorker(
            self.db.db_path, date_start, date_end, trc_filter, window_size, topic_method,
            source_id=source_id,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_results)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_progress(self, msg):
        """Progress callback — forwarded by job queue to overlay."""
        pass  # Handled by unified overlay now

    def _on_results(self, result):
        self._show_content()  # T12: swap skeleton -> real content
        self._empty_state.setVisible(False)
        self._display_results(result)

        # Update overview KPI cards
        self._update_kpi_cards(result)

        # Show AI Tools bar
        self._ai_tools_bar.setVisible(True)

        # Run AI enhancements in background thread (non-blocking)
        self._last_analysis_result = result
        self._run_ai_enhancements(result)

        # Save report
        import time
        duration_ms = int((time.time() - self._scan_start_time) * 1000) if self._scan_start_time else 0

        rising_count = len(result.get("terms", {}).get("rising", []))
        topic_data = result.get("topics", {})
        topic_count = len(topic_data.get("topics", topic_data.get("clusters", [])))

        summary = {"rising_count": rising_count, "topic_count": topic_count}
        params = {
            "date_range": f"{self.date_from.date().toString('yyyy-MM-dd')} to {self.date_to.date().toString('yyyy-MM-dd')}",
            "trc_filter": self.trc_combo.currentData() or "All",
            "window": self.window_combo.currentText(),
            "method": self.method_combo.currentData(),
        }

        try:
            self.db.save_report(
                page="trending_topics",
                parameters=params,
                summary=summary,
                full_results=json.dumps(result, default=str),
                ticket_count=result.get("ticket_count", 0),
                duration_ms=duration_ms,
            )
        except Exception:
            pass

        self._reports_tab.refresh()

    def _update_kpi_cards(self, result):
        """Populate the KPI card row from analysis results."""
        # Sentiment Avg
        sentiment = result.get("sentiment", {})
        if sentiment:
            all_vals = []
            for trc, series in sentiment.items():
                for _, val in series:
                    all_vals.append(val)
            if all_vals:
                avg = sum(all_vals) / len(all_vals)
                self._kpi_sentiment.set_value(f"{avg:+.3f}")
                if avg > 0.05:
                    self._kpi_sentiment.set_delta("Positive", "up")
                elif avg < -0.05:
                    self._kpi_sentiment.set_delta("Negative", "down")
                else:
                    self._kpi_sentiment.set_delta("Neutral", "neutral")
            else:
                self._kpi_sentiment.set_value("\u2014")
        else:
            self._kpi_sentiment.set_value("\u2014")

        # TRC Count — number of unique TRC codes
        trc_count = len(sentiment) if sentiment else 0
        self._kpi_trc_count.set_value(f"{trc_count}" if trc_count else "\u2014")
        self._kpi_trc_count.set_subtitle("unique TRC codes")

        # Sentiment Range — min → max across all data points
        if all_vals:
            low, high = min(all_vals), max(all_vals)
            self._kpi_range.set_value(f"{low:+.2f} → {high:+.2f}")
            span = high - low
            self._kpi_range.set_subtitle(f"span {span:.2f}")
        else:
            self._kpi_range.set_value("\u2014")

        # Analysis Period
        date_from_str = self.date_from.date().toString("MMM d")
        date_to_str = self.date_to.date().toString("MMM d, yyyy")
        self._kpi_period.set_value(f"{date_from_str} - {date_to_str}")
        window = self.window_combo.currentText()
        self._kpi_period.set_subtitle(f"{window} windows")

    def _display_results(self, result):
        """Populate all panels from analysis results."""
        self._populate_sentiment(result.get("sentiment", {}))
        self._populate_correlations(result.get("correlations", {}))
        self._populate_terms(result.get("terms", {}))
        self._populate_topics(result.get("topics", {}))

    def _on_error(self, error_text):
        # Mark job as failed so queue advances immediately
        main_win = self.window()
        if hasattr(main_win, '_job_queue'):
            main_win._job_queue.mark_job_failed("trending_analysis", error_text)
        # Defer error dialog so it doesn't block the job queue
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._show_error(error_text))

    def _show_error(self, error_text):
        self._show_content()  # T12: restore real content on error
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(self, "Analysis Error", f"Trending analysis failed:\n\n{error_text}")

    # ═══════════════════════════════════════════
    #  POPULATE: SENTIMENT
    # ═══════════════════════════════════════════

    def _populate_sentiment(self, data):
        """data: {trc: [(window_label, avg_compound), ...]}"""
        if not data:
            self.sentiment_chart.set_data({})
            self.sentiment_table.setRowCount(0)
            self._sentiment_pager.set_total(0)
            self._sentiment_legend.update_legend([])
            return

        # Store full data (all TRCs)
        self._sentiment_all_data = data

        # Feed data into the Lightdash-style TRC dropdown
        self._trc_dropdown.set_trc_data(data)

        # Default: auto-select top 10 TRCs by volume (most data points)
        self._trc_dropdown.select_top_n(10)

    # ── TRC selection handler ─────────────────────────────

    def _on_trc_selection_changed(self, selected_set):
        """Called by TRCDropdownSelector whenever selection changes."""
        self._selected_trcs = selected_set
        self._refresh_sentiment_chart()

    def _refresh_sentiment_chart(self):
        """Update chart + legend + table with only the selected TRCs."""
        # Filter data to selected TRCs
        filtered = {
            trc: pts for trc, pts in self._sentiment_all_data.items()
            if trc in self._selected_trcs
        }

        self.sentiment_chart.set_data(
            filtered,
            show_zero_line=True, show_bg_tint=True,
        )

        # Update standalone legend section
        self._sentiment_legend.update_legend(list(filtered.keys()))

        # Table shows only selected TRCs
        self._sentiment_pager.set_total(len(filtered))
        self._show_sentiment_page(0)

    def _on_sentiment_page_changed(self, page: int):
        """Handle sentiment table pagination."""
        self._show_sentiment_page(page)

    def _show_sentiment_page(self, page: int):
        """Render one page of sentiment table rows."""
        layman = is_layman_mode()

        # Update table headers for layman mode
        if layman:
            self.sentiment_table.setHorizontalHeaderLabels([
                "TRC", "Current Mood", "Previous Mood", "Change", "Trend"
            ])
        else:
            self.sentiment_table.setHorizontalHeaderLabels([
                "TRC", "Current Window", "Previous Window", "\u0394 Change", "Trend"
            ])

        # Show only selected TRCs in the table
        all_items = [
            (trc, pts) for trc, pts in self._sentiment_all_data.items()
            if trc in self._selected_trcs
        ]
        ps = self._sentiment_pager.page_size
        start = page * ps
        end = min(start + ps, len(all_items))
        page_items = all_items[start:end]

        self.sentiment_table.setRowCount(len(page_items))
        for i, (trc, series) in enumerate(page_items):
            trc_item = QTableWidgetItem(trc)
            trc_item.setToolTip(trc)  # hover-to-discover full name
            self.sentiment_table.setItem(i, 0, trc_item)

            current = series[-1][1] if series else 0
            previous = series[-2][1] if len(series) >= 2 else 0
            delta = current - previous

            self.sentiment_table.setItem(i, 1, QTableWidgetItem(format_sentiment(current, layman)))
            self.sentiment_table.setItem(i, 2, QTableWidgetItem(format_sentiment(previous, layman)))

            if layman:
                delta_text = f"{'Worse' if delta < 0 else 'Better'} ({abs(delta)*100:.0f}%)"
            else:
                delta_text = f"{delta:+.2f} {'\u2193' if delta < 0 else '\u2191'}"
            delta_item = QTableWidgetItem(delta_text)
            delta_item.setForeground(QColor(ALMA_ERROR if delta < 0 else ALMA_SUCCESS))
            self.sentiment_table.setItem(i, 3, delta_item)

            if delta < -0.05:
                trend = translate_label("Declining", layman)
                trend_color = ALMA_ERROR
            elif delta > 0.05:
                trend = translate_label("Improving", layman)
                trend_color = ALMA_SUCCESS
            else:
                trend = translate_label("Stable", layman)
                trend_color = ALMA_TEXT_MID

            trend_item = QTableWidgetItem(trend)
            trend_item.setForeground(QColor(trend_color))
            trend_item.setFont(QFont("Segoe UI", 11, QFont.Bold))
            self.sentiment_table.setItem(i, 4, trend_item)

        # Resize table to fit content — standalone, not dependent on layout
        self._resize_mood_table()
        self._mood_section.setVisible(True)

    def _resize_mood_table(self):
        """Set table height to fit one page of rows (pagination-driven, fixed size)."""
        row_h = self.sentiment_table.verticalHeader().defaultSectionSize()
        header_h = self.sentiment_table.horizontalHeader().height()
        # Always sized for page_size rows so the card never changes height
        visible_rows = min(self.sentiment_table.rowCount(), self._mood_page_size)
        needed = header_h + (visible_rows * row_h) + 4
        self.sentiment_table.setFixedHeight(max(needed, 60))

    # ═══════════════════════════════════════════
    #  POPULATE: TERMS
    # ═══════════════════════════════════════════

    def _populate_terms(self, data):
        """data: {rising: [...], cooling: [...]}"""
        # Clear existing
        self._clear_layout(self._rising_container)
        self._clear_layout(self._cooling_container)

        rising = data.get("rising", [])
        cooling = data.get("cooling", [])

        if not rising and not cooling:
            lbl = QLabel("Not enough data for term analysis")
            lbl.setStyleSheet(f"color: {ALMA_TEXT_LIGHT}; font-size: 12px; border: none;")
            self._rising_container.addWidget(lbl)
            return

        for term_info in rising:
            row = self._make_term_row(term_info, rising=True)
            self._rising_container.addWidget(row)

        for term_info in cooling:
            row = self._make_term_row(term_info, rising=False)
            self._cooling_container.addWidget(row)

    def _make_term_row(self, term_info, rising=True):
        """Create a clickable term row with sparkline, badges, and context menu."""
        frame = QFrame()
        frame.setCursor(Qt.PointingHandCursor)
        color = ALMA_SUCCESS if rising else ALMA_ERROR
        frame.setStyleSheet(f"""
            QFrame {{
                background: transparent; border: none; border-radius: 6px;
                padding: 4px 8px;
            }}
            QFrame:hover {{ background: {ALMA_CREAM}; }}
        """)

        layout = QHBoxLayout(frame)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(8)

        # Visual indicator badges (Pass 1.5)
        user_action = term_info.get("user_action")
        if user_action == "promote" or user_action == "important":
            badge = QLabel("\u2605")
            badge.setStyleSheet(f"color: {ALMA_SUCCESS}; font-size: 12px; border: none;")
            badge.setToolTip("Marked as important")
            layout.addWidget(badge)
        elif user_action == "demote" or user_action == "noise":
            badge = QLabel("\u2715")
            badge.setStyleSheet(f"color: {ALMA_ERROR}; font-size: 12px; border: none;")
            badge.setToolTip("Marked as noise")
            layout.addWidget(badge)
        elif user_action == "watchlist":
            badge = QLabel("\U0001f441")
            badge.setStyleSheet("font-size: 11px; border: none;")
            badge.setToolTip("On watchlist")
            layout.addWidget(badge)

        # Temporal promotion badge
        if term_info.get("temporal_promoted"):
            fire = QLabel("\U0001f525")
            fire.setStyleSheet("font-size: 11px; border: none;")
            peak = term_info.get("peak_recency", "current")
            fire.setToolTip(f"Temporally promoted (peak: {peak})")
            layout.addWidget(fire)

        # Term text
        term_lbl = QLabel(term_info["term"])
        term_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {ALMA_TEXT_DARK}; border: none;")
        layout.addWidget(term_lbl, 1)

        # Velocity
        vel = term_info["velocity"]
        layman = is_layman_mode()
        vel_text = format_tfidf(abs(vel), layman) if layman else f"{vel:+.4f}"
        vel_lbl = QLabel(vel_text)
        vel_lbl.setStyleSheet(f"font-size: 10px; color: {color}; font-weight: 600; border: none;")
        layout.addWidget(vel_lbl)

        # Sparkline
        sparkline = SparklineWidget()
        sparkline.set_data(term_info.get("sparkline_data", []), color=color)
        layout.addWidget(sparkline)

        # Click handler (left-click -> drill-down)
        term = term_info["term"]
        frame.mousePressEvent = lambda e, t=term, w=frame: (
            self._drilldown_term(t) if e.button() == Qt.LeftButton else None
        )

        # Right-click context menu (Pass 1.5)
        frame.setContextMenuPolicy(Qt.CustomContextMenu)
        frame.customContextMenuRequested.connect(
            lambda pos, t=term, w=frame: self._show_term_context_menu(pos, t, w)
        )

        return frame

    def _drilldown_term(self, term):
        """Show matching tickets for a clicked term in the side panel."""
        if not self._drilldown:
            return
        from src.data.trending_engine import get_tickets_for_term

        date_start = self.date_from.date().toString("yyyy-MM-dd")
        date_end = self.date_to.date().toString("yyyy-MM-dd") + "T23:59:59"
        trc_filter = self.trc_combo.currentData() or None

        tickets = get_tickets_for_term(self.db, term, date_start, date_end, trc_filter, limit=50)
        total = len(tickets)

        self._drilldown.show_tickets(
            f'Term: "{term}"',
            f"Showing {min(total, 50)} of {total} matches" if total > 50 else f"{total} matches",
            tickets,
        )

    # ═══════════════════════════════════════════
    #  POPULATE: CORRELATIONS
    # ═══════════════════════════════════════════

    def _populate_correlations(self, data):
        """data: {correlations: [...], per_trc_series: {...}}"""
        self._clear_layout(self._correlation_container)

        correlations = data.get("correlations", [])

        if not correlations:
            self._correlation_message.setText("No significant cross-TRC correlations detected")
            self._correlation_message.show()
            self._corr_pager.set_total(0)
            return

        self._correlation_message.hide()
        self._corr_all = correlations
        self._corr_pager.set_total(len(correlations))
        self._show_corr_page(0)

    def _on_corr_page_changed(self, page: int):
        """Handle correlation cards pagination."""
        self._show_corr_page(page)

    def _show_corr_page(self, page: int):
        """Render one page of correlation cards."""
        self._clear_layout(self._correlation_container)

        ps = self._corr_pager.page_size
        start = page * ps
        end = min(start + ps, len(self._corr_all))
        page_corrs = self._corr_all[start:end]

        for corr in page_corrs:
            card = self._make_correlation_card(corr)
            self._correlation_container.addWidget(card)

    def _make_correlation_card(self, corr):
        """Create a correlation card with dual sparkline."""
        card = QFrame()
        card.setCursor(Qt.PointingHandCursor)

        strength = corr.get("strength", "moderate")
        border_color = ALMA_ERROR if strength == "strong" else ALMA_WARNING

        card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED}; border: none;
                border-radius: 10px; padding: 8px;
            }}
            QFrame:hover {{ background: {ALMA_CREAM}; }}
        """)
        apply_card_shadow_soft(card)

        outer = QHBoxLayout(card)
        outer.setContentsMargins(12, 8, 12, 8)
        outer.setSpacing(12)

        # Text content (left)
        text_col = QVBoxLayout()
        text_col.setSpacing(4)

        # TRC pair with lead-lag info
        lead_lag = corr.get("lead_lag")
        if lead_lag:
            pair_text = (
                f"{lead_lag['leader']} \u2192 ({lead_lag['lag_label']}) \u2192 {lead_lag['follower']}"
            )
        else:
            pair_text = f"{corr['trc_a']}  \u2194  {corr['trc_b']}"

        pair_lbl = QLabel(pair_text)
        pair_lbl.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        text_col.addWidget(pair_lbl)

        # Metrics and strength
        r_val = corr["r"]
        strength_color = ALMA_ERROR if strength == "strong" else ALMA_WARNING
        metrics_text = (
            f"{corr['metric_a']} vs. {corr['metric_b']}  \u2022  "
            f"r = {r_val:+.2f}  \u2022  p = {corr['p_value']:.3f}"
        )
        metrics_lbl = QLabel(metrics_text)
        metrics_lbl.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_MID}; border: none;")
        text_col.addWidget(metrics_lbl)

        # Strength badge
        badge_row = QHBoxLayout()
        badge_row.setSpacing(8)

        badge_bg = "rgba(196,30,30,0.10)" if strength == "strong" else "rgba(180,83,9,0.10)"
        badge = QLabel(f"{'Strong' if strength == 'strong' else 'Moderate'}")
        badge.setStyleSheet(
            f"background: {badge_bg}; color: {strength_color}; "
            f"border-radius: 8px; padding: 2px 8px; font-size: 10px; font-weight: 600; border: none;"
        )
        badge_row.addWidget(badge)

        # Lead-lag badge
        if lead_lag:
            lag_badge = QLabel(f"\u23f1 {lead_lag['interpretation']}")
            lag_badge.setStyleSheet(f"font-size: 10px; color: {ALMA_INFO}; border: none;")
            lag_badge.setWordWrap(True)
            text_col.addWidget(lag_badge)

        badge_row.addStretch()
        text_col.addLayout(badge_row)

        # Interpretation
        interp = QLabel(corr.get("interpretation", ""))
        interp.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; border: none;")
        interp.setWordWrap(True)
        text_col.addWidget(interp)

        outer.addLayout(text_col, 1)

        # Dual sparkline (right)
        sparkline = DualSparklineWidget()
        sparkline.set_data(
            corr.get("series_a", []), corr.get("series_b", []),
            label_a=corr.get("metric_a", ""), label_b=corr.get("metric_b", ""),
        )
        outer.addWidget(sparkline)

        # Click handler: drill-down with tickets from both TRCs
        trc_a = corr["trc_a"]
        trc_b = corr["trc_b"]
        card.mousePressEvent = lambda e, a=trc_a, b=trc_b: self._drilldown_correlation(a, b)

        return card

    def _drilldown_correlation(self, trc_a, trc_b):
        """Show tickets from both correlated TRC codes in the side panel."""
        if not self._drilldown:
            return
        from src.data.trending_engine import get_tickets_for_term

        date_start = self.date_from.date().toString("yyyy-MM-dd")
        date_end = self.date_to.date().toString("yyyy-MM-dd") + "T23:59:59"

        tickets_a = get_tickets_for_term(self.db, "", date_start, date_end, trc_a, limit=25)
        tickets_b = get_tickets_for_term(self.db, "", date_start, date_end, trc_b, limit=25)

        # Combine and interleave
        tickets = []
        for ta, tb in zip(tickets_a, tickets_b):
            tickets.append(ta)
            tickets.append(tb)
        longer = tickets_a if len(tickets_a) > len(tickets_b) else tickets_b
        shorter_len = min(len(tickets_a), len(tickets_b))
        tickets.extend(longer[shorter_len:])

        total = len(tickets)
        self._drilldown.show_tickets(
            f"{trc_a} \u2194 {trc_b}",
            f"{total} tickets",
            tickets[:50],
        )

    # ═══════════════════════════════════════════
    #  POPULATE: TOPICS / CLUSTERS
    # ═══════════════════════════════════════════

    def _populate_topics(self, data):
        """Handle both NMF topics and K-Means clusters."""
        # Clear grid
        while self._clusters_grid.count():
            child = self._clusters_grid.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        method = data.get("method", "kmeans")

        if method == "nmf":
            self._topics_title.setText("NMF Topic Model")
            topics = data.get("topics", [])
            self._current_clusters = topics  # Reuse for drill-down

            if not topics:
                self._clusters_message.setText(
                    data.get("message", "No topics detected")
                )
                self._clusters_message.show()
                self._multi_topic_label.hide()
                return

            self._clusters_message.hide()

            cols = 3 if len(topics) > 4 else 2
            for i, topic in enumerate(topics):
                card = self._make_topic_card(topic, i)
                row = i // cols
                col = i % cols
                self._clusters_grid.addWidget(card, row, col)

            # Multi-topic tickets info
            multi = data.get("multi_topic_tickets", [])
            if multi:
                self._multi_topic_label.setText(
                    f"\U0001f517 {len(multi)} tickets span multiple topics"
                )
                self._multi_topic_label.show()
            else:
                self._multi_topic_label.hide()
        else:
            # K-Means (original behavior)
            self._topics_title.setText("K-Means Topic Clusters")
            clusters = data.get("clusters", [])
            message = data.get("message", "")
            self._current_clusters = clusters
            self._multi_topic_label.hide()

            if not clusters:
                self._clusters_message.setText(message or "No clusters detected")
                self._clusters_message.show()
                return

            self._clusters_message.hide()

            cols = 3 if len(clusters) > 4 else 2
            for i, cluster in enumerate(clusters):
                card = self._make_topic_card(cluster, i)
                row = i // cols
                col = i % cols
                self._clusters_grid.addWidget(card, row, col)

    def _make_topic_card(self, topic, index):
        """Create a clickable topic/cluster card with optional trend badge."""
        card = QFrame()
        card.setCursor(Qt.PointingHandCursor)

        # Border color by sentiment
        sentiment = topic.get("avg_sentiment", 0)
        if sentiment > 0.1:
            border_color = ALMA_SUCCESS
        elif sentiment < -0.1:
            border_color = ALMA_ERROR
        else:
            border_color = ALMA_WARNING

        card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED}; border: 2px solid {border_color};
                border-radius: 10px; padding: 12px;
            }}
            QFrame:hover {{ background: {ALMA_CREAM}; }}
        """)
        apply_card_shadow_soft(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # Label (top terms)
        label = QLabel(topic["label"])
        label.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        label.setWordWrap(True)
        layout.addWidget(label)

        # Stats row
        stats_parts = [f"{topic['count']} tickets"]
        if topic.get("avg_csat") is not None:
            stats_parts.append(f"CSAT {topic['avg_csat']:.1f}")
        stats_parts.append(f"Sentiment {topic['avg_sentiment']:+.2f}")

        stats = QLabel("  \u2022  ".join(stats_parts))
        stats.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; border: none;")
        stats.setWordWrap(True)
        layout.addWidget(stats)

        # Trend badge (NMF only)
        trend_dir = topic.get("trend_direction")
        if trend_dir:
            if trend_dir == "rising":
                badge_text = "\u2191 Rising"
                badge_bg = "rgba(22,118,58,0.12)"
                badge_color = ALMA_SUCCESS
            elif trend_dir == "declining":
                badge_text = "\u2193 Declining"
                badge_bg = "rgba(196,30,30,0.12)"
                badge_color = ALMA_ERROR
            else:
                badge_text = "\u2192 Stable"
                badge_bg = "rgba(74,74,74,0.12)"
                badge_color = ALMA_TEXT_MID

            badge = QLabel(badge_text)
            badge.setStyleSheet(
                f"background: {badge_bg}; color: {badge_color}; "
                f"border-radius: 10px; padding: 3px 10px; "
                f"font-size: 10px; font-weight: 600; border: none;"
            )
            badge.setMaximumWidth(100)
            layout.addWidget(badge)

        # Click handler
        card.mousePressEvent = lambda e, idx=index: self._drilldown_cluster(idx)

        return card

    def _drilldown_cluster(self, cluster_index):
        """Show tickets from a clicked cluster/topic in the side panel."""
        if not self._drilldown:
            return
        if cluster_index >= len(self._current_clusters):
            return

        cluster = self._current_clusters[cluster_index]
        ticket_ids = cluster.get("ticket_ids", [])

        from src.data.trending_engine import get_tickets_by_ids
        tickets = get_tickets_by_ids(self.db, ticket_ids, limit=50)
        total = len(ticket_ids)

        label = cluster["label"][:50]
        self._drilldown.show_tickets(
            f'Topic: "{label}"',
            f"Showing {min(total, 50)} of {total} tickets" if total > 50 else f"{total} tickets",
            tickets,
        )

    # ═══════════════════════════════════════════
    #  TERM CONTEXT MENU (Pass 1.5)
    # ═══════════════════════════════════════════

    def _show_term_context_menu(self, pos, term, widget):
        """Right-click context menu on a term row."""
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: {ALMA_WHITE}; border: none;
                border-radius: 6px; padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 20px; font-size: 12px;
            }}
            QMenu::item:selected {{
                background: {ALMA_CREAM};
            }}
        """)

        menu.addAction("\u2b50 Mark as important", lambda: self._record_feedback(term, "important"))
        menu.addAction("\u2713 Relevant", lambda: self._record_feedback(term, "relevant"))
        menu.addAction("\u2717 Mark as noise", lambda: self._record_feedback(term, "noise"))
        menu.addSeparator()
        menu.addAction("\U0001f517 Merge with...", lambda: self._merge_term(term))
        menu.addAction("\U0001f441 Add to watchlist", lambda: self._record_feedback(term, "watchlist"))

        menu.exec(widget.mapToGlobal(pos))

    def _record_feedback(self, term, feedback_type):
        """Write term feedback to the database."""
        try:
            self.db.log_tfidf_feedback(term, feedback_type)

            # Also create/update user_term for watchlist
            if feedback_type == "watchlist":
                canonical = term.lower().replace(" ", "_")
                self.db.upsert_user_term(term, canonical, "watchlist", weight_modifier=1.0)

            self.status_msg = f"Recorded: {term} \u2192 {feedback_type}"
        except Exception:
            pass

    def _merge_term(self, term):
        """Prompt user to merge a term with another."""
        target, ok = QInputDialog.getText(
            self, "Merge Term",
            f"Merge \"{term}\" with which term?"
        )
        if ok and target.strip():
            try:
                self.db.log_tfidf_feedback(term, "merge", merge_target=target.strip())
            except Exception:
                pass

    def _open_term_manager(self):
        """Switch to the Manage Terms tab."""
        # Tab 5 (0-indexed: 4)
        self.tab_widget.setCurrentIndex(4)
        self._term_panel.refresh()

    def _on_terms_changed(self):
        """Callback when terms are modified in the Term Manager."""
        # Could re-run analysis automatically; for now just note the change
        pass

    # ═══════════════════════════════════════════
    #  AI ENHANCEMENTS (Smoothing + Keywords)
    # ═══════════════════════════════════════════

    def set_scan_blocking(self, blocked: bool):
        """Block AI enhancements and hypothesis Gemini calls during scans."""
        self._scan_blocked = blocked

    def _run_ai_enhancements(self, result):
        """Launch AI enhancements in a background thread (non-blocking).

        Reads settings on the main thread (fast), then spawns
        AIEnhancementWorker for the actual Gemini calls.
        """
        # Block during active NLP scan (Gemini contention)
        if getattr(self, '_scan_blocked', False):
            self._smoothing_btn.setVisible(False)
            self._keywords_btn.setVisible(False)
            return

        try:
            ai_cfg = get_section("ai_enhancements", {})
            smoothing_enabled = ai_cfg.get("smoothing", False)
            keywords_enabled = ai_cfg.get("keywords", False)

            if not (smoothing_enabled or keywords_enabled):
                self._smoothing_btn.setVisible(False)
                self._keywords_btn.setVisible(False)
                return

            # Quick availability check (fast -- no CLI call)
            try:
                from src.gemini.client_factory import is_provider_available_for_task
                if not is_provider_available_for_task("voc_analysis"):
                    return
            except Exception:
                return

            # Hide buttons until results arrive
            self._smoothing_btn.setVisible(False)
            self._keywords_btn.setVisible(False)

            # Stop any previous enhancement worker
            if hasattr(self, "_ai_worker") and self._ai_worker is not None:
                if self._ai_worker.isRunning():
                    self._ai_worker.quit()
                    self._ai_worker.wait(2000)

            # Launch background worker
            self._ai_worker = AIEnhancementWorker(
                result, smoothing_enabled, keywords_enabled,
            )
            self._ai_worker.smoothing_ready.connect(self._on_ai_smoothing_ready)
            self._ai_worker.keywords_ready.connect(self._on_ai_keywords_ready)
            self._ai_worker.error.connect(self._on_ai_enhancement_error)
            self._ai_worker.finished_all.connect(self._on_ai_enhancement_done)
            self._ai_worker.start()

        except Exception:
            self._smoothing_btn.setVisible(False)
            self._keywords_btn.setVisible(False)

    # ── AI Enhancement signal handlers ────────────────────────────

    def _on_ai_smoothing_ready(self, suggestions):
        """Apply AI smoothing suggestions from background worker."""
        if suggestions:
            self._smoothing_count = len(suggestions)
            self._smoothing_panel.set_suggestions(suggestions)
            self._smoothing_btn.setText(f"Smoothing ({self._smoothing_count})")
            self._smoothing_btn.setVisible(True)
        else:
            self._smoothing_btn.setVisible(False)

    def _on_ai_keywords_ready(self, kw_result):
        """Apply AI keyword suggestions from background worker."""
        suppress = kw_result.get("suppress", [])
        add_to_map = kw_result.get("add_to_map", [])
        if suppress or add_to_map:
            self._keyword_count = len(suppress) + len(add_to_map)
            self._keyword_panel.set_suggestions(
                suppress=suppress, add_to_map=add_to_map,
            )
            self._keywords_btn.setText(f"Keywords ({self._keyword_count})")
            self._keywords_btn.setVisible(True)
        else:
            self._keywords_btn.setVisible(False)

    def _on_ai_enhancement_error(self, error_msg):
        """AI enhancement failed -- just hide buttons silently."""
        self._smoothing_btn.setVisible(False)
        self._keywords_btn.setVisible(False)

    def _on_ai_enhancement_done(self):
        """Cleanup after AI enhancement worker finishes."""
        self._ai_worker = None

    def _on_smoothing_applied(self, accepted):
        """Apply AI smoothing suggestions to the displayed topics."""
        if not accepted or not hasattr(self, "_last_analysis_result"):
            return

        result = self._last_analysis_result
        topics = result.get("topics", {})
        topic_list = topics.get("topics", topics.get("clusters", []))

        for idx, suggestion in accepted.items():
            idx = int(idx)
            if idx < len(topic_list):
                ai_label = suggestion.get("ai_label", "")
                if ai_label:
                    topic_list[idx]["label"] = ai_label

        # Re-display with updated labels
        self._populate_topics(result.get("topics", {}))

        # Close drilldown after apply
        if self._drilldown:
            self._drilldown.close_panel()

    def _on_keywords_applied(self, changes):
        """Apply AI keyword changes: suppress terms and add to concept map."""
        suppress_list = changes.get("suppress", [])
        add_list = changes.get("add", [])

        for item in suppress_list:
            term = item.get("term", "")
            if term:
                try:
                    self.db.upsert_user_term(
                        term, term.replace(" ", "_"), "demote",
                        weight_modifier=0.0,
                    )
                except Exception:
                    pass

        for item in add_list:
            term = item.get("term", "")
            group = item.get("concept_group", "")
            if term and group:
                try:
                    self.db.upsert_user_term(
                        term, group, "promote",
                        weight_modifier=1.5,
                    )
                except Exception:
                    pass

        # Close drilldown after apply
        if self._drilldown:
            self._drilldown.close_panel()

    # ═══════════════════════════════════════════
    #  DRILLDOWN OPENERS (AI Tools bar)
    # ═══════════════════════════════════════════

    def _open_smoothing_drilldown(self):
        """Open the DrilldownPanel with AI smoothing suggestions."""
        if not self._drilldown:
            return
        self._drilldown.show_widget(
            "AI Smoothing Suggestions",
            f"{self._smoothing_count} suggestion{'s' if self._smoothing_count != 1 else ''}",
            self._smoothing_panel,
        )

    def _open_keywords_drilldown(self):
        """Open the DrilldownPanel with AI keyword suggestions."""
        if not self._drilldown:
            return
        self._drilldown.show_widget(
            "AI Keyword Suggestions",
            f"{self._keyword_count} suggestion{'s' if self._keyword_count != 1 else ''}",
            self._keyword_panel,
        )

    def _open_terms_drilldown(self):
        """Switch to the Manage Terms tab (replaces drilldown approach)."""
        self._open_term_manager()

    def _on_panel_dismissed(self):
        """Callback when an AI panel emits 'dismissed' -- close the drawer."""
        if self._drilldown:
            self._drilldown.close_panel()

    # ═══════════════════════════════════════════
    #  REPORT HISTORY (Reports tab + drilldown)
    # ═══════════════════════════════════════════

    def _open_report_history(self):
        """Open the DrilldownPanel with the full report history list."""
        if not hasattr(self, "_drilldown") or not self._drilldown:
            return
        reports = self._reports_tab._history.get_reports_for_drilldown()
        count = len(reports)
        self._drilldown.show_reports(
            "Trending Topics Reports",
            f"{count} report{'s' if count != 1 else ''}",
            reports,
            detail_callback=self._render_report_detail_html,
            load_callback=self._load_past_report,
        )

    def _render_report_detail_html(self, report_id):
        """Render a trending topics report as HTML for the DrilldownPanel."""
        report = self.db.get_full_report(report_id)
        if not report:
            return "<p>Report not found.</p>"

        raw = report.get("full_results", "")
        try:
            result = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return "<p>Could not parse report data.</p>"

        html = ['<div style="font-family: Segoe UI, sans-serif; font-size: 13px;">']

        # ── Sentiment ──
        sentiment = result.get("sentiment", {})
        if sentiment:
            avg = sentiment.get("avg_polarity", 0)
            trend = sentiment.get("trend_direction", "")
            html.append(f"<h3 style='margin: 12px 0 6px;'>Sentiment</h3>")
            html.append(f"<p>Average polarity: <b>{avg:+.3f}</b></p>")
            if trend:
                html.append(f"<p>Trend: {trend}</p>")

        # ── Rising terms ──
        terms = result.get("terms", {})
        rising = terms.get("rising", [])
        if rising:
            html.append(f"<h3 style='margin: 12px 0 6px;'>Rising Terms ({len(rising)})</h3>")
            html.append("<table cellpadding='3' style='border-collapse: collapse; width: 100%;'>")
            html.append("<tr style='border-bottom: 1px solid #E8E5DE;'><th align='left'>Term</th><th align='right'>Velocity</th></tr>")
            for t in rising[:20]:
                term = t.get("term", "")
                vel = t.get("velocity", 0)
                html.append(f"<tr style='border-bottom: 1px solid #F0F0F0;'><td>{term}</td><td align='right'>{vel:+.4f}</td></tr>")
            html.append("</table>")

        # ── Cooling terms ──
        cooling = terms.get("cooling", [])
        if cooling:
            html.append(f"<h3 style='margin: 12px 0 6px;'>Cooling Terms ({len(cooling)})</h3>")
            html.append("<table cellpadding='3' style='border-collapse: collapse; width: 100%;'>")
            for t in cooling[:10]:
                term = t.get("term", "")
                vel = t.get("velocity", 0)
                html.append(f"<tr style='border-bottom: 1px solid #F0F0F0;'><td>{term}</td><td align='right'>{vel:+.4f}</td></tr>")
            html.append("</table>")

        # ── Topics ──
        topics = result.get("topics", {})
        topic_list = topics.get("topics", topics.get("clusters", []))
        if topic_list:
            html.append(f"<h3 style='margin: 12px 0 6px;'>Topics ({len(topic_list)})</h3>")
            for i, topic in enumerate(topic_list):
                label = topic.get("label", f"Topic {i+1}")
                count_t = topic.get("count", 0)
                sent = topic.get("avg_sentiment", 0)
                html.append(
                    f"<p style='margin: 4px 0;'>"
                    f"<b>{label}</b> &mdash; {count_t} tickets, "
                    f"sentiment {sent:+.2f}</p>"
                )

        html.append("</div>")
        return "".join(html)

    def _load_past_report(self, report_id):
        """Reload a past trending topics report into the main view."""
        report = self.db.get_full_report(report_id)
        if not report:
            return
        raw = report.get("full_results", "")
        if not raw:
            return
        try:
            result = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return
        self._display_results(result)

    # ═══════════════════════════════════════════
    #  HELPERS
    # ═══════════════════════════════════════════

    @staticmethod
    def _clear_layout(layout):
        """Remove all widgets from a layout."""
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    # ═══════════════════════════════════════════
    #  HYPOTHESIS TEST TAB
    # ═══════════════════════════════════════════

    def _build_hypothesis_tab(self):
        """Build the Hypothesis Test tab widget."""
        self._hyp_worker = None

        widget = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(f"QScrollArea {{ background: {ALMA_CREAM}; border: none; }}")

        content = QWidget()
        content.setStyleSheet(f"background: {ALMA_CREAM};")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(16)

        # Header
        header = QLabel("Hypothesis Test")
        header.setObjectName("PageHeader")
        layout.addWidget(header)

        sub = QLabel("Type a hypothesis and test it against ticket data using keyword and semantic search")
        sub.setObjectName("PageSubheader")
        layout.addWidget(sub)

        # Input card
        input_card = QFrame()
        input_card.setObjectName("HypInputCard")
        input_card.setStyleSheet(f"""
            #HypInputCard {{
                background: {ALMA_BG_ELEVATED}; border: none;
                border-radius: 12px;
            }}
        """)
        apply_card_shadow_soft(input_card)
        input_layout = QVBoxLayout(input_card)
        input_layout.setContentsMargins(16, 14, 16, 14)
        input_layout.setSpacing(10)

        input_lbl = QLabel("Hypothesis")
        input_lbl.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {ALMA_TEXT_MID}; letter-spacing: 0.5px; border: none;")
        input_layout.addWidget(input_lbl)

        self._hyp_input = QTextEdit()
        self._hyp_input.setPlaceholderText("e.g. 'Auto-pay is causing client churn'")
        self._hyp_input.setMaximumHeight(72)
        self._hyp_input.setStyleSheet(f"""
            QTextEdit {{
                background: {ALMA_CREAM}; color: {ALMA_TEXT_DARK};
                border: none; border-radius: 8px;
                padding: 8px 10px; font-size: 13px;
            }}
        """)
        input_layout.addWidget(self._hyp_input)

        # Date range row
        date_row = QHBoxLayout()
        date_row.setSpacing(12)

        date_lbl_style = f"font-size: 11px; font-weight: 600; color: {ALMA_TEXT_MID}; letter-spacing: 0.5px; border: none;"
        date_field_style = f"""
            QDateEdit {{
                color: {ALMA_TEXT_DARK}; background: {ALMA_WHITE};
                border: none; border-radius: 8px;
                padding: 8px 12px; font-size: 13px;
            }}
        """

        from_col = QVBoxLayout()
        from_col.setSpacing(4)
        from_lbl = QLabel("From")
        from_lbl.setStyleSheet(date_lbl_style)
        from_col.addWidget(from_lbl)
        self._hyp_date_from = ModernDatePicker()
        self._hyp_date_from.setDate(QDate.currentDate().addDays(-90))
        from_col.addWidget(self._hyp_date_from)
        date_row.addLayout(from_col)

        to_col = QVBoxLayout()
        to_col.setSpacing(4)
        to_lbl = QLabel("To")
        to_lbl.setStyleSheet(date_lbl_style)
        to_col.addWidget(to_lbl)
        self._hyp_date_to = ModernDatePicker()
        self._hyp_date_to.setDate(QDate.currentDate())
        to_col.addWidget(self._hyp_date_to)
        date_row.addLayout(to_col)
        date_row.addStretch()

        btn_col = QVBoxLayout()
        btn_col.setSpacing(4)
        btn_col.addWidget(QLabel(""))
        self._hyp_run_btn = QPushButton("  Test Hypothesis  ")
        self._hyp_run_btn.setMinimumHeight(36)
        self._hyp_run_btn.setCursor(Qt.PointingHandCursor)
        self._hyp_run_btn.clicked.connect(self._on_test_hypothesis)
        btn_col.addWidget(self._hyp_run_btn)
        date_row.addLayout(btn_col)

        input_layout.addLayout(date_row)
        layout.addWidget(input_card)

        # Progress label (hidden until running)
        self._hyp_progress_lbl = QLabel("")
        self._hyp_progress_lbl.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID};")
        self._hyp_progress_lbl.setVisible(False)
        layout.addWidget(self._hyp_progress_lbl)

        # Results area (hidden until first run)
        self._hyp_results_frame = QFrame()
        self._hyp_results_frame.setVisible(False)
        self._hyp_results_layout = QVBoxLayout(self._hyp_results_frame)
        self._hyp_results_layout.setContentsMargins(0, 0, 0, 0)
        self._hyp_results_layout.setSpacing(12)
        layout.addWidget(self._hyp_results_frame)

        layout.addStretch()
        scroll.setWidget(content)

        outer = QVBoxLayout(widget)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        return widget

    def _on_test_hypothesis(self):
        """Start hypothesis testing worker."""
        if getattr(self, '_scan_blocked', False):
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "Scan Active",
                "An NLP scan is in progress. Hypothesis testing is "
                "paused until the scan completes."
            )
            return

        hypothesis = self._hyp_input.toPlainText().strip()
        if not hypothesis:
            return

        if self._hyp_worker and self._hyp_worker.isRunning():
            return

        self._hyp_run_btn.setEnabled(False)
        self._hyp_run_btn.setText("  Testing...  ")
        self._hyp_progress_lbl.setVisible(True)
        self._hyp_progress_lbl.setText("Starting...")

        date_start = self._hyp_date_from.date().toString("yyyy-MM-dd")
        date_end = self._hyp_date_to.date().toString("yyyy-MM-dd")
        window_size = "Weekly"  # use a sensible default

        # Load routed provider client for hypothesis testing.
        # Variable kept as ``gemini_client`` for downstream compat — value
        # may now be a ClaudeCliClient when override_all=claude.
        gemini_client = None
        try:
            from src.gemini.client_factory import build_client_for_task
            gc = build_client_for_task("ab_comparison")
            if gc is not None and gc.is_available():
                gemini_client = gc
        except Exception:
            pass

        self._hyp_worker = HypothesisWorker(
            self.db.db_path, hypothesis, date_start, date_end,
            window_size, gemini_client=gemini_client,
        )
        self._hyp_worker.progress.connect(self._on_hyp_progress)
        self._hyp_worker.finished.connect(self._on_hyp_finished)
        self._hyp_worker.error.connect(self._on_hyp_error)
        self._hyp_worker.start()

    def _on_hyp_progress(self, msg: str):
        self._hyp_progress_lbl.setText(msg)

    def _on_hyp_error(self, trace: str):
        self._hyp_run_btn.setEnabled(True)
        self._hyp_run_btn.setText("  Test Hypothesis  ")
        self._hyp_progress_lbl.setText("Error \u2014 see console")
        print(trace)

    def _on_hyp_finished(self, result: dict):
        self._hyp_run_btn.setEnabled(True)
        self._hyp_run_btn.setText("  Test Hypothesis  ")
        self._hyp_progress_lbl.setVisible(False)
        self._display_hypothesis_results(result)

    def _display_hypothesis_results(self, result: dict):
        """Render hypothesis evidence panels."""
        self._clear_layout(self._hyp_results_layout)
        self._hyp_results_frame.setVisible(True)

        STRENGTH_BADGE = {
            "strong":      ("\U0001f7e2", "Strong",      ALMA_SUCCESS),
            "moderate":    ("\U0001f7e1", "Moderate",    ALMA_WARNING),
            "weak":        ("\U0001f534", "Weak",         ALMA_ERROR),
            "insufficient":("\u26aa", "Insufficient", ALMA_TEXT_LIGHT),
        }
        strength = result.get("evidence_strength", "insufficient")
        icon, label, color = STRENGTH_BADGE.get(strength, STRENGTH_BADGE["insufficient"])

        # ── Hypothesis card ──
        hyp_card = QFrame()
        hyp_card.setObjectName("HypCard")
        hyp_card.setStyleSheet(f"""
            #HypCard {{
                background: {ALMA_BG_ELEVATED}; border: none;
                border-radius: 12px;
            }}
        """)
        apply_card_shadow_soft(hyp_card)
        hyp_layout = QVBoxLayout(hyp_card)
        hyp_layout.setContentsMargins(16, 14, 16, 14)
        hyp_layout.setSpacing(8)

        top_row = QHBoxLayout()
        hyp_text = QLabel(f'"{result.get("hypothesis", "")}"')
        hyp_text.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {ALMA_TEXT_DARK}; border: none;")
        hyp_text.setWordWrap(True)
        top_row.addWidget(hyp_text, 1)

        badge = QLabel(f"{icon} {label}")
        badge.setStyleSheet(f"""
            font-size: 11px; font-weight: 700; color: {color};
            background: transparent; border: 1px solid {color};
            border-radius: 12px; padding: 3px 10px;
        """)
        badge.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        top_row.addWidget(badge)
        hyp_layout.addLayout(top_row)

        # Concepts extracted
        concepts = result.get("concepts_extracted", [])
        if concepts:
            chips_row = QHBoxLayout()
            chips_row.setSpacing(6)
            chip_lbl = QLabel("Concepts:")
            chip_lbl.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_MID}; border: none;")
            chips_row.addWidget(chip_lbl)
            for c in concepts[:8]:
                chip = QLabel(c.replace("_", " "))
                chip.setStyleSheet(f"""
                    font-size: 11px; font-weight: 600;
                    color: {ALMA_GREEN_DARK};
                    background: {ALMA_GREEN_SUBTLE};
                    border-radius: 8px; padding: 2px 8px; border: none;
                """)
                chips_row.addWidget(chip)
            chips_row.addStretch()
            hyp_layout.addLayout(chips_row)

        self._hyp_results_layout.addWidget(hyp_card)

        # ── Evidence panels grid ──
        evidence_card = QFrame()
        evidence_card.setObjectName("EvidenceCard")
        evidence_card.setStyleSheet(f"""
            #EvidenceCard {{
                background: {ALMA_BG_ELEVATED}; border: none;
                border-radius: 12px;
            }}
        """)
        apply_card_shadow_soft(evidence_card)
        ev_layout = QVBoxLayout(evidence_card)
        ev_layout.setContentsMargins(16, 14, 16, 14)
        ev_layout.setSpacing(10)

        ev_title = QLabel("Evidence")
        ev_title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        ev_layout.addWidget(ev_title)

        def _ev_row(label: str, value: str, color: str = ALMA_TEXT_DARK):
            row = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID}; min-width: 180px; border: none;")
            val = QLabel(value)
            val.setStyleSheet(f"font-size: 12px; color: {color}; border: none;")
            val.setWordWrap(True)
            row.addWidget(lbl)
            row.addWidget(val, 1)
            return row

        # Ticket count
        matching = result.get("matching_tickets", [])
        trc_set = {t.get("trc_code", "") for t in matching if t.get("trc_code")}
        ev_layout.addLayout(_ev_row(
            "Matching tickets:",
            f"{len(matching)} across {len(trc_set)} TRC(s): {', '.join(sorted(trc_set)[:5])}",
        ))

        # Concept groups
        concept_groups = result.get("concept_groups", {})
        for concept, group in list(concept_groups.items())[:4]:
            trc_summary = ", ".join(f"{k}:{v}" for k, v in list(group.get("trcs", {}).items())[:3])
            ev_layout.addLayout(_ev_row(
                f"  {concept.replace('_', ' ')}:",
                f"{len(group.get('ticket_ids', []))} tickets  |  {trc_summary}",
            ))

        # Temporal pattern
        temporal = result.get("temporal_pattern", {})
        ev_layout.addLayout(_ev_row(
            "Temporal pattern:",
            temporal.get("description", "No pattern detected"),
        ))

        # Correlation
        corr = result.get("correlation", {})
        ev_layout.addLayout(_ev_row(
            "Correlation:",
            corr.get("interpretation", "No correlation detected"),
        ))

        # Incident corroboration
        incidents = result.get("incident_signals", {})
        flags = incidents.get("related_flags", [])
        if flags:
            desc = incidents.get("description", "")
            ev_layout.addLayout(_ev_row(
                "\U0001f6a8 Incident corroboration:",
                desc,
                color=ALMA_ERROR,
            ))

        # Sentiment
        sentiment = result.get("sentiment_data", {})
        if sentiment:
            avg = sentiment.get("avg_compound", 0)
            trend = sentiment.get("trend", "neutral")
            color = ALMA_ERROR if avg < -0.1 else ALMA_SUCCESS if avg > 0.1 else ALMA_TEXT_MID
            ev_layout.addLayout(_ev_row(
                "Sentiment:",
                f"{trend}  (avg {avg:+.2f})",
                color=color,
            ))

        self._hyp_results_layout.addWidget(evidence_card)

        # ── AI Synthesis ──
        synth_card = QFrame()
        synth_card.setObjectName("SynthCard")
        synth_card.setStyleSheet(f"""
            #SynthCard {{
                background: {ALMA_BG_ELEVATED}; border: none;
                border-radius: 12px;
            }}
        """)
        apply_card_shadow_soft(synth_card)
        synth_layout = QVBoxLayout(synth_card)
        synth_layout.setContentsMargins(16, 14, 16, 14)
        synth_layout.setSpacing(8)

        synth_title = QLabel("AI Synthesis")
        synth_title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        synth_layout.addWidget(synth_title)

        gemini_text = result.get("gemini_synthesis")
        if gemini_text:
            from src.ui.widgets.markdown_viewer import MarkdownViewer
            synth_body = MarkdownViewer()
            synth_body.set_markdown(gemini_text)
            synth_body.setMinimumHeight(150)
            synth_layout.addWidget(synth_body)
        else:
            guide = QLabel("Enable Gemini CLI in Settings for AI synthesis")
            guide.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID}; border: none;")
            synth_layout.addWidget(guide)

        self._hyp_results_layout.addWidget(synth_card)
