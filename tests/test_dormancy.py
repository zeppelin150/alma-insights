"""Tests for Phase 6 dormancy + resurrection detection.

Covers `_detect_dormancy_and_resurrection` and tier transitions:
    active → dormant (after N consecutive empty scans)
    dormant → retired (after M consecutive empty scans)
    dormant / retired → active (on ≥ K new members)
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import numpy as np
import pytest

from src.data.canonicalization_engine import (
    DEFAULT_PARAMS,
    _detect_dormancy_and_resurrection,
    _l2_normalize,
    _snapshot_prior_state,
    _vec_to_blob,
    run_canonicalization,
)
from src.data.db_manager import DatabaseManager


EMBED_DIM = 1024
PARAMS = {
    **DEFAULT_PARAMS,
    "telemetry_dormancy_enabled": True,
    "min_cluster_size": 5,
    "dormancy_threshold_scans": 3,
    "retirement_threshold_scans": 10,
    "resurrection_min_members": 3,
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


def _insert_embedding(conn, tid: str, vec: np.ndarray):
    v = _l2_normalize(vec.astype(np.float32))
    conn.execute(
        """INSERT OR REPLACE INTO ticket_embeddings
             (ticket_id, embedding_blob, source_text_hash, model_name, dim_size, created_at)
           VALUES (?, ?, ?, 'Qwen3-Embedding-0.6B', ?, '2026-04-15T00:00:00')""",
        (tid, _vec_to_blob(v), f"h{tid}", len(v)),
    )


def _seed_cluster(fresh_db, *, cluster_id: str, tier: str = "active",
                   scans_without: int = 0, member_count: int = 5) -> np.ndarray:
    """Create a canonical_clusters row + ticket_index members. Returns centroid."""
    rng = np.random.default_rng(17)
    if member_count > 0:
        vecs = _l2_normalize(rng.standard_normal((member_count, EMBED_DIM)).astype(np.float32))
        centroid = _l2_normalize(np.mean(vecs, axis=0))
    else:
        # No current members — still need a deterministic, non-NaN centroid
        # so resurrection detection can compute cosine against new members.
        centroid = _l2_normalize(rng.standard_normal(EMBED_DIM).astype(np.float32))
        vecs = np.empty((0, EMBED_DIM), dtype=np.float32)
    fresh_db.execute(
        """INSERT INTO canonical_clusters
             (cluster_id, trc, canonical_label, centroid_blob, tier,
              scans_without_members, member_count, discovered_scan_id)
           VALUES (?, 'BILLING', 'Test cluster', ?, ?, ?, ?, 'scan0')""",
        (cluster_id, _vec_to_blob(centroid), tier, scans_without, member_count),
    )
    for i, v in enumerate(vecs):
        tid = f"{cluster_id}_t{i}"
        _insert_ticket(fresh_db, tid)
        _insert_embedding(fresh_db, tid, v)
        fresh_db.execute(
            "UPDATE ticket_index SET canonical_issue_id = ? WHERE ticket_id = ?",
            (cluster_id, tid),
        )
    fresh_db.commit()
    return centroid


def _remove_all_members(fresh_db, cluster_id: str) -> None:
    fresh_db.execute(
        "UPDATE ticket_index SET canonical_issue_id = NULL WHERE canonical_issue_id = ?",
        (cluster_id,),
    )
    fresh_db.commit()


def _add_new_members(fresh_db, cluster_id: str, centroid: np.ndarray,
                      n: int, label_prefix: str = "NEW") -> None:
    rng = np.random.default_rng(99)
    noise = 0.1 * rng.standard_normal((n, EMBED_DIM)).astype(np.float32)
    vecs = _l2_normalize(centroid + noise)
    for i, v in enumerate(vecs):
        tid = f"{label_prefix}_{cluster_id}_{i}"
        _insert_ticket(fresh_db, tid)
        _insert_embedding(fresh_db, tid, v)
        fresh_db.execute(
            "UPDATE ticket_index SET canonical_issue_id = ? WHERE ticket_id = ?",
            (cluster_id, tid),
        )
    fresh_db.commit()


# ──────────────────────────────────────────────────────────────────────
# Counter behavior
# ──────────────────────────────────────────────────────────────────────

class TestCounterUpdates:
    def test_active_with_members_keeps_counter_zero(self, fresh_db):
        _seed_cluster(fresh_db, cluster_id="c1", tier="active", scans_without=0)
        _detect_dormancy_and_resurrection(fresh_db, "scan2", prior_state={}, params=PARAMS)
        row = fresh_db.execute(
            "SELECT scans_without_members, tier FROM canonical_clusters WHERE cluster_id = 'c1'"
        ).fetchone()
        assert row[0] == 0
        assert row[1] == "active"

    def test_active_with_zero_members_increments_counter(self, fresh_db):
        _seed_cluster(fresh_db, cluster_id="c1", tier="active", scans_without=0)
        _remove_all_members(fresh_db, "c1")
        _detect_dormancy_and_resurrection(fresh_db, "scan2", prior_state={}, params=PARAMS)
        row = fresh_db.execute(
            "SELECT scans_without_members, tier FROM canonical_clusters WHERE cluster_id = 'c1'"
        ).fetchone()
        # Counter incremented, tier still active (counter < 3)
        assert row[0] == 1
        assert row[1] == "active"

    def test_counter_resets_when_members_return(self, fresh_db):
        _seed_cluster(fresh_db, cluster_id="c1", tier="active", scans_without=2)
        # Members still present → counter should reset to 0
        _detect_dormancy_and_resurrection(fresh_db, "scan2", prior_state={}, params=PARAMS)
        row = fresh_db.execute(
            "SELECT scans_without_members FROM canonical_clusters WHERE cluster_id = 'c1'"
        ).fetchone()
        assert row[0] == 0


# ──────────────────────────────────────────────────────────────────────
# Tier transitions
# ──────────────────────────────────────────────────────────────────────

class TestTierTransitions:
    def test_active_to_dormant_at_threshold(self, fresh_db):
        _seed_cluster(fresh_db, cluster_id="c1", tier="active", scans_without=2)
        _remove_all_members(fresh_db, "c1")
        n = _detect_dormancy_and_resurrection(fresh_db, "scan3", prior_state={}, params=PARAMS)
        assert n >= 1
        tier = fresh_db.execute(
            "SELECT tier FROM canonical_clusters WHERE cluster_id = 'c1'"
        ).fetchone()[0]
        assert tier == "dormant"
        event = fresh_db.execute(
            "SELECT tier_transition, scans_without_members FROM dormancy_events "
            "WHERE cluster_id = 'c1'"
        ).fetchone()
        assert event[0] == "active->dormant"
        assert event[1] == 2  # counter value before increment this scan

    def test_dormant_to_retired_at_threshold(self, fresh_db):
        _seed_cluster(fresh_db, cluster_id="c1", tier="dormant", scans_without=9)
        _remove_all_members(fresh_db, "c1")
        n = _detect_dormancy_and_resurrection(fresh_db, "scan10", prior_state={}, params=PARAMS)
        assert n >= 1
        tier = fresh_db.execute(
            "SELECT tier FROM canonical_clusters WHERE cluster_id = 'c1'"
        ).fetchone()[0]
        assert tier == "retired"
        event = fresh_db.execute(
            "SELECT tier_transition FROM dormancy_events WHERE cluster_id = 'c1'"
        ).fetchone()[0]
        assert event == "dormant->retired"

    def test_no_transition_below_threshold(self, fresh_db):
        _seed_cluster(fresh_db, cluster_id="c1", tier="active", scans_without=1)
        _remove_all_members(fresh_db, "c1")
        n = _detect_dormancy_and_resurrection(fresh_db, "scan2", prior_state={}, params=PARAMS)
        assert n == 0
        tier = fresh_db.execute(
            "SELECT tier FROM canonical_clusters WHERE cluster_id = 'c1'"
        ).fetchone()[0]
        assert tier == "active"


# ──────────────────────────────────────────────────────────────────────
# Resurrection
# ──────────────────────────────────────────────────────────────────────

class TestResurrection:
    def test_dormant_resurrected_by_enough_new_members(self, fresh_db):
        centroid = _seed_cluster(
            fresh_db, cluster_id="c1", tier="dormant", scans_without=5,
            member_count=0,
        )
        _add_new_members(fresh_db, "c1", centroid, n=3)
        # Prior state: cluster was dormant with zero prior members
        prior = {"c1": {"members": set()}}
        n = _detect_dormancy_and_resurrection(fresh_db, "scan6", prior_state=prior, params=PARAMS)
        assert n >= 1
        row = fresh_db.execute(
            "SELECT tier, scans_without_members FROM canonical_clusters WHERE cluster_id = 'c1'"
        ).fetchone()
        assert row[0] == "active"
        assert row[1] == 0  # counter reset

        event = fresh_db.execute(
            """SELECT tier_transition, confirmation_gate_passed, avg_resurrection_cosine,
                      resurrecting_ticket_ids_json
                 FROM dormancy_events WHERE cluster_id = 'c1'"""
        ).fetchone()
        transition, gate, cos_avg, ids_json = event
        assert transition == "dormant->active"
        assert gate in (1, True)
        assert cos_avg is not None and 0.0 <= cos_avg <= 1.0 + 1e-5
        ids = json.loads(ids_json)
        assert len(ids) == 3

    def test_retired_can_also_resurrect(self, fresh_db):
        centroid = _seed_cluster(
            fresh_db, cluster_id="c1", tier="retired", scans_without=15,
            member_count=0,
        )
        _add_new_members(fresh_db, "c1", centroid, n=4)
        prior = {"c1": {"members": set()}}
        _detect_dormancy_and_resurrection(fresh_db, "scan20", prior_state=prior, params=PARAMS)
        tier = fresh_db.execute(
            "SELECT tier FROM canonical_clusters WHERE cluster_id = 'c1'"
        ).fetchone()[0]
        assert tier == "active"
        t = fresh_db.execute(
            "SELECT tier_transition FROM dormancy_events WHERE cluster_id = 'c1'"
        ).fetchone()[0]
        assert t == "retired->active"

    def test_dormant_with_few_new_members_stays_dormant(self, fresh_db):
        centroid = _seed_cluster(
            fresh_db, cluster_id="c1", tier="dormant", scans_without=5,
            member_count=0,
        )
        # Only 2 new members — below resurrection threshold (3)
        _add_new_members(fresh_db, "c1", centroid, n=2)
        prior = {"c1": {"members": set()}}
        _detect_dormancy_and_resurrection(fresh_db, "scan6", prior_state=prior, params=PARAMS)
        tier = fresh_db.execute(
            "SELECT tier FROM canonical_clusters WHERE cluster_id = 'c1'"
        ).fetchone()[0]
        assert tier == "dormant"
        events = fresh_db.execute(
            "SELECT COUNT(*) FROM dormancy_events WHERE cluster_id = 'c1'"
        ).fetchone()[0]
        assert events == 0


# ──────────────────────────────────────────────────────────────────────
# End-to-end via run_canonicalization telemetry path
# ──────────────────────────────────────────────────────────────────────

class TestRunCanonicalizationDormancyPath:
    def test_disabled_writes_no_events(self, fresh_db):
        _seed_cluster(fresh_db, cluster_id="c1", tier="active", scans_without=2)
        _remove_all_members(fresh_db, "c1")
        # Run canonicalization with telemetry_dormancy_enabled=False (default)
        run_canonicalization(fresh_db, scan_id="scan3", params=DEFAULT_PARAMS)
        n = fresh_db.execute("SELECT COUNT(*) FROM dormancy_events").fetchone()[0]
        assert n == 0

    def test_enabled_transitions_active_to_dormant(self, fresh_db):
        _seed_cluster(fresh_db, cluster_id="c1", tier="active", scans_without=2)
        _remove_all_members(fresh_db, "c1")
        run_canonicalization(fresh_db, scan_id="scan3", params=PARAMS)
        row = fresh_db.execute(
            "SELECT tier FROM canonical_clusters WHERE cluster_id = 'c1'"
        ).fetchone()
        assert row[0] == "dormant"
