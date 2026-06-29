"""Torch-free TF-IDF vectoriser (pure Python, no numpy/torch).

Each catalog summary becomes a sparse TF-IDF vector; similarity is cosine.
Dependency-light so it runs in enablement mode (which avoids torch) and in CI.
The vectoriser is the pluggable piece: a hosted embedding (Bedrock Titan, etc.)
can replace `tfidf_vector` behind the same `cosine` ranking for true semantic
matching when the catalog outgrows lexical retrieval.
"""

from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "the a an and or of to for in on at is are be was were with by from this that "
    "as it its your you we our their not no can will would should may use using how "
    "what when where which who".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall((text or "").lower()) if len(t) >= 2 and t not in _STOP]


def compute_idf(docs_tokens: list[list[str]]) -> dict[str, float]:
    """Smoothed inverse document frequency across the catalog."""
    n = len(docs_tokens) or 1
    df: Counter = Counter()
    for toks in docs_tokens:
        for t in set(toks):
            df[t] += 1
    return {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()}


def tfidf_vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    if not tokens:
        return {}
    tf = Counter(tokens)
    inv = 1.0 / len(tokens)
    vec = {t: (c * inv) * idf.get(t, 0.0) for t, c in tf.items()}
    return {t: v for t, v in vec.items() if v}


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    dot = sum(v * b.get(k, 0.0) for k, v in a.items())
    if dot == 0.0:
        return 0.0
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0
