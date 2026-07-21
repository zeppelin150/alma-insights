"""
Alma Insights — Redaction Signal-Retention Metric (offline)

Measures the SECOND axis of the PHI-scrub trade-off: how much of the
business/clinical vocabulary the NLP classifier needs SURVIVES a scrub.

PHI recall is the safety axis (see ``tests/test_redaction_recall.py``).
This module is the *signal* axis. The two pull against each other: a
maximally aggressive scrubber redacts everything (perfect recall, zero
signal); a surgical scrubber removes only true PHI and leaves the
clinical/billing vocabulary intact (high recall AND high signal).

The score here RISES toward 1.0 as the scrubber gets more surgical, and
is computed entirely OFFLINE — no Bedrock / Gemini call. It is a PROXY
for classification quality, not a substitute: the keep-vocabulary it
checks (payers, product areas, ICD-like codes, TRC vocab) is exactly the
signal ``worker_agent._build_prompt`` feeds the classifier as
``full_thread``. Full ground truth is a scrubbed-vs-unscrubbed
classification E2E diff (see this module's docstring footer).

Public API:
    load_keep_terms()           -> set[str]   business/clinical vocab
    count_present(text, terms)  -> set[str]   keep-terms in raw text
    signal_retention(engine, corpus) -> SignalScore
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
_ENTITIES_DIR = _CONFIG_DIR / "entities"
_PAYERS = _ENTITIES_DIR / "payers.json"
_PRODUCT_AREAS = _ENTITIES_DIR / "product_areas.json"

# ICD-10-style code: a letter, two digits, optional dotted sub-code
# (e.g. F41.1, E11.9, Z00). These carry diagnosis signal and must survive.
_ICD_RE = re.compile(r"\b[A-TV-Z]\d{2}(?:\.\d{1,4})?\b")
# TRC vocabulary token (e.g. TRC-100). Classifier groups on these.
_TRC_RE = re.compile(r"\bTRC-\d{2,4}\b", re.IGNORECASE)


@dataclass
class SignalScore:
    """Result of one signal-retention measurement.

    Attributes:
        present: keep-terms found in the raw (pre-scrub) corpus.
        survived: subset of ``present`` still found after scrub().
        lost: ``present`` minus ``survived`` — the degradation.
        retention: ``len(survived) / len(present)`` in [0.0, 1.0];
            1.0 if no keep-terms were present (nothing to lose).
    """

    present: set[str] = field(default_factory=set)
    survived: set[str] = field(default_factory=set)
    lost: set[str] = field(default_factory=set)
    retention: float = 1.0


def _load_alias_terms(path: Path) -> set[str]:
    """Load a ``{canonical: [aliases]}`` entity dict into a lowercase set.

    Both ``payers.json`` and ``product_areas.json`` use this shape. The
    canonical key and every alias becomes a keep-term.
    """
    terms: set[str] = set()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return terms
    for canonical, aliases in data.items():
        terms.add(canonical.lower())
        for alias in aliases:
            terms.add(str(alias).lower())
    return terms


def load_keep_terms() -> set[str]:
    """Build the lowercase set of business/clinical keep-terms.

    Sources: ``payers.json`` (canonical payers + aliases) and
    ``product_areas.json`` (product areas + aliases). ICD codes and TRC
    vocab are matched by regex in ``count_present`` rather than enumerated
    here, since they are open-ended.
    """
    terms = _load_alias_terms(_PAYERS)
    terms |= _load_alias_terms(_PRODUCT_AREAS)
    return terms


def _term_present(term: str, text: str) -> bool:
    """True if ``term`` appears in ``text`` on word boundaries (case-insensitive)."""
    pattern = r"(?<!\w)" + re.escape(term) + r"(?!\w)"
    return re.search(pattern, text, re.IGNORECASE) is not None


def _regex_tokens(text: str) -> set[str]:
    """Extract ICD-like codes and TRC tokens present in ``text`` (lowercased)."""
    found = {m.group(0).lower() for m in _ICD_RE.finditer(text)}
    found |= {m.group(0).lower() for m in _TRC_RE.finditer(text)}
    return found


def count_present(text: str, terms: set[str]) -> set[str]:
    """Return the keep-terms (and ICD/TRC tokens) present in raw ``text``.

    Combines the dictionary keep-terms with regex-matched ICD-like codes
    and TRC vocab so the denominator reflects every signal token the
    classifier could have used.
    """
    present = {t for t in terms if _term_present(t, text)}
    present |= _regex_tokens(text)
    return present


def signal_retention(engine, corpus: str, terms: set[str] | None = None) -> SignalScore:
    """Measure the fraction of keep-terms that survive ``engine.scrub``.

    Args:
        engine: a ``RedactionEngine`` (anything with a ``scrub(str)->str``).
        corpus: raw text to scrub (e.g. concatenated ticket ``full_thread``).
        terms: keep-term set; defaults to ``load_keep_terms()``.

    Returns:
        A ``SignalScore``. ``retention`` rises toward 1.0 as the scrubber
        gets more surgical. Empty/absent signal yields ``retention=1.0``.
    """
    terms = load_keep_terms() if terms is None else terms
    present = count_present(corpus, terms)
    if not present:
        return SignalScore(retention=1.0)

    scrubbed = engine.scrub(corpus)
    survived = count_present(scrubbed, terms)
    survived &= present  # only credit terms that were present pre-scrub
    lost = present - survived
    return SignalScore(
        present=present,
        survived=survived,
        lost=lost,
        retention=len(survived) / len(present),
    )


def corpus_from_conn(conn, limit: int = 200) -> str:
    """Concatenate ticket ``full_thread`` text from a DB connection.

    Pulls up to ``limit`` rows from ``conversations`` (the same text
    ``worker_agent`` feeds the classifier) so the operator can score the
    metric on seeded_db or a live warehouse offline.
    """
    rows = conn.execute(
        "SELECT full_thread FROM conversations "
        "WHERE full_thread IS NOT NULL AND full_thread != '' "
        "LIMIT ?",
        (limit,),
    ).fetchall()
    return "\n".join(r[0] for r in rows if r[0])


# ── How to run (operator notes) ───────────────────────────────────────
#
# Offline signal score after tuning the scrubber (no Bedrock):
#
#   from src.data.redaction_engine import RedactionEngine
#   from src.data.redaction_signal import signal_retention, corpus_from_conn
#   eng = RedactionEngine()
#   text = corpus_from_conn(conn)          # or any sample corpus string
#   print(signal_retention(eng, text).retention)   # rises toward 1.0
#
# Ground-truth confirmation (the full E2E, costs a Bedrock run) — diff the
# classifier on scrubbed vs unscrubbed threads and compare TRC/sub-pattern
# agreement:
#
#   python -m pytest tests/test_pipeline_full.py -k classification -x -v
#   # then compare nlp_ticket_classifications with scrub on vs off.
