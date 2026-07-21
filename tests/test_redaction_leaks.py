"""
Alma Insights — PHI recall-leak closures + over-redaction guards.

Pins the three residual recall leaks the surgical name-cue patch left open
and proves the fixes do NOT re-nuke clinical / billing / payer vocabulary:

  (a) colon-cued headers ("From:" / "Attn:" / "Name:") must redact the name;
  (b) a title-paired name must redact BOTH tokens (surname must not leak);
  (c) MRN and separated member-id formats must be caught by structured PHI.

Each leak test FAILS for the right reason on the pre-fix engine (a real PHI
token survives), not an import/fixture error. The over-redaction guards prove
recall does not cost classification signal.
"""

from __future__ import annotations

import pytest

from src.data.redaction_engine import RedactionEngine


@pytest.fixture
def engine() -> RedactionEngine:
    """Fresh RedactionEngine with shipped config."""
    return RedactionEngine()


# ── (a) Colon-cued name headers ───────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "From: Robert Johnson replied to the ticket.",
        "Attn: Robert Johnson please review.",
        "Name: Robert Johnson on the account.",
    ],
)
def test_colon_cued_header_redacts_name(engine, text):
    """A name after a 'cue:' header must be redacted, colon notwithstanding.

    Pre-fix: the trailing ':' was the last token, defeating the single-word
    cue check, so 'From: Robert Johnson' leaked entirely.
    """
    out = engine.scrub(text)
    assert "Robert" not in out, out
    assert "Johnson" not in out, out
    assert "[NAME]" in out, out


# ── (b) Title-paired surname must not leak ────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "Patient Robert Johnson called today.",
        "Dr Alan Pierce signed the note.",
        "member Mary Jane Watson escalated.",
    ],
)
def test_title_paired_surname_redacted(engine, text):
    """A title-paired name must redact the trailing surname too.

    Pre-fix: 'Patient' greedily paired with the FIRST name token, so
    '[NAME] Johnson' leaked the surname.
    """
    out = engine.scrub(text)
    assert "Johnson" not in out, out
    assert "Pierce" not in out, out
    assert "Watson" not in out, out


# ── (c) MRN + separated member-id formats ─────────────────────────────


@pytest.mark.parametrize(
    "text,leaked",
    [
        ("MRN12345678 documented.", "12345678"),
        ("MRN-12345678 on file.", "12345678"),
        ("MRN: 12345678 noted.", "12345678"),
        ("Member ID: 99887766 escalated.", "99887766"),
        ("Member ID 99887766 escalated.", "99887766"),
    ],
)
def test_mrn_and_separated_member_id_removed(engine, text, leaked):
    """MRN / separated member-id formats must be scrubbed.

    Pre-fix: the member_id pattern allowed only a single '-'/'#' separator
    and had no MRN keyword, so these identifiers leaked.
    """
    out = engine.scrub(text)
    assert leaked not in out, out
    assert "[MEMBER_ID]" in out, out


# ── Over-redaction guards (recall must not cost signal) ───────────────


def test_clinical_and_billing_vocab_survives(engine):
    """The fixes must NOT re-nuke clinical / billing Title-Case vocab."""
    text = "Direct Deposit failed; Anxiety Disorder flagged on Patient Portal."
    out = engine.scrub(text)
    assert "Direct Deposit" in out, out
    assert "Anxiety Disorder" in out, out
    assert "Patient Portal" in out, out
    assert "[NAME]" not in out, out


def test_payer_vocab_survives(engine):
    """Payer names/aliases must survive the recall fixes."""
    text = "Aetna and Optum denied; routed to Blue Shield."
    out = engine.scrub(text)
    assert "Aetna" in out, out
    assert "Optum" in out, out
    assert "Blue Shield" in out, out


def test_bare_short_member_digits_not_false_positive(engine):
    """A bare 'member <few digits>' must NOT become [MEMBER_ID].

    Guards the member_id broadening: only long (>=4) digit runs after a
    member keyword are identifiers; 'member 12' is plain prose.
    """
    text = "The member 12 was seen and remembered 42 times."
    out = engine.scrub(text)
    assert "[MEMBER_ID]" not in out, out
    assert "member 12" in out, out


def test_word_with_mrn_substring_not_redacted(engine):
    """'mrna' and similar non-identifier words must not trip the MRN rule."""
    text = "The mrna sequence and MRNs were discussed."
    out = engine.scrub(text)
    assert "mrna sequence" in out, out
    assert "[MEMBER_ID]" not in out, out
