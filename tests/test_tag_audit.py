"""Tests for src/data/tag_audit.py (Phase 9 §9.3)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from src.data.db_manager import DatabaseManager
from src.data.tag_audit import (
    _blob_to_vec,
    audit_tag_correlation,
    handle_audit_tag_correlation,
)


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


def _vec_to_blob(v: np.ndarray) -> bytes:
    n = float(np.linalg.norm(v))
    v = v / n if n else v
    return v.astype(np.float32).tobytes()


def _make_centroid(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(1024).astype(np.float32)
    n = float(np.linalg.norm(v))
    return (v / n) if n else v


def _insert_cluster(conn, cluster_id: str, trc: str, label: str, centroid: np.ndarray,
                     concept_id: str | None = None):
    conn.execute(
        """INSERT INTO canonical_clusters
             (cluster_id, trc, canonical_label, centroid_blob, tier, concept_id)
           VALUES (?, ?, ?, ?, 'active', ?)""",
        (cluster_id, trc, label, _vec_to_blob(centroid), concept_id),
    )


def _insert_ticket(conn, ticket_id: str, *, trc="BILLING", payer="X",
                    cluster_id: str | None = None, subject: str = "test",
                    embedding: np.ndarray | None = None):
    conn.execute(
        """INSERT INTO ticket_index
             (ticket_id, trc_code, insurance_payer, subject_sanitized,
              canonical_issue_id, first_seen_scan_id, last_seen_scan_id,
              first_seen_date, ticket_created_date)
           VALUES (?, ?, ?, ?, ?, 'scan1', 'scan1', '2026-01-15', '2026-01-15')""",
        (ticket_id, trc, payer, subject, cluster_id),
    )
    if embedding is not None:
        conn.execute(
            """INSERT INTO ticket_embeddings
                 (ticket_id, embedding_blob, source_text_hash, model_name, dim_size, created_at)
               VALUES (?, ?, ?, 'Qwen3-Embedding-0.6B', 1024, '2026-01-15')""",
            (ticket_id, _vec_to_blob(embedding), f"h{ticket_id}"),
        )


def _tag_ticket(conn, ticket_id: str, tag: str):
    conn.execute(
        "INSERT INTO ticket_tags (ticket_id, tag, source) VALUES (?, ?, 'manual')",
        (ticket_id, tag),
    )


def _insert_incident(conn, incident_id: str, description: str,
                      expected_cluster: str | None = None):
    conn.execute(
        """INSERT INTO incidents
             (incident_id, description, incident_date, expected_canonical_cluster)
           VALUES (?, ?, '2026-03-01', ?)""",
        (incident_id, description, expected_cluster),
    )


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

class TestBlobToVec:
    def test_none_returns_none(self):
        assert _blob_to_vec(None) is None

    def test_zero_vector_preserved(self):
        z = np.zeros(10, dtype=np.float32).tobytes()
        v = _blob_to_vec(z)
        assert v is not None and float(np.linalg.norm(v)) == 0.0

    def test_normalizes(self):
        raw = np.array([3.0, 4.0], dtype=np.float32).tobytes()
        v = _blob_to_vec(raw)
        assert abs(float(np.linalg.norm(v)) - 1.0) < 1e-5


# ──────────────────────────────────────────────────────────────────────
# Invalid inputs
# ──────────────────────────────────────────────────────────────────────

class TestInvalidInput:
    def test_empty_tag_returns_error(self, fresh_db):
        r = audit_tag_correlation(fresh_db, "")
        assert "error" in r

    def test_none_tag_returns_error(self, fresh_db):
        r = audit_tag_correlation(fresh_db, None)
        assert "error" in r


# ──────────────────────────────────────────────────────────────────────
# Unknown tag
# ──────────────────────────────────────────────────────────────────────

class TestUnknownTag:
    def test_unknown_tag_returns_zero_counts(self, fresh_db):
        r = audit_tag_correlation(fresh_db, "never_seen_tag")
        assert r["tag"] == "never_seen_tag"
        assert r["total_tagged_tickets"] == 0
        assert r["tagged_with_assignment"] == 0
        assert r["tagged_unassigned"] == 0
        assert r["expected_cluster_id"] is None
        assert r["concept_distribution"] == []
        assert r["cluster_distribution"] == []
        assert r["likely_mistagged"] == []


# ──────────────────────────────────────────────────────────────────────
# Known tag, no incident mapping
# ──────────────────────────────────────────────────────────────────────

class TestKnownTagNoIncident:
    def test_returns_distributions_but_no_mistag_ranking(self, fresh_db):
        # 2 tagged tickets in cluster A, 1 in cluster B, no incident record
        centA = _make_centroid(1)
        centB = _make_centroid(99)
        _insert_cluster(fresh_db, "clusA", "BILLING", "Billing issue A", centA, "bill")
        _insert_cluster(fresh_db, "clusB", "BILLING", "Billing issue B", centB, "bill")
        fresh_db.execute(
            """INSERT INTO canonical_concepts (concept_id, concept_label) VALUES ('bill', 'Billing')"""
        )
        for i in range(2):
            tid = f"A{i}"
            _insert_ticket(fresh_db, tid, cluster_id="clusA", embedding=centA)
            _tag_ticket(fresh_db, tid, "custom_tag")
        tid = "B0"
        _insert_ticket(fresh_db, tid, cluster_id="clusB", embedding=centB)
        _tag_ticket(fresh_db, tid, "custom_tag")
        fresh_db.commit()

        r = audit_tag_correlation(fresh_db, "custom_tag")
        assert r["total_tagged_tickets"] == 3
        assert r["tagged_with_assignment"] == 3
        assert r["tagged_unassigned"] == 0
        assert r["expected_cluster_id"] is None
        assert r["likely_mistagged"] == []
        # Concept distribution: all 3 in 'bill'
        assert len(r["concept_distribution"]) == 1
        assert r["concept_distribution"][0]["concept_id"] == "bill"
        assert r["concept_distribution"][0]["ticket_count"] == 3
        # Cluster distribution: 2 in clusA + 1 in clusB
        clusters = {c["cluster_id"]: c["ticket_count"] for c in r["cluster_distribution"]}
        assert clusters == {"clusA": 2, "clusB": 1}


# ──────────────────────────────────────────────────────────────────────
# Known tag with incident + expected cluster
# ──────────────────────────────────────────────────────────────────────

class TestKnownTagWithIncident:
    def test_mistagged_tickets_ranked_by_cosine_distance(self, fresh_db):
        # Expected cluster for the incident: PORT
        centPort = _make_centroid(42)
        centBill = _make_centroid(7)
        _insert_cluster(fresh_db, "port-outage", "PORTAL", "Portal outage", centPort)
        _insert_cluster(fresh_db, "bill-dup",   "BILLING", "Duplicate invoice", centBill)

        _insert_incident(fresh_db, "incident_portal_outage_0301",
                          "Portal outage March 1", expected_cluster="port-outage")

        # 3 correctly tagged tickets in port-outage
        for i in range(3):
            _insert_ticket(fresh_db, f"OK{i}", cluster_id="port-outage", embedding=centPort)
            _tag_ticket(fresh_db, f"OK{i}", "incident_portal_outage_0301")
        # 2 mis-tagged tickets in bill-dup (different concept entirely)
        for i in range(2):
            _insert_ticket(fresh_db, f"BAD{i}", cluster_id="bill-dup", embedding=centBill)
            _tag_ticket(fresh_db, f"BAD{i}", "incident_portal_outage_0301")
        fresh_db.commit()

        r = audit_tag_correlation(fresh_db, "incident_portal_outage_0301")
        assert r["total_tagged_tickets"] == 5
        assert r["expected_cluster_id"] == "port-outage"
        assert r["expected_cluster_label"] == "Portal outage"

        # Exactly 2 tickets should be flagged as likely mis-tagged (the BAD ones)
        assert len(r["likely_mistagged"]) == 2
        mistag_ids = {m["ticket_id"] for m in r["likely_mistagged"]}
        assert mistag_ids == {"BAD0", "BAD1"}
        for m in r["likely_mistagged"]:
            # bill-dup centroid is orthogonal to port-outage → distance ~ 1.0
            assert m["assigned_cluster_id"] == "bill-dup"
            assert m["cosine_distance_to_expected"] is not None
            assert m["cosine_distance_to_expected"] > 0.5

    def test_tickets_without_embeddings_go_last(self, fresh_db):
        centPort = _make_centroid(42)
        centBill = _make_centroid(7)
        _insert_cluster(fresh_db, "port-outage", "PORTAL", "Portal outage", centPort)
        _insert_cluster(fresh_db, "bill-dup", "BILLING", "Duplicate invoice", centBill)
        _insert_incident(fresh_db, "inc1", "Test", expected_cluster="port-outage")

        # Mis-tagged ticket WITH embedding (high cos distance)
        _insert_ticket(fresh_db, "with_emb", cluster_id="bill-dup", embedding=centBill)
        _tag_ticket(fresh_db, "with_emb", "inc1")
        # Mis-tagged ticket WITHOUT embedding (should sort last)
        _insert_ticket(fresh_db, "no_emb", cluster_id="bill-dup", embedding=None)
        _tag_ticket(fresh_db, "no_emb", "inc1")
        fresh_db.commit()

        r = audit_tag_correlation(fresh_db, "inc1")
        assert len(r["likely_mistagged"]) == 2
        # First has cosine distance set; second is None
        assert r["likely_mistagged"][0]["cosine_distance_to_expected"] is not None
        assert r["likely_mistagged"][1]["cosine_distance_to_expected"] is None
        assert r["likely_mistagged"][0]["ticket_id"] == "with_emb"
        assert r["likely_mistagged"][1]["ticket_id"] == "no_emb"

    def test_correctly_tagged_tickets_excluded_from_mistag_list(self, fresh_db):
        centPort = _make_centroid(42)
        _insert_cluster(fresh_db, "port-outage", "PORTAL", "Portal outage", centPort)
        _insert_incident(fresh_db, "inc1", "Test", expected_cluster="port-outage")

        for i in range(5):
            _insert_ticket(fresh_db, f"T{i}", cluster_id="port-outage", embedding=centPort)
            _tag_ticket(fresh_db, f"T{i}", "inc1")
        fresh_db.commit()

        r = audit_tag_correlation(fresh_db, "inc1")
        assert r["total_tagged_tickets"] == 5
        assert r["likely_mistagged"] == []  # all correctly tagged

    def test_top_k_limits_mistag_output(self, fresh_db):
        centPort = _make_centroid(42)
        centBill = _make_centroid(7)
        _insert_cluster(fresh_db, "port-outage", "PORTAL", "Portal outage", centPort)
        _insert_cluster(fresh_db, "bill-dup", "BILLING", "Duplicate invoice", centBill)
        _insert_incident(fresh_db, "inc1", "Test", expected_cluster="port-outage")

        for i in range(15):
            _insert_ticket(fresh_db, f"BAD{i}", cluster_id="bill-dup", embedding=centBill)
            _tag_ticket(fresh_db, f"BAD{i}", "inc1")
        fresh_db.commit()

        r = audit_tag_correlation(fresh_db, "inc1", top_k=5)
        assert len(r["likely_mistagged"]) == 5


# ──────────────────────────────────────────────────────────────────────
# Tickets with no canonical assignment
# ──────────────────────────────────────────────────────────────────────

class TestUnassignedTickets:
    def test_unassigned_tickets_counted_separately(self, fresh_db):
        _insert_cluster(fresh_db, "clusA", "BILLING", "A", _make_centroid(1))
        _insert_ticket(fresh_db, "assigned", cluster_id="clusA", embedding=_make_centroid(1))
        _insert_ticket(fresh_db, "orphan", cluster_id=None, embedding=_make_centroid(1))
        _tag_ticket(fresh_db, "assigned", "mixed")
        _tag_ticket(fresh_db, "orphan", "mixed")
        fresh_db.commit()

        r = audit_tag_correlation(fresh_db, "mixed")
        assert r["total_tagged_tickets"] == 2
        assert r["tagged_with_assignment"] == 1
        assert r["tagged_unassigned"] == 1


# ──────────────────────────────────────────────────────────────────────
# Handler adapter
# ──────────────────────────────────────────────────────────────────────

class TestHandler:
    def test_handler_passes_tag_and_top_k(self, fresh_db):
        _insert_cluster(fresh_db, "c1", "BILLING", "A", _make_centroid(1))
        _insert_ticket(fresh_db, "t1", cluster_id="c1")
        _tag_ticket(fresh_db, "t1", "x")
        fresh_db.commit()
        r = handle_audit_tag_correlation(fresh_db, {"tag": "x", "top_k": 3})
        assert r["tag"] == "x"
        assert r["total_tagged_tickets"] == 1

    def test_handler_default_top_k(self, fresh_db):
        r = handle_audit_tag_correlation(fresh_db, {"tag": "x"})
        assert r["tag"] == "x"
