"""Concept linker — Phase 7 of canonicalization-enrichment.md.

Groups `canonical_clusters` rows into higher-level `canonical_concepts` via
one batched Gemini call per scan. Each cluster joins exactly one concept.
Concepts are the *recall layer*: the Phase 3 gate proved that cluster-level
recall caps at ~0.65 on pairwise golden sets because Qwen3 embeds
same-concept tickets into distinct sub-groups on surface wording. Grouping
clusters into concepts re-scores recall at the level the product actually
asks about ("among 47 Thunderbird tickets, top concept is payment
responsibility inaccuracy, 35 tickets").

Public API:
    run_concept_linking(conn, scan_id, *, llm_client) -> ConceptLinkingResult

Persists:
    - canonical_concepts rows (one per concept)
    - canonical_clusters.concept_id (UPDATE)
    - canonical_concept_linking_runs row (audit)

Plan reference: canonicalization-enrichment.md §7 (reinterpreted per
phase-5-7-session-kernel.md: LLM-driven, not agglomerative).
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

_PROMPT_PATH_DEFAULT = (
    Path(__file__).resolve().parents[2] / "config" / "prompts" / "concept_linking.txt"
)

_SNIPPETS_PER_CLUSTER = 3
_KEY_PHRASES_PER_CLUSTER = 8
_SNIPPET_MAX_CHARS = 240

# Cap clusters per LLM call; groups span batches but we reconcile at the
# end (collisions rare because the model sees one shared context).
_LLM_BATCH_MAX_CLUSTERS = 80


# ──────────────────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class ClusterPayload:
    cluster_id: str
    canonical_label: str
    trc: str
    member_count: int
    representative_snippets: list[str] = field(default_factory=list)
    top_key_phrases: list[str] = field(default_factory=list)

    def to_prompt_obj(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "canonical_label": self.canonical_label,
            "trc": self.trc,
            "member_count": self.member_count,
            "representative_snippets": self.representative_snippets[:_SNIPPETS_PER_CLUSTER],
            "top_key_phrases": self.top_key_phrases[:_KEY_PHRASES_PER_CLUSTER],
        }


@dataclass
class ConceptDecision:
    concept_id: str
    concept_label: str
    concept_description: str
    cluster_ids: list[str]
    rationale: str = ""
    confidence: float = 0.0


@dataclass
class ConceptLinkingResult:
    scan_id: str
    cluster_count_input: int
    concept_count_output: int
    unlinked_cluster_count: int
    decisions: list[ConceptDecision] = field(default_factory=list)
    wall_time_ms: int = 0
    raw_output: str = ""

    def summary(self) -> dict:
        return {
            "scan_id": self.scan_id,
            "cluster_count_input": self.cluster_count_input,
            "concept_count_output": self.concept_count_output,
            "unlinked_cluster_count": self.unlinked_cluster_count,
            "wall_time_ms": self.wall_time_ms,
        }


# ──────────────────────────────────────────────────────────────────────────
# Cluster payload construction
# ──────────────────────────────────────────────────────────────────────────


def _safe_json_list(raw) -> list:
    if not raw:
        return []
    try:
        v = json.loads(raw) if isinstance(raw, (bytes, str)) else raw
    except (TypeError, ValueError):
        return []
    return v if isinstance(v, list) else []


def load_cluster_payloads(conn: sqlite3.Connection) -> list[ClusterPayload]:
    """Load one payload per row in canonical_clusters with snippets + phrases."""
    rows = conn.execute(
        """
        SELECT cluster_id, trc, canonical_label, member_count
        FROM canonical_clusters
        WHERE tier IS NULL OR tier NOT IN ('retired', 'split')
        ORDER BY member_count DESC
        """
    ).fetchall()

    payloads: list[ClusterPayload] = []
    for cid, trc, label, mcount in rows:
        phrase_counts: Counter = Counter()
        for (kp_raw,) in conn.execute(
            """
            SELECT c.key_phrases
            FROM nlp_ticket_classifications c
            JOIN ticket_index ti ON ti.ticket_id = c.ticket_id
            WHERE ti.canonical_issue_id = ?
            """,
            (cid,),
        ):
            for phrase in _safe_json_list(kp_raw):
                if isinstance(phrase, str) and phrase.strip():
                    phrase_counts[phrase.strip()] += 1

        snippet_rows = conn.execute(
            """
            SELECT COALESCE(ti.issue_snippet, c.summary, '') AS snip
            FROM ticket_index ti
            LEFT JOIN nlp_ticket_classifications c ON c.ticket_id = ti.ticket_id
            WHERE ti.canonical_issue_id = ?
            ORDER BY ti.classification_confidence DESC
            LIMIT ?
            """,
            (cid, _SNIPPETS_PER_CLUSTER),
        ).fetchall()
        snippets: list[str] = []
        for (snip,) in snippet_rows:
            if not snip:
                continue
            s = str(snip).strip()
            if len(s) > _SNIPPET_MAX_CHARS:
                s = s[:_SNIPPET_MAX_CHARS] + "…"
            snippets.append(s)

        payloads.append(
            ClusterPayload(
                cluster_id=cid,
                canonical_label=label or trc or cid,
                trc=trc or "",
                member_count=mcount or 0,
                representative_snippets=snippets,
                top_key_phrases=[p for p, _ in phrase_counts.most_common(_KEY_PHRASES_PER_CLUSTER)],
            )
        )

    return payloads


# ──────────────────────────────────────────────────────────────────────────
# LLM call + parsing
# ──────────────────────────────────────────────────────────────────────────


def _load_prompt_template(prompt_path: Optional[Path] = None) -> str:
    with open(prompt_path or _PROMPT_PATH_DEFAULT, encoding="utf-8") as f:
        return f.read()


def _extract_json_object(text: str) -> Optional[dict]:
    if not text:
        return None
    stripped = re.sub(r"```(?:json)?\s*", "", text).replace("```", "")
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return None
    try:
        return json.loads(stripped[start:end + 1])
    except (ValueError, TypeError):
        return None


def _slugify(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not s:
        return uuid.uuid4().hex[:12]
    return s[:max_len].rstrip("-")


def call_llm_for_concepts(
    payloads: list[ClusterPayload],
    llm_client,
    *,
    prompt_template: Optional[str] = None,
    timeout: int = 240,
) -> tuple[list[ConceptDecision], str]:
    """Invoke the LLM in batches. Returns (decisions, concatenated_raw_output).

    If the cluster count exceeds `_LLM_BATCH_MAX_CLUSTERS`, we split into
    batches. Cross-batch concept identity is NOT reconciled (concepts from
    different batches are assumed distinct); this is a known limitation for
    very large scans. Current scale (1788 tickets → 19 clusters) fits in one
    call comfortably.
    """
    if not payloads or llm_client is None:
        return [], ""

    tpl = prompt_template if prompt_template is not None else _load_prompt_template()

    all_decisions: list[ConceptDecision] = []
    raw_accum: list[str] = []
    used_ids: set[str] = set()

    for start in range(0, len(payloads), _LLM_BATCH_MAX_CLUSTERS):
        batch = payloads[start:start + _LLM_BATCH_MAX_CLUSTERS]
        clusters_payload = json.dumps(
            [p.to_prompt_obj() for p in batch], ensure_ascii=False, indent=2
        )
        prompt = tpl.replace("{clusters_json}", clusters_payload)

        try:
            raw = llm_client.generate(prompt, timeout=timeout)
        except Exception as exc:
            logger.warning("Concept linking LLM call failed (batch %d): %s", start, exc)
            continue
        raw_accum.append(raw or "")

        parsed = _extract_json_object(raw)
        if not parsed:
            logger.warning("Concept linking: unparseable JSON (batch %d); raw head=%r", start, (raw or "")[:200])
            continue

        concepts_arr = parsed.get("concepts") or []
        if not isinstance(concepts_arr, list):
            logger.warning("Concept linking: missing 'concepts' array")
            continue

        for item in concepts_arr:
            if not isinstance(item, dict):
                continue
            cluster_ids = item.get("cluster_ids") or []
            if not isinstance(cluster_ids, list) or not cluster_ids:
                continue
            cluster_ids = [str(cid).strip() for cid in cluster_ids if cid]
            if not cluster_ids:
                continue

            raw_cid = item.get("concept_id") or ""
            slug_source = raw_cid if raw_cid else (item.get("concept_label") or "")
            cid = _slugify(slug_source)
            # Avoid collisions across batches
            base = cid
            suffix = 1
            while cid in used_ids:
                cid = f"{base}-{suffix}"
                suffix += 1
            used_ids.add(cid)

            all_decisions.append(
                ConceptDecision(
                    concept_id=cid,
                    concept_label=(item.get("concept_label") or "").strip() or cid,
                    concept_description=(item.get("concept_description") or "").strip(),
                    cluster_ids=cluster_ids,
                    rationale=(item.get("rationale") or "").strip(),
                    confidence=float(item.get("confidence") or 0.0),
                )
            )

    return all_decisions, "\n---\n".join(raw_accum)


# ──────────────────────────────────────────────────────────────────────────
# Reconciliation — ensure every cluster_id is assigned exactly once
# ──────────────────────────────────────────────────────────────────────────


def reconcile_decisions(
    decisions: list[ConceptDecision],
    all_cluster_ids: Iterable[str],
) -> tuple[list[ConceptDecision], list[str]]:
    """Guarantee every cluster_id appears in exactly one concept.

    - Duplicates: first occurrence wins, later occurrences are dropped.
    - Orphans (cluster_id in `all_cluster_ids` but no concept): added to a
      catch-all 'unassigned' concept per cluster (each orphan → its own
      concept) so the invariant "every cluster has concept_id" holds.

    Returns (reconciled_decisions, orphan_cluster_ids_as_single_concepts).
    """
    seen: set[str] = set()
    cleaned: list[ConceptDecision] = []
    for d in decisions:
        unique = [cid for cid in d.cluster_ids if cid not in seen]
        if not unique:
            continue
        seen.update(unique)
        cleaned.append(
            ConceptDecision(
                concept_id=d.concept_id,
                concept_label=d.concept_label,
                concept_description=d.concept_description,
                cluster_ids=unique,
                rationale=d.rationale,
                confidence=d.confidence,
            )
        )

    all_ids = [cid for cid in all_cluster_ids]
    orphans = [cid for cid in all_ids if cid not in seen]
    orphan_decisions: list[ConceptDecision] = []
    for cid in orphans:
        slug = _slugify(cid)[:30] or uuid.uuid4().hex[:12]
        orphan_decisions.append(
            ConceptDecision(
                concept_id=f"unlinked-{slug}",
                concept_label="Unlinked cluster",
                concept_description="No concept grouping produced by the LLM; cluster stands alone.",
                cluster_ids=[cid],
                rationale="orphan (no LLM concept assignment)",
                confidence=0.0,
            )
        )

    return cleaned + orphan_decisions, orphans


# ──────────────────────────────────────────────────────────────────────────
# Persistence
# ──────────────────────────────────────────────────────────────────────────


def _write_concepts(
    conn: sqlite3.Connection,
    decisions: list[ConceptDecision],
    scan_id: str,
) -> None:
    # Roll up per-cluster metadata for trcs_touched and lifetime_tickets
    cluster_meta_rows = conn.execute(
        "SELECT cluster_id, trc, member_count, lifetime_tickets FROM canonical_clusters"
    ).fetchall()
    meta_by_id = {r[0]: (r[1], r[2] or 0, r[3] or 0) for r in cluster_meta_rows}

    # Insert/replace concepts + update clusters
    for d in decisions:
        trcs: set[str] = set()
        tickets = 0
        for cid in d.cluster_ids:
            m = meta_by_id.get(cid)
            if m:
                if m[0]:
                    trcs.add(m[0])
                tickets += max(m[1], m[2])
        conn.execute(
            """
            INSERT OR REPLACE INTO canonical_concepts
              (concept_id, concept_label, concept_description, member_cluster_count,
               lifetime_tickets, trcs_touched_json, first_seen_scan_id,
               last_seen_scan_id, last_seen_at, source, llm_confidence, rationale)
            VALUES (?, ?, ?, ?, ?, ?,
                    COALESCE((SELECT first_seen_scan_id FROM canonical_concepts WHERE concept_id=?), ?),
                    ?, CURRENT_TIMESTAMP, ?, ?, ?)
            """,
            (
                d.concept_id, d.concept_label, d.concept_description,
                len(d.cluster_ids), tickets,
                json.dumps(sorted(trcs), ensure_ascii=False),
                d.concept_id, scan_id,
                scan_id,
                "llm" if not d.concept_id.startswith("unlinked-") else "unlinked",
                d.confidence, d.rationale,
            ),
        )

        conn.executemany(
            "UPDATE canonical_clusters SET concept_id = ? WHERE cluster_id = ?",
            [(d.concept_id, cid) for cid in d.cluster_ids],
        )

    conn.commit()


def _write_linking_run(
    conn: sqlite3.Connection,
    *,
    scan_id: str,
    cluster_count_input: int,
    concept_count_output: int,
    unlinked_cluster_count: int,
    wall_time_ms: int,
    raw_output: str,
) -> str:
    run_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO canonical_concept_linking_runs
          (run_id, scan_id, cluster_count_input, concept_count_output,
           unlinked_cluster_count, wall_time_ms, raw_output)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, scan_id, cluster_count_input, concept_count_output,
         unlinked_cluster_count, wall_time_ms, raw_output[:100_000]),
    )
    conn.commit()
    return run_id


# ──────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────


def run_concept_linking(
    conn: sqlite3.Connection,
    scan_id: str,
    *,
    llm_client=None,
    prompt_path: Optional[Path] = None,
    persist: bool = True,
) -> ConceptLinkingResult:
    """Run concept linking end-to-end.

    Args:
        conn: SQLite connection (factory-managed).
        scan_id: Scan identifier for audit trail.
        llm_client: Pre-built LLM client. If None, every cluster becomes
            its own concept (graceful no-op).
        prompt_path: Override default prompt template.
        persist: If False, decisions are computed but not written.

    Returns:
        ConceptLinkingResult with per-concept decisions.
    """
    started = time.perf_counter()

    payloads = load_cluster_payloads(conn)
    all_ids = [p.cluster_id for p in payloads]

    decisions, raw_output = call_llm_for_concepts(
        payloads, llm_client, prompt_template=None if prompt_path is None else _load_prompt_template(prompt_path)
    )
    reconciled, orphans = reconcile_decisions(decisions, all_ids)

    wall_ms = int((time.perf_counter() - started) * 1000)

    if persist and reconciled:
        # Detach all clusters first so stale concept_id values from a prior
        # run don't survive into this one. _write_concepts will re-link.
        conn.execute("UPDATE canonical_clusters SET concept_id = NULL")
        _write_concepts(conn, reconciled, scan_id)
        # Prune canonical_concepts rows that no longer own any cluster.
        conn.execute(
            """
            DELETE FROM canonical_concepts
            WHERE concept_id NOT IN (
                SELECT DISTINCT concept_id FROM canonical_clusters
                WHERE concept_id IS NOT NULL
            )
            """
        )
        conn.commit()
        _write_linking_run(
            conn,
            scan_id=scan_id,
            cluster_count_input=len(all_ids),
            concept_count_output=len(reconciled),
            unlinked_cluster_count=len(orphans),
            wall_time_ms=wall_ms,
            raw_output=raw_output,
        )

    result = ConceptLinkingResult(
        scan_id=scan_id,
        cluster_count_input=len(all_ids),
        concept_count_output=len(reconciled),
        unlinked_cluster_count=len(orphans),
        decisions=reconciled,
        wall_time_ms=wall_ms,
        raw_output=raw_output,
    )
    logger.info(
        "Phase 7: linked %d clusters → %d concepts (orphans=%d) in %.1f s",
        len(all_ids), len(reconciled), len(orphans), wall_ms / 1000.0,
    )
    return result
