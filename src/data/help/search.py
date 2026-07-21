"""Lexical search over the help corpus — deterministic, no embeddings, no LLM
in the ranking path (owner-locked for the enablement lane).

    score = 0.25·bm25(weighted FTS: title×3, summary×2, features×2, body×1)
          + 0.35·query-term coverage over the whole article
          + 0.30·query-term coverage in the TITLE
          + 0.10·query-term coverage in the summary

bm25 is deliberately the SMALLEST weight. On a contentless FTS5 table over a
small corpus it is erratic — it scored a clearly-relevant article at exactly
0.0 while ranking a passing mention above it — so the blend leans on term
coverage, which is computed here and can be reasoned about.

**No LIKE fallback, by design.** Six existing search paths in this repo wrap
the ENTIRE query in a single ``LIKE '%…%'`` — ``enablement_store.search_documents``,
``search_drafts``, ``kb/search.py``'s fulltext floor, ``claude_tools``,
``fast_path`` and the reports history tab — so a multi-word question only
matches when those words appear contiguously. "How do I reschedule a calendar
task" finds nothing. Help search is the one path users reach with a whole
sentence, so it tokenizes and never falls back to LIKE.

A zero-hit search is not a dead end: it returns the section list so Renn (and
the user) get a next step instead of silence.
"""

from __future__ import annotations

import logging
import re

from src.data.help import store

logger = logging.getLogger("alma.help.search")

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Raw user text (quotes, hyphens, parens) must never reach MATCH syntax, and
# an unbounded term list makes FTS pathologically slow — same cap as kb/search.
_MAX_TERMS = 24
_MIN_TOKEN = 2

# Rows pulled from FTS before the blended rescoring runs. Must comfortably
# exceed the corpus size, or bm25 gets to veto articles the blend would have
# ranked first.
_CANDIDATE_POOL = 500

# Words that carry no signal in a help question and would otherwise dominate
# a short query's coverage score ("how do I ..." matches everything).
_STOP = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "do",
    "does", "for", "from", "get", "how", "i", "if", "in", "into", "is", "it",
    "me", "my", "of", "on", "or", "should", "that", "the", "then", "there",
    "this", "to", "use", "want", "was", "what", "when", "where", "which",
    "why", "will", "with", "you", "your",
    # Negation carries no retrieval signal but matches almost every article
    # (help copy is full of "not"), so it dilutes coverage badly.
    "not", "no", "cannot",
}


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def _singular(tok: str) -> str:
    """Cheap plural fold so 'cards' matches 'card'. Pure code — no stemmer."""
    if len(tok) > 3 and tok.endswith("ies"):
        return tok[:-3] + "y"
    if len(tok) > 3 and tok.endswith("es") and not tok.endswith("ses"):
        return tok[:-2]
    if len(tok) > 3 and tok.endswith("s"):
        return tok[:-1]
    return tok


def _query_terms(query: str) -> list[str]:
    """Signal-bearing, de-duplicated, singularized tokens from the raw query.

    Stop words are dropped only when something survives — a query that is
    ENTIRELY stop words ("what is this") still searches rather than matching
    every article at once.
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


def _match_expr(terms: list[str]) -> str:
    """An OR of quoted prefix terms. Quoting neutralizes FTS operators, so no
    user input can inject MATCH syntax."""
    return " OR ".join(f'"{t}"*' for t in terms)


def _hits(term: str, haystack: set[str]) -> bool:
    """Does ``term`` match any token, using the SAME prefix rule as the FTS
    query?

    This must stay consistent with ``_match_expr``. When the scoring used
    exact equality while MATCH used prefixes, the ranking silently discarded
    what retrieval had found: "publish" matched the article titled
    "Publishing to Drive is not wired yet" in FTS, then scored zero coverage
    and zero title-hit against it, and the right answer fell out of the top
    five.
    """
    if term in haystack:
        return True
    return any(tok.startswith(term) for tok in haystack)


def search_help(conn, query: str, *, section: str | None = None,
                limit: int = 5) -> list[dict]:
    """Rank help articles against a natural-language query.

    Returns article dicts (including ``status``, so a caller can say "that is
    behind a flag that is off" rather than describing a feature that does not
    run) plus ``score`` and ``excerpt``.
    """
    terms = _query_terms(query)
    if not terms:
        return []
    try:
        rows = conn.execute(
            """SELECT a.article_id, a.title, a.section, a.section_title,
                      a.status, a.summary, a.body, a.features_json,
                      bm25(help_articles_fts, 3.0, 2.0, 1.0, 2.0) AS rank
               FROM help_articles_fts f
               JOIN help_articles a ON a.rowid = f.rowid
               WHERE help_articles_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            # Generous candidate pool. bm25 alone is a poor ranker here — it
            # over-rewards a long body that mentions a common word like
            # "drive" — so the blend below does the real work. A pool of
            # limit*4 silently truncated the right answer before rescoring
            # could rescue it. A help corpus is bounded (tens of articles),
            # so fetching a wide pool costs nothing.
            (_match_expr(terms), _CANDIDATE_POOL),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 — a bad query must not raise at the user
        logger.debug("help search failed for %r: %s", query, exc)
        return []

    scored = []
    for row in rows:
        d = dict(row)
        if section and d.get("section") != section:
            continue
        # bm25 returns negative numbers, better = more negative. Map onto
        # 0..1 so the blend below is readable.
        raw = float(d.pop("rank", 0.0) or 0.0)
        bm = 1.0 / (1.0 + max(0.0, -raw))
        bm = 1.0 - bm                      # more negative rank → higher score

        haystack = set(_singular(t) for t in _tokens(
            f"{d.get('title','')} {d.get('summary','')} {d.get('body','')} "
            f"{d.get('features_json','')}"))
        coverage = sum(1 for t in terms if _hits(t, haystack)) / len(terms)

        # How MANY of the query's terms the title carries, not merely whether
        # one does. This is the signal that separates "Publishing to Drive is
        # not wired yet" (2 of 3) from "Connecting Google Drive" (1 of 3) for
        # the query "why is drive publish not working" — a binary title hit
        # scored both identically.
        title_toks = set(_singular(t) for t in _tokens(d.get("title", "")))
        title_cov = sum(1 for t in terms if _hits(t, title_toks)) / len(terms)

        summary_toks = set(_singular(t) for t in _tokens(d.get("summary", "")))
        summary_cov = sum(1 for t in terms
                          if _hits(t, summary_toks)) / len(terms)

        d["score"] = round(
            0.25 * bm + 0.35 * coverage + 0.30 * title_cov + 0.10 * summary_cov,
            4)
        d["excerpt"] = _excerpt(d.get("body", ""), terms)
        d["banner"] = store.status_banner(d.get("status", ""))
        d.pop("body", None)
        scored.append(d)

    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored[:max(1, int(limit))]


def _excerpt(body: str, terms: list[str], width: int = 220) -> str:
    """A snippet around the first matching term, so a result is readable
    without opening the article."""
    if not body:
        return ""
    low = body.lower()
    pos = -1
    for term in terms:
        pos = low.find(term)
        if pos >= 0:
            break
    if pos < 0:
        return body[:width].strip()
    start = max(0, pos - width // 3)
    snippet = body[start:start + width].strip()
    return ("…" if start > 0 else "") + snippet + ("…" if
                                                   start + width < len(body)
                                                   else "")
