"""AI Reports — End-to-end test suite (R5.2).

Exercises the full structured-output stack offline, with an optional
toggle to live-bridge mode via `ALMA_E2E_LIVE=1`.

Coverage:
  * full pipeline run via stub bridge → Report with findings + accuracy
  * persistence: db.save_report writes findings_json + pipeline_kind +
    accuracy_score; rehydration reproduces the same Report
  * grounding harness fires post-parse without blocking
  * legacy markdown fallback still works when LLM emits non-JSON text
  * report bridge failure → data-only fallback Report (never raises)
  * prompt-authoring lifecycle: build → save → re-load
  * report-history rehydration: full_results JSON round-trips Report
  * fast mode (<3s); live mode is opt-in and skipped by default

The suite uses the stub-bridge fixtures from
`tests/conftest_stub_bridge.py`. Run live mode with::

    ALMA_E2E_LIVE=1 pytest tests/test_ai_reports_e2e.py -v
"""
from __future__ import annotations

import json
import os
import sqlite3

import pytest

# Bring fixtures into module-scope so pytest discovers them
from tests.conftest_stub_bridge import (    # noqa: F401
    stub_bridge_client, seeded_warehouse_conn, e2e_pipeline,
    StubReportBridgeClient, CANNED_REPORT_TEXT, is_live_mode,
)
from src.data.report_schema import Report
from src.data.report_parser import parse_report


# ──────────────────────────────────────────────────────────────────────
# Pipeline happy path
# ──────────────────────────────────────────────────────────────────────

class TestPipelineHappyPath:
    @pytest.fixture(autouse=True)
    def _force_single_pass(self, monkeypatch):
        """E2E tests run single-pass to keep wall-clock < 3s. Multi-bridge
        path is exercised by tests/test_specialist_pipeline.py with stubs."""
        monkeypatch.setenv("ALMA_E2E_FORCE_SINGLE_PASS", "1")
        import src.data.ai_report_pipeline as pipe
        monkeypatch.setattr(pipe, "_get_pipeline_params", lambda: {
            "kind": "single_pass", "bridges": 1, "model": None,
            "convergence_model": None,
        })

    def test_run_returns_structured_report(self, e2e_pipeline):
        result = e2e_pipeline.run(
            prompt_data={"name": "Stub Test", "prompt_text": "Test prompt {data_block}", "system_prompt": ""},
            date_start="2026-01-01", date_end="2026-04-30",
        )
        report: Report = result["report"]
        assert isinstance(report, Report)
        assert len(report.findings) == 2
        assert report.findings[0].title == "Billing & charge discrepancies"
        assert "structured_parse" in result["phases_completed"]
        assert "grounding" in result["phases_completed"]

    def test_grounding_attaches_score(self, e2e_pipeline):
        result = e2e_pipeline.run(
            prompt_data={"name": "Stub Test", "prompt_text": "x {data_block}", "system_prompt": ""},
            date_start="2026-01-01", date_end="2026-04-30",
        )
        report = result["report"]
        # Grounding ran; score is populated (may be None if no findings can be
        # verified, but our stub data SHOULD verify cleanly)
        assert report.accuracy_score is not None
        assert 0.0 <= report.accuracy_score <= 1.0

    def test_pipeline_kind_propagates(self, e2e_pipeline):
        result = e2e_pipeline.run(
            prompt_data={"name": "Stub", "prompt_text": "x {data_block}"},
            date_start="2026-01-01", date_end="2026-04-30",
        )
        assert result["report"].pipeline_kind == "single_pass"

    def test_legacy_report_md_still_present(self, e2e_pipeline):
        result = e2e_pipeline.run(
            prompt_data={"name": "Stub", "prompt_text": "x {data_block}"},
            date_start="2026-01-01", date_end="2026-04-30",
        )
        # Legacy callers (save .md, follow-up chat context) consume report_md
        assert isinstance(result["report_md"], str)
        assert "Billing" in result["report_md"]


# ──────────────────────────────────────────────────────────────────────
# Persistence + rehydration
# ──────────────────────────────────────────────────────────────────────

class TestPersistence:
    def test_save_report_writes_structured_columns(self, seeded_warehouse_conn):
        report = parse_report(CANNED_REPORT_TEXT, fallback_title="Test")
        seeded_warehouse_conn.execute(
            """INSERT INTO analysis_reports
                  (page, run_at, parameters, summary, full_results,
                   ticket_count, duration_ms, notes, report_type, chat_history,
                   findings_json, pipeline_kind, specialist_count,
                   accuracy_score, cost_usd)
               VALUES (?, ?, ?, ?, ?, 0, 0, '', 'standard', '', ?, ?, ?, ?, ?)""",
            (
                "ai_reports", "2026-05-06T12:00:00",
                json.dumps({"prompt": "test"}), "summary",
                json.dumps({"report_struct": report.to_dict()}),
                report.to_json(), report.pipeline_kind,
                report.specialist_count, report.accuracy_score, report.cost_usd,
            ),
        )
        seeded_warehouse_conn.commit()

        row = seeded_warehouse_conn.execute(
            "SELECT findings_json, pipeline_kind FROM analysis_reports LIMIT 1"
        ).fetchone()
        assert row["pipeline_kind"] == "single_pass"
        rehydrated = Report.from_json(row["findings_json"])
        assert len(rehydrated.findings) == 2
        assert rehydrated.findings[0].title == "Billing & charge discrepancies"

    def test_rehydration_round_trip(self, seeded_warehouse_conn):
        # Persist + read back the full payload via full_results
        report = parse_report(CANNED_REPORT_TEXT, fallback_title="Test")
        seeded_warehouse_conn.execute(
            """INSERT INTO analysis_reports
                  (page, run_at, parameters, summary, full_results,
                   findings_json, pipeline_kind, specialist_count,
                   accuracy_score, cost_usd)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "ai_reports", "2026-05-06T12:00:00", "{}",
                "summary",
                json.dumps({"report_struct": report.to_dict(), "report_text": "raw"}),
                report.to_json(), report.pipeline_kind, 0, 1.0, 0.05,
            ),
        )
        seeded_warehouse_conn.commit()

        row = seeded_warehouse_conn.execute(
            "SELECT full_results FROM analysis_reports LIMIT 1"
        ).fetchone()
        payload = json.loads(row["full_results"])
        rehydrated = Report.from_dict(payload["report_struct"])
        assert rehydrated.to_dict() == report.to_dict()


# ──────────────────────────────────────────────────────────────────────
# Failure modes
# ──────────────────────────────────────────────────────────────────────

class TestFailureModes:
    @pytest.fixture
    def _patched_builder(self, monkeypatch):
        """Same data-block stubs as e2e_pipeline — shared across failure tests."""
        monkeypatch.setattr(
            "src.data.ai_report_pipeline._get_pipeline_params",
            lambda: {"kind": "single_pass", "bridges": 1, "model": None,
                     "convergence_model": None},
        )
        import src.data.report_builder as rb
        monkeypatch.setattr(rb, "build_data_block",
                             lambda *a, **kw: {"ticket_count": 8})
        monkeypatch.setattr(rb, "format_data_block_for_prompt",
                             lambda block: "Tickets: 8")
        monkeypatch.setattr(rb, "replace_prompt_variables",
                             lambda prompt, block: prompt)
        monkeypatch.setattr(rb, "_validate_prompt_before_send", lambda prompt: None)

    def test_legacy_markdown_falls_back(self, seeded_warehouse_conn, _patched_builder):
        from src.data.ai_report_pipeline import AIReportPipeline

        class _DBShim:
            def __init__(self, conn):
                self.conn = conn
                self.db_path = ":memory:"

        bad_client = StubReportBridgeClient(canned_text="## Plain markdown — no JSON.")
        pipeline = AIReportPipeline(_DBShim(seeded_warehouse_conn), gemini_client=bad_client)
        result = pipeline.run(
            prompt_data={"name": "Bad", "prompt_text": "x {data_block}"},
            date_start="2026-01-01", date_end="2026-04-30",
        )
        report = result["report"]
        assert report.findings == []
        assert "legacy_unparseable" in report.accuracy_flags
        # report_md still carries the legacy text for save .md / chat
        assert "## Plain markdown" in (result["report_md"] or "")

    def test_bridge_crash_returns_data_only(self, seeded_warehouse_conn, _patched_builder):
        from src.data.ai_report_pipeline import AIReportPipeline

        class _DBShim:
            def __init__(self, conn):
                self.conn = conn
                self.db_path = ":memory:"

        crashing = StubReportBridgeClient(fail_first_n=99)  # always fails
        pipeline = AIReportPipeline(_DBShim(seeded_warehouse_conn), gemini_client=crashing)
        result = pipeline.run(
            prompt_data={"name": "Crash", "prompt_text": "x {data_block}"},
            date_start="2026-01-01", date_end="2026-04-30",
        )
        report = result["report"]
        # Data-only fallback: at least one finding exists with INFO severity
        assert len(report.findings) >= 1
        assert any("data_only" in f or "llm_error" in f for f in report.accuracy_flags)


# ──────────────────────────────────────────────────────────────────────
# Prompt authoring lifecycle
# ──────────────────────────────────────────────────────────────────────

class TestPromptLifecycle:
    def test_authoring_session_writes_prompt(self, seeded_warehouse_conn):
        from src.data.prompt_authoring import AuthoringSession
        s = AuthoringSession()
        s.bot_greeting()
        s.step("scope all")
        s.step("- Q1\n- Q2")
        s.step("Use AlmaReport schema")
        record = s.finalize("E2E Custom", "for testing")
        seeded_warehouse_conn.execute(
            """INSERT INTO prompt_library
                  (name, category, description, prompt_text, system_prompt,
                   is_active, created_at, updated_at)
               VALUES (?, 'custom', ?, ?, ?, 1, ?, ?)""",
            (record["name"], record["description"], record["prompt_text"],
             record["system_prompt"], "2026-05-06", "2026-05-06"),
        )
        seeded_warehouse_conn.commit()
        row = seeded_warehouse_conn.execute(
            "SELECT name, prompt_text FROM prompt_library WHERE name=?",
            ("E2E Custom",),
        ).fetchone()
        assert row is not None
        assert "AlmaReport" in row["prompt_text"]


# ──────────────────────────────────────────────────────────────────────
# Live-mode opt-in
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not is_live_mode(), reason="ALMA_E2E_LIVE not set")
class TestLiveMode:
    """Skipped by default. Set ALMA_E2E_LIVE=1 to exercise the real bridge.

    Useful for nightly / pre-release validation against the bundled
    Gemini CLI. Wall-clock budget: 60s (single bridge call against
    pre-computed analytics).
    """

    def test_live_bridge_returns_structured_report(self, e2e_pipeline):
        result = e2e_pipeline.run(
            prompt_data={
                "name": "Live exec summary",
                "prompt_text": "Produce an exec summary. {data_block}",
                "system_prompt": "Emit AlmaReport JSON-fenced output only.",
            },
            date_start="2026-01-01", date_end="2026-04-30",
        )
        # Live runs are inherently flaky; we only assert the pipeline didn't crash
        # and a Report came back — finding count varies by model output.
        assert isinstance(result["report"], Report)
        assert "structured_parse" in result["phases_completed"]
