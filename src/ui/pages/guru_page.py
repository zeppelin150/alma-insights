"""
Alma Insights — Guru Knowledge Base Page (Phase 4, T6)

Five-tab layout for Guru integration:
  1. Cards      — browse/search Guru cards with friction scores
  2. Gap Analysis — friction types vs Guru coverage, sorted by gaps
  3. Drafts     — diff view (current vs proposed), approve/reject
  4. Effectiveness — pre/post volume, delta %, significance
  5. Connection — Guru email + API token, sync articles
"""

import logging
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QLineEdit, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar,
    QAbstractItemView, QMessageBox, QSizePolicy, QSplitter,
    QTextEdit, QComboBox,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_MID, ALMA_GREEN_LIGHT, ALMA_GREEN_SUBTLE,
    ALMA_WHITE, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_TEXT_ON_DARK, ALMA_BORDER, ALMA_BORDER_LIGHT,
    ALMA_SUCCESS, ALMA_WARNING, ALMA_ERROR, ALMA_INFO,
    ALMA_BG_ELEVATED, ALMA_BG_INSET,
    apply_card_shadow, apply_card_shadow_soft,
)

logger = logging.getLogger("alma.guru_page")

# ── Shared style constants ──────────────────────────────────────

_CARD_STYLE = f"""
    QFrame {{
        background: {ALMA_BG_ELEVATED};
        border: 1px solid rgba(214, 210, 202, 0.45);
        border-radius: 12px;
    }}
"""
_FIELD_STYLE = f"""
    QLineEdit {{
        background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER};
        border-radius: 8px; padding: 8px 12px; font-size: 13px;
        color: {ALMA_TEXT_DARK};
    }}
"""
_LBL_STYLE = f"font-size: 11px; font-weight: 600; color: {ALMA_TEXT_MID}; letter-spacing: 0.5px;"

_GHOST_BTN = f"""
    QPushButton {{
        background: transparent; color: {ALMA_GREEN_DARK};
        border: 1px solid {ALMA_GREEN_DARK}; border-radius: 6px;
        padding: 6px 14px; font-size: 12px; font-weight: 600;
    }}
    QPushButton:hover {{ background: {ALMA_CREAM}; }}
    QPushButton:disabled {{ color: {ALMA_TEXT_LIGHT}; border-color: {ALMA_BORDER}; }}
"""

_PRIMARY_BTN = f"""
    QPushButton {{
        background: {ALMA_GREEN_DARK}; color: {ALMA_CREAM};
        border: none; border-radius: 8px; padding: 8px 20px;
        font-weight: 600; font-size: 13px;
    }}
    QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
    QPushButton:disabled {{ background: {ALMA_BORDER}; color: {ALMA_TEXT_LIGHT}; }}
"""

_DANGER_BTN = f"""
    QPushButton {{
        background: {ALMA_ERROR}; color: white;
        border: none; border-radius: 6px; padding: 6px 14px;
        font-weight: 600; font-size: 12px;
    }}
    QPushButton:hover {{ background: #C0392B; }}
"""


class GuruPage(QWidget):
    """Guru Knowledge Base integration page with 6 tabs."""

    connection_changed = Signal()

    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db = db_manager
        self._guru_client = None
        self._friction_pipeline = None
        self._content_pipeline = None
        self._effectiveness_tracker = None
        self._drilldown = None
        self._card_cache: dict = {}  # card_id → full card dict

        self.setStyleSheet(f"background: {ALMA_CREAM};")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 0)
        layout.setSpacing(12)

        # Header
        header = QLabel("Guru Knowledge Base")
        header.setFont(QFont("Segoe UI", 22, QFont.Weight.Bold))
        header.setStyleSheet(f"color: {ALMA_TEXT_DARK}; background: transparent;")
        layout.addWidget(header)

        subtitle = QLabel("Friction analysis · Content drafts · Effectiveness tracking")
        subtitle.setStyleSheet(f"color: {ALMA_TEXT_MID}; font-size: 13px; background: transparent;")
        layout.addWidget(subtitle)

        # Tabs
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        layout.addWidget(self._tabs)

        self._build_cards_tab()
        self._build_workbench_tab()
        self._build_gap_tab()
        self._build_drafts_tab()
        self._build_effectiveness_tab()
        self._build_connection_tab()

        # Refresh workbench data when its tab is selected
        self._tabs.currentChanged.connect(self._on_tab_changed)

    # ── Public wiring ───────────────────────────────────────────

    def set_friction_pipeline(self, pipeline):
        """Wire the GuruFrictionPipeline."""
        self._friction_pipeline = pipeline

    def set_content_pipeline(self, pipeline):
        """Wire the GuruContentPipeline."""
        self._content_pipeline = pipeline

    def set_effectiveness_tracker(self, tracker):
        """Wire the GuruEffectivenessTracker."""
        self._effectiveness_tracker = tracker

    def set_drilldown_panel(self, panel):
        """Wire the shared DrilldownPanel for card detail view."""
        self._drilldown = panel
        if hasattr(self, "_workbench"):
            self._workbench.set_drilldown_panel(panel)

    def set_guru_client(self, client):
        """Wire the GuruClient after MainWindow creates it."""
        self._guru_client = client
        if hasattr(self, "_workbench"):
            self._workbench.set_guru_client(client)
            self._workbench.refresh()

    def _on_tab_changed(self, index: int):
        """Refresh data when switching to data-dependent tabs."""
        widget = self._tabs.widget(index)
        if widget is self._workbench and self._guru_client:
            self._workbench.refresh()

    # ══════════════════════════════════════════════════════════════
    # Tab 1 — Cards
    # ══════════════════════════════════════════════════════════════

    def _build_cards_tab(self):
        tab = QWidget()
        tab.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 12, 0, 0)

        # Search bar
        search_row = QHBoxLayout()
        self._card_search = QLineEdit()
        self._card_search.setPlaceholderText("Search Guru cards…")
        self._card_search.setStyleSheet(_FIELD_STYLE)
        search_row.addWidget(self._card_search)

        search_btn = QPushButton("Search")
        search_btn.setStyleSheet(_GHOST_BTN)
        search_btn.clicked.connect(self._on_search_cards)
        search_row.addWidget(search_btn)

        refresh_btn = QPushButton("Refresh All")
        refresh_btn.setStyleSheet(_GHOST_BTN)
        refresh_btn.clicked.connect(self._on_refresh_cards)
        search_row.addWidget(refresh_btn)
        layout.addLayout(search_row)

        # Cards table
        self._cards_table = QTableWidget(0, 6)
        self._cards_table.setHorizontalHeaderLabels([
            "Title", "Collection", "Friction Score", "Last Synced", "", "",
        ])
        self._cards_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._cards_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._cards_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._cards_table.setAlternatingRowColors(True)
        self._cards_table.verticalHeader().setVisible(False)
        layout.addWidget(self._cards_table)

        self._tabs.addTab(tab, "📄 Cards")

    def _on_search_cards(self):
        query = self._card_search.text().strip()
        if not self._guru_client:
            self._show_not_connected()
            return
        try:
            cards = self._guru_client.search_cards(query)
            self._populate_cards_table(cards)
        except Exception as exc:
            logger.warning("Card search failed: %s", exc)

    def _on_refresh_cards(self):
        if not self._guru_client:
            self._show_not_connected()
            return
        try:
            cards = self._guru_client.list_cards()
            self._populate_cards_table(cards)
        except Exception as exc:
            logger.warning("Card refresh failed: %s", exc)

    def _populate_cards_table(self, cards: list[dict]):
        self._cards_table.setRowCount(len(cards))
        self._card_id_by_row: list[str] = []
        for i, card in enumerate(cards):
            card_id = card.get("id", "")
            self._card_id_by_row.append(card_id)

            self._cards_table.setItem(
                i, 0, QTableWidgetItem(card.get("title", ""))
            )
            self._cards_table.setItem(
                i, 1, QTableWidgetItem(card.get("collection", ""))
            )

            # Friction score from DB
            score = self._get_friction_score(card_id)
            score_item = QTableWidgetItem(f"{score:.2f}" if score else "—")
            self._cards_table.setItem(i, 2, score_item)

            self._cards_table.setItem(
                i, 3, QTableWidgetItem(card.get("lastModified", "")[:10])
            )

            # View button — opens card in drilldown
            view_btn = QPushButton("View")
            view_btn.setStyleSheet(_GHOST_BTN)
            view_btn.clicked.connect(
                lambda checked, cid=card_id: self._on_view_card(cid)
            )
            self._cards_table.setCellWidget(i, 4, view_btn)

            # Analyze button
            btn = QPushButton("Analyze")
            btn.setStyleSheet(_GHOST_BTN)
            btn.clicked.connect(
                lambda checked, cid=card_id: self._on_analyze_card(cid)
            )
            self._cards_table.setCellWidget(i, 5, btn)

    def _get_friction_score(self, card_id: str) -> float:
        try:
            row = self.db.conn.execute(
                "SELECT friction_score FROM guru_articles WHERE card_id = ?",
                (card_id,),
            ).fetchone()
            return row[0] if row else 0.0
        except Exception:
            return 0.0

    def _on_view_card(self, card_id: str):
        """Open a Guru card in the drilldown panel via GuruCardViewer."""
        if not self._drilldown or not self._guru_client:
            return
        try:
            # Use cache if available
            if card_id in self._card_cache:
                card = self._card_cache[card_id]
            else:
                card = self._guru_client.get_card(card_id)
                self._card_cache[card_id] = card

            if not card:
                return

            from src.ui.widgets.guru_card_viewer import GuruCardViewer
            viewer = GuruCardViewer()
            viewer.set_card(card)
            self._drilldown.show_widget(
                card.get("title", "Guru Card"),
                card.get("collection", ""),
                viewer,
            )
        except Exception as exc:
            logger.warning("Card view failed: %s", exc)

    def _on_analyze_card(self, card_id: str):
        if not self._friction_pipeline:
            return
        try:
            results = self._friction_pipeline.analyze_coverage()
            self._refresh_gap_table()
            QMessageBox.information(
                self, "Analysis Complete",
                f"Friction analysis complete. {len(results)} coverage "
                f"records updated.",
            )
        except Exception as exc:
            logger.warning("Friction analysis failed: %s", exc)

    # ══════════════════════════════════════════════════════════════
    # Tab 2 — Workbench
    # ══════════════════════════════════════════════════════════════

    def _build_workbench_tab(self):
        from src.ui.widgets.guru_workbench_panel import GuruWorkbenchPanel
        self._workbench = GuruWorkbenchPanel(self.db, self._guru_client)
        self._workbench.analysis_complete.connect(self._on_workbench_commit)
        self._tabs.addTab(self._workbench, "🔧 Workbench")

    def _on_workbench_commit(self, result: dict):
        """Handle Workbench 'Commit to Drafts' — run propose_rewrite() to
        generate a clean push-ready draft from the redline analysis."""
        card = result.get("card", {})
        redlines = result.get("redlines", [])
        card_id = card.get("id", "")
        if not card_id or not hasattr(self, "_content_pipeline") or not self._content_pipeline:
            logger.warning("Cannot commit draft: missing card_id or content pipeline")
            return
        # Derive friction types from redlines
        friction_types = list({r.get("friction_type", "unknown") for r in redlines if r})
        if not friction_types:
            friction_types = ["general"]
        try:
            draft = self._content_pipeline.propose_rewrite(
                card_id, friction_types
            )
            if draft:
                logger.info("Draft %d created from workbench commit", draft.get("id", -1))
                self._workbench._set_status(
                    f"Draft #{draft.get('id', '?')} created — review in Drafts tab",
                    "#2e7d32"
                )
        except Exception as exc:
            logger.error("Failed to create draft from workbench: %s", exc)
            self._workbench._set_status(f"Draft creation failed: {exc}", "#c62828")

    # ══════════════════════════════════════════════════════════════
    # Tab 3 — Gap Analysis
    # ══════════════════════════════════════════════════════════════

    def _build_gap_tab(self):
        tab = QWidget()
        tab.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 12, 0, 0)

        # Top bar
        top_bar = QHBoxLayout()

        scan_guard = QLabel("")
        scan_guard.setStyleSheet(
            f"color: {ALMA_WARNING}; font-size: 12px; background: transparent;"
        )
        self._gap_guard_label = scan_guard
        top_bar.addWidget(scan_guard)
        top_bar.addStretch()

        from src.ui.widgets.source_selector import SourceSelector
        self._gap_source_selector = SourceSelector(self)
        self._gap_source_selector.setFixedWidth(160)
        top_bar.addWidget(self._gap_source_selector)
        try:
            from src.data.connection_factory import get_connection
            conn = get_connection(self.db.db_path)
            self._gap_source_selector.refresh_sources(conn)
            conn.close()
        except Exception:
            pass

        analyze_btn = QPushButton("Run Gap Analysis")
        analyze_btn.setStyleSheet(_PRIMARY_BTN)
        analyze_btn.clicked.connect(self._on_run_gap_analysis)
        top_bar.addWidget(analyze_btn)

        deep_btn = QPushButton("Analyze Friction")
        deep_btn.setStyleSheet(_GHOST_BTN)
        deep_btn.clicked.connect(self._on_analyze_friction_deep)
        top_bar.addWidget(deep_btn)

        draft_btn = QPushButton("Draft New Article")
        draft_btn.setStyleSheet(_GHOST_BTN)
        draft_btn.clicked.connect(self._on_draft_new_article)
        top_bar.addWidget(draft_btn)

        layout.addLayout(top_bar)

        # Gap table
        self._gap_table = QTableWidget(0, 7)
        self._gap_table.setHorizontalHeaderLabels([
            "Friction Type", "TRC", "Tickets (lifetime)",
            "Coverage", "Gap Score", "Covering Article", "Gap Description",
        ])
        self._gap_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._gap_table.horizontalHeader().setSectionResizeMode(
            6, QHeaderView.ResizeMode.Stretch
        )
        self._gap_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._gap_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._gap_table.setAlternatingRowColors(True)
        self._gap_table.verticalHeader().setVisible(False)
        layout.addWidget(self._gap_table)

        # Deep analysis results area
        self._deep_analysis_frame = QFrame()
        self._deep_analysis_frame.setStyleSheet(_CARD_STYLE)
        self._deep_analysis_frame.setVisible(False)
        da_layout = QVBoxLayout(self._deep_analysis_frame)
        da_layout.setContentsMargins(16, 12, 16, 12)

        da_header = QHBoxLayout()
        self._deep_analysis_title = QLabel("Friction Analysis")
        self._deep_analysis_title.setStyleSheet(
            f"font-size: 14px; font-weight: bold; color: {ALMA_TEXT_DARK}; "
            f"background: transparent; border: none;"
        )
        da_header.addWidget(self._deep_analysis_title)
        da_header.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; "
            f"color: {ALMA_TEXT_MID}; font-size: 14px; }}"
        )
        close_btn.clicked.connect(
            lambda: self._deep_analysis_frame.setVisible(False)
        )
        da_header.addWidget(close_btn)
        da_layout.addLayout(da_header)

        self._deep_analysis_status = QLabel("")
        self._deep_analysis_status.setStyleSheet(
            f"font-size: 12px; color: {ALMA_TEXT_MID}; background: transparent; "
            f"border: none;"
        )
        da_layout.addWidget(self._deep_analysis_status)

        self._deep_analysis_text = QTextEdit()
        self._deep_analysis_text.setReadOnly(True)
        self._deep_analysis_text.setMinimumHeight(200)
        self._deep_analysis_text.setStyleSheet(
            f"QTextEdit {{ background: {ALMA_BG_INSET}; border: 1px solid "
            f"{ALMA_BORDER_LIGHT}; border-radius: 8px; padding: 12px; "
            f"font-size: 13px; color: {ALMA_TEXT_DARK}; }}"
        )
        da_layout.addWidget(self._deep_analysis_text)

        layout.addWidget(self._deep_analysis_frame)

        self._tabs.addTab(tab, "🔍 Gap Analysis")

    def _on_run_gap_analysis(self):
        if not self._friction_pipeline:
            self._show_not_connected()
            return

        # Check if sub_patterns exist
        try:
            count = self.db.conn.execute(
                "SELECT COUNT(*) FROM sub_patterns "
                "WHERE friction_type IS NOT NULL AND friction_type != ''"
            ).fetchone()[0]
        except Exception:
            count = 0

        if count == 0:
            self._gap_guard_label.setText(
                "⚠ No friction types found. Run an NLP scan first."
            )
            return

        self._gap_guard_label.setText("")
        source_id = self._gap_source_selector.selected_source_id() if hasattr(self, '_gap_source_selector') else None
        try:
            self._friction_pipeline.analyze_coverage(source_id=source_id)
            self._friction_pipeline.compute_friction_scores()
            self._refresh_gap_table()
        except Exception as exc:
            logger.warning("Gap analysis failed: %s", exc)

    def _refresh_gap_table(self):
        if not self._friction_pipeline:
            return
        try:
            report = self._friction_pipeline.get_gap_report()
        except Exception:
            return

        self._gap_table.setRowCount(len(report))
        for i, row in enumerate(report):
            self._gap_table.setItem(
                i, 0, QTableWidgetItem(row["friction_type"])
            )
            self._gap_table.setItem(i, 1, QTableWidgetItem(row["trc"]))
            self._gap_table.setItem(
                i, 2, QTableWidgetItem(str(row["ticket_volume"]))
            )

            # Coverage score with color
            score = row["coverage_score"]
            score_item = QTableWidgetItem(f"{score:.2f}")
            if score < 0.3:
                score_item.setForeground(Qt.GlobalColor.red)
            elif score < 0.7:
                score_item.setForeground(Qt.GlobalColor.darkYellow)
            self._gap_table.setItem(i, 3, score_item)

            gap_item = QTableWidgetItem(f"{row['gap_score']:.2f}")
            if row["gap_score"] > 0.7:
                gap_item.setForeground(Qt.GlobalColor.red)
            self._gap_table.setItem(i, 4, gap_item)

            self._gap_table.setItem(
                i, 5, QTableWidgetItem(row["card_title"])
            )
            self._gap_table.setItem(
                i, 6, QTableWidgetItem(row["gap_description"])
            )

    def _on_analyze_friction_deep(self):
        """Run deep friction analysis on the selected gap row."""
        row = self._gap_table.currentRow()
        if row < 0:
            self._gap_guard_label.setText("⚠ Select a row first")
            return

        friction_type = self._gap_table.item(row, 0)
        if not friction_type:
            return
        friction_type = friction_type.text()

        if not self._friction_pipeline:
            self._show_not_connected()
            return

        # Show results panel
        self._deep_analysis_frame.setVisible(True)
        self._deep_analysis_title.setText(f"Friction Analysis: {friction_type}")
        self._deep_analysis_status.setText("Running deep analysis...")
        self._deep_analysis_text.setPlainText("")

        # Build optional LLM client
        llm_client = None
        try:
            from src.gemini.client_factory import build_client_for_task
            llm_client = build_client_for_task("guru_analysis")
        except Exception:
            pass

        def _update_status(msg):
            self._deep_analysis_status.setText(msg)

        try:
            result = self._friction_pipeline.analyze_friction_deep(
                friction_type,
                llm_client=llm_client,
                progress_cb=_update_status,
            )

            if result.get("error"):
                self._deep_analysis_status.setText(
                    f"Completed with warning: {result['error']}"
                )
            else:
                phases = ", ".join(result.get("phases_completed", []))
                self._deep_analysis_status.setText(
                    f"Done — {result['cards_analyzed']} cards analyzed "
                    f"({phases})"
                )

            self._deep_analysis_text.setMarkdown(
                result.get("flow_map_md", "No results.")
            )

        except Exception as exc:
            logger.warning("Deep friction analysis failed: %s", exc)
            self._deep_analysis_status.setText(f"Failed: {exc}")

    def _on_draft_new_article(self):
        """Draft new article for the selected friction type."""
        row = self._gap_table.currentRow()
        if row < 0:
            QMessageBox.information(
                self, "Select Friction Type",
                "Select a row in the gap table first.",
            )
            return

        ft_item = self._gap_table.item(row, 0)
        if not ft_item:
            return

        friction_type = ft_item.text()
        if not self._content_pipeline:
            self._show_not_connected()
            return

        try:
            draft = self._content_pipeline.propose_new_article(friction_type)
            self._refresh_drafts_table()
            self._tabs.setCurrentIndex(2)  # Switch to Drafts tab
            QMessageBox.information(
                self, "Draft Created",
                f"New article draft created: \"{draft['title']}\".\n"
                f"Review it in the Drafts tab.",
            )
        except Exception as exc:
            logger.warning("Draft creation failed: %s", exc)

    # ══════════════════════════════════════════════════════════════
    # Tab 3 — Drafts & Diff
    # ══════════════════════════════════════════════════════════════

    def _build_drafts_tab(self):
        tab = QWidget()
        tab.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 12, 0, 0)

        # Warning bar
        warn = QFrame()
        warn.setStyleSheet(
            f"background: {ALMA_WARNING}22; border: 1px solid {ALMA_WARNING}; "
            f"border-radius: 8px; padding: 8px;"
        )
        warn_layout = QHBoxLayout(warn)
        warn_label = QLabel(
            "⚠ Review all changes carefully before pushing to Guru. "
            "Approved content will update your live knowledge base."
        )
        warn_label.setStyleSheet(
            f"color: {ALMA_TEXT_DARK}; font-size: 12px; background: transparent;"
        )
        warn_label.setWordWrap(True)
        warn_layout.addWidget(warn_label)
        layout.addWidget(warn)

        # Splitter: drafts list (left) + diff view (right)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: drafts list
        left = QWidget()
        left.setStyleSheet("background: transparent;")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 4, 0)

        self._drafts_table = QTableWidget(0, 5)
        self._drafts_table.setHorizontalHeaderLabels([
            "Title", "Type", "Friction", "Status", "Created",
        ])
        self._drafts_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._drafts_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._drafts_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._drafts_table.setAlternatingRowColors(True)
        self._drafts_table.verticalHeader().setVisible(False)
        self._drafts_table.currentCellChanged.connect(self._on_draft_selected)
        left_layout.addWidget(self._drafts_table)
        splitter.addWidget(left)

        # Right: diff / preview
        right = QWidget()
        right.setStyleSheet("background: transparent;")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 0, 0, 0)

        self._draft_preview = QTextEdit()
        self._draft_preview.setReadOnly(True)
        self._draft_preview.setStyleSheet(
            f"background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER}; "
            f"border-radius: 8px; padding: 12px; font-size: 13px; "
            f"color: {ALMA_TEXT_DARK};"
        )
        right_layout.addWidget(self._draft_preview)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._reject_btn = QPushButton("Reject")
        self._reject_btn.setStyleSheet(_DANGER_BTN)
        self._reject_btn.clicked.connect(self._on_reject_draft)
        self._reject_btn.setEnabled(False)
        btn_row.addWidget(self._reject_btn)

        self._approve_btn = QPushButton("Approve && Push")
        self._approve_btn.setStyleSheet(_PRIMARY_BTN)
        self._approve_btn.clicked.connect(self._on_approve_draft)
        self._approve_btn.setEnabled(False)
        btn_row.addWidget(self._approve_btn)
        right_layout.addLayout(btn_row)

        splitter.addWidget(right)
        splitter.setSizes([300, 500])
        layout.addWidget(splitter)

        self._tabs.addTab(tab, "📝 Drafts")
        self._current_draft_id = None

    def _refresh_drafts_table(self):
        if not self._content_pipeline:
            return
        try:
            drafts = self._content_pipeline.get_all_drafts()
        except Exception:
            return

        self._drafts_table.setRowCount(len(drafts))
        for i, d in enumerate(drafts):
            self._drafts_table.setItem(
                i, 0, QTableWidgetItem(d["title"])
            )
            type_item = QTableWidgetItem(d["draft_type"])
            self._drafts_table.setItem(i, 1, type_item)
            self._drafts_table.setItem(
                i, 2, QTableWidgetItem(d["friction_type"])
            )

            status = d["status"]
            status_item = QTableWidgetItem(status.upper())
            if status == "pushed":
                status_item.setForeground(Qt.GlobalColor.darkGreen)
            elif status == "rejected":
                status_item.setForeground(Qt.GlobalColor.red)
            elif status == "pending":
                status_item.setForeground(Qt.GlobalColor.darkYellow)
            self._drafts_table.setItem(i, 3, status_item)

            created = d.get("created_at", "")[:10]
            self._drafts_table.setItem(i, 4, QTableWidgetItem(created))

            # Store draft ID in first column data
            title_item = self._drafts_table.item(i, 0)
            title_item.setData(Qt.ItemDataRole.UserRole, d["id"])

    def _on_draft_selected(self, row, col, prev_row, prev_col):
        if row < 0:
            return
        title_item = self._drafts_table.item(row, 0)
        if not title_item:
            return

        draft_id = title_item.data(Qt.ItemDataRole.UserRole)
        self._current_draft_id = draft_id

        status_item = self._drafts_table.item(row, 3)
        is_pending = status_item and status_item.text() == "PENDING"

        self._approve_btn.setEnabled(is_pending)
        self._reject_btn.setEnabled(is_pending)

        # Load content preview
        if self._content_pipeline:
            try:
                draft = self._content_pipeline._get_draft(draft_id)
                if draft:
                    self._draft_preview.setPlainText(draft["content"])
                    return
            except Exception:
                pass
        self._draft_preview.setPlainText("")

    def _on_approve_draft(self):
        if self._current_draft_id is None or not self._content_pipeline:
            return

        reply = QMessageBox.question(
            self, "Confirm Push to Guru",
            "This will update the live Guru knowledge base.\n\n"
            "Are you sure you want to push this draft?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            ok = self._content_pipeline.approve_and_push(
                self._current_draft_id
            )
            if ok:
                QMessageBox.information(
                    self, "Success", "Draft pushed to Guru successfully."
                )
            else:
                QMessageBox.warning(
                    self, "Failed", "Failed to push draft to Guru."
                )
            self._refresh_drafts_table()
        except Exception as exc:
            logger.warning("Approve failed: %s", exc)

    def _on_reject_draft(self):
        if self._current_draft_id is None or not self._content_pipeline:
            return

        try:
            self._content_pipeline.reject(self._current_draft_id)
            self._refresh_drafts_table()
        except Exception as exc:
            logger.warning("Reject failed: %s", exc)

    # ══════════════════════════════════════════════════════════════
    # Tab 4 — Effectiveness
    # ══════════════════════════════════════════════════════════════

    def _build_effectiveness_tab(self):
        tab = QWidget()
        tab.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 12, 0, 0)

        # Top bar
        top_bar = QHBoxLayout()
        top_bar.addStretch()

        measure_btn = QPushButton("Measure Effectiveness")
        measure_btn.setStyleSheet(_PRIMARY_BTN)
        measure_btn.clicked.connect(self._on_measure_effectiveness)
        top_bar.addWidget(measure_btn)
        layout.addLayout(top_bar)

        # Effectiveness table
        self._eff_table = QTableWidget(0, 7)
        self._eff_table.setHorizontalHeaderLabels([
            "Article", "Friction Type", "Pre Volume",
            "Post Volume", "Delta %", "Significant", "Measured",
        ])
        self._eff_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._eff_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._eff_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._eff_table.setAlternatingRowColors(True)
        self._eff_table.verticalHeader().setVisible(False)
        layout.addWidget(self._eff_table)

        # Minimum post-window label
        note = QLabel(
            "ℹ Effectiveness measurements require a minimum 14-day "
            "post-change window."
        )
        note.setStyleSheet(
            f"color: {ALMA_TEXT_LIGHT}; font-size: 11px; background: transparent;"
        )
        layout.addWidget(note)

        self._tabs.addTab(tab, "📊 Effectiveness")

    def _on_measure_effectiveness(self):
        if not self._effectiveness_tracker:
            self._show_not_connected()
            return

        try:
            results = self._effectiveness_tracker.measure_effectiveness()
            self._refresh_effectiveness_table()
            if results:
                QMessageBox.information(
                    self, "Measurement Complete",
                    f"{len(results)} effectiveness measurements updated.",
                )
            else:
                QMessageBox.information(
                    self, "No New Measurements",
                    "No pending measurements ready yet (minimum 14-day "
                    "post-change window).",
                )
        except Exception as exc:
            logger.warning("Effectiveness measurement failed: %s", exc)

    def _refresh_effectiveness_table(self):
        if not self._effectiveness_tracker:
            return
        try:
            report = self._effectiveness_tracker.get_effectiveness_report()
        except Exception:
            return

        self._eff_table.setRowCount(len(report))
        for i, row in enumerate(report):
            self._eff_table.setItem(
                i, 0, QTableWidgetItem(row["card_title"])
            )
            self._eff_table.setItem(
                i, 1, QTableWidgetItem(row["friction_type"])
            )
            self._eff_table.setItem(
                i, 2, QTableWidgetItem(f"{row['pre_volume']:.1f}")
            )
            self._eff_table.setItem(
                i, 3, QTableWidgetItem(f"{row['post_volume']:.1f}")
            )

            delta = row["delta_pct"]
            delta_item = QTableWidgetItem(f"{delta:+.1f}%")
            if delta < 0:
                delta_item.setForeground(Qt.GlobalColor.darkGreen)
            elif delta > 0:
                delta_item.setForeground(Qt.GlobalColor.red)
            self._eff_table.setItem(i, 4, delta_item)

            sig = "✓ Yes" if row["is_significant"] else "—"
            sig_item = QTableWidgetItem(sig)
            if row["is_significant"]:
                sig_item.setForeground(Qt.GlobalColor.darkGreen)
            self._eff_table.setItem(i, 5, sig_item)

            measured = row.get("measurement_date", "")[:10]
            self._eff_table.setItem(i, 6, QTableWidgetItem(measured))

    # ══════════════════════════════════════════════════════════════
    # Tab 5 — Connection
    # ══════════════════════════════════════════════════════════════

    def _build_connection_tab(self):
        tab = QWidget()
        tab.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(16)

        # Connection card
        card = QFrame()
        card.setStyleSheet(_CARD_STYLE)
        apply_card_shadow(card)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 20, 24, 20)
        card_layout.setSpacing(12)

        title = QLabel("Guru API Connection")
        title.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {ALMA_TEXT_DARK}; background: transparent;")
        card_layout.addWidget(title)

        # Email
        lbl_email = QLabel("EMAIL ADDRESS")
        lbl_email.setStyleSheet(_LBL_STYLE)
        card_layout.addWidget(lbl_email)
        self._guru_email = QLineEdit()
        self._guru_email.setPlaceholderText("user@company.com")
        self._guru_email.setStyleSheet(_FIELD_STYLE)
        card_layout.addWidget(self._guru_email)

        # API Token
        lbl_token = QLabel("API TOKEN")
        lbl_token.setStyleSheet(_LBL_STYLE)
        card_layout.addWidget(lbl_token)
        self._guru_token = QLineEdit()
        self._guru_token.setPlaceholderText("Guru API token")
        self._guru_token.setEchoMode(QLineEdit.EchoMode.Password)
        self._guru_token.setStyleSheet(_FIELD_STYLE)
        card_layout.addWidget(self._guru_token)

        # Buttons
        btn_row = QHBoxLayout()
        save_btn = QPushButton("Save Credentials")
        save_btn.setStyleSheet(_PRIMARY_BTN)
        save_btn.clicked.connect(self._on_save_credentials)
        btn_row.addWidget(save_btn)

        test_btn = QPushButton("Test Connection")
        test_btn.setStyleSheet(_GHOST_BTN)
        test_btn.clicked.connect(self._on_test_connection)
        btn_row.addWidget(test_btn)

        btn_row.addStretch()
        card_layout.addLayout(btn_row)

        # Status
        self._conn_status = QLabel("")
        self._conn_status.setStyleSheet(
            f"font-size: 12px; background: transparent;"
        )
        card_layout.addWidget(self._conn_status)

        layout.addWidget(card)

        # Sync card
        sync_card = QFrame()
        sync_card.setStyleSheet(_CARD_STYLE)
        apply_card_shadow_soft(sync_card)
        sync_layout = QVBoxLayout(sync_card)
        sync_layout.setContentsMargins(24, 20, 24, 20)
        sync_layout.setSpacing(12)

        sync_title = QLabel("Article Sync")
        sync_title.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        sync_title.setStyleSheet(f"color: {ALMA_TEXT_DARK}; background: transparent;")
        sync_layout.addWidget(sync_title)

        sync_btn_row = QHBoxLayout()
        sync_btn = QPushButton("Sync Articles")
        sync_btn.setStyleSheet(_PRIMARY_BTN)
        sync_btn.clicked.connect(self._on_sync_articles)
        sync_btn_row.addWidget(sync_btn)
        sync_btn_row.addStretch()

        self._sync_status = QLabel("")
        self._sync_status.setStyleSheet(
            f"font-size: 12px; color: {ALMA_TEXT_MID}; background: transparent;"
        )
        sync_btn_row.addWidget(self._sync_status)
        sync_layout.addLayout(sync_btn_row)

        # Last sync timestamp
        self._last_sync_label = QLabel("")
        self._last_sync_label.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; background: transparent;"
        )
        sync_layout.addWidget(self._last_sync_label)

        layout.addWidget(sync_card)
        layout.addStretch()

        self._tabs.addTab(tab, "⚙ Connection")

        # Load saved credentials
        self._load_saved_credentials()

    def _load_saved_credentials(self):
        try:
            from src.data.guru_client import GuruClient
            email, token = GuruClient.load_credentials()
            if email:
                self._guru_email.setText(email)
            if token:
                self._guru_token.setText(token)
        except Exception:
            pass

        # Show last sync time
        try:
            row = self.db.conn.execute(
                "SELECT MAX(last_synced_at) FROM guru_articles"
            ).fetchone()
            if row and row[0]:
                self._last_sync_label.setText(
                    f"Last synced: {row[0][:19]}"
                )
        except Exception:
            pass

    def _on_save_credentials(self):
        from src.data.guru_client import GuruClient
        email = self._guru_email.text().strip()
        token = self._guru_token.text().strip()

        if not email or not token:
            self._conn_status.setText("⚠ Both email and token are required")
            self._conn_status.setStyleSheet(
                f"color: {ALMA_WARNING}; font-size: 12px; background: transparent;"
            )
            return

        ok = GuruClient.save_credentials(email, token)
        if ok:
            self._conn_status.setText("✓ Credentials saved")
            self._conn_status.setStyleSheet(
                f"color: {ALMA_SUCCESS}; font-size: 12px; background: transparent;"
            )
            self.connection_changed.emit()
        else:
            self._conn_status.setText("✗ Failed to save credentials")
            self._conn_status.setStyleSheet(
                f"color: {ALMA_ERROR}; font-size: 12px; background: transparent;"
            )

    def _on_test_connection(self):
        email = self._guru_email.text().strip()
        token = self._guru_token.text().strip()

        if not email or not token:
            self._conn_status.setText("⚠ Enter credentials first")
            self._conn_status.setStyleSheet(
                f"color: {ALMA_WARNING}; font-size: 12px; background: transparent;"
            )
            return

        from src.data.guru_client import GuruClient
        client = GuruClient(email, token)
        ok = client.test_connection()

        if ok:
            self._conn_status.setText("✓ Connected to Guru")
            self._conn_status.setStyleSheet(
                f"color: {ALMA_SUCCESS}; font-size: 12px; background: transparent;"
            )
        else:
            self._conn_status.setText("✗ Connection failed — check credentials")
            self._conn_status.setStyleSheet(
                f"color: {ALMA_ERROR}; font-size: 12px; background: transparent;"
            )

    def _on_sync_articles(self):
        if not self._friction_pipeline:
            self._show_not_connected()
            return

        self._sync_status.setText("Syncing…")
        try:
            count = self._friction_pipeline.sync_articles()
            self._sync_status.setText(f"✓ Synced {count} articles")
            self._last_sync_label.setText(
                f"Last synced: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )
        except Exception as exc:
            self._sync_status.setText(f"✗ Sync failed: {exc}")
            logger.warning("Article sync failed: %s", exc)

    # ── Helpers ─────────────────────────────────────────────────

    def _show_not_connected(self):
        QMessageBox.information(
            self, "Not Connected",
            "Configure Guru credentials in the Connection tab first.",
        )
