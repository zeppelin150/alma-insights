"""Unit tests for the two LLM stages + the JSON helper.

Covers llm_json (extraction + bounded retry), identify_updates, write_updates.
"""

from __future__ import annotations

from content_update_helpers import SequencedLLM, StubLLM

from src.data.content_update.identify_updates import identify_updates
from src.data.content_update.llm_json import _extract_json, call_llm_json
from src.data.content_update.models import (
    Change, PulledCard, SourceBundle, SourceDoc, UpdatePlan,
)
from src.data.content_update.write_updates import write_updates


def _src():
    return SourceBundle(ok=True, primary=SourceDoc("inline", "Src", "new policy text"))


# ── llm_json ─────────────────────────────────────────────────────────

def test_extract_json_fenced():
    data, err = _extract_json('text ```json\n{"a": 1}\n``` trailing')
    assert err == "" and data == {"a": 1}


def test_extract_json_bare_object():
    data, err = _extract_json('preamble {"a": 2, "b": [1, 2]} end')
    assert err == "" and data == {"a": 2, "b": [1, 2]}


def test_extract_json_missing():
    _, err = _extract_json("no json here")
    assert err == "no_json_object_found"


def test_call_llm_json_retries_then_succeeds():
    llm = SequencedLLM(["not json at all", '```json\n{"changes": []}\n```'])
    res = call_llm_json(llm, "sys", "prompt",
                        lambda d: "" if "changes" in d else "missing changes")
    assert res["ok"] and res["data"] == {"changes": []}
    assert len(llm.calls) == 2


def test_call_llm_json_gives_up_on_validation():
    llm = SequencedLLM(['{"x": 1}', '{"x": 2}', '{"x": 3}'])
    res = call_llm_json(llm, "", "p", lambda d: "always invalid", retries=2)
    assert not res["ok"] and res["error"] == "always invalid"
    assert len(llm.calls) == 3  # initial + 2 retries


def test_call_llm_json_gives_up_on_unparseable():
    llm = SequencedLLM(["nope", "still nope", "bad"])
    res = call_llm_json(llm, "", "p", lambda d: "", retries=2)
    assert not res["ok"] and res["error"] == "no_json_object_found"


# ── identify_updates ─────────────────────────────────────────────────

def test_identify_updates_parses_plan():
    pulled = PulledCard(ok=True, current_md="old", title="Card")
    plan = identify_updates(StubLLM(), _src(), pulled)
    assert plan.ok and len(plan.changes) == 1
    assert plan.changes[0].section == "Intro"


def test_identify_updates_handles_bad_json():
    pulled = PulledCard(ok=True, current_md="old", title="Card")
    plan = identify_updates(SequencedLLM(["garbage", "garbage", "garbage"]), _src(), pulled)
    assert not plan.ok and plan.error


# ── write_updates ────────────────────────────────────────────────────

def test_write_updates_parses_card():
    pulled = PulledCard(ok=True, current_md="old body", title="Old Title")
    plan = UpdatePlan(ok=True, changes=[Change("update", "Intro", "reason")])
    prop = write_updates(StubLLM(title="New Title", body="Body A\nBody B"),
                         pulled, plan, _src(), "")
    assert prop.ok and prop.title == "New Title" and "Body A" in prop.content_md


def test_write_updates_empty_errors():
    pulled = PulledCard(ok=True, current_md="old", title="T")
    prop = write_updates(SequencedLLM([""]), pulled, UpdatePlan(ok=True), _src(), "")
    assert not prop.ok and prop.error == "empty_content"
