"""
Alma Insights — Claude Tool Definitions + Executor (Hardening H4)

Eight tools exposing read-only DB queries for Claude ops-lane tasks.
Each tool wraps an existing query or module function.

Claude receives structured JSON results — NEVER raw ticket text (PHI).

Tools:
    read_scan_summary      — Current or historical scan ledger
    get_friction_gaps       — Gap report from guru_friction_coverage
    get_sub_pattern_trends  — Sub-pattern tier/volume trends
    search_guru_cards       — Search guru_articles by keyword
    get_card_detail         — Single card metadata (no raw content to LLM)
    get_card_relationships  — Cards in same collection / cross-refs
    get_effectiveness_report — Pre/post volume deltas per card
    propose_guru_edit       — Stage a content draft (human-gated)

Usage:
    from src.llm.claude_tools import TOOL_DEFINITIONS, execute_tool
    result = execute_tool("read_scan_summary", {"scan_id": "abc123"}, db)
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("alma.claude_tools")


# ═══════════════════════════════════════════════════════════════════
#  Tool Definitions (Claude Messages API format)
# ═══════════════════════════════════════════════════════════════════

TOOL_DEFINITIONS = [
    {
        "name": "read_scan_summary",
        "description": (
            "Read a structured summary of an NLP scan. Returns ticket counts, "
            "TRC breakdown, sub-patterns, findings, and alerts. "
            "Use mode='current' with a scan_id, or mode='historical' for trends."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["current", "historical"],
                    "description": "current = single scan, historical = multi-scan trends",
                },
                "scan_id": {
                    "type": "string",
                    "description": "Required for mode=current. The scan ID to summarize.",
                },
                "num_scans": {
                    "type": "integer",
                    "description": "For mode=historical. Number of recent scans (default 5).",
                },
            },
            "required": ["mode"],
        },
    },
    {
        "name": "get_friction_gaps",
        "description": (
            "Get friction types with low or missing Guru KB coverage. "
            "Returns friction_type, TRC, lifetime tickets, coverage score, "
            "gap description, and linked card title. Sorted worst-first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "min_tickets": {
                    "type": "integer",
                    "description": "Only include friction types with at least this many tickets.",
                },
            },
        },
    },
    {
        "name": "get_sub_pattern_trends",
        "description": (
            "Get active sub-patterns with tier, volume, and date info. "
            "Useful for identifying emerging or growing friction areas."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "trc_filter": {
                    "type": "string",
                    "description": "Filter to a specific TRC code.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 20).",
                },
            },
        },
    },
    {
        "name": "search_guru_cards",
        "description": (
            "Search Guru KB articles by keyword. Returns card ID, title, "
            "collection, and content hash. Does NOT return article body."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search keyword or phrase.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_card_detail",
        "description": (
            "Get metadata for a single Guru card: title, collection, "
            "friction coverage score, last sync time, and effectiveness data. "
            "Does NOT expose raw article content to the LLM."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "card_id": {
                    "type": "string",
                    "description": "The Guru card ID.",
                },
            },
            "required": ["card_id"],
        },
    },
    {
        "name": "get_card_relationships",
        "description": (
            "Find cards related to a given card: same collection, "
            "shared friction types, or domain overlap."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "card_id": {
                    "type": "string",
                    "description": "The Guru card ID to find relationships for.",
                },
            },
            "required": ["card_id"],
        },
    },
    {
        "name": "get_effectiveness_report",
        "description": (
            "Get pre/post volume deltas for Guru cards that were updated. "
            "Shows whether KB changes reduced ticket volume. "
            "Includes statistical significance (Poisson test)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "propose_guru_edit",
        "description": (
            "Stage a draft edit for a Guru card. This does NOT push to Guru — "
            "it saves a draft that a human must review and approve. "
            "Returns the draft ID for tracking."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "card_id": {
                    "type": "string",
                    "description": "The Guru card to propose edits for.",
                },
                "friction_type": {
                    "type": "string",
                    "description": "The friction type this edit addresses.",
                },
                "draft_title": {
                    "type": "string",
                    "description": "Title for the draft.",
                },
                "draft_content": {
                    "type": "string",
                    "description": "The proposed new content (markdown).",
                },
            },
            "required": ["card_id", "friction_type", "draft_content"],
        },
    },
    {
        "name": "query_issues",
        "description": (
            "Scope-aware issue query over canonical clusters/concepts. Returns "
            "a ranked list of top issues matching the scope filters with "
            "ticket_count, pct_of_scope, sample ticket_ids, and a sanitized "
            "body excerpt. Results are PHI-safe. ALWAYS set scope to whatever "
            "the user asked about; never expand scope silently."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "trc": {"type": "string", "description": "Single TRC filter"},
                "payer": {"type": "string", "description": "Insurance payer substring"},
                "provider": {"type": "string", "description": "Provider ID"},
                "state": {"type": "string", "description": "Service state code"},
                "date_range": {"type": "string", "description": "'YYYY-MM-DD/YYYY-MM-DD'"},
                "concept_id": {"type": "string", "description": "Drill into one concept"},
                "tag": {"type": "string", "description": "Restrict to tagged tickets"},
                "group_by": {
                    "type": "string",
                    "enum": ["concept", "cluster", "trc", "payer", "provider"],
                    "description": "Aggregation dimension (default concept)",
                },
                "limit": {"type": "integer", "description": "Max issues to return (default 10)"},
                "include_samples": {"type": "boolean", "description": "Include ticket samples"},
            },
        },
    },
    {
        "name": "audit_tag_correlation",
        "description": (
            "Audit a tag against canonical clusters/concepts. Returns the "
            "concept/cluster distribution of tagged tickets, the expected "
            "cluster (from the incidents table, if any), and ranked mis-tagged "
            "tickets — those whose canonical cluster differs from the expected "
            "one, sorted by cosine distance from the expected centroid."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tag": {"type": "string", "description": "The tag name to audit"},
                "top_k": {"type": "integer", "description": "Max mis-tagged tickets to return (default 10)"},
            },
            "required": ["tag"],
        },
    },
]


# ═══════════════════════════════════════════════════════════════════
#  Tool Executor
# ═══════════════════════════════════════════════════════════════════

def execute_tool(tool_name: str, args: dict, db: Any) -> str:
    """Execute a tool by name and return JSON string result.

    Args:
        tool_name: One of the defined tool names.
        args: Tool arguments dict (from Claude tool_use block).
        db: DatabaseManager instance.

    Returns:
        JSON string with the tool result.
    """
    handler = _DISPATCH.get(tool_name)
    if handler is None:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})
    try:
        result = handler(args, db)
        return json.dumps(result, default=str)
    except Exception as exc:
        logger.error("Tool '%s' failed: %s", tool_name, exc)
        return json.dumps({"error": str(exc)})


# ═══════════════════════════════════════════════════════════════════
#  Tool Implementations (all read-only except propose_guru_edit)
# ═══════════════════════════════════════════════════════════════════

def _read_scan_summary(args: dict, db) -> dict:
    from src.data.scan_ledger import build_current_ledger, build_historical_ledger
    mode = args.get("mode", "current")
    if mode == "historical":
        num = args.get("num_scans", 5)
        return {"ledger": build_historical_ledger(db, num)}
    scan_id = args.get("scan_id", "")
    if not scan_id:
        # Default to latest scan
        conn = db.get_connection() if hasattr(db, 'get_connection') else db.conn
        row = conn.execute(
            "SELECT scan_id FROM nlp_scan_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        scan_id = row[0] if row else ""
    if not scan_id:
        return {"error": "No scans available"}
    return {"ledger": build_current_ledger(db, scan_id)}


def _get_friction_gaps(args: dict, db) -> dict:
    from src.data.guru_friction_pipeline import GuruFrictionPipeline
    pipeline = GuruFrictionPipeline(db)
    gaps = pipeline.get_gap_report()
    min_tickets = args.get("min_tickets", 0)
    if min_tickets > 0:
        gaps = [g for g in gaps if g.get("lifetime_tickets", 0) >= min_tickets]
    return {"gaps": gaps, "count": len(gaps)}


def _get_sub_pattern_trends(args: dict, db) -> dict:
    conn = db.get_connection() if hasattr(db, 'get_connection') else db.conn
    trc_filter = args.get("trc_filter", "")
    limit = args.get("limit", 20)

    query = """
        SELECT trc, label, friction_type, tier, lifetime_tickets,
               discovered_at, last_seen_at
        FROM sub_patterns
        WHERE merged_into IS NULL AND tier != 'retired'
    """
    params = []
    if trc_filter:
        query += " AND trc = ?"
        params.append(trc_filter)
    query += " ORDER BY lifetime_tickets DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    return {
        "patterns": [
            {
                "trc": r[0], "label": r[1], "friction_type": r[2] or "",
                "tier": r[3], "lifetime_tickets": r[4],
                "discovered_at": r[5], "last_seen_at": r[6],
            }
            for r in rows
        ],
        "count": len(rows),
    }


def _search_guru_cards(args: dict, db) -> dict:
    conn = db.get_connection() if hasattr(db, 'get_connection') else db.conn
    query = args.get("query", "")
    rows = conn.execute("""
        SELECT card_id, title, collection_name, content_hash, last_synced_at
        FROM guru_articles
        WHERE title LIKE ? OR collection_name LIKE ?
        ORDER BY last_synced_at DESC
        LIMIT 20
    """, (f"%{query}%", f"%{query}%")).fetchall()
    return {
        "cards": [
            {
                "card_id": r[0], "title": r[1], "collection": r[2],
                "content_hash": r[3], "last_synced": r[4],
            }
            for r in rows
        ],
        "count": len(rows),
    }


def _get_card_detail(args: dict, db) -> dict:
    conn = db.get_connection() if hasattr(db, 'get_connection') else db.conn
    card_id = args.get("card_id", "")

    card = conn.execute("""
        SELECT card_id, title, collection_id, collection_name,
               content_hash, last_synced_at, friction_score, status
        FROM guru_articles WHERE card_id = ?
    """, (card_id,)).fetchone()

    if not card:
        return {"error": f"Card not found: {card_id}"}

    # Coverage entries
    coverage = conn.execute("""
        SELECT friction_type, coverage_score, gap_description
        FROM guru_friction_coverage WHERE card_id = ?
    """, (card_id,)).fetchall()

    # Effectiveness entries
    effectiveness = conn.execute("""
        SELECT friction_type, pre_volume, post_volume, delta_pct, is_significant
        FROM guru_effectiveness WHERE card_id = ?
    """, (card_id,)).fetchall()

    return {
        "card_id": card[0], "title": card[1],
        "collection_id": card[2], "collection_name": card[3],
        "content_hash": card[4], "last_synced": card[5],
        "friction_score": card[6], "status": card[7],
        "coverage": [
            {"friction_type": c[0], "score": c[1], "gap": c[2]}
            for c in coverage
        ],
        "effectiveness": [
            {
                "friction_type": e[0], "pre": e[1], "post": e[2],
                "delta_pct": e[3], "significant": bool(e[4]),
            }
            for e in effectiveness
        ],
    }


def _get_card_relationships(args: dict, db) -> dict:
    conn = db.get_connection() if hasattr(db, 'get_connection') else db.conn
    card_id = args.get("card_id", "")

    # Get the card's collection
    card = conn.execute(
        "SELECT collection_id, collection_name FROM guru_articles WHERE card_id = ?",
        (card_id,),
    ).fetchone()
    if not card:
        return {"error": f"Card not found: {card_id}"}

    # Same-collection siblings
    siblings = conn.execute("""
        SELECT card_id, title FROM guru_articles
        WHERE collection_id = ? AND card_id != ?
        LIMIT 10
    """, (card[0], card_id)).fetchall()

    # Cards sharing the same friction types
    shared_friction = conn.execute("""
        SELECT DISTINCT ga.card_id, ga.title, gfc.friction_type
        FROM guru_friction_coverage gfc
        JOIN guru_articles ga ON gfc.card_id = ga.card_id
        WHERE gfc.friction_type IN (
            SELECT friction_type FROM guru_friction_coverage WHERE card_id = ?
        ) AND gfc.card_id != ?
        LIMIT 10
    """, (card_id, card_id)).fetchall()

    return {
        "collection": card[1],
        "siblings": [{"card_id": s[0], "title": s[1]} for s in siblings],
        "shared_friction": [
            {"card_id": sf[0], "title": sf[1], "friction_type": sf[2]}
            for sf in shared_friction
        ],
    }


def _get_effectiveness_report(args: dict, db) -> dict:
    from src.data.guru_effectiveness import GuruEffectivenessTracker
    tracker = GuruEffectivenessTracker(db)
    report = tracker.get_effectiveness_report()
    return {"measurements": report, "count": len(report)}


def _propose_guru_edit(args: dict, db) -> dict:
    conn = db.get_connection() if hasattr(db, 'get_connection') else db.conn
    card_id = args.get("card_id", "")
    friction_type = args.get("friction_type", "")
    title = args.get("draft_title", f"Draft for {friction_type}")
    content = args.get("draft_content", "")

    if not card_id or not content:
        return {"error": "card_id and draft_content are required"}

    # Verify card exists
    exists = conn.execute(
        "SELECT 1 FROM guru_articles WHERE card_id = ?", (card_id,)
    ).fetchone()
    if not exists:
        return {"error": f"Card not found: {card_id}"}

    import uuid
    draft_id = str(uuid.uuid4())

    conn.execute("""
        INSERT INTO guru_content_drafts
            (id, card_id, friction_type, draft_type, title, content, status)
        VALUES (?, ?, ?, 'rewrite', ?, ?, 'pending')
    """, (draft_id, card_id, friction_type, title, content))
    conn.commit()

    logger.info("Staged draft %s for card %s (human review required)", draft_id, card_id)
    return {"draft_id": draft_id, "status": "pending", "note": "Human review required"}


# ── Dispatch table ───────────────────────────────────────────────

def _query_issues(args: dict, db) -> dict:
    from src.data.issue_query_handler import query_issues
    conn = db.get_connection() if hasattr(db, 'get_connection') else db.conn
    return query_issues(
        conn,
        trc=args.get("trc"),
        payer=args.get("payer"),
        provider=args.get("provider"),
        state=args.get("state"),
        date_range=args.get("date_range"),
        concept_id=args.get("concept_id"),
        tag=args.get("tag"),
        group_by=args.get("group_by", "concept"),
        limit=int(args.get("limit", 10)),
        include_samples=bool(args.get("include_samples", True)),
    )


def _audit_tag_correlation(args: dict, db) -> dict:
    from src.data.tag_audit import audit_tag_correlation
    conn = db.get_connection() if hasattr(db, 'get_connection') else db.conn
    tag = args.get("tag") or ""
    return audit_tag_correlation(conn, tag, top_k=int(args.get("top_k", 10)))


_DISPATCH = {
    "read_scan_summary": _read_scan_summary,
    "get_friction_gaps": _get_friction_gaps,
    "get_sub_pattern_trends": _get_sub_pattern_trends,
    "search_guru_cards": _search_guru_cards,
    "get_card_detail": _get_card_detail,
    "get_card_relationships": _get_card_relationships,
    "get_effectiveness_report": _get_effectiveness_report,
    "propose_guru_edit": _propose_guru_edit,
    "query_issues": _query_issues,
    "audit_tag_correlation": _audit_tag_correlation,
}
