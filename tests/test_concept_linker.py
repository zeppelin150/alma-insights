"""Tests for src/data/concept_linker.py (Phase 7)."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from src.data.concept_linker import (
    ClusterPayload,
    ConceptDecision,
    _extract_json_object,
    _slugify,
    call_llm_for_concepts,
    load_cluster_payloads,
    reconcile_decisions,
    run_concept_linking,
)
from src.data.canonicalization_engine import (
    score_golden_set,
    score_golden_set_by_concept,
)
from src.data.db_manager import DatabaseManager


def _zero_blob(dim: int = 1024) -> bytes:
    return struct.pack(f"<{dim}f", *([0.0] * dim))


@pytest.fixture
def fresh_db(tmp_path: Path):
    db_path = tmp_path / "concept.db"
    mgr = DatabaseManager(db_path)
    mgr.initialize()
    conn = mgr.get_connection()
    conn.execute("PRAGMA foreign_keys = OFF")
    yield conn
    try:
        conn.close()
    except Exception:
        pass


def _insert_cluster(conn, *, cluster_id: str, trc: str, label: str, member_count: int = 5):
    conn.execute(
        """
        INSERT INTO canonical_clusters
          (cluster_id, trc, canonical_label, label_source, centroid_blob,
           member_count, lifetime_tickets, discovered_scan_id)
        VALUES (?, ?, ?, 'llm', ?, ?, ?, 'scan-x')
        """,
        (cluster_id, trc, label, _zero_blob(), member_count, member_count),
    )


def _insert_ticket(conn, *, ticket_id: str, cluster_id: str, trc: str = "B"):
    conn.execute(
        """
        INSERT INTO ticket_index
          (ticket_id, first_seen_scan_id, last_seen_scan_id, first_seen_date,
           trc_code, trc_label, issue_snippet, canonical_issue_id,
           classification_confidence)
        VALUES (?, 'scan-x', 'scan-x', '2026-01-01', ?, ?, ?, ?, 0.8)
        """,
        (ticket_id, trc, trc, f"Snippet for {ticket_id}", cluster_id),
    )


def _insert_classification(conn, *, ticket_id: str, key_phrases: list[str]):
    conn.execute(
        """
        INSERT INTO nlp_ticket_classifications
          (classification_id, batch_id, scan_id, ticket_id, trc, sub_cluster,
           sub_cluster_confidence, key_phrases, created_at)
        VALUES (?, 'b', 'scan-x', ?, 'T', 'S', 0.8, ?, CURRENT_TIMESTAMP)
        """,
        (f"c-{ticket_id}", ticket_id, json.dumps(key_phrases)),
    )


# ──────────────────────────────────────────────────────────────────────
# Schema / migration
# ──────────────────────────────────────────────────────────────────────


class TestMigration020:
    def test_canonical_concepts_table_exists(self, fresh_db):
        rows = fresh_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='canonical_concepts'"
        ).fetchall()
        assert rows

    def test_concept_linking_runs_table_exists(self, fresh_db):
        rows = fresh_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='canonical_concept_linking_runs'"
        ).fetchall()
        assert rows

    def test_cluster_concept_fk_column_present(self, fresh_db):
        cols = [r[1] for r in fresh_db.execute("PRAGMA table_info(canonical_clusters)")]
        assert "concept_id" in cols


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


class TestSlugify:
    def test_basic(self):
        assert _slugify("Hello World Issue") == "hello-world-issue"

    def test_punctuation_stripped(self):
        assert _slugify("Payment & Refunds!!!").startswith("payment-refunds")

    def test_length_cap(self):
        out = _slugify("a" * 200, max_len=20)
        assert len(out) <= 20

    def test_empty_returns_uuid_fallback(self):
        out = _slugify("")
        assert len(out) >= 6


class TestJSONExtraction:
    def test_fenced(self):
        raw = "```json\n{\"concepts\": []}\n```"
        assert _extract_json_object(raw) == {"concepts": []}

    def test_prose_wrapped(self):
        raw = "Sure!\n{\"concepts\": [{\"concept_id\": \"x\"}]}\nEnd."
        parsed = _extract_json_object(raw)
        assert parsed["concepts"][0]["concept_id"] == "x"


# ──────────────────────────────────────────────────────────────────────
# Payload loading
# ──────────────────────────────────────────────────────────────────────


class TestPayloadLoading:
    def test_joins_snippets_and_phrases(self, fresh_db):
        conn = fresh_db
        _insert_cluster(conn, cluster_id="cl1", trc="B", label="Billing dispute")
        _insert_ticket(conn, ticket_id="t1", cluster_id="cl1")
        _insert_classification(conn, ticket_id="t1", key_phrases=["alpha", "beta"])
        conn.commit()

        payloads = load_cluster_payloads(conn)
        assert len(payloads) == 1
        p = payloads[0]
        assert p.cluster_id == "cl1"
        assert p.canonical_label == "Billing dispute"
        assert "alpha" in p.top_key_phrases
        assert p.representative_snippets and "Snippet for t1" in p.representative_snippets[0]

    def test_skips_retired_clusters(self, fresh_db):
        conn = fresh_db
        _insert_cluster(conn, cluster_id="cl1", trc="B", label="Keep")
        conn.execute(
            "INSERT INTO canonical_clusters (cluster_id, trc, tier, centroid_blob) VALUES (?,?,?,?)",
            ("cl2-retired", "B", "retired", _zero_blob()),
        )
        conn.commit()
        payloads = load_cluster_payloads(conn)
        assert {p.cluster_id for p in payloads} == {"cl1"}


# ──────────────────────────────────────────────────────────────────────
# Reconciliation
# ──────────────────────────────────────────────────────────────────────


class TestReconcile:
    def test_happy_path(self):
        decisions = [
            ConceptDecision("c-billing", "Billing issues", "", ["a", "b"]),
            ConceptDecision("c-portal", "Portal problems", "", ["d"]),
        ]
        rec, orphans = reconcile_decisions(decisions, ["a", "b", "c", "d"])
        # "c" is orphan
        assert orphans == ["c"]
        # +1 orphan concept
        assert len(rec) == 3
        all_ids = [cid for d in rec for cid in d.cluster_ids]
        assert set(all_ids) == {"a", "b", "c", "d"}

    def test_duplicate_assignment_first_wins(self):
        decisions = [
            ConceptDecision("c1", "X", "", ["a", "b"]),
            ConceptDecision("c2", "Y", "", ["b", "c"]),  # "b" duplicated
        ]
        rec, orphans = reconcile_decisions(decisions, ["a", "b", "c"])
        assert orphans == []
        # c1 keeps "b", c2 only has "c"
        by_id = {d.concept_id: d for d in rec}
        assert set(by_id["c1"].cluster_ids) == {"a", "b"}
        assert set(by_id["c2"].cluster_ids) == {"c"}


# ──────────────────────────────────────────────────────────────────────
# LLM invocation (fake client)
# ──────────────────────────────────────────────────────────────────────


class _FakeLLM:
    def __init__(self, response: str):
        self._response = response
        self.calls = 0

    def generate(self, prompt: str, timeout: int = 240) -> str:
        self.calls += 1
        self._last_prompt = prompt
        return self._response


class TestLLMCall:
    def test_parses_concepts(self):
        payloads = [
            ClusterPayload(cluster_id=f"c{i}", canonical_label=f"Label {i}",
                           trc="B", member_count=5) for i in range(3)
        ]
        resp = json.dumps({
            "concepts": [
                {"concept_id": "billing-core", "concept_label": "Billing disputes",
                 "concept_description": "Customers dispute billed amounts.",
                 "cluster_ids": ["c0", "c1"], "rationale": "r", "confidence": 0.9},
                {"concept_id": "standalone", "concept_label": "Single-cluster",
                 "concept_description": "A lone cluster.",
                 "cluster_ids": ["c2"], "rationale": "", "confidence": 0.7},
            ]
        })
        client = _FakeLLM(resp)
        decisions, raw = call_llm_for_concepts(payloads, client)
        assert client.calls == 1
        assert len(decisions) == 2
        assert decisions[0].cluster_ids == ["c0", "c1"]
        assert decisions[1].concept_label == "Single-cluster"

    def test_empty_on_none_client(self):
        payloads = [ClusterPayload("c0", "L", "B", 5)]
        decisions, raw = call_llm_for_concepts(payloads, None)
        assert decisions == []

    def test_unparseable_is_empty(self):
        client = _FakeLLM("no json")
        decisions, raw = call_llm_for_concepts(
            [ClusterPayload("c0", "L", "B", 5)], client,
        )
        assert decisions == []


# ──────────────────────────────────────────────────────────────────────
# End-to-end run_concept_linking
# ──────────────────────────────────────────────────────────────────────


class TestRunConceptLinking:
    def test_no_llm_makes_each_cluster_its_own_concept(self, fresh_db):
        conn = fresh_db
        _insert_cluster(conn, cluster_id="cl1", trc="B", label="Billing one")
        _insert_cluster(conn, cluster_id="cl2", trc="B", label="Billing two")
        conn.commit()

        result = run_concept_linking(conn, scan_id="s", llm_client=None)
        assert result.cluster_count_input == 2
        # Without an LLM, reconcile puts each orphan in its own concept
        assert result.concept_count_output == 2
        # Clusters get concept_id populated
        ids = conn.execute(
            "SELECT cluster_id, concept_id FROM canonical_clusters ORDER BY cluster_id"
        ).fetchall()
        assert all(cid is not None for _, cid in ids)

    def test_llm_groups_clusters_and_updates_fk(self, fresh_db):
        conn = fresh_db
        _insert_cluster(conn, cluster_id="cl1", trc="B", label="Billing eligibility dispute")
        _insert_cluster(conn, cluster_id="cl2", trc="B", label="Billing eligibility denial")
        _insert_cluster(conn, cluster_id="cl3", trc="B", label="Portal login broken")
        conn.commit()

        resp = json.dumps({
            "concepts": [
                {"concept_id": "eligibility", "concept_label": "Eligibility disputes",
                 "concept_description": "Billing eligibility issues.",
                 "cluster_ids": ["cl1", "cl2"], "confidence": 0.9},
                {"concept_id": "portal", "concept_label": "Portal login issues",
                 "concept_description": "Users cannot log in.",
                 "cluster_ids": ["cl3"], "confidence": 0.85},
            ]
        })
        result = run_concept_linking(conn, scan_id="s", llm_client=_FakeLLM(resp))
        assert result.concept_count_output == 2
        fk = dict(conn.execute("SELECT cluster_id, concept_id FROM canonical_clusters").fetchall())
        assert fk["cl1"] == fk["cl2"]
        assert fk["cl1"] != fk["cl3"]

        # Audit row written
        rows = conn.execute(
            "SELECT cluster_count_input, concept_count_output FROM canonical_concept_linking_runs"
        ).fetchall()
        assert len(rows) == 1
        assert (rows[0][0], rows[0][1]) == (3, 2)


# ──────────────────────────────────────────────────────────────────────
# score_golden_set_by_concept
# ──────────────────────────────────────────────────────────────────────


class TestScoreByConcept:
    def test_concept_recovers_recall_across_sibling_clusters(self, fresh_db):
        """Two tickets in different clusters but same concept → same_group=True."""
        conn = fresh_db
        _insert_cluster(conn, cluster_id="cl1", trc="B", label="Eligibility A")
        _insert_cluster(conn, cluster_id="cl2", trc="B", label="Eligibility B")
        _insert_ticket(conn, ticket_id="t1", cluster_id="cl1")
        _insert_ticket(conn, ticket_id="t2", cluster_id="cl2")
        conn.commit()

        golden = [("t1", "t2", True)]

        # At cluster level: t1 and t2 are in different clusters → FN
        r_cluster = score_golden_set(conn, golden)
        assert r_cluster["recall"] == 0.0

        # Link both clusters into one concept
        run_concept_linking(
            conn, scan_id="s",
            llm_client=_FakeLLM(json.dumps({
                "concepts": [{
                    "concept_id": "eligibility",
                    "concept_label": "Eligibility issues",
                    "concept_description": "Eligibility disputes.",
                    "cluster_ids": ["cl1", "cl2"],
                    "confidence": 0.9,
                }]
            })),
        )

        # Concept-level: t1 and t2 share concept → TP, recall = 1.0
        r_concept = score_golden_set_by_concept(conn, golden)
        assert r_concept["recall"] == 1.0
        assert r_concept["scoring_level"] == "concept"

    def test_concept_preserves_precision(self, fresh_db):
        """Two tickets in same cluster but golden says different → still FP."""
        conn = fresh_db
        _insert_cluster(conn, cluster_id="cl1", trc="B", label="Billing")
        _insert_ticket(conn, ticket_id="t1", cluster_id="cl1")
        _insert_ticket(conn, ticket_id="t2", cluster_id="cl1")
        conn.commit()

        golden = [("t1", "t2", False)]  # golden says they are different
        r = score_golden_set_by_concept(conn, golden)
        assert r["fp"] == 1  # same cluster + same concept (via cluster fallback)
        assert r["precision"] == 0.0
