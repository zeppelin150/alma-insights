"""
Alma Insights — Trending Topics Page
Sentiment trends, rising/cooling terms (TF-IDF velocity), topic clusters,
and term management (Pass 1.5).
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QDateEdit, QFrame, QScrollArea, QSplitter,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QProgressDialog, QApplication, QSizePolicy, QTextBrowser,
    QGridLayout, QMenu, QInputDialog,
)
from PySide6.QtCore import Qt, QDate, QThread, Signal
from PySide6.QtGui import QFont, QColor

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_GREEN_SUBTLE,
    ALMA_WHITE, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_TEXT_ON_DARK, ALMA_BORDER, ALMA_BORDER_LIGHT,
    ALMA_SUCCESS, ALMA_WARNING, ALMA_ERROR, ALMA_INFO,
)
from src.ui.widgets.charts import LineChartWidget, SparklineWidget, DualSparklineWidget
from src.ui.widgets.report_history import ReportHistoryWidget


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
#  TRENDING TOPICS PAGE
# ═══════════════════════════════════════════

class TrendingTopicsPage(QWidget):

    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db = db_manager
        self._worker = None
        self._current_clusters = []
        self._scan_start_time = 0
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Main splitter: top scrollable area + bottom drill-down
        self._splitter = QSplitter(Qt.Vertical)

        # ── Top: Scrollable content ──
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
        self._build_report_history()

        self._layout.addStretch()
        scroll.setWidget(scroll_content)
        self._splitter.addWidget(scroll)

        # ── Bottom: Drill-down panel ──
        self._build_drilldown_panel()

        self._splitter.setSizes([600, 200])
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 1)

        outer.addWidget(self._splitter)

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
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 10px;
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
        frame = QFrame()
        frame.setObjectName("SentimentPanel")
        frame.setStyleSheet(f"#SentimentPanel {{ background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 10px; }}")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)

        title = QLabel("Sentiment Trend")
        title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        layout.addWidget(title)

        self.sentiment_chart = LineChartWidget()
        self.sentiment_chart.setMinimumHeight(250)
        layout.addWidget(self.sentiment_chart)

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
        self.sentiment_table.setMaximumHeight(180)
        self.sentiment_table.setStyleSheet("QTableWidget { border: none; }")
        layout.addWidget(self.sentiment_table)

        self._layout.addWidget(frame)
        self._layout.addSpacing(20)

    # ── CROSS-TRC CORRELATION PANEL ──

    def _build_correlation_panel(self):
        frame = QFrame()
        frame.setObjectName("CorrelationPanel")
        frame.setStyleSheet(f"#CorrelationPanel {{ background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 10px; }}")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)

        title = QLabel("Cross-TRC Correlation Signals")
        title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        layout.addWidget(title)

        subtitle = QLabel("Statistically significant correlations between TRC metrics across time")
        subtitle.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none;")
        layout.addWidget(subtitle)
        layout.addSpacing(8)

        self._correlation_container = QVBoxLayout()
        self._correlation_container.setSpacing(8)
        layout.addLayout(self._correlation_container)

        self._correlation_message = QLabel("Run analysis to detect cross-TRC correlations")
        self._correlation_message.setStyleSheet(f"color: {ALMA_TEXT_LIGHT}; font-size: 12px; border: none;")
        self._correlation_message.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._correlation_message)

        self._correlation_frame = frame
        self._layout.addWidget(frame)
        self._layout.addSpacing(20)

    # ── RISING TERMS PANEL ──

    def _build_terms_panel(self):
        frame = QFrame()
        frame.setObjectName("TermsPanel")
        frame.setStyleSheet(f"#TermsPanel {{ background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 10px; }}")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)

        title = QLabel("Rising & Cooling Terms")
        title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
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

        self._terms_frame = frame
        self._layout.addWidget(frame)
        self._layout.addSpacing(20)

    # ── TOPIC CLUSTERS PANEL ──

    def _build_topics_panel(self):
        frame = QFrame()
        frame.setObjectName("TopicsPanel")
        frame.setStyleSheet(f"#TopicsPanel {{ background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 10px; }}")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)

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

        self._clusters_frame = frame
        self._layout.addWidget(frame)

    # ── DRILL-DOWN PANEL ──

    def _build_drilldown_panel(self):
        frame = QFrame()
        frame.setStyleSheet(f"background: {ALMA_WHITE}; border-top: 2px solid {ALMA_BORDER};")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(28, 12, 28, 12)
        layout.setSpacing(8)

        header_row = QHBoxLayout()
        self._drilldown_title = QLabel("Drill-Down: select a term or cluster above")
        self._drilldown_title.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {ALMA_TEXT_MID};")
        header_row.addWidget(self._drilldown_title, 1)

        self._drilldown_count = QLabel("")
        self._drilldown_count.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT};")
        header_row.addWidget(self._drilldown_count)
        layout.addLayout(header_row)

        self._drilldown_table = QTableWidget()
        self._drilldown_table.setColumnCount(5)
        self._drilldown_table.setHorizontalHeaderLabels([
            "Ticket ID", "Subject", "TRC", "CSAT", "Date"
        ])
        self._drilldown_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for i in [0, 2, 3, 4]:
            self._drilldown_table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self._drilldown_table.verticalHeader().setVisible(False)
        self._drilldown_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._drilldown_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._drilldown_table.setAlternatingRowColors(True)
        self._drilldown_table.setStyleSheet(f"""
            QTableWidget {{ alternate-background-color: rgba(3,40,27,0.02); }}
        """)
        self._drilldown_table.currentCellChanged.connect(self._on_drilldown_row_selected)
        layout.addWidget(self._drilldown_table)

        self._drilldown_results = []
        self._splitter.addWidget(frame)

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

        date_start = self.date_from.date().toString("yyyy-MM-dd")
        date_end = self.date_to.date().toString("yyyy-MM-dd") + "T23:59:59"
        trc_filter = self.trc_combo.currentData() or None
        window_size = self.window_combo.currentText()

        # Progress dialog
        self._progress = QProgressDialog("Initializing analysis...", None, 0, 0, self)
        self._progress.setWindowTitle("Analyzing Trends")
        self._progress.setWindowModality(Qt.WindowModal)
        self._progress.setMinimumDuration(0)
        self._progress.show()
        QApplication.processEvents()

        topic_method = self.method_combo.currentData()
        self._worker = TrendingWorker(
            self.db.db_path, date_start, date_end, trc_filter, window_size, topic_method
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_results)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_progress(self, msg):
        if hasattr(self, "_progress"):
            self._progress.setLabelText(msg)
            QApplication.processEvents()

    def _on_results(self, result):
        if hasattr(self, "_progress"):
            self._progress.close()

        self._display_results(result)

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

        self.report_history.refresh()

    def _display_results(self, result):
        """Populate all panels from analysis results."""
        self._populate_sentiment(result.get("sentiment", {}))
        self._populate_correlations(result.get("correlations", {}))
        self._populate_terms(result.get("terms", {}))
        self._populate_topics(result.get("topics", {}))

    def _on_error(self, error_text):
        if hasattr(self, "_progress"):
            self._progress.close()
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
            return

        self.sentiment_chart.set_data(
            data, y_min=-1.0, y_max=1.0,
            show_zero_line=True, show_bg_tint=True
        )

        # Summary table
        self.sentiment_table.setRowCount(len(data))
        for i, (trc, series) in enumerate(data.items()):
            self.sentiment_table.setItem(i, 0, QTableWidgetItem(trc))

            current = series[-1][1] if series else 0
            previous = series[-2][1] if len(series) >= 2 else 0
            delta = current - previous

            self.sentiment_table.setItem(i, 1, QTableWidgetItem(f"{current:+.2f}"))
            self.sentiment_table.setItem(i, 2, QTableWidgetItem(f"{previous:+.2f}"))

            delta_item = QTableWidgetItem(f"{delta:+.2f} {'↓' if delta < 0 else '↑'}")
            delta_item.setForeground(QColor(ALMA_ERROR if delta < 0 else ALMA_SUCCESS))
            self.sentiment_table.setItem(i, 3, delta_item)

            if delta < -0.05:
                trend = "Declining"
                trend_color = ALMA_ERROR
            elif delta > 0.05:
                trend = "Improving"
                trend_color = ALMA_SUCCESS
            else:
                trend = "Stable"
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
        vel_lbl = QLabel(f"{vel:+.4f}")
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
        """Show matching tickets for a clicked term."""
        from src.data.trending_engine import get_tickets_for_term

        date_start = self.date_from.date().toString("yyyy-MM-dd")
        date_end = self.date_to.date().toString("yyyy-MM-dd") + "T23:59:59"
        trc_filter = self.trc_combo.currentData() or None

        tickets = get_tickets_for_term(self.db, term, date_start, date_end, trc_filter, limit=50)
        total = len(tickets)

        self._drilldown_title.setText(f"Drill-Down: \"{term}\"")
        self._drilldown_count.setText(
            f"Showing {min(total, 50)} of {total} matches" if total > 50 else f"{total} matches"
        )
        self._populate_drilldown(tickets)

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
            return

        self._correlation_message.hide()

        for corr in correlations[:10]:
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
                background: {ALMA_WHITE}; border: 1px solid {border_color};
                border-radius: 8px; padding: 8px;
            }}
            QFrame:hover {{ background: {ALMA_CREAM}; }}
        """)

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
        """Show tickets from both correlated TRC codes."""
        from src.data.trending_engine import get_tickets_for_term

        date_start = self.date_from.date().toString("yyyy-MM-dd")
        date_end = self.date_to.date().toString("yyyy-MM-dd") + "T23:59:59"

        # Fetch tickets from both TRCs (no term filter, use TRC filter approach)
        tickets_a = get_tickets_for_term(self.db, "", date_start, date_end, trc_a, limit=25)
        tickets_b = get_tickets_for_term(self.db, "", date_start, date_end, trc_b, limit=25)

        # Combine and interleave
        tickets = []
        for ta, tb in zip(tickets_a, tickets_b):
            tickets.append(ta)
            tickets.append(tb)
        # Add remainders
        longer = tickets_a if len(tickets_a) > len(tickets_b) else tickets_b
        shorter_len = min(len(tickets_a), len(tickets_b))
        tickets.extend(longer[shorter_len:])

        total = len(tickets)
        self._drilldown_title.setText(f"Drill-Down: {trc_a} ↔ {trc_b}")
        self._drilldown_count.setText(f"{total} tickets")
        self._populate_drilldown(tickets[:50])

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
                background: {ALMA_WHITE}; border: 2px solid {border_color};
                border-radius: 8px; padding: 12px;
            }}
            QFrame:hover {{ background: {ALMA_CREAM}; }}
        """)

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
        """Show tickets from a clicked cluster/topic."""
        if cluster_index >= len(self._current_clusters):
            return

        cluster = self._current_clusters[cluster_index]
        ticket_ids = cluster.get("ticket_ids", [])

        from src.data.trending_engine import get_tickets_by_ids
        tickets = get_tickets_by_ids(self.db, ticket_ids, limit=50)
        total = len(ticket_ids)

        label = cluster["label"][:50]
        self._drilldown_title.setText(f"Drill-Down: \"{label}\"")
        self._drilldown_count.setText(
            f"Showing {min(total, 50)} of {total} tickets" if total > 50 else f"{total} tickets"
        )
        self._populate_drilldown(tickets)

    # ═══════════════════════════════════════════
    #  DRILL-DOWN TABLE
    # ═══════════════════════════════════════════

    def _populate_drilldown(self, tickets):
        self._drilldown_results = tickets
        self._drilldown_table.setRowCount(len(tickets))

        for i, t in enumerate(tickets):
            self._drilldown_table.setItem(i, 0, QTableWidgetItem(t.get("ticket_id", "")))
            self._drilldown_table.setItem(i, 1, QTableWidgetItem(t.get("subject", "")))
            self._drilldown_table.setItem(i, 2, QTableWidgetItem(t.get("trc_code", "")))

            csat = t.get("csat_score")
            self._drilldown_table.setItem(i, 3, QTableWidgetItem(
                str(int(csat)) if csat else "\u2014"
            ))

            date_str = (t.get("created_at") or "")[:10]
            self._drilldown_table.setItem(i, 4, QTableWidgetItem(date_str))

    def _on_drilldown_row_selected(self, row, col, prev_row, prev_col):
        """Open full thread dialog when a drill-down row is clicked."""
        if row < 0 or row >= len(self._drilldown_results):
            return

        conv = self._drilldown_results[row]
        thread = conv.get("full_thread", "")
        if not thread:
            return

        from PySide6.QtWidgets import QDialog, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Ticket {conv.get('ticket_id', '')} — {conv.get('subject', '')}")
        dlg.setMinimumSize(700, 500)
        dlg_layout = QVBoxLayout(dlg)

        viewer = QTextBrowser()
        viewer.setStyleSheet(f"""
            QTextBrowser {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 8px; padding: 12px; font-size: 13px;
            }}
        """)
        viewer.setPlainText(thread)
        dlg_layout.addWidget(viewer)

        btn = QDialogButtonBox(QDialogButtonBox.Close)
        btn.rejected.connect(dlg.reject)
        dlg_layout.addWidget(btn)

        dlg.exec()

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
        """Open the Term Manager dialog."""
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

    def _build_report_history(self):
        """Add Report History widget at bottom of scroll content."""
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet(f"background: {ALMA_BORDER_LIGHT}; max-height: 1px; border: none;")
        self._layout.addSpacing(20)
        self._layout.addWidget(divider)

        self.report_history = ReportHistoryWidget(self.db, "trending_topics")
        self.report_history.report_selected.connect(self._load_past_report)
        self._layout.addWidget(self.report_history)

    def _load_past_report(self, report_id):
        """Reload a past trending topics report."""
        import json
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
