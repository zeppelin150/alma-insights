"""
Alma Insights — Model Registry (Phase P1)

Central registry of available LLM models and their configurations.
Singleton pattern with a Qt Signal so the sidebar can update live
when the active model changes.

Usage:
    from src.llm.model_registry import ModelRegistry
    registry = ModelRegistry.instance()
    active = registry.active()          # -> ModelConfig
    registry.set_active("claude-sonnet-4-6")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import ClassVar

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger("alma.model_registry")


@dataclass
class ModelConfig:
    """Immutable description of a single LLM model."""
    id: str                 # e.g. "gemini-2.5-flash", "claude-sonnet-4-6"
    provider: str           # "gemini" | "claude"
    display_name: str       # "Gemini 2.5 Flash"
    model_string: str       # exact API model identifier
    use_bridge: bool        # Gemini-only: persistent subprocess
    enabled: bool = True    # user can toggle
    requires_baa: bool = False  # True for Claude (HIPAA concern)


# ── Built-in model catalogue ──────────────────────────────────────

BUILTIN_MODELS: list[ModelConfig] = [
    ModelConfig(
        id="gemini-2.5-flash",
        provider="gemini",
        display_name="Gemini 2.5 Flash",
        model_string="gemini-2.5-flash",
        use_bridge=True,
        enabled=True,
        requires_baa=False,
    ),
    ModelConfig(
        id="gemini-2.5-pro",
        provider="gemini",
        display_name="Gemini 2.5 Pro",
        model_string="gemini-2.5-pro",
        use_bridge=True,
        enabled=True,
        requires_baa=False,
    ),
    ModelConfig(
        id="gemini-3-flash-preview",
        provider="gemini",
        display_name="Gemini 3 Flash (Preview)",
        model_string="gemini-3-flash-preview",
        use_bridge=True,
        enabled=True,
        requires_baa=False,
    ),
    # gemini-3.1-pro-preview: kept for reference but disabled — API
    # returns 429 aggressively on every call, making it unusable for
    # batched workloads.  Re-enable if Google raises preview quota.
    ModelConfig(
        id="gemini-3.1-pro-preview",
        provider="gemini",
        display_name="Gemini 3.1 Pro (Preview, throttled)",
        model_string="gemini-3.1-pro-preview",
        use_bridge=True,
        enabled=False,
        requires_baa=False,
    ),
    ModelConfig(
        id="gemini-2.5-flash-lite",
        provider="gemini",
        display_name="Gemini 2.5 Flash Lite",
        model_string="gemini-2.5-flash-lite",
        use_bridge=True,
        enabled=True,
        requires_baa=False,
    ),
    ModelConfig(
        id="claude-sonnet-4-6",
        provider="claude",
        display_name="Claude Sonnet 4.6",
        model_string="claude-sonnet-4-6",
        use_bridge=False,
        enabled=False,
        requires_baa=True,
    ),
    ModelConfig(
        id="claude-opus-4-6",
        provider="claude",
        display_name="Claude Opus 4.6",
        model_string="claude-opus-4-6",
        use_bridge=False,
        enabled=False,
        requires_baa=True,
    ),
    ModelConfig(
        id="claude-haiku-4-5",
        provider="claude",
        display_name="Claude Haiku 4.5",
        model_string="claude-haiku-4-5-20251001",
        use_bridge=False,
        enabled=False,
        requires_baa=True,
    ),
]

# Gemini fallback — always available
_FALLBACK_MODEL_ID = "gemini-2.5-flash"


class ModelRegistry(QObject):
    """Singleton registry of LLM models.

    Persists active model + enabled flags to ``data/settings.yaml``
    under the ``ai`` section.
    """

    model_changed = Signal(str)  # emits model_id

    _instance: ClassVar[ModelRegistry | None] = None

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # Deep-copy builtins so mutations don't leak
        self._models: dict[str, ModelConfig] = {
            m.id: ModelConfig(**m.__dict__) for m in BUILTIN_MODELS
        }
        self._active_id: str = _FALLBACK_MODEL_ID
        self._load_from_settings()

    # ── Singleton ──────────────────────────────────────────────────

    @classmethod
    def instance(cls) -> ModelRegistry:
        """Return (or create) the global ModelRegistry singleton."""
        if cls._instance is None:
            cls._instance = ModelRegistry()
        return cls._instance

    @classmethod
    def _reset_instance(cls):
        """Testing only — drop the singleton."""
        cls._instance = None

    # ── Queries ────────────────────────────────────────────────────

    def active(self) -> ModelConfig:
        """Return the currently active ModelConfig."""
        return self._models.get(self._active_id, self._models[_FALLBACK_MODEL_ID])

    def get(self, model_id: str) -> ModelConfig | None:
        """Lookup a model by id.  Returns None if unknown."""
        return self._models.get(model_id)

    def available(self) -> list[ModelConfig]:
        """Return only enabled models (for dropdown population)."""
        return [m for m in self._models.values() if m.enabled]

    def all_models(self) -> list[ModelConfig]:
        """Return every registered model, enabled or not."""
        return list(self._models.values())

    # ── Mutations ──────────────────────────────────────────────────

    def set_active(self, model_id: str) -> None:
        """Switch the active model.  Validates it is enabled first."""
        cfg = self._models.get(model_id)
        if cfg is None:
            logger.warning(f"Unknown model id: {model_id}")
            return
        if not cfg.enabled:
            logger.warning(f"Cannot activate disabled model: {model_id}")
            return
        if model_id == self._active_id:
            return
        self._active_id = model_id
        self._persist()
        logger.info(f"Active model changed to {model_id}")
        self.model_changed.emit(model_id)

    def enable(self, model_id: str) -> None:
        """Enable a model so it appears in dropdowns."""
        cfg = self._models.get(model_id)
        if cfg and not cfg.enabled:
            cfg.enabled = True
            self._persist()

    def disable(self, model_id: str) -> None:
        """Disable a model.  If it was active, fall back to Gemini."""
        cfg = self._models.get(model_id)
        if cfg is None or not cfg.enabled:
            return
        cfg.enabled = False
        if self._active_id == model_id:
            self._active_id = _FALLBACK_MODEL_ID
            self.model_changed.emit(self._active_id)
        self._persist()

    # ── Persistence ────────────────────────────────────────────────

    def _persist(self) -> None:
        """Write current state to ``data/settings.yaml`` → ``ai`` section."""
        try:
            from src.data.settings_manager import load_settings, save_settings
            cfg = load_settings()
            ai = cfg.setdefault("ai", {})
            ai["active_model"] = self._active_id
            enabled_map = {m.id: m.enabled for m in self._models.values()}
            ai["enabled_models"] = enabled_map
            save_settings(cfg)
        except Exception as e:
            logger.warning(f"Failed to persist model registry: {e}")

    def _load_from_settings(self) -> None:
        """Restore state from ``data/settings.yaml``."""
        try:
            from src.data.settings_manager import get_section
            ai = get_section("ai", {})
            if not ai:
                return

            # Restore enabled flags
            enabled_map = ai.get("enabled_models", {})
            for model_id, enabled in enabled_map.items():
                if model_id in self._models:
                    self._models[model_id].enabled = bool(enabled)

            # Restore active model
            saved_active = ai.get("active_model", "")
            if saved_active and saved_active in self._models:
                m = self._models[saved_active]
                if m.enabled:
                    self._active_id = saved_active
                else:
                    # Saved model was disabled — fall back
                    self._active_id = _FALLBACK_MODEL_ID
            else:
                self._active_id = _FALLBACK_MODEL_ID

        except Exception as e:
            logger.debug(f"Could not load model registry settings: {e}")
