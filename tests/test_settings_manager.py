"""
Tests for src/data/settings_manager.py (Phase 0)

Verifies settings migration from config/ → data/, load/save round-trips,
section access, and edge cases.
"""

import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml


class TestSettingsManager(unittest.TestCase):
    """Test centralized settings manager without touching real files."""

    def setUp(self):
        """Create a temp directory structure mimicking the project layout."""
        import tempfile

        self.tmp = Path(tempfile.mkdtemp())
        self.project_root = self.tmp / "alma-insights"
        self.config_dir = self.project_root / "config"
        self.data_dir = self.project_root / "data"
        self.config_dir.mkdir(parents=True)
        self.data_dir.mkdir(parents=True)

        # Write a sample config/settings.yaml
        self.sample_config = {
            "gemini": {"model": "gemini-2.5-flash", "temperature": 0.2},
            "display": {"layman_mode": True},
            "agents": {"num_workers": 3},
        }
        config_path = self.config_dir / "settings.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(self.sample_config, f, default_flow_style=False)

        # Patch the module-level path constants
        import src.data.settings_manager as sm

        self._sm = sm
        self._orig_root = sm._PROJECT_ROOT
        self._orig_data = sm._DATA_DIR
        self._orig_config = sm._CONFIG_DIR

        sm._PROJECT_ROOT = self.project_root
        sm._DATA_DIR = self.data_dir
        sm._CONFIG_DIR = self.config_dir
        sm.reset_migration_flag()

    def tearDown(self):
        # Restore originals
        self._sm._PROJECT_ROOT = self._orig_root
        self._sm._DATA_DIR = self._orig_data
        self._sm._CONFIG_DIR = self._orig_config
        self._sm.reset_migration_flag()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── Migration Tests ──────────────────────────────────────────────────

    def test_migration_copies_from_config_to_data(self):
        """First call migrates config/settings.yaml → data/settings.yaml."""
        data_path = self.data_dir / "settings.yaml"
        self.assertFalse(data_path.exists())

        path = self._sm.get_settings_path()
        self.assertEqual(path, data_path)
        self.assertTrue(data_path.exists())

        # Verify content is identical
        with open(data_path, encoding="utf-8") as f:
            migrated = yaml.safe_load(f)
        self.assertEqual(migrated["gemini"]["model"], "gemini-2.5-flash")

    def test_migration_leaves_breadcrumb(self):
        """Original config/settings.yaml renamed to .yaml.migrated."""
        self._sm.get_settings_path()
        migrated_path = self.config_dir / "settings.yaml.migrated"
        self.assertTrue(migrated_path.exists())
        # Original should no longer exist
        self.assertFalse((self.config_dir / "settings.yaml").exists())

    def test_no_double_migration(self):
        """Second call doesn't re-migrate if data/ already has the file."""
        self._sm.get_settings_path()  # first call — migrates
        # Modify the data copy
        cfg = self._sm.load_settings()
        cfg["gemini"]["model"] = "modified"
        self._sm.save_settings(cfg)
        # Reset flag and call again
        self._sm.reset_migration_flag()
        self._sm.get_settings_path()  # second call — should NOT overwrite
        cfg2 = self._sm.load_settings()
        self.assertEqual(cfg2["gemini"]["model"], "modified")

    def test_data_preferred_when_both_exist(self):
        """If both config/ and data/ have settings, data/ wins."""
        # Create data/settings.yaml manually
        data_cfg = {"gemini": {"model": "from-data"}}
        data_path = self.data_dir / "settings.yaml"
        with open(data_path, "w", encoding="utf-8") as f:
            yaml.dump(data_cfg, f)

        cfg = self._sm.load_settings()
        self.assertEqual(cfg["gemini"]["model"], "from-data")

    # ── Load/Save Round-Trip ─────────────────────────────────────────────

    def test_load_returns_empty_on_missing(self):
        """If neither file exists, returns {}."""
        (self.config_dir / "settings.yaml").unlink()
        cfg = self._sm.load_settings()
        self.assertEqual(cfg, {})

    def test_save_round_trip(self):
        """save_settings → load_settings returns identical data."""
        test_cfg = {
            "gemini": {"model": "test-model", "temperature": 0.5},
            "custom": {"key": "value"},
        }
        self._sm.save_settings(test_cfg)
        loaded = self._sm.load_settings()
        self.assertEqual(loaded, test_cfg)

    def test_save_creates_parent_dirs(self):
        """save_settings creates data/ dir if it doesn't exist."""
        shutil.rmtree(self.data_dir)
        self.assertFalse(self.data_dir.exists())
        self._sm.save_settings({"test": True})
        self.assertTrue(self.data_dir.exists())
        self.assertTrue((self.data_dir / "settings.yaml").exists())

    # ── Section Access ───────────────────────────────────────────────────

    def test_get_section_returns_section(self):
        """get_section returns a specific top-level section."""
        self._sm.get_settings_path()  # trigger migration
        gemini = self._sm.get_section("gemini")
        self.assertEqual(gemini["model"], "gemini-2.5-flash")
        self.assertAlmostEqual(gemini["temperature"], 0.2)

    def test_get_section_missing_returns_default(self):
        """get_section returns default for non-existent section."""
        self._sm.get_settings_path()
        result = self._sm.get_section("nonexistent", {"fallback": True})
        self.assertEqual(result, {"fallback": True})

    def test_get_section_missing_returns_empty_dict(self):
        """get_section returns {} by default when section is missing."""
        self._sm.get_settings_path()
        result = self._sm.get_section("nonexistent")
        self.assertEqual(result, {})

    def test_set_section_preserves_others(self):
        """set_section writes one section without affecting others."""
        self._sm.get_settings_path()
        self._sm.set_section("gemini", {"model": "new-model"})
        # Verify gemini changed
        self.assertEqual(self._sm.get_section("gemini")["model"], "new-model")
        # Verify display is untouched
        self.assertTrue(self._sm.get_section("display")["layman_mode"])

    def test_update_section_merges(self):
        """update_section merges updates without overwriting existing keys."""
        self._sm.get_settings_path()
        self._sm.update_section("gemini", {"new_key": "new_value"})
        gemini = self._sm.get_section("gemini")
        self.assertEqual(gemini["model"], "gemini-2.5-flash")  # preserved
        self.assertEqual(gemini["new_key"], "new_value")  # added

    def test_update_section_creates_missing(self):
        """update_section creates the section if it doesn't exist."""
        self._sm.get_settings_path()
        self._sm.update_section("brand_new", {"key": "value"})
        self.assertEqual(self._sm.get_section("brand_new"), {"key": "value"})

    # ── Edge Cases ───────────────────────────────────────────────────────

    def test_corrupt_yaml_returns_empty(self):
        """Corrupted YAML file returns {} instead of crashing."""
        data_path = self.data_dir / "settings.yaml"
        data_path.write_text("{{{{ not valid yaml ::::", encoding="utf-8")
        cfg = self._sm.load_settings()
        self.assertEqual(cfg, {})

    def test_empty_file_returns_empty(self):
        """Empty YAML file returns {}."""
        data_path = self.data_dir / "settings.yaml"
        data_path.write_text("", encoding="utf-8")
        cfg = self._sm.load_settings()
        self.assertEqual(cfg, {})

    def test_fallback_to_config_on_migration_failure(self):
        """If migration fails (e.g., permissions), falls back to config/."""
        # Make data dir read-only to simulate permission error
        # On Windows this is tricky, so we'll just remove data_dir
        shutil.rmtree(self.data_dir)
        # Patch mkdir to raise
        orig_mkdir = Path.mkdir
        def fail_mkdir(*args, **kwargs):
            raise PermissionError("Simulated permission error")
        with patch.object(Path, "mkdir", fail_mkdir):
            path = self._sm.get_settings_path()
        # Should fall back to config path
        self.assertTrue("config" in str(path))


if __name__ == "__main__":
    unittest.main()
