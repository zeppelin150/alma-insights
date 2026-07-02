# Branch Integration Guide — reconciling the work-local branch with GitHub

> **Purpose.** Your work-local Alma branch forked ~2 weeks ago and holds important updates that were never pushed. In parallel, GitHub `enablement-surgical-updates` accumulated the entire agent-mode surface (Connect & Configure, the Agent chat M1–M7, Phase 1.5 M1–M6a, the content-update stack, optimizations/fixes, and the security docs). This guide reconciles the two **safely**, self-contained — run it in the Bedrock work environment; it does not need the authoring session.
>
> **Audience:** you, or a *capable* Claude in your Bedrock env. **NOT the low-sonnet workclaude** — do not let a low-effort model resolve merge conflicts; it silently drops work. Workclaude runs the surgical-update guide *after* this reconciliation lands clean.

---

## 0. The governing principle (read once, internalize)

**GitHub `enablement-surgical-updates` is the COMPLETE superset and the base of truth.** Your work-local branch = the shared 2-week-old base + a *small* important-update delta. Therefore:

> **Replay your small local delta ONTO GitHub HEAD. Never merge the big GitHub history INTO your stale local branch.**

Move the small thing onto the big thing. Same end state, a fraction of the conflict surface, and you land on the complete base instead of the stale one. Everything below serves that principle.

**Not in scope / not in either branch:** the redaction/corpus workstream (uncommitted on the dev box, deliberately never pushed). Don't expect it here; don't try to integrate it in this pass.

---

## 1. Back up first (non-negotiable — do all three)

`git branch`/`git tag` only snapshot **committed** state, so:

```
# a) full folder copy of the work-local repo (captures uncommitted work too)
#    (File Explorer copy, or: robocopy <repo> <repo>-backup /E   on Windows)

# b) commit any uncommitted important work so it's replayable and tagged
git add -A && git commit -m "WIP: work-local important updates (pre-integration snapshot)"

# c) an immovable restore point
git tag backup/worklocal-pre-integration
git branch backup/worklocal-pre-integration-branch
```

Confirm `git status` is clean before proceeding. If anything below goes sideways, `git switch backup/worklocal-pre-integration-branch` (or restore the folder copy) returns you to exactly here. **No step in this guide is destructive to the backup.**

---

## 2. Measure the divergence (turn "nightmare" into a number)

```
git fetch origin enablement-surgical-updates

# the shared fork point:
git merge-base HEAD origin/enablement-surgical-updates
#   -> copy this SHA; call it BASE below

# how far each side diverged  (prints:  <yours>  <theirs>):
git rev-list --left-right --count HEAD...origin/enablement-surgical-updates

# files YOUR delta touches:
git diff --name-only BASE..HEAD

# files GitHub's delta touches:
git diff --name-only BASE..origin/enablement-surgical-updates
```

**Now compute the OVERLAP — the files *both* sides changed. That set IS your real conflict surface.**

- Git Bash:
  ```
  comm -12 <(git diff --name-only BASE..HEAD | sort) \
           <(git diff --name-only BASE..origin/enablement-surgical-updates | sort)
  ```
- PowerShell:
  ```
  Compare-Object (git diff --name-only BASE..HEAD) `
                 (git diff --name-only BASE..origin/enablement-surgical-updates) `
                 -IncludeEqual -ExcludeDifferent | % InputObject
  ```

**Decision gate on the overlap size:**
| Overlap | Meaning | Path |
|---|---|---|
| 0 files | Your delta and GitHub's touch disjoint files | Replay applies clean — §4 Strategy A, near-zero conflicts |
| 1–10 files | Contained | §4 Strategy A, resolve per-commit |
| >10 files, or any of the "hot" files below | Real reconciliation | §4 Strategy A **staged** — reconcile subsystem by subsystem, §5 |

**"Hot" files GitHub changed heavily** (if your delta also touches these, expect genuine conflicts): `src/ui/pages/enablement/page.py`, `src/ui/pages/enablement/*.py`, `src/data/chat_tools/enablement_tools.py`, `src/data/chat_tools/registry.py`, `src/gemini/client_factory.py`, `src/services/agent_chat.py`, `web/src/App.jsx`, `src/data/settings_manager.py`.

---

## 3. Pin down YOUR local delta precisely

```
# Are your important updates in COMMITS, or uncommitted? (should be committed after §1b)
git log --oneline BASE..HEAD          # <- these are the commits you will replay
git diff --stat BASE..HEAD            # <- the full delta you must preserve
```

Write down the commit range (`<first>..<last>`) or the commit SHAs. If `git log BASE..HEAD` is empty, your "important updates" were uncommitted and §1b just captured them into one commit — use that commit. **Sanity check:** does this delta look like *only* your intended important work? If it also shows agent-mode files, your local branch may be less stale than assumed — re-check BASE.

---

## 4. The replay

### Strategy A — cherry-pick onto a fresh branch off GitHub HEAD  *(recommended default)*
```
git switch -c integration origin/enablement-surgical-updates   # fresh branch ON the complete base
git cherry-pick <first>^..<last>                               # replay your delta, oldest→newest
```
- Conflicts surface **one commit at a time** (small, localized). For each: resolve (§5), `git add <files>`, `git cherry-pick --continue`.
- Bail anytime: `git cherry-pick --abort` → you're back on a clean `integration` = GitHub HEAD.
- **Staged variant (large overlap):** cherry-pick your commits in small groups by subsystem, compiling/testing between groups, instead of all at once.

### Strategy B — rebase --onto  *(only if your local delta is a clean linear series with no merges)*
```
git switch your-work-local-branch
git rebase --onto origin/enablement-surgical-updates BASE
# resolves commit-by-commit; git rebase --abort to bail
```

### Strategy C — patch apply  *(if the delta is messy / squashing is fine)*
```
git switch -c integration origin/enablement-surgical-updates
git diff BASE..your-work-local-branch > local-delta.patch
git apply --3way local-delta.patch      # --3way leaves conflict markers you resolve by hand
```

**Do not** run `git merge origin/enablement-surgical-updates` from your local branch — that's the "big into stale" direction this guide exists to avoid.

---

## 5. Resolving conflicts (the human-judgment part)

- **Never blind-accept `--ours`/`--theirs`.** Read both sides and understand intent.
- **Default resolution shape:** on a hot file where GitHub added agent-mode work *and* your delta changed the same file, you almost always want **GitHub's version as the base, with your specific change re-applied on top** — not your whole 2-week-old file overwriting GitHub's. Take theirs, then re-insert your hunk.
- After each resolved file: `python -m py_compile <file>` (or the JS build for `web/src`). Don't advance with a file that won't compile.
- If a conflict is bigger than one clear change, stop and reason about it explicitly — this is exactly where a rushed resolution loses work.
- Keep a running note of every file you resolved and how — you'll want it for §6.

---

## 6. Verify the integrated result

```
# 1) everything compiles
#    (compile every .py you touched; build web/ if App.jsx changed)

# 2) the integration is ADDITIVE — it must not have reverted any agent-mode work.
#    This should show ONLY your important-update files, nothing agent-mode:
git diff --stat origin/enablement-surgical-updates..integration
```
- If that diff shows agent-mode files being *removed or reverted*, a conflict was resolved the wrong way — go back (backup restore) and redo that file taking GitHub's side + your hunk.
- Kill zombies, then run the local test suites that exist on the work machine in groups of 3–4:
  `python -m pytest tests/<file> -x -q` (never the full `tests/` — it hangs on Windows).
- Smoke-test the app if you can: enablement → ASSISTANT → Agent loads, a chat turn works, the calendar/tasks render.

---

## 7. Land it, then hand off to workclaude

1. `integration` is now the unified truth: GitHub's complete agent-mode surface **+** your important updates, verified.
2. Push it: `git push origin integration` (or fast-forward the branch you want to carry forward).
3. **Only now** does workclaude run `docs/WORKCLAUDE_IMPLEMENTATION_GUIDE.md` — on this clean, unified base (point it at `integration`).
4. The redaction/corpus workstream stays separate throughout; don't fold it in here.

**Full sequence:** backup → measure → replay (Strategy A) → resolve (human) → verify additive → push `integration` → workclaude runs the surgical-update guide → merge into `enablement-content-tabs`.

---

## 8. Fallback

Anything wrong, at any point: `git cherry-pick --abort` / `git rebase --abort`, or `git switch backup/worklocal-pre-integration-branch`, or restore the folder copy. You measured the surface, you have a backup, and you're moving the small side onto the big side — this is recoverable by construction.

---

## Appendix — why this shape

Merging GitHub *into* the stale local would force you to resolve every conflict while landing on the 2-week-old base, then you'd still have to bring it forward. Replaying your small delta onto GitHub HEAD resolves the *same* conflicts once, lands you on the complete base immediately, and keeps your important work as a clean, reviewable set of commits on top. The overlap measurement (§2) is what tells you, before you touch anything, whether this is an hour or a staged afternoon.
