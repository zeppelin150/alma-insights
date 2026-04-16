# Chat Tools

Structured tool handlers for the hybrid chat architecture.
All tools are dispatched via `registry.dispatch_tool()`.

## Public API

```python
from src.data.chat_tools import dispatch_tool

result_json = dispatch_tool(
    tool_name="query_ticket_classifications",
    args={"group_by": "friction_type"},
    conn=db_connection,
    session_filters={"trc_codes": ["Billing"]},
)
```

## Module Files

| File | Purpose |
|------|---------|
| `registry.py` | Tool registry dict + dispatch function |
| `fast_path.py` | query_classifications, list_tickets, query_findings, query_stats + 7 legacy wrappers |
| `thread_tools.py` | read_thread, read_threads_batch (PII-redacted) |
| `report_tools.py` | query_report, run_report (propose-only until Session 5) |
| `tool_prompts.py` | TOOL_PROMPT_ADDENDUM — the prompt text that teaches LLMs the tool format |
| `tool_logger.py` | Structured JSONL tool execution logging |

## PHI Levels

| Level | Meaning | Tools |
|-------|---------|-------|
| 0 | No PHI — aggregates only | query_ticket_classifications, query_findings, query_stats, query_report |
| 1 | Sanitized snippets | list_tickets |
| 2 | Full thread text (redacted) | read_thread, read_threads_batch |
| 3 | Pipeline trigger | run_report |

## Tool Dispatch Flow

1. ChatEngine parses `TOOL_CALL: tool_name {args}` from LLM response
2. Calls `dispatch_tool(tool_name, args, conn, session_filters)`
3. Registry merges session_filters (base) + args.filters (override)
4. Handler called with (conn, args, effective_filters)
5. Result logged to JSONL, returned as JSON string
