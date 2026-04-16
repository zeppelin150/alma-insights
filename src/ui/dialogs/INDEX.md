# src/ui/dialogs/

> Modal dialog windows for focused user interactions: data ingestion, column mapping, prompt editing, intervention management, term curation, help/feedback, and expanded content viewing.

## Module Index

### expanded_section_dialog.py
> Near-fullscreen modal that re-parents a CollapsibleSection's content widget for full-window viewing, then returns it on close.

**Public API:**
- `ExpandedSectionDialog(QDialog)` — expanded view dialog for any CollapsibleSection content
  - `__init__(title: str, content_widget, parent)` — re-parents the widget into the dialog

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.widgets.collapsible_section`

---

### help_dialog.py
> Help and feedback dialog with four tabs: Overview, Documentation (Guru link), Feedback (Typeform link), and About.

**Public API:**
- `HelpDialog(QDialog)` — help and feedback dialog
- `GURU_FOLDER_URL` — configurable Guru documentation URL
- `TYPEFORM_URL` — configurable Typeform feedback URL

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.main_window`

---

### ingestion_dialog.py
> Modal dialog for CSV/API ingestion with preflight checklist, live log, progress bar, and background worker thread.

**Public API:**
- `IngestionDialog(QDialog)` — ingestion dialog with preflight checks and live log
  - Constructor accepts: `mode`, `pat`, `chart_url`, `date_start`, `date_end`, `db_path`, `trusted_base`, `is_test_mode`

**Depends on:** `src.ui.theme`, `src.ui.widgets.date_picker`, `src.data.db_manager`, `src.data.run_logger`
**Depended by:** `src.ui.pages.conversation_search`

---

### intervention_dialog.py
> Dialog for adding/editing intervention events with category, date, description, affected TRCs, and tags.

**Public API:**
- `InterventionDialog(QDialog)` — intervention marker add/edit dialog
  - `get_intervention_data() -> dict` — return the edited intervention data
- `INTERVENTION_CATEGORIES` — list of (code, label) tuples for intervention types
- `CATEGORY_COLORS` — dict mapping category codes to hex colors

**Depends on:** `src.ui.theme`, `src.ui.widgets.date_picker`
**Depended by:** `src.ui.pages.settings_page`

---

### mapping_preview_dialog.py
> Column mapping preview/edit dialog shown during CSV import when column mappings are ambiguous; lets users review and override Gemini-proposed or auto-detected mappings.

**Public API:**
- `MappingPreviewDialog(QDialog)` — column mapping preview and editor
  - `__init__(mapping_result, parent)` — initialize with a MappingResult
  - `get_column_override() -> dict` — return the final `{normalized_header: target_field}` dict after accept

**Depends on:** `src.ui.theme`, `src.agents.csv_reformatter`
**Depended by:** `src.ui.pages.conversation_search`

---

### prompt_editor.py
> Create/edit prompts for the AI Reports prompt library, with variable insertion, expanded editor, and file import.

**Public API:**
- `ExpandedPromptDialog(QDialog)` — near-fullscreen modal for editing long prompts
  - `get_text() -> str` — return the edited prompt text
- `PromptEditorDialog(QDialog)` — full prompt editor with name, description, system prompt, analysis prompt, and variable reference
  - `get_prompt_data() -> dict` — return the edited prompt data dict
- `PROMPT_VARIABLES` — list of `(variable, description)` tuples for available prompt variables

**Depends on:** `src.ui.theme`
**Depended by:** `src.ui.pages.ai_reports`

---

### term_manager_dialog.py
> Three-tab dialog for managing the adaptive term intelligence system: active terms, discovered candidates (PMI-based), and aliases/concepts.

**Public API:**
- `TermManagerDialog(QDialog)` — term management dialog
  - `terms_changed` — Signal() emitted when terms are modified

**Depends on:** `src.ui.theme`, `src.data.trending_engine`
**Depended by:** *(none currently; superseded by `src.ui.widgets.term_manager_panel`)*

---
