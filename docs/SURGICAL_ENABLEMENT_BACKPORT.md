# Surgical Enablement Backport Plan

**Audience:** A Claude (Sonnet, via AWS Bedrock) running on the **work machine**, executing against the work copy of Alma Insights.
**Source of truth:** The Mac branch `enablement-content-tabs` (and its ancestors `credentials-oauth`, `app-redesign`). Latest commits on that branch include `23721b4` (E2E upgrade), `1dd1ffe` (live E2E), `e9db9da` (live integration validator).
**Author's verification basis:** This plan was authored against a checkout where `enablement-content-tabs` and `main` both exist. `git merge-base main enablement-content-tabs` = `63f58b30d90eb50372e5e40fa300170e2fd40d45`. All file paths below were confirmed to exist on the `enablement-content-tabs` branch via `git ls-tree`. All "MODIFY" divergence notes were confirmed via `git diff main enablement-content-tabs -- <path>`.

> **READ THIS FIRST — the single most important nuance.** On the author's checkout, the work-baseline proxy (`main`) **already contained** the Bedrock CLI bridge (`src/agents/claude_cli_bridge.py`), the Claude CLI client (`src/llm/claude_cli_client.py`), and `src/services/chat_engine.py` **byte-identical** to the branch, plus `resolve_provider_for_task()` in `src/gemini/client_factory.py`. **Your work version may differ.** Do NOT assume. Every MODIFY step below tells you exactly what to grep for to decide whether a change is already present. Never overwrite a file blindly.

---

## 1. Objective & Non-Negotiable Constraints

### Objective
Bring three things onto the work version, surgically, file-by-file:
1. **Home page + product/enablement mode split** (boot/entry wiring, design system, sidebar).
2. **The Enablement Workbench** (8-tab enablement mode: Calendar / Tasks / Workbench / PowerPoint / Zendesk / Analytics / Settings, plus Home), with its full live back-end (Guru analytics, Asana sync, Drive read, Renn chat).
3. **The two live-integration test scripts** — `scripts/validate_live_integrations.py` and `scripts/e2e_asana_to_guru.py` — so the live Guru+Asana integration and the E2E can run on the work box **under Claude Bedrock**.

### Non-negotiable constraints
- **SURGICAL, not a merge or a checkout.** Do **not** `git merge enablement-content-tabs`, do **not** `git checkout enablement-content-tabs -- .`, do **not** rebase. Apply files in ordered layers, each with a pre-overwrite check.
- **Never commit onto the Mac branch.** Do all work on a NEW branch/worktree created off the work version's current HEAD. The Mac updates on `enablement-content-tabs` MUST NOT be overwritten or force-pushed.
- **The work version may have diverged.** For every MODIFY target, run the stated pre-check before overwriting. If the work version's copy has its own edits, do a manual 3-way merge — do not clobber.
- **Demo mode on the work box runs through Claude Bedrock, not Gemini.** The enablement lanes and Renn chat MUST resolve to the Claude Bedrock CLI path. See §5.
- **Prefer whole-file copy** from the branch (`git show enablement-content-tabs:<path> > <path>`) for files that are NEW on the work version. **Hand-merge** only the small set of shared files that the work version has independently changed.
- **PHI safety is preserved by construction:** enablement Claude is force-routed through the Bedrock CLI (BAA path), never the direct Anthropic API. Do not weaken this.

---

## 2. Pre-flight: Inspect, Diverge-Check, and Back Up Safely

### 2.1 Confirm you are on the work version, not the Mac branch
```bash
git -C C:/alma-insights rev-parse --abbrev-ref HEAD
git -C C:/alma-insights status --short
git -C C:/alma-insights log --oneline -5
```
If HEAD is `enablement-content-tabs`, STOP — you are on the Mac branch. Switch to the work version's mainline first.

### 2.2 Make the Mac branch reachable locally (read-only source)
The script may run where `enablement-content-tabs` is a local branch already, or only as `origin/enablement-content-tabs`. Verify:
```bash
git -C C:/alma-insights branch -a | grep enablement-content-tabs
```
- If you see a local `enablement-content-tabs`, use that name in all `git show` commands below.
- If you only see `remotes/origin/enablement-content-tabs`, first `git fetch origin`, then use `origin/enablement-content-tabs` everywhere this plan writes `enablement-content-tabs`.
- If neither exists, you cannot source files — fetch from origin or obtain the branch before continuing. **Do not** reconstruct files from this document's prose; copy them from the branch.

Define a shell variable to avoid mistakes:
```bash
SRC=enablement-content-tabs   # or: SRC=origin/enablement-content-tabs
```

### 2.3 Measure divergence between the work version and the branch
This tells you which shared files you can copy whole vs. must hand-merge.
```bash
# What the branch adds/changes overall (informational):
git -C C:/alma-insights diff --stat $(git merge-base HEAD $SRC) $SRC | tail -40

# For each SHARED file this plan will MODIFY, see if YOUR work version already has changes
# relative to the merge base (i.e., is it "clean" vs the base or has it diverged?):
BASE=$(git -C C:/alma-insights merge-base HEAD $SRC)
for f in main.py src/ui/main_window.py src/ui/theme.py src/ui/pages/settings_page.py \
         src/data/guru_client.py src/data/chat_tools/registry.py \
         src/data/chat_tools/semantic_tools.py src/mcp/chat_mcp_server.py \
         src/llm/claude_tools.py src/gemini/client_factory.py \
         src/agents/claude_cli_bridge.py src/llm/claude_cli_client.py \
         src/services/chat_engine.py src/data/pat_store.py \
         src/data/zendesk_client.py src/data/drive_reader.py requirements.txt; do
  if git -C C:/alma-insights cat-file -e HEAD:"$f" 2>/dev/null; then
    if git -C C:/alma-insights diff --quiet "$BASE" HEAD -- "$f"; then
      echo "CLEAN  (== base, safe to take branch version after re-check): $f"
    else
      echo "DIVERGED (work version edited this — HAND-MERGE): $f"
    fi
  else
    echo "ABSENT  (file is NEW on work version — copy whole): $f"
  fi
done
```
Record the output. Any line marked **DIVERGED** must be hand-merged (3-way), never overwritten.

### 2.4 Back up safely — new branch + optional worktree
```bash
# Tag the exact pre-backport state so rollback is trivial (see §9):
git -C C:/alma-insights tag pre-enablement-backport
# New working branch off the work version's current HEAD:
git -C C:/alma-insights switch -c enablement-backport
```
Optional (recommended) — isolate in a worktree so the live work tree is untouched:
```bash
git -C C:/alma-insights worktree add ../alma-enablement-backport enablement-backport
```
Back up the live DB before any migration:
```bash
cp C:/alma-insights/data/local_warehouse.db C:/alma-insights/data/local_warehouse.db.bak-pre-enablement 2>/dev/null || true
# (Path may be data/alma_insights.db on some installs — back up whichever exists.)
```

**Commit discipline:** commit after each layer (a–f) with a clear message, e.g. `backport(enablement): layer (a) design system`. End each commit message with the Co-Authored-By trailer your harness requires. Never push to `origin/enablement-content-tabs`.

---

## 3. Dependency & Migration Layer

### 3.1 Python dependencies (apply to `requirements.txt`)
The branch adds exactly these (confirmed via diff). Add them; do NOT remove anything the work version already pins.
```
python-pptx==1.0.2                 # PowerPoint tab .pptx export (pulls lxml, XlsxWriter, Pillow)
google-api-python-client==2.149.0  # Drive API
google-auth==2.36.0                # Service-account + OAuth tokens
google-auth-oauthlib==1.2.1        # InstalledAppFlow PKCE loopback (per-user OAuth)
```
Notes:
- On the branch, the old commented-out `# google-api-python-client` / `# google-auth` lines under "Optional — Google Drive export" were **uncommented and pinned**; `google-auth-oauthlib` is new. If your work `requirements.txt` still has them commented, uncomment+pin to the versions above.
- **`python-docx` is NOT needed and NOT on the branch.** `src/data/doc_reader.py` parses `.docx` purely with the stdlib (`zipfile` + `xml.etree.ElementTree`) and never imports `python-docx` (verified: the file's only imports are `os`, `re`, `zipfile`, `xml.etree.ElementTree`). Do **not** add `python-docx` to satisfy this backport. (If your work `requirements.txt` already pins it for some other reason, leave it — just don't add it on account of `doc_reader`.)
- `keyring` and `markdown` are pre-existing — do not re-add.

Install into the work venv:
```bash
pip install python-pptx==1.0.2 google-api-python-client==2.149.0 google-auth==2.36.0 google-auth-oauthlib==1.2.1
```
Verify imports:
```bash
python -c "import pptx, googleapiclient, google.auth, google_auth_oauthlib; print('deps OK')"
```

### 3.2 Migrations — apply IN ORDER
Copy the migration files first (they are NEW; safe whole-copies), then let the app's `schema_migrator.py` apply them on next launch, OR apply manually. All five are idempotent (`CREATE TABLE IF NOT EXISTS` / guarded `ALTER TABLE ADD COLUMN`).

**Copy the files:**
```bash
for m in 027_enablement 028_guru_analytics 029_pptx_decks 030_zendesk_content 031_guru_card_html; do
  git -C C:/alma-insights show $SRC:migrations/$m.sql > C:/alma-insights/migrations/$m.sql
done
```

**Required order and dependency facts:**
| Order | File | Creates / Alters | Hard dependency |
|------|------|------------------|-----------------|
| 1 | `migrations/027_enablement.sql` | `enablement_tasks`, `enablement_subtasks`, `enablement_documents`, `monitor_sources`, `asana_users`; **adds `source_ref` to `guru_content_drafts`** | Requires `guru_content_drafts` (migration `003`) to exist. |
| 2 | `migrations/028_guru_analytics.sql` | `guru_events`, `guru_team_stats`, `guru_card_verification`, `guru_card_comments`, `guru_sync_state` | None (standalone). |
| 3 | `migrations/029_pptx_decks.sql` | `pptx_decks` | None. |
| 4 | `migrations/030_zendesk_content.sql` | `zendesk_articles`, `zendesk_macros`, `zendesk_article_drafts`, `zendesk_macro_drafts` | None. |
| 5 | `migrations/031_guru_card_html.sql` | **adds `content_html` to `guru_content_drafts`** | Requires migration `003` (and is harmless after `027`). |

**Pre-check before applying** (idempotency / divergence guard):
```bash
# Confirm the work DB has migration 003's table (027 + 031 depend on it):
sqlite3 C:/alma-insights/data/local_warehouse.db "SELECT name FROM sqlite_master WHERE type='table' AND name='guru_content_drafts';"
# Confirm the work version's highest existing migration is < 027 (no number collision):
ls migrations/ | grep -oE '^0[0-9]+' | sort | tail -3
```
If a migration number `027`–`031` is **already used on the work version for something else**, STOP and reconcile numbering — do not overwrite an unrelated migration. (On the author's baseline the highest was `026`, so `027`–`031` were free.)

**Apply — use the project's `SchemaMigrator`, NOT raw `sqlite3 <`.** The migrator (`src/updater/schema_migrator.py`) does three things raw `sqlite3` does not: (1) records each applied file in the `schema_migrations` ledger so the app does not re-run them on launch; (2) applies `ALTER TABLE ADD COLUMN` **idempotently** by guarding each with a `PRAGMA table_info` check (`_apply_alters_idempotent`); (3) runs each file in its own transaction and stops on first failure. Migration `031`'s body is a bare `ALTER TABLE guru_content_drafts ADD COLUMN content_html TEXT;` — that is idempotent **only through the migrator**. If you apply it with raw `sqlite3 <`, a second run raises `duplicate column name`, AND the ledger never learns the file ran, so the app tries again on next launch and may error mid-init. There is **no `__main__`/argparse CLI** on `schema_migrator.py` (verified), so invoke it from Python:

```bash
python - <<'PY'
from src.data.connection_factory import get_connection
from src.updater.schema_migrator import SchemaMigrator
DB = "C:/alma-insights/data/local_warehouse.db"   # use the path that exists on YOUR box (see Appendix B #3)
conn = get_connection(DB)
applied = SchemaMigrator().migrate(conn)
conn.commit()
print("applied:", applied)
PY
```
Alternatively, **just launch the app once** — `schema_migrator` runs on DB init and applies all pending files in order. Do NOT use a `for ... sqlite3 < $m.sql` loop; it bypasses the ledger and breaks 031's idempotency.

**Post-check:**
```bash
sqlite3 C:/alma-insights/data/local_warehouse.db ".tables" | tr ' ' '\n' | grep -E "enablement|guru_events|guru_card_|pptx_decks|zendesk_"
sqlite3 C:/alma-insights/data/local_warehouse.db "PRAGMA table_info(guru_content_drafts);" | grep -E "source_ref|content_html"
```
"Green" = all five table families present AND `guru_content_drafts` has both `source_ref` and `content_html`.

---

## 4. Layered, Ordered File-by-File Update Phases

Apply layers **in order (a)→(f)**. Within a layer, copy NEW files first, then handle MODIFY files. Commit per layer.

**Copy helper** (use for every NEW file): `git -C C:/alma-insights show $SRC:<path> > <path>` (create parent dirs first). For every MODIFY file, run the stated pre-check, then either copy-whole (if your pre-check from §2.3 said CLEAN/ABSENT) or hand-merge (if DIVERGED).

---

### Layer (a) — Design System  *(zero runtime cost, no app-logic change; do this first so later UI imports resolve)*

**NEW files** — copy whole:
| Path | Purpose | Integration |
|------|---------|-------------|
| `src/ui/design/__init__.py` | Package marker; re-exports `icon`. | Imported by main_window + enablement pages. |
| `src/ui/design/tokens.py` | Canonical LIGHT palette + spacing/radius/type/elevation; flat `ALMA_*` constants. | Single source for all colors; `theme.py` re-exports from here. |
| `src/ui/design/icons.py` | QtSvg Feather glyph registry; `icon(name,size,color)→QIcon`. | Sidebar buttons + Home tiles call `icon(...)`. |
| `src/ui/design/anim.py` | Animation presets + `fade_in()` helper. | Page transitions / sidebar collapse. |
| `src/ui/design/qss.py` | `build_stylesheet()` = legacy sections + extras. | `theme.get_stylesheet()` delegates here. |

```bash
mkdir -p C:/alma-insights/src/ui/design
for f in __init__ tokens icons anim qss; do
  git -C C:/alma-insights show $SRC:src/ui/design/$f.py > C:/alma-insights/src/ui/design/$f.py
done
```

**MODIFY:**
- `src/ui/theme.py` — **gutted to a facade**: re-exports flat `ALMA_*` from `src/ui/design/tokens.py`; keeps shadow/table helpers; `get_stylesheet()` delegates to `qss.build_stylesheet()`.
  - **Before you overwrite, check X:** Run `git diff --quiet $BASE HEAD -- src/ui/theme.py`. If CLEAN/ABSENT → copy whole (`git show $SRC:src/ui/theme.py > src/ui/theme.py`). If DIVERGED (the work version added custom colors/helpers to `theme.py`), HAND-MERGE: keep the work version's bespoke helpers, but make `get_stylesheet()` delegate to `qss.build_stylesheet()` and source `ALMA_*` from `tokens.py`. Then verify every `from src.ui.theme import ALMA_*` symbol the work app uses still exists in `tokens.py` (`grep -rhoE "ALMA_[A-Z_]+" src/ | sort -u` vs the names defined in `tokens.py`). Any missing symbol must be added to `tokens.py` or re-exported.

**Validate layer (a):**
```bash
python -c "from src.ui.design.tokens import *; from src.ui.design.icons import icon; from src.ui.design.qss import build_stylesheet; from src.ui import theme; print('design OK', bool(build_stylesheet()))"
```

---

### Layer (b) — Home + Mode Split + Boot/Entry Wiring

**NEW files** — copy whole:
| Path | Purpose | Integration |
|------|---------|-------------|
| `src/ui/app_modes.py` | Pure-logic mode registry (PageSpec/ServiceSpec, factory names, `set_cli_override`, `resolve_startup_mode`, `current_mode`, `MODE_PRODUCT`/`MODE_ENABLEMENT`). | main.py CLI override; main_window mounting/sidebar/services. No Qt imports. |
| `src/ui/pages/home_page.py` | Home landing (mode tiles, quick actions, recent-activity feed with guarded SQL). | Mounted in both modes; signals `mode_selected`/`quick_action`/`activity_activated`. |

```bash
git -C C:/alma-insights show $SRC:src/ui/app_modes.py > C:/alma-insights/src/ui/app_modes.py
git -C C:/alma-insights show $SRC:src/ui/pages/home_page.py > C:/alma-insights/src/ui/pages/home_page.py
```

**MODIFY:**

- `main.py` — adds `--mode product|enablement` parsing BEFORE `QApplication`, calls `app_modes.set_cli_override(...)`, strips the arg from `sys.argv`.
  - **Before you overwrite, check X:** `git diff --quiet $BASE HEAD -- main.py`. If DIVERGED (likely — the work version may have its own startup logic, `--profile`, pycache clearing at lines ~13–18), **HAND-MERGE**: insert ONLY the mode-override block (verbatim from the branch) right before `QApplication(...)` is created. Do not replace the whole file. The block to insert:
    ```python
    mode_override = None
    if "--mode" in sys.argv:
        i = sys.argv.index("--mode")
        if i + 1 < len(sys.argv):
            mode_override = sys.argv[i + 1]
            sys.argv.remove("--mode"); sys.argv.remove(mode_override)
    for arg in list(sys.argv):
        if arg.startswith("--mode="):
            mode_override = arg.split("=", 1)[1]; sys.argv.remove(arg)
    if mode_override:
        from src.ui import app_modes
        app_modes.set_cli_override(mode_override)
    ```
    (Copy the exact text from `git show $SRC:main.py` to avoid drift.)

- `src/ui/main_window.py` — **MAJOR REWRITE on the branch** (+678/−... vs base): factory-method page construction, per-mode lazy mounting, `_mount_mode_pages`, `_populate_sidebar`, `_start_services_for_mode`/`_stop_services_for_mode`, `switch_mode`, `_create_home_page`, `_create_enablement_page`, `_wire_enablement_monitor`, sidebar QIcons, legacy `PAGE_*` aliases.
  - **Before you overwrite, check X:** This is the highest-risk file. Run `git diff --quiet $BASE HEAD -- src/ui/main_window.py`.
    - If your work `main_window.py` is **CLEAN vs base** (no local edits): copy the branch version whole (`git show $SRC:src/ui/main_window.py > src/ui/main_window.py`), then immediately confirm it imports cleanly and that every page factory it references resolves (it references product pages by method name; those product page modules must still exist on the work version under the same import paths). If the work version renamed/added product pages, the branch's `app_modes.py` PageSpecs + main_window factories may reference a page the work version doesn't have, or miss one it does. Reconcile: the set of product PageSpecs in `app_modes.py` must match the product pages your work `main_window` can build.
    - If **DIVERGED**: do NOT copy whole. Instead, port the structural changes by hand: introduce the factory methods + `_mount_mode_pages`/`_populate_sidebar`/`switch_mode`/`_start_services_for_mode` skeleton from the branch, but keep the work version's own page set and service wiring. This is the one place a 3-way merge tool (`git merge-file` or your editor's 3-way view) is worth it. Treat the branch as "theirs", `$BASE` as "base", work HEAD as "ours".
  - **Integration points to preserve:** drilldown panel created before any page mounts; `_page_widgets` keyed by `page_id` (str); enablement pages all set `wants_drilldown=True`; `_create_enablement_page()` is a singleton-per-mode that constructs `EnablementPage`, wires `GuruClient` + `EnablementMonitor` + drilldown.

- `src/ui/pages/settings_page.py` — Display tab gains a "Default Mode at Startup" combo (product/enablement/last) writing `app.default_mode`.
  - **Before you overwrite, check X:** `git diff --quiet $BASE HEAD -- src/ui/pages/settings_page.py`. The branch delta is small (+68). If DIVERGED, HAND-MERGE just the combo block; do not clobber the work version's other Settings tabs.

**Validate layer (b):**
```bash
python -c "from src.ui import app_modes as m; print('modes:', m.MODE_PRODUCT, m.MODE_ENABLEMENT); print('startup:', m.resolve_startup_mode())"
QT_QPA_PLATFORM=offscreen python scripts/_p1_boot_smoke.py product 2>&1 | tail -5   # copy this script in layer (f) if not yet present, or run after (f)
```

---

### Layer (c) — Enablement Data Infra (stores / clients / monitors / utilities)

All of these are **NEW** on a pre-enablement work version (confirmed ABSENT on baseline). Copy whole. (Pre-check each with `git cat-file -e HEAD:<path>`; if any unexpectedly EXISTS and is DIVERGED, hand-merge instead.)

**Copy NEW data-layer files** (order does not matter for copying; it matters for import-time, which Python resolves lazily):
```bash
for f in enablement_store enablement_tasks enablement_sources enablement_sim enablement_monitor \
         asana_client asana_setup asana_monitor \
         drive_reader drive_monitor drive_query google_oauth \
         guru_analytics guru_analytics_monitor guru_blocks html_markdown \
         pptx_store zendesk_store doc_reader doc_to_card call_trace; do
  git -C C:/alma-insights show $SRC:src/data/$f.py > C:/alma-insights/src/data/$f.py
done
git -C C:/alma-insights show $SRC:src/data/chat_tools/enablement_tools.py > C:/alma-insights/src/data/chat_tools/enablement_tools.py
```

| Group | Files | Purpose |
|------|-------|---------|
| Spine | `enablement_store.py`, `enablement_tasks.py`, `enablement_sources.py` | Document + card-draft store; task/subtask spine; monitor-source config. |
| Asana | `asana_client.py`, `asana_setup.py`, `asana_monitor.py` | Read-only Asana REST client; GID discovery + board config; poll→auto-task. |
| Drive | `drive_reader.py`, `drive_monitor.py`, `drive_query.py`, `google_oauth.py` | SA + per-user OAuth read client; folder poll→docs→draft; query builder; OAuth (PKCE, keyring). |
| Guru analytics | `guru_analytics.py`, `guru_analytics_monitor.py` | Sync engine (events/stats/verification/comments) + read API; background poll. |
| Content utils | `guru_blocks.py`, `html_markdown.py`, `doc_reader.py`, `doc_to_card.py` | Guru-native block markup; MD↔HTML; .docx/.pdf reader; deterministic doc→card. |
| PowerPoint/Zendesk | `pptx_store.py`, `zendesk_store.py` | Deck CRUD + .pptx export; article/macro drafts. |
| Orchestration | `enablement_monitor.py`, `enablement_sim.py`, `call_trace.py` | Master monitor (Asana+Drive+Guru); demo simulator; `ALMA_TRACE=1` tracer. |
| Chat tools | `chat_tools/enablement_tools.py` | ~19 Renn tools (shared `_*_impl` for both Gemini + Claude paths). |

**MODIFY (data layer):**

- `src/data/guru_client.py` — branch adds analytics + write endpoints (`get_team_id`, `get_analytics`, `get_team_stats`, `list_unverified_cards`, `verify_card`/`unverify_card`, `get_card_comments`, `create_card`, `update_card`, `create_card_comment`, `_request_raw`, `_paged_get`). Delta ~+164.
  - **Before you overwrite, check X:** `git diff --quiet $BASE HEAD -- src/data/guru_client.py`. If CLEAN → copy whole. If DIVERGED, HAND-MERGE: append the new methods; keep the work version's existing client methods. Verify no method-name collisions (`grep -n "def " src/data/guru_client.py | sort`).

- `src/data/pat_store.py` — adds secret keys `asana_api_key`, `google_oauth_user` (and on the branch lineage `guru_email`, `guru_api_token`). Delta +6.
  - **Before you overwrite, check X:** Confirm whether the work version already has Guru keys (`grep -nE "guru_email|guru_api_token|asana_api_key|google_oauth_user" src/data/pat_store.py`). HAND-MERGE missing keys into `_SECRET_KEYS`; never remove keys the work version already declares.

- `src/data/zendesk_client.py` — reused for article/macro read+write. The branch may add small methods.
  - **Before you overwrite, check X:** `git diff --stat $SRC -- src/data/zendesk_client.py` vs your HEAD. If the work version's Source Monitor depends on this file, HAND-MERGE only the new article/macro methods. (Zendesk tab is optional for the two test scripts — see §8.)

- `src/data/chat_tools/registry.py` — registers the enablement tools in both dispatch paths. Delta +79.
  - **Before you overwrite, check X:** `git diff --quiet $BASE HEAD -- src/data/chat_tools/registry.py`. If DIVERGED, HAND-MERGE: add the enablement-tools registration block; keep existing registrations.

- `src/data/chat_tools/semantic_tools.py` — adds the `ALMA_MCP_EXCLUDE_TOOLS` belt-and-braces guard (returns error dict before importing torch when `semantic_search` is excluded). Delta +7.
  - **Before you overwrite, check X:** `grep -n "ALMA_MCP_EXCLUDE_TOOLS" src/data/chat_tools/semantic_tools.py`. If absent, HAND-MERGE the guard near the top of the handler. This is what lets enablement mode boot without torch.

- `src/data/enablement_store.py` import note: it imports `html_markdown`, `guru_blocks`, `doc_to_card`, and `settings_manager`. Ensure all are present (they are, from the copy step above).

**Validate layer (c):**
```bash
python -c "from src.data.enablement_tasks import list_tasks; from src.data.guru_analytics import sync; from src.data.asana_monitor import poll_once; from src.data.enablement_store import save_document; from src.data.html_markdown import markdown_to_html; print('data layer imports OK')"
```

---

### Layer (d) — Enablement UI Pages

All **NEW** (the `src/ui/pages/enablement/` directory is absent on the work version). Copy the whole directory + the shared credentials panel. Copy shared/leaf components before `page.py` (which imports them) — though Python resolves lazily, copy in this order for cleanliness.

```bash
mkdir -p C:/alma-insights/src/ui/pages/enablement
for f in __init__ _common guru_preview rich_editor chat_panel mini_charts slide_view \
         focus_overlay expand_overlay pptx_expand calendar tasks card_picker task_detail \
         pptx_tab zendesk_tab analytics settings workbench page; do
  git -C C:/alma-insights show $SRC:src/ui/pages/enablement/$f.py > C:/alma-insights/src/ui/pages/enablement/$f.py
done
git -C C:/alma-insights show $SRC:src/ui/widgets/credentials_panel.py > C:/alma-insights/src/ui/widgets/credentials_panel.py
```

| Path | Purpose |
|------|---------|
| `enablement/page.py` | `EnablementPage` host; 8 tabs; demo flow; ChatEngine wiring (`task_type="enablement_chat"`, `use_mcp_tools=True`); `_prepare_provider()` reads the provider toggle. |
| `enablement/calendar.py` | Month/week calendar, due-date + guru-card-due chips → Workbench. |
| `enablement/tasks.py` | Task table + inline expand + checklist + AI draft-subtasks. |
| `enablement/workbench.py` | Card preview/rich/markdown views; Import▾ menu; tools hamburger; publish. |
| `enablement/pptx_tab.py` + `slide_view.py` + `pptx_expand.py` | PowerPoint deck list/preview/expand. |
| `enablement/zendesk_tab.py` | Articles/macros, human-gated push. |
| `enablement/analytics.py` + `mini_charts.py` | Guru KPIs, verification donut, due cards, comments→task. |
| `enablement/settings.py` | Connections / Providers (CredentialsPanel) / Sources (Asana+Drive) / Style Guide. **Provider combo lives here.** |
| `enablement/rich_editor.py` + `expand_overlay.py` + `focus_overlay.py` | Rich-text editor + focus overlays. |
| `enablement/chat_panel.py` | Renn assistant panel (ChatEngine loop). |
| `enablement/guru_preview.py` + `_common.py` + `card_picker.py` + `task_detail.py` | Guru-look preview; shared primitives; draft picker; task drilldown. |
| `widgets/credentials_panel.py` | Shared LLM + OAuth panel (used by enablement Settings; optionally product Settings external-only). |

**MODIFY (config prompts)** — these four are NEW; copy whole:
```bash
for p in enablement_card_from_doc enablement_pptx_from_doc enablement_article_from_doc enablement_macro_from_doc; do
  git -C C:/alma-insights show $SRC:config/prompts/$p.txt > C:/alma-insights/config/prompts/$p.txt
done
```

**Validate layer (d):**
```bash
QT_QPA_PLATFORM=offscreen python -c "from src.ui.pages.enablement.page import EnablementPage; from src.ui.widgets.credentials_panel import CredentialsPanel; print('enablement UI imports OK')"
```

---

### Layer (e) — Credentials / Provider Wiring for Bedrock

This layer makes the enablement + Renn chat path resolve to **Claude Bedrock**. See §5 for the full mechanism; the file work is below.

**MODIFY:**

- `src/gemini/client_factory.py` — the **load-bearing** provider change. The branch adds: enablement task-lane default routes; `_enablement_provider()` (reads `enablement.provider`); the `resolve_provider_for_task()` enablement branch; `build_client_for_task()` Claude path with **`force_cli=True` for `enablement_*`**; `_build_claude_client_from_registry(force_cli=...)`; `_resolve_claude_cli_alias()`; and `client.pii_redaction = False` for enablement lanes. Delta +62.
  - **Before you overwrite, check X:** First determine what the work version already has:
    ```bash
    grep -nE "_enablement_provider|force_cli|enablement_card_gen|pii_redaction = False|_resolve_claude_cli_alias" src/gemini/client_factory.py
    ```
    - If `resolve_provider_for_task` exists but the enablement bits are absent (the author's baseline case), HAND-MERGE just the enablement additions (the eight `enablement_*` default routes, `_enablement_provider`, the `if task_type.startswith("enablement_")` branch in `resolve_provider_for_task`, and the `force_cli`/`pii_redaction` logic in `build_client_for_task`). Copy exact text from `git show $SRC:src/gemini/client_factory.py`.
    - If the whole file is CLEAN vs base, copy whole.
    - If DIVERGED with unrelated work-version edits, hand-merge.

- `src/agents/claude_cli_bridge.py` — the Bedrock CLI bridge (`_build_subprocess_env` injects `CLAUDE_CODE_USE_BEDROCK=1` + `AWS_REGION` when `bedrock.enabled`; `_build_cmd` adds `--mcp-config`/`--allowedTools`/`--permission-mode dontAsk`; `set_mcp_config` writes the MCP config).
  - **Before you overwrite, check X:** `grep -nE "CLAUDE_CODE_USE_BEDROCK|set_mcp_config|strict-mcp-config" src/agents/claude_cli_bridge.py`. **On the author's baseline this file was byte-identical to the branch — likely no change needed.** If your grep shows all three present, do nothing. If any are missing, `git diff $SRC -- src/agents/claude_cli_bridge.py` and HAND-MERGE the missing pieces. Do NOT blindly overwrite — this file may carry work-version-specific bridge fixes.

- `src/llm/claude_cli_client.py` and `src/services/chat_engine.py` — on the author's baseline both were **byte-identical** to the branch.
  - **Before you overwrite, check X:** `git diff --quiet HEAD $SRC -- src/llm/claude_cli_client.py src/services/chat_engine.py`. If quiet (identical), do nothing. If not, diff and hand-merge only the deltas relevant to provider-aware tool wiring (`set_mcp_config`, the text-loop vs MCP branch). Do not touch unrelated chat logic.

- `src/llm/claude_tools.py` — adds Claude tool definitions for the 3 new Renn tools (`import_guru_card`, `get_guru_analytics`, `create_task_from_comment`) plus the enablement tool set. Delta is large (+447) because on a pre-enablement work version these definitions are new.
  - **Before you overwrite, check X:** `git diff --quiet $BASE HEAD -- src/llm/claude_tools.py`. If CLEAN → copy whole. If DIVERGED, HAND-MERGE the new tool definitions into `_DISPATCH`/definitions list; keep existing tools.

- `src/mcp/chat_mcp_server.py` — adds enablement tool schemas + parses `ALMA_MCP_EXCLUDE_TOOLS` to filter `TOOL_SCHEMAS`. Delta +306 (new schemas on a pre-enablement baseline).
  - **Before you overwrite, check X:** `git diff --quiet $BASE HEAD -- src/mcp/chat_mcp_server.py`. If CLEAN → copy whole. If DIVERGED, HAND-MERGE: add the enablement `TOOL_SCHEMAS` entries + the `ALMA_MCP_EXCLUDE_TOOLS` filter; keep existing schemas. **Tool parity matters:** the same enablement tools must appear in `chat_tools/registry.py`, `claude_tools.py`, and `chat_mcp_server.py` (test `test_enablement_new_tools.py` checks this 3-way parity).

**Settings to set on the work box** (data/settings.yaml — use `settings_manager`, not raw edits, or set via the in-app Settings UI):
```yaml
enablement:
  provider: claude        # <-- routes ALL enablement_* lanes (incl. Renn chat) to Claude
  demo_mode: true         # demo uses throwaway temp DB; pushes stay local unless creds present
bedrock:
  enabled: true           # injects CLAUDE_CODE_USE_BEDROCK=1
  region: us-west-2       # set the org's region
  use_environment: true   # CLI inherits AWS SSO creds from ~/.aws (ops-ai profile)
```

**Validate layer (e):**
```bash
python - <<'PY'
from src.gemini.client_factory import resolve_provider_for_task
import src.data.settings_manager as sm
sm.set_section("enablement", {"provider": "claude", "demo_mode": True})
print("enablement_chat ->", resolve_provider_for_task("enablement_chat"))   # expect: claude
print("enablement_pptx_gen ->", resolve_provider_for_task("enablement_pptx_gen"))  # expect: claude
PY
```

---

### Layer (f) — Integration-Test Scripts

**NEW** — copy whole:
```bash
git -C C:/alma-insights show $SRC:scripts/validate_live_integrations.py > C:/alma-insights/scripts/validate_live_integrations.py
git -C C:/alma-insights show $SRC:scripts/e2e_asana_to_guru.py > C:/alma-insights/scripts/e2e_asana_to_guru.py
git -C C:/alma-insights show $SRC:scripts/_p1_boot_smoke.py > C:/alma-insights/scripts/_p1_boot_smoke.py
# Optional (offline-LLM PoC, no app wiring; copy only if you want it):
git -C C:/alma-insights show $SRC:scripts/poc_local_card_gen.py > C:/alma-insights/scripts/poc_local_card_gen.py
```
| Path | Purpose | Provider it actually uses |
|------|---------|---------------------------|
| `scripts/validate_live_integrations.py` | Validates Guru + Asana live integrations (auth, data flow, sync) against real accounts. **Does NO LLM authoring** (verified — no `.generate()`, no `build_client_for_task`). | **None.** Provider-agnostic; `enablement.provider`/`bedrock.*` are irrelevant to it. |
| `scripts/e2e_asana_to_guru.py` | Full E2E: Asana task → Calendar render → doc grep → LLM author → Guru publish → verify. | **Gemini CLI, hardcoded.** Stage 4 (`stage_author`) constructs a `GeminiClient` directly (banner literally reads "STAGE 4 — Renn (Gemini) authors") and does **NOT** call `build_client_for_task` / the `enablement.provider` toggle. It will NOT exercise the Bedrock path and **requires a working Gemini CLI**. |
| `scripts/_p1_boot_smoke.py` | Per-mode boot smoke (page/service expectations, timing, switch round-trip). Self-sets `QT_QPA_PLATFORM=offscreen` internally. | None. |

> **Bedrock-correctness caveat (read this).** Neither script proves the Bedrock authoring path. `validate_live_integrations.py` does no authoring at all; `e2e_asana_to_guru.py` hardcodes Gemini for Stage 4. **The only thing in this backport that authors via Claude Bedrock is the in-app Renn chat** (`EnablementPage` → `ChatEngine(task_type="enablement_chat")` → the `enablement.provider` toggle), exercised by interacting with the running app, not by these scripts. To verify Bedrock authoring end-to-end you must: (a) confirm provider routing with the §7.3 assertion (proves `resolve_provider_for_task` returns `"claude"`), and (b) drive the Renn chat in the live app (§7.5 below). If you only need the two scripts green, you need a working **Gemini CLI** for the e2e script — do not waste time chasing why "the e2e script isn't using Bedrock"; by design it never does.

**Dependency closure these scripts need** (all delivered by layers (c)–(e)): `connection_factory`, `pat_store`, `settings_manager`, `guru_client` (extended), `gemini_client` (the e2e script's Stage 4 imports `GeminiClient` directly — so a working **Gemini CLI** is a runtime prerequisite for the e2e script, independent of Bedrock), plus the enablement infra (`enablement_tasks`, `enablement_store`, `enablement_sources`, `asana_client`, `asana_setup`, `asana_monitor`, `guru_analytics`, `guru_analytics_monitor`, `html_markdown`, `guru_blocks`, `doc_to_card`, `doc_reader`). The scripts do NOT import `drive_*`, `pptx_store`, `zendesk_store`, `enablement_monitor`, `enablement_sim`, or `call_trace` (verified by grepping their imports), but those are copied anyway in layer (c) for the full workbench. Migrations needed by the scripts: **027** (`enablement_tasks`, `asana_users`) + **028** (`guru_events`, `guru_card_verification`, …) + **031** (`guru_content_drafts.content_html`), atop pre-existing **003** (`guru_content_drafts`). `029`/`030` are not touched by either script (the scripts do not exercise PowerPoint/Zendesk). All are present after layers (c)–(e).

---

## 5. Provider Wiring — Making Enablement + Renn Run on Claude Bedrock

The toggle is **`enablement.provider`** in `data/settings.yaml`. The mechanism, end to end:

1. **`enablement.provider = "claude"`** → `_enablement_provider()` in `src/gemini/client_factory.py` returns `"claude"`.
2. **`resolve_provider_for_task("enablement_*")`** short-circuits to that value for every enablement lane (card gen, extract, triage, subtasks, **chat**, pptx, article, macro). It wins over `ai.task_routing.override_all` and the per-task default routes.
3. **`build_client_for_task()`** sees `provider == "claude"` and, because the task starts with `enablement_`, sets **`force_cli=True`** → `_build_claude_client_from_registry(force_cli=True)` returns a `ClaudeCliClient` (never the direct Anthropic API). This is the BAA-safe Bedrock path. It also sets `client.pii_redaction = False` for enablement lanes (product docs are not PHI; aggressive redaction wrecks card quality — do not re-enable).
4. **`ClaudeCliBridge._build_subprocess_env()`** injects `CLAUDE_CODE_USE_BEDROCK=1` and `AWS_REGION` **only when `bedrock.enabled=true`**. With `use_environment=true` (default), the `claude` CLI inherits AWS SSO creds from `~/.aws` (the org's `ops-ai` profile). So **both** `enablement.provider=claude` AND `bedrock.enabled=true` must be set.
5. **Renn chat path:** `EnablementPage._setup_engine()` builds `ChatEngine(task_type="enablement_chat", use_mcp_tools=True, tools_enabled=True)`. `_prepare_provider()` reads `resolve_provider_for_task("enablement_chat")`:
   - `claude` → drops any warm Gemini bridge, sets `engine.set_client(None)`; Claude builds a client per message and exposes tools via **native MCP** (`set_mcp_config()` → `--mcp-config`/`--allowedTools mcp__*__*`/`--permission-mode dontAsk`). This is why Claude does NOT fabricate ticket IDs (it gets real tool execution, not text-injected results).
   - `gemini` → boots the warm `ReportBridgeClient` ACP session (text TOOL_CALL loop).

**Required preconditions on the work box (from research, restated):**
- `enablement.provider = "claude"` (settings) — routes the **in-app Renn chat** and all `enablement_*` lanes that go through `build_client_for_task` to Claude Bedrock.
- `bedrock.enabled = true` + `bedrock.region` (settings).
- AWS SSO profile `ops-ai` configured in `~/.aws` (org/ops step, not app code).
- `claude` CLI on PATH (or `claude.cli_path` set).
- Model alias resolvable to `sonnet`/`haiku`/`opus` (the CLI rejects full ids; `_resolve_claude_cli_alias()` maps by substring, defaults to `sonnet`).
- Demo DB exists (the page creates `alma_enablement_demo*.db` in temp on first scan); tools read it via `ALMA_DB_PATH`.
- Do NOT run Gemini and Claude simultaneously for one session; the toggle change should start a fresh session.

**Scope boundary — what the toggle does and does NOT reach:**
- ✅ Reaches: the in-app Renn chat (`EnablementPage`), and any code path that calls `build_client_for_task("enablement_*")`.
- ❌ Does NOT reach: `scripts/e2e_asana_to_guru.py` Stage 4, which constructs a `GeminiClient` directly. That script still needs a **working Gemini CLI** regardless of the toggle. If your only goal is the two integration scripts, Bedrock setup is required by *neither* script; the Gemini CLI is required by the e2e script. The Bedrock settings matter for the live app, not the scripts.

**Files cited:** `src/gemini/client_factory.py` (`_enablement_provider`, `resolve_provider_for_task`, `build_client_for_task`, `_build_claude_client_from_registry`, `_resolve_claude_cli_alias`); `src/agents/claude_cli_bridge.py` (`_build_subprocess_env`, `_build_cmd`, `set_mcp_config`); `src/llm/claude_cli_client.py` (`set_mcp_config`); `src/ui/pages/enablement/page.py` (`_setup_engine`, `_prepare_provider`, `_build_mcp_config`, `_ensure_warm_bridge`); `src/ui/pages/enablement/settings.py` (`_provider_combo`, `_save_provider`).

---

## 6. KNOWN ISSUE — Gemini Native Tool-Calling (do NOT re-fix here)

Gemini's CLI/ACP native tool-calling is **architecturally broken**: one-shot Gemini CLI prompts hang on tool-use approval in headless mode (no TTY), and `gemini-3-flash-preview` spawns native agents that stall 300s+. The codebase already worked around this. **The demo on the work box runs on Claude Bedrock, so you will not hit the Gemini tool-hang at all.** Therefore: **do NOT attempt a new Gemini tool-calling fix as part of this backport.** If you touch the chat/tool path, REUSE the proven techniques already in the copied files — do not invent new ones:

1. **NDJSON-primary scan output** (`config/prompts/nlp_classify.txt`): prompt forbids tools; model emits pure NDJSON; `StreamParser` extracts JSON; local `ToolRegistry` executes. No tool schema exposed to the model.
2. **Prompt "TOOL CONSTRAINT" sections** (report prompts): explicitly name and forbid `write_file`/`read_file`/`edit`/`run_shell_command`/`web_fetch`/`web_search`/sub-agents; "if denied, do not retry, emit JSON inline."
3. **ACP permission allowlist** (`src/agents/acp_bridge.py`): auto-approve only `mcp_*` toolCallIds, deny all native tools — prevents approval-prompt stalls.
4. **Text `TOOL_CALL`/`TOOL_RESULT` loop for Gemini chat** (`src/services/chat_engine.py`, `use_mcp_tools=False` path) with `TOOL_PROMPT_ADDENDUM` grounding ("never invent a ticket ID").
5. **Native MCP for Claude chat** (`src/agents/claude_cli_bridge.set_mcp_config()` + `--mcp-config`/`--allowedTools`/`--permission-mode dontAsk`): Claude refuses text-injected tool results, so it must get tools natively. **This is the path the Bedrock demo uses.**
6. **Provider-aware wiring** (`EnablementPage._prepare_provider()`): Gemini → text loop; Claude → native MCP. Read the provider at dispatch time, not init time.

If a tool round-trip misbehaves under Bedrock-Claude, the cause is almost always MCP wiring (config path, `--allowedTools` pattern, or `ALMA_DB_PATH` env), NOT the Gemini hang — debug the MCP config, do not reach for the Gemini workarounds.

---

## 7. Validation

Run from the work-version repo root, in the work venv. Kill zombies first (per project protocol):
```bash
wmic process where "commandline like '%alma_mcp_server%'" call terminate 2>/dev/null || true
```

### 7.1 Import / boot smoke
```bash
QT_QPA_PLATFORM=offscreen python scripts/_p1_boot_smoke.py product 2>&1 | tail -8     # ~2.5–2.7s, torch loaded
QT_QPA_PLATFORM=offscreen python scripts/_p1_boot_smoke.py enablement 2>&1 | tail -8  # ~0.4–0.5s, NO torch
```
Green = both report their expected page/service sets and the runtime switch round-trips. (`_p1_boot_smoke.py` already self-sets `QT_QPA_PLATFORM=offscreen` internally via `setdefault`, so the prefix is harmless-redundant; the positional mode arg — `product`/`enablement` as a string — is what it reads from `sys.argv[1]`.)

### 7.2 Enablement test suites (run in groups of 3–4 files; full `tests/` hangs on Windows)
```bash
python -m pytest tests/test_app_modes.py tests/test_home_page.py tests/test_mcp_tool_exclusion.py -x -q
python -m pytest tests/test_design_tokens.py tests/test_icons.py tests/test_qss_builder.py -x -q
python -m pytest tests/test_html_markdown.py tests/test_workbench_import.py tests/test_style_guide.py -x -q
python -m pytest tests/test_guru_analytics_sync.py tests/test_guru_client_analytics.py -x -q
python -m pytest tests/test_analytics_page.py tests/test_calendar_guru_chips.py tests/test_enablement_new_tools.py -x -q
python -m pytest tests/test_enablement_fidelity.py tests/test_enablement_ui_live.py tests/test_guru_wip_placeholder.py -x -q
```
Copy any of these test files that are absent on the work version from the branch first:
`for t in test_app_modes test_home_page test_mcp_tool_exclusion test_design_tokens test_icons test_qss_builder test_html_markdown test_workbench_import test_style_guide test_guru_analytics_sync test_guru_client_analytics test_analytics_page test_calendar_guru_chips test_enablement_new_tools test_enablement_fidelity test_enablement_e2e; do git show $SRC:tests/$t.py > tests/$t.py 2>/dev/null; done`
Green = each group reports all-passed (the branch reports ~73 enablement feature tests + ~1,390 across the redesign suites, all green). A handful of `live_db`-marked or live-creds tests may skip without credentials — skips are acceptable; failures are not.

### 7.3 Provider routing assertion (Bedrock)
```bash
python - <<'PY'
import src.data.settings_manager as sm
sm.set_section("enablement", {"provider":"claude","demo_mode":True})
sm.set_section("bedrock", {"enabled":True,"region":"us-west-2","use_environment":True})
from src.gemini.client_factory import resolve_provider_for_task
assert resolve_provider_for_task("enablement_chat")=="claude"
assert resolve_provider_for_task("enablement_card_gen")=="claude"
print("provider routing -> claude: OK")
PY
```

### 7.4 The two integration scripts
Prerequisites differ per script (see §5 scope boundary): the validator needs Guru+Asana creds only; the e2e script ALSO needs a working **Gemini CLI** (Stage 4 authors via Gemini, not Bedrock).

```bash
# --- Live validator (Guru + Asana). Does NO LLM authoring; Bedrock/Gemini irrelevant. ---
# CLI: it uses a manual `sys.argv` check, NOT argparse, so `--help` does NOT print help (it just runs).
# `--save` persists creds it reads from env into pat_store. Verify flags by reading the file header:
sed -n '1,30p' scripts/validate_live_integrations.py    # read the usage banner at the top
python scripts/validate_live_integrations.py            # green = auth OK + Guru sync rows + Asana poll creates tasks
# (or, to also persist env creds into pat_store:)
# python scripts/validate_live_integrations.py --save

# --- End-to-end (Asana task -> Calendar -> doc grep -> author [GEMINI CLI] -> Guru publish -> verify) ---
# CLI: uses argparse. Real flags are --cleanup and --collection (NOT --save). `--help` works here.
python scripts/e2e_asana_to_guru.py --help
python scripts/e2e_asana_to_guru.py --cleanup          # --cleanup is OPT-IN; without it the test task+draft are LEFT in Asana/Guru
# Pin a specific Guru collection if the default isn't present in your workspace:
# python scripts/e2e_asana_to_guru.py --cleanup --collection "Welcome to Guru!"
```
"Green" for `validate_live_integrations.py`: connection tests pass for Guru and Asana; a sync writes to `guru_events`/`guru_card_verification`; `asana_monitor.poll_once` creates `enablement_tasks`.
"Green" for `e2e_asana_to_guru.py`: all stages report OK, the CalendarPage renders the seeded task, Stage 4 (Gemini) produces a card body, a real Guru draft is created via `enablement_store.save_card_draft`/`publish_draft`, and Stage 6 verifies it via the live Guru API. **Cleanup is NOT automatic** — pass `--cleanup` to delete the test Asana task + Guru draft, or remove them by hand afterward. If live creds/SSO are unavailable, the script fails at the first live call (it has no built-in dry-run); read its banner output to see which stage failed.

### 7.5 In-app Bedrock authoring check (the ONLY way to verify the Claude path)
The scripts do not exercise Bedrock authoring (§5 scope boundary). To prove Renn authors on Claude Bedrock:
1. Set `enablement.provider=claude`, `bedrock.enabled=true`, `bedrock.region`, sign into AWS SSO (`ops-ai`), confirm the `claude` CLI runs (`claude --version`).
2. Launch the app in enablement mode: `python main.py --mode enablement`.
3. Open the Workbench tab → run "Scan all (demo)" → open the Renn chat and ask it to draft or revise a card.
4. Confirm a real Bedrock call (not Gemini): with `ALMA_TRACE=1 python main.py --mode enablement`, the trace shows the `claude` CLI subprocess with `CLAUDE_CODE_USE_BEDROCK=1` in its env, and the chat returns grounded content with no fabricated ticket IDs (native MCP tool execution).

---

## 8. Explicit DO-NOT-TOUCH List

Leave these alone to avoid breaking the work version or clobbering Mac work:
- **Do not modify the product analysis pipeline:** `src/agents/scan_orchestrator.py`, `worker_agent.py`, `stream_parser.py`, `tool_registry.py`, `acp_bridge.py`, `batch_packer.py`, `rate_governor.py`, and `config/prompts/nlp_classify.txt`. The Gemini tool-suppression there is LIVE and correct (see §6).
- **Do not "fix" the Gemini native tool-calling path.** Out of scope; demo runs on Bedrock-Claude.
- **Do not touch `src/data/db_manager.py::initialize()`** (the 650-line `executescript`) except via the additive migrations 027–031. A stray edit silently breaks later CREATEs.
- **Do not renumber or edit existing migrations `001`–`026`.** Only add `027`–`031`.
- **Do not alter Source Monitor internals** (`src/ui/pages/source_monitor/`, `src/data/source_baseline.py`, `src/ui/widgets/rate_chart.py`) — product-only, unrelated, and doc-protected.
- **Do not change the bedrock env-var contract** in `claude_cli_bridge.py` (`bedrock.enabled` / `CLAUDE_CODE_USE_BEDROCK` / `use_environment`). A future "Phase 0" migration replaces it; do not pre-empt that here.
- **Do not weaken PHI routing:** keep `force_cli=True` for `enablement_*` Claude tasks (no direct Anthropic API).
- **Do not re-enable `pii_redaction` for enablement lanes.**
- **Do not push to `origin/enablement-content-tabs`** or any Mac branch. Do not `git merge`/`checkout` the Mac branch into the work tree wholesale.
- **Do not delete the work version's own product pages or services** while wiring `app_modes`/`main_window` — the product mode must retain every page the work app currently ships.
- **Do not commit credentials** (`data/settings.yaml` with real keys, `data/*.db`, keyring blobs).

---

## 9. Rollback Procedure

Everything is on the `enablement-backport` branch and the `pre-enablement-backport` tag, so rollback is clean.

1. **Abandon the working changes (keep the branch for later):**
   ```bash
   git -C C:/alma-insights switch -   # back to the work mainline
   # The mainline is untouched; nothing was committed onto it.
   ```
2. **Full hard reset of the backport branch to the pre-backport state** (if you committed and want to discard):
   ```bash
   git -C C:/alma-insights switch enablement-backport
   git -C C:/alma-insights reset --hard pre-enablement-backport
   ```
3. **Delete the worktree + branch entirely:**
   ```bash
   git -C C:/alma-insights worktree remove ../alma-enablement-backport 2>/dev/null || true
   git -C C:/alma-insights branch -D enablement-backport
   ```
4. **Restore the database** (migrations are additive/idempotent, but to be safe):
   ```bash
   cp C:/alma-insights/data/local_warehouse.db.bak-pre-enablement C:/alma-insights/data/local_warehouse.db
   ```
   (Migrations 027–031 only ADD tables/columns; they do not drop or alter existing data. If you prefer to keep the new tables, no DB rollback is needed — the work-mainline code simply ignores them.)
5. **Revert dependencies** (only if they cause conflicts):
   ```bash
   pip uninstall -y python-pptx google-api-python-client google-auth google-auth-oauthlib
   ```
6. **Settings:** set `enablement.provider` back to `gemini` (or remove the `enablement`/`bedrock` sections you added) via the Settings UI or `settings_manager`.

The `pre-enablement-backport` tag is your guaranteed return point; the work mainline branch was never committed to.

---

## Appendix A — Counts

- **Migrations applied:** 5 (`027`–`031`), in order; `027` (adds `source_ref`) & `031` (adds `content_html`) both ALTER `guru_content_drafts`, so both depend on migration `003`. Apply via `SchemaMigrator`, not raw `sqlite3 <` (§3.2).
- **New pip deps:** exactly 4 — `python-pptx==1.0.2`, `google-api-python-client==2.149.0`, `google-auth==2.36.0`, `google-auth-oauthlib==1.2.1`. **NOT `python-docx`** (doc_reader is stdlib-only). Verified against `git diff base..branch -- requirements.txt`.
- **New files to copy (verified counts):** design system 5; mode/home 2; data infra **21** `src/data/*.py` (+1 `chat_tools/enablement_tools.py`); enablement UI 20 (+1 `widgets/credentials_panel.py`); config prompts 4; scripts 3 (+1 optional PoC); migrations 5; plus test files as needed. ~62 NEW source/config files.
- **Shared files to MODIFY (pre-check each):** `main.py`, `src/ui/main_window.py`, `src/ui/theme.py`, `src/ui/pages/settings_page.py`, `src/data/guru_client.py`, `src/data/pat_store.py`, `src/data/zendesk_client.py`, `src/data/chat_tools/registry.py`, `src/data/chat_tools/semantic_tools.py`, `src/gemini/client_factory.py`, `src/llm/claude_tools.py`, `src/mcp/chat_mcp_server.py`, `requirements.txt`, and (only if your work version diverges) `src/agents/claude_cli_bridge.py`, `src/llm/claude_cli_client.py`, `src/services/chat_engine.py`. ~16 MODIFY targets.

## Appendix B — Gaps / Uncertainties (genuinely need human/work-box input)
1. **Work-version divergence is unknown to the author — this is the one true unknown.** This plan was authored against a checkout where `main` already had the Bedrock bridge (`claude_cli_bridge.py`, `claude_cli_client.py`, `chat_engine.py` byte-identical to the branch) + `resolve_provider_for_task`/`_resolve_claude_cli_alias` in `client_factory.py`. **The actual WORK box may be older or carry local edits in any shared file.** The §2.3 divergence scan is mandatory — its output decides copy-whole vs. hand-merge for every shared file. (Everything else in this plan was re-verified against the branch and is factual; see the verification notes inline.)
2. **`main_window.py` is the chief merge risk.** Branch delta vs base is +467/−211. If your work version has heavy local changes there, the factory/mode refactor must be ported by hand (3-way). Also: the branch's `app_modes.py` product PageSpecs must match the exact set of product pages your work `main_window` can build — reconcile if the work version added/renamed/removed product pages.
3. **DB path** is `data/local_warehouse.db` per CLAUDE.md but MEMORY.md mentions `data/alma_insights.db`. Confirm which exists on the work box (`ls data/*.db`) and substitute that path in §3, §7, and §9. (On this author's box, `data/local_warehouse.db` is the live one.)
4. **AWS `ops-ai` SSO + `claude` CLI availability** are org/environment prerequisites outside this repo; the in-app Renn-on-Bedrock path (§7.5) fails at auth if they are not set up. Separately, the **e2e script needs a working Gemini CLI** (its Stage 4 is Gemini-hardcoded — not Bedrock; §5 scope boundary).
5. **Migration numbering collision** is NOT expected (the work baseline's highest was `026`; `027`–`031` are free here too), but if the work version independently shipped a `027`–`031`, reconcile numbering before applying (§3.2 pre-check). Apply via `SchemaMigrator` (§3.2), never raw `sqlite3 <` (031 is only idempotent through the migrator).

**Resolved during review (no longer open questions):**
- ~~`python-docx` pin~~ — **not needed.** `doc_reader.py` uses only stdlib (`zipfile`+`ElementTree`); it never imports `python-docx`. Do not add it. (§3.1)
- ~~Integration-script `--help`/flags unread~~ — read and corrected in §7.4: `validate_live_integrations.py` uses a manual `sys.argv` check (`--save` only; `--help` does not print help — read its header instead); `e2e_asana_to_guru.py` uses argparse (`--cleanup`, `--collection`; cleanup is OPT-IN and NOT automatic).
- ~~"scripts author via Claude Bedrock"~~ — **false; corrected.** `validate_live_integrations.py` does no LLM authoring; `e2e_asana_to_guru.py` Stage 4 hardcodes `GeminiClient`. Only the in-app Renn chat uses the Bedrock toggle (§5 scope boundary, §7.5).
- Data-layer copy loop (§4 layer c) verified **exactly complete**: all 21 NEW `src/data/*.py` files covered, no extras, no omissions. Enablement UI loop (20 files) and config-prompt loop (4 files) likewise complete.
