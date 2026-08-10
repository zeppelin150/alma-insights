"""Tests for migration 052 + the solver_ledger store API.

Schema presence rides the empty_db fixture (initialize() runs SchemaMigrator
over the real migrations/ dir); the idempotency test applies 052 alone in a
tmp migrations dir, exactly like TestMigration051's pre051 fixture.
"""

import shutil
import sqlite3
from pathlib import Path

import pytest

from src.data import solver_ledger as sl

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"

_TABLES = (
    "solver_calls",
    "solver_tool_execs",
    "solver_stage_results",
    "solver_result_calls",
    "solver_drafts",
    "solver_citations",
    "solver_checks",
)


# ── migration application ────────────────────────────────────────────

class TestMigration052:
    def test_tables_present_on_empty_db(self, empty_db):
        conn = empty_db.conn
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for table in _TABLES:
            assert table in tables, f"{table} missing"

    def test_key_columns_present(self, empty_db):
        conn = empty_db.conn
        expect = {
            "solver_calls": ("call_id", "job_id", "stage", "attempt", "purpose",
                             "bridge_instance_id", "bedrock_active", "prompt_sha",
                             "response_sha", "cost_usd", "error_class",
                             "salvage_applied", "status", "started_at",
                             "finished_at"),
            "solver_tool_execs": ("exec_id", "job_id", "call_id", "tool_name",
                                  "args_digest", "result_status"),
            "solver_stage_results": ("result_id", "job_id", "stage", "attempt",
                                     "status", "payload_json"),
            "solver_drafts": ("draft_id", "job_id", "kind", "target_kind",
                              "target_ref", "content_md", "rationale",
                              "status", "executed_at"),
            "solver_citations": ("citation_id", "draft_id", "source_kind",
                                 "source_ref", "quote", "overlap_score"),
            "solver_checks": ("check_id", "job_id", "stage", "name", "kind",
                              "verdict", "reason", "consumer_action"),
        }
        for table, cols in expect.items():
            have = {r[1] for r in conn.execute(
                f"PRAGMA table_info({table})").fetchall()}
            for col in cols:
                assert col in have, f"{table}.{col} missing"

    def test_reapply_is_idempotent_and_preserves_data(self, tmp_path):
        from src.data.connection_factory import get_connection
        from src.updater.schema_migrator import SchemaMigrator
        mig_dir = tmp_path / "migs"
        mig_dir.mkdir()
        shutil.copy(MIGRATIONS / "052_solver_ledger.sql",
                    mig_dir / "052_solver_ledger.sql")
        conn = get_connection(tmp_path / "m.db")
        try:
            SchemaMigrator(mig_dir).migrate(conn)
            call_id = sl.begin_call(conn, job_id="j1", stage="frame")
            conn.execute("DELETE FROM schema_migrations")
            conn.commit()
            SchemaMigrator(mig_dir).migrate(conn)
            row = conn.execute(
                "SELECT status FROM solver_calls WHERE call_id = ?",
                (call_id,)).fetchone()
            assert row is not None, "call row lost on re-apply"
            assert row["status"] == "spawned"
        finally:
            conn.close()


# ── calls: pre-spawn protocol + single-winner finish ─────────────────

def test_begin_finish_roundtrip(empty_db):
    conn = empty_db.conn
    call_id = sl.begin_call(
        conn, job_id="j1", stage="research", attempt=1,
        purpose="worker", model="sonnet", bedrock_active=True,
        prompt_text="find the payer bulletin",
    )
    row = conn.execute(
        "SELECT * FROM solver_calls WHERE call_id = ?", (call_id,)).fetchone()
    assert row["status"] == "spawned", "row must exist before the subprocess runs"
    assert row["prompt_sha"] is not None
    assert row["bedrock_active"] == 1
    assert row["started_at"] is not None
    assert row["finished_at"] is None

    ok = sl.finish_call(
        conn, call_id, status="done", response_text="found it",
        tokens_in=100, tokens_out=50, cost_usd=0.12, duration_ms=900,
        stop_reason="end_turn",
    )
    assert ok is True
    row = conn.execute(
        "SELECT * FROM solver_calls WHERE call_id = ?", (call_id,)).fetchone()
    assert row["status"] == "done"
    assert row["response_sha"] is not None
    assert row["finished_at"] is not None


def test_finish_is_single_winner(empty_db):
    conn = empty_db.conn
    call_id = sl.begin_call(conn, job_id="j1", stage="frame")
    assert sl.finish_call(conn, call_id, status="done") is True
    assert sl.finish_call(conn, call_id, status="error") is False, \
        "a finished call must not be finishable again"


def test_call_vocab_validation(empty_db):
    conn = empty_db.conn
    with pytest.raises(ValueError):
        sl.begin_call(conn, job_id="j1", stage="nope")
    with pytest.raises(ValueError):
        sl.begin_call(conn, job_id="j1", stage="frame", purpose="autonomous")
    call_id = sl.begin_call(conn, job_id="j1", stage="frame")
    with pytest.raises(ValueError):
        sl.finish_call(conn, call_id, status="spawned")
    with pytest.raises(ValueError):
        sl.finish_call(conn, call_id, status="error", error_class="mystery")


def test_mark_interrupted_reaper(empty_db):
    conn = empty_db.conn
    a = sl.begin_call(conn, job_id="j1", stage="frame")
    b = sl.begin_call(conn, job_id="j2", stage="frame")
    done = sl.begin_call(conn, job_id="j1", stage="research")
    sl.finish_call(conn, done, status="done")
    assert sl.mark_interrupted_calls(conn, "j1") == 1
    row = conn.execute(
        "SELECT status FROM solver_calls WHERE call_id = ?", (a,)).fetchone()
    assert row["status"] == "interrupted"
    assert sl.mark_interrupted_calls(conn) == 1
    row = conn.execute(
        "SELECT status FROM solver_calls WHERE call_id = ?", (b,)).fetchone()
    assert row["status"] == "interrupted"


# ── derived reads: attempts + spend ──────────────────────────────────

def test_attempts_is_a_query(empty_db):
    conn = empty_db.conn
    assert sl.attempts(conn, "j1", "draft") == 0
    sl.begin_call(conn, job_id="j1", stage="draft", attempt=1)
    sl.begin_call(conn, job_id="j1", stage="draft", attempt=2, purpose="repair")
    assert sl.attempts(conn, "j1", "draft") == 2
    assert sl.attempts(conn, "j1", "frame") == 0


def test_spend_sums_ledger(empty_db):
    conn = empty_db.conn
    a = sl.begin_call(conn, job_id="j1", stage="frame")
    sl.finish_call(conn, a, status="done", cost_usd=0.10)
    b = sl.begin_call(conn, job_id="j1", stage="draft")
    sl.finish_call(conn, b, status="error", cost_usd=0.25, error_class="transport")
    c = sl.begin_call(conn, job_id="j2", stage="frame")
    sl.finish_call(conn, c, status="done", cost_usd=1.00)
    assert sl.spend(conn, "j1") == pytest.approx(0.35), \
        "failed calls must still count toward spend"
    assert sl.spend(conn) == pytest.approx(1.35)


# ── tool execs + watermark ───────────────────────────────────────────

def test_watermark_confirmed_empty(empty_db):
    conn = empty_db.conn
    mark = sl.snapshot_watermark(conn, "j1")
    out = sl.resolve_watermark(conn, "j1", mark)
    assert out["status"] == "confirmed_empty"


def test_watermark_confirmed_ran(empty_db):
    conn = empty_db.conn
    sl.record_tool_exec(conn, job_id="j1", tool_name="kb_search")
    mark = sl.snapshot_watermark(conn, "j1")
    sl.record_tool_exec(conn, job_id="j1", tool_name="search_google_drive")
    sl.record_tool_exec(conn, job_id="j1", tool_name="zd_search")
    out = sl.resolve_watermark(conn, "j1", mark)
    assert out["status"] == "confirmed_ran"
    assert [r["tool_name"] for r in out["rows"]] == \
        ["search_google_drive", "zd_search"]


def test_watermark_shrunk_ledger_is_unknown(empty_db):
    conn = empty_db.conn
    sl.record_tool_exec(conn, job_id="j1", tool_name="kb_search")
    sl.record_tool_exec(conn, job_id="j1", tool_name="zd_search")
    mark = sl.snapshot_watermark(conn, "j1")
    conn.execute("DELETE FROM solver_tool_execs WHERE tool_name = 'kb_search'")
    conn.commit()
    out = sl.resolve_watermark(conn, "j1", mark)
    assert out["status"] == "unknown", "deletion must degrade, never accuse"


def test_watermark_missing_anchor_is_unknown(empty_db):
    conn = empty_db.conn
    sl.record_tool_exec(conn, job_id="j1", tool_name="kb_search")
    anchor = sl.record_tool_exec(conn, job_id="j1", tool_name="zd_search")
    mark = sl.snapshot_watermark(conn, "j1")
    conn.execute("DELETE FROM solver_tool_execs WHERE exec_id = ?", (anchor,))
    conn.commit()
    sl.record_tool_exec(conn, job_id="j1", tool_name="guru_search")
    out = sl.resolve_watermark(conn, "j1", mark)
    assert out["status"] == "unknown", "anchor identity mismatch must degrade"


def test_watermark_scoped_per_job(empty_db):
    conn = empty_db.conn
    mark = sl.snapshot_watermark(conn, "j1")
    sl.record_tool_exec(conn, job_id="OTHER", tool_name="kb_search")
    out = sl.resolve_watermark(conn, "j1", mark)
    assert out["status"] == "confirmed_empty", \
        "another job's tools must not leak into this job's watermark"


# ── stage results + provenance ───────────────────────────────────────

def test_stage_result_provenance(empty_db):
    conn = empty_db.conn
    a = sl.begin_call(conn, job_id="j1", stage="research")
    b = sl.begin_call(conn, job_id="j1", stage="research", purpose="verifier")
    rid = sl.record_stage_result(
        conn, job_id="j1", stage="research", status="pass",
        payload={"hits": 6}, call_ids=[a, b],
    )
    links = conn.execute(
        "SELECT call_id FROM solver_result_calls WHERE result_id = ? ORDER BY rowid",
        (rid,)).fetchall()
    assert [r["call_id"] for r in links] == [a, b]
    assert sl.has_passing_result(conn, "j1", "research") is True
    assert sl.has_passing_result(conn, "j1", "draft") is False


def test_stage_result_parked_is_not_passing(empty_db):
    conn = empty_db.conn
    sl.record_stage_result(conn, job_id="j1", stage="sufficiency", status="parked")
    assert sl.has_passing_result(conn, "j1", "sufficiency") is False


def test_stage_result_attempt_unique(empty_db):
    conn = empty_db.conn
    sl.record_stage_result(conn, job_id="j1", stage="frame", attempt=1)
    with pytest.raises(sqlite3.IntegrityError):
        sl.record_stage_result(conn, job_id="j1", stage="frame", attempt=1)
    sl.record_stage_result(conn, job_id="j1", stage="frame", attempt=2)


# ── checks: consumer_action is mandatory ─────────────────────────────

def test_check_requires_consumer_action(empty_db):
    conn = empty_db.conn
    with pytest.raises(ValueError):
        sl.record_check(conn, job_id="j1", stage="draft", name="pii_scan",
                        verdict="fail", consumer_action="ignored")
    with pytest.raises(ValueError):
        sl.record_check(conn, job_id="j1", stage="draft", name="pii_scan",
                        verdict="maybe", consumer_action="parked")
    cid = sl.record_check(
        conn, job_id="j1", stage="draft", name="corpus_containment",
        verdict="fail", consumer_action="repaired", kind="deterministic",
        reason="B:guru:ungrounded_atoms=1",
    )
    rows = sl.list_checks(conn, "j1", "draft")
    assert [r["check_id"] for r in rows] == [cid]
    assert rows[0]["consumer_action"] == "repaired"


# ── drafts: mandatory rationale + transition matrix ──────────────────

def _draft(conn, **kw):
    base = dict(job_id="j1", kind="edit", title="Refresh SOP",
                content_md="# body", rationale="bulletin 2026-14 changed the window")
    base.update(kw)
    return sl.create_draft(conn, **base)


def test_draft_mandatory_fields(empty_db):
    conn = empty_db.conn
    with pytest.raises(ValueError):
        _draft(conn, rationale="   ")
    with pytest.raises(ValueError):
        _draft(conn, title="")
    with pytest.raises(ValueError):
        _draft(conn, kind="mutate")
    did = _draft(conn)
    assert sl.get_draft(conn, did)["status"] == "pending"


def test_draft_transition_matrix(empty_db):
    conn = empty_db.conn
    did = _draft(conn)
    assert sl.set_draft_status(conn, did, "executed") == "invalid_transition", \
        "pending must pass through ready before executing"
    assert sl.set_draft_status(conn, did, "ready") == "ok"
    assert sl.set_draft_status(conn, did, "pending") == "ok"
    assert sl.set_draft_status(conn, did, "ready") == "ok"
    assert sl.set_draft_status(conn, did, "executed") == "ok"
    assert sl.get_draft(conn, did)["executed_at"] is not None
    for target in ("pending", "ready", "rejected"):
        assert sl.set_draft_status(conn, did, target) == "invalid_transition", \
            f"executed draft mutated to {target}"


def test_draft_compare_and_set(empty_db):
    conn = empty_db.conn
    did = _draft(conn)
    assert sl.set_draft_status(conn, did, "ready", expected="ready") == "status_changed"
    assert sl.set_draft_status(conn, did, "ready", expected="pending") == "ok"
    assert sl.set_draft_status(conn, "nope", "ready") == "not_found"


def test_citations_cascade_with_draft(empty_db):
    conn = empty_db.conn
    did = _draft(conn)
    with pytest.raises(ValueError):
        sl.add_citation(conn, draft_id=did, source_kind="fax", source_ref="x")
    with pytest.raises(ValueError):
        sl.add_citation(conn, draft_id=did, source_kind="drive", source_ref=" ")
    sl.add_citation(conn, draft_id=did, source_kind="drive",
                    source_ref="doc-123", quote="within 45 days",
                    overlap_score=0.92)
    assert len(sl.list_citations(conn, did)) == 1
    conn.execute("DELETE FROM solver_drafts WHERE draft_id = ?", (did,))
    conn.commit()
    assert sl.list_citations(conn, did) == [], \
        "citations must cascade when their draft is deleted"


def test_list_drafts_filters(empty_db):
    conn = empty_db.conn
    a = _draft(conn)
    b = _draft(conn, job_id="j2", kind="removal")
    sl.set_draft_status(conn, a, "ready")
    assert [d["draft_id"] for d in sl.list_drafts(conn, job_id="j1")] == [a]
    assert [d["draft_id"] for d in sl.list_drafts(conn, status="pending")] == [b]
    assert len(sl.list_drafts(conn)) == 2
