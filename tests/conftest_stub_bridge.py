"""Test fixtures — Stub bridge client for AI Reports E2E tests (R5.1).

Provides `StubReportBridgeClient` — a drop-in replacement for
`ReportBridgeClient` that returns canned JSON-fenced Report output. Lets
the AI Reports E2E suite run offline + fast (<10s end-to-end), with an
opt-in toggle to swap in a real bridge for live verification.

Activation
----------
Default: stub mode (zero env vars set).
Live mode: set `ALMA_E2E_LIVE=1` before running pytest. The fixture then
yields a real `ReportBridgeClient` configured per `ai.active_model` and
no canned output is injected.

Usage in test files::

    from tests.conftest_stub_bridge import (
        stub_bridge_client, e2e_pipeline, _CANNED_REPORT_JSON,
    )

    def test_full_pipeline(stub_bridge_client, seeded_db):
        ...

Public API
----------
- `StubReportBridgeClient(canned_text: str | None = None)`
- pytest fixtures: `stub_bridge_client`, `e2e_pipeline`,
  `seeded_warehouse_conn`, `is_live_mode()`

Reference: R5 spec in conversation history (2026-05-06).
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

# ──────────────────────────────────────────────────────────────────────
# Canned structured output — well-formed JSON-fenced Report
# ──────────────────────────────────────────────────────────────────────

_CANNED_REPORT: dict = {
    "title": "Stub Executive Summary",
    "executive_summary": (
        "Net negative trajectory driven by billing disputes; CSAT "
        "degraded 0.3 points across primary cohorts."
    ),
    "findings": [
        {
            "finding_id": "f-stub-01",
            "title": "Billing & charge discrepancies",
            "summary": "Auto-pay duplicate charges across 3 payers.",
            "severity": "high",
            "confidence": 0.9,
            "supporting_ticket_ids": ["t1", "t2"],
            "evidence_chips": [
                {"label": "Tickets", "value": "3", "kind": "metric"},
                {"label": "Cohort", "value": "Cigna, Humana", "kind": "cohort"},
                {"label": "Trend", "value": "+20% WoW", "kind": "trend"},
            ],
            "trcs_touched": ["BILL"],
            "cohort": "Cigna, Humana",
            "body_md": "## Detail\n\nDup charges on monthly auto-pay cycle.",
        },
        {
            "finding_id": "f-stub-02",
            "title": "Portal access incidents",
            "summary": "Sustained portal failures since Apr 1.",
            "severity": "medium",
            "confidence": 0.8,
            "supporting_ticket_ids": ["t6", "t7", "t8"],
            "evidence_chips": [
                {"label": "Tickets", "value": "3", "kind": "metric"},
                {"label": "Onset", "value": "2026-04-01", "kind": "metric"},
            ],
            "trcs_touched": ["PORTAL"],
            "body_md": "## Detail\n\nSAML config drift suspected.",
        },
    ],
}


CANNED_REPORT_TEXT: str = (
    "Here is your report:\n\n"
    "```json\n"
    + json.dumps(_CANNED_REPORT, indent=2)
    + "\n```\n"
)


# ──────────────────────────────────────────────────────────────────────
# StubReportBridgeClient
# ──────────────────────────────────────────────────────────────────────

class StubReportBridgeClient:
    """Mimics the GeminiClient/ReportBridgeClient surface used by AIReportPipeline.

    Records every call so tests can assert on prompts, system prompts, and
    timeouts without a real bridge subprocess. Always reports as available.
    """

    def __init__(
        self,
        canned_text: str | None = None,
        *,
        sleep_ms: int = 0,
        fail_first_n: int = 0,
    ) -> None:
        self.model = "stub-model"
        self.pii_redaction = False
        self._canned_text = canned_text or CANNED_REPORT_TEXT
        self._sleep_ms = sleep_ms
        self._fail_first_n = fail_first_n
        self.calls: list[dict] = []
        self._call_counter = 0
        self._last_tool_calls: list[dict] = []

    def is_available(self) -> bool:
        return True

    def generate(self, prompt: str, system_prompt: str = "", timeout: int = 120) -> str:
        self._call_counter += 1
        self.calls.append({
            "prompt": prompt,
            "system_prompt": system_prompt,
            "timeout": timeout,
            "call_index": self._call_counter,
        })
        if self._fail_first_n >= self._call_counter:
            raise RuntimeError("simulated bridge failure")
        if self._sleep_ms:
            time.sleep(self._sleep_ms / 1000.0)
        return self._canned_text

    def shutdown(self) -> None:
        return None


# ──────────────────────────────────────────────────────────────────────
# Pytest fixtures
# ──────────────────────────────────────────────────────────────────────

def is_live_mode() -> bool:
    """Return True when `ALMA_E2E_LIVE=1` is set in the environment."""
    return os.environ.get("ALMA_E2E_LIVE", "").strip() in ("1", "true", "yes")


@pytest.fixture
def stub_bridge_client():
    """Default stub. Tests that need failure modes build their own with kwargs."""
    if is_live_mode():
        from src.agents.report_bridge_client import ReportBridgeClient
        client = ReportBridgeClient()
        try:
            yield client
        finally:
            client.shutdown()
        return
    yield StubReportBridgeClient()


@pytest.fixture
def seeded_warehouse_conn(tmp_path: Path):
    """Tiny SQLite warehouse used by E2E tests — same schema fields the
    grounding harness queries. Yields a sqlite3.Connection."""
    db = tmp_path / "e2e_warehouse.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.executescript(_E2E_SCHEMA)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def e2e_pipeline(stub_bridge_client, seeded_warehouse_conn, tmp_path: Path,
                  monkeypatch):
    """Full AIReportPipeline wired to the stub client + tiny warehouse.

    Stubs `build_data_block` + `format_data_block_for_prompt` so the
    pipeline doesn't depend on the full source-registry / conversations
    schema. The E2E test focuses on parse → grounding → persist; the
    real data-block builder is exercised by tests/test_reporting_foundation.
    """
    from src.data.ai_report_pipeline import AIReportPipeline

    class _DBShim:
        def __init__(self, conn, db_path):
            self.conn = conn
            self.db_path = db_path
        def initialize(self):
            return None
        def close(self):
            return None

    # Stub the report_builder data-assembly path
    import src.data.report_builder as rb
    monkeypatch.setattr(rb, "build_data_block",
                         lambda db, ds, de, **kw: {
                             "ticket_count": 8, "date_range": f"{ds}/{de}",
                             "trcs": ["BILL", "CLAIM", "PORTAL"],
                         })
    monkeypatch.setattr(rb, "format_data_block_for_prompt",
                         lambda block: f"Tickets: {block.get('ticket_count', 0)}")
    monkeypatch.setattr(rb, "replace_prompt_variables",
                         lambda prompt, block: prompt)
    monkeypatch.setattr(rb, "_validate_prompt_before_send", lambda prompt: None)

    db = _DBShim(seeded_warehouse_conn, tmp_path / "e2e_warehouse.db")
    pipeline = AIReportPipeline(db, gemini_client=stub_bridge_client)
    yield pipeline


# ──────────────────────────────────────────────────────────────────────
# Schema
# ──────────────────────────────────────────────────────────────────────

_E2E_SCHEMA = """
CREATE TABLE ticket_index (
  ticket_id TEXT PRIMARY KEY,
  trc_code TEXT, trc_label TEXT,
  insurance_payer TEXT, provider_id TEXT,
  ticket_created_date TEXT, friction_type TEXT,
  subject_sanitized TEXT, issue_snippet TEXT
);
CREATE TABLE analysis_reports (
  report_id INTEGER PRIMARY KEY AUTOINCREMENT,
  page TEXT NOT NULL, run_at TEXT NOT NULL,
  parameters TEXT NOT NULL, summary TEXT NOT NULL,
  full_results TEXT DEFAULT '',
  ticket_count INTEGER DEFAULT 0, duration_ms INTEGER DEFAULT 0,
  notes TEXT DEFAULT '', report_type TEXT DEFAULT 'standard',
  chat_history TEXT DEFAULT '', exported_at TEXT DEFAULT '',
  findings_json TEXT DEFAULT '',
  pipeline_kind TEXT DEFAULT 'single_pass',
  specialist_count INTEGER DEFAULT 0,
  accuracy_score REAL DEFAULT NULL,
  cost_usd REAL DEFAULT 0.0
);
CREATE TABLE prompt_library (
  prompt_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, category TEXT DEFAULT 'custom',
  description TEXT DEFAULT '', prompt_text TEXT NOT NULL,
  system_prompt TEXT DEFAULT '', is_active INTEGER DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
INSERT INTO ticket_index VALUES
  ('t1','BILL','Billing','Cigna','p-1','2026-03-01','incorrect_charge','subj','snip'),
  ('t2','BILL','Billing','Cigna','p-1','2026-03-02','incorrect_charge','subj','snip'),
  ('t3','BILL','Billing','Humana','p-2','2026-03-03','incorrect_charge','subj','snip'),
  ('t4','CLAIM','Claim disputes','BCBS','p-3','2026-03-04','feature_broken','subj','snip'),
  ('t5','CLAIM','Claim disputes','BCBS','p-3','2026-03-05','feature_broken','subj','snip'),
  ('t6','PORTAL','Portal access','Aetna','p-4','2026-04-01','access_blocked','subj','snip'),
  ('t7','PORTAL','Portal access','Aetna','p-4','2026-04-02','access_blocked','subj','snip'),
  ('t8','PORTAL','Portal access','Aetna','p-4','2026-04-03','access_blocked','subj','snip');
"""
