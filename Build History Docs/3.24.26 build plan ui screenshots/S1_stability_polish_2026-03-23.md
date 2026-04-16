# Session 1: Stability & Polish — Fix, Decompose, Test

**Date**: 2026-03-23
**Plan File**: `C:\Users\Chris\.claude\plans\stability-and-polish.md`
**Phases Completed**: 1 (Fix Broken Things), 2A (Data Layer Complexity), 2B partial, 3 (Test Coverage)
**Phases Remaining**: 2B remainder, 4 (UX Polish), 5 (Test Restructuring), 6 (Prompt Improvement)

---

## What Was Done

### Phase 1: Fix Broken Things

**1A — Settings Restoration**
- Merged 11 sections from `config/settings.yaml.migrated` into `data/settings.yaml`
- Added `ai.task_routing` section with `override_all` + `routes` structure (9 task types)
- Updated default model from `gemini-2.0-flash` to `gemini-2.5-flash`
- Files: `data/settings.yaml`

**1B — Test Failure Fixes (4 → 0)**

| Test | Root Cause | Fix |
|------|-----------|-----|
| `test_record_baseline` | Hardcoded dates (Mar 1-3) expired past 14-day lookback | Replaced with `datetime.now() - timedelta(days=N)` |
| `test_task_routing_in_settings_yaml` | Missing `ai.task_routing` in settings | Fixed by 1A |
| `test_task_routing_has_all_tasks` | Missing `routes` sub-dict | Fixed by 1A (corrected YAML structure) |
| `test_orchestrator_default_fallback` | Test patched wrong module | Migrated `scan_orchestrator._load_model_from_config()` to use `settings_manager.get_section()` instead of raw YAML, updated test patch target |

Files: `tests/test_guru_pipeline.py`, `src/agents/scan_orchestrator.py`, `tests/test_pipeline_full.py`

**1C — Guru Feature Fixes**
- **Workbench signal wiring**: Connected `GuruWorkbenchPanel.analysis_complete` signal to new `_on_workbench_commit()` handler in `guru_page.py`. Calls `propose_rewrite()` to generate a clean draft from redline analysis.
- **New article creation**: Added `create_card(collection_id, title, content)` to `GuruClient` (POST to `/cards` endpoint, TEAM visibility). Updated `approve_and_push()` with new article path: try API create → fall back to clipboard copy.
- Files: `src/ui/pages/guru_page.py`, `src/data/guru_client.py`, `src/data/guru_content_pipeline.py`

**1D — Trending Correlation Verb Bug**
- Fixed `trending_engine.py:1060`: `else "rises"` → `else "falls"`

**1E — Dead File Cleanup**
- Archived 8 files to `_archive/dead_code/` and `_archive/dead_tests/` (not deleted, per user request):
  - `src/ui/pages/nlp_scanner_page.py` (1,542 lines — absorbed into TRC Analytics Tab 2)
  - `tests/test_full_system.py`, `test_session_4_5.py`, `test_session_6.py`, `test_session_7.py` (old manual harnesses)
  - `tests/test_calendar_grab.py`, `test_calendar_visual.py` (visual debugging scripts)
  - `tests/test_build_55.py` (depended on dead NLPScannerPage)
- Archived `debug/captures/` (~93MB of Feb diagnostic dumps)
- Removed `scan_server/node_modules/` (~72MB)

**1F — Minor Fixes**
- `scan_worker.py`: System prompt "JSON arrays" → "structured tool_call blocks"
- `trc_analytics.py`: Cost estimator pricing updated from 2.0 Flash ($0.10/$0.40) to 2.5 Flash ($0.075/$0.30)
- `trc_analytics.py`: Added double meta-analysis guard (`_meta_analysis_running` flag + `nlp_findings` existence check)
- `conversation_search.py`: Fixed `self.search_box` → `self.keyword_input`

---

### Phase 2: Complexity Surgery (6 of 9 Targets)

Pure refactoring — behavior identical, functions decomposed for testability.

| File | Function | CC Before | CC After | Sub-functions Extracted |
|------|----------|-----------|----------|------------------------|
| `csv_ingestion.py` | `ingest_csv` | 61 | ~12 | `_read_csv_with_encoding`, `_resolve_columns`, `_group_rows_by_ticket`, `_write_tickets_to_db`, `_write_single_ticket`, `_post_ingest` |
| `report_builder.py` | `format_data_block_for_prompt` | 51 | ~5 | 13 `_fmt_*` section formatters |
| `trending_engine.py` | `compute_topic_model` | 46 | ~10 | `_build_tfidf_standalone`, `_run_nmf_model`, `_assign_doc_to_window`, `_compute_topic_trends`, `_find_multi_topic_tickets` |
| `conversation_rebuild.py` | `rebuild_conversations` | 39 | ~8 | `_fetch_raw_rows`, `_group_by_ticket`, `_extract_ticket_fields`, `_extract_resolution_times`, `_build_thread`, `_rebuild_single_ticket` |
| `trending_engine.py` | `test_hypothesis` | 33 | ~8 | `_fetch_matching_conversations`, `_build_concept_groups`, `_compute_concept_correlation`, `_summarize_sentiment`, `_run_gemini_synthesis` |
| `scan_report_builder.py` | `_build_diagnostics_section` | 32 | ~3 | 7 `_diag_*` metric formatters |

**Deferred to next session** (lower priority, higher risk):
- `scan_orchestrator._worker_loop` (CC 50, 444 lines) — most complex refactoring, needs careful state management
- `db_manager._migrate` (CC 42, 181 lines) — version-specific migration blocks
- `markdown_viewer._md_to_html_regex` (CC 38) — replace with `markdown` library (Decision 6)

---

### Phase 3: Test Coverage

**3A — Infrastructure**
- Created `tests/conftest.py` with 8 shared fixtures:
  - `empty_db` — fully initialized DatabaseManager (59 tables, zero rows)
  - `seeded_db` — 100 deterministic tickets (3 TRCs × ~33, 30-day span, CSAT, resolution times, daily_counts)
  - `seeded_conn` — raw SQLite connection from seeded_db
  - `mock_settings` — temp settings.yaml with safe defaults
  - `mock_gemini_client`, `mock_claude_client` — MagicMock LLM clients
  - `mock_client_factory` — patches `build_client_for_task()` by provider
  - `mock_guru_client` — canned collection/card data
- Created directory structure: `tests/unit/`, `tests/integration/`, `tests/ui/`, `tests/e2e/`
- Added pytest markers: `@pytest.mark.slow`, `@pytest.mark.e2e`, `@pytest.mark.ui`, `@pytest.mark.live_db`

**3B-3F — Unit Tests (246 new tests)**

| File | Tests | Module | Key Coverage |
|------|-------|--------|-------------|
| `tests/unit/test_trending_engine.py` | 92 | trending_engine.py (2,034 LOC) | Sentiment bucketing, rising terms velocity, NMF topic model, cross-TRC correlations, hypothesis testing, concept maps, all 5 window sizes, full analysis orchestration |
| `tests/unit/test_report_builder.py` | 44 | report_builder.py (644 LOC) | All 13 section formatters, full data block assembly, build_data_block with seeded DB, edge cases (empty sections, missing keys) |
| `tests/unit/test_incident_engine.py` | 43 | incident_engine.py | Poisson parameters (lambda, theta1, theta2), CUSUM accumulation/reset, flag lifecycle (create/update/acknowledge/resolve), tier assignment, intervention correlation |
| `tests/unit/test_csv_ingestion.py` | 34 | csv_ingestion.py (461 LOC) | Encoding fallback (UTF-8/Latin-1), column mapping (91 variants), required field validation, ticket grouping, destructive import, full E2E round-trip |
| `tests/unit/test_nlp_meta_analyzer.py` | 33 | nlp_meta_analyzer.py | Within-TRC analysis, sub-taxonomy lifecycle (probationary→active→dormant→retired), snapshots, cross-TRC detection, finding deduplication, n-gram upsert |

---

## Test Results

| Metric | Before Session | After Session |
|--------|---------------|---------------|
| Total tests | ~600 passing, 4 failing | **~850 passing, 0 new failures** |
| New unit tests | 0 | 246 |
| Zero-coverage critical modules | 5 | 0 |
| Pre-existing failure | `test_bridge_fallback` (1) | Same (1) — not in scope |
| Dead code archived | 0 lines | ~4,500+ lines |
| F-rated functions decomposed | 0 | 6 of 9 |

---

## Caveats and Known Issues

1. **Pre-existing `test_bridge_fallback` failure** — `test_reporting_foundation.py::TestClientFactory::test_bridge_fallback_when_unavailable` was failing before this session and was not addressed. It's a test environment issue (bridge subprocess not available in test context).

2. **conftest.py `daily_counts` column name** — Fixed during session (`day_bucket` → `date` to match actual schema). The incident_engine tests have their own seeding helper that works around this.

3. **Deferred Phase 2B refactoring** — Three complex functions remain:
   - `scan_orchestrator._worker_loop` (CC 50) — This is the most critical refactoring target but also the highest-risk. It's the inner scan processing loop with retry logic, bridge health tracking, rate governing, and worker stats all interleaved. Decomposition plan exists (11 sub-methods) but was not implemented.
   - `db_manager._migrate` (CC 42) — Low risk but mechanical. 9 version-specific blocks to extract.
   - `markdown_viewer._md_to_html_regex` (CC 38) — Decision made to replace with `markdown` library. Needs output verification.

4. **Guru `create_card()` untested against live API** — The `GuruClient.create_card()` method follows the same `_request("POST", ...)` pattern as `update_card()` but hasn't been tested against the actual Guru API. The clipboard fallback ensures no data loss if the API call fails.

5. **Settings.yaml override_all** — Set to `null` (YAML null → Python None). The `get_task_routing()` function in `client_factory.py` treats None as "no override". This is correct behavior but worth noting.

6. **Cost estimator is still hardcoded** — Updated from 2.0 Flash to 2.5 Flash rates, but the pricing should ideally read from model config rather than being hardcoded. This is a minor tech-debt item for Phase 4.

---

## Files Modified (26 files)

**Source files** (14):
- `src/agents/scan_orchestrator.py` — migrated to settings_manager
- `src/data/csv_ingestion.py` — decomposed into 6 sub-functions
- `src/data/conversation_rebuild.py` — decomposed into 6 sub-functions
- `src/data/guru_client.py` — added `create_card()`
- `src/data/guru_content_pipeline.py` — new article push with API + clipboard fallback
- `src/data/report_builder.py` — decomposed into 13 section formatters
- `src/data/scan_report_builder.py` — decomposed diagnostics into 7 sub-methods
- `src/data/scan_worker.py` — system prompt fix
- `src/data/trending_engine.py` — verb bug + decomposed 2 functions into 10 sub-functions
- `src/ui/pages/conversation_search.py` — widget reference fix
- `src/ui/pages/guru_page.py` — workbench signal wiring
- `src/ui/pages/trc_analytics.py` — cost estimator + double meta-analysis guard
- `data/settings.yaml` — full restore with task routing

**Test files** (7 new, 2 modified):
- `tests/conftest.py` — NEW: shared fixtures
- `tests/unit/test_trending_engine.py` — NEW: 92 tests
- `tests/unit/test_report_builder.py` — NEW: 44 tests
- `tests/unit/test_incident_engine.py` — NEW: 43 tests
- `tests/unit/test_csv_ingestion.py` — NEW: 34 tests
- `tests/unit/test_nlp_meta_analyzer.py` — NEW: 33 tests
- `tests/unit/__init__.py` — NEW: package marker
- `tests/test_guru_pipeline.py` — fixed hardcoded dates
- `tests/test_pipeline_full.py` — fixed patch target

**Archived** (8 files → `_archive/`):
- `src/ui/pages/nlp_scanner_page.py`, 5 test files, `test_build_55.py`, debug captures
