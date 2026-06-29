"""Hardening tests covering the review findings (F1-F5) + untested branches."""

from __future__ import annotations

import pytest
from content_update_helpers import FakeDrive, FakeGuru, SequencedLLM

from src.data.content_update import ContentUpdateRequest, Deps, run_content_update
from src.data.content_update.attachment import fetch_attachment_text, pick_attachment
from src.data.content_update.find_card import find_target_card
from src.data.content_update.llm_json import _extract_json
from src.data.content_update.models import (
    PulledCard, SourceBundle, SourceDoc, UpdatePlan,
)
from src.data.content_update.prompts import _render_refs, build_identify_prompt, build_write_prompt

_DRIVE_ID = "A" * 20


def _src(title):
    return SourceBundle(ok=True, primary=SourceDoc("inline", title, "x"))


# ── F1/F2: robust JSON extraction ────────────────────────────────────

def test_extract_json_brace_inside_string_value():
    data, err = _extract_json('{"reason": "use the } and { chars", "x": 1}')
    assert err == "" and data == {"reason": "use the } and { chars", "x": 1}


def test_extract_json_bare_array_is_rejected_not_sliced():
    data, err = _extract_json('[{"a": 1}, {"b": 2}]')
    assert data == {} and err == "json_not_an_object"


def test_extract_json_object_with_prose_around_it():
    data, err = _extract_json('Here it is: {"changes": []} thanks')
    assert err == "" and data == {"changes": []}


# ── F3: harness http_get scheme guard ────────────────────────────────

def test_demo_http_get_rejects_non_http_scheme():
    from scripts.run_content_update_demo import _http_get
    with pytest.raises(ValueError):
        _http_get("file:///etc/passwd")


# ── F4/F5: Drive-host attachment handling ────────────────────────────

def test_fetch_attachment_google_drive_uses_reader_not_http():
    drive = FakeDrive(texts={_DRIVE_ID: "drive doc text"})
    att = {"host": "google_drive", "download_url": "",
           "view_url": f"https://drive.google.com/d/{_DRIVE_ID}/view"}
    used = {"http": False}

    def http_get(url):
        used["http"] = True
        return "SHOULD NOT BE USED"

    text = fetch_attachment_text(att, http_get=http_get, drive_reader=drive)
    assert text == "drive doc text" and used["http"] is False


def test_fetch_attachment_google_drive_no_reader_returns_empty():
    att = {"host": "google_drive", "download_url": "https://drive.google.com/uc?id=x",
           "view_url": "https://drive.google.com/d/AAAAAAAAAAAAAAAAAAAA/view"}
    # Must NOT fall back to GETting the Drive URL (would return a login page).
    assert fetch_attachment_text(att, http_get=lambda u: "<html>login</html>",
                                 drive_reader=None) == ""


def test_pick_attachment_selector_then_fallback():
    atts = [{"gid": "a1", "name": "new policy.md"}, {"gid": "a2", "name": "old draft.md"}]
    assert pick_attachment(atts, "old")["gid"] == "a2"
    assert pick_attachment(atts, "nomatch")["gid"] == "a1"
    assert pick_attachment([], "x") is None


# ── find_card untested branches ──────────────────────────────────────

def test_find_card_by_ref_not_found():
    m = find_target_card(FakeGuru(cards=[]), _src("X"),
                         ContentUpdateRequest(target_card_ref="missing"))
    assert not m.ok and m.status == "not_found"


def test_find_card_exact_name_resolves_ambiguity():
    guru = FakeGuru(cards=[{"id": "c1", "title": "Copay policy A", "content": "x"},
                           {"id": "c2", "title": "Copay policy B", "content": "x"}])
    m = find_target_card(guru, _src("Copay policy"),
                         ContentUpdateRequest(target_card_name="Copay policy B"))
    assert m.ok and m.card_id == "c2"


def test_find_card_small_score_gap_is_ambiguous():
    guru = FakeGuru(cards=[{"id": "c1", "title": "Aetna copay policy", "content": "x"},
                           {"id": "c2", "title": "Aetna copay process", "content": "x"}])
    m = find_target_card(guru, _src("Aetna copay"), ContentUpdateRequest())
    assert not m.ok and m.status == "needs_human_pick"


# ── orchestrator write-failure path ──────────────────────────────────

def test_pipeline_write_failure_is_graceful(empty_db):
    guru = FakeGuru(cards=[{"id": "c1", "title": "Copay policy", "content": "<p>old</p>"}])
    llm = SequencedLLM(['```json\n{"summary": "s", "changes": []}\n```', ""])
    deps = Deps(llm_client=llm, guru_client=guru)
    req = ContentUpdateRequest(source_text="x", source_title="Copay policy",
                               target_card_ref="c1")
    res = run_content_update(empty_db.conn, req, deps)
    assert not res.ok and res.stage == "write"
    assert res.plan is not None and res.draft_id > 0


# ── prompt rendering ─────────────────────────────────────────────────

def test_identify_prompt_includes_references_no_token_leak():
    src = SourceBundle(ok=True, primary=SourceDoc("inline", "Primary", "primary body"),
                       references=[SourceDoc("ref1", "Ref One", "reference body")])
    pulled = PulledCard(ok=True, current_md="card body", title="Card")
    prompt = build_identify_prompt(src, pulled)
    assert "reference body" in prompt and "Ref One" in prompt and "<<" not in prompt


def test_write_prompt_no_changes_sentinel():
    pulled = PulledCard(ok=True, current_md="c", title="Card")
    src = SourceBundle(ok=True, primary=SourceDoc("i", "S", "x"))
    prompt = build_write_prompt(pulled, UpdatePlan(ok=True, changes=[]), src, "")
    assert "no specific changes" in prompt and "<<" not in prompt


def test_render_refs_none():
    assert _render_refs(SourceBundle(ok=True, primary=SourceDoc("i", "S", "x"))) == "(none provided)"


# ── demo request mapping ─────────────────────────────────────────────

def test_demo_build_request_maps_and_coalesces():
    from types import SimpleNamespace
    from scripts.run_content_update_demo import _build_request
    args = SimpleNamespace(asana_task="T1", attachment="pol", doc=None, text=None,
                           title="Ttl", card="c1", card_name=None, search=None,
                           collection=None, reference=None, approved_by="me")
    req = _build_request(args)
    assert req.asana_task_gid == "T1" and req.target_card_ref == "c1"
    assert req.approved_by == "me" and req.collections == [] and req.reference_refs == []
