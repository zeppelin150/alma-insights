"""Tests for src/data/issue_query_handler.py (Phase 9 §9.1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pytest

from src.data.db_manager import DatabaseManager
from src.data.issue_query_handler import (
    QueryIssueFilters,
    _parse_date_range,
    handle_query_issues,
    query_issues,
)


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────

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


def _make_centroid(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(1024).astype(np.float32)
    n = float(np.linalg.norm(v))
    v = v / n if n else v
    return v.astype(np.float32).tobytes()


def _seed_dataset(conn) -> None:
    """Populate canonical_concepts + canonical_clusters + ticket_index with
    a compact but realistic scenario:
      concept BILL (2 clusters: BILL-dup, BILL-overcharge)
      concept PORT (1 cluster: PORT-outage)
      concept None (1 cluster: LOGIN-reset, concept_id=NULL)"""
    conn.execute(
        """INSERT INTO canonical_concepts
             (concept_id, concept_label, member_cluster_count)
           VALUES ('bill', 'Billing disputes', 2),
                  ('port', 'Portal outages', 1)"""
    )
    conn.execute(
        """INSERT INTO canonical_clusters
             (cluster_id, trc, canonical_label, centroid_blob, tier,
              lifetime_tickets, member_count, concept_id)
           VALUES
             ('bill-dup',       'BILLING', 'Duplicate invoice',    ?, 'active', 100, 10, 'bill'),
             ('bill-overcharge','BILLING', 'Overcharge',           ?, 'active',  80,  8, 'bill'),
             ('port-outage',    'PORTAL',  'Portal outage',        ?, 'active',  60,  6, 'port'),
             ('login-reset',    'LOGIN',   'Password reset',       ?, 'active',  40,  4, NULL)""",
        (_make_centroid(1), _make_centroid(2), _make_centroid(3), _make_centroid(4)),
    )

    rows: list[tuple] = []
    # BILL concept: 10 tickets (Thunderbird), 8 tickets (Molina)
    for i in range(10):
        rows.append((
            f"B{i}", "BILLING", "Billing", "Thunderbird Insurance", "CA",
            "2026-01-15", f"dup invoice {i}", "bill-dup",
        ))
    for i in range(8):
        rows.append((
            f"O{i}", "BILLING", "Billing", "Molina Health", "TX",
            "2026-02-10", f"overcharge {i}", "bill-overcharge",
        ))
    # PORT concept: 6 tickets (mixed payers, 2026-03)
    for i in range(6):
        rows.append((
            f"P{i}", "PORTAL", "Portal", "Thunderbird Insurance" if i < 3 else "Molina Health",
            "CA", "2026-03-01", f"portal down {i}", "port-outage",
        ))
    # Unlinked cluster: 4 tickets (LOGIN)
    for i in range(4):
        rows.append((
            f"L{i}", "LOGIN", "Login", "Aetna", "NY",
            "2026-03-15", f"login reset {i}", "login-reset",
        ))
    # Unassigned ticket (shouldn't appear in concept/cluster groupings)
    rows.append((
        "UNASSIGNED_1", "BILLING", "Billing", "Thunderbird Insurance", "CA",
        "2026-01-20", "orphan ticket", None,
    ))

    for tid, trc_code, trc_label, payer, state, created, subject, cid in rows:
        conn.execute(
            """INSERT INTO ticket_index
                 (ticket_id, trc_code, trc_label, insurance_payer, service_state,
                  ticket_created_date, subject_sanitized, canonical_issue_id,
                  first_seen_scan_id, last_seen_scan_id, first_seen_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'scan1', 'scan1', ?)""",
            (tid, trc_code, trc_label, payer, state, created, subject, cid, created),
        )

    # Tags
    for tid in ["B0", "B1", "B2", "O0", "L0"]:
        conn.execute(
            "INSERT INTO ticket_tags (ticket_id, tag, source) VALUES (?, 'billing_issue', 'manual')",
            (tid,),
        )
    for tid in ["P0", "P1", "P2"]:
        conn.execute(
            "INSERT INTO ticket_tags (ticket_id, tag, source) VALUES (?, 'portal_outage', 'manual')",
            (tid,),
        )
    conn.commit()


# ──────────────────────────────────────────────────────────────────────
# Parse helpers
# ──────────────────────────────────────────────────────────────────────

class TestParseDateRange:
    def test_none_input(self):
        assert _parse_date_range(None) == (None, None)
        assert _parse_date_range("") == (None, None)

    def test_full_range(self):
        assert _parse_date_range("2026-01-01/2026-03-31") == ("2026-01-01", "2026-03-31")

    def test_open_start(self):
        assert _parse_date_range("/2026-03-31") == (None, "2026-03-31")

    def test_open_end(self):
        assert _parse_date_range("2026-01-01/") == ("2026-01-01", None)

    def test_single_date_both_bounds(self):
        assert _parse_date_range("2026-01-15") == ("2026-01-15", "2026-01-15")


class TestQueryIssueFilters:
    def test_empty_as_dict(self):
        assert QueryIssueFilters().as_dict() == {}

    def test_populated_as_dict(self):
        f = QueryIssueFilters(trc="Billing", payer="Thunderbird", date_start="2026-01-01", date_end="2026-03-31")
        d = f.as_dict()
        assert d == {
            "trc": "Billing",
            "payer": "Thunderbird",
            "date_range": "2026-01-01/2026-03-31",
        }


# ──────────────────────────────────────────────────────────────────────
# Core query_issues
# ──────────────────────────────────────────────────────────────────────

class TestQueryIssuesEmpty:
    def test_empty_db_returns_empty_result(self, fresh_db):
        result = query_issues(fresh_db)
        assert result["scope"]["total_tickets"] == 0
        assert result["top_issues"] == []
        assert result["group_by"] == "concept"

    def test_invalid_group_by_raises(self, fresh_db):
        with pytest.raises(ValueError):
            query_issues(fresh_db, group_by="bogus")


class TestQueryIssuesGrouping:
    def test_group_by_concept_counts_correctly(self, fresh_db):
        _seed_dataset(fresh_db)
        result = query_issues(fresh_db, group_by="concept")
        # 10 + 8 (bill) + 6 (port) + 4 (login unlinked = cluster fallback)
        assert result["scope"]["total_tickets"] == 10 + 8 + 6 + 4
        top = result["top_issues"]
        # Top concept should be 'bill' with 18 tickets
        assert top[0]["group_id"] == "bill"
        assert top[0]["label"] == "Billing disputes"
        assert top[0]["ticket_count"] == 18
        # All three groups should be present
        ids = {i["group_id"] for i in top}
        assert "bill" in ids
        assert "port" in ids
        # Unlinked cluster falls back to 'cluster:login-reset'
        assert any(i.startswith("cluster:") for i in ids)

    def test_group_by_cluster_shows_each_cluster(self, fresh_db):
        _seed_dataset(fresh_db)
        result = query_issues(fresh_db, group_by="cluster")
        cluster_ids = {i["group_id"] for i in result["top_issues"]}
        assert "bill-dup" in cluster_ids
        assert "bill-overcharge" in cluster_ids
        assert "port-outage" in cluster_ids
        assert "login-reset" in cluster_ids

    def test_group_by_trc(self, fresh_db):
        _seed_dataset(fresh_db)
        result = query_issues(fresh_db, group_by="trc")
        ids = {i["group_id"] for i in result["top_issues"]}
        assert "BILLING" in ids
        assert "PORTAL" in ids
        assert "LOGIN" in ids

    def test_group_by_payer(self, fresh_db):
        _seed_dataset(fresh_db)
        result = query_issues(fresh_db, group_by="payer")
        labels = [i["label"] for i in result["top_issues"]]
        assert "Thunderbird Insurance" in labels


class TestQueryIssuesFilters:
    def test_filter_by_trc(self, fresh_db):
        _seed_dataset(fresh_db)
        result = query_issues(fresh_db, trc="BILLING", group_by="concept")
        # Only assigned BILL tickets counted in concept-scoped total (10+8=18);
        # the 1 unassigned orphan is excluded from concept groupings.
        assert result["scope"]["total_tickets"] == 18
        bill_item = [i for i in result["top_issues"] if i["group_id"] == "bill"][0]
        assert bill_item["ticket_count"] == 18
        assert not any(i["group_id"] == "port" for i in result["top_issues"])
        assert result["scope"]["filters"]["trc"] == "BILLING"

    def test_filter_by_trc_group_by_trc_includes_unassigned(self, fresh_db):
        _seed_dataset(fresh_db)
        # group_by="trc" is a ticket-intrinsic grouping and should NOT exclude
        # tickets with no canonical assignment
        r = query_issues(fresh_db, trc="BILLING", group_by="trc")
        assert r["scope"]["total_tickets"] == 19  # 18 assigned + 1 orphan

    def test_filter_by_payer_case_insensitive_substring(self, fresh_db):
        _seed_dataset(fresh_db)
        r = query_issues(fresh_db, payer="thunderbird", group_by="concept")
        # Thunderbird has 10 BILL-dup + 3 PORT = 13 assigned (orphan excluded)
        assert r["scope"]["total_tickets"] == 13
        assert r["scope"]["filters"]["payer"] == "thunderbird"

    def test_filter_by_state(self, fresh_db):
        _seed_dataset(fresh_db)
        r = query_issues(fresh_db, state="CA")
        # CA: 10 BILL-dup + 6 PORT = 16 assigned (orphan excluded from concept grouping)
        assert r["scope"]["total_tickets"] == 16

    def test_filter_by_date_range(self, fresh_db):
        _seed_dataset(fresh_db)
        r = query_issues(fresh_db, date_range="2026-03-01/2026-03-31")
        # 2026-03-01: 6 PORT; 2026-03-15: 4 LOGIN = 10
        assert r["scope"]["total_tickets"] == 10

    def test_filter_by_concept_id(self, fresh_db):
        _seed_dataset(fresh_db)
        r = query_issues(fresh_db, concept_id="bill", group_by="cluster")
        # Only clusters linked to 'bill': bill-dup (10) + bill-overcharge (8) = 18
        assert r["scope"]["total_tickets"] == 18
        cluster_ids = {i["group_id"] for i in r["top_issues"]}
        assert cluster_ids == {"bill-dup", "bill-overcharge"}

    def test_filter_by_tag(self, fresh_db):
        _seed_dataset(fresh_db)
        r = query_issues(fresh_db, tag="portal_outage", group_by="cluster")
        # 3 tickets tagged 'portal_outage' (P0, P1, P2), all in port-outage
        assert r["scope"]["total_tickets"] == 3
        assert r["top_issues"][0]["group_id"] == "port-outage"


class TestQueryIssuesSamples:
    def test_samples_populated_for_each_issue(self, fresh_db):
        _seed_dataset(fresh_db)
        r = query_issues(fresh_db, group_by="cluster", include_samples=True, sample_size=3)
        for issue in r["top_issues"]:
            if issue["group_id"].startswith("cluster:"):
                continue
            assert len(issue["sample_ticket_ids"]) <= 3
            assert len(issue["sample_ticket_ids"]) >= 1
            # Snippet should be populated since seed gives subject_sanitized
            assert issue["example_snippet"] is not None
            assert len(issue["example_snippet"]) > 0

    def test_samples_skipped_when_include_samples_false(self, fresh_db):
        _seed_dataset(fresh_db)
        r = query_issues(fresh_db, group_by="cluster", include_samples=False)
        for issue in r["top_issues"]:
            assert issue["sample_ticket_ids"] == []
            assert issue["example_snippet"] is None

    def test_trcs_touched_only_for_concept_cluster_groups(self, fresh_db):
        _seed_dataset(fresh_db)
        concept_r = query_issues(fresh_db, group_by="concept")
        for issue in concept_r["top_issues"]:
            assert "trcs_touched" in issue
        trc_r = query_issues(fresh_db, group_by="trc")
        for issue in trc_r["top_issues"]:
            assert "trcs_touched" not in issue

    def test_pct_of_scope_sums_to_one(self, fresh_db):
        _seed_dataset(fresh_db)
        r = query_issues(fresh_db, group_by="concept")
        total_pct = sum(i["pct_of_scope"] for i in r["top_issues"])
        # All 28 assigned tickets land in some top_issue group (concept
        # or cluster-fallback), so pct should sum to ~1.0
        assert 0.99 <= total_pct <= 1.01


class TestQueryIssuesLimits:
    def test_limit_honored(self, fresh_db):
        _seed_dataset(fresh_db)
        r = query_issues(fresh_db, group_by="cluster", limit=2)
        assert len(r["top_issues"]) == 2

    def test_limit_clamped_to_max(self, fresh_db):
        _seed_dataset(fresh_db)
        # limit=9999 should be clamped to 50, but there are only 4 clusters
        r = query_issues(fresh_db, group_by="cluster", limit=9999)
        assert len(r["top_issues"]) == 4


# ──────────────────────────────────────────────────────────────────────
# handle_query_issues adapter
# ──────────────────────────────────────────────────────────────────────

class TestHandler:
    def test_dispatch_from_args_dict(self, fresh_db):
        _seed_dataset(fresh_db)
        r = handle_query_issues(fresh_db, {"payer": "Thunderbird", "group_by": "concept"})
        assert "scope" in r
        assert r["scope"]["filters"]["payer"] == "Thunderbird"

    def test_session_filters_override_args(self, fresh_db):
        _seed_dataset(fresh_db)
        r = handle_query_issues(
            fresh_db,
            {"trc": "BILLING", "group_by": "concept"},
            session_filters={"trc": "PORTAL"},
        )
        # Session filter wins
        assert r["scope"]["filters"]["trc"] == "PORTAL"

    def test_invalid_group_by_returns_error_dict(self, fresh_db):
        r = handle_query_issues(fresh_db, {"group_by": "invalid"})
        assert "error" in r

    def test_unknown_keys_ignored(self, fresh_db):
        _seed_dataset(fresh_db)
        # Should not crash on garbage keys
        r = handle_query_issues(fresh_db, {
            "trc": "BILLING", "garbage_key": "ignored",
            "another_bad_one": 42,
        })
        assert "scope" in r
