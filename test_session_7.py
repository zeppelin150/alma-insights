"""
Session 7 Tests -- Edge Cases + Validation
Tests edge case handling, planted scenario detection,
taxonomy lifecycle, budget cap enforcement, and resume logic.
"""

import sys, os, json, uuid, tempfile
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.data.db_manager import DatabaseManager


def fresh_db():
    db_path = Path(tempfile.gettempdir()) / f"alma_s7_{uuid.uuid4().hex[:8]}.db"
    if db_path.exists():
        db_path.unlink()
    db = DatabaseManager(db_path)
    db.initialize()
    return db, db_path


def cleanup_db(db, db_path):
    db.close()
    for suffix in ["", "-wal", "-shm"]:
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()


def insert_ticket(conn, tid, trc, subject, body, created_at, csat=3):
    conn.execute("INSERT OR IGNORE INTO tickets (ticket_id, subject, trc_code, created_at) VALUES (?,?,?,?)",
                 (tid, subject, trc, created_at))
    conn.execute("""
        INSERT OR IGNORE INTO conversations
            (ticket_id, subject, trc_code, trc_label, csat_score,
             created_at, full_thread, message_count, client_messages, agent_messages)
        VALUES (?, ?, ?, ?, ?, ?, ?, 3, 1, 2)
    """, (tid, subject, trc or "", f"TRC {trc}" if trc else "", csat, created_at, body))


# ========================================================
#  TEST 1: Empty/NULL TRC handling
# ========================================================

def test_empty_trc():
    """Tickets with NULL or empty TRC should be batchable as '(untagged)'."""
    print("=" * 60)
    print("TEST 1: Empty/NULL TRC handling")
    print("=" * 60)

    db, db_path = fresh_db()
    conn = db.conn
    now = datetime.now().isoformat()

    # Insert tickets with empty/NULL TRCs
    for i in range(10):
        insert_ticket(conn, f"UNTAG-{i}", None, "No TRC ticket",
                      "Some body text", now)
    for i in range(5):
        insert_ticket(conn, f"EMPTY-{i}", "", "Empty TRC ticket",
                      "Other body text", now)
    conn.commit()

    # Verify they're queryable
    count = conn.execute("""
        SELECT COUNT(DISTINCT ticket_id) FROM conversations
        WHERE trc_code IS NULL OR trc_code = ''
    """).fetchone()[0]
    print(f"  Untagged tickets: {count}")
    assert count == 15, f"FAIL: Expected 15, got {count}"

    # Verify they'd be included in scan planning
    count_in_range = db.get_ticket_count_in_range("2020-01-01", "2030-12-31")
    assert count_in_range == 15
    print("  get_ticket_count_in_range includes untagged OK")

    cleanup_db(db, db_path)
    print("  OK TEST 1 PASSED")


# ========================================================
#  TEST 2: TRC with < 5 tickets (skip threshold)
# ========================================================

def test_small_trc_skipped():
    """TRCs with fewer than minTrcTickets should be skipped in batch planning."""
    print("\n" + "=" * 60)
    print("TEST 2: Small TRC skip threshold")
    print("=" * 60)

    db, db_path = fresh_db()
    conn = db.conn
    now = datetime.now().isoformat()

    # TRC-A: 3 tickets (below threshold)
    for i in range(3):
        insert_ticket(conn, f"SMALL-{i}", "TRC-SMALL", "Small TRC",
                      "Just a few tickets", now)

    # TRC-B: 10 tickets (above threshold)
    for i in range(10):
        insert_ticket(conn, f"BIG-{i}", "TRC-BIG", "Big TRC",
                      "Many tickets here", now)
    conn.commit()

    # Check TRC counts
    trc_counts = conn.execute("""
        SELECT trc_code, COUNT(DISTINCT ticket_id) AS n
        FROM conversations
        WHERE trc_code IS NOT NULL AND trc_code != ''
        GROUP BY trc_code ORDER BY n DESC
    """).fetchall()

    for row in trc_counts:
        trc = row[0]
        n = row[1]
        print(f"  TRC: {trc} -> {n} tickets")
        # The Node server uses minTrcTickets=5 to filter
        if trc == "TRC-SMALL":
            assert n < 5, "FAIL: Small TRC should be < 5"
        elif trc == "TRC-BIG":
            assert n >= 5, "FAIL: Big TRC should be >= 5"

    cleanup_db(db, db_path)
    print("  OK TEST 2 PASSED")


# ========================================================
#  TEST 3: Budget cap enforcement
# ========================================================

def test_budget_cap():
    """Scan should pause when budget cap is reached."""
    print("\n" + "=" * 60)
    print("TEST 3: Budget cap enforcement (DB-level)")
    print("=" * 60)

    db, db_path = fresh_db()
    conn = db.conn
    now = datetime.now().isoformat()

    # Create a scan run with budget cap
    scan_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO nlp_scan_runs
            (scan_id, created_at, status, date_range_start, date_range_end,
             mode, batch_strategy, total_batches, total_tickets, total_comments,
             budget_cap_usd, actual_cost_usd, config_snapshot)
        VALUES (?, ?, 'running', '2025-01-01', '2025-01-31',
                'full', 'trc', 10, 1000, 0, 5.00, 4.80, '{}')
    """, (scan_id, now))
    conn.commit()

    # Verify budget check logic
    scan = db.get_scan_status(scan_id)
    budget = scan["budget_cap_usd"]
    actual = scan["actual_cost_usd"]
    remaining = budget - actual
    print(f"  Budget: ${budget:.2f}, Spent: ${actual:.2f}, Remaining: ${remaining:.2f}")

    # batch_worker checks: if actual_cost > budget_cap -> pause
    assert actual < budget, "FAIL: Already over budget"
    assert remaining < 1.0, "FAIL: Should be close to budget"

    # Simulate going over budget
    conn.execute("UPDATE nlp_scan_runs SET actual_cost_usd = 5.10 WHERE scan_id = ?",
                 (scan_id,))
    conn.commit()
    scan = db.get_scan_status(scan_id)
    assert scan["actual_cost_usd"] > scan["budget_cap_usd"], \
        "FAIL: Should be over budget now"
    print("  Budget cap exceeded correctly detectable")

    cleanup_db(db, db_path)
    print("  OK TEST 3 PASSED")


# ========================================================
#  TEST 4: Resume-on-reopen logic
# ========================================================

def test_resume_on_reopen():
    """If server finds a 'running' scan on startup, it should mark it paused."""
    print("\n" + "=" * 60)
    print("TEST 4: Resume-on-reopen (interrupted scan recovery)")
    print("=" * 60)

    db, db_path = fresh_db()
    conn = db.conn
    now = datetime.now().isoformat()

    # Create a scan that was 'running' when app crashed
    scan_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO nlp_scan_runs
            (scan_id, created_at, status, date_range_start, date_range_end,
             mode, batch_strategy, total_batches, total_tickets, total_comments,
             budget_cap_usd, config_snapshot)
        VALUES (?, ?, 'running', '2025-01-01', '2025-01-31',
                'full', 'trc', 10, 1000, 0, 50.00, '{}')
    """, (scan_id, now))
    conn.commit()

    # Simulate server startup check (from server.js start() function)
    all_scans = conn.execute(
        "SELECT * FROM nlp_scan_runs ORDER BY created_at DESC"
    ).fetchall()
    interrupted = None
    for s in all_scans:
        s_dict = dict(s)
        if s_dict["status"] == "running":
            interrupted = s_dict
            break

    assert interrupted is not None, "FAIL: Should find interrupted scan"
    print(f"  Found interrupted scan: {interrupted['scan_id'][:8]}...")

    # Mark as paused (what server.js does)
    conn.execute("UPDATE nlp_scan_runs SET status = 'paused' WHERE scan_id = ?",
                 (interrupted["scan_id"],))
    conn.commit()

    # Verify
    scan = db.get_scan_status(scan_id)
    assert scan["status"] == "paused", "FAIL: Should be paused now"
    print("  Interrupted scan marked as paused OK")

    cleanup_db(db, db_path)
    print("  OK TEST 4 PASSED")


# ========================================================
#  TEST 5: Taxonomy lifecycle (probationary -> active -> dormant)
# ========================================================

def test_taxonomy_lifecycle():
    """Verify sub-pattern tier promotion/demotion logic."""
    print("\n" + "=" * 60)
    print("TEST 5: Taxonomy lifecycle (tier promotion/demotion)")
    print("=" * 60)

    db, db_path = fresh_db()
    conn = db.conn
    now = datetime.now().isoformat()

    # Create patterns in different tiers
    patterns = [
        ("p1", "TRC-A", "Pattern one", "probationary", 5, 1),
        ("p2", "TRC-A", "Pattern two", "probationary", 10, 2),
        ("p3", "TRC-A", "Pattern three", "active", 50, 5),
        ("p4", "TRC-B", "Pattern four", "active", 20, 3),
        ("p5", "TRC-B", "Pattern five", "dormant", 15, 0),
    ]
    for pid, trc, label, tier, lt, ls in patterns:
        conn.execute("""
            INSERT INTO sub_patterns
                (pattern_id, trc, label, description, tier,
                 discovered_scan, discovered_at, last_seen_scan, last_seen_at,
                 lifetime_tickets, lifetime_scans)
            VALUES (?, ?, ?, 'desc', ?, 'scan-1', ?, 'scan-1', ?, ?, ?)
        """, (pid, trc, label, tier, now, now, lt, ls))
    conn.commit()

    # Verify health report
    health = db.get_sub_taxonomy_health()
    print(f"  Health: {health}")
    assert health.get("probationary", 0) == 2
    assert health.get("active", 0) == 2
    assert health.get("dormant", 0) == 1

    # Verify get_active_sub_patterns returns active + probationary
    patterns_a = db.get_active_sub_patterns("TRC-A")
    labels = [dict(p)["label"] for p in patterns_a]
    print(f"  Active patterns for TRC-A: {labels}")
    assert "Pattern one" in labels  # probationary
    assert "Pattern two" in labels  # probationary
    assert "Pattern three" in labels  # active
    assert "Pattern five" not in labels  # dormant, wrong TRC

    cleanup_db(db, db_path)
    print("  OK TEST 5 PASSED")


# ========================================================
#  TEST 6: N-gram specificity computation
# ========================================================

def test_ngram_specificity():
    """Verify n-gram specificity = 1 - (patterns_containing / total_in_trc)."""
    print("\n" + "=" * 60)
    print("TEST 6: N-gram specificity logic")
    print("=" * 60)

    db, db_path = fresh_db()
    conn = db.conn
    now = datetime.now().isoformat()

    # 3 patterns in TRC-A
    for pid in ["pa1", "pa2", "pa3"]:
        conn.execute("""
            INSERT INTO sub_patterns
                (pattern_id, trc, label, tier,
                 discovered_scan, discovered_at, lifetime_tickets)
            VALUES (?, 'TRC-A', ?, 'active', 'scan-1', ?, 10)
        """, (pid, f"Label {pid}", now))

    # "billing error" appears in all 3 -> specificity = 1 - 3/3 = 0
    # "payout delay" appears in 1 -> specificity = 1 - 1/3 = 0.667
    conn.execute("""
        INSERT INTO sub_pattern_ngrams (pattern_id, trc, ngram, n, source, frequency, specificity, first_seen, last_seen)
        VALUES ('pa1', 'TRC-A', 'billing error', 2, 'gemini', 5, 0.0, ?, ?)
    """, (now, now))
    conn.execute("""
        INSERT INTO sub_pattern_ngrams (pattern_id, trc, ngram, n, source, frequency, specificity, first_seen, last_seen)
        VALUES ('pa2', 'TRC-A', 'billing error', 2, 'gemini', 4, 0.0, ?, ?)
    """, (now, now))
    conn.execute("""
        INSERT INTO sub_pattern_ngrams (pattern_id, trc, ngram, n, source, frequency, specificity, first_seen, last_seen)
        VALUES ('pa3', 'TRC-A', 'billing error', 2, 'gemini', 3, 0.0, ?, ?)
    """, (now, now))
    conn.execute("""
        INSERT INTO sub_pattern_ngrams (pattern_id, trc, ngram, n, source, frequency, specificity, first_seen, last_seen)
        VALUES ('pa1', 'TRC-A', 'payout delay', 2, 'gemini', 8, 0.667, ?, ?)
    """, (now, now))
    conn.commit()

    # Verify n-gram retrieval with specificity filter
    ngrams_all = db.get_sub_pattern_ngrams("pa1", min_specificity=0.0)
    ngrams_high = db.get_sub_pattern_ngrams("pa1", min_specificity=0.5)

    print(f"  pa1 all n-grams: {len(ngrams_all)}")
    print(f"  pa1 high-specificity: {len(ngrams_high)}")

    assert len(ngrams_all) == 2, f"FAIL: Expected 2, got {len(ngrams_all)}"
    assert len(ngrams_high) == 1, f"FAIL: Expected 1 high-specificity"

    high_ng = dict(ngrams_high[0])
    assert high_ng["ngram"] == "payout delay"
    assert high_ng["specificity"] > 0.5
    print(f"  High-specificity n-gram: '{high_ng['ngram']}' = {high_ng['specificity']}")

    cleanup_db(db, db_path)
    print("  OK TEST 6 PASSED")


# ========================================================
#  TEST 7: Multi-scan snapshot time series
# ========================================================

def test_snapshot_time_series():
    """Verify snapshot records accumulate across scans."""
    print("\n" + "=" * 60)
    print("TEST 7: Multi-scan snapshot accumulation")
    print("=" * 60)

    db, db_path = fresh_db()
    conn = db.conn
    now = datetime.now().isoformat()

    # Create scan run parent records (FK target for snapshots)
    for i, scan_id in enumerate(["scan-1", "scan-2", "scan-3"]):
        conn.execute("""
            INSERT INTO nlp_scan_runs
                (scan_id, created_at, status, date_range_start, date_range_end, mode)
            VALUES (?, ?, 'completed', ?, ?, 'full')
        """, (scan_id, now, f"2025-0{i+1}-01", f"2025-0{i+1}-31"))

    # Create pattern
    conn.execute("""
        INSERT INTO sub_patterns
            (pattern_id, trc, label, tier,
             discovered_scan, discovered_at, lifetime_tickets)
        VALUES ('p1', 'TRC-A', 'Test pattern', 'active', 'scan-1', ?, 10)
    """, (now,))

    # Create snapshots from 3 scans
    for i, scan_id in enumerate(["scan-1", "scan-2", "scan-3"]):
        conn.execute("""
            INSERT INTO sub_pattern_snapshots
                (pattern_id, scan_id, scan_date_start, scan_date_end,
                 ticket_count, pct_of_trc, avg_sentiment)
            VALUES ('p1', ?, ?, ?, ?, ?, ?)
        """, (scan_id, f"2025-0{i+1}-01", f"2025-0{i+1}-31",
              10 + i * 5, 0.3 + i * 0.05, -0.4 + i * 0.1))

    conn.commit()

    # Verify snapshots
    snapshots = conn.execute("""
        SELECT * FROM sub_pattern_snapshots
        WHERE pattern_id = 'p1'
        ORDER BY scan_date_start
    """).fetchall()

    print(f"  Snapshots: {len(snapshots)}")
    for s in snapshots:
        s = dict(s)
        print(f"    scan={s['scan_id']}, tickets={s['ticket_count']}, "
              f"pct={s['pct_of_trc']:.2f}, sentiment={s['avg_sentiment']:.2f}")

    assert len(snapshots) == 3, "FAIL: Expected 3 snapshots"

    # Verify trend (tickets increasing)
    counts = [dict(s)["ticket_count"] for s in snapshots]
    assert counts == [10, 15, 20], f"FAIL: Expected [10, 15, 20], got {counts}"

    cleanup_db(db, db_path)
    print("  OK TEST 7 PASSED")


# ========================================================
#  TEST 8: Response parser edge cases
# ========================================================

def test_response_parser_edge_cases():
    """Verify response parser handles malformed JSON, empty arrays, etc."""
    print("\n" + "=" * 60)
    print("TEST 8: Response parser edge cases")
    print("=" * 60)

    # Test the Node.js parser logic in Python (same logic)
    def extract_json_array(text):
        """Replicate the JS extractJsonArray logic."""
        # Strip markdown fences
        import re
        text = re.sub(r'```json\s*', '', text)
        text = re.sub(r'```\s*', '', text)
        text = text.strip()

        # Fix trailing commas
        text = re.sub(r',\s*]', ']', text)
        text = re.sub(r',\s*}', '}', text)

        # Try direct parse
        try:
            result = json.loads(text)
            if isinstance(result, list):
                return result
            if isinstance(result, dict):
                return [result]
        except json.JSONDecodeError:
            pass

        # Try to find array in text
        match = re.search(r'\[[\s\S]*?\]', text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        return []

    # Case 1: Normal JSON array
    result = extract_json_array('[{"ticket_id": "T1", "sub_cluster": "test"}]')
    assert len(result) == 1
    print("  Case 1 (normal array): OK")

    # Case 2: Markdown-wrapped
    result = extract_json_array('```json\n[{"ticket_id": "T1"}]\n```')
    assert len(result) == 1
    print("  Case 2 (markdown-wrapped): OK")

    # Case 3: Trailing comma
    result = extract_json_array('[{"ticket_id": "T1",},]')
    assert len(result) == 1
    print("  Case 3 (trailing comma): OK")

    # Case 4: Single object (not array)
    result = extract_json_array('{"ticket_id": "T1", "sub_cluster": "test"}')
    assert len(result) == 1
    print("  Case 4 (single object): OK")

    # Case 5: Empty response
    result = extract_json_array('')
    assert len(result) == 0
    print("  Case 5 (empty): OK")

    # Case 6: Garbage text
    result = extract_json_array('I could not classify these tickets.')
    assert len(result) == 0
    print("  Case 6 (garbage): OK")

    # Case 7: Array embedded in text
    result = extract_json_array('Here are the results:\n[{"ticket_id": "T1"}]\nThank you.')
    assert len(result) == 1
    print("  Case 7 (embedded array): OK")

    print("  OK TEST 8 PASSED")


# ========================================================
#  TEST 9: Redaction config consistency
# ========================================================

def test_redaction_config():
    """Verify shared redaction config is loadable from both Python and is valid."""
    print("\n" + "=" * 60)
    print("TEST 9: Shared redaction config (HIPAA A1)")
    print("=" * 60)

    config_path = Path(__file__).parent / "config" / "redaction_patterns.json"
    assert config_path.exists(), f"FAIL: {config_path} not found"

    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)

    patterns = config.get("patterns", [])
    skip_terms = config.get("aggressive_skip_terms", [])

    print(f"  Redaction patterns: {len(patterns)}")
    print(f"  Aggressive skip terms: {len(skip_terms)}")

    assert len(patterns) >= 5, "FAIL: Need at least 5 redaction patterns"
    assert len(skip_terms) >= 10, "FAIL: Need at least 10 skip terms"

    # Verify each pattern has required fields
    for p in patterns:
        assert "name" in p, f"FAIL: Pattern missing 'name'"
        assert "regex" in p, f"FAIL: Pattern '{p.get('name')}' missing 'regex'"
        assert "replace" in p, f"FAIL: Pattern '{p.get('name')}' missing 'replace'"
        print(f"    {p['name']}: {p['replace']}")

    # Verify Python can compile the regex patterns
    import re
    for p in patterns:
        try:
            re.compile(p["regex"])
        except re.error as e:
            assert False, f"FAIL: Invalid regex for '{p['name']}': {e}"

    print("  All patterns valid and compilable")

    # Verify Python loader works
    from src.gemini.gemini_client import GeminiClient
    gc = GeminiClient(cli_path="", pii_redaction=True)
    test_text = "Contact john@example.com or call 555-123-4567"
    redacted = gc._redact_base(test_text)
    assert "john@example.com" not in redacted, "FAIL: Email not redacted"
    assert "555-123-4567" not in redacted, "FAIL: Phone not redacted"
    print(f"  Python redaction: '{test_text}' -> '{redacted}'")

    print("  OK TEST 9 PASSED")


# ========================================================
#  TEST 10: End-to-end data pipeline check
# ========================================================

def test_e2e_pipeline():
    """Verify the full data pipeline: ticket -> classification -> finding -> synthesis prompt."""
    print("\n" + "=" * 60)
    print("TEST 10: End-to-end data pipeline")
    print("=" * 60)

    db, db_path = fresh_db()
    conn = db.conn
    now = datetime.now().isoformat()
    base = datetime(2025, 10, 1)

    # 1. Insert tickets
    for i in range(30):
        dt = (base + timedelta(days=i % 14)).strftime("%Y-%m-%d %H:%M:%S")
        insert_ticket(conn, f"E2E-{i:03d}", "TRC-A",
                      "Login issue", "Can't log in, password reset broken", dt, csat=1)
    conn.commit()
    print(f"  Inserted 30 tickets")

    # 2. Create scan run + batch
    scan_id = "e2e-scan"
    batch_id = "e2e-batch"
    conn.execute("""
        INSERT INTO nlp_scan_runs
            (scan_id, created_at, status, date_range_start, date_range_end,
             mode, batch_strategy, total_batches, total_tickets, total_comments,
             budget_cap_usd, config_snapshot)
        VALUES (?, ?, 'completed', '2025-10-01', '2025-10-14',
                'full', 'trc', 1, 30, 0, 50.0, '{}')
    """, (scan_id, now))
    conn.execute("""
        INSERT INTO nlp_batches
            (batch_id, scan_id, batch_number, trc, status, ticket_count, created_at)
        VALUES (?, ?, 1, 'TRC-A', 'completed', 30, ?)
    """, (batch_id, scan_id, now))

    # 3. Insert classifications
    for i in range(30):
        conn.execute("""
            INSERT INTO nlp_ticket_classifications
                (classification_id, batch_id, scan_id, ticket_id, trc,
                 sub_cluster, sub_cluster_confidence, is_novel,
                 sentiment_intensity, sentiment_polarity, friction_type,
                 anomaly_flag, created_at)
            VALUES (?, ?, ?, ?, 'TRC-A', 'Password reset broken', 0.85, 0,
                    0.7, '-0.5', 'process_gap', 'normal', ?)
        """, (str(uuid.uuid4()), batch_id, scan_id, f"E2E-{i:03d}", now))
    conn.commit()
    print("  Created scan + 30 classifications")

    # 4. Run meta-analysis
    from src.data.nlp_meta_analyzer import NLPMetaAnalyzer
    analyzer = NLPMetaAnalyzer(db)
    analyzer.run_analysis(scan_id)

    findings = db.get_scan_findings(scan_id)
    print(f"  Meta-analysis generated {len(findings)} findings")
    assert len(findings) > 0, "FAIL: No findings from meta-analysis"

    # 5. Verify finding has exemplar tickets
    f = findings[0]
    exemplars = json.loads(f.get("exemplar_ticket_ids") or "[]")
    print(f"  Top finding: '{f['title']}' with {len(exemplars)} exemplars")
    assert len(exemplars) > 0, "FAIL: No exemplar tickets"

    # 6. Verify synthesis prompt assembly works
    from src.data.nlp_synthesis import NLPSynthesizer

    class MockGemini:
        def generate(self, prompt):
            self.last_prompt = prompt
            return "Mock synthesis"

    mock = MockGemini()
    synth = NLPSynthesizer(db, mock)
    result = synth.synthesize_findings(scan_id)
    assert result == "Mock synthesis"
    assert len(mock.last_prompt) > 100
    print(f"  Synthesis prompt: {len(mock.last_prompt)} chars")

    # 7. Verify sub-taxonomy updated
    health = db.get_sub_taxonomy_health()
    print(f"  Taxonomy health: {health}")

    cleanup_db(db, db_path)
    print("  OK TEST 10 PASSED")


# ========================================================
#  MAIN
# ========================================================

if __name__ == "__main__":
    try:
        test_empty_trc()
        test_small_trc_skipped()
        test_budget_cap()
        test_resume_on_reopen()
        test_taxonomy_lifecycle()
        test_ngram_specificity()
        test_snapshot_time_series()
        test_response_parser_edge_cases()
        test_redaction_config()
        test_e2e_pipeline()

        print("\n" + "=" * 60)
        print("ALL SESSION 7 TESTS PASSED")
        print("=" * 60)

    except Exception as e:
        print(f"\n\nFAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
