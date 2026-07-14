# Knowledge Base Architecture (WS2 — renn-calendar-kb-studio)

The enablement knowledge base is a **self-building, human-readable index in the
operator's own Google Drive**, mirrored locally for fast deterministic search.
**No embedding models** (product-owner decision, 2026-07-13): retrieval is
FTS5 + code-ranked hybrid scoring + entity-dictionary query expansion, with a
full-text floor so a fact a summary omitted is always still findable.

## The EC folder (cloud, source of truth for card content)

- App-created **`EC/`** folder in Drive (`drive.file` scope — the app can only
  ever touch files it created or the operator picked).
- One **topic subfolder** per topic (e.g. `eligibility-recheck/`), plus
  `published-cards/` (archives of every card Renn publishes to Guru) and
  `misc/` (topic-cap overflow).
- Each card is a Markdown file with YAML frontmatter (spec v1 in
  `src/data/kb/card_format.py`): `card_id` (`kb-<uuid8>`, also embedded in the
  filename so a human rename survives), `type`, `topics`, `source_id`,
  `source_modified` (the staleness key), `summary`, `key_facts`, provenance.
- Every folder carries an auto-maintained **`_index.md`** head file —
  regenerated deterministically from the mirror after each sync — so the tree
  is browsable at a glance. Edits to `_index.md` are overwritten; edit cards.
- Humans may hand-edit or hand-drop cards. **Pull wins, absolutely**: the sync
  absorbs Drive-side edits; broken frontmatter is **flag-only**
  (`needs_repair` in the mirror + never rewritten — `drive.file` cannot write
  foreign files anyway).

## The gate exemption (ratified D-GATE)

Writes **inside the EC folder** skip the human Confirm gate; every write
outside it stays gated. Enforcement is code, not prompt:

1. `src/data/kb/drive_kb.py` is the **single write chokepoint** — it raises
   `KBWriteDenied` unless the parent folder id is a non-quarantined row in
   `kb_folders` (migration 046). Tools never pass folder ids; they pass topic
   NAMES that code resolves.
2. **Topic-folder minting is code-gated**: Haiku-emitted topics are slugified
   + fuzzy-matched against existing folders; genuinely new folders need the
   product-area seed list or a per-job budget (global cap 40; overflow →
   `misc/`).
3. The MCP tool subprocess has **no Drive write path at all**
   (`google_oauth` raises under `ALMA_MCP_MODE`): tools only enqueue
   `kb_queue` rows; the main-process KBWorker validates and executes.
4. Every write is audited in `kb_sync_log`, and the whole tree is plainly
   readable in the operator's Drive.

## Sync engine (`src/data/kb/sync.py`, `worker.py`)

- **KBWorker** starts whenever `enablement.kb.enabled` is true (demo mode
  off) and checks `google_oauth.is_active()` **per tick** — OAuth is
  disable-on-launch, so a start-time gate would never fire.
- Tick: reset claim leases (15 min; 3 attempts → dead-letter) → expire stale
  pending work (7 days) → run queued **index jobs** → push queued card writes
  (check-and-set on Drive `modifiedTime`; conflict → discard push, pull, log)
  → pull every EC folder by `modifiedTime` cursor → periodically: full
  reconcile (deletions are invisible to cursors) + `_index.md` regeneration +
  the **staleness scan**.
- **Echo suppression**: each push records the update-response `modifiedTime`
  as `kb_cards.drive_modified`; the pull skips files matching it, so the
  app's own writes never re-ingest or ping-pong.
- **Writability probe** at bootstrap: `drive.file` grants are per-OAuth-
  client-ID, so an EC tree created by a dev build is readable but NOT
  writable by a release build — the probe (create+update `.kb_marker`)
  surfaces "re-bootstrap required" instead of eternal 403s. Trashed folders
  quarantine (a trashed folder still accepts writes — into the trash).
- All Drive writes route through `GoogleDriveExporter`'s **throttled surface**
  (0.5 s pacing + exponential backoff on 403 rate/429/5xx).

## Ingestion (`src/data/kb/ingest.py`)

`index_drive_folder` (chat tool, **enqueue-only**) → KBWorker executes:
enumerate (depth ≤ 3, ≤ 50 docs/job) → extract (Google Docs export, `.pptx`
via `pptx_reader` — previously silently empty — `.docx`, PDF via pypdf) →
Haiku summarize via the shared `llm_gen` validate-retry primitive (LLM failure
→ deterministic excerpt card; indexing never fails on model trouble) → card
mint + queued Drive push. The **full extracted text** also lands in
`enablement_documents.full_text` — the recall safety net.

## Update detection (WS2-M5)

The staleness scan compares each card's `source_modified` to the live source
`modifiedTime`: stale → re-extract → re-summarize → diff digest → `## Changelog`
append → re-queued push → an **attention task** (`source='kb'`,
`kind='card_review'`) stamped with the operator's Asana GID so it appears in
the default "Mine" calendar scope. A 404'd source marks the card
`source_missing` (never deleted). Publishing a draft to Guru enqueues a
**published-card archive** (full body) into `published-cards/`.

## Search (`src/data/kb/search.py`)

`kb_search` = 0.55·weighted-bm25 (title×3, topics×2, key_facts×2) +
0.25·term coverage + 0.10·title bonus + 0.10·recency, over an expansion of the
query through `config/entities/payers.json` + `product_areas.json` (pure
code — no LLM in the ranking path; MATCH input sanitized). Thin card results
fall through to a windowed-snippet LIKE over `enablement_documents.full_text`.
`kb_list_topics` / `kb_list_cards` enumerate completely; `kb_get_card` reads
one card. All error shapes steer a weak model (nearest matches, available
topics) instead of dead-ending.

## Status surface

Settings → Sources → **Knowledge base** card: enable toggle, EC bootstrap
button (off-thread, Google-gated), and the single degraded-state list
explaining why any background capability (Asana sync, briefs, Drive, KB) is
inactive — demo mode, missing PAT, Google not reconnected, KB disabled.

## Tests

Gitignored `tests/test_kb_*_local.py` (drive/store/sync/ingest/search/update-
detection) — run in groups of ≤ 4 with the other `*_local.py` files.
