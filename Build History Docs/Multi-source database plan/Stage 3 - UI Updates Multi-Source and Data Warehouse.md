# Stage 3: UI Updates, Multi-Source, and Data Warehouse

## Overview

**Goal**: Build the Data Warehouse UI page, enable multi-source intake (Zendesk + Kodif + future), add source selection across all analytics/report pages, enable combined OR separate analysis per source, point AI Reports and Gemini chats at specific data structures, make Guru source-aware, and provide full lookback into ticket/TRC history.

**Prerequisite**: Stage 1 (additive imports) and Stage 2 (source registry, warehouse query, always-on analytics) complete.

**Scope**: New UI page, multi-source plumbing, cross-source analytics, Guru source awareness, historical lookback.

---

## File Tree

```
src/
├── data/
│   ├── source_registry.py            MOD   (~50 LOC Δ)  — Kodif source type, source-type templates
│   ├── warehouse_query.py            MOD   (~80 LOC Δ)  — Cross-source union queries, combined analytics
│   ├── csv_ingestion.py              MOD   (~30 LOC Δ)  — Kodif column mapping support
│   ├── conversation_rebuild.py       MOD   (~20 LOC Δ)  — Self-contained conversation handling (no rebuild needed)
│   ├── voc_builder.py                MOD   (~30 LOC Δ)  — Source-scoped VOC, combined mode
│   ├── trending_engine.py            MOD   (~20 LOC Δ)  — Source-scoped trending
│   ├── incident_engine.py            MOD   (~20 LOC Δ)  — Source-scoped incidents
│   ├── smart_pipeline.py             MOD   (~20 LOC Δ)  — Source-scoped pipeline runs
│   ├── guru_friction_pipeline.py     MOD   (~30 LOC Δ)  — Source-aware friction scoring
│   └── guru_content_pipeline.py      MOD   (~20 LOC Δ)  — Source-aware content matching
├── agents/
│   └── scan_orchestrator.py          MOD   (~30 LOC Δ)  — Source-scoped scan batching
├── ui/
│   ├── pages/
│   │   ├── data_warehouse_page.py    NEW   (~500 LOC)   — Virtual-scroll ticket browser, filters, TRC history
│   │   ├── conversation_search.py    MOD   (~60 LOC Δ)  — Source type selector on import, Kodif support
│   │   ├── incidents_page.py         MOD   (~30 LOC Δ)  — Source selector dropdown, combined mode toggle
│   │   ├── trending_topics.py        MOD   (~30 LOC Δ)  — Source selector dropdown, combined mode toggle
│   │   ├── ai_reports.py             MOD   (~40 LOC Δ)  — Source selector, AI reports scoped per source
│   │   ├── smart_reporting.py        MOD   (~30 LOC Δ)  — Source selector for pipeline runs
│   │   ├── guru_page.py              MOD   (~30 LOC Δ)  — Source-aware friction analysis, dataset selector
│   │   └── settings_page.py          MOD   (~20 LOC Δ)  — Source management refinements
│   ├── widgets/
│   │   ├── source_selector.py        NEW   (~120 LOC)   — Reusable source dropdown widget
│   │   ├── virtual_scroll_table.py   NEW   (~300 LOC)   — High-performance virtual scroll for warehouse
│   │   ├── trc_history_panel.py      NEW   (~200 LOC)   — TRC timeline, ngram trends, topic evolution
│   │   └── ticket_detail_panel.py    NEW   (~250 LOC)   — Single-ticket detail view (enrichments, NLP, timeline)
│   ├── main_window.py                MOD   (~20 LOC Δ)  — Add Data Warehouse page to sidebar (position 5)
│   └── dialogs/
│       └── source_config_dialog.py   MOD   (~40 LOC Δ)  — Kodif source template, column preview
├── services/
│   └── chat_tools/
│       ├── fast_path.py              MOD   (~15 LOC Δ)  — Source-aware ticket lookup
│       ├── semantic_tools.py         MOD   (~15 LOC Δ)  — Source-aware search
│       └── thread_tools.py           MOD   (~15 LOC Δ)  — Source-aware thread injection
tests/
├── unit/
│   ├── test_data_warehouse_page.py   NEW   (~300 LOC)   — Virtual scroll, filtering, TRC history
│   ├── test_source_selector.py       NEW   (~100 LOC)   — Widget state, source switching, signal emission
│   ├── test_virtual_scroll.py        NEW   (~150 LOC)   — Scroll performance, row rendering, lazy loading
│   └── test_trc_history_panel.py     NEW   (~120 LOC)   — Timeline rendering, ngram display
├── integration/
│   ├── test_multi_source_analytics.py NEW  (~250 LOC)   — Per-source AND combined analytics
│   ├── test_kodif_import.py          NEW   (~150 LOC)   — Kodif column mapping, self-contained conversations
│   ├── test_guru_source_aware.py     NEW   (~120 LOC)   — Friction per source, combined friction
│   └── test_chat_source_aware.py     NEW   (~100 LOC)   — Chat tools query correct source
├── regression/
│   └── test_stage3_regression.py     NEW   (~100 LOC)   — Full suite passes after Stage 3
└── debug/
    └── conftest.py                   MOD   (~20 LOC Δ)  — Warehouse page fixtures, virtual scroll helpers
```

**Total**: 4 new UI/widget files (~1,370 LOC), 1 new reusable widget (~120 LOC), 19 modified files (~710 LOC Δ), 7 new test files (~1,290 LOC)

---

## Danger Zones

| Area | Risk | Mitigation |
|------|------|------------|
| **main_window.py sidebar** | Adding page at position 5 shifts Settings from index 5 to index 6. All sidebar navigation indices are hardcoded. | Audit ALL `setCurrentIndex()` calls. The sidebar uses `self.page_stack.setCurrentIndex(n)`. Add Data Warehouse at index 5, shift Settings to 6. |
| **Virtual scroll performance** | Warehouse could have 50,000+ rows. QTableView with full model will hang the UI thread. | Use `QAbstractTableModel` with lazy loading. Only fetch visible rows + buffer. Page size = 100 rows. Virtual scroll fetches on-demand via `fetchMore()`. |
| **Combined cross-source analytics** | Unioning Zendesk + Kodif tickets in one analysis could mix incompatible TRC taxonomies. | Combined mode is OPT-IN. Default is per-source. Combined results show source_type as a dimension. User must explicitly choose "All Sources." |
| **Guru friction per source** | Guru currently scores friction globally. Per-source scoring could fragment the friction landscape. | Guru defaults to combined view (it's looking at KB gaps, not ticket source). Add optional source filter for "show friction from Zendesk only." |
| **Chat tools source scope** | Chat tools (`fast_path.py`, `semantic_tools.py`) currently query `conversations` directly. Must route to warehouse_query. | Same pattern as analytics: replace direct SQL with `warehouse_query` calls. Chat context includes `source_id` when user specifies. |
| **AI Reports source targeting** | Reports are currently source-agnostic. Per-source reports need different prompts or at least different data scoping. | Add `source_id` parameter to report pipeline entry points. The LLM prompt includes source context. |
| **Kodif self-contained conversations** | Kodif data has conversation body in one column — no rebuild needed. But `conversation_rebuild.py` assumes row-per-comment. | Add `skip_rebuild` flag in source_config. If `conversation_structure = 'self_contained'`, skip rebuild, write directly to conversations table. |
| **FTS index per source** | Multiple FTS tables for search. Conversation Search must union across them for global search. | `warehouse_query.search_fts(query, source_id=None)` unions across source FTS tables. Performance: UNION is fine up to 5-10 sources. |

---

## Cyclomatic Complexity Targets

| File | Target CC | Strategy |
|------|-----------|----------|
| `data_warehouse_page.py` | CC ≤ 12 per function, ≤ 500 LOC total | Split into page + model + delegate. Heavy logic in model, page is wiring only. |
| `virtual_scroll_table.py` | CC ≤ 10 per function | `fetchMore()` is the complex function (~8 CC). Everything else is simple. |
| `trc_history_panel.py` | CC ≤ 8 per function | Data fetching separate from rendering. |
| `ticket_detail_panel.py` | CC ≤ 10 per function | Tab-based layout. Each tab's populate function is independent. |
| `source_selector.py` | CC ≤ 5 per function | Simple dropdown + signal emission. |

---

## Data Warehouse Page — Architecture

### Page Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  Data Warehouse                                    [Source: ▼]  │
│  Browse all stored data across all sources                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Filters:                                                        │
│  [Date From ▼] [Date To ▼] [TRC: All ▼] [Client ID ___]        │
│  [Provider ID ___] [Insurance ___] [Source: All ▼] [Search 🔍]  │
│                                                                  │
│  ─── Results: 2,847 tickets across 2 sources ─────────────────  │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ ID    │ Date    │ TRC     │ Subject   │ Source  │ Client  │ │
│  ├───────┼─────────┼─────────┼───────────┼─────────┼─────────┤ │
│  │ 12345 │ 03/15   │ AUTH-01 │ Prior auth│ Zendesk │ CLT-001 │ │
│  │ 12346 │ 03/15   │ BIL-03  │ Claim den │ Zendesk │ CLT-002 │ │
│  │ K-891 │ 03/14   │ AUTH-01 │ Auth help │ Kodif   │ CLT-001 │ │
│  │  ...  │  ...    │  ...    │  ...      │  ...    │  ...    │ │
│  │       │         │  (virtual scroll — 100 rows loaded)      │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ─── Ticket Detail ───────────────────────────────────────────  │
│  ┌──────────┬──────────┬──────────┬──────────┐                  │
│  │ Overview │ NLP Data │ Timeline │ Related  │                  │
│  └──────────┴──────────┴──────────┴──────────┘                  │
│  TRC: AUTH-01 | Client: CLT-001 | Provider: PRV-042             │
│  Classification: Prior Authorization Issue                       │
│  Sentiment: Negative (-0.72) | Friction: High                   │
│  Ngrams: "prior auth", "peer review", "turnaround"              │
│  Issue Types: [Authorization Delay] [Peer Review Required]       │
│                                                                  │
│  ─── TRC History: AUTH-01 ────────────────────────────────────  │
│  Volume: ▁▂▃▅▇▅▃▂▁ (last 90 days)                              │
│  Top Issues: Authorization Delay (42%), Peer Review (28%)        │
│  Ngram Trend: "prior auth" ↑15%, "turnaround" ↓8%               │
│  Related TRCs: BIL-03 (co-occurrence: 34%)                      │
└─────────────────────────────────────────────────────────────────┘
```

### Virtual Scroll Model

```python
class WarehouseTableModel(QAbstractTableModel):
    """Lazy-loading model for warehouse data.

    Fetches rows in pages of 100. Virtual scroll triggers
    fetchMore() when user scrolls near the bottom of loaded data.
    """
    PAGE_SIZE = 100

    def __init__(self, warehouse_query, filters=None):
        super().__init__()
        self.wq = warehouse_query
        self.filters = filters or {}
        self._rows = []
        self._total_count = 0
        self._fetched = 0

    def fetchMore(self, parent=QModelIndex()):
        """Load next page of rows from warehouse."""
        new_rows = self.wq.get_conversations_paged(
            offset=self._fetched,
            limit=self.PAGE_SIZE,
            **self.filters
        )
        self.beginInsertRows(parent, self._fetched, self._fetched + len(new_rows) - 1)
        self._rows.extend(new_rows)
        self._fetched += len(new_rows)
        self.endInsertRows()
```

---

## Multi-Source Analytics — Combined Mode

### How It Works

Each analytics page gets a `SourceSelector` widget with options:
- Individual sources: "Zendesk - RCM Support", "Kodif - Member Chat", etc.
- Combined: "All Sources" — unions data across all source tables

When "All Sources" is selected:
1. `warehouse_query.get_conversations(source_id=None)` returns UNION across all source tables
2. Each row includes `source_type` column
3. Analytics engines process as normal — the data shape is identical
4. Results can be faceted by source_type in the UI (e.g., stacked bar chart)

### Source Selector Widget (`source_selector.py`)

```python
class SourceSelector(QComboBox):
    """Reusable dropdown for selecting data source scope."""
    source_changed = Signal(str)  # Emits source_id or None for "All"

    def __init__(self, source_registry, include_all=True):
        super().__init__()
        if include_all:
            self.addItem("All Sources", None)
        for source in source_registry.list_sources():
            self.addItem(source.source_name, source.source_id)
        self.currentIndexChanged.connect(self._on_change)
```

Placed in every analytics page's filter bar. Consistent behavior across all pages.

---

## Guru Source Awareness

### Current Guru Flow

```
Guru cards (KB) ←→ sub_patterns (NLP findings) ←→ conversations (ticket data)
```

Friction is scored by matching sub_patterns against Guru card content. Sub_patterns come from NLP scans which come from conversations.

### Stage 3 Change

- Guru defaults to **combined view** (KB quality is about coverage, not source)
- Optional source filter: "Show friction for Zendesk tickets only"
- `guru_friction_pipeline.py` accepts `source_id` parameter
- When filtered: only sub_patterns from that source's tickets contribute to friction scores

---

## Chat Tools Source Awareness

### Current

```python
# fast_path.py
def get_ticket_detail(ticket_id):
    return conn.execute("SELECT * FROM conversations WHERE ticket_id = ?", (ticket_id,))
```

### Stage 3

```python
def get_ticket_detail(ticket_id, source_id=None):
    wq = WarehouseQuery(conn, registry)
    return wq.get_conversation(ticket_id, source_id=source_id)
```

Chat context includes the active source. If the user says "look at ticket K-891" and K-891 is a Kodif ID, the system routes to the Kodif source table.

---

## Navigation Update

### Current Sidebar (indices 0-8)

```
0: Conversation Search
1: TRC Analytics
2: Trending Topics
3: Incidents
4: Smart Reporting
5: Settings
6: AI Reports
7: Source Monitor
8: Guru
```

### New Sidebar (indices 0-9)

```
0: Conversation Search
1: TRC Analytics
2: Trending Topics
3: Incidents
4: Smart Reporting
5: DATA WAREHOUSE (NEW)    ← Insert here
6: Settings                ← Shifted from 5
7: AI Reports              ← Shifted from 6
8: Source Monitor           ← Shifted from 7
9: Guru                    ← Shifted from 8
```

**Breaking change**: All `setCurrentIndex(n)` calls for indices 5+ must be incremented by 1. Audit every file that navigates to Settings, AI Reports, Source Monitor, or Guru.

---

## Kodif Source Type

### Structural Differences from Zendesk

| Aspect | Zendesk | Kodif |
|--------|---------|-------|
| ID field | `ticket_id` | `conversation_id` or `chat_id` |
| Conversation structure | Row-per-comment, needs rebuild | Self-contained in one column |
| TRC equivalent | `trc_code` column | May use different categorization |
| Provider/Client | Inferred from requester | May have explicit fields |

### Handling in Pipeline

1. Source config defines `conversation_structure: 'self_contained'` vs `'row_per_comment'`
2. If self-contained: skip `conversation_rebuild.py`, write directly to source conversations table
3. Column mapping in source_config maps Kodif fields to internal schema (e.g., `chat_id` → `ticket_id`)

---

## Testing Strategy

### Test Structure

```
tests/
├── unit/
│   ├── test_data_warehouse_page.py   — Page initialization, filter wiring, model integration
│   ├── test_source_selector.py       — Widget creation, source switching, signal emission
│   ├── test_virtual_scroll.py        — Lazy loading, fetchMore, row count accuracy
│   └── test_trc_history_panel.py     — Data fetching, timeline rendering, ngram display
├── integration/
│   ├── test_multi_source_analytics.py — Per-source AND combined analytics correctness
│   ├── test_kodif_import.py          — Kodif CSV → source table → warehouse query
│   ├── test_guru_source_aware.py     — Friction scoring per source and combined
│   └── test_chat_source_aware.py     — Chat tools route to correct source
├── regression/
│   └── test_stage3_regression.py     — Full existing suite passes, sidebar navigation correct
└── debug/
    └── conftest.py                   — Warehouse page fixtures, virtual scroll test helpers
```

### Unit Tests — `test_data_warehouse_page.py` (~300 LOC)

```python
"""
Test Module: Data Warehouse Page
Stage: 3
Dependencies: Stage 1 + Stage 2 complete
Tests: ~20

Covers:
  - WarehouseTableModel initialization and lazy loading
  - Filter application (date, TRC, client_id, provider_id, source)
  - Ticket detail panel population
  - TRC history panel data fetching
  - Empty state handling
  - Source switching behavior

Debug:
  - Run with: python -m pytest tests/unit/test_data_warehouse_page.py -x -v
  - Qt debug: QT_LOGGING_RULES="alma.*=true" python -m pytest ...
  - Visual debug: Set ALMA_SHOW_WIDGETS=1 to render widgets during test
"""
```

| Test | What It Validates |
|------|-------------------|
| `test_page_initializes` | Page creates without error, all widgets present |
| `test_model_lazy_load` | First 100 rows loaded, total count accurate |
| `test_model_fetch_more` | Scroll triggers fetch of next 100 rows |
| `test_filter_by_date` | Date range filter reduces visible rows |
| `test_filter_by_trc` | TRC filter shows only matching tickets |
| `test_filter_by_client_id` | Client ID filter works |
| `test_filter_by_provider_id` | Provider ID filter works |
| `test_filter_by_source` | Source filter shows only selected source |
| `test_filter_combined` | Multiple filters AND together correctly |
| `test_ticket_detail_selection` | Clicking row populates detail panel |
| `test_trc_history_on_select` | Selecting ticket shows TRC history |
| `test_empty_warehouse` | Graceful empty state message |
| `test_source_selector_integration` | Source dropdown changes filter and refreshes |
| `test_virtual_scroll_performance` | 10,000 row model loads under 200ms |

### Unit Tests — `test_virtual_scroll.py` (~150 LOC)

```python
"""
Test Module: Virtual Scroll Table Widget
Stage: 3
Dependencies: None (standalone widget)
Tests: ~10

Covers:
  - QAbstractTableModel contract (rowCount, data, fetchMore, canFetchMore)
  - Lazy loading correctness (no duplicate rows, correct offsets)
  - Edge cases (empty data, single page, exact page boundary)

Debug:
  - Run with: python -m pytest tests/unit/test_virtual_scroll.py -x -v
"""
```

| Test | What It Validates |
|------|-------------------|
| `test_initial_row_count` | Returns PAGE_SIZE or total if less |
| `test_can_fetch_more` | True when unfetched rows remain |
| `test_fetch_more_appends` | New rows appended, old rows unchanged |
| `test_no_duplicate_rows` | Multiple fetchMore calls don't produce duplicates |
| `test_exact_page_boundary` | 200 rows with PAGE_SIZE=100 → exactly 2 fetches |
| `test_empty_model` | 0 rows → rowCount=0, canFetchMore=False |
| `test_single_row` | 1 row → loads immediately, no fetchMore |
| `test_column_data` | Correct column values returned for each role |
| `test_filter_reset` | Changing filters resets model to page 1 |

### Integration Tests — `test_multi_source_analytics.py` (~250 LOC)

```python
"""
Test Module: Multi-Source Analytics Integration
Stage: 3
Dependencies: Stage 1 + Stage 2 + Source Registry + Warehouse Query
Tests: ~15

Covers:
  - Per-source incident detection
  - Combined cross-source incident detection
  - Per-source trending topics
  - Combined trending topics (TRC taxonomy merge)
  - Source-scoped VOC analysis
  - Source-scoped AI reports
  - Guru friction with source filter

Debug:
  - Run with: python -m pytest tests/integration/test_multi_source_analytics.py -x -v
  - DB debug: Set ALMA_DEBUG_SQL=1 for query logging
"""
```

| Test | What It Validates |
|------|-------------------|
| `test_incidents_per_source` | Zendesk incidents separate from Kodif incidents |
| `test_incidents_combined` | Combined mode detects cross-source anomalies |
| `test_trending_per_source` | TF-IDF trends scoped to single source |
| `test_trending_combined` | Combined mode unions text from all sources |
| `test_voc_per_source` | VOC samples from single source only |
| `test_voc_combined` | VOC samples across all sources |
| `test_ai_report_per_source` | Report scoped to Zendesk only |
| `test_guru_friction_combined` | Default: friction from all sources |
| `test_guru_friction_filtered` | Filtered: friction from Zendesk only |
| `test_scan_per_source` | NLP scan batches from single source |
| `test_analytics_source_switch` | Switching source re-runs analysis correctly |
| `test_combined_trc_taxonomy` | Different TRC pools from different sources handled |

### Schema/Data Tests — `test_kodif_import.py` (~150 LOC)

```python
"""
Test Module: Kodif Source Import
Stage: 3
Dependencies: Source Registry, CSV Ingestion
Tests: ~10

Covers:
  - Kodif CSV column mapping (chat_id → ticket_id, etc.)
  - Self-contained conversation (no rebuild step)
  - Kodif tickets stored in separate table from Zendesk
  - Dedupe works across Kodif imports
  - Warehouse query returns Kodif data correctly

Debug:
  - Run with: python -m pytest tests/integration/test_kodif_import.py -x -v
"""
```

| Test | What It Validates |
|------|-------------------|
| `test_kodif_column_mapping` | chat_id mapped to ticket_id, body mapped to full_thread |
| `test_kodif_no_rebuild` | Self-contained conversations skip rebuild step |
| `test_kodif_separate_table` | Data goes to kodif_*_conversations, not zendesk_* |
| `test_kodif_dedupe` | Same Kodif CSV twice → no duplicates |
| `test_kodif_warehouse_query` | Warehouse returns Kodif data with correct source_type |
| `test_kodif_and_zendesk_coexist` | Both sources have data, queries return correct results |
| `test_kodif_fts_search` | Full-text search finds Kodif conversations |
| `test_kodif_analytics` | Incidents/trending work on Kodif source alone |

### Regression Tests — `test_stage3_regression.py` (~100 LOC)

| Test | What It Validates |
|------|-------------------|
| `test_existing_suite_passes` | All 631+ original tests + Stage 1 + Stage 2 tests pass |
| `test_sidebar_navigation_indices` | All page indices correct after Data Warehouse insertion |
| `test_settings_still_accessible` | Settings page at new index 6 |
| `test_guru_still_accessible` | Guru page at new index 9 |
| `test_conversation_search_unchanged` | Import flow unaffected |
| `test_clear_close_unchanged` | Clear & Close behavior from Stage 1 preserved |

---

## What NOT to Change (Stage 3)

| File/Area | Why |
|-----------|-----|
| `scan_orchestrator.py` pipeline ordering | Agent pipeline (classify → analyst → meta → merge) is unchanged. Only the data SOURCE changes. |
| `ticket_index_writer.py` | Source-agnostic. Writes to ticket_index regardless of source. |
| `tool_registry.py` boundary guard | NLP safety guard is source-agnostic. |
| `clear_session.py` | Already updated in Stage 1. No further changes. |
| `update_checker.py` / `updater.py` | Auto-update system is independent of data model. |
