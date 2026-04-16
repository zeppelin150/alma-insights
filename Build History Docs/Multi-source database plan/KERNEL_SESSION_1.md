# Session 1 Kernel: Stop Destructive Imports

## What You Are

You are building Session 1 of a 5-session persistent data architecture overhaul for Alma Insights, a PySide6 desktop app with SQLite backend.

## First Steps

1. Read THIS file completely
2. Read `C:\alma-insights\Build History Docs\Multi-source database plan\SESSION_BUILD_LOG.md` — the living build log. You will APPEND a debrief to this file when you're done.
3. Read `C:\alma-insights\Build History Docs\Multi-source database plan\Stage 1 - Stop Destructive Imports.md` — your detailed implementation plan
4. Run `python -m pytest tests/ -x -q` to establish baseline test count
5. Then start building

## The Problem

Every CSV/Lightdash import runs `DELETE FROM tickets; DELETE FROM conversations; DELETE FROM comments` before inserting. This wipes all raw data, orphans 10,000+ derived rows, and forces $2-5 NLP re-scans. The database is currently at 0 tickets/conversations with 10,000+ orphaned downstream rows.

## What You're Building

Remove destructive DELETEs. Make imports additive. Dedupe by ticket_id — only new tickets get inserted.

### Files to Create (3 files, ~165 LOC)

| File | LOC | Purpose |
|------|-----|---------|
| `src/data/import_mode.py` | ~20 | `ImportMode` enum (INCREMENTAL, FULL_REFRESH) |
| `src/data/import_tracker.py` | ~120 | Import run logging, `get_existing_ticket_ids()`, `filter_new_tickets()` |
| `migrations/010_import_tracking.sql` | ~25 | `import_runs` table |

### Files to Modify (6 files, ~95 LOC delta)

| File | What to Change |
|------|---------------|
| `src/data/csv_ingestion.py` | Remove DELETEs at lines 260-262, add dedupe gate before write loop |
| `src/data/conversation_rebuild.py` | Remove DELETEs at lines 57-59, add dedupe gate after grouping |
| `src/data/lightdash_client.py` | Pass `mode` param through to `rebuild_conversations()` at line 535 |
| `src/services/clear_session.py` | Remove `tickets`, `conversations`, `comments` from EPHEMERAL_TABLES |
| `src/ui/main_window.py` | Update closeEvent dialog text, remove `_clear_all_data()` fallback at line 1383 |
| `src/ui/pages/settings_page.py` | Add "Full Database Reset" button in danger zone section |

### Tests to Write (~600 LOC across 8 test files)

See Stage 1 plan for full test matrix. Key tests:
- Dedupe gate: 100 incoming, 95 existing → 5 new
- Additive import: CSV A then CSV B → both present
- Clear & Close preserves tickets/conversations/comments
- Full Database Reset clears everything with confirmation
- Migration 010 creates import_runs table correctly

## Critical Rules

1. **Both DELETE blocks must be removed** — csv_ingestion.py:260-262 AND conversation_rebuild.py:57-59. Missing one leaves a hidden destructive path.
2. **EPHEMERAL_TABLES update is critical** — if `tickets`, `conversations`, `comments` stay in the list, Clear & Close will nuke the warehouse.
3. **Remove the `_clear_all_data()` fallback** at main_window.py:1383 — this is the nuclear fallback that deletes from ALL tables.
4. **Do NOT modify** db_manager.py upsert methods, scan_orchestrator.py, ticket_index_writer.py, or any analytics engines.
5. **Cyclomatic complexity**: Keep all new functions under CC 10. import_mode.py should be CC 2-3.

## UI Reference

HTML mockup file for visual reference: `C:\alma-insights\Build History Docs\Multi-source database plan\New_Persistent Database Design\Alma_Insights_UI_Mockup_Reference_Sheet (1).html`

Mockups 1.1 (Clear & Close dialog), 1.2 (Full Database Reset), 1.3 (Import status banner).

## When You're Done

**MANDATORY**: Append a full debrief to `SESSION_BUILD_LOG.md` covering:
- Every file created/modified with line counts
- Deviations from plan
- Regression results (test count before/after, broken tests, fixes)
- Organic adaptations (edge cases found, patterns established)
- Updated file tree
- State for Session 2 (what works, warnings, verification command)

## Verification Checklist

- [ ] Both DELETE blocks removed (grep for `DELETE FROM conversations` — should only appear in `_clear_all_data` and Settings reset)
- [ ] `python -m pytest tests/ -x -q` — all tests pass
- [ ] Import CSV twice → ticket count stable (no duplicates)
- [ ] Close app with "Clear & Close" → reopen → tickets still present
- [ ] Settings "Full Database Reset" → confirmation → all data gone
