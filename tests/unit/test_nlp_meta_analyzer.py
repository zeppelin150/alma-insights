"""
Unit tests for NLPMetaAnalyzer (Layer 2 meta-analysis).

Tests cover:
- Within-TRC analysis (significant group detection, critical anomaly findings)
- Sub-taxonomy tier lifecycle (probationary -> active -> dormant -> retired)
- Snapshot creation per scan
- Cross-TRC pattern detection (entity + friction spanning 3+ TRCs)
- Finding deduplication and impact scoring
- N-gram upsert logic
- Edge cases (empty scan, no classifications)
"""

import json
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from src.data.nlp_meta_analyzer import NLPMetaAnalyzer


# ── Helpers ──────────────────────────────────────────────────────────

def _make_scan_id():
    return str(uuid.uuid4())


def _insert_scan(db, scan_id, status="scan_complete",
                 date_start=None, date_end=None):
    """Insert a minimal nlp_scan_runs row."""
    now = datetime.now()
    date_start = date_start or (now - timedelta(days=30)).strftime("%Y-%m-%d")
    date_end = date_end or now.strftime("%Y-%m-%d")
    db.conn.execute("""
        INSERT INTO nlp_scan_runs
            (scan_id, created_at, status, date_range_start, date_range_end,
             mode, total_tickets)
        VALUES (?, ?, ?, ?, ?, 'agentic', 0)
    """, (scan_id, now.isoformat(), status, date_start, date_end))
    db.commit()


def _insert_classification(db, scan_id, ticket_id, trc, sub_cluster,
                           friction_type="process_gap",
                           anomaly_flag="none",
                           sentiment_intensity=3,
                           sentiment_polarity="negative",
                           is_novel=0,
                           entities_json=None,
                           key_phrases=None,
                           summary=None):
    """Insert a single nlp_ticket_classifications row."""
    cid = str(uuid.uuid4())
    batch_id = f"batch-{scan_id[:8]}"
    # Ensure the batch exists
    db.conn.execute("""
        INSERT OR IGNORE INTO nlp_batches
            (batch_id, scan_id, batch_number, trc, status, created_at)
        VALUES (?, ?, 1, ?, 'complete', ?)
    """, (batch_id, scan_id, trc, datetime.now().isoformat()))
    db.conn.execute("""
        INSERT INTO nlp_ticket_classifications
            (classification_id, batch_id, scan_id, ticket_id, trc,
             sub_cluster, friction_type, anomaly_flag, sentiment_intensity,
             sentiment_polarity, is_novel, entities_json, key_phrases,
             summary, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (cid, batch_id, scan_id, ticket_id, trc,
          sub_cluster, friction_type, anomaly_flag,
          sentiment_intensity, sentiment_polarity, is_novel,
          entities_json, key_phrases, summary,
          datetime.now().isoformat()))


def _insert_sub_pattern(db, trc, label, tier="active",
                        lifetime_tickets=20, lifetime_scans=3,
                        discovered_scan=None, last_seen_at=None,
                        merged_into=None):
    """Insert a sub_patterns row and return its pattern_id."""
    pid = str(uuid.uuid4())
    now = datetime.now().isoformat()
    discovered_scan = discovered_scan or _make_scan_id()
    last_seen_at = last_seen_at or now
    db.conn.execute("""
        INSERT INTO sub_patterns
            (pattern_id, trc, label, description, friction_type, tier,
             discovered_scan, discovered_at, last_seen_scan, last_seen_at,
             lifetime_tickets, lifetime_scans, merged_into)
        VALUES (?, ?, ?, 'test', 'process_gap', ?, ?, ?, ?, ?, ?, ?, ?)
    """, (pid, trc, label, tier, discovered_scan, now,
          discovered_scan, last_seen_at, lifetime_tickets, lifetime_scans,
          merged_into))
    db.commit()
    return pid


def _count_findings(db, scan_id, finding_type=None):
    if finding_type:
        return db.conn.execute(
            "SELECT COUNT(*) FROM nlp_findings WHERE scan_id = ? AND finding_type = ?",
            (scan_id, finding_type)
        ).fetchone()[0]
    return db.conn.execute(
        "SELECT COUNT(*) FROM nlp_findings WHERE scan_id = ?", (scan_id,)
    ).fetchone()[0]


def _get_findings(db, scan_id, finding_type=None):
    if finding_type:
        rows = db.conn.execute(
            "SELECT * FROM nlp_findings WHERE scan_id = ? AND finding_type = ?",
            (scan_id, finding_type)
        ).fetchall()
    else:
        rows = db.conn.execute(
            "SELECT * FROM nlp_findings WHERE scan_id = ?", (scan_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════
#  WITHIN-TRC ANALYSIS
# ═══════════════════════════════════════════════════════════════════

class TestWithinTRCAnalysis:
    """Tests for _analyze_within_trc and significant group detection."""

    def test_significant_group_creates_finding(self, seeded_db):
        """Sub-cluster with >= 5 tickets produces a within_trc finding."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        scan_id = _make_scan_id()
        _insert_scan(seeded_db, scan_id)

        for i in range(6):
            _insert_classification(
                seeded_db, scan_id, f"T-sig-{i}", "TRC-100",
                "billing confusion", friction_type="process_gap",
            )
        seeded_db.commit()

        analyzer._analyze_within_trc(scan_id, "TRC-100")

        findings = _get_findings(seeded_db, scan_id, "within_trc")
        assert len(findings) == 1
        assert findings[0]["ticket_count"] == 6
        assert "billing confusion" in findings[0]["title"]

    def test_below_threshold_no_finding(self, seeded_db):
        """Sub-cluster with < 5 tickets and no critical anomaly => no finding."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        scan_id = _make_scan_id()
        _insert_scan(seeded_db, scan_id)

        for i in range(3):
            _insert_classification(
                seeded_db, scan_id, f"T-small-{i}", "TRC-100",
                "minor issue",
            )
        seeded_db.commit()

        analyzer._analyze_within_trc(scan_id, "TRC-100")
        assert _count_findings(seeded_db, scan_id) == 0

    def test_critical_anomaly_always_creates_finding(self, seeded_db):
        """Even 1 ticket with critical anomaly creates a finding."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        scan_id = _make_scan_id()
        _insert_scan(seeded_db, scan_id)

        _insert_classification(
            seeded_db, scan_id, "T-crit-1", "TRC-200",
            "system outage", anomaly_flag="critical",
        )
        seeded_db.commit()

        analyzer._analyze_within_trc(scan_id, "TRC-200")

        findings = _get_findings(seeded_db, scan_id, "within_trc")
        assert len(findings) == 1
        assert findings[0]["ticket_count"] == 1

    def test_multiple_sub_clusters_create_separate_findings(self, seeded_db):
        """Two significant sub-clusters in same TRC => two findings."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        scan_id = _make_scan_id()
        _insert_scan(seeded_db, scan_id)

        for i in range(5):
            _insert_classification(
                seeded_db, scan_id, f"T-a-{i}", "TRC-100",
                "payment failure",
            )
            _insert_classification(
                seeded_db, scan_id, f"T-b-{i}", "TRC-100",
                "refund delay",
            )
        seeded_db.commit()

        analyzer._analyze_within_trc(scan_id, "TRC-100")
        assert _count_findings(seeded_db, scan_id, "within_trc") == 2

    def test_dominant_friction_type_captured(self, seeded_db):
        """Finding records the most common friction type in the group."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        scan_id = _make_scan_id()
        _insert_scan(seeded_db, scan_id)

        for i in range(5):
            ft = "escalation_demand" if i < 3 else "process_gap"
            _insert_classification(
                seeded_db, scan_id, f"T-ft-{i}", "TRC-300",
                "claims stuck", friction_type=ft,
            )
        seeded_db.commit()

        analyzer._analyze_within_trc(scan_id, "TRC-300")
        findings = _get_findings(seeded_db, scan_id, "within_trc")
        assert findings[0]["dominant_friction_type"] == "escalation_demand"

    def test_empty_trc_no_findings(self, seeded_db):
        """TRC with zero classifications produces no findings."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        scan_id = _make_scan_id()
        _insert_scan(seeded_db, scan_id)

        analyzer._analyze_within_trc(scan_id, "TRC-999")
        assert _count_findings(seeded_db, scan_id) == 0


# ═══════════════════════════════════════════════════════════════════
#  SUB-TAXONOMY LIFECYCLE
# ═══════════════════════════════════════════════════════════════════

class TestSubTaxonomyLifecycle:
    """Tests for _update_sub_taxonomy and tier transitions."""

    def test_new_pattern_created_as_probationary(self, seeded_db):
        """First-time sub_cluster creates a probationary pattern."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        scan_id = _make_scan_id()
        _insert_scan(seeded_db, scan_id)

        for i in range(3):
            _insert_classification(
                seeded_db, scan_id, f"T-new-{i}", "TRC-100",
                "brand new issue",
            )
        seeded_db.commit()

        analyzer._update_sub_taxonomy(scan_id)

        pat = seeded_db.conn.execute(
            "SELECT * FROM sub_patterns WHERE trc = 'TRC-100' AND label = 'brand new issue'"
        ).fetchone()
        assert pat is not None
        pat = dict(pat)
        assert pat["tier"] == "probationary"
        assert pat["lifetime_tickets"] == 3
        assert pat["lifetime_scans"] == 1

    def test_existing_pattern_updates_lifetime(self, seeded_db):
        """Seeing a known pattern again increments lifetime counters."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        scan_id = _make_scan_id()
        _insert_scan(seeded_db, scan_id)

        pid = _insert_sub_pattern(
            seeded_db, "TRC-100", "known issue",
            tier="active", lifetime_tickets=10, lifetime_scans=2,
        )

        for i in range(4):
            _insert_classification(
                seeded_db, scan_id, f"T-k-{i}", "TRC-100",
                "known issue",
            )
        seeded_db.commit()

        analyzer._update_sub_taxonomy(scan_id)

        pat = dict(seeded_db.conn.execute(
            "SELECT * FROM sub_patterns WHERE pattern_id = ?", (pid,)
        ).fetchone())
        assert pat["lifetime_tickets"] == 14
        assert pat["lifetime_scans"] == 3

    def test_promotion_probationary_to_active(self, seeded_db):
        """Pattern with 2+ scans and 10+ tickets promotes to active."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        scan_id = _make_scan_id()
        _insert_scan(seeded_db, scan_id)

        _insert_sub_pattern(
            seeded_db, "TRC-200", "ready to promote",
            tier="probationary", lifetime_tickets=12, lifetime_scans=2,
        )

        analyzer._promote_patterns(scan_id)

        pat = dict(seeded_db.conn.execute(
            "SELECT * FROM sub_patterns WHERE label = 'ready to promote'"
        ).fetchone())
        assert pat["tier"] == "active"

    def test_no_promotion_if_insufficient_scans(self, seeded_db):
        """Pattern with only 1 scan stays probationary even with 10+ tickets."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        scan_id = _make_scan_id()
        _insert_scan(seeded_db, scan_id)

        _insert_sub_pattern(
            seeded_db, "TRC-200", "too few scans",
            tier="probationary", lifetime_tickets=15, lifetime_scans=1,
        )

        analyzer._promote_patterns(scan_id)

        pat = dict(seeded_db.conn.execute(
            "SELECT * FROM sub_patterns WHERE label = 'too few scans'"
        ).fetchone())
        assert pat["tier"] == "probationary"

    def test_demotion_active_to_dormant(self, seeded_db):
        """Active pattern with < 10 tickets in last 3 scans gets demoted."""
        analyzer = NLPMetaAnalyzer(seeded_db)

        # Create 3 completed scans
        scan_ids = []
        for i in range(3):
            sid = _make_scan_id()
            _insert_scan(seeded_db, sid, status="analysis_complete")
            scan_ids.append(sid)

        pid = _insert_sub_pattern(
            seeded_db, "TRC-100", "fading pattern",
            tier="active", lifetime_tickets=50, lifetime_scans=5,
        )

        # Create snapshots with very low ticket counts (total < 10)
        for sid in scan_ids:
            seeded_db.conn.execute("""
                INSERT INTO sub_pattern_snapshots
                    (pattern_id, scan_id, scan_date_start, scan_date_end,
                     ticket_count, pct_of_trc)
                VALUES (?, ?, '2026-01-01', '2026-01-31', 2, 0.01)
            """, (pid, sid))
        seeded_db.commit()

        analyzer._demote_patterns(scan_ids[-1])

        pat = dict(seeded_db.conn.execute(
            "SELECT * FROM sub_patterns WHERE pattern_id = ?", (pid,)
        ).fetchone())
        assert pat["tier"] == "dormant"

    def test_no_demotion_with_insufficient_scan_history(self, seeded_db):
        """Demotion requires 3 completed scans; fewer => no demotion."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        sid = _make_scan_id()
        _insert_scan(seeded_db, sid, status="analysis_complete")

        _insert_sub_pattern(
            seeded_db, "TRC-100", "safe pattern",
            tier="active", lifetime_tickets=5, lifetime_scans=1,
        )

        analyzer._demote_patterns(sid)

        pat = dict(seeded_db.conn.execute(
            "SELECT * FROM sub_patterns WHERE label = 'safe pattern'"
        ).fetchone())
        assert pat["tier"] == "active"

    def test_retire_old_dormant(self, seeded_db):
        """Dormant pattern with < 5 lifetime and not seen in 180 days => retired."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        scan_id = _make_scan_id()
        _insert_scan(seeded_db, scan_id)

        old_date = (datetime.now() - timedelta(days=200)).isoformat()
        _insert_sub_pattern(
            seeded_db, "TRC-300", "ancient pattern",
            tier="dormant", lifetime_tickets=3, lifetime_scans=1,
            last_seen_at=old_date,
        )

        analyzer._retire_patterns(scan_id)

        pat = dict(seeded_db.conn.execute(
            "SELECT * FROM sub_patterns WHERE label = 'ancient pattern'"
        ).fetchone())
        assert pat["tier"] == "retired"

    def test_dormant_not_retired_if_enough_tickets(self, seeded_db):
        """Dormant pattern with >= 5 lifetime tickets is NOT retired."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        scan_id = _make_scan_id()
        _insert_scan(seeded_db, scan_id)

        old_date = (datetime.now() - timedelta(days=200)).isoformat()
        _insert_sub_pattern(
            seeded_db, "TRC-300", "robust dormant",
            tier="dormant", lifetime_tickets=10, lifetime_scans=2,
            last_seen_at=old_date,
        )

        analyzer._retire_patterns(scan_id)

        pat = dict(seeded_db.conn.execute(
            "SELECT * FROM sub_patterns WHERE label = 'robust dormant'"
        ).fetchone())
        assert pat["tier"] == "dormant"

    def test_reactivate_dormant_via_fuzzy_match(self, seeded_db):
        """Dormant pattern reactivated when a similar label appears (> 0.8 similarity)."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        scan_id = _make_scan_id()
        _insert_scan(seeded_db, scan_id)

        pid = _insert_sub_pattern(
            seeded_db, "TRC-100", "billing confusion issue",
            tier="dormant", lifetime_tickets=8, lifetime_scans=2,
        )

        # Very similar label triggers reactivation
        reactivated = analyzer._try_reactivate(
            "TRC-100", "billing confusion issues", scan_id, 5,
            datetime.now().isoformat()
        )

        assert reactivated is True
        pat = dict(seeded_db.conn.execute(
            "SELECT * FROM sub_patterns WHERE pattern_id = ?", (pid,)
        ).fetchone())
        assert pat["tier"] == "active"

    def test_no_reactivation_low_similarity(self, seeded_db):
        """No reactivation when label similarity <= 0.8."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        scan_id = _make_scan_id()
        _insert_scan(seeded_db, scan_id)

        _insert_sub_pattern(
            seeded_db, "TRC-100", "billing confusion",
            tier="dormant", lifetime_tickets=8, lifetime_scans=2,
        )

        reactivated = analyzer._try_reactivate(
            "TRC-100", "login timeout error", scan_id, 5,
            datetime.now().isoformat()
        )
        assert reactivated is False

    def test_merged_pattern_ignored(self, seeded_db):
        """Patterns with merged_into set are skipped in reactivation."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        scan_id = _make_scan_id()
        _insert_scan(seeded_db, scan_id)

        _insert_sub_pattern(
            seeded_db, "TRC-100", "billing confusion issue",
            tier="dormant", lifetime_tickets=8, lifetime_scans=2,
            merged_into="some-other-pattern-id",
        )

        reactivated = analyzer._try_reactivate(
            "TRC-100", "billing confusion issues", scan_id, 5,
            datetime.now().isoformat()
        )
        assert reactivated is False


# ═══════════════════════════════════════════════════════════════════
#  SNAPSHOTS
# ═══════════════════════════════════════════════════════════════════

class TestSnapshots:
    """Tests for _create_snapshots."""

    def test_snapshot_created_for_matching_pattern(self, seeded_db):
        """Snapshot is created when a sub_pattern matches classifications."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        scan_id = _make_scan_id()
        _insert_scan(seeded_db, scan_id)

        pid = _insert_sub_pattern(seeded_db, "TRC-100", "payment failure")

        for i in range(5):
            _insert_classification(
                seeded_db, scan_id, f"T-snap-{i}", "TRC-100",
                "payment failure", sentiment_polarity="negative",
                friction_type="process_gap",
            )
        seeded_db.commit()

        analyzer._create_snapshots(scan_id)

        snap = seeded_db.conn.execute(
            "SELECT * FROM sub_pattern_snapshots WHERE pattern_id = ? AND scan_id = ?",
            (pid, scan_id)
        ).fetchone()
        assert snap is not None
        snap = dict(snap)
        assert snap["ticket_count"] == 5
        assert snap["pct_of_trc"] == 1.0  # all 5 of 5 in TRC-100

    def test_snapshot_sentiment_distribution(self, seeded_db):
        """Snapshot captures sentiment polarity distribution as JSON."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        scan_id = _make_scan_id()
        _insert_scan(seeded_db, scan_id)

        pid = _insert_sub_pattern(seeded_db, "TRC-200", "login issue")

        _insert_classification(seeded_db, scan_id, "T-s1", "TRC-200",
                               "login issue", sentiment_polarity="negative")
        _insert_classification(seeded_db, scan_id, "T-s2", "TRC-200",
                               "login issue", sentiment_polarity="negative")
        _insert_classification(seeded_db, scan_id, "T-s3", "TRC-200",
                               "login issue", sentiment_polarity="neutral")
        seeded_db.commit()

        analyzer._create_snapshots(scan_id)

        snap = dict(seeded_db.conn.execute(
            "SELECT * FROM sub_pattern_snapshots WHERE pattern_id = ? AND scan_id = ?",
            (pid, scan_id)
        ).fetchone())
        sent_dist = json.loads(snap["sentiment_dist"])
        assert sent_dist["negative"] == 2
        assert sent_dist["neutral"] == 1

    def test_no_snapshot_for_merged_pattern(self, seeded_db):
        """Merged patterns are excluded from snapshot creation."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        scan_id = _make_scan_id()
        _insert_scan(seeded_db, scan_id)

        pid = _insert_sub_pattern(
            seeded_db, "TRC-100", "merged away",
            merged_into="some-other-id",
        )

        for i in range(3):
            _insert_classification(
                seeded_db, scan_id, f"T-mg-{i}", "TRC-100",
                "merged away",
            )
        seeded_db.commit()

        analyzer._create_snapshots(scan_id)

        snap = seeded_db.conn.execute(
            "SELECT * FROM sub_pattern_snapshots WHERE pattern_id = ? AND scan_id = ?",
            (pid, scan_id)
        ).fetchone()
        assert snap is None


# ═══════════════════════════════════════════════════════════════════
#  CROSS-TRC ANALYSIS
# ═══════════════════════════════════════════════════════════════════

class TestCrossTRCAnalysis:
    """Tests for _analyze_cross_trc."""

    def test_entity_spanning_3_trcs_creates_finding(self, seeded_db):
        """Payer appearing in 3+ TRCs creates a cross_trc finding."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        scan_id = _make_scan_id()
        _insert_scan(seeded_db, scan_id)

        for i, trc in enumerate(["TRC-100", "TRC-200", "TRC-300"]):
            _insert_classification(
                seeded_db, scan_id, f"T-cross-{i}", trc,
                "some issue",
                entities_json=json.dumps({"payer": "Acme Corp"}),
            )
        seeded_db.commit()

        analyzer._analyze_cross_trc(scan_id)

        findings = _get_findings(seeded_db, scan_id, "cross_trc")
        payer_findings = [f for f in findings if "Acme Corp" in f["title"]]
        assert len(payer_findings) == 1

    def test_entity_in_2_trcs_no_finding(self, seeded_db):
        """Payer in only 2 TRCs does not trigger a cross_trc finding."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        scan_id = _make_scan_id()
        _insert_scan(seeded_db, scan_id)

        for i, trc in enumerate(["TRC-100", "TRC-200"]):
            _insert_classification(
                seeded_db, scan_id, f"T-2trc-{i}", trc,
                "some issue",
                entities_json=json.dumps({"payer": "TwoCorp"}),
            )
        seeded_db.commit()

        analyzer._analyze_cross_trc(scan_id)

        findings = _get_findings(seeded_db, scan_id, "cross_trc")
        payer_findings = [f for f in findings if "TwoCorp" in (f.get("title") or "")]
        assert len(payer_findings) == 0

    def test_friction_spanning_3_trcs_creates_finding(self, seeded_db):
        """Friction type in 3+ TRCs (not 'other') creates cross_trc finding."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        scan_id = _make_scan_id()
        _insert_scan(seeded_db, scan_id)

        for i, trc in enumerate(["TRC-100", "TRC-200", "TRC-300"]):
            _insert_classification(
                seeded_db, scan_id, f"T-fric-{i}", trc,
                "cluster", friction_type="escalation_demand",
            )
        seeded_db.commit()

        analyzer._analyze_cross_trc(scan_id)

        findings = _get_findings(seeded_db, scan_id, "cross_trc")
        fric_findings = [f for f in findings if "escalation_demand" in (f.get("title") or "")]
        assert len(fric_findings) == 1

    def test_friction_other_excluded(self, seeded_db):
        """Friction type 'other' is excluded from cross-TRC findings."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        scan_id = _make_scan_id()
        _insert_scan(seeded_db, scan_id)

        for i, trc in enumerate(["TRC-100", "TRC-200", "TRC-300"]):
            _insert_classification(
                seeded_db, scan_id, f"T-oth-{i}", trc,
                "cluster", friction_type="other",
            )
        seeded_db.commit()

        analyzer._analyze_cross_trc(scan_id)

        findings = _get_findings(seeded_db, scan_id, "cross_trc")
        other_findings = [f for f in findings
                         if f.get("dominant_friction_type") == "other"]
        assert len(other_findings) == 0


# ═══════════════════════════════════════════════════════════════════
#  IMPACT SCORING
# ═══════════════════════════════════════════════════════════════════

class TestImpactScoring:
    """Tests for _compute_impact_score and _rank_findings."""

    def test_escalation_demand_boost(self, seeded_db):
        """Escalation demand friction type gets a 1.5x boost."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        base = {
            "pct_of_scanned": 0.5,
            "avg_sentiment_intensity": 4,
            "ticket_count": 50,
            "dominant_friction_type": "process_gap",
        }
        boosted = dict(base)
        boosted["dominant_friction_type"] = "escalation_demand"

        score_base = analyzer._compute_impact_score(base)
        score_boosted = analyzer._compute_impact_score(boosted)
        assert score_boosted == pytest.approx(score_base * 1.5)

    def test_zero_pct_yields_zero_score(self, seeded_db):
        """Finding with 0% of scanned tickets gets a zero impact score."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        score = analyzer._compute_impact_score({
            "pct_of_scanned": 0,
            "avg_sentiment_intensity": 5,
            "ticket_count": 10,
            "dominant_friction_type": "process_gap",
        })
        assert score == 0.0


# ═══════════════════════════════════════════════════════════════════
#  N-GRAM UPSERT
# ═══════════════════════════════════════════════════════════════════

class TestNgramUpsert:
    """Tests for _upsert_ngram."""

    def test_insert_new_ngram(self, seeded_db):
        """New n-gram is inserted with correct fields."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        pid = _insert_sub_pattern(seeded_db, "TRC-100", "test pattern")
        now = datetime.now().isoformat()

        analyzer._upsert_ngram(pid, "TRC-100", "billing error", 2,
                               "gemini", 5, 5, now)
        seeded_db.commit()

        row = dict(seeded_db.conn.execute(
            "SELECT * FROM sub_pattern_ngrams WHERE pattern_id = ? AND ngram = ?",
            (pid, "billing error")
        ).fetchone())
        assert row["frequency"] == 5
        assert row["n"] == 2
        assert row["source"] == "gemini"

    def test_upsert_existing_ngram_increments(self, seeded_db):
        """Second upsert increments frequency and ticket_count."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        pid = _insert_sub_pattern(seeded_db, "TRC-100", "test pattern")
        now = datetime.now().isoformat()

        analyzer._upsert_ngram(pid, "TRC-100", "billing error", 2,
                               "gemini", 3, 3, now)
        analyzer._upsert_ngram(pid, "TRC-100", "billing error", 2,
                               "gemini", 2, 2, now)
        seeded_db.commit()

        row = dict(seeded_db.conn.execute(
            "SELECT * FROM sub_pattern_ngrams WHERE pattern_id = ? AND ngram = ?",
            (pid, "billing error")
        ).fetchone())
        assert row["frequency"] == 5
        assert row["ticket_count"] == 5

    def test_upsert_different_source_sets_both(self, seeded_db):
        """Inserting same n-gram from different source changes source to 'both'."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        pid = _insert_sub_pattern(seeded_db, "TRC-100", "test pattern")
        now = datetime.now().isoformat()

        analyzer._upsert_ngram(pid, "TRC-100", "billing error", 2,
                               "gemini", 3, 3, now)
        analyzer._upsert_ngram(pid, "TRC-100", "billing error", 2,
                               "tfidf", 2, 2, now)
        seeded_db.commit()

        row = dict(seeded_db.conn.execute(
            "SELECT * FROM sub_pattern_ngrams WHERE pattern_id = ? AND ngram = ?",
            (pid, "billing error")
        ).fetchone())
        assert row["source"] == "both"


# ═══════════════════════════════════════════════════════════════════
#  FULL RUN_ANALYSIS
# ═══════════════════════════════════════════════════════════════════

class TestRunAnalysis:
    """Integration-style tests for run_analysis end-to-end."""

    def test_empty_scan_completes(self, seeded_db):
        """Scan with no classifications still reaches analysis_complete."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        scan_id = _make_scan_id()
        _insert_scan(seeded_db, scan_id)

        with patch.object(analyzer, "_update_ngrams"):
            analyzer.run_analysis(scan_id)

        status = seeded_db.conn.execute(
            "SELECT status FROM nlp_scan_runs WHERE scan_id = ?", (scan_id,)
        ).fetchone()[0]
        assert status == "analysis_complete"

    def test_failed_analysis_sets_failed_status(self, seeded_db):
        """Exception during analysis sets status to 'failed'."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        scan_id = _make_scan_id()
        _insert_scan(seeded_db, scan_id)

        with patch.object(analyzer, "_get_scan_trcs", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                analyzer.run_analysis(scan_id)

        status = seeded_db.conn.execute(
            "SELECT status FROM nlp_scan_runs WHERE scan_id = ?", (scan_id,)
        ).fetchone()[0]
        assert status == "failed"

    def test_run_analysis_sets_analyzing_then_complete(self, seeded_db):
        """run_analysis transitions status: scan_complete -> analyzing -> analysis_complete."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        scan_id = _make_scan_id()
        _insert_scan(seeded_db, scan_id)

        statuses_seen = []
        original_get_trcs = analyzer._get_scan_trcs

        def capture_status(sid):
            s = seeded_db.conn.execute(
                "SELECT status FROM nlp_scan_runs WHERE scan_id = ?", (sid,)
            ).fetchone()[0]
            statuses_seen.append(s)
            return original_get_trcs(sid)

        with patch.object(analyzer, "_get_scan_trcs", side_effect=capture_status):
            with patch.object(analyzer, "_update_ngrams"):
                analyzer.run_analysis(scan_id)

        assert "analyzing" in statuses_seen

    def test_prune_provisional_cleans_old_confirmed(self, seeded_db):
        """_prune_provisional deletes confirmed provisional classifications older than 30 days."""
        analyzer = NLPMetaAnalyzer(seeded_db)
        scan_id = _make_scan_id()
        _insert_scan(seeded_db, scan_id)

        old_date = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
        seeded_db.conn.execute("""
            INSERT INTO provisional_classifications
                (ticket_id, trc, confirmed_by_scan, created_at)
            VALUES ('T-old', 'TRC-100', ?, ?)
        """, (scan_id, old_date))
        seeded_db.commit()

        analyzer._prune_provisional(scan_id)

        count = seeded_db.conn.execute(
            "SELECT COUNT(*) FROM provisional_classifications WHERE ticket_id = 'T-old'"
        ).fetchone()[0]
        assert count == 0
