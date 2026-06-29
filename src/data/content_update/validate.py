"""Deterministic critic for a proposed card update.

Returns a list of human-readable issue strings (empty == clean). This NEVER
blocks: the orchestrator stages the draft regardless and surfaces issues for the
reviewer. It is the cheap, model-free guard against the Haiku failure modes we
care about (no-op rewrites, content collapse, fabricated card links).
"""

from __future__ import annotations

import re

from .models import ProposedUpdate, PulledCard, UpdatePlan

_SHRINK_FLOOR = 0.4   # flag if proposed body is < 40% of the original length
_CARD_REF = re.compile(r"getguru\.com/card/([A-Za-z0-9_-]+)", re.I)


def validate_update(pulled: PulledCard, proposed: ProposedUpdate,
                    plan: UpdatePlan) -> list[str]:
    """Flag suspicious proposals; an empty list means the proposal looks safe."""
    issues: list[str] = []
    new = (proposed.content_md or "").strip()
    old = (pulled.current_md or "").strip()

    if not new:
        issues.append("empty_content: proposed card body is empty")
        return issues
    if not (proposed.title or "").strip():
        issues.append("empty_title: proposed card has no title")
    if new == old:
        issues.append("no_change: proposed content is identical to the current card")
    if len(new) < _SHRINK_FLOOR * max(1, len(old)):
        issues.append(
            f"content_shrank: proposed body is {len(new)} chars vs {len(old)} "
            "in the current card (<40%) — possible content loss"
        )
    if plan.ok and not plan.changes:
        issues.append("no_planned_changes: identify stage found nothing to update")

    foreign = {m for m in _CARD_REF.findall(new) if m != (pulled.card_id or "")}
    if foreign:
        issues.append(
            "foreign_card_link: proposed body references other Guru card id(s) "
            f"{sorted(foreign)} — verify these are intentional"
        )
    return issues
