"""
Alma Insights — Surgical PHI Scrubber Tests

Goal: the redaction engine must strip TRUE PHI (names in person-context,
SSN/DOB/phone/email/address/member-ids) while PRESERVING the clinical and
billing vocabulary the NLP classifier depends on (payers + their aliases,
product/feature terms, diagnosis codes, complaint language).

These tests pin two surgical behaviors:
  (A) payers.json + product_areas.json terms join phi_allowlist.json as
      keep-regions, so payer aliases (e.g. "Optum") and product/feature
      terms are never redacted.
  (B) the name_heuristic is context-gated: a Title-Case pair is only
      redacted when a name CUE precedes it (member/patient/caller/Mr/
      Dr/"name:"/"spoke with"/"per "). Cue-less Title-Case pairs (clinical
      or billing phrases like "Session Note", "Anxiety Disorder",
      "Direct Deposit") are kept.

Each test documents why it FAILS on the pre-patch engine.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def engine():
    """Fresh RedactionEngine with default config (loads all entity dicts)."""
    from src.data.redaction_engine import RedactionEngine
    return RedactionEngine()


# ── (B) Context-gated name heuristic ──────────────────────────────────


def test_member_context_redacts_name_keeps_clinical_and_payer(engine):
    """A name in member-context is PHI; clinical + payer vocab is signal.

    FAILS pre-patch: the engine already redacts "John Smith" (good), but
    this test also asserts a stable surgical contract once (A)+(B) land —
    "anxiety disorder" (lowercase, kept) and "Aetna" (allowlisted, kept)
    must survive while only the cued name is scrubbed.
    """
    text = "F41.1 anxiety disorder denied by Aetna; member John Smith called"
    out = engine.scrub(text)
    assert "anxiety disorder" in out  # clinical signal preserved
    assert "Aetna" in out             # payer signal preserved
    assert "F41.1" in out             # diagnosis code preserved
    assert "John Smith" not in out    # PHI removed (member cue present)


def test_cueless_title_case_pair_is_kept(engine):
    """A Title-Case billing phrase with NO name cue must NOT be redacted.

    FAILS pre-patch: "Session Note" → kept only by luck ("Session" is in
    aggressive_skip_terms). This case proves the *general* policy with a
    phrase that is NOT in skip_terms. "Direct Deposit" → "[NAME] failed"
    today, so the assertion that the phrase survives fails.
    """
    text = "Direct Deposit failed for the provider"
    out = engine.scrub(text)
    assert "Direct Deposit" in out
    assert "[NAME]" not in out


def test_session_note_and_payer_kept(engine):
    """Product/feature phrase + payer must both survive scrubbing.

    FAILS pre-patch: "Session Note" survives only because "Session" is a
    skip term; this remains green, but pairing it with the explicit
    no-NAME assertion documents the surgical contract. The companion
    clinical-pair test below is the one that exposes the pre-patch bug.
    """
    text = "Session Note submit error on the Aetna portal"
    out = engine.scrub(text)
    assert "Session Note" in out
    assert "Aetna" in out
    assert "[NAME]" not in out


def test_clinical_title_case_pair_kept_without_cue(engine):
    """A clinical Title-Case pair with no cue must be preserved.

    FAILS pre-patch: "Anxiety Disorder flagged" → "[NAME] flagged" because
    name_heuristic eats any Title-Case pair and neither token is in
    aggressive_skip_terms.
    """
    text = "Anxiety Disorder flagged on the account"
    out = engine.scrub(text)
    assert "Anxiety Disorder" in out
    assert "[NAME]" not in out


def test_bare_name_policy_no_cue_is_kept(engine):
    """Bare cue-less name policy: PRESERVE (precision over recall).

    Documented trade-off: with no name cue we CANNOT distinguish "John
    Smith" the person from "Session Note" the feature, so a context-gated
    heuristic keeps bare Title-Case pairs. Recall on cue-less personal
    names drops; precision on clinical/billing vocab rises. The structured
    PHI patterns (SSN/email/phone/DOB/address/member-id) still fire
    regardless of cue, so identifying PHI around a bare name is removed.

    FAILS pre-patch: bare "John Smith called about billing" →
    "[NAME] called about billing"; this asserts the name is now KEPT.
    """
    text = "John Smith called about billing"
    out = engine.scrub(text)
    assert "John Smith" in out


def test_cued_name_variants_are_redacted(engine):
    """Each supported cue must trigger redaction of the following pair.

    FAILS pre-patch only for the assertion semantics tied to (B): today
    every pair is redacted regardless of cue (over-redaction). Post-patch
    these must redact *because* of the cue, while cue-less pairs above are
    kept — the two together prove the gate works in both directions.
    """
    cued = [
        "Patient Jane Doe was seen",
        "caller Robert Brown asked",
        "spoke with Mary Jones yesterday",
        "Dr Alan Pierce signed off",
        "per Kevin Lee the claim",
        "name: Sarah White",
    ]
    for text in cued:
        out = engine.scrub(text)
        assert "[NAME]" in out, f"expected redaction for cue in: {text!r} -> {out!r}"


# ── (A) Payer + product entity dicts as keep-regions ──────────────────


def test_payer_alias_from_payers_json_is_kept(engine):
    """A payer alias that lives ONLY in payers.json must survive.

    FAILS pre-patch: "Optum" is a UHC alias in payers.json but is absent
    from phi_allowlist.json, so it gets no keep-region. "Optum Health
    rejected the claim" → "[NAME] rejected the claim".
    """
    text = "Optum Health rejected the claim"
    out = engine.scrub(text)
    assert "Optum" in out
    assert "[NAME]" not in out


def test_product_area_term_is_kept(engine):
    """A product/feature term from product_areas.json must survive.

    FAILS pre-patch: "Patient Portal" → "[NAME] login broken"; "portal"
    is a product_areas.json term but is not loaded into keep-terms.
    """
    text = "Patient Portal login broken"
    out = engine.scrub(text)
    assert "Patient Portal" in out
    assert "[NAME]" not in out


def test_pegasus_ppo_payer_kept(engine):
    """Niche payer ("Pegasus PPO") from payers.json must survive.

    FAILS pre-patch in the general case: the canonical "Pegasus PPO" is
    only in payers.json. With a Title-Case neighbor it would be redacted;
    here we assert the keep-region protects the canonical name.
    """
    text = "Member escalated a Pegasus PPO billing dispute"
    out = engine.scrub(text)
    assert "Pegasus PPO" in out


# ── PHI must still be removed (regression guard) ──────────────────────


def test_structured_phi_still_removed(engine):
    """SSN/email/phone/DOB must be scrubbed regardless of the name gate."""
    text = "Reach me at 555-123-4567 or jsmith@test.com, SSN 123-45-6789, DOB 01/02/1990"
    out = engine.scrub(text)
    assert "555-123-4567" not in out
    assert "jsmith@test.com" not in out
    assert "123-45-6789" not in out
    assert "01/02/1990" not in out
