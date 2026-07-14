# Three-Pillar Build — Surgical Patch Guide (for work Claude on macOS)

**Audience:** the Claude instance patching the WORK copy of Alma Insights on
the Mac. You have a downloaded checkout of `enablement-content-tabs` (the
REFERENCE) and a separate TARGET codebase that must NOT be broadly
overwritten — every change lands as a targeted file copy or an
anchor-verified surgical edit.

**What this delivers:** the 2026-07-13/14 three-pillar build —
WS1 live two-way Asana calendar · WS2 self-building Drive knowledge base ·
WS3 content studio. Architecture background: `docs/KB_ARCHITECTURE.md`
(ships with this branch).

**Commit anchors in the reference checkout:**

| Ref | Meaning |
|---|---|
| `7adbe7b` | base (pre-build) |
| `1b5f951` | the build (53 files) |
| `413e8fd` | tracked-test isolation fixes (2 files) |

The authoritative per-file patch is always:

```bash
git -C <reference> diff 7adbe7b..413e8fd -- <path>
```

Use that diff as your source of truth for every modified file below. Apply
hunks with anchor-verified edits (match on the surrounding code, never on
line numbers). **If a hunk's anchor does not exist in the target, STOP on
that file and reconcile intent using the "what/why" notes below — do not
force-apply.**

---

## 0. Hard invariants — read before touching anything

Violating any of these reintroduces a reviewed-and-fixed defect:

1. **DB discipline:** all new modules use plain `execute` + explicit
   `commit()/rollback()`, NEVER `connection_factory.atomic()` (it cannot
   nest, and several call sites run on raw sqlite3 connections in the MCP
   subprocess). Settings only via `settings_manager`.
2. **Qt threading:** widgets only from the main thread; workers are
   QTimer→daemon-thread with a single-flight latch and a FRESH connection
   per tick (copy the AsanaMonitor pattern, which this build extends).
3. **Asana `/events` returns HTTP 412 as its sync-token HANDSHAKE, not an
   error.** The client retry helper must never retry 412; `get_events`
   reads the fresh token out of the 412 body. On a full resync the token is
   minted BEFORE the baseline re-list (the reverse order permanently loses
   mid-list changes).
4. **`enablement_tasks.update_task` silently drops non-whitelisted
   fields.** Every new column written through it MUST be added to
   `_UPDATABLE` (`remote_modified_at`, `brief_json`, `brief_status`,
   `brief_source_modified_at`) or the write is a no-op and the entire
   check-and-set/brief tier dies silently.
5. **Gated-write ordering:** `execute_write` does `mark_resolved` FIRST
   (single-winner claim) for non-idempotent writes; the pickers keep their
   persist-first order. The new per-op `PRE_DISPATCH_CHECKS` hook runs
   BEFORE the claim (a blocked check keeps the Confirm card open).
6. **`INFORMATIONAL_ACTION_TYPES`** (`research_plan`, `artifact_preview`)
   are excluded from `has_pending_action` — nothing resolves those rows
   until Phase 1.5 M6b ships, and counting them bricks every picker for the
   session.
7. **KB writes only through the chokepoint** (`src/data/kb/drive_kb.py`,
   allowlist-enforced, raises `KBWriteDenied`); all Drive WRITES go through
   `GoogleDriveExporter`'s throttled surface; `google_oauth` refresh is
   lock-guarded. Tools NEVER touch Drive from the MCP subprocess — they
   enqueue `kb_queue` rows.
8. **Deferred by owner decision — do NOT add:** in-chat mermaid/quiz
   rendering or any React/`dist` change (D-MERMAID), diagram imagery in
   Guru cards (D-GURU; diagrams attach as fenced text), podcast/TTS beyond
   the `podcast` enum value.

---

## 1. Phase A — copy NEW files verbatim (no surgery needed)

These are net-new in `1b5f951`; copy each from the reference checkout to the
same path in the target. Copying new files is not a "broad overwrite."

```
migrations/043_asana_events.sql
migrations/044_asana_task_extras.sql
migrations/045_task_brief.sql
migrations/046_kb.sql
migrations/047_enablement_artifacts.sql
src/data/llm_gen.py
src/data/mermaid_lint.py
src/data/artifact_store.py
src/data/task_brief.py
src/data/asana_extras.py
src/data/pptx_reader.py
src/data/quiz_artifacts.py
src/data/kb/                       (whole package: __init__, drive_kb,
                                    card_format, store, sync, ingest,
                                    search, worker)
src/data/chat_tools/artifact_tools.py
src/data/chat_tools/kb_tools.py
config/prompts/enablement_task_brief.txt
config/prompts/enablement_mermaid.txt
config/prompts/enablement_quiz.txt
config/prompts/enablement_one_pager.txt
config/prompts/enablement_battle_card.txt
docs/KB_ARCHITECTURE.md
assets/templates/renn_deck.pptx    (BINARY — copy bytes, or regenerate:
                                    python -c "from pptx import Presentation;
                                    Presentation().save('assets/templates/renn_deck.pptx')")
```

**Migration-number check before copying migrations:** confirm the target's
`migrations/` has nothing ≥ 043. If the target somehow minted its own
043+, STOP — the numbering was pre-allocated (WS1=043-045, WS2=046,
WS3=047) and a collision needs human arbitration.

## 2. Phase B — dependency pins (3 files, one hunk each)

`pypdf==6.14.2` added directly after the `python-pptx==1.0.2` line, with
its comment block, in: `requirements.txt`, `requirements.lock`,
`installer/build_release.py` (the `PACKAGES` list). Then
`pip install pypdf==6.14.2` in the target venv. The hashed-lock regen (the
pip-compile-with-hashes step) runs on this Mac per the existing convention —
pypdf is a pure-Python wheel with no new transitives.

## 3. Phase C — surgical edits to EXISTING files

Apply in this order (respects import/runtime dependencies). For each file:
extract the reference diff, verify anchors, apply hunk-by-hunk.

### C1. `src/data/enablement_tasks.py` (1 hunk — do FIRST, invariant 4)
`_UPDATABLE` gains `remote_modified_at`, `brief_json`, `brief_status`,
`brief_source_modified_at`.

### C2. `src/data/asana_client.py` (4 hunks)
- Module constants: retry policy consts + extended `_TASK_FIELDS`
  (html_notes, start_on, num_subtasks, number/text/date custom subtypes).
- `_open_get_with_retry` helper; `_get`/`_get_raw` route through it.
  GET-only: 429 honors Retry-After (cap 30s, ≤2 retries), one 5xx retry.
  **412 passes through untouched** (invariant 3). `_send` (POST/PUT) never
  retries.
- New methods after `update_due_date`: `update_task(gid, **fields)`.
- New reads before `list_subtasks`: `get_events` (412→
  `{events:[], sync:<body token>, full_resync:True}`) and `list_stories`
  (comment_added only, paged, max 5 pages).

### C3. `src/data/google_oauth.py` (2 hunks)
`_ACTIVE_LOCK` (threading.Lock next to `_active`); `reconnect()` body
wrapped in `with _ACTIVE_LOCK:` (KBWorker/DriveMonitor/exporter threads now
share the module-global Credentials; unlocked refresh races into 401s).

### C4. `src/export/gdrive_export.py` (2 hunks)
- Module-level throttle chokepoint: `_throttled_execute(request)` — 0.5s
  min-interval under a lock + exponential backoff on
  (user)RateLimitExceeded/429/5xx, max 5 tries. `upload_report`'s
  `.execute()` routes through it.
- New write surface after `upload_report`: `create_folder`,
  `update_file` (returns `modifiedTime` — echo-suppression anchor),
  `upload_file(filename, bytes, mime, folder_id, app_properties)`,
  `get_file_meta` (requests `trashed` + `capabilities/canAddChildren` —
  DriveReader's get_file omits `trashed`, and a trashed folder still
  accepts writes), `find_child_by_app_property` (the `kb_card_id`
  idempotency probe).

### C5. `src/data/asana_monitor.py` (the largest rework — take the whole diff)
Poll core split: `poll_once(conn, client=None, results=None)` (results
out-param keeps the return contract), dispatcher `_poll_board` (events →
failure-count degradation → `_poll_board_legacy`), `_poll_board_events`
(token lifecycle per invariant 3; dict-shape validation so a mocked client
degrades instead of silently no-oping), `_run_baseline` (paged,
`modified_since=cursor`, truncation warning), shared `_process_task`,
`_dismiss_by_gid` (deleted→dismissed), `_handle_removed` (verify with
get_task; only dismiss on 404 — `removed` also fires on multi-homing),
`_on_stories_dirty` → `asana_extras.mark_stories_dirty`.
`_reconcile_existing_task` now returns the task_id (was bool) and stamps
`remote_modified_at`; reconcile SKIPS the status field while
`asana_writeback.is_status_inflight(tid)` (the optimistic-flip race
guard). `_create_task_from_asana` stamps `remote_modified_at`.
`_mark_board` gains `events_sync=`. After the board loop, a capped
`asana_extras.drain_stale(...)` runs (never before cursor advance). New
signal `tasks_updated(list)`; `_do_fetch` emits it from `results`.
Payload cache (`results["_payload_cache"]`) filled on all three paths.

### C6. `src/data/asana_writeback.py` (3 hunks)
In-flight status guard (`_INFLIGHT_STATUS` + lock + `is_status_inflight`);
`_cas_precheck` (NULL anchor → fetch-and-stamp-and-proceed; failed CAS read
→ proceed — protection, not a gate) and `_restamp` (PUT-response
modifiedTime → `remote_modified_at`, and → `brief_source_modified_at` only
when a brief already exists); `update_due_in_asana` reworked (CAS before
the local write; unlinked/unconfigured degrade preserved EXACTLY);
new `set_completed_in_asana` (CAS → optimistic flip under the guard → PUT →
restamp; API failure reverts only if the status still holds the optimistic
value).

### C7. `src/data/chat_action_requests.py` (3 hunks)
`INFORMATIONAL_ACTION_TYPES` frozenset; `has_pending_action` adds
`AND type NOT IN (...)` over it (docstring documents why); 
`_CONFIRM_WRITE_OPS` gains `asana_task_update` and
`upload_artifact_to_drive`.

### C8. `src/services/agent_chat.py` (5 hunks)
- Module-level gated-write registry replacing WriteWorker's if/elif:
  handler fns (`_write_guru_folder`, `_write_create_asana_task`,
  `_write_asana_task_update` — action-specific CAS: whole-task drift for
  complete/reopen, FIELD-level due_on comparison for set_due, none for
  comment/add_subtask, then anchor re-stamp so asana_writeback's internal
  CAS agrees — and `_write_upload_artifact` with an explicit
  extension→mime map, Windows/macOS mimetypes are non-deterministic),
  `_precheck_upload_artifact` (oauth_user + inactive →
  `needs_google_connect` WITHOUT burning the row, kicks off
  start_google_connect), `WRITE_HANDLERS`, `PRE_DISPATCH_CHECKS`.
- `WriteWorker.__init__` gains `ctx` (carries `db_path`); `_dispatch`
  delegates to the registry.
- `execute_write`: payload parsed BEFORE the claim; `PRE_DISPATCH_CHECKS`
  consulted (dict result → return `kept_open` WITHOUT mark_resolved); then
  claim; worker constructed with `ctx={"db_path": self._db_path()}` and
  stored in `self._write_workers` (a SET — the single-attribute version is
  the documented mid-run-GC crash).
- `_on_write_finished`/`_on_write_failed` call `_prune_write_workers()`
  (new helper) instead of `self._write_worker = None`.
- `__init__`: `self._write_worker = None` → `self._write_workers = set()`
  (comment updated).

### C9. `src/data/chat_tools/enablement_tools.py` (4 hunks)
- `RESOLVER_ACTION_TOOLS` gains `request_asana_task_update` and
  `request_upload_artifact_to_drive`.
- `_list_tasks_impl` gains detail mode (`task_id=` → one task enriched with
  extras + decoded brief; `brief_json` never reaches the model raw);
  `handle_list_tasks` passes `task_id`.
- New `_request_asana_task_update_impl` + handler after
  `handle_request_create_asana_task`: per-action value validation AT
  PROPOSE TIME (ISO-date regex for set_due, non-empty caps for
  comment/subtask, no-value for complete/reopen — steer strings, no row),
  captures `expected_modified_at` + `expected_due`.
- `_push_guru_draft_impl`: wrap the `store.publish_draft` result; on ok,
  fire-and-forget `kb.ingest.enqueue_distill(conn, did)` in try/except
  (the publish result stands regardless).

### C10. `src/data/chat_tools/registry.py` (2 hunks)
Import block: the three retired handlers
(`handle_create_asana_subtask/post_asana_comment/update_asana_due_date`)
replaced by `handle_request_asana_task_update`; their `_register` calls
replaced by one gated registration (comment explains the retirement).
New registrations: `list_artifacts`, `generate_diagram`, `generate_deck`,
`generate_quiz`, `generate_doc`, `attach_artifact_to_draft`,
`request_upload_artifact_to_drive` (from `artifact_tools`), and
`index_drive_folder`, `kb_search`, `kb_list_topics`, `kb_list_cards`,
`kb_get_card` (from `kb_tools`).

### C11. `src/mcp/chat_mcp_server.py` (3 hunks)
- TOOL_SCHEMAS: REMOVE the three retired write-tool schemas; ADD schemas
  for `request_asana_task_update`, `list_artifacts`, `generate_diagram`,
  `generate_deck`, `generate_quiz`, `generate_doc`,
  `attach_artifact_to_draft`, `request_upload_artifact_to_drive`,
  `kb_search`, `kb_list_topics`, `kb_list_cards`, `kb_get_card`,
  `index_drive_folder`; `list_tasks` schema gains `task_id`
  (all new schemas `additionalProperties: false` — the strict validator
  keys off it).
- `_RETIRED_TOOL_HINTS` dict after `_MCP_ALLOWED_TOOLS`.
- Dispatch: retired-name check BEFORE the unknown-tool error (steers a
  history-imitating model to the gated tool).

### C12. `src/llm/claude_tools.py` (3 hunks)
Same retirement mirrored: three schemas → one `request_asana_task_update`
schema; three wrappers → one `_request_asana_task_update` wrapper; three
`_DISPATCH` entries → one.

### C13. `src/data/drive_reader.py` (1 hunk)
`_extract_text`: a `"presentationml" in mt` branch routing to
`pptx_reader.pptx_to_markdown` BEFORE the word/officedocument branch
(.pptx previously fell into docx handling and silently returned '').

### C14. `src/data/pptx_store.py` (3 hunks)
`_DECK_TEMPLATE` const; `_normalize_outline` tolerates optional `notes`;
`export_pptx(..., template_path=None)` — template with graceful degrade,
defensive `_layout(i)` lookup, per-slide speaker notes, guarded
title/placeholder access.

### C15. `src/data/enablement_monitor.py` (3 hunks)
`start()` gains `asana_interval_seconds=`; `tasks_updated` folded into
`changed`; BriefWorker started with the Asana interval (inside the
asana-connected branch); KBWorker started when `kb_enabled()` (it checks
`google_oauth.is_active()` PER TICK — never gate the start on it,
invariant: OAuth is disable-on-launch); `stop()` iterates the two new
workers via `getattr`.

### C16. `src/ui/main_window.py` (1 hunk)
`_wire_enablement_monitor`: read `enablement.asana.poll_interval_seconds`
(default 60) and pass as `asana_interval_seconds`.

### C17. `src/ui/pages/enablement/task_detail.py` (3 hunks)
`completed_changed = Signal(bool)` + Mark complete/Reopen button in the
meta row; BRIEF section (renders only when `task["brief"]` present; LLM
output → `Qt.PlainText`); CUSTOM FIELDS / ATTACHMENTS / COMMENTS sections
from `task["extras"]` (all `Qt.PlainText` — Asana content is untrusted;
attachment links route through the existing `open_source` signal).

### C18. `src/ui/pages/enablement/page.py` (6 hunks)
RENN_SYSTEM_PROMPT: the "Two-way Asana" line now describes
`request_asana_task_update`; new KNOWLEDGE BASE and CONTENT STUDIO tool
paragraphs; gated-writes section lists the two new ops.
`_chat_context`: KB scope line (`cards_count` + topic count, try/except).
`_show_task_detail`: lazy extras+brief join for Asana tasks (fresh conn,
closed; enrichment-only try/except); `completed_changed` wired to
`_run_task_writeback("set_completed_in_asana", ...)`; `_open_task_id`
recorded. `_on_task_action_done`: `conflict` branch ("Task changed in
Asana — refreshed") + reopen-by-task_id with title fallback.
`set_monitor`: 500ms single-shot debounce timer replacing the direct
`_load_live` connect + `asana.tasks_updated → _on_asana_tasks_updated`
(new method: silently re-open the panel if the open task changed).
After `self.settings = SettingsPage()`: `self.settings.kb_conn_factory =
self._conn`.

### C19. `src/ui/pages/enablement/settings.py` (4 hunks)
`_kb_bootstrap_finished = Signal(str)` (worker→main hop; QTimer.singleShot
from a non-Qt thread is NOT safe); `_knowledge_base()` card added to the
Sources tab; `kb_conn_factory = None` + signal connect at the end of
`__init__`; `refresh_kb_status` (the single degraded-state surface:
demo_mode / no PAT / Google not connected / KB disabled / counts),
`_on_kb_toggle`, `_on_kb_bootstrap` (daemon thread → `ensure_ec_root` →
signal), `_kb_bootstrap_done`.

### C20. Tracked tests (contract + isolation updates)
- `tests/test_asana_readback.py`: two asserts —
  `_reconcile_existing_task` now returns the task_id / None (was
  True/False).
- `tests/test_asana_writeback.py`: `_poll_board` takes a results dict;
  `test_writeback_tools_registered` now asserts the three tools RETIRED
  from all surfaces + the gated replacement present + the MCP steering
  hints.
- `tests/test_enablement_live_cutover.py` / `test_enablement_ui_live.py`
  (from `413e8fd`): autouse fixtures pinning operator identity / credential
  reads empty — REQUIRED on any machine with real creds in the keyring.

---

## 4. Phase D — post-patch verification (macOS)

Zombie cleanup first: `pkill -f alma_mcp_server` (and between live scans
`pkill -f gemini; pkill -f node` per the existing convention).

1. **Import + registration smoke:**
   ```bash
   QT_QPA_PLATFORM=offscreen python -c "
   import src.services.agent_chat, src.data.kb.worker, src.data.kb.search
   import src.ui.pages.enablement.settings, src.ui.pages.enablement.task_detail
   from src.mcp.chat_mcp_server import TOOL_SCHEMAS, _RETIRED_TOOL_HINTS
   from src.data.chat_tools import registry; registry._ensure_registered()
   names = {t['name'] for t in TOOL_SCHEMAS}
   assert {'kb_search','generate_deck','request_asana_task_update',
           'index_drive_folder','attach_artifact_to_draft'} <= names
   assert not ({'create_asana_subtask','post_asana_comment',
                'update_asana_due_date'} & names)
   assert names <= set(registry._CHAT_TOOLS)
   import src.services.agent_chat as ac
   assert set(ac.WRITE_HANDLERS) == {'create_guru_folder','rename_guru_folder',
       'create_asana_task','asana_task_update','upload_artifact_to_drive'}
   print('registration OK —', len(TOOL_SCHEMAS), 'MCP tools')"
   ```
2. **Migrations:** boot once (or instantiate the test `empty_db` fixture)
   and confirm tables `asana_task_extras`, `kb_cards`, `kb_cards_fts`,
   `kb_queue`, `enablement_artifacts` exist and `enablement_tasks` has
   `remote_modified_at` + the three brief columns.
3. **Tracked test groups (3-4 files per invocation — never all of
   `tests/` at once):**
   ```
   pytest tests/test_asana_monitor.py tests/test_asana_writeback.py tests/test_asana_readback.py tests/test_asana_attachments.py -q
   pytest tests/test_enablement_monitor.py tests/test_enablement_tools.py tests/test_enablement_write_tools.py -q
   pytest tests/test_enablement_chat.py tests/test_enablement_new_tools.py tests/test_enablement_live_cutover.py -q
   pytest tests/test_enablement_ui_live.py tests/test_pptx_store.py tests/test_pptx_page.py -q
   pytest tests/test_action_channel_local.py -q   # only if the target carries local tests
   ```
   NOTE: the ~120 `tests/test_*_local.py` files from the build are
   gitignored and did NOT travel. The tracked groups above + the smoke in
   step 1 are the portable verification. Do not chase these pre-existing
   failures if the target shows them (all verified upstream of this build):
   `test_migration_idempotent` (chain breaks at mig 012),
   `test_feature_integration` (live-DB), `test_reporting_foundation::
   test_bridge_fallback`, bridge/embedding live tests,
   `test_chat_mcp_models::test_model` (mis-collected helper),
   `test_filter_engine` FTS-bracket asserts, `test_guru_pipeline::
   test_approve_and_push_rewrite` (markdown→HTML drift).
4. **Functional smoke (needs creds):** set `demo_mode: false`, restart →
   calendar reflects an Asana edit within ~1 min; open the task → extras
   sections; Settings → Sources → Knowledge base card shows the
   degraded-state list; with Google connected + KB enabled → Bootstrap →
   `EC/` appears in Drive; ask Renn to `index_drive_folder` a small folder
   → cards + `_index.md` in Drive; `generate_deck` → .pptx in
   `data/artifacts/<id>/`.

## 5. New settings keys (all default-safe; no settings file edits required)

```
enablement.asana.poll_interval_seconds   # default 60
enablement.asana.use_events              # default true (legacy fallback kept)
enablement.asana.extras_per_poll_cap     # default 10
enablement.asana.brief_per_poll_cap      # default 5
enablement.kb.enabled                    # default false (KB fully dormant)
enablement.kb.ec_folder_id               # written by bootstrap — never by hand
enablement.kb.ec_parent_id               # optional bootstrap parent
```

Everything ships dormant: with `demo_mode: true` (the default) the target
behaves identically to before the patch — that is the expected end state of
a correct patch, verified by step 1-3 rather than by visible UI change.
