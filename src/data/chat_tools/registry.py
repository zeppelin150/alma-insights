"""Unified chat tool registry and dispatch.

All chat tools register here. The dispatcher merges session filters
with per-call args, routes to the handler, and logs via ToolLogger.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from src.data.chat_tools.tool_logger import ToolLogger

logger = logging.getLogger(__name__)

_MAX_RESULT_JSON_LEN = 4096  # PHI-safe: truncate result_json to 4KB

# Lazily populated on first import of handler modules
_CHAT_TOOLS: dict[str, dict[str, Any]] = {}
_logger_instance: ToolLogger | None = None


def _ensure_registered():
    """Populate the registry on first call (avoids circular imports)."""
    if _CHAT_TOOLS:
        return

    from src.data.chat_tools.fast_path import (
        handle_query_classifications,
        handle_list_tickets,
        handle_query_findings,
        handle_query_stats,
    )
    from src.data.chat_tools.thread_tools import (
        handle_read_thread,
        handle_read_threads_batch,
    )
    from src.data.chat_tools.report_tools import (
        handle_query_report,
        handle_run_report,
    )

    _register("query_ticket_classifications", handle_query_classifications,
              phi_level=0, desc="Count and group tickets by classification field")
    _register("list_tickets", handle_list_tickets,
              phi_level=1, desc="List individual tickets matching filters")
    _register("query_findings", handle_query_findings,
              phi_level=0, desc="Retrieve NLP scan findings and themes")
    _register("query_stats", handle_query_stats,
              phi_level=0, desc="Query anomalies, trends, baselines")
    _register("read_thread", handle_read_thread,
              phi_level=2, desc="Read full conversation thread (PII-redacted)")
    _register("read_threads_batch", handle_read_threads_batch,
              phi_level=2, desc="Read threads for up to 5 tickets")
    _register("query_report", handle_query_report,
              phi_level=0, desc="Read a previously generated analysis report")
    _register("run_report", handle_run_report,
              phi_level=3, desc="Trigger a new analysis report")

    # ── Entity lookup (Addendum Session 3) ──
    from src.data.chat_tools.fast_path import handle_query_entities
    _register("query_entities", handle_query_entities,
              phi_level=0, desc="Look up tickets by payer, product area, or feature")

    # ── Semantic search (Session 4) ──
    from src.data.chat_tools.semantic_tools import handle_semantic_search
    _register("semantic_search", handle_semantic_search,
              phi_level=1, desc="Find tickets by semantic similarity")

    # ── Phase 9: unified scope-aware issue query + tag audit ──
    from src.data.issue_query_handler import handle_query_issues
    _register("query_issues", handle_query_issues,
              phi_level=1, desc="Unified scope-aware issue query over canonical clusters/concepts")
    from src.data.tag_audit import handle_audit_tag_correlation
    _register("audit_tag_correlation", handle_audit_tag_correlation,
              phi_level=1, desc="Audit a tag against canonical clusters + rank likely mis-tags")

    # ── Enablement: local doc search + business Drive query + Asana setup ──
    from src.data.chat_tools.enablement_tools import (
        handle_search_local_documents,
        handle_query_business_drive,
        handle_asana_discover,
        handle_set_asana_board_config,
        handle_create_card_draft,
        handle_revise_draft,
        handle_push_guru_draft,
        handle_list_guru_collections,
        handle_list_guru_folders,
        handle_render_card_preview,
        handle_draft_subtasks,
        handle_add_subtask,
        handle_toggle_subtask,
        handle_update_scratchpad,
        handle_create_asana_subtask,
        handle_post_asana_comment,
        handle_update_asana_due_date,
        handle_create_task,
        handle_update_task,
        handle_list_tasks,
        handle_search_drive_docs,
        handle_get_drive_doc,
        handle_list_style_guides,
        handle_get_style_guide,
        handle_set_active_style_guide,
        handle_run_monitor_now,
    )
    _register("search_local_documents", handle_search_local_documents,
              phi_level=0, desc="Find stored enablement documents + card drafts by name/topic")
    _register("query_business_drive", handle_query_business_drive,
              phi_level=0, desc="Query the business Google Drive (live or local mirror) for documents")
    _register("asana_discover", handle_asana_discover,
              phi_level=0, desc="Discover Asana projects + custom-field/enum-value GIDs")
    _register("set_asana_board_config", handle_set_asana_board_config,
              phi_level=0, desc="Save an Asana board's config using resolved GIDs (the assistant's only write)")
    # ── Enablement Workbench action tools ──
    _register("create_card_draft", handle_create_card_draft,
              phi_level=0, desc="Create a new Guru card draft from a title + Markdown content")
    _register("revise_draft", handle_revise_draft,
              phi_level=0, desc="Revise a Guru card draft with an instruction and re-render it")
    _register("push_guru_draft", handle_push_guru_draft,
              phi_level=0, desc="Publish a card draft to Guru (creates a new card or updates an existing one); optional collection_id + folder_id target a sub-folder")
    _register("list_guru_collections", handle_list_guru_collections,
              phi_level=0, desc="List Guru collections to choose a publish target")
    _register("list_guru_folders", handle_list_guru_folders,
              phi_level=0, desc="List a Guru collection's folders (sub-folders) by collection id or name")
    _register("render_card_preview", handle_render_card_preview,
              phi_level=0, desc="Return a draft's current title + content for preview")
    _register("draft_subtasks", handle_draft_subtasks,
              phi_level=0, desc="Attach a checklist of subtasks to a task")
    _register("add_subtask", handle_add_subtask,
              phi_level=0, desc="Add one subtask to a task")
    _register("toggle_subtask", handle_toggle_subtask,
              phi_level=0, desc="Check or uncheck a subtask")
    _register("update_scratchpad", handle_update_scratchpad,
              phi_level=0, desc="Write freeform notes on a task")
    # ── Asana write-back (two-way sync) ──
    _register("create_asana_subtask", handle_create_asana_subtask,
              phi_level=0, desc="Add a subtask AND create it back in Asana under the parent task")
    _register("post_asana_comment", handle_post_asana_comment,
              phi_level=0, desc="Post a comment back to the linked Asana task")
    _register("update_asana_due_date", handle_update_asana_due_date,
              phi_level=0, desc="Update a task's due date locally and push it to the linked Asana task")
    _register("create_task", handle_create_task,
              phi_level=0, desc="Create an enablement task")
    _register("update_task", handle_update_task,
              phi_level=0, desc="Update an enablement task's status/priority/due date/etc.")
    _register("list_tasks", handle_list_tasks,
              phi_level=0, desc="List enablement tasks with optional filters")
    _register("search_drive_docs", handle_search_drive_docs,
              phi_level=0, desc="Search indexed Drive documents")
    _register("list_style_guides", handle_list_style_guides,
              phi_level=0, desc="List the operator's stored style guides (the active one is flagged)")
    _register("get_style_guide", handle_get_style_guide,
              phi_level=0, desc="Read the active style guide's text so a card can be written to follow it")
    _register("set_active_style_guide", handle_set_active_style_guide,
              phi_level=0, desc="Switch which stored style guide is active (injected into card generation/revision)")
    _register("get_drive_doc", handle_get_drive_doc,
              phi_level=0, desc="Get one indexed Drive document by id")
    _register("run_monitor_now", handle_run_monitor_now,
              phi_level=0, desc="Run a one-off poll of the configured Asana/Drive monitors")
    # ── Guru analytics tools (P7 redesign) ──
    from src.data.chat_tools.enablement_tools import (
        handle_create_task_from_comment,
        handle_get_guru_analytics,
        handle_import_guru_card,
        handle_update_card_from_doc,
        handle_search_guru_cards,
        handle_research_topic,
        handle_open_guru_card,
        handle_index_content,
        handle_search_content,
        handle_update_cards_from_doc,
    )
    _register("import_guru_card", handle_import_guru_card,
              phi_level=0, desc="Import an existing Guru card as an editable draft (publish updates it)")
    _register("get_guru_analytics", handle_get_guru_analytics,
              phi_level=0, desc="Guru usage analytics: top_cards | verification | comments | due_cards")
    _register("create_task_from_comment", handle_create_task_from_comment,
              phi_level=0, desc="Convert an open Guru card comment into an enablement task")
    _register("update_card_from_doc", handle_update_card_from_doc,
              phi_level=0, desc="Review a source doc, find the existing Guru card, identify changes, write the update, and stage a draft for review")
    _register("search_guru_cards", handle_search_guru_cards,
              phi_level=0, desc="Search LIVE Guru for existing cards by topic/title (returns id, title, snippet)")
    _register("research_topic", handle_research_topic,
              phi_level=1, desc="Gather reference points on a topic from every source: Guru cards + local docs + ticket signals")
    _register("open_guru_card", handle_open_guru_card,
              phi_level=0, desc="Open a Guru card in the operator's default web browser by id or URL")
    _register("index_content", handle_index_content,
              phi_level=0, desc="Build/refresh the summary catalog over PHI-free content (docs + Guru cards) for fast scalable search")
    _register("search_content", handle_search_content,
              phi_level=0, desc="Deterministic hybrid search over the content catalog summaries (torch-free; disambiguates look-alike titles by content)")
    _register("update_cards_from_doc", handle_update_cards_from_doc,
              phi_level=0, desc="Fan-out: find the SET of Guru cards a source doc affects and stage an update for each changed card (human-gated publish)")

    # ── Backward-compat aliases for old tool names ──
    # These map old names to new handlers so existing prompts keep working
    from src.data.chat_tools.fast_path import (
        handle_legacy_query_tickets,
        handle_legacy_ticket_detail,
        handle_legacy_query_trends,
        handle_legacy_query_anomalies,
        handle_legacy_compare_periods,
        handle_legacy_query_insights,
        handle_legacy_search_conversations,
    )
    _register("query_tickets", handle_legacy_query_tickets,
              phi_level=1, desc="(Legacy) Query tickets by filters")
    _register("ticket_detail", handle_legacy_ticket_detail,
              phi_level=1, desc="(Legacy) Get ticket detail by ID")
    _register("query_trends", handle_legacy_query_trends,
              phi_level=0, desc="(Legacy) Monthly volume trends")
    _register("query_anomalies", handle_legacy_query_anomalies,
              phi_level=0, desc="(Legacy) Anomaly flags")
    _register("compare_periods", handle_legacy_compare_periods,
              phi_level=0, desc="(Legacy) Period comparison")
    _register("query_insights", handle_legacy_query_insights,
              phi_level=0, desc="(Legacy) NLP insights")
    _register("search_conversations", handle_legacy_search_conversations,
              phi_level=1, desc="(Legacy) FTS conversation search")


def _register(name: str, handler, phi_level: int, desc: str):
    _CHAT_TOOLS[name] = {
        "handler": handler,
        "phi_level": phi_level,
        "description": desc,
    }


def get_tool_registry() -> dict[str, dict[str, Any]]:
    """Return the full tool registry dict."""
    _ensure_registered()
    return dict(_CHAT_TOOLS)


def dispatch_tool(
    tool_name: str,
    args: dict,
    conn,
    session_filters: dict | None = None,
    session_id: str | None = None,
    message_id: str | None = None,
) -> str:
    """Central tool dispatch. Returns JSON result string.

    Merges session_filters (base) with args (override), calls
    the handler, logs telemetry, persists to chat_tool_executions,
    and returns JSON.
    """
    global _logger_instance
    _ensure_registered()

    if _logger_instance is None:
        _logger_instance = ToolLogger()

    tool_def = _CHAT_TOOLS.get(tool_name)
    if tool_def is None:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    # Merge session filters as base, tool args override
    effective_filters = dict(session_filters or {})
    if "filters" in args:
        effective_filters.update(args["filters"])

    start = time.perf_counter()
    error = None
    result = None
    result_rows = None
    tables_touched = None
    try:
        handler = tool_def["handler"]
        result = handler(conn, args, effective_filters)

        # Estimate result_rows from common response shapes
        if isinstance(result, dict):
            for key in ("tickets", "results", "rows", "findings", "data"):
                if key in result and isinstance(result[key], list):
                    result_rows = len(result[key])
                    break
            if result_rows is None and "count" in result:
                result_rows = result.get("count")
        elif isinstance(result, list):
            result_rows = len(result)

        return json.dumps(result, default=str)
    except Exception as e:
        error = str(e)
        logger.warning("Tool %s failed: %s", tool_name, e)
        return json.dumps({"error": error})
    finally:
        elapsed = (time.perf_counter() - start) * 1000
        result_size = len(json.dumps(result, default=str)) if result else 0

        # JSONL file log (existing)
        _logger_instance.log_call(
            tool_name=tool_name,
            args=args,
            result_size=result_size,
            elapsed_ms=elapsed,
            error=error,
            session_id=session_id,
        )

        # Persist to chat_tool_executions table (migration 009)
        _persist_tool_execution(
            conn=conn,
            message_id=message_id,
            session_id=session_id,
            tool_name=tool_name,
            args=args,
            result=result,
            result_rows=result_rows,
            tables_touched=tables_touched,
            elapsed_ms=elapsed,
            error=error,
        )


def _persist_tool_execution(
    conn,
    message_id: str | None,
    session_id: str | None,
    tool_name: str,
    args: dict,
    result: Any,
    result_rows: int | None,
    tables_touched: list[str] | None,
    elapsed_ms: float,
    error: str | None,
) -> None:
    """Write a row to chat_tool_executions (best-effort, non-blocking).

    When session_id is None (programmatic probes, harness runs, any
    non-UI caller) we still write with a synthetic "adhoc_probe"
    session_id so the paper trail survives. Previously this silently
    dropped the row — which hid every programmatic invocation from
    the audit table.
    """
    effective_session_id = session_id or "adhoc_probe"

    try:
        # Check table exists (graceful on pre-009 databases)
        conn.execute("SELECT 1 FROM chat_tool_executions LIMIT 0")
    except Exception:
        return

    try:
        # Truncate result_json for PHI safety
        result_json = None
        if result is not None:
            raw = json.dumps(result, default=str)
            result_json = raw[:_MAX_RESULT_JSON_LEN] if len(raw) > _MAX_RESULT_JSON_LEN else raw

        conn.execute(
            """INSERT INTO chat_tool_executions
               (execution_id, message_id, session_id, tool_name,
                args_json, result_json, result_rows, tables_touched,
                elapsed_ms, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                message_id or "",
                effective_session_id,
                tool_name,
                json.dumps(args, default=str),
                result_json,
                result_rows,
                json.dumps(tables_touched) if tables_touched else None,
                int(elapsed_ms),
                error,
            ),
        )
        conn.commit()
    except Exception as e:
        # A failed telemetry write (e.g. a FOREIGN KEY violation on an adhoc/probe
        # session_id) must NOT leave the connection mid-transaction — the dangling
        # transaction would make the next atomic() on a reused connection raise
        # "atomic() cannot be nested". Roll back defensively so failure is inert.
        try:
            conn.rollback()
        except Exception:
            pass
        logger.debug("Failed to persist tool execution: %s", e)
