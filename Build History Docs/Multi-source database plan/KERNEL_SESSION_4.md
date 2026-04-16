# Session 4 Kernel: Data Warehouse Page + Virtual Scroll + Widgets

## What You Are

You are building Session 4 of 5. Sessions 1-3 built the data foundation (additive imports, source registry, warehouse query, analytics migration). You are now building the Data Warehouse UI page — the user-facing window into all stored data.

## First Steps

1. Read THIS file completely
2. Read `SESSION_BUILD_LOG.md` — check ALL prior session debriefs (1, 2, 3). Key things to look for:
   - Session 2: How sidebar named lookup works (you need to add a page to it)
   - Session 3: Which analytics pages have source selectors already (don't duplicate)
   - Session 3: Whether `source_selector.py` widget was created as standalone or inline
   - Any organic adaptations that affect UI patterns
3. Read `Stage 3 - UI Updates Multi-Source and Data Warehouse.md` — your detailed plan
4. Open the HTML mockup for visual reference: `New_Persistent Database Design\Alma_Insights_UI_Mockup_Reference_Sheet (1).html` — mockups 3.1, 3.2 are your primary targets
5. Run `python -m pytest tests/ -x -q` to verify Session 3 baseline holds
6. Then start building

## What Sessions 1-3 Built (Expected State)

- **Session 1**: Additive imports, dedupe, updated clear_session, Settings reset
- **Session 2**: source_registry, warehouse_query, per-source tables, sidebar named lookup, Settings source management
- **Session 3**: All analytics query warehouse, redaction engine, source selectors on analytics pages, FTS routed through warehouse_query

**Critical dependency**: `warehouse_query.py` should have a paginated method by now (or you need to add one):
```python
def get_conversations_paged(self, offset=0, limit=100, **filters):
    """Fetch a page of conversations for virtual scroll."""
```
If Session 3 didn't add this, add it now — the virtual scroll model needs it.

## What You're Building

### New UI Files (4 files, ~1,370 LOC)

| File | LOC | Purpose |
|------|-----|---------|
| `src/ui/pages/data_warehouse_page.py` | ~500 | Full page: filter bar, virtual scroll table, ticket detail panel, TRC history panel |
| `src/ui/widgets/virtual_scroll_table.py` | ~300 | `QAbstractTableModel` with lazy loading via `fetchMore()`, PAGE_SIZE=100 |
| `src/ui/widgets/ticket_detail_panel.py` | ~250 | Tabbed detail view: Overview, NLP Data, Timeline, Related |
| `src/ui/widgets/trc_history_panel.py` | ~200 | Volume sparkline, top issues bar chart, ngram trends, related TRCs |

If `source_selector.py` doesn't exist yet (Session 3 may have created it), also create:
| `src/ui/widgets/source_selector.py` | ~120 | Reusable `QComboBox` with source_changed signal |

### Files to Modify (3 files, ~80 LOC delta)

| File | What to Change |
|------|---------------|
| `src/ui/main_window.py` | Add Data Warehouse page to sidebar using named lookup (NOT index). Add to page stack. |
| `src/data/warehouse_query.py` | Add `get_conversations_paged()` if not present. Add `get_trc_history()` for TRC history panel. |
| `src/ui/pages/guru_page.py` | Add source selector to Gap Analysis tab (if not done in Session 3) |

### Tests to Write (~670 LOC)

| Test File | What It Covers |
|-----------|---------------|
| `tests/unit/test_data_warehouse_page.py` | Page init, filter application, ticket selection, empty state |
| `tests/unit/test_virtual_scroll.py` | Lazy loading, fetchMore, no duplicate rows, page boundaries |
| `tests/unit/test_source_selector.py` | Widget state, source switching, signal emission (if creating new) |
| `tests/unit/test_trc_history_panel.py` | Data fetching, trend rendering |

## Critical Rules

1. **Sidebar: Use named lookup.** Session 2 refactored from `setCurrentIndex(n)` to named pages. Add Data Warehouse using the same pattern. Do NOT use a numeric index.
2. **Virtual scroll MUST be lazy.** Do not load all 50,000 rows into memory. `fetchMore()` loads PAGE_SIZE=100 rows on demand. `canFetchMore()` returns True until all rows fetched.
3. **data_warehouse_page.py should be ~500 LOC max.** Split heavy logic into the widget files (virtual_scroll_table, ticket_detail_panel, trc_history_panel). The page file is assembly/wiring only.
4. **Follow existing UI patterns.** Check how `trc_analytics.py` or `incidents_page.py` structure their tabs and filter bars. Match the existing `AnalysisPageBase` pattern if applicable, or use `PageHeader` + filter bar + content area.
5. **Design tokens**: Use `ALMA_GREEN_DARK`, `ALMA_CREAM`, `ALMA_WHITE` from `theme.py`. Match the HTML mockup colors exactly.
6. **TRC history panel**: Volume sparkline can be a simple text-based chart (Unicode block elements) or a QChart. Keep it lightweight. The HTML mockup uses `▁▂▃▅▇▅▃▂▁` — a text approach is fine for v1.

## HTML Mockup Reference

The Data Warehouse page mockup (3.1) shows these states:
- **Populated + detail expanded**: Full filter bar, table with 4 visible rows, ticket detail panel with tabs, TRC history with bar charts and ngram trends
- **Empty state**: Warehouse icon + "No data in warehouse" message
- **Filtered state**: TRC filter applied, showing "342 of 2,847 tickets"

Match the layout, spacing, and component structure from these mockups.

## When You're Done

**MANDATORY**: Append a full debrief to `SESSION_BUILD_LOG.md` covering:
- Every file created/modified with line counts
- How the sidebar integration was done (named lookup details)
- Virtual scroll performance: how many rows before UI lag?
- Any mockup deviations and why
- Regression results
- Organic adaptations (UI patterns established for future widgets)
- Updated file tree
- State for Session 5 (what the warehouse page can do, what's missing, any limitations)

## Verification Checklist

- [ ] Data Warehouse page accessible from sidebar
- [ ] All sidebar navigation still works (no wrong-page bugs)
- [ ] Virtual scroll loads first 100 rows immediately
- [ ] Scrolling loads more rows (fetchMore triggers)
- [ ] All filters work: date, TRC, client_id, provider_id, source, keyword
- [ ] Clicking a ticket row populates the detail panel
- [ ] TRC history panel shows volume, top issues, ngram trends
- [ ] Empty state displays when warehouse has no data
- [ ] Filtered state shows "X of Y tickets"
- [ ] `python -m pytest tests/ -x -q` — all tests pass
