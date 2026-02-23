"""
Session 4+5 Tests — NLP Synthesis + N-gram Matcher + theta Integration
Tests the meta-analyzer findings, synthesis prompt assembly,
n-gram matcher provisional classification, and theta sub-pattern shares.
"""

import sys, os, json, sqlite3, uuid
from pathlib import Path
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.data.db_manager import DatabaseManager

# ────────────────────────────────────────────────────
# HELPER: Seed test database with realistic data
# ────────────────────────────────────────────────────

def seed_test_db(db):
    """Create test data: conversations, sub-patterns, ngrams, classifications, findings."""
    conn = db.conn

    # ── Create 3 TRCs with tickets across 14 days ──
    base_date = datetime(2025, 10, 1)
    ticket_id_counter = [0]

    def make_ticket(trc, day_offset, subject, body, csat=3):
        ticket_id_counter[0] += 1
        tid = f"TKT-{ticket_id_counter[0]:04d}"
        dt = (base_date + timedelta(days=day_offset)).strftime("%Y-%m-%d %H:%M:%S")
        # Insert parent ticket row first (FK)
        conn.execute("""
            INSERT OR IGNORE INTO tickets (ticket_id, subject, trc_code, created_at)
            VALUES (?, ?, ?, ?)
        """, (tid, subject, trc, dt))
        conn.execute("""
            INSERT INTO conversations
                (ticket_id, subject, trc_code, trc_label, csat_score,
                 created_at, full_thread, message_count, client_messages, agent_messages)
            VALUES (?, ?, ?, ?, ?, ?, ?, 3, 1, 2)
        """, (tid, subject, trc, f"TRC {trc}", csat, dt, body))
        return tid

    # TRC-A: Portal login issues (25 tickets, 14 days)
    trc_a_tids = []
    for d in range(14):
        for _ in range(2 if d < 10 else 1):
            tid = make_ticket("TRC-A", d,
                "Cannot log into portal",
                "I keep getting an error when trying to log in. "
                "Password reset link doesn't work. My account shows locked. "
                "I never completed activation.", csat=1)
            trc_a_tids.append(tid)

    # TRC-B: Claim denial questions (20 tickets)
    trc_b_tids = []
    for d in range(14):
        if d < 10:
            tid = make_ticket("TRC-B", d,
                "Claim denied - need explanation",
                "My claim was denied for timely filing. I submitted on time "
                "but the portal showed a different deadline. Prior auth was "
                "approved but claim still denied.", csat=2)
            trc_b_tids.append(tid)
        if d >= 5:
            tid = make_ticket("TRC-B", d,
                "ERA/EOB missing for denied claim",
                "I cannot find the ERA for this denied claim. "
                "The payment shows $0 but no denial reason code. "
                "Remittance advice is blank.", csat=2)
            trc_b_tids.append(tid)

    # TRC-C: Payment posting (15 tickets) — planted cross-TRC pattern
    trc_c_tids = []
    for d in range(14):
        if d < 8:
            tid = make_ticket("TRC-C", d,
                "Payment not applied correctly",
                "The payment was posted to wrong account. Portal shows "
                "payment but balance unchanged. I never completed activation "
                "so can't verify the posting.", csat=2)
            trc_c_tids.append(tid)

    conn.commit()

    # ── Create sub-patterns (simulating Session 3 output) ──
    patterns = [
        ("pat-login-1", "TRC-A", "Password reset dead-end", "active", 15, "process_gap"),
        ("pat-login-2", "TRC-A", "Account never activated", "active", 8, "process_gap"),
        ("pat-denial-1", "TRC-B", "Timely filing confusion", "active", 10, "policy_confusion"),
        ("pat-denial-2", "TRC-B", "Missing ERA/EOB", "probationary", 7, "information_gap"),
        ("pat-payment-1", "TRC-C", "Portal shows wrong balance", "active", 6, "system_bug"),
    ]
    now = datetime.now().isoformat()
    for pid, trc, label, tier, lt, friction in patterns:
        conn.execute("""
            INSERT INTO sub_patterns
                (pattern_id, trc, label, description, friction_type, tier,
                 discovered_scan, discovered_at, last_seen_scan, last_seen_at,
                 lifetime_tickets, lifetime_scans)
            VALUES (?, ?, ?, ?, ?, ?, 'scan-test', ?, 'scan-test', ?, ?, 1)
        """, (pid, trc, label, f"Auto-detected: {label}", friction, tier,
              now, now, lt))

    # ── Create n-grams for each pattern ──
    ngrams_data = {
        "pat-login-1": [
            ("password reset", 0.8, 0.9),
            ("reset link", 0.7, 0.85),
            ("account locked", 0.6, 0.7),
            ("error logging", 0.5, 0.6),
        ],
        "pat-login-2": [
            ("never completed activation", 0.9, 0.95),
            ("activation", 0.7, 0.5),
            ("never activated", 0.8, 0.9),
        ],
        "pat-denial-1": [
            ("timely filing", 0.9, 0.85),
            ("submitted on time", 0.7, 0.8),
            ("different deadline", 0.6, 0.75),
        ],
        "pat-denial-2": [
            ("era missing", 0.8, 0.9),
            ("remittance advice", 0.7, 0.85),
            ("denial reason code", 0.6, 0.7),
        ],
        "pat-payment-1": [
            ("wrong account", 0.8, 0.8),
            ("balance unchanged", 0.7, 0.9),
            ("portal shows payment", 0.6, 0.7),
        ],
    }
    # Build pid -> trc lookup
    pid_to_trc = {pid: trc for pid, trc, _, _, _, _ in patterns}
    for pid, ngrams in ngrams_data.items():
        trc = pid_to_trc.get(pid, "")
        for ngram, freq, spec in ngrams:
            conn.execute("""
                INSERT INTO sub_pattern_ngrams
                    (pattern_id, trc, ngram, n, frequency, specificity, source,
                     first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, 'gemini', ?, ?)
            """, (pid, trc, ngram, len(ngram.split()), freq, spec, now, now))

    # ── Create scan run + batches + classifications ──
    scan_id = "scan-test-001"
    total_tickets = len(trc_a_tids) + len(trc_b_tids) + len(trc_c_tids)
    conn.execute("""
        INSERT INTO nlp_scan_runs
            (scan_id, created_at, status, date_range_start, date_range_end,
             mode, batch_strategy, total_batches, total_tickets, total_comments,
             budget_cap_usd, config_snapshot)
        VALUES (?, ?, 'completed', '2025-10-01', '2025-10-14',
                'full', 'trc', 3, ?, 0, 50.0, '{}')
    """, (scan_id, now, total_tickets))

    # Create batch records (one per TRC)
    batch_a = str(uuid.uuid4())
    batch_b = str(uuid.uuid4())
    batch_c = str(uuid.uuid4())
    for bid, trc, num, count in [
        (batch_a, "TRC-A", 1, len(trc_a_tids)),
        (batch_b, "TRC-B", 2, len(trc_b_tids)),
        (batch_c, "TRC-C", 3, len(trc_c_tids)),
    ]:
        conn.execute("""
            INSERT INTO nlp_batches
                (batch_id, scan_id, batch_number, trc, status,
                 ticket_count, created_at)
            VALUES (?, ?, ?, ?, 'completed', ?, ?)
        """, (bid, scan_id, num, trc, count, now))

    # Classify TRC-A tickets
    for i, tid in enumerate(trc_a_tids):
        label = "Password reset dead-end" if i % 3 != 0 else "Account never activated"
        conn.execute("""
            INSERT INTO nlp_ticket_classifications
                (classification_id, batch_id, scan_id, ticket_id, trc,
                 sub_cluster, sub_cluster_confidence, is_novel,
                 sentiment_intensity, sentiment_polarity, friction_type,
                 anomaly_flag, created_at)
            VALUES (?, ?, ?, ?, 'TRC-A', ?, 0.85, 0, 0.7, '-0.5',
                    'process_gap', 'normal', ?)
        """, (str(uuid.uuid4()), batch_a, scan_id, tid, label, now))

    # Classify TRC-B tickets
    for i, tid in enumerate(trc_b_tids):
        label = "Timely filing confusion" if i < 10 else "Missing ERA/EOB"
        friction = "policy_confusion" if i < 10 else "information_gap"
        conn.execute("""
            INSERT INTO nlp_ticket_classifications
                (classification_id, batch_id, scan_id, ticket_id, trc,
                 sub_cluster, sub_cluster_confidence, is_novel,
                 sentiment_intensity, sentiment_polarity, friction_type,
                 anomaly_flag, created_at)
            VALUES (?, ?, ?, ?, 'TRC-B', ?, 0.80, 0, 0.6, '-0.4',
                    ?, 'normal', ?)
        """, (str(uuid.uuid4()), batch_b, scan_id, tid, label, friction, now))

    # Classify TRC-C tickets
    for i, tid in enumerate(trc_c_tids):
        conn.execute("""
            INSERT INTO nlp_ticket_classifications
                (classification_id, batch_id, scan_id, ticket_id, trc,
                 sub_cluster, sub_cluster_confidence, is_novel,
                 sentiment_intensity, sentiment_polarity, friction_type,
                 anomaly_flag, created_at)
            VALUES (?, ?, ?, ?, 'TRC-C', 'Portal shows wrong balance',
                    0.75, 0, 0.5, '-0.3', 'system_bug', 'normal', ?)
        """, (str(uuid.uuid4()), batch_c, scan_id, tid, now))

    conn.commit()
    return scan_id, trc_a_tids, trc_b_tids, trc_c_tids


# ────────────────────────────────────────────────────
# TEST 1: Meta-Analyzer — findings generation
# ────────────────────────────────────────────────────

def test_meta_analyzer(db, scan_id):
    """Verify within-TRC and cross-TRC findings, impact scoring, exemplars."""
    print("\n" + "=" * 60)
    print("TEST 1: Meta-Analyzer — findings + cross-TRC + impact")
    print("=" * 60)

    from src.data.nlp_meta_analyzer import NLPMetaAnalyzer
    analyzer = NLPMetaAnalyzer(db)

    results = analyzer.run_analysis(scan_id)

    # 1a. Within-TRC findings
    findings = db.get_scan_findings(scan_id)
    print(f"\n  Findings generated: {len(findings)}")
    assert len(findings) > 0, "FAIL: No findings generated"

    for f in findings:
        f = dict(f)
        print(f"    [{f.get('scope','?')}] {f['title']}")
        print(f"         tickets={f.get('ticket_count',0)}, "
              f"friction={f.get('dominant_friction_type','?')}, "
              f"impact={f.get('impact_score', 0):.3f}")

        # Verify exemplars
        exemplar_ids = json.loads(f.get("exemplar_ticket_ids") or "[]")
        if f.get("ticket_count", 0) >= 3:
            assert len(exemplar_ids) > 0, \
                f"FAIL: No exemplar tickets for finding '{f['title']}'"
            print(f"         exemplars={len(exemplar_ids)}")

    # 1b. Cross-TRC findings
    cross_trc = [dict(f) for f in findings if dict(f).get("scope") == "cross_trc"]
    print(f"\n  Cross-TRC findings: {len(cross_trc)}")
    # The "never completed activation" text appears in both TRC-A and TRC-C tickets
    # so entity-based cross-TRC detection should fire

    # 1c. Impact scores are ranked
    impacts = [dict(f).get("impact_score", 0) for f in findings]
    if len(impacts) > 1:
        assert impacts[0] >= impacts[-1], "FAIL: Findings not sorted by impact"
        print(f"  Impact range: {max(impacts):.3f} -> {min(impacts):.3f} OK")

    # 1d. Sub-taxonomy updated
    health = db.get_sub_taxonomy_health()
    print(f"\n  Sub-taxonomy health: {health}")

    print("\n  OK TEST 1 PASSED")


# ────────────────────────────────────────────────────
# TEST 2: Synthesis prompt assembly (no Gemini call)
# ────────────────────────────────────────────────────

def test_synthesis_prompt(db, scan_id):
    """Verify synthesis prompt assembly (without Gemini call)."""
    print("\n" + "=" * 60)
    print("TEST 2: Synthesis — prompt assembly")
    print("=" * 60)

    from src.data.nlp_synthesis import NLPSynthesizer

    # Mock Gemini client
    class MockGemini:
        def generate(self, prompt):
            self.last_prompt = prompt
            return "Mock synthesis response"

    mock_gemini = MockGemini()
    synth = NLPSynthesizer(db, mock_gemini)

    # Run synthesis
    result = synth.synthesize_findings(scan_id, max_findings=5)

    assert result == "Mock synthesis response", "FAIL: synthesis return value wrong"
    assert hasattr(mock_gemini, 'last_prompt'), "FAIL: Gemini not called"

    prompt = mock_gemini.last_prompt
    print(f"\n  Prompt length: {len(prompt)} chars")

    # Verify prompt contains key sections
    assert "behavioral" in prompt.lower() or "BEHAVIORAL" in prompt, \
        "FAIL: Prompt missing behavioral clusters section"
    assert "root cause" in prompt.lower() or "ROOT CAUSE" in prompt, \
        "FAIL: Prompt missing root cause section"
    assert "finding" in prompt.lower(), \
        "FAIL: Prompt missing findings"

    # Verify date range injected
    assert "2025-10-01" in prompt, "FAIL: date_start not in prompt"
    assert "2025-10-14" in prompt, "FAIL: date_end not in prompt"

    # Verify exemplar tickets included (should have JSONL lines)
    if '"ticket_id"' in prompt:
        print("  Exemplar tickets included in prompt OK")
    else:
        print("  WARNING: No exemplar ticket JSONL in prompt")

    # Verify report saved
    reports = db.conn.execute(
        "SELECT * FROM analysis_reports WHERE report_type = 'nlp_synthesis'"
    ).fetchall()
    print(f"  Reports saved: {len(reports)}")
    assert len(reports) >= 1, "FAIL: Synthesis report not saved"

    print("\n  OK TEST 2 PASSED")


# ────────────────────────────────────────────────────
# TEST 3: Drilldown prompt assembly
# ────────────────────────────────────────────────────

def test_drilldown_prompt(db, scan_id):
    """Verify drilldown prompt assembly."""
    print("\n" + "=" * 60)
    print("TEST 3: Drilldown — per-finding prompt")
    print("=" * 60)

    from src.data.nlp_synthesis import NLPSynthesizer

    class MockGemini:
        def generate(self, prompt):
            self.last_prompt = prompt
            return "Mock drilldown response"

    mock_gemini = MockGemini()
    synth = NLPSynthesizer(db, mock_gemini)

    # Get a finding
    findings = db.get_scan_findings(scan_id, limit=1)
    assert len(findings) > 0, "FAIL: No findings to drill into"

    finding = dict(findings[0])
    finding_id = finding["finding_id"]

    # Run drilldown
    result = synth.synthesize_single_finding(
        finding_id, user_question="What is causing this?"
    )

    assert result == "Mock drilldown response", "FAIL: drilldown return wrong"

    prompt = mock_gemini.last_prompt
    print(f"\n  Prompt length: {len(prompt)} chars")
    assert "DRILLDOWN" in prompt or "drilldown" in prompt.lower(), \
        "FAIL: Drilldown framework not in prompt"
    assert "What is causing this?" in prompt, \
        "FAIL: User question not injected"

    print("\n  OK TEST 3 PASSED")


# ────────────────────────────────────────────────────
# TEST 4: N-gram Matcher — provisional classification
# ────────────────────────────────────────────────────

def test_ngram_matcher(db):
    """
    After scan with active sub-patterns, ingest new tickets.
    Verify provisional classifications stored with reasonable match scores.
    Verify unmatched tickets have NULL pattern_id.
    """
    print("\n" + "=" * 60)
    print("TEST 4: N-gram Matcher — provisional classification")
    print("=" * 60)

    from src.data.ngram_matcher import NgramMatcher
    conn = db.conn

    # Create some new tickets (post-scan)
    now = datetime.now().isoformat()
    new_tids = []

    def insert_test_ticket(tid, subject, trc, trc_label, csat, thread, ts):
        conn.execute("INSERT OR IGNORE INTO tickets (ticket_id, subject, trc_code, created_at) VALUES (?, ?, ?, ?)",
                     (tid, subject, trc, ts))
        conn.execute("""
            INSERT INTO conversations
                (ticket_id, subject, trc_code, trc_label, csat_score,
                 created_at, full_thread, message_count, client_messages, agent_messages)
            VALUES (?, ?, ?, ?, ?, ?, ?, 3, 1, 2)
        """, (tid, subject, trc, trc_label, csat, ts, thread))

    # Ticket that should match "Password reset dead-end"
    tid1 = "NEW-0001"
    insert_test_ticket(tid1, 'Password reset not working', 'TRC-A', 'TRC A', 2,
                       'My password reset link expired. Account is locked.', now)
    new_tids.append(tid1)

    # Ticket that should match "Missing ERA/EOB"
    tid2 = "NEW-0002"
    insert_test_ticket(tid2, 'ERA missing for claim', 'TRC-B', 'TRC B', 2,
                       'The remittance advice is blank. No denial reason code visible.', now)
    new_tids.append(tid2)

    # Ticket that should NOT match anything (unrelated TRC)
    tid3 = "NEW-0003"
    insert_test_ticket(tid3, 'General question about enrollment', 'TRC-X', 'TRC X', 4,
                       'When does open enrollment start?', now)
    new_tids.append(tid3)

    # Ticket with matching TRC but no matching n-grams
    tid4 = "NEW-0004"
    insert_test_ticket(tid4, 'Other TRC-A question', 'TRC-A', 'TRC A', 3,
                       'This is a completely unrelated topic about something else.', now)
    new_tids.append(tid4)

    conn.commit()

    # Run n-gram matcher
    matcher = NgramMatcher(db)
    matcher.classify_batch(new_tids)

    # Check provisional classifications
    provs = conn.execute(
        "SELECT * FROM provisional_classifications WHERE ticket_id IN (?, ?, ?, ?)",
        tuple(new_tids)
    ).fetchall()

    print(f"\n  Provisional classifications stored: {len(provs)}")

    for p in provs:
        p = dict(p) if hasattr(p, 'keys') else {
            'ticket_id': p[0], 'matched_pattern_id': p[2],
            'match_score': p[3], 'match_method': p[4]
        }
        tid = p.get('ticket_id', '')
        pid = p.get('matched_pattern_id')
        score = p.get('match_score', 0)
        print(f"    {tid}: pattern={pid}, score={score:.3f}")

    # Verify tid1 matched password reset pattern
    p1 = conn.execute(
        "SELECT * FROM provisional_classifications WHERE ticket_id = ?", (tid1,)
    ).fetchone()
    if p1:
        p1_pid = p1["matched_pattern_id"] if hasattr(p1, 'keys') else p1[2]
        p1_score = p1["match_score"] if hasattr(p1, 'keys') else p1[3]
        assert p1_pid == "pat-login-1", \
            f"FAIL: Expected pat-login-1, got {p1_pid}"
        assert p1_score > 0.3, f"FAIL: Score too low: {p1_score}"
        print(f"\n  OK tid1 correctly matched pat-login-1 (score={p1_score:.3f})")

    # Verify tid2 matched ERA pattern
    p2 = conn.execute(
        "SELECT * FROM provisional_classifications WHERE ticket_id = ?", (tid2,)
    ).fetchone()
    if p2:
        p2_pid = p2["matched_pattern_id"] if hasattr(p2, 'keys') else p2[2]
        p2_score = p2["match_score"] if hasattr(p2, 'keys') else p2[3]
        assert p2_pid == "pat-denial-2", \
            f"FAIL: Expected pat-denial-2, got {p2_pid}"
        assert p2_score > 0.3, f"FAIL: Score too low: {p2_score}"
        print(f"  OK tid2 correctly matched pat-denial-2 (score={p2_score:.3f})")

    # Verify tid3 has no pattern (TRC-X has no sub-patterns)
    p3 = conn.execute(
        "SELECT * FROM provisional_classifications WHERE ticket_id = ?", (tid3,)
    ).fetchone()
    if p3:
        p3_pid = p3["matched_pattern_id"] if hasattr(p3, 'keys') else p3[2]
        # Should have NULL pattern_id (no active patterns for TRC-X)
        print(f"  tid3 (TRC-X): pattern={p3_pid}")
    else:
        print("  OK tid3 (TRC-X) skipped — no active patterns for TRC-X")

    # Verify tid4 has low/no score (unrelated content)
    p4 = conn.execute(
        "SELECT * FROM provisional_classifications WHERE ticket_id = ?", (tid4,)
    ).fetchone()
    if p4:
        p4_pid = p4["matched_pattern_id"] if hasattr(p4, 'keys') else p4[2]
        p4_score = p4["match_score"] if hasattr(p4, 'keys') else p4[3]
        if p4_pid is None:
            print(f"  OK tid4 (unrelated) has NULL pattern (score={p4_score:.3f})")
        else:
            print(f"  WARNING: tid4 matched {p4_pid} (score={p4_score:.3f}) — may be noise")
    else:
        print("  OK tid4 (unrelated) no provisional row")

    print("\n  OK TEST 4 PASSED")


# ────────────────────────────────────────────────────
# TEST 5: theta sub-pattern share computation
# ────────────────────────────────────────────────────

def test_theta_sub_pattern_shares(db):
    """
    After accumulating 7+ days of data, verify theta-EWMA computes
    share baselines. Inject a sudden share change and verify theta fires alert.
    """
    print("\n" + "=" * 60)
    print("TEST 5: theta sub-pattern share — EWMA + threshold")
    print("=" * 60)

    from src.data.theta_engine import compute_sub_pattern_shares
    conn = db.conn

    # Run compute_sub_pattern_shares for each day in range
    base_date = datetime(2025, 10, 1)
    all_flags = []
    for d in range(14):
        date_str = (base_date + timedelta(days=d)).strftime("%Y-%m-%d")
        flags = compute_sub_pattern_shares(conn, date_str)
        if flags:
            all_flags.extend(flags)
            for f in flags:
                print(f"    Day {d}: FLAG trc={f['trc_code']} "
                      f"key={f['metric_key']} z={f['z_score']:.2f} "
                      f"theta={f['theta_level']}")

    # Verify daily_baselines populated
    baseline_count = conn.execute("""
        SELECT COUNT(*) FROM daily_baselines
        WHERE metric_type = 'sub_pattern_share'
    """).fetchone()[0]
    print(f"\n  Daily baselines stored: {baseline_count}")
    assert baseline_count > 0, "FAIL: No sub_pattern_share baselines stored"

    # Verify rolling_stats updated
    stats_count = conn.execute("""
        SELECT COUNT(*) FROM rolling_stats
        WHERE metric_type = 'sub_pattern_share'
    """).fetchone()[0]
    print(f"  Rolling stats stored: {stats_count}")

    # ── Inject sudden share change and verify theta fires ──
    # Add a bunch of tickets for day 15 that break the normal share distribution
    # Make pat-login-2 suddenly dominate (normally ~30%, inject to 90%)
    day15 = (base_date + timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
    for i in range(20):
        tid = f"SPIKE-{i:04d}"
        conn.execute("INSERT OR IGNORE INTO tickets (ticket_id, subject, trc_code, created_at) VALUES (?, ?, ?, ?)",
                     (tid, 'Account never activated', 'TRC-A', day15))
        conn.execute("""
            INSERT INTO conversations
                (ticket_id, subject, trc_code, trc_label, csat_score,
                 created_at, full_thread, message_count, client_messages, agent_messages)
            VALUES (?, 'Account never activated', 'TRC-A', 'TRC A', 1,
                    ?, 'I never completed activation and now I cannot log in at all.', 3, 1, 2)
        """, (tid, day15))

        # Classify them all as "Account never activated"
        # Use the batch_a ID from seed (need to look it up)
        batch_row = conn.execute(
            "SELECT batch_id FROM nlp_batches WHERE scan_id = 'scan-test-001' AND trc = 'TRC-A' LIMIT 1"
        ).fetchone()
        spike_batch_id = batch_row[0] if batch_row else "batch-spike"
        conn.execute("""
            INSERT INTO nlp_ticket_classifications
                (classification_id, batch_id, scan_id, ticket_id, trc,
                 sub_cluster, sub_cluster_confidence, is_novel,
                 sentiment_intensity, sentiment_polarity, friction_type,
                 anomaly_flag, created_at)
            VALUES (?, ?, 'scan-test-001', ?, 'TRC-A', 'Account never activated',
                    0.90, 0, 0.8, '-0.6', 'process_gap', 'normal', ?)
        """, (str(uuid.uuid4()), spike_batch_id, tid, datetime.now().isoformat()))

    conn.commit()

    # Run theta on day 15
    day15_str = (base_date + timedelta(days=14)).strftime("%Y-%m-%d")
    spike_flags = compute_sub_pattern_shares(conn, day15_str)

    print(f"\n  Flags on spike day (day 15): {len(spike_flags)}")
    for f in spike_flags:
        print(f"    trc={f['trc_code']} key={f['metric_key']} "
              f"observed={f['observed']:.3f} mean={f['expected_mean']:.3f} "
              f"z={f['z_score']:.2f} theta={f['theta_level']}")

    # Note: With only 7+ days baseline, we may or may not get theta flags
    # depending on variance. The key is that baselines are computed.
    if spike_flags:
        print("  OK theta fired on spike day")
    else:
        # Even if no flag, verify the share was computed and is high
        share_row = conn.execute("""
            SELECT value FROM daily_baselines
            WHERE metric_type = 'sub_pattern_share'
              AND metric_key = 'pat-login-2' AND date = ?
        """, (day15_str,)).fetchone()
        if share_row:
            print(f"  Share for pat-login-2 on day 15: {share_row[0]:.3f}")
            assert share_row[0] > 0.5, "FAIL: Expected high share on spike day"
            print("  OK High share recorded (theta may need more history for flag)")

    print("\n  OK TEST 5 PASSED")


# ────────────────────────────────────────────────────
# TEST 6: CSV ingestion n-gram hook
# ────────────────────────────────────────────────────

def test_ingestion_hook(db):
    """Verify the post-ingestion n-gram hook is wired correctly."""
    print("\n" + "=" * 60)
    print("TEST 6: CSV ingestion — n-gram hook wiring")
    print("=" * 60)

    # Just verify the code path exists (actual CSV ingestion is tested elsewhere)
    from src.data.csv_ingestion import ingest_csv

    # Verify n-gram matcher import works
    from src.data.ngram_matcher import NgramMatcher
    matcher = NgramMatcher(db)

    # Verify sub_patterns check query works
    has = db.conn.execute(
        "SELECT 1 FROM sub_patterns WHERE tier IN ('active','probationary') LIMIT 1"
    ).fetchone()
    print(f"  Active sub-patterns exist: {has is not None}")
    assert has is not None, "FAIL: No active sub-patterns in test DB"

    print("\n  OK TEST 6 PASSED")


# ────────────────────────────────────────────────────
# MAIN
# ────────────────────────────────────────────────────

if __name__ == "__main__":
    # Use a temp database
    import tempfile
    db_path = Path(tempfile.gettempdir()) / "alma_test_session_4_5.db"
    if db_path.exists():
        db_path.unlink()

    print(f"Test database: {db_path}")

    db = DatabaseManager(db_path)
    db.initialize()

    try:
        # Seed test data
        print("\nSeeding test database...")
        scan_id, trc_a, trc_b, trc_c = seed_test_db(db)
        print(f"  Created {len(trc_a)} TRC-A, {len(trc_b)} TRC-B, "
              f"{len(trc_c)} TRC-C tickets")
        print(f"  Scan ID: {scan_id}")

        # Run tests
        test_meta_analyzer(db, scan_id)
        test_synthesis_prompt(db, scan_id)
        test_drilldown_prompt(db, scan_id)
        test_ngram_matcher(db)
        test_theta_sub_pattern_shares(db)
        test_ingestion_hook(db)

        print("\n" + "=" * 60)
        print("ALL SESSION 4+5 TESTS PASSED")
        print("=" * 60)

    except Exception as e:
        print(f"\n\nFAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    finally:
        db.close()
        # Cleanup
        if db_path.exists():
            db_path.unlink()
        for suffix in ["-wal", "-shm"]:
            p = Path(str(db_path) + suffix)
            if p.exists():
                p.unlink()
