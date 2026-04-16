"""
Alma Insights — AI Reports: Report History Tab (Build 11.0)

Searchable, filterable table of past report runs from analysis_runs table.
Filter pills: All, VOC, Billing, Engineering, Custom.
Actions per row: View (loads in Analysis Canvas), Copy, Export.
"""

import sqlite3
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFrame, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_MID, ALMA_GREEN_LIGHT,
    ALMA_WHITE, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID,
    ALMA_BORDER, ALMA_BORDER_LIGHT, ALMA_BG_ELEVATED,
    apply_card_shadow_soft,
)


class ReportHistoryTab(QWidget):
    """Report history table backed by the analysis_runs table."""

    view_report_requested = Signal(str)  # run_id

    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db = db_manager
        self._active_filter = "All"
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 16, 28, 24)
        layout.setSpacing(12)

        # ── Filter pills + search ──
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        self._filter_buttons = {}
        for label in ("All", "VOC", "Billing", "Engineering", "Custom"):
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setCheckable(True)
            btn.setChecked(label == "All")
            btn.clicked.connect(lambda checked, l=label: self._on_filter(l))
            btn.setStyleSheet(self._pill_style())
            filter_row.addWidget(btn)
            self._filter_buttons[label] = btn

        filter_row.addStretch()

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search reports...")
        self._search.setStyleSheet(f"""
            QLineEdit {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER};
                border-radius: 8px; padding: 8px 14px; font-size: 13px;
                color: {ALMA_TEXT_DARK}; min-width: 200px;
            }}
        """)
        self._search.textChanged.connect(self._refresh)
        filter_row.addWidget(self._search)

        layout.addLayout(filter_row)

        # ── Table ──
        self._table = QTableWidget()
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels([
            "Report", "Date", "Tickets", "TRC Filter",
            "Duration", "Cost", "Actions",
        ])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, 7):
            self._table.horizontalHeader().setSectionResizeMode(
                i, QHeaderView.ResizeToContents
            )
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setStyleSheet(f"""
            QTableWidget {{
                background: {ALMA_BG_ELEVATED};
                border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 8px;
                gridline-color: {ALMA_BORDER_LIGHT};
                font-size: 12px;
            }}
            QTableWidget::item {{
                padding: 6px 8px;
                color: {ALMA_TEXT_DARK};
            }}
            QHeaderView::section {{
                background: {ALMA_CREAM};
                color: {ALMA_TEXT_MID};
                font-weight: 600;
                font-size: 11px;
                padding: 6px 8px;
                border: none;
                border-bottom: 1px solid {ALMA_BORDER_LIGHT};
            }}
        """)
        layout.addWidget(self._table)

        self._refresh()

    def _pill_style(self):
        return f"""
            QPushButton {{
                background: {ALMA_CREAM};
                color: {ALMA_TEXT_MID};
                border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 14px;
                padding: 5px 14px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:checked {{
                background: {ALMA_GREEN_DARK};
                color: white;
                border-color: {ALMA_GREEN_DARK};
            }}
            QPushButton:hover {{
                background: {ALMA_GREEN_LIGHT};
            }}
        """

    def _on_filter(self, label):
        self._active_filter = label
        for name, btn in self._filter_buttons.items():
            btn.setChecked(name == label)
        self._refresh()

    def _refresh(self):
        """Reload table from analysis_runs."""
        try:
            conn = self.db.conn
            query = "SELECT run_id, prompt_template, run_date, ticket_count, trc_filter, duration_sec, cost_usd FROM analysis_runs"
            conditions = []
            params = []

            search = self._search.text().strip()
            if search:
                conditions.append("(prompt_template LIKE ? OR trc_filter LIKE ? OR output_text LIKE ?)")
                params.extend([f"%{search}%"] * 3)

            if self._active_filter != "All":
                fmap = {
                    "VOC": "voc%",
                    "Billing": "%billing%",
                    "Engineering": "%eng%",
                    "Custom": "%custom%",
                }
                if self._active_filter in fmap:
                    conditions.append("prompt_template LIKE ?")
                    params.append(fmap[self._active_filter])

            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY run_date DESC LIMIT 100"

            rows = conn.execute(query, params).fetchall()
        except Exception:
            rows = []

        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            run_id = row[0] if isinstance(row, tuple) else row["run_id"]
            template = row[1] if isinstance(row, tuple) else row["prompt_template"]
            date = row[2] if isinstance(row, tuple) else row["run_date"]
            tickets = row[3] if isinstance(row, tuple) else row["ticket_count"]
            trc = row[4] if isinstance(row, tuple) else row["trc_filter"]
            duration = row[5] if isinstance(row, tuple) else row["duration_sec"]
            cost = row[6] if isinstance(row, tuple) else row["cost_usd"]

            self._table.setItem(i, 0, QTableWidgetItem(str(template or "")))

            # Format date
            date_str = str(date or "")
            if "T" in date_str:
                date_str = date_str.split("T")[0]
            self._table.setItem(i, 1, QTableWidgetItem(date_str))

            self._table.setItem(i, 2, QTableWidgetItem(str(tickets or "")))
            self._table.setItem(i, 3, QTableWidgetItem(str(trc or "All TRCs")))

            dur_str = f"{duration:.0f}s" if duration else ""
            self._table.setItem(i, 4, QTableWidgetItem(dur_str))

            cost_str = f"${cost:.2f}" if cost else ""
            self._table.setItem(i, 5, QTableWidgetItem(cost_str))

            # Actions: View button
            view_btn = QPushButton("View")
            view_btn.setCursor(Qt.PointingHandCursor)
            view_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {ALMA_GREEN_DARK};
                    border: 1px solid {ALMA_GREEN_DARK}; border-radius: 4px;
                    padding: 3px 10px; font-size: 11px; font-weight: 600;
                }}
                QPushButton:hover {{ background: {ALMA_CREAM}; }}
            """)
            view_btn.clicked.connect(lambda _, rid=run_id: self.view_report_requested.emit(rid))
            self._table.setCellWidget(i, 6, view_btn)

    def showEvent(self, event):
        """Refresh data when tab becomes visible."""
        super().showEvent(event)
        self._refresh()
