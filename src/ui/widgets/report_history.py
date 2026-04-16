"""
Alma Insights — Report History Widget
Reusable scrollable list of past analysis runs.
Embeddable in any analysis page.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QScrollArea, QPushButton,
)
from PySide6.QtCore import Qt, Signal
import json
from datetime import datetime

from src.ui.theme import (
    ALMA_WHITE, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_BORDER_LIGHT, ALMA_INFO,
)


class ReportHistoryWidget(QWidget):
    """
    Scrollable list of past analysis reports for a specific page.
    Shows past reports with lazy loading.

    Emits `report_selected(report_id)` when user clicks a past report
    so the parent page can reload those results.
    """

    report_selected = Signal(int)   # report_id

    REPORTS_PER_PAGE = 50           # Load 50 at a time for scroll performance
    MAX_DISPLAY = 500

    def __init__(self, db, page_key: str, parent=None):
        super().__init__(parent)
        self.db = db
        self.page_key = page_key
        self._offset = 0
        self._total = 0

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 16, 0, 0)

        # ── Header ──
        header_row = QHBoxLayout()

        title = QLabel("Report History")
        title.setStyleSheet("font-size: 14px; font-weight: 600; border: none; background: transparent;")
        header_row.addWidget(title)

        self.count_label = QLabel("")
        self.count_label.setStyleSheet("font-size: 12px; color: #888; border: none; background: transparent;")
        header_row.addWidget(self.count_label)
        header_row.addStretch()

        layout.addLayout(header_row)

        # ── Scrollable list ──
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setMaximumHeight(400)
        self.scroll_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(4)
        self.list_layout.addStretch()

        self.scroll_area.setWidget(self.list_container)
        layout.addWidget(self.scroll_area)

        # ── Load More button ──
        self.load_more_btn = QPushButton("Load More")
        self.load_more_btn.setObjectName("SecondaryButton")
        self.load_more_btn.setVisible(False)
        self.load_more_btn.clicked.connect(self._load_more)
        layout.addWidget(self.load_more_btn, alignment=Qt.AlignCenter)

    def refresh(self):
        """Reload the report list from the database."""
        # Clear existing items
        while self.list_layout.count() > 1:  # keep the stretch
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._offset = 0
        self._total = self.db.get_report_count(self.page_key)
        self.count_label.setText(f"({self._total} reports)")
        self._load_batch()

    def _load_batch(self):
        """Load the next batch of reports."""
        reports = self.db.get_reports(
            self.page_key,
            limit=self.REPORTS_PER_PAGE,
            offset=self._offset,
        )

        insert_idx = self.list_layout.count() - 1  # before the stretch

        for report in reports:
            card = self._build_report_card(report)
            self.list_layout.insertWidget(insert_idx, card)
            insert_idx += 1

        self._offset += len(reports)

        # Show/hide Load More
        self.load_more_btn.setVisible(
            self._offset < self._total and self._offset < self.MAX_DISPLAY
        )

    def _load_more(self):
        self._load_batch()

    def _build_report_card(self, report: dict) -> QFrame:
        """Build a single report row."""
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_WHITE};
                border: none;
                border-radius: 6px;
                padding: 8px 12px;
            }}
            QFrame:hover {{
                border-color: {ALMA_INFO};
                background: #F8FAFF;
            }}
        """)
        card.setCursor(Qt.PointingHandCursor)

        layout = QHBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Parse data
        run_at = report.get("run_at", "")
        try:
            dt = datetime.fromisoformat(run_at)
            time_str = dt.strftime("%b %d, %Y %I:%M %p")
        except (ValueError, TypeError):
            time_str = run_at

        summary = report.get("summary", "{}")
        if isinstance(summary, str):
            try:
                summary = json.loads(summary)
            except json.JSONDecodeError:
                summary = {}

        # Timestamp
        ts_label = QLabel(time_str)
        ts_label.setStyleSheet(f"font-size: 12px; font-weight: 500; min-width: 160px; border: none; background: transparent; color: {ALMA_TEXT_DARK};")
        layout.addWidget(ts_label)

        # Summary text
        summary_text = self._format_summary(summary, report)
        summary_label = QLabel(summary_text)
        summary_label.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_MID}; border: none; background: transparent;")
        summary_label.setWordWrap(True)
        layout.addWidget(summary_label, 1)

        # Duration
        duration = report.get("duration_ms", 0)
        if duration > 0:
            dur_label = QLabel(f"{duration / 1000:.1f}s")
            dur_label.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; min-width: 50px; border: none; background: transparent;")
            dur_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            layout.addWidget(dur_label)

        # Click handler
        report_id = report["report_id"]
        card.mousePressEvent = lambda e, rid=report_id: self.report_selected.emit(rid)

        return card

    def _format_summary(self, summary: dict, report: dict) -> str:
        """Format summary dict into a short display string."""
        parts = []

        ticket_count = report.get("ticket_count", 0)
        if ticket_count:
            parts.append(f"{ticket_count} tickets")

        # Page-specific summary fields
        if self.page_key == "incidents":
            theta_2 = summary.get("theta_2_count", 0)
            theta_1 = summary.get("theta_1_count", 0)
            if theta_2:
                parts.append(f"{theta_2} incidents")
            if theta_1:
                parts.append(f"{theta_1} watches")
            if not theta_2 and not theta_1:
                parts.append("All clear")

        elif self.page_key == "trending_topics":
            rising = summary.get("rising_count", 0)
            topics = summary.get("topic_count", 0)
            if rising:
                parts.append(f"{rising} rising terms")
            if topics:
                parts.append(f"{topics} topics")

        elif self.page_key == "trc_analytics":
            trcs = summary.get("trc_count", 0)
            avg_csat = summary.get("avg_csat")
            if trcs:
                parts.append(f"{trcs} TRCs")
            if avg_csat:
                parts.append(f"CSAT {avg_csat:.1f}")

        # Parameters
        params = report.get("parameters", "{}")
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except json.JSONDecodeError:
                params = {}
        date_range = params.get("date_range", "")
        if date_range:
            parts.append(date_range)

        return " · ".join(parts) if parts else "—"
