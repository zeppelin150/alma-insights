"""Task detail panel — shown in the shared DrilldownPanel when a task is clicked.

Clicking a task (calendar event or task row) opens this, NOT the Workbench. An
"Open in Workbench" button is the explicit way to jump to the editor.

Two-way Asana (Phase 2): for tasks sourced from Asana the panel exposes write-back
actions — add a subtask, post a comment, change the due date — that the host pushes
back to Asana via asana_writeback. The scratch pad stays a local-only note.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from src.ui.pages.enablement._common import badge
from src.ui.theme import (
    ALMA_ACCENT_TEAL, ALMA_BG_ELEVATED, ALMA_BG_INSET, ALMA_BORDER,
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_TEXT_DARK, ALMA_TEXT_LIGHT,
    ALMA_TEXT_MID,
)


class TaskDetailPanel(QWidget):
    """Task detail with two-way Asana actions + a local scratch pad."""

    open_in_workbench = Signal()
    subtask_added = Signal(str)      # subtask text (synced to Asana if linked)
    comment_posted = Signal(str)     # comment text → Asana story
    due_changed = Signal(str)        # ISO date YYYY-MM-DD (or "" to clear)

    def __init__(self, task: dict, parent=None):
        super().__init__(parent)
        self._task = task or {}
        self._is_asana = (self._task.get("source") == "asana")
        self.setStyleSheet(f"background:{ALMA_BG_ELEVATED};")
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 18)
        v.setSpacing(12)

        title = QLabel(self._task.get("title", "Task"))
        title.setWordWrap(True)
        title.setStyleSheet(f"color:{ALMA_TEXT_DARK}; font-size:18px; font-weight:700; border:none;")
        v.addWidget(title)

        meta = QHBoxLayout()
        meta.setSpacing(8)
        src = self._task.get("source", "drive")
        meta.addWidget(badge(src.capitalize(), src))
        prio = self._task.get("priority", "normal")
        meta.addWidget(badge(prio.capitalize(), prio))
        asg = self._task.get("assignee", "—")
        if asg and asg != "—":
            a = QLabel(asg)
            a.setStyleSheet(f"color:{ALMA_TEXT_MID}; font-size:12px; border:none;")
            meta.addWidget(a)
        if self._is_asana:
            meta.addWidget(badge("Asana two-way", "asana"))
        meta.addStretch(1)
        v.addLayout(meta)

        # ── Due date (editable; pushes to Asana when linked) ──
        v.addWidget(self._label("DUE DATE"))
        due_row = QHBoxLayout()
        due_row.setSpacing(8)
        self._due_edit = QLineEdit(self._task.get("due_iso", "") or "")
        self._due_edit.setPlaceholderText("YYYY-MM-DD")
        self._due_edit.setStyleSheet(self._input_css())
        self._due_edit.setFixedWidth(140)
        due_row.addWidget(self._due_edit)
        due_btn = self._action_btn("Update due")
        due_btn.clicked.connect(lambda: self.due_changed.emit(self._due_edit.text().strip()))
        due_row.addWidget(due_btn)
        due_row.addStretch(1)
        v.addLayout(due_row)

        # ── Subtasks (existing + add row) ──
        v.addWidget(self._label("SUBTASKS"))
        self._subs_box = QVBoxLayout()
        self._subs_box.setSpacing(4)
        subtasks = self._task.get("subtasks") or []
        if subtasks:
            for text, done in subtasks:
                self._subs_box.addLayout(self._subtask(text, bool(done)))
        else:
            empty = QLabel("No subtasks yet.")
            empty.setStyleSheet(f"color:{ALMA_TEXT_LIGHT}; font-size:12.5px; border:none;")
            self._subs_box.addWidget(empty)
        v.addLayout(self._subs_box)

        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        self._sub_edit = QLineEdit()
        self._sub_edit.setPlaceholderText(
            "Add a subtask (creates it in Asana)" if self._is_asana else "Add a subtask")
        self._sub_edit.setStyleSheet(self._input_css())
        self._sub_edit.returnPressed.connect(self._emit_subtask)
        add_row.addWidget(self._sub_edit, 1)
        add_btn = self._action_btn("Add")
        add_btn.clicked.connect(self._emit_subtask)
        add_row.addWidget(add_btn)
        v.addLayout(add_row)

        # ── Comment → Asana (Asana-linked tasks only) ──
        if self._is_asana:
            v.addWidget(self._label("COMMENT TO ASANA"))
            c_row = QHBoxLayout()
            c_row.setSpacing(8)
            self._comment_edit = QLineEdit()
            self._comment_edit.setPlaceholderText("Leave a comment on the Asana task…")
            self._comment_edit.setStyleSheet(self._input_css())
            self._comment_edit.returnPressed.connect(self._emit_comment)
            c_row.addWidget(self._comment_edit, 1)
            c_btn = self._action_btn("Comment")
            c_btn.clicked.connect(self._emit_comment)
            c_row.addWidget(c_btn)
            v.addLayout(c_row)

        # ── Scratch pad (local note — retained as read display) ──
        v.addWidget(self._label("SCRATCH PAD"))
        note = QLabel(self._task.get("scratch") or "—")
        note.setWordWrap(True)
        note.setStyleSheet(
            f"background:{ALMA_BG_INSET}; color:{ALMA_TEXT_MID}; border:1px solid {ALMA_BORDER}; "
            f"border-radius:8px; padding:10px 12px; font-size:12px;"
        )
        v.addWidget(note)

        v.addStretch(1)
        btn = QPushButton("Open in Workbench ›")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton{{background:{ALMA_GREEN_DARK}; color:white; border:none; border-radius:8px; "
            f"padding:9px 16px; font-size:12.5px; font-weight:600;}}"
        )
        btn.clicked.connect(self.open_in_workbench.emit)
        v.addWidget(btn)

    # ── emit helpers (clear the input after firing) ──────────────────
    def _emit_subtask(self):
        text = self._sub_edit.text().strip()
        if text:
            self.subtask_added.emit(text)
            self._sub_edit.clear()

    def _emit_comment(self):
        text = self._comment_edit.text().strip()
        if text:
            self.comment_posted.emit(text)
            self._comment_edit.clear()

    # ── styling helpers ──────────────────────────────────────────────
    def _input_css(self) -> str:
        return (
            f"QLineEdit{{background:{ALMA_BG_ELEVATED}; border:1px solid {ALMA_BORDER}; "
            f"border-radius:7px; padding:6px 10px; font-size:12.5px; color:{ALMA_TEXT_DARK};}}"
            f"QLineEdit:focus{{border:1px solid {ALMA_ACCENT_TEAL};}}"
        )

    def _action_btn(self, text: str) -> QPushButton:
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(
            f"QPushButton{{background:{ALMA_BG_ELEVATED}; color:{ALMA_ACCENT_TEAL}; "
            f"border:1px solid {ALMA_ACCENT_TEAL}; border-radius:7px; padding:6px 12px; "
            f"font-size:12px; font-weight:600;}}"
        )
        return b

    def _label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color:{ALMA_TEXT_LIGHT}; font-size:10.5px; font-weight:700; letter-spacing:0.5px; border:none;")
        return lbl

    def _subtask(self, text: str, done: bool) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        box = QLabel("")
        box.setFixedSize(14, 14)
        box.setStyleSheet(
            f"background:{ALMA_GREEN_LIGHT if done else ALMA_BG_ELEVATED}; "
            f"border:1px solid {ALMA_GREEN_LIGHT if done else ALMA_BORDER}; border-radius:4px;"
        )
        row.addWidget(box)
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color:{ALMA_TEXT_LIGHT if done else ALMA_TEXT_MID}; font-size:12.5px; border:none;")
        row.addWidget(lbl)
        row.addStretch(1)
        return row
