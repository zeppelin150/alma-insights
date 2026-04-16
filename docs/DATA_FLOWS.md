# Alma Insights — Data Flow Documentation

## Overview

Data flows through 6 stages from ingestion to export:

```
1. IMPORT ──► 2. STORE ──► 3. CLASSIFY ──► 4. ANALYZE ──► 5. REPORT ──► 6. EXPORT
   CSV/API      SQLite       NLP Scan       Stats engines   AI synthesis   Google Drive
```

---

## Flow 1: Data Import

### Import Paths

Two ingestion paths, both now **additive** (dedup by ticket_id — only new tickets are inserted):

```
Path A: CSV File
  │
  ├── CSVReformatter.analyze_csv()          [optional: AI column mapping]
  │     src/agents/csv_reformatter.py
  │     Uses Gemini or COLUMN_MAP fallback
  │
  └── csv_ingestion.ingest_csv()
        src/data/csv_ingestion.py
        1. Parse CSV with column mapping
        2. Group rows by ticket_id
        3. Build ticket/comment dicts
        4. Dedup: get_existing_ticket_ids() filters already-imported tickets
        5. Write new tickets, comments, conversations to DB
        6. Extract entities (payer, product_area)
        7. Rebuild FTS index

Path B: Lightdash API
  │
  └── lightdash_client.run_ingestion()
        src/data/lightdash_client.py
        1. Parse saved-chart URL
        2. Preflight checks (auth, field mapping)
        3. Chunked data pull with retry/backoff
        4. Write raw rows to raw_ingestion_rows
        5. conversation_rebuild.rebuild_conversations()
             src/data/conversation_rebuild.py
             Groups by ticket_id, sorts chronologically
             Dedup: skips tickets already in DB
        6. Entity extraction
        7. FTS index rebuild
```

### Multi-Source Architecture

When source registry is configured (migration 011+), imports also write to per-source tables:

```
csv_ingestion / lightdash_client
  ├── Shared tables:     tickets, comments, conversations
  └── Per-source tables: {prefix}_tickets, {prefix}_conversations, {prefix}_comments, {prefix}_fts
```

Source registry: `source_registry` table. Default: `zendesk_default`.
Dynamic tables created by `schema_builder.create_source_tables()`.

### Import Tracking

`import_runs` table (migration 010) logs each import: source, mode, file, ticket counts, status. Used for audit trail and dedup support via `import_tracker.get_existing_ticket_ids()`.

### Tables Written

| Table | What's Written |
|-------|---------------|
| `tickets` | One row per ticket (upsert by ticket_id) |
| `comments` | One row per comment (upsert by comment_id) |
| `conversations` | One rebuilt thread per ticket |
| `conversations_fts` | FTS5 index (rebuilt after import) |
| `ticket_entities` | Extracted payer/product entities |
| `{prefix}_*` | Per-source copies when source registry active |
| `import_runs` | Import audit log |

---

## Flow 2: NLP Classification Scan

### Trigger

UI: TRC Analytics page → "Start Scan" button → `ScanOrchestrator.start_scan()`

### Pipeline

```
ScanOrchestrator.start_scan(date_start, date_end, trc_filter, ...)
  │
  ├── 1. Create scan record in nlp_scan_runs
  │
  ├── 2. Query tickets in date range
  │     Dedup gate: ticket_index_writer.should_classify_ticket()
  │       'classify'       → needs LLM classification
  │       'skip'           → already done in this scan
  │       'update_scan_id' → good existing, just update reference
  │
  ├── 3. BatchPacker partitions tickets into batches
  │     Dual constraint: output budget + input budget
  │     Per-TRC EMA learning from prior batches
  │     Records: nlp_batches (one row per batch)
  │
  ├── 4. Boot N WorkerAgents (each with ACPBridge)
  │
  ├── 5. Worker classification loop:
  │     ┌─────────────────────────────────────────┐
  │     │ For each batch:                          │
  │     │  a. RateGovernor.acquire()               │
  │     │  b. Build prompt (nlp_classify.txt)      │
  │     │  c. Send to ACPBridge                    │
  │     │  d. StreamParser extracts fenced blocks  │
  │     │  e. ToolRegistry executes tool_calls     │
  │     │  f. Persist to nlp_ticket_classifications│
  │     │  g. Upsert ticket_index                  │
  │     │  h. Report health to Supervisor          │
  │     └─────────────────────────────────────────┘
  │
  ├── 6. Supervisor monitors health, updates scan_progress
  │
  └── 7. Post-scan chain (after all batches complete):
        │
        ├── AnalystAgent.run_cross_trc_synthesis(scan_id)
        │     Finds shared root causes across TRCs
        │
        ├── AnalystAgent.run_quality_audit(scan_id)
        │     Grades classification accuracy on sample
        │
        ├── AnalystAgent.run_novelty_validation(scan_id)
        │     Validates novel patterns are genuinely new
        │
        ├── AnalystAgent.run_pattern_merge(scan_id)
        │     Suggests merges between similar sub-patterns
        │
        ├── post_scan_persist.write_scan_category_snapshot()
        │     Aggregates to scan_category_snapshots
        │
        ├── post_scan_persist.write_trend_deltas()
        │     Compares vs prior scan → trend_snapshots
        │
        ├── entity_normalizer → ticket_entities_normalized
        │
        ├── enriched_trends writer
        │
        └── ticket_theme_tagger → ticket_theme_tags
```

### Stream Protocol

LLM output contains fenced code blocks that `StreamParser` extracts:

```
```tool_call
{"name": "store_classification", "args": {...}}
```​

```classification
{"ticket_id": "T-1234", "sub_cluster": "...", ...}
```​

```batch_complete
{"classified": 25, "tool_calls": 12}
```​
```

### Tables Written

| Table | What's Written |
|-------|---------------|
| `nlp_scan_runs` | Scan metadata |
| `nlp_batches` | Batch records |
| `nlp_batch_tickets` | Batch → ticket mapping |
| `nlp_ticket_classifications` | Per-ticket classification (append-only) |
| `ticket_index` | Deduplicated latest classification (**source of truth**) |
| `sub_patterns` | Discovered sub-patterns |
| `sub_pattern_ngrams` | Pattern fingerprints |
| `sub_pattern_snapshots` | Per-scan pattern metrics |
| `nlp_findings` | Cross-TRC findings |
| `analyst_reports` | Post-scan analyst reports |
| `agent_health` | Worker health during scan |
| `scan_progress` | Progress for UI polling |
| `review_flags` | Tickets flagged for review |
| `scan_category_snapshots` | Post-scan distributions |
| `trend_snapshots` | Deltas vs prior scan |
| `ticket_entities_normalized` | Normalized entities |
| `enriched_trends` | Aggregated trend stats |
| `ticket_theme_tags` | Theme → ticket links |
| `gemini_usage` | API cost tracking |

---

## Flow 3: Statistical Analysis

### Trending Engine

`src/data/trending_engine.py` — 12-step pipeline triggered from Trending Topics page.

```
trending_engine.run_analysis(db, date_from, date_to, trc_filter)
  │
  ├── Step 1:  Load conversations from DB
  ├── Step 2:  Tokenize + domain stopword removal
  ├── Step 3:  Concept normalization (synonym → canonical)
  ├── Step 4:  Compound term discovery (PMI bigrams/trigrams)
  ├── Step 5:  TF-IDF vectorization (per-period)
  ├── Step 6:  Rising/falling term detection (chi-squared)
  ├── Step 7:  VADER sentiment analysis
  ├── Step 8:  Temporal correlation (lead-lag analysis)
  ├── Step 9:  Topic clustering (KMeans on TF-IDF vectors)
  ├── Step 10: CUSUM drift detection
  ├── Step 11: Z-score anomaly detection
  └── Step 12: Final assembly → return results dict
```

**Memory note**: Steps 1-12 hold 3-4 copies of the full dataset in memory (raw text, tokens, TF-IDF vectors, DataFrames). Large datasets can cause OOM. See `docs/ARCHITECTURE_KERNEL.md` for scaling analysis.

### Incident Engine

`src/data/incident_engine.py` — Two-tier Poisson SPC.

```
run_incident_scan(db, target_date)
  │
  ├── Compute daily ticket counts per TRC
  ├── Build Poisson baselines (lambda, theta-1, theta-2)
  ├── Check daily counts against thresholds
  ├── CUSUM drift detection (cumulative sum)
  ├── Write incident_flags for spikes
  └── Correlate with recent interventions
```

Tables written: `daily_counts`, `trc_baselines`, `hourly_baselines`, `incident_flags`

### Theta Engine

`src/data/theta_engine.py` — Z-score anomaly detection across multiple metrics.

Tables written: `daily_baselines`, `rolling_stats`, `anomaly_flags`

### Watchlist Engine

`src/data/watchlist_engine.py` — Custom pattern monitoring.

Tables read: `watchlist_rules`. Tables written: `watchlist_alerts`.

---

## Flow 4: AI Report Generation

### AI Report Pipeline

`src/data/ai_report_pipeline.py` — triggered from AI Reports page.

```
AIReportPipeline.run(prompt_data, date_start, date_end, trc_filter)
  │
  ├── Phase 1: Data Assembly
  │     Build data block from ticket_index, trending results, analyst reports
  │     Generate tech summary
  │
  ├── Phase 2: LLM Generation
  │     Send assembled data + user prompt to Gemini
  │     Template from prompt_library or config/prompts/
  │
  └── Phase 3: Assembly
        Combine report_md + tech_summary_md + analyst_md
        Save to analysis_reports table
```

### VOC Builder Pipeline

`src/data/voc_builder.py` (2,285 LOC) — Multi-phase Voice of Customer analysis.

```
VOCBuilder.run(date_start, date_end, scan_id)
  │
  ├── Phase 1: Batched TRC Analysis (priority 0)
  │     VOCBatchPacker bin-packs TRCs into batches
  │     Each batch → Gemini via ReportOrchestrator
  │     Template: voc_analysis_batch.txt
  │
  ├── Phase 2a: Accumulator (priority 1, pipelined)
  │     6-8 sequential rounds building evidence ledger
  │     Template: voc_accumulator.txt
  │
  ├── Phase 2b: Specialists (after Phase 1, priority 0)
  │     Pattern Detector  → voc_pattern_detector.txt
  │     Novelty Scanner   → voc_novelty_scanner.txt
  │     Friction Scorer   → voc_friction_scorer.txt
  │
  └── Phase 3: Convergence
        Accumulator ledger + 3 specialist reports
        → voc_convergence.txt → executive report
```

All phases dispatched through `ReportOrchestrator.run_parallel()` with priority queue.

### Smart Report Pipeline

`src/data/smart_pipeline.py` — Automated scheduled reporting.

### A/B Comparison Pipeline

`src/data/ab_report_pipeline.py` — Dual-dataset comparison using chi-squared, Mann-Whitney U, and t-tests.

---

## Flow 5: Chat / Interactive Analysis

### Chat Architecture

```
User input
  │
  ▼
ChatWidget (src/ui/widgets/chat_widget.py)
  │
  ▼
ChatEngine (src/services/chat_engine.py)
  │
  ├── Build prompt with context injection
  │     context_injector.py: recent scan results, trending data, ticket stats
  │
  ├── Select LLM client
  │     build_client_for_task() or warm client
  │
  ├── Send to LLM
  │
  ├── Parse response for TOOL_CALL patterns (if tools_enabled)
  │     Match: TOOL_CALL: tool_name {args_json}
  │     Execute against local DB via chat_tools/
  │     Resubmit result (max 3 round-trips)
  │
  └── Return response + telemetry
        Store in chat_messages table
```

### Chat Tool Categories

Tools in `src/data/chat_tools/`:
- **Fast path**: Quick lookups (ticket count, TRC list)
- **Thread**: Retrieve and display conversation threads
- **Report**: Access saved analysis reports
- **Semantic**: Embedding-based similarity search

### MCP Servers

- `src/mcp/alma_mcp_server.py`: Classification tools (stdio JSON-RPC)
- `src/mcp/chat_mcp_server.py`: Chat tools (stdio JSON-RPC)

Tables read: `ticket_index`, `sub_patterns`, `conversations`, `analysis_reports`, `ticket_embeddings`
Tables written: `chat_messages`, `chat_tool_executions`, `chat_sessions`

---

## Flow 6: Export

### Google Drive

`src/export/gdrive_export.py` — uploads markdown reports to Google Drive via service account.

Optional dependency: `google-api-python-client`, `google-auth`.

### Report Persistence

All generated reports saved to `analysis_reports` table with full markdown, parameters, duration, ticket count.

---

## PHI Boundary

```
┌──────────────────────────────────────────────────────────┐
│  PHI ZONE — Contains identifiable patient/requester data │
│                                                          │
│  tickets.requester_name, .requester_email                │
│  comments.body, comments.author_name                     │
│  conversations.full_thread                               │
│  {prefix}_tickets, {prefix}_conversations                │
│                                                          │
│  Lives in: src/data/ (DB), src/ui/ (display only)        │
├──────────────────────────────────────────────────────────┤
│  REDACTION BOUNDARY                                      │
│                                                          │
│  RedactionEngine.scrub()                                 │
│    src/data/redaction_engine.py                           │
│    Config: config/redaction_patterns.json                 │
│    Allowlist: config/entities/phi_allowlist.json          │
│                                                          │
│  Applied automatically by:                               │
│    GeminiClient.generate()                               │
│    ClaudeClient.generate()                               │
│    ReportBridgeClient.generate()                         │
├──────────────────────────────────────────────────────────┤
│  PHI-FREE ZONE — Safe for LLM, persistence, export      │
│                                                          │
│  ticket_index (sanitized subject, issue_snippet)         │
│  sub_patterns, nlp_findings                              │
│  analysis_reports, analyst_reports                        │
│  scan_category_snapshots, trend_snapshots                │
│  enriched_trends, ticket_theme_tags                      │
│  scan_ledger (structured context for Claude tools)       │
│                                                          │
│  Lives in: src/agents/ (prompts), src/llm/ (tools),     │
│            src/services/ (persistence), src/export/       │
└──────────────────────────────────────────────────────────┘
```

### Redaction Rules

1. Identify "keep" regions — allowlisted business entities (insurance names, TRC codes)
2. Apply PHI detection patterns (SSN, email, phone, DOB, etc.)
3. Skip matches overlapping keep regions
4. Replace remaining PHI with tokens: `[SSN]`, `[EMAIL]`, `[PHONE]`, etc.

---

## See Also

- `CLAUDE.md` — Quick reference
- `docs/DATABASE.md` — Complete schema reference
- `docs/ARCHITECTURE.md` — System architecture
- `docs/AGENTS.md` — Agentic pipeline deep dive
- `src/data/SCHEMA_CONTRACT.md` — Canonical source rules
- `DESTRUCTIVE_IMPORT_KERNEL.md` — Import architecture history
