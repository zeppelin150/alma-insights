# Phase 1.5 — GUI QA Checklist

Manual verification of the "proactive chief-of-staff" build (M1–M6a) on branch
`enablement-content-tabs` (commits `d3fd508` → `cc4e708`). The 72 gitignored
`*_local.py` tests already cover these behaviors headlessly; this is the human
pass in the running app.

**Everything in M1–M5 is native Qt (PySide6)** — no `web/` React/vite bundle
rebuild is needed. Migrations `040/041/042` auto-apply on first launch.

---

## Setup (once)

- [ ] Launch `python main.py` → switch to **Enablement** mode.
- [ ] **Settings → Providers**: set provider = **Claude**.
- [ ] Connect **Guru** (`chris@cambric.ai`), **Asana** (PAT), **Google** (OAuth) in Settings.
- [ ] Click **Scan now** to ingest live Asana tasks — *or* enable demo mode for
      sample data (note: identity-dependent features still need a real identity).

---

## M1 — Operator identity
*Settings → Providers → "OPERATOR IDENTITY" card (top of tab)*

- [ ] Identity card renders: email field + **Auto-detect from Google** + **Resolve GID**.
- [ ] Type an email → reopen Settings → it persisted; "You are: …" label shows.
- [ ] **Auto-detect from Google** (Google connected) → email fills in and "You are:" updates.
- [ ] Auto-detect with Google *not* connected → the **Google Drive** status dot warns
      "Connect your Google account first."
- [ ] **Resolve GID** (Asana connected) → "Asana: `<name>` · gid `<id>`" appears.
- [ ] **Regression:** type a deliberate override email, then click Auto-detect →
      your override is **not** clobbered.

---

## M2 — Only-mine task filter
*Calendar + Tasks tabs*

- [ ] **Mine / All** segmented toggle appears on the Calendar header **and** the Tasks filter bar.
- [ ] Identity set: **Mine** shows only tasks assigned to you; **All** shows everyone's.
- [ ] Toggling one view mirrors the other view.
- [ ] The Mine/All choice **persists across restart**.
- [ ] Identity *unset* → shows **all** tasks (never a blank board).

---

## M3 — Calendar go-live + Asana-parity detail  *(highest-value visual check)*
*Calendar tab*

- [ ] Calendar opens on the **real current month** with **today** highlighted
      (the old bug parked it on June 2026).
- [ ] Real tasks render on their **real due dates**.
- [ ] Click a task chip → **detail panel** opens (not the Workbench) showing
      **full description**, **"requested by `<name>`"**, assignee, and due date.
- [ ] **"Open in Asana ›"** link opens the task in your browser.
- [ ] A day with **>3 tasks** shows **"+N more"** → click → day list → click through
      to a task's detail.
- [ ] **Security:** a task whose title contains `<b>`, `[SYSTEM:]`, or HTML renders
      **literally** (no bold/markup) in the chip and detail panel.

---

## M4 — Pluggable task sources  *(mostly backend)*

- [ ] **Scan now** pulls from all configured connectors (Asana + Drive + Guru) and
      new tasks appear. *(No "add a source" button yet — a cosmetic Settings piece
      was deliberately deferred; the registry-driven scan is the functional win.)*

---

## M5 — "Here's your day" startup greeting
*Enablement → the Renn / Agent chat*

- [ ] Identity set **and** you have due-today/overdue tasks: opening the chat makes
      **Renn auto-greet** with a prioritized "here's what's due / what to tackle
      first" — **without you typing**.
- [ ] Only **Renn's** bubble shows (no phantom user message).
- [ ] No identity, or no dated tasks → **no greeting** (silent, by design).

---

## Not testable in the GUI yet — M6a (engine only)

The ask-first research engine (propose plan → operator approves → cancelable
background research → per-task manifest Renn can talk to) is **built and tested
headlessly**, but its tools aren't registered and there's **no approval card**,
so there is nothing to click for research today. Wiring it is **M6b**: register
the 2 research tools, the controller approve→run thread, the React
`ResearchPlanCard`, the vite bundle rebuild, and the decision on mounting the
research/jobs rail into the enablement Workbench (a Qt-vs-React architecture call).

---

## Known caveats (not Phase 1.5 defects)

- Two **pre-existing** test failures, verified present before this work:
  - the other workstream's `test_chat_engine.py::TestUseMcpToolsBoundary` (hangs);
  - `test_migration_idempotent.py` (`no such column: priority`) — a flaw in that
    test's custom chain-replay harness, **not** the real migration path
    (`DatabaseManager.initialize` → `SchemaMigrator`, verified working by all
    72 Phase 1.5 tests that apply 040/041/042 and assert the new columns/indexes).
- The redaction/corpus workstream in the working tree is **unrelated** and was
  left untouched by every Phase 1.5 commit.
