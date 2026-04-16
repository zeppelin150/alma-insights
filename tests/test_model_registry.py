"""
Tests for src/llm/model_registry.py (Phase P1-T1)

Covers: singleton, active/set_active, enable/disable, fallback,
persistence round-trip, signal emission.
"""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock

# Ensure project root on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtWidgets import QApplication

# Ensure QApplication exists for Signal tests
app = QApplication.instance() or QApplication(sys.argv[:1])


class TestModelRegistry:
    """Core ModelRegistry tests."""

    def _fresh_registry(self):
        """Create a clean registry (not the singleton) for isolation."""
        from src.llm.model_registry import ModelRegistry
        ModelRegistry._reset_instance()
        with patch("src.llm.model_registry.ModelRegistry._load_from_settings"):
            with patch("src.llm.model_registry.ModelRegistry._persist"):
                reg = ModelRegistry()
        return reg

    def test_singleton_returns_same_instance(self):
        from src.llm.model_registry import ModelRegistry
        ModelRegistry._reset_instance()
        with patch("src.llm.model_registry.ModelRegistry._load_from_settings"):
            a = ModelRegistry.instance()
            b = ModelRegistry.instance()
        assert a is b
        ModelRegistry._reset_instance()

    def test_default_active_is_gemini(self):
        reg = self._fresh_registry()
        assert reg.active().id == "gemini-2.5-flash"
        assert reg.active().provider == "gemini"

    def test_set_active_switches_model(self):
        reg = self._fresh_registry()
        # Enable Claude first
        reg._models["claude-sonnet-4-6"].enabled = True
        with patch.object(reg, "_persist"):
            reg.set_active("claude-sonnet-4-6")
        assert reg.active().id == "claude-sonnet-4-6"
        assert reg.active().provider == "claude"

    def test_set_active_rejects_disabled_model(self):
        reg = self._fresh_registry()
        assert reg._models["claude-sonnet-4-6"].enabled is False
        with patch.object(reg, "_persist"):
            reg.set_active("claude-sonnet-4-6")
        # Should remain on gemini
        assert reg.active().id == "gemini-2.5-flash"

    def test_set_active_rejects_unknown_model(self):
        reg = self._fresh_registry()
        with patch.object(reg, "_persist"):
            reg.set_active("nonexistent-model")
        assert reg.active().id == "gemini-2.5-flash"

    def test_set_active_noop_if_already_active(self):
        reg = self._fresh_registry()
        persist_mock = MagicMock()
        with patch.object(reg, "_persist", persist_mock):
            reg.set_active("gemini-2.5-flash")
        persist_mock.assert_not_called()

    def test_enable_model(self):
        reg = self._fresh_registry()
        assert reg._models["claude-opus-4-6"].enabled is False
        with patch.object(reg, "_persist"):
            reg.enable("claude-opus-4-6")
        assert reg._models["claude-opus-4-6"].enabled is True

    def test_disable_model(self):
        reg = self._fresh_registry()
        assert reg._models["gemini-2.5-flash-lite"].enabled is True
        with patch.object(reg, "_persist"):
            reg.disable("gemini-2.5-flash-lite")
        assert reg._models["gemini-2.5-flash-lite"].enabled is False

    def test_disable_active_falls_back_to_gemini(self):
        reg = self._fresh_registry()
        reg._models["claude-sonnet-4-6"].enabled = True
        with patch.object(reg, "_persist"):
            reg.set_active("claude-sonnet-4-6")
        assert reg.active().id == "claude-sonnet-4-6"
        with patch.object(reg, "_persist"):
            reg.disable("claude-sonnet-4-6")
        assert reg.active().id == "gemini-2.5-flash"

    def test_available_returns_enabled_only(self):
        reg = self._fresh_registry()
        available = reg.available()
        for m in available:
            assert m.enabled is True
        ids = {m.id for m in available}
        assert "gemini-2.5-flash" in ids
        # Claude models start disabled
        assert "claude-sonnet-4-6" not in ids

    def test_all_models_returns_everything(self):
        reg = self._fresh_registry()
        all_m = reg.all_models()
        assert len(all_m) >= 5

    def test_get_returns_model_or_none(self):
        reg = self._fresh_registry()
        assert reg.get("gemini-2.5-flash") is not None
        assert reg.get("nonexistent") is None

    def test_signal_emitted_on_set_active(self):
        reg = self._fresh_registry()
        reg._models["claude-sonnet-4-6"].enabled = True
        received = []
        reg.model_changed.connect(lambda mid: received.append(mid))
        with patch.object(reg, "_persist"):
            reg.set_active("claude-sonnet-4-6")
        assert received == ["claude-sonnet-4-6"]

    def test_signal_emitted_on_disable_active(self):
        reg = self._fresh_registry()
        reg._models["claude-sonnet-4-6"].enabled = True
        with patch.object(reg, "_persist"):
            reg.set_active("claude-sonnet-4-6")
        received = []
        reg.model_changed.connect(lambda mid: received.append(mid))
        with patch.object(reg, "_persist"):
            reg.disable("claude-sonnet-4-6")
        assert received == ["gemini-2.5-flash"]

    def test_requires_baa_flag(self):
        reg = self._fresh_registry()
        assert reg.get("gemini-2.5-flash").requires_baa is False
        assert reg.get("claude-sonnet-4-6").requires_baa is True

    def test_builtin_models_have_correct_providers(self):
        reg = self._fresh_registry()
        for m in reg.all_models():
            if "gemini" in m.id:
                assert m.provider == "gemini"
            elif "claude" in m.id:
                assert m.provider == "claude"


class TestModelRegistryPersistence:
    """Test save/load round-trip."""

    def test_persist_and_reload(self, tmp_path):
        """Settings round-trip: save state, create new registry, verify."""
        from src.llm.model_registry import ModelRegistry

        settings_file = tmp_path / "settings.yaml"
        stored = {}

        def mock_load_settings():
            import yaml
            if settings_file.exists():
                return yaml.safe_load(settings_file.read_text()) or {}
            return {}

        def mock_save_settings(cfg):
            import yaml
            settings_file.write_text(yaml.dump(cfg))
            return True

        def mock_get_section(section, default=None):
            cfg = mock_load_settings()
            return cfg.get(section, default if default is not None else {})

        ModelRegistry._reset_instance()

        # Create registry, enable Claude, set active
        with patch("src.llm.model_registry.ModelRegistry._load_from_settings"):
            reg1 = ModelRegistry()

        reg1._models["claude-sonnet-4-6"].enabled = True
        reg1._active_id = "claude-sonnet-4-6"

        with patch("src.data.settings_manager.load_settings", mock_load_settings):
            with patch("src.data.settings_manager.save_settings", mock_save_settings):
                reg1._persist()

        # Create fresh registry and load
        with patch("src.llm.model_registry.ModelRegistry._load_from_settings"):
            reg2 = ModelRegistry()

        with patch("src.data.settings_manager.get_section", mock_get_section):
            reg2._load_from_settings()

        assert reg2._active_id == "claude-sonnet-4-6"
        assert reg2._models["claude-sonnet-4-6"].enabled is True
        ModelRegistry._reset_instance()

    def test_load_with_disabled_active_falls_back(self, tmp_path):
        """If saved active model is disabled, fall back to Gemini."""
        from src.llm.model_registry import ModelRegistry

        def mock_get_section(section, default=None):
            if section == "ai":
                return {
                    "active_model": "claude-sonnet-4-6",
                    "enabled_models": {"claude-sonnet-4-6": False},
                }
            return default or {}

        ModelRegistry._reset_instance()
        with patch("src.llm.model_registry.ModelRegistry._load_from_settings"):
            reg = ModelRegistry()

        with patch("src.data.settings_manager.get_section", mock_get_section):
            reg._load_from_settings()

        assert reg._active_id == "gemini-2.5-flash"
        ModelRegistry._reset_instance()
