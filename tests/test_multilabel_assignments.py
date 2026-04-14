"""Tests for Phase 4 multi-label tiered assignments."""

from __future__ import annotations

import uuid
from pathlib import Path

import numpy as np
import pytest

from src.data.canonicalization_engine import (
    DEFAULT_PARAMS,
    _compute_multilabel_ranks,
    _l2_normalize,
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
        (ticket_id, trc, "2026-04-14T00:00:00", "2026-04-14T00:00:00"),
    )
    conn.execute(
        """INSERT INTO nlp_ticket_classifications
             (classification_id, batch_id, ticket_id, scan_id, trc,
              sub_cluster, sub_cluster_confidence, created_at)
           VALUES (?, 'b1', ?, 'scan1', ?, 'x', 0.85, ?)""",
        (uuid.uuid4().hex, ticket_id, trc, "2026-04-14T00:00:00"),
    )


def _insert_embedding(conn, ticket_id: str, vec: np.ndarray) -> None:
    v = _l2_normalize(vec.astype(np.float32))
    conn.execute(
        """INSERT OR REPLACE INTO ticket_embeddings
             (ticket_id, embedding_blob, source_text_hash, model_name,
              dim_size, created_at)
           VALUES (?, ?, ?, 'Qwen3-Embedding-0.6B', ?, '2026-04-14T00:00:00')""",
        (ticket_id, _vec_to_blob(v), f"h_{ticket_id}", len(v)),
    )


def _make_cluster_vectors(rng, seed, n, dim=EMBED_DIM, noise_std=None):
    centre_rng = np.random.default_rng(seed)
    centre = _l2_normalize(centre_rng.standard_normal(dim).astype(np.float32))
    if noise_std is None:
        noise_std = 0.4 / np.sqrt(dim)
    deltas = rng.standard_normal((n, dim)).astype(np.float32) * noise_std
    return _l2_normalize(centre + deltas)


# ──────────────────────────────────────────────────────────────────────
# Pure-function rank/tier logic
# ──────────────────────────────────────────────────────────────────────

class TestComputeMultilabelRanks:
    def test_empty_returns_empty(self):
        emb = np.array([1.0, 0.0], dtype=np.float32)
        centroids = np.empty((0, 2), dtype=np.float32)
        out = _compute_multilabel_ranks(emb, [], centroids, [], "X", DEFAULT_PARAMS)
        assert out == []

    def test_ranks_ordered_by_similarity(self):
        emb = _l2_normalize(np.array([1.0, 0.0], dtype=np.float32))
        centroids = _l2_normalize(np.array([
            [0.99, 0.14],  # closest to [1,0] — cos ~ 0.99
            [0.85, 0.53],  # cos ~ 0.85
            [0.70, 0.71],  # cos ~ 0.70
        ], dtype=np.float32))
        out = _compute_multilabel_ranks(
            emb, ["c1", "c2", "c3"], centroids,
            all_cluster_trcs=["X", "X", "X"], ticket_trc="X",
            params=DEFAULT_PARAMS,
        )
        # c1 rank 1 (0.99 > 0.75), c2 rank 2 (0.85 > 0.65), c3 rank 3 (0.70 > 0.55)
        assert [r[1] for r in out] == [1, 2, 3]
        assert [r[0] for r in out] == ["c1", "c2", "c3"]
        assert [r[3] for r in out] == ["primary", "secondary", "tertiary"]

    def test_below_primary_threshold_no_rank1(self):
        emb = _l2_normalize(np.array([1.0, 0.0], dtype=np.float32))
        centroids = _l2_normalize(np.array([[0.7, 0.71]], dtype=np.float32))
        out = _compute_multilabel_ranks(
            emb, ["c1"], centroids, ["X"], "X", DEFAULT_PARAMS,
        )
        # cos ~ 0.70 < 0.75 primary threshold but no rank-2 candidate either
        assert out == []

    def test_below_all_thresholds_returns_empty(self):
        emb = _l2_normalize(np.array([1.0, 0.0], dtype=np.float32))
        centroids = _l2_normalize(np.array([[0.0, 1.0]], dtype=np.float32))
        out = _compute_multilabel_ranks(
            emb, ["c1"], centroids, ["X"], "X", DEFAULT_PARAMS,
        )
        assert out == []

    def test_same_trc_preferred(self):
        # Build a case where cross-TRC cluster has higher raw cosine,
        # but same-TRC cluster still ranks first.
        emb = _l2_normalize(np.array([1.0, 0.0], dtype=np.float32))
        centroids = _l2_normalize(np.array([
            [0.85, 0.53],  # cross-TRC, cos ~ 0.85
            [0.80, 0.60],  # same-TRC, cos ~ 0.80
        ], dtype=np.float32))
        out = _compute_multilabel_ranks(
            emb, ["cross", "same"], centroids,
            all_cluster_trcs=["OTHER", "BILLING"], ticket_trc="BILLING",
            params=DEFAULT_PARAMS,
        )
        # Same-TRC partition scanned first — 'same' should be rank 1
        # even though 'cross' has slightly higher raw cosine.
        assert out[0][0] == "same"
        # Cross-TRC fills lower ranks if threshold met
        assert any(r[0] == "cross" for r in out)

    def test_no_duplicate_clusters_across_ranks(self):
        emb = _l2_normalize(np.array([1.0, 0.0], dtype=np.float32))
        centroids = _l2_normalize(np.array([
            [0.99, 0.14],
            [0.99, 0.14],  # duplicate-positioned but different id
        ], dtype=np.float32))
        out = _compute_multilabel_ranks(
            emb, ["c1", "c2"], centroids, ["X", "X"], "X", DEFAULT_PARAMS,
        )
        assert len(set(r[0] for r in out)) == len(out)


# ──────────────────────────────────────────────────────────────────────
# Migration + schema
# ──────────────────────────────────────────────────────────────────────

class TestMigration018:
    def test_ticket_canonical_assignments_columns(self, fresh_db):
        cols = {r[1] for r in fresh_db.execute(
            "PRAGMA table_info(ticket_canonical_assignments)"
        ).fetchall()}
        expected = {"ticket_id", "cluster_id", "rank", "cosine_similarity",
                    "assignment_tier", "assignment_method",
                    "assigned_in_scan_id", "assigned_at"}
        assert expected.issubset(cols)

    def test_assignment_transitions_columns(self, fresh_db):
        cols = {r[1] for r in fresh_db.execute(
            "PRAGMA table_info(assignment_transitions)"
        ).fetchall()}
        expected = {"transition_id", "ticket_id", "scan_id",
                    "old_primary_cluster_id", "new_primary_cluster_id",
                    "old_cosine", "new_cosine", "transition_reason"}
        assert expected.issubset(cols)


# ──────────────────────────────────────────────────────────────────────
# End-to-end via run_canonicalization
# ──────────────────────────────────────────────────────────────────────

class TestMultilabelE2E:
    def test_clustered_ticket_writes_primary_assignment(self, fresh_db):
        rng = np.random.default_rng(7)
        for i, v in enumerate(_make_cluster_vectors(rng, 1, 10)):
            _insert_ticket(fresh_db, f"T{i}")
            _insert_embedding(fresh_db, f"T{i}", v)
        fresh_db.commit()

        run_canonicalization(fresh_db, scan_id="s1")
        rows = fresh_db.execute(
            """SELECT ticket_id, cluster_id, rank, assignment_tier
                 FROM ticket_canonical_assignments
                WHERE assignment_tier = 'primary'"""
        ).fetchall()
        assert len(rows) >= 5  # at least half of 10 should have a primary

    def test_primary_matches_ticket_index(self, fresh_db):
        """ticket_index.canonical_issue_id must equal rank-1 assignment."""
        rng = np.random.default_rng(7)
        for i, v in enumerate(_make_cluster_vectors(rng, 1, 10)):
            _insert_ticket(fresh_db, f"T{i}")
            _insert_embedding(fresh_db, f"T{i}", v)
        fresh_db.commit()

        run_canonicalization(fresh_db, scan_id="s1")
        rows = fresh_db.execute(
            """SELECT ti.ticket_id, ti.canonical_issue_id,
                      (SELECT cluster_id FROM ticket_canonical_assignments
                        WHERE ticket_id = ti.ticket_id AND rank = 1) AS primary_cid
                 FROM ticket_index ti
                WHERE ti.canonical_issue_id IS NOT NULL"""
        ).fetchall()
        assert len(rows) >= 5
        for tid, idx_cid, prim in rows:
            assert idx_cid == prim, f"ticket {tid}: index={idx_cid} primary={prim}"

    def test_multiple_clusters_produce_secondary_assignments(self, fresh_db):
        """With two clusters in different TRCs that share a base direction,
        tickets in TRC A can rank cross-TRC secondary against TRC B.

        HDBSCAN runs per-TRC, so the two clusters stay separate even though
        their centroids are close. Multi-label scorer then ranks a TRC-A
        ticket against the TRC-B centroid for rank 2.
        """
        rng = np.random.default_rng(11)
        base = _l2_normalize(np.random.default_rng(100).standard_normal(EMBED_DIM).astype(np.float32))
        r1 = _l2_normalize(np.random.default_rng(1).standard_normal(EMBED_DIM).astype(np.float32))
        r2 = _l2_normalize(np.random.default_rng(2).standard_normal(EMBED_DIM).astype(np.float32))
        c1 = _l2_normalize(0.7 * base + 0.3 * r1)
        c2 = _l2_normalize(0.7 * base + 0.3 * r2)
        noise_std = 0.4 / np.sqrt(EMBED_DIM)
        cluster_a = _l2_normalize(c1 + rng.standard_normal((10, EMBED_DIM)).astype(np.float32) * noise_std)
        cluster_b = _l2_normalize(c2 + rng.standard_normal((10, EMBED_DIM)).astype(np.float32) * noise_std)
        for i, v in enumerate(cluster_a):
            _insert_ticket(fresh_db, f"A{i}", trc="BILLING")
            _insert_embedding(fresh_db, f"A{i}", v)
        for i, v in enumerate(cluster_b):
            _insert_ticket(fresh_db, f"B{i}", trc="AUTH")
            _insert_embedding(fresh_db, f"B{i}", v)
        fresh_db.commit()

        # Relaxed secondary threshold so cross-TRC partial-overlap triggers rank-2
        params = {
            **DEFAULT_PARAMS,
            "multilabel_secondary_threshold": 0.50,
            "multilabel_tertiary_threshold": 0.30,
        }
        run_canonicalization(fresh_db, scan_id="s1", params=params)
        counts = fresh_db.execute(
            """SELECT COUNT(*) FROM (
                   SELECT ticket_id, COUNT(*) AS c
                     FROM ticket_canonical_assignments
                    GROUP BY ticket_id
                   HAVING c >= 2)"""
        ).fetchone()[0]
        assert counts >= 1

    def test_transitions_log_new_ticket_reason(self, fresh_db):
        rng = np.random.default_rng(13)
        for i, v in enumerate(_make_cluster_vectors(rng, 1, 10)):
            _insert_ticket(fresh_db, f"T{i}")
            _insert_embedding(fresh_db, f"T{i}", v)
        fresh_db.commit()

        run_canonicalization(fresh_db, scan_id="s1")
        reasons = [r[0] for r in fresh_db.execute(
            "SELECT transition_reason FROM assignment_transitions"
        ).fetchall()]
        assert "new_ticket" in reasons

    def test_rerun_no_spurious_transitions(self, fresh_db):
        """Re-running with identical data should not create new transition rows
        (primary cluster assignment stays the same)."""
        rng = np.random.default_rng(17)
        for i, v in enumerate(_make_cluster_vectors(rng, 1, 10)):
            _insert_ticket(fresh_db, f"T{i}")
            _insert_embedding(fresh_db, f"T{i}", v)
        fresh_db.commit()

        run_canonicalization(fresh_db, scan_id="s1")
        count_1 = fresh_db.execute(
            "SELECT COUNT(*) FROM assignment_transitions"
        ).fetchone()[0]

        # Second run — same data, primaries should not shift (snap_existing)
        run_canonicalization(fresh_db, scan_id="s2")
        count_2 = fresh_db.execute(
            "SELECT COUNT(*) FROM assignment_transitions"
        ).fetchone()[0]
        # No new 'centroid_shift' transitions; count should equal count_1
        assert count_2 == count_1

    def test_deletion_semantics_replaces_prior_assignments(self, fresh_db):
        """Re-canonicalization should replace, not duplicate, assignments."""
        rng = np.random.default_rng(19)
        for i, v in enumerate(_make_cluster_vectors(rng, 1, 8)):
            _insert_ticket(fresh_db, f"T{i}")
            _insert_embedding(fresh_db, f"T{i}", v)
        fresh_db.commit()

        run_canonicalization(fresh_db, scan_id="s1")
        a_count = fresh_db.execute(
            "SELECT COUNT(*) FROM ticket_canonical_assignments"
        ).fetchone()[0]

        run_canonicalization(fresh_db, scan_id="s2")
        b_count = fresh_db.execute(
            "SELECT COUNT(*) FROM ticket_canonical_assignments"
        ).fetchone()[0]
        # Should be same or smaller (same data → same/fewer assignments),
        # never cumulatively larger.
        assert b_count <= a_count + 2  # +2 grace for ranking jitter

    def test_tier_gating_with_strict_thresholds(self, fresh_db):
        """With very strict thresholds, only tightly-matched primaries land."""
        rng = np.random.default_rng(23)
        for i, v in enumerate(_make_cluster_vectors(rng, 1, 10)):
            _insert_ticket(fresh_db, f"T{i}")
            _insert_embedding(fresh_db, f"T{i}", v)
        fresh_db.commit()

        params = {
            **DEFAULT_PARAMS,
            "multilabel_primary_threshold": 0.99,  # very strict
            "multilabel_secondary_threshold": 0.99,
            "multilabel_tertiary_threshold": 0.99,
        }
        run_canonicalization(fresh_db, scan_id="s1", params=params)
        # With threshold 0.99, multi-label rows for non-engine-primaries
        # will be sparse. Engine primary always wins rank 1 though (promoted
        # via the override branch).
        rows = fresh_db.execute(
            "SELECT COUNT(*) FROM ticket_canonical_assignments WHERE rank = 1"
        ).fetchone()[0]
        # Engine gave primaries → should still have rank-1 rows for those
        assert rows >= 1

    def test_multilabel_disabled_skips_writes(self, fresh_db):
        rng = np.random.default_rng(29)
        for i, v in enumerate(_make_cluster_vectors(rng, 1, 10)):
            _insert_ticket(fresh_db, f"T{i}")
            _insert_embedding(fresh_db, f"T{i}", v)
        fresh_db.commit()

        params = {**DEFAULT_PARAMS, "multilabel_enabled": False}
        run_canonicalization(fresh_db, scan_id="s1", params=params)
        rows = fresh_db.execute(
            "SELECT COUNT(*) FROM ticket_canonical_assignments"
        ).fetchone()[0]
        assert rows == 0
