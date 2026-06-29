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
# Any /card/<token> ref; the capture is permissive so malformed ids are caught
# by the valid-card-id check below rather than silently skipped here.
_CARD_REF = re.compile(r"getguru\.com/card/(\S+)", re.I)
# A well-formed Guru card id is the URL-safe slug the API uses: letters, digits,
# underscore, hyphen (mirrors enablement_store._GURU_CARD_URL's capture class).
_VALID_CARD_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_update(pulled: PulledCard, proposed: ProposedUpdate,
                    plan: UpdatePlan, *, style_block: str = "") -> list[str]:
    """Flag suspicious proposals; an empty list means the proposal looks safe.

    ``style_block`` (default ``""``) is forwarded to the shared
    ``enablement_checks`` library: the conn-free link / PII / readability /
    valid-card-id checks always run, while style-conformance only contributes
    when a non-empty style block is supplied. The default reproduces the
    original (style-free) behaviour for existing call sites.
    """
    new = (proposed.content_md or "").strip()
    old = (pulled.current_md or "").strip()

    if not new:
        return ["empty_content: proposed card body is empty"]

    issues = _legacy_issues(new, old, proposed, plan)
    issues.extend(_card_ref_issues(new, pulled.card_id or ""))
    issues.extend(_upstream_issues(new, style_block, pulled.card_id or ""))
    return issues


def _legacy_issues(new: str, old: str, proposed: ProposedUpdate,
                   plan: UpdatePlan) -> list[str]:
    """The original no-op / collapse / no-plan heuristics over the body."""
    issues: list[str] = []
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
    return issues


def _card_ref_issues(new: str, card_id: str) -> list[str]:
    """Split embedded /card/ refs into malformed (invalid id) vs foreign-link."""
    issues: list[str] = []
    refs = {m.rstrip(").,;]") for m in _CARD_REF.findall(new)}
    malformed = sorted(r for r in refs if not _VALID_CARD_ID.match(r))
    if malformed:
        issues.append(
            "invalid_card_id: proposed body links malformed Guru card id(s) "
            f"{malformed} — not a well-formed card id"
        )
    foreign = sorted(r for r in refs if _VALID_CARD_ID.match(r) and r != card_id)
    if foreign:
        issues.append(
            "foreign_card_link: proposed body references other Guru card id(s) "
            f"{foreign} — verify these are intentional"
        )
    return issues


def _upstream_issues(new: str, style_block: str, card_id: str) -> list[str]:
    """Surface the shared model-free checks (links/pii/readability/style)."""
    from src.data.enablement_checks import run_checks

    out: list[str] = []
    for res in run_checks(new, style_block=style_block, card_id=card_id):
        if res["status"] in ("fail", "warn"):
            out.append(f"{res['check']}: {res['detail']}")
    return out
