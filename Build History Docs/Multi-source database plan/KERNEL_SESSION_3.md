# Session 3 Kernel: Analytics Migration + Redaction Engine

## What You Are

You are building Session 3 of 5. This is the HIGHEST RISK session. You are migrating 59 `FROM conversations` query sites across 20+ files to use `warehouse_query.py`, building the entity-aware redaction engine, and making all analytics pages always-on (no dependency on loaded conversations).

## First Steps

1. Read THIS file completely
2. Read `SESSION_BUILD_LOG.md` — check Session 1 AND Session 2 debriefs. Pay special attention to:
   - How `warehouse_query.py` was implemented (its exact API surface)
   - Whether migration 012 succeeded (row counts)
   - Any organic adaptations from Sessions 1-2
   - Sidebar refactor details (how page lookup now works)
3. Read `Stage 2 - Evolved Data Model and Always-On Analytics.md` — redaction engine and analytics sections
4. Run `python -m pytest tests/ -x -q` to verify Session 2 baseline holds
5. **Before writing ANY code**: grep for all `FROM conversations` to get the current migration target list. The number may have changed from the pre-build 59 if Sessions 1-2 added/removed queries.

## What Sessions 1-2 Built (Expected State)

**Session 1**: Additive imports, dedupe gate, updated clear_session, Settings reset button
**Session 2**: source_registry, schema_builder, warehouse_query, migrations 011-013, sidebar named lookup, ticket_index source_id column, provider_id/client_id columns, Data Sources section in Settings

**Critical dependency**: `warehouse_query.py` must exist with these methods:
- `get_conversations(source_id=None, date_start=None, date_end=None, trc_filter=None)`
- `get_ticket_count(source_id=None)`
- `get_trc_distribution(source_id=None, date_start=None, date_end=None)`
- `get_full_threads(ticket_ids, source_id=None)`
- `search_fts(query, source_id=None)`

If the API differs from this, adapt your migration accordingly.

## What You're Building

### Part A: Redaction Engine (2 new files, ~250 LOC)

| File | LOC | Purpose |
|------|-----|---------|
| `src/data/redaction_engine.py` | ~200 | Entity-aware PHI scrubbing — removes names, email, DOB, SSN, CC; preserves insurance names, client IDs, TRC codes |
| `config/entities/phi_allowlist.json` | ~50 | Insurance names (UHC, Oscar, BCBS, etc.), business acronyms, preserve patterns |

Also refine `config/redaction_patterns.json` — make patterns less aggressive.

### Part B: The 59-Query Migration (~510 LOC delta across 16+ files)

**APPROACH: One file at a time. Test after each file. Do NOT batch.**

Migration order (by risk, lowest first):

| Priority | File | Queries | Risk | Notes |
|----------|------|---------|------|-------|
| 1 | `product_gap_engine.py` | 1 | LOW | Simple, isolated |
| 2 | `smart_pipeline.py` | 1 | LOW | Simple, isolated |
| 3 | `theta_engine.py` | 3 | LOW | Statistical engine, well-tested |
| 4 | `incident_engine.py` | 3 | LOW | Poisson baselines, well-tested |
| 5 | `report_builder.py` | 3 | MEDIUM | Evidence extraction for reports |
| 6 | `voc_builder.py` | 1 | MEDIUM | Large file (2,282 LOC), critical pipeline |
| 7 | `trending_engine.py` | 6 | MEDIUM | Large file (2,071 LOC), most queries |
| 8 | `chat_tools/fast_path.py` | 2 | MEDIUM | Includes FTS fallback |
| 9 | `chat_tools/thread_tools.py` | 2 | MEDIUM | Thread context injection |
| 10 | `chat_tools/semantic_tools.py` | 1 | MEDIUM | Semantic search |
| 11 | `db_manager.py` | 14 | HIGH | 2,421 LOC, central to everything |
| 12 | `scan_orchestrator.py` | 4 | HIGH | 2,268 LOC, most critical pipeline |
| 13 | `conversation_search.py` | FTS | HIGH | User-facing search, FTS routing |
| 14 | Other files | remaining | VARIES | Check grep results for any missed |

**Pattern for each file:**
```python
# OLD:
rows = conn.execute("SELECT full_thread FROM conversations WHERE ...").fetchall()

# NEW:
from src.data.warehouse_query import WarehouseQuery
wq = WarehouseQuery(conn, source_registry)
rows = wq.get_conversations(source_id=source_id, date_start=start, date_end=end)
```

### Part C: Always-On Analytics (~60 LOC delta across 4 pages)

Remove "no conversations loaded" checks from:
- `incidents_page.py` — replace with warehouse data check
- `trending_topics.py` — replace with warehouse data check
- `ai_reports.py` — replace with warehouse data check
- `smart_reporting.py` — add source selector to filter bar

### Part D: Source Selector Widget on Analytics Pages (~80 LOC)

Add the `SourceSelector` dropdown (built in Session 2 settings, or create as reusable widget now) to each analytics page's filter bar. If Session 2 didn't create a standalone widget, create `src/ui/widgets/source_selector.py` (~120 LOC) now.

### Tests to Write (~450 LOC)

| Test File | What It Covers |
|-----------|---------------|
| `tests/unit/test_redaction_engine.py` | PHI removal, allowlist preservation, edge cases (Oscar as insurance vs name) |
| `tests/integration/test_always_on_analytics.py` | Analytics engines query warehouse without loaded conversations |

## Critical Rules

1. **ONE FILE AT A TIME.** Migrate a file, run `python -m pytest tests/ -x -q`, fix any failures, then move to the next file. Do NOT batch migrations.
2. **Do NOT change the return shape** of warehouse_query responses. Analytics engines expect specific column names. If you find a mismatch, fix warehouse_query, not the analytics engine.
3. **db_manager.py has 14 queries** — some are called by UI, some by analytics, some by tests. Map each query's callers before changing it.
4. **scan_orchestrator.py is sacred** — 2,268 lines, most complex file. Change ONLY the 4 SELECT queries. Touch nothing else.
5. **Redaction: when in doubt, DON'T redact.** False positives (stripping business data) are worse than false negatives (leaving a person name). The allowlist is the safety net.
6. **FTS migration**: `conversations_fts` queries must route to per-source FTS tables or a unified FTS. Check what Session 2 built for FTS handling.

## When You're Done

**MANDATORY**: Append a full debrief to `SESSION_BUILD_LOG.md` covering:
- Every file migrated with query count
- Total `FROM conversations` queries remaining (should be 0 in production code, some may remain in tests)
- Redaction engine accuracy: any false positives/negatives found during testing
- Regression results (test count before/after — expect some test updates needed)
- Organic adaptations (query patterns that didn't fit the simple migration pattern)
- Updated file tree
- State for Session 4 (which analytics pages have source selectors, FTS status, any unmigrated queries)

## Verification Checklist

- [ ] `grep -r "FROM conversations" src/` returns 0 hits (or only deprecated table references)
- [ ] `grep -r "FROM tickets" src/` — only in warehouse_query.py and deprecated references
- [ ] Incident engine runs against warehouse without loaded conversations
- [ ] Trending engine runs against warehouse without loaded conversations
- [ ] VOC builder samples from warehouse
- [ ] Conversation search FTS works against per-source FTS tables
- [ ] Redaction engine preserves "UHC", "Oscar", "Blue Cross Blue Shield"
- [ ] Redaction engine removes person names, emails, DOB, SSN
- [ ] Source selector appears on analytics page filter bars
- [ ] `python -m pytest tests/ -x -q` — all tests pass
