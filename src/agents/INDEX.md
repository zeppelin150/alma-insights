# src/agents/

> Agentic NLP pipeline for ticket classification and VOC report generation. Contains persistent Gemini bridge wrappers, worker agents with tool-use loops, orchestrators for parallel execution, and supporting infrastructure (rate limiting, batch packing, stream parsing).

## Module Index

### analyst_agent.py
> Post-scan LLM agent that performs cross-TRC synthesis, quality audits, novelty validation, and pattern merge suggestions after all classification batches complete.

**Public API:**
- `AnalystAgent(bridge, db_path)` — constructor; accepts a GeminiBridge instance and SQLite path
- `AnalystAgent.run_cross_trc_synthesis(scan_id)` — find shared root causes and correlated patterns across TRCs
- `AnalystAgent.run_quality_audit(scan_id, sample_size=25)` — grade classification accuracy on a random sample
- `AnalystAgent.run_novelty_validation(scan_id, input_budget=None)` — validate that novel patterns are genuinely new, with batched processing
- `AnalystAgent.apply_novelty_verdicts(scan_id, validations)` — write DUPLICATE/MERGE verdicts back to classifications table
- `AnalystAgent.run_pattern_merge(scan_id)` — suggest merges between similar sub-patterns
- `AnalystAgent.shutdown()` — close database connection

**Depends on:** *(no src/ imports; uses bridge passed in at construction)*
**Depended by:** `src.agents.scan_orchestrator`, `tests.test_pipeline_full`

---

### batch_packer.py
> Dynamic dual-constraint batch sizing (output budget + input budget) with per-TRC learning via exponential moving average, adapted to the selected Gemini model.

**Public API:**
- `BatchPacker(db_connection, model=None)` — constructor; loads learned TRC profiles from SQLite
- `BatchPacker.compute_batch_size(trc, ticket_count, avg_thread_chars=0) -> int` — compute optimal batch size under dual constraints
- `BatchPacker.get_input_budget() -> int` — return input char budget for mixed-batch packing
- `BatchPacker.record_result(trc, ticket_count, output_chars)` — record actual output ratio for EMA learning
- `BatchPacker.halve_for_retry(original_size) -> int` — halve batch size for retry, respecting minimum
- `BatchPacker.get_profile(trc)` — return current chars_per_ticket estimate for a TRC
- `BatchPacker.get_all_profiles() -> dict` — return all learned TRC profiles

**Module-level constants:**
- `MODEL_OUTPUT_LIMITS` — dict of model name to output char limit
- `MODEL_MAX_BATCH` — dict of model name to max batch size
- `MODEL_INPUT_LIMITS` — dict of model name to input char limit
- `DEFAULT_INPUT_BUDGET` — fallback input budget (100,000)
- `MIN_BATCH_SIZE` — minimum batch size (5)

**Depends on:** *(no src/ imports)*
**Depended by:** `src.agents.scan_orchestrator`, `src.agents.voc_batch_packer`, `src.data.voc_builder`, `tests.test_pipeline_stress`, `tests.test_pipeline_full`, `tests.test_voc_build9`

---

### csv_reformatter.py
> Agentic CSV column mapper that analyzes arbitrary CSV headers and sample data, proposes mappings to a target schema via Gemini or COLUMN_MAP fallback, with caching and PII masking.

**Public API:**
- `MappingResult` — dataclass holding mapping results
  - `MappingResult.is_valid -> bool` — True if both required fields have high-confidence mappings
  - `MappingResult.all_high_confidence -> bool` — True if every mapping is high-confidence
  - `MappingResult.get_column_override() -> Dict[str, str]` — return column override dict for ingest_csv()
  - `MappingResult.get_mapped_field_count() -> int` — number of target fields successfully mapped
- `CSVReformatter(cache_dir=None)` — constructor; defaults cache to data/
- `CSVReformatter.analyze_csv(file_path, bridge_client=None) -> MappingResult` — analyze CSV headers and return a mapping proposal

**Module-level constants:**
- `TARGET_SCHEMA` — list of 13 target field definitions
- `REQUIRED_FIELDS` — set of required field names
- `ALL_TARGET_FIELDS` — list of all target field names

**Depends on:** `src.data.csv_ingestion` (COLUMN_MAP, via lazy import)
**Depended by:** `src.ui.pages.conversation_search`, `src.ui.dialogs.mapping_preview_dialog`, `tests.test_csv_reformatter`, `tests.test_voc_pipeline_live`, `tests.run_csv_import_integration`

---

### acp_bridge.py
> Drop-in replacement for the earlier GeminiBridge. Communicates with Gemini CLI via its official ACP (Agent Client Protocol) mode (`gemini --acp`). JSON-RPC 2.0 over stdio. Thread-safe — reader thread dispatches events to per-request queues.

**Public API:**
- `BridgeEvent(id, type, data)` — parsed event from ACP stdout JSON-RPC stream
  - `BridgeEvent.is_terminal -> bool` — True if event is done/error/stopped
  - `BridgeEvent.is_error -> bool` — True if event is an error
  - `BridgeEvent.is_recoverable -> bool` — True if error is recoverable
  - `BridgeEvent.error_code -> str` — error code string
- `ACPBridge(bridge_script=None, node_path=None, model=None)` — constructor (bridge_script/node_path accepted but ignored for backward compat)
- `ACPBridge.ensure_running()` — boot the ACP subprocess if not alive
- `ACPBridge.call_streaming(prompt, request_id, on_token=None, timeout=300) -> dict` — streaming prompt call returning full_text, elapsed_ms, turns, events, error
- `ACPBridge.call_blocking(prompt, request_id, timeout=300) -> str` — blocking call returning full response text
- `ACPBridge.abort(request_id)` — cancel an in-flight call
- `ACPBridge.ping(timeout=10) -> dict` — health check returning status, uptime, etc.
- `ACPBridge.probe(timeout=30) -> dict` — API-level canary through full Gemini round-trip
- `ACPBridge.is_alive() -> bool` — check if bridge process is running
- `ACPBridge.shutdown(timeout=10)` — graceful shutdown
- `ACPBridge.restart()` — shutdown and reboot
- `ACPBridge.set_on_death(callback)` — register callback for unexpected death
- `ACPBridge.record_stall() -> bool` — record stall event; returns True if escalation triggered
- `ACPBridge.record_success()` — record successful call, reset stall counter
- `ACPBridge.get_stats() -> dict` — bridge stats including health monitor fields
- `ACPBridge.boot_count -> int` — number of boots
- `ACPBridge.total_calls -> int` — total calls made
- `ACPBridge.last_error -> str` — last error code

**Module-level constants:**
- `RECOVERABLE_ERRORS` — frozenset of recoverable error codes
- `FATAL_ERRORS` — frozenset of fatal error codes
- `TERMINAL_EVENTS` — frozenset of terminal event types

**Depends on:** `src.data.pat_store` (lazy import for API key)
**Depended by:** `src.agents.scan_orchestrator`, `src.agents.worker_agent`, `src.agents.report_bridge_client`, `src.agents.report_orchestrator`, `tests.test_acp_bridge`, `tests.test_pipeline_full`, `tests.test_pipeline_stress`

---

### rate_governor.py
> Thread-safe token bucket rate limiter shared across all worker agents, with adaptive tightening on sustained success and exponential backoff on 429 errors.

**Public API:**
- `RateGovernor(min_interval=25.0)` — constructor; starts conservative
- `RateGovernor.acquire(timeout=120) -> bool` — block until safe to make an API call
- `RateGovernor.report_success(duration_seconds)` — report successful call; triggers two-tier tightening
- `RateGovernor.report_rate_limit()` — report 429/quota error; doubles interval
- `RateGovernor.set_probe_floor(floor_seconds)` — set minimum interval floor from canary probe data
- `RateGovernor.report_error()` — report non-rate-limit error
- `RateGovernor.get_throughput() -> float` — measured calls per minute over last 5 minutes
- `RateGovernor.estimate_completion(remaining_batches) -> dict` — time estimate with seconds, confidence, interval, avg_call_duration, effective_interval

**Depends on:** *(no src/ imports)*
**Depended by:** `src.agents.scan_orchestrator`, `src.agents.report_orchestrator`, `tests.test_pipeline_stress`, `tests.test_pipeline_full`, `tests.test_report_orchestrator`, `tests.test_voc_build9`

---

### report_bridge_client.py
> Drop-in replacement for GeminiClient that routes generate() calls through a persistent GeminiBridge subprocess, with mandatory PII redaction and GeminiClient-compatible interface.

**Public API:**
- `ReportBridgeClient(model="gemini-2.5-flash", pii_redaction=True, cli_path="", temperature=0.2)` — constructor
- `ReportBridgeClient.is_available() -> bool` — check if bridge can be booted
- `ReportBridgeClient.generate(prompt: str, system_prompt: str = "", timeout: int = 120) -> str` — send prompt through bridge, return response
- `ReportBridgeClient.shutdown()` — kill the bridge subprocess

**Depends on:** `src.agents.acp_bridge` (lazy), `src.gemini.gemini_client` (PII redaction)
**Depended by:** `src.gemini.client_factory`, `tests.test_voc_pipeline_live`, `tests.test_voc_full_e2e`

---

### report_orchestrator.py
> Manages a pool of persistent GeminiBridge subprocesses for parallelized report generation with priority queue dispatch, canary probes, stall escalation, retry budgets, and rate governing.

**Public API:**
- `ReportOrchestrator(db_path, model="gemini-2.5-flash", num_bridges=4, min_rate_interval=25.0)` — constructor
- `ReportOrchestrator.boot()` — start the bridge pool (idempotent)
- `ReportOrchestrator.shutdown()` — kill all bridges
- `ReportOrchestrator.cancel()` — signal cancellation
- `ReportOrchestrator.run_parallel(tasks, progress_cb=None, task_queue=None, on_complete=None, drain_event=None) -> dict` — execute tasks across bridge pool with priority dispatch
- `ReportOrchestrator.run_single(prompt, request_id=None, timeout=300) -> str` — single blocking call through bridge[0]
- `ReportOrchestrator.run_resilient(prompt, request_id=None, timeout=None) -> str` — single call with full resilience (retry, stall escalation)
- `ReportOrchestrator.get_stats() -> dict` — orchestrator health stats

**Depends on:** `src.agents.rate_governor`, `src.agents.acp_bridge` (both lazy imports)
**Depended by:** `src.ui.pages.ai_reports`, `tests.test_report_orchestrator`, `tests.test_voc_build9`, `tests.test_voc_full_e2e`

---

### scan_orchestrator.py
> Top-level coordinator for the agentic NLP classification pipeline, managing N persistent workers, a supervisor, an analyst agent, rate governor, and batch packer with drop-in ScanWorkerManager-compatible API.

**Public API:**
- `ScanOrchestrator(db_path=None, num_workers=3)` — constructor
- `ScanOrchestrator.start_scan(date_start, date_end, trc_filter=None, batch_size=25, budget_cap=50.0, parallel_workers=3, mode='full') -> dict` — create scan, partition batches, boot workers, start classification
- `ScanOrchestrator.get_status(scan_id=None) -> dict` — read scan progress from SQLite + live supervisor state
- `ScanOrchestrator.pause_scan(scan_id) -> dict` — pause scan; workers check on next batch
- `ScanOrchestrator.resume_scan(scan_id, budget_cap=None) -> dict` — resume a paused scan
- `ScanOrchestrator.cancel_scan(scan_id) -> dict` — cancel scan; workers exit gracefully
- `ScanOrchestrator.get_history(limit=20) -> list[dict]` — return recent scan history
- `ScanOrchestrator.shutdown()` — shutdown all bridges and workers
- `ScanOrchestrator.retry_failed_batches(scan_id)` — re-run only failed batches from a completed scan

**Depends on:** `src.agents.acp_bridge`, `src.agents.worker_agent`, `src.agents.supervisor`, `src.agents.analyst_agent`, `src.agents.rate_governor`, `src.agents.batch_packer`, `src.data.usage_tracker`, `src.data.settings_manager`, `src.data.db_manager`, `src.data.nlp_meta_analyzer`
**Depended by:** `src.data.smart_pipeline`, `src.ui.pages.trc_analytics`, `tests.test_pipeline_stress`, `tests.test_pipeline_full`, `tests.test_nlp_full_e2e`, `tests.run_e2e_full_scan`, `tests.debug_live_scan`, `tests.bench_scan`, `tests.bench_53`

---

### stream_parser.py
> Stateful parser for fenced code block events (tool_call, classification, batch_complete) embedded in the LLM's streaming text output, with support for partial chunks, JSONL, and malformed JSON recovery.

**Public API:**
- `StreamEvent` — enum: TEXT, TOOL_CALL, CLASSIFICATION, BATCH_COMPLETE, ERROR
- `ParsedEvent(event_type, data=None, raw="")` — a single parsed event
  - `ParsedEvent.event_type` — StreamEvent enum value
  - `ParsedEvent.data` — parsed dict for structured events
  - `ParsedEvent.raw` — raw text content
- `StreamParser()` — constructor
- `StreamParser.reset()` — reset parser state for a new batch
- `StreamParser.feed(chunk) -> yields ParsedEvent` — feed a text chunk and yield parsed events
- `StreamParser.flush() -> yields ParsedEvent` — flush remaining buffered content
- `StreamParser.is_in_fence -> bool` — True if currently inside a fenced block
- `StreamParser.pending_fence_type -> str` — fence type being parsed, or None

**Module-level constants:**
- `FENCE_MAP` — dict mapping fence type strings to StreamEvent enums

**Depends on:** *(no src/ imports)*
**Depended by:** `src.agents.worker_agent`, `tests.test_pipeline_stress`, `tests.test_pipeline_full`, `tests.debug_classify`

---

### supervisor.py
> Deterministic Python health monitor (no LLM, no latency) that polls worker health against thresholds, restarts degraded workers, updates scan_progress for UI polling, and provides time estimates via RateGovernor.

**Public API:**
- `Supervisor(workers, rate_governor, db_path, scan_id=None)` — constructor
- `Supervisor.set_scan(scan_id, total_batches, total_tickets)` — configure for a new scan
- `Supervisor.run()` — main supervisor loop; blocks until scan complete or stopped
- `Supervisor.stop()` — signal the supervisor to stop
- `Supervisor.notify_batch_complete(worker_id, result)` — called by orchestrator when a batch finishes
- `Supervisor.get_time_estimate() -> dict` — precise time estimate from RateGovernor
- `Supervisor.get_status() -> dict` — current supervisor status

**Depends on:** *(no src/ imports)*
**Depended by:** `src.agents.scan_orchestrator`, `tests.test_pipeline_stress`, `tests.test_pipeline_full`

---

### tool_registry.py
> Registry of 7 tools available to worker agents during classification (query_taxonomy, get_stats_context, store_classification, flag_for_review, get_full_thread, report_progress, check_cross_trc), backed by SQLite queries with field validation.

**Public API:**
- `Tool(name, description, parameters, handler)` — definition of a single tool
- `ToolRegistry(db_path)` — constructor; registers all 7 tools
- `ToolRegistry.set_context(**kwargs)` — set per-batch context (scan_id, batch_id, trc, agent_id)
- `ToolRegistry.execute(name, args) -> dict` — execute a tool by name
- `ToolRegistry.get_prompt_description() -> str` — generate tool documentation for system prompt
- `ToolRegistry.get_available_tools() -> list[str]` — return list of tool names
- `ToolRegistry.close()` — close database connection
- `ToolRegistry.call_count -> int` — number of tool calls made

**Module-level constants:**
- `VALID_FRICTION` — frozenset of valid friction types
- `VALID_ANOMALY` — frozenset of valid anomaly flags
- `VALID_POLARITY` — frozenset of valid sentiment polarities
- `VALID_SENTIMENT_RANGE` — tuple (1, 5)

**Depends on:** *(no src/ imports)*
**Depended by:** `src.agents.worker_agent`, `tests.test_pipeline_stress`, `tests.test_pipeline_full`, `tests.test_pipeline_live`, `tests.debug_classify`

---

### voc_batch_packer.py
> TRC-level bin-packing for batched VOC analysis using a greedy decreasing-first-fit heuristic, with 25% headroom reserve and intra-TRC chunking for oversized TRCs.

**Public API:**
- `VOCBatchPacker(model="gemini-2.5-flash")` — constructor; looks up input budget from batch_packer constants
- `VOCBatchPacker.input_budget -> int` — effective input budget in chars after headroom and overhead
- `VOCBatchPacker.pack_trcs(trc_sizes, trc_ticket_counts=None) -> list[dict]` — bin-pack TRCs into batch specs with chunk_info

**Module-level constants:**
- `BATCH_PROMPT_OVERHEAD` — overhead for batch prompt template (25,000 chars)
- `TRC_SECTION_OVERHEAD` — overhead per TRC section (500 chars)
- `HEADROOM_FACTOR` — token headroom factor (0.75)

**Depends on:** `src.agents.batch_packer` (MODEL_INPUT_LIMITS, DEFAULT_INPUT_BUDGET)
**Depended by:** `src.data.voc_builder`, `tests.test_voc_build9`

---

### worker_agent.py
> Persistent Gemini worker with tool-use loop that boots the bridge once and stays alive across batches, processing ticket classification via streaming with fenced code block protocol and per-ticket persistence.

**Public API:**
- `WorkerAgent(agent_id, bridge, db_path)` — constructor
- `WorkerAgent.classify_batch(batch_payload) -> dict` — classify a batch of tickets; returns classified, failed, tool_calls, elapsed_seconds, error, batch_id
- `WorkerAgent.needs_reset() -> bool` — True if estimated context tokens exceed limit
- `WorkerAgent.reset()` — restart bridge and reset health metrics
- `WorkerAgent.get_health() -> dict` — health metrics for supervisor monitoring
- `WorkerAgent.shutdown()` — clean shutdown of tool registry connection
- `WorkerAgent.agent_id` — worker identifier
- `WorkerAgent.batches_processed` — lifetime batch count
- `WorkerAgent.tickets_classified` — lifetime classified count
- `WorkerAgent.tools_called` — lifetime tool call count
- `WorkerAgent.parse_rate` — EMA parse success rate
- `WorkerAgent.avg_confidence` — running average confidence
- `WorkerAgent.status` — idle, active, degraded, or stalled

**Depends on:** `src.agents.acp_bridge`, `src.agents.stream_parser`, `src.agents.tool_registry`
**Depended by:** `src.agents.scan_orchestrator`, `tests.test_pipeline_stress`, `tests.test_pipeline_live`, `tests.debug_classify`, `tests.debug_mixed_batch`
