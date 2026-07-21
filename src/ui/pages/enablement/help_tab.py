"""Help Center tab — browse the bundled help corpus.

Left: a table of contents grouped by section. Right: the selected article,
rendered from markdown, headed by a status badge and (for anything that is not
fully available) a banner stating plainly what does not work.

The status banner is the honesty mechanism and it is DATA-DRIVEN — it comes
from the article's frontmatter via ``help_articles.status``, not from prose
someone remembered to write. Several documented features are stubs, flag-gated
off, or unreachable; this is what stops the Help Center from confidently
instructing a user to do something that cannot work.

Search uses ``src.data.help.search``, which tokenizes. It deliberately does
not share the whole-query LIKE used elsewhere in the app — the Help Center is
the one surface users address with a whole sentence.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSplitter,
    QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from src.ui.theme import (
    ALMA_BG_ELEVATED, ALMA_BG_INSET, ALMA_BORDER, ALMA_CREAM, ALMA_TEXT_DARK,
    ALMA_TEXT_LIGHT, ALMA_TEXT_MID,
)

_ACCENT = "#0D7D72"

# status → (label, background, foreground). "available" renders no badge:
# a badge on everything is a badge on nothing.
_STATUS_BADGE = {
    "partial": ("PARTIAL", "#FDF3E3", "#8A5A1C"),
    "flag-gated": ("OFF BY DEFAULT", "#EEF0F5", "#4A5568"),
    "not-available": ("NOT AVAILABLE YET", "#F7E2E2", "#8A1C1C"),
}

_ROLE_ID = Qt.UserRole + 1


class HelpTab(QFrame):
    """Browse + search the help corpus. Read-only."""

    flag_bug_requested = Signal(str)   # article_id ('' from the header button)

    def __init__(self, conn_provider, parent=None):
        super().__init__(parent)
        # A callable, not a live connection: the tab outlives any one
        # connection and must survive the DB being swapped underneath it.
        self._conn_provider = conn_provider
        self._articles: dict[str, dict] = {}
        self._current_id = ""
        self.setObjectName("HelpTab")
        self.setStyleSheet(f"#HelpTab {{ background: {ALMA_CREAM}; }}")
        self._build()
        self.reload()

    # ── construction ────────────────────────────────────────────────

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(10)
        title = QLabel("Help Center")
        title.setStyleSheet(
            f"font-size: 18px; font-weight: 700; color: {ALMA_TEXT_DARK}; "
            "background: transparent; border: none;")
        head.addWidget(title)

        self._search = QLineEdit()
        self._search.setPlaceholderText(
            "Ask a question — e.g. how do I publish a card to Guru")
        self._search.setClearButtonEnabled(True)
        self._search.setStyleSheet(
            f"QLineEdit {{ background: {ALMA_BG_ELEVATED}; "
            f"border: 1px solid {ALMA_BORDER}; border-radius: 7px; "
            f"padding: 6px 10px; font-size: 12.5px; color: {ALMA_TEXT_DARK}; }} "
            f"QLineEdit:focus {{ border-color: {_ACCENT}; }}")
        self._search.textChanged.connect(self._on_search)
        head.addWidget(self._search, 1)

        self._bug_btn = QPushButton("Flag a bug")
        self._bug_btn.setCursor(Qt.PointingHandCursor)
        self._bug_btn.setStyleSheet(
            f"QPushButton {{ background: {ALMA_BG_ELEVATED}; "
            f"color: {ALMA_TEXT_DARK}; border: 1px solid {ALMA_BORDER}; "
            "border-radius: 7px; padding: 6px 13px; font-size: 12px; "
            f"font-weight: 600; }} QPushButton:hover {{ border-color: {_ACCENT}; }}")
        self._bug_btn.clicked.connect(
            lambda: self.flag_bug_requested.emit(self._current_id))
        head.addWidget(self._bug_btn)
        outer.addLayout(head)

        split = QSplitter(Qt.Horizontal)

        self._toc = QTreeWidget()
        self._toc.setHeaderHidden(True)
        self._toc.setIndentation(12)
        self._toc.setStyleSheet(
            f"QTreeWidget {{ background: {ALMA_BG_ELEVATED}; "
            f"border: 1px solid {ALMA_BORDER}; border-radius: 8px; "
            f"font-size: 12.5px; color: {ALMA_TEXT_DARK}; padding: 6px; }} "
            f"QTreeWidget::item {{ padding: 4px 2px; }} "
            f"QTreeWidget::item:selected {{ background: {ALMA_BG_INSET}; "
            f"color: {ALMA_TEXT_DARK}; }}")
        self._toc.currentItemChanged.connect(self._on_toc_change)
        split.addWidget(self._toc)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(8)

        self._banner = QLabel()
        self._banner.setWordWrap(True)
        self._banner.setVisible(False)
        rv.addWidget(self._banner)

        self._viewer = QTextBrowser()
        self._viewer.setOpenExternalLinks(False)
        self._viewer.setOpenLinks(False)
        self._viewer.anchorClicked.connect(self._on_anchor)
        self._viewer.setStyleSheet(
            f"QTextBrowser {{ background: {ALMA_BG_ELEVATED}; "
            f"border: 1px solid {ALMA_BORDER}; border-radius: 8px; "
            f"padding: 14px 18px; font-size: 13px; color: {ALMA_TEXT_DARK}; }}")
        rv.addWidget(self._viewer, 1)
        split.addWidget(right)

        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([260, 700])
        outer.addWidget(split, 1)

        self._status = QLabel("")
        self._status.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; "
            "background: transparent; border: none;")
        outer.addWidget(self._status)

    # ── data ────────────────────────────────────────────────────────

    def _conn(self):
        try:
            return self._conn_provider() if callable(self._conn_provider) \
                else self._conn_provider
        except Exception:  # noqa: BLE001 — a dead connection must not crash the tab
            return None

    def reload(self):
        """Re-read the corpus and rebuild the table of contents."""
        from src.data.help import store
        conn = self._conn()
        self._toc.clear()
        self._articles = {}
        if conn is None:
            self._status.setText("Help content unavailable.")
            return
        try:
            articles = store.list_articles(conn)
        except Exception:  # noqa: BLE001 — table missing (migration not run)
            articles = []
        if not articles:
            # Self-healing: the bundled corpus is the source of truth, so an
            # empty table just means it has not been loaded into THIS database
            # yet (fresh install, new warehouse, demo DB). Load and retry once.
            try:
                from src.data.help import loader
                loader.load_bundled_help(conn)
                articles = store.list_articles(conn)
            except Exception:  # noqa: BLE001 — help must never break the page
                articles = []
        if not articles:
            self._status.setText(
                "No help articles are loaded. Reinstalling the app restores "
                "the bundled content.")
            self._viewer.setMarkdown(
                "# Help content is not loaded\n\nThe bundled help articles "
                "could not be read. This is a bug worth flagging.")
            return

        by_section: dict[str, list[dict]] = {}
        for art in articles:
            self._articles[art["article_id"]] = art
            by_section.setdefault(art["section_title"] or art["section"],
                                  []).append(art)

        first_item = None
        for section_title, items in by_section.items():
            parent = QTreeWidgetItem([section_title])
            parent.setFlags(parent.flags() & ~Qt.ItemIsSelectable)
            font = parent.font(0)
            font.setBold(True)
            parent.setFont(0, font)
            self._toc.addTopLevelItem(parent)
            for art in items:
                child = QTreeWidgetItem([art["title"]])
                child.setData(0, _ROLE_ID, art["article_id"])
                if art["status"] in _STATUS_BADGE:
                    child.setForeground(0, Qt.gray)
                    child.setToolTip(0, _STATUS_BADGE[art["status"]][0])
                parent.addChild(child)
                if first_item is None:
                    first_item = child
            parent.setExpanded(True)

        self._status.setText(
            f"{len(articles)} articles in {len(by_section)} sections")
        if first_item is not None:
            self._toc.setCurrentItem(first_item)

    # ── rendering ───────────────────────────────────────────────────

    def show_article(self, article_id: str):
        art = self._articles.get(str(article_id or ""))
        if art is None:
            return
        self._current_id = art["article_id"]

        badge = _STATUS_BADGE.get(art["status"])
        if badge and art.get("banner"):
            label, bg, fg = badge
            self._banner.setText(f"  {label} — {art['banner']}")
            self._banner.setStyleSheet(
                f"background: {bg}; color: {fg}; border-radius: 7px; "
                "padding: 9px 12px; font-size: 12px; font-weight: 600;")
            self._banner.setVisible(True)
        else:
            self._banner.setVisible(False)

        body = f"# {art['title']}\n\n{art['body']}"
        self._viewer.setMarkdown(body)
        self._viewer.verticalScrollBar().setValue(0)

    def current_article_id(self) -> str:
        return self._current_id

    def _on_toc_change(self, current, _previous):
        if current is None:
            return
        article_id = current.data(0, _ROLE_ID)
        if article_id:
            self.show_article(article_id)

    # ── search ──────────────────────────────────────────────────────

    def _on_search(self, text: str):
        query = (text or "").strip()
        if not query:
            self._status.setText(
                f"{len(self._articles)} articles"
                f" in {self._toc.topLevelItemCount()} sections")
            return
        conn = self._conn()
        if conn is None:
            return
        from src.data.help import search as help_search
        try:
            hits = help_search.search_help(conn, query, limit=1)
        except Exception:  # noqa: BLE001
            hits = []
        if not hits:
            self._status.setText(
                f"Nothing matched “{query}”. Try fewer, more specific words.")
            return
        self._status.setText(f"Best match for “{query}”")
        self._select_in_toc(hits[0]["article_id"])

    def _select_in_toc(self, article_id: str):
        for i in range(self._toc.topLevelItemCount()):
            parent = self._toc.topLevelItem(i)
            for j in range(parent.childCount()):
                child = parent.child(j)
                if child.data(0, _ROLE_ID) == article_id:
                    self._toc.setCurrentItem(child)
                    return

    # ── cross-article links ─────────────────────────────────────────

    def _on_anchor(self, url):
        """``help://<article-id>`` jumps within the Help Center; anything else
        is refused — a help page must not be able to navigate the app."""
        try:
            text = url.toString()
        except Exception:  # noqa: BLE001
            return
        if text.startswith("help://"):
            self._select_in_toc(text[len("help://"):].strip("/"))
