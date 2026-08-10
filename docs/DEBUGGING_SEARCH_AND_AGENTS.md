# Debugging Guide — Search Stack & Agentic Flows

Practical diagnosis playbook for the two subsystems that fail in the most
confusing ways: lexical search (Drive/KB/help/Zendesk) and the linear agentic
chains (research runner, content-update pipeline, solver ledger, Claude CLI
bridge). Written 2026-07-26; verify file:line refs against the tree before
trusting them blindly.

---

## 0. First moves for ANY misbehavior

1. **Kill zombies.** Orphaned MCP/CLI subprocesses cause the weirdest
   symptoms (locks, port-less hangs, machine slowdown):
   ```bash
   wmic process where "commandline like '%alma_mcp_server%'" call terminate
   ```
   Between scans also kill `gemini.exe` / stray `node.exe`.
2. **WAL state.** A huge `data/local_warehouse.db-wal` after a crash means
   an aborted writer; boot runs `wal_health_check()` (main.py) and prints
   what it checkpointed. `database is locked` under load = a writer holding
   a long transaction — find it before raising busy_timeout.
3. **Web surface blank?** `python scripts/web_diag.py` first, always.
4. **Where output goes:** startup prints to stdout (`[startup] ...`);
   chat tool calls → `chat_tool_executions` table + JSONL under
   `data/logs/chat_tools/`; Qt paint exceptions → status bar via
   `qt_error_guard`.

---

## 1. The search stack

### 1.1 Know which path you are debugging

| Surface | Path | Tokenized? |
|---|---|---|
| `search_documents` / doc search | `enablement_store.search_documents` → `src/data/enablement_doc_search.py` over `enablement_documents_fts` (mig 050) | Yes — blended ranker, IDF-weighted coverage gate `WCOV_MIN=0.45` |
| `kb_search` | primary: `kb_cards_fts` (mig 046) — tokenized; floor: delegates to enablement_doc_search | Yes |
| Help Center search | `src/data/help/search.py` — the reference blended-ranker implementation | Yes |
| Zendesk mirror search | `zendesk_store.search_mirror` over `zendesk_articles_fts` / `zendesk_macros_fts` (mig 051, contentless) | Yes |
| `search_google_drive` | LIVE Drive API via `DriveReader.search_files` (SA creds; paginated + 429/5xx retry) | Drive's own |
| `search_everywhere` | local + live merged, deduped against the whole library | mixed |
| `search_drafts` | **still whole-query LIKE — phrase-only, known limitation** | **No** |

If "search is broken", first establish which row of this table you're in.
The historic 0%-recall bug was whole-query `LIKE '%query%'` — `FHIR` hit,
`what is FHIR` returned nothing. `search_drafts` still behaves that way by
design (documented in the help articles).

### 1.2 Diagnosis ladder (local FTS paths)

Work top-down; each step isolates one layer.

1. **Is the doc in the index at all?**
   ```python
   conn.execute("SELECT COUNT(*) FROM enablement_documents").fetchone()
   conn.execute("SELECT COUNT(*) FROM enablement_documents_fts").fetchone()
   ```
   Counts diverging = trigger problem (see 1.3). Doc missing entirely =
   ingest caps — `kb/ingest.py` `MAX_DOCS_PER_JOB=500`, `MAX_DEPTH=6`
   (docs deeper than 6 folders are silently dropped).
2. **Does raw FTS match?** Bypass the ranker:
   ```python
   conn.execute("SELECT rowid FROM enablement_documents_fts WHERE "
                "enablement_documents_fts MATCH ?", ('"eligibility"',))
   ```
   Raw hit but no search result → the ranker/gate is filtering (step 3).
   No raw hit → tokenization/content problem (wrong column, stemming
   expectation — FTS5 `unicode61` does NOT stem: `resubmissions` ≠
   `resubmission`).
3. **Is the wcov gate cutting it?** The precision gate is a normalized
   IDF-coverage ratio, threshold 0.45 — *not* an absolute score.
   Legit-but-filtered queries typically sit just under it; truly-absent
   topics cap ≈0.31–0.37. Print the per-query wcov by calling
   `enablement_doc_search` internals directly. A stopword issue looks like
   this too: filler words that aren't in the stopword list depress wcov
   ("mean"/"means" were added for exactly that reason).
4. **Ranking wrong but present?** The blended score = term coverage +
   title weight + relevance floor (help/search.py is the reference). Check
   whether the title carries the terms — title matches outweigh body.
5. **Full regression:** the gold set is committed —
   ```bash
   python -m pytest tests/test_enablement_doc_search.py -x -q
   ```
   and the live harness (scratch DB, never the warehouse):
   ```bash
   python scripts/run_drive_eval.py --folder-id <wrapper-id> --index-mode mirror \
     --max-docs 300 --queries tests/drive_eval/gold.yaml --search-mode kb \
     --limit 10 --db <scratch.db> --report <out.md>
   ```
   Baseline to beat: recall@10 0.932, MRR 0.822, 21/22, empty-result
   precision clean (2026-07-21 live run).

### 1.3 FTS trigger discipline (mirror desync)

The contentless FTS mirrors (mig 050/051 pattern) require the
delete-then-insert trigger form, with delete rows passing the ORIGINAL old
values. Symptoms of desync: search returns rows that were deleted, or
misses rows that exist. Repair = re-run the migration's backfill block
(`INSERT INTO fts(fts) VALUES ('delete-all')` + `INSERT ... SELECT`).
**Never `INSERT OR REPLACE` into mirror tables** — it fires the delete
trigger with the NEW row's values, silently corrupting the FTS mirror
(grep-guarded by `tests/test_zendesk_mirror_schema.py`).

### 1.4 Live Drive search

- SA creds: `enablement.drive.credentials_path`; the SA **reads only**
  (0 storage quota on personal Drive) — writes fail by design.
- Folder-scoped search is **non-recursive** (`in parents` = direct
  children); a nested corpus scoped to its wrapper returns 0. Whole-Drive
  search works.
- 403 → wrong GCP project or Drive API disabled; 429/5xx → the retry layer
  logs and backs off; a hang → check pagination loop.

---

## 2. Linear agentic chains

### 2.1 The shapes in play

- **content_update** (`src/data/content_update/`): load → find → pull →
  identify → write → validate → stage. Typed result per stage
  (`PipelineResult.stage` names the failure point), `ambiguous_card` parks
  for a human pick, validate is warn-only (issues ride to the reviewer),
  and `llm_json.call_llm_json` is the one bounded re-ask (JSON shape only).
- **research_runner** (`run_research_job`): sequential per-step generate()
  loop, progress into `agent_jobs`, manifest into `task_research`.
  **Currently unwired — zero production callers**; drive it manually (see
  2.4) when debugging.
- **solver ledger** (mig 052 + `src/data/solver_ledger.py`): the audit
  spine the future solver runner will drive; already fully functional for
  simulation (see 2.5).

### 2.2 Reading the ledger like a debugger

- **Call stuck in `spawned`** = the subprocess died/hung without
  `finish_call` — the reaper's signal (`mark_interrupted_calls`). If you
  see these after a clean session, the runner lost track of a call.
- **`resolve_watermark` → `unknown`** is *deliberate* ambiguity: the tool
  ledger changed underneath the snapshot (deletion, anchor mismatch,
  arithmetic drift). Never treat unknown as pass — solver gates park on it.
- **Draft transition returns** are exact strings: `invalid_transition`
  (matrix violation — e.g. pending→executed skips review),
  `status_changed` (CAS drift — someone else moved it), `not_found`.
- **Audit trail query** (what the Q/A screen renders):
  ```sql
  SELECT c.stage, c.attempt, c.purpose, c.status, c.cost_usd,
         k.name, k.verdict, k.consumer_action
  FROM solver_calls c LEFT JOIN solver_checks k
    ON k.job_id = c.job_id AND k.stage = c.stage AND k.attempt = c.attempt
  WHERE c.job_id = ? ORDER BY c.started_at;
  ```

### 2.3 Claude CLI bridge failure signatures

| Symptom | Likely cause | Check |
|---|---|---|
| Model refuses / acts out of persona | system prompt rode argv or cwd leaked CLAUDE.md | must be `--system-prompt-file` + neutral cwd (`%TEMP%/alma_cli_neutral`) |
| MCP tools "ran" but results are fabricated | permission mode ≠ `bypassPermissions` (all other modes silently deny in `-p`) | `_build_cmd` flags |
| Hang with no output | Bedrock auth stall — bridge timeout only fires when a stdout LINE arrives | external watchdog; check `bedrock.enabled` + creds |
| Silent wrong account | Bedrock env injection failed → CLI fell back to OAuth login | verify `CLAUDE_CODE_USE_BEDROCK=1` in the child env; preflight before batches |
| Truncated/garbled output | stream-json parse — check stderr ring buffer (`stderr_tail`, 200-line cap) | salvage chain before re-ask |
| Machine slows over a session | zombie CLI/MCP children | §0 cleanup |

### 2.4 Driving research_runner by hand

```python
from src.data.db_manager import DatabaseManager
from src.data import agent_jobs, research_store
from src.data.research_runner import run_research_job

db = DatabaseManager(db_path="scratch.db"); db.initialize()
conn = db.conn
job_id = agent_jobs.create_job(conn, title="probe", kind="task_research")
rid = research_store.create_research(conn, task_id="t1", title="probe")
run_research_job(conn, job_id, "t1", rid, "Probe task",
                 ["step one", "step two"],
                 generate=lambda p: f"[fake] {p[:40]}",
                 is_cancelled=lambda: False)
print(research_store.latest_for_task(conn, "t1"))
```
Swap the lambda for `default_generate()` to test the real CLI lane
(one paid call per step). Cancellation is cooperative at step boundaries
only — a hung generate() needs the bridge kill path.

### 2.5 Simulating a solver job

`solver_ledger` needs no runner to be exercised: begin/finish calls,
tool execs + watermark, checks (consumer_action mandatory), stage results
with provenance, draft + citations + transitions all work against any
initialized DB. See `tests/test_solver_ledger.py` for canonical usage of
every primitive; a full simulated-job script lives in the practical-test
suite (scripts/ or scratchpad) and prints the audit trail above.

---

## 3. Appendix — command crib sheet

```bash
# zombies
wmic process where "commandline like '%alma_mcp_server%'" call terminate

# focused test groups (NEVER the whole tests/ dir — hangs on Windows)
python -m pytest tests/test_enablement_doc_search.py tests/test_help_search.py -q
python -m pytest tests/test_solver_ledger.py tests/test_app_paths.py -q

# search gold-set regression + live harness — see §1.2
# web surface diagnosis
python scripts/web_diag.py

# boot without splash (dev)
python main.py --no-splash
```
