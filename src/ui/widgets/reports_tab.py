"""
Alma Insights — Reports Tab Building Block

Standardized "Reports" tab used as the last tab on every analysis page.
Contains KPI snapshot row + report history list + export buttons
with DrilldownPanel integration.

Usage:
    reports = ReportsTab("trending", db_manager)
    reports.set_drilldown_panel(drilldown)
    tab_widget.addTab(reports, "Reports")
"""

import json

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QFileDialog, QApplication,
)
from PySide6.QtCore import Qt, QTimer

from src.ui.widgets.kpi_card import KPICard, KPICardRow
from src.ui.widgets.tab_scroll_content import TabScrollContent
from src.ui.widgets.report_history_summary import ReportHistorySummary
from src.ui.widgets.empty_state import EmptyState
from src.ui.theme import (
    ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_BORDER, ALMA_CREAM,
)


class ReportsTab(TabScrollContent):
    """Reports tab with KPI row, report history, and export buttons.

    Inherits TabScrollContent so it's directly usable as a tab widget.
    """

    def __init__(self, report_type: str, db_manager, parent=None):
        super().__init__(parent)
        self._report_type = report_type
        self._db = db_manager
        self._drilldown = None
        self._detail_callback = None

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

        # Export button row
        self._build_export_bar(layout)

        # Empty state (shown when no reports)
        self._empty = EmptyState(
            message="No reports yet",
            icon="table",
            heading="No reports yet",
            description="Reports are generated automatically after each NLP scan.",
        )
        layout.addWidget(self._empty)

        layout.addStretch()

    def _build_export_bar(self, parent_layout):
        """Add Copy / Save .md / Save .html export buttons."""
        export_row = QHBoxLayout()
        export_row.setSpacing(8)
        export_row.addStretch()

        btn_style = f"""
            QPushButton {{
                background: transparent; color: {ALMA_TEXT_MID};
                font-size: 11px; font-weight: 600;
                border: 1px solid {ALMA_BORDER}; border-radius: 6px;
                padding: 4px 12px;
            }}
            QPushButton:hover {{ background: {ALMA_CREAM}; }}
        """

        self._export_copy_btn = QPushButton("Copy")
        self._export_copy_btn.setCursor(Qt.PointingHandCursor)
        self._export_copy_btn.setStyleSheet(btn_style)
        self._export_copy_btn.clicked.connect(self._export_copy)
        export_row.addWidget(self._export_copy_btn)

        self._export_md_btn = QPushButton("Save .md")
        self._export_md_btn.setCursor(Qt.PointingHandCursor)
        self._export_md_btn.setStyleSheet(btn_style)
        self._export_md_btn.clicked.connect(self._export_save_md)
        export_row.addWidget(self._export_md_btn)

        self._export_html_btn = QPushButton("Save .html")
        self._export_html_btn.setCursor(Qt.PointingHandCursor)
        self._export_html_btn.setStyleSheet(btn_style)
        self._export_html_btn.clicked.connect(self._export_save_html)
        export_row.addWidget(self._export_html_btn)

        self._export_frame = QFrame()
        self._export_frame.setLayout(export_row)
        self._export_frame.setStyleSheet("border: none; background: transparent;")
        self._export_frame.setVisible(False)  # hidden until reports exist
        parent_layout.addWidget(self._export_frame)

    # ── Public API ──

    def set_drilldown_panel(self, panel, detail_callback=None):
        """Wire the drilldown panel for report detail view.

        Args:
            panel: DrilldownPanel instance.
            detail_callback: Optional callable(report_id) -> HTML string.
                             If not provided, a generic markdown renderer is used.
        """
        self._drilldown = panel
        self._detail_callback = detail_callback

    @property
    def kpi_row(self) -> KPICardRow:
        return self._kpi_row

    def refresh(self):
        """Refresh report history from database."""
        self._history.refresh()

        count = self._db.get_report_count(self._report_type)
        has_reports = count > 0
        self._empty.setVisible(not has_reports)
        self._history.setVisible(has_reports)
        self._export_frame.setVisible(has_reports)

        if has_reports:
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

                # Populate metric KPIs from summary JSON (auto_scan_report)
                self._populate_summary_kpis(reports[0])
        else:
            self._kpi_total.set_value("\u2014")
            self._kpi_latest.set_value("\u2014")
            self._kpi_metric1.set_value("\u2014")
            self._kpi_metric1.set_subtitle("per report")
            self._kpi_metric2.set_value("\u2014")
            self._kpi_metric2.set_subtitle("data coverage")

    def _populate_summary_kpis(self, report):
        """Fill Avg Metrics and Coverage KPIs from report summary JSON."""
        raw = report.get("summary", "{}")
        try:
            summary = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            return

        cost = summary.get("total_cost_usd")
        if cost is not None:
            self._kpi_metric1.set_value(f"${cost:.3f}")
            self._kpi_metric1.set_subtitle("scan cost")

        trc_count = summary.get("trc_count")
        if trc_count is not None:
            self._kpi_metric2.set_value(str(trc_count))
            self._kpi_metric2.set_subtitle("TRCs scanned")

    # ── Export ──

    def _get_latest_report_markdown(self) -> str:
        """Get full markdown of the most recent report."""
        reports = self._db.get_reports(self._report_type, limit=1, offset=0)
        if reports:
            full = self._db.get_full_report(reports[0]["report_id"])
            if full:
                return full.get("full_results", "")
        return ""

    def _export_copy(self):
        """Copy latest report markdown to clipboard."""
        md = self._get_latest_report_markdown()
        if md:
            QApplication.clipboard().setText(md)
            self._export_copy_btn.setText("Copied!")
            QTimer.singleShot(2000, lambda: self._export_copy_btn.setText("Copy"))

    def _export_save_md(self):
        """Save latest report as .md file."""
        md = self._get_latest_report_markdown()
        if not md:
            return
        from src.data.app_paths import start_dir
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Report", start_dir("downloads"), "Markdown (*.md);;Text (*.txt)"
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(md)
            self._export_md_btn.setText("Saved!")
            QTimer.singleShot(2000, lambda: self._export_md_btn.setText("Save .md"))

    def _export_save_html(self):
        """Save latest report as styled .html file."""
        md = self._get_latest_report_markdown()
        if not md:
            return
        from src.data.app_paths import start_dir
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Report as HTML", start_dir("downloads"), "HTML (*.html)"
        )
        if path:
            from src.ui.widgets.markdown_viewer import md_to_html
            html_content = md_to_html(md)
            full_html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Alma Insights — Scan Report</title>
<style>
  body {{ font-family: 'Segoe UI', sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; color: #333; }}
  h1, h2, h3 {{ color: #14573F; }}
  table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
  th, td {{ border: none; padding: 8px; text-align: left; }}
  th {{ background: #f5f5f0; font-weight: 600; }}
  code {{ background: #f5f5f0; padding: 2px 6px; border-radius: 3px; }}
</style>
</head><body>
{html_content}
</body></html>"""
            with open(path, "w", encoding="utf-8") as f:
                f.write(full_html)
            self._export_html_btn.setText("Saved!")
            QTimer.singleShot(2000, lambda: self._export_html_btn.setText("Save .html"))

    # ── Drilldown ──

    def _open_report_drilldown(self):
        """Open the DrilldownPanel with full report list."""
        if not self._drilldown:
            return

        reports = self._history.get_reports_for_drilldown()
        if reports:
            cb = self._detail_callback or self._default_render_detail
            self._drilldown.show_reports(
                title=f"{self._report_type.replace('_', ' ').title()} Reports",
                subtitle=f"{len(reports)} reports",
                reports=reports,
                detail_callback=cb,
            )

    def _default_render_detail(self, report_id):
        """Generic fallback renderer: load full_results and render as markdown."""
        try:
            report = self._db.get_full_report(report_id)
            if not report:
                return "<p>Report not found.</p>"

            raw = report.get("full_results", "")
            if not raw:
                return "<p>No report content available.</p>"

            # Try JSON → report_text extraction (standard format)
            import json as _json
            try:
                data = _json.loads(raw)
                text = data.get("report_text", raw)
            except (_json.JSONDecodeError, TypeError):
                text = raw

            from src.ui.widgets.markdown_viewer import md_to_html
            return md_to_html(text)
        except Exception:
            return "<p>Error loading report.</p>"
