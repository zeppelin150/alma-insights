# Alma Insights — LLM Integration Guide

## Provider Architecture

Two LLM providers with a duck-typed interface:

```python
# Both providers implement this interface:
client.generate(prompt: str, system_prompt: str = "", timeout: int = 120) -> str
client.is_available() -> bool
```

| Provider | Use Case | Transport | PHI Allowed | Files |
|----------|----------|-----------|-------------|-------|
| **Gemini** | PHI tasks (classification, VOC, reports) | CLI subprocess or ACP bridge | Yes (BAA covers CLI) | `src/gemini/gemini_client.py` |
| **Claude** | Ops tasks (Guru analysis, watchlist, meta) | urllib HTTPS | No (redacted first) | `src/llm/claude_client.py` |

**HIPAA constraint:** Gemini must be accessed via CLI only. The Gemini SDK/REST/Vertex APIs are NOT covered by the BAA.

---

## Client Factory (`src/gemini/client_factory.py`)

Central routing function. All LLM access should go through this factory.

### Task-Routed Dispatch (preferred)

```python
from src.gemini.client_factory import build_client_for_task

client = build_client_for_task("nlp_classification")     # → GeminiClient
client = build_client_for_task("guru_analysis")          # → ClaudeClient
client = build_client_for_task("report_generation", use_bridge=True)  # → ReportBridgeClient
```

**Routing logic:**
1. Check `ai.task_routing.override_all` in settings — forces all tasks to one provider
2. Look up per-task route in `ai.task_routing.routes`
3. Fall back to `_DEFAULT_ROUTES` mapping

### Default Task Routes

| Task Type | Default Provider | Lane |
|-----------|-----------------|------|
| `nlp_classification` | Gemini | PHI |
| `voc_analysis` | Gemini | PHI |
| `report_generation` | Gemini | PHI |
| `ab_comparison` | Gemini | PHI |
| `guru_analysis` | Claude | Ops |
| `guru_content_generation` | Claude | Ops |
| `watchlist_triage` | Claude | Ops |
| `meta_analytics` | Claude | Ops |

### Legacy Shims

```python
# These still work for backward compatibility:
build_client_for_model(model_id=None, use_bridge=False)  # active model from registry
build_gemini_client(use_bridge=False)                     # backward-compat
```

---

## Gemini Integration

### GeminiClient (`src/gemini/gemini_client.py`, 328 LOC)

Wraps the Gemini CLI executable as a subprocess call.

```python
from src.gemini.gemini_client import GeminiClient

client = GeminiClient(
    cli_path="/path/to/gemini",   # auto-detected if empty
    model="gemini-2.5-flash",
    temperature=0.2,
    pii_redaction=True,           # mandatory, always on
)

response = client.generate(
    prompt="Classify these tickets...",
    system_prompt="You are an RCM analyst...",
    timeout=120,
)
```

**Key behaviors:**
- PII redaction applied before every call (via `RedactionEngine.scrub()`)
- CLI invoked as subprocess with `--model`, `--temperature` flags
- Optional API key from `pat_store` (set as `client._api_key`)
- `is_available()` checks CLI binary exists or API key is saved

### ACPBridge (`src/agents/acp_bridge.py`, 1,509 LOC)

Persistent subprocess for streaming. Used by scan workers and report orchestrator.

```python
from src.agents.acp_bridge import ACPBridge

bridge = ACPBridge(model="gemini-2.5-flash")
bridge.ensure_running()
result = bridge.call_blocking("Classify...", request_id="batch_1", timeout=300)
bridge.shutdown()
```

**Transport:** `gemini --acp` subprocess, JSON-RPC 2.0 over stdio.
**Thread safety:** All methods thread-safe. Reader thread + per-request queues.

See `docs/AGENTS.md` for full ACPBridge documentation.

### ReportBridgeClient (`src/agents/report_bridge_client.py`)

Drop-in replacement for `GeminiClient` that routes `.generate()` through an ACPBridge. Same interface, same PII redaction.

```python
from src.agents.report_bridge_client import ReportBridgeClient

client = ReportBridgeClient(model="gemini-2.5-flash", pii_redaction=True)
response = client.generate(prompt, system_prompt, timeout=120)
client.shutdown()
```

### Scan Server (`scan_server/`, Node.js)

Express HTTPS REST API for orchestrating Gemini batch classification. Entry point: `scan_server/server.js`.

**Hardening controls (15 layers):**
- TLS with self-signed cert (in-memory)
- Loopback binding (127.0.0.1 only)
- Random ephemeral port
- Per-run auth token (file handoff)
- helmet() security headers
- Rate limiting (60 req/min)
- Host header validation
- Body size limit (50 MB)
- Idle shutdown (30 min)
- SQLite write scope enforcement

**Critical:** No `@google/generative-ai` SDK. CLI-only Gemini access.

**Files:**
| File | Purpose |
|------|---------|
| `server.js` | Express app, TLS, auth, routing |
| `config.js` | CLI arg parsing, defaults |
| `batch_worker.js` | Gemini CLI invocation per batch |
| `db.js` | SQLite result persistence |
| `payload_builder.js` | Prompt assembly |
| `response_parser.js` | Parse Gemini output |
| `redaction.js` | PII scrubbing (mirrors Python engine) |
| `tls.js` | Self-signed cert generation |
| `utils.js` | Helpers |

---

## Claude Integration

### ClaudeClient (`src/llm/claude_client.py`, 285 LOC)

Anthropic Claude API client. Uses stdlib `urllib` — no SDK dependency.

```python
from src.llm.claude_client import ClaudeClient

client = ClaudeClient(
    api_key="sk-ant-...",
    model="claude-sonnet-4-6",
    pii_redaction=True,
)

# Blocking
response = client.generate(prompt, system_prompt, timeout=120, max_tokens=4096)

# Streaming
for chunk in client.generate_streaming(prompt, system_prompt):
    print(chunk, end="")
```

**Error types:**
- `ClaudeAuthError` — 401 (invalid API key)
- `ClaudeRateLimitError` — 429 (includes `retry_after` seconds)

**Key behaviors:**
- PII redaction applied before every call
- SSE streaming parser for `generate_streaming()`
- Duck-typed to match `GeminiClient.generate()` interface

### Claude Tools (`src/llm/claude_tools.py`, 445 LOC)

8 read-only tools + 1 human-gated write tool for Claude Messages API.

| Tool | Purpose | Access |
|------|---------|--------|
| `read_scan_summary` | Scan ledger (current or historical) | Read |
| `get_friction_gaps` | Friction types with low Guru coverage | Read |
| `get_sub_pattern_trends` | Sub-pattern tier/volume trends | Read |
| `search_guru_cards` | Search guru_articles by keyword | Read |
| `get_card_detail` | Single card metadata (no raw content) | Read |
| `get_card_relationships` | Cards in same collection / cross-refs | Read |
| `get_effectiveness_report` | Pre/post volume deltas per card | Read |
| `propose_guru_edit` | Stage content draft (human-gated) | Write |

**PHI safety:** Claude NEVER receives raw ticket text. Only aggregated counts, patterns, scores from PHI-free tables (`ticket_index`, `sub_patterns`, `nlp_findings`, `guru_*`).

**Usage:**
```python
from src.llm.claude_tools import TOOL_DEFINITIONS, execute_tool

# TOOL_DEFINITIONS is a list of dicts in Claude Messages API format
result_json = execute_tool("read_scan_summary", {"mode": "current", "scan_id": "abc"}, db)
```

### Scan Ledger (`src/data/scan_ledger.py`)

Builds structured markdown summaries from PHI-free tables for use as Claude context.

- `build_current_ledger(db, scan_id)` — latest scan snapshot
- `build_historical_ledger(db, n_scans)` — multi-scan trend view

Source tables (all PHI-free): `nlp_scan_runs`, `sub_patterns`, `nlp_findings`, `source_trc_daily`, `watchlist_alerts`, `guru_friction_coverage`

---

## Model Registry (`src/llm/model_registry.py`, 249 LOC)

Singleton registry of available LLM models. Qt Signal integration.

```python
from src.llm.model_registry import ModelRegistry

registry = ModelRegistry.instance()  # singleton
active = registry.active()           # → ModelConfig
registry.set_active("claude-sonnet")  # emits model_changed signal
```

**ModelConfig dataclass:** `id`, `provider`, `display_name`, `model_string`, `use_bridge`, `enabled`, `requires_baa`

**Built-in models (5):**
- Gemini 2.5 Flash, Gemini 2.5 Pro (provider=gemini)
- Claude Sonnet 4.6, Claude Haiku 4.5, Claude Opus 4.6 (provider=claude)

**Persistence:** Saved to `settings.yaml` under `ai.models`. Active model and enabled states persist across sessions.

---

## PII Redaction

### RedactionEngine (`src/data/redaction_engine.py`)

Mandatory on ALL LLM calls. Both `GeminiClient` and `ClaudeClient` apply redaction before sending prompts.

**Order of operations:**
1. Identify "keep" regions — allowlisted business entities (insurance names, TRC codes, business acronyms)
2. Apply PHI detection patterns (SSN, email, phone, DOB, names, addresses, etc.)
3. Skip matches that overlap keep regions
4. Replace remaining PHI with tokens: `[SSN]`, `[EMAIL]`, `[PHONE]`, `[DOB]`, etc.

**Configuration:**
- Detection patterns: `config/redaction_patterns.json`
- Business entity allowlist: `config/entities/phi_allowlist.json`

---

## API Key Management

### pat_store (`src/data/pat_store.py`)

Encrypted key storage. Keys stored as encrypted values in a local file.

```python
from src.data.pat_store import load_setting, save_setting

save_setting("gemini_api_key", "AIza...")
save_setting("anthropic_api_key", "sk-ant-...")

key = load_setting("gemini_api_key", "")
```

**Known keys:** `gemini_api_key`, `anthropic_api_key`, `guru_email`, `guru_api_token`, `lightdash_pat`, `github_pat`

---

## Prompt Templates

21 templates in `config/prompts/`. Loaded by path, variable replacement via Python `.format()` or manual substitution.

| Template | Used By | Purpose |
|----------|---------|---------|
| `nlp_classify.txt` | WorkerAgent | Ticket classification prompt |
| `nlp_synthesize.txt` | nlp_meta_analyzer | Post-scan NLP synthesis |
| `nlp_drilldown.txt` | NLP drilldown | Deep-dive on specific finding |
| `voc_analysis.txt` | VOCBuilder | Single-TRC VOC analysis |
| `voc_analysis_batch.txt` | VOCBuilder | Multi-TRC batched VOC |
| `voc_accumulator.txt` | VOCBuilder | Evidence ledger rounds |
| `voc_convergence.txt` | VOCBuilder | Cross-TRC convergence |
| `voc_pattern_detector.txt` | VOCBuilder | Pattern detection specialist |
| `voc_novelty_scanner.txt` | VOCBuilder | Novelty validation specialist |
| `voc_friction_scorer.txt` | VOCBuilder | Friction scoring specialist |
| `voc_synthesis.txt` | VOCBuilder | Final synthesis |
| `executive_summary.txt` | ai_report_pipeline | Executive report prompt |
| `general_trend.txt` | trending_engine | Trend synthesis |
| `ab_comparison.txt` | ab_report_pipeline | A/B comparison prompt |
| `incident_summary.txt` | incident_engine | Incident analysis |
| `hypothesis.txt` | prompts.py | Hypothesis testing |
| `synthesis.txt` | prompts.py | Gemini synthesis |
| `sentiment_dive.txt` | trending_topics | Sentiment deep-dive |
| `drilldown.txt` | UI drilldown | Ticket drilldown prompt |
| `rcm_themes.txt` | Theme extraction | RCM theme identification |
| `csv_reformat_schema.txt` | CSVReformatter | Column mapping prompt |

---

## Adding a New LLM Provider

1. **Create client class** in `src/llm/` implementing:
   - `__init__(self, api_key, model, pii_redaction=True)`
   - `generate(self, prompt, system_prompt="", timeout=120) -> str`
   - `is_available(self) -> bool`
   - Apply PII redaction before every call

2. **Register models** in `model_registry.py`:
   - Add `ModelConfig` entries to `BUILTIN_MODELS`
   - Set `provider` to your new provider name

3. **Add routing** in `client_factory.py`:
   - Add default routes in `_DEFAULT_ROUTES`
   - Add builder function `_build_<provider>_client()`
   - Wire into `build_client_for_task()` provider switch

4. **Store API key** via `pat_store`:
   - `save_setting("<provider>_api_key", key)`
   - Add UI field in Settings page (Integrations tab)

5. **Add task routing UI** in `settings_page.py`:
   - Add provider option to task routing dropdowns

---

## See Also

- `CLAUDE.md` — Quick reference (LLM Integration section)
- `docs/ARCHITECTURE.md` — System architecture (Layer 5: LLM)
- `docs/AGENTS.md` — Agentic pipeline (uses ACPBridge + ToolRegistry)
- `src/gemini/INDEX.md` — Full API for Gemini modules
- `src/llm/INDEX.md` — Full API for Claude/LLM modules
