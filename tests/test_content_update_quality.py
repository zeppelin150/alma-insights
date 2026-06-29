"""Grounding-verification (#2) + section-level targeted edits (#3)."""

from __future__ import annotations

from src.data.content_update.grounding import verify_grounding
from src.data.content_update.identify_updates import identify_updates
from src.data.content_update.models import (
    Change, PulledCard, SourceBundle, SourceDoc, UpdatePlan,
)
from src.data.content_update.write_targeted import write_targeted_updates

_SRC = "Telehealth copays are waived effective 2026-07-01 for Aetna members."


class _SectionStub:
    def __init__(self, revised):
        self.revised = revised
        self.calls = []

    def generate(self, prompt, system_prompt="", timeout=120):
        self.calls.append(prompt)
        if "Revise ONLY this one section" in prompt:
            return self.revised
        if "COMPLETE revised card" in prompt:        # whole-card fallback path
            return "TITLE: Card\n---\nwhole rewrite"
        return ""


# ── #2 grounding ──

def test_verify_grounding_drops_fabricated_evidence():
    changes = [Change("update", "Policy", "r", "telehealth copays are waived effective 2026"),
               Change("add", "X", "r", "unicorns provide free dental coverage worldwide")]
    grounded, dropped = verify_grounding(changes, _SRC)
    assert len(grounded) == 1 and grounded[0].section == "Policy"
    assert len(dropped) == 1 and "ungrounded" in dropped[0]["reason"]


def test_verify_grounding_keeps_no_evidence():
    grounded, dropped = verify_grounding([Change("update", "P", "r", "")], _SRC)
    assert len(grounded) == 1 and dropped == []


def test_identify_applies_grounding(empty_db):
    class LLM:
        def generate(self, prompt, system_prompt="", timeout=120):
            return ('```json\n{"summary":"s","changes":['
                    '{"type":"update","section":"Policy","reason":"r",'
                    '"evidence":"telehealth copays waived effective 2026"},'
                    '{"type":"add","section":"Z","reason":"r",'
                    '"evidence":"unicorns free dental coverage worldwide forever"}]}\n```')
    src = SourceBundle(ok=True, primary=SourceDoc("i", "Src", _SRC))
    plan = identify_updates(LLM(), src, PulledCard(ok=True, current_md="old", title="Card"))
    assert plan.ok and len(plan.changes) == 1 and plan.changes[0].section == "Policy"
    assert plan.dropped and "ungrounded" in plan.dropped[0]["reason"]


# ── #3 section-level targeted edits ──

def test_targeted_rewrites_only_affected_section_keeps_rest_verbatim():
    card_md = ("## Current policy (effective 2026-01-01)\n"
               "- Standard copay: collect the full amount.\n\n"
               "## Escalation\n"
               "- Eligibility mismatch: route to the Eligibility queue.")
    pulled = PulledCard(ok=True, draft_id=1, card_id="c1", title="Card", current_md=card_md)
    plan = UpdatePlan(ok=True, changes=[Change("update", "Current policy", "telehealth waiver", "ev")])
    src = SourceBundle(ok=True, primary=SourceDoc("i", "Src", _SRC))
    revised = "## Current policy (effective 2026-07-01)\n- Telehealth copays are WAIVED for virtual visits."
    stub = _SectionStub(revised)

    prop = write_targeted_updates(stub, pulled, plan, src, "")
    assert prop.ok
    assert "Telehealth copays are WAIVED" in prop.content_md          # affected section rewritten
    assert "route to the Eligibility queue" in prop.content_md        # untouched section verbatim
    assert "Standard copay: collect the full amount" not in prop.content_md  # old policy gone
    # only the one affected section was sent to the model
    assert sum(1 for c in stub.calls if "Revise ONLY this one section" in c) == 1


def test_targeted_falls_back_without_headings():
    pulled = PulledCard(ok=True, current_md="just one paragraph, no headings here", title="Card")
    plan = UpdatePlan(ok=True, changes=[Change("update", "Body", "r", "ev")])
    src = SourceBundle(ok=True, primary=SourceDoc("i", "S", "x"))
    prop = write_targeted_updates(_SectionStub("unused"), pulled, plan, src, "")
    assert prop.ok and "whole rewrite" in prop.content_md  # used the whole-card fallback
