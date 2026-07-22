# Mac Surgical Update — Per-File Reference Dossier (`7adbe7b` → `dbb5c8f`)

**Companion to [`MAC_SURGICAL_UPDATE_GUIDE.md`](MAC_SURGICAL_UPDATE_GUIDE.md). Read that first; execute from that.**

This dossier is a LOOKUP TABLE, not a to-do list: every changed file with what changed, exact
names/keys/signatures, what it depends on, macOS risks, and the command that proves it works.
Consult it when a gate or test in the guide fails and you need to diagnose without re-reading diffs.
It was generated on the Windows dev box from the actual `git diff 7adbe7b..dbb5c8f` content.
The guide's rules override anything here that reads like an instruction to act.

---

## 1. Data layer — migrations 043–050, KB, Help Center, artifacts, doc search

### `migrations/043_asana_events.sql`  (A)

Two idempotent ALTERs: monitor_sources.events_sync (Asana /events sync token) and enablement_tasks.remote_modified_at (Asana-side modified_at snapshot, check-and-set anchor for write-back conflict safety).

**Key details:** ALTER TABLE monitor_sources ADD COLUMN events_sync TEXT; ALTER TABLE enablement_tasks ADD COLUMN remote_modified_at TEXT. Idempotence relies on schema_migrator's PRAGMA table_info guard for ALTER ADD COLUMN. Consumers: asana monitor (other slice), enablement_tasks._UPDATABLE, task_brief.dirty_brief_task_ids (queries remote_modified_at).

**Depends on:**
- src/updater/schema_migrator.py PRAGMA-guarded ALTER support (pre-existing)
- monitor_sources + enablement_tasks tables (pre-existing)

**Verify:** `sqlite3 data/local_warehouse.db "PRAGMA table_info(enablement_tasks)" | grep remote_modified_at (after app launch applies migrations)`

### `migrations/044_asana_task_extras.sql`  (A)

Creates asana_task_extras side table (html_notes, custom_fields_json, attachments_json, stories_json, for_modified_at, fetched_at) keyed to enablement_tasks with ON DELETE CASCADE.

**Key details:** CREATE TABLE IF NOT EXISTS asana_task_extras (task_id TEXT PRIMARY KEY REFERENCES enablement_tasks(task_id) ON DELETE CASCADE, ...). Consumers: src/data/asana_extras.py (A, other slice) and task_brief._brief_source_text (reads html_notes/custom_fields/stories for the brief prompt).

**Depends on:**
- enablement_tasks table
- migration 043 conceptually (staleness compares for_modified_at vs remote_modified_at)

**Verify:** `sqlite3 data/local_warehouse.db ".schema asana_task_extras"`

### `migrations/045_task_brief.sql`  (A)

Three idempotent ALTERs on enablement_tasks for Haiku briefs: brief_json, brief_status (DEFAULT 'pending'), brief_source_modified_at.

**Key details:** brief_status values pending|ok|failed; 'failed' still stamps brief_source_modified_at (no retry storm). Consumers: src/data/task_brief.py, enablement_tasks._UPDATABLE.

**Depends on:**
- migration 043 (remote_modified_at column — brief staleness compares against it)
- schema_migrator PRAGMA guard

**Verify:** `python -m pytest tests/test_help_corpus_sync.py -x -q (smoke) + PRAGMA table_info(enablement_tasks) shows brief_json/brief_status/brief_source_modified_at`

### `migrations/046_kb.sql`  (A)

The whole KB mirror schema: kb_folders (Drive write allowlist), kb_cards (+2 indexes), kb_cards_fts (contentless FTS5) + 3 triggers, kb_sync_state, kb_queue (+ unique partial index for pending-coalescing), kb_sync_log.

**Key details:** Tables: kb_folders(folder_id PK, topic, parent_id, role ec_root|topic, status ok|quarantined, created_at); kb_cards(card_id PK, drive_file_id UNIQUE, topic_folder_id, title, type, topics_json, source_id, source_url, source_mime, source_modified, summary, key_facts_json, body_md, content_hash, drive_modified, synced_at, status); indexes idx_kb_cards_topic(topic_folder_id,status), idx_kb_cards_source(source_id); kb_cards_fts fts5(title,summary,key_facts,body,topics, content='', tokenize='unicode61 remove_diacritics 2') with triggers kb_cards_ai/ad/au; kb_sync_state(folder_id PK, cursor, last_run_at, last_status, last_error); kb_queue(queue_id AUTOINCREMENT, kind index_folder|write_card|distill|refresh, target, payload_json, status pending|claimed|done|error|dead, attempts, job_id, created_at, claimed_at) + UNIQUE partial index idx_kb_queue_pending ON (kind,target) WHERE status='pending'; kb_sync_log(log_id, action, card_id, drive_file_id, detail, created_at). Consumers: everything under src/data/kb/.

**Depends on:**
- SQLite FTS5 available in the Mac Python's sqlite3 (system Python/pyenv builds sometimes lack it)

**macOS risks:**
- FTS5 must be compiled into the arm64 Python's sqlite3; verify with sqlite3 'CREATE VIRTUAL TABLE t USING fts5(x)' in a scratch DB

**Verify:** `sqlite3 data/local_warehouse.db ".schema kb_cards" and ".schema kb_queue" after launch; python -m pytest tests/test_kb_extra_and_readopt.py -x -q`

### `migrations/047_enablement_artifacts.sql`  (A)

Creates enablement_artifacts registry (one row per studio artifact: diagram/quiz/deck/one_pager/battle_card/podcast-reserved) with soft refs, spec_json, file_path, provenance_json + 3 indexes.

**Key details:** No CHECK constraints on kind/status by design (whitelists live in artifact_store.py). Indexes: idx_artifacts_kind_status(kind,status), idx_artifacts_task(task_id,updated_at), idx_artifacts_session(session_id,updated_at). Consumer: src/data/artifact_store.py (+ studio generator tools in another slice).

**Verify:** `sqlite3 data/local_warehouse.db ".schema enablement_artifacts"`

### `migrations/048_help_center.sql`  (A)

Creates help_articles (+2 indexes) and contentless help_articles_fts with DROP-and-recreate triggers; the delete trigger passes ORIGINAL column values (required on contentless FTS5 or old text stays searchable).

**Key details:** help_articles(article_id PK, title, section, section_title, section_order, article_order, status available|partial|flag-gated|not-available, applies_to enablement|product|both, summary, body, features_json, last_verified, content_hash, loaded_at). fts5(title,summary,body,features, content='', tokenize='unicode61 remove_diacritics 2'). Triggers help_articles_ai/ad/au are DROP TRIGGER IF EXISTS then CREATE (re-run repairs in place). Consumers: src/data/help/* and help_tab.py / chat help_tools.py (other slice).

**Depends on:**
- FTS5

**Verify:** `python -m pytest tests/test_help_store.py tests/test_help_search.py tests/test_help_corpus_sync.py -x -q`

### `migrations/049_kb_extra_fields.sql`  (A)

ALTER TABLE kb_cards ADD COLUMN extra_json — stores a human's unrecognized card-frontmatter keys so they survive the DB round-trip and are merged back on the next Drive push (finding 17).

**Key details:** Nullable, no default: upsert_card passes NULL to mean 'preserve stored value'. Consumers: kb/store.py (upsert_card 18-column INSERT includes extra_json; _row_to_dict parses it), kb/sync.py pull/push paths.

**Depends on:**
- migration 046 (kb_cards must exist first)

**Verify:** `python -m pytest tests/test_kb_extra_and_readopt.py -x -q`

### `migrations/050_enablement_documents_fts.sql`  (A)

Creates contentless enablement_documents_fts (name, full_text) with full-value-delete triggers plus a delete-all + back-fill so pre-existing documents become searchable immediately; the retrieval layer for the 0%→93% Drive-search fix.

**Key details:** fts5(name, full_text, content='', tokenize='unicode61 remove_diacritics 2'); triggers enablement_documents_ai/ad/au (DROP then CREATE); back-fill: INSERT ... VALUES('delete-all') then SELECT rowid,name,full_text FROM enablement_documents. Consumers: enablement_doc_search.py, enablement_store.search_documents (rewritten to use it), kb/search.py full-text floor.

**Depends on:**
- enablement_documents table (migration 027, pre-existing on the Mac base)
- FTS5

**Verify:** `python -m pytest tests/test_enablement_doc_search.py -x -q`

### `src/data/kb/__init__.py`  (A)

9-line package docstring module naming the five KB submodules (drive_kb, card_format, store, sync, worker). No imports, no code.

**Key details:** Pure docstring; safe to copy verbatim.

**Verify:** `python -c "import src.data.kb"`

### `src/data/kb/card_format.py`  (A)

KB index-card format v1: YAML-frontmatter Markdown serializer + tolerant parser (never raises; degraded parse yields issues like no_frontmatter/broken_frontmatter), filename convention slug--kb-<uuid8>.md, extract_extra() for non-schema keys.

**Key details:** Public: SCHEMA_VERSION=1, CARD_TYPES frozenset, new_card_id() -> 'kb-<uuid8>', card_filename(title,card_id), card_id_from_filename(name), extract_extra(meta), serialize_card(meta,body), parse_card(text)->(meta,body,issues). 1MB size cap (_MAX_CARD_CHARS). Imports slugify from src.data.artifact_store; yaml imported lazily inside functions.

**Depends on:**
- src/data/artifact_store.py (slugify)
- PyYAML (pre-existing)

**Verify:** `python -c "from src.data.kb.card_format import parse_card; print(parse_card('---\nx: 1\n---\nbody'))" (gitignored test_kb_*_local.py suites do NOT ship — no committed unit tests for this module)`

### `src/data/kb/store.py`  (A)

SQLite mirror API for kb_cards: content-hash-skipping upsert_card (preserves extra_json when extra=None), get/list/count, weighted-bm25 fts_search, mark_status, mirror-only delete_card.

**Key details:** upsert_card(conn, meta, body, *, drive_file_id, topic_folder_id, drive_modified, status, extra) — 18-column INSERT ON CONFLICT(card_id) DO UPDATE; requires extra_json column (mig 049). fts_search uses bm25(kb_cards_fts, 3.0,1.0,2.0,1.0,2.0) and excludes status='source_missing'. Plain execute+commit, never atomic() (worker threads + MCP subprocess).

**Depends on:**
- migrations 046 + 049 applied

**Verify:** `python -m pytest tests/test_kb_extra_and_readopt.py -x -q`

### `src/data/kb/drive_kb.py`  (A)

The single Drive write chokepoint: allowlist-enforced write_card_file (raises KBWriteDenied outside kb_folders), EC-root bootstrap with writability probe + quarantine + re-adoption of restored roots, code-gated topic-folder minting (seed list from config/entities/product_areas.json, fuzzy match >=0.8, MAX_TOPIC_FOLDERS=40, overflow to 'misc').

**Key details:** Public: KBWriteDenied, allowed_folder, ec_root_id, topic_folders, ensure_ec_root (writes enablement.kb.ec_folder_id via set_section), resolve_topic_folder(conn, topic, new_folder_budget=3), write_card_file(conn, parent_folder_id, filename, content, card_id=...). Constants EC_ROOT_NAME='EC', MISC_TOPIC='misc', MARKER_NAME='.kb_marker'. All Drive I/O via GoogleDriveExporter.from_settings() — needs its NEW methods create_folder(app_properties=), upload_file(app_properties=), update_file, get_file_meta(fields=), find_child_by_app_property (M src/export/gdrive_export.py, another slice). _ENTITIES_DIR = Path(__file__)... /config/entities.

**Depends on:**
- src/export/gdrive_export.py delta (new exporter methods)
- migration 046
- src/data/artifact_store.py (slugify)
- config/entities/product_areas.json (pre-existing)
- src/data/settings_manager

**Settings keys:**
- enablement.kb.ec_parent_id (read, optional)
- enablement.kb.ec_folder_id (written on bootstrap)

**macOS risks:**
- Live behavior needs Google OAuth/service-account creds on the Mac; drive.file grants are per-OAuth-client-ID (an EC tree bootstrapped by another build is readable but not writable — probe surfaces re-bootstrap)

**Verify:** `import-only on the Mac (python -c "from src.data.kb import drive_kb"); live checks are owner-gated (no committed tests; test_kb_drive_local.py is gitignored)`

### `src/data/kb/ingest.py`  (A)

index_folder job engine: bounded folder enumeration (depth 6, 500 docs/job), text extraction via DriveReader.export_text, Haiku summarize-to-card via llm_gen.generate_validated with deterministic 240-char-excerpt fallback, full text persisted to enablement_documents as the recall floor, cards queued to kb_queue ('write_card'); distill_draft archives published Guru cards as 'published_card' KB cards; enqueue helpers coalesce via the unique pending index.

**Key details:** Public: MAX_DOCS_PER_JOB=500, MAX_DEPTH=6, summarize_doc(name,text,llm_client=), enumerate_folder(reader,folder_id), index_folder(conn,folder_id,reader=,exporter=,llm_client=,topic_hint=,job_id=), enqueue_distill(conn,draft_id), distill_draft(conn,draft_id,exporter=), _enqueue_write. Runs under llm_gen.background_gate. Uses agent_jobs.is_cancelled, enablement_store.save_document(source='drive', source_ref=fileId,...), enablement_store.get_draft.

**Depends on:**
- src/data/kb/{card_format,drive_kb,store}
- src/data/llm_gen.py
- src/data/drive_reader.py delta (export_text handles pptx/pdf)
- src/data/agent_jobs.py (pre-existing)
- src/data/enablement_store.py
- migrations 046+049+050

**macOS risks:**
- LLM path resolves through build_client_for_task('enablement_card_gen') → local 'claude' CLI; if CLI missing/not logged in the module degrades to deterministic excerpt cards (no crash)

**Verify:** `python -c "from src.data.kb import ingest"; live indexing requires Google + claude CLI (gitignored local tests only)`

### `src/data/kb/search.py`  (A)

Deterministic hybrid KB search (no embeddings, owner-locked): sanitized FTS5 MATCH (quoted OR tokens, cap 24) + blend 0.55·bm25 + 0.25·coverage + 0.10·title + 0.10·recency; falls through to the enablement_documents full-text floor via enablement_doc_search when card hits < limit.

**Key details:** Public: expand_query(query) (tokens + singulars + payer/product-area alias expansion from config/entities/payers.json + product_areas.json), kb_search(conn, query, topics=, type=, limit=8). Module-level _alias_map cache. Floor path imports search_documents_ranked + windowed_snippet from src.data.enablement_doc_search.

**Depends on:**
- src/data/kb/store.py
- src/data/enablement_doc_search.py
- migration 050 (floor)
- config/entities/*.json (pre-existing)

**Verify:** `python -c "from src.data.kb.search import expand_query; print(expand_query('claim denials'))"`

### `src/data/kb/sync.py`  (A)

Two-way sync engine, pull-wins: pull_folder/pull_all (modifiedTime cursor in kb_sync_state, echo suppression via kb_cards.drive_modified), push_pending (claim→write→done queue drain, check-and-set discards conflicting pushes), drain_index_jobs/drain_distill_jobs, scan_stale_sources (source-staleness re-summarize + '## Changelog' append + operator-stamped attention task), expire_stale_pending (7 days→dead), reset_stale_claims (15-min lease, 3 attempts), full_reconcile (mirror-drop vanished files + deterministic _index.md regen).

**Key details:** Constants: INDEX_FILENAME='_index.md', _LEASE_MINUTES=15, _MAX_ATTEMPTS=3. scan_stale_sources imports text_diff.diff_rows, enablement_tasks.create_task/update_task (source='kb', kind='card_review'), enablement_identity.operator_asana_gid/operator_name. Qt-free; plain execute+commit; fresh connection per tick supplied by caller.

**Depends on:**
- src/data/kb/{card_format,drive_kb,store,ingest}
- src/data/text_diff.py (pre-existing)
- src/data/enablement_tasks.py (this delta: _UPDATABLE additions)
- src/data/enablement_identity.py (pre-existing)
- src/data/agent_jobs.py
- migrations 046+049

**Verify:** `python -c "from src.data.kb import sync" (test_kb_sync_local.py is gitignored, not on the Mac)`

### `src/data/kb/worker.py`  (A)

KBWorker: QTimer-driven tick (default 300s, floor 60s) that spawns a daemon threading.Thread per tick (single-flight latch), opens a FRESH get_connection per run, and runs tick_once: reset leases → expire pending → drain index jobs → push → pull all → drain distill; every 5th tick adds full_reconcile + scan_stale_sources. Gates PER TICK on google_access.google_access_ready() (start-time gates can never be true — OAuth is disable-on-launch).

**Key details:** Public: kb_enabled() (False when enablement.demo_mode true or enablement.kb.enabled false), tick_once(conn, reader=, exporter=, do_reconcile=), class KBWorker(db_manager) with start(interval_seconds=300)/stop()/scan_now(). Not a QObject; no signals. _RECONCILE_EVERY=5.

**Depends on:**
- src/data/google_access.py (A, other slice)
- src/data/kb/sync.py + drive_kb.py
- src/data/drive_reader.py delta
- src/data/connection_factory
- PySide6 QTimer (constructed on Qt main thread via EnablementMonitor.start)

**Settings keys:**
- enablement.demo_mode (default True → KB off)
- enablement.kb.enabled (default False)

**macOS risks:**
- Threads are plain daemon threading.Thread — cross-platform fine; no subprocess flags. Only indirect subprocess is the claude CLI via llm_gen during index jobs (PATH caveat for GUI-launched apps on macOS)

**Verify:** `python -c "from src.data.kb.worker import kb_enabled; print(kb_enabled())" → False on a demo-mode Mac (flag off = worker never starts; safe default)`

### `src/data/help/__init__.py`  (A)

Package facade re-exporting STATUSES, get_article, list_articles, list_sections, upsert_article (store), load_bundled_help (loader), search_help (search).

**Key details:** Note: sync_bundled_help is NOT re-exported — callers import it from src.data.help.loader directly.

**Depends on:**
- help/store.py
- help/loader.py
- help/search.py

**Verify:** `python -c "import src.data.help"`

### `src/data/help/loader.py`  (A)

Loads assets/help/**/*.md (YAML frontmatter + markdown body) into help_articles. sync_bundled_help is the ca750a3 'sync when behind' fix: re-loads whenever on-disk *.md count exceeds DB row count (or DB is empty), otherwise returns {'skipped_current': N} at the cost of one COUNT. load_bundled_help upserts on content_hash, never raises; duplicate ids and unparseable frontmatter are skipped with logged errors.

**Key details:** _HELP_DIR = Path(__file__).resolve().parents[3]/'assets'/'help'. Required frontmatter: id, title, section. parse_article maps id→article_id, order→article_order, defaults status='available', applies_to='enablement'. bundled_file_count = rglob('*.md') count. NOT called at app startup: invoked lazily by src/ui/pages/enablement/help_tab.py (tab open) and src/data/chat_tools/help_tools.py (help_search tool) — both other-slice files.

**Depends on:**
- migration 048
- assets/help/ corpus (62 .md files, all A in this delta)
- PyYAML

**macOS risks:**
- parents[3] anchoring assumes the repo layout src/data/help/loader.py → project root; fine for a checkout, breaks if the file is relocated
- corpus filenames must check out exactly (case) — all lowercase-hyphen, safe on APFS

**Verify:** `python -m pytest tests/test_help_corpus_sync.py -x -q; in-app: open Help tab, expect 62 articles`

### `src/data/help/search.py`  (A)

Lexical help search: sanitized prefix-token FTS5 MATCH (OR of quoted terms, cap 24, stop-word list incl. negations), candidate pool 500, blend 0.25·bm25 + 0.35·coverage + 0.30·title-coverage + 0.10·summary-coverage; prefix-consistent _hits scoring; excerpt around first hit; zero-hit returns [] (callers fall back to section list). No LIKE fallback by design.

**Key details:** Public: search_help(conn, query, section=, limit=5) returning dicts with score, excerpt, status, banner. bm25 weights (help_articles_fts, 3.0,2.0,1.0,2.0).

**Depends on:**
- migration 048
- help/store.py (status_banner)

**Verify:** `python -m pytest tests/test_help_search.py -x -q`

### `src/data/help/store.py`  (A)

help_articles read/write layer: upsert_article (content_hash skip, REJECTS unknown status values — the honesty mechanism), list_articles/list_sections (applies_to='enablement' or 'both'), get_article, count_articles, status_banner text for partial/flag-gated/not-available.

**Key details:** STATUSES=('available','partial','flag-gated','not-available'). Plain execute+commit (loader runs at startup path, search runs in MCP subprocess).

**Depends on:**
- migration 048

**Verify:** `python -m pytest tests/test_help_store.py -x -q`

### `src/data/artifact_store.py`  (A)

Studio artifact registry over enablement_artifacts: create_artifact (kind whitelist KINDS incl. reserved 'podcast', link-field typo guard), update_artifact (whitelist _UPDATABLE, loud rejection), list_artifacts, artifact_dir (data/artifacts/<hex id>/ anchored to PROJECT ROOT). Also home of the shared slugify() used by KB card/folder names and deck filenames.

**Key details:** KINDS={diagram,quiz,deck,one_pager,battle_card,podcast}; STATUSES={draft,rendered,attached,published,archived}; _UPDATABLE={title,status,task_id,card_id,research_id,doc_id,draft_id,deck_id,spec_json,file_path,provenance_json}. slugify(text,max_len=60) → strict [a-z0-9-]. _ARTIFACTS_ROOT = <project>/data/artifacts. artifact_dir validates hex id (8-64 chars) then mkdir(parents=True).

**Depends on:**
- migration 047

**macOS risks:**
- data/artifacts/ is created under the project root — the Mac checkout dir must be writable (it is for a git working tree); do NOT relocate into the gitignored data/ carelessly — creating the subdir does not touch existing local settings

**Verify:** `python -c "from src.data.artifact_store import slugify, artifact_dir; print(slugify('A: B/c?'))" → 'a-b-c'`

### `src/data/quiz_artifacts.py`  (A)

Deterministic MD/YAML quiz format: parse_quiz_md (strict: 1-12 questions, 2-6 choices, >=1 correct), serialize_quiz (canonical diffable MD), to_guru_html (static JS-free HTML with <details>/<summary> answer reveals, all text html.escape'd).

**Key details:** MAX_QUESTIONS=12, MAX_CHOICES=6. Regexes for '### Qn:', '- [ ]/[x]', 'Explanation:'. yaml imported lazily.

**Depends on:**
- PyYAML

**Verify:** `python -c "from src.data.quiz_artifacts import parse_quiz_md; print(parse_quiz_md('### Q1: x?\n- [x] a\n- [ ] b')[0])" → True (test_quiz_artifacts_local.py is gitignored)`

### `src/data/task_brief.py`  (A)

Haiku enrichment briefs for Asana tasks: build_brief (prompt from config/prompts/enablement_task_brief.txt, strict-JSON validator {ask, deliverable, links, stakeholders, effective_date YYYY-MM-DD|null}, failure stamps brief_source_modified_at EXCEPT no_llm_client), dirty_brief_task_ids (self-describing queue: brief_source_modified_at != remote_modified_at), drain_briefs (cap enablement.asana.brief_per_poll_cap default 5, under llm_gen.background_gate), and BriefWorker (QTimer + daemon thread, own cadence — never the sync poll thread).

**Key details:** _PROMPTS_DIR = <project>/config/prompts. Input assembled from task + asana_task_extras (html_notes tag-stripped, custom fields, last 5 stories), 6000-char cap. Client via build_client_for_task('enablement_card_gen'). BriefWorker(db_manager).start(interval_seconds=60, floor 30); single-flight _running latch; fresh get_connection per drain.

**Depends on:**
- migrations 043+044+045
- src/data/asana_extras.py (A, other slice)
- src/data/enablement_tasks.py (this delta)
- src/data/llm_gen.py
- config/prompts/enablement_task_brief.txt (A in delta)
- claude CLI login for live briefs

**Settings keys:**
- enablement.asana.brief_per_poll_cap (default 5)

**macOS risks:**
- Live path needs the claude CLI on PATH as seen by the GUI-launched app (macOS GUI apps don't inherit shell PATH); degrade is clean (no_llm_client skips cycle without stamping)

**Verify:** `python -c "from src.data.task_brief import _validate_brief; print(_validate_brief('{\"ask\":\"a\",\"deliverable\":\"d\",\"links\":[],\"stakeholders\":[]}')[0])" → True`

### `src/data/llm_gen.py`  (A)

Shared generate→strip-fences→validate→retry-once primitive for all studio/KB generators, plus background_gate = threading.BoundedSemaphore(1) capping background Haiku CLI calls machine-wide (M1/16GB target: max 1 background + 1 interactive CLI process).

**Key details:** generate_validated(prompt, validator, task_type='enablement_card_gen', client=None, retries=1) → {'ok',value,raw,retries} or {'ok':False,'error': no_llm_client|llm_error|validation_failed}. client=None → build_client_for_task(task_type); no client returns BEFORE any side effect. strip_fences returns first fenced block. Retry appends validator errors verbatim.

**Depends on:**
- src/gemini/client_factory.build_client_for_task routing 'enablement_card_gen' → claude CLI (pre-existing routing)

**macOS risks:**
- Indirectly depends on claude CLI presence; module itself never spawns subprocesses

**Verify:** `python -c "from src.data.llm_gen import strip_fences; print(strip_fences('x```json\n{}\n```y'))" → '{}'`

### `src/data/mermaid_lint.py`  (A)

Pure-stdlib Mermaid linter/security-cleaner: strips %%{init}%% directives and click lines (QWebChannel-privileged renderer protection), hard-errors on javascript:/callback, validates header/bracket balance/reserved 'end' id/unquoted-parens labels; the single gate between generation and artifact persistence.

**Key details:** lint(text) -> (ok, errors, cleaned_source). Accepted headers: flowchart, graph, sequenceDiagram, stateDiagram-v2, stateDiagram, classDiagram, erDiagram, journey, gantt, pie.

**Verify:** `python -c "from src.data.mermaid_lint import lint; print(lint('flowchart TD\nA-->B')[0])" → True`

### `src/data/pptx_reader.py`  (A)

Read .pptx (bytes or path) into Markdown via python-pptx: titles→headings, text frames→bullets, tables→pipe tables, speaker notes→blockquotes. Best-effort: any failure returns '' and never raises (ingest treats '' as metadata-only). Closes the hole where Drive .pptx silently extracted ''.

**Key details:** Public: pptx_to_markdown(data). python-pptx imported lazily.

**Depends on:**
- python-pptx==1.0.2 (pre-existing dep)

**Verify:** `python -c "from src.data.pptx_reader import pptx_to_markdown; print(pptx_to_markdown(b'junk'))" → '' (no raise)`

### `src/data/pptx_store.py`  (M)

export_pptx now uses the committed brand template assets/templates/renn_deck.pptx by default (template_path= override; graceful degrade to python-pptx built-in when missing/corrupt), defensive layout/placeholder lookups (no KeyError on swapped templates), and optional per-slide speaker 'notes' carried through _normalize_outline.

**Key details:** _DECK_TEMPLATE = <project>/assets/templates/renn_deck.pptx. Layout contract (docs only): layout[0]=cover, layout[1]=title+bullets, layout[2]=divider. export_pptx(conn, deck_id, out_path, *, template_path=None). Old saved outlines unchanged (notes optional).

**Depends on:**
- assets/templates/renn_deck.pptx (A in delta — BINARY file; ensure it transfers intact, not via text patch)
- python-pptx

**macOS risks:**
- Binary asset must be copied byte-exact; a text-mode transfer corrupts the zip and export silently degrades to the default template (looks done but unbranded)

**Verify:** `python -m pytest tests/test_pptx_store.py -x -q`

### `src/data/enablement_doc_search.py`  (A)

Tokenized IDF-weighted search over enablement_documents via the mig-050 FTS index — the 0.000→0.93 recall fix. Blend 0.20·bm25 + 0.45·idf-weighted coverage + 0.25·idf-weighted name coverage + 0.10·plain coverage, gated on wcov >= WCOV_MIN=0.45 (corpus-size-robust precision cut). windowed_snippet renders the hit context.

**Key details:** Public: search_documents_ranked(conn, query, limit=20, source=None ['drive'|'upload'|'manual']), windowed_snippet(full_text, query, width=240), WCOV_MIN=0.45. Sanitized MATCH (quoted prefix tokens, cap 24). Any FTS exception → [] (silent).

**Depends on:**
- migration 050 applied (without it every doc search silently returns [])

**Verify:** `python -m pytest tests/test_enablement_doc_search.py -x -q`

### `src/data/enablement_store.py`  (M)

Two changes: (1) search_documents rewritten from whole-query LIKE to search_documents_ranked (return shape unchanged); (2) the style-guide tagged-document mechanism generalized into private _get/_set/_list/_set_active/_delete/_clear_guide helpers and a SECOND instance added: the card/article template ([CARD-TEMPLATE] tag, enablement.card_template_doc_id pointer), whose card_template_block() is appended into the same {style_guide} prompt slot in draft_card_from_document. _set_guide also strips inline <span> tags from guide text.

**Key details:** New public API: get_card_template, set_card_template, list_card_templates, set_active_card_template, delete_card_template, clear_card_template, update_card_template_text, card_template_block, update_style_guide_text. Keys: _STYLE_GUIDE_KEY='style_guide_doc_id', _CARD_TEMPLATE_KEY='card_template_doc_id'; tags '[STYLE-GUIDE]', '[CARD-TEMPLATE]'. draft_card_from_document: style_guide=style_guide_block(conn)+card_template_block(conn).

**Depends on:**
- src/data/enablement_doc_search.py
- migration 050 (search path)
- assets/templates/support_center_article_template.md (A in delta — seed template, loaded elsewhere)

**Settings keys:**
- enablement.style_guide_doc_id
- enablement.card_template_doc_id (new)

**Verify:** `python -m pytest tests/test_enablement_doc_search.py tests/test_help_claims_workbench.py -x -q`

### `src/data/enablement_tasks.py`  (M)

3-line change: _UPDATABLE whitelist gains remote_modified_at, brief_json, brief_status, brief_source_modified_at (update_task silently drops unlisted fields — without this the brief/write-back columns could never be written).

**Key details:** _UPDATABLE now = {status, priority, due_date, summary, title, description, assignee, assignee_gid, submitter, scratchpad, draft_id, source_url, kind, remote_modified_at, brief_json, brief_status, brief_source_modified_at}.

**Depends on:**
- migrations 043+045 (the columns themselves)

**Verify:** `grep brief_json src/data/enablement_tasks.py; brief write path exercised by task_brief`

### `src/data/enablement_monitor.py`  (M)

EnablementMonitor now (1) connects asana.tasks_updated to the folded changed signal, (2) start() takes asana_interval_seconds for the 60s Asana events cadence while Drive keeps the shared interval, (3) starts BriefWorker (own timer) when Asana is connected and KBWorker when kb_enabled(), (4) stop() also stops self._briefs/self._kb.

**Key details:** self.asana.tasks_updated.connect(...) is in __init__ OUTSIDE any try/except — AsanaMonitor MUST define the tasks_updated signal (delta to the asana monitor, another slice) or EnablementMonitor construction raises AttributeError. Workers stored as self._briefs / self._kb; stop() uses getattr fallbacks.

**Depends on:**
- AsanaMonitor.tasks_updated signal (asana slice — hard order)
- src/data/task_brief.py
- src/data/kb/worker.py
- src/data/asana_setup.is_asana_connected (pre-existing)

**Settings keys:**
- enablement.kb.enabled + enablement.demo_mode (via kb_enabled())

**Verify:** `python -c "import src.data.enablement_monitor" plus app launch in enablement mode without traceback`

### `src/data/doc_reader.py`  (M)

read_document gains strict mode: known binary extensions (_BINARY_EXT: pdf/doc/ppt/pptx/xls/images/archives/executables) or a NUL-byte content sniff (_looks_binary, first 2048 bytes) now raise UnsupportedDocumentError under strict=True (deck modelling) or return the lenient _unreadable stub under the default (Guru import). .docx is exempt from the sniff (legitimately a zip).

**Key details:** New public: UnsupportedDocumentError, read_document(path, *, strict=False). _looks_binary opens 'rb' — encoding-safe.

**Verify:** `python -m pytest tests/test_doc_reader.py tests/test_doc_reader_unsupported.py -x -q`

### `src/data/html_sanitize.py`  (A)

Stdlib-only (html.parser), Qt-free allowlist HTML sanitizer for everything reaching Chromium preview iframes: deny-by-default tags/attrs, DROP_WITH_CONTENT subtree suppression via a name stack (handles unmatched closes + drop-voids), scheme-checked href (https/http/mailto) and src (https/http/data:image base64), style rebuilt from an allowlisted property set rejecting url()/expression/@import/backslash, everything re-escaped, parser blow-up fails CLOSED to escape(html).

**Key details:** Public: sanitize_html(html), ALLOWED_TAGS, DROP_WITH_CONTENT. Enforced by tests/test_web_guardrails.py (no innerHTML in web/src). Load-bearing for the enablement web tabs (CLAUDE.md architecture rule).

**Verify:** `python -m pytest tests/test_html_sanitize.py -x -q`

---

### Slice-level notes — data-kb-help

**Hard ordering constraints (matter only for piecemeal moves; a full checkout satisfies them all):**
- Migrations must apply in numeric order 043→050; 049 ALTERs kb_cards so 046 must precede it; 050's triggers/back-fill reference enablement_documents (mig 027, already on the Mac base). The existing schema_migrator applies them at app launch — no manual DB surgery, so the gitignored data/ dir is only touched through the normal migration path.
- src/data/artifact_store.py must land before src/data/kb/card_format.py, drive_kb.py and kb/search-adjacent code (they import slugify from it) — practically: port artifact_store in the same batch as the kb package.
- migration 050 must be applied before the new enablement_store.search_documents / kb.search full-text floor / enablement_doc_search run, otherwise every document search silently returns [] (the FTS exception is swallowed).
- AsanaMonitor must gain the tasks_updated signal (asana slice) before or with src/data/enablement_monitor.py — the connect() is in __init__ outside try/except and will raise AttributeError at monitor construction otherwise.
- src/export/gdrive_export.py delta (create_folder/upload_file with app_properties, update_file, get_file_meta(fields=), find_child_by_app_property) and src/data/drive_reader.py delta (export_text pptx/pdf branches, list_folders, get_file) and src/data/google_access.py (A) must land before any kb/ module does live work; kb modules import them lazily so mere import order is free.
- src/data/asana_extras.py (A, other slice) + migrations 043-045 before task_brief.py does useful work (it degrades gracefully if extras are absent).
- assets/help/ corpus (62 .md files) must be checked out before sync_bundled_help counts make sense; assets/templates/renn_deck.pptx (BINARY) and config/prompts/enablement_task_brief.txt + enablement_{mermaid,quiz,one_pager,battle_card}.txt before their consumers run (all degrade to fallbacks if missing).
- pip install pypdf==6.14.2 in the Mac venv before Drive PDF ingestion (requirements.txt/lock + installer/build_release.py all pin it; pure-Python wheel, arm64-safe).

**Cross-file risks / silent failures:**
- Silent-failure #1: if migration 050 is missing or FTS5 is absent from the Mac Python's sqlite3, ALL enablement document search paths (enablement_store.search_documents, kb full-text floor, enablement_doc_search) catch the exception and return [] — the app looks fine, search is just empty. Verify FTS5 first.
- Silent-failure #2: missing pypdf never errors — drive_reader._extract_text's broad except turns the ImportError into '' and the KB writes metadata-only cards. Check pip show pypdf, not runtime behavior.
- Silent-failure #3: a corrupt/text-mode-transferred assets/templates/renn_deck.pptx makes export_pptx silently fall back to the unbranded python-pptx default deck. Transfer the binary via git, verify with python -c "from pptx import Presentation; Presentation('assets/templates/renn_deck.pptx')".
- Silent-failure #4: help corpus — sync_bundled_help only fires from the Help tab and the help_search chat tool (other-slice files help_tab.py / chat_tools/help_tools.py). If those files are not ported, the help tables stay empty with zero errors.
- All the deep KB unit tests (test_kb_store_local.py, test_kb_sync_local.py, test_kb_ingest_local.py, test_kb_drive_local.py, test_kb_search_local.py, test_task_brief_local.py, test_mermaid_lint_local.py, test_quiz_artifacts_local.py, test_artifact_store_local.py, test_pptx_artifacts_local.py) are GITIGNORED and will not exist on the Mac — the only committed KB test is test_kb_extra_and_readopt.py. Import-smoke plus the committed help/doc-search/sanitizer suites are the practical verification ceiling.
- kb_cards_fts triggers in 046 use the rowid-only 'delete' form on a contentless table (the very pattern 048's comment calls out as leaving stale terms). This is shipped behavior — port it verbatim, do not 'fix' it on the Mac or the schemas diverge from Windows.
- EnablementMonitor.start now spawns up to 2 extra QTimer+thread workers (BriefWorker, KBWorker). Both default OFF on a fresh Mac (demo_mode true / kb.enabled false / Asana not connected) — a port that 'works' proves nothing about worker behavior until those settings flip.
- ensure_ec_root WRITES settings (enablement.kb.ec_folder_id via set_section) and creates real Drive folders — do not run live KB bootstrap as part of port verification on the work Mac; the EC tree is also per-OAuth-client-ID (a tree bootstrapped elsewhere is readable but not writable, by design surfaced as needs_rebootstrap).

**macOS-specific:**
- No wmic/taskkill/CREATE_NO_WINDOW/backslash paths/cp1252 anywhere in this slice — all pathlib joins and explicit utf-8. Nothing to translate.
- Verify the arm64 Python's sqlite3 has FTS5 before trusting migrations 046/048/050: python -c "import sqlite3; c=sqlite3.connect(':memory:'); c.execute(\"CREATE VIRTUAL TABLE t USING fts5(x)\")".
- pip install pypdf==6.14.2 python-pptx==1.0.2 PyYAML in the Mac venv (pypdf is the only NEW pin; pure-Python wheel, no arch issues).
- Live brief/KB-summarize paths call the claude CLI through build_client_for_task; a GUI-launched app on macOS does not inherit the shell PATH — the existing client_factory PATH handling (other slice) is the fix point, this slice only degrades gracefully (no_llm_client / deterministic excerpt cards).
- assets/templates/renn_deck.pptx is binary — ensure git (not a patch tool) transfers it; confirm python-pptx can open it after checkout.
- data/artifacts/<id>/ is created lazily under the project root by artifact_store.artifact_dir — harmless to the gitignored Mac data/ dir (new subdirectory only, no existing files touched).

**Analyst notes / answered questions:**
- Migration→module map: 043→asana monitor/enablement_tasks/task_brief; 044→asana_extras+task_brief; 045→task_brief+enablement_tasks; 046→all of src/data/kb/*; 047→artifact_store (+studio tools slice); 048→src/data/help/*; 049→kb/store+kb/sync; 050→enablement_doc_search+enablement_store.search_documents+kb/search floor. (Answered — placed here per task instructions.)
- Help loader startup: NOT at app startup. sync_bundled_help(conn) is called lazily from help_tab.py on tab open and chat_tools/help_tools.py before help_search; it re-loads IFF disk *.md count (assets/help rglob) > help_articles row count or the table is empty — the ca750a3 sync-when-behind condition; otherwise it costs one COUNT and returns {'skipped_current': N}.
- KB worker threads/subprocesses: KBWorker and BriefWorker each use a main-thread QTimer firing a daemon threading.Thread per tick (single-flight latch, fresh sqlite connection per run). No direct subprocess and no Windows-only flags — nothing needs macOS-specific handling in this slice. The only subprocess is the claude CLI reached indirectly via llm_gen→build_client_for_task, serialized by llm_gen.background_gate (BoundedSemaphore(1)).
- pypdf==6.14.2: imported lazily at src/data/drive_reader.py:483 inside _extract_text's pdf branch (drive_reader is in another slice but the pin is load-bearing for kb/ingest). If missing, the ImportError is swallowed by the best-effort except → extraction returns '' → KB cards become metadata-only and no full-text row lands; completely silent.
- Unresolved: does the Mac base branch already contain src/data/agent_jobs.py, src/data/text_diff.py, src/data/enablement_identity.py? They are NOT in the 7adbe7b..dbb5c8f delta (so they predate it on this repo), but kb/sync and kb/ingest import them — the plan writer should confirm they exist on the Mac checkout at the same base.
- Unresolved: which slice owns wiring EnablementMonitor.start's new asana_interval_seconds argument from the page/main_window? No caller changes are in this slice's files.


## 2. Google Drive / Asana integration layer

### `src/data/google_access.py`  (A)

NEW 57-line module: the single auth_type-aware answer to 'is Google Drive usable right now'. Fixes the bug where google_oauth.is_active() (always False on the service-account path) was used as a connected-proxy, making a healthy SA credential look permanently disconnected.

**Key details:** google_access_ready() -> bool: delegates to DriveReader.from_settings().is_configured() (the one correct auth_type branch); never raises, never does network I/O; catches everything incl. google_oauth's ALMA_MCP_MODE RuntimeError -> False. service_account_email() -> str: reads client_email ONLY from the SA key file at enablement.drive.credentials_path (open(path, encoding='utf-8'), json.load), returns '' unless auth_type=='service_account'; the ONE place allowed to open the key file. Consumers: src/services/agent_chat.py:675, src/data/kb/worker.py:44 (per-tick gate), src/data/enablement_monitor.py:72, src/ui/dialogs/drive_folder_picker_dialog.py:294+299, src/ui/pages/enablement/settings.py:402/459, src/ui/widgets/drive_probe_worker.py:88 (email display).

**Depends on:**
- src/data/drive_reader.py (is_configured)
- src/data/settings_manager
- src/data/google_oauth.py (oauth_user branch only)

**Settings keys:**
- enablement.drive.auth_type (default 'service_account')
- enablement.drive.credentials_path
- enablement.drive.read_enabled

**macOS risks:**
- None Windows-specific. If google-auth/google-api-python-client are missing on the Mac, google_access_ready() correctly returns False (import error swallowed) — a silent 'not connected' that is by-design fail-closed, not a port bug.

**Verify:** `python -m pytest tests/test_google_access_gate.py tests/test_drive_sa_status.py -x -q`

### `src/data/drive_reader.py`  (M)

+282 lines: recursive folder-scoped search, SA picker roots, read-retry/backoff, pptx text extraction. Fixes: (a) single-page search recall cap, (b) 'in parents' non-recursion returning 0 hits for nested corpora, (c) SA folder picker rendering empty (SA entry points live in sharedWithMe, not My Drive/drives.list), (d) .pptx silently extracting '' via the docx branch.

**Key details:** New module-level: _read_with_retry(request, max_retries=5, base_delay=0.5) exp-backoff on _RETRYABLE=('rateLimitExceeded','userRateLimitExceeded','429','500','502','503','backendError','internalError'), matched on str(exc). _PARENTS_PER_QUERY=15; _SUBTREE_TTL_S=60.0; _SUBTREE_CACHE module dict keyed (sorted root ids tuple, max_folders, max_depth, auth_type, credentials_path) storing (monotonic_ts, ids, truncated). _parents_clause(folder_ids). New methods: DriveReader.list_shared_roots() (q="sharedWithMe = true and mimeType folder and trashed = false"), list_picker_roots() (SA: drops synthetic My Drive, = real Shared Drives + shared roots; oauth_user: My Drive + Shared Drives + shared roots), list_subtree_folder_ids(root_ids, *, max_folders=250, max_depth=10) -> (ids, truncated) BFS with chunked OR-groups + cache; truncated=capped OR non-empty frontier, logged. search_files(query, limit=20, *, folder_id=None) rewritten: pages nextPageToken to limit, scopes via subtree enumeration, queries EVERY parent chunk before applying limit (deep-miss fix), dedupes by file id, sets self._last_scope_truncated (init'd False in __init__), rows now include 'modified' (modifiedTime). _extract_text: 'presentationml' branch -> src.data.pptx_reader.pptx_to_markdown BEFORE the word/officedocument branch.

**Depends on:**
- src/data/pptx_reader.py (NEW in this delta, another slice — pptx branch ImportErrors without it)
- src/data/google_oauth.py (oauth_user creds)
- google-api-python-client + google-auth (pip)
- pypdf / python-docx / python-pptx for extraction

**Settings keys:**
- enablement.drive.credentials_path
- enablement.drive.auth_type
- enablement.drive.read_enabled

**macOS risks:**
- Pure stdlib+google libs, no Windows constructs. Ensure python-pptx installed on the Mac or deck extraction raises inside _extract_text (caught upstream? — export_text callers vary).
- _SUBTREE_CACHE is process-global keyed on credentials_path string: fine, but paths differ per-machine — no port issue, just don't hardcode.

**Verify:** `python -m pytest tests/test_drive_reader_search.py tests/test_drive_query.py tests/test_drive_folder_picker.py -x -q`

### `src/data/drive_query.py`  (M)

Adds build_live_drive_client() (production entry point that builds DriveReader.from_settings() when configured, else None — keeps query_business_drive injection-only) and threads folder_id scoping + scope_truncated honesty through query_business_drive.

**Key details:** build_live_drive_client() -> DriveReader|None (any failure -> None). query_business_drive(conn, query, *, limit=20, live_client=None, folder_id: str|list[str]|None=None): forwards folder_id to live_client.search_files(**kwargs); result dict gains 'scope_truncated': bool(getattr(live_client,'_last_scope_truncated',False)) on the live path. Mirror fallback path unchanged.

**Depends on:**
- src/data/drive_reader.py (search_files folder_id kwarg + _last_scope_truncated attr)

**Settings keys:**
- (indirect via DriveReader.from_settings)

**macOS risks:**
- None.

**Verify:** `python -m pytest tests/test_drive_query.py tests/test_drive_live_search_tools.py -x -q`

### `src/data/google_oauth.py`  (M)

Thread-safety: reconnect() (the only setter of the module-global _active Credentials) is now serialized by a module _ACTIVE_LOCK because KBWorker / DriveMonitor / exporter threads race google.oauth2's unlocked refresh into transient 401s.

**Key details:** New: import threading as _threading; _ACTIVE_LOCK = _threading.Lock() next to _active. reconnect() body wrapped in 'with _ACTIVE_LOCK:'; logic otherwise identical (stored record -> Credentials.from_authorized_user_info -> refresh if not valid -> _active). Still refuses in subprocess via _refuse_in_subprocess('reconnect') (ALMA_MCP_MODE).

**Depends on:**
- google-auth / google-auth-oauthlib (unchanged)

**macOS risks:**
- None — pure threading.

**Verify:** `python -m pytest tests/test_google_access_gate.py -x -q (oauth branch) — no dedicated new test; import + existing oauth tests suffice`

### `src/export/gdrive_export.py`  (M)

+118 lines: WS2-M1 Drive WRITE throttle/backoff chokepoint (_throttled_execute — nothing in the repo had write backoff before) plus a new write surface: create_folder, update_file, upload_file (bytes), get_file_meta, find_child_by_app_property. Existing upload path rerouted through the throttle.

**Key details:** Module state: _WRITE_LOCK, _MIN_WRITE_INTERVAL_S=0.5, _MAX_RETRIES=5, _last_write_ts. _is_rate_error(exc): str-match on rateLimitExceeded/userRateLimitExceeded/429/500/502/503. _throttled_execute(request): min-interval pacing under lock + exp backoff (1s doubling, cap 16). GoogleDriveExporter new methods: create_folder(name, parent_id=None, *, app_properties=None) -> {id,name,webViewLink}; update_file(file_id, content, mime_type='text/markdown') -> {id, modifiedTime} (caller records modifiedTime for echo suppression); upload_file(filename, data: bytes, mime_type, folder_id=None, *, app_properties=None) (parent = folder_id or self._folder_id; WS3-M7 artifact upload rides this); get_file_meta(file_id, fields='id, name, trashed, modifiedTime, capabilities/canAddChildren') — trashed is the field bootstrap re-verify needs (writes into trash succeed); find_child_by_app_property(folder_id, key, value) -> first non-trashed child with appProperties {key:value} (write-idempotency probe), escapes backslash+quote manually. RULE from comments: every Drive write anywhere must go through _throttled_execute, never request.execute().

**Depends on:**
- google-api-python-client (MediaIoBaseUpload)
- consumers: KB sync/ingest (src/data/kb/, other slice), artifact_tools upload (other slice)

**macOS risks:**
- None — time/threading only.

**Verify:** `python -m pytest tests/test_drive_live_search_tools.py -x -q plus any committed kb/export tests; smoke: python -c "from src.export.gdrive_export import GoogleDriveExporter, _throttled_execute"`

### `src/data/asana_client.py`  (M)

+276 lines: GET-only retry policy (429 honoring Retry-After, one 5xx retry), a generic _paginate helper following next_page.offset with expired-offset degradation, expanded _TASK_FIELDS, and three new methods: update_task (generic PUT), get_events (/events diff-poll with 412 handshake), list_stories (comment stories).

**Key details:** Constants: _MAX_429_RETRIES=2, _RETRY_AFTER_CAP_S=30, _5XX_BACKOFF_S=2, _RETRYABLE_5XX={500,501,502,503}, _PAGE_SIZE=100, _DEFAULT_MAX_PAGES=50, _PROJECTS_MAX_PAGES=200, _OFFSET_REJECT_CODES={400,401}. _open_get_with_retry(req): GETs only; 412 NEVER retried (it is the /events sync handshake); POST/PUT (_send) never retried. _paginate(path, params, *, max_pages=_DEFAULT_MAX_PAGES): mid-walk offset expiry (per _looks_like_offset_expiry: 400/401 + body mentions offset/pagination/token/expire, or unreadable body) -> log + return partial; first-page errors re-raise; page-cap logged. SIGNATURE CHANGES (the ff430fe pagination fix): list_subtasks(task_gid) — the '*, limit: int = 100' kwarg REMOVED; list_attachments(task_gid) — '*, limit: int = 100' REMOVED (no in-repo caller passed limit; verified). list_workspaces/list_projects/get_custom_fields/list_workspace_users/list_tasks_paged now route through _paginate (list_projects uses _PROJECTS_MAX_PAGES; previously silently capped at 100). _TASK_FIELDS now adds: start_on, html_notes, num_subtasks, custom_fields.number_value, custom_fields.text_value, custom_fields.date_value.date. New: update_task(task_gid, **fields) -> dict (PUT /tasks/{gid}, human-gated by caller, returns modified_at for CAS restamp); get_events(resource_gid, sync_token=None) -> {'events':[...], 'sync':token, 'has_more':bool, 'full_resync':bool} — catches HTTP 412 and reads the fresh sync token from the 412 body (full_resync=True); list_stories(task_gid, *, max_pages=5) filters resource_subtype=='comment_added', rows {gid,text,created_at,author}.

**Depends on:**
- stdlib only (urllib) — no SDK

**Settings keys:**
- (api key via load_setting('asana_api_key') — unchanged)

**macOS risks:**
- None — pure stdlib urllib + time.sleep.

**Verify:** `python -m pytest tests/test_asana_pagination.py tests/test_asana_readback.py tests/test_asana_writeback.py -x -q`

### `src/data/asana_extras.py`  (A)

NEW 167-line side-table store (asana_task_extras) for rich task context the lean poll skips: html_notes (stored raw, render PlainText only — UNTRUSTED), custom fields, attachments, comment stories. Staleness = for_modified_at != enablement_tasks.remote_modified_at (same-source comparison); drained capped per poll to avoid an extras storm.

**Key details:** Public: extras_cap() (reads enablement.asana.extras_per_poll_cap, default 10); upsert_extras(conn, task_id, task_payload, *, client=None) — INSERT..ON CONFLICT(task_id) DO UPDATE into asana_task_extras(task_id, html_notes, custom_fields_json, attachments_json, stories_json, for_modified_at, fetched_at); attachments/stories fetched best-effort via client.list_attachments/list_stories; get_extras(conn, task_id) decodes *_json to keys custom_fields/attachments/stories; stale_task_ids(conn, *, limit) -> [(task_id, source_ref)] joining enablement_tasks (source='asana', status!='dismissed', remote_modified_at non-empty, mismatch vs for_modified_at); mark_stories_dirty(conn, gids) sets for_modified_at='' (story events never bump task modified_at); drain_stale(conn, client, *, cap=None, payload_cache=None) — payload_cache (gid->task payload from events path) saves the get_task refetch. Qt-free; plain execute+commit, NO atomic() (poll-thread + MCP-subprocess safe).

**Depends on:**
- migrations/044_asana_task_extras.sql (table)
- migrations/043_asana_events.sql (enablement_tasks.remote_modified_at)
- src/data/asana_client.py (list_attachments/list_stories/get_task/_TASK_FIELDS)
- src/data/settings_manager

**Settings keys:**
- enablement.asana.extras_per_poll_cap (default 10)

**macOS risks:**
- None.

**Verify:** `Committed coverage is thin (test_asana_extras_local.py is gitignored and will NOT be on the Mac). Verify: python -c "from src.data import asana_extras" + python -m pytest tests/test_asana_readback.py tests/test_asana_monitor.py -x -q`

### `src/data/asana_monitor.py`  (M)

+373 lines: events-API diff polling (default ON) with 412 token handshake + paged baseline re-list, per-board degradation to the legacy modified_since poll after 3 consecutive failures, shared per-task pipeline, extras drain after every poll, deleted/removed/story event handling, and a new tasks_updated Qt signal.

**Key details:** Constants: _DEFAULT_INTERVAL=300 (monitor start default unchanged), _ASANA_DEFAULT_INTERVAL=60 (comment says enablement.asana.poll_interval_seconds — the constant exists here; the reader of that key is elsewhere/UI), _EVENTS_FAILURE_LIMIT=3 with in-memory _events_failures dict, _BASELINE_MAX_PAGES=50. _use_events(): reads enablement.asana.use_events, DEFAULT TRUE, settings-error -> True. poll_once(conn, *, client=None, results: dict|None=None) — return contract still the created-list; results out-param gains 'created'/'updated' lists + internal '_payload_cache' (gid->payload); after all boards, asana_extras.drain_stale(conn, client, payload_cache=...) runs (never fatal). _poll_board -> tries _poll_board_events unless flag off/failure-limit hit, else _poll_board_legacy (old behavior verbatim). _poll_board_events: token from monitor_sources.events_sync; no token or full_resync -> get_events(gid, None) 412 handshake THEN _run_baseline (list_tasks_paged modified_since=cursor, max 50 pages, cap logged) THEN store token; event loop classifies task added/changed/undeleted (fetch via get_task with _TASK_FIELDS; 404 -> _dismiss_by_gid), deleted -> _dismiss_by_gid (status='dismissed', never row-delete), removed -> _handle_removed (verify with get_task; only dismiss on 404, live task gets scratchpad note '[monitor] No longer on {board} (moved in Asana).'), story events -> asana_extras.mark_stories_dirty. _reconcile_existing_task now returns task_id|None (was bool), stamps fields['remote_modified_at']=task['modified_at'] (CAS anchor), and SKIPS the status='done' flip while asana_writeback.is_status_inflight(tid) (in-flight guard). _create_task_from_asana also stamps remote_modified_at. _mark_board gains events_sync=None kwarg (dynamic SET list on monitor_sources). AsanaMonitor gains tasks_updated = Signal(list) emitted from _fetch when results['updated'] non-empty.

**Depends on:**
- migrations/043_asana_events.sql (monitor_sources.events_sync + enablement_tasks.remote_modified_at) — MUST be applied first
- src/data/asana_client.py (get_events/list_tasks_paged/update _TASK_FIELDS)
- src/data/asana_extras.py
- src/data/asana_writeback.py (is_status_inflight)
- src/data/enablement_tasks.py update_task accepting remote_modified_at kwarg (other slice)

**Settings keys:**
- enablement.asana.use_events (default true)
- enablement.asana.poll_interval_seconds (named in comment; constant _ASANA_DEFAULT_INTERVAL=60)
- enablement.asana.extras_per_poll_cap (via extras drain)

**macOS risks:**
- None Windows-specific. Behavior note: events path is ON by default with no settings change, so the Mac gets the new path immediately after migration.

**Verify:** `python -m pytest tests/test_asana_monitor.py tests/test_asana_readback.py -x -q (events-path deep tests are in gitignored *_local.py files and won't exist on the Mac)`

### `src/data/asana_setup.py`  (M)

discover() now tags its result with a 'mock': bool flag so callers can refuse to persist a board config built from MOCK_DISCOVERY's fabricated GIDs into a real source (finding 14). Returns a shallow copy so the shared MOCK_DISCOVERY constant is never mutated.

**Key details:** discover(api_key=None, project_gid=None): sets is_mock=True for the mock path (no key, or client error fallback), False on live client success; project_gid branch returns {'mock': is_mock, 'workspace':…, 'project':…, 'custom_fields':…}; no-gid branch returns {**data, 'mock': is_mock}. The gate that consumes the flag ('the setup writer') is in the UI slice.

**Depends on:**
- src/data/asana_client.py

**macOS risks:**
- None.

**Verify:** `python -m pytest tests/test_asana_setup_guard.py -x -q`

### `src/data/asana_writeback.py`  (M)

+142 lines: WS1-M5 write-back hardening — module-level in-flight status guard (poll reconcile must not revert an optimistic complete-flip mid-PUT), check-and-set precheck on destructive verbs (compare Asana live modified_at vs stored remote_modified_at anchor), CAS restamp from the PUT response, and a new set_completed_in_asana verb with revert-on-failure.

**Key details:** New module state: _INFLIGHT_LOCK (threading.Lock), _INFLIGHT_STATUS dict task_id->status; public is_status_inflight(task_id) (consumed by asana_monitor._reconcile_existing_task). _cas_precheck(conn, task_id, gid, client): SELECT remote_modified_at FROM enablement_tasks; live GET modified_at; mismatch -> {'ok':False,'conflict':True,'error':'task changed in Asana since it was last synced'}; NULL anchor -> fetch-and-stamp then proceed; failed CAS read -> proceed (protection not gate); Asana has no If-Match, sub-second GET->PUT race self-heals via reconcile. _restamp(conn, task_id, response): stamps remote_modified_at from PUT response; also advances brief_source_modified_at (mig 045 column) when brief_status != 'pending' (self-write suppression so our own click doesn't burn a Haiku brief rebuild). update_due_in_asana: now CAS-gated; local et.update_task moved AFTER the unlinked/unconfigured early-returns and after CAS; on conflict returns the conflict dict WITHOUT applying locally. NEW set_completed_in_asana(conn, task_id, done: bool, *, client=None) -> dict: CAS precheck -> _set_inflight -> optimistic local status flip ('done'/'open') -> client.update_task(gid, completed=bool(done)) -> _restamp; API failure REVERTS the flip only if status still holds the optimistic value ({'ok':False,'error':…,'reverted':True}); finally _clear_inflight. Unlinked/unconfigured degrade: local-only update with synced:False notes (unchanged shape).

**Depends on:**
- migrations/043_asana_events.sql (remote_modified_at)
- migrations/045_task_brief.sql (brief_status, brief_source_modified_at)
- src/data/asana_client.py (get_task/update_task)
- src/data/enablement_tasks.py update_task accepting the new kwargs

**macOS risks:**
- None — threading only.

**Verify:** `python -m pytest tests/test_asana_writeback.py tests/test_asana_readback.py -x -q`

### `src/data/chat_action_requests.py`  (M)

Fixes the human-gate 'gate-brick' class: INFORMATIONAL action types (research_plan, artifact_preview) are excluded from has_pending_action (their UI never resolves them, so one such row previously closed the gate for the whole session), and create_action_request now SUPERSEDES (resolved=1) any older unresolved request of the same type in the session. Adds two confirm_write ops.

**Key details:** New frozenset INFORMATIONAL_ACTION_TYPES = {'research_plan', 'artifact_preview'}. has_pending_action(conn, session_id): SQL now '... AND type NOT IN (placeholders)' bound to sorted(INFORMATIONAL_ACTION_TYPES). create_action_request: before INSERT, runs UPDATE chat_action_requests SET resolved=1 WHERE session_id=? AND type=? AND resolved=0 (confirm_write rows can never be hit — they are rejected earlier in this function). _CONFIRM_WRITE_OPS gains 'asana_task_update' (WS1-M6 complete/reopen/set_due/comment/add_subtask) and 'upload_artifact_to_drive' (WS3-M7 publish artifact into EC folder). No schema change — table from pre-existing migration 038.

**Depends on:**
- migration 038 (chat_action_requests table, pre-existing)
- consumers: agent_chat / chat tool handlers (other slices) that gate on has_pending_action and dispatch the two new ops

**macOS risks:**
- None.

**Verify:** `Committed gate tests test_action_channel_local.py / test_action_guards_local.py are gitignored (_local) — on the Mac verify via import + the chat-tool test files in the services slice; python -c "from src.data.chat_action_requests import INFORMATIONAL_ACTION_TYPES, has_pending_action"`

### `src/data/usage_tracker.py`  (M)

UsageTracker.log_call gains cost_usd: float = None — a REAL reported cost (e.g. the Claude CLI's total_cost_usd) wins over the Gemini plan-pricing estimate; None keeps the old estimate_cost path byte-identical.

**Key details:** Signature: log_call(self, source, tokens_in, tokens_out, model='gemini-2.5-flash', scan_id=None, cost_usd=None). Body: cost = float(cost_usd) if cost_usd is not None else self.estimate_cost(...). Still writes via self.db.log_gemini_usage (db_manager.py:2378, unchanged).

**Depends on:**
- src/data/db_manager.py log_gemini_usage (unchanged)

**macOS risks:**
- None.

**Verify:** `python -m pytest tests/test_scoped_usage_tracker.py -x -q`

### `src/data/scoped_usage_tracker.py`  (M)

Pass-through of the new cost_usd kwarg: ScopedUsageTracker.log_call forwards cost_usd to the base UsageTracker.log_call. Must land together with usage_tracker.py or the forward raises TypeError.

**Key details:** log_call(..., scan_id=None, cost_usd: float | None = None) -> forwards cost_usd=cost_usd.

**Depends on:**
- src/data/usage_tracker.py (cost_usd param must exist first)

**macOS risks:**
- None.

**Verify:** `python -m pytest tests/test_scoped_usage_tracker.py -x -q`

### `src/data/renn_usage.py`  (A)

NEW 164-line Renn usage metering: RennUsageRecorder writes one gemini_usage row per assistant turn with source='renn_chat' (NO migration — gemini_usage pre-exists in db_manager.initialize with all needed columns), plus the read-side query helpers the enablement Usage tab renders.

**Key details:** RENN_SOURCES=('renn_chat',); RENN_CHAT_SOURCE='renn_chat'. RennUsageRecorder(db_path, source='renn_chat'): bridge-compatible sink (log_call + estimate_tokens, same surface as UsageTracker); log_call(source=None, tokens_in=0, tokens_out=0, model='', scan_id=None, cost_usd=None) opens a FRESH WAL connection per write via get_connection(self.db_path) (thread-safe from the chat worker, no DatabaseManager), INSERTs into gemini_usage(date, hour, source, scan_id, tokens_in, tokens_out, cost_usd, api_calls=1, model, created_at); never raises. Subscription (claude.ai) CLI logins report no cost -> 0.0 recorded. Read side: usage_summary(conn, *, today=None) -> {today, week, month, all_time (each {turns,tokens_in,tokens_out,cost_usd}), series (daily_series 14d zero-filled), recent (recent_turns 8)}; all-zero dict on any failure. HOOK CHAIN (answers the question): src/services/chat_engine.py:467-478 — for task types startswith('enablement'), calls client.set_usage_sink(RennUsageRecorder(self._db_path), source=RENN_CHAT_SOURCE); src/agents/claude_cli_bridge.py:582 set_usage_sink stores sink+source; its _log_usage_if_configured logs per terminal 'result' event, passing the CLI's total_cost_usd verbatim (None when unreported). Panel: src/ui/pages/enablement/usage_tab.py:232 calls renn_usage.usage_summary(conn).

**Depends on:**
- gemini_usage table (pre-existing, db_manager.py:604 — NO new migration)
- src/data/connection_factory.get_connection
- hook-side files in other slices: src/services/chat_engine.py, src/agents/claude_cli_bridge.py, src/ui/pages/enablement/usage_tab.py

**macOS risks:**
- Metering only produces rows when the 'claude' CLI is installed + logged in on the Mac (cross-slice dependency); with a subscription login cost_usd is 0.0 by design — do not 'fix' that on port.

**Verify:** `python -m pytest tests/test_renn_usage.py -x -q`

### `src/data/source_baseline.py`  (M)

Test-determinism only: list_active_trcs gains an optional now: datetime|None kwarg. None (production) keeps the SQLite datetime('now') query byte-identical; a fixed now formats a cutoff string ('%Y-%m-%dT%H:%M:%S', matching hour_bucket's stored shape) so seeded tests don't drift out of window as the anchor ages.

**Key details:** list_active_trcs(conn, source, *, lookback_hours=48, now: datetime | None = None). Two query branches; the now-branch computes cutoff=(now - timedelta(hours=lookback_hours)).strftime('%Y-%m-%dT%H:%M:%S') and compares hour_bucket >= ?. Requires timedelta already imported at module top (it is).

**macOS risks:**
- None. Reminder: this file carries the deliberate heavy-docstring override from the 2026-05-07 redesign — do not strip on port.

**Verify:** `python -m pytest tests/test_source_baseline.py -x -q`

---

### Slice-level notes — data-google-asana

**Hard ordering constraints (matter only for piecemeal moves; a full checkout satisfies them all):**
- migrations 043_asana_events.sql -> 044_asana_task_extras.sql -> 045_task_brief.sql must be applied (schema_migrator runs them on launch) BEFORE asana_monitor/asana_extras/asana_writeback execute: 043 adds monitor_sources.events_sync + enablement_tasks.remote_modified_at; 044 creates asana_task_extras; 045 adds brief_status/brief_source_modified_at read by asana_writeback._restamp.
- src/data/usage_tracker.py must land before/with src/data/scoped_usage_tracker.py (cost_usd forward would TypeError against the old base signature).
- src/data/pptx_reader.py (A, another slice) must exist before drive_reader._extract_text's presentationml branch works — until then .pptx export_text raises ImportError inside extraction.
- src/data/drive_reader.py (search_files folder_id kwarg + _last_scope_truncated) must land before/with src/data/drive_query.py and before src/data/chat_tools/enablement_tools.py (other slice) which calls build_live_drive_client + the folder_id seam.
- src/data/asana_client.py must land before asana_monitor/asana_extras/asana_writeback (they use get_events, list_stories, update_task, expanded _TASK_FIELDS, and the limit-less list_subtasks/list_attachments signatures).
- src/data/asana_writeback.py exports is_status_inflight consumed by asana_monitor._reconcile_existing_task — land writeback with or before monitor.
- src/data/enablement_tasks.py (other slice) must accept remote_modified_at / brief_source_modified_at kwargs in update_task before monitor/writeback stamp them.
- renn_usage.py is standalone (no migration) but produces nothing until the hook files land (chat_engine.py set_usage_sink call + claude_cli_bridge.set_usage_sink/_log_usage_if_configured, other slice) and the 'claude' CLI is logged in on the Mac.

**Cross-file risks / silent failures:**
- Missing-migration failures are SILENT by design: if 043 is not applied, asana_monitor's events path raises inside _poll_board_events (SELECT events_sync), is caught, counts toward _EVENTS_FAILURE_LIMIT, and degrades to the legacy poll — the app 'works' while hiding the un-applied migration; likewise asana_extras.drain_stale failures log at debug only. Verify migrations applied explicitly, don't trust runtime behavior.
- The Asana events path is ON by default (enablement.asana.use_events default True, read via settings with error->True) — the Mac's first post-port poll performs the 412 token handshake + a paged baseline re-list against the real board. This changes live API traffic without any settings edit.
- asana_client signature changes: list_subtasks and list_attachments LOST their limit kwarg — any out-of-tree/local caller passing limit= gets TypeError (all in-repo callers verified clean: asana_extras.py:52, asana_monitor.py:413, content_update/load_source.py:51).
- The richest tests for the events path, extras, gated writes, and the action channel are in gitignored *_local.py files (test_asana_client_events_local.py, test_asana_events_poll_local.py, test_asana_extras_local.py, test_asana_gated_writes_local.py, test_action_channel_local.py, test_action_guards_local.py) — they will NOT exist on the Mac; committed coverage is test_asana_pagination.py / test_asana_writeback.py / test_asana_readback.py / test_asana_setup_guard.py / test_renn_usage.py / test_drive_* / test_google_access_gate.py / test_source_baseline.py / test_scoped_usage_tracker.py. A green Mac run therefore proves LESS than the dev box's run did.
- asana_setup.discover() now returns an extra 'mock' key in both return shapes — any caller doing exact-dict comparison or key iteration breaks; the writer that must gate on it lives in the UI slice, so if that slice is ported later, mock configs are flagged but not yet blocked.
- chat_action_requests.has_pending_action semantics changed (informational types excluded; same-type supersede on create). Chat-tool handlers in other slices assume the new semantics; porting them without this file re-introduces the gate-brick bug, porting this file without them is safe.
- update_due_in_asana behavior change: on CAS conflict it now returns {'ok': False, 'conflict': True} WITHOUT applying the local update (previously local-first, unconditional). Callers rendering the result must handle the conflict shape.
- Module-level in-memory state added in several files (_SUBTREE_CACHE keyed on credentials_path+auth_type, _INFLIGHT_STATUS, _events_failures, gdrive _last_write_ts) — all single-process assumptions consistent with the app; do not import these modules into a second long-lived process expecting shared state.
- drive_reader._SUBTREE_CACHE type annotation says tuple[float, list[str]] but a 3-tuple (ts, ids, truncated) is stored/read — cosmetic, works; don't 'fix' the tuple shape without fixing both sites.

**macOS-specific:**
- This slice contains NO Windows-only constructs (no wmic/taskkill/CREATE_NO_WINDOW/backslash paths/%APPDATA%/cp1252) — it is the most portable slice of the delta; all new I/O is stdlib urllib, time.sleep, threading, and googleapiclient.
- pip on the Mac (arm64 Python) must have: google-api-python-client, google-auth (Drive live read/write), pypdf, python-docx, and python-pptx (new — pptx_reader dependency for deck extraction). Absent google libs fail CLOSED (google_access_ready()->False, DriveReader._build_service raises a clear ImportError).
- Renn usage metering needs the 'claude' CLI installed and logged in on the Mac (claude.ai login, per project convention — NOT Bedrock); subscription logins report no cost so gemini_usage.cost_usd rows will be 0.0 there — expected, the Usage tab says so.
- Do NOT touch the Mac's gitignored data/ dir: enablement.drive.credentials_path in its settings.yaml points at a Mac-local SA key file; all new settings keys in this slice have safe in-code defaults (use_events=True, extras_per_poll_cap=10, active_folders unset -> whole-Drive scope) so no settings edits are required for the port.
- The SA key path stored in the Mac's settings is opened with encoding='utf-8' (google_access.service_account_email) — fine on macOS; just confirm the path exists (case-sensitive APFS if configured, though default macOS is case-insensitive).

**Analyst notes / answered questions:**
- ANSWER — auth_type-aware Drive reachability gate: google_access.google_access_ready() (new module) delegates to DriveReader.from_settings().is_configured(), which branches on enablement.drive.auth_type: 'service_account' (default) = read_enabled + credentials_path file exists; 'oauth_user' = read_enabled + google_oauth.is_active() this session (disable-on-launch). It replaces the buggy is_active()-as-proxy that reported SA setups permanently disconnected. Consumers: services/agent_chat.py:675, data/kb/worker.py:44 (per-tick), data/enablement_monitor.py:72, ui/dialogs/drive_folder_picker_dialog.py:294, ui/pages/enablement/settings.py:402+459, ui/widgets/drive_probe_worker.py:88 (service_account_email — the share-to address surfaced in UI).
- ANSWER — Renn usage metering: yes, writes the existing gemini_usage table with source='renn_chat' (RENN_CHAT_SOURCE); NO migration needed (table + all columns date/hour/source/scan_id/tokens_in/tokens_out/cost_usd/api_calls/model/created_at pre-exist in db_manager.initialize, line 604). Hooked per turn: chat_engine.py:467-478 attaches RennUsageRecorder via client.set_usage_sink(...) for task types starting with 'enablement'; claude_cli_bridge.py:582 set_usage_sink + _log_usage_if_configured write one row per terminal CLI 'result' event, passing total_cost_usd verbatim (None on subscription logins -> 0.0). Read by ui/pages/enablement/usage_tab.py:232 via renn_usage.usage_summary(conn).
- ANSWER — enablement.drive.active_folders: read by src/data/chat_tools/enablement_tools.py handle_search_google_drive (default scope; entries normalized to {id,name,drive_id} by _normalize_folder_entry; also persisted there at :408-432), src/ui/pages/enablement/page.py:1438-1461 and settings.py:964 (feed the settings card/picker), ui/dialogs/drive_folder_picker_dialog.py (the picker that writes it), enablement_tools.py:835 (another consumer). FALLBACK when unset: folder_scope=None -> query_business_drive searches EVERYTHING the account can see, and the tool's scope block reports kind='all_visible', recursive=False with the note 'no active Drive folder set — searched everything the account can see'.
- ANSWER — Asana pagination fix signature changes: list_subtasks(task_gid, *, limit=100) -> list_subtasks(task_gid); list_attachments(task_gid, *, limit=100) -> list_attachments(task_gid) (limit kwarg REMOVED from both). list_workspaces/list_projects/get_custom_fields/list_workspace_users/list_tasks_paged kept signatures but now paginate fully via the new _paginate helper (list_projects up to 200 pages; expired-offset mid-walk -> partial results, logged). New methods alongside: update_task(task_gid, **fields), get_events(resource_gid, sync_token=None), list_stories(task_gid, *, max_pages=5).
- OPEN — who reads enablement.asana.poll_interval_seconds? The constant _ASANA_DEFAULT_INTERVAL=60 and its comment live in asana_monitor.py but nothing in THIS slice reads that settings key or passes it to AsanaMonitor.start() (default still 300, floor 60); the wiring is presumably in the enablement page/monitor-startup slice — the plan writer should confirm there.
- OPEN — the 'setup writer gates on this flag' for asana_setup's new 'mock' key is not in this slice (UI/settings slice); confirm the gating consumer is ported in the same batch or mock GIDs can still be persisted.
- OPEN — artifact_tools.py:467 references target_folder_id > enablement.kb.ec_folder_id > STEER precedence for uploads (explicitly NOT active_folders); that file is another slice — flagging so the Drive-scoping story stays consistent across slices.


## 3. Chat / LLM / Renn — persona, guardrails, tools, metering

### `src/agents/claude_cli_bridge.py`  (M)

Renn persona fix (0ff816d): new set_system_prompt(text) writes the persona to a reused temp file and passes it as --system-prompt-file (REPLACES the CLI's 'you are Claude Code' identity); subprocess now runs from a NEUTRAL cwd so the claude CLI cannot walk up and ingest repo CLAUDE.md files (~14.6K tokens/turn leak, live-verified on CLI 2.1.216); MCP server env gets PYTHONPATH anchored to the app root. Also new set_usage_sink(sink, source, scan_id) so Renn chat meters into gemini_usage as source='renn_chat', and _log_usage_if_configured now passes cost_usd=(parser.cost_usd or None).

**Key details:** Module const _APP_ROOT = Path(__file__).resolve().parents[2]. set_system_prompt: tempfile.mkstemp(prefix='alma_sysprompt_', suffix='.txt') in OS temp dir, os.close(fd), Path.write_text(text, encoding='utf-8'); reused if same text AND file still exists (survives %TEMP%/tmp cleaners); empty text unlinks + clears (Gemini/report-bridge paths stay byte-identical); _system_prompt_text cached only AFTER durable write. _build_cmd appends ['--system-prompt-file', path] when set. _neutral_cwd(): Path(tempfile.gettempdir())/'alma_cli_neutral', mkdir(parents=True, exist_ok=True), returns None on OSError (inherit legacy cwd); passed as cwd= to CliSubprocess in _spawn_subprocess. set_mcp_config: env.setdefault('PYTHONPATH', str(_APP_ROOT)) per server spec (explicit PYTHONPATH in spec wins). New attrs _system_prompt_text/_system_prompt_path/_usage_source (default 'nlp_scan' — scan_orchestrator assigns _usage_tracker directly and keeps old behavior). shutdown() unlinks the persona temp file. CLI resolution unchanged: claude.cli_path setting else shutil.which over ('claude','claude.cmd','claude.exe').

**Depends on:**
- src/agents/claude_cli_subprocess.py (new cwd kwarg)
- src/agents/claude_cli_stream.py parser.cost_usd (pre-existing, unchanged)
- src/data/usage_tracker.py log_call must accept cost_usd kwarg (9-line change, other slice) — bridge passes it to ANY sink including the scan path's UsageTracker
- local 'claude' CLI login, version with --system-prompt-file/--tools/--no-session-persistence (verified on 2.1.216)

**Settings keys:**
- claude.cli_path (pre-existing, read by _find_claude_cli)

**macOS risks:**
- shutil.which('claude') fails under a GUI-launched app on macOS (minimal PATH, no /opt/homebrew/bin); 'claude.cmd'/'claude.exe' fallbacks are Windows-only — operator must have claude on PATH for the launch context or set claude.cli_path in the Mac's local data/settings.yaml (do NOT commit; data/ is gitignored)
- tempfile.gettempdir() on macOS is per-user /var/folders/... — fine; comments reference %TEMP% but code is portable
- persona temp file at rest in TMPDIR — same exposure as Windows, unlink-on-shutdown handles it

**Verify:** `python -m pytest tests/test_claude_cli_bridge.py tests/test_claude_cli_bridge_mcp.py tests/test_claude_cli_system_prompt.py -x -q; live: 4/4 persona turns previously green — a Renn chat turn must answer in persona, not as Claude Code, and must NOT mention repo engineering rules`

### `src/agents/claude_cli_subprocess.py`  (M)

CliSubprocess gains an optional cwd parameter, plumbed to subprocess.Popen(cwd=...), so the bridge can launch the CLI from the neutral directory. None = inherit (legacy).

**Key details:** __init__(self, cmd, env, prompt, cwd: str | None = None); Popen(... env=self.env, cwd=self.cwd, creationflags=(subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)).

**macOS risks:**
- CREATE_NO_WINDOW correctly guarded by sys.platform=='win32' — no change needed

**Verify:** `covered by tests/test_claude_cli_bridge.py + tests/test_claude_cli_system_prompt.py`

### `src/llm/claude_cli_client.py`  (M)

_prepare_prompt now returns (user_prompt, system_prompt) as a TUPLE instead of embedding a [SYSTEM INSTRUCTIONS] block in user content (the structural prompt-injection Sonnet refused on 2026-07-21); generate()/generate_stream() call bridge.set_system_prompt(sys_prompt) each turn. New set_usage_sink(sink, source='renn_chat') stored on the client and re-applied by _ensure_bridge after any bridge respawn.

**Key details:** _prepare_prompt(prompt, system_prompt) -> tuple[str, str]; still runs GeminiClient._redact_base/_redact_aggressive on BOTH parts (redaction single-source at config/redaction_patterns.json). _ensure_bridge re-applies _mcp_config AND _usage_sink (getattr-guarded, so old pickles/instances safe). Gemini/report-bridge callers keep the [SYSTEM INSTRUCTIONS] convention — only this CLI path changed.

**Depends on:**
- src/agents/claude_cli_bridge.py set_system_prompt + set_usage_sink

**Verify:** `python -m pytest tests/test_claude_cli_system_prompt.py -x -q (asserts tuple return + bridge wiring)`

### `src/llm/claude_client.py`  (M)

Redaction now FAILS CLOSED: new RedactionError(RuntimeError); _redact_text raises it instead of the old 'log warning and send un-redacted' fallback, so generate() aborts before anything leaves the machine when the redaction pipeline itself errors (e.g. unreadable pattern config). Closes the HIGH finding from the 2026-07-01 security review (claude_client.py:83 fail-open).

**Key details:** class RedactionError(RuntimeError) at module level; except branch in _redact_text now logger.error(...) + raise RedactionError(f'PII redaction failed: {e}') from e.

**Depends on:**
- src/gemini/gemini_client.py _load_redaction_config (unchanged)
- config/redaction_patterns.json present and readable

**macOS risks:**
- if config/redaction_patterns.json is missing/unreadable on the Mac checkout, ALL Claude API calls now hard-fail instead of silently sending raw text — verify the file ships and is readable before declaring the port done

**Verify:** `python -m pytest tests/test_redaction_fail_closed.py -x -q`

### `src/llm/claude_tools.py`  (M)

Native Claude tool surface updated: TOOL_DEFINITIONS adds search_google_drive / search_everywhere / import_drive_doc, rewrites search_local_documents + query_business_drive descriptions (local-vs-live split), and REPLACES the three un-gated Asana write tools (create_asana_subtask, post_asana_comment, update_asana_due_date) with the single Confirm-gated request_asana_task_update. _DISPATCH updated to match.

**Key details:** New dispatchers _search_google_drive/_import_drive_doc/_search_everywhere delegate to enablement_tools handle_* with conn from db.get_connection() or db.conn. _request_asana_task_update -> _request_asana_task_update_impl(_ent_conn(db), _active_session_id(), task_id, action, value) and returns a STRING (write-wait steer). request_asana_task_update schema: required [task_id, action], action enum [complete, reopen, set_due, comment, add_subtask], value string. Old _create_asana_subtask/_post_asana_comment/_update_asana_due_date dispatchers deleted.

**Depends on:**
- src/data/chat_tools/enablement_tools.py new handlers + _request_asana_task_update_impl + _active_session_id

**Verify:** `python -m pytest tests/test_chat_tools.py -x -q`

### `src/mcp/chat_mcp_server.py`  (M)

MCP TOOL_SCHEMAS extended with the full new surface: search_google_drive, search_everywhere, import_drive_doc (live-Drive trio), list_artifacts, generate_diagram, generate_deck, generate_quiz, generate_doc, attach_artifact_to_draft, request_upload_artifact_to_drive (content studio), kb_search, kb_list_topics, kb_list_cards, kb_get_card, index_drive_folder (KB), help_search (Help Center), request_asana_task_update (replacing the 3 retired Asana write schemas), and list_tasks gains a task_id detail mode. Retired tools get a purposeful-error shim.

**Key details:** _RETIRED_TOOL_HINTS dict maps create_asana_subtask/post_asana_comment/update_asana_due_date -> steering error string ('call request_asana_task_update(task_id, action, value) instead'); checked FIRST in _execute_tool, before _MCP_ALLOWED_TOOLS. All execution still routes through registry.dispatch_tool(tool_name, args, conn, session_filters={}, session_id from ALMA_CHAT_SESSION_FILE pointer (None -> 'adhoc_probe'), message_id=None). DB via ALMA_DB_PATH env. TOOL_SCHEMAS is also read by chat_engine._live_tool_names() for the Tier-A drift guard.

**Depends on:**
- src/data/chat_tools/registry.py (new registrations)
- env ALMA_DB_PATH + ALMA_CHAT_SESSION_FILE set by the host page wiring
- spawned by the claude CLI as '-m src.mcp.chat_mcp_server' — needs the PYTHONPATH anchor from claude_cli_bridge once the CLI runs from the neutral cwd

**macOS risks:**
- the MCP server spec's python command must be an absolute path (sys.executable) on macOS — a bare 'python' from a GUI-launched app is not guaranteed on PATH; that wiring lives in the chat page (other slice) but this file breaks silently (no tools) if it's wrong

**Verify:** `python -m pytest tests/test_claude_cli_bridge_mcp.py tests/test_chat_tools.py -x -q; in-app: Renn tools/list must show 86+ tools incl. help_search/kb_search`

### `src/data/chat_tools/registry.py`  (M)

_ensure_registered wires the new tool modules: 3 live-Drive handlers from enablement_tools; request_asana_task_update replaces the 3 retired un-gated Asana writes (impls kept for the TaskDetailPanel direct-click path); 7 artifact tools from NEW artifact_tools.py; 5 KB tools from NEW kb_tools.py; help_search from NEW help_tools.py. Descriptions for search_local_documents/query_business_drive rewritten for the local-vs-live split.

**Key details:** New registrations (name -> handler): search_google_drive->handle_search_google_drive, import_drive_doc->handle_import_drive_doc, search_everywhere->handle_search_everywhere, request_asana_task_update->handle_request_asana_task_update, generate_quiz/generate_doc/attach_artifact_to_draft/list_artifacts/generate_diagram/generate_deck/request_upload_artifact_to_drive->artifact_tools.handle_*, index_drive_folder/kb_search/kb_list_topics/kb_list_cards/kb_get_card->kb_tools.handle_*, help_search->help_tools.handle_help_search. All phi_level=0. NOTE: registry imports artifact_tools/kb_tools/help_tools INSIDE _ensure_registered — if any of those files is missing, EVERY tool dispatch fails, not just the new ones. The 4096-byte PHI cap on persisted result_json (registry.py:19) is the TRUNCATION_CAP turn_grounding relies on — do not change one without the other.

**Depends on:**
- src/data/chat_tools/artifact_tools.py
- src/data/chat_tools/kb_tools.py
- src/data/chat_tools/help_tools.py
- src/data/chat_tools/enablement_tools.py

**Verify:** `python -m pytest tests/test_chat_tools.py -x -q`

### `src/data/chat_tools/enablement_tools.py`  (M)

Adds the live-Drive trio (handle_search_google_drive with honest scope reporting + recursive active-folder default, handle_import_drive_doc, handle_search_everywhere with mirrored-file dedupe), the consolidated Confirm-gated request_asana_task_update propose path with per-action validation + drift anchors, _normalize_folder_entry healing legacy string entries in enablement.drive.active_folders (fixes the 2026-07-22 blank-pick crash and the '0 active folders' routing lie), a demo-mode gate on _push_guru_draft_impl (chat publish hit production Guru while demo was on), a KB distill hook after successful publish, list_tasks detail mode (task_id -> Asana custom fields/attachments/latest 3 comments + decoded brief), and card_template_block appended to revise-draft prompts.

**Key details:** handle_search_google_drive: scope resolution explicit folder_id > scope='all' > enablement.drive.active_folders (recursive); returns {'ok', 'scope': {kind: explicit_folder|all_visible|active_folders, folder_ids, recursive, note?, truncated?}, ...}; scope_truncated from drive_query flips scope honesty note. handle_search_everywhere: over-fetch pool=min(max(limit*4, limit+20), 400), dedupes against enablement_documents by doc_id OR source_ref, returns {local, local_count, google_drive, drive_count, drive_available, drive_error}; Drive failure degrades to local-only. RESOLVER_ACTION_TOOLS += {request_asana_task_update, request_upload_artifact_to_drive}. _request_asana_task_update_impl(conn, session_id, task_id, action, value): actions frozenset {complete, reopen, set_due, comment, add_subtask}; validates at PROPOSE time (ISO date regex ^\d{4}-\d{2}-\d{2}$ for set_due, comment<=2000 chars, subtask<=300, complete/reopen take no value); params carry expected_modified_at (task.remote_modified_at) and expected_due (for set_due field-level CAS); emits via _emit_confirm_write(conn, sid, 'asana_task_update', summary, params). _push_guru_draft_impl: demo_mode = bool(en_cfg.get('demo_mode', True)) — defaults SAFE to demo; live GuruClient built only when demo_mode False; on ok result calls kb.ingest.enqueue_distill(conn, did) fire-and-forget. _list_tasks_impl(..., task_id=None): detail mode uses asana_extras.get_extras + brief_json when brief_status=='ok'. _revise_draft_impl: style_block = store.style_guide_block(conn) + store.card_template_block(conn).

**Depends on:**
- src/data/drive_query.py build_live_drive_client + query_business_drive(live_client=, folder_id=, scope_truncated) (changed in this delta, other slice)
- src/data/enablement_store.py import_drive_doc / search_documents / card_template_block (changed, other slice)
- src/data/enablement_tasks.py get_task/update_task (remote_modified_at field — migration 044/045 era)
- src/data/asana_extras.py (NEW, other slice; migration 044)
- src/data/kb/ingest.py enqueue_distill (NEW, other slice; migrations 046/049)
- migrations/050_enablement_documents_fts.sql (tokenized local search)

**Settings keys:**
- enablement.drive.active_folders (list of {id,name,drive_id}; legacy plain-string ids tolerated + healed)
- enablement.demo_mode (default True = safe/demo)
- enablement.guru (publish target fallback, pre-existing)

**macOS risks:**
- none code-level; behavior risk: fresh Mac data/ without enablement.demo_mode reads as demo=True — Guru publishes will only mark-pushed locally until the operator sets demo_mode: false

**Verify:** `python -m pytest tests/test_drive_live_search_tools.py tests/test_asana_writeback.py tests/test_demo_guru_suppression.py -x -q`

### `src/data/chat_tools/fast_path.py`  (M)

Fixes date-scoped list_tickets silently returning ALL rows: new _coerce_date_filters normalizes date_range 'YYYY-MM-DD/YYYY-MM-DD' (either side optional), single date, or explicit date_start/date_end — from args or args['filters'] — into the date_start/date_end keys build_filter_query understands; handle_list_tickets now re-merges args['filters'] defensively and applies the coercion.

**Key details:** _coerce_date_filters(filters, args) mutates filters in place, tolerant of malformed input; keys scanned: date_start, date_end, date_range, date.

**Depends on:**
- src/data/chat_tools (build_filter_query, unchanged)

**Verify:** `python -m pytest tests/test_chat_tools.py -x -q`

### `src/data/chat_tools/artifact_tools.py`  (A)

NEW 526-line module: content-studio (WS3) chat tools — generate Mermaid diagrams, .pptx decks (tracked agent_jobs job with deterministic outline fallback), quizzes, one-pagers/battle-cards, list artifacts, attach an artifact to a Guru card draft (review-gated), and propose a Drive upload (Confirm-gated). Kept OUT of enablement_tools.py per the merge-hotspot rule.

**Key details:** Public handlers: handle_list_artifacts, handle_generate_diagram, handle_generate_deck, handle_generate_quiz, handle_generate_doc, handle_attach_artifact, handle_request_upload_artifact; helper steer_unknown_artifact. _PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent/'config'/'prompts'. Exactly-ONE-of source args ('source_task_id','source_research_id','source_doc_id','source_text'), validated in _load_source with steering errors; sources truncated to 8000 chars. Templates enablement_mermaid.txt / enablement_quiz.txt / enablement_one_pager.txt / enablement_battle_card.txt via .replace (NOT .format — mermaid %%{...}%% braces); inline fallbacks exist if files missing. generate_deck: agent_jobs.create_job(kind='deck_generation', steps=['load source','outline','export']), pptx_store.save_deck/export_pptx into artifact_store.artifact_dir(artifact_id)/slug.pptx, cancel checks at step boundaries. handle_request_upload_artifact target chain: explicit target_folder_id > enablement.kb.ec_folder_id > STEER (active_folders deliberately NOT a fallback — PHI-adjacent); summary carries folder ID never name; emits _emit_confirm_write(conn, sid, 'upload_artifact_to_drive', summary, {artifact_id, folder_id}). Session id from ALMA_CHAT_SESSION_FILE pointer file. attach: diagram->fenced mermaid md block; quiz->quiz_md + quiz_artifacts.to_guru_html; one_pager/battle_card->markdown; deck NOT attachable; records content_update.provenance proposal; sets artifact status='attached'.

**Depends on:**
- src/data/artifact_store.py (NEW, other slice; migration 047_enablement_artifacts.sql)
- src/data/llm_gen.py generate_validated (NEW)
- src/data/mermaid_lint.py (NEW)
- src/data/pptx_store.py parse_outline/outline_from_markdown/save_deck/export_pptx (changed)
- src/data/quiz_artifacts.py (NEW)
- src/data/agent_jobs.py (migration 036, pre-existing)
- src/data/research_store.py
- src/data/enablement_store.py get_document/get_draft/update_draft_content/save_card_draft
- config/prompts/enablement_{mermaid,quiz,one_pager,battle_card}.txt (all A in this delta)
- env ALMA_CHAT_SESSION_FILE

**Settings keys:**
- enablement.kb.ec_folder_id (upload target fallback)

**macOS risks:**
- python-pptx must be installed in the Mac venv (pptx_store dependency)
- paths built with pathlib throughout — no hardcoded separators

**Verify:** `python -m pytest tests/test_chat_tools.py tests/test_card_template.py -x -q; in-app: generate_deck from a task must produce a job in the sidebar and a .pptx under the artifact dir`

### `src/data/chat_tools/kb_tools.py`  (A)

NEW 182-line module: Drive knowledge-base (WS2) chat tools. index_drive_folder ENQUEUES only (kb_queue row + agent_jobs 'kb_index' job; the main-process KBWorker executes on its next tick — the MCP subprocess must never touch Drive/Qt); kb_search (hybrid ranked), kb_list_topics / kb_list_cards (complete enumeration), kb_get_card (full card, body capped 8000 chars, nearest-match steering on bad ids).

**Key details:** handle_kb_search/handle_kb_list_topics/handle_kb_list_cards/handle_kb_get_card/handle_index_drive_folder. _kb_precheck refuses with actionable messages when enablement.demo_mode (default True), not enablement.kb.enabled (default False), or no enablement.kb.ec_folder_id — prevents forever-stuck jobs. index_drive_folder: INSERT INTO kb_queue (kind='index_folder', target=folder_id, payload_json={topic}, job_id, created_at); unique-pending constraint violation -> 'already queued' + job closed. Raw SQL against kb_folders/kb_cards (role='topic', topic_folder_id join).

**Depends on:**
- migrations/046_kb.sql + migrations/049_kb_extra_fields.sql (kb_folders, kb_cards, kb_queue)
- src/data/kb/search.py kb_search + src/data/kb/store.py (NEW, other slice)
- src/data/agent_jobs.py
- env ALMA_CHAT_SESSION_FILE

**Settings keys:**
- enablement.demo_mode (default True blocks indexing)
- enablement.kb.enabled (default False)
- enablement.kb.ec_folder_id

**Verify:** `python -m pytest tests/test_kb_extra_and_readopt.py -x -q`

### `src/data/chat_tools/help_tools.py`  (A)

NEW 100-line module: handle_help_search — lexical FTS5 search over the bundled in-app Help Center corpus, self-syncing (sync_bundled_help loads when the DB is BEHIND the bundled files — the 8-of-62 fix), every result carrying an availability status so Renn states limitations before describing features; zero-hit returns the section list instead of a dead end.

**Key details:** _ensure_corpus(conn): help.loader.sync_bundled_help(conn) then store.count_articles>0; False when help_articles table missing (migration 048 not run) -> polite 'not available' note, never an exception. Result rows: article_id/title/section/status/summary/excerpt; status in {available, partial, flag-gated, not-available}; status_note added when any non-available result. limit clamped 1..20 (default 5).

**Depends on:**
- migrations/048_help_center.sql (help_articles + FTS)
- src/data/help/{__init__,loader,search,store}.py (NEW, other slice)
- bundled help corpus files that loader syncs from (help content workstream)

**macOS risks:**
- corpus file discovery in help/loader is another slice — confirm its bundled-file path is Path-based, else help_search silently returns 'not available' on Mac

**Verify:** `python -m pytest tests/test_help_search.py tests/test_help_store.py tests/test_help_corpus_sync.py -x -q`

### `src/data/chat_tools/tool_prompts.py`  (M)

Legacy text-loop prompt addendum: list_tickets docs now advertise date filters (filters.date_start/date_end + top-level date_range 'YYYY-MM-DD/YYYY-MM-DD' with example), and a new Rule 8 GROUNDING clause — only state ticket IDs/flag IDs/counts/dates that appear in a TOOL_RESULT, never invent IDs, say plainly when a query returns no rows.

**Key details:** Rule 8 text is the prompt-side companion of the chat_engine anti-fabrication guard; TOOL_PROMPT_ADDENDUM is appended to system when tools_enabled and not use_mcp.

**Depends on:**
- src/data/chat_tools/fast_path.py (the date_range coercion this documents)

**Verify:** `python -m pytest tests/test_chat_tools.py -x -q`

### `src/services/chat_engine.py`  (M)

The anti-fabrication core (6100625). Layer 1: detects a tool call WRITTEN AS TEXT (Anthropic XML invoke, function_calls/function_call/tool_use/tool_call blocks, ```tool_call/tool_code fences, line-anchored bare TOOL_CALL:) after blanking quoted spans; discards the turn with a notice UNLESS tools genuinely ran (ledger CONFIRMED_RAN or positive telemetry) or the previous turn was already discarded (livelock guard) — then it prepends a warning banner instead. Layer 2 (TEL-SG): captures a per-turn evidence corpus (prompt+system+tool results) + a send-time chat_tool_executions watermark, and post-turn runs turn_grounding.assess — warn-only, telemetry-mode by default. Also: usage-sink attachment for Renn metering, suppress-once state, history_restored tracking.

**Key details:** New imports: html, src.services.turn_grounding. Regexes: _FABRICATED_INVOKE_RE (bounded [^>]{0,400} — load-bearing anti-quadratic), _BLOCK_OPEN_RES/_BLOCK_CLOSE_RES over _BLOCK_TAGS, _TOOL_FENCE_LANGS, _BARE_TOOL_CALL_RE, _INLINE_CODE_RE, _TOOL_NAME_RE; helpers _strip_quoted_spans (single linear pass), _has_closed_call_block, _detect_unexecuted_tool_call(text, mcp_mode) -> tool-name|''|None. Notices: _UNEXECUTED_TOOL_CALL_NOTICE ('This turn was discarded...') / _UNEXECUTED_TOOL_CALL_BANNER. _live_tool_names() memoizes chat_mcp_server.TOOL_SCHEMAS names; unreadable registry -> ('__registry_unreadable__background__',) which DISABLES the Tier-A job rung. Engine state: _last_turn_fabricated, _turn_evidence, _history_restored (set_history sets True; clear_history resets — load-old-chat->new-chat must re-arm Tier A-write). send(): _capture_turn_evidence(prompt, system) — captures PROMPT not ctx (survives one-shot [TODAY'S PLAN]); surface_ok = bool(_use_mcp_tools and _db_path); watermark snapshot NOT gated on the mode key (layer 1 consumes it too; ~14 ms read-only open). _on_worker_finished: telemetry dict-normalized once; grounding_opts = turn_grounding.load_options(); _resolve_turn_tools -> resolve_tool_evidence(db_path, mark, telemetry, use_mcp); keep_text = ledger CONFIRMED_RAN or tools_ran>0 or _last_turn_fabricated; telemetry['unexecuted_tool_call']=True on layer-1 hits; layer 2 skipped when discarded; verdict.as_telemetry() -> telemetry['turn_grounding']; banner only in mode=banner and suppressed if layer-1 banner already shown (banner_suppressed:'layer1'); S0/off abstains not decorated. Fabricated calls do NOT feed the degraded-recycle streak. Legacy text tool loop folds result into _turn_evidence.add('tool_result', result) — evidence object NOT re-created. _attach_usage_sink(client): only when _db_path set AND _task_type startswith 'enablement' AND client has set_usage_sink; wires RennUsageRecorder(self._db_path) with RENN_CHAT_SOURCE; called from set_client and the factory path.

**Depends on:**
- src/services/turn_grounding.py (top-level import — must exist or chat_engine fails to import AT ALL)
- src/data/renn_usage.py (lazy import, best-effort)
- src/mcp/chat_mcp_server.py TOOL_SCHEMAS (lazy, degrades)
- chat_tool_executions table (migration 009, pre-existing)

**Settings keys:**
- enablement.turn_grounding.mode — off|telemetry|banner, default telemetry, unknown value -> off, unreadable settings -> off
- enablement.turn_grounding.tier_c_enabled — default false (Tier C ships OFF)

**macOS risks:**
- none — pure Python/Qt-signal logic

**Verify:** `python -m pytest tests/test_chat_engine.py tests/test_turn_grounding.py -x -q (run together; grouped under 4 files)`

### `src/services/turn_grounding.py`  (A)

NEW 1396-line PURE module (no Qt/LLM/engine imports): Turn Evidence Ledger + Subject Gate. Judges a completed turn by comparing extracted atoms (dates, identifiers, URLs, Title-Case names) of the response against the captured corpus of every byte that legitimately entered the turn, AND-gated by a subject allowlist. Ordered gates S0 surface / S1 tool evidence / S2 truncation / S4 retraction guard / S5 Tier A structural impossibility (uncofirmed writes, fake background jobs) / S3 zero-atom fast exit / S6 Tier B atom grounding / S7 Tier C fabricated negatives (shipped OFF). Fail-open everywhere: any error -> UNKNOWN/abstain; never raises, never blocks; warn-only by construction (no 'block' mode exists on purpose).

**Key details:** Public surface: constants CONFIRMED_RAN/CONFIRMED_EMPTY/UNKNOWN, STATE_CLEAN/STATE_UNKNOWN/STATE_FLAG, MODE_OFF/MODE_TELEMETRY/MODE_BANNER, TIER_A/B/C, TABLE='chat_tool_executions', TRUNCATION_CAP=4096 (mirrors registry.py:19 PHI cap), MIN_UNGROUNDED=3, MIN_UNGROUNDED_RATIO=0.6, MAX_ATOMS=40, CORPUS_CAP=256KiB, RESPONSE_CAP=32KiB, CONFIRM_MARKER='[SYSTEM: operator confirmed', PLAN_MARKER="[TODAY'S PLAN]", BANNER_TIER_A_WRITE/_A_JOB/_B/_C. Dataclasses: LedgerMark(max_rowid, count, anchor_id — anchor pins the execution_id at the watermark against chat_session.delete_session's delete-N-insert-N rowid reuse), ToolEvidence(state, payloads, tool_names, rows, truncated, payloads_complete, detail), TurnEvidence(prompt, system, tool_payloads, mark, surface_ok, tool_state, history_restored, tool_names; methods add(kind,text)/apply_tool_evidence/corpus_text), GroundingOptions(mode, tier_c_enabled, min_ungrounded, min_ratio), GroundingVerdict(state, gate, tier, reason '<tier>:<subject>:<detail>', action, banner, atom_count, ungrounded_count, detail; .flagged; .as_telemetry() — ungrounded_hashes only on FLAG, salted per-process os.urandom(16) digests). Functions: load_options() reads enablement.turn_grounding (FAILS TO off on settings errors), snapshot_tool_ledger(db_path) -> LedgerMark|None (fresh get_connection(readonly=True) per probe, never cached/never in a txn), resolve_tool_evidence(db_path, mark, telemetry=None, use_mcp=True) -> ToolEvidence (rowid-window WITH NO session filter — 48% of live rows are session_id='adhoc_probe'; count/max/anchor mismatch -> UNKNOWN; rows -> CONFIRMED_RAN + payloads join corpus; telemetry positive-only corroborator), assess(response, evidence, history_text='', registry_names=None, opts=None) -> GroundingVerdict (never raises), extract_atoms, is_grounded, CorpusIndex. PRIVACY: corpus can contain PHI — never logged, never in telemetry, never sent to an LLM.

**Depends on:**
- src/data/connection_factory.get_connection (readonly)
- src/data/settings_manager.get_section
- chat_tool_executions table (migration 009)

**Settings keys:**
- enablement.turn_grounding.mode (default telemetry)
- enablement.turn_grounding.tier_c_enabled (default false)

**macOS risks:**
- none — stdlib only (hashlib/os/re/unicodedata/dataclasses)

**Verify:** `python -m pytest tests/test_turn_grounding.py -x -q (pins the deliberate misses too)`

### `src/services/chat_session.py`  (M)

New resolve_or_create_session(source_page, conn, *, pointer_dir=None): the Agent page and the Workbench assistant panel are the same assistant and now SHARE one transcript — first surface creates the session and writes the .current_chat_session pointer file; the other reads the pointer and reuses it (fixes the two-divergent-histories last-writer-wins fight, finding 5). Plus _session_exists helper.

**Key details:** pointer = Path(pointer_dir)/'.current_chat_session'; read/validated against chat_sessions table; any pointer error -> create fresh (legacy behavior when pointer_dir omitted). Written UTF-8.

**Depends on:**
- chat_sessions table (pre-existing)

**macOS risks:**
- pointer file lives NEXT TO the DB (data/ dir) — runtime state, not settings; safe re the don't-touch-local-settings constraint

**Verify:** `python -m pytest tests/test_shared_session.py -x -q`

### `src/services/agent_chat.py`  (M)

Gated-write dispatch refactored to a module-level registry (WRITE_HANDLERS op->fn(params, ctx) + PRE_DISPATCH_CHECKS op->main-thread check) with two NEW ops: asana_task_update (WS1-M6; CAS drift protection — complete/reopen compare live modified_at vs propose-time snapshot, set_due compares the due FIELD itself, comment/add_subtask append unconditionally; anchor re-stamped after a passing check) and upload_artifact_to_drive (WS3-M7; throttled GoogleDriveExporter, explicit MIME map, provenance drive_file_id + status='published'). execute_write reordered: payload read BEFORE the single-winner claim so a pre-check can block WITHOUT burning the confirm row (needs_google_connect pattern, kept_open:True). WriteWorker gains ctx={'db_path'}; _write_worker single attr replaced by a _write_workers SET (second Confirm mid-write must not GC a running QThread). DriveListWorker now calls reader.list_picker_roots() for roots (Shared Drives + sharedWithMe folders — the only roots a service account has) and emits a 'shared' flag; _google_is_active now uses google_access.google_access_ready() (auth_type-aware; SA installs were permanently 'needs_connect'). Session init uses resolve_or_create_session with pointer_dir=db parent.

**Key details:** WRITE_HANDLERS = {create_guru_folder, rename_guru_folder, create_asana_task, asana_task_update: _write_asana_task_update, upload_artifact_to_drive: _write_upload_artifact}. PRE_DISPATCH_CHECKS = {upload_artifact_to_drive: _precheck_upload_artifact} (returns dict to block; only when auth_type=='oauth_user' and not google_oauth.is_active(); kicks controller.start_google_connect()). _write_asana_task_update: enablement_tasks.get_task -> source_ref gid; AsanaClient.from_store(); conflict returns {'ok':False,'conflict':True,'op_action',...}; dispatches to asana_writeback.set_completed_in_asana / update_due_in_asana / post_comment_to_asana / create_subtask_in_asana(created_by='agent'). _write_upload_artifact: artifact_store.get_artifact, GoogleDriveExporter.from_settings(folder_id).upload_file(name, bytes, mime, folder_id); explicit MIME map {.pptx, .svg, .png, .md, .pdf} FIRST because Windows mimetypes reads the registry (mac-friendly already). _prune_write_workers keeps only isRunning(). resolve_drive_folder now logs persist failures (2026-07-22 silent-failure forensics).

**Depends on:**
- src/data/asana_writeback.py new/extended functions (changed, other slice)
- src/data/enablement_tasks.py
- src/data/artifact_store.py
- src/export/gdrive_export.py GoogleDriveExporter.from_settings/upload_file
- src/data/google_access.py google_access_ready (NEW, other slice)
- src/data/drive_reader.py list_picker_roots (changed, other slice)
- src/services/chat_session.py resolve_or_create_session
- src/data/asana_client.py get_task(opt_fields=...)

**Settings keys:**
- enablement.drive.auth_type (default 'service_account'; 'oauth_user' triggers the reconnect pre-check)

**macOS risks:**
- none code-level; mimetypes nondeterminism already neutralized

**Verify:** `python -m pytest tests/test_task_detail_actions.py tests/test_google_access_gate.py tests/test_asana_writeback.py -x -q; in-app: Confirm card for an Asana update must survive a 'Google not connected' block with the card still open`

---

### Slice-level notes — chat-llm-agents

**Hard ordering constraints (matter only for piecemeal moves; a full checkout satisfies them all):**
- turn_grounding.py MUST land before (or with) chat_engine.py — chat_engine has a TOP-LEVEL 'from src.services import turn_grounding'; without it the whole chat engine fails to import and both chat pages die at construction.
- artifact_tools.py, kb_tools.py, help_tools.py MUST land before (or with) registry.py — _ensure_registered imports them; a missing module breaks EVERY chat tool dispatch, not just the new tools.
- Data-layer siblings (other slices) before the chat_tools files that call them: drive_query.py + drive_reader.py + enablement_store.py + google_access.py (Drive trio, picker, google_access_ready), artifact_store.py + llm_gen.py + mermaid_lint.py + pptx_store.py + quiz_artifacts.py (artifact tools), kb/ package (kb tools + enqueue_distill), help/ package (help_search), asana_extras.py + asana_writeback.py + enablement_tasks.py (task detail + gated writes), renn_usage.py (usage sink).
- Migrations before first run of the ported code: 044 (asana_task_extras -> list_tasks detail mode), 045 (task_brief), 046+049 (kb_folders/kb_cards/kb_queue), 047 (enablement_artifacts), 048 (help_articles), 050 (enablement_documents FTS — search_local_documents/search_everywhere ranking). chat_tool_executions already exists (migration 009). Migrations are applied by the app's schema_migrator on launch — verify with a SELECT against each table.
- claude_cli_subprocess.py (cwd kwarg) before claude_cli_bridge.py (passes cwd=); claude_cli_bridge.py (set_system_prompt/set_usage_sink) before claude_cli_client.py (calls both).
- usage_tracker.py's log_call(cost_usd=...) signature change (other slice, 9 lines) MUST land with claude_cli_bridge.py — the bridge now passes cost_usd to whatever sink is attached, including the scan path's UsageTracker; without it every scan usage write throws and is swallowed to a debug log (usage silently stops recording).
- config/prompts/enablement_{mermaid,quiz,one_pager,battle_card,task_brief}.txt before exercising the generate_* tools (inline fallbacks exist but are minimal).
- The Workbench-panel side of session sharing (page.py wiring, other slice) should land in the same pass as chat_session.py/agent_chat.py or sharing stays one-way.

**Cross-file risks / silent failures:**
- SILENT: bridge passes cost_usd to _usage_tracker.log_call inside try/except that only debug-logs — if UsageTracker's signature was not updated (other slice's usage_tracker.py), scans keep working but ALL usage accounting silently stops. Check gemini_usage rows appear after the first scan/chat turn on the Mac.
- SILENT: Renn metering (_attach_usage_sink) is best-effort by design — if renn_usage.py is missing or the task_type isn't 'enablement*', no error, just no rows. Verify with: SELECT COUNT(*) FROM gemini_usage WHERE source='renn_chat' after one Renn turn.
- SILENT: turn_grounding defaults to mode=telemetry — active immediately on the MCP surface. It fails open everywhere, but expect telemetry['turn_grounding'] keys in chat_messages telemetry; absence after a Renn turn means the capture path is broken (not just 'off').
- LOOKS DONE BUT ISN'T: demo_mode defaults True when absent — on a fresh Mac data/ the KB tools refuse and Guru publishes only mark-pushed locally. That is CORRECT fail-safe behavior, but a tester will report 'publish doesn't work' unless told to set enablement.demo_mode: false locally.
- The persona fix is three legs (system-prompt-file + neutral cwd + PYTHONPATH); porting only the file/flag leg reintroduces the CLAUDE.md identity-bleed (~14.6K tokens/turn) because the Mac repo checkout also has CLAUDE.md above the default cwd. All three must ship together.
- ClaudeCliClient._prepare_prompt changed return type (str -> tuple). It is private and both in-file callers were updated, but any out-of-slice caller (tests, report bridge shims) that grabbed it would now get a tuple — grep before declaring done.
- chat_engine's layer-1 discard depends on the chat_tool_executions ledger for its keep-text escape hatch; if ALMA_DB_PATH is mis-set on the Mac the ledger reads UNKNOWN and a genuinely tool-backed turn that also narrates a call gets discarded (deterministic retry banner saves the second attempt — but it will look flaky).
- Retired Asana tools (create_asana_subtask/post_asana_comment/update_asana_due_date): impls intentionally REMAIN in enablement_tools.py for the TaskDetailPanel direct-click path — do not 'clean up' the orphan-looking _*_impl functions.
- resolve_tool_evidence deliberately has NO session filter (adhoc_probe rows) and TRUNCATION_CAP mirrors registry.py:19's 4096 PHI cap — changing either side alone breaks the grounding contract.

**macOS-specific:**
- claude CLI discovery: only 'claude', 'claude.cmd', 'claude.exe' are probed via shutil.which; on a GUI-launched macOS app PATH usually lacks /opt/homebrew/bin and npm-global bins. Either ensure the launch context inherits a full PATH or have the operator set claude.cli_path (absolute path from `which claude` in a terminal) in the Mac's LOCAL data/settings.yaml — never commit it (data/ is gitignored and must not be touched by the port).
- Verify the Mac's claude CLI version supports --system-prompt-file, --strict-mcp-config, --tools '', --no-session-persistence (behavior was live-verified on 2.1.216); an older CLI silently ignores/errors on flags and the persona regresses.
- Neutral cwd becomes $TMPDIR/alma_cli_neutral (per-user /var/folders/...) — fine, but confirm the MCP server subprocess still resolves 'src.mcp.chat_mcp_server' there: the PYTHONPATH anchor is set in the server spec env; the python COMMAND in the spec (wired by the chat page, other slice) must be an absolute sys.executable on macOS.
- Zombie cleanup for tests: replace the Windows 'wmic ... alma_mcp_server ... terminate' / taskkill with pkill -f alma_mcp_server; pkill -f chat_mcp_server; pkill -f 'claude .*-p' equivalents before E2E runs.
- python-pptx must be in the arm64 Mac venv for generate_deck (pptx_store).
- No hardcoded backslash paths, no cp1252 assumptions, and the one Windows-registry hazard (mimetypes for .pptx) is already neutralized with an explicit MIME map in agent_chat._write_upload_artifact — no action needed.
- Ten help-claims test files have hyphens in their names (e.g. tests/test_help_claims_getting-started.py) — fine for pytest file collection, but they cannot be imported as modules; run them by path.

**Analyst notes / answered questions:**
- Renn persona mechanism (answered): persona text -> tempfile.mkstemp('alma_sysprompt_*.txt') in the OS temp dir, UTF-8, reused per bridge, unlinked on shutdown; delivered via '--system-prompt-file <path>'; subprocess cwd = <tempdir>/alma_cli_neutral (None->inherit on OSError); PYTHONPATH = app root (Path(__file__).resolve().parents[2]) setdefault'ed into each MCP server's env. Nothing assumes Windows paths — all pathlib/tempfile; the only Windows-specific bits are correctly guarded (CREATE_NO_WINDOW) or commentary (32,767-char argv cap, %TEMP% cleaners).
- Anti-fabrication guardrails (answered): Layer 1 in chat_engine.py — regex detection of tool-calls-as-text with discard/banner logic, no settings key, always on, telemetry key 'unexecuted_tool_call'. Layer 2 = turn_grounding.py TEL-SG — settings enablement.turn_grounding.mode (off|telemetry|banner, DEFAULT telemetry, fails to off on settings errors) + enablement.turn_grounding.tier_c_enabled (default false); hook points: ChatEngine.send() -> _capture_turn_evidence + snapshot_tool_ledger(chat_tool_executions watermark: MAX(rowid)+COUNT(*)+anchor execution_id); _on_worker_finished -> resolve_tool_evidence -> assess() -> telemetry['turn_grounding'] + optional banner. Warn-only; there is deliberately no block mode.
- Redaction fail-closed (answered): YES, in claude_client.py — new RedactionError; _redact_text raises instead of returning raw text on pipeline failure; tests/test_redaction_fail_closed.py covers it. (The broader redaction_engine.py workstream remains deliberately uncommitted per owner and is NOT in this delta.)
- Tools added (answered): search_google_drive / import_drive_doc / search_everywhere + request_asana_task_update (enablement_tools.py); generate_diagram / generate_deck / generate_quiz / generate_doc / list_artifacts / attach_artifact_to_draft / request_upload_artifact_to_drive (artifact_tools.py); kb_search / kb_list_topics / kb_list_cards / kb_get_card / index_drive_folder (kb_tools.py); help_search (help_tools.py). Retired from the model surface: create_asana_subtask, post_asana_comment, update_asana_due_date (MCP returns a steering error via _RETIRED_TOOL_HINTS; impls kept for the UI direct path). Schemas mirrored in both claude_tools.TOOL_DEFINITIONS and chat_mcp_server.TOOL_SCHEMAS.
- OPEN: does the Mac chat-page wiring (other slice) pass sys.executable as the MCP server 'command'? The PYTHONPATH anchor here assumes only module RESOLUTION was cwd-dependent, not the interpreter path.
- OPEN: is the Workbench panel's session bootstrap (page.py, other slice) also switched to resolve_or_create_session? agent_chat.py's side alone gives one-way sharing.
- OPEN: gitignored live-WebEngine tests (tests/test_*_web_local.py) are referenced by memory but not in this delta — confirm the Mac plan doesn't expect them from git.


## 4. Web layer — React/QtWebEngine, controllers, bridges, diagnostics

### `src/ui/web/web_host.py`  (A)

Shared QWebEngineView host used by every web surface (Agent chat, Calendar, Workbench, Home). Owns bundle resolution, QWebChannel registration (multi-object via extra_bridges), diagnostics (JS console->Python log, renderProcessTerminated->web_diag dump), optional Qt-side drag-drop forwarding, and in-process DevTools.

**Key details:** class WebHost(QWidget): __init__(bridge=None, channel_name='almaBridge', route='', accept_drops=False, log_name='alma.web', extra_bridges=None, parent=None); Signal dropped(str); methods load(), on_page_shown(); module constants _HERE, _DIST=os.path.join(_HERE,'dist','index.html'), _SPIKE=os.path.join(_HERE,'static','index.html'). load() picks _DIST if exists else _SPIKE, QUrl.fromLocalFile + setFragment(route). CRITICAL ownership invariant at lines 137-153: for every registered object, `if obj.parent() is None: obj.setParent(self)` BEFORE channel.registerObject — QWebChannel does NOT own registered objects; a parentless bridge is GC'd and the channel dereferences freed memory (native access violation). Do not remove. LoggingPage(QWebEnginePage) routes console.* into Python log; RequestLogger(QWebEngineUrlRequestInterceptor) only under ALMA_AGENT_DEVTOOLS=1. _startup_diag logs web_diag report once per process under ALMA_WEB_DIAG=1. renderProcessTerminated handler calls web_diag.log_report. Sets QWebEngineSettings.LocalContentCanAccessFileUrls=True so file:// page loads sibling qwebchannel.js.

**Depends on:**
- src/ui/web/dist/index.html (committed bundle; else static/index.html spike)
- src/ui/web/web_diag.py (soft import, guarded)
- PySide6 full install incl. QtWebEngineCore/QtWebEngineWidgets (NOT Essentials-only)

**macOS risks:**
- Requires arm64-native Python + PySide6 with QtWebEngine; Rosetta/x86_64 python kills the renderer (QTBUG-98487) — web_diag probes this
- Env vars honored: ALMA_WEB_DIAG, ALMA_AGENT_DEVTOOLS, QT_QPA_PLATFORM (offscreen => grabs always blank; verify via runJavaScript never screenshots)

**Verify:** `python -m pytest tests/test_web_guardrails.py tests/test_web_diag.py -x -q; then python scripts/web_diag.py (expect 0 FAIL)`

### `src/ui/web/web_flags.py`  (A)

Qt-free rollout-flag reader for the web surfaces. Both flags fail closed to the native Qt pages on ANY settings error or unrecognized value.

**Key details:** web_tabs_mode() -> 'off'|'calendar'|'all': reads settings section 'enablement' key 'web_tabs' via settings_manager.get_section('enablement',{}); default and error path -> 'off'. web_home_enabled() -> bool: reads section 'ui' key 'web_home'; default False; accepts bool, numeric 1, strings in ('on','true','yes','1'); everything else / any exception -> False. VALID_MODES=('off','calendar','all'). Home deliberately on its own flag because it is the boot page in both modes (app_modes._FIRST_PAGE).

**Depends on:**
- src/data/settings_manager.get_section (imported lazily inside functions, so import never fails)

**Settings keys:**
- enablement.web_tabs (default 'off'; 'off'|'calendar'|'all')
- ui.web_home (default False; truthy strings on/true/yes/1)

**macOS risks:**
- None — pure python. NOTE: both flags default off, so after the port the Mac renders native pages unless its gitignored data/settings.yaml (must NOT be touched) already enables them

**Verify:** `python -m pytest tests/test_enablement_web_flag.py -x -q`

### `src/ui/web/web_diag.py`  (A)

OS-aware blank-page diagnostics library: deterministic probes for every known blank-render cause (Rosetta page-size fingerprint, python-vs-QtWebEngineProcess Mach-O arch mismatch, quarantine xattrs, _CodeSignature re-sign leftovers, missing bundle, env overrides, Windows helper exe). Pure stdlib, Qt-free.

**Key details:** Public: run_diagnostics()->dict{platform,checks,summary}, format_report(diag)->str (ASCII-only for cp1252 consoles), log_report(logger) (never raises), macho_arches(path)->set, probes: probe_platform (mmap.PAGESIZE: arm64 mac must be 16384, 4096=Rosetta), probe_qt, probe_bundle (dist/index.html size>50k, dist/qwebchannel.js sibling), probe_env (QT_QPA_PLATFORM, QTWEBENGINE_DISABLE_SANDBOX, QTWEBENGINE_CHROMIUM_FLAGS, ALMA_AGENT_DEVTOOLS, ALMA_WEB_DIAG), probe_macos (sysctl sysctl.proc_translated / hw.optional.arm64, walks PySide6 for QtWebEngineProcess, xattr for com.apple.quarantine), probe_windows (QtWebEngineProcess.exe). Constants OK/WARN/FAIL.

**Depends on:**
- src/ui/web/dist/index.html + dist/qwebchannel.js for the bundle probe to pass
- macOS branch shells out to `sysctl -n` and `xattr` (present on every Mac)

**macOS risks:**
- This file is the mac-portability tool itself — its macOS branch is exactly what the M1 needs; nothing Windows-only leaks (probe_windows only runs on win32)

**Verify:** `python -m pytest tests/test_web_diag.py -x -q; python scripts/web_diag.py on the Mac should show rosetta=ok, helper_arch=ok, bundle=ok`

### `src/ui/web/agent_page.py`  (M)

Refactor: AgentPage delegates all view/channel/diagnostic plumbing to the shared WebHost; keeps only ChatBridge wiring. Re-exports _DIST/_SPIKE from web_host for test back-compat; exposes self.view=self.host.view and self._page=self.host._page.

**Key details:** AgentPage builds ChatBridge(parent=self) then WebHost(bridge=self.bridge, channel_name='almaBridge', route='', log_name='alma.agent.web', parent=self). Chat is the SPA default (no hash fragment). load() forwards to host.load(). Import line: `from .web_host import _DIST, _SPIKE, WebHost  # noqa: F401`.

**Depends on:**
- src/ui/web/web_host.py (must land first)
- src/ui/web/chat_bridge.py

**macOS risks:**
- None beyond WebHost's

**Verify:** `python -m pytest tests/test_web_guardrails.py -x -q; in-app Agent page renders chat`

### `src/ui/web/chat_bridge.py`  (M)

Adds host-pushed chat notices for embedded drawers (M5.5): new Python->JS signal chatNotice and a plain (deliberately NOT @Slot) push_notice method so page scripts cannot forge notices.

**Key details:** New Signal chatNotice = Signal(str)  # JSON {role:'u'|'a', text}. New method push_notice(self, role, text): emits json.dumps({'role': 'u' if role=='u' else 'a', 'text': str(text or '')}); swallows all exceptions. Called from enablement page.py's add_message flow (other slice) to mirror scan/publish results into web ChatDrawers.

**Depends on:**
- Callers live in src/ui/pages/enablement/page.py (other slice) — port chat_bridge.py before/with it or push_notice calls AttributeError

**macOS risks:**
- None

**Verify:** `python -m pytest tests/test_web_chat_drawer.py -x -q (MUST run singly — offscreen Chromium teardown stacks to exit 255 across files)`

### `src/ui/web/calendar_bridge.py`  (A)

Pure-relay QWebChannel bridge for the web Calendar tab, registered as 'calendarBridge'. All signals/callables injected from CalendarWebController; every slot swallows exceptions and carries no authority.

**Key details:** class CalendarBridge(QObject): Signals calendarData(str), briefReady(str), rescheduleResolved(str). __init__(data_signal, brief_signal, refresh_fn, scope_fn, open_fn, brief_fn, reschedule_fn, resolved_signal, parent). Slots: refresh(), setScope(str), openTask(str), requestBrief(str), requestReschedule(str,str), ping()->'pong'.

**Depends on:**
- src/services/enablement_web.CalendarWebController supplies the injected callables (wired in src/ui/pages/enablement/page.py::_make_calendar)
- src/ui/web/web_host.py parents it on the channel

**macOS risks:**
- None

**Verify:** `python -m pytest tests/test_calendar_bridge.py -x -q`

### `src/ui/web/workbench_bridge.py`  (A)

Pure-relay QWebChannel bridge for the web Workbench tab, registered as 'workbenchBridge'. Signals for data/draft/diff/preview/cards/publish/AI-edit; slots only ASK the controller (native dialogs and confirms stay Python-side).

**Key details:** class WorkbenchBridge(QObject): Signals workbenchData, draftLoaded, diffReady, previewUpdated, existingCards, publishResolved, aiEditResolved (all str/JSON). __init__ takes 7 *_signal + 12 *_fn injections. Slots: refresh(), switchWorkspace(str), closeWorkspace(str), requestDiff(), uploadRequested(), findTask(), openChat(), contentEdited(str,str), aiEdit(str,str), requestPublish(str), requestImport(str), listExistingCards(), ping().

**Depends on:**
- src/services/enablement_web.WorkbenchWebController (wired in page.py::_make_workbench, which also puts the shared almaBridge chat drawer on the same channel via WebHost extra_bridges)

**macOS risks:**
- None

**Verify:** `python -m pytest tests/test_workbench_bridge.py -x -q`

### `src/ui/web/home_bridge.py`  (A)

Pure-relay QWebChannel bridge for the web Home page, registered as 'homeBridge'. requestModeSwitch is the one authority-adjacent slot; it only forwards to the controller's fully-gated path.

**Key details:** class HomeBridge(QObject): Signal homeData(str). __init__(data_signal, refresh_fn, quick_action_fn, activity_fn, mode_switch_fn, parent). Slots: refresh(), quickAction(str), activityActivated(str), requestModeSwitch(str), ping()->'pong'.

**Depends on:**
- src/services/home_web.HomeWebController (wired in src/ui/main_window.py::_create_home_page, other slice)

**macOS risks:**
- None

**Verify:** `python -m pytest tests/test_home_bridge.py -x -q`

### `src/services/enablement_web.py`  (A)

The two Python controllers behind the enablement web tabs. CalendarWebController mirrors the Qt CalendarPage surface (set_tasks/set_scope + signals) and gates drag-reschedule with validate -> single-winner inflight claim -> NATIVE confirm -> dispatch. WorkbenchWebController mirrors WorkbenchPage (workspaces, sanitized preview, word diff, edits, AI-edit, publish) with the publish belt comparing the operator-seen cache against the actual DB bytes.

**Key details:** CalendarWebController(QObject): signals calendar_data, brief_ready, scope_changed, event_activated(dict), event_clicked, day_expanded, reschedule_resolved, reschedule_dispatched(str,str); ctor(brief_lookup, today_fn, confirm_fn, write_fn); methods set_tasks, set_scope, request_refresh, request_scope, open_task, request_reschedule (strict ^\d{4}-\d{2}-\d{2}$ full-string date check, guru_card_due chips refused, fails closed without confirm_fn), request_brief. _VALID_SCOPES=('mine','all'), _KINDS=('drive','guru','asana','high','done','normal'), _DESCRIPTION_CAP=240; guru card ids become 'guru:<card_id>'. WorkbenchWebController(QObject): MAX_WORKSPACES=4; signals workbench_data, draft_loaded, diff_ready, preview_updated, existing_cards_data, publish_resolved, ai_edit_resolved + WorkbenchPage-parity signals (draft_selected(int), workspace_closed(int), find_task_requested, publish_requested(str), load_file_requested(str), open_chat_requested, existing_cards_requested, import_requested(str), content_edited(int,str), ai_edit_requested(str,str), upload_requested); ctor(checks_fn, md_to_html_fn, publish_confirm_fn, content_lookup, busy_lookup); _PUBLISH_STATIC=('guru_new','drive_new','drive_update') + 'guru_existing:<key>' only for host-offered keys; _AI_INSTRUCTION_CAP=2000, _AI_SELECTION_CAP=8000; js_* entry points frozen while _publish_inflight; publish refused while _ai_edit_inflight or busy_lookup() truthy; _preview_html ALWAYS passes src.data.html_sanitize.sanitize_html; js_request_diff uses src.data.text_diff.diff_words/change_count; _render_markdown falls back to src.data.html_markdown.markdown_to_html then escaped <pre>. page.py reads controller._current_drafts and .active_draft_id directly.

**Depends on:**
- src/data/html_sanitize.py (sanitize_html)
- src/data/text_diff.py (diff_words, change_count — verify it exists in the data slice)
- src/data/html_markdown.py (markdown_to_html)
- src/ui/pages/enablement/page.py wiring (_make_calendar/_make_workbench, other slice)

**macOS risks:**
- None — pure Qt-core Python; no paths/subprocess

**Verify:** `python -m pytest tests/test_calendar_bridge.py tests/test_workbench_bridge.py tests/test_web_guardrails.py -x -q`

### `src/services/home_web.py`  (A)

HomeWebController — Python half of the #/home route. Duplicates HomePage's SQL + presentation constants on purpose (src/services must not import src/ui); builds the entire viewmodel (greeting, stats, activity, CCC banner) so web and Qt render byte-identical. js_request_mode_switch is the one authority-bearing slot: allowlist -> reject same-mode -> single-winner claim -> native confirm (fail-closed if confirm_fn absent) -> DEFERRED dispatch via QTimer.singleShot(0,...).

**Key details:** HomeWebController(QObject): signals home_data(str), mode_selected(str), quick_action(str), activity_activated(str); ctor(db, current_mode, confirm_fn, defer_fn, now_fn, parent); methods set_mode, refresh, request_refresh, js_quick_action, js_activity_activated, js_request_mode_switch, _dispatch. SQL (each individually guarded, missing table -> 0): enablement stats over guru_content_drafts(status='pending'), enablement_tasks(status NOT IN ('done','dismissed')), pptx_decks; product stats over tickets, analysis_reports, chat_sessions; activity queries over chat_sessions, analysis_reports, enablement_tasks. Activity sort is a STRING compare preserved bug-for-bug from home_page.py:375 — do not fix during the port. Imports src.branding (CONTENT_COMMAND_CENTER, CONTENT_COMMAND_CENTER_DESCRIPTION) and src.ui.app_modes (MODE_PRODUCT/MODE_ENABLEMENT/MODES). Constants: _TITLE_CAP=110, ts_display = ts[:16].replace('T','  '), _ACCENT '#0D7D72'.

**Depends on:**
- src/branding.py (NEW in this delta — branding slice; must land first or import fails)
- src/ui/app_modes.py
- src/ui/main_window.py wiring incl. _web_home_mode_confirm + _arm_home_watchdog (other slice)
- DB tables above (all soft — errors degrade to 0/skip)

**Settings keys:**
- app.last_mode (persisted by MainWindow.switch_mode when mode_selected fires — not read here)

**macOS risks:**
- None

**Verify:** `python -m pytest tests/test_home_web_controller.py tests/test_home_bridge.py tests/test_home_page.py -x -q (parity test is the anti-drift guard for the duplicated SQL)`

### `src/ui/web/dist/index.html`  (M)

THE COMMITTED BUILD ARTIFACT: vite-plugin-singlefile output of web/src (241,310 bytes, all JS/CSS inlined, title 'Alma Agent'), rebuilt in this delta to include the calendar/workbench/home routes, ChatDrawer, and headless hooks (grep confirms __almaHomeMounted present). Loaded via file:// by WebHost; sibling dist/qwebchannel.js (already committed, unchanged) is the classic-script channel shim.

**Key details:** Confirmed via web/vite.config.js: outDir '../src/ui/web/dist', viteSingleFile plugin, base './', emptyOutDir true, assetsInlineLimit 1e8; git ls-files shows dist/index.html + dist/qwebchannel.js tracked. CONSEQUENCE: the Mac needs NO node/npm to run the app — checkout of this file IS the web UI. Only rebuild if web/src is edited: npm --prefix web ci && npm --prefix web run build (public/qwebchannel.js is re-copied into dist by vite).

**Depends on:**
- src/ui/web/dist/qwebchannel.js (sibling, already tracked)
- src/ui/web/web_host.py resolves it relative to its own __file__

**macOS risks:**
- Must arrive byte-exact via git (it is minified; any merge-tool mangling silently breaks all web surfaces)
- Do NOT regenerate on the Mac unless node arm64 + npm ci succeed — a stale/failed rebuild with emptyOutDir:true deletes the working bundle

**Verify:** `wc -c src/ui/web/dist/index.html => 241310; grep -c __almaHomeMounted src/ui/web/dist/index.html => 1; python scripts/web_diag.py bundle probe = ok`

### `web/package.json`  (M)

Adds the vitest test runner: new script "test": "vitest run" and devDependency vitest ^4.1.10. Build script unchanged (vite build).

**Key details:** deps: react ^18.3.1, react-dom; devDeps: @vitejs/plugin-react ^4.3.4, vite ^5.4.11, vite-plugin-singlefile ^2.0.3, vitest ^4.1.10.

**Depends on:**
- web/package-lock.json
- Node >= 18 (vitest 4 / vite 5 floor) — only needed for JS tests or rebuilds, NOT to run the app

**macOS risks:**
- arm64 node required if tests/rebuild are run; skippable entirely for the app itself

**Verify:** `npm --prefix web run test (optional on Mac)`

### `web/package-lock.json`  (A)

Full npm lockfile (2941 lines) pinning the vite/vitest toolchain so `npm ci` is reproducible. Not needed at app runtime.

**Key details:** Pins the devDependency tree for vite 5 / vitest 4 / react 18.

**Depends on:**
- web/package.json

**macOS risks:**
- Lockfiles resolve platform-specific optional deps (esbuild/rollup natives) at install time — npm ci on the arm64 Mac fetches darwin-arm64 binaries automatically; no action needed

**Verify:** `npm --prefix web ci exits 0 (only if JS work is planned)`

### `web/vitest.config.js`  (A)

Separate vitest config so viteSingleFile (build-only) never runs in test mode; tests render with react-dom/server in a plain node environment (no jsdom).

**Key details:** plugins [react()], test.environment 'node', include ['src/**/*.test.{js,jsx}'].

**Depends on:**
- web/package.json test script

**macOS risks:**
- None

**Verify:** `npm --prefix web run test`

### `web/src/App.jsx`  (M)

Shrunk from 1520 LOC to a 33-line hash router: no hash -> ChatApp (pre-router bundles keep working), #/calendar -> CalendarApp, #/workbench -> WorkbenchApp, #/home -> HomeApp. The old inline chat UI moved to web/src/chat/ChatApp.jsx.

**Key details:** routeFromHash() strips ^#/? and prefix-matches; sets window.__almaRoute headless hook.

**Depends on:**
- web/src/chat/ChatApp.jsx
- web/src/calendar/CalendarApp.jsx
- web/src/workbench/WorkbenchApp.jsx
- web/src/home/HomeApp.jsx

**macOS risks:**
- None (ships inside committed dist bundle)

**Verify:** `npm --prefix web run test; app-level: each WebHost route renders its surface`

### `web/src/lib/bridge.js`  (A)

useBridge(name) React hook: ONE QWebChannel per page shared by all consumers (multiple client channels over one qt.webChannelTransport interleave message ids and break); resolves channel.objects[name] ('almaBridge'/'calendarBridge'/'workbenchBridge'/'homeBridge'), exposes it on window[name], returns null until ready.

**Key details:** Module-level channelPromise singleton; requires window.QWebChannel (from sibling classic-script qwebchannel.js) + window.qt.webChannelTransport.

**Depends on:**
- dist/qwebchannel.js loaded as classic script by index.html

**macOS risks:**
- None

**Verify:** `covered indirectly by all *_web_local round-trip tests and headless window.__alma* hooks`

### `web/src/lib/markdown.jsx`  (A)

Dependency-free markdown -> JSX renderer (bold/italic/code/links/lists/headings/rules/tables/paragraphs). XSS-safe by construction: all text enters as escaped React string children. Links only open via bridge.openExternal and only http/https — a click can never navigate the QWebEngine app away.

**Key details:** Exports Markdown component and mdOpen(url) (gates on /^https?:\/\//i and window.almaBridge.openExternal).

**Depends on:**
- ChatBridge.openExternal slot Python-side

**macOS risks:**
- None

**Verify:** `npm --prefix web run test (markdown.test.jsx locks the escaping contract)`

### `web/src/lib/markdown.test.jsx`  (A)

Vitest regression suite locking the markdown escaping contract: script tags/img-onerror payloads always render entity-escaped, never live elements — the property the bridge security model leans on (paired with tests/test_web_guardrails.py).

**Key details:** renderToStaticMarkup-based; node env, no DOM.

**Depends on:**
- web/vitest.config.js

**macOS risks:**
- None

**Verify:** `npm --prefix web run test`

### `web/src/chat/ChatApp.jsx`  (A)

The full Agent chat UI (1384 LOC), moved out of App.jsx: sessions/recents, streaming bubbles, tool rows, usage meter, jobs, draft review, voice, Drive/Asana/Guru pickers, Google action prompts. Pure renderer over almaBridge; sets ~20 window.__alma* headless verification hooks (__almaReady, __almaSessions, __almaStreamBuf, __almaGoogleState, ...).

**Key details:** Consumes ChatBridge signals incl. the pre-existing surface plus chatNotice; timestamps rendered best-effort via Date/toLocaleString.

**Depends on:**
- web/src/lib/bridge.js
- web/src/lib/markdown.jsx
- ChatBridge (Python)

**macOS risks:**
- None (bundle-shipped)

**Verify:** `gitignored tests/test_agent_bridge_local.py on the Mac if present (run singly, QT_QPA_PLATFORM=offscreen), else in-app Agent page`

### `web/src/chat/ChatDrawer.jsx`  (A)

Renn as an in-page drawer (M5.5) — the chat surface the web tabs/Home raise instead of the Qt ChatPanel drilldown. Same ChatBridge/ChatEngine as the Agent page; renders streaming, markdown, tool rows, and host notices (chatNotice). Deduplicates its own optimistic user echo via a pendingEchoes ref.

**Key details:** default export ChatDrawer({bridge, open, onClose}); headless hooks __almaDrawerMsgs, __almaDrawerNotices, __almaDrawerReady. Parent owns open/close so it can fall back to the Qt drilldown when no web chat bridge is registered.

**Depends on:**
- ChatBridge.chatNotice + push_notice (src/ui/web/chat_bridge.py)
- shared almaBridge registered via WebHost extra_bridges on the tab channels (page.py _get_web_chat_bridge)

**macOS risks:**
- None

**Verify:** `python -m pytest tests/test_web_chat_drawer.py -x -q (SINGLY)`

### `web/src/chat/demoRenn.js`  (A)

Demo-mode stand-in for the almaBridge chat object (dev/preview only): mimics the signal surface ChatDrawer consumes and answers sends with a canned reply. Engages only with ?demo AND no real bridge.

**Key details:** export makeDemoRenn(); fake signals responseReady/errorOccurred/busyChanged/statusUpdate/tokenStreamed/toolCall/chatNotice + send().

**macOS risks:**
- None

**Verify:** `open dist/index.html#/home?demo in a browser — drawer answers with '(sample reply)'`

### `web/src/calendar/CalendarApp.jsx`  (A)

Enablement Calendar route (#/calendar): month/week/agenda views over the calendarBridge viewmodel, lazy hover briefs, drag-to-reschedule (asks Python; native confirm), scope toggle, embedded ChatDrawer. Demo mode via #/calendar?demo when no bridge.

**Key details:** Headless hooks: __almaCalendarMounted, __almaCalEvents, __almaCalScope, __almaCalView, __almaBriefFor, __almaRescheduled, __almaCalDemo. Kind legend drive/guru/asana/high.

**Depends on:**
- web/src/calendar/grid.js
- web/src/calendar/demo.js
- web/src/chat/ChatDrawer.jsx
- calendarBridge + almaBridge on the channel

**macOS risks:**
- None

**Verify:** `npm --prefix web run test (grid.test.js); gitignored tests/test_calendar_web_local.py singly on the Mac if present`

### `web/src/calendar/grid.js`  (A)

Pure calendar math (ISO strings in/out) mirroring Python calendar.Calendar(firstweekday=6).monthdatescalendar: Sunday-start full weeks, agenda grouping, keyboard-focus reducer.

**Key details:** Exports iso, parseIso, daysInMonth, dowSunday0, addDays, addMonths, monthGrid, monthTitle, shortDate, weekOf, groupByDate, agendaGroups, moveFocus.

**macOS risks:**
- None

**Verify:** `npm --prefix web run test`

### `web/src/calendar/grid.test.js`  (A)

Vitest parity suite proving monthGrid matches Python monthdatescalendar(firstweekday=6) (e.g. July 2026 = 5 Sunday-start weeks Jun 28 -> Aug 1) plus agenda/focus reducers.

**Key details:** Pinned to concrete 2026 dates — deterministic.

**Depends on:**
- web/src/calendar/grid.js

**macOS risks:**
- None

**Verify:** `npm --prefix web run test`

### `web/src/calendar/demo.js`  (A)

Sample-data fixture for #/calendar?demo (dev/preview only; engages only when demo flag present AND no bridge; header badges SAMPLE DATA). Events generated relative to today so the demo never staleness-drifts.

**Key details:** exports isDemoMode(), buildDemoData(todayIso).

**Depends on:**
- web/src/calendar/grid.js addDays

**macOS risks:**
- None

**Verify:** `open dist/index.html#/calendar?demo in a browser`

### `web/src/home/HomeApp.jsx`  (A)

Home route (#/home) behind ui.web_home. Pure renderer: greeting/stats/tints/timestamps all arrive finished from HomeWebController. Mode-switch tile calls bridge.requestModeSwitch (native confirm Python-side). Sets window.__almaHomeMounted=true on mount — the exact predicate MainWindow._arm_home_watchdog probes at 8s to decide web-Home is alive.

**Key details:** Exports Tile, CccBanner, Stat, ActivityRow (for vitest) + default HomeApp; useBridge('homeBridge') + useBridge('almaBridge') for the Renn drawer; hooks __almaHomeMounted, __almaHomeVm, __almaHomeDemo; demo via #/home?demo.

**Depends on:**
- src/services/home_web.py viewmodel shape
- src/ui/web/home_bridge.py
- web/src/chat/ChatDrawer.jsx
- MainWindow watchdog (other slice)

**Settings keys:**
- ui.web_home (mentioned in the waiting-state copy)

**macOS risks:**
- If the bundle on disk predates this file, the watchdog silently swaps back to native Home after 8s — web Home looks 'off' with only an error log line ('page never mounted')

**Verify:** `npm --prefix web run test (home.test.jsx); in-app with ui.web_home on: Home renders and log shows no fallback`

### `web/src/home/home.test.jsx`  (A)

Vitest suite locking the Home escaping contract: DB-carried activity titles / tile copy with script-tag and img-onerror payloads must render entity-escaped, never markup.

**Key details:** Tests ActivityRow, CccBanner, Stat, Tile via renderToStaticMarkup.

**Depends on:**
- web/src/home/HomeApp.jsx exports
- web/src/home/demo.js

**macOS risks:**
- None

**Verify:** `npm --prefix web run test`

### `web/src/home/demo.js`  (A)

Sample Home viewmodel for #/home?demo (dev/preview only, DEMO badge so screenshots can't pass as live).

**Key details:** exports isDemoMode(), buildDemoHome().

**macOS risks:**
- None

**Verify:** `open dist/index.html#/home?demo`

### `web/src/workbench/WorkbenchApp.jsx`  (A)

Enablement Workbench route (#/workbench): workspace chips, TRUE-FIDELITY sandboxed Guru preview (iframe sandbox="" + srcDoc, defense-in-depth behind the Python sanitizer), word diff, check badges, markdown editor with Qt commit-on-leave semantics, AI-edit bar, publish/import menus that only ASK Python.

**Key details:** Exports PreviewFrame({html}) and DiffBody({diff}) (locked by tests) + default WorkbenchApp; FRAME_CSS inlined into the sandboxed doc; headless hooks __almaWorkbenchMounted, __almaWbChips, __almaWbDraft, __almaWbDiffRows, __almaWbPreviewRev, __almaWbCards, __almaWbPublish, __almaWbAiResolved, __almaWbDemo.

**Depends on:**
- web/src/workbench/EditTools.jsx
- web/src/workbench/demo.js
- workbenchBridge + almaBridge on the channel
- WorkbenchWebController sanitized preview_html

**macOS risks:**
- None

**Verify:** `npm --prefix web run test (workbench.test.jsx asserts sandbox="" with no allow-scripts/allow-same-origin)`

### `web/src/workbench/EditTools.jsx`  (A)

M4 editing chrome: AiEditBar (canned prompts Tighten/Fix grammar/Simplify + free text; shows 'Renn is revising…' while busy) and ToolsMenu (Import / Push to Guru / Save to Drive). Pure renderers — actions only ASK over the bridge.

**Key details:** exports AiEditBar({busy,disabled,onAsk}), ToolsMenu.

**Depends on:**
- web/src/workbench/WorkbenchApp.jsx wiring

**macOS risks:**
- None

**Verify:** `npm --prefix web run test`

### `web/src/workbench/demo.js`  (A)

Sample chips/drafts/diff for #/workbench?demo (dev/preview only, SAMPLE DATA badge, engages only with no bridge).

**Key details:** exports isDemoMode(), DEMO_CHIPS, DEMO_DRAFTS, DEMO_DIFF.

**macOS risks:**
- None

**Verify:** `open dist/index.html#/workbench?demo`

### `web/src/workbench/workbench.test.jsx`  (A)

Vitest suite locking the two safety-critical Workbench renderers: PreviewFrame must ALWAYS emit a fully-sandboxed iframe (sandbox="" — no allow-scripts, no allow-same-origin) via srcdoc, and DiffBody emits word spans as escaped text.

**Key details:** renderToStaticMarkup based.

**Depends on:**
- web/src/workbench/WorkbenchApp.jsx exports

**macOS risks:**
- None

**Verify:** `npm --prefix web run test`

### `web/src/styles.css`  (M)

Purely additive +427 lines: styles for the calendar (cal-*), workbench (wb-*), home (home-*), chat drawer, and route-empty waiting states. Ships inlined inside the dist bundle.

**Key details:** No deletions; existing chat styles untouched.

**macOS risks:**
- None

**Verify:** `visual — routes render styled in-app`

### `scripts/web_diag.py`  (A)

CLI wrapper for src/ui/web/web_diag: prints the OS-aware fact sheet, exit 1 on any FAIL. THE first command to run on any 'web page is blank' report and the first post-port smoke check on the Mac.

**Key details:** python scripts/web_diag.py [--json]; forces sys.stdout utf-8 reconfigure (cp1252 guard); sys.path.insert repo root.

**Depends on:**
- src/ui/web/web_diag.py

**macOS risks:**
- None — its macOS branch is the point; expects sysctl/xattr on PATH (always true on macOS)

**Verify:** `python scripts/web_diag.py on the M1 → expect '0 FAIL' with rosetta=ok, page_size=16384, helper_arch containing arm64`

### `scripts/verify_web_pivot_mac.sh`  (A)

The Mac verification harness (M6 checklist), non-destructive: (1) web_diag fact sheet must be 0 FAIL, (2) PySide6 version-parity note, (3) runs gitignored WebEngine round-trip tests SINGLY under QT_QPA_PLATFORM=offscreen (test_agent_bridge_local, test_calendar_web_local, test_workbench_web_local, test_home_web_local — skipped with a message if absent from the checkout), (4) informational RSS snapshot. Exits with the failure count.

**Key details:** Usage: bash scripts/verify_web_pivot_mac.sh [path-to-python3] (defaults python3; pass the venv/app python that imports PySide6). set -uo pipefail (deliberately not -e).

**Depends on:**
- scripts/web_diag.py
- scripts/measure_web_rss.py
- optionally the gitignored tests/test_*_web_local.py if copied to the Mac

**macOS risks:**
- Steps 3 tests are gitignored — a plain git checkout on the Mac will skip them all (prints 'not present … skipped'); that is expected, not a failure
- If files were transferred from the Windows working tree instead of via git, CRLF endings break bash — ensure LF

**Verify:** `bash scripts/verify_web_pivot_mac.sh <python3> → 'web pivot verification: PASS'`

### `scripts/sign_qtwebengine_dev.sh`  (A)

DO NOT RUN. Kept for historical reference of the 2026-07-08 blank-render post-mortem: its premise (re-sign PySide6 QtWebEngine + JIT entitlements) was empirically disproven and running it CORRUPTED the framework. Hard-guarded: exits 3 unless ALMA_I_UNDERSTAND_THIS_BREAKS_RENDERING=1.

**Key details:** Header documents the recovery recipe: pip install --force-reinstall --no-deps PySide6 PySide6-Essentials PySide6-Addons shiboken6 (all four — frameworks live in Addons) + remove leftover _CodeSignature dirs. Real blank-render causes were framework corruption from manual re-signing and Rosetta (fixed via LSRequiresNativeExecution / arch -arm64 in installer/install.py). Shipped-build signing path is installer/ci/sign_macos.sh — never this.

**macOS risks:**
- THE risk IS running it — the plan must state explicitly: never execute this on the Mac; web_diag's framework_signature probe detects if anyone ever did

**Verify:** `bash scripts/sign_qtwebengine_dev.sh → must print 'REFUSING TO RUN' and exit 3`

### `scripts/collect_mac_diag.sh`  (A)

READ-ONLY triage collector for the macOS blank-render class: sw_vers/arch, PySide6/Qt versions, codesign state of the app python + QtWebEngineProcess helper, framework seal verification, quarantine xattrs, .bak presence. Only needed IF a blank page appears; otherwise skippable.

**Key details:** Usage: bash scripts/collect_mac_diag.sh <app-python3> (default python3). Deliberately not -e; corrected 2026-07-14 note: missing JIT entitlements are NORMAL for the pip wheel, not a failure — python scripts/web_diag.py is the authoritative fact sheet.

**Depends on:**
- codesign/xattr/sysctl (stock macOS)

**macOS risks:**
- None — modifies nothing

**Verify:** `run only on a blank-page report; paste full output`

### `scripts/measure_web_rss.py`  (A)

Informational RSS probe: boots Agent + web Calendar + web Workbench offscreen and prints per-surface memory deltas (Qt process + Chromium children). Fills the M6 budget table on both dev box and M1.

**Key details:** Sets QT_QPA_PLATFORM=offscreen (default) and, only when offscreen, QTWEBENGINE_DISABLE_SANDBOX=1 + QTWEBENGINE_CHROMIUM_FLAGS='--no-sandbox --disable-gpu --disable-software-rasterizer --in-process-gpu' (the offscreen TEST recipe — the real app sets none). Uses psutil when available; else a Windows-only ctypes/psapi fallback that is exception-wrapped and returns -1.0 on macOS.

**Depends on:**
- src/services/enablement_web.py
- src/ui/web/{agent_page,web_host,calendar_bridge,workbench_bridge}.py
- psutil (optional but effectively required on macOS for real numbers)

**macOS risks:**
- Without psutil the fallback is Windows psapi — on the Mac it degrades to '-1.0 MB'; pip install psutil there for meaningful output
- Informational only — verify_web_pivot_mac.sh treats its failure as non-fatal

**Verify:** `python scripts/measure_web_rss.py prints four MB lines without crashing`

---

### Slice-level notes — web-layer

**Hard ordering constraints (matter only for piecemeal moves; a full checkout satisfies them all):**
- src/ui/web/web_host.py before src/ui/web/agent_page.py (imports _DIST/_SPIKE/WebHost from it), before scripts/measure_web_rss.py, and before any bridge is registered on a channel (the orphan-parenting fix lives there)
- src/ui/web/web_diag.py before scripts/web_diag.py and before web_host.py is exercised (web_host soft-imports it inside try/except, so import order is safe but diagnostics silently vanish if absent)
- src/branding.py (branding slice) before src/services/home_web.py — hard import of CONTENT_COMMAND_CENTER / CONTENT_COMMAND_CENTER_DESCRIPTION; home_web fails to import without it, and main_window then falls back to native Home
- src/data/text_diff.py, src/data/html_sanitize.py, src/data/html_markdown.py (data slice) before src/services/enablement_web.py's diff/preview paths execute (imports are lazy inside methods, so the module imports fine but Workbench diff/preview break at runtime)
- src/ui/web/chat_bridge.py (chatNotice/push_notice) before src/ui/pages/enablement/page.py (other slice) whose add_message flow calls bridge.push_notice
- src/ui/web/web_flags.py before src/ui/pages/enablement/page.py::_make_calendar/_make_workbench and src/ui/main_window.py::_create_home_page (both call it)
- src/ui/web/dist/index.html + existing dist/qwebchannel.js must be present (byte-exact from git) before ANY web surface renders — everything else degrades to the static spike or a blank page
- web/* sources and configs have no runtime ordering — they matter only before an optional `npm --prefix web run test` or a bundle rebuild

**Cross-file risks / silent failures:**
- SILENT-OFF BY DESIGN: enablement.web_tabs defaults 'off' and ui.web_home defaults False, both failing closed on any settings error — after a flawless port the Mac shows ONLY native pages. This looks like the port 'did nothing'. Since the Mac's gitignored data/ must not be touched, in-app verification of the web surfaces requires the Mac's owner to flip the flags; the committed headless tests (test_web_guardrails, test_*_bridge, test_home_web_controller, test_web_diag, test_enablement_web_flag) are the port gate instead.
- WATCHDOG MASKING: if web Home breaks on the Mac (stale bundle, renderer death), MainWindow._arm_home_watchdog silently swaps in the native HomePage after 8s (loadFinished not-ok, or runJavaScript('!!window.__almaHomeMounted') falsy) — the app looks fine while web Home is actually dead. Check the 'alma.home.web' logger for 'falling back to the native page' when verifying.
- BUNDLE = ARTIFACT: src/ui/web/dist/index.html (241,310 bytes) IS the app's web UI; no npm/node needed on the Mac. Corollary: never run `npm run build` casually there — emptyOutDir:true wipes dist/ first, so a failed or stale-source rebuild destroys a working UI.
- QWEBCHANNEL OWNERSHIP INVARIANT: any future wiring that registers a bridge must either parent it or pass it through WebHost (which parents orphans). Bypassing WebHost and calling registerObject with a temporary reintroduces the native access-violation crash class.
- OFFSCREEN TEST DISCIPLINE: WebEngine round-trip tests (gitignored *_web_local.py + committed tests/test_web_chat_drawer.py) MUST run one file per pytest invocation — offscreen Chromium teardown stacks to exit 255 across files; offscreen window grabs are ALWAYS blank so assertions go through runJavaScript/window.__alma* hooks, never screenshots.
- chat_bridge.chatNotice/push_notice is consumed by page.py (another slice) — porting the slices out of sync yields AttributeError on scan/publish notice pushes (swallowed only if page.py wraps them).
- home_web.py duplicates HomePage SQL/constants ON PURPOSE (src/services must not import src/ui) including a bug-for-bug string-compare activity sort; the porter must not 'clean up' either side — tests/test_home_web_controller.py parity test is the drift guard.

**macOS-specific:**
- Verify arm64-native stack first: python scripts/web_diag.py must show page_size=16384, rosetta=ok, helper_arch containing arm64, framework_signature ok, bundle ok (0 FAIL). It also detects the two historical killers: Rosetta translation (QTBUG-98487) and _CodeSignature leftovers from past manual re-signing.
- NEVER run scripts/sign_qtwebengine_dev.sh (it corrupts the PySide6 QtWebEngine framework; hard-guarded behind ALMA_I_UNDERSTAND_THIS_BREAKS_RENDERING=1). If web_diag reports framework corruption, recover with: pip install --force-reinstall --no-deps PySide6 PySide6-Essentials PySide6-Addons shiboken6, then strip quarantine (xattr -rd com.apple.quarantine <pyside6 dir>) if flagged.
- Full PySide6 required (Essentials lacks QtWebEngine) — web_diag's webengine_import probe FAILs otherwise and every web surface degrades to native/placeholder.
- Run bash scripts/verify_web_pivot_mac.sh <venv-python3> as the slice's acceptance step; gitignored *_local.py steps will report 'skipped' on a clean checkout — expected.
- pip install psutil on the Mac if RSS numbers are wanted from scripts/measure_web_rss.py (the fallback is Windows-psapi and prints -1.0 on macOS).
- node/npm are OPTIONAL: only for `npm --prefix web ci && npm --prefix web run test` (vitest, Node >= 18 arm64) or a deliberate bundle rebuild. The committed dist/index.html runs the app as-is.
- If any files are copied from the Windows working tree instead of git-checkout, ensure the three .sh scripts have LF endings (CRLF breaks bash) — invoke via `bash scripts/...` (no chmod needed).

**Analyst notes / answered questions:**
- ANSWERED (critical): yes — src/ui/web/dist/index.html is the committed vite-plugin-singlefile artifact of web/src (web/vite.config.js: outDir '../src/ui/web/dist', viteSingleFile, base './', assetsInlineLimit 1e8; git ls-files tracks dist/index.html + dist/qwebchannel.js; bundle contains __almaHomeMounted and all hash routes). The Mac needs NO npm/node to run the app; WebHost/agent_page locate the bundle relative to src/ui/web/__file__ (_DIST, falling back to static/index.html spike).
- ANSWERED: flags are enablement.web_tabs ('off' default | 'calendar' | 'all') and ui.web_home (False default; bool/1/'on'/'true'/'yes'/'1' truthy), both read exclusively through src/ui/web/web_flags.py (web_tabs_mode / web_home_enabled); ANY settings exception or unrecognized value degrades to off/False — a flag can never remove the native surface by accident.
- ANSWERED: web_host.py parents orphan bridges (obj.setParent(self) when obj.parent() is None, before registerObject) — the GC-crash fix. Invariant for the plan: every QWebChannel-registered QObject must have a Qt parent; route all registration through WebHost.
- ANSWERED: script roles — web_diag.py first on any blank page (and post-port smoke); verify_web_pivot_mac.sh as the acceptance harness (safe, non-destructive); collect_mac_diag.sh only on an actual blank-render report (read-only); sign_qtwebengine_dev.sh NEVER (booby-trapped historical artifact).
- ANSWERED: Home watchdog — MainWindow._arm_home_watchdog(host, 8000ms) hooks host.view.loadFinished (not-ok → immediate fallback) and after 8s runs runJavaScript('!!window.__almaHomeMounted'); falsy → _fallback_to_native_home builds the native HomePage, swaps it into content_stack/_page_widgets/_factory_widgets and deleteLater()s the host. HomeApp.jsx sets the flag in a mount effect.
- OPEN: the gitignored tests/test_*_web_local.py round-trip suites exist only on the Windows dev box — decide whether to copy them to the Mac for verify_web_pivot_mac.sh step 3 or accept the committed-contract-tests-only gate.
- OPEN: whether the Mac's checkout already carries an older dist/index.html from the 91f9df6-era commit — if the port cherry-picks rather than merges dbb5c8f, dist/index.html MUST be included or web Home/drawer features silently regress to the older bundle (routes fall back to chat; watchdog hides it).


## 5. Qt UI & branding

### `src/__init__.py`  (M)

VERSION bumped 1.0.0 -> 1.0.7. Splash and updater version comparisons read this; porting the delta without it makes the update checker compare against the wrong baseline.

**Key details:** Single constant: VERSION = "1.0.7". main.py now feeds it to app.applicationVersion() (fixed a hardcoded 1.0.0 on the splash).

**Verify:** `python -c "from src import VERSION; assert VERSION=='1.0.7'"`

### `src/branding.py`  (A)

New single source for Content Command Center branding strings, placed at top of src/ so both src/services and src/ui may import it (services must not import ui). Exports CONTENT_COMMAND_CENTER = "Content Command Center" and CONTENT_COMMAND_CENTER_DESCRIPTION (owner-supplied paragraph).

**Key details:** Consumers: splash_window.py (enablement branding block), main_window.py (_chrome_subtitle, _sidebar_footer_text), home_page.py (CCC banner). The Help article assets/help/getting-started/content-command-center.md quotes the description verbatim and tests/test_help_claims_getting-started.py pins the two together. Branding is mode-conditional everywhere: product mode keeps 'ALMA INSIGHTS / RCM Issue Analysis / RCM Operations'.

**Depends on:**
- assets/help/getting-started/content-command-center.md (claims test pins verbatim)

**Verify:** `python -m pytest tests/test_help_claims_getting-started.py -x -q`

### `src/ui/app_modes.py`  (M)

Adds two enablement PageSpecs only: en_attention ('Attention Queue', icon 'tasks', INSIGHTS group, tab_key='home') — restores sidebar access to the default landing tab (main_window hides the internal tab bar when the sidebar owns nav, so the tab was unreachable after navigating away) — and en_help ('Help', icon 'book', SYSTEM group, tab_key='help').

**Key details:** resolve_startup_mode() is UNCHANGED and pre-existing: CLI --mode > app.default_mode (value 'last' resolves via app.last_mode) > product. _FIRST_PAGE is 'home' for both modes. Nothing in this delta forces a mode in code — the dev box change was a data/settings.yaml edit (app.default_mode: last) which is gitignored and must NOT be replicated to the Mac.

**Depends on:**
- src/ui/design/icons.py glyphs 'tasks' and 'book' (pre-existing, verified present)
- enablement page select_tab handling of 'help' key (page.py in this delta)

**Settings keys:**
- app.default_mode (read, unchanged code)
- app.last_mode (read, unchanged code)

**Verify:** `python -m pytest tests/test_app_modes.py -x -q`

### `src/ui/design/qss.py`  (M)

Sidebar QSS additions supporting the new scrollable nav: transparent #SidebarScroll/#SidebarNavHost plumbing (prevents a light rectangle over the dark sidebar), thin 6px scrollbar styling, and min-height guards (SidebarButton 20px so font descenders g/y/p/Q don't clip, SidebarFooter 14px, SidebarCollapseBtn 22px).

**Key details:** Selectors: #SidebarScroll, #SidebarNavHost, '#SidebarScroll > QWidget > QWidget', #SidebarScroll QScrollBar:vertical/::handle/::add-line/::sub-line. Pairs with main_window.py's new QScrollArea (objectName SidebarScroll) hosting nav_host (SidebarNavHost).

**Depends on:**
- src/ui/main_window.py sidebar QScrollArea (same commit)

**macOS risks:**
- Font metrics differ on macOS — the min-height values were tuned against Windows font rendering; visually verify no clipped descenders on the Mac (tests/test_sidebar_layout.py covers structure, not pixels)

**Verify:** `python -m pytest tests/test_sidebar_layout.py -x -q`

### `src/ui/dialogs/drive_folder_picker_dialog.py`  (A)

Native (no-LLM) Google Drive folder picker: QTreeWidget browsing roots from list_picker_roots (Shared Drives + shared-with-me — the only roots a service account has; old tree rendered blank), lazy child listing on expand, empty-state banner naming the exact service-account email to share folders to, 'Copy service-account email' button.

**Key details:** class DriveFolderPickerDialog(QDialog). After exec()==Accepted, self.picked = {id, name, drive_id}. Class attr worker_cls = DriveListWorker (test seam); Signal listing_loaded(str). Workers parented to dialog AND held in self._workers until the main-thread slot fires (mid-run QThread GC = native crash — the documented QWebChannel-era lesson); dialog is cached/REUSED by SettingsPage (never destroyed per open, so close-with-listing-in-flight never destroys a running thread's parent). Token routing via self._pending; stale tokens dropped after reload(). Static seams _access_ready() -> google_access.google_access_ready(), _sa_email() -> google_access.service_account_email(). Selecting the synthetic 'root' alias is refused. Applies style_native_dialog(self) in __init__ (blank-button fix).

**Depends on:**
- src/services/agent_chat.py DriveListWorker + list_picker_roots/list_folders (same delta, services slice)
- src/data/google_access.py google_access_ready + service_account_email (same delta, data slice)
- src/ui/pages/enablement/_common.py style_native_dialog (this slice)

**Verify:** `python -m pytest tests/test_drive_folder_picker.py tests/test_native_dialog_styling.py -x -q`

### `src/ui/main_window.py`  (M)

Five changes: (1) mode-aware chrome — top-bar subtitle and sidebar footer retext via _apply_mode_chrome(mode) on switch_mode ('Content Command Center' in enablement, 'RCM Issue Analysis'/'RCM Operations' in product; footer 'v{VERSION} — {label} · Drive + local search'); (2) sidebar nav moved into a QScrollArea (SidebarScroll/SidebarNavHost) with trailing stretch re-added on every rebuild; (3) web Home behind ui.web_home with construction fallback + runtime watchdog (_arm_home_watchdog: loadFinished not-ok OR window.__almaHomeMounted never set within 8000ms -> _fallback_to_native_home swaps native widget into the stack in place); (4) _set_active_page gains an opt-in on_page_shown() refresh hook (fired when a different widget becomes active) plus web-home refresh(); (5) enablement Help/Feedback routing (_show_help -> en_help page; _show_feedback -> en_help + _open_bug_form) and a real native ChatPanel fallback for the Agent page when only QtWebEngine is missing.

**Key details:** New methods: _chrome_subtitle, _sidebar_footer_text, _apply_mode_chrome, _web_home_wanted, _build_native_home, _build_web_home, _web_home_mode_confirm (native QMessageBox gate, defaults No), _arm_home_watchdog, _fallback_to_native_home, _native_agent_fallback. _build_web_home wires HomeWebController(confirm_fn=...) signals mode_selected/quick_action/activity_activated and HomeBridge(data_signal, refresh_fn, quick_action_fn, activity_fn, mode_switch_fn), WebHost(channel_name='homeBridge', route='/home', log_name='alma.home.web'); triple (ctrl,bridge,host) held on self._home_web against GC (bridge additionally parented by WebHost). _start_services_for_mode now passes asana_interval_seconds=int(enablement.asana.poll_interval_seconds, default 60) to EnablementMonitor.start(). Fallback bookkeeping updates _page_widgets['home'] and _factory_widgets['_create_home_page'].

**Depends on:**
- src/branding.py
- src/ui/web/web_flags.py web_home_enabled (same delta)
- src/services/home_web.py + src/ui/web/home_bridge.py + web_host.py (same delta, web slice)
- src/ui/pages/enablement/chat_panel.py ChatPanel (pre-existing) for the agent fallback
- src/data/enablement_monitor.py start(asana_interval_seconds=) signature (same delta, data slice)
- en_help PageSpec in app_modes.py
- web/src build -> src/ui/web/dist bundle only if ui.web_home is turned on

**Settings keys:**
- ui.web_home (read via web_flags; defaults off, fails closed)
- enablement.poll_interval_seconds (default 300)
- enablement.asana.poll_interval_seconds (default 60)

**macOS risks:**
- QtWebEngine paths are all flag-gated off by default; the watchdog exists precisely for the macOS blank-render class (renderer dies with load reported ok) — keep ui.web_home off on the Mac until scripts/web_diag.py is clean
- _show_feedback in enablement reads _page_widgets.get('en_help') BEFORE navigating; if the enablement page was never mounted the opener is None and it silently falls back to the product HelpDialog

**Verify:** `python -m pytest tests/test_page_return_reload.py tests/test_sidebar_layout.py tests/test_home_page.py -x -q; then launch app, switch modes, confirm top-bar subtitle and sidebar footer retext`

### `src/ui/pages/enablement/_common.py`  (M)

Adds the native-dialog blank-button fix: native_dialog_button_qss() (green-fill button QSS incl. :default and :hover) and style_native_dialog(dialog) (sets cream QDialog/QMessageBox background + the button QSS ON the dialog itself). Root cause: enablement pages set a selectorless 'background: <cream>' stylesheet that cascades to every descendant including native dialogs parented to the page, overriding the app QSS's green button fill while its white text survives — white-on-cream = invisible labels. An ancestor rule loses to the page's own selectorless background by proximity, so the corrective must sit on the dialog.

**Key details:** New imports from theme: ALMA_GREEN_DARK, ALMA_TEXT_ON_DARK, ALMA_WHITE. Consumers: DriveFolderPickerDialog.__init__, page.py _web_reschedule_confirm + _web_workbench_publish_confirm, settings.py _prompt_text (QInputDialog). NOT platform-specific: QMessageBox/QInputDialog are Qt-drawn (not NSAlert) so the cascade bug and the fix behave identically on macOS. QFileDialog uses the real native sheet on macOS and is unaffected.

**Depends on:**
- src/ui/theme.py constants (pre-existing)

**Verify:** `python -m pytest tests/test_native_dialog_styling.py -x -q`

### `src/ui/pages/enablement/attention_queue_tab.py`  (M)

reload() now clears the session _dismissed set first: an explicit Refresh brings dismissed rows back (dismiss means 'not now', not 'hide until restart').

**Key details:** One-line behavior change: self._dismissed.clear() at the top of reload(). Rest of the diff is docstring.

**Verify:** `python -m pytest tests/test_attention_dismissal.py -x -q`

### `src/ui/pages/enablement/help_tab.py`  (A)

New Help Center tab: TOC tree grouped by section (left) + QTextBrowser markdown article view (right), data-driven status badges/banners from article frontmatter (partial / flag-gated / not-available — the honesty mechanism), tokenized search via src.data.help.search (best match only), help:// cross-article anchors (any other scheme refused), 'Flag a bug' button emitting flag_bug_requested(article_id).

**Key details:** class HelpTab(QFrame); Signal flag_bug_requested(str). Constructor takes conn_provider (a CALLABLE, not a connection — survives DB swap). reload() calls loader.sync_bundled_help(conn) (sync-when-BEHIND, not only-when-empty — the 8-vs-62 field bug fix) then store.list_articles(conn). _STATUS_BADGE maps partial/flag-gated/not-available to label+colors; 'available' renders no badge. _ROLE_ID = Qt.UserRole+1. viewer has setOpenExternalLinks(False)+setOpenLinks(False).

**Depends on:**
- src/data/help/{store,search,loader}.py (same delta, data slice)
- migrations/048_help_center.sql (help_articles table)
- assets/help/** (62 articles, same delta)

**macOS risks:**
- Bundled corpus path resolution lives in the loader (data slice) — verify assets/help ships in the Mac install layout; the tab itself is path-free

**Verify:** `python -m pytest tests/test_help_tab.py tests/test_help_store.py tests/test_help_search.py tests/test_help_corpus_sync.py -x -q`

### `src/ui/pages/enablement/page.py`  (M)

The biggest file in the slice (~960 diff lines). Adds: web Calendar/Workbench tab construction behind enablement.web_tabs with native fallback (_make_calendar/_make_workbench returning (controller, tab_widget)); the shared web chat bridge (almaBridge) + transcript mirroring (_chat_say/_mirror_user_turn replacing most chat.add_message calls); Help tab (_make_help + _open_bug_form opening enablement.help.bug_form_url in the SYSTEM browser via QDesktopServices — deliberately never the embedded view); native Drive picker persistence (_on_drive_folder_picked through chat_tools' _set_drive_folder_impl so button and model paths converge on enablement.drive.active_folders); the card-template guide section generalization (_guide_action parameterized for style guide + card template, upload/upload_folder/paste/drive/clear, inline-editor save handlers); on_page_shown() refresh hook; Asana WS1 M3-M7 UI (lazy extras/brief join in _show_task_detail, completed_changed wiring to set_completed_in_asana, CAS conflict status, debounced 500ms monitor refresh, auto-reopen of an open detail panel after background reconcile); RENN_SYSTEM_PROMPT expansion (live Drive tools, KB tools, content studio, help_search, request_asana_task_update gated-write contract); demo-mock Asana setup guard; stable upload source_ref dedupe.

**Key details:** CRITICAL ORDER CHANGE: __init__ now calls _setup_engine() BEFORE _build() — the web tabs register the shared chat bridge on their channels at construction and WebHost registers channel objects exactly once; reversing the order kills the Renn drawer for the page lifetime. New/changed methods: _make_help, _open_bug_form(article_id=''), _make_calendar, _web_reschedule_confirm, _get_web_chat_bridge, _web_chat_send (refuses sends while workbench.is_publish_inflight), _mirror_user_turn, _web_chat_tool_poll (reads chat_tool_executions by session_id/rowid), _chat_say, _make_workbench, _web_workbench_publish_confirm, _web_publish_content_lookup, _web_workbench_upload (native QFileDialog), _web_brief_lookup, on_page_shown, _on_drive_folder_picked, _refresh_active_drive_folder, _guide_action/_store_guide_files/_guide_files_in_folder (recursive rglob, cap 50, skips '.'/'~$' prefixes), _bundled_card_template_text (Path(__file__).parents[4]/assets/templates/support_center_article_template.md, utf-8, inline fallback), _load_bundled_card_template, _refresh_card_template_status, _on_style_guide_saved/_on_card_template_saved (update_*_text in place), _on_card_template_activate/delete, _clear_web_ai_inflight, _on_asana_tasks_updated. ChatEngine now constructed with stream=True. Session: resolve_or_create_session('enablement', conn, pointer_dir=Path(db_path).parent) replaces create_session + manual .current_chat_session file write (shares ONE session with the Agent page). _fetch_existing_cards always calls workbench.set_existing_cards (even []) to unwedge the web spinner. All self.workbench tab references became self._workbench_tab. read_document(path, strict=True) for deck modelling. _on_analytics_synced reorder: _load_live FIRST then status. _on_ai_edit_done refreshes the draft the WORKER captured (res['draft_id']), not the currently-active one; _on_content_edited also syncs the in-memory _drafts cache.

**Depends on:**
- src/ui/web/web_flags.py web_tabs_mode
- src/services/enablement_web.py CalendarWebController/WorkbenchWebController + src/ui/web/{calendar_bridge,workbench_bridge,chat_bridge,web_host}.py
- src/ui/pages/enablement/help_tab.py + _common.style_native_dialog (this slice)
- src/data/chat_tools/enablement_tools.py _set_drive_folder_impl
- src/data/enablement_store.py card-template API: get/set/clear_card_template, list_card_templates, set_active_card_template, delete_card_template, update_style_guide_text, update_card_template_text, save_document(source_ref=)
- src/data/{asana_extras,enablement_tasks}.py + migrations 044/045 (extras, brief_status/brief_json)
- src/data/kb/store.py cards_count + migration 046/049 (kb_folders)
- src/services/chat_session.py resolve_or_create_session
- src/services/chat_engine.py stream= kwarg
- src/data/doc_reader.py read_document(strict=)
- src/data/asana_setup.py discover() 'mock' flag + writeback op set_completed_in_asana
- assets/templates/support_center_article_template.md
- chat_tool_executions table (pre-existing)

**Settings keys:**
- enablement.web_tabs (off|calendar|all; read via web_flags)
- enablement.help.bug_form_url (new; unset -> explanatory message)
- enablement.drive.active_folders (written via _set_drive_folder_impl, read by _refresh_active_drive_folder)

**macOS risks:**
- QFileDialog.getOpenFileNames/getExistingDirectory now start at os.path.expanduser('~') explicitly BECAUSE macOS native dialogs otherwise open on an opaque default — keep this
- _bundled_card_template_text uses parents[4] — correct only if the file stays at src/ui/pages/enablement/page.py depth; case-sensitive path 'assets/templates/...' matters on any case-sensitive APFS volume
- web tab branches need the built web bundle (npm --prefix web run build) only when enablement.web_tabs is flipped on — native fallback otherwise; do not flip flags on the Mac as part of the port

**Verify:** `python -m pytest tests/test_card_template.py tests/test_asana_setup_guard.py tests/test_shared_session.py -x -q; then tests/test_task_detail_actions.py tests/test_asana_writeback.py tests/test_page_return_reload.py -x -q`

### `src/ui/pages/enablement/settings.py`  (M)

Enablement Settings rebuilt as a 7-icon-tab surface: Connections (status dots + workspace basics), Providers (identity + shared CredentialsPanel), Sources (Asana + Drive + NEW knowledge-base card), Style Guide (NEW _GuideSection class x2: style guide + card/article template with rendered-markdown preview, inline Edit/Save editor, stored-doc library), Updates (shared UpdatesPanel), Usage (lazily-built RennUsagePanel), Maintenance (shared MaintenancePanel). Adds the native Browse Drive… picker entry point and the KB enable/bootstrap card.

**Key details:** New signals: drive_folder_picked(str,str,str), style_guide_saved(str), card_template_action/activate/delete/saved, _kb_bootstrap_finished(str) (internal, worker->main queued hop). Tab icons via design_icon glyphs: antenna/sliders/database/pen/download/pie/zap, QSize(14,14). Host-wired attributes set AFTER construction by page.py: kb_conn_factory (connection callable) and usage_db_factory (DatabaseManager callable; demo mode passes _ensure_demo_db). Usage tab built lazily in _on_tab_selected via tabText(index)=='Usage' -> _maybe_build_usage() -> RennUsagePanel(db). KB card: refresh_kb_status() is the ONE degraded-state surface (demo_mode / asana token / google_access_ready / kb.enabled / kb.ec_folder_id, card+topic counts via kb_store.cards_count + kb_folders role='topic' status='ok'); _on_kb_bootstrap runs drive_kb.ensure_ec_root(conn) on a daemon threading.Thread and returns via the queued signal. _on_kb_toggle flips enablement.kb.enabled via get_section/set_section. _prompt_text wraps QInputDialog with native_dialog_button_qss (blank-button fix). _on_browse_drive caches/reuses DriveFolderPickerDialog (thread-parent safety) and emits drive_folder_picked. set_active_drive_folders normalizes legacy plain-string entries (a string entry crashed the label). _GuideSection strips tags [STYLE-GUIDE]/[CARD-TEMPLATE] from names. Old _guru() mock section deleted. Docstring drift: comments claim the Usage tab hosts 'shared CostDashboard' but the code builds RennUsagePanel — do not 'fix' by porting CostDashboard.

**Depends on:**
- src/ui/widgets/{updates_panel,maintenance_panel}.py (this slice)
- src/ui/pages/enablement/usage_tab.py (this slice)
- src/ui/dialogs/drive_folder_picker_dialog.py (this slice)
- src/data/kb/{store,drive_kb}.py + migrations 046/049
- src/data/asana_setup.py is_asana_connected
- src/data/google_access.py google_access_ready
- src/ui/design/icons.py glyphs (pre-existing)
- page.py wires kb_conn_factory + usage_db_factory and connects all new signals

**Settings keys:**
- enablement.kb.enabled (toggled here)
- enablement.kb.ec_folder_id (read)
- enablement.demo_mode (read, default True)
- enablement.drive.active_folders (displayed)

**macOS risks:**
- _maybe_build_usage keys off the literal tab label 'Usage' — fragile if renamed; not platform-specific but a silent-failure trap
- KB bootstrap thread does network I/O; safe pattern (queued signal) — no main-thread violations

**Verify:** `python -m pytest tests/test_settings_subtabs.py tests/test_system_panels.py tests/test_native_dialog_styling.py -x -q`

### `src/ui/pages/enablement/task_detail.py`  (M)

Task detail panel layout fix + WS1 features: badges/who/complete-button each get their own row (variable-length names previously pushed the Complete button into an unclickable clipped sliver in the 440px panel); new completed_changed(bool) signal with a Mark complete/Reopen button (direct write, click is the consent); renders the Haiku brief block (only when brief_status=='ok'), Asana extras read-only sections (custom fields chips max 6, attachments max 8 routed through open_source for scheme validation, last 10 comments/stories).

**Key details:** New signal: completed_changed = Signal(bool) (True=complete, False=reopen). All Asana-sourced text rendered with setTextFormat(Qt.PlainText) (untrusted). brief dict keys: ask, deliverable, effective_date, stakeholders (max 4 chips), links (max 3). extras dict keys: custom_fields[{name,display_value}], attachments[{name,view_url}], stories[{author,created_at,text}]. Host (page.py) joins extras/brief lazily and connects completed_changed -> _run_task_writeback('set_completed_in_asana', tid, done).

**Depends on:**
- page.py lazy extras/brief join + set_completed_in_asana writeback (this slice)
- src/data/asana_extras.py + migration 044 (data slice)

**Verify:** `python -m pytest tests/test_task_detail_actions.py -x -q`

### `src/ui/pages/enablement/usage_tab.py`  (A)

New RennUsagePanel — the enablement Usage tab: hero stat tiles (turns today/week/month + 30-day CLI-reported cost), a QPainter 14-day column chart (_ColumnChart, zero-dependency), and the recent-turns list. Replaces the scan-centric CostDashboard on the enablement side; refresh on show + Refresh button, deliberately no timers (Settings page pinned timer-free by help claims).

**Key details:** RennUsagePanel(db) reads via getattr(db,'conn') and renn_usage.usage_summary(conn); summary shape used: summary['today'|'week'|'month'] = {turns, tokens_in, tokens_out, cost_usd}, summary['series'] = [(day,count)x14], summary['recent'] = [{source, model, tokens_in, tokens_out, cost_usd, date, hour}]. Helpers _fmt_tokens (950/12.3k/1.2M) and _fmt_cost ($0.00 / $0.0001 / $1,234.56). Cost tile shows '$0 on subscription login' when zero — the Claude CLI reports no dollar figure on subscription auth. showEvent triggers refresh, swallows exceptions.

**Depends on:**
- src/data/renn_usage.py usage_summary (same delta, data slice; rows in gemini_usage with source='renn_chat', NO new migration)
- src/ui/pages/enablement/_common.py card_frame/pill/section_label (pre-existing)

**macOS risks:**
- Cost is $0.00 on a subscription 'claude' CLI login (the Mac's expected auth per memory) — that is documented behavior, not a port failure

**Verify:** `python -m pytest tests/test_renn_usage.py tests/test_system_panels.py -x -q`

### `src/ui/pages/gemini_chats_page.py`  (M)

Chat tools flipped off native MCP: ChatEngine now gets use_mcp_tools=False (re-arms TOOL_PROMPT_ADDENDUM anti-fabrication guardrail + the in-process regex TOOL_CALL dispatch loop, which works for Gemini AND the Claude CLI bridge) and the warm ReportBridgeClient gets set_mcp_config([]) instead of a built MCP config. Rationale in-code: ACP-MCP tool path is architecturally broken (commit 68e743d) and the Claude CLI bridge never wires MCP, so MCP=True left the model toolless and it invented ticket IDs.

**Key details:** Two hunks only: ChatEngine(..., use_mcp_tools=False, ...) in the engine construction; _warm_bridge.set_mcp_config([]) mirroring scan_orchestrator.py:2525. Session-pointer file for MCP no longer needed on this path (engine's dispatch_tool tags session_id).

**Depends on:**
- src/services/chat_engine.py text tool loop (chat_engine.py:298,345 referenced; changed in delta, services slice)

**macOS risks:**
- Pre-existing reliance on the gemini CLI/node bridge being installed remains; this diff changes no process invocation

**Verify:** `python -m pytest tests/test_chat_engine.py -x -q; in-app: product-mode chat answers a ticket query with a real TOOL_CALL round-trip`

### `src/ui/pages/home_page.py`  (M)

Home page gains the Content Command Center banner (name + owner description from src/branding), visible ONLY in enablement mode (toggled in set_mode); the enablement mode tile description retexted to the CCC pitch.

**Key details:** New _build_ccc_banner() -> card with objectName CccBanner; set_mode(mode) now calls self._ccc_banner.setVisible(mode == app_modes.MODE_ENABLEMENT). Imports CONTENT_COMMAND_CENTER + CONTENT_COMMAND_CENTER_DESCRIPTION. _MODE_TILES enablement entry text changed.

**Depends on:**
- src/branding.py

**Verify:** `python -m pytest tests/test_home_page.py -x -q`

### `src/ui/pages/settings_page.py`  (M)

Product Settings shrinks by ~810 lines: the entire auto-update/GitHub-repo/support-recovery surface and the memory-profiler + full-database-reset danger zone were EXTRACTED VERBATIM into the shared UpdatesPanel and MaintenancePanel widgets; the tabs now just mount the panels. Also imports update_section from settings_manager.

**Key details:** Removed methods (now in updates_panel.py): _build_support_and_recovery, _on_export_crash_reports, _on_manual_rollback, _load_github_settings, _on_save_github_settings, _on_check_updates, _on_update_available, _on_up_to_date, _on_check_failed, _on_view_release_notes, _on_install_update, _resolve_install_artifact, _on_update_progress/_complete/_failed, _on_restart_app, _save_last_checked, _load_last_checked. Removed (now in maintenance_panel.py): _build_memory_debug_section, _on_memory_snapshot, _on_force_gc, _build_data_management_section, _full_database_reset. Mount sites: Updates tab -> self._updates_panel = UpdatesPanel(); Display tab -> self._maintenance_panel = MaintenancePanel(). Import direction is clean: pages (both modes) -> widgets; widgets import only src.data/src.updater/src.ui.theme and never import pages.

**Depends on:**
- src/ui/widgets/updates_panel.py
- src/ui/widgets/maintenance_panel.py

**Settings keys:**
- updates.github_repo / updates.last_checked (now written by the panel via update_section merge, not set_section)

**Verify:** `python -m pytest tests/test_system_panels.py tests/test_settings_subtabs.py -x -q`

### `src/ui/widgets/credentials_panel.py`  (M)

Google Drive card gains its own service-account status line fed by the new off-thread DriveProbeWorker (previously only the OAuth line showed, so a healthy SA key read as broken on builds without a bundled GCP client). Also a correctness fix: saving the SA key now ASSIGNS drive.auth_type='service_account' (setdefault left a stale 'oauth_user' from a prior OAuth connect, making DriveReader.is_configured() take the wrong branch).

**Key details:** New state: _sa_probe_worker, _sa_probed_once; new label _sa_status. showEvent() probes once when 'external' in sections (NEVER from __init__/refresh — constructing the panel in tests without an event loop crashed the interpreter at teardown, 0xC0000409). _probe_sa(): single-flight, RuntimeError catch for a deleted C++ wrapper, worker parented to self, finished -> _on_sa_probe THEN deleteLater (connection order load-bearing: the slot drops the Python ref before delete). _format_sa_status is pure+static (table-testable): distinguishes auth-failure vs the crucial '0 files = fresh key, share a folder with <account>' case vs 'N+ files visible'. Save path probes only when isVisible().

**Depends on:**
- src/ui/widgets/drive_probe_worker.py (this slice)
- src/data/drive_reader.py from_settings/_auth_type/_build_service/test_connection (changed in delta, data slice)
- src/data/google_access.py service_account_email

**Settings keys:**
- enablement.drive.credentials_path (written)
- enablement.drive.auth_type (now force-assigned 'service_account' on save)
- enablement.drive.read_enabled (setdefault True)

**macOS risks:**
- The SA key file path stored in the Mac's own gitignored settings — the port must not touch data/; the auth_type assign only happens when the user clicks save

**Verify:** `python -m pytest tests/test_drive_sa_status.py -x -q`

### `src/ui/widgets/drive_probe_worker.py`  (A)

New QThread worker answering two questions for the credentials card: does the service-account key authenticate, and how many files can it SEE (a fresh SA sees 0 — normal, not an error). Blocking network calls (DriveReader.test_connection + files().list pageSize=100) kept off the Qt main thread.

**Key details:** class DriveProbeWorker(QThread) with finished = Signal(dict) — NOTE this deliberately shadows QThread's built-in no-arg finished signal; consumers connect to the dict signal (deleteLater tolerates the extra arg). run() never raises: emits {ok, count, capped, account, detail} (detail truncated to 160 chars). _PROBE_PAGE_SIZE=100 (renders '100+'). Only probes the service_account branch (reader._auth_type check); OAuth has its own status. _account() delegates to google_access.service_account_email — the one place allowed to open the key file (client_email only, never the private key). No main-thread violations: the emitting thread is the worker, delivery to the panel slot is queued cross-thread.

**Depends on:**
- src/data/drive_reader.py (DriveReader.from_settings, test_connection, _build_service — private-surface coupling)
- src/data/google_access.py service_account_email
- google-api-python-client installed in the Mac venv (arm64)

**Settings keys:**
- enablement.drive.* (indirectly via DriveReader.from_settings)

**macOS risks:**
- Custom 'finished' shadows QThread.finished — if the Mac agent 'cleans up' the shadowing, the panel's dict slot breaks; leave it
- A hung network keeps the QThread alive at app close — accepted by design (Qt parent teardown), do not add blocking wait()

**Verify:** `python -m pytest tests/test_drive_sa_status.py -x -q (contains the pure _format_sa_status table tests)`

### `src/ui/widgets/maintenance_panel.py`  (A)

New shared MaintenancePanel widget (extracted verbatim from product Settings' Display tab): memory profiler card (Take Snapshot / Force GC via src.data.memory_profiler.MemoryProfiler) + Full Database Reset danger zone behind a typed-"DELETE" QInputDialog confirm; reaches the hosting window via self.window()._clear_all_data() (MainWindow in both modes).

**Key details:** MaintenancePanel(QWidget), no signals, no settings writes. _full_database_reset uses hasattr(main_window,'_clear_all_data') and optionally updates main_window.ticket_count_label. Snapshot path uses MemoryProfiler.is_started/start/snapshot/format_report and _get_process_rss_mb (private).

**Depends on:**
- src/data/memory_profiler.py (pre-existing)
- MainWindow._clear_all_data (pre-existing)

**macOS risks:**
- RSS reads in MemoryProfiler are platform-dependent (pre-existing module — psutil/resource path should already handle darwin; verify snapshot doesn't show N/A on the Mac)
- Full Database Reset deletes the Mac's LOCAL warehouse — the panel must exist but nobody should click it during port verification

**Verify:** `python -m pytest tests/test_system_panels.py -x -q`

### `src/ui/widgets/updates_panel.py`  (A)

New shared UpdatesPanel widget (extracted verbatim from product Settings' Updates tab): check/install/restart auto-update flow, GitHub repo + PAT config, crash-report export, and manual rollback. Two behavior fixes baked in: updates-section writes now use update_section (MERGE — a bare set_section clobbered github_repo vs last_checked in a write-write cycle so the repo never persisted), and Updater.stage() is passed token= (a private-repo asset 404s without it, even after a successful manifest fetch).

**Key details:** UpdatesPanel(QWidget). Deferred credential read: showEvent loads GitHub repo + PAT once (constructor touches only settings.yaml last_checked + rollback state). Key calls: UpdateChecker(parent, releases_url=f'https://api.github.com/repos/{repo}/releases/latest', github_pat=...) with signals update_available/up_to_date/check_failed; install resolves the platform artifact via manifest_fetcher.resolve_release_artifact(checker.last_assets, token=checker.last_token) — require_checksum guard refuses unchecksummed installs; Updater.stage(url, expected_sha256, new_version, token=); restart via src.updater.restart.restart_app behind a QMessageBox confirm; rollback via src.updater.rollback.current_state/perform_rollback; crash export via src.core.crash_handler.list_reports/export_bundle (zip, QFileDialog.getSaveFileName). PAT stored via pat_store.save_setting('github_pat'). Fallback download URL pattern alma-insights-v{ver}.zip is only used when the manifest path is skipped.

**Depends on:**
- src/updater/updater.py stage(token=) signature (same delta)
- src/updater/update_checker.py last_assets/last_token/normalize_github_repo (same delta)
- src/updater/manifest_fetcher.py resolve_release_artifact (same delta)
- src/updater/restart.py + rollback.py (restart changed in delta)
- src/data/settings_manager.update_section (pre-existing at base)
- src/data/pat_store.py github_pat
- src/core/crash_handler.py (pre-existing)

**Settings keys:**
- updates.github_repo (merged write)
- updates.last_checked (merged write)

**macOS risks:**
- Install path depends on the release's manifest.json declaring a macOS/arm64 artifact — resolve_release_artifact is 'platform-specific'; on the Mac an update check will succeed but Install must resolve a mac asset or fail with a readable message (updater slice owns that logic — verify tests/test_manifest_fetcher.py covers darwin)
- restart_app spawns the replacement process — its darwin behavior lives in src/updater/restart.py (updater slice); the panel only calls it

**Verify:** `python -m pytest tests/test_system_panels.py tests/test_updater.py tests/test_manifest_fetcher.py -x -q`

### `src/startup/splash_window.py`  (M)

Splash brands per startup mode via a frozen _Branding dataclass: product keeps 'ALMA INSIGHTS / RCM ISSUE ANALYSIS / RCM Operations'; enablement shows 'CONTENT COMMAND CENTER / AI-POWERED CONTENT WORKSPACE / Content Command Center'. SplashWindow.__init__ gains mode: str|None — None (production path) resolves via app_modes.resolve_startup_mode() (honors --mode CLI + app.default_mode/app.last_mode from the machine's OWN settings); ANY failure falls back to product branding (splash must never crash over a label).

**Key details:** New: _Branding dataclass (window_title, header_title, tagline, footer_suffix), _PRODUCT_BRANDING, _ENABLEMENT_BRANDING, _branding_for_mode(mode) ('enablement' -> CCC else product), _resolve_branding(mode). Footer renders f'{app_version} — {footer_suffix}'. main.py constructs without mode (self-resolving).

**Depends on:**
- src/ui/app_modes.py resolve_startup_mode (pre-existing)

**Settings keys:**
- app.default_mode / app.last_mode (read indirectly; the Mac's gitignored settings decide — the delta forces nothing)

**Verify:** `python -m pytest tests/test_splash.py -x -q`

### `src/startup/update_action_widget.py`  (M)

Two splash-update fixes: (1) Updater.stage() now receives token=self._payload.get('token','') — required for private-repo assets which 404 without auth even after a successful manifest fetch; (2) after restart_app() the widget explicitly closes self.window() so the splash's NESTED QDialog.exec() unwinds — QApplication.quit() cannot unwind a nested exec, and without this the old process stayed alive beside the freshly-spawned replacement (the 'second window' bug).

**Key details:** stage(url, expected_sha256=sha, new_version=new_ver, token=...). The close runs only on the success path (early return after _show_error on restart failure). _payload must carry 'token' — produced by the startup update check (updater slice).

**Depends on:**
- src/updater/updater.py stage(token=) (same delta)
- startup check payload including 'token' (updater/startup slice)

**macOS risks:**
- restart_app's replacement-process spawn is the platform-sensitive part (updater slice); the nested-exec close fix itself is platform-neutral and load-bearing on macOS too

**Verify:** `python -m pytest tests/test_update_action_widget.py -x -q`

---

### Slice-level notes — qt-ui

**Hard ordering constraints (matter only for piecemeal moves; a full checkout satisfies them all):**
- src/branding.py before splash_window.py, main_window.py, home_page.py (all import it at module or call time).
- src/ui/pages/enablement/_common.py (style_native_dialog / native_dialog_button_qss) before drive_folder_picker_dialog.py, enablement page.py, enablement settings.py — all import the new helpers.
- updater slice (updater.py stage(token=), update_checker last_assets/last_token, manifest_fetcher.resolve_release_artifact, restart.py) before src/ui/widgets/updates_panel.py and src/startup/update_action_widget.py — both call the new signatures and will TypeError without them.
- data slice enablement_store card-template API (get/set/clear/list_card_templates, set_active/delete_card_template, update_style_guide_text, update_card_template_text, save_document(source_ref=)) before enablement page.py + settings.py — page.py calls them unguarded in _guide_action.
- src/data/help/* + migration 048 + assets/help/** before help_tab.py first open (help_tab degrades to 'not loaded' without them, but the claims tests fail).
- src/data/renn_usage.py before usage_tab.py (no migration needed — rows live in existing gemini_usage).
- src/services/agent_chat.py (DriveListWorker, list_picker_roots) + src/data/google_access.py before drive_folder_picker_dialog.py and drive_probe_worker.py.
- src/services/chat_session.py resolve_or_create_session and src/services/chat_engine.py stream= kwarg before enablement page.py (constructor calls both).
- app_modes.py PageSpecs (en_attention/en_help) and page.py's help tab must land together with main_window.py's _show_help routing — main_window routes to 'en_help' which must exist as a spec AND a tab_key.
- settings_page.py (product) must be ported in the same step as updates_panel.py/maintenance_panel.py — it deletes ~810 lines whose only remaining home is the new widgets; porting the deletion first leaves product Settings without an Updates surface.
- Extraction order within enablement page.py: keep _setup_engine() BEFORE _build() (web chat bridge registration is once-only); do not 'restore' the old order.
- src/__init__.py VERSION bump can land anytime but must land before testing the update-check flow (baseline comparison).

**Cross-file risks / silent failures:**
- Silent mode-forcing: NOTHING in this delta forces enablement mode in code. resolve_startup_mode honors the machine's own app.default_mode/app.last_mode. The dev box booted product-first only because its gitignored data/settings.yaml was edited to default_mode: last — do NOT replicate that on the Mac; its data/ dir is untouchable per the port constraints.
- settings.py _maybe_build_usage triggers on tabText(index)=='Usage' string match — renaming/translating the tab label silently keeps the placeholder forever. Looks done, is not.
- Enablement docstrings/comments say the Usage tab hosts the 'shared CostDashboard'; the code builds RennUsagePanel. A literal-minded porter reconciling docstring-to-code in the wrong direction would resurrect the wrong widget.
- page.py __init__ order (_setup_engine before _build) is load-bearing only when enablement.web_tabs is on — with flags off (Mac default) a wrong port passes every visible check and the Renn web drawer is dead the day the flag flips.
- main_window._show_feedback (enablement) silently falls back to the product HelpDialog when the enablement page has not been mounted yet (_page_widgets.get('en_help') is None) — works-on-second-click behavior that can masquerade as a port bug.
- DriveProbeWorker.finished deliberately shadows QThread.finished with Signal(dict); the connection order (_on_sa_probe BEFORE deleteLater) and ref-drop-in-slot are crash-avoidance, not style — reordering reintroduces the 'Internal C++ object already deleted' crash.
- DriveFolderPickerDialog is cached and REUSED by settings.py (never destroyed per open) so a running DriveListWorker never loses its Qt parent; a porter 'simplifying' to construct-per-open reintroduces a native-crash window.
- UpdatesPanel/update_action_widget: forgetting token= reproduces the exact prior field bug (manifest fetch succeeds, asset download 404s) — it fails only against a private repo, so local testing against a public repo will look green.
- updates section writes MUST stay update_section (merge): the settings_page import line added update_section for this reason; reverting to set_section resurrects the github_repo/last_checked mutual-clobber.
- credentials_panel drive['auth_type'] = 'service_account' is assign-not-setdefault on purpose; setdefault reintroduces the stale-oauth_user misreport.
- help_tab reload() calls loader.sync_bundled_help every time (sync-when-BEHIND); if the loader port keeps the old presence-gated logic the Mac warehouse freezes at whatever article count it first loaded (the 8-vs-62 field bug).
- main_window sidebar: _populate_sidebar now re-adds a trailing stretch on every rebuild because the clear loop drops it — omitting that addStretch centers the nav entries vertically after the first mode switch only.
- gemini_chats_page use_mcp_tools=False + set_mcp_config([]) is a matched pair; porting one without the other leaves the model either toolless or double-configured.

**macOS-specific:**
- No wmic/taskkill/CREATE_NO_WINDOW/%APPDATA%/.exe/backslash paths/cp1252 assumptions anywhere in this slice; all explicit file reads pass encoding='utf-8'.
- QFileDialog calls in page.py deliberately start at os.path.expanduser('~') BECAUSE macOS native dialogs otherwise open on an opaque default location — keep the argument.
- The blank-button dialog fix (style_native_dialog) applies to Qt-drawn QMessageBox/QInputDialog and behaves identically on macOS (these are not NSAlert); macOS-native QFileDialog sheets are unaffected by the QSS and need nothing.
- Keep ui.web_home and enablement.web_tabs OFF on the Mac during the port (they default off and fail closed); the main_window watchdog exists specifically for the macOS blank-render class (renderer dies with loadFinished ok, window.__almaHomeMounted never set). If flags are ever flipped, run python scripts/web_diag.py first and npm --prefix web run build.
- Updates flow on the Mac requires the GitHub release's manifest.json to declare a darwin/arm64 artifact — resolve_release_artifact picks per-platform; verify with tests/test_manifest_fetcher.py and do NOT test Install Now against a real release on the work Mac.
- Usage tab will show $0.00 cost on the Mac's subscription 'claude' CLI login — documented behavior (the CLI reports no dollar figure), not a defect.
- qss.py sidebar min-heights were tuned on Windows; do a visual pass on macOS for clipped descenders (different font metrics), but do not change values preemptively.
- Case-sensitive filesystem check: assets/help/**, assets/templates/support_center_article_template.md, and the parents[4] path resolution in page.py are all lower-case and correct as committed — preserve exact casing.
- Do not touch the Mac's data/ directory: this slice writes settings only through settings_manager on user action (drive save, KB toggle, repo save); no port step should pre-seed enablement.help.bug_form_url, enablement.kb.*, updates.*, or app.default_mode.

**Analyst notes / answered questions:**
- Branding question answered: src/branding.py exports CONTENT_COMMAND_CENTER + CONTENT_COMMAND_CENTER_DESCRIPTION; consumed by splash_window (mode-resolved _Branding), main_window chrome (subtitle + sidebar footer, retexted on switch_mode), home_page banner (visible only in enablement), and the Help article pinned by tests/test_help_claims_getting-started.py. Branding is fully mode-conditional — product surfaces are unchanged.
- Shared-panels question answered: UpdatesPanel + MaintenancePanel (src/ui/widgets/) are hosted by BOTH product settings_page.py and enablement settings.py; import direction is pages -> widgets only, widgets depend on src.data/src.updater/src.ui.theme and never on any page — no cycle. The Usage tab is NOT shared: product keeps CostDashboard, enablement gets the new RennUsagePanel.
- Native-dialog fix answered: root cause is the enablement pages' selectorless cream background cascading into Qt-drawn native dialogs, leaving app-QSS white text on cream (invisible). Fix must be set ON the dialog (ancestor rules lose by proximity). Not platform-sensitive — same on macOS; macOS-native QFileDialog sheets were never affected.
- app_modes answered: only two PageSpec additions; resolve_startup_mode unchanged; the delta forces no mode. The Mac's own settings decide the boot mode.
- drive_probe_worker answered: QThread subclass, custom finished=Signal(dict) (shadows built-in), started only from showEvent/visible-save, parented, single-flight, ref dropped in-slot before deleteLater — no main-thread violations; UI updates happen in the queued slot on the main thread.
- Unverified: whether tests/test_enablement_ui_live.py and tests/test_enablement_live_cutover.py (in the delta) are in the known env-failure list from memory — the plan author should consult memory/enablement_settings_system_tabs.md before treating their failures on the Mac as port regressions.
- Unverified: WorkbenchPage (native Qt) set_existing_cards([]) tolerance — page.py now always calls it with possibly-empty lists; the native page pre-dates this. Worth a 1-line check during the port (src/ui/pages/enablement/workbench.py).
- Minor: main_window._show_feedback's fallback-to-HelpDialog when en_help is unmounted may be intentional or an accepted edge; no test pins it.


## 6. Infra — main.py, updater, installer, CI, docs, eval scripts

### `main.py`  (M)

Four independent additions: (1) QtWebEngine init-order fix — sets QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True) and eagerly imports PySide6.QtWebEngineWidgets at module top, BEFORE QApplication is constructed (guarded try/except ImportError so an Essentials-only env degrades); (2) after apply_staged_update() returns True, importlib.invalidate_caches() + importlib.reload(src) so src.VERSION reflects the just-applied update (kills the 'update re-offers itself' loop); (3) app.setApplicationVersion is set from src.VERSION (was hardcoded '1.0.0') after any staged update is applied, so the splash shows the real version; (4) after _run_splash(app) returns, checks src.updater.restart.restart_requested() and sys.exit(0) before building MainWindow — stops the old process surviving a splash 'Install & Restart' (the splash is a nested QDialog.exec(), so QApplication.quit() is a no-op there). Answering the task question: NO splash/branding code and NO ALMA_WEB_DIAG hook changed in main.py in this range; --mode CLI parsing pre-existed at 7adbe7b.

**Key details:** New import site: `from src.updater.restart import restart_requested` inside main() after _run_splash. Reload block: `import src as _src_pkg; importlib.reload(_src_pkg)` wrapped in bare except (never blocks startup). Version block: `from src import VERSION as _RUNNING_VERSION; app.setApplicationVersion(_RUNNING_VERSION)`. WebEngine block sits between the PySide6 imports and `from src.ui.theme import get_stylesheet` at lines ~34-45. Commits: 79e8bd7 (reload), d2bc239 (restart guard), b5d241a (real version), 34545cd (WebEngine init).

**Depends on:**
- src/updater/restart.py (restart_requested() — new function, same delta)
- src/updater/updater.py (apply_staged_update, pre-existing)
- src/__init__.py VERSION bumped to "1.0.7" in this range (NOT in this slice's file list — must travel or splash shows 1.0.0)
- PySide6 QtWebEngine (PySide6-Addons) installed for the eager import to succeed; degrades gracefully if absent

**macOS risks:**
- Eager QtWebEngineWidgets import requires an arm64-native PySide6 with intact QtWebEngine frameworks — on the Mac this is exactly the surface that previously blank-rendered under Rosetta/framework corruption (see memory macos_agent_blank_render); the import itself is ImportError-guarded but a corrupt (non-ImportError) framework failure would crash at startup rather than at first Agent-tab open
- AA_ShareOpenGLContexts before QApplication is required on macOS too — do not reorder when porting

**Verify:** `python main.py boots to splash with no 'AA_ShareOpenGLContexts must be set before QApplication' warning in the console, and the splash footer shows 1.0.7 (not 1.0.0).`

### `src/updater/manifest_fetcher.py`  (M)

Private-repo release support (commit 7ce570a): accepts BOTH manifest asset names ('manifest.json' and 'release_manifest.json' — CI uploads the latter); prefers the GitHub API asset url ('.../releases/assets/<id>', Bearer-authenticable, works on private repos) over browser_download_url; and when the URL is an API asset URL, sends Accept: application/octet-stream (else GitHub returns asset JSON metadata instead of the file).

**Key details:** New module constant _MANIFEST_ASSET_NAMES = ("manifest.json", "release_manifest.json") (old _MANIFEST_ASSET_NAME kept). _find_manifest_url and _find_artifact_url now return asset.get("url") or asset.get("browser_download_url"). _fetch_manifest_json computes is_api_asset = 'api.github.com' in url and '/releases/assets/' in url to pick the Accept header.

**Depends on:**
- src/updater/release_manifest.lookup_artifact (pre-existing)
- scripts/make_release_manifest.py + .github/workflows/release.yml naming the asset release_manifest.json

**Settings keys:**
- updates.github_repo (read indirectly via update_checker)
- updates.auth_mode

**macOS risks:**
- None — pure stdlib urllib, platform-neutral

**Verify:** `python -m pytest tests/test_manifest_fetcher.py -x -q (or the nearest updater test group); functional check is Settings > check-for-updates against the private repo resolving a manifest without 'missing manifest.json'.`

### `src/updater/restart.py`  (M)

Commit d2bc239: adds module-global _restart_requested flag + public restart_requested() -> bool, set True in restart_app() right after the detached replacement process is spawned. main.py polls it after the splash returns and exits, because QApplication.quit() cannot unwind the splash's nested QDialog.exec() — without this, splash 'Install & Restart' left the old app alive and a second window opened.

**Key details:** def restart_requested() -> bool at module level; `global _restart_requested; _restart_requested = True` inside restart_app() after subprocess spawn, before the QApplication.quit() attempt. Pre-existing Windows-only constants _WIN_DETACHED_PROCESS / _WIN_CREATE_NEW_PROCESS_GROUP untouched (used only on win32 branch).

**Depends on:**
- main.py's restart_requested() check (same delta)
- src/startup/update_action_widget.py b5d241a change (OTHER slice): the widget must close the splash dialog after spawning, or the nested exec() never returns and the main.py guard is unreachable

**macOS risks:**
- None new — the spawn path's Windows creationflags are in the pre-existing platform branch; the new flag/getter is pure Python

**Verify:** `python -m pytest tests/test_restart*.py tests/test_update_action_widget.py -x -q — test_update_action_widget asserts the token forwarding and splash-close behavior.`

### `src/updater/update_checker.py`  (M)

Two UX/correctness fixes: (1) new public normalize_github_repo(raw) -> str strips https://github.com/, git@github.com:, www., trailing .git and path tails so a pasted repo URL still resolves to owner/repo (previously the API URL became .../repos/https://github.com/... and every check 404'd); applied in _resolve_config_and_token as defence-in-depth on the stored settings value. (2) 404s from /releases/latest are now disambiguated by _diagnose_404(headers): probes the repo URL itself and reports 'no published Releases yet' vs 'token rejected (401/403)' vs 'repo not found / fine-grained PAT missing Contents: Read'.

**Key details:** normalize_github_repo uses re.sub(r"^(?:git@|https?://)?(?:www\.)?github\.com[:/]+", ...). _resolve_config_and_token: repo = normalize_github_repo(cfg.get("github_repo", "")) or f"{DEFAULT_OWNER}/{DEFAULT_REPO}". _diagnose_404 derives repo_url = self._url.split("/releases/")[0] and GETs it with the same headers.

**Depends on:**
- settings section updates (github_repo, auth_mode) via settings_manager

**Settings keys:**
- updates.github_repo
- updates.auth_mode ("disabled"|"pat"|"github_app")

**macOS risks:**
- None — stdlib urllib; performs a network GET on the 404-diagnosis path (harmless read)

**Verify:** `python -m pytest tests/test_update_checker.py -x -q; unit-check normalize_github_repo('https://github.com/owner/repo/tree/main') == 'owner/repo'.`

### `src/updater/updater.py`  (M)

Commit 7ce570a: authenticated release-asset downloads for private repos. Updater.stage() and _do_stage()/_download() gain a token: str = '' keyword; when set, the download sends Authorization: Bearer <token> plus Accept: application/octet-stream, via a custom opener using new class _StripAuthOnRedirect(urllib.request.HTTPRedirectHandler) which drops the Authorization header when GitHub 302s to a different-host signed storage URL (which rejects forwarded auth with a confusing 400). The plain urllib.request.urlopen seam is preserved when token is empty so e2e tests that patch urlopen still work.

**Key details:** Signature: stage(self, download_url, expected_sha256='', new_version='', *, require_checksum=True, token=''). _download(self, url, token=''). _StripAuthOnRedirect.redirect_request compares urlparse(newurl).netloc vs urlparse(req.full_url).netloc and pops 'Authorization'/'authorization' from new.headers and new.unredirected_hdrs. UNCHANGED but load-bearing for the clobber question: _REPLACEABLE_DIRS = ("src", "config", "migrations") and _PROTECTED = {"data", "_update_staging", ".git", ".venv", "venv", "docs", "installer", "tests"}; apply_staged_update() renames live src/, config/, migrations/ to _<dir>_backup, moves staged dirs in, and rewrites VERSION in src/__init__.py.

**Depends on:**
- Callers passing token=: src/ui/pages/settings_page.py (7ce570a) and src/startup/update_action_widget.py (3ed5a62) — both in OTHER slices; token kwarg has a default so updater.py can land first, but the callers' fixes are what make private-repo installs work end-to-end

**Settings keys:**
- updates.* (token resolution happens in callers via update_checker._load_token)

**macOS risks:**
- THE dev-tree clobber danger: apply_staged_update() on a git checkout replaces tracked src/, config/, migrations/ with release payload and rewrites src/__init__.py — the checkout desyncs from git and uncommitted work in those dirs is shunted to _src_backup/_config_backup/_migrations_backup. data/ is _PROTECTED so Mac settings survive, but the plan MUST forbid clicking 'Install & Restart' (splash or Settings) on the Mac dev checkout

**Verify:** `python -m pytest tests/test_updater.py tests/test_update_checker.py tests/test_manifest_fetcher.py -x -q. Never exercise stage/apply against the dev tree.`

### `installer/build_release.py`  (M)

One line: adds "pypdf==6.14.2" to the PACKAGES pin list that the release bundler pip-installs into the embedded Python, matching the requirements.txt addition (enablement KB PDF ingestion).

**Key details:** PACKAGES list, inserted after python-pptx==1.0.2.

**Depends on:**
- requirements.txt pypdf==6.14.2 (same delta)

**macOS risks:**
- None for a dev run — build_release.py only matters when cutting a packaged release; pypdf is pure-Python so no arm64 wheel concern

**Verify:** `grep pypdf installer/build_release.py; for the dev venv: python -c "import pypdf; print(pypdf.__version__)" == 6.14.2.`

### `installer/ci/sign_macos.sh`  (M)

Deep-signing rework for the blank-QtWebEngine-render fix on Apple Silicon: QtWebEngineProcess (inner Mach-O AND its .app bundle) plus the bundled python binaries are now signed with JIT entitlements (com.apple.security.cs.allow-jit, com.apple.security.cs.allow-unsigned-executable-memory, com.apple.security.cs.disable-library-validation) via new sign_jit(); node binaries keep the plain hardened-runtime sign. Prefers Qt's own bundled QtWebEngineProcess.entitlements file, falls back to writing the 3-key plist. Stapling is now loud: if `xcrun stapler staple` fails (always does on .zip) it prints a WARNING that an unstapled quarantined copy will NOT launch offline and that the artifact should be a .dmg/.pkg.

**Key details:** HELPER_ENT resolved by find for '*QtWebEngineProcess.app/Contents/Resources/QtWebEngineProcess.entitlements'; sign_jit() = codesign --force --options runtime --timestamp --entitlements "$HELPER_ENT". Sign order: helper bin -> helper .app -> *.so/*.dylib -> *.framework dirs -> python/bin/* (JIT) -> node/bin/* (plain). Uses BSD find syntax `-perm +111` (macOS-only; would fail under GNU find).

**Depends on:**
- CI secrets: KEYCHAIN, APPLE_SIGNING_IDENTITY, APPLE_APP_PASSWORD (pre-existing)
- .github/workflows/release.yml invoking it

**macOS risks:**
- Runs ONLY in CI signing of packaged artifacts — a plain git-checkout dev run on the Mac needs NONE of this (dev PySide6 wheels are ad-hoc signed by pip and QtWebEngineProcess runs unsandboxed-signed fine)
- If the Mac agent ever hand-signs a bundle, missing the JIT entitlements reproduces the blank-render symptom

**Verify:** `No dev-run action. For a packaged artifact: codesign -d --entitlements - <bundle>/.../QtWebEngineProcess.app shows allow-jit; spctl -a -vv accepts the app.`

### `installer/install.py`  (M)

create_macos_app_bundle() hardened against the Rosetta blank-render class (QTBUG-98487): Info.plist gains LSRequiresNativeExecution=true and LSArchitecturePriority=[arm64]; the generated launch script now (a) prepends $HOME/.local/bin to PATH so the `claude` CLI is reachable from a Finder-launched app (launchd strips PATH), and (b) execs `arch -arm64 <python> <main.py>` when `sysctl -n hw.optional.arm64` == 1 (belt-and-braces vs a translated shell reaching the universal2 QtWebEngineProcess grandchild).

**Key details:** Function create_macos_app_bundle(install_dir, version=None). Launch script line: export PATH="{node_bin}:$HOME/.local/bin:$PATH"; the pre-existing xattr -rd com.apple.quarantine lines on python/ and node/ remain.

**Depends on:**
- Packaged-install flow only (installer zip); irrelevant to `python main.py` from the git checkout

**macOS risks:**
- None negative — this file IS the macOS fix. For the dev checkout the equivalent manual checks are: `python3 -c "import platform; print(platform.machine())"` must print arm64, and the `claude` CLI must be on PATH in whatever shell launches the app

**Verify:** `Packaged only: plutil -p <App>.app/Contents/Info.plist shows LSRequiresNativeExecution; Activity Monitor shows the app as Apple (not Intel). Dev checkout: platform.machine() == 'arm64'.`

### `installer/launcher_mac.command`  (M)

Same two fixes as install.py's launch script, for the zip-distribution launcher that build_release.py bundles: adds $HOME/.local/bin to PATH (claude CLI reachability) and execs `arch -arm64 $DIR/python/bin/python3 $DIR/app/main.py` when hw.optional.arm64==1, falling through to plain exec otherwise.

**Key details:** Consumed at installer/build_release.py:510 (copied into the release layout). 6 lines changed.

**Depends on:**
- Packaged zip layout ($DIR/python, $DIR/node, $DIR/app) — not used by a git-checkout dev run

**macOS risks:**
- None — macOS-only script; no action needed for the dev port

**Verify:** `Packaged only: double-click launch runs native arm64 (Activity Monitor 'Kind: Apple').`

### `.github/workflows/build.yml`  (M)

Node 20 -> 24 for the web UI build (commit 0cd75ab: match the npm that generated web/package-lock.json) and a new CI step 'Test web UI (vitest)': `npm --prefix web run test` after `npm --prefix web ci && npm --prefix web run build`.

**Key details:** actions/setup-node@v4 with node-version: '24'.

**Depends on:**
- web/package-lock.json committed (commit 38b4c20, OTHER slice) — npm ci fails without it
- web/ package.json test script (vitest)

**macOS risks:**
- Only relevant if the Mac agent rebuilds the web bundle locally: use Node 24 (or at least an npm that accepts the committed lockfile) for `npm --prefix web ci`

**Verify:** `CI-side; locally: npm --prefix web ci && npm --prefix web run build && npm --prefix web run test all green under Node 24.`

### `.github/workflows/release.yml`  (M)

Identical pair of changes to build.yml: node-version '20' -> '24' and the added vitest step in the release job's web-build sequence.

**Key details:** Same step names/order as build.yml.

**Depends on:**
- web/package-lock.json committed; release job is `needs: build` so build.yml must pass first

**macOS risks:**
- None locally

**Verify:** `CI-side only — next v* tag publishes a release (previously npm ci failed in ~12s and no release was ever published).`

### `.gitignore`  (M)

Adds `!web/package-lock.json` exception under the blanket package-lock.json ignore, with a comment explaining the release workflow's `npm ci` REQUIRES a committed lockfile (its absence made the build job fail and no release ever published).

**Key details:** 4 lines (3 comment + 1 negation) appended after the existing package-lock.json ignore at ~line 159.

**Depends on:**
- web/package-lock.json itself (committed in 38b4c20, OTHER slice)

**macOS risks:**
- None; just ensure the port copies .gitignore BEFORE any git add of web/package-lock.json on the Mac, or the add silently no-ops

**Verify:** `git check-ignore -v web/package-lock.json returns nothing (i.e., not ignored).`

### `requirements.txt`  (M)

Adds exactly one top-level dep: pypdf==6.14.2 (enablement KB ingestion reads PDF policy docs from Drive; pure Python, no new transitives — typing_extensions already pulled). Nothing else changed.

**Key details:** Consumed lazily at src/data/drive_reader.py:483 (`from pypdf import PdfReader` inside a function) and by src/data/kb/ingest.py's doc-extraction path — missing dep breaks PDF extraction at call time, not app startup.

**macOS risks:**
- None — pure-Python wheel, arm64-safe. Must be pip-installed into the Mac venv as part of the port

**Verify:** `pip install -r requirements.txt (or pip install pypdf==6.14.2); python -c "from pypdf import PdfReader; print('ok')".`

### `requirements.lock`  (M)

Adds pypdf==6.14.2 as a BARE pin (no hashes yet) with a comment saying the hashes get filled by the next M1 pip-compile run — the lockfile's own header documents that regeneration happens on the macOS M1 production machine via pip-tools in a .lockenv venv.

**Key details:** 4 added lines: 3-line comment + `pypdf==6.14.2`. The file is otherwise hash-pinned; this entry is intentionally incomplete pending the M1 pass.

**Depends on:**
- requirements.txt pypdf pin

**macOS risks:**
- A --require-hashes install from this lock will FAIL until the M1 pip-compile regeneration adds the pypdf hashes — the Mac is the designated machine for that step

**Verify:** `Optional Mac step: python -m venv .lockenv && pip install pip-tools && regenerate per the file header, confirming pypdf gains --hash lines.`

### `CLAUDE.md`  (M)

+73 lines: inserts the 'Enablement Web Pivot — React/QtWebEngine tabs (M0–M6, 2026-07-14)' section (the one visible in the current CLAUDE.md): enablement.web_tabs flag semantics, ui.web_home boot-page rationale and watchdog, the four load-bearing architecture rules (pure renderer, QWebChannel trust boundary, sanitize_html+sandboxed iframe, bridge-parenting), key file map, web_diag diagnostics, and the run-singly WebEngine test rule. Doc-only; copy verbatim.

**Key details:** Section lands between the 'AI Reports' section and 'Bug Bash Protocol'. References scripts/web_diag.py, scripts/verify_web_pivot_mac.sh, ALMA_WEB_DIAG=1, tests/test_web_guardrails.py, tests/test_home_web_controller.py.

**Depends on:**
- The web-pivot code files it describes (other slices) — but the doc can land any time

**Settings keys:**
- enablement.web_tabs (off|calendar|all, documented)
- ui.web_home (documented, defaults off)

**macOS risks:**
- None

**Verify:** `grep -c 'Enablement Web Pivot' CLAUDE.md == 1.`

### `docs/ENABLEMENT_ROADMAP.md`  (M)

Adds a 'Built — Enablement Web Pivot' entry ahead of the existing Connect & Configure section. NOTE A DOC BLEMISH: the diff introduces TWO overlapping web-pivot sections and the second is truncated mid-sentence ('flag off, W') — harmless but the porter should copy the file as-is rather than 'fixing' it (or flag to owner).

**Key details:** Doc-only. Duplicated header text: '## Built — Enablement web pivot: Calendar + Workbench + Renn drawer in React/QtWebEngine'.

**Settings keys:**
- enablement.web_tabs (documented)

**macOS risks:**
- None

**Verify:** `File byte-identical to the reference branch copy.`

### `docs/DRIVE_SEARCH_EVAL.md`  (A)

325-line doc for the Drive-search evaluation harness. Explicitly MEASURE-ONLY: the defects it lists are deliberately unfixed and pinned by xfail tests that start failing when someone fixes them (intended signal). Carries a load-bearing PHI warning: kb index mode sends extracted Drive text to Haiku via the local claude CLI with aggressive PII redaction disabled — the target Drive must be synthetic.

**Key details:** Documents the 3-stage funnel (find folder -> live Drive search -> mirror/FTS search) and the gold.yaml / judgments workflow used by scripts/run_drive_eval.py.

**Depends on:**
- scripts/drive_eval_preflight.py, scripts/run_drive_eval.py, tests/drive_eval (other slice)

**Settings keys:**
- enablement.drive.active_folders (referenced)

**macOS risks:**
- None (doc)

**Verify:** `Present; links resolve within the repo.`

### `docs/KB_ARCHITECTURE.md`  (A)

110-line WS2 knowledge-base architecture doc: self-building, human-readable EC/ folder in the operator's Google Drive (drive.file scope) as cloud source of truth, local SQLite mirror for search; retrieval is FTS5 + code-ranked hybrid + entity-dictionary expansion with a full-text floor; NO embedding models (product-owner decision 2026-07-13).

**Key details:** Describes kb_folders write-allowlist enforcement (single chokepoint src/data/kb/drive_kb.py) matching migration 046.

**Depends on:**
- migrations/046_kb.sql, src/data/kb/* (other slices)

**macOS risks:**
- None (doc)

**Verify:** `Present.`

### `docs/MAC_STYLE_GUIDE_TEMPLATE_GUIDE.md`  (A)

PRIOR-ART PORTING GUIDE (123 lines), audience = the Claude session on the macOS checkout. Covers ONLY commit 870fade (style-guide rendered preview/inline editor + Card/Article Template section in enablement Settings) — a small subset of this full delta. Its stance is 'verify, not re-implement': everything travels as git-tracked files with no Windows-specific code; it calls out two macOS-specific behaviors to check (native dialogs/pickers).

**Key details:** Note it was later FOLDED INTO the three-pillar guide by commit fa7e266 ('fold the style-guide/template pass into the surgical patch guide') — THREE_PILLAR_SURGICAL_PATCH_GUIDE.md supersedes it as the master prior-art doc.

**macOS risks:**
- None (doc) — but the new plan should reconcile with it rather than duplicate its steps

**Verify:** `Present.`

### `docs/QWEN3_EMBEDDINGS.md`  (A)

406-line historical technical handoff for the Qwen3-Embedding-0.6B semantic layer used by the PRODUCT-side canonicalization/enrichment work (dated 2026-06-10). Purely documentary; note the tension it does NOT create: the enablement KB deliberately rejected embeddings (KB_ARCHITECTURE.md), this doc describes the separate product pipeline.

**Key details:** No code dependency introduced by this range.

**macOS risks:**
- None (doc). If its instructions are ever executed: model runs via MPS on M1, never CUDA (durable project fact)

**Verify:** `Present.`

### `docs/RCM_AGENT_ARCHITECTURE.md`  (A)

269-line draft design proposal (2026-07-07) for a Claude-on-Bedrock RCM ticket-resolution agent (triage -> ground -> draft -> verify -> gate -> act; human gate before any provider-facing action). No code in this range implements it; doc-only.

**Key details:** Cross-links SECURITY_COMPLIANCE_REVIEW.md and WORKCLAUDE_IMPLEMENTATION_GUIDE.md.

**macOS risks:**
- None (doc)

**Verify:** `Present.`

### `docs/SESSION_SUMMARY.md`  (A)

71-line session handoff doc written for a fresh model pass. Part 3 is the then-live macOS QtWebEngine blank-render problem with a 'Ruled out — do NOT re-try' list; also summarizes the security sweep findings (redaction fails open, telemetry not disabled, secrets cluster) and records that the redaction/corpus workstream stayed deliberately uncommitted.

**Key details:** States repo was pushed @ 7adbe7b and that twin branch enablement-surgical-updates exists for the workclaude surgical pass — directly relevant context for whoever writes the Mac plan.

**macOS risks:**
- None (doc); its 'ruled out' list should be honored by the Mac agent when debugging any residual blank render

**Verify:** `Present.`

### `docs/STARTUP_SPLASH.md`  (M)

6-line doc update: documents the 2026-07-22 mode-aware splash branding — header/title/footer resolved via app_modes.resolve_startup_mode() (product: 'ALMA INSIGHTS / RCM ISSUE ANALYSIS'; enablement: 'CONTENT COMMAND CENTER'), strings in src/branding.py and splash_window._ENABLEMENT_BRANDING, any resolution failure falls back to product branding.

**Key details:** The code it documents (src/branding.py, src/startup/splash_window.py) is in OTHER slices; --mode is parsed in main.py before the splash (pre-existing).

**Depends on:**
- src/branding.py + splash_window changes (other slice)

**macOS risks:**
- None (doc)

**Verify:** `python main.py --mode enablement shows CONTENT COMMAND CENTER on the splash.`

### `docs/THREE_PILLAR_SURGICAL_PATCH_GUIDE.md`  (A)

PRIOR-ART PORTING GUIDE (566 lines) — the direct predecessor of the plan now being written. Audience: 'work Claude' patching the Mac TARGET from a downloaded REFERENCE checkout via targeted file copies / anchor-verified surgical edits, never broad overwrite. Scope: the 2026-07-13/14 three-pillar build (WS1 live Asana calendar, WS2 Drive KB, WS3 content studio = commit 1b5f951, migrations 043-047) PLUS the 870fade demo pass (style guide + template + macOS picker fixes, Phase C2). It does NOT cover the rest of this delta: web tabs/web Home/Help Center (34545cd), drive search FTS + eval (aba4337/39b8816, mig 048-050), the 5-commit updater chain, CCC branding, system tabs/Renn metering (dbb5c8f), or the Drive picker (4313f53).

**Key details:** The new per-file plan should treat this guide's file lists and anchors as authoritative for the 1b5f951+870fade subset and only author fresh instructions for the newer commits.

**macOS risks:**
- Risk of the new plan CONFLICTING with this guide if both are handed to the Mac agent — the plan must state which supersedes

**Verify:** `Present.`

### `scripts/drive_eval_preflight.py`  (A)

324-line read-only readiness probe for the Drive search eval: no writes, no LLM, no network — reads data/settings.yaml via settings_manager, stats paths, asks google_oauth for session state; reports reachability of the 3 funnel stages. Secrets are never printed (length-masked _mask fingerprint, idiom copied from scripts/validate_live_integrations.py:50-56). Refuses to run when ALMA_MCP_MODE is set (google_oauth hard-raises there by design).

**Key details:** CLI: `python scripts/drive_eval_preflight.py [--json]`. Inserts repo root into sys.path itself; reconfigures stdout/stderr to utf-8 errors=replace (Windows-console guard, harmless on Mac).

**Depends on:**
- src/data/settings_manager, src/data/google_oauth, src/data/drive_reader (other slices)
- the Mac's existing data/settings.yaml — READ ONLY, satisfying the do-not-touch constraint

**Settings keys:**
- enablement.drive.active_folders (read)
- enablement.drive.* auth config (read, masked)

**macOS risks:**
- None — pure Python + repo-relative paths; safe first smoke command on the Mac

**Verify:** `python scripts/drive_eval_preflight.py --json exits 0 and prints stage reachability without leaking secrets.`

### `scripts/run_drive_eval.py`  (A)

492-line LIVE eval runner (script, never collected by pytest — testpaths=tests). Drives list_drives -> list_folders -> set_drive_folder -> index -> search with PROOF-OF-CALL semantics (ProbedReader records whether a Google service object was actually built; service_built=False stages print UNPROVEN). Default --index-mode mirror writes ONLY to data/local_warehouse.db (no LLM, no Drive writes); --index-mode kb runs the real KB ingest — sends doc text to Haiku via the local `claude` CLI and CREATES folders + uploads a .kb_marker file in Drive — and requires --yes-i-understand-this-writes.

**Key details:** Flags: --list-drives | --folder-id <ID> --index-mode mirror|kb | --queries <gold.yaml|.txt> --search-mode ... --report <md> --judgments <csv>. With a .txt queries file it emits <report>.judgments.csv for manual marking. Scores recall@k against gold.yaml.

**Depends on:**
- migrations 046-050 applied (auto at app/db init; the script touches the same warehouse)
- src/data/enablement_doc_search.py, src/data/kb/*, src/data/drive_reader.py (other slices)
- kb mode only: `claude` CLI on PATH and logged in; a SYNTHETIC target Drive (PHI rule)

**Settings keys:**
- enablement.drive.active_folders (set_drive_folder path)
- enablement.provider / claude CLI lane config (kb mode)

**macOS risks:**
- kb mode assumes `claude` CLI resolvable on PATH (~/.local/bin from a plain terminal is fine; Finder-launched contexts are not — but this is a terminal script)
- mirror mode still WRITES to the Mac's data/local_warehouse.db — acceptable (DB, not settings) but the plan should say so explicitly given the do-not-touch-data sensitivity

**Verify:** `python scripts/run_drive_eval.py --list-drives (read-only) shows drives with service_built=True; never run --index-mode kb on the Mac without an explicitly synthetic Drive.`

---

### Slice-level notes — infra-updater

**Hard ordering constraints (matter only for piecemeal moves; a full checkout satisfies them all):**
- src/updater/updater.py (token kwarg, default='') must land BEFORE or WITH its token-passing callers src/ui/pages/settings_page.py and src/startup/update_action_widget.py (other slices) — callers-first raises TypeError on stage(); updater-first is safe.
- src/updater/restart.py (restart_requested) must land BEFORE or WITH main.py (main.py imports it); and the b5d241a splash-close change in src/startup/update_action_widget.py (other slice) is REQUIRED for the main.py guard to ever fire — without it the nested splash exec() never returns.
- src/__init__.py VERSION='1.0.7' (other slice / trivial) should land with main.py, else the splash reads 1.0.0 (cosmetic).
- .gitignore's !web/package-lock.json exception before any git operation that adds web/package-lock.json on the Mac.
- pip install pypdf==6.14.2 into the Mac venv before exercising Drive PDF ingestion or run_drive_eval.py against PDFs (import is lazy at drive_reader.py:483, so app startup is unaffected).
- Migrations 043-050 need no manual step: they are git-tracked files auto-applied by SchemaMigrator at DatabaseManager.initialize() — but the FULL source port (all migration files present) must be complete before first app launch on the Mac, or a partial migrations/ dir applies a partial chain.
- If the Mac agent rebuilds the web bundle: Node 24 + committed web/package-lock.json before `npm --prefix web ci`.

**Cross-file risks / silent failures:**
- MIGRATIONS ANSWER (verified from src/updater/schema_migrator.py + db_manager.py:769-794): db_manager.initialize() calls SchemaMigrator().migrate(conn) on EVERY app start against data/local_warehouse.db. Tracking table: schema_migrations (filename TEXT PRIMARY KEY, applied_at). pending() = sorted glob of migrations/*.sql minus applied filenames; each migration runs via executescript + per-migration commit and is recorded; ALTER TABLE ADD COLUMN statements are extracted and applied idempotently against PRAGMA table_info. A failure raises RuntimeError and HALTS the chain (later migrations skipped, app init fails). 043-050 on a warehouse at 042: clean — all are CREATE (VIRTUAL) TABLE IF NOT EXISTS / triggers / backfills, self-described idempotent. A warehouse OLDER than 042: also fine — every missing file 038..050 applies in filename order on next launch since all files ship in git. Precondition worth one Mac check: the arm64 Python's sqlite3 must have FTS5 (048/050 create fts5 tables): python3 -c "import sqlite3; sqlite3.connect(':memory:').execute('CREATE VIRTUAL TABLE t USING fts5(x)')".
- DEV-TREE CLOBBER (answer): YES, real. apply_staged_update() renames live src/, config/, migrations/ (_REPLACEABLE_DIRS) to _<dir>_backup and moves the release payload in, then rewrites VERSION in src/__init__.py. On the Mac git checkout this destroys branch state (data/ is _PROTECTED so settings survive). The updater chain in this delta makes private-repo installs WORK for the first time, which INCREASES the chance the splash's 'Install & Restart' now succeeds if clicked. The plan MUST instruct work claude: never click Install & Restart (splash or Settings > Updates) on the dev checkout; checking for updates is a harmless GET.
- UPDATER CHAIN SUMMARY: 7ce570a = Bearer-auth asset downloads + API asset URLs + octet-stream Accept + _StripAuthOnRedirect (private repos 404'd); 3ed5a62 = splash install path forwards the token to stage() too (manifest resolved but zip 404'd; touches src/startup/update_action_widget.py — other slice); 79e8bd7 = importlib.reload(src) after apply so VERSION is fresh (kills the reinstall loop) + VERSION/footer marker; d2bc239 = restart_requested() flag + main.py exit guard (old process survived Install & Restart); b5d241a = close the splash after spawn so the d2bc239 guard is reachable + setApplicationVersion(src.VERSION) so the splash stops claiming 1.0.0. Files span this slice AND src/startup/update_action_widget.py + src/ui/pages/settings_page.py + src/ui/main_window.py in other slices — the port is only coherent if those land together.
- main.py's eager QtWebEngineWidgets import moves any PySide6/WebEngine breakage from 'first web tab open' to 'app startup' on the Mac — if the Mac's PySide6 install has the previously-diagnosed framework corruption, the app may now fail at boot; run scripts/web_diag.py first on any such symptom.
- The two prior-art guides (THREE_PILLAR_SURGICAL_PATCH_GUIDE.md, MAC_STYLE_GUIDE_TEMPLATE_GUIDE.md — the latter folded into the former by fa7e266) cover only the 1b5f951+870fade subset of this ~60k-line delta; handing both an old guide and the new plan to the Mac agent without a supersedence statement risks conflicting instructions.
- docs/ENABLEMENT_ROADMAP.md ships with a truncated duplicate section ('flag off, W' mid-sentence) — copy verbatim, do not 'repair' during the port.

**macOS-specific:**
- Verify native arm64 Python before anything else: python3 -c "import platform; print(platform.machine())" must print arm64 (Rosetta x86_64 reproduces the blank-QtWebEngine class of failures the installer changes exist to prevent).
- Verify sqlite3 FTS5 support (needed by migrations 048/050): python3 -c "import sqlite3; sqlite3.connect(':memory:').execute('CREATE VIRTUAL TABLE t USING fts5(x)')".
- pip install pypdf==6.14.2 into the Mac venv (pure-Python, arm64-safe).
- Do NOT run 'Install & Restart' from splash or Settings on the dev checkout — the now-working private-repo updater will swap src/, config/, migrations/ with release payload (data/ survives but the git tree is clobbered).
- No signing/notarization/launcher steps are required for a plain git-checkout dev run — sign_macos.sh, launcher_mac.command, and install.py's plist/arch changes apply only to packaged artifacts.
- claude CLI must be on PATH (typically ~/.local/bin) for the enablement lane and for run_drive_eval.py --index-mode kb; the packaged launchers now add it explicitly, a Terminal-launched dev run inherits the shell PATH.
- requirements.lock hash regeneration (pip-compile per the file's header) is explicitly designated to run on the M1 — the new pypdf entry is hash-less until that pass.
- If rebuilding web/: use Node 24 to match the committed web/package-lock.json (npm ci).
- Zombie-kill commands on macOS: use pkill -f alma_mcp_server / pkill -f gemini / pkill -f 'scan_server' — never the Windows wmic/taskkill forms baked into CLAUDE.md and memory.

**Analyst notes / answered questions:**
- Is the Mac warehouse's schema_migrations high-water mark actually 042, or older? Either auto-applies cleanly, but knowing it lets the plan predict exactly which of 038-050 fire on first launch (and how long the FTS backfills in 048/050 take on the Mac's document volume).
- Should the Mac plan include the requirements.lock pip-compile regeneration (the file designates the M1 as the machine for it), or defer it to the owner? It writes only requirements.lock, not data/.
- The updates.* settings on the Mac (auth_mode/github_repo/token) live in the untouchable data/settings.yaml — if the splash's update check + install button turns out to be enabled there, the only permitted mitigation is behavioral (decline), since editing settings is off-limits. Should the plan state this explicitly?
- docs/MAC_STYLE_GUIDE_TEMPLATE_GUIDE.md was superseded by the folded three-pillar guide (fa7e266) — should the new plan tell the Mac agent to ignore it entirely to avoid double-applying Phase C2 steps?
- run_drive_eval.py --index-mode mirror writes rows into the Mac's data/local_warehouse.db — is warehouse-write acceptable under the 'local settings must not be touched' constraint (it is a DB, not settings.yaml), or should the Mac verification stick to --list-drives + preflight only?


## 7. Tests, help corpus, prompts, templates

### `tests/drive_eval/__init__.py`  (A)

Package marker + docstring for the offline half of the Drive search eval harness; declares scorer as pure/deterministic and importable from both tests and scripts/run_drive_eval.py.

**Key details:** 10 lines, docstring only. Corpus is owner-authored on a real Drive (docs/DRIVE_SEARCH_EVAL.md referenced).

**Verify:** `python -c "import tests.drive_eval"`

### `tests/drive_eval/scorer.py`  (A)

Pure ranking-metrics module (485 LOC): no network/LLM/clock. Scores Drive-search runs against a gold set.

**Key details:** K_VALUES=(1,3,5,10), PRECISION_K=5; normalize_key(); @dataclass Query{id,query,category,relevant:set,notes}, RunResult; load_gold_yaml(path) (lazy `import yaml`, raises on malformed/empty/dup-id/missing-query), load_judgments_csv(), score_query(), score(queries,runs,...), to_markdown(), write_report() (writes .md+.json utf-8), write_judgment_csv().

**Depends on:**
- PyYAML installed (already a project dep)
- tests/drive_eval/gold.yaml

**Verify:** `python -m pytest tests/test_drive_eval_scorer.py -x -q`

### `tests/drive_eval/gold.yaml`  (A)

Gold query set (147 lines) for the 'almainsightstest' RCM/EHR Drive corpus of 196 synthetic docs; keys are Drive document filenames without .md.

**Key details:** Top-level `queries:` list; each entry {id: qNN, query, category, relevant (str|list|[] for empty-result precision), notes}. Categories: natural-language, near-duplicate-disambiguation, buried-needle, acronym/alias, deep-nesting, empty-result. Two empty-result queries have relevant: [].

**Depends on:**
- tests/drive_eval/scorer.py::load_gold_yaml

**Verify:** `python -c "from tests.drive_eval.scorer import load_gold_yaml; print(len(load_gold_yaml('tests/drive_eval/gold.yaml')))"`

### `tests/e2e_claude_chat.py`  (M)

Live-CLI smoke script (not pytest): system prompt no longer embedded in user content as an [SYSTEM INSTRUCTIONS] block; now passed via client.generate(prompt, system_prompt=SYSTEM_PROMPT, timeout=90).

**Key details:** _format_chat_prompt(context, user) dropped the system arg. Mirrors the 2026-07-21 Renn-on-Sonnet fix (--system-prompt-file delivery).

**Depends on:**
- logged-in `claude` CLI on PATH
- src/llm/claude_cli_client.py new generate(system_prompt=) signature

**Settings keys:**
- ai.task_routing override_all=claude (checked at runtime)

**macOS risks:**
- requires `claude` CLI installed + logged in on the Mac; run manually, never in CI

**Verify:** `python tests/e2e_claude_chat.py (manual, live)`

### `tests/test_app_modes.py`  (M)

Registry assertions updated for the new enablement nav: tab_key set gains home/help; enablement page-id set and sidebar sections rewritten.

**Key details:** tab_key in {home,calendar,tasks,workbench,analytics,powerpoint,zendesk,help,settings}; enablement ids == {home,en_agent,en_calendar,en_tasks,en_workbench,en_analytics,en_attention,en_powerpoint,en_zendesk,en_help,en_settings}; sections: INSIGHTS==[en_attention,en_analytics], SYSTEM==[en_help,en_settings].

**Depends on:**
- src/ui/app_modes.py PAGES additions (en_attention, en_help, home tab_key)

**Verify:** `python -m pytest tests/test_app_modes.py -x -q`

### `tests/test_asana_pagination.py`  (A)

Asana list calls must walk next_page.offset to completion and survive a stale/expired offset token (HTTP 400) by returning partial results + log, never crashing discovery.

**Key details:** Tests src/data/asana_client (list_projects et al pagination). Hermetic fake HTTP.

**Depends on:**
- src/data/asana_client.py pager changes

**Verify:** `python -m pytest tests/test_asana_pagination.py -x -q`

### `tests/test_asana_readback.py`  (M)

WS1-M2 contract change: asana_monitor._reconcile_existing_task returns the local task_id (truthy) instead of True, and None (not False) for untracked gids.

**Key details:** Uses empty_db fixture; FakeAsana stub.

**Depends on:**
- src/data/asana_monitor.py return-value change

**Verify:** `python -m pytest tests/test_asana_readback.py -x -q`

### `tests/test_asana_setup_guard.py`  (A)

asana_setup.discover() must tag mock discovery results with `mock`, and the setup writer must refuse to persist mock board config to monitor_sources outside demo mode (audit finding 14).

**Key details:** Mock GIDs like project 120420000111 'Enablement Requests' must never reach a real source config.

**Depends on:**
- src/data/asana_setup.py mock tagging + writer guard

**Verify:** `python -m pytest tests/test_asana_setup_guard.py -x -q`

### `tests/test_asana_writeback.py`  (M)

WS1-M6: three un-gated write tools (create_asana_subtask, post_asana_comment, update_asana_due_date) are retired from every dispatch surface; request_asana_task_update replaces them. _poll_board signature changed to mutate a results dict.

**Key details:** Asserts retired names absent from registry._CHAT_TOOLS, claude_tools.TOOL_DEFINITIONS/_DISPATCH, MCP._MCP_ALLOWED_TOOLS; MCP._RETIRED_TOOL_HINTS[t] mentions request_asana_task_update. _poll_board(conn, client, board, results) with results={'created':[],'updated':[]}.

**Depends on:**
- src/data/chat_tools/registry.py
- src/llm/claude_tools.py
- src/mcp/chat_mcp_server.py (_MCP_ALLOWED_TOOLS, _RETIRED_TOOL_HINTS)
- src/data/asana_monitor.py

**Verify:** `python -m pytest tests/test_asana_writeback.py -x -q`

### `tests/test_attention_dismissal.py`  (A)

Attention-queue dismissal is session-scoped 'not now': hides the row now, an explicit Refresh (reload) brings it back (audit finding 24).

**Key details:** pytestmark=pytest.mark.ui; QApplication/QFrame; tests AttentionQueueTab._dismissed semantics.

**Depends on:**
- src/ui/pages/enablement/attention_queue_tab.py

**macOS risks:**
- needs QT_QPA_PLATFORM=offscreen

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_attention_dismissal.py -x -q`

### `tests/test_calendar_bridge.py`  (A)

CalendarWebController + CalendarBridge contract tests, explicitly no WebEngine: forged ids, bad scopes, raising brief lookups are safe no-ops; viewmodel shape locked for the blind-rendering React route.

**Key details:** Imports src.services.enablement_web.CalendarWebController and src.ui.web.calendar_bridge.CalendarBridge; QCoreApplication only (no widgets).

**Depends on:**
- src/services/enablement_web.py
- src/ui/web/calendar_bridge.py

**Verify:** `python -m pytest tests/test_calendar_bridge.py -x -q`

### `tests/test_card_template.py`  (A)

Card/article template: storage round-trip in enablement_store, injection into card-gen and revise prompts, and the Style Guide tab's rendered preview + inline editor.

**Key details:** fake_settings fixture (dict-backed settings_manager, never touches settings.yaml); two @pytest.mark.ui blocks build the tab.

**Depends on:**
- src/data/enablement_store.py template functions
- assets/templates/support_center_article_template.md
- Style Guide tab in src/ui/pages/enablement/settings.py

**Settings keys:**
- style-guide pointer keys faked via monkeypatched settings_manager

**macOS risks:**
- needs QT_QPA_PLATFORM=offscreen for the ui-marked tests

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_card_template.py -x -q`

### `tests/test_chat_engine.py`  (M)

+1049 lines: fabricated-tool-call guard (detection, dialects, false-positive corpus, recovery/livelock), Layer-1 ledger-evidence rewiring, turn-evidence capture, Layer-2 grounding shadow/banner modes, morning-briefing end-to-end, adversarial regressions. All hermetic with fake clients.

**Key details:** New symbols exercised: chat_engine._detect_unexecuted_tool_call, _UNEXECUTED_TOOL_CALL_NOTICE; src.services.turn_grounding integration; ledger_db fixture writes tool rows; _ClaudeShapedClient stub; asserts response bodies never logged at WARNING and corpus never reaches logs.

**Depends on:**
- src/services/chat_engine.py guard + grounding wiring
- src/services/turn_grounding.py
- src/data/startup_greeting.py
- src/data/enablement_tasks.py

**Verify:** `python -m pytest tests/test_chat_engine.py -x -q (slowish: pump loops with timeouts up to 10s)`

### `tests/test_chat_tools.py`  (M)

Regression tests: fast_path.handle_list_tickets must honor date filters in all three shapes (filters.date_range 'START/END', top-level date_start/date_end, filters.date_start/date_end).

**Key details:** Seed dates T-1..T-5 = 2026-01-10..14; exact expected id sets asserted.

**Depends on:**
- src/data/chat_tools/fast_path.py date filtering

**Verify:** `python -m pytest tests/test_chat_tools.py -x -q`

### `tests/test_claude_cli_bridge.py`  (M)

ClaudeCliClient._prepare_prompt contract flipped (2026-07-21 incident): now returns (prepared, system_prompt) tuple; system text must NEVER be embedded as an [SYSTEM INSTRUCTIONS] block in user content.

**Key details:** asserts prepared.strip()=='user prompt here' and sys_prompt returned separately; PII redaction still applies to prepared prompt.

**Depends on:**
- src/llm/claude_cli_client.py _prepare_prompt tuple return

**Verify:** `python -m pytest tests/test_claude_cli_bridge.py -x -q`

### `tests/test_claude_cli_bridge_mcp.py`  (M)

Materialized MCP config for the claude CLI must now include PYTHONPATH=str(_APP_ROOT) in each server env (the CLI runs from a neutral cwd, so `-m src.mcp...` needs an explicit anchor).

**Key details:** env == {ALMA_DB_PATH, ALMA_CHAT_SESSION_FILE, PYTHONPATH:str(_APP_ROOT)}; imports src.agents.claude_cli_bridge._APP_ROOT.

**Depends on:**
- src/agents/claude_cli_bridge.py _APP_ROOT + env injection

**Verify:** `python -m pytest tests/test_claude_cli_bridge_mcp.py -x -q`

### `tests/test_claude_cli_system_prompt.py`  (A)

302-line regression suite for the Renn-on-Sonnet persona fix: persona must reach the CLI as a REAL system prompt via --system-prompt-file, subprocess must run from a neutral cwd outside the app tree, and MCP env gets a PYTHONPATH anchor.

**Key details:** Hermetic: monkeypatches src.agents.claude_cli_bridge.CliSubprocess (FakeSub captures cwd) — no real `claude` spawn. Asserts: system-prompt file written utf-8 and rewritten on persona change; file used because Windows caps cmdline at 32767 chars (persona >10KB); captured cwd not startswith(_APP_ROOT); env['PYTHONPATH']==str(_APP_ROOT), explicit spec PYTHONPATH wins.

**Depends on:**
- src/agents/claude_cli_bridge.py (--system-prompt-file, neutral cwd, _APP_ROOT)
- src/llm/claude_cli_client.py

**macOS risks:**
- none in the test (hermetic); the PRODUCTION neutral-cwd must resolve to a valid dir on macOS (tempdir) — verify in the bridge slice

**Verify:** `python -m pytest tests/test_claude_cli_system_prompt.py -x -q`

### `tests/test_demo_guru_suppression.py`  (A)

Demo mode must suppress the live GuruClient inside Renn's push_guru_draft chat tool (draft still marks pushed), mirroring the Workbench's page.py::_guru_for_push behavior (audit finding 03).

**Key details:** Tests src.data.chat_tools.enablement_tools push path with demo mode on.

**Depends on:**
- src/data/chat_tools/enablement_tools.py demo check in _push_guru_draft_impl

**Verify:** `python -m pytest tests/test_demo_guru_suppression.py -x -q`

### `tests/test_doc_reader_unsupported.py`  (A)

read_document must not decode unreadable binary formats into mojibake: strict=True raises on unsupported format (deck modelling path), lenient default returns a clear stub (Guru import path) — audit finding 20.

**Key details:** Writes binary fixtures via tmp_path; tests src.data.doc_reader.

**Depends on:**
- src/data/doc_reader.py unsupported-format branch

**Verify:** `python -m pytest tests/test_doc_reader_unsupported.py -x -q`

### `tests/test_drive_eval_scorer.py`  (A)

365-line unit suite pinning the eval scorer's metric definitions against hand-computed values (recall@k, precision@5, MRR, aggregation, gold/CSV loaders, malformed-input raises). Pure offline.

**Key details:** Imports tests.drive_eval.scorer as SC, Query, RunResult; parametrized malformed-body cases.

**Depends on:**
- tests/drive_eval/scorer.py
- PyYAML

**Verify:** `python -m pytest tests/test_drive_eval_scorer.py -x -q`

### `tests/test_drive_folder_picker.py`  (A)

607-line suite for the model-independent Drive folder picker: DriveFolderPickerDialog, sharedWithMe roots (an SA's entry points are sharedWithMe folders, not list_drives), Settings 'Browse Drive…' button wiring, DriveListWorker.

**Key details:** Imports src.ui.dialogs.drive_folder_picker_dialog, src.services.agent_chat.DriveListWorker, src.data.drive_reader.DriveReader; googleapiclient service fully mocked (svc.files().list().execute side_effect pages).

**Depends on:**
- src/ui/dialogs/drive_folder_picker_dialog.py
- src/services/agent_chat.py DriveListWorker
- src/data/drive_reader.py roots methods

**Settings keys:**
- enablement.drive.active_folders (written by picker accept path)

**macOS risks:**
- needs QT_QPA_PLATFORM=offscreen (QDialog construction)

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_drive_folder_picker.py -x -q`

### `tests/test_drive_live_search_tools.py`  (A)

New Renn tools search_google_drive / import_drive_doc / search_everywhere: behavior with build_live_drive_client monkeypatched (hermetic), plus registration on all three dispatch surfaces.

**Key details:** _NEW_TOOLS=['search_google_drive','import_drive_doc','search_everywhere']; parametrized registration asserts against chat_tools registry, claude_tools, MCP allowlist; search_everywhere dedupes by Drive file id.

**Depends on:**
- src/data/chat_tools/enablement_tools.py new handlers + build_live_drive_client seam
- src/data/enablement_store.py
- registry/claude_tools/chat_mcp_server registration

**Verify:** `python -m pytest tests/test_drive_live_search_tools.py -x -q`

### `tests/test_drive_query.py`  (A)

query_business_drive's injected live_client seam (the ONLY route to live Drive search) and folder-scoped recursion with a depth cap; documents that production callers omit live_client so the local branch always runs.

**Key details:** Cites src/data/drive_query.py:27-58, enablement_tools.py:28-31, claude_tools.py:1213-1216; parametrized depth_cap {0:{top},1:{top,deep}}; also is_live_drive_configured imported by help_claims_reference.

**Depends on:**
- src/data/drive_query.py

**Verify:** `python -m pytest tests/test_drive_query.py -x -q`

### `tests/test_drive_reader_search.py`  (A)

432-line contract for DriveReader.search_files (the only live-Drive search entry point): q-string construction/escaping via _q, empty-query degradation, pagination, corpora params, HttpError 404 behavior. Fully mocked service.

**Key details:** Imports DriveReader, _q from src.data.drive_reader; asserts single execute() per page and side_effect paging.

**Depends on:**
- src/data/drive_reader.py search_files + _q

**Verify:** `python -m pytest tests/test_drive_reader_search.py -x -q`

### `tests/test_drive_sa_status.py`  (A)

CredentialsPanel must show a service-account status line independent of the OAuth line (2026-07-21 incident: healthy SA looked broken because only the OAuth 'No OAuth client' text showed).

**Key details:** Builds src.ui.widgets.credentials_panel.CredentialsPanel under QApplication; writes fake SA key file utf-8.

**Depends on:**
- src/ui/widgets/credentials_panel.py SA status line

**macOS risks:**
- needs QT_QPA_PLATFORM=offscreen

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_drive_sa_status.py -x -q`

### `tests/test_enablement_doc_search.py`  (A)

Locks the tokenized-lexical-search fix for the whole-query-LIKE recall collapse (0% recall in the Drive eval): enablement_store.search_documents and kb.search must match multi-word natural questions.

**Key details:** Folds gold.yaml intent into local fixtures; parametrized NL queries ('what is an ERA', 'difference between EOB and ERA'); imports kb_search from src.data.kb.search.

**Depends on:**
- migrations/050_enablement_documents_fts.sql applied by DatabaseManager/migrator
- src/data/enablement_store.py tokenized search
- src/data/kb/search.py

**Verify:** `python -m pytest tests/test_enablement_doc_search.py -x -q`

### `tests/test_enablement_live_cutover.py`  (M)

Adds autouse _no_operator_identity fixture: monkeypatches enablement_identity.operator_identity to {} so a dev machine with a real onboarded identity doesn't filter the sim's demo tasks to [].

**Key details:** Sim-based (run_simulation), hermetic despite the name; Qt qapp fixture.

**Depends on:**
- src/data/enablement_identity.py

**macOS risks:**
- needs QT_QPA_PLATFORM=offscreen

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_enablement_live_cutover.py -x -q`

### `tests/test_enablement_ui_live.py`  (M)

Connection-check-worker test now pins asana_setup.is_asana_connected→False and GuruClient.load_credentials→('','') so machines with real keyring credentials still assert the no-credentials shape.

**Key details:** Hermetic despite the name; classmethod monkeypatch on GuruClient.load_credentials.

**Depends on:**
- src/data/asana_setup.py
- src/data/guru_client.py

**macOS risks:**
- needs QT_QPA_PLATFORM=offscreen; keyring backend differences are neutralized by the monkeypatch

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_enablement_ui_live.py -x -q`

### `tests/test_enablement_web_flag.py`  (A)

Web rollout flags parse + fail-closed: enablement.web_tabs enum (off default | calendar | all; junk → off) and the independent ui.web_home boolean (on/true/yes case-insensitive; junk/errors → off).

**Key details:** Imports src.ui.web.web_flags as wf; parametrized raw values including non-strings and lists.

**Depends on:**
- src/ui/web/web_flags.py

**Settings keys:**
- enablement.web_tabs (off|calendar|all, default off)
- ui.web_home (default off, fails closed)

**Verify:** `python -m pytest tests/test_enablement_web_flag.py -x -q`

### `tests/test_google_access_gate.py`  (A)

google_access.google_access_ready() must be auth_type-aware (service-account credentials count as connected even though google_oauth.is_active() is structurally False on that path); consumers: KB worker tick, SettingsPage status line, drive_kb bootstrap, AgentChatController.

**Key details:** Sets QT_QPA_PLATFORM=offscreen at import; imports google_access_ready from src.data.google_access, plus kb.worker, kb.drive_kb, agent_chat, SettingsPage.

**Depends on:**
- src/data/google_access.py (new module)
- src/data/google_oauth.py
- src/data/kb/worker.py
- src/ui/pages/enablement/settings.py

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_google_access_gate.py -x -q`

### `tests/test_help_claims_getting-started.py`  (A)

81-test accuracy audit of the 6 getting-started help articles: every test settles one falsifiable article claim against real code; discrepancies are strict xfail with the real behavior in the reason. Hermetic: no network, no credentials, no QtWebEngine.

**Key details:** Autouse module fixtures: _force_native_tabs (web_flags.web_tabs_mode→'off') and _no_guru_credentials (GuruClient.load_credentials→('','')); help_db fixture = DatabaseManager.initialize()+loader.load_bundled_help; _DEMO_DB_PATH=os.path.join(tempfile.gettempdir(),'alma_enablement_demo.db'); one test asserts help.bug_form_url scheme guard rejects file:///C:/evil.exe.

**Depends on:**
- assets/help/** corpus
- src/data/help package
- migrations/048_help_center.sql
- src/ui/pages/enablement/*
- src/ui/web/web_flags.py

**Settings keys:**
- help.bug_form_url

**macOS risks:**
- a leftover /tmp/alma_enablement_demo.db from prior runs is shared global state — harmless but delete if demo-vs-live tests act oddly

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest 'tests/test_help_claims_getting-started.py' -x -q`

### `tests/test_help_claims_create.py`  (A)

103-test audit of the 7 create-section articles (PowerPoint, Zendesk, content studio, deferred previews, mermaid): exercises pptx_store deck generation against the real template, zendesk_tab, artifact_store, quiz/mermaid tools.

**Key details:** Uses python-pptx (from pptx import Presentation) to open generated decks; asserts pptx_store._DECK_TEMPLATE == assets/templates/renn_deck.pptx and its layout names; also asserts prompt templates exist; path comparisons use .as_posix() (portable).

**Depends on:**
- python-pptx installed
- assets/templates/renn_deck.pptx
- config/prompts/enablement_* prompt files
- src/data/pptx_store.py
- src/data/{artifact_store,mermaid_lint,quiz_artifacts,zendesk_store,agent_jobs,chat_action_requests}.py
- migrations/047_enablement_artifacts.sql

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_help_claims_create.py -x -q`

### `tests/test_help_claims_insights.py`  (A)

99-test audit of the 4 insights articles: card-health scoring (enablement_health score/signals/models), attention queue buckets, guru_analytics, AnalyticsPage, DonutChart.

**Key details:** Imports attention_queue_tab._BUCKETS/group_by_bucket, enablement_health.score.score_card, signals.gather_signals, content_catalog.upsert_entry/CatalogEntry.

**Depends on:**
- src/data/enablement_health/*
- src/data/content_catalog/*
- src/data/guru_analytics.py
- src/ui/pages/enablement/{analytics,attention_queue_tab,mini_charts}.py

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_help_claims_insights.py -x -q`

### `tests/test_help_claims_knowledge-base.py`  (A)

77-test audit of the 6 knowledge-base articles: kb store/sync/ingest/search/worker/card_format, drive_kb KBWriteDenied, kb_tools handlers, quarantine + bootstrap flows. 11 conditional skips guard dev-machine state.

**Key details:** Skips: 'no local settings.yaml to inspect' and 'operator has bootstrapped the KB on this machine' — both skip cleanly on a fresh Mac (data/ untouched). Tables: kb_cards, kb_folders, kb_sync_state, guru_content_drafts.

**Depends on:**
- migrations/046_kb.sql + 049_kb_extra_fields.sql
- src/data/kb/*
- src/data/chat_tools/kb_tools.py
- src/data/google_access.py

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest 'tests/test_help_claims_knowledge-base.py' -x -q`

### `tests/test_help_claims_plan.py`  (A)

108-test audit of the 7 plan articles: CalendarPage/_Chip, TasksPage/_Check, TaskDetailPanel, asana setup/writeback/conflicts, CalendarWebController drag-reschedule, identity scoping.

**Key details:** Imports pat_store, enablement_identity, asana_client/asana_writeback, chat_tools.registry, client_factory.

**Depends on:**
- src/ui/pages/enablement/{calendar,tasks,task_detail,settings}.py
- src/services/enablement_web.py
- src/data/asana_*.py

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_help_claims_plan.py -x -q`

### `tests/test_help_claims_reference.py`  (A)

54-test audit of the 4 reference articles (glossary, settings-keys, version-and-data, what-renn-can-write): pins ACTION_TYPES, _CONFIRM_WRITE_OPS, artifact KINDS, RESOLVER_ACTION_TOOLS, _TASK_UPDATE_ACTIONS, DEFAULT_DB_PATH, extras_cap — i.e. the article's tables of truth vs the constants in code.

**Key details:** settings-keys article audited against actual settings_manager reads; imports is_live_drive_configured, _kb_precheck.

**Depends on:**
- src/data/chat_action_requests.py
- src/data/artifact_store.py
- src/data/chat_tools/{enablement_tools,kb_tools,registry}.py
- src/data/asana_extras.py

**Settings keys:**
- audits the documented key list in assets/help/reference/settings-keys.md against code

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_help_claims_reference.py -x -q`

### `tests/test_help_claims_renn.py`  (A)

62-test audit of the 8 renn articles: tool reference vs registry/MCP TOOL_SCHEMAS, confirm-card flow (chat_action_requests), daily plan (startup_greeting), pickers, where-renn-appears (chat_panel, page).

**Key details:** Imports AgentChatController._write_asana_task_update, chat_session.create_session, help_tools.handle_help_search, _DOC_STYLES from artifact_tools.

**Depends on:**
- src/services/agent_chat.py
- src/mcp/chat_mcp_server.py TOOL_SCHEMAS
- src/data/startup_greeting.py
- src/ui/pages/enablement/chat_panel.py

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_help_claims_renn.py -x -q`

### `tests/test_help_claims_settings.py`  (A)

119-test audit of the 8 settings articles: SettingsPage sections (_GuideSection, 'ASANA BOARDS · N configured'), provider routing (build_client_for_task/resolve_provider_for_task, GeminiClient vs ClaudeCliClient), identity, connect flows, usage tab, status dot colors (ALMA_SUCCESS/ALMA_WARNING).

**Key details:** 4 conditional skips for dev-local state; imports RennUsageRecorder, prioritize_today, ChatEngine, RennUsagePanel from src.ui.pages.enablement.usage_tab.

**Depends on:**
- src/ui/pages/enablement/{settings,usage_tab,page}.py
- src/gemini/client_factory.py
- src/data/renn_usage.py
- src/ui/theme.py

**Settings keys:**
- enablement.provider
- ai.task_routing (routing claims audited)

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_help_claims_settings.py -x -q`

### `tests/test_help_claims_troubleshooting.py`  (A)

53-test audit of the 6 troubleshooting articles: blank-screen claims vs web_flags/web fallback, connect-first gates, flag-a-bug flow, RENN_SYSTEM_PROMPT refusal claims, HelpTab._STATUS_BADGE honesty vocabulary, MainWindow behavior.

**Key details:** Imports RENN_SYSTEM_PROMPT from src.ui.pages.enablement.page, _STATUS_BADGE from help_tab, drive_monitor, web_flags; 3 conditional skips.

**Depends on:**
- src/ui/main_window.py
- src/ui/pages/enablement/{help_tab,chat_panel,page,settings}.py
- src/data/help/*

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_help_claims_troubleshooting.py -x -q`

### `tests/test_help_claims_workbench.py`  (A)

117-test audit of the 7 workbench articles: doc_to_card.card_from_document, html_markdown round-trip, guru_blocks.expand_blocks, text_diff.diff_words, DiffView/diff_lines/_ROW_STYLE, RichTextEditor, GuruCardPickerDialog, publish paths (_push_guru_draft_impl/_revise_draft_impl).

**Key details:** 7 conditional skips; builds EnablementPage; Guru suppressed via module fixtures like the other claims files.

**Depends on:**
- src/data/{doc_to_card,html_markdown,guru_blocks,text_diff,doc_reader}.py
- src/ui/pages/enablement/{workbench,diff_view,rich_editor,card_picker}.py

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_help_claims_workbench.py -x -q`

### `tests/test_help_corpus_sync.py`  (A)

The bundled help corpus must SYNC when behind, not only when the table is empty (field report 2026-07-22: 8 shown vs 62 bundled): help_tools._ensure_help_corpus and HelpTab.reload must both pick up later-added articles.

**Key details:** Simulates the stale state via DELETE FROM help_articles WHERE section <> 'renn' then asserts re-sync; loader is upsert-on-hash.

**Depends on:**
- src/data/help/loader.py sync-when-behind logic
- src/data/chat_tools/help_tools.py
- assets/help corpus

**Verify:** `python -m pytest tests/test_help_corpus_sync.py -x -q`

### `tests/test_help_search.py`  (A)

Help search must be tokenized lexical, NOT whole-query LIKE — multi-word natural questions must find articles; also reads src/data/help/search.py SOURCE text to structurally ban the LIKE pattern.

**Key details:** Reads (repo)/src/data/help/search.py via read_text(encoding='utf-8') — repo layout dependency; parametrized natural questions.

**Depends on:**
- src/data/help/{search,loader,store}.py
- assets/help corpus

**Verify:** `python -m pytest tests/test_help_search.py -x -q`

### `tests/test_help_store.py`  (A)

Help corpus loader + store contract: frontmatter parsing, status vocabulary (honesty mechanism), id uniqueness, loader idempotency by content hash, FTS triggers; asserts loader.help_dir() (assets/help) exists.

**Key details:** Writes synthetic articles via tmp_path with '---\nfm\n---' frontmatter; load_bundled_help(help_db, directory=...) summary dict asserted; missing-directory case at loader.load_bundled_help(help_db, directory=tmp_path/'nope').

**Depends on:**
- src/data/help/{loader,store}.py
- assets/help/ present
- migrations/048_help_center.sql (help_articles + FTS)

**Verify:** `python -m pytest tests/test_help_store.py -x -q`

### `tests/test_help_tab.py`  (A)

HelpTab browse, search, and status-banner honesty contract; offscreen Qt, asserts through widget APIs (never screenshots).

**Key details:** pytestmark=pytest.mark.ui; imports HelpTab from src.ui.pages.enablement.help_tab.

**Depends on:**
- src/ui/pages/enablement/help_tab.py
- src/data/help/*
- assets/help corpus

**macOS risks:**
- needs QT_QPA_PLATFORM=offscreen

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_help_tab.py -x -q`

### `tests/test_home_bridge.py`  (A)

HomeBridge is a pure relay with zero authority: every slot callable by untrusted page script, forged modes rejected (parametrized 'admin','root','PRODUCT',...), inert without injected controller; is the GATED_SLOT_TESTS registration for requestModeSwitch.

**Key details:** Reads src/ui/web/home_bridge.py source text (utf-8) for structural assertions; QCoreApplication only, no WebEngine.

**Depends on:**
- src/ui/web/home_bridge.py
- src/ui/app_modes.py

**Settings keys:**
- app.last_mode (persisted by the gated slot under test)

**Verify:** `python -m pytest tests/test_home_bridge.py -x -q`

### `tests/test_home_page.py`  (M)

New test: the CCC banner on the native HomePage is visible only in enablement mode and carries the branding strings from src/branding.py.

**Key details:** Asserts page._ccc_banner.isVisibleTo(page) toggles with set_mode; CONTENT_COMMAND_CENTER.upper() and CONTENT_COMMAND_CENTER_DESCRIPTION appear as QLabel texts.

**Depends on:**
- src/branding.py (CONTENT_COMMAND_CENTER, CONTENT_COMMAND_CENTER_DESCRIPTION)
- src/ui/pages/home_page.py _ccc_banner

**macOS risks:**
- needs QT_QPA_PLATFORM=offscreen

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_home_page.py -x -q`

### `tests/test_home_web_controller.py`  (A)

HomeWebController viewmodel shape locked (React renders it blind); forged modes/unknown quick-action keys/bogus activity kinds are silent no-ops; includes THE parity test guarding the deliberate SQL duplication between home_page.py and home_web.py.

**Key details:** Parametrized greeting hour/word; bad inputs ['','admin','../reports',123,...]; drops pptx_decks table to test resilience.

**Depends on:**
- src/services/home_web.py
- src/ui/pages/home_page.py (parity target)
- src/ui/app_modes.py

**macOS risks:**
- needs QT_QPA_PLATFORM=offscreen (QApplication used)

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_home_web_controller.py -x -q`

### `tests/test_html_sanitize.py`  (A)

XSS corpus + benign-markup preservation for src/data/html_sanitize.sanitize_html: deny-by-default (no executables, unsafe schemes, style smuggling) while Guru/Zendesk markup survives.

**Key details:** First of two layers (second is the sandbox='' iframe); CI-critical for the web pivot security posture.

**Depends on:**
- src/data/html_sanitize.py

**Verify:** `python -m pytest tests/test_html_sanitize.py -x -q`

### `tests/test_ingest_dedupe.py`  (A)

Re-uploading the same local file refreshes one document + one draft instead of duplicating (audit finding 19): _ingest_local_file must pass a stable source_ref (the file path).

**Key details:** Counts enablement_documents and guru_content_drafts rows; imports EnablementPage.

**Depends on:**
- src/ui/pages/enablement/page.py _ingest_local_file
- src/data/enablement_store.py save_document(source_ref=)

**macOS risks:**
- needs QT_QPA_PLATFORM=offscreen

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ingest_dedupe.py -x -q`

### `tests/test_kb_extra_and_readopt.py`  (A)

KB fixes 17+18: custom frontmatter keys on a card survive the DB round-trip and are written back on push (extra-fields column); a quarantined EC folder is re-verified and re-adopted by re-bootstrap when healthy again.

**Key details:** Inspects kb_cards PRAGMA columns and kb_folders.status transitions; imports kb store/sync/card_format/drive_kb.

**Depends on:**
- migrations/049_kb_extra_fields.sql
- src/data/kb/{store,sync,card_format,drive_kb}.py

**Verify:** `python -m pytest tests/test_kb_extra_and_readopt.py -x -q`

### `tests/test_manifest_fetcher.py`  (M)

New TestManifestAssetNaming: _find_manifest_url must accept both 'release_manifest.json' (what CI's make_release_manifest.py uploads) and legacy 'manifest.json', raising ManifestFetchError('missing manifest') if neither present.

**Key details:** src.updater.manifest_fetcher._find_manifest_url, ManifestFetchError.

**Depends on:**
- src/updater/manifest_fetcher.py

**Verify:** `python -m pytest tests/test_manifest_fetcher.py -x -q`

### `tests/test_native_dialog_styling.py`  (A)

Native dialog buttons must stay readable under the enablement pages' selectorless cream stylesheet (blank Yes/No bug 2026-07-22): pixel-luminance test of a real shown QMessageBox plus inspect.getsource gates that every native confirm applies the corrective.

**Key details:** style_native_dialog / native_dialog_button_qss from src.ui.pages.enablement._common; gated fns: EnablementPage._web_reschedule_confirm, _web_workbench_publish_confirm (must build instance + .exec(), never static QMessageBox.question), DriveFolderPickerDialog.__init__, SettingsPage._prompt_text (not QInputDialog.getText); luminance spread must be > 80 (pre-fix ~12); box.show() briefly during the run.

**Depends on:**
- src/ui/pages/enablement/_common.py correctives
- src/ui/design/qss.py build_stylesheet
- src/ui/dialogs/drive_folder_picker_dialog.py

**macOS risks:**
- pixel test uses btn.grab() — plain-widget grabs DO render offscreen (unlike WebEngine), but font/AA differences on macOS could shift the luminance spread; if it fails near the 80 threshold suspect the platform font, not the fix

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_native_dialog_styling.py -x -q`

### `tests/test_page_return_reload.py`  (A)

MainWindow refreshes a page when the operator returns to it (audit 09/11) and provides a usable native Agent fallback when the web view can't be built (audit 10).

**Key details:** pytestmark=pytest.mark.ui; synthetic QWidget pages with reload hooks.

**Depends on:**
- src/ui/main_window.py return-reload + fallback logic

**macOS risks:**
- needs QT_QPA_PLATFORM=offscreen

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_page_return_reload.py -x -q`

### `tests/test_phase5_release.py`  (M)

TestUpdatesTabHandlers retargeted from src.ui.pages.settings_page.SettingsPage to src.ui.widgets.updates_panel.UpdatesPanel (2026-07-22 extraction); _save_last_checked now asserted to use update_section (merge) not set_section.

**Key details:** Methods exercised on UpdatesPanel: _on_update_progress/_on_update_complete/_on_update_failed/_save_last_checked/_load_last_checked/_on_install_update; patches src.ui.widgets.updates_panel.update_section/get_section.

**Depends on:**
- src/ui/widgets/updates_panel.py
- src/data/settings_manager.py update_section

**Settings keys:**
- updates.last_checked
- updates.github_repo (must survive merge)

**Verify:** `python -m pytest tests/test_phase5_release.py -x -q`

### `tests/test_redaction_fail_closed.py`  (A)

PII redaction on the Claude direct-API path must fail CLOSED (audit finding 27): a broken pattern config makes _redact_text raise so generate() aborts, instead of logging and sending raw text.

**Key details:** Tests src.llm.claude_client._redact_text raise behavior (old bug at claude_client.py:83).

**Depends on:**
- src/llm/claude_client.py fail-closed change

**Verify:** `python -m pytest tests/test_redaction_fail_closed.py -x -q`

### `tests/test_renn_usage.py`  (A)

Renn usage metering full chain: ClaudeCliBridge.set_usage_sink/_log_usage_if_configured → RennUsageRecorder → gemini_usage rows (source='renn_chat', real cost_usd, model='claude-sonnet-4-6') → renn_usage.usage_summary → RennUsagePanel. Hermetic, no CLI subprocess.

**Key details:** gemini_usage already has cost_usd in the base schema (db_manager.py ~line 612) — NO new migration; bridge stub via SimpleNamespace(_usage_tracker,_usage_source); sets QT_QPA_PLATFORM=offscreen at import; pytestmark=ui.

**Depends on:**
- src/data/renn_usage.py (new)
- src/agents/claude_cli_bridge.py set_usage_sink/_log_usage_if_configured
- src/ui/pages/enablement/usage_tab.py RennUsagePanel

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_renn_usage.py -x -q`

### `tests/test_scoped_usage_tracker.py`  (M)

Stub tracker's log_call signature gains cost_usd=None to match the extended ScopedUsageTracker.log_call used by Renn metering.

**Key details:** One-line signature change in _StubBase.log_call.

**Depends on:**
- src/data usage tracker log_call(cost_usd=) change

**Verify:** `python -m pytest tests/test_scoped_usage_tracker.py -x -q`

### `tests/test_settings_subtabs.py`  (M)

Enablement SettingsPage tab strip is now 7 tabs: [Connections, Providers, Sources, Style Guide, Updates, Usage, Maintenance]; Updates/Maintenance embed the shared panels; Usage lazily builds RennUsagePanel only when s.usage_db_factory is wired, idempotently.

**Key details:** Asserts isinstance(s._updates, UpdatesPanel), isinstance(s._maintenance, MaintenancePanel), s._usage_dashboard None until factory set, placeholder visible, second visit reuses the same panel instance.

**Depends on:**
- src/ui/widgets/{updates_panel,maintenance_panel}.py
- src/ui/pages/enablement/{settings,usage_tab}.py

**macOS risks:**
- needs QT_QPA_PLATFORM=offscreen

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_settings_subtabs.py -x -q`

### `tests/test_shared_session.py`  (A)

Renn's two surfaces (Agent page + Workbench assistant panel) share ONE session: chat_session.resolve_or_create_session reuses the .current_chat_session pointer file instead of each minting its own (audit finding 5).

**Key details:** Asserts pointer.read_text(encoding='utf-8').strip()==sid; counts chat_sessions rows.

**Depends on:**
- src/services/chat_session.py resolve_or_create_session

**Verify:** `python -m pytest tests/test_shared_session.py -x -q`

### `tests/test_sidebar_layout.py`  (A)

Sidebar must not compress entries as the nav grows: nav lives in a QScrollArea and #SidebarButton has a min-height, asserted across all modes x window heights (1000/700/520).

**Key details:** Builds full MainWindow; parametrized over app_modes.MODES; pytestmark=ui.

**Depends on:**
- src/ui/main_window.py sidebar scroll area + QSS min-height
- src/ui/app_modes.py

**macOS risks:**
- needs QT_QPA_PLATFORM=offscreen; heaviest UI constructor in the new set — keep in its own group

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_sidebar_layout.py -x -q`

### `tests/test_source_baseline.py`  (M)

list_active_trcs tests now pass now=rate_baseline_now so the lookback window covers the fixed-date seed regardless of wall-clock (deflakes the suite over time).

**Key details:** list_active_trcs(conn, 'zendesk', now=rate_baseline_now) — requires the now= parameter on src/data/source_baseline.list_active_trcs.

**Depends on:**
- src/data/source_baseline.py list_active_trcs(now=) param

**Verify:** `python -m pytest tests/test_source_baseline.py -x -q`

### `tests/test_splash.py`  (M)

Splash is mode-aware branded: product = 'Alma Insights — Starting up' + 'ALMA INSIGHTS'/'RCM ISSUE ANALYSIS'; enablement = 'Content Command Center — Starting up' + 'CONTENT COMMAND CENTER', with zero 'RCM' strings; unpinned mode resolves via app_modes.

**Key details:** make_splash fixture gains mode= kwarg; existing construction test pins mode='product' to isolate from dev-box app.default_mode=last.

**Depends on:**
- src/branding.py
- splash widget mode parameter

**Settings keys:**
- app.default_mode / app.last_mode (resolved when mode unpinned)

**macOS risks:**
- needs QT_QPA_PLATFORM=offscreen

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_splash.py -x -q`

### `tests/test_stage4_data_warehouse.py`  (M)

TestGetTrcHistory deflaked: passes days=100_000 (DAYS_COVERING_SEEDED_DATA) because the fixture seeds fixed 2026-03 dates that the default wall-clock 90-day window ages out.

**Key details:** get_trc_history(trc, days=...) parameter must exist.

**Depends on:**
- src/data warehouse query get_trc_history(days=)

**Verify:** `python -m pytest tests/test_stage4_data_warehouse.py -x -q`

### `tests/test_sync_status.py`  (A)

Analytics-sync outcome must be the LAST write to the status line — _load_live must not overwrite it with the generic 'N tasks · M pending drafts.' (audit finding 21).

**Key details:** pytestmark=ui; targets _on_analytics_synced ordering.

**Depends on:**
- src/ui/pages/enablement analytics sync status ordering fix

**macOS risks:**
- needs QT_QPA_PLATFORM=offscreen

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_sync_status.py -x -q`

### `tests/test_system_panels.py`  (A)

UpdatesPanel + MaintenancePanel (the shared system widgets both Settings pages embed, extracted 2026-07-22) — hermetic: settings/keyring/updater/rollback monkeypatched, offscreen Qt, no network/vault.

**Key details:** Imports src.ui.widgets.maintenance_panel.MaintenancePanel, src.ui.widgets.updates_panel (as up), src.data.pat_store, src.updater.rollback; monkeypatches QInputDialog/QMessageBox; sets QT_QPA_PLATFORM=offscreen at import; pytestmark=ui.

**Depends on:**
- src/ui/widgets/{updates_panel,maintenance_panel}.py
- src/updater/rollback.py
- src/data/pat_store.py

**Settings keys:**
- updates.* (panel reads/writes via monkeypatched settings_manager)

**macOS risks:**
- keyring/pat_store is monkeypatched so macOS Keychain differences don't matter here

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_system_panels.py -x -q`

### `tests/test_task_detail_actions.py`  (M)

New regression: TaskDetailPanel's Complete button sits on its own row and is not clipped at 440px width (header used to pack 3 badges + assignee + submitter + button into one non-wrapping row).

**Key details:** Uses p.grab().save(out) with os.path.getsize>0 for the existing test — grab of plain widgets works offscreen.

**Depends on:**
- src/ui/pages/enablement/task_detail.py header layout fix (commit beb80ce)

**macOS risks:**
- needs QT_QPA_PLATFORM=offscreen

**Verify:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_task_detail_actions.py -x -q`

### `tests/test_turn_grounding.py`  (A)

1588-line suite for src/services/turn_grounding (Layer-2 grounding detector); the heart is the 27-case false-positive corpus from TEL_SG_RECON.md encoded table-driven, so a detector edit shows up as a named case.

**Key details:** Pure: sqlite3/time/date only; imports tg + named symbols from src.services.turn_grounding. Contains an explicit 'READ THIS BEFORE CHANGING AN EXPECTATION' preamble.

**Depends on:**
- src/services/turn_grounding.py

**Verify:** `python -m pytest tests/test_turn_grounding.py -x -q`

### `tests/test_web_chat_drawer.py`  (A)

M5.5 web Renn drawer plumbing WITHOUT WebEngine: chatNotice relay on ChatBridge, page.py _chat_say/_web_chat_send helpers, shared-bridge factory; invariant = the drawer is the SAME assistant as the Qt ChatPanel (turns mirror, engine output only via bridge, never doubled).

**Key details:** QCoreApplication only; imports src.ui.web.chat_bridge.ChatBridge. NOTE: distinct from the gitignored WebEngine round-trip file of the same-name family — this one IS committed and runs headless anywhere.

**Depends on:**
- src/ui/web/chat_bridge.py chatNotice
- src/ui/pages/enablement/page.py _chat_say/_web_chat_send/_get_web_chat_bridge

**Verify:** `python -m pytest tests/test_web_chat_drawer.py -x -q`

### `tests/test_web_diag.py`  (A)

web_diag blank-page fact sheet: BOTH OS branches (Windows helper-exe check; macOS Rosetta/Mach-O arch/framework-corruption checks) exercised on any host via monkeypatched platform/sysctl/filesystem — the module can't rot on the OS not in front of us.

**Key details:** Uses struct to build fake Mach-O headers; imports src.ui.web.web_diag as wd.

**Depends on:**
- src/ui/web/web_diag.py
- scripts/web_diag.py counterpart (not required by the test)

**macOS risks:**
- none — deliberately host-independent; this is the diagnostic to run FIRST on any Mac blank-web-page report

**Verify:** `python -m pytest tests/test_web_diag.py -x -q`

### `tests/test_web_guardrails.py`  (A)

CI architecture guardrails scanning web/src JS/JSX SOURCES (not the built dist): bans dangerouslySetInnerHTML, .innerHTML, document.write, eval(, new Function, fetch(, XMLHttpRequest, WebSocket, sendBeacon, localStorage, sessionStorage, indexedDB; asserts App.jsx and ChatApp.jsx exist.

**Key details:** WEB_SRC = repo/web/src; _sources() asserts non-empty (fails loudly if web/ missing); no npm build needed.

**Depends on:**
- web/src/** checked out on the Mac (App.jsx, ChatApp.jsx present)

**macOS risks:**
- fails if the port omits the web/ tree — that failure is the intended signal, not flake

**Verify:** `python -m pytest tests/test_web_guardrails.py -x -q`

### `tests/test_workbench_bridge.py`  (A)

693-line WorkbenchWebController + WorkbenchBridge contract suite (no WebEngine): EVERY preview_html emitted has passed sanitize_html (stored Guru HTML and markdown-derived alike), WorkbenchPage surface parity (_current_drafts), untrusted-input gating on bridge entry points.

**Key details:** Imports WorkbenchWebController from src.services.enablement_web and WorkbenchBridge from src.ui.web.workbench_bridge; QCoreApplication only.

**Depends on:**
- src/services/enablement_web.py
- src/ui/web/workbench_bridge.py
- src/data/html_sanitize.py

**Verify:** `python -m pytest tests/test_workbench_bridge.py -x -q`

### `tests/test_updater.py`  (M)

Two new classes: TestNormalizeGithubRepo (update_checker.normalize_github_repo accepts owner/repo, full/partial/deep/ssh GitHub URLs, .git suffix, empty/None→'') and TestUpdatesSectionMerge (github_repo must survive the last_checked write — update_section merges; set_section's clobber is pinned as the documented old bug).

**Key details:** src.updater.update_checker.normalize_github_repo; src.data.settings_manager.update_section/get_section/set_section; patches _DATA_DIR/_SETTINGS_FILE/get_settings_path to tmp_path so the real data/settings.yaml is never touched.

**Depends on:**
- src/updater/update_checker.py normalize_github_repo
- src/data/settings_manager.py update_section (new merge API)

**Settings keys:**
- updates.github_repo
- updates.last_checked

**Verify:** `python -m pytest tests/test_updater.py -x -q`

### `tests/test_update_action_widget.py`  (M)

3-line addition: the update widget must forward token=widget._payload.get('token','') to the download — without it a private-repo asset 404s after the manifest resolve succeeded.

**Key details:** PAT forwarding assertion only.

**Depends on:**
- update action widget token plumb-through

**Verify:** `python -m pytest tests/test_update_action_widget.py -x -q`

### `assets/help/** (63 files)`  (A)

The bundled Help Center corpus: 63 markdown articles across 10 sections (create 7, getting-started 6, insights 4, knowledge-base 6, plan 7, reference 4, renn 8, settings 8, troubleshooting 6, workbench 7). Loaded into SQLite (help_articles + FTS) by src/data/help/loader.load_bundled_help, upsert-on-content-hash, sync-when-behind.

**Key details:** Frontmatter schema per file: id, title, section, section_title, section_order, order, status (honesty vocabulary e.g. 'available'), features (list of page ids like en_workbench), summary, last_verified. Article ids like 'getting-started-what-this-is' are asserted by the help_claims suites; reference/settings-keys.md documents settings keys audited by test_help_claims_reference.py. Copy verbatim — content hashes drive resync and tests quote exact strings.

**Depends on:**
- src/data/help/loader.py (help_dir() resolves assets/help relative to repo)
- migrations/048_help_center.sql

**Settings keys:**
- help.bug_form_url documented in troubleshooting/flag-a-bug.md

**macOS risks:**
- copy byte-identical (utf-8); any CRLF/encoding mangling changes content hashes and retriggers sync churn; filenames are lowercase-hyphenated so APFS case-insensitivity is safe

**Verify:** `python -m pytest tests/test_help_store.py tests/test_help_corpus_sync.py -x -q (loader summary counts 63)`

### `assets/templates/renn_deck.pptx`  (A)

Binary PowerPoint template (27,387 bytes) that pptx_store uses as the base for generated decks; referenced as src/data/pptx_store.py:28 _DECK_TEMPLATE = <repo>/assets/templates/renn_deck.pptx.

**Key details:** test_help_claims_create.py opens it with python-pptx, asserts its slide_layouts names and that it is python-pptx's own default deck marked/derived — it must be the exact shipped binary, not regenerated.

**Depends on:**
- src/data/pptx_store.py
- python-pptx

**macOS risks:**
- transfer as binary (git handles this; do NOT pass through any text/EOL filter)

**Verify:** `python -c "from pptx import Presentation; from src.data.pptx_store import _DECK_TEMPLATE; Presentation(str(_DECK_TEMPLATE))"`

### `assets/templates/support_center_article_template.md`  (A)

51-line 'Unified Support Center/Guru Article Template' — the default style-guide/article template injected into card-gen and revise prompts and shown in the Style Guide tab.

**Key details:** Contains Alma governance conventions (no Overview header, 2-3 sentence intro as ~140-char metadata description). Exercised by tests/test_card_template.py.

**Depends on:**
- src/data/enablement_store.py template loading

**macOS risks:**
- contains typographic quotes/em-dashes — keep utf-8

**Verify:** `python -m pytest tests/test_card_template.py -x -q`

### `config/prompts/enablement_battle_card.txt`  (A)

22-line prompt: internal battle-card generator from SOURCE, pure markdown with EXACT machine-validated section headers.

**Key details:** Output format is machine-validated by artifact code — header text is load-bearing; consumed via config/prompts loading in artifact generation.

**Depends on:**
- src/data/chat_tools/artifact_tools.py or artifact_store consumers

**Verify:** `covered by tests/test_help_claims_create.py artifact tests`

### `config/prompts/enablement_mermaid.txt`  (A)

19-line prompt: Mermaid diagram generation for a knowledge card; output must be ONLY a fenced ```mermaid block, no prose (machine-validated, linted by src/data/mermaid_lint.py).

**Key details:** Pairs with mermaid_lint imported in test_help_claims_create.py.

**Depends on:**
- src/data/mermaid_lint.py

**Verify:** `covered by tests/test_help_claims_create.py`

### `config/prompts/enablement_one_pager.txt`  (A)

22-line prompt: one-pager from SOURCE; EXACT headers '# <title>', '## Overview', '## Why it matters', '## Key facts', '## How to talk about it', '## Links' in order (machine-validated).

**Key details:** Header list verbatim above.

**Depends on:**
- artifact generation pipeline

**Verify:** `covered by tests/test_help_claims_create.py`

### `config/prompts/enablement_quiz.txt`  (A)

25-line prompt: knowledge-check quiz testing ONLY facts in SOURCE; exact machine-validated output format (consumed by src/data/quiz_artifacts.py).

**Key details:** Format block is parsed programmatically — copy verbatim.

**Depends on:**
- src/data/quiz_artifacts.py

**Verify:** `covered by tests/test_help_claims_create.py`

### `config/prompts/enablement_task_brief.txt`  (A)

18-line prompt: summarize an incoming Asana enablement request into a structured brief; extract-only, never invent.

**Key details:** Pairs with migrations/045_task_brief.sql and the task-brief feature.

**Depends on:**
- task brief generation code
- migrations/045_task_brief.sql

**Verify:** `covered by tests/test_help_claims_plan.py brief tests`

---

### Slice-level notes — tests-assets

**Hard ordering constraints (matter only for piecemeal moves; a full checkout satisfies them all):**
- Port ALL src/ modules + migrations 043-050 BEFORE running any test in this slice: db_manager.initialize()/schema_migrator must apply 046_kb, 047_enablement_artifacts, 048_help_center, 049_kb_extra_fields, 050_enablement_documents_fts or every kb/help/artifact/doc-search test fails at fixture setup.
- assets/help/** (63 md files) + src/data/help package before test_help_store/test_help_search/test_help_corpus_sync/test_help_tab and all 10 test_help_claims_* files (test_help_store asserts loader.help_dir().exists()).
- assets/templates/renn_deck.pptx + config/prompts/enablement_*.txt + python-pptx installed before test_help_claims_create.py (it opens the real template binary and checks layout names).
- web/src SPA sources (App.jsx, ChatApp.jsx) must be checked out before test_web_guardrails.py — it scans sources and asserts non-empty; no npm build is required for any committed test.
- src/ui/widgets/updates_panel.py + maintenance_panel.py + settings_manager.update_section before test_phase5_release/test_updater/test_system_panels/test_settings_subtabs (they import the new panels and the merge API).
- src/data/renn_usage.py + ClaudeCliBridge.set_usage_sink/_log_usage_if_configured + usage_tab.RennUsagePanel before test_renn_usage/test_settings_subtabs; no migration needed (gemini_usage.cost_usd exists at base).
- src/services/turn_grounding.py before test_turn_grounding.py AND test_chat_engine.py (chat_engine imports tg for Layer-2 tests).
- src/agents/claude_cli_bridge.py (--system-prompt-file, neutral cwd, _APP_ROOT, PYTHONPATH env) + claude_cli_client._prepare_prompt tuple return before test_claude_cli_system_prompt/test_claude_cli_bridge/test_claude_cli_bridge_mcp/e2e_claude_chat.
- src/data/google_access.py before test_google_access_gate.py and test_help_claims_knowledge-base.py.
- src/ui/app_modes.py PAGES changes (en_attention, en_help, home tab) + src/branding.py before test_app_modes/test_sidebar_layout/test_splash/test_home_page.
- tests/drive_eval/ package before tests/test_drive_eval_scorer.py (imports tests.drive_eval.scorer).
- Run the 10 test_help_claims_* files LAST as the port's final gate — they import ~40 src modules across data/services/ui and construct EnablementPage/MainWindow, so they fail on any incomplete port surface.

**Cross-file risks / silent failures:**
- The help_claims suites ARE a real final gate: they are hermetic (web tabs pinned off, Guru credentials pinned empty, no network/WebEngine) yet import and construct nearly every enablement module — an incomplete port fails them at import/collect time. But their xfail(strict=True) discipline cuts both ways: if the Mac port FIXES a documented discrepancy, a strict xfail will XPASS and fail the suite — that signals a behavior delta vs the Windows tree, not a broken test.
- Roughly 25 skips across the help_claims files are conditional on dev-machine state ('no local settings.yaml to inspect', 'operator has bootstrapped the KB'); on the Mac with data/ untouched they skip or run the fresh-state branch cleanly — do NOT 'fix' them by creating data/settings.yaml.
- Several tests read SOURCE text or use inspect.getsource for structural bans (test_help_search.py bans LIKE in src/data/help/search.py; test_home_bridge.py reads home_bridge.py; test_native_dialog_styling gates via getsource) — a port that rewrites those files 'equivalently' can still fail these tests; port the files verbatim.
- test_web_guardrails.py fails loudly if web/src is missing — that is a signal the web tree was omitted from the port, not a flaky test.
- test_native_dialog_styling.py samples real pixels of a briefly shown QMessageBox (threshold: luminance spread > 80, pre-fix ~12); macOS offscreen font rendering could land differently — if it fails marginally, check the corrective QSS is applied before blaming the platform.
- Nothing in this slice constructs QtWebEngine — all bridge/controller tests are explicitly WebEngine-free, so the exit-255 offscreen-Chromium teardown problem does NOT apply to any committed test here (only to the gitignored test_*_web_local.py files, which are not in the delta).
- Silent-failure mode: db_manager.initialize() is a 650+ line executescript — if the ported schema for help_articles/kb_* has a syntax error, subsequent CREATEs silently no-op and dozens of these tests fail with 'no such table'; run test_help_store + test_kb_extra_and_readopt early to smoke the schema.
- test_chat_engine.py has pump loops with up to 10s timeouts — a hang there usually means the fabricated-tool-call guard or grounding wiring was not ported, not a Mac issue.
- Time-anchored fixes (test_source_baseline now=, test_stage4 days=100_000) mean the OLD tests fail on any machine after enough wall-clock drift — if the porter sees those fail, port the test file too, not just src.

**macOS-specific:**
- Zombie-cleanup preamble translated for macOS (run before test groups and any E2E): `pkill -f alma_mcp_server; pkill -f chat_mcp_server; pkill -x gemini; pkill -f 'scan_server/server.js'` (append `|| true` in scripts; avoid a bare `pkill node` — kill only the scan_server node).
- Prefix EVERY Qt-importing test group with QT_QPA_PLATFORM=offscreen (only test_google_access_gate, test_renn_usage, test_system_panels, and the help_claims files set it themselves).
- Recommended macOS run for the NEW/CHANGED tests, in dependency order, groups of 3-4: (1) pytest tests/test_drive_eval_scorer.py tests/test_enablement_web_flag.py tests/test_html_sanitize.py tests/test_redaction_fail_closed.py -x -q; (2) tests/test_help_store.py tests/test_help_search.py tests/test_help_corpus_sync.py tests/test_enablement_doc_search.py; (3) tests/test_drive_query.py tests/test_drive_reader_search.py tests/test_asana_pagination.py tests/test_asana_setup_guard.py; (4) tests/test_turn_grounding.py tests/test_shared_session.py tests/test_claude_cli_system_prompt.py tests/test_claude_cli_bridge.py; (5) tests/test_chat_engine.py tests/test_chat_tools.py tests/test_claude_cli_bridge_mcp.py tests/test_scoped_usage_tracker.py; (6) tests/test_calendar_bridge.py tests/test_workbench_bridge.py tests/test_home_bridge.py tests/test_web_chat_drawer.py; (7) tests/test_home_web_controller.py tests/test_web_diag.py tests/test_web_guardrails.py tests/test_google_access_gate.py; (8) tests/test_system_panels.py tests/test_updater.py tests/test_manifest_fetcher.py tests/test_update_action_widget.py; (9) tests/test_renn_usage.py tests/test_settings_subtabs.py tests/test_phase5_release.py tests/test_app_modes.py; (10) tests/test_help_tab.py tests/test_sync_status.py tests/test_attention_dismissal.py tests/test_card_template.py; (11) tests/test_drive_folder_picker.py tests/test_drive_sa_status.py tests/test_drive_live_search_tools.py tests/test_native_dialog_styling.py; (12) tests/test_home_page.py tests/test_splash.py tests/test_page_return_reload.py tests/test_sidebar_layout.py; (13) tests/test_ingest_dedupe.py tests/test_kb_extra_and_readopt.py tests/test_demo_guru_suppression.py tests/test_doc_reader_unsupported.py; (14) tests/test_asana_readback.py tests/test_asana_writeback.py tests/test_task_detail_actions.py tests/test_enablement_live_cutover.py; (15) tests/test_enablement_ui_live.py tests/test_source_baseline.py tests/test_stage4_data_warehouse.py; FINAL GATE, pairs: (16) 'tests/test_help_claims_getting-started.py' tests/test_help_claims_reference.py; (17) tests/test_help_claims_renn.py tests/test_help_claims_troubleshooting.py; (18) tests/test_help_claims_settings.py tests/test_help_claims_plan.py; (19) tests/test_help_claims_insights.py 'tests/test_help_claims_knowledge-base.py'; (20) tests/test_help_claims_create.py tests/test_help_claims_workbench.py. All with QT_QPA_PLATFORM=offscreen python -m pytest ... -x -q.
- pip install python-pptx and PyYAML into the Mac venv if not present (arm64 wheels exist for both); python-pptx is needed by test_help_claims_create, PyYAML by drive_eval scorer.
- e2e_claude_chat.py is a manual live script requiring the `claude` CLI installed AND logged in on the Mac — exclude from any automated run.
- Quote the two hyphenated filenames in zsh commands: 'tests/test_help_claims_getting-started.py' and 'tests/test_help_claims_knowledge-base.py' (the hyphen is fine, but globbing/copy-paste errors are common).
- If any web-flagged surface renders blank during in-app verification, run `python scripts/web_diag.py` first (test_web_diag proves the macOS branch: Rosetta, Mach-O arch, framework corruption).

**Analyst notes / answered questions:**
- Do the help_claims tests gate the port? YES — hermetic but deep: they import ~40 src modules, build EnablementPage/MainWindow/SettingsPage, and quote exact UI strings; run them last as the acceptance gate. Their strict xfails will XPASS (fail) if the Mac build accidentally 'fixes' a documented discrepancy.
- Known env-failure list for a fresh no-credentials Mac: NOTHING in the new committed tests requires credentials — live paths are monkeypatched (build_live_drive_client, GuruClient.load_credentials, asana_setup.is_asana_connected, CliSubprocess). Guarded/expected-inert: e2e_claude_chat.py (manual script, needs claude CLI login), test_ai_reports_e2e (skips without ALMA_E2E_LIVE), test_enablement_e2e (skips without ALMA_LIVE), test_settings_model + several help_claims tests (skip without data/settings.yaml — desired on the Mac), test_taxonomy_drift (skips without production DB), test_gemini_chat_pipeline (skips without configured Gemini client). Pre-existing known failures outside this slice: test_reporting_foundation::test_bridge_fallback, test_feature_integration live-DB cases.
- Family map — help_claims (10 files, hermetic, Qt offscreen, final gate); help center (store/search/tab/corpus_sync — hermetic, tab needs Qt); kb (kb_extra_and_readopt, enablement_doc_search, google_access_gate — hermetic, need migs 046/049/050); drive (drive_query, drive_reader_search, drive_live_search_tools, drive_folder_picker, drive_sa_status, drive_eval_scorer — all mocked/hermetic; picker+sa_status need Qt); web bridges (calendar/workbench/home/chat_bridge + home_web_controller + web_flags + web_diag + web_guardrails — no WebEngine, run anywhere); system panels/updater (system_panels, updater, phase5_release, manifest_fetcher, update_action_widget — hermetic); usage metering (renn_usage, scoped_usage_tracker, settings_subtabs — hermetic, Qt); renn persona/CLI (claude_cli_system_prompt, claude_cli_bridge, claude_cli_bridge_mcp — hermetic; e2e_claude_chat live); grounding (turn_grounding, chat_engine — pure); asana (pagination, setup_guard, readback, writeback — hermetic, empty_db); UI regressions (attention_dismissal, sync_status, sidebar_layout, page_return_reload, native_dialog_styling, task_detail_actions, home_page, splash, app_modes, card_template, ingest_dedupe, demo_guru_suppression, doc_reader_unsupported — Qt offscreen). None are Windows-only; previously Windows-flaky time-anchored tests (source_baseline, stage4) are deflaked IN this delta.
- Unverified assumption for the plan-writer: DatabaseManager.initialize() (not only migrations/) creates help_articles/kb tables for the tmp-path fixtures — confirm the db_manager slice ports the schema additions, else test_help_store's help_db fixture fails before the loader runs.
- The corpus is 63 files but the sync-fix narrative says 62 — one article (likely troubleshooting/known-issues.md) landed after that count; no test hardcodes 62/63, so this is informational only.


