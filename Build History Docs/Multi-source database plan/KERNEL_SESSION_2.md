# Session 2 Kernel: Source Registry + Warehouse Query + Migrations

## What You Are

You are building Session 2 of 5 for the persistent data architecture overhaul. Session 1 (stop destructive imports) is complete. You are now creating the multi-source data model foundation.

## First Steps

1. Read THIS file completely
2. Read `SESSION_BUILD_LOG.md` — check Session 1 debrief for what was built, organic adaptations, and warnings
3. Read `Stage 2 - Evolved Data Model and Always-On Analytics.md` — your detailed plan
4. Read `Cross-Correlation - Stage 1-2-3 Overlap and Breaking Changes.md` — know what breaks downstream
5. Run `python -m pytest tests/ -x -q` to verify Session 1 baseline holds
6. Then start building

## What Session 1 Built (Expected State)

- Destructive DELETEs removed from csv_ingestion.py and conversation_rebuild.py
- Dedupe gate: `filter_new_tickets()` skips existing ticket_ids
- `clear_session.py` EPHEMERAL_TABLES updated (tickets/conversations/comments removed)
- `main_window.py` closeEvent only clears staging data
- Settings page has "Full Database Reset" button
- `import_mode.py`, `import_tracker.py`, `migrations/010_import_tracking.sql` exist
- **Verify this**: If Session 1 debrief notes deviations, adapt accordingly

## What You're Building

Source registry pattern, per-source database tables, warehouse query compatibility layer, data migration, and two proactive refactors (sidebar + ticket_index) that prevent Stage 3 breakage.

### Files to Create (5 files, ~615 LOC)

| File | LOC | Purpose |
|------|-----|---------|
| `src/data/source_registry.py` | ~200 | Source CRUD, table naming, validation, column mapping storage |
| `src/data/schema_builder.py` | ~150 | Dynamic DDL generation from source config, creates per-source tables + FTS |
| `src/data/warehouse_query.py` | ~180 | Unified query interface — replaces direct `FROM conversations` SQL |
| `migrations/011_source_registry.sql` | ~40 | `source_registry` table |
| `migrations/012_default_zendesk_source.sql` | ~30 | Copy existing data to `zendesk_default_*` tables, register default source |

### Files to Modify (4 files, ~200 LOC delta)

| File | What to Change |
|------|---------------|
| `src/ui/main_window.py` | **PROACTIVE**: Refactor sidebar from `setCurrentIndex(n)` to `setCurrentWidget(self.page_name)`. Update all 11 PAGE constants to use named lookup. This prevents Stage 3 breakage when Data Warehouse page is inserted. |
| `src/data/db_manager.py` | Add `source_id TEXT DEFAULT 'zendesk_default'` column to `ticket_index` table creation. **PROACTIVE**: prevents Stage 3 migration. |
| `migrations/013_provider_client_ids.sql` | Add `provider_id` and `client_id` columns (nullable) to ticket tables |
| `src/ui/pages/settings_page.py` | Add "Data Sources" section — registered sources list, "+ New Source" button |

### Tests to Write (~600 LOC)

| Test File | What It Covers |
|-----------|---------------|
| `tests/unit/test_source_registry.py` | Source CRUD, validation, table naming, duplicate prevention |
| `tests/unit/test_warehouse_query.py` | Query routing, date filtering, source filtering, return shape compat |
| `tests/schema/test_schema_migrations.py` | Migrations 011-013 safety, idempotency, data preservation |

## Critical Rules

1. **warehouse_query.py MUST return identical column names and types** as the old `SELECT * FROM conversations` queries. This is the compatibility contract that makes Session 3's 59-query migration safe.
2. **Migration 012 MUST copy ALL existing data** from `conversations` → `zendesk_default_conversations`, `tickets` → `zendesk_default_tickets`, `comments` → `zendesk_default_comments`. Verify row counts match.
3. **Do NOT drop the old `conversations` table** — keep it as deprecated. Session 3 will verify no code references it before removal.
4. **Sidebar refactor**: Find every `setCurrentIndex()` call across ALL UI files (there are 42). The PAGE constants at main_window.py:42-52 cover most, but line 1255 has a hardcoded `child.setCurrentIndex(2)`. Find ALL of them.
5. **Do NOT touch analytics engines yet** — that's Session 3. Only build the query layer they'll use.

## Sidebar Page Constants (current, for refactor reference)

```python
PAGE_CONVERSATIONS = 0
PAGE_DASHBOARD = 1
PAGE_TRENDING = 2
PAGE_INCIDENTS = 3
PAGE_REPORTS = 4
PAGE_AB_COMPARE = 5
PAGE_SMART_REPORTING = 6
PAGE_SETTINGS = 7
PAGE_SOURCE_MONITOR = 8
PAGE_GURU = 9
PAGE_GEMINI_CHATS = 10
```

## When You're Done

**MANDATORY**: Append a full debrief to `SESSION_BUILD_LOG.md` covering:
- Every file created/modified with line counts
- Deviations from plan (especially migration 012 — did row counts match?)
- Sidebar refactor: list every `setCurrentIndex` call found and how it was updated
- Regression results (test count before/after)
- Organic adaptations
- Updated file tree (add new files, update modified LOC)
- State for Session 3 (warehouse_query API surface, migration status, warnings)

## Verification Checklist

- [ ] `source_registry` table created, `zendesk_default` entry exists
- [ ] `zendesk_default_tickets`, `zendesk_default_conversations`, `zendesk_default_comments` tables created
- [ ] Row counts match: old conversations == zendesk_default_conversations
- [ ] `warehouse_query.get_conversations()` returns same shape as `SELECT * FROM conversations`
- [ ] Sidebar navigation works correctly (no wrong-page navigation)
- [ ] `ticket_index` has `source_id` column
- [ ] Provider_id and client_id columns exist (nullable)
- [ ] `python -m pytest tests/ -x -q` — all tests pass
- [ ] No grep hits for hardcoded `setCurrentIndex` with numeric literals (except internal widget stacks)
