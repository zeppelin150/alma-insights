"""Task detail panel — shown in the shared DrilldownPanel when a task is clicked.

Clicking a task (calendar event or task row) opens this, NOT the Workbench. An
"Open in Workbench" button is the explicit way to jump to the editor.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from src.ui.pages.enablement._common import badge
from src.ui.theme import (
    ALMA_BG_ELEVATED, ALMA_BG_INSET, ALMA_BORDER, ALMA_GREEN_DARK, ALMA_GREEN_LIGHT,
    ALMA_TEXT_DARK, ALMA_TEXT_LIGHT, ALMA_TEXT_MID,
)


class TaskDetailPanel(QWidget):
    """Read view of a task: meta, subtasks, scratch pad, open-in-workbench."""

    open_in_workbench = Signal()

    def __init__(self, task: dict, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{ALMA_BG_ELEVATED};")
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 18)
        v.setSpacing(12)

        title = QLabel(task.get("title", "Task"))
        title.setWordWrap(True)
        title.setStyleSheet(f"color:{ALMA_TEXT_DARK}; font-size:18px; font-weight:700; border:none;")
        v.addWidget(title)

        meta = QHBoxLayout()
        meta.setSpacing(8)
        src = task.get("source", "drive")
        meta.addWidget(badge(src.capitalize(), src))
        prio = task.get("priority", "normal")
        meta.addWidget(badge(prio.capitalize(), prio))
        due = task.get("due", "—")
        if due and due != "—":
            d = QLabel(f"Due {due}")
            d.setStyleSheet(f"color:{ALMA_TEXT_MID}; font-size:12px; border:none;")
            meta.addWidget(d)
        asg = task.get("assignee", "—")
        if asg and asg != "—":
            a = QLabel(asg)
            a.setStyleSheet(f"color:{ALMA_TEXT_MID}; font-size:12px; border:none;")
            meta.addWidget(a)
        meta.addStretch(1)
        v.addLayout(meta)

        v.addWidget(self._label("SUBTASKS"))
        subtasks = task.get("subtasks") or []
        if subtasks:
            for text, done in subtasks:
                v.addLayout(self._subtask(text, bool(done)))
        else:
            empty = QLabel("No subtasks yet.")
            empty.setStyleSheet(f"color:{ALMA_TEXT_LIGHT}; font-size:12.5px; border:none;")
            v.addWidget(empty)

        v.addWidget(self._label("SCRATCH PAD"))
        note = QLabel(task.get("scratch") or "—")
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
