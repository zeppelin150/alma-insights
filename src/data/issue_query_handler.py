"""Issue query handler — Phase 9 scope-aware `query_issues` tool.

Unified replacement for `query_entities` + `query_ticket_classifications`
+ `query_findings`. Takes explicit scope filters (trc, payer, provider,
state, date_range, concept_id, tag), aggregates `ticket_index` joined
to `canonical_clusters` / `canonical_concepts`, and returns a ranked
list of top issues with body snippets.

Kernel spec (phase-6-9-session-kernel.md §"Session N+2"):

    query_issues(
        trc: str | None = None,
        payer: str | None = None,
        state: str | None = None,
        date_range: str | None = None,    # "YYYY-MM-DD/YYYY-MM-DD"
        group_by: str = "concept",        # concept | cluster | trc | payer | provider
        limit: int = 10,
    ) -> dict

Returns:
    {
      "scope": {"filters": {...applied...}, "total_tickets": N},
      "group_by": "concept",
      "top_issues": [
        {rank, group_id, label, ticket_count, pct_of_scope,
         sample_ticket_ids, example_snippet, trcs_touched}, ...
      ],
    }

Plan reference: canonicalization-enrichment.md §9.1.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────

_VALID_GROUPS = {"concept", "cluster", "trc", "payer", "provider"}
_DEFAULT_LIMIT = 10
_MAX_LIMIT = 50
_DEFAULT_SAMPLE_SIZE = 5
_MAX_SAMPLE_SIZE = 10
_SNIPPET_MAX_CHARS = 240


@dataclass
class QueryIssueFilters:
    """Normalized filter bag — the single source of truth for what scope
    a query_issues call applied. Serialized back into the response as
    scope.filters so downstream LLMs can report *exactly* what was filtered."""
    trc: Optional[str] = None
    payer: Optional[str] = None
    provider: Optional[str] = None
    state: Optional[str] = None
    date_start: Optional[str] = None
    date_end: Optional[str] = None
    concept_id: Optional[str] = None
    tag: Optional[str] = None

    def as_dict(self) -> dict:
        """Only populated filter fields, for transparent scope reporting."""
        out = {}
        if self.trc is not None:
            out["trc"] = self.trc
        if self.payer is not None:
            out["payer"] = self.payer
        if self.provider is not None:
            out["provider"] = self.provider
        if self.state is not None:
            out["state"] = self.state
        if self.date_start is not None or self.date_end is not None:
            out["date_range"] = f"{self.date_start or ''}/{self.date_end or ''}"
        if self.concept_id is not None:
            out["concept_id"] = self.concept_id
        if self.tag is not None:
            out["tag"] = self.tag
        return out


# ──────────────────────────────────────────────────────────────────────
# Filter parsing
# ──────────────────────────────────────────────────────────────────────

def _parse_date_range(date_range: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Accept 'YYYY-MM-DD/YYYY-MM-DD', 'YYYY-MM-DD/', '/YYYY-MM-DD', or None.
    Returns (start, end) — both 10-char date strings or None."""
    if not date_range:
        return None, None
    if "/" not in date_range:
        # Single date treated as both bounds
        d = date_range.strip()[:10] or None
        return d, d
    parts = date_range.split("/", 1)
    start = parts[0].strip()[:10] or None
    end = parts[1].strip()[:10] or None
    return start, end


def _normalize_filters(
    *, trc, payer, provider, state, date_range, concept_id, tag,
) -> QueryIssueFilters:
    start, end = _parse_date_range(date_range)
    return QueryIssueFilters(
        trc=str(trc).strip() if trc else None,
        payer=str(payer).strip() if payer else None,
        provider=str(provider).strip() if provider else None,
        state=str(state).strip() if state else None,
        date_start=start,
        date_end=end,
        concept_id=str(concept_id).strip() if concept_id else None,
        tag=str(tag).strip() if tag else None,
    )


# ──────────────────────────────────────────────────────────────────────
# SQL composition
# ──────────────────────────────────────────────────────────────────────

# Group-by → (id_expr, label_expr)
_GROUP_EXPRESSIONS: dict[str, tuple[str, str]] = {
    # For concept: fall back to cluster id when concept_id is NULL so
    # unlinked clusters aren't silently dropped (matches the pattern used
    # by score_golden_set_by_concept).
    "concept": (
        "COALESCE(cp.concept_id, 'cluster:' || cc.cluster_id)",
        "COALESCE(cp.concept_label, cc.canonical_label)",
    ),
    "cluster": ("cc.cluster_id", "cc.canonical_label"),
    "trc":     ("ti.trc_code",   "COALESCE(ti.trc_label, ti.trc_code)"),
    "payer":   ("ti.insurance_payer", "ti.insurance_payer"),
    "provider":("ti.provider_id", "ti.provider_id"),
}


def _build_where(f: QueryIssueFilters) -> tuple[list[str], list[Any]]:
    """Return (where_fragments, params). No leading AND on any fragment."""
    parts: list[str] = []
    params: list[Any] = []

    # Require an assignment for grouping by concept/cluster — tickets with
    # NULL canonical_issue_id would produce NULL group_ids that the LLM
    # can't act on. Other groupings keep all tickets.
    # (This condition applies only when we JOIN the cluster tables — the
    # caller decides via _needs_cluster_join.)

    if f.trc is not None:
        # Case-insensitive exact match on trc_code OR trc_label
        parts.append("(LOWER(ti.trc_code) = LOWER(?) OR LOWER(COALESCE(ti.trc_label, '')) = LOWER(?))")
        params.extend([f.trc, f.trc])
    if f.payer is not None:
        parts.append("LOWER(COALESCE(ti.insurance_payer, '')) LIKE LOWER(?)")
        params.append(f"%{f.payer}%")
    if f.provider is not None:
        parts.append("COALESCE(ti.provider_id, '') = ?")
        params.append(f.provider)
    if f.state is not None:
        parts.append("LOWER(COALESCE(ti.service_state, '')) = LOWER(?)")
        params.append(f.state)
    if f.date_start is not None:
        parts.append("SUBSTR(ti.ticket_created_date, 1, 10) >= ?")
        params.append(f.date_start)
    if f.date_end is not None:
        parts.append("SUBSTR(ti.ticket_created_date, 1, 10) <= ?")
        params.append(f.date_end)
    if f.concept_id is not None:
        parts.append("cp.concept_id = ?")
        params.append(f.concept_id)
    if f.tag is not None:
        # EXISTS keeps the row count correct (multiple tags per ticket)
        parts.append("EXISTS (SELECT 1 FROM ticket_tags tt WHERE tt.ticket_id = ti.ticket_id AND tt.tag = ?)")
        params.append(f.tag)

    return parts, params


def _needs_cluster_join(group_by: str, f: QueryIssueFilters) -> bool:
    """Is a JOIN to canonical_clusters required for this query?"""
    if group_by in ("concept", "cluster"):
        return True
    if f.concept_id is not None:
        return True
    return False


def _needs_concept_join(group_by: str, f: QueryIssueFilters) -> bool:
    if group_by == "concept":
        return True
    if f.concept_id is not None:
        return True
    return False


def _compose_scope_sql(
    group_by: str, f: QueryIssueFilters, *, limit: int,
) -> tuple[str, str, str, list[Any]]:
    """Build the group-by SQL + a "total_tickets" SQL sharing the same scope.

    Returns (group_sql, total_sql, group_id_expr_for_samples, params).
    """
    id_expr, label_expr = _GROUP_EXPRESSIONS[group_by]

    joins = []
    if _needs_cluster_join(group_by, f):
        joins.append("LEFT JOIN canonical_clusters cc ON cc.cluster_id = ti.canonical_issue_id")
    if _needs_concept_join(group_by, f):
        joins.append("LEFT JOIN canonical_concepts cp ON cp.concept_id = cc.concept_id")
    joins_sql = "\n".join(joins)

    where_parts, params = _build_where(f)

    # For concept/cluster groupings, drop tickets that have no canonical
    # assignment — they'd otherwise become a NULL group_id the LLM can't
    # act on, and would distort pct_of_scope. For trc/payer/provider
    # groupings we keep all tickets (the grouping dimension is a
    # ticket-intrinsic attribute, not a canonicalization output).
    if group_by in ("concept", "cluster"):
        where_parts.insert(0, "ti.canonical_issue_id IS NOT NULL")

    where_clause = " AND ".join(where_parts) if where_parts else "1=1"

    group_sql = f"""
        SELECT
          {id_expr} AS group_id,
          {label_expr} AS label,
          COUNT(DISTINCT ti.ticket_id) AS ticket_count
        FROM ticket_index ti
        {joins_sql}
        WHERE {where_clause}
        GROUP BY {id_expr}
        HAVING COUNT(DISTINCT ti.ticket_id) > 0
        ORDER BY ticket_count DESC, label ASC
        LIMIT ?
    """.strip()

    total_sql = f"""
        SELECT COUNT(DISTINCT ti.ticket_id) AS total
        FROM ticket_index ti
        {joins_sql}
        WHERE {where_clause}
    """.strip()

    return group_sql, total_sql, id_expr, params


# ──────────────────────────────────────────────────────────────────────
# Sample fetching
# ──────────────────────────────────────────────────────────────────────

def _fetch_samples_for_group(
    conn: sqlite3.Connection,
    *, group_id_expr: str, group_id_value: Any,
    f: QueryIssueFilters, sample_size: int,
) -> tuple[list[str], Optional[str]]:
    """Return (sample_ticket_ids, example_snippet) for one group.

    Reuses the same filter scope as the parent query so samples are in-scope."""
    joins = [
        "LEFT JOIN canonical_clusters cc ON cc.cluster_id = ti.canonical_issue_id",
        "LEFT JOIN canonical_concepts cp ON cp.concept_id = cc.concept_id",
    ]
    joins_sql = "\n".join(joins)
    where_parts, params = _build_where(f)
    where_parts.append(f"{group_id_expr} = ?")
    params.append(group_id_value)
    where_sql = " AND ".join(where_parts)

    sql = f"""
        SELECT ti.ticket_id, ti.subject_sanitized
        FROM ticket_index ti
        {joins_sql}
        WHERE {where_sql}
        ORDER BY ti.ticket_created_date DESC, ti.ticket_id ASC
        LIMIT ?
    """
    rows = conn.execute(sql, params + [sample_size]).fetchall()
    tids = [str(r[0]) for r in rows]
    snippet = None
    for r in rows:
        if r[1]:
            snippet = str(r[1])[:_SNIPPET_MAX_CHARS]
            break
    return tids, snippet


def _fetch_trcs_touched_for_group(
    conn: sqlite3.Connection,
    *, group_id_expr: str, group_id_value: Any,
    f: QueryIssueFilters,
) -> list[str]:
    """Return the sorted list of distinct TRCs that members of this group
    touch, subject to the same filter scope."""
    joins = [
        "LEFT JOIN canonical_clusters cc ON cc.cluster_id = ti.canonical_issue_id",
        "LEFT JOIN canonical_concepts cp ON cp.concept_id = cc.concept_id",
    ]
    joins_sql = "\n".join(joins)
    where_parts, params = _build_where(f)
    where_parts.append(f"{group_id_expr} = ?")
    params.append(group_id_value)
    where_sql = " AND ".join(where_parts)
    sql = f"""
        SELECT DISTINCT ti.trc_code
        FROM ticket_index ti
        {joins_sql}
        WHERE {where_sql} AND ti.trc_code IS NOT NULL
        ORDER BY ti.trc_code
    """
    return [r[0] for r in conn.execute(sql, params).fetchall()]


# ──────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────

def query_issues(
    conn: sqlite3.Connection,
    *,
    trc: Optional[str] = None,
    payer: Optional[str] = None,
    provider: Optional[str] = None,
    state: Optional[str] = None,
    date_range: Optional[str] = None,
    concept_id: Optional[str] = None,
    tag: Optional[str] = None,
    group_by: str = "concept",
    limit: int = _DEFAULT_LIMIT,
    include_samples: bool = True,
    sample_size: int = _DEFAULT_SAMPLE_SIZE,
) -> dict:
    """Unified scope-aware issue query.

    Validates + normalizes filters, runs a single aggregation query,
    optionally hydrates samples per group, and returns a transparent
    structured result the chat LLM can't misinterpret.

    Raises ValueError on invalid group_by or limit.
    """
    if group_by not in _VALID_GROUPS:
        raise ValueError(
            f"Invalid group_by={group_by!r}. Valid: {sorted(_VALID_GROUPS)}"
        )
    limit = max(1, min(int(limit), _MAX_LIMIT))
    sample_size = max(1, min(int(sample_size), _MAX_SAMPLE_SIZE))

    f = _normalize_filters(
        trc=trc, payer=payer, provider=provider, state=state,
        date_range=date_range, concept_id=concept_id, tag=tag,
    )

    group_sql, total_sql, id_expr, params = _compose_scope_sql(
        group_by, f, limit=limit,
    )

    total_tickets = int(conn.execute(total_sql, params).fetchone()[0] or 0)
    rows = conn.execute(group_sql, params + [limit]).fetchall()

    top_issues: list[dict] = []
    for rank, r in enumerate(rows, start=1):
        group_id = r[0]
        label = r[1]
        ticket_count = int(r[2] or 0)
        pct = (ticket_count / total_tickets) if total_tickets else 0.0

        sample_tids: list[str] = []
        snippet: Optional[str] = None
        if include_samples and group_id is not None:
            sample_tids, snippet = _fetch_samples_for_group(
                conn, group_id_expr=id_expr, group_id_value=group_id,
                f=f, sample_size=sample_size,
            )

        issue: dict = {
            "rank": rank,
            "group_id": group_id,
            "label": label,
            "ticket_count": ticket_count,
            "pct_of_scope": round(pct, 4),
            "sample_ticket_ids": sample_tids,
            "example_snippet": snippet,
        }
        # Only hydrate trcs_touched for issue-level groupings where it's
        # informative — for group_by=trc it'd just be the row's own TRC.
        if group_by in ("concept", "cluster"):
            issue["trcs_touched"] = _fetch_trcs_touched_for_group(
                conn, group_id_expr=id_expr, group_id_value=group_id, f=f,
            )
        top_issues.append(issue)

    return {
        "scope": {
            "filters": f.as_dict(),
            "total_tickets": total_tickets,
        },
        "group_by": group_by,
        "top_issues": top_issues,
    }


# ──────────────────────────────────────────────────────────────────────
# MCP/chat-tool adapter
# ──────────────────────────────────────────────────────────────────────

def handle_query_issues(conn, args: dict, session_filters: Optional[dict] = None) -> dict:
    """Adapter for the chat-tools dispatch contract.

    Accepts `args` from the MCP tools/call payload (flat kwargs) plus an
    optional session_filters dict whose keys *override* `args` (per
    existing chat tool convention — session filters are UI-applied, args
    are LLM-provided).

    Any unknown keys in args/session_filters are silently ignored so the
    LLM can't crash the tool with a typo.
    """
    sf = session_filters or {}
    # Session filters have higher precedence by convention.
    merged: dict[str, Any] = {}
    for d in (args or {}, sf):
        for k in ("trc", "payer", "provider", "state", "date_range",
                  "concept_id", "tag", "group_by", "limit",
                  "include_samples", "sample_size"):
            if k in d and d[k] not in (None, ""):
                merged[k] = d[k]

    try:
        return query_issues(conn, **merged)
    except ValueError as exc:
        return {"error": str(exc)}
    except sqlite3.OperationalError as exc:
        logger.warning("query_issues SQL error: %s", exc)
        return {"error": f"Query failed: {exc}"}
