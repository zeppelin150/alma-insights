"""
Alma Insights — Compound Term Discovery

Automatically detects statistically significant multi-word terms (bigrams,
trigrams) from conversation text using PMI (Pointwise Mutual Information).

PMI(w1, w2) = log2( P(w1,w2) / (P(w1) * P(w2)) )

High PMI → words strongly associate (e.g., "prior authorization")
Low PMI  → words just happen to be near each other (e.g., "the claim")
"""

from __future__ import annotations

import re
import math
from collections import Counter
from datetime import datetime


# ── Shared stopwords (subset — avoid importing from trending_engine to prevent circular imports) ──
_DISCOVERY_STOPS = {
    "ticket", "zendesk", "please", "thank", "thanks", "hi", "hello",
    "would", "could", "also", "like", "know", "need", "get", "one",
    "us", "see", "let", "want", "going", "sure", "okay", "yes", "no",
    "got", "make", "made", "right", "well", "just", "really", "much",
    "still", "back", "even", "way", "thing", "things", "said", "say",
    "status", "changed", "updated", "assigned", "queue", "priority",
    "automated", "reminder", "survey", "sent", "requester",
    "good", "morning", "afternoon", "evening", "regards", "best",
    "sincerely", "team", "support", "help", "assist", "assistance",
    "reach", "contact", "alma", "email", "send", "received", "dear",
    "call", "called", "day", "time", "able", "told", "take", "give",
    "come", "look", "tell", "work", "use", "try", "ask", "show",
    "think", "feel", "seem", "keep", "put", "mean", "become", "leave",
    "information", "provide", "process", "request", "number", "date",
    "name", "phone", "case", "system", "update",
}

_ROLE_HEADER_RE = re.compile(r'\[.*?\]\s*(CUSTOMER|AGENT|BOT).*?:', re.IGNORECASE)


def _clean_for_discovery(text):
    """Minimal text cleaning for PMI discovery (avoids circular import)."""
    if not text:
        return ""
    text = _ROLE_HEADER_RE.sub(' ', text)
    text = text.replace('---', ' ')
    return text.lower()


def _tokenize_for_discovery(text):
    """Simple tokenization for discovery — letters, digits, hyphens."""
    return re.findall(r'\b[a-z][a-z0-9-]+\b', text)


def discover_compounds(
    conversations: list[dict],
    existing_compounds: set[str] | None = None,
    min_cooccurrence: int = 5,
    min_pmi: float = 3.0,
) -> list[dict]:
    """
    Discover statistically significant multi-word phrases from conversation text.

    Args:
        conversations: List of dicts with "full_thread" key
        existing_compounds: Set of already-known compound phrases to skip
        min_cooccurrence: Minimum times the phrase must appear together
        min_pmi: Minimum PMI score to consider significant

    Returns:
        List of dicts: [{phrase, normalized, frequency, pmi_score}, ...]
    """
    existing_compounds = existing_compounds or set()

    # Tokenize all conversations
    all_tokens = []
    for conv in conversations:
        text = conv.get("full_thread", "")
        cleaned = _clean_for_discovery(text)
        tokens = _tokenize_for_discovery(cleaned)
        # Filter stopwords and short tokens
        tokens = [t for t in tokens if len(t) >= 3 and t not in _DISCOVERY_STOPS]
        all_tokens.extend(tokens)

    if len(all_tokens) < 100:
        return []

    total_tokens = len(all_tokens)

    # Count unigrams
    unigram_counts = Counter(all_tokens)

    # Count bigrams
    bigram_counts = Counter()
    for i in range(len(all_tokens) - 1):
        bigram = (all_tokens[i], all_tokens[i + 1])
        bigram_counts[bigram] += 1

    # Count trigrams
    trigram_counts = Counter()
    for i in range(len(all_tokens) - 2):
        trigram = (all_tokens[i], all_tokens[i + 1], all_tokens[i + 2])
        trigram_counts[trigram] += 1

    candidates = []

    # Score bigrams by PMI
    for (w1, w2), count in bigram_counts.items():
        if count < min_cooccurrence:
            continue

        phrase = f"{w1} {w2}"
        if phrase in existing_compounds:
            continue

        # Skip if either word already contains underscore (already a compound)
        if "_" in w1 or "_" in w2:
            continue

        p_bigram = count / total_tokens
        p_w1 = unigram_counts[w1] / total_tokens
        p_w2 = unigram_counts[w2] / total_tokens

        if p_w1 == 0 or p_w2 == 0:
            continue

        pmi = math.log2(p_bigram / (p_w1 * p_w2))

        if pmi >= min_pmi:
            candidates.append({
                "phrase": phrase,
                "normalized": phrase.replace(" ", "_").replace("-", "_"),
                "frequency": count,
                "pmi_score": round(pmi, 2),
            })

    # Score trigrams by PMI
    for (w1, w2, w3), count in trigram_counts.items():
        if count < min_cooccurrence:
            continue

        phrase = f"{w1} {w2} {w3}"
        if phrase in existing_compounds:
            continue

        if "_" in w1 or "_" in w2 or "_" in w3:
            continue

        p_trigram = count / total_tokens
        p_w1 = unigram_counts[w1] / total_tokens
        p_w2 = unigram_counts[w2] / total_tokens
        p_w3 = unigram_counts[w3] / total_tokens

        if p_w1 == 0 or p_w2 == 0 or p_w3 == 0:
            continue

        pmi = math.log2(p_trigram / (p_w1 * p_w2 * p_w3))

        if pmi >= min_pmi:
            candidates.append({
                "phrase": phrase,
                "normalized": phrase.replace(" ", "_").replace("-", "_"),
                "frequency": count,
                "pmi_score": round(pmi, 2),
            })

    # Sort by PMI descending, cap at 50
    candidates.sort(key=lambda x: -x["pmi_score"])
    return candidates[:50]


def persist_discoveries(db: object, candidates: list[dict]) -> None:
    """
    Save discovered compounds to the database.
    New candidates get status='candidate'. Existing ones get frequency/last_seen updated.
    """
    for c in candidates:
        db.upsert_discovered_compound(
            c["phrase"], c["normalized"], c["frequency"], c["pmi_score"]
        )


def get_active_compounds(db: object) -> dict[str, str]:
    """
    Return merged dict of hardcoded COMPOUND_TERMS + user-approved discovered compounds.

    Returns: {phrase: replacement, ...}
    """
    from src.data.trending_engine import COMPOUND_TERMS

    # Start with hardcoded compounds
    merged = dict(COMPOUND_TERMS)

    # Add approved discovered compounds (override if conflict)
    approved = db.get_discovered_compounds(status="approved")
    for row in approved:
        merged[row["phrase"]] = row["normalized"]

    return merged
