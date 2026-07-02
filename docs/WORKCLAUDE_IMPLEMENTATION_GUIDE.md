# Implementation Guide — Surgical Update Pass (for workclaude)

> **You are working on a fresh clone of `alma-insights`, branch `enablement-content-tabs`.** This guide is your **only** source of truth — you do not have the authoring session's context, memory, or chat history. Read Sections 0–1 fully before editing anything. Work top-down; each task is independent unless it says otherwise. Do the smallest change that satisfies the acceptance check. Do not refactor beyond the task.

---

## 0. Orientation (read first)

**What this repo is:** a PySide6 (Qt) desktop app for healthcare RCM ticket analysis. Python 3.10+ (dev box runs 3.13). SQLite warehouse at `data/local_warehouse.db`. Two LLM lanes: **Gemini** (PHI/BAA lane, via a CLI subprocess/Node bridge) and **Claude** (ops/enablement lane, via `urllib` API + a local `claude` CLI). "Renn" is the enablement assistant (chat UI). "Phase 1.5" = Renn's proactive chief-of-staff layer (operator identity, my-tasks filter, calendar, morning greeting, and a research engine).

**Your environment:**
- OS: Windows. Shell: **PowerShell** (primary) and Git Bash both available. Use forward slashes in paths.
- Run a file compile-check: `python -m py_compile <path>`
- Run tests: `python -m pytest tests/<file> -x -q` — **run in groups of 3–4 files; the full `tests/` dir HANGS on Windows.**
- **ALWAYS kill zombie subprocesses before running any test or launching the app:**
  ```
  wmic process where "commandline like '%alma_mcp_server%'" call terminate
  ```

**Project discipline (violating these breaks things — non-negotiable):**
1. **DB:** only `from src.data.connection_factory import get_connection` (and `atomic`). Never `sqlite3.connect`. `atomic()` **cannot nest** (raises `RuntimeError`).
2. **Settings:** only `from src.data.settings_manager import get_section, update_section, load_settings, save_settings`. Never read/write `settings.yaml` directly.
3. **LLM:** route via `from src.gemini.client_factory import build_client_for_task`.
4. **Qt threading:** NEVER touch a widget from a worker thread. Cross-thread → emit a Qt `Signal` the main thread is connected to, or `QMetaObject.invokeMethod`. A DB flag (see `agent_jobs.is_cancelled`) is the accepted way to signal a worker thread.
5. **Bug fixes** follow the 4-gate **Bug Bash Protocol** in `CLAUDE.md` (Investigate → failing repro → smallest fix → verify). Read it before fixing any *bug*; feature tasks below don't need a repro but still get a verification step.

---

## 1. CRITICAL CAVEATS (things that are NOT normal about this clone)

**1.1 — The Phase-1.5 unit tests DID NOT travel.** All `tests/test_*_local.py` files are gitignored (`.gitignore:142`) and were intentionally kept local. That includes the ~74 tests that cover identity, the my-tasks filter, the greeting, task-sources, and the research engine. **You have no unit tests for the code you are changing.** Verify by the methods in Section 2 instead. Do **not** recreate these files.

**1.2 — The React chat bundle IS committed.** `src/ui/web/dist/index.html` (a single self-contained ~186 KB file) + `qwebchannel.js` are checked in so offline installs need no `npm build`. The app (`src/ui/web/agent_page.py`) loads that file. **Consequence:** if you edit anything under `web/src/`, the running app will NOT reflect it until the bundle is rebuilt. If you can run npm: `cd web && npm install && npm run build` (writes into `src/ui/web/dist/`), then re-commit the bundle. **If you cannot reach the npm registry, do the `web/src/` edits anyway, commit them, and clearly state in your report that the bundle needs a rebuild on a networked machine.** Never hand-edit the minified `dist/index.html`.

**1.3 — Do NOT touch the redaction/corpus workstream.** A separate, uncommitted effort owns these files; they are not part of this clone's tree and are out of scope. If you see references to them, leave them alone: `src/data/redaction_engine.py`, `src/data/redaction_signal.py`, `src/data/chat_tools/fast_path.py` (redaction parts), `scripts/corpus_gen/`, `scripts/build_realistic_corpus.py`, `scripts/deidentify_spreadsheet.py`, `config/redaction_patterns.json`, and any `tests/test_redaction_*`. **Exception:** the P0 redaction *fail-closed* fix in Task A2 is in `src/llm/claude_client.py`, which IS in scope — that file is committed and separate from the workstream above.

**1.4 — Read `docs/SECURITY_COMPLIANCE_REVIEW.md`.** It is the source for all Task A items and has file:line detail and rationale.

---

## 2. How to verify (since local tests don't travel)

For every file you change, in order:
1. **Compile:** `python -m py_compile <file>` — must print nothing (success).
2. **Import:** `python -c "import src.data.research_runner"` (adjust module) — must not raise.
3. **Run the nearest committed tests** (these travelled; run in groups, zombies killed first). Safe, relevant ones:
   - `python -m pytest tests/test_chat_tools.py tests/test_chat_engine.py -x -q`
   - `python -m pytest tests/test_stage4_data_warehouse.py -x -q`
   Report pass/fail counts. If a test was already failing before your change, say so (compare against a clean checkout).
4. **UI changes** can't be verified headless easily — describe what you changed and what the operator should see; do not claim you verified a rendered UI you did not run.
5. **Never** mark a task done without at least steps 1–2 green.

---

## 3. The work — priority order

Three independent workstreams: **A (security P0)**, **B (M6b research wiring)**, **C (review cleanups)**. Do A first (compliance-blocking), then B (the headline unfinished feature), then C (polish). You may stop after any complete task.

---

### TASK A — Security P0 (details + rationale in `docs/SECURITY_COMPLIANCE_REVIEW.md`)

#### A1 — Disable Claude CLI telemetry/auto-updater/error-reporting (airgap + SOC2)
- **File:** `src/agents/claude_cli_bridge.py`, method `_build_subprocess_env` (grep: `def _build_subprocess_env`).
- **Change:** after `env = os.environ.copy()`, set these four to `"1"` (they are read by the `claude` CLI):
  `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, `DISABLE_TELEMETRY`, `DISABLE_ERROR_REPORTING`, `DISABLE_AUTOUPDATER`.
  Do this **unconditionally** (before the Bedrock early-return), so it applies on every spawn.
- **Acceptance:** `python -m py_compile` green; grep shows the four vars set in that method; existing Bedrock behavior unchanged (the `if not bedrock_cfg.get("enabled"): return env` still returns the now-hardened env).

#### A2 — Make PII redaction fail CLOSED (HIPAA)
- **File:** `src/llm/claude_client.py`, function `_redact_text` (grep: `sending un-redacted`).
- **Current bug:** on a redaction exception it logs `"PII redaction failed, sending un-redacted"` and **returns the raw text** → PHI can reach the API.
- **Change:** on exception, do NOT return raw text. Raise a `RuntimeError("PII redaction failed; refusing to send un-redacted text")` (fail closed). Keep the warning log. Callers (`generate`, `generate_streaming`) already call `_redact_text` synchronously, so the raise propagates and blocks the send.
- **Acceptance:** compile green; the function has no code path that returns un-redacted text on error. Add a note in your report that this changes behavior from "send anyway" to "block" — intended.

#### A3 — Make PHI task lanes non-overridable to a non-BAA provider (HIPAA)
- **File:** `src/gemini/client_factory.py`, function `resolve_provider_for_task` (grep: `def resolve_provider_for_task`).
- **Risk:** `ai.task_routing.override_all=claude` (or `enablement.provider=claude`) can route ticket-bearing tasks (e.g. `nlp_classification`, `voc_analysis`, `report_generation`) to the non-BAA Claude API.
- **Change:** define a constant set `_PHI_LOCKED = {"nlp_classification", "voc_analysis", "report_generation", "ab_comparison"}` and, at the TOP of `resolve_provider_for_task`, `if task_type in _PHI_LOCKED: return "gemini"` — before any override/enablement logic. (Confirm these task-type strings against `_DEFAULT_ROUTES` in the same file; only lock the ones whose input is raw ticket text.)
- **Acceptance:** compile green; a manual check that `resolve_provider_for_task("nlp_classification")` returns `"gemini"` even with `override_all` set. Non-PHI tasks (`guru_analysis`, `enablement_*`) still honor their routing.

#### A4 — Bump `scan_server` node-forge (supply-chain; RUNTIME dep that ships)
- **Dir:** `scan_server/`. **Only if npm registry is reachable.**
- **Change:** `cd scan_server && npm install node-forge@^1.4.0 && npm audit fix` (non-major). Also bump `express>=4.22.2` if `npm audit` still flags it.
- **Acceptance:** `npm audit` shows the node-forge HIGH advisories cleared. **If offline:** skip and record in your report as "needs a networked build."

> **Lower-friction dependency bumps** (`nltk==3.9.4`, `markdown==3.8.1`, `PyJWT==2.13.0` in `requirements.txt`) are P1 — only do them if you can `pip install` and re-run the test groups in Section 2 to confirm nothing breaks. If offline, list them in your report instead.

---

### TASK B — Wire the M6b research engine (the one unfinished Phase-1.5 feature)

**Background:** Renn's "ask-first research" backend is fully built and committed but **not connected**: the runner has zero callers, the two chat tools aren't registered, and there's no approval UI. Today Renn is told (in its system prompt) that research is unavailable — see **B0**. Your job is to connect the existing pieces. Do the sub-tasks in order; each builds on the last.

**The existing, already-built pieces (do not rewrite these — call them):**
- `src/data/research_store.py` — `create_research(conn, task_id=…, session_id=…, job_id=…)`, `update_research`, `latest_for_task`. (table `task_research`, migration `042`.)
- `src/data/research_runner.py` — `run_research_job(conn, *, job_id, task_id, research_id, task_title, steps, generate, is_cancelled=…)` and `default_generate()`.
- `src/data/agent_jobs.py` — `create_job`/`update_job`, and (already built) `cancel_job`, `is_cancelled(conn, job_id)`.
- `src/data/chat_action_requests.py` — `create_research_plan(...)` mints a `research_plan` action row (already wired; `research_plan` is in `ACTION_TYPES`).
- `src/data/chat_tools/enablement_tools.py` — `handle_request_research_plan` and `handle_get_task_research` (defined, NOT registered).

#### B1 — Register the two research tools (unblocks everything)
- **File 1:** `src/data/chat_tools/registry.py`.
  - Add `handle_request_research_plan,` and `handle_get_task_research,` to the enablement handler **import tuple** (grep: `handle_request_create_asana_task,` — add the two names right after it, inside the same `from … import ( … )`).
  - Add two `_register(...)` calls next to the existing `_register("request_create_asana_task", handle_request_create_asana_task, …)` line (grep: `request_create_asana_task`). Mirror its signature exactly:
    ```python
    _register("request_research_plan", handle_request_research_plan,
              phi_level=0, desc="Propose an ask-first research plan (2-5 steps) for a task; opens an approval card. Reads no PHI.")
    _register("get_task_research", handle_get_task_research,
              phi_level=0, desc="Read back the latest stored research markdown for a task.")
    ```
- **File 2:** `src/mcp/chat_mcp_server.py`, list `TOOL_SCHEMAS` (grep: `TOOL_SCHEMAS = [`). Add two schema dicts modeled on the `request_create_asana_task` entry (grep its `"name"`). Shapes:
  - `request_research_plan`: params `task_id` (string, required), `summary` (string), `plan_steps` (array of strings, required).
  - `get_task_research`: param `task_id` (string, required).
- **Acceptance:** compile both files; `python -c "from src.data.chat_tools import registry"` imports clean; grep confirms both names appear in registry.py `_register` calls and in `TOOL_SCHEMAS`. Run `tests/test_chat_tools.py` (Section 2).

#### B2 — Approve→run controller (the missing execution path)
- **Goal:** when the operator Approves a `research_plan` action, run the plan on a worker thread and write the manifest.
- **File:** `src/ui/web/chat_bridge.py` (this is where JS↔Python action resolution lives; grep: `actionRequested`, and look at how an existing picker is resolved for the template — the Drive/Asana pickers resolve through this bridge).
- **Add a `@Slot(str, bool)` method** e.g. `resolveResearchPlan(request_id, approved)` that:
  1. Opens a connection via `get_connection`.
  2. Marks the action consumed (use the same consume/claim path the other actions use — grep `claim_pending_actions` / how `confirm_write` is marked consumed — reuse it; do NOT invent a new column).
  3. If `approved` is False: mark the research row/action declined and return.
  4. If approved: read the plan payload (`task_id`, `summary`, `steps`), then:
     - `research_id = research_store.create_research(conn, task_id=…, session_id=…, status="running")`
     - `job_id = agent_jobs.create_job(...)` (grep `def create_job` for its signature)
     - Start a **worker thread** (Qt `QThread` or the pattern already used for scans — grep the codebase for an existing `QThread` worker in the enablement pages to copy) that calls:
       ```python
       research_runner.run_research_job(
           conn2, job_id=job_id, task_id=task_id, research_id=research_id,
           task_title=title, steps=steps,
           generate=research_runner.default_generate(),
           is_cancelled=lambda: agent_jobs.is_cancelled(conn2, job_id))
       ```
       (Open a **fresh** connection `conn2` inside the thread — do not share the main-thread connection.)
     - Emit a `jobsListed`/status signal so the UI shows progress (the jobs channel already exists — grep `jobsListed`).
- **Threading rule:** the worker must not touch Qt widgets; it only writes to the DB and emits signals. The `is_cancelled` DB-flag pattern is exactly why `agent_jobs.is_cancelled` exists.
- **Acceptance:** compile green; import green. You likely cannot run this end-to-end headless — in your report, state that and describe the wiring. Confirm by grep that `run_research_job` now has a caller in `src/` (previously zero).

#### B3 — The React ResearchPlanCard
- **File:** `web/src/App.jsx` (source; remember caveat 1.2 — rebuild the bundle after).
- The free-running action channel already renders known action types and falls back to `ActionPlaceholder` for unknown ones (grep: `ACTION_LABELS`, `ActionPlaceholder`, `ConnectGoogleCard` as the template).
- Add `research_plan: "Approve a research plan"` to `ACTION_LABELS`, and add a `ResearchPlanCard({ action, bridge, onDismiss })` component modeled on `ConnectGoogleCard`: it shows `action.payload.summary` + the numbered `action.payload.steps`, with **Approve** and **Decline** buttons that call `bridge.resolveResearchPlan(action.request_id, true|false)` then dismiss. Wire it into the same switch/router that picks a card by `action.type` (grep where `google_connect`/`confirm_write` types are matched).
- **Acceptance:** `web/src/App.jsx` compiles under the build (`cd web && npm run build`) if you can build; otherwise ensure the JSX is syntactically valid and note the pending rebuild. The card must send only `{request_id, approved}` — no research logic in JS.

#### B4 — Read-back + remove the guardrail
- Confirm `get_task_research` returns the manifest markdown for a task (it calls `research_store.latest_for_task`). Renn uses it to discuss finished research on a later turn — no new code likely needed beyond B1's registration.
- **Then (and only when B1–B3 are done and working):** remove the "Background research (NOT AVAILABLE YET)" block from `RENN_SYSTEM_PROMPT` in `src/ui/pages/enablement/page.py` (grep: `Background research (NOT AVAILABLE YET)`). While research is only partially wired, **leave that guardrail in place** so Renn doesn't promise a capability it can't deliver.
- **Acceptance:** if you removed the guardrail, confirm B1–B3 are complete; otherwise leave it and say so.

---

### TASK C — Review cleanups (low severity; polish)

#### C1 — Consolidate the three "only mine" predicates
There are three implementations of "is this task the operator's": `src/data/enablement_identity.py::is_mine` (GID-first, then email/name text), `src/data/startup_greeting.py::_is_mine` (text aliases only — **ignores GID**), and the SQL filter in `src/data/enablement_tasks.py::list_tasks`. The greeting path silently drops a GID-only match.
- **Change:** make `startup_greeting._is_mine` also honor the operator's Asana GID (route it through `enablement_identity.is_mine`, passing the task's `assignee_gid`). Keep behavior identical for the common name-match case.
- **Acceptance:** compile green; logic reads the GID when present.

#### C2 — Document the assignee vs assignee_gid divergence
In `src/data/asana_monitor.py::_create_task_from_asana` (grep: `assignee_gid = (task.get`), the displayed `assignee` **name** can come from a custom people-field mapping while `assignee_gid` is only the *direct* task assignee — they can describe different people (this is why custom-field-assigned boards can show empty "Mine" lists). Add a one-line code comment noting this; no behavior change.

#### C3 — Validate the operator email before persisting
`src/ui/pages/enablement/settings.py` (grep: `_save_operator_email` or `operator_email`) persists whatever string is typed. Add a minimal format guard (`"@" in value and "." in value.split("@")[-1]`) before `update_section`; on failure, show the existing status/error affordance and don't persist. **Acceptance:** compile green.

#### C4 — Retry a transiently-failed task source
`src/data/task_sources.py::ensure_sources_loaded` sets `_LOADED = True` before importing, so a connector that fails to import once stays unregistered forever. Change it to only set `_LOADED = True` **after** the import loop completes without the registry being empty (keep the re-entrancy guard intact — use a separate `_LOADING` flag if needed). **Acceptance:** compile green; a successful load still runs once.

---

## 4. Guardrails (do NOT do these)
- Do not commit anything under `tests/test_*_local.py` or the redaction/corpus files (Caveat 1.3).
- Do not delete or hand-edit `src/ui/web/dist/` (Caveat 1.2).
- Do not add new dependencies. Do not bump `transformers`/`sentence-transformers` (major, breaks embeddings).
- Do not "fix" the 136 Bandit B608 SQL findings wholesale — they are the reviewed whitelisted-column pattern.
- Do not run the full `tests/` directory (hangs). Groups of 3–4 only, zombies killed first.
- Do not touch `data/settings.yaml`, `data/local_warehouse.db`, or migration files 001–042 (they are applied history; a NEW migration would be 043+).

## 5. When done — report format
For each task attempted, report: **task id**, **files changed** (with paths), **verification run** (compile/import/test output — real numbers), **anything left undone or needing a networked machine** (npm build, pip bump, UI you couldn't run). Be honest about what you did NOT verify. End with a one-line status per task: DONE / PARTIAL / SKIPPED (reason).

---
*Companion docs: `docs/SECURITY_COMPLIANCE_REVIEW.md` (Task A rationale), `CLAUDE.md` (project conventions + Bug Bash Protocol), `docs/ENABLEMENT_ROADMAP.md` (where Phase 1.5 sits).*
