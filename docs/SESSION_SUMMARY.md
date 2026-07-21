# Session Summary & Handoff — Alma Insights

**Purpose of this doc:** a self-contained handoff for a fresh model pass ("additional pass" via fable/ultracode). Most of it is context; the **live, unsolved problem is Part 3 — the macOS QtWebEngine "Agent renders blank"** — and the section "Ruled out — do NOT re-try" exists specifically so a fresh pass doesn't repeat days of dead ends.

**Environment note (load-bearing):** the whole session was driven from a **Windows dev box** (`C:\alma-insights`). The live problem is on a **separate macOS Apple Silicon machine** that the assistant could not see — all Mac results were relayed by the user. Repo branch `enablement-content-tabs`, pushed to `origin` @ `7adbe7b`; twin branch `enablement-surgical-updates` exists for a "workclaude" (low-Sonnet) surgical-update pass.

---

## Part 1 — Phase 1.5 review, security sweep, docs, push (DONE)

- **Reviewed Renn "Phase 1.5" (proactive chief-of-staff, M1–M6a):** code audit + claim verification + plan assessment. M1–M5 + M6a are shipped/tested; **M6b (background research engine) is built but UNWIRED** — `run_research_job` has zero callers, the two chat tools aren't registered, no React approval card. Landed two quick wins: a `RENN_SYSTEM_PROMPT` guardrail so Renn stops offering research it can't run, and escaping untrusted Asana titles in the calendar day-drilldown.
- **Security/compliance sweep** (pip-audit + bandit + npm audit + a 6-dimension multi-agent sweep, then per-finding verification). Bottom line: **NOT airgap-capable by design** (Gemini CLI→Google, Claude CLI→Anthropic, all integrations cloud). Confirmed HIGHs: **redaction fails OPEN** (`src/llm/claude_client.py:83` — logs then sends un-redacted on exception), **PHI routing overridable to non-BAA** (`src/gemini/client_factory.py::resolve_provider_for_task`), **Claude CLI telemetry/auto-updater not disabled** (`src/agents/claude_cli_bridge.py::_build_subprocess_env`), GitHub auto-update phones home on launch. **Secrets cluster** (verified): live GCP service-account key in `~/Downloads` referenced by `settings.yaml`; `pat_store` migration renames-not-deletes → live cleartext tokens in `~/.alma-insights/credentials.json.migrated`; GitHub PAT mis-routed to plaintext `ui_state.json`; Gemini key temp-file no cleanup on spawn failure. **PHI/egress cluster** (verified): `registry.dispatch_tool` returns tool results with no central redaction; Renn's lane boundary is a prompt line only (can reach warehouse tools like `read_thread`); "BAA via Bedrock" is comment-only.
- **Operational reframe (from the owner):** in production the Claude lane is the **AWS/Bedrock CLI (BAA-covered)** and **no `anthropic_api_key` is present** when the Bedrock CLI is deployed → the direct-`api.anthropic.com` branch is not reachable in prod. So the PHI cluster is **defense-in-depth within a BAA boundary**, not an active breach — but it's config-enforced, not code-enforced (dev boxes / misconfig can diverge).
- **Committed + pushed:** `docs/SECURITY_COMPLIANCE_REVIEW.md`, `docs/WORKCLAUDE_IMPLEMENTATION_GUIDE.md` (self-contained surgical-update guide — security P0, M6b wiring with grep anchors, review cleanups; caveats: local `*_local.py` tests don't travel, the React bundle IS committed, do NOT touch the redaction/corpus workstream), `docs/BRANCH_INTEGRATION_GUIDE.md`. **Un-ignored + committed the React bundle** (`src/ui/web/dist/`) for airgap installs. The **redaction/corpus workstream stayed uncommitted and untouched** the entire session.
- **Branch reconciliation guidance:** the user has a ~2-week-divergent work-local branch (~400-file merge). Recommended strategy: **replay the small local delta ONTO GitHub HEAD** (cherry-pick), never merge the big history into the stale local. Human/capable-model job, not workclaude.

## Part 2 — Harpax review (DONE — decision locked)

Reviewed Cambric's own **Harpax v2 Hybrid** (`C:\harpax_hybrid`, Go — a Claude Code security runtime: hook + PTY shim, tiered detection, goja JS rules, SQLite + Tauri dashboard). **Decision: Harpax stays a standalone product; it is NOT bolted into Alma.** Alma solves its own PHI/injection in-house (per-lane tool allowlist, central redaction in `dispatch_tool`, BAA hard-fail routing). Enforcement reality: `pre_tool` can block a tool call inline; `post_tool` is observe-only (no result rewrite) → Harpax could own P2 (block PHI tools), not P1 (redact results) or P3 (route enforcement).

---

## Part 3 — macOS QtWebEngine "Agent renders blank" (LIVE / UNSOLVED)

**Symptom:** the embedded Agent chat (`QWebEngineView` in `src/ui/web/agent_page.py`, loading a React bundle) renders **blank white** on the Mac; the rest of the PySide6 app works. Bundle is a single self-contained inlined `index.html` (~187 KB) + `qwebchannel.js`, loaded via `setUrl(QUrl.fromLocalFile(...))` with an in-process `QWebChannel` bridge (`almaBridge`).

### Ruled out — do NOT re-try (each cost hours)
- **QtWebEngine install / .so libs** — those are a pip dependency (in `site-packages/PySide6/`), never in the repo; that was a red herring.
- **Code signing** — `QtWebEngineProcess` helper now has `com.apple.security.cs.allow-jit` (+ `allow-unsigned-executable-memory`, `disable-library-validation`); `codesign --verify --deep --strict` on the framework = `SEAL OK`; Python re-signed. (Detour: the real interpreter is `python3.12`, not `python3.13`; and never use `codesign --deep` for signing — sign inner→outer, leaf-first; Qt ships `QtWebEngineProcess.entitlements` inside the framework.)
- **QtWebEngine version / module loader** — an inline `<script type="module">` executes fine (`MODULE RAN` printed). Not a version bug, not the ES-module loader.
- **Bundle chunks / CORS** — fully inlined single file, no external `assets/*.js`.
- **`_LoggingPage` subclass / QWebChannel object identity** in isolation.
- **`--single-process`** — renders the *minimal* test but **NOT the full agent (throws an error)**. It is out.
- **`app://` custom URL scheme handler** — tried (scheme registered pre-`QApplication`); **renderer died before the handler was ever hit.** Failed.

### Established facts
- **Minimal `setHtml("<html><body>red</body></html>")` renders reliably** (both `python3` and `python3.12`, no flags, multi-process). Clean process.
- **The real 187 KB React bundle over `file://` does NOT render:** `loadFinished True` then renderer dies `NormalTerminationStatus exit=0` with **no JS executed**; in the full app it's `KilledTerminationStatus exit=9` + `channel_mac.cc … mach_msg receive: (ipc/rcv) msg too large (0x10004004)`.
- **The differentiator is the full-app process, not the bundle content per se** — the full app also floods the log with a caught **cross-thread SQLite violation** in `source_monitor` (`SQLite objects created in a thread can only be used in that same thread`), i.e. an unhealthy host process. That bug is real and cross-platform (present on Windows too, just never noticed because the agent renders there) but it is **caught/logged noise, not the render cause.**
- A React `useBridge` race (reading `qt.webChannelTransport` before Qt injects it) was found and **already fixed in source** (`web/src/App.jsx:111` guards it); the bundle was rebuilt + redeployed to the Mac install, but **blank persists** — so the bridge race was a real bug but not the whole story; the installed bundle had merely been stale.
- **THE ONLY CONFIRMED FULL-AGENT RENDER ON MAC IS A LOCALHOST / LOCAL-PORT SERVER** (a real `http://` origin → the whole React bundle just runs). `file://` (opaque/null origin) is what macOS QtWebEngine 6.8.1 chokes on; Windows QtWebEngine tolerates it.

### Current recommendation (NOT yet implemented)
**Serve the agent bundle from a `127.0.0.1` loopback HTTP server** in `agent_page.py`, macOS-guarded (Windows keeps `file://`, which works there). Load `http://127.0.0.1:<ephemeral-port>/index.html`. Rationale: it is the **only path that has ever fully rendered the agent on the Mac**, and a loopback server is **not an airgap violation** (no external egress; bound to `127.0.0.1`, unreachable from the network; serves only the static UI bundle — no PHI/data). QWebChannel works unchanged (its transport is independent of the page URL). Sketch:
```python
import http.server, socketserver, threading, functools
def _serve_local(root):
    h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=root)
    httpd = socketserver.TCPServer(("127.0.0.1", 0), h)          # loopback only, OS-picked port
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]
# macOS: self._httpd, port = _serve_local(dist); view.setUrl(QUrl(f"http://127.0.0.1:{port}/index.html"))
# else : view.setUrl(QUrl.fromLocalFile(dist))
```
**Durable fix (separate, networked-Mac build job):** a properly **signed + notarized + stapled `.app`** with frameworks in the standard `Contents/Frameworks` layout — where `file://` or `app://` may be made to work and multi-process becomes reliable. macOS was only ever scaffolded (roadmap M7). `installer/ci/sign_macos.sh` was patched this session for the helper JIT entitlements but still emits a `.zip` (can't be stapled → needs `.dmg`/`.pkg` for offline Gatekeeper).

---

## Files created/modified this session
- **Committed + pushed** (branch `enablement-content-tabs` / `enablement-surgical-updates`): `docs/SECURITY_COMPLIANCE_REVIEW.md`, `docs/WORKCLAUDE_IMPLEMENTATION_GUIDE.md`, `docs/BRANCH_INTEGRATION_GUIDE.md`, `src/ui/web/dist/` (React bundle un-ignored), the two Phase-1.5 quick-win fixes in `src/ui/pages/enablement/page.py`.
- **Local (this box), NOT necessarily pushed:** `scripts/sign_qtwebengine_dev.sh` (offline inner→outer ad-hoc sign of the pip QtWebEngine + Python), `scripts/collect_mac_diag.sh` (read-only macOS diagnostics), patched `installer/ci/sign_macos.sh` (JIT entitlements + notarize/staple + `.zip` warning), a debug harness added to `src/ui/web/agent_page.py` (`_LoggingPage` JS-console + `renderProcessTerminated` + request logger, gated by `ALMA_AGENT_DEVTOOLS`).
- **Memory files updated** (`~/.claude/projects/C--alma-insights/memory/`): `renn_security_compliance.md`, `cambric_harpax.md`. The macOS saga is only in the chat / this doc.

## Open items (priority order)
1. **Implement the macOS localhost-server render** in `agent_page.py` — the recommended, only-working fix. (This is the thing for the fresh pass.)
2. Fix the `source_monitor` cross-thread SQLite bug (per-thread `get_connection()`, not a shared connection) — hygiene.
3. Security P0s (redaction fail-CLOSED; per-lane `phi_level` allowlist at `dispatch_tool`; BAA hard-fail routing; `scan_server` node-forge bump; the secrets fixes).
4. M6b research wiring; the durable macOS `.app` build (sign + `.dmg` + notarize + staple).

## What the fresh pass should actually chew on
The sharpest open question: **why does localhost render the full agent but `file://`, `app://`, and `--single-process` all fail on this exact QtWebEngine-6.8.1 + relocated-pip-bundle + Apple-Silicon combo** — and is the loopback-server workaround acceptable long-term, or must the proper signed `.app` build be finished? Everything under "Ruled out" is already eliminated; a fresh pass should not re-run those.
