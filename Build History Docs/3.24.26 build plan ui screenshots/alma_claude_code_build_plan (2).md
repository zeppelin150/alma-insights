# Alma Insights — Claude Code Build Plan

## Context

Alma Insights is a PySide6 (Qt for Python) desktop app for RCM issue analysis. It ingests Zendesk/Lightdash ticket data into SQLite, classifies tickets via Gemini CLI (NLP scanner), runs statistical anomaly detection (Poisson SPC, CUSUM, TF-IDF trending), and generates AI reports via a multi-bridge VOC pipeline with specialist orchestrator agents. The Follow-Up Chat panel sends messages to Gemini CLI which has filesystem tools (read_file, write_file, grep_search, run_shell_command).

The core problem: the AI layer (Gemini CLI) cannot access structured data during conversations, analysis results are ephemeral (burned after display), and the Follow-Up Chat has no tool routing to query the warehouse. This plan transforms the system from a batch report generator into a conversational intelligence platform.

Tech stack: PySide6 (Qt6), Python, SQLite, Gemini CLI (primary AI), Claude API (secondary, non-PHI). All UI is Qt widgets with signals/slots. No JavaScript, no Electron, no web renderer.

---

## Database Architecture: Ephemeral/Persistent Partition

The existing SQLite warehouse (`data/local_warehouse.db`) stays as one database file. No second database, no sync layer. Instead, tables are classified as **ephemeral** (wiped on Clear & Close) or **persistent** (survive Clear & Close). The partition boundary is PHI: ephemeral tables contain raw ticket text or customer data; persistent tables contain only metadata, classifications, ticket IDs, and PHI-free analytical summaries.

### Ephemeral Tables (wiped on Clear & Close)

These contain raw ticket text, customer messages, or data that could echo PHI:

| Table | Reason |
|-------|--------|
| `raw_ingestion_rows` | Raw JSON from Lightdash/CSV with full ticket content |
| `ingestion_chunks` | Chunk metadata tied to raw imports |
| `tickets` | Ticket metadata with requester_hash |
| `comments` | Individual messages with full body text |
| `conversations` | Rebuilt full threads (full_thread field) |
| `conversations_fts` | FTS5 index over full thread text |
| `nlp_batches` | Contains raw_response with ticket text echoed by Gemini |
| `nlp_ticket_classifications` | Contains summary field that may echo customer language |
| `ticket_entities` | Extracted PII entities (email, phone, customer_name) |
| `datasets` | Imported CSV batches |

### Persistent Tables (survive Clear & Close)

Everything analytical, plus the new tables from this plan:

**Existing tables that persist:** `sub_patterns`, `sub_pattern_ngrams`, `sub_pattern_snapshots`, `nlp_scan_runs` (metadata only), `nlp_findings`, `source_events`, `source_trc_daily`, `source_trc_hourly`, `daily_counts`, `hourly_counts`, `trc_baselines`, `hourly_baselines`, `incident_flags`, `daily_baselines`, `rolling_stats`, `anomaly_flags`, `watchlist_rules`, `watchlist_alerts`, `watchlist_examples`, `prompt_library`, `analysis_reports`, `report_schedules`, `gemini_usage`, `cost_limits`, `user_terms`, `tfidf_feedback`, `discovered_compounds`, `interventions`, `guru_*` tables, `chart_layouts`, `agent_health`, `scan_progress`.

**New tables that persist:** `ticket_index`, `scan_category_snapshots`, `analysis_runs`, `trend_snapshots`, `insight_ledger`, `chat_sessions`, `report_definitions`.

### Clear & Close Behavior

Refactor the existing "Clear & Close" handler. Instead of deleting the database file, iterate over the ephemeral table list and wipe them:

```python
EPHEMERAL_TABLES = [
    'raw_ingestion_rows', 'ingestion_chunks', 'tickets', 'comments',
    'conversations', 'nlp_batches', 'nlp_ticket_classifications',
    'ticket_entities', 'datasets'
]

def clear_session_data(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")
    for table in EPHEMERAL_TABLES:
        conn.execute(f"DELETE FROM {table}")
    # Drop and recreate FTS table (can't DELETE from virtual tables)
    conn.execute("DROP TABLE IF EXISTS conversations_fts")
    conn.execute("""
        CREATE VIRTUAL TABLE conversations_fts USING fts5(
            ticket_id, subject, trc_label, full_thread
        )
    """)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("VACUUM")  # Reclaim disk space, physically remove deleted data
    conn.commit()
    conn.close()
```

"Keep & Close" does nothing — closes the app, database stays intact for next launch.

### The ticket_index Table

This is the core persistent ticket-level record. PHI-free, deduplicated, enriched. Every ticket that passes through the NLP scan gets a row here that survives Clear & Close. This is what `alma_query` commands query when the ephemeral data has been cleared.

```sql
CREATE TABLE ticket_index (
  ticket_id TEXT PRIMARY KEY,
  first_seen_scan_id TEXT NOT NULL,
  last_seen_scan_id TEXT NOT NULL,
  first_seen_date DATETIME NOT NULL,
  ticket_created_date DATE,
  trc_code TEXT,
  trc_label TEXT,
  subject_sanitized TEXT,        -- subject line with PII stripped
  issue_snippet TEXT,            -- 1-sentence PHI-free issue summary from Gemini
  friction_type TEXT,
  sub_pattern TEXT,
  sub_pattern_id INTEGER,        -- FK → sub_patterns.pattern_id
  sentiment_polarity TEXT,
  sentiment_intensity REAL,
  anomaly_flag TEXT,
  anomaly_reason TEXT,
  root_cause_hint TEXT,
  csat_score REAL,
  message_count INTEGER,
  resolution_hours REAL,
  classification_confidence REAL,
  classification_method TEXT,    -- 'llm' or 'ngram'
  entities_json TEXT,            -- non-PII entities only (product names, features, payer names)
  key_phrases TEXT,
  is_novel BOOLEAN DEFAULT 0,
  dataset_id TEXT
);
CREATE INDEX idx_ti_trc ON ticket_index(trc_code);
CREATE INDEX idx_ti_friction ON ticket_index(friction_type);
CREATE INDEX idx_ti_sub_pattern ON ticket_index(sub_pattern);
CREATE INDEX idx_ti_anomaly ON ticket_index(anomaly_flag);
CREATE INDEX idx_ti_created ON ticket_index(ticket_created_date);
CREATE INDEX idx_ti_sentiment ON ticket_index(sentiment_polarity);
CREATE INDEX idx_ti_last_scan ON ticket_index(last_seen_scan_id);
```

### Issue Snippet (Existing Field, No Prompt Change Needed)

The NLP classification prompt (`nlp_classify.txt`) already requires Gemini to produce a `"summary": "<1-2 sentence de-identified>"` field for every ticket. This is the issue_snippet — no prompt modification needed. During the `ticket_index` upsert, map `classification.summary` → `ticket_index.issue_snippet` directly.

The existing summary field already follows PHI-free conventions (the classification rules state "No PII in any output field"). If any legacy scan data has summaries that leaked PII, the backfill job can re-generate them, but for new scans this is already handled.

`issue_snippet` is the primary text the LLM reads when the ephemeral layer is cleared.

### Dedup Gate (worker_agent.py — Per-Batch Processing)

The dedup gate hooks into `worker_agent.py`, step 4 of the NLP scan pipeline. The worker currently: (1) receives a batch from the queue, (2) loads tickets for the batch, (3) builds the prompt with ticket JSONL, (4) dispatches to the bridge. The dedup check goes **between step 2 and step 3** — after loading tickets but before building the prompt:

```python
# In worker_agent.py, per-batch processing:

# Step 2: Load tickets for batch (existing)
tickets = load_batch_tickets(batch_id, conn)

# Step 2.5 (NEW): Dedup gate — filter tickets against ticket_index
tickets_to_classify = []
for ticket in tickets:
    action = should_classify_ticket(ticket['ticket_id'], scan_id, conn)
    if action == 'skip':
        continue  # Already in this scan (restart recovery)
    elif action == 'update_scan_id':
        update_scan_reference(ticket['ticket_id'], scan_id, conn)
        continue  # Good existing classification, just mark as seen
    else:  # 'classify'
        tickets_to_classify.append(ticket)

if not tickets_to_classify:
    mark_batch_complete(batch_id)  # All tickets already classified
    continue  # Next batch

# Step 3: Build prompt with ONLY remaining tickets (existing, but filtered list)
prompt = build_classification_prompt(tickets_to_classify, trc, stats_context, sub_taxonomy)

# Step 4: Dispatch to bridge (existing, unchanged)
```

This means the JSONL sent to Gemini only contains tickets that genuinely need classification. Bin-packing sizes (from VOCBatchPacker) don't change — batches were packed based on original counts, but fewer tickets go to the LLM. Some batches run smaller than planned, which is fine and saves tokens.

The dedup functions themselves (in `ticket_index_writer.py`):

```python
def should_classify_ticket(ticket_id: str, scan_id: str, conn) -> str:
    """Returns 'classify', 'skip', or 'update_scan_id'."""
    row = conn.execute(
        "SELECT last_seen_scan_id, classification_confidence FROM ticket_index WHERE ticket_id = ?",
        (ticket_id,)
    ).fetchone()

    if row is None:
        return 'classify'  # New ticket, never seen before

    last_scan_id, confidence = row
    if last_scan_id == scan_id:
        return 'skip'  # Already processed in this scan (restart recovery)

    # Ticket exists from a prior scan.
    # Update last_seen_scan_id to mark it as part of this scan's date range,
    # but don't reclassify unless confidence was low or force_reclassify is set.
    if confidence and confidence >= 0.7:
        return 'update_scan_id'  # Good existing classification, just update the scan reference
    else:
        return 'classify'  # Low confidence, reclassify


def update_scan_reference(ticket_id: str, scan_id: str, conn):
    """Mark an existing ticket as seen in the current scan without reclassifying."""
    conn.execute(
        "UPDATE ticket_index SET last_seen_scan_id = ? WHERE ticket_id = ?",
        (scan_id, ticket_id)
    )


def upsert_ticket_index(ticket_id: str, scan_id: str, classification: dict, conn):
    """Insert or update the ticket_index row from a fresh classification."""
    conn.execute("""
        INSERT INTO ticket_index (
            ticket_id, first_seen_scan_id, last_seen_scan_id, first_seen_date,
            ticket_created_date, trc_code, trc_label, subject_sanitized,
            issue_snippet, friction_type, sub_pattern, sub_pattern_id,
            sentiment_polarity, sentiment_intensity, anomaly_flag, anomaly_reason,
            root_cause_hint, csat_score, message_count, resolution_hours,
            classification_confidence, classification_method, entities_json,
            key_phrases, is_novel, dataset_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticket_id) DO UPDATE SET
            last_seen_scan_id = excluded.last_seen_scan_id,
            friction_type = excluded.friction_type,
            sub_pattern = excluded.sub_pattern,
            sub_pattern_id = excluded.sub_pattern_id,
            sentiment_polarity = excluded.sentiment_polarity,
            sentiment_intensity = excluded.sentiment_intensity,
            anomaly_flag = excluded.anomaly_flag,
            anomaly_reason = excluded.anomaly_reason,
            root_cause_hint = excluded.root_cause_hint,
            issue_snippet = excluded.issue_snippet,
            classification_confidence = excluded.classification_confidence,
            classification_method = excluded.classification_method,
            entities_json = excluded.entities_json,
            key_phrases = excluded.key_phrases,
            is_novel = excluded.is_novel
    """, (...))  # flatten classification dict to tuple
```

The dedup gate runs per-ticket inside the worker agent's batch processing. For overlapping scan windows (Jan–Mar then Feb–Apr), the Feb–Mar tickets get `update_scan_id` (fast, no LLM call) while the new April tickets get `classify` (full LLM classification). This means:
- No duplicate ticket records in `ticket_index`
- Trend counts based on `ticket_index` reflect unique tickets, not scan artifacts
- LLM costs drop for overlapping scans since already-classified tickets are skipped
- `scan_category_snapshots` counts are computed from `ticket_index` WHERE `last_seen_scan_id = current_scan`, giving accurate per-scan distributions without double-counting

### ticket_index Writes (Inline with store_classification)

The `ticket_index` upsert happens **inside the `store_classification` tool call handler** in the stream parser, not as a post-scan batch operation. The NLP pipeline already writes to `nlp_ticket_classifications` (ephemeral) immediately as each ticket's classification streams in from Gemini. The `ticket_index` write piggybacks on the same event:

```python
# In the store_classification tool call handler (stream_parser.py / worker_agent.py):

def handle_store_classification(classification: dict, scan_id: str, conn):
    # Existing: write to ephemeral nlp_ticket_classifications (unchanged)
    store_nlp_classification(classification, scan_id, conn)

    # NEW: write to persistent ticket_index
    # Map existing classification fields to ticket_index columns:
    #   classification.summary → ticket_index.issue_snippet
    #   classification.sub_cluster → ticket_index.sub_pattern
    #   classification.sub_cluster_confidence → ticket_index.classification_confidence
    #   classification.entities → ticket_index.entities_json (filter to non-PII only)
    #   All other fields map by name
    upsert_ticket_index(
        ticket_id=classification['ticket_id'],
        scan_id=scan_id,
        classification=classification,
        conn=conn
    )
```

This gives `ticket_index` the same crash-recovery property the pipeline already has: if the app crashes mid-batch, every ticket whose `store_classification` tool call completed has its row in both `nlp_ticket_classifications` AND `ticket_index`. No data loss for completed tickets.

The field mapping from NLP classification output → `ticket_index`:

| NLP Classification Field | ticket_index Column | Notes |
|---|---|---|
| `summary` | `issue_snippet` | Already PHI-free per classification rules |
| `sub_cluster` | `sub_pattern` | |
| `sub_cluster_confidence` | `classification_confidence` | |
| `is_novel` | `is_novel` | |
| `sentiment_intensity` | `sentiment_intensity` | |
| `sentiment_polarity` | `sentiment_polarity` | |
| `friction_type` | `friction_type` | |
| `anomaly_flag` | `anomaly_flag` | |
| `anomaly_reason` | `anomaly_reason` | |
| `entities` | `entities_json` | Filter: keep only payer, product_area, feature. Drop any PII entities. |
| `key_phrases` | `key_phrases` | JSON array → comma-separated string |
| `root_cause_hint` | `root_cause_hint` | |

Additional `ticket_index` fields populated from ticket metadata (available in the batch's ticket JSONL):

| Source | ticket_index Column |
|---|---|
| `ticket.ticket_id` | `ticket_id` (PK) |
| `ticket.trc` | `trc_code` |
| TRC label lookup | `trc_label` |
| `ticket.subject` (PII-stripped) | `subject_sanitized` |
| `ticket.created_at` | `ticket_created_date` |
| `ticket.csat` | `csat_score` |
| Thread message count | `message_count` |
| Resolution hours from `tickets` table | `resolution_hours` |
| `'llm'` (or `'ngram'` for fast-path) | `classification_method` |
| Current dataset_id | `dataset_id` |

### Deduped Scan Category Snapshots

The post-scan persistence hook now computes snapshots from `ticket_index` instead of from the ephemeral `nlp_ticket_classifications`:

```python
def write_scan_category_snapshot(scan_id: str, scan_date: str, conn):
    """Compute category distributions from ticket_index for this scan."""
    rows = conn.execute("""
        SELECT trc_code, friction_type, sub_pattern,
               COUNT(*) as ticket_count,
               AVG(sentiment_intensity) as avg_sentiment,
               AVG(csat_score) as avg_csat,
               SUM(CASE WHEN anomaly_flag IS NOT NULL THEN 1 ELSE 0 END) as anomaly_count
        FROM ticket_index
        WHERE last_seen_scan_id = ?
        GROUP BY trc_code, friction_type, sub_pattern
    """, (scan_id,)).fetchall()

    for row in rows:
        # Get up to 5 sample ticket IDs for this group
        samples = conn.execute("""
            SELECT ticket_id FROM ticket_index
            WHERE last_seen_scan_id = ? AND trc_code = ? AND friction_type = ? AND sub_pattern = ?
            LIMIT 5
        """, (scan_id, row[0], row[1], row[2])).fetchall()

        conn.execute("""
            INSERT INTO scan_category_snapshots
            (scan_id, scan_date, trc, friction_type, sub_pattern,
             ticket_count, avg_sentiment, avg_csat, anomaly_count, sample_ticket_ids)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (scan_id, scan_date, row[0], row[1], row[2],
              row[3], row[4], row[5], row[6],
              json.dumps([s[0] for s in samples])))
```

### Backfill Jobs

When you need to add a new column to `ticket_index` and backfill it:

1. **Schema change:** `ALTER TABLE ticket_index ADD COLUMN new_field TEXT` — SQLite handles this without rebuilding.
2. **If derivable from existing data:** Single `UPDATE` statement. Example: `UPDATE ticket_index SET severity_score = CASE WHEN anomaly_flag = 'critical' THEN 3 WHEN anomaly_flag = 'unusual' THEN 2 ELSE 1 END`.
3. **If requires re-reading tickets (e.g., generating issue_snippet for old records):**
   - Query `ticket_index` for rows where the new field IS NULL.
   - Re-import those specific tickets from Lightdash/Zendesk into the ephemeral layer (targeted import by ticket_id list).
   - Run a backfill enrichment pass: for each ticket, call Gemini with the conversation thread + the enrichment prompt, write the result to `ticket_index`.
   - Clear the ephemeral layer when done.
   - This is a first-class operation: `python -m src.data.backfill --column issue_snippet --where "issue_snippet IS NULL" --batch-size 50`.

### alma_query Behavior with Cleared Ephemeral Data

When the ephemeral layer is cleared, `alma_query` commands degrade gracefully:

- `alma_query tickets` → queries `ticket_index` instead of ephemeral tables. Returns ticket_id, trc, friction_type, sub_pattern, issue_snippet, sentiment, anomaly_flag. Works fully.
- `alma_query ticket-detail` → checks ephemeral `conversations` first. If empty, returns `ticket_index` metadata + issue_snippet with a note: `"full_thread_available": false, "note": "Full conversation not loaded. Re-import data covering [date] to view the complete thread."`. The issue_snippet gives Gemini enough to reason about the ticket.
- `alma_query search --keyword X` → searches `ticket_index.issue_snippet` and `ticket_index.key_phrases` via LIKE. Less precise than FTS5 over full threads, but functional.
- `alma_query trends`, `anomalies`, `compare`, `insights`, `past-analysis`, `validate` → all query persistent tables only. Work identically regardless of ephemeral state.

---

## Phase 1: Persistence Layer (Weeks 1–4)

### 1.1 Database Migrations

Create these tables in the existing SQLite warehouse. Add a migration file to the existing migration system (numbered SQL file in `migrations/`).

**`ticket_index`** — the core persistent ticket-level record (see schema above in Database Architecture).

**`scan_category_snapshots`** — written after every NLP scan completion:
```sql
CREATE TABLE scan_category_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scan_id TEXT NOT NULL,
  scan_date DATETIME NOT NULL,
  trc TEXT NOT NULL,
  friction_type TEXT,
  sub_pattern TEXT,
  ticket_count INTEGER DEFAULT 0,
  avg_sentiment REAL,
  avg_csat REAL,
  anomaly_count INTEGER DEFAULT 0,
  sample_ticket_ids TEXT -- JSON array, up to 5
);
CREATE INDEX idx_scs_scan_date ON scan_category_snapshots(scan_date);
CREATE INDEX idx_scs_friction ON scan_category_snapshots(friction_type);
```

**`analysis_runs`** — written after every AI report generation:
```sql
CREATE TABLE analysis_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT UNIQUE NOT NULL,
  run_date DATETIME NOT NULL,
  prompt_template TEXT,
  prompt_text TEXT,
  trc_filter TEXT,
  date_start DATE,
  date_end DATE,
  ticket_count INTEGER,
  model_used TEXT,
  output_text TEXT,
  output_structured TEXT, -- JSON key findings if extractable
  token_count INTEGER,
  cost_usd REAL,
  duration_sec REAL,
  definition_id TEXT,         -- FK → report_definitions.definition_id (null for ad-hoc runs)
  source TEXT DEFAULT 'manual' -- manual | scheduled | smart_pipeline
);
CREATE INDEX idx_ar_date ON analysis_runs(run_date);
CREATE INDEX idx_ar_template ON analysis_runs(prompt_template);
```

**`trend_snapshots`** — written after each statistical engine run:
```sql
CREATE TABLE trend_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_date DATETIME NOT NULL,
  engine TEXT NOT NULL, -- tfidf | poisson | cusum | ewma
  metric_key TEXT NOT NULL,
  metric_value REAL,
  direction TEXT, -- rising | falling | stable | new | critical
  severity TEXT, -- info | watch | warning | critical
  pct_change REAL,
  baseline_value REAL,
  context TEXT -- JSON engine-specific context
);
CREATE INDEX idx_ts_date ON trend_snapshots(snapshot_date);
CREATE INDEX idx_ts_engine ON trend_snapshots(engine);
```

**`insight_ledger`** — running log of significant findings:
```sql
CREATE TABLE insight_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  insight_id TEXT UNIQUE NOT NULL,
  date_identified DATETIME NOT NULL,
  source_run_id TEXT,
  insight_type TEXT, -- anomaly | trend | root_cause | correlation
  title TEXT,
  description TEXT,
  severity TEXT, -- info | moderate | high | critical
  supporting_ticket_ids TEXT, -- JSON array
  supporting_data TEXT, -- JSON quantitative evidence
  status TEXT DEFAULT 'new', -- new | acknowledged | investigating | resolved
  resolved_date DATETIME,
  notes TEXT
);
CREATE INDEX idx_il_status ON insight_ledger(status);
CREATE INDEX idx_il_severity ON insight_ledger(severity);
```

**`chat_sessions`** — persists Follow-Up Chat conversations:
```sql
CREATE TABLE chat_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT UNIQUE NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  source_page TEXT, -- ai_reports | nlp_scanner | chat | smart_reporting
  source_context TEXT, -- JSON: report output, scan results, filters at launch
  trc_filter TEXT,
  date_start DATE,
  date_end DATE,
  messages TEXT, -- JSON array of {role, content, timestamp, tool_calls}
  title TEXT
);
CREATE INDEX idx_cs_updated ON chat_sessions(updated_at);
```

**`report_definitions`** — Smart Report pipeline configs:
```sql
CREATE TABLE report_definitions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  definition_id TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  description TEXT,
  created_by TEXT,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  config TEXT NOT NULL, -- JSON Report Definition
  schedule TEXT, -- JSON schedule config or null
  last_run_id TEXT,
  last_run_date DATETIME,
  is_active BOOLEAN DEFAULT 1
);
```

### 1.2 NLP Scan Pipeline Integration

Three integration points into the existing NLP scan pipeline. The dedup gate and ticket_index upsert logic is detailed in the Database Architecture section above; this section specifies where in the pipeline code each hook goes.

**Hook 1: Dedup gate in worker_agent.py (per-batch, before prompt building)**

In `worker_agent.py`, between loading tickets for a batch and building the classification prompt: filter the ticket list through `should_classify_ticket()`. Tickets that return `'skip'` or `'update_scan_id'` are removed from the batch before prompt assembly. Only remaining `'classify'` tickets go to Gemini. See the Database Architecture section for the full code pattern.

**Hook 2: ticket_index upsert in store_classification handler (per-ticket, inline with stream)**

In the `store_classification` tool call handler (where the stream parser writes to `nlp_ticket_classifications`): add a second write to `ticket_index` via `upsert_ticket_index()`. Map `classification.summary` → `issue_snippet`. This runs for each ticket as its classification streams in — same crash-recovery guarantee as the existing pipeline. See the field mapping table in the Database Architecture section.

**Hook 3: Post-scan persistence in scan_orchestrator.py (after meta-analyzer)**

In `_run_scan()`, the existing execution sequence is:
1. Preflight → Queue batches → Run workers → Retry sweep
2. Run analyst (post-classification: cross-TRC synthesis, quality audit, novelty validation)
3. Run meta-analyzer (Layer 2: aggregate, tier management, n-gram extraction, snapshots, findings)
4. Finalize

Add a new step between meta-analyzer and finalize:

```
3. Run meta-analyzer (existing)
4. NEW: Run persistence writer (post_scan_persist.py):
   a. write_scan_category_snapshot(scan_id, scan_date, conn)
      — queries ticket_index WHERE last_seen_scan_id = current scan
      — groups by (trc_code, friction_type, sub_pattern)
      — writes to scan_category_snapshots
   b. Compare current snapshot vs. most recent prior snapshot
      — for groups with >20% ticket_count change or new groups: write to trend_snapshots
   c. Check significance thresholds
      — new pattern >10 tickets, existing >30% growth, newly critical: write to insight_ledger
   d. Log dedup stats
      — total tickets in scope, classified (new), skipped (dedup), updated (scan ref only), cost saved
5. Finalize: mark scan complete (existing)
```

This runs after the meta-analyzer because the meta-analyzer's tier management (promote/demote/retire sub-patterns) and n-gram extraction should complete first — the persistence writer reads the final state of `ticket_index` and the updated `sub_patterns` tiers.

### 1.3 Post-Report Persistence Hook

Locate the report generation completion handler (AI Reports page, after Gemini returns the report output). After the output renders:

1. Generate a UUID run_id.
2. Write to `analysis_runs` with the full prompt text, template name, filters, output, token count, cost, duration.
3. This replaces the existing "Save to History" button behavior — reports auto-persist. The button can remain as a manual trigger for users who want to explicitly name/tag a report.

### 1.4 alma-query CLI Tool

Create `src/tools/alma_query.py` — a Python CLI that Gemini invokes via `run_shell_command`. Uses argparse with subcommands. Each subcommand executes parameterized SQL against the SQLite warehouse and returns JSON to stdout.

**Subcommands to implement:**

`tickets` — filtered ticket retrieval:
- Args: --trc, --friction-type, --sub-pattern, --from, --to, --sentiment, --anomaly-flag, --keyword, --limit (default 25, max 100), --sort (date|sentiment|csat)
- Returns: JSON array of {ticket_id, trc_code, trc_label, subject_sanitized, issue_snippet, friction_type, sub_pattern, sentiment_polarity, sentiment_intensity, csat_score, anomaly_flag, anomaly_reason, ticket_created_date, message_count, classification_method}
- SQL: queries `ticket_index` (persistent). Works regardless of whether ephemeral data is loaded. If --keyword is provided, searches issue_snippet, subject_sanitized, and key_phrases via LIKE.

`ticket-detail` — full conversation thread for one ticket:
- Args: --id (required), --include-metadata (default true)
- Returns: {ticket_id, trc_code, trc_label, classification: {friction_type, sub_pattern, sentiment, anomaly_flag, root_cause_hint}, issue_snippet, full_thread_available: bool, messages: [{role, content, timestamp}] | null}
- Behavior: first checks ephemeral `conversations` table for the full thread. If found, returns full messages array with PII redaction applied. If ephemeral data is cleared, returns `ticket_index` metadata + issue_snippet with `full_thread_available: false` and `messages: null`. Includes a `note` field: "Full conversation not loaded. Re-import data covering [ticket_created_date] to view the complete thread."

`search` — full-text search across ticket content:
- Args: --query (required), --from, --to, --limit (default 20)
- Returns: same format as `tickets` but ranked by relevance.
- Behavior: if ephemeral `conversations_fts` table has data, uses FTS5 for full-thread search. If ephemeral data is cleared, falls back to LIKE search across `ticket_index.issue_snippet`, `ticket_index.subject_sanitized`, and `ticket_index.key_phrases`. Less precise but functional.

`trends` — pre-computed trend data:
- Args: --metric (volume|sentiment|csat|anomaly_rate), --by (trc|friction_type|sub_pattern), --granularity (daily|weekly|monthly), --months (default 3), --direction (rising|falling|critical)
- Returns: JSON array of {metric_key, current_value, prior_value, pct_change, direction, severity, data_points: [{period, value}]}
- SQL: query scan_category_snapshots grouped by time period.

`anomalies` — flagged incidents:
- Args: --from, --to, --severity, --engine, --limit (default 20)
- Returns: JSON array from trend_snapshots and NLP anomaly flags.

`compare` — period-over-period delta:
- Args: --period-a, --period-b, --dimension (trc|friction_type|sub_pattern)
- Returns: JSON array of {dimension_value, period_a_count, period_b_count, delta, pct_change, direction}

`insights` — query the insight ledger:
- Args: --status, --type, --severity, --since
- Returns: JSON array from insight_ledger.

`past-analysis` — prior report runs:
- Args: --template, --trc, --since, --limit (default 5)
- Returns: JSON array from analysis_runs (output truncated to first 500 chars).

`validate` — verify a quantitative claim:
- Args: --claim, --metric, --filters (JSON)
- Returns: {claim_valid, actual_value, expected_value, discrepancy_pct, explanation}
- Implementation: parse the claim for numbers, run the relevant SQL count/aggregate, compare.

**Error handling:** All subcommands return `{"error": "message"}` with exit code 1 on failure. Input validation before SQL execution. SQL injection prevention via parameterized queries only.

**Testing:** Create a test suite that loads sample data into a test SQLite DB and verifies each subcommand returns correct JSON.

---

## Phase 2: Conversational Shell (Weeks 5–8)

### 2.1 Context Injection Layer

Locate the Follow-Up Chat message send handler. Before the user's message is sent to Gemini CLI, prepend a context block:

```
[SYSTEM CONTEXT]
The user is on the {page_name} page.
Active filters: TRC={trc_filter}, Date range={date_start} to {date_end}.
Data scope: {ticket_count} tickets in ticket_index for this range, {pattern_count} classified patterns, {anomaly_count} active anomalies.
Ephemeral data loaded: {yes/no} (if no, full conversation threads are unavailable — use issue_snippet from ticket_index instead).
Last NLP scan: {scan_recency}.

{If on AI Reports and a report is loaded:}
The user is viewing a {template_name} report generated on {run_date}.
Report summary: {first 500 chars of output}

{If on NLP Scanner:}
Latest scan classified {scan_ticket_count} tickets into {category_count} categories.
Top friction types: {top 5 friction types with counts}
Critical anomalies: {count and brief descriptions}

You have access to these query tools via run_shell_command:
- python alma_query.py tickets [--trc X] [--friction-type X] [--from X] [--to X] [--keyword X] [--limit N]
- python alma_query.py ticket-detail --id X
- python alma_query.py trends [--metric X] [--by X] [--months N]
- python alma_query.py anomalies [--severity X]
- python alma_query.py compare --period-a X --period-b X
- python alma_query.py insights [--status X]
- python alma_query.py validate --claim "X" --filters '{}'

When you make quantitative claims, be specific and cite ticket IDs where possible.
[END CONTEXT]
```

Build this context from: (a) the current page's active filters/state from the Qt widget properties, (b) a quick SQL query against `ticket_index` for ticket count in the active date range + scan recency from `nlp_scan_runs`, (c) a check of whether ephemeral `conversations` table has rows (to set the ephemeral data loaded flag), (d) the loaded report output if present. Implement as a Python class `ContextInjector` that takes the current page state and returns the assembled context string.

### 2.2 Tool Routing

Currently Gemini CLI has generic `run_shell_command`. The tool routing layer doesn't intercept — it lets Gemini call run_shell_command with alma_query commands directly. The context injection (2.1) tells Gemini what commands are available.

What needs to happen on the UI side:
1. When Gemini's response contains tool call results (JSON output from alma_query), parse them and render appropriately in the chat QTextBrowser/QListWidget — ticket lists as compact rows with clickable IDs, trend data as text summaries, anomaly lists as flagged items.
2. When a ticket ID appears in a response (regex: `#\d{4,6}`), make it clickable via Qt rich text or a custom QTextBrowser link handler. Clicking emits a signal that loads the ticket detail in the Evidence Panel (on Analysis Canvas) or opens the Conversation Search drilldown.

### 2.3 Chat Session Persistence

1. On first message in a new chat, create a `chat_sessions` row with a UUID, source_page, source_context (serialized current page state), and active filters.
2. After each message (user or AI), append to the messages JSON array and update `updated_at`.
3. Auto-title sessions: after the 3rd message, use a simple heuristic or Gemini to generate a short title from the conversation content.
4. Build a session selector dropdown/list that queries `chat_sessions` ordered by updated_at desc. Selecting a session loads its messages into the chat UI and restores the source_context filters.

### 2.4 Response Validation (P1)

After Gemini's response renders in the chat:
1. Extract numeric claims via regex: patterns like "47 tickets", "up 30%", "$0.09", "87 tickets/month".
2. For each claim, construct an alma-query validate call with the claim text and active filters.
3. Run validation in a QThread worker (don't block the UI thread).
4. When validation completes, update the message widget to append a small QLabel indicator next to each validated claim: ✓ green if within 5% of actual, ⚠ amber if 5-20% off, ✗ red if >20% off with the actual number shown.
5. This is progressive — the response widget shows immediately, validation indicators appear 1-2 seconds later as the QThread emits results.

---

## Phase 3: UI Redesign + Smart Reports (Weeks 9–16)

### 3.1 AI Reports Page Redesign

The AI Reports page currently has: prompt selector (QComboBox), TRC filter, date range, Generate Report button (QPushButton), Report Output rendered markdown (likely QTextBrowser), and action buttons (Copy, Save .md, Save .html, Save to History, Export to Drive, Follow-Up Chat).

**Replace with a QTabWidget layout:**

**Tab: Analysis Canvas** (default)
- Top bar: TRC filter (QComboBox), date range (QDateEdit pair), data scope badge (QLabel showing ticket count, pattern count, anomaly count, scan recency). These persist across tabs.
- Left panel (~60%, QSplitter): Report selector dropdown (QComboBox populated from analysis_runs, most recent per template). Below it, the report output rendered as structured sections in a QScrollArea — the renderer parses markdown headings into section cards with finding-level action buttons. Different Report Definitions produce different section counts (VOC has 7, a bug tracker might have 5); the renderer handles any section structure from the convergence output. Below the report, the chat input field (QLineEdit + QPushButton) for follow-up questions. Chat messages appear below the report output in the same scroll, creating a continuous report → conversation flow.
- Right panel (~40%, QSplitter): Evidence panel (QWidget). Context-reactive via Qt signals. When user clicks a finding or ticket in the report/chat, shows: ticket preview (full thread, PII-redacted), trend sparkline for the active topic (custom QPainter widget reading from trend_snapshots), report metadata (pipeline info, specialist count, ticket/TRC counts, cost, date). Default/empty state: QLabel with "Select a finding or ask a question to see supporting evidence."
- Keep existing export actions (Copy, Save .md, Save .html, Export to Drive) as buttons above the report output.

**Tab: Report History**
- Searchable, filterable QTableView backed by a QSortFilterProxyModel over analysis_runs.
- Columns: Report name, date, tickets, TRC filter, duration, cost, actions.
- Filter pills: QPushButton group — All, VOC, Billing, Engineering, Custom.
- Actions per row: View (loads in Analysis Canvas tab), Copy, Save .md, Save .html, Export to Drive.
- "View" switches to Analysis Canvas tab with that report loaded (emit signal, tab widget switches).

**Tab: Manage Prompts**
- Left sidebar (QListWidget): list of all prompt templates (built-in and custom). Grouped by "Built-in" and "Custom" sections. Each shows name and variable count. "+ New prompt" QPushButton at bottom.
- Right panel (QSplitter, vertical): conversational prompt builder. When creating/editing a custom prompt, the panel shows a chat interface (reusing the ChatEngine) where Gemini walks the user through building the prompt. Gemini asks what the report should answer, what data to scope, what output structure is needed. As the conversation progresses, a "Generated prompt preview" QPlainTextEdit at the bottom shows the assembled prompt template with variables highlighted. QPushButtons: Preview prompt, Test run, Save.
- For existing prompts, show the prompt text in a QPlainTextEdit with the variable reference list.

### 3.2 Follow-Up Chat

The Follow-Up Chat panel (currently a QDockWidget or sidebar that slides in) becomes available in two modes:
1. **Embedded in Analysis Canvas:** the chat input at the bottom of the report view, with messages appearing below the report in the same QScrollArea. This is the primary mode — report and conversation are one flow.
2. **Standalone page:** accessible from the sidebar nav (like the existing chat icon). Opens as a full-width QWidget chat view with session management (QComboBox for session switching). Data context QLabel at top shows what data is loaded. This is the "just ask questions" mode — no report needed, just a chat about the data.

Both modes use the same underlying chat engine (context injection, tool routing, session persistence). Implemented as a reusable `ChatEngine` class that the embedded widget and standalone page both instantiate.

### 3.3 NLP Scanner Redesign

The NLP Scanner page currently has: scan config (TRC filter QComboBox, date range QDateEdit, status filter), Start Scan QPushButton, batch execution log (QTextEdit or QListWidget), and scan results.

**Add these sections (as QWidgets in the existing page layout):**

**Scan History Timeline:** Below the scan config, add a custom QWidget with paintEvent that draws past scans as dots on a horizontal line. Clicking a past scan loads its results. Shows scan date, ticket count on hover via QToolTip. The most recent scan is highlighted.

**Scan Diff Banner:** When the current scan completes and there's a prior scan to compare against, show a banner: "Changes since last scan (date): X new patterns detected. Y grew +Z%. N patterns resolved." This reads from scan_category_snapshots.

**Metrics Row:** Four QFrame cards in a QHBoxLayout — Tickets classified, Active patterns, Critical anomalies, Avg sentiment. Each shows the current value (QLabel, large font) and the delta from the prior scan (QLabel, smaller, colored).

**Classification Dashboard:** Below metrics, show the friction type distribution as clickable QFrame cards — users can click a friction type to filter the ticket explorer below (emit `friction_type_selected(str)` signal). Show sub-pattern taxonomy in a QTreeWidget or QTableView with lifecycle indicators (new, growing, stable, declining, resolved) computed from scan_category_snapshots history. Sparklines next to each pattern as custom QWidget cells with paintEvent drawing the mini chart.

**Ticket Explorer:** Filterable, sortable QTableView of every classified ticket. Columns: ticket ID, subject, TRC, friction type, sub-pattern, sentiment, anomaly flag. Backed by QSortFilterProxyModel for live filtering. Bulk actions toolbar: "Investigate in Chat" (opens chat with selected tickets as context), "Add to Insight Ledger", "Export Selection".

### 3.4 Smart Reports: Configurable VOCBuilder

The existing VOC pipeline is a 4-phase architecture: Phase 0 (planning), Phase 1 (batched TRC analysis via VOCBatchPacker with greedy bin-packing, 4 parallel bridge subprocesses, adaptive rate governor), Phase 2a (accumulator rounds pipelined into Phase 1 whitespace with append-only evidence ledger + rewritable synthesis), Phase 2b (3 parallel specialists with distinct output ownership), Phase 3 (convergence into 7-section report). This architecture is sophisticated and battle-tested — do NOT replace it with a generic framework.

**The refactoring is parameterization, not abstraction.** Make `VOCBuilder.run()` accept an optional Report Definition config that controls what the pipeline does without changing how it does it.

#### Report Definition JSON Schema

```json
{
  "name": "Monthly Billing Review",
  "description": "Billing TRC analysis with charge discrepancy focus",
  "data_scope": {
    "trc_filter": ["Provider Alma deduction due to medical audits", "Client disputes invoice adjustments..."],
    "date_mode": "rolling",
    "rolling_days": 30,
    "status_filter": "All"
  },
  "sampling": {
    "max_tickets": 200,
    "stratify_by": "friction_type",
    "oversample_anomalies": true,
    "anomaly_oversample_pct": 0.20
  },
  "phase1": {
    "batch_prompt": "voc_analysis_batch.txt",
    "single_trc_prompt": "voc_analysis.txt",
    "enabled": true
  },
  "accumulator": {
    "enabled": true,
    "max_rounds": 6
  },
  "specialists": [
    {
      "name": "pattern_detector",
      "prompt": "voc_pattern_detector.txt",
      "owns_sections": [2, 4],
      "extra_context": ["global_statistics"]
    },
    {
      "name": "novelty_scanner",
      "prompt": "voc_novelty_scanner.txt",
      "owns_sections": [3],
      "extra_context": ["nlp_baseline"]
    },
    {
      "name": "friction_scorer",
      "prompt": "voc_friction_scorer.txt",
      "owns_sections": [5, 6],
      "extra_context": ["csat_metrics", "escalation_metrics"]
    }
  ],
  "convergence": {
    "prompt": "voc_convergence.txt",
    "output_sections": [
      "executive_summary",
      "top_friction_points",
      "getting_worse",
      "getting_better",
      "root_cause_map",
      "recommendations",
      "trc_summary_table"
    ],
    "input_cap_chars": 800000,
    "target_lines": "300-500"
  }
}
```

A simpler report (e.g., Engineering Bug Tracker) might drop specialists and the accumulator entirely:

```json
{
  "name": "Engineering Bug Tracker",
  "data_scope": {
    "trc_filter": ["platform_bug_trcs"],
    "date_mode": "rolling",
    "rolling_days": 7
  },
  "sampling": {
    "max_tickets": 100,
    "stratify_by": "friction_type",
    "oversample_anomalies": false
  },
  "phase1": {
    "batch_prompt": "eng_bug_analysis_batch.txt",
    "single_trc_prompt": "eng_bug_analysis.txt",
    "enabled": true
  },
  "accumulator": {
    "enabled": false
  },
  "specialists": [
    {
      "name": "bug_classifier",
      "prompt": "eng_bug_classifier.txt",
      "owns_sections": [2, 3, 4],
      "extra_context": ["global_statistics"]
    }
  ],
  "convergence": {
    "prompt": "eng_bug_convergence.txt",
    "output_sections": [
      "executive_summary",
      "bug_categories",
      "severity_ranking",
      "affected_features",
      "recommendations"
    ],
    "input_cap_chars": 400000,
    "target_lines": "100-200"
  }
}
```

#### VOCBuilder.run() Modifications

The modifications are surgical — 6 specific injection points in the existing pipeline code:

**1. Accept optional config (Phase 0):**
```python
def run(self, date_start, date_end, trc_filter=None, report_definition=None):
    # If report_definition is provided, load config. Otherwise use hardcoded VOC defaults.
    config = report_definition or self._default_voc_config()
    
    # Phase 0: planning — use config.data_scope for TRC filtering
    trc_filter = trc_filter or config['data_scope'].get('trc_filter')
    trc_counts = get_trc_ticket_counts(date_start, date_end, trc_filter)
    
    # Sampling: use config.sampling instead of hardcoded thresholds
    max_tickets = config['sampling'].get('max_tickets', 200)
    stratify_by = config['sampling'].get('stratify_by', 'friction_type')
    # ... rest of planning
```

**2. Prompt loading (Phase 1):**
```python
# Instead of hardcoded prompt file names:
batch_prompt_file = config['phase1'].get('batch_prompt', 'voc_analysis_batch.txt')
single_prompt_file = config['phase1'].get('single_trc_prompt', 'voc_analysis.txt')
batch_prompt = load_prompt(batch_prompt_file)
# VOCBatchPacker, bin-packing, dispatch, retry — ALL UNCHANGED
```

**3. Accumulator toggle (Phase 2a):**
```python
if config['accumulator'].get('enabled', True):
    max_rounds = config['accumulator'].get('max_rounds', 8)
    # Existing accumulator logic with configurable round count
    # Evidence ledger, running synthesis, pipelining into Phase 1 whitespace — UNCHANGED
else:
    # Skip accumulator entirely — Phase 2b gets raw Phase 1 outputs
    accumulator_ledger = None
    accumulator_synthesis = None
```

**4. Specialist dispatch (Phase 2b):**
```python
# Instead of hardcoding 3 specialists:
specialist_configs = config.get('specialists', self._default_specialists())
specialist_tasks = []
for spec in specialist_configs:
    prompt = load_prompt(spec['prompt'])
    extra_ctx = self._build_extra_context(spec.get('extra_context', []))
    specialist_tasks.append({
        'name': spec['name'],
        'prompt': prompt,
        'owns_sections': spec['owns_sections'],
        'extra_context': extra_ctx
    })
# Parallel dispatch via existing ReportOrchestrator — UNCHANGED
# Priority queue, rate governor, retry budgets — UNCHANGED
```

**5. Convergence (Phase 3):**
```python
convergence_prompt_file = config['convergence'].get('prompt', 'voc_convergence.txt')
convergence_prompt = load_prompt(convergence_prompt_file)
input_cap = config['convergence'].get('input_cap_chars', 800000)
output_sections = config['convergence'].get('output_sections', self._default_sections())

# Build section ownership map for convergence prompt context
section_ownership = {}
for spec in specialist_configs:
    for section_idx in spec['owns_sections']:
        section_ownership[output_sections[section_idx - 1]] = spec['name']

# Inject section_ownership into convergence prompt context
# Existing convergence logic (dedup rules, agreement/disagreement, fallback) — UNCHANGED
```

**6. Storage (Post-completion):**
```python
# Write to analysis_runs with definition_id for traceability
run_id = str(uuid.uuid4())
definition_id = config.get('definition_id')  # None for ad-hoc VOC runs
conn.execute("""
    INSERT INTO analysis_runs (run_id, run_date, prompt_template, ..., definition_id)
    VALUES (?, ?, ?, ..., ?)
""", (run_id, now, config['name'], ..., definition_id))
```

#### What Does NOT Change

- **VOCBatchPacker** — bin-packing logic stays identical
- **ReportOrchestrator** — subprocess dispatch, priority queue, rate governor, retry budgets
- **Accumulator evidence ledger + running synthesis pattern** — append-only ledger, rewritable scratchpad
- **Convergence assembly logic** — agreement/disagreement handling, dedup rules, graceful degradation to `_assemble_partial_report()`
- **PII redaction** in ticket JSONL assembly
- **NLP-enriched sampling** logic (stratified by friction_type, over-sample anomalies)
- **Statistical context** caching per (trc, date_range)
- **Cost estimation** and **runtime estimation** in Phase 0

#### Default VOC Config

Create `_default_voc_config()` that returns the exact current hardcoded behavior as a config dict. This is the backward-compatibility path — existing `VOCBuilder.run(date_start, date_end)` calls with no `report_definition` argument produce identical output.

```python
def _default_voc_config(self):
    return {
        "name": "VOC Root Cause Analysis",
        "definition_id": None,
        "data_scope": {"trc_filter": None, "date_mode": "fixed"},
        "sampling": {
            "max_tickets": 300,
            "stratify_by": "friction_type",
            "oversample_anomalies": True,
            "anomaly_oversample_pct": 0.20
        },
        "phase1": {
            "batch_prompt": "voc_analysis_batch.txt",
            "single_trc_prompt": "voc_analysis.txt",
            "enabled": True
        },
        "accumulator": {"enabled": True, "max_rounds": 8},
        "specialists": [
            {"name": "pattern_detector", "prompt": "voc_pattern_detector.txt",
             "owns_sections": [2, 4], "extra_context": ["global_statistics"]},
            {"name": "novelty_scanner", "prompt": "voc_novelty_scanner.txt",
             "owns_sections": [3], "extra_context": ["nlp_baseline"]},
            {"name": "friction_scorer", "prompt": "voc_friction_scorer.txt",
             "owns_sections": [5, 6], "extra_context": ["csat_metrics", "escalation_metrics"]}
        ],
        "convergence": {
            "prompt": "voc_convergence.txt",
            "output_sections": [
                "executive_summary", "top_friction_points", "getting_worse",
                "getting_better", "root_cause_map", "recommendations", "trc_summary_table"
            ],
            "input_cap_chars": 800000,
            "target_lines": "300-500"
        }
    }
```

Store this as `report_definitions/voc_root_cause.json` AND insert it as the first row in `report_definitions` table during migration. Verify it produces identical output to the current hardcoded pipeline before proceeding with any custom definitions.

#### AI-Assisted Report Configuration (Manage Prompts Tab)

When a user creates a new report via the Manage Prompts conversational builder, Gemini needs to know:
- The available specialist types and what each does (Pattern Detector finds cross-TRC patterns, Novelty Scanner finds deteriorating trends, Friction Scorer assigns severity and recommends)
- The available extra_context types (global_statistics, nlp_baseline, csat_metrics, escalation_metrics)
- The existing prompt files as examples of what good prompts look like
- The convergence section structure and how section ownership maps to specialists

The conversational flow produces: (1) custom prompt files saved to `prompt_library`, and (2) a Report Definition JSON that references those prompts and configures the pipeline. The user previews the config (showing which specialists will run, what sections the report will have, estimated cost/runtime from Phase 0 planning), then saves. The definition goes to `report_definitions` table, the prompt files go to `prompt_library`.

### 3.5 Smart Reporting Page Update

The existing Smart Reporting page (QWidget) has: prompt selector, lookback days, NLP budget cap, NLP workers, Run Full Pipeline button, auto-import toggle, scheduled runs (empty), CLI usage, and run history table.

**Update it to:**
- Show report pipeline cards at top (QFrame widgets in a QHBoxLayout/QGridLayout, one per report_definitions entry) with name, description, schedule, last run, and actions (Run Now, Edit Config, View Last — QPushButtons).
- Pipeline configuration QGroupBox (expanded when editing): prompt QComboBox, lookback days QSpinBox, budget cap QDoubleSpinBox, workers QSpinBox, QCheckBoxes for NLP scan, auto-import, prior-run diff, auto-export.
- Scheduled Runs QGroupBox with QCheckBox toggle switches per pipeline and next-run date QLabels. "+ Add Schedule" QPushButton.
- Run History QTableView (from analysis_runs filtered to smart pipeline runs).
- CLI usage QFrame at bottom with copyable command QLineEdit: `python -m src.data.smart_pipeline --config voc_root_cause --export-drive` (loads Report Definition by name from report_definitions table and runs VOCBuilder with it).

---

## Phase 4: Scheduling & Polish (Weeks 17–22)

### 4.1 Report Scheduling

- Implement a schedule checker that runs on a QTimer (check every 15 minutes) within the application's main thread or a background QThread.
- For each active report_definitions with a schedule config, check if the schedule is due.
- When due: assemble data scope (rolling date window from definition config), optionally trigger auto-import, optionally trigger NLP scan, then call `VOCBuilder.run(date_start, date_end, report_definition=loaded_config)`.
- Store output in analysis_runs with source='scheduled'.
- If auto-export is enabled, export to Google Drive via existing Drive integration.
- Surface a notification in the app (QSystemTrayIcon notification or in-app banner) when a scheduled run completes.
- Include a prior-run diff section by comparing the current output's key metrics against the most recent prior run for the same definition.

### 4.2 Report Template Library

Create 3-4 pre-built Report Definition JSONs as starting templates:
- **Monthly Billing Review:** Billing TRCs only, 2-bridge (batch + convergence), 2 specialists (Pattern Detector, Friction Scorer), output focused on charge discrepancies and copay issues.
- **Engineering Bug Tracker:** Platform bug TRCs, 1-bridge (single pass), 1 specialist, weekly cadence, fast turnaround.
- **Client Churn Analysis:** Cancellation-related TRCs, 2-bridge with a pre-cancellation history phase, focus on warning signs.
- **Payer Performance:** Claim denial TRCs, 2-bridge, focus on payer-specific patterns.

### 4.3 Insight Ledger UI

Add a dedicated QWidget page (accessible from sidebar nav) showing the insight_ledger:
- Filterable by status (QComboBox: new, acknowledged, investigating, resolved), type, severity.
- QTableView or QListWidget where each insight shows title, description, date, severity, status, supporting ticket count.
- Actions via QPushButtons or context menu: Acknowledge, Mark Investigating, Mark Resolved, Add Note (opens QDialog with QTextEdit).
- "Investigate in Chat" QPushButton opens a chat session with the insight's context pre-loaded.

### 4.4 Lightdash Integration (Optional, Config-Gated)

- Add a feature flag in Settings: "Enable Lightdash write-back."
- When disabled (default): add "Export for Lightdash" button that generates CSV from `ticket_index` (ticket_id, trc_code, friction_type, sub_pattern, sentiment_polarity, anomaly_flag, issue_snippet, root_cause_hint) for manual upload.
- When enabled: after each NLP scan, write enrichment fields to the Lightdash source database. Implementation depends on Lightdash's data layer (direct DB write or API).

---

## File Structure (New/Modified)

```
src/
  tools/
    alma_query.py          # NEW — CLI query tool for Gemini (queries ticket_index + persistent tables)
    alma_query_test.py     # NEW — test suite
  data/
    migrations/
      006_persistence.sql  # NEW — ticket_index + all new table definitions
    smart_pipeline.py      # MODIFY — VOCBuilder.run() accepts optional report_definition config
    backfill.py            # NEW — backfill runner for adding/populating new ticket_index columns
    report_definitions/    # NEW — JSON configs for pre-built reports
      voc_root_cause.json
      monthly_billing.json
      eng_bug_tracker.json
      client_churn.json
  services/
    ticket_index_writer.py # NEW — dedup gate + upsert logic for ticket_index
    clear_session.py       # NEW — Clear & Close handler (wipes ephemeral tables, preserves persistent)
    post_scan_persist.py   # NEW — scan → snapshot/trend/insight writer (reads from ticket_index)
    post_report_persist.py # NEW — report → analysis_runs writer
    context_injector.py    # NEW — assembles system context for chat
    chat_session.py        # NEW — session CRUD operations
    validation_engine.py   # NEW — async claim validation (QThread worker)
    scheduler.py           # NEW — QTimer-based schedule checker for smart reports
  ui/
    pages/
      ai_reports_page.py   # MODIFY — QTabWidget layout (canvas, history, prompts)
      nlp_scanner_page.py  # MODIFY — add timeline, dashboard, explorer widgets
      smart_reporting_page.py # MODIFY — pipeline cards, schedule management
      insight_ledger_page.py  # NEW — insight tracking view
      chat_page.py         # NEW — standalone chat page (QWidget)
    widgets/
      evidence_panel.py    # NEW — context-reactive QWidget sidebar
      report_viewer.py     # NEW — structured report renderer with action buttons
      ticket_explorer.py   # NEW — QTableView with filtering/sorting (backed by ticket_index)
      scan_timeline.py     # NEW — custom QPainter scan history dots
      prompt_builder.py    # NEW — conversational prompt config (chat + preview)
      chat_thread.py       # MODIFY — add tool result rendering, validation badges
      session_selector.py  # NEW — QComboBox/QListWidget for chat sessions
```

All UI components are PySide6 QWidgets. Layouts use QVBoxLayout, QHBoxLayout, QSplitter, QTabWidget. The evidence panel connects to the chat and report viewer via Qt signals — when a ticket_id_clicked signal emits, the evidence panel's slot fetches and displays the ticket detail. Chat messages render in a QScrollArea with custom QWidget items per message (not a web view).

---

## Key Implementation Notes

1. **Gemini CLI is the engine.** All new query tools are Python scripts Gemini calls via run_shell_command. Don't build a separate API server — keep everything as CLI tools and SQLite queries.

2. **`ticket_index` is the single source of truth for ticket-level data.** All `alma_query` commands that return ticket data query `ticket_index`, not the ephemeral tables. The ephemeral `conversations` table is only used for `ticket-detail` when the user wants the full thread. If ephemeral data is cleared, `alma_query` degrades gracefully — it returns `ticket_index` metadata + `issue_snippet` instead of the full thread.

3. **The dedup gate saves money and prevents data inflation.** When overlapping scan windows hit the same ticket, the dedup gate skips re-classification for high-confidence tickets. This means: no duplicate rows in `ticket_index`, accurate trend counts in `scan_category_snapshots`, and lower Gemini token costs on repeated scans. Log dedup stats per scan (classified/skipped/updated) so users can see the cost savings.

4. **Clear & Close wipes ephemeral tables, not the database file.** The refactored handler iterates over a defined list of ephemeral tables, DELETEs their contents, drops and recreates the FTS5 virtual table, and VACUUMs the database. This physically removes the data from disk. Persistent tables (including `ticket_index` with its PHI-free issue snippets) survive intact.

5. **Persistence writes are fire-and-forget on the happy path.** Post-scan and post-report hooks should not block the UI. Use QThread or QRunnable workers for background SQLite writes. If a write fails, log the error but don't interrupt the user's workflow.

6. **Context injection is the highest-leverage change.** Even before the UI redesign, wiring context injection into the existing Follow-Up Chat will dramatically improve the conversational experience. Ship this early.

7. **The default VOC config must produce identical output to the current hardcoded VOCBuilder.** Before creating any custom Report Definitions, run both paths (hardcoded and config-driven with `_default_voc_config()`) on the same data and diff outputs. Any divergence means the parameterization introduced a bug.

8. **Evidence panel is read-only and reactive.** It doesn't have its own data fetching — it connects to Qt signals from the chat and report components. When a `ticket_id_clicked(str)` signal emits, the evidence panel's slot calls `alma_query ticket-detail` and renders the result (full thread if available, issue_snippet if not). When a `finding_selected(dict)` signal emits, it queries trend_snapshots for the relevant metric. Use QSplitter so users can resize or collapse it.

9. **Chat sessions don't need to be fancy.** A JSON array of messages in SQLite is fine. Don't over-engineer with message tables or real-time sync. The user is one person on one machine.

10. **Report Definitions are JSON, not code.** The AI-assisted configurator produces JSON that `VOCBuilder.run()` interprets as config. If a config is malformed (missing required fields, referencing nonexistent prompt files), VOCBuilder returns an error before dispatching any LLM calls. Users can always fall back to the pre-built templates.

11. **Backfill is a first-class operation.** When a new column is added to `ticket_index`, there should be a clear path to populate it: derivable columns get a single UPDATE statement; columns requiring re-reading tickets get a targeted import + enrichment pass + clear. Build `backfill.py` as a CLI tool that handles both cases, runnable as `python -m src.data.backfill --column issue_snippet --where "issue_snippet IS NULL" --batch-size 50`.

12. **Qt-specific UI notes:** Use QTabWidget for the AI Reports tab layout. The Analysis Canvas is a QSplitter (horizontal) with the report+chat scroll area on the left and evidence panel on the right. Chat messages render as custom QWidget items in a QVBoxLayout inside a QScrollArea — not a QTextBrowser or web view, because we need interactive elements (clickable ticket IDs, action buttons on findings). Style with Qt stylesheets (QSS) to match the existing Alma green theme. The scan timeline in NLP Scanner is a custom QWidget with paintEvent override drawing dots and lines. The Ticket Explorer QTableView should be backed by a QSqlTableModel or QSortFilterProxyModel pointing at `ticket_index` — this means it works even after Clear & Close.
