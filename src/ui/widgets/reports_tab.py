"""
Alma Insights — Reports Tab Building Block

Standardized "Reports" tab used as the last tab on every analysis page.
Contains KPI snapshot row + report history list with DrilldownPanel integration.

Usage:
    reports = ReportsTab("trending", db_manager)
    reports.set_drilldown_panel(drilldown)
    tab_widget.addTab(reports, "Reports")
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel,
)
from PySide6.QtCore import Qt

from src.ui.widgets.kpi_card import KPICard, KPICardRow
from src.ui.widgets.tab_scroll_content import TabScrollContent
from src.ui.widgets.report_history_summary import ReportHistorySummary
from src.ui.widgets.empty_state import EmptyState
from src.ui.theme import ALMA_TEXT_DARK


class ReportsTab(TabScrollContent):
    """Reports tab with KPI row and report history.

    Inherits TabScrollContent so it's directly usable as a tab widget.
    """

    def __init__(self, report_type: str, db_manager, parent=None):
        super().__init__(parent)
        self._report_type = report_type
        self._db = db_manager
        self._drilldown = None

        self._build_content()

    def _build_content(self):
        layout = self.content_layout

        # Section title
        title = QLabel("Report History")
        title.setObjectName("SectionHeading")
        layout.addWidget(title)

        # KPI row
        self._kpi_row = KPICardRow()
        self._kpi_total = self._kpi_row.add_card(
            KPICard("Total Reports", "\u2014", "generated reports")
        )
        self._kpi_latest = self._kpi_row.add_card(
            KPICard("Latest Report", "\u2014", "most recent run")
        )
        self._kpi_metric1 = self._kpi_row.add_card(
            KPICard("Avg Metrics", "\u2014", "per report")
        )
        self._kpi_metric2 = self._kpi_row.add_card(
            KPICard("Coverage", "\u2014", "data coverage")
        )
        layout.addWidget(self._kpi_row)

        # Report history summary (clickable row)
        self._history = ReportHistorySummary(self._db, self._report_type)
        self._history.view_all_clicked.connect(self._open_report_drilldown)
        layout.addWidget(self._history)

        # Empty state (shown when no reports)
        self._empty = EmptyState(
            message="No reports yet",
            icon="table",
            heading="No reports yet",
            description="Generate a report from the AI Reports page to see results here.",
        )
        layout.addWidget(self._empty)

        layout.addStretch()

    # ── Public API ──

    def set_drilldown_panel(self, panel):
        """Wire the drilldown panel for report detail view."""
        self._drilldown = panel

    @property
    def kpi_row(self) -> KPICardRow:
        return self._kpi_row

    def refresh(self):
        """Refresh report history from database."""
        self._history.refresh()

        count = self._db.get_report_count(self._report_type)
        self._empty.setVisible(count == 0)
        self._history.setVisible(count > 0)

        if count > 0:
            self._kpi_total.set_value(str(count))

            # Get latest report info
            reports = self._db.get_reports(self._report_type, limit=1, offset=0)
            if reports:
                from datetime import datetime
                run_at = reports[0].get("run_at", "")
                try:
                    dt = datetime.fromisoformat(run_at)
                    self._kpi_latest.set_value(dt.strftime("%b %d"))
                    self._kpi_latest.set_subtitle(dt.strftime("%I:%M %p"))
                except (ValueError, TypeError):
                    self._kpi_latest.set_value(run_at[:10] if run_at else "\u2014")
        else:
            self._kpi_total.set_value("\u2014")
            self._kpi_latest.set_value("\u2014")

    # ── Drilldown ──

    def _open_report_drilldown(self):
        """Open the DrilldownPanel with full report list."""
        if not self._drilldown:
            return

        reports = self._history.get_reports_for_drilldown()
        if reports:
            self._drilldown.show_reports(
                title=f"{self._report_type.replace('_', ' ').title()} Reports",
                subtitle=f"{len(reports)} reports",
                reports=reports,
            )
