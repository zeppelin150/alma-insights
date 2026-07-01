# Renn / Enablement Roadmap

**What this is:** the live roadmap for Renn (the enablement assistant) and the
Workbench — from an embedded chat assistant toward a signal-driven content
agent. Strategy detail lives in the local plans
(`~/.claude/plans/renn-uplevel-roadmap.md`, `renn-phase-0-1-build.md`); this
file is the committed, shareable status.

**Loop we're building:** `detect → score → triage → draft → review → publish → measure`,
draft-only with a human gate on every publish (healthcare-RCM constraint).

**Hard constraint (product-owner decision, 2026-06-29):** enablement is
**fully decoupled from the product/RCM warehouse** — Renn reads only the Guru
**live API** + enablement-local tables (catalog, drafts, provenance, card
health, signal snapshots). **No ticket/PHI/warehouse reads, ever.** Effectiveness
is measured Guru-natively (views / open-comment deltas), not by ticket volume.

---

## Done

### Content-update pipeline hardening (audit of the 2026-06-27/28 batch)
A 7-dimension adversarial audit found 17 confirmed bugs; all fixed with tests:
- Catalog summariser coerces malformed LLM `topics`/`summary` (no char-splitting / crash / `"None"`).
- Fan-out isolates per-card failures (one card can't abort the batch + orphan drafts).
- Grounding: stem-lite matching + empty-source guard; honest "coarse heuristic" docstring.
- Targeted writes: fence-aware section split (no code-fence corruption), type-aware routing, deterministic tie-break.
- Provenance `finalize_publish` upserts an anchor for **every** push path.
- Tool error clarity: `update_task` → `task_not_found` / `no_updatable_fields`; `update_card(s)_from_doc` → `no_matching_doc` (distinct from `source_doc_required`).

### Phase 0 — Haiku foundation + attention queue *(complete)*
- **0.1** Strict JSON schemas on the enablement MCP tools + a dispatch-time validator (`chat_tools/_schema.py`); the Gemini text-loop and non-enablement tools pass through untouched.
- **0.2** Task-shaped verbs: `find_cards_to_update`, `find_stale_cards`, `find_content_gaps` (thin wrappers over `compute_health`).
- **0.3** Per-model `CapabilityProfile` on the `ModelRegistry`; model-aware token estimate; no-arg `max_tokens` default preserved (larger ceiling is opt-in).
- **0.4** Composite **card-health score** — `src/data/enablement_health/` (Guru-signal facade, signals, deterministic scorer, store, `compute_health`); migration `034`.
- **0.5** **Attention-queue Home tab** (`ui/pages/enablement/attention_queue_tab.py`) — ranked buckets (source-changed / verification-overdue / gap-dup / staged drafts), off-thread load, Open/Dismiss.
- **0.6** Critic finished (`content_update/validate.py` + shared `enablement_checks.py`): broken-link / readability / PII / style + valid-card-id checks.

### Phase 1 — best-in-class draft → review → publish *(complete)*
- **1.1** **Evidence pack** — `provenance.evidence_pack` (source + per-change quotes) surfaced via the `card_history` tool.
- **1.2** **Red/green diff view** — `ui/pages/enablement/diff_view.py` + a Workbench "Review changes" toggle.
- **1.3** **Upstream auto-checks** — `enablement_checks.run_checks` (deterministic, offline) as status badges.
- **1.4** **Unified chat + canvas** — an inline `/`-menu + highlight-to-edit in the editor (`ai_edit_requested`) routed to an off-thread revise of the active draft with a **live canvas reload**; chat-driven edits reload too.
- **Guru-native effectiveness proxy** — `content_update/effectiveness_proxy.py` + migration `035` (views-delta / comments-resolved at publish → after a window).

**Engineering bar held every milestone:** built via standards-gated multi-agent
workflows; each change adversarially verified for low complexity, no god files,
gitignored `*_local.py` tests, full decoupling, behavior preservation, and Qt
main-thread safety. Verifiers caught and forced fixes for real regressions (an
app-wide `max_tokens` doubling, broken tracked tests).

---

## Built — Agent: a standalone, polished chat experience *(M1–M7 done 2026-06-29)*

A dedicated, Claude-app-grade chat surface to drive the enablement workflows,
built **inside Alma Insights** (not a second app) so the local-first / no-server
posture holds.

**Architecture (Option B — embedded web UI):** a React chat rendered in a
`QWebEngineView`, loaded from a static `qrc`/`file` bundle (**no web server, no
`localhost:8080`**), bridged to Python via **`QWebChannel`** (in-process, not
network). The backend is unchanged — `ChatEngine`, the `claude`/`aws` CLIs, the
MCP servers, and redaction stay exactly as today; the web layer is a pure
renderer talking to a `ChatBridge` QObject. The bridge is the only boundary, so
the migration stays clean.

**Features:**
- Smooth streaming chat (markdown, code blocks), a live **tool-call timeline**, **token-use** display.
- **Past-chat review** browser over the existing `chat_sessions`.
- **Dictation** via a bundled on-device **whisper.cpp** engine (`pywhispercpp` + `sounddevice`) — audio never leaves the machine (HIPAA-safe, no BAA, no cloud fallback).
- **Job-builder MCP** — a tool that turns a task into a first-class **job** (phases / agents / tokens / tools / status, persisted locally), tracked + inspectable in the **native sidebar pop-out** (replicating the background-tasks panel).
- **In-thread tool-edit review + sign-off** — when the AI edits a tool/card, the user sees the diff and approves inline, reusing `diff_view.py` + the human-gate.

**Cross-platform (load-bearing):** designed + heavily tested for **Windows (dev)
and macOS / M1 (target)** in lockstep — QtWebEngine availability, Chromium bundle
size, Mac code-signing/notarization, OS-speech API differences.

**Installer:** the Express installer (bundled Node.js, deps, migrations) gains a
React build step + the QtWebEngine dependency + the static UI bundle, for both
platforms.

> Status: **M1–M7 built and pushed** — `origin/enablement-content-tabs` at commit
> `bfb1b6d`, on top of the M1/M2/React commits (a3332e5 … 41f6356). The Agent is a
> live page (enablement → ASSISTANT → Agent) on a real `ChatEngine`, rendered as a
> React/Vite app (vite-plugin-singlefile → one inlined `dist/index.html`) in
> `QWebEngineView` over `QWebChannel` (no server, no localhost). Highlights:
>
> - **M2** — live tool-call timeline (`toolCall` tails `chat_tool_executions`) + token/cost meter.
> - **M3 past-chat browser** — a History drawer over `chat_sessions` + migration-009
>   FTS5: recent list, full-text search, click-to-load (restores the thread), delete
>   (cascade), New chat — strictly `source_page='enablement'`-scoped so a product-mode
>   chat can never load into the Agent. The Agent now **persists its turns** to
>   `chat_messages` (history had been empty / "Untitled" before).
> - **Streaming** — the assistant bubble fills token-by-token (optional `on_token` on
>   the Gemini + Claude-CLI clients → `ChatEngine.token_streamed` → React), with the
>   whole-message commit still driving telemetry + tools.
> - **M4 Job-builder** (migration 036) — `create_job`/`update_job`/`list_jobs` MCP
>   tools + a live **Jobs** sidebar (status/progress/steps), fully local/decoupled.
> - **M5 review/sign-off** (migration 037) — a real DB-layer **`require_approval`
>   gate**: `push_guru_draft` cannot publish to Guru without recorded human sign-off;
>   a **Review** drawer shows the red/green diff + pre-flight checks and the operator
>   approves/rejects inline (the Workbench's own publish path is unchanged).
> - **M6 voice** — **on-device dictation via whisper.cpp** (`pywhispercpp` +
>   `sounddevice`), a supply-chain-vetted, PyTorch-free engine with a bundled,
>   SHA-256-pinned GGML model verified **fail-closed at load** and raw-PCM inference.
>   **No cloud fallback** — audio never leaves the machine (the earlier WinRT path was
>   Microsoft cloud and was removed from the HIPAA path). Click-to-toggle mic.
> - **M7 packaging** — full `PySide6` (QtWebEngine) in requirements/PACKAGES, the
>   React build + the voice model bundled by the installer + CI
>   (`fetch_voice_model.py`), and a real **deep-sign of QtWebEngineProcess** on macOS.
>
> Hardened by three adversarial-review workflows (incl. a 30-agent voice review that
> caught + fixed a cloud-fallback **blocker**). **Remaining = on-hardware/CI
> verification only:** the macOS Speech.framework path is a scaffold; the macOS
> deep-sign/notarize + the WebEngine-bundle build need a CI run / a Mac (Developer ID
> or a free ad-hoc deep-sign for M1 dev/pilot). Plan: `~/.claude/plans/agent-chat-build.md`.

---

## Built — Connect & Configure: in-chat Google OAuth + Drive/Asana pickers + conversational routing + gated writes *(M0–M9 built + GUI-tested 2026-06-30; uncommitted on `enablement-content-tabs`)*

> **Status: built and live-tested in the app** (enablement → ASSISTANT → Agent).
> Real-data GUI test confirmed the Asana board picker (your live projects), the
> Guru collection picker + routing, the in-chat Connect-Google card, and the gated
> write Confirm cards. ~140 gitignored `*_local.py` tests across M0–M9, green
> (verified via PowerShell — Qt tests swallow stdout under Git Bash). Plan + the
> 16-point MUST-FIX checklist: `~/.claude/plans/renn-google-asana-integration.md`.
> **Not yet committed** — the whole M0–M9 stack + the picker restyle sits in the
> working tree, kept separate from the uncommitted redaction/corpus workstream.


The operator OAuths their **own Google account from chat**, browses the
**Drives/folders they can access** (My Drive + Shared Drives), browses **live
Asana boards + tasks**, and sets routing (active Drive folder, Asana board, Guru
publish target) **conversationally** — Renn lets them **pick** GIDs/folders
instead of demanding pasted IDs. Closes the transcript gaps: Renn couldn't
initiate OAuth, the Drive index was empty (drive.readonly never activated), and
Renn had **no tool to list tasks on a board**.

**Decisions (operator, 2026-06-30):** full interactive pickers · single operator
per install (global `enablement.*` prefs, creds in keyring) · My Drive **+ Shared
Drives** (`corpora=allDrives`) · Asana **shared PAT, browse + routing** (no Asana
OAuth, no task-create — that's 2.2).

**~80% of the backend already exists, just unwired to Renn:** `google_oauth.py`
(PKCE, keyring, disable-on-launch) + `GoogleOAuthWorker`, `DriveReader`,
`AsanaClient.list_tasks()` (**built, never exposed**), `set_asana_board_config`,
the Guru collection/folder listers. The one real problem: the MCP tool server is
a **subprocess with no Qt loop**, so a tool can't pop a browser/picker itself.

**Architecture (adversarially hardened — design panel + dual pre-mortem):**
**Two-Phase Resolver Tools.** A resolver tool (subprocess) only mints a
server-side `request_id` and writes a row to a **new `chat_action_requests`
table** (migration 038, AUTOINCREMENT + `consumed` flag); it returns the LLM a
**minimal "a picker is open — stop and wait"** string (envelope never reaches the
model). A **free-running ~500ms poll** in the main process atomically claims the
row and emits `actionRequested` → a React picker. The picker lazily loads its
tree via bridge slots that run Drive/Asana HTTP **off-thread in the main process**
(data never touches the LLM). On pick, the controller **persists to settings
first** (durable, out of band), then notifies Renn with a `[SYSTEM]` turn that
injects the **id only (names redacted — folder names can be PHI)**, busy-queued.
OAuth reuses `GoogleOAuthWorker` in-process; **disable-on-launch preserved**.

> Pre-mortems killed the naive "piggyback the tool-call poll" design
> (turn-lifecycle inversion: in ACP mode the tool loop runs in one opaque turn, so
> an envelope can't pause it) and forced: a dedicated action table (not a
> non-monotonic `rowid` cursor), a free-running poll (not busy-gated — else a
> cross-process commit race never opens the picker), a non-forgeable channel
> (resolver-name allowlist + server-minted id, so a PHI tool result can't spoof an
> OAuth prompt), and a human-gate (writers refuse while a pick is pending).

**7 core milestones (M0 action-channel → M1 Drive shared-drive surface → M2 connect
→ M3 Drive picker → M4 Asana picker+tasks → M5 Guru target → M6 prompt+E2E),** each
built + shipped through an adversarial-review workflow against the MUST-FIX
checklist + a fresh pre-mortem. Review caught one real defect (M3 `resolve_drive_folder`
ran `mark_resolved` before the settings persist → data-loss + a TOCTOU human-gate
window); fixed to persist-first.

**Extensions added during GUI testing (M7–M9):**
- **M7 — gated write actions.** Renn can **create/rename a Guru folder** and
  **create a top-level Asana task**, each behind a **sidebar Confirm/Cancel card** —
  the model gets no direct-write tool, so the only path to a mutation is the
  operator's click. Non-idempotent writes, so `execute_write` does `mark_resolved`
  **first** (one click = one write). Guru folder *delete* isn't in the public API →
  Renn routes to the Guru web app.
- **M8 — write pre-flight.** The propose tools pre-check feasibility and **steer**
  before opening a doomed card (read-only Guru collection → lists writable ones;
  unknown Asana board → lists boards); the Guru picker badges read-only collections.
  Pre-flight only gates card *opening* — the human-gate is unchanged.
- **M9 — content search & listing.** The list-vs-search fix: `list_guru_cards` /
  `list_guru_folder_items` / `list_zendesk_articles` / `list_zendesk_macros`
  **enumerate completely** (paginate to the end), distinct from the query-ranked
  `search_*` tools; `search_zendesk_articles` + `search_asana_tasks` added;
  `list_asana_tasks` + `GuruClient.list_cards` now paginate; a unified
  `search_content` fans out across Guru+Zendesk+Drive. Renn's prompt teaches which
  to use. (Live bug fixed during testing: `create_folder` routed on the full
  slash-bearing `homeBoardSlug` → 404; now routes on the short id + guards read-only
  collections.)

Plan + full checklist: `~/.claude/plans/renn-google-asana-integration.md`.

---

## Left

### Phase 2 — policy / product-update signal *(the biggest missing input; the RCM wedge)*
- **2.1** GitHub ingestion — releases + `compare` filtered to watched files → fire an update task when a release post-dates a card.
- **2.2** Asana launch-trigger — read custom fields; `Status=Shipped` / launch date → KB-update task. *(✅ the create-task gap is closed — M7 added a gated `create_asana_task`; the remaining work is the launch-field → task **trigger** wiring on `asana_monitor`.)*
- **2.3** Drive un-mock — real policy-doc ingestion + mod-timestamp staleness. *(✅ the live-access blocker is gone — M1–M3 shipped per-user OAuth + the Drive folder picker + `DriveReader` shared-drive surface; remaining is feeding the picked `active_folders` into the ingestion monitor + the staleness signal.)*
- **2.4** Rising-term → card-staleness wiring.

> External dependencies: a GitHub token + watched repos, an Asana create scope, a Drive service-account share. Confirm sources before building.

### Phase 3 — content-health depth + connect-anywhere
- **3.1** Contradiction detection vs source truth ("card says X, the doc says Y").
- **3.2** Connect-anywhere read-only data layer (point Renn at an external DB via the same task-tool interface).
- **3.3** Tiered model router (route by capability tier; escalate hard steps to a higher tier).

### Phase 4 — agent maturity & scale
- **4.1** No-code review-routine builder (save scored signal → draft → queue routines).
- **4.2** Autonomous overnight signal → draft loop (still draft-only).
- **4.3** Customer-friction signal (fast-follow; plugs into the same scoring interface).
- **4.4** Standalone-extraction prep.

### Deferred low-severity refinements
- `enablement_health/signals.py`: card-vs-doc cosine uses separate TF-IDF spaces (a shared IDF would be sounder; thresholds are currently tuned to the split spaces).
- Pluggable hosted embedding (Bedrock Titan) behind the catalog vectoriser for the synonym tail (currently TF-IDF only).

---

## Testing

New tests are kept **local/gitignored** (`tests/test_*_local.py`). Run in groups
of 3–4 files (full `tests/` hangs on Windows); UI tests need
`QT_QPA_PLATFORM=offscreen`. Migrations `033`–`035` apply via the normal
`DatabaseManager.initialize()` boot path. In-app: set `enablement.demo_mode=false`,
`enablement.provider=claude`, connect Guru, and **restart** after tool changes
(the MCP server is persistent).
