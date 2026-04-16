"""Tests for Phase 7 cluster enrichment rollups (S10.1).

Covers src/data/cluster_enrichment_rollup.compute_enrichment_rollups +
get_enrichment and the engine integration call from run_canonicalization.
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
    run_canonicalization,
)
from src.data.cluster_enrichment_rollup import (
    _CORE_METRICS_CHECKSUM,
    _CORE_METRICS_SQL,
    ClusterEnrichment,
    RollupResult,
    compute_enrichment_rollups,
    get_enrichment,
)
from src.data.db_manager import DatabaseManager


EMBED_DIM = 1024
PARAMS = {**DEFAULT_PARAMS, "min_cluster_size": 3, "enrichment_rollup_enabled": True}


# ──────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────

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


def _insert_ticket(
    conn,
    ticket_id: str,
    *,
    trc: str = "BILLING",
    cluster_id: str | None = None,
    sentiment_polarity: str | None = "neutral",
    sentiment_intensity: float | None = 0.5,
    csat: float | None = None,
    resolution_hours: float | None = 2.0,
    anomaly_flag: str | None = "normal",
    insurance_payer: str | None = None,
    provider_id: str | None = None,
    service_state: str | None = None,
    agent_id: str | None = None,
    key_phrases: str | None = None,
    created_date: str = "2026-04-15",
    subject: str = "subject",
):
    conn.execute(
        """INSERT INTO ticket_index
             (ticket_id, trc_code, first_seen_scan_id, last_seen_scan_id,
              first_seen_date, ticket_created_date, subject_sanitized,
              sentiment_polarity, sentiment_intensity, csat_score,
              resolution_hours, anomaly_flag,
              insurance_payer, provider_id, service_state, agent_id,
              key_phrases, canonical_issue_id)
           VALUES (?, ?, 'ingestion', 'scan1', ?, ?, ?,
                   ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            ticket_id, trc, f"{created_date}T00:00:00", created_date, subject,
            sentiment_polarity, sentiment_intensity, csat, resolution_hours, anomaly_flag,
            insurance_payer, provider_id, service_state, agent_id,
            key_phrases, cluster_id,
        ),
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


def _insert_classification(conn, ticket_id: str, trc: str = "BILLING", conf: float = 0.85):
    conn.execute(
        """INSERT INTO nlp_ticket_classifications
             (classification_id, batch_id, ticket_id, scan_id, trc,
              sub_cluster, sub_cluster_confidence, created_at)
           VALUES (?, 'b1', ?, 'scan1', ?, 'x', ?, '2026-04-15T00:00:00')""",
        (uuid.uuid4().hex, ticket_id, trc, conf),
    )


def _seed_cluster(
    conn,
    cluster_id: str,
    trc: str,
    n: int,
    *,
    center_seed: int = 11,
    sentiment_polarity: str = "negative",
    csat: float | None = 2.5,
    anomaly_flag: str = "normal",
    payer: str = "BlueCross",
    state: str = "CA",
    provider_prefix: str = "prov_",
):
    """Insert a cluster row + n tickets assigned to it."""
    # Create a centroid
    rng = np.random.default_rng(center_seed)
    c = _l2_normalize(rng.standard_normal(EMBED_DIM).astype(np.float32))
    conn.execute(
        """INSERT OR REPLACE INTO canonical_clusters
             (cluster_id, trc, centroid_blob, representative_ticket_id,
              member_count, lifetime_tickets, lifetime_scans, tier,
              discovered_scan_id, last_seen_scan_id, label_source)
           VALUES (?, ?, ?, NULL, ?, ?, 1, 'active',
                   'scan1', 'scan1', 'medoid')""",
        (cluster_id, trc, _vec_to_blob(c), n, n),
    )
    for i in range(n):
        tid = f"{cluster_id}-t{i}"
        _insert_ticket(
            conn, tid, trc=trc, cluster_id=cluster_id,
            sentiment_polarity=sentiment_polarity,
            sentiment_intensity=0.7 if sentiment_polarity == "negative" else 0.3,
            csat=csat if i % 2 == 0 else None,  # half respond
            anomaly_flag="anomaly" if i < max(1, n // 5) else anomaly_flag,
            insurance_payer=payer,
            provider_id=f"{provider_prefix}{i % 3}",
            service_state=state,
            agent_id=f"agent_{i % 2}",
            key_phrases=json.dumps(["refund delay", "portal error"]) if i % 2 == 0 else json.dumps(["refund delay"]),
            created_date=f"2026-04-{10 + (i % 5):02d}",
            subject=f"{trc} ticket {i}",
        )
        _insert_classification(conn, tid, trc=trc)
        # Embedding noise around centroid
        noise = rng.standard_normal(EMBED_DIM).astype(np.float32) * 0.02
        _insert_embedding(conn, tid, c + noise)


# ──────────────────────────────────────────────────────────────────────────
# Basic path tests
# ──────────────────────────────────────────────────────────────────────────

def test_empty_db_returns_zero(fresh_db):
    result = compute_enrichment_rollups(fresh_db, scan_id="s1")
    assert isinstance(result, RollupResult)
    assert result.rows_written == 0
    assert result.cluster_count == 0


def test_single_cluster_populates_row(fresh_db):
    _seed_cluster(fresh_db, "cluster-A", "BILLING", n=5)
    result = compute_enrichment_rollups(fresh_db, scan_id="s1")
    assert result.rows_written == 1
    assert result.cluster_count == 1

    row = fresh_db.execute(
        "SELECT cluster_id, scan_id, ticket_count FROM canonical_cluster_enrichment"
    ).fetchone()
    assert row[0] == "cluster-A"
    assert row[1] == "s1"
    assert row[2] == 5


def test_ticket_count_matches_ticket_index(fresh_db):
    _seed_cluster(fresh_db, "cA", "BILLING", n=7)
    _seed_cluster(fresh_db, "cB", "CLAIMS", n=3, center_seed=22)
    compute_enrichment_rollups(fresh_db, scan_id="s1")

    rows = {r[0]: r[1] for r in fresh_db.execute(
        "SELECT cluster_id, ticket_count FROM canonical_cluster_enrichment"
    ).fetchall()}
    assert rows["cA"] == 7
    assert rows["cB"] == 3


def test_get_enrichment_roundtrip(fresh_db):
    _seed_cluster(fresh_db, "cA", "BILLING", n=4)
    compute_enrichment_rollups(fresh_db, scan_id="s1")
    e = get_enrichment(fresh_db, "cA", "s1")
    assert e is not None
    assert isinstance(e, ClusterEnrichment)
    assert e.cluster_id == "cA"
    assert e.ticket_count == 4


def test_get_enrichment_returns_none_when_missing(fresh_db):
    _seed_cluster(fresh_db, "cA", "BILLING", n=3)
    compute_enrichment_rollups(fresh_db, scan_id="s1")
    assert get_enrichment(fresh_db, "doesnotexist", "s1") is None
    assert get_enrichment(fresh_db, "cA", "wrong-scan") is None


# ──────────────────────────────────────────────────────────────────────────
# Top-N distribution tests
# ──────────────────────────────────────────────────────────────────────────

def test_top_payers_sums_to_total(fresh_db):
    _seed_cluster(fresh_db, "cA", "BILLING", n=6, payer="Aetna")
    compute_enrichment_rollups(fresh_db, scan_id="s1")
    e = get_enrichment(fresh_db, "cA", "s1")
    assert e is not None
    total = sum(item["count"] for item in e.top_payers)
    assert total == 6
    assert e.top_payers[0]["value"] == "Aetna"
    assert e.top_payers[0]["count"] == 6
    assert e.top_payers[0]["pct"] == 1.0


def test_top_tags_populates_from_ticket_tags(fresh_db):
    _seed_cluster(fresh_db, "cA", "BILLING", n=5)
    # Add tags for 3 of the 5 tickets
    for i in range(3):
        fresh_db.execute(
            "INSERT INTO ticket_tags (ticket_id, tag, source) VALUES (?, ?, 'test')",
            (f"cA-t{i}", "billing_refund"),
        )
    fresh_db.execute(
        "INSERT INTO ticket_tags (ticket_id, tag, source) VALUES (?, ?, 'test')",
        ("cA-t0", "portal_error"),
    )
    compute_enrichment_rollups(fresh_db, scan_id="s1")
    e = get_enrichment(fresh_db, "cA", "s1")
    assert e is not None
    tags = {t["value"]: t for t in e.top_tags}
    assert "billing_refund" in tags
    assert tags["billing_refund"]["count"] == 3
    assert tags["billing_refund"]["pct"] == round(3 / 5, 4)


def test_top_key_phrases_aggregates_across_tickets(fresh_db):
    _seed_cluster(fresh_db, "cA", "BILLING", n=4)
    compute_enrichment_rollups(fresh_db, scan_id="s1")
    e = get_enrichment(fresh_db, "cA", "s1")
    assert e is not None
    phrase_values = {item["value"] for item in e.top_key_phrases}
    # Even-indexed tickets got 2 phrases, odd got 1 — so refund delay appears 4x,
    # portal error 2x
    assert "refund delay" in phrase_values
    assert "portal error" in phrase_values
    refund = next(p for p in e.top_key_phrases if p["value"] == "refund delay")
    assert refund["count"] == 4


def test_trc_distribution_single_trc(fresh_db):
    _seed_cluster(fresh_db, "cA", "BILLING", n=5)
    compute_enrichment_rollups(fresh_db, scan_id="s1")
    e = get_enrichment(fresh_db, "cA", "s1")
    assert len(e.trc_distribution) == 1
    assert e.trc_distribution[0]["value"] == "BILLING"
    assert e.trc_distribution[0]["count"] == 5


def test_all_top_n_lists_capped_at_5(fresh_db):
    # 10 different payers, expect top 5 only
    for i in range(10):
        _seed_cluster(fresh_db, f"cX{i}", "BILLING", n=3, payer=f"Payer{i}", center_seed=100 + i)
    # Combine them into one cluster manually for the test
    fresh_db.execute(
        "UPDATE ticket_index SET canonical_issue_id = ? WHERE canonical_issue_id LIKE 'cX%'",
        ("cM",),
    )
    dummy_centroid = _vec_to_blob(_l2_normalize(np.ones(EMBED_DIM, dtype=np.float32)))
    fresh_db.execute(
        """INSERT INTO canonical_clusters
             (cluster_id, trc, centroid_blob, member_count, lifetime_tickets,
              lifetime_scans, tier, discovered_scan_id, last_seen_scan_id, label_source)
           VALUES ('cM', 'BILLING', ?, 30, 30, 1, 'active', 's1', 's1', 'medoid')""",
        (dummy_centroid,),
    )
    compute_enrichment_rollups(fresh_db, scan_id="s1")
    e = get_enrichment(fresh_db, "cM", "s1")
    assert e is not None
    assert len(e.top_payers) == 5


# ──────────────────────────────────────────────────────────────────────────
# Sentiment / CSAT / anomaly
# ──────────────────────────────────────────────────────────────────────────

def test_negative_sentiment_pct(fresh_db):
    # seed 5 tickets: 4 negative, 1 positive
    _seed_cluster(fresh_db, "cA", "BILLING", n=4, sentiment_polarity="negative")
    # replace one with positive manually
    fresh_db.execute(
        "UPDATE ticket_index SET sentiment_polarity = 'positive' WHERE ticket_id = 'cA-t0'"
    )
    compute_enrichment_rollups(fresh_db, scan_id="s1")
    e = get_enrichment(fresh_db, "cA", "s1")
    # 3 of 4 are negative -> 0.75
    assert e.negative_sentiment_pct == pytest.approx(0.75, abs=1e-4)


def test_csat_response_rate_and_avg(fresh_db):
    _seed_cluster(fresh_db, "cA", "BILLING", n=6, csat=4.0)
    # half respond (i % 2 == 0 → 3 of 6 have CSAT)
    compute_enrichment_rollups(fresh_db, scan_id="s1")
    e = get_enrichment(fresh_db, "cA", "s1")
    assert e.csat_response_rate == pytest.approx(0.5, abs=1e-4)
    assert e.avg_csat == pytest.approx(4.0, abs=1e-4)


def test_anomaly_ticket_pct(fresh_db):
    _seed_cluster(fresh_db, "cA", "BILLING", n=10)
    # _seed_cluster marks n//5 == 2 tickets as 'anomaly'
    compute_enrichment_rollups(fresh_db, scan_id="s1")
    e = get_enrichment(fresh_db, "cA", "s1")
    assert e.anomaly_ticket_pct == pytest.approx(0.2, abs=1e-4)


# ──────────────────────────────────────────────────────────────────────────
# Idempotence and checksum
# ──────────────────────────────────────────────────────────────────────────

def test_rerun_replaces_rows_for_same_scan(fresh_db):
    _seed_cluster(fresh_db, "cA", "BILLING", n=3)
    compute_enrichment_rollups(fresh_db, scan_id="s1")
    n1 = fresh_db.execute("SELECT COUNT(*) FROM canonical_cluster_enrichment").fetchone()[0]
    # Re-run — should not double rows
    compute_enrichment_rollups(fresh_db, scan_id="s1")
    n2 = fresh_db.execute("SELECT COUNT(*) FROM canonical_cluster_enrichment").fetchone()[0]
    assert n1 == n2 == 1


def test_different_scan_ids_coexist(fresh_db):
    _seed_cluster(fresh_db, "cA", "BILLING", n=3)
    compute_enrichment_rollups(fresh_db, scan_id="s1")
    compute_enrichment_rollups(fresh_db, scan_id="s2")
    n = fresh_db.execute("SELECT COUNT(*) FROM canonical_cluster_enrichment").fetchone()[0]
    assert n == 2  # one row per (cluster, scan)


def test_checksum_stable_across_runs(fresh_db):
    _seed_cluster(fresh_db, "cA", "BILLING", n=3)
    compute_enrichment_rollups(fresh_db, scan_id="s1")
    chk_a = fresh_db.execute(
        "SELECT source_query_checksum FROM canonical_cluster_enrichment"
    ).fetchone()[0]
    compute_enrichment_rollups(fresh_db, scan_id="s2")
    chk_b = fresh_db.execute(
        "SELECT source_query_checksum FROM canonical_cluster_enrichment WHERE scan_id = 's2'"
    ).fetchone()[0]
    assert chk_a == chk_b == _CORE_METRICS_CHECKSUM


def test_json_fields_parse_cleanly(fresh_db):
    _seed_cluster(fresh_db, "cA", "BILLING", n=4)
    compute_enrichment_rollups(fresh_db, scan_id="s1")
    for col in ("top_payers_json", "top_providers_json", "top_states_json",
                "top_agents_json", "top_key_phrases_json", "top_tags_json",
                "trc_distribution_json", "custom_metrics_json"):
        raw = fresh_db.execute(
            f"SELECT {col} FROM canonical_cluster_enrichment"
        ).fetchone()[0]
        # All should be valid JSON — empty list is fine
        parsed = json.loads(raw)
        assert parsed is not None


# ──────────────────────────────────────────────────────────────────────────
# Error surfaces
# ──────────────────────────────────────────────────────────────────────────

def test_missing_migration_is_noop(tmp_path: Path):
    # Build a DB without migration 023 applied
    db_path = tmp_path / "no_migration.db"
    conn = sqlite3.connect(str(db_path))
    # Minimal ticket_index table so core SQL doesn't crash
    conn.execute("""CREATE TABLE ticket_index (
        ticket_id TEXT, canonical_issue_id TEXT,
        sentiment_intensity REAL, sentiment_polarity TEXT,
        csat_score REAL, resolution_hours REAL, anomaly_flag TEXT,
        trc_code TEXT, ticket_created_date DATE, insurance_payer TEXT,
        provider_id TEXT, service_state TEXT, agent_id TEXT, key_phrases TEXT
    )""")
    conn.commit()
    result = compute_enrichment_rollups(conn, scan_id="s1")
    assert result.rows_written == 0
    assert result.cluster_count == 0


def test_run_canonicalization_writes_rollups_when_enabled(fresh_db):
    """End-to-end: run_canonicalization should populate enrichment after clustering."""
    # Seed 2 clusters with embeddings so canonicalization actually runs
    for i in range(6):
        vec = np.zeros(EMBED_DIM, dtype=np.float32)
        vec[i % 3] = 1.0  # 3 tight clusters of 2 each — too small
    # Actually use _seed_cluster to make it work
    rng = np.random.default_rng(42)
    c1 = _l2_normalize(rng.standard_normal(EMBED_DIM).astype(np.float32))
    c2 = _l2_normalize(rng.standard_normal(EMBED_DIM).astype(np.float32))
    for i in range(5):
        _insert_ticket(fresh_db, f"a{i}", trc="BILLING", insurance_payer="Aetna")
        _insert_classification(fresh_db, f"a{i}", "BILLING")
        noise = rng.standard_normal(EMBED_DIM).astype(np.float32) * 0.02
        _insert_embedding(fresh_db, f"a{i}", c1 + noise)
    for i in range(5):
        _insert_ticket(fresh_db, f"b{i}", trc="BILLING", insurance_payer="BlueCross")
        _insert_classification(fresh_db, f"b{i}", "BILLING")
        noise = rng.standard_normal(EMBED_DIM).astype(np.float32) * 0.02
        _insert_embedding(fresh_db, f"b{i}", c2 + noise)
    fresh_db.commit()

    run_canonicalization(fresh_db, scan_id="scan1", params=PARAMS)

    # After canonicalization, the rollup should have run
    n = fresh_db.execute(
        "SELECT COUNT(*) FROM canonical_cluster_enrichment WHERE scan_id = 'scan1'"
    ).fetchone()[0]
    assert n >= 1  # at least one cluster got rolled up


def test_run_canonicalization_skips_rollup_when_disabled(fresh_db):
    """enrichment_rollup_enabled=False leaves the table empty."""
    rng = np.random.default_rng(42)
    c = _l2_normalize(rng.standard_normal(EMBED_DIM).astype(np.float32))
    for i in range(5):
        _insert_ticket(fresh_db, f"t{i}", trc="BILLING")
        _insert_classification(fresh_db, f"t{i}", "BILLING")
        noise = rng.standard_normal(EMBED_DIM).astype(np.float32) * 0.02
        _insert_embedding(fresh_db, f"t{i}", c + noise)
    fresh_db.commit()

    run_canonicalization(
        fresh_db, scan_id="scan1",
        params={**PARAMS, "enrichment_rollup_enabled": False},
    )

    n = fresh_db.execute(
        "SELECT COUNT(*) FROM canonical_cluster_enrichment"
    ).fetchone()[0]
    assert n == 0


def test_core_metrics_sql_is_well_formed():
    """The SQL string should be a single SELECT with expected aggregations."""
    assert "SELECT" in _CORE_METRICS_SQL
    assert "COUNT(*)" in _CORE_METRICS_SQL
    assert "canonical_issue_id" in _CORE_METRICS_SQL
    assert "GROUP BY" in _CORE_METRICS_SQL
    assert len(_CORE_METRICS_CHECKSUM) == 64  # SHA256 hex is 64 chars
