# src/services/

> Service layer connecting UI to data: chat orchestration, session management, and post-scan/post-report persistence hooks. Pure logic — no PySide6 dependency except QObject/Signal in ChatEngine.

## Module Index

### chat_engine.py
> Pure-logic QObject powering all Gemini/Claude chat interactions. NOT a widget — consumers own their UI and configure the engine via callbacks and signals.

**Public API:**
- `ChatEngine(db_path, parent=None)` — constructor
  - `response_ready` — Signal(str, dict) emitting (response_text, telemetry)
  - `error_occurred` — Signal(str) emitting error message
  - `send(message, system_prompt, history, context_provider, history_packer, response_handler)` — send message to LLM
  - `set_client(client)` — inject pre-built LLM client (warm client mode)
  - `set_tools_enabled(enabled)` — toggle tool execution (max 3 round-trips)
  - `is_busy() -> bool` — check if a request is in flight

**Depends on:** `src.data.connection_factory`, `src.data.chat_tools.tool_prompts`, `src.gemini.client_factory`
**Depended by:** `src.ui.widgets.chat_widget`, `src.ui.pages.gemini_chats_page`, `tests.test_chat_engine`

---

### chat_session.py
> CRUD operations for `chat_sessions` + `chat_messages` tables. Messages stored as individual rows in `chat_messages` (migration 009).

**Public API:**
- `create_session(conn, source_page, trc_filter, date_start, date_end, title) -> str` — create new session, return session_id
- `append_message(conn, session_id, role, content, model_used, tokens_in, tokens_out, cost_usd, latency_ms, tool_calls, error_code) -> str` — append message row
- `list_sessions(conn, limit, project_id) -> list[dict]` — list sessions for sidebar
- `load_session(conn, session_id) -> dict` — load full session with messages
- `get_session_filters(conn, session_id) -> dict` — get filter state
- `set_session_filters(conn, session_id, filter_json)` — update filter state
- `set_session_ticket_count(conn, session_id, count)` — update ticket count
- `get_active_report_ids(conn, session_id) -> list` — get linked report IDs
- `add_active_report_id(conn, session_id, report_id)` — link a report
- `create_project(conn, name, description) -> str` — create project folder
- `assign_session_to_project(conn, session_id, project_id)` — organize session
- `list_projects(conn) -> list[dict]` — list project folders
- `get_message(conn, message_id) -> dict` — get single message
- `search_messages(conn, query, session_id) -> list[dict]` — FTS5 search

**Depends on:** `src.data.connection_factory`
**Depended by:** `src.ui.pages.gemini_chats_page`, `src.ui.widgets.chat_widget`, `tests.test_chat_data_layer`, `tests.unit.test_chat_session`

---

### clear_session.py
> Wipes ephemeral tables (raw ticket text, customer data) while preserving persistent tables (ticket_index, analysis_runs, insight_ledger). Replaces the old "delete DB file" approach.

**Public API:**
- `clear_session_data(db_path: str) -> dict` — clear ephemeral data, return stats (tables_cleared, rows_deleted)

**Depends on:** `src.data.connection_factory`
**Depended by:** `src.ui.main_window`, `tests.test_bug_clear_close`, `tests.unit.test_clear_session`

---

### context_injector.py
> Builds the `[SYSTEM CONTEXT]` block prepended to every chat message sent to the LLM. Provides data scope, filter state, and available query tools.

**Public API:**
- `build_context(conn, trc_filter, date_start, date_end, source_id) -> str` — build context string with ticket counts, TRC list, date range, scan status

**Depends on:** `src.data.connection_factory`
**Depended by:** `src.ui.widgets.chat_widget`, `tests.unit.test_context_injector`

---

### post_report_persist.py
> Persists every AI report generation to the `analysis_runs` table. Called after report output renders in the UI.

**Public API:**
- `persist_report_run(conn, run_date, prompt_template, trc_filter, ticket_count, model_used, output_text, output_structured, token_count, cost_usd, duration_sec)` — insert analysis_runs row

**Depends on:** (none from src/)
**Depended by:** `src.ui.pages.ai_reports`, `tests.unit.test_post_report_persist`

---

### post_scan_persist.py
> Runs after the NLP meta-analyzer completes. Reads from `ticket_index` and writes scan_category_snapshots, trend_snapshots, and insight_ledger entries.

**Public API:**
- `write_scan_category_snapshot(scan_id, scan_date, conn) -> int` — aggregate ticket_index by TRC/friction/sub_pattern, write to scan_category_snapshots
- `write_trend_deltas(scan_id, conn) -> int` — compare current snapshot vs prior scan, write >20% changes to trend_snapshots
- `write_scan_insights(scan_id, conn) -> int` — detect significant findings, write to insight_ledger
- `run_post_scan_persistence(scan_id, conn)` — convenience wrapper running all three

**Depends on:** (none from src/)
**Depended by:** `src.agents.scan_orchestrator`, `tests.unit.test_post_scan_persist`

---

### ticket_index_writer.py
> Dedup gate + upsert logic for the persistent `ticket_index` table. Called from the NLP scan pipeline to maintain a PHI-free, deduplicated ticket-level record.

**Public API:**
- `should_classify_ticket(ticket_id, scan_id, conn) -> str` — returns 'classify' (new), 'skip' (already in this scan), or 'update_scan_id' (good existing, confidence >= 0.7)
- `update_scan_reference(ticket_id, scan_id, conn)` — mark existing ticket as seen in current scan
- `upsert_ticket_index(conn, ticket_id, scan_id, trc_code, classification_data)` — insert or update ticket_index row with PHI-free data

**Depends on:** (none from src/)
**Depended by:** `src.agents.worker_agent`, `src.agents.tool_registry`, `tests.unit.test_ticket_index_writer`

---

### zendesk_web.py
> `ZendeskWebController` — the Python authority behind the web Zendesk workspace (QtWebEngine tab on `#/zendesk`). Owns all SQL reads, viewmodel shaping, sanitize-every-srcdoc, and the gated actions: read-only pull, file/folder import via native pickers, copy-exact via Python-side clipboard reading exact DB bytes, revision transitions, and native-confirmed destructive purge/delete. Mirrors the native `ZendeskPage` signal surface so `page.py` feeds either implementation unchanged; never emits push/sync signals and never references a `ZendeskClient` write method (structurally tested).

**Public API:**
- `ZendeskWebController(conn_fn=None, confirm_fn=None, clipboard_fn=None, file_pick_fn=None, folder_pick_fn=None, pull_runner=None, import_runner=None, now_fn=None, demo=False, parent=None)`
- Web signals (JSON str): `zendesk_data`, `article_detail`, `macro_detail`, `revisions_data`, `diff_ready`, `import_resolved`, `pull_resolved`, `copy_resolved`, `action_resolved`; `status_text` (plain)
- `js_*` entry points relayed by `src.ui.web.zendesk_bridge.ZendeskBridge` (silent no-op on forged/stale ids; single-winner inflight claims)

**Depends on:** `src.data.zendesk_store`, `src.data.zendesk_import`, `src.data.html_sanitize`, `src.data.text_diff`
**Depended by:** `src.ui.pages.enablement.page` (`_make_zendesk`), `src.ui.web.zendesk_bridge`, `tests.test_zendesk_bridge`, `tests.test_zendesk_web_tab`
