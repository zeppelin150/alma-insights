"""
Alma Insights — Trending Topics Page
Sentiment trends, rising/cooling terms (TF-IDF velocity), topic clusters,
and term management (Pass 1.5).
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QFrame, QScrollArea,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QApplication, QSizePolicy, QTextBrowser,
    QGridLayout, QMenu, QInputDialog, QTabWidget, QTextEdit,
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
from src.ui.widgets.charts import LineChartWidget, SparklineWidget, DualSparklineWidget
from src.ui.widgets.report_history_summary import ReportHistorySummary
from src.ui.widgets.smoothing_panel import SmoothingReviewPanel
from src.ui.widgets.keyword_panel import KeywordReviewPanel
from src.ui.widgets.term_manager_panel import TermManagerPanel
from src.ui.widgets.collapsible_section import CollapsibleSection
from src.ui.widgets.pagination_bar import PaginationBar
from src.ui.layman_mode import is_layman_mode, translate_label, format_sentiment, format_tfidf


# ═══════════════════════════════════════════
#  WORKER THREAD
# ═══════════════════════════════════════════

class TrendingWorker(QThread):
    progress = Signal(str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, db_path, date_start, date_end, trc_filter, window_size,
                 topic_method="nmf"):
        super().__init__()
        self.db_path = db_path
        self.date_start = date_start
        self.date_end = date_end
        self.trc_filter = trc_filter
        self.window_size = window_size
        self.topic_method = topic_method

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
            from src.gemini.gemini_client import GeminiClient
            client = GeminiClient()
            if not client.is_available():
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

class TrendingTopicsPage(QWidget):

    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db = db_manager
        self._drilldown = None
        self._worker = None
        self._current_clusters = []
        self._scan_start_time = 0
        self._last_analysis_result = {}
        self._build_ui()

    def set_drilldown_panel(self, panel):
        """Accept a reference to the shared DrilldownPanel from MainWindow."""
        self._drilldown = panel

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Tab bar ──
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: none; background: transparent;
            }}
            QTabBar::tab {{
                background: transparent; color: {ALMA_TEXT_MID};
                font-size: 13px; font-weight: 600;
                padding: 10px 22px; border: none;
                border-bottom: 3px solid transparent;
            }}
            QTabBar::tab:selected {{
                color: {ALMA_GREEN_DARK};
                border-bottom: 3px solid {ALMA_GREEN_DARK};
            }}
            QTabBar::tab:hover:!selected {{ color: {ALMA_TEXT_DARK}; }}
        """)

        # ── Tab 0: Analysis (existing content) ──
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
        self._build_sentiment_panel()
        self._build_correlation_panel()
        self._build_terms_panel()
        self._build_topics_panel()
        self._build_ai_tools_bar()
        self._build_report_history()

        self._layout.addStretch()
        scroll.setWidget(scroll_content)
        self._tabs.addTab(scroll, "Analysis")

        # ── Tab 1: Hypothesis Test ──
        self._hypothesis_tab = self._build_hypothesis_tab()
        self._tabs.addTab(self._hypothesis_tab, "Hypothesis Test")

        outer.addWidget(self._tabs)

    # ── HEADER ──

    def _build_header(self):
        header = QLabel("Trending Topics")
        header.setObjectName("PageHeader")
        self._layout.addWidget(header)

        sub = QLabel("Sentiment trends, rising terms, and topic clusters detected from ticket text")
        sub.setObjectName("PageSubheader")
        self._layout.addWidget(sub)
        self._layout.addSpacing(20)

    # ── FILTER BAR ──

    def _build_filter_bar(self):
        card = QFrame()
        card.setObjectName("FilterCard")
        card.setStyleSheet(f"""
            #FilterCard {{
                background: {ALMA_BG_ELEVATED}; border: 1px solid rgba(214, 210, 202, 0.45);
                border-radius: 12px;
            }}
            #FilterCard QLabel {{
                color: {ALMA_TEXT_MID}; border: none; background: transparent;
            }}
            #FilterCard QComboBox {{
                color: {ALMA_TEXT_DARK}; background: {ALMA_WHITE};
                border: 1px solid {ALMA_BORDER}; border-radius: 8px;
                padding: 8px 12px; font-size: 13px;
            }}
            #FilterCard QDateEdit {{
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

        # Window size
        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("Window")
        lbl.setStyleSheet(label_style)
        col.addWidget(lbl)
        self.window_combo = QComboBox()
        for w in ["Hourly", "Daily", "Weekly", "Biweekly", "Monthly"]:
            self.window_combo.addItem(w, w)
        self.window_combo.setCurrentIndex(2)  # Default Weekly
        col.addWidget(self.window_combo)
        row.addLayout(col, 1)

        # Topic method
        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("Topic Method")
        lbl.setStyleSheet(label_style)
        col.addWidget(lbl)
        self.method_combo = QComboBox()
        self.method_combo.addItem("NMF Topics (overlap)", "nmf")
        self.method_combo.addItem("K-Means Clusters", "kmeans")
        col.addWidget(self.method_combo)
        row.addLayout(col, 1)

        # Analyze button
        col = QVBoxLayout()
        col.setSpacing(4)
        col.addWidget(QLabel(""))
        self.analyze_btn = QPushButton("  Analyze  ")
        self.analyze_btn.setMinimumHeight(36)
        self.analyze_btn.setCursor(Qt.PointingHandCursor)
        self.analyze_btn.clicked.connect(self._on_analyze)
        col.addWidget(self.analyze_btn)
        row.addLayout(col, 1)

        # Manage Terms button (Pass 1.5)
        col = QVBoxLayout()
        col.setSpacing(4)
        col.addWidget(QLabel(""))
        manage_btn = QPushButton("📋 Manage Terms")
        manage_btn.setMinimumHeight(36)
        manage_btn.setCursor(Qt.PointingHandCursor)
        manage_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_WHITE}; color: {ALMA_TEXT_DARK};
                font-size: 11px; font-weight: 600;
                border: 1px solid {ALMA_BORDER};
                border-radius: 6px; padding: 4px 12px;
            }}
            QPushButton:hover {{ background: {ALMA_CREAM}; }}
        """)
        manage_btn.clicked.connect(self._open_term_manager)
        col.addWidget(manage_btn)
        row.addLayout(col)

        self._layout.addWidget(card)
        self._layout.addSpacing(20)

    # ── SENTIMENT PANEL ──

    def _build_sentiment_panel(self):
        section = CollapsibleSection("Sentiment Trend", section_key="trending.sentiment_trend")

        self.sentiment_chart = LineChartWidget()
        self.sentiment_chart.setMinimumHeight(250)
        section.add_widget(self.sentiment_chart)

        # Summary table below chart
        self.sentiment_table = QTableWidget()
        self.sentiment_table.setColumnCount(5)
        self.sentiment_table.setHorizontalHeaderLabels([
            "TRC", "Current Window", "Previous Window", "\u0394 Change", "Trend"
        ])
        self.sentiment_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, 5):
            self.sentiment_table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self.sentiment_table.verticalHeader().setVisible(False)
        self.sentiment_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.sentiment_table.setMaximumHeight(250)
        self.sentiment_table.setStyleSheet("QTableWidget { border: none; }")
        section.add_widget(self.sentiment_table)
        self._sentiment_pager = PaginationBar(page_size=10)
        self._sentiment_pager.page_changed.connect(self._on_sentiment_page_changed)
        section.add_widget(self._sentiment_pager)
        self._sentiment_all_data = {}  # full sentiment data for pagination

        self._layout.addWidget(section)
        self._layout.addSpacing(16)

    # ── CROSS-TRC CORRELATION PANEL ──

    def _build_correlation_panel(self):
        section = CollapsibleSection("Cross-TRC Correlation Signals", section_key="trending.cross_trc_correlation")

        corr_inner = QWidget()
        layout = QVBoxLayout(corr_inner)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        subtitle = QLabel("Statistically significant correlations between TRC metrics across time")
        subtitle.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none;")
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

        section.add_widget(corr_inner)
        self._correlation_frame = section
        self._layout.addWidget(section)
        self._layout.addSpacing(16)

    # ── RISING TERMS PANEL ──

    def _build_terms_panel(self):
        section = CollapsibleSection("Rising & Cooling Terms", section_key="trending.rising_cooling_terms")

        terms_inner = QWidget()
        layout = QVBoxLayout(terms_inner)
        layout.setContentsMargins(0, 0, 0, 0)

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

        section.add_widget(terms_inner)
        self._terms_frame = section
        self._layout.addWidget(section)
        self._layout.addSpacing(16)

    # ── TOPIC CLUSTERS PANEL ──

    def _build_topics_panel(self):
        section = CollapsibleSection("Topic Clusters", section_key="trending.topic_clusters")

        topics_inner = QWidget()
        layout = QVBoxLayout(topics_inner)
        layout.setContentsMargins(0, 0, 0, 0)

        self._topics_title = QLabel("Topic Clusters")
        self._topics_title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
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

        section.add_widget(topics_inner)
        self._clusters_frame = section
        self._layout.addWidget(section)
        self._layout.addSpacing(16)

        # ── AI Review Panels (no longer inline — shown in DrilldownPanel) ──
        self._smoothing_panel = SmoothingReviewPanel()
        self._smoothing_panel.suggestions_applied.connect(self._on_smoothing_applied)
        self._smoothing_panel.dismissed.connect(self._on_panel_dismissed)

        self._keyword_panel = KeywordReviewPanel()
        self._keyword_panel.keywords_applied.connect(self._on_keywords_applied)
        self._keyword_panel.dismissed.connect(self._on_panel_dismissed)

        self._term_panel = TermManagerPanel(self.db)
        self._term_panel.terms_changed.connect(self._on_terms_changed)

        # Track AI result counts for badge display
        self._smoothing_count = 0
        self._keyword_count = 0

    # ── AI TOOLS BAR ──

    def _build_ai_tools_bar(self):
        """Compact notification bar with buttons to open AI panels in the DrilldownPanel."""
        self._ai_tools_bar = QFrame()
        self._ai_tools_bar.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED};
                border: 1px solid {ALMA_GREEN_SUBTLE};
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
                border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 6px;
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

        self._layout.addWidget(self._ai_tools_bar)
        self._layout.addSpacing(16)

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
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, "No Data",
                "No conversations found in the database.\n\n"
                "Use the Conversations page to pull data from Lightdash or import a CSV first."
            )
            return

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

        self._worker = TrendingWorker(
            self.db.db_path, date_start, date_end, trc_filter, window_size, topic_method
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_results)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_progress(self, msg):
        """Progress callback — forwarded by job queue to overlay."""
        pass  # Handled by unified overlay now

    def _on_results(self, result):
        self._display_results(result)

        # Show AI Tools bar
        self._ai_tools_bar.setVisible(True)

        # Run AI enhancements in background thread (non-blocking)
        self._last_analysis_result = result
        self._run_ai_enhancements(result)

        # Save report
        import time, json
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

        self.report_summary.refresh()

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
            return

        # Line chart always shows all series (handles many fine)
        self.sentiment_chart.set_data(
            data, y_min=-1.0, y_max=1.0,
            show_zero_line=True, show_bg_tint=True
        )

        # Store full data for table pagination
        self._sentiment_all_data = data
        self._sentiment_pager.set_total(len(data))
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

        all_items = list(self._sentiment_all_data.items())
        ps = self._sentiment_pager.page_size
        start = page * ps
        end = min(start + ps, len(all_items))
        page_items = all_items[start:end]

        self.sentiment_table.setRowCount(len(page_items))
        for i, (trc, series) in enumerate(page_items):
            self.sentiment_table.setItem(i, 0, QTableWidgetItem(trc))

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
            badge = QLabel("★")
            badge.setStyleSheet(f"color: {ALMA_SUCCESS}; font-size: 12px; border: none;")
            badge.setToolTip("Marked as important")
            layout.addWidget(badge)
        elif user_action == "demote" or user_action == "noise":
            badge = QLabel("✕")
            badge.setStyleSheet(f"color: {ALMA_ERROR}; font-size: 12px; border: none;")
            badge.setToolTip("Marked as noise")
            layout.addWidget(badge)
        elif user_action == "watchlist":
            badge = QLabel("👁")
            badge.setStyleSheet("font-size: 11px; border: none;")
            badge.setToolTip("On watchlist")
            layout.addWidget(badge)

        # Temporal promotion badge
        if term_info.get("temporal_promoted"):
            fire = QLabel("🔥")
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

        # Click handler (left-click → drill-down)
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
                background: {ALMA_BG_ELEVATED}; border: 1px solid {border_color};
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
                f"{lead_lag['leader']} → ({lead_lag['lag_label']}) → {lead_lag['follower']}"
            )
        else:
            pair_text = f"{corr['trc_a']}  ↔  {corr['trc_b']}"

        pair_lbl = QLabel(pair_text)
        pair_lbl.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        text_col.addWidget(pair_lbl)

        # Metrics and strength
        r_val = corr["r"]
        strength_color = ALMA_ERROR if strength == "strong" else ALMA_WARNING
        metrics_text = (
            f"{corr['metric_a']} vs. {corr['metric_b']}  •  "
            f"r = {r_val:+.2f}  •  p = {corr['p_value']:.3f}"
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
            lag_badge = QLabel(f"⏱ {lead_lag['interpretation']}")
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
                    f"🔗 {len(multi)} tickets span multiple topics"
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

        stats = QLabel("  •  ".join(stats_parts))
        stats.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; border: none;")
        stats.setWordWrap(True)
        layout.addWidget(stats)

        # Trend badge (NMF only)
        trend_dir = topic.get("trend_direction")
        if trend_dir:
            if trend_dir == "rising":
                badge_text = "↑ Rising"
                badge_bg = "rgba(22,118,58,0.12)"
                badge_color = ALMA_SUCCESS
            elif trend_dir == "declining":
                badge_text = "↓ Declining"
                badge_bg = "rgba(196,30,30,0.12)"
                badge_color = ALMA_ERROR
            else:
                badge_text = "→ Stable"
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
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER};
                border-radius: 6px; padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 20px; font-size: 12px;
            }}
            QMenu::item:selected {{
                background: {ALMA_CREAM};
            }}
        """)

        menu.addAction("⭐ Mark as important", lambda: self._record_feedback(term, "important"))
        menu.addAction("✓ Relevant", lambda: self._record_feedback(term, "relevant"))
        menu.addAction("✗ Mark as noise", lambda: self._record_feedback(term, "noise"))
        menu.addSeparator()
        menu.addAction("🔗 Merge with...", lambda: self._merge_term(term))
        menu.addAction("👁 Add to watchlist", lambda: self._record_feedback(term, "watchlist"))

        menu.exec(widget.mapToGlobal(pos))

    def _record_feedback(self, term, feedback_type):
        """Write term feedback to the database."""
        try:
            self.db.log_tfidf_feedback(term, feedback_type)

            # Also create/update user_term for watchlist
            if feedback_type == "watchlist":
                canonical = term.lower().replace(" ", "_")
                self.db.upsert_user_term(term, canonical, "watchlist", weight_modifier=1.0)

            self.status_msg = f"Recorded: {term} → {feedback_type}"
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
        """Open the Term Manager in the DrilldownPanel (or fallback to dialog)."""
        if self._drilldown:
            self._open_terms_drilldown()
        else:
            from src.ui.dialogs.term_manager_dialog import TermManagerDialog
            dlg = TermManagerDialog(self.db, self)
            dlg.terms_changed.connect(self._on_terms_changed)
            dlg.exec()

    def _on_terms_changed(self):
        """Callback when terms are modified in the Term Manager."""
        # Could re-run analysis automatically; for now just note the change
        pass

    # ═══════════════════════════════════════════
    #  REPORT HISTORY
    # ═══════════════════════════════════════════

    # ═══════════════════════════════════════════
    #  AI ENHANCEMENTS (Smoothing + Keywords)
    # ═══════════════════════════════════════════

    def _run_ai_enhancements(self, result):
        """Launch AI enhancements in a background thread (non-blocking).

        Reads settings on the main thread (fast), then spawns
        AIEnhancementWorker for the actual Gemini calls.
        """
        try:
            import yaml
            from pathlib import Path
            settings_path = Path("config/settings.yaml")
            if settings_path.exists():
                with open(settings_path) as f:
                    settings = yaml.safe_load(f) or {}
            else:
                settings = {}

            ai_cfg = settings.get("ai_enhancements", {})
            smoothing_enabled = ai_cfg.get("smoothing", False)
            keywords_enabled = ai_cfg.get("keywords", False)

            if not (smoothing_enabled or keywords_enabled):
                self._smoothing_btn.setVisible(False)
                self._keywords_btn.setVisible(False)
                return

            # Quick availability check (fast — no CLI call)
            try:
                from src.gemini.gemini_client import GeminiClient
                client = GeminiClient()
                if not client.is_available():
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
        """AI enhancement failed — just hide buttons silently."""
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
        """Open the DrilldownPanel with the term manager."""
        if not self._drilldown:
            return
        self._term_panel.refresh()
        self._drilldown.show_widget(
            "Manage Terms",
            "Weights, compounds & aliases",
            self._term_panel,
        )

    def _on_panel_dismissed(self):
        """Callback when an AI panel emits 'dismissed' — close the drawer."""
        if self._drilldown:
            self._drilldown.close_panel()

    def _build_report_history(self):
        """Add compact Report History summary at bottom of scroll content."""
        self.report_summary = ReportHistorySummary(self.db, "trending_topics")
        self.report_summary.view_all_clicked.connect(self._open_report_history)
        self._layout.addWidget(self.report_summary)

    def _open_report_history(self):
        """Open the DrilldownPanel with the full report history list."""
        if not hasattr(self, "_drilldown") or not self._drilldown:
            return
        reports = self.report_summary.get_reports_for_drilldown()
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
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 24, 28, 24)
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
        input_card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED}; border: 1px solid rgba(214, 210, 202, 0.45);
                border-radius: 12px;
            }}
        """)
        apply_card_shadow(input_card)
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
                border: 1px solid {ALMA_BORDER}; border-radius: 8px;
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
                border: 1px solid {ALMA_BORDER}; border-radius: 8px;
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

        # Load Gemini client if configured
        gemini_client = None
        try:
            from src.gemini.gemini_client import GeminiClient
            gc = GeminiClient()
            if gc.is_available():
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
        self._hyp_progress_lbl.setText("Error — see console")
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
            "strong":      ("🟢", "Strong",      ALMA_SUCCESS),
            "moderate":    ("🟡", "Moderate",    ALMA_WARNING),
            "weak":        ("🔴", "Weak",         ALMA_ERROR),
            "insufficient":("⚪", "Insufficient", ALMA_TEXT_LIGHT),
        }
        strength = result.get("evidence_strength", "insufficient")
        icon, label, color = STRENGTH_BADGE.get(strength, STRENGTH_BADGE["insufficient"])

        # ── Hypothesis card ──
        hyp_card = QFrame()
        hyp_card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED}; border: 1px solid rgba(214, 210, 202, 0.45);
                border-radius: 12px;
            }}
        """)
        apply_card_shadow(hyp_card)
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
        evidence_card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED}; border: 1px solid rgba(214, 210, 202, 0.45);
                border-radius: 12px;
            }}
        """)
        apply_card_shadow(evidence_card)
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
                "🚨 Incident corroboration:",
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
        synth_card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED}; border: 1px solid rgba(214, 210, 202, 0.45);
                border-radius: 12px;
            }}
        """)
        apply_card_shadow(synth_card)
        synth_layout = QVBoxLayout(synth_card)
        synth_layout.setContentsMargins(16, 14, 16, 14)
        synth_layout.setSpacing(8)

        synth_title = QLabel("AI Synthesis")
        synth_title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        synth_layout.addWidget(synth_title)

        gemini_text = result.get("gemini_synthesis")
        if gemini_text:
            synth_body = QTextBrowser()
            synth_body.setPlainText(gemini_text)
            synth_body.setMinimumHeight(150)
            synth_body.setStyleSheet(f"""
                QTextBrowser {{
                    background: {ALMA_CREAM}; color: {ALMA_TEXT_DARK};
                    border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 8px;
                    padding: 10px; font-size: 13px;
                }}
            """)
            synth_layout.addWidget(synth_body)
        else:
            guide = QLabel("Enable Gemini CLI in Settings for AI synthesis")
            guide.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID}; border: none;")
            synth_layout.addWidget(guide)

        self._hyp_results_layout.addWidget(synth_card)
