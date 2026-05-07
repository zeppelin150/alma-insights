# CLAUDE.md — Alma Insights

## What This Project Is

Alma Insights is a PySide6 desktop application for analyzing healthcare RCM (Revenue Cycle Management) support tickets. It ingests ticket data from CSV exports or Lightdash API, runs LLM-powered NLP classification via an agentic pipeline, performs statistical trend and anomaly detection, and generates AI-synthesized reports. Built for non-technical cross-company users at Alma Health.

## Quick Reference

| Item | Value |
|------|-------|
| Entry point | `python main.py` |
| Python | 3.10+ |
| UI framework | PySide6 |
| Database | SQLite (WAL mode) at `data/local_warehouse.db` |
| Settings | `data/settings.yaml` (migrated from `config/` on first launch) |
| Run tests | `python -m pytest tests/test_X.py tests/test_Y.py -x -v` |
| Memory profiler | `python main.py --profile` |
| Kill zombies | `wmic process where "commandline like '%alma_mcp_server%'" call terminate` |

**Testing notes:**
- Run tests in groups of 3-4 files; full `tests/` hangs on Windows
- Fixtures: `empty_db` (zero rows), `seeded_db` (100 tickets, 3 TRCs)
- Markers: `@slow`, `@e2e`, `@ui`, `@live_db`
- Known: `test_reporting_foundation::test_bridge_fallback` pre-existing failure
- Known: `test_feature_integration` has live-DB-dependent failures (expected)

## AI Reports — Structured-output rebuild (R1–R5, 2026-05-06)

The Analysis Canvas, Evidence Panel, and Prompt Builder were rebuilt
2026-05-06 against the 3.24.26 reference screenshots. The pipeline now
emits a JSON-fenced `AlmaReport` (see [`src/data/report_schema.py`](src/data/report_schema.py))
which the canvas renders as `FindingCard` widgets bound to the evidence
panel via `finding_clicked` + `metadata_changed` signals. Multi-bridge
specialist + convergence dispatch is the **universal default**
(`ai.report_pipeline.kind=multi_bridge`); `single_pass` falls back to a
single Gemini call for legacy callers.

Settings (`data/settings.yaml > ai.report_pipeline`):
- `kind`               — `multi_bridge` (default) | `single_pass`
- `bridges`            — pool size (default `4`)
- `model`              — empty → uses `ai.active_model`
- `convergence_model`  — empty → reuses `model`

Grounding harness ([`src/data/report_grounding.py`](src/data/report_grounding.py))
audits every report along count / entity / time fidelity dimensions;
below 0.75 the report gains a `low_accuracy` flag (warn-only, never
blocks per user spec). Migration `migrations/026_report_findings.sql`
extends `analysis_reports` with `findings_json`, `pipeline_kind`,
`specialist_count`, `accuracy_score`, `cost_usd`.

Full architecture: [`docs/AI_REPORTS.md`](docs/AI_REPORTS.md). Drift
audit checklist: `~/.claude/projects/C--alma-insights/memory/reports_drift_audit.md`.

## Bug Bash Protocol (MANDATORY)

When the user reports a bug, or asks you to fix/diagnose/investigate a defect, you MUST follow these four gates **in order**. Do not skip. Do not combine. Do not propose a fix before Gate 2 is complete.

### Gate 1 — Investigate

Before touching any code:

1. Open a TodoWrite list with one entry per gate (Investigate / Repro / Fix / Verify), plus sub-items for investigation steps.
2. Restate the bug in one sentence in your own words — if you can't, ask the user a clarifying question before proceeding.
3. **Targeted reads only.** Start from the symptom (error message, stack trace, page name, function name) and use `Grep`/`Glob` to locate the relevant code. Read only the files the evidence points to. Do NOT bulk-read directories speculatively.
4. If the trail needs more than 3 searches/reads to narrow down, stop and spawn the `Explore` subagent instead. Protect the main context window.
5. Produce a written **Investigation Summary** with:
   - Suspected root cause (one sentence)
   - Specific file:line references supporting the hypothesis (use markdown links like `[db_manager.py:204](src/data/db_manager.py:204)`)
   - Any assumptions that still need verification

**Do not proceed to Gate 2 until the Investigation Summary is posted to the user.**

### Gate 2 — Plan + Build a Repro

Before writing the fix:

1. Describe the test plan in ≤ 5 lines: what input, what expected output, what file the test lives in.
2. Write a **failing test** that reproduces the bug. Prefer pytest in `tests/`; match the existing test file's style and fixtures (`empty_db`, `seeded_db`).
3. Run the test and confirm it fails for the expected reason (not an import error, not a fixture error). Quote the relevant failure line in your response.
4. **Exception:** If a repro is genuinely impractical (e.g. Qt paint glitch, race condition requiring live bridge, third-party UI bug), you must explicitly state *why* a failing test can't be written and get user acknowledgment before proceeding. Do not silently skip this gate.

**Do not proceed to Gate 3 until there is a failing test (or an acknowledged exception).**

### Gate 3 — Fix

1. Make the smallest change that turns the failing test green. No drive-by refactors, no unrelated cleanups, no "while I'm here" improvements (see existing guidance on scope discipline).
2. Respect project patterns: `get_connection()` for DB, `settings_manager` for config, `build_client_for_task()` for LLM, signals/slots for cross-thread Qt.
3. If the fix requires changes in more than 2 files, pause and explain why before editing.

### Gate 4 — Verify

1. Re-run the failing test — it must now pass.
2. Run the **nearest related test file(s)** in groups of 3–4 (never the full `tests/`). Report the pass/fail counts.
3. For UI-observable changes, use the `preview_*` tools to verify in the running app.
4. Kill zombies before any E2E run: `wmic process where "commandline like '%alma_mcp_server%'" call terminate`.
5. Update the TodoWrite list — mark each gate completed as it finishes, one at a time.
6. Final response to the user must include: (a) root cause in one sentence, (b) files changed with links, (c) test results, (d) any remaining risks.

### Gate violations

If you catch yourself about to propose a fix without a repro, or about to bulk-read files without a symptom trail, stop and restart at Gate 1. The user has explicitly authorized this protocol — treat skipping a gate as a bug in your own behavior.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  UI Layer (PySide6)                                             │
│  src/ui/  ─  75 files, ~40K LOC                                │
│  main_window → 10 pages + 40+ widgets + 7 dialogs              │
├─────────────────────────────────────────────────────────────────┤
│  Services Layer                                                 │
│  src/services/  ─  8 files                                      │
│  chat_engine, chat_session, post_scan_persist, context_injector │
├───────────────────────────┬─────────────────────────────────────┤
│  Data Layer               │  Agent Layer                        │
│  src/data/  ─ 85 files    │  src/agents/  ─ 14 files, ~9.7K LOC│
│  ~29K LOC                 │  ScanOrchestrator → N WorkerAgents  │
│  DB, analysis engines,    │  ACPBridge (subprocess)  │
│  integrations, reports    │  Supervisor, RateGovernor, BatchPack│
├───────────────────────────┼─────────────────────────────────────┤
│  LLM Layer                │  MCP Layer                          │
│  src/gemini/ (4 files)    │  src/mcp/ (4 files)                 │
│  src/llm/ (4 files)       │  alma_mcp_server, chat_mcp_server   │
│  client_factory (routing) │  stdio JSON-RPC for tool-use        │
├───────────────────────────┴─────────────────────────────────────┤
│  Infrastructure                                                 │
│  src/updater/ (4 files)  ─  auto-update, schema migration       │
│  scan_server/ (Node.js)  ─  persistent Gemini bridge            │
│  installer/              ─  cross-platform build & install       │
│  migrations/             ─  15 SQL migration files               │
└─────────────────────────────────────────────────────────────────┘
```

## Source Layout

| Directory | Files | LOC | Purpose | Key Files |
|-----------|-------|-----|---------|-----------|
| `src/agents/` | 14 | 9.7K | Scan orchestration, workers, bridge, rate limiting | `scan_orchestrator.py`, `worker_agent.py`, `acp_bridge.py` |
| `src/data/` | 85 | 29K | DB, analysis engines, integrations, reports | `db_manager.py`, `voc_builder.py`, `trending_engine.py` |
| `src/gemini/` | 4 | 700 | Gemini CLI wrapper, prompt assembly, client factory | `client_factory.py`, `gemini_client.py` |
| `src/llm/` | 4 | 980 | Claude API client, tool definitions, model registry | `claude_client.py`, `claude_tools.py`, `model_registry.py` |
| `src/mcp/` | 4 | 700 | MCP servers for classification and chat tools | `alma_mcp_server.py`, `chat_mcp_server.py` |
| `src/services/` | 8 | 1.5K | Chat engine, persistence hooks, session mgmt | `chat_engine.py`, `post_scan_persist.py` |
| `src/ui/pages/` | 16 | 19K | Application pages (analytics, reports, settings) | `trc_analytics.py`, `settings_page.py`, `ai_reports.py` |
| `src/ui/widgets/` | 46 | 16K | Reusable widgets (charts, tables, panels) | `charts.py`, `drilldown_panel.py`, `chat_widget.py` |
| `src/ui/dialogs/` | 8 | 2K | Modal dialogs (ingestion, mapping, help) | `ingestion_dialog.py` |
| `src/updater/` | 4 | 630 | Auto-update, schema migration | `updater.py`, `schema_migrator.py` |
| `src/export/` | 2 | 116 | Google Drive export | `gdrive_export.py` |
| `config/prompts/` | 21 | — | LLM prompt templates (txt) | `nlp_classify.txt`, `voc_analysis.txt` |
| `config/entities/` | 3 | — | Entity dictionaries (JSON) | `payers.json`, `product_areas.json` |
| `tests/` | 100 | 38.6K | Unit, integration, E2E, benchmarks | `conftest.py`, `test_pipeline_full.py` |
| `migrations/` | 15 | — | SQL schema migrations | `001_initial_baseline.sql` → `015_batch_tickets.sql` |

**Module-level docs** exist in INDEX.md files inside each `src/` subdirectory. These contain public API signatures, dependencies, and dependents for every module. Read them for detailed API reference:
- `src/agents/INDEX.md`
- `src/data/INDEX.md`
- `src/gemini/INDEX.md`
- `src/llm/INDEX.md`
- `src/ui/INDEX.md`
- `src/updater/INDEX.md`
- `src/export/INDEX.md`

## Key Patterns & Conventions

### Connection Discipline
ALL database access MUST go through `src/data/connection_factory.get_connection()`. Direct `sqlite3.connect()` is banned. The factory guarantees WAL mode, `busy_timeout=30s`, FK enforcement, and `Row` factory.

```python
from src.data.connection_factory import get_connection, atomic

conn = get_connection(db_path)           # standard connection
conn = get_connection(db_path, readonly=True)  # read-only

with atomic(conn):                       # transaction with auto-rollback
    conn.execute("INSERT INTO ...")
```

### Settings Access
Always use `src/data/settings_manager`. Never read/write `settings.yaml` directly.

```python
from src.data.settings_manager import load_settings, save_settings
from src.data.settings_manager import get_section, set_section

cfg = load_settings()                    # full dict
gemini = get_section("gemini", {})       # single section
set_section("gemini", {"model": "..."})  # write single section
```

### LLM Task Routing
Use `build_client_for_task()` from `src/gemini/client_factory`. Routes PHI tasks to Gemini, ops tasks to Claude, based on `ai.task_routing` in settings.

```python
from src.gemini.client_factory import build_client_for_task

client = build_client_for_task("nlp_classification")     # → Gemini
client = build_client_for_task("guru_analysis")          # → Claude
client = build_client_for_task("report_generation", use_bridge=True)  # → ACPBridge
```

Default routing:
| Task | Provider |
|------|----------|
| `nlp_classification`, `voc_analysis`, `report_generation`, `ab_comparison` | Gemini |
| `guru_analysis`, `guru_content_generation`, `watchlist_triage`, `meta_analytics` | Claude |

### UI Threading
Never touch widgets from worker threads. Use signals/slots:
```python
# WRONG: self.label.setText("done")  from a QThread
# RIGHT: emit a signal that the main thread connects to
```

For one-off cross-thread calls use `QMetaObject.invokeMethod()`.

### PII Redaction
Mandatory on ALL LLM calls. Both `GeminiClient` and `ClaudeClient` apply PII redaction by default. Redaction patterns live in `config/redaction_patterns.json`.

### Error Handling
`src/ui/qt_error_guard.py` catches silent Qt paint/delegate exceptions and surfaces them in the status bar. Wired in `main.py`.

## Database Essentials

- **Path**: `data/local_warehouse.db`
- **Schema**: 42+ tables defined in `db_manager.py::initialize()` + 15 migration files
- **Connection config**: WAL, synchronous=FULL, busy_timeout=30s, FK=ON, row_factory=Row

### Table Categories

| Category | Key Tables | Lifecycle |
|----------|-----------|-----------|
| Core ticket data | `tickets`, `comments`, `conversations`, `conversations_fts` | Ephemeral (cleared on import) |
| Per-source dynamic | `{prefix}_tickets`, `{prefix}_conversations`, `{prefix}_fts` | Per data source |
| NLP classification | `nlp_scan_runs`, `nlp_batches`, `nlp_ticket_classifications`, `sub_patterns` | Persist across imports |
| Persistence layer | `ticket_index`, `analysis_runs`, `trend_snapshots`, `chat_sessions` | Survive Clear & Close |
| Anomaly detection | `daily_counts`, `trc_baselines`, `incident_flags`, `anomaly_flags` | Recomputed per scan |
| Agent infrastructure | `agent_health`, `scan_progress`, `gemini_usage`, `cost_limits` | Session-scoped |
| Guru KB | `guru_cards`, `guru_card_graph`, `guru_card_domains`, `guru_friction_scores` | Persist |
| Import tracking | `import_runs`, `import_sources` | Persist |

### Ephemeral vs Persistent
- **Ephemeral** (deleted on fresh import): `tickets`, `comments`, `conversations`
- **Persistent** (survive across imports): `ticket_index`, `sub_patterns`, `nlp_findings`, `chat_sessions`, `analysis_reports`

Full schema reference: `docs/DATABASE.md`

## LLM Integration

### Dual-Provider Architecture
- **Gemini** (primary): Used for PHI-containing tasks (classification, VOC, reports). Accessed via CLI subprocess (`GeminiClient`) or persistent Node.js bridge (`ACPBridge` / `ReportBridgeClient`).
- **Claude** (secondary): Used for ops tasks (Guru analysis, watchlist, meta-analytics). Accessed via `ClaudeClient` using stdlib `urllib` (no SDK dependency).

### ACPBridge Protocol
The bridge (`scan_server/server.js`) is a persistent Node.js subprocess communicating via JSON-line protocol over stdin/stdout. Event types: `token`, `done`, `error`, `stopped`, `pong`, `probe_result`.

### Model Registry
Singleton at `src/llm/model_registry.ModelRegistry.instance()`. 5 built-in models (Gemini + Claude). Persisted to `settings.yaml`. Emits `model_changed` signal.

### API Key Storage
`src/data/pat_store.py` provides encrypted key storage. Keys: `gemini_api_key`, `anthropic_api_key`.

## The Agentic Pipeline (NLP Classification)

```
ScanOrchestrator
  ├── BatchPacker (dual-constraint sizing with EMA learning)
  ├── RateGovernor (token bucket + adaptive backoff)
  ├── Worker 1 ──┐
  ├── Worker 2 ──┤── each has: ACPBridge + ToolRegistry + StreamParser
  ├── Worker N ──┘
  ├── Supervisor (health monitor, progress, time estimates)
  └── AnalystAgent (post-scan: synthesis, audit, novelty, merge)
```

**Flow**: `start_scan()` → partition tickets into batches → assign to workers → workers classify via bridge → StreamParser extracts fenced code blocks → ToolRegistry persists results → Supervisor monitors → AnalystAgent runs post-scan phases.

**Stream protocol**: LLM output contains fenced code blocks:
- `` ```tool_call `` — tool invocation (JSON)
- `` ```classification `` — ticket classification (JSON)
- `` ```batch_complete `` — batch completion signal

**Post-scan chain**: `ticket_index_writer` → `entity_normalizer` → `enriched_trends` → `ticket_theme_tagger`

**Classification output**: NDJSON text (primary). MCP tools are disabled. See `mcp-pivot-ndjson.md` plan.

## Data Import Flow

Two import paths, both **additive** (dedup by ticket_id — only new tickets are inserted):

1. **CSV**: File → `CSVReformatter` (optional Gemini column mapping) → `csv_ingestion.ingest_csv()` → tickets/conversations tables
2. **Lightdash API**: URL → `lightdash_client.run_ingestion()` → chunked pull → `conversation_rebuild` → tickets/conversations

Dedup mechanism: `import_tracker.get_existing_ticket_ids()` filters already-imported tickets before insertion. Import audit logged to `import_runs` table.

Post-import: FTS index rebuild, conversation threading, entity extraction.

See `docs/DATA_FLOWS.md` for detailed flow documentation.

## Known Gotchas & Danger Zones

1. **Import dedup**: Imports are now additive (dedup by ticket_id). The old destructive DELETE-before-INSERT was removed. `import_tracker.get_existing_ticket_ids()` prevents duplicates.

2. **Memory**: `trending_engine.py` holds 3-4 copies of the full dataset in memory during its 12-step pipeline. Large datasets can cause OOM.

3. **db_manager.initialize()**: 650+ line `executescript()`. Fragile — a syntax error in any CREATE statement silently fails all subsequent statements.

4. **Windows __pycache__**: Cleared on every launch (`main.py:13-18`). Normal behavior, not a bug.

5. **PySide6 threading**: Qt widgets MUST only be modified from the main thread. Worker threads must emit signals. Violating this causes intermittent crashes.

6. **Bridge zombie processes**: Gemini CLI / MCP server subprocesses can accumulate. Kill before E2E tests: `wmic process where "commandline like '%alma_mcp_server%'" call terminate`

7. **atomic() cannot nest**: `connection_factory.atomic()` raises `RuntimeError` if called inside an existing transaction. Refactor to a single `atomic()` block.

8. **Settings migration**: On first launch, `settings.yaml` migrates from `config/` to `data/`. The original is renamed to `.yaml.migrated`. Auto-updates replace `config/` wholesale, so user settings must live in `data/`.

## UI Pages

| Page | File | Purpose |
|------|------|---------|
| Conversation Search | `conversation_search.py` | Search/filter tickets, CSV import |
| TRC Analytics | `trc_analytics.py` | TRC dashboard, scan trigger |
| Trending Topics | `trending_topics.py` | Topic trends, forecasting |
| Incidents | `incidents_page.py` | Critical incident flags |
| AI Reports | `ai_reports.py` | Report generation + history |
| A/B Compare | `ab_compare.py` | Dataset comparison |
| Smart Reporting | `smart_reporting.py` | Automated adaptive reports |
| Guru KB | `guru_page.py` | Guru knowledge base integration |
| Source Monitor | `source_monitor_page.py` | Data source dashboard |
| Settings | `settings_page.py` | App configuration (4 tabs) |
| Gemini Chat | `gemini_chats_page.py` | Interactive AI chat |
| Data Warehouse | `data_warehouse_page.py` | Custom SQL queries |

## Prompt Templates

21 templates in `config/prompts/`. Key ones:

| Template | Purpose |
|----------|---------|
| `nlp_classify.txt` | Ticket classification prompt for workers |
| `nlp_synthesize.txt` | Post-scan NLP synthesis |
| `voc_analysis.txt` | Single-TRC VOC analysis |
| `voc_analysis_batch.txt` | Batched multi-TRC VOC |
| `voc_convergence.txt` | Cross-TRC convergence |
| `executive_summary.txt` | Executive report prompt |
| `ab_comparison.txt` | A/B dataset comparison |
| `incident_summary.txt` | Incident analysis |
| `hypothesis.txt` | Hypothesis testing |
| `synthesis.txt` | Gemini synthesis prompt |

## Documentation Index

### Detailed Docs
- `docs/ARCHITECTURE.md` — System architecture (layers, components, data lifecycle)
- `docs/DATABASE.md` — Complete SQLite schema reference
- `docs/DATA_FLOWS.md` — Data flow documentation (import → scan → analysis → report)
- `docs/AGENTS.md` — Agentic pipeline deep dive
- `docs/LLM_INTEGRATION.md` — LLM provider integration guide
- `docs/UI_GUIDE.md` — UI development guide
- `docs/CONFIGURATION.md` — Configuration reference
- `docs/CONTRIBUTING.md` — Developer setup and conventions
- `docs/DEPLOYMENT.md` — Build, install, and update
- `docs/TROUBLESHOOTING.md` — Common issues and diagnostics

### Existing Reference Docs
- `docs/ARCHITECTURE_KERNEL.md` — Data scaling analysis (500K row target)
- `docs/NLP_PIPELINE_4_1_REFERENCE.md` — Historical pipeline reference
- `DESTRUCTIVE_IMPORT_KERNEL.md` — Import architecture and migration plan
- `BUILD_SPEC_TRC_AND_TRENDING.md` — Trending engine spec with formulas

### Module-Level API Reference
Each `src/` subdirectory has an `INDEX.md` with public API signatures, dependencies, and dependents:
- `src/agents/INDEX.md` — 14 agent modules
- `src/data/INDEX.md` — 85 data layer modules
- `src/gemini/INDEX.md` — 4 Gemini modules
- `src/llm/INDEX.md` — 4 Claude/LLM modules
- `src/ui/INDEX.md` — UI shell modules
- `src/updater/INDEX.md` — 4 updater modules
- `src/export/INDEX.md` — 2 export modules
