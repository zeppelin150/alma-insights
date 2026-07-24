"""Controller behind the web Zendesk tab (``enablement.web_tabs``).

``ZendeskWebController`` is the Python owner of the web Garden-clone Zendesk
surface: it reads the local mirror (mig 030 + 051 tables) through the injected
``conn_fn``, shapes JSON viewmodels, and pushes them to the React route via
``ZendeskBridge``. The web layer is a pure renderer — every fact it shows
comes from here, and every click routes back here for resolution against
Python-held state.

Security posture (the QWebChannel trust boundary):

* Every ``js_*`` slot receives UNTRUSTED input — any page script can invoke
  it. Invalid input is a SILENT no-op: ids resolve only against the mirror /
  the controller's last-served rows, kinds and scopes against hard
  allowlists, and save payload keys against a hard RENAME-ONLY allowlist
  (a page script can never ride a status transition — or draft CONTENT —
  through a save; reviewed bytes and copied bytes must never diverge).
* Destructive actions (delete revision, purge mirror) run the workbench
  gate verbatim: validate → single-winner ``_action_inflight`` claim BEFORE
  the native confirm modal → re-verify after approve → dispatch → resolve.
  No ``confirm_fn`` injected → the gate FAILS CLOSED.
* While EITHER inflight claim is held every mutating slot silently refuses
  (confused-deputy freeze); the claim is taken BEFORE any native nested
  event loop (the QMessageBox AND the QFileDialog pickers).
* Every article HTML string leaving this controller passes
  ``sanitize_html`` (``_article_srcdoc`` is the only srcdoc producer).
* **This controller can never write to real Zendesk**: it holds no client,
  the compat ``article_push`` / ``macro_push`` / ``sync_requested`` signals
  exist for page.py wiring parity only and are NEVER emitted, and the only
  conduit toward Zendesk is the injected Python-side clipboard.
"""

from __future__ import annotations

import json
import re
import time

from PySide6.QtCore import QObject, Signal

_VIEWS = ("articles", "macros", "revisions")
_REVISION_FILTERS = ("open", "copied", "all")
_SEARCH_KINDS = ("all", "articles", "macros")
_DRAFT_KINDS = ("article", "macro")
_PURGE_SCOPES = ("all", "articles", "macros", "imported")
# Per-target copy-field allowlists: the TARGET kind (never a polymorphic id)
# decides the table, so a draft id can never silently address an article row.
_COPY_FIELDS = {
    "article": ("title", "body_html", "body_rich"),
    "article_draft": ("title", "body_html", "body_rich"),
    "macro": ("macro_name", "macro_reply"),
    "macro_draft": ("macro_name", "macro_reply"),
}
_COMMENT_FIELDS = ("comment_value", "comment_value_html")
# Zendesk Admin action wording (ui dossier §macro actions): id-suffixed
# fields display their object name; multi-word fields are sentence case
# ("Add tags", never "Add Tags"). Unknown fields fall back to sentence case.
_FIELD_DISPLAY = {"comment_value": "Comment/Reply",
                  "comment_value_html": "Comment/Reply (HTML)",
                  "subject": "Subject",
                  "status": "Status",
                  "priority": "Priority",
                  "type": "Type",
                  "group_id": "Group",
                  "assignee_id": "Assignee",
                  "ticket_form_id": "Form",
                  "brand_id": "Brand",
                  "set_tags": "Set tags",
                  "add_tags": "Add tags",
                  "remove_tags": "Remove tags",
                  "comment_mode": "Comment mode"}

_ID_RE = re.compile(r"^-?\d+$")     # mirror ids (imports use negative ids)
_QUERY_CAP = 200
_TITLE_CAP = 255
_PULL_COOLDOWN_S = 60               # untrusted slot must not drive traffic
_PULL_FAIL_COOLDOWN_S = 5           # a failed pull must not lock out retry
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _probe_connected() -> bool:
    """Whether a read-only Zendesk client could be built from settings.
    Module-level so tests can monkeypatch away the keyring probe."""
    try:
        from src.data.zendesk_client import ZendeskClient
        return ZendeskClient.from_settings() is not None
    except Exception:  # noqa: BLE001 — display-only fact, never fatal
        return False


def _last_pull_iso():
    """``enablement.zendesk.last_pull`` (written by page.py's main-thread
    ``zendesk_mirror_done`` slot). Module-level for test monkeypatching."""
    try:
        from src.data.settings_manager import get_section
        return ((get_section("enablement", {}) or {}).get("zendesk", {})
                or {}).get("last_pull")
    except Exception:  # noqa: BLE001
        return None


def _display_date(value) -> str:
    """'2026-07-01…' → 'Jul 1, 2026' (locked presentation format — the only
    duplicated display constant; see the parity note in the test module)."""
    s = str(value or "")[:10]
    try:
        y, m, d = int(s[0:4]), int(s[5:7]), int(s[8:10])
        return f"{_MONTHS[m - 1]} {d}, {y}"
    except (ValueError, IndexError):
        return ""


def _actions_plain(actions) -> str:
    """Plain-text projection of a macro actions list — mirrors the store's
    actions_text projection so diffs compare like against like."""
    parts = []
    for a in actions or []:
        if isinstance(a, dict):
            piece = f"{a.get('field', '')} {a.get('value', '')}".strip()
            if piece:
                parts.append(piece)
    return "\n".join(parts)


def _first_reply(actions):
    """Text of the first comment action (``comment_value`` OR
    ``comment_value_html`` — real macros routinely use the html form)."""
    for a in actions or []:
        if isinstance(a, dict) and a.get("field") in _COMMENT_FIELDS:
            return str(a.get("value", ""))
    return None


class ZendeskWebController(QObject):
    """State + viewmodels for the web Zendesk tab.

    Mirrors the native ``ZendeskPage`` surface (7 signals + 5 setters) so
    page.py's existing wiring and ``_load_zendesk`` work unchanged against
    either implementation — but ``article_push`` / ``macro_push`` /
    ``sync_requested`` are NEVER emitted here, which is the structural
    guarantee that no live Zendesk write can originate from the web surface.
    """

    # ── web-facing pushes (JSON strings unless noted) ────────────────
    zendesk_data = Signal(str)      # main viewmodel {view, counts, articles, …}
    article_detail = Signal(str)    # one article + sanitized body_srcdoc
    macro_detail = Signal(str)      # one macro + decoded actions
    revisions_data = Signal(str)    # {filter, revisions: […]}
    diff_ready = Signal(str)        # workbench DiffBody row shape
    import_resolved = Signal(str)   # zendesk_import report dict verbatim
    pull_resolved = Signal(str)     # pull report dict verbatim
    copy_resolved = Signal(str)     # {request_id, target, field, ok, chars, sanitized}
    action_resolved = Signal(str)   # {request_id, action, ok, approved, error, …}
    status_text = Signal(str)       # PLAIN text (compat set_status echo)

    # ── ZendeskPage-compatible surface (wiring parity; page.py:381-388).
    # article_push / macro_push / sync_requested are NEVER emitted.
    sync_requested = Signal()
    article_selected = Signal(int)
    article_saved = Signal(int, str, str)
    article_push = Signal(int)
    macro_selected = Signal(int)
    macro_saved = Signal(int, str, str)
    macro_push = Signal(int)

    def __init__(self, conn_fn=None, confirm_fn=None, clipboard_fn=None,
                 file_pick_fn=None, folder_pick_fn=None,
                 pull_runner=None, import_runner=None,
                 now_fn=None, demo=False, parent=None):
        super().__init__(parent)
        # ``conn_fn() -> sqlite3.Connection`` — page.py passes self._conn
        # (demo/live aware). Only fast indexed reads run here; heavy work
        # (pull/import) is delegated to the injected runners, which page.py
        # implements as daemon-thread workers.
        self._conn_fn = conn_fn
        # ``confirm_fn(title, text) -> bool`` — NATIVE QMessageBox, injected
        # by page.py, physically unreachable from Chromium. Absent → every
        # destructive gate fails closed.
        self._confirm_fn = confirm_fn
        # ``clipboard_fn(text, html|None) -> bool`` — Python-side QClipboard.
        self._clipboard_fn = clipboard_fn
        # Native QFileDialog pickers (the page never sees filesystem paths).
        self._file_pick_fn = file_pick_fn
        self._folder_pick_fn = folder_pick_fn
        # ``pull_runner() -> bool`` / ``import_runner(paths) -> bool`` start
        # off-thread work; results return via notify_pull_done/import_done.
        self._pull_runner = pull_runner
        self._import_runner = import_runner
        self._now_fn = now_fn           # test-pinnable clock (epoch seconds)
        self._demo = bool(demo)
        self._view = "articles"
        self._status = ""
        self._action_inflight = False   # destructive-gate claim
        self._pull_inflight = False     # pull/import serialization claim
        self._last_pull_done = None     # epoch seconds; cooldown floor
        self._pull_cooldown_s = _PULL_COOLDOWN_S   # per-outcome window
        self._connected_cache = None
        self._req_seq = 0
        # Last-served row registries — the ONLY ids untrusted slots may
        # reference (accumulated across pushes; the DB re-check at dispatch
        # time stays authoritative for status/state).
        self._served_article_ids: set[int] = set()
        self._served_macro_ids: set[int] = set()
        self._served_drafts: dict[tuple[str, int], str] = {}
        self._last_filter = "open"

    # ── the ZendeskPage-compatible setters page.py drives ────────────
    def set_articles(self, synced_count, drafts):
        """Host signalled article data changed (post-sync/import reload) —
        the args carry the NATIVE tab's shapes; the web viewmodel re-reads
        the mirror directly instead."""
        self._serve_data()

    def show_article_draft(self, draft):
        """Parity no-op — the web surface loads drafts from the store itself
        (the article_selected loop that feeds this is never emitted here)."""

    def set_macros(self, synced_count, drafts):
        self._serve_data()

    def show_macro_draft(self, draft):
        """Parity no-op (see show_article_draft)."""

    def set_status(self, text):
        """Native status line text. ALSO invoked internally for the copy
        announcements — a trusted surface the page cannot forge."""
        self._status = str(text or "")
        try:
            self.status_text.emit(self._status)
        except Exception:  # noqa: BLE001
            pass

    # ── host surface (page.py / workers) ─────────────────────────────
    def notify_pull_done(self, report):
        """Off-thread pull finished — release the claim, start the cooldown
        floor, hand the report to the page verbatim, re-serve the mirror.
        A FAILED pull arms only the short window (a user retry after a
        transient error must not be silently refused for a minute; sequential
        traffic stays bounded by the inflight claim + the short floor)."""
        self._pull_inflight = False
        ok = isinstance(report, dict) and bool(report.get("ok"))
        self._last_pull_done = self._now()
        self._pull_cooldown_s = (_PULL_COOLDOWN_S if ok
                                 else _PULL_FAIL_COOLDOWN_S)
        # The connected/mirror-only probe is stale by definition after a
        # pull resolves (credentials may have just been added/removed).
        self._connected_cache = None
        self._emit(self.pull_resolved,
                   report if isinstance(report, dict) else {})
        self._serve_data()

    def notify_import_done(self, report):
        self._pull_inflight = False
        self._emit(self.import_resolved,
                   report if isinstance(report, dict) else {})
        self._serve_data()

    def request_refresh(self):
        """Host asked for a re-serve (fresh build — no dedup cache exists on
        this feed, so a rebuild is also the invalidation; the connected
        probe re-runs too so a Settings change lands without a restart)."""
        self._connected_cache = None
        self._serve_data()

    # ── bridge-facing entry points (UNTRUSTED input) ─────────────────
    def js_refresh(self):
        self._connected_cache = None    # probe again — creds may have changed
        self._serve_data()

    def js_set_view(self, view):
        view = str(view or "")
        if view not in _VIEWS:
            return
        self._view = view
        self._serve_data()

    def js_search(self, query, kind):
        """FTS search over the mirror; results ship as a filtered
        zendesk_data payload (the lists keep their full row shape)."""
        q = str(query or "")[:_QUERY_CAP]
        kind = str(kind or "")
        if kind not in _SEARCH_KINDS:
            return
        if not q.strip():
            self._serve_data()
            return
        self._serve_data(query=q, search_kind=kind)

    def js_open_article(self, article_id):
        """Open an article detail. Digits/'-'-digits only, and the row must
        exist in the mirror — anything else is a silent no-op."""
        s = str(article_id or "")
        if not _ID_RE.match(s):
            return
        conn = self._db()
        if conn is None:
            return
        from src.data import zendesk_store as store
        art = store.get_article(conn, int(s))
        if art is None:
            return
        aid = art["article_id"]
        section, category = self._section_names(conn, art.get("section_id"))
        revisions = []
        for r in store.list_revisions(conn, kind="article"):
            if r.get("target_id") == aid:
                revisions.append({
                    "draft_id": r["draft_id"], "status": r["status"],
                    "title": r.get("title") or "",
                    "updated_display": _display_date(r.get("updated_at"))})
                self._served_drafts[("article", r["draft_id"])] = r["status"]
        self._served_article_ids.add(aid)
        self._emit(self.article_detail, {
            "id": aid, "title": art.get("title") or "",
            "section_id": art.get("section_id"), "section": section,
            "category": category, "labels": art.get("labels") or [],
            "author": art.get("author_name") or "—",
            "draft": bool(art.get("draft")),
            "outdated": bool(art.get("outdated")),
            "position": art.get("position"),
            "html_url": art.get("html_url") or "",
            "origin": art.get("origin") or "pull",
            "source_file": art.get("source_file"),
            "updated_display": _display_date(art.get("updated_at")),
            "body_srcdoc": self._article_srcdoc(art),
            "revisions": revisions,
        })

    def js_open_macro(self, macro_id):
        s = str(macro_id or "")
        if not _ID_RE.match(s):
            return
        conn = self._db()
        if conn is None:
            return
        from src.data import zendesk_store as store
        macro = store.get_macro(conn, int(s))
        if macro is None:
            return
        mid = macro["macro_id"]
        actions = []
        for a in macro.get("actions") or []:
            if isinstance(a, dict):
                field = str(a.get("field", ""))
                actions.append({
                    "field": field,
                    "display": _FIELD_DISPLAY.get(
                        field, field.replace("_", " ").capitalize()),
                    "value": str(a.get("value", ""))})
        revisions = []
        for r in store.list_revisions(conn, kind="macro"):
            if r.get("target_id") == mid:
                revisions.append({
                    "draft_id": r["draft_id"], "status": r["status"],
                    "name": r.get("title") or ""})
                self._served_drafts[("macro", r["draft_id"])] = r["status"]
        self._served_macro_ids.add(mid)
        self._emit(self.macro_detail, {
            "id": mid, "name": macro.get("name") or "",
            "description": macro.get("description") or "",
            "active": bool(macro.get("active")),
            "updated_display": _display_date(macro.get("updated_at")),
            "actions": actions, "revisions": revisions,
        })

    def js_request_revisions(self, filter):  # noqa: A002 — bridge slot name
        f = str(filter or "")
        if f not in _REVISION_FILTERS:
            return
        self._last_filter = f
        self._push_revisions(f)

    def js_request_diff(self, kind, draft_id):
        """Word-level diff of a draft against its mirror baseline, computed
        in Python over the plain-text projections (pure renderer rule)."""
        kind = str(kind or "")
        if kind not in _DRAFT_KINDS:
            return
        did = self._served_draft_id(kind, draft_id)
        if did is None:
            return
        conn = self._db()
        if conn is None:
            return
        from src.data import zendesk_store as store
        from src.data.text_diff import change_count, diff_words
        if kind == "article":
            d = store.get_article_draft(conn, did)
            if d is None:
                return
            target = (store.get_article(conn, d["article_id"])
                      if d.get("article_id") else None)
            baseline = ""
            if target is not None:
                baseline = target.get("body_text")
                if baseline is None:
                    baseline = store.html_to_text(
                        target.get("body_html") or target.get("body") or "")
            # Belt: diff EXACTLY what the clipboard will deliver. When the
            # draft carries body_html, _resolve_copy prefers it over the
            # markdown body — so the reviewed text must be its plain-text
            # projection, never a field the copy path ignores (reviewed ==
            # copied, always).
            if d.get("body_html"):
                new_text = store.html_to_text(d["body_html"])
            else:
                new_text = d.get("body") or ""
            old_title = (target or {}).get("title") or ""
            new_title = d.get("title") or ""
        else:
            d = store.get_macro_draft(conn, did)
            if d is None:
                return
            target = (store.get_macro(conn, d["macro_id"])
                      if d.get("macro_id") else None)
            baseline = ""
            if target is not None:
                baseline = target.get("actions_text")
                if baseline is None:
                    baseline = _actions_plain(target.get("actions"))
            new_text = _actions_plain(d.get("actions"))
            old_title = (target or {}).get("name") or ""
            new_title = d.get("name") or ""
        rows = diff_words(baseline, new_text)
        self._req_seq += 1
        self._emit(self.diff_ready, {
            "request_id": f"d-{self._req_seq}", "kind": kind, "draft_id": did,
            "baseline_present": target is not None,
            "change_count": change_count(rows),
            "title": {"changed": old_title != new_title,
                      "old": old_title, "new": new_title},
            "rows": rows,
        })

    def js_save_draft(self, kind, draft_id, payload_json):
        """RENAME a pending/ready draft. Payload keys are a HARD allowlist —
        article: {title}; macro: {name, description} — every other key is
        silently dropped. The SPA only renames; draft CONTENT (body /
        body_html / actions) is deliberately NOT page-writable: the review
        diff is computed from it and the clipboard delivers it, so a
        page-written body would ride attacker content past the review gate
        (reviewed bytes and copied bytes must never diverge). Status keys
        stay dropped too, so a page script can never ride a status
        transition past the js_mark_* gates."""
        if self._frozen():
            return
        kind = str(kind or "")
        if kind not in _DRAFT_KINDS:
            return
        did = self._served_draft_id(kind, draft_id)
        if did is None:
            return
        try:
            payload = json.loads(str(payload_json or ""))
        except (ValueError, TypeError):
            return
        if not isinstance(payload, dict):
            return
        conn = self._db()
        if conn is None:
            return
        from src.data import zendesk_store as store
        try:
            if kind == "article":
                d = store.get_article_draft(conn, did)
                # PRECONDITION: the draft's CURRENT DB status is editable —
                # copied/pushed rows are immutable from every surface.
                if d is None or d.get("status") not in ("pending", "ready"):
                    return
                if not isinstance(payload.get("title"), str):
                    return
                store.update_article_draft(
                    conn, did, title=payload["title"][:_TITLE_CAP])
            else:
                d = store.get_macro_draft(conn, did)
                if d is None or d.get("status") not in ("pending", "ready"):
                    return
                name = payload.get("name")
                desc = payload.get("description")
                if not isinstance(name, str) and not isinstance(desc, str):
                    return
                if isinstance(name, str):
                    store.update_macro_draft(conn, did,
                                             name=name[:_TITLE_CAP])
                if isinstance(desc, str):
                    # update_macro_draft has no description kwarg; same
                    # table, same _txn discipline (pass-through when the
                    # caller already holds a transaction).
                    from datetime import datetime, timezone
                    with store._txn(conn):  # noqa: SLF001
                        conn.execute(
                            "UPDATE zendesk_macro_drafts SET description=?, "
                            "updated_at=? WHERE id=?",
                            (desc[:_TITLE_CAP],
                             datetime.now(timezone.utc).isoformat(), did))
        except Exception:  # noqa: BLE001 — a failed save must not crash the tab
            return
        self._push_revisions(self._last_filter)

    def js_mark_ready(self, kind, draft_id):
        """pending → ready only, validated against the CURRENT DB status."""
        self._transition(kind, draft_id, "ready", allowed_from=("pending",))

    def js_mark_copied(self, kind, draft_id):
        """pending|ready → copied (sets copied_at in the store)."""
        self._transition(kind, draft_id, "copied",
                         allowed_from=("pending", "ready"))

    def js_copy_field(self, target, target_id, field):
        """Copy exact: re-reads the EXACT DB bytes at click time (never the
        page's copy of the text) and hands them to the Python-side clipboard.
        Read-only — no confirm, no status change (locked decision). Every
        successful copy is ALSO announced on the native status line via the
        compat set_status, a surface the page cannot forge."""
        target = str(target or "")
        fields = _COPY_FIELDS.get(target)
        if fields is None or str(field or "") not in fields:
            return
        field = str(field)
        tid = self._copy_target_id(target, target_id)
        if tid is None:
            return
        conn = self._db()
        if conn is None:
            return
        resolved = self._resolve_copy(conn, target, tid, field)
        if resolved is None:
            return
        text, html, sanitized = resolved
        ok = False
        if self._clipboard_fn is not None:
            try:
                ok = bool(self._clipboard_fn(text, html))
            except Exception:  # noqa: BLE001
                ok = False
        self._req_seq += 1
        self._emit(self.copy_resolved, {
            "request_id": f"c-{self._req_seq}", "target": target,
            "target_id": tid, "field": field, "ok": ok,
            "chars": len(text), "sanitized": sanitized})
        if ok:
            note = (" (rich copy sanitized: script/iframe content removed)"
                    if sanitized else "")
            self.set_status(
                f"Copied {target} {tid} {field} - {len(text):,} chars{note}")

    def js_request_import(self):
        """Manual file import. The ``_pull_inflight`` claim is taken BEFORE
        ``file_pick_fn`` runs — the native picker spins a nested event loop,
        so a re-entrant bridge invoke must already find the claim held."""
        self._start_import(self._pick_files)

    def js_request_import_folder(self):
        self._start_import(self._pick_folder)

    def js_request_pull(self):
        """Read-only API pull (GET only, non-destructive → no confirm,
        locked decision). Python-side cooldown: silently refused within 60s
        of the last pull completion so an untrusted slot can never drive
        unbounded authenticated traffic."""
        if self._frozen():
            return
        if (self._last_pull_done is not None
                and self._now() - self._last_pull_done
                < self._pull_cooldown_s):
            return
        self._pull_inflight = True
        started = False
        if self._pull_runner is not None:
            try:
                started = bool(self._pull_runner())
            except Exception:  # noqa: BLE001
                started = False
        if not started:
            # Never wedge pullBusy: release the claim and tell the page.
            self._pull_inflight = False
            self._emit(self.pull_resolved,
                       {"ok": False, "error": "not_started"})

    def js_delete_revision(self, kind, draft_id):
        """DESTRUCTIVE gate — the workbench pattern verbatim: validate
        against Python-held state → claim BEFORE the modal → native confirm
        (fail closed) → re-verify existence + status → dispatch → resolve."""
        kind = str(kind or "")
        if kind not in _DRAFT_KINDS:
            return
        did = self._served_draft_id(kind, draft_id)
        if did is None:
            return
        if self._frozen():
            return
        conn = self._db()
        if conn is None:
            return
        from src.data import zendesk_store as store
        getter = (store.get_article_draft if kind == "article"
                  else store.get_macro_draft)
        try:
            row = getter(conn, did)
        except Exception:  # noqa: BLE001
            row = None
        if row is None:
            return
        status_before = row.get("status")
        ok = False
        self._action_inflight = True
        try:
            self._req_seq += 1
            request_id = f"a-{self._req_seq}"
            approved = self._confirm(
                "Delete revision",
                "Delete this revision? This cannot be undone.")
            error = None
            if approved:
                # Re-verify after the modal: the row must still exist with
                # the status the operator was shown.
                try:
                    now_row = getter(conn, did)
                except Exception:  # noqa: BLE001
                    now_row = None
                if now_row is None:
                    error = "draft_not_found"
                elif now_row.get("status") != status_before:
                    error = "status_changed"
                else:
                    try:
                        res = store.delete_draft(conn, kind, did)
                        ok = bool(res.get("ok"))
                        error = res.get("error")
                    except Exception:  # noqa: BLE001 — resolves un-dispatched
                        ok, error = False, "store_error"
            self._emit(self.action_resolved, {
                "request_id": request_id, "action": "delete_revision",
                "kind": kind, "draft_id": did, "ok": ok,
                "approved": approved, "error": error})
        finally:
            self._action_inflight = False
        if ok:
            self._served_drafts.pop((kind, did), None)
            self._push_revisions(self._last_filter)

    def js_purge_mirror(self, scope):
        """DESTRUCTIVE gate over the whole mirror. Scope-specific confirm
        text with live counts; counts are RECOMPUTED at dispatch (the store
        reports actual deleted rowcounts) and those recomputed counts are
        what action_resolved carries. copied/pushed draft rows are excluded
        from purge entirely (store rule — the audit trail survives)."""
        scope = str(scope or "")
        if scope not in _PURGE_SCOPES:
            return
        if self._frozen():
            return
        conn = self._db()
        if conn is None:
            return
        from src.data import zendesk_store as store
        ok = False
        self._action_inflight = True
        try:
            self._req_seq += 1
            request_id = f"a-{self._req_seq}"
            approved = self._confirm("Purge mirror",
                                     self._purge_text(conn, scope))
            error = None
            counts = {}
            if approved:
                try:
                    res = store.purge_mirror(conn, scope=scope)
                    ok = bool(res.get("ok"))
                    if ok:
                        counts = {k: v for k, v in res.items() if k != "ok"}
                    else:
                        error = res.get("error")
                except Exception:  # noqa: BLE001
                    ok, error = False, "store_error"
            self._emit(self.action_resolved, {
                "request_id": request_id, "action": "purge_mirror",
                "scope": scope, "ok": ok, "approved": approved,
                "error": error, "counts": counts})
        finally:
            self._action_inflight = False
        if ok:
            self._served_article_ids.clear()
            self._served_macro_ids.clear()
            self._serve_data()
            self._push_revisions(self._last_filter)

    # ── internals ────────────────────────────────────────────────────
    def _db(self):
        if self._conn_fn is None:
            return None
        try:
            return self._conn_fn()
        except Exception:  # noqa: BLE001
            return None

    def _now(self) -> float:
        if self._now_fn is not None:
            try:
                return float(self._now_fn())
            except Exception:  # noqa: BLE001
                pass
        return time.time()

    def _frozen(self) -> bool:
        """Confused-deputy freeze: every mutating slot refuses while a
        destructive modal OR a pull/import is inflight."""
        return self._action_inflight or self._pull_inflight

    def _confirm(self, title, text) -> bool:
        """Native confirm; absent fn or a broken dialog means NO."""
        if self._confirm_fn is None:
            return False
        try:
            return bool(self._confirm_fn(title, text))
        except Exception:  # noqa: BLE001
            return False

    def _emit(self, signal, payload: dict):
        try:
            signal.emit(json.dumps(payload, default=str))
        except Exception:  # noqa: BLE001 — never crash the feed path
            pass

    def _connected(self) -> bool:
        if self._demo:
            return False
        if self._connected_cache is None:
            self._connected_cache = _probe_connected()
        return self._connected_cache

    def _served_draft_id(self, kind, draft_id):
        """Resolve an untrusted draft id against the last-served revisions
        (forged draft ids never reach the store)."""
        s = str(draft_id or "")
        if not s.isdigit():
            return None
        did = int(s)
        return did if (kind, did) in self._served_drafts else None

    def _copy_target_id(self, target, target_id):
        """Resolve a copy target id against THAT kind's last-served rows."""
        s = str(target_id or "")
        if not _ID_RE.match(s):
            return None
        tid = int(s)
        if target == "article":
            return tid if tid in self._served_article_ids else None
        if target == "macro":
            return tid if tid in self._served_macro_ids else None
        kind = "article" if target == "article_draft" else "macro"
        return tid if (kind, tid) in self._served_drafts else None

    def _transition(self, kind, draft_id, status, *, allowed_from):
        if self._frozen():
            return
        kind = str(kind or "")
        if kind not in _DRAFT_KINDS:
            return
        did = self._served_draft_id(kind, draft_id)
        if did is None:
            return
        conn = self._db()
        if conn is None:
            return
        from src.data import zendesk_store as store
        getter = (store.get_article_draft if kind == "article"
                  else store.get_macro_draft)
        try:
            d = getter(conn, did)
        except Exception:  # noqa: BLE001
            return
        # Validate against the CURRENT DB status; ``expected`` makes a
        # concurrent change between read and write a silent store refusal.
        if d is None or d.get("status") not in allowed_from:
            return
        try:
            res = store.set_draft_status(conn, kind, did, status,
                                         expected=d.get("status"))
        except Exception:  # noqa: BLE001
            return
        if res.get("ok"):
            self._served_drafts[(kind, did)] = status
            self._push_revisions(self._last_filter)

    # ── viewmodel builders ───────────────────────────────────────────
    def _serve_data(self, query=None, search_kind="all"):
        payload = self._build_data(query=query, search_kind=search_kind)
        for a in payload["articles"]:
            self._served_article_ids.add(a["id"])
        for m in payload["macros"]:
            self._served_macro_ids.add(m["id"])
        self._emit(self.zendesk_data, payload)

    def _build_data(self, query=None, search_kind="all") -> dict:
        payload = {
            "view": self._view, "connected": self._connected(),
            "demo": self._demo,
            "counts": {"articles": 0, "macros": 0, "revisions_open": 0},
            "categories": [], "articles": [], "macros": [],
            "last_pull_display": _display_date(_last_pull_iso()) or "Never",
            "status": self._status,
        }
        if query is not None:
            payload["query"] = query
        conn = self._db()
        if conn is None:
            return payload
        from src.data import zendesk_store as store
        try:
            payload["counts"] = self._counts(conn)
            payload["categories"] = self._category_tree(conn, store)
            open_arts = self._open_rev_map(
                conn, "zendesk_article_drafts", "article_id")
            open_macros = self._open_rev_map(
                conn, "zendesk_macro_drafts", "macro_id")
            sections = {s["section_id"]: s for s in store.list_sections(conn)}
            # Under a search the served lists are AUTHORITATIVE AND COMPLETE
            # for the query (contract with the SPA): rows come straight from
            # the FTS hit ids, fetched by id in rank order — NEVER
            # intersected with the 500-row browse slice, whose recency cap
            # would silently drop older matches.
            hits = (store.search_mirror(conn, query, kind=search_kind,
                                        limit=100)
                    if query is not None else None)
            if hits is not None and search_kind in ("all", "articles"):
                art_rows = [r for r in
                            (store.get_article(conn, h["id"])
                             for h in hits.get("articles", []))
                            if r is not None]
            else:
                art_rows = store.list_articles_full(conn)
            articles = [self._article_vm(row, sections, open_arts)
                        for row in art_rows]
            if hits is not None and search_kind in ("all", "macros"):
                mac_rows = [r for r in
                            (store.get_macro(conn, h["id"])
                             for h in hits.get("macros", []))
                            if r is not None]
            else:
                mac_rows = [dict(r) for r in conn.execute(
                    "SELECT macro_id, name, description, active, updated_at "
                    "FROM zendesk_macros "
                    "ORDER BY updated_at DESC, macro_id LIMIT 500").fetchall()]
            macros = [self._macro_vm(row, open_macros) for row in mac_rows]
            payload["articles"] = articles
            payload["macros"] = macros
        except Exception:  # noqa: BLE001 — a broken mirror degrades to empty
            pass
        return payload

    def _article_vm(self, row, sections, open_map) -> dict:
        """One article list row (browse slice AND search hits share this
        shape — the SPA must never see two dialects)."""
        sect = sections.get(row.get("section_id")) or {}
        labels = row.get("labels")
        if not isinstance(labels, list):
            try:
                labels = json.loads(row.get("labels_json") or "[]")
            except (ValueError, TypeError):
                labels = []
        return {
            "id": row["article_id"],
            "title": row.get("title") or "",
            "section_id": row.get("section_id"),
            "section": sect.get("name") or "",
            "author": row.get("author_name") or "—",
            "updated_display": _display_date(row.get("updated_at")),
            "draft": bool(row.get("draft")),
            "outdated": bool(row.get("outdated")),
            "labels": labels,
            "origin": row.get("origin") or "pull",
            "open_revisions": open_map.get(row["article_id"], 0)}

    def _macro_vm(self, row, open_map) -> dict:
        return {
            "id": row["macro_id"], "name": row.get("name") or "",
            "description": row.get("description") or "",
            "active": bool(row.get("active")),
            "updated_display": _display_date(row.get("updated_at")),
            "open_revisions": open_map.get(row["macro_id"], 0)}

    def _counts(self, conn) -> dict:
        arts = conn.execute(
            "SELECT COUNT(*) FROM zendesk_articles").fetchone()[0]
        macs = conn.execute(
            "SELECT COUNT(*) FROM zendesk_macros").fetchone()[0]
        open_rev = conn.execute(
            "SELECT (SELECT COUNT(*) FROM zendesk_article_drafts "
            "WHERE status IN ('pending','ready')) + "
            "(SELECT COUNT(*) FROM zendesk_macro_drafts "
            "WHERE status IN ('pending','ready'))").fetchone()[0]
        return {"articles": arts, "macros": macs, "revisions_open": open_rev}

    def _open_rev_map(self, conn, table, fk) -> dict:
        rows = conn.execute(
            f"SELECT {fk}, COUNT(*) FROM {table} "                # noqa: S608
            f"WHERE status IN ('pending','ready') AND {fk} IS NOT NULL "
            f"GROUP BY {fk}").fetchall()
        return {r[0]: r[1] for r in rows}

    def _category_tree(self, conn, store) -> list[dict]:
        counts = {r[0]: r[1] for r in conn.execute(
            "SELECT section_id, COUNT(*) FROM zendesk_articles "
            "GROUP BY section_id").fetchall()}
        by_cat: dict = {}
        orphans = []
        cats = store.list_categories(conn)
        known = {c["category_id"] for c in cats}
        for s in store.list_sections(conn):
            entry = {"id": s["section_id"], "name": s.get("name") or "",
                     "article_count": counts.get(s["section_id"], 0)}
            if s.get("category_id") in known:
                by_cat.setdefault(s["category_id"], []).append(entry)
            else:
                orphans.append(entry)
        tree = [{"id": c["category_id"], "name": c.get("name") or "",
                 "sections": by_cat.get(c["category_id"], [])} for c in cats]
        if orphans:
            tree.append({"id": None, "name": "Other", "sections": orphans})
        return tree

    def _section_names(self, conn, section_id):
        if section_id is None:
            return "", ""
        row = conn.execute(
            "SELECT s.name, c.name FROM zendesk_sections s "
            "LEFT JOIN zendesk_categories c ON c.category_id = s.category_id "
            "WHERE s.section_id=?", (section_id,)).fetchone()
        if row is None:
            return "", ""
        return row[0] or "", row[1] or ""

    def _article_srcdoc(self, art: dict) -> str:
        """The ONLY producer of preview HTML — EVERY path (mirror body_html,
        legacy body, markdown fallback) passes sanitize_html."""
        from src.data.html_sanitize import sanitize_html
        raw = art.get("body_html")
        if raw is None:
            raw = art.get("body") or ""
        return sanitize_html(str(raw))

    def _push_revisions(self, filt: str):
        conn = self._db()
        if conn is None:
            return
        from src.data import zendesk_store as store
        try:
            rows = store.list_revisions(conn)
        except Exception:  # noqa: BLE001
            rows = []
        wanted = {"open": ("pending", "ready"),
                  "copied": ("copied", "pushed"),
                  "all": ("pending", "ready", "copied", "pushed")}[filt]
        out = []
        for r in rows:
            if r.get("status") not in wanted:
                continue
            try:
                sources = json.loads(r.get("sources_json") or "[]")
            except (ValueError, TypeError):
                sources = []
            if not isinstance(sources, list):
                sources = []
            out.append({
                "draft_id": r["draft_id"], "kind": r["kind"],
                "target_id": r.get("target_id"),
                "target_title": r.get("target_title"),
                "title": r.get("title") or "", "status": r.get("status"),
                "rationale": r.get("rationale"), "sources": sources,
                "created_display": _display_date(r.get("created_at")),
                "copied_display": _display_date(r.get("copied_at")) or None,
                "is_new": r.get("target_id") is None})
            self._served_drafts[(r["kind"], r["draft_id"])] = r.get("status")
        self._emit(self.revisions_data, {"filter": filt, "revisions": out})

    # ── copy-exact resolution ────────────────────────────────────────
    def _resolve_copy(self, conn, target, tid, field):
        """(text, html|None, sanitized) for a validated copy request, or
        None for a silent refusal. DRAFT-content copies require the CURRENT
        DB status in ('ready','copied') — the pending→ready review step is
        ENFORCED at the clipboard boundary, not advisory."""
        from src.data import zendesk_store as store
        if target == "article":
            art = store.get_article(conn, tid)
            if art is None:
                return None
            if field == "title":
                return (art.get("title") or "", None, False)
            raw = art.get("body_html")
            if raw is None:
                raw = art.get("body") or ""
            raw = str(raw)
            if field == "body_html":
                # Plain-text HTML source: byte-verbatim for EVERY origin.
                return (raw, None, False)
            # body_rich: text/html mime so pasting keeps formatting. Mime
            # laundering guard — imported bodies are stored verbatim and
            # unsanitized, so the RICH variant passes sanitize_html before
            # it can reach the live Zendesk editor as formatted paste.
            html = raw
            sanitized = False
            if (art.get("origin") or "pull") == "import":
                from src.data.html_sanitize import sanitize_html
                html = sanitize_html(raw)
                sanitized = html != raw
            return (raw, html, sanitized)

        if target == "article_draft":
            d = store.get_article_draft(conn, tid)
            if d is None or d.get("status") not in ("ready", "copied"):
                return None      # an unreviewed pending draft never reaches
            if field == "title":  # the clipboard
                return (d.get("title") or "", None, False)
            html = d.get("body_html")
            if not html:
                # Renn drafts store markdown; render deterministically.
                from src.data.html_markdown import markdown_to_html
                html = markdown_to_html(d.get("body") or "")
            html = str(html)
            if field == "body_html":
                # Plain-text HTML source: byte-verbatim (pastes as text).
                return (html, None, False)
            # body_rich: the text/html mime variant passes sanitize_html
            # exactly like the origin='import' article path —
            # markdown_to_html passes raw inline HTML through, so draft
            # body_html is NOT guaranteed inert markup.
            from src.data.html_sanitize import sanitize_html
            safe = sanitize_html(html)
            return (html, safe, safe != html)

        if target == "macro":
            m = store.get_macro(conn, tid)
            if m is None:
                return None
            if field == "macro_name":
                return (m.get("name") or "", None, False)
            reply = _first_reply(m.get("actions"))
            return None if reply is None else (reply, None, False)

        # macro_draft
        d = store.get_macro_draft(conn, tid)
        if d is None or d.get("status") not in ("ready", "copied"):
            return None
        if field == "macro_name":
            return (d.get("name") or "", None, False)
        reply = _first_reply(d.get("actions"))
        return None if reply is None else (reply, None, False)

    # ── pull / import claim machinery ────────────────────────────────
    def _pick_files(self):
        if self._file_pick_fn is None:
            return None
        try:
            return list(self._file_pick_fn() or [])
        except Exception:  # noqa: BLE001
            return []

    def _pick_folder(self):
        if self._folder_pick_fn is None:
            return None
        try:
            folder = self._folder_pick_fn()
        except Exception:  # noqa: BLE001
            return []
        return [str(folder)] if folder else []

    def _start_import(self, picker):
        """Claim → native picker (nested event loop!) → runner. The claim is
        held through runner completion; released on cancel or failure."""
        if self._frozen():
            return
        self._pull_inflight = True
        paths = picker()
        if paths is None:
            # No picker injected at all — report rather than wedge.
            self._pull_inflight = False
            self._emit(self.import_resolved,
                       {"ok": False, "error": "not_started"})
            return
        if not paths:
            # Operator cancelled the dialog — silent release.
            self._pull_inflight = False
            return
        started = False
        if self._import_runner is not None:
            try:
                started = bool(self._import_runner(list(paths)))
            except Exception:  # noqa: BLE001
                started = False
        if not started:
            self._pull_inflight = False
            self._emit(self.import_resolved,
                       {"ok": False, "error": "not_started"})

    def _purge_text(self, conn, scope) -> str:
        """Scope-specific confirm text with live counts (§4 wording)."""
        if scope == "imported":
            n = conn.execute(
                "SELECT (SELECT COUNT(*) FROM zendesk_articles "
                "WHERE origin='import') + (SELECT COUNT(*) FROM "
                "zendesk_macros WHERE origin='import')").fetchone()[0]
            return (f"Remove {n} imported rows from the local mirror? "
                    "Revisions are kept.")
        n_articles = conn.execute(
            "SELECT COUNT(*) FROM zendesk_articles").fetchone()[0]
        n_macros = conn.execute(
            "SELECT COUNT(*) FROM zendesk_macros").fetchone()[0]
        if scope == "articles":
            return (f"Remove {n_articles} mirrored articles from the local "
                    "mirror? Revisions are kept.")
        if scope == "macros":
            return (f"Remove {n_macros} mirrored macros from the local "
                    "mirror? Revisions are kept.")
        n_open = conn.execute(
            "SELECT (SELECT COUNT(*) FROM zendesk_article_drafts "
            "WHERE status IN ('pending','ready')) + "
            "(SELECT COUNT(*) FROM zendesk_macro_drafts "
            "WHERE status IN ('pending','ready'))").fetchone()[0]
        return (f"Remove {n_articles} articles and {n_macros} macros AND "
                f"delete {n_open} pending/ready revisions (including Renn's "
                "open work)? Copied/pushed revisions are kept as the audit "
                "record. This cannot be undone.")
