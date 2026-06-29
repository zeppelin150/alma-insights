"""Unit tests for the deterministic content_update stages.

Covers load_source, find_card, pull_card, validate, diff.
"""

from __future__ import annotations

from content_update_helpers import FakeGuru, FakeAsanaAttachments

from src.data.content_update.diff import unified
from src.data.content_update.find_card import find_target_card
from src.data.content_update.load_source import load_source
from src.data.content_update.models import (
    CardMatch, Change, ContentUpdateRequest, ProposedUpdate, PulledCard,
    SourceBundle, SourceDoc, UpdatePlan,
)
from src.data.content_update.pull_card import pull_target_card
from src.data.content_update.validate import validate_update


# ── load_source ──────────────────────────────────────────────────────

def test_load_source_from_asana_uploaded(empty_db):
    asana = FakeAsanaAttachments(
        attachments=[{"gid": "a1", "name": "policy.md", "subtype": "asana"}],
        detail={"a1": {"gid": "a1", "name": "policy.md",
                       "download_url": "https://s3/x", "host": "asana", "view_url": ""}},
    )
    seen = {}

    def http_get(url):
        seen["url"] = url
        return "New policy: Aetna copays are waived."

    req = ContentUpdateRequest(asana_task_gid="T1")
    bundle = load_source(empty_db.conn, req, asana_client=asana, http_get=http_get)
    assert bundle.ok
    assert "copays are waived" in bundle.primary.text
    assert seen["url"] == "https://s3/x"


def test_load_source_text_with_reference_doc(empty_db):
    conn = empty_db.conn
    from src.data import enablement_store as store
    store.save_document(conn, source="manual", name="Ref", doc_id="ref1",
                        full_text="reference truth")
    req = ContentUpdateRequest(source_text="primary text", source_title="P",
                               reference_refs=["ref1"])
    bundle = load_source(conn, req)
    assert bundle.ok and bundle.primary.text == "primary text"
    assert len(bundle.references) == 1
    assert bundle.references[0].text == "reference truth"


def test_load_source_no_source_errors(empty_db):
    bundle = load_source(empty_db.conn, ContentUpdateRequest())
    assert not bundle.ok and bundle.error == "no_source_text"


# ── find_card ────────────────────────────────────────────────────────

def _src(title):
    return SourceBundle(ok=True, primary=SourceDoc("inline", title, "x"))


def test_find_card_by_ref(empty_db):
    guru = FakeGuru(cards=[{"id": "c1", "title": "Copay policy", "content": "<p>old</p>"}])
    m = find_target_card(guru, _src("Copay"), ContentUpdateRequest(target_card_ref="c1"))
    assert m.ok and m.status == "matched" and m.card_id == "c1"


def test_find_card_search_strong_match(empty_db):
    guru = FakeGuru(cards=[
        {"id": "c1", "title": "Copay policy", "content": "x",
         "collection": "Billing", "collection_id": "col-1"},
        {"id": "c2", "title": "Unrelated subject", "content": "x",
         "collection": "Billing", "collection_id": "col-1"},
    ])
    m = find_target_card(guru, _src("Copay policy"), ContentUpdateRequest())
    assert m.ok and m.card_id == "c1"


def test_find_card_ambiguous_needs_human_pick(empty_db):
    guru = FakeGuru(cards=[
        {"id": "c1", "title": "Copay policy A", "content": "x"},
        {"id": "c2", "title": "Copay policy B", "content": "x"},
    ])
    m = find_target_card(guru, _src("Copay policy"), ContentUpdateRequest())
    assert not m.ok and m.status == "needs_human_pick" and len(m.candidates) == 2


def test_find_card_collection_scope_excludes(empty_db):
    guru = FakeGuru(cards=[{"id": "c1", "title": "Copay policy", "content": "x",
                            "collection": "Billing", "collection_id": "col-1"}])
    m = find_target_card(guru, _src("Copay policy"),
                         ContentUpdateRequest(collections=["Claims"]))
    assert not m.ok and m.status == "not_found"


# ── pull_card ────────────────────────────────────────────────────────

def test_pull_card_creates_linked_draft(empty_db):
    conn = empty_db.conn
    guru = FakeGuru(cards=[{"id": "c1", "title": "Copay policy",
                            "content": "<p>old body</p>"}])
    pulled = pull_target_card(conn, guru, CardMatch(ok=True, status="matched",
                                                    card_id="c1", title="Copay policy"))
    assert pulled.ok and pulled.draft_id > 0 and pulled.card_id == "c1"
    assert "old body" in pulled.current_md
    from src.data import enablement_store as store
    assert store.get_draft(conn, pulled.draft_id)["card_id"] == "c1"


# ── validate ─────────────────────────────────────────────────────────

def test_validate_flags_no_change_and_shrink():
    pulled = PulledCard(ok=True, card_id="c1", title="T",
                        current_md="line1\nline2\nline3\nline4")
    plan = UpdatePlan(ok=True, changes=[Change("update", "s", "r")])
    same = ProposedUpdate(ok=True, title="T", content_md="line1\nline2\nline3\nline4")
    assert any("no_change" in i for i in validate_update(pulled, same, plan))
    shrunk = ProposedUpdate(ok=True, title="T", content_md="x")
    assert any("content_shrank" in i for i in validate_update(pulled, shrunk, plan))


def test_validate_clean_proposal():
    pulled = PulledCard(ok=True, card_id="c1", current_md="aaaa bbbb cccc dddd")
    prop = ProposedUpdate(ok=True, title="T",
                          content_md="aaaa bbbb cccc dddd UPDATED with extra text here")
    issues = validate_update(pulled, prop, UpdatePlan(ok=True,
                                                      changes=[Change("update", "s", "r")]))
    assert issues == []


# ── diff ─────────────────────────────────────────────────────────────

def test_diff_shows_changes():
    d = unified("old line", "new line", title="Card")
    assert "-old line" in d and "+new line" in d
