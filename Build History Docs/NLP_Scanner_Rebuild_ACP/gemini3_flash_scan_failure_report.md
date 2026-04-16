# NLP Scanner Failure Report: Gemini 3 Flash — "Unknown" Batch Errors

**Date**: 2026-04-06
**Model**: gemini-3-flash-preview
**CLI Version**: v0.36.0
**Symptom**: 100% batch failure, 0 tickets classified, all retries exhausted
**Status**: Root cause identified, fix pending

---

## 1. Symptom

Every batch completes with tool calls matching the ticket count but zero
classifications stored. The orchestrator retries each batch 3 times and
all fail identically.

Sample log output:
```
Worker worker_1: attempt 1 failed (unknown), retrying...
Worker worker_1: attempt 2 failed (unknown), retrying...
Worker worker_1: batch d4a4d245 complete - 0/24 classified, 24 tool calls, 104.5s
Batch d4a4d245 requeued (retry 1/3)

Worker worker_0: batch 1077f44f complete - 0/23 classified, 23 tool calls, 93.0s
Worker worker_2: batch 93dd907f complete - 0/43 classified, 43 tool calls, 202.1s
```

Key observation: **the model IS calling the tool correctly** (24 tool calls
for 24 tickets). The calls are counted but never produce results.

---

## 2. Root Cause

The classification prompt (`config/prompts/nlp_classify.txt`) was changed
from **fenced code block output** to **native Gemini tool calling**. This
routes execution through a completely different code path that fails inside
the CLI's internal Scheduler.

### What Changed in the Prompt

**BEFORE** (working):
```
For EACH ticket, call the store_classification tool using a fenced code block.
Output one block per ticket, with NO text before, between, or after:

\`\`\`tool_call
{"tool": "store_classification", "args": {
  "ticket_id": "<ticket_id>",
  "sub_cluster": "...",
  ...
}}
\`\`\`
```

**AFTER** (broken):
```
For EACH ticket, call the store_classification tool. Call it once per
ticket, with NO text before, between, or after the tool calls:

store_classification arguments:
  ticket_id: "<ticket_id>"
  sub_cluster: "..."
  ...
```

The old prompt produced **text** containing fenced blocks. The new prompt
triggers **native function calling** in the Gemini API.

---

## 3. The Two Code Paths

### Path A: Fenced Code Blocks (OLD — WORKING)

```
Model outputs text with ```tool_call fences
        |
        v
Bridge streams text as "content" events to Python
        |
        v
Python StreamParser extracts ```tool_call JSON blocks
        |
        v
Python ToolRegistry.execute("store_classification", args)
        |
        v
SQLite INSERT into nlp_ticket_classifications
        |
        v
Worker counts classified_ids, returns success
```

This path never touches the CLI's Scheduler. The model just outputs text,
the bridge forwards it, and Python does all the parsing and storage. It is
the proven path used across all prior builds (5.0 through hardening).

### Path B: Native Tool Calling (NEW — BROKEN)

```
Model emits GeminiEventType.ToolCallRequest events (24 of them)
        |
        v
Bridge emits "tool_call" JSON to Python (worker counts: tool_calls += 1)
Bridge also collects toolCallRequests[] array
        |
        v
Stream ends. Bridge calls: scheduler.schedule(toolCallRequests, signal)
        |
        v
*** SCHEDULER THROWS AN EXCEPTION ***
        |
        v
catch-all handler: classifyError(e) -> { error: "unknown" }
Bridge emits: { type: "error", error: "unknown" }
        |
        v
Python receives error event. Worker returns error: "unknown"
0 "tool_result" events were ever emitted -> 0 classified
        |
        v
Orchestrator retries batch. Same failure. All retries exhausted.
```

The critical failure point is `scheduler.schedule()` — the CLI's internal
Scheduler that executes tool calls. Our `store_classification` tool IS
registered (boot log confirms `tools=store_classification`), but the
Scheduler fails during execution. The exception is caught by the bridge's
generic catch-all, classified as "unknown", and the batch fails.

---

## 4. Why the Scheduler Fails

The Scheduler is an internal class from the Gemini CLI v0.36.0 bundle.
We instantiate it per-call in `gemini_bridge.mjs`:

```javascript
const scheduler = new Scheduler({
  context: { config, messageBus: config.getMessageBus() },
  messageBus: config.getMessageBus(),
  getPreferredEditor: () => undefined,
  schedulerId: ROOT_SCHEDULER_ID,
});
```

The Scheduler calls our tool's `execute()` method via the registered
`StoreClassificationInvocation`. The invocation is a no-op:

```javascript
async execute(_signal) {
  const tid = this.params?.ticket_id || 'unknown';
  return {
    llmContent: JSON.stringify({ status: 'stored', ticket_id: tid }),
    returnDisplay: `Classification stored for ${tid}`,
  };
}
```

Despite being a no-op, the Scheduler wraps this in additional infrastructure
(permission checks, tool confirmation flows, error handling) that can throw
for reasons outside our control. The exact exception message is not captured
because:

1. `classifyError()` returns `{ error: "unknown", raw: msg.slice(0,500) }`
   for unrecognized errors
2. The `raw` field was not propagated through the Python bridge wrapper
   (fixed in this session — see Section 7)
3. The `message` field on the error event appears to be empty for this
   specific failure mode

A secondary diagnostic clue: the bridge boot log shows
`params=0 fields` for the tool schema probe:

```
store_classification registered — schema.name=store_classification, params=0 fields
```

This means `getSchema().parameters.properties` is empty, suggesting the
CLI's `BaseDeclarativeTool` does not surface the `parameterSchema` we
pass to its constructor via `getSchema()`. Whether this causes the
Scheduler failure or is merely cosmetic is unconfirmed, but it has been
this way since the tool was first registered and did not prevent the
fenced-block path from working.

---

## 5. Why It Worked Yesterday

The prompt was changed as part of the Build 11.0 data path updates. The
git diff of `config/prompts/nlp_classify.txt` shows the fenced block
format was replaced with the native tool calling format. This change was
made alongside other data path changes:

- `tool_registry.py`: Added boundary guard, ticket_index upsert,
  switched to `get_connection()`, added warehouse query for get_full_thread
- `worker_agent.py`: Added dedup gate (has a bug: `_db_path` vs `db_path`),
  added `tool_result` event handler for native tool calls
- `scan_orchestrator.py`: Various data path updates

The `tool_result` handler was added to the worker specifically to support
native tool calling — confirming this was an intentional migration from
fenced blocks to native calls. However, the migration is incomplete
because the bridge-side Scheduler execution fails before any `tool_result`
events are emitted.

---

## 6. Additional Issues Found

### 6a. Dedup Gate AttributeError

```
Dedup gate failed (proceeding without): 'ToolRegistry' object has no attribute '_db_path'
```

In `worker_agent.py` line 141:
```python
dedup_conn = get_connection(self.tool_registry._db_path)
```

`ToolRegistry` stores the path as `self.db_path` (no leading underscore),
not `self._db_path`. This causes the dedup gate to fail on every batch.
The try/except catches it and proceeds without dedup, so it's non-blocking
but the dedup feature is completely non-functional.

### 6b. Stall Timeouts

Some batches fail with `stall_timeout` instead of `unknown`. The bridge's
stall timer fires at 150s if no content events are received. When the model
spends all its time making tool calls (which emit `tool_call` events, not
`content` events), the stall timer's `lastContentTime` is never updated.
For large batches (43 tickets), tool execution + Scheduler overhead can
exceed 150s, triggering a false stall.

### 6c. Error Propagation Gap

Before this session's fixes, the `raw` error detail from `classifyError()`
was not propagated through the Python bridge wrapper. The orchestrator
retry log showed just "unknown" with no diagnostic detail. Fixed by adding
`result["raw"] = event.data.get("raw", "")` to `call_streaming()`.

---

## 7. Fixes Applied This Session

| File | Change | Purpose |
|------|--------|---------|
| `gemini_bridge_wrapper.py:546` | Added `result["raw"] = event.data.get("raw", "")` | Propagate raw error detail from bridge to Python |
| `gemini_bridge.mjs:407` | Changed `recoverable: false` to `recoverable: true` for unknown errors | Allow retries on unrecognized errors |
| `gemini_bridge.mjs:406-412` | Added 5 new error patterns: `server_internal`, `safety_filter`, `truncation`, `server_overloaded`, `deadline_exceeded` | Better error classification for Gemini 3 Flash |

These are diagnostic/resilience improvements. They do NOT fix the root cause.

---

## 8. Recommended Fix

**Revert the prompt to fenced code block format.** This is a one-line
conceptual change that routes execution back through Path A (text parsing)
instead of Path B (CLI Scheduler).

Specifically, restore `config/prompts/nlp_classify.txt` to the prior version
that uses:
```
\`\`\`tool_call
{"tool": "store_classification", "args": {...}}
\`\`\`
```

This bypasses the CLI Scheduler entirely. The model outputs text, Python
parses it, Python stores it. No dependency on CLI internals for tool
execution.

### Why Not Fix the Native Tool Path Instead?

1. `scheduler.schedule()` is inside the CLI bundle — we cannot patch it
2. The exact exception is not surfaced (empty message field)
3. The `params=0 fields` schema issue suggests deeper CLI incompatibility
4. The fenced block path is battle-tested across 600+ successful scans
5. Native tool calling adds no benefit — our tool is a no-op on the bridge
   side; all real work happens in Python

### Additional Cleanup

1. Fix dedup gate: `self.tool_registry._db_path` -> `self.tool_registry.db_path`
2. Consider keeping the `tool_result` handler in the worker as a fallback
   for future CLI versions that may fix the Scheduler
3. Update stall timer to count `tool_call` events as activity (prevents
   false stalls on tool-heavy batches)

---

## 9. File Reference

| File | Role in Pipeline |
|------|-----------------|
| `config/prompts/nlp_classify.txt` | Classification prompt — controls output format |
| `src/gemini/bridge_tools.mjs` | Registers `store_classification` in CLI tool registry |
| `src/gemini/gemini_bridge.mjs` | Bridge: streams events, executes tools via Scheduler |
| `src/agents/gemini_bridge_wrapper.py` | Python: reads bridge JSON events, routes to workers |
| `src/agents/worker_agent.py` | Python: processes events, extracts classifications |
| `src/agents/tool_registry.py` | Python: validates and stores classifications to SQLite |
| `src/agents/scan_orchestrator.py` | Python: batch dispatch, retry logic, progress tracking |
| `src/agents/stream_parser.py` | Python: extracts fenced code blocks from text stream |

---

## 10. Test Evidence

### E2E Scan Run 1 (via GUI, user-initiated)
- 24 batches, 3 workers, gemini-3-flash-preview
- Canary probes: 3/3 OK, P50=4309ms
- Every batch: tool calls = ticket count, classified = 0
- All batches failed after 3 retries

### E2E Scan Run 2 (via test script, 1 worker, DEBUG logging)
- Same results: 0/24 classified, 24 tool calls per batch
- Bridge boot log: `tools=store_classification`, `params=0 fields`
- Dedup gate error on every batch: `'ToolRegistry' object has no attribute '_db_path'`
- Mix of `unknown` and `stall_timeout` failures

### Git Diff Confirmation
- `config/prompts/nlp_classify.txt`: fenced blocks removed, native call format added
- `src/agents/worker_agent.py`: `tool_result` handler added (for native path)
- `src/agents/tool_registry.py`: data path refactored (connection_factory, warehouse query)
