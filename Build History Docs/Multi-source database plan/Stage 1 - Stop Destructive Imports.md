# Stage 1: Stop Destructive Imports

## Overview

**Goal**: Make all data imports additive. Remove destructive DELETE statements from the import path. Dedupe by ticket_id so only new tickets are inserted and flow through NLP. Fix the current orphaned database state.

**Scope**: Minimal code changes. No schema redesign, no new UI pages, no new columns, no redaction changes.

**Priority**: URGENT — database is currently orphaned (0 tickets/conversations, 10,000+ orphaned derived rows).

---

## File Tree (Changes Only)

```
src/
├── data/
│   ├── import_mode.py              NEW   (~20 LOC)   — ImportMode enum
│   ├── import_tracker.py           NEW   (~120 LOC)  — Import run logging + dedupe gate
│   ├── csv_ingestion.py            MOD   (~15 LOC Δ) — Remove DELETEs, add dedupe gate
│   ├── conversation_rebuild.py     MOD   (~15 LOC Δ) — Remove DELETEs, add dedupe gate
│   └── lightdash_client.py         MOD   (~5 LOC Δ)  — Pass mode param through
├── services/
│   └── clear_session.py            MOD   (~10 LOC Δ) — Update EPHEMERAL_TABLES list
├── ui/
│   ├── main_window.py              MOD   (~20 LOC Δ) — Update close dialog behavior
│   └── pages/
│       └── settings_page.py        MOD   (~30 LOC Δ) — Add "Full Database Reset" button
migrations/
│   └── 010_import_tracking.sql     NEW   (~25 LOC)   — import_runs table
tests/
│   └── test_incremental_import.py  NEW   (~200 LOC)  — Dedupe, additive, reset tests
```

**Total**: 3 new files (~365 LOC), 6 modified files (~95 LOC Δ)

---

## Danger Zones

| Area | Risk | Mitigation |
|------|------|------------|
| **Double DELETE** | Both `csv_ingestion.py:260-262` AND `conversation_rebuild.py:57-59` delete the same tables. Missing one leaves a hidden destructive path. | Remove BOTH. Test both CSV and Lightdash import paths. |
| **Orphaned FTS index** | `conversations_fts` is a virtual table that mirrors `conversations`. If conversations grow but FTS isn't rebuilt, search breaks. | FTS is already rebuilt via triggers on INSERT. Verify after first additive import. |
| **clear_session.py EPHEMERAL_TABLES** | Currently lists `tickets`, `comments`, `conversations` as ephemeral. After Stage 1, these become permanent. Clearing them on tab close would destroy the warehouse. | Remove these 3 tables from EPHEMERAL_TABLES. Clear only `raw_ingestion_rows`, `ingestion_chunks`, `nlp_batches`. |
| **_clear_all_data() fallback** | `main_window.py:1383` falls back to `_clear_all_data()` which deletes from ALL non-preserved tables. This would nuke the permanent store. | Harden the primary `clear_session_data()` path. Remove the destructive fallback or restrict it to truly ephemeral tables only. |
| **INSERT OR REPLACE semantics** | `INSERT OR REPLACE` deletes + re-inserts on PK conflict. If tickets table has FK children (comments, conversations), the implicit DELETE cascading could orphan data. | SQLite has no cascade by default and FK enforcement is OFF during clear. Verify: run `PRAGMA foreign_key_list(tickets)` to confirm no cascades. |
| **scan_orchestrator reads conversations** | `SELECT full_thread FROM conversations` — if conversations is empty (current state), scans return 0 results. After Stage 1, conversations will populate and STAY populated. | No code change needed — this self-heals once additive import runs. |

---

## Cyclomatic Complexity Targets

| File | Target CC | Notes |
|------|-----------|-------|
| `import_mode.py` | CC ≤ 3 | Enum + one converter function |
| `import_tracker.py` | CC ≤ 10 per function | 5 small functions, no branching beyond error handling |
| `csv_ingestion.py` (modified sections) | CC ≤ 12 | Remove DELETE block reduces existing CC; add one `if` for dedupe |
| `conversation_rebuild.py` (modified sections) | CC ≤ 10 | Same pattern as csv_ingestion |

---

## Step-by-Step Implementation

### Step 1: `src/data/import_mode.py` (NEW, ~20 LOC)

```python
"""Import mode definitions for CSV and Lightdash ingestion."""

from enum import Enum

class ImportMode(Enum):
    INCREMENTAL = "incremental"      # Default: skip existing ticket_ids
    FULL_REFRESH = "full_refresh"    # Nuclear: wipe and reload (Settings only)

def mode_from_ui_text(text: str) -> ImportMode:
    """Convert UI display text to ImportMode enum."""
    if text == "Full Refresh":
        return ImportMode.FULL_REFRESH
    return ImportMode.INCREMENTAL
```

**CC**: 2 (one branch in `mode_from_ui_text`).

---

### Step 2: `migrations/010_import_tracking.sql` (NEW, ~25 LOC)

```sql
-- Track import runs for auditing and dedupe support
CREATE TABLE IF NOT EXISTS import_runs (
    run_id             TEXT PRIMARY KEY,
    started_at         TEXT NOT NULL,
    completed_at       TEXT,
    source             TEXT NOT NULL,        -- 'csv' or 'lightdash'
    mode               TEXT NOT NULL,        -- 'incremental' or 'full_refresh'
    file_name          TEXT,
    tickets_seen       INTEGER DEFAULT 0,
    tickets_new        INTEGER DEFAULT 0,
    tickets_skipped    INTEGER DEFAULT 0,
    status             TEXT DEFAULT 'running',
    error_message      TEXT
);
CREATE INDEX IF NOT EXISTS idx_import_runs_status ON import_runs(source, status);
```

**No schema changes to existing tables.** This is additive only.

---

### Step 3: `src/data/import_tracker.py` (NEW, ~120 LOC)

Functions:
- `start_import_run(conn, source, mode, file_name) -> run_id` — Creates import_runs record
- `complete_import_run(conn, run_id, stats_dict)` — Sets completed_at, counts, status='completed'
- `fail_import_run(conn, run_id, error_msg)` — Sets status='failed', error_message
- `get_existing_ticket_ids(conn) -> set[str]` — `SELECT ticket_id FROM tickets` → set for O(1) lookup
- `filter_new_tickets(existing_ids: set, incoming_rows: list, id_column: str) -> tuple[list, int]` — Returns (new_rows, skipped_count)

**Key function — the dedupe gate:**
```python
def filter_new_tickets(existing_ids: set, incoming_rows: list, id_column: str = "ticket_id"):
    """Return only rows whose ticket_id is not already in the database.

    Returns: (new_rows, skipped_count)
    """
    new_rows = [r for r in incoming_rows if r.get(id_column) not in existing_ids]
    skipped = len(incoming_rows) - len(new_rows)
    return new_rows, skipped
```

**CC per function**: ≤ 5. Total file CC: ≤ 10.

---

### Step 4: Modify `src/data/csv_ingestion.py`

**Current (lines 260-262):**
```python
db.conn.execute("DELETE FROM conversations")
db.conn.execute("DELETE FROM comments")
db.conn.execute("DELETE FROM tickets")
```

**New:**
```python
# REMOVED: Destructive DELETEs. Upserts (INSERT OR REPLACE) handle merging.
# Full database reset is available in Settings only.
```

**Add dedupe gate before the write loop (in `_write_tickets_to_db`):**
```python
from src.data.import_tracker import get_existing_ticket_ids, filter_new_tickets

existing_ids = get_existing_ticket_ids(db.conn)
rows, skipped = filter_new_tickets(existing_ids, ticket_rows)
logger.info("Import: %d new, %d skipped (already exist)", len(rows), skipped)
# Only process `rows` from here forward
```

**Add `mode` parameter to `ingest_csv()` signature** (default `ImportMode.INCREMENTAL`). Mode is logged but the behavior is always the same — the DELETEs are gone. Mode only matters if someone explicitly calls with `FULL_REFRESH` from the Settings reset button (Stage 1 wiring).

**Lines changed**: ~15 LOC removed/modified.

---

### Step 5: Modify `src/data/conversation_rebuild.py`

**Current (lines 57-59):**
```python
db.conn.execute("DELETE FROM conversations")
db.conn.execute("DELETE FROM comments")
db.conn.execute("DELETE FROM tickets")
```

**New:** Remove the DELETE block entirely. The `INSERT OR REPLACE` (upsert) calls that follow handle conflicts.

**Add dedupe gate** at the grouping stage — after `_group_by_ticket()`, filter out ticket_ids already in the database:
```python
existing_ids = get_existing_ticket_ids(conn)
new_groups = {tid: events for tid, events in grouped.items() if tid not in existing_ids}
logger.info("Rebuild: %d new tickets, %d skipped", len(new_groups), len(grouped) - len(new_groups))
```

**Lines changed**: ~15 LOC.

---

### Step 6: Modify `src/data/lightdash_client.py`

**At line ~535** where `rebuild_conversations()` is called: pass through `mode` parameter.

```python
# Was:
rebuild_conversations(conn, dataset_id=dataset_id)
# Now:
rebuild_conversations(conn, dataset_id=dataset_id, mode=mode)
```

**Lines changed**: ~5 LOC (parameter threading).

---

### Step 7: Modify `src/services/clear_session.py`

**Update EPHEMERAL_TABLES** — remove `tickets`, `comments`, `conversations` from the ephemeral list. After Stage 1, these are permanent.

```python
EPHEMERAL_TABLES = [
    "raw_ingestion_rows",
    "ingestion_chunks",
    "nlp_batches",
    # REMOVED: tickets, comments, conversations — now permanent
    # REMOVED: nlp_ticket_classifications — needed for scan history
    # REMOVED: ticket_entities — needed for entity search
    # REMOVED: datasets — needed for source tracking
]
```

**Clear & Close now only clears staging data**, not source records.

---

### Step 8: Modify `src/ui/main_window.py`

**`closeEvent()` (line 1341):** Update the dialog text:
```python
msg.setInformativeText(
    "Staging data (raw import rows) will be cleared.\n"
    "All tickets, enrichments, and reports are preserved in the database.\n\n"
    "Choose 'Keep & Close' to retain everything as-is."
)
```

**Remove `_clear_all_data()` fallback** at line 1383. If `clear_session_data()` fails, log and close — don't nuke everything.

```python
except Exception as e:
    logging.getLogger("alma.main").warning(
        "Session clear failed: %s (data preserved)", e
    )
# REMOVED: self._clear_all_data() fallback
```

---

### Step 9: Add "Full Database Reset" to Settings

**`src/ui/pages/settings_page.py`:** Add a clearly-labeled danger zone button:

```python
# In the Display or new "Data" tab:
reset_btn = QPushButton("Full Database Reset")
reset_btn.setStyleSheet("background-color: #D32F2F; color: white;")
reset_btn.setToolTip("Permanently delete ALL data and start fresh. This cannot be undone.")
reset_btn.clicked.connect(self._full_database_reset)
```

`_full_database_reset()` shows a confirmation dialog ("Type DELETE to confirm"), then calls the old `_clear_all_data()` logic.

---

### Step 10: Comprehensive Testing Strategy

#### Test File Structure

```
tests/
├── unit/
│   ├── test_import_mode.py            NEW (~30 LOC)   — Enum values, UI text conversion
│   ├── test_import_tracker.py         NEW (~120 LOC)  — All tracker functions in isolation
│   └── test_dedupe_gate.py            NEW (~80 LOC)   — filter_new_tickets logic
├── integration/
│   ├── test_csv_incremental.py        NEW (~150 LOC)  — Full CSV import path, additive behavior
│   ├── test_lightdash_incremental.py  NEW (~120 LOC)  — Full Lightdash path, rebuild + dedupe
│   └── test_clear_session_updated.py  NEW (~100 LOC)  — Clear & Close preserves permanent data
├── schema/
│   └── test_migration_010.py          NEW (~80 LOC)   — Migration safety, idempotency, rollback
├── regression/
│   └── test_stage1_regression.py      NEW (~60 LOC)   — Existing 631+ tests unbroken
└── debug/
    └── conftest.py                    MOD (~30 LOC Δ)  — Qt debug fixtures, test DB factory
```

#### Debug Configuration

All test files include a module docstring:

```python
"""
Test Module: [module name]
Stage: 1
Dependencies: None (Stage 1 is the foundation)
Tests: [count]

Covers:
  - [list of functions/classes under test]
  - [expected behaviors validated]

Run:
  - Single file:  python -m pytest tests/unit/test_import_mode.py -x -v
  - All Stage 1:  python -m pytest tests/ -k "stage1 or incremental or dedupe" -x -v
  - With Qt debug: QT_LOGGING_RULES="alma.*=true" python -m pytest ...
  - With SQL debug: ALMA_DEBUG_SQL=1 python -m pytest ...
  - Verbose DB:    python -m pytest --log-cli-level=DEBUG ...
"""
```

Qt debug logging fixture in `conftest.py`:
```python
@pytest.fixture(autouse=True)
def qt_debug_logging(caplog):
    """Route Qt debug messages to Python logging for test assertion."""
    import logging
    logging.getLogger("alma").setLevel(logging.DEBUG)
    # qDebug/qWarning/qCritical → caplog for assertion in tests

@pytest.fixture
def test_db(tmp_path):
    """Create an isolated test database with schema applied."""
    from src.data.db_manager import DatabaseManager
    db_path = tmp_path / "test_alma.db"
    db = DatabaseManager(str(db_path))
    db.initialize()
    # Run all migrations through 010
    from src.updater.schema_migrator import SchemaMigrator
    SchemaMigrator(str(db_path)).run_pending()
    yield db
    db.close()
```

---

#### Unit Tests — `test_import_mode.py` (~30 LOC)

```python
"""
Test Module: ImportMode Enum
Stage: 1
Tests: 4

Covers:
  - ImportMode enum values (INCREMENTAL, FULL_REFRESH)
  - mode_from_ui_text() conversion
  - Default behavior (unknown text → INCREMENTAL)
"""
```

| Test | What It Validates | CC Check |
|------|-------------------|----------|
| `test_enum_values` | `INCREMENTAL.value == "incremental"`, `FULL_REFRESH.value == "full_refresh"` | N/A |
| `test_mode_from_ui_full_refresh` | `mode_from_ui_text("Full Refresh") == FULL_REFRESH` | CC=2 verified |
| `test_mode_from_ui_incremental` | `mode_from_ui_text("Incremental") == INCREMENTAL` | CC=2 verified |
| `test_mode_from_ui_unknown_defaults` | `mode_from_ui_text("garbage") == INCREMENTAL` | Safe default |

---

#### Unit Tests — `test_import_tracker.py` (~120 LOC)

```python
"""
Test Module: Import Tracker
Stage: 1
Tests: 10

Covers:
  - start_import_run() — creates record, returns run_id
  - complete_import_run() — sets completed_at, stats, status
  - fail_import_run() — sets error_message, status='failed'
  - get_existing_ticket_ids() — returns set of all ticket_ids
  - filter_new_tickets() — separates new from existing
"""
```

| Test | What It Validates |
|------|-------------------|
| `test_start_import_run_creates_record` | Record created in import_runs table with status='running' |
| `test_start_import_run_unique_id` | Two calls produce different run_ids |
| `test_complete_import_run` | completed_at set, stats recorded, status='completed' |
| `test_fail_import_run` | error_message set, status='failed' |
| `test_complete_nonexistent_run` | Raises or handles gracefully (no crash) |
| `test_get_existing_ticket_ids_empty` | Returns empty set on fresh DB |
| `test_get_existing_ticket_ids_populated` | Returns correct set after inserts |
| `test_filter_new_tickets_all_new` | 10 incoming, 0 existing → returns all 10, skipped=0 |
| `test_filter_new_tickets_all_existing` | 10 incoming, 10 existing → returns 0 new, skipped=10 |
| `test_filter_new_tickets_mixed` | 100 incoming, 95 existing → returns 5 new, skipped=95 |

---

#### Unit Tests — `test_dedupe_gate.py` (~80 LOC)

```python
"""
Test Module: Dedupe Gate Logic
Stage: 1
Tests: 6

Covers:
  - Ticket ID matching (exact string match)
  - Empty incoming list
  - Empty existing set
  - Large dataset performance (10k+ IDs)
  - Case sensitivity
  - Custom ID column name
"""
```

| Test | What It Validates |
|------|-------------------|
| `test_dedupe_exact_match` | ticket_id "12345" matches existing "12345" → skipped |
| `test_dedupe_no_partial_match` | "123" does NOT match "1234" |
| `test_dedupe_empty_incoming` | Empty list → empty result, no crash |
| `test_dedupe_empty_existing` | Empty existing set → all incoming pass through |
| `test_dedupe_case_sensitive` | "ABC-123" ≠ "abc-123" (IDs are case-sensitive) |
| `test_dedupe_performance_10k` | 10,000 incoming against 10,000 existing completes under 100ms |

---

#### Integration Tests — `test_csv_incremental.py` (~150 LOC)

```python
"""
Test Module: CSV Incremental Import (Integration)
Stage: 1
Tests: 8

Covers:
  - Full CSV import path: file → parse → dedupe → write → verify
  - Additive behavior: two imports don't destroy first
  - Orphan recovery: import reconnects orphaned ticket_index rows
  - FTS index: additive inserts trigger FTS update

Prerequisites:
  - Test DB with schema + migrations applied
  - Sample CSV files in tests/fixtures/

Debug:
  - ALMA_DEBUG_SQL=1 to trace all SQL statements during import
"""
```

| Test | What It Validates |
|------|-------------------|
| `test_csv_import_populates_tables` | After import: tickets > 0, conversations > 0, comments > 0 |
| `test_csv_import_no_delete_executed` | Mock db.conn.execute — verify no DELETE FROM called |
| `test_csv_import_twice_no_duplicates` | Import same CSV twice → row count unchanged |
| `test_csv_import_additive_different_files` | Import CSV A (50 tickets), then CSV B (30 tickets) → 80 total |
| `test_csv_import_five_new_of_2000` | 2000 incoming, 1995 existing → only 5 new inserted |
| `test_csv_import_fts_updated` | After additive import, FTS search finds new tickets |
| `test_csv_import_orphan_recovery` | ticket_index has 886 orphaned rows → import reconnects them |
| `test_csv_import_tracker_recorded` | import_runs table has new record with correct stats |

---

#### Integration Tests — `test_lightdash_incremental.py` (~120 LOC)

```python
"""
Test Module: Lightdash Incremental Import (Integration)
Stage: 1
Tests: 6

Covers:
  - Full Lightdash path: API pull → raw_ingestion_rows → rebuild → dedupe → write
  - conversation_rebuild.py additive behavior
  - Mode parameter threading from lightdash_client → rebuild

Prerequisites:
  - Lightdash mock client (src/data/lightdash_mock.py)
"""
```

| Test | What It Validates |
|------|-------------------|
| `test_lightdash_import_no_delete` | Mock rebuild_conversations — no DELETE executed |
| `test_lightdash_rebuild_additive` | Rebuild on existing data → upserts, no data loss |
| `test_lightdash_dedupe` | Same data pulled twice → no duplicates |
| `test_lightdash_mode_passthrough` | Mode parameter reaches rebuild_conversations() |
| `test_lightdash_raw_rows_staging` | raw_ingestion_rows populated, then conversations rebuilt |
| `test_lightdash_import_tracker` | import_runs record created for Lightdash imports |

---

#### Integration Tests — `test_clear_session_updated.py` (~100 LOC)

```python
"""
Test Module: Clear Session (Updated for Persistent Data)
Stage: 1
Tests: 8

Covers:
  - Clear & Close preserves tickets, conversations, comments
  - Clear & Close clears raw_ingestion_rows, ingestion_chunks, nlp_batches
  - Full Database Reset (Settings) clears everything
  - _clear_all_data() fallback removed — graceful error handling
  - FTS index rebuilt after clear

Debug:
  - Qt debug captures QMessageBox interactions via QTest
"""
```

| Test | What It Validates |
|------|-------------------|
| `test_clear_preserves_tickets` | After clear_session_data(): tickets table row count unchanged |
| `test_clear_preserves_conversations` | conversations table row count unchanged |
| `test_clear_preserves_comments` | comments table row count unchanged |
| `test_clear_preserves_ticket_index` | ticket_index row count unchanged |
| `test_clear_clears_staging` | raw_ingestion_rows = 0 after clear |
| `test_clear_clears_nlp_batches` | nlp_batches = 0 after clear |
| `test_full_reset_clears_all` | After full reset: ALL tables = 0 |
| `test_full_reset_requires_confirmation` | Reset without typing "DELETE" → no action taken |

---

#### Schema Tests — `test_migration_010.py` (~80 LOC)

```python
"""
Test Module: Migration 010 (Import Tracking)
Stage: 1
Tests: 6

Covers:
  - import_runs table created with correct columns
  - Index created on (source, status)
  - Migration is idempotent (running twice doesn't error)
  - Migration doesn't affect existing tables
  - Rollback safety (failed migration leaves clean state)
  - Data types correct (TEXT, INTEGER, DEFAULT values)
"""
```

| Test | What It Validates |
|------|-------------------|
| `test_import_runs_table_created` | Table exists after migration |
| `test_import_runs_columns` | All expected columns present with correct types |
| `test_import_runs_index` | idx_import_runs_status index exists |
| `test_migration_idempotent` | Running migration twice → no error, no duplicate tables |
| `test_migration_preserves_existing` | tickets, conversations, ticket_index unchanged after migration |
| `test_migration_default_values` | status defaults to 'running', integer fields default to 0 |

---

#### Regression Tests — `test_stage1_regression.py` (~60 LOC)

```python
"""
Test Module: Stage 1 Regression
Stage: 1
Tests: 5

Covers:
  - All 631+ existing tests pass after Stage 1 changes
  - No import of new modules breaks existing imports
  - scan_orchestrator still reads conversations correctly
  - NLP pipeline functions unchanged

Run:
  - Full suite: python -m pytest tests/ -x -q
  - Regression only: python -m pytest tests/regression/ -x -v
"""
```

| Test | What It Validates |
|------|-------------------|
| `test_csv_ingestion_import_works` | `from src.data.csv_ingestion import ingest_csv` succeeds |
| `test_conversation_rebuild_import_works` | `from src.data.conversation_rebuild import rebuild_conversations` succeeds |
| `test_scan_orchestrator_reads_conversations` | scan_orchestrator can SELECT from conversations (populated DB) |
| `test_ticket_index_writer_unchanged` | ticket_index_writer.should_classify_ticket() still works |
| `test_upsert_methods_unchanged` | db.upsert_ticket/comment/conversation signatures unchanged |

---

## What NOT to Change (Stage 1)

| File/Area | Why |
|-----------|-----|
| `db_manager.py` upsert methods | Already correct (`INSERT OR REPLACE`) |
| `scan_orchestrator.py` | Reads conversations — self-heals once data persists |
| `ticket_index_writer.py` | Already has dedupe gate (`should_classify_ticket()`) |
| `raw_ingestion_rows` handling | Already uses `INSERT OR IGNORE` |
| `db_manager.py:1599 delete_dataset()` | Intentional for A/B dataset management |
| Analytics pages | They read conversations — will work once data persists |
| Redaction patterns | Stage 2 concern |
| Smart Reporting dropdown | Not related to import mode; it's auto-import scheduling config |
| Source tagging | Stage 2 concern |
| New columns (provider_id, client_id) | Stage 2 concern |

---

## Verification Checklist

- [ ] Both DELETE blocks removed (csv_ingestion.py AND conversation_rebuild.py)
- [ ] Dedupe gate tested with overlapping ticket_ids
- [ ] Clear & Close preserves tickets/conversations/comments
- [ ] Clear & Close still clears raw_ingestion_rows
- [ ] Settings "Full Database Reset" works with confirmation
- [ ] `_clear_all_data()` fallback removed from closeEvent
- [ ] Import tracker records run_id, counts, status
- [ ] Existing test suite passes (631+ tests, 0 regressions)
- [ ] First additive import after orphaned state reconnects data
- [ ] FTS index rebuilds correctly on additive insert
