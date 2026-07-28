# Zendesk Block Port Guide — `d27eb3e` → `a8ead66` (enablement-content-tabs)

**Written:** 2026-07-28 on the Windows dev box, from the actual git delta. **Retargeted the same day** from `4ccd321` to `a8ead66` — see §1 for the four commits that moved the target, and §4.2, which changed from a hand-create step into a verify step as a result.
**For:** the owner, on the production Mac (Apple Silicon M1, 16 GB, arm64). You are competent with a terminal but you did not write this code, and you should not have to read any of it to execute this guide.
**Scope:** every change committed between `d27eb3e` (2026-07-24, "docs(port-guide): Appendix A — corrected delivery path via GitHub Desktop") and `a8ead66` (2026-07-28, "test(guru): lock the legacy guru_page preview to the publish body"), all pushed to `origin/enablement-content-tabs`.

**The delta:** 11 commits, **85 files** (42 added, 43 modified, **zero deleted, zero renamed** — a copy-over sync is complete and nothing on the Mac needs removing), +28,352 / −761 lines. 2 new SQL migrations (051 and 053 — the gap is real and safe, §7). **Zero new dependencies:** `requirements.txt`, `PACKAGES.md`, `web/package.json` and `web/package-lock.json` are byte-identical across the whole range. **The Mac needs no `pip install` and no `npm install`.** That removes the single largest historical source of Mac port pain from this run.

**What makes this port different from the last one.** `PILOT_FIX_PORT_GUIDE.md` was a patch: five surgical FIND/REPLACE hunks into code the Mac already had. **This is an addition.** The entire Zendesk mirror subsystem — the local mirror schema, the importer, the controller, the bridge, the React workspace, the Renn revision tools — does not exist on production at all. 42 of the 85 files are brand new and copy wholesale with nothing to reconcile. The risk in this port is concentrated in the **43 modified files**, and inside those, in a much smaller set of about eight that are load-bearing in non-obvious ways. §1 and §12 name them individually.

**The breaking change:** commit `2cc0089` **deletes the app's ability to write to Zendesk, permanently.** Before this range the app could POST/PUT to a live, public, non-restorable Guide instance. After it, there is no write method left to call. This is a locked owner decision, and two files must be synced **as a pair** or the policy silently reverts — see §1/`2cc0089` and §12.B.

**Transfer mechanism (the owner's decision — do not substitute another):** the code arrives as a **GitHub source zip** of this branch, downloaded by you, inflated on the Mac, compared locally against the existing tree, then synced file-over-file. There is **no `git fetch` / `pull` / `checkout` in this flow**, even though the Mac checkout has a configured remote. Git on the Mac is used only for a rescue snapshot and as an audit trail — never to move code. The one exception is Phase 8b, which moves no code at all.

This guide **supersedes (but does not delete)** [`PILOT_FIX_PORT_GUIDE.md`](PILOT_FIX_PORT_GUIDE.md), whose tip `d27eb3e` is this guide's baseline. That guide's **Appendix A file-copy delivery path and its ground rules remain binding** — this guide restates them rather than replacing them. It is a sibling of, not a replacement for, [`MAC_SURGICAL_UPDATE_GUIDE.md`](MAC_SURGICAL_UPDATE_GUIDE.md) (`7adbe7b` → `dbb5c8f`), whose Section 0 hard invariants also remain binding.

**Companion documents:** [`ZENDESK_GURU_IMPLEMENTATION_NOTES.md`](ZENDESK_GURU_IMPLEMENTATION_NOTES.md) — the author's own record of what shipped, what is deferred, and what was never verified. It arrives in the zip as part of this range. Open it when a gate here points at it; do not execute from it. [`MAC_SURGICAL_UPDATE_REFERENCE.md`](MAC_SURGICAL_UPDATE_REFERENCE.md) — the lookup table for macOS WebEngine failures. Execute from THIS file only.

---

## 0. Absolute rules — read before touching anything

These override anything else you infer. Violating any one of them is the failure mode this guide exists to prevent.

1. **NEVER touch `data/`.** The zip contains no `data/` directory — it is gitignored, so it is not in the archive and a file-over-file sync cannot reach it. Do not edit, "fix", add keys to, or reformat `data/settings.yaml`, even when a feature in this port appears to need a key. The one settings change this port contemplates (`enablement.web_tabs`) is **owner-gated and comes last**, after acceptance, and is listed in §10 as explicitly out of the port body. If something else needs a key, report it; do not set it.
2. **NEVER touch `~/.alma-insights/ui_state.json` either.** Rule 1's protection stops at `data/` — but the Zendesk subdomain, email and view id live in that JSON file outside the project tree, and the API key lives in the macOS Keychain. The sync cannot reach either, and neither should you. They are backed up in Phase 1 as insurance, not as a thing to change.
3. **NEVER pass `--delete` (or any `--delete-*` flag) to any `rsync` involving the project tree.** The zip has no `data/`, no `.git`, no `.venv` and no gitignored files, so a deleting sync would destroy exactly the things rules 1 and 2 protect. The range has zero deletions and zero renames, so a plain copy-over is already complete — `--delete` can only subtract things you need.
4. **NEVER run `npm build` / `npm --prefix web run build`.** The React app ships as a committed build artifact: `src/ui/web/dist/index.html` (**333,921 bytes at `a8ead66`** — unchanged since `45aab99`, single-file bundle) plus `src/ui/web/dist/qwebchannel.js` (15,152 bytes, unchanged since June and still required). The Mac needs **no Node and no npm at all** to run the app. Rebuilding overwrites `dist/` with non-identical bytes and puts the Mac out of parity with what was tested.
5. **NEVER run a migration by hand with `sqlite3 data/local_warehouse.db < migrations/051_….sql`.** Both new migrations carry mandatory Python post-hooks (§7). A raw `sqlite3` apply skips them, leaves the FTS mirrors indexing empty body text and the drafts with no rollback baseline, and records nothing in `schema_migrations` — so the migrator will later try again on a half-built schema. Migrations land **only** by launching the app or by the explicit `DatabaseManager().initialize()` call in §7.
6. **NEVER run the full test suite** (`pytest tests/`) — it hangs on this project. Run the four named groups in §8, each in its own invocation.
7. **NEVER relax `tests/test_zendesk_readonly_guard.py`.** It AST-scans every module under `src/` for a re-added Zendesk write method and also asserts the GET lanes still exist. If it fails on the Mac, a stale file survived the sync — **remove the write, do not edit the guard.**
8. **NEVER "fix" shipped quirks.** The Versions/History React panels are wired to nothing on purpose. The classic tab's attributes are still named `_a_push` / `_m_push` on purpose. The dormant-looking things in §11 are dormant by design; the known-red tests in §8 are known-red. Report them, do not repair them.
9. **Port order is not negotiable where stated**, and do NOT substitute git mechanics for the zip flow: no fetch, no pull, no checkout, no cherry-pick, no `git apply`. Phase 8b is the only git step that moves anything, and it moves metadata only, after acceptance.
10. **When something is unclear or a gate fails, STOP and report.** Do not improvise around a failed gate. Every gate in this guide states what it means when it fails.

Two conventions borrowed verbatim from `PILOT_FIX_PORT_GUIDE.md` and still in force:

- Where this guide gives file contents to create, it is **CREATE file with exact contents**. Nothing is to be improvised, adapted, reformatted or "improved". Python blocks are indentation-sensitive — copy leading spaces exactly, **never tabs**.
- Copy special characters exactly: `—` (em-dash), `…` (ellipsis), `→`, `·`, `≥`. Tests assert on some of these. Do not substitute ASCII lookalikes.

---

## 1. What you are porting — feature map by commit

Newest last. Read this so you know what "done" looks like before any command runs. Per-file detail is in §12; the full manifest is Appendix A.

| Commit | Date | What it is |
|---|---|---|
| `1ba6fa5` | 07-24 | **The bulk of the range** (49 files, +10,342/−245). A complete second Zendesk surface: a React/QtWebEngine clone of Zendesk's Guide article editor and Admin Center macro editor on SPA route `#/zendesk`, running over a **local SQLite mirror** (migration 051), plus a file importer, a GET-only paged pull, and a Renn tool family that proposes edits as `pending` drafts. The native `ZendeskPage` stays the flag-off default. |
| `fabb4be` | 07-26 | Cosmetics only (5 files, +198/−193). Collapses the Garden chrome into one 52px header row; import controls fold into the Mirror menu. **Zero behavior change** — handlers, headless hooks, keyboard access and the bridge surface are untouched. Entirely subsumed by the tip bundle; you never handle this commit individually. |
| `2cc0089` | 07-26 | **BREAKING** (20 files, +2,388/−451). Deletes the app's Zendesk write capability at the transport layer. Five methods removed outright, replaced by one choke point that raises on any non-GET. `publish_article_draft` / `publish_macro_draft` keep their signatures but become local bookkeeping. The classic tab's "Push to Zendesk" becomes "Copy for Zendesk" + "Mark as copied". Adds the permanent guard test. |
| `5743ef3` | 07-26 | Specialist editing + the clipboard invariant (19 files, +5,125/−306). Body edits land as revisions with Python recomputing and sanitizing the HTML, so reviewed content and copied content cannot diverge. **Nothing reaches the clipboard that a human was not shown at the moment it went there.** Rendered article becomes primary, raw HTML a collapsed disclosure, with a new additive preview-sanitize profile. Also repairs two silent breakages shipped in `1ba6fa5`. |
| `45aab99` | 07-27 | **The commit that makes this range not only about Zendesk** (44 files, +9,855/−530). Zendesk half: every markup-bearing clipboard release now passes a native dialog showing the exact bytes; preview CSS policy inverted to an allowlist; `force_plain_text` on native dialogs. Versions layer phase 1 lands **dormant** (migration 053 + store module + two React panels that nothing imports). **Guru half:** `publish_body()` becomes the single definition of the published body, fixing two confirmed criticals — attaching a quiz published *only* the quiz over live cards, and approval surfaces rendering markdown while publish sent a different HTML column. |
| `ed6482b` | 07-27 | Security fix (3 files, +72/−20). A model-written draft `title` containing newlines could inject fake field lines into the publish confirm dialog and push the real destination below the pane's fold. Every field is now collapsed to a single line and capped before the join. **The fix is entirely Python-side** — the bundle change in this commit is pure re-minify churn. |
| `4ccd321` | 07-28 | Documentation only (1 file, +41/−17). Records that the four approval-gate criticals were closed in `45aab99` and re-verified, states the final invariant, and documents the `publish_collection_id = "col-1"` config trap (§13.2). **Zero code change.** |
| `1aa0ef2` | 07-28 | **The reason this guide was retargeted** (1 file, +124). Commits `src/data/app_paths.py`, which was already imported by committed code at `4ccd321` but had never been added to the index — so a zip taken at `4ccd321` shipped a tree whose Zendesk **Import files…** / **Import folder…** buttons raised `ModuleNotFoundError` inside a bridge that swallows exceptions, i.e. did nothing at all, silently. See §4.2. |
| `24c8d0f` | 07-28 | **This guide itself** (1 file, added). Documentation only, zero code change. Its presence in the zip is now expected, not an anomaly — §4.1. |
| `30fb086` | 07-28 | Startup convenience (1 file, +9). `main.py` calls `app_paths.ensure_docs_tree()` after the splash, inside a `try/except` that prints and continues. **Not load-bearing:** `space_dir()` already does `mkdir(parents=True, exist_ok=True)` on every access, so every consumer self-heals the tree on first use. If this hunk were missing the app would behave identically; it only moves folder creation earlier so the folders exist before the user goes looking for them in Finder. |
| `a8ead66` | 07-28 | Test only (1 file, +85). Locks the legacy `guru_page` preview to `publish_body()` — the C4 fix shipped in `45aab99` with no test, and this adds one non-vacuous test for it. **Zero production-code change.** Adds one test to §8's group 4. |

**One consequence to internalize now:** only the **tip** bundle is correct. `2cc0089` and `5743ef3` ship a stale `dist/index.html` (the React work in `5743ef3` did not reach the bundle until `45aab99`). You are syncing the tree at `a8ead66` in one pass, so this is automatic — but it is why you must never hand-merge or partially copy `src/ui/web/dist/index.html`. None of the four commits after `4ccd321` rebuilt the bundle, so every `333921` assertion in this guide is still exactly right at the new target.

---

## 2. Phase 0 — Inspect the Mac baseline (do this FIRST)

You are not assuming the Mac's state — you are observing it. Nothing is written in this phase.

Open Terminal. Find the real checkout — GitHub Desktop → Repository → Show in Finder is the reliable way, and the last port failed precisely because files went into a scratch folder instead. Then:

```bash
cd <the alma-insights checkout>    # find it; do not guess paths in commands below
pwd
ls -la | head -30
```

**P1 — confirm the baseline is really `d27eb3e`-era.** This port is only valid on top of it.

```bash
git log --oneline -1
git rev-parse HEAD
```

**PASS:** the short SHA is `d27eb3e`, or a docs-only descendant of it. **FAIL:** if `git log --oneline -15` shows any of `1ba6fa5`, `fabb4be`, `2cc0089`, `5743ef3`, `45aab99`, `ed6482b`, `4ccd321`, `1aa0ef2`, `24c8d0f`, `30fb086`, `a8ead66`, part or all of this range is already applied — **STOP and report**; a re-run is not automatically safe.

**P2 — the pre-051 Zendesk tables must already exist.** Migration 051 is mostly `ALTER TABLE … ADD COLUMN` against tables created by migration 030. If they are absent, 051 fails — and it fails *silently* (§7).

```bash
sqlite3 data/local_warehouse.db "SELECT name FROM sqlite_master WHERE name IN ('zendesk_articles','zendesk_macros','zendesk_article_drafts','zendesk_macro_drafts');"
sqlite3 data/local_warehouse.db "SELECT filename FROM schema_migrations ORDER BY filename DESC LIMIT 3;"
```

**PASS:** all four table names print, and the highest applied migration is `050_enablement_documents_fts.sql` with `051`/`053` absent. **FAIL:** any missing table → **STOP**; the Mac is behind the migration-030 era and this port is not the fix. A migration higher than 050 already applied → **STOP**, see P1.

**P3 — is this a git repo at all?** Everything downstream branches on the answer.

```bash
git rev-parse --is-inside-work-tree 2>/dev/null || echo "NOT a git repo"
```

### Getting the zip (you do the download)

**Preferred — pinned to the exact commit**, so a later push to the branch cannot race your download:

```
https://github.com/zeppelin150/alma-insights/archive/a8ead66e09d2f1709ee5ec24a74713a4edc145c3.zip
```

The branch form (`https://github.com/zeppelin150/alma-insights/archive/refs/heads/enablement-content-tabs.zip`) is acceptable — it just risks picking up commits pushed after this guide was retargeted, which the structural-authentication fallback in §4.1 tells you how to handle.

A GitHub source zip is the full tree at one commit: **no `.git` directory, no gitignored files (therefore no `data/`)**, LF line endings preserved exactly as committed, binaries byte-exact, and the archive comment stamped with the full commit SHA.

Put it somewhere outside the checkout and inflate it with **command-line `unzip`, not by double-clicking** — Finder's Archive Utility propagates the browser's quarantine extended attribute onto every extracted file.

```bash
mkdir -p ~/alma_port_incoming
cd ~/alma_port_incoming
unzip -q <path-to-zip>
ls -d alma-insights-*
```

---

## 3. Phase 1 — Pre-flight backups & environment gates

### Backups (cheap insurance, even though the sync can't touch `data/`)

Pick one date string and use it everywhere. This guide uses `2026-07-28`.

```bash
cd <the alma-insights checkout>
mkdir -p ~/alma_port_backup_2026-07-28
```

**If it IS a git repo** — create an immovable pointer at the baseline:

```bash
git branch pre-zendesk-port-2026-07-28
git rev-parse pre-zendesk-port-2026-07-28 > ~/alma_port_backup_2026-07-28/baseline_commit.txt
git status --porcelain > ~/alma_port_backup_2026-07-28/baseline_status.txt
cat ~/alma_port_backup_2026-07-28/baseline_commit.txt
```

Write that hash down. Nothing tracked can now be lost.

**Either way** — the tree, the database and the settings:

```bash
tar czf ~/alma_port_backup_2026-07-28/pre_sync_tree.tgz \
    --exclude='.venv' --exclude='venv' --exclude='data' --exclude='.git' --exclude='__pycache__' \
    -C "$(dirname "$PWD")" "$(basename "$PWD")"

# The database is in WAL mode. NEVER just `cp` the .db — use SQLite's own backup.
sqlite3 data/local_warehouse.db ".backup 'data/local_warehouse.pre051.db'"
ls -l data/local_warehouse.pre051.db

cp data/settings.yaml ~/alma_port_backup_2026-07-28/settings.yaml
cp ~/.alma-insights/ui_state.json ~/alma_port_backup_2026-07-28/ui_state.json 2>/dev/null || true
```

**If it is NOT a git repo:** `pre_sync_tree.tgz` is your only code rollback — do not skip it. Either way, `data/local_warehouse.pre051.db` is your only schema rollback (§16, R3). There are no down-migrations.

### Gate G1 — native arm64 Python (the #1 historical Mac killer)

```bash
<venv-python> -c "import platform, mmap; print(platform.machine(), mmap.PAGESIZE)"
```

**PASS:** `arm64 16384`. **FAIL:** `x86_64`, or page size `4096` → the venv runs under Rosetta. QtWebEngineProcess is then killed at launch on a page-size mismatch (QTBUG-98487) and **every web surface renders blank no matter what you port** — including the new Zendesk tab, which you would then wrongly blame on this port. Fix the venv (native arm64 Python) before anything else.

### Gate G2 — SQLite has FTS5

Migration 051 creates two contentless FTS5 virtual tables. Without FTS5 the migration raises, and `db_manager` swallows it (§7).

```bash
<venv-python> -c "import sqlite3; c=sqlite3.connect(':memory:'); c.execute(\"CREATE VIRTUAL TABLE t USING fts5(a, tokenize='unicode61 remove_diacritics 2')\"); print('FTS5 OK')"
```

**PASS:** `FTS5 OK`. **FAIL:** this Python's bundled SQLite lacks FTS5 → the mirror will have no search and migration 051 will not complete. STOP.

### Gate G3 — full PySide6 quartet present and importable

```bash
<venv-python> -c "import PySide6, PySide6.QtWidgets, PySide6.QtWebEngineWidgets, PySide6.QtWebEngineCore, PySide6.QtWebChannel; print('PySide6', PySide6.__version__, 'OK')"
```

**PASS:** a version prints with `OK`. **FAIL:** if this gate fails, the recovery is `pip install --force-reinstall --no-deps PySide6 PySide6-Essentials PySide6-Addons shiboken6`, then delete any leftover `_CodeSignature` directories under the PySide6 tree, and if quarantined: `xattr -rd com.apple.quarantine <site-packages>/PySide6`. **This is repair of a pre-existing condition, not part of the port** — no dependency in this range changed (rule: §5).

### Gate G4 — WebEngine health BEFORE adding a new WebEngine surface

This must already be clean. If it is not, the Zendesk tab will be blamed for a fault that predates it.

```bash
<venv-python> scripts/web_diag.py
echo "web_diag exit=$?"
```

**PASS:** exit 0. **FAIL (exit 1):** read which probe failed. `page_size` / `rosetta` → see G1. `helper_arch` → the bundled `QtWebEngineProcess` Mach-O slices do not match `platform.machine()`; this is the QTBUG-98487 kill fingerprint. Framework-integrity FAIL → a re-sign or copy left `QtWebEngineCore.framework` inconsistent; reinstall per G3. `quarantine` WARN → `xattr -rd com.apple.quarantine <site-packages>/PySide6`. **STOP on any FAIL** — porting a fourth WebEngine surface onto a broken WebEngine proves nothing.

```bash
bash scripts/verify_web_pivot_mac.sh    # informational; its round-trip step will skip — see §9
```

### Record versions for the report

```bash
<venv-python> --version
<venv-python> -c "import sqlite3; print('sqlite', sqlite3.sqlite_version)"
sw_vers
uname -m
```

---

## 4. Phase 2 — Inflate, compare, sync

### 4.1 Authenticate the zip

```bash
unzip -z <path-to-zip>
# Expect the archive comment to be:  a8ead66e09d2f1709ee5ec24a74713a4edc145c3
```

**This guide ships inside the target commit** — it landed in `24c8d0f`, which is an ancestor of `a8ead66`. So a zip taken at `a8ead66` **will** contain `docs/ZENDESK_BLOCK_PORT_GUIDE.md` *and* the SHA above **will** match. Finding this file in the zip is no longer a reason for the SHA to differ.

**If it prints a different SHA, STOP — with one sanctioned exception.** A differing SHA now means only one thing: you downloaded the **branch tip** (or a pinned commit) that is a **descendant** of `a8ead66`, i.e. work pushed after this guide was retargeted. That is not automatically wrong, but it is unverified. Authenticate **structurally** instead: the pre-sync compare (4.3) must show nothing beyond Appendix A. Anything extra must be **under `docs/`** and docs-only. **Any extra path outside `docs/` — any `.py`, any `.sql`, any file under `src/` or `web/` or `tests/` — is code this guide did not review → STOP and report it before syncing.**

```bash
SRC=$(ls -d ~/alma_port_incoming/alma-insights-*)
DEST=<the alma-insights checkout>
echo "SRC=$SRC"; echo "DEST=$DEST"
xattr -rd com.apple.quarantine "$SRC" 2>/dev/null || true
```

Inflation sanity count — a truncated inflate is the classic silent failure:

```bash
find "$SRC/assets/help" -name '*.md' | wc -l          # expect 63
ls "$SRC/migrations"/05*.sql                          # expect 050, 051, 053 — and NO 052
wc -c "$SRC/src/ui/web/dist/index.html"               # expect 333921
wc -c "$SRC/src/ui/web/dist/qwebchannel.js"           # expect 15152
```

**`migrations/052_solver_ledger.sql` is correctly absent.** It belongs to a separate local-only workstream on the dev box, it is untracked, and it is not part of this port. Do not create it. Do not "fix" the gap. §7 explains why the gap is harmless.

### 4.2 Verify `src/data/app_paths.py` is in the zip — do NOT create it

**This step used to be a hand-create. It is now a one-line check.** At `4ccd321` this module was imported by committed code but had never been `git add`ed, so it was missing from the archive. Commit `1aa0ef2` fixed exactly that. **At `a8ead66` the file is tracked and ships in the zip. You create nothing.**

```bash
ls -l "$SRC/src/data/app_paths.py"      # expect ~124 lines / ~4 KB, present
```

**PASS:** the file exists in `$SRC`. Nothing to do — it rides in with the one-pass `rsync` in 4.4 like every other added file. Go to 4.3.

**FAIL (file absent):** you are **not** holding a zip at `a8ead66` or later. Do not hand-write the module and do not proceed — go back to §4.1, re-check the archive comment, and download the pinned archive. A tree missing this file will launch, pass most of the smokes, and then fail silently on the exact feature this port exists to deliver.

**Why this one file gets its own gate.** `src/ui/pages/enablement/page.py` imports `src.data.app_paths` **lazily, inside three methods** — so a missing module cannot break launch or import-time smokes. The damage is at click time, and it is silent:

| Where | User-visible failure if the module is missing |
|---|---|
| `page.py` `_web_zendesk_file_pick` | Zendesk web tab → **Import files…** is a **silent no-op**. No error, no log line. |
| `page.py` `_web_zendesk_folder_pick` | Zendesk web tab → **Import folder…** is a **silent no-op**. |
| `page.py` `_on_pptx_export` | **PowerPoint deck export raises** — a regression of a feature already shipped on the Mac. |

The silence is structural: `zendesk_web.py::_pick_files` catches `Exception` and returns `[]`, so `ModuleNotFoundError` becomes "the button did nothing". That is how the gap was found in the first place, and it is why the check above is worth ten seconds.

Commit `30fb086` additionally calls `app_paths.ensure_docs_tree()` from `main.py` at boot, inside a `try/except` that prints and continues. That hunk is **not** load-bearing — `space_dir()` does `mkdir(parents=True, exist_ok=True)` on every access, so every consumer creates what it needs on first use. It only makes the `data/documents/` folders appear before the user goes looking for them.

**Verify after the sync** (this replaces the old hand-create verification):

```bash
grep -c "def start_dir" src/data/app_paths.py
# expect 1
<venv-python> -c "from src.data.app_paths import start_dir; print(start_dir('exports','D.pptx'))"
# expect a path ending  data/documents/Exports/D.pptx
```

**Its siblings must NOT appear, and must NOT be created.** `src/data/docs_backfill.py`, `src/data/solver_ledger.py` and `migrations/052_solver_ledger.sql` belong to the same local-only workstream on the dev box. They are **still untracked**, they are **correctly absent from the zip**, **nothing committed references them**, and creating them by hand would put unreviewed code and an unshipped migration onto production. `app_paths.py` was the only member of that group anything in this range imports, and it is the only one that got committed. If any of the three shows up in the pre-sync compare, the zip came from a dirty snapshot → **STOP** (§4.5).

### 4.3 Pre-sync compare (the audit moment — never skip)

```bash
diff -rq "$SRC" "$DEST" \
  --exclude data --exclude .git --exclude .venv --exclude venv --exclude __pycache__ \
  --exclude .pytest_cache --exclude .claude --exclude node_modules --exclude .DS_Store \
  | sort > ~/alma_port_backup_2026-07-28/pre_sync_compare.txt
wc -l ~/alma_port_backup_2026-07-28/pre_sync_compare.txt
grep -c "^Only in $SRC" ~/alma_port_backup_2026-07-28/pre_sync_compare.txt
grep -c "differ$" ~/alma_port_backup_2026-07-28/pre_sync_compare.txt
```

**How to read it:**

- **"Files … differ"** and **"Only in `$SRC`"** together are the delta about to be applied. They should account for the **85 paths in Appendix A**, plus any docs-only descendants explained in §4.1. The rule, not a guess: **every `A` row in Appendix A is a "Only in `$SRC`" line (42 of them, including `src/data/app_paths.py`), and every `M` row is a "differ" line (43 of them).** **More than that = baseline drift** — the sync heals it, but report every extra path.
- **"Only in `$DEST`"** = Mac-local files the sync will leave untouched. Expected: `data/` never appears (excluded), `.venv`, editor files. **Exception to inspect: any `$DEST`-only `.py` under `src/` or `web/src/`** is a stray hand-copy that can **shadow imports** after the sync — report these; do not delete on your own. `src/data/app_paths.py` must **not** appear here: it now arrives from `$SRC`, and a `$DEST`-only copy would mean somebody hand-created it from an earlier revision of this guide. If you see it there, say so — the `rsync` will overwrite it with the shipped version, which is the correct outcome, but the owner needs to know it happened.

### 4.4 Sync — one pass

```bash
rsync -a "$SRC"/ "$DEST"/        # NO --delete. EVER. (rule 3)  Trailing slashes are load-bearing.
```

**Do not cherry-pick, do not hand-apply hunks, do not copy files one at a time.** The per-file detail in §12 and in Appendix A exists for verification and debugging, not as a to-do list of manual copies. In particular, `src/data/zendesk_client.py` and `src/data/zendesk_store.py` must land together (§12.B) and a one-pass `rsync` guarantees that.

### 4.5 Post-sync compare (the byte-identity proof — this replaces `git rev-parse` in a zip flow)

```bash
diff -rq "$SRC" "$DEST" \
  --exclude data --exclude .git --exclude .venv --exclude venv --exclude __pycache__ \
  --exclude .pytest_cache --exclude .claude --exclude node_modules --exclude .DS_Store \
  | sort > ~/alma_port_backup_2026-07-28/post_sync_compare.txt
grep -c "differ$" ~/alma_port_backup_2026-07-28/post_sync_compare.txt      # expect 0
grep -c "^Only in $SRC" ~/alma_port_backup_2026-07-28/post_sync_compare.txt  # expect 0
```

**PASS = zero "differ" lines and zero "Only in `$SRC`" lines.** ("Only in `$DEST`" strays remain by design.) **FAIL:** the sync did not complete — re-run 4.4 and re-check. Do not proceed on a partial tree; §12.B explains why a partial sync of this particular range is the one dangerous outcome.

**If it IS a git repo**, capture the audit trail of exactly what the sync changed:

```bash
# -uall is load-bearing: without it git COLLAPSES a wholly-new directory
# (web/src/zendesk/) into a single ?? line and the count will not add up.
git status --porcelain -uall > ~/alma_port_backup_2026-07-28/applied_delta_git_view.txt
wc -l ~/alma_port_backup_2026-07-28/applied_delta_git_view.txt      # expect 85
grep -c '^??' ~/alma_port_backup_2026-07-28/applied_delta_git_view.txt   # expect 42
grep -c '^ M' ~/alma_port_backup_2026-07-28/applied_delta_git_view.txt   # expect 43
grep '^??' ~/alma_port_backup_2026-07-28/applied_delta_git_view.txt
```

**Expect exactly 85 lines** — the 85 files in `d27eb3e..a8ead66` — split **42 `??`** (the added files, which are untracked because the Mac's HEAD is still the baseline) and **43 ` M`**. `src/data/app_paths.py` is now one of the 42 `??`, not a special case.

**The count is a rule, not a magic number:** if the Mac's baseline is a *docs-only descendant* of `d27eb3e` (allowed by P1), one or two docs paths may already match and drop out, so a count of 83–85 with the shortfall entirely under `docs/` is fine. **A shortfall anywhere else means a partial sync → re-run 4.4.**

**Any `??` line naming `migrations/052_solver_ledger.sql`, `src/data/docs_backfill.py`, `src/data/solver_ledger.py`, `docs/pilot/` or `wheel_tail.bin`** means the zip was inflated from a dirty snapshot or copied too broadly — **STOP, do not commit.**

### Gate G5 — integrity spot-checks (binary fidelity + junk awareness)

```bash
<venv-python> -c "import os; s=os.path.getsize('src/ui/web/dist/index.html'); print(s); assert s==333921, 'dist bundle size mismatch'"
ls -la src/ui/web/dist/qwebchannel.js                       # must exist; 15152 bytes, unchanged in this range
grep -c __almaZendeskMounted src/ui/web/dist/index.html     # expect >= 1 — the Zendesk route is in the bundle
grep -c __almaHomeMounted src/ui/web/dist/index.html        # expect 1 — the other routes survived the rebuild
ls -la migrations/051_zendesk_mirror.sql migrations/053_zendesk_versions.sql
ls migrations/052_solver_ledger.sql 2>/dev/null && echo "UNEXPECTED — 052 must NOT be here"
```

**FAIL on the size assert** = a truncated inflate or a partial copy; re-inflate the zip and re-run 4.4. **FAIL on `__almaZendeskMounted`** = an old bundle survived; the Zendesk tab will render blank later and you will chase WebEngine ghosts for nothing.

---

## 5. Phase 3 — Python and JavaScript dependencies

**There are none. This phase is a check, not an action.**

```bash
diff "$SRC/requirements.txt"  requirements.txt   && echo "requirements.txt identical"
diff "$SRC/PACKAGES.md"       PACKAGES.md        && echo "PACKAGES.md identical"
diff "$SRC/web/package.json"  web/package.json   && echo "package.json identical"
```

All three must print `identical`. `requirements.txt`, `PACKAGES.md`, `web/package.json`, `web/package-lock.json`, everything under `installer/` and everything under `scan_server/` are untouched across the whole of `d27eb3e..a8ead66` (re-verified at the retarget).

**Therefore: no `pip install`. No `npm install`. And never `npm run build` (rule 4).** Every new module in this range is stdlib-only or uses packages the Mac already has. If an import smoke in §6 fails with `ModuleNotFoundError` for a third-party package, that is a **pre-existing** venv problem on the Mac, not something this port introduced — report it rather than installing your way around it.

`installer/build_release.py` already ships `migrations/` inside `APP_CONTENTS`, so an installed bundle picks up the two new `.sql` files with no installer change.

---

## 6. Phase 4 — Import smokes (catch a partial port before Qt ever starts)

Fast, headless, no database writes.

```bash
cd <the alma-insights checkout>
<venv-python> - <<'EOF'
import importlib
checks = [
    # (module, why)
    ("src.data.app_paths",                     "NEW in 1aa0ef2 (§4.2) — Import buttons and PPTX export need it"),
    ("src.data.zendesk_import",                "NEW — file import + GET-only pull"),
    ("src.data.zendesk_versions",              "NEW — the 053 post-hook imports this at migrate time"),
    ("src.data.chat_tools.zendesk_mirror_tools", "NEW — Renn propose tools"),
    ("src.services.zendesk_web",               "NEW — the controller holding all authority"),
    ("src.ui.web.zendesk_bridge",              "NEW — the pure relay"),
    ("src.data.zendesk_store",                 "MODIFIED — mirror API, paired with zendesk_client"),
    ("src.data.zendesk_client",                "MODIFIED — write methods deleted"),
    ("src.data.html_sanitize",                 "MODIFIED — preview profile + CSS allowlist"),
    ("src.data.enablement_store",              "MODIFIED — publish_body()"),
    ("src.services.agent_chat",                "MODIFIED — publish gate + fingerprints"),
    ("src.ui.web.chat_bridge",                 "MODIFIED — PublishConfirmHost"),
    ("src.ui.web.web_flags",                   "MODIFIED — the 'zendesk' mode"),
    ("src.updater.schema_migrator",            "MODIFIED — 051 + 053 post-hooks"),
]
for mod, why in checks:
    importlib.import_module(mod); print(f"ok  {mod}")

from src.ui.web.web_flags import VALID_MODES
assert VALID_MODES == ("off", "calendar", "all", "zendesk"), VALID_MODES
print("ok  VALID_MODES", VALID_MODES)

from src.updater.schema_migrator import SchemaMigrator
h = SchemaMigrator._POST_HOOKS
assert h.get("051_zendesk_mirror.sql") == "_posthook_051_zendesk_projections", h
assert h.get("053_zendesk_versions.sql") == "_posthook_053_seed_draft_baselines", h
print("ok  post-hooks registered for 051 and 053")

from src.data import zendesk_client as zc
for gone in ("_write", "create_article", "update_article", "create_macro", "update_macro"):
    assert not hasattr(zc.ZendeskClient, gone), f"WRITE METHOD STILL PRESENT: {gone}"
assert hasattr(zc.ZendeskClient, "_build_request"), "choke point missing"
assert hasattr(zc, "ZendeskWriteBlocked"), "ZendeskWriteBlocked missing"
for lane in ("get_articles", "get_sections", "get_categories", "list_macros"):
    assert hasattr(zc.ZendeskClient, lane), f"READ LANE MISSING: {lane}"
print("ok  Zendesk client is structurally read-only, GET lanes intact")

from src.data.enablement_store import publish_body
from src.ui.web.chat_bridge import PublishConfirmHost
print("ok  publish_body + PublishConfirmHost present")

print("ALL IMPORT SMOKES PASSED")
EOF
```

**If `src.data.app_paths` raises `ModuleNotFoundError`:** the zip predates `1aa0ef2` or the sync was partial. **Do not hand-write the module** — go back to §4.1/§4.2 and get a zip at `a8ead66` or later, then re-run 4.4. The app will still launch without it, which is exactly what makes this failure dangerous.

**If `WRITE METHOD STILL PRESENT` fires:** a stale `src/data/zendesk_client.py` survived the sync. This is the dangerous direction of a partial port (§12.B). Re-run 4.4 and 4.5.

**If `ZendeskWriteBlocked missing` or a `READ LANE MISSING` fires:** the tree is not at `a8ead66` — re-run Phase 2 steps 4.4 and 4.5.

---

## 7. Phase 5 — First launch & migrations 051 → 053

### The numbering gap 051 → 053 is safe

Production will apply `051_zendesk_mirror.sql` then `053_zendesk_versions.sql` with **no 052**. That is correct and expected.

`src/updater/schema_migrator.py::pending()` is:

```python
files = sorted(self._dir.glob("*.sql"))
return [f for f in files if f.name not in applied]
```

Discovery is **sorted filename minus the set already recorded in `schema_migrations`**. There is no contiguity check, no "expected next number", nowhere that compares N to N−1. A missing `052_*.sql` is simply a file that never appears in the glob. `current_version()` is `max()` over the parsed leading digits, not a count, so the version reads **53** and nothing is confused by the jump. Migration 053's SQL references nothing 052 creates — its own comment cites 052 only as a *precedent* for a soft-reference convention.

**Do not hand-create a placeholder `052`.** That would record a filename in `schema_migrations` that does not correspond to anything, and it would diverge production from every other machine.

### ⚠ Migration failure is SILENT at launch

`src/data/db_manager.py` wraps the migrator in `try/except Exception` and degrades to a `logging.warning("Schema migration skipped: %s", exc)`. **The app boots normally on a half-migrated schema. A clean launch is NOT evidence that migrations applied.** Verification below is mandatory, not optional.

### Apply the migrations

Rule 5 stands: **never** `sqlite3 data/local_warehouse.db < migrations/051_zendesk_mirror.sql`. Both migrations carry Python post-hooks a raw apply would skip:

| Migration | Post-hook | What it does, and what breaks without it |
|---|---|---|
| `051_zendesk_mirror.sql` | `_posthook_051_zendesk_projections` → `zendesk_store.backfill_mirror_projections(conn)` | Recomputes the Python-side `body_text` / `actions_text` plain-text projections and `content_hash` for rows predating 051, then the FTS mirrors rebuild. Without it, pre-051 rows index **empty body text** — invisible to search, and **unrepairable on a box with no API key to re-pull from**. |
| `053_zendesk_versions.sql` | `_posthook_053_seed_draft_baselines` → `zendesk_versions.seed_draft_baselines(conn)` | Seeds a `seq=1` `'create'` version from each existing article draft so pre-053 drafts have a rollback baseline. Guarded — only drafts with zero version rows are seeded, so it is re-run safe. |

Apply them the sanctioned way, with the app closed:

```bash
cd <the alma-insights checkout>
<venv-python> -c "
from src.data.db_manager import DatabaseManager
db = DatabaseManager(); db.initialize()
rows = [r[0] for r in db.conn.execute('SELECT filename FROM schema_migrations ORDER BY filename')]
print(rows[-4:])
"
```

**Expect the tail:** `['049_kb_extra_fields.sql', '050_enablement_documents_fts.sql', '051_zendesk_mirror.sql', '053_zendesk_versions.sql']`.

### Prove it landed

```bash
sqlite3 data/local_warehouse.db "
SELECT name FROM sqlite_master WHERE name IN
 ('zendesk_sections','zendesk_categories','zendesk_articles_fts','zendesk_macros_fts',
  'zendesk_article_versions','zendesk_draft_versions');
SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'zendesk_%';"
```

**Expect:** all **6 tables** and all **6 triggers** (`zendesk_articles_ai` / `_ad` / `_au`, `zendesk_macros_ai` / `_ad` / `_au`).

```bash
sqlite3 data/local_warehouse.db "PRAGMA table_info(zendesk_articles);" | grep -cE "body_html|body_text|content_hash|origin"
# expect 4
```

**On the next app launch the splash's Database integrity line must read `Schema v53`.** If it still says `v50`, the migrator raised and `db_manager` swallowed it — read the `alma.db` log for `Schema migration skipped:` and go to §16, R2.

### Import + FTS smoke (needs no credentials)

This proves the triggers and the projection post-hook actually work, on a throwaway database:

```bash
<venv-python> - <<'PY'
import tempfile, pathlib
tmp = pathlib.Path(tempfile.mkdtemp())
(tmp/"Refund_policy.html").write_text(
    "<h1>Refund policy</h1><p>Refunds are issued within 10 business days.</p>",
    encoding="utf-8")
from src.data.db_manager import DatabaseManager
from src.data import zendesk_import, zendesk_store
db = DatabaseManager(db_path=tmp/"smoke.db"); db.initialize()
print(zendesk_import.import_file(db.conn, str(tmp/"Refund_policy.html")))
print("mirror rows:", len(zendesk_store.list_articles(db.conn)))
print("fts:", zendesk_store.search_mirror(db.conn, "refunds")["articles"])
PY
```

**Expect** `'imported': 1`, `mirror rows: 1`, and a non-empty `fts` list whose snippet contains `Refunds are issued within 10 business days`. **An `imported: 1` with an EMPTY `fts` list** means the 051 triggers or the projection post-hook did not land → §16, R2.

---

## 8. Phase 6 — Test gates

Before every group:

```bash
pkill -9 -f alma_mcp_server; pkill -9 -f chat_mcp_server; pkill -9 -x gemini; pkill -9 -f 'scan_server/server.js'
# do NOT `pkill -9 node` blindly — match the server.js path; a blind node-kill takes out unrelated tooling.
# CLAUDE.md's `wmic …` zombie command is Windows-only and does not exist here.
export QT_QPA_PLATFORM=offscreen
```

Four groups. **Each in its OWN pytest invocation** — grouping WebEngine-touching files stacks Chromium teardown to exit 255. Never `pytest tests/` (rule 6). Timings are from the dev box; the M1 will be in the same order.

```bash
QT_QPA_PLATFORM=offscreen <venv-python> -m pytest \
  tests/test_zendesk_mirror_schema.py tests/test_zendesk_versions.py \
  tests/test_zendesk_import.py -q
# expect: 145 passed   (~2 min)  — schema/FTS discipline, versions layer, importer

QT_QPA_PLATFORM=offscreen <venv-python> -m pytest \
  tests/test_zendesk_bridge.py tests/test_zendesk_web_tab.py \
  tests/test_zendesk_readonly_guard.py -q
# expect: 252 passed   (~2.5 min) — bridge contracts, web tab, THE READ-ONLY GUARD

QT_QPA_PLATFORM=offscreen <venv-python> -m pytest \
  tests/test_zendesk_client_readonly.py tests/test_zendesk_content.py \
  tests/test_zendesk_mirror_tools.py -q
# expect: 102 passed   (~40 s)   — client choke point, classic-tab contracts, Renn tools

QT_QPA_PLATFORM=offscreen <venv-python> -m pytest \
  tests/test_publish_body_parity.py tests/test_chat_review_panel.py \
  tests/test_enablement_web_flag.py tests/test_web_guardrails.py -q
# expect: 166 passed   (~50 s)   — publish-body parity, approval gate, flag, no-innerHTML CI rule
```

**Total: 665 passing.** Report the per-group counts in your final summary.

Group 4 is **166, not the 165** an earlier revision of this guide quoted: commit `a8ead66` adds exactly one test to `tests/test_publish_body_parity.py` (that file alone now reports **28 passed**). Groups 1–3 are unchanged by the retarget — none of the four commits after `4ccd321` touched their files.

Optional fifth group — the help-text corpus. It is **known-red for reasons unrelated to this port**; include it only if you want the coverage, and expect exactly these three failures:

```bash
QT_QPA_PLATFORM=offscreen <venv-python> -m pytest \
  tests/test_help_claims_create.py tests/test_help_claims_troubleshooting.py \
  tests/test_help_claims_reference.py tests/test_workbench_bridge.py -q
# dev box: 3 failed, 272 passed, 1 skipped
```

### How to read the results — non-passes that are NOT port bugs

- **`test_this_build_is_version_1_0_0`** and **`test_sidebar_and_settings_show_the_same_v_prefixed_version`** — `assert '1.0.7' == '1.0.0'`. The help corpus still claims 1.0.0. Stale test text, predates this range.
- **`test_export_asks_where_to_put_the_file_and_cancelling_writes_nothing`** (in `tests/test_help_claims_create.py`) — `assert '…/data/documents/Exports/D.pptx' == 'D.pptx'`. This test fails **because `src/data/app_paths.py` is present**, and as of `1aa0ef2` it is *always* present — it ships in the zip. The test text is stale, not the code. It is the expected cost of the silent-Import-buttons fix. Known-red; do not "fix" it by deleting the module.
- **`tests/test_zendesk_readonly_guard.py` failing is never acceptable** (rule 7). It means a Zendesk write survived on the Mac. Re-run Phase 2 and the §6 smokes.
- Machine-state skips (no credentials, no `claude` CLI) are expected — this port needs neither.
- **Anything else red → a real problem.** Report it with the full pytest output; do not patch tests.
- If a group errors with `web_tabs` / native-widget assertion noise, check `data/settings.yaml`: a box running `enablement.web_tabs: all` breaks some native-widget tests. Production should be `off` during the port and `zendesk` after (§13.1) — **but do not change it now** (rule 1).

---

## 9. Phase 7 — Web-stack acceptance (before the flag is ever flipped)

**`scripts/verify_web_pivot_mac.sh` proves nothing about the Zendesk route.** `.gitignore` excludes `tests/test_*_local.py`, so `tests/test_zendesk_web_local.py` is **not in the zip**; the script prints `not present on this checkout (gitignored *_local.py) — skipped` and moves on. Use the probe below instead.

The probe needs no bridge, no database and no credentials: the SPA carries a built-in `#/zendesk?demo` fixture that engages only when no bridge is attached. Create the script, then run it **singly**.

```bash
cat > /tmp/zd_mount_probe.py <<'PY'
import os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS",
    "--no-sandbox --disable-gpu --disable-software-rasterizer --in-process-gpu")
sys.path.insert(0, os.getcwd())
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401 (BEFORE QApplication)
from PySide6.QtWidgets import QApplication
from src.ui.web.web_host import WebHost, _DIST
print("bundle:", _DIST, os.path.getsize(_DIST), "bytes")
app = QApplication.instance() or QApplication([])
host = WebHost(route="/zendesk?demo"); host.resize(1200, 800); host.show(); host.load()
def ev(expr, t=20.0):
    end = time.monotonic() + t; st = {"d": False, "v": None}
    host.view.page().runJavaScript(expr, lambda r: st.update(d=True, v=r))
    while not st["d"] and time.monotonic() < end:
        app.processEvents(); time.sleep(0.01)
    return st["v"]
deadline, mounted = time.monotonic() + 40, False
while time.monotonic() < deadline:
    if ev("!!window.__almaZendeskMounted", 5.0) is True: mounted = True; break
    app.processEvents(); time.sleep(0.1)
arts = ev("window.__almaZdArticles"); body = ev("document.body.innerText.length")
print(f"mounted={mounted} demo_articles={arts} innerText_len={body}")
sys.exit(0 if (mounted and (body or 0) > 200) else 1)
PY

QT_QPA_PLATFORM=offscreen <venv-python> /tmp/zd_mount_probe.py; echo "exit=$?"
```

**PASS** (measured on the dev box):

```
bundle: …/src/ui/web/dist/index.html 333921 bytes
mounted=True demo_articles=10.0 innerText_len=1170.0
exit=0
```

**Reading it:**

- `mounted=True` with a non-trivial `innerText_len` → the route renders. That is the whole gate.
- `mounted=True` with `innerText_len` near 0 → **the classic macOS blank render.** Go to §12.G and `scripts/web_diag.py --json`. Do **not** touch the database.
- `mounted=False` → the bundle never executed. Re-check Gate G5's size assert, then `web_diag`.
- Benign on macOS and to be ignored: `ERROR:gpu_channel_manager.cc … Failed to create GLES3 context`. Only `mounted` and `innerText_len` matter.
- **Assert via `runJavaScript`, never screenshots** — offscreen grabs are always blank on this stack and will mislead you.

---

## 10. Phase 8 — In-app smoke checklist (launch from a terminal)

Launch with the diagnostic dump armed, so the WebEngine fact sheet is already in the log if anything goes wrong:

```bash
cd <the alma-insights checkout>
ALMA_WEB_DIAG=1 <venv-python> main.py
```

Observe, in order:

1. Splash appears; **Database integrity reads `Schema v53`**. `v50` here = the migrator raised and was swallowed → §7, then §16 R2.
2. App reaches the Home page with no error dialog. Existing surfaces (Calendar, Workbench, Chat) look and behave exactly as before — **this port changes none of them visually.**
3. Enablement → **Zendesk tab opens as the classic native Qt tab** (`enablement.web_tabs` is still `off`, and that is correct at this point). Its former "Push to Zendesk" button is now **"Copy for Zendesk"** plus **"Mark as copied"**, with a read-only notice label. That change is the point of `2cc0089` — it is not a bug.
4. Help tab opens → **62 articles** in the ToC. The Zendesk and Zendesk-macros articles no longer claim the app can push to Zendesk. A count below 62 means the assets tree did not land.
5. Guru / Workbench → open any draft's **Review changes** and confirm the preview renders. This exercises the `publish_body()` cluster from `45aab99`. Do **not** publish yet — see §13.2 first.
6. **Do not flip any flag yet.** Finish acceptance (§17) with everything dormant.

**Explicitly owner-gated — do NOT do as part of the port:**

- Setting `enablement.web_tabs` to `zendesk` or `all`. That is a deliberate, separate act after acceptance (§13.1), and `zendesk` is the correct value, not `all`.
- Turning on `ui.web_home`. It is a **different flag**, not part of this port, and it starts Chromium at launch.
- Entering Zendesk credentials or running a live pull (§13.3).
- Publishing anything to Guru before checking `publish_collection_id` (§13.2).
- Committing anything on the Mac. Phase 8b is metadata only.

### Once the flag IS flipped (owner's call, after acceptance)

Restart the app — the flag is read at page construction. Then:

1. Enablement → Zendesk renders the **web** workspace: one compact 52px header row, an article list, and a Mirror menu. A blank white pane here → §9's probe and `web_diag`, not the database.
2. Mirror menu → **Import file** → pick any `.html` or `.json`. **A dialog must appear.** If the button does nothing at all — no dialog, no error, no log line — that is §4.2: `src/data/app_paths.py` is missing, meaning a pre-`1aa0ef2` zip or a partial sync. It is the only tell.
3. Import a file → it appears in the article list; search finds a phrase from its body.
4. Open an article → the **rendered** body is primary, raw HTML is a collapsed disclosure.
5. Copy any content → **a native dialog appears showing the exact bytes** before anything reaches the clipboard. No dialog, no clipboard write. That is the invariant from `5743ef3`/`45aab99`, working.
6. There is **no Versions or Rollback UI**. Correct — see §11.

---

## 10b. Phase 8b — Reconcile the Mac's git metadata with GitHub (LAST step)

Only after §17 acceptance is fully green. This step delivers **no code** — the tracked content already matches — so rule 9's ban on git mechanics does not apply to it.

```bash
cd <the alma-insights checkout>
git status --porcelain > ~/alma_port_backup_2026-07-28/pre_reconcile_status.txt
git fetch origin enablement-content-tabs
git checkout enablement-content-tabs
git status --porcelain
git log --oneline -3
```

**Expect:** `git status` shows only untracked strays — `?? src/data/app_paths.py` and anything gitignored; `data/` never appears. `git log` tip matches the zip's commit.

**If `git checkout` reports it would overwrite local files, STOP.** The tree is NOT identical to the snapshot and the post-sync compare in 4.5 was wrong. Do not force anything.

Leave the `pre-zendesk-port-2026-07-28` branch in place — it is the audit trail. **Never force-push anything from the Mac.**

---

## 11. Phase 9 — The do-not-touch list

Things you will see and should leave exactly as they are.

| What you'll see | Why it stays |
|---|---|
| No Versions / History / Rollback UI anywhere, despite migration 053 running and `zendesk_versions.py` being present | **Phase 1 is deliberately dormant.** `VersionsPanel.jsx`, `HistoryPanel.jsx`, `versionShape.js` and `versions.css` are imported by nothing except their own test file, and the tip bundle contains no "Versions"/"Rollback" strings. The Python side is *not* dormant — the 053 post-hook imports `zendesk_versions`, so the module is load-bearing for the migration to run at all. Answer to "where is the version history": **not shipped yet**, not broken on macOS. |
| `migrations/052_solver_ledger.sql` missing; `schema_migrations` jumps 051 → 053 | Correct and safe (§7). Do not create a placeholder. |
| The classic Zendesk tab's attributes are still called `_a_push` / `_m_push`, and its signals `article_push` / `macro_push` | Deliberately kept so host wiring and existing assertions keep resolving. They now mean "mark handled locally". Renaming them breaks `tests/test_zendesk_content.py`. |
| `publish_article_draft` / `publish_macro_draft` still take a `zendesk_client=` kwarg and ignore it | Source compatibility, on purpose. They are local bookkeeping now and report `remote_write: False`. |
| Grep hits for `create_confirm_write`, `_emit_confirm_write`, `_enqueue_write`, `execute_write`, `cancel_write`, `update_article_draft`, `update_macro_draft` | **Unrelated names.** They are not Zendesk writes. Do not "clean them up". |
| `tests/test_export_asks_where_to_put_the_file_and_cancelling_writes_nothing` failing | Expected — `app_paths.py` ships as of `1aa0ef2`, and this test's expected string predates it (§8). |
| `data/documents/` (Downloads, Exports, Zendesk Imports, Zendesk Edits, Worksheets, `README.txt`) appearing after first launch | `main.py` creates it at boot (`30fb086`). It lives under `data/`, so rule 1 applies: leave it alone. It would have been created lazily on first use anyway. |
| Preview iframes that render a blank article while the rest of the tab works | The sandboxed no-scripts iframe, not a WebEngine failure. Untrusted article HTML reaches the page only through `sanitize_html` into `sandbox=""`. |
| `Failed to create GLES3 context` in the console | Benign offscreen/GPU noise on macOS. |
| Windows console-flash `subprocess` sites in `gemini_client.py` / `gemini_setup.py` | Cosmetic, Windows-only, unrelated to this range. |

---

## 12. Subsystem reference

Go here when a specific gate or smoke fails; stay in the phases for execution order.

### 12.A — Migrations & data layer

| File | A/M | Creates / changes | Consumed by |
|---|---|---|---|
| `migrations/051_zendesk_mirror.sql` | A (137 lines) | 21 `ADD COLUMN`s across `zendesk_articles` (`body_html`, `body_text`, `draft`, `outdated`, `labels_json`, `author_name`, `position`, `created_at_remote`, `content_hash`, `origin`, `source_file`, `raw_json`), `zendesk_macros` (`actions_text`, `content_hash`, `origin`, `source_file`, `raw_json`), and both draft tables (`body_html`/`reply_html`, `rationale`, `sources_json`, `copied_at`); new `zendesk_categories` + `zendesk_sections`; two contentless FTS5 mirrors; six delete-discipline triggers | `zendesk_store`, `zendesk_import`, `zendesk_web` |
| `migrations/053_zendesk_versions.sql` | A (59 lines) | `zendesk_article_versions` + `zendesk_draft_versions` (`UNIQUE(draft_id, seq)`), both `AUTOINCREMENT` so pruning at 50/row cannot recycle rowids | `zendesk_versions` (Python only — no UI) |
| `src/data/zendesk_import.py` | A (429) | File import (API-shaped JSON / HTML / doc_reader), per-file reports, hostile-input hardened (50 MB cap, 255-char titles); `pull_mirror` GET-only paged pull | the web controller and the classic tab |
| `src/data/zendesk_versions.py` | A (284) | Version rows + `seed_draft_baselines` | **the 053 post-hook — must be present before the migration runs** |
| `src/data/zendesk_store.py` | M (+967/−102) | The whole mirror API. **Never `INSERT OR REPLACE` into mirror tables** — grep-guarded by `tests/test_zendesk_mirror_schema.py` | everything |
| `src/updater/schema_migrator.py` | M (+25) | Registers both post-hooks in `_POST_HOOKS` | §7 |

Both migrations are **re-runnable**, which is what makes §16 R2 a real repair lever: every `ADD COLUMN` in 051 is single-line so the migrator's `PRAGMA table_info` guard catches all 21; every `CREATE TABLE`/`INDEX`/`VIRTUAL TABLE` is `IF NOT EXISTS`; the six triggers are `DROP … IF EXISTS` + `CREATE`; the FTS backfill is a `'delete-all'` followed by a full re-index. 053 is two `CREATE TABLE IF NOT EXISTS` and one index.

### 12.B — The read-only policy, and the one pair that must land together

Deleted outright from `src/data/zendesk_client.py` in `2cc0089` — **no stubs left behind**:

| Deleted | Was |
|---|---|
| `_write(self, method, path, body)` | the POST/PUT transport helper |
| `create_article(section_id, title, body, *, locale="en-us")` | POST `/help_center/{locale}/sections/{id}/articles.json` |
| `update_article(article_id, *, title=None, body=None, locale="en-us")` | PUT `/help_center/articles/{id}/translations/{locale}.json` |
| `create_macro(name, actions, *, description=None)` | POST `/macros.json` |
| `update_macro(macro_id, *, name=None, actions=None)` | PUT `/macros/{id}.json` |

Replaced by one choke point `_build_request(url_or_path, *, method="GET")` raising `ZendeskWriteBlocked` on any non-GET, then re-checking the built request (`req.get_method() != "GET" or req.data is not None`) as belt-and-braces. It carries no request body, so there is nothing for a write to send. The read lanes (`get_articles`, `get_sections`, `get_categories`, `list_macros`, `get_macro`, and all ticket ingestion) are untouched — **read-only is not read-nothing.**

**At the baseline `d27eb3e` there were exactly four call sites of the deleted methods, all in one file:** `src/data/zendesk_store.py` lines 161, 167, 254, 257, inside `publish_article_draft` / `publish_macro_draft`. Both functions are rewritten in the same commit to be local bookkeeping.

**Therefore `zendesk_client.py` and `zendesk_store.py` are an atomic pair:**

- New client + **stale store** → `AttributeError` at publish time. Loud, safe, obvious.
- **Stale client** + new store → **the dangerous direction.** The write methods stay alive on the Mac even though nothing calls them, the policy has silently reverted, and `tests/test_zendesk_readonly_guard.py` fails. The one-pass `rsync` in 4.4 makes this impossible; the §6 smoke catches it if it happens anyway.

Why Zendesk specifically, and not Guru or Asana: Guru and Asana are production-locked behind domain and Zscaler restrictions and their deletions are restorable. A Zendesk Guide instance is a **public domain** and its content is **not restorable the same way**. **Guru and Asana write paths are out of scope of this policy and must not be touched.**

### 12.C — The web triple (all authority is in Python)

- `src/services/zendesk_web.py` [A, 2,242 lines / 112 KB] — the largest new file. Holds **all** authority: sanitize-every-`srcdoc`, copy-exact via a Python `QClipboard` reading DB bytes, native confirms for every destructive action.
- `src/ui/web/zendesk_bridge.py` [A, 185 lines] — **pure relay.** No logic, no authority.
- `web/src/zendesk/` [A, 18 files] — the SPA. A pure renderer: no server, no localhost, one `file://` bundle, no network from the page, no secrets.

**QWebChannel is the trust boundary** — any page script can call any slot, so no slot carries authority. Reads return viewmodels; side-effectful actions validate against Python-held state plus a single-winner claim plus a **native** confirm (`QMessageBox`, unreachable from Chromium). Never `dangerouslySetInnerHTML` / `innerHTML` in `web/src/` — `tests/test_web_guardrails.py` enforces it in the §8 group 4.

`src/ui/pages/enablement/page.py` [M, +458/−12] wires it in `_make_zendesk`, with the native `ZendeskPage` as the **construction-failure fallback**: any import or construction error in the web branch yields the native tab, not a crash.

### 12.D — The Guru half (do NOT treat this range as Zendesk-only)

`45aab99` + `ed6482b` touch eleven Python files and one JSX file well outside `src/data/zendesk_*`, and they fix two **confirmed criticals**: attaching a quiz published *only* the quiz, overwriting live cards; and approval surfaces rendering markdown while publish sent a separate HTML column. Skipping any of these re-introduces both.

`enablement_store.py` (+311, `publish_body()` — the single definition of the published body) · `agent_chat.py` (+808, the publish gate, fingerprints, `record_approval`/`revoke_approval`) · `chat_bridge.py` (+237, `PublishConfirmHost`, `build_publish_confirm_dialog`, `_publish_field`) · `guru_content_pipeline.py` · `html_markdown.py` · `artifact_tools.py` · `enablement_web.py` · `guru_page.py` · `guru_preview.py` · `workbench.py` · `expand_overlay.py` · `web/src/chat/ChatApp.jsx` (+238).

**`src/ui/main_window.py` is a 10-line change that is easy to miss and fails closed.** It injects `confirm_host=PublishConfirmHost(self)` into `AgentChatController`. **Without that injection the controller refuses to publish Guru cards at all.** The Mac symptom would be "Guru publish does nothing" with no obvious cause. The §6 smoke checks `PublishConfirmHost` imports; the `git status` count in 4.5 is what proves `main_window.py` actually changed.

`ed6482b`'s fix: a model-written draft `title` (from `revise_draft` → `update_draft_content`) was interpolated unchecked into the publish confirm's 5-field destination pane, which joins fields with newlines — a title carrying newlines injected fake field lines and pushed the real Destination and card_id below the pane's fold. Every field value is now collapsed to one line and capped before the join. **Python-side only.** The bundle delta in that commit is pure Vite re-minify churn (2,099 bytes of variable renaming) — do not let it convince you the fix is in the bundle.

### 12.E — Sanitizer and dialog hardening

`src/data/html_sanitize.py` [M, +1,174/−7] gains an **additive preview profile** so pulled articles render with their real classes inside the sandboxed iframe. **The strict `sanitize_html` profile guarding stored content and the clipboard is untouched.** In `45aab99` the preview CSS policy inverted to a provably-safe **allowlist**: anything that could hide, shrink, move or obscure content is dropped and reported, so an unknown future property is dropped by default.

`src/ui/pages/enablement/_common.py` [M, +31/−1] adds `force_plain_text(dialog)`, applied via `style_native_dialog`. Subtle cause: `QLabel`/`QMessageBox` default to `Qt::AutoText`, and `Qt::mightBeRichText` **only scans up to the first newline** — exactly where interpolated content names sit. A name beginning `<!--` swallowed the rest of the dialog's text.

### 12.F — Renn's tool surface

Three dispatch paths all carry the same tool definitions and all state that Zendesk is read-only: `src/llm/claude_tools.py` (+220/−14), `src/mcp/chat_mcp_server.py` (+174/−13), `src/data/chat_tools/enablement_tools.py` (+143/−85), registered via `src/data/chat_tools/registry.py` (+35/−4). The new family lives in `src/data/chat_tools/zendesk_mirror_tools.py` [A, 537]: **propose-only.** Tools create `pending` drafts with mandatory rationale and sources; a specialist reviews the word-diff, marks ready, copies exact content, pastes into real Zendesk by hand, marks copied. Lifecycle `pending → ready → copied`; `copied` and `pushed` are immutable. **No tool can reach a Zendesk write, because there are none left to reach** (§12.B).

### 12.G — macOS risk on a brand-new WebEngine surface

This tab has **never run on macOS**. The project's own history is why that matters: the Agent chat rendered blank on macOS in July, from two environmental causes — PySide6 **framework corruption** (a re-sign or copy leaving `QtWebEngineCore.framework` inconsistent) and a **Rosetta page-size mismatch (QTBUG-98487)**, where an x86_64 Python under translation reports a 4096-byte mach page size on Apple Silicon and the mixed-arch renderer is killed at launch. Neither was in app code.

The diagnostic is already written — **use it first, always**:

```bash
<venv-python> scripts/web_diag.py           # human-readable; exit 1 on any FAIL
<venv-python> scripts/web_diag.py --json    # attach this to any bug report
```

Its macOS probes are `page_size`, `rosetta` (`sysctl.proc_translated`, fix named inline: `LSRequiresNativeExecution` / `arch -arm64`), `helper_arch` (Mach-O slices of the bundled `QtWebEngineProcess` versus `platform.machine()` — the QTBUG-98487 kill fingerprint), framework integrity under the PySide6 tree, and `quarantine`. It also dumps automatically when a renderer dies, and at startup under `ALMA_WEB_DIAG=1` (§10).

**M1 / 16 GB specifics:** this is the **fourth** WebEngine surface (chat, calendar, workbench, home). On `web_tabs: zendesk` only chat and zendesk are live — that is the argument for `zendesk` over `all` on a 16 GB machine. `scripts/measure_web_rss.py` gives the number if anyone asks. And because `web_flags` fails closed and `_make_zendesk` falls back on any error, **a WebEngine problem yields the native tab, not a crash** — which means a blank Zendesk tab is never a reason to roll back the schema.

### 12.H — Consolidated silent-failure matrix

| Symptom | Almost certainly | First action |
|---|---|---|
| Zendesk tab blank / white | macOS WebEngine, not this port | `scripts/web_diag.py`, then §9's probe. **Do not touch the DB.** |
| Whole app blank at launch | `ui.web_home` got turned on — not part of this port | Set it back to `off` |
| Tab renders but article/macro lists are empty | 051 didn't land, or FTS is desynced | §7 verification → §16 R2 |
| **Import file / Import folder does nothing at all** | **`src/data/app_paths.py` is missing** — a pre-`1aa0ef2` zip, or a partial sync | §4.2. Nothing is logged; this is the only tell. Re-check the archive comment, then re-run 4.4 / 4.5. |
| PowerPoint deck export raises | Same root cause | §4.2 |
| Guru publish does nothing | `PublishConfirmHost` not injected — stale `src/ui/main_window.py` | §12.D; re-run 4.4 / 4.5 |
| Guru publish fails at the API | `publish_collection_id` is still `col-1` | §13.2 |
| Pull says `zendesk_not_connected` | No credentials — expected, not a defect | §13.3 |
| Splash says `Schema v50` | Migrator raised; `db_manager` swallowed it | Read the `alma.db` log for `Schema migration skipped:` → §16 R2 |
| `test_zendesk_readonly_guard` fails | A stale `zendesk_client.py` survived | §12.B. **Remove the write, never the guard.** |
| Copy button produces no clipboard content and no dialog | Working as designed — no dialog means no write | Not a defect (§10, smoke 5) |

---

## 13. New and relevant settings keys — READ-ONLY inventory

**You never write any of these during the port** (rules 1 and 2). This table exists so you know what the Mac does with them left alone, and what the owner may choose to change afterwards.

| Key | Where it lives | Default when absent | Effect on this Mac if untouched |
|---|---|---|---|
| `enablement.web_tabs` | `data/settings.yaml` | `"off"` | Native Qt tabs everywhere; the whole new web workspace stays dormant. Valid values are now `off`, `calendar`, `zendesk`, `all`. |
| `ui.web_home` | `data/settings.yaml` | `"off"` | **Separate flag, not part of this port.** Leave alone. |
| `enablement.guru.publish_collection_id` | `data/settings.yaml` | — | **Check it before any Guru publish.** See §13.2. |
| `documents.root` | `data/settings.yaml` | `data/documents` | Optional override for the documents tree `app_paths.py` manages. Absent is fine. |
| `zendesk_api_key` | **macOS Keychain** (`pat_store._SECRET_KEYS`) | absent | Pull degrades to `zendesk_not_connected`. Everything else works. |
| `zendesk_subdomain` · `zendesk_email` · `zendesk_view_id` | `~/.alma-insights/ui_state.json` | absent | Same. **Outside `data/` — rule 2 covers it.** |

### 13.1 — `enablement.web_tabs`: `zendesk`, not `all`

`src/ui/web/web_flags.py`: `VALID_MODES = ("off", "calendar", "all", "zendesk")`, default `"off"`, and **anything unrecognized or any settings error degrades to `"off"`** — a flag can never take the native surface away by accident.

| value | calendar | workbench | zendesk |
|---|---|---|---|
| `off` (default) | native | native | native |
| `calendar` | web | native | native |
| `zendesk` | native | native | **web** |
| `all` | web | web | web |

`zendesk` is a **solo rollout** value — it does not turn on Calendar or Workbench. For a QtWebEngine surface that has never run on macOS, on a 16 GB M1, it is the right first value: it lights exactly the surface being ported, leaves the other two on their proven native widgets, and keeps the Chromium blast radius to one tab. `all` additionally flips two surfaces this port did not change, and is the known cause of native-widget test failures on the dev box.

**The flag is read at page construction — a restart is required after changing it.**

### 13.2 — The `publish_collection_id` trap (named gotcha; this has bitten before)

`enablement.guru.publish_collection_id` ships in some settings as the demo placeholder **`col-1`**. Every publish that falls back to the default target then fails at the Guru API — and per `ZENDESK_GURU_IMPLEMENTATION_NOTES.md`, *a failed publish is exactly what used to arm the approval-gate hazard* that `ed6482b` and `4ccd321` closed. **Check it on every machine.**

```bash
grep -n -A6 "^  guru:" data/settings.yaml
```

It must be a real Guru collection UUID (shape: `a3fa9e07-41c5-4276-afcf-9c027e97deb9`) — never `col-1`, never blank. List the real ids with `GuruClient.list_collections()`. **Report the value to the owner; do not set it yourself** (rule 1).

### 13.3 — Zendesk credentials (all optional)

Storage is split: `zendesk_api_key` goes to the **macOS Keychain**; `zendesk_subdomain`, `zendesk_email` and `zendesk_view_id` go to `~/.alma-insights/ui_state.json`.

`ZendeskClient.from_settings()` returns `None` unless subdomain **and** email **and** key are all present, and `pull_mirror` then returns `{"ok": False, "error": "zendesk_not_connected"}` and **never raises**.

**Nothing else needs credentials.** File and folder import, the entire mirror workspace, search, drafts, versions, diffs and copy-exact all work with zero credentials. **Ship this port credential-less** and add the key later if the owner wants live pulls.

**There is no Zendesk credential UI in enablement mode.** The only entry point is **product mode → Source Monitor → Connection tab**. Say this out loud, or the operator will hunt through enablement Settings and not find it.

---

## 14. Known oddities riding along in this range

Things the sync will visibly land that are not defects. Report, do not fix.

- `docs/ZENDESK_GURU_IMPLEMENTATION_NOTES.md` arrives new and its "Not yet done" section is honest about this port's own gaps — see §15.
- `CLAUDE.md` grows an 83-line Zendesk section. It is documentation for future development sessions; it changes no behavior.
- `src/data/INDEX.md` (+50) and `src/services/INDEX.md` (+13) are API reference files, regenerated by hand.
- The four `assets/help/*.md` changes are **not optional cosmetics.** `tests/test_help_claims_create.py`, `test_help_claims_troubleshooting.py` and `test_help_claims_reference.py` assert their exact claims, and they feed the in-app Help Center corpus. Skipping them would leave the Mac's Help articles telling the user the app can push to Zendesk when it structurally cannot.
- `web/src/zendesk/demo.js` (391 lines) is a fixture, not dead code — it is what §9's headless probe renders.
- `fabb4be` shows up in the history as a 5-file cosmetic refactor you never handle individually. Expected.
- **`docs/ZENDESK_BLOCK_PORT_GUIDE.md` — this file — is itself one of the 85 paths.** It landed in `24c8d0f`. Seeing it in the compare and in `git status` is correct, not a sign of a wrong snapshot (§4.1).
- **`main.py` gains 9 fenced lines** (`30fb086`) that call `app_paths.ensure_docs_tree()` after the splash. It is the only root-level file in the range. Not load-bearing (§4.2) — but it *is* a modified file, so it must land like any other.

---

## 15. What has NOT been verified — read this before trusting the tab

This is the honest ledger. All of it is the author's own disclosure in `ZENDESK_GURU_IMPLEMENTATION_NOTES.md`; it is repeated here because you are the person it affects.

1. **Never run on Apple Silicon.** Every verification behind this range was on Windows. The Zendesk workspace is a **new QtWebEngine surface** and this project has documented macOS blank-render history (§12.G). **Phase 7's probe is the first time this code will have executed on a Mac.** Treat a green probe as necessary, not sufficient.
2. **No human end-to-end UAT.** It is verified by test, headless probe and screenshot. **Nobody has pulled → reviewed → copied → pasted for real.** The first real specialist run is a UAT, and should be treated as one — with a scratch article, not a live one.
3. **Pull ceiling: 5,000 items per family** (`max_pages=50` × `per_page=100`, in `zendesk_client._paged`). At 4,000 macros that is 25% headroom. The pull reports `truncated: True` rather than lying about the count — but **whether that surfaces in the UI has not been verified.** If production is anywhere near that scale, raise the cap before relying on a pull.
4. **Timing at production scale (measured, Windows):** 200 articles × 2,000 words plus 4,000 macros ≈ 44 sequential GETs, **15–40 s** end to end. The database is not the bottleneck — 0.46 s to upsert the articles, 0.25 s for the macros, 2 ms FTS search, 43 MB on disk. Re-pulls of unchanged content are near-free thanks to content-hash dedup. Not measured on an M1.
5. **The versions/revisions editor is phase 1 only** — migration, store module and two React panels exist and pass; controller and UI wiring are not built (§11).
6. **Deferred audit majors.** Seventeen audit majors were left as-is by owner decision, along with a deliberate decision not to run `sanitize_html` on the Guru write path (Guru's native callouts and card-links depend on `class=` / `data-ghq-*` attributes the strict sanitizer strips) and to leave Renn's Guru/Asana write capability intact. **Two you should know about because they will look like bugs:** the Qt workbench "Push to Guru" ships the last *saved* body rather than what is on screen, and both "Review changes" diffs compare against an empty baseline, so a replacement renders as all-additions. **Read `ZENDESK_GURU_IMPLEMENTATION_NOTES.md` for the full list — this guide will not restate it and must not be treated as the authority on it.**
7. **The GET-only pull was live-verified on the dev box** (16 articles, 8 macros, 2 sections, 1 category, `truncated: False`) — so the pull lane itself is sound. **Only the Mac's own credentials are in question**, and this port does not need them (§13.3).

---

## 16. Rollback

Ordered least- to most-destructive. Note the asymmetry: **code rollback is cheap; schema rollback is a restore.** In almost every case the answer is R0.

**R0 — instant, and it covers roughly 95% of "the Zendesk tab is broken".** Set `enablement.web_tabs: off` in `data/settings.yaml` and restart. The native Qt `ZendeskPage` returns immediately. Migrations 051 and 053 stay applied and harm nothing — baseline code never reads their tables. **Try this before anything else.**

**R1 — code back to baseline.** File-copy method, symmetric with the port: re-inflate the `d27eb3e` source zip and `rsync -a` it over the checkout (again **no `--delete`**), then remove the three files that only exist forward of the baseline:

```bash
rm -f src/data/app_paths.py migrations/051_zendesk_mirror.sql migrations/053_zendesk_versions.sql
git status --porcelain                          # expect only untracked docs strays (see below)
git diff pre-zendesk-port-2026-07-28 --stat     # expect empty
```

`git diff` against the rescue branch is the authoritative check and **must be empty**. `git status` will still list `?? docs/ZENDESK_BLOCK_PORT_GUIDE.md` and `?? docs/ZENDESK_GURU_IMPLEMENTATION_NOTES.md` — both are added-in-range docs that the baseline `rsync` cannot remove. They are inert text; delete them or leave them, but do not let them make you think R1 failed.

**Deleting the two `.sql` files does not undo them.** The rows stay in `schema_migrations` and the tables and columns stay in the database. That is harmless — baseline code never reads them — and it means R1 is *not* a schema rollback.

**R2 — repair a bad 051/053 application without touching data.** This is the supported lever and it is empirically clean, because both migrations are re-runnable (§12.A). Use it when the FTS mirrors are desynced, the article lists are empty, or the draft baselines are missing:

```bash
# app fully closed
sqlite3 data/local_warehouse.db "DELETE FROM schema_migrations WHERE filename IN ('051_zendesk_mirror.sql','053_zendesk_versions.sql');"
<venv-python> -c "from src.data.db_manager import DatabaseManager; db=DatabaseManager(); db.initialize()"
```

This re-runs both migrations **and both post-hooks** — which is exactly how you rebuild the FTS projections and re-seed the draft baselines. Then re-verify with §7.

**R3 — full schema rollback.** There are **no down-migrations.** Restore the Phase-1 backup, app fully closed:

```bash
mv data/local_warehouse.db data/local_warehouse.db.failed
cp data/local_warehouse.pre051.db data/local_warehouse.db
rm -f data/local_warehouse.db-wal data/local_warehouse.db-shm
```

**Cost: every article and macro imported or pulled since the port, and every draft and version created since, is lost.** Do R0 first, R2 second, and R3 only if the database is genuinely corrupt.

**R4 — settings and credentials.**

```bash
cp ~/alma_port_backup_2026-07-28/settings.yaml data/settings.yaml
cp ~/alma_port_backup_2026-07-28/ui_state.json ~/.alma-insights/ui_state.json
```

Keyring secrets are untouched by this port and need no rollback.

---

## 17. Acceptance criteria — the definition of done

Report each line as ✅ / ❌ / skipped-with-reason.

1. **Baseline confirmed** — Phase 0 P1 showed `d27eb3e` (or a docs-only descendant), P2 showed all four pre-051 tables and max migration `050`.
2. **Backups exist** — `~/alma_port_backup_2026-07-28/` contains `pre_sync_tree.tgz`, `settings.yaml`, `pre_sync_compare.txt`, `post_sync_compare.txt`; `data/local_warehouse.pre051.db` exists; the `pre-zendesk-port-2026-07-28` branch exists (or the tar is explicitly the sole rollback).
3. **Environment gates green** — G1 `arm64 16384`, G2 `FTS5 OK`, G3 PySide6 quartet imports, G4 `web_diag` exit 0.
4. **Sync is byte-identical** — 4.5 shows **zero** "differ" and **zero** "Only in `$SRC`" lines; `git status --porcelain -uall | wc -l` = **85** (42 `??` + 43 ` M`), with any shortfall confined to `docs/`, and **no** `??` naming `052_solver_ledger.sql`, `docs_backfill.py`, `solver_ledger.py`, `docs/pilot/` or `wheel_tail.bin`.
5. **Integrity gate G5 green** — `dist/index.html` is exactly **333,921 bytes**, `qwebchannel.js` present, `__almaZendeskMounted` in the bundle, `051` and `053` present, **`052` absent**.
6. **No dependency work happened** — §5's three `diff`s all printed `identical`; no `pip install`, no `npm install`, no `npm build` was run.
7. **All import smokes passed** — including `ALL IMPORT SMOKES PASSED`, the read-only structural assertions, and `src.data.app_paths` (which shipped in the zip — it was **not** hand-created).
8. **Migrations applied via the migrator** — `schema_migrations` tail is `[…049, 050, 051_zendesk_mirror.sql, 053_zendesk_versions.sql]`; 6 tables and 6 triggers present; 4 new `zendesk_articles` columns; the import+FTS smoke returned `imported: 1` with a **non-empty** `fts` list.
9. **Test gates** — the four groups reported **145 / 252 / 102 / 166 = 665 passing**, with per-group counts recorded. `tests/test_zendesk_readonly_guard.py` **passed**.
10. **Headless SPA probe** — `mounted=True`, `innerText_len` > 200, `exit=0`.
11. **In-app smokes** — splash reads `Schema v53`; app reaches Home; existing surfaces unchanged; the classic Zendesk tab shows **"Copy for Zendesk" + "Mark as copied"** with the read-only notice; Help shows **62 articles**.
12. **Settings untouched** — `diff data/settings.yaml ~/alma_port_backup_2026-07-28/settings.yaml` is empty; `~/.alma-insights/ui_state.json` unchanged. (The warehouse differs by the new tables — that is expected, and it is not "destroyed settings".)
13. **`publish_collection_id` checked and its value reported to the owner** — not `col-1`, not blank; **and not changed by you**.
14. **Phase 8b done** — `git checkout enablement-content-tabs` completed without overwriting anything; `git log --oneline -3` tip matches the zip; the rescue branch is still in place; nothing was pushed.

If every line is ✅, the Mac is functionally identical to the Windows dev box at `a8ead66` — with its own settings intact, the entire new Zendesk workspace **dormant behind its own flag**, no credentials required, and the owner holding every switch. Flipping `enablement.web_tabs` to `zendesk` is then a separate, reversible, one-line decision (§13.1), and §16 R0 undoes it in one restart.

---

## Appendix A — Full file manifest (85 files at `a8ead66`, grouped)

This is a **VERIFICATION checklist** — it is what the Phase-2 pre-sync compare should show. It is **NOT** a list of manual copies (rule 9). **A** = added, copies wholesale, nothing to reconcile. **M** = modified, the Mac copy may have diverged.

**Everything is in this manifest.** There is no longer an off-manifest hand-created file: `src/data/app_paths.py` is listed under `src/data` below, and it ships. Totals: **42 A + 43 M = 85**.

### migrations — 2 files, both A

`051_zendesk_mirror.sql` (A, 137) · `053_zendesk_versions.sql` (A, 59)

### root — 1 M

`main.py` (+9/−0 — the fenced `ensure_docs_tree()` call from `30fb086`; not load-bearing, §4.2)

### src/data — 4 A, 11 M

**A:** `zendesk_import.py` (429) · `zendesk_versions.py` (284) · `chat_tools/zendesk_mirror_tools.py` (537) · **`app_paths.py` (124 — new in `1aa0ef2`; §4.2)**
**M:** `html_sanitize.py` (+1174/−7) · `zendesk_store.py` (+967/−102) · `enablement_store.py` (+311/−18) · `chat_tools/enablement_tools.py` (+143/−85) · `zendesk_client.py` (+116/−74) · `chat_tools/registry.py` (+35/−4) · `enablement_sim.py` (+27/−19) · `guru_content_pipeline.py` (+23/−8) · `chat_tools/artifact_tools.py` (+12/−2) · `html_markdown.py` (+6/−1) · `INDEX.md` (+50)

### src/services — 1 A, 3 M

**A:** `zendesk_web.py` (2,242 lines / 112 KB — the largest new file, holds all authority)
**M:** `agent_chat.py` (+808/−36) · `enablement_web.py` (+51/−26) · `INDEX.md` (+13)

### src/ui — 1 A, 11 M

**A:** `web/zendesk_bridge.py` (185)
**M:** `pages/enablement/page.py` (+458/−12) · `web/chat_bridge.py` (+237/−6) · `pages/enablement/zendesk_tab.py` (+93/−19) · `pages/enablement/_common.py` (+31/−1) · `web/web_flags.py` (+24/−2) · `pages/enablement/workbench.py` (+19/−4) · `pages/enablement/guru_preview.py` (+13/−7) · `pages/guru_page.py` (+12/−2) · **`main_window.py` (+10/−1 — fails closed, §12.D)** · `pages/enablement/expand_overlay.py` (+9/−3) · `web/dist/index.html` (241,310 → **333,921 bytes**)

### src/llm, src/mcp, src/updater — 3 M

`llm/claude_tools.py` (+220/−14) · `mcp/chat_mcp_server.py` (+174/−13) · `updater/schema_migrator.py` (+25/−0)

### web/src — 22 A, 2 M

**A (`web/src/zendesk/`):** `ZendeskApp.jsx` (609) · `garden.css` (623) · `demo.js` (391) · `shape.js` (333) · `RevisionCenter.jsx` (247) · `RevisionDiff.jsx` (207) · `ArticleEditor.jsx` (201) · `ArticleBody.jsx` (197) · `VersionsPanel.jsx` (184) · `HistoryPanel.jsx` (145) · `MacroEditor.jsx` (130) · `GardenChrome.jsx` (130) · `ArticleList.jsx` (125) · `MacroList.jsx` (99) · `CopyControls.jsx` (92) · `versionShape.js` (81) · `versions.css` (80) · `BodyEditForm.jsx` (35) · `zendesk.test.jsx` (1,461) · `shape.test.js` (428) · `versions.test.jsx` (437)
**A (other):** `web/src/chat/chat.test.jsx` (136)
**M:** `web/src/App.jsx` (+3 — route registration only) · `web/src/chat/ChatApp.jsx` (+238/−6)

### tests — 10 A, 7 M

**A:** `test_zendesk_bridge.py` (4,161) · `test_chat_review_panel.py` (1,540) · `test_zendesk_web_tab.py` (815) · `test_zendesk_mirror_tools.py` (773) · `test_zendesk_import.py` (763) · `test_publish_body_parity.py` (**585** — 500 at `4ccd321`, +85 in `a8ead66`; 28 tests) · `test_zendesk_versions.py` (567) · `test_zendesk_readonly_guard.py` (505) · `test_zendesk_mirror_schema.py` (499) · `test_zendesk_client_readonly.py` (253)
**M:** `test_zendesk_content.py` (+210/−41) · `test_help_claims_create.py` (+140/−146) · `test_workbench_bridge.py` (+66/−5) · `test_enablement_web_flag.py` (+43) · `test_help_claims_troubleshooting.py` (+39/−6) · `test_help_claims_reference.py` (+6/−6) · `test_web_guardrails.py` (+5)

### docs, help, CLAUDE.md — 1 A, 6 M

**A:** `docs/ZENDESK_GURU_IMPLEMENTATION_NOTES.md` (159)
**M:** `CLAUDE.md` (+83/−4) · `assets/help/create/zendesk.md` (+74/−35) · `assets/help/create/zendesk-macros.md` (+38/−26) · `assets/help/reference/settings-keys.md` (+7/−6) · `assets/help/troubleshooting/connect-first.md` (+7/−1)

**Addendum.** If the zip was taken from the branch tip rather than pinned at `4ccd321`, it may also contain `docs/ZENDESK_BLOCK_PORT_GUIDE.md` (this file) and other docs-only descendants. The pre-sync compare will show those paths beyond the 82 above — expected, and the basis for the structural authentication in §4.1. **Anything beyond Appendix A that is not under `docs/` is unexplained → STOP.**
