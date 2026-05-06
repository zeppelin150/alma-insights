"""Monthly taxonomy drift audit (standalone CLI).

Compares the actual distinct values in production ticket_index columns
against the canonical lists in src.data.chat_tools.canonical_taxonomy
and produces a markdown report of:

  - Proposed additions    — values present in data, missing from canon
  - Proposed retirements  — canon values absent from data in last N days
  - Suspected typos       — observed values close to a canonical value
                            (similarity ≥ 0.85, stdlib difflib)
  - Volume per finding    — so the human reviewer can prioritize

Audited columns:
  ticket_index.friction_type        canonical: CANONICAL_FRICTION_TYPES
  ticket_index.sentiment_polarity   canonical: CANONICAL_SENTIMENT_POLARITY
  ticket_index.anomaly_flag         canonical: CANONICAL_ANOMALY_FLAGS
  ticket_index.trc_code             canonical: previous-snapshot diff
  nlp_ticket_classifications.sub_cluster
                                    canonical: previous-snapshot diff

The first three columns have a Python-side canonical tuple. The last two
are too long / drift-prone to maintain inline; for those we diff the
current distinct set against the previous audit's snapshot
(tools/audit_reports/last_snapshot.json) and report new arrivals only.

OUTPUT
    tools/audit_reports/<YYYY-MM-DD>.md   — human-readable report
    tools/audit_reports/<YYYY-MM-DD>.json — machine-readable manifest
    tools/audit_reports/last_snapshot.json — current distinct sets, used
                                              as next month's baseline

USAGE
    python tools/audit_taxonomies.py [--days 30] [--db PATH]

Cadence: 30 days. See docs/TAXONOMY_AUDIT.md for review/promotion workflow.
Library policy: stdlib only — uses difflib.get_close_matches for typo
detection (no python-Levenshtein dependency).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from difflib import get_close_matches
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data.chat_tools.canonical_taxonomy import (
    CANONICAL_ANOMALY_FLAGS,
    CANONICAL_FRICTION_TYPES,
    CANONICAL_SENTIMENT_POLARITY,
)

DEFAULT_DB = ROOT / "data" / "local_warehouse.db"
REPORTS_DIR = ROOT / "tools" / "audit_reports"

# Similarity cutoff for typo detection (stdlib difflib SequenceMatcher).
# 0.85 matches "incorect_charge" → "incorrect_charge" but not "bug" →
# "incorrect_charge". Tuned on the 12 canonical values.
TYPO_SIMILARITY_CUTOFF = 0.85


# ──────────────────────────────────────────────────────────────────
# Audit primitives
# ──────────────────────────────────────────────────────────────────

def fetch_distinct_with_counts(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    days: int,
    *,
    date_column: str | None = None,
) -> list[tuple[str, int, str | None]]:
    """Return [(value, count, first_seen_date), ...] for the column over the
    last `days` days. If date_column is None, no date filter is applied
    (and first_seen_date is None for every row)."""
    if date_column:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        sql = (
            f"SELECT {column} AS v, COUNT(*) AS n, MIN({date_column}) AS first_seen "
            f"FROM {table} "
            f"WHERE {column} IS NOT NULL AND {column} != '' "
            f"AND SUBSTR({date_column}, 1, 10) >= ? "
            f"GROUP BY {column} ORDER BY n DESC"
        )
        rows = conn.execute(sql, (cutoff,)).fetchall()
    else:
        sql = (
            f"SELECT {column} AS v, COUNT(*) AS n "
            f"FROM {table} "
            f"WHERE {column} IS NOT NULL AND {column} != '' "
            f"GROUP BY {column} ORDER BY n DESC"
        )
        rows = [(r[0], r[1], None) for r in conn.execute(sql).fetchall()]
        return rows
    return [(r[0], r[1], r[2]) for r in rows]


def audit_constant_canonical(
    conn: sqlite3.Connection,
    *,
    table: str,
    column: str,
    canonical: tuple[str, ...],
    days: int,
    date_column: str | None,
) -> dict[str, Any]:
    """Audit a column with an in-code canonical list."""
    distinct = fetch_distinct_with_counts(
        conn, table, column, days, date_column=date_column
    )
    canon_set = set(canonical)
    observed_set = {v for v, _, _ in distinct}

    # Proposed additions: in data, not in canon
    proposed_additions: list[dict] = []
    typos: list[dict] = []
    for value, count, first_seen in distinct:
        if value in canon_set:
            continue
        # Try difflib fuzzy match against canonical
        close = get_close_matches(
            value, list(canonical), n=1, cutoff=TYPO_SIMILARITY_CUTOFF,
        )
        if close:
            typos.append({
                "observed": value,
                "closest_canonical": close[0],
                "count": count,
                "first_seen": first_seen,
            })
        else:
            proposed_additions.append({
                "value": value,
                "count": count,
                "first_seen": first_seen,
            })

    # Proposed retirements: in canon, absent from data
    proposed_retirements = [
        {"value": c} for c in canonical if c not in observed_set
    ]

    return {
        "table": table,
        "column": column,
        "canonical_size": len(canonical),
        "distinct_observed": len(observed_set),
        "proposed_additions": proposed_additions,
        "proposed_retirements": proposed_retirements,
        "suspected_typos": typos,
        "in_canon_count": len([v for v in observed_set if v in canon_set]),
    }


def audit_snapshot_diff(
    conn: sqlite3.Connection,
    *,
    table: str,
    column: str,
    days: int,
    date_column: str | None,
    last_snapshot: dict[str, list[str]],
) -> dict[str, Any]:
    """Audit a column without a Python canonical list — diff against
    last month's snapshot."""
    distinct = fetch_distinct_with_counts(
        conn, table, column, days, date_column=date_column
    )
    observed_set = {v for v, _, _ in distinct}
    snapshot_set = set(last_snapshot.get(f"{table}.{column}", []))

    new_arrivals = []
    for value, count, first_seen in distinct:
        if value not in snapshot_set:
            new_arrivals.append({
                "value": value,
                "count": count,
                "first_seen": first_seen,
            })

    departed = sorted(snapshot_set - observed_set)
    return {
        "table": table,
        "column": column,
        "snapshot_size": len(snapshot_set),
        "distinct_observed": len(observed_set),
        "new_arrivals": new_arrivals,
        "departed": [{"value": v} for v in departed],
        "_observed_set": sorted(observed_set),  # for next snapshot
    }


# ──────────────────────────────────────────────────────────────────
# Markdown rendering
# ──────────────────────────────────────────────────────────────────

def render_markdown(report: dict, *, days: int) -> str:
    parts: list[str] = []
    parts.append(f"# Taxonomy Drift Audit — {report['date']}")
    parts.append("")
    parts.append(f"**Window**: last {days} days · "
                 f"**DB**: `{report['db']}` · "
                 f"**Tool**: stdlib `difflib.get_close_matches` (cutoff={TYPO_SIMILARITY_CUTOFF})")
    parts.append("")
    parts.append("Review checklist for the engineer accepting this audit:")
    parts.append("- For each *Proposed addition*: real new category, or classifier drift?")
    parts.append("- For each *Suspected typo*: misclassification → file a bug against the NLP prompt.")
    parts.append("- For each *Proposed retirement*: confirm the category is genuinely retired before removing.")
    parts.append("- For each *New arrival* (snapshot-diff columns): ensure the new TRC/sub-cluster is intentional.")
    parts.append("")

    for col_report in report["columns"]:
        col_id = f"{col_report['table']}.{col_report['column']}"
        parts.append(f"## `{col_id}`")
        parts.append("")
        if "canonical_size" in col_report:
            parts.append(
                f"Canonical: {col_report['canonical_size']} values · "
                f"Observed: {col_report['distinct_observed']} distinct · "
                f"In-canon: {col_report['in_canon_count']}"
            )
            parts.append("")

            # Proposed additions
            if col_report["proposed_additions"]:
                parts.append("### Proposed additions (in data, not in canon)")
                parts.append("")
                parts.append("| Value | Count | First seen |")
                parts.append("|---|---:|---|")
                for r in col_report["proposed_additions"]:
                    parts.append(
                        f"| `{r['value']}` | {r['count']} | {r['first_seen'] or '—'} |"
                    )
                parts.append("")

            # Suspected typos
            if col_report["suspected_typos"]:
                parts.append("### Suspected typos (close to canonical)")
                parts.append("")
                parts.append("| Observed | Closest canonical | Count | First seen |")
                parts.append("|---|---|---:|---|")
                for r in col_report["suspected_typos"]:
                    parts.append(
                        f"| `{r['observed']}` | `{r['closest_canonical']}` | "
                        f"{r['count']} | {r['first_seen'] or '—'} |"
                    )
                parts.append("")

            # Proposed retirements
            if col_report["proposed_retirements"]:
                parts.append("### Proposed retirements (canon values absent from data)")
                parts.append("")
                for r in col_report["proposed_retirements"]:
                    parts.append(f"- `{r['value']}`")
                parts.append("")

            if (not col_report["proposed_additions"]
                    and not col_report["suspected_typos"]
                    and not col_report["proposed_retirements"]):
                parts.append("✓ No drift detected.")
                parts.append("")

        else:  # snapshot-diff column
            parts.append(
                f"Snapshot baseline: {col_report['snapshot_size']} values · "
                f"Observed: {col_report['distinct_observed']} distinct"
            )
            parts.append("")
            if col_report["new_arrivals"]:
                parts.append("### New arrivals since last snapshot")
                parts.append("")
                parts.append("| Value | Count | First seen |")
                parts.append("|---|---:|---|")
                # Cap at 50 so the report stays readable
                for r in col_report["new_arrivals"][:50]:
                    parts.append(
                        f"| `{r['value']}` | {r['count']} | {r['first_seen'] or '—'} |"
                    )
                if len(col_report["new_arrivals"]) > 50:
                    parts.append(
                        f"| _(+{len(col_report['new_arrivals']) - 50} more)_ | | |"
                    )
                parts.append("")
            if col_report["departed"]:
                parts.append("### Departed since last snapshot (no longer observed)")
                parts.append("")
                for r in col_report["departed"][:50]:
                    parts.append(f"- `{r['value']}`")
                if len(col_report["departed"]) > 50:
                    parts.append(f"- _(+{len(col_report['departed']) - 50} more)_")
                parts.append("")
            if not col_report["new_arrivals"] and not col_report["departed"]:
                parts.append("✓ No drift detected.")
                parts.append("")

    parts.append("## Promotion workflow")
    parts.append("")
    parts.append("1. Review each finding above")
    parts.append("2. For *Proposed additions* you accept: edit "
                 "`src/data/chat_tools/canonical_taxonomy.py` and add the value "
                 "to the relevant tuple. Update its `Last reviewed` date in the docstring.")
    parts.append("3. For *Suspected typos*: file a bug against the NLP classifier prompt "
                 "(values come from `config/prompts/nlp_classify.txt`).")
    parts.append("4. Commit; the next audit's snapshot diff will use the updated baseline.")
    parts.append("")

    return "\n".join(parts)


# ──────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────

def run_audit(db_path: Path, days: int) -> dict:
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    last_snapshot_path = REPORTS_DIR / "last_snapshot.json"
    last_snapshot = (
        json.loads(last_snapshot_path.read_text(encoding="utf-8"))
        if last_snapshot_path.exists() else {}
    )

    conn = sqlite3.connect(str(db_path))

    column_audits = []
    new_snapshot: dict[str, list[str]] = {}

    # Constant-canonical columns
    for table, column, canonical, date_column in [
        ("ticket_index", "friction_type", CANONICAL_FRICTION_TYPES, "ticket_created_date"),
        ("ticket_index", "sentiment_polarity", CANONICAL_SENTIMENT_POLARITY, "ticket_created_date"),
        ("ticket_index", "anomaly_flag", CANONICAL_ANOMALY_FLAGS, "ticket_created_date"),
    ]:
        result = audit_constant_canonical(
            conn, table=table, column=column, canonical=canonical,
            days=days, date_column=date_column,
        )
        column_audits.append(result)
        # Snapshot the actual observed set for this column too
        observed = fetch_distinct_with_counts(
            conn, table, column, days, date_column=date_column,
        )
        new_snapshot[f"{table}.{column}"] = sorted({v for v, _, _ in observed})

    # Snapshot-diff columns
    for table, column, date_column in [
        ("ticket_index", "trc_code", "ticket_created_date"),
        ("nlp_ticket_classifications", "sub_cluster", "created_at"),
    ]:
        try:
            result = audit_snapshot_diff(
                conn, table=table, column=column,
                days=days, date_column=date_column,
                last_snapshot=last_snapshot,
            )
            new_snapshot[f"{table}.{column}"] = result.pop("_observed_set")
            column_audits.append(result)
        except sqlite3.OperationalError as e:
            # Table or column may not exist on every DB — keep the audit
            # robust to schema variation
            column_audits.append({
                "table": table, "column": column,
                "snapshot_size": 0, "distinct_observed": 0,
                "new_arrivals": [], "departed": [],
                "error": str(e),
            })

    conn.close()

    today = datetime.now().strftime("%Y-%m-%d")
    report = {
        "date": today,
        "days": days,
        "db": str(db_path),
        "columns": column_audits,
    }

    md_path = REPORTS_DIR / f"{today}.md"
    json_path = REPORTS_DIR / f"{today}.json"
    md_path.write_text(render_markdown(report, days=days), encoding="utf-8")
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    last_snapshot_path.write_text(json.dumps(new_snapshot, indent=2), encoding="utf-8")

    print(f"[audit] wrote {md_path}")
    print(f"[audit] wrote {json_path}")
    print(f"[audit] updated {last_snapshot_path}")

    # Headline summary to stdout
    total_additions = sum(
        len(c.get("proposed_additions", [])) for c in column_audits
    )
    total_typos = sum(len(c.get("suspected_typos", [])) for c in column_audits)
    total_retirements = sum(
        len(c.get("proposed_retirements", [])) for c in column_audits
    )
    total_arrivals = sum(len(c.get("new_arrivals", [])) for c in column_audits)
    print(
        f"[audit] proposed_additions={total_additions} "
        f"suspected_typos={total_typos} "
        f"proposed_retirements={total_retirements} "
        f"snapshot_new_arrivals={total_arrivals}"
    )

    return report


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--days", type=int, default=30,
                        help="Audit window in days (default 30)")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB,
                        help=f"DB path (default {DEFAULT_DB})")
    args = parser.parse_args()
    run_audit(args.db, args.days)


if __name__ == "__main__":
    main()
