"""
Alma Insights — Source Monitor Page (Phase 3 + 3.5)

Real-time source monitoring with five tabs:
  1. Live Feed  — scrollable ticket cards + TRC spikes sidebar
  2. Alerts     — watchlist alert cards with Confirm/Dismiss feedback
  3. TRC Spikes — table with TRC, count, delta, progress bar
  4. Watchlist  — rule management (CRUD, EWMA confidence, enable/disable)
  5. Connection — Zendesk config (subdomain, email, API key, poll interval)
"""

import logging
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QLineEdit, QSpinBox, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar,
    QAbstractItemView, QMessageBox, QSizePolicy, QComboBox,
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

logger = logging.getLogger("alma.source_monitor")

_CARD_STYLE = f"""
    QFrame {{
        background: {ALMA_BG_ELEVATED};
        border: 1px solid rgba(214, 210, 202, 0.45);
        border-radius: 12px;
    }}
"""
_FIELD_STYLE = f"""
    QLineEdit, QSpinBox {{
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


class SourceMonitorPage(QWidget):
    """Source Monitor with Live Feed, Alerts, TRC Spikes, Watchlist, and Connection tabs."""

    # Emitted when connection settings change so MainWindow can rewire monitor
    connection_changed = Signal()

    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db = db_manager
        self._monitor = None
        self._watchlist = None
        self._ticket_cards: list[QFrame] = []
        self._alert_cards: list[QFrame] = []
        self._open_alert_count = 0

        self.setStyleSheet(f"background: {ALMA_CREAM};")
        self._build_ui()

    def set_watchlist(self, watchlist):
        """Wire the WatchlistEngine to this page for alerts + rules tabs."""
        self._watchlist = watchlist
        self._refresh_rules_table()
        self._refresh_alerts()

    def set_monitor(self, monitor):
        """Wire the ZendeskMonitor to this page.

        Also clears stale UI state (old ticket cards, spike sidebar)
        so the new monitor starts fresh.
        """
        self._monitor = monitor
        monitor.tickets_received.connect(self._on_tickets_received)
        monitor.spike_detected.connect(self._on_spike_detected)
        monitor.status_changed.connect(self._on_status_changed)
        monitor.alert_fired.connect(self._on_alert_fired)

        # Clear old ticket cards from previous monitor
        for card in self._ticket_cards:
            card.setParent(None)
            card.deleteLater()
        self._ticket_cards.clear()
        self._feed_placeholder.show()

        # Clear spike sidebar entries
        while self._spike_sidebar_container.count() > 0:
            item = self._spike_sidebar_container.takeAt(0)
            w = item.widget()
            if w and w is not self._spike_sidebar_empty:
                w.setParent(None)
                w.deleteLater()
        self._spike_sidebar_empty.setVisible(True)

        # Clear spikes table
        if hasattr(self, "_spikes_table"):
            self._spikes_table.setRowCount(0)

    # ═══════════════════════════════════════
    #  UI CONSTRUCTION
    # ═══════════════════════════════════════

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 20, 28, 0)
        outer.setSpacing(12)

        # Header
        header = QLabel("Source Monitor")
        header.setObjectName("PageHeader")
        outer.addWidget(header)

        sub = QLabel("Real-time Zendesk ticket feed with TRC spike detection")
        sub.setObjectName("PageSubheader")
        outer.addWidget(sub)

        # Status bar
        self._status_bar = self._build_status_bar()
        outer.addWidget(self._status_bar)

        # Tab widget
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        outer.addWidget(self._tabs, 1)

        # Tab 0: Live Feed
        self._tabs.addTab(self._build_live_feed_tab(), "Live Feed")
        # Tab 1: Alerts
        self._tabs.addTab(self._build_alerts_tab(), "Alerts")
        # Tab 2: TRC Spikes
        self._tabs.addTab(self._build_spikes_tab(), "TRC Spikes")
        # Tab 3: Watchlist
        self._tabs.addTab(self._build_watchlist_tab(), "Watchlist")
        # Tab 4: Connection
        self._tabs.addTab(self._build_connection_tab(), "Connection")

    # ── Status Bar ──

    def _build_status_bar(self):
        bar = QFrame()
        bar.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED};
                border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 8px; padding: 6px 12px;
            }}
        """)
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 6, 12, 6)
        row.setSpacing(20)

        # Status indicator
        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet(f"font-size: 14px; color: {ALMA_TEXT_LIGHT};")
        row.addWidget(self._status_dot)

        self._status_label = QLabel("Not connected")
        self._status_label.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {ALMA_TEXT_MID};")
        row.addWidget(self._status_label)

        row.addStretch()

        self._last_pull_label = QLabel("Last pull: —")
        self._last_pull_label.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT};")
        row.addWidget(self._last_pull_label)

        self._tickets_today_label = QLabel("Today: 0")
        self._tickets_today_label.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT};")
        row.addWidget(self._tickets_today_label)

        # Pause/Resume button
        self._pause_btn = QPushButton("Pause")
        self._pause_btn.setStyleSheet(_GHOST_BTN)
        self._pause_btn.setCursor(Qt.PointingHandCursor)
        self._pause_btn.setFixedWidth(80)
        self._pause_btn.clicked.connect(self._on_pause_resume)
        self._pause_btn.setEnabled(False)
        row.addWidget(self._pause_btn)

        return bar

    # ── Tab 1: Live Feed ──

    def _build_live_feed_tab(self):
        tab = QWidget()
        layout = QHBoxLayout(tab)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(12)

        # Left: Ticket feed
        feed_area = QScrollArea()
        feed_area.setWidgetResizable(True)
        feed_area.setFrameShape(QFrame.NoFrame)
        feed_area.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self._feed_container = QWidget()
        self._feed_layout = QVBoxLayout(self._feed_container)
        self._feed_layout.setContentsMargins(0, 0, 0, 0)
        self._feed_layout.setSpacing(8)

        # Placeholder message
        self._feed_placeholder = QLabel(
            "No tickets yet. Configure Zendesk connection in the Connection tab, "
            "or wait for incoming tickets."
        )
        self._feed_placeholder.setStyleSheet(
            f"font-size: 13px; color: {ALMA_TEXT_LIGHT}; padding: 40px;"
        )
        self._feed_placeholder.setAlignment(Qt.AlignCenter)
        self._feed_placeholder.setWordWrap(True)
        self._feed_layout.addWidget(self._feed_placeholder)
        self._feed_layout.addStretch()

        feed_area.setWidget(self._feed_container)
        layout.addWidget(feed_area, 3)

        # Right: Spike sidebar
        spike_panel = QFrame()
        spike_panel.setStyleSheet(_CARD_STYLE)
        spike_panel.setMinimumWidth(240)
        spike_panel.setMaximumWidth(320)
        apply_card_shadow_soft(spike_panel)
        spike_lay = QVBoxLayout(spike_panel)
        spike_lay.setContentsMargins(14, 12, 14, 12)
        spike_lay.setSpacing(8)

        spike_title = QLabel("TRC Spikes")
        spike_title.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;"
        )
        spike_lay.addWidget(spike_title)

        self._spike_sidebar_container = QVBoxLayout()
        self._spike_sidebar_container.setSpacing(4)
        spike_lay.addLayout(self._spike_sidebar_container)

        self._spike_sidebar_empty = QLabel("No spikes detected")
        self._spike_sidebar_empty.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none;"
        )
        self._spike_sidebar_container.addWidget(self._spike_sidebar_empty)
        spike_lay.addStretch()

        layout.addWidget(spike_panel, 1)
        return tab

    # ── Tab 2: TRC Spikes Table ──

    def _build_spikes_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(12)

        self._spikes_table = QTableWidget()
        self._spikes_table.setColumnCount(4)
        self._spikes_table.setHorizontalHeaderLabels([
            "TRC Code", "Count (60 min)", "Prior (60 min)", "Delta %"
        ])
        self._spikes_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self._spikes_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._spikes_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._spikes_table.setAlternatingRowColors(True)
        self._spikes_table.verticalHeader().setVisible(False)
        self._spikes_table.setStyleSheet(f"""
            QTableWidget {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 8px; font-size: 12px; gridline-color: {ALMA_BORDER_LIGHT};
            }}
            QTableWidget::item {{ padding: 6px 10px; }}
            QHeaderView::section {{
                background: {ALMA_CREAM}; font-weight: 600; font-size: 11px;
                border: none; padding: 8px; color: {ALMA_TEXT_MID};
            }}
        """)
        layout.addWidget(self._spikes_table)

        refresh_row = QHBoxLayout()
        refresh_btn = QPushButton("Refresh Spikes")
        refresh_btn.setStyleSheet(_GHOST_BTN)
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.clicked.connect(self._refresh_spikes_table)
        refresh_row.addWidget(refresh_btn)
        refresh_row.addStretch()
        layout.addLayout(refresh_row)

        return tab

    # ── Tab 1: Alerts ──

    def _build_alerts_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(12)

        # Filter row
        filter_row = QHBoxLayout()
        filter_row.setSpacing(12)

        lbl = QLabel("Severity:")
        lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {ALMA_TEXT_MID};")
        filter_row.addWidget(lbl)

        self._alert_severity_filter = QComboBox()
        self._alert_severity_filter.addItems(["All", "incident", "watch"])
        self._alert_severity_filter.setStyleSheet(f"""
            QComboBox {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER};
                border-radius: 6px; padding: 4px 10px; font-size: 12px;
                color: {ALMA_TEXT_DARK}; min-width: 100px;
            }}
        """)
        self._alert_severity_filter.currentIndexChanged.connect(
            self._refresh_alerts
        )
        filter_row.addWidget(self._alert_severity_filter)

        filter_row.addStretch()

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setStyleSheet(_GHOST_BTN)
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.clicked.connect(self._refresh_alerts)
        filter_row.addWidget(refresh_btn)

        layout.addLayout(filter_row)

        # Scrollable alert cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self._alerts_container = QWidget()
        self._alerts_layout = QVBoxLayout(self._alerts_container)
        self._alerts_layout.setContentsMargins(0, 0, 0, 0)
        self._alerts_layout.setSpacing(8)

        self._alerts_placeholder = QLabel(
            "No alerts yet. Watchlist rules evaluate incoming tickets automatically."
        )
        self._alerts_placeholder.setStyleSheet(
            f"font-size: 13px; color: {ALMA_TEXT_LIGHT}; padding: 40px;"
        )
        self._alerts_placeholder.setAlignment(Qt.AlignCenter)
        self._alerts_placeholder.setWordWrap(True)
        self._alerts_layout.addWidget(self._alerts_placeholder)
        self._alerts_layout.addStretch()

        scroll.setWidget(self._alerts_container)
        layout.addWidget(scroll, 1)

        return tab

    # ── Tab 3: Watchlist Rules ──

    def _build_watchlist_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(12)

        # Header row with Add Rule button
        hdr_row = QHBoxLayout()
        hdr_row.setSpacing(12)

        desc = QLabel(
            "Manage alert rules. System rules are prepopulated and cannot be "
            "deleted but can be toggled on/off."
        )
        desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        desc.setWordWrap(True)
        hdr_row.addWidget(desc, 1)

        self._add_rule_btn = QPushButton("+ Add Rule")
        self._add_rule_btn.setStyleSheet(_PRIMARY_BTN)
        self._add_rule_btn.setCursor(Qt.PointingHandCursor)
        self._add_rule_btn.clicked.connect(self._on_add_rule)
        hdr_row.addWidget(self._add_rule_btn)

        layout.addLayout(hdr_row)

        # Rules table
        self._rules_table = QTableWidget()
        self._rules_table.setColumnCount(8)
        self._rules_table.setHorizontalHeaderLabels([
            "Name", "Type", "Severity", "Source", "EWMA",
            "Fires", "Confirmed", "Actions"
        ])
        header = self._rules_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, 8):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self._rules_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._rules_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._rules_table.setAlternatingRowColors(True)
        self._rules_table.verticalHeader().setVisible(False)
        self._rules_table.setStyleSheet(f"""
            QTableWidget {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 8px; font-size: 12px; gridline-color: {ALMA_BORDER_LIGHT};
            }}
            QTableWidget::item {{ padding: 6px 10px; }}
            QHeaderView::section {{
                background: {ALMA_CREAM}; font-weight: 600; font-size: 11px;
                border: none; padding: 8px; color: {ALMA_TEXT_MID};
            }}
        """)
        layout.addWidget(self._rules_table, 1)

        return tab

    # ── Tab 4: Connection Config ──

    def _build_connection_tab(self):
        tab = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 12, 0, 12)
        layout.setSpacing(16)

        # Zendesk config card
        card = QFrame()
        card.setStyleSheet(_CARD_STYLE)
        apply_card_shadow(card)
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(20, 16, 20, 16)
        card_lay.setSpacing(14)

        title = QLabel("Zendesk Connection")
        title.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;"
        )
        card_lay.addWidget(title)

        desc = QLabel(
            "Configure your Zendesk Support instance for real-time ticket monitoring. "
            "Requires an API token (Admin → Channels → API)."
        )
        desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT}; border: none;")
        desc.setWordWrap(True)
        card_lay.addWidget(desc)

        # Row 1: Subdomain + Email
        row1 = QHBoxLayout()
        row1.setSpacing(16)

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("SUBDOMAIN")
        lbl.setStyleSheet(_LBL_STYLE)
        col.addWidget(lbl)
        self._zd_subdomain = QLineEdit()
        self._zd_subdomain.setPlaceholderText("mycompany  (from mycompany.zendesk.com)")
        self._zd_subdomain.setStyleSheet(_FIELD_STYLE)
        col.addWidget(self._zd_subdomain)
        row1.addLayout(col, 1)

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("EMAIL")
        lbl.setStyleSheet(_LBL_STYLE)
        col.addWidget(lbl)
        self._zd_email = QLineEdit()
        self._zd_email.setPlaceholderText("admin@company.com")
        self._zd_email.setStyleSheet(_FIELD_STYLE)
        col.addWidget(self._zd_email)
        row1.addLayout(col, 1)

        card_lay.addLayout(row1)

        # Row 2: View ID + API Key
        row2 = QHBoxLayout()
        row2.setSpacing(16)

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("VIEW ID")
        lbl.setStyleSheet(_LBL_STYLE)
        col.addWidget(lbl)
        self._zd_view_id = QLineEdit()
        self._zd_view_id.setPlaceholderText("e.g. 360012345  (Admin → Views → URL)")
        self._zd_view_id.setStyleSheet(_FIELD_STYLE)
        col.addWidget(self._zd_view_id)
        view_hint = QLabel(
            "Optional — scopes monitoring to a specific Zendesk view. "
            "Leave blank to use the incremental export (all tickets)."
        )
        view_hint.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; border: none;")
        view_hint.setWordWrap(True)
        col.addWidget(view_hint)
        row2.addLayout(col, 1)

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("API TOKEN")
        lbl.setStyleSheet(_LBL_STYLE)
        col.addWidget(lbl)
        self._zd_api_key = QLineEdit()
        self._zd_api_key.setPlaceholderText("Enter Zendesk API token")
        self._zd_api_key.setEchoMode(QLineEdit.Password)
        self._zd_api_key.setStyleSheet(_FIELD_STYLE)
        col.addWidget(self._zd_api_key)
        row2.addLayout(col, 1)

        card_lay.addLayout(row2)

        # Row 3: Poll Interval
        row3 = QHBoxLayout()
        row3.setSpacing(16)

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("POLL INTERVAL (seconds)")
        lbl.setStyleSheet(_LBL_STYLE)
        col.addWidget(lbl)
        self._zd_interval = QSpinBox()
        self._zd_interval.setRange(30, 600)
        self._zd_interval.setValue(120)
        self._zd_interval.setSuffix("s")
        self._zd_interval.setStyleSheet(_FIELD_STYLE)
        col.addWidget(self._zd_interval)
        row3.addLayout(col, 1)

        row3.addStretch(2)
        card_lay.addLayout(row3)

        # ── TRC Field Mapping ──
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background: {ALMA_BORDER_LIGHT}; max-height: 1px;")
        card_lay.addWidget(sep)

        trc_title = QLabel("TRC Field Mapping")
        trc_title.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;"
        )
        card_lay.addWidget(trc_title)

        trc_desc = QLabel(
            "Choose which Zendesk field maps to the TRC code for spike detection. "
            "Click \"Fetch Fields\" to load custom fields from your Zendesk instance."
        )
        trc_desc.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none;")
        trc_desc.setWordWrap(True)
        card_lay.addWidget(trc_desc)

        trc_row = QHBoxLayout()
        trc_row.setSpacing(12)

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("TRC SOURCE FIELD")
        lbl.setStyleSheet(_LBL_STYLE)
        col.addWidget(lbl)
        self._trc_field_combo = QComboBox()
        self._trc_field_combo.setStyleSheet(f"""
            QComboBox {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER};
                border-radius: 8px; padding: 8px 12px; font-size: 13px;
                color: {ALMA_TEXT_DARK}; min-width: 250px;
            }}
        """)
        # Built-in options (indices 0-5)
        self._trc_field_combo.addItem("Subject (default)", "subject")
        self._trc_field_combo.addItem("Tags (all, comma-joined)", "tags")
        self._trc_field_combo.addItem("Tag by prefix (see below)", "tag:")
        self._trc_field_combo.addItem("Type (incident/problem/question/task)", "type")
        self._trc_field_combo.addItem("Priority", "priority")
        self._trc_field_combo.addItem("Status", "status")
        self._trc_field_combo.currentIndexChanged.connect(
            self._on_trc_field_changed
        )
        col.addWidget(self._trc_field_combo)
        trc_row.addLayout(col, 2)

        # Tag prefix input (shown only when "Tag by prefix" is selected)
        col_prefix = QVBoxLayout()
        col_prefix.setSpacing(4)
        lbl_prefix = QLabel("TAG PREFIX")
        lbl_prefix.setStyleSheet(_LBL_STYLE)
        col_prefix.addWidget(lbl_prefix)
        self._tag_prefix_input = QLineEdit()
        self._tag_prefix_input.setPlaceholderText("e.g. category")
        self._tag_prefix_input.setStyleSheet(_FIELD_STYLE)
        self._tag_prefix_input.setToolTip(
            "Extract only the first tag starting with this prefix.\n"
            "Example: prefix 'category' matches tags like\n"
            "'category_billing', 'category_shipping', etc."
        )
        col_prefix.addWidget(self._tag_prefix_input)
        self._tag_prefix_container = QWidget()
        self._tag_prefix_container.setLayout(col_prefix)
        self._tag_prefix_container.setVisible(False)  # hidden by default
        trc_row.addWidget(self._tag_prefix_container, 1)

        col2 = QVBoxLayout()
        col2.setSpacing(4)
        lbl2 = QLabel(" ")
        lbl2.setStyleSheet(_LBL_STYLE)
        col2.addWidget(lbl2)
        self._fetch_fields_btn = QPushButton("Fetch Fields")
        self._fetch_fields_btn.setStyleSheet(_GHOST_BTN)
        self._fetch_fields_btn.setCursor(Qt.PointingHandCursor)
        self._fetch_fields_btn.setToolTip("Load custom ticket fields from Zendesk")
        self._fetch_fields_btn.clicked.connect(self._on_fetch_fields)
        col2.addWidget(self._fetch_fields_btn)
        trc_row.addLayout(col2, 1)

        trc_row.addStretch(1)
        card_lay.addLayout(trc_row)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        self._zd_test_btn = QPushButton("Test Connection")
        self._zd_test_btn.setStyleSheet(_GHOST_BTN)
        self._zd_test_btn.setCursor(Qt.PointingHandCursor)
        self._zd_test_btn.clicked.connect(self._on_test_connection)
        btn_row.addWidget(self._zd_test_btn)

        self._zd_save_btn = QPushButton("Save & Connect")
        self._zd_save_btn.setStyleSheet(_PRIMARY_BTN)
        self._zd_save_btn.setCursor(Qt.PointingHandCursor)
        self._zd_save_btn.clicked.connect(self._on_save_connection)
        btn_row.addWidget(self._zd_save_btn)

        self._zd_disconnect_btn = QPushButton("Disconnect")
        self._zd_disconnect_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_ERROR};
                border: 1px solid {ALMA_ERROR}; border-radius: 6px;
                padding: 6px 14px; font-size: 12px; font-weight: 600;
            }}
            QPushButton:hover {{ background: rgba(220,38,38,0.05); }}
        """)
        self._zd_disconnect_btn.setCursor(Qt.PointingHandCursor)
        self._zd_disconnect_btn.clicked.connect(self._on_disconnect)
        self._zd_disconnect_btn.setVisible(False)
        btn_row.addWidget(self._zd_disconnect_btn)

        btn_row.addStretch()

        self._zd_status = QLabel("")
        self._zd_status.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_MID};")
        btn_row.addWidget(self._zd_status)

        card_lay.addLayout(btn_row)
        layout.addWidget(card)
        layout.addStretch()

        scroll.setWidget(content)

        tab_outer = QVBoxLayout(tab)
        tab_outer.setContentsMargins(0, 0, 0, 0)
        tab_outer.addWidget(scroll)

        # Load saved credentials
        self._load_zendesk_settings()

        return tab

    # ═══════════════════════════════════════
    #  SIGNAL HANDLERS
    # ═══════════════════════════════════════

    def _on_trc_field_changed(self, index: int):
        """Show/hide tag prefix input based on combo selection."""
        data = self._trc_field_combo.currentData()
        self._tag_prefix_container.setVisible(data == "tag:")

    def _on_tickets_received(self, tickets: list):
        """Add ticket cards to the live feed."""
        # Always hide placeholder when tickets arrive
        if tickets:
            self._feed_placeholder.hide()

        for t in tickets:
            card = self._make_ticket_card(t)
            # Insert at top (most recent first)
            self._feed_layout.insertWidget(0, card)
            self._ticket_cards.append(card)

        # Limit visible cards to 200
        while len(self._ticket_cards) > 200:
            old = self._ticket_cards.pop(0)
            old.setParent(None)
            old.deleteLater()

        # Update status bar
        if self._monitor:
            self._tickets_today_label.setText(
                f"Today: {self._monitor.tickets_today}"
            )
            self._last_pull_label.setText(
                f"Last pull: {self._monitor.last_pull}"
            )

        # Auto-refresh the TRC Spikes table
        self._refresh_spikes_table()

    def _on_spike_detected(self, trc: str, count: int, delta: float):
        """Add a spike alert to the sidebar."""
        self._spike_sidebar_empty.setVisible(False)

        alert = QLabel(f"⚠ {trc}: {count} tickets (+{delta:.0%})")
        alert.setStyleSheet(
            f"font-size: 11px; font-weight: 600; color: {ALMA_WARNING}; "
            f"background: rgba(245,158,11,0.08); border-radius: 4px; "
            f"padding: 4px 8px; border: none;"
        )
        self._spike_sidebar_container.insertWidget(0, alert)

    def _on_status_changed(self, status: str):
        """Update status bar indicators."""
        colors = {
            "live": ALMA_SUCCESS,
            "paused": ALMA_WARNING,
            "error": ALMA_ERROR,
        }
        labels = {
            "live": "Live",
            "paused": "Paused",
            "error": "Error",
        }
        c = colors.get(status, ALMA_TEXT_LIGHT)
        self._status_dot.setStyleSheet(f"font-size: 14px; color: {c};")
        self._status_label.setText(labels.get(status, status))
        self._status_label.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {c};"
        )
        self._pause_btn.setEnabled(status in ("live", "paused"))
        self._pause_btn.setText("Resume" if status == "paused" else "Pause")

    # ═══════════════════════════════════════
    #  ALERT HANDLERS
    # ═══════════════════════════════════════

    def _on_alert_fired(self, alert: dict):
        """Handle incoming watchlist alert from monitor."""
        self._refresh_alerts()
        # Update tab badge
        self._open_alert_count += 1
        self._tabs.setTabText(1, f"Alerts ({self._open_alert_count})")

    def _refresh_alerts(self):
        """Reload alert cards from the watchlist engine."""
        # Clear existing cards
        for card in self._alert_cards:
            card.setParent(None)
            card.deleteLater()
        self._alert_cards.clear()

        if not self._watchlist:
            self._alerts_placeholder.show()
            return

        alerts = self._watchlist.get_all_alerts(limit=50)

        # Apply severity filter
        sev_filter = self._alert_severity_filter.currentText()
        if sev_filter != "All":
            alerts = [a for a in alerts if a.get("severity") == sev_filter]

        if not alerts:
            self._alerts_placeholder.show()
            return

        self._alerts_placeholder.hide()

        open_count = 0
        for alert in alerts:
            card = self._make_alert_card(alert)
            self._alerts_layout.insertWidget(
                self._alerts_layout.count() - 1, card  # before stretch
            )
            self._alert_cards.append(card)
            if alert.get("status") == "open":
                open_count += 1

        self._open_alert_count = open_count
        badge = f" ({open_count})" if open_count > 0 else ""
        self._tabs.setTabText(1, f"Alerts{badge}")

    def _make_alert_card(self, alert: dict) -> QFrame:
        """Build a single alert card with Confirm/Dismiss buttons."""
        card = QFrame()
        severity = alert.get("severity", "watch")
        status = alert.get("status", "open")
        is_incident = severity == "incident"
        border_color = ALMA_ERROR if is_incident else ALMA_WARNING
        bg = ALMA_WHITE

        if status == "confirmed":
            border_color = ALMA_SUCCESS
        elif status == "dismissed" or status == "expired":
            border_color = ALMA_BORDER_LIGHT

        card.setStyleSheet(f"""
            QFrame {{
                background: {bg};
                border: 1px solid {ALMA_BORDER_LIGHT};
                border-left: 4px solid {border_color};
                border-radius: 8px;
            }}
        """)
        apply_card_shadow_soft(card)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        # Top row: severity badge + title + timestamp
        top = QHBoxLayout()

        sev_color = ALMA_ERROR if is_incident else ALMA_WARNING
        sev_bg = "rgba(220,38,38,0.08)" if is_incident else "rgba(245,158,11,0.08)"
        sev_badge = QLabel(severity.upper())
        sev_badge.setStyleSheet(
            f"font-size: 10px; font-weight: 700; color: {sev_color}; "
            f"background: {sev_bg}; border-radius: 4px; padding: 2px 8px; border: none;"
        )
        top.addWidget(sev_badge)

        title = QLabel(alert.get("title", "Alert"))
        title.setStyleSheet(
            f"font-size: 12px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;"
        )
        top.addWidget(title)
        top.addStretch()

        ts = alert.get("created_at", "")
        ts_lbl = QLabel(ts[:16] if ts else "")
        ts_lbl.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; border: none;")
        top.addWidget(ts_lbl)
        lay.addLayout(top)

        # Summary
        summary = alert.get("summary", "")
        if summary:
            sum_lbl = QLabel(summary)
            sum_lbl.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_MID}; border: none;")
            sum_lbl.setWordWrap(True)
            sum_lbl.setMaximumHeight(50)
            lay.addWidget(sum_lbl)

        # Meta row: ticket count + source + TRC
        meta = QHBoxLayout()
        ticket_count = alert.get("ticket_count", 0)
        tc_lbl = QLabel(f"{ticket_count} ticket(s)")
        tc_lbl.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; border: none;")
        meta.addWidget(tc_lbl)

        source = alert.get("source", "")
        if source:
            src_lbl = QLabel(source)
            src_lbl.setStyleSheet(
                f"font-size: 10px; font-weight: 600; color: {ALMA_GREEN_DARK}; "
                f"background: {ALMA_GREEN_SUBTLE}; border-radius: 3px; "
                f"padding: 1px 5px; border: none;"
            )
            meta.addWidget(src_lbl)

        trc = alert.get("trc_code", "")
        if trc:
            trc_lbl = QLabel(trc)
            trc_lbl.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; border: none;")
            meta.addWidget(trc_lbl)

        meta.addStretch()

        # Status badge
        status_colors = {
            "open": ALMA_INFO, "confirmed": ALMA_SUCCESS,
            "dismissed": ALMA_TEXT_LIGHT, "expired": ALMA_TEXT_LIGHT,
        }
        s_color = status_colors.get(status, ALMA_TEXT_LIGHT)
        s_lbl = QLabel(status.upper())
        s_lbl.setStyleSheet(
            f"font-size: 9px; font-weight: 700; color: {s_color}; border: none;"
        )
        meta.addWidget(s_lbl)
        lay.addLayout(meta)

        # Action buttons (only for open alerts)
        if status == "open":
            btn_row = QHBoxLayout()
            btn_row.setSpacing(8)
            btn_row.addStretch()

            confirm_btn = QPushButton("Confirm")
            confirm_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {ALMA_SUCCESS}; color: white;
                    border: none; border-radius: 4px; padding: 4px 12px;
                    font-size: 11px; font-weight: 600;
                }}
                QPushButton:hover {{ background: #059669; }}
            """)
            confirm_btn.setCursor(Qt.PointingHandCursor)
            alert_id = alert.get("id")
            confirm_btn.clicked.connect(
                lambda _, aid=alert_id: self._on_alert_feedback(aid, "confirmed")
            )
            btn_row.addWidget(confirm_btn)

            dismiss_btn = QPushButton("Dismiss")
            dismiss_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {ALMA_TEXT_MID};
                    border: 1px solid {ALMA_BORDER}; border-radius: 4px;
                    padding: 4px 12px; font-size: 11px; font-weight: 600;
                }}
                QPushButton:hover {{ background: {ALMA_CREAM}; }}
            """)
            dismiss_btn.setCursor(Qt.PointingHandCursor)
            dismiss_btn.clicked.connect(
                lambda _, aid=alert_id: self._on_alert_feedback(aid, "dismissed")
            )
            btn_row.addWidget(dismiss_btn)

            lay.addLayout(btn_row)

        return card

    def _on_alert_feedback(self, alert_id: int, outcome: str):
        """User confirms or dismisses an alert."""
        if not self._watchlist:
            return
        self._watchlist.record_feedback(alert_id, outcome)
        self._refresh_alerts()

    # ═══════════════════════════════════════
    #  WATCHLIST RULE HANDLERS
    # ═══════════════════════════════════════

    def _refresh_rules_table(self):
        """Reload watchlist rules into the rules table."""
        if not self._watchlist:
            self._rules_table.setRowCount(0)
            return

        rules = self._watchlist.list_rules()
        self._rules_table.setRowCount(len(rules))

        for row, rule in enumerate(rules):
            is_system = rule.get("is_system", 0)

            # Name
            name_item = QTableWidgetItem(rule.get("name", ""))
            if is_system:
                name_item.setToolTip("System rule (cannot be deleted)")
            self._rules_table.setItem(row, 0, name_item)

            # Type
            self._rules_table.setItem(
                row, 1, QTableWidgetItem(rule.get("rule_type", ""))
            )

            # Severity
            sev = rule.get("severity", "watch")
            sev_item = QTableWidgetItem(sev.upper())
            self._rules_table.setItem(row, 2, sev_item)

            # Source filter
            src_filter = rule.get("source_filter", "") or "All"
            self._rules_table.setItem(row, 3, QTableWidgetItem(src_filter))

            # EWMA confidence
            ewma = rule.get("ewma_confidence", 0.5)
            ewma_bar = QProgressBar()
            ewma_bar.setRange(0, 100)
            ewma_bar.setValue(int(ewma * 100))
            ewma_bar.setFormat(f"{ewma:.2f}")
            ewma_bar.setFixedHeight(18)
            ewma_bar.setStyleSheet(f"""
                QProgressBar {{
                    background: {ALMA_BG_INSET}; border: none;
                    border-radius: 4px; text-align: center;
                    font-size: 10px; color: {ALMA_TEXT_MID};
                }}
                QProgressBar::chunk {{
                    background: {ALMA_GREEN_MID}; border-radius: 4px;
                }}
            """)
            self._rules_table.setCellWidget(row, 4, ewma_bar)

            # Fires
            fires = rule.get("total_fires", 0)
            self._rules_table.setItem(row, 5, QTableWidgetItem(str(fires)))

            # Confirmed
            confirmed = rule.get("total_confirmed", 0)
            dismissed = rule.get("total_dismissed", 0)
            self._rules_table.setItem(
                row, 6, QTableWidgetItem(f"{confirmed}/{dismissed}")
            )

            # Actions cell
            actions_widget = QWidget()
            actions_lay = QHBoxLayout(actions_widget)
            actions_lay.setContentsMargins(2, 2, 2, 2)
            actions_lay.setSpacing(4)

            rule_id = rule.get("id")
            enabled = rule.get("enabled", 1)

            # Toggle button
            toggle_btn = QPushButton("ON" if enabled else "OFF")
            toggle_color = ALMA_SUCCESS if enabled else ALMA_TEXT_LIGHT
            toggle_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {toggle_color};
                    border: 1px solid {toggle_color}; border-radius: 3px;
                    padding: 2px 8px; font-size: 10px; font-weight: 700;
                }}
            """)
            toggle_btn.setCursor(Qt.PointingHandCursor)
            toggle_btn.setFixedHeight(22)
            toggle_btn.clicked.connect(
                lambda _, rid=rule_id, en=enabled: self._on_toggle_rule(
                    rid, not en
                )
            )
            actions_lay.addWidget(toggle_btn)

            # Delete button (user rules only)
            if not is_system:
                del_btn = QPushButton("Del")
                del_btn.setStyleSheet(f"""
                    QPushButton {{
                        background: transparent; color: {ALMA_ERROR};
                        border: 1px solid {ALMA_ERROR}; border-radius: 3px;
                        padding: 2px 6px; font-size: 10px; font-weight: 600;
                    }}
                    QPushButton:hover {{ background: rgba(220,38,38,0.05); }}
                """)
                del_btn.setCursor(Qt.PointingHandCursor)
                del_btn.setFixedHeight(22)
                del_btn.clicked.connect(
                    lambda _, rid=rule_id: self._on_delete_rule(rid)
                )
                actions_lay.addWidget(del_btn)

            self._rules_table.setCellWidget(row, 7, actions_widget)

    def _on_toggle_rule(self, rule_id: int, enabled: bool):
        """Toggle a watchlist rule on/off."""
        if self._watchlist:
            self._watchlist.toggle_rule(rule_id, enabled)
            self._refresh_rules_table()

    def _on_delete_rule(self, rule_id: int):
        """Delete a user watchlist rule."""
        reply = QMessageBox.question(
            self, "Delete Rule",
            "Delete this watchlist rule? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        if self._watchlist:
            self._watchlist.delete_rule(rule_id)
            self._refresh_rules_table()

    def _on_add_rule(self):
        """Show dialog to add a new watchlist rule."""
        from PySide6.QtWidgets import QDialog, QFormLayout, QDialogButtonBox

        dlg = QDialog(self)
        dlg.setWindowTitle("Add Watchlist Rule")
        dlg.setMinimumWidth(420)
        form = QFormLayout(dlg)
        form.setSpacing(10)

        name_edit = QLineEdit()
        name_edit.setPlaceholderText("e.g. Refund Complaints")
        form.addRow("Name:", name_edit)

        type_combo = QComboBox()
        type_combo.addItems(["keyword", "entity", "volume", "compound"])
        form.addRow("Type:", type_combo)

        sev_combo = QComboBox()
        sev_combo.addItems(["watch", "incident"])
        form.addRow("Severity:", sev_combo)

        source_edit = QLineEdit()
        source_edit.setPlaceholderText("Leave blank for all sources")
        form.addRow("Source Filter:", source_edit)

        kw_edit = QLineEdit()
        kw_edit.setPlaceholderText("Comma-separated keywords")
        form.addRow("Keywords:", kw_edit)

        kw_mode_combo = QComboBox()
        kw_mode_combo.addItems(["any", "all"])
        form.addRow("Keyword Mode:", kw_mode_combo)

        entity_combo = QComboBox()
        entity_combo.addItems(["", "provider", "payer", "member"])
        form.addRow("Entity Type:", entity_combo)

        vol_spin = QSpinBox()
        vol_spin.setRange(0, 1000)
        vol_spin.setValue(0)
        vol_spin.setToolTip("0 = no volume threshold")
        form.addRow("Volume Threshold:", vol_spin)

        window_spin = QSpinBox()
        window_spin.setRange(5, 1440)
        window_spin.setValue(60)
        window_spin.setSuffix(" min")
        form.addRow("Volume Window:", window_spin)

        cooldown_spin = QSpinBox()
        cooldown_spin.setRange(1, 1440)
        cooldown_spin.setValue(120)
        cooldown_spin.setSuffix(" min")
        form.addRow("Cooldown:", cooldown_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)

        if dlg.exec() == QDialog.Accepted and self._watchlist:
            name = name_edit.text().strip()
            if not name:
                QMessageBox.warning(self, "Missing Name", "Rule name is required.")
                return

            self._watchlist.create_rule(
                name=name,
                rule_type=type_combo.currentText(),
                severity=sev_combo.currentText(),
                source_filter=source_edit.text().strip(),
                keywords=kw_edit.text().strip(),
                keyword_mode=kw_mode_combo.currentText(),
                entity_type=entity_combo.currentText(),
                volume_threshold=vol_spin.value(),
                volume_window_minutes=window_spin.value(),
                cooldown_minutes=cooldown_spin.value(),
            )
            self._refresh_rules_table()

    # ═══════════════════════════════════════
    #  TICKET CARD
    # ═══════════════════════════════════════

    def _make_ticket_card(self, ticket: dict) -> QFrame:
        """Create a ticket card widget for the live feed."""
        card = QFrame()
        trc = ticket.get("trc_code", "")
        is_flagged = ticket.get("flagged", False)

        border_color = ALMA_ERROR if is_flagged else ALMA_BORDER_LIGHT
        card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_WHITE};
                border: 1px solid {ALMA_BORDER_LIGHT};
                border-left: 3px solid {border_color};
                border-radius: 8px;
            }}
        """)
        apply_card_shadow_soft(card)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        # Top row: ticket ID + TRC badge + timestamp
        top = QHBoxLayout()
        tid_lbl = QLabel(f"#{ticket.get('id', '?')}")
        tid_lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;"
        )
        top.addWidget(tid_lbl)

        if trc:
            trc_badge = QLabel(trc)
            trc_badge.setStyleSheet(
                f"font-size: 10px; font-weight: 600; color: {ALMA_GREEN_DARK}; "
                f"background: {ALMA_GREEN_SUBTLE}; border-radius: 4px; "
                f"padding: 2px 6px; border: none;"
            )
            top.addWidget(trc_badge)

        top.addStretch()

        ts = ticket.get("created_at", "")
        if ts:
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                ts_str = dt.strftime("%H:%M")
            except Exception:
                ts_str = ts[:16]
        else:
            ts_str = ""
        ts_lbl = QLabel(ts_str)
        ts_lbl.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; border: none;")
        top.addWidget(ts_lbl)
        lay.addLayout(top)

        # Subject
        subject = ticket.get("subject", "(no subject)")
        subj_lbl = QLabel(subject)
        subj_lbl.setStyleSheet(
            f"font-size: 12px; color: {ALMA_TEXT_DARK}; border: none;"
        )
        subj_lbl.setWordWrap(True)
        subj_lbl.setMaximumHeight(40)
        lay.addWidget(subj_lbl)

        # Status + Priority
        meta = QHBoxLayout()
        status = ticket.get("status", "")
        if status:
            s_lbl = QLabel(status.capitalize())
            s_lbl.setStyleSheet(
                f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; border: none;"
            )
            meta.addWidget(s_lbl)

        priority = ticket.get("priority", "")
        if priority:
            p_lbl = QLabel(f"Priority: {priority}")
            p_lbl.setStyleSheet(
                f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; border: none;"
            )
            meta.addWidget(p_lbl)

        meta.addStretch()
        lay.addLayout(meta)

        return card

    # ═══════════════════════════════════════
    #  CONNECTION HANDLERS
    # ═══════════════════════════════════════

    def _load_zendesk_settings(self):
        """Load saved Zendesk credentials and TRC field mapping."""
        try:
            from src.data.zendesk_client import ZendeskClient
            sub, email, key, view_id = ZendeskClient.load_credentials()
            if sub:
                self._zd_subdomain.setText(sub)
            if email:
                self._zd_email.setText(email)
            if view_id:
                self._zd_view_id.setText(view_id)
            if key:
                self._zd_api_key.setText(key)
                self._zd_disconnect_btn.setVisible(True)
                self._zd_status.setText("✓ Credentials saved")

            # Restore TRC field mapping
            trc_field = ZendeskClient.load_trc_field()
            idx = self._trc_field_combo.findData(trc_field)
            if idx >= 0:
                self._trc_field_combo.setCurrentIndex(idx)
            elif trc_field.startswith("tag:"):
                # Restore "Tag by prefix" mode with saved prefix
                prefix = trc_field.split(":", 1)[1]
                tag_idx = self._trc_field_combo.findData("tag:")
                if tag_idx >= 0:
                    self._trc_field_combo.setCurrentIndex(tag_idx)
                self._tag_prefix_input.setText(prefix)
                self._tag_prefix_container.setVisible(True)
            elif trc_field.startswith("custom_field:"):
                # Custom field that was fetched previously — add and select
                field_id = trc_field.split(":", 1)[1]
                self._trc_field_combo.addItem(
                    f"Custom Field #{field_id}", trc_field
                )
                self._trc_field_combo.setCurrentIndex(
                    self._trc_field_combo.count() - 1
                )
        except Exception:
            pass

    def _on_test_connection(self):
        """Test Zendesk API credentials."""
        self._zd_test_btn.setEnabled(False)
        self._zd_test_btn.setText("Testing…")
        self._zd_status.setText("")

        from src.data.zendesk_client import ZendeskClient
        client = ZendeskClient(
            self._zd_subdomain.text().strip(),
            self._zd_email.text().strip(),
            self._zd_api_key.text().strip(),
        )

        # Run in background (quick enough for inline in most cases)
        try:
            ok = client.test_connection()
            if ok:
                self._zd_status.setText("✓ Connection successful!")
                self._zd_status.setStyleSheet(f"font-size: 11px; color: {ALMA_SUCCESS};")
            else:
                self._zd_status.setText("✗ Connection failed — check credentials")
                self._zd_status.setStyleSheet(f"font-size: 11px; color: {ALMA_ERROR};")
        except Exception as e:
            self._zd_status.setText(f"✗ Error: {e}")
            self._zd_status.setStyleSheet(f"font-size: 11px; color: {ALMA_ERROR};")
        finally:
            self._zd_test_btn.setEnabled(True)
            self._zd_test_btn.setText("Test Connection")

    def _on_save_connection(self):
        """Save credentials, TRC field mapping, and start monitoring."""
        sub = self._zd_subdomain.text().strip()
        email = self._zd_email.text().strip()
        key = self._zd_api_key.text().strip()
        view_id = self._zd_view_id.text().strip()

        if not (sub and email and key):
            QMessageBox.warning(
                self, "Missing Fields",
                "Please fill in subdomain, email, and API token."
            )
            return

        from src.data.zendesk_client import ZendeskClient
        ZendeskClient.save_credentials(sub, email, key, view_id=view_id)

        # Save TRC field mapping (append prefix for tag: mode)
        trc_field = self._trc_field_combo.currentData() or "subject"
        if trc_field == "tag:":
            prefix = self._tag_prefix_input.text().strip()
            if prefix:
                trc_field = f"tag:{prefix}"
            else:
                # No prefix → fall back to all tags
                trc_field = "tags"
        ZendeskClient.save_trc_field(trc_field)

        self._zd_disconnect_btn.setVisible(True)
        self._zd_status.setText("✓ Saved — starting monitor…")
        self._zd_status.setStyleSheet(f"font-size: 11px; color: {ALMA_SUCCESS};")

        self.connection_changed.emit()

    def _on_disconnect(self):
        """Clear credentials and stop monitoring."""
        reply = QMessageBox.question(
            self, "Disconnect Zendesk",
            "Remove Zendesk credentials and stop monitoring?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        from src.data.pat_store import save_setting
        save_setting("zendesk_subdomain", "")
        save_setting("zendesk_email", "")
        save_setting("zendesk_api_key", "")

        self._zd_subdomain.clear()
        self._zd_email.clear()
        self._zd_api_key.clear()
        self._zd_disconnect_btn.setVisible(False)
        self._zd_status.setText("Disconnected")
        self._zd_status.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT};")

        if self._monitor:
            self._monitor.stop()

    def _on_fetch_fields(self):
        """Fetch ticket fields from Zendesk and populate the TRC combo."""
        sub = self._zd_subdomain.text().strip()
        email = self._zd_email.text().strip()
        key = self._zd_api_key.text().strip()

        if not (sub and email and key):
            QMessageBox.warning(
                self, "Missing Credentials",
                "Enter subdomain, email, and API token before fetching fields."
            )
            return

        self._fetch_fields_btn.setEnabled(False)
        self._fetch_fields_btn.setText("Fetching…")

        try:
            from src.data.zendesk_client import ZendeskClient
            client = ZendeskClient(sub, email, key)
            fields = client.fetch_ticket_fields()

            if not fields:
                self._zd_status.setText("No fields returned — check connection")
                self._zd_status.setStyleSheet(
                    f"font-size: 11px; color: {ALMA_WARNING};"
                )
                return

            # Remember current selection
            current_data = self._trc_field_combo.currentData()

            # Remove previous custom fields (keep built-in items 0-4)
            while self._trc_field_combo.count() > 5:
                self._trc_field_combo.removeItem(5)

            # Add separator
            self._trc_field_combo.insertSeparator(5)

            # Add custom fields
            added = 0
            for f in fields:
                fid = str(f.get("id", ""))
                title = f.get("title", f"Field {fid}")
                ftype = f.get("type", "")
                active = f.get("active", True)

                # Skip system fields (already covered above) and inactive
                if not active:
                    continue
                if ftype in ("subject", "status", "priority", "tickettype",
                             "description", "group", "assignee"):
                    continue

                data_val = f"custom_field:{fid}"
                display = f"{title}  ({ftype}, #{fid})"
                self._trc_field_combo.addItem(display, data_val)
                added += 1

            # Restore selection
            if current_data:
                idx = self._trc_field_combo.findData(current_data)
                if idx >= 0:
                    self._trc_field_combo.setCurrentIndex(idx)

            self._zd_status.setText(f"✓ Loaded {added} custom fields")
            self._zd_status.setStyleSheet(f"font-size: 11px; color: {ALMA_SUCCESS};")

        except Exception as exc:
            self._zd_status.setText(f"✗ Fetch failed: {exc}")
            self._zd_status.setStyleSheet(f"font-size: 11px; color: {ALMA_ERROR};")

        finally:
            self._fetch_fields_btn.setEnabled(True)
            self._fetch_fields_btn.setText("Fetch Fields")

    def _on_pause_resume(self):
        if not self._monitor:
            return
        if self._monitor.status == "live":
            self._monitor.pause()
        else:
            self._monitor.resume()

    def _refresh_spikes_table(self):
        """Populate the TRC Spikes table from monitor data."""
        if not self._monitor:
            return
        spikes = self._monitor.get_spike_summary()
        self._spikes_table.setRowCount(len(spikes))
        for row, s in enumerate(spikes):
            self._spikes_table.setItem(row, 0, QTableWidgetItem(s["trc"]))
            self._spikes_table.setItem(row, 1, QTableWidgetItem(str(s["count"])))
            self._spikes_table.setItem(row, 2, QTableWidgetItem(str(s["prior"])))
            delta_str = f"{s['delta_pct']:+.0f}%" if s["delta_pct"] else "—"
            item = QTableWidgetItem(delta_str)
            if s["delta_pct"] > 0:
                item.setForeground(ALMA_ERROR if s["delta_pct"] >= 25 else ALMA_WARNING)
            self._spikes_table.setItem(row, 3, item)
