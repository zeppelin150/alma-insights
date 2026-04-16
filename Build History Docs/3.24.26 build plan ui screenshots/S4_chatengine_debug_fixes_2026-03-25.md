# Session 4: ChatEngine + Debug Fixes — Shared Chat Layer + 6 Bug Fixes

**Date**: 2026-03-25
**Previous Session**: S3_codebase_clarity_2026-03-24.md
**Focus**: Fix 6 broken Build 11.0 features, then extract a shared ChatEngine powering all Gemini chat interactions with tool execution, model selection, and thinking indicators.

---

## Summary

This session had two phases:

**Phase 1 (Bug Fixes):** Fixed 6 broken features from Build 11.0 — Report History View button, missing duration tracking, wrong-table summary widget, dead evidence panel, silent Gemini Chat failures, and unwired Manage Prompts buttons.

**Phase 2 (ChatEngine):** Extracted a shared `ChatEngine` class from 3 duplicate chat implementations (`ChatWorker`, `GeminiChatWorker`, `PromptBuilderWorker`), added a prompt-engineered tool execution loop giving Gemini read access to the local DB, dynamic model selection from CLI, and "Gemini is thinking..." status indicators.

---

## Phase 1: Debug Fixes (6 Bugs)

### Bug 1: Report History "View" Button Does Nothing

**Root cause:** Build 11.0 added `analysis_runs` table for report persistence but never completed the read-back chain. Four breaks: signal not connected, wrong table queried, no DB accessor, column mismatch.

**Fix:**
- `db_manager.py` — Added `get_report_run(run_id)`, `get_report_run_count()`, `get_latest_report_runs()` methods targeting `analysis_runs` table
- `ai_reports.py` — Connected `view_report_requested` signal on `ReportHistoryTab`, added `_on_view_report(run_id)` handler that loads report, renders in MarkdownViewer, switches to Analysis Canvas tab

### Bug 2: Missing Duration in Report History

**Root cause:** `AIReportPipeline.run()` computed elapsed time but never put it in the result dict.

**Fix:** One line in `ai_report_pipeline.py:117` — `result["duration_sec"] = round(elapsed, 1)`. The persist path already read this key.

### Bug 3: Summary Widget Always Says "No Reports Yet"

**Root cause:** `ReportHistorySummary.refresh()` queried `analysis_reports` (legacy table). New AI Reports go to `analysis_runs`.

**Fix:** `report_history_summary.py` — Branch on `page_key == "ai_reports"` to query `analysis_runs` via new DB methods. After persist, refresh both summary widget and history tab.

### Bug 4: Gemini Chat Silent Failures

**Root cause:** 5 `except: pass` blocks in `gemini_chats_page.py` swallowed all errors silently.

**Fix:** Replaced all 5 with `logger.warning(...)` calls. Added diagnostic test suite (`test_gemini_chat_pipeline.py`, 20 tests).

### Bug 5: Evidence Panel Never Populates

**Root cause:** `EvidencePanel` (222 LOC, fully implemented) was created at `ai_reports.py:728` with zero signal connections.

**Fix:**
- Wired `show_metadata()` after report generation and in `_on_view_report()`
- Added `ticket_clicked` signal to `MarkdownViewer` — ticket ID patterns (#12345) are now linkified as `ticket://` URLs, clicks route to `evidence_panel.show_ticket()`
- `markdown_viewer.py` — Changed to `setOpenExternalLinks(False)`, added `_on_link_clicked` handler that routes ticket URLs to signal, others to `QDesktopServices`

### Bug 6: Manage Prompts Test & Save Buttons Unwired

**Root cause:** Buttons existed at lines 192-209 with no `.clicked.connect()`.

**Fix:** Wired Save button to `_on_save_prompt()` (uses existing `db.save_prompt()`), wired Test button to `_on_test_prompt()` (launches test run via worker).

---

## Phase 2: ChatEngine

### Problem

Three separate chat implementations duplicated the same patterns:

| Implementation | File | LOC | Used By |
|---|---|---|---|
| `ChatWorker` | `chat_widget.py` | 95 | Follow-Up Chat (AI Reports) |
| `GeminiChatWorker` | `gemini_chats_page.py` | 35 | Standalone Gemini Chats |
| `PromptBuilderWorker` | `ai_reports_prompts_tab.py` | 17 | Manage Prompts Builder |

All three duplicated: QThread worker, history list, client lifecycle, conversation formatting. The Gemini Chats page was broken because it built a new client per message inside the thread (vs. the working Follow-Up Chat which received a pre-built cached client).

Additionally, Gemini had **no tool execution** — the context injector told Gemini about `alma_query` tools, but Gemini CLI is stateless text-in/text-out and couldn't actually call them.

### Solution: Shared ChatEngine

**New file:** `src/services/chat_engine.py` (418 LOC)

```
ChatEngine(QObject)
├── Signals: response_ready, error_occurred, busy_changed, status_update
├── Client strategies:
│   ├── Build-per-message (default) — fresh client via build_client_for_task()
│   └── Warm client (opt-in) — injected via set_client() for bridge cold-start avoidance
├── Callbacks (consumer-configurable):
│   ├── context_provider(msg, history) → str — context block prepended to prompt
│   ├── history_packer(msg, history) → str — custom conversation formatting
│   └── response_handler(raw) → str — post-processing (e.g., template extraction)
├── Tool Executor (opt-in via tools_enabled=True):
│   ├── Prompt-engineered TOOL_CALL parsing from Gemini responses
│   ├── 6 tools: query_tickets, ticket_detail, query_trends, query_anomalies, compare_periods, query_insights
│   ├── Execute against local SQLite DB (no subprocess, direct import)
│   ├── Resubmit results in second LLM call
│   └── Max 3 round-trips (infinite loop guard)
├── Model selection: set_model(model_id) overrides per-send()
└── Internal _ChatWorker(QThread) — private, consumers never touch it
```

### Consumer Wiring

| Consumer | Client Strategy | Callbacks | Tools | Model Picker |
|----------|----------------|-----------|-------|-------------|
| **Gemini Chats** | Build-per-message | `context_provider` (context_injector) | Yes | Yes (dynamic from CLI) |
| **Prompt Builder** | Build-per-message | `history_packer` + `response_handler` | No | Yes (dynamic from CLI) |
| **Follow-Up Chat** | Warm client (bridge) | `context_provider` (drilldown) + `history_packer` (last 5 Q&A) | No | No (inherits from report) |

### Tool Execution Architecture

```
User sends message
  ↓
ChatEngine.send()
  → Pack prompt → Prepend context → Build client → Launch _ChatWorker
    ↓
_ChatWorker calls client.generate()
  → Gemini returns: "I need to check the data.\nTOOL_CALL: query_tickets {"trc": "Billing"}"
    ↓
Engine parses TOOL_CALL regex
  → Executes _run_query_tickets({"trc": "Billing"}, sqlite_conn)
  → Returns: {"tickets": [...], "count": 3}
    ↓
Engine builds follow-up: original_response + "TOOL_RESULT: {json}"
  → Launches second _ChatWorker
    ↓
Gemini returns final response with actual data
  → Engine emits response_ready(final_text)
```

**System prompt addendum** (when tools_enabled=True):
```
When you need data, output: TOOL_CALL: <tool_name> <args_json>
Available: query_tickets, ticket_detail, query_trends, query_anomalies, compare_periods, query_insights
Do NOT make up data. If you need data, use a tool call.
```

### UX Additions

**Thinking indicator:** Each consumer shows a status label during LLM calls:
- Gemini Chats: "Gemini is thinking..." with animated dots (QTimer, 400ms cycle)
- Prompt Builder: "Building prompt..."
- Follow-Up Chat: "Thinking..." / "Querying data + thinking..." (drilldown-aware)

All driven by `busy_changed(bool)` and `status_update(str)` signals.

**Model selector:** QComboBox in Gemini Chats and Prompt Builder headers. Populated via `GeminiClient.list_models()` which queries the CLI dynamically, falling back to hardcoded defaults `["gemini-2.5-flash", "gemini-2.0-flash"]` on failure. Results cached after first call.

### OAuth Race Condition

**Risk:** Gemini CLI uses OAuth when no API key is set. If the NLP/VOC pipeline (bridge subprocess) and chat run simultaneously, concurrent OAuth token refreshes can conflict.

**Mitigations:**
- Build-per-message is safe — each subprocess is short-lived and sequential per user action
- Warm-client path (Follow-Up Chat) reuses the bridge's persistent subprocess, serializing calls
- Longer-term fix: enforce API key auth (documented, out of scope)

---

## Files Changed

### New Files

| File | LOC | Purpose |
|------|-----|---------|
| `src/services/chat_engine.py` | 418 | Shared ChatEngine + _ChatWorker + tool executor |
| `tests/test_chat_engine.py` | 426 | 23 tests: base engine, callbacks, client strategies, tool dispatch, tool loop, status signals |

### Modified Files

| File | LOC | Changes |
|------|-----|---------|
| `src/data/db_manager.py` | 2422 | +3 methods: `get_report_run`, `get_report_run_count`, `get_latest_report_runs` |
| `src/data/ai_report_pipeline.py` | 216 | +1 line: populate `duration_sec` in result dict |
| `src/gemini/gemini_client.py` | 324 | +`list_models()`, `_parse_model_list()` — dynamic model discovery from CLI |
| `src/ui/pages/ai_reports.py` | 1585 | Signal wiring, `_on_view_report()`, evidence panel metadata, refresh calls, ticket signal connect |
| `src/ui/pages/gemini_chats_page.py` | 442 | Full rewrite: deleted `GeminiChatWorker`, ChatEngine integration, model combo, status label, tools_enabled |
| `src/ui/pages/ai_reports_prompts_tab.py` | 679 | Deleted `PromptBuilderWorker`, ChatEngine integration, model combo, status label, Save/Test wiring |
| `src/ui/widgets/chat_widget.py` | 916 | ChatEngine integration (warm path), `_provide_drilldown_context` + `_pack_followup_history` callbacks, FindingWorker path preserved |
| `src/ui/widgets/markdown_viewer.py` | 246 | `ticket_clicked` signal, `_on_link_clicked`, `_linkify_tickets` regex preprocessor |
| `src/ui/widgets/report_history_summary.py` | 153 | Branch `refresh()` for ai_reports page key to query `analysis_runs` |
| `tests/test_gemini_chat_pipeline.py` | 410 | Updated tests to use ChatEngine instead of deleted GeminiChatWorker |

---

## Test Results

| Test File | Tests | Result |
|-----------|-------|--------|
| `test_chat_engine.py` | 23 | 23 passed |
| `test_gemini_chat_pipeline.py` | 20 | 19 passed, 1 skipped (API key) |
| `test_build11_regression.py` | 13 | 13 passed |
| `test_reporting_foundation.py` | 29 | 28 passed, 1 pre-existing failure (bridge_fallback) |
| `test_phase5_regression.py` | 39 | 39 passed |
| **Total** | **124** | **122 passed, 1 skipped, 1 pre-existing** |

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────┐
│                   ChatEngine (QObject)               │
│                                                       │
│  Signals: response_ready, error_occurred,            │
│           busy_changed, status_update                │
│                                                       │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────┐ │
│  │ history_packer│  │context_provider│ │response_   │ │
│  │  (callback)   │  │  (callback)   │ │handler     │ │
│  └──────┬───────┘  └──────┬───────┘  └─────┬──────┘ │
│         │                  │                 │        │
│  ┌──────▼──────────────────▼─────────────────▼──────┐│
│  │              send(user_message)                    ││
│  │  1. Pack prompt  2. Prepend context  3. Get client││
│  │  4. Launch _ChatWorker thread                     ││
│  │  5. On response: check for TOOL_CALL              ││
│  │     → execute tool → resubmit (max 3 rounds)     ││
│  └───────────────────────────────────────────────────┘│
│                                                       │
│  Tool Dispatch (6 tools, direct SQLite queries):     │
│  query_tickets, ticket_detail, query_trends,         │
│  query_anomalies, compare_periods, query_insights    │
└──────────────┬──────────────┬──────────────┬─────────┘
               │              │              │
    ┌──────────▼──┐  ┌───────▼────┐  ┌──────▼───────┐
    │ Gemini Chats │  │  Prompt    │  │  Follow-Up   │
    │   Page       │  │  Builder   │  │  Chat        │
    │              │  │            │  │              │
    │ tools=True   │  │ packer +   │  │ warm client  │
    │ model picker │  │ handler    │  │ drilldown    │
    │ status dots  │  │ model pick │  │ context      │
    └──────────────┘  └────────────┘  └──────────────┘
```

---

## Migration Path for Each Consumer

### Gemini Chats Page (simplest)
- **Before:** `GeminiChatWorker` built client inside thread, no tool access, no model selection, silent failures
- **After:** ChatEngine with `tools_enabled=True`, model QComboBox, animated status label, build-per-message client strategy, context injection via `build_context()`

### Prompt Builder (callbacks)
- **Before:** `PromptBuilderWorker` built client inside thread, Save/Test buttons unwired
- **After:** ChatEngine with `history_packer` (adds pseudo-response for template guidance) + `response_handler` (extracts `{variable}` patterns to preview pane), model QComboBox, status label

### Follow-Up Chat (warm client + drilldown)
- **Before:** `ChatWorker` received pre-built client, drilldown enrichment ran in worker thread
- **After:** ChatEngine with `set_client()` warm path, `context_provider` (drilldown enrichment runs synchronously — fast DB queries), `history_packer` (last 5 Q&A + original report), `FindingWorker` path preserved as-is (bypasses engine)

---

## Known Issues / Future Work

1. **OAuth race condition** — documented risk when NLP pipeline and chat run concurrently without API key auth. Longer-term fix: enforce API key or serialize CLI access through singleton.

2. **Tool reliability** — Gemini's compliance with the `TOOL_CALL:` format depends on prompt engineering. In testing, Gemini 2.5 Flash follows the format reliably. Less capable models may not. The 3-round max limit prevents infinite loops.

3. **Model discovery** — `GeminiClient.list_models()` tries two CLI patterns (`gemini models list`, `gemini --list-models`). If neither works, falls back to hardcoded defaults. The exact CLI syntax depends on the installed Gemini CLI version.

4. **Drilldown enrichment** moved from worker thread to main thread (context_provider callback). DB queries are <100ms on typical data sizes. If profiling shows blocking on large datasets, can add an async pre-processor to the engine in a future build.

5. **Pre-existing test failure** — `test_bridge_fallback_when_unavailable` in `test_reporting_foundation.py` fails (pre-existing, unrelated to this session's changes).

---

## What to Test Manually

1. **Gemini Chats:** Navigate to page → model combo populated → type message → see "Gemini is thinking..." → get response with tool-grounded data → session persisted
2. **Prompt Builder:** Select prompt → chat with builder → template extracted to preview → Save works → Test run works → model combo works
3. **Follow-Up Chat:** Generate report → ask follow-up → "Thinking..." / "Querying data + thinking..." → drilldown-enriched response
4. **Report History:** Generate report → history tab shows duration → click View → renders in canvas → summary widget updated
5. **Evidence Panel:** Generate report → panel shows metadata → click #12345 ticket ID in report → panel shows ticket detail
