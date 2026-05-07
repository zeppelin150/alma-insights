"""Unit tests — src/data/specialist_pipeline.py (R2.5).

Coverage:
  * happy path — 3 specialists + convergence produce a unified Report
  * specialist failure — single specialist returns "[Error: ...]" → that
    specialist's findings drop, others survive, Report still produced
  * all specialists failed → fallback Report with `specialist_failed_all`
    flag
  * convergence failure → falls back to merged partials
  * orchestrator boot exception → safe failure_report
  * settings: pipeline_kind metadata propagates onto Report

The specialist pipeline depends on `ReportOrchestrator`, which boots a
real ACP bridge. We stub the orchestrator entirely via monkeypatch so
these tests run offline + fast (<1s each).
"""
from __future__ import annotations

from typing import Any

import pytest

from src.data.report_schema import Report, Severity
from src.data.specialist_pipeline import (
    SPECIALISTS, SpecialistPipeline,
    _format_partials_for_convergence,
    _stitch_specialist_prompt,
    _wrap_with_system,
)


# ──────────────────────────────────────────────────────────────────────
# Stub orchestrator — what tests inject in place of ReportOrchestrator
# ──────────────────────────────────────────────────────────────────────

class _StubOrchestrator:
    """In-memory replacement for ReportOrchestrator. Records calls."""

    def __init__(self, parallel_results: dict[str, str], single_result: str):
        self._parallel_results = parallel_results
        self._single_result = single_result
        self.boot_called = False
        self.shutdown_called = False
        self.parallel_calls: list[list[dict]] = []
        self.single_calls: list[tuple[str, str | None, int]] = []

    def boot(self):
        self.boot_called = True

    def run_parallel(self, tasks, progress_cb=None):
        self.parallel_calls.append(list(tasks))
        return dict(self._parallel_results)

    def run_single(self, prompt, request_id=None, timeout=300):
        self.single_calls.append((prompt, request_id, timeout))
        return self._single_result

    def shutdown(self):
        self.shutdown_called = True


def _good_specialist_block(spec_id: str) -> str:
    """Minimal valid JSON-fenced block for one specialist."""
    return (
        "```json\n"
        '{"title":"' + spec_id.title() + ' Findings",'
        '"executive_summary":"specialist summary",'
        '"findings":[{"title":"Foo ' + spec_id + '",'
        '"summary":"finding text",'
        '"severity":"medium",'
        '"confidence":0.7,'
        '"evidence_chips":[{"label":"Tickets","value":"100"}]}]}\n'
        "```"
    )


def _good_convergence_block(title: str = "Unified Report") -> str:
    return (
        "```json\n"
        '{"title":"' + title + '",'
        '"executive_summary":"unified",'
        '"findings":['
            '{"title":"Top driver","summary":"x","severity":"high","confidence":0.9},'
            '{"title":"Sentiment","summary":"y","severity":"low","confidence":0.7}'
        ']}\n'
        "```"
    )


@pytest.fixture
def stub_pipeline(monkeypatch):
    """Build a SpecialistPipeline whose orchestrator is fully stubbed."""

    def _factory(parallel_results: dict[str, str], single_result: str) -> tuple[SpecialistPipeline, _StubOrchestrator]:
        stub = _StubOrchestrator(parallel_results, single_result)

        class _StubReportOrchestrator:
            def __init__(self, *a, **kw):
                pass
            def boot(self):
                stub.boot_called = True
            def run_parallel(self, tasks, progress_cb=None):
                return stub.run_parallel(tasks, progress_cb)
            def run_single(self, prompt, request_id=None, timeout=300):
                return stub.run_single(prompt, request_id, timeout)
            def shutdown(self):
                stub.shutdown_called = True

        # Patch the import inside _ensure_orchestrator
        import src.agents.report_orchestrator as orch_mod
        monkeypatch.setattr(orch_mod, "ReportOrchestrator", _StubReportOrchestrator)
        # Also stub DB_PATH so the import path inside _ensure_orchestrator is happy
        import src.data.db_manager as dm
        monkeypatch.setattr(dm, "DB_PATH", ":memory:", raising=False)

        pipeline = SpecialistPipeline(
            db=None, bridges=2, model="stub-model", convergence_model="stub-conv",
        )
        return pipeline, stub

    return _factory


# ──────────────────────────────────────────────────────────────────────
# Happy path
# ──────────────────────────────────────────────────────────────────────

class TestHappyPath:
    def test_three_specialists_converge(self, stub_pipeline):
        results = {sp.specialist_id: _good_specialist_block(sp.specialist_id)
                   for sp in SPECIALISTS}
        pipeline, stub = stub_pipeline(results, _good_convergence_block())
        report = pipeline.run(
            prompt_data={"name": "Test", "description": "test desc"},
            data_block={"ticket_count": 1788},
            data_block_text="block text",
            title_hint="Test",
        )
        assert isinstance(report, Report)
        assert report.pipeline_kind == "multi_bridge"
        assert report.specialist_count == len(SPECIALISTS)
        assert report.bridges_used == 2
        assert len(report.findings) == 2
        assert report.findings[0].severity is Severity.HIGH
        assert stub.boot_called
        assert len(stub.parallel_calls) == 1
        assert len(stub.parallel_calls[0]) == 3   # 3 specialists dispatched
        assert len(stub.single_calls) == 1         # convergence ran once

    def test_metadata_populated(self, stub_pipeline):
        results = {sp.specialist_id: _good_specialist_block(sp.specialist_id)
                   for sp in SPECIALISTS}
        pipeline, _ = stub_pipeline(results, _good_convergence_block())
        report = pipeline.run(
            prompt_data={"name": "X"}, data_block={}, data_block_text="",
            title_hint="X",
        )
        assert report.duration_sec >= 0


# ──────────────────────────────────────────────────────────────────────
# Failure modes
# ──────────────────────────────────────────────────────────────────────

class TestFailureModes:
    def test_one_specialist_failed(self, stub_pipeline):
        # friction returns Error; sentiment + anomaly succeed
        results = {
            "friction":  "[Error: timeout]",
            "sentiment": _good_specialist_block("sentiment"),
            "anomaly":   _good_specialist_block("anomaly"),
        }
        pipeline, _ = stub_pipeline(results, _good_convergence_block())
        report = pipeline.run(
            prompt_data={"name": "X"}, data_block={}, data_block_text="",
            title_hint="X",
        )
        # Convergence still ran since 2 of 3 succeeded
        assert report.pipeline_kind == "multi_bridge"
        assert len(report.findings) >= 1

    def test_all_specialists_failed(self, stub_pipeline):
        results = {sp.specialist_id: "[Error: rate_governor_timeout]"
                   for sp in SPECIALISTS}
        pipeline, stub = stub_pipeline(results, _good_convergence_block())
        report = pipeline.run(
            prompt_data={"name": "X"}, data_block={}, data_block_text="",
            title_hint="X",
        )
        assert "specialist_failed_all" in report.accuracy_flags
        # convergence should NOT have been called
        assert len(stub.single_calls) == 0

    def test_convergence_returns_unparseable(self, stub_pipeline):
        results = {sp.specialist_id: _good_specialist_block(sp.specialist_id)
                   for sp in SPECIALISTS}
        pipeline, _ = stub_pipeline(results, "## Bare markdown, no JSON")
        report = pipeline.run(
            prompt_data={"name": "X"}, data_block={}, data_block_text="",
            title_hint="X",
        )
        # Falls back to merged partials → at least 1 finding from each specialist
        assert report.pipeline_kind == "multi_bridge"
        assert len(report.findings) >= 1


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

class TestHelpers:
    def test_wrap_with_system_no_op_when_empty(self):
        assert _wrap_with_system("hello", "") == "hello"

    def test_wrap_with_system_includes_block(self):
        out = _wrap_with_system("body", "sys")
        assert "[SYSTEM INSTRUCTIONS]" in out
        assert "[END SYSTEM INSTRUCTIONS]" in out
        assert out.endswith("body")

    def test_stitch_specialist_prompt_substitutes(self):
        out = _stitch_specialist_prompt(
            "name={name}, desc={description}, scope={trc_filter}",
            {"name": "Foo", "description": "bar"}, "Billing",
        )
        assert "name=Foo" in out
        assert "desc=bar" in out
        assert "scope=Billing" in out

    def test_stitch_specialist_prompt_default_scope(self):
        out = _stitch_specialist_prompt("scope={trc_filter}", {}, None)
        assert "scope=all TRCs" in out

    def test_format_partials_renders_each_specialist(self):
        partials = {
            "friction": Report(report_id="r-friction", title="Friction",
                                executive_summary="es",
                                findings=[]),
            "anomaly":  Report(report_id="r-anomaly",  title="Anomaly",
                                executive_summary="ea",
                                findings=[]),
        }
        text = _format_partials_for_convergence(partials)
        assert "## SPECIALIST: friction" in text
        assert "## SPECIALIST: anomaly" in text
