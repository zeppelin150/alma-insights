"""
Alma Insights — Source Selector Widget
Dropdown for selecting a data source (or "All Sources") on analytics pages.
"""

import logging

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox

logger = logging.getLogger("alma.ui.source_selector")


class SourceSelector(QComboBox):
    """Dropdown showing registered data sources.

    Emits source_changed(source_id: str) when selection changes.
    source_id is "" for "All Sources" or the specific source_id.
    """

    source_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(160)
        self.addItem("All Sources", "")
        self.currentIndexChanged.connect(self._on_index_changed)

    def refresh_sources(self, conn):
        """Reload source list from source_registry."""
        current = self.currentData()
        self.blockSignals(True)
        self.clear()
        self.addItem("All Sources", "")

        try:
            from src.data.source_registry import SourceRegistry
            registry = SourceRegistry(conn)
            for src in registry.list_sources():
                label = src["source_name"]
                if src["is_default"]:
                    label += " (default)"
                count = src.get("ticket_count", 0)
                if count:
                    label += f" [{count:,}]"
                self.addItem(label, src["source_id"])
        except Exception as e:
            logger.debug("Could not load sources: %s", e)

        # Restore previous selection
        for i in range(self.count()):
            if self.itemData(i) == current:
                self.setCurrentIndex(i)
                break

        self.blockSignals(False)

    def selected_source_id(self) -> str | None:
        """Return selected source_id or None for all sources."""
        sid = self.currentData()
        return sid if sid else None

    def _on_index_changed(self, _index):
        self.source_changed.emit(self.currentData() or "")
