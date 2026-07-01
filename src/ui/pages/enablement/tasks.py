"""Enablement Task list — running tasks with subtasks + scratch pad.

Data-driven: load_tasks(rows) rebuilds the list from task dicts (used by the demo
controller against the demo DB). Falls back to a curated sample when empty. Rows
expand to a subtask checklist (clickable checkboxes + an "add subtask" input) and
a scratch pad.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from src.ui.pages.enablement._common import DOT, badge, card_frame, field
from src.ui.theme import (
    ALMA_BG_ELEVATED, ALMA_BG_INSET, ALMA_BORDER, ALMA_BORDER_LIGHT, ALMA_CREAM,
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_TEXT_DARK, ALMA_TEXT_LIGHT, ALMA_TEXT_MID,
    ALMA_TEXT_ON_DARK,
)

COLS = [("SOURCE", 96), ("DUE", 74), ("PRIORITY", 92), ("ASSIGNEE", 120), ("SUBTASKS", 64)]
_TRANSPARENT = "background:transparent;"

_SSO_SUBS = [("Draft card from doc", True), ("Add product screenshots", True),
             ("SME technical review", False), ("Publish to Guru", False),
             ("Announce in #enablement-updates", False)]
_SSO_SCRATCH = ("Waiting on final launch date from Product (Maya).\n"
                "Confirm rollout regions before publishing.\n"
                "Pilot org: acme-health (test SSO first).")

# Curated fallback rows (used until a demo scan loads real tasks).
_SAMPLE = [
    {"status": "open", "title": "Draft Guru card from “SSO Setup.gdoc”", "source": "drive",
     "due": "Jun 12", "priority": "high", "assignee": "—", "subs": "2 / 5",
     "subtasks": _SSO_SUBS, "scratch": _SSO_SCRATCH},
    {"status": "open", "title": "Payments v2 — product update posted to Guru", "source": "guru",
     "due": "—", "priority": "normal", "assignee": "—", "subs": "0 / 0", "subtasks": [], "scratch": ""},
    {"status": "in_progress", "title": "Triage: SSO bulk-provisioning request", "source": "asana",
     "due": "Jun 15", "priority": "high", "assignee": "J. Rivera", "subs": "1 / 3",
     "subtasks": [("Confirm scope", True), ("Draft response", False), ("Loop in IT", False)], "scratch": ""},
    {"status": "open", "title": "Index Q3 launch deadlines from “Returns Policy.gdoc”", "source": "drive",
     "due": "Jun 20", "priority": "normal", "assignee": "—", "subs": "0 / 2", "subtasks": [], "scratch": ""},
    {"status": "done", "title": "Refresh “Account Settings” card", "source": "guru",
     "due": "—", "priority": "low", "assignee": "M. Chen", "subs": "4 / 4", "subtasks": [], "scratch": ""},
    {"status": "open", "title": "Onboarding deck — extract milestone dates", "source": "drive",
     "due": "Jun 12", "priority": "high", "assignee": "—", "subs": "0 / 0", "subtasks": [], "scratch": ""},
]


class _Row(QFrame):
    """A task row that emits `clicked` so the list can expand/collapse it."""

    clicked = Signal()

    def mousePressEvent(self, e):
        self.clicked.emit()
        super().mousePressEvent(e)


class _Check(QFrame):
    """A clickable checkbox that toggles its done state."""

    def __init__(self, done: bool = False, parent=None):
        super().__init__(parent)
        self.done = done
        self.setFixedSize(15, 15)
        self.setCursor(Qt.PointingHandCursor)
        self._render()

    def _render(self):
        self.setStyleSheet(
            f"background:{ALMA_GREEN_LIGHT if self.done else ALMA_BG_ELEVATED}; "
            f"border:1px solid {ALMA_GREEN_LIGHT if self.done else ALMA_BORDER}; border-radius:4px;"
        )

    def mousePressEvent(self, e):
        self.done = not self.done
        self._render()
        super().mousePressEvent(e)


def _fixed_cell(width: int, widget: QWidget) -> QWidget:
    """Left-aligned fixed-width column cell. A QFrame with an explicit white
    background (matching the row) — a bare QWidget would paint the palette's
    window-gray once a global stylesheet is active, which read as ugly column bands."""
    box = QFrame()
    box.setFixedWidth(width)
    box.setStyleSheet(f"QFrame{{background:{ALMA_BG_ELEVATED}; border:none;}}")
    h = QHBoxLayout(box)
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(widget)
    h.addStretch(1)
    return box


class TasksPage(QWidget):
    """Filterable task list; rows expand to show subtasks + scratch pad."""

    scope_changed = Signal(str)   # "mine" | "all" — the operator task filter (M2)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{ALMA_CREAM};")
        self._scope = "mine"          # "mine" | "all"
        self._rows = list(_SAMPLE)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 18)
        outer.setSpacing(12)
        outer.addWidget(self._filter_bar())
        self._table_card = card_frame()
        self._table_v = QVBoxLayout(self._table_card)
        self._table_v.setContentsMargins(0, 0, 0, 0)
        self._table_v.setSpacing(0)
        outer.addWidget(self._table_card, 1)
        self._rebuild()

    def load_tasks(self, rows: list[dict]):
        """Replace the list with task dicts (or restore the sample if empty)."""
        self._rows = list(rows) if rows else list(_SAMPLE)
        self._rebuild()

    def _rebuild(self):
        while self._table_v.count():
            item = self._table_v.takeAt(0)
            w = item.widget()
            if w:
                w.hide()
                w.deleteLater()
        self._table_v.addWidget(self._header_row())
        for i, d in enumerate(self._rows):
            self._table_v.addWidget(self._task_row(d, expanded=(i == 0)))
        self._table_v.addStretch(1)

    def _filter_bar(self) -> QFrame:
        card = card_frame()
        h = QHBoxLayout(card)
        h.setContentsMargins(16, 10, 16, 10)
        h.setSpacing(10)
        h.addWidget(self._scope_toggle())
        h.addSpacing(4)
        for f in ("Source: All", "Status: Open", "Due: Any", "Priority: Any"):
            h.addWidget(field(f))
        h.addWidget(field("Search tasks…", w=200))
        h.addStretch(1)
        new = QPushButton("+ New task")
        new.setStyleSheet(
            f"QPushButton{{background:{ALMA_GREEN_DARK}; color:{ALMA_TEXT_ON_DARK}; border:none; "
            f"border-radius:8px; padding:8px 18px; font-size:12.5px; font-weight:600;}}"
        )
        h.addWidget(new)
        return card

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
        """Host sets the scope without echoing scope_changed."""
        if scope not in ("mine", "all") or scope == self._scope:
            return
        self._scope = scope
        self._restyle_scope()

    def _col(self, text, w, *, header=False, color=None, bold=False, align_right=False):
        lbl = QLabel(text)
        if w:
            lbl.setFixedWidth(w)
        if align_right:
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        if header:
            lbl.setStyleSheet(f"color:{ALMA_TEXT_MID}; font-size:10.5px; font-weight:700; letter-spacing:0.5px; border:none; {_TRANSPARENT}")
        else:
            c = color or ALMA_TEXT_MID
            lbl.setStyleSheet(f"color:{c}; font-size:{'13.5px' if bold else '12.5px'}; font-weight:{'700' if bold else '400'}; border:none; {_TRANSPARENT}")
        return lbl

    def _header_row(self) -> QFrame:
        f = QFrame()
        f.setStyleSheet(f"QFrame{{background:{ALMA_BG_INSET}; border:none; border-top-left-radius:12px; border-top-right-radius:12px;}}")
        h = QHBoxLayout(f)
        h.setContentsMargins(20, 9, 20, 9)
        h.setSpacing(0)
        h.addWidget(self._col("", 24, header=True))
        h.addWidget(self._col("TASK", 0, header=True), 1)
        for name, w in COLS:
            h.addWidget(self._col(name, w, header=True, align_right=(name == "SUBTASKS")))
        return f

    def _task_row(self, d: dict, expanded=False) -> QWidget:
        holder = QWidget()
        holder.setStyleSheet(_TRANSPARENT)
        hv = QVBoxLayout(holder)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.setSpacing(0)

        row = _Row()
        row.setCursor(Qt.PointingHandCursor)
        row.setStyleSheet(f"QFrame{{background:{ALMA_BG_ELEVATED}; border:none; border-bottom:1px solid {ALMA_BORDER_LIGHT};}}")
        h = QHBoxLayout(row)
        h.setContentsMargins(20, 0, 20, 0)
        h.setSpacing(0)
        dot = QLabel("•")
        dot.setFixedWidth(24)
        dot.setStyleSheet(f"color:{DOT.get(d['status'], ALMA_TEXT_LIGHT)}; font-size:18px; border:none; {_TRANSPARENT}")
        h.addWidget(dot)
        h.addWidget(self._col(d["title"], 0, bold=True, color=ALMA_TEXT_DARK), 1)
        h.addWidget(_fixed_cell(COLS[0][1], badge(d["source"].capitalize(), d["source"])))
        h.addWidget(self._col(d["due"], COLS[1][1], color=(ALMA_TEXT_MID if d["due"] != "—" else ALMA_TEXT_LIGHT)))
        h.addWidget(_fixed_cell(COLS[2][1], badge(d["priority"].capitalize(), d["priority"])))
        h.addWidget(self._col(d["assignee"], COLS[3][1], color=(ALMA_TEXT_MID if d["assignee"] != "—" else ALMA_TEXT_LIGHT)))
        h.addWidget(self._col(d["subs"], COLS[4][1], color=ALMA_TEXT_MID, align_right=True))
        row.setFixedHeight(54)
        hv.addWidget(row)

        panel = self._expand_panel(d)
        panel.setVisible(expanded)
        hv.addWidget(panel)
        row.clicked.connect(lambda: panel.setVisible(not panel.isVisible()))
        return holder

    def _subtask_row(self, text: str, done: bool = False) -> QWidget:
        item = QWidget()
        item.setStyleSheet(_TRANSPARENT)
        hl = QHBoxLayout(item)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)
        hl.addWidget(_Check(done))
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color:{ALMA_TEXT_LIGHT if done else ALMA_TEXT_MID}; font-size:12.5px; border:none; {_TRANSPARENT}")
        hl.addWidget(lbl)
        hl.addStretch(1)
        return item

    def _expand_panel(self, d: dict) -> QFrame:
        panel = QFrame()
        panel.setStyleSheet(f"QFrame{{background:{ALMA_BG_INSET}; border:none; border-bottom:1px solid {ALMA_BORDER_LIGHT}; border-left:3px solid {ALMA_GREEN_LIGHT};}}")
        h = QHBoxLayout(panel)
        h.setContentsMargins(24, 14, 20, 14)
        h.setSpacing(28)

        left_w = QWidget()
        left_w.setStyleSheet(_TRANSPARENT)
        left_w.setFixedWidth(380)
        left = QVBoxLayout(left_w)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(8)
        lab = QLabel("SUBTASKS")
        lab.setStyleSheet(f"color:{ALMA_TEXT_LIGHT}; font-size:10.5px; font-weight:700; letter-spacing:0.5px; border:none; {_TRANSPARENT}")
        left.addWidget(lab)

        subs_box = QWidget()
        subs_box.setStyleSheet(_TRANSPARENT)
        subs_v = QVBoxLayout(subs_box)
        subs_v.setContentsMargins(0, 0, 0, 0)
        subs_v.setSpacing(6)
        for text, done in (d.get("subtasks") or []):
            subs_v.addWidget(self._subtask_row(text, done))
        left.addWidget(subs_box)

        # add-subtask input (a visible way to add subtasks)
        add = QHBoxLayout()
        add.setSpacing(6)
        inp = QLineEdit()
        inp.setPlaceholderText("Add a subtask…")
        inp.setFixedHeight(28)
        inp.setStyleSheet(
            f"QLineEdit{{background:{ALMA_BG_ELEVATED}; border:1px solid {ALMA_BORDER}; "
            f"border-radius:7px; padding:2px 10px; font-size:12px; color:{ALMA_TEXT_DARK};}}"
        )
        add_btn = QPushButton("+ Add")
        add_btn.setStyleSheet(
            f"QPushButton{{background:{ALMA_GREEN_DARK}; color:{ALMA_TEXT_ON_DARK}; border:none; "
            f"border-radius:7px; padding:5px 12px; font-size:11.5px; font-weight:600;}}"
        )

        def _add():
            t = inp.text().strip()
            if t:
                subs_v.addWidget(self._subtask_row(t, False))
                inp.clear()

        inp.returnPressed.connect(_add)
        add_btn.clicked.connect(_add)
        add.addWidget(inp, 1)
        add.addWidget(add_btn)
        left.addLayout(add)

        ai = QPushButton("AI · Draft subtasks")
        ai.setStyleSheet(
            f"QPushButton{{background:transparent; color:{ALMA_GREEN_DARK}; border:1px solid {ALMA_BORDER}; "
            f"border-radius:7px; padding:5px 12px; font-size:11.5px; font-weight:600;}}"
        )
        ai.setFixedWidth(160)

        def _ai_draft():
            for t in ("Review with SME", "Add screenshots", "Draft announcement"):
                subs_v.addWidget(self._subtask_row(t, False))

        ai.clicked.connect(_ai_draft)
        left.addWidget(ai)
        left.addStretch(1)
        h.addWidget(left_w)

        right = QVBoxLayout()
        right.setSpacing(7)
        lab2 = QLabel("SCRATCH PAD")
        lab2.setStyleSheet(f"color:{ALMA_TEXT_LIGHT}; font-size:10.5px; font-weight:700; letter-spacing:0.5px; border:none; {_TRANSPARENT}")
        right.addWidget(lab2)
        note = QLabel(d.get("scratch") or "—")
        note.setStyleSheet(f"background:{ALMA_BG_ELEVATED}; color:{ALMA_TEXT_MID}; border:1px solid {ALMA_BORDER}; border-radius:8px; padding:10px 12px; font-size:12px;")
        note.setFixedWidth(420)
        note.setWordWrap(True)
        note.setMinimumHeight(72)
        note.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        right.addWidget(note)
        right.addStretch(1)
        h.addLayout(right)

        h.addStretch(1)
        openw = QPushButton("Open in Workbench ›")
        openw.setStyleSheet(
            f"QPushButton{{background:{ALMA_BG_ELEVATED}; color:{ALMA_GREEN_DARK}; border:1px solid {ALMA_BORDER}; "
            f"border-radius:7px; padding:7px 14px; font-size:12px; font-weight:600;}}"
        )
        col = QVBoxLayout()
        col.addStretch(1)
        col.addWidget(openw)
        h.addLayout(col)
        return panel
