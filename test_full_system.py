"""
Alma Insights — Full System Test
Exercises every engine and data path against the live database.
No Gemini calls — tests data layer, engines, and module integrity only.
"""

import sys
import os
import json
import traceback
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

PASS = 0
FAIL = 0
WARN = 0
results = []

# Enable ANSI colors on Windows
import os as _os
_os.system('')  # enables VT100 escape codes on Win10+

def test(name, fn):
    global PASS, FAIL, WARN
    try:
        result = fn()
        if result is True or result is None:
            PASS += 1
            results.append(("PASS", name, ""))
            print(f"  \033[92mPASS\033[0m  {name}")
        elif isinstance(result, str) and result.startswith("WARN"):
            WARN += 1
            results.append(("WARN", name, result))
            print(f"  \033[93mWARN\033[0m  {name}: {result}")
        else:
            FAIL += 1
            results.append(("FAIL", name, str(result)))
            print(f"  \033[91mFAIL\033[0m  {name}: {result}")
    except Exception as e:
        FAIL += 1
        tb = traceback.format_exc().split('\n')[-3].strip()
        results.append(("FAIL", name, f"{e.__class__.__name__}: {e}"))
        print(f"  \033[91mFAIL\033[0m  {name}: {e.__class__.__name__}: {e}")
        print(f"         {tb}")


# ===================================================
print("\n" + "=" * 60)
print("  ALMA INSIGHTS — FULL SYSTEM TEST")
print("=" * 60)


# ─--1. DATABASE MANAGER --─
print("\n--1. Database Manager --")

def t_db_import():
    from src.data.db_manager import DatabaseManager
    return True

def t_db_connect():
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager()
    assert db.conn is not None
    db.close()
    return True

def t_db_conversation_count():
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager()
    count = db.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    db.close()
    if count == 0:
        return "WARN: No conversations in database"
    assert count > 0, f"Expected conversations, got {count}"
    return True

def t_db_trc_distribution():
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager()
    rows = db.conn.execute(
        "SELECT COUNT(DISTINCT trc_code) as n FROM conversations WHERE trc_code IS NOT NULL"
    ).fetchone()
    db.close()
    assert rows[0] > 0, f"No TRC codes found"
    return True

def t_db_fts_search():
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager()
    # Check FTS5 table exists
    tables = db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='conversations_fts'"
    ).fetchone()
    db.close()
    if not tables:
        return "WARN: FTS5 table not found"
    return True

def t_db_nlp_tables_exist():
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager()
    required = [
        'nlp_scan_runs', 'nlp_batches', 'nlp_ticket_classifications',
        'sub_patterns', 'sub_pattern_ngrams', 'sub_pattern_snapshots',
        'nlp_findings', 'provisional_classifications'
    ]
    tables = [r[0] for r in db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    missing = [t for t in required if t not in tables]
    db.close()
    if missing:
        return f"Missing tables: {missing}"
    return True

def t_db_get_scan_history():
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager()
    history = db.get_scan_history(limit=10)
    db.close()
    assert isinstance(history, list)
    assert len(history) > 0, "No scan history"
    return True

def t_db_get_latest_scan():
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager()
    scan = db.get_latest_completed_scan()
    db.close()
    assert scan is not None, "No completed scan"
    assert scan['status'] in ('completed', 'scan_complete', 'analysis_complete')
    return True

def t_db_get_scan_findings():
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager()
    scan = db.get_latest_completed_scan()
    findings = db.get_scan_findings(scan['scan_id'], limit=10)
    db.close()
    assert isinstance(findings, list)
    assert len(findings) > 0, "No findings for latest scan"
    return True

def t_db_taxonomy_health():
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager()
    health = db.get_sub_taxonomy_health()
    db.close()
    assert isinstance(health, dict)
    total = health.get('active', 0) + health.get('probationary', 0) + health.get('dormant', 0)
    assert total > 0, f"No sub-patterns found in health check"
    assert health.get('avg_ngrams', 0) > 0, f"Avg n-grams is 0"
    return True

def t_db_sub_pattern_ngrams():
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager()
    # Get first pattern
    pat = db.conn.execute(
        "SELECT pattern_id FROM sub_patterns LIMIT 1"
    ).fetchone()
    assert pat, "No sub-patterns exist"
    ngrams = db.get_sub_pattern_ngrams(pat[0], min_specificity=0.0)
    db.close()
    assert isinstance(ngrams, list)
    assert len(ngrams) > 0, "Pattern has no n-grams"
    return True

def t_db_no_json_array_trcs():
    """Verify TRC propagation fix: no JSON arrays in classifications."""
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager()
    bad = db.conn.execute(
        "SELECT COUNT(*) FROM nlp_ticket_classifications WHERE trc LIKE '[%'"
    ).fetchone()[0]
    db.close()
    assert bad == 0, f"Found {bad} classifications with JSON-array TRCs"
    return True

def t_db_sub_patterns_individual_trcs():
    """Verify sub-patterns are keyed to individual TRC codes, not arrays."""
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager()
    bad = db.conn.execute(
        "SELECT COUNT(*) FROM sub_patterns WHERE trc LIKE '[%'"
    ).fetchone()[0]
    distinct = db.conn.execute(
        "SELECT COUNT(DISTINCT trc) FROM sub_patterns"
    ).fetchone()[0]
    db.close()
    assert bad == 0, f"Found {bad} sub-patterns with JSON-array TRCs"
    assert distinct > 7, f"Only {distinct} distinct TRCs — propagation fix may not have worked"
    return True

def t_db_cross_trc_findings():
    """Verify cross-TRC analysis produced findings."""
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager()
    cross = db.conn.execute(
        "SELECT COUNT(*) FROM nlp_findings WHERE finding_type = 'cross_trc'"
    ).fetchone()[0]
    db.close()
    assert cross > 0, "No cross-TRC findings — cross-TRC analysis may be broken"
    return True

def t_db_tier_lifecycle():
    """Verify tier lifecycle: patterns seen in 2+ scans should exist."""
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager()
    multi = db.conn.execute(
        "SELECT COUNT(*) FROM sub_patterns WHERE lifetime_scans >= 2"
    ).fetchone()[0]
    db.close()
    if multi == 0:
        return "WARN: No patterns seen in 2+ scans (may be expected with limited data)"
    return True

test("Import DatabaseManager", t_db_import)
test("Connect to database", t_db_connect)
test("Conversation count > 0", t_db_conversation_count)
test("TRC distribution exists", t_db_trc_distribution)
test("FTS5 search table", t_db_fts_search)
test("NLP tables exist", t_db_nlp_tables_exist)
test("get_scan_history()", t_db_get_scan_history)
test("get_latest_completed_scan()", t_db_get_latest_scan)
test("get_scan_findings()", t_db_get_scan_findings)
test("get_sub_taxonomy_health()", t_db_taxonomy_health)
test("get_sub_pattern_ngrams()", t_db_sub_pattern_ngrams)
test("No JSON-array TRCs in classifications", t_db_no_json_array_trcs)
test("Sub-patterns keyed to individual TRCs", t_db_sub_patterns_individual_trcs)
test("Cross-TRC findings exist", t_db_cross_trc_findings)
test("Tier lifecycle working", t_db_tier_lifecycle)


# ─--2. THETA ENGINE --─
print("\n--2. Theta Engine --")

def t_theta_import():
    from src.data.theta_engine import run_theta_scan, run_theta_scan_range
    return True

def t_theta_scan_range():
    from src.data.db_manager import DatabaseManager
    from src.data.theta_engine import run_theta_scan_range
    db = DatabaseManager()
    # run_theta_scan_range takes (conn, progress_callback=None)
    flags = run_theta_scan_range(db.conn)
    db.close()
    assert isinstance(flags, dict), f"Unexpected return: {type(flags)}"
    assert "flags" in flags, f"Missing 'flags' key in result: {list(flags.keys())}"
    assert "days_scanned" in flags, f"Missing 'days_scanned' key"
    return True

def t_theta_anomaly_flags():
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager()
    count = db.conn.execute("SELECT COUNT(*) FROM anomaly_flags").fetchone()[0]
    db.close()
    if count == 0:
        return "WARN: No anomaly flags (may be expected with limited data)"
    return True

def t_theta_get_flag_history():
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager()
    try:
        rows = db.conn.execute(
            "SELECT * FROM anomaly_flags ORDER BY date DESC LIMIT 5"
        ).fetchall()
        db.close()
        return True
    except Exception as e:
        db.close()
        return f"Query failed: {e}"

test("Import theta engine", t_theta_import)
test("run_theta_scan_range()", t_theta_scan_range)
test("Anomaly flags table", t_theta_anomaly_flags)
test("Query flag history", t_theta_get_flag_history)


# ─--3. TRENDING ENGINE --─
print("\n--3. Trending Engine --")

def t_trending_import():
    from src.data.trending_engine import compute_rising_terms, run_full_analysis
    return True

def t_trending_rising_terms():
    from src.data.db_manager import DatabaseManager
    from src.data.trending_engine import compute_rising_terms
    db = DatabaseManager()
    row = db.conn.execute(
        "SELECT MIN(created_at), MAX(created_at) FROM conversations"
    ).fetchone()
    if not row or not row[0]:
        db.close()
        return "WARN: No conversations for trending"
    date_start = row[0][:10]
    date_end = row[1][:10]
    result = compute_rising_terms(db.conn, date_start, date_end, None, 'Weekly')
    db.close()
    assert isinstance(result, (list, dict)), f"Expected list/dict, got {type(result)}"
    return True

def t_trending_full_analysis():
    from src.data.db_manager import DatabaseManager
    from src.data.trending_engine import run_full_analysis
    db = DatabaseManager()
    row = db.conn.execute(
        "SELECT MIN(created_at), MAX(created_at) FROM conversations"
    ).fetchone()
    if not row or not row[0]:
        db.close()
        return "WARN: No conversations for trending"
    date_start = row[0][:10]
    date_end = row[1][:10]
    result = run_full_analysis(db.conn, date_start, date_end, None, 'Weekly')
    db.close()
    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    return True

test("Import trending functions", t_trending_import)
test("compute_rising_terms()", t_trending_rising_terms)
test("run_full_analysis()", t_trending_full_analysis)


# ─--4. CONCEPT MAP --─
print("\n--4. Concept Map --")

def t_concept_import():
    from src.data.concept_map import DOMAIN_CONCEPTS, build_concept_index, apply_concept_normalization
    assert len(DOMAIN_CONCEPTS) > 0
    return True

def t_concept_index():
    from src.data.concept_map import build_concept_index
    index = build_concept_index()
    assert isinstance(index, dict)
    assert len(index) > 0
    return True

test("Import concept map", t_concept_import)
test("Build concept index", t_concept_index)


# ─--5. CSV INGESTION + REBUILD --─
print("\n--5. CSV Ingestion & Rebuild --")

def t_csv_import():
    from src.data.csv_ingestion import COLUMN_MAP, ingest_csv
    assert len(COLUMN_MAP) > 0
    return True

def t_rebuild_import():
    from src.data.rebuild_utils import parse_timestamp, normalize_role
    return True

def t_rebuild_timestamp():
    from src.data.rebuild_utils import parse_timestamp
    result = parse_timestamp("2025-01-15 14:30:00")
    assert result is not None
    return True

def t_rebuild_role():
    from src.data.rebuild_utils import normalize_role
    assert normalize_role("agent") in ("Agent", "agent")
    assert normalize_role("CLIENT") in ("Client", "client")
    return True

test("Import csv_ingestion", t_csv_import)
test("Import rebuild_utils", t_rebuild_import)
test("parse_timestamp()", t_rebuild_timestamp)
test("normalize_role()", t_rebuild_role)


# ─--6. EMBEDDING ENGINE --─
print("\n--6. Embedding Engine --")

def t_embedding_import():
    from src.data.embedding_engine import is_available, embed_texts
    return True

def t_embedding_available():
    from src.data.embedding_engine import is_available
    avail = is_available()
    if not avail:
        return "WARN: sentence-transformers not available (air-gapped mode)"
    return True

test("Import embedding engine", t_embedding_import)
test("Embedding availability check", t_embedding_available)


# ─--7. GEMINI CLIENT --─
print("\n--7. Gemini Client --")

def t_gemini_import():
    from src.gemini.gemini_client import GeminiClient
    return True

def t_gemini_init():
    from src.gemini.gemini_client import GeminiClient
    gc = GeminiClient()
    return True

def t_gemini_available():
    from src.gemini.gemini_client import GeminiClient
    gc = GeminiClient()
    avail = gc.is_available()
    if not avail:
        return "WARN: Gemini CLI not found on PATH"
    return True

def t_gemini_redact():
    from src.gemini.gemini_client import GeminiClient
    gc = GeminiClient()
    # Test base PII redaction
    text = "John Smith called about SSN 123-45-6789 and email john@test.com"
    redacted = gc._redact_base(text)
    assert '123-45-6789' not in redacted, "SSN not redacted"
    assert 'john@test.com' not in redacted, "Email not redacted"
    return True

test("Import GeminiClient", t_gemini_import)
test("GeminiClient init", t_gemini_init)
test("Gemini CLI available", t_gemini_available)
test("PII redaction (base)", t_gemini_redact)


# ─--8. GEMINI PROMPTS --─
print("\n--8. Prompt Templates --")

def t_prompts_import():
    from src.gemini.prompts import build_synthesis_prompt, build_hypothesis_prompt
    return True

def t_prompt_templates_exist():
    templates = ['nlp_classify.txt', 'synthesis.txt', 'hypothesis.txt']
    missing = []
    for t in templates:
        p = Path(__file__).parent / 'config' / 'prompts' / t
        if not p.exists():
            missing.append(t)
    if missing:
        return f"WARN: Missing templates: {missing}"
    return True

def t_nlp_classify_template():
    p = Path(__file__).parent / 'config' / 'prompts' / 'nlp_classify.txt'
    if not p.exists():
        return "WARN: nlp_classify.txt not found"
    content = p.read_text(encoding='utf-8')
    # Check required placeholders
    required = ['{trc_path}', '{sub_taxonomy_section}', '{ticket_jsonl}',
                '{n_tickets}', '{date_start}', '{date_end}']
    missing = [ph for ph in required if ph not in content]
    if missing:
        return f"Missing placeholders: {missing}"
    return True

test("Import prompt builders", t_prompts_import)
test("Prompt templates exist", t_prompt_templates_exist)
test("NLP classify template placeholders", t_nlp_classify_template)


# ─--9. NLP META-ANALYZER --─
print("\n--9. NLP Meta-Analyzer --")

def t_meta_import():
    from src.data.nlp_meta_analyzer import NLPMetaAnalyzer
    return True

def t_meta_findings_integrity():
    """Verify findings have valid structure."""
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager()
    findings = db.conn.execute("""
        SELECT finding_id, finding_type, scope, title, ticket_count,
               impact_score, exemplar_ticket_ids
        FROM nlp_findings
        ORDER BY impact_score DESC LIMIT 10
    """).fetchall()
    db.close()
    for f in findings:
        f = dict(f)
        assert f['finding_type'] in ('within_trc', 'cross_trc'), \
            f"Invalid finding_type: {f['finding_type']}"
        assert f['title'], f"Empty title on {f['finding_id']}"
        assert f['impact_score'] is not None, f"Null impact on {f['finding_id']}"
    return True

def t_meta_snapshots():
    """Verify snapshots were created."""
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager()
    count = db.conn.execute("SELECT COUNT(*) FROM sub_pattern_snapshots").fetchone()[0]
    db.close()
    assert count > 0, "No snapshots created"
    return True

def t_meta_snapshot_integrity():
    """Verify snapshots have valid JSON fields."""
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager()
    rows = db.conn.execute(
        "SELECT sentiment_dist, friction_dist FROM sub_pattern_snapshots LIMIT 5"
    ).fetchall()
    db.close()
    for r in rows:
        if r[0]:
            parsed = json.loads(r[0])
            assert isinstance(parsed, dict), f"sentiment_dist not a dict: {r[0][:50]}"
        if r[1]:
            parsed = json.loads(r[1])
            assert isinstance(parsed, dict), f"friction_dist not a dict: {r[1][:50]}"
    return True

test("Import NLPMetaAnalyzer", t_meta_import)
test("Findings integrity", t_meta_findings_integrity)
test("Snapshots created", t_meta_snapshots)
test("Snapshot JSON integrity", t_meta_snapshot_integrity)


# ─--10. SCAN WORKER --─
print("\n--10. Scan Worker --")

def t_worker_import():
    from src.data.scan_worker import ScanWorker
    return True

def t_worker_trc_map_init():
    """Verify _ticket_trc_map is initialized in __init__."""
    from src.data.scan_worker import ScanWorker
    import inspect
    source = inspect.getsource(ScanWorker.__init__)
    assert '_ticket_trc_map' in source, "_ticket_trc_map not in __init__"
    return True

def t_worker_trc_propagation():
    """Verify _parse_response uses _ticket_trc_map for TRC lookup."""
    from src.data.scan_worker import ScanWorker
    import inspect
    source = inspect.getsource(ScanWorker._parse_response)
    assert '_ticket_trc_map' in source, "_ticket_trc_map not in _parse_response"
    assert '.get(' in source, "No .get() lookup in _parse_response"
    return True

def t_worker_mixed_sub_taxonomy():
    """Verify _build_mixed_sub_taxonomy method exists."""
    from src.data.scan_worker import ScanWorker
    assert hasattr(ScanWorker, '_build_mixed_sub_taxonomy'), \
        "_build_mixed_sub_taxonomy method missing"
    return True

def t_worker_build_prompt_uses_mixed():
    """Verify _build_prompt routes to _build_mixed_sub_taxonomy for mixed batches."""
    from src.data.scan_worker import ScanWorker
    import inspect
    source = inspect.getsource(ScanWorker._build_prompt)
    assert '_build_mixed_sub_taxonomy' in source, \
        "_build_prompt doesn't call _build_mixed_sub_taxonomy"
    # Verify no [:5] cap on stats
    assert 'trc_list[:5]' not in source, \
        "_build_prompt still has [:5] cap on TRC list"
    return True

test("Import ScanWorker", t_worker_import)
test("_ticket_trc_map in __init__", t_worker_trc_map_init)
test("TRC propagation in _parse_response", t_worker_trc_propagation)
test("_build_mixed_sub_taxonomy exists", t_worker_mixed_sub_taxonomy)
test("_build_prompt uses mixed sub-taxonomy", t_worker_build_prompt_uses_mixed)


# ─--11. SCAN WORKER MANAGER --─
print("\n--11. Scan Worker Manager --")

def t_manager_import():
    from src.data.scan_worker_manager import ScanWorkerManager
    return True

def t_manager_init():
    from src.data.scan_worker_manager import ScanWorkerManager
    mgr = ScanWorkerManager(str(Path(__file__).parent / 'data' / 'local_warehouse.db'))
    return True

def t_manager_get_status():
    from src.data.db_manager import DatabaseManager
    from src.data.scan_worker_manager import ScanWorkerManager
    db = DatabaseManager()
    scan = db.get_latest_completed_scan()
    db.close()
    mgr = ScanWorkerManager(str(Path(__file__).parent / 'data' / 'local_warehouse.db'))
    status = mgr.get_status(scan['scan_id'])
    assert status is not None
    assert 'status' in status
    return True

test("Import ScanWorkerManager", t_manager_import)
test("ScanWorkerManager init", t_manager_init)
test("get_status() for latest scan", t_manager_get_status)


# ─--12. UI MODULE IMPORTS --─
print("\n--12. UI Module Imports (headless) --")

def t_ui_main_window():
    # Just test importability, not instantiation (needs QApp)
    import importlib
    spec = importlib.util.find_spec("src.ui.main_window")
    assert spec is not None, "main_window module not found"
    return True

def t_ui_nlp_scanner():
    import importlib
    spec = importlib.util.find_spec("src.ui.pages.nlp_scanner_page")
    assert spec is not None
    return True

def t_ui_conversation_search():
    import importlib
    spec = importlib.util.find_spec("src.ui.pages.conversation_search")
    assert spec is not None
    return True

def t_ui_trending():
    import importlib
    spec = importlib.util.find_spec("src.ui.pages.trending_topics")
    assert spec is not None
    return True

def t_ui_ai_reports():
    import importlib
    spec = importlib.util.find_spec("src.ui.pages.ai_reports")
    assert spec is not None
    return True

def t_ui_settings():
    import importlib
    spec = importlib.util.find_spec("src.ui.pages.settings_page")
    assert spec is not None
    return True

test("main_window module", t_ui_main_window)
test("nlp_scanner_page module", t_ui_nlp_scanner)
test("conversation_search module", t_ui_conversation_search)
test("trending_topics module", t_ui_trending)
test("ai_reports module", t_ui_ai_reports)
test("settings_page module", t_ui_settings)


# ─--13. INCIDENT ENGINE --─
print("\n--13. Incident Engine --")

def t_incident_tables():
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager()
    tables = ['daily_counts', 'hourly_counts', 'incident_flags']
    existing = [r[0] for r in db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    missing = [t for t in tables if t not in existing]
    db.close()
    if missing:
        return f"WARN: Missing incident tables: {missing}"
    return True

def t_incident_daily_counts():
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager()
    count = db.conn.execute("SELECT COUNT(*) FROM daily_counts").fetchone()[0]
    db.close()
    if count == 0:
        return "WARN: No daily counts populated"
    return True

def t_incident_flags():
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager()
    count = db.conn.execute("SELECT COUNT(*) FROM incident_flags").fetchone()[0]
    db.close()
    if count == 0:
        return "WARN: No incident flags"
    return True

test("Incident tables exist", t_incident_tables)
test("Daily counts populated", t_incident_daily_counts)
test("Incident flags", t_incident_flags)


# ─--14. GEMINI BRIDGE --─
print("\n--14. Gemini Bridge --")

def t_bridge_exists():
    p = Path(__file__).parent / 'src' / 'gemini' / 'gemini_bridge.mjs'
    assert p.exists(), "gemini_bridge.mjs not found"
    return True

def t_bridge_syntax():
    import subprocess
    result = subprocess.run(
        ['node', '--check', str(Path(__file__).parent / 'src' / 'gemini' / 'gemini_bridge.mjs')],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"Syntax error: {result.stderr[:200]}"
    return True

def t_bridge_exit_codes():
    """Verify bridge has smart exit guard with correct codes."""
    p = Path(__file__).parent / 'src' / 'gemini' / 'gemini_bridge.mjs'
    content = p.read_text(encoding='utf-8')
    assert 'code === 0' in content, "Missing exit code 0 handling"
    assert 'code === 41' in content, "Missing exit code 41 (auth) handling"
    assert 'code === 42' in content, "Missing exit code 42 (input) handling"
    assert 'code === 52' in content, "Missing exit code 52 (config) handling"
    assert 'code === 130' in content, "Missing exit code 130 (cancel) handling"
    return True

def t_bridge_streaming():
    """Verify bridge has streaming call function."""
    p = Path(__file__).parent / 'src' / 'gemini' / 'gemini_bridge.mjs'
    content = p.read_text(encoding='utf-8')
    assert 'callGeminiStreaming' in content, "Missing streaming call function"
    assert 'sendMessageStream' in content, "Missing sendMessageStream call"
    assert 'GeminiEventType' in content, "Missing GeminiEventType handling"
    return True

test("Bridge file exists", t_bridge_exists)
test("Bridge syntax valid", t_bridge_syntax)
test("Bridge exit codes (0/41/42/52/130)", t_bridge_exit_codes)
test("Bridge streaming support", t_bridge_streaming)


# ─--15. DATA INTEGRITY --─
print("\n--15. Data Integrity --")

def t_classification_ticket_match():
    """Every classified ticket_id exists in conversations."""
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager()
    orphans = db.conn.execute("""
        SELECT COUNT(*)
        FROM nlp_ticket_classifications tc
        LEFT JOIN conversations c ON tc.ticket_id = c.ticket_id
        WHERE c.ticket_id IS NULL
    """).fetchone()[0]
    db.close()
    if orphans > 0:
        return f"WARN: {orphans} classified tickets not in conversations table"
    return True

def t_classification_trc_match():
    """Classification TRCs match their ticket's trc_code."""
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager()
    mismatches = db.conn.execute("""
        SELECT COUNT(*)
        FROM nlp_ticket_classifications tc
        JOIN conversations c ON tc.ticket_id = c.ticket_id
        WHERE tc.trc != c.trc_code AND c.trc_code IS NOT NULL AND c.trc_code != ''
    """).fetchone()[0]
    total = db.conn.execute("SELECT COUNT(*) FROM nlp_ticket_classifications").fetchone()[0]
    db.close()
    if mismatches > 0:
        return f"{mismatches}/{total} TRC mismatches between classifications and conversations"
    return True

def t_sub_pattern_scan_refs():
    """Sub-pattern scan references are valid."""
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager()
    orphans = db.conn.execute("""
        SELECT COUNT(*)
        FROM sub_patterns sp
        LEFT JOIN nlp_scan_runs sr ON sp.discovered_scan = sr.scan_id
        WHERE sr.scan_id IS NULL
    """).fetchone()[0]
    db.close()
    assert orphans == 0, f"{orphans} sub-patterns reference non-existent scans"
    return True

def t_ngram_pattern_refs():
    """Every n-gram references a valid sub-pattern."""
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager()
    orphans = db.conn.execute("""
        SELECT COUNT(*)
        FROM sub_pattern_ngrams spn
        LEFT JOIN sub_patterns sp ON spn.pattern_id = sp.pattern_id
        WHERE sp.pattern_id IS NULL
    """).fetchone()[0]
    db.close()
    assert orphans == 0, f"{orphans} orphan n-grams"
    return True

def t_finding_scan_refs():
    """Every finding references a valid scan."""
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager()
    orphans = db.conn.execute("""
        SELECT COUNT(*)
        FROM nlp_findings f
        LEFT JOIN nlp_scan_runs sr ON f.scan_id = sr.scan_id
        WHERE sr.scan_id IS NULL
    """).fetchone()[0]
    db.close()
    assert orphans == 0, f"{orphans} orphan findings"
    return True

test("Classification-conversation join", t_classification_ticket_match)
test("Classification TRC matches ticket TRC", t_classification_trc_match)
test("Sub-pattern scan references valid", t_sub_pattern_scan_refs)
test("N-gram -> pattern references valid", t_ngram_pattern_refs)
test("Finding -> scan references valid", t_finding_scan_refs)


# ===================================================
print("\n" + "=" * 60)
print(f"  RESULTS: {PASS} passed, {FAIL} failed, {WARN} warnings")
print("=" * 60)

if FAIL > 0:
    print("\n  FAILURES:")
    for status, name, detail in results:
        if status == "FAIL":
            print(f"    \033[91m{name}\033[0m: {detail}")

if WARN > 0:
    print("\n  WARNINGS:")
    for status, name, detail in results:
        if status == "WARN":
            print(f"    \033[93m{name}\033[0m: {detail}")

print()
sys.exit(1 if FAIL > 0 else 0)
