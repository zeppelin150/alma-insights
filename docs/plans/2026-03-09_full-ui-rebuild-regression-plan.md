# Alma Insights — Full UI Rebuild & Regression Plan

## Status

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Living Audit Deliverable | DONE |
| 2 | TRC Analytics Rebuild | DONE |
| 2.5 | NLP Scanner Migration | DONE |
| 3 | Trending Topics v2 Migration | DONE |
| 4 | Incidents v2 Migration | DONE |
| 5 | Cross-Page Regression & Consolidation | DONE |
| 5.1 | Sidebar Collapse Fix | DONE |
| 5.2 | UI E2E Tests: NLP Scanner + AI Reports | DONE |
| 5.3 | Analyst Agent Hardening (Reports UI + Full Novelty + Pipeline Reorder) | DONE |
| 5.4 | Classification Pipeline Hardening (Boundary Guard + Partial Parse Recovery) | DONE |
| 5.4b | Scan Pipeline Bug Fixes (Progress Counter + Date Filter) | DONE |
| 5.5A | Reporting Foundation (Markdown Renderer + Tech Summary + Schedule Schema) | DONE |
| 5.5B | AI Reports & A/B Compare Pipeline Overhaul | DONE |
| 5.5C | Smart Reporting & Scheduling Overhaul | DONE |
| 5.5D | Polish, Integration & Testing | DONE |

## Context

Gate 1 v2 mockups are approved (green-cell tabs, action-first filter bar, accent KPIs, no pagination) and pushed to Figma. The full analytics suite needs rebuilding to match these designs across **13 tabs** spanning 3 pages, then wired to real data engines, functionally tested, and visually verified via the OCR module. The codebase is ~50k lines across 100+ Python files — too large for a single session. This plan defines a **5-phase, multi-session approach** with a transferable living audit document produced in Phase 1 so codebase discovery only happens once.

---

## Phase 1 — Living Audit Deliverable (DONE)

**Goal**: Produce two files that become the transferable context for all future sessions.

### Deliverable A: `ocr_debug/audit/alma_audit.jsonl`
Machine-readable, one JSON object per line. Schema:

```jsonl
{"type":"meta","version":"1.0","generated":"2026-02-28","total_files":N,"total_lines":N}
{"type":"file","path":"src/ui/pages/trc_analytics_page.py","category":"ui/page","lines":580,"classes":["TRCAnalyticsPage"],"base":"AnalysisPageBase","tabs":["Overview","NLP Scanner","CSAT Heatmaps","SubTaxonomy","Reports"],"signals":["deep_dive_requested","scan_active_changed"],"imports":["SharedFilterBar","TabScrollContent","PageHeader"],"data_engine":"src/data/trc_analytics.py","description":"Main TRC Analytics page with 5 tabs, filter bar, KPI strip, charts, and data tables."}
{"type":"file","path":"src/data/trc_analytics.py","category":"data/engine","lines":420,"classes":["TRCAnalyticsEngine"],"methods":["get_volume_by_trc","get_resolution_dist","get_csat_heatmap","get_subtaxonomy_tree"],"db_tables":["trc_codes","tickets","csat_scores"],"description":"..."}
{"type":"signal_map","source":"MainWindow.data_loaded","targets":["TRCAnalyticsPage.refresh","TrendingTopicsPage.refresh","IncidentsPage.refresh"]}
{"type":"design_token","name":"ALMA_GREEN_DARK","value":"#03281B","usage":"primary buttons, active tabs, dark accents"}
{"type":"architecture","layer":"ui","pattern":"AnalysisPageBase → PageHeader + SharedFilterBar + QTabWidget(TabScrollContent per tab)","description":"..."}
```

Categories: `meta`, `file`, `widget`, `dialog`, `signal_map`, `design_token`, `architecture`, `test`, `config`, `pipeline_gate`.

### Deliverable B: `ocr_debug/audit/alma_audit.md`
Human-readable Markdown with:

1. **Architecture Overview** — layered diagram (UI → Data → Agents → Server)
2. **Design System** — all tokens from `src/ui/theme.py` with hex values and usage
3. **Page Inventory** — per page: tabs, widgets used, data engine, signals, current state
4. **Widget Catalog** — every reusable widget with constructor signature and screenshot ref
5. **Data Layer Map** — engines, DB tables, query methods
6. **Agent Pipeline** — orchestrator → workers → Gemini bridge flow
7. **Test Coverage** — test files, what they cover, how to run
8. **OCR Pipeline** — 5-gate flow with file paths and gate descriptions
9. **File Index** — alphabetical with one-line descriptions

### Files to read and document (critical paths):

**UI Pages** (10 pages, `src/ui/pages/`):
- `trc_analytics_page.py` — 5 tabs, primary analytics
- `trending_topics_page.py` — 4 tabs, TF-IDF/sentiment
- `incidents_page.py` — 4 tabs, anomaly/SPC
- `dashboard_page.py` — landing/summary
- `settings_page.py` — app config
- `import_page.py` — data import wizard
- `agent_scan_page.py` — NLP scan control
- `voc_pipeline_page.py` — voice of customer
- `report_builder_page.py` — export/reporting
- `help_page.py` — docs/about

**UI Widgets** (33 widgets, `src/ui/widgets/`):
- `shared_filter_bar.py` — reusable filter bar (date, TRC, status)
- `page_header.py` — title + subtitle
- `tab_scroll_content.py` — scrollable tab container
- `kpi_strip.py` — horizontal KPI card row
- `chart_card.py` — chart container with header badge
- `data_table.py` — sortable/filterable table
- `status_badge.py` — colored status pill
- `trend_indicator.py` — up/down/flat micro-indicator
- Plus ~25 more specialized widgets

**UI Dialogs** (7 dialogs, `src/ui/dialogs/`):
- Deep dive, export, scan config, filter builder, etc.

**Data Engines** (`src/data/`):
- `trc_analytics.py`, `trending_engine.py`, `incident_engine.py`
- `theta_engine.py` (SPC), `db_manager.py` (SQLite 32 tables)
- `data_loader.py`, `cache_manager.py`

**Agents** (`src/agents/`):
- `scan_orchestrator.py`, `worker_agent.py`, `gemini_bridge.py`
- `supervisor.py`, `rate_governor.py`, `batch_packer.py`

**Theme/Design** (`src/ui/`):
- `theme.py` — all design tokens, QSS stylesheet, chart palette
- `main_window.py` — navigation, page stack, signal wiring

**OCR Pipeline** (`ocr_debug/`):
- `mockup.py` (Gate 1), `wireframe.py` (Gate 2)
- `design_registry.py` (Build Spec), `visual_inspect.py` (Gates 3+5)

**Tests** (`tests/`):
- 15+ test files: unit, integration, Qt regression, stress, VOC

### Execution approach:
1. Read every file listed above (parallel where possible)
2. Extract structured metadata per file into JSONL records
3. Map signal chains and data flows across layers
4. Write MD document with architecture narratives
5. Validate by cross-referencing imports and signal connections

### Verification:
- `python -c "import json; [json.loads(l) for l in open('ocr_debug/audit/alma_audit.jsonl')]"` — JSONL parses cleanly
- MD renders correctly (check headers, code blocks, links)
- Spot-check 5 random file entries against actual source

---

## Phase 2 — TRC Analytics Rebuild (DONE)

**Scope**: 5 tabs × full widget rebuild + data wiring

| Tab | Key Widgets | Data Engine Method | Status |
|-----|-------------|-------------------|--------|
| Overview | KPI strip, Volume bar chart, Resolution box plot | `get_volume_by_trc()`, `get_resolution_dist()` | Mockup approved |
| NLP Scanner | Scan controls, results table, entity highlight | `run_nlp_scan()`, `get_scan_results()` | Needs rebuild |
| CSAT Heatmaps | Heatmap grid, color scale, drill-down | `get_csat_heatmap()` | Needs rebuild |
| SubTaxonomy | Tree view, node detail, stat cards | `get_subtaxonomy_tree()` | Needs rebuild |
| Reports | Export config, preview, schedule | `generate_report()` | Needs rebuild |

**Steps**:
1. Load audit JSONL for context
2. Rebuild each tab's widget tree to match v2 design tokens
3. Wire SharedFilterBar signals → data engine refresh
4. Wire primary action button (Refresh) + auto-refresh toggle
5. Functional test each tab with sample data
6. Capture Gate 1 stills for each sub-tab

---

## Phase 2.5 — Restore NLP Scanner Functionality (DONE)

**Context**: The TRC Analytics page was refactored (Phase 2) to 5 tabs using `AnalysisPageBase`. During that refactor, Tab 2 (NLP Scanner) was stubbed with a bare `ScanMonitorWidget` — the debug-only live monitor. Users cannot initiate scans, see scan config, or access history. Tab 4 (SubTaxonomy) was left unwired — no signal connections, no auto-refresh after scans.

The standalone `NLPScannerPage` (`src/ui/pages/nlp_scanner_page.py`) has the complete scanner UI and lifecycle. We migrate that into Tab 2.

**Scope — what changes**: `src/ui/pages/trc_analytics.py` only
**Scope — what stays**: Tabs 1, 3, 5 untouched. `AnalysisPageBase` untouched. `main_window.py` untouched (signals already wired).

---

### Changes (1 file: `src/ui/pages/trc_analytics.py`)

#### 1. Add imports

```python
import logging
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QSpinBox, QDoubleSpinBox, QComboBox, QScrollArea, QApplication
from src.ui.widgets.scan_monitor import ScanMonitorWidget, ScanStatusPanel
from src.ui.widgets.date_picker import ModernDatePicker
from src.ui.widgets.collapsible_section import CollapsibleSection
```
Plus theme tokens: `ALMA_WHITE, ALMA_TEXT_MID, ALMA_BORDER, ALMA_BORDER_LIGHT, ALMA_CREAM, ALMA_GREEN_MID, ALMA_GREEN_DARK, ALMA_TEXT_ON_DARK, ALMA_SUCCESS, ALMA_WARNING, ALMA_ERROR, ALMA_GREEN_SUBTLE`

Add `logger = logging.getLogger("alma.trc_analytics")`

#### 2. Add scanner state to `__init__` (after `self._scan_start_time = 0`)

```python
self._scan_mgr = None
self._poll_timer = None
self._active_scan_id = None
```

#### 3. Replace `_build_scanner_tab()` (lines 285-297)

Replace the 12-line stub with full scanner UI inside a `TabScrollContent`:
- Info panel ("What is NLP Scanning?")
- Scan config card (date range, TRC filter, budget cap, workers, cost estimator)
- `ScanStatusPanel` (idle/running states)
- Control buttons (Start, Pause, Resume, Cancel)
- `ScanMonitorWidget` (hidden until scan starts)
- Scan history table (collapsible)
- Docs panel (collapsible)

All widget names prefixed `_scan_` to avoid conflicts with Overview.
Source: `NLPScannerPage._build_scanner_tab()` + sub-builders.

#### 4. Update `_build_taxonomy_tab()` (lines 409-412)

Wire signals:
```python
self._taxonomy.deep_dive_requested.connect(self.deep_dive_requested.emit)
self._taxonomy.view_tickets_requested.connect(self.view_tickets_requested.emit)
self._taxonomy.pattern_selected.connect(self._on_pattern_selected)
```

#### 5. Add tab-switch handler

In `_setup_tabs()` after all tabs built:
```python
self._tab_widget.currentChanged.connect(self._on_tab_changed)
```

Handler:
```python
def _on_tab_changed(self, index):
    if index == 1: self._refresh_scanner_tab()
    elif index == 3: self._taxonomy.refresh()
```

#### 6. Add scanner lifecycle methods

Migrated from `NLPScannerPage` with `_scan_`/`_nlp_` prefixes:
- `_ensure_scan_manager()` — lazy ScanOrchestrator → ScanWorkerManager
- `_start_nlp_scan()`, `_pause_nlp_scan()`, `_resume_nlp_scan()`, `_cancel_nlp_scan()`
- `_start_scan_polling()`, `_stop_scan_polling()`, `_poll_scan_status()`
- `_run_post_scan_analysis()` — NLPMetaAnalyzer + refresh taxonomy
- `_on_nlp_scan_completed()` — reset state + refresh tabs
- `_update_scan_cost_estimate()` — compute from scanner date pickers
- `_refresh_scanner_tab()`, `_populate_scan_trc_combo()`, `_sync_scan_date_range()`, `_refresh_scan_history()`
- `_on_pattern_selected()` — taxonomy drilldown handler

---

### Verification

1. `python -c "from src.ui.pages.trc_analytics import TRCAnalyticsPage; print('OK')"`
2. `python -m pytest tests/ -x -q` — all pass
3. Launch app → TRC Analytics → NLP Scanner tab: config card, Start Scan button, history
4. SubTaxonomy tab: auto-refreshes on tab switch; signals fire to drilldown

---

## Phase 3 — Trending Topics v2 Migration (THIS SESSION)

### Context

`TrendingTopicsPage` (2,282 lines) still inherits `QWidget` directly with a manual `_build_ui()` and custom filter bar. It needs migration to `AnalysisPageBase` to match TRC Analytics (the Phase 2 gold standard). All data engine logic (`trending_engine.py`), workers, and analysis pipeline stay untouched — this is purely a UI structure migration.

**Current state (QWidget):**
- Manual QVBoxLayout → QTabWidget → QScrollArea per tab
- Custom filter bar: `_build_filter_bar()` with raw QFrame + QHBoxLayout
- Direct widget attributes: `self.date_from`, `self.date_to`, `self.trc_combo`, `self.window_combo`, `self.method_combo`, `self.analyze_btn`
- 2 tabs: Analysis (main), Hypothesis Test
- 3 workers: TrendingWorker, AIEnhancementWorker, HypothesisWorker
- No KPICardRow, no FilterChipBar, no loading skeleton

**Target state (AnalysisPageBase):**
- Inherits AnalysisPageBase → PageHeader + SharedFilterBar + QTabWidget
- Action-first filter bar: `add_primary_action("Analyze")` + date range + TRC combo + Window combo + Method combo
- FilterChipBar for active filter visualization
- KPICardRow with `apply_accent_cycle()` on Overview tab
- TabScrollContent per tab, loading skeleton pattern
- 4 tabs: Overview, Deep Dive, Hypothesis Test, Reports
- Backward-compat properties for main_window.py

### Critical file: `src/ui/pages/trending_topics.py` (2,282 lines)

### main_window.py backward-compat contract (MUST preserve)

main_window.py directly accesses these on `self.trending_page`:
```
page.date_from              → ModernDatePicker (has .date(), .date_changed, .setDate())
page.date_to                → ModernDatePicker
page.trc_combo              → QComboBox (has .currentData(), .currentText())
page.window_combo           → QComboBox (has .currentText())
page.method_combo           → QComboBox (has .currentData())
page.populate_trc_filter()  → method
page.sync_date_to_data()    → method
page.set_scan_blocking()    → method
page.set_drilldown_panel()  → method (inherited from AnalysisPageBase)
page._scan_start_time       → attribute
page._worker                → attribute
page._on_results            → method (worker.finished signal)
page._on_error              → method (worker.error signal)
page._hyp_worker            → attribute (cleanup check)
page._last_analysis_result  → dict attribute
```

### Changes

#### 1. Change class inheritance (line 212)

```python
# BEFORE:
class TrendingTopicsPage(QWidget):

# AFTER:
class TrendingTopicsPage(AnalysisPageBase):
```

#### 2. Rewrite `__init__` (lines 214-222)

```python
def __init__(self, db_manager, parent=None):
    super().__init__(
        db_manager,
        "Trending Topics",
        subtitle="Sentiment trends, rising terms, topic clusters, and cross-TRC correlations",
        parent=parent,
    )
    self._worker = None
    self._hyp_worker = None
    self._current_clusters = []
    self._scan_start_time = 0
    self._last_analysis_result = {}
    self._ai_worker = None
    self._setup_filters()
    self._setup_tabs()
```

#### 3. New `_setup_filters()` method (replaces `_build_filter_bar()`)

```python
def _setup_filters(self):
    self.filter_bar.add_primary_action("Analyze")
    self.filter_bar.add_date_range()
    self.filter_bar.add_combo_filter("trc", "TRC Code", ["All TRCs"])
    self.filter_bar.add_combo_filter("window", "Window", ["7 days", "14 days", "30 days", "60 days", "90 days"])
    self.filter_bar.add_combo_filter("method", "Method", [("NMF", "nmf"), ("K-Means", "kmeans")])

    # Setup TRC combo with data role
    trc_combo = self.filter_bar.get_combo("trc")
    trc_combo.clear()
    trc_combo.addItem("All TRCs", "")
    trc_combo.setMinimumWidth(160)
```

#### 4. Backward-compat properties (new section after __init__)

```python
@property
def date_from(self):
    return self.filter_bar.get_date_from()

@property
def date_to(self):
    return self.filter_bar.get_date_to()

@property
def trc_combo(self):
    return self.filter_bar.get_combo("trc")

@property
def window_combo(self):
    return self.filter_bar.get_combo("window")

@property
def method_combo(self):
    return self.filter_bar.get_combo("method")
```

#### 5. New `_setup_tabs()` method (replaces relevant parts of `_build_ui()`)

```python
def _setup_tabs(self):
    # Tab 1: Overview
    self._overview_tab = TabScrollContent()
    self._build_overview_content()   # KPI row + charts + term tables
    self.add_tab(self._overview_tab, "Overview")

    # Tab 2: Deep Dive (new — splits out term detail from overview)
    self._deep_dive_tab = TabScrollContent()
    self._build_deep_dive_content()  # Co-occurrence, per-term timeline
    self.add_tab(self._deep_dive_tab, "Deep Dive")

    # Tab 3: Hypothesis Test
    self._hypothesis_tab = TabScrollContent()
    self._build_hypothesis_content()
    self.add_tab(self._hypothesis_tab, "Hypothesis Test")

    # Tab 4: Reports
    self._reports_tab = ReportsTab(self.db)
    self.add_tab(self._reports_tab, "Reports")
```

#### 6. Add KPICardRow to Overview tab

```python
# In _build_overview_content():
self._kpi_row = KPICardRow()
self._kpi_total = self._kpi_row.add_card(KPICard("Conversations", "—", "in selected range"))
self._kpi_sentiment = self._kpi_row.add_card(KPICard("Avg Sentiment", "—", "VADER compound"))
self._kpi_rising = self._kpi_row.add_card(KPICard("Rising Terms", "—", "above threshold"))
self._kpi_topics = self._kpi_row.add_card(KPICard("Topic Clusters", "—", "from NMF/K-Means"))
self._kpi_row.apply_accent_cycle()
self._overview_tab.content_layout.addWidget(self._kpi_row)
```

#### 7. Add FilterChipBar to Overview tab

```python
self._chip_bar = FilterChipBar()
self._chip_bar.filter_removed.connect(self._on_chip_removed)
self._chip_bar.all_cleared.connect(self._on_chip_clear_all)
self._overview_tab.content_layout.addWidget(self._chip_bar)
```

#### 8. Implement filter/action hooks

```python
def _on_filters_changed(self, filters):
    if hasattr(self, '_chip_bar'):
        self._sync_chips()

def _on_action_triggered(self, action):
    if action == "Analyze":
        self._on_analyze()  # existing method that starts TrendingWorker
```

#### 9. Add loading skeleton pattern

```python
def _show_loading(self):
    self._skeleton.setVisible(True)
    for w in self._content_sections:
        w.setVisible(False)

def _show_content(self):
    self._skeleton.setVisible(False)
    for w in self._content_sections:
        w.setVisible(True)
```

#### 10. Delete `_build_ui()` and `_build_filter_bar()`

These are fully replaced by `_setup_filters()`, `_setup_tabs()`, and the AnalysisPageBase constructor.

#### 11. Preserve all existing methods (NO changes needed)

Keep as-is: `_on_results()`, `_on_error()`, `_on_analyze()`, `_populate_results()`, `_run_ai_enhancement()`, `_on_ai_results()`, `populate_trc_filter()`, `sync_date_to_data()`, `set_scan_blocking()`, all hypothesis methods, all chart rendering methods.

#### 12. Wire drilldown forwarding

```python
def set_drilldown_panel(self, panel):
    super().set_drilldown_panel(panel)
    self._reports_tab.set_drilldown_panel(panel)
```

### What stays untouched
- `trending_engine.py` — zero changes (data layer)
- `main_window.py` — zero changes (backward-compat properties preserve API)
- All worker classes (TrendingWorker, AIEnhancementWorker, HypothesisWorker)
- All chart rendering methods, result population logic
- All AI enhancement methods

### Verification
1. `python -c "from src.ui.pages.trending_topics import TrendingTopicsPage; print('OK')"` — imports clean
2. `python -m pytest tests/ -x -q` — all tests pass
3. Launch app → Trending Topics → verify: action-first filter bar, accent KPIs, 4 tabs
4. Click "Analyze" → worker runs → results populate all charts + tables
5. Change TRC filter → FilterChipBar updates → charts refresh
6. Hypothesis Test tab → form works → results display
7. Reports tab → drilldown wired

---

## Phase 4 — Incidents v2 Migration

### Context

`IncidentsPage` (1,440 lines) still inherits `QWidget` directly. Same migration pattern as Phase 3, but with incident-specific widgets (control chart, severity badges, theta EWMA tab).

**Current state (QWidget):**
- Manual QVBoxLayout → QScrollArea → content widget
- Custom filter bar: `_build_filter_bar()` with raw QFrame
- No QTabWidget — single scrollable page with status grid, charts, tables
- 2 workers: IncidentWorker, ThetaWorker
- `scan_complete` signal (consumed by main_window for badge updates)
- `control_chart` attribute (main_window sets interventions on it)

**Target state (AnalysisPageBase):**
- Inherits AnalysisPageBase → PageHeader + SharedFilterBar + QTabWidget
- Action-first filter bar: `add_primary_action("Run Scan")` + date range + TRC combo
- FilterChipBar, KPICardRow with `apply_accent_cycle()`
- 4 tabs: Overview, Open Incidents, θ EWMA Scan, Reports
- Loading skeleton pattern

### Critical file: `src/ui/pages/incidents_page.py` (1,440 lines)

### main_window.py backward-compat contract

```
page.date_from              → ModernDatePicker
page.date_to                → ModernDatePicker
page.scan_complete          → Signal (connect to _update_incident_badge)
page.control_chart          → widget (has .set_interventions())
page.populate_trc_filter()  → method
page.sync_date_to_data()    → method
page._scan_start_time       → attribute
page._worker                → attribute
page._on_scan_results       → method
page._on_scan_error         → method
```

### Changes — same pattern as Phase 3

1. Change `class IncidentsPage(QWidget)` → `class IncidentsPage(AnalysisPageBase)`
2. Rewrite `__init__` to call `super().__init__()` + `_setup_filters()` + `_setup_tabs()`
3. New `_setup_filters()`: `add_primary_action("Run Scan")` + date range + TRC combo
4. Backward-compat properties: `date_from`, `date_to`, `trc_combo` (forwarding to `filter_bar.get_*`)
5. New `_setup_tabs()`: 4 tabs in TabScrollContent containers
6. KPICardRow: Total Incidents, Open, Avg Duration, 2θ Flagged → `apply_accent_cycle()`
7. FilterChipBar + chip sync
8. Hooks: `_on_action_triggered("Run Scan")` → existing scan method
9. Loading skeleton
10. Delete `_build_ui()`, `_build_filter_bar()`, `_build_header()`
11. Preserve: `scan_complete` signal, `control_chart` attribute, all workers, all scan/theta methods
12. Tab split: current single-scroll layout → 4 tabs (Overview has KPIs + control chart + status grid, Open Incidents has filtered table, θ EWMA has theta scan controls + results, Reports tab)

### What stays untouched
- `incident_engine.py` — zero changes
- `theta_engine.py` — zero changes
- `main_window.py` — zero changes
- All worker classes (IncidentWorker, ThetaWorker)

### Verification
1. Import check, pytest pass
2. Launch app → Incidents → verify: action-first filter bar, accent KPIs, 4 tabs
3. "Run Scan" → IncidentWorker runs → status grid + control chart populate
4. θ EWMA tab → ThetaWorker runs → EWMA chart populates
5. `scan_complete` signal fires → sidebar badge updates
6. Settings → change interventions → `control_chart.set_interventions()` works

---

## Phase 5 — Cross-Page Regression & Consolidation

### Context

After Phases 3+4, all 3 analysis pages use AnalysisPageBase with consistent v2 patterns. Phase 5 validates everything works together and catches regressions.

### 5A. Automated Test Suite

**Existing tests** (`tests/test_build_55.py` — 22 tests):
- `test_trc_analytics_v2_*` — TRC Analytics page tests
- Update these and add parallel tests for Trending + Incidents

**New tests to add** (~36 tests in `tests/test_build_phase3.py`):

```
# Trending Topics — Structure (6)
test_trending_inherits_analysis_page_base
test_trending_has_shared_filter_bar
test_trending_has_4_tabs
test_trending_kpi_accent_cycle
test_trending_filter_chip_bar_present
test_trending_backward_compat_properties

# Trending Topics — Data Flow (4)
test_trending_analyze_triggers_worker
test_trending_filter_change_syncs_chips
test_trending_populate_trc_filter
test_trending_sync_date_to_data

# Incidents — Structure (6)
test_incidents_inherits_analysis_page_base
test_incidents_has_shared_filter_bar
test_incidents_has_4_tabs
test_incidents_kpi_accent_cycle
test_incidents_filter_chip_bar_present
test_incidents_backward_compat_properties

# Incidents — Data Flow (4)
test_incidents_run_scan_triggers_worker
test_incidents_scan_complete_signal_exists
test_incidents_control_chart_attribute_exists
test_incidents_populate_trc_filter

# Cross-Page Integration (6)
test_all_pages_extend_analysis_page_base
test_all_pages_have_consistent_filter_bar
test_date_sync_propagates_all_pages
test_populate_trc_filter_all_pages
test_drilldown_panel_wired_all_pages
test_set_scan_blocking_pages

# MainWindow Integration (4)
test_make_trending_job_uses_backward_compat
test_make_incident_job_uses_backward_compat
test_sidebar_navigation_all_pages
test_auto_refresh_all_pages
```

### 5B. Manual Verification Checklist

1. Navigation: Sidebar → each of 3 pages → each tab within
2. Filter bar: action-first button → date range → TRC combo on all 3 pages
3. Filter chip bar: change filter → chip appears → remove chip → filter resets
4. KPI cards: accent cycle (green, blue, teal, amber) on all 3 pages
5. Data refresh: click action button → loading skeleton → results populate
6. Date sync: change date on one page → matches on others (if linked)
7. TRC populate: import data → all TRC combos refresh
8. Drilldown: click row in any table → drilldown panel opens
9. Auto-refresh toggle: settings persist, triggers on data import
10. Worker cleanup: navigate away during analysis → worker cancelled cleanly

### 5C. Memory Consolidation (from CONSOLIDATION_FIX_PLAN.md)

Apply the 6 changes from `docs/CONSOLIDATION_FIX_PLAN.md` to `trending_engine.py`:
1. `compute_sentiment_trends()` — add `conversations=None` param
2. `compute_rising_terms()` — add `conversations=None` param
3. `compute_topic_clusters()` — add `conversations=None` param
4. Wire pre-fetched conversations in `run_full_analysis()`
5. Strip `per_trc_series` from returned correlations
6. Strip `full_thread` after compound discovery

This eliminates 2-3 redundant `_fetch_conversations()` calls, saving ~150-200 MB at 20K rows.

### Verification
- `python -m pytest tests/ -v` — all tests pass (existing + new)
- Manual checklist above completed
- Memory spot-check: Task Manager before/after trending analysis at current data scale

---

## Session Transfer Protocol

At the start of each new session:
1. Provide the audit JSONL path: `ocr_debug/audit/alma_audit.jsonl`
2. Provide the audit MD path: `ocr_debug/audit/alma_audit.md`
3. State which phase/tab is next
4. Reference the v2 mockup HTMLs in `mockups/` for design reference
5. Reference `_sessions.json` for Figma URLs

At the end of each session:
1. Update JSONL with any new files created or modified
2. Update MD with completed tab status
3. Commit changes with descriptive message
4. Note what's next in the session summary

---

---

## Phase 5.2 — E2E Tests: NLP Scanner + AI Report Creator (Live Gemini)

### Context

Existing backend coverage is strong (228+ tests) but most mock the Gemini boundary. The live tests (`test_pipeline_live.py`, `test_voc_full_e2e.py`) only cover classification and VOC. There are no live E2E tests that:
1. Run a **full NLP scan** via `ScanOrchestrator` → poll to completion → verify classifications + findings in DB
2. Run **all AI Report types** via `ReportBridgeClient` → verify substantive output → save/retrieve history

### Approach

**Single test file**: `tests/test_e2e_scanner_reports.py`

**Real Gemini API** — boots actual GeminiBridge/ReportBridgeClient, makes real API calls. Gated: only runs when `--live` flag passed or `ALMA_LIVE_TEST=1` env var set. Uses the production DB at `data/alma_insights.db` (same as `test_pipeline_live.py` and `test_feature_integration.py`).

**Pattern**: Follow `test_pipeline_live.py` structure — `@pytest.mark.skipif` gate, class-based test ordering, shared bridge/orchestrator fixtures at module level to avoid repeated cold-starts.

### Test Inventory (~16 tests)

#### A. NLP Scanner — Full Pipeline (8 tests, ordered)

Uses real `ScanOrchestrator` with real GeminiBridge pool against production DB tickets.

| # | Test | What it exercises |
|---|------|-------------------|
| 1 | `test_01_orchestrator_boot` | Boot ScanOrchestrator(db_path, num_workers=2), verify bridges alive |
| 2 | `test_02_start_scan_returns_plan` | `start_scan(date_start, date_end, trc_filter="TRC-001", budget_cap=5.0, parallel_workers=2)` → returns `{scan_id, total_batches, total_tickets, estimated_cost}`, all > 0 |
| 3 | `test_03_poll_shows_progress` | Poll `get_status(scan_id)` every 10s, verify `classified_count` increases over time, `status` transitions through `running` |
| 4 | `test_04_scan_completes` | Poll until `status == 'completed'` (timeout 10 min), verify `completed_batches == total_batches` |
| 5 | `test_05_classifications_persisted` | Query `nlp_ticket_classifications WHERE scan_id = ?` → row count matches `classified_count`, each row has valid `sub_cluster`, `sentiment_polarity`, `summary` |
| 6 | `test_06_field_validation` | For each classification row: `sub_cluster_confidence` in [0,1], `sentiment_intensity` in [1,5], `is_novel` in [0,1], `summary` len > 10 |
| 7 | `test_07_scan_events_logged` | Query `scan_events WHERE scan_id = ?` → has preflight events, batch_started/completed events, scan_completed event |
| 8 | `test_08_orchestrator_shutdown` | `orchestrator.shutdown()` completes without error, bridges cleaned up |

**Scoped small**: Filter to a single TRC (~40-50 tickets) with $5 budget cap to keep cost/time bounded (~2-4 min).

#### B. AI Report Creator — All Report Types (8 tests, ordered)

Uses real `ReportBridgeClient` with persistent bridge subprocess against production DB.

| # | Test | What it exercises |
|---|------|-------------------|
| 1 | `test_01_bridge_client_boot` | Boot `ReportBridgeClient`, verify `is_available() == True`, `model` is set |
| 2 | `test_02_build_data_block` | `build_data_block(db, date_start, date_end)` → returns dict with `ticket_count > 0`, `trc_distribution`, `top_terms`, `sentiment_by_trc` |
| 3 | `test_03_general_trend_report` | Load "General Trend Analysis" prompt → `format_data_block_for_prompt` → `client.generate(prompt)` → response is ≥500 chars, contains section headers |
| 4 | `test_04_executive_summary_report` | Same flow with "Executive Summary" prompt → response contains "findings" or "recommendations" |
| 5 | `test_05_incident_summary_report` | Same flow with "Incident Summary" prompt → response is substantive text |
| 6 | `test_06_nlp_synthesis_report` | Query latest completed scan → `NLPSynthesizer(db).synthesize_findings(scan_id)` → response ≥200 chars |
| 7 | `test_07_report_history_save_load` | Save report text to DB via `db.save_report()` → `db.get_full_report(id)` → text round-trips |
| 8 | `test_08_bridge_client_shutdown` | `client.shutdown()` completes without error |

**Scoped**: Uses full DB date range (all 888 tickets). Each Gemini call takes ~10-30s. Total section time ~3-5 min.

### Critical Files

| File | Role |
|------|------|
| `tests/test_e2e_scanner_reports.py` | **NEW** — all 16 tests |
| `src/agents/scan_orchestrator.py` | `ScanOrchestrator` — real boot, start_scan, get_status, shutdown |
| `src/agents/report_bridge_client.py` | `ReportBridgeClient` — real boot, generate, shutdown |
| `src/data/report_builder.py` | `build_data_block()`, `format_data_block_for_prompt()` |
| `src/data/nlp_synthesizer.py` | `NLPSynthesizer.synthesize_findings()` |
| `data/alma_insights.db` | Production DB (888 tickets, 127 TRCs) |

### Gating

```python
LIVE = os.environ.get("ALMA_LIVE_TEST") == "1" or "--live" in sys.argv
pytestmark = pytest.mark.skipif(not LIVE, reason="Live Gemini tests: set ALMA_LIVE_TEST=1")
```

### Expected Runtime & Cost

| Section | Duration | Estimated Cost |
|---------|----------|---------------|
| A. NLP Scanner (single TRC, 2 workers) | ~2-4 min | ~$0.50-1.00 |
| B. AI Reports (5 Gemini calls) | ~3-5 min | ~$0.30-0.50 |
| **Total** | **~5-9 min** | **~$0.80-1.50** |

### Verification

1. `ALMA_LIVE_TEST=1 python -m pytest tests/test_e2e_scanner_reports.py -v --tb=short` — all 16 pass
2. Without the flag: `python -m pytest tests/test_e2e_scanner_reports.py -v` — all 16 skipped (gated)
3. Existing tests unaffected: `python -m pytest tests/test_phase5_regression.py tests/test_build_55.py -v` — 61 pass

---

## Phase 5.3 — Analyst Agent Hardening

### Context

The NLP scan pipeline runs: Classification → Analyst (4 LLM tasks) → Meta-Analyzer → Finalize. The analyst stores results in `analyst_reports` table, but **nothing reads them** — `get_analyst_reports(scan_id)` exists in db_manager but zero UI references call it. Three problems:

1. **No read path** — analyst reports are write-only, invisible to users
2. **12% novelty sampling** — novelty validation hard-caps at 30 tickets (`[:30]`) out of ~252 novels
3. **Pattern merge ordering** — runs BEFORE meta-analyzer creates/updates `sub_patterns`, so always has stale data

**Architecture constraint**: Classification ledger (`nlp_ticket_classifications`) is append-only. Sub-patterns table is a mutable managed index. Analyst is an advisory signal — it improves meta-analyzer decisions but never mutates the ledger directly.

### Three Fixes

---

#### Fix 1 — Surface Analyst Reports in TRC Analytics Drilldown

**Goal**: Add "View Analyst Reports" button to NLP Scanner tab (Tab 2). Opens DrilldownPanel in reports mode with analyst report cards (synthesis, audit, novelty, merge). Detail view renders structured JSON as formatted HTML.

**File: `src/ui/pages/trc_analytics.py`**

| Change | Description |
|--------|-------------|
| A | Add `_view_analyst_reports_btn` (QPushButton, outline style) to scan control button layout after Retry button |
| B | Show button via `_show_analyst_reports_button()` — called from `_on_nlp_scan_completed()` and `_refresh_scanner_tab()`. Checks latest completed scan for analyst_reports |
| C | Add `_open_analyst_reports()` — loads `db.get_analyst_reports(scan_id)`, adapts to drilldown format (`report_id`, `run_at`, `summary`, `parameters`, `ticket_count`), calls `drilldown.show_reports()` with detail_callback |
| D | Add `_render_analyst_report_html(report_id)` — fetches row from `analyst_reports`, parses JSON content, renders structured HTML with metrics badges + nested JSON sections (lists as cards, dicts as key-value, strings as paragraphs). Falls back to plain text if not valid JSON |

**Format adapter** (analyst_reports → drilldown format):
```python
{
    "report_id": r["report_id"],
    "run_at": r["created_at"],
    "summary": "Cross-TRC Synthesis",  # from type_labels lookup
    "parameters": {"report_type": report_type, "scan_id": scan_id[:8]},
    "ticket_count": metrics.get("total_novels", 0),  # varies by report type
}
```

**No changes needed**: `drilldown_panel.py` (show_reports API is sufficient), `db_manager.py` (get_analyst_reports already works).

---

#### Fix 2 — Full Novel Ticket Validation with Smart Batching

**Goal**: Remove `[:30]` hard cap. Batch all novel tickets using model input budget. Make multiple bridge calls. Aggregate results. Feed DUPLICATE verdicts back to classifications so meta-analyzer's `_update_sub_taxonomy()` excludes false-positives.

**File: `src/agents/analyst_agent.py`**

| Change | Description |
|--------|-------------|
| A | Replace `run_novelty_validation()` (lines 261-358) with batched version |
| B | Add `apply_novelty_verdicts(scan_id, validations)` method |

**Batched novelty validation logic**:
1. Load ALL `is_novel=1` tickets (no truncation)
2. Load existing patterns (keep `LIMIT 50` for prompt context)
3. Build per-novel text items (~200 chars each)
4. Calculate prompt overhead: ~900 chars (instructions + existing patterns block + JSON template)
5. Batch novels so each prompt stays under `input_budget` (default 100K chars)
6. Loop batches → `_call_bridge()` each → aggregate `validations[]` + recompute summary
7. If a batch fails: log warning, skip, continue with remaining batches
8. Store aggregated JSON report via `_store_report()`

**Verdict application** (`apply_novelty_verdicts`):
- DUPLICATE verdict → `UPDATE nlp_ticket_classifications SET is_novel = 0, novelty_verdict = 'DUPLICATE', novelty_match = ? WHERE scan_id = ? AND ticket_id = ?`
- MERGE verdict → `UPDATE ... SET novelty_verdict = 'MERGE', novelty_match = ?` (keeps is_novel=1, advisory tag)
- VALID verdict → no update needed (default state)

**File: `src/data/db_manager.py`**

| Change | Description |
|--------|-------------|
| A | Add `_ensure_novelty_verdict_columns()` migration — `ALTER TABLE nlp_ticket_classifications ADD COLUMN novelty_verdict TEXT` + `novelty_match TEXT`. Called from schema init chain, safe if columns already exist |

**File: `src/agents/scan_orchestrator.py`**

| Change | Description |
|--------|-------------|
| A | Capture return value from `run_novelty_validation(scan_id)` (currently discarded) |
| B | Call `self._analyst.apply_novelty_verdicts(scan_id, result["validations"])` after novelty validation completes |

**File: `src/data/nlp_meta_analyzer.py`**

| Change | Description |
|--------|-------------|
| — | **No code changes needed**. DUPLICATE verdicts set `is_novel=0`, which the existing `SUM(CASE WHEN is_novel=1...)` query already excludes. MERGE verdicts remain advisory — meta-analyzer creates probationary entries as normal. The `novelty_verdict`/`novelty_match` columns are available for future enhanced logic. |

---

#### Fix 3 — Move Pattern Merge After Meta-Analyzer

**Goal**: Pattern merge reads `sub_patterns WHERE tier IN ('active', 'probationary')`. Meta-analyzer's `_update_sub_taxonomy()` creates new patterns, updates `lifetime_tickets`, promotes/demotes tiers. Pattern merge must run AFTER these updates.

**File: `src/agents/scan_orchestrator.py`**

| Change | Description |
|--------|-------------|
| A | Remove `run_pattern_merge()` call from analyst block (lines 808-813) |
| B | Add `run_pattern_merge()` after meta-analyzer completes (after line 853), in its own try/except with event logging |
| C | Guard: if analyst was not booted (bridge died), re-boot via `_boot_analyst()` |
| D | Update pipeline docstring in `_run_scan()` to reflect new order |

**New pipeline order**:
```
1. Preflight (boot bridges + workers)
2. Classification (worker threads process batches)
3. Analyst: synthesis → audit → novelty + verdict application
4. Meta-Analyzer: within-TRC → sub-taxonomy → n-grams → snapshots → cross-TRC → findings → exemplars → prune
5. Pattern Merge (post-meta): reads fresh sub_patterns data
6. Finalize
```

---

### Files Modified (Summary)

| File | Fixes | Changes |
|------|-------|---------|
| `src/ui/pages/trc_analytics.py` | #1 | +4 methods, +1 button widget |
| `src/agents/analyst_agent.py` | #2 | Replace `run_novelty_validation()`, +1 method `apply_novelty_verdicts()` |
| `src/agents/scan_orchestrator.py` | #2, #3 | Capture novelty result, apply verdicts, move pattern_merge after meta-analyzer |
| `src/data/db_manager.py` | #2 | +1 migration method for 2 new columns |
| `src/data/nlp_meta_analyzer.py` | — | No changes (DUPLICATE exclusion is automatic) |

### Error Handling

| Scenario | Handling |
|----------|----------|
| No analyst reports for scan | Button stays hidden (`_show_analyst_reports_button` checks count) |
| Drilldown panel is None | Early return in `_open_analyst_reports()` |
| Report content is not valid JSON | Fallback to plain-text rendering |
| Zero novel tickets | Store empty report, return valid empty result |
| A novelty batch bridge call fails | Log warning, skip batch, aggregate remaining |
| All novelty batches fail | Return empty aggregated result with 0 counts |
| Analyst bridge died before pattern merge | Re-boot via `_boot_analyst()` guard |
| novelty_verdict column missing (old DB) | ALTER TABLE migration adds it safely |
| Meta-analyzer fails before pattern merge | Pattern merge still attempts with existing sub_patterns state |

### Testing Plan

**Unit tests** (add to `tests/test_pipeline_full.py`):
- `test_novelty_batch_splitting_zero_novels` — 0 novels → empty result
- `test_novelty_batch_splitting_under_limit` — 25 novels → 1 batch
- `test_novelty_batch_splitting_over_limit` — 500 novels, small budget → multiple batches
- `test_apply_novelty_verdicts_duplicate` — DUPLICATE → is_novel becomes 0
- `test_apply_novelty_verdicts_merge` — MERGE → is_novel stays 1, verdict column set
- `test_apply_novelty_verdicts_valid` — VALID → no changes
- `test_novelty_verdict_column_migration` — columns added to fresh DB
- `test_analyst_report_format_adapter` — analyst_reports → drilldown format

**Regression**: `python -m pytest tests/ -x -q` — all 83+ existing tests pass

**E2E production test**: `ALMA_LIVE_TEST=1` full scan across all ~888 tickets:
- Verify all 4 analyst reports created
- Verify ALL novels validated (not truncated at 30)
- Verify DUPLICATE verdicts reduce is_novel count
- Verify pattern merge has fresh sub_patterns data (lifetime_tickets > 0 for current scan)
- Verify analyst reports visible via "View Analyst Reports" button

### Documentation Updates

1. Update `ALMA_PROJECT_STATUS.md`:
   - Add Phase 5.3 to build history
   - Update pipeline diagram with new ordering
   - Document `novelty_verdict` / `novelty_match` columns
   - Update analyst agent section (batched validation, verdict feedback loop)

2. Update `ocr_debug/audit/alma_audit.md`:
   - Update Agent Pipeline section with new pipeline order
   - Add analyst report UI wiring to Page Inventory (TRC Analytics)
   - Update analyst_agent.py file entry with new method signatures

3. Sync worktree → main repo after verification passes

### Estimated Scope

| Component | LOC Added | LOC Modified |
|-----------|-----------|-------------|
| trc_analytics.py | ~120 | ~5 |
| analyst_agent.py | ~100 | ~100 (replace) |
| scan_orchestrator.py | ~25 | ~15 |
| db_manager.py | ~15 | ~2 |
| test_pipeline_full.py | ~80 | 0 |
| **Total** | **~340** | **~122** |

---

## Phase 5.4 — Classification Pipeline Hardening (Boundary Guard + Partial Parse Recovery)

### Context

Full E2E scan across 888 tickets (Phase 5.3 verification) revealed two data-quality gaps:

1. **Batch overflow → wrong TRC** (31 tickets): Model classifies ticket_ids beyond its batch boundary. `tool_registry._tool_store_classification()` falls back to the batch-level TRC for unknown ticket_ids (line 479-484). The overflow tickets get stored with the wrong TRC. Later, when the correct batch runs, `INSERT OR REPLACE` fixes most — but any that aren't re-classified persist with wrong TRC. Measured: 31 overflow tickets across batches 2+3 in our 888-ticket scan (3.5% of total).

2. **Partial parse → silent data loss** (37 tickets): Model truncates output mid-response. Bridge call succeeds (no error), so `has_error = False` (orchestrator line 1194). Batch marked `completed` with 26/63 classified. No retry triggered. 37 tickets permanently unclassified. Measured: batch 6 in our scan, 37 missing tickets (4.2% of total).

Combined: 96.5% coverage → ~99.5% expected after fix. TRC accuracy 96.3% → ~100%.

Compound failure analysis confirmed these fixes are safe:
- Partial parse retries are self-correcting (fewer tickets on each retry)
- Neither fix interacts with rate governor interval (only 429s do)
- Stall timeouts + partial parses don't cascade (independent failure modes)
- Max retries = 3 provides absolute bound on retry amplification

### Two Fixes

---

#### Fix 1 — Ticket ID Boundary Guard

**Goal**: Reject classifications for ticket_ids not in the batch manifest. Prevents overflow→wrong-TRC. Also makes Check 3 (TRC cross-audit) unnecessary — overflow is the only path to wrong TRC in the current architecture.

**File: `src/agents/tool_registry.py`** — `_tool_store_classification()` method

**Change**: Insert boundary check after line 473 (`ticket_id = args.get("ticket_id", "")`), before TRC resolution at line 479.

```python
# ── 5.4: Ticket ID boundary guard ──
# Reject classifications for tickets not in this batch's manifest.
# Prevents model overflow (classifying sequential IDs beyond batch boundary)
# which would assign the batch-level TRC fallback to wrong tickets.
ticket_trc_map = self._context.get("ticket_trc_map", {})
if ticket_trc_map and ticket_id not in ticket_trc_map:
    logger.warning(
        "Boundary guard: rejected ticket_id=%s (not in batch manifest of %d tickets)",
        ticket_id, len(ticket_trc_map),
    )
    return {"status": "rejected", "ticket_id": ticket_id, "reason": "not_in_batch"}
```

**Why this is safe**:
- `ticket_trc_map` is populated by worker at line 122: `{t["ticket_id"]: t.get("trc", trc) for t in tickets}`
- Empty map (`{}`) → guard is bypassed (backward-compatible with any code path that doesn't set it)
- Rejected tickets return `status="rejected"` instead of `status="stored"` → worker's `_handle_parsed_event` (line 386-388) only adds `"stored"` results to `classified_ids` → rejected tickets reduce the classified count → feeds into Fix 2
- The JSON fallback path (line 312-320) has the same check: `if store_result.get("status") == "stored"` → rejected tickets excluded there too
- Overflow tickets will be classified correctly when their actual batch runs later

**LOC**: ~8 lines added, ~2 lines moved (ticket_trc_map assignment moves up from line 479)

---

#### Fix 2 — Partial Parse Completeness Check + Retry

**Goal**: Detect when classified < expected and route through the existing retry path. Self-correcting because `_get_tickets_for_batch()` on retry only fetches unclassified tickets.

**File: `src/agents/scan_orchestrator.py`** — `_worker_loop()` method

**Change**: Insert completeness check between line 1192 (batch size recording) and line 1193 (current `# Update batch status` comment). Injects a synthetic error before the `has_error` determination.

```python
# ── 5.4: Partial parse completeness check ──
# If the bridge call succeeded but classified significantly fewer
# tickets than expected, treat as truncation for retry.
# Self-correcting: retry fetches only unclassified tickets (fewer each time).
_n_classified = result.get("classified", 0)
_n_expected = len(tickets)
_n_missing = _n_expected - _n_classified
_completeness = _n_classified / _n_expected if _n_expected > 0 else 1.0
_prev_classified = batch.get('_prev_classified', -1)

if (not result.get("error")
        and _completeness < 0.80
        and _n_missing > 3
        and _n_classified > _prev_classified):
    result["error"] = "partial_parse"
    result["recoverable"] = True
    batch['_prev_classified'] = _n_classified
    logger.warning(
        f"Partial parse detected: {_n_classified}/{_n_expected} "
        f"({_completeness:.0%}) — routing to retry"
    )
elif (not result.get("error")
        and _completeness < 0.80
        and _n_missing > 3
        and _n_classified <= _prev_classified):
    # Stagnation: retry didn't improve. Accept as final.
    logger.warning(
        f"Partial parse stagnation: {_n_classified}/{_n_expected} "
        f"({_completeness:.0%}), prev={_prev_classified} — accepting as final"
    )
```

**Three guard conditions**:

| Guard | Purpose | Value |
|-------|---------|-------|
| `_completeness < 0.80` | Percentage threshold — don't retry near-complete batches | 80% |
| `_n_missing > 3` | Absolute floor — don't retry for 1-2 missing tickets in small batches | 3 tickets |
| `_n_classified > _prev_classified` | Stagnation detection — if retry didn't improve, stop wasting API calls | Monotonic increase required |

**How stagnation detection works**:
- First run: `_prev_classified` defaults to -1 via `batch.get('_prev_classified', -1)`. Since classified ≥ 0 > -1, the check always passes on first attempt (even if 0 tickets classified).
- Retry 1: classified some more. `_prev_classified` was set to first run's count. If retry improved → continue. If retry didn't improve (same or fewer classified) → stagnation → accept as final, no error injected, batch completes.
- This bounds wasted retries on genuinely unclassifiable tickets to at most 1 extra attempt.

**Downstream interaction with existing retry logic (lines 1200-1229)**:
- `has_error = bool(result.get('error'))` → True (because we injected `"partial_parse"`)
- `error_str = "partial_parse"` → `is_quota = False` → `max_retries = 3`
- Batch requeued with `retry_count + 1`
- On next pull: `_get_tickets_for_batch()` returns only unclassified tickets (self-correcting)
- `_prev_classified` is stored on the batch dict (in-memory), survives requeue

**What does NOT happen**:
- **Rate governor degradation**: The completeness check fires AFTER the rate governor block (lines 1134-1180). By that point, `report_success(elapsed)` already ran — which is correct. The API call succeeded; we're injecting a pipeline-level quality error, not an API error. The rate governor should see partial-parse calls as successful (the API responded). The subsequent retry path calls `report_error()` on the injected error, which resets `consecutive_successes` but does NOT change `min_interval`.
- **Worker internal double-retry**: The bridge call succeeded (`error=None` from worker). Worker returns after 1 internal attempt. Completeness check fires in orchestrator AFTER the worker returns. No double-retry.
- **Queue depth explosion**: Requeue replaces same batch_id. Max retries = 3. Self-correcting (fewer tickets each retry).
- **Bridge stall escalation**: The partial_parse error is injected after `bridge.record_success()` already ran (line 1180), so the bridge's consecutive stall counter stays at 0.

**Insertion point**: AFTER rate governor reporting (line 1192) but BEFORE `has_error` determination (line 1194). This ensures the rate governor correctly sees the API call as a success, while the retry logic correctly sees the batch as incomplete.

**LOC**: ~20 lines added, 0 modified

---

#### Fix 2b — Scan Event Logging for Partial Parse

**File: `src/agents/scan_orchestrator.py`** — same `_worker_loop()` method

**Change**: Add the `"partial_parse"` error to the retry event message. No new code needed — the existing retry event at lines 1219-1228 already formats `result.get("error", "unknown")` which will show `"partial_parse"`.

Also add a batch-level event for stagnation acceptance:
```python
# After the stagnation branch above:
self._emit_event(
    scan_id, 'info', 'running',
    f'Batch {batch_num} partial parse accepted '
    f'({_n_classified}/{_n_expected}, stagnation)',
    metadata={'batch_id': batch_id, 'classified': _n_classified,
              'expected': _n_expected}
)
```

**LOC**: ~6 lines

---

### Files Modified (Summary)

| File | Fix | Changes |
|------|-----|---------|
| `src/agents/tool_registry.py` | #1 | +8 lines: boundary guard in `_tool_store_classification()` |
| `src/agents/scan_orchestrator.py` | #2, #2b | +26 lines: completeness check + stagnation detection + event in `_worker_loop()` |
| **Total** | | **~34 lines added, ~2 lines moved** |

### What Stays Untouched

- `worker_agent.py` — no changes (boundary rejection flows through existing `status != "stored"` checks)
- `rate_governor.py` — no changes (partial parse → `report_success()` is correct — API call succeeded)
- `supervisor.py` — no changes (parse_rate EMA unaffected — partial parse still has `classified > 0`)
- `batch_packer.py` — no changes (halve_for_retry not invoked for partial_parse)
- `db_manager.py` — no changes (no new columns needed)
- `gemini_bridge_wrapper.py` — no changes

### Error Handling

| Scenario | Handling |
|----------|----------|
| Overflow ticket_id | Boundary guard rejects → `status="rejected"` → not counted in classified → lower completeness → triggers Fix 2 if threshold met |
| Partial parse (26/63) | Completeness check → inject `partial_parse` error → retry with 37 remaining tickets |
| Partial parse on small batch (4/5) | `_n_missing = 1 ≤ 3` → guard blocks retry → batch completes normally |
| Retry improves but still partial (37→30 remaining) | `_n_classified > _prev_classified` → continues retrying |
| Retry stagnates (37→37 remaining) | `_n_classified <= _prev_classified` → stagnation → accept as final |
| All retries fail (max_retries=3 exhausted) | Batch marked failed → retry_sweep gets one more attempt with fresh worker |
| Empty ticket_trc_map (backward-compat) | `if ticket_trc_map and ...` → guard bypassed → no behavior change |
| 0 tickets classified, bridge succeeded | `_completeness = 0.0 < 0.80` and `_n_missing > 3` (if batch > 3 tickets) → `_prev_classified = -1` (default) → `0 > -1` is True → triggers retry correctly |

**Edge case correction**: `_prev_classified` default must be -1, not 0. Otherwise a batch that classifies 0 tickets (model returned garbage) on first attempt triggers stagnation immediately. With default -1: `0 > -1` is True → retry fires. On retry, if still 0: `0 > 0` is False → stagnation → accept.

### Testing Plan

**Unit tests** (add to `tests/test_pipeline_full.py`):

| Test | What it validates |
|------|------------------|
| `test_boundary_guard_rejects_overflow` | Store classification with ticket_id not in manifest → returns `status="rejected"` |
| `test_boundary_guard_allows_valid` | Store classification with ticket_id in manifest → returns `status="stored"` |
| `test_boundary_guard_skips_empty_map` | Store classification with empty ticket_trc_map → returns `status="stored"` (backward-compat) |
| `test_partial_parse_detected` | Simulate `classified=26, expected=63, error=None` → error becomes `"partial_parse"` |
| `test_partial_parse_skips_small_batch` | Simulate `classified=4, expected=5, error=None` → no error injected (missing ≤ 3) |
| `test_partial_parse_skips_near_complete` | Simulate `classified=55, expected=63, error=None` → no error injected (87% > 80%) |
| `test_partial_parse_stagnation` | Simulate retry with `_prev_classified=26, classified=26` → no error injected (stagnation) |
| `test_partial_parse_default_prev` | First attempt with `classified=0` → `_prev_classified=-1` → error injected (0 > -1) |

**Regression**: `python -m pytest tests/ -x -q` — all 83+ existing tests pass

**E2E verification** (optional, with `ALMA_LIVE_TEST=1`): Run full scan, verify:
- No overflow tickets stored with wrong TRC (boundary guard logged rejections)
- All partial-parse batches retried (scan_events show `partial_parse` retry entries)
- Final classified count closer to 888 than previous 849
- No rate governor degradation (min_interval unchanged after partial parse retries)

### Estimated Impact

| Metric | Before (E2E measured) | After (expected) |
|--------|----------------------|-------------------|
| Coverage | 849/888 (95.6%) | ~880/888 (~99.1%) |
| TRC accuracy | 818/849 (96.3%) | ~880/880 (~100%) |
| Overflow tickets | 31 (wrong TRC) | 0 (rejected at boundary) |
| Silent data loss | 37 (batch 6) | 0-5 (stagnation-accepted only) |
| API cost increase | — | +$0.06-0.18 (retry calls) |
| Scan time increase | — | +50-75s (retry governor waits) |

---

## Phase 5.4b — Scan Pipeline Bug Fixes (Progress Counter + Date Filter)

### Context

Full E2E production scan (888 tickets, Phase 5.4 verification) confirmed 886/888 classified (99.8%), but the UI displayed "823 of 886" (92%). Two independent bugs:

1. **Bug A — Supervisor progress counter stuck at 823**: `supervisor._classified_tickets` (line 147) accumulates only from `notify_batch_complete(result)` calls. When a batch streams 63 classifications via tool_call before its bridge fails (e.g., `bridge_shutdown`), those 63 are in the DB but the worker reports `classified=10` on retry (only the retry's new tickets). Supervisor total: 823 instead of 886. The scan_monitor UI (line 553-569) reads `scan_progress.classified` which reflects this stale in-memory counter.

2. **Bug B — Single-digit hour date filter excludes 2 tickets**: String comparison `'2025-03-12 8:24:00' <= '2025-03-12 23:59:59'` returns False because `'8' > '2'` lexicographically. Tickets 10885 and 10886 have single-digit hour timestamps and are excluded from batch planning queries. Same bug class previously fixed for incident grid/chart (using SUBSTR), but NLP scan queries still use raw string comparison.

### Fix A — Query Actual DB Count in Supervisor

**Goal**: Replace in-memory accumulation with a periodic DB count query so the progress counter reflects actual classified rows, including those from failed-then-retried batches.

**File: `src/agents/supervisor.py`**

| Change | Line(s) | Description |
|--------|---------|-------------|
| A1 | 327-364 | In `_update_scan_progress()`, query actual classified count from `nlp_ticket_classifications` table instead of using `self._classified_tickets` |
| A2 | 107-110 | Add `self._db_path` parameter to `__init__` (passed through from orchestrator) |

**Implementation**:

```python
# In _update_scan_progress(), replace line 352 (self._classified_tickets):
# Query actual DB count instead of in-memory accumulator
try:
    import sqlite3
    db_conn = sqlite3.connect(self._db_path)
    db_conn.row_factory = sqlite3.Row
    actual_count = db_conn.execute(
        "SELECT COUNT(*) AS n FROM nlp_ticket_classifications WHERE scan_id = ?",
        (self.scan_id,)
    ).fetchone()["n"]
    db_conn.close()
    classified_count = actual_count
except Exception:
    classified_count = self._classified_tickets  # fallback to in-memory
```

**Why separate connection**: Supervisor runs in its own thread with its own poll loop. Using a separate short-lived connection avoids thread-safety issues with the main db_manager connection. The query is lightweight (indexed on `scan_id` via `idx_nlp_tc_scan`).

**File: `src/agents/scan_orchestrator.py`**

| Change | Description |
|--------|-------------|
| Pass `db_path` to Supervisor constructor | Currently only passes `scan_id`, `total_batches`, `total_tickets`, `workers`, `rate_governor`. Add `db_path=self._db_path` |

**LOC**: ~15 lines added, ~3 modified

---

### Fix B — SUBSTR Date Comparison in All Scan Queries

**Goal**: Replace all `created_at >= ? AND created_at <= ?` with `SUBSTR(created_at, 1, 10)` date extraction. This is the same fix pattern already applied to incident grid/chart queries.

**File: `src/agents/scan_orchestrator.py`** — 4 locations

| Line | Current | Fix |
|------|---------|-----|
| 126 | `WHERE created_at >= ? AND created_at <= ?` with `date_end + ' 23:59:59'` | `WHERE SUBSTR(created_at, 1, 10) >= ? AND SUBSTR(created_at, 1, 10) <= ?` with `(date_start, date_end)` |
| 133 | Same pattern | Same fix |
| 149 | Same pattern | Same fix |
| 1878 | `WHERE created_at >= ? AND created_at <= ?` with `(date_start, date_end)` | `WHERE SUBSTR(created_at, 1, 10) >= ? AND SUBSTR(created_at, 1, 10) <= ?` |

**File: `src/data/db_manager.py`** — 5 locations

| Line | Method | Fix |
|------|--------|-----|
| 1034 | `search_conversations()` | `SUBSTR(c.created_at, 1, 10) >= ?` |
| 1038 | `search_conversations()` | `SUBSTR(c.created_at, 1, 10) <= ?` |
| 1673 | `get_entity_distribution()` | `SUBSTR(c.created_at, 1, 10) >= ?` |
| 1676 | `get_entity_distribution()` | `SUBSTR(c.created_at, 1, 10) <= ?` |
| 1779 | `get_trc_ticket_counts()` | `SUBSTR(created_at, 1, 10) >= ? AND SUBSTR(created_at, 1, 10) <= ?` |
| 1792 | `get_tickets_for_trc()` | `SUBSTR(c.created_at, 1, 10) >= ? AND SUBSTR(c.created_at, 1, 10) <= ?` |
| 1984 | `get_ticket_count_in_range()` | `SUBSTR(created_at, 1, 10) >= ? AND SUBSTR(created_at, 1, 10) <= ?` — also remove ` + " 23:59:59"` param append |

**Parameter changes**: All callers currently pass date-only strings (`'2025-03-12'`) or `date_end + ' 23:59:59'`. After fix, all pass date-only strings (`'2025-03-12'`). Remove all `+ ' 23:59:59'` concatenations.

**LOC**: ~15 lines modified across both files

---

### Files Modified (Summary)

| File | Fix | Changes |
|------|-----|---------|
| `src/agents/supervisor.py` | A | +15 lines: DB count query in `_update_scan_progress()`, add `db_path` param |
| `src/agents/scan_orchestrator.py` | A, B | +3 lines: pass `db_path` to Supervisor. ~4 lines modified: SUBSTR date fixes |
| `src/data/db_manager.py` | B | ~7 lines modified: SUBSTR date fixes across 5 methods |

### Testing

**Unit tests** (add to `tests/test_pipeline_full.py`):

| Test | What it validates |
|------|------------------|
| `test_supervisor_db_count_overrides_memory` | Mock DB with extra rows beyond what notify_batch_complete reported → classified count reflects DB |
| `test_substr_date_includes_single_digit_hour` | Query with SUBSTR pattern includes ticket with `created_at = '2025-03-12 8:24:00'` |
| `test_substr_date_excludes_out_of_range` | Query with SUBSTR pattern excludes ticket outside date range |

**Regression**: `python -m pytest tests/ -x -q` — all existing tests pass

**Manual verification**: Launch app → run NLP scan → progress counter shows actual classified count (not stale in-memory value)

### Verification

1. `python -m pytest tests/ -x -q` — all tests pass (existing + new)
2. Launch app → NLP scan → progress counter reaches actual classified count (not stuck at partial)
3. Query DB: `SELECT COUNT(*) FROM nlp_ticket_classifications WHERE scan_id = ?` matches UI display
4. Verify tickets with single-digit hours (8:xx, 9:xx) included in scan results
5. Spot-check: `get_ticket_count_in_range()` returns 888 (not 886) for the full date range

---

## Phase 5.5 — Reporting Suite Overhaul

### Context

The product generates high-value analysis (NLP classifications, analyst reports, statistical comparisons, incident detection) but the reporting layer has three critical gaps:

1. **Analyst reports invisible** — synthesis/audit/novelty/merge stored in `analyst_reports` table but only visible in NLP Scanner tab
2. **No technical process visibility** — tokens, cost, time, batches already tracked in 6+ DB tables but never surfaced to users
3. **Single-shot reports** — AI Reports and A/B Compare use one Gemini call vs VOC's multi-phase bridge pipeline (parallel analysis → accumulator → convergence)
4. **Plain text rendering** — all reports display markdown as literal text in QTextEdit
5. **Ephemeral scheduling** — Smart Reporting QTimer lost on app restart
6. **Product goal**: robust sentiment, friction, and end-user reporting for process/product improvements

The VOC pipeline (`src/data/voc_builder.py`) is the gold standard: 7-section markdown, multi-phase bridge synthesis, evidence-based claims, graceful degradation. All other reporting pages need to match this pattern.

### Dependency Graph

```
5.5A (Foundation) ─── no dependencies
       │
       ├──> 5.5B (AI Reports & A/B)  ── needs MarkdownViewer, tech_summary, analyst_formatter
       │
       ├──> 5.5C (Smart Reporting)    ── needs report_schedules schema, MarkdownViewer
       │
       └──> 5.5D (Polish & Testing)   ── needs all above
```

5.5B and 5.5C can run in either order. 5.5D must be last.

---

### 5.5A — Foundation Layer (Session 1)

**Goal**: Extract shared utilities, add markdown rendering, build persistent scheduling schema, and surface analyst reports — all without changing page layouts yet.

#### 1. Markdown Viewer Widget

**New file**: `src/ui/widgets/markdown_viewer.py` (~180 LOC)

Wraps `QTextBrowser` (already used by DrilldownPanel at `drilldown_panel.py` line 226) with markdown→HTML conversion + Alma design token CSS.

```python
class MarkdownViewer(QTextBrowser):
    def set_markdown(self, text: str):
        html = self._md_to_html(text)
        self.setHtml(self._wrap_with_styles(html))

    def _md_to_html(self, text: str) -> str:
        # Try `markdown` package, fallback to regex converter
        # Handles: # headings, **bold**, *italic*, - lists, | tables |,
        # ``` fenced code, --- rules, > blockquotes

    def _wrap_with_styles(self, html: str) -> str:
        # CSS using theme.py tokens:
        # Headings: color: ALMA_GREEN_DARK, font-weight: 700
        # Code blocks: bg: ALMA_CREAM, border: ALMA_BORDER_LIGHT, font: Cascadia Code
        # Tables: border-collapse, ALMA_BORDER
        # Blockquotes: border-left: 3px ALMA_GREEN_LIGHT
```

#### 2. Technical Process Summary Builder

**New file**: `src/data/tech_summary_builder.py` (~250 LOC)

Aggregates all existing DB metrics for a given scan/report:

```python
def build_tech_summary(db, scan_id=None, report_run_id=None) -> dict:
    # Sources: gemini_usage, nlp_scan_runs, nlp_batches, scan_events,
    #          probe_history, smart_report_runs
    # Returns: {
    #   "gemini_usage": {tokens_in, tokens_out, cost_usd, api_calls, model},
    #   "scan_stats": {total_batches, completed, tickets, duration_ms, budget_cap, actual_cost, retries},
    #   "batch_breakdown": [{trc, batch_count, avg_latency_ms, retry_count}],
    #   "pipeline_stages": [{stage, status, duration_ms}],
    #   "cost_breakdown": {input_cost, output_cost, total, model},
    # }

def format_tech_summary_as_markdown(summary: dict) -> str:
    # Renders as:
    # ## Technical Process Summary
    # | Metric | Value |
    # | Total API Calls | 47 |
    # | Input Tokens | 2,340,561 |
    # | Output Tokens | 185,002 |
    # | Estimated Cost | $0.46 |
    # | Duration | 14m 23s |
    # | Model | gemini-2.5-flash |
    # | Batches (completed/total) | 35/35 |
    # | Average Batch Latency | 8.2s |
    # | Retries | 3 |
```

Data already exists in: `gemini_usage` (per-call), `nlp_batches` (per-batch), `nlp_scan_runs` (per-scan), `scan_events` (timeline), `smart_report_runs` (per-pipeline). No new tracking infrastructure needed.

#### 3. Analyst Report Formatter

**New file**: `src/data/analyst_report_formatter.py` (~120 LOC)

Parses JSON `content` from `analyst_reports` table into readable markdown:

```python
def format_analyst_reports_as_markdown(reports: list[dict]) -> str:
    # Maps report_type → section:
    #   synthesis → ## Cross-TRC Synthesis (shared_root_causes, systemic_issues, correlations)
    #   audit     → ## Quality Audit (quality_score, common_errors, grade distribution)
    #   novelty   → ## Novelty Validation (validated/rejected/merged counts, ticket details)
    #   merge     → ## Pattern Merge Suggestions (candidates, rationale, confidence)

def get_latest_analyst_summary(db) -> tuple[str, dict]:
    # Returns (markdown_text, raw_metrics) for most recent completed scan
```

Extracts from: `analyst_agent.py` lines 108-141 (synthesis JSON), lines 163-195 (audit JSON), lines 272-358 (novelty JSON), lines 371-420 (merge JSON).

#### 4. Persistent Scheduling Schema

**Modified file**: `src/data/db_manager.py`

```sql
CREATE TABLE IF NOT EXISTS report_schedules (
    schedule_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    page            TEXT NOT NULL,          -- 'smart_reporting', 'ai_reports'
    config_json     TEXT NOT NULL,          -- serialized pipeline config
    timezone        TEXT DEFAULT 'America/New_York',
    repeat_type     TEXT DEFAULT 'weekly',  -- daily/weekly/biweekly/monthly
    repeat_day      INTEGER DEFAULT 1,     -- day of week (0=Mon) or day of month
    repeat_time     TEXT DEFAULT '06:00',  -- HH:MM in local TZ
    enabled         INTEGER DEFAULT 1,
    last_run_at     TEXT,
    next_run_at     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
```

Methods: `save_schedule()`, `get_schedules()`, `update_schedule()`, `delete_schedule()`, `update_schedule_last_run()` (~80 LOC)

#### 5. Shared Gemini Client Factory

**New file**: `src/gemini/client_factory.py` (~60 LOC)

Deduplicate `_build_gemini_client()` (currently copied in ai_reports.py, ab_compare.py, smart_pipeline.py):

```python
def build_gemini_client(use_bridge=False) -> GeminiClient | ReportBridgeClient:
    # Loads from settings.yaml, returns bridge client for persistent subprocess
```

#### Files Summary — 5.5A

| File | Change | LOC |
|------|--------|-----|
| `src/ui/widgets/markdown_viewer.py` | **NEW** | ~180 |
| `src/data/tech_summary_builder.py` | **NEW** | ~250 |
| `src/data/analyst_report_formatter.py` | **NEW** | ~120 |
| `src/gemini/client_factory.py` | **NEW** | ~60 |
| `src/data/db_manager.py` | ADD table + 5 methods | ~80 |
| **Total** | | **~690** |

#### Verification — 5.5A

1. Unit test MarkdownViewer: feed 7-section VOC-style markdown → verify `<h2>`, `<table>`, `<code>` in HTML
2. Unit test `build_tech_summary()`: mock DB with known `gemini_usage`/`nlp_batches` rows → verify math
3. Unit test `format_analyst_reports_as_markdown()`: feed sample JSON content → verify readable markdown
4. Unit test `report_schedules` CRUD: create/read/update/delete
5. `python -m pytest tests/ -x -q` — all existing tests pass

---

### 5.5B — AI Reports & A/B Compare Pipeline Overhaul (Session 2)

**Goal**: Replace single-shot Gemini calls with multi-phase bridge pipelines; render markdown; surface analyst reports and tech summaries inline.

#### 1. AI Reports Bridge Pipeline

**New file**: `src/data/ai_report_pipeline.py` (~400 LOC)

Modeled after `VOCBuilder` (`src/data/voc_builder.py`), adapted for AI Reports:

**Phase 1 — Data Assembly**: `build_data_block()` (existing) + `build_tech_summary()` (5.5A) + `get_latest_analyst_summary()` (5.5A)

**Phase 2 — Parallel Analysis** via `ReportOrchestrator.run_parallel()`:
- Task A (priority 0): Primary report generation (selected prompt)
- Task B (priority 1): Evidence validation (cross-reference claims vs data block)
- Task C (priority 1): Recommendations extraction (distill actionable items)

**Phase 3 — Convergence**: Merge into 7-section markdown using `config/prompts/ai_report_convergence.txt`

**Graceful degradation**: If bridge pool fails → fall back to single `generate()` call (current behavior). If convergence fails → return Phase 2a primary report directly.

```python
class AIReportPipeline:
    def __init__(self, db, orchestrator=None, progress_cb=None): ...
    def plan(self, date_start, date_end) -> dict:  # cost/time estimate
    def run(self, prompt_data, date_start, date_end, trc_filter=None) -> dict:
        # Returns {report_md, tech_summary_md, analyst_md, raw_data_block}
```

#### 2. A/B Compare Bridge Pipeline

**New file**: `src/data/ab_report_pipeline.py` (~350 LOC)

**Phase 1 — Dual Data Assembly**: `compute_dataset_stats()` + `compare_datasets()` (from `src/data/ab_analysis.py`)

**Phase 2 — Parallel Analysis**:
- Task A: Statistical comparison narrative (volume, sentiment, resolution)
- Task B: Trend delta analysis (new friction themes in B, resolved from A)
- Task C: Impact assessment (which changes matter most, recommendations)

**Phase 3 — Convergence**: Merge into structured comparison report

#### 3. AI Reports Page UI Overhaul

**Modified file**: `src/ui/pages/ai_reports.py`

| Change | Description |
|--------|-------------|
| Replace `QTextEdit` output | Use `MarkdownViewer.set_markdown()` instead of `setPlainText()` |
| Replace `ReportWorker` | New `AIReportPipelineWorker` using bridge pipeline |
| Add Analyst Reports section | `CollapsibleSection` below output with formatted analyst reports |
| Add Technical Summary section | `CollapsibleSection` with tech metrics from latest run |
| Report type grouping | `QComboBox` separators: Standard / NLP Analysis / Advanced |
| Report history detail | Use `MarkdownViewer` in DrilldownPanel detail callback |
| Shared client factory | Replace local `_build_gemini_client()` |

**Layout**:
```
[Controls Card: Prompt + TRC + Date + Generate]
[Progress / Animation]
[Report Output Card: MarkdownViewer]
  [▸ Analyst Reports: CollapsibleSection]
  [▸ Technical Summary: CollapsibleSection]
[Action buttons: Copy | Save .md | Save History | Export | Chat]
[Report History Summary]
```

#### 4. A/B Compare Page UI Overhaul

**Modified file**: `src/ui/pages/ab_compare.py`

| Change | Description |
|--------|-------------|
| Replace `QTextEdit` output | Use `MarkdownViewer` |
| Replace `ABCompareWorker` | New `ABReportPipelineWorker` using bridge pipeline |
| Add report history | Currently missing — add `ReportHistorySummary` + Save to History |
| Add Technical Summary | Collapsible section with run metrics |
| Shared client factory | Replace local `_build_gemini_client()` |

#### 5. New Prompt Templates

| File | Purpose | LOC |
|------|---------|-----|
| `config/prompts/ai_report_convergence.txt` | Merge parallel analysis into 7-section report | ~50 |
| `config/prompts/ab_convergence.txt` | Merge comparative analysis into structured report | ~50 |
| `config/prompts/evidence_validation.txt` | Phase 2b: cross-reference claims vs data | ~30 |
| `config/prompts/recommendations_extract.txt` | Phase 2c: extract actionable recommendations | ~30 |

#### Files Summary — 5.5B

| File | Change | LOC |
|------|--------|-----|
| `src/data/ai_report_pipeline.py` | **NEW** | ~400 |
| `src/data/ab_report_pipeline.py` | **NEW** | ~350 |
| `src/ui/pages/ai_reports.py` | OVERHAUL | ~200 net |
| `src/ui/pages/ab_compare.py` | OVERHAUL | ~150 net |
| `config/prompts/` (4 new templates) | **NEW** | ~160 |
| **Total** | | **~1260** |

#### Verification — 5.5B

1. AI Reports + canned prompt → verify styled markdown rendering (headings, tables, code blocks)
2. AI Reports + NLP data available → Analyst Reports section visible with 4 sub-sections
3. AI Reports → Technical Summary shows correct token counts/cost from `gemini_usage`
4. Graceful degradation: kill bridge mid-pipeline → confirms fallback to single-shot
5. A/B Compare → markdown rendering + history save works
6. Open past report from history → markdown renders in DrilldownPanel
7. `python -m pytest tests/ -x -q` + `tests/test_qt_regression.py`

---

### 5.5C — Smart Reporting & Scheduling Overhaul (Session 3)

**Goal**: Modern scheduling UI with persistent DB-backed schedules, calendar day picker, timezone support, enhanced run history with click-to-view.

#### 1. Schedule Manager Service

**New file**: `src/data/schedule_manager.py` (~200 LOC)

```python
class ScheduleManager(QObject):
    run_triggered = Signal(dict)  # emits config when schedule fires

    def __init__(self, db):
        self._check_timer = QTimer()
        self._check_timer.timeout.connect(self._check_schedules)
        self._check_timer.start(60_000)  # poll every 60s

    def _check_schedules(self):
        # Query: SELECT * FROM report_schedules WHERE enabled=1 AND next_run_at <= now()
        # For each due schedule: emit run_triggered, update last_run_at + next_run_at

    def _compute_next_run(self, schedule) -> str:
        # Handle repeat_type: daily/weekly/biweekly/monthly
        # Apply timezone via zoneinfo (Python 3.9+ stdlib)
        # Supported: America/New_York, America/Chicago, America/Denver, America/Los_Angeles, UTC
```

#### 2. Smart Reporting Page UI Overhaul

**Modified file**: `src/ui/pages/smart_reporting.py` (657 → ~900 LOC)

**Pipeline Configuration Card** (enhanced):
- Add `ModernDatePicker` for specific day selection (exists at `src/ui/widgets/date_picker.py`)
- Add timezone `QComboBox` (EST/CST/MST/PST/UTC)
- Keep existing: prompt selector, NLP toggle, budget, workers

**Scheduling Card** (completely rebuilt):
- Replace ephemeral QTimer with persistent schedule management
- "Add Schedule" button → inline form:
  - Name field (text input)
  - Repeat type: QComboBox (Daily / Weekly on [day] / Biweekly on [day] / Monthly on [date])
  - Day selector: QComboBox (Mon–Sun for weekly; 1st–28th for monthly)
  - Time: QTimeEdit (HH:MM format)
  - Timezone: QComboBox (inherited from pipeline config)
  - Save / Cancel buttons
- Active schedules displayed as mini cards with toggle + delete
- "Next run: Thu Mar 12, 9:00 AM EST" per schedule

**Run History Card** (enhanced):
- Add 6th column: "Report" with clickable "View" link
- Row click → open report in DrilldownPanel (rendered as markdown via MarkdownViewer)
- Status icons: ✓ green (success), ✗ red (failed), ⟳ yellow (running)
- Expandable row → Technical Summary per run

**Analyst Reports Card** (new):
- Below pipeline config, above scheduling
- Shows latest scan's 4 analyst reports as compact summary cards
- Click → DrilldownPanel with full formatted report

#### 3. Main Window Integration

**Modified file**: `src/ui/main_window.py` (~30 LOC)

- Instantiate `ScheduleManager(db)` after DB init
- Pass to SmartReportingPage
- Connect `run_triggered` signal to pipeline execution

#### Files Summary — 5.5C

| File | Change | LOC |
|------|--------|-----|
| `src/data/schedule_manager.py` | **NEW** | ~200 |
| `src/ui/pages/smart_reporting.py` | OVERHAUL | ~350 net |
| `src/ui/main_window.py` | ADD integration | ~30 |
| `src/ui/widgets/drilldown_panel.py` | ADD run detail rendering | ~60 |
| **Total** | | **~640** |

#### Verification — 5.5C

1. Create schedule via UI → verify row in `report_schedules` table
2. Restart app → schedule reloads and displays
3. Set schedule 1 min in future → verify pipeline triggers
4. Click run history row → DrilldownPanel shows markdown report + tech summary
5. Timezone test: set PST schedule → verify `next_run_at` offset correct
6. Delete schedule → removed from DB and UI
7. `python -m pytest tests/ -x -q`

---

### 5.5D — Polish, Integration & Testing (Session 4)

**Goal**: Cross-page markdown consistency, export bar, cost visibility, run-report JOIN, comprehensive tests.

**Context**: After 5.5A-C, 3 pages use MarkdownViewer (AI Reports, A/B Compare, Smart Reporting) but 1 page still renders Gemini synthesis as plain text. No export-to-file buttons exist on report viewers. Smart Reporting history doesn't show cost. No JOIN between `smart_report_runs` and `analysis_reports`. Test coverage needs a dedicated reporting integration suite.

#### Change 1 — Markdown in Trending Topics Hypothesis Synthesis

**File**: `src/ui/pages/trending_topics.py` (line ~2369-2381)

Replace `QTextBrowser.setPlainText(gemini_text)` with `MarkdownViewer.set_markdown(gemini_text)`:

```python
# BEFORE (11 lines):
synth_body = QTextBrowser()
synth_body.setPlainText(gemini_text)  # ← plain text
synth_body.setMinimumHeight(150)
synth_body.setStyleSheet(...)

# AFTER (5 lines):
from src.ui.widgets.markdown_viewer import MarkdownViewer
synth_body = MarkdownViewer()
synth_body.set_markdown(gemini_text)
synth_body.setMinimumHeight(150)
```

**LOC**: +5, −11 = **−6 net**

---

#### Change 2 — Replace Custom HTML Builder in TRC Analytics

**File**: `src/ui/pages/trc_analytics.py` (lines 1891-2010+)

Replace the 127-line `_render_analyst_report_html()` method (manual HTML construction from JSON) with the 5.5A `analyst_report_formatter` → `md_to_html()` pipeline:

```python
def _render_analyst_report_html(self, report_id):
    """Render analyst report as styled HTML for DrilldownPanel."""
    try:
        row = self.db.conn.execute(
            "SELECT * FROM analyst_reports WHERE report_id = ?",
            (report_id,)
        ).fetchone()
        if not row:
            return "<p>Report not found.</p>"
    except Exception:
        return "<p>Error loading report.</p>"

    from src.data.analyst_report_formatter import format_analyst_reports_as_markdown
    from src.ui.widgets.markdown_viewer import md_to_html
    md = format_analyst_reports_as_markdown([dict(row)])
    return md_to_html(md) if md else "<p>No content.</p>"
```

**LOC**: +22, −127 = **−105 net** (massive simplification, consistent styling)

---

#### Change 3 — Run-Report JOIN Query

**File**: `src/data/db_manager.py` (add after `get_smart_runs`)

New method `get_smart_runs_with_reports(limit=20)` that LEFT JOINs `smart_report_runs` → `analysis_reports` and adds cost from `gemini_usage`:

```sql
SELECT r.*, ar.summary AS report_summary,
       CASE WHEN ar.full_results IS NOT NULL AND ar.full_results != '' THEN 1 ELSE 0 END AS has_full_results,
       COALESCE((SELECT SUM(cost_usd) FROM gemini_usage
                 WHERE created_at >= r.started_at AND created_at <= COALESCE(r.completed_at, r.started_at)),
                0) AS run_cost_usd
FROM smart_report_runs r
LEFT JOIN analysis_reports ar ON r.report_id = ar.report_id
ORDER BY r.started_at DESC LIMIT ?
```

**LOC**: +18

---

#### Change 4 — Cost Column in Smart Reporting History

**File**: `src/ui/pages/smart_reporting.py`

- History table: 6 → 7 columns. Insert "Cost" after "Duration" (column index 4).
- `_refresh_smart_history()`: Use `get_smart_runs_with_reports()` if available (fallback to `get_smart_runs()`). Display `run_cost_usd` formatted as `$0.46`.
- `_on_history_cell_clicked()`: Update column index for "Report" link (5 → 6).

**LOC**: +15

---

#### Change 5 — Export Action Bar on Smart Reporting Viewer

**File**: `src/ui/pages/smart_reporting.py`

Add a 3-button row below the Report Viewer content: **Copy** | **Save .md** | **Save .html**

- Copy: `QApplication.clipboard().setText(self._report_viewer.get_markdown())`
- Save .md: `QFileDialog.getSaveFileName()` → write raw markdown
- Save .html: `QFileDialog.getSaveFileName()` → write `md_to_html()` output

Buttons hidden until a report is loaded. Enabled/shown in `_view_report()`.

**LOC**: +50

---

#### Change 6 — Save .html Button on AI Reports

**File**: `src/ui/pages/ai_reports.py`

Add "Save .html" button alongside existing Copy / Save .md / Save to History / Export buttons. Same pattern as Change 5.

**LOC**: +20

---

#### Change 7 — Comprehensive Test Suite

**File**: `tests/test_reporting_suite.py` (**NEW**)

| Test Class | Tests | What It Covers |
|------------|-------|----------------|
| `TestComputeNextRun` | 10 | All recurrence types (daily/weekly/biweekly/monthly), edge cases (December rollover, past time, invalid time, unknown type) |
| `TestMarkdownEdgeCases` | 7 | Empty string, whitespace, single paragraph, unclosed code block, nested bold/italic, table without separator, 1000-item list |
| `TestAnalystFormatterRoundTrip` | 2 | JSON → markdown → HTML full pipeline, empty reports |
| `TestSmartRunsJoinQuery` | 4 | JOIN returns report summary, NULL report handling, cost aggregation, DESC ordering |
| `TestFormatNextRun` | 3 | Valid ISO, empty string, invalid ISO fallback |
| **Total** | **26** | |

**LOC**: ~180

---

#### LOC Budget

| Change | File | Net LOC |
|--------|------|---------|
| 1. MD in Hypothesis | trending_topics.py | −6 |
| 2. Replace HTML builder | trc_analytics.py | −105 |
| 3. JOIN query | db_manager.py | +18 |
| 4. Cost column | smart_reporting.py | +15 |
| 5. Export bar | smart_reporting.py | +50 |
| 6. Save .html | ai_reports.py | +20 |
| 7. Test suite | test_reporting_suite.py (NEW) | +180 |
| **Total** | | **+172 net** |

---

#### Implementation Sequence

1. `db_manager.py` (Change 3) — JOIN query, foundation for Change 4
2. `trending_topics.py` (Change 1) — standalone
3. `trc_analytics.py` (Change 2) — standalone
4. `smart_reporting.py` (Changes 4+5) — depends on Change 3
5. `ai_reports.py` (Change 6) — standalone
6. `test_reporting_suite.py` (Change 7) — tests all above

All changes applied to **both** worktree and production simultaneously.

---

#### Verification

1. `python -m pytest tests/ -q` — all 169+ existing tests pass
2. `python -m pytest tests/test_reporting_suite.py -v` — all 26 new tests pass
3. Manual: Trending Topics → hypothesis test → AI Synthesis renders styled markdown
4. Manual: TRC Analytics → analyst report in DrilldownPanel → consistent Alma-branded styling
5. Manual: Smart Reporting → history table shows Cost column → "View" → export bar works (Copy/Save .md/Save .html)
6. Manual: AI Reports → generate → Save .html button works

---

### Total Estimated Scope

| Sub-Phase | New Files | Modified Files | LOC | Session |
|-----------|-----------|---------------|-----|---------|
| 5.5A Foundation | 4 | 1 | ~690 | 1 |
| 5.5B AI Reports & A/B | 6 | 2 | ~1260 | 2 |
| 5.5C Smart Reporting | 1 | 3 | ~640 | 3 |
| 5.5D Polish & Testing | 2 | ~5 | ~710 | 4 |
| **Total** | **13** | **~11** | **~3300** | **4 sessions** |

### Key Infrastructure to Reuse

| Component | File | What It Provides |
|-----------|------|-----------------|
| `ReportOrchestrator` | `src/agents/report_orchestrator.py` | Bridge pool, PriorityQueue dispatch, stall recovery |
| `ReportBridgeClient` | `src/agents/report_bridge_client.py` | Persistent bridge (8s vs 25s per call) |
| `build_data_block()` | `src/data/report_builder.py` | Foundation for all report pipelines |
| `UsageTracker` | `src/data/usage_tracker.py` | `get_scan_cost()`, daily/weekly/monthly aggregation |
| `ModernDatePicker` | `src/ui/widgets/date_picker.py` | Calendar widget with popup |
| `CollapsibleSection` | `src/ui/widgets/collapsible_section.py` | Expandable sections for analyst/tech summaries |
| `DrilldownPanel` | `src/ui/widgets/drilldown_panel.py` | `show_reports()`, `show_widget()`, HTML detail |
| `db.get_analyst_reports()` | `src/data/db_manager.py` | Already queries analyst_reports table |
| VOC prompt format | `config/prompts/voc_synthesis.txt` | 7-section template (gold standard) |

---

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| Context drift between sessions | Living audit document + session transfer protocol |
| Design inconsistency across tabs | All tabs inherit from AnalysisPageBase + SharedFilterBar |
| Data engine API changes | Audit doc captures method signatures; validate before wiring |
| OCR false positives | Tolerance thresholds in registry specs; manual review of flagged items |
| Session runs out of context | Each phase scoped to 1 page (4-5 tabs); can split further if needed |
