# src/gemini/

> LLM client layer for Google Gemini integration, including prompt assembly, PII redaction, CLI subprocess management, and a multi-provider client factory that routes tasks to Gemini or Claude.

## Module Index

### client_factory.py
> Builds the appropriate LLM client (Gemini or Claude) based on task-routed dispatch, active model selection, or legacy shim calls.

**Public API:**
- `get_task_routing() -> dict` — Load task routing config from settings; returns dict with 'override_all' and 'routes'.
- `resolve_provider_for_task(task_type: str) -> str` — Determine provider name ('gemini' or 'claude') for a task type, checking override then per-task route.
- `build_client_for_task(task_type: str, use_bridge: bool = False)` — Build an LLM client routed by task type; returns ClaudeClient, ReportBridgeClient, or GeminiClient (or None).
- `build_client_for_model(model_id: str | None = None, use_bridge: bool = False)` — Build an LLM client for the specified (or active) model from ModelRegistry.
- `build_gemini_client(use_bridge: bool = False)` — Backward-compatible shim; existing call sites continue to work unchanged.

**Depends on:** `src.llm.model_registry`, `src.llm.claude_client`, `src.data.settings_manager`, `src.data.pat_store`, `src.gemini.gemini_client`, `src.agents.report_bridge_client`
**Depended by:** `src.data.source_warehouse`, `src.data.watchlist_engine`, `src.ui.pages.ai_reports`, `src.ui.pages.ab_compare`, `src.ui.pages.smart_reporting`, `src.ui.pages.settings_page`, `src.ui.pages.guru_page`, `src.ui.widgets.guru_workbench_panel`, `tests.test_hardening`, `tests.test_reporting_foundation`

---

### gemini_client.py
> Wraps the Gemini CLI executable for AI synthesis with mandatory HIPAA-compliant PII redaction, subprocess invocation, and optional usage tracking.

**Public API:**
- `GeminiClient.__init__(self, cli_path: str = "", model: str = "gemini-2.5-flash", temperature: float = 0.2, pii_redaction: bool = True)` — Initialize with CLI path, model, temperature, and redaction settings.
- `GeminiClient.is_available(self) -> bool` — Return True if Gemini is usable (CLI binary exists or API key saved).
- `GeminiClient.generate(self, prompt: str, system_prompt: str = "", timeout: int = 120) -> str` — Send a prompt to Gemini CLI and return the response; always applies base PII redaction.

**Depends on:** `src.data.settings_manager`, `src.data.pat_store`
**Depended by:** `src.gemini.client_factory`, `src.llm.claude_client`, `src.data.nlp_meta_analyzer`, `src.data.nlp_synthesis`, `src.data.report_builder`, `src.data.scan_worker`, `src.data.smart_pipeline`, `src.data.source_warehouse`, `src.data.voc_builder`, `src.data.watchlist_engine`, `src.agents.report_bridge_client`, `src.ui.pages.ab_compare`, `src.ui.pages.ai_reports`, `src.ui.pages.smart_reporting`, `src.ui.pages.trending_topics`, `src.ui.widgets.chat_widget`, `tests.test_feature_integration`, `tests.test_ai_reports_live`, `tests.test_pipeline_full`, `tests.test_reporting_foundation`, `tests.bench_voc`, `test_full_system`, `test_session_7`

---

### prompts.py
> Prompt templates for Gemini-powered synthesis, hypothesis testing, and A/B dataset comparison, assembled from aggregated statistics only.

**Public API:**
- `SYSTEM_PROMPT` — Module-level string constant with the RCM analytics assistant system prompt.
- `build_synthesis_prompt(analysis_results: dict) -> str` — Assemble a synthesis prompt from topics, sentiment, rising terms, correlations, lead-lag, and incident data.
- `build_hypothesis_prompt(hypothesis: str, evidence: dict) -> str` — Build a prompt for hypothesis testing with matching tickets, temporal patterns, and correlations.
- `build_ab_prompt(ab_data_block: dict) -> str` — Build a prompt for A/B dataset comparison using the ab_comparison.txt template.

**Depends on:** (none -- only stdlib `pathlib`)
**Depended by:** `src.data.trending_engine`, `test_full_system`

---
