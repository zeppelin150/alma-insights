# src/ui/pages/

> Application pages: each module implements a full-screen page hosted inside the MainWindow's QStackedWidget. Pages cover conversation search, analytics dashboards, incident monitoring, AI report generation, and settings.

## Module Index

### ab_compare.py
> Side-by-side dataset comparison page with Gemini-powered A/B analysis and follow-up chat.

**Public API:**
- `ABCompareWorker(QThread)` — background thread for A/B dataset comparison
  - `progress` — Signal(str)
  - `finished` — Signal(str, str)
  - `error` — Signal(str)
- `ABPipelineWorker(QThread)` — background thread for full A/B pipeline execution
- `ABComparePage(QWidget)` — A/B comparison page widget
  - `set_drilldown_panel(panel)` — wire the drill-down panel
  - `refresh_gemini_status()` — refresh Gemini connection indicator

**Depends on:** `src.ui.theme`, `src.ui.widgets.chat_widget`, `src.ui.widgets.report_history_summary`, `src.ui.widgets.markdown_viewer`, `src.ui.widgets.collapsible_section`, `src.data.db_manager`, `src.data.ab_analysis`, `src.data.report_builder`, `src.gemini.client_factory`
**Depended by:** `src.ui.main_window`

---

### ai_reports.py
> AI Reports page for prompt-based report generation via Gemini, with canned/custom prompts, report history, chat follow-up, and VOC convergence analysis.

**Public API:**
- `ReportWorker(QThread)` — background thread for single report generation
- `PipelineWorker(QThread)` — background thread for full report pipeline
- `VOCReportWorker(QThread)` — background thread for VOC root-cause analysis
  - `cancel()` — cancel the running worker
- `AIReportsPage(QWidget)` — AI reports page widget
  - `set_drilldown_panel(panel)` — wire the drill-down panel
  - `refresh_gemini_status()` — refresh Gemini connection indicator
  - `set_scan_blocking(active: bool)` — block/unblock during NLP scans
  - `populate_trc_filter()` — populate TRC filter dropdown from DB
  - `sync_date_to_data()` — sync date pickers to data range
  - `load_nlp_finding(finding_id, finding_title)` — load NLP finding into drilldown
  - `cleanup()` — shutdown background resources

**Depends on:** `src.ui.theme`, `src.ui.widgets.date_picker`, `src.ui.widgets.report_history_summary`, `src.ui.widgets.generation_animation`, `src.ui.widgets.chat_widget`, `src.ui.widgets.markdown_viewer`, `src.ui.widgets.collapsible_section`, `src.ui.dialogs.prompt_editor`, `src.data.settings_manager`, `src.data.db_manager`, `src.data.report_builder`, `src.gemini.client_factory`
**Depended by:** `src.ui.main_window`

---

### conversation_search.py
> Full conversation search interface with keyword/TRC/date/CSAT filters, results table, thread viewer, CSV import, and API ingestion.

**Public API:**
- `ConversationSearchPage(QWidget)` — conversation search and viewer page
  - `data_loaded` — Signal(dict) emitted on successful CSV/API import
  - `set_drilldown_panel(panel)` — wire the drill-down panel
  - `update_datasets(datasets)` — update available API datasets
  - `update_api_state(enabled)` — update API enabled state
  - `update_pat(pat)` — update personal access token
  - `update_test_mode(is_test)` — update test mode flag
  - `update_debug_mode(enabled)` — update debug mode flag
  - `set_data_source_label(text)` — set the data source badge text
  - `filter_by_ticket_ids(ticket_ids)` — filter results to specific ticket IDs
  - `show_deferred_import_summary()` — show CSV import summary after jobs finish
  - `populate_trc_filter()` — populate TRC filter dropdown from DB
  - `run_search()` — execute the current search
  - `clear_filters()` — reset all search filters

**Depends on:** `src.ui.theme`, `src.ui.widgets.date_picker`, `src.ui.widgets.empty_state`, `src.ui.widgets.filter_chip_bar`, `src.ui.dialogs.ingestion_dialog`, `src.ui.dialogs.mapping_preview_dialog`
**Depended by:** `src.ui.main_window`

---

### guru_page.py
> Guru Knowledge Base page with five tabs: card browser, gap analysis, drafts review, effectiveness tracking, and connection settings.

**Public API:**
- `GuruPage(QWidget)` — Guru KB page widget
  - `connection_changed` — Signal() emitted when Guru connection settings change
  - `set_guru_client(client)` — set the Guru API client
  - `set_friction_pipeline(pipeline)` — set the friction analysis pipeline
  - `set_content_pipeline(pipeline)` — set the content generation pipeline
  - `set_effectiveness_tracker(tracker)` — set the effectiveness tracker
  - `set_drilldown_panel(panel)` — wire the drill-down panel

**Depends on:** `src.ui.theme`, `src.ui.widgets.guru_card_viewer`, `src.ui.widgets.guru_workbench_panel`
**Depended by:** `src.ui.main_window`, `tests.test_guru_pipeline`, `tests.test_hardening`

---

### incidents_page.py
> Incidents page with TRC ticket-rate anomaly detection using Poisson thresholds, CUSUM drift, and EWMA theta scans across four tabs: Overview, Open Incidents, Anomaly Scan, and Reports.

**Public API:**
- `IncidentWorker(QThread)` — background thread for incident anomaly scan
  - `finished` — Signal(dict)
  - `error` — Signal(str)
  - `progress` — Signal(str, int)
- `ThetaWorker(QThread)` — background thread for EWMA anomaly detection
- `IncidentsPage(AnalysisPageBase)` — incidents page widget
  - `scan_complete` — Signal(int) emitted with 2-theta flag count
  - `date_from` — property returning the from-date picker
  - `date_to` — property returning the to-date picker
  - `control_chart` — the ControlChartWidget instance
  - `set_drilldown_panel(panel)` — wire the drill-down panel
  - `populate_trc_filter()` — populate TRC filter dropdown from DB
  - `sync_date_to_data()` — sync date pickers to data range
  - `auto_run_scan()` — trigger automatic scan
  - `auto_run_theta_scan()` — trigger automatic theta scan

**Depends on:** `src.ui.theme`, `src.ui.layman_mode`, `src.ui.widgets.control_chart`, `src.ui.widgets.pagination_bar`, `src.ui.widgets.empty_state`, `src.ui.widgets.skeleton`, `src.ui.widgets.analysis_page_base`, `src.ui.widgets.kpi_card`, `src.ui.widgets.tab_scroll_content`, `src.ui.widgets.reports_tab`, `src.data.db_manager`, `src.data.incident_engine`
**Depended by:** `src.ui.main_window`

---

### placeholders.py
> Generic placeholder page for features not yet built; shows a "Coming Soon" card.

**Public API:**
- `PlaceholderPage(QWidget)` — placeholder page with icon, title, and description

**Depends on:** `src.ui.theme`
**Depended by:** *(none currently)*

---

### settings_page.py
> Settings page covering data source configuration, Gemini API setup, display preferences, behavior toggles, intervention management, and cost limits.

**Public API:**
- `ToggleSwitch(QWidget)` — custom toggle switch widget
  - `toggled` — Signal(bool)
  - `checked` — property (bool)
- `DatasetRow(QFrame)` — editable row for a Lightdash dataset
  - `get_data() -> dict` — return dataset config
  - `is_valid() -> bool` — check if row has valid data
- `GeminiSetupWorker(QThread)` — background thread for Gemini setup/validation
- `SettingsPage(QWidget)` — settings page widget
  - `datasets_changed` — Signal(list) emitted when datasets change
  - `test_data_changed` — Signal(bool) emitted when test data toggle changes
  - `debug_mode_changed` — Signal(bool) emitted when debug mode changes
  - `settings_changed` — Signal(dict) emitted on generic settings change
  - `api_toggle` — the ToggleSwitch for API enable/disable
  - `get_datasets() -> list` — return configured dataset list
  - `is_api_enabled() -> bool` — check if API is enabled
  - `is_test_data_enabled() -> bool` — check if test data is enabled
  - `is_debug_mode_enabled() -> bool` — check if debug mode is enabled
  - `get_pat() -> str` — return the personal access token
  - `set_db_manager(db)` — set the database manager reference
  - `is_layman_mode_enabled() -> bool` — check if layman mode is enabled

**Depends on:** `src.ui.theme`, `src.ui.dialogs.intervention_dialog`, `src.ui.widgets.cost_dashboard`, `src.data.settings_manager`
**Depended by:** `src.ui.main_window`, `tests.test_phase5_release`, `tests.test_hardening`

---

### smart_reporting.py
> Smart Reporting page with full pipeline execution, persistent DB-backed scheduling, CLI reference, run history with report viewer, and collapsible summaries.

**Public API:**
- `SmartPipelineWorker(QThread)` — background thread for smart reporting pipeline
  - `progress` — Signal(str)
  - `finished` — Signal(dict)
  - `error` — Signal(str)
- `SmartReportingPage(QWidget)` — smart reporting page widget
  - `set_schedule_manager(manager)` — set the persistent schedule manager
  - `refresh_gemini_status()` — refresh Gemini connection indicator

**Depends on:** `src.ui.theme`, `src.ui.widgets.collapsible_section`, `src.ui.widgets.markdown_viewer`, `src.data.settings_manager`, `src.data.db_manager`, `src.gemini.client_factory`
**Depended by:** `src.ui.main_window`

---

### source_monitor_page.py
> Source Monitor page with five tabs: Live Feed, Alerts, TRC Spikes, Watchlist rule management, and Zendesk Connection configuration.

**Public API:**
- `SourceMonitorPage(QWidget)` — source monitor page widget
  - `connection_changed` — Signal() emitted when connection settings change
  - `set_watchlist(watchlist)` — set the WatchlistEngine
  - `set_monitor(monitor)` — set the ZendeskMonitor

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.main_window`

---

### trending_topics.py
> Trending Topics page with sentiment trends, rising/cooling terms (TF-IDF velocity), topic clusters, hypothesis testing, and term management across four tabs.

**Public API:**
- `TrendingWorker(QThread)` — background thread for trending analysis
- `AIEnhancementWorker(QThread)` — background thread for AI cluster smoothing
- `HypothesisWorker(QThread)` — background thread for hypothesis testing
- `TrendingTopicsPage(AnalysisPageBase)` — trending topics page widget
  - `date_from` — property returning the from-date picker
  - `date_to` — property returning the to-date picker
  - `trc_combo` — property returning the TRC filter combo
  - `window_combo` — property returning the window size combo
  - `method_combo` — property returning the topic method combo
  - `set_drilldown_panel(panel)` — wire the drill-down panel
  - `populate_trc_filter()` — populate TRC filter dropdown from DB
  - `sync_date_to_data()` — sync date pickers to data range
  - `auto_refresh()` — trigger automatic refresh
  - `set_scan_blocking(blocked: bool)` — block/unblock during NLP scans

**Depends on:** `src.ui.theme`, `src.ui.layman_mode`, `src.ui.widgets.date_picker`, `src.ui.widgets.charts`, `src.ui.widgets.chart_builders`, `src.ui.widgets.report_history_summary`, `src.ui.widgets.smoothing_panel`, `src.ui.widgets.keyword_panel`, `src.ui.widgets.term_manager_panel`, `src.ui.widgets.collapsible_section`, `src.ui.widgets.pagination_bar`, `src.ui.widgets.empty_state`, `src.ui.widgets.skeleton`, `src.ui.widgets.analysis_page_base`, `src.ui.widgets.kpi_card`, `src.ui.widgets.tab_scroll_content`, `src.ui.widgets.reports_tab`, `src.ui.widgets.filter_chip_bar`, `src.ui.widgets.markdown_viewer`, `src.data.settings_manager`, `src.data.db_manager`, `src.data.trending_engine`
**Depended by:** `src.ui.main_window`, `tests.test_phase5_regression`

---

### trc_analytics.py
> TRC Analytics dashboard with volume charts, resolution times, CSAT heatmaps, NLP Scanner integration, sub-taxonomy browser, and reports tab.

**Public API:**
- `AnalyticsWorker(QThread)` — background thread for TRC analytics computation
  - `finished` — Signal(dict)
  - `error` — Signal(str)
- `TRCAnalyticsPage(AnalysisPageBase)` — TRC analytics page widget
  - `deep_dive_requested` — Signal(str, str) for NLP finding drilldown
  - `view_tickets_requested` — Signal(list) for ticket filter navigation
  - `scan_active_changed` — Signal(bool) for scan blocking
  - `date_from` — property returning the from-date picker
  - `date_to` — property returning the to-date picker
  - `trc_combo` — property returning the TRC filter combo
  - `status_combo` — property returning the status filter combo
  - `set_drilldown_panel(panel)` — wire the drill-down panel
  - `populate_trc_filter()` — populate TRC filter dropdown from DB
  - `sync_date_to_data()` — sync date pickers to data range
  - `auto_refresh()` — trigger automatic refresh
  - `is_scan_active() -> bool` — check if an NLP scan is running

**Depends on:** `src.ui.theme`, `src.ui.layman_mode`, `src.ui.widgets.charts`, `src.ui.widgets.pagination_bar`, `src.ui.widgets.skeleton`, `src.ui.widgets.filter_chip_bar`, `src.ui.widgets.analysis_page_base`, `src.ui.widgets.kpi_card`, `src.ui.widgets.tab_scroll_content`, `src.ui.widgets.reports_tab`, `src.ui.widgets.scan_monitor`, `src.ui.widgets.date_picker`, `src.ui.widgets.collapsible_section`, `src.ui.widgets.taxonomy_browser`, `src.ui.widgets.markdown_viewer`, `src.data.settings_manager`, `src.data.trc_analytics`
**Depended by:** `src.ui.main_window`

---

### ai_reports_history_tab.py
> Searchable, filterable table of past AI report runs from the `analysis_runs` table with filter pills and per-row actions.

**Public API:**
- `ReportHistoryTab(db, parent=None)` — QWidget embedding a report history table
  - `refresh()` — reload data from DB

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.pages.ai_reports`

---

### ai_reports_prompts_tab.py
> Prompt library browser with Gemini-powered conversational prompt builder. Left sidebar for browsing prompts, right panel for step-by-step creation.

**Public API:**
- `ManagePromptsTab(db, parent=None)` — QWidget for prompt library management
  - `refresh()` — reload prompts from DB

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.pages.ai_reports`

---

### data_warehouse_page.py
> Multi-source data warehouse page with virtual scroll table, ticket detail panel, TRC history panel, and persistent database architecture.

**Public API:**
- `DataWarehousePage(db, parent=None)` — QWidget for custom SQL queries against the warehouse
  - `set_drilldown_panel(panel)` — wire drilldown overlay
  - `refresh()` — reload data

**Depends on:** `src.ui.theme`, `src.ui.widgets.page_header`, `src.ui.widgets.virtual_scroll_table`, `src.ui.widgets.ticket_detail_panel`
**Depended by:** `src.ui.main_window`

---

### gemini_chats_page.py
> Redesigned conversational UI matching Cambric mockups. User/Gemini message bubbles, status bar, telemetry integration via ChatEngine.

**Public API:**
- `GeminiChatsPage(db, parent=None)` — QWidget for interactive AI chat
  - `set_drilldown_panel(panel)` — wire drilldown overlay

**Depends on:** `src.ui.theme`, `src.services.chat_engine`, `src.services.chat_session`
**Depended by:** `src.ui.main_window`

---

### placeholders.py
> Placeholder page stubs for pages not yet implemented. Returns a centered label with the page name.

**Public API:**
- `PlaceholderPage(title, parent=None)` — QWidget with centered placeholder label

**Depends on:** (none)
**Depended by:** (none — development utility)

---
