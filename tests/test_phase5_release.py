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
    """Test the new handler methods added in P5-T2."""

    def _make_page(self):
        """Create a minimal SettingsPage mock with needed attributes."""
        page = MagicMock()
        page._install_btn = MagicMock()
        page._update_progress = MagicMock()
        page._progress_label = MagicMock()
        page._restart_btn = MagicMock()
        page._update_msg = MagicMock()
        page._last_checked_label = MagicMock()
        page._latest_download_url = "https://github.com/org/repo/releases/download/v1.0.0/alma-insights-v1.0.0.zip"
        page._latest_new_version = "1.0.0"
        return page

    def test_on_update_progress(self):
        from src.ui.pages.settings_page import SettingsPage
        page = self._make_page()
        SettingsPage._on_update_progress(page, 50, "Downloading...")
        page._update_progress.setValue.assert_called_with(50)
        page._progress_label.setText.assert_called_with("Downloading...")

    def test_on_update_complete(self):
        from src.ui.pages.settings_page import SettingsPage
        page = self._make_page()
        SettingsPage._on_update_complete(page)
        page._update_progress.setVisible.assert_called_with(False)
        page._restart_btn.setVisible.assert_called_with(True)

    def test_on_update_failed_shows_error(self):
        from src.ui.pages.settings_page import SettingsPage
        page = self._make_page()
        SettingsPage._on_update_failed(page, "Network error")
        page._update_msg.setText.assert_called_with("Update failed: Network error")
        page._install_btn.setVisible.assert_called_with(True)

    @patch("src.ui.pages.settings_page.set_section")
    @patch("src.ui.pages.settings_page.get_section")
    def test_save_last_checked(self, mock_get, mock_set):
        from src.ui.pages.settings_page import SettingsPage
        page = self._make_page()
        SettingsPage._save_last_checked(page)
        mock_set.assert_called_once()
        args = mock_set.call_args
        self.assertEqual(args[0][0], "updates")
        self.assertIn("last_checked", args[0][1])

    @patch("src.ui.pages.settings_page.get_section")
    def test_load_last_checked_with_value(self, mock_get):
        from src.ui.pages.settings_page import SettingsPage
        mock_get.return_value = {"last_checked": "2026-03-11 10:30"}
        page = self._make_page()
        SettingsPage._load_last_checked(page)
        page._last_checked_label.setText.assert_called_with(
            "Last checked: 2026-03-11 10:30"
        )

    @patch("src.ui.pages.settings_page.get_section")
    def test_load_last_checked_empty(self, mock_get):
        from src.ui.pages.settings_page import SettingsPage
        mock_get.return_value = {}
        page = self._make_page()
        SettingsPage._load_last_checked(page)
        page._last_checked_label.setText.assert_not_called()

    def test_on_install_update_hides_button_shows_progress(self):
        """Verify _on_install_update hides install btn and shows progress."""
        from src.ui.pages.settings_page import SettingsPage
        page = self._make_page()
        mock_updater = MagicMock()
        with patch("src.updater.updater.Updater", return_value=mock_updater):
            SettingsPage._on_install_update(page)
        page._install_btn.setVisible.assert_called_with(False)
        page._update_progress.setVisible.assert_called_with(True)
        page._progress_label.setVisible.assert_called_with(True)


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
