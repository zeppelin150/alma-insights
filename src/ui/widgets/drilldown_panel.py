"""
Alma Insights — Universal Drill-Down Panel
Overlay drawer that slides in from the right edge of the content area.

Supports three modes:
  - "tickets": Level 1 = ticket list, Level 2 = conversation thread viewer
  - "reports": Level 1 = report list, Level 2 = report detail viewer
  - "widget":  Embeds an arbitrary QWidget (e.g. AI panels, term manager)
"""

import json
from datetime import datetime

from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget, QTextBrowser, QGraphicsDropShadowEffect,
    QSizePolicy, QStackedWidget,
)
from PySide6.QtCore import (
    Qt, Signal, QPropertyAnimation, QEasingCurve, QRect, QEvent,
)
from PySide6.QtGui import QFont, QColor

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_GREEN_SUBTLE,
    ALMA_WHITE, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_BORDER, ALMA_BORDER_LIGHT, ALMA_HOVER_LIGHT,
    ALMA_INFO,
)
from src.ui.widgets.thread_renderer import render_thread_html, build_meta_text
from src.ui.widgets.pagination_bar import PaginationBar

# Neutralise global QPushButton padding from theme.py
_BTN_RESET = "padding: 0px; margin: 0px;"

PANEL_WIDTH = 440


class DrilldownPanel(QFrame):
    """Overlay drawer for drilling into ticket lists, conversation threads,
    and report history."""

    panel_opened = Signal()
    panel_closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DrilldownPanel")

        # Shared state
        self._tickets = []
        self._current_index = -1
        self._level = 0          # 0 = hidden, 1 = list, 2 = detail
        self._anim = None
        self._mode = "tickets"   # "tickets", "reports", or "widget"
        self._saved_title = ""   # Level-1 title (restored on back)

        # Report-mode state
        self._reports = []
        self._report_detail_cb = None   # callback(report_id) -> html str
        self._report_load_cb = None     # callback(report_id) -> load main view

        # Widget-mode state
        self._hosted_widget = None      # widget currently embedded in _widget_host

        self._build_ui()
        self._apply_shadow()

        # Start hidden off-screen
        self.hide()

        # Track parent resizes
        if parent:
            parent.installEventFilter(self)

    # ═══════════════════════════════════════════
    #  BUILD UI
    # ═══════════════════════════════════════════

    def _build_ui(self):
        self.setFixedWidth(PANEL_WIDTH)
        self.setStyleSheet(f"""
            #DrilldownPanel {{
                background: {ALMA_WHITE};
                border-left: 1px solid {ALMA_BORDER};
                border-top-left-radius: 12px;
                border-bottom-left-radius: 12px;
            }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Chevron tab (collapse toggle) ──
        self._chevron_bar = QFrame()
        self._chevron_bar.setFixedHeight(36)
        self._chevron_bar.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_CREAM};
                border-bottom: 1px solid {ALMA_BORDER_LIGHT};
                border-top-left-radius: 12px;
            }}
        """)
        chev_layout = QHBoxLayout(self._chevron_bar)
        chev_layout.setContentsMargins(12, 0, 12, 0)
        chev_layout.setSpacing(0)

        self._close_btn = QPushButton("\u00bb")   # »
        self._close_btn.setFixedSize(28, 28)
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_btn.setToolTip("Close panel")
        self._close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_TEXT_MID};
                font-size: 16px; font-weight: 700; border: none;
                border-radius: 4px; {_BTN_RESET}
            }}
            QPushButton:hover {{
                background: {ALMA_HOVER_LIGHT}; color: {ALMA_GREEN_DARK};
                {_BTN_RESET}
            }}
        """)
        self._close_btn.clicked.connect(self.close_panel)
        chev_layout.addWidget(self._close_btn)

        self._panel_title = QLabel("")
        self._panel_title.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {ALMA_TEXT_DARK}; padding-left: 4px;"
        )
        self._panel_title.setWordWrap(True)
        chev_layout.addWidget(self._panel_title, 1)

        root.addWidget(self._chevron_bar)

        # ── Level 1: List frame (tickets or reports) ──
        self._list_frame = QFrame()
        list_layout = QVBoxLayout(self._list_frame)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(0)

        # Subtitle / count
        self._list_subtitle = QLabel("")
        self._list_subtitle.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; padding: 6px 16px 4px 16px;"
        )
        list_layout.addWidget(self._list_subtitle)

        # Scrollable cards
        self._list_scroll = QScrollArea()
        self._list_scroll.setWidgetResizable(True)
        self._list_scroll.setFrameShape(QFrame.NoFrame)
        self._list_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self._list_container_widget = QWidget()
        self._list_container = QVBoxLayout(self._list_container_widget)
        self._list_container.setContentsMargins(12, 4, 12, 12)
        self._list_container.setSpacing(8)
        self._list_container.addStretch()
        self._list_scroll.setWidget(self._list_container_widget)

        list_layout.addWidget(self._list_scroll, 1)

        # Pagination
        self._list_pager = PaginationBar(page_size=20)
        self._list_pager.page_changed.connect(self._on_list_page_changed)
        list_layout.addWidget(self._list_pager)

        root.addWidget(self._list_frame)

        # ── Level 2: Detail frame (thread or report content) ──
        self._thread_frame = QFrame()
        thread_layout = QVBoxLayout(self._thread_frame)
        thread_layout.setContentsMargins(0, 0, 0, 0)
        thread_layout.setSpacing(0)

        # Back bar
        back_bar = QHBoxLayout()
        back_bar.setContentsMargins(12, 8, 12, 4)
        back_bar.setSpacing(8)

        self._back_btn = QPushButton("\u2190 Back to list")
        self._back_btn.setCursor(Qt.PointingHandCursor)
        self._back_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_GREEN_DARK};
                font-size: 12px; font-weight: 600; border: none;
                border-radius: 4px; {_BTN_RESET}
            }}
            QPushButton:hover {{
                color: {ALMA_GREEN_LIGHT}; {_BTN_RESET}
            }}
        """)
        self._back_btn.clicked.connect(self._go_back_to_list)
        back_bar.addWidget(self._back_btn)
        back_bar.addStretch()
        thread_layout.addLayout(back_bar)

        # Detail header + meta
        self._thread_header = QLabel("")
        self._thread_header.setWordWrap(True)
        self._thread_header.setStyleSheet(f"""
            font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK};
            padding: 4px 16px 2px 16px;
        """)
        thread_layout.addWidget(self._thread_header)

        self._thread_meta = QLabel("")
        self._thread_meta.setWordWrap(True)
        self._thread_meta.setStyleSheet(f"""
            font-size: 11px; color: {ALMA_TEXT_LIGHT};
            padding: 0px 16px 8px 16px;
        """)
        thread_layout.addWidget(self._thread_meta)

        # Divider
        div = QFrame()
        div.setFixedHeight(1)
        div.setStyleSheet(f"background: {ALMA_BORDER_LIGHT};")
        thread_layout.addWidget(div)

        # Stacked content area: index 0 = browser, index 1 = widget host
        self._detail_stack = QStackedWidget()

        # Index 0: Content browser (tickets / reports)
        self._thread_browser = QTextBrowser()
        self._thread_browser.setOpenExternalLinks(False)
        self._thread_browser.setStyleSheet(f"""
            QTextBrowser {{
                background: {ALMA_WHITE}; border: none;
                padding: 8px 12px; font-size: 13px;
            }}
        """)
        self._detail_stack.addWidget(self._thread_browser)

        # Index 1: Widget host (for embedded panels like AI suggestions, term manager)
        self._widget_host = QWidget()
        self._widget_host_layout = QVBoxLayout(self._widget_host)
        self._widget_host_layout.setContentsMargins(8, 4, 8, 4)
        self._widget_host_layout.setSpacing(0)
        self._detail_stack.addWidget(self._widget_host)

        thread_layout.addWidget(self._detail_stack, 1)

        # Nav bar: Prev / [Load in Main View] / Next
        nav_bar = QHBoxLayout()
        nav_bar.setContentsMargins(12, 6, 12, 10)
        nav_bar.setSpacing(8)

        self._prev_btn = QPushButton("\u25c0  Previous")
        self._prev_btn.setCursor(Qt.PointingHandCursor)
        self._prev_btn.setStyleSheet(self._nav_btn_style())
        self._prev_btn.clicked.connect(self._go_prev)
        nav_bar.addWidget(self._prev_btn)

        # "Load in Main View" button (reports mode only)
        self._load_main_btn = QPushButton("\u21b3 Load in Main View")
        self._load_main_btn.setCursor(Qt.PointingHandCursor)
        self._load_main_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_INFO};
                border: 1px solid {ALMA_INFO}; border-radius: 6px;
                font-size: 10px; font-weight: 600;
                padding: 5px 10px;
            }}
            QPushButton:hover {{
                background: rgba(29, 111, 165, 0.08);
            }}
        """)
        self._load_main_btn.clicked.connect(self._on_load_main_view)
        self._load_main_btn.hide()
        nav_bar.addWidget(self._load_main_btn)

        self._nav_label = QLabel("")
        self._nav_label.setAlignment(Qt.AlignCenter)
        self._nav_label.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT};")
        nav_bar.addWidget(self._nav_label, 1)

        self._next_btn = QPushButton("Next  \u25b6")
        self._next_btn.setCursor(Qt.PointingHandCursor)
        self._next_btn.setStyleSheet(self._nav_btn_style())
        self._next_btn.clicked.connect(self._go_next)
        nav_bar.addWidget(self._next_btn)

        thread_layout.addLayout(nav_bar)

        root.addWidget(self._thread_frame)

        # Start both frames hidden
        self._list_frame.hide()
        self._thread_frame.hide()

    def _nav_btn_style(self):
        return f"""
            QPushButton {{
                background: transparent; color: {ALMA_GREEN_DARK};
                border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 6px;
                font-size: 11px; font-weight: 600;
                padding: 6px 14px;
            }}
            QPushButton:hover {{
                background: {ALMA_CREAM}; border-color: {ALMA_GREEN_LIGHT};
                color: {ALMA_GREEN_LIGHT};
            }}
            QPushButton:disabled {{
                color: {ALMA_BORDER_LIGHT}; border-color: {ALMA_BORDER_LIGHT};
            }}
        """

    def _apply_shadow(self):
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(-4, 0)
        shadow.setColor(QColor(0, 0, 0, 30))
        self.setGraphicsEffect(shadow)

    # ═══════════════════════════════════════════
    #  PUBLIC API
    # ═══════════════════════════════════════════

    def show_tickets(self, title: str, subtitle: str, tickets: list):
        """Open the panel at Level 1 showing a ticket list."""
        self._mode = "tickets"
        self._detach_hosted_widget()
        self._detail_stack.setCurrentIndex(0)
        self._tickets = tickets or []
        self._reports = []
        self._current_index = -1
        self._thread_fetcher = None
        self._report_detail_cb = None
        self._report_load_cb = None
        self._level = 1
        self._saved_title = title

        self._panel_title.setText(title)
        self._list_subtitle.setText(subtitle)

        self._list_pager.set_total(len(self._tickets))
        self._render_ticket_page(0)

        self._list_frame.show()
        self._thread_frame.hide()
        self._back_btn.show()
        self._load_main_btn.hide()
        self._restore_nav_buttons()
        self._slide_open()

    def show_conversation(self, conv: dict, ticket_list: list = None, index: int = 0,
                          thread_fetcher=None):
        """Open the panel directly at Level 2 (thread view).
        If ticket_list is provided, enables prev/next navigation.

        Args:
            thread_fetcher: Optional callable(ticket_id) -> dict with full_thread.
                When provided, full_thread is lazy-loaded on demand instead of
                requiring it pre-loaded in every conv dict.  This avoids
                materializing the full text of all conversations in memory.
        """
        self._mode = "tickets"
        self._detach_hosted_widget()
        self._tickets = ticket_list or [conv]
        self._reports = []
        self._current_index = index
        self._level = 2
        self._thread_fetcher = thread_fetcher
        self._report_detail_cb = None
        self._report_load_cb = None

        self._render_thread(conv)

        self._list_frame.hide()
        self._thread_frame.show()
        self._load_main_btn.hide()

        # Hide back button if there's no list to go back to
        has_list = ticket_list is not None and len(ticket_list) > 1
        self._back_btn.setVisible(has_list)

        self._slide_open()

    def show_reports(self, title: str, subtitle: str, reports: list,
                     detail_callback=None, load_callback=None):
        """Open the panel at Level 1 showing a paginated report list.

        Args:
            title: Panel header text (e.g. "Trending Topics Reports")
            subtitle: Count text (e.g. "12 reports")
            reports: list of report dicts from db.get_reports()
            detail_callback: callable(report_id) -> html string for Level 2
            load_callback: callable(report_id) -> load report into main page view
        """
        self._mode = "reports"
        self._detach_hosted_widget()
        self._detail_stack.setCurrentIndex(0)
        self._tickets = []
        self._reports = reports or []
        self._report_detail_cb = detail_callback
        self._report_load_cb = load_callback
        self._thread_fetcher = None
        self._current_index = -1
        self._level = 1
        self._saved_title = title

        self._panel_title.setText(title)
        self._list_subtitle.setText(subtitle)

        self._list_pager.set_total(len(self._reports))
        self._render_report_page(0)

        self._list_frame.show()
        self._thread_frame.hide()
        self._back_btn.show()
        self._load_main_btn.hide()
        self._restore_nav_buttons()
        self._slide_open()

    def show_widget(self, title: str, subtitle: str, widget: QWidget):
        """Open the panel with an embedded QWidget (e.g. AI panel, term manager).

        The widget is temporarily reparented into the panel's host area.
        On close, it is detached (setParent(None)) but NOT deleted — the
        caller retains ownership.
        """
        self._mode = "widget"
        self._level = 2
        self._saved_title = title

        # Detach any previously hosted widget
        self._detach_hosted_widget()

        # Embed new widget
        self._hosted_widget = widget
        self._widget_host_layout.addWidget(widget)
        widget.show()
        self._detail_stack.setCurrentIndex(1)

        # Configure UI for widget mode
        self._panel_title.setText(title)
        self._thread_header.setText(title)
        self._thread_meta.setText(subtitle)

        # Hide list frame, nav controls, load button
        self._list_frame.hide()
        self._back_btn.hide()
        self._prev_btn.hide()
        self._next_btn.hide()
        self._nav_label.hide()
        self._load_main_btn.hide()

        # Show thread frame (it hosts the stacked widget)
        self._thread_frame.show()
        self._slide_open()

    def show_pattern(self, pattern_data: dict):
        """Open the panel showing sub-pattern stats, ticket list, and deep dive.

        Args:
            pattern_data: dict with keys: pattern_id, label, trc, tier,
                          lifetime_tickets, etc. (from sub_patterns table)
        """
        from PySide6.QtWidgets import QTextBrowser

        pattern_id = pattern_data.get("pattern_id", "")
        label = pattern_data.get("label", "Unknown Pattern")
        trc = pattern_data.get("trc", "?")
        tier = pattern_data.get("tier", "?")
        lifetime = pattern_data.get("lifetime_tickets", 0)

        # Build a stats widget
        stats_widget = QWidget()
        stats_layout = QVBoxLayout(stats_widget)
        stats_layout.setContentsMargins(8, 8, 8, 8)
        stats_layout.setSpacing(6)

        # Pattern info header
        header = QLabel(label)
        header.setStyleSheet("font-size: 14px; font-weight: 700; border: none;")
        header.setWordWrap(True)
        stats_layout.addWidget(header)

        # Stats grid
        stats_text = (
            f"TRC: {trc}\n"
            f"Tier: {tier}\n"
            f"Lifetime Tickets: {lifetime}"
        )

        # Try to get CSAT and sentiment from DB
        try:
            from src.ui.theme import ALMA_TEXT_MID
            # Get latest completed scan
            parent_widget = self.parent()
            if parent_widget and hasattr(parent_widget, 'db'):
                db = parent_widget.db
            else:
                # Try to get db from the closest ancestor that has it
                db = None
                p = self.parent()
                while p:
                    if hasattr(p, 'db'):
                        db = p.db
                        break
                    p = p.parent()

            if db:
                sub_cluster = label
                row = db.conn.execute("""
                    SELECT COUNT(*) as ticket_count,
                           AVG(c.csat_rating) as avg_csat,
                           AVG(tc.sentiment_intensity) as avg_sentiment
                    FROM nlp_ticket_classifications tc
                    LEFT JOIN conversations c ON c.ticket_id = tc.ticket_id
                    WHERE tc.sub_cluster = ?
                      AND tc.scan_id = (
                          SELECT scan_id FROM nlp_scan_runs
                          WHERE status IN ('analysis_complete', 'complete', 'completed')
                          ORDER BY created_at DESC LIMIT 1
                      )
                """, (sub_cluster,)).fetchone()

                if row:
                    ticket_count = row["ticket_count"] or 0
                    avg_csat = row["avg_csat"]
                    avg_sent = row["avg_sentiment"]
                    stats_text += f"\nClassified Tickets: {ticket_count}"
                    if avg_csat is not None:
                        stats_text += f"\nAvg CSAT: {avg_csat:.1f}"
                    if avg_sent is not None:
                        stats_text += f"\nAvg Sentiment: {avg_sent:.2f}"

                # Get matching ticket IDs for the ticket list
                ticket_rows = db.conn.execute("""
                    SELECT DISTINCT tc.ticket_id
                    FROM nlp_ticket_classifications tc
                    WHERE tc.sub_cluster = ?
                      AND tc.scan_id = (
                          SELECT scan_id FROM nlp_scan_runs
                          WHERE status IN ('analysis_complete', 'complete', 'completed')
                          ORDER BY created_at DESC LIMIT 1
                      )
                    LIMIT 50
                """, (sub_cluster,)).fetchall()
                ticket_ids = [r["ticket_id"] for r in ticket_rows]
            else:
                ticket_ids = []
        except Exception:
            ticket_ids = []

        stats_lbl = QLabel(stats_text)
        stats_lbl.setStyleSheet("font-size: 12px; color: #555; border: none;")
        stats_lbl.setWordWrap(True)
        stats_layout.addWidget(stats_lbl)

        # Ticket list (if available)
        if ticket_ids:
            tickets_title = QLabel(f"Tickets ({len(ticket_ids)})")
            tickets_title.setStyleSheet(
                "font-size: 12px; font-weight: 600; border: none; margin-top: 8px;"
            )
            stats_layout.addWidget(tickets_title)

            for tid in ticket_ids[:20]:
                tid_lbl = QLabel(f"  #{tid}")
                tid_lbl.setStyleSheet("font-size: 11px; color: #666; border: none;")
                stats_layout.addWidget(tid_lbl)

            if len(ticket_ids) > 20:
                more = QLabel(f"  ... and {len(ticket_ids) - 20} more")
                more.setStyleSheet("font-size: 10px; color: #999; font-style: italic; border: none;")
                stats_layout.addWidget(more)

        stats_layout.addStretch()

        # Use show_widget to display
        subtitle = f"TRC: {trc} | Tier: {tier} | {lifetime} tickets"
        self.show_widget(f"Pattern: {label[:30]}", subtitle, stats_widget)

    def close_panel(self):
        """Slide the panel closed."""
        if not self.isVisible():
            return
        self._slide_closed()

    def is_open(self) -> bool:
        return self.isVisible() and self._level > 0

    # ═══════════════════════════════════════════
    #  LEVEL 1 — LIST (tickets or reports)
    # ═══════════════════════════════════════════

    def _on_list_page_changed(self, page: int):
        if self._mode == "reports":
            self._render_report_page(page)
        else:
            self._render_ticket_page(page)

    # ── Ticket cards ──

    def _render_ticket_page(self, page: int):
        """Render one page of ticket cards."""
        self._clear_list()

        start, end = self._list_pager.page_slice()
        page_tickets = self._tickets[start:end]

        for i, ticket in enumerate(page_tickets):
            card = self._make_ticket_card(ticket, start + i)
            self._list_container.insertWidget(self._list_container.count() - 1, card)

        self._list_scroll.verticalScrollBar().setValue(0)

    def _make_ticket_card(self, ticket: dict, abs_index: int) -> QFrame:
        card = QFrame()
        card.setCursor(Qt.PointingHandCursor)
        card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_WHITE};
                border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 8px;
            }}
            QFrame:hover {{
                background: {ALMA_CREAM};
            }}
        """)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        # Row 1: Ticket ID + Date
        row1 = QHBoxLayout()
        row1.setSpacing(8)

        tid = QLabel(str(ticket.get("ticket_id", "")))
        tid.setStyleSheet(
            f"font-family: Consolas, 'SF Mono', monospace; font-size: 11px;"
            f" font-weight: 700; color: {ALMA_GREEN_DARK};"
        )
        row1.addWidget(tid)
        row1.addStretch()

        date_str = str(ticket.get("created_at", ""))[:10]
        date_lbl = QLabel(date_str)
        date_lbl.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT};")
        row1.addWidget(date_lbl)
        layout.addLayout(row1)

        # Row 2: Subject
        subject = ticket.get("subject", "No subject")
        if len(subject) > 80:
            subject = subject[:77] + "..."
        subj_lbl = QLabel(subject)
        subj_lbl.setWordWrap(True)
        subj_lbl.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_DARK};")
        layout.addWidget(subj_lbl)

        # Row 3: TRC badge + CSAT + Status
        row3 = QHBoxLayout()
        row3.setSpacing(8)

        trc = ticket.get("trc_code", "")
        if trc:
            trc_badge = QLabel(trc)
            trc_badge.setStyleSheet(f"""
                font-size: 10px; font-weight: 600; color: {ALMA_GREEN_DARK};
                background: rgba(20, 87, 63, 0.08); border-radius: 4px;
                padding: 2px 6px;
            """)
            row3.addWidget(trc_badge)

        csat = ticket.get("csat_score")
        if csat:
            csat_lbl = QLabel(f"CSAT {int(csat)}/5")
            csat_lbl.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT};")
            row3.addWidget(csat_lbl)

        status = ticket.get("status", "")
        if status:
            s_color = ALMA_GREEN_LIGHT if status.lower() == "solved" else ALMA_TEXT_LIGHT
            status_lbl = QLabel(status.title())
            status_lbl.setStyleSheet(f"font-size: 10px; font-weight: 600; color: {s_color};")
            row3.addWidget(status_lbl)

        row3.addStretch()
        layout.addLayout(row3)

        # Click handler
        card.mousePressEvent = lambda e, idx=abs_index: self._on_ticket_clicked(idx)

        return card

    def _on_ticket_clicked(self, index: int):
        if 0 <= index < len(self._tickets):
            self._current_index = index
            self._level = 2
            self._detail_stack.setCurrentIndex(0)
            self._restore_nav_buttons()
            self._render_thread(self._tickets[index])
            self._list_frame.hide()
            self._thread_frame.show()
            self._back_btn.show()
            self._load_main_btn.hide()

    # ── Report cards ──

    def _render_report_page(self, page: int):
        """Render one page of report cards."""
        self._clear_list()

        start, end = self._list_pager.page_slice()
        page_reports = self._reports[start:end]

        for i, report in enumerate(page_reports):
            card = self._make_report_card(report, start + i)
            self._list_container.insertWidget(self._list_container.count() - 1, card)

        self._list_scroll.verticalScrollBar().setValue(0)

    def _make_report_card(self, report: dict, abs_index: int) -> QFrame:
        card = QFrame()
        card.setCursor(Qt.PointingHandCursor)
        card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_WHITE};
                border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 8px;
            }}
            QFrame:hover {{
                background: {ALMA_CREAM};
            }}
        """)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        # Row 1: Timestamp + Duration badge
        row1 = QHBoxLayout()
        row1.setSpacing(8)

        run_at = report.get("run_at", "")
        try:
            dt = datetime.fromisoformat(run_at)
            time_str = dt.strftime("%b %d, %Y %I:%M %p")
        except (ValueError, TypeError):
            time_str = run_at

        ts_label = QLabel(time_str)
        ts_label.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {ALMA_TEXT_DARK};"
        )
        row1.addWidget(ts_label)
        row1.addStretch()

        duration = report.get("duration_ms", 0)
        if duration and duration > 0:
            dur_label = QLabel(f"{duration / 1000:.1f}s")
            dur_label.setStyleSheet(f"""
                font-size: 10px; color: {ALMA_TEXT_LIGHT};
                background: rgba(0,0,0,0.04); border-radius: 4px;
                padding: 1px 6px;
            """)
            row1.addWidget(dur_label)

        layout.addLayout(row1)

        # Row 2: Summary text
        summary_text = self._format_report_summary(report)
        summary_label = QLabel(summary_text)
        summary_label.setWordWrap(True)
        summary_label.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_MID};")
        layout.addWidget(summary_label)

        # Row 3: Parameter badges
        row3 = QHBoxLayout()
        row3.setSpacing(6)

        params = report.get("parameters", "{}")
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except (json.JSONDecodeError, TypeError):
                params = {}

        date_range = params.get("date_range", "")
        if date_range:
            dr_badge = QLabel(date_range)
            dr_badge.setStyleSheet(f"""
                font-size: 10px; color: {ALMA_INFO};
                background: rgba(29, 111, 165, 0.08); border-radius: 4px;
                padding: 1px 6px;
            """)
            row3.addWidget(dr_badge)

        trc_filter = params.get("trc_filter", "")
        if trc_filter and trc_filter != "All":
            trc_badge = QLabel(trc_filter)
            trc_badge.setStyleSheet(f"""
                font-size: 10px; font-weight: 600; color: {ALMA_GREEN_DARK};
                background: rgba(20, 87, 63, 0.08); border-radius: 4px;
                padding: 1px 6px;
            """)
            row3.addWidget(trc_badge)

        ticket_count = report.get("ticket_count", 0)
        if ticket_count:
            tc_badge = QLabel(f"{ticket_count} tickets")
            tc_badge.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT};")
            row3.addWidget(tc_badge)

        row3.addStretch()
        layout.addLayout(row3)

        # Click handler
        card.mousePressEvent = lambda e, idx=abs_index: self._on_report_clicked(idx)

        return card

    def _on_report_clicked(self, index: int):
        if 0 <= index < len(self._reports):
            self._current_index = index
            self._level = 2
            self._detail_stack.setCurrentIndex(0)
            self._restore_nav_buttons()
            self._render_report_detail(self._reports[index])
            self._list_frame.hide()
            self._thread_frame.show()
            self._back_btn.show()
            self._load_main_btn.setVisible(self._report_load_cb is not None)

    # ── Shared helpers ──

    def _clear_list(self):
        """Remove all cards from the list container (keep the stretch)."""
        while self._list_container.count() > 1:
            item = self._list_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    @staticmethod
    def _format_report_summary(report: dict) -> str:
        """Build a summary string from a report's summary JSON."""
        summary = report.get("summary", "{}")
        if isinstance(summary, str):
            try:
                summary = json.loads(summary)
            except (json.JSONDecodeError, TypeError):
                summary = {}

        if not isinstance(summary, dict):
            return str(summary) if summary else "\u2014"

        parts = []

        # Page-agnostic: try common fields
        # Incidents
        theta_2 = summary.get("theta_2_count", 0)
        theta_1 = summary.get("theta_1_count", 0)
        if theta_2:
            parts.append(f"{theta_2} incidents")
        if theta_1:
            parts.append(f"{theta_1} watches")

        # Trending
        rising = summary.get("rising_count", 0)
        topics = summary.get("topic_count", 0)
        if rising:
            parts.append(f"{rising} rising terms")
        if topics:
            parts.append(f"{topics} topics")

        # TRC Analytics
        trcs = summary.get("trc_count", 0)
        avg_csat = summary.get("avg_csat")
        if trcs:
            parts.append(f"{trcs} TRCs")
        if avg_csat:
            try:
                parts.append(f"CSAT {float(avg_csat):.1f}")
            except (ValueError, TypeError):
                pass

        # AI Reports: summary is often just text (first 500 chars)
        if not parts and not isinstance(summary, dict):
            text = str(summary)
            if len(text) > 80:
                text = text[:77] + "..."
            return text

        return " \u00b7 ".join(parts) if parts else "\u2014"

    # ═══════════════════════════════════════════
    #  LEVEL 2 — DETAIL VIEW
    # ═══════════════════════════════════════════

    # ── Thread (ticket mode) ──

    def _render_thread(self, conv: dict):
        self._detail_stack.setCurrentIndex(0)
        ticket_id = conv.get("ticket_id", "")
        subject = conv.get("subject", "No subject")

        self._panel_title.setText(f"{ticket_id}")
        self._thread_header.setText(f"{ticket_id} \u2014 {subject}")
        self._thread_meta.setText(build_meta_text(conv))

        # Lazy-load full_thread on demand (avoids 7 GB bulk materialization)
        thread = conv.get("full_thread")
        if not thread and hasattr(self, "_thread_fetcher") and self._thread_fetcher:
            try:
                full_conv = self._thread_fetcher(ticket_id)
                if full_conv:
                    thread = full_conv.get("full_thread", "")
                    conv["full_thread"] = thread  # Cache for re-renders
            except Exception:
                thread = None
        thread = thread or "No conversation data available."
        self._thread_browser.setHtml(render_thread_html(thread))

        # Update nav
        total = len(self._tickets)
        idx = self._current_index
        self._prev_btn.setEnabled(idx > 0)
        self._next_btn.setEnabled(idx < total - 1)
        self._nav_label.setText(f"{idx + 1} of {total}")

    # ── Report detail (report mode) ──

    def _render_report_detail(self, report: dict):
        """Render report detail into the thread frame."""
        self._detail_stack.setCurrentIndex(0)
        report_id = report.get("report_id", 0)

        # Timestamp for header
        run_at = report.get("run_at", "")
        try:
            dt = datetime.fromisoformat(run_at)
            time_str = dt.strftime("%b %d, %Y %I:%M %p")
        except (ValueError, TypeError):
            time_str = run_at

        self._panel_title.setText(f"Report \u2014 {time_str}")
        self._thread_header.setText(time_str)

        # Meta: parameters
        params = report.get("parameters", "{}")
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except (json.JSONDecodeError, TypeError):
                params = {}
        meta_parts = []
        for key in ("date_range", "trc_filter", "window", "method", "scan_date"):
            val = params.get(key)
            if val:
                meta_parts.append(f"{key.replace('_', ' ').title()}: {val}")
        duration = report.get("duration_ms", 0)
        if duration and duration > 0:
            meta_parts.append(f"Duration: {duration / 1000:.1f}s")
        self._thread_meta.setText(" \u00b7 ".join(meta_parts) if meta_parts else "")

        # Content: use callback if available, else fallback
        if self._report_detail_cb:
            html = self._report_detail_cb(report_id)
        else:
            html = f"<p style='color: {ALMA_TEXT_LIGHT};'>No detail renderer available.</p>"

        self._thread_browser.setHtml(html)

        # Update nav
        total = len(self._reports)
        idx = self._current_index
        self._prev_btn.setEnabled(idx > 0)
        self._next_btn.setEnabled(idx < total - 1)
        self._nav_label.setText(f"{idx + 1} of {total}")

    # ── Navigation ──

    def _go_prev(self):
        if self._current_index > 0:
            self._current_index -= 1
            if self._mode == "reports":
                if self._current_index < len(self._reports):
                    self._render_report_detail(self._reports[self._current_index])
            else:
                if self._current_index < len(self._tickets):
                    self._render_thread(self._tickets[self._current_index])

    def _go_next(self):
        if self._mode == "reports":
            if 0 <= self._current_index < len(self._reports) - 1:
                self._current_index += 1
                self._render_report_detail(self._reports[self._current_index])
        else:
            if 0 <= self._current_index < len(self._tickets) - 1:
                self._current_index += 1
                self._render_thread(self._tickets[self._current_index])

    def _go_back_to_list(self):
        self._level = 1
        self._thread_frame.hide()
        self._list_frame.show()
        self._load_main_btn.hide()
        self._panel_title.setText(self._saved_title)

    def _on_load_main_view(self):
        """'Load in Main View' button — replay report in page's main content."""
        if self._mode == "reports" and self._report_load_cb:
            report = self._reports[self._current_index] if 0 <= self._current_index < len(self._reports) else None
            if report:
                self._report_load_cb(report.get("report_id", 0))

    # ═══════════════════════════════════════════
    #  ANIMATION
    # ═══════════════════════════════════════════

    def _slide_open(self):
        if self.isVisible() and self._anim is None:
            # Already visible, no animation needed — just ensure position
            self._position_panel()
            self.panel_opened.emit()
            return

        parent = self.parentWidget()
        if not parent:
            return

        ph = parent.height()
        pw = parent.width()

        # End position: right edge
        end_rect = QRect(pw - PANEL_WIDTH, 0, PANEL_WIDTH, ph)
        # Start position: off-screen right
        start_rect = QRect(pw, 0, PANEL_WIDTH, ph)

        self.setGeometry(start_rect)
        self.show()
        self.raise_()

        self._anim = QPropertyAnimation(self, b"geometry")
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.setStartValue(start_rect)
        self._anim.setEndValue(end_rect)
        self._anim.finished.connect(self._on_open_finished)
        self._anim.start()

    def _on_open_finished(self):
        self._anim = None
        self.panel_opened.emit()

    def _slide_closed(self):
        parent = self.parentWidget()
        if not parent:
            self.hide()
            return

        ph = parent.height()
        pw = parent.width()

        start_rect = self.geometry()
        end_rect = QRect(pw, 0, PANEL_WIDTH, ph)

        self._anim = QPropertyAnimation(self, b"geometry")
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.InCubic)
        self._anim.setStartValue(start_rect)
        self._anim.setEndValue(end_rect)
        self._anim.finished.connect(self._on_close_finished)
        self._anim.start()

    def _on_close_finished(self):
        self._anim = None
        self._level = 0
        self._detach_hosted_widget()
        self._mode = "tickets"
        self._report_detail_cb = None
        self._report_load_cb = None
        self._thread_fetcher = None
        self._tickets = []
        self._reports = []
        self._current_index = -1
        self.hide()
        self.panel_closed.emit()

    def _detach_hosted_widget(self):
        """Remove hosted widget from the panel without destroying it."""
        if self._hosted_widget is not None:
            self._widget_host_layout.removeWidget(self._hosted_widget)
            self._hosted_widget.setParent(None)
            self._hosted_widget = None

    def _restore_nav_buttons(self):
        """Re-show nav buttons that widget mode hides."""
        self._prev_btn.show()
        self._next_btn.show()
        self._nav_label.show()

    def _position_panel(self):
        """Keep the panel pinned to the right edge of its parent."""
        parent = self.parentWidget()
        if not parent:
            return
        ph = parent.height()
        pw = parent.width()
        self.setGeometry(pw - PANEL_WIDTH, 0, PANEL_WIDTH, ph)

    # ═══════════════════════════════════════════
    #  EVENT FILTER (parent resize tracking)
    # ═══════════════════════════════════════════

    def eventFilter(self, obj, event):
        if obj == self.parentWidget() and event.type() == QEvent.Resize:
            if self.isVisible():
                self._position_panel()
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close_panel()
        else:
            super().keyPressEvent(event)
