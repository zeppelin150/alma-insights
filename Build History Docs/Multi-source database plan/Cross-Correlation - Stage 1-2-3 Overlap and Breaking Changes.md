# Cross-Correlation: Stage 1 → 2 → 3 Overlap & Breaking Changes

## Purpose

This document maps every file touched across all three stages to identify:
1. Files modified in multiple stages (overlap risk)
2. Changes that break downstream stages if done wrong
3. Sequencing dependencies
4. Shared danger zones

---

## File Overlap Matrix

### Files Touched in Multiple Stages

| File | Stage 1 | Stage 2 | Stage 3 | Conflict Risk |
|------|---------|---------|---------|---------------|
| **csv_ingestion.py** | Remove DELETEs, add dedupe gate | Source-aware ingestion, redaction engine call | Kodif column mapping | **MEDIUM** — Each stage adds parameters. Must maintain backward-compat signatures. |
| **conversation_rebuild.py** | Remove DELETEs, add dedupe gate | Source-aware rebuild, target table routing | Self-contained conversation skip | **MEDIUM** — Stage 3 adds `skip_rebuild` flag that must not break Stage 1/2 logic. |
| **lightdash_client.py** | Pass mode param | Pass source_config param | No change | **LOW** — Additive parameters only. |
| **clear_session.py** | Update EPHEMERAL_TABLES | Source-aware clearing | No change | **LOW** — Stage 2 extends, doesn't contradict. |
| **settings_page.py** | Add "Full Database Reset" button | Source management UI section | Refinements to source UI | **LOW** — Different sections of the page. No overlap in widget placement. |
| **conversation_search.py** | Pass mode to ingest | Source selector on import | Source type selector, Kodif support | **MEDIUM** — UI layout changes compound. Plan widget placement for all 3 stages upfront. |
| **incidents_page.py** | No change | Remove conversation check, add source filter | Source selector dropdown, combined mode | **LOW** — Stage 2 adds the query change, Stage 3 adds UI widget. Clean separation. |
| **trending_topics.py** | No change | Remove conversation check, add source filter | Source selector dropdown, combined mode | **LOW** — Same pattern as incidents. |
| **ai_reports.py** | No change | Source selector for report scope | Per-source AI reports | **LOW** — Stage 2 adds parameter, Stage 3 adds UI. |
| **db_manager.py** | No change | New queries for source tables | No change | **LOW** — Additive queries only. |
| **scan_orchestrator.py** | No change | Read from warehouse_query | Source-scoped batching | **MEDIUM** — This is the most critical file (2268 lines). Stage 2 changes the data source. Stage 3 adds source scoping. Must be tested heavily between stages. |
| **main_window.py** | Update close dialog, remove fallback | No change | Add Data Warehouse page (sidebar index shift) | **HIGH** — Stage 3 shifts ALL sidebar indices 5+. Any hardcoded index from Stage 1 or 2 must be updated. |

---

## Breaking Change Chain Analysis

### Chain 1: EPHEMERAL_TABLES → clear_session → closeEvent

```
Stage 1: Remove tickets/conversations/comments from EPHEMERAL_TABLES
         Remove _clear_all_data() fallback from closeEvent
    ↓
Stage 2: Add source-aware clearing (per-source table clears)
    ↓
Stage 3: No change
```

**Risk**: If Stage 1 removes the wrong tables from EPHEMERAL_TABLES, Stage 2's source-aware clearing inherits the mistake.

**Mitigation**: Stage 1 test `test_clear_session_preserves_tickets` validates the exact table list. Stage 2 must re-validate.

---

### Chain 2: conversations table → warehouse_query → analytics engines

```
Stage 1: conversations table persists (additive imports)
    ↓
Stage 2: New per-source tables created. warehouse_query provides compatibility layer.
         Old `conversations` table data migrated to `zendesk_default_conversations`.
         Analytics engines switch from direct SQL to warehouse_query.
    ↓
Stage 3: Kodif source adds second set of tables.
         Combined mode unions across sources.
```

**Risk**: Stage 2 migration (012) copies data from `conversations` → `zendesk_default_conversations`. If Stage 1 import writes to `conversations` but Stage 2 analytics read from `zendesk_default_conversations`, there's a data gap during the transition.

**Mitigation**: Migration 012 must:
1. Copy ALL existing data from `conversations` to `zendesk_default_conversations`
2. Keep `conversations` table intact (deprecated, not dropped)
3. `warehouse_query` checks BOTH old and new tables during transition
4. Stage 3 can safely drop old `conversations` table once verified

---

### Chain 3: sidebar indices → main_window → all navigation calls

```
Stage 1: No sidebar change
    ↓
Stage 2: No sidebar change
    ↓
Stage 3: Insert Data Warehouse at index 5 → shifts Settings, AI Reports, Source Monitor, Guru
```

**Risk**: Any code from Stage 1 or 2 that hardcodes sidebar indices 5+ will navigate to the WRONG page after Stage 3.

**Mitigation**:
1. Audit all `setCurrentIndex()` calls across entire codebase BEFORE Stage 3
2. Consider switching to named page lookup instead of index-based: `self.page_stack.setCurrentWidget(self.settings_page)` instead of `setCurrentIndex(5)`
3. **Recommendation**: Do this refactor in Stage 2 proactively to prevent Stage 3 breakage

**Files with setCurrentIndex calls (must audit):**
```
src/ui/main_window.py          — Primary sidebar navigation
src/ui/pages/settings_page.py  — May navigate to other pages
src/ui/pages/ai_reports.py     — May navigate to settings
src/ui/pages/guru_page.py      — May navigate to settings for API config
```

---

### Chain 4: FTS index → search → conversation_search

```
Stage 1: conversations_fts stays as-is (additive inserts trigger FTS update)
    ↓
Stage 2: Per-source FTS tables created. conversation_search must query new FTS.
         Old conversations_fts deprecated.
    ↓
Stage 3: Kodif FTS table added. Search unions across all source FTS tables.
```

**Risk**: If Stage 2 creates per-source FTS but doesn't update `conversation_search.py` to query it, search returns 0 results.

**Mitigation**: `warehouse_query.search_fts()` handles FTS routing. `conversation_search.py` calls warehouse_query instead of direct FTS SQL.

---

### Chain 5: NLP scan → ticket_index → analytics

```
Stage 1: Scans read from persistent `conversations` table. ticket_index updated as before.
    ↓
Stage 2: Scans read from warehouse_query. ticket_index unchanged (it's source-agnostic).
    ↓
Stage 3: Scans can be scoped to a specific source. ticket_index may need source_id column.
```

**Risk**: `ticket_index` has no `source_id` column. After Stage 3, you can't tell which source a ticket_index row came from without joining back to the source ticket table.

**Mitigation**: Add `source_id` column to `ticket_index` in Stage 2 migration (013). It's nullable, defaults to 'zendesk_default' for existing rows. This prevents a Stage 3 migration.

**Recommendation**: Move this to Stage 2 to avoid a breaking schema change in Stage 3.

---

### Chain 6: redaction_engine → csv_ingestion → warehouse storage

```
Stage 1: No redaction changes. Data stored as-is (PHI present in ephemeral, scrubbed in persistent).
    ↓
Stage 2: redaction_engine created. csv_ingestion calls scrub() before writing to source tables.
    ↓
Stage 3: No redaction change.
```

**Risk**: Stage 1 imports store unscrubbed data in `conversations` (which is now persistent). When Stage 2 migrates this data to source tables, it's already in the DB without scrubbing.

**Mitigation**: Migration 012 should run redaction_engine.scrub() on full_thread when copying data from old `conversations` to new `zendesk_default_conversations`. This is a one-time retroactive scrub.

---

## Recommended Cross-Stage Actions

### Do in Stage 2 (to prevent Stage 3 breakage):

1. **Add `source_id` to `ticket_index`** — prevents schema migration in Stage 3
2. **Refactor sidebar to named page lookup** — prevents index-shift breakage in Stage 3
3. **Add `source_id` parameter to all analytics engine function signatures** — even if unused in Stage 2, prevents signature changes in Stage 3
4. **Create `warehouse_query.search_fts()` with source routing** — prevents FTS breakage in Stage 3

### Do in Stage 1 (to prevent Stage 2 breakage):

1. **Don't modify `conversations` table schema** — Stage 2 will create new tables and migrate
2. **Use `import_mode.py` enums consistently** — Stage 2 extends them
3. **Keep `import_tracker.py` source-agnostic** — it tracks runs, not source types

---

## Cumulative Test Count

| Stage | New Tests | Cumulative Total (est.) |
|-------|-----------|------------------------|
| Baseline | 631 | 631 |
| Stage 1 | ~13 | ~644 |
| Stage 2 | ~65 | ~709 |
| Stage 3 | ~60 | ~769 |

---

## Cumulative LOC Impact

| Stage | New Files | Modified LOC | New LOC | Cumulative New LOC |
|-------|-----------|-------------|---------|-------------------|
| Stage 1 | 3 | ~95 | ~365 | ~365 |
| Stage 2 | 12 | ~510 | ~2,280 | ~2,645 |
| Stage 3 | 11 | ~710 | ~2,660 | ~5,305 |

**Total across all stages**: ~5,300 new LOC, ~1,315 modified LOC, ~26 new files, ~169 new tests

---

## Critical Path

```
Stage 1 (stop bleeding)
    │
    ├── Must complete BEFORE Stage 2 can start
    │   (Stage 2 assumes additive imports work)
    │
    ▼
Stage 2 (data model + always-on)
    │
    ├── Must complete BEFORE Stage 3 can start
    │   (Stage 3 assumes source registry, warehouse query, always-on analytics exist)
    │
    ├── PROACTIVE: Add source_id to ticket_index
    ├── PROACTIVE: Refactor sidebar to named page lookup
    ├── PROACTIVE: Add source_id params to analytics signatures
    │
    ▼
Stage 3 (UI + multi-source + warehouse)
    │
    └── Can be split into sub-phases:
        3a: Data Warehouse page (independent of multi-source)
        3b: Kodif source type + multi-source analytics
        3c: Combined cross-source mode
        3d: Guru/Chat source awareness
```

---

## Files NEVER Touched Across All 3 Stages (Stability Anchors)

These files are safe zones — no modifications planned:

```
src/agents/worker_agent.py
src/agents/tool_registry.py         (boundary guard stays)
src/agents/supervisor.py
src/agents/rate_governor.py
src/agents/batch_packer.py
src/agents/analyst_agent.py
src/agents/report_bridge_client.py
src/agents/report_orchestrator.py
src/data/nlp_meta_analyzer.py
src/data/ticket_index_writer.py     (source-agnostic by design)
src/data/schedule_manager.py
src/data/tech_summary_builder.py
src/data/usage_tracker.py
src/data/zendesk_client.py
src/data/zendesk_monitor.py
src/data/source_warehouse.py        (cold tier, separate from hot tier warehouse)
src/data/watchlist_engine.py
src/data/guru_client.py
src/data/guru_effectiveness.py
src/llm/model_registry.py
src/llm/claude_client.py
src/updater/*
src/gemini/*
src/ui/theme.py
src/ui/qt_error_guard.py
src/ui/widgets/analysis_page_base.py
src/ui/widgets/shared_filter_bar.py
```
