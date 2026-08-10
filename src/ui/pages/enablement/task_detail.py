"""Task detail panel — shown in the shared DrilldownPanel when a task is clicked.

Clicking a task (calendar event or task row) opens this, NOT the Workbench. An
"Open in Workbench" button is the explicit way to jump to the editor.

Two-way Asana (Phase 2): for tasks sourced from Asana the panel exposes write-back
actions — add a subtask, post a comment, change the due date — that the host pushes
back to Asana via asana_writeback. The scratch pad stays a local-only note.

Asana-parity rebuild (WS-D, 2026-08-09): near-1:1 with Asana's own task view —
permalink-styled title, status pill, "assignee · board(s) · due" meta line, a
two-column grid of ALL custom fields, the rich description (html_notes →
markdown via html_markdown, then setMarkdown — an owner decision 2026-08-09
reversing the old PlainText-only rule; the conversion output, never raw HTML,
is what reaches Qt), card-style comment rows, per-subtask assignee/due meta,
resolve-on-click attachments (stored rows carry only a gid, so the click
resolves the URL via AsanaClient.get_attachment OFF the UI thread), and an
"Updated <relative> · Refresh" freshness row (the poll drain caps at 10/poll,
so a just-changed task can lag — Refresh asks the host for one on-demand
fetch). The host DrilldownPanel provides scrolling; nothing here scrolls
except the description browser, which auto-sizes to its document instead.
"""

from __future__ import annotations

import re
import threading
import webbrowser
from datetime import datetime, timezone

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSizePolicy, QTextBrowser, QVBoxLayout, QWidget,
)

from src.ui.pages.enablement._common import badge
from src.ui.theme import (
    ALMA_ACCENT_TEAL, ALMA_BG_ELEVATED, ALMA_BG_INSET, ALMA_BORDER,
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_INFO, ALMA_SUCCESS,
    ALMA_TEXT_DARK, ALMA_TEXT_LIGHT, ALMA_TEXT_MID, ALMA_WARNING,
)

# Status-pill classification (D2): simple keyword scan over the status text.
# complete/done → green · in progress → blue · blocked/on hold → orange ·
# anything else → gray. Substring search, so "completed"/"in_progress" match.
_STATUS_DONE_RE = re.compile(r"complete|done|closed|resolved", re.I)
_STATUS_PROGRESS_RE = re.compile(r"in[\s_\-]?progress", re.I)
_STATUS_BLOCKED_RE = re.compile(r"blocked|on[\s_\-]?hold", re.I)


class TaskDetailPanel(QWidget):
    """Task detail with two-way Asana actions + a local scratch pad."""

    open_in_workbench = Signal()
    subtask_added = Signal(str)      # subtask text (synced to Asana if linked)
    comment_posted = Signal(str)     # comment text → Asana story
    due_changed = Signal(str)        # ISO date YYYY-MM-DD (or "" to clear)
    completed_changed = Signal(bool) # True=complete, False=reopen (WS1-M5)
    open_source = Signal(str)        # source_url → host opens it (scheme-validated)
    refresh_requested = Signal()     # WS-D4: one on-demand extras fetch (host runs it)

    # Worker → main-thread hop for attachment resolution (D3). Emitted from the
    # resolver thread; the auto connection queues it onto the UI thread.
    _att_done = Signal(object, object)   # (button, payload dict)

    def __init__(self, task: dict, parent=None, *, client_factory=None):
        super().__init__(parent)
        self._task = task or {}
        self._is_asana = (self._task.get("source") == "asana")
        # D3: how the attachment resolver builds its client. Tests inject a
        # fake; production leaves None → AsanaClient.from_store() in the worker.
        self._client_factory = client_factory
        self._att_threads: list[threading.Thread] = []   # joinable in tests
        self.setStyleSheet(f"background:{ALMA_BG_ELEVATED};")
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 18)
        v.setSpacing(12)

        extras = self._task.get("extras") or {}
        source_url = (self._task.get("source_url") or "").strip()

        # ── Header: permalink-styled title + status pill + meta line ────
        title = QLabel(self._task.get("title", "Task"))
        title.setWordWrap(True)
        title.setTextFormat(Qt.PlainText)   # untrusted Asana title — no rich text/HTML
        title_css = f"QLabel{{color:{ALMA_TEXT_DARK}; font-size:18px; font-weight:700; border:none;}}"
        if source_url.lower().startswith(("http://", "https://")):
            # Styled like the Asana permalink: pointer cursor + hover underline,
            # click routes through open_source so the host scheme-validates.
            title_css += "QLabel:hover{text-decoration:underline;}"
            title.setCursor(Qt.PointingHandCursor)
            title.setToolTip("Open in Asana" if self._is_asana else "Open source")
            title.mousePressEvent = lambda e, u=source_url: self.open_source.emit(u)
        title.setStyleSheet(title_css)
        v.addWidget(title)

        # Badges only on this row — short and predictable, so they never clip.
        # Assignee/submitter (variable-length names) and the Complete button
        # used to share this row; in the 440px panel they overflowed and the
        # button clipped to an unclickable sliver off the right edge
        # (2026-07-22). Each now gets its own row.
        meta = QHBoxLayout()
        meta.setSpacing(8)
        status = (self._task.get("status") or "").strip()
        if status:
            self._status_pill_lbl = self._status_pill(status)
            meta.addWidget(self._status_pill_lbl)
        src = self._task.get("source", "drive")
        meta.addWidget(badge(src.capitalize(), src))
        prio = self._task.get("priority", "normal")
        meta.addWidget(badge(prio.capitalize(), prio))
        if self._is_asana:
            meta.addWidget(badge("Asana two-way", "asana"))
        meta.addStretch(1)
        v.addLayout(meta)

        # Meta line "Assignee · Board(s) · Due" (Asana's field strip, condensed).
        asg = self._task.get("assignee", "—")
        meta_parts = []
        if asg and asg != "—":
            meta_parts.append(asg)
        boards = [b for b in (self._task.get("board_names") or []) if b]
        if boards:
            meta_parts.append(", ".join(boards))
        due_disp = (self._task.get("due") or "").strip()
        if due_disp and due_disp != "—":
            meta_parts.append(f"Due {due_disp}")
        if meta_parts:
            m = QLabel(" · ".join(meta_parts))
            m.setWordWrap(True)
            m.setTextFormat(Qt.PlainText)
            m.setStyleSheet(f"color:{ALMA_TEXT_MID}; font-size:12px; border:none;")
            v.addWidget(m)
        sub = (self._task.get("submitter") or "").strip()
        if sub:
            rb = QLabel(f"requested by {sub}")
            rb.setWordWrap(True)
            rb.setTextFormat(Qt.PlainText)
            rb.setStyleSheet(f"color:{ALMA_TEXT_LIGHT}; font-size:12px; border:none;")
            v.addWidget(rb)

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

        # ── Freshness row (WS-D4; Asana tasks only) ─────────────────────
        # The panel reads the local mirror, and the poll's extras drain caps at
        # 10/poll — so a just-changed task can lag. Shows how old the mirror is
        # (extras.fetched_at) and offers one on-demand fetch via the host.
        if self._is_asana:
            fetched = (extras.get("fetched_at") or "").strip()
            self._updated_lbl = QLabel(
                f"Updated {self._rel_time(fetched)}" if fetched else "Not synced yet")
            self._updated_lbl.setTextFormat(Qt.PlainText)
            # background:transparent is load-bearing: inside the drilldown's
            # scroll host a bare QLabel inherits an opaque ancestor rule and
            # paints a white box over its neighbors (2026-08-10 report).
            self._updated_lbl.setStyleSheet(
                f"background:transparent; color:{ALMA_TEXT_LIGHT}; "
                f"font-size:11px; border:none;")
            self._refresh_btn = self._action_btn("Refresh")
            self._refresh_btn.clicked.connect(self._on_refresh_clicked)
            fresh_row = QHBoxLayout()
            fresh_row.setSpacing(8)
            fresh_row.addWidget(self._updated_lbl)
            fresh_row.addWidget(self._refresh_btn)
            fresh_row.addStretch(1)
            v.addLayout(fresh_row)

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

        # ── Custom fields: two-column name-over-value grid, ALL fields ──
        # (the old chip row capped at 6 and dropped empty values; Asana's pane
        # shows every field, with "—" standing in for an unset value)
        fields = [cf for cf in (extras.get("custom_fields") or [])
                  if (cf.get("name") or "").strip()]
        if fields:
            v.addWidget(self._label("CUSTOM FIELDS"))
            grid = QGridLayout()
            grid.setHorizontalSpacing(14)
            grid.setVerticalSpacing(10)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            for i, cf in enumerate(fields):
                cell = QVBoxLayout()
                cell.setSpacing(1)
                name_lbl = QLabel(cf.get("name", ""))
                name_lbl.setTextFormat(Qt.PlainText)   # untrusted Asana values
                name_lbl.setWordWrap(True)
                name_lbl.setStyleSheet(
                    f"color:{ALMA_TEXT_LIGHT}; font-size:10.5px; font-weight:600; border:none;")
                cell.addWidget(name_lbl)
                val_lbl = QLabel((cf.get("display_value") or "").strip() or "—")
                val_lbl.setTextFormat(Qt.PlainText)
                val_lbl.setWordWrap(True)
                val_lbl.setStyleSheet(
                    f"color:{ALMA_TEXT_DARK}; font-size:12.5px; border:none;")
                cell.addWidget(val_lbl)
                grid.addLayout(cell, i // 2, i % 2)
            v.addLayout(grid)

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

        # ── Description (rich when html_notes exists; collapsible) ──────
        # Owner decision 2026-08-09: render RICH. html_notes never reaches Qt
        # as HTML — it goes through html_to_markdown and the browser gets the
        # markdown (Asana's tag vocabulary is small, so this is visually
        # equivalent with zero HTML-injection surface). Empty/failed
        # conversion falls back to the plain-text description.
        notes_html = (extras.get("html_notes") or "").strip()
        desc = (self._task.get("description") or "").strip()
        md = ""
        if notes_html:
            try:
                from src.data.html_markdown import html_to_markdown
                md = (html_to_markdown(notes_html) or "").strip()
            except Exception:  # noqa: BLE001 — conversion is best-effort
                md = ""
        if md or desc:
            self._desc_browser = QTextBrowser()
            self._desc_browser.setOpenExternalLinks(True)
            self._desc_browser.setStyleSheet(
                f"QTextBrowser{{background:{ALMA_BG_INSET}; color:{ALMA_TEXT_DARK}; "
                f"border:1px solid {ALMA_BORDER}; border-radius:8px; "
                f"padding:6px 8px; font-size:12.5px;}}")
            if md:
                self._desc_browser.setMarkdown(md)
            else:
                self._desc_browser.setPlainText(desc)
            self._autosize_browser(self._desc_browser)
            head = QHBoxLayout()
            head.setSpacing(8)
            head.addWidget(self._label("DESCRIPTION"))
            head.addStretch(1)
            head.addWidget(self._collapse_toggle(self._desc_browser))
            v.addLayout(head)
            v.addWidget(self._desc_browser)
        if source_url:
            src_btn = self._action_btn("Open in Asana ›" if self._is_asana else "Open source ›")
            src_btn.clicked.connect(lambda: self.open_source.emit(source_url))
            src_row = QHBoxLayout()
            src_row.addWidget(src_btn)
            src_row.addStretch(1)
            v.addLayout(src_row)

        # ── Subtasks (existing checklist + assignee·due meta + add row) ──
        v.addWidget(self._label("SUBTASKS"))
        self._subs_box = QVBoxLayout()
        self._subs_box.setSpacing(4)
        subtasks = self._task.get("subtasks") or []
        if subtasks:
            for entry in subtasks:
                # Entries arrive as (text, done) tuples (legacy) or dicts that
                # additionally carry assignee/due meta from promoted subtask
                # rows (WS-D2) — render the meta line only when present.
                if isinstance(entry, dict):
                    text = entry.get("text", "")
                    done = bool(entry.get("done"))
                    s_meta = " · ".join(x for x in (
                        (entry.get("assignee") or "").strip(),
                        (entry.get("due") or "").strip()) if x)
                else:
                    text, done = entry[0], bool(entry[1])
                    s_meta = ""
                self._subs_box.addLayout(self._subtask(text, done, s_meta))
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

        # ── Attachments (WS-D3: resolve-on-click) ───────────────────────
        # Stored attachment rows carry only gid+name+subtype (no URLs), so the
        # click resolves the URL live: AsanaClient.get_attachment(gid) OFF the
        # UI thread, then webbrowser.open. A pre-stored view_url (legacy shape)
        # still routes through open_source so the host scheme-validates it.
        attachments = extras.get("attachments") or []
        if attachments:
            v.addWidget(self._label("ATTACHMENTS"))
            self._att_done.connect(self._on_att_done)
            for att in attachments:
                name = (att.get("name") or "attachment").strip()
                a_btn = self._action_btn(f"{name} ›")
                a_btn.clicked.connect(
                    lambda _=False, b=a_btn, a=att: self._on_attachment_clicked(b, a))
                a_row = QHBoxLayout()
                a_row.addWidget(a_btn)
                a_row.addStretch(1)
                v.addLayout(a_row)
            self._att_error = QLabel("")
            self._att_error.setWordWrap(True)
            self._att_error.setTextFormat(Qt.PlainText)   # carries API error text
            self._att_error.setStyleSheet(
                f"color:{ALMA_WARNING}; font-size:11px; border:none;")
            self._att_error.hide()
            v.addWidget(self._att_error)

        # ── Comments (card rows: author bold · local time · pre-wrap body) ──
        stories = extras.get("stories") or []
        if stories:
            cards = QWidget()
            cards.setStyleSheet("background:transparent;")
            cards_box = QVBoxLayout(cards)
            cards_box.setContentsMargins(0, 0, 0, 0)
            cards_box.setSpacing(6)
            for s in stories:                             # newest last in Asana order
                cards_box.addWidget(self._comment_card(s))
            head = QHBoxLayout()
            head.setSpacing(8)
            head.addWidget(self._label(f"COMMENTS ({len(stories)})"))
            head.addStretch(1)
            head.addWidget(self._collapse_toggle(cards))
            v.addLayout(head)
            v.addWidget(cards)

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

    # ── WS-D4: freshness ─────────────────────────────────────────────
    def _on_refresh_clicked(self):
        """Hand the fetch to the host (it owns the DB path + client) and lock
        the button; the host re-opens a fresh panel when the fetch lands, so
        no un-lock is needed here."""
        self._refresh_btn.setEnabled(False)
        self._refresh_btn.setText("Refreshing…")
        self.refresh_requested.emit()

    @staticmethod
    def _rel_time(iso: str) -> str:
        """'just now' / '5m ago' / '3h ago' / '2d ago' from a UTC ISO stamp."""
        try:
            s = (iso or "").strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            secs = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
            if secs < 60:
                return "just now"
            if secs < 3600:
                return f"{int(secs // 60)}m ago"
            if secs < 86400:
                return f"{int(secs // 3600)}h ago"
            return f"{int(secs // 86400)}d ago"
        except Exception:  # noqa: BLE001 — a bad stamp must not break the panel
            return "—"

    # ── WS-D3: attachment resolution ─────────────────────────────────
    def _on_attachment_clicked(self, btn: QPushButton, att: dict):
        name = (att.get("name") or "attachment").strip()
        view = (att.get("view_url") or "").strip()
        if view:
            # Legacy pre-stored URL — route through open_source (host validates).
            self.open_source.emit(view)
            return
        gid = (att.get("gid") or "").strip()
        if not gid:
            self._show_att_error(name, "no attachment id stored")
            return
        btn.setEnabled(False)
        btn.setText(f"{name} — resolving…")
        if hasattr(self, "_att_error"):
            self._att_error.hide()
        t = threading.Thread(
            target=self._resolve_attachment, args=(btn, gid, name), daemon=True)
        self._att_threads.append(t)
        t.start()

    def _resolve_attachment(self, btn, gid: str, name: str):
        """Worker thread — NEVER touches widgets; ships the result back via
        the _att_done signal (auto connection → queued onto the UI thread)."""
        payload = {"label": f"{name} ›", "name": name, "url": "", "error": ""}
        try:
            if self._client_factory is not None:
                client = self._client_factory()
            else:
                from src.data.asana_client import AsanaClient
                client = AsanaClient.from_store()
            res = client.get_attachment(gid) or {}
            payload["url"] = (res.get("view_url") or res.get("download_url") or "").strip()
            if not payload["url"]:
                payload["error"] = "Asana returned no URL for this attachment"
        except Exception as exc:  # noqa: BLE001 — error is surfaced on the row
            payload["error"] = str(exc)
        try:
            self._att_done.emit(btn, payload)
        except RuntimeError:
            pass    # panel deleted while the resolve was in flight

    def _on_att_done(self, btn, payload: dict):
        """Main-thread landing: restore the button, then open or report."""
        try:
            btn.setEnabled(True)
            btn.setText(payload.get("label") or "attachment ›")
        except RuntimeError:
            return  # button already destroyed with the panel
        name = payload.get("name") or "attachment"
        if payload.get("error"):
            self._show_att_error(name, payload["error"])
            return
        url = payload.get("url") or ""
        if url.lower().startswith(("http://", "https://")):
            webbrowser.open(url)
        else:
            # Mirrors the host's _open_source_url rule: web schemes only.
            self._show_att_error(name, "refused to open a non-web attachment URL")

    def _show_att_error(self, name: str, err: str):
        if hasattr(self, "_att_error"):
            self._att_error.setText(f"{name}: {err}")
            self._att_error.show()

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
        lbl.setStyleSheet(f"background:transparent; color:{ALMA_TEXT_LIGHT}; font-size:10.5px; font-weight:700; letter-spacing:0.5px; border:none;")
        return lbl

    def _status_pill(self, status: str) -> QLabel:
        """Colored status pill (D2): keyword-classified, gray by default."""
        s = (status or "").strip()
        if _STATUS_DONE_RE.search(s):
            bg, fg = "#E4EFE9", ALMA_SUCCESS
        elif _STATUS_PROGRESS_RE.search(s):
            bg, fg = "#E2ECF4", ALMA_INFO
        elif _STATUS_BLOCKED_RE.search(s):
            bg, fg = "#F6EBDD", ALMA_WARNING
        else:
            bg, fg = "#ECEAE5", ALMA_TEXT_LIGHT
        disp = s.replace("_", " ")
        disp = (disp[:1].upper() + disp[1:]) if disp else disp
        lbl = QLabel(disp)
        lbl.setTextFormat(Qt.PlainText)   # status text is source data
        lbl.setStyleSheet(
            f"background:{bg}; color:{fg}; border:none; border-radius:10px; "
            f"padding:2px 10px; font-size:11px; font-weight:700;")
        lbl.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        return lbl

    def _collapse_toggle(self, body: QWidget) -> QPushButton:
        """▴/▾ section toggle (the FindingCard idiom) — starts expanded."""
        btn = QPushButton("▴ Hide")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton{{background:transparent; color:{ALMA_TEXT_LIGHT}; "
            f"border:none; font-size:11px; font-weight:600;}}")
        state = {"open": True}

        def _flip(_=False):
            state["open"] = not state["open"]
            body.setVisible(state["open"])
            btn.setText("▴ Hide" if state["open"] else "▾ Show")

        btn.clicked.connect(_flip)
        return btn

    def _autosize_browser(self, browser: QTextBrowser):
        """Grow the browser to its document height — the DrilldownPanel scroll
        area (WS-D1) owns scrolling, so the browser itself never scrolls."""
        browser.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        browser.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        browser.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        def _fit(_size=None, b=browser):
            h = int(b.document().documentLayout().documentSize().height())
            b.setFixedHeight(max(40, h + 18))

        browser.document().documentLayout().documentSizeChanged.connect(_fit)
        _fit()

    def _comment_card(self, story: dict) -> QFrame:
        """One Asana story as a card: author bold · local time, pre-wrap body."""
        author = (story.get("author") or "").strip() or "someone"
        when = self._fmt_story_ts(story.get("created_at") or "")
        card = QFrame()
        card.setStyleSheet(
            f"QFrame{{background:{ALMA_BG_INSET}; border:1px solid {ALMA_BORDER}; "
            f"border-radius:8px;}}")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(10, 8, 10, 8)
        cl.setSpacing(2)
        head = QLabel(f"{author} · {when}" if when else author)
        head.setWordWrap(True)
        head.setTextFormat(Qt.PlainText)              # untrusted Asana text
        head.setStyleSheet(
            f"background:transparent; color:{ALMA_TEXT_DARK}; border:none; "
            f"font-size:12px; font-weight:700;")
        cl.addWidget(head)
        body = QLabel((story.get("text") or "").strip())
        body.setWordWrap(True)                        # PlainText + wrap = pre-wrap
        body.setTextFormat(Qt.PlainText)              # untrusted Asana text
        body.setStyleSheet(
            f"background:transparent; color:{ALMA_TEXT_MID}; border:none; "
            f"font-size:12px;")
        cl.addWidget(body)
        return card

    @staticmethod
    def _fmt_story_ts(iso: str) -> str:
        """Asana ISO stamp → local 'MMM d, h:mm AM' (e.g. 'Jul 14, 6:22 PM');
        unparseable input degrades to the raw date prefix."""
        try:
            s = (iso or "").strip()
            if not s:
                return ""
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is not None:
                dt = dt.astimezone()                  # localize
            hour = dt.strftime("%I").lstrip("0") or "12"
            return f"{dt.strftime('%b')} {dt.day}, {hour}:{dt.strftime('%M')} {dt.strftime('%p')}"
        except Exception:  # noqa: BLE001
            return (iso or "")[:10]

    def _subtask(self, text: str, done: bool, meta: str = "") -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        box = QLabel("")
        box.setFixedSize(14, 14)
        box.setStyleSheet(
            f"background:{ALMA_GREEN_LIGHT if done else ALMA_BG_ELEVATED}; "
            f"border:1px solid {ALMA_GREEN_LIGHT if done else ALMA_BORDER}; border-radius:4px;"
        )
        row.addWidget(box, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(0)
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.PlainText)
        lbl.setStyleSheet(f"color:{ALMA_TEXT_LIGHT if done else ALMA_TEXT_MID}; font-size:12.5px; border:none;")
        col.addWidget(lbl)
        if meta:
            m = QLabel(meta)
            m.setTextFormat(Qt.PlainText)
            m.setStyleSheet(f"color:{ALMA_TEXT_LIGHT}; font-size:11px; border:none;")
            col.addWidget(m)
        row.addLayout(col, 1)
        return row
