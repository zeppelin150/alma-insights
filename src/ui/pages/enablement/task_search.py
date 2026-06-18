"""Task search — find a card draft to open as a Workbench workspace.

A lightweight modal: type to filter your drafts (enablement_store.search_drafts),
pick one to open it. Keeps the Workbench a "choose what to work on" surface rather
than auto-displaying everything pending.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout,
)

from src.ui.theme import (
    ALMA_BG_ELEVATED, ALMA_BORDER, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_LIGHT,
)


class TaskSearchDialog(QDialog):
    """Search drafts and pick one. After exec(), ``selected_draft_id`` holds the
    chosen draft id (or None if cancelled)."""

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self._conn = conn
        self.selected_draft_id = None
        self.setWindowTitle("Find a task to work on")
        self.setStyleSheet(f"background:{ALMA_CREAM};")
        self.resize(480, 440)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(10)

        head = QLabel("Search your card drafts")
        head.setStyleSheet(
            f"color:{ALMA_TEXT_DARK}; font-size:15px; font-weight:700; border:none;")
        v.addWidget(head)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Type a title or topic…")
        self._search.setStyleSheet(
            f"QLineEdit{{background:{ALMA_BG_ELEVATED}; border:1px solid {ALMA_BORDER}; "
            f"border-radius:8px; padding:8px 12px; font-size:13px; color:{ALMA_TEXT_DARK};}}"
        )
        self._search.textChanged.connect(self._reload)
        self._search.returnPressed.connect(self._activate_first)
        v.addWidget(self._search)

        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget{{background:{ALMA_BG_ELEVATED}; border:1px solid {ALMA_BORDER}; "
            f"border-radius:8px; font-size:13px; color:{ALMA_TEXT_DARK};}}"
            "QListWidget::item{padding:8px 10px;}"
        )
        self._list.itemActivated.connect(self._pick)
        self._list.itemDoubleClicked.connect(self._pick)
        v.addWidget(self._list, 1)

        self._empty = QLabel("")
        self._empty.setStyleSheet(f"color:{ALMA_TEXT_LIGHT}; font-size:12px; border:none;")
        v.addWidget(self._empty)

        self._reload("")

    def _reload(self, text: str = ""):
        from src.data import enablement_store as store
        self._list.clear()
        try:
            rows = store.search_drafts(self._conn, text or "", limit=40)
        except Exception:
            rows = []
        for r in rows:
            title = r.get("title") or "Untitled"
            status = r.get("status") or ""
            item = QListWidgetItem(f"{title}   ·   {status}")
            item.setData(Qt.UserRole, int(r["id"]))
            self._list.addItem(item)
        self._empty.setText("No matching drafts." if not rows else "")

    def _pick(self, item):
        self.selected_draft_id = item.data(Qt.UserRole)
        self.accept()

    def _activate_first(self):
        if self._list.count():
            self._pick(self._list.item(0))

    def results_count(self) -> int:
        return self._list.count()
