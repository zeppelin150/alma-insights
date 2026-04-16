"""
Alma Insights -- MCP Tool Server (Session 1 -- ACP Migration)

Standalone Python MCP server wrapping ToolRegistry.  Launched as a
subprocess by ACP's native MCP integration.

Transport: stdio JSON-RPC 2.0 (reads stdin, writes stdout).
Context: Batch-specific context passed via environment variables.

Entry point:
    python -m src.mcp.alma_mcp_server

Environment variables (set by the ACP bridge before session/new):
    ALMA_DB_PATH              -- Path to SQLite database
    ALMA_SCAN_ID              -- Current scan ID
    ALMA_BATCH_ID             -- Current batch ID
    ALMA_TRC                  -- TRC code (or comma-separated for mixed)
    ALMA_AGENT_ID             -- Worker agent ID
    ALMA_TICKET_TRC_MAP_JSON  -- JSON dict mapping ticket_id -> trc

Handles:
    initialize       -- MCP protocol handshake
    tools/list       -- Returns 7 tool schemas
    tools/call       -- Dispatches to ToolRegistry.execute()
"""

from __future__ import annotations

import json
import logging
import os
import sys

logger = logging.getLogger("alma.mcp_server")


# ── Tool schemas (mirror ToolRegistry parameters exactly) ────────────

TOOL_SCHEMAS = [
    {
        "name": "query_taxonomy",
        "description": (
            "Get active sub-patterns and n-gram fingerprints for a TRC. "
            "Use this to see what patterns already exist before classifying."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "trc": {
                    "type": "string",
                    "description": "TRC code (e.g. 'RCM_02') or comma-separated list",
                },
            },
            "required": ["trc"],
        },
    },
    {
        "name": "get_stats_context",
        "description": (
            "Get statistical context: Poisson incident flags, theta "
            "anomaly flags, and rising TF-IDF terms for a TRC."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "trc": {
                    "type": "string",
                    "description": "TRC code",
                },
            },
            "required": ["trc"],
        },
    },
    {
        "name": "store_classification",
        "description": (
            "Persist a single ticket classification immediately. "
            "Call this for EACH ticket after classification."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string", "description": "Ticket ID (required)"},
                "sub_cluster": {"type": "string", "description": "Sub-pattern label"},
                "sub_cluster_confidence": {"type": "number", "description": "Confidence 0.0-1.0"},
                "is_novel": {"type": "boolean", "description": "True if new pattern"},
                "sentiment_intensity": {"type": "integer", "description": "1-5 scale"},
                "sentiment_polarity": {"type": "string", "description": "positive/negative/mixed/neutral"},
                "friction_type": {"type": "string", "description": "One of 14 friction types"},
                "anomaly_flag": {"type": "string", "description": "normal/unusual/critical or null"},
                "anomaly_reason": {"type": "string", "description": "Reason if anomaly"},
                "entities": {"type": "object", "description": "Dict of extracted entities"},
                "key_phrases": {"type": "array", "items": {"type": "string"}, "description": "List of key phrases"},
                "root_cause_hint": {"type": "string", "description": "Root cause hypothesis"},
                "summary": {"type": "string", "description": "Brief summary"},
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "flag_for_review",
        "description": (
            "Flag a ticket for human review. Use when uncertain, "
            "when content is ambiguous, or when a critical anomaly is detected."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string", "description": "Ticket ID to flag"},
                "reason": {"type": "string", "description": "Why this ticket needs review"},
                "severity": {"type": "string", "description": "low/medium/high/critical"},
            },
            "required": ["ticket_id", "reason"],
        },
    },
    {
        "name": "get_full_thread",
        "description": (
            "Retrieve the full conversation thread for a ticket. "
            "Use when the batch excerpt is too short to classify."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string", "description": "Ticket ID to retrieve"},
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "report_progress",
        "description": (
            "Report classification progress for UI display. "
            "Call periodically during long batches."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "classified": {"type": "integer", "description": "Number classified so far"},
                "total": {"type": "integer", "description": "Total in this batch"},
                "message": {"type": "string", "description": "Optional status message"},
            },
            "required": ["classified", "total"],
        },
    },
    {
        "name": "check_cross_trc",
        "description": (
            "Check if a sub-pattern or n-gram signature exists in "
            "a different TRC. Use to detect cross-TRC patterns."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern_label": {"type": "string", "description": "Sub-pattern label to search for"},
                "exclude_trc": {"type": "string", "description": "TRC to exclude from search (current TRC)"},
            },
            "required": ["pattern_label"],
        },
    },
]


def _build_registry():
    """Build a ToolRegistry from environment variable context.

    Returns None if DB path is not set (server started without context).
    """
    db_path = os.environ.get("ALMA_DB_PATH", "")
    if not db_path:
        return None

    try:
        from src.agents.tool_registry import ToolRegistry

        registry = ToolRegistry(db_path)

        # Set context from env vars
        context = {}
        if os.environ.get("ALMA_SCAN_ID"):
            context["scan_id"] = os.environ["ALMA_SCAN_ID"]
        if os.environ.get("ALMA_BATCH_ID"):
            context["batch_id"] = os.environ["ALMA_BATCH_ID"]
        if os.environ.get("ALMA_TRC"):
            context["trc"] = os.environ["ALMA_TRC"]
        if os.environ.get("ALMA_AGENT_ID"):
            context["agent_id"] = os.environ["ALMA_AGENT_ID"]

        # Ticket TRC map (for boundary guard + mixed batches)
        trc_map_json = os.environ.get("ALMA_TICKET_TRC_MAP_JSON", "")
        if trc_map_json:
            try:
                context["ticket_trc_map"] = json.loads(trc_map_json)
            except json.JSONDecodeError:
                pass

        if context:
            registry.set_context(**context)

        return registry
    except Exception as e:
        logger.error("Failed to build ToolRegistry: %s", e)
        return None


def _make_response(id, result):
    """Build a JSON-RPC 2.0 response."""
    return {"jsonrpc": "2.0", "id": id, "result": result}


def _make_error(id, code, message, data=None):
    """Build a JSON-RPC 2.0 error response."""
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": id, "error": err}


def _handle_initialize(msg_id, params):
    """Handle MCP initialize handshake."""
    return _make_response(msg_id, {
        "protocolVersion": "2024-11-05",
        "capabilities": {
            "tools": {},
        },
        "serverInfo": {
            "name": "alma-tools",
            "version": "1.0.0",
        },
    })


def _handle_tools_list(msg_id, params):
    """Handle tools/list -- return all 7 tool schemas."""
    return _make_response(msg_id, {
        "tools": TOOL_SCHEMAS,
    })


def _handle_tools_call(msg_id, params, registry):
    """Handle tools/call -- dispatch to ToolRegistry."""
    tool_name = params.get("name", "")
    arguments = params.get("arguments", {})

    if not tool_name:
        return _make_error(msg_id, -32602, "Missing tool name")

    if registry is None:
        return _make_error(
            msg_id, -32603,
            "ToolRegistry not initialized (ALMA_DB_PATH not set)",
        )

    try:
        result = registry.execute(tool_name, arguments)
        # MCP tools/call returns content array
        return _make_response(msg_id, {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result, ensure_ascii=False, default=str),
                }
            ],
        })
    except Exception as e:
        return _make_error(msg_id, -32603, f"Tool execution failed: {e}")


def run_server():
    """Main server loop: read stdin, dispatch, write stdout."""
    # Configure logging to stderr (stdout is the JSON-RPC transport)
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(name)s %(levelname)s %(message)s",
    )

    logger.info("alma-mcp-server starting (pid=%d)", os.getpid())

    # Build registry from env (may be None if no context)
    registry = _build_registry()
    if registry:
        logger.info(
            "ToolRegistry initialized: db=%s scan=%s batch=%s trc=%s",
            os.environ.get("ALMA_DB_PATH", "?"),
            os.environ.get("ALMA_SCAN_ID", "?"),
            os.environ.get("ALMA_BATCH_ID", "?"),
            os.environ.get("ALMA_TRC", "?"),
        )
    else:
        logger.warning(
            "ToolRegistry not initialized (ALMA_DB_PATH not set). "
            "tools/call will return errors."
        )

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        # Parse JSON-RPC request
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            # Not valid JSON -- skip (MCP spec says to ignore)
            continue

        if not isinstance(msg, dict):
            continue

        msg_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params", {})

        # Dispatch by method
        if method == "initialize":
            response = _handle_initialize(msg_id, params)
        elif method == "notifications/initialized":
            # Client ACK -- no response needed
            continue
        elif method == "tools/list":
            response = _handle_tools_list(msg_id, params)
        elif method == "tools/call":
            response = _handle_tools_call(msg_id, params, registry)
        else:
            if msg_id is not None:
                response = _make_error(msg_id, -32601, f"Method not found: {method}")
            else:
                # Notification we don't handle -- skip
                continue

        # Write response
        out = json.dumps(response, ensure_ascii=False, default=str) + "\n"
        sys.stdout.write(out)
        sys.stdout.flush()

    # Cleanup
    if registry:
        registry.close()
    logger.info("alma-mcp-server exiting")


if __name__ == "__main__":
    run_server()
