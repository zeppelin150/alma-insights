"""KB hybrid search — deterministic ranking, no embeddings, no LLM in the
ranking path (WS2-M6, renn-calendar-kb-studio plan; owner-locked: NO embedding
models on the enablement lane).

score = 0.55·bm25(weighted FTS: title×3, topics×2, key_facts×2)
      + 0.25·query-term coverage
      + 0.10·title-hit bonus
      + 0.10·recency (source_modified decay)

Query expansion is PURE CODE: payer/product-area alias dictionaries
(config/entities — name dictionaries, PHI-free; phi_allowlist.json is
excluded) plus plural/hyphen normalization. Haiku's whole job shrinks to
choosing among well-ranked, snippet-quoted results — the M9 lesson.

FTS MATCH input is sanitized here: raw user text (quotes, hyphens, parens)
must never reach MATCH syntax.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from src.data.kb import store

logger = logging.getLogger("alma.kb.search")

_ENTITIES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "config" / "entities"
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_alias_map: dict | None = None


def _aliases() -> dict:
    """alias-token → canonical-name tokens, built once from the entity dicts."""
    global _alias_map
    if _alias_map is not None:
        return _alias_map
    _alias_map = {}
    for fname in ("payers.json", "product_areas.json"):
        try:
            data = json.loads((_ENTITIES_DIR / fname).read_text("utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict):
            for canonical, aliases in data.items():
                names = [canonical] + (aliases if isinstance(aliases, list) else [])
                group = {n.lower() for n in names if isinstance(n, str)}
                for name in group:
                    _alias_map.setdefault(name, set()).update(group)
    return _alias_map


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def _singular(tok: str) -> str:
    if len(tok) > 3 and tok.endswith("ies"):
        return tok[:-3] + "y"
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    return tok


def expand_query(query: str) -> list[str]:
    """Query → deduped term list (tokens + singulars + entity aliases)."""
    terms: list[str] = []
    seen: set = set()

    def _add(t: str):
        t = t.strip().lower()
        if t and t not in seen:
            seen.add(t)
            terms.append(t)

    lowered = (query or "").lower()
    for tok in _tokens(query):
        _add(tok)
        _add(_singular(tok))
    for alias, group in _aliases().items():
        if alias in lowered:
            for name in group:
                for tok in _tokens(name):
                    _add(tok)
    return terms


def _match_expr(terms: list[str]) -> str:
    """Sanitized FTS5 MATCH expression: quoted tokens OR'd together."""
    safe = [t for t in terms if _TOKEN_RE.fullmatch(t)]
    if not safe:
        return ""
    return " OR ".join(f'"{t}"' for t in safe[:24])


def kb_search(conn, query: str, *, topics: list | None = None,
              type: str | None = None, limit: int = 8) -> list[dict]:
    """Ranked hybrid search over the mirror. Falls through to the FULL-TEXT
    floor (enablement_documents) when card hits are thin — the pre-mortem
    recall gate: a fact a summary omitted must still surface."""
    terms = expand_query(query)
    expr = _match_expr(terms)
    hits: list[dict] = []
    if expr:
        try:
            hits = store.fts_search(conn, expr, limit=max(limit * 3, 24))
        except Exception as exc:  # noqa: BLE001 — a MATCH edge case must not 500
            logger.debug("kb fts failed (%s): %s", expr, exc)
    if topics:
        wanted = {str(t).lower() for t in topics}
        filtered = [h for h in hits
                    if wanted & {str(x).lower() for x in (h.get("topics") or [])}]
        hits = filtered or hits          # unmatched topic filter → ignore it (steer)
    if type:
        hits = [h for h in hits if h.get("type") == type]

    term_set = set(terms)
    ranked = []
    if hits:
        raw = [-(h.get("rank") or 0.0) for h in hits]      # bm25: lower=better
        lo, hi = min(raw), max(raw)
        for h, r in zip(hits, raw):
            bm25_norm = (r - lo) / (hi - lo) if hi > lo else 1.0
            haystack = " ".join([h.get("title") or "", h.get("summary") or "",
                                 " ".join(h.get("key_facts") or []),
                                 h.get("body_md") or ""]).lower()
            covered = sum(1 for t in term_set if t in haystack)
            coverage = covered / len(term_set) if term_set else 0.0
            title_hit = 1.0 if any(t in (h.get("title") or "").lower()
                                   for t in term_set) else 0.0
            recency = 1.0 if (h.get("source_modified") or "") >= "2026" else 0.5
            score = 0.55 * bm25_norm + 0.25 * coverage + 0.10 * title_hit + 0.10 * recency
            ranked.append((score, h))
        ranked.sort(key=lambda x: -x[0])

    out = [{"card_id": h["card_id"], "title": h.get("title"),
            "type": h.get("type"), "topics": h.get("topics") or [],
            "summary": h.get("summary") or "", "score": round(s, 3),
            "source_url": h.get("source_url") or "", "match": "card"}
           for s, h in ranked[:limit]]

    # Full-text floor: when card hits are thin, a TOKENIZED search over the
    # stored full text with a WINDOWED snippet around the actual hit — never
    # retire the recall net (cross-cutting acceptance gate: the slide-37 fact a
    # summary omitted must still surface, quoted). The floor used to wrap the
    # whole query in one LIKE, so a natural-language question found nothing
    # (0.000 recall in the Drive eval); it now ranks over enablement_documents_fts
    # via the shared enablement ranker, which also keeps empty-result queries
    # empty (no OR-of-terms flood).
    if len(out) < limit:
        try:
            from src.data.enablement_doc_search import (
                search_documents_ranked, windowed_snippet)
            for d in search_documents_ranked(conn, query, limit=limit - len(out)):
                out.append({"card_id": "", "title": d.get("name"),
                            "type": "fulltext_document", "topics": [],
                            "summary": windowed_snippet(d.get("full_text") or "", query),
                            "score": d.get("score", 0.0), "doc_id": d.get("doc_id"),
                            "source_url": d.get("web_url") or "",
                            "match": "fulltext"})
        except Exception as exc:  # noqa: BLE001
            logger.debug("fulltext floor failed: %s", exc)
    return out
