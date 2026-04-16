# migrations/ — SQL Schema Migrations

> 15 numbered SQL files applied incrementally by `src/updater/schema_migrator.py`. Tracked in the `schema_migrations` table so each migration runs exactly once.

## Migration Index

| # | File | Purpose | Key Tables/Columns |
|---|------|---------|-------------------|
| 001 | `001_initial_baseline.sql` | Baseline marker | (empty — tables created by `db_manager.initialize()`) |
| 002 | `002_source_warehouse.sql` | Source warehouse + watchlist | `source_events`, `watchlist_rules`, `watchlist_alerts`, `source_trc_hourly/daily` |
| 003 | `003_guru_tables.sql` | Guru KB integration | `guru_articles`, `guru_friction_coverage`, `guru_effectiveness`, `guru_content_drafts` |
| 004 | `004_guru_card_graph.sql` | Card relationship graph | `guru_card_graph`, `guru_card_domains` |
| 005 | `005_persistence_layer.sql` | Persistent PHI-free tables | `ticket_index`, `scan_category_snapshots`, `analysis_runs`, `trend_snapshots`, `insight_ledger`, `chat_sessions`, `report_definitions` |
| 006 | `006_hybrid_chat.sql` | Chat + embeddings | `ticket_theme_tags`, `enriched_trends`, `ticket_embeddings` + columns on `chat_sessions` |
| 007 | _(reserved)_ | Incremental import sprint | (skipped) |
| 008 | `008_entity_normalization.sql` | Entity lookup table | `ticket_entities_normalized` |
| 009 | `009_chat_data_layer.sql` | Per-message chat storage | `chat_projects`, `chat_messages`, `chat_tool_executions`, `chat_messages_fts` + 3 views |
| 010 | `010_import_tracking.sql` | Import audit log | `import_runs` |
| 011 | `011_source_registry.sql` | Source registry | `source_registry` + default Zendesk insert |
| 012 | `012_default_zendesk_source.sql` | Default per-source tables | `zendesk_default_tickets/conversations/comments/fts` + data copy |
| 013 | `013_provider_client_ids.sql` | Multi-tenancy columns | `provider_id`, `client_id` on `tickets`; `source_id` on `ticket_index` |
| 014 | `014_sub_pattern_source_id.sql` | Per-source friction | `source_id` on `sub_patterns` and `nlp_scan_runs` |
| 015 | `015_batch_tickets.sql` | Batch-ticket mapping | `nlp_batch_tickets` |

## Adding a New Migration

1. Create `NNN_description.sql` (next number in sequence)
2. Use `CREATE TABLE IF NOT EXISTS` for idempotency
3. Wrap `ALTER TABLE ADD COLUMN` — the migrator handles duplicate-column errors
4. Test with `python -m pytest tests/test_migration_idempotent.py -v`
5. Update `docs/DATABASE.md` with any new tables

## How Migrations Run

- Applied automatically by `db_manager.initialize()` on every launch
- `SchemaMigrator` scans this directory for `NNN_*.sql` files
- Compares against `schema_migrations` table to find pending ones
- Each migration runs in its own transaction

## See Also

- `docs/DATABASE.md` — Full schema reference
- `src/updater/schema_migrator.py` — Migration runner
- `src/data/db_manager.py` — Baseline schema (42+ tables)
