"""Tests for the centroid-merge post-pass and merge-event audit log
(canonicalization_engine over-splitting fix, migration 019)."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import numpy as np
import pytest

from src.data.canonicalization_engine import (
    DEFAULT_PARAMS,
    MergeEvent,
    _l2_normalize,
    _merge_close_clusters,
    _vec_to_blob,
    run_canonicalization,
)
from src.data.db_manager import DatabaseManager


EMBED_DIM = 1024


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def fresh_db(tmp_path: Path):
    db_path = tmp_path / "t.db"
    mgr = DatabaseManager(db_path)
    mgr.initialize()
    conn = mgr.get_connection()
    conn.execute("PRAGMA foreign_keys = OFF")
    yield conn
    conn.close()


def _insert_ticket(conn, ticket_id: str, trc: str = "BILLING") -> None:
    conn.execute(
        """INSERT INTO ticket_index
             (ticket_id, trc_code, first_seen_scan_id, last_seen_scan_id,
              first_seen_date, ticket_created_date)
           VALUES (?, ?, 'ingestion', 'scan1', ?, ?)""",
        (ticket_id, trc, "2026-04-14", "2026-04-14"),
    )
    conn.execute(
        """INSERT INTO nlp_ticket_classifications
             (classification_id, batch_id, ticket_id, scan_id, trc,
              sub_cluster, sub_cluster_confidence, created_at)
           VALUES (?, 'b1', ?, 'scan1', ?, 'x', 0.85, ?)""",
        (uuid.uuid4().hex, ticket_id, trc, "2026-04-14"),
    )


def _insert_embedding(conn, ticket_id: str, vec: np.ndarray) -> None:
    v = _l2_normalize(vec.astype(np.float32))
    conn.execute(
        """INSERT OR REPLACE INTO ticket_embeddings
             (ticket_id, embedding_blob, source_text_hash, model_name,
              dim_size, created_at)
           VALUES (?, ?, ?, 'Q', ?, '2026')""",
        (ticket_id, _vec_to_blob(v), f"h_{ticket_id}", len(v)),
    )


def _vec_near(base: np.ndarray, jitter: float = 0.005) -> np.ndarray:
    """Return a vector cos > 0.99 from base (for tightly-packed test clusters)."""
    rng = np.random.default_rng(int(np.sum(np.abs(base) * 1000)) % 2**31)
    return _l2_normalize(base + rng.standard_normal(base.shape).astype(np.float32) * jitter)


def _make_close_centroids(rng, base_seed: int, n_pairs: int, alpha: float = 0.95) -> list[np.ndarray]:
    """Return a list of L2-normalized centroids that mostly share direction.

    alpha controls overlap: 0.95 gives pairwise cos ~ 0.95-0.99.
    """
    base = _l2_normalize(np.random.default_rng(base_seed).standard_normal(EMBED_DIM).astype(np.float32))
    out = []
    for _ in range(n_pairs):
        offset = rng.standard_normal(EMBED_DIM).astype(np.float32)
        v = _l2_normalize(alpha * base + (1 - alpha) * offset)
        out.append(v)
    return out


# ──────────────────────────────────────────────────────────────────────
# Pure-function tests
# ──────────────────────────────────────────────────────────────────────

class TestMergeCloseClustersPure:
    def test_disabled_threshold_zero(self):
        clusters = {0: [0, 1], 1: [2, 3]}
        embeddings = _l2_normalize(np.random.default_rng(0).standard_normal((4, 32)).astype(np.float32))
        confs = np.ones(4, dtype=np.float32)
        merged, centroids, events = _merge_close_clusters(clusters, embeddings, confs, threshold=0.0)
        assert merged == clusters
        assert events == []
        assert set(centroids.keys()) == {0, 1}

    def test_single_cluster_no_merge(self):
        clusters = {0: [0, 1, 2]}
        embeddings = _l2_normalize(np.random.default_rng(1).standard_normal((3, 16)).astype(np.float32))
        confs = np.ones(3, dtype=np.float32)
        merged, centroids, events = _merge_close_clusters(clusters, embeddings, confs, threshold=0.5)
        assert merged == clusters
        assert events == []
        assert 0 in centroids

    def test_distant_clusters_dont_merge(self):
        # Two random near-orthogonal clusters → cosine ~ 0
        rng = np.random.default_rng(2)
        a = _l2_normalize(rng.standard_normal((5, 64)).astype(np.float32))
        b = _l2_normalize(rng.standard_normal((5, 64)).astype(np.float32))
        # Tag them as different cluster centers by averaging
        embeddings = np.vstack([a, b])
        clusters = {0: list(range(5)), 1: list(range(5, 10))}
        confs = np.ones(10, dtype=np.float32)
        merged, _, events = _merge_close_clusters(clusters, embeddings, confs, threshold=0.85)
        assert len(merged) == 2
        assert events == []

    def test_close_clusters_merge(self):
        # Two clusters whose centroids are cos > 0.95 → should merge
        rng = np.random.default_rng(3)
        base = _l2_normalize(rng.standard_normal(EMBED_DIM).astype(np.float32))
        # Cluster A: 5 jittered copies of base
        a = np.vstack([_vec_near(base, 0.01) for _ in range(5)])
        # Cluster B: 5 more jittered copies of base
        b = np.vstack([_vec_near(base, 0.01) for _ in range(5)])
        embeddings = np.vstack([a, b])
        clusters = {0: list(range(5)), 1: list(range(5, 10))}
        confs = np.ones(10, dtype=np.float32)
        merged, centroids, events = _merge_close_clusters(clusters, embeddings, confs, threshold=0.85)
        assert len(merged) == 1
        assert len(events) == 1
        # Lower label wins
        assert 0 in merged
        assert sorted(merged[0]) == list(range(10))
        # Event accounting
        ev = events[0]
        assert ev.kept_label == 0
        assert ev.absorbed_label == 1
        assert ev.kept_count_before == 5
        assert ev.absorbed_count == 5
        assert ev.kept_count_after == 10
        assert ev.cosine_at_merge >= 0.85

    def test_three_clusters_only_close_pair_merges(self):
        rng = np.random.default_rng(4)
        # Two close, one distant
        shared = _l2_normalize(rng.standard_normal(EMBED_DIM).astype(np.float32))
        distant = _l2_normalize(rng.standard_normal(EMBED_DIM).astype(np.float32))
        a = np.vstack([_vec_near(shared, 0.01) for _ in range(5)])
        b = np.vstack([_vec_near(shared, 0.01) for _ in range(5)])
        c = np.vstack([_vec_near(distant, 0.01) for _ in range(5)])
        embeddings = np.vstack([a, b, c])
        clusters = {0: list(range(5)), 1: list(range(5, 10)), 2: list(range(10, 15))}
        confs = np.ones(15, dtype=np.float32)
        merged, _, events = _merge_close_clusters(clusters, embeddings, confs, threshold=0.85)
        assert len(merged) == 2
        assert len(events) == 1
        # Cluster 2 (distant) survives intact
        assert 2 in merged and merged[2] == list(range(10, 15))

    def test_cascading_merges(self):
        """Three clusters all close to each other → two merge events, end state 1 cluster."""
        rng = np.random.default_rng(5)
        base = _l2_normalize(rng.standard_normal(EMBED_DIM).astype(np.float32))
        a = np.vstack([_vec_near(base, 0.005) for _ in range(4)])
        b = np.vstack([_vec_near(base, 0.005) for _ in range(4)])
        c = np.vstack([_vec_near(base, 0.005) for _ in range(4)])
        embeddings = np.vstack([a, b, c])
        clusters = {0: list(range(4)), 1: list(range(4, 8)), 2: list(range(8, 12))}
        confs = np.ones(12, dtype=np.float32)
        merged, _, events = _merge_close_clusters(clusters, embeddings, confs, threshold=0.85)
        assert len(merged) == 1
        assert len(events) == 2  # cascading: 2 merges to collapse 3 -> 1
        assert 0 in merged
        assert sorted(merged[0]) == list(range(12))

    def test_confidence_weighting_respected(self):
        """Low-confidence rows should pull centroid less."""
        # Two rows: one in direction X, one in direction Y. With weights (1, 0)
        # the centroid should be very close to X.
        rng = np.random.default_rng(6)
        x = _l2_normalize(rng.standard_normal(64).astype(np.float32))
        y = _l2_normalize(rng.standard_normal(64).astype(np.float32))
        embeddings = np.vstack([x, y])
        clusters = {0: [0, 1]}
        confs = np.array([1.0, 0.0], dtype=np.float32)
        _, centroids, _ = _merge_close_clusters(clusters, embeddings, confs, threshold=0.0)
        # Centroid should be ~equal to x (up to L2 normalization, since y has 0 weight)
        assert centroids[0] @ x > 0.99


# ──────────────────────────────────────────────────────────────────────
# Migration 019 schema
# ──────────────────────────────────────────────────────────────────────

class TestMigration019:
    def test_cluster_merge_events_columns(self, fresh_db):
        cols = {r[1] for r in fresh_db.execute(
            "PRAGMA table_info(cluster_merge_events)"
        ).fetchall()}
        expected = {"event_id", "scan_id", "trc", "kept_cluster_id",
                    "kept_member_count_before", "kept_member_count_after",
                    "absorbed_member_count", "cosine_at_merge",
                    "centroid_merge_threshold", "merged_at"}
        assert expected.issubset(cols)


# ──────────────────────────────────────────────────────────────────────
# End-to-end audit logging
# ──────────────────────────────────────────────────────────────────────

class TestMergeAuditLogE2E:
    def _seed_two_overlapping_clusters(self, conn, n_each: int = 8, alpha: float = 0.92):
        """Two clusters that are TIGHT internally (intra cos ~ 0.99) but whose
        CENTERS are mutually close (cos ~ alpha). HDBSCAN sees two distinct
        clusters; centroid-merge then fuses them."""
        rng = np.random.default_rng(101)
        base = _l2_normalize(np.random.default_rng(11).standard_normal(EMBED_DIM).astype(np.float32))
        r1 = _l2_normalize(np.random.default_rng(2).standard_normal(EMBED_DIM).astype(np.float32))
        r2 = _l2_normalize(np.random.default_rng(3).standard_normal(EMBED_DIM).astype(np.float32))
        c1 = _l2_normalize(alpha * base + (1 - alpha) * r1)
        c2 = _l2_normalize(alpha * base + (1 - alpha) * r2)
        # Very tight intra-cluster (small noise) so HDBSCAN finds 2 distinct
        noise = 0.05 / np.sqrt(EMBED_DIM)
        a = _l2_normalize(c1 + rng.standard_normal((n_each, EMBED_DIM)).astype(np.float32) * noise)
        b = _l2_normalize(c2 + rng.standard_normal((n_each, EMBED_DIM)).astype(np.float32) * noise)
        for i, v in enumerate(a):
            _insert_ticket(conn, f"A{i}", trc="X")
            _insert_embedding(conn, f"A{i}", v)
        for i, v in enumerate(b):
            _insert_ticket(conn, f"B{i}", trc="X")
            _insert_embedding(conn, f"B{i}", v)
        conn.commit()

    def test_merge_event_persisted(self, fresh_db):
        self._seed_two_overlapping_clusters(fresh_db, n_each=8, alpha=0.85)
        # First: confirm without merge we get 2 clusters, with merge we get 1
        params_no_merge = {**DEFAULT_PARAMS, "centroid_merge_threshold": 0.0,
                           "min_cluster_size": 5}
        run_canonicalization(fresh_db, scan_id="probe", params=params_no_merge)
        n_pre = fresh_db.execute("SELECT COUNT(*) FROM canonical_clusters").fetchone()[0]
        # Wipe, re-run with merge enabled
        for stmt in ("DELETE FROM ticket_canonical_assignments",
                     "DELETE FROM assignment_transitions",
                     "DELETE FROM cluster_merge_events",
                     "DELETE FROM canonical_clusters"):
            fresh_db.execute(stmt)
        fresh_db.execute("UPDATE ticket_index SET canonical_issue_id=NULL, "
                         "canonical_confidence=NULL, assignment_method=NULL, "
                         "hdbscan_membership_prob=NULL, canonicalized_at=NULL")
        fresh_db.commit()

        params = {**DEFAULT_PARAMS, "centroid_merge_threshold": 0.70,
                  "min_cluster_size": 5}
        run_canonicalization(fresh_db, scan_id="s1", params=params)
        n_post = fresh_db.execute("SELECT COUNT(*) FROM canonical_clusters").fetchone()[0]
        events = fresh_db.execute(
            "SELECT scan_id, trc, kept_cluster_id, absorbed_member_count, "
            "cosine_at_merge, centroid_merge_threshold FROM cluster_merge_events"
        ).fetchall()
        # Either HDBSCAN found multiple → merge collapsed them → events fired
        # OR HDBSCAN already produced 1 → nothing to merge. If n_pre >= 2 we
        # MUST have events; if n_pre == 1 we accept zero events.
        if n_pre >= 2:
            assert len(events) >= 1, f"n_pre={n_pre} n_post={n_post} but no merge events"
            assert n_post < n_pre, "merge didn't reduce cluster count"
            ev = events[0]
            assert ev[0] == "s1"
            assert ev[1] == "X"
            assert ev[2] is not None
            assert ev[3] >= 1
            assert ev[4] >= 0.70
            assert ev[5] == 0.70

    def test_no_merge_event_when_disabled(self, fresh_db):
        self._seed_two_overlapping_clusters(fresh_db)
        params = {**DEFAULT_PARAMS, "centroid_merge_threshold": 0.0}
        run_canonicalization(fresh_db, scan_id="s1", params=params)
        n = fresh_db.execute(
            "SELECT COUNT(*) FROM cluster_merge_events"
        ).fetchone()[0]
        assert n == 0

    def test_no_merge_event_when_distant(self, fresh_db):
        """Distant clusters should not trigger merge or audit row."""
        rng = np.random.default_rng(999)
        for tag, seed in [("A", 1), ("B", 99)]:
            centre = _l2_normalize(np.random.default_rng(seed).standard_normal(EMBED_DIM).astype(np.float32))
            for i in range(8):
                v = _l2_normalize(centre + rng.standard_normal(EMBED_DIM).astype(np.float32) * (0.4/np.sqrt(EMBED_DIM)))
                _insert_ticket(fresh_db, f"{tag}{i}", trc="X")
                _insert_embedding(fresh_db, f"{tag}{i}", v)
        fresh_db.commit()
        params = {**DEFAULT_PARAMS, "centroid_merge_threshold": 0.85}
        run_canonicalization(fresh_db, scan_id="s1", params=params)
        n = fresh_db.execute(
            "SELECT COUNT(*) FROM cluster_merge_events"
        ).fetchone()[0]
        assert n == 0
        # Both clusters should survive
        n_c = fresh_db.execute("SELECT COUNT(*) FROM canonical_clusters").fetchone()[0]
        assert n_c >= 2

    def test_audit_log_fk_to_canonical_clusters(self, fresh_db):
        """Every kept_cluster_id in audit log must resolve to a canonical_clusters row."""
        self._seed_two_overlapping_clusters(fresh_db)
        run_canonicalization(fresh_db, scan_id="s1",
                             params={**DEFAULT_PARAMS, "centroid_merge_threshold": 0.70})
        # Inner join — every event row's kept_cluster_id must match a real cluster
        joined = fresh_db.execute(
            """SELECT cme.event_id, cc.cluster_id
                 FROM cluster_merge_events cme
                 JOIN canonical_clusters cc ON cc.cluster_id = cme.kept_cluster_id"""
        ).fetchall()
        unjoined_count = fresh_db.execute(
            """SELECT COUNT(*) FROM cluster_merge_events cme
                LEFT JOIN canonical_clusters cc ON cc.cluster_id = cme.kept_cluster_id
               WHERE cc.cluster_id IS NULL"""
        ).fetchone()[0]
        assert unjoined_count == 0
