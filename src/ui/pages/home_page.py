"""Home / landing page — mode tiles, quick actions, recent activity.

First sidebar entry in both modes and the boot landing page. Mode tiles
emit `mode_selected` (MainWindow.switch_mode); quick actions emit
`quick_action` routed by MainWindow. Recent activity unions three
guarded queries (chat_sessions / analysis_reports / enablement_tasks) so
a missing table never breaks the page.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

from src.ui import app_modes
from src.ui.theme import (
    ALMA_BG_ELEVATED, ALMA_BORDER, ALMA_CREAM, ALMA_TEXT_DARK,
    ALMA_TEXT_MID,
)
from src.ui.pages.enablement._common import card_frame, pill

_ACCENT = "#0D7D72"

_KIND_TINT = {
    "Chat": ("#E8F0FE", "#1D4ED8"),
    "Report": ("#F3E8FF", "#6D28D9"),
    "Task": ("#E5F3EC", _ACCENT),
}

_QUICK_ACTIONS = {
    app_modes.MODE_PRODUCT: [
        ("search", "Run a search"),
        ("reports", "Open AI Reports"),
    ],
    app_modes.MODE_ENABLEMENT: [
        ("workbench", "Open Workbench"),
        ("calendar", "Open Calendar"),
        ("renn", "Chat with Renn"),
    ],
}

_MODE_TILES = (
    (app_modes.MODE_PRODUCT, "Product",
     "Full analytics suite — conversations, TRC analytics, trending, "
     "incidents, and AI reporting."),
    (app_modes.MODE_ENABLEMENT, "Enablement",
     "Lightweight workbench — calendar, tasks, Guru card drafting, and "
     "the Renn assistant. Skips the heavy analytics stack."),
)


class HomePage(QFrame):
    mode_selected = Signal(str)
    quick_action = Signal(str)

    def __init__(self, db, current_mode: str = app_modes.MODE_PRODUCT,
                 parent=None):
        super().__init__(parent)
        self.db = db
        self._mode = current_mode
        self._tiles = {}
        self._qa_buttons = []
        self._last_rows = []
        self.setObjectName("HomePage")
        self.setStyleSheet(f"#HomePage {{ background: {ALMA_CREAM}; }}")
        self._build()
        self.set_mode(current_mode)
        self.refresh()

    # ── construction ────────────────────────────────────────────────

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 28, 36, 24)
        outer.setSpacing(14)

        self._greeting = QLabel()
        self._greeting.setStyleSheet(
            f"font-size: 24px; font-weight: 700; color: {ALMA_TEXT_DARK}; "
            "background: transparent; border: none;"
        )
        outer.addWidget(self._greeting)

        subtitle = QLabel("Choose a workspace, or pick up where you left off.")
        subtitle.setStyleSheet(
            f"font-size: 13px; color: {ALMA_TEXT_MID}; "
            "background: transparent; border: none;"
        )
        outer.addWidget(subtitle)
        outer.addSpacing(6)

        tiles = QHBoxLayout()
        tiles.setSpacing(14)
        for mode, title, desc in _MODE_TILES:
            tile = self._mode_tile(mode, title, desc)
            self._tiles[mode] = tile
            tiles.addWidget(tile, 1)
        outer.addLayout(tiles)
        outer.addSpacing(8)

        outer.addWidget(self._heading("QUICK ACTIONS"))
        self._qa_row = QHBoxLayout()
        self._qa_row.setSpacing(10)
        outer.addLayout(self._qa_row)
        outer.addSpacing(8)

        outer.addWidget(self._heading("RECENT ACTIVITY"))
        self._activity_card = card_frame()
        self._activity_layout = QVBoxLayout(self._activity_card)
        self._activity_layout.setContentsMargins(18, 12, 18, 12)
        self._activity_layout.setSpacing(6)
        outer.addWidget(self._activity_card)

        outer.addStretch()

    def _heading(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 700; color: {ALMA_TEXT_MID}; "
            "letter-spacing: 1px; background: transparent; border: none;"
        )
        return lbl

    def _mode_tile(self, mode, title, desc):
        card = card_frame()
        card.setMinimumHeight(128)
        v = QVBoxLayout(card)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(6)

        head = QHBoxLayout()
        t = QLabel(title)
        t.setStyleSheet(
            f"font-size: 16px; font-weight: 700; color: {ALMA_TEXT_DARK}; "
            "background: transparent; border: none;"
        )
        head.addWidget(t)
        head.addStretch()
        active_pill = pill("ACTIVE", "#E5F3EC", _ACCENT)
        head.addWidget(active_pill)
        v.addLayout(head)

        d = QLabel(desc)
        d.setWordWrap(True)
        d.setStyleSheet(
            f"font-size: 12px; color: {ALMA_TEXT_MID}; "
            "background: transparent; border: none;"
        )
        v.addWidget(d)
        v.addStretch()

        btn = QPushButton("Switch to this mode")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton {{ background: {_ACCENT}; color: white; border: none; "
            "border-radius: 7px; padding: 7px 14px; font-size: 12px; "
            "font-weight: 600; }"
        )
        btn.clicked.connect(lambda _=False, m=mode: self.mode_selected.emit(m))
        v.addWidget(btn, 0, Qt.AlignLeft)

        card._active_pill = active_pill
        card._switch_btn = btn
        return card

    # ── state ───────────────────────────────────────────────────────

    def set_mode(self, mode: str):
        """Update tile active states + quick actions for the current mode."""
        if mode not in app_modes.MODES:
            return
        self._mode = mode
        for m, tile in self._tiles.items():
            tile._active_pill.setVisible(m == mode)
            tile._switch_btn.setVisible(m != mode)

        while self._qa_row.count():
            item = self._qa_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        self._qa_buttons = []
        for action, label in _QUICK_ACTIONS.get(mode, []):
            b = QPushButton(label)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                f"QPushButton {{ background: {ALMA_BG_ELEVATED}; "
                f"color: {ALMA_TEXT_DARK}; border: 1px solid {ALMA_BORDER}; "
                "border-radius: 7px; padding: 7px 14px; font-size: 12px; "
                "font-weight: 600; }"
            )
            b.clicked.connect(
                lambda _=False, a=action: self.quick_action.emit(a)
            )
            self._qa_buttons.append(b)
            self._qa_row.addWidget(b)
        self._qa_row.addStretch()

    def refresh(self):
        """Re-query recent activity and rebuild the list."""
        h = datetime.now().hour
        word = "morning" if h < 12 else ("afternoon" if h < 17 else "evening")
        self._greeting.setText(f"Good {word}")

        while self._activity_layout.count():
            item = self._activity_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()

        rows = self._recent_activity()
        self._last_rows = rows
        if not rows:
            empty = QLabel(
                "No recent activity yet — run a search or scan to get started."
            )
            empty.setStyleSheet(
                f"font-size: 12px; color: {ALMA_TEXT_MID}; "
                "background: transparent; border: none; padding: 6px 0;"
            )
            self._activity_layout.addWidget(empty)
            return
        for kind, title, ts in rows:
            self._activity_layout.addWidget(self._activity_row(kind, title, ts))

    def _activity_row(self, kind, title, ts):
        row = QFrame()
        row.setStyleSheet("background: transparent; border: none;")
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 2, 0, 2)
        h.setSpacing(10)
        bg, fg = _KIND_TINT.get(kind, ("#EEEEEE", ALMA_TEXT_MID))
        h.addWidget(pill(kind, bg, fg))
        t = QLabel(title[:110])
        t.setStyleSheet(
            f"font-size: 12.5px; color: {ALMA_TEXT_DARK}; "
            "background: transparent; border: none;"
        )
        h.addWidget(t, 1)
        when = QLabel(str(ts)[:16].replace("T", "  "))
        when.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_MID}; "
            "background: transparent; border: none;"
        )
        h.addWidget(when)
        return row

    def _recent_activity(self):
        out = []
        conn = getattr(self.db, "conn", None)
        if conn is None:
            return out
        queries = (
            ("Chat",
             "SELECT COALESCE(title, source_page, 'Chat session'), updated_at "
             "FROM chat_sessions ORDER BY updated_at DESC LIMIT 8"),
            ("Report",
             "SELECT page || ' — ' || substr(summary, 1, 80), run_at "
             "FROM analysis_reports ORDER BY run_at DESC LIMIT 8"),
            ("Task",
             "SELECT title, COALESCE(updated_at, created_at, '') "
             "FROM enablement_tasks WHERE status != 'dismissed' "
             "ORDER BY COALESCE(updated_at, created_at, '') DESC LIMIT 8"),
        )
        for kind, sql in queries:
            try:
                for title, ts in conn.execute(sql).fetchall():
                    out.append((kind, str(title or ""), str(ts or "")))
            except Exception:
                continue  # table missing / schema drift — never break Home
        out.sort(key=lambda r: r[2], reverse=True)
        return out[:8]

    def showEvent(self, event):
        super().showEvent(event)
        try:
            self.refresh()
        except Exception:
            pass
