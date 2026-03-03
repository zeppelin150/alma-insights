# Alma Insights — Data Architecture Kernel

**Purpose**: Briefing document for architecture sprint planning. Covers the current data infrastructure, the scaling bottleneck, all downstream dependencies, and three candidate solutions for rearchitecting to support 500K-row datasets.

---

## 1. Current Architecture

### Data Flow
```
Lightdash CSV Export
    → csv_ingestion.py (parse + column mapping)
    → SQLite warehouse (data/local_warehouse.db)
    → Python fetchall() → in-memory processing
    → Result dict → UI (stored as _last_analysis_result)
    → Optional: Gemini CLI subprocess for AI enhancement
```

### Storage Layer
- **SQLite database** (`data/local_warehouse.db`) with `conversations` table as the core table
- Key columns: `ticket_id`, `subject`, `trc_code`, `status`, `csat_score`, `created_at`, `full_thread` (full conversation text, typically 1-5 KB per row)
- FTS5 full-text search index on conversation content
- Additional tables: `anomaly_flags`, `rolling_stats`, `daily_counts`, `hourly_counts`, `analysis_reports`, `nlp_scans`, `nlp_patterns`, `nlp_tickets`, `compound_terms`, `term_feedback`, `agent_health`, `cost_limits`, etc.

### Processing Layer (trending_engine.py — the primary concern)
The main analysis pipeline (`run_full_analysis`) is a 12-step sequential pipeline:

| Step | What | Memory Behavior |
|------|------|-----------------|
| 1 | NLTK data prep | Negligible |
| 2 | Load compound terms | Negligible |
| **3** | **`_fetch_conversations()` — SELECT full_thread fetchall()** | **Materializes ALL conversations into Python list-of-dicts** |
| 4 | Preprocess text + VADER sentiment scoring | Mutates conversation dicts in-place (adds `_sentiment` key), builds `texts[]` + `texts_meta[]` |
| 5 | TF-IDF matrix (sklearn) | Sparse matrix in memory; reasonable |
| 5b | Sentence-transformer embeddings (optional) | ~150-250 MB model + embedding vectors |
| **6** | **`compute_sentiment_trends()` — re-fetches all conversations** | **Duplicate copy #1** |
| **7** | **`compute_rising_terms()` — re-fetches all conversations** | **Duplicate copy #2** |
| 8 | Topic modeling (NMF or K-Means) — K-Means re-fetches | Conditional duplicate (kmeans path only) |
| 9 | Cross-TRC series build — re-fetches if `trc_filter` set | Conditional duplicate (filtered analyses only) |
| 10 | Cross-TRC correlations | Uses pre-built series; reasonable |
| 11 | Temporal lead-lag detection | Lightweight numerical |
| 12 | Compound discovery | Reads `full_thread` from Step 3 conversations — last consumer of raw text |

**Result**: 3-4 copies of the full conversation dataset live in memory simultaneously during analysis.

### `_fetch_conversations()` — The Root Issue
```python
def _fetch_conversations(conn, date_start, date_end, trc_filter):
    query = f"""
        SELECT ticket_id, subject, trc_code, status, csat_score, created_at, full_thread
        FROM conversations
        WHERE {where}
        ORDER BY created_at
    """
    return [dict(r) for r in conn.execute(query, params).fetchall()]
```
- `fetchall()` materializes the entire result set
- `dict(r)` creates a Python dict per row (with full_thread string copy)
- Called independently by each compute function with identical parameters
- At 20K conversations × ~3 KB avg → ~60 MB per call → ~240 MB for 4 calls
- At 500K conversations × ~3 KB avg → **~1.5 GB per call → ~6 GB for 4 calls**

---

## 2. The Scaling Problem

### Current State
- Tested on ~20K conversations (native small sets)
- Memory usage observed: **4.5 GB** (up from ~400 MB baseline)
- Root cause: redundant fetches + no streaming + all processing in Python memory

### Target State
- Must handle **500,000 conversations** (max design capacity)
- Must not crash the app or exceed reasonable memory bounds (~1-2 GB ceiling)
- Math: 500K rows × 3 KB avg full_thread = **1.5 GB** just for one materialized copy

### What Breaks at Scale
1. **`_fetch_conversations` fetchall()**: 1.5 GB per call, 3-4 calls = 4.5-6 GB just from conversation copies
2. **TF-IDF vectorizer**: sklearn's TfidfVectorizer on 500K documents would produce a massive sparse matrix
3. **VADER sentiment scoring**: 500K calls to `polarity_scores()` is CPU-bound (~10-15 min)
4. **`_bucket_conversations()` + `defaultdict`**: Creates additional reference copies grouped by TRC
5. **`_last_analysis_result` cache in UI**: Holds entire result dict indefinitely
6. **sentence-transformers embeddings**: 500K × 384-dim vectors = ~750 MB, plus 150 MB model
7. **Agent pipeline (scan_orchestrator.py)**: Independently fetches `full_thread` per batch — not shared with trending pipeline

---

## 3. Downstream Dependency Map

### Direct Consumers of `run_full_analysis()` Return Value

| Consumer | File | What It Reads |
|----------|------|---------------|
| TrendingTopics UI | `trending_topics.py` | All keys: sentiment, terms, topics, correlations, discovery, embedding_clusters |
| AI Enhancement Worker | `trending_topics.py:88` | Only `topics` and `terms` (Gemini smoothing + keyword suggestions) |
| AI Smoothing Apply | `trending_topics.py:1655` | `topics` from `_last_analysis_result` (re-labels topic clusters) |
| Report Builder | `report_builder.py:82` | `terms.all_terms`, `sentiment` by TRC |
| AB Analysis | `ab_analysis.py:104` | `terms.all_terms` |
| Hypothesis Testing | `trending_topics.py:190` | Separate pipeline (`test_hypothesis()`), does NOT use `run_full_analysis` result |

### Independent Data Pipelines (DO NOT share data with trending)

| Pipeline | File(s) | Data Source | Notes |
|----------|---------|-------------|-------|
| **Theta Engine** | `theta_engine.py` | Own SQL queries (daily_counts, rolling_stats) | Reads aggregated stats, not raw conversations |
| **Incident Engine** | `incident_engine.py` | Own SQL queries (daily_counts, hourly_counts) | CUSUM-based anomaly detection on pre-aggregated counts |
| **NLP Scanner** | `scan_worker.py`, `scan_orchestrator.py` | Own SQL queries per TRC per batch | Fetches `full_thread` independently per batch packing; batches are 100-500 tickets each |
| **VoC Builder** | `voc_builder.py` | Own SQL queries per scan/TRC | Reads from `nlp_scans` / `nlp_patterns` / `nlp_tickets` tables |
| **Agent System** | `worker_agent.py`, `analyst_agent.py`, `tool_registry.py` | Own SQL queries per tool call | `get_full_thread` tool fetches individual tickets; `analyst_agent` does `GROUP_CONCAT(full_thread)` per scan |
| **TRC Analytics** | `trc_analytics.py` | Own SQL queries (aggregated metrics) | No raw conversation fetching |
| **Product Gap Engine** | `product_gap_engine.py` | Own SQL queries with fetchall | Fetches full conversations for date range |
| **NLP Meta Analyzer** | `nlp_meta_analyzer.py` | Reads from `nlp_*` tables (scan results) | Heavy fetchall user but on aggregated NLP data, not raw conversations |
| **Conversation Search UI** | `db_manager.py:search_conversations()` | Paginated SQL (LIMIT/OFFSET) | Already handles pagination correctly |

### Gemini Bridge
- `gemini_client.py`: Subprocess-based CLI wrapper — sends text to Gemini via stdin, receives response via stdout
- Does NOT hold conversation data — receives pre-formatted prompt text
- PII redaction happens before sending — strips emails, phone numbers, names
- Memory impact: negligible (subprocess isolation)

### Job Queue
- `job_queue.py`: `CallableWorker(QThread)` — wraps arbitrary callables
- Holds reference to the callable + args during execution
- When running `run_full_analysis`, the QThread worker holds a reference to the conversations list until the thread completes — this prevents GC even if the function returns

### Report History
- `analysis_reports` table: Stores JSON-serialized analysis results
- `report_builder.py` calls `run_full_analysis()` to build reports
- Serialized result does NOT include raw `full_thread` (it's not in the return dict)
- But `_last_analysis_result` in the UI caches the full result dict in memory indefinitely

---

## 4. Architecture Options

### Option A: Localhost Node.js Server
**Concept**: Node.js middleware between SQLite and the PySide6 UI. Node handles data aggregation, streaming, and caching. UI becomes a thin display layer.

**Pros**:
- V8's streaming + async I/O is built for this pattern
- Can serve multiple consumers (UI, agents, reports) from one data cache
- Could eventually become a REST API for web UI migration
- Memory management: Node's streaming prevents materialization

**Cons**:
- Adds a deployment dependency (Node.js runtime)
- Increases attack surface (localhost HTTP server)
- Requires rewriting all data access patterns (trending_engine, theta, incident, agents, VoC)
- Python → HTTP → Node → SQLite → HTTP → Python round-trip latency
- Two language runtimes for a desktop app
- SQLite concurrent access from Node + Python needs WAL mode and careful locking

**Effort**: HIGH — essentially rewriting the data layer in JavaScript

### Option B: Containerize
**Concept**: Docker container with the full app (Python + SQLite), with resource limits and volume mounts.

**Pros**:
- Memory limits prevent OOM (container gets killed instead of host swap)
- Reproducible environment
- Could add Postgres/Redis for scaling later

**Cons**:
- Doesn't solve the fundamental memory problem — just contains the blast radius
- Desktop UX suffers (Docker Desktop required, startup latency)
- Users need Docker knowledge
- PySide6 GUI inside Docker is painful (X11 forwarding or VNC)
- Adds massive deployment complexity for what's currently a simple Python app

**Effort**: MEDIUM for infrastructure, but ZERO for the actual memory problem

### Option C: Push Computation to SQLite + Streaming Cursors (Recommended)
**Concept**: SQLite IS the compressed dataset. Instead of `fetchall()` into Python, pre-aggregate in SQL and stream results in batches. Only materialize what you need, when you need it.

**Pros**:
- Zero new dependencies — SQLite is already the data store
- Immediate wins: sentiment scoring can be a stored column, not computed at runtime
- SQL aggregation (GROUP BY trc_code, time_bucket) replaces Python loops
- Streaming cursors (`fetchmany(batch_size)`) prevent full materialization
- Already proven: conversation_search uses paginated SQL successfully
- Incremental migration — each function can be converted independently

**Cons**:
- Some operations genuinely need row-level text (TF-IDF, compound discovery)
- SQL window functions can't replace sklearn's TfidfVectorizer
- Requires careful migration to avoid breaking downstream consumers
- SQLite's WAL mode needed for concurrent reads from UI thread + worker threads

**Implementation Strategy (Phased)**:

**Phase 1 — Immediate (this sprint)**:
- Single-fetch consolidation (eliminate 2-3 redundant `_fetch_conversations` calls)
- Strip `per_trc_series` from returned result dict
- Strip `full_thread` after compound discovery step

**Phase 2 — Store Sentiment as Column**:
- Add `sentiment_compound REAL` column to conversations table
- Compute sentiment once at CSV ingestion time (or via migration script)
- `compute_sentiment_trends()` becomes a pure SQL GROUP BY query
- Eliminates need to fetch `full_thread` for sentiment

**Phase 3 — SQL-Level Aggregation**:
- Pre-aggregate daily/weekly volume + avg sentiment per TRC in SQL
- `_bucket_conversations()` → SQL window functions
- `compute_sentiment_trends()` → single SQL query returning aggregated series
- Cross-TRC correlations → SQL-built time series without Python materialization

**Phase 4 — Streaming for Text-Heavy Operations**:
- TF-IDF (rising terms, topics): Stream `full_thread` in batches of 1000, tokenize + partial fit
- Use sklearn's `HashingVectorizer` instead of `TfidfVectorizer` for constant-memory operation
- Compound discovery: Stream and process in chunks

**Phase 5 — Architecture Hardening**:
- SQLite WAL mode for concurrent access
- Connection pool with read-only connections for analysis threads
- Memory-mapped I/O for large result sets
- Background indexing for FTS5 updates

**Effort**: LOW-MEDIUM per phase, total effort spread across sprints

### Option D: Stream from Lightdash Directly
**Concept**: Bypass local SQLite entirely — query Lightdash's API/database for aggregated data on demand.

**Pros**:
- Eliminates local storage entirely
- Lightdash already has pre-computed metrics and aggregations
- Slimmest attack surface (no local data at rest)

**Cons**:
- Network dependency — app doesn't work offline
- Lightdash API rate limits and latency
- Can't do custom NLP (VADER, TF-IDF, compound discovery) without local text
- Agents need full_thread for analysis — can't get that from Lightdash aggregations
- Tight coupling to Lightdash's data model and API versioning
- Loss of historical analysis capability (no local report history)

**Effort**: HIGH — requires Lightdash API integration + rethinking what analyses are even possible without local text

---

## 5. Recommendation

**Option C (SQL-Push + Streaming)** in phases, with Phase 1 as the immediate fix.

**Why not Node.js**: The data is already in SQLite. Adding a Node server means maintaining two runtimes, rewriting the data layer, and introducing HTTP overhead — all to solve a problem that SQLite can solve natively with better query patterns.

**Why not containerize**: Containers don't solve the memory problem, they just hide it. The app still crashes (or gets OOM-killed) at 500K rows.

**Why not Lightdash direct**: Too many features depend on local `full_thread` text (NLP, agents, compound discovery, topic modeling). Lightdash doesn't expose raw conversation text through its API.

**The core insight**: The memory problem isn't that SQLite can't handle 500K rows — it's that we're materializing the entire dataset into Python memory 3-4 times per analysis run. Fix the access patterns, not the infrastructure.

---

## 6. Immediate Fix: Single-Fetch Consolidation

See companion document: `CONSOLIDATION_FIX_PLAN.md`

**Summary**: Add `conversations=None` parameter to `compute_sentiment_trends()`, `compute_rising_terms()`, and `compute_topic_clusters()`. When called from `run_full_analysis()`, pass the pre-fetched conversation list. Eliminates 2-3 redundant `fetchall()` calls. ~20 lines changed, all in `trending_engine.py`, zero external impact.

**Expected savings**: ~150-200 MB at 20K rows, ~900 MB - 1 GB at 100K rows.

---

## 7. File Inventory (Data Layer)

| File | LOC | Role | fetchall() Calls | Memory Risk |
|------|-----|------|-------------------|-------------|
| `trending_engine.py` | ~2000 | TF-IDF, sentiment, topics, correlations | 10+ | **HIGH** — fetches full_thread multiple times |
| `db_manager.py` | ~2100 | All SQLite operations | 60+ | MEDIUM — most are metadata/aggregated queries |
| `scan_worker.py` | ~900 | NLP scanner per-TRC processing | 10+ | MEDIUM — batch-scoped fetches |
| `scan_orchestrator.py` | ~1800 | NLP scan coordination | 8+ | MEDIUM — fetches full_thread per batch |
| `nlp_meta_analyzer.py` | ~900 | Cross-scan NLP analysis | 25+ | MEDIUM — reads from NLP result tables |
| `theta_engine.py` | ~700 | EWMA anomaly detection | 5 | LOW — reads aggregated counts |
| `incident_engine.py` | ~600 | CUSUM incident detection | 2 | LOW — reads aggregated counts |
| `voc_builder.py` | ~2000 | Voice of Customer reports | 2 | LOW — scan-scoped |
| `product_gap_engine.py` | ~250 | Product gap analysis | 3 | MEDIUM — fetches full conversations |
| `report_builder.py` | ~200 | Report generation | 2 (via run_full_analysis) | HIGH — inherits trending_engine's problem |
| `ab_analysis.py` | ~130 | A/B comparison | 1 (via run_full_analysis) | HIGH — inherits trending_engine's problem |

---

## 8. Key Questions for Architecture Sprint

1. **Sentiment column migration**: Should we backfill `sentiment_compound` for all existing data during a one-time migration, or compute lazily on first access?

2. **TF-IDF without full materialization**: Can we use `HashingVectorizer` with streaming batches? What's the quality tradeoff vs. `TfidfVectorizer`?

3. **Agent pipeline isolation**: The agent system (scan_orchestrator → worker_agent) fetches `full_thread` independently. Should it share a cache with trending_engine, or is batch-scoped fetching already optimal?

4. **WAL mode**: SQLite WAL mode allows concurrent readers + one writer. Are there any write-during-analysis scenarios that need transaction management?

5. **Phase 2 vs Phase 3 priority**: Is storing sentiment-as-column (Phase 2, eliminates full_thread need for sentiment) more impactful than SQL aggregation (Phase 3, eliminates Python bucketing)?

6. **500K threshold**: Is 500K the hard ceiling, or should we design for 1M+ with pagination/sampling strategies?

7. **Lightdash integration future**: If we eventually want live Lightdash streaming (Option D), should Phase 3 SQL queries be designed as API-compatible views?
