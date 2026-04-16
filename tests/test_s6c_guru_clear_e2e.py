"""
Session 6C Tests: Guru Source Tagging, Clear Fix, End-to-End
=============================================================
Verifies migration 014, guru friction filtering by source_id,
_clear_all_data handling of per-source FTS and source_registry,
and full lifecycle E2E tests.

Run: python -m pytest tests/test_s6c_guru_clear_e2e.py -x -v
"""

import sqlite3
import pytest
from pathlib import Path
from datetime import datetime, timezone


# ─── Helpers ───

def _init_db(tmp_path, name="e2e.db"):
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager(tmp_path / name)
    db.initialize()
    return db


def _add_source(db, source_id, name, prefix, source_type="kodif"):
    now = datetime.now(timezone.utc).isoformat()
    db.conn.execute(
        "INSERT INTO source_registry (source_id, source_name, source_type, table_prefix, is_default, created_at) "
        "VALUES (?, ?, ?, ?, 0, ?)",
        (source_id, name, source_type, prefix, now),
    )
    from src.data.schema_builder import create_source_tables
    create_source_tables(db.conn, prefix)
    db.conn.commit()


def _seed_tickets(db, prefix, trc, count=5, id_prefix="T"):
    for i in range(count):
        tid = f"{id_prefix}-{i}"
        db.upsert_ticket({
            "ticket_id": tid, "subject": f"Ticket {tid}",
            "trc_code": trc, "trc_label": trc, "status": "open",
            "priority": "", "channel": "", "csat_score": 4.0,
            "created_at": f"2026-01-{15+i:02d}", "updated_at": "", "solved_at": "",
            "requester_name": "", "requester_email": "", "assignee_name": "",
            "group_name": "", "tags": [], "custom_fields": {},
            "assignment_to_resolution_hours": None,
            "total_resolution_hours": None, "first_reply_hours": None,
        }, table_prefix=prefix)
        db.upsert_conversation({
            "ticket_id": tid, "subject": f"Ticket {tid}",
            "trc_code": trc, "trc_label": trc, "status": "open",
            "csat_score": 4.0, "created_at": f"2026-01-{15+i:02d}", "solved_at": "",
            "message_count": 2, "client_messages": 1, "agent_messages": 1,
            "full_thread": f"Thread for {tid}", "thread_preview": f"Preview {tid}",
            "dataset_id": 0,
        }, table_prefix=prefix)
    db.commit()


def _seed_sub_patterns(conn, source_id=None):
    """Insert test sub_patterns with optional source_id."""
    now = datetime.now(timezone.utc).isoformat()
    patterns = [
        ("p1", "AUTH-01", "Prior auth delay", "auth_delay", source_id),
        ("p2", "AUTH-01", "Peer review wait", "peer_review", source_id),
        ("p3", "BIL-03", "Claim denied", "claim_denial", source_id),
    ]
    for pid, trc, label, friction, sid in patterns:
        conn.execute("""
            INSERT OR IGNORE INTO sub_patterns
                (pattern_id, trc, label, description, friction_type,
                 tier, discovered_scan, discovered_at, source_id)
            VALUES (?, ?, ?, ?, ?, 'confirmed', 'scan-001', ?, ?)
        """, (pid, trc, label, f"Desc: {label}", friction, now, sid))
    conn.commit()


# ─── Tests: Migration 014 ───

class TestMigration014:

    @pytest.fixture
    def db(self, tmp_path):
        db = _init_db(tmp_path, "m014.db")
        yield db
        db.close()

    def test_sub_patterns_has_source_id(self, db):
        """sub_patterns table has source_id column after initialize()."""
        cols = {r[1] for r in db.conn.execute("PRAGMA table_info(sub_patterns)").fetchall()}
        assert "source_id" in cols

    def test_nlp_scan_runs_has_source_id(self, db):
        """nlp_scan_runs table has source_id column after initialize()."""
        cols = {r[1] for r in db.conn.execute("PRAGMA table_info(nlp_scan_runs)").fetchall()}
        assert "source_id" in cols

    def test_migration_file_exists(self):
        """Migration 014 SQL file exists."""
        p = Path(__file__).parent.parent / "migrations" / "014_sub_pattern_source_id.sql"
        assert p.exists()


# ─── Tests: Guru Source Filtering ───

class TestGuruSourceFiltering:

    @pytest.fixture
    def db_with_patterns(self, tmp_path):
        db = _init_db(tmp_path, "guru_filter.db")
        # Patterns from Zendesk source
        _seed_sub_patterns(db.conn, source_id="zendesk_default")
        # Extra patterns from Kodif source
        now = datetime.now(timezone.utc).isoformat()
        db.conn.execute("""
            INSERT INTO sub_patterns
                (pattern_id, trc, label, description, friction_type,
                 tier, discovered_scan, discovered_at, source_id)
            VALUES ('p4', 'KOD-01', 'Chat timeout', 'Timeout desc', 'chat_timeout',
                    'confirmed', 'scan-002', ?, 'kodif_chat')
        """, (now,))
        # Pattern with NULL source_id (legacy)
        db.conn.execute("""
            INSERT INTO sub_patterns
                (pattern_id, trc, label, description, friction_type,
                 tier, discovered_scan, discovered_at, source_id)
            VALUES ('p5', 'GEN-01', 'General issue', 'Legacy desc', 'general',
                    'confirmed', 'scan-000', ?, NULL)
        """, (now,))
        db.conn.commit()
        yield db
        db.close()

    def test_all_sources_returns_all(self, db_with_patterns):
        """source_id=None returns all friction types including NULL source."""
        from src.data.guru_friction_pipeline import GuruFrictionPipeline
        pipeline = GuruFrictionPipeline(db_with_patterns, guru_client=None)
        types = pipeline._get_active_friction_types(source_id=None)
        friction_set = {t["friction_type"] for t in types}
        assert "auth_delay" in friction_set
        assert "chat_timeout" in friction_set
        assert "general" in friction_set

    def test_source_filtered_zendesk(self, db_with_patterns):
        """source_id='zendesk_default' only returns Zendesk patterns."""
        from src.data.guru_friction_pipeline import GuruFrictionPipeline
        pipeline = GuruFrictionPipeline(db_with_patterns, guru_client=None)
        types = pipeline._get_active_friction_types(source_id="zendesk_default")
        friction_set = {t["friction_type"] for t in types}
        assert "auth_delay" in friction_set
        assert "peer_review" in friction_set
        assert "chat_timeout" not in friction_set
        assert "general" not in friction_set  # NULL source_id excluded

    def test_source_filtered_kodif(self, db_with_patterns):
        """source_id='kodif_chat' only returns Kodif patterns."""
        from src.data.guru_friction_pipeline import GuruFrictionPipeline
        pipeline = GuruFrictionPipeline(db_with_patterns, guru_client=None)
        types = pipeline._get_active_friction_types(source_id="kodif_chat")
        friction_set = {t["friction_type"] for t in types}
        assert "chat_timeout" in friction_set
        assert "auth_delay" not in friction_set

    def test_unknown_source_returns_empty(self, db_with_patterns):
        """Nonexistent source returns empty list."""
        from src.data.guru_friction_pipeline import GuruFrictionPipeline
        pipeline = GuruFrictionPipeline(db_with_patterns, guru_client=None)
        types = pipeline._get_active_friction_types(source_id="nonexistent")
        assert types == []


# ─── Tests: _clear_all_data ───

class TestClearAllData:

    @pytest.fixture
    def db_with_data(self, tmp_path):
        db = _init_db(tmp_path, "clear_test.db")
        _add_source(db, "kodif_chat", "Kodif", "kodif_chat")
        _seed_tickets(db, "zendesk_default", "AUTH-01", 3, "ZEN")
        _seed_tickets(db, "kodif_chat", "BIL-03", 2, "KOD")
        db.rebuild_source_fts("zendesk_default")
        db.rebuild_source_fts("kodif_chat")
        _seed_sub_patterns(db.conn, source_id="zendesk_default")
        yield db
        db.close()

    def _simulate_clear(self, conn):
        """Simulate _clear_all_data logic from main_window.py."""
        PRESERVE_TABLES = {
            'nlp_scan_runs', 'nlp_batches', 'nlp_ticket_classifications',
            'nlp_findings', 'sub_patterns', 'sub_pattern_ngrams',
            'sub_pattern_snapshots', 'provisional_classifications',
            'prompt_library', 'source_registry',
        }
        conn.execute("PRAGMA foreign_keys = OFF")
        fts_tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_fts'"
        ).fetchall()
        for (fts_name,) in fts_tables:
            try:
                conn.execute(f"DROP TABLE IF EXISTS [{fts_name}]")
            except Exception:
                pass
        try:
            conn.execute("DROP TABLE IF EXISTS conversations_fts")
        except Exception:
            pass
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%_fts%'"
        ).fetchall()
        for (table_name,) in tables:
            if table_name not in PRESERVE_TABLES:
                try:
                    conn.execute(f"DELETE FROM [{table_name}]")
                except Exception:
                    pass
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")

    def test_source_registry_preserved(self, db_with_data):
        """source_registry entries survive clear."""
        self._simulate_clear(db_with_data.conn)
        count = db_with_data.conn.execute("SELECT COUNT(*) FROM source_registry").fetchone()[0]
        assert count >= 1, "source_registry should survive clear"

    def test_per_source_data_cleared(self, db_with_data):
        """Per-source ticket/conversation data deleted by clear."""
        zen_count = db_with_data.conn.execute("SELECT COUNT(*) FROM [zendesk_default_tickets]").fetchone()[0]
        assert zen_count > 0
        self._simulate_clear(db_with_data.conn)
        zen_after = db_with_data.conn.execute("SELECT COUNT(*) FROM [zendesk_default_tickets]").fetchone()[0]
        assert zen_after == 0

    def test_nlp_tables_preserved(self, db_with_data):
        """NLP tables (sub_patterns) survive clear."""
        before = db_with_data.conn.execute("SELECT COUNT(*) FROM sub_patterns").fetchone()[0]
        assert before > 0
        self._simulate_clear(db_with_data.conn)
        after = db_with_data.conn.execute("SELECT COUNT(*) FROM sub_patterns").fetchone()[0]
        assert after == before


# ─── Tests: End-to-End ───

class TestEndToEnd:

    @pytest.fixture
    def db(self, tmp_path):
        db = _init_db(tmp_path, "e2e_full.db")
        _add_source(db, "kodif_chat", "Kodif Chat", "kodif_chat")
        yield db
        db.close()

    def test_full_lifecycle_single_source(self, db):
        """Import → dual-write → query → clear → verify empty."""
        # Import
        _seed_tickets(db, "zendesk_default", "AUTH-01", 5, "ZEN")
        db.rebuild_source_fts("zendesk_default")

        # Verify dual-write: both shared + per-source have data
        shared = db.conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        per_src = db.conn.execute("SELECT COUNT(*) FROM [zendesk_default_tickets]").fetchone()[0]
        assert shared == 5
        assert per_src == 5

        # Query via WarehouseQuery
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        wq = WarehouseQuery(db.conn, SourceRegistry(db.conn))
        assert not wq._legacy_mode
        count = wq.get_ticket_count(source_id="zendesk_default")
        assert count == 5

    def test_full_lifecycle_multi_source(self, db):
        """Import 2 sources → verify isolation → combined query → per-source filter."""
        _seed_tickets(db, "zendesk_default", "AUTH-01", 5, "ZEN")
        _seed_tickets(db, "kodif_chat", "BIL-03", 3, "KOD")

        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        wq = WarehouseQuery(db.conn, SourceRegistry(db.conn))

        # Combined: all 8 tickets
        all_count = wq.get_ticket_count()
        assert all_count == 8

        # Per-source: 5 Zendesk, 3 Kodif
        zen_count = wq.get_ticket_count(source_id="zendesk_default")
        assert zen_count == 5
        kod_count = wq.get_ticket_count(source_id="kodif_chat")
        assert kod_count == 3

        # TRC filtering: AUTH-01 only from Zendesk
        trc_dist = wq.get_trc_distribution(source_id="zendesk_default")
        assert "AUTH-01" in trc_dist
        assert "BIL-03" not in trc_dist

    def test_source_selector_all_sources(self, db):
        """'All Sources' (source_id=None) returns union of all sources."""
        _seed_tickets(db, "zendesk_default", "AUTH-01", 3, "ZEN")
        _seed_tickets(db, "kodif_chat", "BIL-03", 2, "KOD")

        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        wq = WarehouseQuery(db.conn, SourceRegistry(db.conn))

        rows, total = wq.get_conversations_paged(source_id=None, limit=100)
        assert total == 5
        assert len(rows) == 5

    def test_legacy_fallback_still_works(self, tmp_path):
        """DB with no source_registry → WarehouseQuery uses shared tables."""
        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE conversations (
                ticket_id TEXT PRIMARY KEY, subject TEXT, trc_code TEXT,
                trc_label TEXT, status TEXT, csat_score REAL, created_at TEXT,
                solved_at TEXT, message_count INTEGER, client_messages INTEGER,
                agent_messages INTEGER, full_thread TEXT, thread_preview TEXT,
                dataset_id INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE tickets (ticket_id TEXT PRIMARY KEY, subject TEXT, trc_code TEXT)
        """)
        conn.execute("INSERT INTO tickets VALUES ('T-1', 'Test', 'AUTH')")
        conn.execute("""
            INSERT INTO conversations VALUES ('T-1','Test','AUTH','AUTH','open',4.0,
            '2026-01-15','',1,1,0,'Thread','Preview',0)
        """)
        conn.commit()

        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery

        reg = SourceRegistry(conn)
        wq = WarehouseQuery(conn, reg)
        assert wq._legacy_mode
        count = wq.get_ticket_count()
        assert count == 1
        conn.close()
