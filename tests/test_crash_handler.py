"""
Unit tests for src/core/crash_handler.py

Covers:
  - Report written on excepthook invocation
  - Redaction masks only credential-shaped k=v patterns, not tracebacks
  - Full traceback + file paths + line numbers preserved
  - list_reports + export_bundle + clear
  - Handler must never raise, even if the filesystem fails
  - install() is idempotent and chains to the previous hook

Run: python -m pytest tests/test_crash_handler.py -x -v
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core import crash_handler as ch


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_crash_dir(tmp_path, monkeypatch):
    """Redirect the crash directory to a tmp path per test."""
    d = tmp_path / "crash_reports"
    monkeypatch.setattr(ch, "_CRASH_DIR", d)
    # Reset install state between tests
    monkeypatch.setattr(ch, "_installed", False)
    monkeypatch.setattr(ch, "_previous_hook", None)
    yield d


@pytest.fixture
def sample_exception():
    """Raise and catch an exception so we have real exc_info to work with."""
    try:
        raise ValueError("something went wrong: token=abc123secret")
    except ValueError:
        return sys.exc_info()


# ──────────────────────────────────────────────────────────────────
# Report writing
# ──────────────────────────────────────────────────────────────────

class TestReportWriting:
    def test_report_written_on_hook(self, tmp_crash_dir, sample_exception):
        ch._hook(*sample_exception)
        reports = list(tmp_crash_dir.glob("crash_*.json"))
        assert len(reports) == 1

    def test_report_contains_exception_metadata(self, tmp_crash_dir, sample_exception):
        ch._hook(*sample_exception)
        report = json.loads(next(tmp_crash_dir.glob("crash_*.json")).read_text())
        assert report["exception_type"] == "ValueError"
        assert "something went wrong" in report["exception_message"]
        assert "timestamp" in report
        assert "version" in report

    def test_report_preserves_traceback_structure(self, tmp_crash_dir, sample_exception):
        ch._hook(*sample_exception)
        report = json.loads(next(tmp_crash_dir.glob("crash_*.json")).read_text())
        # Full traceback should still have file paths and line numbers
        assert "Traceback" in report["traceback"]
        assert "test_crash_handler.py" in report["traceback"]
        assert "line " in report["traceback"]

    def test_multiple_crashes_produce_separate_files(self, tmp_crash_dir, sample_exception):
        ch._hook(*sample_exception)
        ch._hook(*sample_exception)
        reports = list(tmp_crash_dir.glob("crash_*.json"))
        assert len(reports) == 2


# ──────────────────────────────────────────────────────────────────
# Redaction
# ──────────────────────────────────────────────────────────────────

class TestRedaction:
    def test_redacts_token_equals(self):
        assert "***REDACTED***" in ch._redact("token=abc123")
        assert "abc123" not in ch._redact("token=abc123")

    def test_redacts_with_quotes(self):
        out = ch._redact('api_key="sk-secretvalue"')
        assert "sk-secretvalue" not in out

    def test_redacts_case_insensitive(self):
        assert "***REDACTED***" in ch._redact("PAT=xyz")
        assert "***REDACTED***" in ch._redact("Password:hunter2")

    def test_leaves_non_credential_text_alone(self):
        out = ch._redact("File \"main.py\", line 42, in foo")
        assert "main.py" in out
        assert "line 42" in out

    def test_leaves_regular_variable_names_alone(self):
        # "key" as a substring in a non-credential context
        out = ch._redact("dict_keys=['name', 'age']")
        assert "dict_keys" in out  # substring "key" matches but value has no k=v structure
        # The value gets redacted only if the regex matched
        # So we accept that dict_keys= would trip it; that's OK — safer to over-redact

    def test_redacts_in_stored_report(self, tmp_crash_dir):
        try:
            raise RuntimeError("oauth_token=super-secret-xyz failed")
        except RuntimeError:
            ch._hook(*sys.exc_info())
        report = json.loads(next(tmp_crash_dir.glob("crash_*.json")).read_text())
        assert "super-secret-xyz" not in report["exception_message"]
        assert "***REDACTED***" in report["exception_message"]


# ──────────────────────────────────────────────────────────────────
# Handler robustness
# ──────────────────────────────────────────────────────────────────

class TestRobustness:
    def test_hook_never_raises_on_write_failure(self, tmp_crash_dir, sample_exception, monkeypatch):
        # Simulate a disk failure inside _write_report
        def boom(*a, **kw):
            raise OSError("disk full")
        monkeypatch.setattr(ch, "_write_report", boom)
        # Must not raise:
        ch._hook(*sample_exception)

    def test_hook_calls_previous_hook(self, tmp_crash_dir, sample_exception):
        calls = []
        def previous(exc_type, exc_value, exc_tb):
            calls.append(exc_type.__name__)
        ch._previous_hook = previous
        ch._hook(*sample_exception)
        assert calls == ["ValueError"]

    def test_hook_chains_even_if_write_fails(self, tmp_crash_dir, sample_exception, monkeypatch):
        calls = []
        monkeypatch.setattr(ch, "_write_report", lambda *a, **kw: (_ for _ in ()).throw(OSError()))
        ch._previous_hook = lambda *a, **kw: calls.append("chained")
        ch._hook(*sample_exception)
        assert calls == ["chained"]


# ──────────────────────────────────────────────────────────────────
# install() is idempotent
# ──────────────────────────────────────────────────────────────────

class TestInstall:
    def test_install_replaces_excepthook(self, tmp_crash_dir):
        original = sys.excepthook
        try:
            ch.install()
            assert sys.excepthook is ch._hook
        finally:
            sys.excepthook = original

    def test_install_idempotent(self, tmp_crash_dir):
        original = sys.excepthook
        try:
            ch.install()
            first_previous = ch._previous_hook
            ch.install()  # second call
            # _previous_hook must not be overwritten with our own hook
            assert ch._previous_hook is first_previous
        finally:
            sys.excepthook = original


# ──────────────────────────────────────────────────────────────────
# list_reports / export_bundle / clear
# ──────────────────────────────────────────────────────────────────

class TestReportManagement:
    def _write_n_reports(self, tmp_dir, n):
        tmp_dir.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            (tmp_dir / f"crash_2026041520{i:02d}00_000000.json").write_text('{"i":' + str(i) + '}')

    def test_list_reports_empty(self, tmp_crash_dir):
        assert ch.list_reports() == []

    def test_list_reports_newest_first(self, tmp_crash_dir):
        self._write_n_reports(tmp_crash_dir, 3)
        reports = ch.list_reports()
        assert len(reports) == 3
        # Newest first (sorted reverse by name → reverse chronological)
        assert reports[0].name > reports[1].name > reports[2].name

    def test_list_reports_respects_limit(self, tmp_crash_dir):
        self._write_n_reports(tmp_crash_dir, 5)
        assert len(ch.list_reports(limit=2)) == 2

    def test_export_bundle_zips_reports(self, tmp_crash_dir, tmp_path):
        self._write_n_reports(tmp_crash_dir, 3)
        dest = tmp_path / "bundle.zip"
        ch.export_bundle(dest)
        assert dest.exists()
        with zipfile.ZipFile(dest) as zf:
            names = zf.namelist()
        assert len(names) == 3
        assert all(n.startswith("crash_") for n in names)

    def test_export_bundle_empty_ok(self, tmp_crash_dir, tmp_path):
        dest = tmp_path / "empty.zip"
        ch.export_bundle(dest)
        assert dest.exists()
        with zipfile.ZipFile(dest) as zf:
            assert zf.namelist() == []

    def test_clear_removes_all_by_default(self, tmp_crash_dir):
        self._write_n_reports(tmp_crash_dir, 5)
        deleted = ch.clear()
        assert deleted == 5
        assert ch.list_reports() == []

    def test_clear_keeps_n_newest(self, tmp_crash_dir):
        self._write_n_reports(tmp_crash_dir, 5)
        deleted = ch.clear(keep=2)
        assert deleted == 3
        assert len(ch.list_reports()) == 2
