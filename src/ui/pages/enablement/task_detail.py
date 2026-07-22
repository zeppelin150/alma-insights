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
    completed_changed = Signal(bool) # True=complete, False=reopen (WS1-M5)
    open_source = Signal(str)        # source_url → host opens it (scheme-validated)

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
        title.setTextFormat(Qt.PlainText)   # untrusted Asana title — no rich text/HTML
        title.setStyleSheet(f"color:{ALMA_TEXT_DARK}; font-size:18px; font-weight:700; border:none;")
        v.addWidget(title)

        # Badges only on this row — short and predictable, so they never clip.
        # Assignee/submitter (variable-length names) and the Complete button
        # used to share this row; in the 440px panel they overflowed and the
        # button clipped to an unclickable sliver off the right edge
        # (2026-07-22). Each now gets its own row.
        meta = QHBoxLayout()
        meta.setSpacing(8)
        src = self._task.get("source", "drive")
        meta.addWidget(badge(src.capitalize(), src))
        prio = self._task.get("priority", "normal")
        meta.addWidget(badge(prio.capitalize(), prio))
        if self._is_asana:
            meta.addWidget(badge("Asana two-way", "asana"))
        meta.addStretch(1)
        v.addLayout(meta)

        # Who: assignee + submitter on their own wrapping line.
        asg = self._task.get("assignee", "—")
        sub = (self._task.get("submitter") or "").strip()
        if (asg and asg != "—") or sub:
            who_row = QHBoxLayout()
            who_row.setSpacing(8)
            if asg and asg != "—":
                a = QLabel(asg)
                a.setWordWrap(True)
                a.setTextFormat(Qt.PlainText)
                a.setStyleSheet(f"color:{ALMA_TEXT_MID}; font-size:12px; border:none;")
                who_row.addWidget(a)
            if sub:
                rb = QLabel(f"· requested by {sub}")
                rb.setWordWrap(True)
                rb.setTextFormat(Qt.PlainText)
                rb.setStyleSheet(f"color:{ALMA_TEXT_LIGHT}; font-size:12px; border:none;")
                who_row.addWidget(rb)
            who_row.addStretch(1)
            v.addLayout(who_row)

        # Complete/Reopen (WS1-M5): direct write — the click IS the consent.
        # Its own row so the full label always fits and stays clickable.
        is_done = (self._task.get("status") == "done")
        self._complete_btn = self._action_btn("Reopen" if is_done else "Mark complete")
        self._complete_btn.clicked.connect(
            lambda _=False, done=not is_done: self.completed_changed.emit(done))
        comp_row = QHBoxLayout()
        comp_row.addWidget(self._complete_btn)
        comp_row.addStretch(1)
        v.addLayout(comp_row)

        # ── Haiku brief (WS1-M4; only when brief_status='ok' — degrade path
        # is "render nothing extra", the raw description below stays authoritative) ──
        brief = self._task.get("brief") or {}
        if (brief.get("ask") or "").strip():
            v.addWidget(self._label("BRIEF"))
            ask_line = f"Ask: {brief.get('ask', '')}\nDeliverable: {brief.get('deliverable', '')}"
            if brief.get("effective_date"):
                ask_line += f"\nEffective: {brief['effective_date']}"
            b = QLabel(ask_line)
            b.setWordWrap(True)
            b.setTextFormat(Qt.PlainText)   # LLM output — never rich text
            b.setStyleSheet(
                f"background:{ALMA_BG_INSET}; color:{ALMA_TEXT_DARK}; "
                f"border:1px solid {ALMA_ACCENT_TEAL}; border-radius:8px; "
                f"padding:10px 12px; font-size:12.5px;")
            v.addWidget(b)
            chips = ([f"@{s}" for s in (brief.get("stakeholders") or [])[:4]]
                     + list((brief.get("links") or [])[:3]))
            if chips:
                chip_row = QHBoxLayout()
                chip_row.setSpacing(6)
                for text in chips:
                    chip = QLabel(str(text))
                    chip.setTextFormat(Qt.PlainText)
                    chip.setStyleSheet(
                        f"background:{ALMA_BG_INSET}; color:{ALMA_TEXT_MID}; "
                        f"border:1px solid {ALMA_BORDER}; border-radius:9px; "
                        f"padding:3px 9px; font-size:11px;")
                    chip_row.addWidget(chip)
                chip_row.addStretch(1)
                v.addLayout(chip_row)

        # ── Description (full Asana body) + source link ──
        desc = (self._task.get("description") or "").strip()
        if desc:
            v.addWidget(self._label("DESCRIPTION"))
            body = QLabel(desc)
            body.setWordWrap(True)
            body.setTextFormat(Qt.PlainText)   # untrusted Asana text — no rich text/HTML
            body.setStyleSheet(
                f"background:{ALMA_BG_INSET}; color:{ALMA_TEXT_MID}; border:1px solid {ALMA_BORDER}; "
                f"border-radius:8px; padding:10px 12px; font-size:12px;"
            )
            v.addWidget(body)
        url = (self._task.get("source_url") or "").strip()
        if url:
            src_btn = self._action_btn("Open in Asana ›" if self._is_asana else "Open source ›")
            src_btn.clicked.connect(lambda: self.open_source.emit(url))
            src_row = QHBoxLayout()
            src_row.addWidget(src_btn)
            src_row.addStretch(1)
            v.addLayout(src_row)

        # ── Rich Asana extras (WS1-M3; read-only, host joins lazily) ──
        extras = self._task.get("extras") or {}
        fields = [cf for cf in (extras.get("custom_fields") or [])
                  if (cf.get("display_value") or "").strip()]
        if fields:
            v.addWidget(self._label("CUSTOM FIELDS"))
            chip_row = QHBoxLayout()
            chip_row.setSpacing(6)
            for cf in fields[:6]:
                chip = QLabel(f"{cf.get('name', '')}: {cf.get('display_value', '')}")
                chip.setTextFormat(Qt.PlainText)   # untrusted Asana values
                chip.setStyleSheet(
                    f"background:{ALMA_BG_INSET}; color:{ALMA_TEXT_MID}; "
                    f"border:1px solid {ALMA_BORDER}; border-radius:9px; "
                    f"padding:3px 9px; font-size:11px;")
                chip_row.addWidget(chip)
            chip_row.addStretch(1)
            v.addLayout(chip_row)

        attachments = extras.get("attachments") or []
        if attachments:
            v.addWidget(self._label("ATTACHMENTS"))
            for att in attachments[:8]:
                name = (att.get("name") or "attachment").strip()
                a_btn = self._action_btn(f"{name} ›")
                view = (att.get("view_url") or "").strip()
                if view:
                    # Routed through open_source — the host scheme-validates.
                    a_btn.clicked.connect(
                        lambda _=False, u=view: self.open_source.emit(u))
                else:
                    a_btn.setEnabled(False)
                a_row = QHBoxLayout()
                a_row.addWidget(a_btn)
                a_row.addStretch(1)
                v.addLayout(a_row)

        stories = extras.get("stories") or []
        if stories:
            v.addWidget(self._label("COMMENTS"))
            for s in stories[-10:]:                       # newest last in Asana order
                author = (s.get("author") or "").strip() or "someone"
                when = (s.get("created_at") or "")[:10]
                c = QLabel(f"{author} · {when}\n{(s.get('text') or '').strip()}")
                c.setWordWrap(True)
                c.setTextFormat(Qt.PlainText)             # untrusted Asana text
                c.setStyleSheet(
                    f"background:{ALMA_BG_INSET}; color:{ALMA_TEXT_MID}; "
                    f"border:1px solid {ALMA_BORDER}; border-radius:8px; "
                    f"padding:8px 10px; font-size:12px;")
                v.addWidget(c)

        # ── Due date (editable; pushes to Asana when linked) ──
        v.addWidget(self._label("DUE DATE"))
        due_row = QHBoxLayout()
        due_row.setSpacing(8)
        due_val = self._task.get("due_iso") or (self._task.get("due_date") or "")[:10]
        self._due_edit = QLineEdit(due_val or "")
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
