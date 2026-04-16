# src/ui/widgets/

> Reusable UI building blocks: chart widgets (bar, box, heatmap, line, sparkline), layout scaffolding (page header, filter bar, tab scroll, KPI cards), interactive panels (drill-down, chat, term manager), and utility widgets (toast, skeleton, animation, pagination).

## Module Index

### analysis_page_base.py
> Base class for all tabbed analysis pages (TRC Analytics, Trending Topics, Incidents), providing consistent PageHeader, SharedFilterBar, and QTabWidget skeleton.

**Public API:**
- `AnalysisPageBase(QWidget)` — base class for tabbed analysis pages
  - `header` — property returning PageHeader
  - `filter_bar` — property returning SharedFilterBar
  - `tab_widget` — property returning QTabWidget
  - `add_tab(widget: QWidget, label: str) -> int` — add a tab
  - `set_drilldown_panel(panel)` — wire the DrilldownPanel
  - `drilldown` — property accessing the drilldown panel

**Depends on:** `src.ui.widgets.page_header`, `src.ui.widgets.shared_filter_bar`, `src.ui.theme`
**Depended by:** `src.ui.pages.incidents_page`, `src.ui.pages.trending_topics`, `src.ui.pages.trc_analytics`, `tests.test_phase5_regression`

---

### chart_builders.py
> Standardized helpers for building chart + legend sections: ChartLegendSection, TRCDropdownSelector, and build_chart_section factory.

**Public API:**
- `ChartLegendSection(CollapsibleSection)` — collapsible section displaying a color-coded series legend
  - `update_legend(series_labels, colors=None)` — update legend entries
- `TRCDropdownSelector(QWidget)` — Lightdash-style dropdown checklist for TRC selection
  - `set_trc_data(sentiment_data)` — populate with TRC data
  - `selected_trcs` — property returning list of selected TRCs
  - `select_top_n(n)` — select the top N TRCs
- `SERIES_COLORS` — color palette list for chart series

**Depends on:** `src.ui.theme`, `src.ui.widgets.collapsible_section`, `src.ui.widgets.charts`
**Depended by:** `src.ui.pages.trending_topics`, `src.ui.widgets.taxonomy_browser`

---

### chart_grid.py
> Draggable chart grid layout with view mode (clean, static) and edit mode (drag handles, dashed borders); layout state saved to DB.

**Public API:**
- `ChartWrapper(QFrame)` — wraps a chart widget with title bar, collapse toggle, and drag handle
  - `set_edit_mode(editing: bool)` — toggle edit mode
  - `chart_id` — property returning the chart's ID
  - `is_collapsed` — property checking collapsed state
  - `set_collapsed(collapsed)` — set collapsed state
- `ChartGrid(QWidget)` — grid layout for chart wrappers
  - `add_chart(widget, title: str, chart_id: str, ...)` — add a chart to the grid
  - `toggle_edit_mode()` — toggle grid edit mode

**Depends on:** `src.ui.theme`
**Depended by:** *(none currently)*

---

### charts.py
> QPainter-based chart widgets: BarChart, BoxPlot, Heatmap, LineChart, Sparkline, DualSparkline, and ChartModeSwitcher. No external charting libraries.

**Public API:**
- `BarChartWidget(QWidget)` — horizontal bar chart with gradient fills and pagination
  - `bar_clicked` — Signal(str) emitting clicked label
  - `set_data(data, max_items=None)` — set chart data
  - `set_page(page: int, page_size: int = None)` — set visible page
- `BoxPlotWidget(QWidget)` — box plot chart with whiskers and pagination
  - `set_data(data, max_items=None, min_samples=5)` — set chart data
  - `set_page(page: int, page_size: int = None)` — set visible page
- `HeatmapWidget(QWidget)` — heatmap with color-coded cells and pagination
  - `row_clicked` — Signal(str) emitting clicked row label
  - `set_data(y_labels, x_labels, values)` — set heatmap data
  - `set_page(page: int, page_size: int = None)` — set visible page
- `ChartMode(Enum)` — chart rendering mode: `LINE`, `AREA`, `BAR`, `STEPPED`
- `LineChartWidget(QWidget)` — multi-series line/area/bar/stepped chart with crosshair hover
  - `point_clicked` — Signal(str, int) emitting (series, index)
  - `set_chart_mode(mode: ChartMode)` — switch rendering mode
  - `set_data(data, y_min=None, y_max=None, show_zero_line=True, show_bg_tint=False)` — set chart data
- `ChartModeSwitcher(QWidget)` — toggle buttons for switching between chart modes
  - `mode_changed` — Signal(ChartMode)
- `SparklineWidget(QWidget)` — compact sparkline for inline trend display
  - `set_data(values, color=None)` — set sparkline data
- `DualSparklineWidget(QWidget)` — dual-series sparkline (e.g. volume vs sentiment)
  - `set_data(values_a, values_b, color_a=None, color_b=None, ...)` — set dual sparkline data

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.pages.trc_analytics`, `src.ui.pages.trending_topics`, `src.ui.widgets.chart_builders`, `src.ui.widgets.cost_dashboard`, `src.ui.widgets.taxonomy_browser`

---

### chat_widget.py
> Report follow-up chat widget with Gemini-powered Q&A, data-grounded drilldown enrichment, conversation history, and chat bubble rendering.

**Public API:**
- `ChatWorker(QThread)` — background thread for Gemini chat follow-up
  - `finished` — Signal(str)
  - `error` — Signal(str)
- `ChatBubble(QFrame)` — styled chat message bubble (user or assistant)
- `ReportChatWidget(QWidget)` — full chat interface widget
  - `set_gemini_client(client)` — set the Gemini client
  - `set_report_context(data_block_text, report_text)` — set report context for follow-up
  - `set_db_context(db_path, date_start, date_end, scan_id=None)` — set DB context for data grounding
  - `load_chat_history(history)` — restore chat history from saved data
  - `get_chat_history() -> list` — return current chat history
  - `clear()` — clear the chat interface

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.pages.ai_reports`, `src.ui.pages.ab_compare`

---

### collapsible_section.py
> Collapsible section with clickable header, expand/collapse animation, optional expand-to-dialog button, and config-driven default state.

**Public API:**
- `CollapsibleSection(QFrame)` — collapsible card section
  - `section_key` — property returning the config key
  - `add_widget(widget)` — add a widget to the content area
  - `add_layout(layout)` — add a layout to the content area
  - `content_layout` — property returning the inner QVBoxLayout
  - `toggle()` — toggle collapsed/expanded state
  - `set_collapsed(collapsed: bool)` — programmatically set state
  - `is_collapsed() -> bool` — check if currently collapsed
  - `get_default_collapsed(section_key: str) -> bool` — static: read default state from settings

**Depends on:** `src.ui.theme`, `src.ui.dialogs.expanded_section_dialog`
**Depended by:** `src.ui.main_window`, `src.ui.pages.ai_reports`, `src.ui.pages.ab_compare`, `src.ui.pages.smart_reporting`, `src.ui.pages.trc_analytics`, `src.ui.pages.trending_topics`, `src.ui.widgets.chart_builders`, `src.ui.widgets.cost_dashboard`, `src.ui.widgets.taxonomy_browser`

---

### control_chart.py
> QPainter-based time-series control chart with Poisson-based theta-1/theta-2 bands, CUSUM indicator bar, and intervention marker overlays.

**Public API:**
- `ControlChartWidget(QWidget)` — time-series control chart
  - `set_data(trc_result: dict)` — set chart data from incident scan results
  - `set_interventions(interventions: list)` — set intervention markers
  - `clear_data()` — clear chart data
- `INTERVENTION_COLORS` — dict mapping intervention categories to colors

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.pages.incidents_page`

---

### cost_dashboard.py
> Cost dashboard tab for NLP Scanner: cost limits config, token usage cards, cost history bar chart, and Gemini plan reference.

**Public API:**
- `CostDashboard(QWidget)` — full cost dashboard widget
  - `set_active_scan(scan_id)` — set the active scan for cost tracking
  - `refresh()` — refresh cost data from usage tracker

**Depends on:** `src.ui.theme`, `src.ui.widgets.charts`, `src.ui.widgets.collapsible_section`, `src.data.usage_tracker`
**Depended by:** `src.ui.pages.settings_page`

---

### date_picker.py
> Modern date picker widget with button-triggered calendar popup, replacing the native QDateEdit with a styled, clickable date display.

**Public API:**
- `ModernDatePicker(QWidget)` — date picker with calendar popup
  - `date_changed` — Signal(QDate) emitted when user picks a new date
  - `date() -> QDate` — return the current date
  - `setDate(date: QDate)` — set the date (no signal emission)
  - `set_date(date: QDate)` — alias for setDate
  - `get_date_string() -> str` — return date as "yyyy-MM-dd" string
  - `setDisplayFormat(fmt: str)` — set the date display format
  - `setCalendarPopup(enabled: bool)` — enable/disable calendar popup
  - `setMinimumDate(date: QDate)` — set minimum selectable date
  - `setMaximumDate(date: QDate)` — set maximum selectable date
- `CalendarPopup(QWidget)` — popup calendar widget (internal)

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.pages.ai_reports`, `src.ui.pages.conversation_search`, `src.ui.pages.trending_topics`, `src.ui.pages.trc_analytics`, `src.ui.dialogs.ingestion_dialog`, `src.ui.dialogs.intervention_dialog`, `src.ui.widgets.shared_filter_bar`

---

### drilldown_panel.py
> Overlay drawer that slides in from the right edge; supports ticket list, conversation thread viewer, report history, and arbitrary widget embedding.

**Public API:**
- `DrilldownPanel(QFrame)` — overlay drill-down drawer
  - `panel_opened` — Signal()
  - `panel_closed` — Signal()
  - `show_tickets(title: str, subtitle: str, tickets: list)` — show a ticket list
  - `show_conversation(conv: dict, ticket_list: list = None, index: int = 0, ...)` — show a conversation thread
  - `show_reports(title: str, subtitle: str, reports: list, ...)` — show report history
  - `show_widget(title: str, subtitle: str, widget: QWidget)` — embed an arbitrary widget
  - `show_pattern(pattern_data: dict)` — show a sub-taxonomy pattern detail
  - `close_panel()` — close the drawer
  - `is_open() -> bool` — check if the panel is open

**Depends on:** `src.ui.theme`, `src.ui.widgets.thread_renderer`, `src.ui.widgets.pagination_bar`
**Depended by:** `src.ui.main_window`

---

### empty_state.py
> Muted placeholder widget shown when a chart or table has no data; supports optional heading, description, and CTA button.

**Public API:**
- `EmptyState(QWidget)` — empty state placeholder
  - `action_clicked` — Signal() emitted when CTA button is clicked
  - `ICONS` — dict mapping icon names to emoji characters

**Depends on:** *(PySide6 only, no src imports)*
**Depended by:** `src.ui.pages.conversation_search`, `src.ui.pages.incidents_page`, `src.ui.pages.trending_topics`, `src.ui.widgets.reports_tab`, `src.ui.widgets.guru_workbench_panel`, `src.ui.widgets.taxonomy_browser`

---

### filter_chip_bar.py
> Horizontal bar of active-filter pills with dismiss buttons; auto-hides when no filters are active.

**Public API:**
- `FilterChipBar(QWidget)` — active filter chip bar
  - `filter_removed` — Signal(str) emitted with filter key on dismiss
  - `all_cleared` — Signal() emitted when "Clear all" is clicked
  - `set_filters(filters: dict[str, str])` — set all active filters
  - `clear_all()` — remove all filter chips
  - `add_filter(key: str, value: str)` — add a single filter chip
  - `remove_filter(key: str)` — remove a specific filter chip

**Depends on:** *(PySide6 only, no src imports)*
**Depended by:** `src.ui.pages.conversation_search`, `src.ui.pages.trending_topics`, `src.ui.pages.trc_analytics`

---

### generation_animation.py
> Animated overlay for the report output area during Gemini generation: typewriter status messages, spinning emoji, rotating quips, and elapsed time clock.

**Public API:**
- `TypewriterLabel(QLabel)` — typewriter-style animated label
  - `type_text(text: str, char_delay_ms: int = 22)` — animate text typing
  - `fade_out(on_done=None)` — fade out the label
  - `stop()` — stop current animation
- `SpinnerWidget(QWidget)` — spinning emoji with trailing dots
  - `start()` — start spinning
  - `stop()` — stop spinning
- `ElapsedClockWidget(QLabel)` — elapsed time counter
  - `start()` — start the clock
  - `stop()` — stop the clock
- `GenerationAnimationWidget(QWidget)` — composite animation widget
  - `start_animation()` — start all animation components
  - `stop_animation()` — stop all animation components
  - `update_status(msg: str)` — update the status message

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.pages.ai_reports`

---

### guru_card_viewer.py
> Renders a Guru card in-app with Guru-style chrome, verification status, and optional redline overlay for proposed edits.

**Public API:**
- `GuruCardViewer(QTextBrowser)` — Guru card HTML renderer
  - `set_card(card: dict)` — render a Guru card
  - `set_redlines(redlines: list[dict])` — apply redline edit overlays
  - `set_card_with_redlines(card: dict, redlines: list[dict])` — set card and redlines together
  - `get_card_html() -> str` — return the rendered HTML
  - `get_card_markdown() -> str` — return card content as markdown
  - `clear_content()` — clear the viewer
- `guru_card_to_html(card: dict) -> str` — standalone card-to-HTML converter
- `GAP_LABELS` — dict mapping gap score ranges to labels

**Depends on:** `src.ui.theme`, `src.ui.widgets.markdown_viewer`
**Depended by:** `src.ui.pages.guru_page`, `src.ui.widgets.guru_workbench_panel`, `tests.test_guru_card_viewer`

---

### guru_workbench_panel.py
> Two-panel workbench for SOP teams: card selection, TRC scoping, reference linking, Claude analysis, and redline draft viewing.

**Public API:**
- `GuruWorkbenchPanel(QWidget)` — card analysis workbench
  - `set_guru_client(client)` — set the Guru API client
  - `set_drilldown_panel(panel)` — wire the drill-down panel
  - `refresh()` — refresh card list and state
  - `get_selected_cards() -> list[str]` — return selected card IDs
  - `get_selected_trcs() -> list[str]` — return selected TRC codes
  - `get_reference_cards() -> list[str]` — return reference card IDs
- `AnalysisWorker(QThread)` — background thread for Claude-powered card analysis
- `score_to_gap_label(score: float) -> tuple[str, str]` — convert gap score to label/color

**Depends on:** `src.ui.theme`, `src.ui.widgets.guru_card_viewer`, `src.ui.widgets.empty_state`
**Depended by:** `src.ui.pages.guru_page`, `tests.test_guru_workbench`

---

### job_overlay.py
> Semi-transparent dark-green overlay displayed during job queue execution with animated sprout symbol, current job description, and compact job stack.

**Public API:**
- `JobOverlay(QWidget)` — full-content-area loading overlay
  - `show_overlay()` — show the overlay with fade-in
  - `hide_overlay()` — hide the overlay with fade-out
  - `update_status(description: str)` — update the status text
  - `update_job_list(jobs: list)` — update the job stack display

**Depends on:** `src.ui.widgets.sprout_animation`
**Depended by:** `src.ui.main_window`

---

### keyword_panel.py
> Review panel for AI-suggested keyword suppressions and concept map additions with accept/reject per-suggestion.

**Public API:**
- `KeywordReviewPanel(QWidget)` — keyword suggestion review panel
  - `keywords_applied` — Signal(dict) with `{"suppress": [...], "add": [...]}`
  - `dismissed` — Signal() emitted when panel is dismissed
  - `set_suggestions(suppress=None, add_to_map=None)` — populate suggestions

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.pages.trending_topics`

---

### kpi_card.py
> Reusable KPI snapshot card with title, value, delta indicator, and subtitle; plus KPICardRow for horizontal card layout.

**Public API:**
- `KPICard(QFrame)` — single KPI snapshot card
  - `set_value(value: str)` — set the main value
  - `set_delta(delta_text: str, direction: str = "neutral")` — set delta with direction
  - `set_subtitle(text: str)` — set the subtitle text
  - `set_title(text: str)` — set the title text
  - `set_accent(color_name: str)` — set the accent border color
  - `value_label` — property returning the value QLabel
  - `delta_label` — property returning the delta QLabel
  - `subtitle_label` — property returning the subtitle QLabel
- `KPICardRow(QWidget)` — horizontal row of KPI cards
  - `add_card(card: KPICard) -> KPICard` — add a card to the row
  - `cards` — property returning list of cards
  - `card(index: int) -> KPICard` — get card by index
  - `apply_accent_cycle()` — apply rotating accent colors

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.pages.incidents_page`, `src.ui.pages.trending_topics`, `src.ui.pages.trc_analytics`, `src.ui.widgets.reports_tab`

---

### markdown_viewer.py
> QTextBrowser subclass that renders markdown as styled HTML using the markdown library with tables, fenced_code, and nl2br extensions.

**Public API:**
- `MarkdownViewer(QTextBrowser)` — markdown rendering widget
  - `set_markdown(text: str)` — render markdown text
  - `get_markdown() -> str` — return the raw markdown source
  - `clear_content()` — clear the viewer

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.pages.ai_reports`, `src.ui.pages.ab_compare`, `src.ui.pages.smart_reporting`, `src.ui.pages.trc_analytics`, `src.ui.pages.trending_topics`, `src.ui.widgets.reports_tab`, `src.ui.widgets.guru_card_viewer`

---

### page_header.py
> Standardized page title with optional subtitle and right-aligned action button area.

**Public API:**
- `PageHeader(QWidget)` — page header building block
  - `set_title(text: str)` — update the title
  - `set_subtitle(text: str)` — update the subtitle
  - `add_action(widget: QWidget)` — add an action widget to the right side

**Depends on:** *(PySide6 only, no src imports)*
**Depended by:** `src.ui.widgets.analysis_page_base`

---

### pagination_bar.py
> Compact pagination bar with prev/next buttons, page indicator, and item range display; auto-hides when total items fit in one page.

**Public API:**
- `PaginationBar(QWidget)` — compact pagination bar
  - `page_changed` — Signal(int) emitted with 0-indexed page number
  - `page` — property returning current page
  - `page_size` — property returning items per page
  - `total_pages` — property returning total page count
  - `set_total(total: int)` — set total item count
  - `set_page_size(size: int)` — set items per page
  - `set_page(page: int)` — navigate to a specific page
  - `reset()` — reset to page 0
  - `page_slice() -> tuple` — return `(start, end)` slice for current page

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.pages.incidents_page`, `src.ui.pages.trending_topics`, `src.ui.pages.trc_analytics`, `src.ui.widgets.drilldown_panel`, `src.ui.widgets.term_manager_panel`

---

### report_history.py
> Scrollable list of past analysis reports for a specific page, with lazy loading.

**Public API:**
- `ReportHistoryWidget(QWidget)` — scrollable report history list
  - `report_selected` — Signal(int) emitted with report_id on click
  - `refresh()` — reload reports from DB

**Depends on:** `src.ui.theme`
**Depended by:** *(none currently; superseded by report_history_summary + ReportsTab)*

---

### report_history_summary.py
> Compact single-row summary card showing report count and last run time, with "View All" button that triggers drilldown.

**Public API:**
- `ReportHistorySummary(QFrame)` — compact report history summary card
  - `view_all_clicked` — Signal() emitted on "View All" click or double-click
  - `refresh()` — reload report data from DB
  - `get_reports_for_drilldown() -> list` — return reports list for drilldown panel

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.pages.ai_reports`, `src.ui.pages.ab_compare`, `src.ui.pages.trending_topics`, `src.ui.widgets.reports_tab`

---

### reports_tab.py
> Standardized "Reports" tab used as the last tab on every analysis page, with KPI snapshot row, report history list, export buttons, and DrilldownPanel integration.

**Public API:**
- `ReportsTab(TabScrollContent)` — reports tab building block
  - `set_drilldown_panel(panel)` — wire the drill-down panel
  - `kpi_row` — property returning the KPICardRow
  - `refresh()` — reload report data

**Depends on:** `src.ui.widgets.kpi_card`, `src.ui.widgets.tab_scroll_content`, `src.ui.widgets.report_history_summary`, `src.ui.widgets.empty_state`, `src.ui.widgets.markdown_viewer`, `src.ui.theme`
**Depended by:** `src.ui.pages.incidents_page`, `src.ui.pages.trending_topics`, `src.ui.pages.trc_analytics`

---

### scan_monitor.py
> Live scan monitoring widget for NLP classification: sprout animation with progress, preflight checklist, per-batch progress rows, runtime timer, and bridge/agent/token status.

**Public API:**
- `ScanStatusPanel(QWidget)` — two-state panel: idle (estimates) vs running (live status)
  - `show_idle()` — switch to idle/estimate view
  - `show_running()` — switch to running view
  - `set_estimates(tickets=0, time_min=0.0, tokens=0, cost=0.0)` — set preflight estimates
  - `update_bridge(text)` — update bridge status
  - `update_agents(text)` — update agent status
  - `update_tokens(text)` — update token status
- `BatchProgressRow(QWidget)` — per-batch progress indicator
  - `set_progress(pct, elapsed_text="")` — update progress
  - `set_complete(elapsed_text="")` — mark as complete
  - `set_error()` — mark as errored
- `ScanMonitorWidget(QWidget)` — full scan monitor panel
  - `start_monitoring(scan_id)` — begin monitoring a scan
  - `stop_monitoring()` — stop monitoring
  - `set_error_state(message="Error")` — show error state
  - `update_agent_status(health_data)` — update agent health display

**Depends on:** `src.ui.theme`, `src.ui.widgets.sprout_animation`
**Depended by:** `src.ui.pages.trc_analytics`

---

### shared_filter_bar.py
> Universal horizontal filter bar for analysis pages with date pickers, combo filters, and action buttons; emits signals on filter changes.

**Public API:**
- `SharedFilterBar(QWidget)` — horizontal filter bar
  - `filters_changed` — Signal(dict) emitted on any filter change
  - `action_triggered` — Signal(str) emitted on action button click
  - `add_primary_action(text: str, callback=None)` — add the primary action button
  - `add_date_range(date_from: QDate = None, date_to: QDate = None)` — add from/to date pickers
  - `add_combo_filter(key: str, label: str, options: list[str])` — add a combo filter
  - `add_custom_widget(label: str, widget: QWidget)` — add a custom widget
  - `add_action_button(text: str, callback=None, primary: bool = True)` — add an action button
  - `get_filters() -> dict` — return current filter values
  - `set_date_range(date_from: QDate, date_to: QDate)` — set date range
  - `set_combo_value(key: str, value: str)` — set a combo value
  - `set_combo_items(key: str, items: list[str])` — set combo options
  - `get_combo(key: str) -> QComboBox | None` — get a combo by key
  - `get_date_from() -> ModernDatePicker | None` — get the from-date picker
  - `get_date_to() -> ModernDatePicker | None` — get the to-date picker

**Depends on:** `src.ui.theme`, `src.ui.widgets.date_picker`
**Depended by:** `src.ui.widgets.analysis_page_base`, `tests.test_phase5_regression`

---

### skeleton.py
> Shimmer loading placeholders for KPI cards, tables, and chart areas using QTimer-driven gradient sweep.

**Public API:**
- `SkeletonRect(QWidget)` — single shimmer rectangle (atomic building block)
- `SkeletonGroup(QWidget)` — composite skeleton layout (rows of shimmer rectangles)

**Depends on:** *(PySide6 only, no src imports)*
**Depended by:** `src.ui.pages.incidents_page`, `src.ui.pages.trending_topics`, `src.ui.pages.trc_analytics`

---

### smoothing_panel.py
> Accept/reject panel for AI cluster smoothing suggestions, showing statistical and AI labels side by side.

**Public API:**
- `SmoothingReviewPanel(QWidget)` — smoothing suggestion review panel
  - `suggestions_applied` — Signal(dict) emitted with accepted suggestions
  - `dismissed` — Signal() emitted when panel is dismissed
  - `set_suggestions(suggestions)` — populate with AI suggestions

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.pages.trending_topics`

---

### sprout_animation.py
> QPainter-based 6-phase discrete storyboard animation of a growing sprout, used as the brand loading symbol.

**Public API:**
- `SproutAnimation(QWidget)` — animated sprout loading symbol
  - `start()` — start the animation loop
  - `stop()` — stop the animation
  - `set_size(size: int)` — set the animation canvas size

**Depends on:** *(PySide6 only, no src imports)*
**Depended by:** `src.ui.widgets.job_overlay`, `src.ui.widgets.scan_monitor`

---

### tab_scroll_content.py
> Scrollable container used as the body of each tab in analysis pages, providing consistent margins, background, and scroll behavior.

**Public API:**
- `TabScrollContent(QScrollArea)` — scrollable tab content area
  - `content_layout` — property returning the inner QVBoxLayout
  - `scroll_to_top()` — scroll to the top
  - `add_stretch()` — add a stretch at the bottom

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.pages.incidents_page`, `src.ui.pages.trending_topics`, `src.ui.pages.trc_analytics`, `src.ui.widgets.reports_tab`

---

### taxonomy_browser.py
> Sub-taxonomy browser tab for NLP Scanner: health stat cards, pattern growth chart, taxonomy tree, and latest findings section.

**Public API:**
- `TaxonomyBrowser(QWidget)` — full taxonomy browser tab
  - `pattern_selected` — Signal(dict) emitted when a taxonomy row is clicked
  - `refresh()` — reload taxonomy data from DB

**Depends on:** `src.ui.theme`, `src.ui.widgets.charts`, `src.ui.widgets.chart_builders`, `src.ui.widgets.collapsible_section`, `src.ui.widgets.empty_state`
**Depended by:** `src.ui.pages.trc_analytics`

---

### term_manager_panel.py
> Embeddable three-tab widget for term management: active terms, discovered candidates, and aliases/concepts; all tables paginated.

**Public API:**
- `TermManagerPanel(QWidget)` — embeddable term manager widget
  - `terms_changed` — Signal() emitted when terms are modified
  - `refresh()` — reload term data from DB

**Depends on:** `src.ui.theme`, `src.ui.widgets.pagination_bar`
**Depended by:** `src.ui.pages.trending_topics`

---

### thread_renderer.py
> Converts raw conversation thread text into styled HTML and builds metadata strings from conversation dicts; shared by the drill-down panel and conversations page.

**Public API:**
- `render_thread_html(thread_text: str) -> str` — convert raw thread text to styled HTML
- `build_meta_text(conv: dict) -> str` — build metadata string from a conversation dict

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.widgets.drilldown_panel`

---

### toast.py
> Slide-in toast notifications anchored to the bottom-right of the main window; supports success, error, info, and warning types with auto-dismiss.

**Public API:**
- `ToastWidget(QWidget)` — single toast notification widget
  - `slide_in(target_pos: QPoint)` — animate the toast into view
- `ToastManager` — manages toast stack, positioning, and lifecycle
  - `show_toast(message: str, icon: str = None, toast_type: str = "success", duration_ms: int = 4000)` — show a new toast

**Depends on:** *(PySide6 only, no src imports)*
**Depended by:** `src.ui.main_window`

---

### chat_drilldown.py
> Multi-mode tabbed panel (Monitor, Chats, Projects, Reports, Incidents) embedded in DrilldownPanel. Shows live tool calls, sessions, reports.

**Public API:**
- `ChatDrilldown(db, parent=None)` — QWidget with 5 tab modes
  - `refresh()` — reload all tabs

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.main_window`, `src.ui.pages.gemini_chats_page`

---

### evidence_panel.py
> Context-reactive right-side panel for Analysis Canvas. Displays ticket previews, trend sparklines, and report metadata via Qt signals.

**Public API:**
- `EvidencePanel(parent=None)` — QWidget for context-reactive evidence display
  - `show_ticket(ticket_data)` — display ticket preview
  - `show_trend(trend_data)` — display trend sparkline
  - `clear()` — reset to empty state

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.pages.trc_analytics`

---

### explore_menu.py
> Quick-jump dropdown menu with live count badges for drilldown panel modes (All Chats, Projects, Reports, Incidents, Monitor).

**Public API:**
- `ExploreDropdown(parent=None)` — QFrame with clickable menu items
  - `mode_selected` — Signal(str) emitted when user picks a mode

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.widgets.drilldown_panel`

---

### message_bubble.py
> Chat bubbles matching Cambric mockups. User bubbles (green/dark), Gemini/assistant bubbles (white/border). Parses findings, ticket chips, clean prose rendering.

**Public API:**
- `MessageBubble(role, content, parent=None)` — QFrame rendering a single chat message

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.pages.gemini_chats_page`

---

### report_section_renderer.py
> Parses markdown report output into structured QWidget section cards with headers, severity badges, and clickable ticket IDs.

**Public API:**
- `ReportSectionRenderer(parent=None)` — QWidget rendering parsed report sections
  - `set_report(markdown_text)` — parse and display report
  - `ticket_clicked` — Signal(str) emitted on ticket ID click

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.pages.ai_reports`, `src.ui.pages.smart_reporting`

---

### source_selector.py
> Dropdown for selecting a data source or "All Sources" on analytics pages. Emits `source_changed` signal.

**Public API:**
- `SourceSelector(parent=None)` — QComboBox for data source selection
  - `source_changed` — Signal(str) emitted when source changes
  - `current_source_id() -> str` — return selected source ID

**Depends on:** *(PySide6 only, no src imports)*
**Depended by:** `src.ui.pages.trc_analytics`, `src.ui.pages.trending_topics`, `src.ui.pages.incidents_page`

---

### ticket_detail_panel.py
> Tabbed detail view for a single ticket (Overview, NLP Data, Timeline, Related). Multi-source persistent database architecture.

**Public API:**
- `TicketDetailPanel(parent=None)` — QWidget with tabbed ticket detail
  - `load_ticket(ticket_id, conn)` — populate from DB
  - `clear()` — reset to empty state

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.pages.data_warehouse_page`, `src.ui.widgets.drilldown_panel`

---

### ticket_preview_card.py
> Compact QFrame showing ticket summary for Evidence Panel and other contexts. Severity-based color coding.

**Public API:**
- `TicketPreviewCard(ticket_data, parent=None)` — QFrame with ticket summary

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.widgets.evidence_panel`, `src.ui.widgets.drilldown_panel`

---

### trc_history_panel.py
> Volume sparkline, top issues bar chart, ngram trends, and related TRCs panel. Multi-source architecture.

**Public API:**
- `TRCHistoryPanel(parent=None)` — QWidget showing TRC historical metrics
  - `load_trc(trc_code, conn)` — populate from DB

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.pages.data_warehouse_page`

---

### trend_sparkline.py
> Custom QWidget that draws a mini line chart via QPainter. Input is list of (label, value) tuples.

**Public API:**
- `TrendSparkline(data, parent=None)` — QWidget rendering inline sparkline
  - `set_data(data: list[tuple])` — update data and repaint

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.widgets.evidence_panel`, `src.ui.widgets.trc_history_panel`

---

### virtual_scroll_table.py
> QAbstractTableModel with lazy-loading via `fetchMore()`. Loads 100-row pages on demand for the Data Warehouse page.

**Public API:**
- `WarehouseTableModel(parent=None)` — QAbstractTableModel with lazy row loading
  - `set_data(rows, columns)` — set initial data
  - `canFetchMore(parent) -> bool` — check if more rows available
  - `fetchMore(parent)` — load next page (100 rows)
- `VirtualScrollTable(parent=None)` — QWidget wrapping the model with a QTableView

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.pages.data_warehouse_page`

---
