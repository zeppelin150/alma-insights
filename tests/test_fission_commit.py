"""Tests for S10.3 fission split commit execution.

Covers `_commit_fission_split` and the commit branch inside
`_detect_and_execute_fission`. Default path (commit flag OFF) stays
non-committing.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import numpy as np
import pytest

from src.data.canonicalization_engine import (
    DEFAULT_PARAMS,
    _commit_fission_split,
    _detect_and_execute_fission,
    _l2_normalize,
    _vec_to_blob,
    run_canonicalization,
)
from src.data.db_manager import DatabaseManager


EMBED_DIM = 1024
BASE_PARAMS = {
    **DEFAULT_PARAMS,
    "telemetry_fission_enabled": True,
    "telemetry_fission_commit_splits": True,
    "fission_llm_commit_score": 4.0,
    "fission_silhouette_improvement_min": -1.0,  # relax for test bimodal data
    "fission_variance_threshold": 0.0001,         # always trigger
    "min_cluster_size": 3,
    "fission_subhdbscan_min_cluster_size": 3,
}


class FakeLLM:
    """Minimal llm_client that returns a fixed gate score."""
    def __init__(self, score: float, recommendation: str = "commit"):
        self.score = score
        self.rec = recommendation

    def generate(self, prompt: str, timeout: int = 180) -> str:
        # Parse the number of candidates from the payload (one score per cluster)
        # The prompt embeds a JSON with `parent_cluster_id` per candidate;
        # return a generic 'decisions' array keyed by that.
        import re
        cids = re.findall(r'"parent_cluster_id"\s*:\s*"([^"]+)"', prompt)
        decisions = [
            {
                "parent_cluster_id": cid,
                "score": self.score,
                "reasoning": "fake reasoning",
                "recommendation": self.rec,
            }
            for cid in cids
        ]
        return json.dumps({"decisions": decisions})


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


def _insert_ticket(conn, tid: str, trc: str = "BILLING", subj: str = "s"):
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


def _seed_bimodal_parent(conn, n_per_lobe: int = 10, trc: str = "BILLING"):
    """Seed a parent cluster whose members fall on two distant centers —
    a clean fission target."""
    rng = np.random.default_rng(42)
    lobe_a_center = _l2_normalize(
        np.random.default_rng(11).standard_normal(EMBED_DIM).astype(np.float32)
    )
    lobe_b_center = _l2_normalize(
        np.random.default_rng(77).standard_normal(EMBED_DIM).astype(np.float32)
    )
    all_embeds = []
    all_tids: list[str] = []
    for i in range(n_per_lobe):
        noise = rng.standard_normal(EMBED_DIM).astype(np.float32) * 0.01
        e = _l2_normalize(lobe_a_center + noise)
        tid = f"A{i}"
        _insert_ticket(conn, tid, trc=trc, subj=f"billing A {i}")
        _insert_embedding(conn, tid, e)
        all_embeds.append(e)
        all_tids.append(tid)
    for i in range(n_per_lobe):
        noise = rng.standard_normal(EMBED_DIM).astype(np.float32) * 0.01
        e = _l2_normalize(lobe_b_center + noise)
        tid = f"B{i}"
        _insert_ticket(conn, tid, trc=trc, subj=f"billing B {i}")
        _insert_embedding(conn, tid, e)
        all_embeds.append(e)
        all_tids.append(tid)
    all_mat = np.vstack(all_embeds).astype(np.float32)
    # Parent centroid = mean → splits evenly between lobes → high variance
    parent_cent = _l2_normalize(all_mat.mean(axis=0))
    parent_cid = "parent-1"
    conn.execute(
        """INSERT INTO canonical_clusters
             (cluster_id, trc, centroid_blob, representative_ticket_id,
              member_count, lifetime_tickets, lifetime_scans, tier,
              discovered_scan_id, last_seen_scan_id, label_source,
              canonical_label)
           VALUES (?, ?, ?, NULL, ?, ?, 1, 'active', 'scan1', 'scan1',
                   'medoid', 'billing mixed')""",
        (parent_cid, trc, _vec_to_blob(parent_cent), len(all_tids), len(all_tids)),
    )
    # Assign all to parent
    for tid in all_tids:
        conn.execute(
            """UPDATE ticket_index
                  SET canonical_issue_id = ?,
                      canonical_confidence = 0.85,
                      assignment_method = 'hdbscan_core'
                WHERE ticket_id = ?""",
            (parent_cid, tid),
        )
    conn.commit()
    return parent_cid, all_tids


# ──────────────────────────────────────────────────────────────────────────
# _commit_fission_split unit tests (no LLM gate involved)
# ──────────────────────────────────────────────────────────────────────────

def test_commit_creates_two_child_clusters(fresh_db):
    _seed_bimodal_parent(fresh_db, n_per_lobe=5)
    tids = [f"A{i}" for i in range(5)] + [f"B{i}" for i in range(5)]
    sub_labels = [0] * 5 + [1] * 5

    child_cids, reassigned = _commit_fission_split(
        fresh_db,
        parent_cluster_id="parent-1",
        sub_labels=sub_labels,
        ticket_ids=tids,
        scan_id="scan2",
        params=BASE_PARAMS,
    )
    assert len(child_cids) == 2
    assert reassigned == 10

    # Child rows exist
    for cid in child_cids:
        row = fresh_db.execute(
            "SELECT tier FROM canonical_clusters WHERE cluster_id = ?",
            (cid,),
        ).fetchone()
        assert row is not None
        assert row[0] == "active"


def test_commit_reassigns_tickets(fresh_db):
    _seed_bimodal_parent(fresh_db, n_per_lobe=5)
    tids = [f"A{i}" for i in range(5)] + [f"B{i}" for i in range(5)]
    sub_labels = [0] * 5 + [1] * 5

    child_cids, _ = _commit_fission_split(
        fresh_db,
        parent_cluster_id="parent-1",
        sub_labels=sub_labels,
        ticket_ids=tids,
        scan_id="scan2",
        params=BASE_PARAMS,
    )

    # Every ticket now points at a child, not the parent
    parent_count = fresh_db.execute(
        "SELECT COUNT(*) FROM ticket_index WHERE canonical_issue_id = 'parent-1'"
    ).fetchone()[0]
    assert parent_count == 0
    child_count = fresh_db.execute(
        "SELECT COUNT(*) FROM ticket_index WHERE canonical_issue_id IN (?, ?)",
        tuple(child_cids),
    ).fetchone()[0]
    assert child_count == 10


def test_commit_parents_tier_becomes_split(fresh_db):
    _seed_bimodal_parent(fresh_db, n_per_lobe=5)
    tids = [f"A{i}" for i in range(5)] + [f"B{i}" for i in range(5)]
    sub_labels = [0] * 5 + [1] * 5

    child_cids, _ = _commit_fission_split(
        fresh_db,
        parent_cluster_id="parent-1",
        sub_labels=sub_labels,
        ticket_ids=tids,
        scan_id="scan2",
        params=BASE_PARAMS,
    )

    row = fresh_db.execute(
        "SELECT tier, split_into_json FROM canonical_clusters WHERE cluster_id = 'parent-1'"
    ).fetchone()
    assert row[0] == "split"
    parsed = json.loads(row[1])
    assert set(parsed) == set(child_cids)


def test_commit_assignment_method_is_fission_split(fresh_db):
    _seed_bimodal_parent(fresh_db, n_per_lobe=5)
    tids = [f"A{i}" for i in range(5)] + [f"B{i}" for i in range(5)]
    sub_labels = [0] * 5 + [1] * 5
    _commit_fission_split(
        fresh_db,
        parent_cluster_id="parent-1",
        sub_labels=sub_labels,
        ticket_ids=tids,
        scan_id="scan2",
        params=BASE_PARAMS,
    )
    methods = {r[0] for r in fresh_db.execute(
        "SELECT DISTINCT assignment_method FROM ticket_index WHERE canonical_issue_id != 'parent-1'"
    ).fetchall()}
    assert methods == {"fission_split"}


def test_commit_rejects_insufficient_sub_labels(fresh_db):
    _seed_bimodal_parent(fresh_db, n_per_lobe=5)
    tids = [f"A{i}" for i in range(5)]
    sub_labels = [0] * 5  # only one sub-label → can't split
    with pytest.raises(RuntimeError, match="≥2 sub-clusters"):
        _commit_fission_split(
            fresh_db,
            parent_cluster_id="parent-1",
            sub_labels=sub_labels,
            ticket_ids=tids,
            scan_id="scan2",
            params=BASE_PARAMS,
        )


def test_commit_rejects_unknown_parent(fresh_db):
    with pytest.raises(RuntimeError, match="not found"):
        _commit_fission_split(
            fresh_db,
            parent_cluster_id="ghost-cluster",
            sub_labels=[0, 1],
            ticket_ids=["x", "y"],
            scan_id="scan2",
            params=BASE_PARAMS,
        )


def test_commit_skips_noise_label(fresh_db):
    _seed_bimodal_parent(fresh_db, n_per_lobe=5)
    tids = [f"A{i}" for i in range(5)] + [f"B{i}" for i in range(5)]
    # Introduce one noise label (-1) in the mix
    sub_labels = [0] * 4 + [-1] + [1] * 5
    child_cids, reassigned = _commit_fission_split(
        fresh_db,
        parent_cluster_id="parent-1",
        sub_labels=sub_labels,
        ticket_ids=tids,
        scan_id="scan2",
        params=BASE_PARAMS,
    )
    assert len(child_cids) == 2
    # Noise ticket does NOT count as reassigned
    assert reassigned == 9


# ──────────────────────────────────────────────────────────────────────────
# _detect_and_execute_fission integration — commit flag semantics
# ──────────────────────────────────────────────────────────────────────────

def test_detect_with_commit_off_does_not_commit(fresh_db):
    _seed_bimodal_parent(fresh_db, n_per_lobe=10)
    off_params = {**BASE_PARAMS, "telemetry_fission_commit_splits": False}
    llm = FakeLLM(score=4.5)
    _detect_and_execute_fission(fresh_db, scan_id="scan2", params=off_params, llm_client=llm)

    # Parent should still be active — not split
    tier = fresh_db.execute(
        "SELECT tier FROM canonical_clusters WHERE cluster_id = 'parent-1'"
    ).fetchone()[0]
    assert tier == "active"
    # Event row exists with committed=0
    row = fresh_db.execute(
        "SELECT committed, tickets_reassigned_count FROM cluster_fission_events WHERE parent_cluster_id = 'parent-1'"
    ).fetchone()
    assert row is not None
    assert row[0] == 0
    assert row[1] == 0


def test_detect_with_commit_on_and_gate_pass_commits(fresh_db):
    _seed_bimodal_parent(fresh_db, n_per_lobe=10)
    llm = FakeLLM(score=4.5)
    _detect_and_execute_fission(fresh_db, scan_id="scan2", params=BASE_PARAMS, llm_client=llm)

    tier = fresh_db.execute(
        "SELECT tier FROM canonical_clusters WHERE cluster_id = 'parent-1'"
    ).fetchone()[0]
    assert tier == "split"

    # Events table records commit + reassignment count
    row = fresh_db.execute(
        """SELECT committed, tickets_reassigned_count, child_cluster_ids_json
             FROM cluster_fission_events WHERE parent_cluster_id = 'parent-1'"""
    ).fetchone()
    assert row[0] == 1
    assert row[1] > 0
    cids = json.loads(row[2])
    assert len(cids) >= 2


def test_detect_with_commit_on_but_score_below_threshold_does_not_commit(fresh_db):
    _seed_bimodal_parent(fresh_db, n_per_lobe=10)
    llm = FakeLLM(score=3.0)  # below commit_score=4.0
    _detect_and_execute_fission(fresh_db, scan_id="scan2", params=BASE_PARAMS, llm_client=llm)

    tier = fresh_db.execute(
        "SELECT tier FROM canonical_clusters WHERE cluster_id = 'parent-1'"
    ).fetchone()[0]
    assert tier == "active"
    row = fresh_db.execute(
        "SELECT committed FROM cluster_fission_events WHERE parent_cluster_id = 'parent-1'"
    ).fetchone()
    assert row[0] == 0


def test_detect_without_llm_never_commits(fresh_db):
    _seed_bimodal_parent(fresh_db, n_per_lobe=10)
    # No LLM → no gate score → won't commit even with commit_splits=True
    _detect_and_execute_fission(fresh_db, scan_id="scan2", params=BASE_PARAMS, llm_client=None)
    tier = fresh_db.execute(
        "SELECT tier FROM canonical_clusters WHERE cluster_id = 'parent-1'"
    ).fetchone()[0]
    assert tier == "active"


def test_commit_rollback_on_failure(fresh_db, monkeypatch):
    """Monkey-patch _upsert_cluster to raise on 2nd call. Parent should NOT
    be marked split and ticket_index rows should NOT be reassigned."""
    from src.data import canonicalization_engine as eng

    _seed_bimodal_parent(fresh_db, n_per_lobe=5)
    tids = [f"A{i}" for i in range(5)] + [f"B{i}" for i in range(5)]
    sub_labels = [0] * 5 + [1] * 5

    orig = eng._upsert_cluster
    calls = {"n": 0}

    def boom(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("synthetic child insert failure")
        return orig(*args, **kwargs)

    monkeypatch.setattr(eng, "_upsert_cluster", boom)

    with pytest.raises(RuntimeError, match="synthetic child insert"):
        _commit_fission_split(
            fresh_db,
            parent_cluster_id="parent-1",
            sub_labels=sub_labels,
            ticket_ids=tids,
            scan_id="scan2",
            params=BASE_PARAMS,
        )

    # Because we ran without an enclosing atomic(), the first child may
    # already be in the DB — but the parent should NOT yet be tier='split'
    # (the parent update happens last).
    tier = fresh_db.execute(
        "SELECT tier FROM canonical_clusters WHERE cluster_id = 'parent-1'"
    ).fetchone()[0]
    assert tier == "active"


# ──────────────────────────────────────────────────────────────────────────
# Fission event record integrity
# ──────────────────────────────────────────────────────────────────────────

def test_committed_event_records_tickets_reassigned_count(fresh_db):
    _seed_bimodal_parent(fresh_db, n_per_lobe=10)
    llm = FakeLLM(score=5.0)
    _detect_and_execute_fission(fresh_db, scan_id="scan2", params=BASE_PARAMS, llm_client=llm)
    row = fresh_db.execute(
        "SELECT tickets_reassigned_count FROM cluster_fission_events WHERE parent_cluster_id = 'parent-1'"
    ).fetchone()
    assert row[0] == 20  # both lobes reassigned
