# Build Plan: Stability & Polish

**Master Plan**: `C:\Users\Chris\.claude\plans\stability-and-polish.md`
**Last Updated**: 2026-03-23 (post-Session 2)

---

## Phase Status Overview

| Phase | Status | Sessions Used | Next Session |
|-------|--------|--------------|-------------|
| 1. Fix Broken Things | ✅ DONE | S1 | — |
| 2. Complexity Surgery | ✅ DONE (9/9 targets) | S1 + S2 | — |
| 3. Test Coverage | ✅ DONE (246 unit tests + NLP E2E) | S1 + S2 | — |
| 4. Surgical UX Polish | NOT STARTED | — | S3-S4 |
| 5. Test Restructuring | NOT STARTED | — | S5-S6 |
| 6. Prompt Improvement | NOT STARTED | — | S7 |

---

## What's Done

### Phase 1: Fix Broken Things (S1) ✅
- Settings restoration (11 sections merged, `ai.task_routing` added)
- 4 test failures → 0
- Guru feature fixes (workbench signal wiring, new article creation)
- Trending correlation verb bug
- Dead file cleanup (8 files → `_archive/`, ~165MB freed)
- Minor fixes (system prompt, cost estimator, double meta-analysis guard, widget ref)

### Phase 2: Complexity Surgery (S1 + S2) ✅

All 9 high-complexity functions decomposed. **Zero F-rated or E-rated functions remain.**

| # | File | Function | CC Before → After | Session |
|---|------|----------|-------------------|---------|
| 1 | `csv_ingestion.py` | `ingest_csv` | 61 → ~12 | S1 |
| 2 | `report_builder.py` | `format_data_block_for_prompt` | 51 → ~5 | S1 |
| 3 | `scan_orchestrator.py` | `_worker_loop` | 50 → ~12 | **S2** |
| 4 | `trending_engine.py` | `compute_topic_model` | 46 → ~10 | S1 |
| 5 | `db_manager.py` | `_migrate` | 42 → ~6 | **S2** |
| 6 | `conversation_rebuild.py` | `rebuild_conversations` | 39 → ~8 | S1 |
| 7 | `markdown_viewer.py` | `_md_to_html_regex` | 38 → deleted | **S2** |
| 8 | `trending_engine.py` | `test_hypothesis` | 33 → ~8 | S1 |
| 9 | `scan_report_builder.py` | `_build_diagnostics_section` | 32 → ~3 | S1 |

### Phase 3: Test Coverage (S1 + S2) ✅
- `tests/conftest.py` with 8 shared fixtures (S1)
- 246 new unit tests across 5 modules (S1)
- `tests/test_nlp_full_e2e.py` — full NLP pipeline E2E test, 888 tickets, 3 workers (S2)
- Directory structure: `tests/unit/`, `tests/integration/`, `tests/ui/`, `tests/e2e/`

---

## What's Left — Prioritized

### Session 3: UX Polish — TRC Analytics + Trending/Incidents

**4A — TRC Analytics / NLP Scanner polish (2 hours)**:
- KPI trend deltas (↑/↓ + delta % vs previous equal-length period) — configurable comparison
- Heatmap cell click → filters by period
- Scanner date sync with Overview date pickers
- Empty state on SubTaxonomy tab
- Scan history row click → jump to SubTaxonomy
- Fix "detached process" claim → "background thread"
- Dismissible docs panel (persist to settings)

**4B — Trending / Incidents polish (1 hour)**:
- Step progress during analysis (pipe progress_callback to skeleton label)
- Rename "Anomaly Scan" → "Content Anomalies" with subtitle
- Column header tooltips (λ, θ₁, θ₂, p-value, CUSUM plain-English)
- Move "Manage Terms" to gear icon button
- AI tools bar visible immediately with "preparing..." state

**Caveat**: Phase 4 is design-heavy. Figma mockups recommended before coding. May benefit from fresh session for context budget.

### Session 4: UX Polish — Guru + AI Reports

**4C — Guru KB polish (2 hours)**:
- Gap Analysis → QThread (prevent UI freeze)
- Collection picker dialog for new article push
- Sync progress indicator (card count during sync)
- Graph rebuild button on Connection tab
- Rename per-card "Analyze" button for clarity

**4D — AI Reports polish (1 hour)**:
- NLP synthesis → QThread
- Cancel button for standard reports
- Progress steps for standard pipeline
- Report history search (date/prompt-type filter)

### Session 5-6: Test Restructuring

**5A — Move well-scoped test files (1 hour)**:
- 22 test files that are already single-module → move to `tests/unit/`, `tests/ui/`, `tests/e2e/`
- Add `pyproject.toml` with `[tool.pytest.ini_options] pythonpath = ["."]`

**5B — Split phase-named test files (3 hours)**:
- `test_pipeline_full.py` (94 tests → 8 files)
- `test_hardening.py` (79 tests → 8 files)
- `test_phase5_regression.py` (39 tests → 4 files)
- `test_feature_integration.py` → move to `tests/integration/`
- Refactor shared helpers into `conftest.py` fixtures (already built in Phase 3A)

### Session 7: Prompt Improvement

- Fix `synthesis.txt` (rewrite as distinct "quick summary" format)
- Expand `hypothesis.txt` (add evidence rubric, confidence intervals)
- Add quantitative thresholds to `executive_summary.txt`
- Externalize inline prompts from `analyst_agent.py` and `guru_*_pipeline.py` to `config/prompts/`
- Leave `trending_engine.py` prompts inline with version tags
- Create `config/prompts/_manifest.yaml` for version tracking

---

## Installer Action Item

**`markdown` library** added in S2 — needs bundling in Express Installer:
- Pure Python, zero native deps
- PyInstaller spec needs: `collect_submodules('markdown')` or explicit `hiddenimports` for `markdown.extensions.tables`, `markdown.extensions.fenced_code`, `markdown.extensions.nl2br`, `markdown.extensions.sane_lists`
- Add to `requirements.txt` if not already present

---

## Decisions Made

| # | Decision | Choice | Session |
|---|----------|--------|---------|
| 1 | Default Gemini model | Updated to `gemini-2.5-flash` | S1 |
| 2 | Orchestrator test fix | Migrated to `settings_manager` (proper fix) | S1 |
| 3 | Workbench "Commit to Drafts" | Calls `propose_rewrite()` (new LLM pass for clean draft) | S1 |
| 4 | New article push | API create with collection picker + clipboard fallback | S1 |
| 5 | Dead file cleanup | Archived to `_archive/`, not deleted | S1 |
| 6 | Markdown parser | Replaced with `markdown` library | S1→S2 |
| 7 | Seeded test data | 100 tickets (3 TRCs × ~33, 30 days) | S1 |
| 8 | KPI trend deltas | Configurable comparison period (default: previous equal-length) | S1 |
| 9 | Incremental CUSUM | Toggle with full-rescan default (deferred to Phase 4) | S1 |
| 10 | Guru article content | Store in DB for content-based scoring (deferred to Phase 4) | S1 |
| 11 | Test import paths | Both (pyproject.toml + conftest sys.path) | S1 |
| 12 | Test helpers during split | Refactor into conftest fixtures | S1 |
| 13 | synthesis.txt | Rewrite as quick summary format | S1 |
| 14 | Inline prompts | Selective (guru+analyst external, trending version-tagged) | S1 |
| 15 | Prompt versioning | Manifest only (not embedded in prompt text) | S1 |
| 16 | _worker_loop decomposition | 7 sub-methods (not 11 — consolidated where possible) | S2 |
| 17 | _migrate helper pattern | Generic `_add_columns_if_missing()` replaces 5 PRAGMA copies | S2 |
| 18 | markdown library extensions | tables, fenced_code, nl2br, sane_lists | S2 |

---

## Success Metrics (Running Tally)

| Metric | Pre-S1 | Post-S1 | Post-S2 | Target |
|--------|--------|---------|---------|--------|
| Test failures | 4 | 0 | 0 | 0 |
| Total tests | ~600 | ~850 | ~850 | ~1,050+ |
| F-rated functions (CC 41+) | 5 | 2 | **0** ✅ | 0 |
| E-rated functions (CC 31-40) | 4 | 1 | **0** ✅ | 0 |
| Zero-coverage critical modules | 5 | 0 | 0 | 0 |
| Broken features | 3 | 0 | 0 | 0 |
| Dead code (archived) | ~4,500 lines | 0 active | 0 active | 0 |
| E2E pipeline tests | 1 (VOC) | 1 (VOC) | **2** (VOC + NLP) | 2+ |
| Test files by module | 43% | ~55% | ~55% | 90%+ |
