# Stage 2: Evolved Data Model + Always-On Analytics

## Overview

**Goal**: Update the database schema to support multiple data sources with separate tables per source type, add provider_id/client_id columns, make analytics pages query the permanent database instead of requiring loaded conversations, and refine PHI/PII redaction to be surgical rather than aggressive.

**Prerequisite**: Stage 1 complete (additive imports working, no destructive DELETEs).

**Scope**: Schema evolution, source registry, analytics decoupling, redaction refinement.

---

## File Tree

```
src/
├── data/
│   ├── source_registry.py            NEW   (~200 LOC)  — Source type definitions, table creation, column mapping
│   ├── schema_builder.py             NEW   (~150 LOC)  — Dynamic table DDL from source config
│   ├── warehouse_query.py            NEW   (~180 LOC)  — Unified query interface across source tables
│   ├── redaction_engine.py           NEW   (~200 LOC)  — Entity-aware PHI scrubbing (replaces regex-only)
│   ├── csv_ingestion.py              MOD   (~40 LOC Δ) — Source-aware ingestion, call redaction engine
│   ├── conversation_rebuild.py       MOD   (~30 LOC Δ) — Source-aware rebuild, target table routing
│   ├── lightdash_client.py           MOD   (~20 LOC Δ) — Pass source_config through pipeline
│   ├── db_manager.py                 MOD   (~60 LOC Δ) — New queries for source tables, provider/client lookups
│   ├── incident_engine.py            MOD   (~30 LOC Δ) — Query warehouse_query instead of conversations
│   ├── trending_engine.py            MOD   (~30 LOC Δ) — Query warehouse_query instead of conversations
│   ├── voc_builder.py                MOD   (~25 LOC Δ) — Query warehouse_query instead of conversations
│   ├── theta_engine.py               MOD   (~20 LOC Δ) — Query warehouse_query instead of conversations
│   ├── product_gap_engine.py         MOD   (~15 LOC Δ) — Query warehouse_query instead of conversations
│   ├── entity_extractor.py           MOD   (~30 LOC Δ) — Integrate with redaction_engine for entity-aware scrub
│   └── settings_manager.py           MOD   (~10 LOC Δ) — Source config persistence
├── agents/
│   └── scan_orchestrator.py          MOD   (~40 LOC Δ) — Read from source tables via warehouse_query
├── services/
│   └── clear_session.py              MOD   (~10 LOC Δ) — Source-aware clearing
├── ui/
│   ├── pages/
│   │   ├── conversation_search.py    MOD   (~50 LOC Δ) — Source selector in import flow
│   │   ├── incidents_page.py         MOD   (~20 LOC Δ) — Remove conversation-loaded check, add source filter
│   │   ├── trending_topics.py        MOD   (~20 LOC Δ) — Remove conversation-loaded check, add source filter
│   │   ├── ai_reports.py             MOD   (~20 LOC Δ) — Source selector for report scope
│   │   └── settings_page.py          MOD   (~40 LOC Δ) — Source management UI (create/list sources)
│   └── dialogs/
│       └── source_config_dialog.py   NEW   (~250 LOC)  — Create/edit source type dialog
├── config/
│   ├── redaction_patterns.json       MOD               — Refined patterns (less aggressive)
│   └── entities/
│       └── phi_allowlist.json        NEW   (~50 LOC)   — Insurance names, business entities to preserve
migrations/
│   ├── 011_source_registry.sql       NEW   (~40 LOC)   — source_registry table
│   ├── 012_default_zendesk_source.sql NEW  (~30 LOC)   — Migrate existing data to zendesk_default source
│   └── 013_provider_client_ids.sql   NEW   (~15 LOC)   — Add provider_id, client_id to ticket tables
tests/
│   ├── test_source_registry.py       NEW   (~250 LOC)  — Source CRUD, table creation, column mapping
│   ├── test_warehouse_query.py       NEW   (~200 LOC)  — Cross-source queries, filtering, date ranges
│   ├── test_redaction_engine.py      NEW   (~200 LOC)  — PHI removal, entity preservation, edge cases
│   ├── test_always_on_analytics.py   NEW   (~250 LOC)  — Analytics without loaded conversations
│   └── test_schema_migrations.py     NEW   (~150 LOC)  — Migration safety, rollback, data preservation
```

**Total**: 7 new source files (~1,230 LOC), 16 modified files (~510 LOC Δ), 5 new test files (~1,050 LOC), 3 migrations, 2 config files

---

## Danger Zones

| Area | Risk | Mitigation |
|------|------|------------|
| **Existing conversations table** | Stage 2 creates per-source tables but the old `conversations` table has data from Stage 1 imports. Must migrate, not orphan. | Migration 012 copies existing data into `zendesk_default_*` tables, then marks old table as deprecated. Don't drop it until Stage 3 verification. |
| **42+ files read `conversations` table** | Every analytics engine, scan orchestrator, chat tool, and UI page queries `conversations` directly. Changing the table name breaks everything. | `warehouse_query.py` provides a compatibility layer: same function signatures, same return shapes, different underlying table. Migrate call sites incrementally. |
| **scan_orchestrator.py (2268 lines)** | The largest and most critical file. Reads `SELECT full_thread FROM conversations`. Modifying incorrectly breaks the entire NLP pipeline. | Single-point change: replace the 3-4 SELECT queries with `warehouse_query.get_conversations()` calls. Same return format. Test with existing scan test suite. |
| **FTS5 index coupling** | `conversations_fts` is bound to the `conversations` table. Per-source tables need per-source FTS, or a unified FTS across sources. | Create per-source FTS tables (e.g., `zendesk_default_fts`). `warehouse_query` unions across them for search. |
| **PHI redaction over-stripping** | Current `redaction_patterns.json` uses broad regex. Making it less aggressive could leave real PHI in the database. | Use entity-aware redaction: check against `phi_allowlist.json` (insurance names) before stripping. Add comprehensive test cases with known PHI + known business entities. |
| **Provider/client ID population** | New columns will be NULL for all existing data. Analytics must handle NULL gracefully. | Default to NULL. Filter UIs show "All" by default. Analytics queries use `WHERE provider_id = ? OR ? IS NULL` pattern. |
| **Always-on analytics state** | Analytics pages currently check `if conversations loaded` before running. Removing this check when the warehouse is empty could produce confusing empty results. | Replace with `if warehouse has data` check. Show "No data imported yet" instead of "Load conversations first." |

---

## Cyclomatic Complexity Targets

| File | Target CC | Strategy |
|------|-----------|----------|
| `source_registry.py` | CC ≤ 10 per function | Small CRUD functions. `create_source()` is the most complex (~8) due to validation. |
| `schema_builder.py` | CC ≤ 8 per function | DDL generation is string templating, not logic branching. |
| `warehouse_query.py` | CC ≤ 12 per function | `get_conversations()` has source routing + date filtering. Keep under 12. |
| `redaction_engine.py` | CC ≤ 10 per function | `scrub_text()` has entity detection + pattern matching. Split into `_detect_entities()` + `_apply_patterns()` to keep each under 10. |
| `source_config_dialog.py` | CC ≤ 12 per function | UI dialogs tend higher. Keep validation logic in separate functions. |

---

## Architecture: Source Registry Pattern

### Source Registry Table (`source_registry`)

```sql
CREATE TABLE IF NOT EXISTS source_registry (
    source_id          TEXT PRIMARY KEY,     -- e.g., 'zendesk_default', 'zendesk_provider_group'
    source_name        TEXT NOT NULL,        -- Display name: "RCM Support Tickets"
    source_type        TEXT NOT NULL,        -- 'zendesk', 'kodif', 'custom'
    table_prefix       TEXT NOT NULL UNIQUE, -- e.g., 'zendesk_default' → tables: zendesk_default_tickets, etc.
    column_mapping     TEXT,                 -- JSON: maps source columns to internal schema
    created_at         TEXT NOT NULL,
    is_default         INTEGER DEFAULT 0,    -- 1 for the original Zendesk source
    ticket_count       INTEGER DEFAULT 0,    -- Cached count for UI
    last_import_at     TEXT
);
```

### Per-Source Tables (created dynamically by `schema_builder.py`)

For each registered source, three tables are created:

```sql
-- Example for source_id = 'zendesk_default'
CREATE TABLE zendesk_default_tickets (
    ticket_id       TEXT PRIMARY KEY,
    subject         TEXT,
    trc_code        TEXT,
    created_at      TEXT,
    resolved_at     TEXT,
    status          TEXT,
    priority        TEXT,
    provider_id     TEXT,              -- NEW: nullable
    client_id       TEXT,              -- NEW: nullable
    csat_score      REAL,
    requester_hash  TEXT,
    source_id       TEXT NOT NULL DEFAULT 'zendesk_default',
    imported_at     TEXT NOT NULL,
    metadata        TEXT DEFAULT '{}'  -- JSON for extensible fields
);

CREATE TABLE zendesk_default_conversations (
    ticket_id       TEXT PRIMARY KEY,
    subject         TEXT,
    trc_code        TEXT,
    full_thread     TEXT,              -- PHI-scrubbed conversation body
    thread_preview  TEXT,
    created_at      TEXT,
    comment_count   INTEGER DEFAULT 0,
    source_id       TEXT NOT NULL DEFAULT 'zendesk_default',
    FOREIGN KEY (ticket_id) REFERENCES zendesk_default_tickets(ticket_id)
);

CREATE TABLE zendesk_default_comments (
    comment_id      TEXT PRIMARY KEY,
    ticket_id       TEXT,
    author_role     TEXT,
    body            TEXT,              -- PHI-scrubbed
    created_at      TEXT,
    source_id       TEXT NOT NULL DEFAULT 'zendesk_default',
    FOREIGN KEY (ticket_id) REFERENCES zendesk_default_tickets(ticket_id)
);

-- FTS index per source
CREATE VIRTUAL TABLE zendesk_default_fts USING fts5(
    ticket_id, subject, trc_label, full_thread
);
```

### Minimum Required Fields (every source must have)

| Field | Why Required |
|-------|-------------|
| `ticket_id` (or conversation_id) | Dedupe key, join key for enrichments |
| `created_at` | Date filtering for analytics, trending, incidents |
| `full_thread` (or conversation_body) | NLP pipeline input — classification, sentiment, TF-IDF |
| `subject` (or title) | Search, display, FTS |
| `trc_code` (or category) | TRC analytics, incident detection, Poisson baselines |

### Additive/Optional Fields

| Field | When Present |
|-------|-------------|
| `provider_id` | Provider ticket filtering |
| `client_id` | Client-level aggregation |
| `csat_score` | CSAT heatmaps, correlation |
| `priority` | Priority-based analytics |
| `status` | Open/closed filtering |
| `metadata` JSON | Any source-specific fields (insurance_id, session_status, etc.) |

---

## Architecture: Warehouse Query Layer

`warehouse_query.py` provides a unified interface:

```python
class WarehouseQuery:
    """Unified query interface across all source tables."""

    def __init__(self, conn, source_registry):
        self.conn = conn
        self.registry = source_registry

    def get_conversations(self, source_id=None, date_start=None, date_end=None,
                          trc_filter=None, provider_id=None, client_id=None):
        """Query conversations from one or all sources.

        Returns same shape as old `SELECT * FROM conversations` for backward compat.
        """
        if source_id:
            table = self.registry.get_table_name(source_id, "conversations")
            return self._query_single(table, date_start, date_end, trc_filter)
        else:
            # Union across all sources
            return self._query_all(date_start, date_end, trc_filter)

    def get_ticket_count(self, source_id=None):
        """Total ticket count across one or all sources."""

    def get_trc_distribution(self, source_id=None, date_start=None, date_end=None):
        """TRC code → count mapping for analytics."""

    def get_full_threads(self, ticket_ids: list, source_id=None):
        """Fetch full_thread for specific ticket_ids."""
```

### Analytics Migration Pattern

Each analytics engine changes from:
```python
# OLD
rows = conn.execute("SELECT full_thread FROM conversations WHERE ...").fetchall()
```
to:
```python
# NEW
wq = WarehouseQuery(conn, source_registry)
rows = wq.get_conversations(source_id=source_id, date_start=start, date_end=end)
```

Same return shape. Same downstream processing. The only change is the query source.

---

## Architecture: Redaction Engine

### Current Problem

`config/redaction_patterns.json` uses broad regex patterns that can strip insurance names (e.g., "Oscar" matches as a person name, "Aetna" could match as a proper noun).

### Solution: Entity-Aware Redaction

```python
class RedactionEngine:
    """PHI/PII scrubbing with business entity preservation."""

    def __init__(self, allowlist_path, patterns_path):
        self.allowlist = load_json(allowlist_path)  # Insurance names, business entities
        self.patterns = load_json(patterns_path)     # Regex for PHI detection

    def scrub(self, text: str) -> str:
        """Remove PHI/PII while preserving business entities.

        Order of operations:
        1. Tag business entities (insurance names, TRC codes) as KEEP
        2. Apply PHI patterns (names, emails, DOB, SSN, etc.)
        3. Skip matches that overlap with KEEP regions
        4. Replace PHI matches with [REDACTED]
        """
```

### PHI Removal List

| REMOVE | Pattern Type |
|--------|-------------|
| Person names | NER + title-case heuristic, excluding allowlist |
| Person initials | 2-3 uppercase letters not in known acronyms |
| Email addresses | RFC 5322 regex |
| Phone numbers | US phone format regex |
| SSN | XXX-XX-XXXX pattern |
| Credit card numbers | Luhn-valid 13-19 digit sequences |
| Date of birth | DOB/born/birthday context + date pattern |
| Medical record numbers | MRN/medical record + alphanumeric |
| Personal addresses | Street address heuristic (number + street name) |

### Business Entity Allowlist (`config/entities/phi_allowlist.json`)

```json
{
    "insurance_names": ["UHC", "United Healthcare", "Oscar", "Aetna", "BlueCross", "Blue Cross Blue Shield", "BCBS", "Cigna", "Humana", "Kaiser", "Anthem", "Molina", "Centene", "WellCare", "Ambetter"],
    "business_acronyms": ["TRC", "CSAT", "NLP", "RCM", "EOB", "CPT", "ICD", "NPI", "EIN"],
    "preserve_patterns": ["policy#", "claim#", "auth#", "case#"]
}
```

---

## Always-On Analytics Migration

### Current State

Each analytics page checks for loaded conversations before allowing analysis:

```python
# incidents_page.py (approximate pattern)
def _run_analysis(self):
    count = self.db.get_ticket_count()
    if count == 0:
        self._show_empty_state("No conversations loaded")
        return
```

### New State

```python
def _run_analysis(self):
    wq = WarehouseQuery(self.db.conn, self.source_registry)
    count = wq.get_ticket_count(source_id=self.selected_source)
    if count == 0:
        self._show_empty_state("No data in warehouse. Import data via Conversation Search.")
        return
    # Run analysis against warehouse — no conversation loading required
```

### Pages to Migrate

| Page | Current Check | New Behavior |
|------|--------------|-------------|
| `incidents_page.py` | `if not trc_codes: return` | Query warehouse. Always available if data exists. |
| `trending_topics.py` | `if not conversations: return` | Query warehouse. Date range filter drives scope. |
| `ai_reports.py` | `if ticket_count == 0: return` | Query warehouse. Source selector added. |
| `conversation_search.py` | Searches loaded conversations | Searches warehouse (all sources or filtered). |
| `trc_analytics.py` | Already uses `source_trc_daily` (persistent) | No change needed. |

---

## Migrations

### 011_source_registry.sql

Creates the `source_registry` table and the default Zendesk source entry.

### 012_default_zendesk_source.sql

Migrates existing data:
1. Create `zendesk_default_tickets`, `zendesk_default_conversations`, `zendesk_default_comments` tables
2. Copy data from `tickets` → `zendesk_default_tickets` (with `source_id = 'zendesk_default'`)
3. Copy data from `conversations` → `zendesk_default_conversations`
4. Copy data from `comments` → `zendesk_default_comments`
5. Create FTS index `zendesk_default_fts`
6. **Do NOT drop old tables** — keep as deprecated until Stage 3 verifies no code references them

### 013_provider_client_ids.sql

Adds `provider_id` and `client_id` columns to ticket tables. These are nullable — existing data will have NULL.

---

## Testing Strategy

### Test Structure

```
tests/
├── unit/
│   ├── test_source_registry.py       — Source CRUD, validation, table naming
│   ├── test_schema_builder.py        — DDL generation, column mapping
│   ├── test_warehouse_query.py       — Query routing, date filtering, source filtering
│   ├── test_redaction_engine.py      — PHI removal, allowlist preservation, edge cases
│   └── test_import_mode.py           — Enum conversion, defaults
├── integration/
│   ├── test_always_on_analytics.py   — Analytics engines query warehouse without loaded conversations
│   ├── test_source_migration.py      — Data migration from old tables to new source tables
│   └── test_end_to_end_import.py     — CSV → dedupe → source table → warehouse query → analytics
├── schema/
│   └── test_schema_migrations.py     — Migration safety, idempotency, rollback, data preservation
├── regression/
│   └── test_stage2_regression.py     — Existing 631+ tests pass after Stage 2 changes
└── debug/
    └── conftest.py                   — Qt debug logging fixtures, test DB setup/teardown
```

### Unit Tests — `test_source_registry.py` (~250 LOC)

| Test | What It Validates |
|------|-------------------|
| `test_create_source_valid` | Create source → registry entry + 3 tables + FTS index created |
| `test_create_source_duplicate_name` | Duplicate source_id → raises ValueError |
| `test_create_source_invalid_chars` | Special chars in source_id → sanitized or rejected |
| `test_get_source_by_id` | Lookup returns correct config |
| `test_list_sources` | Returns all registered sources |
| `test_default_source_exists` | `zendesk_default` is always present after migration |
| `test_column_mapping_json` | Column mapping stored/retrieved as valid JSON |
| `test_table_prefix_uniqueness` | No two sources can share a table prefix |
| `test_delete_source_with_data` | Refuses deletion if tickets exist (safety) |
| `test_update_source_metadata` | Updates name, column_mapping without affecting data |

### Unit Tests — `test_warehouse_query.py` (~200 LOC)

| Test | What It Validates |
|------|-------------------|
| `test_query_single_source` | Returns only rows from specified source |
| `test_query_all_sources` | Returns union across all sources |
| `test_date_range_filtering` | Start/end date filters work correctly |
| `test_trc_filtering` | TRC code filter returns matching rows only |
| `test_provider_id_filtering` | Provider filter works, NULL handled gracefully |
| `test_client_id_filtering` | Client filter works, NULL handled gracefully |
| `test_empty_source` | Returns empty result set, not error |
| `test_ticket_count_accuracy` | Count matches actual rows |
| `test_return_shape_compat` | Return columns match old `SELECT * FROM conversations` shape |
| `test_fts_search_across_sources` | Full-text search unions across source FTS tables |

### Unit Tests — `test_redaction_engine.py` (~200 LOC)

| Test | What It Validates |
|------|-------------------|
| `test_remove_person_name` | "John Smith called about..." → "[REDACTED] called about..." |
| `test_preserve_insurance_name` | "Patient has UHC coverage" → preserved |
| `test_preserve_oscar` | "Oscar" as insurance name → preserved (not redacted as person name) |
| `test_remove_email` | "Contact john@example.com" → "Contact [REDACTED]" |
| `test_remove_phone` | "(555) 123-4567" → "[REDACTED]" |
| `test_remove_ssn` | "SSN 123-45-6789" → "SSN [REDACTED]" |
| `test_remove_dob` | "DOB: 01/15/1990" → "DOB: [REDACTED]" |
| `test_remove_credit_card` | "CC 4111111111111111" → "CC [REDACTED]" |
| `test_preserve_business_dates` | "Ticket created 2026-03-15" → preserved |
| `test_preserve_trc_codes` | "TRC-AUTH-001" → preserved |
| `test_preserve_client_ids` | "Client ID: CLT-12345" → preserved |
| `test_preserve_policy_numbers` | "Policy# UHC-987654" → preserved |
| `test_mixed_content` | Text with both PHI and business entities → correct selective redaction |
| `test_empty_input` | Empty string → empty string (no crash) |
| `test_no_false_positives` | Business-only text → no redaction applied |

### Integration Tests — `test_always_on_analytics.py` (~250 LOC)

| Test | What It Validates |
|------|-------------------|
| `test_incidents_no_loaded_conversations` | Incident engine runs against warehouse, not conversations table |
| `test_trending_no_loaded_conversations` | Trending engine runs against warehouse |
| `test_voc_no_loaded_conversations` | VOC builder samples from warehouse |
| `test_analytics_with_source_filter` | Analytics scoped to single source |
| `test_analytics_date_range` | Date range filter on warehouse data |
| `test_analytics_empty_warehouse` | Graceful empty state, not crash |
| `test_scan_orchestrator_reads_warehouse` | Scan orchestrator batches from warehouse |
| `test_report_builder_reads_warehouse` | Report evidence extraction from warehouse |

### Schema Tests — `test_schema_migrations.py` (~150 LOC)

| Test | What It Validates |
|------|-------------------|
| `test_migration_011_creates_registry` | source_registry table created with correct schema |
| `test_migration_012_copies_data` | Existing tickets/conversations copied to zendesk_default_* tables |
| `test_migration_012_preserves_counts` | Row counts match before/after migration |
| `test_migration_012_old_tables_intact` | Old tables not dropped (deprecated, not deleted) |
| `test_migration_013_adds_columns` | provider_id, client_id columns added as nullable |
| `test_migration_idempotent` | Running migrations twice doesn't error or duplicate |
| `test_migration_order` | Migrations 011 → 012 → 013 must run in sequence |
| `test_rollback_safety` | Failed migration doesn't leave partial state |

### Regression Tests — `test_stage2_regression.py` (~100 LOC)

| Test | What It Validates |
|------|-------------------|
| `test_existing_test_suite_passes` | All 631+ existing tests pass after Stage 2 |
| `test_csv_import_still_works` | Stage 1 additive import unaffected |
| `test_conversation_search_still_works` | FTS search returns results |
| `test_nlp_scan_still_works` | Scan orchestrator classifies tickets |
| `test_clear_session_still_works` | Clear & Close behavior unchanged from Stage 1 |

### Debug Configuration

All test files include:

```python
"""
Test Module: [module name]
Stage: 2
Dependencies: Stage 1 complete
Tests: [count]

Covers:
  - [list of what this file tests]
  - [each function/class under test]

Debug:
  - Run with: python -m pytest tests/unit/test_source_registry.py -x -v
  - Qt debug: QT_LOGGING_RULES="alma.*=true" python -m pytest ...
  - DB debug: Set ALMA_DEBUG_SQL=1 to log all SQL queries
"""
```

Qt debug logging fixture in `conftest.py`:
```python
@pytest.fixture(autouse=True)
def qt_debug_logging(caplog):
    """Enable Qt debug output for all tests."""
    import logging
    logging.getLogger("alma").setLevel(logging.DEBUG)
    # PySide6 message handler → Python logging bridge
    # Captures qDebug, qWarning, qCritical for assertion in tests
```

---

## What NOT to Change (Stage 2)

| File/Area | Why |
|-----------|-----|
| `ticket_index_writer.py` | Already has dedupe gate. Source-agnostic by design. |
| `guru_*` files | Guru reads from ticket_index, not conversations. Unaffected. |
| `watchlist_engine.py` | Reads from source_events (cold tier). Unaffected. |
| `source_warehouse.py` | Cold tier storage. Separate from hot tier warehouse. |
| Data Warehouse UI page | Stage 3 concern. |
| Kodif source type | Stage 3 concern. Define Zendesk first. |
| Combined cross-source analytics | Stage 3 concern. Stage 2 is per-source only. |
