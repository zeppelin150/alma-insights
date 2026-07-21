# Google Drive Search — Evaluation Harness

> **Status: MEASURE-ONLY.** This harness exists to *size* the gaps in Drive
> search before anyone repairs them. The defects listed here are deliberately
> **not fixed**. Several are pinned by `xfail` tests that will start failing
> the day someone fixes them — that is the intended signal, not a regression.

---

## ⚠️ Keep the test Drive synthetic — no PHI

`index_drive_folder` extracts document text and sends it to Haiku via the
local `claude` CLI ([`src/data/kb/ingest.py:71-82`](../src/data/kb/ingest.py)),
and the enablement lane **disables aggressive PII redaction**
(`build_client_for_task("enablement_*")`). Extracted text is also persisted in
full to `enablement_documents`.

**The evaluation Drive must contain only synthetic documents.** No real
patient data, no real member IDs, no real provider names, no exported ticket
bodies. Invent everything. If a document would be a problem in a screenshot,
it is a problem here.

`--index-mode mirror` (the runner's default) does **not** call the LLM — it
only stores text locally. Prefer it unless you specifically need cards.

---

## The three-stage funnel

"Google folder search" is not one feature. It is three, and only the third
actually runs in production today.

| # | Stage | Entry point | Reality |
|---|-------|-------------|---------|
| 1 | Find a folder | React `DriveFolderPicker`, [`web/src/chat/ChatApp.jsx:452-602`](../web/src/chat/ChatApp.jsx) | **No search/filter box.** Its sibling `AsanaBoardPicker` (~612-619) has one. The native Qt path is a raw folder-ID `QInputDialog`. |
| 2 | Search Drive live | [`DriveReader.search_files`](../src/data/drive_reader.py) `:209-220` | **Unreachable.** See below. |
| 3 | Search the ingested mirror | [`kb_search`](../src/data/kb/search.py) FTS5 + tokenized `enablement_documents_fts` floor (was a whole-query LIKE — **D6**, now fixed) | The only stage that runs. |

### Stage 2 is now wired (live-search build)

`query_business_drive` ([`src/data/drive_query.py`](../src/data/drive_query.py))
branches on an injected `live_client`. It is now populated by a dedicated tool:
**`search_google_drive`** ([`enablement_tools.handle_search_google_drive`](../src/data/chat_tools/enablement_tools.py))
builds the service-account client via `drive_query.build_live_drive_client()`
and injects it, so Renn can search **live** Google Drive (find-without-sync).
`search_everywhere` runs local + live together; `import_drive_doc` bridges a
live find into the tokenized local library.

`query_business_drive` itself stays injection-only (its handler still takes the
`local_index` mirror branch — the `search_local_documents` / mirror path), so
its unit tests remain hermetic. The historical note below described the
pre-wiring state:

Both call `query_business_drive(conn, query, limit=...)` and omit the seam, so
the chat tool **always** takes the `local_index` branch — regardless of
`enablement.drive.read_enabled`. Worse, the response still reports
`configured: True`, because that flag describes a *setting*, not the branch
that ran. Anyone reading a report row cannot tell live results from mirror
results.

`scripts/run_drive_eval.py --search-mode live` injects the seam manually. It
is the only way to exercise stage 2 at all.

---

## Confirmed defects

Every row was verified by direct read, and all but the last two are pinned by a
test in this harness.

| # | Defect | Location | Pinned by |
|---|--------|----------|-----------|
| D1 | `list_changed_files` accepts `recursive=` and **never reads it**. The `q` is always `<id> in parents` — direct children only. `drive_monitor.poll_once` passes `recursive=True` believing it recurses, so **subfolder files are silently never indexed by the monitor**. | [`drive_reader.py:159-183`](../src/data/drive_reader.py), caller [`drive_monitor.py:57-59`](../src/data/drive_monitor.py) | `test_drive_query.py::test_real_reader_ignores_recursive_parameter`, `::test_monitor_passes_recursive_true_and_still_misses_subfolders` |
| D2 | ✅ **FIXED** (live-search build). `search_files` now pages through `nextPageToken` to the caller's `limit` and takes an optional `folder_id` scope. | [`drive_reader.py`](../src/data/drive_reader.py) `search_files` | `test_drive_reader_search.py::test_search_files_follows_nextpagetoken` |
| D3 | ✅ **FIXED for `search_files`** (retries transient 429/5xx with exponential backoff via `_read_with_retry`). The bulk index read methods (`list_*`, `export_text`) still call `.execute()` bare — a follow-up if a 100-doc job trips limits. | [`drive_reader.py`](../src/data/drive_reader.py) | `test_drive_reader_search.py::test_search_files_retries_on_rate_limit` |
| D4 | Recency is a **string compare**: `(source_modified or '') >= '2026'`, yielding a binary 1.0/0.5 rather than a decay. A Jan-2026 doc and a Dec-2026 doc score identically; a 2025 doc and a 1999 doc also score identically. | [`kb/search.py:133`](../src/data/kb/search.py) | documented here; measured by category in the report |
| D5 | FTS `MATCH` is capped at **24 terms** (`safe[:24]`) *after* alias expansion. Entity aliases can consume the budget, silently truncating a long query. | [`kb/search.py:90-95`](../src/data/kb/search.py) | documented here |
| D6 | ✅ **FIXED** — migration `050_enablement_documents_fts.sql` + [`enablement_doc_search.py`](../src/data/enablement_doc_search.py). *Was:* the floor substring-matched the entire raw query (`LIKE %<whole query>%`), so `"aetna prior authorization"` missed a doc whose body said exactly that unless the phrase appeared **contiguously** — the single biggest recall limiter on the only stage that runs. Both `search_documents` and `kb/search`'s floor now tokenize and rank (IDF-weighted, with a relevance floor) over `enablement_documents_fts`. | [`enablement_store.py:88-107`](../src/data/enablement_store.py), [`kb/search.py:144-171`](../src/data/kb/search.py) | `test_drive_query.py::test_local_branch_tokenizes_multi_word_queries`, `test_enablement_doc_search.py` |
| D7 | The `limit` is applied **before** the `source=='drive'` filter, so non-Drive documents consume the result budget. With 10 newer Guru docs, a `limit=10` Drive query returns **zero** Drive rows even though one matches. | [`drive_query.py:48-49`](../src/data/drive_query.py) | `test_drive_query.py::test_local_branch_limit_is_applied_before_the_drive_filter` |
| D8 | Stage 1 has **no folder search box** — the picker is a manual tree walk. | [`ChatApp.jsx:452-602`](../web/src/chat/ChatApp.jsx) | not pinned (UI) |
| D9 | `_q()` escaping is **duplicated inline** in `gdrive_export` (three copies at `:219-221`, another ~`:239`) rather than imported. Two implementations of a query-injection guard can drift. | [`gdrive_export.py:219-224`](../src/export/gdrive_export.py) | `test_drive_reader_search.py::test_q_escaping_is_duplicated_inline_in_gdrive_export` (asserts they still agree) |

**Not defects** (verified, worth recording so they aren't "fixed" by mistake):

* `_q()` escaping is **correct** — backslash before quote, so injection like
  `x' or name contains 'y` stays inside one literal. Pinned by
  `test_search_files_injection_attempt_stays_one_clause`.
* `search_files` **does** pass all three Shared-Drive flags (`corpora`,
  `includeItemsFromAllDrives`, `supportsAllDrives`).
* `kb.ingest.enumerate_folder` ([`ingest.py:85-105`](../src/data/kb/ingest.py))
  does its **own** breadth-first walk and passes `recursive=False`, so the KB
  indexer *does* reach subfolders. D1 is scoped to the monitor path only.

### Remedies must stay lexical

`kb/search.py:1-3` records an owner lock: **no embedding models on the
enablement lane.** Every fix proposed from this evaluation must therefore be
lexical — alias tables (`config/entities/`), FTS5 tokenizer settings, bm25
column weights, the 24-term cap, the LIKE floor's trigger condition and term
splitting, the limit/filter ordering. **Do not propose a vector index.**

---

## The deliverables

| File | What it does |
|------|--------------|
| [`scripts/drive_eval_preflight.py`](../scripts/drive_eval_preflight.py) | Read-only machine truth + GO/NO-GO per stage. No network, no writes, no LLM. Run this first. |
| [`scripts/run_drive_eval.py`](../scripts/run_drive_eval.py) | The live runner: index a folder, run queries, score. |
| [`tests/drive_eval/scorer.py`](../tests/drive_eval/scorer.py) | Deterministic metrics. No network, no LLM, no clock. |
| [`tests/test_drive_reader_search.py`](../tests/test_drive_reader_search.py) | Stage-2 contract + the pagination/backoff xfails. |
| [`tests/test_drive_query.py`](../tests/test_drive_query.py) | The `live_client` seam + the `recursive=` gap. |
| [`tests/test_drive_eval_scorer.py`](../tests/test_drive_eval_scorer.py) | Metric definitions pinned against hand-computed values. |

All tests are headless and require **no network, no credentials, and no
`GOOGLE_*` env**. `run_drive_eval.py` lives in `scripts/`, and pytest's
`testpaths = tests` means it is never collected.

---

## How to build the test Drive

You are authoring the corpus; the harness does not seed it. The goal is a
corpus where a *naive* search looks fine and a *real* one is needed to
separate the good result from the plausible one. Aim for **150-400
documents**. Below ~100 everything ranks top-10 by accident.

### Noise floor

* **~80% filler.** Meeting notes, old onboarding decks, duplicated policy
  drafts, exports nobody reads. Filler must share vocabulary with the needles
  — "prior authorization", "denial", "onboarding" scattered through
  irrelevant documents. Noise that shares no words with your queries tests
  nothing.
* Vary length hard: a few 1-page memos, a few 60-page PDFs. The `MAX_DEPTH=3`
  and `MAX_DOCS_PER_JOB=50` caps in
  [`kb/ingest.py:28-29`](../src/data/kb/ingest.py) (**verified**) mean a large
  folder gets truncated — which is itself worth observing.

### The structures that actually discriminate

1. **Near-duplicate titles differing only by year.**
   `Aetna Prior Auth Runbook 2024 / 2025 / 2026`, near-identical bodies, only
   the turnaround number differs. Directly probes **D4** (binary recency).
   Ask "what is the current turnaround" and see which year ranks first.

2. **A buried needle.** One document whose distinguishing fact lives deep in
   the body — page 30+ of a PDF, slide 37 of a deck — and whose **title says
   nothing about it**. This is the stated purpose of the full-text floor. If it
   cannot be found, the floor is not doing its job.

3. **Title-says-X, body-says-Y.** A doc titled *Provider Onboarding Guide*
   whose body is 90% claims-denial content. Ask a denial question. Probes the
   `0.10 · title_hit` bonus in `kb/search.py:131-134`.

4. **Multi-word natural-language queries.** The most important case. Create a
   doc containing "Aetna requires prior authorization for advanced imaging"
   and query `aetna prior authorization turnaround`. This used to return
   **nothing** (**D6**: the LIKE floor required a contiguous phrase); with the
   tokenized ranker it now hits. Include several of these — the before/after
   here is the headline number and the whole point of the eval.

5. **Unicode and apostrophe filenames.** `Résumé Screening.docx`,
   `O'Brien Clinic Onboarding.gdoc`, `Policy — Q3 (final) v2.pdf`, and one
   with a backslash if your OS permits. Exercises `_q()` escaping end to end
   and the FTS tokenizer.

6. **A folder with >100 children.** `pageSize=100` is the page boundary in
   `list_folders`/`list_changed_files`. Those loop correctly; `search_files`
   does **not** (**D2**). 120-150 files in one folder makes the difference
   observable.

7. **Nesting past depth 3.** Put a uniquely-worded document at depth 4+
   (`A/B/C/D/needle.gdoc`). `MAX_DEPTH=3` means the KB indexer must miss it —
   confirm it does, and confirm the monitor misses depth **1** (**D1**).

8. **A Shared Drive branch.** At least one folder on a Shared Drive, not My
   Drive. Verifies the `corpora='allDrives'` flags do what they claim.

9. **A trashed folder** containing a document that would otherwise be a strong
   match for one of your queries. `trashed = false` must exclude it. If it
   appears in results, that is a new defect.

10. **Two documents that are exact duplicates** with different names. Reveals
    whether ranking is stable or arbitrary.

### Queries

Write **20-40**, tagged by category — the report breaks metrics down by
`category`, which is where the diagnosis comes from. Suggested tags:
`exact-phrase`, `natural-language`, `buried-body`, `year-disambiguation`,
`title-body-conflict`, `unicode`, `deep-nesting`, `shared-drive`, `acronym`,
`should-return-nothing`.

Include a few queries that **should** return nothing. A system that always
returns something is not discriminating, and only these detect it.

---

## Ground truth — what to hand back

Either format works; **A is strongly preferred.**

### A. `gold.yaml` (preferred)

You declare the answer per query. This is the **only** mode that detects a
total miss — a document never retrieved cannot be judged.

```yaml
queries:
  - id: q1
    query: "what is the current Aetna prior auth turnaround"
    category: year-disambiguation
    relevant: ["Aetna Prior Auth Runbook 2026"]
    notes: "2024/2025 versions exist and are near-identical"

  - id: q2
    query: "which denial code means missing authorization"
    category: buried-body
    relevant: ["Claims Denial Playbook"]

  - id: q3
    query: "employee stock purchase plan"
    category: should-return-nothing
    relevant: []
```

`relevant` may name documents by **title or by doc id** — the scorer matches
either (titles are casefolded and whitespace-collapsed). Multiple entries are
fine. Titles keep punctuation significant, so `Runbook (2026)` and
`Runbook 2026` stay distinct — deliberate, given near-duplicate titles.

### B. Judgment CSV

Use when you have not read the corpus closely enough to know the answers. The
runner emits every result in rank order with a blank `relevant` column; you
mark `1`/`0`; ground truth is derived.

```
python scripts/run_drive_eval.py --queries queries.txt --report report.md
#   -> writes report.judgments.csv
# mark the 'relevant' column, then:
python scripts/run_drive_eval.py --judgments report.judgments.csv --report report.md
```

**Mode B has a ceiling, stated in every report it produces:** it can only judge
what was *retrieved*. Recall computed from judgments alone is an **upper
bound** on true recall. A query you judged where nothing was relevant is a
confirmed failure but has no gold document, so it cannot be scored — the
report counts these separately (`n_judged_no_relevant`) and warns that the
averages flatter the system. Leave a cell blank for "unjudged"; that is
tracked distinctly from an explicit `0`.

---

## Running it

```bash
# 1. readiness (always safe)
python scripts/drive_eval_preflight.py

# 2. what can the account see? (read-only; proves calls were made)
python scripts/run_drive_eval.py --list-drives

# 3. mirror a folder locally — no LLM, no Drive writes
python scripts/run_drive_eval.py --folder-id <FOLDER_ID> --index-mode mirror \
    --queries gold.yaml --report evals/drive/report.md

# 4. full KB build (LLM + Drive writes) — requires the explicit flag
python scripts/run_drive_eval.py --folder-id <FOLDER_ID> --index-mode kb \
    --yes-i-understand-this-writes
```

`--search-mode` selects the stage: `kb` (production path, default), `local`
(the `query_business_drive` local branch), `live` (**injects the dead seam** —
the only way to exercise stage 2).

### Reading the proof-of-call block

Every run ends with a `[proof]` table, because **a green run can silently mean
"no calls happened"**: `list_drives` and `list_folders` return `[]` *without
building a service* when unconfigured
([`drive_reader.py:111,137`](../src/data/drive_reader.py)).

* `PROVEN` — a service was constructed and the call succeeded.
* `FAILED` — a service was constructed, the request was sent, the API rejected
  it. A credentials/scope/API-enablement problem, **not** an empty Drive.
* `UNPROVEN` — the call returned without constructing a service. Empty results
  reflect an unconfigured client, not an empty Drive. **Never report these as
  a result.**

### Metrics

`recall@1/3/5/10`, `precision@5`, `r_precision`, `MRR`, plus a per-category
breakdown. Note that `precision@5` is capped at `|relevant|/5` — a
single-needle query can never exceed 0.2, so read `r_precision` (normalized by
`|relevant|`, reaches 1.0) as the fairer single-needle number.

Reports are **byte-deterministic**: no timestamp is emitted unless the caller
supplies one, so two runs can be diffed to attribute a change to the retrieval
code rather than the harness.

---

## Current machine state (2026-07-20)

From `scripts/drive_eval_preflight.py` on the dev box:

* `enablement.drive.auth_type` — **absent**, defaults to `service_account`.
* `credentials_path` — set and the file **exists**.
* `google_oauth.is_active()` — `False`, as expected (disable-on-launch,
  [`google_oauth.py:66-67,220-223`](../src/data/google_oauth.py)). A
  standalone script cannot inherit an in-app session, so the **service-account**
  path is the only one available to the runner.
* `enablement.kb` — **section absent entirely**. The KB was never bootstrapped,
  so `kb_search` currently returns only the tokenized `enablement_documents_fts` floor.
* `active_folders` — **empty**. Pass `--folder-id` explicitly.
* `demo_mode` — `false`. `web_tabs` — `off`. React bundle present.

**Live blocker found:** `--list-drives` reached the API and was rejected with
`403 accessNotConfigured` — *"Google Drive API has not been used in project
&lt;project&gt; before or it is disabled."* The service account's GCP project
does **not** have the Drive API enabled. Stage 1 and 2 cannot run until that
is turned on in the Cloud console (or the owner connects a Google account
in-app and `auth_type` is switched to `oauth_user`).
