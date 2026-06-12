"""
Alma Insights — LLM Client Factory (Phase 5.5A + P1 + Hardening)

Builds the appropriate LLM client based on:
  - Task-routed dispatch: ``build_client_for_task(task_type)`` reads
    ``ai.task_routing`` from settings to pick the right provider per task.
  - Active model dispatch: ``build_client_for_model()`` uses ModelRegistry.
  - Legacy shim: ``build_gemini_client()`` backward-compat.

Task types and their default lanes:
  PHI lane  → Gemini:  nlp_classification, voc_analysis, report_generation
  Ops lane  → Claude:  guru_analysis, guru_content_generation,
                        watchlist_triage, meta_analytics
  Override:  ``ai.task_routing.override_all`` forces all tasks to one provider.

Usage:
    from src.gemini.client_factory import build_client_for_task
    client = build_client_for_task("guru_analysis")           # task-routed
    client = build_client_for_task("report_generation", use_bridge=True)
    client = build_client_for_model()                         # active model
    client = build_gemini_client(use_bridge=True)             # legacy shim
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("alma.client_factory")

# ── Default task→provider mapping (fallback if settings missing) ─────
_DEFAULT_ROUTES = {
    "nlp_classification": "gemini",
    "voc_analysis": "gemini",
    "report_generation": "gemini",
    "guru_analysis": "claude",
    "guru_content_generation": "claude",
    "watchlist_triage": "claude",
    "meta_analytics": "claude",
    "ab_comparison": "gemini",
    # Enablement Workbench lanes — default Gemini; the operator can flip the whole
    # set via the enablement.provider toggle (see resolve_provider_for_task).
    "enablement_card_gen": "gemini",
    "enablement_extract": "gemini",
    "enablement_triage": "gemini",
    "enablement_subtasks": "gemini",
    "enablement_chat": "gemini",
    "enablement_pptx_gen": "gemini",
    "enablement_article_gen": "gemini",
    "enablement_macro_gen": "gemini",
}


def get_task_routing() -> dict:
    """Load task routing config from settings.

    Returns dict with 'override_all' (str) and 'routes' (dict).
    """
    try:
        from src.data.settings_manager import get_section
        ai_cfg = get_section("ai", {})
        routing = ai_cfg.get("task_routing", {})
        return {
            "override_all": routing.get("override_all", ""),
            "routes": routing.get("routes", dict(_DEFAULT_ROUTES)),
        }
    except Exception:
        return {"override_all": "", "routes": dict(_DEFAULT_ROUTES)}


def _enablement_provider() -> str:
    """Operator's Enablement provider toggle ('gemini'|'claude'|'')."""
    try:
        from src.data.settings_manager import get_section
        return (get_section("enablement", {}) or {}).get("provider", "") or ""
    except Exception:
        return ""


def resolve_provider_for_task(task_type: str) -> str:
    """Determine provider name ('gemini' or 'claude') for a task type.

    Enablement lanes ('enablement_*') honor the operator's Enablement provider
    toggle first so it scopes to those lanes only; then override_all, then the
    per-task route, then the default map.
    """
    if task_type.startswith("enablement_"):
        prov = _enablement_provider()
        if prov in ("gemini", "claude"):
            return prov
    routing = get_task_routing()
    override = routing["override_all"]
    if override in ("gemini", "claude"):
        return override
    routes = routing["routes"]
    return routes.get(task_type, _DEFAULT_ROUTES.get(task_type, "gemini"))


def build_client_for_task(task_type: str, use_bridge: bool = False) -> Any | None:
    """Build an LLM client routed by task type.

    Args:
        task_type: One of the defined task types (e.g. 'guru_analysis').
        use_bridge: For Gemini tasks, use ReportBridgeClient.

    Returns:
        ClaudeClient, ReportBridgeClient, or GeminiClient.
        Returns None if configuration is missing.
    """
    provider = resolve_provider_for_task(task_type)
    logger.debug("Task '%s' → provider '%s'", task_type, provider)

    if provider == "claude":
        # Enablement Claude is BAA-routed through AWS Bedrock (the org's PHI-approved
        # path) — always via the CLI bridge, never the direct Anthropic API.
        force_cli = task_type.startswith("enablement_")
        client = _build_claude_client_from_registry(force_cli=force_cli)
        if client is None:
            logger.info("Claude not configured, falling back to Gemini for task '%s'", task_type)
            client = _build_gemini_client_internal(use_bridge)
    else:
        client = _build_gemini_client_internal(use_bridge)

    # Enablement content is product docs / Guru cards, NOT ticket PHI. The aggressive
    # name-redaction pass rewrites Title-Case product terms (e.g. "Identity Providers",
    # "Okta") to [NAME] and wrecks card quality. Disable it for enablement lanes —
    # base redaction (emails/phones/SSNs/cards/member-ids) always runs regardless.
    if task_type.startswith("enablement_") and client is not None and hasattr(client, "pii_redaction"):
        client.pii_redaction = False
    return client


def _build_claude_client_from_registry(force_cli: bool = False):
    """Build a Claude client.

    Prefers ClaudeClient (Anthropic Messages API) when ``anthropic_api_key``
    is configured in pat_store. Otherwise falls back to ClaudeCliClient,
    which uses the local ``claude`` CLI subprocess and inherits whatever auth
    the user has logged into the CLI with (OAuth/keychain/Bedrock env).

    ``force_cli=True`` skips the direct-API path entirely and always returns the
    CLI client — the Bedrock-routed, BAA-safe path (Bedrock activates when
    ``bedrock.enabled=true`` in settings + the ops-ai SSO profile). Enablement
    Claude work uses this so PHI-adjacent generation never leaves the BAA.

    The CLI fallback is the path that makes ``override_all=claude`` work
    end-to-end on machines without an Anthropic API key.
    """
    from src.data.pat_store import load_setting
    api_key = load_setting("anthropic_api_key", "")

    if api_key and not force_cli:
        try:
            from src.llm.model_registry import ModelRegistry
            registry = ModelRegistry.instance()
            for m in registry.available():
                if m.provider == "claude":
                    client = _build_claude_client(m)
                    if client is not None:
                        return client
        except Exception as e:
            logger.debug("Claude API path failed: %s", e)

    try:
        from src.llm.claude_cli_client import ClaudeCliClient
        alias = _resolve_claude_cli_alias()
        return ClaudeCliClient(model=alias)
    except Exception as e:
        logger.warning(f"Failed to build Claude CLI client: {e}")
        return None


def build_client_for_model(model_id: str | None = None, use_bridge: bool = False) -> Any | None:
    """Build an LLM client for the specified (or active) model.

    Args:
        model_id: Explicit model id.  If None, uses the active model
                  from ModelRegistry.
        use_bridge: For Gemini models, return a ReportBridgeClient
                    instead of a GeminiClient.

    Returns:
        ClaudeClient, ReportBridgeClient, or GeminiClient instance.
        Returns None if configuration is missing.
    """
    try:
        from src.llm.model_registry import ModelRegistry
        registry = ModelRegistry.instance()
        cfg = registry.get(model_id) if model_id else registry.active()
    except Exception:
        cfg = None

    if cfg is not None and cfg.provider == "claude":
        return _build_claude_client(cfg)

    # Gemini path (default / fallback)
    return _build_gemini_client_internal(use_bridge)


def build_gemini_client(use_bridge: bool = False) -> Any | None:
    """Backward-compatible shim.

    Existing call sites (ai_reports, ab_compare, smart_pipeline, etc.)
    continue to work unchanged.  If the active model is Claude, this
    still returns a Claude client — seamless multi-provider.
    """
    return build_client_for_model(use_bridge=use_bridge)


def build_bridge_for_task(task_type: str, model: str | None = None) -> Any | None:
    """Build a streaming bridge object routed by task type.

    Used by ScanOrchestrator / ReportOrchestrator / ReportBridgeClient — any
    caller that needs the persistent-streaming bridge interface
    (call_streaming, call_blocking, is_alive, probe, shutdown, set_on_death).

    Args:
        task_type: One of the defined task types (e.g. 'nlp_classification').
        model: Explicit model id. For Gemini routes this is honored as-is.
            For Claude routes the parameter is overridden — Gemini model ids
            (e.g. 'gemini-2.5-flash') are not valid for the claude CLI, so we
            resolve a Claude alias from the registry instead.

    Returns:
        ACPBridge for Gemini-routed tasks; ClaudeCliBridge for Claude-routed.
    """
    provider = resolve_provider_for_task(task_type)
    logger.debug("Bridge for task '%s' → provider '%s'", task_type, provider)

    if provider == "claude":
        claude_model = _resolve_claude_cli_alias()
        from src.agents.claude_cli_bridge import ClaudeCliBridge
        return ClaudeCliBridge(model=claude_model)

    from src.agents.acp_bridge import ACPBridge
    return ACPBridge(model=model)


def is_provider_available_for_task(task_type: str = "report_generation") -> bool:
    """Return True if the routed provider for ``task_type`` is reachable.

    Replaces the pattern ``GeminiClient().is_available()`` at status-check
    sites — those silently report False when the active provider is Claude
    but Gemini isn't configured. Routing through the factory makes the
    check honor the user's ``override_all`` setting.
    """
    try:
        client = build_client_for_task(task_type)
        if client is None:
            return False
        check = getattr(client, "is_available", None)
        return bool(check()) if callable(check) else True
    except Exception:
        return False


def _resolve_claude_cli_alias() -> str:
    """Pick a CLI-valid model alias ('sonnet'|'opus'|'haiku').

    The model registry stores ids like 'claude-sonnet-4-6' that the claude
    CLI does not accept directly. We map by substring; default sonnet.
    """
    try:
        from src.llm.model_registry import ModelRegistry
        registry = ModelRegistry.instance()
        active = registry.active()
        if active and active.provider == "claude":
            mid = active.id.lower()
            if "haiku" in mid:
                return "haiku"
            if "opus" in mid:
                return "opus"
            return "sonnet"
        for m in registry.available():
            if m.provider == "claude":
                mid = m.id.lower()
                if "haiku" in mid:
                    return "haiku"
                if "opus" in mid:
                    return "opus"
                return "sonnet"
    except Exception:
        pass
    return "sonnet"


# ── Claude builder ────────────────────────────────────────────────

def _build_claude_client(model_cfg):
    """Build a ClaudeClient for the given ModelConfig."""
    try:
        from src.llm.claude_client import ClaudeClient, ClaudeAuthError
        from src.data.pat_store import load_setting

        api_key = load_setting("anthropic_api_key", "")
        if not api_key:
            logger.warning("Anthropic API key not configured")
            return None

        gemini_cfg = _load_gemini_config() or {}
        pii = gemini_cfg.get("pii_redaction", True)

        client = ClaudeClient(
            api_key=api_key,
            model=model_cfg.model_string,
            pii_redaction=pii,
        )
        return client
    except Exception as e:
        logger.warning(f"Failed to build Claude client: {e}")
        return None


# ── Gemini builder (original logic preserved) ─────────────────────

def _build_gemini_client_internal(use_bridge: bool = False):
    """Build a Gemini client from settings (original path)."""
    gemini_cfg = _load_gemini_config()
    if gemini_cfg is None:
        return None

    cli_path = gemini_cfg.get("cli_path", "")
    temperature = gemini_cfg.get("temperature", 0.2)
    pii = gemini_cfg.get("pii_redaction", True)

    # Resolve model: ModelRegistry.active() is authoritative (what
    # Settings UI writes).  Legacy `gemini.model` is the fallback.
    # Previously this only read `gemini.model`, so switching models in
    # Settings silently did nothing for Gemini-routed tasks.
    HARD_FALLBACK = "gemini-2.5-flash"
    model = None
    try:
        from src.llm.model_registry import ModelRegistry
        active = ModelRegistry.instance().active()
        if active and active.provider == "gemini" and active.enabled:
            model = active.model_string
    except Exception as e:
        logger.debug(f"ModelRegistry lookup failed: {e}")
    if not model:
        legacy = gemini_cfg.get("model")
        if legacy:
            logger.warning(
                f"Gemini client: ModelRegistry unavailable; using "
                f"legacy gemini.model='{legacy}'. Check ai.active_model."
            )
            model = legacy
    if not model:
        logger.error(
            f"Gemini client: NEITHER ModelRegistry NOR gemini.model "
            f"resolved a model. Falling back to hardcoded "
            f"'{HARD_FALLBACK}'. This is a configuration error."
        )
        model = HARD_FALLBACK
    logger.debug(f"Gemini client resolved model: {model}")

    if use_bridge:
        try:
            from src.agents.report_bridge_client import ReportBridgeClient
            return ReportBridgeClient(
                cli_path=cli_path,
                model=model,
                temperature=temperature,
                pii_redaction=pii,
            )
        except ImportError:
            logger.info("ReportBridgeClient not available, falling back to GeminiClient")
            # Fall through to GeminiClient

    from src.gemini.gemini_client import GeminiClient
    client = GeminiClient(cli_path=cli_path, model=model, pii_redaction=pii)

    # Load optional API key from pat_store
    try:
        from src.data.pat_store import load_setting
        api_key = load_setting("gemini_api_key", "")
        if api_key:
            client._api_key = api_key
    except Exception:
        pass

    return client


def _load_gemini_config() -> dict | None:
    """Load the gemini section from settings."""
    try:
        from src.data.settings_manager import get_section
        return get_section("gemini", {})
    except Exception as e:
        logger.warning(f"Failed to load gemini config: {e}")
        return None
