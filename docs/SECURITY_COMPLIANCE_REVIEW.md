# Security & Compliance Review — Airgap / SOC 2 / HIPAA

**Date:** 2026-07-01 · **Branch:** `enablement-content-tabs` · **Scope reviewed:** the committed codebase as pushed (Phase 1.5 Renn + prior enablement work + core LLM/routing/redaction). **Explicitly NOT reviewed:** the uncommitted redaction/corpus workstream (`redaction_engine.py`, `fast_path.py`, `redaction_signal.py`, `*_local.py`, `scripts/corpus_gen/`, …) — it is mid-refactor and did not travel in this push.

**Method:** `pip-audit 2.10.1` + `bandit 1.9.4` + `npm audit` scanners, plus a 6-dimension multi-agent compliance sweep (egress, secrets, PHI, SOC2, offline-mode, Phase-1.5 fresh-eyes) with adversarial verification. **The adversarial verify phase was cut short by an API rate limit** — 6 findings were verified by a 3-vote refuter panel; the rest are marked **UNVERIFIED** below and must be confirmed before any attestation. Four of the highest-impact items were then re-verified by hand (citations inline).

---

## Bottom line

> **This application is NOT fully airgap-capable by design, and it does not currently meet a HIPAA/SOC 2 bar without remediation.** Its core value features (NLP scanning, Renn chat, report generation, and every third-party integration) depend on outbound cloud calls. On a truly offline machine those features go dark; several channels also *phone home by default* even when they aren't needed, and the PHI-redaction layer **fails open**.

Nothing here is a reason to withhold the push — these are pre-existing posture issues plus two Phase-1.5 items, and they are exactly the backlog the surgical-update pass should burn down. But **do not represent the app as "airgapped / SOC 2 / HIPAA compliant" until the P0 items below are closed.**

**Update 2026-07-01 — both starved clusters re-verified (one reader per finding).** The PHI/egress cluster is now confirmed and it is worse than "needs remediation": there are **confirmed code paths where raw ticket PHI reaches the non-BAA Anthropic API** — the MCP tool-result path has no central redaction (P1/P6), `phi_level` is never enforced so Renn can call warehouse tools (P2), and "BAA via Bedrock" is comment-only with the direct API as the default (P3). One myth busted: the Gemini client fails *closed* (P4), so only the Claude lane fails open. See **"PHI/egress cluster — verified verdicts"** and **"Secrets cluster — verified verdicts"** below.

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

**PHI / HIPAA — RE-VERIFIED 2026-07-01** (one reader per finding; moved to the CONFIRMED table below). See **"PHI/egress cluster — verified verdicts"**.
- Still open leads (lower priority): PHI at rest unencrypted (SQLite warehouse/FTS/transcripts, `src/data/connection_factory.py` — *known accepted posture*); Zendesk 2-min poll + Settings-page `npm install` egress (below).

**Secrets / SOC 2 — RE-VERIFIED 2026-07-01** (the starved secrets cluster, each hand-confirmed by reading the cited code; moved to the CONFIRMED table below). See **"Secrets cluster — verified verdicts"**.

**Egress / offline:**
- [UNVERIFIED] Zendesk polling timer — egress every 2 min at startup — `src/ui/main_window.py`
- [UNVERIFIED] Runtime `npm install` of the Gemini CLI from the Settings page (`registry.npmjs.org`) — `src/data/gemini_setup.py`

---

## PHI/egress cluster — verified verdicts (re-ran the starved verification, 2026-07-01)

Six readers, one per finding. **This is the HIPAA crux — 4 CONFIRMED HIGH, 2 PARTIAL.** They share ONE root cause: **there is no central redaction chokepoint on the MCP tool-result path, `phi_level` is recorded but never enforced, the enablement/Renn lane boundary is a prompt instruction (not an allowlist), and the Claude lane defaults to the non-BAA direct Anthropic API.** These are *pre-existing* PHI plumbing (not introduced by Phase 1.5), present on HEAD (what's on GitHub).

| ID | Finding | Verdict | Where |
|---|---|---|---|
| **P1** | **Tool results bypass redaction.** `registry.dispatch_tool` returns `json.dumps(handler_output)` verbatim — no central redaction. `ClaudeClient._redact_text` scrubs only the outbound prompt, and the native-MCP path (the only working Claude tool path) feeds results straight to the model. `thread_tools` self-redacts with a **3-of-10 mini-regex** (SSN/email/phone — omits MRN/member-ID/DOB/address); `search_conversations` redacts **nothing**. | **CONFIRMED · HIGH** | `registry.py:415` · `thread_tools.py:19,155` · `chat_mcp_server.py:1447` |
| **P2** | **Renn can reach warehouse PHI.** The Agent chat launches the *full* `chat_mcp_server`; the only lane scoping is `ALMA_MCP_EXCLUDE_TOOLS=semantic_search` (one tool). `read_thread`(phi 2)/`read_threads_batch`/`list_tickets`/`query_*` stay callable; `dispatch_tool` has no `phi_level` gate. Boundary = the RENN_SYSTEM_PROMPT "use only enablement tools" line — overridable by the model or by prompt-injection from an Asana title/Drive doc/Guru card. `client_factory.py:128` also sets `pii_redaction=False` for enablement lanes. | **CONFIRMED · HIGH** | `agent_chat.py:1478,1464` · `registry.py:79-99` |
| **P3** | **"BAA via Bedrock" is unenforced.** Task→claude builds the **direct `api.anthropic.com`** client when an API key is present; Bedrock only if `bedrock.enabled=true` (default **false**). Grep `force.*bedrock`/`phi.*bedrock` = zero. `force_cli=True` (enablement only) forces the *CLI*, not Bedrock. | **CONFIRMED · HIGH (P0)** | `client_factory.py:113-170` · `claude_client.py:27` · `settings.yaml:7` |
| **P4** | **Redaction fail-open — Claude only.** `claude_client.py:82` returns raw text on a redaction exception (fail-OPEN, non-BAA lane = HIGH). **`gemini_client.py` has no try/except → fails CLOSED** (the "both layers" claim is **refuted for Gemini**). Base redaction is non-disableable on both; aggressive/name pass is off for `enablement_*`. | **PARTIAL** (Claude HIGH; Gemini refuted) | `claude_client.py:82` · `gemini_client.py:194-329` |
| **P5** | **`watchlist_triage` → non-BAA Claude with raw ticket text.** `_llm_triage` inlines raw `subject` + `description[:500]`; routes to Claude (`force_cli=False`) → direct API when a key is set. **Mitigated** by the client's base-redaction pass — **but** a latent bug (`from …gemini_client import _redact_base`, an *instance* method → always `ImportError`) silently degrades the in-engine layer to email-only. | **PARTIAL · HIGH** | `watchlist_engine.py:387,390` · `client_factory.py:38,116` |
| **P6** | **Legacy `fast_path` tools return raw `SELECT *` rows** (subject/body/`issue_snippet`/`thread_preview`) with **zero** redaction — identical on HEAD and working-tree (the uncommitted rework only adds date-filter coercion, **not** redaction). Reachable by the Claude lane via `override_all=claude`. | **CONFIRMED · HIGH** | `fast_path.py:651,736,112` |

### The one fix that closes most of this
A **central redaction chokepoint in `registry.dispatch_tool`** — run `RedactionEngine.scrub()` over every tool result with `phi_level ≥ 1` before `json.dumps`, and delete the per-handler mini-redactors — collapses P1 + most of P6 and the tool-result half of P2. Pair it with a **hard per-lane tool allowlist** (reject `phi_level>0` for enablement sessions) for the rest of P2, and a **BAA hard-fail** in routing (P3/P5) so a PHI task can never silently use the direct Anthropic API. **These touch the redaction path you are actively reworking — coordinate; don't double-implement.**

---

## Secrets cluster — verified verdicts (re-ran the starved verification, 2026-07-01)

Six per-finding verifiers each read the cited code. **4 CONFIRMED (2 HIGH, 2 MED), 2 PARTIAL (real mechanism, dormant in this checkout).** Secret *values* were never printed. **`data/settings.yaml` is gitignored/untracked — nothing here leaked via the GitHub push;** the exposures are plaintext-at-rest on the local machine plus code defects that travel.

| ID | Finding | Verdict | Where |
|---|---|---|---|
| **S1** | GitHub PAT saved to plaintext `ui_state.json` — writer uses key `"github_pat"` but `pat_store._SECRET_KEYS` only allowlists `"github_update_token"`, so it falls through to cleartext JSON (Windows skips the `0o600` guard). **Latent** — no PAT on disk yet; triggers on first Save. | **CONFIRMED · MED** | `settings_page.py:1645` · `pat_store.py:53,157` |
| **S2** | `pat_store` migrates to the OS keyring (real encryption — good) but disposes of the legacy `credentials.json` by **`.rename()` to `.json.migrated`**, never deleting/scrubbing. That file **exists on this machine with live cleartext tokens** (Lightdash, Guru len-36, Zendesk len-40). | **CONFIRMED · HIGH** | `pat_store.py:240` · on-disk `~/.alma-insights/credentials.json.migrated` |
| **S3** | `enablement.drive.credentials_path` points **active** code (`drive_reader`→`drive_monitor`/`enablement_monitor`/`agent_chat`) at a **real GCP service-account private key** in `~/Downloads/claims-automation-*.json` — outside the secret store, unencrypted, non-expiring. "claims-automation" ⇒ likely PHI-adjacent Drive access. | **CONFIRMED · HIGH** | `settings.yaml:96` · `drive_reader.py:80` |
| **S4** | Gemini key written to a plaintext temp file (`--api-key-file`); Node unlinks after read, but **no Python-side `finally`** — a failed `Popen` orphans the cleartext key (mkstemp `0600` weak on Windows). Sibling spawners use env vars and are clean. | **CONFIRMED · MED** | `scan_server_manager.py:124-173` |
| **S5** | `github_app_auth._load_private_key()` **can** read a `private_key_pem` inline from plaintext `settings.yaml` (bypassing `pat_store`) — but no key present, `auth_mode` defaults to `pat` (not `github_app`), feature dormant. Latent design risk MED. | **PARTIAL · LOW** | `github_app_auth.py:107` |
| **S6** | `call_trace` logs args/kwargs verbatim with **no redaction** to a persistent `data/trace/*.jsonl` — but **off by default**, never wired into `main.py` (only tests + a dev script opt in), and traced methods get `self` (no custom `__repr__` ⇒ key not serialized). One edge: `asana_setup.discover(api_key=…)` would log a raw key, but the in-app caller passes none. | **PARTIAL · LOW** | `call_trace.py:55,130` |

### ⚠️ Operator actions (live secrets on THIS machine — not workclaude's job, do these yourself)
- **Rotate/revoke the GCP service-account key** `claims-automation-*` in the GCP console, move the JSON out of `~/Downloads` into an OS-restricted path (or switch Drive to `oauth_user`), and update `settings.yaml`. *(S3 — highest urgency: a live, non-expiring, possibly PHI-scoped key in a sync-prone folder.)*
- **Delete `~/.alma-insights/credentials.json.migrated`** after confirming the keyring has your Lightdash/Guru/Zendesk tokens (Settings still works). *(S2)*

### Code fixes (fold into workclaude Task A)
- **S2 [HIGH]:** `pat_store.migrate_legacy_credentials()` — overwrite-then-`unlink` the legacy file (not `.rename`), + a startup sweep of any existing `*.migrated`.
- **S1 [MED]:** `settings_page.py` — use `"github_update_token"` for the PAT save/read (the canonical key already in `_SECRET_KEYS`).
- **S4 [MED]:** `scan_server_manager.py` — wrap spawn in `try/finally` and always `os.unlink(api_key_file)`, or pass the key via `env` like the sibling spawners.
- **S3 [HIGH, code side]:** don't source a service-account key from a plaintext settings path pointed at Downloads — route through `pat_store` / an OS-restricted location.
- **S5 [LOW]:** route `private_key_pem` through `pat_store` or drop the inline option.
- **S6 [LOW]:** add a sensitive-name denylist to `call_trace._short` (mirror `src/core/crash_handler.py`'s redaction).

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
- [ ] **P1/P6 [HIGH] Central redaction chokepoint** — run `RedactionEngine.scrub()` over every `phi_level ≥ 1` tool result in `registry.dispatch_tool` before `json.dumps`; delete the per-handler mini-redactors. *(Coordinate with the active redaction rework.)*
- [ ] **P2 [HIGH] Hard per-lane tool allowlist** — reject `phi_level>0` tools for enablement/Renn sessions at the dispatch chokepoint (not a prompt instruction, not a one-tool exclude).
- [ ] **P3/P5 [HIGH] BAA hard-fail routing** (`client_factory.py`) — PHI-bearing task types non-overridable to `claude`; if a PHI task resolves to Claude without `bedrock.enabled`, **raise**, don't silently use direct Anthropic. Fix the dead `_redact_base` import in `watchlist_engine.py:390`.
- [ ] **P4 [HIGH] Redaction fail-CLOSED** (`claude_client.py:82`) — raise on redaction failure instead of sending un-redacted; add a test. (Gemini already fails closed.)
- [ ] **`scan_server/` node-forge → ≥1.4.0** (`npm audit fix`) and rebuild the bridge.
- [ ] **S2 [HIGH]** `pat_store` migration: scrub+`unlink` the legacy file (not `.rename`) + startup sweep of `*.migrated`.
- [ ] **S3 [HIGH]** operator-rotate the exposed GCP service-account key + stop sourcing it from a plaintext Downloads path.
- [ ] **S1 / S4 [MED]** GitHub-PAT key-name fix (`settings_page.py`) and Gemini temp-key `try/finally` cleanup (`scan_server_manager.py`).

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

*Scanner raw output archived in the workflow run `wf_65c8ce68-e60`. Both the secrets cluster and the PHI/egress cluster were re-verified 2026-07-01 (see their verified-verdict sections). **The confirmed PHI-to-non-BAA paths (P1–P3, P6) mean the current build does not meet a HIPAA bar without the P0 fixes — do not sign a BAA-dependent attestation until those land.** Remaining low-priority leads: PHI-at-rest encryption (known posture), Zendesk 2-min poll + Settings-page `npm install` egress (airgap items).*
