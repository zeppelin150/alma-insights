"""
Alma Insights -- NLP Scanner Page (Pass 5.1)
3-tab coordinator: Scanner, Scanning Costs, SubTaxonomy & Findings.
Composes ScanMonitorWidget, CostDashboard, and TaxonomyBrowser widgets.
"""

import json
import logging
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QFrame, QScrollArea, QProgressBar, QSpinBox,
    QDoubleSpinBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QTabWidget, QSizePolicy, QApplication,
)
from PySide6.QtCore import Qt, QTimer, Signal

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_GREEN_SUBTLE, ALMA_GREEN_MID,
    ALMA_WHITE, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_TEXT_ON_DARK, ALMA_BORDER, ALMA_BORDER_LIGHT,
    ALMA_SUCCESS, ALMA_WARNING, ALMA_ERROR, ALMA_INFO,
    ALMA_BG_ELEVATED, apply_card_shadow, configure_table,
)
from src.ui.widgets.collapsible_section import CollapsibleSection
from src.ui.widgets.empty_state import EmptyState
from src.ui.widgets.date_picker import ModernDatePicker
from src.ui.widgets.scan_monitor import ScanMonitorWidget, ScanStatusPanel
from src.ui.widgets.cost_dashboard import CostDashboard
from src.ui.widgets.taxonomy_browser import TaxonomyBrowser

logger = logging.getLogger("alma.nlp_scanner_page")


class NLPScannerPage(QWidget):
    """NLP Scanner page — 3-tab coordinator (5.1).

    Tab 1 — Scanner: config, live monitor, scan history
    Tab 2 — Scanning Costs: cost limits, token usage, cost history
    Tab 3 — SubTaxonomy & Findings: health stats, taxonomy table, findings
    """

    # Emitted when user clicks "Deep Dive" on a finding
    deep_dive_requested = Signal(str, str)  # (finding_id, finding_title)
    # Emitted when user clicks "View Tickets" on a finding
    view_tickets_requested = Signal(list)  # ticket_ids
    # Emitted when scan starts (True) or ends (False) — blocks Gemini-dependent features
    scan_active_changed = Signal(bool)

    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db = db_manager
        self._scan_mgr = None
        self._poll_timer = None
        self._active_scan_id = None
        self._drilldown = None
        self._debug_mode = True  # default to debug until settings propagate
        self._build_ui()
        self._refresh_all()

    def is_scan_active(self):
        """Return True if an NLP scan is currently running."""
        return self._active_scan_id is not None

    def set_drilldown_panel(self, panel):
        """Called from main_window.py to wire the drilldown side panel."""
        self._drilldown = panel

    # ==============================================
    #  UI CONSTRUCTION
    # ==============================================

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Page header (outside tabs) ──
        header_area = QWidget()
        header_layout = QVBoxLayout(header_area)
        header_layout.setContentsMargins(28, 24, 28, 8)
        header_layout.setSpacing(4)

        header = QLabel("NLP Scanner")
        header.setObjectName("PageHeader")
        header_layout.addWidget(header)

        sub = QLabel("Full-population ticket classification via Gemini with learning sub-taxonomy")
        sub.setObjectName("PageSubheader")
        header_layout.addWidget(sub)

        outer.addWidget(header_area)

        # ── Tab widget ──
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

        # Tab 1: Scanner
        self._tabs.addTab(self._build_scanner_tab(), "Scanner")

        # Tab 2: Scanning Costs
        self._cost_dashboard = CostDashboard(self.db)
        self._tabs.addTab(self._cost_dashboard, "Scanning Costs")

        # Tab 3: SubTaxonomy & Findings
        self._taxonomy_browser = TaxonomyBrowser(self.db)
        self._taxonomy_browser.deep_dive_requested.connect(
            self.deep_dive_requested.emit
        )
        self._taxonomy_browser.view_tickets_requested.connect(
            self.view_tickets_requested.emit
        )
        self._taxonomy_browser.pattern_selected.connect(
            self._on_pattern_selected
        )
        self._tabs.addTab(self._taxonomy_browser, "SubTaxonomy && Findings")

        # Refresh tab content on switch
        self._tabs.currentChanged.connect(self._on_tab_changed)

        outer.addWidget(self._tabs, 1)

    # ------ Tab 1: Scanner ------

    def _build_scanner_tab(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 16, 28, 24)
        layout.setSpacing(16)

        # ── Debug-only: info panel ──
        self._info_panel = self._build_info_panel()
        layout.addWidget(self._info_panel)

        # ── Debug-only: full scan config card ──
        self._scan_config_card = self._build_scan_config_card()
        layout.addWidget(self._scan_config_card)

        # ── Debug-only: scan status panel (idle estimates / running 3-column) ──
        self._status_panel = ScanStatusPanel()
        layout.addWidget(self._status_panel)

        # ── Debug-only: control buttons row ──
        self._control_buttons_frame = self._build_control_buttons()
        layout.addWidget(self._control_buttons_frame)

        # ── Debug-only: live scan monitor (hidden until scan starts) ──
        self._scan_monitor = ScanMonitorWidget(self.db)
        self._scan_monitor.scan_completed.connect(self._on_scan_completed)
        layout.addWidget(self._scan_monitor)

        # ── Production: simple scan card (shown when debug OFF) ──
        self._simple_scan_card = self._build_simple_scan_card()
        self._simple_scan_card.setVisible(False)  # default hidden; update_debug_mode toggles
        layout.addWidget(self._simple_scan_card)

        # ── Always visible: scan history table ──
        layout.addWidget(self._build_history_section())

        # ── Debug-only: docs panel ──
        self._docs_section = self._build_docs_panel()
        layout.addWidget(self._docs_section)

        layout.addStretch()

        scroll.setWidget(content)
        return scroll

    # ------ Shared styles ------

    def _card_frame(self):
        f = QFrame()
        f.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED}; border: 1px solid rgba(214, 210, 202, 0.45);
                border-radius: 12px;
            }}
        """)
        apply_card_shadow(f)
        return f

    _LBL_STYLE = f"font-size: 11px; font-weight: 600; color: {ALMA_TEXT_MID}; letter-spacing: 0.5px;"
    _FIELD_STYLE = f"""
        QComboBox, QDateEdit, QSpinBox, QDoubleSpinBox {{
            color: {ALMA_TEXT_DARK}; background: {ALMA_WHITE};
            border: 1px solid {ALMA_BORDER}; border-radius: 8px;
            padding: 8px 12px; font-size: 13px;
        }}
    """

    # ==============================================
    #  INFO & DOCS PANELS
    # ==============================================

    def _build_info_panel(self):
        """What is NLP Scanning? — brief intro panel."""
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background: rgba(20, 87, 63, 0.04);
                border: 1px solid rgba(20, 87, 63, 0.15);
                border-radius: 10px;
            }}
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
            "classification — friction type, anomaly category, sentiment polarity, "
            "root cause, and sub-pattern assignment. Results power the Findings, "
            "Sub-Taxonomy, and AI Reports features. Scans run as background "
            "processes that survive app close."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_MID}; line-height: 1.4;")
        cl.addWidget(desc)

        return card

    def _build_docs_panel(self):
        """How Scanning Works + Capacity & Limits — collapsible docs."""
        section = CollapsibleSection(
            "How Scanning Works & Limits", initially_collapsed=True,
            section_key="nlp_scanner.docs"
        )

        container = QWidget()
        cl = QVBoxLayout(container)
        cl.setContentsMargins(8, 8, 8, 8)
        cl.setSpacing(12)

        # How it works
        how_title = QLabel("How Scanning Works")
        how_title.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {ALMA_TEXT_DARK};")
        cl.addWidget(how_title)

        how_text = QLabel(
            "1. Tickets are grouped by TRC code and split into batches (~700 tickets each).\n"
            "2. Agentic pipeline boots: bridge → supervisor → workers → rate governor.\n"
            "3. Workers send batches to Gemini for classification: friction type, anomaly "
            "category, polarity, root cause, sub-pattern.\n"
            "4. Results are stored in SQLite. After all batches complete, meta-analysis "
            "identifies cross-TRC findings.\n"
            "5. Workers are detached processes — they continue running even if you close "
            "the app.\n"
            "6. All communication is via SQLite (WAL mode). No network server needed."
        )
        how_text.setWordWrap(True)
        how_text.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_MID}; line-height: 1.5;")
        cl.addWidget(how_text)

        # Capacity & Limits
        cap_title = QLabel("Capacity & Limits")
        cap_title.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {ALMA_TEXT_DARK};")
        cl.addWidget(cap_title)

        cap_text = QLabel(
            "- CLI mode limit: 50,000 tickets per scan\n"
            "- Parallel workers: 1-3 (each claims batches independently)\n"
            "- Budget cap: pauses scan when Gemini cost reaches the limit\n"
            "- Batch retry: failed batches retry up to 3 times\n"
            "- Gemini 2.5 Flash pricing: $0.15/M input + $0.60/M output tokens\n"
            "- Typical cost: ~$0.01 per 100 tickets\n"
            "- Typical speed: ~90 seconds per batch (700 tickets)"
        )
        cap_text.setWordWrap(True)
        cap_text.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_MID}; line-height: 1.5;")
        cl.addWidget(cap_text)

        section.add_widget(container)
        return section

    # ==============================================
    #  SCAN CONFIG CARD
    # ==============================================

    def _build_scan_config_card(self):
        card = self._card_frame()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(10)

        # Title row with mode badge
        title_row = QHBoxLayout()
        title = QLabel("New Scan")
        title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK};")
        title_row.addWidget(title)

        self._mode_badge = QLabel("AGENTIC")
        self._mode_badge.setStyleSheet(
            f"background: {ALMA_GREEN_DARK}; color: {ALMA_CREAM}; "
            "border-radius: 4px; padding: 2px 8px; font-size: 10px; "
            "font-weight: 700; letter-spacing: 0.5px;"
        )
        self._mode_badge.setFixedHeight(20)
        self._mode_badge.setToolTip(
            "Agentic pipeline (5.0): persistent Gemini workers, supervisor, "
            "rate governor. Workers are detached subprocesses."
        )
        title_row.addWidget(self._mode_badge)
        title_row.addStretch()
        cl.addLayout(title_row)

        # Row 1: Date range + TRC filter
        row1 = QHBoxLayout()

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("DATE RANGE")
        lbl.setStyleSheet(self._LBL_STYLE)
        col.addWidget(lbl)
        date_row = QHBoxLayout()
        self._date_start = ModernDatePicker()
        self._date_end = ModernDatePicker()
        self._date_start.setStyleSheet(self._FIELD_STYLE)
        self._date_end.setStyleSheet(self._FIELD_STYLE)
        date_row.addWidget(self._date_start)
        date_row.addWidget(QLabel("to"))
        date_row.addWidget(self._date_end)
        col.addLayout(date_row)
        row1.addLayout(col, 3)

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("TRC FILTER")
        lbl.setStyleSheet(self._LBL_STYLE)
        col.addWidget(lbl)
        self._trc_combo = QComboBox()
        self._trc_combo.setStyleSheet(self._FIELD_STYLE)
        self._trc_combo.addItem("All TRCs", None)
        col.addWidget(self._trc_combo)
        row1.addLayout(col, 2)
        cl.addLayout(row1)

        # Row 2: Budget cap + Workers (batch sizing is automatic via BatchPacker)
        row2 = QHBoxLayout()

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("BUDGET CAP ($)")
        lbl.setStyleSheet(self._LBL_STYLE)
        col.addWidget(lbl)
        self._budget_cap = QDoubleSpinBox()
        self._budget_cap.setRange(1.0, 500.0)
        self._budget_cap.setValue(50.0)
        self._budget_cap.setPrefix("$")
        self._budget_cap.setDecimals(2)
        self._budget_cap.setStyleSheet(self._FIELD_STYLE)
        col.addWidget(self._budget_cap)
        row2.addLayout(col, 1)

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("WORKERS")
        lbl.setStyleSheet(self._LBL_STYLE)
        col.addWidget(lbl)
        self._workers_spin = QSpinBox()
        self._workers_spin.setRange(1, 3)
        self._workers_spin.setValue(3)
        self._workers_spin.setToolTip(
            "Parallel worker subprocesses (1-3). Each worker claims "
            "and processes batches independently."
        )
        self._workers_spin.setStyleSheet(self._FIELD_STYLE)
        col.addWidget(self._workers_spin)
        row2.addLayout(col, 1)

        row2.addStretch(2)
        cl.addLayout(row2)

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

        self._est_label = QLabel("")
        self._est_label.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID}; padding: 2px 0;")
        est_layout.addWidget(self._est_label)

        self._bg_info_label = QLabel(
            "Scan runs in the background as a detached process. "
            "You can close the app and workers will continue."
        )
        self._bg_info_label.setWordWrap(True)
        self._bg_info_label.setStyleSheet(
            f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; font-style: italic;"
        )
        est_layout.addWidget(self._bg_info_label)

        cl.addWidget(est_frame)

        # Connect date change to cost estimate update
        self._date_start.date_changed.connect(self._update_cost_estimate)
        self._date_end.date_changed.connect(self._update_cost_estimate)
        self._workers_spin.valueChanged.connect(self._update_cost_estimate)

        return card

    # ==============================================
    #  CONTROL BUTTONS
    # ==============================================

    def _build_control_buttons(self):
        container = QWidget()
        btn_layout = QHBoxLayout(container)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(10)

        # Start
        self._start_btn = QPushButton("Start Scan")
        self._start_btn.setCursor(Qt.PointingHandCursor)
        self._start_btn.setMinimumHeight(40)
        self._start_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_TEXT_ON_DARK};
                border: none; border-radius: 8px; font-weight: 700;
                font-size: 14px; padding: 10px 24px;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
            QPushButton:disabled {{ background: {ALMA_BORDER}; color: {ALMA_TEXT_LIGHT}; }}
        """)
        self._start_btn.clicked.connect(self._start_scan)
        btn_layout.addWidget(self._start_btn)

        # Pause
        self._pause_btn = QPushButton("Pause")
        self._pause_btn.setCursor(Qt.PointingHandCursor)
        self._pause_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_WARNING}; color: white; border: none;
                border-radius: 8px; padding: 10px 20px; font-weight: 600;
                font-size: 13px;
            }}
            QPushButton:hover {{ background: #E0A800; }}
        """)
        self._pause_btn.clicked.connect(self._pause_scan)
        self._pause_btn.setVisible(False)
        btn_layout.addWidget(self._pause_btn)

        # Resume
        self._resume_btn = QPushButton("Resume")
        self._resume_btn.setCursor(Qt.PointingHandCursor)
        self._resume_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_SUCCESS}; color: white; border: none;
                border-radius: 8px; padding: 10px 20px; font-weight: 600;
                font-size: 13px;
            }}
            QPushButton:hover {{ background: #1B8A2A; }}
        """)
        self._resume_btn.clicked.connect(self._resume_scan)
        self._resume_btn.setVisible(False)
        btn_layout.addWidget(self._resume_btn)

        # Cancel
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setCursor(Qt.PointingHandCursor)
        self._cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_ERROR}; color: white; border: none;
                border-radius: 8px; padding: 10px 20px; font-weight: 600;
                font-size: 13px;
            }}
            QPushButton:hover {{ background: #B71C1C; }}
        """)
        self._cancel_btn.clicked.connect(self._cancel_scan)
        self._cancel_btn.setVisible(False)
        btn_layout.addWidget(self._cancel_btn)

        # Retry Failed Batches
        self._retry_failed_btn = QPushButton("Retry Failed Batches")
        self._retry_failed_btn.setCursor(Qt.PointingHandCursor)
        self._retry_failed_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_WARNING}; color: white; border: none;
                border-radius: 8px; padding: 10px 20px; font-weight: 600;
                font-size: 13px;
            }}
            QPushButton:hover {{ background: #E67E00; }}
        """)
        self._retry_failed_btn.clicked.connect(self._retry_failed_batches)
        self._retry_failed_btn.setVisible(False)
        btn_layout.addWidget(self._retry_failed_btn)

        btn_layout.addStretch()
        return container

    # ==============================================
    #  SCAN HISTORY
    # ==============================================

    def _build_history_section(self):
        self._history_section = CollapsibleSection(
            "Scan History", initially_collapsed=True,
            section_key="nlp_scanner.history"
        )

        container = QWidget()
        cl = QVBoxLayout(container)
        cl.setContentsMargins(8, 8, 8, 8)
        cl.setSpacing(6)

        self._history_table = QTableWidget(0, 6)
        self._history_table.setHorizontalHeaderLabels([
            "Date", "Range", "Tickets", "Cost", "Findings", "Status"
        ])
        self._history_table.horizontalHeader().setStretchLastSection(True)
        self._history_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._history_table.verticalHeader().setVisible(False)
        configure_table(self._history_table)
        self._history_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._history_table.setAlternatingRowColors(True)
        self._history_table.setMaximumHeight(250)
        self._history_table.setStyleSheet(f"""
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
            QTableWidget::item {{
                padding: 4px 8px;
            }}
            QTableWidget::item:alternate {{
                background: rgba(20, 87, 63, 0.02);
            }}
            QScrollBar:vertical {{
                width: 6px; background: transparent;
            }}
            QScrollBar::handle:vertical {{
                background: {ALMA_BORDER}; border-radius: 3px; min-height: 30px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
            QScrollBar:horizontal {{
                height: 0;
            }}
            QTableCornerButton::section {{
                background: {ALMA_CREAM}; border: none;
                border-bottom: 1px solid {ALMA_BORDER_LIGHT};
                color: {ALMA_TEXT_MID};
            }}
        """)
        cl.addWidget(self._history_table)

        self._history_section.add_widget(container)
        return self._history_section

    def _refresh_history(self):
        """Populate scan history table."""
        try:
            scans = self.db.get_scan_history(limit=20)
            self._history_table.setRowCount(len(scans))

            for i, s in enumerate(scans):
                date_str = s.get("created_at", "")[:10]
                range_str = f"{s.get('date_range_start', '')[:10]} to {s.get('date_range_end', '')[:10]}"
                tickets = f"{s.get('total_tickets', 0):,}"
                cost = f"${s.get('actual_cost_usd', 0):.2f}"

                # Count findings
                findings_count = len(self.db.get_scan_findings(s["scan_id"], limit=100))

                status = s.get("status", "unknown")

                self._history_table.setItem(i, 0, QTableWidgetItem(date_str))
                self._history_table.setItem(i, 1, QTableWidgetItem(range_str))
                self._history_table.setItem(i, 2, QTableWidgetItem(tickets))
                self._history_table.setItem(i, 3, QTableWidgetItem(cost))
                self._history_table.setItem(i, 4, QTableWidgetItem(str(findings_count)))

                # User-friendly status labels
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
                    from PySide6.QtGui import QColor
                    status_item.setForeground(QColor(ALMA_WARNING))
                elif status in ("failed", "cancelled", "budget_exceeded",
                                "quota_exhausted"):
                    status_item.setForeground(Qt.red)
                elif status in ("running", "paused"):
                    from PySide6.QtGui import QColor
                    status_item.setForeground(QColor(ALMA_WARNING))
                self._history_table.setItem(i, 5, status_item)
        except Exception:
            pass

    # ==============================================
    #  SIMPLE SCAN CARD (Production Mode)
    # ==============================================

    def _build_simple_scan_card(self):
        """Production scan UI — date range, TRC filter, start/cancel, progress."""
        card = self._card_frame()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(20, 16, 20, 16)
        cl.setSpacing(12)

        # Title
        title = QLabel("NLP Scan")
        title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK};")
        cl.addWidget(title)

        desc = QLabel(
            "Classify tickets in the selected range using Gemini. "
            "Results power SubTaxonomy, Findings, and AI Reports."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_MID}; line-height: 1.4;")
        cl.addWidget(desc)

        # ── Date range row ──
        date_row = QHBoxLayout()
        date_row.setSpacing(8)

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("DATE RANGE")
        lbl.setStyleSheet(self._LBL_STYLE)
        col.addWidget(lbl)
        dr = QHBoxLayout()
        self._simple_date_start = ModernDatePicker()
        self._simple_date_end = ModernDatePicker()
        self._simple_date_start.setStyleSheet(self._FIELD_STYLE)
        self._simple_date_end.setStyleSheet(self._FIELD_STYLE)
        dr.addWidget(self._simple_date_start)
        to_lbl = QLabel("to")
        to_lbl.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID};")
        dr.addWidget(to_lbl)
        dr.addWidget(self._simple_date_end)
        col.addLayout(dr)
        date_row.addLayout(col, 3)

        # ── TRC filter ──
        col2 = QVBoxLayout()
        col2.setSpacing(4)
        trc_lbl = QLabel("TRC FILTER")
        trc_lbl.setStyleSheet(self._LBL_STYLE)
        col2.addWidget(trc_lbl)
        self._simple_trc_combo = QComboBox()
        self._simple_trc_combo.setStyleSheet(self._FIELD_STYLE)
        self._simple_trc_combo.addItem("All TRCs", None)
        col2.addWidget(self._simple_trc_combo)
        date_row.addLayout(col2, 2)

        cl.addLayout(date_row)

        # Last scan info
        self._simple_last_scan_lbl = QLabel("")
        self._simple_last_scan_lbl.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; padding: 2px 0;"
        )
        cl.addWidget(self._simple_last_scan_lbl)

        # ── Start + Cancel buttons ──
        row = QHBoxLayout()
        self._simple_start_btn = QPushButton("Start Scan")
        self._simple_start_btn.setCursor(Qt.PointingHandCursor)
        self._simple_start_btn.setMinimumHeight(38)
        self._simple_start_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_TEXT_ON_DARK};
                border: none; border-radius: 8px; font-weight: 700;
                font-size: 13px; padding: 8px 24px;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
            QPushButton:disabled {{ background: {ALMA_BORDER}; color: {ALMA_TEXT_LIGHT}; }}
        """)
        self._simple_start_btn.clicked.connect(self._start_simple_scan)
        row.addWidget(self._simple_start_btn)

        self._simple_cancel_btn = QPushButton("Cancel")
        self._simple_cancel_btn.setCursor(Qt.PointingHandCursor)
        self._simple_cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_ERROR}; color: white; border: none;
                border-radius: 8px; padding: 8px 16px; font-weight: 600;
                font-size: 12px;
            }}
            QPushButton:hover {{ background: #B71C1C; }}
        """)
        self._simple_cancel_btn.clicked.connect(self._cancel_scan)
        self._simple_cancel_btn.setVisible(False)
        row.addWidget(self._simple_cancel_btn)

        row.addStretch()
        cl.addLayout(row)

        # ── Progress bar + label (hidden until scan starts) ──
        self._simple_progress_bar = QProgressBar()
        self._simple_progress_bar.setRange(0, 100)
        self._simple_progress_bar.setValue(0)
        self._simple_progress_bar.setFixedHeight(14)
        self._simple_progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background: {ALMA_CREAM}; border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 7px; text-align: center; font-size: 9px;
                color: {ALMA_TEXT_MID};
            }}
            QProgressBar::chunk {{
                background: {ALMA_GREEN_DARK}; border-radius: 6px;
            }}
        """)
        self._simple_progress_bar.setVisible(False)
        cl.addWidget(self._simple_progress_bar)

        self._simple_progress_lbl = QLabel("")
        self._simple_progress_lbl.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_MID};"
        )
        self._simple_progress_lbl.setVisible(False)
        cl.addWidget(self._simple_progress_lbl)

        return card

    def _refresh_simple_last_scan(self):
        """Update the 'last scan' label and sync date/TRC pickers in simple card."""
        try:
            scans = self.db.get_scan_history(limit=1)
            if scans:
                s = scans[0]
                date_str = s.get("created_at", "")[:10]
                tickets = s.get("total_tickets", 0)
                status = s.get("status", "unknown")
                status_display = {
                    'scan_complete': 'completed',
                    'analysis_complete': 'completed',
                }.get(status, status)
                self._simple_last_scan_lbl.setText(
                    f"Last scan: {date_str}  \u2022  "
                    f"{tickets:,} tickets  \u2022  {status_display}"
                )
            else:
                self._simple_last_scan_lbl.setText("No scans yet")
        except Exception:
            self._simple_last_scan_lbl.setText("")

        # Sync date pickers to data range
        self._sync_simple_date_range()
        # Populate TRC combo
        self._populate_simple_trc_combo()

    def _sync_simple_date_range(self):
        """Sync simple card date pickers to actual data range."""
        try:
            from PySide6.QtCore import QDate
            row = self.db.conn.execute(
                "SELECT MIN(created_at) AS mn, MAX(created_at) AS mx FROM conversations"
            ).fetchone()
            if row and row["mn"] and row["mx"]:
                mn = row["mn"][:10].split("-")
                mx = row["mx"][:10].split("-")
                if len(mn) == 3 and len(mx) == 3:
                    self._simple_date_start.set_date(
                        QDate(int(mn[0]), int(mn[1]), int(mn[2]))
                    )
                    self._simple_date_end.set_date(
                        QDate(int(mx[0]), int(mx[1]), int(mx[2]))
                    )
        except Exception:
            pass

    def _populate_simple_trc_combo(self):
        """Populate simple card TRC filter dropdown."""
        current = self._simple_trc_combo.currentData()
        self._simple_trc_combo.clear()
        self._simple_trc_combo.addItem("All TRCs", None)
        try:
            trcs = self.db.get_trc_list()
            for trc in trcs:
                self._simple_trc_combo.addItem(trc, trc)
        except Exception:
            pass

    # ==============================================
    #  DEBUG MODE GATING
    # ==============================================

    def update_debug_mode(self, enabled: bool):
        """Show/hide debug-only scan controls based on settings toggle."""
        self._debug_mode = enabled

        # Debug-only sections
        self._info_panel.setVisible(enabled)
        self._scan_config_card.setVisible(enabled)
        self._status_panel.setVisible(enabled)
        self._control_buttons_frame.setVisible(enabled)
        self._scan_monitor.setVisible(enabled and self._active_scan_id is not None)
        self._docs_section.setVisible(enabled)

        # Production simple card — shown when debug OFF
        self._simple_scan_card.setVisible(not enabled)
        if not enabled:
            self._refresh_simple_last_scan()

    # ==============================================
    #  SIMPLE SCAN (Production Mode — via JobQueue)
    # ==============================================

    def _start_simple_scan(self):
        """Start scan via job queue (production mode).

        Reads date range + TRC from simple card pickers on the main thread,
        then passes them through the job factory closure to avoid cross-thread
        DB access in the worker.
        """
        try:
            # Read values from UI (main thread — safe)
            ds = self._simple_date_start.get_date_string()
            de = self._simple_date_end.get_date_string()
            if not ds or not de:
                self._simple_progress_lbl.setVisible(True)
                self._simple_progress_lbl.setText("Select a date range first")
                return

            trc_data = self._simple_trc_combo.currentData()
            trc_filter = [trc_data] if trc_data else None

            # Store scan params for _run_scan_blocking (read on worker thread)
            self._pending_scan_params = {
                "date_start": ds,
                "date_end": de,
                "trc_filter": trc_filter,
            }

            main_win = self.window()
            if hasattr(main_win, '_job_queue') and hasattr(main_win, '_make_nlp_scan_job'):
                self._ensure_scan_manager()
                job = main_win._make_nlp_scan_job()
                main_win._job_queue.submit(job)
                self.scan_active_changed.emit(True)

                # Update simple card UI
                self._simple_start_btn.setEnabled(False)
                self._simple_cancel_btn.setVisible(True)
                self._simple_progress_bar.setVisible(True)
                self._simple_progress_bar.setValue(0)
                self._simple_progress_lbl.setVisible(True)
                self._simple_progress_lbl.setText("Queued...")

                # Start polling for progress updates
                self._poll_wait_count = 0
                self._overlay_model_name = None  # re-read on next poll
                self._start_polling()
            else:
                # Fallback: direct launch (same as debug mode)
                self._start_scan()
        except Exception as e:
            logger.error(f"Simple scan failed: {e}")
            self._simple_progress_lbl.setVisible(True)
            self._simple_progress_lbl.setText(f"Error: {e}")

    def _run_scan_blocking(self):
        """Synchronous scan wrapper for JobQueue execution.

        Called by CallableWorker on a background thread.
        Reads scan parameters from self._pending_scan_params (set on main
        thread by _start_simple_scan) to avoid cross-thread DB access.
        Returns the terminal status string.
        """
        import time
        import yaml
        from pathlib import Path

        self._ensure_scan_manager()

        # Read settings (yaml is thread-safe file I/O)
        with open(Path("config/settings.yaml")) as f:
            cfg = yaml.safe_load(f) or {}
        nlp_cfg = cfg.get("nlp_scan", {})

        # Get params set by _start_simple_scan on the main thread
        params = getattr(self, '_pending_scan_params', {})
        ds = params.get("date_start", "")
        de = params.get("date_end", "")
        trc_filter = params.get("trc_filter", None)

        if not ds or not de:
            return "failed"

        result = self._scan_mgr.start_scan(
            date_start=ds,
            date_end=de,
            trc_filter=trc_filter,
            budget_cap=nlp_cfg.get("budget_cap", 50.0),
            parallel_workers=nlp_cfg.get("parallel_workers", 3),
        )

        if "error" in result:
            return "failed"

        self._active_scan_id = result.get("scan_id")

        # Poll until terminal
        terminal = ("completed", "analysis_complete", "scan_complete",
                     "failed", "cancelled", "budget_exceeded", "quota_exhausted")
        while True:
            try:
                status = self._scan_mgr.get_status(self._active_scan_id)
                scan_status = status.get("status", "") if isinstance(status, dict) else ""
                if scan_status in terminal:
                    return scan_status
            except Exception:
                pass
            time.sleep(5)

    def _on_job_scan_complete(self, result):
        """Handle scan completion from JobQueue worker (main thread)."""
        self._active_scan_id = None
        self._cost_dashboard.set_active_scan(None)
        self.scan_active_changed.emit(False)
        self._stop_polling()

        # Reset simple card
        self._simple_start_btn.setEnabled(True)
        self._simple_cancel_btn.setVisible(False)
        self._simple_progress_bar.setValue(100)
        self._simple_progress_lbl.setText("Scan complete")

        # Run post-scan analysis and refresh
        self._run_post_scan_analysis()
        self._refresh_simple_last_scan()
        self._refresh_history()

    def _on_job_scan_error(self, error_msg):
        """Handle scan error from JobQueue worker (main thread)."""
        self._active_scan_id = None
        self.scan_active_changed.emit(False)
        self._stop_polling()

        # Reset simple card
        self._simple_start_btn.setEnabled(True)
        self._simple_cancel_btn.setVisible(False)
        self._simple_progress_bar.setVisible(False)
        self._simple_progress_lbl.setText(f"Scan failed: {error_msg[:100]}")
        self._simple_progress_lbl.setStyleSheet(
            f"font-size: 11px; color: {ALMA_ERROR};"
        )
        logger.error(f"Job scan error: {error_msg}")

    def _update_simple_progress(self):
        """Update simple scan card progress display AND job overlay (called from poll timer)."""
        if not self._active_scan_id:
            return

        try:
            status = self._scan_mgr.get_status(self._active_scan_id)
            if not isinstance(status, dict) or "error" in status:
                return

            completed = status.get("completed_batches", 0) or 0
            total = status.get("total_batches", 1) or 1
            pct = int(completed / total * 100) if total > 0 else 0
            classified = status.get("classified_tickets", 0) or 0
            total_tix = status.get("total_tickets", 0) or 0

            # ── Time estimate ──
            time_str = ""
            try:
                created_at = status.get("created_at", "")
                if created_at and completed > 0:
                    from datetime import datetime
                    start_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    elapsed = (datetime.now(start_dt.tzinfo)
                               if start_dt.tzinfo
                               else datetime.now()) - start_dt
                    elapsed_secs = max(elapsed.total_seconds(), 1)
                    est_total_secs = elapsed_secs * total / completed
                    remaining_secs = max(est_total_secs - elapsed_secs, 0)
                    remaining_min = int(remaining_secs / 60)
                    if remaining_min >= 1:
                        time_str = f"  \u2022  ~{remaining_min} min remaining"
                    else:
                        time_str = "  \u2022  finishing up"
                elif completed == 0:
                    time_str = "  \u2022  starting"
            except Exception:
                pass

            progress_text = (
                f"{classified:,}/{total_tix:,} tickets  \u2022  "
                f"{completed}/{total} batches  \u2022  {pct}%{time_str}"
            )

            # ── Update simple scan card (if visible) ──
            if hasattr(self, '_simple_progress_bar') and self._simple_scan_card.isVisible():
                self._simple_progress_bar.setValue(pct)
                self._simple_progress_lbl.setText(progress_text)

            # ── Update job overlay with live progress ──
            main_win = self.window()
            if hasattr(main_win, '_job_overlay') and main_win._job_overlay.isVisible():
                # Read model for display
                model = getattr(self, '_overlay_model_name', None)
                if model is None:
                    try:
                        import yaml
                        from pathlib import Path
                        cfg_path = Path("config/settings.yaml")
                        with open(cfg_path) as f:
                            cfg = yaml.safe_load(f) or {}
                        model = cfg.get("gemini", {}).get("model", "gemini")
                        self._overlay_model_name = model
                    except Exception:
                        model = "gemini"
                        self._overlay_model_name = model

                overlay_text = (
                    f"Classifying tickets with {model}  \u2022  "
                    f"{pct}%{time_str}"
                )
                main_win._job_overlay.update_status(overlay_text)

        except Exception:
            pass

    # ==============================================
    #  ACTIONS
    # ==============================================

    def _update_cost_estimate(self):
        """Update cost estimate when date range or config changes.

        Uses Gemini 2.5 Flash pricing: $0.15/M input, $0.60/M output.
        Enforces 50K CLI ticket cap.
        """
        try:
            ds = self._date_start.get_date_string()
            de = self._date_end.get_date_string()
            if not ds or not de:
                return

            ticket_count = self.db.get_ticket_count_in_range(ds, de)
            # Batch sizing: ~20 tickets per batch (output token safe)
            batches = max(1, (ticket_count + 19) // 20) if ticket_count else 0

            # Gemini 2.0 Flash: $0.10/M input, $0.40/M output
            input_tokens = ticket_count * 600
            output_tokens = ticket_count * 200
            cost = ((input_tokens / 1e6) * 0.10
                    + (output_tokens / 1e6) * 0.40)

            # Estimate time: ~10 seconds per batch / workers
            n_workers = self._workers_spin.value()
            est_seconds = (batches * 10) / max(n_workers, 1)
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
                self._est_label.setStyleSheet(
                    f"font-size: 12px; color: {ALMA_ERROR}; padding: 4px 0; font-weight: 600;"
                )
                self._start_btn.setEnabled(False)
            else:
                self._est_label.setStyleSheet(
                    f"font-size: 12px; color: {ALMA_TEXT_MID}; padding: 4px 0;"
                )
                self._start_btn.setEnabled(True)

            self._est_label.setText(est_text)

            # Update status panel idle estimates
            self._status_panel.set_estimates(
                tickets=ticket_count,
                time_min=est_min,
                tokens=input_tokens + output_tokens,
                cost=cost,
            )
        except Exception:
            self._est_label.setText("")

    def _start_scan(self):
        """Start a new NLP scan."""
        try:
            self._ensure_scan_manager()

            ds = self._date_start.get_date_string()
            de = self._date_end.get_date_string()
            if not ds or not de:
                self._est_label.setText("Select a date range first")
                return

            # TRC filter
            trc_filter = None
            trc_data = self._trc_combo.currentData()
            if trc_data:
                trc_filter = [trc_data]

            result = self._scan_mgr.start_scan(
                date_start=ds,
                date_end=de,
                trc_filter=trc_filter,
                budget_cap=self._budget_cap.value(),
                parallel_workers=self._workers_spin.value(),
            )

            if "error" in result:
                self._est_label.setText(f"Error: {result['error']}")
                return

            self._active_scan_id = result.get("scan_id")
            self._cost_dashboard.set_active_scan(self._active_scan_id)
            self.scan_active_changed.emit(True)

            # Show running state
            self._start_btn.setEnabled(False)
            self._pause_btn.setVisible(True)
            self._cancel_btn.setVisible(True)

            # Switch status panel to running mode
            self._status_panel.show_running()

            # Start the live monitor
            self._scan_monitor.start_monitoring(self._active_scan_id)

            # Start polling for scan status
            self._start_polling()

            est_time = result.get("estimated_time_minutes", 0)
            workers = result.get("workers_launched", 1)
            logger.info(
                f"Scan started | {result.get('total_batches', 0)} batches | "
                f"{result.get('total_tickets', 0):,} tickets | "
                f"~{est_time:.0f} min | {workers} worker(s)"
            )

        except Exception as e:
            self._est_label.setText(f"Error: {e}")
            logger.error(f"Start scan failed: {e}")

    def _pause_scan(self):
        if not self._active_scan_id or not self._scan_mgr:
            return
        try:
            self._scan_mgr.pause_scan(self._active_scan_id)
            self._pause_btn.setVisible(False)
            self._resume_btn.setVisible(True)
        except Exception as e:
            logger.error(f"Pause failed: {e}")

    def _resume_scan(self):
        if not self._active_scan_id or not self._scan_mgr:
            return
        try:
            self._scan_mgr.resume_scan(
                self._active_scan_id,
                budget_cap=self._budget_cap.value()
            )
            self._pause_btn.setVisible(True)
            self._resume_btn.setVisible(False)
            self._start_polling()
        except Exception as e:
            logger.error(f"Resume failed: {e}")

    def _cancel_scan(self):
        if not self._active_scan_id or not self._scan_mgr:
            return
        try:
            self._scan_mgr.cancel_scan(self._active_scan_id)
            self._stop_polling()
            self._scan_monitor.stop_monitoring()
            self._active_scan_id = None
            self.scan_active_changed.emit(False)

            # Reset button visibility
            self._start_btn.setEnabled(True)
            self._pause_btn.setVisible(False)
            self._resume_btn.setVisible(False)
            self._cancel_btn.setVisible(False)
            self._retry_failed_btn.setVisible(False)
            self._status_panel.show_idle()
        except Exception as e:
            logger.error(f"Cancel failed: {e}")

    def _retry_failed_batches(self):
        """Retry only the failed batches from the last scan."""
        if not self._active_scan_id or not self._scan_mgr:
            return
        try:
            self._retry_failed_btn.setVisible(False)
            self._start_btn.setEnabled(False)
            self._cancel_btn.setVisible(True)
            self.scan_active_changed.emit(True)

            import threading
            t = threading.Thread(
                target=self._scan_mgr.retry_failed_batches,
                args=(self._active_scan_id,),
                daemon=True,
            )
            t.start()
            self._start_polling()
        except Exception as e:
            logger.error(f"Retry failed batches: {e}")
            self._retry_failed_btn.setVisible(True)
            self._start_btn.setEnabled(True)
            self._cancel_btn.setVisible(False)

    # ==============================================
    #  POLLING
    # ==============================================

    def _start_polling(self):
        if self._poll_timer:
            self._poll_timer.stop()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_status)
        self._poll_timer.start(5000)  # Every 5 seconds

    def _stop_polling(self):
        if self._poll_timer:
            self._poll_timer.stop()
            self._poll_timer = None

    def _poll_status(self):
        """Poll SQLite for active scan status (CLI mode — no server)."""
        if not self._scan_mgr:
            self._stop_polling()
            return

        # Worker thread may not have set _active_scan_id yet — wait
        # up to 60 seconds before giving up (12 polls × 5s interval).
        if not self._active_scan_id:
            self._poll_wait_count = getattr(self, '_poll_wait_count', 0) + 1
            if self._poll_wait_count > 12:
                self._stop_polling()
            return

        self._poll_wait_count = 0  # reset — scan_id is set

        # Update simple progress card if in production mode
        self._update_simple_progress()

        # Refresh cost dashboard if on that tab (live cost tracking)
        if self._tabs.currentIndex() == 1:
            self._cost_dashboard.refresh()

        try:
            status = self._scan_mgr.get_status(self._active_scan_id)
            if "error" in status:
                return

            completed = status.get("completed_batches", 0) or 0
            total = status.get("total_batches", 1) or 1
            cost = status.get("actual_cost_usd", 0) or 0
            budget = status.get("budget_cap_usd", 50) or 50
            scan_status = status.get("status", "")

            # CLI statuses: running → scan_complete → analyzing → analysis_complete
            terminal = ("scan_complete", "analysis_complete",
                        "completed", "completed_with_errors",
                        "failed", "cancelled",
                        "budget_exceeded", "quota_exhausted")

            if scan_status == "paused":
                self._pause_btn.setVisible(False)
                self._resume_btn.setVisible(True)

            if scan_status in terminal:
                self._stop_polling()
                self._scan_monitor.stop_monitoring()
                self._active_scan_id = None
                self._cost_dashboard.set_active_scan(None)
                self._cost_dashboard.refresh()
                self.scan_active_changed.emit(False)

                # Reset buttons
                self._start_btn.setEnabled(True)
                self._pause_btn.setVisible(False)
                self._resume_btn.setVisible(False)
                self._cancel_btn.setVisible(False)
                self._retry_failed_btn.setVisible(False)
                self._status_panel.show_idle()

                if scan_status == "completed_with_errors":
                    # Some batches failed — show retry button
                    try:
                        failed = self.db.get_failed_batches_for_scan(
                            status.get("scan_id", ""))
                        n_failed = len(failed)
                        if n_failed > 0:
                            self._retry_failed_btn.setText(
                                f"Retry {n_failed} Failed Batch(es)")
                            self._retry_failed_btn.setVisible(True)
                            self._active_scan_id = status.get("scan_id")
                    except Exception:
                        pass
                    self._est_label.setText(
                        f"Scan completed with errors | "
                        f"{completed}/{total} batches succeeded"
                    )
                    self._est_label.setStyleSheet(
                        f"font-size: 12px; color: {ALMA_WARNING}; "
                        f"font-weight: 600;"
                    )
                    QApplication.processEvents()
                    self._run_post_scan_analysis()
                elif scan_status in ("scan_complete", "completed"):
                    QApplication.processEvents()
                    self._run_post_scan_analysis()
                elif scan_status == "analysis_complete":
                    # Already analyzed
                    self._refresh_all()
                elif scan_status == "budget_exceeded":
                    self._est_label.setText(
                        f"Scan paused: budget cap (${budget:.2f}) reached. "
                        f"Resume with higher budget to continue."
                    )
                    # Restore scan ID so Resume works
                    self._active_scan_id = status.get("scan_id")
                    self._resume_btn.setVisible(True)
                    self._cancel_btn.setVisible(True)
                elif scan_status == "quota_exhausted":
                    self._est_label.setText(
                        "Scan stopped: Gemini API quota exhausted. "
                        "Wait a few minutes, then Resume."
                    )
                    self._est_label.setStyleSheet(
                        f"font-size: 12px; color: {ALMA_ERROR}; font-weight: 600;"
                    )
                    self._active_scan_id = status.get("scan_id")
                    self._resume_btn.setVisible(True)
                    self._cancel_btn.setVisible(True)
                elif scan_status == "failed":
                    self._est_label.setText(
                        f"Scan failed | {completed}/{total} batches completed"
                    )
                    self._est_label.setStyleSheet(
                        f"font-size: 12px; color: {ALMA_ERROR};"
                    )
                    self._scan_monitor.set_error_state("Scan failed")
                    if completed > 0:
                        QApplication.processEvents()
                        self._run_post_scan_analysis()
                    else:
                        self._refresh_all()
                else:
                    self._refresh_all()

        except Exception as e:
            logger.error(f"Poll failed: {e}")

    def _run_post_scan_analysis(self):
        """Run meta-analysis after scan completes."""
        try:
            scan = self.db.get_latest_completed_scan()
            if not scan:
                return

            from src.data.nlp_meta_analyzer import NLPMetaAnalyzer
            analyzer = NLPMetaAnalyzer(self.db)
            analyzer.run_analysis(scan["scan_id"])

            self._est_label.setText("Scan completed -- findings ready")
            self._refresh_all()
        except Exception as e:
            logger.error(f"Post-scan analysis failed: {e}")
            self._est_label.setText(f"Meta-analysis failed: {e}")

    def _on_scan_completed(self):
        """Handle scan_completed signal from ScanMonitorWidget."""
        self._stop_polling()
        self._scan_monitor.stop_monitoring()
        self._active_scan_id = None
        self.scan_active_changed.emit(False)

        # Reset buttons
        self._start_btn.setEnabled(True)
        self._pause_btn.setVisible(False)
        self._resume_btn.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._status_panel.show_idle()

        self._refresh_all()

    # ==============================================
    #  TAB SWITCHING
    # ==============================================

    def _on_tab_changed(self, index):
        """Refresh the active tab's content."""
        if index == 1:
            # Scanning Costs tab
            self._cost_dashboard.refresh()
        elif index == 2:
            # SubTaxonomy & Findings tab
            self._taxonomy_browser.refresh()

    # ==============================================
    #  PATTERN DRILLDOWN
    # ==============================================

    def _on_pattern_selected(self, pattern_data):
        """Handle taxonomy row click — show pattern in drilldown panel."""
        if self._drilldown:
            try:
                self._drilldown.show_pattern(pattern_data)
            except Exception as e:
                logger.error(f"Pattern drilldown failed: {e}")

    # ==============================================
    #  REFRESH
    # ==============================================

    def _refresh_all(self):
        """Refresh all sections across all tabs."""
        self._populate_trc_combo()
        self._refresh_history()
        self._update_cost_estimate()
        self._sync_date_range()
        self._refresh_simple_last_scan()

        # Refresh active tab
        current_tab = self._tabs.currentIndex()
        if current_tab == 1:
            self._cost_dashboard.refresh()
        elif current_tab == 2:
            self._taxonomy_browser.refresh()

    def _populate_trc_combo(self):
        """Populate TRC filter dropdown."""
        current = self._trc_combo.currentData()
        self._trc_combo.clear()
        self._trc_combo.addItem("All TRCs", None)
        try:
            trcs = self.db.get_trc_list()
            for trc in trcs:
                self._trc_combo.addItem(trc, trc)
        except Exception:
            pass

    def _sync_date_range(self):
        """Sync date pickers to actual data range."""
        try:
            row = self.db.conn.execute(
                "SELECT MIN(created_at) AS mn, MAX(created_at) AS mx FROM conversations"
            ).fetchone()
            if row and row["mn"] and row["mx"]:
                from PySide6.QtCore import QDate
                mn = row["mn"][:10].split("-")
                mx = row["mx"][:10].split("-")
                if len(mn) == 3 and len(mx) == 3:
                    self._date_start.set_date(QDate(int(mn[0]), int(mn[1]), int(mn[2])))
                    self._date_end.set_date(QDate(int(mx[0]), int(mx[1]), int(mx[2])))
        except Exception:
            pass

    # ==============================================
    #  SCAN WORKER MANAGER (CLI MODE)
    # ==============================================

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
