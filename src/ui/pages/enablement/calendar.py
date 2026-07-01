"""Enablement Calendar — due-date grid with working month nav + month/week toggle.

Months are generated dynamically (Python's calendar module), so ‹ › navigates
between months. Events are keyed by ISO date: sample data before a scan, real
demo-DB tasks after (set_tasks). Clicking an event emits event_clicked.
"""

from __future__ import annotations

import calendar as _cal
from datetime import date, timedelta

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from src.ui.pages.enablement._common import TINT, card_frame
from src.ui.theme import (
    ALMA_BG_ELEVATED, ALMA_BORDER, ALMA_BORDER_LIGHT, ALMA_CREAM, ALMA_GREEN_DARK,
    ALMA_TEXT_DARK, ALMA_TEXT_LIGHT, ALMA_TEXT_MID, ALMA_TEXT_ON_DARK,
)

WEEK = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]
_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]
def _today() -> date:
    """The real current date (indirected through a helper so tests can pin it,
    and so the grid always opens on the true month — never a hardcoded demo day)."""
    return date.today()

# Sample events keyed by ISO date (June 2026), shown before a scan loads real tasks.
_SAMPLE = {
    "2026-06-04": [("Triage: bulk SSO", "asana")],
    "2026-06-08": [("Draft: SSO card", "drive"), ("Subtasks x5", "normal")],
    "2026-06-10": [("Payments v2 review", "guru")],
    "2026-06-12": [("Onboarding deck due", "high"), ("Returns deck due", "drive")],
    "2026-06-15": [("SSO request", "asana"), ("+2 more", "normal")],
    "2026-06-17": [("Rollout: SSO live", "done")],
    "2026-06-20": [("Returns policy", "drive")],
    "2026-06-23": [("Payments card review", "guru")],
    "2026-06-24": [("Launch coord sync", "asana")],
    "2026-06-30": [("Q2 wrap-up", "normal")],
}


class _Chip(QLabel):
    """A clickable calendar event chip."""

    clicked = Signal()

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        # Chips render untrusted Asana task titles — never interpret as rich text.
        self.setTextFormat(Qt.PlainText)

    def mousePressEvent(self, e):
        self.clicked.emit()
        super().mousePressEvent(e)


class CalendarPage(QWidget):
    """Month grid (navigable) + week view, colour-coded by source."""

    event_clicked = Signal(str)
    event_activated = Signal(dict)   # full task payload (guru chips → targeted update)
    scope_changed = Signal(str)      # "mine" | "all" — the operator task filter (M2)
    day_expanded = Signal(str)       # ISO date — "+N more" clicked → day drilldown (M3)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{ALMA_CREAM};")
        _t = _today()
        self._year, self._month = _t.year, _t.month
        self._view = "month"
        self._scope = "mine"           # "mine" | "all" — the operator filter
        self._live = False             # flips True once set_tasks feeds real data
        self._events = dict(_SAMPLE)   # sample chips until the first scan
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(20, 16, 20, 18)
        self._outer.setSpacing(12)
        self._outer.addLayout(self._header())
        self._grid_widget = self._grid()
        self._outer.addWidget(self._grid_widget, 1)

    def set_tasks(self, tasks: list[dict]):
        """Populate events from real tasks (keyed by their ISO due_date)."""
        self._live = True
        ev: dict = {}
        for t in tasks:
            due = t.get("due_date") or ""
            if len(due) >= 10:
                kind = t.get("source") if t.get("source") in TINT else "normal"
                ev.setdefault(due[:10], []).append(
                    ((t.get("title") or "")[:20], kind, dict(t))
                )
        self._events = ev
        self._rebuild_grid()

    # ── header (title + month nav + legend + view toggle) ─────────
    def _header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self._month_label = QLabel(self._title_text())
        self._month_label.setStyleSheet(f"color:{ALMA_TEXT_DARK}; font-size:18px; font-weight:700; border:none;")
        row.addWidget(self._month_label)
        from src.ui.design.icons import icon as design_icon
        for name, delta in (("chevron-left", -1), ("chevron-right", 1)):
            b = QPushButton("")
            b.setIcon(design_icon(name, 14, ALMA_TEXT_MID))
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet("QPushButton{background:transparent; border:none; padding:2px 6px;}")
            b.clicked.connect(lambda _=False, d=delta: self._change_month(d))
            row.addWidget(b)
        row.addSpacing(18)
        for lab, kind in (("Drive", "drive"), ("Guru", "guru"), ("Asana", "asana"), ("Due / high", "high")):
            dot = QLabel("•")
            dot.setStyleSheet(f"color:{TINT[kind][1]}; font-size:15px; border:none;")
            row.addWidget(dot)
            t = QLabel(lab)
            t.setStyleSheet(f"color:{ALMA_TEXT_MID}; font-size:12px; border:none;")
            row.addWidget(t)
            row.addSpacing(6)
        row.addStretch(1)
        row.addWidget(self._scope_toggle())
        row.addSpacing(8)
        row.addWidget(self._view_toggle())
        return row

    def _title_text(self) -> str:
        return f"{_MONTHS[self._month - 1]} {self._year}"

    def _change_month(self, delta: int):
        m, y = self._month + delta, self._year
        if m < 1:
            m, y = 12, y - 1
        elif m > 12:
            m, y = 1, y + 1
        self._year, self._month = y, m
        self._month_label.setText(self._title_text())
        if self._view == "week":          # month nav implies the month grid
            self._view = "month"
            self._restyle_toggle()
        self._rebuild_grid()

    def _view_toggle(self) -> QFrame:
        seg = QFrame()
        seg.setStyleSheet(f"background:{ALMA_BG_ELEVATED}; border:1px solid {ALMA_BORDER}; border-radius:8px;")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(2)
        self._mo_btn = QPushButton("Month")
        self._mo_btn.setCursor(Qt.PointingHandCursor)
        self._mo_btn.clicked.connect(lambda: self._set_view("month"))
        self._wk_btn = QPushButton("Week")
        self._wk_btn.setCursor(Qt.PointingHandCursor)
        self._wk_btn.clicked.connect(lambda: self._set_view("week"))
        sl.addWidget(self._mo_btn)
        sl.addWidget(self._wk_btn)
        self._restyle_toggle()
        return seg

    def _restyle_toggle(self):
        # padding overrides the global QPushButton padding that was clipping the text
        base = "border:none; border-radius:6px; font-size:12px; font-weight:600; padding:5px 16px;"
        active = f"QPushButton{{background:{ALMA_GREEN_DARK}; color:{ALMA_TEXT_ON_DARK}; {base}}}"
        plain = f"QPushButton{{background:transparent; color:{ALMA_TEXT_MID}; {base}}}"
        self._mo_btn.setStyleSheet(active if self._view == "month" else plain)
        self._wk_btn.setStyleSheet(active if self._view == "week" else plain)

    def _set_view(self, view: str):
        if view == self._view:
            return
        self._view = view
        self._restyle_toggle()
        self._rebuild_grid()

    # ── mine / all scope toggle (M2) ──────────────────────────────
    def _scope_toggle(self) -> QFrame:
        seg = QFrame()
        seg.setStyleSheet(f"background:{ALMA_BG_ELEVATED}; border:1px solid {ALMA_BORDER}; border-radius:8px;")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(2)
        self._mine_btn = QPushButton("Mine")
        self._mine_btn.setCursor(Qt.PointingHandCursor)
        self._mine_btn.clicked.connect(lambda: self._set_scope("mine"))
        self._all_btn = QPushButton("All")
        self._all_btn.setCursor(Qt.PointingHandCursor)
        self._all_btn.clicked.connect(lambda: self._set_scope("all"))
        sl.addWidget(self._mine_btn)
        sl.addWidget(self._all_btn)
        self._restyle_scope()
        return seg

    def _restyle_scope(self):
        base = "border:none; border-radius:6px; font-size:12px; font-weight:600; padding:5px 16px;"
        active = f"QPushButton{{background:{ALMA_GREEN_DARK}; color:{ALMA_TEXT_ON_DARK}; {base}}}"
        plain = f"QPushButton{{background:transparent; color:{ALMA_TEXT_MID}; {base}}}"
        self._mine_btn.setStyleSheet(active if self._scope == "mine" else plain)
        self._all_btn.setStyleSheet(active if self._scope == "all" else plain)

    def _set_scope(self, scope: str):
        if scope == self._scope:
            return
        self._scope = scope
        self._restyle_scope()
        self.scope_changed.emit(scope)

    def set_scope(self, scope: str):
        """Host sets the scope without echoing scope_changed (avoids a loop)."""
        if scope not in ("mine", "all") or scope == self._scope:
            return
        self._scope = scope
        self._restyle_scope()

    # ── grid ──────────────────────────────────────────────────────
    def _rebuild_grid(self):
        idx = self._outer.indexOf(self._grid_widget)
        new = self._grid()
        self._outer.replaceWidget(self._grid_widget, new)
        self._grid_widget.deleteLater()
        self._grid_widget = new
        if idx >= 0:
            self._outer.setStretch(idx, 1)

    def _grid(self) -> QFrame:
        return self._week_grid() if self._view == "week" else self._month_grid()

    def _weekday_label(self, d: str) -> QLabel:
        wl = QLabel("  " + d)
        wl.setStyleSheet(
            f"color:{ALMA_TEXT_LIGHT}; font-size:10.5px; font-weight:700; letter-spacing:0.5px; "
            f"background:{ALMA_BG_ELEVATED}; border:1px solid {ALMA_BORDER_LIGHT}; padding:6px 4px;"
        )
        return wl

    def _month_grid(self) -> QFrame:
        card = card_frame()
        g = QGridLayout(card)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(0)
        for c, d in enumerate(WEEK):
            g.addWidget(self._weekday_label(d), 0, c)
        weeks = _cal.Calendar(firstweekday=6).monthdatescalendar(self._year, self._month)
        for r, week in enumerate(weeks):
            for c, dt in enumerate(week):
                g.addWidget(self._cell(dt, in_month=(dt.month == self._month)), r + 1, c)
        for c in range(7):
            g.setColumnStretch(c, 1)
        for r in range(1, len(weeks) + 1):
            g.setRowStretch(r, 1)
        return card

    def _week_grid(self) -> QFrame:
        card = card_frame()
        g = QGridLayout(card)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(0)
        for c, d in enumerate(WEEK):
            g.addWidget(self._weekday_label(d), 0, c)
        today = _today()
        dow = (today.weekday() + 1) % 7         # Sunday = 0
        start = today - timedelta(days=dow)
        for c in range(7):
            g.addWidget(self._cell(start + timedelta(days=c), in_month=True), 1, c)
        for c in range(7):
            g.setColumnStretch(c, 1)
        g.setRowStretch(1, 1)
        return card

    def _cell(self, dt: date, in_month: bool) -> QFrame:
        f = QFrame()
        f.setStyleSheet(f"QFrame{{background:{ALMA_BG_ELEVATED}; border:1px solid {ALMA_BORDER_LIGHT};}}")
        v = QVBoxLayout(f)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(3)
        if dt == _today():
            n = QLabel(str(dt.day))
            n.setFixedSize(22, 22)
            n.setAlignment(Qt.AlignCenter)
            n.setStyleSheet(f"background:{ALMA_GREEN_DARK}; color:{ALMA_TEXT_ON_DARK}; border-radius:11px; font-size:12px; font-weight:700; border:none;")
            hl = QHBoxLayout()
            hl.setContentsMargins(0, 0, 0, 0)
            hl.addWidget(n)
            hl.addStretch(1)
            v.addLayout(hl)
        else:
            n = QLabel(str(dt.day))
            n.setStyleSheet(f"color:{ALMA_TEXT_LIGHT if not in_month else ALMA_TEXT_MID}; font-size:12px; font-weight:700; border:none;")
            v.addWidget(n)
        if in_month:
            entries = self._events.get(dt.isoformat(), [])
            # Cap the cell so a busy day doesn't blow out row height; the overflow
            # collapses into a clickable "+N more" that opens the day drilldown.
            visible = entries if len(entries) <= 3 else entries[:2]
            for entry in visible:
                # sample data is (label, kind); real tasks carry a payload
                label, kind = entry[0], entry[1]
                payload = entry[2] if len(entry) > 2 else None
                bg, fg = TINT.get(kind, ("#ECEAE5", ALMA_TEXT_LIGHT))
                chip = _Chip(label)
                chip.setFixedHeight(17)
                chip.setCursor(Qt.PointingHandCursor)
                chip.setStyleSheet(f"background:{bg}; color:{fg}; border-radius:5px; padding:1px 6px; font-size:10.5px; font-weight:600; border:none;")
                chip.clicked.connect(lambda lab=label: self.event_clicked.emit(lab))
                if payload is not None:
                    chip.clicked.connect(
                        lambda p=payload: self.event_activated.emit(p)
                    )
                v.addWidget(chip)
            if len(entries) > 3:
                bg, fg = TINT.get("normal", ("#ECEAE5", ALMA_TEXT_LIGHT))
                more = _Chip(f"+{len(entries) - 2} more")
                more.setFixedHeight(17)
                more.setCursor(Qt.PointingHandCursor)
                more.setStyleSheet(f"background:{bg}; color:{fg}; border-radius:5px; padding:1px 6px; font-size:10.5px; font-weight:600; border:none;")
                more.clicked.connect(lambda iso=dt.isoformat(): self.day_expanded.emit(iso))
                v.addWidget(more)
        v.addStretch(1)
        return f
