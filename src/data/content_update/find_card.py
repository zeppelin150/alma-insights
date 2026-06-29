"""Stage 2 — find the existing Guru card to update.

Selection order: explicit ``target_card_ref`` (id/URL) > exact ``target_card_name``
> live Guru search (scored by title similarity, scoped to ``collections``). When
no candidate is clearly best, returns ``needs_human_pick`` with the candidate
list so Renn can ask the user which card to update.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from .models import CardCandidate, CardMatch, ContentUpdateRequest, SourceBundle

_STRONG = 0.6     # top similarity needed to auto-accept
_GAP = 0.15       # min lead over the runner-up to auto-accept
_TOP_N = 5


def find_target_card(guru_client: Any, source: SourceBundle,
                     request: ContentUpdateRequest) -> CardMatch:
    """Resolve the card to update, or signal that a human must choose."""
    if request.target_card_ref:
        return _from_ref(guru_client, request.target_card_ref)
    query = (request.target_card_name or request.search_query
             or (source.primary.title if source.primary else "")).strip()
    if not query:
        return CardMatch(ok=False, status="error", error="no_search_query")
    if guru_client is None:
        return CardMatch(ok=False, status="error", error="guru_not_connected")
    candidates = _search(guru_client, query, request.collections)
    return _select(candidates, request.target_card_name)


def _from_ref(guru_client, ref: str) -> CardMatch:
    if guru_client is None:
        return CardMatch(ok=False, status="error", error="guru_not_connected")
    from src.data.enablement_store import parse_guru_card_ref
    card_id = parse_guru_card_ref(ref)
    try:
        card = guru_client.get_card(card_id)
    except Exception as exc:  # noqa: BLE001 — surface as a failed lookup
        return CardMatch(ok=False, status="error", error=f"guru_fetch_failed: {exc}")
    if not card or not card.get("id"):
        return CardMatch(ok=False, status="not_found", error=f"card_not_found: {card_id}")
    return CardMatch(ok=True, status="matched", card_id=card["id"],
                     title=card.get("title", ""))


def _search(guru_client, query: str, collections: list[str]) -> list[CardCandidate]:
    try:
        raw = guru_client.search_cards(query)
    except Exception:  # noqa: BLE001 — treat a search failure as no candidates
        raw = []
    allowed = {c.lower() for c in (collections or [])}
    out: list[CardCandidate] = []
    for card in raw or []:
        if not card.get("id"):
            continue
        if allowed and not _in_collections(card, allowed):
            continue
        title = card.get("title", "")
        score = SequenceMatcher(None, query.lower(), title.lower()).ratio()
        out.append(CardCandidate(card_id=card["id"], title=title,
                                 collection=card.get("collection", ""), score=round(score, 3)))
    out.sort(key=lambda c: c.score, reverse=True)
    return out[:_TOP_N]


def _in_collections(card: dict, allowed: set[str]) -> bool:
    return ((card.get("collection", "") or "").lower() in allowed
            or (card.get("collection_id", "") or "").lower() in allowed)


def _select(candidates: list[CardCandidate], exact_name: str | None) -> CardMatch:
    if not candidates:
        return CardMatch(ok=False, status="not_found", error="no_matching_cards")
    if exact_name:
        target = exact_name.strip().lower()
        for c in candidates:
            if c.title.strip().lower() == target:
                return CardMatch(ok=True, status="matched", card_id=c.card_id,
                                 title=c.title, candidates=candidates)
    top = candidates[0]
    runner = candidates[1].score if len(candidates) > 1 else 0.0
    if top.score >= _STRONG and (top.score - runner) >= _GAP:
        return CardMatch(ok=True, status="matched", card_id=top.card_id,
                         title=top.title, candidates=candidates)
    return CardMatch(ok=False, status="needs_human_pick", candidates=candidates)
