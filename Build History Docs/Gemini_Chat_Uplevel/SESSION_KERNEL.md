# Gemini Chat Uplevel — Session Kernel

**Read this first. Then read PLAN.md in this directory. Then start building.**

---

## What We're Doing

Transforming Gemini Chats from a utility into the premier feature of Alma Insights. Claude Desktop-quality chat grounded in ticket data, with observability, project organization, and cross-app report browsing.

## Mockup Reference (this directory)

6 screenshots from Cambric design tool saved as PNGs. Review all of them:
- Main chat view: refined bubbles, RECENT chips header, model selector, Explore button
- Explore dropdown: All Chats (888), Projects (12), Reports (24), Incidents (3) — each opens drilldown
- Gemini response rendering: finding sections with green left-border cards, ticket ID chips (#10246 style), clean prose

**Not yet mocked up**: Monitor panel (observability mode) — see PLAN.md for spec.

## Build Order (4 Sessions)

### Session 1: Data Layer Foundation (START HERE)
**Goal**: New tables, migration, CRUD rewrite. Everything downstream depends on this.

1. Write `migrations/009_chat_data_layer.sql`:
   - `chat_projects` table
   - `chat_messages` table (replaces JSON blob in chat_sessions)
   - `chat_tool_executions` table (powers Monitor panel)
   - `chat_messages_fts` (FTS5 virtual table for chat search)
   - `ALTER chat_sessions ADD COLUMN project_id`
   - 3 virtual views: `v_session_summary`, `v_session_tools`, `v_chat_cost_daily`
   - Data migration: extract existing JSON messages → `chat_messages` rows

2. Update `src/services/chat_session.py`:
   - `append_message()` → simple INSERT into `chat_messages` (not read-modify-write JSON)
   - `load_session()` → SELECT from `chat_messages WHERE session_id=? ORDER BY ordinal`
   - `list_sessions()` → query `v_session_summary` instead of raw `chat_sessions`
   - New: `create_project()`, `assign_session_to_project()`, `list_projects()`

3. Update `src/services/chat_engine.py`:
   - Capture `tokens_in`, `tokens_out`, `cost_usd`, `latency_ms`, `model_used` per response
   - Pass these to `append_message()` for storage in `chat_messages` columns

4. Update `src/data/chat_tools/registry.py`:
   - After each `dispatch_tool()`, INSERT into `chat_tool_executions`
   - Capture: tool_name, args_json, result_rows, tables_touched, elapsed_ms

5. Tests: CRUD for new tables, migration round-trip, virtual view queries

### Session 2: UI Redesign
**Goal**: Header bar, bubbles, Explore dropdown.

### Session 3: Drilldown Panels
**Goal**: All Chats, Projects, Reports, Incidents, Monitor modes.

### Session 4: Performance + Polish
**Goal**: Warm bridge, priority rate governor, FTS5 search, follow-up chat unification.

## Key Files to Read Before Coding

| File | Why |
|------|-----|
| `src/services/chat_session.py` | Current CRUD — you're rewriting this |
| `src/services/chat_engine.py` | Message flow, tool loop — you're adding telemetry |
| `src/data/chat_tools/registry.py` | Tool dispatch — you're adding execution logging |
| `src/ui/pages/gemini_chats_page.py` | Current chat UI — Session 2 rewrites the header + bubbles |
| `src/ui/widgets/drilldown_panel.py` | Existing drilldown — Session 3 adds multi-mode support |
| `migrations/005_persistence_layer.sql` | Current chat_sessions schema |
| `migrations/006_hybrid_chat.sql` | chat_sessions expansion (filter_json, ticket_count, active_report_ids) |
| `src/ui/theme.py` | All ALMA_* color tokens (lines 21-48) |

## Critical Constraints

- **Run `python -m pytest tests/ -x -q` before touching anything** — 631+ passing tests, 1 known failure in test_reporting_foundation.py
- **PRAGMA foreign_keys = ON** — add to db_manager connection factory (Issue #2 from data review)
- **PHI boundary**: chat_tool_executions must NEVER store raw ticket text in result_json — truncate/sanitize
- **Migration must be backward-compatible**: old sessions with JSON blobs must be extractable to new rows
- **SQLite WAL mode**: already enabled — concurrent reads during writes are safe
- **Don't break Follow-Up Chat (ReportChatWidget)**: it's ephemeral today, will be unified in Session 4

## Schema Quick Reference

```sql
chat_messages (message_id PK, session_id FK, ordinal, role, content,
  created_at, model_used, tokens_in, tokens_out, cost_usd, latency_ms,
  tool_calls JSON, tool_round, error_code, error_message, metadata JSON)

chat_tool_executions (execution_id PK, message_id FK, session_id,
  tool_name, args_json, result_json, result_rows, tables_touched JSON,
  elapsed_ms, error, created_at)

chat_projects (project_id PK, name, description, sort_order,
  created_at, updated_at)

chat_sessions + ADD COLUMN project_id FK → chat_projects
```

## Color Tokens for UI Sessions

| Element | Token | Hex |
|---------|-------|-----|
| User bubble bg | ALMA_GREEN_DARK | #03281B |
| User text | ALMA_CREAM | #F3F1EC |
| Gemini bubble bg | ALMA_BG_ELEVATED | #FFFFFF |
| Gemini bubble border | ALMA_BORDER_LIGHT | #E8E5DE |
| "Gemini" label | ALMA_GREEN_LIGHT | #14573F |
| Active recent chip | ALMA_GREEN_DARK bg | #03281B |
| Finding card left border | ALMA_GREEN_LIGHT | #14573F |
| Ticket ID chips | monospace, #F0EDE8 bg | light warm gray |
| Explore button | ALMA_GREEN_DARK bg | #03281B |
| Monitor: TOOL event | ALMA_WARNING border | #B45309 |
| Monitor: CONTEXT event | ALMA_INFO border | #1D6FA5 |
