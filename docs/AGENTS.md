# Alma Insights — Agentic NLP Pipeline

## Overview

The agentic pipeline classifies support tickets into sub-patterns via LLM. Triggered from the TRC Analytics page ("Start Scan") or Smart Reporting.

**Key design decisions:**
- Persistent workers: bridge boots once, stays alive across batches (no cold-start per batch)
- Per-ticket persistence: each classification stored immediately via `store_classification` tool, so crash recovery is automatic
- NDJSON-primary output: model outputs structured JSON, StreamParser + ToolRegistry parse and store. MCP tools are currently disabled.

---

## Architecture Diagram

```
UI (TRC Analytics page "Start Scan")
  │
  ▼
ScanOrchestrator  ─────────────────────────────────────────────────────
  │                                                                    │
  ├── BatchPacker ─── dual-constraint sizing (output + input budget)   │
  │                   per-TRC EMA learning from prior batches          │
  │                                                                    │
  ├── RateGovernor ── token bucket + adaptive backoff on 429s          │
  │                   shared across all workers                        │
  │                                                                    │
  ├── Worker 0 ──┐                                                     │
  ├── Worker 1 ──┤── each: ACPBridge + ToolRegistry + StreamParser     │
  ├── Worker N ──┘                                                     │
  │                                                                    │
  ├── Supervisor ──── health monitor, progress, time estimates         │
  │                   polls workers, updates scan_progress table       │
  │                                                                    │
  └── AnalystAgent ── post-scan: synthesis, audit, novelty, merge      │
                      uses its own ACPBridge                           │
───────────────────────────────────────────────────────────────────────
```

---

## Component Reference

### ScanOrchestrator (`scan_orchestrator.py`, 2,629 LOC)

Top-level coordinator. Drop-in replacement for legacy `ScanWorkerManager`.

**Public API:**
- `start_scan(date_start, date_end, trc_filter, batch_size, budget_cap, parallel_workers, mode)` — create scan, partition batches, boot workers, start classification
- `get_status(scan_id)` — read progress from SQLite + live supervisor state
- `pause_scan(scan_id)` / `resume_scan(scan_id, budget_cap)` — pause/resume
- `cancel_scan(scan_id)` — cancel; workers exit gracefully
- `get_history(limit)` — recent scan history
- `shutdown()` — shutdown all bridges and workers
- `retry_failed_batches(scan_id)` — re-run only failed batches

**Configuration constants:**
- `MAX_TICKETS_CLI = 5000` — hard cap for CLI mode
- `MAX_BATCH_TICKETS = 25` — default per-batch cap
- `MAX_PARALLEL_WORKERS = 32` — hard ceiling
- `DEFAULT_PARALLEL_WORKERS = 8` — sane default

**Dependencies:** ACPBridge, WorkerAgent, Supervisor, AnalystAgent, RateGovernor, BatchPacker, UsageTracker, settings_manager, db_manager

---

### ACPBridge (`acp_bridge.py`, 1,509 LOC)

Python wrapper for `gemini --acp` (JSON-RPC 2.0 over stdio). Drop-in replacement for the earlier GeminiBridge.

**Transport:** JSON-RPC 2.0 over stdin/stdout of a `gemini --acp` subprocess.
**Auth:** OAuth via cached credentials (`~/.gemini/google_accounts.json`).
**Thread safety:** All public methods are thread-safe. Reader thread dispatches events to per-request queues.

**Public API (mirrors GeminiBridge exactly):**
- `ensure_running()` — boot subprocess if not alive
- `call_streaming(prompt, request_id, on_token, timeout)` → dict with full_text, elapsed_ms, turns, events, error
- `call_blocking(prompt, request_id, timeout)` → str (full response)
- `abort(request_id)` — cancel in-flight call
- `ping(timeout)` → dict — health check
- `probe(timeout)` → dict — API-level canary
- `is_alive()` → bool
- `shutdown(timeout)` / `restart()`
- `set_on_death(callback)` — register crash callback
- `record_stall()` / `record_success()` — health tracking
- `get_stats()` → dict

**Event types:** `content`, `heartbeat`, `tool_call`, `tool_result`, `done`, `error`, `stopped`

**Error categories:**
- Recoverable: `rate_limit`, `server_error`, `network_timeout`, `connection_reset`, `stall_timeout`, `aborted_by_client`
- Fatal: `auth_expired`, `forbidden`, `model_not_found`, `config_error`, `bridge_fatal`

**Process lifecycle:**
- Class-level process registry with `atexit` cleanup
- `__del__` safety net kills orphaned subprocesses
- Watchdog thread monitors health every 3s
- Stall escalation after 3 consecutive stalls

---

### WorkerAgent (`worker_agent.py`, 942 LOC)

Persistent worker with tool-use loop. Boots bridge once, stays alive across batches.

**Classification loop (per batch):**
1. Receive batch payload (TRC + tickets + context)
2. Build system prompt with tool descriptions from ToolRegistry
3. Send to ACPBridge (streaming)
4. StreamParser extracts fenced code blocks
5. ToolRegistry executes `tool_call` results (store_classification, etc.)
6. Collect results until `batch_complete` or stream ends
7. Return batch summary to orchestrator

**Health metrics (read by Supervisor):**
- `batches_processed`, `tickets_classified`, `tickets_failed`
- `tools_called`, `parse_rate` (EMA of successful parses)
- `avg_confidence` (running average)
- `status`: idle, active, degraded, stalled
- `context_tokens_estimate`

**Safety limits:**
- `CONTEXT_TOKEN_LIMIT = 800,000` — trigger bridge reset
- `MAX_TOOL_CALLS_PER_BATCH = 150`
- `MAX_RETRIES_PER_BATCH = 2`
- `MAX_BATCHES_BEFORE_RESET = 20` — ACP `new_session()` resets context per batch

**Prompt template:** `config/prompts/nlp_classify.txt`

---

### Supervisor (`supervisor.py`, 489 LOC)

Deterministic health monitor — no LLM, no latency. Runs in its own thread.

**Responsibilities:**
- Poll worker health against thresholds
- Restart degraded workers (low parse_rate, high stall count)
- Update `scan_progress` table for UI polling
- Provide time estimates via RateGovernor

**Public API:**
- `set_scan(scan_id, total_batches, total_tickets)`
- `run()` — main loop, blocks until scan complete
- `stop()` — signal stop
- `notify_batch_complete(worker_id, result)`
- `get_time_estimate()` / `get_status()`

---

### BatchPacker (`batch_packer.py`, 246 LOC)

Dynamic dual-constraint batch sizing.

**Constraints:**
1. Output budget — model's max output tokens (varies by model)
2. Input budget — model's max input context

**Learning:** Per-TRC exponential moving average of `chars_per_ticket`. Records actual output ratios after each batch.

**Key constants:**
- `MODEL_OUTPUT_LIMITS` — dict of model → output char limit
- `MODEL_INPUT_LIMITS` — dict of model → input char limit
- `MIN_BATCH_SIZE = 5`

**Public API:**
- `compute_batch_size(trc, ticket_count, avg_thread_chars)` → int
- `record_result(trc, ticket_count, output_chars)` — update EMA
- `halve_for_retry(original_size)` → int
- `get_input_budget()` → int

---

### VOCBatchPacker (`voc_batch_packer.py`)

TRC-level bin-packing for batched VOC analysis. Greedy decreasing-first-fit heuristic with 25% headroom reserve and intra-TRC chunking for oversized TRCs.

---

### RateGovernor (`rate_governor.py`, 484 LOC)

Thread-safe token bucket shared across all workers.

**Behavior:**
- Starts conservative (25s minimum interval)
- Two-tier tightening on sustained success (adaptive)
- Exponential backoff on 429 errors (doubles interval)
- Probe floor from canary data

**Public API:**
- `acquire(timeout)` → bool — block until safe to call
- `report_success(duration_seconds)` — trigger tightening
- `report_rate_limit()` — double interval
- `set_probe_floor(floor_seconds)` — set minimum from canary
- `get_throughput()` → float (calls/min over last 5 min)
- `estimate_completion(remaining_batches)` → dict

---

### StreamParser (`stream_parser.py`, 293 LOC)

Stateful parser for fenced code blocks in LLM streaming output.

**Event types (StreamEvent enum):**
- `TEXT` — plain text outside fences
- `TOOL_CALL` — ```` ```tool_call ```` block (JSON)
- `CLASSIFICATION` — ```` ```classification ```` block (JSON)
- `BATCH_COMPLETE` — ```` ```batch_complete ```` block
- `ERROR` — parse failure

**Features:**
- Handles partial chunks across `feed()` calls
- JSONL support within blocks
- Malformed JSON recovery (best-effort)

**Public API:**
- `feed(chunk)` → yields ParsedEvent
- `flush()` → yields remaining
- `reset()` — reset for new batch

---

### ToolRegistry (`tool_registry.py`, 864 LOC)

Registry of 7 classification tools available to workers during NLP scans.

| Tool | Purpose | Writes To |
|------|---------|-----------|
| `query_taxonomy` | Get active sub-patterns + n-gram fingerprints for a TRC | (read-only) |
| `get_stats_context` | Poisson flags, theta anomalies, rising TF-IDF terms | (read-only) |
| `store_classification` | Persist a single ticket classification | `nlp_ticket_classifications`, `sub_patterns`, `sub_pattern_ngrams`, `ticket_index` |
| `flag_for_review` | Flag a ticket for human review | `review_flags` |
| `get_full_thread` | Retrieve full conversation thread | (read-only) |
| `report_progress` | Update UI progress display | `scan_progress` |
| `check_cross_trc` | Check if pattern exists in different TRC | (read-only) |

**Validation constants:**
- `VALID_FRICTION` — frozenset of 14 valid friction types
- `VALID_ANOMALY` — frozenset: normal, unusual, critical
- `VALID_POLARITY` — frozenset: positive, negative, neutral, mixed
- `VALID_SENTIMENT_RANGE` — (1, 5)

**Public API:**
- `set_context(**kwargs)` — set per-batch context (scan_id, batch_id, trc, agent_id)
- `execute(name, args)` → dict
- `get_prompt_description()` → str (for system prompt)
- `get_available_tools()` → list[str]

---

### AnalystAgent (`analyst_agent.py`, 656 LOC)

Post-scan LLM agent that runs after all classification batches complete.

**Phases (run sequentially):**
1. `run_cross_trc_synthesis(scan_id)` — find shared root causes across TRCs
2. `run_quality_audit(scan_id, sample_size=25)` — grade classification accuracy on random sample
3. `run_novelty_validation(scan_id, input_budget)` — validate novel patterns are genuinely new (batched)
4. `apply_novelty_verdicts(scan_id, validations)` — write DUPLICATE/MERGE verdicts
5. `run_pattern_merge(scan_id)` — suggest merges between similar sub-patterns

Tables written: `analyst_reports`

---

### ReportOrchestrator (`report_orchestrator.py`, 521 LOC)

Manages a pool of persistent ACPBridge subprocesses for parallelized report generation.

**Features:**
- Priority queue dispatch
- Canary probes before production calls
- Stall escalation with bridge restart
- Retry budgets per task
- Rate governing via shared RateGovernor

**Public API:**
- `boot()` / `shutdown()` / `cancel()`
- `run_parallel(tasks, progress_cb, task_queue, on_complete, drain_event)` → dict
- `run_single(prompt, request_id, timeout)` → str
- `run_resilient(prompt, request_id, timeout)` → str (with retry + stall escalation)

---

### ReportBridgeClient (`report_bridge_client.py`)

Drop-in replacement for `GeminiClient` that routes `.generate()` calls through an ACPBridge. Mandatory PII redaction. GeminiClient-compatible interface.

---

### CSVReformatter (`csv_reformatter.py`, 639 LOC)

Agentic CSV column mapper. Analyzes arbitrary CSV headers + sample data, proposes mappings to target schema via Gemini or `COLUMN_MAP` fallback. Caching and PII masking.

---

## Scan Lifecycle State Machine

```
                    start_scan()
                        │
                        ▼
┌─────────┐    ┌──────────────┐    ┌───────────┐
│ CREATED │───►│   RUNNING    │───►│ COMPLETED │
└─────────┘    └──────┬───────┘    └───────────┘
                      │
               ┌──────┼──────┐
               ▼      ▼      ▼
          ┌────────┐ ┌────────┐ ┌────────┐
          │ PAUSED │ │ FAILED │ │CANCELLED│
          └───┬────┘ └────────┘ └────────┘
              │
              ▼
         resume_scan()
              │
              ▼
         RUNNING (re-enters)
```

**Batch states:** `pending` → `running` → `completed` / `failed` / `skipped`

**Scan completion triggers:**
1. All batches completed → run post-scan chain (analyst, meta-analyzer, persistence)
2. Budget cap reached → status=completed with note
3. Cancel requested → status=cancelled, workers exit on next batch boundary

---

## Error Handling & Resilience

### Retry Strategy
- Per-batch: `MAX_RETRIES_PER_BATCH = 2`
- On recoverable error: `BatchPacker.halve_for_retry()` reduces batch size
- On fatal error: batch marked as `failed`, worker continues to next

### Stall Detection
- Supervisor polls worker health every poll interval
- Workers track `consecutive_stalls` in ACPBridge
- After `STALL_ESCALATION_THRESHOLD = 3` stalls: bridge restart
- Bridge `record_stall()` returns True when escalation triggered

### Bridge Crash Recovery
- ACPBridge registers all subprocesses for atexit cleanup
- `set_on_death(callback)` notifies orchestrator of unexpected death
- Orchestrator reboots bridge and reassigns batch

### Budget Cap
- `budget_cap_usd` (default 50.0) set in scan config
- UsageTracker accumulates cost per scan
- Orchestrator checks budget before dispatching each batch

---

## MCP Integration

### alma_mcp_server (`src/mcp/alma_mcp_server.py`, 344 LOC)

stdio JSON-RPC server exposing 7 classification tools. Launched as subprocess by ACP's native MCP integration.

**Context (via environment variables):**
- `ALMA_DB_PATH` — SQLite path
- `ALMA_SCAN_ID`, `ALMA_BATCH_ID`, `ALMA_TRC`, `ALMA_AGENT_ID`
- `ALMA_TICKET_TRC_MAP_JSON` — ticket → TRC mapping

**Status:** Currently disabled — NDJSON output is primary classification method. See `mcp-pivot-ndjson.md`.

### chat_mcp_server (`src/mcp/chat_mcp_server.py`, 351 LOC)

stdio JSON-RPC server exposing chat-specific tools for interactive analysis.

**Tools:**
- `query_entities` — Look up tickets by payer/product/feature entity
- `query_ticket_classifications` — Count and group by classification field
- Additional tools for structured data queries

**Context:** `ALMA_DB_PATH` environment variable only.

---

## See Also

- `CLAUDE.md` — Quick reference
- `docs/ARCHITECTURE.md` — System architecture
- `docs/DATA_FLOWS.md` — Data flow documentation (Flow 2: NLP Scan)
- `docs/LLM_INTEGRATION.md` — LLM provider integration
- `docs/DATABASE.md` — Tables written by the pipeline
- `src/agents/INDEX.md` — Full API signatures for all 14 agent modules
