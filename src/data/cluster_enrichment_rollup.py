"""Phase 7 cluster enrichment rollups — S10.1.

Single-pass aggregation that produces one `canonical_cluster_enrichment`
row per (cluster_id, scan_id) at the end of `run_canonicalization`.

The analyst report consumes these rows to generate concept-level narrative
("top payers for this concept", "velocity vs last week", etc.) so it never
has to re-aggregate ticket_index itself.

Design notes:
  * Idempotent: re-running with the same scan_id deletes prior rows first.
  * Resilient: any individual cluster rollup that fails is logged and
    skipped — the rest still land. A single malformed ticket row does not
    break the whole scan.
  * Checksum-stable: every row is stamped with SHA256 of the canonical
    aggregation SQL so downstream reports can prove *what* was measured,
    not just when.
  * Graceful schema fallback: optional columns (e.g. csat_score) may not
    be present in older DBs or when ticket_index hasn't been enriched
    with that dimension yet — these produce NULL metrics, not crashes.

Plan reference: canonicalization-enrichment.md §7, s10-backend-foundation-kernel.md §S10.1.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class ClusterEnrichment:
    cluster_id: str
    scan_id: str
    ticket_count: int
    avg_sentiment_intensity: Optional[float] = None
    negative_sentiment_pct: Optional[float] = None
    avg_csat: Optional[float] = None
    csat_response_rate: Optional[float] = None
    avg_resolution_hours: Optional[float] = None
    avg_first_reply_hours: Optional[float] = None
    anomaly_ticket_pct: Optional[float] = None
    velocity_wow_pct: Optional[float] = None
    velocity_mom_pct: Optional[float] = None
    top_payers: list[dict] = field(default_factory=list)
    top_providers: list[dict] = field(default_factory=list)
    top_states: list[dict] = field(default_factory=list)
    top_agents: list[dict] = field(default_factory=list)
    top_key_phrases: list[dict] = field(default_factory=list)
    top_tags: list[dict] = field(default_factory=list)
    trc_distribution: list[dict] = field(default_factory=list)
    custom_metrics: dict = field(default_factory=dict)


@dataclass
class RollupResult:
    scan_id: str
    cluster_count: int
    rows_written: int
    wall_time_ms: int
    skipped_clusters: list[str] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────
# Source-of-truth aggregation SQL (checksum stamped on every row)
# ──────────────────────────────────────────────────────────────────────────
#
# Each top-level metric lives in its own CTE so checksum stays stable when
# the engine adds new dimensions (we hash only the core_metrics block).
_CORE_METRICS_SQL = """
SELECT
    canonical_issue_id                                              AS cluster_id,
    COUNT(*)                                                        AS ticket_count,
    AVG(CAST(sentiment_intensity AS REAL))                          AS avg_sentiment,
    AVG(CASE WHEN sentiment_polarity = 'negative' THEN 1.0 ELSE 0.0 END) AS neg_pct,
    AVG(csat_score)                                                 AS avg_csat,
    AVG(CASE WHEN csat_score IS NOT NULL THEN 1.0 ELSE 0.0 END)     AS csat_rate,
    AVG(resolution_hours)                                           AS avg_resolution,
    AVG(CASE WHEN anomaly_flag IS NOT NULL
                  AND anomaly_flag != ''
                  AND anomaly_flag != 'normal'
                THEN 1.0 ELSE 0.0 END)                              AS anomaly_pct
  FROM ticket_index
 WHERE canonical_issue_id IS NOT NULL
 GROUP BY canonical_issue_id
""".strip()

_CORE_METRICS_CHECKSUM = hashlib.sha256(
    _CORE_METRICS_SQL.replace(" ", "").replace("\n", "").encode("utf-8")
).hexdigest()


# ──────────────────────────────────────────────────────────────────────────
# Top-N helpers
# ──────────────────────────────────────────────────────────────────────────

def _top_n_distribution(
    conn, cluster_id: str, column: str, total: int,
    *, limit: int = 5, where_extra: str = "",
) -> list[dict]:
    """Return [{value, count, pct}, ...] for the top-N distinct values of
    `column` within this cluster. `pct` is (count / total), rounded to 4 dp.
    """
    if total <= 0:
        return []
    try:
        rows = conn.execute(
            f"""
            SELECT {column} AS value, COUNT(*) AS cnt
              FROM ticket_index
             WHERE canonical_issue_id = ?
               AND {column} IS NOT NULL
               AND {column} != ''
               {where_extra}
             GROUP BY {column}
             ORDER BY cnt DESC, value
             LIMIT ?
            """,
            (cluster_id, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        # Column not present on this DB
        return []
    return [
        {"value": str(r[0]), "count": int(r[1]), "pct": round(r[1] / total, 4)}
        for r in rows
    ]


def _top_n_tags(conn, cluster_id: str, total: int, *, limit: int = 5) -> list[dict]:
    """Top tags — requires join through ticket_tags."""
    if total <= 0:
        return []
    try:
        rows = conn.execute(
            """
            SELECT tt.tag AS value, COUNT(DISTINCT tt.ticket_id) AS cnt
              FROM ticket_tags tt
              JOIN ticket_index ti ON ti.ticket_id = tt.ticket_id
             WHERE ti.canonical_issue_id = ?
               AND tt.tag IS NOT NULL
               AND tt.tag != ''
             GROUP BY tt.tag
             ORDER BY cnt DESC, tt.tag
             LIMIT ?
            """,
            (cluster_id, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        {"value": str(r[0]), "count": int(r[1]), "pct": round(r[1] / total, 4)}
        for r in rows
    ]


def _top_n_key_phrases(conn, cluster_id: str, total: int, *, limit: int = 5) -> list[dict]:
    """Key phrases live in a TEXT column as JSON array per ticket; we
    collect all phrases across the cluster and count them in Python."""
    if total <= 0:
        return []
    try:
        rows = conn.execute(
            """SELECT key_phrases FROM ticket_index
                WHERE canonical_issue_id = ?
                  AND key_phrases IS NOT NULL AND key_phrases != ''""",
            (cluster_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    from collections import Counter
    counter: Counter = Counter()
    for r in rows:
        raw = r[0]
        if not raw:
            continue
        # Two historical formats: JSON list or comma-separated
        phrases: list[str] = []
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                phrases = [str(p).strip() for p in parsed if p]
        except Exception:
            phrases = [p.strip() for p in str(raw).split(",") if p.strip()]
        for p in phrases:
            if p:
                counter[p] += 1
    top = counter.most_common(limit)
    return [
        {"value": phrase, "count": int(cnt), "pct": round(cnt / total, 4)}
        for phrase, cnt in top
    ]


def _trc_distribution(conn, cluster_id: str, total: int) -> list[dict]:
    """Per-TRC breakdown of tickets in this cluster. Usually 1 entry since
    canonical clusters are TRC-scoped, but ingest paths sometimes put
    cross-TRC tickets in the same cluster when KNN-assigned."""
    if total <= 0:
        return []
    rows = conn.execute(
        """
        SELECT trc_code AS value, COUNT(*) AS cnt
          FROM ticket_index
         WHERE canonical_issue_id = ?
           AND trc_code IS NOT NULL
         GROUP BY trc_code
         ORDER BY cnt DESC
        """,
        (cluster_id,),
    ).fetchall()
    return [
        {"value": str(r[0]), "count": int(r[1]), "pct": round(r[1] / total, 4)}
        for r in rows
    ]


# ──────────────────────────────────────────────────────────────────────────
# Velocity
# ──────────────────────────────────────────────────────────────────────────

def _velocity(conn, cluster_id: str, total: int) -> tuple[Optional[float], Optional[float]]:
    """Return (wow_pct, mom_pct): percent change in ticket creation vs
    prior 7 days / 30 days, relative to the most recent
    ticket_created_date for this cluster.

    Returns (None, None) when we don't have enough data points to compute
    the comparison (e.g. cluster was just discovered this scan).
    """
    if total <= 0:
        return None, None
    try:
        row = conn.execute(
            """SELECT MAX(ticket_created_date) FROM ticket_index
                WHERE canonical_issue_id = ?
                  AND ticket_created_date IS NOT NULL""",
            (cluster_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None, None
    latest = row[0] if row else None
    if not latest:
        return None, None

    def _window(n_days: int, start_offset: int) -> Optional[int]:
        """COUNT tickets with created_date in [latest - (start_offset+n_days), latest - start_offset]."""
        try:
            r = conn.execute(
                f"""SELECT COUNT(*) FROM ticket_index
                     WHERE canonical_issue_id = ?
                       AND ticket_created_date IS NOT NULL
                       AND date(ticket_created_date) > date(?, '-{start_offset + n_days} days')
                       AND date(ticket_created_date) <= date(?, '-{start_offset} days')""",
                (cluster_id, latest, latest),
            ).fetchone()
            return int(r[0]) if r else None
        except sqlite3.OperationalError:
            return None

    cur_7 = _window(7, 0)
    prev_7 = _window(7, 7)
    cur_30 = _window(30, 0)
    prev_30 = _window(30, 30)

    def _pct(cur: Optional[int], prev: Optional[int]) -> Optional[float]:
        if cur is None or prev is None:
            return None
        if prev == 0:
            # Infinite growth from 0 is represented as None to avoid inf in JSON
            return None if cur == 0 else None
        return round((cur - prev) / prev, 4)

    return _pct(cur_7, prev_7), _pct(cur_30, prev_30)


# ──────────────────────────────────────────────────────────────────────────
# Per-cluster rollup
# ──────────────────────────────────────────────────────────────────────────

def _rollup_one(conn, scan_id: str, core_row: sqlite3.Row) -> ClusterEnrichment:
    """Compute enrichment for a single cluster from its core_metrics row
    plus the per-entity top-N queries."""
    cid = core_row["cluster_id"]
    total = int(core_row["ticket_count"] or 0)

    wow, mom = _velocity(conn, cid, total)

    return ClusterEnrichment(
        cluster_id=cid,
        scan_id=scan_id,
        ticket_count=total,
        avg_sentiment_intensity=_maybe_float(core_row["avg_sentiment"]),
        negative_sentiment_pct=_round_or_none(_maybe_float(core_row["neg_pct"]), 4),
        avg_csat=_maybe_float(core_row["avg_csat"]),
        csat_response_rate=_round_or_none(_maybe_float(core_row["csat_rate"]), 4),
        avg_resolution_hours=_maybe_float(core_row["avg_resolution"]),
        avg_first_reply_hours=None,  # not modeled in current ticket_index
        anomaly_ticket_pct=_round_or_none(_maybe_float(core_row["anomaly_pct"]), 4),
        velocity_wow_pct=wow,
        velocity_mom_pct=mom,
        top_payers=_top_n_distribution(conn, cid, "insurance_payer", total),
        top_providers=_top_n_distribution(conn, cid, "provider_id", total),
        top_states=_top_n_distribution(conn, cid, "service_state", total),
        top_agents=_top_n_distribution(conn, cid, "agent_id", total),
        top_key_phrases=_top_n_key_phrases(conn, cid, total),
        top_tags=_top_n_tags(conn, cid, total),
        trc_distribution=_trc_distribution(conn, cid, total),
        custom_metrics={},
    )


def _maybe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _round_or_none(val: Optional[float], digits: int) -> Optional[float]:
    if val is None:
        return None
    return round(val, digits)


# ──────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────

def compute_enrichment_rollups(
    conn, scan_id: str, *, persist: bool = True,
) -> RollupResult:
    """Compute enrichment rollups for every canonical cluster with ≥1
    assigned ticket. Returns a summary of what was written.

    Re-running with the same `scan_id` replaces prior rows (idempotent).

    If the migration hasn't been applied, this is a no-op — logs a warning
    and returns a zero-row RollupResult.
    """
    t0 = time.perf_counter()

    # Fast exit if migration 023 hasn't landed yet
    try:
        conn.execute("SELECT 1 FROM canonical_cluster_enrichment LIMIT 0")
    except sqlite3.OperationalError:
        logger.warning(
            "canonical_cluster_enrichment not present — migration 023 not applied. "
            "Rollup skipped for scan %s.",
            scan_id,
        )
        return RollupResult(scan_id=scan_id, cluster_count=0, rows_written=0, wall_time_ms=0)

    # Pull core metrics per cluster in one pass
    conn.row_factory = sqlite3.Row
    core_rows = conn.execute(_CORE_METRICS_SQL).fetchall()

    enrichments: list[ClusterEnrichment] = []
    skipped: list[str] = []
    for core in core_rows:
        try:
            enrichments.append(_rollup_one(conn, scan_id, core))
        except Exception as exc:
            cid = core["cluster_id"] if core else "<unknown>"
            logger.warning("Enrichment rollup failed for cluster %s: %s", cid, exc)
            skipped.append(str(cid))

    rows_written = 0
    if persist and enrichments:
        # Idempotent: nuke prior rows for this scan_id, then bulk insert
        conn.execute(
            "DELETE FROM canonical_cluster_enrichment WHERE scan_id = ?",
            (scan_id,),
        )
        params = [
            (
                e.cluster_id, e.scan_id, e.ticket_count,
                e.avg_sentiment_intensity, e.negative_sentiment_pct,
                e.avg_csat, e.csat_response_rate,
                e.avg_resolution_hours, e.avg_first_reply_hours,
                e.anomaly_ticket_pct,
                e.velocity_wow_pct, e.velocity_mom_pct,
                json.dumps(e.top_payers, ensure_ascii=False),
                json.dumps(e.top_providers, ensure_ascii=False),
                json.dumps(e.top_states, ensure_ascii=False),
                json.dumps(e.top_agents, ensure_ascii=False),
                json.dumps(e.top_key_phrases, ensure_ascii=False),
                json.dumps(e.top_tags, ensure_ascii=False),
                json.dumps(e.trc_distribution, ensure_ascii=False),
                json.dumps(e.custom_metrics, ensure_ascii=False),
                _CORE_METRICS_CHECKSUM,
            )
            for e in enrichments
        ]
        conn.executemany(
            """
            INSERT INTO canonical_cluster_enrichment
                (cluster_id, scan_id, ticket_count,
                 avg_sentiment_intensity, negative_sentiment_pct,
                 avg_csat, csat_response_rate,
                 avg_resolution_hours, avg_first_reply_hours,
                 anomaly_ticket_pct,
                 velocity_wow_pct, velocity_mom_pct,
                 top_payers_json, top_providers_json, top_states_json,
                 top_agents_json, top_key_phrases_json, top_tags_json,
                 trc_distribution_json, custom_metrics_json,
                 source_query_checksum)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            params,
        )
        rows_written = len(params)

    wall_ms = int((time.perf_counter() - t0) * 1000)
    return RollupResult(
        scan_id=scan_id,
        cluster_count=len(core_rows),
        rows_written=rows_written,
        wall_time_ms=wall_ms,
        skipped_clusters=skipped,
    )


def get_enrichment(
    conn, cluster_id: str, scan_id: str,
) -> Optional[ClusterEnrichment]:
    """Read back enrichment for a single (cluster, scan) — used by the
    analyst report to populate concept-level narrative. Returns None if
    no row exists."""
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT * FROM canonical_cluster_enrichment
                WHERE cluster_id = ? AND scan_id = ?""",
            (cluster_id, scan_id),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None

    def _j(field: str) -> list:
        raw = row[field]
        if not raw:
            return []
        try:
            return json.loads(raw)
        except Exception:
            return []

    return ClusterEnrichment(
        cluster_id=row["cluster_id"],
        scan_id=row["scan_id"],
        ticket_count=int(row["ticket_count"] or 0),
        avg_sentiment_intensity=row["avg_sentiment_intensity"],
        negative_sentiment_pct=row["negative_sentiment_pct"],
        avg_csat=row["avg_csat"],
        csat_response_rate=row["csat_response_rate"],
        avg_resolution_hours=row["avg_resolution_hours"],
        avg_first_reply_hours=row["avg_first_reply_hours"],
        anomaly_ticket_pct=row["anomaly_ticket_pct"],
        velocity_wow_pct=row["velocity_wow_pct"],
        velocity_mom_pct=row["velocity_mom_pct"],
        top_payers=_j("top_payers_json"),
        top_providers=_j("top_providers_json"),
        top_states=_j("top_states_json"),
        top_agents=_j("top_agents_json"),
        top_key_phrases=_j("top_key_phrases_json"),
        top_tags=_j("top_tags_json"),
        trc_distribution=_j("trc_distribution_json"),
        custom_metrics=_j("custom_metrics_json") if isinstance(_j("custom_metrics_json"), dict) else {},
    )
