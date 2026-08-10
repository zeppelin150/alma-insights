# PORT: Asana board management + Zendesk PAT card

**Target: branch tip `4a2418d` (code target `fbe370f`). 2 migrations. ~15 minutes.**

You are executing a port. You are **not** debugging, improving, or fixing anything.

---

## THE RULE

**You will not edit a single file. Not one.**

Every code change arrives through **one `rsync`** in STEP 4.

> **If you are about to use Edit, Write, `nano`, `vim`, `cat >`, `sed -i`, or any
> other means of changing a file's contents — STOP. You have made a mistake.
> Report what you were about to do and wait.**

---

## NEVER

These are absolute **except** where a numbered step below prescribes the command verbatim. The only commands in this entire guide that write anything are: `git branch` (STEP 2), the `.backup` (STEP 2), the `rsync` (STEP 4), `db.initialize()` (STEP 5), and `git fetch` + `git reset --mixed` (STEP 8). **If you are about to run a mutating command that is not one of those five, you have made a mistake.**

1. **NEVER** run `git pull`, `git checkout`, `git merge`, `git cherry-pick`, `git apply`, `git stash`, `git clean`, `git restore`, `git push`, or `git reset --hard` — at any point, including as cleanup after a STOP. (`git fetch` and `git reset --mixed` appear once, in STEP 8, and nowhere else.)
2. **NEVER** pass `--delete` to `rsync`.
3. **NEVER** run the `rsync` line by itself. See STEP 4 — an unset variable makes it copy the filesystem root.
4. **NEVER** move, rename, delete, or overwrite any file outside the `rsync` in STEP 4.
5. **NEVER** touch `data/settings.yaml` or `~/.alma-insights/ui_state.json`.
6. **NEVER** delete a `-wal` or `-shm` file.
7. **NEVER** run `npm install` or `npm run build`. This port changes zero `web/` files.
8. **NEVER** run `pip install`, `python -m venv`, or fall back to system `python3`.
9. **NEVER** pipe a migration through the `sqlite3` CLI. STEP 5 applies them.
10. **NEVER** run `pytest tests/` with no arguments. It hangs.
11. **NEVER** "fix" a failing test, create a file reported missing, or install a missing package. Report and STOP.
12. **NEVER** continue past a STOP, and never run later steps to fill in the report.

---

## WHAT MAY CHANGE

**16 files**, or **14** if your baseline already has the PAT card (STEP 1 tells you which).

```
A  migrations/054_task_board_source.sql
A  migrations/055_task_board_links.sql
A  tests/test_asana_board_management.py
M  assets/help/settings/connect-asana.md
M  src/data/asana_monitor.py
M  src/data/asana_setup.py
M  src/data/chat_tools/enablement_tools.py
M  src/data/chat_tools/registry.py
M  src/data/enablement_tasks.py
M  src/llm/claude_tools.py
M  src/mcp/chat_mcp_server.py
M  src/ui/pages/enablement/page.py
M  src/ui/pages/enablement/settings.py
M  tests/test_help_claims_settings.py
M  src/ui/widgets/credentials_panel.py     <- 16-file baselines only
M  tests/test_credentials_panel.py         <- 16-file baselines only
```

**No `web/`. No `requirements.txt`. No `package.json`. No `installer/`.**
If any of those ever appears as changed — **STOP**.

---

## STEP 0 — Resolve the three paths (nothing works until these are right)

A human gives you the checkout path. **Do not search the disk for it** — the Express installer leaves a second copy of the tree that looks identical and is the wrong target.

Fill in the path, then run this **as one block**:

```bash
DEST="/Users/<user>/<exact path to checkout>"
cd "$DEST" || { echo "DEST BAD — STOP"; exit 1; }
pwd
git rev-parse --show-toplevel
ls migrations/053_zendesk_versions.sql data/local_warehouse.db
```

**CONTINUE if** `pwd` and `git rev-parse --show-toplevel` print the **same** path, and both files exist.
**STOP otherwise.** Do not go hunting for a different directory.

Now the interpreter — as one block:

```bash
DEST="/Users/<user>/<exact path to checkout>"
VENV="$DEST/.venv/bin/python"      # confirm this path with the human first
"$VENV" -c "import PySide6, sys; print(sys.executable)"
"$VENV" -c "from PySide6.QtTest import QTest; print('QtTest OK')"
```

**CONTINUE if** both print. **STOP if** either raises. Do not use `python3`, do not create a venv, do not `pip install`. (`QtTest` is needed by the new test file; without it STEP 6 fails as a collection error, which is an environment gap, not a port failure.)

**Write `DEST` and `VENV` down. Every block below re-declares them — that is deliberate, because shell variables do not survive between commands.**

---

## STEP 1 — Confirm the baseline

```bash
git log --oneline -1
```

| If the SHA is | Then |
|---|---|
| `a8ead66` `4a4a132` `8b15915` `9c4a697` `9618db1` | **16 files** will change |
| `d018107` `d2ae828` | **14 files** — the PAT card is already installed |
| anything else | **STOP.** Report the SHA. |

Write down which count applies. You will check it in STEP 4.

```bash
sqlite3 data/local_warehouse.db "SELECT filename FROM schema_migrations WHERE filename LIKE '051%' OR filename LIKE '053%' OR filename LIKE '054%' OR filename LIKE '055%' ORDER BY filename;"
```

**CONTINUE if** exactly `051_zendesk_mirror.sql` and `053_zendesk_versions.sql` print.
**STOP if** either is missing — wrong guide, the Zendesk block is not installed.
**STOP if** `054` or `055` prints — already applied.

Other migrations may or may not be present. That is not a signal.

---

## STEP 2 — Back up, and record the starting state

**Close the app first.** Quit from the UI. Do not `kill -9`.

```bash
DEST="/Users/<user>/<exact path to checkout>"
cd "$DEST"
mkdir -p ~/alma_port_backup
git branch pre-asana-port
```

**STOP if** it says the branch already exists — a previous attempt got this far, and that branch is the pristine restore point. **Never** `git branch -f` or `-D` it. Report and wait.

```bash
DEST="/Users/<user>/<exact path to checkout>"
cd "$DEST"
git rev-parse pre-asana-port
sqlite3 data/local_warehouse.db ".backup 'data/local_warehouse.pre054.db'"
ls -l data/local_warehouse.pre054.db
git status --porcelain -uall | sort > ~/alma_port_backup/status_before.txt
wc -l < ~/alma_port_backup/status_before.txt
```

**CONTINUE if** the `.pre054.db` exists and is non-zero.
**STOP otherwise.** Do not `cp` the database — `.backup` is the only correct way.

The `status_before.txt` file is how STEP 4 tells your port apart from pre-existing local mess. Do not skip it.

**Now check that the port's own files have no uncommitted local edits** — `rsync` overwrites in place with no backup:

```bash
DEST="/Users/<user>/<exact path to checkout>"
cd "$DEST"
git status --porcelain -- \
  src/data/asana_monitor.py src/data/asana_setup.py src/data/enablement_tasks.py \
  src/data/chat_tools/enablement_tools.py src/data/chat_tools/registry.py \
  src/llm/claude_tools.py src/mcp/chat_mcp_server.py \
  src/ui/pages/enablement/page.py src/ui/pages/enablement/settings.py \
  src/ui/widgets/credentials_panel.py assets/help/settings/connect-asana.md
```

**CONTINUE if** this prints **nothing**.
**STOP if** it prints anything — there is uncommitted local work in a file this port overwrites. Report the list. Do not commit it, do not stash it, do not proceed.

---

## STEP 3 — Get the code

**A human downloads this URL in a browser.** You do not download it. Do not use `curl`, `git`, or `gh`.

```
https://github.com/zeppelin150/alma-insights/archive/refs/heads/enablement-content-tabs.zip
```

When you are given the path:

```bash
ZIP="$HOME/Downloads/alma-insights-enablement-content-tabs.zip"
ls -l "$ZIP"
```

**STOP if** the file is missing — Safari may have auto-expanded it. Ask the human to re-download with auto-expand off.

```bash
ZIP="$HOME/Downloads/alma-insights-enablement-content-tabs.zip"
mkdir -p ~/alma_port_incoming
cd ~/alma_port_incoming
unzip -q "$ZIP"
ls -d ~/alma_port_incoming/alma-insights-*
xattr -rd com.apple.quarantine ~/alma_port_incoming/alma-insights-* 2>/dev/null || true
ls ~/alma_port_incoming/alma-insights-*/migrations/054_task_board_source.sql \
   ~/alma_port_incoming/alma-insights-*/migrations/055_task_board_links.sql
```

Use command-line `unzip`. Do not double-click in Finder.

**CONTINUE if** exactly one `alma-insights-*` directory exists and both migrations print.
**STOP if** there are two directories (an earlier attempt left one) or a migration is missing.

---

## STEP 4 — Sync (the only write to the project tree)

**Run this entire block as ONE command.** The guards exist because an unset variable turns the `rsync` into a recursive copy of the filesystem root.

```bash
DEST="/Users/<user>/<exact path to checkout>"
SRC=$(ls -d ~/alma_port_incoming/alma-insights-* 2>/dev/null | head -1)
[ -n "$SRC" ] && [ -d "$SRC/migrations" ] || { echo "SRC BAD — STOP"; exit 1; }
[ -n "$DEST" ] && [ -d "$DEST/.git" ] || { echo "DEST BAD — STOP"; exit 1; }
echo "SRC=$SRC"; echo "DEST=$DEST"
rsync -a --exclude 'docs/ZENDESK_BLOCK_PORT_GUIDE.md' --exclude 'docs/PORT_ASANA_PAT.md' "$SRC"/ "$DEST"/
echo "rsync exit=$?"
```

**STOP if** either guard fires, or `rsync exit` is not `0`. Never re-run the `rsync` line on its own.

Now compare against the state you recorded in STEP 2:

```bash
DEST="/Users/<user>/<exact path to checkout>"
cd "$DEST"
git status --porcelain -uall | sort > ~/alma_port_backup/status_after.txt
diff ~/alma_port_backup/status_before.txt ~/alma_port_backup/status_after.txt
```

**CONTINUE if** every `>` line in the diff names a file from the WHAT MAY CHANGE list, and the count matches what STEP 1 told you (16 or 14).

**STOP if** any `>` line names a file outside that list — especially anything under `web/`, `src/ui/web/`, `requirements.txt`, or `data/`.
**STOP if** any `<` line appears (a file that existed before is now gone).

This diff — not a raw line count — is the gate. Pre-existing untracked files on the Mac appear in both files and cancel out.

---

## STEP 5 — Apply migrations 054 and 055

App still closed.

```bash
DEST="/Users/<user>/<exact path to checkout>"
VENV="$DEST/.venv/bin/python"
cd "$DEST"
pwd
test -f data/local_warehouse.db || { echo "NO DB HERE — WRONG DIRECTORY — STOP"; exit 1; }
"$VENV" -c "
from src.data.db_manager import DatabaseManager
db = DatabaseManager(); db.initialize()
rows=[r[0] for r in db.conn.execute('SELECT filename FROM schema_migrations ORDER BY filename')]
print('count', len(rows)); print(rows[-3:])
"
```

The `cd` and the `test -f` are load-bearing: `DatabaseManager()` resolves the database **relative to the working directory**, so from the wrong place it silently creates a brand-new empty warehouse and every later check reads that instead.

**CONTINUE if** the tail is exactly:

```
['053_zendesk_versions.sql', '054_task_board_source.sql', '055_task_board_links.sql']
```

**STOP if** `054` or `055` is missing. Do not apply them by hand.

```bash
DEST="/Users/<user>/<exact path to checkout>"
cd "$DEST"
sqlite3 data/local_warehouse.db "PRAGMA table_info(enablement_tasks);" | grep -c board_source_id
sqlite3 data/local_warehouse.db "SELECT name FROM sqlite_master WHERE name='task_board_links';"
```

**CONTINUE if** the first prints `1` and the second prints `task_board_links`. **STOP otherwise.**

---

## STEP 6 — Tests

Three commands, separately. Do not combine them. Do not add a fourth.

```bash
DEST="/Users/<user>/<exact path to checkout>"; cd "$DEST"; QT_QPA_PLATFORM=offscreen "$DEST/.venv/bin/python" -m pytest tests/test_asana_board_management.py -q
```

Expect **43 passed**.

```bash
DEST="/Users/<user>/<exact path to checkout>"; cd "$DEST"; QT_QPA_PLATFORM=offscreen "$DEST/.venv/bin/python" -m pytest tests/test_credentials_panel.py -q
```

Expect **19 passed**.

```bash
DEST="/Users/<user>/<exact path to checkout>"; cd "$DEST"; QT_QPA_PLATFORM=offscreen "$DEST/.venv/bin/python" -m pytest tests/test_help_claims_settings.py -q
```

Expect **119 passed**.

`QT_QPA_PLATFORM=offscreen` is required. If your shell rejects that prefix syntax, set the variable your shell's way — **do not drop it.**

**If any count is lower — STOP and paste the full pytest output.** Do not edit a test, do not edit source, do not investigate further.

**These three commands are the entire verification. Do not add a fourth.** Every changed source file is covered: `test_asana_board_management.py` reaches `asana_setup.py`, `asana_monitor.py`, `enablement_tasks.py`, `enablement_tools.py`, `claude_tools.py`, `chat_mcp_server.py`, `page.py`, `settings.py`, and asserts both new tools are present in `chat_tools/registry.py` (line 826). `credentials_panel.py` is covered by `test_credentials_panel.py`, and `connect-asana.md` by `test_help_claims_settings.py`.

---

## STEP 7 — Launch and look — **A HUMAN PERFORMS THIS STEP**

You run the launch command and then stop. A human reads the screen and reports back. You must not screenshot, must not substitute SQL or `grep` evidence for a visual check, and must not pass `--no-splash` (it hides the thing item 1 asks about).

First, capture the ground truth the human will check against:

```bash
DEST="/Users/<user>/<exact path to checkout>"
cd "$DEST"
sqlite3 data/local_warehouse.db "SELECT source_id, display_name FROM monitor_sources WHERE source_id LIKE 'asana:%';"
```

Note how many rows print. **This is the number the Asana panel must agree with.**

```bash
DEST="/Users/<user>/<exact path to checkout>"
cd "$DEST"
"$DEST/.venv/bin/python" main.py
```

The human reports these four:

1. Splash "Database integrity" reads **Schema v55**. (This re-reads the ledger STEP 5 already checked — it confirms the app is pointed at the database you migrated, nothing more. If it reads `v53`, the app is on a *different* database file. STOP.)
2. App reaches Home with no error dialog.
3. Settings → **Credentials** shows a **Zendesk** card with Subdomain, Email, API token.
4. Settings → **Sources → Asana** shows a number of boards **equal to the row count from the query above**. Zero rows must render as an empty state saying no board is mapped.

**Item 4 is a count match, not a name match.** Do not treat the board names "Enablement Requests" or "Launch Coordination" as evidence of anything — those are the real Alma Health board names *and* they appear in `asana_setup.MOCK_DISCOVERY`. Only the count against `monitor_sources` distinguishes a real panel from a broken one.

**STEP 7 is look-only. Click nothing except the navigation needed to reach those screens.** No Remove, no toggles, no Save, no Refresh, no Run Backfill, no Import. If something looks wrong, do not click to investigate — write down what you see and STOP.

**Do not change `enablement.web_tabs`.** Not part of this port.

Then quit the app from the UI (not `kill`):

```bash
pgrep -fl "main.py"; echo "---"
```

Must print only `---`. STEP 8 assumes the app is closed.

---

## STEP 8 — Git bookkeeping (LAST, only if STEPS 1–7 all passed)

```bash
DEST="/Users/<user>/<exact path to checkout>"
cd "$DEST"
git fetch origin enablement-content-tabs
git log --oneline -1 FETCH_HEAD
```

**STOP if** `FETCH_HEAD` is not the commit the zip was cut at — the branch moved after the human downloaded it, so the reset would disagree with your working tree. Report both and stop.

```bash
DEST="/Users/<user>/<exact path to checkout>"
cd "$DEST"
git reset --mixed FETCH_HEAD
git status --porcelain
git log --oneline -1
```

`reset --mixed` moves the branch pointer and index. It does **not** touch working-tree files.

**Expect** `git status` clean apart from untracked files already on the Mac. `git log` will show **`4a2418d`, not `fbe370f`** — the commits after `fbe370f` touch only `docs/ZENDESK_BLOCK_PORT_GUIDE.md`, which STEP 4 excluded. That is correct.

**STOP if** `git status` lists modified tracked files under `src/`. Do not run `git checkout -- .`, do not force anything, do not push. Report the list.

---

## LEAVE EVERYTHING IN PLACE

Do not delete `~/alma_port_incoming`, the extracted tree, the zip, `data/local_warehouse.pre054.db`, `~/alma_port_backup/`, or the `pre-asana-port` branch. They are the rollback path. The owner removes them, not you.

---

## IF YOU HAVE TO STOP AFTER STEP 4

**Do not roll back. Report and wait.** If a human explicitly instructs you to restore, the only sanctioned command is:

```bash
DEST="/Users/<user>/<exact path to checkout>"; cd "$DEST"; git reset --mixed pre-asana-port
```

That restores the branch pointer only. **It does not revert file contents, deliberately** — reverting means `git checkout`/`restore`/`clean`, and those cannot tell this port's files from local work. Restoring file contents is a human decision.

The database rolls back only by copying `data/local_warehouse.pre054.db` back into place, app closed, on explicit human instruction.

---

## FINAL REPORT

**If you hit a STOP, the report ends there.** Mark everything after it `NOT REACHED`. An incomplete report from a stopped port is correct; a complete one is false.

1. `DEST` and `VENV` resolved and verified (STEP 0)
2. Baseline SHA, and whether 16 or 14 files were expected (STEP 1)
3. `051` + `053` present, `054`/`055` absent (STEP 1)
4. `pre-asana-port` branch created; `.pre054.db` exists (STEP 2)
5. No uncommitted edits in the port's files (STEP 2)
6. `status_before` → `status_after` diff = only the expected files (STEP 4)
7. Migration tail shows `054` + `055` (STEP 5)
8. `board_source_id` = 1, `task_board_links` present (STEP 5)
9. Test counts: 43 / 19 / 119 (STEP 6)
10. Human's four observations (STEP 7), incl. the Asana count vs `monitor_sources`
11. App closed cleanly (STEP 7)
12. `git status` clean after reset; tip is `4a2418d` (STEP 8)

**Anything not ✅ — say so plainly. Do not fix it, do not retry it, do not continue past it.**
