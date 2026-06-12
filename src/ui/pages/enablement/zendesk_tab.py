"""Zendesk tab — draft + human-gated push of Help Center articles and macros.

A pure view with Articles / Macros sub-tabs. The host (EnablementPage)
feeds synced lists + drafts via set_* and handles sync / select / save /
push against the demo/live connection and the Zendesk client (push writes
live only when configured; demo marks pushed). Article bodies edit in the
shared rich-text editor (E1).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from src.ui.pages.enablement._common import card_frame, section_label
from src.ui.pages.enablement.rich_editor import RichTextEditor
from src.ui.theme import (
    ALMA_BG_ELEVATED, ALMA_BG_INSET, ALMA_BORDER, ALMA_CREAM, ALMA_GREEN_DARK,
    ALMA_TEXT_DARK, ALMA_TEXT_LIGHT, ALMA_TEXT_ON_DARK,
)


def _primary(text) -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    b.setStyleSheet(
        f"QPushButton{{background:{ALMA_GREEN_DARK}; color:{ALMA_TEXT_ON_DARK}; border:none; "
        f"border-radius:8px; padding:7px 14px; font-size:12px; font-weight:600;}} "
        f"QPushButton:disabled{{background:{ALMA_BORDER}; color:{ALMA_TEXT_LIGHT};}}")
    return b


def _secondary(text) -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    b.setStyleSheet(
        f"QPushButton{{background:{ALMA_BG_ELEVATED}; color:{ALMA_GREEN_DARK}; "
        f"border:1px solid {ALMA_BORDER}; border-radius:8px; padding:7px 14px; "
        f"font-size:12px; font-weight:600;}} QPushButton:disabled{{color:{ALMA_TEXT_LIGHT};}}")
    return b


def _line(placeholder, *, bold=False) -> QLineEdit:
    e = QLineEdit()
    e.setPlaceholderText(placeholder)
    weight = "600" if bold else "400"
    e.setStyleSheet(
        f"QLineEdit{{background:{ALMA_BG_ELEVATED}; border:1px solid {ALMA_BORDER}; "
        f"border-radius:7px; padding:6px 10px; font-size:13px; font-weight:{weight}; "
        f"color:{ALMA_TEXT_DARK};}}")
    return e


class ZendeskPage(QWidget):
    sync_requested = Signal()
    article_selected = Signal(int)
    article_saved = Signal(int, str, str)   # (draft_id, title, body_md)
    article_push = Signal(int)
    macro_selected = Signal(int)
    macro_saved = Signal(int, str, str)     # (draft_id, name, reply_text)
    macro_push = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{ALMA_CREAM};")
        self._active_article = None
        self._active_macro = None
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 18)
        outer.setSpacing(10)

        head = QHBoxLayout()
        head.addWidget(section_label("ZENDESK SUPPORT CENTER"))
        self._sync_status = QLabel("")
        self._sync_status.setStyleSheet(
            f"color:{ALMA_TEXT_LIGHT}; font-size:11px; border:none; background:transparent;")
        head.addWidget(self._sync_status)
        head.addStretch(1)
        sync = _secondary("Sync from Zendesk")
        sync.clicked.connect(self.sync_requested.emit)
        head.addWidget(sync)
        outer.addLayout(head)

        self._tabs = QTabWidget()
        self._tabs.setObjectName("AnalysisTab")
        self._tabs.addTab(self._articles_tab(), "Articles")
        self._tabs.addTab(self._macros_tab(), "Macros")
        outer.addWidget(self._tabs, 1)

    # ── Articles ────────────────────────────────────────────────────
    def _articles_tab(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 8, 0, 0)
        h.setSpacing(12)

        left = QVBoxLayout()
        left.addWidget(section_label("ARTICLE DRAFTS"))
        self._a_list = QListWidget()
        self._a_list.setStyleSheet(self._list_style())
        self._a_list.itemClicked.connect(
            lambda it: self.article_selected.emit(int(it.data(Qt.UserRole))))
        left.addWidget(self._a_list, 1)
        h.addLayout(left, 2)

        right = QVBoxLayout()
        card = card_frame()
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(8)
        self._a_title = _line("Article title", bold=True)
        v.addWidget(self._a_title)
        self._a_body = RichTextEditor()
        v.addWidget(self._a_body, 1)
        arow = QHBoxLayout()
        arow.addStretch(1)
        self._a_save = _secondary("Save draft")
        self._a_save.clicked.connect(self._on_article_save)
        arow.addWidget(self._a_save)
        self._a_push = _primary("Push to Zendesk")
        self._a_push.clicked.connect(self._on_article_push)
        arow.addWidget(self._a_push)
        v.addLayout(arow)
        right.addWidget(card, 1)
        h.addLayout(right, 3)
        self._set_article_enabled(False)
        return w

    def _set_article_enabled(self, on):
        for x in (self._a_title, self._a_body, self._a_save, self._a_push):
            x.setEnabled(on)

    def set_articles(self, synced_count: int, drafts: list[dict]):
        self._a_list.clear()
        for d in drafts:
            tag = "update" if d.get("article_id") else "new"
            item = QListWidgetItem(f"{d.get('title', 'Untitled')}   ·   {tag}")
            item.setData(Qt.UserRole, d.get("id"))
            self._a_list.addItem(item)
        self._sync_status.setText(f"{synced_count} articles synced")

    def show_article_draft(self, draft: dict):
        self._active_article = draft.get("id")
        self._a_title.setText(draft.get("title", ""))
        self._a_body.set_markdown(draft.get("body", ""))
        self._set_article_enabled(True)
        if draft.get("status") == "pushed":
            self._a_push.setText("Pushed")
            self._a_push.setEnabled(False)
        else:
            self._a_push.setText("Push to Zendesk")

    def _on_article_save(self):
        if self._active_article is not None:
            self.article_saved.emit(int(self._active_article),
                                    self._a_title.text(), self._a_body.to_markdown())

    def _on_article_push(self):
        if self._active_article is not None:
            self.article_push.emit(int(self._active_article))

    # ── Macros ──────────────────────────────────────────────────────
    def _macros_tab(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 8, 0, 0)
        h.setSpacing(12)

        left = QVBoxLayout()
        left.addWidget(section_label("MACRO DRAFTS"))
        self._m_list = QListWidget()
        self._m_list.setStyleSheet(self._list_style())
        self._m_list.itemClicked.connect(
            lambda it: self.macro_selected.emit(int(it.data(Qt.UserRole))))
        left.addWidget(self._m_list, 1)
        h.addLayout(left, 2)

        right = QVBoxLayout()
        card = card_frame()
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(8)
        self._m_name = _line("Macro name", bold=True)
        v.addWidget(self._m_name)
        hint = QLabel("The macro's public reply (comment):")
        hint.setStyleSheet(
            f"color:{ALMA_TEXT_LIGHT}; font-size:11px; border:none; background:transparent;")
        v.addWidget(hint)
        self._m_reply = QPlainTextEdit()
        self._m_reply.setStyleSheet(
            f"QPlainTextEdit{{background:{ALMA_BG_INSET}; border:1px solid {ALMA_BORDER}; "
            f"border-radius:8px; color:{ALMA_TEXT_DARK}; font-size:12.5px; padding:8px;}}")
        v.addWidget(self._m_reply, 1)
        mrow = QHBoxLayout()
        mrow.addStretch(1)
        self._m_save = _secondary("Save draft")
        self._m_save.clicked.connect(self._on_macro_save)
        mrow.addWidget(self._m_save)
        self._m_push = _primary("Push to Zendesk")
        self._m_push.clicked.connect(self._on_macro_push)
        mrow.addWidget(self._m_push)
        v.addLayout(mrow)
        right.addWidget(card, 1)
        h.addLayout(right, 3)
        self._set_macro_enabled(False)
        return w

    def _set_macro_enabled(self, on):
        for x in (self._m_name, self._m_reply, self._m_save, self._m_push):
            x.setEnabled(on)

    def set_macros(self, synced_count: int, drafts: list[dict]):
        self._m_list.clear()
        for d in drafts:
            tag = "update" if d.get("macro_id") else "new"
            item = QListWidgetItem(f"{d.get('name', 'Untitled')}   ·   {tag}")
            item.setData(Qt.UserRole, d.get("id"))
            self._m_list.addItem(item)

    def show_macro_draft(self, draft: dict):
        self._active_macro = draft.get("id")
        self._m_name.setText(draft.get("name", ""))
        # show the first comment_value action as the editable reply
        reply = ""
        for a in draft.get("actions", []):
            if a.get("field") in ("comment_value", "comment_value_html"):
                reply = a.get("value", "")
                break
        self._m_reply.setPlainText(reply)
        self._set_macro_enabled(True)
        if draft.get("status") == "pushed":
            self._m_push.setText("Pushed")
            self._m_push.setEnabled(False)
        else:
            self._m_push.setText("Push to Zendesk")

    def _on_macro_save(self):
        if self._active_macro is not None:
            self.macro_saved.emit(int(self._active_macro),
                                  self._m_name.text(), self._m_reply.toPlainText())

    def _on_macro_push(self):
        if self._active_macro is not None:
            self.macro_push.emit(int(self._active_macro))

    # ── shared ──────────────────────────────────────────────────────
    def _list_style(self) -> str:
        return (f"QListWidget{{background:{ALMA_BG_ELEVATED}; border:1px solid {ALMA_BORDER}; "
                f"border-radius:8px; font-size:12.5px; color:{ALMA_TEXT_DARK};}}")

    def set_status(self, text: str):
        self._sync_status.setText(text)
