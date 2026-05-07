# src/ui/

> Top-level UI modules: application shell (main window, sidebar, top bar), global stylesheet and brand theme, layman-mode translation layer, and Qt error safety net.

## AI Reports rebuild (R1–R5, 2026-05-06)

The Analysis Canvas now renders structured `Finding` cards (not raw
markdown) with the Evidence Panel binding to clicks + report metadata.
New widgets:

| Widget | Role |
|---|---|
| `widgets/severity_badge.py` | HIGH/MEDIUM/LOW/INFO colored chip |
| `widgets/finding_card.py` | Collapsible card per finding (title + chips + body drilldown) |
| `widgets/report_canvas.py` | Stack: structured findings ↔ legacy markdown fallback |
| `widgets/prompt_wizard.py` | 2-pane conversational prompt-builder |
| `pages/ai_reports_prompts_tab.py` | Thin shim wrapping PromptWizard (was 672 LOC) |

Architecture in [`docs/AI_REPORTS.md`](../../docs/AI_REPORTS.md).

## Module Index

### layman_mode.py
> Translates technical metric labels (VADER, CUSUM, z-score, etc.) into plain-language equivalents when Simplified Language Mode is enabled.

**Public API:**
- `is_layman_mode() -> bool` — check if simplified language mode is enabled in settings
- `translate_label(text: str, layman_mode: bool) -> str` — replace technical labels with plain language
- `format_sentiment(compound: float, layman_mode: bool) -> str` — format a VADER compound score for display
- `format_pvalue(p: float, layman_mode: bool) -> str` — format a p-value for display
- `format_lambda(lam: float, layman_mode: bool) -> str` — format a Poisson lambda for display
- `format_zscore(z: float, layman_mode: bool) -> str` — format a z-score for display
- `format_tfidf(score: float, layman_mode: bool) -> str` — format a TF-IDF score for display
- `format_cusum(value: float, threshold: float, layman_mode: bool) -> str` — format a CUSUM value for display
- `format_theta_level(theta: int, layman_mode: bool) -> str` — format a theta level (1 or 2) for display
- `LAYMAN_TRANSLATIONS` — dict mapping technical terms to plain-language equivalents

**Depends on:** `src.data.settings_manager`
**Depended by:** `src.ui.pages.incidents_page`, `src.ui.pages.trending_topics`, `src.ui.pages.trc_analytics`

---

### main_window.py
> Application shell: QMainWindow with top bar, collapsible sidebar navigation, stacked content pages, job queue, toast notifications, and drill-down panel.

**Public API:**
- `MainWindow` — main application window class
  - Page index constants: `PAGE_CONVERSATIONS`, `PAGE_DASHBOARD`, `PAGE_TRENDING`, `PAGE_INCIDENTS`, `PAGE_REPORTS`, `PAGE_AB_COMPARE`, `PAGE_SMART_REPORTING`, `PAGE_SETTINGS`, `PAGE_SOURCE_MONITOR`, `PAGE_GURU`
  - Instance attributes: `db`, `conversations_page`, `dashboard_page`, `trending_page`, `incidents_page`, `reports_page`, `ab_compare_page`, `smart_reporting_page`, `settings_page`, `source_monitor_page`, `guru_page`

**Depends on:** `src.ui.theme`, `src.ui.pages.conversation_search`, `src.ui.pages.trc_analytics`, `src.ui.pages.trending_topics`, `src.ui.pages.incidents_page`, `src.ui.pages.ai_reports`, `src.ui.pages.ab_compare`, `src.ui.pages.smart_reporting`, `src.ui.pages.settings_page`, `src.ui.pages.source_monitor_page`, `src.ui.pages.guru_page`, `src.ui.dialogs.help_dialog`, `src.ui.widgets.job_overlay`, `src.ui.widgets.drilldown_panel`, `src.ui.widgets.toast`, `src.ui.widgets.collapsible_section`, `src.data.db_manager`, `src.data.settings_manager`, `src.data.job_queue`, `src.data.schedule_manager`, `src.data.source_warehouse`, `src.data.watchlist_engine`, `src.data.zendesk_client`, `src.data.zendesk_monitor`, `src.data.guru_client`, `src.data.guru_friction_pipeline`, `src.data.guru_content_pipeline`, `src.data.guru_effectiveness`
**Depended by:** `main`, `tests.test_qt_regression`, `tests.test_source_monitor`, `tests.test_voc_builder`, `tests.trace_memory_app`

---

### qt_error_guard.py
> Catches silent Qt/PySide6 failures (exceptions in paint/delegate overrides, infinite recursion, memory spikes) and surfaces them in the status bar and logs.

**Public API:**
- `QtErrorGuard(app, parent)` — intercepts and surfaces silent Qt errors
  - `error_detected` — Signal(str, int) emitted on new unique error
  - `memory_warning` — Signal(str) emitted on memory growth spike
  - `attach_to_status_bar(label_widget)` — connect to a QLabel for visible alerts
  - `get_error_summary() -> str` — return formatted string of all captured errors
  - `get_error_count() -> int` — return total error count
  - `clear()` — reset all tracked errors

**Depends on:** *(PySide6 only, no src imports)*
**Depended by:** `main`

---

### theme.py
> Brand theme and global QSS stylesheet: Alma color palette, shadow helpers, table/tree configuration, and the full application stylesheet.

**Public API:**
- Color constants: `ALMA_GREEN_DARK`, `ALMA_GREEN_MID`, `ALMA_GREEN_LIGHT`, `ALMA_GREEN_SUBTLE`, `ALMA_CREAM`, `ALMA_WHITE`, `ALMA_TEXT_DARK`, `ALMA_TEXT_MID`, `ALMA_TEXT_LIGHT`, `ALMA_TEXT_ON_DARK`, `ALMA_BORDER`, `ALMA_BORDER_LIGHT`, `ALMA_HOVER_LIGHT`, `ALMA_SUCCESS`, `ALMA_WARNING`, `ALMA_ERROR`, `ALMA_INFO`, `ALMA_SHADOW_LIGHT`, `ALMA_SHADOW_MED`, `ALMA_BG_ELEVATED`, `ALMA_BG_INSET`, `ALMA_CHART_BG`, `ALMA_CHART_GRID`, `ALMA_CHART_AXIS`, `ALMA_CHART_INSET`, `ALMA_CHART_PALETTE`
- `apply_card_shadow(widget)` — resting-state card shadow
- `apply_card_shadow_hover(widget)` — hover/focus elevated shadow
- `apply_card_shadow_soft(widget)` — lighter shadow for inline cards
- `configure_table(table)` — standard table config (full-row selection, no focus rect)
- `configure_tree(tree)` — standard tree widget config (no focus rect)
- `get_stylesheet() -> str` — return the full application QSS stylesheet

**Depends on:** *(PySide6 only, no src imports)*
**Depended by:** `src.ui.main_window`, `src.ui.pages.*` (all pages), `src.ui.dialogs.*` (all dialogs), `src.ui.widgets.*` (all widgets), `main`, `tests.test_qt_regression`, `tests.test_reporting_foundation`, `tests.trace_memory_app`

---
