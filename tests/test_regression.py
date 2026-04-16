"""Tests for src/data/canonicalization_regression.py (Phase 6 §6.3)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import numpy as np
import pytest

from src.data.canonicalization_engine import _l2_normalize, _vec_to_blob
from src.data.canonicalization_regression import (
    ClusterSample,
    _extract_json_object,
    _prior_verdict_score,
    call_llm_for_regression,
    run_regression,
    stratified_sample,
)
from src.data.db_manager import DatabaseManager


EMBED_DIM = 1024


@pytest.fixture
def fresh_db(tmp_path: Path):
    db_path = tmp_path / "test.db"
    mgr = DatabaseManager(db_path)
    mgr.initialize()
    conn = mgr.get_connection()
    conn.execute("PRAGMA foreign_keys = OFF")
    yield conn
    try:
        conn.close()
    except Exception:
        pass


class FakeLLM:
    def __init__(self, verdicts_json: str, fail: bool = False):
        self.verdicts_json = verdicts_json
        self.fail = fail
        self.calls = 0
        self.last_prompt = None

    def generate(self, prompt, timeout=240):
        self.calls += 1
        self.last_prompt = prompt
        if self.fail:
            raise RuntimeError("simulated LLM failure")
        return self.verdicts_json


def _make_cluster_row(conn, cluster_id: str, *, tier: str = "active",
                      lifetime: int = 50,
                      discovered: str = "scan0",
                      label: str = "Cluster label") -> np.ndarray:
    rng = np.random.default_rng(hash(cluster_id) & 0xFFFF)
    centroid = _l2_normalize(rng.standard_normal(EMBED_DIM).astype(np.float32))
    conn.execute(
        """INSERT INTO canonical_clusters
             (cluster_id, trc, canonical_label, centroid_blob, tier,
              lifetime_tickets, member_count, discovered_scan_id)
           VALUES (?, 'BILLING', ?, ?, ?, ?, 0, ?)""",
        (cluster_id, label, _vec_to_blob(centroid), tier, lifetime, discovered),
    )
    return centroid


def _attach_members(conn, cluster_id: str, n: int, *, prefix: str = "t") -> list[str]:
    tids = []
    for i in range(n):
        tid = f"{cluster_id}_{prefix}{i}"
        tids.append(tid)
        conn.execute(
            """INSERT INTO ticket_index
                 (ticket_id, trc_code, first_seen_scan_id, last_seen_scan_id,
                  first_seen_date, ticket_created_date, subject_sanitized,
                  canonical_issue_id)
               VALUES (?, 'BILLING', 'scan1', 'scan1', '2026-04-15', '2026-04-15', ?, ?)""",
            (tid, f"{cluster_id} ticket {i}", cluster_id),
        )
    return tids


# ──────────────────────────────────────────────────────────────────────
# Extract JSON helper
# ──────────────────────────────────────────────────────────────────────

class TestExtractJsonObject:
    def test_plain(self):
        assert _extract_json_object('{"a":1}') == {"a": 1}

    def test_fenced(self):
        assert _extract_json_object("```json\n{\"a\":1}\n```") == {"a": 1}

    def test_prose_wrapper(self):
        assert _extract_json_object("prefix {\"a\":1} suffix") == {"a": 1}

    def test_empty_or_invalid(self):
        assert _extract_json_object("") is None
        assert _extract_json_object("not json at all") is None


# ──────────────────────────────────────────────────────────────────────
# Stratified sampling
# ──────────────────────────────────────────────────────────────────────

class TestStratifiedSample:
    def test_empty_db_returns_empty_list(self, fresh_db):
        assert stratified_sample(fresh_db, "scan1") == []

    def test_stable_stratum_picks_by_lifetime(self, fresh_db):
        # 5 active clusters with varying lifetime_tickets; top 3 should be picked
        for i, lifetime in enumerate([100, 80, 60, 40, 20]):
            _make_cluster_row(fresh_db, f"c{i}", tier="active", lifetime=lifetime)
            _attach_members(fresh_db, f"c{i}", 2)
        fresh_db.commit()
        samples = stratified_sample(fresh_db, "scan1",
                                     n_stable=3, n_drifting=0,
                                     n_recently_split=0, n_new=0)
        assert len(samples) == 3
        stable_ids = [s.cluster_id for s in samples]
        # c0 (100) and c1 (80) must be in; c4 (20) must not
        assert "c0" in stable_ids
        assert "c1" in stable_ids
        assert "c4" not in stable_ids

    def test_new_stratum_filters_by_discovered_scan(self, fresh_db):
        _make_cluster_row(fresh_db, "old_cluster", tier="active", discovered="scan1")
        _make_cluster_row(fresh_db, "new_cluster", tier="active", discovered="scan5")
        _attach_members(fresh_db, "old_cluster", 2)
        _attach_members(fresh_db, "new_cluster", 2)
        fresh_db.commit()
        samples = stratified_sample(fresh_db, "scan5",
                                     n_stable=0, n_drifting=0,
                                     n_recently_split=0, n_new=5)
        assert [s.cluster_id for s in samples] == ["new_cluster"]
        assert samples[0].stratum == "new"

    def test_drifting_stratum_reads_drift_events(self, fresh_db):
        _make_cluster_row(fresh_db, "c1", tier="active", lifetime=50)
        _make_cluster_row(fresh_db, "c2", tier="active", lifetime=50)
        _attach_members(fresh_db, "c1", 3)
        _attach_members(fresh_db, "c2", 3)
        # Write 2 drift events of different magnitudes
        for cid, drift_cos in [("c1", 0.05), ("c2", 0.35)]:
            fresh_db.execute(
                """INSERT INTO cluster_drift_events
                     (event_id, cluster_id, scan_id, drift_cosine, member_count_before)
                   VALUES (?, ?, 'scan2', ?, 10)""",
                (uuid.uuid4().hex, cid, drift_cos),
            )
        fresh_db.commit()
        samples = stratified_sample(fresh_db, "scan2",
                                     n_stable=0, n_drifting=2,
                                     n_recently_split=0, n_new=0)
        drift_ids = [s.cluster_id for s in samples]
        assert "c2" in drift_ids  # higher drift picked first
        # Drift cosine is attached to the sample
        c2_sample = [s for s in samples if s.cluster_id == "c2"][0]
        assert c2_sample.drift_cosine is not None
        assert abs(c2_sample.drift_cosine - 0.35) < 1e-6
        assert c2_sample.member_count_then == 10

    def test_dedup_across_strata(self, fresh_db):
        # A new cluster that also happens to be top-by-lifetime shouldn't double-count
        _make_cluster_row(fresh_db, "hot_new", tier="active",
                          lifetime=1000, discovered="scan1")
        _attach_members(fresh_db, "hot_new", 5)
        fresh_db.commit()
        samples = stratified_sample(fresh_db, "scan1",
                                     n_stable=3, n_drifting=0,
                                     n_recently_split=0, n_new=3)
        ids = [s.cluster_id for s in samples]
        assert ids.count("hot_new") == 1


# ──────────────────────────────────────────────────────────────────────
# call_llm_for_regression
# ──────────────────────────────────────────────────────────────────────

class TestCallLlmForRegression:
    def test_no_client_returns_empty(self):
        samples = [ClusterSample("c1", "stable", "test", 10, 10, 0.0)]
        verdicts, raw = call_llm_for_regression(samples, None)
        assert verdicts == {}
        assert raw == ""

    def test_no_samples_returns_empty(self):
        verdicts, raw = call_llm_for_regression([], FakeLLM(""))
        assert verdicts == {}
        assert raw == ""

    def test_valid_response_parses_verdicts(self):
        sample = ClusterSample("c1", "stable", "Test", 10, 10, 0.02)
        llm_out = json.dumps({"verdicts": [
            {"cluster_id": "c1", "verdict_score": 4.5,
             "diagnosis": "healthy", "recommended_action": "none"}
        ]})
        verdicts, raw = call_llm_for_regression([sample], FakeLLM(llm_out))
        assert "c1" in verdicts
        assert verdicts["c1"]["verdict_score"] == 4.5
        assert verdicts["c1"]["recommended_action"] == "none"
        assert raw == llm_out

    def test_score_clamped_to_range(self):
        sample = ClusterSample("c1", "stable", "Test", 10, 10, 0.0)
        llm_out = json.dumps({"verdicts": [
            {"cluster_id": "c1", "verdict_score": 9.9,
             "diagnosis": "x", "recommended_action": "none"},
        ]})
        verdicts, _ = call_llm_for_regression([sample], FakeLLM(llm_out))
        assert verdicts["c1"]["verdict_score"] == 5.0

    def test_invalid_action_falls_back_to_none(self):
        sample = ClusterSample("c1", "stable", "Test", 10, 10, 0.0)
        llm_out = json.dumps({"verdicts": [
            {"cluster_id": "c1", "verdict_score": 3.0,
             "diagnosis": "x", "recommended_action": "bogus"},
        ]})
        verdicts, _ = call_llm_for_regression([sample], FakeLLM(llm_out))
        assert verdicts["c1"]["recommended_action"] == "none"

    def test_unparseable_json_returns_empty(self):
        sample = ClusterSample("c1", "stable", "Test", 10, 10, 0.0)
        verdicts, raw = call_llm_for_regression([sample], FakeLLM("garbage, not JSON"))
        assert verdicts == {}
        assert "garbage" in raw

    def test_llm_failure_is_graceful(self):
        sample = ClusterSample("c1", "stable", "Test", 10, 10, 0.0)
        verdicts, raw = call_llm_for_regression([sample], FakeLLM("", fail=True))
        assert verdicts == {}
        assert raw == ""


# ──────────────────────────────────────────────────────────────────────
# run_regression end-to-end
# ──────────────────────────────────────────────────────────────────────

class TestRunRegression:
    def test_no_llm_client_samples_but_writes_nothing(self, fresh_db):
        _make_cluster_row(fresh_db, "c1", tier="active", lifetime=100)
        _attach_members(fresh_db, "c1", 3)
        fresh_db.commit()
        result = run_regression(fresh_db, "scan1", llm_client=None,
                                 n_stable=5, n_drifting=0,
                                 n_recently_split=0, n_new=0)
        assert result.sampled_count == 1
        assert result.verdict_count == 0
        assert result.confirmed_degradation_count == 0
        rows = fresh_db.execute(
            "SELECT COUNT(*) FROM cluster_regression_reports"
        ).fetchone()[0]
        assert rows == 0

    def test_with_llm_writes_reports(self, fresh_db):
        _make_cluster_row(fresh_db, "c1", tier="active", lifetime=100)
        _attach_members(fresh_db, "c1", 3)
        fresh_db.commit()
        llm_out = json.dumps({"verdicts": [
            {"cluster_id": "c1", "verdict_score": 4.5,
             "diagnosis": "healthy", "recommended_action": "none"},
        ]})
        result = run_regression(fresh_db, "scan1", llm_client=FakeLLM(llm_out),
                                 n_stable=5, n_drifting=0,
                                 n_recently_split=0, n_new=0)
        assert result.verdict_count == 1
        row = fresh_db.execute(
            """SELECT cluster_id, verdict_score, stratum, confirmed_degradation
                 FROM cluster_regression_reports"""
        ).fetchone()
        cid, score, stratum, confirmed = row
        assert cid == "c1"
        assert abs(score - 4.5) < 1e-6
        assert stratum == "stable"
        assert confirmed == 0

    def test_two_scan_confirmation_rule(self, fresh_db):
        _make_cluster_row(fresh_db, "c1", tier="active", lifetime=100)
        _attach_members(fresh_db, "c1", 3)
        fresh_db.commit()
        # Scan 1: verdict 2.0 (low but unconfirmed)
        llm1 = FakeLLM(json.dumps({"verdicts": [
            {"cluster_id": "c1", "verdict_score": 2.0,
             "diagnosis": "weak", "recommended_action": "rename"},
        ]}))
        r1 = run_regression(fresh_db, "scan1", llm_client=llm1,
                             n_stable=5, n_drifting=0,
                             n_recently_split=0, n_new=0)
        assert r1.confirmed_degradation_count == 0

        # Scan 2: also low → confirmed
        llm2 = FakeLLM(json.dumps({"verdicts": [
            {"cluster_id": "c1", "verdict_score": 2.5,
             "diagnosis": "still weak", "recommended_action": "rename"},
        ]}))
        r2 = run_regression(fresh_db, "scan2", llm_client=llm2,
                             n_stable=5, n_drifting=0,
                             n_recently_split=0, n_new=0)
        assert r2.confirmed_degradation_count == 1
        # The scan-2 row has confirmed_degradation=1 and prev_verdict_score≈2.0
        rows = fresh_db.execute(
            """SELECT scan_id, verdict_score, prev_verdict_score, confirmed_degradation
                 FROM cluster_regression_reports WHERE cluster_id = 'c1'
                 ORDER BY created_at"""
        ).fetchall()
        assert len(rows) == 2
        # scan1 row: no prev, not confirmed
        assert rows[0][0] == "scan1"
        assert rows[0][2] is None
        assert rows[0][3] == 0
        # scan2 row: prev = 2.0, confirmed
        assert rows[1][0] == "scan2"
        assert abs(rows[1][2] - 2.0) < 1e-6
        assert rows[1][3] == 1

    def test_high_then_low_does_not_confirm(self, fresh_db):
        _make_cluster_row(fresh_db, "c1", tier="active", lifetime=100)
        _attach_members(fresh_db, "c1", 3)
        fresh_db.commit()
        # First verdict: 4.5 (healthy) — not confirmed
        llm1 = FakeLLM(json.dumps({"verdicts": [
            {"cluster_id": "c1", "verdict_score": 4.5,
             "diagnosis": "ok", "recommended_action": "none"},
        ]}))
        run_regression(fresh_db, "scan1", llm_client=llm1,
                        n_stable=5, n_drifting=0, n_recently_split=0, n_new=0)
        # Second verdict: 2.0 (low) — no confirmation because prev was 4.5
        llm2 = FakeLLM(json.dumps({"verdicts": [
            {"cluster_id": "c1", "verdict_score": 2.0,
             "diagnosis": "regressed", "recommended_action": "rename"},
        ]}))
        r2 = run_regression(fresh_db, "scan2", llm_client=llm2,
                             n_stable=5, n_drifting=0, n_recently_split=0, n_new=0)
        assert r2.confirmed_degradation_count == 0


class TestSnippetsThenRoundTrip:
    """S10.4: snippets_then hydration from cluster_member_snapshots."""

    def test_empty_without_snapshot(self, fresh_db):
        from src.data.canonicalization_regression import _build_sample_row
        _make_cluster_row(fresh_db, "c1", tier="active", lifetime=10)
        _attach_members(fresh_db, "c1", 3)
        fresh_db.commit()
        sample = _build_sample_row(
            fresh_db, cluster_id="c1", stratum="stable", current_scan_id="scan1",
        )
        assert sample is not None
        assert sample.snippets_then == []

    def test_populated_from_prior_scan(self, fresh_db):
        from src.data.canonicalization_engine import _write_member_snapshots
        from src.data.canonicalization_regression import _build_sample_row
        _make_cluster_row(fresh_db, "c1", tier="active", lifetime=10)
        _attach_members(fresh_db, "c1", 5)
        fresh_db.commit()
        # Scan 1: write snapshot
        _write_member_snapshots(fresh_db, scan_id="scan1")
        fresh_db.commit()
        # Scan 2: build sample — snippets_then should hydrate from scan1
        sample = _build_sample_row(
            fresh_db, cluster_id="c1", stratum="stable", current_scan_id="scan2",
        )
        assert sample is not None
        assert len(sample.snippets_then) > 0
        # Subjects match _attach_members output pattern
        for s in sample.snippets_then:
            assert "c1 ticket" in s

    def test_current_scan_excluded_from_prior(self, fresh_db):
        from src.data.canonicalization_engine import _write_member_snapshots
        from src.data.canonicalization_regression import _build_sample_row
        _make_cluster_row(fresh_db, "c1", tier="active", lifetime=10)
        _attach_members(fresh_db, "c1", 5)
        fresh_db.commit()
        _write_member_snapshots(fresh_db, scan_id="scan1")
        fresh_db.commit()
        # Same scan → no prior snapshot available
        sample = _build_sample_row(
            fresh_db, cluster_id="c1", stratum="stable", current_scan_id="scan1",
        )
        assert sample is not None
        assert sample.snippets_then == []


class TestPriorVerdictScore:
    def test_none_when_no_rows(self, fresh_db):
        assert _prior_verdict_score(fresh_db, "nonexistent") is None

    def test_returns_most_recent(self, fresh_db):
        # Write two rows with explicit timestamps so ORDER BY created_at is deterministic
        import time
        for score, ts in [(4.0, "2026-04-15 10:00:00"), (2.5, "2026-04-15 12:00:00")]:
            fresh_db.execute(
                """INSERT INTO cluster_regression_reports
                     (report_id, scan_id, cluster_id, verdict_score, created_at)
                   VALUES (?, 'scan1', 'c1', ?, ?)""",
                (uuid.uuid4().hex, score, ts),
            )
        fresh_db.commit()
        assert _prior_verdict_score(fresh_db, "c1") == 2.5
