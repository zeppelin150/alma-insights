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
        logger.debug("Failed to persist tool execution: %s", e)
