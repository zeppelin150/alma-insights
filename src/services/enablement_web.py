"""Controllers behind the enablement web tabs (``enablement.web_tabs``).

``CalendarWebController`` is the Python owner of the web Calendar's state: it
receives the SAME task rows page.py already feeds the Qt ``CalendarPage``
(duck-typed ``set_tasks``/``set_scope`` + the same outbound signals), shapes
them into a JSON viewmodel, and pushes it to the React route through the
bridge. The web layer is a pure renderer — every fact it shows comes from
here, and every click routes back here for resolution against Python state.

Bridge slots hand this controller UNTRUSTED input (any page script can invoke
them): task ids resolve only against the ids of the last ``set_tasks`` feed,
scopes are validated against the two known values, and briefs come from the
injected ``brief_lookup`` (never from the page).
"""

from __future__ import annotations

import json
import re
import uuid

from PySide6.QtCore import QObject, Signal

_VALID_SCOPES = ("mine", "all")
_DESCRIPTION_CAP = 240
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Chip kinds the web calendar styles — superset of the Qt calendar's TINT use.
_KINDS = ("drive", "guru", "asana", "high", "done", "normal")


class CalendarWebController(QObject):
    """State + viewmodel for the web Calendar tab.

    Mirrors the Qt ``CalendarPage`` surface page.py drives (``set_tasks``,
    ``set_scope``, and the ``scope_changed`` / ``event_activated`` /
    ``event_clicked`` / ``day_expanded`` signals — the latter two exist for
    wiring parity but the web path never emits them; day drill-down and chip
    clicks resolve in-page or via ``open_task``).
    """

    calendar_data = Signal(str)     # JSON viewmodel push → bridge.calendarData
    brief_ready = Signal(str)       # JSON {task_id, brief|null} → bridge.briefReady
    scope_changed = Signal(str)     # operator toggled Mine/All in the web UI
    event_activated = Signal(dict)  # full task payload → host opens TaskDetailPanel
    event_clicked = Signal(str)     # wiring parity with CalendarPage (never emitted)
    day_expanded = Signal(str)      # wiring parity with CalendarPage (never emitted)
    # M2 gated reschedule: the resolution of one drag (approved/cancelled/
    # dispatched) → bridge.rescheduleResolved; dispatched fires only when the
    # write was actually handed to the host's write-back path.
    reschedule_resolved = Signal(str)         # JSON, see request_reschedule
    reschedule_dispatched = Signal(str, str)  # (task_id, iso_date)

    def __init__(self, brief_lookup=None, today_fn=None, confirm_fn=None,
                 write_fn=None, parent=None):
        super().__init__(parent)
        # ``brief_lookup(task_id) -> dict | None`` is injected by the host
        # (page.py owns the DB path); absent → hover cards show meta only.
        self._brief_lookup = brief_lookup
        # ``today_fn`` is test-pinnable, like calendar.py's ``_today``.
        self._today_fn = today_fn
        # ``confirm_fn(task_dict, iso_date) -> bool`` is the NATIVE human gate
        # (a QMessageBox in page.py — physically unreachable from Chromium).
        # ``write_fn(task_id, iso_date)`` dispatches the actual write (page.py
        # routes it to _run_task_writeback → asana_writeback.update_due_in_asana,
        # whose completion already reloads every view). Both injected; absent →
        # the gate FAILS CLOSED (no confirm possible → no write, ever).
        self._confirm_fn = confirm_fn
        self._write_fn = write_fn
        self._reschedule_inflight = False
        self._scope = "mine"
        self._tasks_by_id: dict[str, dict] = {}
        self._last_payload: str | None = None

    # ── the CalendarPage-compatible surface page.py drives ──────────
    def set_tasks(self, tasks):
        """Ingest the host's task rows (same shape both feed paths produce)
        and push a fresh viewmodel to the web page."""
        events = []
        self._tasks_by_id = {}
        for t in tasks or []:
            if not isinstance(t, dict):
                continue
            event_id = self._event_id(t)
            if event_id is None:
                continue
            self._tasks_by_id[event_id] = dict(t)
            due = str(t.get("due_date") or "")
            if len(due) < 10:
                continue               # undated rows are openable, never drawn
            source = t.get("source")
            kind = source if source in _KINDS else "normal"
            events.append({
                "id": event_id,
                "title": str(t.get("title") or "Task"),
                "date": due[:10],
                "kind": kind,
                "status": str(t.get("status") or ""),
                "priority": str(t.get("priority") or "normal"),
                "assignee": str(t.get("assignee") or ""),
                "subs": str(t.get("subs") or ""),
                "description": str(t.get("description") or "")[:_DESCRIPTION_CAP],
                "is_card_due": t.get("kind") == "guru_card_due",
                "is_subtask": bool(t.get("is_subtask")),
                "parent_title": str(t.get("parent_title") or ""),
            })
        self._push({"events": events})

    def set_scope(self, scope):
        """Host sets the toggle state without echoing scope_changed (the same
        no-loop contract as CalendarPage.set_scope)."""
        if scope not in _VALID_SCOPES or scope == self._scope:
            return
        self._scope = scope
        self._repush_scope_only()

    # ── bridge-facing entry points (UNTRUSTED input) ─────────────────
    def request_refresh(self):
        """Web page asked for the current state (initial mount, reconnect)."""
        if self._last_payload is not None:
            self.calendar_data.emit(self._last_payload)

    def request_scope(self, scope):
        """Operator toggled Mine/All in the web UI → the host persists,
        refilters at the SQL layer, and feeds set_tasks back (page.py's
        existing _on_scope_changed path)."""
        if scope in _VALID_SCOPES and scope != self._scope:
            self._scope = scope
            self._repush_scope_only()
            self.scope_changed.emit(scope)

    def open_task(self, task_id):
        """Chip clicked in the web UI. Resolves ONLY against the last feed —
        a forged/stale id is a no-op."""
        task = self._tasks_by_id.get(str(task_id or ""))
        if task:
            self.event_activated.emit(task)

    def request_reschedule(self, task_id, new_date):
        """A chip was dropped on a new day. This slot NEVER writes directly —
        the sequence is validate → single-winner claim → NATIVE confirm →
        dispatch to the host's existing write-back path (CAS-guarded,
        off-thread, reload-on-completion).

        Silent no-ops (no resolution emitted — a forged caller gets no oracle):
        forged/stale ids, guru card-due chips (not tasks), malformed dates,
        and same-date drops. A second invoke while a confirm dialog is open
        (the modal spins a nested event loop) is also a silent no-op — the
        inflight flag is claimed BEFORE the dialog, so one drag = at most one
        dialog = at most one write.
        """
        key = str(task_id or "")
        task = self._tasks_by_id.get(key)
        # Strict full-string date check — no truncation. The page only ever
        # sends bare ISO dates; trimming first would launder junk like
        # "2026-07-211" into a valid-looking day.
        date = str(new_date or "")
        if not task or task.get("kind") == "guru_card_due":
            return
        if not _ISO_DATE.match(date):
            return
        if date == str(task.get("due_date") or "")[:10]:
            return
        if self._reschedule_inflight:
            return
        self._reschedule_inflight = True
        try:
            request_id = uuid.uuid4().hex[:12]
            approved = False
            if self._confirm_fn is not None:
                try:
                    approved = bool(self._confirm_fn(dict(task), date))
                except Exception:  # noqa: BLE001 — a broken dialog means NO
                    approved = False
            dispatched = False
            if approved and self._write_fn is not None:
                try:
                    self._write_fn(key, date)
                    dispatched = True
                except Exception:  # noqa: BLE001 — dispatch failure surfaces below
                    dispatched = False
            try:
                self.reschedule_resolved.emit(json.dumps({
                    "request_id": request_id, "task_id": key, "date": date,
                    "approved": approved, "dispatched": dispatched,
                    "cancelled": not approved,
                }))
            except Exception:  # noqa: BLE001
                pass
            if dispatched:
                self.reschedule_dispatched.emit(key, date)
        finally:
            self._reschedule_inflight = False

    def request_brief(self, task_id):
        """Hover card asked for the Haiku brief. Same lazy-join contract as
        the Qt detail panel: the feed stays lean, briefs load on demand."""
        key = str(task_id or "")
        if key not in self._tasks_by_id:
            return
        brief = None
        if self._brief_lookup is not None:
            try:
                brief = self._brief_lookup(key)
            except Exception:  # noqa: BLE001 — briefs are enrichment only
                brief = None
        try:
            self.brief_ready.emit(json.dumps(
                {"task_id": key, "brief": brief}, default=str))
        except Exception:  # noqa: BLE001
            pass

    # ── internals ────────────────────────────────────────────────────
    def _event_id(self, t: dict):
        tid = t.get("task_id")
        if tid:
            return str(tid)
        if t.get("kind") == "guru_card_due" and t.get("card_id"):
            return f"guru:{t['card_id']}"
        return None

    def _today(self) -> str:
        if self._today_fn is not None:
            try:
                return str(self._today_fn())[:10]
            except Exception:  # noqa: BLE001
                pass
        import datetime
        return datetime.date.today().isoformat()

    def _push(self, body: dict):
        payload = dict(body)
        payload["scope"] = self._scope
        payload["today"] = self._today()
        try:
            self._last_payload = json.dumps(payload, default=str)
        except Exception:  # noqa: BLE001 — never crash the feed path
            return
        self.calendar_data.emit(self._last_payload)

    def _repush_scope_only(self):
        """Re-emit the last events with the new scope (the host's reload will
        follow with fresh rows; this keeps the toggle responsive meanwhile)."""
        if self._last_payload is None:
            self._push({"events": []})
            return
        try:
            body = json.loads(self._last_payload)
        except Exception:  # noqa: BLE001
            body = {"events": []}
        self._push({"events": body.get("events", [])})


class WorkbenchWebController(QObject):
    """State + viewmodel for the web Workbench tab (M3: read-only).

    Mirrors the Qt ``WorkbenchPage`` surface page.py drives — including the
    ``_current_drafts`` attribute page.py reads directly in
    ``_on_workspace_closed`` — so every feed, connect, and property access in
    page.py works identically against either build.

    The preview is the security-critical seam: EVERY ``preview_html`` that
    leaves this controller is :func:`src.data.enablement_store.publish_body`'s
    output — the exact bytes a publish would send — passed through
    :func:`src.data.html_sanitize.sanitize_html`. The page renders it inside a
    fully-sandboxed ``srcdoc`` iframe (no scripts), so the sanitizer and the
    sandbox each independently stop execution. The publish belt certifies the
    same ``publish_body`` string, so the screen, the belt and the network all
    read one variable.
    """

    MAX_WORKSPACES = 4          # parity with WorkbenchPage

    # web-facing pushes
    workbench_data = Signal(str)   # JSON {chips, active_id}
    draft_loaded = Signal(str)     # JSON card viewmodel (sanitized preview)
    diff_ready = Signal(str)       # JSON {rows, change_count, baseline_present}
    preview_updated = Signal(str)  # JSON {id, preview_html, checks} — edit echo
    existing_cards_data = Signal(str)  # JSON {items: [{key, title}]}
    publish_resolved = Signal(str)     # JSON {request_id, dest, approved, dispatched, cancelled}
    ai_edit_resolved = Signal(str)     # JSON {ok} — fires on BOTH success and
                                       # failure so the web AI-edit bar always
                                       # un-busies (a failed/no-op revise emits
                                       # no draft_loaded, so we can't rely on it)
    # WorkbenchPage signal-surface parity (page.py connects all of these).
    # M3 emits draft_selected / workspace_closed / find_task_requested /
    # open_chat_requested / upload flows; the rest stay silent until M4.
    draft_selected = Signal(int)
    workspace_closed = Signal(int)
    find_task_requested = Signal()
    publish_requested = Signal(str)
    load_file_requested = Signal(str)
    open_chat_requested = Signal()
    existing_cards_requested = Signal()
    import_requested = Signal(str)
    content_edited = Signal(int, str)
    ai_edit_requested = Signal(str, str)
    upload_requested = Signal()    # web Upload button → host opens the native dialog

    _PUBLISH_STATIC = ("guru_new", "drive_new", "drive_update")
    _AI_INSTRUCTION_CAP = 2000
    _AI_SELECTION_CAP = 8000

    def __init__(self, checks_fn=None, md_to_html_fn=None,
                 publish_confirm_fn=None, content_lookup=None,
                 busy_lookup=None, parent=None):
        super().__init__(parent)
        # ``checks_fn(markdown) -> [ {check, status, detail}, … ]`` — the host
        # injects enablement_checks.run_checks (offline, deterministic).
        self._checks_fn = checks_fn
        # ``md_to_html_fn(markdown) -> html`` — defaults to the repo's
        # html_markdown converter; injected in tests. Output is ALWAYS
        # sanitized afterwards regardless of source.
        self._md_to_html_fn = md_to_html_fn
        # ``publish_confirm_fn(dest_key, title) -> bool`` — the NATIVE human
        # gate for a web publish click (a QMessageBox in page.py, unreachable
        # from Chromium). Absent → publishing FAILS CLOSED.
        self._publish_confirm_fn = publish_confirm_fn
        # ``content_lookup(draft_id) -> str|None`` reads the stored draft's
        # PUBLISH BODY straight from the store (page.py owns the DB, and
        # returns enablement_store.publish_body(draft)). The publish belt
        # compares this — the exact bytes store.publish_draft will SEND — not
        # the in-memory _cards, so no cache/DB divergence can slip a body the
        # operator didn't approve past the gate.
        self._content_lookup = content_lookup
        # ``busy_lookup() -> bool`` reports whether the shared chat engine is
        # mid-turn (a chat-driven revise_draft tool also writes the DB from a
        # worker). Publishing is refused while it's True so no background
        # revise can overlap the publish's DB read.
        self._busy_lookup = busy_lookup
        self._publish_inflight = False
        # True while an off-thread revise is running for the active draft. A
        # revise writes the DB body from a worker thread, so a publish that
        # overlaps it could ship content the operator never saw — refuse to
        # publish until it settles (and the canvas/cache/DB reconverge).
        self._ai_edit_inflight = False
        self._current_drafts: list[dict] = []   # page.py reads this directly
        self._active_draft_id = None
        self._cards: dict = {}                  # draft_id -> last shown card
        self._linked_card_md = ""
        self._existing_keys: set = set()        # publish targets we offered
        self._existing_cards: list = []         # last host-fed card list
        self._existing_cards_fetched = False    # a host result (even []) arrived
        # (active_id, markdown) of the last draft_loaded we emitted. A host
        # feed refresh (chat turn, Asana poll, task write-back) that re-pushes
        # the SAME draft with IDENTICAL content is a no-op for the page — and
        # emitting draft_loaded anyway would wipe the operator's uncommitted
        # editor text and force them out of the Edit view. Skip those.
        self._last_pushed = None

    # ── the WorkbenchPage-compatible surface page.py drives ─────────
    @property
    def active_draft_id(self):
        return self._active_draft_id

    @property
    def is_publish_inflight(self):
        """True while a publish confirm is open — the host freezes concurrent
        chat sends against this so a page script can't start a revise during
        the modal."""
        return self._publish_inflight

    def set_pending_drafts(self, drafts, active_id=None):
        """Set the open workspace chips (capped at MAX_WORKSPACES) — the same
        active-id defaulting as the Qt page."""
        drafts = [dict(d) for d in list(drafts or [])[: self.MAX_WORKSPACES]]
        self._current_drafts = drafts
        ids = [d.get("id") for d in drafts]
        if active_id is not None:
            self._active_draft_id = active_id
        elif drafts and self._active_draft_id not in ids:
            self._active_draft_id = drafts[0].get("id")
        elif not drafts:
            self._active_draft_id = None
        self._cards = {k: v for k, v in self._cards.items() if k in ids}
        self._push_workspaces()

    def show_draft(self, card: dict):
        """Render a card as the active draft's content (sanitized preview)."""
        card = dict(card or {})
        self._linked_card_md = card.get("linked_card_md", "") or ""
        if self._active_draft_id is not None:
            self._cards[self._active_draft_id] = card
        self._push_draft(card)

    def open_workspace(self, draft: dict):
        """Open a chip: activate if already open, else append (dropping the
        oldest beyond MAX_WORKSPACES) — WorkbenchPage semantics."""
        cur = list(self._current_drafts)
        ids = [d.get("id") for d in cur]
        did = (draft or {}).get("id")
        if did in ids:
            self.set_active_draft(did)
            return
        if len(cur) >= self.MAX_WORKSPACES:
            dropped = cur.pop(0)
            self._cards.pop(dropped.get("id"), None)
        cur.append(dict(draft))
        self.set_pending_drafts(cur, active_id=did)

    def switch_workspace(self, draft_id, card=None):
        """Activate a workspace; page.py passes the freshly-loaded card."""
        self.set_active_draft(draft_id)
        if card is not None:
            self.show_draft(card)

    def set_active_draft(self, draft_id):
        self.set_pending_drafts(self._current_drafts, active_id=draft_id)
        cached = self._cards.get(draft_id)
        if cached is not None:
            self._push_draft(cached)

    def reload_active_canvas(self, card: dict):
        """Server-side revise replaced the active draft's content — re-render."""
        self.show_draft(card)

    def notify_ai_edit_done(self, ok: bool):
        """The host's off-thread revise finished (success OR failure) — un-busy
        the web AI-edit bar and release the publish block. Decoupled from
        draft_loaded because a failed or content-identical revise emits none."""
        self._ai_edit_inflight = False
        try:
            self.ai_edit_resolved.emit(json.dumps({"ok": bool(ok)}))
        except Exception:  # noqa: BLE001
            pass

    def set_linked_card_md(self, md: str):
        """The Review-changes diff baseline (host-fed)."""
        self._linked_card_md = md or ""

    def set_existing_cards(self, cards):
        """Host feeds real Guru cards for the 'Existing card' submenu. The keys
        we push are the ONLY guru_existing publish targets we'll later accept
        (fail-closed against forged card ids)."""
        items = []
        self._existing_keys = set()
        for c in cards or []:
            key = str(c.get("id") or c.get("title") or "").strip()
            if not key:
                continue
            self._existing_keys.add(key)
            items.append({"key": key, "title": str(c.get("title") or key)})
        self._existing_cards = items
        self._existing_cards_fetched = True   # a host result arrived (may be [])
        try:
            self.existing_cards_data.emit(json.dumps({"items": items}, default=str))
        except Exception:  # noqa: BLE001
            pass

    def current_html(self):
        """The active card's stored rich HTML (M3 has no editing, so this is
        exactly what the host fed in — parity for _on_content_edited)."""
        card = self._cards.get(self._active_draft_id) or {}
        return card.get("content_html")

    def set_overlay_host(self, _widget):
        """Qt expand-overlay parity no-op — the web expand is a CSS route."""

    # ── bridge-facing entry points (UNTRUSTED input) ─────────────────
    def request_refresh(self):
        # A reconnecting/remounted page has no state — force a fresh push even
        # if the content matches what we last emitted (invalidate the dedup).
        self._last_pushed = None
        self._push_workspaces()
        card = self._cards.get(self._active_draft_id)
        if card is not None:
            self._push_draft(card)

    def js_switch_workspace(self, draft_id):
        """Chip clicked in the web UI → the host loads the draft and calls
        switch_workspace back (same loop as the Qt chip). Forged ids no-op.

        FROZEN during a publish confirm: the native QMessageBox spins a nested
        event loop, so a page script could otherwise call this mid-modal to
        change the active draft and redirect the publish (confused deputy). A
        real operator can't reach this slot while the modal blocks web input,
        so freezing it has no UX cost."""
        if self._publish_inflight:
            return
        did = self._known_id(draft_id)
        if did is not None:
            self.draft_selected.emit(did)

    def js_close_workspace(self, draft_id):
        if self._publish_inflight:        # frozen during a publish confirm
            return
        did = self._known_id(draft_id)
        if did is not None:
            self.workspace_closed.emit(did)

    def js_request_diff(self):
        """Review-changes: word-level diff of the active draft against its
        linked-card baseline, computed in Python (pure renderer rule).

        The proposed side is ``review_text`` — the reviewer-facing text of the
        PUBLISH BODY — so a draft whose ``content_html`` differs from its
        markdown is diffed as what ships, not as the unused markdown column."""
        from src.data.enablement_store import review_text
        from src.data.text_diff import change_count, diff_words
        card = self._cards.get(self._active_draft_id) or {}
        proposed = review_text({"content": card.get("markdown") or "",
                                "content_html": card.get("content_html")},
                               md_to_html=self._render_markdown)
        rows = diff_words(self._linked_card_md, proposed)
        try:
            self.diff_ready.emit(json.dumps({
                "rows": rows,
                "change_count": change_count(rows),
                "baseline_present": bool(self._linked_card_md),
            }, default=str))
        except Exception:  # noqa: BLE001
            pass

    def js_upload(self):
        self.upload_requested.emit()

    def js_find_task(self):
        self.find_task_requested.emit()

    def js_open_chat(self):
        self.open_chat_requested.emit()

    def js_content_edited(self, draft_id, md):
        """Editor committed (view/workspace leave). Only the ACTIVE workspace
        can edit — the same invariant as the Qt editor. A markdown edit clears
        the cached rich HTML (``current_html`` → None) exactly like
        ``_commit_active_editor``, so a subsequent publish re-derives."""
        if self._publish_inflight:        # frozen during a publish confirm —
            return                        # else a mid-modal edit rewrites the
                                          # content the operator is approving
        did = self._known_id(draft_id)
        if did is None or did != self._active_draft_id:
            return
        card = self._cards.get(did)
        if card is None:
            return
        new_md = str(md if md is not None else "")
        if new_md == (card.get("markdown") or ""):
            return
        card["markdown"] = new_md
        card["content_html"] = None
        self._cards[did] = card
        # The displayed content changed out-of-band (we emit preview_updated,
        # not draft_loaded), so the draft_loaded dedup baseline is now stale —
        # invalidate it, or a later LEGITIMATE host push of the pre-edit value
        # would be wrongly skipped and the canvas would keep the edited text.
        self._last_pushed = None
        # Echo the fresh (sanitized) preview + checks WITHOUT a draft_loaded
        # push — the editor's own state must not be reset mid-session.
        checks = []
        if self._checks_fn is not None:
            try:
                checks = list(self._checks_fn(new_md) or [])
            except Exception:  # noqa: BLE001
                checks = []
        try:
            self.preview_updated.emit(json.dumps({
                "id": did, "preview_html": self._preview_html(card),
                "checks": checks}, default=str))
        except Exception:  # noqa: BLE001
            pass
        self.content_edited.emit(did, new_md)

    def js_ai_edit(self, instruction, selection):
        """Slash-menu / highlight-to-edit ask. Validated + capped; requires an
        active draft (the host's revise targets it). The host's existing
        off-thread revise → reload_active_canvas loop pushes the result back."""
        if self._publish_inflight:        # frozen during a publish confirm —
            return                        # a revise rewrites the DB content the
                                          # operator is approving (parallels the
                                          # js_content_edited freeze)
        if self._active_draft_id is None:
            return
        text = str(instruction or "").strip()[: self._AI_INSTRUCTION_CAP]
        if not text:
            return
        sel = str(selection or "")[: self._AI_SELECTION_CAP]
        self._ai_edit_inflight = True     # blocks publish until notify_ai_edit_done
        self.ai_edit_requested.emit(text, sel)

    def js_request_publish(self, dest_key):
        """Publish clicked in the web UI. NEVER publishes directly — validate
        the destination against the allowlist (guru_existing only for keys WE
        offered), claim a single-winner inflight flag BEFORE the native
        dialog, confirm natively, and only then emit ``publish_requested`` into
        page.py's existing publish path (which has its own review semantics).
        No confirm_fn injected → fail closed."""
        dest = str(dest_key or "")
        known = (dest in self._PUBLISH_STATIC
                 or (dest.startswith("guru_existing:")
                     and dest.split(":", 1)[1] in self._existing_keys))
        if not known or self._active_draft_id is None:
            return
        if self._publish_inflight:
            return
        if self._ai_edit_inflight:
            # A workbench AI-edit revise is rewriting this draft's body from a
            # worker thread — publishing now could ship content the operator
            # never saw. Silent no-op: the AI bar shows "Renn is revising…", so
            # it's clear why; the operator publishes once it settles.
            return
        if self._busy_lookup is not None:
            try:
                busy = bool(self._busy_lookup())
            except Exception:  # noqa: BLE001 — treat an unreadable engine as busy
                busy = True
            if busy:
                # A chat turn is running; a chat-driven revise_draft could be
                # writing the DB. Refuse until it settles (with js_ai_edit and
                # chat sends both frozen during the modal, this guarantees NO
                # revise worker overlaps the publish → the belt's DB read equals
                # the bytes publish_draft ships).
                return
        self._publish_inflight = True
        try:
            request_id = uuid.uuid4().hex[:12]
            # PIN the target at check time (the calendar reschedule pattern):
            # the confirm names THIS draft, so the publish must target THIS
            # draft — never a re-read of active state after the modal.
            target_id = self._active_draft_id
            card = self._cards.get(target_id) or {}
            title = str(card.get("title") or "Untitled")
            # Snapshot what the operator is being shown — the PUBLISH BODY of
            # the cached card, i.e. the very bytes publish_draft will send.
            # (Until 2026-07-26 this snapshotted the markdown column, which is
            # neither what the preview rendered nor what shipped whenever
            # content_html was populated — the belt certified a third string.)
            target_body = self._publish_body(card)
            approved = False
            if self._publish_confirm_fn is not None:
                try:
                    approved = bool(self._publish_confirm_fn(dest, title))
                except Exception:  # noqa: BLE001 — a broken dialog means NO
                    approved = False
            # The core invariant: publish ONLY what the operator saw and
            # approved. At approve time (a) the target must still be active and
            # its publish body unchanged in cache, and (b) — authoritatively —
            # that body must equal the publish body of the DB row publish_draft
            # will read. Both comparisons are over publish_body's output, so
            # the bytes this belt certifies ARE the bytes that ship.
            if approved:
                cache_now = self._publish_body(self._cards.get(target_id) or {})
                ok = (self._active_draft_id == target_id and cache_now == target_body)
                if ok and self._content_lookup is not None:
                    ok = (cache_now == self._lookup_db_content(target_id))
                if not ok:
                    approved = False
            dispatched = False
            if approved:
                try:
                    # Ensure page.py's _on_publish re-reads the SAME draft: the
                    # freeze kept _active_draft_id == target_id, so the shared
                    # active_draft_id read resolves to the confirmed draft.
                    self.publish_requested.emit(dest)
                    dispatched = True
                except Exception:  # noqa: BLE001
                    dispatched = False
            try:
                self.publish_resolved.emit(json.dumps({
                    "request_id": request_id, "dest": dest, "title": title,
                    "approved": approved, "dispatched": dispatched,
                    "cancelled": not approved}))
            except Exception:  # noqa: BLE001
                pass
        finally:
            self._publish_inflight = False

    def js_request_import(self, kind):
        """Import asked from the web menu — the host opens its NATIVE dialogs
        (Guru card picker / Drive URL input), so no extra gate is needed."""
        if kind in ("drive", "guru"):
            self.import_requested.emit(kind)

    def js_existing_cards(self):
        """'Existing Guru card' submenu opened. React nulls its list to
        'Loading…' on EVERY open, so we must always re-serve: replay the cached
        list immediately (instant, and shows 'No cards found' rather than a
        forever-spinner once a host result — even empty — has arrived), THEN ask
        the host to refresh (the host may one-shot-guard the network fetch)."""
        if getattr(self, "_existing_cards_fetched", False):
            try:
                self.existing_cards_data.emit(json.dumps(
                    {"items": list(getattr(self, "_existing_cards", []))},
                    default=str))
            except Exception:  # noqa: BLE001
                pass
        self.existing_cards_requested.emit()

    # ── internals ────────────────────────────────────────────────────
    def _lookup_db_content(self, draft_id):
        """The stored draft's PUBLISH BODY (or None if unavailable) — the exact
        bytes store.publish_draft will send. Fail-safe: any error returns a
        sentinel that can never equal a real snapshot, so the belt refuses
        rather than publishing blind."""
        if self._content_lookup is None:
            return None
        try:
            return self._content_lookup(draft_id)
        except Exception:  # noqa: BLE001
            return object()   # unequal to any snapshot → belt refuses

    def _known_id(self, draft_id):
        """Resolve an untrusted id against the OPEN chips only."""
        try:
            did = int(str(draft_id))
        except (TypeError, ValueError):
            return None
        return did if did in [d.get("id") for d in self._current_drafts] else None

    def _push_workspaces(self):
        chips = [{"id": d.get("id"), "title": str(d.get("title") or "Untitled"),
                  "source": str(d.get("source") or "drive")}
                 for d in self._current_drafts]
        try:
            self.workbench_data.emit(json.dumps(
                {"chips": chips, "active_id": self._active_draft_id}, default=str))
        except Exception:  # noqa: BLE001
            pass

    def _publish_body(self, card: dict) -> str:
        """The bytes a publish of THIS card would send — the one composition,
        shared with ``enablement_store.publish_draft``. The preview renders it
        and the publish belt certifies it, so screen and network can't drift."""
        from src.data.enablement_store import publish_body
        return publish_body({"content": card.get("markdown") or "",
                             "content_html": card.get("content_html")},
                            md_to_html=self._render_markdown)

    def _preview_html(self, card: dict) -> str:
        """The ONLY producer of preview HTML — sanitize EVERY path.

        The SOURCE is the publish body (``_publish_body``); the sanitizer is
        the rendering mechanism for the sandboxed iframe, not a second
        composition. Anything the sanitizer removes is removed from a faithful
        copy of the shipped bytes, never from a different representation."""
        from src.data.html_sanitize import sanitize_html
        return sanitize_html(self._publish_body(card))

    def _render_markdown(self, md: str) -> str:
        if self._md_to_html_fn is not None:
            try:
                return self._md_to_html_fn(md)
            except Exception:  # noqa: BLE001
                pass
        try:
            from src.data.html_markdown import markdown_to_html
            return markdown_to_html(md)
        except Exception:  # noqa: BLE001 — degrade to escaped preformatted text
            from html import escape
            return f"<pre>{escape(md)}</pre>"

    def _push_draft(self, card: dict):
        checks = []
        if self._checks_fn is not None:
            try:
                checks = list(self._checks_fn(card.get("markdown") or "") or [])
            except Exception:  # noqa: BLE001 — checks are advisory
                checks = []
        md = card.get("markdown") or ""
        # Content-identity guard: same active draft + byte-identical markdown as
        # the last push → an incidental host refresh, not a real change. Skip so
        # the web editor's live (uncommitted) state survives. A genuine change
        # (switch to another draft, a revise, or an edit committed to the cache)
        # differs and pushes normally; request_refresh invalidates this so a
        # reconnecting page always gets a fresh push.
        key = (self._active_draft_id, md)
        if key == self._last_pushed:
            return
        self._last_pushed = key
        payload = {
            "id": self._active_draft_id,
            "breadcrumb": str(card.get("breadcrumb") or ""),
            "title": str(card.get("title") or "Untitled"),
            "source": str(card.get("source") or ""),
            "markdown": md,
            "preview_html": self._preview_html(card),
            "baseline_present": bool(self._linked_card_md),
            "checks": checks,
        }
        try:
            self.draft_loaded.emit(json.dumps(payload, default=str))
        except Exception:  # noqa: BLE001
            pass
