# Alma Insights — Data Schema Contract

## Canonical Sources

### Ticket Classification
- **Source of truth**: `ticket_index` (one row per ticket, latest classification)
- **Raw data**: `nlp_ticket_classifications` (append-only, one row per scan per ticket)
- **Sync mechanism**: `ticket_index_writer.py` upserts after each classification
- **Dedup**: `content_hash` column prevents reclassification of unchanged tickets

### Entities (Payer, Product Area, Feature)
- **Source of truth**: `ticket_entities_normalized` (indexed, case-normalized)
- **Raw data**: `nlp_ticket_classifications.entities_json` (JSON blob, not queryable)
- **Sync mechanism**: `entity_normalizer.py` extracts after each scan (step 8.8)
- **Case normalization**: Canonical mappings in `entity_normalizer.CASE_NORMALIZATION`

### Embeddings
- **Source of truth**: `ticket_embeddings` (binary blobs, one per ticket)
- **Source text**: Composed from `ticket_index` fields (NOT raw conversation)
- **Freshness rule**: Must be rebuilt after any `ticket_index` change
- **Detection**: `integrity_checker.py` flags stale embeddings (step 8.7)

### Anomaly Detection
- **Ticket-level**: `nlp_ticket_classifications.anomaly_flag` (NLP-derived)
- **TRC-level statistical**: `incident_flags` (Poisson-based daily spikes)
- **TRC-level theta**: `anomaly_flags` (Theta z-score based)
- **NOT synced**: These three systems are independent. Future work: link via trc_code + date.

### Conversations (PHI)
- **Ephemeral**: `tickets`, `comments`, `conversations` (cleared by Clear & Close)
- **Persistent summary**: `ticket_index.issue_snippet` (survives clear)
- **FTS index**: `conversations_fts` (must match `conversations` count)

### Reports
- **Source of truth**: `analysis_reports` (self-contained markdown/JSON)
- **Orphan resilience**: Reports render even after Clear & Close; ticket links
  fall back to `ticket_index` metadata when conversation data is unavailable.

### Guru Knowledge Base
- **Cards**: `guru_cards` (synced from Guru API via `guru_client.py`)
- **Card graph**: `guru_card_graph` + `guru_card_domains` (BFS constellation, domain tagging)
- **Friction analysis**: `guru_friction_scores` (per-card friction metrics)

---

## Connection Discipline

All database connections go through `src/data/connection_factory.get_connection()`.
Direct `sqlite3.connect()` calls are banned outside of `connection_factory.py`.

Guarantees per connection:
- WAL journal mode
- busy_timeout = 30,000ms
- foreign_keys = ON
- row_factory = sqlite3.Row

Multi-step mutations use `atomic()` context manager for rollback safety.

---

## Migration Numbering

| # | File | Purpose |
|---|------|---------|
| 001 | `001_initial_baseline.sql` | Baseline schema |
| 002 | `002_source_warehouse.sql` | Source warehouse tables |
| 003 | `003_guru_tables.sql` | Guru integration tables |
| 004 | `004_guru_card_graph.sql` | Card graph + domains |
| 005 | `005_persistence_layer.sql` | Ticket index, insight ledger |
| 006 | `006_hybrid_chat.sql` | Embeddings, chat tools, FTS |
| 007 | _(reserved)_ | Incremental import sprint |
| 008 | `008_entity_normalization.sql` | Entity normalization table |
