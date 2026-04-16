# src/llm/

> Provider abstraction layer for non-Gemini LLM integrations, currently supporting Anthropic Claude with API client, tool definitions for agentic workflows, and a singleton model registry.

## Module Index

### claude_client.py
> Anthropic Claude API client with PII redaction, duck-typed to match GeminiClient's `.generate()` interface; uses stdlib `urllib` to avoid third-party HTTP deps.

**Public API:**
- `ClaudeAuthError` — Exception raised on 401 (invalid API key).
- `ClaudeRateLimitError.__init__(self, message: str, retry_after: float | None = None)` — Exception raised on 429; includes retry_after seconds.
- `ClaudeClient.__init__(self, api_key: str, model: str = "claude-sonnet-4-6", pii_redaction: bool = True)` — Initialize with API key, model string, and redaction toggle.
- `ClaudeClient.is_available(self) -> bool` — Return True if an API key is configured.
- `ClaudeClient.generate(self, prompt: str, system_prompt: str = "", timeout: int = 120, max_tokens: int = 4096) -> str` — Send a prompt to Claude and return the response text with PII redaction.
- `ClaudeClient.generate_streaming(self, prompt: str, system_prompt: str = "", max_tokens: int = 4096, timeout: int = 120) -> Iterator[str]` — Stream response tokens via SSE, yielding text delta strings.

**Depends on:** `src.gemini.gemini_client`
**Depended by:** `src.gemini.client_factory`, `tests.test_claude_client`

---

### claude_tools.py
> Eight read-only tool definitions (plus one human-gated write tool) for Claude's Messages API tool-use, exposing DB queries for Guru KB analysis and scan ledger inspection.

**Public API:**
- `TOOL_DEFINITIONS` — List of tool definition dicts in Claude Messages API format (8 tools).
- `execute_tool(tool_name: str, args: dict, db) -> str` — Execute a tool by name and return a JSON string result.

**Depends on:** `src.data.scan_ledger`, `src.data.guru_friction_pipeline`, `src.data.guru_effectiveness`
**Depended by:** `tests.test_hardening`

---

### model_registry.py
> Singleton registry of available LLM models (Gemini and Claude) with Qt Signal integration, persistence to settings.yaml, and active-model switching.

**Public API:**
- `ModelConfig` — Dataclass: `id`, `provider`, `display_name`, `model_string`, `use_bridge`, `enabled`, `requires_baa`.
- `BUILTIN_MODELS` — Module-level list of default ModelConfig entries (5 models).
- `ModelRegistry.instance() -> ModelRegistry` — Return (or create) the global singleton.
- `ModelRegistry.active(self) -> ModelConfig` — Return the currently active ModelConfig.
- `ModelRegistry.get(self, model_id: str) -> ModelConfig | None` — Lookup a model by id.
- `ModelRegistry.available(self) -> list[ModelConfig]` — Return only enabled models.
- `ModelRegistry.all_models(self) -> list[ModelConfig]` — Return every registered model, enabled or not.
- `ModelRegistry.set_active(self, model_id: str) -> None` — Switch the active model (validates it is enabled first); emits `model_changed` signal.
- `ModelRegistry.enable(self, model_id: str) -> None` — Enable a model so it appears in dropdowns.
- `ModelRegistry.disable(self, model_id: str) -> None` — Disable a model; falls back to Gemini if it was active.
- `ModelRegistry.model_changed` — Signal(str) emitted with model_id when active model changes.

**Depends on:** `src.data.settings_manager`
**Depended by:** `src.gemini.client_factory`, `src.ui.pages.settings_page`, `tests.test_model_registry`

---
