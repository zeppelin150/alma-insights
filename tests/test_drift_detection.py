"""Tests for Phase 6 drift detection (src/data/canonicalization_engine.py).

Covers `_snapshot_prior_state`, `_detect_and_log_drift`, and the
`telemetry_drift_enabled` path through `run_canonicalization`.

Strategy: seed a DB at state A, snapshot, mutate to state B via another
canonicalization call or direct centroid rewrites, then assert that
cluster_drift_events captures the expected deltas.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import numpy as np
import pytest

from src.data.canonicalization_engine import (
    DEFAULT_PARAMS,
    _classify_stability_band,
    _detect_and_log_drift,
    _l2_normalize,
    _snapshot_prior_state,
    _vec_to_blob,
    run_canonicalization,
)
from src.data.db_manager import DatabaseManager


EMBED_DIM = 1024
TELEMETRY_PARAMS = {**DEFAULT_PARAMS, "telemetry_drift_enabled": True, "min_cluster_size": 5}


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


def _insert_ticket(conn, ticket_id: str, trc: str = "BILLING", subject: str = "test") -> None:
    now = "2026-04-15T00:00:00"
    conn.execute(
        """INSERT INTO ticket_index
             (ticket_id, trc_code, first_seen_scan_id, last_seen_scan_id,
              first_seen_date, ticket_created_date, subject_sanitized)
           VALUES (?, ?, 'ingestion', 'scan1', ?, ?, ?)""",
        (ticket_id, trc, now, now, subject),
    )
    conn.execute(
        """INSERT INTO nlp_ticket_classifications
             (classification_id, batch_id, ticket_id, scan_id, trc,
              sub_cluster, sub_cluster_confidence, created_at)
           VALUES (?, 'b1', ?, 'scan1', ?, 'x', 0.85, ?)""",
        (uuid.uuid4().hex, ticket_id, trc, now),
    )


def _insert_embedding(conn, ticket_id: str, vec: np.ndarray) -> None:
    v = _l2_normalize(vec.astype(np.float32))
    conn.execute(
        """INSERT OR REPLACE INTO ticket_embeddings
             (ticket_id, embedding_blob, source_text_hash, model_name,
              dim_size, created_at)
           VALUES (?, ?, ?, 'Qwen3-Embedding-0.6B', ?, '2026-04-15T00:00:00')""",
        (ticket_id, _vec_to_blob(v), f"hash_{ticket_id}", len(v)),
    )


def _make_cluster(rng: np.random.Generator, center_seed: int, n: int,
                   noise_std: float | None = None, dim: int = EMBED_DIM) -> np.ndarray:
    cr = np.random.default_rng(center_seed)
    c = _l2_normalize(cr.standard_normal(dim).astype(np.float32))
    if noise_std is None:
        noise_std = 0.4 / np.sqrt(dim)
    deltas = rng.standard_normal((n, dim)).astype(np.float32) * noise_std
    return _l2_normalize(c + deltas)


def _seed_two_clusters_and_canonicalize(conn, *, scan_id: str = "scan1"):
    """Create 2 tight clusters (25 + 20 tickets) and run canonicalization."""
    rng = np.random.default_rng(2026)
    a = _make_cluster(rng, center_seed=11, n=25)
    b = _make_cluster(rng, center_seed=77, n=20)
    for i, v in enumerate(a):
        _insert_ticket(conn, f"A{i}", trc="BILLING", subject=f"billing issue {i}")
        _insert_embedding(conn, f"A{i}", v)
    for i, v in enumerate(b):
        _insert_ticket(conn, f"B{i}", trc="BILLING", subject=f"portal error {i}")
        _insert_embedding(conn, f"B{i}", v)
    conn.commit()
    return run_canonicalization(conn, scan_id=scan_id, params=TELEMETRY_PARAMS)


# ──────────────────────────────────────────────────────────────────────
# Stability band classifier
# ──────────────────────────────────────────────────────────────────────

class TestStabilityBand:
    def test_stable_zone(self):
        assert _classify_stability_band(0.0, DEFAULT_PARAMS) == "stable"
        assert _classify_stability_band(0.04, DEFAULT_PARAMS) == "stable"

    def test_normal_zone(self):
        assert _classify_stability_band(0.05, DEFAULT_PARAMS) == "normal"
        assert _classify_stability_band(0.14, DEFAULT_PARAMS) == "normal"

    def test_drifting_zone(self):
        assert _classify_stability_band(0.15, DEFAULT_PARAMS) == "drifting"
        assert _classify_stability_band(0.29, DEFAULT_PARAMS) == "drifting"

    def test_unstable_zone(self):
        assert _classify_stability_band(0.30, DEFAULT_PARAMS) == "unstable"
        assert _classify_stability_band(0.9, DEFAULT_PARAMS) == "unstable"


# ──────────────────────────────────────────────────────────────────────
# Snapshot
# ──────────────────────────────────────────────────────────────────────

class TestSnapshotPriorState:
    def test_empty_db_returns_empty_dict(self, fresh_db):
        assert _snapshot_prior_state(fresh_db) == {}

    def test_post_canonicalization_snapshot_is_populated(self, fresh_db):
        _seed_two_clusters_and_canonicalize(fresh_db, scan_id="scan1")
        snap = _snapshot_prior_state(fresh_db)
        assert len(snap) >= 2
        for cid, entry in snap.items():
            assert isinstance(entry["members"], set)
            assert entry["member_count"] == len(entry["members"])
            assert entry["centroid"] is not None
            assert entry["centroid"].shape == (EMBED_DIM,)
            # Centroid is L2-normalized
            assert abs(float(np.linalg.norm(entry["centroid"])) - 1.0) < 1e-4

    def test_snapshot_includes_variance_for_populated_clusters(self, fresh_db):
        _seed_two_clusters_and_canonicalize(fresh_db)
        snap = _snapshot_prior_state(fresh_db)
        for _cid, entry in snap.items():
            assert entry["variance"] is not None
            assert 0.0 <= entry["variance"] <= 2.0

    def test_snapshot_silhouette_populated_when_multiple_clusters(self, fresh_db):
        _seed_two_clusters_and_canonicalize(fresh_db)
        snap = _snapshot_prior_state(fresh_db)
        # With 2 well-separated clusters we expect positive silhouette
        any_positive = any((e.get("silhouette") or 0.0) > 0.0 for e in snap.values())
        assert any_positive


# ──────────────────────────────────────────────────────────────────────
# Drift detection
# ──────────────────────────────────────────────────────────────────────

class TestDetectAndLogDrift:
    def test_empty_prior_state_writes_no_events(self, fresh_db):
        _seed_two_clusters_and_canonicalize(fresh_db, scan_id="scan1")
        n = _detect_and_log_drift(fresh_db, "scan2", prior_state={}, params=TELEMETRY_PARAMS)
        assert n == 0
        rows = fresh_db.execute("SELECT COUNT(*) FROM cluster_drift_events").fetchone()[0]
        assert rows == 0

    def test_unchanged_centroids_produce_stable_band(self, fresh_db):
        _seed_two_clusters_and_canonicalize(fresh_db, scan_id="scan1")
        prior = _snapshot_prior_state(fresh_db)
        # No mutations between snapshots → drift_cosine ≈ 0
        n = _detect_and_log_drift(fresh_db, "scan2", prior_state=prior, params=TELEMETRY_PARAMS)
        assert n >= 2
        rows = fresh_db.execute(
            "SELECT drift_cosine, stability_band, members_added, members_lost FROM cluster_drift_events WHERE scan_id = 'scan2'"
        ).fetchall()
        assert len(rows) >= 2
        for drift_cos, band, added, lost in rows:
            assert drift_cos < 1e-4
            assert band == "stable"
            assert added == 0
            assert lost == 0

    def test_shifted_centroid_produces_nonzero_drift(self, fresh_db):
        _seed_two_clusters_and_canonicalize(fresh_db, scan_id="scan1")
        prior = _snapshot_prior_state(fresh_db)

        # Mutate: rotate one cluster's persisted centroid by ~0.2 cosine
        target_cid, prev = next(iter(prior.items()))
        rng = np.random.default_rng(999)
        delta = rng.standard_normal(EMBED_DIM).astype(np.float32)
        shifted = _l2_normalize(prev["centroid"] * 0.8 + delta * 0.2)
        fresh_db.execute(
            "UPDATE canonical_clusters SET centroid_blob = ? WHERE cluster_id = ?",
            (_vec_to_blob(shifted), target_cid),
        )
        fresh_db.commit()

        _detect_and_log_drift(fresh_db, "scan2", prior_state=prior, params=TELEMETRY_PARAMS)
        row = fresh_db.execute(
            "SELECT drift_cosine, stability_band FROM cluster_drift_events "
            "WHERE cluster_id = ? AND scan_id = 'scan2'",
            (target_cid,),
        ).fetchone()
        assert row is not None
        drift_cos, band = row
        assert drift_cos > 0.01
        assert band in ("stable", "normal", "drifting", "unstable")

    def test_unstable_drift_updates_tier(self, fresh_db):
        _seed_two_clusters_and_canonicalize(fresh_db, scan_id="scan1")
        prior = _snapshot_prior_state(fresh_db)
        target_cid, prev = next(iter(prior.items()))

        # Force centroid to an orthogonal direction → drift_cosine ≈ 1.0
        rng = np.random.default_rng(42)
        orth = rng.standard_normal(EMBED_DIM).astype(np.float32)
        # Make strictly perpendicular: remove projection onto prev
        orth = orth - prev["centroid"] * float(prev["centroid"] @ orth)
        orth = _l2_normalize(orth)
        fresh_db.execute(
            "UPDATE canonical_clusters SET centroid_blob = ? WHERE cluster_id = ?",
            (_vec_to_blob(orth), target_cid),
        )
        fresh_db.commit()

        _detect_and_log_drift(fresh_db, "scan2", prior_state=prior, params=TELEMETRY_PARAMS)
        # Tier must be 'drifting'
        row = fresh_db.execute(
            "SELECT tier FROM canonical_clusters WHERE cluster_id = ?",
            (target_cid,),
        ).fetchone()
        assert row[0] == "drifting"
        band = fresh_db.execute(
            "SELECT stability_band FROM cluster_drift_events WHERE cluster_id = ?",
            (target_cid,),
        ).fetchone()[0]
        assert band == "unstable"

    def test_member_churn_counted_correctly(self, fresh_db):
        _seed_two_clusters_and_canonicalize(fresh_db, scan_id="scan1")
        prior = _snapshot_prior_state(fresh_db)
        target_cid, prev = next(iter(prior.items()))
        original_members = set(prev["members"])
        assert len(original_members) >= 5

        # Remove 2 members, add 3 new ones (simulated by reassigning + new tickets)
        to_drop = list(original_members)[:2]
        for tid in to_drop:
            fresh_db.execute(
                "UPDATE ticket_index SET canonical_issue_id = NULL WHERE ticket_id = ?",
                (tid,),
            )
        # Inject 3 new tickets + embeddings pointing at the cluster
        rng = np.random.default_rng(88)
        for i, v in enumerate(_make_cluster(rng, center_seed=11, n=3)):
            new_tid = f"NEW{i}"
            _insert_ticket(fresh_db, new_tid, trc="BILLING", subject=f"new issue {i}")
            _insert_embedding(fresh_db, new_tid, v)
            fresh_db.execute(
                "UPDATE ticket_index SET canonical_issue_id = ? WHERE ticket_id = ?",
                (target_cid, new_tid),
            )
        fresh_db.commit()

        _detect_and_log_drift(fresh_db, "scan2", prior_state=prior, params=TELEMETRY_PARAMS)
        added, lost, retained = fresh_db.execute(
            "SELECT members_added, members_lost, members_retained "
            "FROM cluster_drift_events WHERE cluster_id = ?",
            (target_cid,),
        ).fetchone()
        assert added == 3
        assert lost == 2
        assert retained == len(original_members) - 2


# ──────────────────────────────────────────────────────────────────────
# End-to-end via run_canonicalization with telemetry_drift_enabled
# ──────────────────────────────────────────────────────────────────────

class TestRunCanonicalizationDriftPath:
    def test_disabled_writes_no_events(self, fresh_db):
        # Default params (telemetry_drift_enabled=False)
        _seed_two_clusters_and_canonicalize(fresh_db, scan_id="scan1")
        # Run a 2nd scan with flag OFF
        run_canonicalization(fresh_db, scan_id="scan2", params=DEFAULT_PARAMS)
        n = fresh_db.execute("SELECT COUNT(*) FROM cluster_drift_events").fetchone()[0]
        assert n == 0

    def test_enabled_writes_events_on_second_scan(self, fresh_db):
        _seed_two_clusters_and_canonicalize(fresh_db, scan_id="scan1")
        # Scan 2 with flag ON — drift events expected (identical centroids → stable band)
        run_canonicalization(fresh_db, scan_id="scan2", params=TELEMETRY_PARAMS)
        n = fresh_db.execute(
            "SELECT COUNT(*) FROM cluster_drift_events WHERE scan_id = 'scan2'"
        ).fetchone()[0]
        assert n >= 2
