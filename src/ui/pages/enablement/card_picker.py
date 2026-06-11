"""Guru card picker — modal dialog for the Workbench's import flow.

Browse by collection or search; returns the chosen card id via
``selected_card_id`` after exec(). The Guru client calls are small and
synchronous (modal context); failures surface in the status line.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QVBoxLayout,
)

from src.ui.theme import (
    ALMA_BG_ELEVATED, ALMA_BORDER, ALMA_CREAM, ALMA_GREEN_DARK,
    ALMA_TEXT_DARK, ALMA_TEXT_LIGHT, ALMA_TEXT_ON_DARK,
)


class GuruCardPickerDialog(QDialog):
    def __init__(self, guru_client, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import a Guru card")
        self.setMinimumSize(520, 420)
        self.setStyleSheet(f"QDialog{{background:{ALMA_CREAM};}}")
        self._client = guru_client
        self.selected_card_id: str | None = None
        self.selected_card_title: str = ""
        self._build()
        self._load_collections()
        self._reload()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 14)
        outer.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(8)
        self._collections = QComboBox()
        self._collections.addItem("All collections", None)
        self._collections.currentIndexChanged.connect(lambda _i: self._reload())
        top.addWidget(self._collections, 1)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search cards…")
        self._search.returnPressed.connect(self._reload)
        top.addWidget(self._search, 2)
        outer.addLayout(top)

        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget{{background:{ALMA_BG_ELEVATED}; border:1px solid {ALMA_BORDER}; "
            f"border-radius:8px; font-size:13px; color:{ALMA_TEXT_DARK};}}"
        )
        self._list.itemDoubleClicked.connect(lambda _i: self._accept())
        outer.addWidget(self._list, 1)

        self._status = QLabel("")
        self._status.setStyleSheet(
            f"color:{ALMA_TEXT_LIGHT}; font-size:11px; border:none; background:transparent;"
        )
        outer.addWidget(self._status)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("SecondaryButton")
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        ok = QPushButton("Import card")
        ok.setStyleSheet(
            f"QPushButton{{background:{ALMA_GREEN_DARK}; color:{ALMA_TEXT_ON_DARK}; "
            "border:none; border-radius:8px; padding:8px 18px; font-size:12px; "
            "font-weight:600; }"
        )
        ok.clicked.connect(self._accept)
        btns.addWidget(ok)
        outer.addLayout(btns)

    def _load_collections(self):
        try:
            for c in self._client.list_collections() or []:
                if c.get("id"):
                    self._collections.addItem(c.get("name") or c["id"], c["id"])
        except Exception as exc:  # noqa: BLE001
            self._status.setText(f"Couldn't load collections: {exc}")

    def _reload(self):
        self._list.clear()
        self._status.setText("Loading…")
        try:
            query = self._search.text().strip()
            if query:
                cards = self._client.search_cards(query) or []
            else:
                cards = self._client.list_cards(
                    self._collections.currentData()
                ) or []
        except Exception as exc:  # noqa: BLE001
            self._status.setText(f"Couldn't load cards: {exc}")
            return
        for card in cards[:100]:
            cid = card.get("id")
            if not cid:
                continue
            title = card.get("title") or card.get("preferredPhrase") or cid
            item = QListWidgetItem(title)
            item.setData(Qt.UserRole, cid)
            self._list.addItem(item)
        self._status.setText(f"{self._list.count()} cards")

    def _accept(self):
        item = self._list.currentItem()
        if item is None:
            self._status.setText("Pick a card first.")
            return
        self.selected_card_id = item.data(Qt.UserRole)
        self.selected_card_title = item.text()
        self.accept()
