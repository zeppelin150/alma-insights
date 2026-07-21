"""
PHI-recall guardrail for the scrubber (the SAFETY axis).

Asserts ``RedactionEngine.scrub`` removes EACH class of true PHI from a
seeded text blob: SSN, email, phone, DOB, a member id, a street address,
and a person name in name-context. True PHI carries no classification
signal, so a surgical scrubber must keep recall pinned at 100% even as it
gets gentler on business/clinical vocab (see test_redaction_signal.py).

This test is the guardrail on the redaction patch: future tuning that
loosens the scrubber to recover signal must NOT silently drop any of
these. Each assertion fails loudly with the leaked identifier if it does.
"""

from __future__ import annotations

import pytest

from src.data.redaction_engine import RedactionEngine

# True PHI tokens, one per class. The address and name use enough context
# that even a tightened, surgical scrubber should still catch them.
_SSN = "123-45-6789"
_EMAIL = "jane.doe@example.com"
_PHONE = "415-555-0199"
_DOB = "03/14/1985"
_MEMBER_ID = "MEM-88421003"          # form the member_id pattern catches
_ADDRESS = "742 Evergreen Terrace Dr"
_NAME = "Robert Johnson"             # person name in name-context

# Name appears with an explicit cue ("member <name> called") so the surgical
# context gate is *supposed* to redact it. This is the recall floor: even a
# scrubber tuned for maximal signal must still catch a cued name.
_PHI_BLOB = (
    f"Contact email {_EMAIL}.\n"
    f"Patient SSN {_SSN}, born {_DOB}. Member id {_MEMBER_ID}.\n"
    f"The member {_NAME} called from {_PHONE}. Mailing address: {_ADDRESS}.\n"
    "Re: Prior Authorization denied by Blue Shield, TRC-100."
)


@pytest.fixture
def engine() -> RedactionEngine:
    return RedactionEngine()


@pytest.fixture
def scrubbed(engine) -> str:
    return engine.scrub(_PHI_BLOB)


# ── Per-class recall assertions ───────────────────────────────────────

def test_ssn_removed(scrubbed):
    assert _SSN not in scrubbed, "SSN leaked through scrub()"
    assert "[SSN]" in scrubbed


def test_email_removed(scrubbed):
    assert _EMAIL not in scrubbed, "email leaked through scrub()"
    assert "[EMAIL]" in scrubbed


def test_phone_removed(scrubbed):
    assert _PHONE not in scrubbed, "phone leaked through scrub()"
    assert "[PHONE]" in scrubbed


def test_dob_removed(scrubbed):
    assert _DOB not in scrubbed, "DOB leaked through scrub()"
    assert "[DOB]" in scrubbed


def test_member_id_removed(scrubbed):
    assert _MEMBER_ID not in scrubbed, "member id leaked through scrub()"
    assert "[MEMBER_ID]" in scrubbed


def test_address_removed(scrubbed):
    assert _ADDRESS not in scrubbed, "street address leaked through scrub()"
    assert "[ADDRESS]" in scrubbed


def test_name_in_context_removed(scrubbed):
    """A CUED person name ("member Robert Johnson called") must be redacted.

    Asserts neither token survives. The surgical scrubber's context gate is
    supposed to fire here, but currently mis-segments: "member Robert" pairs
    the cue with the first name token and leaves the surname "Johnson". This
    is the recall guardrail catching that regression — fails for the right
    reason (real PHI leak), not an import/fixture error.
    """
    assert _NAME not in scrubbed, "full name leaked through scrub()"
    assert "Robert" not in scrubbed, "name token 'Robert' leaked"
    assert "Johnson" not in scrubbed, "name token 'Johnson' leaked"


def test_all_phi_classes_in_one_pass(scrubbed):
    """One blob, every class redacted — the consolidated guardrail."""
    leaked = [
        tok for tok in (_SSN, _EMAIL, _PHONE, _DOB, _MEMBER_ID, _ADDRESS, _NAME)
        if tok in scrubbed
    ]
    assert not leaked, f"PHI leaked through scrub(): {leaked}"


# ── Recall must not cost signal in the same blob ──────────────────────

# ── Known residual recall gaps from the surgical patch (handoff to-do) ─
# These xfails pin TWO real PHI leaks the surgical name-cue gate introduced.
# They are strict: when work-Claude fixes _has_name_cue / the title-pairing,
# the test XPASSes and CI forces removal of the marker. Until then the suite
# stays green while the gap stays visible.

def test_from_header_name_redacted(engine):
    """'From: Robert Johnson' must redact the name.

    Was a strict xfail: _has_name_cue tokenized 'From: ' to [..., 'from', ':']
    so the LAST token was ':' not 'from' and the cue was missed — the whole
    name leaked. Fixed by treating a trailing colon as transparent so the real
    cue word is checked.
    """
    out = engine.scrub("From: Robert Johnson replied to the ticket.")
    assert "Robert" not in out and "Johnson" not in out, out


def test_title_first_last_fully_redacted(engine):
    """'Patient Robert Johnson' must redact BOTH names, not just the first.

    Was a strict xfail: the title 'Patient' greedily paired with 'Robert'
    (-> [NAME]) leaving the surname 'Johnson' un-redacted. Fixed by a
    second-pass that redacts a Title-Case surname trailing a title-paired
    [NAME] match.
    """
    out = engine.scrub("Patient Robert Johnson called today.")
    assert "Johnson" not in out, out


def test_keep_vocab_survives_alongside_phi(scrubbed):
    """The guardrail blob also proves recall doesn't nuke nearby signal.

    TRC-100 is plain keep-vocab and must survive even in a PHI-dense blob.
    (Blue Shield is intentionally NOT asserted here — it is currently eaten
    by the name_heuristic; that regression is pinned in test_redaction_signal.)
    """
    assert "TRC-100" in scrubbed
