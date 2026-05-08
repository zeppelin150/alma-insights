"""
Smart Reporting — dropdown migration + custom pipeline persistence
==================================================================

Covers the 2026-05-07 rebuild:
- Lookback / NLP budget / NLP workers are QComboBox (not QSpinBox), with the
  expected default values.
- _build_pipeline_config reads the dropdown selections via currentData()
  and produces a config dict the smart_pipeline.run_pipeline runtime expects.
- DatabaseManager round-trips report_definitions through
  list_report_definitions / get_report_definition / save_report_definition /
  delete_report_definition.
- PipelineEditDialog's _on_save persists a new definition and edits keep
  unrelated rich-config keys intact.

Run: python -m pytest tests/test_smart_reporting_dropdowns.py -x -v
"""

from pathlib import Path
import json
import pytest

from PySide6.QtWidgets import QApplication, QComboBox, QDialog

from src.data.db_manager import DatabaseManager
from src.ui.pages.smart_reporting import (
    LOOKBACK_OPTIONS, NLP_BUDGET_OPTIONS,
    DEFAULT_LOOKBACK_DAYS, DEFAULT_NLP_BUDGET,
    SmartReportingPage, PipelineEditDialog, _populate_combo,
    _resolve_nlp_workers,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def fresh_db(tmp_path):
    db = DatabaseManager(tmp_path / "smart_reporting_test.db")
    db.initialize()
    yield db
    db.close()


# ── DB helper tests ───────────────────────────────────────────────────


class TestReportDefinitionsRoundTrip:
    def test_list_empty_initially(self, fresh_db):
        assert fresh_db.list_report_definitions() == []

    def test_save_then_list(self, fresh_db):
        fresh_db.save_report_definition(
            definition_id="weekly_billing",
            name="Weekly Billing",
            description="Charge discrepancy focus",
            config={"lookback_days": 14, "prompt_id": 7, "nlp_workers": 2},
            is_active=True,
        )
        rows = fresh_db.list_report_definitions()
        assert len(rows) == 1
        assert rows[0]["definition_id"] == "weekly_billing"
        assert rows[0]["name"] == "Weekly Billing"
        assert rows[0]["config_dict"]["lookback_days"] == 14
        assert rows[0]["config_dict"]["prompt_id"] == 7

    def test_get_returns_dict_with_parsed_config(self, fresh_db):
        fresh_db.save_report_definition(
            "p1", "Pipe One", "x",
            {"lookback_days": 30, "specialists": [{"name": "pattern"}]},
        )
        got = fresh_db.get_report_definition("p1")
        assert got is not None
        # Rich keys round-trip untouched
        assert got["config_dict"]["specialists"][0]["name"] == "pattern"
        assert got["config_dict"]["lookback_days"] == 30

    def test_save_is_upsert(self, fresh_db):
        fresh_db.save_report_definition("p1", "Original", "v1", {"lookback_days": 7})
        fresh_db.save_report_definition("p1", "Renamed", "v2", {"lookback_days": 90})
        rows = fresh_db.list_report_definitions()
        assert len(rows) == 1
        assert rows[0]["name"] == "Renamed"
        assert rows[0]["config_dict"]["lookback_days"] == 90

    def test_delete(self, fresh_db):
        fresh_db.save_report_definition("p1", "x", "x", {})
        fresh_db.save_report_definition("p2", "y", "y", {})
        fresh_db.delete_report_definition("p1")
        ids = {r["definition_id"] for r in fresh_db.list_report_definitions()}
        assert ids == {"p2"}

    def test_get_unknown_returns_none(self, fresh_db):
        assert fresh_db.get_report_definition("does_not_exist") is None


# ── Dropdown unit tests ───────────────────────────────────────────────


class TestDropdownConstants:
    def test_lookback_options_include_default(self):
        assert any(v == DEFAULT_LOOKBACK_DAYS for _, v in LOOKBACK_OPTIONS)

    def test_lookback_options_only_ints(self):
        for label, value in LOOKBACK_OPTIONS:
            assert isinstance(value, int)
            assert isinstance(label, str)

    def test_budget_options_include_default(self):
        assert any(abs(v - DEFAULT_NLP_BUDGET) < 1e-9 for _, v in NLP_BUDGET_OPTIONS)

    def test_resolve_nlp_workers_reads_settings(self, monkeypatch):
        """nlp_workers comes from settings.yaml (the same setting Cost
        Dashboard writes), capped at 32, floored at 1."""
        import src.ui.pages.smart_reporting as sr
        monkeypatch.setattr(
            "src.data.settings_manager.get_section",
            lambda key, default=None: {"parallel_workers": 16}
                if key == "nlp_scan" else (default or {}),
        )
        assert _resolve_nlp_workers() == 16

    def test_resolve_nlp_workers_caps_at_32(self, monkeypatch):
        monkeypatch.setattr(
            "src.data.settings_manager.get_section",
            lambda key, default=None: {"parallel_workers": 999}
                if key == "nlp_scan" else (default or {}),
        )
        assert _resolve_nlp_workers() == 32

    def test_resolve_nlp_workers_floors_at_1(self, monkeypatch):
        monkeypatch.setattr(
            "src.data.settings_manager.get_section",
            lambda key, default=None: {"parallel_workers": 0}
                if key == "nlp_scan" else (default or {}),
        )
        assert _resolve_nlp_workers() == 1

    def test_resolve_nlp_workers_default_when_unset(self, monkeypatch):
        monkeypatch.setattr(
            "src.data.settings_manager.get_section",
            lambda key, default=None: default or {},
        )
        assert _resolve_nlp_workers() == 8

    def test_populate_combo_selects_default(self, qapp):
        combo = QComboBox()
        _populate_combo(combo, LOOKBACK_OPTIONS, DEFAULT_LOOKBACK_DAYS)
        assert combo.currentData() == DEFAULT_LOOKBACK_DAYS

    def test_populate_combo_falls_back_to_first_when_default_missing(self, qapp):
        combo = QComboBox()
        _populate_combo(combo, [("A", 1), ("B", 2)], 999)
        assert combo.currentIndex() == 0
        assert combo.currentData() == 1


# ── SmartReportingPage form-state tests ───────────────────────────────


class TestSmartReportingPageDropdowns:
    def test_page_uses_comboboxes_not_spinboxes(self, qapp, fresh_db):
        page = SmartReportingPage(fresh_db)
        assert isinstance(page._smart_days_combo, QComboBox), \
            "lookback days widget must be a QComboBox after the rebuild"
        assert isinstance(page._nlp_budget_combo, QComboBox), \
            "NLP budget cap widget must be a QComboBox"
        # The old spinbox attributes are gone
        assert not hasattr(page, "_smart_days_spin")
        assert not hasattr(page, "_nlp_budget")
        assert not hasattr(page, "_nlp_workers")
        # NLP workers is now a read-only hint, NOT a dropdown — single source
        # of truth lives in settings.yaml
        assert not hasattr(page, "_nlp_workers_combo")
        assert hasattr(page, "_nlp_workers_hint")

    def test_default_dropdown_values(self, qapp, fresh_db):
        page = SmartReportingPage(fresh_db)
        assert page._smart_days_combo.currentData() == DEFAULT_LOOKBACK_DAYS
        assert page._nlp_budget_combo.currentData() == DEFAULT_NLP_BUDGET

    def test_build_pipeline_config_reads_dropdowns(self, qapp, fresh_db, monkeypatch):
        # Stub settings to control the resolved worker count
        monkeypatch.setattr(
            "src.data.settings_manager.get_section",
            lambda key, default=None: {"parallel_workers": 12}
                if key == "nlp_scan" else (default or {}),
        )
        page = SmartReportingPage(fresh_db)
        # Switch lookback to 90 days, budget to $25
        for i in range(page._smart_days_combo.count()):
            if page._smart_days_combo.itemData(i) == 90:
                page._smart_days_combo.setCurrentIndex(i); break
        for i in range(page._nlp_budget_combo.count()):
            if page._nlp_budget_combo.itemData(i) == 25.0:
                page._nlp_budget_combo.setCurrentIndex(i); break

        cfg = page._build_pipeline_config(trigger_source="unit_test")
        assert cfg["lookback_days"] == 90
        assert cfg["nlp_budget_cap"] == 25.0
        # nlp_workers comes from settings, not a UI control
        assert cfg["nlp_workers"] == 12
        assert cfg["trigger_source"] == "unit_test"
        assert cfg["db_path"] == str(fresh_db.db_path)


# ── PipelineEditDialog tests ──────────────────────────────────────────


class TestPipelineEditDialogPersistence:
    def test_save_new_pipeline_persists_via_db(self, qapp, fresh_db):
        dlg = PipelineEditDialog(fresh_db, definition=None)
        dlg._name_edit.setText("Custom Pipe")
        dlg._desc_edit.setPlainText("A test pipeline")
        # Pick lookback = 60
        for i in range(dlg._lookback_combo.count()):
            if dlg._lookback_combo.itemData(i) == 60:
                dlg._lookback_combo.setCurrentIndex(i); break
        dlg._enabled_checkbox.setChecked(True)
        dlg._on_save()
        # Should have closed (accepted)
        assert dlg.result() == QDialog.Accepted

        rows = fresh_db.list_report_definitions()
        assert len(rows) == 1
        assert rows[0]["name"] == "Custom Pipe"
        assert rows[0]["description"] == "A test pipeline"
        assert rows[0]["config_dict"]["lookback_days"] == 60
        assert rows[0]["is_active"] == 1

    def test_edit_preserves_rich_config_keys(self, qapp, fresh_db):
        # Seed with rich config (specialists, convergence) the dialog won't touch
        fresh_db.save_report_definition(
            "voc_root_cause", "VOC Root Cause", "Old desc",
            {
                "lookback_days": 30,
                "specialists": [{"name": "pattern_detector"}],
                "convergence": {"target_lines": "300-500"},
                "prompt_id": None,
            },
            is_active=True,
        )
        loaded = fresh_db.get_report_definition("voc_root_cause")
        dlg = PipelineEditDialog(fresh_db, definition=loaded)
        # Change only name + lookback; rich keys should round-trip
        dlg._name_edit.setText("VOC Root Cause v2")
        for i in range(dlg._lookback_combo.count()):
            if dlg._lookback_combo.itemData(i) == 180:
                dlg._lookback_combo.setCurrentIndex(i); break
        dlg._on_save()

        updated = fresh_db.get_report_definition("voc_root_cause")
        assert updated["name"] == "VOC Root Cause v2"
        assert updated["config_dict"]["lookback_days"] == 180
        # Rich keys preserved
        assert updated["config_dict"]["specialists"][0]["name"] == "pattern_detector"
        assert updated["config_dict"]["convergence"]["target_lines"] == "300-500"

    def test_save_rejects_empty_name(self, qapp, fresh_db, monkeypatch):
        # Capture the warning by stubbing QMessageBox.warning
        from PySide6.QtWidgets import QMessageBox
        called = {"n": 0}
        monkeypatch.setattr(QMessageBox, "warning",
                             lambda *a, **kw: called.__setitem__("n", called["n"] + 1))
        dlg = PipelineEditDialog(fresh_db, definition=None)
        dlg._name_edit.setText("   ")
        dlg._on_save()
        assert called["n"] == 1
        # No row written
        assert fresh_db.list_report_definitions() == []


# ── End-to-end wiring tests (Run Now → SmartPipelineWorker) ───────────


class TestRunNowEndToEndWiring:
    """Drive the full path the new 'Run Now' card button triggers.

    We replace SmartPipelineWorker with a stub so no real Gemini calls
    happen, but we still exercise:
        _on_run_pipeline_card → get_report_definition →
        _build_pipeline_config_from_definition → _start_pipeline_worker
    """

    def test_run_now_invokes_worker_with_saved_config(
        self, qapp, fresh_db, monkeypatch
    ):
        # Stub settings so nlp_workers resolves to a known value
        monkeypatch.setattr(
            "src.data.settings_manager.get_section",
            lambda key, default=None: {"parallel_workers": 16}
                if key == "nlp_scan" else (default or {}),
        )

        # Save a pipeline with non-default settings (note: nlp_workers in
        # config is intentionally ignored — settings.yaml is authoritative)
        fresh_db.save_report_definition(
            "weekly_report", "Weekly Report", "Test pipeline",
            {
                "lookback_days": 14,
                "nlp_workers": 2,           # stale field, must be ignored
                "nlp_budget_cap": 25.0,
                "nlp_scan": True,
                "prompt_id": None,
            },
            is_active=True,
        )

        page = SmartReportingPage(fresh_db)

        # Stub out the worker so it captures the config without running anything
        captured = {}

        class StubWorker:
            def __init__(self, config):
                captured["config"] = config

            def isRunning(self):
                return False

            # Signals the page connects to — make them no-ops
            class _Sig:
                def connect(self, *_a, **_kw):
                    pass

            progress = _Sig()
            finished = _Sig()
            error = _Sig()

            def start(self):
                captured["started"] = True

        monkeypatch.setattr(
            "src.ui.pages.smart_reporting.SmartPipelineWorker", StubWorker
        )

        # Allow Run Now button regardless of Gemini availability for this test
        page._gemini_available = True

        page._on_run_pipeline_card("weekly_report")

        assert captured.get("started") is True
        cfg = captured["config"]
        assert cfg["lookback_days"] == 14
        # workers always resolved from settings, regardless of saved config
        assert cfg["nlp_workers"] == 16
        assert cfg["nlp_budget_cap"] == 25.0
        assert cfg["nlp_scan"] is True
        assert cfg["definition_id"] == "weekly_report"
        assert cfg["definition_name"] == "Weekly Report"
        assert cfg["trigger_source"] == "pipeline_card"
        assert cfg["db_path"] == str(fresh_db.db_path)


class TestSmartPipelineEntrypointReachable:
    """Sanity check: smart_pipeline.run_pipeline can be invoked with the
    config dict the page produces, and degrades gracefully on an empty DB.

    This proves the entry point is wired to the real pipeline (no rename,
    no missing import) without burning a real Gemini run."""

    def test_run_pipeline_handles_empty_db_gracefully(self, fresh_db):
        from src.data.smart_pipeline import run_pipeline
        cfg = {
            "db_path": str(fresh_db.db_path),
            "lookback_days": 7,
            "nlp_scan": False,
            "trigger_source": "unit_test",
        }
        result = run_pipeline(cfg, progress_cb=None)
        # Empty DB → pipeline should report failure, not crash
        assert isinstance(result, dict)
        assert result.get("success") is False
        # It either errors at "no data" (most likely) or fails some later step,
        # but it must return a dict with the documented contract.
        for key in ("success", "duration_ms", "steps_completed", "error"):
            assert key in result, f"missing key {key} in run_pipeline result"
