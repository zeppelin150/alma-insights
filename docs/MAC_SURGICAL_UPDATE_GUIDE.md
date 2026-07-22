# Mac Surgical Update Guide — `7adbe7b` → `dbb5c8f` (enablement-content-tabs)

**Written:** 2026-07-22 on the Windows dev box, from the actual git delta.
**For:** work Claude (Sonnet) on the work Mac (Apple Silicon M1, 16 GB, arm64).
**Scope:** every change committed between `7adbe7b` (2026-07-02, "docs(guide): clarify standalone-bundle vs git-checkout for QtWebEngine") and `dbb5c8f` (2026-07-22, "feat(enablement): Content Command Center — branding, system tabs, Renn usage metering"), all pushed to `origin/enablement-content-tabs`.

**The delta:** 27 commits, 295 files (206 added, 89 modified, **zero deleted** — a copy-over sync is complete), +60,225 / −3,220 lines. 8 new SQL migrations (043–050). 1 new Python dependency (`pypdf==6.14.2`). ~150 new/changed source files, 73 test files, 63 Help-Center articles, 5 prompt templates, 2 asset templates (one binary).

**Transfer mechanism (the owner's decision — do not substitute another):** the code arrives as a **GitHub source zip** of this branch, downloaded by the owner, inflated on the Mac, compared locally against the existing tree, then synced file-over-file. There is **no `git fetch` / `pull` / `checkout` in this flow**, even if the Mac checkout has a configured remote — git on the Mac (if present) is used only for a rescue snapshot and as an audit trail, never to move code. Phase 2 has the exact commands. Target commit: `989905897359e4502b1d1684f180479f152ae0cd` (= `dbb5c8f` + the 2026-07-22 follow-up commits: snyk-workflow CI repairs, NLP-stack parking + markdown/PyJWT bumps, text_diff.diff_words restoration, scan_server npm-audit fix, CI action SHA-pinning, and installer/docs currency — see the Appendix A addendum).

This guide supersedes (but does not delete) two narrower prior guides that ship inside this very range:
- `docs/THREE_PILLAR_SURGICAL_PATCH_GUIDE.md` — covers only `7adbe7b..870fade` (the first ~3 feature commits). Its **Section 0 hard invariants remain binding**.
- `docs/MAC_STYLE_GUIDE_TEMPLATE_GUIDE.md` — covers only commit `870fade`.

If a previous session already hand-applied either guide on this Mac, Phase 0 below detects that and tells you what to do. **Do not re-apply their hunks by hand under any circumstances — this guide's git-based mechanics replace all hand-patching.**

**Companion document:** [`MAC_SURGICAL_UPDATE_REFERENCE.md`](MAC_SURGICAL_UPDATE_REFERENCE.md) — a per-file dossier (232 entries: what changed, exact names/keys/signatures, dependencies, macOS risks, proving command for every changed file). It is a LOOKUP TABLE for when a gate or test here fails — open it to diagnose, never as a list of things to do. Execute from THIS file only.

---

## 0. Absolute rules — read before touching anything

These override anything else you infer. Violating any one of them is the failure mode this guide exists to prevent.

1. **NEVER touch `data/`.** The Mac's `data/settings.yaml`, `data/local_warehouse.db`, and everything else under `data/` are the settings this update must not destroy. All of `data/` is gitignored and therefore **was never committed — the downloaded zip contains no `data/` directory at all**, and the Phase-2 sync command carries no `--delete`, so the sync physically cannot touch the Mac's settings. Do not edit, "fix", add keys to, or reformat `data/settings.yaml`, even if a feature seems to need a key. Every new feature in this range **fails closed / defaults off** when its key is absent (that is designed, see §13). If something needs a key, report it to the owner; do not set it.
2. **NEVER pass `--delete` (or `--delete-*`) to any `rsync` involving the project tree** — the zip has no `data/`, no `.git`, and no gitignored local files, so a deleting sync would destroy exactly the things rule 1 protects. And if the Mac tree is a git repo: **never `git clean -x` / `git clean -dfx`** (`-x` deletes gitignored files = the settings, the warehouse, the local test files).
3. **NEVER click "Install & Restart" / "Install Now"** on any update prompt (splash or Settings). The auto-updater's protected set is only `{data, _update_staging, .git, .venv, venv, docs, installer, tests}` — an install **replaces `src/`, `web/`, `config/`, `scripts/`, `main.py`, `requirements*`** with a release zip and desyncs the checkout. The startup update check runs by default (`updates.auth_mode` defaults to `"pat"`), so you WILL see prompts. Dismiss them. Do not "fix" this by editing settings (rule 1).
4. **NEVER run `npm build` / `npm --prefix web run build`.** The React app ships as a committed build artifact: `src/ui/web/dist/index.html` (241,310 bytes at `dbb5c8f`, single-file bundle) + `src/ui/web/dist/qwebchannel.js`. The Mac needs **no Node/npm at all** to run the app. Rebuilding wipes and regenerates `dist/` with non-identical bytes.
5. **NEVER run `scripts/sign_qtwebengine_dev.sh`** and never manually `codesign` anything under the pip-installed PySide6. The script is a preserved historical dead end — its premise was disproven and running it **corrupts QtWebEngineCore.framework**. It self-guards (exits 3); leave the guard intact. The pip PySide6 wheel renders fine unsigned. (`installer/ci/sign_macos.sh` is CI packaging machinery only — never run it against a dev venv either.)
6. **NEVER run the full test suite** (`pytest tests/`) — it hangs. Run committed tests in the exact groups in §8. WebEngine round-trip tests run **singly** (one pytest invocation per file) or they stack to exit 255.
7. **NEVER "fix" shipped quirks.** Several things in this range look like bugs and are deliberate; changing them diverges the Mac from Windows and breaks pinned tests. The complete list is in §11. Highlights: the bug-for-bug timestamp string-sort duplicated in `home_web.py`; the rowid-only FTS delete triggers in migration 046; the duplicated (not imported) CCC strings in `splash_window.py`; the bounded `{0,400}` regexes in `chat_engine.py`; `resolve_tool_evidence` having no session filter; `xfail(strict=True)` tests in the help-claims suites that MUST stay red.
8. **Port order is not negotiable where stated.** With the whole-tree sync in Phase 2, ordering is automatic (every file lands in one rsync pass, and the delta contains no deletions). Ordering only matters if you are ever forced to move files piecemeal — in that case §12's per-subsystem "hard import chains" are mandatory reading first. And do NOT substitute git mechanics for the zip flow: no fetch, no pull, no checkout, no cherry-pick.
9. **When something is unclear or a gate fails, STOP and report.** Do not improvise around a failed gate. Every gate in this guide has a "what it means if it fails" note.

---

## 1. What you are porting — feature map by commit

Read this so you know what "done" looks like. Newest last.

| Commit | Date | What it is |
|---|---|---|
| `1b5f951` | 07-14 | **Three-pillar build**: live Asana calendar (60s events sync, gated writes), self-building Drive KB ("EC" folder, FTS5 cards, pull-wins sync), content studio (diagrams/decks/quizzes/docs as tracked artifacts). Migrations 043–047. |
| `413e8fd` | 07-14 | Test isolation for dev-machine state (identity/credentials fixtures). |
| `870fade` | 07-14 | Style-guide rendered preview/editor + card/article template ([CARD-TEMPLATE] tagged doc, `enablement.card_template_doc_id`). |
| `9afd56f`,`fa7e266` | 07-14 | The prior (narrower) surgical patch guides — docs only. |
| `ff430fe` | 07-20 | Asana pagination: all list calls follow `next_page.offset` to exhaustion; stale-offset 400 returns partials, never crashes. |
| `8c1e80c` | 07-20 | `list_active_trcs` date-injectable (`now=` kwarg) — defuses a wall-clock time-bomb in tests. |
| `2a9a9ba` | 07-20 | ~27 audit fixes: correctness & safety sweep (demo-mode Guru suppression, ingest dedupe, doc_reader strict mode, attention dismissal, sync status ordering, redaction fail-closed, shared chat session, and more). |
| `34545cd` | 07-20 | **The big one (+leftover debug file)**: React web tabs (Calendar/Workbench behind `enablement.web_tabs`), web Home (`ui.web_home`), in-app Help Center (62 articles + FTS + honesty statuses), `WebHost` shared view host, `html_sanitize`, web guardrail tests. Migration 048. Also accidentally committed the `$LOG` junk file (see §14). |
| `aba4337` | 07-20 | Offline Drive-search evaluation harness (`tests/drive_eval/`, scorer, gold set, docs). |
| `39b8816` | 07-21 | **Drive search fix (0%→93% recall)**: migration 050 FTS + tokenized IDF-weighted ranking (`enablement_doc_search.py`), live Drive tools (`search_google_drive`, `search_everywhere`, `import_drive_doc`). |
| `7ce570a`..`b5d241a` | 07-21 | **Updater chain (5 commits)**: private-repo release-asset auth (Bearer + API asset URLs), token on splash install path, reload `src` after staged apply (kills reinstall loop), `restart_requested()` flag (kills double-window), close splash on restart, VERSION → 1.0.7. |
| `38b4c20`,`0cd75ab` | 07-21 | CI: commit `web/package-lock.json` (+ `.gitignore` exception), build web UI on Node 24, vitest step. |
| `6100625` | 07-22 | **Anti-fabrication guardrails**: Layer 1 unexecuted-tool-call detector in `chat_engine`; Layer 2 `turn_grounding.py` TEL-SG evidence ledger (warn-only, telemetry default). |
| `3794111` | 07-22 | `google_access.py`: auth_type-aware "is Drive reachable" gate (service accounts no longer read as disconnected) + SA status line in credentials panel. |
| `0ff816d` | 07-22 | **Renn persona fix**: persona delivered as real CLI `--system-prompt-file` + neutral cwd (blocks CLAUDE.md cwd-injection) + per-MCP-server `PYTHONPATH` anchor. |
| `4313f53` | 07-22 | Native Drive folder picker dialog + Settings "Browse Drive…", recursive scoped search, blank-native-dialog-button fix (`style_native_dialog`). |
| `ca750a3` | 07-22 | Help corpus **sync-when-behind** (the 8-vs-62-articles field bug). |
| `7ab1752` | 07-22 | Help-claims honesty pass (docs match shipped behavior; xfail-pinned contradictions). |
| `beb80ce` | 07-22 | Task detail header no longer clips its buttons (own-row Mark complete). |
| `dbb5c8f` | 07-22 | **Content Command Center**: branding (`src/branding.py`, splash/chrome/Home banner), 7-icon-tab enablement Settings, shared Updates/Maintenance panels, `RennUsagePanel` + real per-turn CLI usage metering (`renn_usage.py`, no migration). |

---

## 2. Phase 0 — Inspect the Mac baseline (do this FIRST)

You are not assuming the Mac's state — you are observing it. The sync in Phase 2 is identical regardless of what you find; what changes is how you SNAPSHOT before it and how you read the pre-sync compare.

```bash
cd <the alma-insights checkout>             # find it; do not guess paths in commands below
ls data/settings.yaml                        # MUST exist — this is the tree whose settings we protect
git rev-parse --is-inside-work-tree 2>/dev/null   # "true" → it's a git repo; error → it isn't
git rev-parse HEAD 2>/dev/null; git log --oneline -3 2>/dev/null    # note where it thinks it is
git status --porcelain 2>/dev/null           # non-empty → hand-applied edits exist
ls src/data/kb/ 2>/dev/null                  # kb/ present on an old HEAD = prior hand-port evidence
# Base-era modules the new KB imports but this delta doesn't ship (they predate 7adbe7b) — all must exist:
ls src/data/agent_jobs.py src/data/text_diff.py src/data/enablement_identity.py src/data/html_markdown.py src/data/enablement_checks.py
```

**If it IS a git repo:** snapshot everything before any file changes — this is your rollback and your audit baseline:
```bash
git checkout -b pre-port-rescue-2026-07-22
git add -A                                   # captures untracked hand-copies too
git commit -m "rescue: Mac state before zip sync to dbb5c8f"
```
Nothing can now be lost. Note: after the Phase-2 sync, `git status` will show the whole delta as modified/untracked **against the old HEAD** — that is expected and useful (it IS the audit trail of exactly what the sync changed). Do not "fix" it with fetch/pull/checkout (rule 8). If `git log` shows local commits whose subjects match nothing in §1's feature map, STOP and report — the Mac has its own unported work and the owner must decide.

**If it is NOT a git repo:** the Phase-1 tar snapshot is your only rollback — do not skip it.

**Either way:** a prior hand-applied port may have already run migrations 043–047 and may have flipped keys in the Mac's `data/settings.yaml` for testing (e.g. `demo_mode`). Migrations are idempotent and self-recording, so first launch is safe regardless. Settings deltas: OBSERVE and report (`grep -nE "demo_mode|kb:|web_tabs|web_home|cli_path" data/settings.yaml` — read-only!), never revert them yourself. If the base-era modules check above shows missing files, the tree is OLDER than `7adbe7b` — the sync still lands canonical content for every tracked path, but say so in the report.

### Getting the zip (the owner does the download)
Preferred URL pins the exact commit, so a later push to the branch can't race the download:
```
https://github.com/zeppelin150/alma-insights/archive/989905897359e4502b1d1684f180479f152ae0cd.zip
```
The branch form (`…/archive/refs/heads/enablement-content-tabs.zip`) is acceptable if downloaded before anything else lands on the branch — Phase 2 authenticates the snapshot either way.

What a GitHub source zip is (why this flow is safe): the full tree at one commit, **no `.git` directory, no gitignored files (therefore no `data/`)**, LF line endings preserved exactly as committed, binaries byte-exact, and the zip's archive comment is stamped with the full commit SHA. Inflate with **command-line `unzip`, not by double-clicking** (Archive Utility propagates the browser's quarantine xattr onto every extracted file).
---

## 3. Phase 1 — Pre-flight backups & environment gates

Do all of this BEFORE changing a single file. Each gate has a hard pass condition.

### Backups (cheap insurance, even though git can't touch `data/`)
```bash
mkdir -p ~/alma_port_backup_2026-07-22
cp data/settings.yaml ~/alma_port_backup_2026-07-22/
cp data/local_warehouse.db ~/alma_port_backup_2026-07-22/          # may be large; that's fine
cp data/local_warehouse.db-wal ~/alma_port_backup_2026-07-22/ 2>/dev/null || true
git rev-parse HEAD > ~/alma_port_backup_2026-07-22/baseline_commit.txt 2>/dev/null || true
git status --porcelain > ~/alma_port_backup_2026-07-22/baseline_status.txt 2>/dev/null || true
# Whole-tree rollback snapshot (essential when the tree is NOT a git repo; cheap insurance when it is):
tar czf ~/alma_port_backup_2026-07-22/pre_sync_tree.tgz \
    --exclude='.venv' --exclude='venv' --exclude='data' --exclude='.git' --exclude='__pycache__' \
    -C "$(dirname "$PWD")" "$(basename "$PWD")"
```
(`data/` is excluded from the tar only because it was already copied file-by-file above.)

### Gate G1 — native arm64 Python (the #1 historical Mac killer)
```bash
<venv-python> -c "import platform, mmap; print(platform.machine(), mmap.PAGESIZE)"
```
**PASS:** `arm64 16384`. **FAIL:** `x86_64` or page size `4096` = the venv runs under Rosetta → QtWebEngineProcess dies with a page-size mismatch (QTBUG-98487) and every web surface renders blank no matter what you port. Fix the venv (native arm64 Python) before anything else.

### Gate G2 — SQLite has FTS5
```bash
<venv-python> -c "import sqlite3; sqlite3.connect(':memory:').execute(\"CREATE VIRTUAL TABLE t USING fts5(x)\"); print('FTS5 ok')"
```
**PASS:** prints `FTS5 ok`. **FAIL:** migrations 046/048/050 will raise at first launch (the migrator halts with `RuntimeError`, earlier migrations stay applied — recoverable, but fix the Python build first). Some pyenv/system builds lack FTS5; the python.org and Homebrew builds have it.

### Gate G3 — full PySide6 quartet present and importable
```bash
<venv-python> -c "import PySide6.QtWebEngineWidgets; print('QtWebEngine ok')"
pip show PySide6 PySide6-Essentials PySide6-Addons shiboken6 | grep -E "Name|Version"
```
**PASS:** import succeeds; all four packages present. QtWebEngine lives in **PySide6-Addons** — an Essentials-only install renders blank. **NEW in this range:** `main.py` now eagerly imports `QtWebEngineWidgets` at every boot (required init-order fix), so a broken WebEngine install now bites at startup even with all web flags off (import-only — no renderer spawns; it degrades via try/except, but a corrupted framework can still crash the dylib load). If this gate fails after files are updated, the recovery is: `pip install --force-reinstall --no-deps PySide6 PySide6-Essentials PySide6-Addons shiboken6`, then delete any leftover `_CodeSignature` directories under the PySide6 tree, and if quarantined: `xattr -rd com.apple.quarantine <site-packages>/PySide6`.

### Gate G4 — claude CLI reachable (needed for live Renn only, not for tests)
```bash
which claude && claude --version
```
**PASS:** resolves and version ≥ 2.1.216 (needs `--system-prompt-file` and `--strict-mcp-config`). **FAIL is non-blocking for the port** — no committed test spawns the CLI — but live Renn chat, task briefs, and KB summarization silently degrade without it. Note: GUI-launched apps (Finder/Dock) get a minimal PATH that excludes `~/.local/bin`; launch the app from a terminal, or the Mac's settings must already carry `claude.cli_path` (if missing, surface to the owner — rule 1 forbids you setting it).

### Record versions for the report
```bash
sw_vers; <venv-python> --version; pip freeze | grep -iE "pyside6|shiboken|pypdf|python-pptx|google-api|psutil"
```

---

## 4. Phase 2 — Inflate, compare, sync

### 4.1 Inflate and authenticate the zip
```bash
mkdir -p ~/alma_port_incoming
unzip -z  <path-to-zip>                      # AUTHENTICATE FIRST: GitHub stamps the commit SHA as the
                                             # archive comment — it MUST print 989905897359e4502b1d1684f180479f152ae0cd
unzip -q  <path-to-zip> -d ~/alma_port_incoming
SRC=$(ls -d ~/alma_port_incoming/alma-insights-*)   # GitHub top dir: <repo>-<branch or sha>
xattr -rd com.apple.quarantine "$SRC" 2>/dev/null || true   # belt-and-braces quarantine strip
find "$SRC/assets/help" -name '*.md' | wc -l        # expect 63 — quick inflation sanity
```
If `unzip -z` prints a different SHA, STOP — with one sanctioned exception: if this guide arrived **inside** the zip (`$SRC/docs/MAC_SURGICAL_UPDATE_GUIDE.md` exists), the snapshot is a docs-only descendant of `dbb5c8f` and the comment SHA will legitimately differ. In that case authenticate structurally instead: the pre-sync compare (4.2) must show nothing beyond Appendix A **including its addendum** plus `docs/MAC_SURGICAL_UPDATE_GUIDE.md` / `docs/MAC_SURGICAL_UPDATE_REFERENCE.md` (and G5 + the Phase-4 `VERSION == 1.0.7` check must pass). Anything else unexplained in the compare = wrong snapshot, STOP. If `unzip -z` prints nothing (some tools strip comments), use the same structural fallback.

### 4.2 Pre-sync compare (the audit moment — never skip)
```bash
DEST=<the Mac checkout>
diff -rq "$SRC" "$DEST" \
  --exclude data --exclude .git --exclude .venv --exclude venv --exclude __pycache__ \
  --exclude .pytest_cache --exclude .claude --exclude node_modules --exclude .DS_Store \
  | sort > ~/alma_port_backup_2026-07-22/pre_sync_compare.txt
wc -l ~/alma_port_backup_2026-07-22/pre_sync_compare.txt
```
How to read it:
- **"Files … differ" + "Only in $SRC"** lines are the delta about to be applied. On a clean July-2 baseline they correspond to Appendix A (89 differ + 206 only-in-SRC = 295). MORE than the manifest = baseline drift (hand-patched or older tree) — every such tracked path is HEALED by the sync (the zip is canonical `dbb5c8f`), but paste the unexplained ones into your report.
- **"Only in $DEST"** lines are Mac-local files the sync will NOT touch — expected for gitignored things (local `test_*_local.py`, logs, scratch, caches). **Exception to inspect: any DEST-only `.py` under `src/` or `web/src/`** is a stray hand-copy that can SHADOW imports after the sync — report these to the owner; do not delete on your own.

### 4.3 Sync — one pass; the delta has zero deletions, so copy-over is complete
```bash
rsync -a "$SRC"/ "$DEST"/        # NO --delete. EVER. (rule 2)  Trailing slashes are load-bearing.
```
This cannot touch `data/`: the zip contains no `data/` directory and nothing is deleted. **Do not cherry-pick, do not hand-apply hunks, do not copy files one at a time** — the per-file detail in §12 and the dossier exists for verification and debugging, not as a to-do list of manual copies.

### 4.4 Post-sync compare (the byte-identity proof — this replaces `git rev-parse` in a zip flow)
Re-run the exact `diff -rq` from 4.2 into `post_sync_compare.txt`.
**PASS = zero "differ" lines and zero "Only in $SRC" lines.** ("Only in $DEST" strays remain by design.) The Mac's tracked tree is now byte-identical to `dbb5c8f`. If the tree is a git repo, `git status` against the old HEAD now lists the applied delta — save it: `git status --porcelain > ~/alma_port_backup_2026-07-22/applied_delta_git_view.txt`.

### Gate G5 — integrity spot-checks (binary fidelity + junk awareness)
```bash
# The committed web bundle — must be byte-exact:
python3 -c "import os; s=os.path.getsize('src/ui/web/dist/index.html'); print(s); assert s==241310, 'dist bundle size mismatch'"
ls -la src/ui/web/dist/qwebchannel.js       # must exist (unchanged in this range, but required)
# The binary deck template — must open as a real pptx:
<venv-python> -c "from pptx import Presentation; Presentation('assets/templates/renn_deck.pptx'); print('pptx ok')"
# Help corpus count:
find assets/help -name '*.md' | wc -l       # expect 63
# The Home watchdog contract must be inside the bundle exactly once:
grep -c __almaHomeMounted src/ui/web/dist/index.html      # expect 1
# Redaction is now FAIL-CLOSED — a missing/unreadable patterns file turns every
# Claude API call into a hard RedactionError (the one LOUD failure in this range):
<venv-python> -c "import json; json.load(open('config/redaction_patterns.json', encoding='utf-8')); print('redaction config ok')"
# Known junk file that rides along (harmless; see §14):
ls | grep -F '$LOG' || true
```

---

## 5. Phase 3 — Python dependencies

Dependency changes in this range (updated 2026-07-22 after the NLP-stack parking):

```bash
pip install pypdf==6.14.2 markdown==3.8.1 "PyJWT[crypto]==2.13.0"
# optional but recommended (only for scripts/measure_web_rss.py numbers):
pip install psutil
```

**Removed from requirements (do NOT install):** `nltk` and `sentence-transformers`
(with their torch/transformers closure) — the product NLP lane is parked while
focus is enablement; the vulnerability scan is clean because of it. If the Mac
venv already has them installed, that is harmless (the app simply no longer
requires them); optionally `pip uninstall nltk sentence-transformers transformers
torch` to reclaim ~2 GB on the M1. The re-add checklist lives as a comment in
`requirements.txt`. Product-mode Trending/θ-sentiment degrade by design — an
error dialog / neutral sentiment is expected behavior, not a port bug.

Notes:
- `pypdf` is pure Python — no arm64 wheel risk. **Its absence is SILENT**: `drive_reader`'s PDF branch swallows the ImportError and extracts `''`, so KB cards from Drive PDFs become metadata-only with zero errors. Verify with `pip show pypdf`, never with runtime behavior.
- Do **not** run `pip install --require-hashes -r requirements.lock` — the `pypdf` entry in the lock deliberately ships **without hashes** (comment says they're to be filled by an M1 `pip-compile` run). A hash-checked install fails until that happens. Plain `pip install pypdf==6.14.2` (or `pip install -r requirements.txt`) is correct.
- Everything else (`python-pptx==1.0.2`, PyYAML, google-api-python-client, keyring, …) was already required at the base commit — confirm presence with `pip show`, don't reinstall.

---

## 6. Phase 4 — Import smokes (catch a partial port before Qt ever starts)

Each of these catches a specific cross-module break. Run all; every one must print its OK.

```bash
<venv-python> - <<'EOF'
import importlib, sys
checks = [
    # (module, why)
    ("src", "package init"),
    ("src.services.turn_grounding", "NEW module chat_engine hard-imports at top"),
    ("src.services.chat_engine", "breaks ALL chat app-wide if turn_grounding missing"),
    ("src.data.kb", "KB package"),
    ("src.data.help", "Help package"),
    ("src.data.enablement_monitor", "raises AttributeError here if asana_monitor lacks tasks_updated"),
    ("src.branding", "CCC strings"),
    ("src.ui.pages.home_page", "module-level branding import"),
    ("src.ui.web.web_flags", "HARD import of enablement page even with flags off"),
    ("src.ui.web.web_host", "agent_page hard-imports it — existing Agent chat breaks without it"),
    ("src.services.home_web", "imports branding + app_modes at module top"),
    ("src.updater.restart", "main.py now hard-imports restart_requested on the splash path"),
    ("src.ui.main_window", "the whole shell"),
]
for mod, why in checks:
    importlib.import_module(mod); print(f"ok  {mod}")
from src import VERSION; assert VERSION == "1.0.7", VERSION; print("ok  VERSION 1.0.7")
from src.updater.restart import restart_requested; assert restart_requested() is False; print("ok  restart flag")
from src.data.chat_tools.registry import _ensure_registered; _ensure_registered(); print("ok  tool registry (atomic 6-file check)")
from src.updater.update_checker import normalize_github_repo as n
assert n("https://github.com/o/r/tree/main") == "o/r" and n("git@github.com:o/r.git") == "o/r"; print("ok  repo normalizer")
import inspect; from src.updater.updater import Updater
assert "token" in inspect.signature(Updater.stage).parameters; print("ok  stage(token=)")
from src.data.mermaid_lint import lint; assert lint("flowchart TD\nA-->B")[0]; print("ok  mermaid lint")
from src.data.artifact_store import slugify; assert slugify("A: B/c?") == "a-b-c"; print("ok  slugify")
print("ALL IMPORT SMOKES PASSED")
EOF
```

**If `_ensure_registered()` raises ImportError:** the tool-family files did not land atomically (`registry.py`, `enablement_tools.py`, `artifact_tools.py`, `kb_tools.py`, `help_tools.py`, `chat_mcp_server.py`). With a completed whole-tree sync this cannot happen; if it does, the sync did not finish — re-run Phase 2 steps 4.3 and 4.4 and demand a clean post-sync compare before continuing.

**If `src.data.enablement_monitor` raises AttributeError:** `asana_monitor.py` is stale (missing the `tasks_updated` signal) — same diagnosis: the tree is not at `dbb5c8f`.

---

## 7. Phase 5 — First launch & migrations 043–050

Migrations auto-apply at startup: `db_manager.initialize()` → `SchemaMigrator().migrate(conn)`. Each file runs and **commits individually in filename order**; tracking table is `schema_migrations` (filename PK). On any failure the migrator raises `RuntimeError` and halts — already-applied files stay applied; the failed file re-runs on the next launch (all 8 new files are idempotent: `CREATE TABLE IF NOT EXISTS`, PRAGMA-guarded `ALTER ADD COLUMN`, `DROP TRIGGER IF EXISTS`-then-create).

**Before launching**, check where the Mac warehouse stands (read-only):
```bash
sqlite3 data/local_warehouse.db "SELECT filename FROM schema_migrations ORDER BY filename DESC LIMIT 3;" 2>/dev/null || echo "no tracking table yet (older base) — fine"
```
Whatever it says, the migrator applies every missing file in order (a warehouse behind 042 just gets a longer list).

**Launch from a terminal** (so the claude CLI is on PATH and you see logs):
```bash
cd <checkout> && <venv-python> main.py
```
Watch stderr/log for `Applied migration: 043_...` … `050_...` lines and **no RuntimeError**. The splash must show **v1.0.7** (not 1.0.0 — if it shows 1.0.0, `src/__init__.py` didn't land or an old process is running). If an update prompt appears: **dismiss it** (rule 3). Then quit the app.

**Post-launch schema verification (read-only):**
```bash
sqlite3 data/local_warehouse.db <<'SQL'
SELECT 'migs', COUNT(*) FROM schema_migrations WHERE filename >= '043';
PRAGMA table_info(enablement_tasks);
SQL
```
Expect: 8 rows counted; `enablement_tasks` columns include `remote_modified_at`, `brief_json`, `brief_status`, `brief_source_modified_at`. Spot-check tables exist: `.schema asana_task_extras`, `.schema kb_cards` (must include `extra_json`), `.schema kb_queue`, `.schema enablement_artifacts`, `.schema help_articles`, `.schema enablement_documents_fts`.

**What does NOT happen at boot (by design — don't chase it):** the Help corpus is *not* loaded at startup (lazy: Help tab open or first `help_search` tool call); the KB worker does not tick (`enablement.kb.enabled` defaults false, `enablement.demo_mode` defaults true); no Drive/Asana network runs unless the Mac's own settings already enable them.
---

## 8. Phase 6 — Test gates

**Preamble before EVERY group** (macOS zombie cleanup — the Windows `wmic`/`taskkill` commands in CLAUDE.md do not exist here):
```bash
pkill -9 -f alma_mcp_server; pkill -9 -f chat_mcp_server; pkill -9 -x gemini; pkill -9 -f 'scan_server/server.js'
# do NOT `pkill -9 node` blindly — match the server.js path; blind node-kill takes out unrelated tooling
export QT_QPA_PLATFORM=offscreen
```

Run groups of 3–4 files with `python -m pytest <files> -x -q`. **Never the whole `tests/` dir** (rule 6). The groups below cover every test file in this range in dependency order; G17–G20 (the ten `test_help_claims_*` suites) run **last** — they import ~30 real modules and `inspect.getsource`/ast-parse live source, so they are the loudest possible "incomplete port" alarm.

```
G1  test_html_sanitize.py test_web_guardrails.py test_web_diag.py
G2  test_help_store.py test_help_search.py test_help_corpus_sync.py
G3  test_help_tab.py test_app_modes.py test_home_page.py test_splash.py
G4  test_home_bridge.py test_home_web_controller.py test_enablement_web_flag.py
G5  test_calendar_bridge.py test_workbench_bridge.py
G6  test_claude_cli_bridge.py test_claude_cli_bridge_mcp.py test_claude_cli_system_prompt.py test_redaction_fail_closed.py
G7  test_chat_engine.py test_turn_grounding.py test_chat_tools.py
G8  test_shared_session.py test_demo_guru_suppression.py test_renn_usage.py test_scoped_usage_tracker.py
G9  test_asana_pagination.py test_asana_setup_guard.py test_asana_readback.py test_asana_writeback.py
G10 test_drive_query.py test_drive_reader_search.py test_drive_live_search_tools.py test_enablement_doc_search.py
G11 test_drive_eval_scorer.py test_drive_folder_picker.py test_drive_sa_status.py test_google_access_gate.py
G12 test_kb_extra_and_readopt.py test_card_template.py test_doc_reader_unsupported.py test_ingest_dedupe.py
G13 test_system_panels.py test_settings_subtabs.py test_phase5_release.py test_updater.py
G14 test_manifest_fetcher.py test_update_action_widget.py test_native_dialog_styling.py test_task_detail_actions.py
G15 test_attention_dismissal.py test_page_return_reload.py test_sidebar_layout.py test_sync_status.py
G16 test_enablement_live_cutover.py test_enablement_ui_live.py test_source_baseline.py test_stage4_data_warehouse.py
G17 test_help_claims_getting-started.py test_help_claims_renn.py test_help_claims_reference.py
G18 test_help_claims_create.py test_help_claims_insights.py test_help_claims_knowledge-base.py
G19 test_help_claims_plan.py test_help_claims_settings.py
G20 test_help_claims_troubleshooting.py test_help_claims_workbench.py
```

Note: the `test_help_claims_*` filenames contain **hyphens** — always pass them as explicit `tests/<name>.py` paths (they are not importable module names, and shell globs on `$`-adjacent names in this repo root can misbehave thanks to the `$LOG` junk file).

**Then, each in its OWN pytest invocation** (WebEngine round-trips; grouping stacks Chromium teardown to exit 255):
```bash
python -m pytest tests/test_web_chat_drawer.py -q
# plus, ONLY if transferred out-of-band (they are gitignored, so they are NOT in the zip; they survive the sync only if already transferred):
# tests/test_agent_bridge_local.py, test_calendar_web_local.py, test_workbench_web_local.py, test_home_web_local.py — one at a time
```

### How to read the results — expected non-passes that are NOT port bugs
- **`xfail` (strict) in `test_help_claims_*`:** these encode article claims CONTRADICTED by shipped code, on purpose. They must show as `xfailed`. **An `XPASS` is a port defect** (the Mac's code diverged from Windows, or someone "fixed" a pinned quirk) — report it, never delete the marker.
- **Machine-state `skipped`:** `test_help_claims_getting-started` (1 test skips without a real `data/settings.yaml` — on this Mac it exists, so it may RUN; it is read-only), `test_help_claims_knowledge-base` (2 skips: no settings.yaml / operator already bootstrapped KB), `test_help_claims_troubleshooting` (1 skip when `enablement.web_tabs` is enabled locally). Skips here are normal.
- **Pre-existing known failures (outside this range):** `test_reporting_foundation::test_bridge_fallback`; live-DB parts of `test_feature_integration`. Not in the groups above; ignore if encountered elsewhere.
- **No test in this range needs live credentials or a logged-in claude CLI** — everything is hermetic (the two previously env-sensitive files, `test_enablement_live_cutover` / `test_enablement_ui_live`, gained isolation fixtures in this very range so machines WITH credentials also pass). The only credential-dependent item is the manual script `tests/e2e_claude_chat.py`, which is not pytest — skip it unless the owner asks.
- **Two font-sensitive geometry tests:** `test_native_dialog_styling` (luminance spread > 80) and `test_task_detail_actions` (440 px clipping). Both were calibrated on Windows fonts; if one fails on macOS, investigate font metrics first — do NOT modify `style_native_dialog` or the task-detail layout to make a threshold pass.
- Anything else red → a real problem. Diagnose before continuing; §12 maps each test family to its source files.

Report pass/fail counts per group in your final summary.

---

## 9. Phase 7 — Web-stack acceptance (even though all web flags stay OFF)

The web surfaces stay dormant on this Mac (`enablement.web_tabs` defaults `off`, `ui.web_home` defaults false, and rule 1 forbids flipping them). But the **Agent chat page already runs on this stack** (refactored onto `WebHost` in this range), and `main.py` now imports QtWebEngine at every boot — so the stack must be healthy regardless.

```bash
python scripts/web_diag.py                      # gate: 0 FAIL. This is THE first command on any blank-page report, ever.
bash scripts/verify_web_pivot_mac.sh <venv-python>   # gate: "web pivot verification: PASS"
# optional (informational, needs psutil): python scripts/measure_web_rss.py
# ONLY if something renders blank: bash scripts/collect_mac_diag.sh <venv-python>   (read-only fact sheet)
```

Caveats:
- `verify_web_pivot_mac.sh` step 3 runs the four gitignored `test_*_web_local.py` files **only if present** — they are not in the zip, so unless previously transferred they're absent and it silently skips, so a PASS proves less than it looks. If the owner wants real WebEngine round-trip coverage, those four files must be transferred out-of-band from the dev box.
- `web_diag`'s probes encode every known blank-render cause (Rosetta page size, Mach-O arch of QtWebEngineProcess, `_CodeSignature` corruption leftovers, quarantine xattrs, bundle size, env vars). Trust its verdicts over intuition.
- Offscreen screenshots of WebEngine are ALWAYS blank — never use a screenshot to judge a web page in tests; assert via `runJavaScript` hooks (`window.__alma*`).

---

## 10. Phase 8 — In-app smoke checklist (launch from a terminal)

Product mode (or whatever the Mac's own settings boot into — the delta never forces a mode):
1. Splash shows **v1.0.7**; if an update prompt appears, dismiss (rule 3). App reaches Home (native — web Home is off).
2. Product Settings → Updates tab renders (it now mounts the shared `UpdatesPanel`); "Check for Updates" may find a release — **do not install**. Display tab renders (shared `MaintenancePanel`).
3. Gemini chat page: log line contains "text-tool loop" (MCP-off change); a tool-using question answers without fabricated ticket IDs.

Switch to enablement mode (Home → mode tile; confirm dialog is native and its buttons must be legible — that's the `style_native_dialog` fix):
4. Top bar subtitle reads **Content Command Center**; sidebar footer reads `v1.0.7 — Content Command Center · Drive + local search`; the 11-entry sidebar scrolls without clipped label descenders; **Attention Queue** and **Help** entries exist.
5. Help tab opens → **62 articles** in the ToC grouped by 10 sections (opening the tab IS what triggers the lazy corpus sync; count < 62 means the sync-when-behind fix or the assets tree didn't land). Status badges (partial / flag-gated / not-available) render on some articles — that's the honesty mechanism, not an error.
6. Settings tab shows **7 icon tabs**: Connections / Providers / Sources / Style Guide / Updates / Usage / Maintenance. Usage builds its panel on first click (empty tiles are fine — no Renn turns metered yet). Sources shows the "Browse Drive…" button (don't click through to live Drive unless the owner's credentials are already configured; picking a folder WRITES `enablement.drive.active_folders` — owner's call).
7. Task detail (open any task): "Mark complete"/"Reopen" button sits on its own row, fully visible at the 440 px panel width; assignee/submitter wrap instead of clipping.
8. One Renn turn (needs claude CLI logged in — skip if G4 failed, and note it):
   - Renn answers in persona (no "I'm Claude Code" refusal).
   - Tools actually execute: `sqlite3 data/local_warehouse.db "SELECT tool_name, session_id FROM chat_tool_executions ORDER BY rowid DESC LIMIT 5;"` shows fresh rows. **Renn narrating tool calls as text + an empty ledger = the per-MCP-server `PYTHONPATH` anchor failed** — check that the spawned CLI's MCP config carries `PYTHONPATH=<checkout root>` and cwd is `$TMPDIR/alma_cli_neutral`.
   - After the turn, Settings → Usage shows the turn metered (source `renn_chat` in `gemini_usage`; cost may be $0.00 on a subscription CLI login — that's honest, not broken).
9. Quit. Confirm exactly one app process existed throughout (`pgrep -fl "python.*main.py"`) — the restart/double-window fixes are in this range.

**Explicitly owner-gated — do NOT do as part of the port:** flipping `enablement.web_tabs` / `ui.web_home` / `enablement.kb.enabled` / `enablement.demo_mode:false`; KB "Bootstrap EC" (creates real Drive folders + writes settings); configuring `enablement.help.bug_form_url`; any live Guru publish or Asana write; any updater install; running `scripts/run_drive_eval.py` (it WRITES the warehouse by default — if ever run, it must get `--db <scratch>` and a fully synthetic Drive).

---

## 10b. Phase 8b — Reconcile the Mac's git metadata with GitHub (LAST step, after all acceptance passes)

After the sync, the tree CONTENT is byte-identical to the pinned commit but the Mac's git still
points HEAD at the old commit, so `git status` shows the whole delta as local modifications.
Once — and only once — §15's acceptance criteria all pass, bring the metadata in line:

```bash
git -C <checkout> status --porcelain > ~/alma_port_backup_2026-07-22/pre_reconcile_status.txt
git fetch origin enablement-content-tabs
git checkout enablement-content-tabs        # metadata-only here: tracked content already matches,
                                            # so no file changes on disk; HEAD/index now agree with origin
git status --porcelain                       # expect: ONLY untracked gitignored strays (data/ never shows)
git log --oneline -3                         # tip should match the zip's commit
```

Rules for this step: it happens AFTER acceptance, never before (rule 8's no-git-mechanics ban
applies to DELIVERING code, and this step delivers none — if `git checkout` reports it would
overwrite files, STOP: the tree is NOT identical and the post-sync compare was wrong). If the
Mac tree is not a git repo, skip this section entirely. If the rescue branch from Phase 0
exists, leave it in place — it is the audit trail. Never force-push anything from the Mac.

---

## 11. Phase 9 — The do-not-touch list (Sonnet footguns)

Things in this tree that look wrong and must be left exactly as shipped. Committed tests pin most of them; the rest diverge the Mac from Windows if "improved".

| What you'll see | Why it stays |
|---|---|
| `home_web.py` duplicates SQL + formatting from `home_page.py`, including a string-sort on raw timestamps | `src/services` must not import `src/ui`; the sort is bug-for-bug parity. Guard: `test_home_web_controller` parity test. Do not deduplicate; do not fix the sort. |
| Migration 046's `kb_cards_fts` delete triggers use the rowid-only form (unlike 048's full-value form) | Shipped behavior; "fixing" it forks the schema from Windows. Port verbatim. |
| Splash CCC strings duplicated in `splash_window.py` instead of importing `src/branding.py` | Deliberate (splash must never crash over an import); a rename touches both. |
| Bounded regexes `{0,400}` in `chat_engine.py` detector | Anti-quadratic-blowup guards. Do not "simplify" to `*`. |
| `resolve_tool_evidence` has no session filter | Deliberate fail-open (48% of ledger rows are `adhoc_probe`). Adding a filter breaks the design. |
| Tool names containing `background`/`enqueue`/`schedul`/`defer` auto-disable a Tier-A grounding rung | By design — don't rename tools to "fix" it. |
| Retired Asana write impls (`_create_asana_subtask_impl` etc.) still present in `enablement_tools.py` | TaskDetailPanel's direct-click path uses them; only the model-facing registrations were removed. Do not delete. |
| `[SYSTEM: operator confirmed` literal in `agent_chat._notify_renn`; 4096-byte cap in `chat_tools/registry.py` | `turn_grounding` pins both verbatim (CONFIRM_MARKER, TRUNCATION_CAP). Rewording silently breaks the guard; tests pin them. |
| `page.py` calls `_setup_engine()` BEFORE `_build()` in `__init__` | Channel objects register exactly once; reversing kills the web Renn drawer for the page lifetime. |
| `WebHost.__init__` parents orphan bridges before `registerObject` | QWebChannel does NOT own registered objects; a GC'd bridge = native access-violation crash. Never remove the parenting loop; never pass a bridge as an unheld temporary. |
| `HomeApp.jsx` sets `window.__almaHomeMounted = true` | The watchdog contract — losing it makes web Home always fall back to native after 8 s. |
| Heavy docstrings in `source_baseline.py`, `rate_chart.py`, `source_monitor/`, `guru_wip_page.py` | Documented CLAUDE.md override — do not strip on any cleanup pass. |
| `xfail(strict=True)` tests in `test_help_claims_*` | Encode honest contradictions. XPASS = defect; passing them is not the goal. |
| `docs/SESSION_SUMMARY.md` Part 3 describes the macOS blank render as unsolved with open investigation items | **Historical.** It was solved after that doc (Rosetta + framework corruption; the installer/`main.py` fixes in this range are the productized fix). Do not chase its open items. |
| `docs/ENABLEMENT_ROADMAP.md` has a duplicated, mid-sentence-truncated "Built — Enablement web pivot" heading | Merge blemish, cosmetic. Port as-is; flagged for owner cleanup. |
| `scripts/sign_qtwebengine_dev.sh` exists and looks useful | It is a trap with a warning label (rule 5). Exits 3 by design. |
| `tests/test_drive_query.py` asserts via `inspect` that NO production caller injects `live_client` | Intentional tripwire — it should fail the day someone wires that seam. Don't wire it to make the test "more real". |
---

## 12. Subsystem reference

Verification-and-debugging detail per subsystem: what changed, the hard import chains (matter only if files ever move piecemeal), the silent-failure modes (things that "work" while broken), and the proving tests.

**Need more depth than this section gives?** Every file in the delta has a full entry (exact function signatures, settings keys, dependency lists, per-file verify commands) in [`MAC_SURGICAL_UPDATE_REFERENCE.md`](MAC_SURGICAL_UPDATE_REFERENCE.md). Go there when a specific test or smoke fails; stay here for the shape of each subsystem.

### 12.A — Migrations & data layer: KB, Help Center, artifacts, doc search

**Migrations (all idempotent, all auto-applied at launch):**
| File | Creates/alters | Consumed by |
|---|---|---|
| `043_asana_events.sql` | `monitor_sources.events_sync`; `enablement_tasks.remote_modified_at` | asana_monitor events sync; write-back CAS; task_brief staleness |
| `044_asana_task_extras.sql` | `asana_task_extras` table (html_notes, custom_fields/attachments/stories JSON, for_modified_at) FK→enablement_tasks CASCADE | `asana_extras.py`, task briefs, task-detail panel |
| `045_task_brief.sql` | `enablement_tasks.brief_json / brief_status ('pending') / brief_source_modified_at` | `task_brief.py` (failed briefs still stamp — no retry storm) |
| `046_kb.sql` | Whole KB mirror: `kb_folders`, `kb_cards` (+2 idx), `kb_cards_fts` (contentless FTS5 +3 triggers), `kb_sync_state`, `kb_queue` (+ UNIQUE pending partial idx), `kb_sync_log` | everything in `src/data/kb/` |
| `047_enablement_artifacts.sql` | `enablement_artifacts` registry (+3 idx); no CHECK constraints by design (whitelists live in `artifact_store.py`) | `artifact_store.py`, studio tools |
| `048_help_center.sql` | `help_articles` (+2 idx), `help_articles_fts` (contentless, DROP-and-recreate triggers, full-value delete) | `src/data/help/*`, help tab, `help_search` tool |
| `049_kb_extra_fields.sql` | `kb_cards.extra_json` (human frontmatter keys survive round-trip) | `kb/store.py` upsert (NULL = preserve), `kb/sync.py` |
| `050_enablement_documents_fts.sql` | `enablement_documents_fts` (contentless FTS5 + triggers + delete-all back-fill of existing docs) | `enablement_doc_search.py`, `enablement_store.search_documents`, KB search floor |

**New packages/modules:**
- `src/data/kb/` (8 files): `card_format` (YAML-frontmatter card spec v1, tolerant parser, `kb-<uuid8>` ids), `store` (content-hash-skipping upsert, weighted-bm25 `fts_search`), `drive_kb` (the SINGLE Drive write chokepoint — allowlist-enforced `write_card_file` raising `KBWriteDenied`; EC-root bootstrap writes `enablement.kb.ec_folder_id`; topic minting capped at 40 folders), `ingest` (bounded folder indexing, Haiku summarize with deterministic-excerpt fallback, full text into `enablement_documents` as recall floor), `search` (hybrid FTS blend, NO embeddings — owner-locked), `sync` (pull-wins two-way, `_index.md` regen, stale-source rescan, 15-min lease/3-attempt queue), `worker` (`KBWorker`: main-thread QTimer → daemon thread per tick, single-flight latch, fresh connection per run; gates per-tick on `google_access_ready()`; `kb_enabled()` false when `demo_mode` true or `kb.enabled` false — i.e. **off on this Mac**).
- `src/data/help/` (4 files): `loader` (parses `assets/help/**/*.md`, anchored `parents[3]` to repo root; `sync_bundled_help` re-loads whenever on-disk count > DB count — the 8→62 fix; NOT called at boot), `store` (rejects unknown statuses — the honesty mechanism), `search` (tokenized FTS5, no LIKE fallback by design).
- `src/data/artifact_store.py` (registry over mig 047; home of shared `slugify`; `data/artifacts/<id>/` created lazily under project root — a new subdir, never touches existing local files), `quiz_artifacts.py`, `task_brief.py` (+ `BriefWorker`, cap `enablement.asana.brief_per_poll_cap`=5), `llm_gen.py` (generate→validate→retry-once + machine-wide `background_gate` BoundedSemaphore(1) — the M1 16 GB protection), `mermaid_lint.py` (strips `%%{init}%%`/click lines; the only gate between LLM output and stored diagrams), `pptx_reader.py` (pptx→markdown, returns `''` on any failure), `html_sanitize.py` (stdlib allowlist sanitizer, fails closed to `escape()`; every web preview passes through it — CI-enforced).
- Modified: `enablement_store.py` (search rewritten from whole-query LIKE → ranked FTS; card-template as second tagged guide `[CARD-TEMPLATE]` + `enablement.card_template_doc_id`), `enablement_tasks.py` (`_UPDATABLE` + 4 new columns — without this the brief/CAS columns can never be written), `enablement_monitor.py` (starts BriefWorker + KBWorker; **connects `asana.tasks_updated` OUTSIDE try/except** — the AttributeError boot-break if asana_monitor is stale; `start(..., asana_interval_seconds=)`), `doc_reader.py` (`strict=` mode + `UnsupportedDocumentError`), `pptx_store.py` (branded template `assets/templates/renn_deck.pptx` with graceful degrade).

**Silent-failure modes to check, not assume:**
1. FTS5 missing or mig 050 unapplied → ALL document search returns `[]` with zero errors (exceptions swallowed). Proof: `pytest tests/test_enablement_doc_search.py`.
2. `pypdf` missing → Drive PDFs extract `''` → metadata-only KB cards, silently. Proof: `pip show pypdf`.
3. Corrupt/text-transferred `renn_deck.pptx` → decks silently degrade to the unbranded default template. Proof: gate G5's `Presentation()` open.
4. Help corpus never syncs if `help_tab.py` / `help_tools.py` didn't land (sync is lazy in exactly those two callers). Proof: smoke №5 (62 articles).
5. Most deep KB unit tests are **gitignored** (`test_kb_*_local.py` family) and won't exist on the Mac — the committed ceiling is `test_kb_extra_and_readopt.py` + import smokes. A green run does NOT mean the KB sync engine was exercised; live KB work stays owner-gated anyway.

**Prompt/asset dependencies:** `config/prompts/enablement_{task_brief,mermaid,quiz,one_pager,battle_card}.txt` (inline fallbacks exist but produce weaker output), `assets/templates/support_center_article_template.md`, `assets/help/**` (63 md files; loader dedupes by content hash — a CRLF transfer re-upserts noisily but harmlessly).

### 12.B — Google Drive / Asana integration layer

The dependency HUB of the delta — nearly every other subsystem imports something from here. The most portable slice too: pure stdlib urllib / threading / googleapiclient; zero Windows constructs.

**Google side:**
- `google_access.py` (A, 57 lines): THE auth-type-aware answer to "is Drive usable" — `google_access_ready()` delegates to `DriveReader.from_settings().is_configured()` (SA path: `read_enabled` + key file exists; OAuth path: `read_enabled` + session-active). Fixes SA setups reading as permanently disconnected (the old code used `google_oauth.is_active()`, always False for SAs). Never raises, never networks; missing google libs → False (fail-closed, not a port bug). `service_account_email()` is the ONE place allowed to open the key file. Consumers: agent_chat, KB worker per-tick gate, enablement_monitor, folder picker, settings KB card, drive probe worker.
- `drive_reader.py` (M, +282): `search_files` rewritten — pages `nextPageToken` to the limit, **recursive** folder scoping via `list_subtree_folder_ids` (BFS, 250 folders/depth 10 cap, 60 s cache, `_last_scope_truncated` honesty flag), dedupe by file id, retry/backoff on 429/5xx. `list_picker_roots()`: for a service account the ONLY entry points are Shared Drives + `sharedWithMe` folders (why the old picker rendered blank). `.pptx` extraction now routes through `pptx_reader.pptx_to_markdown` (ordering: the `presentationml` branch sits BEFORE the generic officedocument branch). pdf branch lazily imports pypdf (the silent-`''`-on-missing behavior in §12.A).
- `drive_query.py` (M): `build_live_drive_client()` (settings-configured DriveReader or None) + `folder_id` scoping and `scope_truncated` threaded through `query_business_drive`. Note the intentional tripwire test on the `live_client` seam (§11).
- `google_oauth.py` (M): `reconnect()` now serialized by a module lock (KBWorker/monitor/exporter threads raced google-auth's unlocked refresh into transient 401s). Still hard-refuses inside MCP subprocesses (`ALMA_MCP_MODE`).
- `gdrive_export.py` (M, +118): the Drive **write** chokepoint — `_throttled_execute` (0.5 s min interval under lock + exp backoff to 16 s) now wraps every write; new surface `create_folder` / `update_file` / `upload_file(bytes)` / `get_file_meta` (incl. `trashed` — bootstrap re-verify needs it: writes into trash succeed) / `find_child_by_app_property` (idempotency probe). RULE from the code: any Drive write anywhere goes through `_throttled_execute`, never `request.execute()`.

**Asana side:**
- `asana_client.py` (M, +276): GET-only retry policy (429 honors Retry-After capped 30 s; one 5xx retry; **412 NEVER retried** — it is the /events sync-token handshake; POST/PUT never retried). `_paginate` follows `next_page.offset` to exhaustion (projects up to 200 pages — previously silently capped at 100); mid-walk offset expiry returns partials + log, never crashes. **Signature changes:** `list_subtasks` and `list_attachments` LOST their `limit=` kwarg (all in-repo callers verified clean; an out-of-tree caller passing `limit=` gets TypeError). New: `update_task` (generic PUT, returns `modified_at` for CAS restamp), `get_events` (412 handshake → fresh token + `full_resync`), `list_stories` (comments). `_TASK_FIELDS` expanded (html_notes, custom-field values, start_on…).
- `asana_extras.py` (A, 167): side-table store over `asana_task_extras` (mig 044) — html_notes (stored raw, rendered PlainText only — UNTRUSTED), custom fields, attachments, stories. Staleness = `for_modified_at != enablement_tasks.remote_modified_at`; drain capped per poll (`enablement.asana.extras_per_poll_cap`, default 10); story events mark dirty explicitly (stories never bump task modified_at).
- `asana_monitor.py` (M, +373): events-API diff polling **ON by default** (`enablement.asana.use_events` default true) with 412 handshake + paged baseline re-list; per-board degradation to the legacy modified_since poll after 3 consecutive failures; deleted → dismiss (never row-delete); "removed from board" verified via live GET before dismissing; **new Qt signal `tasks_updated`** (the one `EnablementMonitor.__init__` connects OUTSIDE try/except — the boot-break coupling in §12.A); `_reconcile_existing_task` now returns `task_id|None` (was bool), stamps the CAS anchor, and skips the done-flip while a write is in flight.
- `asana_setup.py` (M): `discover()` tags results `mock: True/False` so the setup writer refuses to persist MOCK_DISCOVERY's fabricated GIDs outside demo mode (the writer-side gate is in enablement `settings.py`; both are in this range).
- `asana_writeback.py` (M, +142): WS1-M5 hardening — module in-flight status guard (`is_status_inflight`, consumed by the monitor), CAS precheck on destructive verbs (live `modified_at` vs stored anchor; conflict → `{'ok':False,'conflict':True}` **without** applying locally — a behavior change callers now handle), restamp from the PUT response (also advances `brief_source_modified_at` so our own click doesn't burn a Haiku brief rebuild), new `set_completed_in_asana` with optimistic flip + revert-on-failure.
- `chat_action_requests.py` (M): gate-brick fix — INFORMATIONAL types (`research_plan`, `artifact_preview`) no longer block `has_pending_action`; `create_action_request` supersedes older unresolved same-type rows; `_CONFIRM_WRITE_OPS` gains `asana_task_update` + `upload_artifact_to_drive`. No schema change (mig 038 pre-exists).

**Usage plumbing:** `usage_tracker.py` + `scoped_usage_tracker.py` gain the `cost_usd=` kwarg pair (real CLI-reported cost wins over the Gemini estimate; None keeps old behavior) — the cross-slice coupling with `claude_cli_bridge` in §12.C. `renn_usage.py` (A) detailed in §12.C. `source_baseline.py` (M): test-determinism `now=` kwarg only; production SQL byte-identical; **keeps its deliberate heavy docstrings** (§11).

**Behavior changes on the Mac with ZERO settings edits (expected, tell the owner):**
- If Asana is connected in the Mac's own settings, the first post-port poll (~60 s in) performs the 412 token handshake + up to 50 pages of baseline re-list + extras drain against the real board. One-time re-baseline traffic; not a bug.
- If migration 043 were somehow missing, the events path silently degrades to legacy polling after 3 failures — which is why Phase 5 verifies the schema explicitly instead of trusting "the app works".

Proving groups: G9, G10, G11 (+ G8 for usage). The deepest tests for the events path/extras/gated writes are **gitignored** `*_local.py` files that won't exist on the Mac — a green run proves less than the dev box's did; that's a known coverage ceiling, not a gap you can close.

### 12.C — Chat / LLM / Renn: persona, guardrails, tools, metering

**The Renn persona fix (`0ff816d`)** — three legs, all in `claude_cli_bridge.py` + `claude_cli_subprocess.py` + `claude_cli_client.py` (they land together; git handles that):
1. `set_system_prompt(text)` writes the persona to a temp file (`$TMPDIR/alma_sysprompt_*.txt`, 0600, reused when unchanged, rewritten if macOS purges TMPDIR) and `_build_cmd()` passes `--system-prompt-file <path>`. `ClaudeCliClient._prepare_prompt` now returns a `(user, system)` TUPLE — the `[SYSTEM INSTRUCTIONS]`-embedded-in-user-content convention is gone on the CLI leg (remains correct for Gemini).
2. Neutral cwd: the CLI runs from `$TMPDIR/alma_cli_neutral` so cwd-driven CLAUDE.md ingestion (~14.6K tokens/turn measured) cannot leak repo instructions into Renn.
3. Because of (2), `'-m src.mcp.chat_mcp_server'` can no longer resolve via cwd — `set_mcp_config` injects `env.setdefault('PYTHONPATH', <checkout root via __file__>)` per server. **The macOS failure signature if this breaks: Renn narrates tool calls as text and `chat_tool_executions` stays empty** (smoke №8).

**Anti-fabrication (`6100625`)** — Layer 1: `chat_engine._detect_unexecuted_tool_call` (XML invoke tags / fenced tool_call blocks / `TOOL_CALL:` lines, after html.unescape) discards the turn unless tools genuinely ran (ledger/telemetry), suppress-once anti-livelock (second consecutive detection banners instead of discarding — designed, not a bug). Layer 2: `src/services/turn_grounding.py` (NEW, 1396 lines, pure) — evidence ledger + subject gate, warn-only; `enablement.turn_grounding.mode` defaults **telemetry** (recording starts immediately on the Mac; invisible; `off` only on settings errors), `banner` is opt-in, no block mode exists. `chat_engine` hard-imports it at module top.

**Redaction fail-closed (`2a9a9ba` family):** `claude_client.py` adds `RedactionError`; `_redact_text` now **raises** instead of logging-and-sending-raw when the redaction pipeline itself breaks. Proof: `test_redaction_fail_closed.py`. (Memory note "redaction fails OPEN claude_client.py:83 — HIGH" is stale after this port. The broader redaction-engine workstream stays uncommitted on the dev box by owner decision — this specific fix IS in the range; don't confuse the two.)

**Tool surface changes** (all five files atomic: `registry.py`, `enablement_tools.py`, `artifact_tools.py`(A), `kb_tools.py`(A), `help_tools.py`(A), mirrored in `claude_tools.py` + `chat_mcp_server.py`):
- Added: `search_google_drive` (scope chain: explicit folder_id > scope=all > `enablement.drive.active_folders` recursive > all-visible-with-note), `search_everywhere` (local+live merged, deduped), `import_drive_doc`, `list_artifacts`, `generate_diagram/deck/quiz/doc`, `attach_artifact_to_draft`, `request_upload_artifact_to_drive`, `kb_search`, `kb_list_topics/cards`, `kb_get_card`, `index_drive_folder` (**enqueue-only** — the MCP subprocess never touches Drive; KBWorker executes on its tick), `help_search`.
- Retired from the model surface: `create_asana_subtask`, `post_asana_comment`, `update_asana_due_date` → replaced by the single Confirm-gated `request_asana_task_update` (propose-time validation, CAS via `expected_modified_at`/`expected_due`). MCP returns steering errors for the retired names (`_RETIRED_TOOL_HINTS`), not "Unknown tool".
- `chat_mcp_server.TOOL_SCHEMAS` is triple-purpose (MCP surface / allowed-set / Tier-A drift-guard names) — names must match the registry exactly.
- `fast_path.py`: `list_tickets` date filters actually work now (`date_range` / `date_start`/`date_end` coercion).

**Renn usage metering (`dbb5c8f`):** `src/data/renn_usage.py` (A) — `RennUsageRecorder` writes `gemini_usage` rows with `source='renn_chat'` + real CLI-reported `cost_usd` (None on subscription logins → UI shows $0.00 honestly). **No migration** — the table pre-exists. Hooked in `chat_engine._attach_usage_sink` for `enablement*` task types; `usage_tracker.log_call` gained the `cost_usd` kwarg (without it, bridge usage logging TypeErrors silently into debug logs — another reason the tree must be exactly `dbb5c8f`).

**Sessions:** `chat_session.resolve_or_create_session` — Agent page and Workbench drawer now share ONE session via the `.current_chat_session` pointer file in `data/` (runtime state, not settings; its appearance on first run is normal).

Proving groups: G6, G7, G8 (+ G9 for the Asana write path).

### 12.D — Web layer: React/QtWebEngine tabs, web Home, diagnostics

**Runtime truth:** the SPA ships as the committed single-file bundle `src/ui/web/dist/index.html` (vite-plugin-singlefile output of `web/src/` — verified current with `dbb5c8f`: contains the `#/calendar`, `#/workbench`, `#/home` routes and all verification hooks) + sibling `qwebchannel.js`. **No Node on the Mac, ever** (rule 4). `web/` sources, vitest rig, and the lockfile are dev-only.

**Python half:**
- `web_host.py` (A) — shared `WebHost` for every web surface (Agent chat, and the flag-gated Calendar/Workbench/Home). Owns bundle resolution, QWebChannel registration (`extra_bridges` for multi-object channels), JS-console→Python logging, renderer-death auto-dump of the `web_diag` fact sheet, and **the GC-crash fix** (parents orphan bridges — see §11).
- `web_flags.py` (A) — `web_tabs_mode()` (`enablement.web_tabs`: off|calendar|all, ANY error → off) and `web_home_enabled()` (`ui.web_home`, ANY error → False). **The Mac's untouched settings mean everything stays off — the port cannot change visible behavior except the Agent page's internal refactor.**
- `agent_page.py` (M) — Agent chat delegated onto WebHost. **web_host.py and agent_page.py are a pair; the existing Agent chat breaks if only one lands.**
- Pure-relay bridges (A): `calendar_bridge.py`, `workbench_bridge.py`, `home_bridge.py`; `chat_bridge.py` gains `chatNotice` signal + deliberately-non-Slot `push_notice` (page scripts must not forge notices).
- Controllers (A): `src/services/enablement_web.py` (Calendar: reschedule gate = validate-against-last-feed + ISO date + single-winner inflight claim + NATIVE confirm, fail-closed without confirm_fn; Workbench: `sanitize_html` is the ONLY preview producer, publish gate pins content before the modal and re-checks after, freezes mutations while publish inflight) and `src/services/home_web.py` (Home viewmodel + the ONE authority slot `js_request_mode_switch` — full gate + native confirm + **deferred dispatch via `QTimer.singleShot(0,…)`**, which is load-bearing: undeferred, `switch_mode` tears down the WebHost mid-QWebChannel-walk).
- Watchdog: `main_window._arm_home_watchdog` — web Home falls back to native on load-failure or if `window.__almaHomeMounted` is still falsy after 8 s. Only Home has a fallback; Calendar/Workbench have none (acceptable: default off).
- Diagnostics: `src/ui/web/web_diag.py` + `scripts/web_diag.py` (the triage entry point), `scripts/verify_web_pivot_mac.sh` (acceptance), `scripts/collect_mac_diag.sh` (read-only deep triage), `scripts/measure_web_rss.py` (informational; needs psutil on macOS — the ctypes fallback is Windows-only and returns −1), `scripts/sign_qtwebengine_dev.sh` (the guarded trap — rule 5).

**Silent-failure philosophy:** every bridge slot and controller push swallows exceptions — a mis-port looks like "clicks do nothing", never a traceback. Verification is therefore: bridge contract tests (G4/G5), `ping()` round-trips, and `window.__alma*` hooks via `runJavaScript`.

Proving groups: G1, G4, G5 + the singly-run `test_web_chat_drawer.py` + Phase 7 scripts.

### 12.E — Qt UI & branding

- `src/branding.py` (A): exactly two constants (`CONTENT_COMMAND_CENTER`, `CONTENT_COMMAND_CENTER_DESCRIPTION`). Module-level import in `home_page.py` (hard) and `home_web.py`; lazy in `main_window`. The Help article `getting-started/content-command-center.md` pins the description verbatim via test.
- `main_window.py`: mode-aware chrome (CCC subtitle/footer in enablement only — product keeps RCM branding), sidebar in a QScrollArea (pairs with `qss.py` selectors `#SidebarScroll`/`#SidebarNavHost` + min-heights that keep macOS font descenders from clipping), web Home wiring + watchdog + native fallback, generic `on_page_shown()` page-refresh hook, enablement Help/feedback routing, Agent-page fallback is now a functional native ChatPanel (not a dead label), and `EnablementMonitor.start(..., asana_interval_seconds=60)` — **wrapped in try/except that logs DEBUG only**: if the monitor signature didn't land, Asana sync/briefs/KB silently never start.
- `app_modes.py`: +2 PageSpecs only (`en_attention`, `en_help`). **Mode resolution logic unchanged — nothing in this range forces a mode.** (The dev box's `app.default_mode: last` was a local settings edit, not code; never replicate it.)
- `splash_window.py`: mode-aware branding via local `_Branding` dataclass (deliberately NOT importing branding.py); any resolution failure → product branding.
- Enablement `settings.py`: 7 icon-tabs; `_GuideSection` (style guide + card template preview⇄editor); KB card (status enumeration + off-thread EC bootstrap — owner-gated); "Browse Drive…" → `drive_folder_picker_dialog.py` (A; roots = Shared Drives + sharedWithMe, the only roots a service account has; cached instance + `reload()`; workers parented AND held until slot fires); Usage tab lazy-builds `RennUsagePanel` (A, `usage_tab.py`) on first visit — keyed off the literal tab text 'Usage' (fragile but shipped; port as-is).
- Shared system panels (A): `updates_panel.py` (embeds two fixes: `update_section` MERGE so `github_repo` and `last_checked` stop clobbering each other; token passed to `stage()`) and `maintenance_panel.py` (memory profiler + typed-DELETE full reset). **Product `settings_page.py` had ~827 lines deleted in favor of mounting these** — porting the page without the panels = ImportError when the tab builds.
- `_common.py`: `style_native_dialog()` — the blank-dialog-button fix (page-level selectorless cream background overrode button fills but not text color → white-on-cream invisible buttons; the corrective QSS must sit ON the dialog). Platform-neutral; applies on macOS identically.
- `task_detail.py`: own-row Mark complete/Reopen (`completed_changed` signal; click IS consent — direct write path, no Confirm card), wrapping who-row (the clipping fix), Haiku brief block, read-only Asana extras (chips/attachments/comments — all PlainText, untrusted).
- `credentials_panel.py` + `drive_probe_worker.py` (A): SA status line probed on showEvent only (never `__init__` — teardown crash lesson); save now force-ASSIGNS `enablement.drive.auth_type='service_account'` (fires only on an explicit click — don't click Save on the SA field during smokes if the Mac might be OAuth-configured).
- `gemini_chats_page.py`: product chat OFF native MCP (`use_mcp_tools=False`, text TOOL_CALL loop) — the fabricated-ticket-ID fix.
- `attention_queue_tab.py`: Refresh clears session dismissals (dismiss = "not now", not "hidden till restart").

Proving groups: G3, G13, G14, G15 + smokes.

### 12.F — Updater / installer / CI / docs

**The five-commit updater chain works only as a set** (git delivers it as a set): manifest fetcher accepts both `manifest.json`/`release_manifest.json` + API asset URLs + Bearer + octet-stream + strip-auth-on-cross-host-redirect (`7ce570a`); splash install path forwards the token (`3ed5a62` — `update_action_widget.stage(token=…)`; old `updater.py` would TypeError); `importlib.reload(src)` after staged apply + real VERSION on the splash (`79e8bd7`); `restart_requested()` module flag consumed by `main.py` so the old process exits instead of opening a second window (`d2bc239` — **main.py and restart.py are a pair**); splash closes so its nested `exec()` unwinds (`b5d241a`).
- `main.py` also: `AA_ShareOpenGLContexts` + eager QtWebEngineWidgets import **before** QApplication (macOS-required init order — never let anything reorder those lines).
- `installer/install.py` + `launcher_mac.command`: packaged-install fixes (LSRequiresNativeExecution/arm64 priority, `arch -arm64` exec, `~/.local/bin` on PATH for the claude CLI under launchd). **Irrelevant to a source-tree dev run** — but their lessons are the dev-run gates G1/G4. `launcher_mac.command` is tracked non-executable (mode 100644) and is always invoked via `bash`/the packaged installer, so the zip's lack of exec bits costs nothing; LF endings are preserved by the zip.
- `installer/ci/sign_macos.sh`: JIT-entitlement deep-sign for CI packaging (fixes packaged blank render); loud warning that .zip can't be stapled. CI-only.
- `.github/workflows/*`: Node 24 + vitest step. CI-only.
- `requirements.lock`: pypdf pin WITHOUT hashes (expected M1 pip-compile later — see Phase 3).
- Docs added: `KB_ARCHITECTURE.md`, `DRIVE_SEARCH_EVAL.md` (PHI warning: eval Drive must be synthetic), `RCM_AGENT_ARCHITECTURE.md` (draft, no code), `QWEN3_EMBEDDINGS.md` (historical), `SESSION_SUMMARY.md` (**historical — Part 3's "unsolved" blank render is solved; see §11**), the two prior porting guides, `STARTUP_SPLASH.md` update, CLAUDE.md's Enablement Web Pivot section (instructions for YOU — read it).

Proving groups: G13, G14 + Phase 4 smokes (`normalize_github_repo`, `stage(token=)`, restart flag).

### 12.G — Tests & assets (what the 73 test files are)

Families: 10 `test_help_claims_*` audits (~15,600 lines — the final gate; import ~30 real modules + ast-parse `page.py`); help infra (store/search/tab/corpus_sync); drive (query/reader_search/live_search_tools/doc_search/eval_scorer — pure; folder_picker/sa_status/google_access_gate — offscreen Qt + tmp settings); web bridges (calendar/workbench/home/chat_drawer/home_web_controller/web_flag/html_sanitize/web_guardrails — NO WebEngine except web_diag's import probe); chat/CLI (chat_engine/turn_grounding/chat_tools/claude_cli_*/redaction/shared_session/demo_guru — hermetic, no subprocess spawned); asana (pagination/setup_guard/readback/writeback — fakes); system/updater (system_panels/settings_subtabs/phase5_release/updater/update_action_widget/manifest_fetcher/renn_usage/scoped_usage_tracker — monkeypatched settings, never real `data/`); enablement UI (the rest — offscreen Qt + empty_db).

Signature changes the tests pin (already in the tree at `dbb5c8f`; listed so you recognize the failure if the tree is stale): `_prepare_prompt`→tuple; `_reconcile_existing_task`→task_id/None; `_poll_board(conn,obj,spec,results)`; `log_call(cost_usd=)`; `list_active_trcs(now=)`; `get_trc_history(days=)`; `_find_manifest_url` accepts `release_manifest.json`; `stage(token=)`.

`tests/conftest.py` is NOT in this delta — the fixtures (`empty_db`, `seeded_db`, temp settings) are unchanged from base.
### 12.H — Consolidated silent-failure matrix

Every one of these LOOKS like success. Check them explicitly; none throws where a tester can see it.

| If this is broken… | …the app shows | Explicit check |
|---|---|---|
| FTS5 absent / mig 050 unapplied | All doc/help/KB search returns `[]`, no errors | Gate G2 pre-launch; `pytest tests/test_enablement_doc_search.py` |
| pypdf missing | Drive PDFs → metadata-only KB cards | `pip show pypdf` |
| `renn_deck.pptx` corrupted in transfer | Decks silently unbranded | G5 `Presentation()` open |
| Migration 043 missing | Asana events poll silently degrades to legacy | Phase 5 schema verify (`monitor_sources.events_sync`) |
| `usage_tracker` stale vs bridge (`cost_usd`) | ALL usage accounting stops (TypeError → debug log) | `gemini_usage` rows appear after a scan AND after a Renn turn |
| `help_tab.py`/`help_tools.py` didn't land | Help tables stay empty forever | Smoke №5: 62 articles on tab open |
| Web Home broken (if ever flag-flipped) | Watchdog swaps native Home in after 8 s, one log line | `window.__almaHomeMounted` probe; `alma.home.web` log |
| Wrong task_type / sink not attached | No `renn_chat` usage rows | Smoke №8 usage check |
| google libs absent | Drive reads "not connected" forever (fail-closed) | `pip show google-api-python-client google-auth` |
| MCP PYTHONPATH anchor lost | Renn narrates tool calls; empty `chat_tool_executions` | Smoke №8 ledger check (the MCP `command` itself is `sys.executable` — absolute, PATH-proof; only the anchor can break) |
| `config/redaction_patterns.json` missing/unreadable | Every Claude API call hard-fails with `RedactionError` (fail-closed — the ONE loud one) | G5 file check |
| EnablementMonitor signature mismatch | Asana sync/briefs/KB never start; DEBUG-level log only | Import smoke (`enablement_monitor`) + smoke №8 tool rows |
---

## 13. New settings keys — READ-ONLY inventory

Every key this range introduces or newly reads. **You never write any of these** (rule 1). Column 3 is what happens on this Mac, whose settings predate all of them.

| Key | Default when absent | Effect on this Mac untouched |
|---|---|---|
| `enablement.web_tabs` | `"off"` (any error → off) | Native Calendar/Workbench tabs render; web tabs dormant |
| `ui.web_home` | `false` (fails closed) | Native Home is the boot page; no Chromium at Home |
| `enablement.turn_grounding.mode` | `"telemetry"` (settings error → off) | Grounding telemetry recorded per turn; invisible, warn-only |
| `enablement.turn_grounding.tier_c_enabled` | `false` | Tier C stays off |
| `enablement.demo_mode` | `true` when absent | Chat Guru publish = local-only; KB indexing refused. **Looks like a port bug; is the designed fail-safe.** If the Mac's settings already set it false, live behavior resumes per their own config. |
| `enablement.kb.enabled` | `false` | KBWorker never ticks |
| `enablement.kb.ec_folder_id` | unset | Studio Drive-upload tool steers "ec_not_bootstrapped"; KB card shows not-bootstrapped. Written ONLY by owner-gated EC bootstrap. |
| `enablement.kb.ec_parent_id` | unset | EC root would bootstrap at Drive root (owner-gated anyway) |
| `enablement.drive.active_folders` | unset/legacy strings | Live Drive search falls back to all-visible-with-note; legacy string entries are healed to dicts on next successful pick |
| `enablement.drive.auth_type` | `"service_account"` | SA-aware gate (`google_access_ready`) governs pickers/probes/KB tick |
| `enablement.asana.poll_interval_seconds` | `60` | Asana events cadence (only when Asana connected) |
| `enablement.asana.brief_per_poll_cap` | `5` | Brief generation cap per drain |
| `enablement.help.bug_form_url` | unset | Help "Flag a bug" shows a friendly "not configured" dialog |
| `enablement.style_guide_doc_id` / `enablement.card_template_doc_id` | unset | Tagged-guide pointers; set via Settings UI actions only |
| `updates.auth_mode` / `updates.github_repo` / `updates.last_checked` | `"pat"` / `alma-health/alma-insights` / — | Startup release check runs; **prompts get dismissed, never installed** (rule 3) |
| `claude.cli_path` | unset → `shutil.which('claude')` | Pre-existing key; matters more now that the CLI leg is load-bearing for Renn (PATH caveat, gate G4) |
| `app.default_mode` / `app.last_mode` | pre-existing | UNCHANGED by this range. The Mac's own values decide the boot mode. |

---

## 14. Known oddities riding along in this range (do not "fix", do report)

1. **The `$LOG` junk file** at repo root — 230 bytes of debug output (a help-tab state dump) accidentally committed in `34545cd`, filename wrapped in U+F022 private-use characters. Harmless; it rides in the zip and the sync lands it on the Mac. Leave it in place (it's part of the canonical tree until a cleanup commit removes it on the Windows side). Beware it can confuse naive shell globs (`$L` expansion in double quotes).
2. **`docs/ENABLEMENT_ROADMAP.md`** carries a duplicated, mid-sentence-truncated "Built — Enablement web pivot" heading (merge blemish). Cosmetic.
3. **`docs/SESSION_SUMMARY.md` Part 3** presents the macOS blank-render as unsolved — historical snapshot; solved since (see §11). Do not chase.
4. **`requirements.lock`** pypdf entry has no hashes yet (M1 pip-compile expected later).
5. **Stale memory/docs after this port:** MEMORY.md's "redaction fails OPEN (HIGH)" is closed by this range; `docs/SECURITY_COMPLIANCE_REVIEW.md` predates the fix. Note it in your report so future sessions don't re-fix it.
6. Four **gitignored** WebEngine round-trip tests (`test_*_web_local.py`) and the gitignored `test_kb_*_local.py` family exist only on the Windows box. `verify_web_pivot_mac.sh` silently skips them — a false-PASS trap worth naming in your report. Ask the owner whether to transfer them out-of-band.

---

## 15. Acceptance criteria — the definition of done

Report each line explicitly (✅/❌/skipped-why):

1. Zip authenticated (`unzip -z` printed `dbb5c8f303042e52998e6daa1b7ebb8620cbb4e6`, or the pinned-commit fallback corroboration was used); post-sync `diff -rq` shows **zero** "differ" and **zero** "Only in $SRC" lines; `from src import VERSION` == 1.0.7. If the tree is a git repo: the `pre-port-rescue-2026-07-22` branch exists and `applied_delta_git_view.txt` was saved.
2. `data/settings.yaml` byte-identical to the Phase-1 backup (`diff data/settings.yaml ~/alma_port_backup_2026-07-22/settings.yaml` — empty). The warehouse differs only by migrations/new tables (that's expected; it is not "destroyed settings").
3. Gates G1–G5 all passed (arm64/16384, FTS5, PySide6 quartet, CLI noted, binaries byte-exact).
4. `pip show pypdf` → 6.14.2.
5. All Phase-4 import smokes printed OK, including the atomic tool-registry check and VERSION 1.0.7.
6. First launch applied migrations 043–050 (log lines / `schema_migrations` rows) with no RuntimeError; splash showed v1.0.7; update prompt dismissed.
7. Test groups G1–G20 + singly-run `test_web_chat_drawer.py`: pass counts reported; only the expected xfails/skips from §8; zero unexpected failures; zero XPASS in help-claims.
8. `python scripts/web_diag.py` → 0 FAIL; `verify_web_pivot_mac.sh` → PASS (with the skipped-local-tests caveat stated).
9. In-app smokes 1–9 from §10 all observed (CCC branding, 62 help articles, 7 settings tabs, task-detail button, Renn turn with real tool execution + metered usage — or CLI-absent noted).
10. Final report includes: what Phase 0 found (git repo or not; clean or hand-patched; base-era modules present); the pre-sync compare read (delta matched Appendix A? unexplained drift? DEST-only `.py` strays under `src/`?); any settings deltas OBSERVED in the Mac's `data/settings.yaml` (read-only grep) worth the owner's attention; the §14 oddities acknowledged; anything owner-gated that's now unblocked (flag flips, EC bootstrap, bug-form URL, local-test transfer).

If every line is ✅, the Mac is functionally identical to the Windows dev box at `dbb5c8f` — with its own settings intact, all new surfaces dormant behind their own flags, and the owner holding every switch.
---

## Appendix A — Full file manifest (295 files at `dbb5c8f`, grouped) + addendum

**Addendum — the four commits after `dbb5c8f` (included in the pinned zip):**
`a58685b` + `00aa3f1` repair/harden `.github/workflows/snyk.yml` (delete stray `snyk.yml.new`);
`698dffa` parks the NLP stack — edits `requirements.txt`, `requirements.lock`,
`installer/build_release.py`, `src/startup/checks/environment.py`, `.github/workflows/ci.yml`,
`.github/workflows/snyk.yml`, `tests/unit/test_trending_engine.py`;
`6890a70` restores `src/data/text_diff.py` (`diff_words` — fixes 3 committed
`test_workbench_bridge.py` failures that shipped with `dbb5c8f`);
`b8cc2db` adds this guide + the per-file dossier under `docs/`;
`ea0c037` bumps `scan_server/package-lock.json` (npm audit fix — node-forge, path-to-regexp,
express/qs/body-parser; the remaining uuid moderate is unreachable code and deferred);
`9899058` SHA-pins every action in the four active workflows and brings
`installer/README.md`, `installer/README_IT_SECURITY.md`, `setup_alma_insights.py`, root
`README.md`, and `docs/BUILD_AND_RELEASE.md` current with the parked NLP lane.
The pre-sync compare will show these ~20 paths beyond the 295 below — expected.

Generated from `git diff --numstat 7adbe7b..dbb5c8f`. A = added, M = modified (no files were deleted in this range). This is a VERIFICATION checklist — it is what the Phase-2 pre-sync compare should show against a clean July-2 baseline, and everything lands via the single rsync pass. It is NOT a list of manual copies.

### migrations — 8 files (+324/−0)

| File | A/M | +/− |
|---|---|---|
| `migrations/043_asana_events.sql` | A | +15/−0 |
| `migrations/044_asana_task_extras.sql` | A | +19/−0 |
| `migrations/045_task_brief.sql` | A | +13/−0 |
| `migrations/046_kb.sql` | A | +96/−0 |
| `migrations/047_enablement_artifacts.sql` | A | +35/−0 |
| `migrations/048_help_center.sql` | A | +82/−0 |
| `migrations/049_kb_extra_fields.sql` | A | +12/−0 |
| `migrations/050_enablement_documents_fts.sql` | A | +52/−0 |

### src/data + export — 28 files (+3220/−222)

| File | A/M | +/− |
|---|---|---|
| `src/data/artifact_store.py` | A | +181/−0 |
| `src/data/asana_client.py` | M | +228/−48 |
| `src/data/asana_extras.py` | A | +167/−0 |
| `src/data/asana_monitor.py` | M | +324/−49 |
| `src/data/asana_setup.py` | M | +9/−1 |
| `src/data/asana_writeback.py` | M | +139/−3 |
| `src/data/chat_action_requests.py` | M | +36/−3 |
| `src/data/doc_reader.py` | M | +50/−3 |
| `src/data/drive_query.py` | M | +34/−5 |
| `src/data/drive_reader.py` | M | +271/−11 |
| `src/data/enablement_doc_search.py` | A | +221/−0 |
| `src/data/enablement_monitor.py` | M | +35/−4 |
| `src/data/enablement_store.py` | M | +168/−47 |
| `src/data/enablement_tasks.py` | M | +2/−1 |
| `src/data/google_access.py` | A | +57/−0 |
| `src/data/google_oauth.py` | M | +26/−15 |
| `src/data/html_sanitize.py` | A | +237/−0 |
| `src/data/llm_gen.py` | A | +95/−0 |
| `src/data/mermaid_lint.py` | A | +115/−0 |
| `src/data/pptx_reader.py` | A | +75/−0 |
| `src/data/pptx_store.py` | M | +63/−15 |
| `src/data/quiz_artifacts.py` | A | +126/−0 |
| `src/data/renn_usage.py` | A | +164/−0 |
| `src/data/scoped_usage_tracker.py` | M | +3/−1 |
| `src/data/source_baseline.py` | M | +38/−12 |
| `src/data/task_brief.py` | A | +233/−0 |
| `src/data/usage_tracker.py` | M | +7/−2 |
| `src/export/gdrive_export.py` | M | +116/−2 |

### src/data/kb — 8 files (+1623/−0)

| File | A/M | +/− |
|---|---|---|
| `src/data/kb/__init__.py` | A | +9/−0 |
| `src/data/kb/card_format.py` | A | +118/−0 |
| `src/data/kb/drive_kb.py` | A | +343/−0 |
| `src/data/kb/ingest.py` | A | +242/−0 |
| `src/data/kb/search.py` | A | +165/−0 |
| `src/data/kb/store.py` | A | +184/−0 |
| `src/data/kb/sync.py` | A | +440/−0 |
| `src/data/kb/worker.py` | A | +122/−0 |

### src/data/help — 4 files (+552/−0)

| File | A/M | +/− |
|---|---|---|
| `src/data/help/__init__.py` | A | +16/−0 |
| `src/data/help/loader.py` | A | +174/−0 |
| `src/data/help/search.py` | A | +206/−0 |
| `src/data/help/store.py` | A | +156/−0 |

### src/data/chat_tools — 7 files (+1251/−39)

| File | A/M | +/− |
|---|---|---|
| `src/data/chat_tools/artifact_tools.py` | A | +526/−0 |
| `src/data/chat_tools/enablement_tools.py` | M | +336/−24 |
| `src/data/chat_tools/fast_path.py` | M | +37/−2 |
| `src/data/chat_tools/help_tools.py` | A | +100/−0 |
| `src/data/chat_tools/kb_tools.py` | A | +182/−0 |
| `src/data/chat_tools/registry.py` | M | +64/−12 |
| `src/data/chat_tools/tool_prompts.py` | M | +6/−1 |

### src agents/llm/mcp — 6 files (+665/−124)

| File | A/M | +/− |
|---|---|---|
| `src/agents/claude_cli_bridge.py` | M | +134/−4 |
| `src/agents/claude_cli_subprocess.py` | M | +10/−1 |
| `src/llm/claude_cli_client.py` | M | +37/−13 |
| `src/llm/claude_client.py` | M | +15/−1 |
| `src/llm/claude_tools.py` | M | +110/−64 |
| `src/mcp/chat_mcp_server.py` | M | +359/−41 |

### src/services — 6 files (+3253/−60)

| File | A/M | +/− |
|---|---|---|
| `src/services/agent_chat.py` | M | +290/−57 |
| `src/services/chat_engine.py` | M | +417/−3 |
| `src/services/chat_session.py` | M | +44/−0 |
| `src/services/enablement_web.py` | A | +756/−0 |
| `src/services/home_web.py` | A | +350/−0 |
| `src/services/turn_grounding.py` | A | +1396/−0 |

### src/ui/web (Qt web host + bridges) — 9 files (+1052/−36)

| File | A/M | +/− |
|---|---|---|
| `src/ui/web/agent_page.py` | M | +14/−24 |
| `src/ui/web/calendar_bridge.py` | A | +113/−0 |
| `src/ui/web/chat_bridge.py` | M | +15/−0 |
| `src/ui/web/dist/index.html` | M | +28/−12 |
| `src/ui/web/home_bridge.py` | A | +93/−0 |
| `src/ui/web/web_diag.py` | A | +320/−0 |
| `src/ui/web/web_flags.py` | A | +64/−0 |
| `src/ui/web/web_host.py` | A | +260/−0 |
| `src/ui/web/workbench_bridge.py` | A | +145/−0 |

### web (React SPA source) — 22 files (+7086/−1495)

| File | A/M | +/− |
|---|---|---|
| `web/package-lock.json` | A | +2941/−0 |
| `web/package.json` | M | +4/−2 |
| `web/src/App.jsx` | M | +27/−1493 |
| `web/src/calendar/CalendarApp.jsx` | A | +434/−0 |
| `web/src/calendar/demo.js` | A | +69/−0 |
| `web/src/calendar/grid.js` | A | +105/−0 |
| `web/src/calendar/grid.test.js` | A | +109/−0 |
| `web/src/chat/ChatApp.jsx` | A | +1384/−0 |
| `web/src/chat/ChatDrawer.jsx` | A | +174/−0 |
| `web/src/chat/demoRenn.js` | A | +36/−0 |
| `web/src/home/HomeApp.jsx` | A | +230/−0 |
| `web/src/home/demo.js` | A | +69/−0 |
| `web/src/home/home.test.jsx` | A | +145/−0 |
| `web/src/lib/bridge.js` | A | +38/−0 |
| `web/src/lib/markdown.jsx` | A | +100/−0 |
| `web/src/lib/markdown.test.jsx` | A | +72/−0 |
| `web/src/styles.css` | M | +427/−0 |
| `web/src/workbench/EditTools.jsx` | A | +114/−0 |
| `web/src/workbench/WorkbenchApp.jsx` | A | +401/−0 |
| `web/src/workbench/demo.js` | A | +105/−0 |
| `web/src/workbench/workbench.test.jsx` | A | +89/−0 |
| `web/vitest.config.js` | A | +13/−0 |

### src/ui/pages/enablement — 7 files (+2311/−269)

| File | A/M | +/− |
|---|---|---|
| `src/ui/pages/enablement/_common.py` | M | +39/−2 |
| `src/ui/pages/enablement/attention_queue_tab.py` | M | +8/−1 |
| `src/ui/pages/enablement/help_tab.py` | A | +294/−0 |
| `src/ui/pages/enablement/page.py` | M | +853/−105 |
| `src/ui/pages/enablement/settings.py` | M | +682/−149 |
| `src/ui/pages/enablement/task_detail.py` | M | +122/−12 |
| `src/ui/pages/enablement/usage_tab.py` | A | +313/−0 |

### src/ui (other) — 11 files (+1818/−834)

| File | A/M | +/− |
|---|---|---|
| `src/ui/app_modes.py` | M | +10/−0 |
| `src/ui/design/qss.py` | M | +21/−0 |
| `src/ui/dialogs/drive_folder_picker_dialog.py` | A | +300/−0 |
| `src/ui/main_window.py` | M | +264/−16 |
| `src/ui/pages/gemini_chats_page.py` | M | +16/−3 |
| `src/ui/pages/home_page.py` | M | +37/−2 |
| `src/ui/pages/settings_page.py` | M | +15/−812 |
| `src/ui/widgets/credentials_panel.py` | M | +89/−1 |
| `src/ui/widgets/drive_probe_worker.py` | A | +89/−0 |
| `src/ui/widgets/maintenance_panel.py` | A | +304/−0 |
| `src/ui/widgets/updates_panel.py` | A | +673/−0 |

### startup + app shell — 5 files (+155/−8)

| File | A/M | +/− |
|---|---|---|
| `main.py` | M | +45/−1 |
| `src/__init__.py` | M | +1/−1 |
| `src/branding.py` | A | +26/−0 |
| `src/startup/splash_window.py` | M | +65/−5 |
| `src/startup/update_action_widget.py` | M | +18/−1 |

### src/updater — 4 files (+164/−21)

| File | A/M | +/− |
|---|---|---|
| `src/updater/manifest_fetcher.py` | M | +23/−5 |
| `src/updater/restart.py` | M | +30/−4 |
| `src/updater/update_checker.py` | M | +58/−1 |
| `src/updater/updater.py` | M | +53/−11 |

### installer + CI — 6 files (+83/−15)

| File | A/M | +/− |
|---|---|---|
| `.github/workflows/build.yml` | M | +3/−1 |
| `.github/workflows/release.yml` | M | +3/−1 |
| `installer/build_release.py` | M | +1/−0 |
| `installer/ci/sign_macos.sh` | M | +50/−9 |
| `installer/install.py` | M | +18/−2 |
| `installer/launcher_mac.command` | M | +8/−2 |

### scripts — 7 files (+1230/−0)

| File | A/M | +/− |
|---|---|---|
| `scripts/collect_mac_diag.sh` | A | +51/−0 |
| `scripts/drive_eval_preflight.py` | A | +324/−0 |
| `scripts/measure_web_rss.py` | A | +136/−0 |
| `scripts/run_drive_eval.py` | A | +492/−0 |
| `scripts/sign_qtwebengine_dev.sh` | A | +140/−0 |
| `scripts/verify_web_pivot_mac.sh` | A | +49/−0 |
| `scripts/web_diag.py` | A | +38/−0 |

### prompts + templates — 7 files (+157/−0)

| File | A/M | +/− |
|---|---|---|
| `assets/templates/renn_deck.pptx` | A | +-/−- |
| `assets/templates/support_center_article_template.md` | A | +51/−0 |
| `config/prompts/enablement_battle_card.txt` | A | +22/−0 |
| `config/prompts/enablement_mermaid.txt` | A | +19/−0 |
| `config/prompts/enablement_one_pager.txt` | A | +22/−0 |
| `config/prompts/enablement_quiz.txt` | A | +25/−0 |
| `config/prompts/enablement_task_brief.txt` | A | +18/−0 |

### assets/help — 63 files (+5663/−0)

| File | A/M | +/− |
|---|---|---|
| `assets/help/create/content-studio.md` | A | +89/−0 |
| `assets/help/create/deck-outline.md` | A | +96/−0 |
| `assets/help/create/deferred-previews.md` | A | +79/−0 |
| `assets/help/create/powerpoint.md` | A | +94/−0 |
| `assets/help/create/studio-no-screen.md` | A | +81/−0 |
| `assets/help/create/zendesk-macros.md` | A | +82/−0 |
| `assets/help/create/zendesk.md` | A | +82/−0 |
| `assets/help/getting-started/content-command-center.md` | A | +74/−0 |
| `assets/help/getting-started/demo-vs-live.md` | A | +93/−0 |
| `assets/help/getting-started/first-15-minutes.md` | A | +137/−0 |
| `assets/help/getting-started/reading-this-help.md` | A | +97/−0 |
| `assets/help/getting-started/the-surfaces.md` | A | +118/−0 |
| `assets/help/getting-started/what-this-is.md` | A | +87/−0 |
| `assets/help/insights/attention-queue.md` | A | +106/−0 |
| `assets/help/insights/card-health-scoring.md` | A | +95/−0 |
| `assets/help/insights/guru-analytics.md` | A | +104/−0 |
| `assets/help/insights/weak-card-actions.md` | A | +84/−0 |
| `assets/help/knowledge-base/kb-bootstrapping.md` | A | +107/−0 |
| `assets/help/knowledge-base/kb-index-cards.md` | A | +98/−0 |
| `assets/help/knowledge-base/kb-quarantine.md` | A | +97/−0 |
| `assets/help/knowledge-base/kb-searching.md` | A | +91/−0 |
| `assets/help/knowledge-base/kb-sync-rules.md` | A | +82/−0 |
| `assets/help/knowledge-base/kb-what-it-is.md` | A | +91/−0 |
| `assets/help/plan/asana-conflicts.md` | A | +100/−0 |
| `assets/help/plan/asana-setup.md` | A | +116/−0 |
| `assets/help/plan/calendar-views.md` | A | +90/−0 |
| `assets/help/plan/card-due-chips.md` | A | +85/−0 |
| `assets/help/plan/drag-reschedule.md` | A | +88/−0 |
| `assets/help/plan/task-writeback.md` | A | +96/−0 |
| `assets/help/plan/tasks-board.md` | A | +87/−0 |
| `assets/help/reference/glossary.md` | A | +121/−0 |
| `assets/help/reference/settings-keys.md` | A | +142/−0 |
| `assets/help/reference/version-and-data.md` | A | +121/−0 |
| `assets/help/reference/what-renn-can-write.md` | A | +119/−0 |
| `assets/help/renn/asking-well.md` | A | +72/−0 |
| `assets/help/renn/background-research.md` | A | +52/−0 |
| `assets/help/renn/confirm-card.md` | A | +67/−0 |
| `assets/help/renn/daily-plan.md` | A | +55/−0 |
| `assets/help/renn/pickers.md` | A | +63/−0 |
| `assets/help/renn/tool-reference.md` | A | +101/−0 |
| `assets/help/renn/what-renn-can-do.md` | A | +67/−0 |
| `assets/help/renn/where-renn-appears.md` | A | +72/−0 |
| `assets/help/settings/ai-provider.md` | A | +95/−0 |
| `assets/help/settings/connect-asana.md` | A | +114/−0 |
| `assets/help/settings/connect-google.md` | A | +87/−0 |
| `assets/help/settings/connect-guru.md` | A | +74/−0 |
| `assets/help/settings/guides.md` | A | +111/−0 |
| `assets/help/settings/identity.md` | A | +80/−0 |
| `assets/help/settings/overview.md` | A | +101/−0 |
| `assets/help/settings/test-connections.md` | A | +79/−0 |
| `assets/help/troubleshooting/blank-screen.md` | A | +66/−0 |
| `assets/help/troubleshooting/connect-first.md` | A | +66/−0 |
| `assets/help/troubleshooting/flag-a-bug.md` | A | +59/−0 |
| `assets/help/troubleshooting/known-issues.md` | A | +103/−0 |
| `assets/help/troubleshooting/renn-refuses.md` | A | +66/−0 |
| `assets/help/troubleshooting/what-to-include.md` | A | +55/−0 |
| `assets/help/workbench/bringing-documents-in.md` | A | +116/−0 |
| `assets/help/workbench/editing.md` | A | +100/−0 |
| `assets/help/workbench/overview.md` | A | +85/−0 |
| `assets/help/workbench/publish-drive.md` | A | +64/−0 |
| `assets/help/workbench/publish-guru.md` | A | +95/−0 |
| `assets/help/workbench/review-changes.md` | A | +75/−0 |
| `assets/help/workbench/style-guides.md` | A | +94/−0 |

### tests — 73 files (+27580/−95)

| File | A/M | +/− |
|---|---|---|
| `tests/drive_eval/__init__.py` | A | +10/−0 |
| `tests/drive_eval/gold.yaml` | A | +147/−0 |
| `tests/drive_eval/scorer.py` | A | +485/−0 |
| `tests/e2e_claude_chat.py` | M | +11/−7 |
| `tests/test_app_modes.py` | M | +7/−5 |
| `tests/test_asana_pagination.py` | A | +179/−0 |
| `tests/test_asana_readback.py` | M | +4/−2 |
| `tests/test_asana_setup_guard.py` | A | +71/−0 |
| `tests/test_asana_writeback.py` | M | +18/−6 |
| `tests/test_attention_dismissal.py` | A | +78/−0 |
| `tests/test_calendar_bridge.py` | A | +384/−0 |
| `tests/test_card_template.py` | A | +237/−0 |
| `tests/test_chat_engine.py` | M | +1049/−0 |
| `tests/test_chat_tools.py` | M | +33/−0 |
| `tests/test_claude_cli_bridge.py` | M | +16/−8 |
| `tests/test_claude_cli_bridge_mcp.py` | M | +6/−1 |
| `tests/test_claude_cli_system_prompt.py` | A | +302/−0 |
| `tests/test_demo_guru_suppression.py` | A | +130/−0 |
| `tests/test_doc_reader_unsupported.py` | A | +70/−0 |
| `tests/test_drive_eval_scorer.py` | A | +365/−0 |
| `tests/test_drive_folder_picker.py` | A | +607/−0 |
| `tests/test_drive_live_search_tools.py` | A | +222/−0 |
| `tests/test_drive_query.py` | A | +323/−0 |
| `tests/test_drive_reader_search.py` | A | +432/−0 |
| `tests/test_drive_sa_status.py` | A | +214/−0 |
| `tests/test_enablement_doc_search.py` | A | +220/−0 |
| `tests/test_enablement_live_cutover.py` | M | +10/−0 |
| `tests/test_enablement_ui_live.py` | M | +9/−1 |
| `tests/test_enablement_web_flag.py` | A | +115/−0 |
| `tests/test_google_access_gate.py` | A | +319/−0 |
| `tests/test_help_claims_create.py` | A | +1883/−0 |
| `tests/test_help_claims_getting-started.py` | A | +1419/−0 |
| `tests/test_help_claims_insights.py` | A | +1513/−0 |
| `tests/test_help_claims_knowledge-base.py` | A | +1767/−0 |
| `tests/test_help_claims_plan.py` | A | +1854/−0 |
| `tests/test_help_claims_reference.py` | A | +1229/−0 |
| `tests/test_help_claims_renn.py` | A | +1013/−0 |
| `tests/test_help_claims_settings.py` | A | +1795/−0 |
| `tests/test_help_claims_troubleshooting.py` | A | +1293/−0 |
| `tests/test_help_claims_workbench.py` | A | +1850/−0 |
| `tests/test_help_corpus_sync.py` | A | +101/−0 |
| `tests/test_help_search.py` | A | +178/−0 |
| `tests/test_help_store.py` | A | +203/−0 |
| `tests/test_help_tab.py` | A | +177/−0 |
| `tests/test_home_bridge.py` | A | +234/−0 |
| `tests/test_home_page.py` | M | +17/−0 |
| `tests/test_home_web_controller.py` | A | +392/−0 |
| `tests/test_html_sanitize.py` | A | +241/−0 |
| `tests/test_ingest_dedupe.py` | A | +64/−0 |
| `tests/test_kb_extra_and_readopt.py` | A | +154/−0 |
| `tests/test_manifest_fetcher.py` | M | +28/−0 |
| `tests/test_native_dialog_styling.py` | A | +140/−0 |
| `tests/test_page_return_reload.py` | A | +147/−0 |
| `tests/test_phase5_release.py` | M | +56/−54 |
| `tests/test_redaction_fail_closed.py` | A | +51/−0 |
| `tests/test_renn_usage.py` | A | +311/−0 |
| `tests/test_scoped_usage_tracker.py` | M | +1/−1 |
| `tests/test_settings_subtabs.py` | M | +33/−1 |
| `tests/test_shared_session.py` | A | +57/−0 |
| `tests/test_sidebar_layout.py` | A | +103/−0 |
| `tests/test_source_baseline.py` | M | +10/−4 |
| `tests/test_splash.py` | M | +55/−1 |
| `tests/test_stage4_data_warehouse.py` | M | +9/−4 |
| `tests/test_sync_status.py` | A | +51/−0 |
| `tests/test_system_panels.py` | A | +187/−0 |
| `tests/test_task_detail_actions.py` | M | +56/−0 |
| `tests/test_turn_grounding.py` | A | +1588/−0 |
| `tests/test_update_action_widget.py` | M | +3/−0 |
| `tests/test_updater.py` | M | +62/−0 |
| `tests/test_web_chat_drawer.py` | A | +225/−0 |
| `tests/test_web_diag.py` | A | +149/−0 |
| `tests/test_web_guardrails.py` | A | +115/−0 |
| `tests/test_workbench_bridge.py` | A | +693/−0 |

### docs — 10 files (+2021/−2)

| File | A/M | +/− |
|---|---|---|
| `CLAUDE.md` | M | +73/−0 |
| `docs/DRIVE_SEARCH_EVAL.md` | A | +325/−0 |
| `docs/ENABLEMENT_ROADMAP.md` | M | +73/−1 |
| `docs/KB_ARCHITECTURE.md` | A | +110/−0 |
| `docs/MAC_STYLE_GUIDE_TEMPLATE_GUIDE.md` | A | +123/−0 |
| `docs/QWEN3_EMBEDDINGS.md` | A | +406/−0 |
| `docs/RCM_AGENT_ARCHITECTURE.md` | A | +269/−0 |
| `docs/SESSION_SUMMARY.md` | A | +71/−0 |
| `docs/STARTUP_SPLASH.md` | M | +5/−1 |
| `docs/THREE_PILLAR_SURGICAL_PATCH_GUIDE.md` | A | +566/−0 |

### root/other — 4 files (+17/−0)

| File | A/M | +/− |
|---|---|---|
| `"\357\200\242$LOG\357\200\242"` | A | +5/−0 |
| `.gitignore` | M | +4/−0 |
| `requirements.lock` | M | +4/−0 |
| `requirements.txt` | M | +4/−0 |
