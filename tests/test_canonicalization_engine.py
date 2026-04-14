"""Tests for src/data/canonicalization_engine.py (Phase 3)."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import numpy as np
import pytest

from src.data.canonicalization_engine import (
    DEFAULT_PARAMS,
    _blob_to_vec,
    _classify_method,
    _compute_confidence_weighted_centroid,
    _compute_medoid,
    _hdbscan_per_trc,
    _knn_assign_noise,
    _l2_normalize,
    _load_existing_centroids,
    _match_to_existing_centroids,
    _new_cluster_id,
    _slugify_trc,
    _vec_to_blob,
    record_tuning_run,
    run_canonicalization,
    score_golden_set,
)
from src.data.db_manager import DatabaseManager


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────

EMBED_DIM = 1024


@pytest.fixture
def fresh_db(tmp_path: Path):
    """Fully-initialized DB with migrations 001..017 applied.

    FKs disabled for test setup convenience — we fabricate
    nlp_ticket_classifications rows without populating parent batch/scan_run
    tables. Engine code queries these by sub-select only (no FK traversal).
    """
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


def _insert_ticket(conn, ticket_id: str, trc: str = "BILLING",
                    confidence: float | None = 0.85) -> None:
    """Insert a minimal ticket_index + optional classification row."""
    now = "2026-04-14T00:00:00"
    conn.execute(
        """INSERT INTO ticket_index
             (ticket_id, trc_code, first_seen_scan_id, last_seen_scan_id,
              first_seen_date, ticket_created_date)
           VALUES (?, ?, 'ingestion', 'scan1', ?, ?)""",
        (ticket_id, trc, now, now),
    )
    if confidence is not None:
        conn.execute(
            """INSERT INTO nlp_ticket_classifications
                 (classification_id, batch_id, ticket_id, scan_id, trc,
                  sub_cluster, sub_cluster_confidence, created_at)
               VALUES (?, 'b1', ?, 'scan1', ?, 'x', ?, ?)""",
            (uuid.uuid4().hex, ticket_id, trc, confidence, now),
        )


def _insert_embedding(conn, ticket_id: str, vec: np.ndarray) -> None:
    v = _l2_normalize(vec.astype(np.float32))
    conn.execute(
        """INSERT OR REPLACE INTO ticket_embeddings
             (ticket_id, embedding_blob, source_text_hash, model_name,
              dim_size, created_at)
           VALUES (?, ?, ?, 'Qwen3-Embedding-0.6B', ?, '2026-04-14T00:00:00')""",
        (ticket_id, _vec_to_blob(v), f"hash_{ticket_id}", len(v)),
    )


def _make_cluster_vectors(
    rng: np.random.Generator, center_seed: int, n: int,
    noise_std: float | None = None, dim: int = EMBED_DIM,
) -> np.ndarray:
    """Generate N vectors tightly grouped around a fixed random center.

    Noise is scaled by 1/sqrt(dim) so the expected within-cluster cosine
    stays around 0.90 regardless of dimensionality — matches real Qwen3
    embedding behavior where cluster members sit near cos~0.85-0.95.
    """
    centre_rng = np.random.default_rng(center_seed)
    centre = centre_rng.standard_normal(dim).astype(np.float32)
    centre = _l2_normalize(centre)
    if noise_std is None:
        noise_std = 0.4 / np.sqrt(dim)  # tuned for intra-cluster cos ~ 0.92
    deltas = rng.standard_normal((n, dim)).astype(np.float32) * noise_std
    return _l2_normalize(centre + deltas)


# ──────────────────────────────────────────────────────────────────────
# Pure-function helpers
# ──────────────────────────────────────────────────────────────────────

class TestHelpers:
    def test_l2_normalize_1d(self):
        v = np.array([3.0, 4.0], dtype=np.float32)
        n = _l2_normalize(v)
        assert abs(float(np.linalg.norm(n)) - 1.0) < 1e-5

    def test_l2_normalize_2d_rowwise(self):
        m = np.array([[3.0, 4.0], [1.0, 0.0], [0.0, 0.0]], dtype=np.float32)
        n = _l2_normalize(m)
        assert abs(float(np.linalg.norm(n[0])) - 1.0) < 1e-5
        assert abs(float(np.linalg.norm(n[1])) - 1.0) < 1e-5
        # zero row stays zero
        assert np.allclose(n[2], 0.0)

    def test_slugify_trc(self):
        assert _slugify_trc("BILLING / AUTOPAY") == "billing---autopay"
        assert _slugify_trc("") == "unknown"
        assert _slugify_trc(None) == "unknown"

    def test_new_cluster_id_unique_and_prefixed(self):
        a = _new_cluster_id("BILLING")
        b = _new_cluster_id("BILLING")
        assert a != b
        assert a.startswith("billing-")
        assert len(a.split("-")[-1]) == 12  # 12-hex uuid

    def test_blob_roundtrip(self):
        v = np.array([0.1, -0.2, 0.3], dtype=np.float32)
        blob = _vec_to_blob(v)
        back = _blob_to_vec(blob, dim=3)
        assert np.allclose(back, v)

    def test_blob_dim_mismatch_raises(self):
        with pytest.raises(ValueError):
            _blob_to_vec(_vec_to_blob(np.zeros(5, dtype=np.float32)), dim=3)

    def test_compute_medoid(self):
        centre = np.array([1.0, 0.0], dtype=np.float32)
        embeds = _l2_normalize(np.array([
            [1.0, 0.0],          # exact match — should be medoid
            [0.9, 0.1],
            [0.0, 1.0],
        ], dtype=np.float32))
        assert _compute_medoid(embeds, centre) == 0

    def test_confidence_weighted_centroid(self):
        e = _l2_normalize(np.array([
            [1.0, 0.0],
            [0.0, 1.0],
        ], dtype=np.float32))
        # Equal weights → mean direction (0.707, 0.707)
        c = _compute_confidence_weighted_centroid(e, np.array([0.5, 0.5]))
        assert abs(c[0] - c[1]) < 1e-3
        # Weight heavily toward first — centroid biases toward [1,0]
        c2 = _compute_confidence_weighted_centroid(e, np.array([0.9, 0.1]))
        assert c2[0] > c2[1]
        # All-zero weights fall back to uniform (no NaN)
        c3 = _compute_confidence_weighted_centroid(e, np.array([0.0, 0.0]))
        assert not np.isnan(c3).any()

    def test_confidence_weighted_centroid_l2_normalized(self):
        rng = np.random.default_rng(42)
        e = _l2_normalize(rng.standard_normal((10, 50)).astype(np.float32))
        c = _compute_confidence_weighted_centroid(e, np.ones(10))
        assert abs(float(np.linalg.norm(c)) - 1.0) < 1e-5

    def test_classify_method(self):
        p = DEFAULT_PARAMS
        assert _classify_method(0.9, p) == "hdbscan_core"
        assert _classify_method(0.5, p) == "hdbscan_border"
        assert _classify_method(0.1, p) == "hdbscan_border"


# ──────────────────────────────────────────────────────────────────────
# Matching / KNN
# ──────────────────────────────────────────────────────────────────────

class TestMatching:
    def test_match_snaps_above_threshold(self):
        e = _l2_normalize(np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32))
        centroids = _l2_normalize(np.array([[0.99, 0.14]], dtype=np.float32))
        out = _match_to_existing_centroids(e, centroids, ["c1"], threshold=0.75)
        # Row 0 cos≈0.99 > 0.75 → matched; row 1 cos≈0.14 → not matched
        assert 0 in out
        assert out[0][0] == "c1"
        assert 1 not in out

    def test_match_empty_inputs(self):
        e = np.empty((0, 3), dtype=np.float32)
        assert _match_to_existing_centroids(e, e, [], 0.5) == {}

    def test_knn_assigns_or_returns_none(self):
        noise = _l2_normalize(np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32))
        centroids = _l2_normalize(np.array([[0.95, 0.3]], dtype=np.float32))
        res = _knn_assign_noise(noise, centroids, ["c1"], threshold=0.75)
        assert res[0] is not None and res[0][0] == "c1"
        assert res[1] is None  # 0.3 cosine < 0.75


# ──────────────────────────────────────────────────────────────────────
# HDBSCAN
# ──────────────────────────────────────────────────────────────────────

class TestHdbscan:
    def test_hdbscan_empty_returns_empty(self):
        labels, probs = _hdbscan_per_trc(np.empty((0, 5), dtype=np.float32), DEFAULT_PARAMS)
        assert labels.size == 0 and probs.size == 0

    def test_hdbscan_too_few_all_noise(self):
        # 3 points with min_cluster_size=5 → all noise (-1)
        e = _l2_normalize(np.random.default_rng(1).standard_normal((3, 32)).astype(np.float32))
        labels, probs = _hdbscan_per_trc(e, DEFAULT_PARAMS)
        assert (labels == -1).all()

    def test_hdbscan_separates_two_clusters(self):
        rng = np.random.default_rng(7)
        a = _make_cluster_vectors(rng, center_seed=1, n=20, dim=64)
        b = _make_cluster_vectors(rng, center_seed=99, n=20, dim=64)
        data = np.vstack([a, b]).astype(np.float32)
        labels, _ = _hdbscan_per_trc(data, {**DEFAULT_PARAMS, "min_cluster_size": 5})
        # Expect ≥ 2 distinct cluster labels (ignoring noise)
        non_noise = labels[labels >= 0]
        assert len(set(non_noise.tolist())) >= 2


# ──────────────────────────────────────────────────────────────────────
# End-to-end integration with DB
# ──────────────────────────────────────────────────────────────────────

class TestRunCanonicalizationE2E:
    def test_empty_db_returns_zero_result(self, fresh_db):
        res = run_canonicalization(fresh_db, scan_id="scan1")
        assert res.tickets_processed == 0
        assert res.tickets_assigned == 0
        assert res.method_counts == {}

    def test_two_cluster_scenario_assigns_and_persists(self, fresh_db):
        rng = np.random.default_rng(2026)
        a_vecs = _make_cluster_vectors(rng, center_seed=11, n=15, dim=EMBED_DIM)
        b_vecs = _make_cluster_vectors(rng, center_seed=77, n=12, dim=EMBED_DIM)

        for i, v in enumerate(a_vecs):
            tid = f"A{i}"
            _insert_ticket(fresh_db, tid, trc="BILLING")
            _insert_embedding(fresh_db, tid, v)
        for i, v in enumerate(b_vecs):
            tid = f"B{i}"
            _insert_ticket(fresh_db, tid, trc="BILLING")
            _insert_embedding(fresh_db, tid, v)
        fresh_db.commit()

        res = run_canonicalization(fresh_db, scan_id="scan1",
                                    params={**DEFAULT_PARAMS, "min_cluster_size": 5})
        assert res.tickets_processed == 27
        # At least two clusters discovered
        assert len(res.clusters) >= 2
        # Most tickets assigned
        assert res.tickets_assigned >= 20

        # Verify ticket_index was written
        rows = fresh_db.execute(
            "SELECT COUNT(*) FROM ticket_index WHERE canonical_issue_id IS NOT NULL"
        ).fetchone()
        assert rows[0] >= 20

        # Every assigned ticket has a method from the enum
        methods = set(r[0] for r in fresh_db.execute(
            "SELECT DISTINCT assignment_method FROM ticket_index "
            "WHERE canonical_issue_id IS NOT NULL"
        ).fetchall())
        allowed = {"hdbscan_core", "hdbscan_border", "knn_fallback", "snapped_existing"}
        assert methods.issubset(allowed)

        # canonical_clusters table got rows with member_count > 0
        ccs = fresh_db.execute(
            "SELECT cluster_id, member_count, trc FROM canonical_clusters"
        ).fetchall()
        assert len(ccs) >= 2
        assert all(m > 0 for _, m, _ in ccs)
        assert all(t == "BILLING" for _, _, t in ccs)

    def test_snap_to_existing_on_rerun(self, fresh_db):
        """Second run with same data should snap to existing centroids (no duplicate clusters)."""
        rng = np.random.default_rng(5)
        vecs = _make_cluster_vectors(rng, center_seed=42, n=10, dim=EMBED_DIM)
        for i, v in enumerate(vecs):
            tid = f"T{i}"
            _insert_ticket(fresh_db, tid, trc="AUTH")
            _insert_embedding(fresh_db, tid, v)
        fresh_db.commit()

        run_canonicalization(fresh_db, scan_id="scan1")
        first_count = fresh_db.execute(
            "SELECT COUNT(*) FROM canonical_clusters"
        ).fetchone()[0]

        # Re-run — same data should not mint new clusters
        res2 = run_canonicalization(fresh_db, scan_id="scan2")
        second_count = fresh_db.execute(
            "SELECT COUNT(*) FROM canonical_clusters"
        ).fetchone()[0]
        assert second_count == first_count
        # All tickets should have snapped
        snapped = res2.method_counts.get("snapped_existing", 0)
        assert snapped == res2.tickets_processed

    def test_trc_scoping(self, fresh_db):
        """TRC A and TRC B tickets cluster independently per-TRC."""
        rng = np.random.default_rng(11)
        for i, v in enumerate(_make_cluster_vectors(rng, 1, 8, dim=EMBED_DIM)):
            _insert_ticket(fresh_db, f"A{i}", trc="BILLING")
            _insert_embedding(fresh_db, f"A{i}", v)
        for i, v in enumerate(_make_cluster_vectors(rng, 2, 8, dim=EMBED_DIM)):
            _insert_ticket(fresh_db, f"B{i}", trc="SCHEDULING")
            _insert_embedding(fresh_db, f"B{i}", v)
        fresh_db.commit()

        res = run_canonicalization(fresh_db, scan_id="scan1",
                                    params={**DEFAULT_PARAMS, "min_cluster_size": 5})
        trcs_in_clusters = {c.trc for c in res.clusters}
        assert trcs_in_clusters == {"BILLING", "SCHEDULING"}

    def test_trc_filter_scopes_processing(self, fresh_db):
        rng = np.random.default_rng(3)
        for i, v in enumerate(_make_cluster_vectors(rng, 1, 8, dim=EMBED_DIM)):
            _insert_ticket(fresh_db, f"A{i}", trc="BILLING")
            _insert_embedding(fresh_db, f"A{i}", v)
        for i, v in enumerate(_make_cluster_vectors(rng, 2, 8, dim=EMBED_DIM)):
            _insert_ticket(fresh_db, f"B{i}", trc="SCHEDULING")
            _insert_embedding(fresh_db, f"B{i}", v)
        fresh_db.commit()

        res = run_canonicalization(fresh_db, scan_id="s1", trc="BILLING",
                                    params={**DEFAULT_PARAMS, "min_cluster_size": 5})
        assert res.tickets_processed == 8
        # Only BILLING tickets assigned
        rows = fresh_db.execute(
            "SELECT trc_code FROM ticket_index WHERE canonical_issue_id IS NOT NULL"
        ).fetchall()
        assert all(r[0] == "BILLING" for r in rows)

    def test_force_recluster_ignores_existing(self, fresh_db):
        """force_recluster=True should skip stage-1 snapping."""
        rng = np.random.default_rng(13)
        vecs = _make_cluster_vectors(rng, center_seed=9, n=10, dim=EMBED_DIM)
        for i, v in enumerate(vecs):
            _insert_ticket(fresh_db, f"T{i}", trc="X")
            _insert_embedding(fresh_db, f"T{i}", v)
        fresh_db.commit()

        run_canonicalization(fresh_db, scan_id="scan1")
        # Second run forced — no snapped_existing because stage 1 is bypassed
        res = run_canonicalization(fresh_db, scan_id="scan2", force_recluster=True)
        assert res.method_counts.get("snapped_existing", 0) == 0

    def test_tickets_without_embeddings_skipped(self, fresh_db):
        """Tickets in ticket_index but not in ticket_embeddings are ignored."""
        rng = np.random.default_rng(21)
        vecs = _make_cluster_vectors(rng, center_seed=5, n=8, dim=EMBED_DIM)
        for i, v in enumerate(vecs):
            _insert_ticket(fresh_db, f"T{i}", trc="X")
            _insert_embedding(fresh_db, f"T{i}", v)
        # Ticket with no embedding
        _insert_ticket(fresh_db, "ORPHAN", trc="X")
        fresh_db.commit()

        res = run_canonicalization(fresh_db, scan_id="scan1")
        assert res.tickets_processed == 8  # orphan excluded by inner JOIN

    def test_knn_fallback_path(self, fresh_db):
        """A lone ticket close to an existing cluster should snap via KNN."""
        rng = np.random.default_rng(31)
        # Create a tight cluster of 8
        cluster = _make_cluster_vectors(rng, center_seed=88, n=8, dim=EMBED_DIM)
        for i, v in enumerate(cluster):
            _insert_ticket(fresh_db, f"C{i}", trc="X")
            _insert_embedding(fresh_db, f"C{i}", v)
        fresh_db.commit()
        run_canonicalization(fresh_db, scan_id="s1")

        # Add one new ticket close-ish to the cluster centroid but with too
        # few companions to form its own cluster; it should snap via stage 1
        # or KNN fallback. Use a slightly-perturbed cluster vector.
        new_vec = _l2_normalize(cluster[0] + 0.01 * rng.standard_normal(EMBED_DIM).astype(np.float32))
        _insert_ticket(fresh_db, "NEW1", trc="X")
        _insert_embedding(fresh_db, "NEW1", new_vec)
        fresh_db.commit()

        res = run_canonicalization(fresh_db, scan_id="s2")
        # NEW1 should be assigned via snapped_existing (cos likely > 0.75)
        row = fresh_db.execute(
            "SELECT canonical_issue_id, assignment_method FROM ticket_index "
            "WHERE ticket_id = 'NEW1'"
        ).fetchone()
        assert row[0] is not None
        assert row[1] in {"snapped_existing", "knn_fallback", "hdbscan_core", "hdbscan_border"}

    def test_distant_noise_stays_unclustered(self, fresh_db):
        """Tickets far from any cluster and too few to cluster themselves → unclustered."""
        rng = np.random.default_rng(41)
        # 6 tight cluster tickets
        for i, v in enumerate(_make_cluster_vectors(rng, 1, 6, dim=EMBED_DIM)):
            _insert_ticket(fresh_db, f"C{i}", trc="X")
            _insert_embedding(fresh_db, f"C{i}", v)
        # 1 random vector far from everything
        lone = _l2_normalize(rng.standard_normal(EMBED_DIM).astype(np.float32))
        _insert_ticket(fresh_db, "LONE", trc="X")
        _insert_embedding(fresh_db, "LONE", lone)
        fresh_db.commit()

        res = run_canonicalization(fresh_db, scan_id="s1",
                                    params={**DEFAULT_PARAMS, "min_cluster_size": 5})
        methods = {r[0]: r[1] for r in fresh_db.execute(
            "SELECT ticket_id, assignment_method FROM ticket_index"
        ).fetchall()}
        assert methods.get("LONE") == "unclustered"

    def test_performance_500_tickets_under_10s(self, fresh_db):
        """500-ticket canonicalization should complete well under tuning-gate budget."""
        rng = np.random.default_rng(99)
        # 5 clusters × 100 points each
        idx = 0
        for seed in range(5):
            for v in _make_cluster_vectors(rng, center_seed=seed * 17, n=100, dim=EMBED_DIM):
                _insert_ticket(fresh_db, f"T{idx}", trc="PERF")
                _insert_embedding(fresh_db, f"T{idx}", v)
                idx += 1
        fresh_db.commit()

        import time
        t0 = time.perf_counter()
        res = run_canonicalization(fresh_db, scan_id="perf1",
                                    params={**DEFAULT_PARAMS, "min_cluster_size": 10})
        elapsed = time.perf_counter() - t0
        # Plan §3 gate: < 60s for 1788 tickets; 500 tickets should be much faster
        assert elapsed < 10.0, f"Too slow: {elapsed:.2f}s"
        assert res.tickets_processed == 500
        assert res.tickets_assigned >= 400  # most should cluster
        # Should produce approximately 5 clusters (± 2 for HDBSCAN variance)
        assert 3 <= len(res.clusters) <= 10, f"Unexpected cluster count: {len(res.clusters)}"

    def test_null_confidence_uses_default(self, fresh_db):
        """Tickets without classification rows still canonicalize (confidence=0.5)."""
        rng = np.random.default_rng(19)
        for i, v in enumerate(_make_cluster_vectors(rng, 1, 10, dim=EMBED_DIM)):
            _insert_ticket(fresh_db, f"T{i}", trc="X", confidence=None)
            _insert_embedding(fresh_db, f"T{i}", v)
        fresh_db.commit()
        res = run_canonicalization(fresh_db, scan_id="s1",
                                    params={**DEFAULT_PARAMS, "min_cluster_size": 5})
        assert res.tickets_processed == 10
        assert res.tickets_assigned >= 5  # some should cluster


# ──────────────────────────────────────────────────────────────────────
# Golden-set scoring
# ──────────────────────────────────────────────────────────────────────

class TestGoldenSetScoring:
    def test_all_correct(self, fresh_db):
        _insert_ticket(fresh_db, "a")
        _insert_ticket(fresh_db, "b")
        _insert_ticket(fresh_db, "c")
        fresh_db.execute("UPDATE ticket_index SET canonical_issue_id = 'C1' WHERE ticket_id IN ('a','b')")
        fresh_db.execute("UPDATE ticket_index SET canonical_issue_id = 'C2' WHERE ticket_id = 'c'")
        fresh_db.commit()
        scores = score_golden_set(fresh_db, [
            ("a", "b", True),   # TP
            ("a", "c", False),  # TN
        ])
        assert scores["precision"] == 1.0
        assert scores["recall"] == 1.0

    def test_wrong_split_hurts_recall(self, fresh_db):
        _insert_ticket(fresh_db, "a")
        _insert_ticket(fresh_db, "b")
        fresh_db.execute("UPDATE ticket_index SET canonical_issue_id = 'C1' WHERE ticket_id = 'a'")
        fresh_db.execute("UPDATE ticket_index SET canonical_issue_id = 'C2' WHERE ticket_id = 'b'")
        fresh_db.commit()
        # Golden says same — we split → FN → recall = 0
        scores = score_golden_set(fresh_db, [("a", "b", True)])
        assert scores["recall"] == 0.0
        assert scores["precision"] == 0.0  # tp/(tp+fp) = 0/0 defined as 0

    def test_unclustered_ticket_counts_as_negative(self, fresh_db):
        _insert_ticket(fresh_db, "a")
        _insert_ticket(fresh_db, "b")
        # Neither has canonical_issue_id
        fresh_db.commit()
        scores = score_golden_set(fresh_db, [
            ("a", "b", True),
            ("a", "b", False),
        ])
        # Both treated as not-same prediction
        assert scores["fn"] == 1
        assert scores["tn"] == 1


# ──────────────────────────────────────────────────────────────────────
# Tuning-run persistence
# ──────────────────────────────────────────────────────────────────────

class TestTuningRunPersistence:
    def test_record_tuning_run_roundtrip(self, fresh_db):
        run_id = record_tuning_run(
            fresh_db,
            params={"min_cluster_size": 5, "metric": "euclidean"},
            scores={
                "silhouette": 0.42, "davies_bouldin": 0.81,
                "precision": 0.85, "recall": 0.83, "f1": 0.84,
                "noise_pct": 0.12, "n_clusters": 18,
                "gemini_coherence": None,
            },
            scan_id="s1", trc=None, wall_time_ms=4200,
            notes="grid cell 3/45",
        )
        row = fresh_db.execute(
            "SELECT run_id, silhouette, golden_precision, n_clusters, wall_time_ms, notes "
            "FROM canonicalization_tuning_runs WHERE run_id = ?", (run_id,),
        ).fetchone()
        assert row is not None
        assert row[0] == run_id
        assert abs(row[1] - 0.42) < 1e-6
        assert abs(row[2] - 0.85) < 1e-6
        assert row[3] == 18
        assert row[4] == 4200
        assert row[5] == "grid cell 3/45"


# ──────────────────────────────────────────────────────────────────────
# Schema presence (migration 017)
# ──────────────────────────────────────────────────────────────────────

class TestMigration017:
    def test_canonical_clusters_columns(self, fresh_db):
        cols = [r[1] for r in fresh_db.execute("PRAGMA table_info(canonical_clusters)").fetchall()]
        expected = {"cluster_id", "trc", "canonical_label", "label_source", "centroid_blob",
                    "representative_ticket_id", "member_count", "tier",
                    "discovered_scan_id", "first_seen_at", "last_seen_scan_id",
                    "merged_into", "split_into_json", "concept_id"}
        assert expected.issubset(set(cols))

    def test_ticket_index_has_assignment_columns(self, fresh_db):
        cols = {r[1] for r in fresh_db.execute("PRAGMA table_info(ticket_index)").fetchall()}
        assert "canonical_issue_id" in cols
        assert "canonical_confidence" in cols
        assert "assignment_method" in cols
        assert "hdbscan_membership_prob" in cols
        assert "canonicalized_at" in cols

    def test_tuning_runs_columns(self, fresh_db):
        cols = {r[1] for r in fresh_db.execute(
            "PRAGMA table_info(canonicalization_tuning_runs)"
        ).fetchall()}
        expected = {"run_id", "params_json", "silhouette", "davies_bouldin",
                    "golden_precision", "golden_recall", "golden_f1",
                    "noise_pct", "n_clusters", "gemini_coherence",
                    "wall_time_ms", "scan_id", "trc", "notes"}
        assert expected.issubset(cols)
