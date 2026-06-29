"""Fan-out: one source change -> updates to the SET of cards it affects.

Finds candidate cards (catalog-first), then for each compares the source and
stages a draft ONLY when there's a real change. Cards with no change are
reported, never drafted. Each staged draft stays human-gated for publish.
"""

from __future__ import annotations

from .diff import unified
from .find_affected import find_card_candidates
from .identify_updates import identify_updates
from .load_source import load_source
from .models import CardMatch, ContentUpdateRequest, Deps, PulledCard
from .pull_card import fetch_card_markdown, pull_target_card
from .validate import validate_update
from .write_targeted import write_targeted_updates


def run_fanout_update(conn, request: ContentUpdateRequest, deps: Deps,
                      *, max_cards: int = 5) -> dict:
    """Update every card a source change affects; stage one draft per changed card."""
    src = load_source(conn, request, asana_client=deps.asana_client,
                      guru_client=deps.guru_client, http_get=deps.http_get,
                      drive_reader=deps.drive_reader)
    if not src.ok:
        return {"ok": False, "stage": "load_source", "error": src.error, "cards": []}

    query = (request.search_query or request.target_card_name
             or (src.primary.title if src.primary else "")).strip()
    candidates = find_card_candidates(conn, query, guru_client=deps.guru_client,
                                      limit=max_cards, collections=request.collections)
    if not candidates:
        return {"ok": False, "stage": "find_cards", "error": "no_candidate_cards", "cards": []}

    style = _style(conn)
    cards = [_process_card(conn, deps, src, c, style) for c in candidates]
    staged = sum(1 for c in cards if c["status"] == "staged")
    return {"ok": True, "candidates": len(candidates), "staged": staged, "cards": cards}


def _process_card(conn, deps, src, cand, style) -> dict:
    base = {"card_id": cand["card_id"], "title": cand["title"],
            "score": cand["score"], "via": cand.get("via")}
    # Isolate one card's failure from the rest of the fan-out: an LLM/transport
    # error on card N must not abort cards 1..N-1 (already staged) or N+1..end.
    # Without this guard a single raise unwinds the whole batch and the caller
    # returns an error with no `cards` list, orphaning already-staged drafts.
    try:
        return _process_card_inner(conn, deps, src, cand, style, base)
    except Exception as exc:  # noqa: BLE001
        return {**base, "status": "failed", "stage": "exception", "error": str(exc)[:200]}


def _process_card_inner(conn, deps, src, cand, style, base) -> dict:
    fetched = fetch_card_markdown(deps.guru_client, cand["card_id"])
    if not fetched:
        return {**base, "status": "failed", "stage": "fetch"}
    title, current_md = fetched

    # Compare WITHOUT creating a draft first.
    probe = PulledCard(ok=True, draft_id=0, card_id=cand["card_id"],
                       title=title, current_md=current_md)
    plan = identify_updates(deps.llm_client, src, probe)
    if not plan.ok:
        return {**base, "status": "failed", "stage": "identify", "error": plan.error}
    if not plan.changes:
        return {**base, "status": "no_changes", "summary": plan.summary}

    # Real change → now create the linked draft and write the update.
    pulled = pull_target_card(conn, deps.guru_client,
                              CardMatch(ok=True, status="matched",
                                        card_id=cand["card_id"], title=title))
    if not pulled.ok:
        return {**base, "status": "failed", "stage": "pull", "error": pulled.error}
    proposed = write_targeted_updates(deps.llm_client, pulled, plan, src, style)
    if not proposed.ok:
        return {**base, "status": "failed", "stage": "write",
                "error": proposed.error, "draft_id": pulled.draft_id}
    issues = validate_update(pulled, proposed, plan)
    _stage_draft(conn, pulled.draft_id, proposed)
    from . import provenance
    provenance.record_from_result(conn, pulled.draft_id, pulled.card_id, src, plan, issues)
    return {**base, "status": "staged", "draft_id": pulled.draft_id,
            "title": proposed.title, "summary": plan.summary,
            "changes": [{"type": c.type, "section": c.section, "reason": c.reason}
                        for c in plan.changes],
            "issues": issues,
            "diff": unified(current_md, proposed.content_md, title=proposed.title)}


def _style(conn) -> str:
    from src.data.enablement_store import style_guide_block
    try:
        return style_guide_block(conn)
    except Exception:  # noqa: BLE001
        return ""


def _stage_draft(conn, draft_id: int, proposed) -> None:
    from src.data.enablement_store import update_draft_content
    update_draft_content(conn, draft_id, title=proposed.title, content=proposed.content_md)
