"""
Tests for scripts/deidentify_spreadsheet.py — the surgical, local, no-network
PHI scrubber.

Coverage:
  - Identifier COLUMNS are dropped / hashed (deterministic, stable).
  - Free-text columns are scrubbed: true PHI (SSN/email/phone) removed, but
    clinical/billing vocabulary ("Anxiety Disorder", "Direct Deposit",
    payer names, diagnosis codes) PRESERVED — the surgical guarantee.
  - The WHAT-WAS-REMOVED diff report has correct per-PHI-type counts.
  - Full CSV round-trip via the run() entry point.
"""

import csv
from pathlib import Path

import pytest

from scripts.deidentify_spreadsheet import (
    build_text_engine,
    deidentify,
    hash_value,
    render_report,
    resolve_policy,
    run,
)

DROP = ["first_name", "last_name", "address", "phone", "email"]
HASH = ["member_id"]
TEXT = ["full_thread"]


# ── Synthetic PHI spreadsheet ──────────────────────────────────────────

@pytest.fixture
def phi_csv(tmp_path):
    """A tiny ticket CSV carrying PHI in both structured cols and free text."""
    path = tmp_path / "tickets.csv"
    headers = ["ticket_id", "first_name", "last_name", "phone", "email",
               "member_id", "trc_code", "full_thread"]
    rows = [
        ["T-1", "Jane", "Doe", "555-123-4567", "jane@x.com", "MEM12345",
         "TRC-100",
         "Patient reports Anxiety Disorder. Direct Deposit to UHC failed. "
         "Call 555-999-0000 or email jane@x.com. DOB 01/02/1990."],
        ["T-2", "John", "Roe", "555-222-3333", "john@y.com", "MEM67890",
         "TRC-200",
         "Prior Authorization denied by Blue Cross Blue Shield. SSN 123-45-6789."],
    ]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)
    return path


# ── Unit: the surgical guarantee ───────────────────────────────────────

def test_text_engine_disables_name_heuristic_but_keeps_phi():
    """The free-text engine must NOT touch clinical vocab, but MUST kill PHI.

    This is the heart of the fix: with the default name_heuristic ON,
    "Anxiety Disorder" and "Direct Deposit" become [NAME] — degrading the
    classifier. The surgical engine preserves them while still scrubbing
    SSN/email.
    """
    engine = build_text_engine()
    text = ("Patient reports Anxiety Disorder. Direct Deposit failed. "
            "SSN 123-45-6789, email a@b.com.")
    out = engine.scrub(text)

    # Clinical / billing vocabulary preserved (would be [NAME] with the bug):
    assert "Anxiety Disorder" in out
    assert "Direct Deposit" in out
    # True PHI still removed:
    assert "123-45-6789" not in out and "[SSN]" in out
    assert "a@b.com" not in out and "[EMAIL]" in out


def test_hash_value_is_deterministic_and_stable():
    """Same salt+value → same hash; blanks stay blank; salt changes output."""
    h1 = hash_value("MEM12345", "salt")
    h2 = hash_value("MEM12345", "salt")
    assert h1 == h2 and len(h1) == 16
    assert hash_value("MEM12345", "other") != h1
    assert hash_value("", "salt") == ""


def test_resolve_policy_precedence_and_normalization():
    """Headers map to actions; matching is case/space-insensitive."""
    policy = resolve_policy(
        ["Ticket ID", "First Name", "Member ID", "Full Thread"],
        ["first_name"], ["member_id"], ["full_thread"],
    )
    assert policy["First Name"] == "drop"
    assert policy["Member ID"] == "hash"
    assert policy["Full Thread"] == "scrub"
    assert policy["Ticket ID"] == "keep"


# ── Integration: full round-trip ───────────────────────────────────────

def test_run_round_trip_csv(phi_csv, tmp_path):
    """End-to-end: ID cols gone/hashed-stable, free text scrubbed, report written."""
    out_path = tmp_path / "out.csv"
    run(phi_csv, out_path, DROP, HASH, TEXT, salt="unit-salt")

    with open(out_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # Dropped identifier columns are gone entirely.
    assert "first_name" not in rows[0]
    assert "email" not in rows[0]
    # Hashed join key survives, is 16 hex chars, and is stable across runs.
    assert len(rows[0]["member_id"]) == 16
    assert rows[0]["member_id"] == hash_value("MEM12345", "unit-salt")
    # Non-PHI signal columns untouched.
    assert rows[0]["trc_code"] == "TRC-100"
    # Free text: PHI scrubbed, clinical/payer vocab preserved.
    thread = rows[0]["full_thread"]
    assert "555-999-0000" not in thread and "jane@x.com" not in thread
    assert "Anxiety Disorder" in thread
    assert "Direct Deposit" in thread
    assert "UHC" in thread
    assert "Blue Cross Blue Shield" in rows[1]["full_thread"]
    assert "123-45-6789" not in rows[1]["full_thread"]

    # Report file exists and counts at least the obvious PHI hits.
    report_path = out_path.with_name(f"{out_path.stem}.report.txt")
    assert report_path.exists()
    text = report_path.read_text(encoding="utf-8")
    assert "DROP" in text and "first_name" in text
    assert "[SSN]" in text


def test_report_counts_match_phi(phi_csv):
    """The diff report tallies the right per-PHI-type counts from free text."""
    from scripts.deidentify_spreadsheet import read_spreadsheet
    headers, data = read_spreadsheet(phi_csv)
    policy = resolve_policy(headers, DROP, HASH, TEXT)
    _, _, report = deidentify(headers, data, policy, salt="s")

    # Row 1 has one email + one phone (in free text); row 2 has one SSN.
    assert report["phi_counts"]["[SSN]"] == 1
    assert report["phi_counts"]["[EMAIL]"] >= 1
    assert report["phi_counts"]["[PHONE]"] >= 1
    rendered = render_report(report, Path("in.csv"), Path("out.csv"))
    assert "PHI REMOVED FROM FREE TEXT" in rendered
