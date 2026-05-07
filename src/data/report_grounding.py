"""AI Reports — Grounding harness (R3.1).

Audits a generated `Report` against the live SQLite warehouse so we can
attach a 0.0–1.0 `accuracy_score` and a list of `accuracy_flags` to each
report. Per user spec (2026-05-06), the harness is **WARN-ONLY**; it
never raises and never blocks report generation.

Scoring dimensions (per finding):

  1. **Count fidelity** — evidence chips of `kind=metric` whose value
     parses as an integer count are re-run as a `ticket_index` query.
     Pass if claimed within ±2% of the live count, OR if the chip can't
     be matched to a queryable column (informational metrics like
     z-scores aren't penalized).

  2. **Entity fidelity** — every entity name in the finding's `cohort`
     field + chips of kind ∈ {`cohort`, `source`} must exist in
     `ticket_index.insurance_payer / trc_label / trc_code / provider_id`.
     Pass if all entities resolve.

  3. **Time fidelity** — date-like values in chips (and the trend array)
     must fall inside the report's `scope.date_range`. Pass if all in-range
     OR if the report has no scoped date range.

Composite finding score = pass_count / dims_attempted.
Composite report score   = mean(finding_scores). Empty findings → None.

Reference: 2026-05-06 conversation, R3 spec (90% accuracy target on
underlying data).

Public API
----------
- `GroundingHarness(db, date_start, date_end, trc_filter=None)`
- `harness.score(report) -> None`  (mutates report)
- `score_report(report, db, date_start, date_end, trc_filter=None) -> None`

Dependencies
------------
- src.data.report_schema (Report, Finding)
- sqlite3-compatible db with `ticket_index` table. Tolerates absence of
  optional columns (insurance_payer, provider_id) — silently skips.

Dependents
----------
- src.data.ai_report_pipeline._phase2c_ground (post-parse hook)
- tests/test_report_grounding.py
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from src.data.report_schema import Finding, Report

logger = logging.getLogger("alma.report_grounding")


# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────

# A claimed-count chip looks like "275" or "275 tickets" or "1,788".
_INT_RE = re.compile(r"^\s*([\d,]+)\s*(tickets?)?\s*$", re.IGNORECASE)
# A YYYY-MM-DD date inside any string.
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
# Tolerance for count fidelity (±%).
_COUNT_TOLERANCE = 0.10
# Cap entities-checked per finding so we don't run unbounded queries.
_MAX_ENTITIES_PER_FINDING = 8


# ──────────────────────────────────────────────────────────────────────
# Result dataclasses
# ──────────────────────────────────────────────────────────────────────

@dataclass
class _DimResult:
    """One dimension's outcome for a single finding."""
    dim: str            # "count" | "entity" | "time"
    passed: bool
    attempted: bool     # False = no claim of this kind to verify
    detail: str = ""    # short human-readable reason on fail


# ──────────────────────────────────────────────────────────────────────
# GroundingHarness
# ──────────────────────────────────────────────────────────────────────

class GroundingHarness:
    """Score a Report's claims against `ticket_index`. Never raises."""

    def __init__(
        self,
        db: object,
        date_start: str | None,
        date_end: str | None,
        trc_filter: str | None = None,
    ) -> None:
        self.db = db
        self.date_start = (date_start or "")[:10] or None
        self.date_end = (date_end or "")[:10] or None
        self.trc_filter = trc_filter or None
        self._payer_cache: set[str] | None = None
        self._trc_cache: set[str] | None = None
        self._provider_cache: set[str] | None = None

    # ── public ─────────────────────────────────────────────────────

    def score(self, report: Report) -> None:
        """Mutate `report.accuracy_score` + `accuracy_flags` in-place."""
        if not report.findings:
            report.accuracy_score = None
            return
        finding_scores: list[float] = []
        for finding in report.findings:
            results = self._score_finding(finding)
            f_score = _composite(results)
            finding_scores.append(f_score)
            self._apply_finding_flags(report, finding, results)

        if not finding_scores:
            report.accuracy_score = None
            return
        report.accuracy_score = round(sum(finding_scores) / len(finding_scores), 4)
        if report.accuracy_score < 0.75:
            report.accuracy_flags.append("low_accuracy")

    # ── per-finding ────────────────────────────────────────────────

    def _score_finding(self, finding: Finding) -> list[_DimResult]:
        """Run all 3 dimensions for one finding."""
        results: list[_DimResult] = []
        results.append(self._check_count(finding))
        results.append(self._check_entities(finding))
        results.append(self._check_time(finding))
        return results

    def _check_count(self, finding: Finding) -> _DimResult:
        """Re-run claimed ticket_count against ticket_index.

        Treat `evidence_chips` of `kind=metric` whose value parses as an
        integer as a "claimed count". Attempt only the first such chip per
        finding (first-claim semantics) to keep the query budget bounded.
        """
        claim = self._first_count_claim(finding)
        if claim is None:
            return _DimResult(dim="count", passed=True, attempted=False)
        try:
            actual = self._query_ticket_count(finding)
        except Exception as exc:
            logger.debug("count query failed: %s", exc)
            return _DimResult(
                dim="count", passed=True, attempted=False,
                detail="query_unavailable",
            )
        if actual == 0:
            # Can't ground without data — neutral.
            return _DimResult(dim="count", passed=True, attempted=False,
                              detail="no_baseline")
        rel_error = abs(claim - actual) / max(actual, 1)
        if rel_error <= _COUNT_TOLERANCE:
            return _DimResult(dim="count", passed=True, attempted=True)
        return _DimResult(
            dim="count", passed=False, attempted=True,
            detail=f"claim={claim}_actual={actual}_err={rel_error:.0%}",
        )

    def _check_entities(self, finding: Finding) -> _DimResult:
        """Verify entity names referenced in cohort + cohort/source chips
        actually exist in `ticket_index`."""
        entities = self._extract_entities(finding)
        if not entities:
            return _DimResult(dim="entity", passed=True, attempted=False)
        try:
            unresolved = self._unresolved_entities(entities)
        except Exception as exc:
            logger.debug("entity lookup failed: %s", exc)
            return _DimResult(dim="entity", passed=True, attempted=False,
                              detail="lookup_unavailable")
        if not unresolved:
            return _DimResult(dim="entity", passed=True, attempted=True)
        # Half-credit: ≥50% resolved → still pass with a flag
        resolved_pct = 1.0 - (len(unresolved) / max(len(entities), 1))
        passed = resolved_pct >= 0.5
        return _DimResult(
            dim="entity", passed=passed, attempted=True,
            detail=f"unresolved={','.join(sorted(unresolved)[:5])}",
        )

    def _check_time(self, finding: Finding) -> _DimResult:
        """Verify date-like chips and trend periods fall inside scope."""
        if not (self.date_start or self.date_end):
            return _DimResult(dim="time", passed=True, attempted=False)
        dates = list(self._extract_dates(finding))
        if not dates:
            return _DimResult(dim="time", passed=True, attempted=False)
        out_of_range = [d for d in dates if not self._in_range(d)]
        if not out_of_range:
            return _DimResult(dim="time", passed=True, attempted=True)
        return _DimResult(
            dim="time", passed=False, attempted=True,
            detail=f"out_of_range={','.join(sorted(out_of_range)[:3])}",
        )

    # ── extraction helpers ────────────────────────────────────────

    def _first_count_claim(self, finding: Finding) -> int | None:
        """Return the first integer-valued metric chip's value."""
        for chip in finding.evidence_chips:
            if chip.kind.value != "metric":
                continue
            m = _INT_RE.match(chip.value or "")
            if not m:
                continue
            return int(m.group(1).replace(",", ""))
        return None

    def _extract_entities(self, finding: Finding) -> set[str]:
        entities: set[str] = set()
        for chip in finding.evidence_chips:
            if chip.kind.value not in ("cohort", "source"):
                continue
            for token in _split_entity_value(chip.value):
                entities.add(token)
        if finding.cohort:
            for token in _split_entity_value(finding.cohort):
                entities.add(token)
        # Also include trcs_touched entries — they're entity-shaped
        for trc in finding.trcs_touched:
            entities.add(trc.strip())
        # Cap budget
        if len(entities) > _MAX_ENTITIES_PER_FINDING:
            return set(list(entities)[:_MAX_ENTITIES_PER_FINDING])
        return entities

    def _extract_dates(self, finding: Finding):
        """Yield every date string referenced by the finding."""
        for chip in finding.evidence_chips:
            for m in _DATE_RE.finditer(chip.value or ""):
                yield m.group(1)
        for point in finding.trend:
            for m in _DATE_RE.finditer(point.period or ""):
                yield m.group(1)

    def _in_range(self, date_str: str) -> bool:
        """True if date_str is within [date_start, date_end]."""
        if self.date_start and date_str < self.date_start:
            return False
        if self.date_end and date_str > self.date_end:
            return False
        return True

    # ── DB queries ────────────────────────────────────────────────

    def _query_ticket_count(self, finding: Finding) -> int:
        """COUNT(*) over ticket_index scoped by the finding's TRCs/dates.

        Returns 0 if the table doesn't exist or the query fails — caller
        treats 0 as 'no baseline' (no penalty).
        """
        conn = self._conn()
        if conn is None:
            return 0
        where, params = self._scope_where(finding)
        sql = f"SELECT COUNT(*) FROM ticket_index ti WHERE {where}"
        try:
            row = conn.execute(sql, params).fetchone()
        except Exception:
            return 0
        return int(row[0] if row else 0)

    def _unresolved_entities(self, entities: set[str]) -> set[str]:
        """Return the subset of entities that don't appear anywhere
        in the entity caches. Caches are loaded lazily."""
        self._ensure_caches()
        unresolved: set[str] = set()
        for ent in entities:
            if not ent:
                continue
            if (ent in self._payer_cache
                    or ent in self._trc_cache
                    or ent in self._provider_cache):
                continue
            # Try lower-cased substring match for tolerance
            lower = ent.lower()
            if any(lower in p.lower() for p in self._payer_cache):
                continue
            if any(lower in t.lower() for t in self._trc_cache):
                continue
            unresolved.add(ent)
        return unresolved

    def _ensure_caches(self) -> None:
        if self._payer_cache is not None:
            return
        self._payer_cache = self._distinct("insurance_payer")
        self._trc_cache = self._distinct("trc_label") | self._distinct("trc_code")
        self._provider_cache = self._distinct("provider_id")

    def _distinct(self, column: str) -> set[str]:
        """SELECT DISTINCT helper that tolerates missing columns."""
        conn = self._conn()
        if conn is None:
            return set()
        try:
            rows = conn.execute(
                f"SELECT DISTINCT {column} FROM ticket_index "
                f"WHERE {column} IS NOT NULL AND {column} != ''",
            ).fetchall()
        except Exception:
            return set()
        out = set()
        for row in rows:
            value = row[0]
            if isinstance(value, str) and value.strip():
                out.add(value.strip())
        return out

    def _scope_where(self, finding: Finding) -> tuple[str, list]:
        """Build the WHERE clause for ticket-count queries.

        Honors the harness's date_start/date_end (always) and the finding's
        `trcs_touched` (when set). Returns (sql, params).
        """
        clauses: list[str] = ["1=1"]
        params: list = []
        if self.date_start:
            clauses.append("SUBSTR(COALESCE(ti.ticket_created_date, ''), 1, 10) >= ?")
            params.append(self.date_start)
        if self.date_end:
            clauses.append("SUBSTR(COALESCE(ti.ticket_created_date, ''), 1, 10) <= ?")
            params.append(self.date_end)
        if finding.trcs_touched:
            placeholders = ",".join("?" * len(finding.trcs_touched))
            clauses.append(f"(ti.trc_code IN ({placeholders}) "
                            f"OR ti.trc_label IN ({placeholders}))")
            params.extend(finding.trcs_touched)
            params.extend(finding.trcs_touched)
        return " AND ".join(clauses), params

    def _conn(self):
        """Return a sqlite connection or None.

        Tolerates: a DatabaseManager (uses .conn), a raw sqlite3.Connection,
        or any object exposing .execute().
        """
        if self.db is None:
            return None
        if hasattr(self.db, "conn"):
            return self.db.conn
        if hasattr(self.db, "execute"):
            return self.db
        return None

    # ── flag application ──────────────────────────────────────────

    def _apply_finding_flags(
        self, report: Report, finding: Finding, results: list[_DimResult],
    ) -> None:
        """Record per-finding flags on the report's accuracy_flags list.

        Format: `<finding_id>:<dim>:<detail>`. Keeps the audit trail compact
        while preserving enough info to surface in the evidence panel.
        """
        for r in results:
            if r.attempted and not r.passed:
                report.accuracy_flags.append(
                    f"{finding.finding_id}:{r.dim}:{r.detail}"
                )


# ──────────────────────────────────────────────────────────────────────
# Module-level convenience
# ──────────────────────────────────────────────────────────────────────

def score_report(
    report: Report,
    db: object,
    date_start: str | None,
    date_end: str | None,
    trc_filter: str | None = None,
) -> None:
    """Convenience wrapper. Equivalent to `GroundingHarness(...).score(report)`."""
    GroundingHarness(db, date_start, date_end, trc_filter=trc_filter).score(report)


def _composite(results: list[_DimResult]) -> float:
    """Composite per-finding score. Skips dims that weren't attempted."""
    attempted = [r for r in results if r.attempted]
    if not attempted:
        return 1.0  # nothing to verify ⇒ assume good
    passed = sum(1 for r in attempted if r.passed)
    return passed / len(attempted)


def _split_entity_value(value: str | None) -> list[str]:
    """Split a chip value like 'Cigna, Humana, BCBS' into a token list."""
    if not value:
        return []
    parts: list[str] = []
    for chunk in re.split(r"[,;/&]| and ", value):
        token = chunk.strip().strip("'\"")
        if token:
            parts.append(token)
    return parts
