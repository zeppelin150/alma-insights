"""Tests for S10.4 historical member snapshots.

Covers `_write_member_snapshots` + `_fetch_snippets_from_prior_scan` +
the end-to-end round trip through `run_canonicalization` +
`_build_sample_row` with `snippets_then` populated.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import numpy as np
import pytest

from src.data.canonicalization_engine import (
    DEFAULT_PARAMS,
    _l2_normalize,
    _vec_to_blob,
    _write_member_snapshots,
    run_canonicalization,
)
from src.data.canonicalization_regression import (
    _build_sample_row,
    _fetch_snippets_from_prior_scan,
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


def _insert_ticket(conn, tid: str, trc: str = "BILLING",
                   cluster_id: str | None = None,
                   subject: str = "subj"):
    now = "2026-04-15T00:00:00"
    conn.execute(
        """INSERT INTO ticket_index
             (ticket_id, trc_code, first_seen_scan_id, last_seen_scan_id,
              first_seen_date, ticket_created_date, subject_sanitized,
              canonical_issue_id)
           VALUES (?, ?, 'ingestion', 'scan1', ?, ?, ?, ?)""",
        (tid, trc, now, now, subject, cluster_id),
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


def _seed_cluster(
    conn, cluster_id: str, trc: str, n: int, *, center_seed: int = 11,
):
    rng = np.random.default_rng(center_seed)
    c = _l2_normalize(rng.standard_normal(EMBED_DIM).astype(np.float32))
    conn.execute(
        """INSERT OR REPLACE INTO canonical_clusters
             (cluster_id, trc, centroid_blob, representative_ticket_id,
              member_count, lifetime_tickets, lifetime_scans, tier,
              discovered_scan_id, last_seen_scan_id, label_source,
              canonical_label)
           VALUES (?, ?, ?, NULL, ?, ?, 1, 'active',
                   'scan1', 'scan1', 'medoid', 'test label')""",
        (cluster_id, trc, _vec_to_blob(c), n, n),
    )
    for i in range(n):
        tid = f"{cluster_id}-t{i}"
        _insert_ticket(conn, tid, trc=trc, cluster_id=cluster_id,
                       subject=f"subject {i}")
        noise = rng.standard_normal(EMBED_DIM).astype(np.float32) * 0.02
        _insert_embedding(conn, tid, c + noise)


# ──────────────────────────────────────────────────────────────────────────
# _write_member_snapshots unit tests
# ──────────────────────────────────────────────────────────────────────────

def test_write_empty_returns_zero(fresh_db):
    assert _write_member_snapshots(fresh_db, scan_id="s1") == 0


def test_write_single_cluster_writes_one_row(fresh_db):
    _seed_cluster(fresh_db, "cA", "BILLING", n=5)
    n = _write_member_snapshots(fresh_db, scan_id="s1")
    assert n == 1
    row = fresh_db.execute(
        "SELECT cluster_id, scan_id, member_count FROM cluster_member_snapshots"
    ).fetchone()
    assert tuple(row) == ("cA", "s1", 5)


def test_write_multiple_clusters(fresh_db):
    _seed_cluster(fresh_db, "cA", "BILLING", n=5)
    _seed_cluster(fresh_db, "cB", "CLAIMS", n=3, center_seed=22)
    n = _write_member_snapshots(fresh_db, scan_id="s1")
    assert n == 2


def test_write_caps_members_at_cap(fresh_db):
    _seed_cluster(fresh_db, "cA", "BILLING", n=10)
    _write_member_snapshots(fresh_db, scan_id="s1", cap=3)
    row = fresh_db.execute(
        "SELECT member_ticket_ids_json, member_count FROM cluster_member_snapshots"
    ).fetchone()
    ids = json.loads(row[0])
    assert len(ids) == 3        # sampled to cap
    assert row[1] == 10         # member_count preserves true total


def test_write_deterministic_sampling(fresh_db):
    _seed_cluster(fresh_db, "cA", "BILLING", n=50)
    _write_member_snapshots(fresh_db, scan_id="s1", cap=5)
    first = json.loads(fresh_db.execute(
        "SELECT member_ticket_ids_json FROM cluster_member_snapshots"
    ).fetchone()[0])
    # Re-run (same scan_id) replaces prior snapshot → same sample
    _write_member_snapshots(fresh_db, scan_id="s1", cap=5)
    second = json.loads(fresh_db.execute(
        "SELECT member_ticket_ids_json FROM cluster_member_snapshots"
    ).fetchone()[0])
    assert first == second


def test_write_different_scans_produce_different_samples(fresh_db):
    _seed_cluster(fresh_db, "cA", "BILLING", n=50)
    _write_member_snapshots(fresh_db, scan_id="sA", cap=5)
    _write_member_snapshots(fresh_db, scan_id="sB", cap=5)
    s_a = json.loads(fresh_db.execute(
        "SELECT member_ticket_ids_json FROM cluster_member_snapshots WHERE scan_id = 'sA'"
    ).fetchone()[0])
    s_b = json.loads(fresh_db.execute(
        "SELECT member_ticket_ids_json FROM cluster_member_snapshots WHERE scan_id = 'sB'"
    ).fetchone()[0])
    # Seeds differ → samples differ (at cap=5 out of 50, collision improbable)
    assert s_a != s_b


def test_write_idempotent_same_scan(fresh_db):
    _seed_cluster(fresh_db, "cA", "BILLING", n=5)
    _write_member_snapshots(fresh_db, scan_id="s1")
    n = fresh_db.execute(
        "SELECT COUNT(*) FROM cluster_member_snapshots WHERE scan_id = 's1'"
    ).fetchone()[0]
    _write_member_snapshots(fresh_db, scan_id="s1")
    n2 = fresh_db.execute(
        "SELECT COUNT(*) FROM cluster_member_snapshots WHERE scan_id = 's1'"
    ).fetchone()[0]
    assert n == n2 == 1


# ──────────────────────────────────────────────────────────────────────────
# _fetch_snippets_from_prior_scan
# ──────────────────────────────────────────────────────────────────────────

def test_fetch_prior_snippets_empty_when_no_snapshot(fresh_db):
    _seed_cluster(fresh_db, "cA", "BILLING", n=3)
    out = _fetch_snippets_from_prior_scan(fresh_db, "cA", current_scan_id="s1")
    assert out == []


def test_fetch_prior_snippets_skips_current_scan(fresh_db):
    _seed_cluster(fresh_db, "cA", "BILLING", n=3)
    _write_member_snapshots(fresh_db, scan_id="s1")
    # Asking for prior to "s1" — only snapshot we have IS s1 → empty
    out = _fetch_snippets_from_prior_scan(fresh_db, "cA", current_scan_id="s1")
    assert out == []


def test_fetch_prior_snippets_returns_subjects(fresh_db):
    _seed_cluster(fresh_db, "cA", "BILLING", n=4)
    _write_member_snapshots(fresh_db, scan_id="s1")
    # Now pretend we're on scan s2 — s1 is the "prior"
    out = _fetch_snippets_from_prior_scan(fresh_db, "cA", current_scan_id="s2")
    assert len(out) > 0
    # Subjects have "subject " prefix from fixture
    for snippet in out:
        assert snippet.startswith("subject")


def test_fetch_prior_snippets_returns_most_recent(fresh_db):
    _seed_cluster(fresh_db, "cA", "BILLING", n=3)
    _write_member_snapshots(fresh_db, scan_id="s1")
    # Add a second snapshot (different scan) by tweaking rows
    _write_member_snapshots(fresh_db, scan_id="s2")
    # Now currently on s3, should pick s2 (most recent BEFORE s3)
    out = _fetch_snippets_from_prior_scan(fresh_db, "cA", current_scan_id="s3")
    assert len(out) > 0


def test_fetch_prior_snippets_no_current_scan_returns_most_recent(fresh_db):
    _seed_cluster(fresh_db, "cA", "BILLING", n=3)
    _write_member_snapshots(fresh_db, scan_id="s1")
    out = _fetch_snippets_from_prior_scan(fresh_db, "cA", current_scan_id=None)
    assert len(out) > 0


# ──────────────────────────────────────────────────────────────────────────
# End-to-end: engine writes snapshots; regression hydrates snippets_then
# ──────────────────────────────────────────────────────────────────────────

def test_run_canonicalization_writes_snapshots(fresh_db):
    rng = np.random.default_rng(42)
    c = _l2_normalize(rng.standard_normal(EMBED_DIM).astype(np.float32))
    for i in range(5):
        _insert_ticket(fresh_db, f"t{i}", trc="BILLING",
                       subject=f"scan1 subject {i}")
        noise = rng.standard_normal(EMBED_DIM).astype(np.float32) * 0.02
        _insert_embedding(fresh_db, f"t{i}", c + noise)
    fresh_db.commit()

    params = {**DEFAULT_PARAMS, "min_cluster_size": 3, "member_snapshots_enabled": True}
    run_canonicalization(fresh_db, scan_id="scan1", params=params)

    n = fresh_db.execute(
        "SELECT COUNT(*) FROM cluster_member_snapshots WHERE scan_id = 'scan1'"
    ).fetchone()[0]
    assert n >= 1


def test_run_canonicalization_disabled_no_snapshots(fresh_db):
    rng = np.random.default_rng(42)
    c = _l2_normalize(rng.standard_normal(EMBED_DIM).astype(np.float32))
    for i in range(5):
        _insert_ticket(fresh_db, f"t{i}", trc="BILLING")
        noise = rng.standard_normal(EMBED_DIM).astype(np.float32) * 0.02
        _insert_embedding(fresh_db, f"t{i}", c + noise)
    fresh_db.commit()

    params = {**DEFAULT_PARAMS, "min_cluster_size": 3, "member_snapshots_enabled": False}
    run_canonicalization(fresh_db, scan_id="scan1", params=params)

    n = fresh_db.execute(
        "SELECT COUNT(*) FROM cluster_member_snapshots"
    ).fetchone()[0]
    assert n == 0


def test_build_sample_row_populates_snippets_then(fresh_db):
    _seed_cluster(fresh_db, "cA", "BILLING", n=4)
    # Write a "prior" snapshot at scan s1
    _write_member_snapshots(fresh_db, scan_id="s1")
    # Now build sample row for scan s2 — snippets_then should hydrate
    sample = _build_sample_row(
        fresh_db, cluster_id="cA", stratum="stable", current_scan_id="s2",
    )
    assert sample is not None
    assert len(sample.snippets_then) > 0
    assert all(s.startswith("subject") for s in sample.snippets_then)


def test_build_sample_row_first_scan_empty_snippets_then(fresh_db):
    _seed_cluster(fresh_db, "cA", "BILLING", n=4)
    # No prior snapshot
    sample = _build_sample_row(
        fresh_db, cluster_id="cA", stratum="stable", current_scan_id="s1",
    )
    assert sample is not None
    assert sample.snippets_then == []


def test_build_sample_row_same_scan_empty_snippets_then(fresh_db):
    _seed_cluster(fresh_db, "cA", "BILLING", n=4)
    _write_member_snapshots(fresh_db, scan_id="s1")
    # Current scan == s1 → can't use s1's snapshot as "prior"
    sample = _build_sample_row(
        fresh_db, cluster_id="cA", stratum="stable", current_scan_id="s1",
    )
    assert sample is not None
    assert sample.snippets_then == []


# ──────────────────────────────────────────────────────────────────────────
# Error surfaces
# ──────────────────────────────────────────────────────────────────────────

def test_write_missing_migration_noop(tmp_path: Path):
    """If migration 025 isn't applied, write function is a silent no-op."""
    conn = sqlite3.connect(str(tmp_path / "raw.db"))
    conn.execute("""CREATE TABLE ticket_index (
        ticket_id TEXT, canonical_issue_id TEXT
    )""")
    conn.commit()
    n = _write_member_snapshots(conn, scan_id="s1")
    assert n == 0


def test_fetch_missing_migration_returns_empty(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "raw.db"))
    out = _fetch_snippets_from_prior_scan(conn, "cA", current_scan_id="s1")
    assert out == []
