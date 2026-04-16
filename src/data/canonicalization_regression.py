"""Per-scan Gemini regression for canonicalization quality (Phase 6 §6.3).

Each scan, we stratified-sample up to 30 clusters (10 stable + 10 drifting +
5 recently split/merged + 5 newly minted), rate each with a single batched
Gemini call, and persist the verdicts to `cluster_regression_reports`. A
cluster is flagged `confirmed_degradation=True` when its current AND
immediately-prior verdict_score are both < 3.0 (2-scan rule).

The module is **observational**: it never reassigns tickets or modifies
canonical_clusters rows. Phase 8 consumes `confirmed_degradation` to
surface items in the HITL review queue; Phase 6 only needs the numbers to
exist.

Plan reference: canonicalization-enrichment.md §6.3.
Kernel reference: phase-6-9-session-kernel.md "Session N+1".

Public API:
    run_regression(conn, scan_id, *, llm_client,
                   n_stable=10, n_drifting=10,
                   n_recently_split=5, n_new=5,
                   prompt_path=None, persist=True) -> RegressionResult
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_PROMPT_PATH_DEFAULT = (
    Path(__file__).resolve().parents[2] / "config" / "prompts" / "regression_eval.txt"
)

_SNIPPETS_PER_CLUSTER = 3
_SNIPPET_MAX_CHARS = 240
_SAMPLE_TICKET_IDS_MAX = 8
_LABEL_HISTORY_MAX = 3


# ──────────────────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class ClusterSample:
    cluster_id: str
    stratum: str                     # 'stable' | 'drifting' | 'recently_split' | 'new'
    canonical_label: Optional[str]
    member_count_now: int
    member_count_then: Optional[int]
    drift_cosine: Optional[float]
    label_history: list[dict] = field(default_factory=list)   # [{scan_id, label}, ...]
    snippets_now: list[str] = field(default_factory=list)
    snippets_then: list[str] = field(default_factory=list)
    sample_ticket_ids: list[str] = field(default_factory=list)

    def to_prompt_obj(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "stratum": self.stratum,
            "canonical_label": self.canonical_label or "",
            "label_history": self.label_history[:_LABEL_HISTORY_MAX],
            "member_count_now": self.member_count_now,
            "member_count_then": self.member_count_then,
            "drift_cosine": self.drift_cosine,
            "representative_snippets_now": self.snippets_now[:_SNIPPETS_PER_CLUSTER],
            "representative_snippets_then": self.snippets_then[:_SNIPPETS_PER_CLUSTER],
        }


@dataclass
class RegressionVerdict:
    cluster_id: str
    stratum: str
    verdict_score: float
    diagnosis: str
    recommended_action: str           # 'none' | 'rename' | 'split' | 'merge' | 'retire'
    prev_verdict_score: Optional[float]
    confirmed_degradation: bool
    sample_ticket_ids: list[str] = field(default_factory=list)


@dataclass
class RegressionResult:
    scan_id: str
    sampled_count: int
    verdict_count: int
    confirmed_degradation_count: int
    stratum_counts: dict[str, int]
    verdicts: list[RegressionVerdict] = field(default_factory=list)
    wall_time_ms: int = 0
    raw_output: str = ""

    def summary(self) -> dict:
        return {
            "scan_id": self.scan_id,
            "sampled_count": self.sampled_count,
            "verdict_count": self.verdict_count,
            "confirmed_degradation_count": self.confirmed_degradation_count,
            "stratum_counts": self.stratum_counts,
            "wall_time_ms": self.wall_time_ms,
        }


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────

def _load_prompt_template(prompt_path: Optional[Path] = None) -> str:
    p = Path(prompt_path) if prompt_path else _PROMPT_PATH_DEFAULT
    return p.read_text(encoding="utf-8")


def _extract_json_object(raw: str) -> Optional[dict]:
    """Strip markdown fences + balanced-brace scan. Matches the helper in
    canonicalization_engine — duplicated here to avoid a cross-import."""
    if not raw:
        return None
    txt = raw.strip()
    if txt.startswith("```"):
        lines = [ln for ln in txt.splitlines() if not ln.strip().startswith("```")]
        txt = "\n".join(lines).strip()
    start = txt.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(txt)):
        ch = txt[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(txt[start:i + 1])
                except Exception:
                    return None
    return None


def _fetch_label_history(conn, cluster_id: str) -> list[dict]:
    """Return up to _LABEL_HISTORY_MAX prior labels, most recent first."""
    try:
        rows = conn.execute(
            """
            SELECT scan_id, old_label, new_label, changed_at
              FROM cluster_label_changes
             WHERE cluster_id = ?
             ORDER BY changed_at DESC
             LIMIT ?
            """,
            (cluster_id, _LABEL_HISTORY_MAX),
        ).fetchall()
    except sqlite3.OperationalError:
        # cluster_label_changes may not exist yet (Phase 5 audit table)
        return []
    return [
        {"scan_id": r[0], "old_label": r[1], "new_label": r[2], "changed_at": str(r[3])}
        for r in rows
    ]


def _fetch_snippets(
    conn, cluster_id: str, limit: int = _SNIPPETS_PER_CLUSTER,
) -> tuple[list[str], list[str]]:
    """Return (snippets, sample_ticket_ids) — subject lines from `limit`
    tickets currently assigned to cluster_id, capped at _SNIPPET_MAX_CHARS."""
    rows = conn.execute(
        """
        SELECT ti.ticket_id, ti.subject_sanitized
          FROM ticket_index ti
         WHERE ti.canonical_issue_id = ?
           AND ti.subject_sanitized IS NOT NULL
           AND ti.subject_sanitized != ''
         LIMIT ?
        """,
        (cluster_id, max(limit, _SAMPLE_TICKET_IDS_MAX)),
    ).fetchall()
    snippets = [str(r[1])[:_SNIPPET_MAX_CHARS] for r in rows[:limit]]
    tids = [str(r[0]) for r in rows[:_SAMPLE_TICKET_IDS_MAX]]
    return snippets, tids


def _fetch_snippets_from_prior_scan(
    conn, cluster_id: str, current_scan_id: Optional[str] = None,
    limit: int = _SNIPPETS_PER_CLUSTER,
) -> list[str]:
    """S10.4: find the most recent `cluster_member_snapshots` row for
    `cluster_id` that's NOT from `current_scan_id`, pull up to `limit`
    ticket subjects as `snippets_then` historical context.

    Returns empty list when no prior snapshot exists (first scan case) or
    when migration 025 is absent.
    """
    try:
        if current_scan_id is not None:
            row = conn.execute(
                """SELECT member_ticket_ids_json FROM cluster_member_snapshots
                    WHERE cluster_id = ? AND scan_id != ?
                 ORDER BY snapshot_at DESC
                    LIMIT 1""",
                (cluster_id, current_scan_id),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT member_ticket_ids_json FROM cluster_member_snapshots
                    WHERE cluster_id = ?
                 ORDER BY snapshot_at DESC
                    LIMIT 1""",
                (cluster_id,),
            ).fetchone()
    except sqlite3.OperationalError:
        return []
    if not row or not row[0]:
        return []
    try:
        tids = json.loads(row[0])
    except Exception:
        return []
    if not isinstance(tids, list) or not tids:
        return []
    # Hydrate the first `limit` ticket subjects that still exist
    out: list[str] = []
    for tid in tids[: max(limit, _SAMPLE_TICKET_IDS_MAX)]:
        subj_row = conn.execute(
            "SELECT subject_sanitized FROM ticket_index WHERE ticket_id = ?",
            (str(tid),),
        ).fetchone()
        if subj_row and subj_row[0]:
            out.append(str(subj_row[0])[:_SNIPPET_MAX_CHARS])
        if len(out) >= limit:
            break
    return out


def _build_sample_row(
    conn, *, cluster_id: str, stratum: str,
    drift_cosine: Optional[float] = None,
    member_count_then: Optional[int] = None,
    current_scan_id: Optional[str] = None,
) -> Optional[ClusterSample]:
    """Hydrate a ClusterSample for a cluster_id; return None if cluster is
    missing (e.g. retired between sampling and hydration).

    `current_scan_id` gates the historical snapshot lookup: the "then"
    snippets pulled from cluster_member_snapshots must come from a scan
    DIFFERENT from the current one, otherwise "then" == "now".
    """
    row = conn.execute(
        "SELECT canonical_label, lifetime_tickets FROM canonical_clusters WHERE cluster_id = ?",
        (cluster_id,),
    ).fetchone()
    if row is None:
        return None
    snippets, tids = _fetch_snippets(conn, cluster_id)
    label_history = _fetch_label_history(conn, cluster_id)
    # S10.4: pull prior-scan snapshot when available
    snippets_then = _fetch_snippets_from_prior_scan(
        conn, cluster_id, current_scan_id=current_scan_id,
    )
    member_count_now = conn.execute(
        "SELECT COUNT(*) FROM ticket_index WHERE canonical_issue_id = ?",
        (cluster_id,),
    ).fetchone()[0]
    return ClusterSample(
        cluster_id=cluster_id,
        stratum=stratum,
        canonical_label=row[0],
        member_count_now=int(member_count_now),
        member_count_then=member_count_then,
        drift_cosine=drift_cosine,
        label_history=label_history,
        snippets_now=snippets,
        snippets_then=snippets_then,
        sample_ticket_ids=tids,
    )


def stratified_sample(
    conn, scan_id: str, *,
    n_stable: int = 10, n_drifting: int = 10,
    n_recently_split: int = 5, n_new: int = 5,
) -> list[ClusterSample]:
    """Pick clusters to regression-test this scan. Order within each stratum
    deliberately favors clusters that have *not* been reviewed recently so
    coverage rotates across scans."""
    samples: list[ClusterSample] = []
    picked: set[str] = set()

    def _recent_review_key(cid: str) -> tuple[int, str]:
        """(-count_of_reviews, cluster_id). More-reviewed clusters sort last."""
        try:
            cnt = conn.execute(
                "SELECT COUNT(*) FROM cluster_regression_reports WHERE cluster_id = ?",
                (cid,),
            ).fetchone()[0]
        except sqlite3.OperationalError:
            cnt = 0
        return (int(cnt or 0), cid)

    # ── stable: top by lifetime_tickets, tier='active' ──
    stable_cands = conn.execute(
        """
        SELECT cluster_id FROM canonical_clusters
         WHERE tier = 'active'
         ORDER BY COALESCE(lifetime_tickets, 0) DESC, cluster_id
         LIMIT ?
        """,
        (n_stable * 3,),
    ).fetchall()
    stable_ids = sorted([r[0] for r in stable_cands], key=_recent_review_key)[:n_stable]
    for cid in stable_ids:
        if cid in picked:
            continue
        s = _build_sample_row(
            conn, cluster_id=cid, stratum="stable", current_scan_id=scan_id,
        )
        if s is not None:
            samples.append(s)
            picked.add(cid)

    # ── drifting: highest drift_cosine this scan ──
    try:
        drift_cands = conn.execute(
            """
            SELECT cluster_id, drift_cosine, member_count_before
              FROM cluster_drift_events
             WHERE scan_id = ?
             ORDER BY drift_cosine DESC
             LIMIT ?
            """,
            (scan_id, n_drifting * 3),
        ).fetchall()
    except sqlite3.OperationalError:
        drift_cands = []
    drift_list = sorted(
        [r for r in drift_cands if r[0] not in picked],
        key=lambda r: _recent_review_key(r[0]),
    )[:n_drifting]
    for cid, drift_cos, mc_before in drift_list:
        s = _build_sample_row(
            conn, cluster_id=cid, stratum="drifting",
            drift_cosine=drift_cos, member_count_then=mc_before,
            current_scan_id=scan_id,
        )
        if s is not None:
            samples.append(s)
            picked.add(cid)

    # ── recently_split: fission events in this or previous scan ──
    try:
        split_cands = conn.execute(
            """
            SELECT DISTINCT parent_cluster_id FROM cluster_fission_events
             WHERE scan_id = ?
             LIMIT ?
            """,
            (scan_id, n_recently_split * 3),
        ).fetchall()
    except sqlite3.OperationalError:
        split_cands = []
    split_ids = sorted(
        [r[0] for r in split_cands if r[0] not in picked],
        key=_recent_review_key,
    )[:n_recently_split]
    for cid in split_ids:
        s = _build_sample_row(
            conn, cluster_id=cid, stratum="recently_split", current_scan_id=scan_id,
        )
        if s is not None:
            samples.append(s)
            picked.add(cid)

    # ── new: discovered_scan_id = current scan ──
    new_cands = conn.execute(
        """
        SELECT cluster_id FROM canonical_clusters
         WHERE discovered_scan_id = ?
         LIMIT ?
        """,
        (scan_id, n_new * 3),
    ).fetchall()
    new_ids = sorted(
        [r[0] for r in new_cands if r[0] not in picked],
        key=_recent_review_key,
    )[:n_new]
    for cid in new_ids:
        s = _build_sample_row(
            conn, cluster_id=cid, stratum="new", current_scan_id=scan_id,
        )
        if s is not None:
            samples.append(s)
            picked.add(cid)

    return samples


def call_llm_for_regression(
    samples: list[ClusterSample], llm_client,
    *, prompt_template: Optional[str] = None, timeout: int = 240,
) -> tuple[dict[str, dict], str]:
    """Invoke the LLM with all samples in one call.

    Returns ({cluster_id: {verdict_score, diagnosis, recommended_action}}, raw_text).
    Empty dict if the call fails or the client is None.
    """
    if not samples or llm_client is None:
        return {}, ""

    tpl = prompt_template if prompt_template is not None else _load_prompt_template()
    payload = json.dumps(
        [s.to_prompt_obj() for s in samples], ensure_ascii=False, indent=2,
    )
    prompt = tpl.replace("{clusters_json}", payload)

    try:
        raw = llm_client.generate(prompt, timeout=timeout)
    except Exception as exc:
        logger.warning("Regression LLM call failed: %s", exc)
        return {}, ""

    parsed = _extract_json_object(raw or "")
    if not parsed:
        logger.warning("Regression: unparseable JSON; raw head=%r", (raw or "")[:200])
        return {}, raw or ""

    out: dict[str, dict] = {}
    for item in parsed.get("verdicts") or []:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("cluster_id") or "").strip()
        if not cid:
            continue
        try:
            score = float(item.get("verdict_score"))
        except Exception:
            continue
        action = str(item.get("recommended_action") or "none")
        if action not in ("none", "rename", "split", "merge", "retire"):
            action = "none"
        out[cid] = {
            "verdict_score": max(1.0, min(5.0, score)),
            "diagnosis": str(item.get("diagnosis") or "")[:1000],
            "recommended_action": action,
        }
    return out, (raw or "")


def _prior_verdict_score(conn, cluster_id: str) -> Optional[float]:
    """Most recent cluster_regression_reports score for this cluster."""
    try:
        row = conn.execute(
            """
            SELECT verdict_score FROM cluster_regression_reports
             WHERE cluster_id = ?
             ORDER BY created_at DESC
             LIMIT 1
            """,
            (cluster_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None or row[0] is None:
        return None
    try:
        return float(row[0])
    except Exception:
        return None


def _write_report(
    conn, *, scan_id: str, verdict: RegressionVerdict,
) -> None:
    conn.execute(
        """
        INSERT INTO cluster_regression_reports
          (report_id, scan_id, cluster_id, stratum,
           verdict_score, prev_verdict_score,
           diagnosis_text, recommended_action,
           sample_ticket_ids_json, confirmed_degradation)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uuid.uuid4().hex, scan_id, verdict.cluster_id, verdict.stratum,
            verdict.verdict_score, verdict.prev_verdict_score,
            verdict.diagnosis, verdict.recommended_action,
            json.dumps(verdict.sample_ticket_ids) if verdict.sample_ticket_ids else None,
            1 if verdict.confirmed_degradation else 0,
        ),
    )


# ──────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────

_LOW_SCORE_THRESHOLD = 3.0


def run_regression(
    conn: sqlite3.Connection, scan_id: str, *,
    llm_client=None,
    n_stable: int = 10, n_drifting: int = 10,
    n_recently_split: int = 5, n_new: int = 5,
    prompt_path: Optional[Path] = None,
    persist: bool = True,
) -> RegressionResult:
    """Sample clusters, rate with Gemini, apply 2-scan confirmation rule,
    persist to cluster_regression_reports.

    Returns a RegressionResult summarizing what landed. When `llm_client`
    is None the function still samples and returns a RegressionResult with
    `verdict_count=0` — useful for audit visibility even without an LLM.
    """
    t0 = time.perf_counter()
    samples = stratified_sample(
        conn, scan_id,
        n_stable=n_stable, n_drifting=n_drifting,
        n_recently_split=n_recently_split, n_new=n_new,
    )
    stratum_counts: dict[str, int] = {}
    for s in samples:
        stratum_counts[s.stratum] = stratum_counts.get(s.stratum, 0) + 1

    if llm_client is None:
        wall = int((time.perf_counter() - t0) * 1000)
        return RegressionResult(
            scan_id=scan_id,
            sampled_count=len(samples),
            verdict_count=0,
            confirmed_degradation_count=0,
            stratum_counts=stratum_counts,
            verdicts=[],
            wall_time_ms=wall,
            raw_output="",
        )

    tpl = _load_prompt_template(prompt_path)
    verdicts_by_cid, raw_output = call_llm_for_regression(
        samples, llm_client, prompt_template=tpl,
    )

    verdicts: list[RegressionVerdict] = []
    confirmed = 0
    for s in samples:
        v = verdicts_by_cid.get(s.cluster_id)
        if v is None:
            continue
        prev = _prior_verdict_score(conn, s.cluster_id)
        is_confirmed = bool(
            prev is not None
            and prev < _LOW_SCORE_THRESHOLD
            and v["verdict_score"] < _LOW_SCORE_THRESHOLD
        )
        rv = RegressionVerdict(
            cluster_id=s.cluster_id,
            stratum=s.stratum,
            verdict_score=float(v["verdict_score"]),
            diagnosis=v["diagnosis"],
            recommended_action=v["recommended_action"],
            prev_verdict_score=prev,
            confirmed_degradation=is_confirmed,
            sample_ticket_ids=list(s.sample_ticket_ids),
        )
        verdicts.append(rv)
        if is_confirmed:
            confirmed += 1
        if persist:
            _write_report(conn, scan_id=scan_id, verdict=rv)

    if persist:
        conn.commit()

    wall = int((time.perf_counter() - t0) * 1000)
    logger.info(
        "Phase 6 regression scan=%s: sampled=%d verdicts=%d confirmed=%d wall=%dms",
        scan_id, len(samples), len(verdicts), confirmed, wall,
    )
    return RegressionResult(
        scan_id=scan_id,
        sampled_count=len(samples),
        verdict_count=len(verdicts),
        confirmed_degradation_count=confirmed,
        stratum_counts=stratum_counts,
        verdicts=verdicts,
        wall_time_ms=wall,
        raw_output=raw_output,
    )
