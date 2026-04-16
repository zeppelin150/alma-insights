# Session 5 Kernel: Multi-Source + Combined Analytics + Guru/Chat Source Awareness

## What You Are

You are building Session 5 of 5 — the final session. Sessions 1-4 built additive imports, source registry, warehouse query, analytics migration, redaction engine, and the Data Warehouse UI page. You are now adding multi-source support (Kodif), combined cross-source analytics, and source awareness for Guru and Chat tools.

## First Steps

1. Read THIS file completely
2. Read `SESSION_BUILD_LOG.md` — check ALL prior session debriefs (1-4). Key things:
   - Session 2: source_registry API (how sources are created, column mapping format)
   - Session 3: warehouse_query API (how queries are routed, FTS union approach)
   - Session 3: redaction engine API (how scrub() is called during ingestion)
   - Session 4: Data Warehouse page capabilities (what filters exist, how source_selector works)
   - ALL sessions: organic adaptations that affect your work
3. Read `Stage 3 - UI Updates Multi-Source and Data Warehouse.md` — Kodif, combined mode, Guru/Chat sections
4. Run `python -m pytest tests/ -x -q` to verify Session 4 baseline holds
5. Then start building

## What Sessions 1-4 Built (Expected State)

- **Session 1**: Additive imports, dedupe gate
- **Session 2**: Source registry, per-source tables, warehouse_query, sidebar named lookup
- **Session 3**: All 59 query sites migrated to warehouse_query, redaction engine, always-on analytics, source selectors on analytics pages
- **Session 4**: Data Warehouse page with virtual scroll, ticket detail panel, TRC history panel, source selector widget

## What You're Building

### Part A: Kodif Source Type (~100 LOC modifications)

| File | What to Change |
|------|---------------|
| `src/data/source_registry.py` | Add Kodif source type template with default column mapping |
| `src/data/csv_ingestion.py` | Handle Kodif column mapping (chat_id → ticket_id, etc.) |
| `src/data/conversation_rebuild.py` | Add `skip_rebuild` flag — Kodif conversations are self-contained in one column, no rebuild needed |
| `src/ui/dialogs/source_config_dialog.py` | Add Kodif option to source type dropdown, show appropriate defaults |

**Kodif structural differences from Zendesk:**
- ID field: `conversation_id` or `chat_id` (not `ticket_id`)
- Conversations: self-contained in one column (not row-per-comment)
- TRC equivalent: may use different categorization field
- Column mapping stored in source_config defines the transformation

### Part B: Combined Cross-Source Analytics (~150 LOC modifications)

| File | What to Change |
|------|---------------|
| `src/data/warehouse_query.py` | Ensure `get_conversations(source_id=None)` properly unions across all source tables. Add `source_type` column to results when in combined mode. |
| `src/ui/pages/incidents_page.py` | Add Per-Source / Combined toggle to filter bar |
| `src/ui/pages/trending_topics.py` | Add Per-Source / Combined toggle to filter bar |
| `src/ui/pages/ai_reports.py` | Source selector for report scope |
| `src/ui/pages/smart_reporting.py` | Source selector for pipeline runs |

**Combined mode behavior:**
- When "All Sources" + "Combined" selected: warehouse_query unions ALL source tables
- Results include `source_type` column for faceting
- Charts show stacked bars by source
- When "Per-Source" selected: only the selected source's data is analyzed

### Part C: Source-Aware Guru (~50 LOC modifications)

| File | What to Change |
|------|---------------|
| `src/data/guru_friction_pipeline.py` | Add optional `source_id` parameter — filter sub_patterns by source when scoring friction |
| `src/data/guru_content_pipeline.py` | Add optional `source_id` parameter — scope content matching to source |
| `src/ui/pages/guru_page.py` | Source selector on Gap Analysis tab (if not added in Session 4) |

**Default**: Guru analyzes ALL sources combined (KB quality is about coverage, not source). Source filter is optional.

### Part D: Source-Aware Chat Tools (~45 LOC modifications)

| File | What to Change |
|------|---------------|
| `src/data/chat_tools/fast_path.py` | Add `source_id` parameter to ticket lookups |
| `src/data/chat_tools/semantic_tools.py` | Add `source_id` parameter to search |
| `src/data/chat_tools/thread_tools.py` | Add `source_id` parameter to thread context |

Chat context includes source_id when the user specifies a source. If not specified, searches all sources.

### Part E: Conversation Search Multi-Source (~60 LOC modifications)

| File | What to Change |
|------|---------------|
| `src/ui/pages/conversation_search.py` | Add "Importing to:" source selector dropdown above filter bar. Show registered sources + "+ Create New Source..." option. |

### Tests to Write (~620 LOC)

| Test File | What It Covers |
|-----------|---------------|
| `tests/integration/test_multi_source_analytics.py` | Per-source AND combined analytics correctness |
| `tests/integration/test_kodif_import.py` | Kodif CSV → source table → warehouse query (skip rebuild) |
| `tests/integration/test_guru_source_aware.py` | Friction per source and combined |
| `tests/integration/test_chat_source_aware.py` | Chat tools route to correct source |
| `tests/regression/test_stage3_regression.py` | Full suite passes, sidebar navigation, all features work |

## Critical Rules

1. **Kodif skip_rebuild must be bulletproof.** If the flag doesn't properly bypass conversation_rebuild.py, Kodif data gets mangled through the Zendesk rebuild logic (grouping by ticket_id, sorting events — none of which applies to self-contained chats).
2. **Combined mode TRC taxonomy.** Zendesk and Kodif may have different TRC pools. Combined analytics should NOT merge incompatible TRC codes. Show them as separate dimensions.
3. **Combined mode is OPT-IN.** Default analytics view is per-source. User explicitly selects "Combined" mode.
4. **Guru defaults to combined.** Unlike analytics, Guru's KB quality assessment benefits from seeing ALL ticket friction regardless of source. The source filter is secondary.
5. **Chat tools fallback.** If `source_id` is not provided, search ALL sources. Never crash on missing source_id.
6. **This is the FINAL session.** Run a comprehensive regression. Every feature should work end-to-end.

## Final Regression Checklist

Run this full walkthrough after implementation:

1. Import Zendesk CSV → verify additive, dedupe works
2. Create Kodif source in Settings → import Kodif CSV → verify separate table
3. Data Warehouse page → filter by source → verify both sources visible
4. Incidents page → Per-Source mode (Zendesk) → run analysis → verify results
5. Incidents page → Combined mode → run analysis → verify stacked results
6. Trending page → same per-source/combined test
7. AI Reports → scope to Zendesk → run report → verify
8. Guru → Gap Analysis → "All Sources" → analyze friction
9. Guru → Gap Analysis → filter to "Zendesk only" → verify scoped results
10. Gemini Chat → ask about a ticket → verify it finds the right source
11. Close app → "Clear & Close" → reopen → all data preserved
12. Settings → Full Database Reset → confirm → all data gone

## When You're Done

**MANDATORY**: Append a FINAL debrief to `SESSION_BUILD_LOG.md` covering:
- Everything built across all 5 sessions (cumulative summary)
- Final test count
- Full file tree with all new/modified files and LOC
- Any remaining tech debt or known issues
- Performance observations (virtual scroll with real data, query speed)
- Recommendations for future work

## Verification Checklist

- [ ] Kodif CSV imports to separate source table (no rebuild step)
- [ ] Combined mode unions analytics across sources correctly
- [ ] Per-Source / Combined toggle works on Incidents, Trending, AI Reports
- [ ] Guru friction scoped by source when filtered, combined by default
- [ ] Chat tools route to correct source table
- [ ] Conversation Search has "Importing to:" source selector
- [ ] Full regression walkthrough passes (12 steps above)
- [ ] `python -m pytest tests/ -x -q` — all tests pass
- [ ] No grep hits for `FROM conversations` in production code (only deprecated/test references)
