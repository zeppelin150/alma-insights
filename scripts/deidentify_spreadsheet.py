"""
scripts/deidentify_spreadsheet.py — Surgical, local, no-network PHI scrubber
for ticket spreadsheets (CSV / XLSX).

Goal: strip *true* PHI (names, SSN, DOB, address, phone, email, member/MRN ids)
while PRESERVING the clinical/billing vocabulary the NLP classifier depends on
(payers, product areas, diagnosis codes, TRC vocab, complaint language). True PHI
carries no classification signal, so a correct scrubber is near-neutral for the
downstream classifier.

Two-pronged strategy — structure beats heuristics:
  1. IDENTIFIER COLUMNS (name/dob/ssn/mrn/member_id/address/phone/email, …) are
     DROPPED or HASHED wholesale. Deterministic, zero free-text damage. Hashing
     (sha256(salt + value)) is for columns you must keep as a stable join key.
  2. FREE-TEXT COLUMNS (notes/comments/thread/full_thread/description) are scrubbed
     with the project's RedactionEngine — but with the catastrophic ``name_heuristic``
     pattern DISABLED. Person names live in dropped structured columns, so the
     "any two Title-Case words -> [NAME]" rule (which eats "Anxiety Disorder",
     "Session Note", "Direct Deposit", drug/procedure names) is pure downside here.

Why disabling one pattern is safe: structured names are already column-dropped, so
free text rarely contains a raw patient name; the remaining structured PHI patterns
(SSN/email/phone/DOB/member_id/address/URL) still run and still catch real PHI.

Outputs:
  - <input>.deidentified.{csv,xlsx}  — the scrubbed spreadsheet
  - <input>.deidentified.report.txt  — a WHAT-WAS-REMOVED audit (per-column action,
    per-PHI-type counts, and a few before/after redaction samples)

Dependencies / supply-chain note:
  - ``pandas`` is already a pinned production dep (requirements.txt: pandas==2.2.3).
  - ``openpyxl`` is NOT a current dependency. It is only needed for the XLSX path.
    CSV in / CSV out uses the stdlib ``csv`` module and needs nothing new. If you
    adopt the XLSX path, add ``openpyxl`` to requirements.txt with an exact pin and
    refresh requirements.lock (transitive closure + hashes) per the repo's
    pin-and-vendor policy. Until then, ``--in foo.xlsx`` raises a clear error
    pointing at the missing dep.

Usage:
    python scripts/deidentify_spreadsheet.py --in tickets.csv
    python scripts/deidentify_spreadsheet.py --in tickets.csv --out clean.csv
    python scripts/deidentify_spreadsheet.py --in tickets.xlsx --out clean.xlsx
    python scripts/deidentify_spreadsheet.py --in tickets.csv \
        --drop-cols first_name,last_name,address \
        --hash-cols member_id,subscriber_id \
        --text-cols full_thread,comment_body \
        --salt "$ALMA_DEID_SALT"
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import logging
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from src.data.redaction_engine import RedactionEngine  # noqa: E402

logger = logging.getLogger("alma.deidentify")
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

# ── Default column policy ──────────────────────────────────────────────
# DROP: structured identifiers with zero classification signal — removed entirely.
DEFAULT_DROP_COLS = [
    "name", "first_name", "last_name", "full_name", "patient_name",
    "requester_name", "assignee_name", "dob", "date_of_birth", "ssn",
    "address", "street", "phone", "phone_number", "email", "requester_email",
]
# HASH: identifiers you must keep as a stable join key — irreversible sha256.
DEFAULT_HASH_COLS = [
    "mrn", "member_id", "subscriber_id", "patient_id", "client_id",
]
# SCRUB: free-text fields — RedactionEngine with name_heuristic disabled.
DEFAULT_TEXT_COLS = [
    "notes", "comment", "comments", "comment_body", "thread", "full_thread",
    "description", "subject", "body",
]


def _normalize_col(name: str) -> str:
    """Normalize a header for case/space-insensitive policy matching."""
    return (name or "").strip().lower().replace(" ", "_")


def build_text_engine() -> RedactionEngine:
    """Construct a RedactionEngine for free text with name_heuristic DISABLED.

    The engine stores its compiled PHI rules in ``_phi_patterns`` as a list of
    dicts keyed by ``name``; ``_find_phi_matches`` iterates that list. Dropping
    the ``name_heuristic`` entry post-construction cleanly disables only that one
    rule (the "any two Title-Case words" matcher) while leaving SSN/email/phone/
    DOB/member_id/address/URL detection fully active.
    """
    engine = RedactionEngine()
    engine._phi_patterns = [
        p for p in engine._phi_patterns if p.get("name") != "name_heuristic"
    ]
    return engine


def hash_value(value: str, salt: str) -> str:
    """Irreversible, stable join key: sha256(salt + value) → 16 hex chars.

    Empty/whitespace input maps to "" so blank cells stay blank.
    """
    if value is None or not str(value).strip():
        return ""
    digest = hashlib.sha256((salt + str(value)).encode("utf-8")).hexdigest()
    return digest[:16]


# ── Column policy resolution ───────────────────────────────────────────

def resolve_policy(headers, drop_cols, hash_cols, text_cols) -> dict:
    """Map each header to an action: 'drop' | 'hash' | 'scrub' | 'keep'.

    Precedence: drop > hash > scrub > keep. Matching is normalized
    (case/space-insensitive), so "Requester Email" matches "requester_email".
    """
    drop_set = {_normalize_col(c) for c in drop_cols}
    hash_set = {_normalize_col(c) for c in hash_cols}
    text_set = {_normalize_col(c) for c in text_cols}

    policy = {}
    for h in headers:
        norm = _normalize_col(h)
        if norm in drop_set:
            policy[h] = "drop"
        elif norm in hash_set:
            policy[h] = "hash"
        elif norm in text_set:
            policy[h] = "scrub"
        else:
            policy[h] = "keep"
    return policy


# ── Per-cell transforms (each CC <= 10) ────────────────────────────────

def _scrub_cell(value, engine, report):
    """Scrub one free-text cell; record PHI-type counts + a few samples."""
    if value is None:
        return value
    text = str(value)
    cleaned = engine.scrub(text)
    if cleaned != text:
        for token in ("[SSN]", "[EMAIL]", "[PHONE]", "[CARD]",
                      "[MEMBER_ID]", "[DOB]", "[URL]", "[ADDRESS]", "[NAME]"):
            n = cleaned.count(token) - text.count(token)
            if n > 0:
                report["phi_counts"][token] += n
        if len(report["samples"]) < 5:
            report["samples"].append((text[:120], cleaned[:120]))
    return cleaned


def _transform_row(row, policy, engine, salt, report):
    """Apply the column policy to one row dict. Returns the de-identified dict."""
    out = {}
    for col, action in policy.items():
        if action == "drop":
            continue
        value = row.get(col, "")
        if action == "hash":
            out[col] = hash_value(value, salt)
        elif action == "scrub":
            out[col] = _scrub_cell(value, engine, report)
        else:
            out[col] = value
    return out


# ── Readers / writers (CSV stdlib; XLSX via pandas+openpyxl) ────────────

def _read_csv(path: Path):
    """Read a CSV as a list of str->str row dicts (encoding fallback)."""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"Could not decode {path} as text.")
    reader = csv.DictReader(text.splitlines())
    headers = list(reader.fieldnames or [])
    rows = [{k: (v if v is not None else "") for k, v in r.items()} for r in reader]
    return headers, rows


def _read_xlsx(path: Path):
    """Read an XLSX as str rows via pandas. Requires the optional openpyxl dep."""
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - pandas is pinned
        raise RuntimeError("pandas is required for XLSX input.") from exc
    try:
        df = pd.read_excel(path, dtype=str, engine="openpyxl").fillna("")
    except ImportError as exc:
        raise RuntimeError(
            "XLSX input requires 'openpyxl' (not a current dependency). "
            "Install + pin it, or convert the file to CSV first."
        ) from exc
    headers = [str(c) for c in df.columns]
    rows = df.to_dict(orient="records")
    return headers, rows


def read_spreadsheet(path: Path):
    """Dispatch on extension. Returns (headers, rows[list[dict]])."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _read_csv(path)
    if suffix in (".xlsx", ".xlsm"):
        return _read_xlsx(path)
    raise ValueError(f"Unsupported input type: {suffix} (use .csv or .xlsx)")


def _write_csv(path: Path, headers, rows):
    """Write rows to CSV with the surviving (non-dropped) headers."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def _write_xlsx(path: Path, headers, rows):
    """Write rows to XLSX via pandas. Requires the optional openpyxl dep."""
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - pandas is pinned
        raise RuntimeError("pandas is required for XLSX output.") from exc
    df = pd.DataFrame(rows, columns=headers)
    try:
        df.to_excel(path, index=False, engine="openpyxl")
    except ImportError as exc:
        raise RuntimeError(
            "XLSX output requires 'openpyxl' (not a current dependency)."
        ) from exc


def write_spreadsheet(path: Path, headers, rows):
    """Dispatch on extension for output."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        _write_csv(path, headers, rows)
    elif suffix in (".xlsx", ".xlsm"):
        _write_xlsx(path, headers, rows)
    else:
        raise ValueError(f"Unsupported output type: {suffix}")


# ── Core orchestration ─────────────────────────────────────────────────

def deidentify(headers, rows, policy, salt):
    """Apply the policy to every row. Returns (out_headers, out_rows, report)."""
    engine = build_text_engine()
    report = {
        "policy": dict(policy),
        "phi_counts": Counter(),
        "samples": [],
        "row_count": len(rows),
    }
    out_rows = [_transform_row(r, policy, engine, salt, report) for r in rows]
    out_headers = [h for h in headers if policy.get(h) != "drop"]
    return out_headers, out_rows, report


def render_report(report, in_path, out_path) -> str:
    """Build the human-readable WHAT-WAS-REMOVED audit text."""
    lines = [
        "DE-IDENTIFICATION REPORT",
        "=" * 40,
        f"Input:  {in_path}",
        f"Output: {out_path}",
        f"Rows:   {report['row_count']}",
        "",
        "PER-COLUMN ACTION",
        "-" * 40,
    ]
    for col, action in sorted(report["policy"].items()):
        lines.append(f"  {action.upper():5}  {col}")
    lines += ["", "PHI REMOVED FROM FREE TEXT (by type)", "-" * 40]
    if report["phi_counts"]:
        for token, n in sorted(report["phi_counts"].items()):
            lines.append(f"  {token:14} {n}")
    else:
        lines.append("  (none)")
    lines += ["", "REDACTION SAMPLES (before -> after, truncated)", "-" * 40]
    for before, after in report["samples"]:
        lines.append(f"  - {before!r}")
        lines.append(f"    -> {after!r}")
    if not report["samples"]:
        lines.append("  (none)")
    return "\n".join(lines) + "\n"


def _default_out(in_path: Path) -> Path:
    """Derive the default '<stem>.deidentified.<ext>' output path."""
    return in_path.with_name(f"{in_path.stem}.deidentified{in_path.suffix}")


def _split_csv_arg(value, fallback):
    """Parse a comma-separated CLI list; fall back to defaults when blank."""
    if not value:
        return list(fallback)
    return [c.strip() for c in value.split(",") if c.strip()]


def run(in_path, out_path, drop_cols, hash_cols, text_cols, salt) -> Path:
    """End-to-end: read → policy → de-identify → write spreadsheet + report."""
    headers, rows = read_spreadsheet(in_path)
    policy = resolve_policy(headers, drop_cols, hash_cols, text_cols)
    out_headers, out_rows, report = deidentify(headers, rows, policy, salt)
    write_spreadsheet(out_path, out_headers, out_rows)

    report_path = out_path.with_name(f"{out_path.stem}.report.txt")
    report_path.write_text(render_report(report, in_path, out_path), encoding="utf-8")
    logger.info("Wrote %s (%d rows) + %s", out_path, len(out_rows), report_path)
    return out_path


def _parse_args(argv):
    """Build the CLI parser and parse argv."""
    p = argparse.ArgumentParser(description="Surgical PHI scrubber for ticket spreadsheets.")
    p.add_argument("--in", dest="in_path", required=True, help="Input .csv or .xlsx")
    p.add_argument("--out", dest="out_path", default=None, help="Output path (default: *.deidentified.*)")
    p.add_argument("--drop-cols", default=None, help="Comma-separated columns to drop")
    p.add_argument("--hash-cols", default=None, help="Comma-separated columns to hash")
    p.add_argument("--text-cols", default=None, help="Comma-separated free-text columns to scrub")
    p.add_argument("--salt", default="alma-deid-v1", help="Salt for column hashing (set per dataset)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    """CLI entry point."""
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    in_path = Path(args.in_path)
    if not in_path.exists():
        logger.error("Input not found: %s", in_path)
        return 2
    out_path = Path(args.out_path) if args.out_path else _default_out(in_path)
    run(
        in_path, out_path,
        _split_csv_arg(args.drop_cols, DEFAULT_DROP_COLS),
        _split_csv_arg(args.hash_cols, DEFAULT_HASH_COLS),
        _split_csv_arg(args.text_cols, DEFAULT_TEXT_COLS),
        args.salt,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
