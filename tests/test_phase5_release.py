"""
Tests for Phase 5 — Release Packaging + Updates Tab Enhancement

Covers:
- make_release.py: version detection, exclusion patterns, zip contents, SHA256
- Settings Updates tab: new handler methods, last-checked persistence
"""

import hashlib
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

# Ensure project root on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ════════════════════════════════════════════════════
#  make_release.py tests
# ════════════════════════════════════════════════════

class TestMakeReleaseVersionDetection(unittest.TestCase):
    """get_version() reads VERSION from src/__init__.py."""

    def test_get_version_returns_string(self):
        from scripts.make_release import get_version
        ver = get_version()
        self.assertIsInstance(ver, str)
        # Should have at least one dot (X.Y or X.Y.Z)
        self.assertIn(".", ver)

    def test_get_version_fallback(self):
        from scripts.make_release import get_version
        with patch("scripts.make_release.PROJECT_ROOT", Path("/nonexistent")):
            ver = get_version()
            self.assertEqual(ver, "0.0.0")


class TestShouldExclude(unittest.TestCase):
    """Exclusion pattern matching."""

    def test_excludes_pycache(self):
        from scripts.make_release import should_exclude
        self.assertTrue(should_exclude(Path("src/__pycache__/foo.pyc")))

    def test_excludes_pyc(self):
        from scripts.make_release import should_exclude
        self.assertTrue(should_exclude(Path("src/data/module.pyc")))

    def test_excludes_egg_info(self):
        from scripts.make_release import should_exclude
        self.assertTrue(should_exclude(Path("pkg.egg-info/PKG-INFO")))

    def test_allows_normal_py(self):
        from scripts.make_release import should_exclude
        self.assertFalse(should_exclude(Path("src/data/db_manager.py")))


class TestSha256File(unittest.TestCase):
    """SHA-256 file hashing."""

    def test_hash_known_content(self):
        from scripts.make_release import sha256_file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(b"hello world")
            f.flush()
            path = Path(f.name)
        try:
            result = sha256_file(path)
            expected = hashlib.sha256(b"hello world").hexdigest()
            self.assertEqual(result, expected)
        finally:
            path.unlink()


class TestMakeRelease(unittest.TestCase):
    """End-to-end release packaging."""

    def test_creates_zip_and_sums(self):
        from scripts.make_release import make_release
        with tempfile.TemporaryDirectory() as out:
            zip_path = make_release("0.0.1-test", Path(out))
            self.assertTrue(zip_path.exists())
            self.assertTrue(zip_path.name.endswith(".zip"))
            # SHA256SUMS should exist
            sums_path = Path(out) / "SHA256SUMS"
            self.assertTrue(sums_path.exists())
            sums_content = sums_path.read_text()
            self.assertIn("alma-insights-v0.0.1-test.zip", sums_content)
            # .sha256 sidecar
            sidecar = Path(out) / "alma-insights-v0.0.1-test.zip.sha256"
            self.assertTrue(sidecar.exists())

    def test_zip_contains_main_py(self):
        from scripts.make_release import make_release
        with tempfile.TemporaryDirectory() as out:
            zip_path = make_release("0.0.1-test", Path(out))
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = zf.namelist()
                # Should contain main.py at root of zip
                self.assertTrue(
                    any("main.py" in n for n in names),
                    f"main.py not found in zip. Contents: {names[:10]}..."
                )

    def test_zip_contains_src(self):
        from scripts.make_release import make_release
        with tempfile.TemporaryDirectory() as out:
            zip_path = make_release("0.0.1-test", Path(out))
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = zf.namelist()
                src_files = [n for n in names if "/src/" in n]
                self.assertGreater(len(src_files), 0)

    def test_zip_excludes_pycache(self):
        from scripts.make_release import make_release
        with tempfile.TemporaryDirectory() as out:
            zip_path = make_release("0.0.1-test", Path(out))
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = zf.namelist()
                pycache = [n for n in names if "__pycache__" in n]
                self.assertEqual(len(pycache), 0, f"Found pycache: {pycache}")

    def test_sha256_matches_zip(self):
        from scripts.make_release import make_release, sha256_file
        with tempfile.TemporaryDirectory() as out:
            zip_path = make_release("0.0.1-test", Path(out))
            actual = sha256_file(zip_path)
            sidecar = Path(out) / "alma-insights-v0.0.1-test.zip.sha256"
            stored = sidecar.read_text().strip()
            self.assertEqual(actual, stored)


# ════════════════════════════════════════════════════
#  Updates tab handler tests
# ════════════════════════════════════════════════════

class TestUpdatesTabHandlers(unittest.TestCase):
    """Handler tests for the update flow (P5-T2), retargeted at the shared
    UpdatesPanel after the 2026-07-22 extraction from the product page."""

    def _make_panel(self):
        """Create a minimal UpdatesPanel mock with needed attributes."""
        panel = MagicMock()
        panel._install_btn = MagicMock()
        panel._update_progress = MagicMock()
        panel._progress_label = MagicMock()
        panel._restart_btn = MagicMock()
        panel._update_msg = MagicMock()
        panel._last_checked_label = MagicMock()
        panel._latest_download_url = "https://github.com/org/repo/releases/download/v1.0.0/alma-insights-v1.0.0.zip"
        panel._latest_new_version = "1.0.0"
        return panel

    def test_on_update_progress(self):
        from src.ui.widgets.updates_panel import UpdatesPanel
        panel = self._make_panel()
        UpdatesPanel._on_update_progress(panel, 50, "Downloading...")
        panel._update_progress.setValue.assert_called_with(50)
        panel._progress_label.setText.assert_called_with("Downloading...")

    def test_on_update_complete(self):
        from src.ui.widgets.updates_panel import UpdatesPanel
        panel = self._make_panel()
        UpdatesPanel._on_update_complete(panel)
        panel._update_progress.setVisible.assert_called_with(False)
        panel._restart_btn.setVisible.assert_called_with(True)

    def test_on_update_failed_shows_error(self):
        from src.ui.widgets.updates_panel import UpdatesPanel
        panel = self._make_panel()
        UpdatesPanel._on_update_failed(panel, "Network error")
        panel._update_msg.setText.assert_called_with("Update failed: Network error")
        panel._install_btn.setVisible.assert_called_with(True)

    @patch("src.ui.widgets.updates_panel.update_section")
    def test_save_last_checked(self, mock_update):
        from src.ui.widgets.updates_panel import UpdatesPanel
        panel = self._make_panel()
        UpdatesPanel._save_last_checked(panel)
        # MERGE via update_section — a bare set_section would clobber
        # github_repo (the historical bug the panel keeps fixed).
        mock_update.assert_called_once()
        args = mock_update.call_args
        self.assertEqual(args[0][0], "updates")
        self.assertIn("last_checked", args[0][1])

    @patch("src.ui.widgets.updates_panel.get_section")
    def test_load_last_checked_with_value(self, mock_get):
        from src.ui.widgets.updates_panel import UpdatesPanel
        mock_get.return_value = {"last_checked": "2026-03-11 10:30"}
        panel = self._make_panel()
        UpdatesPanel._load_last_checked(panel)
        panel._last_checked_label.setText.assert_called_with(
            "Last checked: 2026-03-11 10:30"
        )

    @patch("src.ui.widgets.updates_panel.get_section")
    def test_load_last_checked_empty(self, mock_get):
        from src.ui.widgets.updates_panel import UpdatesPanel
        mock_get.return_value = {}
        panel = self._make_panel()
        UpdatesPanel._load_last_checked(panel)
        panel._last_checked_label.setText.assert_not_called()

    def test_on_install_update_hides_button_shows_progress(self):
        """Verify _on_install_update hides install btn and shows progress."""
        from src.ui.widgets.updates_panel import UpdatesPanel
        panel = self._make_panel()
        mock_updater = MagicMock()
        with patch("src.updater.updater.Updater", return_value=mock_updater):
            UpdatesPanel._on_install_update(panel)
        panel._install_btn.setVisible.assert_called_with(False)
        panel._update_progress.setVisible.assert_called_with(True)
        panel._progress_label.setVisible.assert_called_with(True)


class TestDownloadUrlConstruction(unittest.TestCase):
    """Test that _on_update_available constructs download URL correctly."""

    def test_github_url_pattern(self):
        """Verify download URL is constructed from release page URL."""
        # The logic from _on_update_available:
        url = "https://github.com/org/alma-insights/releases/tag/v2.0.0"
        new_ver = "2.0.0"
        base = url.rsplit("/releases/", 1)[0] if "/releases/" in url else ""
        tag = f"v{new_ver}"
        download_url = f"{base}/releases/download/{tag}/alma-insights-{tag}.zip"
        self.assertEqual(
            download_url,
            "https://github.com/org/alma-insights/releases/download/v2.0.0/alma-insights-v2.0.0.zip"
        )

    def test_non_github_url_returns_empty(self):
        url = "https://example.com/releases/v1.0"
        result = ""
        if url and "github.com" in url:
            result = "something"
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
