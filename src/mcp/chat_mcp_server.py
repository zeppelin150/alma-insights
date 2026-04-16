"""
Alma Insights -- Chat MCP Tool Server

Lightweight MCP server exposing chat-specific tools for structured
function calling via ACP.  Gemini calls these natively (no text
TOOL_CALL parsing needed).

Transport: stdio JSON-RPC 2.0
Entry point: python -m src.mcp.chat_mcp_server

Environment variables:
    ALMA_DB_PATH -- Path to SQLite database
"""

import json
import logging
import os
import sys

logger = logging.getLogger("alma.chat_mcp")

TOOL_SCHEMAS = [
    {
        "name": "query_issues",
        "description": (
            "Primary tool for answering 'what issues exist' questions. Returns "
            "a ranked list of canonical issues (concepts or clusters) matching "
            "the scope filters, each with ticket_count, pct_of_scope, a handful "
            "of sample ticket_ids, and a body excerpt. ALWAYS set scope filters "
            "to whatever the user asked about (payer, TRC, state, date range) "
            "— never expand scope silently. Use group_by='concept' for "
            "prevalence questions; 'cluster' when the user wants fine-grained "
            "detail within a TRC. Supersedes query_entities, "
            "query_ticket_classifications, and query_findings for issue questions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "trc": {
                    "type": "string",
                    "description": "Filter to one TRC (matches trc_code or trc_label)",
                },
                "payer": {
                    "type": "string",
                    "description": "Insurance payer substring (e.g. 'Thunderbird')",
                },
                "provider": {
                    "type": "string",
                    "description": "Provider ID exact match",
                },
                "state": {
                    "type": "string",
                    "description": "Service state (2-letter code)",
                },
                "date_range": {
                    "type": "string",
                    "description": "ISO date range 'YYYY-MM-DD/YYYY-MM-DD' (either side optional)",
                },
                "concept_id": {
                    "type": "string",
                    "description": "Drill into a specific concept by concept_id",
                },
                "tag": {
                    "type": "string",
                    "description": "Restrict to tickets carrying this tag",
                },
                "group_by": {
                    "type": "string",
                    "enum": ["concept", "cluster", "trc", "payer", "provider"],
                    "description": "Aggregation dimension (default 'concept')",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max top issues to return (default 10, max 50)",
                },
                "include_samples": {
                    "type": "boolean",
                    "description": "Include sample ticket_ids + excerpts per issue (default true)",
                },
            },
        },
    },
    {
        "name": "audit_tag_correlation",
        "description": (
            "Given a tag, audit whether tagged tickets actually describe what "
            "the tag claims. Returns total tagged, concept/cluster distribution, "
            "the expected cluster (from the incidents table, if any), and a "
            "ranked list of 'likely mis-tagged' tickets — those whose canonical "
            "cluster differs from the expected one, sorted by cosine distance "
            "from the expected centroid. Tags are applied by event and are "
            "often wrong; this is how we find the errors."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tag": {
                    "type": "string",
                    "description": "The tag to audit (e.g. 'incident_portal_outage_0301')",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Max mis-tagged tickets to return (default 10, max 50)",
                },
            },
            "required": ["tag"],
        },
    },
    {
        "name": "list_tickets",
        "description": (
            "List individual tickets matching filters, with issue summaries."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "filters": {
                    "type": "object",
                    "description": "Optional filters (trc_codes, friction_types, etc.)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max tickets to return (default 20, max 50)",
                },
            },
        },
    },
    {
        "name": "semantic_search",
        "description": (
            "Find tickets semantically similar to a natural language query "
            "using Qwen 3 embeddings."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language search query",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results (default 10)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "query_stats",
        "description": (
            "Query statistical engine outputs: anomalies, trends, or baselines."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "stat_type": {
                    "type": "string",
                    "enum": ["anomalies", "trends", "baselines"],
                    "description": "Type of statistical data to query",
                },
                "severity": {
                    "type": "string",
                    "description": "Anomaly severity filter (for stat_type=anomalies)",
                },
                "dimension": {
                    "type": "string",
                    "description": "Trend dimension (for stat_type=trends, e.g. friction_type)",
                },
            },
            "required": ["stat_type"],
        },
    },
    {
        "name": "read_thread",
        "description": (
            "Read the full conversation thread for a specific ticket (PII-redacted). "
            "Use when the user asks to see a specific ticket's conversation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "string",
                    "description": "The ticket ID to read (e.g. 'TKT-12345')",
                },
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "read_threads_batch",
        "description": (
            "Read conversation threads for up to 5 tickets at once (PII-redacted)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticket_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of ticket IDs (max 5)",
                },
            },
            "required": ["ticket_ids"],
        },
    },
    {
        "name": "query_report",
        "description": (
            "Read a previously generated analysis report. "
            "Returns latest report if no report_id specified."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "report_id": {
                    "type": "string",
                    "description": "Specific report ID (optional, latest if omitted)",
                },
                "section": {
                    "type": "string",
                    "enum": ["findings", "summary_stats", "recommendations"],
                    "description": "Return only a specific section",
                },
            },
        },
    },
]


def _get_db_connection():
    import sqlite3
    db_path = os.environ.get("ALMA_DB_PATH", "")
    if not db_path:
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # Enables dict-style row access
    return conn


def _execute_tool(name, args):
    """Execute a chat tool and return the result dict."""
    conn = _get_db_connection()
    if not conn:
        return {"error": "ALMA_DB_PATH not set"}

    try:
        if name == "query_issues":
            from src.data.issue_query_handler import handle_query_issues
            return handle_query_issues(conn, args, session_filters={})

        elif name == "audit_tag_correlation":
            from src.data.tag_audit import handle_audit_tag_correlation
            return handle_audit_tag_correlation(conn, args, session_filters={})

        elif name == "list_tickets":
            from src.data.chat_tools.fast_path import handle_list_tickets
            return handle_list_tickets(conn, args, session_filters={})

        elif name == "semantic_search":
            from src.data.chat_tools.semantic_tools import handle_semantic_search
            return handle_semantic_search(conn, args, session_filters={})

        elif name == "query_stats":
            from src.data.chat_tools.fast_path import handle_query_stats
            return handle_query_stats(conn, args, session_filters={})

        elif name == "read_thread":
            from src.data.chat_tools.thread_tools import handle_read_thread
            return handle_read_thread(conn, args, session_filters={})

        elif name == "read_threads_batch":
            from src.data.chat_tools.thread_tools import handle_read_threads_batch
            return handle_read_threads_batch(conn, args, session_filters={})

        elif name == "query_report":
            from src.data.chat_tools.report_tools import handle_query_report
            return handle_query_report(conn, args, session_filters={})

        else:
            return {"error": f"Unknown tool: {name}"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def _make_response(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _make_error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def run_server():
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(name)s %(levelname)s %(message)s")
    logger.info("chat-mcp-server starting (pid=%d)", os.getpid())

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        if not isinstance(msg, dict):
            continue

        msg_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params", {})

        if method == "initialize":
            response = _make_response(msg_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "alma-chat-tools", "version": "1.0.0"},
            })
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            response = _make_response(msg_id, {"tools": TOOL_SCHEMAS})
        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            try:
                result = _execute_tool(tool_name, arguments)
                response = _make_response(msg_id, {
                    "content": [{"type": "text",
                                 "text": json.dumps(result, ensure_ascii=False, default=str)}],
                })
            except Exception as e:
                response = _make_error(msg_id, -32603, f"Tool failed: {e}")
        else:
            if msg_id is not None:
                response = _make_error(msg_id, -32601, f"Method not found: {method}")
            else:
                continue

        sys.stdout.write(json.dumps(response, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()

    logger.info("chat-mcp-server exiting")


if __name__ == "__main__":
    run_server()
