# Three-Pillar Build — Surgical Patch Guide (for work Claude on macOS)

**Audience:** the Claude instance patching the WORK copy of Alma Insights on
the Mac. You have a downloaded checkout of `enablement-content-tabs` (the
REFERENCE) and a separate TARGET codebase that must NOT be broadly
overwritten — every change lands as a targeted file copy or an
anchor-verified surgical edit.

**What this delivers:** the 2026-07-13/14 three-pillar build —
WS1 live two-way Asana calendar · WS2 self-building Drive knowledge base ·
WS3 content studio — PLUS the 2026-07-14 demo pass (`870fade`): style-guide
rendered preview + inline editor, the new Card/Article Template section, and
the macOS file/folder-picker fixes (Phase C2 below). Architecture
background: `docs/KB_ARCHITECTURE.md` (ships with this branch).

**Commit anchors in the reference checkout:**

| Ref | Meaning |
|---|---|
| `7adbe7b` | base (pre-build) |
| `1b5f951` | the three-pillar build (53 files) |
| `413e8fd` | tracked-test isolation fixes (2 files) |
| `870fade` | style-guide preview/editor + card/article template (7 files) |

The authoritative per-file patch is:

```bash
git -C <reference> diff 7adbe7b..413e8fd -- <path>    # Phases A-C (three-pillar)
git -C <reference> diff 413e8fd..870fade -- <path>    # Phase C2 (style guide/template)
```

Three files appear in BOTH ranges (`enablement_tools.py`, `page.py`,
`settings.py`) — apply the Phase C hunks first, then the Phase C2 hunks
anchor cleanly on the result. (Applying a shared file in one sitting with
`7adbe7b..870fade` is also fine; the split exists so each phase can be
test-gated on its own.)

Use those diffs as your source of truth for every modified file below. Apply
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
9. **Guide-document pattern (Phase C2 — style guide + card template):**
   both are tagged `enablement_documents` rows (`[STYLE-GUIDE]` /
   `[CARD-TEMPLATE]` name prefixes) with active pointers
   `enablement.style_guide_doc_id` / `enablement.card_template_doc_id`.
   Three sub-rules: (a) `_set_guide` strips `<span …>` markup — doc_reader
   preserves Word heading colors as spans, which are prompt noise and render
   literally in Qt's markdown preview; (b) inline-editor saves go through
   `update_style_guide_text` / `update_card_template_text`, which preserve
   the stored document's NAME — calling plain `set_*` there would rename an
   uploaded guide to the default; (c) the template block rides the existing
   `{style_guide}` prompt slot in `draft_card_from_document` — do NOT add a
   `{card_template}` placeholder to prompt template files (older files
   without it would KeyError at `.format()`).

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

## 3b. Phase C2 — style-guide preview/editor + card/article template (`870fade`)

Apply AFTER Phase C (three of these files carry Phase C hunks the C2 hunks
anchor on). Reference diff per file:
`git -C <reference> diff 413e8fd..870fade -- <path>`. Deeper feature
background (optional): `docs/MAC_STYLE_GUIDE_TEMPLATE_GUIDE.md`.

**New files — copy verbatim:**

```
assets/templates/support_center_article_template.md
tests/test_card_template.py
docs/MAC_STYLE_GUIDE_TEMPLATE_GUIDE.md
```

The template asset was generated FROM the owner's Unified Support
Center/Guru Article Template docx BY the project's own `doc_reader` (so it
is byte-equivalent to what an in-app upload of that docx produces), then
span-stripped. If the source docx ever changes, regenerate the same way:

```bash
python -c "from src.data.doc_reader import read_document; import re; \
md=re.sub(r'</?span[^>]*>','',read_document('PATH/TO/template.docx')); \
open('assets/templates/support_center_article_template.md','w',encoding='utf-8').write(md)"
```

### C2-1. `src/data/enablement_store.py` (4 hunk groups)
- New constants after `_STYLE_GUIDE_TAG`: `_CARD_TEMPLATE_KEY =
  "card_template_doc_id"`, `_CARD_TEMPLATE_TAG = "[CARD-TEMPLATE]"`.
- The six style-guide functions refactored onto parameterized private
  helpers (`_get_guide`/`_set_guide`/`_list_guides`/`_set_active_guide`/
  `_delete_guide`/`_clear_guide`); the public style-guide API delegates and
  is behaviorally IDENTICAL — `tests/test_style_guide.py` must pass
  unchanged after this file. `_set_guide` strips `<span>` markup
  (invariant 9a).
- New public card-template API mirroring the style guide
  (`get/set/list/set_active/delete/clear_card_template`,
  `card_template_block`) plus `update_style_guide_text` /
  `update_card_template_text` (in-place text rewrite, name preserved —
  invariant 9b).
- `draft_card_from_document`: the format arg becomes
  `style_guide=style_guide_block(conn) + card_template_block(conn)`
  (single slot — invariant 9c).

### C2-2. `src/data/chat_tools/enablement_tools.py` (1 hunk, on top of C9)
`_revise_draft_impl`: `style_block = store.style_guide_block(conn) +
store.card_template_block(conn)`. That is the entire C2 diff for this file.

### C2-3. `src/ui/pages/enablement/settings.py` (3 hunk groups, on top of C19)
- Imports gain `QPlainTextEdit`, `QTextBrowser`, `ALMA_BORDER_LIGHT`.
- New module-level `_GuideSection(QFrame)` ABOVE `SettingsPage` — one
  tagged-guide card: signals `action(str)` (verbs `paste | upload |
  upload_folder | drive | clear`), `activate(str)`, `delete_doc(str)`,
  `saved(str)`; status row + five action buttons; rendered `QTextBrowser`
  markdown preview that flips to a `QPlainTextEdit` raw editor
  (Edit → Save/Cancel); the stored-doc library rows (strips BOTH name
  tags). Edit state is an explicit `_editing` flag — `isVisible()` is
  unreliable in unshown widget trees (tests). Styles are
  objectName-scoped (`QFrame#GuideCard`, `QFrame#GuidePreview`) so child
  frames don't inherit the card chrome.
- `SettingsPage`: new signals `style_guide_saved`,
  `card_template_action/activate/delete/saved`; the Style Guide tab now
  builds TWO `_GuideSection`s (`_sg_section` "STYLE GUIDE", `_ct_section`
  "CARD / ARTICLE TEMPLATE"); the old `_style_guide()`/`_sg_row()`
  implementations are REMOVED; preserved setters `set_style_guides` /
  `set_style_guide_status` now delegate, and new setters
  `set_style_guide_content`, `set_card_templates`,
  `set_card_template_status`, `set_card_template_content` are added.

### C2-4. `src/ui/pages/enablement/page.py` (6 hunk groups, on top of C18)
- Connect block: five new signal connects (`style_guide_saved`,
  `card_template_action/activate/delete/saved`) and
  `_refresh_card_template_status()` next to the style-guide refresh at
  init.
- `_guide_action(...)`: single generalized handler for both sections;
  `_on_style_guide_action` / `_on_card_template_action` parameterize it
  (store fns, import kind, demo seed, refresh callback). The macOS picker
  fixes live here: `Upload…` = `getOpenFileNames` (multi-select) starting
  at `os.path.expanduser("~")` with filter `"Documents (…);;All files (*)"`;
  `upload_folder` = `getExistingDirectory` → `_guide_files_in_folder`
  (recursive, cap 50, skips hidden/`~$` lock files) → `_store_guide_files`
  (each stored; LAST becomes active; per-file read error surfaced only
  when nothing stored).
- New helpers: `_store_guide_files`, `_guide_files_in_folder`,
  `_bundled_card_template_text` (asset via
  `Path(__file__).resolve().parents[4] / "assets" / "templates" / …`, with
  a minimal inline-skeleton fallback), `_load_bundled_card_template`
  (demo-mode "From Drive" seed for the template section).
- `_refresh_style_guide_status` additionally pushes the active text into
  the preview (`set_style_guide_content`); new
  `_refresh_card_template_status` mirrors it.
- Editor-save handlers `_on_style_guide_saved` / `_on_card_template_saved`
  → the `update_*_text` store functions (invariant 9b); empty text clears.
- `_run_import` worker: `kind == "template"` branch →
  `store.set_card_template` (parallel to the `"style"` branch);
  `_on_import_finished` gains the matching `template` status branch.

**Phase C2 gate before proceeding:**

```bash
python -m pytest tests/test_card_template.py tests/test_style_guide.py tests/test_settings_subtabs.py -q   # 32
```

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
   Phase C2 group (also listed as its gate above):
   ```
   pytest tests/test_card_template.py tests/test_style_guide.py tests/test_settings_subtabs.py -q
   pytest tests/test_content_update_fanout.py tests/test_enablement_live_cutover.py -q
   ```
4. **Functional smoke (needs creds):** set `demo_mode: false`, restart →
   calendar reflects an Asana edit within ~1 min; open the task → extras
   sections; Settings → Sources → Knowledge base card shows the
   degraded-state list; with Google connected + KB enabled → Bootstrap →
   `EC/` appears in Drive; ask Renn to `index_drive_folder` a small folder
   → cards + `_index.md` in Drive; `generate_deck` → .pptx in
   `data/artifacts/<id>/`.
5. **Phase C2 functional smoke (works in demo mode — this is the
   demo-critical path):** Settings → Style Guide tab shows TWO sections
   (STYLE GUIDE + CARD / ARTICLE TEMPLATE).
   - **Upload… (the reported Mac bug):** the panel opens at the home
     directory; flip the NSOpenPanel filter popup to "All files" and
     confirm documents are selectable, not greyed out. If a file STILL
     isn't findable, it's a Drive/iCloud streaming placeholder — it must be
     downloaded locally ("Available offline") for `doc_reader` to read it;
     `.gdoc` stubs are never locally readable by design → use From Drive…
   - **Upload folder…:** pick a folder of `.md`/`.docx` → status like
     `3 documents uploaded — "x" is the active style guide`, all rows in
     the library.
   - **Template with exact headings:** upload the real template docx →
     preview renders Purpose / Article Title / Beginning of Article /
     Body / FAQs (SEO + AI Readiness) / Still Need Help? as headings, with
     NO literal `<span` text anywhere.
   - **Inline edit:** Edit shows markdown source; Save persists across an
     app relaunch and does NOT rename the stored document; Cancel discards.
   - **Demo seed:** demo mode ON → template section → From Drive… loads
     the full bundled template instantly (a short generic skeleton instead
     means the asset didn't travel).
   - **Generation follows the template** (needs an LLM): import any doc →
     generated draft carries the template's heading structure.

## 5. New settings keys (all default-safe; no settings file edits required)

```
enablement.asana.poll_interval_seconds   # default 60
enablement.asana.use_events              # default true (legacy fallback kept)
enablement.asana.extras_per_poll_cap     # default 10
enablement.asana.brief_per_poll_cap      # default 5
enablement.kb.enabled                    # default false (KB fully dormant)
enablement.kb.ec_folder_id               # written by bootstrap — never by hand
enablement.kb.ec_parent_id               # optional bootstrap parent
enablement.card_template_doc_id          # Phase C2 — written by the app on first
                                         # template upload/paste, never by hand
                                         # (peer of style_guide_doc_id)
```

The three-pillar phases ship dormant: with `demo_mode: true` (the default)
the target behaves identically to before the patch — verified by steps 1-3
rather than by visible UI change. **Phase C2 is the exception by design:**
the Style Guide tab visibly gains the template section and the rendered
preview/editor even in demo mode — that IS the demo surface (verify with
step 5).
