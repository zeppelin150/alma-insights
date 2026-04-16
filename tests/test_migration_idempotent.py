"""
Diagnostic test: Can we make migrations 002 and 006 idempotent,
then run the full migration chain (001→009) on a database that
already has the conflicting columns?

This simulates the exact state of the live database:
- Tables created by db_manager inline DDL
- 'source' column already exists on conversations
- 'filter_json', 'ticket_count', 'active_report_ids' already exist on chat_sessions
- schema_migrations table with only 001 recorded

The test proves that wrapping the failing ALTERs in IF-NOT-EXISTS
guards (via Python) allows the full chain to apply, and that
the migration 009 data hook correctly extracts JSON blobs.
"""

import json
import sqlite3
import pytest
from pathlib import Path

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def _read_migration(name: str) -> str:
    return (_MIGRATIONS_DIR / name).read_text(encoding="utf-8")


def _add_column_if_missing(conn, table, col_name, col_type):
    """SQLite-safe ADD COLUMN — skips if column already exists."""
    existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if col_name not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")


def _make_migration_idempotent(sql: str, conn: sqlite3.Connection) -> str:
    """Strip bare ALTER TABLE ADD COLUMN statements from SQL,
    apply them via _add_column_if_missing instead, return cleaned SQL."""
    import re
    # Match ALTER TABLE ... ADD COLUMN ...; with optional trailing comments
    alter_re = re.compile(
        r"^\s*ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)\s+([^;]*?)\s*;[^\n]*$",
        re.IGNORECASE | re.MULTILINE,
    )
    alters = alter_re.findall(sql)
    for table, col, col_type in alters:
        # Strip trailing comments from type definition
        clean_type = col_type.split("--")[0].strip()
        _add_column_if_missing(conn, table, col, clean_type)
    cleaned = alter_re.sub("", sql)
    return cleaned


@pytest.fixture
def live_like_db():
    """Simulate the live database state:
    - db_manager inline DDL created all base tables
    - Some columns added by prior partial migration runs
    - schema_migrations has only 001
    """
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys = ON")

    # ── Simulate db_manager inline DDL (subset of critical tables) ──
    db.executescript("""
        CREATE TABLE IF NOT EXISTS tickets (
            ticket_id TEXT PRIMARY KEY,
            subject TEXT, trc_code TEXT, trc_label TEXT,
            status TEXT, created_at TEXT, solved_at TEXT,
            dataset_id INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS conversations (
            ticket_id TEXT PRIMARY KEY,
            subject TEXT, trc_code TEXT, trc_label TEXT,
            status TEXT, csat_score REAL, created_at TEXT,
            solved_at TEXT, message_count INTEGER,
            client_messages INTEGER, agent_messages INTEGER,
            full_thread TEXT, thread_preview TEXT,
            dataset_id INTEGER DEFAULT 0,
            content_hash TEXT,
            source TEXT DEFAULT 'csv',
            FOREIGN KEY (ticket_id) REFERENCES tickets(ticket_id)
        );

        CREATE TABLE IF NOT EXISTS chat_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT UNIQUE NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            source_page TEXT, source_context TEXT,
            trc_filter TEXT, date_start DATE, date_end DATE,
            messages TEXT, title TEXT,
            filter_json TEXT,
            ticket_count INTEGER,
            active_report_ids TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_cs_updated ON chat_sessions(updated_at);

        CREATE TABLE IF NOT EXISTS analysis_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT UNIQUE NOT NULL, run_date DATETIME NOT NULL,
            prompt_template TEXT, prompt_text TEXT,
            trc_filter TEXT, date_start DATE, date_end DATE,
            ticket_count INTEGER, model_used TEXT,
            output_text TEXT, output_structured TEXT,
            token_count INTEGER, cost_usd REAL, duration_sec REAL,
            definition_id TEXT, source TEXT DEFAULT 'manual'
        );

        CREATE TABLE IF NOT EXISTS incident_flags (
            flag_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trc_code TEXT, flag_type TEXT, theta_level TEXT,
            direction TEXT, triggered_at TEXT, triggered_date TEXT,
            triggered_hour INTEGER, observed_value REAL,
            expected_lambda REAL, threshold_value REAL,
            p_value REAL, cusum_value REAL,
            status TEXT DEFAULT 'open', notes TEXT,
            resolved_at TEXT, created_at TEXT
        );

        -- Schema migrations tracking (only 001 recorded)
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO schema_migrations (filename) VALUES ('001_initial_baseline.sql');
    """)

    # ── Seed with realistic data ──
    # 3 chat sessions with JSON message blobs (like the live DB)
    for i in range(3):
        sid = f"session-{i}"
        messages = [
            {"role": "user", "content": f"Question {i}: What bugs exist?", "timestamp": f"2026-04-0{i+1}T10:00:00"},
            {"role": "assistant", "content": f"Answer {i}: Found {i+1} bugs.", "timestamp": f"2026-04-0{i+1}T10:00:05"},
        ]
        db.execute(
            """INSERT INTO chat_sessions
               (session_id, created_at, updated_at, source_page, messages)
               VALUES (?, ?, ?, 'gemini_chats', ?)""",
            (sid, f"2026-04-0{i+1}", f"2026-04-0{i+1}", json.dumps(messages)),
        )

    # 2 incidents
    db.execute(
        "INSERT INTO incident_flags (trc_code, flag_type, theta_level, status, created_at) "
        "VALUES ('BIL-01', 'spike', 'HIGH', 'open', '2026-04-01')"
    )
    db.execute(
        "INSERT INTO incident_flags (trc_code, flag_type, theta_level, status, created_at) "
        "VALUES ('TECH-02', 'drift', 'MED', 'open', '2026-04-02')"
    )

    db.commit()
    yield db
    db.close()


# ═══════════════════════════════════════
#  TEST: Confirm the failure on raw migrations
# ═══════════════════════════════════════

class TestMigrationFailureReproduction:
    """Prove that raw migrations 002 and 006 fail on the live-like DB."""

    def test_migration_002_fails_on_duplicate_source_column(self, live_like_db):
        sql = _read_migration("002_source_warehouse.sql")
        with pytest.raises(sqlite3.OperationalError, match="duplicate column name: source"):
            live_like_db.executescript(sql)

    def test_migration_006_fails_on_duplicate_filter_json(self, live_like_db):
        sql = _read_migration("006_hybrid_chat.sql")
        with pytest.raises(sqlite3.OperationalError, match="duplicate column name: filter_json"):
            live_like_db.executescript(sql)


# ═══════════════════════════════════════
#  TEST: Idempotent migration approach works
# ═══════════════════════════════════════

class TestIdempotentMigrationChain:
    """Prove that making 002 and 006 idempotent allows the full chain to run."""

    def test_full_chain_applies_with_idempotent_alters(self, live_like_db):
        """Apply migrations 002→009 using idempotent ALTER guards."""
        db = live_like_db
        applied = []

        for fname in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            name = fname.name
            if name == "001_initial_baseline.sql":
                continue  # already applied

            sql = fname.read_text(encoding="utf-8")

            # Make ALTERs idempotent
            cleaned = _make_migration_idempotent(sql, db)

            try:
                if cleaned.strip():
                    db.executescript(cleaned)
                db.execute(
                    "INSERT OR IGNORE INTO schema_migrations (filename) VALUES (?)",
                    (name,),
                )
                db.commit()
                applied.append(name)
            except Exception as e:
                pytest.fail(f"Migration {name} failed: {e}")

        assert "002_source_warehouse.sql" in applied
        assert "005_persistence_layer.sql" in applied
        assert "006_hybrid_chat.sql" in applied
        assert "009_chat_data_layer.sql" in applied

    def test_all_new_tables_exist_after_chain(self, live_like_db):
        """After full chain, all migration-009 tables and views should exist."""
        db = live_like_db

        # Apply full chain
        for fname in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            if fname.name == "001_initial_baseline.sql":
                continue
            sql = fname.read_text(encoding="utf-8")
            cleaned = _make_migration_idempotent(sql, db)
            if cleaned.strip():
                db.executescript(cleaned)

        # Check tables
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "chat_messages" in tables
        assert "chat_projects" in tables
        assert "chat_tool_executions" in tables

        # Check views
        views = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='view'"
        ).fetchall()}
        assert "v_session_summary" in views
        assert "v_session_tools" in views
        assert "v_chat_cost_daily" in views

        # Check FTS5
        assert "chat_messages_fts" in tables

        # Check project_id column on chat_sessions
        cols = {r[1] for r in db.execute("PRAGMA table_info(chat_sessions)").fetchall()}
        assert "project_id" in cols


# ═══════════════════════════════════════
#  TEST: Data migration (JSON blob → chat_messages)
# ═══════════════════════════════════════

class TestDataMigrationAfterChain:
    """After the idempotent chain applies, the 009 post-hook should
    extract existing JSON message blobs into chat_messages rows."""

    def _apply_chain(self, db):
        """Apply full migration chain with idempotent guards."""
        for fname in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            if fname.name == "001_initial_baseline.sql":
                continue
            sql = fname.read_text(encoding="utf-8")
            cleaned = _make_migration_idempotent(sql, db)
            if cleaned.strip():
                db.executescript(cleaned)

    def test_posthook_extracts_json_messages(self, live_like_db):
        """Post-hook should extract 6 messages (2 per session × 3 sessions)."""
        db = live_like_db
        self._apply_chain(db)

        # Run the post-hook manually
        from src.updater.schema_migrator import SchemaMigrator
        SchemaMigrator._posthook_009_extract_json_messages(db)

        count = db.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]
        assert count == 6  # 3 sessions × 2 messages each

    def test_extracted_messages_have_correct_ordinals(self, live_like_db):
        db = live_like_db
        self._apply_chain(db)
        from src.updater.schema_migrator import SchemaMigrator
        SchemaMigrator._posthook_009_extract_json_messages(db)

        for sid in ["session-0", "session-1", "session-2"]:
            rows = db.execute(
                "SELECT ordinal, role, content FROM chat_messages "
                "WHERE session_id = ? ORDER BY ordinal",
                (sid,),
            ).fetchall()
            assert len(rows) == 2
            assert rows[0][0] == 0  # ordinal 0
            assert rows[0][1] == "user"
            assert rows[1][0] == 1  # ordinal 1
            assert rows[1][1] == "assistant"

    def test_v_session_summary_populated_after_migration(self, live_like_db):
        """v_session_summary should return correct data after migration + post-hook."""
        db = live_like_db
        self._apply_chain(db)
        from src.updater.schema_migrator import SchemaMigrator
        SchemaMigrator._posthook_009_extract_json_messages(db)

        rows = db.execute(
            """SELECT session_id, message_count, first_question
               FROM v_session_summary
               ORDER BY session_id"""
        ).fetchall()
        assert len(rows) == 3
        assert rows[0][1] == 2  # message_count
        assert rows[0][2] == "Question 0: What bugs exist?"  # first_question

    def test_fts5_populated_after_migration(self, live_like_db):
        """FTS5 index should be populated via triggers after post-hook inserts."""
        db = live_like_db
        self._apply_chain(db)
        from src.updater.schema_migrator import SchemaMigrator
        SchemaMigrator._posthook_009_extract_json_messages(db)

        results = db.execute(
            "SELECT message_id FROM chat_messages_fts WHERE chat_messages_fts MATCH 'bugs'"
        ).fetchall()
        assert len(results) == 6  # "bugs" appears in all 6 messages (user + assistant)

    def test_list_sessions_returns_first_question_after_migration(self, live_like_db):
        """list_sessions should now return first_question (fixing chip titles)."""
        db = live_like_db
        self._apply_chain(db)
        from src.updater.schema_migrator import SchemaMigrator
        SchemaMigrator._posthook_009_extract_json_messages(db)

        from src.services.chat_session import list_sessions
        sessions = list_sessions(5, db)
        assert len(sessions) == 3
        # All sessions should now have first_question
        for s in sessions:
            assert s.get("first_question") is not None
            assert "gemini_chats" not in (s.get("first_question") or "")

    def test_posthook_is_safe_to_rerun(self, live_like_db):
        """Running the post-hook twice should not duplicate messages."""
        db = live_like_db
        self._apply_chain(db)
        from src.updater.schema_migrator import SchemaMigrator

        SchemaMigrator._posthook_009_extract_json_messages(db)
        count_1 = db.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]

        SchemaMigrator._posthook_009_extract_json_messages(db)
        count_2 = db.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]

        assert count_1 == count_2  # INSERT OR IGNORE prevents duplicates


# ═══════════════════════════════════════
#  TEST: CRUD works correctly after full migration
# ═══════════════════════════════════════

class TestCrudAfterFullMigration:
    """After the full chain + post-hook, all CRUD operations should work."""

    def _setup(self, db):
        for fname in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            if fname.name == "001_initial_baseline.sql":
                continue
            sql = fname.read_text(encoding="utf-8")
            cleaned = _make_migration_idempotent(sql, db)
            if cleaned.strip():
                db.executescript(cleaned)
        from src.updater.schema_migrator import SchemaMigrator
        SchemaMigrator._posthook_009_extract_json_messages(db)

    def test_append_message_writes_to_chat_messages(self, live_like_db):
        db = live_like_db
        self._setup(db)

        from src.services.chat_session import append_message
        mid = append_message("session-0", "user", "New question!", db)
        assert mid is not None

        row = db.execute(
            "SELECT content FROM chat_messages WHERE message_id = ?", (mid,)
        ).fetchone()
        assert row[0] == "New question!"

    def test_load_session_reads_from_chat_messages(self, live_like_db):
        db = live_like_db
        self._setup(db)

        from src.services.chat_session import load_session
        session = load_session("session-0", db)
        assert session is not None
        # Should have 2 migrated messages
        assert len(session["messages"]) == 2
        assert session["messages"][0]["role"] == "user"

    def test_create_project_and_assign(self, live_like_db):
        db = live_like_db
        self._setup(db)

        from src.services.chat_session import create_project, assign_session_to_project, list_projects
        pid = create_project("Test Project", conn=db)
        assign_session_to_project("session-0", pid, conn=db)

        projects = list_projects(conn=db)
        assert len(projects) == 1
        assert projects[0]["session_count"] == 1

    def test_search_messages_finds_migrated_content(self, live_like_db):
        db = live_like_db
        self._setup(db)

        from src.services.chat_session import search_messages
        results = search_messages("bugs", conn=db)
        assert len(results) >= 3  # all 3 "What bugs exist?" messages
