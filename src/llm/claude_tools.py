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
    {
        "name": "search_local_documents",
        "description": (
            "Search the enablement document library stored locally — source "
            "documents pulled from Google Drive plus generated Guru card drafts. "
            "Use this whenever the user asks to find, look up, or recall a "
            "document, draft, or past content by name or topic ('find the SSO "
            "doc', 'what did the returns policy draft say'). Returns matching "
            "documents and drafts with snippets."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword or phrase to match names and bodies."},
                "limit": {"type": "integer", "description": "Max results (default 10)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "query_business_drive",
        "description": (
            "Query the connected business Google Drive for documents. Searches "
            "the live Drive when read access is configured, otherwise the "
            "locally-indexed mirror of that Drive. Use when the user asks what's "
            "in the Drive or to find a Drive document. Returns file names, links, "
            "and snippets."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for in the Drive."},
                "limit": {"type": "integer", "description": "Max results (default 10)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "asana_discover",
        "description": (
            "Discover the user's Asana projects, custom fields, and enum-value GIDs "
            "so a non-technical operator never has to find them by hand. Optionally "
            "pass project_gid to narrow to one project's custom fields. Returns "
            "projects [{gid, name}] and custom_fields with field gids + enum_options "
            "[{gid, name}]. Use this BEFORE set_asana_board_config to resolve GIDs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project_gid": {"type": "string", "description": "Optional — narrow to one project's custom fields."},
            },
        },
    },
    {
        "name": "set_asana_board_config",
        "description": (
            "Save an Asana board's enablement config using resolved GIDs. THIS IS THE "
            "ONLY SETTING THE ASSISTANT MAY WRITE — it creates/updates one Asana source "
            "in monitor_sources and touches nothing else. Call after asana_discover to "
            "persist the project + the indicator field/value GIDs + priority/assignee "
            "field GIDs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project_gid": {"type": "string"},
                "project_name": {"type": "string"},
                "indicator_field_gid": {"type": "string", "description": "Custom-field GID that decides task creation (e.g. Assigned Team)."},
                "indicator_field_name": {"type": "string"},
                "indicator_value_gid": {"type": "string", "description": "Enum-value GID that triggers (e.g. the 'Enablement' option)."},
                "indicator_value_name": {"type": "string"},
                "priority_field_gid": {"type": "string", "description": "Optional — field GID to map to task priority."},
                "assignee_field_gid": {"type": "string", "description": "Optional — people-field GID to map to assignee."},
            },
            "required": ["project_gid", "project_name", "indicator_field_gid",
                         "indicator_field_name", "indicator_value_gid", "indicator_value_name"],
        },
    },
    {
        "name": "create_card_draft",
        "description": (
            "Create a NEW Guru card draft from a title and Markdown content. The draft is "
            "saved pending review — publish it with push_guru_draft. Use this to author a "
            "card from scratch (not from a Drive document)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "The card title."},
                "content": {"type": "string", "description": "The card body in Markdown."},
            },
            "required": ["title", "content"],
        },
    },
    {
        "name": "import_guru_card",
        "description": (
            "Import an existing Guru card (by id or app.getguru.com URL) as an editable "
            "draft. The draft stays linked, so publishing UPDATES the same card rather "
            "than creating a duplicate."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "card_ref": {"type": "string", "description": "Guru card id or card URL."},
            },
            "required": ["card_ref"],
        },
    },
    {
        "name": "get_guru_analytics",
        "description": (
            "Read the locally-synced Guru analytics: metric='top_cards' (most viewed), "
            "'verification' (queue KPIs), 'comments' (open card comments), or "
            "'due_cards' (cards needing an update)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "metric": {"type": "string", "description": "top_cards | verification | comments | due_cards"},
                "days": {"type": "integer", "description": "Window in days (default 30)."},
            },
            "required": ["metric"],
        },
    },
    {
        "name": "create_task_from_comment",
        "description": (
            "Convert an open Guru card comment (see get_guru_analytics "
            "metric='comments') into an enablement task. Idempotent."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "comment_id": {"type": "string", "description": "The Guru comment id."},
            },
            "required": ["comment_id"],
        },
    },
    {
        "name": "revise_draft",
        "description": (
            "Revise an existing Guru card draft per an instruction (e.g. 'tighten the "
            "intro', 'add a rollout-date section') and re-render it. Pass the draft_id "
            "and the change to make."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "draft_id": {"type": "integer", "description": "The draft id to revise."},
                "instruction": {"type": "string", "description": "The change to make."},
            },
            "required": ["draft_id", "instruction"],
        },
    },
    {
        "name": "push_guru_draft",
        "description": (
            "Publish a card draft to Guru — creates a new card, or updates the existing "
            "card if the draft is linked to one. This is the 'push to Guru' action; only "
            "call it when the operator asked to publish."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "draft_id": {"type": "integer", "description": "The draft id to publish."},
                "collection_id": {"type": "string", "description": "Optional target collection for a new card."},
            },
            "required": ["draft_id"],
        },
    },
    {
        "name": "render_card_preview",
        "description": "Return a draft's current title and Markdown content for preview.",
        "input_schema": {
            "type": "object",
            "properties": {"draft_id": {"type": "integer"}},
            "required": ["draft_id"],
        },
    },
    {
        "name": "draft_subtasks",
        "description": (
            "Attach a checklist of subtasks to a task. Decompose the work yourself and "
            "pass the steps as items."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "items": {"type": "array", "items": {"type": "string"}, "description": "Ordered checklist steps."},
            },
            "required": ["task_id", "items"],
        },
    },
    {
        "name": "add_subtask",
        "description": "Add a single subtask to a task.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}, "text": {"type": "string"}},
            "required": ["task_id", "text"],
        },
    },
    {
        "name": "toggle_subtask",
        "description": "Check or uncheck a subtask.",
        "input_schema": {
            "type": "object",
            "properties": {"subtask_id": {"type": "string"}, "done": {"type": "boolean"}},
            "required": ["subtask_id", "done"],
        },
    },
    {
        "name": "update_scratchpad",
        "description": "Write freeform operator notes on a task.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}, "text": {"type": "string"}},
            "required": ["task_id", "text"],
        },
    },
    {
        "name": "create_task",
        "description": "Create an enablement task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "source": {"type": "string", "description": "drive|guru|asana|manual (default manual)."},
                "kind": {"type": "string", "description": "card_review|doc_due_date|product_update|request."},
                "due_date": {"type": "string", "description": "ISO date (optional)."},
                "priority": {"type": "string", "description": "low|normal|high."},
                "summary": {"type": "string"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "update_task",
        "description": "Update an enablement task's fields (status, priority, due_date, assignee, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "status": {"type": "string", "description": "open|in_progress|done|dismissed."},
                "priority": {"type": "string"},
                "due_date": {"type": "string"},
                "assignee": {"type": "string"},
                "title": {"type": "string"},
                "summary": {"type": "string"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "list_tasks",
        "description": "List enablement tasks, optionally filtered by status/source/kind/due date.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "source": {"type": "string"},
                "kind": {"type": "string"},
                "due_before": {"type": "string", "description": "ISO date."},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "search_drive_docs",
        "description": "Search the locally-indexed Drive documents by name/topic.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["query"],
        },
    },
    {
        "name": "get_drive_doc",
        "description": "Fetch one indexed Drive document (with full text) by its id.",
        "input_schema": {
            "type": "object",
            "properties": {"doc_id": {"type": "string"}},
            "required": ["doc_id"],
        },
    },
    {
        "name": "run_monitor_now",
        "description": "Run a one-off poll of the configured Asana/Drive monitors now.",
        "input_schema": {
            "type": "object",
            "properties": {"source": {"type": "string", "description": "Optional: 'asana' or 'drive' to poll just one."}},
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


def _search_local_documents(args: dict, db) -> dict:
    from src.data import enablement_store as store
    conn = db.get_connection() if hasattr(db, 'get_connection') else db.conn
    query = args.get("query", "")
    limit = int(args.get("limit", 10))
    docs = store.search_documents(conn, query, limit=limit)
    drafts = store.search_drafts(conn, query, limit=limit)
    return {
        "documents": docs, "drafts": drafts,
        "doc_count": len(docs), "draft_count": len(drafts),
    }


def _query_business_drive(args: dict, db) -> dict:
    from src.data.drive_query import query_business_drive
    conn = db.get_connection() if hasattr(db, 'get_connection') else db.conn
    return query_business_drive(conn, args.get("query", ""), limit=int(args.get("limit", 10)))


def _asana_discover(args: dict, db) -> dict:
    from src.data.asana_setup import discover
    return discover(project_gid=args.get("project_gid"))


def _set_asana_board_config(args: dict, db) -> dict:
    from src.data.asana_setup import set_asana_board_config
    conn = db.get_connection() if hasattr(db, 'get_connection') else db.conn
    return set_asana_board_config(
        conn,
        project_gid=args["project_gid"], project_name=args["project_name"],
        indicator_field_gid=args["indicator_field_gid"], indicator_field_name=args["indicator_field_name"],
        indicator_value_gid=args["indicator_value_gid"], indicator_value_name=args["indicator_value_name"],
        priority_field_gid=args.get("priority_field_gid"), assignee_field_gid=args.get("assignee_field_gid"),
    )


# ── Enablement Workbench action tools (delegate to the shared impls in
#    src/data/chat_tools/enablement_tools so both chat paths stay in sync) ──

def _ent_conn(db):
    return db.get_connection() if hasattr(db, 'get_connection') else db.conn


def _create_card_draft(args: dict, db) -> dict:
    from src.data.chat_tools.enablement_tools import _create_card_draft_impl
    return _create_card_draft_impl(_ent_conn(db), args.get("title", ""), args.get("content", ""))


def _import_guru_card(args: dict, db) -> dict:
    from src.data.chat_tools.enablement_tools import _import_guru_card_impl
    return _import_guru_card_impl(_ent_conn(db), args.get("card_ref", ""))


def _get_guru_analytics(args: dict, db) -> dict:
    from src.data.chat_tools.enablement_tools import _get_guru_analytics_impl
    return _get_guru_analytics_impl(_ent_conn(db), args.get("metric"), args.get("days", 30))


def _create_task_from_comment(args: dict, db) -> dict:
    from src.data.chat_tools.enablement_tools import _create_task_from_comment_impl
    return _create_task_from_comment_impl(_ent_conn(db), args.get("comment_id", ""))


def _revise_draft(args: dict, db) -> dict:
    from src.data.chat_tools.enablement_tools import _revise_draft_impl
    return _revise_draft_impl(_ent_conn(db), args.get("draft_id"), args.get("instruction", ""))


def _push_guru_draft(args: dict, db) -> dict:
    from src.data.chat_tools.enablement_tools import _push_guru_draft_impl
    return _push_guru_draft_impl(_ent_conn(db), args.get("draft_id"), args.get("collection_id"))


def _render_card_preview(args: dict, db) -> dict:
    from src.data.chat_tools.enablement_tools import _render_card_preview_impl
    return _render_card_preview_impl(_ent_conn(db), args.get("draft_id"))


def _draft_subtasks(args: dict, db) -> dict:
    from src.data.chat_tools.enablement_tools import _draft_subtasks_impl
    return _draft_subtasks_impl(_ent_conn(db), args.get("task_id"), args.get("items"))


def _add_subtask(args: dict, db) -> dict:
    from src.data.chat_tools.enablement_tools import _add_subtask_impl
    return _add_subtask_impl(_ent_conn(db), args.get("task_id"), args.get("text", ""))


def _toggle_subtask(args: dict, db) -> dict:
    from src.data.chat_tools.enablement_tools import _toggle_subtask_impl
    return _toggle_subtask_impl(_ent_conn(db), args.get("subtask_id"), args.get("done", True))


def _update_scratchpad(args: dict, db) -> dict:
    from src.data.chat_tools.enablement_tools import _update_scratchpad_impl
    return _update_scratchpad_impl(_ent_conn(db), args.get("task_id"), args.get("text", ""))


def _create_enablement_task(args: dict, db) -> dict:
    from src.data.chat_tools.enablement_tools import _create_task_impl
    return _create_task_impl(_ent_conn(db), **args)


def _update_enablement_task(args: dict, db) -> dict:
    from src.data.chat_tools.enablement_tools import _update_task_impl
    fields = {k: v for k, v in args.items() if k != "task_id"}
    return _update_task_impl(_ent_conn(db), args.get("task_id"), fields)


def _list_enablement_tasks(args: dict, db) -> dict:
    from src.data.chat_tools.enablement_tools import _list_tasks_impl
    return _list_tasks_impl(_ent_conn(db), status=args.get("status"), source=args.get("source"),
                            kind=args.get("kind"), due_before=args.get("due_before"),
                            limit=args.get("limit", 50))


def _search_drive_docs(args: dict, db) -> dict:
    from src.data.chat_tools.enablement_tools import _search_drive_docs_impl
    return _search_drive_docs_impl(_ent_conn(db), args.get("query", ""), args.get("limit", 10))


def _get_drive_doc(args: dict, db) -> dict:
    from src.data.chat_tools.enablement_tools import _get_drive_doc_impl
    return _get_drive_doc_impl(_ent_conn(db), args.get("doc_id"))


def _run_monitor_now(args: dict, db) -> dict:
    from src.data.chat_tools.enablement_tools import _run_monitor_now_impl
    return _run_monitor_now_impl(_ent_conn(db), args.get("source"))


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
    "search_local_documents": _search_local_documents,
    "query_business_drive": _query_business_drive,
    "asana_discover": _asana_discover,
    "set_asana_board_config": _set_asana_board_config,
    "create_card_draft": _create_card_draft,
    "revise_draft": _revise_draft,
    "push_guru_draft": _push_guru_draft,
    "render_card_preview": _render_card_preview,
    "draft_subtasks": _draft_subtasks,
    "add_subtask": _add_subtask,
    "toggle_subtask": _toggle_subtask,
    "update_scratchpad": _update_scratchpad,
    "create_task": _create_enablement_task,
    "update_task": _update_enablement_task,
    "list_tasks": _list_enablement_tasks,
    "search_drive_docs": _search_drive_docs,
    "get_drive_doc": _get_drive_doc,
    "run_monitor_now": _run_monitor_now,
    "import_guru_card": _import_guru_card,
    "get_guru_analytics": _get_guru_analytics,
    "create_task_from_comment": _create_task_from_comment,
}
