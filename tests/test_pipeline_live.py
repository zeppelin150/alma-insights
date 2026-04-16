"""
Alma Insights -- Live Integration Test (Pass 5.0)

End-to-end test that boots a REAL bridge, sends a REAL prompt to Gemini,
parses a REAL streaming response, and stores REAL classifications.

GATED: Only runs when invoked with --live flag or ALMA_LIVE_TEST=1 env var.
Standard test runners skip this entirely.

  python tests/test_pipeline_live.py --live          # run all live tests
  python tests/test_pipeline_live.py --live -v       # verbose
  set ALMA_LIVE_TEST=1 && python -m unittest tests.test_pipeline_live -v

Prerequisites:
  - Node.js installed and on PATH
  - Gemini CLI installed (npm i -g @google/gemini-cli)
  - Gemini auth configured (OAuth or API key)
  - Network access to Gemini API

What this exercises:
  1. GeminiBridge boots Node subprocess (gemini_bridge.mjs)
  2. Bridge ping health check
  3. WorkerAgent builds prompt from template + synthetic tickets
  4. Bridge sends streaming call to Gemini API
  5. StreamParser extracts fenced code blocks from live response
  6. ToolRegistry stores classifications to SQLite (per-ticket)
  7. Field validation (sentiment 1-5, friction types, polarity)
  8. Full thread retrieval via get_full_thread tool
  9. Bridge shutdown and cleanup
"""

import json
import os
import shutil
import sqlite3
import sys
import time
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# ── Gate: skip unless --live or ALMA_LIVE_TEST=1 ──
LIVE_MODE = (
    "--live" in sys.argv
    or os.environ.get("ALMA_LIVE_TEST", "") == "1"
)

if "--live" in sys.argv:
    sys.argv.remove("--live")


def skip_unless_live(cls):
    """Class decorator: skip entire test class unless live mode."""
    if not LIVE_MODE:
        return unittest.skip("Live tests require --live flag")(cls)
    return cls


def _get_model_from_settings():
    """Read model from settings.yaml — same key the Settings UI writes to."""
    settings_path = _PROJECT_ROOT / "config" / "settings.yaml"
    try:
        import yaml
        with open(settings_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        return cfg.get("gemini", {}).get("model", "gemini-2.5-flash")
    except Exception:
        return "gemini-2.5-flash"


# ═══════════════════════════════════════════════════════════════════════════
# SYNTHETIC TEST DATA
# ═══════════════════════════════════════════════════════════════════════════

SYNTHETIC_TICKETS = [
    {
        "ticket_id": "LIVE-001",
        "trc": "RCM_02",
        "full_thread": (
            "[Client] I was charged twice for the same procedure on 1/15. "
            "My EOB shows two line items for CPT 99213 but I only had one visit. "
            "The duplicate charge is $185.00. Please correct this.\n"
            "[Agent] I see the duplicate charge on your account. Let me review "
            "the claim details and submit a correction."
        ),
    },
    {
        "ticket_id": "LIVE-002",
        "trc": "RCM_02",
        "full_thread": (
            "[Client] My insurance was supposed to cover this visit at 100% "
            "but I received a bill for $450. The plan document clearly states "
            "preventive care is fully covered.\n"
            "[Agent] I apologize for the confusion. Let me check the coding "
            "on this claim to ensure it was submitted as preventive care."
        ),
    },
    {
        "ticket_id": "LIVE-003",
        "trc": "RCM_02",
        "full_thread": (
            "[Client] I need to understand why my copay increased from $25 to $50. "
            "Nobody informed me of this change. I've been a member for 3 years.\n"
            "[Agent] Plan benefits were updated at the start of the new plan year. "
            "A notice was mailed to all members in December."
        ),
    },
    {
        "ticket_id": "LIVE-004",
        "trc": "RCM_02",
        "full_thread": (
            "[Client] The portal shows my claim was denied but the denial reason "
            "code doesn't make sense. It says 'timely filing' but the provider "
            "submitted it within 30 days of service.\n"
            "[Agent] Let me investigate the submission date on our end. There may "
            "have been a system processing delay."
        ),
    },
    {
        "ticket_id": "LIVE-005",
        "trc": "RCM_02",
        "full_thread": (
            "[Client] Great experience with the billing team today. Sarah helped "
            "me understand my EOB and corrected an error within 5 minutes. "
            "Very impressed with the turnaround time.\n"
            "[Agent] Thank you for the positive feedback! I'll make sure to share "
            "this with Sarah's team lead."
        ),
    },
    {
        "ticket_id": "LIVE-006",
        "trc": "RCM_02",
        "full_thread": (
            "[Client] I submitted an appeal 45 days ago and haven't heard back. "
            "The appeal was for a denied prior authorization for an MRI. "
            "My doctor says this is medically necessary.\n"
            "[Agent] I see your appeal is still in review. Let me escalate this "
            "to the appeals team for expedited processing."
        ),
    },
    {
        "ticket_id": "LIVE-007",
        "trc": "RCM_02",
        "full_thread": (
            "[Client] The automated phone system keeps disconnecting me after "
            "10 minutes on hold. I've tried calling 4 times today. I just need "
            "to verify my deductible balance.\n"
            "[Agent] I apologize for the phone system issues. Your current "
            "deductible balance is $1,250 of $3,000 met."
        ),
    },
    {
        "ticket_id": "LIVE-008",
        "trc": "RCM_02",
        "full_thread": (
            "[Client] I received a bill from a provider I've never visited. "
            "The charge is for a cardiology consultation on 2/1 but I was not "
            "at any medical facility that day. This looks like fraud.\n"
            "[Agent] This is concerning. I'm flagging this for our fraud "
            "investigation team immediately. We'll place a hold on this charge."
        ),
    },
    {
        "ticket_id": "LIVE-009",
        "trc": "RCM_02",
        "full_thread": (
            "[Client] My provider says they can't verify my eligibility through "
            "the portal. The real-time eligibility check returns an error every "
            "time. This has been happening for 2 weeks.\n"
            "[Agent] I see there's a known issue with the eligibility verification "
            "system for your plan type. Engineering is working on a fix."
        ),
    },
    {
        "ticket_id": "LIVE-010",
        "trc": "RCM_02",
        "full_thread": (
            "[Client] I paid my premium on time but received a termination notice "
            "saying my coverage was cancelled for non-payment. I have the bank "
            "statement showing the payment cleared on the 1st.\n"
            "[Agent] I can see the payment was received. This appears to be a "
            "system error. I'm reinstating your coverage immediately."
        ),
    },
]

# ── Valid field values for assertions ──
from src.agents.tool_registry import VALID_FRICTION, VALID_POLARITY, VALID_ANOMALY


# ═══════════════════════════════════════════════════════════════════════════
# DATABASE SETUP
# ═══════════════════════════════════════════════════════════════════════════

def create_live_test_db(db_path):
    """Create a temp SQLite DB with schema + synthetic data."""
    # Remove stale DB from previous runs (avoids UNIQUE constraint violations)
    try:
        os.unlink(db_path)
    except OSError:
        pass
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Full schema from db_manager.py (core + NLP + agent tables)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
            ticket_id       TEXT PRIMARY KEY,
            subject         TEXT,
            trc_code        TEXT,
            trc_label       TEXT,
            status          TEXT,
            csat_score      REAL,
            created_at      TEXT,
            solved_at       TEXT,
            message_count   INTEGER,
            client_messages INTEGER,
            agent_messages  INTEGER,
            full_thread     TEXT,
            thread_preview  TEXT,
            dataset_id      INTEGER DEFAULT 0,
            content_hash    TEXT
        );

        CREATE TABLE IF NOT EXISTS sub_patterns (
            pattern_id TEXT PRIMARY KEY,
            trc TEXT,
            label TEXT,
            description TEXT,
            friction_type TEXT,
            tier TEXT DEFAULT 'active',
            lifetime_tickets INTEGER DEFAULT 0,
            merged_into TEXT
        );

        CREATE TABLE IF NOT EXISTS sub_pattern_ngrams (
            ngram_id INTEGER PRIMARY KEY,
            pattern_id TEXT,
            ngram TEXT,
            frequency REAL DEFAULT 0,
            specificity REAL DEFAULT 0.5
        );

        CREATE TABLE IF NOT EXISTS incident_flags (
            flag_id INTEGER PRIMARY KEY,
            trc_code TEXT,
            flag_type TEXT,
            flagged_date TEXT,
            description TEXT,
            status TEXT DEFAULT 'open'
        );

        CREATE TABLE IF NOT EXISTS anomaly_flags (
            id INTEGER PRIMARY KEY,
            trc_code TEXT,
            metric_type TEXT,
            date TEXT,
            direction TEXT
        );

        CREATE TABLE IF NOT EXISTS tfidf_scores (
            id INTEGER PRIMARY KEY,
            trc TEXT,
            term TEXT,
            score REAL
        );

        CREATE TABLE IF NOT EXISTS nlp_ticket_classifications (
            classification_id TEXT PRIMARY KEY,
            batch_id TEXT,
            scan_id TEXT,
            ticket_id TEXT,
            trc TEXT,
            sub_cluster TEXT,
            sub_cluster_confidence REAL,
            is_novel INTEGER DEFAULT 0,
            sentiment_intensity INTEGER,
            sentiment_polarity TEXT,
            friction_type TEXT,
            anomaly_flag TEXT,
            anomaly_reason TEXT,
            entities_json TEXT,
            key_phrases TEXT,
            root_cause_hint TEXT,
            summary TEXT,
            raw_classification TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS nlp_scan_runs (
            scan_id TEXT PRIMARY KEY,
            created_at TEXT,
            status TEXT,
            date_range_start TEXT,
            date_range_end TEXT,
            trc_filter TEXT,
            mode TEXT,
            batch_strategy TEXT,
            total_batches INTEGER,
            total_tickets INTEGER,
            completed_batches INTEGER DEFAULT 0,
            estimated_cost_usd REAL,
            actual_cost_usd REAL DEFAULT 0,
            budget_cap_usd REAL,
            config_snapshot TEXT,
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS nlp_batches (
            batch_id TEXT PRIMARY KEY,
            scan_id TEXT,
            batch_number INTEGER,
            trc TEXT,
            trc_chunk INTEGER,
            trc_chunk_total INTEGER,
            status TEXT,
            worker_id TEXT,
            created_at TEXT,
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS agent_health (
            agent_id TEXT PRIMARY KEY,
            scan_id TEXT,
            status TEXT DEFAULT 'idle',
            batches_done INTEGER DEFAULT 0,
            tickets_done INTEGER DEFAULT 0,
            tool_calls INTEGER DEFAULT 0,
            parse_rate REAL DEFAULT 1.0,
            avg_confidence REAL DEFAULT 0.0,
            context_tokens INTEGER DEFAULT 0,
            last_progress TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS scan_progress (
            scan_id TEXT PRIMARY KEY,
            classified INTEGER DEFAULT 0,
            total INTEGER DEFAULT 0,
            tool_calls INTEGER DEFAULT 0,
            batches_complete INTEGER DEFAULT 0,
            est_remaining_seconds REAL,
            est_confidence TEXT DEFAULT 'low',
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS review_flags (
            flag_id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            severity TEXT DEFAULT 'medium',
            agent_id TEXT,
            scan_id TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS analyst_reports (
            report_id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id TEXT NOT NULL,
            report_type TEXT NOT NULL,
            content TEXT,
            metrics TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS trc_batch_profiles (
            trc TEXT PRIMARY KEY,
            avg_chars_per_ticket REAL,
            updated_at TEXT
        );
    """)

    # Seed synthetic conversations
    for t in SYNTHETIC_TICKETS:
        conn.execute("""
            INSERT INTO conversations
                (ticket_id, trc_code, full_thread, created_at,
                 message_count, client_messages, agent_messages,
                 thread_preview, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            t["ticket_id"],
            t["trc"],
            t["full_thread"],
            "2025-10-20 10:00:00",
            2,   # message_count
            1,   # client_messages
            1,   # agent_messages
            t["full_thread"][:200],
            "solved",
        ))

    # Seed one existing sub-pattern so taxonomy tool has something to return
    conn.execute("""
        INSERT INTO sub_patterns
            (pattern_id, trc, label, description, friction_type,
             tier, lifetime_tickets)
        VALUES ('sp_test_1', 'RCM_02', 'Duplicate charge confusion',
                'Customer reports duplicate billing line items',
                'incorrect_charge', 'active', 25)
    """)
    conn.execute("""
        INSERT INTO sub_pattern_ngrams
            (pattern_id, ngram, frequency, specificity)
        VALUES ('sp_test_1', 'duplicate charge', 0.85, 0.92)
    """)
    conn.execute("""
        INSERT INTO sub_pattern_ngrams
            (pattern_id, ngram, frequency, specificity)
        VALUES ('sp_test_1', 'charged twice', 0.70, 0.88)
    """)

    conn.commit()
    return conn


# ═══════════════════════════════════════════════════════════════════════════
# PREFLIGHT CHECKS
# ═══════════════════════════════════════════════════════════════════════════

def check_node_available():
    """Return True if Node.js is on PATH."""
    return shutil.which("node") is not None


def check_bridge_exists():
    """Return True if gemini_bridge.mjs exists."""
    bridge = _PROJECT_ROOT / "src" / "gemini" / "gemini_bridge.mjs"
    return bridge.exists()


# ═══════════════════════════════════════════════════════════════════════════
# LIVE INTEGRATION TESTS
# ═══════════════════════════════════════════════════════════════════════════

@skip_unless_live
class TestLiveBridge(unittest.TestCase):
    """Test real bridge boot, ping, and shutdown."""

    @classmethod
    def setUpClass(cls):
        if not check_node_available():
            raise unittest.SkipTest("Node.js not found on PATH")
        if not check_bridge_exists():
            raise unittest.SkipTest("gemini_bridge.mjs not found")

    def test_01_bridge_boot_and_ping(self):
        """Boot bridge, ping, verify alive, shutdown."""
        from src.agents.acp_bridge import ACPBridge as GeminiBridge  # ACP migration

        bridge = GeminiBridge(model=_get_model_from_settings())
        try:
            bridge.ensure_running()
            self.assertTrue(bridge.is_alive(), "Bridge should be alive after boot")

            # Ping (ACP returns synthetic status — no uptime_ms)
            ping = bridge.ping(timeout=30)
            self.assertIsNotNone(ping, "Ping should return a response")
            self.assertEqual(ping.get("status"), "ok")
            self.assertTrue(ping.get("alive"))
            self.assertIn("session_id", ping)

            print(f"\n  ACP bridge: protocol={ping.get('protocol_version', '?')}")
            print(f"  Session: {ping.get('session_id', '?')}")

        finally:
            bridge.shutdown()
            self.assertFalse(bridge.is_alive(), "Bridge should be dead after shutdown")


@skip_unless_live
class TestLiveClassification(unittest.TestCase):
    """
    Full end-to-end: boot bridge, classify 10 tickets, verify storage.

    This is the core integration test. It exercises:
      - GeminiBridge subprocess management
      - WorkerAgent prompt building
      - Gemini API streaming call
      - StreamParser fenced block extraction
      - ToolRegistry store_classification with validation
      - SQLite per-ticket persistence
    """

    @classmethod
    def setUpClass(cls):
        if not check_node_available():
            raise unittest.SkipTest("Node.js not found on PATH")
        if not check_bridge_exists():
            raise unittest.SkipTest("gemini_bridge.mjs not found")

        # Create temp DB
        cls.db_path = str(_PROJECT_ROOT / "tests" / "_live_test.db")
        cls.conn = create_live_test_db(cls.db_path)

        # Boot bridge with model from settings
        from src.agents.acp_bridge import ACPBridge as GeminiBridge  # ACP migration
        cls.bridge = GeminiBridge(model=_get_model_from_settings())
        try:
            cls.bridge.ensure_running()
        except Exception as e:
            cls.conn.close()
            try:
                os.unlink(cls.db_path)
            except OSError:
                pass
            raise unittest.SkipTest(f"Bridge boot failed: {e}")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'bridge') and cls.bridge:
            cls.bridge.shutdown()
        if hasattr(cls, 'conn') and cls.conn:
            cls.conn.close()
        if hasattr(cls, 'db_path'):
            try:
                os.unlink(cls.db_path)
            except OSError:
                pass

    def test_01_classify_10_tickets(self):
        """Send 10 tickets through full pipeline, verify results."""
        from src.agents.worker_agent import WorkerAgent

        worker = WorkerAgent("live_worker_0", self.bridge, self.db_path)

        payload = {
            "scan_id": "live_scan_001",
            "batch_id": "live_batch_001",
            "trc": "RCM_02",
            "tickets": SYNTHETIC_TICKETS,
            "stats_context": "",
            "sub_taxonomy": "",
            "chunk_n": 1,
            "chunk_total": 1,
            "date_start": "2025-10-01",
            "date_end": "2025-10-31",
        }

        print("\n  Classifying 10 tickets via live Gemini API...")
        start = time.time()
        result = worker.classify_batch(payload)
        elapsed = time.time() - start

        print(f"  Result: {result['classified']}/{len(SYNTHETIC_TICKETS)} classified")
        print(f"  Failed: {result['failed']}")
        print(f"  Tool calls: {result['tool_calls']}")
        print(f"  Elapsed: {elapsed:.1f}s")
        print(f"  Error: {result['error']}")

        # ── Core assertions ──
        self.assertIsNone(
            result["error"],
            f"Classification should succeed, got: {result['error']}"
        )
        self.assertGreater(
            result["classified"], 0,
            "Should classify at least some tickets"
        )

        # Store result for subsequent tests
        self.__class__._result = result
        self.__class__._elapsed = elapsed

        worker.shutdown()

    def test_02_per_ticket_persistence(self):
        """Verify each classified ticket was stored individually in SQLite."""
        result = getattr(self.__class__, '_result', None)
        if result is None:
            self.skipTest("test_01 did not run")

        rows = self.conn.execute("""
            SELECT COUNT(*) as n FROM nlp_ticket_classifications
            WHERE scan_id = 'live_scan_001'
        """).fetchone()

        stored = rows["n"]
        print(f"\n  Stored in DB: {stored} classifications")

        self.assertEqual(
            stored, result["classified"],
            f"DB rows ({stored}) should match classified count ({result['classified']})"
        )

    def test_03_field_validation(self):
        """Verify stored classifications have valid field values."""
        rows = self.conn.execute("""
            SELECT * FROM nlp_ticket_classifications
            WHERE scan_id = 'live_scan_001'
        """).fetchall()

        if not rows:
            self.skipTest("No classifications stored")

        for row in rows:
            tid = row["ticket_id"]

            # Sentiment intensity: clamped 1-5
            si = row["sentiment_intensity"]
            self.assertGreaterEqual(si, 1, f"{tid}: sentiment_intensity {si} < 1")
            self.assertLessEqual(si, 5, f"{tid}: sentiment_intensity {si} > 5")

            # Sentiment polarity: valid enum
            sp = row["sentiment_polarity"]
            self.assertIn(
                sp, VALID_POLARITY,
                f"{tid}: invalid polarity '{sp}'"
            )

            # Friction type: valid enum
            ft = row["friction_type"]
            self.assertIn(
                ft, VALID_FRICTION,
                f"{tid}: invalid friction_type '{ft}'"
            )

            # Anomaly flag: valid or null
            af = row["anomaly_flag"]
            if af is not None:
                self.assertIn(
                    af, VALID_ANOMALY,
                    f"{tid}: invalid anomaly_flag '{af}'"
                )

            # Sub-cluster confidence: 0.0-1.0
            sc = row["sub_cluster_confidence"]
            self.assertGreaterEqual(sc, 0.0, f"{tid}: confidence {sc} < 0")
            self.assertLessEqual(sc, 1.0, f"{tid}: confidence {sc} > 1")

            # Sub-cluster label: non-empty
            self.assertTrue(
                row["sub_cluster"],
                f"{tid}: sub_cluster is empty"
            )

            # Scan/batch IDs echoed correctly
            self.assertEqual(row["scan_id"], "live_scan_001")
            self.assertEqual(row["batch_id"], "live_batch_001")
            self.assertEqual(row["trc"], "RCM_02")

        print(f"\n  All {len(rows)} classifications passed field validation")

    def test_04_ticket_ids_match(self):
        """Verify classified ticket IDs are a subset of input ticket IDs."""
        rows = self.conn.execute("""
            SELECT ticket_id FROM nlp_ticket_classifications
            WHERE scan_id = 'live_scan_001'
        """).fetchall()

        classified_ids = {r["ticket_id"] for r in rows}
        input_ids = {t["ticket_id"] for t in SYNTHETIC_TICKETS}

        # Every classified ID should be from our input set
        unexpected = classified_ids - input_ids
        self.assertEqual(
            len(unexpected), 0,
            f"Unexpected ticket IDs in classifications: {unexpected}"
        )

        print(f"\n  Classified IDs: {sorted(classified_ids)}")

        # Check coverage
        missing = input_ids - classified_ids
        if missing:
            print(f"  Missing IDs (not classified): {sorted(missing)}")

    def test_05_positive_feedback_detection(self):
        """LIVE-005 is positive feedback -- verify sentiment and friction."""
        row = self.conn.execute("""
            SELECT * FROM nlp_ticket_classifications
            WHERE ticket_id = 'LIVE-005' AND scan_id = 'live_scan_001'
        """).fetchone()

        if row is None:
            self.skipTest("LIVE-005 was not classified")

        # Positive feedback ticket should have high sentiment or positive polarity
        print(f"\n  LIVE-005 (positive feedback):")
        print(f"    sentiment: {row['sentiment_intensity']}/5 {row['sentiment_polarity']}")
        print(f"    friction: {row['friction_type']}")
        print(f"    sub_cluster: {row['sub_cluster']}")

        # At minimum, polarity should not be 'negative'
        self.assertNotEqual(
            row["sentiment_polarity"], "negative",
            "Positive feedback ticket should not have negative polarity"
        )

    def test_06_fraud_ticket_anomaly(self):
        """LIVE-008 describes potential fraud -- check anomaly detection."""
        row = self.conn.execute("""
            SELECT * FROM nlp_ticket_classifications
            WHERE ticket_id = 'LIVE-008' AND scan_id = 'live_scan_001'
        """).fetchone()

        if row is None:
            self.skipTest("LIVE-008 was not classified")

        print(f"\n  LIVE-008 (potential fraud):")
        print(f"    anomaly_flag: {row['anomaly_flag']}")
        print(f"    anomaly_reason: {row['anomaly_reason']}")
        print(f"    friction: {row['friction_type']}")
        print(f"    sub_cluster: {row['sub_cluster']}")

        # Fraud ticket should be flagged as unusual or critical
        # (not a hard failure if the LLM doesn't flag it, just a quality signal)
        if row["anomaly_flag"] in ("unusual", "critical"):
            print("    --> Anomaly detected (good)")
        else:
            print("    --> Not anomaly-flagged (quality note, not failure)")

    def test_07_duplicate_charge_matches_existing(self):
        """LIVE-001 matches existing 'Duplicate charge confusion' pattern."""
        row = self.conn.execute("""
            SELECT * FROM nlp_ticket_classifications
            WHERE ticket_id = 'LIVE-001' AND scan_id = 'live_scan_001'
        """).fetchone()

        if row is None:
            self.skipTest("LIVE-001 was not classified")

        print(f"\n  LIVE-001 (duplicate charge):")
        print(f"    sub_cluster: {row['sub_cluster']}")
        print(f"    is_novel: {row['is_novel']}")
        print(f"    confidence: {row['sub_cluster_confidence']}")

        # This ticket should ideally NOT be novel (matches existing pattern)
        # Not a hard failure -- informational
        if not row["is_novel"]:
            print("    --> Matched existing pattern (good)")
        else:
            print("    --> Marked novel despite existing pattern (quality note)")

    def test_08_full_thread_tool(self):
        """Verify get_full_thread tool works against live DB."""
        from src.agents.tool_registry import ToolRegistry

        registry = ToolRegistry(self.db_path)
        result = registry.execute("get_full_thread", {"ticket_id": "LIVE-001"})
        registry.close()

        self.assertEqual(result["ticket_id"], "LIVE-001")
        self.assertGreater(result["message_count"], 0)
        self.assertTrue(len(result["thread"]) > 0)

        print(f"\n  get_full_thread('LIVE-001'): {result['message_count']} messages")

    def test_09_performance_baseline(self):
        """Log performance metrics for comparison."""
        result = getattr(self.__class__, '_result', None)
        elapsed = getattr(self.__class__, '_elapsed', None)

        if result is None:
            self.skipTest("test_01 did not run")

        classified = result["classified"]
        tool_calls = result["tool_calls"]

        print("\n" + "=" * 60)
        print("  LIVE INTEGRATION TEST -- PERFORMANCE BASELINE")
        print("=" * 60)
        print(f"  Tickets sent:      {len(SYNTHETIC_TICKETS)}")
        print(f"  Classified:        {classified}")
        print(f"  Failed:            {result['failed']}")
        print(f"  Tool calls:        {tool_calls}")
        print(f"  Total time:        {elapsed:.1f}s")
        if classified > 0:
            print(f"  Per-ticket:        {elapsed/classified:.2f}s")
            print(f"  Throughput:        {classified/elapsed*60:.1f} tickets/min")
        print(f"  Error:             {result['error'] or 'None'}")
        print("=" * 60)


# ═══════════════════════════════════════════════════════════════════════════
# ENTRYPOINT
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if not LIVE_MODE:
        print(
            "\n  This test requires a live Gemini connection.\n"
            "  Run with: python tests/test_pipeline_live.py --live\n"
            "  Or set:   ALMA_LIVE_TEST=1\n"
        )
        sys.exit(0)

    print("\n" + "=" * 60)
    print("  ALMA INSIGHTS -- LIVE PIPELINE INTEGRATION TEST")
    print("  10 tickets, 1 batch, 1 worker, real Gemini API")
    print("=" * 60 + "\n")

    unittest.main(verbosity=2)
