"""Unit tests for post_report_persist."""

import sqlite3
import pytest
from pathlib import Path

_MIGRATION_SQL = (
    Path(__file__).resolve().parent.parent.parent / "migrations" / "005_persistence_layer.sql"
).read_text(encoding="utf-8")


@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    db.executescript(_MIGRATION_SQL)
    yield db
    db.close()


def test_persist_report_run(conn):
    from src.services.post_report_persist import persist_report_run

    run_id = persist_report_run(
        {
            "prompt_template": "voc_root_cause",
            "output_text": "# Report\nFindings here...",
            "trc_filter": "BIL-01",
            "date_start": "2025-01-01",
            "date_end": "2025-03-01",
            "ticket_count": 150,
            "model_used": "gemini-2.5-flash",
            "cost_usd": 0.12,
            "duration_sec": 45.3,
            "source": "manual",
        },
        conn,
    )

    assert run_id is not None
    row = conn.execute(
        "SELECT prompt_template, ticket_count, cost_usd FROM analysis_runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    assert row[0] == "voc_root_cause"
    assert row[1] == 150
    assert row[2] == 0.12


def test_persist_with_minimal_data(conn):
    from src.services.post_report_persist import persist_report_run

    run_id = persist_report_run(
        {"prompt_template": "quick", "output_text": "OK"},
        conn,
    )
    row = conn.execute("SELECT source FROM analysis_runs WHERE run_id = ?", (run_id,)).fetchone()
    assert row[0] == "manual"  # default
