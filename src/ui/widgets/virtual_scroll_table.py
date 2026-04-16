"""
Alma Insights — Virtual Scroll Table Widget
QAbstractTableModel with lazy loading via fetchMore().
Loads PAGE_SIZE=100 rows on demand for the Data Warehouse page.

Session 4: Multi-source persistent database architecture.
"""

import logging

from PySide6.QtCore import Qt, QModelIndex, QAbstractTableModel, Signal
from PySide6.QtWidgets import (
    QTableView, QHeaderView, QAbstractItemView, QVBoxLayout, QWidget, QSizePolicy,
)

logger = logging.getLogger("alma.ui.virtual_scroll_table")


class WarehouseTableModel(QAbstractTableModel):
    """Lazy-loading model for warehouse data.

    Fetches rows in pages of PAGE_SIZE. Virtual scroll triggers
    fetchMore() when user scrolls near the bottom of loaded data.
    """

    PAGE_SIZE = 100

    COLUMNS = [
        ("ticket_id", "ID"),
        ("created_at", "Date"),
        ("trc_code", "TRC"),
        ("subject", "Subject"),
        ("source_name", "Source"),
        ("status", "Status"),
        ("csat_score", "CSAT"),
    ]

    def __init__(self, warehouse_query=None, parent=None):
        super().__init__(parent)
        self.wq = warehouse_query
        self._rows = []
        self._total_count = 0
        self._fetched = 0
        self._filters = {}

    # ── Qt Model Interface ────────────────────────

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self.COLUMNS)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or role != Qt.DisplayRole:
            return None
        row = self._rows[index.row()]
        col_key = self.COLUMNS[index.column()][0]
        value = row.get(col_key, "")

        # Format date for display
        if col_key == "created_at" and value:
            return str(value)[:10]
        if col_key == "csat_score" and value is not None:
            try:
                return f"{float(value):.1f}"
            except (ValueError, TypeError):
                return str(value)
        return str(value) if value is not None else ""

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole or orientation != Qt.Horizontal:
            return None
        if 0 <= section < len(self.COLUMNS):
            return self.COLUMNS[section][1]
        return None

    # ── Lazy Loading ──────────────────────────────

    def canFetchMore(self, parent=QModelIndex()):
        if parent.isValid():
            return False
        return self._fetched < self._total_count

    def fetchMore(self, parent=QModelIndex()):
        if parent.isValid() or not self.wq:
            return

        new_rows, total = self.wq.get_conversations_paged(
            offset=self._fetched,
            limit=self.PAGE_SIZE,
            **self._filters,
        )
        if not new_rows:
            self._total_count = self._fetched  # No more data
            return

        begin = len(self._rows)
        self.beginInsertRows(parent, begin, begin + len(new_rows) - 1)
        self._rows.extend(new_rows)
        self._fetched += len(new_rows)
        self._total_count = total
        self.endInsertRows()

    # ── Public API ────────────────────────────────

    def load_initial(self):
        """Load the first page of data."""
        if not self.wq:
            return
        self.beginResetModel()
        self._rows = []
        self._fetched = 0
        self._total_count = 0
        self.endResetModel()
        self.fetchMore()

    def set_filters(self, **filters):
        """Apply new filters and reload from page 1."""
        # Strip None values
        self._filters = {k: v for k, v in filters.items() if v is not None and v != ""}
        self.load_initial()

    def get_row_data(self, row_index):
        """Get the full dict for a given row index."""
        if 0 <= row_index < len(self._rows):
            return self._rows[row_index]
        return None

    @property
    def total_count(self):
        return self._total_count

    @property
    def loaded_count(self):
        return len(self._rows)


class VirtualScrollTable(QWidget):
    """Table view with virtual scroll for the Data Warehouse page.

    Wraps QTableView + WarehouseTableModel. Emits row_selected
    when the user clicks a row.
    """

    row_selected = Signal(dict)  # Emits the selected row's data dict

    def __init__(self, warehouse_query=None, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._model = WarehouseTableModel(warehouse_query, self)
        self._table = QTableView()
        self._table.setModel(self._model)

        # Style
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.setSortingEnabled(False)

        # Column sizing
        header = self._table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(3, QHeaderView.Stretch)  # Subject col stretches
        # Set initial column widths
        col_widths = [80, 90, 80, 280, 90, 80, 60]
        for i, w in enumerate(col_widths):
            if i < self._model.columnCount():
                header.resizeSection(i, w)

        # Selection signal
        self._table.selectionModel().currentRowChanged.connect(self._on_row_changed)

        layout.addWidget(self._table)

    def _on_row_changed(self, current, _previous):
        if current.isValid():
            data = self._model.get_row_data(current.row())
            if data:
                self.row_selected.emit(data)

    @property
    def model(self):
        return self._model

    def set_warehouse_query(self, wq):
        """Update the warehouse query instance."""
        self._model.wq = wq

    def load_initial(self):
        self._model.load_initial()

    def set_filters(self, **filters):
        self._model.set_filters(**filters)
