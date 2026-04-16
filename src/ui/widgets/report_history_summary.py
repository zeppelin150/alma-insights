"""
Alma Insights — Report History Summary Widget
Compact single-row card showing report count + last run time.
Clicking 'View All' opens the DrilldownPanel with full report list.
"""

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QPushButton, QFrame,
)
from PySide6.QtCore import Qt, Signal
from datetime import datetime

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_WHITE,
    ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_BORDER_LIGHT, ALMA_BG_ELEVATED, ALMA_HOVER_LIGHT,
    apply_card_shadow_soft,
)


class ReportHistorySummary(QFrame):
    """
    Compact summary card for report history.

    Displays:  '3 reports · Last: Feb 19, 2026 2:30 PM  [View All »]'

    Emits ``view_all_clicked`` when the user clicks the "View All" button
    or double-clicks the card — the parent page should respond by opening
    the DrilldownPanel with the full report list.
    """

    view_all_clicked = Signal()

    def __init__(self, db, page_key: str, parent=None):
        super().__init__(parent)
        self.db = db
        self.page_key = page_key
        self._build_ui()

    def _build_ui(self):
        self.setStyleSheet(f"""
            ReportHistorySummary {{
                background: {ALMA_BG_ELEVATED};
                border: none;
                border-radius: 8px;
            }}
            ReportHistorySummary:hover {{
                border-color: {ALMA_GREEN_LIGHT};
            }}
        """)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(44)
        apply_card_shadow_soft(self)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 10, 0)
        layout.setSpacing(10)

        # Icon
        icon_label = QLabel("\U0001f4cb")   # clipboard emoji
        icon_label.setStyleSheet("font-size: 14px; border: none; background: transparent;")
        icon_label.setFixedWidth(20)
        layout.addWidget(icon_label)

        # Summary text
        self._summary_label = QLabel("No reports yet")
        self._summary_label.setStyleSheet(f"""
            font-size: 12px; color: {ALMA_TEXT_MID};
            border: none; background: transparent;
        """)
        layout.addWidget(self._summary_label, 1)

        # "View All" button
        self._view_btn = QPushButton("View All \u00bb")
        self._view_btn.setCursor(Qt.PointingHandCursor)
        self._view_btn.setEnabled(False)
        self._view_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_GREEN_DARK};
                border: none; border-radius: 4px;
                padding: 4px 10px; font-size: 11px; font-weight: 600;
            }}
            QPushButton:hover {{
                background: {ALMA_HOVER_LIGHT}; color: {ALMA_GREEN_LIGHT};
            }}
            QPushButton:disabled {{
                color: {ALMA_TEXT_LIGHT};
            }}
        """)
        self._view_btn.clicked.connect(self.view_all_clicked.emit)
        layout.addWidget(self._view_btn)

    # ── Public API ──

    def refresh(self):
        """Update count and last run time from DB."""
        # AI Reports page uses analysis_runs table (Build 11.0)
        if self.page_key == "ai_reports":
            count = self.db.get_report_run_count()
            if count == 0:
                self._summary_label.setText("No reports yet")
                self._view_btn.setEnabled(False)
                return
            runs = self.db.get_latest_report_runs(limit=1)
            if runs:
                run_at = runs[0].get("run_date", "")
                try:
                    dt = datetime.fromisoformat(run_at)
                    time_str = dt.strftime("%b %d, %Y %I:%M %p")
                except (ValueError, TypeError):
                    time_str = run_at
                self._summary_label.setText(
                    f"{count} report{'s' if count != 1 else ''} \u00b7 Last: {time_str}"
                )
            else:
                self._summary_label.setText(f"{count} report{'s' if count != 1 else ''}")
            self._view_btn.setEnabled(True)
            return

        # Legacy pages use analysis_reports table
        count = self.db.get_report_count(self.page_key)
        if count == 0:
            self._summary_label.setText("No reports yet")
            self._view_btn.setEnabled(False)
            return

        reports = self.db.get_reports(self.page_key, limit=1, offset=0)
        if reports:
            run_at = reports[0].get("run_at", "")
            try:
                dt = datetime.fromisoformat(run_at)
                time_str = dt.strftime("%b %d, %Y %I:%M %p")
            except (ValueError, TypeError):
                time_str = run_at

            self._summary_label.setText(
                f"{count} report{'s' if count != 1 else ''} \u00b7 Last: {time_str}"
            )
        else:
            self._summary_label.setText(f"{count} report{'s' if count != 1 else ''}")

        self._view_btn.setEnabled(True)

    def get_reports_for_drilldown(self):
        """Fetch all reports (up to 500) for display in the DrilldownPanel."""
        return self.db.get_reports(self.page_key, limit=500, offset=0)

    # ── Double-click opens drilldown ──

    def mouseDoubleClickEvent(self, event):
        if self._view_btn.isEnabled():
            self.view_all_clicked.emit()
        super().mouseDoubleClickEvent(event)
