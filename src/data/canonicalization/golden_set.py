"""Golden-set loader for canonicalization evaluation.

Reads the `alma_test_10000_1_golden_set.csv` format:

    pair_id, ticket_a_id, ticket_b_id, same_concept, concept_id, ...

Produces an iterable of `(ticket_a_id, ticket_b_id, same_issue_bool)` tuples
suitable for `canonicalization_engine.score_golden_set()`.

Stored pair records also feed the optional `canonicalization_golden_set`
DB table (Phase 8) via `persist_pairs_to_db()` when available.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


def load_pairs(csv_path: str | Path) -> list[tuple[str, str, bool]]:
    """Load golden-set pairs from a CSV file.

    Expected columns (case-insensitive on header):
      ticket_a_id, ticket_b_id, same_concept

    Returns:
        List of (ticket_a_id, ticket_b_id, same_issue) tuples with ticket IDs
        coerced to strings and `same_concept` parsed from "True"/"False".
    """
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"Golden-set CSV not found: {path}")

    pairs: list[tuple[str, str, bool]] = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        cols = {c.lower(): c for c in (reader.fieldnames or [])}
        need = {"ticket_a_id", "ticket_b_id", "same_concept"}
        missing = need - cols.keys()
        if missing:
            raise ValueError(
                f"Golden-set CSV missing required columns: {sorted(missing)}"
            )
        a_col, b_col, s_col = cols["ticket_a_id"], cols["ticket_b_id"], cols["same_concept"]
        for row_num, row in enumerate(reader, start=2):
            a = str(row.get(a_col, "")).strip()
            b = str(row.get(b_col, "")).strip()
            s = str(row.get(s_col, "")).strip().lower()
            if not a or not b or not s:
                logger.debug("Skipping golden-set row %d: missing fields", row_num)
                continue
            same = s in ("true", "t", "1", "yes", "y")
            pairs.append((a, b, same))
    logger.info("Loaded %d golden-set pairs from %s", len(pairs), path)
    return pairs


def iter_pairs(csv_path: str | Path) -> Iterator[tuple[str, str, bool]]:
    """Streaming variant of load_pairs for very large golden sets."""
    yield from load_pairs(csv_path)


def pair_stats(pairs: list[tuple[str, str, bool]]) -> dict:
    """Return quick counts: total pairs, same-concept, different-concept."""
    same = sum(1 for _, _, s in pairs if s)
    diff = len(pairs) - same
    return {"total": len(pairs), "same": same, "different": diff}
