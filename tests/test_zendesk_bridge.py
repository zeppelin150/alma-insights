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
  (html_to_text(body_html) when set), so reviewed == copied always;
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
                     "title": "SSO (rev)",
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
    ctrl, seen = _controller(empty_db, clipboard_fn=_clipboard(calls))
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
    ctrl, seen = _controller(empty_db, clipboard_fn=_clipboard(calls))
    ctrl.js_refresh()
    _serve_revisions(ctrl)
    ctrl.js_copy_field("article", "1", "title")
    ctrl.js_copy_field("article_draft", "1", "title")
    assert calls == [("Mirror article", None), ("Draft one", None)]


def test_copy_pending_draft_refused_ready_allowed(empty_db):
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="T", body="**bold** body", article_id=101, rationale="r")
    calls = []
    ctrl, seen = _controller(empty_db, clipboard_fn=_clipboard(calls))
    _serve_revisions(ctrl)
    ctrl.js_copy_field("article_draft", str(did), "body_html")
    assert calls == [] and seen["copies"] == []    # pending → silent refusal
    zendesk_store.set_draft_status(conn, "article", did, "ready")
    ctrl.js_copy_field("article_draft", str(did), "body_html")
    assert len(calls) == 1
    assert "bold" in calls[0][0] and "**" not in calls[0][0]  # rendered md


def test_copy_body_rich_import_origin_sanitized(empty_db):
    conn = empty_db.conn
    zendesk_store.upsert_articles(conn, [
        {"id": 301, "title": "Imported", "body_html": HOSTILE_HTML,
         "updated_at": "2026-07-01T00:00:00Z"}], origin="import",
        source_file="C:/tmp/x.html")
    calls = []
    ctrl, seen = _controller(empty_db, clipboard_fn=_clipboard(calls))
    ctrl.js_refresh()
    # plain body_html copy: byte-verbatim for every origin
    ctrl.js_copy_field("article", "301", "body_html")
    assert calls[-1] == (HOSTILE_HTML, None)
    assert seen["copies"][-1]["sanitized"] is False
    # rich copy: the text/html mime variant is defanged before the clipboard
    ctrl.js_copy_field("article", "301", "body_rich")
    text, html = calls[-1]
    assert text == HOSTILE_HTML                    # plain stays verbatim
    assert "<script" not in html.lower() and "onerror" not in html.lower()
    assert seen["copies"][-1]["sanitized"] is True
    assert "sanitized" in seen["status"][-1]


def test_copy_pull_origin_body_rich_verbatim(empty_db):
    _seed_mirror(empty_db.conn)
    calls = []
    ctrl, seen = _controller(empty_db, clipboard_fn=_clipboard(calls))
    ctrl.js_refresh()
    ctrl.js_copy_field("article", "101", "body_rich")
    raw = "<h2>Steps</h2><p>Log into the admin console.</p>"
    assert calls == [(raw, raw)]
    assert seen["copies"][-1]["sanitized"] is False


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
    ctrl, seen = _controller(empty_db, clipboard_fn=_clipboard(calls))
    _serve_revisions(ctrl)
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
    ctrl, seen = _controller(empty_db, clipboard_fn=_clipboard(calls))
    _serve_revisions(ctrl)
    ctrl.js_copy_field("article_draft", str(did), "body_rich")
    assert calls[-1] == ("<h2>Steps</h2><p>Fine.</p>",
                         "<h2>Steps</h2><p>Fine.</p>")
    assert seen["copies"][-1]["sanitized"] is False


def test_copy_macro_reply_from_either_comment_field(empty_db):
    _seed_mirror(empty_db.conn)
    calls = []
    ctrl, seen = _controller(empty_db, clipboard_fn=_clipboard(calls))
    ctrl.js_refresh()
    ctrl.js_copy_field("macro", "201", "macro_reply")   # comment_value
    ctrl.js_copy_field("macro", "202", "macro_reply")   # comment_value_html
    assert [c[0] for c in calls] == ["Hi there", "<p>Hello rich</p>"]


def test_copy_forged_targets_and_missing_clipboard(empty_db):
    _seed_mirror(empty_db.conn)
    ctrl, seen = _controller(empty_db)                  # no clipboard_fn
    ctrl.js_refresh()
    # bad target / field / unserved id → silent
    ctrl.js_copy_field("wallet", "101", "title")
    ctrl.js_copy_field("article", "101", "raw_json")
    ctrl.js_copy_field("article", "555", "title")
    assert seen["copies"] == []
    # valid request without a clipboard fn resolves ok=False, no announcement
    ctrl.js_copy_field("article", "101", "title")
    assert seen["copies"][-1]["ok"] is False
    assert seen["status"] == []


# ── destructive gates: delete_revision ───────────────────────────────

def _gated(db, *, answer=True, **kw):
    confirms = []
    def confirm(title, text):
        confirms.append((title, text))
        if callable(answer):
            return answer()
        return answer
    ctrl, seen = _controller(db, confirm_fn=confirm, **kw)
    return ctrl, seen, confirms


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
    title, text = confirms[0]
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
    ctrl.set_status("hi")
    assert got["status"] == ["hi"]
    assert bridge.ping() == "pong"


def test_bridge_inert_uninjected_and_swallows_raises():
    inert = ZendeskBridge()
    inert.refresh(); inert.setView("articles"); inert.openArticle("1")
    inert.openMacro("1"); inert.search("q", "all"); inert.requestRevisions("open")
    inert.requestDiff("article", "1"); inert.saveDraft("article", "1", "{}")
    inert.markReady("article", "1"); inert.markCopied("article", "1")
    inert.copyField("article", "1", "title"); inert.requestImport()
    inert.requestImportFolder(); inert.requestPull()
    inert.deleteRevision("article", "1"); inert.purgeMirror("all")
    assert inert.ping() == "pong"
    def boom(*a):
        raise RuntimeError("hostile callable")
    angry = ZendeskBridge(refresh_fn=boom, save_fn=boom, purge_fn=boom,
                          copy_fn=boom, pull_fn=boom)
    angry.refresh(); angry.saveDraft("a", "1", "{}"); angry.purgeMirror("all")
    angry.copyField("article", "1", "title"); angry.requestPull()
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
