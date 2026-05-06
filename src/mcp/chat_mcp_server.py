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
            "Query statistical engine outputs. "
            "stat_type=anomalies returns real statistical anomalies from anomaly_flags "
            "(z_score, theta_level, metric_type, date, notes). "
            "stat_type=trends returns weekly volume by dimension "
            "(trc, friction_type, sub_cluster, payer, provider). "
            "stat_type=baselines returns TRC baseline snapshots. "
            "stat_type=csat returns CSAT score bucket counts (low/mid/high/null + mean). "
            "stat_type=friction_distribution returns the ranked breakdown of "
            "ticket_index.friction_type — USE THIS for 'product bug', 'feature broken', "
            "'friction pattern', 'repeat contact', or any aggregate-over-friction question. "
            "Optional cross_dim='trc'|'payer' nests per-dimension counts inside each row."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "stat_type": {
                    "type": "string",
                    "enum": [
                        "anomalies", "trends", "baselines", "csat",
                        "friction_distribution",
                    ],
                    "description": "Type of statistical data to query",
                },
                "severity": {
                    "type": "string",
                    "enum": ["severe", "minor"],
                    "description": "Anomaly severity filter (for stat_type=anomalies). "
                    "'severe' = theta_level>=2, 'minor' = theta_level=1.",
                },
                "metric_type": {
                    "type": "string",
                    "description": "Anomaly metric filter (sentiment, term_freq, volume...)",
                },
                "trc_code": {
                    "type": "string",
                    "description": "Filter anomalies to one TRC",
                },
                "date_range": {
                    "type": "string",
                    "description": "ISO range 'YYYY-MM-DD/YYYY-MM-DD' for anomaly/trend date filter",
                },
                "dimension": {
                    "type": "string",
                    "enum": [
                        "trc", "trc_code", "friction_type",
                        "sub_cluster", "cluster", "sub_pattern",
                        "payer", "insurance_payer",
                        "provider", "provider_id",
                    ],
                    "description": "Trend dimension (for stat_type=trends)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max rows to return (default 20, max 100/500 depending on stat_type)",
                },
                "top_k": {
                    "type": "integer",
                    "description": "(friction_distribution only) Top N friction types, default 10, max 20",
                },
                "cross_dim": {
                    "type": "string",
                    "enum": ["trc", "payer"],
                    "description": "(friction_distribution only) Nest a per-{trc|payer} breakdown inside each row",
                },
                "friction_type": {
                    "type": "string",
                    "description": (
                        "(friction_distribution only) Filter to a single friction type. "
                        "Known canonical values: incorrect_charge, feature_broken, "
                        "repeat_contact, access_blocked, policy_confusion, missing_information, "
                        "self_serve_failure, process_delay, automation_loop, communication_gap, "
                        "escalation_demand, other. Open enum — novel values return a "
                        "gate_warning rather than an error."
                    ),
                },
                "payer": {
                    "type": "string",
                    "description": "(friction_distribution / trends only) Substring filter on insurance_payer",
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


_MCP_ALLOWED_TOOLS = {t["name"] for t in TOOL_SCHEMAS}


def _execute_tool(name, args):
    """Execute a chat tool and return the result dict.

    Delegates to src.data.chat_tools.registry.dispatch_tool so every
    call is logged to chat_tool_executions with a synthetic session id
    ("adhoc_probe" when no session context is threaded through the MCP
    protocol). Previously this function imported handlers directly,
    bypassing the registry, which is why the audit table stayed empty
    (bug-bash 2026-04-23, F-5).

    Restricts to tool names declared in TOOL_SCHEMAS so legacy registry
    aliases (query_entities, query_tickets, etc.) don't leak through
    the MCP interface.
    """
    if name not in _MCP_ALLOWED_TOOLS:
        return {"error": f"Unknown tool: {name}"}

    conn = _get_db_connection()
    if not conn:
        return {"error": "ALMA_DB_PATH not set"}

    try:
        from src.data.chat_tools.registry import dispatch_tool
        # dispatch_tool returns a JSON string; MCP expects a dict.
        result_json = dispatch_tool(
            tool_name=name,
            args=args,
            conn=conn,
            session_filters={},
            session_id=None,  # resolves to "adhoc_probe" in registry
            message_id=None,
        )
        try:
            return json.loads(result_json)
        except (json.JSONDecodeError, TypeError):
            return {"raw": result_json}
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
