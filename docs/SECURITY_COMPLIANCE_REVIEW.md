# Security & Compliance Review — Airgap / SOC 2 / HIPAA

**Date:** 2026-07-01 · **Branch:** `enablement-content-tabs` · **Scope reviewed:** the committed codebase as pushed (Phase 1.5 Renn + prior enablement work + core LLM/routing/redaction). **Explicitly NOT reviewed:** the uncommitted redaction/corpus workstream (`redaction_engine.py`, `fast_path.py`, `redaction_signal.py`, `*_local.py`, `scripts/corpus_gen/`, …) — it is mid-refactor and did not travel in this push.

**Method:** `pip-audit 2.10.1` + `bandit 1.9.4` + `npm audit` scanners, plus a 6-dimension multi-agent compliance sweep (egress, secrets, PHI, SOC2, offline-mode, Phase-1.5 fresh-eyes) with adversarial verification. **The adversarial verify phase was cut short by an API rate limit** — 6 findings were verified by a 3-vote refuter panel; the rest are marked **UNVERIFIED** below and must be confirmed before any attestation. Four of the highest-impact items were then re-verified by hand (citations inline).

---

## Bottom line

> **This application is NOT fully airgap-capable by design, and it does not currently meet a HIPAA/SOC 2 bar without remediation.** Its core value features (NLP scanning, Renn chat, report generation, and every third-party integration) depend on outbound cloud calls. On a truly offline machine those features go dark; several channels also *phone home by default* even when they aren't needed, and the PHI-redaction layer **fails open**.

Nothing here is a reason to withhold the push — these are pre-existing posture issues plus two Phase-1.5 items, and they are exactly the backlog the surgical-update pass should burn down. But **do not represent the app as "airgapped / SOC 2 / HIPAA compliant" until the P0 items below are closed and the UNVERIFIED findings are confirmed or cleared.**

---

## Airgap verdict (offline behavior)

| Subsystem | Offline behavior | Verdict |
|---|---|---|
| NLP scan pipeline (Gemini CLI → Google) | Cannot reach `generativelanguage`/`cloudcode.googleapis.com` + OAuth refresh | **Dark** — feature unusable offline |
| Renn / enablement chat (Claude CLI → Anthropic, or Gemini) | CLI subprocess can't reach the API | **Dark** |
| Report generation (Gemini bridge) | Same as scan | **Dark** |
| Integrations: Asana, Guru, Zendesk, Drive, Lightdash | Polled on timers; all cloud APIs | **Dark**, and see auto-egress findings |
| GitHub auto-update check | Bounded 5s timeout → degrades to a warning | **Graceful** (verified) |
| Local data browsing / analytics / warehouse SQL | Pure-local SQLite | **Works offline** |

**What "offline mode" realistically means for this app:** local data browsing + local analytics only. Every AI and integration feature requires the network. An honest airgap story requires either (a) accepting those features are disabled offline, or (b) a local-inference backend (out of scope here).

---

## CONFIRMED findings (verified)

### C1 — [HIGH] Claude CLI subprocess runs with telemetry / error-reporting / auto-updater ENABLED
`src/agents/claude_cli_bridge.py:220` — `_build_subprocess_env()` does `os.environ.copy()` and adds only Bedrock vars. It never sets `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, `DISABLE_TELEMETRY`, `DISABLE_AUTOUPDATER`, `DISABLE_ERROR_REPORTING`, or `DISABLE_BUG_COMMAND`. A repo-wide grep confirms those disables exist **only** for HuggingFace (`HF_HUB_DISABLE_TELEMETRY`, `src/startup/env_guard.py:40`), never for the `claude` CLI — the lane **Renn (Phase 1.5) runs on**. Impact: outbound telemetry + an auto-updater that can silently mutate the CLI binary (SOC 2 change-control), plus egress that violates the airgap premise. *Hand-verified.*
**Fix (low-friction):** in `_build_subprocess_env`, set the four `CLAUDE_CODE_*`/`DISABLE_*` env vars to `"1"`. Best done in `src/startup/env_guard.py` alongside the HF disables so it's centralized.

### C2 — [MED] Auto-update check phones home to `api.github.com` on every launch, default-on, with a bundled token
`src/startup/checks/updates.py` (`_fetch_latest`, GET `…/releases/latest` with `Authorization: Bearer`). Runs synchronously during splash (and headless `--no-splash`, `main.py:132`). Offline it raises `_UpdateCheckError("Network unavailable")` and degrades to a warning (bounded `_TIMEOUT_SECONDS` — **no hang**), but online it is unconditional egress. *Hand-verified.*
**Fix:** gate behind an `updates.enabled` setting defaulting to `false` for airgapped builds; never ship a real token in the default config.

### C3 — [MED] IT-security documentation is false about network behavior
`installer/README_IT_SECURITY.md:183` §5 claims *"No telemetry, analytics, crash reporting, or auto-update connections are made."* In reality the runtime contacts `api.github.com` (C2), `api.anthropic.com` (Claude), Google endpoints (Gemini + OAuth), and the integration APIs. A false network-behavior attestation is itself a SOC 2 / HIPAA finding.
**Fix:** rewrite §5 to enumerate every egress destination, trigger, and default state. This doc is the compliance artifact — it must match reality.

### C4 — [MED] Gemini CLI (the PHI lane) is inherently cloud-dependent
`src/agents/acp_bridge.py:339` (+2 other spawn points) run the Gemini CLI → `oauth2.googleapis.com` (token refresh from `~/.gemini/google_accounts.json`), `cloudcode-pa.googleapis.com` / `generativelanguage.googleapis.com`. This is the BAA-covered PHI path *by design*, but it means the classification pipeline cannot run airgapped. Documents the airgap gap; not a bug.

### C5 — [MED] Renn auto-greeting fires an unprompted LLM network call on Agent-page load  *(Phase 1.5, in-scope)*
`src/services/agent_chat.py` — `__init__` schedules `QTimer.singleShot(0, self._open_greeting)`; `_open_greeting` auto-sends a "morning briefing" turn via `self._engine.send(trigger)`, spawning the Claude CLI **without the operator typing anything**. Offline this fails/times out on page load; online it spends tokens unprompted. One verifier voted downgrade (it's summarize-only and guarded by identity+dated-tasks). *Consistent with my earlier code read.*
**Fix:** gate the auto-send behind an `enablement.startup_greeting_enabled` setting (default `false`), and/or make it build the local `[TODAY'S PLAN]` block **without** an LLM round-trip when offline.

### C6 — [MED] Asana polling timer — automatic recurring egress every 5 min
`src/data/asana_monitor.py` — `AsanaMonitor` QTimer (default 300s, min 60s) started at launch by `src/ui/main_window.py:925` whenever enablement mode is live + a PAT is stored. Two verifiers voted downgrade (it's user-configured integration, not covert). Note for airgap: confirm the poll has an explicit socket timeout so it fails fast offline.

---

## UNVERIFIED findings — the verifier phase starved on a rate limit

These were surfaced by the sweep but **NOT** confirmed. Most concern the **uncommitted redaction/PHI workstream or pre-existing PHI plumbing — outside this push.** Treat as leads to confirm, not facts. **Two I re-verified by hand are marked ✓.**

**PHI / HIPAA (confirm before any HIPAA claim):**
- ✓ **Redaction fails open** — `src/llm/claude_client.py:83`: `logger.warning("PII redaction failed, sending un-redacted")` then sends. An exception anywhere in the redaction pipeline ⇒ PHI to the API unredacted. **Confirmed true. Should fail CLOSED (block/raise).** *(pre-existing)*
- ✓ **Config can route raw ticket text to the non-BAA Anthropic API** — `src/gemini/client_factory.py:80` (`resolve_provider_for_task`): `ai.task_routing.override_all=claude` or `enablement.provider=claude` routes ticket-bearing tasks (e.g. `nlp_classification`) to Claude, gated only by the fail-open redaction above. **Confirmed architecturally possible.** *(pre-existing)*
- [UNVERIFIED] MCP-native tool results bypass redaction; thread tools use a 3-pattern mini-redactor — `src/data/chat_tools/thread_tools.py`
- [UNVERIFIED] Legacy chat tools return raw subjects/previews/full rows — `src/data/chat_tools/fast_path.py` *(uncommitted workstream)*
- [UNVERIFIED] Renn agent chat can reach warehouse ticket threads despite the "decoupled" boundary — `src/services/agent_chat.py`
- [UNVERIFIED] "BAA-routed through Bedrock" asserted in comments, not enforced — `src/gemini/client_factory.py`
- [UNVERIFIED] PHI at rest unencrypted (SQLite warehouse, FTS, chat transcripts) — `src/data/connection_factory.py` *(known posture)*

**Secrets / SOC 2 (each needs a file read to confirm):**
- [UNVERIFIED] GitHub PAT written to plaintext `ui_state.json` — `src/ui/pages/settings_page.py`
- [UNVERIFIED] Legacy plaintext credentials file retained after keyring migration — `src/data/pat_store.py`
- [UNVERIFIED] Google service-account private key in plaintext in Downloads, referenced by `data/settings.yaml`
- [UNVERIFIED] Gemini API key transits a plaintext temp file, no cleanup on spawn failure — `src/data/scan_server_manager.py`
- [UNVERIFIED] `ALMA_TRACE=1` debug facility records raw credentials passed as function args — `src/data/call_trace.py`
- [UNVERIFIED] GitHub App private key inline in plaintext `settings.yaml` — `src/updater/github_app_auth.py`

**Egress / offline:**
- [UNVERIFIED] Zendesk polling timer — egress every 2 min at startup — `src/ui/main_window.py`
- [UNVERIFIED] Runtime `npm install` of the Gemini CLI from the Settings page (`registry.npmjs.org`) — `src/data/gemini_setup.py`

---

## Dependency CVEs (these ship — `requirements.txt` is committed)

`pip-audit` needs the PyPI advisory DB (itself an airgap constraint — **make dependency scanning a build-time/CI gate**, or mirror the OSV dataset internally). 20 vulns in 5 packages:

| Package | Worst | Fix | Airgap note |
|---|---|---|---|
| **nltk 3.9.1** | **CRITICAL** — downloader `extractall()` RCE (CVE-2025-14009); + 3 path-traversal file-reads | **3.9.4** | Airgap neutralizes the downloader RCE; the arbitrary-file-read CorpusReader bugs remain. **Low-friction bump.** |
| **pyjwt 2.10.1** | HIGH — verifier alg allow-list bypass (CVE-2026-48523) | **2.13.0** | Exploitability depends on whether the app verifies attacker-controlled JWTs (likely transitive via google-auth). **Low-friction bump.** |
| **transformers** (transitive via `sentence-transformers==3.3.1`; lock pins 4.46.3) | CRITICAL — RCE via malicious model `config.json` (CVE-2026-4372) | 5.3.0 | Airgap neutralizes remote-model-load RCE (models are local). **Fix is a major bump (sentence-transformers 4.x) — do NOT blind-bump; validate.** |
| **markdown 3.7** | MED — DoS on malformed HTML | **3.8.1** | App uses this for doc→card; chat markdown now renders in JS. **Low-friction bump.** |
| pytest 8.3.4 | LOW (test-only, not shipped) | 9.0.3 | Ignore for the bundle. |

**npm (`scan_server/` ships; `web/` is build-only):**
- **`scan_server/` node-forge <1.4.0** — 4 HIGH (signature-forgery ×3 + DoS). **RUNTIME dep of the shipped bridge — highest-priority npm item.** Fix `>=1.4.0` (non-major, `npm audit fix`).
- `scan_server/` `path-to-regexp` ReDoS (HIGH, via express), `qs`/`express`/`body-parser` (MED) — non-major `npm audit fix`; bridge is localhost-only, limiting exposure. `uuid` bounds check → major bump, verify `server.js` usage first.
- `web/` vite/esbuild (1 HIGH, 3 MED) — **all devDependencies; the bundle is prebuilt via `vite-plugin-singlefile` so none ship.** Dev-machine hygiene only.

---

## Bandit SAST (1 real item; rest triaged)

- **[MED, real] `src/gemini/gemini_client.py:255`** — Windows Gemini launch builds a shell command via f-string and runs `shell=True`. Inputs are local (settings/model registry), so practical risk is moderate, but **trivially fixable**: pass the temp file as `stdin=` with a list `argv` (the POSIX branch two lines below already does exactly this).
- 136× B608 (SQL string construction) — dominated by the whitelisted-column `update_*` pattern already reviewed; parameterized where it counts. Spot-confirm any that interpolate a *value*.
- B603/B607 subprocess launches (Gemini/Node/claude CLI) = expected architecture, not findings.
- B104 `lightdash_client.py:132` "0.0.0.0" = false positive (string compare inside `_is_private_host`).

---

## Remediation checklist (priority order)

**P0 — before any compliance attestation**
- [ ] **C1** Disable Claude CLI telemetry/auto-updater/error-reporting via env (`env_guard.py`).
- [ ] **Redaction fail-CLOSED** (`claude_client.py:83`) — block/raise on redaction failure instead of sending un-redacted; add a test.
- [ ] **Routing guardrail** (`client_factory.py`) — refuse to route any ticket-bearing task type to `claude`/non-BAA even under `override_all`; make PHI lanes non-overridable.
- [ ] **`scan_server/` node-forge → ≥1.4.0** (`npm audit fix`) and rebuild the bridge.
- [ ] Confirm or clear the **UNVERIFIED secrets cluster** (PAT/service-account-key/temp-key/call_trace).

**P1 — airgap hardening**
- [ ] **C2** Gate the GitHub update check behind `updates.enabled=false` default; strip the bundled token.
- [ ] **C5** Gate the Renn startup greeting's LLM round-trip (`enablement.startup_greeting_enabled=false`; build the local plan block without an LLM call offline).
- [ ] **C3** Rewrite `README_IT_SECURITY.md` §5 to match real egress.
- [ ] Bump **nltk→3.9.4**, **markdown→3.8.1**, **PyJWT→2.13.0** (low-friction); re-audit `requirements.lock` closure at build.
- [ ] Audit every `urllib`/monitor call for an explicit socket **timeout** (no-timeout = offline hang).
- [ ] Make dependency scanning (pip-audit + npm audit) a **build-time CI gate** — it can't run in the airgapped target.

**P2 — hygiene**
- [ ] `gemini_client.py:255` shell=True → `argv` + `stdin=`.
- [ ] Evaluate SQLite encryption-at-rest (SQLCipher) for the PHI warehouse if PHI-at-rest is confirmed in scope.
- [ ] Consider committing `requirements.lock` + npm lockfiles for reproducible/pinned airgapped builds (currently gitignored).

---

*Scanner raw output archived in the workflow run `wf_65c8ce68-e60`. The rate-limited verifier phase should be re-run (or the UNVERIFIED items hand-confirmed) before this document backs any formal attestation.*
