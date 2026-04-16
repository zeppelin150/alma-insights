"""
Alma Insights — Centralized Settings Manager (Phase 0)

Single point of access for config/settings.yaml → data/settings.yaml.
On first call, migrates user settings from config/ to data/ so they
survive auto-updates (which replace config/ wholesale).

Usage:
    from src.data.settings_manager import load_settings, save_settings
    from src.data.settings_manager import get_section, set_section

    cfg = load_settings()                     # full dict
    gemini = get_section("gemini", {})        # single section
    set_section("gemini", {"model": "..."})   # write single section
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("alma.settings_manager")

# ── Path constants ──────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"
_CONFIG_DIR = _PROJECT_ROOT / "config"
_SETTINGS_FILE = "settings.yaml"
_MIGRATED_SUFFIX = ".yaml.migrated"

_migrated = False  # one-shot flag


def get_settings_path() -> Path:
    """Return the active settings.yaml path (data/ preferred).

    On first call, if data/settings.yaml doesn't exist but
    config/settings.yaml does, copies config → data (one-time migration).
    """
    global _migrated
    data_path = _DATA_DIR / _SETTINGS_FILE
    config_path = _CONFIG_DIR / _SETTINGS_FILE

    if data_path.exists():
        return data_path

    # Migration: copy config → data
    if not _migrated and config_path.exists():
        try:
            _DATA_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(config_path, data_path)
            # Leave breadcrumb — don't delete original
            migrated_path = config_path.with_suffix(_MIGRATED_SUFFIX)
            if not migrated_path.exists():
                config_path.rename(migrated_path)
            logger.info("Migrated settings: config/ → data/")
        except Exception as e:
            logger.warning(f"Settings migration failed: {e}")
            # Fall back to config/ if migration fails
            if config_path.exists():
                return config_path
        _migrated = True

    return data_path


def load_settings() -> dict:
    """Load the full settings dict. Returns {} on failure."""
    path = get_settings_path()
    try:
        if not path.exists():
            return {}
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning(f"Failed to load settings: {e}")
        return {}


def save_settings(cfg: dict) -> bool:
    """Write the full settings dict to data/settings.yaml.

    Uses write-to-temp + rename for atomicity on most filesystems.
    """
    path = get_settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".yaml.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)
        tmp.replace(path)
        return True
    except Exception as e:
        logger.error(f"Failed to save settings: {e}")
        return False


def get_section(section: str, default: Any = None) -> dict:
    """Load a single top-level section from settings.

    Returns the section dict, or `default` if missing.
    """
    cfg = load_settings()
    result = cfg.get(section)
    if result is None:
        return default if default is not None else {}
    return result


def set_section(section: str, value: dict) -> bool:
    """Write a single top-level section, preserving all other sections."""
    cfg = load_settings()
    cfg[section] = value
    return save_settings(cfg)


def update_section(section: str, updates: dict) -> bool:
    """Merge updates into an existing section (shallow merge).

    Useful for updating a few keys without overwriting the whole section.
    """
    cfg = load_settings()
    if section not in cfg:
        cfg[section] = {}
    cfg[section].update(updates)
    return save_settings(cfg)


def reset_migration_flag() -> None:
    """Reset the migration one-shot flag. For testing only."""
    global _migrated
    _migrated = False
