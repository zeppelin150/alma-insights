# Alma Insights — System Architecture

## System Overview

Alma Insights is a desktop analytics application for healthcare Revenue Cycle Management (RCM) support ticket analysis. It combines statistical analysis with LLM-powered NLP classification to surface trends, anomalies, and friction patterns in customer support data.

**Technology stack:**
- **Language**: Python 3.10+
- **UI**: PySide6 (Qt 6)
- **Database**: SQLite with WAL mode
- **LLMs**: Google Gemini (via CLI subprocess) + Anthropic Claude (via urllib)
- **Analysis**: scikit-learn, scipy, NLTK, VADER, sentence-transformers
- **Bridge**: Node.js persistent subprocess for Gemini streaming

**Design philosophy:**
- Local-first: all data stays on the user's machine
- PHI-aware: mandatory PII/PHI redaction before any LLM call
- Single-process: no external servers (except Gemini bridge subprocess)
- HIPAA constraint: Gemini accessed via CLI only (BAA covers CLI, not SDK/REST)

---

## Layer Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         LAYER 1: UI                                     │
│  MainWindow → 12 Pages + 46 Widgets + 8 Dialogs                        │
│  Theme system, layman mode, Qt error guard                              │
│  src/ui/  (75 files, ~40K LOC)                                          │
├──────────────────────────────────────────────────────────────────────────┤
│                         LAYER 2: SERVICES                               │
│  ChatEngine, ChatSession, PostScanPersist, TicketIndexWriter            │
│  Context injection, session management, persistence hooks               │
│  src/services/  (8 files, ~1.5K LOC)                                    │
├───────────────────────────────┬──────────────────────────────────────────┤
│  LAYER 3: DATA                │  LAYER 4: AGENTS                        │
│  DatabaseManager (42+ tables) │  ScanOrchestrator                       │
│  Analysis engines:            │    ├── WorkerAgent × N                  │
│    trending, VOC, incidents,  │    ├── ACPBridge (subprocess)           │
│    theta, watchlist           │    ├── Supervisor                       │
│  Integrations:                │    ├── RateGovernor                     │
│    Guru, Zendesk, Lightdash   │    ├── BatchPacker                     │
│  Reports, ingestion, entities │    └── AnalystAgent                    │
│  src/data/  (85 files, ~29K)  │  src/agents/  (14 files, ~9.7K LOC)    │
├───────────────────────────────┼──────────────────────────────────────────┤
│  LAYER 5: LLM                 │  LAYER 6: MCP                          │
│  ClientFactory (task routing) │  alma_mcp_server (classification tools) │
│  GeminiClient (CLI wrapper)   │  chat_mcp_server (chat tools)           │
│  ClaudeClient (urllib)        │  stdio JSON-RPC protocol                │
│  ModelRegistry (singleton)    │  src/mcp/  (4 files, ~700 LOC)          │
│  src/gemini/ + src/llm/       │                                         │
│  (8 files, ~1.7K LOC)         │                                         │
├───────────────────────────────┴──────────────────────────────────────────┤
│                     LAYER 7: INFRASTRUCTURE                             │
│  src/updater/  — auto-update (GitHub releases, SHA-256, stage-and-apply)│
│  scan_server/  — Node.js Gemini bridge (server.js)                      │
│  installer/    — cross-platform build (build_release.py, install.py)    │
│  migrations/   — 15 SQL migration files (001–015)                       │
│  config/       — 21 prompt templates, entity dictionaries, redaction    │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Layer 1: UI (src/ui/)

### Application Shell

`MainWindow` (`src/ui/main_window.py`) is a `QMainWindow` with:
- **Top bar**: App title, page header, action buttons
- **Collapsible sidebar**: 12 navigation buttons with icons + labels
- **Content area**: `QStackedWidget` holding all pages
- **Job overlay**: Semi-transparent overlay showing async job progress
- **Drilldown panel**: Slide-in overlay drawer for ticket detail views
- **Toast notifications**: Non-blocking status messages
- **Status bar**: Qt error guard alerts, connection status

### Page Inventory

| Idx | Page | File | Purpose |
|-----|------|------|---------|
| 0 | Conversation Search | `conversation_search.py` (960 LOC) | Search/filter tickets, CSV import trigger |
| 1 | TRC Analytics | `trc_analytics.py` (2,087 LOC) | TRC dashboard, NLP scan trigger, analyst reports |
| 2 | Trending Topics | `trending_topics.py` (2,389 LOC) | Topic trends, CUSUM drift, forecasting |
| 3 | Incidents | `incidents_page.py` (1,564 LOC) | Poisson spike detection, incident management |
| 4 | AI Reports | `ai_reports.py` (1,607 LOC) | Report generation with prompt editor + history |
| 5 | A/B Compare | `ab_compare.py` (663 LOC) | Dataset comparison (chi-squared, Mann-Whitney U) |
| 6 | Smart Reporting | `smart_reporting.py` (1,498 LOC) | Automated adaptive reports |
| 7 | Settings | `settings_page.py` (3,515 LOC) | 4-tab config: General, AI, Integrations, Updates |
| 8 | Source Monitor | `source_monitor_page.py` (1,508 LOC) | Multi-source data pipeline dashboard |
| 9 | Guru KB | `guru_page.py` (1,134 LOC) | Guru knowledge base friction analysis |
| 10 | Gemini Chat | `gemini_chats_page.py` (777 LOC) | Interactive AI chat with tool-use |
| 11 | Data Warehouse | `data_warehouse_page.py` (610 LOC) | Custom SQL queries against warehouse |

### Widget Library

46 widgets in `src/ui/widgets/`. Key categories:
- **Charts**: `charts.py` (matplotlib integration, 1,352 LOC)
- **Panels**: `drilldown_panel.py` (overlay drawer), `guru_workbench_panel.py` (KB editing)
- **Chat**: `chat_widget.py` (input/output), `chat_drilldown.py` (detail view)
- **Tables**: `paginated_table.py`, `sortable_table.py`
- **Controls**: `filter_bar.py`, `date_picker.py`, `term_manager_panel.py`
- **Feedback**: `toast.py`, `job_overlay.py`, `spinner.py`
- **Layout**: `collapsible_section.py`, `tab_scroll_content.py`

### Theme System

`src/ui/theme.py` defines the Alma brand palette as module-level constants (`ALMA_GREEN_DARK`, `ALMA_CREAM`, etc.), shadow helpers, table/tree configuration, and a single `get_stylesheet()` function returning the full QSS string. Applied once at startup in `main.py`.

### Layman Mode

`src/ui/layman_mode.py` translates technical metrics (VADER, CUSUM, z-score, TF-IDF, Poisson lambda) into plain-language equivalents when "Simplified Language Mode" is enabled in settings. Used by Incidents, Trending, and TRC Analytics pages.

### Qt Error Guard

`src/ui/qt_error_guard.py` installs a custom exception hook that catches silent Qt failures (paint/delegate exceptions, infinite recursion, memory spikes) and surfaces them in the status bar. Prevents hard-to-debug silent crashes.

---

## Layer 2: Services (src/services/)

### Chat Engine

`chat_engine.py` (382 LOC) is a pure-logic `QObject` powering all AI chat interactions. Not a widget — consumers own their UI.

Key features:
- **Build-per-message** (default): Fresh LLM client via `build_client_for_task()` each `send()`
- **Warm client** (opt-in): Consumer injects pre-built client via `set_client()`
- **Tool execution** (opt-in): Parses `TOOL_CALL:` patterns in responses, executes against local DB, resubmits results (max 3 round-trips)
- **Telemetry**: Captures estimated tokens, latency, model used per response

### Chat Session

`chat_session.py` (488 LOC) manages chat state: message history, session persistence to SQLite, context windowing, and session lifecycle (create, load, clear, delete).

### Persistence Hooks

- `post_scan_persist.py` (256 LOC): After NLP scan completes, syncs results to persistent tables (`ticket_index`, `enriched_trends`, `ticket_theme_tags`)
- `post_report_persist.py` (63 LOC): After report generation, saves to `analysis_reports`
- `ticket_index_writer.py` (166 LOC): Maintains the persistent `ticket_index` table from ephemeral classification data

### Context Injection

`context_injector.py` (117 LOC): Builds context strings for chat prompts by querying recent scan results, trending data, and ticket statistics.

---

## Layer 3: Data (src/data/)

The largest layer (85 files, ~29K LOC). Contains all business logic independent of the UI framework.

### Database (db_manager.py)

`DatabaseManager` is the primary database interface. Key responsibilities:
- Schema creation: 42+ tables via a single `executescript()` in `initialize()` (650+ lines)
- CRUD for tickets, comments, conversations
- FTS5 full-text search index
- Report persistence
- Usage tracking

Schema is extended by 15 migration files (`migrations/001_initial_baseline.sql` through `015_batch_tickets.sql`) applied by `SchemaMigrator`.

Dynamic per-source tables are created by `schema_builder.py`: `{prefix}_tickets`, `{prefix}_conversations`, `{prefix}_comments`, `{prefix}_fts`.

See `docs/DATABASE.md` for full schema reference.

### Analysis Engines

| Engine | File | LOC | Purpose |
|--------|------|-----|---------|
| Trending | `trending_engine.py` | 2,081 | 12-step pipeline: TF-IDF, chi-squared, lead-lag, CUSUM, z-score anomalies |
| VOC Builder | `voc_builder.py` | 2,285 | Multi-phase VOC analysis: accumulate → analyze → synthesize → converge |
| Incident | `incident_engine.py` | 660 | Two-tier Poisson SPC with CUSUM drift detection |
| Theta | `theta_engine.py` | 730 | Product gap/capability analysis via z-score |
| Watchlist | `watchlist_engine.py` | 815 | Custom pattern monitoring with alert thresholds |
| NLP Meta | `nlp_meta_analyzer.py` | 945 | Post-scan Layer 2 NLP synthesis across TRCs |
| NLP Synthesis | `nlp_synthesis.py` | — | Gemini-powered narrative synthesis from scan results |

### Integrations

| Integration | Files | Purpose |
|-------------|-------|---------|
| Guru KB | `guru_client.py`, `guru_friction_pipeline.py`, `guru_content_pipeline.py`, `guru_effectiveness.py`, `guru_graph_builder.py` | Read-only friction analysis (Loop A) + write-gated content generation (Loop B) |
| Zendesk | `zendesk_client.py`, `zendesk_monitor.py` | Zendesk API, polling monitor |
| Lightdash | `lightdash_client.py`, `lightdash_mock.py` | BI data ingestion with chunked pull |

### Data I/O

- `csv_ingestion.py` (715 LOC): CSV import with column mapping
- `conversation_rebuild.py`: Thread reconstruction from raw rows
- `source_warehouse.py` (397 LOC): Multi-source warehouse orchestration
- `import_tracker.py`: Import deduplication

### Reports

- `report_builder.py` (801 LOC): Markdown report formatting
- `scan_report_builder.py` (578 LOC): Compile scan results into structured reports
- `ai_report_pipeline.py`: Multi-phase AI report generation
- `ab_report_pipeline.py`: A/B comparison pipeline
- `smart_pipeline.py` (479 LOC): Automated scheduled reporting

### Configuration & Entities

- `settings_manager.py`: Centralized settings API (`load_settings`, `save_settings`, `get_section`, `set_section`)
- `pat_store.py`: Encrypted API key storage
- `redaction_engine.py`: PHI/PII scrubbing with business entity preservation
- `entity_extractor.py`: Dictionary-based payer/product extraction
- `concept_map.py`: Synonym normalization for TF-IDF

### Embeddings & Search

- `embedding_engine.py`: Dense vector encoding via sentence-transformers (all-MiniLM-L6-v2)
- `src/data/embedding/` (7 files): Gemma-based local embedding model, semantic search, clustering

---

## Layer 4: Agents (src/agents/)

The agentic NLP classification pipeline. See `docs/AGENTS.md` for deep dive.

### Pipeline Architecture

```
UI (TRC Analytics page)
  │
  ▼
ScanOrchestrator.start_scan()
  │
  ├── Partition tickets into batches (BatchPacker)
  ├── Boot N workers (WorkerAgent × N)
  ├── Start Supervisor (health monitoring thread)
  │
  ▼
┌─────────────────────────────────────────────┐
│  Worker Loop (per worker)                    │
│  1. Acquire rate token (RateGovernor)        │
│  2. Build prompt with ticket batch           │
│  3. Send to ACPBridge (subprocess)│
│  4. StreamParser extracts fenced blocks      │
│  5. ToolRegistry executes tool_call results  │
│  6. Persist classifications to SQLite        │
│  7. Report health to Supervisor              │
│  8. Next batch                               │
└─────────────────────────────────────────────┘
  │
  ▼
Post-Scan Chain
  ├── AnalystAgent: cross-TRC synthesis, quality audit, novelty validation
  ├── ticket_index_writer: sync to persistent index
  ├── entity_normalizer: normalize extracted entities
  ├── enriched_trends: update trend data
  └── ticket_theme_tagger: tag themes
```

### Key Components

| Component | File | Purpose |
|-----------|------|---------|
| ScanOrchestrator | `scan_orchestrator.py` (2,629 LOC) | Top-level coordinator with ScanWorkerManager-compatible API |
| WorkerAgent | `worker_agent.py` (942 LOC) | Persistent bridge worker with tool-use loop |
| ACPBridge | `acp_bridge.py` (1,509 LOC) | Subprocess bridge with connection pooling and crash recovery |
| Supervisor | `supervisor.py` (489 LOC) | Deterministic health monitor (no LLM, no latency) |
| RateGovernor | `rate_governor.py` (484 LOC) | Token bucket with adaptive backoff on 429s |
| BatchPacker | `batch_packer.py` (246 LOC) | Dual-constraint sizing with per-TRC EMA learning |
| StreamParser | `stream_parser.py` (293 LOC) | Fenced code block parser for LLM output |
| ToolRegistry | `tool_registry.py` (864 LOC) | 7 classification tools with validation |
| AnalystAgent | `analyst_agent.py` (656 LOC) | Post-scan LLM synthesis agent |
| ReportOrchestrator | `report_orchestrator.py` (521 LOC) | Bridge pool for parallel report generation |

---

## Layer 5: LLM (src/gemini/ + src/llm/)

### Provider Architecture

Two providers with a duck-typed interface (`.generate(prompt, system_prompt, timeout) -> str`):

**Gemini** (primary, PHI tasks):
- `GeminiClient`: Wraps Gemini CLI as subprocess. Mandatory PII redaction.
- `ACPBridge` / `ReportBridgeClient`: Persistent subprocess for streaming. Used by scan workers and report orchestrator.

**Claude** (secondary, ops tasks):
- `ClaudeClient`: Direct Anthropic API via stdlib `urllib`. No SDK dependency. PII redaction enabled.
- `claude_tools.py`: 8 read-only tools + 1 human-gated write tool for Claude Messages API.

### Client Factory (src/gemini/client_factory.py)

Central routing function `build_client_for_task(task_type)`:
1. Checks `ai.task_routing.override_all` in settings
2. Looks up per-task route in `ai.task_routing.routes`
3. Falls back to `_DEFAULT_ROUTES` mapping
4. Returns appropriate client instance

Legacy shims `build_client_for_model()` and `build_gemini_client()` preserved for backward compatibility.

### Model Registry (src/llm/model_registry.py)

Singleton holding 5 built-in models. Persisted to `settings.yaml`. Emits `model_changed` Qt Signal when active model switches. UI dropdowns bind to this registry.

---

## Layer 6: MCP (src/mcp/)

Two Model Context Protocol servers for Claude tool-use:

- `alma_mcp_server.py` (344 LOC): stdio JSON-RPC server exposing classification tools (query_taxonomy, get_stats_context, store_classification, etc.). Used by WorkerAgent during NLP scans. **Currently disabled** — NDJSON output is primary.
- `chat_mcp_server.py` (351 LOC): stdio JSON-RPC server exposing chat tools for interactive analysis.

---

## Layer 7: Infrastructure

### Auto-Update System (src/updater/)

- `update_checker.py`: Background thread checks GitHub releases API for newer versions. Emits Qt Signals (`update_available`, `up_to_date`, `check_failed`).
- `updater.py`: Downloads release ZIP, verifies SHA-256, stages for application on next launch using backup-swap-cleanup pattern (Windows file-lock safe).
- `schema_migrator.py`: Applies pending SQL migration files via numbered `migrations/` directory. Tracks applied migrations in `schema_migrations` table.

### Gemini Bridge (scan_server/)

Node.js application (`server.js`) providing a persistent subprocess for Gemini API streaming. Communicates via JSON-line protocol over stdin/stdout. Event types: `token`, `done`, `error`, `stopped`, `pong`, `probe_result`.

### Installer (installer/)

- `build_release.py` (753 LOC): Builds release ZIP with bundled Node.js, Gemini CLI, dependencies
- `install.py` (747 LOC): Cross-platform installer with dependency checking, venv creation, model pre-download

### Migrations (migrations/)

15 SQL files adding tables and indexes incrementally:

| # | File | Purpose |
|---|------|---------|
| 001 | `001_initial_baseline.sql` | Baseline schema |
| 002 | `002_source_warehouse.sql` | Multi-source warehouse tables |
| 003 | `003_guru_tables.sql` | Guru KB integration |
| 004 | `004_guru_card_graph.sql` | Card relationship graph |
| 005 | `005_persistence_layer.sql` | Persistence layer tables |
| 006 | `006_hybrid_chat.sql` | Chat data layer |
| 008 | `008_entity_normalization.sql` | Entity normalization |
| 009 | `009_chat_data_layer.sql` | Chat message storage |
| 010 | `010_import_tracking.sql` | Import run tracking |
| 011 | `011_source_registry.sql` | Source registry |
| 012 | `012_default_zendesk_source.sql` | Default Zendesk source |
| 013 | `013_provider_client_ids.sql` | Provider client IDs |
| 014 | `014_sub_pattern_source_id.sql` | Sub-pattern source tracking |
| 015 | `015_batch_tickets.sql` | Batch ticket tracking |

Note: migration 007 does not exist (skipped during development).

---

## Cross-Cutting Concerns

### PII/PHI Redaction Boundary

```
┌─────────────────────────────────────┐
│  PHI Zone (raw ticket data)          │
│  tickets, comments, conversations    │
│  full_thread, requester_name/email   │
├─────────────────────────────────────┤
│  REDACTION BOUNDARY                  │
│  RedactionEngine.scrub()             │
│  Applied by GeminiClient/ClaudeClient│
│  before every LLM call               │
├─────────────────────────────────────┤
│  PHI-Free Zone                       │
│  LLM prompts, LLM responses         │
│  sub_patterns, nlp_findings          │
│  ticket_index (aggregated)           │
│  analysis_reports, scan_ledger       │
└─────────────────────────────────────┘
```

Redaction config: `config/redaction_patterns.json` (detection patterns) + `config/entities/phi_allowlist.json` (business terms to preserve).

### Connection Discipline

All SQLite connections via `connection_factory.get_connection()`. Guarantees:
- WAL journal mode (concurrent readers + one writer)
- synchronous = FULL (durability)
- busy_timeout = 30s (retry on SQLITE_BUSY)
- foreign_keys = ON
- row_factory = sqlite3.Row

Multi-step mutations use `atomic(conn)` context manager (BEGIN IMMEDIATE → COMMIT/ROLLBACK). Cannot nest.

### Logging

All loggers use the `alma.*` namespace:
- `alma.settings_manager`
- `alma.client_factory`
- `alma.chat_engine`
- `alma.redaction`
- `alma.bridge`
- etc.

### Threading Model

- **Main thread**: All UI (PySide6 widgets)
- **QThread workers**: LLM calls, data import, analysis, chat
- **Communication**: Qt Signal/Slot mechanism
- **Rule**: Never modify widgets from worker threads

### Memory Management

- `trending_engine.py` holds 3-4 copies of full dataset during 12-step pipeline
- `memory_profiler.py` provides opt-in tracemalloc diagnostics (`python main.py --profile`)
- `qt_error_guard.py` monitors memory growth spikes

---

## Data Lifecycle

```
1. IMPORT                           2. STORE
   CSV file / Lightdash API            SQLite (tickets, comments,
   ──────────────────────►             conversations, FTS index)
   csv_ingestion / lightdash_client     entity extraction
   DESTRUCTIVE: deletes all first       source warehouse

3. CLASSIFY (NLP Scan)              4. ANALYZE
   ScanOrchestrator                    trending_engine (12 steps)
   N workers × ACPBridge            incident_engine (Poisson)
   ──────────────────────►             theta_engine (z-score)
   sub_patterns, classifications       watchlist_engine
   ticket_index (persistent)           VOC builder

5. REPORT                           6. EXPORT (optional)
   ai_report_pipeline                  Google Drive upload
   smart_pipeline (scheduled)          gdrive_export.py
   ab_report_pipeline
   analysis_reports table
```

---

## See Also

- `CLAUDE.md` — Quick reference and entry point
- `docs/DATABASE.md` — Complete SQLite schema reference
- `docs/DATA_FLOWS.md` — Detailed data flow documentation
- `docs/AGENTS.md` — Agentic pipeline deep dive
- `docs/LLM_INTEGRATION.md` — LLM provider integration guide
- `docs/ARCHITECTURE_KERNEL.md` — Data scaling analysis (500K row target)
- `DESTRUCTIVE_IMPORT_KERNEL.md` — Import architecture and migration plan
