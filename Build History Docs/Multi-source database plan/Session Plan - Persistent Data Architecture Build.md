# Session Plan: Persistent Data Architecture Build

## Can We Do It In One Session?

**No.** Here's why:

| Stage | New LOC | Modified LOC | Test LOC | Files to Read | Files to Write/Edit |
|-------|---------|-------------|----------|---------------|-------------------|
| 1 | ~165 | ~95 | ~600 | ~7,500 (7 files) | 9 files |
| 2 | ~1,315 | ~510 | ~1,050 | ~20,000+ (20+ files, 59 query sites) | 28 files |
| 3 | ~1,490 | ~710 | ~1,290 | ~15,000+ (19+ files) | 30 files |
| **Total** | **~2,970** | **~1,315** | **~2,940** | **~42,500+** | **~67 files** |

A single Claude Code session can realistically handle ~1,500-2,000 LOC of thoughtful implementation with testing before context pressure degrades quality. The full build is ~7,200 LOC across 67 files — that's **4-5 sessions**.

The constraint isn't writing speed — it's **read context**. Stage 2 alone requires reading 20,000+ lines across 20+ files to safely migrate 59 `FROM conversations` query sites. Doing that while also writing 2,800 LOC of new code and tests in one session risks sloppy migrations and missed query sites.

---

## Recommended Session Split: 5 Sessions

### Session 1: Stage 1 Complete (Stop Destructive Imports)

**Duration estimate**: Full session, moderate complexity
**Context load**: Light — only 7 files to read (~7,500 LOC)

**Kernel**: Load `DESTRUCTIVE_IMPORT_KERNEL.md` + `Stage 1 - Stop Destructive Imports.md`

**Tasks**:
1. Read all 7 target files (csv_ingestion, conversation_rebuild, lightdash_client, clear_session, main_window, settings_page, conversation_search)
2. Create `src/data/import_mode.py` (20 LOC)
3. Create `migrations/010_import_tracking.sql` (25 LOC)
4. Create `src/data/import_tracker.py` (120 LOC)
5. Modify `csv_ingestion.py` — remove DELETEs, add dedupe gate
6. Modify `conversation_rebuild.py` — remove DELETEs, add dedupe gate
7. Modify `lightdash_client.py` — pass mode param
8. Modify `clear_session.py` — update EPHEMERAL_TABLES
9. Modify `main_window.py` — update closeEvent, remove fallback
10. Modify `settings_page.py` — add Full Database Reset button
11. Write all Stage 1 tests (unit, integration, schema, regression)
12. Run full test suite (1,456 tests), fix any regressions
13. Manual QA: import CSV, verify additive, verify Clear & Close preserves data

**Write budget**: ~860 LOC code + ~600 LOC tests = **~1,460 LOC** — fits comfortably

**Exit criteria**:
- [ ] Both DELETE blocks removed
- [ ] Dedupe gate tested with overlapping ticket_ids
- [ ] Clear & Close preserves tickets/conversations/comments
- [ ] Settings "Full Database Reset" works
- [ ] All 1,456+ tests pass
- [ ] First additive import verified

---

### Session 2: Stage 2A — Source Registry + Warehouse Query + Migrations

**Duration estimate**: Full session, high complexity
**Context load**: Heavy — must read db_manager (2,421 LOC), understand full schema, design new tables

**Kernel**: Load updated kernel + `Stage 2 - Evolved Data Model and Always-On Analytics.md` + `Cross-Correlation` doc

**Tasks**:
1. Create `src/data/source_registry.py` (200 LOC) — source CRUD, table naming, validation
2. Create `src/data/schema_builder.py` (150 LOC) — dynamic DDL generation from source config
3. Create `src/data/warehouse_query.py` (180 LOC) — unified query interface
4. Create `migrations/011_source_registry.sql` (40 LOC)
5. Create `migrations/012_default_zendesk_source.sql` (30 LOC) — data migration
6. Create `migrations/013_provider_client_ids.sql` (15 LOC)
7. Add `source_id` column to `ticket_index` (proactive, prevents Stage 3 breakage)
8. Refactor `main_window.py` sidebar from index-based to named page lookup (proactive, prevents Stage 3 breakage)
9. Write tests: test_source_registry, test_warehouse_query, test_schema_migrations
10. Run migrations against test DB, verify data integrity
11. Run full test suite, fix regressions from sidebar refactor

**Write budget**: ~615 LOC code + ~600 LOC tests + ~100 LOC migrations = **~1,315 LOC**

**Why this is its own session**: The data migration (012) is the riskiest single operation. Copying data from `conversations` to `zendesk_default_conversations` must be bulletproof. The sidebar refactor touches `main_window.py` (1,392 lines) — a file that's easy to break. These need full attention.

**Exit criteria**:
- [ ] Source registry table created, Zendesk default registered
- [ ] Per-source tables created with correct schema
- [ ] Data migrated from old tables to zendesk_default_* tables
- [ ] warehouse_query returns same shape as old direct SQL
- [ ] Sidebar uses named page lookup (no numeric indices)
- [ ] ticket_index has source_id column
- [ ] All tests pass

---

### Session 3: Stage 2B — Analytics Migration + Redaction Engine

**Duration estimate**: Full session, high complexity
**Context load**: Very heavy — must read all analytics engines (~11,800 LOC across 9 files) + 59 query sites

**Kernel**: Load kernel + Stage 2 plan + Session 2 results

**Tasks**:
1. Create `src/data/redaction_engine.py` (200 LOC) — entity-aware PHI scrubbing
2. Create `config/entities/phi_allowlist.json` (50 LOC) — insurance names, business entities
3. Refine `config/redaction_patterns.json` — less aggressive patterns
4. Migrate analytics engines to warehouse_query (one file at a time, test after each):
   - `incident_engine.py` (659 LOC) — 3 conversations queries
   - `trending_engine.py` (2,071 LOC) — 6 conversations queries
   - `theta_engine.py` (717 LOC) — 3 conversations queries
   - `voc_builder.py` (2,282 LOC) — 1 conversations query
   - `product_gap_engine.py` (222 LOC) — 1 conversations query
   - `smart_pipeline.py` (472 LOC) — 1 conversations query
   - `report_builder.py` (754 LOC) — 3 conversations queries
   - `scan_orchestrator.py` (2,268 LOC) — 4 conversations queries
5. Migrate chat tools to warehouse_query:
   - `fast_path.py`, `thread_tools.py`, `semantic_tools.py`
6. Migrate `db_manager.py` conversations queries (14 occurrences)
7. Update `conversation_search.py` to use warehouse_query for search
8. Update FTS handling (per-source FTS tables via warehouse_query)
9. Remove conversation-loaded checks from analytics pages (incidents, trending, ai_reports)
10. Add source selector widget to analytics page filter bars
11. Write tests: test_always_on_analytics, test_redaction_engine
12. Retroactive redaction: migration 012 scrub of existing full_thread data
13. Run full test suite

**Write budget**: ~250 LOC new + ~510 LOC modifications + ~450 LOC tests = **~1,210 LOC**

**Why this is the hardest session**: 59 query sites across 20+ files. Every missed migration = silent data loss in that feature. Must go file-by-file with test runs between each. The redaction engine also needs careful tuning (insurance name preservation vs person name removal).

**Exit criteria**:
- [ ] All 59 `FROM conversations` queries routed through warehouse_query
- [ ] All analytics engines run against warehouse (no conversation loading required)
- [ ] Redaction engine correctly strips PHI, preserves business entities
- [ ] FTS search works across source tables
- [ ] Source selector appears on all analytics pages
- [ ] All tests pass

---

### Session 4: Stage 3A — Data Warehouse Page + Source Selector Widget

**Duration estimate**: Full session, moderate-high complexity
**Context load**: Moderate — mostly new UI code, references existing widget patterns

**Kernel**: Load kernel + Stage 3 plan + HTML mockup file as visual reference

**Tasks**:
1. Create `src/ui/widgets/source_selector.py` (120 LOC) — reusable dropdown
2. Create `src/ui/widgets/virtual_scroll_table.py` (300 LOC) — QAbstractTableModel + lazy loading
3. Create `src/ui/widgets/ticket_detail_panel.py` (250 LOC) — Overview, NLP, Timeline, Related tabs
4. Create `src/ui/widgets/trc_history_panel.py` (200 LOC) — volume sparkline, top issues, ngram trends
5. Create `src/ui/pages/data_warehouse_page.py` (500 LOC) — full page assembly
6. Add Data Warehouse to sidebar in `main_window.py` (using named lookup from Session 2)
7. Wire source_selector into existing analytics pages (incidents, trending, ai_reports, smart_reporting, guru)
8. Write tests: test_data_warehouse_page, test_virtual_scroll, test_source_selector, test_trc_history_panel
9. Visual QA against HTML mockups
10. Run full test suite

**Write budget**: ~1,370 LOC new UI + ~200 LOC modifications + ~670 LOC tests = **~2,240 LOC**

**Why this fills a session**: The Data Warehouse page is the single largest new UI component (~500 LOC). The virtual scroll model needs careful implementation for performance with 50,000+ rows. TRC history panel involves sparkline rendering and ngram trend display.

**Exit criteria**:
- [ ] Data Warehouse page loads, shows virtual-scroll table
- [ ] All filters work (date, TRC, client_id, provider_id, source, keyword)
- [ ] Ticket detail panel populates on row selection
- [ ] TRC history panel shows volume, top issues, ngram trends
- [ ] Source selector widget works on all analytics pages
- [ ] Sidebar navigation correct (no index breakage)
- [ ] All tests pass

---

### Session 5: Stage 3B — Multi-Source + Combined Analytics + Guru/Chat Source Awareness

**Duration estimate**: Full session, moderate complexity
**Context load**: Moderate — builds on Session 2-4 foundations

**Kernel**: Load kernel + Stage 3 plan + Session 4 results

**Tasks**:
1. Add Kodif source type support:
   - Update `source_registry.py` with Kodif template
   - Update `csv_ingestion.py` for Kodif column mapping
   - Update `conversation_rebuild.py` with skip_rebuild for self-contained conversations
   - Update `source_config_dialog.py` with Kodif option
2. Add combined cross-source analytics mode:
   - `warehouse_query.py` — union queries across sources
   - Analytics pages — Per-Source / Combined toggle
   - Charts show stacked bars by source in combined mode
3. Source-aware Guru:
   - `guru_friction_pipeline.py` — optional source_id parameter
   - `guru_content_pipeline.py` — source-aware matching
   - `guru_page.py` — source selector on Gap Analysis tab
4. Source-aware Chat tools:
   - `fast_path.py`, `semantic_tools.py`, `thread_tools.py` — source_id routing
5. Write tests: test_multi_source_analytics, test_kodif_import, test_guru_source_aware, test_chat_source_aware
6. Full regression: test_stage3_regression
7. Run full test suite (should be ~1,600+ tests at this point)

**Write budget**: ~200 LOC new + ~510 LOC modifications + ~620 LOC tests = **~1,330 LOC**

**Exit criteria**:
- [ ] Kodif CSV imports to separate source table
- [ ] Combined mode unions analytics across sources
- [ ] Guru friction scoped by source when filtered
- [ ] Chat tools route to correct source table
- [ ] All ~1,600 tests pass
- [ ] Full app walkthrough: import Zendesk + Kodif → run analytics per-source → run combined → browse warehouse → query via chat

---

## Session Dependency Chain

```
Session 1 (Stage 1) ─── must complete before ───→ Session 2
                                                      │
                                                      ▼
                                                   Session 3
                                                      │
                                                      ▼
                                                   Session 4
                                                      │
                                                      ▼
                                                   Session 5
```

Sessions 2-5 are strictly sequential — each builds on the prior.

---

## What Each Session Needs to Start

| Session | Prerequisite | Kernel/Docs to Load |
|---------|-------------|-------------------|
| 1 | None | `DESTRUCTIVE_IMPORT_KERNEL.md`, `Stage 1 plan`, HTML mockup |
| 2 | Session 1 verified | `DESTRUCTIVE_IMPORT_KERNEL.md`, `Stage 2 plan`, `Cross-Correlation` |
| 3 | Session 2 verified, migrations run | `Stage 2 plan`, Session 2 results, analytics file list |
| 4 | Session 3 verified | `Stage 3 plan`, HTML mockup, `Cross-Correlation` |
| 5 | Session 4 verified | `Stage 3 plan`, Session 4 results |

---

## Risk-Adjusted Timeline

| Session | Confidence | Likely Outcome |
|---------|-----------|---------------|
| 1 | **95%** completes in 1 session | Low complexity, small surface area, clear spec |
| 2 | **85%** completes in 1 session | Migration is risky but scope is controlled. Sidebar refactor may spill. |
| 3 | **70%** completes in 1 session | 59 query sites is a grind. If 5+ need non-trivial changes, could spill to a 3B session. |
| 4 | **80%** completes in 1 session | UI work is predictable. Virtual scroll has edge cases. |
| 5 | **85%** completes in 1 session | Builds on solid foundation. Combined analytics is the wildcard. |

**Most likely total: 5 sessions. Worst case: 6** (if Session 3 query migration spills).

---

## Alternative: Aggressive 3-Session Plan

If we want to compress, we can merge:

| Session | Covers | LOC Budget | Risk |
|---------|--------|-----------|------|
| 1 | Stage 1 + Stage 2 migrations/registry | ~2,700 LOC | **HIGH** — rushing the data migration |
| 2 | Stage 2 analytics migration + Stage 3 warehouse page | ~3,400 LOC | **VERY HIGH** — context overload |
| 3 | Stage 3 multi-source + combined + Guru/Chat | ~1,330 LOC | **MEDIUM** — if prior sessions are clean |

**I don't recommend this.** Session 2 would be writing 3,400 LOC while reading 30,000+ LOC of context. That's where bugs hide in migrated query sites.

---

## Recommendation

**Go with 5 sessions.** The 59-query-site migration in Session 3 is the riskiest part of the entire build. Giving it a full session with file-by-file migration and test-after-each-file discipline is what prevents the 40% break probability from materializing.

Session 1 can start immediately — all specs, mockups, and plans are ready.
