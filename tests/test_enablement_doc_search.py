"""Tokenized lexical search over enablement documents — the regression that
locks in the fix for the whole-query-LIKE recall collapse.

Background: two production search paths wrapped the ENTIRE query in a single
``LIKE '%…%'`` (``enablement_store.search_documents`` and ``kb.search``'s
full-text floor), so a natural-language question only matched when its words
appeared as a verbatim contiguous phrase. The Drive search eval measured
0.000 recall@k across every category because of this.

This file folds the INTENT of ``tests/drive_eval/gold.yaml`` (which scores the
live 196-doc synthetic corpus via the manual harness) into a small, committed,
deterministic corpus so the behaviour can never silently regress:

  * natural-language recall — "what is FHIR" must find the FHIR primer
  * near-duplicate disambiguation — the 2026 doc outranks the 2025 one
  * buried needle — CARC 197 explained in a body, not a title
  * acronym / definition — "what is an ERA" finds the EOB-vs-ERA doc
  * EMPTY-RESULT PRECISION — "orthodontics billing setup" and "veterinary
    claims processing" must return NOTHING (a naive OR-of-terms floods here
    via the common words "billing" / "claims")

The empty-result cases already passed under the old whole-query LIKE (it is
ultra-precise: it matched nothing). They are kept as guards so the tokenized
rewrite does not trade recall for a precision regression.

Run: python -m pytest tests/test_enablement_doc_search.py -q
"""

from __future__ import annotations

import pytest

from src.data import enablement_store as S
from src.data.kb.search import kb_search


# ── corpus ────────────────────────────────────────────────────────────
# Compact stand-ins for the gold documents. Bodies carry the discriminating
# vocabulary each query needs; titles mirror the real corpus (apostrophe,
# em-dash, parentheses) so unicode round-trips are exercised too.

_CORPUS: list[tuple[str, str]] = [
    ("Aetna Prior Authorization Requirements 2026",
     "Aetna prior authorization requirements effective for the 2026 plan year. "
     "Lists services requiring prior auth in 2026 including advanced imaging and "
     "specialty drugs. Standard turnaround is fourteen days."),
    ("Aetna Prior Authorization Requirements 2025",
     "Aetna prior authorization requirements effective for the 2025 plan year. "
     "Lists services requiring prior auth in 2025."),
    ("HL7 and FHIR Interoperability Primer",
     "FHIR, Fast Healthcare Interoperability Resources, is an HL7 standard for "
     "exchanging healthcare data electronically. FHIR defines resources and a "
     "RESTful API for interoperability between systems."),
    ("Denial Codes Reference - Authorization and Precertification",
     "Reference of common claim adjustment reason codes. CARC 197: "
     "precertification, authorization, or notification absent — the service "
     "required a prior authorization that was not obtained. CARC 96: "
     "non-covered charge. CARC 16: claim or service lacks information."),
    ("Insurance Eligibility Verification Workflow",
     "How to verify a patient insurance eligibility before the visit. Check the "
     "payer portal, confirm the coverage is active, and record copay and "
     "benefits so the front desk can collect correctly."),
    ("EOB vs ERA - Explanation of Benefits and Electronic Remittance Advice",
     "An EOB, Explanation of Benefits, is the summary sent to the patient. An "
     "ERA, Electronic Remittance Advice, is the electronic 835 transaction sent "
     "to the provider. The difference is the audience and the format."),
    ("Payer's Guide to Clean Claims",
     "A clean claim passes payer edits on first submission with no errors. To "
     "submit a clean claim, verify eligibility, code the encounter correctly, "
     "and include every required field before submission."),
    ("Claim Submission and Clearinghouse Workflow",
     "Claims are submitted to a clearinghouse, which scrubs and forwards them to "
     "the payer. The clearinghouse processes each claim, returns rejections, and "
     "tracks acknowledgements."),
    ("Front Desk Daily Checklist",
     "Verify insurance benefits at check-in. Confirm eligibility, collect the "
     "copay, and update patient demographics before the encounter."),
    ("Payment Posting and Reconciliation Guide",
     "Post insurance and patient payments, reconcile deposits against the bank, "
     "and handle billing adjustments and write-offs during month-end close."),
    ("ICD-10 Coding — Naive Beginner's Guide",
     "An introduction to ICD-10 diagnosis coding for beginners. Explains code "
     "structure, specificity, and how to look up a diagnosis code."),
    ("Coordination of Benefits (COB) Overview",
     "Coordination of benefits, COB, determines which payer is primary when a "
     "patient has more than one health plan, and how the secondary payer "
     "processes the remaining balance."),
]


@pytest.fixture
def corpus(empty_db):
    """empty_db.conn populated with the compact enablement corpus (all Drive)."""
    conn = empty_db.conn
    for i, (name, body) in enumerate(_CORPUS):
        S.save_document(conn, source="drive", doc_id=f"doc-{i}", name=name,
                        source_ref=f"file-{i}", mime_type="application/vnd.google-apps.document",
                        web_url=f"https://drive/{i}", modified_time="2026-07-01T00:00:00Z",
                        full_text=body)
    return conn


def _names(rows: list[dict]) -> list[str]:
    return [r.get("name") or r.get("title") or "" for r in rows]


# ── natural-language recall (the core regression) ────────────────────

@pytest.mark.parametrize("query,expected", [
    ("what is FHIR", "HL7 and FHIR Interoperability Primer"),
    ("how do I verify a patient's insurance eligibility",
     "Insurance Eligibility Verification Workflow"),
    ("how do I submit a clean claim", "Payer's Guide to Clean Claims"),
    ("what is coordination of benefits", "Coordination of Benefits (COB) Overview"),
    ("icd-10 coding for beginners", "ICD-10 Coding — Naive Beginner's Guide"),
])
def test_natural_language_query_finds_doc(corpus, query, expected):
    """A whole-sentence question must surface the right document. Under the old
    whole-query LIKE these all returned []."""
    hits = _names(S.search_documents(corpus, query, limit=10))
    assert expected in hits, f"{query!r} -> {hits}"


def test_natural_language_query_ranks_doc_first(corpus):
    """Not just present — the canonical answer should rank at the top."""
    hits = _names(S.search_documents(corpus, "what is FHIR", limit=10))
    assert hits and hits[0] == "HL7 and FHIR Interoperability Primer"


# ── near-duplicate disambiguation ────────────────────────────────────

def test_near_duplicate_year_disambiguation(corpus):
    """The 2026 query must rank the 2026 doc above the near-identical 2025 one."""
    hits = _names(S.search_documents(
        corpus, "aetna prior authorization requirements for 2026", limit=10))
    assert hits, "expected results"
    assert hits[0] == "Aetna Prior Authorization Requirements 2026", hits
    # and the 2025 query resolves the other way
    hits25 = _names(S.search_documents(corpus, "aetna prior auth 2025", limit=10))
    assert hits25 and hits25[0] == "Aetna Prior Authorization Requirements 2025", hits25


# ── buried needle (code deep in a body, not a title) ─────────────────

def test_buried_needle_denial_code(corpus):
    hits = _names(S.search_documents(corpus, "what does denial code 197 mean", limit=10))
    assert "Denial Codes Reference - Authorization and Precertification" in hits, hits


# ── acronym / definition ─────────────────────────────────────────────

@pytest.mark.parametrize("query", ["what is an ERA", "difference between EOB and ERA"])
def test_acronym_alias(corpus, query):
    hits = _names(S.search_documents(corpus, query, limit=10))
    assert "EOB vs ERA - Explanation of Benefits and Electronic Remittance Advice" in hits, \
        f"{query!r} -> {hits}"


# ── EMPTY-RESULT PRECISION (the OR-flood guard) ──────────────────────

@pytest.mark.parametrize("query", [
    "orthodontics billing setup",     # matches "billing" in Payment Posting only
    "veterinary claims processing",   # matches "claim"/"processing" in Clearinghouse only
])
def test_empty_result_precision(corpus, query):
    """No such document exists — the distinctive term ('orthodontics',
    'veterinary') is absent from the corpus, so ANY result is a false positive.
    A naive OR-of-terms flunks this by matching the common word."""
    assert S.search_documents(corpus, query, limit=10) == [], query


# ── single-token lookups must still work (no recall regression) ──────

def test_single_token_lookup_by_name_and_body(empty_db):
    conn = empty_db.conn
    S.save_document(conn, source="drive", doc_id="d1", name="SSO Setup.gdoc",
                    full_text="Provider SSO self-serve launches June 24, 2026.")
    assert S.search_documents(conn, "SSO")[0]["doc_id"] == "d1"
    assert S.search_documents(conn, "self-serve")[0]["doc_id"] == "d1"


# ── return shape is preserved for every caller ───────────────────────

def test_return_shape_preserved(corpus):
    rows = S.search_documents(corpus, "clean claim", limit=5)
    assert rows
    for key in ("doc_id", "source", "name", "mime_type", "web_url",
                "modified_time", "text_excerpt", "due_dates_json",
                "card_draft_id", "indexed_at"):
        assert key in rows[0], f"missing {key}"


# ── kb_search full-text floor (the second broken path) ───────────────

def test_kb_floor_natural_language(corpus):
    """With no kb_cards present, kb_search falls entirely to the full-text
    floor. It must now answer a natural-language query."""
    out = kb_search(corpus, "what is FHIR", limit=8)
    assert any(r.get("match") == "fulltext"
               and "FHIR" in (r.get("title") or "") for r in out), out


def test_kb_floor_windowed_snippet_contains_needle(empty_db):
    """The floor's snippet still windows around the actual hit (protects
    test_kb_search_local.py::test_needle_in_deck_recall)."""
    conn = empty_db.conn
    S.save_document(conn, source="drive", name="Aetna product deck",
                    source_ref="deck-9", mime_type="application/pptx",
                    full_text="... slide 37: use denial override code DX-4417 "
                              "for retro eligibility ...")
    out = kb_search(conn, "DX-4417", limit=8)
    assert any(r.get("match") == "fulltext" and "DX-4417" in (r.get("summary") or "")
               for r in out), out


def test_kb_floor_empty_result_precision(corpus):
    """The floor must not flood the empty-result queries either."""
    for q in ("orthodontics billing setup", "veterinary claims processing"):
        out = kb_search(corpus, q, limit=8)
        assert all(r.get("match") != "fulltext" for r in out), (q, out)
