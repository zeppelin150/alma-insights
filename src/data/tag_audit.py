"""Tag audit — Phase 9 "canonicalization audits tags" capability.

Plan §9.3: tags are applied by event and are often wrong. Canonicalization
gives us a ground-truth reference: given a tag, compare the canonical
clusters that tagged tickets map to against the tag's *expected* cluster
(recorded in `incidents.expected_canonical_cluster`). Tickets that land
in a different cluster than the expected one are surfaced as "likely
mis-tagged", ranked by cosine distance from the expected centroid.

Public API:

    audit_tag_correlation(conn, tag: str, *, top_k: int = 10) -> dict

Returns (schema is LLM-facing, keep field names stable):

    {
      "tag": "<input tag>",
      "total_tagged_tickets": int,
      "tagged_with_assignment": int,
      "tagged_unassigned": int,
      "expected_cluster_id": str | None,
      "expected_cluster_label": str | None,
      "incident_description": str | None,
      "concept_distribution": [
        {"concept_id", "concept_label", "ticket_count", "pct_of_tagged"}
      ],
      "cluster_distribution": [
        {"cluster_id", "cluster_label", "ticket_count", "pct_of_tagged"}
      ],
      "likely_mistagged": [     # empty when expected_cluster_id is None
        {"ticket_id", "assigned_cluster_id", "assigned_label",
         "cosine_distance_to_expected", "subject"}
      ],
    }
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_DEFAULT_TOP_K = 10
_MAX_TOP_K = 50
_SNIPPET_MAX_CHARS = 240


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _blob_to_vec(blob) -> Optional[np.ndarray]:
    if blob is None:
        return None
    try:
        v = np.frombuffer(blob, dtype=np.float32).copy()
        if v.size == 0:
            return None
        n = float(np.linalg.norm(v))
        if n == 0.0:
            return v
        return v / n
    except Exception:
        return None


def _lookup_expected_cluster(conn, tag: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (expected_cluster_id, cluster_label, incident_description)
    from the `incidents` table. Matches by tag EQUALING the incident_id OR
    by substring match on tag within incident_id (incidents are keyed by
    tag-like slugs in the test data, e.g. 'incident_portal_outage_0301').

    Returns (None, None, None) if the tag has no expected cluster
    association — the caller then skips the mis-tag ranking."""
    try:
        row = conn.execute(
            """
            SELECT i.expected_canonical_cluster, cc.canonical_label, i.description
              FROM incidents i
              LEFT JOIN canonical_clusters cc
                ON cc.cluster_id = i.expected_canonical_cluster
             WHERE LOWER(i.incident_id) = LOWER(?)
                OR LOWER(?) LIKE '%' || LOWER(i.incident_id) || '%'
             LIMIT 1
            """,
            (tag, tag),
        ).fetchone()
    except sqlite3.OperationalError:
        return None, None, None
    if row is None:
        return None, None, None
    return (
        (row[0] if row[0] else None),
        (row[1] if row[1] else None),
        (row[2] if row[2] else None),
    )


def _tagged_ticket_ids(conn, tag: str) -> tuple[list[str], str]:
    """Return ticket_ids that carry the given tag AND the source used.

    Primary source: ticket_tags table (the canonical home of
    cross-cutting tags applied by enrichment pipelines).

    Fallback: ticket_index.friction_type (bug-bash 2026-04-23 F-8).
    Many production DBs never had the ticket_tags pipeline run but do
    have friction_type populated on ticket_index. Without this fallback
    audit_tag_correlation always reports 'no tickets' for tags like
    'incorrect_charge' / 'feature_broken' that live in friction_type.

    Other possible locations (kept for future expansion):
    - ticket_index.tags (JSON string of tags per ticket)
    - tickets.tags (Zendesk-style tag list)
    """
    try:
        rows = conn.execute(
            "SELECT DISTINCT ticket_id FROM ticket_tags WHERE tag = ?",
            (tag,),
        ).fetchall()
    except Exception:
        rows = []
    ids = [r[0] for r in rows]
    if ids:
        return ids, "ticket_tags"

    # Fallback: friction_type on ticket_index
    try:
        rows = conn.execute(
            "SELECT DISTINCT ticket_id FROM ticket_index WHERE friction_type = ?",
            (tag,),
        ).fetchall()
    except Exception:
        rows = []
    ids = [r[0] for r in rows]
    if ids:
        return ids, "ticket_index.friction_type"

    return [], "ticket_tags"  # empty, but indicate primary source attempted


def _concept_distribution(conn, ticket_ids: list[str]) -> list[dict]:
    """Group tagged tickets by concept (fallback to cluster when concept is NULL)."""
    if not ticket_ids:
        return []
    placeholders = ",".join("?" * len(ticket_ids))
    rows = conn.execute(
        f"""
        SELECT
          COALESCE(cp.concept_id, 'cluster:' || cc.cluster_id) AS cid_out,
          COALESCE(cp.concept_label, cc.canonical_label)       AS clabel_out,
          COUNT(DISTINCT ti.ticket_id)                         AS ticket_count
        FROM ticket_index ti
        LEFT JOIN canonical_clusters cc ON cc.cluster_id = ti.canonical_issue_id
        LEFT JOIN canonical_concepts  cp ON cp.concept_id = cc.concept_id
        WHERE ti.ticket_id IN ({placeholders})
          AND ti.canonical_issue_id IS NOT NULL
        GROUP BY COALESCE(cp.concept_id, 'cluster:' || cc.cluster_id)
        ORDER BY ticket_count DESC, clabel_out ASC
        """,
        tuple(ticket_ids),
    ).fetchall()
    total = sum(int(r[2] or 0) for r in rows) or 1
    return [
        {
            "concept_id": r[0],
            "concept_label": r[1],
            "ticket_count": int(r[2]),
            "pct_of_tagged": round(int(r[2]) / total, 4),
        }
        for r in rows
    ]


def _cluster_distribution(conn, ticket_ids: list[str]) -> list[dict]:
    if not ticket_ids:
        return []
    placeholders = ",".join("?" * len(ticket_ids))
    rows = conn.execute(
        f"""
        SELECT
          cc.cluster_id              AS cluster_id,
          cc.canonical_label         AS cluster_label,
          COUNT(DISTINCT ti.ticket_id) AS ticket_count
        FROM ticket_index ti
        JOIN canonical_clusters cc ON cc.cluster_id = ti.canonical_issue_id
        WHERE ti.ticket_id IN ({placeholders})
        GROUP BY cc.cluster_id
        ORDER BY ticket_count DESC, cluster_label ASC
        """,
        tuple(ticket_ids),
    ).fetchall()
    total = sum(int(r[2] or 0) for r in rows) or 1
    return [
        {
            "cluster_id": r[0],
            "cluster_label": r[1],
            "ticket_count": int(r[2]),
            "pct_of_tagged": round(int(r[2]) / total, 4),
        }
        for r in rows
    ]


def _load_cluster_centroid(conn, cluster_id: str) -> Optional[np.ndarray]:
    row = conn.execute(
        "SELECT centroid_blob FROM canonical_clusters WHERE cluster_id = ?",
        (cluster_id,),
    ).fetchone()
    if row is None:
        return None
    return _blob_to_vec(row[0])


def _rank_mistagged(
    conn, *, tagged_ticket_ids: list[str],
    expected_cluster_id: str, top_k: int,
) -> list[dict]:
    """Return the `top_k` tickets whose assigned cluster differs from
    `expected_cluster_id`, ranked by cosine distance from expected centroid
    (higher distance = more suspicious)."""
    if not tagged_ticket_ids:
        return []
    expected_centroid = _load_cluster_centroid(conn, expected_cluster_id)
    if expected_centroid is None:
        return []

    # Pull tickets + embeddings + assigned cluster label for all tagged
    # tickets whose assigned cluster_id differs from the expected one.
    placeholders = ",".join("?" * len(tagged_ticket_ids))
    rows = conn.execute(
        f"""
        SELECT ti.ticket_id, ti.canonical_issue_id,
               cc.canonical_label, ti.subject_sanitized,
               te.embedding_blob
        FROM ticket_index ti
        LEFT JOIN canonical_clusters cc ON cc.cluster_id = ti.canonical_issue_id
        LEFT JOIN ticket_embeddings te ON te.ticket_id = ti.ticket_id
        WHERE ti.ticket_id IN ({placeholders})
          AND (ti.canonical_issue_id IS NULL
               OR ti.canonical_issue_id != ?)
        """,
        tuple(tagged_ticket_ids) + (expected_cluster_id,),
    ).fetchall()

    scored: list[dict] = []
    for tid, cid, label, subject, emb_blob in rows:
        emb = _blob_to_vec(emb_blob)
        if emb is None:
            cos_dist = None
        else:
            cos = float(emb @ expected_centroid)
            # Cosine distance: clamp to [0, 2] for numerical safety
            cos_dist = max(0.0, min(2.0, 1.0 - cos))
        scored.append({
            "ticket_id": tid,
            "assigned_cluster_id": cid,
            "assigned_label": label,
            "cosine_distance_to_expected": cos_dist,
            "subject": str(subject)[:_SNIPPET_MAX_CHARS] if subject else None,
        })

    # Sort so tickets WITHOUT an embedding go last; highest cos distance first
    scored.sort(
        key=lambda x: (
            0 if x["cosine_distance_to_expected"] is not None else 1,
            -(x["cosine_distance_to_expected"] or 0.0),
        )
    )
    return scored[:top_k]


# ──────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────

def audit_tag_correlation(
    conn: sqlite3.Connection,
    tag: str,
    *,
    top_k: int = _DEFAULT_TOP_K,
) -> dict:
    """Audit a tag against canonical clusters/concepts.

    Args:
        conn: SQLite connection.
        tag: The tag name to audit (e.g. 'incident_portal_outage_0301').
        top_k: Max mis-tagged tickets to surface (1-50).

    Returns a dict with the schema documented in this module's docstring.
    An invalid input returns {"error": ...} rather than raising.
    """
    if not tag or not isinstance(tag, str):
        return {"error": "tag is required (non-empty string)"}
    top_k = max(1, min(int(top_k), _MAX_TOP_K))

    tagged_ids, tag_source = _tagged_ticket_ids(conn, tag)
    total = len(tagged_ids)

    expected_cid, expected_label, incident_desc = _lookup_expected_cluster(conn, tag)

    # Partition into assigned / unassigned
    if tagged_ids:
        placeholders = ",".join("?" * len(tagged_ids))
        with_assignment_count = conn.execute(
            f"""
            SELECT COUNT(*) FROM ticket_index
             WHERE ticket_id IN ({placeholders})
               AND canonical_issue_id IS NOT NULL
            """,
            tuple(tagged_ids),
        ).fetchone()[0]
    else:
        with_assignment_count = 0

    concept_dist = _concept_distribution(conn, tagged_ids)
    cluster_dist = _cluster_distribution(conn, tagged_ids)

    likely_mistagged: list[dict] = []
    if expected_cid:
        likely_mistagged = _rank_mistagged(
            conn, tagged_ticket_ids=tagged_ids,
            expected_cluster_id=expected_cid, top_k=top_k,
        )

    return {
        "tag": tag,
        "tag_source": tag_source,
        "total_tagged_tickets": total,
        "tagged_with_assignment": int(with_assignment_count),
        "tagged_unassigned": total - int(with_assignment_count),
        "expected_cluster_id": expected_cid,
        "expected_cluster_label": expected_label,
        "incident_description": incident_desc,
        "concept_distribution": concept_dist,
        "cluster_distribution": cluster_dist,
        "likely_mistagged": likely_mistagged,
    }


# ──────────────────────────────────────────────────────────────────────
# MCP/chat-tool adapter
# ──────────────────────────────────────────────────────────────────────

def handle_audit_tag_correlation(conn, args: dict, session_filters=None) -> dict:
    """Adapter for the chat-tools dispatch contract."""
    tag = args.get("tag") if args else None
    top_k = args.get("top_k", _DEFAULT_TOP_K) if args else _DEFAULT_TOP_K
    try:
        return audit_tag_correlation(conn, tag, top_k=int(top_k))
    except sqlite3.OperationalError as exc:
        logger.warning("audit_tag_correlation SQL error: %s", exc)
        return {"error": f"Audit failed: {exc}"}
