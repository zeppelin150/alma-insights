"""Tokenized lexical search over ``enablement_documents`` — the fix for the
whole-query-``LIKE`` recall collapse the Drive search eval quantified (0.000
recall@k on natural-language queries).

Same family as :mod:`src.data.help.search` and :mod:`src.data.kb.search`:
deterministic, no embeddings, no LLM in the ranking path (owner-locked for the
enablement lane). The blend is adapted from ``help/search.py`` with ONE
addition the Drive eval demanded — IDF-weighted term coverage — so the two
empty-result queries ("orthodontics billing setup", "veterinary claims
processing") return nothing instead of matching on a common word:

    score = 0.20 * bm25(name x3, full_text x1)   # retrieval prior, downweighted
          + 0.45 * idf_weighted_term_coverage    # the real work
          + 0.25 * idf_weighted_name_coverage    # title carries the answer
          + 0.10 * plain_term_coverage           # a small unweighted floor

Why IDF, not plain coverage: an empty-result query's distinctive word
("veterinary") is ABSENT from the corpus, so it is never retrieved and gets the
highest IDF; the only terms a candidate can cover are the common ones ("claims")
whose IDF is near zero. Its weighted coverage (``wcov``) is therefore tiny and
falls below :data:`WCOV_MIN`. A real query covers its distinctive term, so wcov
approaches 1.0 and clears the gate. Plain coverage alone cannot separate the two
— a doc that covers 2 of 3 common words looks identical to a real 2-of-3 hit.
Gating on the wcov RATIO (not the raw blended score) is what makes the cut hold
across corpus sizes.

The document frequencies are computed over the retrieved candidate pool (cheap,
deterministic), which is exactly the set of documents that matched ANY term.

FTS ``MATCH`` input is sanitized here (quoted prefix tokens), so no user text —
quotes, hyphens, parens — can reach MATCH syntax.
"""

from __future__ import annotations

import logging
import math
import re
import sqlite3

logger = logging.getLogger("alma.enablement.search")

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Raw user text must never reach MATCH syntax, and an unbounded term list makes
# FTS pathologically slow — same caps as help/kb search.
_MAX_TERMS = 24
_MIN_TOKEN = 2

# Rows pulled from FTS before the blended rescoring runs. A business Drive
# folder is bounded (hundreds to low thousands of docs); a wide pool keeps bm25
# from vetoing a document the blend would have ranked first.
_CANDIDATE_POOL = 500

# PRECISION GATE. A result is kept only when its IDF-weighted term coverage
# (``wcov``) clears this bar — NOT the blended score. wcov is a 0..1 ratio, so
# unlike an absolute score floor it behaves the same on a 12-doc and a 196-doc
# corpus (bm25/score magnitudes drift with corpus size; a ratio does not). A
# query whose distinctive term is absent from the corpus ("veterinary claims
# processing") can only cover common words, so its wcov is capped low and it is
# rejected; a real query covers its distinctive term and clears the bar.
# Calibrated on tests/drive_eval/gold.yaml (live 196-doc corpus): every found
# relevant doc scored wcov≈1.0, the two empty-result queries topped out at 0.37.
WCOV_MIN = 0.45

# Words that carry no retrieval signal in a natural-language question and would
# otherwise dominate a short query's coverage. Mirrors help/search._STOP.
_STOP = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "do",
    "does", "for", "from", "get", "how", "i", "if", "in", "into", "is", "it",
    "me", "my", "of", "on", "or", "should", "that", "the", "then", "there",
    "this", "to", "use", "want", "was", "what", "when", "where", "which",
    "why", "will", "with", "you", "your",
    "not", "no", "cannot",
    # Pure query filler ("what does X mean") — an absent filler word must not
    # look like an absent distinctive term and depress the precision gate.
    "mean", "means",
}


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def _singular(tok: str) -> str:
    """Cheap plural fold so 'cards' matches 'card'. Pure code — no stemmer.
    Kept identical to help/search._singular."""
    if len(tok) > 3 and tok.endswith("ies"):
        return tok[:-3] + "y"
    if len(tok) > 3 and tok.endswith("es") and not tok.endswith("ses"):
        return tok[:-2]
    if len(tok) > 3 and tok.endswith("s"):
        return tok[:-1]
    return tok


def _query_terms(query: str) -> list[str]:
    """Signal-bearing, de-duplicated, singularized tokens from the raw query.

    Stop words are dropped only when something survives, so a query that is
    ENTIRELY stop words ("what is this") still searches instead of matching
    everything at once.
    """
    toks = [t for t in _tokens(query) if len(t) >= _MIN_TOKEN]
    kept = [t for t in toks if t not in _STOP] or toks
    out, seen = [], set()
    for tok in kept:
        s = _singular(tok)
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out[:_MAX_TERMS]


def _hits(term: str, haystack: set[str]) -> bool:
    """Does ``term`` match any token, using the SAME prefix rule as the FTS
    query? Must stay consistent with :func:`_match_expr`."""
    if term in haystack:
        return True
    return any(tok.startswith(term) for tok in haystack)


def _match_expr(terms: list[str]) -> str:
    """An OR of quoted prefix terms. Quoting neutralizes FTS operators, so no
    user input can inject MATCH syntax."""
    return " OR ".join(f'"{t}"*' for t in terms)


def _idf(df: int, n: int) -> float:
    """Smoothed inverse document frequency over the candidate pool. An absent
    term (df == 0) gets the largest weight — that is the precision mechanism."""
    return math.log((n + 1.0) / (df + 0.5))


def windowed_snippet(full_text: str, query: str, *, width: int = 240) -> str:
    """A snippet windowed around the first query term found in ``full_text`` —
    so a full-text hit is readable without opening the document, and the actual
    matched fact (a buried code, a slide-37 number) is what shows."""
    body = full_text or ""
    if not body:
        return ""
    low = body.lower()
    terms = _query_terms(query)
    pos = -1
    for t in terms:
        p = low.find(t)
        if p >= 0 and (pos < 0 or p < pos):
            pos = p
    if pos < 0:
        return body[:width].strip()
    start = max(0, pos - width // 3)
    snippet = body[start:start + width].strip()
    return ("…" if start > 0 else "") + snippet + ("…" if start + width < len(body) else "")


def search_documents_ranked(
    conn: sqlite3.Connection, query: str, *, limit: int = 20,
    source: str | None = None,
) -> list[dict]:
    """Rank ``enablement_documents`` against a natural-language query.

    Returns full document rows (every column) plus a ``score``, best first, with
    everything below the :data:`WCOV_MIN` coverage gate dropped. ``[]`` when
    nothing is relevant — a zero-hit search is honest, not a bug. ``source``
    optionally restricts to a
    single origin ('drive' | 'upload' | 'manual').
    """
    terms = _query_terms(query)
    if not terms:
        return []
    expr = _match_expr(terms)
    sql = ("SELECT d.*, bm25(enablement_documents_fts, 3.0, 1.0) AS _rank "
           "FROM enablement_documents_fts f "
           "JOIN enablement_documents d ON d.rowid = f.rowid "
           "WHERE enablement_documents_fts MATCH ?")
    params: list = [expr]
    if source:
        sql += " AND d.source = ?"
        params.append(source)
    sql += " ORDER BY _rank LIMIT ?"
    params.append(_CANDIDATE_POOL)
    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception as exc:  # noqa: BLE001 — a MATCH edge case must not 500
        logger.debug("enablement fts failed for %r (%s): %s", query, expr, exc)
        return []
    if not rows:
        return []

    cands = [dict(r) for r in rows]
    name_toks: list[set[str]] = []
    all_toks: list[set[str]] = []
    for c in cands:
        nt = {_singular(t) for t in _tokens(c.get("name", ""))}
        bt = {_singular(t) for t in _tokens(c.get("full_text", ""))}
        name_toks.append(nt)
        all_toks.append(nt | bt)

    n = len(cands)
    idf = {t: _idf(sum(1 for toks in all_toks if _hits(t, toks)), n) for t in terms}
    idf_total = sum(idf.values()) or 1.0

    raw = [-(c.get("_rank") or 0.0) for c in cands]   # bm25: lower = better
    lo, hi = min(raw), max(raw)

    scored: list[tuple[float, int, dict]] = []
    for i, c in enumerate(cands):
        bm = (raw[i] - lo) / (hi - lo) if hi > lo else 1.0
        covered = [t for t in terms if _hits(t, all_toks[i])]
        wcov = sum(idf[t] for t in covered) / idf_total
        name_cov = sum(idf[t] for t in terms if _hits(t, name_toks[i])) / idf_total
        plain = len(covered) / len(terms)
        score = 0.20 * bm + 0.45 * wcov + 0.25 * name_cov + 0.10 * plain
        c.pop("_rank", None)
        c["score"] = round(score, 4)
        scored.append((score, wcov, i, c))

    # Ranked by the blended score (best first, ties broken by bm25 order), but
    # GATED on wcov — the corpus-size-robust precision cut (see WCOV_MIN).
    scored.sort(key=lambda x: (-x[0], x[2]))
    return [c for s, wcov, _i, c in scored if wcov >= WCOV_MIN][:max(1, int(limit))]
