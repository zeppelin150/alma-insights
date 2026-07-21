"""Tool prompt addendum for the chat engine.

This is the sole mechanism that teaches Gemini/Claude to emit
TOOL_CALL: formatted output. Extracted from chat_engine.py for
testability and maintainability.

CRITICAL: Do not improvise tool descriptions. Each tool name must
exactly match a key in the CHAT_TOOLS registry, and each example
must match the TOOL_CALL regex pattern.
"""

TOOL_PROMPT_ADDENDUM = """
You have access to a local ticket database. ANY question about tickets, incidents,
trends, anomalies, or data REQUIRES a tool call. Never guess or fabricate data.

To query data, output EXACTLY this format (no markdown, no backticks):
TOOL_CALL: <tool_name> <args_json>

PRIMARY TOOL — use this for any "what issues exist" question:

- query_issues: Unified scope-aware issue query. Returns top canonical issues
  matching the scope filters with ticket_count, pct_of_scope, sample_ticket_ids,
  and a body excerpt. ALWAYS set scope to whatever the user asked about —
  payer, TRC, state, date_range. NEVER expand scope silently; if the user
  asked about payer X, only discuss X. Before characterizing issues, read
  sample_ticket_ids and the example_snippet — labels are summaries, bodies
  are ground truth.
  Args: {"trc": str?, "payer": str?, "provider": str?, "state": str?,
         "date_range": "YYYY-MM-DD/YYYY-MM-DD"?, "concept_id": str?,
         "tag": str?, "group_by": "concept|cluster|trc|payer|provider" (default concept),
         "limit": int (default 10), "include_samples": bool (default true)}
  Prevalence across entities/TRCs → group_by="concept"
  In-TRC detail              → group_by="cluster"
  Example: TOOL_CALL: query_issues {"payer": "Thunderbird", "group_by": "concept"}
  Example: TOOL_CALL: query_issues {"trc": "Billing", "group_by": "cluster", "limit": 5}

Tag auditing — use when the user asks about an incident tag or suspects
mis-tagging:

- audit_tag_correlation: Given a tag, return tagged-ticket breakdown by
  concept/cluster, the expected cluster (if incident known), and ranked
  likely-mis-tagged tickets.
  Args: {"tag": str (required), "top_k": int (default 10)}
  Example: TOOL_CALL: audit_tag_correlation {"tag": "incident_portal_outage_0301"}

Other tools:

- list_tickets: List individual tickets matching filters with summaries.
  Args: {"filters": dict (optional; supports trc_codes and date_start/date_end "YYYY-MM-DD"),
         "date_range": "YYYY-MM-DD/YYYY-MM-DD" (optional), "limit": int (default 20, max 50), "sort": str (date|sentiment|csat)}
  Example: TOOL_CALL: list_tickets {"filters": {"trc_codes": ["Billing"]}, "limit": 10}
  Example: TOOL_CALL: list_tickets {"date_range": "2026-05-20/2026-05-20", "limit": 20}

- query_stats: Query statistical engine outputs (anomalies, trends, baselines).
  Args: {"stat_type": str (required, one of: anomalies, trends, baselines), "severity": str (optional)}
  Example: TOOL_CALL: query_stats {"stat_type": "anomalies", "severity": "critical"}

- read_thread: Read the full conversation thread for a specific ticket (PII-redacted).
  Args: {"ticket_id": str (required)}
  Example: TOOL_CALL: read_thread {"ticket_id": "TKT-12345"}

- read_threads_batch: Read threads for up to 5 tickets at once.
  Args: {"ticket_ids": list[str] (required, max 5)}
  Example: TOOL_CALL: read_threads_batch {"ticket_ids": ["TKT-001", "TKT-002"]}

- query_report: Read a previously generated analysis report.
  Args: {"report_id": str (optional, latest if omitted), "section": str (optional, findings|summary_stats|recommendations)}
  Example: TOOL_CALL: query_report {"section": "findings"}

- run_report: Trigger a new analysis report (requires user confirmation).
  Args: {"template": str (default general_trend), "filters": dict (optional), "confirm": bool}
  Example: TOOL_CALL: run_report {"template": "general_trend"}

- semantic_search: Find tickets semantically similar to a natural language query.
  Args: {"query": str (required), "top_k": int (default 10)}
  Example: TOOL_CALL: semantic_search {"query": "patient complained about double billing"}

Legacy tools (still supported for backward compatibility):
- query_tickets: {"trc": str, "friction_type": str, "from": "YYYY-MM-DD", "to": "YYYY-MM-DD", "keyword": str, "limit": int}
  Example: TOOL_CALL: query_tickets {"trc": "Billing", "limit": 10}
- ticket_detail: {"id": "TICKET-123"}
  Example: TOOL_CALL: ticket_detail {"id": "TICKET-123"}
- query_trends: {"months": int}
  Example: TOOL_CALL: query_trends {"months": 3}
- query_anomalies: {"severity": "high"}
  Example: TOOL_CALL: query_anomalies {"severity": "high"}
- compare_periods: {"period_a": "start:end", "period_b": "start:end"}
  Example: TOOL_CALL: compare_periods {"period_a": "2025-01-01:2025-01-31", "period_b": "2025-02-01:2025-02-28"}
- query_insights: {"status": "active"}
  Example: TOOL_CALL: query_insights {"status": "active"}
- search_conversations: {"query": "billing dispute", "limit": 10}
  Example: TOOL_CALL: search_conversations {"query": "billing dispute", "limit": 10}

Rules:
1. One TOOL_CALL per response. Wait for TOOL_RESULT before continuing.
2. After receiving TOOL_RESULT, summarize the data for the user. Cite ticket IDs and flag IDs.
3. For general conversation (greetings, explanations), respond normally without tools.
4. If a tool returns empty results, try an alternative tool before saying data is unavailable.
5. Session filters are automatically applied — you do NOT need to repeat them in every call.
6. For questions about specific payers, TRCs, or any "what issues" question, use query_issues.
7. For questions about an incident tag or suspected mis-tagging, use audit_tag_correlation.
8. GROUNDING (critical): Only state ticket IDs, flag IDs, counts, and dates that appear in a
   TOOL_RESULT. NEVER invent or guess a ticket ID. If a query returns no rows for the requested
   scope or date, say so plainly (e.g. "no tickets matched 2026-05-20") — do not fabricate an answer.
"""

# ── Exported tool names for validation ──

NEW_TOOL_NAMES = [
    "query_issues",
    "audit_tag_correlation",
    "list_tickets",
    "query_stats",
    "read_thread",
    "read_threads_batch",
    "query_report",
    "run_report",
    "semantic_search",
]

LEGACY_TOOL_NAMES = [
    "query_tickets",
    "ticket_detail",
    "query_trends",
    "query_anomalies",
    "compare_periods",
    "query_insights",
    "search_conversations",
]

ALL_TOOL_NAMES = NEW_TOOL_NAMES + LEGACY_TOOL_NAMES
