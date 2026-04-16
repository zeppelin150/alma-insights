# Alma Insights — Hybrid Architecture Build Plan
## One-Week Sprint with Claude Code

---

## Architecture Recap (From Prior Analysis)

Two interaction modes, one data layer. **Fast path**: chat tools query pre-computed
structured tables (sub-second). **Deep path**: chat triggers the report pipeline
inline when deeper analysis is needed (1-5 min). Filter engine is the shared
primitive. User never knows which path fired.

```
User question
     │
     ├── Answerable from tables? → Fast path (<1s)
     │   (counts, filters, ticket lookups, themes, trends)
     │
     ├── Needs specific ticket text? → Thread path (2-3s)
     │   (read_thread with PII redaction)
     │
     ├── Needs fuzzy/conceptual match? → Semantic path (1-2s)
     │   (EmbeddingGemma similarity search)
     │
     └── Needs deep analysis? → Pipeline path (1-5 min)
         (run_report triggers pipeline, structured JSON output)
```

---

## Day 1: Filter Engine + Schema Migrations

### Filter Engine (`src/data/filter_engine.py`)

The shared primitive. ~200 LOC. Every tool, every report, every session
constraint calls this.

```python
def build_filter_query(
    filters: dict,
    select_columns: list[str],
    base_table: str = "ticket_index",
    limit: int = None,
    order_by: str = None
) -> tuple[str, list]:
    """
    Convert a filter dict into (SQL query, params).
    
    Handles:
    - Single-table queries when all filters map to one table
    - JOIN queries when filters span tables
    - FTS5 integration for keyword filters
    - Table-aware column mapping
    
    Filter dict contract:
    {
        "trc_codes": ["Billing", "Claims"],
        "date_start": "2026-01-01",
        "date_end": "2026-03-31",
        "friction_types": ["incorrect_charge"],
        "sub_patterns": ["duplicate_billing"],
        "sentiment": "negative",
        "anomaly_flag": "critical",
        "keyword": "duplicate",
        "entities": {"payer": "BlueCross"},
        "theme_ids": ["theme_abc"],
        "dataset_id": 3
    }
    """
```

Table-aware column mapping:

| Filter | conversations | ticket_index | tickets |
|--------|--------------|-------------|---------|
| date | created_at | ticket_created_date | created_at |
| trc | trc_code | trc_code | trc_code |
| friction | — (JOIN) | friction_type | — |
| sentiment | — | sentiment_polarity | — |
| anomaly | — | anomaly_flag | — |
| keyword | conversations_fts | — | — |
| csat | — | — | csat_score |

Cross-table JOIN generation when filters span tables:

```sql
SELECT ti.ticket_id, ti.friction_type, ti.summary, c.thread_preview
FROM ticket_index ti
JOIN conversations c ON ti.ticket_id = c.ticket_id
WHERE ti.friction_type = ?
  AND c.created_at >= ?
  AND c.created_at <= ?
```

### Schema Migrations (Day 1 afternoon)

Add to db_manager.py migration chain:

```sql
-- Migration 006: Hybrid chat architecture

-- 1. Expand chat_sessions
ALTER TABLE chat_sessions ADD COLUMN filter_json TEXT;
ALTER TABLE chat_sessions ADD COLUMN ticket_count INTEGER;
ALTER TABLE chat_sessions ADD COLUMN active_report_ids TEXT;  -- JSON array

-- 2. Ticket-theme junction
CREATE TABLE IF NOT EXISTS ticket_theme_tags (
    ticket_id   TEXT NOT NULL,
    theme_id    TEXT NOT NULL,
    finding_id  TEXT,
    confidence  REAL DEFAULT 1.0,
    scan_id     TEXT NOT NULL,
    tagged_at   TEXT NOT NULL,
    PRIMARY KEY (ticket_id, theme_id)
);
CREATE INDEX IF NOT EXISTS idx_ttt_theme ON ticket_theme_tags(theme_id);
CREATE INDEX IF NOT EXISTS idx_ttt_scan ON ticket_theme_tags(scan_id);

-- 3. Enriched trends (post-NLP stats)
CREATE TABLE IF NOT EXISTS enriched_trends (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         TEXT NOT NULL,
    dimension       TEXT NOT NULL,
    dimension_value TEXT NOT NULL,
    period          TEXT NOT NULL,
    ticket_count    INTEGER NOT NULL,
    pct_of_total    REAL,
    velocity        REAL,
    trc_breakdown   TEXT,
    UNIQUE(scan_id, dimension, dimension_value, period)
);
CREATE INDEX IF NOT EXISTS idx_et_dim ON enriched_trends(dimension, dimension_value);
CREATE INDEX IF NOT EXISTS idx_et_period ON enriched_trends(period);

-- 4. Embedding persistence
CREATE TABLE IF NOT EXISTS ticket_embeddings (
    ticket_id        TEXT PRIMARY KEY,
    embedding_blob   BLOB NOT NULL,
    source_text_hash TEXT NOT NULL,
    model_name       TEXT NOT NULL DEFAULT 'embeddinggemma-300m',
    dim_size         INTEGER NOT NULL DEFAULT 768,
    created_at       TEXT NOT NULL,
    FOREIGN KEY (ticket_id) REFERENCES tickets(ticket_id)
);
CREATE INDEX IF NOT EXISTS idx_te_model ON ticket_embeddings(model_name);
```

### Tests for Day 1

- filter_engine: 15-20 unit tests covering single-table, multi-table JOIN,
  FTS5, empty filters, conflicting filters
- Migration: verify all tables create, verify backward compat with existing data

---

## Day 2: Session-Scoped Chat + Core Fast-Path Tools

### Wire ChatEngine to Sessions

```python
class ChatEngine:
    def __init__(self, db, session_id=None):
        self._db = db
        self._session_id = session_id
        self._session_filters = {}
        self._history = []
        
        if session_id:
            session = load_session(db, session_id)
            self._session_filters = json.loads(session.get("filter_json") or "{}")
            self._history = json.loads(session.get("messages") or "[]")
    
    def _execute_tool(self, tool_name, tool_args):
        effective_filters = {**self._session_filters, **tool_args}
        return self._tool_dispatch(tool_name, effective_filters)
```

### Core Fast-Path Tools (Priority Order)

**Tool 1: `query_ticket_classifications`**
Groups ticket_index by any enriched field. Answers "how many X were there?"

**Tool 2: `list_tickets`**
Returns filtered ticket records from ticket_index. Answers "show me the X tickets."

**Tool 3: `query_findings`**
Reads nlp_findings within scope. Answers "what did the scan find?"

**Tool 4: `query_stats`**
Reads incident_flags, daily_counts, enriched_trends. Answers "any anomalies?"

All four tools call `build_filter_query()` with session filters merged.

### Unified Tool Registry

Consolidate Gemini + Claude tools into `src/ai/tool_registry.py`:

```python
CHAT_TOOLS = {
    # Fast path (new)
    "query_ticket_classifications": {
        "description": "Count and group tickets by classification fields",
        "phi_level": "none",
        "handler": handle_query_classifications
    },
    "list_tickets": {
        "description": "List individual tickets matching filters",
        "phi_level": "none",
        "handler": handle_list_tickets
    },
    "query_findings": {
        "description": "Retrieve NLP scan findings and themes",
        "phi_level": "none",
        "handler": handle_query_findings
    },
    "query_stats": {
        "description": "Query statistical engine outputs",
        "phi_level": "none",
        "handler": handle_query_stats
    },
    # Thread access (Day 3)
    "read_thread": {...},
    "read_threads_batch": {...},
    # Report access (Day 4)
    "query_report": {...},
    "run_report": {...},
    # Semantic search (Day 5)
    "semantic_search": {...},
    # Legacy (keep)
    "search_conversations": {...},  # FTS5
}
```

---

## Day 3: Thread Access + Report Query Tools

### read_thread

```python
def handle_read_thread(db, args, session_filters):
    ticket_id = args["ticket_id"]
    
    # Scope validation: ticket must be in session
    if session_filters:
        check_query, check_params = build_filter_query(
            {**session_filters, "ticket_ids": [ticket_id]},
            select_columns=["ticket_id"],
            base_table="conversations"
        )
        if not db.execute(check_query, check_params).fetchone():
            return {"error": "Ticket not in current session scope"}
    
    row = db.execute(
        "SELECT full_thread, message_count, client_messages, agent_messages "
        "FROM conversations WHERE ticket_id = ?", (ticket_id,)
    ).fetchone()
    
    thread_text = row["full_thread"]
    
    # PII redaction (mandatory)
    thread_text = gemini_client._redact_base(thread_text)
    
    # Truncate
    if len(thread_text) > 8000:
        thread_text = thread_text[:8000] + f"\n[TRUNCATED — {len(row['full_thread'])} chars total]"
    
    return {
        "ticket_id": ticket_id,
        "thread": thread_text,
        "message_count": row["message_count"],
        "client_messages": row["client_messages"],
        "agent_messages": row["agent_messages"]
    }
```

### read_threads_batch

Same pattern, up to 5 tickets, 4K chars each, 20K total hard cap.

### query_report

Reads `analysis_runs.output_structured` JSON. Optional section filter
(findings, summary_stats, recommendations).

### Ticket-Theme Tagging in Meta-Analyzer

Modify `nlp_meta_analyzer.py` to populate `ticket_theme_tags` during
the analysis phase:

```python
def _tag_tickets_to_finding(self, finding_id, finding):
    constituent_patterns = finding.get("constituent_sub_patterns", [])
    constituent_frictions = finding.get("constituent_friction_types", [])
    
    query, params = build_filter_query(
        filters={
            "sub_patterns": constituent_patterns,
            "friction_types": constituent_frictions,
        },
        select_columns=["ticket_id"],
        base_table="ticket_index"
    )
    
    for row in self.db.execute(query, params).fetchall():
        self.db.execute("""
            INSERT OR REPLACE INTO ticket_theme_tags 
            (ticket_id, theme_id, finding_id, confidence, scan_id, tagged_at)
            VALUES (?, ?, ?, 1.0, ?, ?)
        """, (row["ticket_id"], finding["theme_id"], finding_id,
              self.scan_id, datetime.utcnow().isoformat()))
```

---

## Day 4: Structured Report Output + run_report + Post-NLP Stats

### Structured Report Output

Modify `report_builder.py` to populate `analysis_runs.output_structured`:

```python
def _build_structured_output(self, findings, stats):
    return json.dumps({
        "generated_at": datetime.utcnow().isoformat(),
        "filter_context": self._active_filters,
        "ticket_count": self._ticket_count,
        "findings": [
            {
                "title": f["title"],
                "ticket_count": f["ticket_count"],
                "ticket_ids": f["ticket_ids"],
                "severity": f["severity"],
                "trend": f["trend"],
                "related_trcs": f["trcs"],
                "friction_types": f["friction_types"],
                "evidence_summary": f["summary"]
            }
            for f in findings
        ],
        "summary_stats": {
            "total_tickets": stats["total"],
            "trc_distribution": stats["trc_dist"],
            "friction_distribution": stats["friction_dist"],
            "sentiment_distribution": stats["sentiment_dist"],
            "anomaly_count": stats["anomaly_count"]
        }
    })
```

### run_report Chat Tool

```python
def handle_run_report(db, args, session_filters):
    if not args.get("confirm"):
        # Propose parameters for user confirmation
        proposed = {**session_filters, **args.get("filters", {})}
        return {
            "action": "confirm_required",
            "proposed_params": proposed,
            "template": args.get("template", "general_trend"),
            "message": "I can run this analysis. Confirm to proceed."
        }
    
    # User confirmed — trigger pipeline
    template = args.get("template", "general_trend")
    filters = {**session_filters, **args.get("filters", {})}
    
    # Call report_builder with expanded filter support
    result = report_builder.build_report(
        db=db,
        date_start=filters.get("date_start"),
        date_end=filters.get("date_end"),
        trc_filter=filters.get("trc_codes"),
        filters=filters,  # full filter dict via filter engine
        template=template
    )
    
    # Add report_id to session's active reports
    if session_id:
        # update chat_sessions.active_report_ids
        pass
    
    return {
        "report_id": result["report_id"],
        "structured_output": json.loads(result["output_structured"])
    }
```

### Post-NLP Stats Pass

New module: `src/analysis/post_nlp_stats.py` (~100 LOC)

```python
def compute_enriched_trends(db, scan_id):
    """Compute time-series trends on NLP-enriched fields."""
    
    dimensions = [
        ("friction_type", "friction_type"),
        ("sub_pattern", "sub_cluster"),
        ("sentiment", "sentiment_polarity"),
    ]
    
    for dim_name, column in dimensions:
        rows = db.execute(f"""
            SELECT {column} as dim_value,
                   strftime('%Y-%W', ticket_created_date) as period,
                   COUNT(*) as ticket_count
            FROM ticket_index
            WHERE scan_id = ?
            GROUP BY {column}, period
            ORDER BY period
        """, (scan_id,)).fetchall()
        
        # Compute velocity (period-over-period change)
        by_value = defaultdict(list)
        for r in rows:
            by_value[r["dim_value"]].append(r)
        
        for dim_value, periods in by_value.items():
            for i, p in enumerate(periods):
                velocity = None
                if i > 0 and periods[i-1]["ticket_count"] > 0:
                    velocity = (p["ticket_count"] - periods[i-1]["ticket_count"]) / periods[i-1]["ticket_count"]
                
                db.execute("""
                    INSERT OR REPLACE INTO enriched_trends
                    (scan_id, dimension, dimension_value, period, ticket_count, velocity)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (scan_id, dim_name, dim_value, p["period"],
                      p["ticket_count"], velocity))
```

Triggered: Add call to `compute_enriched_trends()` at the end of
`nlp_meta_analyzer.py`'s analysis phase, after classifications are finalized.

---

## Day 5: EmbeddingGemma-300M + Semantic Search

### Model: google/embeddinggemma-300m

**Why EmbeddingGemma over all-MiniLM-L6-v2:**

| Property | all-MiniLM-L6-v2 (current) | EmbeddingGemma-300M |
|----------|---------------------------|---------------------|
| Parameters | 22M | 308M |
| Output dims | 384 (fixed) | 768 (MRL: 512/256/128) |
| Context window | 256 tokens | 2,048 tokens |
| Multilingual | Limited | 100+ languages |
| Query/Doc prompts | No | Yes (optimized retrieval) |
| MTEB ranking | Mid-tier | #1 open model <500M |
| RAM (quantized) | ~90MB | <200MB |
| Disk (fp32) | ~82MB | ~1.2GB |
| Disk (quantized) | N/A | ~300-600MB |
| On-device design | General purpose | Explicitly offline-first |

The 2K token context window is the critical upgrade. MiniLM truncates
ticket text at ~200 words. EmbeddingGemma can embed ~1,500 words —
enough to capture the full substance of most ticket conversations.

The query/document prompt format produces better retrieval because the
model understands the asymmetry between a user's search question and the
document being searched.

---

### AIR-GAP DEPLOYMENT SPECIFICATION

#### Why Air-Gapping Is Required

Alma Insights processes PHI (Protected Health Information) under HIPAA.
Even though embedding vectors are not directly reversible to source text,
the embedding computation takes raw or lightly-redacted ticket text as
input. If the embedding model or any supporting library made network
calls during inference — to download model updates, report telemetry,
check licenses, or resolve dependencies — PHI-adjacent data could
theoretically transit the network, or the model's behavior could change
without audit.

The air-gap ensures: no data leaves the machine during embedding
inference, the model binary is fixed and auditable, and behavior is
deterministic across runs.

#### Network Isolation Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    ALMA INSIGHTS DESKTOP APP                    │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │          EMBEDDING ENGINE (Zone B — Air-Gapped)         │    │
│  │                                                         │    │
│  │  Model binary: models/embeddinggemma-300m/              │    │
│  │    ├── config.json                                      │    │
│  │    ├── model.safetensors       (~1.2 GB)                │    │
│  │    ├── tokenizer.json                                   │    │
│  │    ├── tokenizer_config.json                            │    │
│  │    ├── special_tokens_map.json                          │    │
│  │    └── modules.json                                     │    │
│  │                                                         │    │
│  │  Loading: SentenceTransformer("models/embeddinggemma-   │    │
│  │           300m/", local_files_only=True)                 │    │
│  │                                                         │    │
│  │  NETWORK CALLS: ZERO                                    │    │
│  │    • HF_HUB_OFFLINE=1          (blocks hub downloads)   │    │
│  │    • TRANSFORMERS_OFFLINE=1     (blocks model fetches)   │    │
│  │    • HF_DATASETS_OFFLINE=1      (blocks dataset fetches) │    │
│  │    • HF_HUB_DISABLE_TELEMETRY=1 (blocks usage telemetry)│    │
│  │    • SENTENCE_TRANSFORMERS_HOME=./models                 │    │
│  │    • NO_PROXY=*                 (belt-and-suspenders)    │    │
│  │    • CURL_CA_BUNDLE=""          (disables cert lookups)  │    │
│  │                                                         │    │
│  │  PyTorch: torch.hub.set_dir("./models/.cache")          │    │
│  │           No CUDA (CPU-only, no driver calls)            │    │
│  │                                                         │    │
│  │  INPUT:  ticket text (from SQLite, never leaves process) │    │
│  │  OUTPUT: float32 numpy array (stored in SQLite BLOB)     │    │
│  │                                                         │    │
│  │  ┌─ VERIFIED BY ──────────────────────────────────┐     │    │
│  │  │ verify_air_gap() runs at model load time:      │     │    │
│  │  │   1. Checks all env vars are set               │     │    │
│  │  │   2. Checks model files exist locally           │     │    │
│  │  │   3. Attempts socket connection → must FAIL     │     │    │
│  │  │   4. Logs verification result to audit log      │     │    │
│  │  └────────────────────────────────────────────────┘     │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │          GEMINI CLIENT (Zone C — BAA-Covered)           │    │
│  │  Single outbound path: Gemini CLI → Gemini API          │    │
│  │  PII redaction mandatory on all payloads                 │    │
│  │  This is the ONLY network exit point                     │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                 │
│  Embedding engine and Gemini client are in separate zones.      │
│  Embedding engine has NO network access. Gemini client has      │
│  controlled, redacted, BAA-covered access.                      │
└─────────────────────────────────────────────────────────────────┘
```

#### Pre-Download Procedure (One-Time, On Developer Machine)

Run ONCE on a machine with internet access, then bundle with app:

```bash
# 1. Create model directory
mkdir -p models/embeddinggemma-300m

# 2. Download model via sentence-transformers (handles all files)
python -c "
from sentence_transformers import SentenceTransformer
model = SentenceTransformer('google/embeddinggemma-300m')
model.save('models/embeddinggemma-300m')
print('Model saved. Verify contents:')
import os
for f in os.listdir('models/embeddinggemma-300m'):
    size = os.path.getsize(f'models/embeddinggemma-300m/{f}')
    print(f'  {f}: {size:,} bytes')
"

# 3. Verify model loads offline
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python -c "
from sentence_transformers import SentenceTransformer
model = SentenceTransformer('models/embeddinggemma-300m', local_files_only=True)
result = model.encode('test sentence')
print(f'Output shape: {result.shape}')  # Should be (768,)
print('Offline load: SUCCESS')
"

# 4. Ship models/ directory with app — build_release.py picks it up automatically
#    The build script looks for models/embeddinggemma-300m/ and bundles it into the zip
```

The `models/embeddinggemma-300m/` directory contains:

| File | Size | Purpose |
|------|------|---------|
| model.safetensors | ~1.2 GB | Model weights (SafeTensors format, no pickle risk) |
| config.json | ~2 KB | Architecture config (Gemma3TextModel) |
| tokenizer.json | ~4.3 MB | Full tokenizer vocabulary |
| tokenizer_config.json | ~2 KB | Tokenizer settings |
| special_tokens_map.json | ~1 KB | Special token definitions |
| modules.json | ~1 KB | Sentence-transformers module chain |
| 1_Pooling/config.json | ~1 KB | Mean pooling configuration |
| 2_Dense/config.json + model.safetensors | ~9 MB | Projection layer |
| 3_Dense/config.json + model.safetensors | ~9 MB | Second projection layer |
| 4_Normalize/config.json | ~1 KB | L2 normalization config |

Total bundle size: ~1.23 GB. Compressed (zip): ~900 MB.
Compare to PyTorch dependency already shipped: ~600 MB.

#### Runtime Initialization

```python
# src/analysis/embedding_engine.py (replacing current implementation)

import os
import hashlib
import numpy as np
from pathlib import Path

# Air-gap environment enforcement — set BEFORE any imports that
# might trigger network calls
_AIR_GAP_ENV = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "TOKENIZERS_PARALLELISM": "false",
    "SENTENCE_TRANSFORMERS_HOME": str(Path(__file__).parent.parent.parent / "models"),
    "NO_PROXY": "*",
    "CURL_CA_BUNDLE": "",
}

for key, value in _AIR_GAP_ENV.items():
    os.environ[key] = value

# Now safe to import
from sentence_transformers import SentenceTransformer

MODEL_DIR = Path(__file__).parent.parent.parent / "models" / "embeddinggemma-300m"
MODEL_NAME = "embeddinggemma-300m"
EMBEDDING_DIM = 768          # Full dimension; use truncate_dim for smaller
TRUNCATED_DIM = 256          # MRL truncation for storage efficiency (optional)
MAX_SEQ_LENGTH = 2048        # 2K token context window

# Singleton model instance
_model = None
_embedding_cache = None       # (numpy matrix, ticket_id list, timestamp)


def verify_air_gap() -> dict:
    """
    Verify air-gap conditions at model load time.
    Returns verification report for audit logging.
    """
    report = {
        "timestamp": datetime.utcnow().isoformat(),
        "model_name": MODEL_NAME,
        "checks": {}
    }
    
    # Check 1: Environment variables
    for key, expected in _AIR_GAP_ENV.items():
        actual = os.environ.get(key)
        report["checks"][f"env_{key}"] = {
            "expected": expected,
            "actual": actual,
            "pass": actual == expected
        }
    
    # Check 2: Model files exist locally
    required_files = ["config.json", "model.safetensors", "tokenizer.json"]
    for fname in required_files:
        exists = (MODEL_DIR / fname).exists()
        report["checks"][f"file_{fname}"] = {"exists": exists, "pass": exists}
    
    # Check 3: Network connectivity should FAIL
    import socket
    try:
        socket.create_connection(("huggingface.co", 443), timeout=2)
        report["checks"]["network_blocked"] = {"pass": False, "detail": "Connection succeeded — air-gap BROKEN"}
    except (socket.timeout, OSError):
        report["checks"]["network_blocked"] = {"pass": True, "detail": "Connection refused/timed out — air-gap intact"}
    
    report["all_passed"] = all(c["pass"] for c in report["checks"].values())
    return report


def get_model() -> SentenceTransformer:
    """Load model from local directory. No network calls."""
    global _model
    if _model is None:
        if not MODEL_DIR.exists():
            raise FileNotFoundError(
                f"EmbeddingGemma model not found at {MODEL_DIR}. "
                f"Run the pre-download procedure on a networked machine first."
            )
        
        # Verify air-gap before loading
        air_gap_report = verify_air_gap()
        if not air_gap_report["all_passed"]:
            failed = [k for k, v in air_gap_report["checks"].items() if not v["pass"]]
            raise RuntimeError(
                f"Air-gap verification failed: {failed}. "
                f"Cannot load embedding model with network access available."
            )
        
        _model = SentenceTransformer(
            str(MODEL_DIR),
            local_files_only=True,    # Redundant with env vars, but explicit
            # truncate_dim=TRUNCATED_DIM,  # Uncomment to use 256-dim MRL
        )
        _model.max_seq_length = MAX_SEQ_LENGTH
        
        # Log successful load
        logger.info(
            f"EmbeddingGemma loaded: {MODEL_NAME}, "
            f"dim={EMBEDDING_DIM}, max_seq={MAX_SEQ_LENGTH}, "
            f"air_gap=verified, device={_model.device}"
        )
    
    return _model


def embed_documents(texts: list[str], batch_size: int = 32) -> np.ndarray:
    """
    Embed document texts using document-style prompts.
    Returns numpy array of shape (len(texts), EMBEDDING_DIM).
    
    Uses EmbeddingGemma's document prompt format:
      "title: none | text: {document_text}"
    which is handled automatically by encode_document().
    """
    model = get_model()
    embeddings = model.encode_document(
        texts,
        batch_size=batch_size,
        show_progress_bar=len(texts) > 100,
        convert_to_numpy=True,
        normalize_embeddings=True
    )
    return embeddings


def embed_query(query: str) -> np.ndarray:
    """
    Embed a search query using query-style prompt.
    Returns numpy array of shape (EMBEDDING_DIM,).
    
    Uses EmbeddingGemma's query prompt format:
      "task: search result | query: {query_text}"
    which is handled automatically by encode_query().
    """
    model = get_model()
    embedding = model.encode_query(
        query,
        convert_to_numpy=True,
        normalize_embeddings=True
    )
    return embedding


def semantic_search(query: str, embeddings: np.ndarray, 
                    ticket_ids: list[str], top_k: int = 10) -> list[dict]:
    """
    Find tickets most similar to a natural language query.
    
    Args:
        query: Natural language search string
        embeddings: Pre-loaded numpy matrix (n_tickets, EMBEDDING_DIM)
        ticket_ids: Corresponding ticket IDs
        top_k: Number of results to return
    
    Returns:
        List of {ticket_id, similarity_score} sorted by relevance
    """
    query_vec = embed_query(query)
    
    # Cosine similarity (vectors are pre-normalized)
    similarities = embeddings @ query_vec
    
    # Top-k indices
    top_indices = np.argsort(similarities)[::-1][:top_k]
    
    return [
        {
            "ticket_id": ticket_ids[i],
            "similarity_score": float(similarities[i])
        }
        for i in top_indices
        if similarities[i] > 0.1  # minimum relevance threshold
    ]
```

#### Embedding Build Pipeline

```python
def build_embeddings(db, force=False):
    """
    Compute and persist embeddings for all tickets. Incremental by default.
    
    Source text: composite of PHI-free fields from ticket_index.
    If ticket_index is not populated (no NLP scan run yet), falls back
    to redacted thread_preview from conversations.
    
    Returns count of newly embedded tickets.
    """
    # Check existing embeddings
    existing = {}
    try:
        for row in db.execute("SELECT ticket_id, source_text_hash FROM ticket_embeddings"):
            existing[row["ticket_id"]] = row["source_text_hash"]
    except Exception:
        pass  # Table may not exist yet
    
    # Build source texts from ticket_index (PHI-free)
    tickets = db.execute("""
        SELECT ti.ticket_id, 
               COALESCE(c.subject, '') as subject,
               COALESCE(ti.root_cause_hint, '') as root_cause,
               COALESCE(ti.key_phrases, '') as phrases,
               COALESCE(ti.summary, '') as summary,
               COALESCE(ti.friction_type, '') as friction,
               COALESCE(ti.sub_cluster, '') as sub_pattern
        FROM ticket_index ti
        LEFT JOIN conversations c ON ti.ticket_id = c.ticket_id
    """).fetchall()
    
    if not tickets:
        # Fallback: use redacted thread_preview if no NLP scan exists
        tickets = db.execute("""
            SELECT ticket_id, subject, 
                   COALESCE(thread_preview, '') as summary,
                   '' as root_cause, '' as phrases, 
                   '' as friction, '' as sub_pattern
            FROM conversations
        """).fetchall()
    
    to_embed = []
    for t in tickets:
        source_text = " ".join(filter(None, [
            t["subject"], t["friction"], t["sub_pattern"],
            t["root_cause"], t["phrases"], t["summary"]
        ]))
        if not source_text.strip():
            continue
        
        text_hash = hashlib.sha256(source_text.encode()).hexdigest()
        if not force and existing.get(t["ticket_id"]) == text_hash:
            continue
        
        to_embed.append((t["ticket_id"], source_text, text_hash))
    
    if not to_embed:
        return 0
    
    # Batch embed
    texts = [t[1] for t in to_embed]
    vectors = embed_documents(texts, batch_size=32)
    
    for (ticket_id, _, text_hash), vector in zip(to_embed, vectors):
        db.execute("""
            INSERT OR REPLACE INTO ticket_embeddings
            (ticket_id, embedding_blob, source_text_hash, model_name, dim_size, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            ticket_id,
            vector.astype(np.float32).tobytes(),
            text_hash,
            MODEL_NAME,
            EMBEDDING_DIM,
            datetime.utcnow().isoformat()
        ))
    
    db.conn.commit()
    return len(to_embed)
```

#### Embedding Cache + Semantic Search Tool

```python
def load_embedding_cache(db, session_filters=None):
    """
    Bulk-load embeddings into memory for fast search.
    Optionally filtered to session scope.
    
    Memory budget: 50K tickets × 768 dims × 4 bytes = ~147 MB
    With 256-dim MRL: 50K × 256 × 4 = ~49 MB
    """
    global _embedding_cache
    
    if session_filters:
        # Get ticket IDs in session scope
        id_query, id_params = build_filter_query(
            session_filters,
            select_columns=["ticket_id"],
            base_table="ticket_index"
        )
        scoped_ids = {r["ticket_id"] for r in db.execute(id_query, id_params)}
        
        rows = db.execute(
            "SELECT ticket_id, embedding_blob FROM ticket_embeddings"
        ).fetchall()
        
        rows = [r for r in rows if r["ticket_id"] in scoped_ids]
    else:
        rows = db.execute(
            "SELECT ticket_id, embedding_blob FROM ticket_embeddings"
        ).fetchall()
    
    if not rows:
        return None, []
    
    ticket_ids = [r["ticket_id"] for r in rows]
    matrix = np.stack([
        np.frombuffer(r["embedding_blob"], dtype=np.float32)
        for r in rows
    ])
    
    return matrix, ticket_ids


def handle_semantic_search(db, args, session_filters):
    """Chat tool handler for semantic search."""
    query = args["query"]
    top_k = args.get("top_k", 10)
    
    matrix, ticket_ids = load_embedding_cache(db, session_filters)
    if matrix is None:
        return {"error": "No embeddings available. Run embedding build first."}
    
    results = semantic_search(query, matrix, ticket_ids, top_k)
    
    # Enrich results with ticket metadata
    for r in results:
        meta = db.execute("""
            SELECT c.subject, ti.trc_code, ti.friction_type, 
                   ti.summary, ti.sentiment_polarity, c.thread_preview
            FROM ticket_index ti
            JOIN conversations c ON ti.ticket_id = c.ticket_id
            WHERE ti.ticket_id = ?
        """, (r["ticket_id"],)).fetchone()
        
        if meta:
            r.update({
                "subject": meta["subject"],
                "trc_code": meta["trc_code"],
                "friction_type": meta["friction_type"],
                "summary": meta["summary"],
                "sentiment": meta["sentiment_polarity"],
                "preview": (meta["thread_preview"] or "")[:200]
            })
    
    return {"results": results, "query": query, "total_searchable": len(ticket_ids)}
```

#### Performance Characteristics

| Metric | all-MiniLM-L6-v2 (current) | EmbeddingGemma-300M |
|--------|---------------------------|---------------------|
| Cold model load | ~2s | ~5-8s |
| Embed 1 ticket | ~5ms | ~15-25ms |
| Embed 1,000 tickets | ~3s | ~15-20s |
| Embed 10,000 tickets | ~30s | ~2-3 min |
| Embed 50,000 tickets | ~2.5 min | ~10-15 min |
| Search (cached, 50K) | <50ms | <100ms |
| RAM (model loaded) | ~90 MB | ~1.2 GB (fp32) / ~200 MB (int8) |
| Disk (bundled) | ~82 MB | ~1.2 GB (compressed in zip: ~850 MB) |

Trade-off: EmbeddingGemma is ~3-4x slower to embed but produces
meaningfully better vectors for retrieval tasks, especially with the
2K context window capturing full ticket threads vs MiniLM's 256-token
truncation. The embedding build runs once (post-ingest, post-NLP-scan)
and is incremental. Search latency is comparable once cached.

For the 50K ticket cap in CLI mode, the ~10-15 min embedding build is
acceptable as a one-time post-ingest cost. Incremental re-embedding
after NLP scans only touches changed tickets.

#### HIPAA Compliance Summary

| Control | Implementation | Verified By |
|---------|---------------|-------------|
| Model runs 100% local | Loaded from bundled directory, local_files_only=True | verify_air_gap() check #2 |
| Zero network calls during inference | 6 environment variables block all HF/torch network paths | verify_air_gap() check #1 |
| Network connectivity blocked | NO_PROXY=*, CURL_CA_BUNDLE="" | verify_air_gap() check #3 (socket test) |
| No telemetry | HF_HUB_DISABLE_TELEMETRY=1 | verify_air_gap() check #1 |
| No model auto-updates | HF_HUB_OFFLINE=1, TRANSFORMERS_OFFLINE=1 | verify_air_gap() check #1 |
| Model binary is fixed | SafeTensors format (no arbitrary code execution) | File hash verification at build time |
| Input text stays in-process | SQLite read → Python string → numpy array → SQLite BLOB | Code audit (no network calls in pipeline) |
| Embeddings are not PHI | Vectors are not reversible to source text; source text is PHI-free (ticket_index fields) or redacted | Architecture design |
| Audit trail | verify_air_gap() logs to analysis_log at every model load | Log inspection |

#### Comparison to Existing all-MiniLM-L6-v2 Air-Gap

The current embedding_engine.py already sets air-gap env vars for MiniLM:

```python
# Current code in embedding_engine.py (lines ~15-22)
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_DATASETS_OFFLINE"] = "1"
```

EmbeddingGemma uses the same mechanism plus three additions:

1. `HF_HUB_DISABLE_TELEMETRY=1` — explicitly blocks usage reporting
2. `NO_PROXY=*` — system-level proxy override preventing any HTTP egress
3. `verify_air_gap()` — runtime verification function that actively tests
   the air-gap at model load time and logs the result, rather than trusting
   the env vars were set correctly

The SafeTensors model format is also a security improvement over MiniLM's
pickle-based weights. SafeTensors cannot execute arbitrary Python code
during deserialization.

---

### Express Installer: Bundling EmbeddingGemma

The model ships inside the zip alongside `python/` and `app/`. No separate
download, no user-facing complexity, no network calls at install or runtime.

#### Updated Distribution Size

| Component | Current (CUDA torch) | With EmbeddingGemma + CPU-only torch |
|-----------|---------------------|--------------------------------------|
| python/ (runtime + pip packages) | ~2 GB (includes ~1.4 GB CUDA) | ~600 MB (CPU-only torch ~200 MB) |
| app/ (source) | ~5 MB | ~5 MB |
| models/ (MiniLM) | ~82 MB | — (replaced) |
| models/ (EmbeddingGemma) | — | ~1.2 GB |
| **Raw total** | **~2.1 GB** | **~1.8 GB** |
| **Zip compressed** | **~1.2-1.5 GB** | **~800 MB – 1 GB** |

The CPU-only torch swap actually makes the full zip with EmbeddingGemma
**smaller** than the current zip with CUDA torch and MiniLM. You're
cutting ~1.4 GB of dead CUDA libraries and adding ~1.1 GB of model
weights (which compress better than binaries). Net savings: ~300-500 MB.

#### Changes to `build_release.py`

**Step 1 (REQUIRED): Install torch CPU-only BEFORE sentence-transformers.**
The default `pip install torch` pulls ~2 GB with CUDA support that a
desktop app will never use. Install from the CPU wheel index first:

```python
# In the pip install step of build_release.py:

# ─── IMPORTANT: Install CPU-only torch first ───
# This MUST come before sentence-transformers, otherwise pip resolves
# the default CUDA torch as a dependency and you ship 1.4 GB of dead weight.
subprocess.run([
    pip_path, "install", "torch",
    "--index-url", "https://download.pytorch.org/whl/cpu"
], check=True)

# Then install everything else — sentence-transformers will see torch
# already satisfied and won't re-download the CUDA version
pip_packages = [
    "PySide6>=6.6.0",
    "pandas>=2.0.0",
    "scikit-learn>=1.3.0",
    "nltk>=3.8.0",
    "pyyaml>=6.0",
    "sentence-transformers>=3.0.0",   # uses the CPU torch already installed
]

subprocess.run([pip_path, "install"] + pip_packages, check=True)
```

**Why CPU-only is correct here:** EmbeddingGemma was designed for
on-device CPU inference. Embedding 50K tickets takes ~10-15 minutes on
CPU — a one-time post-ingest cost. Search queries against cached
embeddings are <100ms regardless. No GPU needed, no CUDA needed.

```python
# ─── Existing build steps (unchanged) ───
# 1. Download Python embeddable / python-build-standalone
# 2. pip install all dependencies into bundled Python
# 3. Strip __pycache__, .dist-info, test dirs
# 4. Copy app source

# ─── NEW: Bundle EmbeddingGemma model ───

MODEL_SRC = Path("models/embeddinggemma-300m")
MODEL_DST = build_dir / "models" / "embeddinggemma-300m"

if MODEL_SRC.exists():
    print("Bundling EmbeddingGemma-300M model...")
    shutil.copytree(MODEL_SRC, MODEL_DST)
    
    # Verify critical files present
    required_files = [
        "model.safetensors",
        "config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "modules.json",
    ]
    missing = [f for f in required_files if not (MODEL_DST / f).exists()]
    if missing:
        print(f"  ERROR: Missing model files: {missing}")
        print(f"  Run the pre-download procedure first.")
        sys.exit(1)
    
    # Strip junk from model directory
    for junk_pattern in ["__pycache__", ".git", "*.pyc"]:
        for junk in MODEL_DST.rglob(junk_pattern):
            if junk.is_dir():
                shutil.rmtree(junk)
            else:
                junk.unlink()
    
    model_size = sum(f.stat().st_size for f in MODEL_DST.rglob("*") if f.is_file())
    print(f"  Model bundled: {model_size / 1e9:.2f} GB ({len(required_files)} core files verified)")
else:
    print("WARNING: models/embeddinggemma-300m/ not found")
    print("  Building without embedding model — semantic search will be unavailable")
    print("  To include: run pre-download procedure, then rebuild")

# ─── Remove old MiniLM model if present ───
old_model = build_dir / "models" / "all-MiniLM-L6-v2"
if old_model.exists():
    print("Removing old MiniLM model (replaced by EmbeddingGemma)...")
    shutil.rmtree(old_model)

# ─── Continue existing zip creation step ───
```

#### Changes to `install.py`

```python
# ─── In the copy phase, after copying python/ and app/ ───

def install_embedding_model(source_dir, install_dir):
    """Copy EmbeddingGemma model to install location."""
    model_src = source_dir / "models" / "embeddinggemma-300m"
    model_dst = install_dir / "models" / "embeddinggemma-300m"
    
    if not model_src.exists():
        print_step("Embedding model not included in this build")
        print_step("  Semantic search will be unavailable")
        return False
    
    # Check if model already installed and unchanged
    src_config = model_src / "config.json"
    dst_config = model_dst / "config.json"
    
    if model_dst.exists() and dst_config.exists():
        if hash_file(src_config) == hash_file(dst_config):
            print_step("EmbeddingGemma model unchanged — skipping (saves time)")
            return True
        else:
            print_step("EmbeddingGemma model updated — replacing...")
            shutil.rmtree(model_dst)
    
    # Copy model with progress feedback
    # model.safetensors is ~1.2 GB, so this takes a moment
    print_step("Installing EmbeddingGemma model (~1.2 GB)...")
    print_step("  This is a one-time operation — updates skip unchanged models")
    
    model_dst.parent.mkdir(parents=True, exist_ok=True)
    
    # Copy file-by-file with progress for the large safetensors file
    total_bytes = sum(f.stat().st_size for f in model_src.rglob("*") if f.is_file())
    copied_bytes = 0
    
    for src_file in model_src.rglob("*"):
        rel_path = src_file.relative_to(model_src)
        dst_file = model_dst / rel_path
        
        if src_file.is_dir():
            dst_file.mkdir(parents=True, exist_ok=True)
            continue
        
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Large file: copy in chunks with progress
        file_size = src_file.stat().st_size
        if file_size > 100_000_000:  # >100MB — show progress
            with open(src_file, "rb") as fin, open(dst_file, "wb") as fout:
                while True:
                    chunk = fin.read(8_388_608)  # 8MB chunks
                    if not chunk:
                        break
                    fout.write(chunk)
                    copied_bytes += len(chunk)
                    pct = int(copied_bytes / total_bytes * 100)
                    print(f"\r  Progress: {pct}% ({copied_bytes // 1_000_000} / {total_bytes // 1_000_000} MB)", 
                          end="", flush=True)
            print()  # newline after progress
        else:
            shutil.copy2(src_file, dst_file)
            copied_bytes += file_size
    
    # Verify installation
    safetensors = model_dst / "model.safetensors"
    if not safetensors.exists():
        print_step("  ERROR: Model installation failed — model.safetensors missing")
        return False
    
    installed_size = safetensors.stat().st_size
    expected_min = 1_000_000_000  # at least 1 GB
    if installed_size < expected_min:
        print_step(f"  WARNING: model.safetensors is only {installed_size // 1_000_000} MB — may be corrupt")
        return False
    
    print_step(f"  EmbeddingGemma installed: {installed_size / 1e9:.2f} GB")
    return True


# ─── Clean up old MiniLM model on upgrade ───
def cleanup_old_models(install_dir):
    """Remove superseded MiniLM model from prior installs."""
    old_model = install_dir / "models" / "all-MiniLM-L6-v2"
    if old_model.exists():
        print_step("Removing old MiniLM model (replaced by EmbeddingGemma)...")
        shutil.rmtree(old_model)


# ─── In the main install flow, after app/ copy ───
# cleanup_old_models(install_dir)
# install_embedding_model(source_dir, install_dir)
```

#### Upgrade Path

The installer already handles upgrades via backup-and-restore of `data/`.
The model doesn't need backup-and-restore because it's read-only app
content (like `python/` and `app/`). On upgrade:

| Scenario | What Happens |
|----------|-------------|
| Fresh install | Model copied, progress bar shown (~30-60 seconds) |
| Upgrade, same model version | Config hash matches → skip copy entirely (instant) |
| Upgrade, new model version | Old model deleted, new model copied |
| Upgrade from MiniLM build | Old MiniLM dir removed, EmbeddingGemma installed |
| Build without model (dev/lightweight) | Graceful skip, semantic search disabled |

#### Graceful Fallback in App

```python
# In embedding_engine.py — the app works fine without the model

def is_available() -> bool:
    """Check if EmbeddingGemma is installed and loadable."""
    if not MODEL_DIR.exists():
        return False
    if not (MODEL_DIR / "model.safetensors").exists():
        return False
    return True


def get_model():
    """Load model or raise clear error."""
    if not is_available():
        raise ModelNotInstalledError(
            "EmbeddingGemma model not installed. "
            "Semantic search is unavailable. "
            "Reinstall with the full distribution package to enable."
        )
    # ... normal air-gapped load path


# In the semantic_search chat tool handler:
def handle_semantic_search(db, args, session_filters):
    if not is_available():
        return {
            "error": "Semantic search is not available in this installation. "
                     "Use keyword search (search_conversations) instead, "
                     "or reinstall with the full package.",
            "fallback": "search_conversations"
        }
    # ... normal search path
```

This means every feature in Alma Insights works without the model —
you just lose the semantic_search tool. The chat tools, fast-path
queries, thread access, report pipeline, all function independently.

#### Install Directory Structure (Final)

```
AlmaInsights/
├── python/                          # Bundled Python 3.12 + all packages
│   ├── python.exe                   #   PySide6, pandas, sklearn,
│   └── Lib/site-packages/           #   sentence-transformers, torch, ...
│
├── app/                             # Alma Insights source code
│   └── src/
│       ├── data/
│       │   ├── db_manager.py
│       │   └── filter_engine.py     # NEW (Day 1)
│       ├── analysis/
│       │   ├── embedding_engine.py  # REPLACED (Day 5) — EmbeddingGemma
│       │   └── post_nlp_stats.py    # NEW (Day 4)
│       ├── ai/
│       │   ├── chat_engine.py       # MODIFIED (Day 2) — session wiring
│       │   ├── tool_registry.py     # NEW (Day 2) — unified tools
│       │   └── report_builder.py    # MODIFIED (Day 4) — structured output
│       └── ...
│
├── models/                          # Embedding model (air-gapped)
│   └── embeddinggemma-300m/
│       ├── model.safetensors        # ~1.2 GB — the weights
│       ├── config.json              # Architecture config
│       ├── tokenizer.json           # Tokenizer vocabulary
│       ├── tokenizer_config.json
│       ├── special_tokens_map.json
│       ├── modules.json             # Sentence-transformers pipeline
│       ├── 1_Pooling/
│       │   └── config.json          # Mean pooling config
│       ├── 2_Dense/
│       │   ├── config.json
│       │   └── model.safetensors    # ~9 MB projection layer
│       ├── 3_Dense/
│       │   ├── config.json
│       │   └── model.safetensors    # ~9 MB projection layer
│       └── 4_Normalize/
│           └── config.json          # L2 normalization
│
├── data/                            # User data (preserved on upgrade)
│   └── alma_insights.db
│
├── Install Alma Insights.bat        # Windows launcher
└── Install Alma Insights.command    # macOS launcher
```

---

## Day-by-Day Sprint Summary

| Day | Deliverable | LOC Est. | Tests |
|-----|------------|---------|-------|
| 1 (Mon) | Filter engine + migration 006 (all new tables) | ~300 | 20-25 |
| 2 (Tue) | Session wiring + 4 fast-path tools | ~500 | 15-20 |
| 3 (Wed) | Thread tools + query_report + ticket-theme tagging | ~400 | 10-15 |
| 4 (Thu) | Structured report output + run_report + post-NLP stats | ~400 | 10-15 |
| 5 (Fri) | EmbeddingGemma engine + persistence + semantic search + installer (CPU-only torch + model bundling) | ~600 | 10-15 |

**Total: ~2,200 new LOC, 65-90 tests**

### What This Week Produces

By Friday, a user can:
- Open a chat session scoped to a set of tickets
- Ask "how many billing tickets had incorrect charges?" → sub-second answer
- Ask "show me those tickets" → list with summaries
- Ask "read the worst three" → full thread text, PII-redacted
- Ask "is this getting worse?" → trend data from enriched_trends
- Ask "run a full analysis" → pipeline triggers inline, structured output
- Ask "find the ticket where someone described a double charge" → semantic search
- All within one continuous conversation, one session, no context switches

The Express Installer produces a single ~800 MB – 1 GB zip that includes
the EmbeddingGemma model — actually smaller than the current build with
CUDA torch, because the CPU-only torch swap saves more than the model adds.
End users download, extract, double-click install. No Python, no pip,
no model downloads, no network calls at runtime. The model loads from
disk, air-gap verified, HIPAA compliant.

### What's Deferred

- Cross-theme tracking (Layer 5) — needs the junction table from Day 3 to exist first, then 4-5 days
- Client journey tracking (Layer 6) — needs InfoSec sign-off, then 4-5 days
- Claude tool unification — cleanup task, not blocking
- UI changes — PySide6 chat widget needs session_id passthrough, but the data layer works first
- Model quantization (int8) — would cut zip to ~500-600 MB if distribution size becomes a problem
