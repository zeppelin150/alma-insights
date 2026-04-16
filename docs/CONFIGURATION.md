# Alma Insights — Configuration Reference

## Settings System

### File Location

- **Active path**: `data/settings.yaml`
- **Original**: `config/settings.yaml` (renamed to `.yaml.migrated` after first-launch migration)
- **Reason**: Auto-updates replace `config/` wholesale, so user settings must live in `data/`

### API

Always use `src/data/settings_manager`. Never read/write `settings.yaml` directly.

```python
from src.data.settings_manager import load_settings, save_settings
from src.data.settings_manager import get_section, set_section, update_section

cfg = load_settings()                     # full dict (returns {} on failure)
save_settings(cfg)                        # write full dict (atomic via temp+rename)

gemini = get_section("gemini", {})        # single section
set_section("gemini", {"model": "..."})   # replace single section
update_section("gemini", {"model": "..."})  # shallow merge into section
```

### Migration

On first launch, `get_settings_path()` copies `config/settings.yaml` → `data/settings.yaml` and renames the original to `config/settings.yaml.migrated`. This is a one-shot operation.

---

## Settings Schema

### `gemini` — Gemini LLM Configuration

| Key | Type | Default | Purpose |
|-----|------|---------|---------|
| `cli_path` | string | (auto-detected) | Path to `gemini` CLI binary |
| `model` | string | `gemini-2.5-flash` | Gemini model name |
| `pii_redaction` | bool | `true` | Enable PII scrubbing (should always be true) |
| `temperature` | float | `0.2` | LLM temperature |

### `ai` — AI Task Routing & Model Selection

```yaml
ai:
  task_routing:
    override_all: ''          # 'gemini' or 'claude' to force all tasks
    routes:
      nlp_classification: gemini
      voc_analysis: gemini
      report_generation: gemini
      guru_analysis: claude
      guru_content_generation: claude
      watchlist_triage: claude
      meta_analytics: claude
      ab_comparison: gemini
  active_model: gemini-2.5-flash
  enabled_models:
    gemini-2.5-flash: true
    gemini-2.5-pro: true
    claude-sonnet-4-6: false
    claude-opus-4-6: false
    claude-haiku-4-5: false
```

| Key | Type | Purpose |
|-----|------|---------|
| `task_routing.override_all` | string | Force all tasks to one provider (empty = use per-task routes) |
| `task_routing.routes.<task>` | string | Per-task provider: `gemini` or `claude` |
| `active_model` | string | Currently selected model ID |
| `enabled_models.<id>` | bool | Whether model appears in UI dropdowns |

### `behavior` — Auto-Analysis & Calendar Sync

```yaml
behavior:
  auto_analysis:
    trc_analytics:
      enabled: true
      volume_resolution_charts: true
      csat_heatmap: true
      metrics_by_trc: true
    incidents:
      enabled: true
      trc_status_grid: true
      control_chart: true
      open_incidents: true
      theta_anomaly_scan: true
    trending:
      enabled: true
      sentiment_trend: true
      cross_trc_correlation: true
      rising_cooling_terms: true
      topic_clusters: true
  calendar_sync:
    analysis_pages: false       # sync date pickers across analysis pages
    ai_reports: false           # include AI Reports in sync
  source_sync:
    enabled: false              # auto-sync data sources on page switch
  section_defaults:
    preset: custom              # 'full', 'lite', or 'custom'
    custom:                     # per-page section visibility
      trc_analytics: { ... }
      incidents: { ... }
      trending: { ... }
```

| Key | Purpose |
|-----|---------|
| `auto_analysis.<page>.enabled` | Auto-run analysis when page loads |
| `auto_analysis.<page>.<section>` | Show/hide specific chart sections |
| `calendar_sync.analysis_pages` | Sync date range across analysis pages |
| `section_defaults.preset` | Section visibility preset |

### `display` — UI Display Settings

| Key | Type | Default | Purpose |
|-----|------|---------|---------|
| `layman_mode` | bool | `true` | Simplified Language Mode (translate technical labels) |
| `default_date_range_days` | int | `90` | Default date range for analysis pages |

### `auto_import` — Auto-Import on Startup

| Key | Type | Default | Purpose |
|-----|------|---------|---------|
| `enabled` | bool | `false` | Auto-import data on app launch |
| `source_index` | int | `0` | Data source index |
| `mode_index` | int | `0` | Import mode index |
| `lookback_days` | int | `30` | Days of data to import |

### `updates` — Auto-Update System

| Key | Type | Purpose |
|-----|------|---------|
| `last_checked` | string | ISO timestamp of last update check |

### `nlp_scan` — NLP Scan Configuration

| Key | Type | Default | Purpose |
|-----|------|---------|---------|
| `batch_size` | int | `25` | Tickets per batch |
| `budget_cap` | float | `50.0` | Maximum cost in USD |
| `parallel_workers` | int | `3` | Number of parallel workers (max 32) |
| `mode` | string | `agentic` | Scan mode |

---

## API Key Management

### pat_store (`src/data/pat_store.py`)

Encrypted key storage for sensitive credentials. Not in `settings.yaml`.

```python
from src.data.pat_store import load_setting, save_setting

save_setting("gemini_api_key", "AIza...")
key = load_setting("gemini_api_key", "")
```

**Known keys:**

| Key | Purpose | Set Via |
|-----|---------|---------|
| `gemini_api_key` | Gemini API key | Settings → AI tab |
| `anthropic_api_key` | Claude API key | Settings → AI tab |
| `guru_email` | Guru account email | Settings → Integrations tab |
| `guru_api_token` | Guru API token | Settings → Integrations tab |
| `lightdash_pat` | Lightdash personal access token | Ingestion dialog |
| `github_pat` | GitHub PAT for private releases | Settings → Updates tab |

---

## Prompt Templates

21 templates in `config/prompts/`. Plain text files with variable placeholders.

| Template | Consumer | Purpose |
|----------|---------|---------|
| `nlp_classify.txt` | WorkerAgent | Ticket classification |
| `nlp_synthesize.txt` | nlp_meta_analyzer | Post-scan synthesis |
| `nlp_drilldown.txt` | NLP drilldown UI | Finding deep-dive |
| `voc_analysis.txt` | VOCBuilder | Single-TRC VOC |
| `voc_analysis_batch.txt` | VOCBuilder | Multi-TRC batched VOC |
| `voc_accumulator.txt` | VOCBuilder | Evidence ledger rounds |
| `voc_convergence.txt` | VOCBuilder | Cross-TRC convergence |
| `voc_pattern_detector.txt` | VOCBuilder | Pattern detection |
| `voc_novelty_scanner.txt` | VOCBuilder | Novelty validation |
| `voc_friction_scorer.txt` | VOCBuilder | Friction scoring |
| `voc_synthesis.txt` | VOCBuilder | Final synthesis |
| `executive_summary.txt` | ai_report_pipeline | Executive report |
| `general_trend.txt` | trending_engine | Trend synthesis |
| `ab_comparison.txt` | ab_report_pipeline | A/B comparison |
| `incident_summary.txt` | incident_engine | Incident analysis |
| `hypothesis.txt` | prompts.py | Hypothesis testing |
| `synthesis.txt` | prompts.py | Gemini synthesis |
| `sentiment_dive.txt` | trending_topics | Sentiment deep-dive |
| `drilldown.txt` | UI drilldown | Ticket drilldown |
| `rcm_themes.txt` | Theme extraction | RCM theme ID |
| `csv_reformat_schema.txt` | CSVReformatter | Column mapping |

---

## Entity Dictionaries

3 JSON files in `config/entities/`:

| File | Purpose | Used By |
|------|---------|---------|
| `payers.json` | Insurance payer name dictionary | `entity_extractor.py` |
| `product_areas.json` | Product area taxonomy | `entity_extractor.py` |
| `phi_allowlist.json` | Business terms to preserve during PII redaction | `redaction_engine.py` |

---

## PII Redaction Configuration

### Detection Patterns (`config/redaction_patterns.json`)

Defines regex patterns for detecting PHI/PII:
- SSN, email, phone, DOB, addresses
- Names (contextual patterns)
- Medical record numbers
- Custom patterns

### Allowlist (`config/entities/phi_allowlist.json`)

Business terms that should NOT be redacted (insurance company names, TRC codes, product names, business acronyms). The `RedactionEngine` identifies "keep regions" from this allowlist before applying redaction patterns.

---

## Model Configuration

### Model Registry (`src/llm/model_registry.py`)

Singleton with 5 built-in models. Persisted to `settings.yaml` under `ai.active_model` and `ai.enabled_models`.

| Model ID | Provider | Display Name | Requires BAA |
|----------|----------|-------------|-------------|
| `gemini-2.5-flash` | gemini | Gemini 2.5 Flash | Yes |
| `gemini-2.5-pro` | gemini | Gemini 2.5 Pro | Yes |
| `claude-sonnet-4-6` | claude | Claude Sonnet 4.6 | No |
| `claude-opus-4-6` | claude | Claude Opus 4.6 | No |
| `claude-haiku-4-5` | claude | Claude Haiku 4.5 | No |

Enable/disable via Settings → AI tab or `registry.enable(id)` / `registry.disable(id)`.

---

## Source Configuration

### Source Registry (`src/data/source_registry.py`)

Multi-source data architecture. Sources registered in `source_registry` table.

| Field | Purpose |
|-------|---------|
| `source_id` | Unique identifier |
| `source_name` | Display name |
| `source_type` | `zendesk`, `kodif`, `custom` |
| `table_prefix` | Prefix for per-source tables |
| `column_mapping` | JSON column map |
| `is_default` | Default source flag |

Default source: `zendesk_default` (Zendesk Support).

---

## See Also

- `CLAUDE.md` — Quick reference (Key Patterns section)
- `docs/ARCHITECTURE.md` — System architecture
- `docs/LLM_INTEGRATION.md` — LLM provider details
- `docs/UI_GUIDE.md` — UI development guide (Settings page)
- `src/data/settings_manager.py` — Settings API source
- `src/data/pat_store.py` — API key storage source
