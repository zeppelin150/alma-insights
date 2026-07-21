"""
Signal-retention metric tests for the PHI scrubber (offline, no Bedrock).

This is the SIGNAL axis of the scrub trade-off — a proxy for
classification quality. The headline test asserts that the business /
clinical KEEP-vocabulary (payers, product areas, ICD-like codes, TRC
vocab) survives ``RedactionEngine.scrub`` on realistic Title-Case ticket
text. It FAILS against today's engine because:

  - the ``name_heuristic`` redacts ANY two Title-Case words, so
    "Blue Shield", "Patient Portal", "Direct Deposit" become [NAME];
  - ``RedactionEngine`` loads only ``phi_allowlist.json``, not the richer
    ``payers.json`` / ``product_areas.json`` keep-vocabulary.

After the surgical-scrubber patch (load the two dicts as keep-regions +
tighten the name heuristic) the score recovers toward 1.0. The metric is
how the operator confirms that recovery WITHOUT a Bedrock run.
"""

from __future__ import annotations

import pytest

from src.data.redaction_engine import RedactionEngine
from src.data.redaction_signal import (
    SignalScore,
    corpus_from_conn,
    count_present,
    load_keep_terms,
    signal_retention,
)

# Realistic ticket text in Title Case — the surface the scrubber degrades.
# Mixes true PHI (a name in name-context) with KEEP vocab that must survive:
# payer aliases (Blue Shield, United Healthcare, Kaiser Permanente),
# product areas (Patient Portal, Provider Portal, Prior Authorization,
# Direct Deposit), an ICD code (F41.1), and TRC vocab (TRC-100).
_TITLECASE_CORPUS = (
    "Subject: Prior Authorization denied for patient John Smith.\n"
    "Direct Deposit refund request is not posting. United Healthcare claim.\n"
    "Provider Portal login broken. Blue Shield eligibility verification.\n"
    "Claim Status pending with Kaiser Permanente. Patient Portal access.\n"
    "Diagnosis F41.1 noted. Routed to TRC-100 for billing review."
)


@pytest.fixture
def engine() -> RedactionEngine:
    """Default RedactionEngine (loads shipped config)."""
    return RedactionEngine()


# ── Metric plumbing ───────────────────────────────────────────────────

def test_load_keep_terms_pulls_both_dicts():
    """Keep-vocabulary includes payer aliases AND product-area aliases."""
    terms = load_keep_terms()
    assert "optum" in terms                # payers.json alias
    assert "evernorth" in terms            # payers.json alias
    assert "patient portal" in terms       # product_areas.json alias
    assert "prior authorization" in terms  # product_areas.json alias


def test_count_present_finds_icd_and_trc_tokens():
    """ICD-like codes and TRC vocab are counted as present signal."""
    present = count_present("Diagnosis F41.1 routed to TRC-100.", set())
    assert "f41.1" in present
    assert "trc-100" in present


def test_retention_is_one_when_no_signal(engine):
    """Empty signal corpus => retention 1.0 (nothing to lose)."""
    score = signal_retention(engine, "xyzzy plugh frobozz", terms={"nonsuch"})
    assert isinstance(score, SignalScore)
    assert score.retention == 1.0


# ── Headline guardrail (FAILS on today's engine, for the right reason) ─

def test_signal_retention_high_on_titlecase_tickets(engine):
    """Surgical scrubber must preserve >=95% of clinical/billing vocab.

    FAILS today: Title-Case payer/product phrases are eaten by the
    name_heuristic and the two entity dicts are not loaded as keep-regions.
    The diagnostic message names exactly which keep-terms were lost.
    """
    score = signal_retention(engine, _TITLECASE_CORPUS)
    assert score.present, "fixture must contain keep-vocabulary"
    assert score.retention >= 0.95, (
        f"signal retention {score.retention:.3f} too low; "
        f"scrubber ate keep-terms: {sorted(score.lost)}"
    )


def test_titlecase_payer_and_product_terms_survive(engine):
    """Specific over-redaction regressions: these must NOT become [NAME].

    Pins the exact terms the name_heuristic currently eats so a future
    tuning pass can't silently re-introduce the degradation.
    """
    scrubbed = engine.scrub(_TITLECASE_CORPUS)
    assert "Blue Shield" in scrubbed       # payer alias, currently -> [NAME]
    assert "Patient Portal" in scrubbed    # product area, currently -> [NAME]
    assert "Direct Deposit" in scrubbed    # billing phrase, currently -> [NAME]


# ── Run-on-seeded_db path (offline operator workflow) ─────────────────

def test_signal_retention_runs_on_seeded_db(seeded_db, engine):
    """The metric computes against seeded_db ticket text without a live LLM.

    Demonstrates the operator workflow: pull full_thread from the warehouse,
    score retention offline. seeded_db threads carry little KEEP vocab, so
    we only assert the metric runs and returns a valid score in [0, 1].
    """
    corpus = corpus_from_conn(seeded_db.conn)
    assert corpus, "seeded_db should yield conversation text"
    score = signal_retention(engine, corpus)
    assert 0.0 <= score.retention <= 1.0
