# Fix Plan: Multi-Source Data Architecture Gaps (Sessions 6A/6B/6C)

## Context

The 5-session multi-source database build (S1-S5) created the infrastructure for per-source data isolation — source registry, per-source tables, warehouse query routing, and source selector widgets. However, 5 gaps remain:

1. **Dual-write gap** (Critical): Imports write only to shared tables; per-source tables are never populated after migration 012's one-time copy
2. **Source selectors display-only** (High): Dropdowns on 6 analytics pages are not wired to workers/engines
3. **Guru source filtering no-op** (Medium): `source_id` param accepted but ignored; `sub_patterns` lacks `source_id` column
4. **`_clear_all_data` gaps** (Medium): Per-source FTS tables not DROPped (only DELETEd, which fails for FTS5); `source_registry` cleared unnecessarily
5. **No Combined/Per-Source toggle** (Low): Collapses into Issue 2 — SourceSelector "All Sources" already IS combined mode once wired

Issue 1 is the keystone: until imports write to per-source tables, source filtering is academic.

---

## Session 6A: Close the Dual-Write Gap

**Goal:** Make imports write to both shared AND per-source tables. Fix the `upsert_conversation` schema bug. Populate per-source FTS on write.

### 6A-1. `src/data/db_manager.py` — upsert methods (~+65 LOC)

**upsert_ticket** (line 916): Add `table_prefix: str | None = None` param.
- Keep existing shared-table INSERT as-is (backward compat)
- When `table_prefix` provided, execute a second `INSERT OR REPLACE INTO [{table_prefix}_tickets]` with the same values plus `source_id=table_prefix`, `imported_at=datetime.utcnow().isoformat()`, `metadata='{}'`

**upsert_comment** (line 947): Same pattern — dual write to `[{table_prefix}_comments]` with `source_id=table_prefix`.

**upsert_conversation** (line 962): **Bug fix first.** The current INSERT references column `source` (line 967) but the `conversations` table schema (line 120-134) has `dataset_id` as column 14, not `source`. Fix: change column name to `dataset_id`, value to `conv.get("dataset_id", 0)`. Then add dual write to `[{table_prefix}_conversations]` with `source_id=table_prefix`.

### 6A-2. `src/data/db_manager.py` — new `rebuild_source_fts()` (~+15 LOC)

```python
def rebuild_source_fts(self, table_prefix: str):
    """Rebuild per-source FTS5 index from per-source conversations table."""
    fts = f"[{table_prefix}_fts]"
    conv = f"[{table_prefix}_conversations]"
    try:
        self.conn.execute(f"DELETE FROM {fts}")
        self.conn.execute(f"""
            INSERT INTO {fts}(ticket_id, subject, trc_label, full_thread)
            SELECT ticket_id, subject, trc_label, full_thread FROM {conv}
        """)
        self.conn.commit()
    except sqlite3.OperationalError:
        pass
```

### 6A-3. `src/data/csv_ingestion.py` — thread table_prefix (~+30 LOC)

- **`ingest_csv()`** (line 591): After existing source_config extraction, resolve `table_prefix`:
  ```python
  table_prefix = None
  if source_config and source_config.get("source_id"):
      try:
          from src.data.source_registry import SourceRegistry
          reg = SourceRegistry(db.conn)
          src = reg.get_source(source_config["source_id"])
          if src:
              table_prefix = src["table_prefix"]
      except Exception:
          pass
  ```
- **`_write_tickets_to_db()`** (line 259): Add `table_prefix=None` param, pass to `_write_single_ticket()`
- **`_write_single_ticket()`** (line 286): Add `table_prefix=None` param, pass to all 3 `db.upsert_*()` calls
- **`_write_self_contained_to_db()`** (line 448): Add `table_prefix=None` param, pass to `db.upsert_ticket()` and `db.upsert_conversation()` calls
- **Post-ingest** (around line 536): After `db.rebuild_fts_index()`, call `db.rebuild_source_fts(table_prefix)` if `table_prefix` is not None

### 6A-4. `src/data/conversation_rebuild.py` — thread table_prefix (~+15 LOC)

- **`rebuild_conversations()`** (line 18): Add `table_prefix=None` param, pass to `_rebuild_single_ticket()`
- **`_rebuild_single_ticket()`** (line 265): Add `table_prefix=None` param, pass to all `db.upsert_*()` calls

### 6A-5. `src/data/import_tracker.py` — per-source dedup (~+10 LOC)

- **`get_existing_ticket_ids()`** (line 65): Add `table_prefix=None` param. When provided, query `SELECT ticket_id FROM [{table_prefix}_tickets]` instead of shared `tickets`. Fall back to shared table on error (table may not exist yet).

### 6A-6. Tests: `tests/test_s6a_dual_write.py` (~250 LOC, 15 tests)

| Class | Test | Validates |
|-------|------|-----------|
| TestUpsertDualWrite | test_upsert_ticket_shared_only | No table_prefix → shared table only, backward compat |
| | test_upsert_ticket_dual_write | table_prefix → both shared + per-source populated |
| | test_upsert_ticket_per_source_has_source_id | Per-source row has source_id=table_prefix |
| | test_upsert_comment_dual_write | Both tables get comment row |
| | test_upsert_conversation_bug_fix | dataset_id column used (not source), value correct |
| | test_upsert_conversation_dual_write | Both tables get conversation row |
| TestPerSourceDedup | test_dedup_queries_shared_by_default | No table_prefix → queries shared tickets |
| | test_dedup_queries_per_source_table | table_prefix → queries per-source tickets |
| | test_dedup_fallback_on_missing_table | Missing per-source table → falls back to shared |
| TestCsvIngestDualWrite | test_ingest_without_source_config | No source_config → shared table only (backward compat) |
| | test_ingest_with_source_config_populates_both | source_config with source_id → both tables |
| | test_self_contained_ingest_dual_write | Kodif path also dual-writes |
| TestFtsPopulation | test_per_source_fts_populated_after_ingest | Per-source FTS table searchable |
| | test_shared_fts_still_works | Shared FTS unbroken |
| TestWarehouseExitsLegacy | test_legacy_mode_off_after_dual_write | WarehouseQuery._legacy_mode = False after per-source data exists |

**Fixture pattern:** Real SQLite DB via `tmp_path`. Create source_registry + per-source tables via schema_builder. Matches existing test conventions (see `tests/test_stage2_source_registry.py`).

### 6A Verification

```bash
python -m pytest tests/test_s6a_dual_write.py -x -v
# Then verify existing tests still pass:
python -m pytest tests/test_incremental_import.py tests/test_stage2_source_registry.py tests/test_stage3_redaction_analytics.py -x -q
```

---

## Session 6B: Wire Source Selectors to Workers and Engines

**Goal:** Connect all 6 SourceSelector widgets to analysis execution. Thread `source_id` from UI → worker → engine → WarehouseQuery.

### 6B-1. Worker constructors — add source_id

**`src/ui/pages/incidents_page.py`** (~+8 LOC):
- `IncidentWorker.__init__()` (line 57): Add `source_id=None` param, store as `self.source_id`
- `IncidentWorker.run()`: Pass `source_id=self.source_id` to `run_incident_scan()`
- `_run_scan_directly()` (around line 483): Extract `self._source_selector.selected_source_id()`, pass to `IncidentWorker(..., source_id=source_id)`

**`src/ui/pages/trending_topics.py`** (~+8 LOC):
- `TrendingWorker.__init__()` (line 62): Add `source_id=None`, store
- `TrendingWorker.run()`: Pass `source_id=self.source_id` to `run_full_analysis()`
- Worker creation (around line 962): Extract source_id from `self._source_selector`, pass to worker

**`src/ui/pages/ai_reports.py`** (~+8 LOC):
- `PipelineWorker.__init__()` (line 179): Add `source_id=None`, store
- `PipelineWorker.run()`: Pass `source_id=self.source_id` to `AIReportPipeline.run()`
- `_on_generate()` (around line 909): Extract source_id, pass to worker

**`src/ui/pages/smart_reporting.py`** (~+5 LOC):
- `_build_pipeline_config()` (line 989): Add `"source_id": self._source_selector.selected_source_id()` to config dict
- Verify `run_pipeline()` in `src/data/smart_pipeline.py` reads `config.get("source_id")` and threads it

**`src/ui/pages/guru_page.py`** (~+5 LOC):
- `_on_run_gap_analysis()` (line 491): Change `self._friction_pipeline.analyze_coverage()` to `self._friction_pipeline.analyze_coverage(source_id=self._gap_source_selector.selected_source_id())`
- `_on_analyze_friction_deep()`: Same — pass `source_id` from selector

**`src/ui/pages/conversation_search.py`** (~+5 LOC):
- Wire `_import_source_selector.selected_source_id()` into the `source_config` dict that gets passed to `ingest_csv()` (completing the 6A chain)

### 6B-2. Engine entry points — accept source_id

**`src/data/incident_engine.py`** (~+12 LOC):
- `run_incident_scan(db, ..., source_id=None)`: Pass source_id to all `self._wq.query_*()` calls and `db.get_trc_codes(source_id=source_id)`
- Note: `daily_counts` rollup table is not per-source. Incident scan with source_id will still use global daily counts for Poisson baselines. This is acceptable for v1 (baselines should reflect overall patterns).

**`src/data/trending_engine.py`** (~+10 LOC):
- `run_full_analysis(conn, ..., source_id=None)`: Pass to `_fetch_conversations()` which already calls `wq.query_conversations_raw(..., source_id=source_id)`
- `_get_wq()` helper already exists — just need to thread source_id to callers

**`src/data/ai_report_pipeline.py`** (~+8 LOC):
- `AIReportPipeline.run(..., source_id=None)`: Pass to data block builder and evidence extraction calls

**`src/data/smart_pipeline.py`** (~+5 LOC):
- `run_pipeline(config, ...)`: Read `config.get("source_id")`, thread to internal calls

**`src/data/db_manager.py`** (~+15 LOC):
- `get_trc_codes(self, source_id=None)` (line 1006): Pass to `_wq.query_conversations_raw()` call
- `get_date_range(self, source_id=None)` (line ~1018): Same pattern
- These methods already use `_wq` property internally — just need to forward source_id

### 6B-3. Tests: `tests/test_s6b_selector_wiring.py` (~200 LOC, 12 tests)

| Class | Test | Validates |
|-------|------|-----------|
| TestIncidentSourceFilter | test_worker_stores_source_id | Constructor stores param |
| | test_engine_receives_source_id | run_incident_scan gets source_id (mock engine) |
| | test_incident_scan_filtered_data | Real DB: 2 sources, source_id filters results |
| TestTrendingSourceFilter | test_worker_stores_source_id | Constructor stores param |
| | test_trending_filtered | Real DB: source_id scopes conversations to one source |
| TestPipelineSourceFilter | test_worker_stores_source_id | Constructor stores param |
| | test_ai_report_scoped | Real DB: report data block only from selected source |
| TestSmartPipelineConfig | test_config_includes_source_id | _build_pipeline_config has source_id key |
| TestGuruSourceParam | test_gap_analysis_passes_source_id | analyze_coverage called with source_id |
| TestDbManagerSourceFilter | test_get_trc_codes_filtered | Only TRCs from selected source |
| | test_get_date_range_filtered | Date range from selected source only |
| TestConversationSearchImport | test_import_uses_source_selector | source_config includes source_id from selector |

### 6B Verification

```bash
python -m pytest tests/test_s6b_selector_wiring.py -x -v
# Regression:
python -m pytest tests/test_s6a_dual_write.py tests/test_stage4_data_warehouse.py tests/test_stage5_multi_source.py -x -q
```

---

## Session 6C: Guru Source Tagging + Clear Fix + E2E

**Goal:** Make Guru source filtering real, fix _clear_all_data for FTS tables, run full E2E validation.

### 6C-1. Migration 014: `migrations/014_sub_pattern_source_id.sql` (~5 LOC)

```sql
-- Add source_id to sub_patterns for per-source friction filtering
ALTER TABLE sub_patterns ADD COLUMN source_id TEXT DEFAULT NULL;

-- Add source_id to nlp_scan_runs for scan-to-source linkage
ALTER TABLE nlp_scan_runs ADD COLUMN source_id TEXT DEFAULT NULL;
```

### 6C-2. `src/data/db_manager.py` — DDL update (~+2 LOC)

- `sub_patterns` CREATE TABLE (line ~453): Add `source_id TEXT DEFAULT NULL` column
- `nlp_scan_runs` CREATE TABLE (line ~380): Add `source_id TEXT DEFAULT NULL` column

### 6C-3. `src/agents/scan_orchestrator.py` — source tagging (~+10 LOC)

- The orchestrator creates scan runs. Add `source_id` to the INSERT INTO `nlp_scan_runs`. The scan UI (or caller) must pass `source_id` — if not provided, default to NULL (scans all sources).
- When creating scan batches, record the source_id so downstream meta-analyzer knows.

### 6C-4. `src/data/nlp_meta_analyzer.py` — propagate source_id (~+15 LOC)

- `run_analysis(scan_id, source_id=None)` (line 26): Accept source_id
- INSERT INTO sub_patterns (line 252-263): Add `source_id` to column list and value
- Look up source_id from `nlp_scan_runs` if not passed directly: `SELECT source_id FROM nlp_scan_runs WHERE scan_id = ?`

### 6C-5. `src/data/guru_friction_pipeline.py` — real filtering (~+8 LOC)

- `_get_active_friction_types(source_id=None)` (line 627): Add conditional WHERE clause:
  ```python
  sql = """SELECT DISTINCT friction_type, trc, label FROM sub_patterns
           WHERE merged_into IS NULL AND tier != 'retired'
           AND friction_type IS NOT NULL AND friction_type != ''"""
  params = []
  if source_id:
      sql += " AND source_id = ?"
      params.append(source_id)
  sql += " ORDER BY friction_type"
  rows = self.db.conn.execute(sql, params).fetchall()
  ```
- Remove the comment block (lines 634-638) that says source_id is ignored

### 6C-6. `src/ui/main_window.py` — `_clear_all_data()` fix (~+15 LOC)

Lines 1308-1365. Three changes:

1. **Handle per-source FTS tables**: Before the sqlite_master query, find and DROP all FTS virtual tables:
   ```python
   fts_tables = conn.execute(
       "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_fts'"
   ).fetchall()
   for (fts_name,) in fts_tables:
       conn.execute(f"DROP TABLE IF EXISTS [{fts_name}]")
   ```
   The existing `conversations_fts` DROP is already there (line 1336). Extend to cover per-source FTS tables.

2. **Exclude `source_registry` from deletion**: Add `source_registry` to `PRESERVE_TABLES` (line 1319). Source metadata should survive reset so sources can be re-populated on next import without re-registration.

3. **The existing `NOT LIKE 'conversations_fts%'` exclusion** (line 1343) already handles FTS shadow tables. Extend it to also exclude per-source FTS shadow tables: `AND name NOT LIKE '%_fts%'`.

### 6C-7. Tests: `tests/test_s6c_guru_clear_e2e.py` (~300 LOC, 18 tests)

| Class | Test | Validates |
|-------|------|-----------|
| TestMigration014 | test_adds_source_id_to_sub_patterns | Column exists after migration |
| | test_adds_source_id_to_nlp_scan_runs | Column exists after migration |
| | test_migration_idempotent | Double-run doesn't error |
| TestGuruSourceFiltering | test_all_sources_returns_all | source_id=None → all friction types |
| | test_source_filtered | source_id="zendesk" → only zendesk friction types |
| | test_unknown_source_returns_empty | Nonexistent source → empty list |
| | test_null_source_patterns_included_in_all | Patterns with NULL source_id included when source_id=None |
| TestClearAllData | test_per_source_fts_dropped | FTS virtual tables gone after clear |
| | test_source_registry_preserved | source_registry entries survive clear |
| | test_per_source_data_tables_cleared | Per-source ticket/conversation data deleted |
| | test_shared_tables_cleared | Shared tables still cleared |
| | test_preserve_tables_survive | NLP tables survive clear |
| TestEndToEnd | test_full_lifecycle_single_source | Import → verify dual-write → query via WarehouseQuery → clear → verify empty |
| | test_full_lifecycle_multi_source | Import Zendesk + Kodif → per-source tables populated → WarehouseQuery unions both → filter to one source → results scoped |
| | test_import_filter_analyze_cycle | Import → set source filter → run trending analysis → verify only selected source's data used |
| | test_source_selector_all_sources | "All Sources" → WarehouseQuery unions → full dataset returned |
| | test_clear_and_reimport | Clear → reimport → per-source tables repopulated → source_registry intact |
| | test_legacy_fallback_still_works | DB with no source_registry → WarehouseQuery uses shared tables → no crash |

### 6C Verification

```bash
python -m pytest tests/test_s6c_guru_clear_e2e.py -x -v
# Full regression across all session test files:
python -m pytest tests/test_s6a_dual_write.py tests/test_s6b_selector_wiring.py tests/test_s6c_guru_clear_e2e.py tests/test_incremental_import.py tests/test_stage2_source_registry.py tests/test_stage3_redaction_analytics.py tests/test_stage4_data_warehouse.py tests/test_stage5_multi_source.py tests/test_build11_regression.py tests/unit/test_clear_session.py -x -q
```

---

## Dependency Order

```
6A (Dual-Write) ──→ 6B (Selector Wiring) ──→ 6C (Guru + Clear + E2E)
```

6A MUST complete first — without per-source data, source filtering is meaningless.
6B depends on 6A — selectors need per-source data to demonstrate filtering.
6C depends on 6B — E2E tests exercise the full stack from UI through to per-source queries.

## LOC Summary

| Session | Production LOC | Test LOC | Files Modified | Files Created |
|---------|---------------|----------|----------------|---------------|
| 6A | ~135 | ~250 | 4 modified | 1 test |
| 6B | ~90 | ~200 | 10 modified | 1 test |
| 6C | ~50 | ~300 | 5 modified | 1 migration + 1 test |
| **Total** | **~275** | **~750** | **19 modified** | **4 created** |

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| upsert_conversation `source` bug fix changes behavior | The column doesn't exist in the schema — current INSERT silently sets `dataset_id=0`. Fix is corrective. |
| Legacy mode exit after dual-write | WarehouseQuery detects per-source data → exits legacy mode → reads from per-source tables. Dual-write ensures both paths have data. |
| Incident engine daily_counts not per-source | Poisson baselines use global counts. Acceptable for v1 — anomaly detection benefits from full-population baselines. |
| NLP scans don't know source_id yet | sub_patterns.source_id defaults to NULL. `_get_active_friction_types(source_id=None)` returns all patterns including NULL. Only explicit source filtering skips NULL-source patterns. |
| Scan orchestrator has no source_id | Thread from UI or infer from scan target. Default NULL means existing scans work unchanged. |

## Critical Files Reference

- `src/data/db_manager.py` — upsert methods (916-984), sub_patterns DDL (~453), nlp_scan_runs DDL (~380)
- `src/data/csv_ingestion.py` — ingest_csv (591), _write_tickets_to_db (259), _write_single_ticket (286), _write_self_contained_to_db (448)
- `src/data/conversation_rebuild.py` — rebuild_conversations (18), _rebuild_single_ticket (265)
- `src/data/import_tracker.py` — get_existing_ticket_ids (65)
- `src/data/warehouse_query.py` — _legacy_mode detection (25-63), _get_tables (65-76)
- `src/data/incident_engine.py` — run_incident_scan entry point
- `src/data/trending_engine.py` — run_full_analysis (~1378)
- `src/data/ai_report_pipeline.py` — AIReportPipeline.run()
- `src/data/smart_pipeline.py` — run_pipeline()
- `src/data/guru_friction_pipeline.py` — analyze_coverage (110), _get_active_friction_types (627)
- `src/data/nlp_meta_analyzer.py` — run_analysis (26), INSERT sub_patterns (252)
- `src/agents/scan_orchestrator.py` — meta-analyzer call (867-871)
- `src/ui/pages/incidents_page.py` — IncidentWorker (57), _run_scan_directly (~483)
- `src/ui/pages/trending_topics.py` — TrendingWorker (62), worker creation (~962)
- `src/ui/pages/ai_reports.py` — PipelineWorker (179), _on_generate (~909)
- `src/ui/pages/smart_reporting.py` — _build_pipeline_config (989), _source_selector (357)
- `src/ui/pages/guru_page.py` — _on_run_gap_analysis (469), _gap_source_selector (375)
- `src/ui/pages/conversation_search.py` — _import_source_selector (85)
- `src/ui/main_window.py` — _clear_all_data (1308-1365), PRESERVE_TABLES (1319)
- `src/ui/widgets/source_selector.py` — selected_source_id() (58-61)
