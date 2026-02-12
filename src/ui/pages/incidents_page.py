"""
Alma Insights — Incidents Page
TRC ticket-rate anomaly detection with θ₁/θ₂ control bands.
"""

import json
import time
import sqlite3
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QDateEdit, QFrame, QScrollArea, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QProgressDialog,
    QApplication, QSizePolicy,
)
from PySide6.QtCore import Qt, QDate, QThread, Signal
from PySide6.QtGui import QColor, QFont

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT,
    ALMA_WHITE, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_BORDER, ALMA_BORDER_LIGHT,
    ALMA_SUCCESS, ALMA_WARNING, ALMA_ERROR, ALMA_INFO,
)
from src.ui.widgets.control_chart import ControlChartWidget
from src.ui.widgets.report_history import ReportHistoryWidget


# ═══════════════════════════════════════════
#  WORKER THREAD
# ═══════════════════════════════════════════

class IncidentWorker(QThread):
    finished = Signal(dict)
    error = Signal(str)
    progress = Signal(str, int)  # message, percent

    def __init__(self, db_path, target_date):
        super().__init__()
        self.db_path = db_path
        self.target_date = target_date

    def run(self):
        try:
            from src.data.db_manager import DatabaseManager
            db = DatabaseManager(self.db_path)
            db.initialize()

            from src.data.incident_engine import run_incident_scan
            result = run_incident_scan(
                db,
                target_date=self.target_date,
                progress_callback=lambda msg, pct: self.progress.emit(msg, pct),
            )
            db.close()
            self.finished.emit(result)
        except Exception as e:
            import traceback
            self.error.emit(traceback.format_exc())


class ThetaWorker(QThread):
    """Background worker for θ EWMA anomaly detection scan."""
    progress = Signal(str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, db_path):
        super().__init__()
        self.db_path = db_path

    def run(self):
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row

            from src.data.theta_engine import run_theta_scan_range

            def on_progress(step, total, msg):
                self.progress.emit(msg)

            result = run_theta_scan_range(conn, progress_callback=on_progress)
            conn.close()
            self.finished.emit(result)
        except Exception as e:
            import traceback
            self.error.emit(traceback.format_exc())


# ═══════════════════════════════════════════
#  INCIDENTS PAGE
# ═══════════════════════════════════════════

class IncidentsPage(QWidget):

    scan_complete = Signal(int)  # emits count of 2θ flags for sidebar badge

    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db = db_manager
        self._worker = None
        self._theta_worker = None
        self._progress = None
        self._scan_start_time = 0
        self._last_result = None
        self._trc_statuses = []
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Scroll area
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
        self._build_status_grid()
        self._build_control_chart()
        self._build_open_incidents()
        self._build_theta_panel()
        self._build_report_history()

        self._layout.addStretch()
        scroll.setWidget(scroll_content)
        outer.addWidget(scroll)

    # ── HEADER ──

    def _build_header(self):
        header = QLabel("Incident Monitor")
        header.setObjectName("PageHeader")
        self._layout.addWidget(header)

        sub = QLabel("TRC ticket-rate anomaly detection with 1-theta/2-theta control bands")
        sub.setObjectName("PageSubheader")
        self._layout.addWidget(sub)
        self._layout.addSpacing(20)

    # ── FILTER BAR ──

    def _build_filter_bar(self):
        card = QFrame()
        card.setObjectName("IncidentFilterCard")
        card.setStyleSheet(f"""
            #IncidentFilterCard {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 10px;
            }}
            #IncidentFilterCard QLabel {{
                color: {ALMA_TEXT_MID}; border: none; background: transparent;
            }}
            #IncidentFilterCard QComboBox {{
                color: {ALMA_TEXT_DARK}; background: {ALMA_WHITE};
                border: 1px solid {ALMA_BORDER}; border-radius: 8px;
                padding: 8px 12px; font-size: 13px;
            }}
            #IncidentFilterCard QDateEdit {{
                color: {ALMA_TEXT_DARK}; background: {ALMA_WHITE};
                border: 1px solid {ALMA_BORDER}; border-radius: 8px;
                padding: 8px 12px; font-size: 13px;
            }}
        """)
        row = QHBoxLayout(card)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(12)

        label_style = f"font-size: 11px; font-weight: 600; color: {ALMA_TEXT_MID}; letter-spacing: 0.5px;"

        # Date picker
        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("Scan Date")
        lbl.setStyleSheet(label_style)
        col.addWidget(lbl)
        self.date_picker = QDateEdit()
        self.date_picker.setCalendarPopup(True)
        self.date_picker.setDate(QDate.currentDate())
        self.date_picker.setDisplayFormat("MMM d, yyyy")
        col.addWidget(self.date_picker)
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

        # Run Scan button
        col = QVBoxLayout()
        col.setSpacing(4)
        col.addWidget(QLabel(""))  # alignment spacer
        self.scan_btn = QPushButton("  Run Scan  ")
        self.scan_btn.setMinimumHeight(36)
        self.scan_btn.setCursor(Qt.PointingHandCursor)
        self.scan_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_SUCCESS}; color: white; border: none;
                border-radius: 8px; padding: 9px 20px; font-size: 13px; font-weight: 600;
            }}
            QPushButton:hover {{ background: #128A3E; }}
        """)
        self.scan_btn.clicked.connect(self._on_run_scan)
        col.addWidget(self.scan_btn)
        row.addLayout(col, 1)

        self._layout.addWidget(card)
        self._layout.addSpacing(20)

    # ── TRC STATUS GRID ──

    def _build_status_grid(self):
        frame = QFrame()
        frame.setObjectName("StatusGridPanel")
        frame.setStyleSheet(f"""
            #StatusGridPanel {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 10px;
            }}
        """)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)

        title = QLabel("TRC Status Grid")
        title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        layout.addWidget(title)

        self.status_table = QTableWidget()
        headers = ["TRC", "Rate", "Mean", "1-theta", "2-theta", "Z-Score", "Status"]
        self.status_table.setColumnCount(len(headers))
        self.status_table.setHorizontalHeaderLabels(headers)
        self.status_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, len(headers)):
            self.status_table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self.status_table.verticalHeader().setVisible(False)
        self.status_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.status_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.status_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.status_table.setAlternatingRowColors(True)
        self.status_table.setStyleSheet(f"""
            QTableWidget {{ alternate-background-color: rgba(3,40,27,0.02); border: none; }}
        """)
        self.status_table.setMinimumHeight(200)
        self.status_table.setMaximumHeight(350)
        self.status_table.currentCellChanged.connect(self._on_trc_row_selected)
        layout.addWidget(self.status_table)

        self._layout.addWidget(frame)
        self._layout.addSpacing(16)

    # ── CONTROL CHART ──

    def _build_control_chart(self):
        frame = QFrame()
        frame.setObjectName("ChartPanel")
        frame.setStyleSheet(f"""
            #ChartPanel {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 10px;
            }}
        """)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)

        title = QLabel("Control Chart — Last 7 Days Hourly Data")
        title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        layout.addWidget(title)

        self.control_chart = ControlChartWidget()
        layout.addWidget(self.control_chart)

        self._layout.addWidget(frame)
        self._layout.addSpacing(16)

    # ── OPEN INCIDENTS ──

    def _build_open_incidents(self):
        frame = QFrame()
        frame.setObjectName("OpenIncidentsPanel")
        frame.setStyleSheet(f"""
            #OpenIncidentsPanel {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 10px;
            }}
        """)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)

        title = QLabel("Open Incidents")
        title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        layout.addWidget(title)

        self.incidents_container = QVBoxLayout()
        self.incidents_container.setSpacing(8)
        self._no_incidents_label = QLabel("No open incidents. Run a scan to check for anomalies.")
        self._no_incidents_label.setStyleSheet(f"color: {ALMA_TEXT_LIGHT}; font-size: 12px; border: none; padding: 12px;")
        self._no_incidents_label.setAlignment(Qt.AlignCenter)
        self.incidents_container.addWidget(self._no_incidents_label)
        layout.addLayout(self.incidents_container)

        self._layout.addWidget(frame)
        self._layout.addSpacing(16)

    # ── θ EWMA ANOMALY SCAN PANEL ──

    def _build_theta_panel(self):
        frame = QFrame()
        frame.setObjectName("ThetaPanel")
        frame.setStyleSheet(f"""
            #ThetaPanel {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 10px;
            }}
        """)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)

        # Header row
        header_row = QHBoxLayout()
        title = QLabel("θ Anomaly Scan")
        title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        header_row.addWidget(title)
        header_row.addStretch()

        self._theta_scan_btn = QPushButton("Run θ Scan")
        self._theta_scan_btn.setCursor(Qt.PointingHandCursor)
        self._theta_scan_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_ERROR};
                font-size: 11px; font-weight: 600; border: 1px solid {ALMA_ERROR};
                border-radius: 6px; padding: 4px 14px;
            }}
            QPushButton:hover {{ background: rgba(196,30,30,0.08); }}
        """)
        self._theta_scan_btn.clicked.connect(self._run_theta_scan)
        header_row.addWidget(self._theta_scan_btn)

        history_btn = QPushButton("View History")
        history_btn.setCursor(Qt.PointingHandCursor)
        history_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_TEXT_DARK};
                font-size: 11px; font-weight: 600; border: 1px solid {ALMA_BORDER};
                border-radius: 6px; padding: 4px 14px;
            }}
            QPushButton:hover {{ background: {ALMA_CREAM}; }}
        """)
        history_btn.clicked.connect(self._toggle_theta_history)
        header_row.addWidget(history_btn)

        layout.addLayout(header_row)

        subtitle = QLabel("EWMA baseline anomaly detection — volume, sentiment, and term frequency per TRC per day")
        subtitle.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none;")
        layout.addWidget(subtitle)
        layout.addSpacing(6)

        # Container for anomaly flag cards
        self._theta_container = QVBoxLayout()
        self._theta_container.setSpacing(8)
        layout.addLayout(self._theta_container)

        self._theta_message = QLabel("Run θ scan to detect anomalies across your ticket data...")
        self._theta_message.setStyleSheet(f"color: {ALMA_TEXT_LIGHT}; font-size: 12px; border: none;")
        self._theta_message.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._theta_message)

        self._layout.addWidget(frame)
        self._layout.addSpacing(16)

    # ── REPORT HISTORY ──

    def _build_report_history(self):
        divider = QFrame()
        divider.setObjectName("SectionDivider")
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {ALMA_BORDER_LIGHT};")
        self._layout.addWidget(divider)

        self.report_history = ReportHistoryWidget(self.db, "incidents")
        self.report_history.report_selected.connect(self._load_past_report)
        self._layout.addWidget(self.report_history)

    # ═══════════════════════════════════════════
    #  TRC FILTER POPULATION
    # ═══════════════════════════════════════════

    def populate_trc_filter(self):
        """Load TRC codes into the filter dropdown."""
        current = self.trc_combo.currentData()
        self.trc_combo.clear()
        self.trc_combo.addItem("All TRCs", "")
        trcs = self.db.get_trc_codes()
        for trc in trcs:
            self.trc_combo.addItem(f"{trc['code']} — {trc['label']}", trc["code"])
        if current:
            idx = self.trc_combo.findData(current)
            if idx >= 0:
                self.trc_combo.setCurrentIndex(idx)

    def sync_date_to_data(self):
        """Adjust the date picker to match the data range in the DB."""
        try:
            min_d, max_d = self.db.get_date_range()
            if max_d:
                d = max_d[:10]
                parts = d.split("-")
                if len(parts) == 3:
                    self.date_picker.setDate(QDate(int(parts[0]), int(parts[1]), int(parts[2])))
        except Exception:
            pass

    # ═══════════════════════════════════════════
    #  SCAN EXECUTION
    # ═══════════════════════════════════════════

    def _on_run_scan(self):
        """Run incident scan in background thread."""
        if self._worker and self._worker.isRunning():
            return

        self.populate_trc_filter()
        self.sync_date_to_data()

        # Ensure hourly counts are populated
        try:
            self.db.populate_hourly_counts()
        except Exception:
            pass

        target_date = self.date_picker.date().toString("yyyy-MM-dd")

        self._progress = QProgressDialog("Running incident scan...", None, 0, 100, self)
        self._progress.setWindowTitle("Scanning")
        self._progress.setWindowModality(Qt.WindowModal)
        self._progress.setMinimumDuration(0)
        self._progress.show()
        QApplication.processEvents()

        self._scan_start_time = time.time()
        self._worker = IncidentWorker(self.db.db_path, target_date)
        self._worker.finished.connect(self._on_scan_results)
        self._worker.error.connect(self._on_scan_error)
        self._worker.progress.connect(self._on_scan_progress)
        self._worker.start()

    def auto_run_scan(self):
        """Auto-scan triggered after data import. Uses latest data date."""
        self.sync_date_to_data()
        target_date = self.date_picker.date().toString("yyyy-MM-dd")

        if self._worker and self._worker.isRunning():
            return

        self._scan_start_time = time.time()
        self._worker = IncidentWorker(self.db.db_path, target_date)
        self._worker.finished.connect(self._on_scan_results)
        self._worker.error.connect(self._on_scan_error)
        self._worker.start()

    def _on_scan_progress(self, msg, pct):
        if hasattr(self, "_progress"):
            self._progress.setLabelText(msg)
            self._progress.setValue(pct)
            QApplication.processEvents()

    def _on_scan_results(self, result):
        """Handle scan results."""
        if hasattr(self, "_progress") and self._progress is not None:
            self._progress.close()
            self._progress = None

        elapsed_ms = int((time.time() - self._scan_start_time) * 1000)
        self._last_result = result

        # Apply TRC filter
        trc_filter = self.trc_combo.currentData() or None
        statuses = result.get("trc_statuses", [])
        if trc_filter:
            statuses = [s for s in statuses if s["trc_code"] == trc_filter]

        # Populate grid
        self._populate_status_grid(statuses)

        # Populate open incidents
        self._populate_open_incidents()

        # Select first flagged TRC for chart, or first TRC
        self._auto_select_chart(statuses)

        # Save report
        theta_2_count = result.get("theta_2_count", 0)
        theta_1_count = result.get("theta_1_count", 0)
        summary = {
            "theta_2_count": theta_2_count,
            "theta_1_count": theta_1_count,
            "trcs_scanned": result.get("trcs_scanned", 0),
        }
        self.db.save_report(
            page="incidents",
            parameters={"scan_date": result.get("scan_date", "")},
            summary=summary,
            full_results=json.dumps(result, default=str),
            ticket_count=0,
            duration_ms=elapsed_ms,
        )
        self.report_history.refresh()

        # Emit badge signal
        self.scan_complete.emit(theta_2_count)

    def _on_scan_error(self, error_text):
        if hasattr(self, "_progress") and self._progress is not None:
            self._progress.close()
            self._progress = None
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(self, "Scan Error", f"Incident scan failed:\n\n{error_text}")

    # ═══════════════════════════════════════════
    #  STATUS GRID
    # ═══════════════════════════════════════════

    def _populate_status_grid(self, statuses):
        """Fill the TRC status grid table."""
        # Sort: flagged TRCs first (2θ, then 1θ), then by |z_score| desc
        statuses = sorted(
            statuses,
            key=lambda s: (-s["flag_level"], -abs(s["z_score"]))
        )

        self.status_table.setSortingEnabled(False)
        self.status_table.setRowCount(len(statuses))
        self._trc_statuses = statuses  # store for chart selection

        for i, s in enumerate(statuses):
            # TRC
            self.status_table.setItem(i, 0, QTableWidgetItem(s["trc_code"]))

            # Rate
            item = QTableWidgetItem(f"{s['current_rate']:.0f}/hr")
            item.setData(Qt.UserRole, s["current_rate"])
            self.status_table.setItem(i, 1, item)

            # Mean
            item = QTableWidgetItem(f"{s['hourly_mean']:.1f}")
            item.setData(Qt.UserRole, s["hourly_mean"])
            self.status_table.setItem(i, 2, item)

            # θ₁ Band
            item = QTableWidgetItem(f"{s['theta_1_upper']:.1f}")
            item.setData(Qt.UserRole, s["theta_1_upper"])
            self.status_table.setItem(i, 3, item)

            # θ₂ Band
            item = QTableWidgetItem(f"{s['theta_2_upper']:.1f}")
            item.setData(Qt.UserRole, s["theta_2_upper"])
            self.status_table.setItem(i, 4, item)

            # Z-Score
            z = s["z_score"]
            z_text = f"{z:+.1f}s"
            item = QTableWidgetItem(z_text)
            item.setData(Qt.UserRole, z)
            if abs(z) >= 2.0:
                item.setForeground(QColor(ALMA_ERROR))
            elif abs(z) >= 1.0:
                item.setForeground(QColor(ALMA_WARNING))
            else:
                item.setForeground(QColor(ALMA_SUCCESS))
            self.status_table.setItem(i, 5, item)

            # Status
            level = s["flag_level"]
            if level >= 2:
                status_text = "2-theta"
                item = QTableWidgetItem(status_text)
                item.setForeground(QColor(ALMA_ERROR))
                # Light red background for entire row
                for col in range(7):
                    cell = self.status_table.item(i, col)
                    if cell:
                        cell.setBackground(QColor(ALMA_ERROR).lighter(190))
            elif level >= 1:
                status_text = "1-theta Watch"
                item = QTableWidgetItem(status_text)
                item.setForeground(QColor(ALMA_WARNING))
                for col in range(7):
                    cell = self.status_table.item(i, col)
                    if cell:
                        cell.setBackground(QColor(ALMA_WARNING).lighter(190))
            else:
                if s.get("insufficient_data"):
                    status_text = "No Data"
                    item = QTableWidgetItem(status_text)
                    item.setForeground(QColor(ALMA_TEXT_LIGHT))
                else:
                    status_text = "OK"
                    item = QTableWidgetItem(status_text)
                    item.setForeground(QColor(ALMA_SUCCESS))

            self.status_table.setItem(i, 6, item)

        self.status_table.setSortingEnabled(True)

    def _on_trc_row_selected(self, current_row, current_col, prev_row, prev_col):
        """When a TRC row is clicked, update the control chart."""
        if current_row < 0 or current_row >= len(self._trc_statuses):
            return
        status = self._trc_statuses[current_row]
        self.control_chart.set_data(status)

    def _auto_select_chart(self, statuses):
        """Select the first flagged TRC for the chart, or the first TRC."""
        if not statuses:
            self.control_chart.clear_data()
            return

        # Find first 2θ, then first 1θ, then first TRC
        target = None
        for s in statuses:
            if s["flag_level"] == 2:
                target = s
                break
        if target is None:
            for s in statuses:
                if s["flag_level"] == 1:
                    target = s
                    break
        if target is None:
            # Highest volume
            target = max(statuses, key=lambda s: s["current_rate"])

        self.control_chart.set_data(target)

        # Select the corresponding row in the table
        for i, s in enumerate(statuses):
            if s["trc_code"] == target["trc_code"]:
                self.status_table.selectRow(i)
                break

    # ═══════════════════════════════════════════
    #  OPEN INCIDENTS
    # ═══════════════════════════════════════════

    def _populate_open_incidents(self):
        """Build cards for open/acknowledged incident flags."""
        # Clear existing cards
        while self.incidents_container.count() > 0:
            item = self.incidents_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        from src.data.incident_engine import get_open_flags
        flags = get_open_flags(self.db, limit=50)

        if not flags:
            no_label = QLabel("No open incidents.")
            no_label.setStyleSheet(f"color: {ALMA_TEXT_LIGHT}; font-size: 12px; border: none; padding: 12px;")
            no_label.setAlignment(Qt.AlignCenter)
            self.incidents_container.addWidget(no_label)
            return

        for flag in flags:
            card = self._build_incident_card(flag)
            self.incidents_container.addWidget(card)

    def _build_incident_card(self, flag):
        """Build a single incident card with action buttons."""
        card = QFrame()
        level = flag["theta_level"]
        border_color = ALMA_ERROR if level >= 2 else ALMA_WARNING
        bg_tint = "rgba(196, 30, 30, 0.04)" if level >= 2 else "rgba(180, 83, 9, 0.04)"

        card.setStyleSheet(f"""
            QFrame {{
                background: {bg_tint};
                border: 1px solid {ALMA_BORDER_LIGHT};
                border-left: 4px solid {border_color};
                border-radius: 8px;
                padding: 12px 16px;
            }}
        """)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Description
        severity = "Incident" if level >= 2 else "Watch"
        direction = flag.get("direction", "above")
        z = flag.get("z_score", 0)
        rate = flag.get("observed_rate", 0)
        mean = flag.get("expected_mean", 0)
        desc = (
            f"{severity}: {flag['trc_code']} — {abs(z):.1f}s {direction} normal "
            f"({rate:.0f}/hr vs {mean:.1f} avg)"
        )
        desc_label = QLabel(desc)
        desc_label.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {ALMA_TEXT_DARK}; border: none; background: transparent;")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

        # Trigger time + status
        triggered = flag.get("triggered_at", "")
        try:
            dt = datetime.fromisoformat(triggered)
            time_str = dt.strftime("%b %d %H:%M")
        except (ValueError, TypeError):
            time_str = triggered
        status = flag.get("status", "open")
        meta_text = f"Triggered: {time_str}  |  Status: {status}"
        meta_label = QLabel(meta_text)
        meta_label.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none; background: transparent;")
        layout.addWidget(meta_label)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        flag_id = flag["flag_id"]

        if status == "open":
            ack_btn = QPushButton("Acknowledge")
            ack_btn.setObjectName("SecondaryButton")
            ack_btn.setCursor(Qt.PointingHandCursor)
            ack_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {ALMA_INFO};
                    border: 1px solid {ALMA_INFO}; border-radius: 6px;
                    padding: 4px 12px; font-size: 11px; font-weight: 500;
                }}
                QPushButton:hover {{ background: rgba(29,111,165,0.08); }}
            """)
            ack_btn.clicked.connect(lambda checked, fid=flag_id: self._action_acknowledge(fid))
            btn_row.addWidget(ack_btn)

        resolve_btn = QPushButton("Resolve")
        resolve_btn.setCursor(Qt.PointingHandCursor)
        resolve_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_SUCCESS};
                border: 1px solid {ALMA_SUCCESS}; border-radius: 6px;
                padding: 4px 12px; font-size: 11px; font-weight: 500;
            }}
            QPushButton:hover {{ background: rgba(22,118,58,0.08); }}
        """)
        resolve_btn.clicked.connect(lambda checked, fid=flag_id: self._action_resolve(fid))
        btn_row.addWidget(resolve_btn)

        fp_btn = QPushButton("False Positive")
        fp_btn.setCursor(Qt.PointingHandCursor)
        fp_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_TEXT_LIGHT};
                border: 1px solid {ALMA_BORDER}; border-radius: 6px;
                padding: 4px 12px; font-size: 11px; font-weight: 500;
            }}
            QPushButton:hover {{ background: rgba(0,0,0,0.04); }}
        """)
        fp_btn.clicked.connect(lambda checked, fid=flag_id: self._action_false_positive(fid))
        btn_row.addWidget(fp_btn)

        layout.addLayout(btn_row)
        return card

    def _action_acknowledge(self, flag_id):
        from src.data.incident_engine import acknowledge_flag
        acknowledge_flag(self.db, flag_id)
        self._populate_open_incidents()
        self._emit_badge_count()

    def _action_resolve(self, flag_id):
        from src.data.incident_engine import resolve_flag
        resolve_flag(self.db, flag_id)
        self._populate_open_incidents()
        self._emit_badge_count()

    def _action_false_positive(self, flag_id):
        from src.data.incident_engine import mark_false_positive
        mark_false_positive(self.db, flag_id)
        self._populate_open_incidents()
        self._emit_badge_count()

    def _emit_badge_count(self):
        """Count open 2θ flags and emit signal for sidebar badge."""
        count = self.db.conn.execute(
            "SELECT COUNT(*) as cnt FROM incident_flags WHERE status = 'open' AND theta_level = 2"
        ).fetchone()["cnt"]
        self.scan_complete.emit(count)

    # ═══════════════════════════════════════════
    #  θ EWMA SCAN
    # ═══════════════════════════════════════════

    def _run_theta_scan(self):
        """Launch θ EWMA anomaly detection scan in background thread."""
        if self._theta_worker and self._theta_worker.isRunning():
            return

        self._theta_scan_btn.setEnabled(False)
        self._theta_scan_btn.setText("Scanning...")
        self._theta_message.setText("Running θ anomaly scan...")
        self._theta_message.show()

        self._theta_worker = ThetaWorker(self.db.db_path)
        self._theta_worker.progress.connect(self._on_theta_progress)
        self._theta_worker.finished.connect(self._on_theta_results)
        self._theta_worker.error.connect(self._on_theta_error)
        self._theta_worker.start()

    def auto_run_theta_scan(self):
        """Run theta scan silently (called after data import)."""
        if self._theta_worker and self._theta_worker.isRunning():
            return
        self._theta_worker = ThetaWorker(self.db.db_path)
        self._theta_worker.finished.connect(self._on_theta_results)
        self._theta_worker.error.connect(lambda _: None)  # Silent
        self._theta_worker.start()

    def _on_theta_progress(self, msg):
        self._theta_message.setText(msg)

    def _on_theta_results(self, result):
        self._theta_scan_btn.setEnabled(True)
        self._theta_scan_btn.setText("Run θ Scan")

        flags = result.get("flags", [])
        theta_2 = result.get("theta_2_count", 0)

        self._theta_date_range = result.get("date_range", "")
        self._theta_days_scanned = result.get("days_scanned", 0)

        self._populate_theta_flags(flags)

    def _on_theta_error(self, error_text):
        self._theta_scan_btn.setEnabled(True)
        self._theta_scan_btn.setText("Run θ Scan")
        self._theta_message.setText(f"θ scan failed: {error_text[:100]}")
        self._theta_message.show()

    def _populate_theta_flags(self, flags):
        """Display EWMA anomaly flags as colored cards."""
        self._clear_layout(self._theta_container)

        if not flags:
            date_range = getattr(self, "_theta_date_range", "")
            days_scanned = getattr(self, "_theta_days_scanned", 0)
            range_info = f" ({date_range}, {days_scanned} days)" if date_range else ""
            self._theta_message.setText(
                f"No anomalies detected — all metrics within normal range{range_info}"
            )
            self._theta_message.show()
            return

        self._theta_message.hide()

        # Sort: 2θ first, then 1θ, then by abs(z_score)
        flags_sorted = sorted(flags, key=lambda f: (-f["theta_level"], -abs(f["z_score"])))

        for flag in flags_sorted[:20]:
            card = self._make_theta_card(flag)
            self._theta_container.addWidget(card)

        # Footer summary
        theta_2_count = sum(1 for f in flags if f["theta_level"] == 2)
        theta_1_count = sum(1 for f in flags if f["theta_level"] == 1)
        date_range = getattr(self, "_theta_date_range", "")
        days_scanned = getattr(self, "_theta_days_scanned", 0)
        range_info = f"  |  {date_range} ({days_scanned} days)" if date_range else ""
        footer = QLabel(
            f"Total: {theta_2_count} critical (2θ) + {theta_1_count} watch (1θ) anomalies{range_info}"
        )
        footer.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; border: none; margin-top: 4px;")
        footer.setAlignment(Qt.AlignCenter)
        self._theta_container.addWidget(footer)

    def _make_theta_card(self, flag):
        """Create a single EWMA anomaly flag card."""
        card = QFrame()
        theta = flag["theta_level"]
        border_color = ALMA_ERROR if theta == 2 else ALMA_WARNING

        card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_WHITE};
                border: 1px solid {ALMA_BORDER_LIGHT};
                border-left: 4px solid {border_color};
                border-radius: 6px;
                padding: 8px;
            }}
        """)

        outer = QHBoxLayout(card)
        outer.setContentsMargins(12, 8, 12, 8)
        outer.setSpacing(12)

        # θ badge
        badge_bg = "rgba(196,30,30,0.15)" if theta == 2 else "rgba(180,83,9,0.15)"
        badge_color = ALMA_ERROR if theta == 2 else ALMA_WARNING
        badge = QLabel(f"{theta}θ")
        badge.setStyleSheet(
            f"background: {badge_bg}; color: {badge_color}; "
            f"border-radius: 12px; padding: 4px 10px; "
            f"font-size: 14px; font-weight: 800; border: none; min-width: 36px;"
        )
        badge.setAlignment(Qt.AlignCenter)
        outer.addWidget(badge)

        # Text content
        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        trc = flag.get("trc_code", "")
        metric = flag.get("metric_type", "")
        metric_key = flag.get("metric_key", "")
        flag_date = flag.get("date", "")
        metric_display = {
            "volume": "Volume",
            "volume_per_hour": "Ticket Rate",
            "sentiment": "Sentiment",
            "term_freq": "Term Frequency",
        }.get(metric, metric)
        header_text = f"{trc}  •  {metric_display}"
        if metric_key:
            header_text += f": {metric_key}"
        if flag_date:
            header_text += f"  •  {flag_date}"
        header = QLabel(header_text)
        header.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;")
        text_col.addWidget(header)

        observed = flag.get("observed", 0)
        expected = flag.get("expected_mean", 0)
        std = flag.get("expected_std", 0)
        z = flag.get("z_score", 0)
        if metric == "volume":
            detail_text = (
                f"Volume: {int(observed)} tickets  |  "
                f"Normal: {expected:.0f} \u00b1 {std:.1f}  |  z = {z:+.2f}"
            )
        elif metric == "volume_per_hour":
            detail_text = (
                f"Rate: {observed:.1f}/hr  |  "
                f"Normal: {expected:.1f} \u00b1 {std:.1f}/hr  |  z = {z:+.2f}"
            )
        elif metric == "sentiment":
            detail_text = (
                f"Score: {observed:+.2f}  |  "
                f"Normal: {expected:+.2f} \u00b1 {std:.2f}  |  z = {z:+.2f}"
            )
        else:
            detail_text = (
                f"Observed: {observed:.3f}  |  "
                f"Expected: {expected:.3f} \u00b1 {std:.3f}  |  z = {z:+.2f}"
            )
        detail = QLabel(detail_text)
        detail.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_MID}; border: none;")
        text_col.addWidget(detail)

        interp = QLabel(flag.get("interpretation", ""))
        interp.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; border: none;")
        interp.setWordWrap(True)
        text_col.addWidget(interp)

        outer.addLayout(text_col, 1)

        # Action buttons
        btn_col = QVBoxLayout()
        btn_col.setSpacing(4)

        ack_btn = QPushButton("Acknowledge")
        ack_btn.setStyleSheet(
            f"background: transparent; font-size: 10px; color: {ALMA_INFO}; "
            f"border: 1px solid {ALMA_INFO}; border-radius: 4px; padding: 2px 8px;"
        )
        ack_btn.setCursor(Qt.PointingHandCursor)
        flag_data = dict(flag)
        ack_btn.clicked.connect(lambda _, f=flag_data: self._acknowledge_theta_flag(f))
        btn_col.addWidget(ack_btn)

        fp_btn = QPushButton("False +")
        fp_btn.setStyleSheet(
            f"background: transparent; font-size: 10px; color: {ALMA_TEXT_LIGHT}; "
            f"border: 1px solid {ALMA_BORDER}; border-radius: 4px; padding: 2px 8px;"
        )
        fp_btn.setCursor(Qt.PointingHandCursor)
        fp_btn.clicked.connect(lambda _, f=flag_data: self._false_positive_theta_flag(f))
        btn_col.addWidget(fp_btn)

        outer.addLayout(btn_col)

        return card

    def _acknowledge_theta_flag(self, flag):
        """Mark an EWMA theta flag as acknowledged."""
        try:
            conn = sqlite3.connect(str(self.db.db_path))
            conn.row_factory = sqlite3.Row
            from src.data.theta_engine import acknowledge_flag

            row = conn.execute(
                "SELECT flag_id FROM anomaly_flags WHERE date = ? AND trc_code = ? "
                "AND metric_type = ? AND metric_key = ? AND status = 'open' LIMIT 1",
                (flag.get("date", ""), flag.get("trc_code", ""),
                 flag.get("metric_type", ""), flag.get("metric_key", ""))
            ).fetchone()
            if row:
                fid = row["flag_id"] if hasattr(row, "keys") else row[0]
                acknowledge_flag(conn, fid)
            conn.close()
        except Exception:
            pass

    def _false_positive_theta_flag(self, flag):
        """Mark an EWMA theta flag as false positive."""
        try:
            conn = sqlite3.connect(str(self.db.db_path))
            conn.row_factory = sqlite3.Row
            from src.data.theta_engine import mark_false_positive

            row = conn.execute(
                "SELECT flag_id FROM anomaly_flags WHERE date = ? AND trc_code = ? "
                "AND metric_type = ? AND metric_key = ? AND status = 'open' LIMIT 1",
                (flag.get("date", ""), flag.get("trc_code", ""),
                 flag.get("metric_type", ""), flag.get("metric_key", ""))
            ).fetchone()
            if row:
                fid = row["flag_id"] if hasattr(row, "keys") else row[0]
                mark_false_positive(conn, fid)
            conn.close()
        except Exception:
            pass

    def _toggle_theta_history(self):
        """Show a dialog with historical EWMA theta flags."""
        try:
            conn = sqlite3.connect(str(self.db.db_path))
            conn.row_factory = sqlite3.Row
            from src.data.theta_engine import get_flag_history

            flags = get_flag_history(conn, days=30, limit=200)
            conn.close()
        except Exception:
            flags = []

        from PySide6.QtWidgets import QDialog, QDialogButtonBox

        dlg = QDialog(self)
        dlg.setWindowTitle("θ Flag History — Last 30 Days")
        dlg.setMinimumSize(800, 500)
        dlg_layout = QVBoxLayout(dlg)

        table = QTableWidget()
        table.setColumnCount(8)
        table.setHorizontalHeaderLabels([
            "Date", "TRC", "Metric", "Key", "θ Level", "Z-Score", "Status", "Notes"
        ])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        for i in range(4, 8):
            table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeToContents)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        table.setRowCount(len(flags))
        for i, f in enumerate(flags):
            table.setItem(i, 0, QTableWidgetItem(f.get("date", "")))
            table.setItem(i, 1, QTableWidgetItem(f.get("trc_code", "")))
            table.setItem(i, 2, QTableWidgetItem(f.get("metric_type", "")))
            table.setItem(i, 3, QTableWidgetItem(f.get("metric_key", "")))

            theta_item = QTableWidgetItem(f"{f.get('theta_level', '')}θ")
            theta_level = f.get("theta_level", 0)
            theta_item.setForeground(QColor(ALMA_ERROR if theta_level == 2 else ALMA_WARNING))
            table.setItem(i, 4, theta_item)

            z = f.get("z_score", 0)
            table.setItem(i, 5, QTableWidgetItem(f"{z:+.2f}" if z else ""))

            status = f.get("status", "")
            status_item = QTableWidgetItem(status)
            status_colors = {
                "open": ALMA_ERROR, "acknowledged": ALMA_INFO,
                "resolved": ALMA_SUCCESS, "false_positive": ALMA_TEXT_LIGHT,
            }
            status_item.setForeground(QColor(status_colors.get(status, ALMA_TEXT_MID)))
            table.setItem(i, 6, status_item)

            table.setItem(i, 7, QTableWidgetItem(f.get("notes", "")))

        dlg_layout.addWidget(table)

        btn = QDialogButtonBox(QDialogButtonBox.Close)
        btn.rejected.connect(dlg.reject)
        dlg_layout.addWidget(btn)

        dlg.exec()

    @staticmethod
    def _clear_layout(layout):
        """Remove all widgets from a layout."""
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    # ═══════════════════════════════════════════
    #  REPORT HISTORY — RELOAD PAST REPORT
    # ═══════════════════════════════════════════

    def _load_past_report(self, report_id):
        """Reload a past scan's results."""
        report = self.db.get_full_report(report_id)
        if not report or not report.get("full_results"):
            return
        try:
            result = json.loads(report["full_results"])
        except (json.JSONDecodeError, TypeError):
            return

        self._last_result = result
        statuses = result.get("trc_statuses", [])

        # Apply TRC filter
        trc_filter = self.trc_combo.currentData() or None
        if trc_filter:
            statuses = [s for s in statuses if s["trc_code"] == trc_filter]

        self._populate_status_grid(statuses)
        self._auto_select_chart(statuses)
        # Don't reload open incidents — those are live state
