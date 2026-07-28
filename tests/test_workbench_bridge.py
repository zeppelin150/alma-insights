"""WorkbenchWebController + WorkbenchBridge contract tests (no WebEngine).

The load-bearing invariant: EVERY preview_html the controller emits has been
through sanitize_html — stored Guru HTML and markdown-derived HTML alike. Plus
WorkbenchPage surface parity (page.py reaches into _current_drafts directly)
and untrusted-input gating on the bridge-facing entry points.
"""

import json

import pytest
from PySide6.QtCore import QCoreApplication

from src.services.enablement_web import WorkbenchWebController
from src.ui.web.workbench_bridge import WorkbenchBridge


@pytest.fixture(scope="module", autouse=True)
def _qt_app():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


CHIPS = [
    {"id": 1, "title": "SSO Setup", "source": "drive"},
    {"id": 2, "title": "Returns Policy", "source": "drive"},
    {"id": 3, "title": "Payments v2", "source": "guru"},
]

CARD = {
    "breadcrumb": "GURU › PROVIDER ENABLEMENT",
    "title": "Setting up SSO for Providers",
    "source": "From: SSO Setup.gdoc",
    "markdown": "## Steps\n1. Open Admin Console\n2. Upload the metadata XML",
}


def _md_html(md):
    """Content-sensitive stub converter — a constant one would make the
    publish belt's body comparison pass vacuously."""
    return f"<p>{md}</p>"


def _body(md, content_html=None):
    """The publish body for a card, computed the way the controller does.

    The belt compares publish bodies now (not the markdown column), so a fake
    content_lookup has to serve the same unit page.py serves it:
    ``enablement_store.publish_body(draft)``."""
    from src.data.enablement_store import publish_body
    return publish_body({"content": md, "content_html": content_html},
                        md_to_html=_md_html)


def _controller(**kw):
    kw.setdefault("md_to_html_fn", lambda md: "<h2>Steps</h2><p>rendered</p>")
    ctrl = WorkbenchWebController(**kw)
    seen = {"data": [], "drafts": [], "diffs": [], "selected": [], "closed": [],
            "upload": 0, "find": 0, "chat": 0}
    ctrl.workbench_data.connect(lambda j: seen["data"].append(json.loads(j)))
    ctrl.draft_loaded.connect(lambda j: seen["drafts"].append(json.loads(j)))
    ctrl.diff_ready.connect(lambda j: seen["diffs"].append(json.loads(j)))
    ctrl.draft_selected.connect(lambda i: seen["selected"].append(i))
    ctrl.workspace_closed.connect(lambda i: seen["closed"].append(i))
    ctrl.upload_requested.connect(lambda: seen.__setitem__("upload", seen["upload"] + 1))
    ctrl.find_task_requested.connect(lambda: seen.__setitem__("find", seen["find"] + 1))
    ctrl.open_chat_requested.connect(lambda: seen.__setitem__("chat", seen["chat"] + 1))
    return ctrl, seen


# ── preview sanitization: the load-bearing invariant ─────────────────

def test_stored_content_html_is_sanitized():
    ctrl, seen = _controller()
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    evil = dict(CARD, content_html=(
        '<h2>ok</h2><script>window.workbenchBridge.uploadRequested()</script>'
        '<img src=x onerror="alert(1)"><a href="javascript:alert(1)">x</a>'))
    ctrl.show_draft(evil)
    html = seen["drafts"][-1]["preview_html"]
    assert "<script" not in html.lower()
    assert "onerror" not in html.lower()
    assert "javascript:" not in html.lower()
    assert "<h2>ok</h2>" in html


def test_markdown_derived_html_is_sanitized_too():
    # even the md→html converter's output goes through the sanitizer — a
    # converter bug (or hostile markdown passthrough) can't reach the iframe
    ctrl, seen = _controller(
        md_to_html_fn=lambda md: '<p>fine</p><script>alert(1)</script>')
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD))
    html = seen["drafts"][-1]["preview_html"]
    assert "<script" not in html.lower() and "<p>fine</p>" in html


def test_md_converter_failure_degrades_to_escaped_pre():
    def boom(_md):
        raise RuntimeError("converter unavailable")
    ctrl, seen = _controller(md_to_html_fn=boom)
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD, markdown="<script>x</script> steps"))
    html = seen["drafts"][-1]["preview_html"]
    assert "<script" not in html.lower()
    assert "steps" in html


# ── WorkbenchPage surface parity ─────────────────────────────────────

def test_page_py_private_surface_exists():
    ctrl, _ = _controller()
    ctrl.set_pending_drafts(CHIPS, active_id=2)
    # page.py's _on_workspace_closed reads _current_drafts directly
    assert [d["id"] for d in ctrl._current_drafts] == [1, 2, 3]
    assert ctrl.active_draft_id == 2
    for attr in ("set_pending_drafts", "show_draft", "open_workspace",
                 "switch_workspace", "set_active_draft", "reload_active_canvas",
                 "set_linked_card_md", "set_existing_cards", "current_html",
                 "set_overlay_host", "draft_selected", "workspace_closed",
                 "find_task_requested", "publish_requested",
                 "load_file_requested", "open_chat_requested",
                 "existing_cards_requested", "import_requested",
                 "content_edited", "ai_edit_requested"):
        assert hasattr(ctrl, attr), attr


def test_active_id_defaulting_matches_qt():
    ctrl, seen = _controller()
    ctrl.set_pending_drafts(CHIPS)                 # no active → first
    assert ctrl.active_draft_id == 1
    ctrl.set_pending_drafts(CHIPS, active_id=3)
    ctrl.set_pending_drafts(CHIPS)                 # still open → keep 3
    assert ctrl.active_draft_id == 3
    ctrl.set_pending_drafts([])                    # emptied → None
    assert ctrl.active_draft_id is None


def test_workspace_cap_and_open_semantics():
    ctrl, seen = _controller()
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.open_workspace({"id": 4, "title": "Fourth", "source": "drive"})
    assert [d["id"] for d in ctrl._current_drafts] == [1, 2, 3, 4]
    ctrl.open_workspace({"id": 5, "title": "Fifth", "source": "drive"})
    assert [d["id"] for d in ctrl._current_drafts] == [2, 3, 4, 5]  # oldest dropped
    ctrl.open_workspace({"id": 3, "title": "Payments v2", "source": "guru"})
    assert ctrl.active_draft_id == 3               # already open → just activate


def test_switch_workspace_shows_card_and_current_html():
    ctrl, seen = _controller()
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.switch_workspace(3, dict(CARD, content_html="<p>rich</p>"))
    assert ctrl.active_draft_id == 3
    assert seen["drafts"][-1]["title"] == CARD["title"]
    assert ctrl.current_html() == "<p>rich</p>"


def test_set_active_draft_replays_cached_card():
    ctrl, seen = _controller()
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD, title="One"))
    ctrl.switch_workspace(2, dict(CARD, title="Two"))
    ctrl.set_active_draft(1)
    assert seen["drafts"][-1]["title"] == "One"    # cached card re-rendered


# ── checks + diff ────────────────────────────────────────────────────

def test_checks_ride_the_draft_payload_and_fail_soft():
    rows = [{"check": "pii", "status": "ok", "detail": ""}]
    ctrl, seen = _controller(checks_fn=lambda md: rows)
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD))
    assert seen["drafts"][-1]["checks"] == rows

    def boom(_md):
        raise RuntimeError("checks down")
    ctrl2, seen2 = _controller(checks_fn=boom)
    ctrl2.set_pending_drafts(CHIPS, active_id=1)
    ctrl2.show_draft(dict(CARD))
    assert seen2["drafts"][-1]["checks"] == []


def test_diff_uses_linked_baseline_word_level():
    ctrl, seen = _controller()
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD, markdown="the rollout is June 24",
                         linked_card_md="the rollout is June 10"))
    ctrl.js_request_diff()
    d = seen["diffs"][-1]
    assert d["baseline_present"] is True and d["change_count"] == 2
    add_row = next(r for r in d["rows"] if r["tag"] == "add")
    assert {"tag": "add", "text": "24"} in add_row["spans"]


def test_diff_without_baseline_flags_it():
    ctrl, seen = _controller()
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD))
    ctrl.js_request_diff()
    assert seen["diffs"][-1]["baseline_present"] is False


# ── M4 editing: content_edited parity with the Qt editor ─────────────

def _seen_edits(ctrl, seen):
    seen["edited"] = []
    seen["previews"] = []
    ctrl.content_edited.connect(lambda i, m: seen["edited"].append((i, m)))
    ctrl.preview_updated.connect(lambda j: seen["previews"].append(json.loads(j)))


def test_content_edited_persists_and_clears_rich_html():
    ctrl, seen = _controller()
    _seen_edits(ctrl, seen)
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD, content_html="<p>rich</p>"))
    assert ctrl.current_html() == "<p>rich</p>"
    ctrl.js_content_edited("1", "# edited body")
    assert seen["edited"] == [(1, "# edited body")]
    # markdown edit clears the cached rich HTML — the Qt parity rule
    assert ctrl.current_html() is None
    # the preview echo carries fresh sanitized HTML + checks, no draft_loaded
    assert seen["previews"][-1]["id"] == 1
    assert "<script" not in seen["previews"][-1]["preview_html"].lower()


def test_content_edited_only_for_the_active_workspace():
    ctrl, seen = _controller()
    _seen_edits(ctrl, seen)
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD))
    ctrl.js_content_edited("2", "hijack")     # open but NOT active
    ctrl.js_content_edited("99", "forged")    # not open at all
    ctrl.js_content_edited("", "junk")
    assert seen["edited"] == []


def test_content_edited_unchanged_text_is_silent():
    ctrl, seen = _controller()
    _seen_edits(ctrl, seen)
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD))
    ctrl.js_content_edited("1", CARD["markdown"])
    assert seen["edited"] == [] and seen["previews"] == []


def test_ai_edit_validated_and_capped():
    ctrl, seen = _controller()
    asked = []
    ctrl.ai_edit_requested.connect(lambda i, s: asked.append((i, s)))
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD))
    ctrl.js_ai_edit("  Tighten the intro  ", "selected words")
    assert asked == [("Tighten the intro", "selected words")]
    ctrl.js_ai_edit("", "sel")                # empty instruction → no-op
    ctrl.js_ai_edit("   ", "sel")
    assert len(asked) == 1
    ctrl.js_ai_edit("x" * 5000, "y" * 20000)  # capped, not rejected
    assert len(asked[1][0]) == 2000 and len(asked[1][1]) == 8000


def test_ai_edit_without_active_draft_is_silent():
    ctrl, _ = _controller()
    asked = []
    ctrl.ai_edit_requested.connect(lambda i, s: asked.append((i, s)))
    ctrl.js_ai_edit("Tighten", "")
    assert asked == []


# ── M4 gated publish ─────────────────────────────────────────────────

def _pub(confirm=True):
    calls = {"confirm": [], "published": [], "resolved": []}

    def confirm_fn(dest, title):
        calls["confirm"].append((dest, title))
        return confirm(dest, title) if callable(confirm) else bool(confirm)

    ctrl = WorkbenchWebController(md_to_html_fn=lambda md: "<p>x</p>",
                                  publish_confirm_fn=confirm_fn)
    ctrl.publish_requested.connect(lambda d: calls["published"].append(d))
    ctrl.publish_resolved.connect(lambda j: calls["resolved"].append(json.loads(j)))
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD))
    return ctrl, calls


def test_publish_approved_dispatches_once():
    ctrl, calls = _pub(confirm=True)
    ctrl.js_request_publish("guru_new")
    assert calls["confirm"] == [("guru_new", CARD["title"])]
    assert calls["published"] == ["guru_new"]
    r = calls["resolved"][0]
    assert r["approved"] is True and r["dispatched"] is True


def test_publish_cancelled_never_dispatches():
    ctrl, calls = _pub(confirm=False)
    ctrl.js_request_publish("drive_new")
    assert calls["published"] == []
    assert calls["resolved"][0]["cancelled"] is True


def test_publish_forged_destinations_are_silent():
    ctrl, calls = _pub(confirm=True)
    for dest in ("guru_delete_all", "", None, "guru_existing:forged-card",
                 "drive_new; rm -rf", "guru_existing:"):
        ctrl.js_request_publish(dest)
    assert calls["confirm"] == [] and calls["resolved"] == []


def test_publish_existing_only_for_offered_keys():
    ctrl, calls = _pub(confirm=True)
    ctrl.set_existing_cards([{"id": "card-9", "title": "Payments v2"}])
    ctrl.js_request_publish("guru_existing:card-9")
    assert calls["published"] == ["guru_existing:card-9"]
    ctrl.js_request_publish("guru_existing:card-10")   # never offered
    assert calls["published"] == ["guru_existing:card-9"]


def test_publish_without_confirm_fn_fails_closed():
    ctrl = WorkbenchWebController(md_to_html_fn=lambda md: "<p>x</p>")
    published, resolved = [], []
    ctrl.publish_requested.connect(lambda d: published.append(d))
    ctrl.publish_resolved.connect(lambda j: resolved.append(json.loads(j)))
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD))
    ctrl.js_request_publish("guru_new")
    assert published == [] and resolved[0]["cancelled"] is True


def test_publish_single_winner_under_reentrancy():
    holder = {}

    def reentrant(dest, title):
        holder["ctrl"].js_request_publish("drive_new")   # mid-dialog
        return True

    ctrl, calls = _pub(confirm=reentrant)
    holder["ctrl"] = ctrl
    ctrl.js_request_publish("guru_new")
    assert calls["confirm"] == [("guru_new", CARD["title"])]   # one dialog
    assert calls["published"] == ["guru_new"]                  # one dispatch


def test_publish_confirm_raising_means_no():
    def boom(_d, _t):
        raise RuntimeError("dialog broke")
    ctrl, calls = _pub(confirm=boom)
    ctrl.js_request_publish("guru_new")
    assert calls["published"] == []
    assert calls["resolved"][0]["cancelled"] is True


def test_publish_without_active_draft_is_silent():
    ctrl, calls = _pub(confirm=True)
    ctrl.set_pending_drafts([])
    ctrl.js_request_publish("guru_new")
    assert calls["confirm"] == []


# ── M4 import + existing cards ───────────────────────────────────────

def test_import_kind_allowlist():
    ctrl, _ = _controller()
    kinds = []
    ctrl.import_requested.connect(lambda k: kinds.append(k))
    ctrl.js_request_import("drive")
    ctrl.js_request_import("guru")
    ctrl.js_request_import("zendesk")
    ctrl.js_request_import("")
    assert kinds == ["drive", "guru"]


def test_existing_cards_push_and_request_loop():
    ctrl, _ = _controller()
    pushed, asked = [], []
    ctrl.existing_cards_data.connect(lambda j: pushed.append(json.loads(j)))
    ctrl.existing_cards_requested.connect(lambda: asked.append(True))
    ctrl.js_existing_cards()
    assert asked == [True]
    ctrl.set_existing_cards([{"id": "c1", "title": "One"}, {"title": "TitleOnly"},
                             {"id": "", "title": ""}])
    assert pushed[-1]["items"] == [{"key": "c1", "title": "One"},
                                   {"key": "TitleOnly", "title": "TitleOnly"}]


# ── M6 review fixes: publish confused-deputy, draft dedup, ai-resolve ─

def test_publish_frozen_slots_block_mid_modal_swap():
    # The confused-deputy exploit: a page script calls switchWorkspace /
    # contentEdited DURING the native confirm to redirect the publish. With the
    # slots frozen while _publish_inflight, the mid-modal calls are no-ops and
    # the publish targets the confirmed draft.
    switched, edited, published = [], [], []

    def confirm_mid_modal(dest, title):
        # simulate the page firing other slots while the modal is up
        ctrl.js_switch_workspace("2")
        ctrl.js_content_edited("1", "attacker rewrite")
        return True

    ctrl = WorkbenchWebController(md_to_html_fn=lambda md: "<p>x</p>",
                                  publish_confirm_fn=confirm_mid_modal)
    ctrl.draft_selected.connect(lambda i: switched.append(i))
    ctrl.content_edited.connect(lambda i, m: edited.append((i, m)))
    ctrl.publish_requested.connect(lambda d: published.append((d, ctrl.active_draft_id)))
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD))
    ctrl.js_request_publish("guru_new")
    # the mutating slots were frozen → no swap, no rewrite
    assert switched == [] and edited == []
    # publish fired with the ORIGINAL active draft still in place
    assert published == [("guru_new", 1)]


def test_publish_freezes_ai_edit_too():
    # re-verify residual: js_ai_edit is the parallel content-mutation slot; it
    # must also be frozen during the confirm, else a page script revises the
    # DB body of the draft being published.
    asked = []

    def confirm_tries_ai_edit(dest, title):
        ctrl.js_ai_edit("rewrite the body", "")   # mid-modal revise attempt
        return True

    ctrl = WorkbenchWebController(md_to_html_fn=lambda md: "<p>x</p>",
                                  publish_confirm_fn=confirm_tries_ai_edit)
    ctrl.ai_edit_requested.connect(lambda i, s: asked.append((i, s)))
    published = []
    ctrl.publish_requested.connect(lambda d: published.append(d))
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD))
    ctrl.js_request_publish("guru_new")
    assert asked == []                     # the mid-modal revise was frozen out
    assert published == ["guru_new"]


def test_publish_belt_compares_the_db_body_not_just_cache():
    # The authoritative close for the de-focused-revise divergence: publish
    # reads the DB, so the belt must compare the DB body. Here _cards stays
    # OLD (stale) but the DB (content_lookup) went NEW behind the cache → the
    # belt must refuse (operator saw OLD, DB holds NEW).
    db = {1: "OLD"}
    ctrl = WorkbenchWebController(
        md_to_html_fn=_md_html,
        publish_confirm_fn=lambda dest, title: True,
        content_lookup=lambda did: _body(db.get(int(did))))
    published, resolved = [], []
    ctrl.publish_requested.connect(lambda d: published.append(d))
    ctrl.publish_resolved.connect(lambda j: resolved.append(json.loads(j)))
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD, markdown="OLD"))
    db[1] = "NEW"                          # DB diverged from the stale cache
    ctrl.js_request_publish("guru_new")
    assert published == [] and resolved[0]["cancelled"] is True


def test_publish_allowed_when_db_matches_confirmed():
    db = {1: "AGREED"}
    ctrl = WorkbenchWebController(
        md_to_html_fn=_md_html,
        publish_confirm_fn=lambda dest, title: True,
        content_lookup=lambda did: _body(db.get(int(did))))
    published = []
    ctrl.publish_requested.connect(lambda d: published.append(d))
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD, markdown="AGREED"))
    ctrl.js_request_publish("guru_new")
    assert published == ["guru_new"]       # cache, DB, confirm all agree → ships


def test_publish_belt_catches_a_content_html_only_divergence():
    # A2/A6: the markdown column agreed while content_html — the field that
    # actually ships — did not. The old belt compared markdown and approved.
    db = {1: _body("SAME", content_html="<p>SAME</p><p>hidden rider</p>")}
    ctrl = WorkbenchWebController(
        md_to_html_fn=_md_html,
        publish_confirm_fn=lambda dest, title: True,
        content_lookup=lambda did: db.get(int(did)))
    published, resolved = [], []
    ctrl.publish_requested.connect(lambda d: published.append(d))
    ctrl.publish_resolved.connect(lambda j: resolved.append(json.loads(j)))
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD, markdown="SAME"))     # cache: markdown only
    ctrl.js_request_publish("guru_new")
    assert published == [] and resolved[0]["cancelled"] is True


def test_preview_html_is_the_sanitized_publish_body():
    # The preview's SOURCE is publish_body — stored content_html wins over the
    # markdown exactly as it does at publish time.
    from src.data.html_sanitize import sanitize_html
    ctrl, seen = _controller(md_to_html_fn=_md_html)
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    card = dict(CARD, markdown="markdown body",
                content_html="<p>THE HTML THAT SHIPS</p>")
    ctrl.show_draft(card)
    assert seen["drafts"][-1]["preview_html"] == sanitize_html(_body(
        "markdown body", content_html="<p>THE HTML THAT SHIPS</p>"))
    assert "markdown body" not in seen["drafts"][-1]["preview_html"]


def test_diff_proposed_side_derives_from_the_publish_body():
    # Review-changes must diff what ships: a draft whose content_html differs
    # from its markdown is diffed as the HTML-derived text.
    ctrl, seen = _controller(md_to_html_fn=_md_html)
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD, markdown="unused markdown",
                         content_html="<p>quiz only</p>",
                         linked_card_md="the original body"))
    ctrl.js_request_diff()
    text = " ".join(r.get("text", "") for r in seen["diffs"][-1]["rows"])
    assert "quiz only" in text and "unused markdown" not in text


def test_publish_refuses_while_a_revise_is_in_flight():
    # deterministic close for the cross-thread revise race: a revise dispatched
    # before publish is still running (notify_ai_edit_done not yet called) →
    # publish is a no-op until it settles.
    confirms, published = [], []
    ctrl = WorkbenchWebController(
        md_to_html_fn=lambda md: "<p>x</p>",
        publish_confirm_fn=lambda d, t: confirms.append((d, t)) or True)
    ctrl.publish_requested.connect(lambda d: published.append(d))
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD))
    ctrl.js_ai_edit("revise it", "")            # revise now in flight
    ctrl.js_request_publish("guru_new")
    assert confirms == [] and published == []   # blocked while revising
    ctrl.notify_ai_edit_done(True)              # revise settled
    ctrl.js_request_publish("guru_new")
    assert published == ["guru_new"]            # now it ships


def test_publish_refuses_while_chat_engine_busy():
    # closes the chat-driven-revise race: a chat turn (which may run a
    # revise_draft tool that writes the DB) blocks publish until it settles.
    busy = {"v": True}
    published = []
    ctrl = WorkbenchWebController(
        md_to_html_fn=lambda md: "<p>x</p>",
        publish_confirm_fn=lambda d, t: True,
        busy_lookup=lambda: busy["v"])
    ctrl.publish_requested.connect(lambda d: published.append(d))
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD))
    ctrl.js_request_publish("guru_new")
    assert published == []                  # blocked while a chat turn runs
    busy["v"] = False
    ctrl.js_request_publish("guru_new")
    assert published == ["guru_new"]        # ships once the engine is idle


def test_publish_busy_lookup_error_treated_as_busy():
    def boom():
        raise RuntimeError("engine gone")
    published = []
    ctrl = WorkbenchWebController(
        md_to_html_fn=lambda md: "<p>x</p>",
        publish_confirm_fn=lambda d, t: True, busy_lookup=boom)
    ctrl.publish_requested.connect(lambda d: published.append(d))
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD))
    ctrl.js_request_publish("guru_new")
    assert published == []                  # unreadable engine → treat as busy


def test_is_publish_inflight_property():
    idle = WorkbenchWebController(md_to_html_fn=lambda md: "<p>x</p>")
    assert idle.is_publish_inflight is False
    # True only inside the confirm; capture it from within the confirm callback
    seen = {}
    holder = {}

    def confirm(dest, title):
        seen["inflight"] = holder["ctrl"].is_publish_inflight
        return False

    ctrl = WorkbenchWebController(md_to_html_fn=lambda md: "<p>x</p>",
                                  publish_confirm_fn=confirm)
    holder["ctrl"] = ctrl
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD))
    ctrl.js_request_publish("guru_new")
    assert seen["inflight"] is True
    assert ctrl.is_publish_inflight is False   # cleared in finally


def test_publish_belt_fails_closed_on_lookup_error():
    def boom(_did):
        raise RuntimeError("db down")
    ctrl = WorkbenchWebController(
        md_to_html_fn=lambda md: "<p>x</p>",
        publish_confirm_fn=lambda dest, title: True, content_lookup=boom)
    published = []
    ctrl.publish_requested.connect(lambda d: published.append(d))
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD))
    ctrl.js_request_publish("guru_new")
    assert published == []                  # can't read the DB → refuse


def test_publish_refuses_if_content_changed_during_modal():
    # belt: an in-flight revise landing mid-modal (simulated by mutating the
    # controller's card during confirm) must abort the publish
    def confirm_mutates_content(dest, title):
        ctrl._cards[1]["markdown"] = "attacker body"   # a revise reload landed
        return True

    ctrl = WorkbenchWebController(md_to_html_fn=_md_html,
                                  publish_confirm_fn=confirm_mutates_content)
    published, resolved = [], []
    ctrl.publish_requested.connect(lambda d: published.append(d))
    ctrl.publish_resolved.connect(lambda j: resolved.append(json.loads(j)))
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD))
    ctrl.js_request_publish("guru_new")
    assert published == []                  # content changed → refused
    assert resolved[0]["cancelled"] is True


def test_content_edited_invalidates_push_dedup():
    # re-verify residual: after a committed edit (preview_updated only, no
    # draft_loaded), a later host push of the PRE-edit value must NOT be
    # deduped — the real content update has to reach the page.
    ctrl, seen = _controller()
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD, markdown="X"))          # push (1, "X")
    n = len(seen["drafts"])
    ctrl.js_content_edited("1", "Y")                    # commit → preview only
    assert len(seen["drafts"]) == n                    # no draft_loaded
    # host legitimately reverts A back to "X" (discard-edits / revise-to-orig)
    ctrl.show_draft(dict(CARD, markdown="X"))
    assert len(seen["drafts"]) == n + 1                # NOT deduped → pushed


def test_draft_loaded_dedup_skips_identical_repush():
    ctrl, seen = _controller()
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD))
    n = len(seen["drafts"])
    # an incidental host refresh re-pushes the SAME card → no draft_loaded (it
    # would wipe uncommitted editor text); a changed card DOES push
    ctrl.show_draft(dict(CARD))
    assert len(seen["drafts"]) == n
    ctrl.show_draft(dict(CARD, markdown="genuinely new body"))
    assert len(seen["drafts"]) == n + 1


def test_request_refresh_forces_push_after_reconnect():
    ctrl, seen = _controller()
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD))
    n = len(seen["drafts"])
    # a remounted page must get a fresh push even though content is unchanged
    ctrl.request_refresh()
    assert len(seen["drafts"]) == n + 1


def test_notify_ai_edit_done_emits_both_outcomes():
    ctrl, _ = _controller()
    got = []
    ctrl.ai_edit_resolved.connect(lambda j: got.append(json.loads(j)))
    ctrl.notify_ai_edit_done(True)
    ctrl.notify_ai_edit_done(False)
    assert got == [{"ok": True}, {"ok": False}]


def test_existing_cards_re_emits_cached_on_repeat_open():
    ctrl, _ = _controller()
    pushed, asked = [], []
    ctrl.existing_cards_data.connect(lambda j: pushed.append(json.loads(j)))
    ctrl.existing_cards_requested.connect(lambda: asked.append(True))
    # first open: no cache yet → just asks the host
    ctrl.js_existing_cards()
    assert asked == [True] and pushed == []
    # host feeds a result (even empty), marking fetched
    ctrl.set_existing_cards([{"id": "c1", "title": "One"}])
    assert len(pushed) == 1
    # second open: re-serves the cache immediately (no forever-spinner) AND asks
    ctrl.js_existing_cards()
    assert pushed[-1]["items"] == [{"key": "c1", "title": "One"}]
    assert asked == [True, True]


def test_existing_cards_empty_result_still_re_serves():
    ctrl, _ = _controller()
    pushed = []
    ctrl.existing_cards_data.connect(lambda j: pushed.append(json.loads(j)))
    ctrl.set_existing_cards([])          # host said "no cards"
    ctrl.js_existing_cards()
    assert pushed[-1]["items"] == []     # re-serves [] → React shows "No cards found"


# ── untrusted bridge-facing entry points ─────────────────────────────

def test_js_switch_and_close_gate_on_open_chips():
    ctrl, seen = _controller()
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.js_switch_workspace("2")
    ctrl.js_close_workspace("3")
    assert seen["selected"] == [2] and seen["closed"] == [3]
    for forged in ("99", "", None, "abc", "1; DROP TABLE"):
        ctrl.js_switch_workspace(forged)
        ctrl.js_close_workspace(forged)
    assert seen["selected"] == [2] and seen["closed"] == [3]   # unchanged


def test_refresh_replays_chips_and_active_card():
    ctrl, seen = _controller()
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD))
    n_data, n_drafts = len(seen["data"]), len(seen["drafts"])
    ctrl.request_refresh()
    assert len(seen["data"]) == n_data + 1
    assert len(seen["drafts"]) == n_drafts + 1


def test_bridge_relays_slots_and_signals():
    ctrl, seen = _controller()
    bridge = WorkbenchBridge(
        data_signal=ctrl.workbench_data, draft_signal=ctrl.draft_loaded,
        diff_signal=ctrl.diff_ready, ai_resolved_signal=ctrl.ai_edit_resolved,
        refresh_fn=ctrl.request_refresh,
        switch_fn=ctrl.js_switch_workspace, close_fn=ctrl.js_close_workspace,
        diff_fn=ctrl.js_request_diff, upload_fn=ctrl.js_upload,
        find_task_fn=ctrl.js_find_task, open_chat_fn=ctrl.js_open_chat)
    got = {"data": [], "drafts": [], "diffs": []}
    bridge.workbenchData.connect(lambda j: got["data"].append(json.loads(j)))
    bridge.draftLoaded.connect(lambda j: got["drafts"].append(json.loads(j)))
    bridge.diffReady.connect(lambda j: got["diffs"].append(json.loads(j)))
    ctrl.set_pending_drafts(CHIPS, active_id=1)
    ctrl.show_draft(dict(CARD))
    assert got["data"] and got["drafts"]
    bridge.switchWorkspace("2")
    assert seen["selected"] == [2]
    bridge.requestDiff()
    assert got["diffs"]
    bridge.uploadRequested()
    bridge.findTask()
    bridge.openChat()
    assert seen["upload"] == 1 and seen["find"] == 1 and seen["chat"] == 1
    assert bridge.ping() == "pong"
    inert = WorkbenchBridge()          # nothing injected → all slots inert
    inert.refresh(); inert.switchWorkspace("1"); inert.requestDiff()
    assert inert.ping() == "pong"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
