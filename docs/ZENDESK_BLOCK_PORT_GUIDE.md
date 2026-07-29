# Zendesk Block Port Guide — `d27eb3e` → `fbe370f` (enablement-content-tabs)

**Written:** 2026-07-28 on the Windows dev box, from the actual git delta. **Retargeted twice the same day** — first from `4ccd321` to `a8ead66` (see §1 for the four commits that moved it, and §4.2, which changed from a hand-create step into a verify step as a result), then forward through `d018107` to the current target `fbe370f` (full SHA `fbe370fbe6add2a502048c2bc2ebbd109e797504`).
**For:** the owner, on the production Mac (Apple Silicon M1, 16 GB, arm64). You are competent with a terminal but you did not write this code, and you should not have to read any of it to execute this guide.
**Scope:** every change committed between `d27eb3e` (2026-07-24, "docs(port-guide): Appendix A — corrected delivery path via GitHub Desktop") and `fbe370f` (2026-07-28, "feat(asana): real board management — the Sources panel was a mockup"), all pushed to `origin/enablement-content-tabs`.

**The delta:** 18 commits, **96 files** (45 added, 51 modified, **zero deleted, zero renamed** — a copy-over sync is complete and nothing on the Mac needs removing), +30,759 / −842 lines. 4 new SQL migrations (051, 053, 054, 055 — the 052 gap is real and safe, §7). **Zero new dependencies:** `requirements.txt`, `PACKAGES.md`, `web/package.json` and `web/package-lock.json` are byte-identical across the whole range. **The Mac needs no `pip install` and no `npm install`.** That removes the single largest historical source of Mac port pain from this run.

**What makes this port different from the last one.** `PILOT_FIX_PORT_GUIDE.md` was a patch: five surgical FIND/REPLACE hunks into code the Mac already had. **This is an addition.** The entire Zendesk mirror subsystem — the local mirror schema, the importer, the controller, the bridge, the React workspace, the Renn revision tools — does not exist on production at all. 45 of the 96 files are brand new and copy wholesale with nothing to reconcile. The risk in this port is concentrated in the **51 modified files**, and inside those, in a much smaller set of about ten that are load-bearing in non-obvious ways. §1 and §12 name them individually.

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
4. **NEVER run `npm build` / `npm --prefix web run build`.** The React app ships as a committed build artifact: `src/ui/web/dist/index.html` (**333,921 bytes at `fbe370f`** — unchanged since `45aab99`, single-file bundle) plus `src/ui/web/dist/qwebchannel.js` (15,152 bytes, unchanged since June and still required). The Mac needs **no Node and no npm at all** to run the app. Rebuilding overwrites `dist/` with non-identical bytes and puts the Mac out of parity with what was tested.
5. **NEVER run a migration by hand with `sqlite3 data/local_warehouse.db < migrations/051_….sql`.** Two of the four new migrations carry mandatory Python post-hooks, and **none of the four may be applied by hand** (§7). A raw `sqlite3` apply skips them, leaves the FTS mirrors indexing empty body text and the drafts with no rollback baseline, and records nothing in `schema_migrations` — so the migrator will later try again on a half-built schema. Migrations land **only** by launching the app or by the explicit `DatabaseManager().initialize()` call in §7.
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

**Fourteen of the range's eighteen commits are listed below.** The other four
(`4a4a132`, `8b15915`, `9c4a697`, `9618db1`) are docs-only revisions of *this
file* between `a8ead66` and `d018107` — they touch no code, add no path, and
need no row. `git log --oneline d27eb3e..fbe370f | wc -l` returning **18** is
therefore correct.

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
| `d018107` | 07-28 | The Settings **credentials card** (2 files, +211/−3). `src/ui/widgets/credentials_panel.py` gains a Zendesk card so entering Zendesk credentials no longer requires switching into product mode. Preservation rules and the verification command are in **§12.I**. |
| `d2ae828` | 07-28 | Documentation only (1 file — **this guide**, gaining §12.I). **Zero code change.** Its presence in the zip is expected, not an anomaly (§4.1). |
| `fbe370f` | 07-28 | **Asana board management** (14 files, +2,161/−78; migrations **054** and **055**). The enablement Settings → Sources Asana panel was a **hardcoded mockup** — it displayed two configured boards, names, pills and resolver names that were literals, while the database had no board mapped at all. It is now a pure renderer over real rows, with working add / sync / calendar / remove controls, many-to-many board↔task ownership, and a native confirm gating the (local-only) removal. **Full detail, both rough edges and the verification command: §12.J.** |

**One consequence to internalize now:** only the **tip** bundle is correct. `2cc0089` and `5743ef3` ship a stale `dist/index.html` (the React work in `5743ef3` did not reach the bundle until `45aab99`). You are syncing the tree at `fbe370f` in one pass, so this is automatic — but it is why you must never hand-merge or partially copy `src/ui/web/dist/index.html`. **No commit after `4ccd321` rebuilt the bundle** — `fbe370f` touches no file under `web/` at all — so every `333921` assertion in this guide is still exactly right at the new target.

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

**PASS:** the short SHA is `d27eb3e`, or a docs-only descendant of it. **FAIL:** if `git log --oneline -20` shows any of `1ba6fa5`, `fabb4be`, `2cc0089`, `5743ef3`, `45aab99`, `ed6482b`, `4ccd321`, `1aa0ef2`, `24c8d0f`, `30fb086`, `a8ead66`, `4a4a132`, `8b15915`, `9c4a697`, `9618db1`, `d018107`, `d2ae828`, `fbe370f`, part or all of this range is already applied — **STOP and report**; a re-run is not automatically safe.

**P2 — the pre-051 Zendesk tables must already exist.** Migration 051 is mostly `ALTER TABLE … ADD COLUMN` against tables created by migration 030. If they are absent, 051 fails — and it fails *silently* (§7).

```bash
sqlite3 data/local_warehouse.db "SELECT name FROM sqlite_master WHERE name IN ('zendesk_articles','zendesk_macros','zendesk_article_drafts','zendesk_macro_drafts');"
sqlite3 data/local_warehouse.db "SELECT filename FROM schema_migrations ORDER BY filename DESC LIMIT 3;"
```

**PASS:** all four table names print, and the highest applied migration is `050_enablement_documents_fts.sql` with `051`, `053`, `054` and `055` all absent. **FAIL:** any missing table → **STOP**; the Mac is behind the migration-030 era and this port is not the fix. A migration higher than 050 already applied → **STOP**, see P1.

**P3 — is this a git repo at all?** Everything downstream branches on the answer.

```bash
git rev-parse --is-inside-work-tree 2>/dev/null || echo "NOT a git repo"
```

### Getting the zip (you do the download)

**Use the branch form:**

```
https://github.com/zeppelin150/alma-insights/archive/refs/heads/enablement-content-tabs.zip
```

**Why not a pinned SHA — read this, it is not a style preference.** A guide can
never contain its own commit hash: committing this file creates a new commit,
so any SHA printed here is necessarily an *ancestor* of the revision you are
reading. A zip pinned at `a8ead66` therefore carries the **superseded** guide —
the one whose §4.2 tells you to hand-create `src/data/app_paths.py`, which is
wrong now that the module ships. The branch form always resolves to the tip and
is the only form that delivers the guide you are currently reading.

The code delta this guide describes is **`d27eb3e..fbe370f`**;
any commit after `fbe370f` on this branch is expected to be documentation-only
(this file). If the branch has advanced into new *code* since — check §4.1's
structural compare — **STOP and report** rather than proceeding.

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
# Prints the full SHA of whatever commit the zip was cut at.
```

**Do not expect a specific SHA here — authenticate structurally instead.** You
downloaded the branch tip (§3), which is by construction a *descendant* of
`a8ead66`: this guide cannot contain the hash of the commit that contains this
guide. The SHA is useful to record in your report, not to gate on.

**The gate is the compare, not the hash.** The pre-sync compare (4.3) must show
nothing beyond Appendix A. Anything extra must be **under `docs/`** and
docs-only — those are this guide's own later revisions and are expected. **Any
extra path outside `docs/` — any `.py`, any `.sql`, any file under `src/` or
`web/` or `tests/` — is code this guide did not review → STOP and report it
before syncing.** That single rule is what makes a moving branch tip safe to
port from.

```bash
SRC=$(ls -d ~/alma_port_incoming/alma-insights-*)
DEST=<the alma-insights checkout>
echo "SRC=$SRC"; echo "DEST=$DEST"
xattr -rd com.apple.quarantine "$SRC" 2>/dev/null || true
```

Inflation sanity count — a truncated inflate is the classic silent failure:

```bash
find "$SRC/assets/help" -name '*.md' | wc -l          # expect 63
ls "$SRC/migrations"/05*.sql                          # expect 050, 051, 053, 054, 055 — and NO 052
wc -c "$SRC/src/ui/web/dist/index.html"               # expect 333921
wc -c "$SRC/src/ui/web/dist/qwebchannel.js"           # expect 15152
```

**`migrations/052_solver_ledger.sql` is correctly absent.** It belongs to a separate local-only workstream on the dev box, it is untracked, and it is not part of this port. Do not create it. Do not "fix" the gap. **054 and 055 land on the far side of that gap** (they ship with `fbe370f`, §12.J) and change nothing about it — the sequence production applies is 051 → 053 → 054 → 055. §7 explains why the gap is harmless.

### 4.2 Verify `src/data/app_paths.py` is in the zip — do NOT create it

**This step used to be a hand-create. It is now a one-line check.** At `4ccd321` this module was imported by committed code but had never been `git add`ed, so it was missing from the archive. Commit `1aa0ef2` fixed exactly that. **At `fbe370f` the file is tracked and ships in the zip. You create nothing.**

```bash
wc -c "$SRC/src/data/app_paths.py"      # expect 4240
wc -l "$SRC/src/data/app_paths.py"      # expect 124
```

**PASS:** the file exists in `$SRC`. Nothing to do — it rides in with the one-pass `rsync` in 4.4 like every other added file. Go to 4.3.

**FAIL (file absent):** you are **not** holding a zip at `a8ead66` or later (the target is `fbe370f`). Do not hand-write the module and do not proceed — go back to §4.1, re-check the archive comment, and download the branch-tip archive again (§3; never a pinned-SHA archive). A tree missing this file will launch, pass most of the smokes, and then fail silently on the exact feature this port exists to deliver.

**Why this one file gets its own gate.** `src/ui/pages/enablement/page.py` imports `src.data.app_paths` **lazily, inside three methods** — so a missing module cannot break launch or import-time smokes. The damage is at click time, and it is silent:

| Where | User-visible failure if the module is missing |
|---|---|
| `page.py` `_web_zendesk_file_pick` | Zendesk web tab → **Import files…** is a **silent no-op**. No error, no log line. |
| `page.py` `_web_zendesk_folder_pick` | Zendesk web tab → **Import folder…** is a **silent no-op**. |
| `page.py` `_on_pptx_export` | **PowerPoint deck export raises** — a regression of a feature already shipped on the Mac. |

The silence is structural: `zendesk_web.py::_pick_files` catches `Exception` and returns `[]`, so `ModuleNotFoundError` becomes "the button did nothing". That is how the gap was found in the first place, and it is why the check above is worth ten seconds.

Commit `30fb086` additionally calls `app_paths.ensure_docs_tree()` from `main.py` at boot, inside a `try/except` that prints and continues. That hunk is **not** load-bearing — `space_dir()` does `mkdir(parents=True, exist_ok=True)` on every access, so every consumer creates what it needs on first use. It only makes the `data/documents/` folders appear before the user goes looking for them.

**Verify after the sync** — run these two in `$DEST` once 4.4 has completed, not now (this replaces the old hand-create verification):

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

- **"Files … differ"** and **"Only in `$SRC`"** together are the delta about to be applied. They should account for the **96 paths in Appendix A**, plus any docs-only descendants explained in §4.1. The rule, not a guess: **every `A` row in Appendix A is a "Only in `$SRC`" line (45 of them, including `src/data/app_paths.py`), and every `M` row is a "differ" line (51 of them).** **More than that = baseline drift** — the sync heals it, but report every extra path.
- **"Only in `$DEST`"** = Mac-local files the sync will leave untouched. Expected: `data/` never appears (excluded), `.venv`, editor files. **Exception to inspect: any `$DEST`-only `.py` under `src/` or `web/src/`** is a stray hand-copy that can **shadow imports** after the sync — report these; do not delete on your own. `src/data/app_paths.py` must **not** appear here: it now arrives from `$SRC`, and a `$DEST`-only copy would mean somebody hand-created it from an earlier revision of this guide. If you see it there, say so — the `rsync` will overwrite it with the shipped version, which is the correct outcome, but the owner needs to know it happened.

### 4.4 Sync — one pass

```bash
rsync -a --exclude 'docs/ZENDESK_BLOCK_PORT_GUIDE.md' "$SRC"/ "$DEST"/
# NO --delete. EVER. (rule 3)  Trailing slashes are load-bearing.
# The --exclude is this guide protecting itself: if you are reading the copy
# inside $DEST, an unguarded sync overwrites it mid-port with whatever
# revision the zip happens to carry — which, for any zip pinned at or before
# a8ead66, is the SUPERSEDED revision whose §4.2 tells you to hand-create
# src/data/app_paths.py. That instruction is wrong now (the module ships) and
# following it would put a stale hand-written file in front of the shipped
# one. Copy the guide in deliberately at the END of the port instead:
#   cp "$SRC/docs/ZENDESK_BLOCK_PORT_GUIDE.md" "$DEST/docs/"   # only if $SRC is newer
```

**Do not cherry-pick, do not hand-apply hunks, do not copy files one at a time.** The per-file detail in §12 and in Appendix A exists for verification and debugging, not as a to-do list of manual copies. In particular, `src/data/zendesk_client.py` and `src/data/zendesk_store.py` must land together (§12.B) and a one-pass `rsync` guarantees that.

### 4.5 Post-sync compare (the byte-identity proof — this replaces `git rev-parse` in a zip flow)

```bash
diff -rq "$SRC" "$DEST" \
  --exclude data --exclude .git --exclude .venv --exclude venv --exclude __pycache__ \
  --exclude .pytest_cache --exclude .claude --exclude node_modules --exclude .DS_Store \
  | sort > ~/alma_port_backup_2026-07-28/post_sync_compare.txt
grep -c "differ$" ~/alma_port_backup_2026-07-28/post_sync_compare.txt      # expect 0
grep -c "^Only in $SRC" ~/alma_port_backup_2026-07-28/post_sync_compare.txt  # expect 0 or 1 — see below
grep "^Only in $SRC" ~/alma_port_backup_2026-07-28/post_sync_compare.txt
```

**PASS = zero "differ" lines, and the only permitted "Only in `$SRC`" line is
`docs/ZENDESK_BLOCK_PORT_GUIDE.md` itself** — 4.4 deliberately excludes this file
from the sync (it is the document you are reading; see the comment there), so it
stays unsynced by design. That is why the second command above prints the lines
rather than only counting them: **any OTHER "Only in `$SRC`" path means the sync
did not complete.** ("Only in `$DEST`" strays remain by design.) **FAIL:** the sync did not complete — re-run 4.4 and re-check. Do not proceed on a partial tree; §12.B explains why a partial sync of this particular range is the one dangerous outcome.

**If it IS a git repo**, capture the audit trail of exactly what the sync changed:

```bash
# -uall is load-bearing: without it git COLLAPSES a wholly-new directory
# (web/src/zendesk/) into a single ?? line and the count will not add up.
git status --porcelain -uall > ~/alma_port_backup_2026-07-28/applied_delta_git_view.txt
wc -l ~/alma_port_backup_2026-07-28/applied_delta_git_view.txt      # expect 96
grep -c '^??' ~/alma_port_backup_2026-07-28/applied_delta_git_view.txt   # expect 45
grep -c '^ M' ~/alma_port_backup_2026-07-28/applied_delta_git_view.txt   # expect 51
grep '^??' ~/alma_port_backup_2026-07-28/applied_delta_git_view.txt
```

**Expect exactly 96 lines** — the 96 files in `d27eb3e..fbe370f` — split **45 `??`** (the added files, which are untracked because the Mac's HEAD is still the baseline) and **51 ` M`**. `src/data/app_paths.py` is now one of the 45 `??`, not a special case.

**The count is a rule, not a magic number**, and it can legitimately move in
*both* directions:

- **Fewer than 96** — if the Mac's baseline is a *docs-only descendant* of
  `d27eb3e` (allowed by P1), one or two docs paths may already match and drop
  out. A count of 94–96 is fine **when the shortfall is entirely under
  `docs/`**. A shortfall anywhere else means a partial sync → re-run 4.4.
- **More than 96** — you synced from a branch tip carrying later commits (§3
  tells you to, and §4.1 explains why the SHA will not match). Every extra line
  must be **under `docs/`**. Note that
  `docs/ZENDESK_BLOCK_PORT_GUIDE.md` will NOT be among them: 4.4 excludes it
  from the sync, so it never reaches `$DEST` and cannot appear in a git status
  of `$DEST`. It shows up in the 4.5 compare instead, as the one permitted
  "Only in `$SRC`" line. **Any extra line
  outside `docs/` is unreviewed code → STOP and report.**

**Any `??` line naming `migrations/052_solver_ledger.sql`, `src/data/docs_backfill.py`, `src/data/solver_ledger.py`, `docs/pilot/` or `wheel_tail.bin`** means the zip was inflated from a dirty snapshot or copied too broadly — **STOP, do not commit.**

### Gate G5 — integrity spot-checks (binary fidelity + junk awareness)

```bash
<venv-python> -c "import os; s=os.path.getsize('src/ui/web/dist/index.html'); print(s); assert s==333921, 'dist bundle size mismatch'"
ls -la src/ui/web/dist/qwebchannel.js                       # must exist; 15152 bytes, unchanged in this range
grep -c __almaZendeskMounted src/ui/web/dist/index.html     # expect >= 1 — the Zendesk route is in the bundle
grep -c __almaHomeMounted src/ui/web/dist/index.html        # expect 1 — the other routes survived the rebuild
ls -la migrations/051_zendesk_mirror.sql migrations/053_zendesk_versions.sql \
       migrations/054_task_board_source.sql migrations/055_task_board_links.sql
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

All three must print `identical`. `requirements.txt`, `PACKAGES.md`, `web/package.json`, `web/package-lock.json`, everything under `installer/` and everything under `scan_server/` are untouched across the whole of `d27eb3e..fbe370f` (re-verified at each retarget).

**Therefore: no `pip install`. No `npm install`. And never `npm run build` (rule 4).** Every new module in this range is stdlib-only or uses packages the Mac already has. If an import smoke in §6 fails with `ModuleNotFoundError` for a third-party package, that is a **pre-existing** venv problem on the Mac, not something this port introduced — report it rather than installing your way around it.

`installer/build_release.py` already ships `migrations/` inside `APP_CONTENTS`, so an installed bundle picks up all four new `.sql` files with no installer change.

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

**If `src.data.app_paths` raises `ModuleNotFoundError`:** the zip predates `1aa0ef2` or the sync was partial. **Do not hand-write the module** — go back to §4.1/§4.2 and get a zip at `fbe370f` (the branch tip), then re-run 4.4. The app will still launch without it, which is exactly what makes this failure dangerous.

**If `WRITE METHOD STILL PRESENT` fires:** a stale `src/data/zendesk_client.py` survived the sync. This is the dangerous direction of a partial port (§12.B). Re-run 4.4 and 4.5.

**If `ZendeskWriteBlocked missing` or a `READ LANE MISSING` fires:** the tree is not at `fbe370f` — re-run Phase 2 steps 4.4 and 4.5.

---

## 7. Phase 5 — First launch & migrations 051 → 053 → 054 → 055

### The numbering gap 051 → 053 is safe

Production will apply `051_zendesk_mirror.sql`, then `053_zendesk_versions.sql`, `054_task_board_source.sql` and `055_task_board_links.sql`, with **no 052**. That is correct and expected. The two Asana migrations (§12.J) sit on the far side of the gap and do not change its reasoning by one line — they are simply the next two files the glob finds.

`src/updater/schema_migrator.py::pending()` is:

```python
files = sorted(self._dir.glob("*.sql"))
return [f for f in files if f.name not in applied]
```

Discovery is **sorted filename minus the set already recorded in `schema_migrations`**. There is no contiguity check, no "expected next number", nowhere that compares N to N−1. A missing `052_*.sql` is simply a file that never appears in the glob. `current_version()` is `max()` over the parsed leading digits, not a count, so the version reads **55** and nothing is confused by the jump. Migration 053's SQL references nothing 052 creates — its own comment cites 052 only as a *precedent* for a soft-reference convention. 054 and 055 reference nothing 052 creates either: 054 adds a column to `enablement_tasks` (migration 027's table) and 055 creates `task_board_links` against that same table.

**Do not hand-create a placeholder `052`.** That would record a filename in `schema_migrations` that does not correspond to anything, and it would diverge production from every other machine.

### ⚠ Migration failure is SILENT at launch

`src/data/db_manager.py` wraps the migrator in `try/except Exception` and degrades to a `logging.warning("Schema migration skipped: %s", exc)`. **The app boots normally on a half-migrated schema. A clean launch is NOT evidence that migrations applied.** Verification below is mandatory, not optional.

### Apply the migrations

Rule 5 stands: **never** `sqlite3 data/local_warehouse.db < migrations/051_zendesk_mirror.sql`. Two of the four migrations carry Python post-hooks a raw apply would skip:

| Migration | Post-hook | What it does, and what breaks without it |
|---|---|---|
| `051_zendesk_mirror.sql` | `_posthook_051_zendesk_projections` → `zendesk_store.backfill_mirror_projections(conn)` | Recomputes the Python-side `body_text` / `actions_text` plain-text projections and `content_hash` for rows predating 051, then the FTS mirrors rebuild. Without it, pre-051 rows index **empty body text** — invisible to search, and **unrepairable on a box with no API key to re-pull from**. |
| `053_zendesk_versions.sql` | `_posthook_053_seed_draft_baselines` → `zendesk_versions.seed_draft_baselines(conn)` | Seeds a `seq=1` `'create'` version from each existing article draft so pre-053 drafts have a rollback baseline. Guarded — only drafts with zero version rows are seeded, so it is re-run safe. |
| `054_task_board_source.sql` · `055_task_board_links.sql` | **none** | Pure SQL, and idempotent by construction (single-line `ADD COLUMN`, `CREATE … IF NOT EXISTS`, `INSERT OR IGNORE`). They still must land **through the migrator**, because a raw `sqlite3` apply records nothing in `schema_migrations` and the migrator would then try them again. 055's backfill deliberately links only rows that already carry a non-NULL `board_source_id` — see §12.J's first rough edge. |

Apply them the sanctioned way, with the app closed:

```bash
cd <the alma-insights checkout>
<venv-python> -c "
from src.data.db_manager import DatabaseManager
db = DatabaseManager(); db.initialize()
rows = [r[0] for r in db.conn.execute('SELECT filename FROM schema_migrations ORDER BY filename')]
print(rows[-6:])
"
```

**Expect the tail:** `['049_kb_extra_fields.sql', '050_enablement_documents_fts.sql', '051_zendesk_mirror.sql', '053_zendesk_versions.sql', '054_task_board_source.sql', '055_task_board_links.sql']`.

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

# 054 + 055 (Asana board management, §12.J)
sqlite3 data/local_warehouse.db "PRAGMA table_info(enablement_tasks);" | grep -c board_source_id   # expect 1
sqlite3 data/local_warehouse.db "SELECT name FROM sqlite_master WHERE name='task_board_links';"    # expect task_board_links
```

**On the next app launch the splash's Database integrity line must read `Schema v55`.** If it still says `v50`, the migrator raised and `db_manager` swallowed it — read the `alma.db` log for `Schema migration skipped:` and go to §16, R2.

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

Four groups for the Zendesk block, then one more for the Asana work. **Each in its OWN pytest invocation** — grouping WebEngine-touching files stacks Chromium teardown to exit 255. Never `pytest tests/` (rule 6). Timings are from the dev box; the M1 will be in the same order.

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
# expect: 165 passed   (~2 min)  — publish-body parity, approval gate, flag, no-innerHTML CI rule
```

**Total: 664 passing.** Report the per-group counts in your final summary.

**All four counts were re-measured on the dev box at the `fbe370f` target**
(145 / 252 / 102 / 165). Two notes so an earlier revision of this guide does not
mislead you:

- Group 4 is **165**, not the 166 quoted before the `fbe370f` retarget. That
  number was arithmetic (165 + "the one test `a8ead66` adds"), and it
  double-counted: the file `a8ead66` touched, `tests/test_publish_body_parity.py`,
  reports **28 passed** and the group sums to 28 + 69 + 53 + 15 = **165**.
  Measured, not derived. **If you see 166, report it; do not assume this line is
  the stale one.**
- Groups 2 and 3 were re-run specifically because `fbe370f` modifies files they
  cover (`chat_tools/registry.py`, `chat_tools/enablement_tools.py`,
  `llm/claude_tools.py`, `mcp/chat_mcp_server.py`, `pages/enablement/page.py`).
  Both are unchanged at 252 and 102 — the Asana tools are additive.

**`fbe370f` brings its own group**, which is not folded into the four above
because it is not part of the Zendesk block. Run it separately — see §12.J:

```bash
QT_QPA_PLATFORM=offscreen <venv-python> -m pytest tests/test_asana_board_management.py -q
# expect: 43 passed   (~15 s)   — board summary, ownership links, removal gates
```

Optional extra group — the help-text corpus. It is **known-red for reasons unrelated to this port**; include it only if you want the coverage, and expect exactly these three failures:

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

1. Splash appears; **Database integrity reads `Schema v55`**. `v50` here = the migrator raised and was swallowed → §7, then §16 R2.
2. App reaches the Home page with no error dialog. Existing surfaces (Calendar, Workbench, Chat) look and behave exactly as before — **this port changes none of them visually.**
3. Enablement → **Zendesk tab opens as the classic native Qt tab** (`enablement.web_tabs` is still `off`, and that is correct at this point). Its former "Push to Zendesk" button is now **"Copy for Zendesk"** plus **"Mark as copied"**, with a read-only notice label. That change is the point of `2cc0089` — it is not a bug.
4. Help tab opens → **63 articles** in the ToC. The Zendesk and Zendesk-macros articles no longer claim the app can push to Zendesk. A count below 63 means the assets tree did not land.
5. Guru / Workbench → open any draft's **Review changes** and confirm the preview renders. This exercises the `publish_body()` cluster from `45aab99`. Do **not** publish yet — see §13.2 first.
6. Settings → **Sources → Asana** now shows real state, not the old mockup: either your mapped board(s) with derived counts, or the plain empty state saying no board is mapped. **This is the first time this panel has ever been clicked in a running app** — read §12.J before touching its toggles or its Remove button.
7. **Do not flip any flag yet.** Finish acceptance (§17) with everything dormant.

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
git reset --mixed FETCH_HEAD
git status --porcelain
git log --oneline -3
```

**Why `reset --mixed FETCH_HEAD` and not `checkout`.** `git fetch origin <branch>`
moves `FETCH_HEAD` and the remote-tracking ref **only** — it does not move your
local branch. So a `git checkout enablement-content-tabs` here is a **no-op**
when the Mac already has that local branch at the `d27eb3e` baseline (the
expected state per P1): HEAD would stay at the baseline and `git status` would
show all 96 files as changes, looking like a catastrophic failure at the very
last step of a successful port. And if no local branch exists, the DWIM
checkout has to replace 45 untracked files and can abort outright.

`git reset --mixed FETCH_HEAD` moves the branch pointer **and the index** to the
fetched tip **without touching a single working-tree byte**. That is exactly
right here: §4.5 already proved the bytes are correct, so this step is pure
bookkeeping — it tells git what you already know.

**Expect:** `git status` is **clean apart from pre-existing Mac-local strays**
and anything gitignored; `data/` never appears. Every one of the 96 files is now
tracked at the branch tip — including `src/data/app_paths.py`, which used to
appear here as a `??` line and no longer does. `git log` tip matches the zip's
commit (the branch tip you downloaded in §3).

**If `git status` is NOT clean — if it lists modified tracked files under `src/`,
`web/` or `tests/` — STOP.** The tree is not identical to the snapshot and the
post-sync compare in 4.5 missed something. Do not force anything, do not commit,
and do not `git checkout -- .`: report the list. (Untracked `??` strays that were
already on the Mac before the port are fine and expected.)

Leave the `pre-zendesk-port-2026-07-28` branch in place — it is the audit trail. **Never force-push anything from the Mac.**

---

## 11. Phase 9 — The do-not-touch list

Things you will see and should leave exactly as they are.

| What you'll see | Why it stays |
|---|---|
| No Versions / History / Rollback UI anywhere, despite migration 053 running and `zendesk_versions.py` being present | **Phase 1 is deliberately dormant.** `VersionsPanel.jsx`, `HistoryPanel.jsx`, `versionShape.js` and `versions.css` are imported by nothing except their own test file, and the tip bundle contains no "Versions"/"Rollback" strings. The Python side is *not* dormant — the 053 post-hook imports `zendesk_versions`, so the module is load-bearing for the migration to run at all. Answer to "where is the version history": **not shipped yet**, not broken on macOS. |
| `migrations/052_solver_ledger.sql` missing; `schema_migrations` runs 051 → 053 → 054 → 055 with no 052 | Correct and safe (§7). Do not create a placeholder. |
| An Asana board in Settings showing **"0 imported tasks"** while the calendar clearly has that board's tasks on it | Expected on a database with pre-054/055 rows: attribution exists only from the first poll *after* the migrations. Inert, never deleted, always shown — §12.J, rough edge 1. Do not "repair" it by hand. |
| The classic Zendesk tab's attributes are still called `_a_push` / `_m_push`, and its signals `article_push` / `macro_push` | Deliberately kept so host wiring and existing assertions keep resolving. They now mean "mark handled locally". Renaming them breaks `tests/test_zendesk_content.py`. |
| `publish_article_draft` / `publish_macro_draft` still take a `zendesk_client=` kwarg and ignore it | Source compatibility, on purpose. They are local bookkeeping now and report `remote_write: False`. |
| Grep hits for `create_confirm_write`, `_emit_confirm_write`, `_enqueue_write`, `execute_write`, `cancel_write`, `update_article_draft`, `update_macro_draft` | **Unrelated names.** They are not Zendesk writes. Do not "clean them up". |
| `test_export_asks_where_to_put_the_file_and_cancelling_writes_nothing` (in `tests/test_help_claims_create.py`) failing | Expected — `app_paths.py` ships as of `1aa0ef2`, and this test's expected string predates it (§8). |
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
| `migrations/054_task_board_source.sql` | A (32 lines) | `enablement_tasks.board_source_id` + its index — immutable provenance, **never the authority** | `asana_monitor` writes it; nothing reads it for attribution (**§12.J**) |
| `migrations/055_task_board_links.sql` | A (70 lines) | `task_board_links` (PK `(task_id, board_source_id)`, `ON DELETE CASCADE`, index on board) + a backfill of one link per non-NULL `board_source_id` | `asana_setup`, `enablement_tasks`, the Settings Sources panel (**§12.J**) |
| `src/data/zendesk_import.py` | A (429) | File import (API-shaped JSON / HTML / doc_reader), per-file reports, hostile-input hardened (50 MB cap, 255-char titles); `pull_mirror` GET-only paged pull | the web controller and the classic tab |
| `src/data/zendesk_versions.py` | A (284) | Version rows + `seed_draft_baselines` | **the 053 post-hook — must be present before the migration runs** |
| `src/data/zendesk_store.py` | M (+967/−102) | The whole mirror API. **Never `INSERT OR REPLACE` into mirror tables** — grep-guarded by `tests/test_zendesk_mirror_schema.py` | everything |
| `src/updater/schema_migrator.py` | M (+25) | Registers both Zendesk post-hooks in `_POST_HOOKS` (054 and 055 need none) | §7 |

051 and 053 are **re-runnable**, which is what makes §16 R2 a real repair lever: every `ADD COLUMN` in 051 is single-line so the migrator's `PRAGMA table_info` guard catches all 21; every `CREATE TABLE`/`INDEX`/`VIRTUAL TABLE` is `IF NOT EXISTS`; the six triggers are `DROP … IF EXISTS` + `CREATE`; the FTS backfill is a `'delete-all'` followed by a full re-index. 053 is two `CREATE TABLE IF NOT EXISTS` and one index. 054 and 055 are re-runnable on the same principles (single-line `ADD COLUMN`, `IF NOT EXISTS`, `INSERT OR IGNORE` against a primary key).

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
- `web/src/zendesk/` [A, 21 files] — the SPA. A pure renderer: no server, no localhost, one `file://` bundle, no network from the page, no secrets.

**QWebChannel is the trust boundary** — any page script can call any slot, so no slot carries authority. Reads return viewmodels; side-effectful actions validate against Python-held state plus a single-winner claim plus a **native** confirm (`QMessageBox`, unreachable from Chromium). Never `dangerouslySetInnerHTML` / `innerHTML` in `web/src/` — `tests/test_web_guardrails.py` enforces it in the §8 group 4.

`src/ui/pages/enablement/page.py` [M, **+673/−16 across the whole range** — `_make_zendesk` from `1ba6fa5`, plus the Asana board handlers from `fbe370f`, §12.J] wires it in `_make_zendesk`, with the native `ZendeskPage` as the **construction-failure fallback**: any import or construction error in the web branch yields the native tab, not a crash.

### 12.D — The Guru half (do NOT treat this range as Zendesk-only)

`45aab99` + `ed6482b` touch eleven Python files and one JSX file well outside `src/data/zendesk_*`, and they fix two **confirmed criticals**: attaching a quiz published *only* the quiz, overwriting live cards; and approval surfaces rendering markdown while publish sent a separate HTML column. Skipping any of these re-introduces both.

`enablement_store.py` (+311, `publish_body()` — the single definition of the published body) · `agent_chat.py` (+808, the publish gate, fingerprints, `record_approval`/`revoke_approval`) · `chat_bridge.py` (+237, `PublishConfirmHost`, `build_publish_confirm_dialog`, `_publish_field`) · `guru_content_pipeline.py` · `html_markdown.py` · `artifact_tools.py` · `enablement_web.py` · `guru_page.py` · `guru_preview.py` · `workbench.py` · `expand_overlay.py` · `web/src/chat/ChatApp.jsx` (+238).

**`src/ui/main_window.py` is a 10-line change that is easy to miss and fails closed.** It injects `confirm_host=PublishConfirmHost(self)` into `AgentChatController`. **Without that injection the controller refuses to publish Guru cards at all.** The Mac symptom would be "Guru publish does nothing" with no obvious cause. The §6 smoke checks `PublishConfirmHost` imports; the `git status` count in 4.5 is what proves `main_window.py` actually changed.

`ed6482b`'s fix: a model-written draft `title` (from `revise_draft` → `update_draft_content`) was interpolated unchecked into the publish confirm's 5-field destination pane, which joins fields with newlines — a title carrying newlines injected fake field lines and pushed the real Destination and card_id below the pane's fold. Every field value is now collapsed to one line and capped before the join. **Python-side only.** The bundle delta in that commit is pure Vite re-minify churn (2,099 bytes of variable renaming) — do not let it convince you the fix is in the bundle.

### 12.E — Sanitizer and dialog hardening

`src/data/html_sanitize.py` [M, +1,174/−7] gains an **additive preview profile** so pulled articles render with their real classes inside the sandboxed iframe. **The strict `sanitize_html` profile guarding stored content and the clipboard is untouched.** In `45aab99` the preview CSS policy inverted to a provably-safe **allowlist**: anything that could hide, shrink, move or obscure content is dropped and reported, so an unknown future property is dropped by default.

`src/ui/pages/enablement/_common.py` [M, +31/−1] adds `force_plain_text(dialog)`, applied via `style_native_dialog`. Subtle cause: `QLabel`/`QMessageBox` default to `Qt::AutoText`, and `Qt::mightBeRichText` **only scans up to the first newline** — exactly where interpolated content names sit. A name beginning `<!--` swallowed the rest of the dialog's text.

### 12.F — Renn's tool surface

Three dispatch paths all carry the same tool definitions and all state that Zendesk is read-only: `src/llm/claude_tools.py` (+274/−23), `src/mcp/chat_mcp_server.py` (+211/−14), `src/data/chat_tools/enablement_tools.py` (+277/−87), registered via `src/data/chat_tools/registry.py` (+42/−5). **Those four totals are for the whole range — they also carry the two new Asana board tools from `fbe370f` (§12.J), which are not Zendesk tools.** The new Zendesk family lives in `src/data/chat_tools/zendesk_mirror_tools.py` [A, 537]: **propose-only.** Tools create `pending` drafts with mandatory rationale and sources; a specialist reviews the word-diff, marks ready, copies exact content, pastes into real Zendesk by hand, marks copied. Lifecycle `pending → ready → copied`; `copied` and `pushed` are immutable. **No tool can reach a Zendesk write, because there are none left to reach** (§12.B).

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
| Splash says `Schema v50` (or anything below `v55`) | Migrator raised; `db_manager` swallowed it | Read the `alma.db` log for `Schema migration skipped:` → §16 R2 |
| Settings → Sources shows an Asana board with **0 imported tasks** | Pre-054/055 rows carry no attribution — expected, not data loss | §12.J, rough edge 1. Those tasks are never deleted and stay on the calendar. |
| Calendar shows nothing although a board is mapped and enabled | The **old mockup** symptom, which this range fixes — if it persists, the board's `calendar` flag is off (it defaults off for a newly mapped board) | Turn that board's Calendar toggle on in Settings → Sources (§12.J) |
| `test_zendesk_readonly_guard` fails | A stale `zendesk_client.py` survived | §12.B. **Remove the write, never the guard.** |
| Copy button produces no clipboard content and no dialog | Working as designed — no dialog means no write | Not a defect (§10, smoke 5) |

---

### 12.I — The Settings UI: preserve the bedrock, add one card

**The Zendesk block changes exactly ONE existing Settings surface, and the rule
is preservation, not redesign.** `src/ui/widgets/credentials_panel.py` gains a
Zendesk card (+98 lines) and `tests/test_credentials_panel.py` gains its
coverage (+113). Nothing else in the panel moves. (The `fbe370f` retarget adds a
second Settings surface — the **Sources → Asana** panel, `settings.py` — which
is a separate file, a separate subsystem and a separate section: **§12.J**. The
two do not touch each other.)

**Why it is in scope at all:** before this, the only writer of Zendesk
credentials in the entire app was the **product-mode** Source Monitor
connection tab (`src/ui/pages/source_monitor/connection_tab.py`). Changing a
Zendesk setting meant switching app modes. The card closes that gap.

**Strict guidelines — the bedrock UI is not yours to touch:**

1. **`CredentialsPanel` is SHARED by two hosts.** Enablement Settings builds
   it with `sections=("llm","external")`; the product Settings page builds it
   with `sections=("external",)`. The Zendesk card lives in `"external"`, so
   it appears in **both**. That is intended. **Never** "tidy" the panel by
   scoping it to one host — you would silently remove it from the other.
2. **Do not reorder, restyle or refactor the existing cards.** Guru, Google
   and the LLM card are bedrock. The new card is inserted **between Guru and
   Google** and copies `_guru_card()`'s structure verbatim (`self._card()`
   frame, `QVBoxLayout(20,18,20,18)` spacing 10, `_field_label`, `_line_edit`,
   a status + `_primary_btn` row). If your merge produces any diff in the Guru
   or Google card bodies, the merge is wrong — **STOP** and re-copy the file
   whole from `$SRC`.
3. **Do not touch `src/ui/pages/source_monitor/connection_tab.py`.** It still
   owns `view_id` and the TRC-field mapping for ticket ingestion. The new card
   deliberately does **not** offer those fields.
4. **Do not "simplify" the three save guards.** They exist because each one is
   a way this form destroys data, and each is regression-tested:
   - the stored `view_id` is read and **passed back through**
     `save_credentials(..., view_id=view_id)`, so saving from Settings cannot
     wipe the Source Monitor's ingestion setting;
   - the token field is **never populated** with the stored secret, so a blank
     token means *keep the stored one*, never *erase it*;
   - a missing field **saves nothing at all** and names what is missing
     inline, rather than half-writing a credential set that then reports as
     connected.
5. **No new storage path.** The card reuses
   `ZendeskClient.save_credentials` / `load_credentials` unchanged. `pat_store`
   and `zendesk_client.py` are untouched by it. The API token continues to go
   to the OS keyring; subdomain / email / view_id to `ui_state.json`.
6. **It adds no write capability.** `tests/test_zendesk_readonly_guard.py`
   must stay green with this file in tree — entering credentials is not the
   same as being able to use them for a write, and the guard proves it.

**Verify after the sync** (offscreen; both hosts must build):

```bash
QT_QPA_PLATFORM=offscreen <venv-python> -m pytest   tests/test_credentials_panel.py tests/test_credentials_fields_persist.py   tests/test_zendesk_readonly_guard.py -q
```

Expect green, including the nine `test_zendesk_*` cases. **In the app:**
Settings → Credentials shows a **Zendesk** card between Guru and Google, with
Subdomain / Email / API token and the scope line *"Read-only. Used to pull
Help Center content; the app never writes to Zendesk."* The token box is
**empty** even when a token is stored — that is correct, not a bug.

---

### 12.J — Asana board management: the Sources panel was a mockup

**This is the one part of the range that is not about Zendesk or Guru, and it is
the one part that changes what the app *does* with data it already has.** It
arrives whole in `fbe370f` — 14 files, two migrations (**054**, **055**), and
948 lines of new test.

#### What was broken

`src/ui/pages/enablement/settings.py::_asana()` **was a hardcoded mockup, and it
shipped.** The header count "2 configured", the board names "Enablement
Requests" and "Launch Coordination", every field pill, and the literal
`Resolved: J. Rivera, M. Chen, A. Osei (+4)` were **string literals in the
widget**. "+ Add board" and both toggles were connected to nothing.

The consequence was not cosmetic. Settings reported two configured boards while
`monitor_sources` held **none** and `enablement_tasks` held **zero rows**, so the
Calendar rendered empty — and the panel was the surface an operator would check
to find out why. Two further things made it hard to see:

- **Renn's task counts came from a different place.** Renn calls the Asana API
  live and would happily report ~270 tasks; the Calendar renders **local** rows.
  A confident number from the model was not evidence that anything had synced.
- **Setting an ACTIVE board and MAPPING a board were always two separate
  writes**, and only the first ever happened. "I picked my board" was true and
  still left nothing to poll.

#### What it is now

The panel is a **pure renderer** over `asana_setup.board_summary(conn)`. Nothing
in it is a literal:

- one row per **configured** board, read from `monitor_sources`;
- **derived counts** — everything linked to the board, how many of those another
  board also tracks, and *what a removal would actually delete*;
- the indicator field and the priority / assignee mappings shown **by name**;
- a per-board **SYNC** toggle and a per-board **CALENDAR** toggle. Calendar
  defaults **OFF** for a newly mapped board, so mapping a board fills the task
  list without silently repainting the operator's calendar;
- an **empty state that says the true thing** — no board is mapped, so nothing
  will sync — instead of drawing two boards that do not exist.

#### Ownership is many-to-many, and it took two rounds to get right

Read this before you touch the Remove button; it is the reason the model looks
heavier than "one board owns one task".

1. **Round 1 — attribution derived from the permalink.** A task's
   `permalink_url` names its **home project**, not the board that polled it, and
   Asana teams multi-home tasks constantly. Removing board A therefore deleted
   board B's tasks, **unrecoverably**: B's `modified_since` cursor had already
   advanced past them, so re-polling would not bring them back.
2. **Migration 054** closed that by storing the board whose poll *created* the
   row (`enablement_tasks.board_source_id`) — a true fact, and one column.
3. **Round 2 — one column cannot hold the shape.** An Asana **library** custom
   field carries **one gid org-wide**, so two mapped boards legitimately resolve
   the same indicator and both poll the same multi-homed task (in one cycle it
   comes back as *created* to one board and *updated* to the other). Ownership
   fell to whichever `source_id` sorted first, and removing that board deleted a
   row the other board was still actively tracking.
4. **Migration 055** makes co-ownership representable: `task_board_links`, one
   row per (task, board that demonstrably tracks it), written at **both** poll
   sites in `asana_monitor` — `_create_task_from_asana` **and**
   `_reconcile_existing_task`. Removal drops **this board's claim** and deletes
   the task row **only when no other board still holds one**.

`enablement_tasks.board_source_id` survives as **immutable provenance** and is
**never the authority** — there is a test pinning exactly that. Nothing derives
attribution from `source_url` any more.

#### Two asymmetries, deliberately pointing opposite ways

| Rule | Behaviour | Why |
|---|---|---|
| **DELETION FAILS CLOSED** | A task dies in a board removal only when **every** link pointing at it belongs to the board being removed. Rows with no link at all — pre-054/055, manual, demo, non-Asana — survive **every** removal. | The worst case is a stray row an operator can dismiss by hand. The alternative is silent, unrecoverable loss of another board's work. A **pre-055 database degrades closed for free**: no links means nothing is deletable. |
| **DISPLAY FAILS OPEN** | A task is hidden from the Calendar only when **every** linked board is a configured board with Calendar off. Unlinked rows, rows linked to an unmapped board, and every row when no board is mapped stay visible. | Hiding on ambiguity is precisely what produced the empty calendar. A filter must never be the reason the operator's calendar is blank. |

#### Removal is local-only and structurally cannot reach Asana

`asana_setup.remove_board()` performs three `DELETE`s inside one `atomic()` —
this board's links, the tasks nothing else claims, the `monitor_sources` row —
and it **holds no Asana client, no HTTP verb and no gid it could write back
with**. The tests assert it directly: a spy client records **zero calls** across
a full removal, and `AsanaClient` still has no delete-shaped method. Map the
board again, re-poll, and the same tasks come back from Asana untouched.

**Authority sits in a native dialog, not in Renn and not in the panel.**
`EnablementPage._confirm_and_remove_board` raises a `QMessageBox` that names the
board, quotes **`deletable_task_count`** (what removal *actually* deletes — never
the larger "everything this board tracks" number), says out loud that Asana is
not modified, defaults to **No**, and refuses outright in all four broken-confirm
modes: **absent**, **None**, **raising**, **cancelled**. No GUI thread → no
confirm → nothing is deleted.

#### Renn's two new tools — on every dispatch path

Both are declared in `llm/claude_tools.py`, `mcp/chat_mcp_server.py` and
`chat_tools/enablement_tools.py`, and registered in `chat_tools/registry.py`
— the same three-path pattern as §12.F.

| Tool | What it does |
|---|---|
| `list_asana_boards` | **Read-only.** The honest answer to "is Asana set up". `is_asana_connected()` only proves a key *string* exists — it says nothing about whether any board is mapped, and Renn used to answer from that plus a live API call, which is how "Asana is connected" coexisted with a database that had nothing to poll. |
| `remove_asana_board` | Returns a **PROPOSAL**. It deletes nothing. The operator's click on the native confirm is what removes anything. |

#### Verification

```bash
QT_QPA_PLATFORM=offscreen <venv-python> -m pytest tests/test_asana_board_management.py -q
# expect: 43 passed
```

Optional adjunct — the help article changed with the code
(`assets/help/settings/connect-asana.md`), and its claims are asserted:

```bash
QT_QPA_PLATFORM=offscreen <venv-python> -m pytest tests/test_help_claims_settings.py -q
# dev box: 119 passed
```

**In the app:** Settings → Sources → Asana shows either your real mapped
board(s) with derived counts, or the plain empty state. It must **never** again
show "Enablement Requests" or "Launch Coordination" unless those are boards you
actually mapped.

#### Two rough edges — expected, and neither is a defect

1. **Rows imported BEFORE these migrations carry NULL attribution.** 055's
   backfill only links rows that already had a non-NULL `board_source_id`; it
   invents nothing from a permalink, because that inference is what both
   data-loss rounds died on. So a board may honestly read **"0 imported tasks"**
   until those tasks are re-polled and re-created. Those rows are **inert**:
   never deleted by any removal (fails closed), always shown on the Calendar
   (fails open). Do not hand-repair the database.
2. **Nobody has clicked any of this in a running app.** No toggle has been
   flipped in the UI, and no board has been mapped against live Asana. Every bit
   of verification behind `fbe370f` is headless — offscreen Qt and fake Asana
   clients. **You are the first live test.** Treat the first board mapping and
   especially the first Remove as a UAT: prefer a scratch board, and read the
   confirm dialog's numbers before clicking Yes.

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
| `asana_api_key` | **macOS Keychain** (`pat_store._SECRET_KEYS`) | absent | The Asana panel (§12.J) reads existing state either way. **Board mappings are not a settings key at all** — they live in the `monitor_sources` table, so the port neither reads nor writes them. |

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

**Where the credential UI is.** As of `d018107` there is a **Zendesk card in
Settings → Credentials** (§12.I), in the shared panel, so it appears in **both**
enablement and product Settings — subdomain, email, API token. The older entry
point, **product mode → Source Monitor → Connection tab**, still exists and still
owns `view_id` and the TRC-field mapping for ticket ingestion; the new card
deliberately does not offer those and passes the stored `view_id` back through
untouched. Either one writes the same keyring entry.

---

## 14. Known oddities riding along in this range

Things the sync will visibly land that are not defects. Report, do not fix.

- `docs/ZENDESK_GURU_IMPLEMENTATION_NOTES.md` arrives new and its "Not yet done" section is honest about this port's own gaps — see §15.
- `CLAUDE.md` grows an 83-line Zendesk section. It is documentation for future development sessions; it changes no behavior.
- `src/data/INDEX.md` (+50) and `src/services/INDEX.md` (+13) are API reference files, regenerated by hand.
- The five `assets/help/*.md` changes are **not optional cosmetics.** `tests/test_help_claims_create.py`, `test_help_claims_troubleshooting.py`, `test_help_claims_reference.py` and `test_help_claims_settings.py` assert their exact claims, and they feed the in-app Help Center corpus. Skipping them would leave the Mac's Help articles telling the user the app can push to Zendesk when it structurally cannot.
- `web/src/zendesk/demo.js` (391 lines) is a fixture, not dead code — it is what §9's headless probe renders.
- `fabb4be` shows up in the history as a 5-file cosmetic refactor you never handle individually. Expected.
- **`docs/ZENDESK_BLOCK_PORT_GUIDE.md` — this file — is itself one of the 96 paths.** It landed in `24c8d0f` and has been revised several times since (most recently `d2ae828`, and again for the `fbe370f` retarget). Seeing it in the compare and in `git status` is correct, not a sign of a wrong snapshot (§4.1).
- **`assets/help/settings/connect-asana.md` changes with `fbe370f`** and, like the other help articles, is not optional cosmetics — `tests/test_help_claims_settings.py` asserts its claims (§12.J).
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
8. **The Asana board flow (`fbe370f`, §12.J) has never been exercised in a running app, on any platform, and never against live Asana.** No toggle has been clicked in the UI, no board has been mapped live, and no removal has been confirmed by a human. All 43 of its tests are headless — offscreen Qt widgets and fake Asana clients — which proves the wiring and the ownership rules, not the lived behaviour. **The operator is the first live test.** The safety properties that matter most here (removal is local-only, deletion fails closed) are the ones with the strongest structural proof; the ones with the least evidence are the ordinary ones — that the toggles persist and the counts read right on a real board.

---

## 16. Rollback

Ordered least- to most-destructive. Note the asymmetry: **code rollback is cheap; schema rollback is a restore.** In almost every case the answer is R0.

**The ladder below is written for the Zendesk block (051 / 053) and is unchanged
by the `fbe370f` retarget.** Migrations **054** and **055** need no rung of their
own: they carry no post-hooks, so there is nothing for R2 to repair; they add one
nullable column and one table, which baseline code never reads, so like 051/053
they are inert after a code rollback; and R3 restores them away with everything
else. The only mechanical adjustment: R1's `rm -f` line may also name
`migrations/054_task_board_source.sql` and `migrations/055_task_board_links.sql`
— and, exactly as R1 already says of 051/053, deleting the files does not undo
the schema.

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

1. **Baseline confirmed** — Phase 0 P1 showed `d27eb3e` (or a docs-only descendant), P2 showed all four pre-051 tables and max migration `050` (no `051`/`053`/`054`/`055`).
2. **Backups exist** — `~/alma_port_backup_2026-07-28/` contains `pre_sync_tree.tgz`, `settings.yaml`, `pre_sync_compare.txt`, `post_sync_compare.txt`; `data/local_warehouse.pre051.db` exists; the `pre-zendesk-port-2026-07-28` branch exists (or the tar is explicitly the sole rollback).
3. **Environment gates green** — G1 `arm64 16384`, G2 `FTS5 OK`, G3 PySide6 quartet imports, G4 `web_diag` exit 0.
4. **Sync is byte-identical** — 4.5 shows **zero** "differ" and **zero** "Only in `$SRC`" lines; `git status --porcelain -uall | wc -l` = **96** (45 `??` + 51 ` M`), with any shortfall confined to `docs/`, and **no** `??` naming `052_solver_ledger.sql`, `docs_backfill.py`, `solver_ledger.py`, `docs/pilot/` or `wheel_tail.bin`.
5. **Integrity gate G5 green** — `dist/index.html` is exactly **333,921 bytes**, `qwebchannel.js` present, `__almaZendeskMounted` in the bundle, `051`, `053`, `054` and `055` present, **`052` absent**.
6. **No dependency work happened** — §5's three `diff`s all printed `identical`; no `pip install`, no `npm install`, no `npm build` was run.
7. **All import smokes passed** — including `ALL IMPORT SMOKES PASSED`, the read-only structural assertions, and `src.data.app_paths` (which shipped in the zip — it was **not** hand-created).
8. **Migrations applied via the migrator** — `schema_migrations` tail is `[…049, 050, 051_zendesk_mirror.sql, 053_zendesk_versions.sql, 054_task_board_source.sql, 055_task_board_links.sql]`; 6 Zendesk tables and 6 triggers present; 4 new `zendesk_articles` columns; `enablement_tasks.board_source_id` and `task_board_links` present; the import+FTS smoke returned `imported: 1` with a **non-empty** `fts` list.
9. **Test gates** — the four groups reported **145 / 252 / 102 / 165 = 664 passing**, with per-group counts recorded. `tests/test_zendesk_readonly_guard.py` **passed**. The fifth run, §12.J's `tests/test_asana_board_management.py`, reported **43 passed**.
10. **Headless SPA probe** — `mounted=True`, `innerText_len` > 200, `exit=0`.
11. **In-app smokes** — splash reads `Schema v55`; app reaches Home; existing surfaces unchanged; the classic Zendesk tab shows **"Copy for Zendesk" + "Mark as copied"** with the read-only notice; Help shows **63 articles**; Settings → Sources → Asana shows **real state or the empty state**, never the old mockup boards (§12.J).
12. **Settings untouched** — `diff data/settings.yaml ~/alma_port_backup_2026-07-28/settings.yaml` is empty; `~/.alma-insights/ui_state.json` unchanged. (The warehouse differs by the new tables — that is expected, and it is not "destroyed settings".)
13. **`publish_collection_id` checked and its value reported to the owner** — not `col-1`, not blank; **and not changed by you**.
14. **Phase 8b done** — `git reset --mixed FETCH_HEAD` completed and `git status` is clean apart from pre-existing untracked strays; `git log --oneline -3` tip matches the zip; the rescue branch is still in place; nothing was pushed.

If every line is ✅, the Mac is functionally identical to the Windows dev box at `fbe370f` — with its own settings intact, the entire new Zendesk workspace **dormant behind its own flag**, no credentials required, and the owner holding every switch. Flipping `enablement.web_tabs` to `zendesk` is then a separate, reversible, one-line decision (§13.1), and §16 R0 undoes it in one restart.

---

## Appendix A — Full file manifest (96 files at `fbe370f`, grouped)


**Added after the guide was first written (commit `d018107`) — the Settings
credentials card, §12.I:**

- `src/ui/widgets/credentials_panel.py` [M, +95/−3] — Zendesk card in the
  shared `external` section. Appears in BOTH Settings hosts.
- `tests/test_credentials_panel.py` [M, +113] — nine `test_zendesk_*` cases.

These two are why the delta grew from 85 files / 43 modified to **87 / 45**.
They are expected in the compare and are **not** unreviewed code.

**Added by the `fbe370f` retarget — Asana board management,
§12.J.** Fourteen files, of which **nine are new paths in this manifest** (the
other five — `chat_tools/enablement_tools.py`, `chat_tools/registry.py`,
`llm/claude_tools.py`, `mcp/chat_mcp_server.py`, `pages/enablement/page.py` —
were already `M` rows and simply grew):

- `migrations/054_task_board_source.sql` [A, 32] · `migrations/055_task_board_links.sql` [A, 70]
- `src/data/asana_setup.py` [M, +325/−3] · `src/data/asana_monitor.py` [M, +43/−3] · `src/data/enablement_tasks.py` [M, +83/−3]
- `src/ui/pages/enablement/settings.py` [M, +143/−25] — the panel that was a mockup
- `tests/test_asana_board_management.py` [A, 948 — **43 tests**] · `tests/test_help_claims_settings.py` [M, +43/−15]
- `assets/help/settings/connect-asana.md` [M, +27/−12]

That is what takes the delta from **87 / 42 A / 45 M** to **96 / 45 A / 51 M**.

This is a **VERIFICATION checklist** — it is what the Phase-2 pre-sync compare should show. It is **NOT** a list of manual copies (rule 9). **A** = added, copies wholesale, nothing to reconcile. **M** = modified, the Mac copy may have diverged.

**Everything is in this manifest.** There is no longer an off-manifest hand-created file: `src/data/app_paths.py` is listed under `src/data` below, and it ships. Totals: **45 A + 51 M = 96**.

### migrations — 4 files, all A

`051_zendesk_mirror.sql` (A, 137) · `053_zendesk_versions.sql` (A, 59) · `054_task_board_source.sql` (A, 32) · `055_task_board_links.sql` (A, 70) — **no `052`** (§7)

### root — 1 M

`main.py` (+9/−0 — the fenced `ensure_docs_tree()` call from `30fb086`; not load-bearing, §4.2)

### src/data — 4 A, 14 M

**A:** `zendesk_import.py` (429) · `zendesk_versions.py` (284) · `chat_tools/zendesk_mirror_tools.py` (537) · **`app_paths.py` (124 — new in `1aa0ef2`; §4.2)**
**M:** `html_sanitize.py` (+1174/−7) · `zendesk_store.py` (+967/−102) · `enablement_store.py` (+311/−18) · `chat_tools/enablement_tools.py` (+277/−87) · `asana_setup.py` (+325/−3 — §12.J) · `zendesk_client.py` (+116/−74) · `enablement_tasks.py` (+83/−3 — §12.J) · `chat_tools/registry.py` (+42/−5) · `asana_monitor.py` (+43/−3 — §12.J, both poll sites write links) · `enablement_sim.py` (+27/−19) · `guru_content_pipeline.py` (+23/−8) · `chat_tools/artifact_tools.py` (+12/−2) · `html_markdown.py` (+6/−1) · `INDEX.md` (+50)

### src/services — 1 A, 3 M

**A:** `zendesk_web.py` (2,242 lines / 112 KB — the largest new file, holds all authority)
**M:** `agent_chat.py` (+808/−36) · `enablement_web.py` (+51/−26) · `INDEX.md` (+13)

### src/ui — 1 A, 13 M

**A:** `web/zendesk_bridge.py` (185)
**M:** `pages/enablement/page.py` (+673/−16) · `web/chat_bridge.py` (+237/−6) · `pages/enablement/settings.py` (+143/−25 — §12.J) · `widgets/credentials_panel.py` (+98/−3 — §12.I) · `pages/enablement/zendesk_tab.py` (+93/−19) · `pages/enablement/_common.py` (+31/−1) · `web/web_flags.py` (+24/−2) · `pages/enablement/workbench.py` (+19/−4) · `pages/enablement/guru_preview.py` (+13/−7) · `pages/guru_page.py` (+12/−2) · **`main_window.py` (+10/−1 — fails closed, §12.D)** · `pages/enablement/expand_overlay.py` (+9/−3) · `web/dist/index.html` (241,310 → **333,921 bytes**)

### src/llm, src/mcp, src/updater — 3 M

`llm/claude_tools.py` (+274/−23) · `mcp/chat_mcp_server.py` (+211/−14) · `updater/schema_migrator.py` (+25/−0)

### web/src — 22 A, 2 M

**A (`web/src/zendesk/`):** `ZendeskApp.jsx` (609) · `garden.css` (623) · `demo.js` (391) · `shape.js` (333) · `RevisionCenter.jsx` (247) · `RevisionDiff.jsx` (207) · `ArticleEditor.jsx` (201) · `ArticleBody.jsx` (197) · `VersionsPanel.jsx` (184) · `HistoryPanel.jsx` (145) · `MacroEditor.jsx` (130) · `GardenChrome.jsx` (130) · `ArticleList.jsx` (125) · `MacroList.jsx` (99) · `CopyControls.jsx` (92) · `versionShape.js` (81) · `versions.css` (80) · `BodyEditForm.jsx` (35) · `zendesk.test.jsx` (1,461) · `shape.test.js` (428) · `versions.test.jsx` (437)
**A (other):** `web/src/chat/chat.test.jsx` (136)
**M:** `web/src/App.jsx` (+3 — route registration only) · `web/src/chat/ChatApp.jsx` (+238/−6)

### tests — 11 A, 9 M

**A:** `test_zendesk_bridge.py` (4,161) · `test_chat_review_panel.py` (1,540) · `test_zendesk_web_tab.py` (815) · `test_zendesk_mirror_tools.py` (773) · `test_zendesk_import.py` (763) · `test_publish_body_parity.py` (**585** — 500 at `4ccd321`, +85 in `a8ead66`; 28 tests) · `test_zendesk_versions.py` (567) · `test_zendesk_readonly_guard.py` (505) · `test_zendesk_mirror_schema.py` (499) · `test_zendesk_client_readonly.py` (253) · **`test_asana_board_management.py` (948 — 43 tests, §12.J)**
**M:** `test_zendesk_content.py` (+210/−41) · `test_help_claims_create.py` (+140/−146) · `test_credentials_panel.py` (+113 — §12.I) · `test_workbench_bridge.py` (+66/−5) · `test_enablement_web_flag.py` (+43) · `test_help_claims_settings.py` (+43/−15 — §12.J) · `test_help_claims_troubleshooting.py` (+39/−6) · `test_help_claims_reference.py` (+6/−6) · `test_web_guardrails.py` (+5)

### docs, help, CLAUDE.md — 2 A, 6 M

**A:** `docs/ZENDESK_GURU_IMPLEMENTATION_NOTES.md` (159) · **`docs/ZENDESK_BLOCK_PORT_GUIDE.md` (this file — new in `24c8d0f`)**
**M:** `CLAUDE.md` (+83/−4) · `assets/help/create/zendesk.md` (+74/−35) · `assets/help/create/zendesk-macros.md` (+38/−26) · `assets/help/settings/connect-asana.md` (+27/−12 — §12.J) · `assets/help/reference/settings-keys.md` (+7/−6) · `assets/help/troubleshooting/connect-first.md` (+7/−1)

**Addendum.** If the zip was taken from the branch tip rather than pinned at `fbe370f`, it may contain further docs-only descendants pushed after the retarget. The pre-sync compare will show those paths beyond the 96 above — that is the basis for the structural authentication in §4.1. **Anything beyond Appendix A that is not under `docs/` is unexplained → STOP.**
