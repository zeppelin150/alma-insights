# Alma Insights — Database Schema Reference

## Overview

- **Engine**: SQLite 3 with WAL journal mode
- **Path**: `data/local_warehouse.db`
- **Schema source**: `src/data/db_manager.py::initialize()` (baseline) + 15 migration files in `migrations/`
- **Dynamic tables**: `src/data/schema_builder.py` creates per-source table sets
- **Canonical rules**: `src/data/SCHEMA_CONTRACT.md`

## Connection Configuration

All connections via `src/data/connection_factory.get_connection()`. Direct `sqlite3.connect()` is banned.

| Setting | Value | Purpose |
|---------|-------|---------|
| journal_mode | WAL | Concurrent readers + one writer |
| synchronous | FULL | WAL records fsynced before commit returns |
| busy_timeout | 30,000 ms | Retry on SQLITE_BUSY instead of immediate fail |
| foreign_keys | ON | Referential integrity enforced |
| row_factory | sqlite3.Row | Dict-like access on query results |

Multi-step mutations: use `atomic(conn)` context manager (BEGIN IMMEDIATE → COMMIT/ROLLBACK). Cannot nest.

---

## Migration History

Applied by `src/updater/schema_migrator.py`. Tracked in `schema_migrations` table. Migration 007 is reserved (skipped).

| # | File | Purpose |
|---|------|---------|
| 001 | `001_initial_baseline.sql` | Empty marker — baseline tables created by `db_manager.initialize()` |
| 002 | `002_source_warehouse.sql` | Source warehouse: `source_events`, `watchlist_rules/alerts/examples`, `source_trc_hourly/daily`. Adds `source` column to `conversations` |
| 003 | `003_guru_tables.sql` | Guru KB: `guru_articles`, `guru_friction_coverage`, `guru_effectiveness`, `guru_content_drafts` |
| 004 | `004_guru_card_graph.sql` | Card graph: `guru_card_graph`, `guru_card_domains` |
| 005 | `005_persistence_layer.sql` | Persistent tables: `ticket_index`, `scan_category_snapshots`, `analysis_runs`, `trend_snapshots`, `insight_ledger`, `chat_sessions`, `report_definitions` |
| 006 | `006_hybrid_chat.sql` | Chat uplevel: `ticket_theme_tags`, `enriched_trends`, `ticket_embeddings`. Adds columns to `chat_sessions` |
| 008 | `008_entity_normalization.sql` | Entity lookup: `ticket_entities_normalized` |
| 009 | `009_chat_data_layer.sql` | Chat redesign: `chat_projects`, `chat_messages`, `chat_tool_executions`, `chat_messages_fts`. Views: `v_session_summary`, `v_session_tools`, `v_chat_cost_daily` |
| 010 | `010_import_tracking.sql` | Import audit: `import_runs` |
| 011 | `011_source_registry.sql` | Source registry: `source_registry`. Inserts default Zendesk source |
| 012 | `012_default_zendesk_source.sql` | Creates per-source tables for default Zendesk, copies existing data |
| 013 | `013_provider_client_ids.sql` | Adds `provider_id`, `client_id` to `tickets`. Adds `source_id` to `ticket_index` |
| 014 | `014_sub_pattern_source_id.sql` | Adds `source_id` to `sub_patterns` and `nlp_scan_runs` |
| 015 | `015_batch_tickets.sql` | Batch tracking: `nlp_batch_tickets` |

---

## Table Reference

### Core Ticket Data (Ephemeral)

These tables are populated during import. Cleared by Clear & Close.

#### `tickets`
Primary ticket storage. One row per ticket.

| Column | Type | Notes |
|--------|------|-------|
| ticket_id | TEXT PK | |
| subject | TEXT | |
| trc_code | TEXT | Top Reason Code |
| trc_label | TEXT | Human-readable TRC name |
| status | TEXT | open, solved, etc. |
| priority | TEXT | |
| channel | TEXT | email, chat, phone |
| csat_score | REAL | 1.0–5.0 |
| created_at | TEXT | ISO date |
| updated_at | TEXT | |
| solved_at | TEXT | |
| requester_name | TEXT | PHI |
| requester_email | TEXT | PHI |
| assignee_name | TEXT | |
| group_name | TEXT | |
| tags | TEXT | JSON array |
| custom_fields | TEXT | JSON object |
| assignment_to_resolution_hours | REAL | |
| total_resolution_hours | REAL | |
| first_reply_hours | REAL | |
| requester_hash | TEXT | SHA-256 for repeat detection |
| provider_id | TEXT | Added by migration 013 |
| client_id | TEXT | Added by migration 013 |

Indexes: `idx_tickets_trc`, `idx_tickets_created`, `idx_tickets_status`

#### `comments`
Individual ticket comments. FK to `tickets`.

| Column | Type | Notes |
|--------|------|-------|
| comment_id | TEXT PK | |
| ticket_id | TEXT FK | → tickets |
| author_name | TEXT | |
| author_role | TEXT | customer, agent |
| body | TEXT | PHI |
| is_public | INTEGER | 1=public |
| created_at | TEXT | |

Index: `idx_comments_ticket`

#### `conversations`
Rebuilt conversation threads (one per ticket). FK to `tickets`.

| Column | Type | Notes |
|--------|------|-------|
| ticket_id | TEXT PK | |
| subject | TEXT | |
| trc_code | TEXT | |
| trc_label | TEXT | |
| status | TEXT | |
| csat_score | REAL | |
| created_at | TEXT | |
| solved_at | TEXT | |
| message_count | INTEGER | |
| client_messages | INTEGER | |
| agent_messages | INTEGER | |
| full_thread | TEXT | PHI — full conversation |
| thread_preview | TEXT | First 200 chars |
| dataset_id | INTEGER | A/B comparison |
| source | TEXT | csv, lightdash (migration 002) |

Indexes: `idx_conversations_trc`, `idx_conversations_created`

#### `conversations_fts`
FTS5 virtual table for full-text search. Content table: `conversations`.

Columns: `ticket_id`, `subject`, `trc_label`, `full_thread`

---

### Per-Source Dynamic Tables

Created by `schema_builder.create_source_tables(conn, table_prefix)` for each registered source. Schema mirrors core tables with additional `source_id`, `provider_id`, `client_id`, `imported_at`, `metadata` columns.

- `{prefix}_tickets` — mirrors `tickets` + source columns
- `{prefix}_conversations` — mirrors `conversations` + source_id
- `{prefix}_comments` — mirrors `comments` + source_id
- `{prefix}_fts` — FTS5 virtual table

Default source: `zendesk_default` (created by migration 012, data copied from shared tables).

---

### NLP Classification System

#### `nlp_scan_runs`
One row per scan execution.

| Column | Type | Notes |
|--------|------|-------|
| scan_id | TEXT PK | UUID |
| created_at | TEXT | |
| status | TEXT | running, completed, cancelled, failed |
| date_range_start / end | TEXT | |
| trc_filter | TEXT | Optional TRC filter |
| mode | TEXT | full, incremental |
| batch_strategy | TEXT | trc (default) |
| total_batches / completed_batches | INTEGER | |
| total_tickets / total_comments | INTEGER | |
| total_input_tokens / total_output_tokens | INTEGER | |
| estimated_cost_usd / actual_cost_usd | REAL | |
| budget_cap_usd | REAL | Default 50.0 |
| error_log | TEXT | |
| config_snapshot | TEXT | JSON |
| completed_at | TEXT | |
| source_id | TEXT | Migration 014 |

#### `nlp_batches`
One row per batch within a scan. FK to `nlp_scan_runs`.

| Column | Type | Notes |
|--------|------|-------|
| batch_id | TEXT PK | UUID |
| scan_id | TEXT FK | → nlp_scan_runs |
| batch_number | INTEGER | |
| trc | TEXT | |
| trc_chunk / trc_chunk_total | INTEGER | For oversized TRCs |
| status | TEXT | |
| ticket_count / comment_count | INTEGER | |
| input_tokens / output_tokens | INTEGER | |
| cost_usd | REAL | |
| latency_ms | INTEGER | |
| prompt_version | TEXT | |
| prior_chunks_context | TEXT | |
| raw_response | TEXT | |
| error_message | TEXT | |
| retry_count | INTEGER | |
| worker_id | INTEGER | |
| created_at / completed_at | TEXT | |

Indexes: `idx_nlp_batch_scan`, `idx_nlp_batch_trc`

#### `nlp_ticket_classifications`
One row per ticket per scan (append-only).

| Column | Type | Notes |
|--------|------|-------|
| classification_id | TEXT PK | |
| batch_id | TEXT FK | → nlp_batches |
| scan_id | TEXT FK | → nlp_scan_runs |
| ticket_id | TEXT | |
| trc | TEXT | |
| sub_cluster | TEXT | Sub-pattern name |
| sub_cluster_confidence | REAL | 0.0–1.0 |
| is_novel | INTEGER | 1 = new pattern |
| sentiment_intensity | INTEGER | 1–5 |
| sentiment_polarity | TEXT | positive, negative, neutral, mixed |
| friction_type | TEXT | |
| anomaly_flag | TEXT | |
| anomaly_reason | TEXT | |
| entities_json | TEXT | JSON blob |
| key_phrases | TEXT | |
| root_cause_hint | TEXT | |
| summary | TEXT | |
| raw_classification | TEXT | |
| created_at | TEXT | |

UNIQUE constraint: `(scan_id, ticket_id)`
Indexes: `idx_nlp_tc_ticket`, `idx_nlp_tc_scan`, `idx_nlp_tc_trc`, `idx_nlp_tc_sub`, `idx_nlp_tc_anomaly`, `idx_nlp_tc_friction`, `idx_nlp_tc_novel`

#### `nlp_batch_tickets`
Batch-to-ticket mapping for coverage queries. Migration 015.

| Column | Type | Notes |
|--------|------|-------|
| batch_id | TEXT | Composite PK with ticket_id |
| ticket_id | TEXT | |
| scan_id | TEXT FK | → nlp_scan_runs |

#### `sub_patterns`
Discovered classification sub-patterns. Persist across scans.

| Column | Type | Notes |
|--------|------|-------|
| pattern_id | TEXT PK | |
| trc | TEXT | |
| label | TEXT | UNIQUE with trc |
| description | TEXT | |
| friction_type | TEXT | |
| tier | TEXT | probationary, confirmed |
| discovered_scan / discovered_at | TEXT | |
| last_seen_scan / last_seen_at | TEXT | |
| lifetime_tickets / lifetime_scans | INTEGER | |
| merged_into | TEXT | Pattern merge target |
| source_id | TEXT | Migration 014 |

#### `sub_pattern_ngrams`
N-gram fingerprints for pattern matching.

| Column | Type | Notes |
|--------|------|-------|
| ngram_id | INTEGER PK | |
| pattern_id | TEXT FK | → sub_patterns |
| trc | TEXT | |
| ngram | TEXT | UNIQUE with pattern_id |
| n | INTEGER | 1, 2, or 3 |
| source | TEXT | gemini |
| frequency / ticket_count | INTEGER | |
| first_seen / last_seen | TEXT | |
| specificity | REAL | TF-IDF-like score |

#### `sub_pattern_snapshots`
Per-scan snapshot of pattern metrics.

| Column | Type | Notes |
|--------|------|-------|
| snapshot_id | INTEGER PK | |
| pattern_id | TEXT FK | UNIQUE with scan_id |
| scan_id | TEXT FK | |
| scan_date_start / scan_date_end | TEXT | |
| ticket_count | INTEGER | |
| pct_of_trc | REAL | |
| avg_sentiment | REAL | |
| sentiment_dist / friction_dist | TEXT | JSON |
| top_entities | TEXT | JSON |
| novel_tickets / new_ngrams_added | INTEGER | |

#### `provisional_classifications`
Lightweight pre-scan classifications via ngram matching.

#### `nlp_findings`
Cross-TRC findings from meta-analysis. FK to `nlp_scan_runs`.

| Column | Type | Notes |
|--------|------|-------|
| finding_id | TEXT PK | |
| scan_id | TEXT FK | |
| finding_type | TEXT | |
| scope / title / description | TEXT | |
| ticket_count | INTEGER | |
| pct_of_scanned | REAL | |
| avg_sentiment_intensity | REAL | |
| dominant_friction_type | TEXT | |
| top_trcs / top_sub_patterns / top_entities | TEXT | JSON |
| date_concentration / temporal_trend | TEXT | |
| exemplar_ticket_ids | TEXT | JSON |
| statistical_validation / baseline_comparison | TEXT | |
| impact_score | REAL | |

---

### Persistence Layer (Survive Clear & Close)

All tables added by migration 005. PHI-free data.

#### `ticket_index`
Core persistent ticket-level record. **Source of truth** for ticket classification.

| Column | Type | Notes |
|--------|------|-------|
| ticket_id | TEXT PK | |
| trc_code | TEXT | |
| subject_sanitized | TEXT | PHI-free |
| issue_snippet | TEXT | PHI-free summary |
| friction_type / sub_pattern | TEXT | |
| sentiment_polarity | TEXT | |
| sentiment_intensity | REAL | |
| anomaly_flag | TEXT | |
| csat_score | REAL | |
| message_count | INTEGER | |
| resolution_hours | REAL | |
| entities_json | TEXT | PHI-free entities only |
| key_phrases | TEXT | |
| is_novel | INTEGER | |
| dataset_id | INTEGER | |
| source_id | TEXT | Migration 013 |
| last_seen_scan_id | TEXT | |
| classification_confidence | REAL | |
| created_at | TEXT | |

Indexes: `idx_ti_trc`, `idx_ti_friction`, `idx_ti_sub_pattern`, `idx_ti_anomaly`, `idx_ti_created`, `idx_ti_sentiment`, `idx_ti_last_scan`

**Sync mechanism**: `ticket_index_writer.py` upserts after each classification. Dedup gate: `should_classify_ticket()` returns 'classify', 'skip', or 'update_scan_id'.

#### `scan_category_snapshots`
Post-scan TRC/friction distributions. Written by `post_scan_persist.py`.

#### `analysis_runs`
AI report generation records.

#### `trend_snapshots`
Statistical engine outputs — deltas vs prior scan.

#### `insight_ledger`
Significant findings log.

#### `chat_sessions`
Conversation persistence for chat interface. Extended by migrations 006 and 009.

#### `report_definitions`
Smart Report pipeline configs.

---

### Anomaly Detection

#### `daily_counts` / `hourly_counts`
Raw ticket count rollups by TRC and time period. UNIQUE constraints prevent duplicates.

#### `trc_baselines`
Poisson baselines per TRC. Columns: `lambda_daily`, `theta_1_daily`, `theta_2_daily`, `cusum_value`, `cusum_threshold`, `cusum_slack`.

#### `hourly_baselines`
Per-hour Poisson baselines per TRC.

#### `incident_flags`
Poisson-based incident flags. Columns include `flag_type`, `theta_level`, `direction`, `observed_value`, `expected_lambda`, `p_value`, `cusum_value`, `status` (open/acknowledged/resolved/false_positive).

#### `daily_baselines` / `rolling_stats`
Theta engine z-score baselines and rolling statistics.

#### `anomaly_flags`
Theta engine z-score anomaly flags. Similar structure to `incident_flags` but z-score-based.

---

### Analytics & Reporting

| Table | Purpose |
|-------|---------|
| `analysis_log` | Action audit log |
| `analysis_reports` | Saved reports (markdown + JSON) |
| `prompt_library` | Custom prompt templates |
| `datasets` | A/B comparison dataset metadata |
| `interventions` | Tracked business interventions |
| `smart_report_runs` | Smart Report execution log |
| `report_schedules` | Scheduled report configs |
| `chart_layouts` | Saved chart grid positions |

---

### Agent Infrastructure

| Table | Purpose |
|-------|---------|
| `agent_health` | Per-worker health metrics during scan |
| `scan_progress` | Scan progress for UI polling |
| `review_flags` | Tickets flagged for human review |
| `analyst_reports` | Post-scan analyst agent reports |
| `trc_batch_profiles` | Learned batch sizing profiles (EMA) |
| `gemini_usage` | API usage tracking (tokens, cost) |
| `cost_limits` | Budget caps |
| `scan_events` | Scan event log |
| `probe_history` | Canary probe results |

---

### Guru Knowledge Base

| Table | Source | Purpose |
|-------|--------|---------|
| `guru_articles` | Migration 003 | Article cache from Guru API |
| `guru_friction_coverage` | Migration 003 | Friction type → article coverage mapping |
| `guru_effectiveness` | Migration 003 | Pre/post volume comparison for article changes |
| `guru_content_drafts` | Migration 003 | LLM-generated drafts pending approval |
| `guru_card_graph` | Migration 004 | Card-to-card relationships (same_collection, cross_reference, url_link, shared_friction) |
| `guru_card_domains` | Migration 004 | Domain tags per card (collection, keyword, llm) |

---

### Source Warehouse (Migration 002)

| Table | Purpose |
|-------|---------|
| `source_events` | Classified records from any source |
| `source_trc_hourly` | Hourly spike detection baselines |
| `source_trc_daily` | Daily trend rollups |
| `watchlist_rules` | UI-configurable alert triggers |
| `watchlist_alerts` | Fired alert log |
| `watchlist_examples` | Few-shot examples for LLM triage |

---

### Source Registry (Migration 011)

#### `source_registry`
Multi-source data architecture. One row per data source.

| Column | Type | Notes |
|--------|------|-------|
| source_id | TEXT PK | |
| source_name | TEXT | |
| source_type | TEXT | zendesk, kodif, custom |
| table_prefix | TEXT UNIQUE | Used for dynamic table names |
| column_mapping | TEXT | JSON |
| created_at | TEXT | |
| is_default | INTEGER | |
| ticket_count | INTEGER | |
| last_import_at | TEXT | |

Default row: `zendesk_default` (Zendesk Support, prefix `zendesk_default`, is_default=1)

---

### Chat System (Migrations 006, 009)

| Table | Purpose |
|-------|---------|
| `ticket_theme_tags` | Links tickets to NLP findings |
| `enriched_trends` | Post-NLP aggregated stats per dimension |
| `ticket_embeddings` | Local embedding vectors (BLOB) |
| `chat_projects` | Session organization folders |
| `chat_messages` | Per-message storage with telemetry |
| `chat_tool_executions` | Per-tool-call detail |
| `chat_messages_fts` | FTS5 search over messages |

**Views** (migration 009):
- `v_session_summary` — session stats for sidebar
- `v_session_tools` — tool usage per session
- `v_chat_cost_daily` — daily cost tracking

---

### Import Tracking (Migration 010)

#### `import_runs`
Run-level import audit log.

| Column | Type | Notes |
|--------|------|-------|
| run_id | INTEGER PK | |
| started_at / completed_at | TEXT | |
| source | TEXT | csv, lightdash |
| mode | TEXT | incremental, full_refresh |
| file_name | TEXT | |
| tickets_seen / tickets_new / tickets_skipped | INTEGER | |
| status | TEXT | |
| error_message | TEXT | |

---

### Term Intelligence

| Table | Purpose |
|-------|---------|
| `user_terms` | User-managed term overrides (boost, suppress, alias, merge) |
| `tfidf_feedback` | TF-IDF weight feedback from users |
| `discovered_compounds` | PMI-discovered multi-word terms (candidate → approved/rejected) |

---

### Ticket Entities

| Table | Purpose |
|-------|---------|
| `ticket_entities` | Dictionary-extracted entities (payer, product_area) per ticket |
| `ticket_entities_normalized` | Case-normalized entity lookup (migration 008). **Source of truth** for entity queries |

---

## Ephemeral vs Persistent Summary

| Ephemeral (cleared on import/Clear & Close) | Persistent (survive across sessions) |
|---------------------------------------------|---------------------------------------|
| `tickets`, `comments`, `conversations` | `ticket_index`, `sub_patterns`, `nlp_findings` |
| `conversations_fts` | `scan_category_snapshots`, `trend_snapshots` |
| Per-source `{prefix}_*` tables | `analysis_reports`, `analysis_runs` |
| `daily_counts`, `hourly_counts` | `chat_sessions`, `chat_messages` |
| `agent_health`, `scan_progress` | `insight_ledger`, `report_definitions` |
| | `guru_articles`, `guru_card_graph` |
| | `import_runs`, `source_registry` |
| | `ticket_embeddings` |

---

## See Also

- `CLAUDE.md` — Quick reference
- `docs/ARCHITECTURE.md` — System architecture
- `docs/DATA_FLOWS.md` — Data flow documentation
- `src/data/SCHEMA_CONTRACT.md` — Canonical source rules
- `src/data/connection_factory.py` — Connection guarantees
- `src/data/db_manager.py` — Schema DDL source
- `src/data/schema_builder.py` — Dynamic per-source table creation
