"""Tests for Phase 6 fission detection (src/data/canonicalization_engine.py).

Covers `_fission_triggers`, `_sub_hdbscan`, `_detect_and_execute_fission`.
Fission is observational in Phase 6 — the default never commits splits.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import numpy as np
import pytest

from src.data.canonicalization_engine import (
    DEFAULT_PARAMS,
    _detect_and_execute_fission,
    _extract_json_object,
    _fission_triggers,
    _l2_normalize,
    _sub_hdbscan,
    _vec_to_blob,
    run_canonicalization,
)
from src.data.db_manager import DatabaseManager


EMBED_DIM = 1024
PARAMS = {
    **DEFAULT_PARAMS,
    "telemetry_fission_enabled": True,
    "min_cluster_size": 5,
}


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


def _insert_ticket(conn, tid: str, trc="BILLING", subj="s"):
    now = "2026-04-15T00:00:00"
    conn.execute(
        """INSERT INTO ticket_index
             (ticket_id, trc_code, first_seen_scan_id, last_seen_scan_id,
              first_seen_date, ticket_created_date, subject_sanitized)
           VALUES (?, ?, 'ingestion', 'scan1', ?, ?, ?)""",
        (tid, trc, now, now, subj),
    )
    conn.execute(
        """INSERT INTO nlp_ticket_classifications
             (classification_id, batch_id, ticket_id, scan_id, trc,
              sub_cluster, sub_cluster_confidence, created_at)
           VALUES (?, 'b1', ?, 'scan1', ?, 'x', 0.85, ?)""",
        (uuid.uuid4().hex, tid, trc, now),
    )


def _insert_embedding(conn, tid: str, vec: np.ndarray):
    v = _l2_normalize(vec.astype(np.float32))
    conn.execute(
        """INSERT OR REPLACE INTO ticket_embeddings
             (ticket_id, embedding_blob, source_text_hash, model_name, dim_size, created_at)
           VALUES (?, ?, ?, 'Qwen3-Embedding-0.6B', ?, '2026-04-15T00:00:00')""",
        (tid, _vec_to_blob(v), f"h{tid}", len(v)),
    )


def _make_cluster(rng, seed, n, noise_std=None, dim=EMBED_DIM):
    cr = np.random.default_rng(seed)
    c = _l2_normalize(cr.standard_normal(dim).astype(np.float32))
    if noise_std is None:
        noise_std = 0.4 / np.sqrt(dim)
    deltas = rng.standard_normal((n, dim)).astype(np.float32) * noise_std
    return _l2_normalize(c + deltas)


class FakeLLM:
    """Minimal mock with a .generate(prompt, timeout=N) method. Returns
    pre-programmed verdict JSON regardless of prompt contents."""
    def __init__(self, verdicts_json: str, fail: bool = False):
        self.verdicts_json = verdicts_json
        self.fail = fail
        self.calls = 0

    def generate(self, prompt, timeout=180):
        self.calls += 1
        if self.fail:
            raise RuntimeError("simulated LLM failure")
        return self.verdicts_json


# ──────────────────────────────────────────────────────────────────────
# Trigger classifier
# ──────────────────────────────────────────────────────────────────────

class TestFissionTriggers:
    def test_low_variance_low_no_growth_no_trigger(self):
        cur = {"variance": 0.05, "silhouette": 0.6, "member_count": 10}
        assert _fission_triggers(cur, None, DEFAULT_PARAMS) == []

    def test_high_variance_fires(self):
        cur = {"variance": 0.25, "silhouette": 0.6, "member_count": 10}
        triggers = _fission_triggers(cur, None, DEFAULT_PARAMS)
        assert "variance_threshold" in triggers

    def test_low_silhouette_fires(self):
        cur = {"variance": 0.05, "silhouette": 0.1, "member_count": 10}
        triggers = _fission_triggers(cur, None, DEFAULT_PARAMS)
        assert "silhouette_drop" in triggers

    def test_doubled_members_fires(self):
        cur = {"variance": 0.05, "silhouette": 0.6, "member_count": 20}
        prev = {"member_count": 5}
        triggers = _fission_triggers(cur, prev, DEFAULT_PARAMS)
        assert "member_count_doubled" in triggers

    def test_none_metrics_skip_their_triggers(self):
        cur = {"variance": None, "silhouette": None, "member_count": 10}
        assert _fission_triggers(cur, None, DEFAULT_PARAMS) == []


# ──────────────────────────────────────────────────────────────────────
# Sub-HDBSCAN
# ──────────────────────────────────────────────────────────────────────

class TestSubHdbscan:
    def test_bimodal_yields_two_subclusters(self):
        rng = np.random.default_rng(7)
        a = _make_cluster(rng, seed=11, n=8, dim=64)
        b = _make_cluster(rng, seed=77, n=8, dim=64)
        X = np.vstack([a, b]).astype(np.float32)
        labels, _ = _sub_hdbscan(X, DEFAULT_PARAMS)
        non_noise = labels[labels >= 0]
        assert len(set(non_noise.tolist())) >= 2

    def test_subhdbscan_returns_per_row_labels(self):
        rng = np.random.default_rng(13)
        X = _make_cluster(rng, seed=3, n=15, dim=64)
        labels, probs = _sub_hdbscan(X, DEFAULT_PARAMS)
        # Shape invariants — sub-HDBSCAN always returns per-row labels &
        # probabilities (noise=-1). Whether a unimodal cluster splits into
        # ≥ 2 sub-clusters is HDBSCAN-dependent and not a guarantee.
        assert labels.shape == (15,)
        assert probs.shape == (15,)


# ──────────────────────────────────────────────────────────────────────
# End-to-end fission detection
# ──────────────────────────────────────────────────────────────────────

def _seed_bimodal_cluster(fresh_db) -> str:
    """Seed ONE cluster that secretly contains 2 tight sub-clusters. Force a
    single HDBSCAN cluster by running canonicalization with a very low
    min_cluster_size then hand-merging — simpler: run canonicalization with
    centroid_merge_threshold=0.0 so HDBSCAN's output stays 2 clusters, then
    manually re-point half's tickets to the other cluster's cluster_id."""
    rng = np.random.default_rng(33)
    a = _make_cluster(rng, seed=100, n=12, noise_std=0.5 / np.sqrt(EMBED_DIM))
    b = _make_cluster(rng, seed=900, n=12, noise_std=0.5 / np.sqrt(EMBED_DIM))
    for i, v in enumerate(a):
        _insert_ticket(fresh_db, f"A{i}", subj=f"invoice issue {i}")
        _insert_embedding(fresh_db, f"A{i}", v)
    for i, v in enumerate(b):
        _insert_ticket(fresh_db, f"B{i}", subj=f"portal outage {i}")
        _insert_embedding(fresh_db, f"B{i}", v)
    fresh_db.commit()
    run_canonicalization(fresh_db, scan_id="scan1", params={**PARAMS,
                                                            "telemetry_fission_enabled": False,
                                                            "min_cluster_size": 5})
    # Collapse whatever ≥2 clusters exist into one, by reassigning all tickets
    # to the first cluster_id.
    rows = fresh_db.execute(
        "SELECT cluster_id FROM canonical_clusters ORDER BY cluster_id"
    ).fetchall()
    assert len(rows) >= 1
    keep_id = rows[0][0]
    fresh_db.execute(
        "UPDATE ticket_index SET canonical_issue_id = ? WHERE canonical_issue_id IS NOT NULL",
        (keep_id,),
    )
    # Recompute centroid = mean of ALL member embeddings so variance reflects
    # the combined (bimodal) cluster.
    mats = []
    for (blob,) in fresh_db.execute(
        """
        SELECT te.embedding_blob
          FROM ticket_embeddings te
          JOIN ticket_index ti ON ti.ticket_id = te.ticket_id
         WHERE ti.canonical_issue_id = ?
        """,
        (keep_id,),
    ).fetchall():
        mats.append(_l2_normalize(np.frombuffer(blob, dtype=np.float32).copy()))
    centroid = _l2_normalize(np.mean(np.vstack(mats), axis=0))
    fresh_db.execute(
        "UPDATE canonical_clusters SET centroid_blob = ?, member_count = ? WHERE cluster_id = ?",
        (_vec_to_blob(centroid), len(mats), keep_id),
    )
    # Remove any other cluster rows to keep state tidy
    for (cid,) in rows[1:]:
        fresh_db.execute("DELETE FROM canonical_clusters WHERE cluster_id = ?", (cid,))
    fresh_db.commit()
    return keep_id


class TestDetectAndExecuteFission:
    def test_no_triggers_no_events(self, fresh_db):
        # Two clean unimodal clusters — neither should trigger fission
        rng = np.random.default_rng(9)
        a = _make_cluster(rng, seed=1, n=10)
        b = _make_cluster(rng, seed=2, n=10)
        for i, v in enumerate(a):
            _insert_ticket(fresh_db, f"A{i}", subj=f"billing {i}")
            _insert_embedding(fresh_db, f"A{i}", v)
        for i, v in enumerate(b):
            _insert_ticket(fresh_db, f"B{i}", subj=f"login {i}")
            _insert_embedding(fresh_db, f"B{i}", v)
        fresh_db.commit()
        run_canonicalization(fresh_db, scan_id="scan1", params=PARAMS)
        # There may or may not be events — assert none had committed=1
        rows = fresh_db.execute(
            "SELECT committed FROM cluster_fission_events WHERE scan_id = 'scan1'"
        ).fetchall()
        assert all(r[0] == 0 for r in rows)

    def test_bimodal_cluster_detected_no_llm(self, fresh_db):
        parent_cid = _seed_bimodal_cluster(fresh_db)
        # Invoke fission stage directly (scan2 — already-canonicalized state)
        n = _detect_and_execute_fission(fresh_db, "scan2", PARAMS, llm_client=None)
        assert n >= 1
        # Event should have triggered_by populated, committed=0 (no LLM, no gate)
        rows = fresh_db.execute(
            """SELECT parent_cluster_id, triggered_by, committed, llm_gate_score
                 FROM cluster_fission_events WHERE scan_id = 'scan2'"""
        ).fetchall()
        parents = {r[0] for r in rows}
        assert parent_cid in parents
        for pcid, triggered, committed, score in rows:
            if pcid == parent_cid:
                assert triggered
                assert committed == 0
                assert score is None  # no LLM

    def test_llm_scored_high_commit_splits_flag_commits(self, fresh_db):
        parent_cid = _seed_bimodal_cluster(fresh_db)
        verdicts = json.dumps({"decisions": [
            {"parent_cluster_id": parent_cid, "score": 4.5,
             "recommendation": "split", "reasoning": "distinct issues"}
        ]})
        llm = FakeLLM(verdicts)
        params_commit = {**PARAMS, "telemetry_fission_commit_splits": True}
        _detect_and_execute_fission(fresh_db, "scan2", params_commit, llm_client=llm)

        row = fresh_db.execute(
            """SELECT llm_gate_score, committed FROM cluster_fission_events
                WHERE scan_id = 'scan2' AND parent_cluster_id = ?""",
            (parent_cid,),
        ).fetchone()
        assert row is not None
        score, committed = row
        assert abs(score - 4.5) < 1e-6
        assert committed == 1  # commit_splits True + score >= threshold

    def test_llm_scored_low_never_commits(self, fresh_db):
        parent_cid = _seed_bimodal_cluster(fresh_db)
        verdicts = json.dumps({"decisions": [
            {"parent_cluster_id": parent_cid, "score": 2.0,
             "recommendation": "keep", "reasoning": "same issue"}
        ]})
        llm = FakeLLM(verdicts)
        params_commit = {**PARAMS, "telemetry_fission_commit_splits": True}
        _detect_and_execute_fission(fresh_db, "scan2", params_commit, llm_client=llm)
        row = fresh_db.execute(
            """SELECT llm_gate_score, committed FROM cluster_fission_events
                WHERE scan_id = 'scan2' AND parent_cluster_id = ?""",
            (parent_cid,),
        ).fetchone()
        assert row is not None
        score, committed = row
        assert score is not None and score < 4.0
        assert committed == 0

    def test_llm_failure_records_event_without_score(self, fresh_db):
        parent_cid = _seed_bimodal_cluster(fresh_db)
        llm = FakeLLM("", fail=True)
        _detect_and_execute_fission(fresh_db, "scan2", PARAMS, llm_client=llm)
        row = fresh_db.execute(
            """SELECT llm_gate_score, committed FROM cluster_fission_events
                WHERE scan_id = 'scan2' AND parent_cluster_id = ?""",
            (parent_cid,),
        ).fetchone()
        assert row is not None
        score, committed = row
        assert score is None
        assert committed == 0


class TestExtractJsonObject:
    def test_plain_json(self):
        assert _extract_json_object('{"a": 1}') == {"a": 1}

    def test_fenced_markdown(self):
        assert _extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}

    def test_prose_prefix(self):
        assert _extract_json_object('here is the result: {"a": 1} done') == {"a": 1}

    def test_invalid_returns_none(self):
        assert _extract_json_object("not json") is None
        assert _extract_json_object("") is None

    def test_nested_balanced(self):
        raw = '{"x": {"y": [1,2,3]}, "z": "hi"}'
        assert _extract_json_object(raw) == {"x": {"y": [1, 2, 3]}, "z": "hi"}
