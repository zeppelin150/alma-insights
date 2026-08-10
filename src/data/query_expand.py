"""Shared lexical query machinery for the enablement search lanes (WS-F2).

Extracted from ``kb/search.py`` so the Guru card search and the KB lane run the
same alias/token expansion, and extended with bounded query-variant generation
plus an IDF-weighted coverage ranker (the cross-referencing pass). Lexical +
agentic only — embeddings are owner-rejected on the enablement lane.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

_ENTITIES_DIR = Path(__file__).resolve().parent.parent.parent / "config" / "entities"
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_alias_map: dict | None = None

# Function words that carry no search signal. Deliberately includes the
# negation family: cards STATE facts ("credentialing status: not active"), so
# "not/no" in the ask must never gate what we search for.
STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "am", "do", "does", "did", "will", "would", "can", "could", "should",
    "may", "might", "must", "have", "has", "had", "of", "in", "on", "at",
    "to", "for", "with", "by", "from", "about", "that", "this", "these",
    "those", "it", "its", "we", "our", "you", "your", "they", "their",
    "and", "or", "but", "if", "then", "than", "as", "so", "such", "any",
    "all", "some", "there", "here", "what", "which", "who", "whom", "how",
    "when", "where", "why", "not", "no", "nor", "currently", "current",
    "please", "show", "find", "pull", "up", "list", "me", "us", "state",
    "states", "stating", "say", "says", "saying",
}


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


def content_terms(query: str) -> list[str]:
    """The query's signal-bearing tokens (stopwords out, singulars folded)."""
    out: list[str] = []
    seen: set = set()
    for tok in _tokens(query):
        if tok in STOPWORDS:
            continue
        s = _singular(tok)
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _matched_entities(query: str) -> list[tuple[str, set]]:
    """(matched alias, its full alias group) pairs found in the query — one
    entry per distinct group, represented by the LONGEST matching alias.
    Longest matters: "bcbs" substring-fires inside "bcbsma", and keeping the
    short form would leave the real entity token in the topic terms."""
    lowered = (query or "").lower()
    by_group: list[tuple[str, set]] = []
    for alias, group in _aliases().items():
        if alias not in lowered:
            continue
        for i, (best, g) in enumerate(by_group):
            if group == g:
                if len(alias) > len(best):
                    by_group[i] = (alias, g)
                break
        else:
            by_group.append((alias, group))
    by_group.sort(key=lambda ag: -len(ag[0]))
    return by_group


_payer_alias_set: set | None = None


def _payer_aliases() -> set:
    """Every lowercase payer name/alias — classifies a matched alias group as
    an ENTITY (payer, swappable) vs a TOPIC (product area, kept in the ask)."""
    global _payer_alias_set
    if _payer_alias_set is None:
        _payer_alias_set = set()
        try:
            data = json.loads((_ENTITIES_DIR / "payers.json").read_text("utf-8"))
            for canonical, aliases in (data or {}).items():
                _payer_alias_set.add(canonical.lower())
                for a in aliases if isinstance(aliases, list) else []:
                    if isinstance(a, str):
                        _payer_alias_set.add(a.lower())
        except Exception:  # noqa: BLE001
            pass
    return _payer_alias_set


def _alt_names(group: set, exclude: str, n: int = 2) -> list[str]:
    # (len, str) key: deterministic across processes — a bare len key leaves
    # ties in set-iteration (hash) order.
    return sorted((g for g in group if g != exclude),
                  key=lambda s: (len(s), s))[:n]


def query_variants(query: str, *, max_variants: int = 5) -> list[str]:
    """Bounded, deterministic search-orientation set for one ask (≤5).

    Order (the literal query is always index 0 — the kwarg stays literal, per
    the owner's design): stopword-stripped key terms; ENTITY reorientations
    (payer-alias swaps that keep the topic terms — "bcbs alma credentialing");
    topic-only (entity dropped); then topic-alias swaps if room remains.
    """
    q = (query or "").strip()
    if not q:
        return []
    out: list[str] = []
    seen: set = set()

    def _add(v: str):
        v = re.sub(r"\s+", " ", v).strip()
        if v and v.lower() not in seen and len(out) < max_variants:
            seen.add(v.lower())
            out.append(v)

    _add(q)
    terms = content_terms(q)
    _add(" ".join(terms))

    payers = _payer_aliases()
    payer_ents: list[tuple[str, set]] = []
    topic_ents: list[tuple[str, set]] = []
    for alias, group in _matched_entities(q):
        (payer_ents if group & payers else topic_ents).append((alias, group))

    ent_toks: set = set()
    for alias, _group in payer_ents:
        ent_toks.update(_singular(t) for t in _tokens(alias))
    topic = [t for t in terms if t not in ent_toks]

    for alias, group in payer_ents[:2]:
        for alt in _alt_names(group, alias):
            _add(" ".join([alt] + topic))
    if topic and topic != terms:
        _add(" ".join(topic))
    for alias, group in topic_ents[:1]:
        matched = {_singular(t) for t in _tokens(alias)}
        base = [t for t in terms if t not in matched]
        for alt in _alt_names(group, alias):
            _add(" ".join([alt] + base))
    return out


def rank_by_coverage(rows: list[dict], query: str, *,
                     text_keys: tuple = ("content",),
                     title_key: str = "title",
                     min_wcov: float = 0.0) -> list[dict]:
    """Rank result dicts by IDF-weighted coverage of the ORIGINAL query's
    content terms (the cross-referencing pass; model: enablement_doc_search's
    wcov ranker). Each row gains ``score``; rows under ``min_wcov`` drop.
    Input order is the tiebreak, so per-source rank survives equal scores.
    """
    terms = content_terms(query)
    if not terms or not rows:
        return list(rows)
    row_toks: list[set] = []
    title_toks: list[set] = []
    for r in rows:
        tt = {_singular(t) for t in _tokens(str(r.get(title_key) or ""))}
        bt = set(tt)
        for k in text_keys:
            bt.update(_singular(t) for t in _tokens(str(r.get(k) or "")))
        title_toks.append(tt)
        row_toks.append(bt)
    n = len(rows)
    idf = {t: math.log((n + 1.0) / (sum(1 for toks in row_toks if t in toks) + 0.5))
           for t in terms}
    idf_total = sum(idf.values()) or 1.0
    scored = []
    for i, r in enumerate(rows):
        wcov = sum(idf[t] for t in terms if t in row_toks[i]) / idf_total
        title_cov = sum(idf[t] for t in terms if t in title_toks[i]) / idf_total
        plain = sum(1 for t in terms if t in row_toks[i]) / len(terms)
        score = 0.55 * wcov + 0.30 * title_cov + 0.15 * plain
        r = dict(r)
        r["score"] = round(score, 4)
        scored.append((score, wcov, i, r))
    scored.sort(key=lambda x: (-x[0], x[2]))
    return [r for s, w, _i, r in scored if w >= min_wcov]
