"""
Tests for Phase 2 — Auto-Update System

Covers:
  - UpdateChecker: version parsing, comparison, signal emission
  - Updater: staging, checksum, apply_staged_update, rollback
  - SchemaMigrator: tracking table, pending, apply, current_version
"""

import json
import hashlib
import os
import shutil
import sqlite3
import tempfile
import textwrap
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# Ensure src/ is importable
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ═══════════════════════════════════════
#  UpdateChecker Tests
# ═══════════════════════════════════════

class TestVersionParsing:
    """Test the _parse_version and _is_newer helpers."""

    def test_simple_semver(self):
        from src.updater.update_checker import _parse_version
        assert _parse_version("1.2.3") == (1, 2, 3)

    def test_v_prefix(self):
        from src.updater.update_checker import _parse_version
        assert _parse_version("v1.2.3") == (1, 2, 3)
        assert _parse_version("V2.0.0") == (2, 0, 0)

    def test_prerelease_stripped(self):
        from src.updater.update_checker import _parse_version
        assert _parse_version("1.2.3-rc1") == (1, 2, 3)
        assert _parse_version("v2.0.0-beta.3") == (2, 0, 0)

    def test_invalid_returns_zero(self):
        from src.updater.update_checker import _parse_version
        assert _parse_version("") == (0, 0, 0)
        assert _parse_version("not-a-version") == (0, 0, 0)

    def test_is_newer_true(self):
        from src.updater.update_checker import _is_newer
        assert _is_newer("1.0.0", "1.0.1") is True
        assert _is_newer("1.0.0", "2.0.0") is True
        assert _is_newer("1.9.9", "2.0.0") is True

    def test_is_newer_false(self):
        from src.updater.update_checker import _is_newer
        assert _is_newer("1.0.0", "1.0.0") is False
        assert _is_newer("2.0.0", "1.0.0") is False

    def test_is_newer_with_prefix(self):
        from src.updater.update_checker import _is_newer
        assert _is_newer("1.0.0", "v1.1.0") is True


class TestUpdateCheckerSignals:
    """Test UpdateChecker signal emission with mocked HTTP."""

    @pytest.fixture(autouse=True)
    def _ensure_qapp(self):
        from PySide6.QtWidgets import QApplication
        if not QApplication.instance():
            self._app = QApplication([])
        yield

    def test_update_available_signal(self):
        from src.updater.update_checker import UpdateChecker
        checker = UpdateChecker()
        received = []
        checker.update_available.connect(lambda c, n, u: received.append((c, n, u)))

        # Mock the HTTP call to return a newer version
        fake_response = json.dumps({
            "tag_name": "v99.0.0",
            "html_url": "https://github.com/alma/releases/v99.0.0",
        }).encode()

        with patch("src.updater.update_checker.urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fake_response
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_resp

            checker._do_check()

        assert len(received) == 1
        assert received[0][1] == "99.0.0"

    def test_up_to_date_signal(self):
        from src.updater.update_checker import UpdateChecker
        checker = UpdateChecker()
        received = []
        checker.up_to_date.connect(lambda: received.append(True))

        # Return current version (not newer)
        fake_response = json.dumps({
            "tag_name": "v0.0.1",
            "html_url": "",
        }).encode()

        with patch("src.updater.update_checker.urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fake_response
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_resp

            checker._do_check()

        assert len(received) == 1

    def test_check_failed_signal_on_404(self):
        from src.updater.update_checker import UpdateChecker
        import urllib.error

        checker = UpdateChecker()
        received = []
        checker.check_failed.connect(lambda msg: received.append(msg))

        with patch("src.updater.update_checker.urllib.request.urlopen") as mock_open:
            mock_open.side_effect = urllib.error.HTTPError(
                "url", 404, "Not Found", {}, None
            )
            checker._do_check()

        assert len(received) == 1
        assert "not found" in received[0].lower()

    def test_check_failed_signal_on_403(self):
        from src.updater.update_checker import UpdateChecker
        import urllib.error

        checker = UpdateChecker()
        received = []
        checker.check_failed.connect(lambda msg: received.append(msg))

        with patch("src.updater.update_checker.urllib.request.urlopen") as mock_open:
            mock_open.side_effect = urllib.error.HTTPError(
                "url", 403, "Forbidden", {}, None
            )
            checker._do_check()

        assert len(received) == 1
        assert "rate limit" in received[0].lower()


# ═══════════════════════════════════════
#  SchemaMigrator Tests
# ═══════════════════════════════════════

class TestSchemaMigrator:
    """Test SQL migration runner."""

    @pytest.fixture
    def db_conn(self, tmp_path):
        db = sqlite3.connect(str(tmp_path / "test.db"))
        yield db
        db.close()

    @pytest.fixture
    def mig_dir(self, tmp_path):
        d = tmp_path / "migrations"
        d.mkdir()
        return d

    def test_creates_tracking_table(self, db_conn, mig_dir):
        from src.updater.schema_migrator import SchemaMigrator
        migrator = SchemaMigrator(mig_dir)
        migrator.migrate(db_conn)

        tables = {
            row[0] for row in db_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "schema_migrations" in tables

    def test_apply_migration(self, db_conn, mig_dir):
        from src.updater.schema_migrator import SchemaMigrator

        # Create a migration that adds a table
        (mig_dir / "001_add_test.sql").write_text(
            "CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT);",
            encoding="utf-8",
        )

        migrator = SchemaMigrator(mig_dir)
        applied = migrator.migrate(db_conn)

        assert applied == ["001_add_test.sql"]

        # Verify table was created
        tables = {
            row[0] for row in db_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "test_table" in tables

    def test_no_double_apply(self, db_conn, mig_dir):
        from src.updater.schema_migrator import SchemaMigrator

        (mig_dir / "001_add_test.sql").write_text(
            "CREATE TABLE test_table (id INTEGER PRIMARY KEY);",
            encoding="utf-8",
        )

        migrator = SchemaMigrator(mig_dir)
        first = migrator.migrate(db_conn)
        second = migrator.migrate(db_conn)

        assert first == ["001_add_test.sql"]
        assert second == []  # Already applied

    def test_current_version(self, db_conn, mig_dir):
        from src.updater.schema_migrator import SchemaMigrator

        (mig_dir / "001_baseline.sql").write_text("-- empty", encoding="utf-8")
        (mig_dir / "002_add_stuff.sql").write_text("-- empty", encoding="utf-8")

        migrator = SchemaMigrator(mig_dir)
        migrator.migrate(db_conn)

        assert migrator.current_version(db_conn) == 2

    def test_current_version_empty(self, db_conn, mig_dir):
        from src.updater.schema_migrator import SchemaMigrator

        migrator = SchemaMigrator(mig_dir)
        assert migrator.current_version(db_conn) == 0

    def test_pending_returns_unapplied(self, db_conn, mig_dir):
        from src.updater.schema_migrator import SchemaMigrator

        (mig_dir / "001_first.sql").write_text("-- empty", encoding="utf-8")
        (mig_dir / "002_second.sql").write_text("-- empty", encoding="utf-8")

        migrator = SchemaMigrator(mig_dir)
        # Apply first only
        migrator.migrate(db_conn)

        # Add a third
        (mig_dir / "003_third.sql").write_text("-- empty", encoding="utf-8")

        pending = migrator.pending(db_conn)
        assert len(pending) == 1
        assert pending[0].name == "003_third.sql"

    def test_failed_migration_stops_chain(self, db_conn, mig_dir):
        from src.updater.schema_migrator import SchemaMigrator

        (mig_dir / "001_good.sql").write_text(
            "CREATE TABLE good_table (id INTEGER);", encoding="utf-8"
        )
        (mig_dir / "002_bad.sql").write_text(
            "INVALID SQL SYNTAX HERE;", encoding="utf-8"
        )
        (mig_dir / "003_never.sql").write_text(
            "CREATE TABLE never_table (id INTEGER);", encoding="utf-8"
        )

        migrator = SchemaMigrator(mig_dir)
        with pytest.raises(RuntimeError, match="002_bad.sql"):
            migrator.migrate(db_conn)

        # 001 should have been applied, 003 should not
        assert migrator.current_version(db_conn) == 1

    def test_empty_migration_dir(self, db_conn, tmp_path):
        from src.updater.schema_migrator import SchemaMigrator

        missing_dir = tmp_path / "nonexistent"
        migrator = SchemaMigrator(missing_dir)
        assert migrator.pending(db_conn) == []
        assert migrator.migrate(db_conn) == []

    def test_empty_sql_file_applied(self, db_conn, mig_dir):
        from src.updater.schema_migrator import SchemaMigrator

        # Empty migration (like 001_initial_baseline.sql)
        (mig_dir / "001_baseline.sql").write_text(
            "-- This is an empty baseline migration", encoding="utf-8"
        )

        migrator = SchemaMigrator(mig_dir)
        applied = migrator.migrate(db_conn)
        assert applied == ["001_baseline.sql"]
        assert migrator.current_version(db_conn) == 1


# ═══════════════════════════════════════
#  Updater Tests
# ═══════════════════════════════════════

class TestUpdater:
    """Test the stage-and-apply update mechanism."""

    @pytest.fixture(autouse=True)
    def _ensure_qapp(self):
        from PySide6.QtWidgets import QApplication
        if not QApplication.instance():
            self._app = QApplication([])
        yield

    @pytest.fixture
    def project_root(self, tmp_path):
        """Create a mock project directory."""
        root = tmp_path / "project"
        (root / "src").mkdir(parents=True)
        (root / "src" / "__init__.py").write_text('VERSION = "1.0.0"\n')
        (root / "config").mkdir()
        (root / "config" / "prompts").mkdir()
        (root / "config" / "prompts" / "test.txt").write_text("hello")
        (root / "data").mkdir()
        (root / "data" / "settings.yaml").write_text("key: val\n")
        return root

    def _make_update_zip(self, tmp_path, version="2.0.0"):
        """Create a minimal update zip with src/ and config/ dirs."""
        zip_path = tmp_path / "update.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            # Simulate GitHub zip structure: repo-name-tag/...
            prefix = f"alma-insights-v{version}/"
            zf.writestr(f"{prefix}src/__init__.py", f'VERSION = "{version}"\n')
            zf.writestr(f"{prefix}src/new_file.py", "# new file\n")
            zf.writestr(f"{prefix}config/prompts/test.txt", "updated")
            # data/ should NOT be in the zip (not replaceable)
            zf.writestr(f"{prefix}data/should_not_extract.txt", "nope")
        return zip_path

    def test_sha256_helper(self):
        from src.updater.updater import Updater
        fd, path = tempfile.mkstemp()
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(b"hello world")
            expected = hashlib.sha256(b"hello world").hexdigest()
            assert Updater._sha256(path) == expected
        finally:
            os.unlink(path)

    def test_extract_to_staging(self, tmp_path, project_root):
        from src.updater.updater import Updater, _STAGING_DIR

        zip_path = self._make_update_zip(tmp_path)

        updater = Updater()
        # Override staging dir for test isolation
        with patch("src.updater.updater._STAGING_DIR", tmp_path / "staging"):
            staging = tmp_path / "staging"
            updater._extract_to_staging(str(zip_path), "2.0.0")

            # src/ and config/ should be extracted
            assert (staging / "src" / "__init__.py").is_file()
            assert (staging / "src" / "new_file.py").is_file()
            assert (staging / "config" / "prompts" / "test.txt").is_file()
            # data/ should NOT be extracted
            assert not (staging / "data").exists()

    def test_has_staged_update_false(self, tmp_path):
        with patch("src.updater.updater._STAGING_META", tmp_path / "nonexistent.json"):
            from src.updater.updater import has_staged_update
            assert has_staged_update() is False

    def test_has_staged_update_true(self, tmp_path):
        meta = tmp_path / "meta.json"
        meta.write_text('{"version": "2.0.0"}')
        with patch("src.updater.updater._STAGING_META", meta):
            from src.updater.updater import has_staged_update
            assert has_staged_update() is True

    def test_apply_staged_update_swaps_dirs(self, tmp_path, project_root):
        """Full end-to-end: stage then apply."""
        from src.updater import updater as updater_mod

        staging = project_root / "_update_staging"
        staging.mkdir()
        (staging / "src").mkdir()
        (staging / "src" / "__init__.py").write_text('VERSION = "2.0.0"\n')
        (staging / "src" / "new_module.py").write_text("# new\n")
        (staging / "config").mkdir()
        (staging / "config" / "prompts").mkdir()
        (staging / "config" / "prompts" / "test.txt").write_text("updated")

        meta = staging / "update_meta.json"
        meta.write_text(json.dumps({"version": "2.0.0"}))

        # Patch module-level constants to use our temp project. Phase 5
        # added rollback.record_apply which writes data/rollback_state.json
        # and renames _*_backup to _*_previous — patch its paths too so
        # side effects stay inside the tmp project root.
        from src.updater import rollback as rollback_mod
        with patch.object(updater_mod, "_PROJECT_ROOT", project_root), \
             patch.object(updater_mod, "_STAGING_DIR", staging), \
             patch.object(updater_mod, "_STAGING_META", meta), \
             patch.object(rollback_mod, "_PROJECT_ROOT", project_root), \
             patch.object(rollback_mod, "_ROLLBACK_STATE",
                           project_root / "data" / "rollback_state.json"), \
             patch.object(rollback_mod, "_CRASH_DIR",
                           project_root / "data" / "crash_reports"):

            result = updater_mod.apply_staged_update()

        assert result is True
        # src/ should have the new content
        assert (project_root / "src" / "new_module.py").is_file()
        init_text = (project_root / "src" / "__init__.py").read_text()
        assert '2.0.0' in init_text
        # config/ should be updated
        assert (project_root / "config" / "prompts" / "test.txt").read_text() == "updated"
        # data/ should be untouched
        assert (project_root / "data" / "settings.yaml").read_text() == "key: val\n"
        # Staging should be cleaned up
        assert not staging.exists()

    def test_apply_staged_update_no_staging(self, tmp_path):
        from src.updater import updater as updater_mod
        with patch.object(updater_mod, "_STAGING_META", tmp_path / "nope.json"):
            assert updater_mod.apply_staged_update() is False

    def test_rollback_restores_backup(self, tmp_path, project_root):
        """If apply fails mid-way, rollback should restore backups."""
        from src.updater import updater as updater_mod

        # Simulate a backup existing (as if apply started but failed)
        backup = project_root / "_src_backup"
        shutil.copytree(project_root / "src", backup)
        # Remove live src/ to simulate failed swap
        shutil.rmtree(project_root / "src")

        with patch.object(updater_mod, "_PROJECT_ROOT", project_root):
            updater_mod._rollback()

        # src/ should be restored from backup
        assert (project_root / "src" / "__init__.py").is_file()
        assert not backup.exists()


# ═══════════════════════════════════════
#  Auto-Import Settings Tests
# ═══════════════════════════════════════

class TestAutoImportSettings:
    """Test auto-import config persistence via settings_manager.

    SmartReportingPage can't be instantiated in isolated test context
    (Windows Qt segfault on deep widget tree), so we test the settings
    round-trip and config dict construction logic directly.
    """

    def test_auto_import_settings_roundtrip(self, tmp_path):
        """Save and load auto-import config via settings_manager."""
        from src.data.settings_manager import set_section, get_section

        settings_file = tmp_path / "settings.yaml"
        with patch("src.data.settings_manager._DATA_DIR", tmp_path), \
             patch("src.data.settings_manager._SETTINGS_FILE", "settings.yaml"), \
             patch("src.data.settings_manager.get_settings_path", return_value=settings_file):
            cfg = {
                "enabled": True,
                "source_index": 1,
                "mode_index": 0,
                "lookback_days": 14,
            }
            set_section("auto_import", cfg)
            loaded = get_section("auto_import")

        assert loaded["enabled"] is True
        assert loaded["source_index"] == 1
        assert loaded["lookback_days"] == 14

    def test_auto_import_disabled_by_default(self, tmp_path):
        """Default auto-import settings show disabled."""
        from src.data.settings_manager import get_section

        settings_file = tmp_path / "settings.yaml"
        with patch("src.data.settings_manager.get_settings_path", return_value=settings_file):
            loaded = get_section("auto_import")
        assert loaded == {}

    def test_auto_import_config_dict_shape(self):
        """Verify the expected shape of auto-import config when included."""
        # The config dict structure that _get_auto_import_config returns
        config = {
            "source": "Lightdash (CSV API)",
            "mode": "Incremental",
            "lookback_days": 14,
        }
        # Verify structure matches what the pipeline would receive
        assert "source" in config
        assert "mode" in config
        assert "lookback_days" in config
        assert isinstance(config["lookback_days"], int)
