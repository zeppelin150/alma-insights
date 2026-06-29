"""Find the SET of cards a source change plausibly affects.

Catalog-first: when the content catalog holds card summaries, rank by CONTENT
(disambiguates look-alike titles and surfaces every related card). Falls back to
live Guru title search when the catalog has no cards yet.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

_MIN_SCORE = 0.12


def find_card_candidates(conn, query: str, *, guru_client: Any | None = None,
                         limit: int = 5, min_score: float = _MIN_SCORE,
                         collections: list[str] | None = None) -> list[dict]:
    """Return ranked candidate cards [{card_id, title, score, via}] for a topic."""
    cands = _from_catalog(conn, query, limit=limit, min_score=min_score)
    if cands:
        return cands
    return _from_guru(guru_client, query, limit=limit, collections=collections)


def _from_catalog(conn, query: str, *, limit: int, min_score: float) -> list[dict]:
    from src.data.content_catalog import all_entries
    from src.data.content_catalog.search import rank_entries
    cards = [e for e in all_entries(conn) if e.item_type == "card"]
    if not cards:
        return []
    out = []
    for r in rank_entries(query or "", cards, limit=limit):
        if r.score < min_score:
            continue
        out.append({"card_id": r.entry.item_id.split("card:", 1)[-1],
                    "title": r.entry.title, "score": r.score, "via": "catalog"})
    return out


def _from_guru(guru_client, query: str, *, limit: int, collections) -> list[dict]:
    if guru_client is None:
        return []
    try:
        cards = guru_client.search_cards(query or "")
    except Exception:  # noqa: BLE001 — search failure → no candidates
        return []
    allowed = {c.lower() for c in (collections or [])}
    out = []
    for c in cards or []:
        if not c.get("id"):
            continue
        coll = (c.get("collection", "") or "").lower()
        coll_id = (c.get("collection_id", "") or "").lower()
        if allowed and coll not in allowed and coll_id not in allowed:
            continue
        score = SequenceMatcher(None, (query or "").lower(),
                                (c.get("title", "") or "").lower()).ratio()
        out.append({"card_id": c["id"], "title": c.get("title", ""),
                    "score": round(score, 3), "via": "guru"})
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:limit]
