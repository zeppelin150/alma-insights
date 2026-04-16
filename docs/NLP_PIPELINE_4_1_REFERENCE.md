# NLP Pipeline 4.1 Architecture Reference

> **Purpose:** Revert map for restoring the pre-5.0 pipeline if the agentic architecture needs rollback.
> **Generated:** 2026-02-21
> **Snapshot of:** Pass 4.1 (post-TRC propagation fix, post-sub-taxonomy injection fix)

---

## A. File Inventory

| File | Lines | Purpose |
|------|-------|---------|
| `src/data/scan_worker.py` | 990 | Detached batch processor (subprocess per worker) |
| `src/data/scan_worker_manager.py` | 415 | Orchestrator / batch partitioner / worker launcher |
| `src/data/smart_pipeline.py` | 478 | Pipeline hub (CLI entry point + full analytics chain) |
| `src/data/nlp_meta_analyzer.py` | 922 | Post-scan Layer 2: sub-taxonomy, n-grams, findings, cross-TRC |
| `src/data/nlp_synthesis.py` | 183 | Post-scan Layer 3: Gemini narrative synthesis |
| `src/gemini/gemini_client.py` | 251 | CLI subprocess wrapper with PII redaction |
| `src/ui/pages/nlp_scanner_page.py` | 1282 | UI controller (scan trigger, progress, findings) |
| `config/prompts/nlp_classify.txt` | ~80 | Classification prompt template |
| `config/prompts/nlp_synthesize.txt` | ~60 | Synthesis prompt template |
| `config/prompts/nlp_drilldown.txt` | ~50 | Single-finding drilldown prompt template |
| `config/settings.yaml` | 82 | NLP scan settings block (lines 64-69) |

---

## B. Data Flow Diagram

```
UI (_start_scan button click)
  |
  v
ScanWorkerManager.start_scan(date_start, date_end, trc_filter, batch_size, budget_cap, parallel_workers, mode)
  |
  |-- 1. Query TRC distribution: SELECT trc_code, COUNT(DISTINCT ticket_id) ... GROUP BY trc_code
  |-- 2. Partition into batches:
  |      Large TRCs (>200 tickets)  -> chunked into multi-batch (chunk_size=200)
  |      Medium TRCs (>batch_size/3) -> single batch
  |      Small TRCs               -> packed into mixed batches (JSON array of TRC codes, cap 200)
  |-- 3. Estimate cost: input_tokens = tickets * 600, output_tokens = tickets * 200
  |-- 4. Create nlp_scan_runs record (status='running')
  |-- 5. Create nlp_batches records (all status='queued')
  |-- 6. Assign batches: worker_id = batch_number % n_workers
  |-- 7. Launch detached subprocesses:
  |      python -m src.data.scan_worker --db <path> --scan <scan_id> --worker <worker_id>
  |      Windows: DETACHED_PROCESS | CREATE_NO_WINDOW
  |      Unix: start_new_session=True
  |
  v
ScanWorker.run() [in detached subprocess]
  |
  |-- Loop until no batches remain:
  |     |
  |     |-- Check scan status (exit if paused/cancelled/failed)
  |     |-- Check budget (exit if actual_cost >= budget_cap)
  |     |-- Claim next batch (atomic UPDATE worker_id WHERE status='queued')
  |     |
  |     v
  |   _process_batch(batch, scan):
  |     |-- _get_tickets_for_batch(batch)
  |     |     |-- Query tickets for TRC(s) with full_thread, subject, trc_code
  |     |     |-- Slice for chunk (offset by trc_chunk index)
  |     |     |-- Build _ticket_trc_map: {ticket_id: trc_code}
  |     |
  |     |-- _build_prompt(batch, tickets):
  |     |     |-- _build_stats_context(trc)  [incident/theta/trending flags]
  |     |     |-- _build_sub_taxonomy(trc)   [active patterns + n-grams]
  |     |     |   OR _build_mixed_sub_taxonomy(trc_list) [for mixed batches]
  |     |     |-- Format ticket JSONL (redaction happens in gemini.generate)
  |     |     |-- Load config/prompts/nlp_classify.txt template
  |     |     |-- Substitute: {trc_path}, {statistical_context}, {sub_taxonomy},
  |     |     |   {ticket_jsonl}, {n_tickets}, {chunk_n}, {chunk_total}, etc.
  |     |
  |     |-- gemini.generate(prompt, system_prompt, timeout)
  |     |     |-- _redact_base(prompt)       [MANDATORY: emails, phones, SSNs]
  |     |     |-- _redact_aggressive(prompt) [OPTIONAL: name heuristic]
  |     |     |-- Write to temp file
  |     |     |-- subprocess.run([node, gemini-cli, --model, ...], input_redirect)
  |     |     |-- Return stdout
  |     |
  |     |-- _parse_response(raw, batch_id, trc):
  |     |     |-- Try: json.loads(raw) as full array
  |     |     |-- Fallback: regex search for JSON array
  |     |     |-- Fallback: _extract_partial_json_objects (brace-depth tracking)
  |     |     |-- Validate fields: friction_type, anomaly_flag, polarity, intensity
  |     |     |-- Per-ticket TRC from _ticket_trc_map (NOT batch-level trc)
  |     |     |-- Returns (records, errors, output_tokens)
  |     |
  |     |-- _store_classifications(records)
  |     |     |-- INSERT OR REPLACE INTO nlp_ticket_classifications
  |     |
  |     |-- _increment_scan_progress(cost)
  |     |-- _update_prior_chunks(batch, records) [for next chunk context]
  |
  |-- Rate limit handling:
  |     |-- Quota errors: up to 6 retries, exponential backoff (up to 120s)
  |     |-- Other errors: up to 3 retries
  |     |-- 5 consecutive errors: status='quota_exhausted'
  |
  |-- _try_finalize_scan(): set status='scan_complete' when all batches done/failed
  |
  v
UI polls ScanWorkerManager.get_status() every 5 seconds
  |-- Reads nlp_scan_runs + current batch from SQLite
  |-- Updates progress bar, cost, TRC label
  |
  v  (on status = 'scan_complete')
NLPMetaAnalyzer.run_analysis(scan_id)  [Layer 2]
  |-- Stage 1: _analyze_within_trc()     -- findings per TRC
  |-- Stage 2: _update_sub_taxonomy()    -- pattern tier lifecycle
  |-- Stage 3: _update_ngrams()          -- dual-source n-gram extraction
  |-- Stage 4: _create_snapshots()       -- per-pattern per-scan snapshots
  |-- Stage 5: _analyze_cross_trc()      -- entity/friction patterns across 3+ TRCs
  |-- Stage 6: _cross_reference_engines() -- validate vs incident/theta flags
  |-- Stage 7: _rank_findings()          -- impact scoring + exemplar selection
  |-- Stage 8: _prune_provisional()      -- HIPAA A7 cleanup
  |-- Set status = 'analysis_complete'
  |
  v  (optional, triggered from AI Reports page)
NLPSynthesizer.synthesize_findings(scan_id)  [Layer 3]
  |-- Load top findings by impact_score
  |-- Pull exemplar tickets (redacted)
  |-- Assemble synthesis prompt (config/prompts/nlp_synthesize.txt)
  |-- Call gemini.generate()
  |-- Store narrative to analysis_reports table
```

---

## C. Function Signature Catalog

### scan_worker.py (990 lines)

```python
class ScanWorker:
    def __init__(self, db_path, scan_id, worker_id=0)
    def _open_conn(self)
    def run(self)
    def _process_batch(self, batch, scan)
    def _build_prompt(self, batch, tickets)
    def _build_stats_context(self, trc) -> str
    def _build_sub_taxonomy(self, trc) -> str
    def _build_mixed_sub_taxonomy(self, trc_list) -> str
    def _parse_response(self, raw, batch_id, trc) -> tuple[list, list, int]
    @staticmethod
    def _extract_partial_json_objects(text) -> list[dict]
    def _store_classifications(self, records)
    def _read_scan(self) -> dict
    def _update_status(self, status)
    def _get_next_batch(self) -> dict|None
    def _count_remaining_batches(self) -> int
    def _count_failed_batches(self) -> int
    @staticmethod
    def _is_quota_error(error_str) -> bool
    def _try_finalize_scan(self)
    def _update_batch_status(self, batch_id, status, **extras)
    def _increment_scan_progress(self, batch_cost)
    def _get_tickets_for_batch(self, batch) -> list[dict]
    def _handle_batch_error(self, batch, error_msg)
    def _update_prior_chunks(self, batch, records)
    def _estimate_tokens(self, text) -> int
    def _estimate_cost(self, input_tokens, output_tokens) -> float
```

### scan_worker_manager.py (415 lines)

```python
class ScanWorkerManager:
    MAX_TICKETS_CLI = 50_000
    MIXED_BATCH_CAP = 200

    def __init__(self, db_path=None)
    def _get_mode(self) -> str
    def start_scan(self, date_start, date_end, trc_filter=None, batch_size=700,
                   budget_cap=50.0, parallel_workers=1, mode='full') -> dict
    def _make_batch(self, scan_id, batch_num, trc, chunk, chunk_total, n_workers) -> dict
    def _launch_worker(self, scan_id, worker_id)
    def get_status(self, scan_id=None) -> dict
    def pause_scan(self, scan_id)
    def resume_scan(self, scan_id, budget_cap=None)
    def cancel_scan(self, scan_id)
    def get_history(self) -> list[dict]
    def _get_conn(self) -> sqlite3.Connection
```

### nlp_meta_analyzer.py (922 lines)

```python
class NLPMetaAnalyzer:
    def __init__(self, db)
    def run_analysis(self, scan_id)
    def _get_scan_trcs(self, scan_id) -> list[str]
    def _analyze_within_trc(self, scan_id, trc)
    def _update_sub_taxonomy(self, scan_id)
    def _try_reactivate(self, trc, label, scan_id, cnt, now) -> bool
    def _promote_patterns(self, scan_id)
    def _demote_patterns(self, scan_id)
    def _retire_patterns(self, scan_id)
    def _update_ngrams(self, scan_id)
    def _redact_ngram(self, text, config) -> str
    def _is_pure_redaction_token(self, text) -> bool
    def _upsert_ngram(self, pattern_id, trc, ngram, n, source, freq, ticket_count, now)
    def _extract_tfidf_ngrams(self, scan_id, pattern_id, trc, label, redaction_config, now)
    def _recompute_specificity(self, trc)
    def _create_snapshots(self, scan_id)
    def _analyze_cross_trc(self, scan_id)
    def _cross_reference_engines(self, finding, scan_id)
    def _compute_impact_score(self, finding) -> float
    def _rank_findings(self, scan_id)
    def _select_exemplars_for_findings(self, scan_id, max_n=10)
    def _prune_provisional(self, scan_id)
```

### nlp_synthesis.py (183 lines)

```python
class NLPSynthesizer:
    def __init__(self, db, gemini_client)
    def synthesize_findings(self, scan_id, max_findings=10) -> str
    def synthesize_single_finding(self, finding_id, user_question=None) -> str
    def _assemble_synthesis_prompt(self, scan, findings_with_exemplars) -> str
    def _assemble_drilldown_prompt(self, finding, exemplar_text, user_question=None) -> str
    def _pull_exemplar_tickets(self, ticket_ids) -> str
```

### gemini_client.py (251 lines)

```python
def _load_redaction_config() -> dict   # module-level, cached

class GeminiClient:
    def __init__(self, cli_path="", model="gemini-2.5-flash", temperature=0.2, pii_redaction=True)
    def _find_cli(self) -> str
    def is_available(self) -> bool
    def _get_api_key(self) -> str
    def generate(self, prompt, system_prompt="", timeout=120) -> str
    def _redact_base(self, text) -> str
    def _redact_aggressive(self, text) -> str
    def _log_outbound(self, prompt, system_prompt)
```

---

## D. Database Tables (NLP, Pass 4.0)

### nlp_scan_runs
```sql
CREATE TABLE IF NOT EXISTS nlp_scan_runs (
    scan_id          TEXT PRIMARY KEY,
    created_at       TEXT NOT NULL,
    status           TEXT NOT NULL,
    date_range_start TEXT NOT NULL,
    date_range_end   TEXT NOT NULL,
    trc_filter       TEXT,
    mode             TEXT NOT NULL,
    batch_strategy   TEXT DEFAULT 'trc',
    total_batches    INTEGER DEFAULT 0,
    completed_batches INTEGER DEFAULT 0,
    total_tickets    INTEGER DEFAULT 0,
    total_comments   INTEGER DEFAULT 0,
    total_input_tokens  INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    estimated_cost_usd  REAL DEFAULT 0.0,
    actual_cost_usd     REAL DEFAULT 0.0,
    budget_cap_usd      REAL DEFAULT 50.0,
    error_log        TEXT,
    config_snapshot   TEXT
);
```

### nlp_batches
```sql
CREATE TABLE IF NOT EXISTS nlp_batches (
    batch_id         TEXT PRIMARY KEY,
    scan_id          TEXT NOT NULL,
    batch_number     INTEGER NOT NULL,
    trc              TEXT NOT NULL,
    trc_chunk        INTEGER DEFAULT 1,
    trc_chunk_total  INTEGER DEFAULT 1,
    status           TEXT NOT NULL,
    ticket_count     INTEGER DEFAULT 0,
    comment_count    INTEGER DEFAULT 0,
    input_tokens     INTEGER DEFAULT 0,
    output_tokens    INTEGER DEFAULT 0,
    cost_usd         REAL DEFAULT 0.0,
    latency_ms       INTEGER DEFAULT 0,
    prompt_version   TEXT,
    prior_chunks_context TEXT,
    raw_response     TEXT,
    error_message    TEXT,
    retry_count      INTEGER DEFAULT 0,
    worker_id        INTEGER DEFAULT 0,
    created_at       TEXT NOT NULL,
    completed_at     TEXT,
    FOREIGN KEY (scan_id) REFERENCES nlp_scan_runs(scan_id)
);
```

### nlp_ticket_classifications
```sql
CREATE TABLE IF NOT EXISTS nlp_ticket_classifications (
    classification_id TEXT PRIMARY KEY,
    batch_id         TEXT NOT NULL,
    scan_id          TEXT NOT NULL,
    ticket_id        TEXT NOT NULL,
    trc              TEXT NOT NULL,
    sub_cluster      TEXT,
    sub_cluster_confidence REAL,
    is_novel         INTEGER DEFAULT 0,
    sentiment_intensity INTEGER,
    sentiment_polarity TEXT,
    friction_type    TEXT,
    anomaly_flag     TEXT,
    anomaly_reason   TEXT,
    entities_json    TEXT,
    key_phrases      TEXT,
    root_cause_hint  TEXT,
    summary          TEXT,
    raw_classification TEXT,
    created_at       TEXT NOT NULL,
    FOREIGN KEY (batch_id) REFERENCES nlp_batches(batch_id),
    FOREIGN KEY (scan_id) REFERENCES nlp_scan_runs(scan_id)
);
```

### sub_patterns
```sql
CREATE TABLE IF NOT EXISTS sub_patterns (
    pattern_id       TEXT PRIMARY KEY,
    trc              TEXT NOT NULL,
    label            TEXT NOT NULL,
    description      TEXT,
    friction_type    TEXT,
    tier             TEXT DEFAULT 'probationary',
    discovered_scan  TEXT NOT NULL,
    discovered_at    TEXT NOT NULL,
    last_seen_scan   TEXT,
    last_seen_at     TEXT,
    lifetime_tickets INTEGER DEFAULT 0,
    lifetime_scans   INTEGER DEFAULT 0,
    merged_into      TEXT,
    UNIQUE(trc, label)
);
```

### sub_pattern_ngrams
```sql
CREATE TABLE IF NOT EXISTS sub_pattern_ngrams (
    ngram_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id       TEXT NOT NULL,
    trc              TEXT NOT NULL,
    ngram            TEXT NOT NULL,
    n                INTEGER NOT NULL,
    source           TEXT DEFAULT 'gemini',
    frequency        INTEGER DEFAULT 1,
    ticket_count     INTEGER DEFAULT 1,
    first_seen       TEXT NOT NULL,
    last_seen        TEXT NOT NULL,
    specificity      REAL DEFAULT 0.0,
    UNIQUE(pattern_id, ngram),
    FOREIGN KEY (pattern_id) REFERENCES sub_patterns(pattern_id)
);
```

### sub_pattern_snapshots
```sql
CREATE TABLE IF NOT EXISTS sub_pattern_snapshots (
    snapshot_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id       TEXT NOT NULL,
    scan_id          TEXT NOT NULL,
    scan_date_start  TEXT NOT NULL,
    scan_date_end    TEXT NOT NULL,
    ticket_count     INTEGER DEFAULT 0,
    pct_of_trc       REAL,
    avg_sentiment    REAL,
    sentiment_dist   TEXT,
    friction_dist    TEXT,
    top_entities     TEXT,
    novel_tickets    INTEGER DEFAULT 0,
    new_ngrams_added INTEGER DEFAULT 0,
    UNIQUE(pattern_id, scan_id),
    FOREIGN KEY (pattern_id) REFERENCES sub_patterns(pattern_id),
    FOREIGN KEY (scan_id) REFERENCES nlp_scan_runs(scan_id)
);
```

### provisional_classifications
```sql
CREATE TABLE IF NOT EXISTS provisional_classifications (
    provisional_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id        TEXT NOT NULL,
    trc              TEXT NOT NULL,
    matched_pattern_id TEXT,
    match_score      REAL,
    match_method     TEXT DEFAULT 'ngram',
    is_confirmed     INTEGER DEFAULT 0,
    confirmed_by_scan TEXT,
    confirmed_pattern_id TEXT,
    created_at       TEXT NOT NULL,
    FOREIGN KEY (matched_pattern_id) REFERENCES sub_patterns(pattern_id)
);
```

### nlp_findings
```sql
CREATE TABLE IF NOT EXISTS nlp_findings (
    finding_id       TEXT PRIMARY KEY,
    scan_id          TEXT NOT NULL,
    finding_type     TEXT NOT NULL,
    scope            TEXT,
    title            TEXT NOT NULL,
    description      TEXT,
    ticket_count     INTEGER,
    pct_of_scanned   REAL,
    avg_sentiment_intensity REAL,
    dominant_friction_type TEXT,
    top_trcs         TEXT,
    top_sub_patterns TEXT,
    top_entities     TEXT,
    date_concentration TEXT,
    temporal_trend   TEXT,
    exemplar_ticket_ids TEXT,
    statistical_validation TEXT,
    baseline_comparison TEXT,
    impact_score     REAL,
    created_at       TEXT NOT NULL,
    FOREIGN KEY (scan_id) REFERENCES nlp_scan_runs(scan_id)
);
```

---

## E. Configuration Snapshot

```yaml
# config/settings.yaml (lines 64-69)
nlp_scan:
  mode: cli
  batch_size: 700
  budget_cap: 50.0
  parallel_workers: 1
  max_tickets_cli: 50000

# Gemini settings used by scan_worker
gemini:
  cli_path: <path-to-gemini-cli>   # e.g. %APPDATA%\npm\gemini.CMD on Windows,
                                    # or /usr/local/bin/gemini on macOS
  model: gemini-2.0-flash
  pii_redaction: true
  temperature: 0.2
```

---

## F. Revert Instructions

If the 5.0 agentic pipeline needs rollback to 4.1:

1. **Swap imports in `src/data/smart_pipeline.py`:**
   ```python
   # Change back to:
   from src.data.scan_worker_manager import ScanWorkerManager
   # And use ScanWorkerManager(db_path=db_path) instead of ScanOrchestrator
   ```

2. **Swap imports in `src/ui/pages/nlp_scanner_page.py`:**
   ```python
   # In _ensure_scan_manager(), change back to:
   from src.data.scan_worker_manager import ScanWorkerManager
   self._scan_manager = ScanWorkerManager(db_path=str(db_path))
   ```

3. **Remove worker status sub-panel** from nlp_scanner_page.py Active Scan card
   (or leave it -- it will just show empty data since agent_health table won't be written to)

4. **Revert `config/settings.yaml`:** Remove `agents:` section (not needed by 4.1)

5. **Leave `src/agents/` on disk** -- no existing code imports from it, so it's inert

6. **New tables are inert:** `agent_health`, `scan_progress`, `review_flags`,
   `analyst_reports`, `trc_batch_profiles` -- no 4.1 code reads or writes them.
   They can remain in the schema without side effects.

7. **Verify retained files are intact:**
   - `src/data/scan_worker.py` (990 lines)
   - `src/data/scan_worker_manager.py` (415 lines)
   Both should be unmodified by the 5.0 build.

8. **Test:** Run a scan via UI to verify 4.1 pipeline works end-to-end.
