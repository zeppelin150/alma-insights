"""Gather per-card health signals — fully decoupled from the warehouse.

``gather_signals`` composes one tiny helper per signal into a list of
``CardSignals``. Every input is Guru-native (via ``GuruSignals``) or
enablement-local (the ``content_catalog`` table). The PHI boundary is absolute:
no ticket / RCM / warehouse table is read here.

Signals:
  * FRESHNESS  — verification_state + days_overdue from the verification queue.
  * DEMAND     — per-card view counts from the analytics event stream.
  * COMMENTS   — open-comment count per card.
  * SOURCE-CHG — a catalog *doc* on the card's topic is newer than the card.
  * DUPLICATE  — a near-duplicate card by summary cosine.
  * GAP        — a catalog doc/topic with no covering card.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.data.content_catalog import all_entries
from src.data.content_catalog.vectorizer import compute_idf, cosine, tfidf_vector, tokenize

from .models import CardSignals

_DUP_THRESHOLD = 0.82   # summary-cosine above which two cards are near-duplicates
_MATCH_THRESHOLD = 0.30  # card↔doc cosine that counts as "same topic" for source-change
_GAP_THRESHOLD = 0.18   # best card-cosine below which a doc is uncovered (a gap)


def _parse_dt(value: str) -> datetime | None:
    """Tolerant ISO parse (Guru mixes '+0000', 'Z' and millis forms)."""
    if not value:
        return None
    v = value.strip().replace("Z", "+00:00")
    if len(v) >= 5 and (v[-5] in "+-") and v[-3] != ":":
        v = v[:-2] + ":" + v[-2:]
    for candidate in (v, v[:19]):
        try:
            dt = datetime.fromisoformat(candidate)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _days_overdue(next_verification_date: str, now: datetime) -> float:
    """Days the card is past its next-verification date (0 if not yet due)."""
    due = _parse_dt(next_verification_date)
    if due is None:
        return 0.0
    overdue = (now - due).total_seconds() / 86400.0
    return round(overdue, 2) if overdue > 0 else 0.0


def _catalog_split(conn) -> tuple[list, list]:
    """Catalog entries partitioned into (cards, docs)."""
    cards, docs = [], []
    for e in all_entries(conn):
        (cards if e.item_type == "card" else docs).append(e)
    return cards, docs


def _summary_vectors(entries) -> tuple[list, dict]:
    """TF-IDF vectors over (title+summary+topics) for every entry, shared IDF."""
    corpus = [tokenize(" ".join([e.title, e.summary, " ".join(e.topics)])) for e in entries]
    idf = compute_idf(corpus) if corpus else {}
    vectors = [tfidf_vector(toks, idf) for toks in corpus]
    return vectors, idf


def _best_cosine(vec, others) -> tuple[int, float]:
    """Index + score of the best-matching vector in ``others`` (skip self by None)."""
    best_i, best_s = -1, 0.0
    for i, ov in enumerate(others):
        if ov is None:
            continue
        s = cosine(vec, ov)
        if s > best_s:
            best_i, best_s = i, s
    return best_i, best_s


def _duplicate_map(cards, vectors) -> dict[str, list[str]]:
    """card_id -> ids of near-duplicate cards (summary cosine >= threshold)."""
    dups: dict[str, list[str]] = {c.item_id: [] for c in cards}
    for i, ci in enumerate(cards):
        for j in range(i + 1, len(cards)):
            if cosine(vectors[i], vectors[j]) >= _DUP_THRESHOLD:
                dups[ci.item_id].append(cards[j].item_id)
                dups[cards[j].item_id].append(ci.item_id)
    return dups


def _source_changed(card, docs, doc_vectors, card_vec) -> bool:
    """A topic-matched catalog doc is newer than the card's last-modified."""
    card_dt = _parse_dt(card.updated_at)
    if card_dt is None:
        return False
    best_i, best_s = _best_cosine(card_vec, doc_vectors)
    if best_i < 0 or best_s < _MATCH_THRESHOLD:
        return False
    doc_dt = _parse_dt(docs[best_i].updated_at)
    return doc_dt is not None and doc_dt > card_dt


def _gap_card_ids(cards, doc_vectors, card_vectors) -> set[str]:
    """Cards that are the nearest (but inadequate) match to an uncovered doc.

    A doc is a *gap* when its best-matching card scores below the coverage
    threshold; the gap is attributed to that nearest card so the per-card
    ``gap`` flag points at the content the card half-covers.
    """
    gaps: set[str] = set()
    for i in range(len(doc_vectors)):
        best_i, best = _best_cosine(doc_vectors[i], card_vectors)
        if best_i >= 0 and best < _GAP_THRESHOLD:
            gaps.add(cards[best_i].item_id)
    return gaps


def _queue_index(queue: list[dict]) -> dict[str, dict]:
    """card_id -> verification-queue row, for O(1) freshness lookup."""
    return {row.get("id", ""): row for row in queue if isinstance(row, dict) and row.get("id")}


def gather_signals(conn, guru_signals, *, card_ids=None, now: datetime | None = None) -> list[CardSignals]:
    """Build a ``CardSignals`` per card from Guru-live + catalog-local inputs.

    When ``card_ids`` is None the verification queue defines the card set.
    ``now`` is injected (defaults to UTC now) so overdue math is testable.
    """
    now = now or datetime.now(timezone.utc)
    queue = _queue_index(guru_signals.verification_queue())
    views = guru_signals.card_view_counts()
    cards, docs = _catalog_split(conn)

    card_vecs, _ = _summary_vectors(cards)
    doc_vecs, _ = _summary_vectors(docs)
    vec_by_card = {c.item_id: card_vecs[i] for i, c in enumerate(cards)}
    card_by_id = {c.item_id: c for c in cards}
    dup_map = _duplicate_map(cards, card_vecs)
    gap_cards = _gap_card_ids(cards, doc_vecs, card_vecs)

    ids = list(card_ids) if card_ids is not None else list(queue.keys())
    return [
        _build_one(cid, queue, views, guru_signals, now,
                   card_by_id, vec_by_card, dup_map, docs, doc_vecs, gap_cards)
        for cid in ids
    ]


def _build_one(card_id, queue, views, guru_signals, now,
               card_by_id, vec_by_card, dup_map, docs, doc_vecs, gap_cards) -> CardSignals:
    """Assemble the ``CardSignals`` record for a single card."""
    q = queue.get(card_id, {})
    card = card_by_id.get(card_id)
    card_vec = vec_by_card.get(card_id, {})
    source_changed = bool(card) and _source_changed(card, docs, doc_vecs, card_vec)
    return CardSignals(
        card_id=card_id,
        title=q.get("title", "") or (card.title if card else ""),
        verification_state=q.get("verification_state", ""),
        next_verification_date=q.get("next_verification_date", ""),
        days_overdue=_days_overdue(q.get("next_verification_date", ""), now),
        last_modified=q.get("last_modified", "") or (card.updated_at if card else ""),
        view_count=views.get(card_id, 0),
        open_comment_count=guru_signals.open_comment_count(card_id),
        source_changed=source_changed,
        duplicate_of=sorted(set(dup_map.get(card_id, []))),
        gap=card_id in gap_cards,
    )
