# Alma Insights — Project Status
## Single Source of Truth

**Last Updated**: Mar 31, 2026
**Codebase**: ~255 Python files (~95,700 lines) + 11 Node.js files (~2,700 lines) + 24 config files + 3 SQL migrations
**Status**: Platform Build P0-P5 complete. 23 new files, ~30 modified, ~250 new tests (598 total passing). P0: centralized settings_manager (46 call sites migrated). P1: Claude client + model registry + multi-provider factory + 4-tab settings. P2: GitHub auto-update (stage-and-apply, SHA-256, schema migrator). P3: Zendesk live source monitor (incremental cursor export, TRC spike detection). P3.5: source abstraction layer + data warehouse (hot/cold tier, n-gram→LLM hybrid classification, EWMA watchlist engine with learning loop, 5 system rules). P4: Guru KB integration (friction analysis, gap report, content generation with human-gated push, effectiveness tracking with Poisson significance). P5: release packager + install flow. 2 new UI pages (Source Monitor 5 tabs, Guru KB 5 tabs), settings restructured to 4 tabs, ~15 new DB tables across 3 migrations. Gemini + Claude dual-model via build_client_for_model(). **NEXT: Hybrid Chat Architecture sprint (Layers 0-4) — filter engine, session-scoped chat with 8 tools (fast path + thread access + pipeline trigger + semantic search), EmbeddingGemma-300M air-gapped embedding engine, structured report output, post-NLP stats pass. Target: 1 week via Claude Code.**

---

## 1. Architecture

PySide6 desktop app for RCM support ticket analytics. Ingests Zendesk
data via CSV or Lightdash, rebuilds conversations, runs a multi-layer
analytical pipeline, and classifies full ticket populations via Gemini
through a persistent agentic pipeline.

### Engine Stack

| Engine | File | Lines | Model | Purpose |
|--------|------|-------|-------|---------|
| Incident | incident_engine.py | 549 | Poisson CDF + CUSUM | Volume thresholds, sustained drift |
| Theta | theta_engine.py | 581 | Gaussian EWMA ± σ | Sentiment/term/sub-pattern anomalies |
| Trending | trending_engine.py | 1,712 | TF-IDF + NMF + VADER | Terms, topics, sentiment |
| Entity | entity_extractor.py | 110 | Dictionary + Gemini fallback | Payer/product tagging |
| Product Gap | product_gap_engine.py | 184 | Composite scoring | Cross-TRC gap detection |
| A/B | ab_analysis.py | 318 | χ², t-test, Mann-Whitney U | Before/after comparison |
| Embedding | embedding_engine.py | 68→**~350** | **EmbeddingGemma-300M (local, air-gapped)** | **Semantic search + ticket retrieval. REPLACING all-MiniLM-L6-v2. 308M params, 768-dim output (MRL: 512/256/128), 2K token context. Pre-bundled SafeTensors (~1.2 GB), CPU-only torch. Air-gap verified at load time (6 env vars + socket test + audit log). Persistence to ticket_embeddings table. Incremental build post-ingest/post-NLP-scan.** |
| NLP Meta | nlp_meta_analyzer.py | 789 | Aggregation + cross-ref | Within-TRC, cross-TRC, engine cross-ref |
| NLP Synthesis | nlp_synthesis.py | 151 | Gemini narrative | Exemplar-grounded findings |
| N-gram Matcher | ngram_matcher.py | 122 | Weighted n-gram scoring | Provisional classification between scans |
| **VOC Builder** | **voc_builder.py** | **~1,330** | **Multi-perspective Gemini pipeline** | **Root cause analysis: VOCBatchPacker → pipelined priority dispatch (Phase 1 batches + accumulator + 3 specialists + convergence) → 7-section executive report** |
| **VOC Batch Packer** | **voc_batch_packer.py** | **174** | **Greedy first-fit bin-packing** | **127 TRCs → ~8 batches: size-descending sort, intra-TRC chunking for oversized, 75% headroom, 25K prompt overhead** |
| **Report Bridge** | **report_bridge_client.py** | **187** | **Persistent bridge wrapper** | **Drop-in GeminiClient replacement: .generate() via GeminiBridge.call_blocking(), PII redaction, lazy boot** |
| **Report Orchestrator** | **report_orchestrator.py** | **~455** | **4-bridge PriorityQueue dispatch** | **Parallel Gemini calls: PriorityQueue + on_complete callbacks, canary probes, adaptive rate floor/timeout, stall escalation + restart, retry budget (3/6/1), run_resilient(), drain_event keep-alive** |
| **Client Factory** | **client_factory.py** | **~80** | **Multi-provider LLM factory** | **5.5A→P1: build_client_for_model() dispatches to Claude or Gemini based on active model from ModelRegistry. build_gemini_client(use_bridge) kept as backward-compat shim. All pipeline code uses factory — provider-agnostic.** |
| **Tech Summary Builder** | **tech_summary_builder.py** | **~260** | **5-table DB aggregation** | **5.5A: build_tech_summary(db, scan_id) → tokens, cost, API calls, batch breakdown, stage timeline, bridge health from gemini_usage/nlp_scan_runs/nlp_batches/scan_events/probe_history → formatted markdown** |
| **Analyst Report Formatter** | **analyst_report_formatter.py** | **~230** | **JSON→markdown parser** | **5.5A: Converts analyst_reports rows (synthesis/audit/novelty/merge) to structured markdown. get_latest_analyst_summary(db, scan_id) convenience method.** |
| **Schedule Manager** | **schedule_manager.py** | **243** | **DB-backed scheduling service** | **5.5C: QObject + 60s QTimer poll → report_schedules table. 4 recurrence types (daily/weekly/biweekly/monthly), timezone-aware (zoneinfo, 5 US zones + UTC), compute_next_run() pure function, run_triggered signal.** |
| **Settings Manager** | **settings_manager.py** | **~130** | **Centralized config singleton** | **P0: load_settings()/save_settings()/get_section()/set_section(). Auto-migrates config/settings.yaml → data/settings.yaml. 46 call sites migrated.** |
| **Model Registry** | **model_registry.py** | **~190** | **Multi-provider LLM registry** | **P1: ModelConfig dataclass + singleton. Tracks all models, active model, enabled state. model_changed Signal. Persists to settings.yaml ai section.** |
| **Claude Client** | **claude_client.py** | **~240** | **Anthropic API client** | **P1: Duck-typed .generate() (same as Gemini). PII redaction (HIPAA mandatory + optional aggressive). Streaming. Auth/rate-limit handling.** |
| **Update Checker** | **update_checker.py** | **~125** | **GitHub Releases API** | **P2: Private repo PAT support. update_available/up_to_date/check_failed Signals.** |
| **Updater** | **updater.py** | **~295** | **Stage-and-apply updater** | **P2: Download zip → SHA-256 verify → extract to _update_staging/ → swap src/+config/ on next launch. Rollback on failure.** |
| **Schema Migrator** | **schema_migrator.py** | **~105** | **Numbered SQL migration runner** | **P2: Reads migrations/*.sql in order. schema_migrations tracking table. Runs on every app start.** |
| **Zendesk Client** | **zendesk_client.py** | **~200** | **Zendesk API v2** | **P3: Incremental cursor-based export. TRC extraction: subject/custom field/tag prefix modes. Cursor persistence via pat_store.** |
| **Zendesk Monitor** | **zendesk_monitor.py** | **~260** | **Background QTimer polling** | **P3: Configurable interval (min 30s). TRC spike detection (50% increase or ≥3 with no prior). Dedup via _seen_ids. tickets_received/spike_detected Signals.** |
| **Source Warehouse** | **source_warehouse.py** | **~250** | **Hot/cold tier data warehouse** | **P3.5: Hot tier (in-memory, PHI for UI) + Cold tier (SQLite source_events, NO PHI). Hybrid classification: n-gram fast path → LLM slow path if confidence < 0.7. Hourly/daily rollups. 7-day prune.** |
| **Watchlist Engine** | **watchlist_engine.py** | **~350** | **EWMA learning + LLM triage** | **P3.5: 4 rule types (keyword/entity/volume/compound). 5 system rules. EWMA confidence loop (confirm↑ dismiss↓, floor 0.1, ceiling 0.95). Gate at < 0.3. Cooldown. Few-shot LLM triage for compound rules.** |
| **Guru Client** | **guru_client.py** | **~150** | **Guru API v1** | **P4: Basic auth. list_collections/cards, get/search/update_card (human-gated). SHA-256 content_hash change detection.** |
| **Guru Friction Pipeline** | **guru_friction_pipeline.py** | **~250** | **Coverage analysis** | **P4: sync_articles() → analyze_coverage() (keyword + LLM scoring) → get_gap_report() (LEFT JOIN sub_patterns vs guru_friction_coverage, worst-first). friction_score on articles.** |
| **Guru Content Pipeline** | **guru_content_pipeline.py** | **~200** | **LLM content generation** | **P4: propose_rewrite() / propose_new_article() → guru_content_drafts. approve_and_push() human-gated only → GuruClient.update_card() + baseline recording. Fallback templates when no LLM.** |
| **Guru Effectiveness** | **guru_effectiveness.py** | **~150** | **Poisson significance testing** | **P4: record_baseline() on push → measure_effectiveness() after 14 days. Pre/post volume delta. Pure-Python _poisson_cdf() (log-space, no scipy). p < 0.05 significance flag.** |

### Agentic NLP Pipeline (5.0→5.3) — ✅ BUILT + PRODUCTION VALIDATED

```
UI → ScanOrchestrator.start_scan()
  │
  ├─ Partition TRCs (same strategy + BatchPacker dual-constraint sizing)
  │    gemini-2.0-flash → 20K output / 100K input budget, max 25/batch
  │    gemini-2.5-flash → 200K output / 300K input budget, max 75/batch
  ├─ Boot N GeminiBridge instances (Node subprocess, ~1.5s boot ONCE)
  ├─ Create N WorkerAgents (each owns a bridge + ToolRegistry)
  │
  ├─ Worker threads pull from batch queue:
  │    WorkerAgent.classify_batch():
  │      → RateGovernor.acquire() (thread-safe, adaptive interval)
  │      → Build prompt (live taxonomy via query_taxonomy tool)
  │      → Bridge.call_streaming() → JSON-line protocol stdin/stdout
  │      → StreamParser extracts fenced ```tool_call blocks in real-time
  │      → Per-ticket store_classification tool calls (immediate persist)
  │      → Fallback: re-feed full_text through fresh StreamParser if streaming missed events
  │      → Context reset: proactive bridge restart every 5 batches (MAX_BATCHES_BEFORE_RESET)
  │      → Crash recovery: skip already-classified ticket_ids on resume
  │
  ├─ Supervisor polls health every 5s (deterministic, NO LLM):
  │    Parse rate < 70% → restart worker
  │    Avg confidence < 0.15 → restart worker (grace: 5 batches since last reset)
  │    Stall > 300s → restart worker
  │    Context > 800K tokens → reset worker
  │    Writes agent_health + scan_progress (UI polls)
  │
  ├─ After classification: AnalystAgent runs 3 judgment tasks:
  │    Cross-TRC synthesis (shared root causes)
  │    Quality audit (grades random 25-sample)
  │    Novelty validation (batched, no cap — verdicts fed back:
  │      DUPLICATE→is_novel=0, MERGE advisory, VALID→no change)
  │
  ├─ After analyst: NLPMetaAnalyzer (unchanged Layer 2)
  │
  ├─ After meta-analyzer: AnalystAgent.pattern_merge
  │    (reads fresh sub_patterns with updated tiers + lifetime_tickets)
  │
  └─ Finalize scan
```

### Gemini Streaming Bridge (V4) — ✅ BUILT

```
gemini_bridge.mjs (771 lines)
  │
  ├─ Boot: loadSettings → parseArgs → loadCliConfig → storage.init
  │        → initializeApp → validateAuth → refreshAuth → config.init
  │        (~1.5s, once)
  │
  ├─ Streaming mode (default):
  │    geminiClient.sendMessageStream() — direct core library call
  │    Token-by-token deltas, heartbeats, stall detection,
  │    abort support, full agentic tool loop
  │
  ├─ Legacy mode (stream:false):
  │    runNonInteractive() wrapper — backward compat
  │
  ├─ Smart Exit Guard:
  │    Code 0 (SUCCESS) → block, bridge stays alive
  │    Code 42 (INPUT_ERROR) → block, per-call error
  │    Code 130 (CANCELLATION) → block, not fatal
  │    Code 41 (AUTH_ERROR) → allow + bridge_fatal event
  │    Code 52 (CONFIG_ERROR) → allow + bridge_fatal event
  │
  └─ Platform fixes:
       Module resolution (global npm → CLI → core)
       process.exit interception (smart guard)
       stdin hijacking (isTTY=false during calls)
```

### Bridge Health Monitor (6.2) — ✅ BUILT

```
GeminiBridge (per instance):

  WATCHDOG THREAD (every 3.0s):
    is_alive()? → YES: sleep → NO: _notify_death()
      ├─ _bridge_healthy = False
      ├─ death_count++
      ├─ POISON all waiting request queues (unblocks callers)
      └─ fire _on_death callback

  STALL ESCALATION:
    record_stall() on stall_timeout errors:
      consecutive_stalls++ / stall_count++
      if consecutive_stalls >= 3 → ESCALATION → caller restarts bridge
    record_success() on any non-stall:
      consecutive_stalls = 0 (reset)

  SUPERVISOR INTEGRATION (deterministic, every 5s):
    bridge_alive == False?      → RESTART worker
    consecutive_stalls >= 3?    → RESTART worker
    parse_rate < 50%?           → RESTART (after 5+ batches)
    avg_confidence < 15%?       → RESTART (after 5+ batches)
    no_progress > 300s?         → RESTART (stall timeout)
    context_tokens > 800K?      → RESET (not full restart)
```

### Canary Probe System (6.3) — ✅ BUILT

```
PRE-SCAN CANARY PROBES:
  _run_canary_probes(scan_id):
    For each bridge (sequential):
      bridge.probe(timeout=30)
        → Python → Node.js → HTTPS → Google → back
        → prompt: "Respond with exactly one word: OK"
        → Returns: {latency_ms, status, error}
      db.store_probe(scan_id, bridge_idx, status, latency_ms)
    Emit scan_event: "preflight" with P50/P95 summary

  ADAPTIVE THRESHOLDS (from probe data):
    ① Current P50/P95 from this scan's probes
    ② Historical P95 from probe_history (7-day rolling)
    ③ reference_p95 = max(current_p95, historical_p95)

    Call timeout = (P95/1000) × 62.5 × 1.5, clamped [45s, 600s]
    Rate governor floor = max(15.0s, P50_seconds × 3.0)

  VOC INTEGRATION:
    Probe data injected into VOC synthesis via _build_model_health_context():
    "Pre-scan probe latency: P50=1823ms, P95=3741ms (3 bridges probed)"
```

### Bridge-Powered Report Engine (7.0) — ✅ BUILT

```
REPORT ENGINE ARCHITECTURE:
  T1: ReportBridgeClient — drop-in GeminiClient replacement
    .generate() → GeminiBridge.call_blocking() (persistent subprocess)
    PII: _redact_base() + _redact_aggressive() (from GeminiClient)

  T2: ReportOrchestrator — 4-bridge PriorityQueue dispatch (8.5→9.0)
    ThreadPoolExecutor(4) + PriorityQueue + on_complete callbacks
    Canary probes on boot → adaptive rate floor + call timeout
    Stall escalation (3 consecutive → bridge.restart())
    Retry budget (3× stall, 6× quota, 1× other) with requeue
    run_resilient() for single critical calls (convergence)
    drain_event keep-alive for dynamic task injection

  T3: Single choke-point swap (5.5A: centralized in client_factory.py)
    build_gemini_client(use_bridge=True) → returns ReportBridgeClient
    build_gemini_client(use_bridge=False) → returns GeminiClient (fallback)
    Replaces 3 diverged _build_gemini_client() copies in ai_reports/ab_compare/smart_pipeline
    Duck-typed .generate() covers ALL flows:
      ✓ Standard reports | VOC | NLP Synthesis | Follow-up chat

  VOC PIPELINE (9.0): Pipelined Priority Dispatch
    VOCBatchPacker: 127 TRCs → ~8 batches (greedy first-fit, 75% headroom)
    Phase 1: Batched analysis at priority 0 (~25-35 calls)
    Accumulator: 8-round progressive synthesis at priority 1 (fills gaps)
    Specialists: 3 parallel (Pattern, Novelty, Friction) at priority 0
    Convergence: 1 call via run_resilient() → 7-section executive report
    5 daemon threads: main (run_parallel), batch_parser, accumulator,
      specialist_injector + on_complete callback in worker threads
    Graceful degradation: failed batches → partial TRCs, failed specialists
      → noted in report, failed convergence → _assemble_partial_report()

  T6-T7: Windowed temporal context
    Date range → weekly windows → per-window mini data block
    Injected via {temporal_context} in 4 prompt templates

  T8: Data-grounded follow-up chat
    User question → intent detection → DB enrichment → Gemini
    _detect_trc_reference() + _detect_date_reference()
    + _detect_sample_request() → live DB mini blocks + NLP aggregates

  T9: Lifecycle management
    Lazy boot on first report (not page load)
    Shared across all report types + chat
    cleanup() on page close → bridge.shutdown()
```

### Tool Registry (7 Tools)

| # | Tool | Purpose |
|---|------|---------|
| 1 | `query_taxonomy` | Get active sub-patterns + n-gram fingerprints for a TRC |
| 2 | `get_stats_context` | Poisson flags, CUSUM state, theta baselines, rising terms |
| 3 | `store_classification` | Persist one ticket classification immediately (crash resilience) |
| 4 | `flag_for_review` | Flag ticket for human review (compliance, PHI, abuse) |
| 5 | `get_full_thread` | Retrieve untruncated conversation thread |
| 6 | `report_progress` | Write progress for UI polling (every 25 tickets) |
| 7 | `check_cross_trc` | Check if a pattern exists in a different TRC |

### Three-Layer NLP Pipeline

**Layer 1 — Classify**: WorkerAgents (via ScanOrchestrator) batch tickets
BY TRC, inject live statistical context via tools, send to Gemini via
persistent streaming bridge. Per-ticket structured JSON output with
per-ticket persistence.

**Layer 2 — Analyze**: nlp_meta_analyzer.py (unchanged, 789 lines)
aggregates classifications, manages sub-taxonomy tiers, updates n-gram
fingerprints, detects cross-TRC patterns, cross-references statistical
engines, computes impact scores, selects exemplar tickets.

**Layer 3 — Synthesize**: nlp_synthesis.py (unchanged, 151 lines) sends
top findings + exemplar tickets to Gemini for qualia-rich narrative.

**Layer 4 — Analyst** (NEW in 5.0): AnalystAgent runs 4 post-scan
judgment tasks: cross-TRC synthesis, quality audit, novelty validation,
pattern merge suggestions. Results stored in analyst_reports table.

**Learning Loop**: Sub-patterns persist across scans. Workers query
live taxonomy via `query_taxonomy` tool (no static snapshots). Between
scans, ngram_matcher.py provides provisional classifications.
theta_engine.py monitors sub-pattern share for anomaly detection.

### VOC Root Cause Analysis Pipeline (6.0→9.0) — ✅ BUILT + E2E VALIDATED

Multi-perspective Gemini pipeline: bin-packed batches → pipelined priority dispatch (accumulator + 3 specialists) → convergence assembly → 7-section executive report. Replaces the 6.0 two-phase sequential model.

```
PHASE 0 — PLANNING:
  VOCBuilder.plan()
  ├── TRC ticket counts → sample size calculation
  ├── NLP data availability check per TRC
  ├── Cost estimate (input + output chars × pricing)
  ├── Time estimate (calls × avg latency)
  └── Plan Preview Dialog (TRC count, samples, calls, cost, time)

PHASE 1 — BATCHED ANALYSIS (pipelined, priority 0):
  VOCBatchPacker: 127 TRCs → ~8 batches (greedy first-fit, 75% headroom)
  For each batch:
  ├── Sample tickets per TRC (≤200: all, 200-1000: 200, >1000: 300)
  │   ├── NLP available → stratify by friction_type, 20% anomaly oversample
  │   └── No NLP → stratify by CSAT quartile
  ├── Package as JSONL (truncate 1500 chars, PII redact, NLP enrich)
  ├── Assemble prompt (voc_analysis_batch.txt, === TRC: <name> === delimiters)
  └── Dispatch to ReportOrchestrator PriorityQueue (priority 0)
  batch_parser_loop: parse responses → trc_analyses dict
  Split-on-fail: missing TRCs requeued as individual tasks

ACCUMULATOR — PROGRESSIVE SYNTHESIS (pipelined, priority 1):
  8 rounds, fills Phase 1 whitespace (never starves Phase 1):
  ├── Wait for 80% of available TRC analyses
  ├── Build prompt (voc_accumulator.txt + new TRCs + prior ledger + synthesis)
  ├── Enqueue at priority 1 → Gemini → parse NEW_FINDINGS + RUNNING_SYNTHESIS
  ├── Evidence ledger: append-only (model never mutates prior entries)
  └── Synthesis: model's scratchpad, fully rewritten each round

SPECIALISTS — 3 PARALLEL DETECTORS (after Phase 1, priority 0):
  All receive {accumulator_context} (snapshot of partial ledger):
  ├── Pattern Detector (voc_pattern_detector.txt) → §2 Top Friction, §4 Improving
  ├── Novelty Scanner (voc_novelty_scanner.txt) → §3 What's Getting Worse
  └── Friction Scorer (voc_friction_scorer.txt) → §5 Root Cause Map, §6 Recommendations

CONVERGENCE — EXECUTIVE REPORT (run_resilient, 1 call):
  Inputs: accumulator ledger + synthesis + 3 specialist reports
  Template: voc_convergence.txt
  Output: 7-section executive report (40-53k chars)
    §1. Executive Summary (convergence integrates all sources)
    §2. Top Friction Points (pattern detector)
    §3. What's Getting Worse (novelty scanner)
    §4. What's Getting Better (pattern detector)
    §5. Root Cause Map (friction scorer)
    §6. Recommendations (friction scorer)
    §7. TRC Summary Table (convergence)
  Fallback: _assemble_partial_report() if convergence fails

PERSIST + DISPLAY:
  ├── Save to data/reports/voc_report_<timestamp>.md
  ├── Save to analysis_reports (report_type='voc')
  └── Enable: Copy, Save .md, Save to History, Export to Drive
```

**Token budget:** Phase 1 max ~200K effective chars/batch (300K raw × 75% headroom − 25K overhead). Convergence ~800K char input cap.

**PII safety chain:** Truncate → _redact_base() (SSN/email/phone/card/DOB/URL/address) → _redact_aggressive() (name heuristics) → JSONL packaging → Gemini bridge.

**E2E validated:** 4 live runs on 888-ticket / 127-TRC dataset. 19-24 min, 40-53k char reports, 83-86% TRC coverage. First-ever complete 7-section executive report.

### NLP Scan — Dual Mode (5.0 active + 4.1 fallback) + UI Modes (5.5)

```
AGENTIC MODE (default — v5.0):
  PySide6 → ScanOrchestrator
    → N GeminiBridge instances (persistent Node.js)
    → N WorkerAgents (tool-use loop)
    → Supervisor (deterministic health monitoring)
    → AnalystAgent (post-scan judgment)
    → NLPMetaAnalyzer (Layer 2, unchanged)
    → NLPSynthesis (Layer 3, unchanged)

UI MODES (5.5):
  Debug ON:  Full config card, control buttons, live monitor, docs panel.
             Scan launches via direct ScanOrchestrator.
  Debug OFF: Simple scan card (last-scan summary, Start, progress bar, Cancel).
             Scan launches via JobQueue → CallableWorker → JobOverlay.

GEMINI CONTENTION PREVENTION (5.5):
  Scan active → scan_active_changed(True) →
    AI Reports: _generate_btn disabled + tooltip
    Trending:   _run_ai_enhancements() short-circuits
    Hypothesis: dialog guard "scan in progress"
  Scan ends   → scan_active_changed(False) → all unblocked

FALLBACK MODE (v4.1 — swap 2 imports):
  scan_worker.py + scan_worker_manager.py remain on disk
  Revert: docs/NLP_PIPELINE_4_1_REFERENCE.md

SERVER MODE (retained — future enterprise):
  scan_server/*.js — 10 files, ~1,900 lines
  Not active: raises NotImplementedError
```

### HIPAA Compliance

| ID | Requirement | Status |
|----|------------|--------|
| A1 | Shared redaction config (config/redaction_patterns.json) | ✅ Built |
| A2 | Parity verification before first batch | ✅ Built |
| A3 | PII canary on every batch payload | ✅ Built |
| A4 | Redact Gemini output before storage | ✅ Built |
| A5 | Redact n-grams before storage | ✅ Built |
| A6 | Shared secret on Node REST API | ✅ Built (server mode) |
| A7 | Provisional classification pruning | ✅ Built |
| A8 | Disk encryption + permissions check | ✅ Documented |

---

## 2. File Inventory

### Python — src/agents/ (13 files, ~3,320 lines)

| File | Lines | Purpose | Key changes |
|------|-------|---------|-------------|
| scan_orchestrator.py | ~685 | Top-level coordinator | 5.4: dual-constraint batching. 6.3: +canary probes, adaptive thresholds. 1.0: +partial parse completeness check + stagnation detection (~20 lines), +pattern merge moved after meta-analyzer, +novelty verdict application |
| tool_registry.py | ~478 | 7 tools with SQLite handlers | `get_full_thread` dual-path query. 1.0: +ticket ID boundary guard in _tool_store_classification() (rejects overflow ticket_ids not in batch manifest) |
| **report_orchestrator.py** | **~455** | **8.5→9.0 MODIFIED** | **9.0: +PriorityQueue dispatch (priority, seq_num, task, retries), +on_complete callbacks, +run_resilient() for single critical calls, +drain_event keep-alive for dynamic injection. 8.5: canary probes, stall escalation, retry budget, queue-pull, 4 bridges** |
| gemini_bridge_wrapper.py | ~438 | Python wrapper for bridge.mjs | 6.2: watchdog, queue poisoning, stall escalation, get_stats(), probe(). 9.0: +_send_lock in _send_raw() for thread-safe stdin |
| worker_agent.py | ~400 | Persistent Gemini worker | 5.3: _batches_since_reset. 6.2: bridge health in get_health(), stall escalation triggers restart |
| supervisor.py | ~325 | Deterministic health monitor | 5.3: confidence floor 0.15. 6.2: +bridge_alive check, +consecutive_stalls >= 3 check, +health snapshots to agent_health table |
| analyst_agent.py | ~410 | Post-scan LLM agent, 4 tasks | 1.0: run_novelty_validation() rewritten (removed [:30] cap, smart batching to input_budget, multi-call aggregation), +apply_novelty_verdicts() (DUPLICATE→is_novel=0, MERGE advisory) |
| **report_bridge_client.py** | **187** | **Drop-in GeminiClient replacement** | **7.0 NEW: .generate() via GeminiBridge.call_blocking(), PII redaction (_redact_base + _redact_aggressive), lazy bridge boot, .is_available(), .shutdown()** |
| stream_parser.py | 230 | Fenced code block event parser | — |
| batch_packer.py | ~215 | Dual-constraint batch sizing | 5.4: input+output budgets, hard cap 75 |
| rate_governor.py | ~188 | Thread-safe adaptive token bucket | 6.3: +set_probe_floor(). 8.5: +two-tier recovery (10% at 5, 20-30% at 10), +_last_rate_limit_time |
| **voc_batch_packer.py** | **174** | **9.0 NEW: VOC bin-packing** | **Greedy first-fit: size-descending sort, intra-TRC chunking for oversized, 75% headroom, 25K prompt overhead. 127 TRCs → ~8 batches** |
| __init__.py | 5 | Package init | — |

### Python — src/data/ (32 files)

| File | Lines | Pass | Role |
|------|-------|------|------|
| trending_engine.py | 1,712 | 1/1.5 | TF-IDF, NMF, velocity, cross-TRC. 5.4 hotfix: fixed suggest_keyword_improvements key mapping (all_terms/rising_terms → rising/cooling) |
| db_manager.py | ~1,818 | 1→**5.5D** | All schema (8 NLP + 5 agent + 3 cost/event tables). 6.0: +get_nlp_aggregate_for_trc(). 6.3: +probe_history table. 5.5A: +report_schedules table + 5 CRUD methods. 5.5D: +get_smart_runs_with_reports() LEFT JOIN smart_report_runs + analysis_reports + correlated gemini_usage cost subquery |
| scan_worker.py | 811 | 4.1 | Detached CLI batch worker (FALLBACK) |
| nlp_meta_analyzer.py | 789 | 4.0 | Layer 2 meta-analysis |
| lightdash_client.py | 799 | 1 | Lightdash API |
| theta_engine.py | 581 | 1.75/4.0 | EWMA + sub-pattern share tracking |
| incident_engine.py | 549 | 1.75 | Poisson CDF + CUSUM |
| **voc_builder.py** | **~1,330** | **6.0→9.0** | **VOC Root Cause Analysis. 9.0: +_run_analysis_and_synthesis() pipelined dispatch (batch_parser_loop, accumulator_loop, specialist_injector, on_complete), +VOCBatchPacker integration, +accumulator evidence ledger (8-round progressive), +3 specialist prompts (pattern/novelty/friction), +convergence assembly, +_assemble_partial_report() fallback, +stat context caching, +_parse_batch_response(), +_merge_chunks(). 7.0: +_run_analysis_phase_parallel(), +_build_model_health_context(), +temporal context** |
| report_builder.py | ~480 | 3.0→**7.0** | Stats → Gemini prompt assembly. 7.0: +build_windowed_data_blocks() (weekly windows), +format_temporal_context() (week-over-week narrative) |
| **tech_summary_builder.py** | **~260** | **5.5A NEW** | **build_tech_summary(db, scan_id): aggregates from gemini_usage (tokens/cost/calls), nlp_scan_runs (batches/classified/findings), nlp_batches (per-TRC breakdown), scan_events (pipeline timeline), probe_history (bridge health). format_tech_summary_as_markdown() renders tables.** |
| **analyst_report_formatter.py** | **~230** | **5.5A NEW** | **Parses analyst_reports JSON → structured markdown: synthesis (root causes, systemic issues, correlations), audit (quality score, grade distribution, errors), novelty (verdict counts, non-valid findings), merge (candidates, rationale, confidence). get_latest_analyst_summary(db, scan_id) convenience.** |
| **ab_report_pipeline.py** | **~185** | **5.5B NEW** | **Multi-phase A/B pipeline: dual dataset assembly → statistical comparison (χ², t-test, Mann-Whitney U) → tech metadata → Gemini narrative → final assembly. Graceful degradation to stats-only markdown table if Gemini unavailable.** |
| **ai_report_pipeline.py** | **~170** | **5.5B NEW** | **Multi-phase AI report pipeline: data assembly → analyst/tech metadata attachment → Gemini generation → final assembly. Graceful degradation to data-only formatted markdown if Gemini unavailable.** |
| smart_pipeline.py | 401 | 3.0/5.0 | Headless pipeline (now routes to ScanOrchestrator) |
| **schedule_manager.py** | **243** | **5.5C NEW** | **Persistent scheduling service: QObject + 60s QTimer poll → queries report_schedules WHERE enabled AND next_run_at <= now() → compute_next_run() (daily/weekly/biweekly/monthly, timezone-aware via zoneinfo, 5 US zones + UTC) → fire run_triggered signal. ISO lexicographic comparison, day-of-month capped at 28.** |
| csv_ingestion.py | 381 | 1 | CSV import + column mapping |
| scan_worker_manager.py | 359 | 4.1 | Worker subprocess lifecycle (FALLBACK) |
| ab_analysis.py | 318 | 3.0 | Statistical comparison |
| lightdash_mock.py | 284 | 1 | Test mode |
| gemini_setup.py | 246 | organic | First-run wizard |
| scan_server_manager.py | 243 | 4.0 | Original REST manager (retained) |
| conversation_rebuild.py | 232 | 1 | Thread reconstruction |
| usage_tracker.py | 227 | **5.1** | **Token/cost aggregation, plan utilization, GEMINI_PLANS pricing reference** |
| run_logger.py | 209 | 1.75 | Run logging |
| trc_analytics.py | 197 | 1 | TRC metrics |
| job_queue.py | 195 | organic | Background tasks |
| product_gap_engine.py | 184 | 3.0 | Gap scoring |
| demo_data.py | 165 | 1 | Test data |
| compound_discovery.py | 154 | 1.5 | PMI compounds |
| nlp_synthesis.py | 151 | 4.0 | Layer 3 narrative |
| ngram_matcher.py | 122 | 4.0 | Provisional classification |
| entity_extractor.py | 110 | 3.0 | Payer/product tagging |
| rebuild_utils.py | 74 | 1 | Helpers |
| embedding_engine.py | 68→**~350** | 2.0→**HYBRID** | Local embeddings → **EmbeddingGemma-300M: air-gapped SafeTensors, verify_air_gap(), embed_documents/embed_query with prompt format, build_embeddings() incremental persistence, load_embedding_cache() with session scope masking** |
| concept_map.py | 64 | 1.5/2.0 | Synonyms |
| pat_store.py | 79 | 1 | PAT persistence |

### Python — src/ui/ (31 files, largest components)

| File | Lines | Pass |
|------|-------|------|
| pages/settings_page.py | 1,876 | 1→3.1 |
| **pages/trending_topics.py** | **~2,194** | **1.5→5.5D** (1.0 Phase 3: QWidget → AnalysisPageBase, 4 tabs, KPICardRow, FilterChipBar. 5.5D: hypothesis synthesis QTextBrowser.setPlainText() → MarkdownViewer.set_markdown()) |
| **pages/incidents_page.py** | **~1,600** | **1.75→1.0** (1.0 Phase 4: QWidget → AnalysisPageBase, 4 tabs (Overview/Open Incidents/θ EWMA/Reports), KPICardRow, FilterChipBar, scan_complete signal preserved) |
| pages/nlp_scanner_page.py | ~1,200 | 4.0→**5.5** (rewritten: 3-tab coordinator, 5.5: debug mode gating, simple scan card, job queue integration) |
| widgets/charts.py | 936 | 1 |
| main_window.py | ~966 | 1→**5.5C** (5.5: scan wiring. 6.0: +VOC job factory. 7.0: +_stop_all_workers(). 5.5C: +ScheduleManager instantiation + wiring with try/except guard) |
| pages/ai_reports.py | ~1,212 | 2.0/3.0→**5.5D** (5.5B: PipelineWorker, MarkdownViewer, collapsible sections, shared client_factory. 5.5C: +restored scan blocking. 5.5D: +Save .html button with full Alma-branded HTML document export, enabled in 7 contexts. 7.0: +ReportBridgeClient, +cleanup()) |
| widgets/drilldown_panel.py | 798 | 1.5 |
| **pages/trc_analytics.py** | **~645** | **1→5.5D** (1.0: AnalysisPageBase, 5 tabs, NLP Scanner lifecycle, View Analyst Reports. 5.5D: 127-line _render_analyst_report_html() → 22-line analyst_report_formatter + md_to_html() pipeline, −105 lines) |
| widgets/taxonomy_browser.py | 641 | **5.1** (health stats, growth chart, tree table, findings) |
| widgets/scan_monitor.py | 607 | **5.1** (sprout, %, preflight log, batch rows, timer) |
| pages/conversation_search.py | 592 | 1 |
| **pages/smart_reporting.py** | **~1,137** | **3.0/4.1→5.5D** (5.5C: full overhaul with schedule CRUD, MarkdownViewer, 6-column history. 5.5D: +7-column history with $X.XX cost via JOIN query, +export action bar (Copy/Save .md/Save .html), uses get_smart_runs_with_reports() with hasattr fallback) |
| **widgets/chat_widget.py** | **530** | **7.0 REWRITTEN** (data-grounded follow-up: TRC/date/sample detection → live DB enrichment → Gemini) |
| widgets/cost_dashboard.py | 530 | **5.1** (cost limits, token KPIs, bar chart, plan ref) |
| dialogs/ingestion_dialog.py | 527 | 1 |
| pages/ab_compare.py | ~500 | 3.0→**5.5B** (5.5B: ABPipelineWorker, MarkdownViewer replaces QTextEdit, +collapsible Tech Summary, +Save .md + Save to History + Report History (previously missing), _build_gemini_client() → shared client_factory) |
| widgets/control_chart.py | 420 | 1.75 |
| **widgets/markdown_viewer.py** | **~280** | **5.5A→5.5B** (5.5A: QTextBrowser + regex markdown→HTML, Alma CSS with theme tokens, zero deps. 5.5B: +public md_to_html() for DrilldownPanel detail callbacks) |
| (+ 14 more widgets/dialogs) | ~2,800 | various |

### Gemini — src/gemini/ (6 files)

| File | Lines | Pass | Role |
|------|-------|------|------|
| gemini_bridge.mjs | ~780 | 4.1→5.2 | Persistent streaming bridge (V4) + model override + token reporting. 6.2: watchdog/stall/poison handled by Python wrapper. 6.3: +probe support |
| gemini_client.py | 219 | 2.0 | CLI wrapper (legacy callers). 7.0: replaced by ReportBridgeClient for all report flows. 5.5A: construction centralized in client_factory.py |
| **client_factory.py** | **~80** | **5.5A NEW** | **build_gemini_client(use_bridge=False): single shared factory replacing 3 diverged _build_gemini_client() copies in ai_reports/ab_compare/smart_pipeline. use_bridge=True → ReportBridgeClient (persistent, ~8s/call) with ImportError fallback → GeminiClient (~25s). Always loads settings.yaml + checks pat_store API key.** |
| prompts.py | 112 | 2.0/3.0 | Template loader |
| gemini_setup.py | 246 | organic | First-run wizard |
| report_builder.py | 416 | 3.0 | Stats → Gemini payload assembly |

### Node.js — scan_server/ (retained, not active)

| File | Lines |
|------|-------|
| server.js | 475 |
| db.js | 298 |
| batch_worker.js | 276 |
| payload_builder.js | 194 |
| response_parser.js | 190 |
| redaction.js | 119 |
| test_spec_41.js | 179 |
| utils.js, config.js, tls.js | 172 |

### Tests (9 NEW files + 2 updated)

| File | Tests | Time | Coverage |
|------|-------|------|----------|
| test_pipeline_full.py | 51 | 1.3s | All 5.0 components. 5.4: max_batch + input_budget. 5.5: +4 tests |
| test_pipeline_stress.py | 23 | 21.3s | Concurrency, edge cases, 50K chunks |
| test_pipeline_live.py | 10 | ~320s | **END-TO-END**: real bridge → real Gemini → real classification → real DB |
| test_full_system.py | 57 (+1 pre-existing fail) | — | Original system (no regressions) |
| bench_53.py | — | ~1020s | Production benchmark. 5.4: fixed column names |
| **test_build_55.py** | **14** | **<1s** | **5.5: signal wiring, scan blocking, debug gating, simple card, bug bash verification** |
| **test_voc_builder.py** | **20** | **<1s** | **6.0: planning (2), sampling (4), JSONL packaging (3), NLP aggregation (3), prompts (2), cost (1), cancel (1), E2E with mocked Gemini (1), UI integration (3)** |
| **test_report_orchestrator.py** | **22** | **<1s** | **8.5: canary probes (4), stall escalation (2), retry budget (3), queue-pull dispatch (2), rate governor recovery (7), backward compat API (4). All mock bridges.** |
| **test_voc_build9.py** | **33** | **<1s** | **9.0: send_raw locking (1), priority queue dispatch (4), on_complete callback (2), VOCBatchPacker (6), run_resilient (2), stat context caching (2), batch response parsing (4), oversized TRC chunking (2), accumulator parsing (3), specialist compression (2), convergence (3), backward compat (2). All mocked.** |
| **test_phase5_regression.py** | **~36** | **<1s** | **1.0: Trending structure (6) + data flow (4), Incidents structure (6) + data flow (4), cross-page integration (6), MainWindow integration (4). Qt regression.** |
| **test_e2e_scanner_reports.py** | **16** | **~5-9 min** | **1.0 Phase 5.2: NLP Scanner live pipeline (8: boot → scan → classify → findings → shutdown), AI Reports live (8: all 5 types + history + bridge lifecycle). Gated: ALMA_LIVE_TEST=1.** |
| **test_build_55a.py** | **31** | **<1s** | **5.5A: MarkdownViewer (8), tech_summary_builder (8), analyst_report_formatter (8), client_factory (4), report_schedules CRUD (3). All mocked.** |
| **test_reporting_suite.py** | **26** | **<1s** | **5.5D: compute_next_run all recurrence types (10), markdown edge cases (7), analyst formatter round-trip JSON→MD→HTML (2), get_smart_runs_with_reports JOIN + cost aggregation (4), format_next_run display (3). In-memory SQLite for JOIN tests.** |

### Config (24 files)

settings.yaml (81 + agents section + scanning_costs section + voc_report block),
redaction_patterns.json (21), 2 entity JSONs, 20 prompt templates.
6.0: +voc_analysis.txt (62 lines), +voc_synthesis.txt (103 lines).
7.0: +{temporal_context} token in 4 prompt templates (general_trend.txt,
executive_summary.txt, incident_summary.txt, voc_synthesis.txt).
9.0: +6 new VOC prompts: voc_analysis_batch.txt (63), voc_accumulator.txt (80),
voc_pattern_detector.txt (62), voc_novelty_scanner.txt (64),
voc_friction_scorer.txt (83), voc_convergence.txt (89).

### Docs (4 files)

| File | Purpose |
|------|---------|
| docs/NLP_PIPELINE_4_1_REFERENCE.md | Fallback revert procedure |
| BUILD_SPEC_5_6_QUALITY_AND_UX.md | Original 6-task spec (partial — VOC pipeline built as 6.0, T1-T6 remain for future builds) |
| **ocr_debug/audit/alma_audit.jsonl** | **1.0 Phase 1: Machine-readable codebase audit (one JSON object per line — file, widget, signal_map, design_token, architecture, test, config records)** |
| **ocr_debug/audit/alma_audit.md** | **1.0 Phase 1: Human-readable codebase documentation (architecture, design system, page inventory, widget catalog, data layer, agent pipeline, test coverage, OCR pipeline)** |

---

## 3. Build History

| Pass | Status | Key Deliverable |
|------|--------|-----------------|
| 1 | ✅ | Foundation: CSV, conversations, TRC, trending, charts, Lightdash |
| 1.5 | ✅ | Term intelligence: compounds, feedback loop, curation, velocity |
| 1.75 | ✅ | Dual-engine monitoring: Poisson/CUSUM + θ-EWMA, incidents page |
| 2.0 | ✅ | Semantic layer: concept map, embeddings, Gemini, hypothesis testing |
| 3.0 | ✅ | AI Reports system: 3 tabs, A/B, smart pipeline, entities, product gaps |
| 3.1 | ✅ | UI polish: date pickers, shadows, layman mode, chart grid |
| 4.0 | ✅ | NLP infrastructure: 8 tables, meta-analyzer, synthesis, n-gram matcher, scanner page |
| 4.1 | ✅ | CLI worker: scan_worker.py, manager, smart pipeline NLP stages, 50K cap, timers |
| Bridge V4 | ✅ | Persistent Gemini bridge: gemini_bridge.mjs, smart exit guard, streaming + legacy |
| **5.0** | **✅** | **Agentic pipeline: 10 files (~2,600 lines), 3 workers + supervisor + analyst, 7 tools, 5 new tables** |
| **5.0 Live** | **✅** | **Live integration test: 10 real Gemini calls, boot readiness gate, schema fixes, 137 total tests passing** |
| **5.1** | **✅** | **Scanner page restructure: 3-tab layout (Scanner, Costs, SubTaxonomy & Findings), 4 new widgets (~2,005 lines), page rewrite (1,293→1,040), usage tracker, settings model wiring fix** |
| **5.2** | **✅** | **Per-ticket tool call classification, model-adaptive batch sizing, crash recovery, 8 bug fixes (supervisor death spiral, context degradation, tool_call accumulation), full production scan: 849/886 tickets, 385 sub-patterns, 57 findings** |
| **Model Eval** | **✅** | **gemini-2.0-flash vs 2.5-flash: 886 tickets, 14 dimensions. 2.5-flash wins 9/14: 100% classification, 3× better taxonomy (126 vs 382 patterns), 0.948 confidence, +2 friction types, +59% findings. Default model set to 2.5-flash.** |
| **5.3** | **✅** | **TRC propagation fix verified (0 JSON-array TRCs, 127 unique TRCs vs 11 corrupted), dedup on retry (0 duplicates despite 4 requeues), quality audit fixed (3 analyst reports), supervisor false-restart prevention (_batches_since_reset grace window), confidence 0.977 avg, 446 sub-patterns, 66 within-TRC findings, 3 workers validated** |
| **5.4** | **✅** | **Input-token-aware batch sizing: dual constraint (output budget + input budget), hard cap 100→75, per-TRC avg thread length query, mixed-batch input-char accumulation. ~98 lines across 5 files. Trending Topics AI Enhancement hotfix (3 bugs). Full benchmark pending — API degraded. 76 tests passed, 0 failures.** |
| **5.5** | **✅** | **Scan UI architecture: debug mode gating (full controls vs simple card), Gemini contention prevention (blocks AI Reports + Trending during scans), job queue integration for production scans. ~217 lines across 4 UI files. Bug bash: 3 pre-existing bugs fixed (keyword suggestions dead since creation, 2 floating window popups). 14 new tests, 90 passed / 10 skipped / 0 failed.** |
| **5.6 → 6.0** | **✅** | **VOC Root Cause Analysis pipeline: two-phase Gemini pipeline (per-TRC analysis → executive synthesis), stratified sampling (friction_type or CSAT), NLP enrichment, PII redaction chain, plan preview with cost/time estimates. ~1,470 lines across 8 files (4 new, 4 modified). 20 new tests, 117 passed / 10 skipped / 0 failed.** |
| **6.1** | **✅** | **VOC refinements (bundled with 6.0 debrief). Cost model validated: 128 TRCs × ~200K input = ~$4.24 total. Sampling thresholds tuned.** |
| **6.2** | **✅** | **Bridge Health Monitor: watchdog thread (3s poll), _notify_death() queue poisoning, stall escalation (consecutive_stalls >= 3 → restart), get_stats() tracking (alive, death_count, stall_count, boot_count). Supervisor integration: bridge_alive + consecutive_stalls checks.** |
| **6.3** | **✅** | **Canary Probe System: pre-scan bridge.probe() latency measurement, probe_history table (7-day rolling), adaptive call timeout (P95-based formula, clamped [45s, 600s]), rate governor floor (P50 × 3.0). Model health context injected into VOC synthesis.** |
| **7.0** | **✅** | **Bridge-Powered Report Engine: ReportBridgeClient (187 lines, drop-in swap), ReportOrchestrator (252 lines, 3-bridge ThreadPool), VOC Phase 1 parallelized (42 min → ~14 min), windowed temporal context (weekly data blocks in 4 prompts), data-grounded follow-up chat (TRC/date/sample detection → live DB enrichment), chat_widget.py rewritten (366→530 lines), lazy lifecycle management. Standard reports 25s → 8s (3× speedup).** |
| **8.5** | **✅** | **ReportOrchestrator Resilience: canary probes on boot (adaptive floor + timeout from P50/P95), stall escalation + bridge restart (3 consecutive → restart), retry budget with requeue (3× stall, 6× quota, 1× other), queue-pull dispatch (replaces round-robin, eliminates head-of-line blocking), two-tier rate governor recovery (10% at 5 successes, 20-30% at 10), 4 bridges default. report_orchestrator.py rewritten (~370 lines), rate_governor.py modified (~15 lines). 22 new tests, 155/155 regression passed. E2E: 127/127 TRC resilience confirmed.** |
| **9.0** | **✅** | **Multi-Perspective VOC Pipeline: VOCBatchPacker (127 TRCs → ~8 batches via greedy first-fit bin-packing), pipelined priority dispatch (PriorityQueue, on_complete callbacks, drain_event), 8-round accumulator evidence ledger, 3 specialists (pattern detector, novelty scanner, friction scorer), convergence → 7-section executive report. run_resilient() for critical calls. Graceful degradation at every level. _send_lock for thread-safe bridge stdin. 6 new prompt templates. voc_builder.py rewritten (~1,330 lines). 7 bugs found/fixed during 4 E2E runs (2 critical, 3 high, 1 medium, 1 low). 33 new tests, 192/192 regression passed. E2E validated: 19-24 min wall time, 40-53k char reports, 83-86% TRC coverage.** |
| **1.0** | **✅** | **Full v2 UI rebuild + pipeline hardening. 5-phase, multi-session effort. Phase 1: Living audit deliverable (alma_audit.jsonl + alma_audit.md — machine-readable + human-readable codebase documentation). Phase 2: TRC Analytics rebuild (5 tabs on AnalysisPageBase, v2 design tokens, KPI accent cards, SharedFilterBar). Phase 2.5: NLP Scanner migration into TRC Analytics Tab 2 (full scan lifecycle, config, history). Phase 3: Trending Topics v2 migration (QWidget → AnalysisPageBase, 4 tabs: Overview/Deep Dive/Hypothesis/Reports, KPICardRow, FilterChipBar, backward-compat properties). Phase 4: Incidents v2 migration (same pattern, 4 tabs: Overview/Open Incidents/θ EWMA/Reports). Phase 5: cross-page regression (36 new tests), sidebar collapse fix. Phase 5.2: 16 live E2E tests (NLP Scanner full pipeline + AI Report all 5 types, gated on ALMA_LIVE_TEST=1). Phase 5.3: analyst agent hardening — surface analyst reports in drilldown, full novelty validation (removed [:30] cap, smart batching, verdict feedback to classifications: DUPLICATE→is_novel=0, MERGE advisory), pipeline reorder (pattern merge after meta-analyzer). Phase 5.4: classification pipeline hardening — ticket ID boundary guard (reject overflow classifications), partial parse completeness check + retry (80% threshold, stagnation detection). Expected: 95.6% → ~99.1% coverage, 96.3% → ~100% TRC accuracy. New DB columns: novelty_verdict, novelty_match on nlp_ticket_classifications. ~228+ tests, 0 regressions.** |
| **5.5A** | **✅** | **Reporting Foundation: 4 new files + 1 modified + 1 test = ~720 LOC production, 31 tests. MarkdownViewer widget (~280 lines, QTextBrowser + regex markdown→HTML, Alma CSS with theme tokens, zero external deps). tech_summary_builder (~260 lines, 5-table process metrics aggregation: gemini_usage/nlp_scan_runs/nlp_batches/scan_events/probe_history → formatted markdown). analyst_report_formatter (~230 lines, analyst_reports JSON→markdown: synthesis/audit/novelty/merge with structured sections). client_factory (~80 lines, single build_gemini_client(use_bridge) replacing 3 diverged copies in ai_reports/ab_compare/smart_pipeline — use_bridge=True → ReportBridgeClient with ImportError fallback → GeminiClient, always checks pat_store API key). report_schedules DB table (13 cols, timezone-aware, 5 CRUD methods). Pure additions — zero existing behavior changes. Foundation for 5.5B/C/D.** |
| **5.5B** | **✅** | **AI Reports & A/B Compare Pipeline Overhaul: 2 new multi-phase pipelines + 2 major page overhauls. ai_report_pipeline.py (~170 lines): data assembly → analyst/tech metadata → Gemini generation → final assembly, graceful degradation to data-only markdown. ab_report_pipeline.py (~185 lines): dual dataset stats + comparison → tech metadata → Gemini narrative, falls back to stats-only table. ai_reports.py overhauled: PipelineWorker replaces ReportWorker, MarkdownViewer replaces QTextEdit, collapsible Analyst Reports + Technical Summary sections, shared client factory, report history detail renders markdown. ab_compare.py overhauled: ABPipelineWorker, MarkdownViewer, collapsible Tech Summary, +Save .md + Save to History + Report History (previously missing entirely). markdown_viewer.py: +public md_to_html() for DrilldownPanel. 129/129 tests, 18 skipped, 0 failures.** |
| **5.5C** | **✅** | **Smart Reporting & Scheduling Overhaul: ScheduleManager service (schedule_manager.py, 243 lines — DB-backed 60s QTimer poll, 4 recurrence types: daily/weekly/biweekly/monthly, timezone-aware via zoneinfo, compute_next_run() pure function, ISO lexicographic comparison, 5 US zones + UTC). Smart Reporting page rebuilt (657→1,072 lines): decomposed _build_ui() into 5 focused builders, complete schedule CRUD (inline form with dynamic day picker, day-of-month capped at 28, enable/disable/delete), MarkdownViewer report viewer with collapsible Analyst Reports + Technical Summary sub-sections, 6-column clickable history table (Started/Status/Tickets/Duration/Source/Report), shared _build_pipeline_config() for manual + scheduled runs. main_window.py +8 lines: ScheduleManager instantiation + wiring with try/except guard. ai_reports.py bug fix: restored _scan_blocked + set_scan_blocking() lost in 5.5B overhaul. 149/149 tests, 2 pre-existing, 18 skipped, 0 new failures.** |
| **5.5D** | **✅** | **Polish, Integration & Testing — completes Phase 5.5 Reporting Suite Overhaul. 7 changes across 6 files: (1) Trending Topics hypothesis synthesis: QTextBrowser.setPlainText() → MarkdownViewer.set_markdown() (−6 lines). (2) TRC Analytics analyst report renderer: 127-line custom HTML builder → 22-line analyst_report_formatter + md_to_html() pipeline (−105 lines). (3) db_manager +get_smart_runs_with_reports(): LEFT JOIN smart_report_runs + analysis_reports + correlated gemini_usage cost subquery (+18 lines). (4) Smart Reporting history: 6→7 columns with $X.XX cost display (+15 lines). (5) Smart Reporting export action bar: Copy / Save .md / Save .html buttons below report viewer (+50 lines). (6) AI Reports +Save .html button with full Alma-branded HTML document export (+35 lines). (7) test_reporting_suite.py: 26 tests across 5 classes (compute_next_run 10, markdown edge cases 7, analyst formatter round-trip 2, JOIN query 4, format_next_run 3). 171/171 passed, 1 pre-existing, 0 regressions.** |
| **P0** | **✅** | **Settings Migration: settings_manager.py (~130 lines, singleton load/save/get/set). Auto-migrates config/settings.yaml → data/settings.yaml with .migrated breadcrumb. All 46 call sites project-wide migrated to centralized settings. Foundation for P1-P5. 16 tests.** |
| **P1** | **✅** | **Claude Client + Model Registry: model_registry.py (~190 lines, ModelConfig dataclass + singleton, model_changed Signal, persists to settings.yaml ai section). claude_client.py (~240 lines, Anthropic API, duck-typed .generate(), PII redaction, streaming). client_factory.py upgraded: build_client_for_model() dispatches Gemini/Claude based on active model, build_gemini_client() kept as backward-compat shim. settings_page.py restructured 2→4 tabs (AI Provider, Integrations, Updates, Display). All pipeline code is now provider-agnostic via factory. 30 tests.** |
| **P2** | **✅** | **Auto-Update System: update_checker.py (~125 lines, GitHub Releases API, private repo PAT, update_available/up_to_date Signals). updater.py (~295 lines, stage-and-apply pattern: download zip → SHA-256 verify → extract to _update_staging/ → swap src/+config/ on next launch, rollback on failure). schema_migrator.py (~105 lines, numbered SQL migration runner, schema_migrations table, runs on every app start). migrations/001_initial_baseline.sql. Settings Updates tab: Check + Install + Restart flow. 30 tests.** |
| **P3** | **✅** | **Zendesk Source Monitor: zendesk_client.py (~200 lines, API v2, incremental cursor export, TRC extraction: subject/custom field/tag prefix). zendesk_monitor.py (~260 lines, QTimer polling min 30s, TRC spike detection 50%+ or ≥3 new, dedup via _seen_ids). source_monitor_page.py (3-tab: Live Feed with ticket cards, TRC Spikes table, Connection config). main_window.py: PAGE_SOURCE_MONITOR=8, LIVE badge. Smoke-tested against live Zendesk (1,778 tickets). 65 tests.** |
| **P3.5** | **✅** | **Source Abstraction + Data Warehouse + Watchlist Engine: source_types.py (~80 lines, SourceConfig/SourceClient ABC/SourceMonitor ABC — adding a new source requires 1 client + 1 monitor + 1 UI section). source_warehouse.py (~250 lines, hot tier in-memory with PHI + cold tier SQLite NO PHI, hybrid n-gram→LLM classification, hourly/daily rollups, 7-day prune). watchlist_engine.py (~350 lines, 4 rule types: keyword/entity/volume/compound, 5 pre-populated system rules, EWMA learning loop: confirm↑ dismiss↓ floor 0.1 ceiling 0.95, gate < 0.3, cooldown, few-shot LLM triage for compound rules). migrations/002_source_warehouse.sql (~15 tables: source_events, rollups, watchlist_rules/alerts/examples). Source Monitor expanded to 5 tabs (+Alerts with severity badges + Confirm/Dismiss EWMA feedback, +Watchlist with CRUD dialog). scan_orchestrator.py: +source filter parameter. 80 tests.** |
| **P4** | **✅** | **Guru KB Integration: guru_client.py (~150 lines, API v1, human-gated update_card()). guru_friction_pipeline.py (~250 lines, sync_articles() with SHA-256 content_hash change detection, analyze_coverage() keyword + LLM scoring, get_gap_report() LEFT JOIN sub_patterns vs coverage worst-first). guru_content_pipeline.py (~200 lines, propose_rewrite()/propose_new_article() → guru_content_drafts, approve_and_push() human-gated only → API + baseline recording, fallback templates). guru_effectiveness.py (~150 lines, record_baseline() on push, measure_effectiveness() after 14 days, pre/post delta, pure-Python Poisson CDF log-space, p < 0.05 significance). guru_page.py (~600 lines, 5 tabs: Cards with friction bars, Gap Analysis worst-first, Drafts & Diff two-column, Effectiveness with significance badges, Connection). migrations/003_guru_tables.sql (guru_articles/friction_coverage/effectiveness/content_drafts). Universal join key: friction_type from sub_patterns.pattern_name. 48 tests.** |
| **P5** | **✅** | **GitHub Wiring + Settings UI: scripts/make_release.py (~165 lines, packages src/config/migrations/main.py into zip, SHA256SUMS manifest). Settings Updates tab enhanced: Install Now button with progress bar, Restart Now after staging, last-checked timestamp persistence. 21 tests.** |
| 1.75a | 📋 SPECCED | CUSUM corrections: k-factor, DOW baselines, persistent accumulators, two-sided, low-volume guards |

---

## 4. Active Specs

| Spec | Status | Description |
|------|--------|-------------|
| BUILD_SPEC_5_0_AGENTIC_PIPELINE.md | ✅ BUILT + LIVE TESTED | 3 Gemini workers + supervisor + analyst. 137 tests (unit + stress + live + system). |
| BUILD_SPEC_STREAMING_BRIDGE_COMPLETE.md | ✅ BUILT | Bridge V4 + Python wrapper. Wired into 5.0. |
| BUILD_SPEC_5_1_SCANNER_PAGE_RESTRUCTURE.md | ✅ BUILT | 3-tab Scanner page: live monitor, cost tracking, taxonomy drilldown. 4 new widgets + page rewrite. Settings model wiring fixed. |
| BUILD_SPEC_5_2 (debrief only) | ✅ BUILT | Per-ticket tool calls, model-adaptive batching, 8 bug fixes, production-validated 849/886. |
| BUILD_5_3_BENCHMARK (debrief) | ✅ BUILT + VALIDATED | TRC fix verified (127 unique TRCs), dedup fix, quality audit fix, supervisor grace window, 886/886 classified, 3 analyst reports, confidence 0.977. |
| BUILD_5_4 (debrief) | ✅ BUILT | Input-token-aware batch sizing: dual constraint, hard cap 75, per-TRC thread length query, mixed-batch input-char accumulation. ~98 lines/5 files. Trending Topics hotfix (3 bugs). Full benchmark pending API stability. |
| BUILD_5_5 (debrief) | ✅ BUILT | Scan UI architecture: debug mode gating, simple production scan card, Gemini contention prevention, job queue integration. ~217 lines/4 files. Bug bash: 3 pre-existing bugs fixed. 14 new tests. |
| BUILD_SPEC_5_6_QUALITY_AND_UX.md | ✅ BUILT (partial — VOC pipeline) | Original 6-task spec. Build 6.0 implemented VOC Root Cause Analysis (not in original spec). Remaining tasks (T1-T6: prompt eval, findings dedup, incremental scans, export, embeddings, confidence calibration) available for future builds. |
| BUILD_6_0 (debrief) | ✅ BUILT | VOC Root Cause Analysis: two-phase Gemini pipeline, stratified sampling, NLP enrichment, PII redaction, plan preview. ~1,470 lines/8 files. 20 new tests. |
| BUILD_6.1 (debrief) | ✅ BUILT | VOC refinements: cost model validated ($4.24 for 128 TRCs), sampling thresholds tuned. |
| BUILD_6.2 (debrief) | ✅ BUILT | Bridge Health Monitor: watchdog thread, queue poisoning, stall escalation, supervisor integration. |
| BUILD_6.3 (debrief) | ✅ BUILT | Canary Probe System: pre-scan latency probes, adaptive timeouts, rate governor floor, model health context for VOC. |
| BUILD_7.0 (debrief) | ✅ BUILT | Bridge-Powered Report Engine: ReportBridgeClient + ReportOrchestrator, VOC parallelization, temporal context, data-grounded chat, lifecycle management. |
| BUILD_8.5 (debrief) | ✅ BUILT | ReportOrchestrator Resilience: canary probes, stall escalation, retry budget, queue-pull dispatch, two-tier rate governor recovery, 4 bridges. report_orchestrator.py rewritten. 22 new tests. E2E confirmed. |
| BUILD_9.0 (debrief) | ✅ BUILT + E2E VALIDATED | Multi-Perspective VOC Pipeline: VOCBatchPacker, pipelined priority dispatch, accumulator evidence ledger, 3 specialists, convergence → 7-section report. voc_builder.py rewritten (~1,330 lines). 6 new prompts. 7 bugs fixed. 33 new tests. 4 E2E runs (19-24 min, 40-53k char reports). |
| BUILD_1.0 (debrief) | ✅ BUILT | Full v2 UI rebuild (5 phases): all 3 analysis pages on AnalysisPageBase, living audit, NLP Scanner migration, 16 live E2E tests, analyst agent hardening (full novelty + verdict feedback + pipeline reorder), classification pipeline hardening (boundary guard + partial parse retry). ~228+ tests. |
| BUILD_5.5A (debrief) | ✅ BUILT | Reporting Foundation: MarkdownViewer, tech_summary_builder, analyst_report_formatter, client_factory, report_schedules schema. 4 new files + 1 modified (~720 LOC), 31 tests. Zero behavior changes — pure dependency layer for 5.5B/C/D. |
| BUILD_5.5B (debrief) | ✅ BUILT | AI Reports & A/B Compare Pipeline Overhaul: 2 new pipelines (ai_report_pipeline, ab_report_pipeline), both pages overhauled with MarkdownViewer + collapsible sections + shared client factory. A/B Compare gains full report history. 129/129 tests. |
| BUILD_5.5C (debrief) | ✅ BUILT | Smart Reporting & Scheduling Overhaul: ScheduleManager service (DB-backed, 4 recurrence types, timezone-aware), Smart Reporting page rebuilt (schedule CRUD, MarkdownViewer viewer, collapsible summaries, 6-column history). AI Reports scan blocking fix. 149/149 tests. |
| BUILD_5.5D (debrief) | ✅ BUILT | Polish, Integration & Testing — completes Phase 5.5. Markdown in Trending hypothesis + TRC Analytics analyst reports. JOIN query with cost. 7-column history. Export bar (Copy/Save .md/Save .html). Save .html on AI Reports. 26 new tests. 171/171. |
| PLATFORM P0 (debrief) | ✅ BUILT | Settings Migration: centralized settings_manager, 46 call sites migrated. 16 tests. |
| PLATFORM P1 (debrief) | ✅ BUILT | Claude Client + Model Registry: multi-provider factory, claude_client.py, model_registry.py, 4-tab settings. 30 tests. |
| PLATFORM P2 (debrief) | ✅ BUILT | Auto-Update: GitHub Releases checker, stage-and-apply updater, schema migrator, migration runner. 30 tests. |
| PLATFORM P3 (debrief) | ✅ BUILT | Zendesk Source Monitor: live API polling, TRC spike detection, 3-tab page. Live-tested 1,778 tickets. 65 tests. |
| PLATFORM P3.5 (debrief) | ✅ BUILT | Source Abstraction + Data Warehouse + Watchlist: hot/cold tier, hybrid n-gram→LLM classification, EWMA learning engine, 5 system rules, 5-tab UI. 80 tests. |
| PLATFORM P4 (debrief) | ✅ BUILT | Guru KB Integration: friction pipeline, gap analysis, content generation (human-gated), effectiveness tracking (Poisson). 5-tab Guru page. 48 tests. |
| PLATFORM P5 (debrief) | ✅ BUILT | GitHub Wiring: release packager, install/restart flow, last-checked persistence. 21 tests. |
| BUILD_SPEC_4_1_HOTFIX_TRC_PROPAGATION.md | ✅ APPLIED | Fixed mixed-batch TRC storage → per-ticket TRC. Taxonomy now populates correctly. |
| BUILD_SPEC_1_75a_CUSUM_CORRECTIONS.md | 📋 SPECCED | 5 CUSUM fixes. ~310 lines across 5 files. |
| PHASE_5_CUSTOMER_ENTITY.md | 📋 SKETCH | Customer entity model. Blocked by Lightdash + InfoSec. |

---

## 5. Test Results (Mar 11, 2026 — session 13)

### Pipeline Unit + Stress (74 passed, 10 skipped, 0 failed)

```
test_pipeline_full.py — 51 tests
test_pipeline_stress.py — 23 tests
test_pipeline_live.py — 10 tests (skipped: requires Gemini CLI)
```

### Live Integration Test (10 passed, 0 failed)

```
test_pipeline_live.py — 10 tests, ~34.5s (17.4 tickets/min)
  Bridge boot + ping with model override:    PASS
  10-ticket classification (real Gemini):     10/10 PASS
  Anomaly detection + sentiment detection:   PASS
```

### UI Widget Smoke Tests (15 passed, 0 failed)

```
5.1 widget imports and initialization — all 15 pass
```

### System Tests (36 passed, 28 failed — all pre-existing)

```
test_full_system.py — 36 passed, 28 failed
  28 failures: all "no such table" from empty test DB (pre-existing)
  NO REGRESSIONS from 5.3 changes
```

### 5.3 Fix Verification (via production benchmark — bench_53.py)

```
Fix 1 — TRC propagation:          FIXED (0 JSON-array TRCs, was 104; 127 unique TRCs, was 11)
Fix 2 — Dedup on retry:           FIXED (0 duplicate classifications despite 4 batch requeues)
Fix 3 — Quality audit column:     FIXED (8,318 byte audit report stored; synthesis 22KB, novelty 7KB)
Fix 4 — Sub-pattern TRC cleanup:  FIXED (0 JSON-array values in sub_patterns table)
Fix 5 — Supervisor false restart: FIXED (2 restarts, both legitimate low parse rate; no false positives after context resets)
```

### 5.2 Bug Fix Verification (via production scan progression)

```
Bug 1 — Supervisor death spiral:     FIXED (0 restarts on final scan, was 16)
Bug 2 — Reset didn't clear metrics:  FIXED (no restart cascades)
Bug 3 — UsageTracker thread safety:  FIXED (no SQLite thread warnings)
Bug 4 — Context degradation:         FIXED (proactive reset every 5 batches)
Bug 5 — StreamParser missing events: FIXED (fallback re-feed catches 100%)
Bug 6 — tools_called accumulation:   FIXED (849/886 vs previous 300/886 ceiling)
Bug 7 — BatchPacker conservative:    KNOWN (37 tickets unassigned, 95.6% coverage)
Bug 8 — Quality audit column:        KNOWN (analyst task non-blocking, others succeed)
```

### 5.5 Build Tests (14 passed, 0 failed — NEW)

```
test_build_55.py — 14 tests
  scan_active_changed signal exists + emits correctly:           PASS (2 tests)
  AI Reports scan blocking (disable/enable + generate guard):   PASS (2 tests)
  Trending scan blocking (flag + AI enhancements short-circuit): PASS (2 tests)
  Scanner debug mode gating (hide/show widgets):                 PASS (2 tests)
  Simple scan card widgets present + defaults:                   PASS (1 test)
  is_scan_active accessor:                                       PASS (1 test)
  Keyword key fix (rising/cooling vs old broken keys):           PASS (1 test)
  Smoothing/keyword panel no self.show():                        PASS (2 tests)
  Main window integration (signal wiring + debug propagation):   PASS (1 test)
```

### 6.0 VOC Builder Tests (20 passed, 0 failed — NEW)

```
test_voc_builder.py — 20 tests
  Planning (plan structure, TRC filter):                         PASS (2 tests)
  Sampling (below/above threshold, CSAT strat, NLP strat):      PASS (4 tests)
  JSONL packaging (truncation, PII redaction, NLP enrichment):   PASS (3 tests)
  NLP aggregation (query, no-data, context formatting):          PASS (3 tests)
  Prompts (no unreplaced tokens, synthesis includes all TRCs):   PASS (2 tests)
  Cost estimation (reasonable range):                            PASS (1 test)
  Cooperative cancellation (between TRCs):                       PASS (1 test)
  E2E pipeline (mocked Gemini, 3 calls verified):                PASS (1 test)
  UI integration (VOC in combo, worker class, job factory):      PASS (3 tests)
```

### 8.5 ReportOrchestrator Resilience Tests (22 passed, 0 failed — NEW)

```
test_report_orchestrator.py — 22 tests (all mock bridges, no live Gemini)

  TestCanaryProbes (4 tests):
    Adaptive floor from 4000ms probes (floor=15.0):                  PASS
    High-latency floor from 8000ms probes (floor=24.0):              PASS
    Adaptive timeout from 5000ms probes (timeout=188):               PASS
    All probes fail → defaults preserved:                            PASS

  TestStallEscalation (2 tests):
    3 consecutive stalls → bridge.restart() called:                  PASS
    Stall-success-stall-success → no escalation (reset works):       PASS

  TestRetryBudget (3 tests):
    Stall → requeue → succeeds on retry:                             PASS
    Budget exhausted after 1 + 3 retries = 4 calls:                  PASS
    Constants: STALL=3, QUOTA=6, OTHER=1:                            PASS

  TestQueuePullDispatch (2 tests):
    Fast bridge handles more tasks than slow bridge (no HOL):        PASS
    20 tasks across 3 bridges → all 20 complete:                     PASS

  TestRateGovernorRecovery (7 tests):
    10 successes (>120s): 40→36→25.2 (tier 1 + tier 2 at 30%):      PASS
    5 successes: 40→36 (standard 10%):                               PASS
    Tightening clamped to probe floor:                               PASS
    Rate limit doubles interval (20→40):                             PASS
    Backoff capped at 120 (80→120 not 160):                          PASS
    10 successes (<120s): 40→36→28.8 (tier 2 at 20%):               PASS
    Full spiral: 20→40→36→25.2→22.68→15.88 (near floor):            PASS

  TestBackwardCompatibleAPI (4 tests):
    run_parallel(tasks) → dict (same interface as 7.0):              PASS
    progress_cb fires for each completed task:                       PASS
    cancel() mid-run → fewer than all tasks complete:                PASS
    get_stats() includes adaptive_timeout field:                     PASS
```

### 9.0 Multi-Perspective VOC Pipeline Tests (33 passed, 0 failed — NEW)

```
test_voc_build9.py — 33 tests (all mocked, no live Gemini)

  TestSendRawLocking (1 test):
    Concurrent stdin writes serialized via _send_lock:               PASS

  TestPriorityQueueDispatch (4 tests):
    Priority ordering (0 before 1):                                  PASS
    Sequence tie-breaking (same priority):                           PASS
    Dynamic task injection mid-run:                                  PASS
    drain_event keep-alive:                                          PASS

  TestOnCompleteCallback (2 tests):
    Callback fires per task:                                         PASS
    Receives correct result:                                         PASS

  TestVOCBatchPacker (6 tests):
    Small TRCs packed into batches:                                  PASS
    Large TRCs chunked (intra-TRC split):                            PASS
    Mixed sizes packed correctly:                                    PASS
    75% headroom enforced:                                           PASS
    chunk_info populated:                                            PASS
    Empty input handled:                                             PASS

  TestRunResilient (2 tests):
    Success path returns result:                                     PASS
    Raises RuntimeError on exhausted retries:                        PASS

  TestStatContextCaching (2 tests):
    Cache hit bypasses report_builder:                               PASS
    Cache cleared per run:                                           PASS

  TestBatchResponseParsing (4 tests):
    All TRCs extracted from delimited response:                      PASS
    Missing TRCs detected:                                           PASS
    Single-TRC batch (no delimiter):                                 PASS
    Empty response returns all missing:                              PASS

  TestOversizedTRCChunking (2 tests):
    Chunks merged with demarcation:                                  PASS
    Single chunk passthrough:                                        PASS

  TestAccumulatorParsing (3 tests):
    Both sections (findings + synthesis) extracted:                   PASS
    Malformed response fallback:                                     PASS
    Empty response handled:                                          PASS

  TestSpecialistCompression (2 tests):
    Low-volume TRCs dropped:                                         PASS
    Truncation applied:                                              PASS

  TestConvergence (3 tests):
    Graceful degradation (missing specialists):                      PASS
    Specialist compression applied:                                  PASS
    Empty input handled:                                             PASS

  TestBackwardCompatibility (2 tests):
    run() returns expected keys:                                     PASS
    run_parallel() backward compatible:                              PASS
```

### 9.0 E2E Validation (4 live runs against 888-ticket / 127-TRC dataset)

```
Run #1 (b141c92): CRASHED — parser regex \S+ failed on TRC names with spaces.
                   3 bugs found (parser, unicode, progress overflow)
Run #2 (b45466d): CRASHED — 127/127 parsed, 3/3 specialists, but convergence
                   exhausted retries (stalls) + stdout encoding crash.
                   2 bugs found (accumulator break→continue, stdout cp1252)
Run #3 (ba7fa2c): ✅ PASSED — 109/127 TRCs, 1 accum round, 3/3 specialists,
                   convergence succeeded (1 retry). 19m 34s. 53,020 char report.
Run #4 (b9ad5fb): ✅ PASSED — 105/127 TRCs, 3 accum rounds, 2/3 specialists,
                   convergence succeeded (1st try). 23m 44s. 41,035 char report.
                   First run with report persisted to data/reports/.
```

### 1.0 Build Tests (NEW)

```
Phase 5 Cross-Page Regression (tests/test_phase5_regression.py — ~36 tests)
  Trending Topics: structure (6), data flow (4)
  Incidents: structure (6), data flow (4)
  Cross-page integration (6)
  MainWindow integration (4)

Phase 5.2 Live E2E (tests/test_e2e_scanner_reports.py — 16 tests, gated)
  NLP Scanner full pipeline (8): boot → scan → classify → findings → shutdown
  AI Report all 5 types (8): boot → build data → 5 reports → history → shutdown
  Gate: ALMA_LIVE_TEST=1 required

Phase 5.3 Analyst Agent Hardening (in test_pipeline_full.py — 8 tests)
  Novelty batch splitting: 0 novels, under limit, over limit
  Verdict application: DUPLICATE, MERGE, VALID
  novelty_verdict column migration
  Analyst report format adapter

Phase 5.4 Classification Pipeline Hardening (in test_pipeline_full.py — 8 tests)
  Boundary guard: reject overflow, allow valid, skip empty map
  Partial parse: detected, skips small batch, skips near-complete,
    stagnation detection, default _prev_classified=-1
```

### 5.5A Reporting Foundation Tests (31 passed, 0 failed — NEW)

```
test_build_55a.py — 31 tests

  TestMarkdownViewer (8 tests):
    Headings, bold/italic, code blocks, tables, lists,
    blockquotes, links, empty input

  TestTechSummaryBuilder (8 tests):
    Full summary from populated scan, empty scan, missing tables,
    markdown formatting, number formatting, duration calculation

  TestAnalystReportFormatter (8 tests):
    Synthesis parsing, audit parsing, novelty parsing, merge parsing,
    malformed JSON fallback, empty reports, get_latest_analyst_summary

  TestClientFactory (4 tests):
    use_bridge=False → GeminiClient, use_bridge=True → ReportBridgeClient,
    ImportError fallback → GeminiClient, pat_store API key loaded

  TestReportSchedules (3 tests):
    save + get round-trip, update + delete, update_schedule_last_run
```

### 5.5B Regression: 129 passed, 18 skipped, 0 failures

### 5.5C Regression: 149 passed, 18 skipped, 2 pre-existing failures, 0 new failures

### 5.5D Regression: 171 passed, 18 skipped, 1 pre-existing failure, 0 new regressions

```
test_reporting_suite.py — 26 new tests (5 classes)
  TestComputeNextRun (10): daily future/past, weekly same/diff day,
    biweekly, monthly future/past/december rollover, unknown fallback
  TestMarkdownEdgeCases (7): empty, whitespace, paragraph, heading,
    bold, table, list — validates md_to_html() doesn't crash
  TestAnalystFormatterRoundTrip (2): full JSON→MD→HTML pipeline,
    empty reports
  TestSmartRunsJoinQuery (4): report summary linkage, null report,
    cost aggregation (in-window vs out-of-window), desc ordering
  TestFormatNextRun (3): valid ISO, empty string, invalid fallback
```

### Platform Build P0-P5 Tests (~250 new across 11 test files, 598 total passing)

```
P0 Settings Migration (16 tests):
  Load/save round-trip, section get/set, auto-migration, breadcrumb

P1 Claude Client + Model Registry (30 tests):
  ModelConfig serialization, registry CRUD, active model switching,
  model_changed Signal, claude_client .generate(), PII redaction,
  factory dispatch Gemini/Claude, backward-compat shim

P2 Auto-Update (30 tests):
  Version comparison, GitHub API mock, download+verify+extract,
  stage-and-apply, rollback on failure, migration ordering,
  schema_migrations tracking, idempotent re-run

P3 Zendesk Source Monitor (65 tests):
  API client auth, cursor pagination, TRC extraction (3 modes),
  spike detection thresholds, dedup, Signal emissions,
  connection test, monitor lifecycle (start/pause/resume/stop)

P3.5 Source Abstraction + Warehouse + Watchlist (80 tests):
  SourceClient/SourceMonitor ABC compliance, hot/cold tier ingest,
  n-gram classification, LLM fallback, rollup accuracy,
  watchlist rule CRUD, EWMA confidence update math,
  gate threshold, cooldown enforcement, alert lifecycle,
  few-shot prompt construction, compound rule evaluation

P4 Guru KB Integration (48 tests):
  API client auth, sync with content_hash dedup, coverage scoring,
  gap report ordering, content generation (rewrite + new article),
  human-gate enforcement, effectiveness baseline/measurement,
  Poisson CDF accuracy, significance threshold, delta calculation

P5 GitHub Wiring (21 tests):
  Release packaging, SHA256 manifest, version detection,
  install flow, progress Signal, restart trigger, timestamp persistence
```

### Combined: 598 passed, 1 pre-existing failure, skips for live/gated tests, 0 regressions

### Pre-1.0 Feature Test Status

**Tested (~12 core engines/pipelines + 1.0 + 5.5 + P0-P5 coverage) | Remaining: 3 P0 + 5 P1 + 7 P2 = 15 user-facing features**

#### ✅ Tested (unit + E2E coverage exists)

| Feature | Coverage | Tests |
|---------|----------|-------|
| VOC Pipeline (batched analysis, accumulator, specialists, convergence) | Strong | 33 unit + 2 passing E2E |
| Batch parser (multi-TRC response parsing) | Strong | 4 unit tests |
| VOCBatchPacker (bin-packing) | Strong | 6 unit tests |
| Report Orchestrator (priority queue, parallel dispatch) | Strong | Unit + E2E |
| Rate Governor (interval management, recovery) | Moderate | Unit tests |
| GeminiBridge (subprocess lifecycle, stall detection) | Moderate | E2E validated |
| CSV Ingestion (column mapping, conversation rebuild) | Moderate | Integration test |
| CSV Reformatter (schema mapping) | Moderate | Unit test |
| Theta Engine (EWMA baselines, z-scores) | Strong | Unit tests |
| Incident Engine (Poisson/CUSUM, daily/hourly counts) | Strong | Unit tests |
| Trending Engine (TF-IDF, sentiment, bucketing) | Strong | Unit tests |
| Concept Map (domain concepts, normalization) | Moderate | Unit tests |
| **NLP Scanner (full pipeline, boot → classify → findings)** | **Strong** | **8 live E2E (1.0 Phase 5.2)** |
| **AI Report generation (all 5 canned types + history)** | **Strong** | **8 live E2E (1.0 Phase 5.2)** |
| **TRC Analytics page (5 tabs, KPI, filter bar)** | **Moderate** | **Phase 5 regression (36 tests)** |
| **Trending Topics page (4 tabs, AnalysisPageBase)** | **Moderate** | **Phase 5 regression (structure + data flow)** |
| **Incidents page (4 tabs, AnalysisPageBase)** | **Moderate** | **Phase 5 regression (structure + data flow)** |
| **Date picker sync across pages** | **Moderate** | **Phase 5 cross-page integration tests** |
| **Analyst agent (batched novelty, verdict feedback)** | **Moderate** | **8 unit tests (1.0 Phase 5.3)** |
| **Boundary guard + partial parse recovery** | **Moderate** | **8 unit tests (1.0 Phase 5.4)** |
| Full regression suite | ~228+ tests | 0 failures |

#### ❌ P0 — Must test before UAT (user will hit these immediately)

| # | Feature | Page/File | Why P0 | Status |
|---|---------|-----------|--------|--------|
| 1 | Drill-down chat (follow-up Q&A on AI reports) | ai_reports.py, chat_widget.py | Core user workflow — generate report → ask questions | ☐ |
| 2 | AI Report generation (all 5 canned report types) | ai_reports.py, report_builder.py | Users will generate these on day 1 | ☑ 1.0 Phase 5.2 |
| 3 | Keyword/cluster smoothing (AI-powered relabeling) | trending_topics.py, smoothing_panel.py | Specifically called out | ☐ |
| 4 | CSV import → full analysis flow (end-to-end) | conversation_search.py → all pages | Entire first-run experience | ☐ |
| 5 | Settings persistence (Gemini config, API keys, prefs) | settings_page.py | If settings don't save, nothing works after restart | ☑ P0 (centralized settings_manager, 46 call sites) + P1 (4-tab settings) |
| 6 | Date picker sync across pages | main_window.py, all analysis pages | Users change dates on one page, expect others to follow | ☑ 1.0 Phase 5 |
| 7 | Conversation search + thread viewer | conversation_search.py | Primary data exploration tool | ☐ |
| 8 | TRC Analytics dashboard (KPI cards, charts, metrics) | trc_analytics.py | First thing users see after import | ☑ 1.0 Phase 2+5 |

#### ❌ P1 — Should test before UAT (will surface in first week)

| # | Feature | Page/File | Why P1 | Status |
|---|---------|-----------|--------|--------|
| 9 | NLP Scanner (agentic scan, batch progress, findings) | nlp_scanner_page.py, scan_orchestrator.py | Complex multi-agent pipeline, never E2E tested | ☑ 1.0 Phase 5.2 |
| 10 | Incident management (flag workflow: open → ack → close) | incidents_page.py | Users need to triage flags | ☑ 1.0 Phase 4+5 (UI rebuilt) |
| 11 | Intervention timeline (manual event markers) | intervention_dialog.py | Key for "what changed" analysis | ☐ |
| 12 | A/B Compare (side-by-side dataset comparison) | ab_compare.py | Pre/post analysis is a core use case | ☐ |
| 13 | Report history (view/delete past reports) | report_history.py | Accumulates over time, must work | ☐ |
| 14 | Prompt editor (create/edit custom prompts) | prompt_editor.py | Power users will customize immediately | ☐ |
| 15 | Term Manager (approve/suppress/alias terms) | term_manager_dialog.py | Noise control for trending analysis | ☐ |
| 16 | Lightdash API pull (PAT auth, dataset selection) | settings_page.py, csv_ingestion.py | Production data source | ☐ |
| 17 | PII redaction validation (verify on real data) | gemini_client.py | HIPAA compliance — must verify | ☑ P1 (claude_client PII redaction tests + existing Gemini PII chain) |

#### ❌ P2 — Nice to test before UAT (can fix in first sprint)

| # | Feature | Page/File | Why P2 | Status |
|---|---------|-----------|--------|--------|
| 18 | Smart Reporting (automated pipeline runner) | smart_reporting.py | Convenience feature, manual flow works | ☑ 5.5C (full overhaul + persistent scheduling) |
| 19 | Export to Google Drive | settings_page.py, smart_pipeline.py | Optional integration | ☐ |
| 20 | Layman mode toggle (simplified terminology) | All pages | Display-only, low risk | ☐ |
| 21 | ~~Embedding engine / semantic search~~ | embedding_engine.py | Optional enhancement | ☐ → **ACTIVE: Hybrid Architecture Layer 4 (EmbeddingGemma-300M)** |
| 22 | Cost dashboard (scanning budget tracking) | cost_dashboard.py | Important but not blocking | ☐ |
| 23 | SubTaxonomy browser (learned patterns, n-grams) | taxonomy_browser.py | NLP deep-dive, power user feature | ☐ |
| 24 | Product gap engine | product_gap_engine.py | Newer feature, may not be fully wired | ☐ |
| 25 | Help dialog (docs, FAQ, shortcuts) | help_dialog.py | Low risk, content-only | ☐ |
| 26 | Backup/restore database | settings_page.py | Useful but not day-1 critical | ☐ |
| 27 | Print/PDF export for reports | ai_reports.py | Convenience feature | ☑ 5.5D (Save .html on AI Reports + Smart Reporting) |

---

## 6. Performance: v4.1 → Platform Build

| Metric | v4.1 (subprocess) | v5.4 (agentic) | v1.0 (hardened) | Improvement |
|--------|-------------------|-----------------|-----------------|-------------|
| Cold start overhead | ~17s per batch | ~1.5s total (once) | ~1.5s total | ~91% reduction |
| 45-batch scan overhead | ~12.75 min waste | ~1.5s total | ~1.5s total | ~99.8% reduction |
| Persistence granularity | Batch-level (all-or-nothing) | Per-ticket (immediate) | Per-ticket + boundary guard | Crash-safe + TRC-accurate |
| Taxonomy freshness | Static snapshot | Live tool query | Live tool query | Real-time |
| Rate limiting | None | Adaptive token bucket + probe floor (6.3) + two-tier recovery (8.5) | Same | Zero 429s, fast recovery |
| Batch sizing | Fixed 200/batch | Dual-constraint (input+output budgets), cap 75 | Same | Right-sized |
| Health monitoring | None | 5s deterministic + watchdog (6.2) + canary probes (6.3) | Same | Self-healing + predictive |
| Cross-TRC detection | Post-hoc only | During scan (tool) + analyst synthesis | Same | Real-time |
| Post-scan analysis | Meta-analyzer only | Analyst + meta-analyzer | Analyst (batched novelty, verdict feedback) + meta + pattern merge | Full pipeline |
| Crash recovery | Re-run entire scan | Skip already-classified, resume | + partial parse retry | Minutes vs hours |
| Classification coverage | — | 849/888 (95.6%) | ~880/888 (~99.1%) | Boundary guard + partial parse |
| TRC accuracy | — | 818/849 (96.3%) | ~880/880 (~100%) | Boundary guard rejects overflow |
| Novelty validation | — | 30 tickets max (hard cap) | All novels (batched) | Full coverage |

### Report Engine Performance (7.0 → 8.5 → 9.0)

| Report Type | Before (CLI fork) | 7.0 (Bridge) | 8.5 (Resilient) | 9.0 (Pipelined) | Notes |
|-------------|-------------------|--------------|-----------------|-----------------|-------|
| Standard (1 call) | ~25s | ~8s | ~8s | ~8s | 3× speedup (7.0) |
| VOC Phase 1 (127 TRCs) | ~42 min (sequential) | ~48 min (E2E actual) | 30-50 min (resilient) | ~10-14 min (batched) | 9.0: 127 TRCs → ~8 batches via VOCBatchPacker |
| VOC Accumulator | n/a | n/a | n/a | ~0 min additional | Pipelined into Phase 1 whitespace (priority 1) |
| VOC Specialists (×3) | n/a | n/a | n/a | ~3-5 min | Pattern + Novelty + Friction in parallel |
| VOC Convergence | n/a | Failed (800ch truncation) | Failed | ~2-3 min (1 call) | 9.0: run_resilient() with full retry budget |
| **VOC Total** | **n/a** | **~48 min, no report** | **40-80 min, no report** | **19-24 min, 40-53k char report** | **First-ever complete 7-section executive report** |
| Follow-up chat | ~20s (text only) | ~5s + live DB data | ~5s + live DB data | ~5s + live DB data | 4× + quality (7.0) |
| NLP Synthesis (1 call) | ~25s | ~8s | ~8s | ~8s | 3× speedup (7.0) |

### Production Scan Results (v5.2→5.4, Feb 21-24)

Three full production scans on 886 tickets across 38 TRCs (5.2–5.3). **5.3 validates all
bug fixes and confirms gemini-2.5-flash as default model.** 5.4 partial benchmark
confirms batch sizing changes (0 batches >75 tickets) but full benchmark pending
stable API conditions.

### Pipeline Throughput

| Metric | 5.2 (2.0-flash) | 5.2 (2.5-flash) | 5.3 (2.5-flash) | 5.4 (partial) | Delta (5.3→5.4) |
|--------|-----------------|-----------------|-----------------|----------------|-----------------|
| Workers | 2 | 2 | 3 | 3 | — |
| Batches | 42 | 12 | 12 | 14 | +2 (smaller batches) |
| Max batch size | 25 | 100 | 100 | 75 ✅ | Hard cap enforced |
| Classified | 843/886 (95.1%) | 886/886 (100.0%) | 886/886 (100.0%) | API degraded | Pending |
| Wall clock | 810s | 920s | 1,020s | API degraded | Expected ~650-750s |
| Stall timeouts | 0 | 1 | ~8 | 85%+ batch stall (API) | Pending stable API |
| Supervisor restarts | 0 | 1 | 2 (both legitimate) | — | — |
| Batches requeued | 0 | 0 | 4 | — | Expected 0 |
| Analyst reports | 0 (crashed) | 0 (crashed) | 3 ✅ | — | — |
| Throughput (tickets/min) | 62.4 | 57.8 | 52.1 | Pending | Expected ~70-80 |

### Classification Quality

| Metric | 5.2 (2.0-flash) | 5.2 (2.5-flash) | 5.3 (2.5-flash) | Delta (5.2→5.3) |
|--------|-----------------|-----------------|-----------------|-----------------|
| Avg confidence | 0.867 | 0.948 | 0.977 ⬆ | +3.1% |
| Min confidence | 0.400 | 0.500 | 0.700 ⬆ | +0.200 |
| Max confidence | 1.000 | 1.000 | 1.000 | — |
| Confidence std dev | ~0.15 (wide) | ~0.04 (tight) | ~0.03 (tighter) | Improved |
| Unique sub-clusters | ~80 | ~80 | 96 ⬆ | +20% |
| Novel tickets | — | — | 252 (28.4%) | New metric |
| Sub-patterns | 382 | 126 | 446 ⬆⬆ | 3.5× (TRC fix) |
| N-grams per pattern | 9.4 | 22.2 | 8.3 | Normalized (healthier) |
| Unique TRCs in sub_patterns | 11 (corrupted) | 11 (corrupted) | 127 ✅ | Fixed |
| Friction types | 10 | 12 | 11 | data_discrepancy absorbed |
| Findings (total) | 65 | 103 | 94 | — |
| Within-TRC findings | 30 | 27 | 66 ⬆⬆ | 2.4× (TRC fix) |
| Cross-TRC findings | 35 | 76 | 28 ⬇ | Rebalanced correctly |

### Friction Type Distribution (model + build comparison)

| Friction Type | 5.2 (2.0-flash) | 5.2 (2.5-flash) | 5.3 (2.5-flash) | Notes |
|---------------|-----------|-----------|-----------|-------|
| incorrect_charge | 41.3% | 36.8% | 42.3% | Recovered with clean TRCs |
| policy_confusion | 7.8% | 13.4% | 14.2% | Stable |
| access_blocked | 11.6% | 8.2% | 12.4% | +4.2 pp with proper TRC context |
| feature_broken | 11.7% | 11.6% | 9.4% | Stable |
| missing_information | 6.5% | 5.9% | 5.2% | Stable |
| communication_gap | — | 3.6% | 5.0% | Retained from 5.2 |
| automation_loop | — | — | 3.6% | Emerged in 5.3 |
| process_delay | 5.2% | 4.8% | 3.5% | Stable |
| self_serve_failure | 4.1% | 3.7% | 2.3% | Stable |
| escalation_demand | — | 2.7% | 1.5% | Retained from 5.2 |
| other | 4.2% | 2.8% | 0.7% | Better bucket specificity |
| data_discrepancy | — | — | 0% (gone) | Absorbed into incorrect_charge + missing_information |

### Anomaly & Sentiment Calibration

| Metric | 5.2 (2.0-flash) | 5.2 (2.5-flash) | 5.3 (2.5-flash) | Analysis |
|--------|-----------|-----------|-----------|----------|
| Normal | — | ~830 | 748 | Fewer false-normals |
| Critical anomalies | 9 (1.1%) | 24 (2.7%) | 87 (9.8%) ⬆⬆ | Clean TRC context enables real anomaly ID |
| Unusual anomalies | 184 (21.8%) | 121 (13.7%) | 51 (5.8%) | Tighter filter, less noise |
| Mixed sentiment | 0% | 33.1% | 19.9% | 4-class retained, calibrated |
| Negative sentiment | 77.5% | 58.1% | 67.9% | More accurate with clean TRCs |
| Neutral sentiment | — | — | 11.4% | — |
| Positive sentiment | — | — | 0.8% | — |

### Cost Comparison

| Metric | 2.0-flash | 2.5-flash |
|--------|-----------|-----------|
| API calls | 42 | 12 |
| Est. cost | ~$0.22 | ~$0.34 |
| Cost per ticket | $0.00026 | $0.00038 |
| At 50K tickets/month | ~$13/mo | ~$19/mo |

### Model Scorecard (14 dimensions)

| Dimension | Winner |
|-----------|--------|
| Classification rate (100% vs 95.1%) | **2.5-flash** |
| Throughput (62.4 vs 57.8 tkt/min) | 2.0-flash |
| Bridge stability | Tie |
| Confidence avg (0.948 vs 0.867) | **2.5-flash** |
| Confidence floor (0.800 vs 0.050) | **2.5-flash** |
| Sub-pattern quality (126 vs 382) | **2.5-flash** |
| N-gram richness (22.2 vs 9.4/pattern) | **2.5-flash** |
| Friction granularity (12 vs 10 types) | **2.5-flash** |
| Anomaly calibration | **2.5-flash** |
| Sentiment nuance (4-class vs 3-class) | **2.5-flash** |
| Findings volume (103 vs 65) | **2.5-flash** |
| API calls (12 vs 42) | **2.5-flash** |
| Cost per ticket | 2.0-flash |
| Batch stall risk | 2.0-flash |
| **Final: 2.5-flash wins 9 of 14** | |

### Scan Progression (5.2 debugging arc, gemini-2.0-flash)

| Scan | Batches | Classified | Restarts | Issue |
|------|---------|-----------|----------|-------|
| 1 (pre-fix) | 10/43 timeout | 161 | 16 | Supervisor death spiral + context degradation |
| 2 | 27/42 | 300 | 3 | tools_called accumulation ceiling |
| 3 | 42/42 | 300 | 0 | tools_called still capped at 150/worker |
| 4 | 42/42 | 300 | 0 | Same — identified _batch_tool_calls fix |
| **5 (final)** | **42/42** | **849** | **0** | **All 6 runtime bugs fixed** |

### Build 5.3 Fix Verification

| Fix | Status | Prior (5.2) | 5.3 Result |
|-----|--------|-------------|------------|
| TRC propagation | ✅ PASS | 104 JSON-array TRC values | 0 |
| Dedup on retry | ✅ PASS | 147 rows for 100 tickets | 0 duplicates |
| Quality audit | ✅ PASS | Crashed: no such column: message | 8,318 byte report stored |
| Sub-pattern TRCs | ✅ PASS | JSON-array pollution in sub_patterns | 0 JSON-array values |
| Unique TRC coverage | ✅ FIXED | 11 unique TRCs (corrupted) | 127 unique TRCs |
| Supervisor false restart | ✅ PASS | Would trigger on context reset | 2 restarts (both legitimate — low parse rate) |

### Agent Performance (5.3)

| Agent | 5.2 (2.5-flash) | 5.3 (2.5-flash) |
|-------|-----------------|-----------------|
| Worker_0 | A- (1 stall) | A (reliable, handled 100-ticket batches) |
| Worker_1 | B+ (had stall) | B (stall-prone on mixed-TRC batches, 2 resets) |
| Worker_2 | — | B (new 3rd worker, stall-prone, 1 restart) |
| Supervisor | A (1 restart) | A (2 legitimate restarts, no false positives) |
| Analyst | F (crashed) | A ✅ (all 3 reports: synthesis 22KB, audit 8KB, novelty 7KB) |
| Meta-Analyzer | A | A |
| Rate Governor | A | A (tightened interval 25s → 22.5s) |

---

## 7. What's Next — Immediate Build Path

### Completed Builds
~~Priority 1: Build 8.5 — ReportOrchestrator Resilience~~ ✅ BUILT + E2E VALIDATED
~~Priority 1.5: Build 9.0 — Multi-Perspective VOC Pipeline~~ ✅ BUILT + E2E VALIDATED (4 live runs)
~~Build 1.0 — Full v2 UI Rebuild + Pipeline Hardening~~ ✅ BUILT (5 phases, all done)
~~Phase 5.5 — Reporting Suite Overhaul (A-D)~~ ✅ BUILT (MarkdownViewer, pipelines, scheduling, export)
~~Platform Build P0-P5~~ ✅ BUILT (settings, Claude+registry, auto-update, Zendesk monitor, source warehouse+watchlist, Guru KB, GitHub wiring. 23 new files, ~250 tests, 598 total passing)

### 🔥 Priority 1: Hybrid Chat Architecture Sprint (THIS WEEK — Claude Code)

**Vision:** Non-technical end users load tickets, ask Gemini conversational questions, get answers grounded in specific ticket evidence — quantify issues, pinpoint root causes, drill into individual tickets, track trends, find needles in haystacks. Today, Gemini only sees aggregated statistics and can't reference individual tickets from chat.

**Architecture:** Two interaction modes sharing one data layer. Fast path (sub-second): chat tools query pre-computed structured tables. Deep path (1-5 min): chat triggers the report pipeline inline. Filter engine is the shared primitive. User never knows which path fired.

```
User question
     │
     ├── Answerable from tables? → Fast path (<1s)
     │   query_ticket_classifications, list_tickets, query_findings, query_stats
     │
     ├── Needs specific ticket text? → Thread path (2-3s)
     │   read_thread / read_threads_batch (PII-redacted, 8K/4K cap)
     │
     ├── Needs fuzzy/conceptual match? → Semantic path (1-2s)
     │   EmbeddingGemma-300M similarity search against cached vectors
     │
     └── Needs deep analysis? → Pipeline path (1-5 min)
         run_report triggers pipeline, structured JSON output → query_report reads it
```

**Day-by-Day Sprint:**

| Day | Deliverable | LOC Est. | Tests |
|-----|------------|---------|-------|
| 1 (Mon) | Filter engine (`src/data/filter_engine.py` ~200 LOC) + migration 006 (4 new tables: ticket_theme_tags, enriched_trends, ticket_embeddings + expanded chat_sessions) | ~300 | 20-25 |
| 2 (Tue) | Session-scoped ChatEngine (wire to chat_sessions) + 4 fast-path tools (query_ticket_classifications, list_tickets, query_findings, query_stats) + unified tool registry | ~500 | 15-20 |
| 3 (Wed) | Thread access tools (read_thread, read_threads_batch with PII redaction + scope validation) + query_report tool + ticket-theme junction tagging in nlp_meta_analyzer | ~400 | 10-15 |
| 4 (Thu) | Structured JSON report output (populate analysis_runs.output_structured) + run_report chat tool (inline pipeline trigger with confirm flow) + post-NLP stats pass (enriched_trends: friction/sub-pattern/sentiment velocity) | ~400 | 10-15 |
| 5 (Fri) | EmbeddingGemma-300M engine (replace embedding_engine.py) + persistence pipeline (ticket_embeddings table, incremental build) + semantic_search tool + Express Installer bundling (CPU-only torch + model directory) | ~600 | 10-15 |

**Total: ~2,200 new LOC, 65-90 tests**

**Key Data Layer Changes (Migration 006):**
- `ticket_theme_tags` — junction table linking tickets to NLP themes/findings (solves "which tickets are in this theme?")
- `enriched_trends` — post-NLP time series on friction_type, sub_pattern, sentiment (solves "is this growing?")
- `ticket_embeddings` — persisted EmbeddingGemma vectors as BLOB (solves needle-in-haystack search)
- `chat_sessions.filter_json` — expanded session filter context (replaces narrow trc_filter + date_start + date_end)

**New Chat Tools (8):**
1. `query_ticket_classifications` — count/group by any enriched field
2. `list_tickets` — return filtered ticket records from ticket_index
3. `query_findings` — read nlp_findings within session scope
4. `query_stats` — read incident_flags, daily_counts, enriched_trends
5. `read_thread` — full conversation text, PII-redacted, 8K cap, scope-validated
6. `read_threads_batch` — up to 5 tickets, 4K each, 20K total cap
7. `query_report` — read structured JSON from analysis_runs.output_structured
8. `run_report` — trigger pipeline inline (propose params → user confirms → pipeline runs → JSON output)

**EmbeddingGemma-300M (replacing all-MiniLM-L6-v2):**
- 308M params, 768-dim output, 2K token context (vs MiniLM's 256 tokens)
- Query/document prompt format (optimized retrieval asymmetry)
- #1 open model <500M on MTEB benchmarks
- Air-gapped: 6 env vars (HF_HUB_OFFLINE, TRANSFORMERS_OFFLINE, HF_DATASETS_OFFLINE, HF_HUB_DISABLE_TELEMETRY, NO_PROXY=*, CURL_CA_BUNDLE="") + verify_air_gap() runtime check (socket test must FAIL) + audit log
- SafeTensors format (no arbitrary code execution, security upgrade over MiniLM's pickle)
- Pre-bundled in Express Installer (~1.2 GB model dir, CPU-only torch saves ~1.4 GB vs CUDA)
- Express Installer zip with EmbeddingGemma: **~800 MB – 1 GB** (actually smaller than current build with CUDA torch)

**Deferred (Second Horizon):**
- Layer 5: Cross-theme tracking (themes table + theme_snapshots, 4-5 days after Layers 2+3)
- Layer 6: Client journey tracking (client_profiles + client_journey_events, 4-5 days, needs InfoSec)

**Full spec:** See `ALMA_INSIGHTS_ONE_WEEK_SPRINT.md`

### Priority 2: Integration & Testing for Platform Build
The P0-P5 platform build is code-complete but uncommitted. Immediate needs:
1. Commit strategy decision (per-phase commits vs. feature branch squash)
2. Live E2E testing: Zendesk monitor against production instance, Guru sync against live KB
3. Watchlist engine tuning: validate EWMA learning loop with real alert confirm/dismiss cycles
4. Guru effectiveness: need 14+ days of post-push data to validate Poisson significance testing

### Priority 3: Data Integration Roadmap (from CX Audit vision)
Five integrations to evolve from problem identification to cost intelligence:
1. Zendesk handle time enrichment (LOW effort, 1-2 weeks) — cost-per-sub-pattern
2. Provider/client retention data from Salesforce (MODERATE, 2-3 weeks) — friction-to-churn correlation
3. Claims/eligibility operational data from Redshift (MODERATE, 2-4 weeks) — group member ID attribution
4. Guru effectiveness measurement (already built in P4, needs production data)
5. Outcome measurement framework (LOW-MODERATE, 1-2 weeks) — closed audit loop

### Priority 4: VOC Reliability Hardening
Based on 9.0 E2E observations:
1. Accumulator yield — target 8 rounds, actual 1-3 (consider dedicating 1 of 4 bridges)
2. Batch parser fuzz tests — unit tests with all 127 actual TRC names
3. Report caching — if convergence fails, cache intermediate state for retry
4. Serialized regression + stress testing under simulated stall conditions

### Priority 5: CUSUM Statistical Corrections (1.75a)
5 fixes for false positives, missed detections, statistical accuracy.
~310 lines across 5 files.
See BUILD_SPEC_1_75a_CUSUM_CORRECTIONS.md.

### Priority 5: Quality + UX Backlog (from BUILD_SPEC_5_6)
Remaining tasks from the original 5.6 spec not covered by Build 6.0's VOC pipeline:
1. T3 Incremental Scans (delta-only classification) — highest ROI for cost/runtime
2. T2 Findings Dedup (Jaccard cross-scan) — signal-to-noise for repeat users
3. T4 Export (CSV/HTML/clipboard) — team sharing
4. T1 Prompt Eval (labeler + scorer) — classification quality baseline
5. T6 Confidence Calibration (1-5 rating + review queue)
6. T5 Semantic Search (wire embedding_engine)

### Priority 6: Scale Validation
End-to-end scan on alma_test_5000.csv (5,000 tickets) with gemini-2.5-flash.
Validate 75-ticket dual-constraint batches at scale.
Verify 446 sub-patterns consolidate further on second scan (learning loop).
Best tested AFTER T3 (incremental scans) is built — run full scan, then incremental.

### Completed Follow-ups
~~Cap mixed-batch TRC count at 10 for batches >80 tickets~~ — ✅ ADDRESSED in 5.4.
~~Fix JSON-array TRC storage~~ — ✅ FIXED in 5.3.
~~Fix quality_audit column mismatch~~ — ✅ FIXED in 5.3.
~~Investigate duplicate classifications on retry~~ — ✅ FIXED in 5.3.

### Gated: Phase 5 (Customer Entity)
Blocked by Lightdash validation + InfoSec.

---

## 8. Open Items

### Must Verify
- [ ] Requester hash in csv_ingestion.py (SHA-256, 3.0 §11)
- [ ] Intervention correlation in incident_engine.py (3.0 §9)
- [ ] Build 1.0 Phase 5.4 E2E validation — full scan with boundary guard + partial parse recovery. Expected: 95.6% → ~99.1% coverage, 96.3% → ~100% TRC accuracy
- [ ] VOC report quality consistency — run 5+ E2E runs, track TRC coverage (83-86%), accumulator yield (1-3 rounds), specialist completion (2-3/3), wall time variance (19-24 min)
- [ ] Report engine lifecycle — verify bridge cleanup on app close (no orphaned Node.js processes)
- [ ] Temporal context accuracy — verify weekly window calculations against actual ticket date distribution
- [ ] Memory consolidation (trending_engine.py) — verify 2-3 redundant _fetch_conversations() calls eliminated per 1.0 Phase 5C spec
- [ ] **Hybrid Architecture: filter_engine.py — verify cross-table JOINs produce correct results for all filter combinations (ticket_index × conversations × tickets)**
- [ ] **Hybrid Architecture: ChatEngine session persistence — verify session filters survive app restart via chat_sessions.filter_json**
- [ ] **Hybrid Architecture: read_thread scope validation — verify ticket_id checked against session scope before returning thread text (prevents scope escape)**
- [ ] **Hybrid Architecture: run_report inline trigger — verify pipeline runs from chat, structured JSON populates analysis_runs.output_structured, and query_report reads it back**
- [ ] **Hybrid Architecture: EmbeddingGemma air-gap — verify verify_air_gap() passes on target install machines (socket test, env vars, model files present)**
- [ ] **Hybrid Architecture: semantic_search within session scope — verify embedding cache is masked to session filter, not searching outside scope**
- [ ] **Hybrid Architecture: Express Installer — verify models/embeddinggemma-300m/ copies correctly, CPU-only torch bundles (no CUDA), zip size ~800MB-1GB**
- [ ] **Hybrid Architecture: ticket_theme_tags populated — verify nlp_meta_analyzer produces junction rows, not just exemplar_ticket_ids**
- [ ] **Hybrid Architecture: enriched_trends computed — verify post-NLP stats pass produces friction/sub-pattern velocity data queryable from chat**
- [x] Build 8.5 live E2E benchmark — ✅ VALIDATED via 9.0 E2E runs
- [x] VOC pipeline production validation — ✅ VALIDATED in 9.0: 4 E2E runs on 888-ticket / 127-TRC dataset
- [x] Build 5.4 full benchmark — ✅ SUPERSEDED by 1.0 Phase 5.4 (boundary guard + partial parse addresses remaining coverage gaps)
- [x] NLP Scanner live E2E — ✅ VALIDATED in 1.0 Phase 5.2 (8 live tests: boot → scan → classify → findings → shutdown)
- [x] AI Report generation live E2E — ✅ VALIDATED in 1.0 Phase 5.2 (8 live tests: all 5 report types + history + bridge lifecycle)
- [x] TRC propagation fix applied + re-scan completed — taxonomy populating correctly
- [x] Settings model wiring — single source of truth via gemini.model in settings.yaml
- [x] Full production scan — 849/886 (2.0-flash), 886/886 (2.5-flash), 385→126 sub-patterns, 57→103 findings
- [x] Model comparison complete — gemini-2.5-flash set as default
- [x] Build 5.3 validated — 886/886 classified, TRC fix confirmed (127 unique TRCs), 3 analyst reports, confidence 0.977, 446 sub-patterns, 94 findings (66 within-TRC)

### Known Bugs (P5 remaining)
- [ ] **Smart Reporting Lookback Days queries empty rows** — Uses "N days back from today" but data is import-bounded (CSV ranges). If last import ended Feb 28 and today is Mar 9, last 9 days return nothing. Fix: replace with ModernDatePicker pair + sync_date_to_data(), matching all other pages. See Architecture Decisions Pending.
- [x] **5.5B regression: AI Reports scan blocking lost** — 5.5B overhaul of ai_reports.py dropped _scan_blocked and set_scan_blocking() that main_window.py depends on during NLP scans. Fixed in 5.5C: restored field, guard, method, and refresh_gemini_status() respect.
- [ ] **Double-space TRC name mismatch** — `"Provider clinical tools feature request  (Note Assist...)"` has double space in DB, model returns single space. Split-on-failure correctly requeues as individual task. Working as designed, ~10s overhead per occurrence.
- [ ] **Accumulator yield below target** — Target 8 rounds, actual 1-3. Under heavy stalls, Phase 1 (priority 0) consumes all bridge capacity. Convergence handles variable depth. Consider dedicated bridge.
- [ ] **Run-to-run variance** — 83-86% TRC coverage, 2-3/3 specialists, 19-24 min. Driven by external Gemini stall patterns. Architecture handles gracefully.
- [ ] **gemini-2.0-flash RETIRES TODAY (March 31, 2026)** — Add gemini-2.5-flash-lite to MODEL_INPUT_LIMITS + MODEL_OUTPUT_LIMITS + MODEL_MAX_BATCH + settings dropdown. ~5 lines. URGENT.
- [x] **VOC Phase 1 regression (48 min vs expected 14 min)** — ✅ FIXED in 8.5 (5 resilience features) + 9.0 (batching reduces to ~8 calls). E2E validated: 19-24 min total pipeline.
- [x] **9.0 Bug #1: Batch parser regex** (CRITICAL) — `\S+` couldn't match TRC names with spaces. Fixed: `.+?`. Discovered Run #1.
- [x] **9.0 Bug #2: Unicode `→` in log messages** (HIGH) — Windows cp1252 crash. Fixed: replaced with `->`. Discovered Run #1.
- [x] **9.0 Bug #3: Progress overflow** (LOW) — 80%, 160%, 240% when total=0. Fixed: skip internal progress for dynamic dispatch. Discovered Run #1.
- [x] **9.0 Bug #4: Accumulator `break` vs `continue`** (HIGH) — Loop exited entirely on first timeout. Fixed: `continue` + logging. Discovered Run #2. Run #2: 0 rounds → Run #4: 3 rounds.
- [x] **9.0 Bug #5: E2E stdout cp1252 encoding** (HIGH) — Crash at end of 21-min run. Fixed: UTF-8 stdout wrapper. Discovered Run #2.
- [x] **9.0 Bug #6: SQLite thread safety** (CRITICAL) — Specialist thread called DB methods. Fixed: pre-build all DB contexts in main thread. Discovered prior session.
- [x] **9.0 Bug #7: Report not persisted** (MEDIUM) — Reports only visible in terminal scrollback. Fixed: save to `data/reports/`. Discovered after Run #3.

### Architecture Decisions Pending

- [x] **Smart Reporting: Scheduling vs. Local-Data Model Mismatch** — PARTIALLY RESOLVED by P3/P3.5. Zendesk monitor provides real-time inflow. **Remaining:** Lookback Days → date range pickers. "Run after import" trigger for CSV-only users.
- [ ] **Commit strategy for P0-P5** — 23 new files + ~30 modified, all uncommitted on main.
- [ ] **Source abstraction extensibility validation** — Validate by adding a second source (Intercom or Jira).

### Architecture: Planned Hardening (No Net New Features)

#### Distributed AI Model — Task-Routed Dual Provider
Replace the global model toggle (P1 model_registry `active_model`) with a **task-routing architecture** that assigns AI tasks to providers by trust boundary:

**Gemini lane (PHI-certified, CLI binary):** NLP ticket classification, VOC analysis, per-ticket sentiment/friction/entity extraction — anything that touches `conversations` or `tickets` table content. Runs through existing GeminiBridge pipeline with full resilience layer (canary probes, stall escalation, retry budget, queue-pull dispatch).

**Claude lane (Anthropic API, non-PHI):** Guru KB analysis + content generation, watchlist compound rule LLM triage, meta-analytics on scan results, cross-run trend analysis, effectiveness assessment. Operates on **structured ledger data** — PHI-free statistical aggregates, sub-pattern taxonomies, friction distributions, and Guru card content. Never sees raw ticket text.

**The ledger pattern:** After each Gemini scan, a dynamic .md ledger is generated from existing DB tables (nlp_ticket_classifications, sub_patterns, nlp_findings, scan_events — all already PHI-free post-redaction). Claude reads this structured context via tools: `read_scan_summary(scan_id)`, `get_friction_gaps()`, `get_sub_pattern_trends(trc, window)`, `search_guru_cards(query)`, `propose_guru_edit(card_id, changes)`, `get_effectiveness_report()`. Each tool returns pre-aggregated data from existing queries.

**Implementation:** model_registry evolves from single `active_model` to `task_routing` dict in settings.yaml. client_factory's `build_client_for_model()` becomes `build_client_for_task(task_type)`. Existing pipeline code gets task type parameter. "Fully convert to Claude" remains a config-level override for non-PHI environments.

#### Guru KB Uplevel — Multi-Agent Card Graph Analysis
The current guru_friction_pipeline does flat keyword + LLM scoring against individual cards. This is insufficient for 1,000+ card knowledge bases where friction points span multiple documentation layers (product guides, CX process docs, upstream/downstream operational docs across different collections).

**Card Graph (Layer 1):** On sync, build a relationship index — collection/board/folder hierarchy, cross-card references (title mentions, links), team ownership, product/process domain tagging. SQLite adjacency list. Incremental update on re-sync. Enables navigation from "eligibility check friction" to the full documentation constellation without reading every card.

**Friction-Aware Search Agent (Layer 2):** Given a sub-pattern (e.g., "EC verification timeout"), queries the card graph to find the relevant constellation: EC product guide + CX EC process cards + claims process cards that reference EC outcomes. Uses graph topology + semantic matching on titles/summaries. Output: ranked list of 5-15 cards forming the documentation surface.

**Deep Analysis Agent (Layer 3, parallel):** For each card in the constellation, reads full content alongside specific friction data (sub-pattern descriptions, volume, sentiment, redacted sample summaries from ledger). Produces per-card assessment: accurate/outdated/missing scenario/wrong workflow. Runs in parallel — same dispatch pattern as VOC specialist workers.

**Synthesis & Rewrite Agent (Layer 4):** Takes all per-card assessments → (a) friction flow map showing where in the process chain documentation breaks, (b) prioritized rewrite recommendations (friction volume × gap severity), (c) draft rewrites for top N cards with specific friction context cited. Human-gated push (existing pattern from P4).

### Technical Debt
- [ ] agents.model in settings.yaml — P0 centralized settings but this key may now conflict with P1 model_registry. Verify or remove.
- [ ] datetime.utcnow() deprecation warnings (3x in tool_registry.py) — Python 3.13
- [ ] 28 system test failures from empty DB "no such table" — P2 schema_migrator should fix this if migrations run; verify
- [ ] scan_worker.py + scan_worker_manager.py on disk as fallback — document retention policy
- [ ] Node scan_server/ on disk but not active — document or remove
- [ ] docs/smart_reporting_setup.md not created (Task Scheduler guide)
- [ ] gemini_client.py (219 lines) now legacy — client_factory dispatches via build_client_for_model(). Remove or keep as CLI-only
- [ ] VOC batch parser not fuzz-tested against all 127 actual TRC names
- [ ] No simulated stall testing infrastructure
- [ ] Concurrent `run()` calls untested — two simultaneous VOCBuilder instances with same orchestrator
- [ ] P0-P5 uncommitted — all work on main, no commits yet. See Architecture Decisions Pending.
- [ ] Watchlist EWMA learning loop needs production validation — cold-start behavior (n < 5) and gate threshold (< 0.3) untested with real alert cycles
- [ ] Guru effectiveness 14-day measurement window — no production data yet to validate Poisson significance testing
- [ ] Hot tier memory management — source_warehouse hot tier holds full records in-memory. At 1,000 tickets/day, needs prune strategy or max-size cap
- [ ] **Hybrid Architecture: two disconnected tool systems** — 7 Gemini tools in chat_engine.py + 8 Claude tools in claude_tools.py with zero overlap. tool_registry.py (736 lines) exists for scan pipeline tools but is separate from chat tools. Unified registry needed.
- [ ] **Hybrid Architecture: chat_sessions table unwired** — migration 005 created the table but ChatEngine has zero references to chat_sessions or session_id. History is in-memory list only.
- [ ] **Hybrid Architecture: analysis_runs.output_structured empty** — column exists but report pipeline doesn't populate it. All report output is markdown in analysis_reports.summary/full_results.
- [ ] **Hybrid Architecture: nlp_findings exemplar-only** — themes store 3-5 exemplar_ticket_ids, not complete ticket membership. ticket_theme_tags junction table needed for "which tickets are in this theme?"
- [ ] **Hybrid Architecture: embeddings computed fresh every time** — embedding_engine.py (68 lines) computes in-memory, discards results. No persistence, no cache, ~30s cold start per 10K tickets. Unusable for sub-second chat search.
- [ ] **Hybrid Architecture: CPU-only torch in Express Installer** — current build may bundle full CUDA torch (~2 GB). Switch to CPU-only wheel index to save ~1.4 GB. Must install before sentence-transformers to avoid pip resolving CUDA version.

---

## 9. IP Boundary

**Alma owns**: Everything in this codebase — Zendesk → SQLite, NLP
classification, statistical engines, AI synthesis, entity extraction,
sub-pattern taxonomy, product gap detection, agentic pipeline,
streaming bridge, bridge health monitor, canary probes, tool registry,
supervisor, analyst (batched novelty validation, verdict feedback loop),
VOC root cause analysis pipeline (multi-perspective: batch packing,
accumulator evidence ledger, 3 specialists, convergence), report bridge
engine, shared resilience layer (priority dispatch, queue-pull, retry
budget, stall escalation), classification pipeline hardening (ticket ID
boundary guard, partial parse recovery), temporal context, data-grounded
chat, v2 UI design system, reporting foundation (MarkdownViewer, tech
summary, analyst formatter, scheduling, export pipelines), cross-page
markdown rendering, multi-provider LLM abstraction (model registry,
Claude client, Gemini client, provider-agnostic factory), auto-update
system (stage-and-apply, SHA-256 verify, schema migrator), Zendesk
real-time source monitor (incremental cursor, TRC spike detection),
source abstraction layer (SourceClient/SourceMonitor ABCs, hot/cold
tier data warehouse, hybrid n-gram→LLM classification), EWMA watchlist
engine (4 rule types, learning loop, few-shot LLM triage, gated
escalation), Guru KB integration (friction analysis pipeline, coverage
gap detection, LLM content generation with human-gated push,
effectiveness tracking with Poisson significance testing), release
packager, living audit deliverable, **hybrid chat architecture (filter
engine, session-scoped conversational tools, 8 chat tools with fast/thread/
pipeline/semantic paths, EmbeddingGemma-300M air-gapped semantic search,
structured report output layer, post-NLP enriched trend computation,
ticket-theme junction tagging, Express Installer model bundling)**.

**Startup IP (not here)**: Multi-source canonical layer, Jaccard
similarity, DiD modeling, outcome correlation, LLM analytical clustering,
pattern-of-life narratives, probabilistic scoring.

**Clean split**: Alma uses Gemini (PHI-approved CLI binary) for ticket
classification + VOC analysis inside a persistent agentic pipeline, and
Claude (Anthropic API) for non-PHI operations (Guru KB analysis, content
generation, meta-analysis, watchlist LLM triage). Provider-agnostic
factory dispatches to either model. Real-time Zendesk monitoring with
EWMA watchlist feeds classified tickets through source-agnostic warehouse.
Guru friction pipeline maps ticket sub-patterns to knowledge base gaps
with human-gated content push and Poisson effectiveness measurement.
Startup uses Gemini inside a statistical framework for analytical refinement.

---

## Appendix A: Confidence Scoring — End-to-End

### A.1 Origin — Gemini Generates It

Confidence starts in the prompt template (config/prompts/nlp_classify.txt). When Gemini classifies each ticket, it's instructed to emit a sub_cluster_confidence value between 0.0 and 1.0 in the tool_call JSON:

```tool_call
{"tool": "store_classification", "args": {
  ...
  "sub_cluster_confidence": <0.0-1.0>,
  ...
}}
```

The value represents **how confident the model is that the ticket belongs to the assigned `sub_cluster`**. This is a self-assessed score — Gemini evaluates its own certainty. There's no external ground-truth validation. The model decides "I'm 0.95 sure this ticket is a Copay Discrepancy under the incorrect_charge friction type."

---

### A.2 Validation — Tool Registry Clamps It

When the streaming response hits `tool_registry.py`, the `_tool_store_classification()` handler validates and clamps:

```python
sub_cluster_confidence = max(0.0, min(1.0,
    float(args.get("sub_cluster_confidence", 0.5))))
```

Three safety nets:

- **Type conversion** — forces to float (handles string edge cases)
- **Clamping** — hard floor at 0.0, hard ceiling at 1.0
- **Default** — if the model doesn't emit it at all, falls back to 0.5

It then goes directly into the `INSERT OR REPLACE` into `nlp_ticket_classifications.sub_cluster_confidence` (a REAL column).

---

### A.3 Worker-Level Aggregation — Running Average

The worker tracks a running `avg_confidence` across all tickets it has classified. There are actually two different averaging methods depending on the parse path:

**Tool call path (primary, ~99% of tickets)** — uses incremental mean:

```python
conf = float(tool_args.get("sub_cluster_confidence", 0.5))
conf = max(0.0, min(1.0, conf))
n = len(classified_ids)
if n <= 1:
    self.avg_confidence = conf
else:
    self.avg_confidence = (self.avg_confidence * (n - 1) + conf) / n
```

This is a true running average — each ticket's contribution is 1/n. After 100 tickets, any single ticket only moves the needle by 1%.

**JSON fallback path (rare, when tool_call streaming fails)** — uses EMA blending:

```python
batch_avg = total_conf / n_conf
if self.avg_confidence == 0.0:
    self.avg_confidence = batch_avg
else:
    self.avg_confidence = self.avg_confidence * 0.7 + batch_avg * 0.3
```

This is an exponential moving average — 70% old signal, 30% new. It responds faster to recent changes but doesn't weight all history equally.

**The practical effect**: under normal operation (tool_call path), confidence is a stable, slowly-moving average. One bad ticket out of 100 barely registers. But on the fallback path, a bad batch can swing it 30%.

---

### A.4 Supervisor Health Monitoring — Threshold Gate

The supervisor polls worker health every 5 seconds and checks confidence against a floor:

```python
CONFIDENCE_FLOOR = 0.15     # restart worker if below this

# Only checked after 5+ batches (grace period)
avg_confidence = health.get("avg_confidence", 1.0)
if batches_done >= 5 and avg_confidence < self.CONFIDENCE_FLOOR:
    self._restart_worker(worker, "low_confidence")
```

Two gates protect against false positives:

- **batches_done >= 5** — the supervisor waits for 5 post-reset batches before evaluating. This is the 5.3 fix — `batches_done` now uses `_batches_since_reset` instead of `batches_processed`, so after a proactive context reset the grace period restarts properly.

- **< 0.15 threshold** — very permissive. A confidence of 0.15 means the model is basically saying "I'm guessing." In practice, gemini-2.5-flash runs at 0.95-0.98 avg, so this would only trigger if the model is genuinely broken (corrupted context, wrong prompt, etc).

The confidence is also written to the `agent_health` table every poll cycle for monitoring/debugging.

---

### A.5 Reset Lifecycle

When a worker is restarted (by the supervisor or proactive context reset):

```python
self._batches_since_reset = 0    # grace window
self.parse_rate = 1.0            # reset to optimistic
self.avg_confidence = 0.0        # reset to zero
```

The confidence resets to 0.0, but the supervisor won't check it until 5 new batches have been processed. By that point, the incremental average has accumulated enough signal (~100+ tickets) to be meaningful.

This prevents the "death spiral" that existed before 5.3: worker resets → confidence at 0.0 → supervisor sees < 0.15 after 5 batches → restarts again → infinite loop.

---

### A.6 Downstream — Where Confidence Is (and Isn't) Used

**Stored per-ticket**: Every classification's confidence is in `nlp_ticket_classifications.sub_cluster_confidence`. It's available for any query.

**NOT used for findings creation**: The meta-analyzer creates findings based on ticket count thresholds (5+ tickets in a sub-cluster) and anomaly flags, not confidence. A sub-cluster with avg confidence 0.7 gets the same treatment as one with 0.95.

**NOT used by the analyst**: The analyst's cross-TRC synthesis and quality audit don't aggregate or filter by confidence.

**NOT used for subtaxonomy**: Sub-patterns are built from sub-cluster labels and ngrams — confidence isn't a factor in whether a pattern gets created or promoted.

So effectively, confidence serves two purposes today:

- **Worker health monitoring** — the supervisor uses it as a model-degradation signal
- **Per-ticket metadata** — stored for potential future use (e.g. filtering low-confidence classifications, weighting findings by confidence, quality reporting)

---

### A.7 The Numbers in Practice

From the 5.3 benchmark:

| Metric | Value |
|--------|-------|
| Avg confidence | 0.977 |
| Min confidence | 0.700 |
| Max confidence | 1.000 |
| Supervisor threshold | 0.15 |

The model is very confident across the board. The minimum of 0.700 means even the least certain classification was "I'm 70% sure" — well above the supervisor's 0.15 floor. The 0.977 average means typical classifications come back at 0.95-1.00.

The confidence improvement from 5.2 (0.948) → 5.3 (0.977) is likely attributable to the TRC fix: when the model gets a clean per-ticket TRC instead of a JSON array like `["TRC-A", "TRC-B", "TRC-C"]`, it can classify with higher certainty because the context makes sense.

---

## Appendix B: System Architecture — Full Pipeline Diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                        ALMA INSIGHTS NLP SCAN PIPELINE                      │
│                              Pipeline v5.2+                                 │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────┐         ┌──────────────────────────────────┐           │
│  │  PySide6 UI /    │────────▶│    ScanOrchestrator              │           │
│  │  settings.yaml   │         │    (Python main thread)          │           │
│  └─────────────────┘         │                                  │           │
│                              │  • Reads gemini.model from YAML  │           │
│                              │  • Creates BatchPacker(model=...) │           │
│                              │  • Partitions tickets → batches  │           │
│                              │  • Boots N bridges + workers     │           │
│                              │  • Distributes work via queue    │           │
│                              │  • Runs Analyst + MetaAnalyzer   │           │
│                              └───────┬────────┬────────┬────────┘           │
│                                      │        │        │                    │
│                     ┌────────────────┘        │        └───────────────┐    │
│                     ▼                         ▼                        ▼    │
│  ┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────┐  │
│  │   WorkerAgent_0       │  │   WorkerAgent_1       │  │   Supervisor     │  │
│  │   (Thread)            │  │   (Thread)            │  │   (Thread)       │  │
│  │                       │  │                       │  │                  │  │
│  │  • Builds prompt      │  │  • Builds prompt      │  │  • Polls health  │  │
│  │  • bridge.call_stream │  │  • bridge.call_stream │  │  • parse_rate    │  │
│  │  • StreamParser.feed  │  │  • StreamParser.feed  │  │  • avg_confidence│  │
│  │  • ToolRegistry.exec  │  │  • ToolRegistry.exec  │  │  • stall detect  │  │
│  │  • Per-ticket SQLite  │  │  • Per-ticket SQLite  │  │  • Restart worker│  │
│  └──────────┬───────────┘  └──────────┬───────────┘  └──────────────────┘  │
│             │                         │                                     │
│             ▼                         ▼                                     │
│  ┌──────────────────────┐  ┌──────────────────────┐                        │
│  │  GeminiBridge_0       │  │  GeminiBridge_1       │                        │
│  │  (Python wrapper)     │  │  (Python wrapper)     │                        │
│  │                       │  │                       │                        │
│  │  • Subprocess mgmt    │  │  • Subprocess mgmt    │                        │
│  │  • JSON-line protocol │  │  • JSON-line protocol │                        │
│  │  • Reader thread      │  │  • Reader thread      │                        │
│  │  • Request ID routing │  │  • Request ID routing │                        │
│  │  • Crash recovery     │  │  • Crash recovery     │                        │
│  └──────────┬───────────┘  └──────────┬───────────┘                        │
│             │ stdin/stdout             │ stdin/stdout                        │
│             ▼                         ▼                                     │
│  ┌──────────────────────┐  ┌──────────────────────┐                        │
│  │  gemini_bridge.mjs    │  │  gemini_bridge.mjs    │                        │
│  │  (Node.js subprocess) │  │  (Node.js subprocess) │                        │
│  │                       │  │                       │                        │
│  │  • createRequire() to │  │  • Boots CLI modules  │                        │
│  │    load CLI internals │  │    ONCE (~1.5s)       │                        │
│  │  • loadSettings()     │  │  • Stays alive for    │                        │
│  │  • initializeApp()    │  │    all batch calls    │                        │
│  │  • validateAuth()     │  │  • sendMessageStream  │                        │
│  │  • geminiClient       │  │  • Heartbeat + stall  │                        │
│  │    .sendMessageStream │  │  • Exit guard patches │                        │
│  └──────────┬───────────┘  └──────────┬───────────┘                        │
│             │ HTTPS                    │ HTTPS                              │
│             ▼                         ▼                                     │
│         ┌───────────────────────────────────┐                               │
│         │       Gemini API (Google Cloud)    │                               │
│         │    gemini-2.0-flash / 2.5-flash   │                               │
│         └───────────────────────────────────┘                               │
│                                                                              │
│  POST-CLASSIFICATION (sequential, single bridge):                           │
│  ┌────────────────┐   ┌──────────────────┐   ┌──────────────────────┐      │
│  │ AnalystAgent   │──▶│ NLPMetaAnalyzer  │──▶│  SQLite warehouse    │      │
│  │ (LLM judgment) │   │ (Python, no LLM) │   │  • sub_patterns      │      │
│  │ • Synthesis    │   │ • Within-TRC agg │   │  • sub_pattern_ngrams│      │
│  │ • Quality audit│   │ • Sub-taxonomy   │   │  • nlp_findings      │      │
│  │ • Novelty valid│   │ • N-gram extract │   │  • nlp_ticket_class. │      │
│  │ • Pattern merge│   │ • Cross-TRC      │   │  • analyst_reports   │      │
│  └────────────────┘   │ • Impact scoring │   └──────────────────────┘      │
│                       │ • HIPAA pruning  │                                  │
│                       └──────────────────┘                                  │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Appendix C: CLI Bridge Deep Dive

### C.1 Why a Bridge?

InfoSec requires BAA-covered CLI access only — no direct @google/generative-ai SDK calls. The old architecture spawned a new `subprocess.run()` for each batch (~17s cold-start overhead × 42 batches = 12 minutes wasted). The bridge boots the CLI modules once and stays alive.

### C.2 Boot Sequence (gemini_bridge.mjs)

```
Node.js Process Startup
  │
  ├── 1. createRequire() → resolve @google/gemini-cli from global npm
  ├── 2. cliRequire() → resolve @google/gemini-cli-core (nested in CLI's node_modules)
  ├── 3. INSTALL EXIT GUARD (patches process.exit):
  │      • exit(0)   → BLOCKED (CLI thinks one-shot done, bridge stays alive)
  │      • exit(42)  → BLOCKED (input error, per-call, bridge stays alive)
  │      • exit(130) → BLOCKED (cancellation, per-call)
  │      • exit(41)  → ALLOWED (auth failure, bridge must die → bridge_fatal event)
  │      • exit(52)  → ALLOWED (config error, bridge broken)
  │
  ├── 4. INSTALL STDIN PROTECTION (block stdin.destroy() / stdin.end())
  ├── 5. Import CLI modules via dynamic import():
  │      loadSettings, loadCliConfig, initializeApp,
  │      validateNonInteractiveAuth, runNonInteractive,
  │      GeminiEventType, Scheduler, sessionId
  │
  ├── 6. BOOT CLI:
  │      settings = loadSettings()
  │      argv = parseArguments(settings) ← injects --model from Python
  │      config = loadCliConfig(settings, sessionId, argv)
  │      config.storage.initialize()
  │      initializeApp(config, settings)
  │      validateNonInteractiveAuth() → config.refreshAuth()
  │      config.initialize()
  │
  ├── 7. KEEPALIVE TIMER (setInterval 60s, prevents Node exit)
  │
  └── 8. stderr: "Bridge ready. Send JSON on stdin."
         ↓
         Python _ready_event.set()
```

### C.3 Communication Protocol

```
Python (GeminiBridge)                Node.js (gemini_bridge.mjs)
      │                                        │
      │── {"id":"b1","prompt":"...","stream":true} ──▶│
      │                                        │
      │◀── {"id":"b1","type":"content","delta":"For ticket..."} ──│
      │◀── {"id":"b1","type":"content","delta":"```tool_call\n"} ──│
      │◀── {"id":"b1","type":"content","delta":"{\"tool\":..."} ──│
      │◀── {"id":"b1","type":"content","delta":"```\n"} ──│
      │◀── {"id":"b1","type":"heartbeat","elapsed_ms":5000} ──│
      │    ... (repeats for each ticket) ...   │
      │◀── {"id":"b1","type":"done","full_text":"...","elapsed_ms":14200} ──│
      │                                        │
      │── {"command":"ping","id":"p1"} ────────▶│
      │◀── {"id":"p1","status":"alive","uptime_ms":120000} ──│
      │                                        │
      │── {"command":"quit"} ──────────────────▶│
      │                     process.exit(0)     │
```

### C.4 Python-Side Threading Model

```
┌─────────────────────────────────────────────────────┐
│  GeminiBridge Instance                               │
│                                                      │
│  Main Thread:                                        │
│    bridge.call_streaming(prompt, "b1", on_token=cb)  │
│      │                                               │
│      ├── _register_queue("b1") → Queue()             │
│      ├── _send_raw({"id":"b1","prompt":..}) → stdin  │
│      └── BLOCKS on queue.get() until terminal event  │
│           │                                          │
│  Reader Thread (daemon):                             │
│    _reader_loop():                                   │
│      for line in proc.stdout:                        │
│        data = json.loads(line)                       │
│        event = BridgeEvent(id, type, data)           │
│        queues[req_id].put(event) ──▶ unblocks main   │
│                                                      │
│  Stderr Thread (daemon):                             │
│    _stderr_drain():                                  │
│      for line in proc.stderr:                        │
│        if "Bridge ready" → _ready_event.set()        │
│        logger.debug(line)                            │
└─────────────────────────────────────────────────────┘
```

### C.5 Streaming Call → Per-Ticket Persistence Flow

```
Gemini API Response (streaming)
  │
  ▼
gemini_bridge.mjs: GeminiEventType.Content events
  │
  ▼ stdout JSON lines: {"type":"content","delta":"..."}
  │
  ▼
GeminiBridge._reader_loop() → queue.put(BridgeEvent)
  │
  ▼
call_streaming() → queue.get() → event.type == "content"
  │
  ▼ on_token callback
  │
  ▼
WorkerAgent.on_token() → StreamParser.feed(delta)
  │
  ▼
StreamParser: accumulates text, detects ``` fences
  ├── Opening: ```tool_call\n  → enters fence mode
  ├── Content: {"tool":"store_classification","args":{...}}
  ├── Closing: ```  → JSON.parse → ParsedEvent(TOOL_CALL, data)
  │
  ▼
WorkerAgent._handle_parsed_event(TOOL_CALL)
  │
  ├── Check: _batch_tool_calls < MAX_TOOL_CALLS_PER_BATCH (150)
  ├── ToolRegistry.execute("store_classification", args)
  │     │
  │     ▼
  │   INSERT INTO nlp_ticket_classifications ← IMMEDIATE per-ticket persist
  │     │
  │     └── Returns {"status":"stored","ticket_id":"10123"}
  │
  ├── classified_ids.add("10123")
  ├── _batch_tool_calls += 1
  ├── tools_called += 1 (cumulative metric)
  └── Update avg_confidence from tool_args.sub_cluster_confidence
```

---

## Appendix D: Sub-Taxonomy — How It Works

### D.1 NLPMetaAnalyzer (Layer 2 — Pure Python, No LLM)

Runs after all classification batches complete. Builds a persistent learning taxonomy from classification data.

```
NLPMetaAnalyzer.run_meta_analysis(scan_id)
  │
  ├── 1. WITHIN-TRC ANALYSIS
  │     Load all classifications for this scan
  │     GROUP BY (trc, sub_cluster, friction_type)
  │     For each group:
  │       • Count tickets, avg sentiment, confidence
  │       • Determine tier: probationary → active → dormant → retired
  │       • Extract entities (payers, features)
  │       • Create or update sub_patterns record
  │       • Create scan snapshot (sub_pattern_scan_snapshots)
  │
  ├── 2. N-GRAM EXTRACTION (dual-source)
  │     Source A: Gemini's key_phrases from each classification
  │     Source B: TF-IDF computed from ticket full_threads
  │     Merge + deduplicate → INSERT INTO sub_pattern_ngrams
  │     (HIPAA A5: PII redaction on n-grams before storage)
  │
  ├── 3. WITHIN-TRC FINDINGS
  │     Find sub-patterns with 5+ tickets
  │     Impact score = ticket_count × avg_sentiment × confidence
  │     INSERT INTO nlp_findings (finding_type='within_trc')
  │
  ├── 4. CROSS-TRC ANALYSIS
  │     A. Entity concentration: payers/features spanning 3+ TRCs
  │        → Finding: "Entity X spans N TRCs, M tickets"
  │     B. Friction patterns: friction_types spanning 10+ TRCs
  │        → Finding: "Systemic friction_type across N TRCs"
  │
  ├── 5. STATISTICAL CROSS-REFERENCE
  │     Match nlp_findings against:
  │       • Poisson incident flags (anomaly_flags table)
  │       • Theta anomaly baselines (theta_baselines table)
  │       • Rising/cooling terms (trending_engine)
  │     Boost impact_score if corroborated
  │
  └── 6. HIPAA A7: PROVISIONAL PRUNING
        Delete classifications > 30 days old where
        the scan confirmed the same pattern still active
```

### D.2 Sub-Taxonomy Lifecycle

```
  ┌─────────────┐    5+ tickets    ┌────────┐    0 tickets    ┌─────────┐
  │ probationary │───────────────▶│ active  │──(3 scans)────▶│ dormant │
  └─────────────┘                 └────────┘                 └────┬────┘
                                       ▲                          │
                                       │    Re-appears            │ 5 scans
                                       └──────────────────────────│ no data
                                                                  ▼
                                                            ┌─────────┐
                                                            │ retired │
                                                            └─────────┘
```

### D.3 Tables Populated (5.3 numbers)

| Table | Records | Purpose |
|-------|---------|---------|
| sub_patterns | 446 | One per unique (trc, sub_cluster, friction_type). Tracks tier, lifetime_tickets, description |
| sub_pattern_ngrams | 3,687 | Domain-specific phrases linked to each sub_pattern. Used by future scans for pattern matching |
| nlp_findings | 94 | Ranked actionable findings — 66 within-TRC + 28 cross-TRC |
| nlp_ticket_classifications | 886 | Per-ticket: sub_cluster, friction_type, sentiment, anomaly_flag, entities, key_phrases |
| analyst_reports | 3 | Cross-TRC synthesis (22KB), quality audit (8KB), novelty validation (7KB) |

---

## Appendix E: Build 5.2 — Feature Implementations

### E.1 Per-Ticket Tool Call Classification

**Before (5.1):** Prompt said "output a JSON array" → entire batch lost if truncated.

**After (5.2):** Prompt says "output per-ticket tool_call fenced blocks" → each ticket persisted immediately via `store_classification` as it streams in. If Gemini truncates at ticket 15/20, tickets 1–14 are already in SQLite.

### E.2 Model-Adaptive Batch Sizing → Dual-Constraint Sizing (5.4)

`BatchPacker` originally had `MODEL_OUTPUT_LIMITS` and `MODEL_MAX_BATCH` dicts (5.2):

- `gemini-2.0-flash` → 20K output budget, max 25/batch
- `gemini-2.5-flash` → 200K output budget, max 100/batch

**5.4 upgrade:** Added `MODEL_INPUT_LIMITS` for input-token-aware sizing. `compute_batch_size()` now applies a dual constraint — min(output_limit, input_limit, hard_cap). Mixed-batch packing tracks cumulative input chars and flushes when either count or input budget is exceeded. Hard cap lowered from 100 → 75 for 2.5-flash.

| Model | Input Budget | Hard Cap | Output Budget |
|-------|-------------|----------|---------------|
| gemini-2.0-flash | 100K chars | 25 | 20K chars |
| gemini-2.5-flash | 300K chars | 75 (was 100) | 200K chars |
| gemini-2.5-flash-lite | 250K chars | 75 | 200K chars |
| gemini-2.5-pro | 400K chars | 100 | 200K chars |

### E.3 Skip Already-Classified on Resume

`_get_tickets_for_batch` excludes ticket_ids that already have classifications for this `scan_id`, enabling crash-recovery without re-classifying.

### E.4 Settings Cleanup

`config/settings.yaml` aligned with agentic pipeline: `batch_size: 25`, `mode: agentic`, `parallel_workers: 2`.

### E.5 Bridge Token Reporting

Bridge "done" event includes estimated `input_tokens` and `output_tokens` (char/4 estimate since CLI doesn't expose `usageMetadata`).

---

## Appendix F: The 8 Bug Fixes (5.2 Chronological)

### F.1 Supervisor Death Spiral — avg_confidence = 0.00

**Symptom:** After ~10 batches, supervisor restarted workers in an infinite loop. Workers never ran more than 1 batch before being restarted again.

**Root Cause:** The `TOOL_CALL` event handler in `_handle_parsed_event` never updated `self.avg_confidence`. Only `CLASSIFICATION` events (the old JSON path) updated it. With 5.2's tool_call format, confidence stayed at 0.00 forever.

**Fix:** Added confidence extraction from `tool_args.get("sub_cluster_confidence")` inside the `TOOL_CALL` branch of `_handle_parsed_event`. Running average updated per-ticket.

### F.2 Reset Didn't Reset Supervisor Metrics

**Symptom:** After supervisor restarted a worker, the worker was immediately restarted again on the next poll (death spiral continued).

**Root Cause:** `worker.reset()` restarted the bridge but didn't reset `batches_processed`, `parse_rate`, or `avg_confidence`. So supervisor still saw 5+ batches with confidence=0.00 and restarted again.

**Fix:** Added `self.batches_processed = 0`, `self.parse_rate = 1.0`, `self.avg_confidence = 0.0` to `reset()`.

### F.3 UsageTracker Thread Safety

**Symptom:** Non-blocking SQLite warning: "objects created in thread X used in thread Y".

**Root Cause:** Single `UsageTracker` shared across worker threads, each with different thread IDs.

**Fix:** Create per-bridge `UsageTracker` instances in `_boot_workers` using separate `DatabaseManager` instances.

### F.4 Context Degradation After ~10 Batches

**Symptom:** Mixed-TRC batches classified 0/24 after ~10 batches, but worked perfectly with a fresh bridge.

**Root Cause:** Multi-turn conversation accumulates context in Gemini's session. After ~8-10 batches the model's output quality degraded.

**Fix:** Added `MAX_BATCHES_BEFORE_RESET = 5` — proactive bridge restart after every 5 batches. Keeps context fresh (~40K tokens per cycle).

### F.5 Streaming Parser Missing Valid Tool Calls

**Symptom:** Bridge returned 22K chars of valid tool_call blocks, but StreamParser produced 0 events during streaming. Full response contained perfectly valid fenced blocks.

**Root Cause:** Streaming deltas sometimes didn't trigger fence boundary detection (possible chunking edge case with partial ``` sequences split across deltas).

**Fix:** Added tool_call fallback recovery in `_try_parse_json_response` — re-feeds `full_text` through a fresh StreamParser instance after streaming completes.

### F.6 self.tools_called Accumulation (THE ROOT CAUSE)

**Symptom:** Exactly 300/886 tickets classified. Every scan. Two workers × 150 = 300.

**Root Cause:** `self.tools_called` was an instance variable that accumulated across ALL batches but was checked against `MAX_TOOL_CALLS_PER_BATCH` (150):

```python
if self.tools_called < MAX_TOOL_CALLS_PER_BATCH:  # 300 < 150? NO → silently dropped
    result = self.tool_registry.execute(tool_name, tool_args)
    self.tools_called += 1
```

After ~7 batches per worker (150 tool calls), ALL subsequent tool calls — both streaming AND fallback — were silently dropped.

**Fix:** Added `self._batch_tool_calls` per-batch counter, reset at start of each `_run_classification`. Changed limit check from `self.tools_called` → `self._batch_tool_calls`. Kept `self.tools_called` as cumulative metric for health reporting.

### F.7 BatchPacker Conservative Sizing (Pre-existing, Not Fixed in 5.2)

**Symptom:** 37 tickets from the 4 largest TRCs never assigned to any batch.

**Root Cause:** Budget-based sizing in `BatchPacker` underestimates capacity for large TRCs, leaving some tickets outside chunk boundaries.

**Status:** Identified, not fixed in 5.2. 95.6% coverage on 2.0-flash. 2.5-flash achieves 100%. **Addressed in 5.4:** dual-constraint sizing with per-TRC avg thread length awareness prevents oversized batches.

### F.8 Quality Audit Column Mismatch (Fixed in 5.3)

**Symptom:** `Analyst: quality audit data load failed: no such column: message`

**Root Cause:** Quality audit query references a `message` column that doesn't exist in the conversations table schema.

**Status:** Non-blocking in 5.2 (other analyst tasks succeeded). **Fixed in 5.3** — correct column referenced, 8KB audit report stored successfully.

---

## Appendix G: Scan Progression (5.2 Debugging Arc → 5.3 Validation)

### G.1 Build 5.2 Debugging Arc (gemini-2.0-flash)

| Scan | Batches | Classified | Restarts | Runtime | Issue |
|------|---------|-----------|----------|---------|-------|
| 1 (pre-fix) | 10/43 timeout | 161 | 16 | Timeout 10min | Supervisor death spiral + context degradation |
| 2 | 27/42 | 300 | 3 | Timeout 10min | tools_called accumulation ceiling |
| 3 | 42/42 | 300 | 0 | ~14min | tools_called still capped at 150/worker |
| 4 | 42/42 | 300 | 0 | ~14min | Same — identified _batch_tool_calls fix |
| **5 (final)** | **42/42** | **849** | **0** | **~14min** | **All 6 runtime bugs fixed** |

### G.2 Build 5.2 Final Scan Output

| Metric | Value |
|--------|-------|
| Sub-patterns | 385 |
| N-grams | 3,412 |
| Findings | 57 |
| Classification rate | 849/886 (95.1%) |

### G.3 Cross-TRC Insights (5.2 final scan)

- **Top entity concentration:** Griffin Health Group spans 22 TRCs (29 tickets)
- **Top systemic friction:** incorrect_charge across 38 TRCs (367 tickets)
- **Entities detected:** 18 unique payers/entities crossing TRC boundaries

### G.4 Build 5.3 Validation (gemini-2.5-flash, 3 workers)

| Metric | 5.2 Final | 5.3 Validated | Delta |
|--------|-----------|---------------|-------|
| Classification rate | 849/886 (95.1%) | 886/886 (100%) | +4.9 pp |
| Sub-patterns | 385 | 446 | +15.8% (TRC fix unlocked proper grouping) |
| N-grams | 3,412 | 3,687 | +8.1% |
| Findings | 57 | 94 | +64.9% |
| Unique TRCs in taxonomy | 11 (corrupted) | 127 (clean) | Fixed |
| Analyst reports | 0 (crashed) | 3 (all stored) | Fixed |
| Confidence avg | 0.867 | 0.977 | +12.7% |

**Bottom line:** Build 5.3 fixes transformed the pipeline from "mechanically functional but producing corrupt downstream data" to "producing clean, trustworthy analysis output." The TRC fix alone unlocked 3.5× more sub-patterns, 2.4× more within-TRC findings, and 3.6× more critical anomaly detection. The quality audit and novelty reports now actually run. The data is usable.

### G.5 Build 5.4 — Input-Token-Aware Batch Sizing (partial benchmark)

**Problem:** 5.3's 100-ticket mixed-TRC batches stalled repeatedly (8 stall timeouts, 4 requeues), eating speed gains from the 3rd worker. Root cause: BatchPacker only considered OUTPUT budget (response chars) — no awareness of INPUT size (prompt + ticket threads). Mixed batches with 100 tickets × 14-27 TRCs generated massive prompts causing Gemini generation stalls.

**Solution:** Dual-constraint sizing — `compute_batch_size()` now takes `avg_thread_chars` from a per-TRC SQL query, applies both output budget AND input budget constraints, and caps at 75 tickets. Mixed-batch packing tracks cumulative input chars and flushes when either count or input budget is exceeded.

| Metric | 5.3 | 5.4 (partial) | Notes |
|--------|-----|---------------|-------|
| Total batches | 12 | 14 | More, smaller batches |
| Max batch size | 100 | 75 ✅ | Hard cap enforced |
| Batches > 75 tickets | 3 | 0 ✅ | Eliminated |
| Full benchmark | ✅ Complete | ⏳ Pending | API degraded (85%+ batch stall rate) |
| Expected wall clock | 1,020s | ~650-750s | ~35% improvement projected |

**Hotfix applied same session:** Three bugs fixed in the Trending Topics AI Enhancement system — `suggest_keyword_improvements` key mapping (`all_terms/rising_terms` → `rising/cooling`), and floating top-level window bugs in `SmoothingReviewPanel.set_suggestions()` and `KeywordReviewPanel.set_suggestions()` (removed erroneous `self.show()` calls).

---

## Appendix H: Bridge Health Monitor (6.2) — Detailed Architecture

### H.1 Watchdog + Stall Escalation + Queue Poisoning

```
┌─────────────────────────────────────────────────────────┐
│                  GeminiBridge (per instance)              │
│                                                          │
│  ┌─────────────────────────────────────────────────────┐ │
│  │            WATCHDOG THREAD                          │ │
│  │                                                     │ │
│  │  Every 3.0s:                                        │ │
│  │    is_alive()?                                      │ │
│  │      YES → sleep, loop                              │ │
│  │      NO  → _notify_death()                          │ │
│  │             ├─ _bridge_healthy = False               │ │
│  │             ├─ death_count++                         │ │
│  │             ├─ POISON all waiting request queues     │ │
│  │             │   (unblocks all call_blocking callers) │ │
│  │             └─ fire _on_death callback               │ │
│  └─────────────────────────────────────────────────────┘ │
│                                                          │
│  ┌─────────────────────────────────────────────────────┐ │
│  │           STALL ESCALATION                          │ │
│  │                                                     │ │
│  │  record_stall() called on stall_timeout errors:     │ │
│  │    consecutive_stalls++                              │ │
│  │    stall_count++                                    │ │
│  │    if consecutive_stalls >= 3:                      │ │
│  │       → return True (ESCALATION TRIGGERED)          │ │
│  │       → caller does bridge.restart()                │ │
│  │                                                     │ │
│  │  record_success() on any non-stall:                 │ │
│  │    consecutive_stalls = 0 (reset counter)           │ │
│  └─────────────────────────────────────────────────────┘ │
│                                                          │
│  ┌─────────────────────────────────────────────────────┐ │
│  │         QUEUE POISONING                             │ │
│  │                                                     │ │
│  │  On bridge death, inject into ALL waiting queues:   │ │
│  │   BridgeEvent(                                      │ │
│  │     id="__watchdog__",                              │ │
│  │     type="error",                                   │ │
│  │     data={                                          │ │
│  │       "error": "bridge_dead",                       │ │
│  │       "recoverable": True,                          │ │
│  │       "bridge_healthy": False                       │ │
│  │     }                                               │ │
│  │   )                                                 │ │
│  │  → Callers unblock with RuntimeError                │ │
│  └─────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

### H.2 Supervisor Integration (Deterministic Health Checks)

```
Supervisor._poll_cycle() — every 5 seconds
│
├─ For each WorkerAgent:
│   health = worker.get_health()
│   │
│   ├─ bridge_alive == False?     → RESTART worker
│   ├─ consecutive_stalls >= 3?   → RESTART worker
│   ├─ parse_rate < 50%?          → RESTART (after 5+ batches)
│   ├─ avg_confidence < 15%?      → RESTART (after 5+ batches)
│   ├─ no_progress > 300s?        → RESTART (stall timeout)
│   └─ context_tokens > 800K?     → RESET (not full restart)
│
└─ Write health snapshots to agent_health table
```

### H.3 Bridge Stats Tracking

```
get_stats() → {
    "alive":               bool,    # process.poll() is None
    "healthy":             bool,    # ensure_running() succeeded
    "boot_count":          int,     # total boots (incl. restarts)
    "total_calls":         int,     # lifetime API calls
    "death_count":         int,     # unexpected deaths
    "stall_count":         int,     # total stall_timeouts
    "consecutive_stalls":  int,     # resets on success
    "last_death_time":     str,     # ISO timestamp
    "boot_time":           str,     # ISO timestamp
}
```

---

## Appendix I: Canary Probe System (6.3) — Detailed Architecture

### I.1 Pre-Scan Probes → Adaptive Thresholds

```
┌──────────────────────────────────────────────────────────────────┐
│                 PRE-SCAN CANARY PROBES                            │
│                                                                   │
│  _run_canary_probes(scan_id)                                      │
│  ┌──────────────────────────────────────────────────────────────┐ │
│  │  For each bridge (sequential, avoids rate interference):     │ │
│  │                                                              │ │
│  │   bridge.probe(timeout=30)                                   │ │
│  │     │                                                        │ │
│  │     ├─ Python → Node.js → HTTPS → Google → back             │ │
│  │     │  prompt: "Respond with exactly one word: OK"           │ │
│  │     │                                                        │ │
│  │     └─ Returns: {latency_ms, status, error}                  │ │
│  │                                                              │ │
│  │   db.store_probe(scan_id, bridge_idx, "pre_scan",            │ │
│  │                  status, latency_ms, error)                  │ │
│  │                                                              │ │
│  │  Emit scan_event: "preflight" with P50/P95 summary           │ │
│  └──────────────────────────────────────────────────────────────┘ │
│                              ↓                                    │
│  _derive_adaptive_thresholds(scan_id, probe_results)              │
│  ┌──────────────────────────────────────────────────────────────┐ │
│  │  ① Current P50/P95 from this scan's probes                  │ │
│  │  ② Historical P95 from probe_history (7-day rolling)        │ │
│  │  ③ reference_p95 = max(current_p95, historical_p95)         │ │
│  │                                                              │ │
│  │  ADAPTIVE CALL TIMEOUT:                                      │ │
│  │    batch_multiplier = 25 tickets × 2.5s/ticket = 62.5       │ │
│  │    timeout = (P95/1000) × 62.5 × 1.5                        │ │
│  │    clamped to [45s, 600s] — typical: 100–200s                │ │
│  │                                                              │ │
│  │  RATE GOVERNOR FLOOR:                                        │ │
│  │    floor = max(15.0s, P50_seconds × 3.0)                    │ │
│  │    rate_governor.set_probe_floor(floor)                      │ │
│  │    → prevents tightening below baseline                      │ │
│  └──────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

### I.2 Probe History Schema

```sql
probe_history
├── id               INTEGER PRIMARY KEY
├── scan_id          TEXT
├── bridge_index     INTEGER
├── probe_type       TEXT         -- "pre_scan"
├── timestamp        TEXT
├── status           TEXT         -- "success" | "timeout" | "error"
├── latency_ms       INTEGER
├── error_message    TEXT
└── created_at       TEXT

Indexes: idx_probe_scan(scan_id), idx_probe_ts(timestamp)
```

### I.3 VOC Integration — Model Health Context

Probe data injected into VOC synthesis via `_build_model_health_context()`:

```
[MODEL HEALTH CONTEXT]
Pre-scan probe latency: P50=1823ms, P95=3741ms (3 bridges probed)
Historical 7-day: P50=2105ms, P95=4200ms (42 probes, 95% success)
Scan events: 1 stall(s), 0 bridge death(s), 0 restart(s)
```

---

## Appendix J: Report Engine (7.0→8.5→9.0) — Detailed Architecture

### J.1 Master Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                   REPORT ENGINE (7.0→8.5→9.0)                       │
│                                                                    │
│  T1: ReportBridgeClient (187 lines)                                │
│  ┌───────────────────┐    ┌───────────────────┐                    │
│  │ .generate()       │───▶│ GeminiBridge      │                    │
│  │ .is_available()   │    │ .call_blocking()  │                    │
│  │ .shutdown()       │    │ (persistent proc) │                    │
│  └───────────────────┘    └───────────────────┘                    │
│    PII: _redact_base() + _redact_aggressive()                      │
│                                                                    │
│  T2: ReportOrchestrator (~455 lines, 9.0 modified)                 │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐      │
│  │ Bridge[0] │  │ Bridge[1] │  │ Bridge[2] │  │ Bridge[3] │      │
│  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘      │
│        └───────────────┼───────────────┼───────────────┘           │
│               ThreadPoolExecutor(4) + PriorityQueue                │
│               Workers pull highest-priority task from queue        │
│               + Canary probes on boot (adaptive floor + timeout)   │
│               + Stall escalation (3 consecutive → restart)         │
│               + Retry budget (3× stall, 6× quota, 1× other)       │
│               + on_complete callbacks + drain_event keep-alive     │
│               + run_resilient() for single critical calls          │
│               + RateGovernor (two-tier recovery) + cancel flag     │
│                                                                    │
│  T3: Single choke-point swap                                       │
│    _build_gemini_client() → ReportBridgeClient                     │
│    Duck-typed .generate() covers ALL flows:                        │
│      ✓ Standard | VOC | NLP Synthesis | Chat | Custom              │
│                                                                    │
│  VOC 9.0: Pipelined Priority Dispatch                              │
│    VOCBatchPacker: 127 TRCs → ~8 batches (bin-packing)             │
│    Phase 1: ~8 batch tasks at priority 0                           │
│    Accumulator: 8-round evidence ledger at priority 1 (fills gaps) │
│    Specialists: 3 parallel at priority 0 (after Phase 1)           │
│    Convergence: 1 call via run_resilient() → 7-section report      │
│    Total: 19-24 min, 40-53k char reports (E2E validated)           │
│                                                                    │
│  T9: Lifecycle                                                     │
│    Lazy boot on first report → shared across all types → cleanup() │
└────────────────────────────────────────────────────────────────────┘
```

### J.2 VOC Pipelined Dispatch (9.0) — Thread Model

```
_run_analysis_and_synthesis(plan, date_start, date_end)
│
│  ┌─── MAIN THREAD ────────────────────────────────────────────────┐
│  │  1. VOCBatchPacker: 127 TRCs → ~8 batch_specs (bin-packing)   │
│  │  2. Pre-build DB contexts (SQLite-safe): stat, NLP, friction  │
│  │  3. Build batch tasks [{id, prompt, timeout, priority=0}]     │
│  │  4. Start 3 daemon threads:                                    │
│  │     - batch_parser_loop                                        │
│  │     - accumulator_loop                                         │
│  │     - specialist_injector                                      │
│  │  5. Call run_parallel(tasks, on_complete, drain_event)         │
│  │     [BLOCKS until drain_event.set() + queue empty]             │
│  │  6. Join threads, return (trc_analyses, accum, specialists)    │
│  └────────────────────────────────────────────────────────────────┘
│                              ↓
│  ┌─── PRIORITY QUEUE (4 bridges) ─────────────────────────────────┐
│  │  PriorityQueue items: (priority, seq_num, task, retries)       │
│  │                                                                │
│  │  Priority 0: Phase 1 batches (~8) + Specialists (3)           │
│  │  Priority 1: Accumulator rounds (up to 8)                     │
│  │                                                                │
│  │  Workers pull highest-priority task, self-serve from queue     │
│  │  on_complete(task_id, result) fires in worker thread (<1ms)   │
│  │  → wakes batch_parser, accumulator, specialist_injector        │
│  └────────────────────────────────────────────────────────────────┘
│                              ↓
│  ┌─── DAEMON THREADS ─────────────────────────────────────────────┐
│  │                                                                │
│  │  batch_parser_loop:                                            │
│  │    Polls raw_batch_results → parse TRC analyses → trc_analyses │
│  │    Handles chunk merge, split-on-fail requeue                  │
│  │                                                                │
│  │  accumulator_loop (8 rounds):                                  │
│  │    Waits for 80% of TRC data → builds prompt → enqueues at    │
│  │    priority 1 → parses into evidence_ledger + synthesis        │
│  │    Ledger: append-only. Synthesis: rewritten each round.       │
│  │                                                                │
│  │  specialist_injector:                                          │
│  │    Waits for phase1_done event → snapshot ledger → budget      │
│  │    check → builds 3 prompts → enqueues at priority 0           │
│  │    → waits for 3 results → sets drain_event                    │
│  └────────────────────────────────────────────────────────────────┘
│                              ↓
│  ┌─── CONVERGENCE (run_resilient) ────────────────────────────────┐
│  │  Inputs: accumulator ledger + synthesis + 3 specialist reports │
│  │  Template: voc_convergence.txt                                 │
│  │  Output: 7-section executive report (40-53k chars)             │
│  │  Fallback: _assemble_partial_report() if convergence fails     │
│  └────────────────────────────────────────────────────────────────┘
```

### J.3 Lifecycle Management

```
┌─── App Startup ────────────────────────────────────────┐
│  MainWindow.__init__()                                  │
│    └─ reports_page._shared_gemini_client = None (lazy)  │
└─────────────────────────────────────────────────────────┘

┌─── First Report ───────────────────────────────────────┐
│  _get_gemini_client() → _build_gemini_client()          │
│    → ReportBridgeClient(model, pii, ...)                │
│    → bridge NOT booted yet (lazy)                       │
│  .generate() → _ensure_bridge() → NOW boots Node.js    │
│  → bridge.call_blocking() → ~8s                        │
└─────────────────────────────────────────────────────────┘

┌─── Subsequent Reports ─────────────────────────────────┐
│  _get_gemini_client() → cached (bridge running)         │
│  .generate() → ~8s (no cold start)                      │
└─────────────────────────────────────────────────────────┘

┌─── App Shutdown ───────────────────────────────────────┐
│  MainWindow.closeEvent() → _stop_all_workers()          │
│    ├─ job_queue.cancel_all()                            │
│    └─ reports_page.cleanup()                            │
│         ├─ voc_worker.cancel() (if running)             │
│         └─ _shared_gemini_client.shutdown()              │
│              └─ bridge.kill() (Node.js process dead)    │
└─────────────────────────────────────────────────────────┘
```

---

## Appendix K: Windowed Temporal Context + Data-Grounded Chat (7.0)

### K.1 Temporal Context Generation (T6-T7)

```
_generate_windows("2025-10-01", "2025-10-31", "weekly")
│
└─▶ [("2025-10-01", "2025-10-07", "Week 1 (Oct 01-Oct 07)"),
      ("2025-10-08", "2025-10-14", "Week 2 (Oct 08-Oct 14)"),
      ("2025-10-15", "2025-10-21", "Week 3 (Oct 15-Oct 21)"),
      ("2025-10-22", "2025-10-28", "Week 4 (Oct 22-Oct 28)"),
      ("2025-10-29", "2025-10-31", "Week 5 (Oct 29-Oct 31)")]

Per window: build_data_block() → extract:
  ticket_count, delta_pct, CSAT avg, top_3_terms,
  rising_terms, incident_flag_count

Formatted output (injected via {temporal_context} in 4 prompt templates):
  Week 1 (Oct 01-Oct 07): 245 tickets
      CSAT avg: 3.71
      Top terms: "billing", "subscription", "renewal"
      Rising: "sync_error", "timeout"
  Week 2 (Oct 08-Oct 14): 312 tickets (+27.3%)
      CSAT avg: 3.45
      Top terms: "billing", "login", "sync_error"
      Rising: "password_reset", "2fa"
      Incident flags: 2
```

### K.2 Data-Grounded Follow-Up Chat (T8)

```
User: "Show me billing resolution ticket examples from week 2"
                              │
                              ▼
┌─────────── MAIN THREAD ──────────────────────────────────┐
│  _detect_trc_reference("...billing...")                    │
│    → matches "Billing Resolution" label → TRC code        │
│                                                            │
│  _detect_date_reference("...week 2...")                    │
│    → regex: \bweek\s*(\d+)\b = week 2                     │
│    → returns ("2025-10-08", "2025-10-14")                  │
│                                                            │
│  _detect_sample_request("...examples...")                   │
│    → "example" keyword matched → True                      │
│                                                            │
│  drilldown_ctx = {                                         │
│    trc: "billing_resolution",                              │
│    date_start: "2025-10-08", date_end: "2025-10-14",      │
│    wants_samples: True                                     │
│  }                                                         │
└────────────────┬───────────────────────────────────────────┘
                 ▼
┌─────── WORKER THREAD ────────────────────────────────────┐
│  _build_enrichment():                                     │
│    ① build_data_block(db, dates, trc_filter)              │
│      → mini data block (46 tickets, CSAT=3.2, terms)      │
│    ② _build_nlp_context(db, trc, scan_id)                 │
│      → friction dist, sentiment dist, root cause hints    │
│    ③ _pull_samples(db, trc, dates, limit=5)               │
│      → 5 PII-redacted JSONL ticket examples               │
│                                                            │
│  PREPEND enrichment to prompt → CALL Gemini                │
│  → Response with specific metrics, dates, ticket evidence  │
└────────────────────────────────────────────────────────────┘
```

### K.3 Cross-Build Integration Map

```
Build 6.0 (VOC)          Build 6.2 (Health)     Build 6.3 (Canary)
─────────────             ──────────────         ──────────────────
VOCBuilder                Bridge watchdog        bridge.probe()
  ├─ Phase 1 analysis      ├─ _notify_death()     ├─ probe_history
  ├─ Phase 2 synthesis     │   poisons queues     ├─ adaptive thresholds
  │   injects model_health ├─ record_stall()      ├─ set_probe_floor()
  └─ plan + cost estimate  │   escalation ≥3      └─ {model_health_context}
                           └─ Supervisor checks        → VOC synthesis
                                every 5s

                    Build 7.0 (Report Engine)
                    ─────────────────────────
                    ReportBridgeClient (T1)
                      └─ wraps GeminiBridge (same as 6.2/6.3)
                    ReportOrchestrator (T2)
                      ├─ pool of 3 GeminiBridges
                      └─ VOC Phase 1 parallel (T4)
                    Temporal Context (T6-T7)
                      └─ {temporal_context} in 4 prompts
                    Data-Grounded Chat (T8)
                      ├─ TRC + date + sample detection
                      └─ live DB enrichment
                    Lifecycle (T9)
                      └─ lazy boot → shared → cleanup

                    Build 8.5 (Shared Resilience)
                    ─────────────────────────────
                    ReportOrchestrator rewritten (~370 lines)
                      ├─ T1 Canary probes on boot
                      │    └─ reuses bridge.probe() from 6.3
                      │    └─ adaptive floor + timeout → RateGovernor
                      ├─ T2 Stall escalation
                      │    └─ wires record_stall()/record_success() from 6.2
                      │    └─ 3 consecutive → bridge.restart()
                      ├─ T3 Retry budget with requeue
                      │    └─ failed tasks → shared queue (any worker)
                      │    └─ 3× stall / 6× quota / 1× other
                      ├─ T4 Queue-pull dispatch
                      │    └─ shared Queue replaces round-robin
                      │    └─ eliminates head-of-line blocking
                      └─ T6 4 bridges (matches NLP scan orchestrator)
                    RateGovernor (T5)
                      ├─ two-tier recovery (10% at 5, 20-30% at 10)
                      └─ _last_rate_limit_time → aggressive/cautious

                    Build 9.0 (Multi-Perspective VOC)
                    ─────────────────────────────────
                    VOCBatchPacker (174 lines, NEW)
                      └─ 127 TRCs → ~8 batches (greedy first-fit)
                    VOCBuilder rewritten (~1,330 lines)
                      ├─ Pipelined priority dispatch
                      │    └─ uses ReportOrchestrator.run_parallel() from 8.5
                      │    └─ PriorityQueue + on_complete + drain_event
                      ├─ Phase 1: ~8 batched analysis tasks (priority 0)
                      │    └─ batch_parser_loop parses → trc_analyses
                      │    └─ split-on-fail requeues missing TRCs
                      ├─ Accumulator: 8-round evidence ledger (priority 1)
                      │    └─ fills Phase 1 whitespace, never starves P0
                      │    └─ append-only ledger + model synthesis scratchpad
                      ├─ 3 Specialists (priority 0, after phase1_done)
                      │    ├─ Pattern Detector → §2, §4
                      │    ├─ Novelty Scanner → §3
                      │    └─ Friction Scorer → §5, §6
                      ├─ Convergence (run_resilient)
                      │    └─ ledger + synthesis + 3 specialists → §1-§7
                      └─ Graceful degradation at every level
                    6 new prompt templates (441 lines total)
                    GeminiBridgeWrapper: +_send_lock for thread safety
```
