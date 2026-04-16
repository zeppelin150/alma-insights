# Alma Insights — Overnight Build Implementation Plan
## 2026-03-25 | Build 11.0

**Source plan:** `Build History Docs/alma_claude_code_build_plan (2).md`
**Screenshots:** `Build History Docs/3.24.26 build plan ui screenshots/`
**Constraints:** CC < 15, files < 300 LOC (functions), .md at branch tops, unit tests per module, regression after each phase

---

## Build Execution Overview

The overnight build is organized into **8 sequential phases** with regression gates between each. Each phase is self-contained and testable. If a phase fails regression, the build stops and the app remains functional at the last good state.

**Estimated file count:** ~35 new files, ~12 modified files
**Estimated total LOC:** ~6,500 new, ~800 modified

---

## Phase 0: Scaffolding & Migrations (30 min)

### 0.1 Directory scaffolding + README.md files

Create directories and add `.md` description files at every new branch:

```
src/services/README.md          — "Service layer: persistence hooks, context injection, scheduling"
src/tools/README.md             — "CLI tools invoked by Gemini via run_shell_command"
src/data/report_definitions/README.md — "JSON configs for parameterized VOC pipeline runs"
```

**Files created:**
| File | Purpose | Est. LOC |
|------|---------|----------|
| `src/services/__init__.py` | Package init | 1 |
| `src/services/README.md` | Branch doc | 5 |
| `src/tools/__init__.py` | Package init | 1 |
| `src/tools/README.md` | Branch doc | 5 |
| `src/data/report_definitions/README.md` | Branch doc | 5 |

### 0.2 Database migration `005_persistence_layer.sql`

> Why 005, not 006? The existing migrations are 001–004. The build plan says 006 but there's no 005, so we use the next sequential number.

**Tables created (all persistent — survive Clear & Close):**

1. **`ticket_index`** — Core PHI-free ticket record (26 columns, 7 indexes)
2. **`scan_category_snapshots`** — Post-scan TRC/friction distributions (10 cols, 2 indexes)
3. **`analysis_runs`** — Every AI report generation (15 cols, 2 indexes)
4. **`trend_snapshots`** — Statistical engine outputs (12 cols, 2 indexes)
5. **`insight_ledger`** — Significant findings log (13 cols, 2 indexes)
6. **`chat_sessions`** — Chat conversation persistence (11 cols, 1 index)
7. **`report_definitions`** — Smart Report pipeline configs (11 cols)

**File:** `migrations/005_persistence_layer.sql` (~120 LOC)

### 0.3 DatabaseManager migration runner update

Modify `src/data/db_manager.py` to discover and run `005_persistence_layer.sql`.

**Regression gate 0:** App launches, `db.initialize()` runs migration, all 7 new tables exist. Existing pages render. Run `test_qt_regression.py`.

---

## Phase 1: Persistence Services (60 min)

### 1.1 `src/services/ticket_index_writer.py` (~120 LOC)

Three functions (all CC < 10):

| Function | Purpose | CC |
|----------|---------|---|
| `should_classify_ticket(ticket_id, scan_id, conn)` | Returns `'classify'`, `'skip'`, or `'update_scan_id'` | 4 |
| `update_scan_reference(ticket_id, scan_id, conn)` | Marks existing ticket as seen in current scan | 1 |
| `upsert_ticket_index(ticket_id, scan_id, classification, ticket_meta, conn)` | INSERT ON CONFLICT UPDATE to `ticket_index` | 3 |

**Test:** `tests/unit/test_ticket_index_writer.py` — In-memory SQLite, seed 5 tickets, verify dedup logic, verify upsert idempotency.

### 1.2 `src/services/clear_session.py` (~60 LOC)

| Function | Purpose | CC |
|----------|---------|---|
| `clear_session_data(db_path)` | DELETE from ephemeral tables, rebuild FTS5, VACUUM | 2 |

`EPHEMERAL_TABLES` constant list. FTS5 table dropped and recreated.

**Test:** `tests/unit/test_clear_session.py` — Seed ephemeral + persistent tables, run clear, verify persistent survived.

### 1.3 `src/services/post_scan_persist.py` (~130 LOC)

| Function | Purpose | CC |
|----------|---------|---|
| `write_scan_category_snapshot(scan_id, scan_date, conn)` | GROUP BY query from `ticket_index`, write to `scan_category_snapshots` | 3 |
| `write_trend_deltas(scan_id, conn)` | Compare current vs prior snapshot, write to `trend_snapshots` if >20% change | 6 |
| `write_scan_insights(scan_id, conn)` | Check significance thresholds, write to `insight_ledger` | 5 |
| `run_post_scan_persistence(scan_id, scan_date, conn)` | Orchestrator calling the above 3 | 2 |

**Test:** `tests/unit/test_post_scan_persist.py` — Seed `ticket_index` with known distributions, run persistence, verify snapshot/trend/insight rows.

### 1.4 `src/services/post_report_persist.py` (~50 LOC)

| Function | Purpose | CC |
|----------|---------|---|
| `persist_report_run(run_data, conn)` | Insert into `analysis_runs` | 2 |

**Test:** `tests/unit/test_post_report_persist.py`

### 1.5 `src/services/chat_session.py` (~80 LOC)

| Function | Purpose | CC |
|----------|---------|---|
| `create_session(source_page, source_context, filters, conn)` | Insert new `chat_sessions` row | 2 |
| `append_message(session_id, role, content, conn)` | Load messages JSON, append, update | 3 |
| `list_sessions(limit, conn)` | Query recent sessions | 1 |
| `load_session(session_id, conn)` | Load full session with messages | 1 |

**Test:** `tests/unit/test_chat_session.py`

**Regression gate 1:** All 5 new unit test files pass. App launches cleanly. Existing pages unchanged.

---

## Phase 2: Pipeline Integration Hooks (45 min)

### 2.1 Hook into `worker_agent.py` — Dedup gate

**File modified:** `src/agents/worker_agent.py`
**Location:** Between ticket loading and prompt building (step 2 → step 3)
**Change:** Filter ticket list through `should_classify_ticket()`. Log dedup stats.
**CC impact:** +3 to existing method (if/elif/else per ticket)

### 2.2 Hook into stream parser — ticket_index upsert

**File modified:** `src/agents/stream_parser.py` (or `worker_agent.py` store_classification handler)
**Location:** Inside `store_classification` tool call handler
**Change:** Add `upsert_ticket_index()` call after existing `store_nlp_classification()` call.
**CC impact:** +1

### 2.3 Hook into `scan_orchestrator.py` — Post-scan persistence

**File modified:** `src/agents/scan_orchestrator.py`
**Location:** Between meta-analyzer and finalize steps
**Change:** Call `run_post_scan_persistence(scan_id, scan_date, conn)`
**CC impact:** +1

### 2.4 Hook into AI Reports — Post-report persistence

**File modified:** `src/ui/pages/ai_reports.py`
**Location:** Report generation completion handler (`_on_pipeline_finished` or similar)
**Change:** Call `persist_report_run()` with report metadata
**CC impact:** +1

### 2.5 Refactor Clear & Close handler

**File modified:** `src/ui/main_window.py` or wherever Clear & Close is handled
**Change:** Replace database file deletion with `clear_session_data()` call
**CC impact:** -2 (simplification)

**Regression gate 2:** App launches. Can trigger a mock scan cycle. Existing AI Reports still generate. Run `test_qt_regression.py` + `test_feature_integration.py`.

---

## Phase 3: AI Reports Page Redesign — Tab Shell (60 min)

This is the biggest UI change. The current `ai_reports.py` (1,500+ lines) becomes a **tab container** that hosts 4 sub-tabs. We preserve all existing report generation logic.

### 3.1 Refactor `ai_reports.py` into tab container (~200 LOC)

**Strategy:** Extract the current monolithic page into a QTabWidget wrapper.

```
ai_reports.py (MODIFIED — becomes lightweight tab host)
  └── QTabWidget
      ├── Tab 0: "Analysis canvas" → AnalysisCanvasTab (new widget)
      ├── Tab 1: "Report history"  → ReportHistoryTab (new widget)
      ├── Tab 2: "Manage prompts"  → ManagePromptsTab (new widget)
      ├── Tab 3: "A/B Compare"     → ABCompareTab (moved from separate page)
```

**Screenshot reference:** `Updated_AI_Reports_Analysis_Canvas.png` — shows tabs at top: "Analysis canvas | Report history | Manage prompts" with the top bar showing "AI Reports | All TRCs | Jan 1 - Mar 12, 2025"

The refactored `ai_reports.py`:
- Creates QTabWidget with Alma-themed tab bar styling
- Shared filter bar at top (TRC filter, date range) that persists across tabs — emits signals consumed by each tab
- Instantiates each tab widget
- Routes `set_drilldown_panel()` and `populate_trc_filter()` to sub-tabs

### 3.2 `src/ui/pages/ai_reports_canvas_tab.py` (~280 LOC)

**Screenshot reference:** `Updated_AI_Reports_Analysis_Canvas.png`

Layout: QSplitter (horizontal)
- **Left panel (60%):** Report selector dropdown, structured report output in QScrollArea, chat input + messages below report (continuous flow)
- **Right panel (40%):** Evidence panel — ticket preview, trend sparkline, report metadata

Key UI elements from screenshot:
- Report dropdown: "[VOC] Root Cause Analysis — Mar 24" with metadata "880 tickets · 127 TRCs · 3 specialist rounds"
- Executive summary section
- "Top friction points" with severity badges (DETERIORATING/STABLE)
- "What's getting worse" section
- Chat input: "Ask a follow-up question about this report" with Send button
- Evidence panel: "SELECTED FINDING: BILLING DISCREPANCIES" with ticket cards (#19128, #19513), trend chart, report metadata

**Widgets needed (new):**
| Widget | File | Est. LOC | Purpose |
|--------|------|----------|---------|
| `EvidencePanel` | `src/ui/widgets/evidence_panel.py` | 200 | Right-side reactive panel |
| `ReportSectionRenderer` | `src/ui/widgets/report_section_renderer.py` | 180 | Parse markdown into clickable section cards |
| `TrendSparkline` | `src/ui/widgets/trend_sparkline.py` | 80 | Mini chart via QPainter |
| `TicketPreviewCard` | `src/ui/widgets/ticket_preview_card.py` | 100 | Compact ticket display in evidence panel |

**Signals architecture:**
```
finding_clicked(dict)  →  EvidencePanel.show_finding()
ticket_id_clicked(str) →  EvidencePanel.show_ticket()
report_loaded(dict)    →  EvidencePanel.show_metadata()
```

### 3.3 `src/ui/pages/ai_reports_history_tab.py` (~180 LOC)

**Screenshot reference:** `Updated_AI_Reports_Report_History_Page.png`

Layout:
- Filter pills: "All | VOC | Billing | Engineering | Custom" (QPushButton group)
- Search bar: "Search reports..." QLineEdit
- QTableView with columns: Report, Date, Tickets, TRC Filter, Duration, Cost, Actions
- Action buttons per row: View (switches to Analysis Canvas with that report loaded)

Backed by QSortFilterProxyModel over `analysis_runs` table.

### 3.4 `src/ui/pages/ai_reports_prompts_tab.py` (~250 LOC)

**Screenshot reference:** `Updated_AI_Reports_Prompt_Builder_Page.png`

Layout: QSplitter (horizontal)
- **Left sidebar:** QListWidget with built-in prompts ("[VOC] Root Cause Analysis", "[VOC] Executive Summary", "[TRC] Thematic Analysis", "[TREND] Deterioration") and custom prompts ("Client churn analysis", "Payer performance"). "+ New prompt" button at bottom.
- **Right panel:** Conversational prompt builder with chat interface. Shows Gemini walking user through building a report prompt. Generated prompt preview at bottom in QPlainTextEdit. Buttons: "Preview prompt", "Test run", "Save".

**Key interaction from screenshot:**
- User selects "Client churn analysis" (Custom · Building)
- Chat shows Gemini asking questions about report goals, data scope, etc.
- User responds with what they want
- Generated prompt preview shows the assembled template with variables

### 3.5 Move A/B Compare into AI Reports as Tab 4

**File modified:** `src/ui/pages/ab_compare.py`
**Change:** Extract the main content widget into a reusable class `ABCompareWidget` that can be hosted either as a standalone page OR as a tab.

**File modified:** `src/ui/main_window.py`
**Change:** Remove `PAGE_AB_COMPARE` from sidebar. Add A/B Compare as 4th tab in AI Reports. Update page index constants (shift down by 1 for everything after).

**Regression gate 3:** AI Reports page shows 4 tabs. Analysis Canvas renders with split layout. Report History shows empty table (no data yet). Manage Prompts shows existing prompts. A/B Compare tab works. Sidebar no longer shows A/B Compare as separate item. Run `test_qt_regression.py`.

---

## Phase 4: Gemini Chats Page (45 min)

### 4.1 `src/ui/pages/gemini_chats_page.py` (~250 LOC)

**Screenshot reference:** `New_Gemini_Chats.png`

Net-new sidebar page — a full-width conversational UI for Gemini CLI.

Layout from screenshot:
- Top: "Chat" title with filter chips: "Billing Investigation", "Eligibility deep dive", "New chat"
- Chat area: Message bubbles with Gemini responses
- Example interaction shows investigation-style Q&A about ticket anomalies
- Bottom: "Ask anything about your ticket data..." input with Send button

**Architecture:**
- Reuses existing `ChatWorker` from `chat_widget.py` for Gemini CLI calls
- Manages `chat_sessions` for persistence
- Session selector as filter chips / dropdown at top
- Context injection via `ContextInjector` (Phase 5)

### 4.2 `src/services/context_injector.py` (~100 LOC)

Builds the `[SYSTEM CONTEXT]` block prepended to every chat message:

| Function | Purpose | CC |
|----------|---------|---|
| `build_context(page_state, db_path)` | Assembles context string from page state + DB queries | 5 |
| `_get_data_scope(db_path, trc_filter, date_range)` | Ticket count, pattern count, anomaly count | 2 |
| `_get_ephemeral_status(db_path)` | Checks if conversations table has rows | 1 |
| `_get_scan_recency(db_path)` | Latest scan date from nlp_scan_runs | 1 |

**Test:** `tests/unit/test_context_injector.py`

### 4.3 MainWindow integration

**File modified:** `src/ui/main_window.py`

Changes:
- Add `PAGE_GEMINI_CHATS` constant (insert between existing pages — new index)
- Import and instantiate `GeminiChatsPage`
- Add sidebar button: "💬 Gemini Chats" under a new "CHAT" section (or under REPORTS)
- Add to content_stack
- Wire drilldown panel

**Updated page constants:**
```python
PAGE_CONVERSATIONS = 0
PAGE_DASHBOARD = 1
PAGE_TRENDING = 2
PAGE_INCIDENTS = 3
PAGE_REPORTS = 4
# PAGE_AB_COMPARE removed (now a tab inside AI Reports)
PAGE_SMART_REPORTING = 5   # was 6
PAGE_GEMINI_CHATS = 6      # NEW
PAGE_SETTINGS = 7
PAGE_SOURCE_MONITOR = 8
PAGE_GURU = 9
```

**Regression gate 4:** Gemini Chats page renders. Can type and send a message (if Gemini CLI is configured). Chat history persists to DB. Sidebar shows new item. All other pages still work. Run `test_qt_regression.py`.

---

## Phase 5: Smart Reports Update (45 min)

### 5.1 `src/ui/pages/smart_reporting.py` — Update layout

**Screenshot reference:** `Updated_Smart_Reports.png`

Changes from screenshot:
- **Report pipeline cards** at top: "VOC Root Cause" (Enabled), "Monthly Billing" (Disabled), "Eng Bug Tracker" (Disabled) — each card shows description, last run, schedule, and action buttons
- **Pipeline configuration panel** below cards: shows selected pipeline config (prompt, lookback days, budget, workers, toggles)
- **Scheduled runs** section with table: Pipeline, Started, Frequency, Next Run, Actions
- **Run history** table at bottom: Pipeline, Date, Tickets, TRC Categories, Duration, Cost, Status, Actions
- **CLI usage** bar at very bottom

**Implementation approach:**
1. Add `_build_pipeline_cards()` method — reads from `report_definitions` table
2. Refactor config panel to load from selected pipeline card
3. Add schedule management section
4. Keep existing run history table, add pipeline filter

### 5.2 `src/data/report_definitions/voc_root_cause.json` (~60 LOC)

Default VOC config matching current hardcoded behavior — the `_default_voc_config()` from the build plan.

### 5.3 VOCBuilder parameterization (6 surgical injection points)

**File modified:** `src/data/voc_builder.py` (or wherever VOCBuilder lives)

| Injection Point | Change | CC Impact |
|----------------|--------|-----------|
| `run()` signature | Accept optional `report_definition` param | +1 |
| Phase 0 planning | Use `config.data_scope` for TRC filtering | +2 |
| Phase 1 prompt loading | Use `config.phase1.batch_prompt` | +1 |
| Phase 2a accumulator | Check `config.accumulator.enabled` | +1 |
| Phase 2b specialists | Iterate `config.specialists` list | +1 |
| Phase 3 convergence | Use `config.convergence.prompt` | +1 |

**Test:** `tests/unit/test_voc_parameterization.py` — Verify `_default_voc_config()` produces a valid config. Verify config loading from JSON file.

**Regression gate 5:** Smart Reports page renders with pipeline cards. Existing VOC pipeline still runs with default config. Run `test_qt_regression.py` + `test_voc_builder.py`.

---

## Phase 6: alma_query CLI Tool (45 min)

### 6.1 `src/tools/alma_query.py` (~280 LOC)

Argparse-based CLI that Gemini invokes via `run_shell_command`.

**Subcommands (each is a function, CC < 8):**

| Subcommand | Function | Est. LOC | CC |
|-----------|----------|----------|---|
| `tickets` | `cmd_tickets(args, conn)` | 40 | 5 |
| `ticket-detail` | `cmd_ticket_detail(args, conn)` | 35 | 6 |
| `search` | `cmd_search(args, conn)` | 30 | 4 |
| `trends` | `cmd_trends(args, conn)` | 35 | 5 |
| `anomalies` | `cmd_anomalies(args, conn)` | 25 | 3 |
| `compare` | `cmd_compare(args, conn)` | 30 | 4 |
| `insights` | `cmd_insights(args, conn)` | 25 | 3 |
| `past-analysis` | `cmd_past_analysis(args, conn)` | 25 | 3 |
| `validate` | `cmd_validate(args, conn)` | 35 | 7 |
| `main()` | Argparse setup + dispatch | 40 | 2 |

All subcommands use parameterized SQL queries (no f-strings with user input).

### 6.2 `tests/unit/test_alma_query.py` (~200 LOC)

In-memory SQLite, seed test data, verify each subcommand returns correct JSON.

**Regression gate 6:** `python -m src.tools.alma_query tickets --limit 5` returns valid JSON (empty array if no data). All unit tests pass.

---

## Phase 7: New Widgets & Evidence Panel (45 min)

### 7.1 `src/ui/widgets/evidence_panel.py` (~200 LOC)

QWidget with QStackedWidget showing:
- Empty state: "Select a finding or ask a question to see supporting evidence"
- Ticket view: TicketPreviewCard + thread messages
- Finding view: title, severity badge, trend sparkline, related tickets
- Metadata view: pipeline info, specialist count, cost, dates

Connected via Qt signals — no independent data fetching.

### 7.2 `src/ui/widgets/report_section_renderer.py` (~180 LOC)

Parses markdown report output into structured QWidget section cards:
- Section header with severity badge
- Bullet point findings with clickable ticket IDs (regex `#\d{4,6}`)
- Action buttons per finding: "Investigate", "Add to Ledger"

### 7.3 `src/ui/widgets/trend_sparkline.py` (~80 LOC)

Custom QWidget with `paintEvent` that draws a mini line chart.
- Input: list of `(date, value)` tuples
- Draws lines with QPainter, highlights current trend direction

### 7.4 `src/ui/widgets/ticket_preview_card.py` (~100 LOC)

Compact QFrame showing:
- Ticket ID (clickable), severity badge
- Issue snippet (1-2 lines)
- Key metadata: TRC, friction type, sentiment

### 7.5 README files for new widgets

`src/ui/widgets/README.md` — document all widgets (update existing if present)

**Regression gate 7:** All new widgets can be instantiated without errors. Evidence panel renders empty state. Run `test_qt_regression.py`.

---

## Phase 8: Integration Testing & Final Regression (30 min)

### 8.1 Full regression test suite

Run in order:
1. `tests/test_qt_regression.py` — All pages render without exceptions
2. `tests/test_feature_integration.py` — Feature flows work
3. All new unit tests from Phases 1-7
4. Manual smoke test: navigate all pages, verify no crashes

### 8.2 `tests/test_build11_regression.py` (~150 LOC)

New test file that validates all Build 11.0 changes:

| Test | Validates |
|------|-----------|
| `test_migration_005_tables_exist` | All 7 new tables created |
| `test_ticket_index_upsert` | Dedup gate + upsert logic |
| `test_clear_session_preserves_persistent` | Clear & Close behavior |
| `test_ai_reports_has_tabs` | QTabWidget with 4 tabs |
| `test_gemini_chats_page_exists` | New page in MainWindow |
| `test_smart_reports_pipeline_cards` | Pipeline card rendering |
| `test_alma_query_subcommands` | CLI tool returns valid JSON |
| `test_evidence_panel_signals` | Signal/slot wiring |

### 8.3 Update `feature-manifest.json`

Add entries for new features:
- Gemini Chats (new page)
- Analysis Canvas (AI Reports redesign)
- Persistence Layer (ticket_index, insight_ledger)

---

## File Inventory Summary

### New Files (35 files)

| # | File | Phase | LOC |
|---|------|-------|-----|
| 1 | `migrations/005_persistence_layer.sql` | 0 | 120 |
| 2 | `src/services/__init__.py` | 0 | 1 |
| 3 | `src/services/README.md` | 0 | 5 |
| 4 | `src/tools/__init__.py` | 0 | 1 |
| 5 | `src/tools/README.md` | 0 | 5 |
| 6 | `src/data/report_definitions/README.md` | 0 | 5 |
| 7 | `src/services/ticket_index_writer.py` | 1 | 120 |
| 8 | `src/services/clear_session.py` | 1 | 60 |
| 9 | `src/services/post_scan_persist.py` | 1 | 130 |
| 10 | `src/services/post_report_persist.py` | 1 | 50 |
| 11 | `src/services/chat_session.py` | 1 | 80 |
| 12 | `tests/unit/test_ticket_index_writer.py` | 1 | 80 |
| 13 | `tests/unit/test_clear_session.py` | 1 | 60 |
| 14 | `tests/unit/test_post_scan_persist.py` | 1 | 80 |
| 15 | `tests/unit/test_post_report_persist.py` | 1 | 40 |
| 16 | `tests/unit/test_chat_session.py` | 1 | 60 |
| 17 | `src/ui/pages/ai_reports_canvas_tab.py` | 3 | 280 |
| 18 | `src/ui/pages/ai_reports_history_tab.py` | 3 | 180 |
| 19 | `src/ui/pages/ai_reports_prompts_tab.py` | 3 | 250 |
| 20 | `src/ui/pages/gemini_chats_page.py` | 4 | 250 |
| 21 | `src/services/context_injector.py` | 4 | 100 |
| 22 | `tests/unit/test_context_injector.py` | 4 | 60 |
| 23 | `src/data/report_definitions/voc_root_cause.json` | 5 | 60 |
| 24 | `tests/unit/test_voc_parameterization.py` | 5 | 80 |
| 25 | `src/tools/alma_query.py` | 6 | 280 |
| 26 | `tests/unit/test_alma_query.py` | 6 | 200 |
| 27 | `src/ui/widgets/evidence_panel.py` | 7 | 200 |
| 28 | `src/ui/widgets/report_section_renderer.py` | 7 | 180 |
| 29 | `src/ui/widgets/trend_sparkline.py` | 7 | 80 |
| 30 | `src/ui/widgets/ticket_preview_card.py` | 7 | 100 |
| 31 | `src/ui/widgets/README.md` | 7 | 20 |
| 32 | `tests/test_build11_regression.py` | 8 | 150 |
| 33 | `src/ui/pages/README.md` | 0 | 10 |
| 34 | `src/services/README.md` (already listed) | — | — |
| 35 | `src/data/README.md` | 0 | 10 |

### Modified Files (12 files)

| # | File | Phase | Changes |
|---|------|-------|---------|
| 1 | `src/data/db_manager.py` | 0 | Run migration 005 |
| 2 | `src/agents/worker_agent.py` | 2 | Dedup gate hook |
| 3 | `src/agents/stream_parser.py` | 2 | ticket_index upsert hook |
| 4 | `src/agents/scan_orchestrator.py` | 2 | Post-scan persistence hook |
| 5 | `src/ui/pages/ai_reports.py` | 3 | Refactor to QTabWidget container |
| 6 | `src/ui/pages/ab_compare.py` | 3 | Extract reusable widget |
| 7 | `src/ui/main_window.py` | 3,4 | Remove A/B sidebar, add Gemini Chats, update page constants |
| 8 | `src/ui/pages/smart_reporting.py` | 5 | Pipeline cards, schedule UI |
| 9 | `src/data/voc_builder.py` | 5 | Accept report_definition config |
| 10 | `src/ui/widgets/chat_widget.py` | 4 | Add context injection support |
| 11 | `feature-manifest.json` | 8 | Add new feature entries |
| 12 | `src/ui/theme.py` | 3 | Add tab bar styles if needed |

---

## Execution Sequence & Time Budget

| Phase | Description | Time | Cumulative | Gate |
|-------|-------------|------|------------|------|
| 0 | Scaffolding + Migration | 30m | 0:30 | App launches, tables exist |
| 1 | Persistence Services + Tests | 60m | 1:30 | 5 unit test files pass |
| 2 | Pipeline Integration Hooks | 45m | 2:15 | App launches, existing features work |
| 3 | AI Reports Tab Redesign | 60m | 3:15 | 4 tabs render, reports generate |
| 4 | Gemini Chats Page | 45m | 4:00 | New page in sidebar, chat works |
| 5 | Smart Reports Update | 45m | 4:45 | Pipeline cards render |
| 6 | alma_query CLI Tool | 45m | 5:30 | CLI returns valid JSON |
| 7 | New Widgets | 45m | 6:15 | All widgets instantiate cleanly |
| 8 | Integration + Regression | 30m | 6:45 | Full test suite green |

**Total estimated: ~7 hours**

---

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| `ai_reports.py` refactor breaks existing report generation | Extract current logic into `AnalysisCanvasTab` as-is first, then restructure. Keep all worker classes and signal connections intact. |
| Page index shift breaks sidebar navigation | Update ALL page constant references in one atomic change. Grep for `PAGE_AB_COMPARE` before removing. |
| Migration fails on existing DB | Test migration on a copy of production DB before running on real data. |
| VOC parameterization changes output | Run `_default_voc_config()` path first, diff output vs hardcoded path before enabling custom configs. |
| QTabWidget styling doesn't match Alma theme | Pre-build QSS for tab bar matching the green/cream palette from `theme.py`. |

---

## UI Screenshot → Implementation Mapping

| Screenshot | Implementation Target | Key Elements |
|-----------|----------------------|-------------|
| `Stable_Alma_Insights layout.png` | Reference — current state, preserve this base | Sidebar, top bar, report output |
| `Updated_AI_Reports_Analysis_Canvas.png` | `ai_reports_canvas_tab.py` + `evidence_panel.py` | Split layout, evidence panel, report sections |
| `Updated_AI_Reports_Report_History_Page.png` | `ai_reports_history_tab.py` | Filter pills, search bar, table |
| `Updated_AI_Reports_Prompt_Builder_Page.png` | `ai_reports_prompts_tab.py` | Prompt list, chat builder, preview |
| `Updated_Smart_Reports.png` | `smart_reporting.py` modifications | Pipeline cards, schedule table, config panel |
| `New_Gemini_Chats.png` | `gemini_chats_page.py` | Chat bubbles, session chips, data-grounded Q&A |

---

## Quality Gates (Applied Throughout)

1. **Cyclomatic complexity:** Every new function CC < 15 (target < 10 for most)
2. **File size:** Function files < 300 LOC. UI widget files kept as small as practical.
3. **Unit tests:** Every new `src/services/` and `src/tools/` file gets a corresponding test
4. **Regression:** `test_qt_regression.py` runs after each phase
5. **README.md:** Every new directory branch gets a description file
6. **No security regressions:** All SQL uses parameterized queries. PII redaction unchanged.
7. **Backward compatibility:** Default VOC config must produce identical output to current hardcoded pipeline
