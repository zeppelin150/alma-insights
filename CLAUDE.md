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

## Source Monitor — Rate-Chart Redesign (2026-05-07)

The Source Monitor page was rebuilt with a rate-per-hour control chart
replacing the old ticket-card "Live Feed" + separate "TRC Spikes" tab.
The 1508-LOC `source_monitor_page.py` god file was decomposed into a
package at `src/ui/pages/source_monitor/` with one module per tab. The
old import path still works via a 6-line shim.

Key new modules:
- [`src/data/source_baseline.py`](src/data/source_baseline.py) —
  `compute_rate_baseline(conn, source, trc_code=None, *, window_hours=24,
  baseline_days=7, spike_sigma=2.0)` returning a `RateBaseline` dataclass.
  Per-hour-of-day trailing mean ± 2σ. Fixed 7-day window.
- [`src/ui/widgets/rate_chart.py`](src/ui/widgets/rate_chart.py) —
  `RateChartWidget`, QPainter control chart with cold-start banner and
  hover tooltips.
- [`src/ui/pages/source_monitor/`](src/ui/pages/source_monitor/) —
  package with `rate_tab.py`, `alerts_tab.py`, `watchlist_tab.py`,
  `connection_tab.py`, `rule_dialog.py`, `_styles.py`, and `page.py`
  (the shell). None over ~340 LOC.
- [`src/ui/pages/guru_wip_page.py`](src/ui/pages/guru_wip_page.py) —
  WIP placeholder for the Guru KB page; legacy `GuruPage` preserved
  intact and routed via `guru.experimental_ui_enabled` settings flag.

**Documentation policy override (this redesign only)**: per explicit
user direction, files in this redesign carry full per-function
docstrings + inline math comments — heavier than CLAUDE.md's general
"no comments" rule. **Do not strip them on a future cleanup pass.**
Specifically: `src/data/source_baseline.py`, `src/ui/widgets/rate_chart.py`,
the `src/ui/pages/source_monitor/` package, and
`src/ui/pages/guru_wip_page.py`.

Settings (`data/settings.yaml`):
- `source_monitor.rate_chart.window_hours` — `24` | `48` (user toggle)
- `source_monitor.rate_chart.baseline_days` — fixed `7`
- `source_monitor.rate_chart.default_view` — `"aggregate"` | `"per_trc"`
- `guru.experimental_ui_enabled` — default `false`; flips Guru page to legacy interactive UI

Test suite: 113 new tests across `test_source_baseline.py` (43),
`test_rate_chart.py` (25), `test_source_monitor_pages.py` (26),
`test_watchlist_ui.py` (18), `test_guru_wip_placeholder.py` (13),
`test_source_monitor_e2e.py` (8). All existing
`test_source_monitor.py` (64) + `test_watchlist_engine.py` (39)
regressions still pass. Total: 236 tests, all green.

Full architecture: [`docs/SOURCE_MONITOR.md`](docs/SOURCE_MONITOR.md).
Plan: `~/.claude/plans/source-monitor-redesign.md`.

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

## Enablement Web Pivot — React/QtWebEngine tabs (M0–M6, 2026-07-14)

The Calendar, Workbench, and Zendesk enablement tabs have React/QtWebEngine
implementations behind the `enablement.web_tabs` setting
(`off` default | `calendar` | `zendesk` | `all` — the single values enable
just that web surface, `all` enables all three); the native Qt tabs stay
intact and render when the flag is off. Same in-process QWebChannel
architecture as the Agent chat — **no web server, no localhost**, one
`file://` bundle.

**Home page (`ui.web_home`, 2026-07-20).** The app-level Home page
([`src/ui/pages/home_page.py`](src/ui/pages/home_page.py)) also has a web
implementation on the SPA route `#/home`:
[`src/services/home_web.py`](src/services/home_web.py) (controller, owns the
SQL + all display formatting), [`src/ui/web/home_bridge.py`](src/ui/web/home_bridge.py)
(pure relay), and `web/src/home/`. Wired in `MainWindow._create_home_page`.

Home is deliberately on its **own flag**, not the `web_tabs` enum, because it
is the **boot page in both modes** (`app_modes._FIRST_PAGE`): turning it on
starts Chromium at launch, and a blank render is the first thing the user
sees rather than one broken tab. Consequences, all load-bearing:
- `ui.web_home` defaults **off** and fails closed on any settings error.
- The native `HomePage` stays permanently as the fallback — construction
  failure falls through to it, and `_arm_home_watchdog` swaps back at runtime
  if the page fails to load or never sets `window.__almaHomeMounted`.
- `requestModeSwitch` is the one authority-bearing slot on Home (it persists
  `app.last_mode` and rebuilds the sidebar + page mounts). It runs the full
  gate and dispatches **deferred** via `QTimer.singleShot(0, …)` — undeferred,
  `switch_mode` would tear down the widget hosting the caller while the
  QWebChannel is still walking the slot.
- The SQL and presentation constants are duplicated between `home_page.py`
  and `home_web.py` on purpose (`src/services` must not import `src/ui`);
  the anti-drift guard is the parity test in `tests/test_home_web_controller.py`.

**Architecture rules (do not break):**
- The web layer is a **pure renderer**. All HTTP/DB/LLM/redaction/business
  logic stays in Python; the page gets pre-shaped JSON viewmodels and holds no
  secrets and no network access.
- **QWebChannel is the trust boundary** — any page script can call any slot, so
  no slot carries authority. Reads return viewmodels; side-effectful actions
  validate against Python-held state + a single-winner claim + a **native**
  confirm (`QMessageBox`, unreachable from Chromium). Pattern lives in
  [`src/services/enablement_web.py`](src/services/enablement_web.py)
  (`request_reschedule`, `js_request_publish`).
- **Untrusted HTML only via `sanitize_html` → `sandbox=""` iframe.** Every
  card preview passes [`src/data/html_sanitize.py`](src/data/html_sanitize.py)
  and renders in a fully-sandboxed (no-scripts) iframe. Never
  `dangerouslySetInnerHTML`/`innerHTML` in `web/src/` — CI enforces this
  (`tests/test_web_guardrails.py`).
- **QWebChannel does NOT own registered objects.** A parentless bridge passed
  as a temporary is GC'd and the channel then dereferences freed memory (native
  access violation). `WebHost` parents orphan bridges; keep it that way.

**Key files:** [`src/ui/web/web_host.py`](src/ui/web/web_host.py) (shared view
host + `extra_bridges` for multi-object channels),
`calendar_bridge.py` / `workbench_bridge.py` / `chat_bridge.py` (pure relays),
[`src/services/enablement_web.py`](src/services/enablement_web.py) (the
controllers that mirror the Qt pages' surfaces), and the React SPA in
`web/src/` (`calendar/`, `workbench/`, `chat/`, `lib/`). page.py wires it via
`_make_calendar` / `_make_workbench` / `_get_web_chat_bridge`.

**Diagnostics (OS-aware):** run `python scripts/web_diag.py` first on any
"web page is blank" report — a cross-compiling fact sheet (Rosetta / Mach-O
arch / framework corruption on macOS; helper exe on Windows; bundle/env/Qt
everywhere). It dumps automatically when a renderer dies and at startup under
`ALMA_WEB_DIAG=1`. Mac checklist: `scripts/verify_web_pivot_mac.sh`.

**Testing:** committed contract/gate/sanitizer tests run headless anywhere.
WebEngine round-trips live in gitignored `tests/test_*_web_local.py` +
`test_web_chat_drawer.py` and **MUST run singly** — offscreen Chromium
teardown stacks to exit 255 across files, and offscreen grabs are always
blank, so assert via `runJavaScript`, never screenshots. JS: `npm --prefix web
run test` (vitest). Full plan + per-milestone build log:
`~/.claude/plans/enablement-web-pivot.md`.

## Zendesk Mirror + Web Workspace (2026-07-24)

### ZENDESK IS READ-ONLY — one-way API (locked owner decision, 2026-07-26)

**The Zendesk API is import-only. No code in this repo may POST, PUT, PATCH or
DELETE to Zendesk — ever, on any surface, under any flag.** The app pulls
articles/sections/categories/macros into the local mirror over GET and stops
there. Approved content reaches Zendesk when an enablement specialist **copies
it out of the mirror and pastes it into the Zendesk editor by hand**. Renn must
have no tool, and no helper in any module a tool executes inside of, that can
reach a Zendesk write.

Why Zendesk specifically: Guru and Asana are slim attack surfaces —
production-locked behind domain + Zscaler restrictions, and content deleted
there is restorable. A Zendesk Guide instance is a **public domain** and its
content is **not restorable** the same way. **Guru and Asana write paths are
out of scope and must not be touched by this policy.**

Enforcement: [`tests/test_zendesk_readonly_guard.py`](tests/test_zendesk_readonly_guard.py)
scans every module under `src/` (AST identifiers, so names held as strings by
the runtime fence do not count as references) and fails if anyone re-adds a
Zendesk REST write method, a `_write` transport helper, a non-GET HTTP call
site in a Zendesk-capable module, a write-shaped `ZendeskClient` method, or a
write-shaped Renn Zendesk tool. It also asserts the GET lanes still exist —
read-only is not read-nothing. **If it fails, remove the write; do not relax
the guard.**

Consequences already in the tree:
- `ZendeskClient` funnels every request through one `_build_request` choke
  point that raises `ZendeskWriteBlocked` on anything but GET. It carries no
  request body, so there is nothing for a write to send.
- `publish_article_draft` / `publish_macro_draft` keep their signatures
  (including the `zendesk_client` kwarg) for source compatibility but **ignore
  the client**: they are local bookkeeping that moves a draft to `copied` and
  report `remote_write: False`. The classic tab's "push" is now a local mark.
- Unaffected and must keep working: ticket ingestion (`fetch_incremental` /
  `fetch_view_tickets` / `fetch_ticket_fields`, product-mode Source Monitor)
  and the mirror pull — both GET-only.

The enablement Zendesk tab has a web implementation on `#/zendesk`
(`enablement.web_tabs` = `zendesk` | `all`) that replicates Zendesk's Guide
article editor + Admin Center macro editor (Garden v8 tokens, dossier at
`~/.claude/plans/zendesk-ui-dossier.md`) over a **local mirror** — the AI and
the workspace never touch the live Zendesk instance.

- **Mirror** (migration 051 extends the mig-030 tables): articles + sections +
  categories + macros with `content_hash` dedup, `origin` 'pull'|'import'
  provenance, contentless FTS5 mirrors with delete-discipline triggers.
  Store API in [`src/data/zendesk_store.py`](src/data/zendesk_store.py).
  **Never INSERT OR REPLACE into mirror tables** (grep-guarded by
  `tests/test_zendesk_mirror_schema.py`).
- **Population**: [`src/data/zendesk_import.py`](src/data/zendesk_import.py) —
  manual file import (API-shaped JSON / HTML / doc_reader docs, per-file
  reports, hostile-input hardened) + `pull_mirror` (GET-only paged pull;
  degrades to `zendesk_not_connected` without credentials).
- **Web triple**: [`src/services/zendesk_web.py`](src/services/zendesk_web.py)
  controller (all authority; sanitize-every-srcdoc; copy-exact via Python
  QClipboard reading DB bytes; native confirms only for destructive
  purge/delete) + `src/ui/web/zendesk_bridge.py` pure relay +
  `web/src/zendesk/` SPA. Wired via `page.py::_make_zendesk` with the native
  `ZendeskPage` as construction-failure fallback.
- **Revisions**: Renn's `zendesk_mirror_tools.py` propose tools create
  `pending` drafts (rationale + sources mandatory); the specialist reviews the
  word-diff, marks ready, copies exact content, pastes into real Zendesk by
  hand, marks copied (`pending → ready → copied`; `copied`/`pushed`
  immutable). **No surface or tool may call a Zendesk write method — there are
  none left to call** (see the read-only policy above); structurally tested in
  `tests/test_zendesk_bridge.py` and `tests/test_zendesk_readonly_guard.py`.
- The classic tab (`zendesk_tab.py`) stays the default; its former push is now
  a local "handled" mark, and its locked contracts in
  `tests/test_zendesk_content.py` must keep passing. Plan:
  `~/.claude/plans/zendesk-clone-web.md`.
- User-facing docs for the policy: `assets/help/create/zendesk.md`,
  `assets/help/create/zendesk-macros.md`,
  `assets/help/troubleshooting/connect-first.md` (claims locked by
  `tests/test_help_claims_create.py` / `tests/test_help_claims_troubleshooting.py`).

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
