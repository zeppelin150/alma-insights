"""Deterministic hybrid search over the content catalog.

Score = weighted TF-IDF cosine (the "vector" signal, over summaries) + lexical
term coverage + a title-hit bonus. Fully deterministic — Renn consumes the
ranked candidates; it never does the searching itself.
"""

from __future__ import annotations

from .models import CatalogEntry, ScoredEntry
from .vectorizer import compute_idf, cosine, tfidf_vector, tokenize

_VECTOR_W = 0.7      # semantic-ish: TF-IDF cosine over title+summary+topics
_LEXICAL_W = 0.3     # fraction of query terms present
_TITLE_BONUS = 0.15  # a query term appears in the (short) title


def _entry_text(e: CatalogEntry) -> str:
    # Title counted twice — it's high-signal; then summary + topics carry the content.
    return " ".join([e.title, e.title, e.summary, " ".join(e.topics)])


def rank_entries(query: str, entries: list[CatalogEntry], *, limit: int = 5) -> list[ScoredEntry]:
    qtoks = tokenize(query)
    if not entries or not qtoks:
        return []
    qset = set(qtoks)
    corpus_tokens = [tokenize(_entry_text(e)) for e in entries]
    idf = compute_idf(corpus_tokens)
    qv = tfidf_vector(qtoks, idf)

    scored: list[ScoredEntry] = []
    for e, toks in zip(entries, corpus_tokens):
        ev = tfidf_vector(toks, idf)
        vscore = cosine(qv, ev)
        tset = set(toks)
        matched = sorted(qset & tset)
        lexical = len(matched) / len(qset)
        title_hit = _TITLE_BONUS if (qset & set(tokenize(e.title))) else 0.0
        score = _VECTOR_W * vscore + _LEXICAL_W * lexical + title_hit
        if score > 0:
            scored.append(ScoredEntry(
                entry=e, score=round(score, 4), vector_score=round(vscore, 4),
                lexical_score=round(lexical, 4), matched_terms=matched))
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored[:limit]


def search_catalog(conn, query: str, *, limit: int = 5) -> list[ScoredEntry]:
    from .store import all_entries
    return rank_entries(query, all_entries(conn), limit=limit)
