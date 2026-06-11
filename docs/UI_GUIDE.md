# Alma Insights — UI Development Guide

## Architecture

### Application Shell

`MainWindow` (`src/ui/main_window.py`) is a `QMainWindow` with:
- **Top bar**: App title ("Alma Insights — RCM Issue Analysis"), Help & Feedback buttons
- **Collapsible sidebar**: 12 navigation buttons with icons + labels. Toggle via hamburger icon.
- **Content area**: `QStackedWidget` holding all 12 pages
- **Job overlay**: Semi-transparent overlay showing async job progress (`JobOverlay`)
- **Drilldown panel**: Right-edge slide-in drawer (`DrilldownPanel`) — wired to 9 pages
- **Toast notifications**: Non-blocking status messages (`ToastManager`)
- **Status bar**: Ready label, layman mode indicator, ticket count, Qt error guard alerts

### Page Index Constants

```python
PAGE_CONVERSATIONS = 0    PAGE_AB_COMPARE = 5
PAGE_DASHBOARD = 1        PAGE_SMART_REPORTING = 6
PAGE_TRENDING = 2         PAGE_SETTINGS = 7
PAGE_INCIDENTS = 3        PAGE_SOURCE_MONITOR = 8
PAGE_REPORTS = 4          PAGE_GURU = 9
                          PAGE_GEMINI_CHATS = 10
                          PAGE_DATA_WAREHOUSE = 11
```

### Boot Sequence

1. `DatabaseManager()` → `initialize()` (create tables)
2. `_build_ui()` → top bar, sidebar, content stack with all 12 pages
3. `JobQueue` + `JobOverlay` wired
4. `ToastManager` created
5. `DrilldownPanel` wired to 9 pages
6. `_setup_calendar_sync()` → date picker sync across pages
7. `_restore_settings()` → load saved UI state
8. `_load_data()` → initial data fetch

---

## Theme System (`src/ui/theme.py`)

### Color Palette

| Constant | Hex | Usage |
|----------|-----|-------|
| `ALMA_GREEN_DARK` | #03281B | Sidebar background |
| `ALMA_GREEN_MID` | #0A3D2C | Sidebar hover |
| `ALMA_GREEN_LIGHT` | #14573F | Primary accent |
| `ALMA_GREEN_SUBTLE` | #1B6B4D | Active states |
| `ALMA_CREAM` | #F3F1EC | Page backgrounds |
| `ALMA_WHITE` | #FAFAF8 | Card backgrounds |
| `ALMA_TEXT_DARK` | #1A1A1A | Primary text |
| `ALMA_TEXT_MID` | #4A4A4A | Secondary text |
| `ALMA_TEXT_LIGHT` | #7A7A7A | Muted text |
| `ALMA_TEXT_ON_DARK` | #F3F1EC | Text on dark bg |
| `ALMA_BORDER` | #D6D2CA | Card borders |
| `ALMA_BORDER_LIGHT` | #E8E5DE | Subtle borders |
| `ALMA_HOVER_LIGHT` | #EAE7E0 | Row/card hover |
| `ALMA_SUCCESS` | #16763A | Success states |
| `ALMA_WARNING` | #B45309 | Warning states |
| `ALMA_ERROR` | #C41E1E | Error states |
| `ALMA_INFO` | #1D6FA5 | Info states |

**Chart palette** (`ALMA_CHART_PALETTE`): Brand green, Blue, Amber, Purple, Pink, Teal
**Chart colors**: `ALMA_CHART_BG` (sage inset), `ALMA_CHART_GRID` (dotted grid), `ALMA_CHART_AXIS` (labels)

### Shadow Helpers

```python
from src.ui.theme import apply_card_shadow, apply_card_shadow_hover, apply_card_shadow_soft

apply_card_shadow(widget)       # resting state: blur=12, offset=(0,2)
apply_card_shadow_hover(widget) # hover/focus:   blur=20, offset=(0,6)
apply_card_shadow_soft(widget)  # inline cards:  blur=10, offset=(0,2)
```

### Table/Tree Configuration

```python
from src.ui.theme import configure_table, configure_tree

configure_table(table_widget)  # full-row selection, no focus rect, alternating rows
configure_tree(tree_widget)    # no focus rect, styled branch indicators
```

### Stylesheet

`get_stylesheet()` returns the complete QSS string. Applied once at startup in `main.py`:
```python
app.setStyleSheet(get_stylesheet())
```

Covers: `QMainWindow`, `QFrame`, `QPushButton`, `QLineEdit`, `QComboBox`, `QTabWidget`, `QTableWidget`, `QTreeWidget`, `QScrollBar`, `QMenu`, `QStatusBar`, `QLabel`, tooltips, and all named object styles (`#TopBar`, `#SidebarButton`, etc.).

---

## Page Inventory

| Idx | Page | File (LOC) | Purpose |
|-----|------|-----------|---------|
| 0 | Conversation Search | `conversation_search.py` (960) | Search/filter tickets, CSV import trigger |
| 1 | TRC Analytics | `trc_analytics.py` (2,087) | TRC dashboard, NLP scan trigger, analyst reports |
| 2 | Trending Topics | `trending_topics.py` (2,389) | TF-IDF terms, sentiment, CUSUM, forecasting |
| 3 | Incidents | `incidents_page.py` (1,564) | Poisson spike detection, incident management |
| 4 | AI Reports | `ai_reports.py` (1,607) | Report generation + prompt editor + history |
| 5 | A/B Compare | `ab_compare.py` (663) | Dataset comparison (chi-squared, Mann-Whitney U) |
| 6 | Smart Reporting | `smart_reporting.py` (1,498) | Automated adaptive reports |
| 7 | Settings | `settings_page.py` (3,515) | 4 tabs: General, AI, Integrations, Updates |
| 8 | Source Monitor | `source_monitor_page.py` (1,508) | Multi-source data pipeline dashboard |
| 9 | Guru KB | `guru_page.py` (1,134) | Knowledge base friction analysis |
| 10 | Gemini Chat | `gemini_chats_page.py` (777) | Interactive AI chat with tool-use |
| 11 | Data Warehouse | `data_warehouse_page.py` (610) | Custom SQL queries |

---

## Widget Library (46 widgets)

### Charts & Visualization

| Widget | File (LOC) | Purpose |
|--------|-----------|---------|
| Charts | `charts.py` (1,352) | QPainter-based: BarChart, BoxPlot, Heatmap, LineChart |
| Control Chart | `control_chart.py` (503) | Time-series with Poisson control bands |
| Chart Grid | `chart_grid.py` (206) | Drag-resizable chart grid with layout persistence |
| Chart Builders | `chart_builders.py` (498) | Standardized helpers for chart + legend sections |
| Trend Sparkline | `trend_sparkline.py` (98) | Mini inline line chart via QPainter |

### Panels & Drawers

| Widget | File (LOC) | Purpose |
|--------|-----------|---------|
| Drilldown Panel | `drilldown_panel.py` (1,122) | Universal right-edge overlay drawer (3 modes) |
| Chat Drilldown | `chat_drilldown.py` (1,007) | Multi-mode panel with 5 tab modes |
| Evidence Panel | `evidence_panel.py` (220) | Context-reactive right-side panel |
| TRC History Panel | `trc_history_panel.py` (235) | Volume sparkline, top issues, ngram trends |
| Guru Workbench | `guru_workbench_panel.py` (918) | Two-panel layout for Guru card management |
| Keyword Panel | `keyword_panel.py` (192) | Review panel for AI keyword suggestions |
| Smoothing Panel | `smoothing_panel.py` (194) | Accept/reject for cluster smoothing |

### Chat

| Widget | File (LOC) | Purpose |
|--------|-----------|---------|
| Chat Widget | `chat_widget.py` (920) | Report chat for drilldown follow-up |
| Message Bubble | `message_bubble.py` (301) | User/assistant chat bubbles |

### Tables & Lists

| Widget | File (LOC) | Purpose |
|--------|-----------|---------|
| Virtual Scroll Table | `virtual_scroll_table.py` (206) | Lazy-loading QAbstractTableModel |
| Taxonomy Browser | `taxonomy_browser.py` (622) | Health stats + pattern management |
| Report History | `report_history.py` (230) | Scrollable list of past runs |
| Report History Summary | `report_history_summary.py` (153) | Compact card with count + last run |
| Ticket Detail Panel | `ticket_detail_panel.py` (399) | Tabbed detail: Overview, NLP, Timeline |
| Ticket Preview Card | `ticket_preview_card.py` (92) | Compact ticket summary card |

### Controls

| Widget | File (LOC) | Purpose |
|--------|-----------|---------|
| Date Picker | `date_picker.py` (434) | Modern date picker with popup calendar |
| Shared Filter Bar | `shared_filter_bar.py` (248) | Universal filter bar for analysis pages |
| Filter Chip Bar | `filter_chip_bar.py` (154) | Active-filter pills with dismiss |
| Source Selector | `source_selector.py` (64) | Data source dropdown |
| Pagination Bar | `pagination_bar.py` (188) | Auto-hide pagination controls |
| Term Manager Panel | `term_manager_panel.py` (811) | Term management (boost, suppress, alias) |
| Explore Menu | `explore_menu.py` (196) | Quick-jump dropdown for drilldown |

### Layout & Structure

| Widget | File (LOC) | Purpose |
|--------|-----------|---------|
| Analysis Page Base | `analysis_page_base.py` (122) | Base class for analysis pages |
| Page Header | `page_header.py` (62) | Page title + subtitle + action area |
| Tab Scroll Content | `tab_scroll_content.py` (47) | Scrollable container for tab bodies |
| Collapsible Section | `collapsible_section.py` (208) | Expandable/collapsible section |
| KPI Card | `kpi_card.py` (182) | Reusable KPI snapshot card |
| Reports Tab | `reports_tab.py` (302) | Standardized reports tab template |
| Report Section Renderer | `report_section_renderer.py` (176) | Markdown → structured section cards |
| Markdown Viewer | `markdown_viewer.py` (246) | Markdown → styled HTML |
| Scan Monitor | `scan_monitor.py` (644) | Real-time scan progress with animation |
| Cost Dashboard | `cost_dashboard.py` (610) | Cost limits config tab |
| Guru Card Viewer | `guru_card_viewer.py` (662) | Guru card rendering with chrome |

### Feedback

| Widget | File (LOC) | Purpose |
|--------|-----------|---------|
| Toast | `toast.py` (200) | Slide-in notifications (success/error/info/warning) |
| Job Overlay | `job_overlay.py` (206) | Semi-transparent overlay during jobs |
| Empty State | `empty_state.py` (105) | Muted icon + message for empty views |
| Skeleton | `skeleton.py` (204) | Shimmer loading placeholders |
| Generation Animation | `generation_animation.py` (382) | Animated overlay during report gen |
| Sprout Animation | `sprout_animation.py` (183) | 6-phase storyboard growing sprout |
| Thread Renderer | `thread_renderer.py` (123) | Conversation → styled HTML |

---

## Dialog Inventory (8 dialogs)

| Dialog | File (LOC) | Purpose |
|--------|-----------|---------|
| Ingestion | `ingestion_dialog.py` (601) | Preflight checklist + live log for API import |
| Mapping Preview | `mapping_preview_dialog.py` (296) | Review/edit Gemini-proposed CSV column mappings |
| Prompt Editor | `prompt_editor.py` (345) | Create/edit AI report prompts |
| Help | `help_dialog.py` (238) | Documentation, feedback, tool overview |
| Intervention | `intervention_dialog.py` (233) | Add/edit intervention events |
| Term Manager | `term_manager_dialog.py` (346) | 3-tab term intelligence management |
| Expanded Section | `expanded_section_dialog.py` (95) | Fullscreen view of collapsible content |

---

## Common UI Patterns

### Threading

**Rule**: Never modify widgets from worker threads. Always use signals/slots.

```python
# WRONG — crashes intermittently:
def worker_thread():
    self.label.setText("done")

# RIGHT — emit signal, connect in main thread:
class MyPage(QWidget):
    data_ready = Signal(str)

    def __init__(self):
        self.data_ready.connect(self._on_data)

    def _start_work(self):
        worker = QThread(target=self._fetch)
        worker.start()

    def _fetch(self):
        result = expensive_operation()
        self.data_ready.emit(result)  # signal to main thread

    def _on_data(self, result):
        self.label.setText(result)  # safe — main thread
```

For one-off calls: `QMetaObject.invokeMethod(widget, "method", Qt.QueuedConnection)`

### Job Queue

Long-running operations go through `JobQueue` (sequential executor):
```python
from src.data.job_queue import JobQueue, JobDescriptor, CallableWorker

job = JobDescriptor(
    job_id="import_csv",
    name="CSV Import",
    description="Importing tickets from file...",
    create_worker=lambda: CallableWorker(my_callable, arg1, arg2),
)
self._job_queue.submit(job)
```

### Drilldown Panel

Overlay drawer for ticket details. Pages wire via `page.set_drilldown_panel(drilldown)`.

```python
# Open drilldown with ticket detail
self._drilldown.show_ticket(ticket_id, db)

# Open with custom widget
self._drilldown.show_widget(my_widget, title="Custom View")
```

### Empty States

```python
from src.ui.widgets.empty_state import EmptyState

empty = EmptyState("No data available", "Import tickets to get started")
layout.addWidget(empty)
```

### Loading Skeletons

```python
from src.ui.widgets.skeleton import SkeletonKPI, SkeletonTable

skeleton = SkeletonKPI()  # shimmer animation placeholder
layout.addWidget(skeleton)
# Replace with real widget when data loads
```

---

## Layman Mode (`src/ui/layman_mode.py`)

Translates technical metrics into plain language when enabled:

| Technical | Plain Language |
|-----------|---------------|
| VADER Compound | Customer Mood Score |
| CUSUM | Gradual Change Detector |
| Poisson lambda | Expected Daily Volume |
| TF-IDF Score | Term Importance |
| z-score | Unusual Activity Score |
| p-value | Statistical Confidence |

Controlled by `display.layman_mode` in settings. Check via `is_layman_mode()`.

```python
from src.ui.layman_mode import translate_label, is_layman_mode

label = translate_label("CUSUM Drift Detected", is_layman_mode())
# → "Gradual Change Detector Drift Detected" (if layman mode on)
```

---

## Error Handling (`src/ui/qt_error_guard.py`)

`QtErrorGuard` catches silent Qt failures that would otherwise be swallowed:
- Python exceptions in Qt overrides (paint, delegates, event handlers)
- Infinite recursion in delegates/paint methods
- Rapid memory growth (checked every 5s)

Wired at startup in `main.py`. Errors surface in status bar.

```python
from src.ui.qt_error_guard import QtErrorGuard

guard = QtErrorGuard(app)
guard.attach_to_status_bar(status_label)
```

---

## Adding a New Page

1. **Create page file** in `src/ui/pages/`:
   ```python
   from PySide6.QtWidgets import QWidget, QVBoxLayout
   from src.ui.widgets.page_header import PageHeader

   class MyNewPage(QWidget):
       def __init__(self, db, parent=None):
           super().__init__(parent)
           self.db = db
           layout = QVBoxLayout(self)
           layout.addWidget(PageHeader("My Page", "Description"))
           # ... add widgets
   ```

2. **Register in MainWindow** (`src/ui/main_window.py`):
   - Add `PAGE_MY_PAGE = N` constant
   - Import the page class
   - Create instance in `_build_content_area()`
   - Add sidebar button in `_build_sidebar()`
   - Wire drilldown panel if needed

3. **Follow patterns:**
   - Use `PageHeader` for consistent title
   - Use `SharedFilterBar` for date/TRC filters
   - Use `JobQueue` for async operations
   - Never touch widgets from worker threads

---

## See Also

- `CLAUDE.md` — Quick reference (UI Pages section)
- `docs/ARCHITECTURE.md` — System architecture (Layer 1: UI)
- `docs/CONFIGURATION.md` — Settings reference (display section)
- `src/ui/INDEX.md` — Full API signatures for UI shell modules

---

## Design System (2026-06-11 redesign, P3)

The theme was split into a structured design system under `src/ui/design/`;
`src/ui/theme.py` is now a compatibility facade (every existing
`from src.ui.theme import *` call site keeps working).

| Module | Contents |
|--------|----------|
| `design/tokens.py` | `LIGHT` palette dict (semantic keys, dark-ready), `SPACING` 4px grid, `RADIUS`, `TYPE_SCALE`, `ELEVATION`, and the flat legacy `ALMA_*` constants derived from `LIGHT`. |
| `design/icons.py` | `icon(name, size=18, color=None) -> QIcon` — inline-SVG glyph registry (Feather-style strokes) rendered at 2x via QtSvg. No emoji / symbol-font glyphs anywhere new (offscreen tofu + professionalism). `glyph_names()` lists the set. |
| `design/anim.py` | `DUR` / `EASE` presets + `fade_in(widget)`. Page fade and sidebar collapse use these. |
| `design/qss.py` | `build_stylesheet(theme=None)` = `_legacy_sections()` (the original stylesheet, moved verbatim) + `_extras()` (menus, message boxes, icon sizing). New styling goes in new section functions — do not grow the legacy block. |

Rules:
- New colors enter `LIGHT` with a semantic key; the flat `ALMA_*` alias is
  only added when legacy code needs to import it.
- Sidebar nav entries come from `src/ui/app_modes.py` PageSpecs whose
  `icon` field is a glyph NAME (validated by `tests/test_icons.py`).
- QIcons cannot be recolored by QSS — bake the color at creation
  (`icon("home", 18, "#E7E4DC")` for on-dark sidebar use).
