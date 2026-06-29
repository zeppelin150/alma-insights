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

## Left

### Phase 2 — policy / product-update signal *(the biggest missing input; the RCM wedge)*
- **2.1** GitHub ingestion — releases + `compare` filtered to watched files → fire an update task when a release post-dates a card.
- **2.2** Asana launch-trigger — read custom fields; `Status=Shipped` / launch date → KB-update task. *(Also: Renn cannot create a top-level Asana task today — only subtasks on board-sourced tasks; needs an Asana create-task tool + scope.)*
- **2.3** Drive un-mock — service-account share → real policy-doc ingestion + mod-timestamp staleness.
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
