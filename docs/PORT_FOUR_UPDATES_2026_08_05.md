# Port Guide — Four Owner Updates of 2026-08-05 (`ae0392d`)

**Written:** 2026-08-06 on the Windows dev box, from commit `ae0392d` on `enablement-content-tabs` and the post-commit tree itself (every embedded code block below is copied byte-for-byte from that tree, not retyped).
**For:** a Claude Sonnet instance on a DIFFERENT checkout of Alma Insights / Content Command Center (e.g. the Mac work machine) that may lag this branch and may have drifted.
**Mechanism:** surgical hand-application of the code in this document. **No branch merge, no `git fetch`/`pull`/`cherry-pick` of `ae0392d`** — the target tree may not even have the commit's parents. This document is self-contained: you should never need the conversation that produced it.

## What this ports

1. **Chat stop + queue-context (Agent page + Workbench drawer):** a Stop button that aborts the in-flight `claude` CLI turn by killing the whole subprocess tree (Windows `taskkill /T /F`, one choke point), and a composer that stays enabled while Renn is busy — extra messages queue with honest "queued" badges and drain one per turn.
2. **List-format parity (one shared converter):** `markdown_to_html` unwraps loose `<li><p>` list items to tight form (LLM-style lists no longer double-space on Guru/Zendesk while looking tight in Qt) and emits text glyph task markers (`☐`/`☑`) instead of `<input>` checkboxes no surface rendered; `html_to_markdown` restores `[ ]`/`[x]` fence-aware.
3. **Asana subtask promotion (migration 056):** assigned subtasks of tracked parent tasks promote into real `enablement_tasks` rows (`parent_task_ref`) — calendar chips with a `↳` marker on both calendars, mine-scope, Renn `list_tasks`, the morning greeting — with fail-closed vanish-dismissal, parent-dismissal cascade, and board attribution through `task_board_links` only.
4. **Zendesk copy-gate removal + dead-button fix:** the draft-status clipboard gate is deleted (any reviewed draft — pending/ready/copied/pushed — releases under the unchanged review-record hash gate + native byte-showing confirm; statuses stay hand-advanced bookkeeping), and the SPA's primary "Copy content" button works — `body_text` is now a served copy field for drafts and mirror articles, with a JS↔Python parity test.

Each workstream also carries the fixes from the owner's adversarial review of 2026-08-05 (4 verified majors + 8 minors). The code below already reflects them; each workstream section lists them under "Review fixes baked in" so you understand why the code looks the way it does. **Do not simplify them away.**

## True size

- **29 production files**, +1,180 / −190 lines (excluding tests and `dist`).
- **Tests:** ~1,600 more lines across 8 Python + 3 JS test files, including one entirely new 1,228-line suite (`tests/test_chat_stop_queue.py`, embedded verbatim in §7.1).
- **`src/ui/web/dist/index.html` is NOT ported.** It is a build artifact — after landing the `web/src` changes you regenerate it with `npm --prefix web run build`. Never hand-edit it, never copy this tree's dist bytes over.
- **1 new migration:** `migrations/056_subtask_promotion.sql` (embedded verbatim in §5.1).

Source commit: `ae0392d` — "feat(enablement): chat stop/queue, list-format parity, Asana subtask promotion, Zendesk copy-gate removal", branch `enablement-content-tabs`, 2026-08-06.

---

## 1. Prerequisites the target tree MUST have

This port lands on top of specific structures. For each prerequisite: run the structural check from the repo root. **If a check fails, STOP and report — do not improvise a substitute, do not build the missing subsystem, do not "adapt" the code.** A failed check means the target tree predates a subsystem this port extends, and the owner must decide what to do.

(`grep -n` is shown; on Windows PowerShell use `Select-String -Pattern` or run the greps in Git Bash. All paths are repo-relative.)

### P1 — Claude CLI bridge with per-call tracking and an abort surface

```bash
grep -n "_track_active\|_untrack_active\|_active_proc_lock" src/agents/claude_cli_bridge.py
grep -n "def abort\|def call_streaming\|def call_blocking\|_all_processes" src/agents/claude_cli_bridge.py
grep -n "class CliSubprocess" src/agents/claude_cli_subprocess.py
```

Expected: `ClaudeCliBridge` tracks the active subprocess per request id under `_active_proc_lock` (`_track_active`/`_untrack_active`), exposes `abort(request_id)`, `call_streaming`, `call_blocking`, and an `_all_processes` atexit registry; `CliSubprocess` exists with `start`/`kill`/`wait`. Absent → STOP: the stop feature has nothing to abort through.

### P2 — ChatEngine with busy signal, streaming, and a recorded current client

```bash
grep -n "_current_client\|def is_busy\|busy_changed\|token_streamed\|_degraded_streak" src/services/chat_engine.py
```

Expected: `busy_changed`/`token_streamed` signals, an `is_busy` property, `_launch_worker` recording `self._current_client = client`, and the `_degraded_streak` recycle counter. Absent → STOP.

### P3 — AgentChatController with the busy-queue and session surfaces

```bash
grep -n "_pending_triggers\|def enqueue_trigger\|def _persist_message\|def load_session\|def new_session\|def _setup_engine" src/services/agent_chat.py
```

Expected: the `_pending_triggers` list + `enqueue_trigger` busy-queue (M0), message persistence to `chat_messages`, `load_session` (with the enablement decoupling guard), `new_session`, `_setup_engine`. Absent → STOP.

### P4 — ChatBridge (web chat) with tool-poll timer and safe emit helpers

```bash
grep -n "def _on_busy\|_tool_timer\|def _poll_tools\|def _safe_emit_str\|def push_notice\|session_api" src/ui/web/chat_bridge.py
grep -n "class AgentPage" src/ui/web/agent_page.py
grep -n "confirm_fn=self._agent_controller.execute_write" src/ui/main_window.py
```

Expected: the bridge already re-emits engine signals, polls `tool_poll` on a `_tool_timer` from `_on_busy`, has `_safe_emit_str` and `push_notice`, accepts `session_api`/`confirm_fn`/`cancel_fn` kwargs; `AgentPage` forwards ctor kwargs into `ChatBridge`; `MainWindow._create_agent_page` wires the controller in. Absent → STOP.

### P5 — EnablementPage web chat drawer surfaces

```bash
grep -n "def _web_chat_send\|def _get_web_chat_bridge\|def _dispatch_chat\|def _mirror_user_turn\|is_publish_inflight\|def _load_live\|def _reload_active_draft_canvas" src/ui/pages/enablement/page.py
```

Expected: the page-level drawer wiring from the enablement web pivot (M5.5): a cached `ChatBridge` factory, `_web_chat_send` with the publish-confirm refusal, `_dispatch_chat`, `_mirror_user_turn`, and the canvas refresh helpers. Absent → STOP.

### P6 — html_markdown with the GFM post-passes and both converter paths

```bash
grep -n "_GFM_TASK\|_GFM_STRIKE\|def markdown_to_html\|def html_to_markdown\|MarkdownDialectGitHub\|class _MarkdownParser" src/data/html_markdown.py
```

Expected: `markdown_to_html` using the base `markdown` package with `["tables", "fenced_code", "sane_lists", "md_in_html"]` plus `_GFM_STRIKE`/`_GFM_TASK` regex post-passes, and `html_to_markdown` with the Qt `toMarkdown` path + `_MarkdownParser` stdlib fallback. Absent → STOP. (If `_GFM_TASK` currently emits `<input type="checkbox">`, that is exactly the pre-change state — good.)

### P7 — Migrations 054/055 + Asana board management

```bash
ls migrations/054_* migrations/055_*
grep -n "def link_task_board\|def task_board_ids\|board_source_id" src/data/enablement_tasks.py
grep -n "def get_asana_config" src/data/asana_setup.py
grep -n "task_board_links" src/data/asana_monitor.py src/data/enablement_tasks.py
```

Expected: migration 054 (`board_source_id` provenance column) and 055 (`task_board_links` many-to-many attribution) applied-able, `link_task_board`/`task_board_ids` present, monitor reconcile linking boards. Absent → STOP: subtask promotion attributes rows through `task_board_links` and will not be re-based on anything else.

### P8 — AsanaClient pagination internals

```bash
grep -n "_PAGE_SIZE\|def _paginate\|def _get_raw\|def list_subtasks" src/data/asana_client.py
grep -n "def is_status_inflight" src/data/asana_writeback.py
```

Expected: `_PAGE_SIZE = 100`, `_paginate` walking `next_page.offset` (with the stale-offset partial-return behavior), `_get_raw`, and an existing `list_subtasks` returning gid/name/completed; `asana_writeback.is_status_inflight` exists. Absent → STOP: the fail-closed vanish-dismissal gate is built on `_paginate`'s truncation semantics.

### P9 — Migration numbering headroom

```bash
ls migrations/ | sort | tail -5
```

Expected: the highest migration is ≤ 055 and **no file named `056_*` exists**. If a different `056_*.sql` already exists on the target, the trees have diverged — STOP and report; do not renumber this port's migration yourself.

### P10 — Zendesk mirror + web controller with the review/copy machinery

```bash
grep -n "_COPY_FIELDS\|def _resolve_copy\|def _review_covers\|def _copy_bundle\|def _record_review\|def _confirm_copy_release\|def _html_payloads\|def _article_body_text" src/services/zendesk_web.py
grep -n "def draft_content_hash\|def get_article_draft\|def save_article_draft" src/data/zendesk_store.py
ls migrations/051_*
grep -n "def copyField" src/ui/web/zendesk_bridge.py
```

Expected: the full copy-exact machinery (per-target field allowlists, review-record hash gate, byte-showing native confirm, `_copy_bundle` as single source of truth), the mig-051 mirror store, and the bridge relay. Absent → STOP: this workstream only *removes a status check* and *adds a field* to that machinery; without it there is nothing to modify.

### P11 — The web SPA with the surfaces this port touches

```bash
ls web/src/chat/ChatApp.jsx web/src/chat/ChatDrawer.jsx web/src/calendar/CalendarApp.jsx \
   web/src/zendesk/RevisionCenter.jsx web/src/zendesk/shape.js web/src/zendesk/CopyControls.jsx \
   web/src/zendesk/ArticleEditor.jsx web/src/lib/markdown.jsx
grep -n "COPY_DRAFTED_FIELD" web/src/zendesk/shape.js
grep -n "chatNotice\|tokenStreamed" web/src/chat/ChatDrawer.jsx
```

Expected: all files exist; `shape.js` already exports `COPY_DRAFTED_FIELD = "body_text"` (it predates this port — the SPA always *asked* for `body_text`; Python never served it, which is the dead-button defect this fixes); the drawer already handles `chatNotice`/`tokenStreamed`. Absent → STOP.

### P12 — Task tooling + greeting surfaces the Asana workstream extends

```bash
grep -n "def handle_update_task\|def _list_tasks_impl" src/data/chat_tools/enablement_tools.py
grep -n "def _bullet" src/data/startup_greeting.py
grep -n '"name": "list_tasks"' src/llm/claude_tools.py src/mcp/chat_mcp_server.py
grep -n "def set_tasks" src/services/enablement_web.py src/ui/pages/enablement/calendar.py
```

Absent → STOP.

### P13 — Test scaffolding the new tests assume

```bash
grep -n "def create_session" src/services/chat_session.py
grep -n "class FakeAsana" tests/test_asana_readback.py
grep -n "empty_db" tests/conftest.py | head -3
grep -n "_gated\|_seed_mirror\|_ready_draft\|_serve_revisions\|_clipboard\|_confirm_content" tests/test_zendesk_bridge.py | head -10
```

Expected: `chat_session.create_session`, the `empty_db` fixture, `FakeAsana` (with `tasks=`/`subtasks=` ctor kwargs), and the zendesk test helpers (`_gated`, `_seed_mirror`, `_ready_draft`, `_serve_revisions`, `_clipboard`, `_confirm_content`, `_confirm_visible`, `_review`, `_review_article`, `_review_macro`, `_source_added`, `REPO`). Absent → STOP.

---

## 2. Global guardrails (verbatim rules — none are negotiable)

1. **Zendesk stays GET-only.** No code in this port touches the Zendesk API at all, and `tests/test_zendesk_readonly_guard.py` must pass **unmodified** after every zendesk-workstream edit. If it fails, you added something write-shaped — remove it; never relax the guard.
2. **QWebChannel bridges stay pure relays.** `chat_bridge.py` / `zendesk_bridge.py` carry no authority: reads return viewmodels, side-effectful slots delegate to injected Python-held functions. The new `stopRun`/`queueMessage` slots follow this exactly (injected `stop_fn`/`queue_fn`, never imported); keep it that way. `notify_queued_dispatched` and `push_notice` are deliberately plain methods, NOT `@Slot` — a page script must not be able to forge them.
3. **`src/services` never imports `src/ui`.** Everything the services layer needs from the UI is injected (the confirm host, the clipboard fn, `stop_fn`/`queue_fn`).
4. **DELETION FAILS CLOSED / DISPLAY FAILS OPEN for Asana.** Nothing in the monitor ever DELETEs a row — dismissal is a status change. A listing that is not demonstrably complete must not dismiss anything (that is the `_PAGE_SIZE` gate in `_pull_subtasks`). Display-side link lookups that fail show everything rather than hiding work.
5. **No pymdown-extensions.** The format-parity work is regex post-passes precisely because the dependency set is frozen. Do not add `pymdownx` anything; `tests/test_html_markdown.py::test_no_pymdownx_dependency` pins this.
6. **`enablement_tasks._UPDATABLE` is a whitelist that silently drops unlisted fields.** `update_task(**fields)` filters on it — that is the SQL-injection guard and also why `parent_task_ref` must be IN the set (the monitor writes it through `update_task`) while the chat tool lane strips it BEFORE calling (`handle_update_task`). Both halves are load-bearing.
7. **Parent every QWebChannel-registered QObject.** `registerObject` does NOT take ownership; a parentless bridge is GC'd and the channel then dereferences freed memory (native crash). Every `ChatBridge(...)` construction in this port passes `parent=` — keep that in any adaptation.
8. **After ANY change under `web/src/`, run `npm --prefix web run build`.** The app loads `src/ui/web/dist/index.html` from disk; until you rebuild, the Python-side changes talk to an old bundle. **NEVER hand-edit `dist/index.html` and NEVER copy this tree's dist over** — regenerate on the target.
9. **pytest in groups of ≤ 4 files, never the full `tests/` directory** (it hangs on Windows and stacks WebEngine teardowns elsewhere). The `test_*_local.py` WebEngine round-trip suites run **singly** — one pytest invocation per file.
10. **Kill zombies before any E2E run:** `wmic process where "commandline like '%alma_mcp_server%'" call terminate` (Windows) / `pkill -f alma_mcp_server` (POSIX).

---

## 3. Workstream 1 — Chat stop + queue-context

**Feature contract.** While a turn runs, the composer's Send button becomes Stop. Stop aborts the in-flight `claude` CLI subprocess tree; the engine converts the resulting worker error into a `run_stopped` signal (not `error_occurred`, no degraded-streak bump), history gains a `RUN_STOPPED_MARKER` assistant row (also persisted to `chat_messages` on the Agent surface — single writer), the web UI keeps any partially-streamed text ("… — stopped") or shows a "Run stopped." system line, and a post-stop tool-row catch-up + canvas resync runs. The composer input stays ENABLED while busy: a send during a turn queues (`'queued'` return → badge on that exact bubble) and drains one-per-turn on idle; a send while idle dispatches immediately (`'sent'`). A successful Stop still drains a queued message — stop-then-send-corrected-context is the intended workflow.

**Review fixes baked in** (why the code has parts a naive implementation would lack):

- **FIX 1 (verified major — wrong-session contamination):** `load_session` / `new_session` CLEAR `_pending_triggers`. A queued message referred to the old conversation; draining it into a freshly-loaded thread was reproduced. The drop is intentional; there is no re-queue affordance this round. A REFUSED load (the enablement decoupling guard) switches nothing and therefore drops nothing.
- **FIX 2a (verified major — MCP child survives the kill):** `kill_process_tree()` is THE kill choke point. On Windows a bare `proc.kill()` leaves the `python -m src.mcp.chat_mcp_server` child the CLI spawned alive — it kept committing tool rows after Stop. `taskkill /PID <pid> /T /F` walks the tree; any taskkill failure falls back to the plain kill so a stop is never weaker than before. All four kill sites route through it (bridge `_kill_proc`, `CliSubprocess.kill`, the atexit sweep — and abort goes through `_kill_proc`).
- **FIX 2b (late tool row swallowed):** one delayed (~1200 ms) catch-up poll after `busy(False)`, so a tool row committing after the final polls (cross-process WAL latency, or a row the Stop's kill raced) isn't permanently swallowed by the next `busy(True)` cursor re-baseline. The catch-up self-cancels if a new turn already started (accepted residual window).
- **FIX 2c (transcript divergence + dead canvas):** the stop path never runs the telemetry persistence callback, so the Agent controller persists the marker row on `run_stopped` (`_on_run_stopped`); the enablement page writes "Run stopped." to the native panel and reruns the same refresh the response path does (a tool that completed before the kill may have revised content the canvas isn't showing).
- **FIX 3 (honest stop):** `abort(request_id)` → bool, `abort_active()` → bool, `ChatEngine.stop()` returns False AND clears `_stop_requested` when the abort raised or matched nothing — so a later worker error surfaces as a REAL error (degraded streak included), never a fake user stop, and the Stop button re-arms.
- **FIX 4 (honest un-badging):** the drain announces the EXACT dispatched text (`queued_dispatched` signal → bridge `queuedDispatched`); JS un-badges by text match. Never inferred from `busyChanged(true)` — the Python queue also carries `[SYSTEM]` triggers and other-surface sends that have no bubble on this surface.
- **FIX 5 (drawer message lost under the publish modal):** the page-level drain PEEKs then pops — `_web_chat_send` returns False while the publish-confirm modal is open, the message stays at the head, and one ~1s single-flight retry is armed.
- **Deferred drain (both drains):** the drain is dispatched via `QTimer.singleShot(0, …)`, never synchronously inside the `busy_changed(False)` delivery — a synchronous send makes the new turn's `busy_changed(True)` reach later slots BEFORE the False they are still processing, leaving the web UI showing not-busy during a live run with its poll timer stopped.

### 3.1 `src/agents/claude_cli_subprocess.py` — the kill choke point

**Intent:** add the module-level `kill_process_tree()` helper and route `CliSubprocess.kill()` through it.

(a) **Immediately after the module's `logger = logging.getLogger("alma.claude_cli_subprocess")` line and before `class CliSubprocess`**, insert this new function (the module already imports `subprocess`, `sys`, `logging` — no new imports):

```python
def kill_process_tree(proc: subprocess.Popen | None) -> None:
    """Force-kill ``proc`` AND every child it spawned. Idempotent; never raises.

    On Windows a bare ``proc.kill()`` is ``TerminateProcess`` on the one PID:
    the ``claude`` CLI dies but the ``python -m src.mcp.chat_mcp_server`` child
    it spawned survives — a zombie holding the warehouse DB open and still able
    to commit tool rows after the operator pressed Stop. ``taskkill /T /F``
    walks the child tree. Any taskkill failure (missing binary, timeout,
    nonzero exit) falls back to the plain kill, so a stop is never weaker than
    it was before.

    POSIX path unchanged (single kill). Mac follow-up for the port: spawn the
    CLI with ``start_new_session=True`` and kill the process group
    (``os.killpg``) to get the same tree semantics.

    This is the ONE choke point for killing a CLI call: the bridge's
    abort/shutdown (``_kill_proc``), the timeout/early_stop paths
    (``CliSubprocess.kill``) and the atexit sweep all route through it.
    """
    if proc is None:
        return
    try:
        if proc.poll() is not None:
            return
        if sys.platform == "win32":
            rc = 1
            try:
                rc = subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True, timeout=10,
                ).returncode
            except Exception as e:  # noqa: BLE001 — fall through to plain kill
                logger.debug("taskkill /T failed for pid %s: %s", proc.pid, e)
                rc = 1
            if rc != 0:
                proc.kill()
        else:
            proc.kill()
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001 — a kill must never raise into the caller
        pass
```

(b) **In `class CliSubprocess`, replace the entire `kill` method** (previously an inline poll/kill/wait try-block) with:

```python
    def kill(self) -> None:
        """Force-kill the subprocess AND its children (the MCP server the CLI
        spawned) — see ``kill_process_tree``. Idempotent."""
        kill_process_tree(self.proc)
```

### 3.2 `src/agents/claude_cli_bridge.py` — abort reports the kill; every kill site tree-kills

**Intent:** import the helper, route the atexit sweep and `_kill_proc` through it, and make `abort` return whether it matched + killed (the honesty contract `ChatEngine.stop()` relies on).

(a) **Change the import line** near the top of the module (it previously imported only `CliSubprocess`):

```python
from src.agents.claude_cli_subprocess import CliSubprocess, kill_process_tree
```

(b) **In `class ClaudeCliBridge`, replace the classmethod `_cleanup_all`** with:

```python
    @classmethod
    def _cleanup_all(cls) -> None:
        for proc in cls._all_processes:
            # Tree-kill, same as every other kill site: at exit the MCP-server
            # child must die with the CLI or it outlives the app as a zombie.
            kill_process_tree(proc)
        cls._all_processes.clear()
```

(c) **Replace the method `abort`** (previously `-> None` with no return values) with:

```python
    def abort(self, request_id: str) -> bool:
        """Kill the active subprocess if it matches ``request_id``.

        Returns True only when a tracked in-flight call matched and the kill
        was issued — the honesty contract ``ChatEngine.stop()`` relies on. A
        non-matching id (the call already finished and untracked itself, or a
        stale abort) returns False: nothing was aborted, so nothing may be
        reported as stopped."""
        with self._active_proc_lock:
            if self._active_request_id == request_id and self._active_proc:
                self._kill_proc(self._active_proc)
                return True
        return False
```

(d) **Replace the method `_kill_proc`** with:

```python
    def _kill_proc(self, proc: subprocess.Popen) -> None:
        # The abort/shutdown choke point. Routes through kill_process_tree —
        # on Windows a bare proc.kill() leaves the MCP-server child the CLI
        # spawned alive (it kept committing tool rows after a Stop). The
        # timeout/early_stop paths kill via CliSubprocess.kill(), which routes
        # through the same helper.
        kill_process_tree(proc)
```

**Wiring note:** `abort` depends on the pre-existing `_track_active`/`_untrack_active` bookkeeping (prerequisite P1) — do not touch those. For reference, this is what they look like on the source tree (unchanged by this port):

```python
    def _track_active(self, proc, request_id: str) -> None:
        with self._active_proc_lock:
            self._active_proc = proc
            self._active_request_id = request_id

    def _untrack_active(self, proc) -> None:
        with self._active_proc_lock:
            if self._active_proc is proc:
                self._active_proc = None
                self._active_request_id = None
```

### 3.3 `src/llm/claude_cli_client.py` — record the in-flight request id; `abort_active()`

**Intent:** every generate path records its `request_id` just before the bridge call and clears it in a `finally`; a new `abort_active()` forwards the recorded id to `ClaudeCliBridge.abort` and reports honestly.

(a) **In `__init__`, add the tracking attribute** — the full post-change ctor:

```python
    def __init__(self, model: str = "sonnet", pii_redaction: bool = True) -> None:
        self.model = model
        self.pii_redaction = pii_redaction
        self._bridge = None  # lazily initialized in _ensure_bridge
        self._call_counter = 0
        self._mcp_config: list[dict] = []
        self._active_request_id: str | None = None
```

(b) **Replace the whole `generate` method** (the change is the `self._active_request_id = request_id` / `try/finally` scoping around BOTH the blocking and the streaming call — everything else is as before):

```python
    def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        timeout: int = 120,
        max_tokens: int | None = None,  # accepted for API parity, not used
        on_token=None,
    ) -> str:
        """Synchronous .generate() — returns full response text.

        Applies PII redaction (base always; aggressive if pii_redaction=True)
        then delegates to the CLI bridge. The (redacted) system prompt travels
        separately as the subprocess's REAL system prompt — never embedded in
        user content (see _prepare_prompt). When ``on_token`` is given, streams
        via the bridge and forwards each text delta in real time (the call still
        returns the full accumulated text, so callers are unchanged otherwise).
        """
        full_prompt, sys_prompt = self._prepare_prompt(prompt, system_prompt)
        bridge = self._ensure_bridge()
        bridge.set_system_prompt(sys_prompt)
        self._call_counter += 1
        if on_token is None:
            request_id = f"cli_client_{self._call_counter}_{int(time.time())}"
            self._active_request_id = request_id
            try:
                return bridge.call_blocking(full_prompt, request_id, timeout=timeout)
            finally:
                self._active_request_id = None

        request_id = f"cli_client_stream_{self._call_counter}_{int(time.time())}"

        def _on_token(event):
            if event.type == "content":
                delta = event.data.get("delta", "")
                if delta:
                    try:
                        on_token(delta)
                    except Exception:  # noqa: BLE001 — streaming is best-effort, never fatal
                        pass

        self._active_request_id = request_id
        try:
            result = bridge.call_streaming(
                full_prompt, request_id, on_token=_on_token, timeout=timeout,
            )
        finally:
            self._active_request_id = None
        if result.get("error"):
            raise RuntimeError(
                f"Claude CLI streaming call failed: {result['error']} - "
                f"{result.get('message', '')}"
            )
        return result.get("full_text", "")
```

(c) **Replace the whole `generate_streaming` method** (same scoping around its bridge call):

```python
    def generate_streaming(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int | None = None,
        timeout: int = 120,
    ) -> Iterator[str]:
        """Yield text chunks as the model produces them.

        Uses the bridge's on_token callback to collect deltas; this iterator
        finishes synchronously after the subprocess exits — it does not stream
        in real time across the iterator boundary. Sufficient for callers that
        only need iterator-shaped semantics; real interleaved streaming would
        require running the call in a thread + queue.
        """
        full_prompt, sys_prompt = self._prepare_prompt(prompt, system_prompt)
        bridge = self._ensure_bridge()
        bridge.set_system_prompt(sys_prompt)
        self._call_counter += 1
        request_id = f"cli_client_stream_{self._call_counter}_{int(time.time())}"

        deltas: list[str] = []

        def _on_token(event):
            if event.type == "content":
                txt = event.data.get("delta", "")
                if txt:
                    deltas.append(txt)

        self._active_request_id = request_id
        try:
            result = bridge.call_streaming(
                full_prompt, request_id, on_token=_on_token, timeout=timeout,
            )
        finally:
            self._active_request_id = None
        if result.get("error"):
            raise RuntimeError(
                f"Claude CLI streaming call failed: {result['error']} - "
                f"{result.get('message', '')}"
            )
        for delta in deltas:
            yield delta
```

(d) **Immediately after `set_usage_sink` and before `shutdown`, insert the new method:**

```python
    def abort_active(self) -> bool:
        """Kill the in-flight CLI call, if any (safe from the main thread).

        The generate paths record the request_id just before each bridge call
        and clear it in a ``finally``; ``ClaudeCliBridge.abort`` is thread-safe
        (``_active_proc_lock``) and only kills the subprocess whose id still
        matches, so a late abort against a finished call is a no-op. The killed
        call surfaces to the caller as the normal error path.

        Returns True only when the bridge actually matched + killed the
        subprocess; False when there is no bridge, no recorded request, the
        bridge no longer tracks the id (the call already finished), or the
        abort raised — so ``ChatEngine.stop()`` never reports a stop it
        cannot prove.
        """
        bridge = self._bridge
        request_id = self._active_request_id
        if bridge is None or not request_id:
            return False
        try:
            return bool(bridge.abort(request_id))
        except Exception as e:  # noqa: BLE001 — an abort must never crash the caller
            logger.debug("ClaudeCliClient abort_active failed: %s", e)
            return False
```

### 3.4 `src/services/chat_engine.py` — `stop()`, `run_stopped`, the marker

**Intent:** the shared engine gains a `stop()` that delegates to the active client's `abort_active`, a `run_stopped` signal, and a `_stop_requested` flag consumed by whichever worker signal lands first. A stopped turn appends `RUN_STOPPED_MARKER` to in-memory history and never feeds the degraded streak.

(a) **At module level, immediately after the `_DEFAULT_RECYCLE_THRESHOLD = 3` line** (and before the `TOOL_PROMPT_ADDENDUM` import), insert:

```python
#: The transcript marker for a user-aborted turn. ``_on_worker_error`` appends
#: it to the in-memory history; consumers that persist transcripts (the Agent
#: controller) write the SAME text on ``run_stopped`` so a reloaded session
#: replays coherently.
RUN_STOPPED_MARKER = "[Response stopped by the user before completion.]"
```

(b) **In `class ChatEngine`'s signal declarations, add `run_stopped` after `token_streamed`** — the post-change block reads:

```python
    response_ready = Signal(str)
    error_occurred = Signal(str)
    busy_changed = Signal(bool)
    status_update = Signal(str)
    token_streamed = Signal(str)   # per-token text delta while a turn streams
    run_stopped = Signal()         # the user aborted the in-flight turn
    # Adaptive bridge-recycle (F-9, bug-bash 2026-04-23):
    # fires when N consecutive responses look degraded. Consumers that
    # own a warm client should shut it down and set_client() a fresh one.
    bridge_recycle_requested = Signal()
```

(c) **In `ChatEngine.__init__`, immediately after the adaptive bridge-recycle state (`self._degraded_streak = 0`) and before the unexecuted-tool-call guard**, insert:

```python
        # Stop control: set by stop(), consumed by whichever worker signal
        # lands first (the killed subprocess surfaces as the error path; a
        # run that completed before the kill landed clears it as a normal
        # completion).
        self._stop_requested = False
```

(d) **Immediately after the `send()` method** (which ends with `self._launch_worker(client, prompt, system)`) **and before `_capture_turn_evidence`, insert the new `stop()` method:**

```python
    def stop(self) -> bool:
        """Abort the in-flight turn. Returns True only when an abort was sent.

        Degrades gracefully: not busy, a client with no ``abort_active`` (the
        Gemini ReportBridgeClient has none), an ``abort_active`` that raises,
        or one that reports ``False`` (it matched nothing — nothing was
        killed) → False, ``_stop_requested`` cleared, and the run continues
        untouched. The False is load-bearing honesty: the Stop button re-arms,
        and if the worker later errors on its own that surfaces as a REAL
        error (``error_occurred`` + degraded streak), never a fake user stop.
        On success the abort kills the CLI subprocess tree; the worker then
        errors, and ``_on_worker_error`` converts that into ``run_stopped``
        instead of ``error_occurred``. Call from the main thread — never a
        QThread kill.
        """
        if not self.is_busy:
            return False
        client = getattr(self, "_current_client", None)
        if client is None or not hasattr(client, "abort_active"):
            return False
        self._stop_requested = True
        try:
            aborted = client.abort_active()
        except Exception as e:  # noqa: BLE001 — a failed abort is a refused stop
            logger.debug("stop(): abort_active failed: %s", e)
            self._stop_requested = False
            return False
        if aborted is False:
            # The client looked and found nothing in flight to kill (it raced
            # a completion, or the bridge lost the request id). ``None`` — a
            # legacy abort_active with no return contract — keeps the old
            # success path.
            self._stop_requested = False
            return False
        return True
```

(e) **At the very top of `_on_worker_finished`, before the telemetry normalization**, insert the raced-completion clear — the post-change opening of the method:

```python
    def _on_worker_finished(self, raw_response: str, telemetry: dict = None):
        # A stop that raced a normal completion: the run finished before the
        # kill landed, so treat it as an ordinary turn.
        self._stop_requested = False

        # Normalized ONCE, up front: two `{**telemetry, ...}` splats downstream
        # would raise TypeError on a non-mapping, and one of them now runs on
        # every turn rather than only on a fabricated one.
        telemetry = dict(telemetry) if isinstance(telemetry, dict) else {}
```

(f) **At the very top of `_on_worker_error`, insert the stop conversion** — the post-change method through the degraded-streak line:

```python
    def _on_worker_error(self, error_text: str):
        if self._stop_requested:
            # The user killed the run: not an error, not a degraded bridge.
            # The marker keeps the full-history replay coherent next turn.
            self._stop_requested = False
            self._history.append({
                "role": "assistant",
                "content": RUN_STOPPED_MARKER,
            })
            self.busy_changed.emit(False)
            self.status_update.emit("")
            self.run_stopped.emit()
            return
        self.busy_changed.emit(False)
        self.status_update.emit("")
        self.error_occurred.emit(error_text)
        # Worker-level errors (bridge crashed, timeout, etc.) count as
        # degraded — they're the strongest signal that the warm bridge
        # needs to be recycled.
        self._degraded_streak += 1
```

**Wiring note:** `stop()` reads `self._current_client`, which `_launch_worker` already records (P2). The Gemini `ReportBridgeClient` has no `abort_active` — `stop()` returns False for it by design (the run continues; the button re-arms).

### 3.5 `src/services/agent_chat.py` — controller: queue, stop, drain, session-switch clearing, marker persistence

**Intent:** the Agent controller exposes `queue_user_message` / `stop_run` for the bridge, announces drains on a `queued_dispatched` signal, defers the drain, persists the stop marker, and drops the queue on session switches.

(a) **In the signal declarations of `class AgentChatController`, immediately after `guruTargetsListed = Signal(str)`**, add:

```python
    # Busy-queue drain announcement (stop/queue review wave, FIX 4). Emitted
    # with the EXACT text the drain just dispatched, so the web surfaces
    # un-badge precisely the bubble that ran. The bridge re-emits it as
    # ``queuedDispatched``. Never inferred from busy alone: the queue also
    # carries [SYSTEM] triggers and other-surface sends with no JS bubble.
    queued_dispatched = Signal(str)
```

(b) **Immediately after the `send()` method and before `_persist_turn`, insert:**

```python
    def _on_run_stopped(self) -> None:
        """Persist the stop marker as an assistant row (FIX 2c).

        Mirrors what ``_on_worker_error`` already stamped into the engine's
        in-memory history, so a reloaded transcript replays coherently.
        One row per stopped turn by construction: ``run_stopped`` fires
        exactly once per stop (the engine consumes ``_stop_requested`` before
        emitting) and the telemetry-callback persistence path
        (``_persist_turn``) runs only on ``_on_worker_finished`` — never on
        the stop path — so this is the marker row's ONLY writer."""
        from src.services.chat_engine import RUN_STOPPED_MARKER
        self._persist_message("assistant", RUN_STOPPED_MARKER)
```

(c) **Immediately after `enqueue_trigger` (which is unchanged), insert the two new public methods:**

```python
    def queue_user_message(self, text: str) -> str:
        """The composer's send while Renn may be busy: dispatch now when idle
        ('sent'), else park it on the busy-queue to run as its own turn when
        the engine frees up ('queued'). Same queue as ``enqueue_trigger``, so
        drain order interleaves fairly with system follow-ups."""
        if self._engine is None:
            return "error"
        if self._engine.is_busy:
            self._pending_triggers.append(text or "")
            return "queued"
        self.send(text or "")
        return "sent"

    def stop_run(self) -> bool:
        """Abort Renn's in-flight turn (the Stop control). Delegates to the
        engine; False when idle or when the active client can't abort."""
        if self._engine is None or not hasattr(self._engine, "stop"):
            return False
        try:
            return bool(self._engine.stop())
        except Exception:  # noqa: BLE001 — a failed stop must never crash the chat
            return False
```

(d) **Replace `_on_busy_changed` and add `_drain_pending_trigger` right after it** (previously `_on_busy_changed` popped and sent synchronously):

```python
    def _on_busy_changed(self, busy: bool) -> None:
        """Drain ONE queued trigger when the engine goes idle. One-at-a-time so
        each follow-up runs as its own turn (and re-queues correctly if another
        arrives mid-turn).

        The drain is DEFERRED (queued dispatch), never run inside this
        ``busy_changed(False)`` delivery: a synchronous ``send`` here makes the
        new turn's ``busy_changed(True)`` reach later slots BEFORE the False
        they are still processing, leaving the web UI showing not-busy during a
        live run with its poll timer stopped."""
        if busy or not self._pending_triggers:
            return
        from PySide6.QtCore import QCoreApplication, QTimer
        if QCoreApplication.instance() is not None:
            QTimer.singleShot(0, self._drain_pending_trigger)
        else:
            self._drain_pending_trigger()

    def _drain_pending_trigger(self) -> None:
        """Run the head of the busy-queue if the engine is still idle. If a
        turn started between the schedule and now, leave the queue intact and
        wait for the next ``busy_changed(False)``. After dispatch the text is
        announced on ``queued_dispatched`` so the web UI un-badges exactly the
        bubble that ran (FIX 4)."""
        if not self._pending_triggers or self._engine is None:
            return
        if self._engine.is_busy:
            return
        text = self._pending_triggers.pop(0)
        self.send(text)
        self.queued_dispatched.emit(text)
```

(e) **In `load_session`, after `self._session_id = session_id` and before `self._write_session_pointer(session_id)`**, insert the FIX-1 clear. The full post-change tail of the method (from the engine-history restore) for anchor certainty:

```python
        if self._engine is not None:
            try:
                self._engine.set_history([dict(m) for m in msgs])
                self._engine.set_session_id(session_id)
            except Exception:  # noqa: BLE001
                pass
        self._session_id = session_id
        # FIX 1 (wrong-session drain): a session switch DROPS anything still
        # parked on the busy-queue. A queued message referred to the OLD
        # conversation's context; draining it here would replay it into the
        # newly-loaded thread (verified cross-session contamination). The loss
        # is intentional — there is no re-queue affordance this round. Note
        # this line is only reached AFTER the enablement decoupling guard: a
        # refused load switches nothing, so it drops nothing.
        self._pending_triggers.clear()
        self._write_session_pointer(session_id)
        return {"session_id": session_id, "messages": msgs}
```

(f) **In `new_session`, after `self._session_id = None`**, insert the same clear — full post-change method:

```python
    def new_session(self) -> None:
        """Start a fresh thread: clear engine history and drop the active session
        so the next send creates a new one; clear the tools→session pointer."""
        self._session_id = None
        # FIX 1: same rule as load_session — queued texts referred to the old
        # thread and must not seed the new one. Intentional drop.
        self._pending_triggers.clear()
        if self._engine is not None:
            try:
                self._engine.clear_history()
                self._engine.set_session_id(None)
            except Exception:  # noqa: BLE001
                pass
        self._write_session_pointer("")
```

(g) **In `_setup_engine`, between the `busy_changed` connect and the `set_telemetry_callback` call**, insert the `run_stopped` wiring — full post-change method:

```python
    def _setup_engine(self):
        try:
            from src.services.chat_engine import ChatEngine
            from src.ui.pages.enablement.page import RENN_SYSTEM_PROMPT
            self._engine = ChatEngine(
                system_prompt=RENN_SYSTEM_PROMPT,
                context_provider=self._agent_context,
                task_type="enablement_chat",
                tools_enabled=True,
                use_mcp_tools=True,
                db_path=self._db_path(),
                stream=True,   # the Agent surface streams tokens (M-streaming)
            )
            self._engine.bridge_recycle_requested.connect(self._on_recycle)
            # M0 busy-queue: when a turn finishes, drain one queued trigger (a
            # picker-resolve follow-up that arrived while Renn was busy).
            self._engine.busy_changed.connect(self._on_busy_changed)
            # FIX 2c (transcript divergence): the stop path never runs the
            # telemetry callback, so the persisted transcript would silently
            # diverge from the engine's in-memory history (which already
            # carries the stop marker). hasattr-guarded for engine fakes.
            if hasattr(self._engine, "run_stopped"):
                self._engine.run_stopped.connect(self._on_run_stopped)
            # Persist the assistant turn (content + telemetry) to chat_messages so
            # past chats actually have a transcript + a title. The bridge CHAINS
            # this callback (it adds the live meter on top), so both survive.
            self._engine.set_telemetry_callback(self._persist_turn)
        except Exception as exc:  # noqa: BLE001 — chat degrades, the page still loads
            logger.warning("Agent chat engine unavailable: %s", exc)
            self._engine = None
```

### 3.6 `src/ui/web/chat_bridge.py` — the relay: stopRun / queueMessage slots, runStopped / queuedDispatched signals, late catch-up

**Intent:** pure-relay additions. Two new outbound signals; two new injected fns (`stop_fn`, `queue_fn`); two new inbound slots; the FIX-4 relay from the controller's `queued_dispatched` signal; the FIX-2b delayed catch-up poll.

(a) **In the signal declarations, immediately after `chatNotice`**, add (shown with `chatNotice` for the anchor):

```python
    chatNotice = Signal(str)       # JSON {role, text} — host-pushed notices (scan
                                   # results, publish outcomes) for embedded drawers
                                   # (M5.5); NOT an engine turn, NOT JS-invokable
    runStopped = Signal()          # the in-flight turn was aborted by the user
    queuedDispatched = Signal(str)  # a previously QUEUED message was just
                                    # dispatched — payload is its exact text, so
                                    # JS un-badges the one bubble that ran (the
                                    # Python queue also carries [SYSTEM] triggers
                                    # and other-surface sends with no bubble, so
                                    # busyChanged(True) is NOT proof of dispatch)
```

(b) **Extend the ctor signature with `stop_fn=None, queue_fn=None`** (before `parent=None`) — the post-change signature:

```python
    def __init__(self, engine, send_fn=None, tool_poll=None, session_api=None,
                 job_poll=None, draft_api=None, voice=None, action_poll=None,
                 connect_fn=None, google_state_signal=None, list_fn=None,
                 resolve_fn=None, drive_folders_signal=None,
                 action_resolved_signal=None, asana_list_fn=None,
                 asana_resolve_fn=None, asana_projects_signal=None,
                 guru_list_fn=None, guru_resolve_fn=None,
                 guru_targets_signal=None, confirm_fn=None, cancel_fn=None,
                 stop_fn=None, queue_fn=None, parent=None):
```

(c) **Immediately after `self._session_api = session_api` in the ctor**, insert the queued-dispatch relay:

```python
        # ── queued-dispatch relay (FIX 4) ──
        # The Agent controller (injected here as session_api on that surface)
        # announces each busy-queue drain on ``queued_dispatched(text)``; we
        # re-emit it as ``queuedDispatched`` so React clears the queued badge
        # on exactly the bubble that ran. Duck-typed + best-effort: hosts
        # without the signal (page-level drains) call
        # ``notify_queued_dispatched`` directly instead.
        _queued_sig = getattr(session_api, "queued_dispatched", None)
        if _queued_sig is not None:
            try:
                _queued_sig.connect(self.queuedDispatched)
            except Exception:  # noqa: BLE001 — best-effort wiring
                pass
```

(d) **Later in the ctor, immediately after `self._cancel_fn = cancel_fn` and before the engine signal re-emits**, insert the stop/queue storage; and in the re-emit block add the `run_stopped` connect after the `token_streamed` one. The full post-change run of the ctor from the confirm-channel comment to the end:

```python
        # ── GATED write confirm channel (M7b) ──
        # ``confirm_fn(request_id)`` (the controller's ``execute_write``) runs the
        # gated, non-idempotent live write on a real operator Confirm click;
        # ``cancel_fn(request_id)`` (``cancel_write``) resolves the row without any
        # write on Cancel. Both injected (not imported) so the bridge stays the only
        # JS<->Python boundary and tests can supply fakes. There is NO direct-execute
        # tool — these slots are the ONLY way a write runs, and only via a click.
        self._confirm_fn = confirm_fn
        self._cancel_fn = cancel_fn
        # ── stop + queue-while-busy ──
        # ``stop_fn() -> bool`` aborts the in-flight LLM turn and NOTHING else —
        # no WriteWorkers, no chat_action_requests, no drafts, no confirms.
        # ``queue_fn(text) -> 'sent'|'queued'`` dispatches now when idle, else
        # parks the text to run as its own turn on the next idle. Both injected
        # (not imported) so the bridge stays the only JS<->Python boundary and
        # tests can supply fakes.
        self._stop_fn = stop_fn
        self._queue_fn = queue_fn
        # Re-emit the engine's signals as the bridge's (signal-to-signal).
        engine.response_ready.connect(self.responseReady)
        engine.error_occurred.connect(self.errorOccurred)
        engine.busy_changed.connect(self.busyChanged)
        engine.busy_changed.connect(self._on_busy)
        engine.status_update.connect(self.statusUpdate)
        if hasattr(engine, "token_streamed"):
            engine.token_streamed.connect(self.tokenStreamed)
        if hasattr(engine, "run_stopped"):
            engine.run_stopped.connect(self.runStopped)
        if hasattr(engine, "set_telemetry_callback"):
            # Chain — don't clobber. A controller may already have registered a
            # telemetry callback (e.g. to persist the turn to chat_messages); we
            # call it first, then add the live meter on top.
            self._prior_telemetry = getattr(engine, "_telemetry_callback", None)
            engine.set_telemetry_callback(self._on_telemetry)
```

(e) **Immediately after `push_notice`, insert:**

```python
    def notify_queued_dispatched(self, text):
        """Tell the JS surfaces a previously queued message was just dispatched
        (the page-level drawer drain calls this directly; the Agent controller
        arrives via its ``queued_dispatched`` signal instead). Plain method,
        deliberately NOT a Slot — a page script must not be able to forge a
        dispatch event and silently un-badge a message that never ran."""
        self._safe_emit_str(self.queuedDispatched, str(text or ""))
```

(f) **In the inbound-slot section, immediately after the `send` slot, insert the two new slots:**

```python
    @Slot(result=bool)
    def stopRun(self):
        """Abort the in-flight LLM turn. True only when an abort was sent.

        Carries no authority beyond that abort: it never touches WriteWorkers,
        chat_action_requests, drafts, or confirms, so a forged invoke can at
        worst cancel a response the operator was reading."""
        if self._stop_fn is None:
            return False
        try:
            return bool(self._stop_fn())
        except Exception:  # noqa: BLE001 — never crash the chat
            return False

    @Slot(str, result=str)
    def queueMessage(self, text):
        """Send a user message, queueing it when a turn is in flight. Returns
        'sent' (dispatched now), 'queued' (parked; runs on the next idle), or
        'error'. Without an injected ``queue_fn`` this degrades to a plain
        send so the composer keeps working on older wirings."""
        try:
            if self._queue_fn is not None:
                return str(self._queue_fn(text or ""))
            self._send_fn(text or "")
            return "sent"
        except Exception:  # noqa: BLE001 — never crash the chat
            return "error"
```

(g) **Replace `_on_busy`** (the change is the trailing `QTimer.singleShot(1200, self._late_catchup)`) **and add `_late_catchup` right after it, before `_latest_tool_id`:**

```python
    def _on_busy(self, busy):
        if self._tool_poll is None and self._job_poll is None and self._draft_api is None:
            return
        if busy:
            # Baseline the tool cursor to the newest existing row so we only
            # surface THIS turn's tool calls; force a fresh job/draft emit. Then
            # poll all three while the turn runs.
            if self._tool_poll is not None:
                self._tool_cursor = self._latest_tool_id()
            self._jobs_last = None
            self._drafts_last = None
            self._tool_timer.start()
        else:
            self._poll_tools()        # final catch after the turn completes
            self._poll_jobs()
            self._poll_drafts()
            self._tool_timer.stop()
            # FIX 2b (late tool row swallowed): a row the MCP subprocess
            # commits in the same instant the turn ends can land AFTER the
            # final polls above (cross-process WAL latency, or a row a Stop's
            # kill raced), and the next busy(True) re-baselines _tool_cursor
            # past it forever. One delayed catch-up closes that window.
            QTimer.singleShot(1200, self._late_catchup)

    def _late_catchup(self):
        """The delayed final poll scheduled by ``_on_busy(False)``. Runs the
        same catch-up ONLY while the engine is still idle: if a new turn
        already started, its busy(True) re-baseline owns the cursor and this
        poll must not race it. That residual window (a late row swallowed by
        an immediately-following turn) is accepted — the subprocess tree-kill
        closes the main hazard (an MCP child outliving a Stop and committing
        rows nobody polls for)."""
        try:
            if getattr(self._engine, "is_busy", False):
                return
        except Exception:  # noqa: BLE001 — a dead engine ends the catch-up
            return
        self._poll_tools()
        self._poll_jobs()
        self._poll_drafts()
```

**Wiring note:** `QTimer` is already imported at this module's top (`from PySide6.QtCore import QObject, QTimer, Signal, Slot` or equivalent — verify with `grep -n "QTimer" src/ui/web/chat_bridge.py`; if absent, add it to the existing PySide6.QtCore import).

### 3.7 `src/ui/web/agent_page.py` — thread the kwargs

**Intent:** `AgentPage` accepts `stop_fn`/`queue_fn` and forwards them into the bridge. The full post-change class (the only changes are the two kwargs in the signature and the `stop_fn=stop_fn, queue_fn=queue_fn,` line in the `ChatBridge(...)` call):

```python
class AgentPage(QWidget):
    """A self-contained Agent chat surface. ``engine`` is the chat runtime
    (a real ``ChatEngine`` in production, a fake in tests)."""

    def __init__(self, engine, send_fn=None, tool_poll=None, session_api=None,
                 job_poll=None, draft_api=None, voice=None, action_poll=None,
                 connect_fn=None, google_state_signal=None, list_fn=None,
                 resolve_fn=None, drive_folders_signal=None,
                 action_resolved_signal=None, asana_list_fn=None,
                 asana_resolve_fn=None, asana_projects_signal=None,
                 guru_list_fn=None, guru_resolve_fn=None,
                 guru_targets_signal=None, confirm_fn=None, cancel_fn=None,
                 stop_fn=None, queue_fn=None, parent=None):
        super().__init__(parent)
        self.bridge = ChatBridge(engine, send_fn=send_fn, tool_poll=tool_poll,
                                 session_api=session_api, job_poll=job_poll,
                                 draft_api=draft_api, voice=voice,
                                 action_poll=action_poll, connect_fn=connect_fn,
                                 google_state_signal=google_state_signal,
                                 list_fn=list_fn, resolve_fn=resolve_fn,
                                 drive_folders_signal=drive_folders_signal,
                                 action_resolved_signal=action_resolved_signal,
                                 asana_list_fn=asana_list_fn,
                                 asana_resolve_fn=asana_resolve_fn,
                                 asana_projects_signal=asana_projects_signal,
                                 guru_list_fn=guru_list_fn,
                                 guru_resolve_fn=guru_resolve_fn,
                                 guru_targets_signal=guru_targets_signal,
                                 confirm_fn=confirm_fn, cancel_fn=cancel_fn,
                                 stop_fn=stop_fn, queue_fn=queue_fn,
                                 parent=self)
        # The chat is the SPA's default route — no fragment, so pre-router
        # bundles keep working unchanged.
        self.host = WebHost(bridge=self.bridge, channel_name="almaBridge",
                            route="", log_name="alma.agent.web", parent=self)
        # Back-compat surface: tests and callers reach the view directly.
        self.view = self.host.view
        self._page = self.host._page

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.host)

    def load(self):
        self.host.load()
```

### 3.8 `src/ui/main_window.py` — wire the controller into the page

**Intent:** in `_create_agent_page`, the `AgentPage(...)` construction gains two kwargs. The post-change construction block (anchor: the existing `confirm_fn=`/`cancel_fn=` lines):

```python
            self.agent_page = AgentPage(self._agent_controller.engine,
                                        send_fn=self._agent_controller.send,
                                        tool_poll=self._agent_controller.recent_tool_calls,
                                        session_api=self._agent_controller,
                                        job_poll=self._agent_controller.recent_jobs,
                                        draft_api=self._agent_controller,
                                        voice=self._agent_controller.voice,
                                        action_poll=self._agent_controller.poll_action_requests,
                                        connect_fn=self._agent_controller.start_google_connect,
                                        google_state_signal=self._agent_controller.googleAuthState,
                                        list_fn=self._agent_controller.list_drive_folders,
                                        resolve_fn=self._agent_controller.resolve_drive_folder,
                                        drive_folders_signal=self._agent_controller.driveFoldersListed,
                                        action_resolved_signal=self._agent_controller.actionResolved,
                                        asana_list_fn=self._agent_controller.list_asana_projects_for_picker,
                                        asana_resolve_fn=self._agent_controller.resolve_asana_board,
                                        asana_projects_signal=self._agent_controller.asanaProjectsListed,
                                        guru_list_fn=self._agent_controller.list_guru_targets,
                                        guru_resolve_fn=self._agent_controller.resolve_guru_target,
                                        guru_targets_signal=self._agent_controller.guruTargetsListed,
                                        confirm_fn=self._agent_controller.execute_write,
                                        cancel_fn=self._agent_controller.cancel_write,
                                        stop_fn=self._agent_controller.stop_run,
                                        queue_fn=self._agent_controller.queue_user_message)
            return self.agent_page
```

### 3.9 `src/ui/pages/enablement/page.py` — the Workbench drawer's stop/queue + run_stopped resync

**Intent:** the page-level mirror of the controller work: the drawer's `ChatBridge` gains `stop_fn`/`queue_fn`, the page keeps its own `_web_chat_pending` queue with the FIX-5 peek-then-pop drain, and `run_stopped` resyncs the native panel + canvas.

(a) **Replace `_get_web_chat_bridge`** (the changes: `self._web_chat_pending = []`, the two new kwargs, and the `busy_changed` connect):

```python
    def _get_web_chat_bridge(self):
        """One ChatBridge over THIS page's enablement ChatEngine, published as
        ``almaBridge`` on every web tab's channel (M5.5) — so the in-page Renn
        drawer is the SAME assistant, session, and canvas coupling as the Qt
        ChatPanel, just rendered by the updated web chat. Built once; None when
        the engine is unavailable (the drawer shows its unavailable state and
        the web button falls back to the Qt drilldown)."""
        if getattr(self, "_web_chat_bridge", None) is not None:
            return self._web_chat_bridge
        if self._engine is None:
            return None
        try:
            from src.ui.web.chat_bridge import ChatBridge
            self._web_chat_pending = []
            self._web_chat_bridge = ChatBridge(
                self._engine, send_fn=self._web_chat_send,
                tool_poll=self._web_chat_tool_poll,
                stop_fn=self._web_chat_stop,
                queue_fn=self._web_chat_queue, parent=self)
            self._engine.busy_changed.connect(self._on_web_chat_busy_changed)
        except Exception:  # noqa: BLE001 — chat degrades, the tabs still work
            self._web_chat_bridge = None
        return self._web_chat_bridge
```

(b) **Replace `_web_chat_send`** (it now returns True/False — the FIX-5 contract) **and add the four new methods right after it**, before `_mirror_user_turn`:

```python
    def _web_chat_send(self, text):
        """A web-drawer send: mirror the user turn into the Qt ChatPanel (the
        canonical transcript) AND to every open drawer, then dispatch to the
        shared engine. The originating drawer already rendered its own bubble
        and dedupes this echo (by text); a second drawer (flag=all) renders it,
        so the transcript stays coherent across surfaces.

        Returns True when the message was dispatched, False when the
        publish-confirm refusal below swallowed it — the drain uses that to
        KEEP a queued message instead of losing it (FIX 5)."""
        # Refuse a send while a publish confirm modal is open: the modal blocks
        # human input, so any send arriving now is a page script — and a chat
        # revise_draft during the publish would write the DB out from under the
        # confirmed content. (Zero legit-UX cost: a human can't send then.)
        if getattr(getattr(self, "workbench", None), "is_publish_inflight", False):
            return False
        try:
            self.chat.add_message("u", text)
        except Exception:  # noqa: BLE001
            pass
        self._mirror_user_turn(text)
        self._dispatch_chat(text)
        return True

    def _web_chat_stop(self) -> bool:
        """The drawer's Stop control: abort this page's in-flight engine turn.
        No authority beyond that abort."""
        engine = self._engine
        if engine is None or not hasattr(engine, "stop"):
            return False
        try:
            return bool(engine.stop())
        except Exception:  # noqa: BLE001 — a failed stop must never crash the chat
            return False

    def _web_chat_queue(self, text) -> str:
        """The drawer's queue-while-busy send: dispatch now when idle ('sent'),
        else park it ('queued') to drain on the next busy_changed(False) —
        the page-level mirror of AgentChatController.queue_user_message."""
        engine = self._engine
        if engine is None:
            return "error"
        if engine.is_busy:
            self._web_chat_pending.append(text or "")
            return "queued"
        self._web_chat_send(text or "")
        return "sent"

    def _on_web_chat_busy_changed(self, busy):
        """Drain ONE queued drawer message when the engine goes idle — via
        deferred dispatch, so the drain's busy_changed(True) never reaches
        later slots before the False they are still processing."""
        if busy or not getattr(self, "_web_chat_pending", None):
            return
        from PySide6.QtCore import QCoreApplication, QTimer
        if QCoreApplication.instance() is not None:
            QTimer.singleShot(0, self._drain_web_chat_pending)
        else:
            self._drain_web_chat_pending()

    def _drain_web_chat_pending(self):
        """Send the head of the drawer queue if the engine is still idle; if a
        turn started meanwhile, leave it queued for the next idle.

        PEEK-then-pop (FIX 5): ``_web_chat_send`` refuses (returns False)
        while the publish-confirm modal is open, and a popped-then-refused
        message was silently LOST. On refusal the message stays at the head
        and one ~1s retry is armed — a modal that outlives the last
        busy_changed(False) would otherwise strand it with nothing left to
        drain on. On success the drained text is announced so the JS surfaces
        un-badge exactly the bubble that ran (FIX 4)."""
        pending = getattr(self, "_web_chat_pending", None)
        if not pending or self._engine is None:
            return
        if self._engine.is_busy:
            return
        text = pending[0]
        if not self._web_chat_send(text):
            self._arm_web_drain_retry()
            return
        pending.pop(0)
        bridge = getattr(self, "_web_chat_bridge", None)
        if bridge is not None:
            bridge.notify_queued_dispatched(text)

    def _arm_web_drain_retry(self):
        """One armed ~1s retry for a drain the publish modal refused. Single
        timer at a time (re-armed on each refusal), so an open modal is polled
        gently rather than stacking timers; the next busy_changed(False) also
        retries, whichever comes first."""
        if getattr(self, "_web_drain_retry_armed", False):
            return
        from PySide6.QtCore import QCoreApplication, QTimer
        if QCoreApplication.instance() is None:
            return   # no event loop to schedule on; the next idle drains it
        self._web_drain_retry_armed = True

        def _retry():
            self._web_drain_retry_armed = False
            self._drain_web_chat_pending()

        QTimer.singleShot(1000, _retry)
```

(c) **In `_setup_engine`, after the `bridge_recycle_requested` connect**, insert the `run_stopped` wiring — full post-change method:

```python
    def _setup_engine(self):
        """Build the live ChatEngine (the ACP bridge boots lazily on first send)."""
        try:
            from src.services.chat_engine import ChatEngine
            self._engine = ChatEngine(
                system_prompt=RENN_SYSTEM_PROMPT,
                task_type="enablement_chat",
                context_provider=self._chat_context,
                tools_enabled=True,
                use_mcp_tools=True,
                db_path=self._engine_db_path(),
                # M5.5: the web Renn drawer streams; response_ready still
                # finalizes whole turns, so the Qt ChatPanel is unaffected.
                stream=True,
            )
            self._engine.response_ready.connect(self._on_engine_response)
            self._engine.error_occurred.connect(self._on_engine_error)
            self._engine.bridge_recycle_requested.connect(self._on_bridge_recycle)
            # FIX 2c: a stopped run ends with run_stopped, not response_ready,
            # so without this the native panel showed nothing and the canvas
            # never refreshed after tools that ran before the kill.
            if hasattr(self._engine, "run_stopped"):
                self._engine.run_stopped.connect(self._on_engine_stopped)
        except Exception as exc:  # noqa: BLE001 — chat degrades, the page still works
            logger.warning("Enablement chat engine unavailable: %s", exc)
            self._engine = None
```

(d) **Immediately after `_on_engine_error`, insert:**

```python
    def _on_engine_stopped(self):
        """The user aborted the in-flight turn (Stop). Surface it on the
        native Qt ChatPanel and run the same refresh the response path does —
        a tool that completed before the kill may have revised or created
        content the canvas is not showing. The panel is written DIRECTLY
        (like _on_engine_response / _on_engine_error), never via the
        say-to-everywhere notice relay: the bridge already re-emits
        runStopped into every web drawer, and a pushed notice would double
        the line there."""
        self.chat.add_message("a", "Run stopped.")
        try:
            self._load_live(prefer_draft_id=self.workbench.active_draft_id)
            self._reload_active_draft_canvas()
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"Refresh error: {exc}")
```

**Wiring note:** `_on_engine_stopped` writes the panel DIRECTLY (like `_on_engine_response`), never via the say-to-everywhere notice relay — the bridge already re-emits `runStopped` into every web drawer, and a pushed notice would double the line there. This is pinned by `tests/test_chat_stop_queue.py::TestPageRunStopped::test_on_engine_stopped_writes_the_panel_directly`.

### 3.10 `web/src/chat/ChatApp.jsx` — Composer/Bubble/unbadgeDispatched + Agent surface wiring

**Intent:** extract the composer and bubble into exported components (so the drawer shares them and vitest reaches them), add the queued badge, the Stop button state machine, the streamed-buffer ref that `runStopped` finalizes, and route sends through `bridge.queueMessage`.

(a) **Immediately after the `Meter` component (before the `when(ts)` helper), insert the three exported helpers:**

```jsx
// Clear the queued badge on the first queued bubble whose text matches the
// dispatched payload. Driven ONLY by the bridge's queuedDispatched(text) —
// never inferred from busyChanged(true): the Python queue interleaves items
// with no JS bubble ([SYSTEM] triggers, sends from the other surface), so "a
// turn started" is not proof that THIS surface's oldest queued bubble is the
// one that ran. Pure + exported so both surfaces share it and it is testable.
export function unbadgeDispatched(messages, text) {
  const i = messages.findIndex((x) => x.queued && x.text === text);
  if (i < 0) return messages;
  const next = messages.slice();
  next[i] = { ...next[i], queued: false };
  return next;
}

// One transcript bubble. Exported so the queued badge is testable: a message
// parked while a turn ran carries `queued: true` until its turn starts.
export function Bubble({ m }) {
  return (
    <div className={"msg " + m.role}>
      {m.role === "assistant" ? <Markdown text={m.text} /> : m.text}
      {m.queued ? <span className="queued-badge">queued</span> : null}
    </div>
  );
}

// The message composer. Exported so its busy contract is testable: the input
// stays ENABLED while a turn runs (extra context queues and sends when the
// engine frees up), and the Send control swaps to Stop while busy. `mic` is
// the surface's own dictation button (or null).
export function Composer({ input, onInput, onSend, onStop, busy, stopping, disabled, mic }) {
  return (
    <div className="composer">
      <input
        value={input}
        onChange={(e) => onInput(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && onSend()}
        placeholder="Message the agent…"
        autoFocus
      />
      {mic}
      {busy ? (
        <button className="send stop" onClick={onStop} disabled={stopping || disabled}
                title="Stop this response">
          Stop
        </button>
      ) : (
        <button className="send" onClick={onSend} disabled={disabled}>
          Send
        </button>
      )}
    </div>
  );
}
```

(b) **In the `ChatApp` component's state declarations** — after `voiceError` — add `stopping`, and next to `scrollRef` add the two refs. Post-change block:

```jsx
  const [stopping, setStopping] = useState(false); // Stop clicked, kill in flight
  const scrollRef = useRef(null);
  // The streamed buffer, mirrored into a ref so runStopped can finalize the
  // partial text without racing React state.
  const streamRef = useRef("");
  // Monotonic id for user bubbles, so a queueMessage callback can badge the
  // exact bubble it queued.
  const seqRef = useRef(0);
```

(c) **In the main `useEffect(() => { if (!bridge) return; ... }, [bridge])` bridge-wiring block, replace the `responseReady` / `errorOccurred` / `busyChanged` connects and insert the `queuedDispatched` + `runStopped` connects between `busyChanged` and `statusUpdate`; also mirror the stream buffer in `tokenStreamed`.** Post-change run, from `responseReady` through `tokenStreamed`:

```jsx
    bridge.responseReady.connect((t) => {
      document.title = "RT:" + t; // round-trip hook for headless verification
      setBusy(false);
      setTools([]);
      setStreaming(""); // finalized — the full text replaces the streamed buffer
      streamRef.current = "";
      setMessages((m) => [...m, { role: "assistant", text: t }]);
    });
    bridge.errorOccurred.connect((e) => {
      setBusy(false);
      setStreaming("");
      streamRef.current = "";
      setMessages((m) => [...m, { role: "error", text: e }]);
    });
    bridge.busyChanged.connect((b) => {
      setBusy(b);
      if (b) {
        setTools([]);
        setStreaming("");
        streamRef.current = "";
        setStatus("Renn is thinking…");
      } else {
        setStopping(false);
      }
    });
    // A queued bubble is un-badged only when Python names the drained text —
    // see unbadgeDispatched for why busyChanged(true) must not be used.
    if (bridge.queuedDispatched) {
      bridge.queuedDispatched.connect((t) => {
        setMessages((m) => unbadgeDispatched(m, t));
      });
    }
    // The user aborted the turn: finalize any partial text instead of
    // discarding it, or say so plainly when nothing had streamed yet.
    if (bridge.runStopped) {
      bridge.runStopped.connect(() => {
        const buf = streamRef.current;
        streamRef.current = "";
        setStreaming("");
        setTools([]);
        setStopping(false);
        setMessages((m) => [
          ...m,
          buf
            ? { role: "assistant", text: buf + "\n\n— stopped" }
            : { role: "system", text: "Run stopped." },
        ]);
        window.__almaRunStopped = (window.__almaRunStopped || 0) + 1; // headless hook
      });
    }
    // Keep the Agent's own neutral label — the shared engine hardcodes
    // "Gemini is thinking…", which is wrong on the Claude path. Real status
    // messages (e.g. bridge refresh) still come through.
    bridge.statusUpdate.connect((s) => {
      if (s && !/is thinking/i.test(s)) setStatus(s);
    });
    // Per-token streaming: append each delta into the in-flight assistant bubble.
    bridge.tokenStreamed.connect((d) => {
      setStreaming((s) => {
        const next = s + d;
        streamRef.current = next;
        window.__almaStreamBuf = next; // headless hook
        return next;
      });
    });
```

(d) **In the `historyLoaded` handler, add the `streamRef` reset** — post-change handler:

```jsx
    // A loaded past chat (or a new-chat reset) replaces the live transcript.
    bridge.historyLoaded.connect((j) => {
      try {
        const p = JSON.parse(j);
        const msgs = (p.messages || []).map((m) => ({
          role: m.role === "user" ? "user" : "assistant",
          text: m.content || "",
        }));
        setMessages(msgs);
        setTools([]);
        setMeter(null);
        setBusy(false);
        setStreaming("");
        streamRef.current = "";
        setHistoryOpen(false);
        window.__almaLoaded = msgs.length; // headless hook
      } catch (e) {}
    });
```

(e) **Replace the `send()` function and add `stop()` after it** (anchor: `newChat()` follows):

```jsx
  function send() {
    const text = input.trim();
    if (!text || !bridge) return;
    const id = ++seqRef.current;
    setMessages((m) => [...m, { role: "user", text, id }]);
    setInput("");
    if (bridge.queueMessage) {
      // Always route through the queue: it sends immediately when idle and
      // parks the text while a turn runs. The result arrives via the
      // QWebChannel callback (same consumption as voiceAvailable).
      bridge.queueMessage(text, (r) => {
        if (r === "queued") {
          setMessages((m) => m.map((x) => (x.id === id ? { ...x, queued: true } : x)));
        }
      });
    } else {
      bridge.send(text);
    }
  }

  function stop() {
    if (!bridge || !bridge.stopRun) return;
    setStopping(true);
    bridge.stopRun((ok) => {
      // Nothing was aborted (idle, or a client with no abort) — the run keeps
      // going, so give the button back rather than leaving it dead.
      if (!ok) setStopping(false);
    });
  }
```

(f) **In the render, replace the per-message `<div className={"msg " + m.role}>` map body with `<Bubble key={i} m={m} />`:**

```jsx
      <div className="messages" ref={scrollRef}>
        {messages.map((m, i) => (
          <Bubble key={i} m={m} />
        ))}
```

(the `{busy && (...)}` streaming block that follows is unchanged), **and replace the whole inline `<div className="composer">…</div>` block (input + mic button + send button) with the `<Composer …>` element carrying the mic button as a prop** — post-change block, from after `<Meter m={meter} />`:

```jsx
      <Composer
        input={input}
        onInput={setInput}
        onSend={send}
        onStop={stop}
        busy={busy}
        stopping={stopping}
        disabled={!bridge}
        mic={
          <button
            className={
              "mic" +
              (listening || transcribing ? " listening" : voiceError ? " err" : "")
            }
            title={
              !voiceAvail
                ? "Dictation unavailable on this device"
                : listening
                ? "Listening — click to stop"
                : voiceError
                ? "Dictation failed — click to try again"
                : "Click to dictate (on-device)"
            }
            disabled={!voiceAvail || busy || transcribing}
            onClick={() => {
              if (!bridge) return;
              if (listening) bridge.stopVoice();
              else bridge.startVoice();
            }}
          >
            {transcribing ? "● transcribing…" : listening ? "● listening" : voiceError ? "mic ⚠" : "mic"}
          </button>
        }
      />
```

**Note the behavior deltas encoded there:** the input is no longer `disabled={busy}` (queueing is the point); the Send button no longer carries `disabled={busy}`; `bridge.queueMessage(text, callback)` is the QWebChannel async-return form (the result arrives via callback — same consumption pattern as `voiceAvailable`); a `queued` result badges exactly the bubble with the captured monotonic `id`.

### 3.11 `web/src/chat/ChatDrawer.jsx` — the Workbench drawer surface

**Intent:** same additions as ChatApp (queue-through, stop, badge, stream ref, `Bubble` reuse), on the drawer. The file is small — **replace it in full** with:

```jsx
import { useEffect, useRef, useState } from "react";
import { Markdown } from "../lib/markdown.jsx";
import { Bubble, unbadgeDispatched } from "./ChatApp.jsx";

// Renn as an in-page drawer (M5.5) — the updated chat surface the web tabs
// raise instead of the legacy Qt ChatPanel drilldown. A pure renderer over
// the SAME ChatBridge/ChatEngine page.py already owns (send_fn routes through
// _dispatch_chat), so it is the same assistant, session, and canvas coupling
// as before — streaming bubbles, markdown, live tool rows, and host notices
// (scan/publish results) included. History/jobs/review/voice deliberately
// stay on the dedicated Agent page.
//
// ``bridge`` is the resolved almaBridge object (or null → unavailable state);
// the parent owns open/close so it can fall back to the Qt drilldown when no
// web chat bridge is registered.
export default function ChatDrawer({ bridge, open, onClose }) {
  const [messages, setMessages] = useState([]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("Renn is thinking…");
  const [streaming, setStreaming] = useState("");
  const [tools, setTools] = useState([]);
  const [input, setInput] = useState("");
  const [stopping, setStopping] = useState(false);
  const scrollRef = useRef(null);
  // Texts this drawer rendered optimistically and expects to see echoed back
  // as a chatNotice("u") from the host — so it can skip its own echo (a second
  // open drawer, which has no pending echo, renders the turn normally).
  const pendingEchoes = useRef([]);
  // The streamed buffer mirrored into a ref (runStopped finalizes it) and a
  // monotonic id so a queueMessage callback can badge the bubble it queued.
  const streamRef = useRef("");
  const seqRef = useRef(0);

  useEffect(() => {
    if (!bridge) return;
    bridge.responseReady.connect((t) => {
      setBusy(false);
      setTools([]);
      setStreaming("");
      streamRef.current = "";
      setMessages((m) => [...m, { role: "assistant", text: t }]);
      window.__almaDrawerMsgs = (window.__almaDrawerMsgs || 0) + 1; // headless hook
    });
    bridge.errorOccurred.connect((e) => {
      setBusy(false);
      setStreaming("");
      streamRef.current = "";
      setMessages((m) => [...m, { role: "error", text: e }]);
    });
    bridge.busyChanged.connect((b) => {
      setBusy(b);
      if (b) {
        setTools([]);
        setStreaming("");
        streamRef.current = "";
        setStatus("Renn is thinking…");
      } else {
        setStopping(false);
      }
    });
    // A queued bubble is un-badged only when Python names the drained text —
    // see unbadgeDispatched (ChatApp) for why busyChanged(true) must not be
    // used: the shared queue also drains items with no bubble in THIS drawer.
    if (bridge.queuedDispatched) {
      bridge.queuedDispatched.connect((t) => {
        setMessages((m) => unbadgeDispatched(m, t));
      });
    }
    // The user aborted the turn: keep any partial text instead of dropping it.
    if (bridge.runStopped) {
      bridge.runStopped.connect(() => {
        const buf = streamRef.current;
        streamRef.current = "";
        setStreaming("");
        setTools([]);
        setStopping(false);
        setMessages((m) => [
          ...m,
          buf
            ? { role: "assistant", text: buf + "\n\n— stopped" }
            : { role: "system", text: "Run stopped." },
        ]);
      });
    }
    // Keep the drawer's neutral label — the shared engine hardcodes a
    // provider-specific "… is thinking" we don't want to parrot.
    bridge.statusUpdate.connect((s) => {
      if (s && !/is thinking/i.test(s)) setStatus(s);
    });
    if (bridge.tokenStreamed) {
      bridge.tokenStreamed.connect((d) =>
        setStreaming((s) => {
          const next = s + d;
          streamRef.current = next;
          return next;
        })
      );
    }
    if (bridge.toolCall) {
      bridge.toolCall.connect((j) => {
        try {
          setTools((p) => [...p, JSON.parse(j)]);
        } catch (e) {}
      });
    }
    // Host notices arrive as bubbles too, so every surface shows the same
    // transcript: scan/publish outcomes ("a"), and USER turns ("u") mirrored
    // from the Qt panel or another drawer. A user turn THIS drawer originated
    // was already rendered optimistically (see send()) — we dedupe our own
    // echo by text so it isn't doubled here, while a turn from another surface
    // (no pending echo) is appended.
    if (bridge.chatNotice) {
      bridge.chatNotice.connect((j) => {
        try {
          const p = JSON.parse(j);
          if (p.role === "u") {
            const idx = pendingEchoes.current.indexOf(p.text || "");
            if (idx !== -1) {
              pendingEchoes.current.splice(idx, 1);   // our own echo — skip
              return;
            }
            setMessages((m) => [...m, { role: "user", text: p.text || "" }]);
          } else {
            setMessages((m) => [...m, { role: "assistant", text: p.text || "" }]);
          }
          window.__almaDrawerNotices = (window.__almaDrawerNotices || 0) + 1;
        } catch (e) {}
      });
    }
    window.__almaDrawerReady = true; // headless hook
  }, [bridge]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages, tools, busy, streaming, open]);

  function send() {
    const text = input.trim();
    if (!text || !bridge) return;
    const id = ++seqRef.current;
    setMessages((m) => [...m, { role: "user", text, id }]);
    pendingEchoes.current.push(text);   // dedupe our own mirrored echo
    setInput("");
    if (bridge.queueMessage) {
      // Sends immediately when idle; parks the text while a turn runs.
      bridge.queueMessage(text, (r) => {
        if (r === "queued") {
          setMessages((m) => m.map((x) => (x.id === id ? { ...x, queued: true } : x)));
        }
      });
    } else {
      bridge.send(text);
    }
  }

  function stop() {
    if (!bridge || !bridge.stopRun) return;
    setStopping(true);
    bridge.stopRun((ok) => {
      if (!ok) setStopping(false);   // nothing was aborted — re-arm the button
    });
  }

  if (!open) return null;

  return (
    <aside className="chatdock" data-testid="chat-drawer">
      <div className="chatdock-hdr">
        <span className="dot" aria-hidden="true" />
        <span className="chatdock-title">Renn</span>
        <span className="chatdock-sub">Enablement assistant</span>
        <span className="cal-spacer" />
        <button className="drawer-x" onClick={onClose} title="Close">×</button>
      </div>

      {!bridge ? (
        <div className="chatdock-empty">
          <p>Renn isn't wired to this surface yet.</p>
        </div>
      ) : (
        <>
          <div className="chatdock-msgs" ref={scrollRef}>
            {messages.length === 0 && !busy && (
              <div className="msg assistant">
                <Markdown text={"Hi, I'm Renn. Ask me to revise the draft, draft subtasks, or push a card — I can see what you're working on."} />
              </div>
            )}
            {messages.map((m, i) => (
              <Bubble key={i} m={m} />
            ))}
            {busy && (
              <div className={"msg assistant " + (streaming ? "streaming" : "thinking")}>
                {streaming ? (
                  <div className="streamtext">
                    {streaming}
                    <span className="caret">▍</span>
                  </div>
                ) : (
                  <div className="tstatus">{status}</div>
                )}
                {tools.length > 0 && (
                  <div className="tools">
                    {tools.map((t, i) => (
                      <div key={i} className={"tool " + (t.ok ? "ok" : "err")}>
                        <span className="tick">{t.ok ? "✓" : "✗"}</span>
                        <span className="tname">{t.name}</span>
                        <span className="tmeta">
                          {t.ms ? ` · ${t.ms}ms` : ""}
                          {t.rows !== null && t.rows !== undefined ? ` · ${t.rows} rows` : ""}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
          <div className="chatdock-composer">
            <input value={input}
                   onChange={(e) => setInput(e.target.value)}
                   onKeyDown={(e) => e.key === "Enter" && send()}
                   placeholder="Message Renn…" />
            {busy ? (
              <button className="wb-btn primary stop" onClick={stop}
                      disabled={stopping} title="Stop this response">Stop</button>
            ) : (
              <button className="wb-btn primary" onClick={send}
                      disabled={!input.trim()}>Send</button>
            )}
          </div>
        </>
      )}
    </aside>
  );
}
```

**Note:** `send()` no longer refuses while `busy` (the old `if (!text || !bridge || busy) return;` became `if (!text || !bridge) return;`), the composer input no longer has `disabled={busy}`, and the Send/Stop swap mirrors ChatApp with the workbench button classes (`wb-btn primary stop`).

### 3.12 `web/src/styles.css` — chat additions

**Intent:** the Stop button tint, the queued badge, and the stopped-run system line. **Immediately after the `.composer .send:disabled` rule** (and before the "connect & configure pickers" section), insert:

```css
.composer .send.stop, .chatdock-composer .stop {
  background: var(--err); border-color: var(--err); color: #fff;
}

/* queued-while-busy bubbles + the stopped-run system line */
.queued-badge {
  display: inline-block; margin-left: 8px; padding: 1px 7px; border-radius: 8px;
  font-size: 10px; font-weight: 700; letter-spacing: 0.5px; text-transform: uppercase;
  background: rgba(255, 255, 255, 0.28); color: inherit; vertical-align: middle;
}
.msg.assistant .queued-badge, .msg.system .queued-badge {
  background: rgba(15, 81, 50, 0.08); color: var(--mid);
}
.msg.system {
  background: transparent; border: none; color: var(--mid);
  font-size: 12.5px; font-style: italic; text-align: center;
  max-width: 100%; margin: 6px auto; padding: 2px 13px;
}
```

### 3.13 Rebuild the bundle

```bash
npm --prefix web run build
```

Never port `src/ui/web/dist/index.html` bytes; regenerate them (guardrail 8).

---

## 4. Workstream 2 — List-format parity (`src/data/html_markdown.py` only)

**Feature contract.** One shared converter feeds every preview/publish/copy surface (Qt editor preview, Guru publish payload, Zendesk draft rendering, clipboard flavours), so a change here moves every surface together — that is the point. Two defects fixed:

1. **Loose lists double-spaced on Chromium surfaces.** Blank-line-separated markdown lists render as `<li><p>…</p></li>`. Qt collapses the inner `<p>` (editor looked tight) while Chromium (Guru, Zendesk) honors its margins (published card looked double-spaced). `markdown_to_html` now unwraps the `<p>` when it is the item's only paragraph, and unwraps a leading `<p>` immediately followed by a nested list. Genuinely multi-paragraph items keep their paragraphs (the `(?!</?p\b)` guard).
2. **Task checkboxes rendered nowhere.** `- [ ]`/`- [x]` used to become `<input type="checkbox" disabled>` — Qt cannot render `<input>`, the strict sanitizer drops it, and Guru may strip it. Now they become text glyphs `☐`/`☑` (keeping `class="task-list-item"`), and `html_to_markdown` maps the glyphs BACK to `[ ]`/`[x]` — **fence-aware**, so a code sample containing `- ☐ item` round-trips byte-identical.

**Review fixes baked in:**

- `_GFM_TASK` gained a `(?P<lead>…)` group so a task item still wrapped in its loose-item `<p>` (order of post-passes) is matched too, and the lead is preserved in the replacement.
- `_restore_task_markers` is **backtick-fence-aware only**: both converter paths emit ``` fences exclusively (QTextMarkdownWriter and the stdlib fallback), and a line starting `~~~` is Qt strikethrough (`~~` + text beginning `~`) — treating it as a fence would suppress restoration for the rest of the document. Leading whitespace is allowed (Qt indents fences inside list items); a one-line span like ```` ```foo``` ```` is not an opener (the ``[^`]*$`` guard — a backtick fence's info string cannot contain backticks, per CommonMark); a closer must be at least as long as its opener and carry no info string.
- **Consequence you must tell the operator about:** this converter feeds `zendesk_store.draft_content_hash`, so existing Zendesk draft review hashes ROTATE on upgrade — recorded copy-reviews correctly invalidate and the specialist re-opens the source view before the next copy. Expected, by design, one-time.

### 4.1 The changed code

(a) **Replace `markdown_to_html` and the regex/helper block that follows it** (everything from the `def markdown_to_html` line through `_gfm_task_items`, i.e. up to the "Qt HTML → portable clean HTML" section which is unchanged):

```python
def markdown_to_html(md: str) -> str:
    import markdown as _md
    # The rich editor emits GitHub-dialect markdown (toMarkdown). The base
    # `markdown` package (no pymdown-extensions installed) renders tables /
    # fenced code / sane lists but NOT GFM strikethrough or task-list
    # checkboxes — small regex post-passes close those gaps within the
    # installed deps so the preview matches what the WYSIWYG editor showed.
    # md_in_html lets the document reader's exact-colour callout boxes
    # (<div markdown="1" style="background-color:…">) render their inner
    # markdown; tables/fenced_code/sane_lists as before. All core extensions
    # (no new dependency).
    html = _md.markdown(
        md or "",
        extensions=["tables", "fenced_code", "sane_lists", "md_in_html"])
    html = _unwrap_loose_list_items(html)
    html = _GFM_STRIKE.sub(r"<del>\1</del>", html)
    html = _gfm_task_items(html)
    return html


# ~~text~~ → <del>text</del>  (GFM strikethrough)
_GFM_STRIKE = re.compile(r"~~(.+?)~~", re.DOTALL)
# Leading "[ ]" / "[x]" inside a freshly-opened <li> (tight, or still wrapped
# in the item's leading <p>) → a text glyph. Qt cannot render <input>, the
# strict sanitizer drops it, and Guru may strip it — a character survives
# every surface.
_GFM_TASK = re.compile(
    r"<li>(?P<lead>\s*(?:<p>\s*)?)\[(?P<state> |x|X)\]\s*", re.IGNORECASE)

# Loose (blank-line-separated) markdown lists render as <li><p>…</p></li>.
# Qt collapses the inner <p> while Chromium (Guru, Zendesk) honors its
# margins, so the editor shows tight items and the published card shows
# double-spaced ones. Unwrap the <p> when it is the item's only paragraph
# (the (?!</?p\b) guard keeps genuinely multi-paragraph items intact), and
# unwrap a leading <p> immediately followed by a nested list.
_LOOSE_LI = re.compile(
    r"<li>\s*<p>((?:(?!</?p\b).)*?)</p>\s*</li>", re.DOTALL)
_LOOSE_LI_NESTED = re.compile(
    r"<li>\s*<p>((?:(?!</?p\b).)*?)</p>\s*(?=<[uo]l\b)", re.DOTALL)


def _unwrap_loose_list_items(html: str) -> str:
    html = _LOOSE_LI.sub(r"<li>\1</li>", html)
    html = _LOOSE_LI_NESTED.sub("<li>\\1\n", html)
    return html


def _gfm_task_items(html: str) -> str:
    def _repl(m: "re.Match") -> str:
        glyph = "☑" if m.group("state").lower() == "x" else "☐"
        return f'<li class="task-list-item">{m.group("lead")}{glyph} '
    return _GFM_TASK.sub(_repl, html)
```

(b) **Immediately after `qt_html_to_clean_html` (which is unchanged) and before `html_to_markdown`, insert the new reverse-map block:**

````python
# List items whose text starts with a task glyph map back to markdown
# checkboxes: "- ☐ Do A" → "- [ ] Do A" (inverse of _gfm_task_items).
_TASK_GLYPH_MD = re.compile(
    r"^(?P<marker>\s*(?:[-*+]|\d+[.)])\s+)(?P<glyph>[☐☑])\s*",
    re.MULTILINE)

# A fenced-code delimiter line. Backtick fences ONLY: both converter paths
# emit them exclusively (QTextMarkdownWriter writes ``` fences; the stdlib
# fallback emits literal "```"), and a line starting "~~~" is Qt
# strikethrough ("~~" + text beginning "~"), never a converter-produced
# fence — treating it as one would suppress restoration for the rest of
# the document. Leading whitespace is allowed because Qt indents fences
# inside list items. The [^`]*$ guard keeps a one-line code SPAN
# (```foo```) from reading as an opener, per the CommonMark rule that a
# backtick fence's info string cannot contain backticks.
_FENCE_DELIM = re.compile(r"^\s*(?P<fence>`{3,})(?P<info>[^`]*)$")


def _restore_task_markers(md: str) -> str:
    """Map task glyphs back to markdown checkboxes, OUTSIDE code fences.

    A line like "- ☐ item" inside a ``` fence is code someone wrote and
    must round-trip byte-identical (it seeds _article_body_text and the
    zendesk body_text clipboard flavour); the same line outside a fence is
    a rendered task item mapping back to "- [ ] item". The document is
    split on fence delimiter lines and the substitution runs only on the
    segments outside fences, so out-of-fence behavior is unchanged."""
    def _repl(m: "re.Match") -> str:
        box = "[x]" if m.group("glyph") == "☑" else "[ ]"
        return f"{m.group('marker')}{box} "

    out: list[str] = []
    plain: list[str] = []       # consecutive lines outside any fence

    def _flush():
        if plain:
            out.append(_TASK_GLYPH_MD.sub(_repl, "\n".join(plain)))
            plain.clear()

    fence_len = 0               # opening run length; >0 while inside
    for line in md.split("\n"):
        m = _FENCE_DELIM.match(line)
        # An opener is any delimiter line; a closer must be at least as
        # long as its opener and carry no info string (CommonMark).
        if m is not None and (
                fence_len == 0
                or (len(m.group("fence")) >= fence_len
                    and not m.group("info").strip())):
            _flush()
            fence_len = len(m.group("fence")) if fence_len == 0 else 0
            out.append(line)
        elif fence_len:
            out.append(line)    # inside a fence: byte-identical
        else:
            plain.append(line)
    _flush()
    return "\n".join(out)
````

(c) **Replace `html_to_markdown`** (both return paths now route through `_restore_task_markers`):

```python
def html_to_markdown(html: str) -> str:
    if not (html or "").strip():
        return ""
    try:
        from PySide6.QtGui import QGuiApplication, QTextDocument
        # isinstance, NOT `is not None`: QGuiApplication.instance() returns the
        # QCoreApplication singleton in a console/headless process, and
        # constructing a QTextDocument without a GUI application ABORTS the
        # process (no Python exception to catch). Only take the Qt path when a
        # real QGuiApplication is up.
        if isinstance(QGuiApplication.instance(), QGuiApplication):
            doc = QTextDocument()
            doc.setHtml(html)
            return _restore_task_markers(doc.toMarkdown(
                QTextDocument.MarkdownFeature.MarkdownDialectGitHub
            ).strip())
    except Exception:
        pass
    parser = _MarkdownParser()
    parser.feed(html)
    parser.close()
    return _restore_task_markers(parser.result())
```

**Wiring notes:** nothing else changes — `_MarkdownParser` and the Qt-vs-fallback selection logic are untouched. No new imports (the module already has `re`). Consumers (`guru` publish, zendesk `_copy_bundle`'s `markdown_to_html(row["body"])`, `_article_body_text`) pick the change up automatically because there is exactly one converter.

---

## 5. Workstream 3 — Asana subtask promotion (migration 056)

**Feature contract.** Subtasks used to exist only as name+done checklist mirrors in `enablement_subtasks`, so assigned subtask work never reached the calendar, the "mine" scope, or Renn. Now: a subtask WITH an assignee (ANY assignee — the existing "mine" display scope filters per-operator downstream) is promoted into a real `enablement_tasks` row carrying `parent_task_ref` (the parent's Asana gid). A row is a subtask **iff `parent_task_ref IS NOT NULL`** — the single flag behind both calendars' `↳` marker, `list_tasks`' `parent_title` join, and Renn's `is_subtask` field. Unassigned subtasks stay checklist-only; the parent's `x / y` checklist counts are unchanged. One `list_subtasks` call per parent per poll is the whole API budget. The Asana client stays GET-only; attribution rides `task_board_links` only (mig 055 stays the sole authority — nothing is derived from the permalink).

**Review fixes baked in:**

- **Verified major — un-assignment froze the row:** the assignment gate applies to CREATION ONLY. A promoted row is reconciled on every poll even after the Asana assignee is removed (assignee columns clear to empty; title/parent/due/description/done-state keep syncing). An early `return` on no-assignee is how a promoted row froze forever.
- **Verified major — vanish-dismissal fails closed:** subtasks are not project members, so deleting one emits no board event — the authoritative `list_subtasks` listing is the only deletion signal. But `_paginate` can silently return a TRUNCATED list (mid-walk offset expiry, or the page cap) — only ever after at least one full page. So the dismissal diff runs ONLY when `len(subs) < _PAGE_SIZE` (a demonstrably complete listing); a full-page-or-more listing skips the diff for that parent this cycle (debug-logged). A raised exception skips it too, by construction. Parents with ≥ 100 subtasks therefore never run the diff — accepted, fail-closed by design (see §9).
- **Parent-dismissal cascade:** `_dismiss_by_gid` cascades to promoted children (`parent_task_ref = gid`) — a deleted parent takes its subtasks with it in Asana but their deletion emits no event of its own. Status change only, never a DELETE; `done` children keep their done state. `_handle_removed`'s verified-404 branch routes through the same `_dismiss_by_gid` so the cascade applies there too.
- **`parent_task_ref` is stripped in `handle_update_task`:** it is promotion lineage owned by `asana_monitor` — a model turn must not fake a subtask or orphan a promoted row. The monitor keeps writing it through `update_task` directly (which is why the field IS in `_UPDATABLE`).
- The checklist-mirror row of a vanished subtask is retained (never a DELETE) — it simply stops syncing. Same never-DELETE discipline as the task rows.

### 5.1 `migrations/056_subtask_promotion.sql` — NEW FILE, verbatim

```sql
-- ─────────────────────────────────────────────────────────────────────
-- Migration 056 — promote ASSIGNED Asana subtasks to first-class tasks.
--
-- Subtasks used to exist only as name+done checklist mirrors in
-- enablement_subtasks, so assigned subtask work never reached the calendar,
-- the "mine" scope or Renn. The monitor now promotes a subtask WITH an
-- assignee (any assignee — the "mine" display scope filters per-operator
-- downstream) into a real enablement_tasks row; unassigned subtasks stay
-- checklist-only, and the parent's checklist mirror is unchanged.
--
-- parent_task_ref stores the Asana gid of the subtask's PARENT task; NULL
-- for normal tasks. A row is a subtask iff parent_task_ref IS NOT NULL —
-- the single flag behind both calendars' "↳" marker, list_tasks'
-- parent_title join and Renn's is_subtask field. Board attribution of a
-- promoted row goes through task_board_links exactly like tasks (055 stays
-- the only authority; nothing is ever derived from the permalink).
--
-- Idempotent: single-line ALTER ADD COLUMN (the schema migrator's PRAGMA
-- table_info guard skips it when present) + CREATE INDEX IF NOT EXISTS.
-- The index serves the parent-title lookup (p.source='asana' AND
-- p.source_ref = t.parent_task_ref) list_tasks runs per listing, and the
-- source_ref equality probes the monitor already runs every poll.
-- ─────────────────────────────────────────────────────────────────────

ALTER TABLE enablement_tasks ADD COLUMN parent_task_ref TEXT;
CREATE INDEX IF NOT EXISTS idx_ent_source_ref ON enablement_tasks(source_ref);
```

No migrator changes needed: the schema migrator's PRAGMA table_info guard makes the single-line `ALTER TABLE ADD COLUMN` idempotent, and the index is `IF NOT EXISTS`.

### 5.2 `src/data/asana_client.py` — richer subtask read (GET-only, additive)

(a) **Replace the `_TASK_FIELDS` constant** (adds `parent.gid,parent.name`):

```python
# Fields fetched per task when polling a board for enablement sync. Includes the
# custom-field values (enum + people + raw number/text/date subtypes) the
# indicator/mapping/brief logic reads, plus html_notes (rich body — stored raw,
# never rendered as HTML), start_on, and the task assignee as a fallback.
_TASK_FIELDS = (
    "name,due_on,start_on,permalink_url,completed,modified_at,notes,html_notes,"
    "num_subtasks,parent.gid,parent.name,"
    "assignee.name,assignee.gid,assignee.email,created_by.name,"
    "custom_fields.gid,custom_fields.name,custom_fields.display_value,"
    "custom_fields.enum_value.gid,custom_fields.enum_value.name,"
    "custom_fields.people_value.gid,custom_fields.people_value.name,"
    "custom_fields.number_value,custom_fields.text_value,"
    "custom_fields.date_value.date"
)
```

(b) **Replace `list_subtasks`** (extends the returned shape ADDITIVELY — existing consumers keep reading gid/name/completed):

```python
    def list_subtasks(self, task_gid: str) -> list[dict]:
        """List an Asana task's subtasks for read-back + assigned-subtask
        promotion. The original gid/name/completed shape is extended
        ADDITIVELY (existing consumers keep reading those three keys)."""
        data = self._paginate(f"/tasks/{task_gid}/subtasks",
                              {"opt_fields": "name,completed,assignee.gid,"
                                             "assignee.name,due_on,modified_at,"
                                             "permalink_url,notes"})
        out = []
        for s in data:
            assignee = s.get("assignee") or {}
            out.append({
                "gid": s["gid"], "name": s.get("name", ""),
                "completed": bool(s.get("completed")),
                "assignee_gid": assignee.get("gid", ""),
                "assignee_name": assignee.get("name", ""),
                "due_on": s.get("due_on"),
                "modified_at": s.get("modified_at", ""),
                "permalink_url": s.get("permalink_url", ""),
                "notes": s.get("notes", ""),
            })
        return out
```

### 5.3 `src/data/asana_monitor.py` — promotion, reconciliation, cascade, fail-closed diff

(a) **In `_process_task`, thread the board source id into the initial subtask pull** — full post-change function:

```python
def _process_task(conn, client, board: dict, task: dict, results: dict) -> None:
    """Shared per-task pipeline: reconcile a tracked task, else create on an
    indicator match. Fills results['created'] / results['updated']."""
    cfg = board.get("config") or {}
    indicators = cfg.get("indicators") or []
    mappings = cfg.get("mappings") or {}
    # Two-way read-back: if we already track this task, pull Asana-side
    # changes (due / completion / assignee / subtasks) into the local task
    # and stop — never re-create what we already have.
    tid = _reconcile_existing_task(conn, client, task, board=board)
    if tid:
        results["updated"].append(tid)
        return
    if task.get("completed"):
        return
    if not _matches_indicators(task, indicators):
        return
    tid = _create_task_from_asana(conn, board, task, mappings)
    if tid:
        results["created"].append(tid)
        try:
            _pull_subtasks(conn, client, tid, task.get("gid"),
                           board_source_id=board.get("source_id"))
        except Exception as exc:  # noqa: BLE001 — best-effort initial subtask pull
            logger.debug("initial subtask pull failed for %s: %s", task.get("gid"), exc)
```

(b) **Replace `_dismiss_by_gid`** (idempotence check + the children cascade):

```python
def _dismiss_by_gid(conn, gid: str) -> None:
    """A task deleted in Asana → dismiss the local row (kept resolvable —
    dismissed ≠ deleted, per D1/D7).

    CASCADES to promoted subtask rows (``parent_task_ref = gid``): a deleted
    parent takes its subtasks with it in Asana, but subtasks are not project
    members so their deletion emits no event of its own — this is the only
    place their orphaning is observable. Status change only, never a DELETE;
    ``done`` children keep their done state (completed work stays recorded)."""
    from src.data import enablement_tasks as etasks
    row = conn.execute(
        "SELECT task_id, status FROM enablement_tasks "
        "WHERE source='asana' AND source_ref=?",
        (gid,),
    ).fetchone()
    if not row:
        return
    if row[1] != "dismissed":
        etasks.update_task(conn, row[0], status="dismissed")
    kids = conn.execute(
        "SELECT task_id FROM enablement_tasks WHERE source='asana' "
        "AND parent_task_ref=? AND status NOT IN ('done','dismissed')",
        (gid,),
    ).fetchall()
    for kid in kids:
        etasks.update_task(conn, kid[0], status="dismissed")
```

(c) **In `_handle_removed`, route the verified-404 branch through `_dismiss_by_gid`** — full post-change function:

```python
def _handle_removed(conn, client, board: dict, gid: str) -> None:
    """'removed' fires on multi-home/re-organize too, not just deletion —
    verify with a follow-up get_task and only dismiss when it 404s; a live
    task gets a scratchpad note instead."""
    from src.data import enablement_tasks as etasks
    row = conn.execute(
        "SELECT task_id, scratchpad FROM enablement_tasks "
        "WHERE source='asana' AND source_ref=?",
        (gid,),
    ).fetchone()
    if not row:
        return
    try:
        client.get_task(gid, opt_fields="name,completed")
    except Exception as exc:  # noqa: BLE001
        if _is_http_404(exc):
            # Verified deleted — same dismissal path as a 'deleted' event,
            # including the promoted-children cascade.
            _dismiss_by_gid(conn, gid)
        return
    name = (board.get("display_name") or board.get("source_id") or "board")
    note = f"[monitor] No longer on {name} (moved in Asana)."
    scratch = row[1] or ""
    if note not in scratch:
        etasks.set_scratchpad(conn, row[0], (scratch + "\n" + note).strip())
```

(d) **In `_reconcile_existing_task`, thread the board source id into the read-back pull** — the only change is the `_pull_subtasks(...)` call near the end; full post-change function for anchor certainty:

```python
def _reconcile_existing_task(conn, client, task: dict, board: dict | None = None):
    """Pull Asana-side changes into the local task we already track for this gid.

    Updates due date / completion / assignee and mirrors subtasks. Returns the
    local task_id when a tracked task was found (so the caller won't try to
    re-create it), else None.

    CO-OWNERSHIP IS RECORDED HERE. The row is matched on ``source_ref`` alone —
    which is the point: an Asana task multi-homed into two mapped projects is
    polled by both boards, one creates the row and the other lands here. That
    second board demonstrably tracks the task (it advances its own cursor and
    events token over it), so it takes a ``task_board_links`` row too. This is
    exactly where the previous single-column model observed co-ownership and
    then threw it away, leaving "remove board A" free to delete board B's live
    work. ``board`` is optional only so the legacy 3-arg call shape still works;
    the poll always passes it.
    """
    from src.data import enablement_tasks as etasks
    gid = task.get("gid")
    if not gid:
        return None
    row = conn.execute(
        "SELECT task_id FROM enablement_tasks WHERE source='asana' AND source_ref=?",
        (gid,),
    ).fetchone()
    if not row:
        return None
    tid = row[0]
    if board is not None:
        try:
            etasks.link_task_board(conn, tid, board.get("source_id"))
        except Exception as exc:  # noqa: BLE001 — a link failure must not kill the poll
            logger.warning("board link failed for %s/%s: %s",
                           board.get("source_id"), tid, exc)
    fields: dict = {}
    if task.get("modified_at"):
        # Check-and-set anchor for the WS1-M5/M6 write-back tier.
        fields["remote_modified_at"] = task["modified_at"]
    if "due_on" in task:
        fields["due_date"] = task.get("due_on")        # date, or None to clear
    if task.get("completed"):
        # In-flight guard (WS1-M5): while a panel complete-flip is mid-PUT,
        # Asana's stale completed-state must not revert the user's click.
        from src.data.asana_writeback import is_status_inflight
        if not is_status_inflight(tid):
            fields["status"] = "done"
    assignee = task.get("assignee") or {}
    asg = assignee.get("name")
    if asg:
        fields["assignee"] = asg
    asg_gid = assignee.get("gid")
    if asg_gid:
        fields["assignee_gid"] = asg_gid
    if task.get("notes") is not None:
        fields["description"] = task.get("notes")   # Asana is source-of-truth for the body
    creator = (task.get("created_by") or {}).get("name")
    if creator:
        fields["submitter"] = creator
    if fields:
        etasks.update_task(conn, tid, **fields)
    try:
        _pull_subtasks(conn, client, tid, gid,
                       board_source_id=(board or {}).get("source_id"))
    except Exception as exc:  # noqa: BLE001 — subtask read-back is best-effort
        logger.debug("subtask read-back failed for %s: %s", gid, exc)
    return tid
```

(e) **Replace `_pull_subtasks`** (new `board_source_id=None` kwarg; per-subtask promotion call; the fail-closed deletion diff at the end):

```python
def _pull_subtasks(conn, client, task_id, task_gid, board_source_id=None) -> None:
    """Mirror Asana subtasks locally: add ones we don't have yet and sync the
    done-state of ones we do, matched by asana_subtask_gid (so our own
    write-backs are recognised and never duplicated).

    ASSIGNED subtasks are additionally promoted into real enablement_tasks
    rows (see :func:`_promote_subtask`) so they reach the calendar, the "mine"
    scope and Renn. The checklist mirror above is unchanged — the parent's
    "x / y" counts still cover every subtask. One list_subtasks call per
    parent per pass is the whole API budget.

    DELETION DIFF. Subtasks are not project members, so deleting one emits no
    board event — this authoritative list is the only deletion signal. Any
    previously-known gid (the checklist mirror's ``asana_subtask_gid`` set)
    that has vanished from the list gets its PROMOTED row dismissed via
    :func:`_dismiss_by_gid` (status change, never a DELETE). FAILS CLOSED:
    ``list_subtasks`` → ``_paginate`` can silently return a TRUNCATED list
    (mid-walk offset expiry, or the page cap) — but only after at least one
    full page, so a result of one-full-page-or-more is not demonstrably
    complete and the dismissal pass is skipped for that parent this cycle
    (a raised exception skips it too, by construction — we never get here).
    Decision: the checklist-mirror row itself is retained, same never-DELETE
    discipline as the task row — it simply stops syncing.
    """
    from src.data import enablement_tasks as etasks
    from src.data.connection_factory import atomic
    subs = client.list_subtasks(task_gid) or []
    existing = {r[0]: r[1] for r in conn.execute(
        "SELECT asana_subtask_gid, subtask_id FROM enablement_subtasks "
        "WHERE task_id=? AND asana_subtask_gid IS NOT NULL AND asana_subtask_gid != ''",
        (task_id,),
    ).fetchall()}
    for s in subs:
        gid = s.get("gid")
        if not gid:
            continue
        if gid in existing:
            etasks.toggle_subtask(conn, existing[gid], s["completed"])
        else:
            sid = etasks.add_subtask(conn, task_id, s["name"],
                                     created_by="asana", done=s["completed"])
            with atomic(conn):
                conn.execute(
                    "UPDATE enablement_subtasks SET asana_subtask_gid=? WHERE subtask_id=?",
                    (gid, sid),
                )
        try:
            _promote_subtask(conn, s, parent_gid=task_gid,
                             board_source_id=board_source_id)
        except Exception as exc:  # noqa: BLE001 — promotion must not kill the mirror
            logger.debug("subtask promotion failed for %s: %s", gid, exc)
    try:
        from src.data.asana_client import _PAGE_SIZE as _page_size
    except Exception:  # noqa: BLE001 — completeness gate must never raise
        _page_size = 100
    if len(subs) < _page_size:
        returned = {s.get("gid") for s in subs if s.get("gid")}
        for gone in set(existing) - returned:
            _dismiss_by_gid(conn, gone)
    elif set(existing) - {s.get("gid") for s in subs}:
        logger.debug(
            "subtask listing for %s spans a full page (%d items) — possibly "
            "truncated; skipping the deletion diff this cycle", task_gid, len(subs))
```

(f) **Immediately after `_pull_subtasks` and before `_matches_indicators`, insert the new `_promote_subtask`:**

```python
def _promote_subtask(conn, sub: dict, *, parent_gid, board_source_id) -> None:
    """Promote an ASSIGNED Asana subtask into a real enablement_tasks row.

    Admission is assignment (any assignee — the existing "mine" display scope
    filters per-operator downstream); unassigned subtasks stay checklist-only.
    The assignment gate applies to CREATION ONLY: a row that already exists is
    reconciled on every poll even when the Asana assignee has since been
    removed (the un-assignment clears the assignee columns to empty while
    title / parent / due / description / done-state keep syncing — an early
    return here is how a promoted row froze forever).
    Matched on ``source_ref`` exactly like tasks, so a re-poll reconciles the
    row in place (title / due / assignee / done-state) instead of duplicating
    it, and a completed subtask is never created after the fact. Board
    attribution goes through ``link_task_board`` — ``task_board_links`` stays
    the only authority (migration 055); nothing is derived from the permalink.
    """
    from src.data import enablement_tasks as etasks
    gid = sub.get("gid")
    if not gid:
        return
    assignee_gid = sub.get("assignee_gid") or ""
    row = conn.execute(
        "SELECT task_id FROM enablement_tasks WHERE source='asana' AND source_ref=?",
        (gid,),
    ).fetchone()
    if row:
        tid = row[0]
        fields: dict = {
            "title": sub.get("name") or "Asana subtask",
            "parent_task_ref": parent_gid,
            "assignee_gid": assignee_gid,
        }
        if not assignee_gid:
            fields["assignee"] = ""            # un-assigned in Asana → clear
        elif sub.get("assignee_name"):
            fields["assignee"] = sub["assignee_name"]
        if "due_on" in sub:
            fields["due_date"] = sub.get("due_on")
        if sub.get("notes") is not None:
            fields["description"] = sub.get("notes")  # Asana owns the body
        if sub.get("modified_at"):
            fields["remote_modified_at"] = sub["modified_at"]
        if sub.get("completed"):
            from src.data.asana_writeback import is_status_inflight
            if not is_status_inflight(tid):
                fields["status"] = "done"
        etasks.update_task(conn, tid, **fields)
    else:
        if not assignee_gid:
            return
        if sub.get("completed"):
            return
        tid = etasks.create_task(
            conn,
            source="asana",
            kind="request",
            title=sub.get("name") or "Asana subtask",
            source_ref=gid,
            source_url=sub.get("permalink_url"),
            description=sub.get("notes"),
            due_date=sub.get("due_on"),
            created_by="agent",
            board_source_id=board_source_id,
            parent_task_ref=parent_gid,
        )
        upd: dict = {"assignee_gid": assignee_gid}
        if sub.get("assignee_name"):
            upd["assignee"] = sub["assignee_name"]
        if sub.get("modified_at"):
            upd["remote_modified_at"] = sub["modified_at"]
        etasks.update_task(conn, tid, **upd)
    try:
        etasks.link_task_board(conn, tid, board_source_id)
    except Exception as exc:  # noqa: BLE001 — a link failure must not kill the poll
        logger.warning("board link failed for %s/%s: %s", board_source_id, tid, exc)
```

### 5.4 `src/data/enablement_tasks.py` — column, whitelist, parent-title join

(a) **Replace `_UPDATABLE`** (adds `parent_task_ref` — guardrail 6 explains why it must be here):

```python
# Columns update_task() is allowed to set (guards against SQL injection via **fields).
_UPDATABLE = {
    "status", "priority", "due_date", "summary", "title", "description",
    "assignee", "assignee_gid", "submitter", "scratchpad", "draft_id",
    "source_url", "kind", "remote_modified_at", "parent_task_ref",
    "brief_json", "brief_status", "brief_source_modified_at",
}
```

(b) **Replace `create_task`** (new `parent_task_ref` kwarg threaded into the INSERT):

```python
def create_task(
    conn: sqlite3.Connection,
    *,
    source: str,
    kind: str,
    title: str,
    source_ref: str | None = None,
    source_url: str | None = None,
    summary: str | None = None,
    description: str | None = None,
    submitter: str | None = None,
    due_date: str | None = None,
    priority: str = "normal",
    status: str = "open",
    draft_id: int | None = None,
    llm_rationale: str | None = None,
    created_by: str = "agent",
    key: str | None = None,
    board_source_id: str | None = None,
    parent_task_ref: str | None = None,
) -> str:
    """Create a task (idempotent on dedup_key). Returns the task_id.

    If a task with the same dedup_key already exists, its id is returned and no
    new row is created — so a monitor can call this every poll cycle safely.

    ``parent_task_ref`` is the Asana gid of the PARENT task when this row is a
    promoted subtask (migration 056); NULL for normal tasks. A row is a subtask
    iff parent_task_ref IS NOT NULL.

    ``board_source_id`` is IMMUTABLE PROVENANCE — the monitor_sources.source_id
    of the board whose poll created this row (migration 054). It is stored,
    never derived, and deliberately absent from ``_UPDATABLE`` so no later edit,
    tool call or reconcile can rewrite it.

    It is NOT the attribution authority. Ownership is many-to-many (a
    multi-homed Asana task is polled by every board it sits in) and lives in
    ``task_board_links`` (migration 055) — see :func:`link_task_board`. Nothing
    reads this column to decide what a board owns, counts, shows or deletes.
    """
    key = key or dedup_key(source, source_ref, title)
    tid = _uid()
    now = _now()
    with atomic(conn):
        conn.execute(
            """INSERT INTO enablement_tasks
               (task_id, source, source_ref, source_url, kind, title, summary,
                description, submitter, due_date, priority, status, draft_id,
                llm_rationale, dedup_key, created_by, created_at, updated_at,
                board_source_id, parent_task_ref)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(dedup_key) DO NOTHING""",
            (tid, source, source_ref, source_url, kind, title, summary,
             description, submitter, due_date, priority, status, draft_id,
             llm_rationale, key, created_by, now, now, board_source_id,
             parent_task_ref),
        )
    row = conn.execute(
        "SELECT task_id FROM enablement_tasks WHERE dedup_key = ?", (key,)
    ).fetchone()
    return row[0] if row else tid
```

(c) **Replace `list_tasks`** (every filter gains a `t.` prefix because the query becomes a self-LEFT-JOIN carrying `parent_title`):

```python
def list_tasks(
    conn: sqlite3.Connection,
    *,
    source: str | None = None,
    status: str | None = None,
    kind: str | None = None,
    due_before: str | None = None,
    assignee_gid: str | None = None,
    assignee: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """List tasks, newest first, with optional filters. Subtask counts included.

    ``assignee_gid``/``assignee`` implement the "only mine" scope: a task matches
    on the Asana GID (unambiguous) or, for legacy/manual rows with no GID, a
    case-insensitive exact match of the display name/email. Pass neither for
    "all" (the show-all toggle) — never string-interpolate, always parameterized.

    Promoted-subtask rows (migration 056) additionally carry ``parent_title`` —
    the tracked parent row's title, joined on the parent's Asana gid; NULL for
    normal tasks and for subtasks whose parent is not tracked.
    """
    where, params = [], []
    if source:
        where.append("t.source = ?"); params.append(source)
    if status:
        where.append("t.status = ?"); params.append(status)
    if kind:
        where.append("t.kind = ?"); params.append(kind)
    if due_before:
        where.append("t.due_date IS NOT NULL AND t.due_date <= ?"); params.append(due_before)
    if assignee_gid and assignee:
        where.append("(t.assignee_gid = ? OR (t.assignee_gid IS NULL AND LOWER(t.assignee) = LOWER(?)))")
        params.append(assignee_gid); params.append(assignee)
    elif assignee_gid:
        where.append("t.assignee_gid = ?"); params.append(assignee_gid)
    elif assignee:
        where.append("LOWER(t.assignee) = LOWER(?)"); params.append(assignee)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    params.append(limit)
    rows = conn.execute(
        f"SELECT t.*, p.title AS parent_title FROM enablement_tasks t "
        f"LEFT JOIN enablement_tasks p "
        f"ON p.source = 'asana' AND p.source_ref = t.parent_task_ref"
        f"{clause} ORDER BY t.created_at DESC LIMIT ?", params
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        counts = conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(done),0) AS done "
            "FROM enablement_subtasks WHERE task_id = ?", (d["task_id"],)
        ).fetchone()
        d["subtask_total"] = counts["total"]
        d["subtask_done"] = counts["done"]
        out.append(d)
    return out
```

### 5.5 `src/data/chat_tools/enablement_tools.py` — Renn's tool lane

(a) **In `_list_tasks_impl`, after the `tasks.list_tasks(...)` call and before the return**, add the `is_subtask` flag — the post-change tail of the function:

```python
    rows = tasks.list_tasks(conn, source=source, status=status, kind=kind,
                            due_before=due_before, limit=int(limit or 50))
    for r in rows:
        r["is_subtask"] = r.get("parent_task_ref") is not None
    return {"tasks": rows, "count": len(rows)}
```

(b) **Replace `handle_update_task`** (the strip):

```python
def handle_update_task(conn, args, filters):
    # parent_task_ref is promotion lineage owned by asana_monitor — stripped
    # here so a model turn can't fake a subtask or orphan a promoted row (the
    # monitor keeps writing it through update_task directly).
    fields = {k: v for k, v in args.items()
              if k not in ("task_id", "parent_task_ref")}
    return _update_task_impl(conn, args.get("task_id"), fields)
```

### 5.6 `src/data/startup_greeting.py` — morning-greeting bullet

**Replace `_bullet`:**

```python
def _bullet(t: dict) -> str:
    due = (t.get("due_date") or "")[:10] or "no date"
    prio = (t.get("priority") or "normal").lower()
    src = t.get("source") or "task"
    line = f"- {_clean(t.get('title'))} — due {due} [{prio}] ({src})"
    if t.get("parent_task_ref"):
        parent = _clean(t.get("parent_title") or "")
        line += f" (subtask of {parent})" if parent else " (subtask)"
    return line
```

### 5.7 `src/llm/claude_tools.py` — tool description (API lane)

**In `TOOL_DEFINITIONS`, replace the `list_tasks` entry's `description`** — post-change entry:

```python
    {
        "name": "list_tasks",
        "description": (
            "List enablement tasks, optionally filtered by status/source/kind/due "
            "date. Assigned Asana subtasks are included as first-class rows, "
            "flagged is_subtask with parent_task_ref/parent_title."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "source": {"type": "string"},
                "kind": {"type": "string"},
                "due_before": {"type": "string", "description": "ISO date."},
                "limit": {"type": "integer"},
            },
        },
    },
```

### 5.8 `src/mcp/chat_mcp_server.py` — tool description (MCP lane)

**In `TOOL_SCHEMAS`, replace the `list_tasks` entry's `description`** — post-change entry:

```python
    {
        "name": "list_tasks",
        "description": (
            "List enablement tasks, optionally filtered by status/source/kind/due "
            "date. Assigned Asana subtasks are included as first-class rows, "
            "flagged is_subtask with parent_task_ref/parent_title. Pass task_id "
            "to get ONE task in detail — including its Asana custom fields, "
            "attachment names, and latest comments."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "source": {"type": "string"},
                "kind": {"type": "string"},
                "due_before": {"type": "string", "description": "ISO date."},
                "limit": {"type": "integer"},
                "task_id": {"type": "string",
                            "description": "Detail mode: return just this task, enriched."},
            },
        },
    },
```

### 5.9 `src/services/enablement_web.py` — web calendar viewmodel

**In `CalendarWebController.set_tasks`, add the two fields to the event dict** — full post-change method:

```python
    def set_tasks(self, tasks):
        """Ingest the host's task rows (same shape both feed paths produce)
        and push a fresh viewmodel to the web page."""
        events = []
        self._tasks_by_id = {}
        for t in tasks or []:
            if not isinstance(t, dict):
                continue
            event_id = self._event_id(t)
            if event_id is None:
                continue
            self._tasks_by_id[event_id] = dict(t)
            due = str(t.get("due_date") or "")
            if len(due) < 10:
                continue               # undated rows are openable, never drawn
            source = t.get("source")
            kind = source if source in _KINDS else "normal"
            events.append({
                "id": event_id,
                "title": str(t.get("title") or "Task"),
                "date": due[:10],
                "kind": kind,
                "status": str(t.get("status") or ""),
                "priority": str(t.get("priority") or "normal"),
                "assignee": str(t.get("assignee") or ""),
                "subs": str(t.get("subs") or ""),
                "description": str(t.get("description") or "")[:_DESCRIPTION_CAP],
                "is_card_due": t.get("kind") == "guru_card_due",
                "is_subtask": bool(t.get("is_subtask")),
                "parent_title": str(t.get("parent_title") or ""),
            })
        self._push({"events": events})
```

### 5.10 `src/ui/pages/enablement/calendar.py` — native calendar chip marker

**In `CalendarPage.set_tasks`, prefix subtask labels with `↳ `** — full post-change method:

```python
    def set_tasks(self, tasks: list[dict]):
        """Populate events from real tasks (keyed by their ISO due_date)."""
        self._live = True
        ev: dict = {}
        for t in tasks:
            due = t.get("due_date") or ""
            if len(due) >= 10:
                kind = t.get("source") if t.get("source") in TINT else "normal"
                label = (t.get("title") or "")[:20]
                if t.get("is_subtask"):
                    # Promoted Asana subtasks carry a ↳ marker (PlainText chip
                    # — the glyph is never interpreted as markup).
                    label = "↳ " + label
                ev.setdefault(due[:10], []).append((label, kind, dict(t)))
        self._events = ev
        self._rebuild_grid()
```

### 5.11 `src/ui/pages/enablement/page.py` — task-row feed

**In the page's task-row builder (the loop that produces the dicts both calendars consume — anchor: the dict containing `"board_source_ids"`), add the two fields** — post-change loop:

```python
        rows = []
        for t in task_list:
            subs = tasks.list_subtasks(conn, t["task_id"])
            rows.append({
                "task_id": t["task_id"],
                "status": t["status"] if t["status"] in ("open", "in_progress", "done") else "open",
                "title": t["title"],
                "source": t["source"] if t["source"] in ("drive", "guru", "asana") else "drive",
                "due": self._fmt_due(t.get("due_date")),
                "due_iso": (t.get("due_date") or "")[:10],
                "priority": t.get("priority") or "normal",
                "assignee": t.get("assignee") or "—",
                "subs": f"{t.get('subtask_done', 0)} / {t.get('subtask_total', 0)}",
                "subtasks": [(s["text"], bool(s["done"])) for s in subs],
                "scratch": t.get("scratchpad") or "",
                "due_date": t.get("due_date"),
                "description": t.get("description") or "",
                "submitter": t.get("submitter") or "",
                "source_url": t.get("source_url") or "",
                "source_ref": t.get("source_ref") or "",
                "board_source_ids": list(links.get(t["task_id"], ())),
                "is_subtask": t.get("parent_task_ref") is not None,
                "parent_title": t.get("parent_title") or "",
            })
        return rows
```

### 5.12 `web/src/calendar/CalendarApp.jsx` — web calendar marker, hover line, legend

(a) **Replace the `LEGEND` constant:**

```jsx
const LEGEND = [
  ["drive", "Drive"], ["guru", "Guru"], ["asana", "Asana"], ["high", "Due / high"],
  ["subtask", "Subtask"],
];
const WEEK = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];
```

(b) **In the `Chip` component, add the `↳` glyph span after the `is_card_due` glyph** — full post-change component:

```jsx
function Chip({ e, compact, onOpen, onHover, onLeave, draggable, onDragStart, onDragEnd }) {
  return (
    <button
      className={"cal-chip kind-" + e.kind + (e.status === "done" ? " done" : "")}
      onClick={() => onOpen(e.id)}
      onMouseEnter={(ev) => onHover(e, ev)}
      onMouseLeave={onLeave}
      draggable={draggable ? "true" : undefined}
      onDragStart={draggable ? (ev) => onDragStart(e, ev) : undefined}
      onDragEnd={draggable ? onDragEnd : undefined}
      title=""
    >
      {e.priority === "high" && <span className="cal-chip-dot" aria-hidden="true" />}
      {e.is_card_due && <span className="cal-chip-glyph" aria-hidden="true">↻</span>}
      {e.is_subtask && <span className="cal-chip-sub" aria-hidden="true">↳</span>}
      <span className={"cal-chip-title" + (compact ? " compact" : "")}>{e.title}</span>
    </button>
  );
}
```

(c) **In `HoverCard`, add the "Subtask of …" row after the assignee row** — full post-change component:

```jsx
function HoverCard({ e, brief, pos }) {
  const left = Math.max(8, Math.min(pos.x, (window.innerWidth || 1200) - 340));
  return (
    <div className="cal-hover-card" style={{ left, top: pos.y + 6 }}>
      <div className="cal-hc-title">{e.title}</div>
      <div className="cal-hc-meta">
        <span className={"cal-hc-kind kind-" + e.kind}>{e.kind}</span>
        {e.status && <span>{e.status.replace("_", " ")}</span>}
        {e.priority === "high" && <span className="cal-hc-high">high priority</span>}
        <span>{shortDate(e.date)}</span>
      </div>
      {e.assignee && <div className="cal-hc-row">Assignee: {e.assignee}</div>}
      {e.is_subtask && e.parent_title && (
        <div className="cal-hc-row">Subtask of {e.parent_title}</div>
      )}
      {e.subs && <div className="cal-hc-row">Subtasks: {e.subs}</div>}
      {e.description && <div className="cal-hc-desc">{e.description}</div>}
      <BriefBlock brief={brief} />
    </div>
  );
}
```

(d) **In the legend render inside the header, branch on the `subtask` kind** (it draws a glyph, not a colored dot) — post-change block:

```jsx
        <span className="cal-legend">
          {LEGEND.map(([kind, label]) => (
            <span key={kind} className="cal-legend-item">
              {kind === "subtask"
                ? <span className="cal-legend-glyph" aria-hidden="true">↳</span>
                : <span className={"cal-legend-dot kind-" + kind} aria-hidden="true" />}
              {label}
            </span>
          ))}
        </span>
```

### 5.13 `web/src/styles.css` — calendar additions

Two one-line rules. **After `.cal-legend-dot.kind-high`**, add:

```css
.cal-legend-glyph { font-size: 11px; color: var(--mid); }
```

**After `.cal-chip-glyph`**, add:

```css
.cal-chip-sub { flex: none; font-size: 10px; opacity: 0.85; }
```

Then rebuild the bundle (`npm --prefix web run build`).

**Wiring notes for the whole workstream:** there is no new poll, no new API call shape, and no settings key. The promotion runs inside the existing `_pull_subtasks` call sites (initial create + every reconcile). The migration applies on next launch via the standard schema migrator; the tests in §7.3 also prove idempotence (`test_migration_056_applied_and_rerun_is_noop`).

---

## 6. Workstream 4 — Zendesk copy-gate removal + `body_text`

**Feature contract (owner decision 2026-08-05).** Draft STATUS is workflow bookkeeping the specialist advances by hand — it never gates the clipboard. Any draft with a recorded review (a served SOURCE diff for its current bytes) releases at pending, ready, copied or pushed alike, always behind the native byte-showing confirm (drafts confirm everything). The review-record hash gate (`_review_covers`) and the confirm (`_confirm_copy_release`) are UNCHANGED and remain the protection. Separately: the SPA's primary "Copy content" button sends `shape.js`'s `COPY_DRAFTED_FIELD` (`"body_text"`) through the bridge — Python never served that field, so the primary copy was a silent no-op on every article and draft. `body_text` (the drafted-content markdown prose; the `_article_body_text` projection for mirror rows) is now a served copy field, text-flavour only, un-gated on mirror rows like `title` (a draft still confirms it, because a draft confirms everything).

**Review fixes baked in:**

- **Docstring precision (reviewer-caught overclaim):** the "character-for-character in the diff" guarantee is now SCOPED to the diffed flavours (title + `body_html`-derived strings). `body_text` is the markdown SOURCE of the same reviewed content and is NOT separately diffed — markdown constructs that render to nothing in HTML (e.g. an unused link reference definition) can be present in it without a visible diff row. Its release-time disclosure on a draft is the native byte-showing confirm. This wording lives in the module docstring, `js_request_diff`, `_record_review`, and `_resolve_copy`, and is mechanically pinned by `test_body_text_scope_limit_is_real_and_the_confirm_covers_it`.
- **Body-less legacy drafts:** `_copy_bundle` falls back to `_article_body_text(row)` (project the stored HTML back to markdown) when a draft has no stored `body`, the same way a mirror row seeds its editor — so `body_text` is never an empty payload on old rows.
- **`shape.js` keeps unknown statuses OFF:** `canCopyDraft` enumerates the four known statuses rather than returning `true` — an unknown/garbled status keeps the copy buttons disabled ("refresh before copying").
- Refuted-by-design review claim (do not re-flag on the target either): "un-gated mirror `body_text` copy as clipboard-overwrite primitive" — it is within the already-reviewed 10-second `_COPY_HOLD_S` threat model, same as mirror titles.

### 6.1 `src/services/zendesk_web.py`

Six edits. The heavy machinery (`_review_covers`, `_confirm_copy_release`, `_copy_confirm_text`, the freeze/hold/backoff logic) is untouched.

(a) **Module docstring — replace items 1 and 5 and the "Precision on shown" note.** The post-change docstring run from item 1 through the item-6 mirror-exemption sentence (replace the corresponding span of the existing docstring; everything before item 1 and after this run is unchanged):

```python
  1. **The authoritative review is a SOURCE diff of the exact bytes the
     clipboard will deliver** (``js_request_diff``): the HTML source text
     itself for articles, the canonical actions JSON for macros, diffed
     line/word-wise. Every attribute, style declaration, ``<script>`` body
     and hidden span is literally on screen. The readable
     ``html_to_review_text`` projection ships alongside as an explicitly
     SECONDARY convenience view and is never shown alone. (The
     ``body_text`` flavour is the markdown SOURCE of that same reviewed
     content and is NOT separately diffed — see the precision note below;
     its release-time disclosure on a draft is the native byte-showing
     confirm, item 6.)
  2. **Copy releases only reviewed bytes** (``_resolve_copy`` +
     ``_review_covers``): the payload is recomputed from the DB by
     ``_copy_bundle`` — the SAME function that built the reviewed material
     — and every exact string about to be handed to the clipboard (the
     text flavour AND the text/html mime flavour) must hash-match a string
     the recorded review showed, on top of a recompute-from-row content
     hash. This gate covers DRAFTS **and** mirror articles/macros: the
     mirror review is recorded by ``js_open_article`` /``js_open_macro``,
     whose payloads carry the exact source bytes (``body_source``). No
     record, any mismatch, or a row that changed under the review means NO
     clipboard write — fail closed everywhere.
  3. **Verbatim bytes are honest, not silently rewritten.** ``origin='pull'``
     mirror rows are byte-faithful to remote Zendesk by design, so when the
     bytes differ from what ``sanitize_html`` would produce the review and
     the copy status line say so explicitly (``_MARKUP_NOTICE``).
  4. **No silent zero-change**: if the source diff reports zero changed
     lines while the bytes differ from the baseline, that is a diff bug —
     the payload carries ``warning`` instead of an innocent "0 changed
     lines".
  5. Every draft-content mutator drops the recorded review, so a stale
     on-screen diff can never authorize a copy. Draft STATUS is workflow
     bookkeeping only: any reviewed draft — pending, ready, copied or
     pushed — releases under this same review record + native confirm
     (owner decision 2026-08-05). The pending→ready→copied lane tracks
     the specialist's progress by hand; it does not gate the clipboard.

  Precision on "shown": of the strings ``_record_review`` binds, the
  DIFFED plain flavours are rendered literally — the diff rows,
  ``body_source``, the title row. ``body_text`` is NOT one of them: the
  diff is computed from the ``body_html`` bytes + title, and ``body_text``
  is the markdown SOURCE of that same reviewed content (a draft's stored
  body; the ``_article_body_text`` projection for a mirror row, which
  ``js_open_article`` serves as the editor seed), so markdown constructs
  that render to nothing in HTML — a link reference definition, say — can
  be present in it without a visible diff row. The character-for-character
  guarantee is therefore scoped to the diffed flavours; for ``body_text``
  the release-time protection is that every DRAFT copy, ``body_text``
  included, passes the native byte-showing confirm
  (``_confirm_copy_release``, item 6), which displays the exact released
  bytes. The rich (``text/html`` mime) flavour is not independent
  content: it is ``sanitize_html`` of the string the reviewer WAS shown,
  and sanitize only removes and escapes — it can never introduce a tag,
  attribute or URL absent from the source. That derivation is asserted in
  tests, not assumed. (The rendered preview srcdoc is a THIRD, wider
  rendering — ``sanitize_html_preview``, see ``_article_srcdoc`` — and is
  never a clipboard flavour.)

  6. **EVERY COPY OF MARKUP TAKES A NATIVE CONFIRM THAT DISPLAYS THE
     BYTES.**

         No clipboard release of MARKUP — ``body_html``, ``body_rich`` or
         ``macro_reply``, on a MIRROR row or a draft — and no clipboard
         release of DRAFT content at all — article draft or macro draft,
         every field, both mime flavours — happens without a NATIVE confirm
         showing the EXACT characters about to be released, IN A VISIBLE
         SCROLLABLE VIEW, with the row's title and the field named.
         No ``confirm_fn`` injected, declined, or the row's bytes moved
         while the dialog was open ⇒ nothing is written to the clipboard and
         NO ``copy_resolved`` receipt is emitted. The rule lives in
         ``_copy_needs_confirm`` and is a pure function of (target kind,
         field name) — nothing the content can steer.

         Titles and macro names stay un-gated on MIRROR rows: they are not
         markup, the destination does not interpret them, and the review
         record already covers them. (A DRAFT still confirms them, because
         a draft confirms everything.)

```

(b) **Replace the `_COPY_FIELDS` table** (the two `body_text` additions):

```python
# Per-target copy-field allowlists: the TARGET kind (never a polymorphic id)
# decides the table, so a draft id can never silently address an article row.
_COPY_FIELDS = {
    "article": ("title", "body_html", "body_rich", "body_text"),
    "article_draft": ("title", "body_html", "body_rich", "body_text"),
    "macro": ("macro_name", "macro_reply"),
    "macro_draft": ("macro_name", "macro_reply"),
}
```

(c) **Replace the comment block above `_MARKUP_COPY_FIELDS`** (the constant itself and `_copy_needs_confirm` are unchanged — shown for anchor):

```python
# draft kind -> copy target, the key shape of the review ledger.
_DRAFT_TARGET = {"article": "article_draft", "macro": "macro_draft"}
# The copy targets that are DRAFT content — not yet in Zendesk, about to be
# pasted into a public site by hand. Every clipboard release from one of
# these takes the native confirm (module docstring, item 6).
_DRAFT_COPY_TARGETS = frozenset(_DRAFT_TARGET.values())
# THE COPY FIELDS THAT CARRY MARKUP, on every target kind — mirror rows
# included (module docstring, item 6). A static set, not a look-at-the-bytes
# test: "does this string contain a tag" is a content-derived branch, and a
# content-derived branch is something whoever controls the content can steer.
# ``macro_reply`` is here because a reply is routinely ``comment_value_html``
# and the live Zendesk comment editor INTERPRETS what is pasted into it.
# Titles, macro names and the drafted-content text (``body_text``, a prose
# flavour the destination does not interpret) are not markup and stay
# un-gated (a draft still confirms them, because a draft confirms
# everything).
_MARKUP_COPY_FIELDS = frozenset({"body_html", "body_rich", "macro_reply"})


def _copy_needs_confirm(target: str, field: str) -> bool:
    """Whether this clipboard release takes the native confirm.

    TRUE for every release of markup, and for every release from a DRAFT.
    Deliberately a pure function of (target kind, field name): no row state,
    no byte inspection, nothing the content can influence."""
    return target in _DRAFT_COPY_TARGETS or field in _MARKUP_COPY_FIELDS
```

(d) **In `js_open_article`: build the payloads with the body-text projection, and serve `body_text` from the payload** (so the editor seed and the copy flavour are the same bytes by construction) — full post-change method:

```python
    def js_open_article(self, article_id):
        """Open an article detail. Digits/'-'-digits only, and the row must
        exist in the mirror — anything else is a silent no-op.

        THIS IS THE MIRROR REVIEW SURFACE. The payload carries BOTH the
        ``body_srcdoc`` (the rendered preview — the PRIMARY surface, and
        the reason it uses the wider preview profile) and ``body_source``
        — the exact stored bytes the clipboard would deliver, which the SPA
        renders as escaped text in a collapsed disclosure — plus
        ``markup_notice`` when the preview genuinely cannot display part of
        those bytes. Serving it
        RECORDS the review for this article, which is what unlocks
        ``js_copy_field`` for it; before this, a mirror copy is refused
        exactly like an unreviewed draft. Previously the ONLY on-screen
        view of a mirror article was the sanitized preview while
        ``_resolve_copy`` released the raw row bytes, so imported
        script/form markup was invisible yet copyable."""
        s = str(article_id or "")
        if not _ID_RE.match(s):
            return
        conn = self._db()
        if conn is None:
            return
        from src.data import zendesk_store as store
        art = store.get_article(conn, int(s))
        if art is None:
            return
        aid = art["article_id"]
        section, category = self._section_names(conn, art.get("section_id"))
        revisions = []
        for r in store.list_revisions(conn, kind="article"):
            if r.get("target_id") == aid:
                revisions.append({
                    "draft_id": r["draft_id"], "status": r["status"],
                    "title": r.get("title") or "",
                    "source_ref": r.get("source_ref"),
                    "updated_display": _display_date(r.get("updated_at"))})
                self._served_drafts[("article", r["draft_id"])] = r["status"]
        self._served_article_ids.add(aid)
        self._open_article_id = aid
        payloads = self._html_payloads(art.get("title") or "",
                                       self._article_source(art),
                                       self._article_body_text(art))
        self._record_review("article", aid, payloads,
                            str(art.get("content_hash") or ""))
        self._emit(self.article_detail, {
            "id": aid, "title": art.get("title") or "",
            "section_id": art.get("section_id"), "section": section,
            "category": category, "labels": art.get("labels") or [],
            "author": art.get("author_name") or "—",
            "draft": bool(art.get("draft")),
            "outdated": bool(art.get("outdated")),
            "position": art.get("position"),
            "html_url": art.get("html_url") or "",
            "origin": art.get("origin") or "pull",
            "source_file": art.get("source_file"),
            "updated_display": _display_date(art.get("updated_at")),
            "body_srcdoc": self._article_srcdoc(art),
            # The EXACT stored bytes — what "Copy HTML" delivers. Rendered
            # as escaped text by the SPA (never as markup), so the reviewer
            # reads the same characters the clipboard will carry.
            "body_source": payloads["body_html"]["text"],
            # Plain/markdown seed for the specialist edit textarea
            # (ArticleEditor -> BodyEditForm) AND the bytes the primary
            # "Copy content" (body_text) copy delivers. Without it the
            # editor opened BLANK and a save silently replaced the whole
            # body with only what was typed.
            "body_text": payloads["body_text"]["text"],
            "markup_notice": payloads["body_html"]["notice"],
            # WHAT diverges, item by item (dropped containers, refused or
            # merely unexpected URL schemes, and every hiding construct the
            # preview neutralized). The notice says "something"; this says
            # what, so the reviewer knows where to look in the source.
            "markup_report": payloads["body_html"].get("report") or [],
            "revisions": revisions,
        })
```

(e) **Replace the `js_request_diff` docstring** (body unchanged) — the post-change docstring plus the opening lines of the body for anchor certainty:

```python
    def js_request_diff(self, kind, draft_id):
        """THE AUTHORITATIVE REVIEW SURFACE for a draft.

        ``rows`` is a diff of the exact SOURCE bytes the clipboard will
        deliver — the HTML source text for articles, the canonical actions
        JSON (``_actions_source``) for macros — so every attribute, style
        declaration, ``<script>`` body, duplicate attribute and zero-size
        span is literally on screen. A readable projection
        (``html_to_review_text`` / ``_actions_plain``, both sides
        like-for-like) ships as ``text_rows``, explicitly SECONDARY: it
        drops things by construction and must never be the only thing a
        reviewer sees before a copy.

        Emitting the diff RECORDS the review: hashes of every exact string
        ``_copy_bundle`` can release for this draft. ``_resolve_copy``
        releases nothing whose hash is absent. For the DIFFED flavours —
        the title and the ``body_html``-derived strings this surface
        renders — that means nothing can be present in the copied bytes
        without having been present, character for character, in this
        diff. The ``body_text`` flavour is NOT separately diffed: it is
        the markdown SOURCE of the same reviewed content (the draft's
        stored body), and markdown constructs that render to nothing in
        HTML — a link reference definition, say — can be present in it
        without a visible diff row. Its release-time disclosure is the
        native byte-showing confirm (``_confirm_copy_release``), which
        displays the exact released bytes for EVERY draft copy,
        ``body_text`` included (module docstring, item 6).

        ``warning`` is set when the source diff reports no changed line
        although the bytes differ from the baseline — a diff bug, surfaced
        instead of an innocent "0 changed lines"."""
        kind = str(kind or "")
        if kind not in _DRAFT_KINDS:
            return
        did = self._served_draft_id(kind, draft_id)
        if did is None:
            return
        conn = self._db()
        if conn is None:
            return
        from src.data import zendesk_store as store
        from src.data.text_diff import change_count, diff_words
        target = _DRAFT_TARGET[kind]
        bundle = self._copy_bundle(conn, target, did)
        if bundle is None:
            return
```

(f) **Replace the `js_copy_field` docstring** (body unchanged) — post-change docstring plus the body for anchor certainty:

```python
    def js_copy_field(self, target, target_id, field):
        """Copy exact: re-reads the EXACT DB bytes at click time (never the
        page's copy of the text) and hands them to the Python-side clipboard.

        Every release of MARKUP (``body_html`` / ``body_rich`` /
        ``macro_reply``, mirror rows included) and every release from a
        DRAFT takes the native confirm that displays the exact bytes
        (``_resolve_copy`` → ``_confirm_copy_release``); if it is absent or
        declined, ``_resolve_copy`` returns None and NOTHING is emitted —
        no clipboard write and no ``copy_resolved`` receipt. Only the
        plain-text mirror fields — titles, macro names and the
        drafted-content ``body_text`` prose — copy under the review record
        alone. Draft STATUS never gates a copy: any reviewed draft
        releases behind the confirm, at pending, ready, copied or pushed
        alike (owner decision 2026-08-05) — the statuses track the
        specialist's hand-paste workflow, nothing more.

        Every successful copy is ALSO announced on the native status line
        via the compat set_status, a surface the page cannot forge —
        including the ``_MARKUP_NOTICE`` when the released bytes carry
        markup the preview cannot display faithfully.

        THE GATE GOVERNS THE CLIPBOARD, NOT ONLY THE RELEASE (F2). Two
        belts, because "the human approved these bytes" is worthless if
        different bytes are on the clipboard by the time they paste:

        * this slot is FROZEN like every other mutating slot, so a page
          script cannot write the clipboard from inside the nested event
          loop of an open native modal (the observed exploit fired an
          un-gated MIRROR copy while the draft dialog was still up);
        * an approved GATED release arms ``_COPY_HOLD_S`` during which no
          copy that carries no gate of its own — since 2026-07-27 that is
          only a plain-text mirror field (title / macro name / body_text),
          which the page self-serves through js_open_article — may
          overwrite it. A gated copy may, and pays for its own confirm."""
        if self._frozen():
            # a native modal or a pull/import owns this moment
            self.set_status(_COPY_REFUSED)
            return
        target = str(target or "")
        fields = _COPY_FIELDS.get(target)
        if fields is None or str(field or "") not in fields:
            return
        field = str(field)
        tid = self._copy_target_id(target, target_id)
        if tid is None:
            return
        gated = _copy_needs_confirm(target, field)
        if not gated and self._now() < self._copy_hold_until:
            # the operator's approved bytes are still the clipboard's
            self.set_status(_COPY_REFUSED)
            return
        conn = self._db()
        if conn is None:
            return
        resolved = self._resolve_copy(conn, target, tid, field)
        if resolved is None:
            return
        text, html, sanitized, notice = resolved
        ok = False
        if self._clipboard_fn is not None:
            try:
                ok = bool(self._clipboard_fn(text, html))
            except Exception:  # noqa: BLE001
                ok = False
        if ok and gated:
            self._copy_hold_until = self._now() + _COPY_HOLD_S
        self._req_seq += 1
        self._emit(self.copy_resolved, {
            "request_id": f"c-{self._req_seq}", "target": target,
            "target_id": tid, "field": field, "ok": ok,
            "chars": len(text), "sanitized": sanitized, "notice": notice})
        if ok:
            note = (" (rich copy sanitized: script/iframe content removed)"
                    if sanitized else "")
            warn = f" - WARNING: {notice}" if notice else ""
            self.set_status(
                f"Copied {target} {tid} {field} - "
                f"{len(text):,} chars{note}{warn}")
```

(g) **Replace `_html_payloads`** (new third parameter + the `body_text` payload):

```python
    @staticmethod
    def _html_payloads(title: str, raw: str, body_text: str = "") -> dict:
        """Releasable payloads for an HTML-bodied row (mirror article or
        article draft), keyed by copy field.

        Each payload is ``{text, html|None, sanitized, notice}``: ``text``
        is the plain flavour handed to the clipboard, ``html`` the
        text/html mime flavour (None when the field pastes as text).
        ``body_rich`` always passes the STRICT ``sanitize_html`` —
        markdown_to_html and the byte-faithful pull both forward markup we
        do not vouch for, so the mime flavour is defanged before it can
        reach the live Zendesk editor as formatted paste. The clipboard
        never sees the wider preview profile.

        ``body_text`` is the drafted-content prose — the SPA's primary
        "Copy content" flavour: the stored markdown for a draft, the
        ``_article_body_text`` projection for a mirror row. Prose, not
        markup, so like ``title`` it copies text-only.

        ``notice``/``report`` are measured against the PREVIEW profile
        instead: they fire when the sandboxed preview and the bytes are not
        the same picture — something could not be displayed (a dropped
        script/style/form container, a stripped handler, an unexpected URL
        scheme) OR something the source hides is displayed anyway (the
        preview neutralizes every hiding construct it can recognise). Not
        merely because the strict profile would have thrown away a class
        attribute. ``report`` names each item so the surface can say WHAT
        was hidden, not only that something was."""
        from src.data.html_sanitize import sanitize_html, sanitize_html_preview
        raw = "" if raw is None else str(raw)
        safe = sanitize_html(raw)
        _preview, report = sanitize_html_preview(raw, report=True)
        notice = _MARKUP_NOTICE if report else ""
        return {
            "title": {"text": title or "", "html": None,
                      "sanitized": False, "notice": "", "report": []},
            # Plain-text HTML source: byte-verbatim (pastes as text).
            "body_html": {"text": raw, "html": None, "sanitized": False,
                          "notice": notice, "report": list(report)},
            "body_rich": {"text": raw, "html": safe,
                          "sanitized": safe != raw, "notice": notice,
                          "report": list(report)},
            # Drafted-content prose: text-only, like the title.
            "body_text": {"text": body_text or "", "html": None,
                          "sanitized": False, "notice": "", "report": []},
        }
```

(h) **Replace `_copy_bundle`** (threads body text for articles and drafts; macro branches unchanged):

```python
    def _copy_bundle(self, conn, target, tid):
        """``(payloads, content_hash, row)`` for a copy target, or None when
        the row is gone.

        THE SINGLE SOURCE OF TRUTH for "what could this target put on the
        clipboard". The review recorders and ``_resolve_copy`` both call it,
        which is what makes reviewed bytes and copied bytes the same bytes
        by construction rather than by two code paths agreeing."""
        from src.data import zendesk_store as store
        if target == "article":
            row = store.get_article(conn, tid)
            if row is None:
                return None
            return (self._html_payloads(row.get("title") or "",
                                        self._article_source(row),
                                        self._article_body_text(row)),
                    str(row.get("content_hash") or ""), row)
        if target == "article_draft":
            row = store.get_article_draft(conn, tid)
            if row is None:
                return None
            html = row.get("body_html")
            if not html:
                # Renn drafts store markdown; render deterministically.
                from src.data.html_markdown import markdown_to_html
                html = markdown_to_html(row.get("body") or "")
            body_text = str(row.get("body") or "")
            if not body_text:
                # Body-less legacy drafts: project the stored HTML back to
                # markdown, the same way a mirror row seeds its editor.
                body_text = self._article_body_text(row)
            return (self._html_payloads(row.get("title") or "", str(html),
                                        body_text),
                    store.draft_content_hash("article", row), row)
        if target == "macro":
            row = store.get_macro(conn, tid)
            if row is None:
                return None
            return (self._macro_payloads(row.get("name") or "",
                                         row.get("actions")),
                    str(row.get("content_hash") or ""), row)
        row = store.get_macro_draft(conn, tid)
        if row is None:
            return None
        return (self._macro_payloads(row.get("name") or "",
                                     row.get("actions")),
                store.draft_content_hash("macro", row), row)
```

(i) **Replace `_record_review`** (docstring only — the hash-binding body is unchanged):

```python
    def _record_review(self, target, tid, payloads, content_hash):
        """Bind the exact strings of the material just served for review
        to (target, tid). Only these hashes can later leave through the
        clipboard.

        Not every bound string is literally on the review surface: the
        diffed flavours (title, ``body_html``-derived) are rendered
        character for character, while ``body_text`` is the markdown
        SOURCE of the same content — served as the editor seed /
        ``body_text`` detail field, never diffed. Every DRAFT copy of it
        still passes the native byte-showing confirm
        (``_confirm_copy_release``), which is the release-time disclosure
        (module docstring, item 6 + the precision note)."""
        blobs = set()
        for p in (payloads or {}).values():
            blobs.add(_sha(p.get("text")))
            if p.get("html") is not None:
                blobs.add(_sha(p["html"]))
        self._reviewed[(target, tid)] = {"content": content_hash,
                                         "bytes": frozenset(blobs)}
```

(j) **Replace `_resolve_copy` — THIS is the gate removal.** The pre-change body contained:

```python
        payloads, content_hash, row = bundle
        ...
        if target in _DRAFT_COPY_TARGETS:
            if row.get("status") not in ("ready", "copied"):
                return None
```

Those three status lines are DELETED (and `row` becomes `_row`, unused). Full post-change method:

```python
    def _resolve_copy(self, conn, target, tid, field):
        """(text, html|None, sanitized, notice) for a validated copy
        request, or None for a silent refusal.

        Draft STATUS is not consulted (owner decision 2026-08-05): a draft
        releases at pending, ready, copied or pushed alike. The protection
        is the review record + the native confirm, and status is workflow
        bookkeeping the specialist advances by hand. Every target, mirror
        rows included, goes through ``_review_covers``: no recorded review
        means no clipboard write. That gate stays as defence in depth even
        though it is no longer the disclosure.

        And every release ``_copy_needs_confirm`` names — all markup, on
        mirror rows and drafts alike, plus every draft field whatever it
        carries — then takes the one thing the page cannot fake: a NATIVE
        confirm displaying those exact bytes (module docstring, item 6)."""
        bundle = self._copy_bundle(conn, target, tid)
        if bundle is None:
            return None
        payloads, content_hash, _row = bundle
        payload = payloads.get(field)
        if payload is None:
            return None                 # e.g. a macro with no reply action
        if not self._review_covers(target, tid, payload, content_hash):
            return None
        if _copy_needs_confirm(target, field):
            row_title = (payloads.get("title") or payloads.get("macro_name")
                         or {}).get("text")
            if not self._confirm_copy_release(
                    conn, target, tid, field, payload, content_hash,
                    row_title):
                return None
        return (payload["text"], payload["html"], payload["sanitized"],
                payload["notice"])
```

### 6.2 `src/ui/web/zendesk_bridge.py` — relay docstring

**Replace the `copyField` slot's docstring** (pure relay, no behavior change):

```python
    @Slot(str, str, str)
    def copyField(self, target, target_id, field):
        """Copy exact — the controller re-reads the DB bytes at click time
        and the Python-side clipboard does the copy (target kind decides
        the table; draft copies require a recorded review + the native
        confirm, at any status)."""
        self._call(self._copy_fn, target or "", target_id or "", field or "")
```

### 6.3 `web/src/zendesk/shape.js` — the UI mirror of the gate

**Replace `canCopyDraft` and the comment above it** (anchor: it sits right after `canSaveDraft`; `COPY_DRAFTED_FIELD = "body_text"` further down is pre-existing — do not touch it):

```js
export function canMarkReady(status) { return status === "pending"; }
export function canMarkCopied(status) { return status === "pending" || status === "ready"; }
export function canSaveDraft(status) { return status === "pending" || status === "ready"; }
// Status never gates the clipboard (owner decision 2026-08-05): any draft
// with a served review copies behind Python's native confirm. Every known
// status is copyable; only an unknown one keeps the buttons off.
export function canCopyDraft(status) {
  return status === "pending" || status === "ready" ||
    status === "copied" || status === "pushed";
}
```

### 6.4 `web/src/zendesk/RevisionCenter.jsx` — header + hint copy

(a) **Replace the file-header comment** (lines 1–8) — post-change header with the import block for anchor:

```jsx
// Revision Center: Renn's proposed drafts in status lanes (pending → ready →
// copied) with the specialist's review controls. Every button only ASKS —
// status transitions, deletes and clipboard copies are validated and gated
// Python-side (delete gets a NATIVE confirm; a copy releases only bytes a
// served SOURCE diff showed, behind a native confirm displaying them; the
// status lanes are workflow bookkeeping the specialist advances by hand and
// never gate the clipboard). Buttons here mirror those gates so the UI
// doesn't invite clicks the controller will silently refuse.
import BodyEditForm from "./BodyEditForm.jsx";
import CopyControls from "./CopyControls.jsx";
import RevisionDiff from "./RevisionDiff.jsx";
import {
  COPY_DRAFTED_FIELD, canCopyDraft, canEditDraftBody, canMarkCopied,
  canMarkReady, canSaveDraft, filterRevisions, groupRevisions, keyActivate,
  originInfo, statusInfo,
} from "./shape.js";
```

(b) **In `RevisionDetail`, replace the `copyHint` computation and its comment** — post-change opening of the component:

```jsx
export function RevisionDetail({
  rev, diff, onSave, onMarkReady, onMarkCopied, onCopy, onDelete, busy,
  bodyEditing, onEditBody, onCancelBodyEdit, onSaveBody, copyBusy,
}) {
  const st = statusInfo(rev.status);
  const copyOk = canCopyDraft(rev.status);
  // Python refuses a copy until a SOURCE diff has been served for the
  // draft's current bytes, then a native confirm displays the exact bytes
  // before anything reaches the clipboard — status never gates a copy.
  const copyHint = copyOk
    ? "Releases only bytes the source review above showed — a native confirm shows them first"
    : "Unknown revision status — refresh before copying";
  return (
    <div className="zd-rev-detail">
      <div className="zd-rev-detail-hd">
        <span className="zd-rev-title">{rev.title}</span>
        <span className={"zd-tag soft" + st.cls}>{st.label}</span>
        <OriginTag sourceRef={rev.source_ref} />
        <span className="zd-hdr-spacer" />
        <span className="zd-cell-meta">
          {rev.kind === "macro" ? "Macro draft " : "Article draft "}{rev.draft_id}
        </span>
      </div>
```

Then rebuild the bundle (`npm --prefix web run build`).

### 6.5 `assets/help/create/zendesk.md` — help article honesty

(a) **Front matter:** change `last_verified: 2026-07-26` → `last_verified: 2026-08-05`.

(b) **Replace the "Working in the mirror." paragraph** (it used to describe pending → ready → copied as the gate sequence) with:

```markdown
**Working in the mirror.** The article and macro editors look and behave like
Zendesk's, but every change stays local. Renn researches from your go-to-market
docs, ticket exports and Asana briefs, and proposes updates as **revisions** —
each with a rationale and its sources. You review the diff against the
mirrored original, then copy the content whenever you are ready — a native
confirmation dialog in the app window shows the exact bytes before anything
reaches the clipboard — paste it into real Zendesk yourself, and mark the
revision ready or copied to track your progress by hand. The pending → ready →
copied statuses are workflow bookkeeping, not a gate: a revision can be copied
at any of them.
```

The help-claims suite (`tests/test_help_claims_create.py`) locks article claims — if it flags this article after the edit, re-read the claim it pins before touching anything else, and remember some help-claims tests are `xfail(strict=True)` and MUST stay red.

**Wiring notes for the workstream:** no schema change, no new slot, no bridge signature change. `tests/test_zendesk_readonly_guard.py` must pass unmodified afterwards (guardrail 1). Remind the operator: the format-parity converter change rotates `draft_content_hash` values, so previously recorded copy-reviews invalidate once — re-open the source view before the next copy (§9).

---

## 7. Tests — the acceptance criteria

These tests ARE the definition of done. Land them exactly as embedded; if one fails on the target, the port (or a prerequisite) is wrong — fix the code, not the test.

**Assertions that FLIPPED (old expectations you must not "restore"):**

| Where | Before | After |
|---|---|---|
| `tests/test_zendesk_bridge.py` | `test_copy_pending_draft_refused_ready_allowed` asserted a pending draft's copy was silently refused | RENAMED to `test_copy_pending_draft_releases_under_review_and_confirm`; a pending draft with a recorded review copies behind the confirm |
| `web/src/zendesk/shape.test.js` | `canCopyDraft("pending")` → `false` | → `true` (and `copied`/`pushed` too; unknown/empty status → `false`) |
| `web/src/zendesk/zendesk.test.jsx` | "a pending draft disables clipboard copies" (asserted ≥4 `disabled=""` + the "Mark the draft ready first" tooltip) | "a pending draft shows ENABLED copy controls" (no disabled controls, `Copy content` + "native confirm" hint present) |
| `tests/test_html_markdown.py` | `test_task_list_renders_checkboxes` asserted two `type="checkbox"` inputs | RENAMED to `test_task_list_renders_glyphs`; asserts NO `<input>`, two `task-list-item`s, `☐ todo` / `☑ done` |
| `tests/test_zendesk_bridge.py` | `test_every_html_copy_target_takes_the_confirm` expected only `["Setting up SSO", "Refund apology"]` (title + macro name) to survive without a confirm host | now also expects the mirror article's `body_text` projection between them, and `field in ("title", "body_text", "macro_name")` |

### 7.1 `tests/test_chat_stop_queue.py` — NEW FILE, complete, verbatim (1,228 lines)

```python
"""Stop-the-run + queue-context-while-busy (Renn chat, 2026-08-05).

Covers the three layers of the feature:
  - ChatEngine.stop(): abort route via the client's abort_active, surfacing as
    run_stopped (not error_occurred), with graceful degradation for idle
    engines and abort-less clients (the Gemini ReportBridgeClient).
  - AgentChatController.queue_user_message / stop_run: the busy-queue reused
    from enqueue_trigger, with the DEFERRED drain (queued dispatch) and its
    still-busy re-check.
  - ChatBridge stopRun / queueMessage slots: thin, exception-safe relays plus
    the runStopped signal re-emit.

Review-wave regressions (2026-08-05):
  - FIX 1: a session switch (load_session / new_session) DROPS the busy-queue —
    a queued message referred to the old conversation and must never replay
    into the newly-active session.
  - FIX 2a: killing the CLI proc walks the WHOLE tree on Windows (taskkill /T)
    so the MCP-server child dies with it; one choke point (kill_process_tree).
  - FIX 2b: one delayed catch-up poll after busy(False) so a tool row that
    lands after the final polls is not swallowed forever by the next
    busy(True) cursor re-baseline.
  - FIX 2c: run_stopped resyncs the enablement page (panel line + the same
    refresh the response path runs) and persists the stop marker on the Agent
    surface (one row per stopped turn).
  - FIX 3: stop() returns False AND clears _stop_requested when abort_active
    raises or reports nothing killed — a later worker error is then a REAL
    error (error_occurred + degraded streak), never a fake user stop.
  - FIX 4: the drained text is announced (queued_dispatched / the bridge's
    queuedDispatched) so JS un-badges exactly the bubble that ran, never
    inferred from busyChanged(True).
  - FIX 5: a drawer message queued during a publish-confirm modal is KEPT
    (peek-then-pop drain + one armed ~1s retry), not silently dropped.
"""

import json
import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QObject, Signal

# Ensure an application exists for signal delivery + zero-timers. A FULL
# QApplication (same rationale as test_chat_engine): widget tests running
# later in the same pytest process crash if the singleton is core-only.
try:
    from PySide6.QtWidgets import QApplication as _AppClass
except Exception:  # noqa: BLE001 — headless build without QtWidgets
    _AppClass = QCoreApplication
_app = _AppClass.instance() or _AppClass([])

STOP_MARKER = "[Response stopped by the user before completion.]"


def _pump(seconds=0.2):
    """Process events (incl. QTimer.singleShot(0) callbacks) for a while."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        _app.processEvents()
        time.sleep(0.01)


def _wait_for_engine(engine, timeout=10.0):
    """Wait for the engine worker to finish and process pending signals."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if engine._worker is not None:
            engine._worker.wait(100)
        _app.processEvents()
        if not engine.is_busy:
            _app.processEvents()  # process final signals
            return
    raise TimeoutError("Engine worker did not finish")


def _wait_until_busy(engine, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if engine.is_busy:
            return
        _app.processEvents()
        time.sleep(0.01)
    raise TimeoutError("Engine never became busy")


class _AbortableClient:
    """Blocks in generate() until abort_active fires, then raises — the shape
    of a ClaudeCliClient whose subprocess was killed mid-call. abort_active
    reports True (matched + killed), per the FIX-3 bool contract."""

    model = "claude-cli"

    def __init__(self):
        self._killed = threading.Event()
        self.abort_calls = 0

    def generate(self, prompt, system_prompt="", timeout=180):
        if not self._killed.wait(timeout=10):
            return "completed normally"
        raise RuntimeError(
            "Claude CLI call failed: claude_cli_nonzero_exit - killed")

    def abort_active(self):
        self.abort_calls += 1
        self._killed.set()
        return True


class _FalseAbortClient:
    """abort_active reports False — the bridge matched nothing (the call
    already finished, or the id was lost); the run itself keeps going."""

    model = "claude-cli"

    def __init__(self):
        self.release = threading.Event()
        self.abort_calls = 0

    def generate(self, prompt, system_prompt="", timeout=180):
        self.release.wait(timeout=10)
        return "full answer"

    def abort_active(self):
        self.abort_calls += 1
        return False


class _RaisingAbortClient:
    """abort_active raises (the proc is already gone); the run later dies on
    its own — which must then surface as a REAL error, not a user stop."""

    model = "claude-cli"

    def __init__(self):
        self.release = threading.Event()

    def generate(self, prompt, system_prompt="", timeout=180):
        if not self.release.wait(timeout=10):
            return "never released"
        raise RuntimeError("bridge crashed")

    def abort_active(self):
        raise RuntimeError("proc already gone")


class _NoAbortClient:
    """A client with NO abort_active (the Gemini ReportBridgeClient shape).
    Deliberately not a MagicMock — a mock would auto-vivify abort_active."""

    model = "gemini-bridge"

    def __init__(self):
        self.release = threading.Event()

    def generate(self, prompt, system_prompt="", timeout=180):
        self.release.wait(timeout=10)
        return "full answer"


class _GatedClient:
    """Returns instantly once released; blocks until then."""

    model = "claude-cli"

    def __init__(self):
        self.release = threading.Event()
        self.calls = []

    def generate(self, prompt, system_prompt="", timeout=180):
        self.calls.append(prompt)
        if not self.release.wait(timeout=10):
            raise RuntimeError("test client never released")
        return "done"


def _engine_signals(engine):
    seen = {"stopped": [], "errors": [], "busy": [], "responses": []}
    engine.run_stopped.connect(lambda: seen["stopped"].append(1))
    engine.error_occurred.connect(seen["errors"].append)
    engine.busy_changed.connect(seen["busy"].append)
    engine.response_ready.connect(seen["responses"].append)
    return seen


# ═══════════════════════════════════════
#  ChatEngine.stop()
# ═══════════════════════════════════════

class TestChatEngineStop:

    def test_stop_kills_the_run_and_emits_run_stopped(self):
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="t")
        client = _AbortableClient()
        engine.set_client(client)
        seen = _engine_signals(engine)

        engine.send("kill this")
        _wait_until_busy(engine)
        streak_before = engine._degraded_streak

        assert engine.stop() is True
        assert client.abort_calls == 1
        _wait_for_engine(engine)

        assert seen["stopped"] == [1], "run_stopped did not fire"
        assert seen["errors"] == [], "the stop surfaced as an error"
        assert seen["responses"] == []
        assert seen["busy"] == [True, False]
        assert engine._degraded_streak == streak_before, (
            "a user stop fed the degraded streak"
        )
        assert engine._stop_requested is False
        assert engine.history[-1] == {
            "role": "assistant", "content": STOP_MARKER,
        }, "the stop marker is missing from history"

    def test_stop_when_idle_is_a_refused_noop(self):
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="t")
        engine.set_client(_AbortableClient())
        seen = _engine_signals(engine)

        assert engine.stop() is False
        assert seen["stopped"] == []
        assert seen["busy"] == []
        assert engine._stop_requested is False

    def test_stop_with_abortless_client_lets_the_run_complete(self):
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="t")
        client = _NoAbortClient()
        engine.set_client(client)
        seen = _engine_signals(engine)

        engine.send("hello")
        _wait_until_busy(engine)
        assert engine.stop() is False, (
            "stop claimed success against a client with no abort_active"
        )
        assert engine._stop_requested is False
        client.release.set()
        _wait_for_engine(engine)

        assert seen["responses"] == ["full answer"], "the run did not complete"
        assert seen["stopped"] == []
        assert seen["errors"] == []

    def test_completion_that_raced_the_kill_is_a_normal_turn(self):
        """The run finished before the kill landed: _on_worker_finished must
        clear the flag so nothing later misreads a plain error as a stop."""
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="t")
        seen = _engine_signals(engine)
        engine._history = [{"role": "user", "content": "q"}]
        engine._stop_requested = True

        engine._on_worker_finished("made it", {})

        assert engine._stop_requested is False
        assert seen["responses"] == ["made it"]
        assert seen["stopped"] == []

    def test_a_real_error_after_a_cleared_stop_still_errors(self):
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="t")
        seen = _engine_signals(engine)
        streak_before = engine._degraded_streak

        engine._on_worker_error("bridge crashed")

        assert seen["errors"] == ["bridge crashed"]
        assert seen["stopped"] == []
        assert engine._degraded_streak == streak_before + 1

    # ── FIX 3: stop() must not claim success it cannot prove ──────────

    def test_stop_false_when_abort_reports_nothing_killed(self):
        """abort_active() -> False means NOTHING was aborted: stop() must
        return False and clear _stop_requested so the run completes as a
        normal turn (not a fake user stop)."""
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="t")
        client = _FalseAbortClient()
        engine.set_client(client)
        seen = _engine_signals(engine)

        engine.send("hello")
        _wait_until_busy(engine)
        assert engine.stop() is False, (
            "stop claimed success when abort_active matched nothing"
        )
        assert client.abort_calls == 1
        assert engine._stop_requested is False, (
            "_stop_requested survived a refused abort"
        )
        client.release.set()
        _wait_for_engine(engine)

        assert seen["responses"] == ["full answer"], "the run did not complete"
        assert seen["stopped"] == []
        assert seen["errors"] == []

    def test_stop_false_when_abort_raises_and_a_later_error_is_real(self):
        """abort_active() raising is a refused stop: False, flag cleared —
        and when the worker later errors on its own, that surfaces as
        error_occurred + a degraded-streak increment, NOT run_stopped."""
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="t")
        client = _RaisingAbortClient()
        engine.set_client(client)
        seen = _engine_signals(engine)

        engine.send("hello")
        _wait_until_busy(engine)
        streak_before = engine._degraded_streak
        assert engine.stop() is False, "stop claimed success when abort raised"
        assert engine._stop_requested is False

        client.release.set()          # the run now dies for its own reasons
        _wait_for_engine(engine)

        assert seen["errors"] == ["bridge crashed"], (
            "the real worker error was not surfaced"
        )
        assert seen["stopped"] == [], "a refused stop still ate the error"
        assert engine._degraded_streak == streak_before + 1, (
            "the real error did not feed the degraded streak"
        )


# ═══════════════════════════════════════
#  ClaudeCliClient.abort_active
# ═══════════════════════════════════════

def _bare_cli_client():
    from src.llm.claude_cli_client import ClaudeCliClient
    client = ClaudeCliClient.__new__(ClaudeCliClient)
    client.model = "sonnet"
    client.pii_redaction = False
    client._bridge = None
    client._call_counter = 0
    client._mcp_config = []
    client._active_request_id = None
    return client


class TestClaudeCliClientAbort:

    def test_generate_scopes_the_active_request_id(self, monkeypatch):
        client = _bare_cli_client()
        seen = {}

        class _Bridge:
            def set_system_prompt(self, s):
                pass

            def call_blocking(self, prompt, request_id, timeout=300):
                seen["during"] = client._active_request_id
                seen["request_id"] = request_id
                return "ok"

        bridge = _Bridge()
        monkeypatch.setattr(client, "_ensure_bridge", lambda: bridge)
        monkeypatch.setattr(client, "_prepare_prompt", lambda p, s: (p, s))
        assert client.generate("hi") == "ok"
        assert seen["during"] == seen["request_id"], (
            "the request id was not recorded before the bridge call"
        )
        assert client._active_request_id is None, (
            "the request id was not cleared in the finally"
        )

    def test_request_id_clears_even_when_the_call_raises(self, monkeypatch):
        client = _bare_cli_client()

        class _Bridge:
            def set_system_prompt(self, s):
                pass

            def call_blocking(self, prompt, request_id, timeout=300):
                raise RuntimeError("killed")

        monkeypatch.setattr(client, "_ensure_bridge", lambda: _Bridge())
        monkeypatch.setattr(client, "_prepare_prompt", lambda p, s: (p, s))
        with pytest.raises(RuntimeError):
            client.generate("hi")
        assert client._active_request_id is None

    def test_abort_active_forwards_the_recorded_id_and_reports_the_kill(self):
        client = _bare_cli_client()
        aborted = []

        class _Bridge:
            def abort(self, request_id):
                aborted.append(request_id)
                return True

        client._bridge = _Bridge()
        client._active_request_id = "r9"
        assert client.abort_active() is True
        assert aborted == ["r9"]

    def test_abort_active_reports_false_when_the_bridge_matched_nothing(self):
        client = _bare_cli_client()

        class _Bridge:
            def abort(self, request_id):
                return False

        client._bridge = _Bridge()
        client._active_request_id = "r9"
        assert client.abort_active() is False

    def test_abort_active_with_nothing_in_flight_is_a_refused_noop(self):
        client = _bare_cli_client()
        aborted = []

        class _Bridge:
            def abort(self, request_id):
                aborted.append(request_id)
                return True

        client._bridge = _Bridge()
        client._active_request_id = None
        assert client.abort_active() is False    # no id
        client._bridge = None
        client._active_request_id = "r1"
        assert client.abort_active() is False    # no bridge
        assert aborted == []

    def test_abort_active_swallows_bridge_errors_and_reports_false(self):
        client = _bare_cli_client()

        class _Bridge:
            def abort(self, request_id):
                raise RuntimeError("proc gone")

        client._bridge = _Bridge()
        client._active_request_id = "r1"
        assert client.abort_active() is False    # must not raise either


# ═══════════════════════════════════════
#  Controller: queue_user_message + deferred drain + stop_run
# ═══════════════════════════════════════

@pytest.fixture
def harness(empty_db, monkeypatch):
    """A stripped AgentChatController (test_drive_folder_picker precedent)
    over a REAL ChatEngine + gated client, with real message persistence into
    the fixture DB."""
    from src.data.connection_factory import get_connection
    from src.services.agent_chat import AgentChatController
    from src.services.chat_engine import ChatEngine
    from src.services.chat_session import create_session

    sid = create_session("agent", conn=empty_db.conn)

    client = _GatedClient()
    engine = ChatEngine(system_prompt="t")
    engine.set_client(client)

    ctrl = AgentChatController.__new__(AgentChatController)
    QObject.__init__(ctrl)
    ctrl._engine = engine
    ctrl._session_id = sid
    ctrl._pending_triggers = []
    db_path = str(empty_db.db_path)
    monkeypatch.setattr(
        ctrl, "_open_conn",
        lambda readonly=False: get_connection(db_path, readonly=readonly))
    monkeypatch.setattr(ctrl, "_prepare_provider", lambda: None)
    monkeypatch.setattr(ctrl, "_write_session_pointer", lambda sid: None)
    engine.busy_changed.connect(ctrl._on_busy_changed)

    yield ctrl, engine, client, sid, empty_db.conn

    client.release.set()
    try:
        _wait_for_engine(engine)
    except TimeoutError:
        pass


def _user_rows(conn, sid):
    return [r[0] for r in conn.execute(
        "SELECT content FROM chat_messages WHERE session_id=? AND role='user' "
        "ORDER BY ordinal", (sid,)).fetchall()]


class TestQueueUserMessage:

    def test_idle_send_reports_sent_and_persists_once(self, harness):
        ctrl, engine, client, sid, conn = harness
        assert ctrl.queue_user_message("hello") == "sent"
        client.release.set()
        _wait_for_engine(engine)
        assert _user_rows(conn, sid) == ["hello"]

    def test_busy_send_queues_then_drains_on_idle(self, harness):
        ctrl, engine, client, sid, conn = harness
        assert ctrl.queue_user_message("one") == "sent"
        _wait_until_busy(engine)

        assert ctrl.queue_user_message("two") == "queued"
        assert ctrl._pending_triggers == ["two"]
        assert _user_rows(conn, sid) == ["one"], (
            "a queued message was persisted before its turn started"
        )

        client.release.set()
        deadline = time.time() + 10
        while time.time() < deadline:
            _pump(0.05)
            if len(client.calls) >= 2 and not engine.is_busy:
                break
        _wait_for_engine(engine)

        assert len(client.calls) == 2, "the queued message never ran"
        assert ctrl._pending_triggers == []
        rows = _user_rows(conn, sid)
        assert rows == ["one", "two"]
        assert rows.count("two") == 1, "double-persisted the queued text"

    def test_stale_idle_dispatch_requeues_at_head(self, harness):
        """busy_changed(False) delivered while the engine is ALREADY busy
        again (the drain re-check): the trigger stays at the head, nothing is
        sent, nothing is lost, nothing double-persists."""
        ctrl, engine, client, sid, conn = harness
        ctrl.queue_user_message("one")
        _wait_until_busy(engine)
        assert ctrl.queue_user_message("two") == "queued"

        ctrl._on_busy_changed(False)   # stale — the engine is still busy
        _pump(0.3)                     # let the deferred drain run

        assert ctrl._pending_triggers == ["two"], "the trigger was lost"
        assert len(client.calls) == 1, "the drain sent into a busy engine"
        assert _user_rows(conn, sid) == ["one"]

        client.release.set()
        deadline = time.time() + 10
        while time.time() < deadline:
            _pump(0.05)
            if len(client.calls) >= 2 and not engine.is_busy:
                break
        _wait_for_engine(engine)

        assert len(client.calls) == 2
        assert ctrl._pending_triggers == []
        assert _user_rows(conn, sid) == ["one", "two"]

    def test_engine_none_reports_error(self, harness):
        ctrl, engine, client, sid, conn = harness
        ctrl._engine = None
        assert ctrl.queue_user_message("x") == "error"

    def test_stop_run_delegates_to_the_engine(self, harness):
        ctrl, engine, client, sid, conn = harness
        assert ctrl.stop_run() is False   # idle
        abortable = _AbortableClient()
        engine.set_client(abortable)
        ctrl.queue_user_message("kill this")
        _wait_until_busy(engine)
        assert ctrl.stop_run() is True
        assert abortable.abort_calls == 1
        _wait_for_engine(engine)

    def test_stop_run_with_no_engine_is_false(self, harness):
        ctrl, engine, client, sid, conn = harness
        ctrl._engine = None
        assert ctrl.stop_run() is False


# ═══════════════════════════════════════
#  FIX 1: a session switch drops the busy-queue
# ═══════════════════════════════════════

class TestSessionSwitchDropsQueue:

    def test_queued_message_does_not_follow_a_new_session(self, harness):
        """Probe-style repro: queue during busy, start a NEW session, release.
        The queued text referred to the OLD conversation — it must be dropped,
        not replayed into the fresh thread."""
        ctrl, engine, client, sid, conn = harness
        ctrl.queue_user_message("one")
        _wait_until_busy(engine)
        assert ctrl.queue_user_message("for the old thread") == "queued"

        ctrl.new_session()
        assert ctrl._pending_triggers == [], (
            "new_session left the busy-queue armed"
        )

        client.release.set()
        _wait_for_engine(engine)
        _pump(0.3)   # let any (wrongly surviving) deferred drain run

        assert len(client.calls) == 1, (
            "a queued message replayed after the session switch"
        )
        assert _user_rows(conn, sid) == ["one"]

    def test_queued_message_does_not_follow_a_load_session(self, harness):
        """Probe-style repro: queue during busy, LOAD another session, release.
        The other session's transcript must show zero contamination and the
        queue must be empty."""
        from src.services.chat_session import create_session
        ctrl, engine, client, sid, conn = harness
        other = create_session("enablement", conn=conn)

        ctrl.queue_user_message("one")
        _wait_until_busy(engine)
        assert ctrl.queue_user_message("stale context") == "queued"

        data = ctrl.load_session(other)
        assert data["session_id"] == other
        assert ctrl._session_id == other
        assert ctrl._pending_triggers == [], (
            "load_session left the busy-queue armed"
        )

        client.release.set()
        _wait_for_engine(engine)
        _pump(0.3)

        assert len(client.calls) == 1, (
            "a queued message replayed after the session switch"
        )
        assert _user_rows(conn, other) == [], (
            "the freshly-loaded session was contaminated by the old queue"
        )

    def test_a_guarded_load_keeps_the_queue(self, harness):
        """The decoupling guard (non-enablement session) does NOT switch the
        active session — so it must not drop the queue either."""
        from src.services.chat_session import create_session
        ctrl, engine, client, sid, conn = harness
        product = create_session("trc_analytics", conn=conn)

        ctrl.queue_user_message("one")
        _wait_until_busy(engine)
        assert ctrl.queue_user_message("two") == "queued"

        ctrl.load_session(product)          # refused: not an enablement chat
        assert ctrl._session_id == sid, "the guard let a product chat in"
        assert ctrl._pending_triggers == ["two"], (
            "a refused load still dropped the queue"
        )

        client.release.set()
        deadline = time.time() + 10
        while time.time() < deadline:
            _pump(0.05)
            if len(client.calls) >= 2 and not engine.is_busy:
                break
        _wait_for_engine(engine)
        assert len(client.calls) == 2
        assert _user_rows(conn, sid) == ["one", "two"]


# ═══════════════════════════════════════
#  FIX 4: the drain announces the dispatched text
# ═══════════════════════════════════════

class TestQueuedDispatchedAnnouncement:

    def test_drain_announces_the_dispatched_text(self, harness):
        ctrl, engine, client, sid, conn = harness
        seen = []
        ctrl.queued_dispatched.connect(seen.append)

        ctrl.queue_user_message("one")
        _wait_until_busy(engine)
        assert ctrl.queue_user_message("two") == "queued"
        assert seen == [], "announced before the drain actually dispatched"

        client.release.set()
        deadline = time.time() + 10
        while time.time() < deadline:
            _pump(0.05)
            if len(client.calls) >= 2 and not engine.is_busy:
                break
        _wait_for_engine(engine)

        assert len(client.calls) == 2
        assert seen == ["two"], "the drained text was not announced"

    def test_an_immediate_send_is_not_announced(self, harness):
        ctrl, engine, client, sid, conn = harness
        seen = []
        ctrl.queued_dispatched.connect(seen.append)
        assert ctrl.queue_user_message("now") == "sent"
        client.release.set()
        _wait_for_engine(engine)
        assert seen == [], "an idle send is not a queued dispatch"


# ═══════════════════════════════════════
#  FIX 2c: run_stopped persists the marker on the Agent surface
# ═══════════════════════════════════════

class TestAgentStopPersistence:

    def test_stopped_turn_persists_exactly_one_marker_row(self, harness):
        ctrl, engine, client, sid, conn = harness
        engine.run_stopped.connect(ctrl._on_run_stopped)
        abortable = _AbortableClient()
        engine.set_client(abortable)

        ctrl.queue_user_message("kill this")
        _wait_until_busy(engine)
        assert ctrl.stop_run() is True
        _wait_for_engine(engine)

        rows = [r[0] for r in conn.execute(
            "SELECT content FROM chat_messages WHERE session_id=? AND "
            "role='assistant' ORDER BY ordinal", (sid,)).fetchall()]
        assert rows == [STOP_MARKER], (
            "expected exactly one persisted stop-marker row, got %r" % rows
        )
        # …and it matches what the engine stamped into in-memory history, so a
        # reloaded transcript replays coherently.
        assert engine.history[-1]["content"] == STOP_MARKER

    def test_setup_engine_wires_run_stopped(self):
        import inspect
        from src.services.agent_chat import AgentChatController
        src = inspect.getsource(AgentChatController._setup_engine)
        assert "run_stopped" in src and "_on_run_stopped" in src, (
            "the Agent controller does not persist stopped turns"
        )

    def test_marker_constant_matches_the_engine_history_marker(self):
        from src.services.chat_engine import RUN_STOPPED_MARKER
        assert RUN_STOPPED_MARKER == STOP_MARKER


# ═══════════════════════════════════════
#  ChatBridge: stopRun / queueMessage / runStopped
# ═══════════════════════════════════════

class _FakeEngine(QObject):
    response_ready = Signal(str)
    error_occurred = Signal(str)
    busy_changed = Signal(bool)
    status_update = Signal(str)
    run_stopped = Signal()

    def __init__(self):
        super().__init__()
        self.sent = []

    def send(self, text):
        self.sent.append(text)


class _LegacyEngine(QObject):
    """No run_stopped signal — the hasattr guard must tolerate it."""
    response_ready = Signal(str)
    error_occurred = Signal(str)
    busy_changed = Signal(bool)
    status_update = Signal(str)

    def send(self, text):
        pass


class TestChatBridgeStopQueueSlots:

    def test_stop_run_relays_the_injected_fn(self):
        from src.ui.web.chat_bridge import ChatBridge
        bridge = ChatBridge(_FakeEngine(), stop_fn=lambda: True)
        assert bridge.stopRun() is True
        bridge = ChatBridge(_FakeEngine(), stop_fn=lambda: False)
        assert bridge.stopRun() is False

    def test_stop_run_without_fn_is_false(self):
        from src.ui.web.chat_bridge import ChatBridge
        bridge = ChatBridge(_FakeEngine())
        assert bridge.stopRun() is False

    def test_stop_run_swallows_exceptions(self):
        from src.ui.web.chat_bridge import ChatBridge

        def boom():
            raise RuntimeError("no")

        bridge = ChatBridge(_FakeEngine(), stop_fn=boom)
        assert bridge.stopRun() is False

    def test_queue_message_relays_the_injected_fn(self):
        from src.ui.web.chat_bridge import ChatBridge
        calls = []

        def queue(text):
            calls.append(text)
            return "queued"

        bridge = ChatBridge(_FakeEngine(), queue_fn=queue)
        assert bridge.queueMessage("more context") == "queued"
        assert calls == ["more context"]

    def test_queue_message_without_fn_degrades_to_send(self):
        from src.ui.web.chat_bridge import ChatBridge
        engine = _FakeEngine()
        bridge = ChatBridge(engine)
        assert bridge.queueMessage("hi") == "sent"
        assert engine.sent == ["hi"]

    def test_queue_message_swallows_exceptions(self):
        from src.ui.web.chat_bridge import ChatBridge

        def boom(text):
            raise RuntimeError("no")

        bridge = ChatBridge(_FakeEngine(), queue_fn=boom)
        assert bridge.queueMessage("hi") == "error"

    def test_run_stopped_is_re_emitted(self):
        from src.ui.web.chat_bridge import ChatBridge
        engine = _FakeEngine()
        bridge = ChatBridge(engine)
        seen = []
        bridge.runStopped.connect(lambda: seen.append(1))
        engine.run_stopped.emit()
        assert seen == [1]

    def test_engine_without_run_stopped_still_builds(self):
        from src.ui.web.chat_bridge import ChatBridge
        bridge = ChatBridge(_LegacyEngine())
        assert bridge.stopRun() is False


# ═══════════════════════════════════════
#  FIX 2a: kill the WHOLE subprocess tree (Windows taskkill /T)
# ═══════════════════════════════════════

class _FakeProc:
    """A Popen stand-in. NEVER hand a real PID to these tests — the Windows
    path shells out to taskkill, which is monkeypatched here."""

    def __init__(self, alive=True, pid=424242):
        self.pid = pid
        self._alive = alive
        self.killed = 0
        self.waited = []

    def poll(self):
        return None if self._alive else 0

    def kill(self):
        self.killed += 1
        self._alive = False

    def wait(self, timeout=None):
        self.waited.append(timeout)
        self._alive = False
        return 0


class TestKillProcessTree:

    def test_windows_kill_walks_the_tree_via_taskkill(self, monkeypatch):
        import src.agents.claude_cli_subprocess as sub
        calls = []

        class _Done:
            returncode = 0

        monkeypatch.setattr(sub.sys, "platform", "win32")
        monkeypatch.setattr(
            sub.subprocess, "run",
            lambda cmd, **kw: (calls.append((cmd, kw)), _Done())[1])

        proc = _FakeProc()
        sub.kill_process_tree(proc)

        assert calls, "taskkill was never invoked on Windows"
        cmd, kw = calls[0]
        assert cmd == ["taskkill", "/PID", "424242", "/T", "/F"]
        assert kw.get("capture_output") is True
        assert kw.get("timeout") == 10
        assert proc.killed == 0, "taskkill succeeded but bare kill ran too"
        assert proc.waited == [5], "the proc was not reaped after the kill"

    def test_windows_kill_falls_back_when_taskkill_raises(self, monkeypatch):
        import src.agents.claude_cli_subprocess as sub
        monkeypatch.setattr(sub.sys, "platform", "win32")

        def _boom(cmd, **kw):
            raise OSError("taskkill missing")

        monkeypatch.setattr(sub.subprocess, "run", _boom)
        proc = _FakeProc()
        sub.kill_process_tree(proc)
        assert proc.killed == 1, "no fallback kill after a taskkill failure"

    def test_windows_kill_falls_back_on_nonzero_exit(self, monkeypatch):
        import src.agents.claude_cli_subprocess as sub
        monkeypatch.setattr(sub.sys, "platform", "win32")

        class _Failed:
            returncode = 128

        monkeypatch.setattr(sub.subprocess, "run", lambda cmd, **kw: _Failed())
        proc = _FakeProc()
        sub.kill_process_tree(proc)
        assert proc.killed == 1, "no fallback kill after taskkill exit 128"

    def test_posix_path_is_the_plain_kill(self, monkeypatch):
        import src.agents.claude_cli_subprocess as sub
        monkeypatch.setattr(sub.sys, "platform", "darwin")

        def _never(cmd, **kw):
            raise AssertionError("taskkill invoked on POSIX")

        monkeypatch.setattr(sub.subprocess, "run", _never)
        proc = _FakeProc()
        sub.kill_process_tree(proc)
        assert proc.killed == 1
        assert proc.waited == [5]

    def test_dead_proc_and_none_are_noops(self, monkeypatch):
        import src.agents.claude_cli_subprocess as sub

        def _never(cmd, **kw):
            raise AssertionError("taskkill invoked for a dead proc")

        monkeypatch.setattr(sub.subprocess, "run", _never)
        proc = _FakeProc(alive=False)
        sub.kill_process_tree(proc)
        sub.kill_process_tree(None)
        assert proc.killed == 0 and proc.waited == []

    def test_cli_subprocess_kill_routes_through_the_tree_kill(self, monkeypatch):
        import src.agents.claude_cli_subprocess as sub
        seen = []
        monkeypatch.setattr(sub, "kill_process_tree", seen.append)
        cs = sub.CliSubprocess(["claude"], {}, "prompt")
        proc = _FakeProc()
        cs.proc = proc
        cs.kill()
        assert seen == [proc], (
            "CliSubprocess.kill (timeout/early_stop path) bypassed the tree kill"
        )

    def test_bridge_kill_proc_routes_through_the_tree_kill(self, monkeypatch):
        import src.agents.claude_cli_bridge as bmod
        seen = []
        monkeypatch.setattr(bmod, "kill_process_tree", seen.append)
        bridge = bmod.ClaudeCliBridge()
        proc = _FakeProc()
        bridge._kill_proc(proc)
        assert seen == [proc], (
            "the bridge's abort/shutdown choke point bypassed the tree kill"
        )


# ═══════════════════════════════════════
#  FIX 3 (bridge leg): abort reports whether it matched + killed
# ═══════════════════════════════════════

class TestBridgeAbortReportsBool:

    def test_abort_is_true_only_when_the_id_matches(self, monkeypatch):
        import src.agents.claude_cli_bridge as bmod
        killed = []
        monkeypatch.setattr(bmod, "kill_process_tree", killed.append)
        bridge = bmod.ClaudeCliBridge()

        assert bridge.abort("r1") is False          # nothing in flight
        proc = _FakeProc()
        bridge._track_active(proc, "r1")
        assert bridge.abort("other") is False       # wrong id
        assert killed == []
        assert bridge.abort("r1") is True           # matched + killed
        assert killed == [proc]
        bridge._untrack_active(proc)
        assert bridge.abort("r1") is False          # already untracked


# ═══════════════════════════════════════
#  FIX 2b: the delayed catch-up poll after busy(False)
# ═══════════════════════════════════════

class TestLateToolRowCatchup:

    def _bridge_with_store(self):
        from src.ui.web.chat_bridge import ChatBridge
        store = []

        def tool_poll(since_id):
            return [r for r in store if r["id"] > int(since_id)]

        engine = _FakeEngine()
        bridge = ChatBridge(engine, tool_poll=tool_poll)
        got = []
        bridge.toolCall.connect(lambda j: got.append(json.loads(j)))
        return engine, bridge, store, got

    def test_a_row_landing_after_the_final_polls_is_caught_up(self):
        """Repro: busy True→False with the tool row committing AFTER the final
        busy(False) polls. Without the delayed catch-up the next busy(True)
        re-baselines the cursor past it and it never renders."""
        engine, bridge, store, got = self._bridge_with_store()
        engine.busy_changed.emit(True)     # baselines the cursor at 0
        engine.busy_changed.emit(False)    # final polls see nothing yet
        assert got == []
        store.append({"id": 1, "name": "late_tool", "ok": True})
        _pump(1.6)                         # the ~1200 ms catch-up fires
        assert [r["name"] for r in got] == ["late_tool"], (
            "the late tool row was swallowed"
        )

    def test_catchup_skips_when_a_new_turn_already_started(self):
        engine, bridge, store, got = self._bridge_with_store()
        engine.busy_changed.emit(True)
        engine.busy_changed.emit(False)
        store.append({"id": 1, "name": "late_tool", "ok": True})
        engine.is_busy = True              # a new turn owns the cursor now
        bridge._late_catchup()
        assert got == [], (
            "the catch-up polled into a running turn (accepted residual window)"
        )

    def test_catchup_polls_directly_when_idle(self):
        engine, bridge, store, got = self._bridge_with_store()
        engine.busy_changed.emit(True)
        engine.busy_changed.emit(False)
        store.append({"id": 1, "name": "late_tool", "ok": True})
        bridge._late_catchup()
        assert [r["name"] for r in got] == ["late_tool"]


# ═══════════════════════════════════════
#  FIX 4 (bridge leg): queuedDispatched relay
# ═══════════════════════════════════════

class _QueueSignalApi(QObject):
    """The controller shape: injected as session_api, carrying the
    queued_dispatched signal the bridge re-emits."""
    queued_dispatched = Signal(str)


class TestChatBridgeQueuedDispatched:

    def test_controller_signal_re_emits_as_queued_dispatched(self):
        from src.ui.web.chat_bridge import ChatBridge
        api = _QueueSignalApi()
        bridge = ChatBridge(_FakeEngine(), session_api=api)
        got = []
        bridge.queuedDispatched.connect(got.append)
        api.queued_dispatched.emit("parked text")
        assert got == ["parked text"]

    def test_notify_queued_dispatched_emits_directly(self):
        from src.ui.web.chat_bridge import ChatBridge
        bridge = ChatBridge(_FakeEngine())
        got = []
        bridge.queuedDispatched.connect(got.append)
        bridge.notify_queued_dispatched("drawer text")
        assert got == ["drawer text"]

    def test_session_api_without_the_signal_still_builds(self):
        from src.ui.web.chat_bridge import ChatBridge

        class _PlainApi:
            def list_sessions(self):
                return []

        bridge = ChatBridge(_FakeEngine(), session_api=_PlainApi())
        assert bridge.stopRun() is False   # alive and functional


# ═══════════════════════════════════════
#  FIX 5 + FIX 2c: the enablement page (drawer queue + run_stopped resync)
# ═══════════════════════════════════════

class _FakePanel:
    def __init__(self):
        self.rows = []

    def add_message(self, role, text):
        self.rows.append((role, text))


def _page_stub():
    """Plain host stand-in for the page helpers, called unbound
    (test_web_chat_drawer precedent — an uninitialized QWidget from __new__
    has broken shiboken attribute access)."""
    import src.ui.pages.enablement.page as pg

    class _Inst:
        pass

    inst = _Inst()
    inst.chat = _FakePanel()
    return pg, inst


class TestDrawerPublishRefusalRequeue:

    def _wire_send(self, pg, inst):
        sent = []
        inst._dispatch_chat = sent.append
        inst._mirror_user_turn = lambda t: None
        inst._web_chat_send = lambda t: pg.EnablementPage._web_chat_send(inst, t)
        return sent

    def test_web_chat_send_reports_dispatch_and_refusal(self):
        pg, inst = _page_stub()
        inst.workbench = SimpleNamespace(is_publish_inflight=True)
        sent = self._wire_send(pg, inst)
        assert inst._web_chat_send("blocked") is False
        assert sent == []
        inst.workbench.is_publish_inflight = False
        assert inst._web_chat_send("through") is True
        assert sent == ["through"]

    def test_refused_drain_keeps_the_message_then_sends_after_the_modal(self):
        """Repro: a message queued while a publish-confirm modal is open was
        popped, refused, and silently LOST. It must stay at the head and go
        out once the modal clears."""
        pg, inst = _page_stub()
        inst.workbench = SimpleNamespace(is_publish_inflight=True)
        inst._engine = SimpleNamespace(is_busy=False)
        inst._web_chat_pending = ["held message"]
        inst._web_chat_bridge = None
        sent = self._wire_send(pg, inst)
        retries = []
        inst._arm_web_drain_retry = lambda: retries.append(1)

        pg.EnablementPage._drain_web_chat_pending(inst)
        assert inst._web_chat_pending == ["held message"], (
            "the queued message was dropped during the publish confirm"
        )
        assert sent == []
        assert retries == [1], "no retry was armed for the refused drain"

        inst.workbench.is_publish_inflight = False     # the modal closed
        pg.EnablementPage._drain_web_chat_pending(inst)
        assert sent == ["held message"], "the message never went out"
        assert inst._web_chat_pending == []

    def test_successful_drain_notifies_the_bridge(self):
        from src.ui.web.chat_bridge import ChatBridge
        pg, inst = _page_stub()
        inst.workbench = SimpleNamespace(is_publish_inflight=False)
        inst._engine = SimpleNamespace(is_busy=False)
        inst._web_chat_pending = ["queued text"]
        bridge = ChatBridge(_FakeEngine())
        inst._web_chat_bridge = bridge
        got = []
        bridge.queuedDispatched.connect(got.append)
        self._wire_send(pg, inst)
        inst._arm_web_drain_retry = lambda: None

        pg.EnablementPage._drain_web_chat_pending(inst)
        assert got == ["queued text"], (
            "the drawer drain did not announce the dispatched text"
        )
        assert inst._web_chat_pending == []

    def test_retry_is_single_flight_and_fires_once(self):
        pg, inst = _page_stub()
        calls = []
        inst._drain_web_chat_pending = lambda: calls.append(1)

        pg.EnablementPage._arm_web_drain_retry(inst)
        assert inst._web_drain_retry_armed is True
        pg.EnablementPage._arm_web_drain_retry(inst)   # second arm: no-op
        _pump(1.4)
        assert calls == [1], "the armed retry stacked or never fired"
        assert inst._web_drain_retry_armed is False


class TestPageRunStopped:

    def test_on_engine_stopped_writes_the_panel_and_refreshes(self):
        pg, inst = _page_stub()
        inst.workbench = SimpleNamespace(active_draft_id=7)
        loads, reloads = [], []
        inst._load_live = lambda prefer_draft_id=None: loads.append(prefer_draft_id)
        inst._reload_active_draft_canvas = lambda: reloads.append(1)
        inst._set_status = lambda s: None

        pg.EnablementPage._on_engine_stopped(inst)
        assert ("a", "Run stopped.") in inst.chat.rows, (
            "the native ChatPanel never heard about the stop"
        )
        assert loads == [7] and reloads == [1], (
            "the post-stop refresh (same as the response path) did not run"
        )

    def test_on_engine_stopped_survives_refresh_errors(self):
        pg, inst = _page_stub()
        inst.workbench = SimpleNamespace(active_draft_id=None)

        def _boom(prefer_draft_id=None):
            raise RuntimeError("db locked")

        inst._load_live = _boom
        inst._reload_active_draft_canvas = lambda: None
        statuses = []
        inst._set_status = statuses.append

        pg.EnablementPage._on_engine_stopped(inst)   # must not raise
        assert ("a", "Run stopped.") in inst.chat.rows
        assert statuses and "Refresh error" in statuses[0]

    def test_setup_engine_wires_run_stopped(self):
        import inspect
        import src.ui.pages.enablement.page as pg
        src = inspect.getsource(pg.EnablementPage._setup_engine)
        assert "run_stopped" in src and "_on_engine_stopped" in src, (
            "the page never resyncs after a stopped run"
        )

    def test_on_engine_stopped_writes_the_panel_directly(self):
        """Doubling guard (same rule as _on_engine_response): the bridge
        already relays runStopped into the drawer, so the page writes the
        panel DIRECTLY — a _chat_say notice would double the line there."""
        import inspect
        import src.ui.pages.enablement.page as pg
        src = inspect.getsource(pg.EnablementPage._on_engine_stopped)
        assert "_chat_say" not in src and "add_message" in src
```

### 7.2 `tests/test_chat_engine.py` — harness fix (QApplication, not QCoreApplication)

**Replace the module-level app bootstrap** (previously `_app = QCoreApplication.instance() or QCoreApplication([])`). This fixed a pre-existing crash: this module imports first in its pytest group, and widget tests later in the same process (test_chat_review_panel's real dialog) crash natively if the singleton is core-only.

```python
# Ensure an application exists for signal delivery. A FULL QApplication, not
# QCoreApplication: this module imports first in its pytest group, and widget
# tests later in the same process (test_chat_review_panel's real dialog)
# crash natively if the singleton is core-only.
try:
    from PySide6.QtWidgets import QApplication as _AppClass
except Exception:  # noqa: BLE001 — headless build without QtWidgets
    _AppClass = QCoreApplication
_app = _AppClass.instance() or _AppClass([])
```

### 7.3 `tests/test_asana_readback.py` — subtask-promotion suite

(a) Pre-existing context you should find already present (P13) — the `FakeAsana` helper (UNCHANGED by this port; shown so you can verify shape):

```python
class FakeAsana:
    def __init__(self, tasks=None, subtasks=None):
        self._tasks = tasks or []
        self._subtasks = subtasks or {}
        self.api_key = "tok"

    def list_tasks(self, project_gid, modified_since=None, **kw):
        return list(self._tasks)

    def list_subtasks(self, task_gid, **kw):
        return [dict(s) for s in self._subtasks.get(task_gid, [])]

    def get_task(self, gid, **kw):
        return next((dict(t) for t in self._tasks if t["gid"] == gid), {})
```

(b) **Replace `test_client_read_requests`** (adds the `_get_raw` stub — `list_subtasks` now pages via `_get_raw`):

```python
def test_client_read_requests():
    from src.data.asana_client import AsanaClient
    c = AsanaClient("tok")
    calls = []
    c._get = lambda path, params=None: calls.append((path, params)) or {}
    # list_subtasks pages via _get_raw, so the stub has to sit there too.
    c._get_raw = lambda path, params=None: calls.append((path, params)) or {"data": []}
    c.get_task("100")
    assert calls[-1][0] == "/tasks/100"
    c.list_subtasks("100")
    assert calls[-1][0] == "/tasks/100/subtasks"
```

(c) **Immediately after `test_pull_subtasks_adds_then_syncs_no_dup`, insert the `_SUB` fixture constant and the 13 new tests** (everything from `_SUB = {...}` through `test_update_task_tool_cannot_set_parent_task_ref`, ending just before the pre-existing `test_poll_reconciles_existing_not_recreate`):

```python
_SUB = {"gid": "sa1", "name": "Draft the FAQ", "completed": False,
        "assignee_gid": "u77", "assignee_name": "Ada Lovelace",
        "due_on": "2026-09-01", "modified_at": "2026-08-01T00:00:00Z",
        "permalink_url": "https://app.asana.com/0/1/sa1", "notes": "Body"}


def test_assigned_subtask_promoted_to_task(empty_db):
    from src.data import asana_monitor as am, enablement_tasks as et
    conn = empty_db.conn
    tid = et.create_task(conn, source="asana", kind="request", title="Parent",
                         source_ref="g10")
    fake = FakeAsana(subtasks={"g10": [dict(_SUB)]})
    am._pull_subtasks(conn, fake, tid, "g10", board_source_id="asana:111")
    row = conn.execute(
        "SELECT * FROM enablement_tasks WHERE source='asana' AND source_ref='sa1'"
    ).fetchone()
    assert row is not None
    d = dict(row)
    assert d["parent_task_ref"] == "g10"
    assert d["title"] == "Draft the FAQ"
    assert d["due_date"] == "2026-09-01"
    assert d["assignee"] == "Ada Lovelace" and d["assignee_gid"] == "u77"
    assert d["source_url"] == "https://app.asana.com/0/1/sa1"
    assert d["description"] == "Body"
    # attribution goes through task_board_links, like any polled task
    assert et.task_board_ids(conn, d["task_id"]) == ["asana:111"]
    # the parent's checklist mirror is untouched by promotion
    assert len(et.list_subtasks(conn, tid)) == 1


def test_promoted_subtask_no_duplicate_on_repoll(empty_db):
    from src.data import asana_monitor as am, enablement_tasks as et
    conn = empty_db.conn
    tid = et.create_task(conn, source="asana", kind="request", title="Parent",
                         source_ref="g11")
    fake = FakeAsana(subtasks={"g11": [dict(_SUB)]})
    am._pull_subtasks(conn, fake, tid, "g11", board_source_id="asana:111")
    am._pull_subtasks(conn, fake, tid, "g11", board_source_id="asana:111")
    assert conn.execute(
        "SELECT COUNT(*) FROM enablement_tasks WHERE source='asana' AND source_ref='sa1'"
    ).fetchone()[0] == 1
    assert len(et.list_subtasks(conn, tid)) == 1


def test_promoted_subtask_reconciles_due_assignee_and_done(empty_db):
    from src.data import asana_monitor as am, enablement_tasks as et
    conn = empty_db.conn
    tid = et.create_task(conn, source="asana", kind="request", title="Parent",
                         source_ref="g12")
    fake = FakeAsana(subtasks={"g12": [dict(_SUB)]})
    am._pull_subtasks(conn, fake, tid, "g12", board_source_id="asana:111")
    fake._subtasks["g12"][0].update(
        {"due_on": "2026-09-15", "completed": True,
         "assignee_gid": "u88", "assignee_name": "Grace Hopper"})
    am._pull_subtasks(conn, fake, tid, "g12", board_source_id="asana:111")
    row = dict(conn.execute(
        "SELECT * FROM enablement_tasks WHERE source='asana' AND source_ref='sa1'"
    ).fetchone())
    assert row["due_date"] == "2026-09-15"
    assert row["assignee"] == "Grace Hopper" and row["assignee_gid"] == "u88"
    assert row["status"] == "done"


def test_completed_subtask_is_not_promoted(empty_db):
    from src.data import asana_monitor as am, enablement_tasks as et
    conn = empty_db.conn
    tid = et.create_task(conn, source="asana", kind="request", title="Parent",
                         source_ref="g13")
    done_sub = dict(_SUB, completed=True)
    fake = FakeAsana(subtasks={"g13": [done_sub]})
    am._pull_subtasks(conn, fake, tid, "g13", board_source_id="asana:111")
    assert conn.execute(
        "SELECT COUNT(*) FROM enablement_tasks WHERE source='asana' AND source_ref='sa1'"
    ).fetchone()[0] == 0
    # still mirrored on the parent's checklist, as done
    subs = et.list_subtasks(conn, tid)
    assert len(subs) == 1 and subs[0]["done"] == 1


def test_unassigned_subtask_stays_checklist_only(empty_db):
    from src.data import asana_monitor as am, enablement_tasks as et
    conn = empty_db.conn
    tid = et.create_task(conn, source="asana", kind="request", title="Parent",
                         source_ref="g14")
    fake = FakeAsana(subtasks={"g14": [
        {"gid": "sb1", "name": "No assignee", "completed": False}]})
    am._pull_subtasks(conn, fake, tid, "g14", board_source_id="asana:111")
    assert conn.execute(
        "SELECT COUNT(*) FROM enablement_tasks WHERE source='asana' AND source_ref='sb1'"
    ).fetchone()[0] == 0
    assert len(et.list_subtasks(conn, tid)) == 1


def test_list_tasks_carries_parent_title_for_promoted_rows(empty_db):
    from src.data import asana_monitor as am, enablement_tasks as et
    conn = empty_db.conn
    tid = et.create_task(conn, source="asana", kind="request", title="Parent",
                         source_ref="g15")
    fake = FakeAsana(subtasks={"g15": [dict(_SUB)]})
    am._pull_subtasks(conn, fake, tid, "g15", board_source_id="asana:111")
    by_ref = {t.get("source_ref"): t for t in et.list_tasks(conn)}
    assert by_ref["sa1"]["parent_task_ref"] == "g15"
    assert by_ref["sa1"]["parent_title"] == "Parent"
    assert by_ref["g15"]["parent_task_ref"] is None
    assert by_ref["g15"]["parent_title"] is None


def test_migration_056_applied_and_rerun_is_noop(empty_db):
    conn = empty_db.conn
    cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(enablement_tasks)").fetchall()}
    assert "parent_task_ref" in cols
    applied = {r[0] for r in conn.execute(
        "SELECT filename FROM schema_migrations").fetchall()}
    assert "056_subtask_promotion.sql" in applied
    from src.updater.schema_migrator import SchemaMigrator
    assert SchemaMigrator().migrate(conn) == []


def test_unassignment_clears_assignee_and_still_syncs(empty_db):
    """A promoted subtask whose Asana assignee is REMOVED must keep
    reconciling: assignee columns clear to empty, due/done still sync."""
    from src.data import asana_monitor as am
    conn = empty_db.conn
    from src.data import enablement_tasks as et
    tid = et.create_task(conn, source="asana", kind="request", title="Parent",
                         source_ref="g20")
    fake = FakeAsana(subtasks={"g20": [dict(_SUB)]})
    am._pull_subtasks(conn, fake, tid, "g20", board_source_id="asana:111")
    fake._subtasks["g20"][0].update(
        {"assignee_gid": "", "assignee_name": "",
         "due_on": "2026-09-20", "completed": True})
    am._pull_subtasks(conn, fake, tid, "g20", board_source_id="asana:111")
    row = dict(conn.execute(
        "SELECT * FROM enablement_tasks WHERE source='asana' AND source_ref='sa1'"
    ).fetchone())
    assert row["assignee"] == "" and row["assignee_gid"] == ""
    assert row["due_date"] == "2026-09-20"
    assert row["status"] == "done"


def test_vanished_subtask_dismissed_not_deleted(empty_db):
    """A subtask deleted in Asana emits no board event; the authoritative
    list_subtasks diff must dismiss the promoted row — never DELETE it."""
    from src.data import asana_monitor as am, enablement_tasks as et
    conn = empty_db.conn
    tid = et.create_task(conn, source="asana", kind="request", title="Parent",
                         source_ref="g21")
    fake = FakeAsana(subtasks={"g21": [dict(_SUB)]})
    am._pull_subtasks(conn, fake, tid, "g21", board_source_id="asana:111")
    fake._subtasks["g21"] = []
    am._pull_subtasks(conn, fake, tid, "g21", board_source_id="asana:111")
    rows = conn.execute(
        "SELECT status FROM enablement_tasks WHERE source='asana' AND source_ref='sa1'"
    ).fetchall()
    assert len(rows) == 1                      # dismissed, not deleted
    assert rows[0][0] == "dismissed"
    # decision: the checklist-mirror row is retained (never a DELETE), same
    # discipline as the task row — it just stops syncing.
    assert len(et.list_subtasks(conn, tid)) == 1


def test_truncated_listing_skips_dismissal(empty_db):
    """DELETION-FAILS-CLOSED: a listing that could be silently truncated
    (>= one full Asana page) must not dismiss anything this cycle."""
    from src.data import asana_monitor as am, enablement_tasks as et
    from src.data.asana_client import _PAGE_SIZE
    conn = empty_db.conn
    tid = et.create_task(conn, source="asana", kind="request", title="Parent",
                         source_ref="g22")
    fake = FakeAsana(subtasks={"g22": [dict(_SUB)]})
    am._pull_subtasks(conn, fake, tid, "g22", board_source_id="asana:111")
    # Next poll returns a full page NOT containing sa1 — indistinguishable
    # from a mid-walk truncation, so the dismissal pass must be skipped.
    fake._subtasks["g22"] = [
        {"gid": f"t{i}", "name": f"n{i}", "completed": False}
        for i in range(_PAGE_SIZE)]
    am._pull_subtasks(conn, fake, tid, "g22", board_source_id="asana:111")
    row = conn.execute(
        "SELECT status FROM enablement_tasks WHERE source='asana' AND source_ref='sa1'"
    ).fetchone()
    assert row[0] != "dismissed"


def test_failed_listing_skips_dismissal(empty_db):
    """A listing that dies mid-call proves nothing about deletions —
    the exception propagates and no dismissal happens."""
    import pytest
    from src.data import asana_monitor as am, enablement_tasks as et
    conn = empty_db.conn
    tid = et.create_task(conn, source="asana", kind="request", title="Parent",
                         source_ref="g23")
    fake = FakeAsana(subtasks={"g23": [dict(_SUB)]})
    am._pull_subtasks(conn, fake, tid, "g23", board_source_id="asana:111")

    def boom(task_gid, **kw):
        raise RuntimeError("429 mid-page")

    fake.list_subtasks = boom
    with pytest.raises(RuntimeError):
        am._pull_subtasks(conn, fake, tid, "g23", board_source_id="asana:111")
    row = conn.execute(
        "SELECT status FROM enablement_tasks WHERE source='asana' AND source_ref='sa1'"
    ).fetchone()
    assert row[0] != "dismissed"


def test_parent_dismissal_cascades_to_promoted_children(empty_db):
    """Deleting the parent task in Asana dismisses its promoted subtask rows
    too (their deletion emits no event of its own). Done children keep their
    done state; nothing is DELETEd."""
    from src.data import asana_monitor as am, enablement_tasks as et
    conn = empty_db.conn
    tid = et.create_task(conn, source="asana", kind="request", title="Parent",
                         source_ref="g24")
    done_child = dict(_SUB, gid="sa2", name="Done child")
    fake = FakeAsana(subtasks={"g24": [dict(_SUB), done_child]})
    am._pull_subtasks(conn, fake, tid, "g24", board_source_id="asana:111")
    child_done = conn.execute(
        "SELECT task_id FROM enablement_tasks WHERE source_ref='sa2'"
    ).fetchone()[0]
    et.update_task(conn, child_done, status="done")
    am._dismiss_by_gid(conn, "g24")
    by_ref = {r[0]: r[1] for r in conn.execute(
        "SELECT source_ref, status FROM enablement_tasks WHERE source='asana'"
    ).fetchall()}
    assert by_ref["g24"] == "dismissed"
    assert by_ref["sa1"] == "dismissed"        # open child cascades
    assert by_ref["sa2"] == "done"             # done child keeps done
    assert len(by_ref) == 3                    # nothing deleted


def test_description_synced_on_repoll(empty_db):
    """Asana notes are source-of-truth for the body on subtasks too, matching
    the parent-task reconcile convention."""
    from src.data import asana_monitor as am, enablement_tasks as et
    conn = empty_db.conn
    tid = et.create_task(conn, source="asana", kind="request", title="Parent",
                         source_ref="g25")
    fake = FakeAsana(subtasks={"g25": [dict(_SUB)]})
    am._pull_subtasks(conn, fake, tid, "g25", board_source_id="asana:111")
    fake._subtasks["g25"][0]["notes"] = "Edited in Asana"
    am._pull_subtasks(conn, fake, tid, "g25", board_source_id="asana:111")
    row = conn.execute(
        "SELECT description FROM enablement_tasks WHERE source_ref='sa1'"
    ).fetchone()
    assert row[0] == "Edited in Asana"


def test_update_task_tool_cannot_set_parent_task_ref(empty_db):
    """parent_task_ref is promotion lineage owned by asana_monitor — the chat
    tool lane must strip it so a model turn can't fake or orphan a subtask."""
    from src.data import enablement_tasks as et
    from src.data.chat_tools.enablement_tools import handle_update_task
    conn = empty_db.conn
    tid = et.create_task(conn, source="asana", kind="request", title="T",
                         source_ref="g26")
    res = handle_update_task(
        conn, {"task_id": tid, "parent_task_ref": "evil", "priority": "high"}, {})
    assert res["ok"] is True
    t = et.get_task(conn, tid)
    assert t["parent_task_ref"] is None
    assert t["priority"] == "high"
    # parent_task_ref alone → nothing updatable left after the strip
    res2 = handle_update_task(conn, {"task_id": tid, "parent_task_ref": "evil"}, {})
    assert res2["ok"] is False and res2["error"] == "no_updatable_fields"
    assert et.get_task(conn, tid)["parent_task_ref"] is None
```

### 7.4 `tests/test_calendar_bridge.py` — web-calendar viewmodel

**After `test_description_capped`, insert:**

```python
def test_subtask_fields_carried_into_viewmodel():
    ctrl, seen = _controller()
    ctrl.set_tasks([
        {"task_id": "s1", "title": "Sub work", "source": "asana",
         "due_date": "2026-07-19", "is_subtask": True,
         "parent_title": "Parent task"},
        {"task_id": "t1", "title": "Top work", "source": "asana",
         "due_date": "2026-07-19"},
    ])
    events = {e["id"]: e for e in seen["data"][0]["events"]}
    assert events["s1"]["is_subtask"] is True
    assert events["s1"]["parent_title"] == "Parent task"
    assert events["t1"]["is_subtask"] is False
    assert events["t1"]["parent_title"] == ""
```

### 7.5 `tests/test_calendar_guru_chips.py` — native-calendar chip marker

**After `test_event_activated_carries_payload`, insert:**

```python
def test_subtask_chip_carries_marker(qapp):
    from src.ui.pages.enablement.calendar import CalendarPage, _Chip
    cal = CalendarPage()
    today = date.today().isoformat()
    cal.set_tasks([
        {"due_date": today, "source": "asana", "title": "Sub work",
         "is_subtask": True, "parent_title": "Parent"},
        {"due_date": today, "source": "asana", "title": "Top work"},
    ])
    texts = [c.text() for c in cal.findChildren(_Chip)]
    assert "↳ Sub work" in texts
    assert "Top work" in texts
```

### 7.6 `tests/test_html_markdown.py` — parity + fence-awareness suites

(a) **Update the import** to pull `_restore_task_markers`:

```python
from src.data.html_markdown import (
    _MarkdownParser, _restore_task_markers, html_to_markdown,
    markdown_to_html,
)
```

(b) **In `class TestMarkdownToHtml`, replace `test_task_list_renders_checkboxes` with:**

```python
    def test_task_list_renders_glyphs(self):
        html = markdown_to_html("- [ ] todo\n- [x] done\n")
        assert "<input" not in html
        assert html.count('class="task-list-item"') == 2
        assert "☐ todo" in html
        assert "☑ done" in html
```

(c) **After `class TestMarkdownToHtml` (its last test is `test_fenced_code_and_hr`), append the two new classes:**

````python
class TestListFormatParity:
    """Loose (blank-line-separated) lists must render as tight <li> items —
    Chromium honors <li><p> margins (double-spaced steps on Guru/Zendesk)
    while Qt collapses them, so the shared converter normalizes to tight."""

    def test_loose_ordered_list_emits_tight_items(self):
        html = markdown_to_html("1. Step one\n\n2. Step two")
        assert "<li>Step one</li>" in html
        assert "<li>Step two</li>" in html
        assert "<p>" not in html

    def test_loose_bullet_list_emits_tight_items(self):
        html = markdown_to_html("- Item A\n\n- Item B")
        assert "<li>Item A</li>" in html
        assert "<li>Item B</li>" in html
        assert "<p>" not in html

    def test_multi_paragraph_item_keeps_paragraphs(self):
        html = markdown_to_html(
            "- First para\n\n    Second para of same item\n\n- Another item")
        assert "<p>First para</p>" in html
        assert "<p>Second para of same item</p>" in html
        assert "<li>Another item</li>" in html

    def test_leading_paragraph_before_nested_list_unwrapped(self):
        html = markdown_to_html(
            "1. Parent step\n\n    - child a\n    - child b\n\n2. Next")
        assert "<p>Parent step</p>" not in html
        assert "Parent step" in html
        assert "<li>child a</li>" in html
        assert "<li>Next</li>" in html

    def test_tight_task_items_emit_glyphs_not_inputs(self):
        html = markdown_to_html("- [ ] Do A\n- [x] Do B\n")
        assert "<input" not in html
        assert html.count('class="task-list-item"') == 2
        assert "☐ Do A" in html
        assert "☑ Do B" in html

    def test_loose_task_items_emit_glyphs_not_inputs(self):
        html = markdown_to_html("- [ ] Do A\n\n- [x] Do B\n")
        assert "<input" not in html
        assert html.count('class="task-list-item"') == 2
        assert "☐ Do A" in html
        assert "☑ Do B" in html
        assert "[ ]" not in html and "[x]" not in html

    def test_task_glyph_round_trip(self):
        html = markdown_to_html("- [ ] Do A\n- [x] Do B\n")
        md = html_to_markdown(html)
        assert "- [ ] Do A" in md
        assert "- [x] Do B" in md


class TestTaskGlyphFenceAwareness:
    """_restore_task_markers must not rewrite glyph lines inside code
    fences: a fenced sample containing '- ☐ item' is CODE someone wrote,
    not a rendered task list, and must round-trip byte-identical (it seeds
    _article_body_text and the zendesk body_text clipboard flavour). Only
    backtick fences count — both converter paths (Qt toMarkdown and the
    stdlib fallback) emit ``` fences exclusively, and a line starting
    '~~~' is Qt strikethrough ('~~' + text beginning '~'), never a
    converter-produced fence."""

    def test_fenced_glyph_lines_survive_restore(self):
        md = ("- ☐ real task\n\n"
              "```\n"
              "- ☐ literal glyph\n"
              "- ☑ another literal\n"
              "```\n\n"
              "- ☑ done task")
        out = _restore_task_markers(md)
        assert "- [ ] real task" in out
        assert "- [x] done task" in out
        # inside the fence: byte-identical, never mapped to checkboxes
        assert "- ☐ literal glyph" in out
        assert "- ☑ another literal" in out
        assert "[ ] literal glyph" not in out
        assert "[x] another literal" not in out

    def test_language_info_fence_is_still_a_fence(self):
        md = "```python\n- ☐ item\n```\n\n- ☐ outside"
        out = _restore_task_markers(md)
        assert "- ☐ item" in out
        assert "- [ ] outside" in out

    def test_unclosed_fence_runs_to_end_of_document(self):
        md = "- ☐ before\n\n```\n- ☐ inside, fence never closes"
        out = _restore_task_markers(md)
        assert "- [ ] before" in out
        assert "- ☐ inside, fence never closes" in out

    def test_indented_fence_inside_list_item(self):
        # Qt indents fences inside list items — still a fence.
        md = "- item\n  ```\n  - ☐ code\n  ```\n- ☐ after"
        out = _restore_task_markers(md)
        assert "- ☐ code" in out
        assert "- [ ] after" in out

    def test_tilde_run_is_not_a_fence(self):
        # '~~~struck~~' is strikethrough of '~struck', not a fence opener;
        # it must not suppress restoration for the rest of the document.
        md = "~~~struck~~\n\n- ☐ task"
        out = _restore_task_markers(md)
        assert "- [ ] task" in out
        assert "~~~struck~~" in out

    def test_inline_code_span_line_is_not_a_fence(self):
        # A one-line span like ```foo``` is not an opener (a backtick
        # fence's info string cannot contain backticks).
        md = "```code span```\n\n- ☐ task"
        out = _restore_task_markers(md)
        assert "- [ ] task" in out

    def test_html_to_markdown_fallback_pre_keeps_glyphs_literal(
            self, monkeypatch):
        # Pin the STDLIB fallback path headless even when an earlier test
        # created a QApplication: no QGuiApplication → parser path.
        from PySide6.QtGui import QGuiApplication
        monkeypatch.setattr(QGuiApplication, "instance",
                            staticmethod(lambda: None))
        html = ("<ul><li>☐ real task</li></ul>"
                "<pre>- ☐ keep literal\n- ☑ also literal</pre>")
        md = html_to_markdown(html)
        assert "- [ ] real task" in md
        assert "- ☐ keep literal" in md
        assert "- ☑ also literal" in md
        assert "[ ] keep literal" not in md
        assert "[x] also literal" not in md
````

### 7.7 `tests/test_web_chat_drawer.py` — bridge-factory stubs

The bridge factory now reads three more attributes off the page instance; the two factory tests need stubs for them. **In `test_get_web_chat_bridge_none_without_engine_and_cached_with` and `test_get_web_chat_bridge_caches_the_bridge_object`, after the `_web_chat_tool_poll` stub line, add:**

```python
    inst._web_chat_stop = lambda: False
    inst._web_chat_queue = lambda t: "sent"
    inst._on_web_chat_busy_changed = lambda b: None
```

Post-change functions in full:

```python
def test_get_web_chat_bridge_none_without_engine_and_cached_with():
    import src.ui.pages.enablement.page as pg
    # the factory parents the bridge to the page, so the stand-in must be a
    # real QObject here
    inst = QObject()
    inst.chat = _FakePanel()
    inst._engine = None
    assert pg.EnablementPage._get_web_chat_bridge(inst) is None
    inst._web_chat_bridge = None
    inst._engine = _FakeEngine()
    inst._web_chat_send = lambda t: None
    inst._web_chat_tool_poll = lambda since_id=0: []
    inst._web_chat_stop = lambda: False
    inst._web_chat_queue = lambda t: "sent"
    inst._on_web_chat_busy_changed = lambda b: None
    b1 = pg.EnablementPage._get_web_chat_bridge(inst)
    b2 = pg.EnablementPage._get_web_chat_bridge(inst)
    assert isinstance(b1, ChatBridge) and b1 is b2
```

```python
def test_get_web_chat_bridge_caches_the_bridge_object():
    """The factory must cache — else a second call rebuilds and the two tabs
    would register DIFFERENT bridge objects (or the None short-circuit would
    leave _web_chat_bridge unset)."""
    import src.ui.pages.enablement.page as pg
    from PySide6.QtCore import QObject
    inst = QObject()
    inst.chat = _FakePanel()
    inst._web_chat_bridge = None
    inst._engine = _FakeEngine()
    inst._web_chat_send = lambda t: None
    inst._web_chat_tool_poll = lambda since_id=0: []
    inst._web_chat_stop = lambda: False
    inst._web_chat_queue = lambda t: "sent"
    inst._on_web_chat_busy_changed = lambda b: None
    b = pg.EnablementPage._get_web_chat_bridge(inst)
    assert b is not None and inst._web_chat_bridge is b
    assert pg.EnablementPage._get_web_chat_bridge(inst) is b   # cached, same object
```

### 7.8 `tests/test_zendesk_bridge.py` — copy-gate + body_text suites

(a) **In the module docstring, replace the copy-exact bullet** — post-change bullet:

```python
* copy-exact reads DB bytes with target-kind disambiguation, draft copies
  release at EVERY status (pending/ready/copied/pushed — status is workflow
  bookkeeping, never a clipboard gate; the review record + native confirm
  are the protection, owner decision 2026-08-05), and the body_rich
  mime-laundering guard (drafts included — draft rich copies pass
  sanitize_html too);
```

(b) **Replace `test_copy_pending_draft_refused_ready_allowed` with the renamed test, and insert the two new tests after it** (anchor: `test_import_origin_stores_sanitized_bytes` follows):

```python
def test_copy_pending_draft_releases_under_review_and_confirm(empty_db):
    """Owner decision 2026-08-05: draft status never gates the clipboard.
    A PENDING draft with a recorded review copies behind the universal
    native confirm — and marking it ready changes nothing (the review
    survives, the content is unchanged)."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    did = zendesk_store.save_article_draft(
        conn, title="T", body="**bold** body", article_id=101, rationale="r")
    calls = []
    ctrl, seen, confirms = _gated(empty_db, answer=True,
                                  clipboard_fn=_clipboard(calls))
    _serve_revisions(ctrl)
    _review(ctrl, "article", did)
    ctrl.js_copy_field("article_draft", str(did), "body_html")
    assert len(calls) == 1 and len(confirms) == 1  # pending → copies, gated
    assert "bold" in calls[0][0] and "**" not in calls[0][0]  # rendered md
    assert seen["copies"][-1]["ok"] is True
    zendesk_store.set_draft_status(conn, "article", did, "ready")
    # marking ready does NOT invalidate the review (content is unchanged)
    ctrl.js_copy_field("article_draft", str(did), "body_html")
    assert len(calls) == 2 and calls[1] == calls[0]


def test_copy_body_text_ready_draft_reaches_the_clipboard(empty_db):
    """The SPA's PRIMARY "Copy content" button asks for ``body_text`` — the
    drafted prose. For a reviewed READY draft behind the approved native
    confirm the stored markdown must land on the clipboard, text flavour
    only. This was a silent no-op for every article and draft at every
    status (the field was absent from _COPY_FIELDS and _html_payloads built
    no payload for it)."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    calls = []
    ctrl, seen, confirms = _gated(empty_db, answer=True,
                                  clipboard_fn=_clipboard(calls))
    did = _ready_draft(conn, ctrl, title="T", body="**bold** drafted prose")
    _review(ctrl, "article", did)
    ctrl.js_copy_field("article_draft", str(did), "body_text")
    # stored markdown, verbatim, text-only clipboard flavour
    assert calls == [("**bold** drafted prose", None)]
    assert len(confirms) == 1                      # draft ⇒ native confirm
    assert "**bold** drafted prose" in _confirm_content(confirms)
    res = seen["copies"][-1]
    assert res["ok"] is True and res["field"] == "body_text"
    assert res["target"] == "article_draft"


def test_spa_primary_copy_field_is_a_python_copy_field():
    """CROSS-BOUNDARY PARITY: the SPA's primary copy button sends
    shape.js's COPY_DRAFTED_FIELD through the bridge verbatim, so Python
    must accept that field for both article targets — otherwise the primary
    copy is a silent no-op, which is exactly the defect that shipped
    because no test read both sides."""
    src = (REPO / "web" / "src" / "zendesk" / "shape.js").read_text(
        encoding="utf-8")
    m = re.search(r'export const COPY_DRAFTED_FIELD\s*=\s*"([^"]+)"', src)
    assert m, "COPY_DRAFTED_FIELD not found in web/src/zendesk/shape.js"
    field = m.group(1)
    assert field in zendesk_web._COPY_FIELDS["article"]
    assert field in zendesk_web._COPY_FIELDS["article_draft"]
```

(`test_spa_primary_copy_field_is_a_python_copy_field` uses the module-level `REPO` path constant and `re` — both already used elsewhere in this file, P13.)

(c) **Replace `test_every_recorded_hash_is_shown_or_derived_from_shown_bytes`** (docstring re-scoped; assertions unchanged) **and insert `test_body_text_scope_limit_is_real_and_the_confirm_covers_it` right after it** (anchor: `test_zero_change_with_differing_bytes_is_surfaced_as_a_warning` follows):

```python
def test_every_recorded_hash_is_shown_or_derived_from_shown_bytes(empty_db):
    """Closes the last gap in the invariant's wording.

    Of everything ``_record_review`` binds, the DIFFED plain text flavours
    are rendered literally on the review surface (the diff rows /
    body_source / the title row). ``body_text`` is not one of them — it is
    the markdown SOURCE of the same reviewed content, never diffed, and its
    release-time disclosure on a draft is the native byte-showing confirm
    (``_confirm_copy_release``). The rich (text/html mime) flavour is not
    independent content: it is ``sanitize_html`` of the string the reviewer
    WAS shown, and sanitize only removes and escapes — it can never
    introduce a tag, attribute or URL that was absent from the source.
    Assert that derivation (and the accompanying notice) so the claim is
    mechanically checked, not assumed.

    Note the RENDERED preview srcdoc is a third, WIDER rendering
    (sanitize_html_preview + Help Center CSS) and is deliberately NOT a
    clipboard flavour — pinned here so a future widening of the preview can
    never be mistaken for widening the clipboard."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    ctrl, seen = _controller(empty_db)
    _review_article(ctrl, 101)
    payloads, _h, _r = ctrl._copy_bundle(conn, "article", 101)
    detail = seen["article"][-1]
    assert payloads["body_html"]["text"] == detail["body_source"]
    # the rich flavour derives from the exact bytes the reviewer was shown
    assert payloads["body_rich"]["html"] == sanitize_html(detail["body_source"])
    # ...and it is NOT the preview srcdoc (which carries the Help Center CSS
    # and the wider preview profile)
    assert payloads["body_rich"]["html"] != detail["body_srcdoc"]
    assert "alma-hc-article" in detail["body_srcdoc"]

    from src.data.html_sanitize import sanitize_html_preview
    for html in ("<h2>Clean</h2><p>Fine.</p>", HOSTILE_HTML,
                 '<p><span style="font-size: 0">hidden</span></p>'):
        did = _ready_draft(conn, ctrl, title="T", body="x", body_html=html)
        ctrl.js_request_diff("article", str(did))
        diff = seen["diffs"][-1]
        payloads, _h, _r = ctrl._copy_bundle(conn, "article_draft", did)
        shown = payloads["body_html"]["text"]
        rich = payloads["body_rich"]["html"]
        # the plain flavour is literally in the served source diff
        assert shown in _source_added(diff)
        # the rich flavour is sanitize_html OF that exact string, and
        # sanitizing again changes nothing (removal-only, idempotent)
        assert rich == sanitize_html(shown)
        assert sanitize_html(rich) == rich
        # The notice tracks the PREVIEW report — everything the preview
        # drops AND everything it un-hides — not "the strict profile would
        # have changed something". `font-size: 0` survives sanitize_html
        # untouched (rich == shown) yet hides the text from a reader, which
        # is exactly the case the old rich!=shown rule stayed silent for.
        _preview, report = sanitize_html_preview(shown, report=True)
        if report:
            assert "preview does not display" in diff["markup_notice"]
            assert diff["markup_report"] == report
        else:
            assert diff["markup_notice"] == "" and diff["markup_report"] == []
    assert sanitize_html_preview(
        '<p><span style="font-size: 0">hidden</span></p>',
        report=True)[1] == ["font-size:0"]


def test_body_text_scope_limit_is_real_and_the_confirm_covers_it(empty_db):
    """Pins the SCOPED wording of the reviewed-bytes claim (docstring fix,
    2026-08-05): the character-for-character guarantee holds for the DIFFED
    flavours only. ``body_text`` is the markdown SOURCE of the reviewed
    content and is NOT diffed — a markdown link reference definition
    renders to nothing in HTML, so its bytes can be present in the
    body_text copy while appearing in NO diff row. The release-time
    protection is the native byte-showing confirm: every draft copy of
    body_text displays the exact released bytes, definition included, and
    a declined confirm releases nothing."""
    _seed_mirror(empty_db.conn)
    conn = empty_db.conn
    # An UNUSED link reference definition: python-markdown consumes it and
    # renders nothing, so no body_html-derived surface carries its bytes.
    body = ("See the doc for details.\n\n"
            '[ref]: https://example.com/hidden-target "never rendered"')
    calls = []
    ctrl, seen, confirms = _gated(empty_db, answer=True,
                                  clipboard_fn=_clipboard(calls))
    did = _ready_draft(conn, ctrl, title="T", body=body)
    ctrl.js_request_diff("article", str(did))
    diff = seen["diffs"][-1]
    # the definition line renders to nothing: NO diff surface carries it
    joined_rows = "\n".join(r["text"] for r in diff["rows"])
    joined_text = "\n".join(r["text"] for r in diff["text_rows"])
    assert "hidden-target" not in joined_rows
    assert "hidden-target" not in joined_text
    # ...yet the review record binds the body_text bytes (defence in depth)
    rec = ctrl._reviewed[("article_draft", did)]
    assert zendesk_web._sha(body) in rec["bytes"]
    # the release-time disclosure: the native confirm shows the EXACT
    # released bytes, link reference definition included
    ctrl.js_copy_field("article_draft", str(did), "body_text")
    assert len(confirms) == 1
    assert body in _confirm_content(confirms)
    assert "hidden-target" in _confirm_content(confirms)
    assert calls == [(body, None)]
    # declined confirm ⇒ no clipboard write and no receipt
    calls2 = []
    ctrl2, seen2, confirms2 = _gated(empty_db, answer=False,
                                     clipboard_fn=_clipboard(calls2))
    _serve_revisions(ctrl2)
    ctrl2.js_request_diff("article", str(did))
    ctrl2.js_copy_field("article_draft", str(did), "body_text")
    assert len(confirms2) == 1
    assert calls2 == []
    assert seen2["copies"] == []
```

(d) **Replace the tail of `test_every_html_copy_target_takes_the_confirm`** — full post-change function:

```python
def test_every_html_copy_target_takes_the_confirm(empty_db):
    """THE INVARIANT, enumerated from the controller's own tables instead of
    listed by hand: every copy field that carries MARKUP requires the native
    confirm, on mirror rows and drafts alike. Plain-text fields keep the
    review-record gate alone — unless the target is a DRAFT, which confirms
    everything."""
    for target, fields in zendesk_web._COPY_FIELDS.items():
        for field in fields:
            markup = field in zendesk_web._MARKUP_COPY_FIELDS
            draft = target in zendesk_web._DRAFT_COPY_TARGETS
            need = zendesk_web._copy_needs_confirm(target, field)
            assert need is (markup or draft), (target, field)
            if markup:
                assert need is True, (target, field)
    # every html-bearing field is covered, on the mirror targets too
    assert zendesk_web._MARKUP_COPY_FIELDS == {"body_html", "body_rich",
                                               "macro_reply"}

    # ...and end to end with NO confirm host injected: every html-bearing
    # target fails closed, and only the plain-text mirror fields survive.
    conn = empty_db.conn
    _seed_mirror(conn)
    calls = []
    ctrl, seen = _controller(empty_db, clipboard_fn=_clipboard(calls))
    ctrl.js_refresh()
    did = _ready_draft(conn, ctrl, title="Draft", body="x",
                       body_html="<p>draft body</p>")
    mdid = zendesk_store.save_macro_draft(
        conn, name="Macro draft", macro_id=201, rationale="r",
        actions=[{"field": "comment_value", "value": "draft reply"}])
    zendesk_store.set_draft_status(conn, "macro", mdid, "ready")
    _serve_revisions(ctrl)
    _review_article(ctrl, 101)
    _review_macro(ctrl, 201)
    _review(ctrl, "article", did)
    _review(ctrl, "macro", mdid)
    ids = {"article": 101, "macro": 201,
           "article_draft": did, "macro_draft": mdid}
    for target, fields in zendesk_web._COPY_FIELDS.items():
        for field in fields:
            ctrl.js_copy_field(target, str(ids[target]), field)
    # the plain-text mirror fields — title, drafted-content prose, macro
    # name — are all that survives without a confirm host
    assert [c[0] for c in calls] == [
        "Setting up SSO",
        zendesk_web.ZendeskWebController._article_body_text(
            zendesk_store.get_article(conn, 101)),
        "Refund apology"]
    assert all(c["field"] in ("title", "body_text", "macro_name")
               for c in seen["copies"])
```

### 7.9 `web/src/chat/chat.test.jsx` — Composer / Bubble / unbadgeDispatched

(a) **Extend the import from `./ChatApp.jsx`:**

```jsx
import { Bubble, Composer, DraftCard, ResolveNotice, ReviewPanel, unbadgeDispatched } from "./ChatApp.jsx";
```

(b) **After the `DraftCard` describe block, insert the three new describes** (anchor: the `STALE` const + `ReviewPanel` describe follow):

```jsx
describe("Composer — stop the run + add context while busy", () => {
  function composer(props) {
    return renderToStaticMarkup(
      <Composer input="more context" onInput={noop} onSend={noop} onStop={noop}
                busy={false} stopping={false} disabled={false} mic={null}
                {...props} />);
  }

  it("keeps the input ENABLED while a turn runs (extra context queues)", () => {
    expect(composer({ busy: true })).not.toMatch(/<input[^>]*disabled/);
    expect(composer({ busy: false })).not.toMatch(/<input[^>]*disabled/);
  });

  it("swaps Send for Stop while busy", () => {
    const busyOut = composer({ busy: true });
    expect(busyOut).toContain(">Stop</button>");
    expect(busyOut).not.toContain(">Send</button>");
    const idleOut = composer({ busy: false });
    expect(idleOut).toContain(">Send</button>");
    expect(idleOut).not.toContain(">Stop</button>");
  });

  it("disables Stop after the click until busy clears", () => {
    const out = composer({ busy: true, stopping: true });
    expect(out).toMatch(/<button[^>]*class="send stop"[^>]*disabled/);
  });

  it("leaves Stop clickable before the click", () => {
    const out = composer({ busy: true, stopping: false });
    expect(out).not.toMatch(/<button[^>]*class="send stop"[^>]*disabled/);
  });
});

describe("Bubble — the queued badge", () => {
  it("badges a message parked while a turn runs", () => {
    const out = renderToStaticMarkup(
      <Bubble m={{ role: "user", text: "later please", queued: true }} />);
    expect(out).toContain("queued-badge");
    expect(out).toContain("later please");
  });

  it("has no badge once the message is no longer queued", () => {
    const out = renderToStaticMarkup(
      <Bubble m={{ role: "user", text: "later please" }} />);
    expect(out).not.toContain("queued-badge");
  });

  it("renders the stopped-run system line", () => {
    const out = renderToStaticMarkup(
      <Bubble m={{ role: "system", text: "Run stopped." }} />);
    expect(out).toContain("msg system");
    expect(out).toContain("Run stopped.");
  });
});

describe("unbadgeDispatched — the badge clears on the named text only", () => {
  // The bridge's queuedDispatched(text) names EXACTLY what Python drained.
  // Un-badging must match on that text — never "the oldest queued bubble on
  // busyChanged(true)", because the Python queue interleaves items with no
  // bubble here ([SYSTEM] picker triggers, sends from the other surface).
  const msgs = [
    { role: "user", text: "first", queued: true },
    { role: "user", text: "second", queued: true },
    { role: "user", text: "not queued twin" },
  ];

  it("clears the first queued bubble whose text matches", () => {
    const out = unbadgeDispatched(msgs, "second");
    expect(out[0].queued).toBe(true);   // untouched — a different text
    expect(out[1].queued).toBe(false);  // the named one
  });

  it("does not clear anything when the dispatched item has no bubble here", () => {
    // A [SYSTEM] trigger or an other-surface send drained: this surface's
    // queued bubbles keep their badges.
    const out = unbadgeDispatched(msgs, "[SYSTEM: operator selected a board]");
    expect(out).toBe(msgs);             // same array — no state churn either
    expect(out[0].queued).toBe(true);
    expect(out[1].queued).toBe(true);
  });

  it("clears only ONE bubble when duplicates are queued", () => {
    const dupes = [
      { role: "user", text: "again", queued: true },
      { role: "user", text: "again", queued: true },
    ];
    const out = unbadgeDispatched(dupes, "again");
    expect(out[0].queued).toBe(false);
    expect(out[1].queued).toBe(true);
  });

  it("ignores a non-queued bubble with the same text", () => {
    const out = unbadgeDispatched(msgs, "not queued twin");
    expect(out).toBe(msgs);
  });

  it("does not mutate the input array", () => {
    const before = msgs.map((m) => ({ ...m }));
    unbadgeDispatched(msgs, "first");
    expect(msgs).toEqual(before);
  });
});
```

### 7.10 `web/src/zendesk/shape.test.js` — lifecycle-guard flips

**Replace the "draft lifecycle guards" describe** — post-change block:

```js
describe("draft lifecycle guards (mirror of the Python gates)", () => {
  it("pending: ready-able, editable, and copyable (status never gates the clipboard)", () => {
    expect(canMarkReady("pending")).toBe(true);
    expect(canMarkCopied("pending")).toBe(true);
    expect(canSaveDraft("pending")).toBe(true);
    expect(canCopyDraft("pending")).toBe(true);
  });

  it("ready: copyable, no re-ready", () => {
    expect(canMarkReady("ready")).toBe(false);
    expect(canMarkCopied("ready")).toBe(true);
    expect(canCopyDraft("ready")).toBe(true);
  });

  it("copied/pushed: immutable, copy-only", () => {
    for (const s of ["copied", "pushed"]) {
      expect(canMarkReady(s)).toBe(false);
      expect(canMarkCopied(s)).toBe(false);
      expect(canSaveDraft(s)).toBe(false);
      expect(canCopyDraft(s)).toBe(true);
    }
  });

  it("an unknown status keeps the copy buttons off", () => {
    expect(canCopyDraft("weird")).toBe(false);
    expect(canCopyDraft("")).toBe(false);
  });

  it("statusInfo maps every state to a label", () => {
    expect(statusInfo("pending").label).toBe("Pending review");
    expect(statusInfo("ready").cls).toBe("blue");
    expect(statusInfo("copied").cls).toBe("green");
    expect(statusInfo("weird").cls).toBe("grey");
  });
});
```

(The import already carries `COPY_DRAFTED_FIELD` and `canCopyDraft` — verify, don't duplicate.)

### 7.11 `web/src/zendesk/zendesk.test.jsx` — pending-draft copy controls

**Replace the "a pending draft disables clipboard copies" test with:**

```jsx
  it("a pending draft shows ENABLED copy controls (status never gates the clipboard)", () => {
    const out = renderToStaticMarkup(
      <RevisionCenter {...props}
                      activeDraft={{ draft_id: 8, kind: "article" }}
                      diff={fx.diffs["article:8"]} />);
    expect(out).toContain("Mark ready");
    // the copy affordances are live — Python's review record + native
    // confirm are the gate, not the status
    expect(out).not.toContain("Mark the draft ready first");
    expect(out).toContain("Copy content");
    expect(out).toContain("native confirm");
    expect(out).not.toContain("disabled=\"\"");
  });
```

(The neighboring test "the reviewed DRAFTED CONTENT is the primary copy…" is pre-existing and unchanged — it is the test that always encoded the SPA's `body_text` intent.)

---

## 8. Order of implementation + verification matrix

### 8.0 FIRST: inventory pre-existing failures

Before changing a single file, run every group in §8.2 on the UNTOUCHED target tree and record the baseline. The target may carry its own drift; you must be able to distinguish "this port broke it" from "it was already red". On the source dev box the known-environmental reds were: `test_calendar_guru_chips` 2× (dev settings have `enablement.web_tabs=all`; both pre-date this port), the help-claims drift suites, and the mig-idempotence harness suite — none caused by this work. Whatever the target's baseline is, WRITE IT DOWN and report it.

### 8.1 Recommended order (with the reason)

1. **Format parity (§4)** — one file, zero dependents inside this port but two dependents downstream of it (`zendesk _copy_bundle` renders drafts through it; `_article_body_text` round-trips through `html_to_markdown`). Landing it first means the zendesk `body_text` tests exercise the final converter, and you observe the draft-hash rotation once, not twice.
2. **Zendesk (§6)** — depends only on the converter (already landed) and the pre-existing copy machinery. Small diff, big test suite; getting `test_zendesk_bridge.py` green early proves the target's copy machinery matched expectations before you touch anything harder.
3. **Asana (§5)** — the largest data-layer change but fully self-contained (migration + monitor + task store + display surfaces). No interaction with chat.
4. **Chat stop/queue (§3)** — last, because it spans the most layers (subprocess → bridge → client → engine → controller → web bridge → two pages → two JSX surfaces) and because its new suite (`test_chat_stop_queue.py`) also pins page/controller structure via `inspect.getsource` — everything else should already be stable underneath it.
5. **One `npm --prefix web run build`** at the end covers all three web-touching workstreams (you may also rebuild per-workstream if you verify in the running app as you go).

### 8.2 Verification matrix

Every command from the repo root. Counts are the ACTUAL green counts on the source tree at `ae0392d` (re-verified while writing this guide). pytest in groups of ≤ 4 files (guardrail 9); kill zombies before anything that spawns bridges (guardrail 10).

| # | Command | Expected on the source tree |
|---|---|---|
| 1 | `python -m pytest tests/test_html_markdown.py tests/test_publish_body_parity.py tests/test_guru_html_publish.py -q` | **69 passed** |
| 2 | `python -m pytest tests/test_zendesk_bridge.py -q` | **219 passed** (~2 min; run it alone — it is one file but heavy) |
| 3 | `python -m pytest tests/test_zendesk_readonly_guard.py tests/test_zendesk_mirror_schema.py tests/test_zendesk_content.py -q` | **64 passed** — with the readonly guard **unmodified** |
| 4 | `python -m pytest tests/test_asana_readback.py tests/test_asana_monitor.py tests/test_asana_pagination.py -q` | **32 passed** |
| 5 | `python -m pytest tests/test_chat_stop_queue.py tests/test_chat_engine.py tests/test_chat_review_panel.py -q` | **198 passed** (~50 s; `test_chat_stop_queue.py` alone is 57) |
| 6 | `python -m pytest tests/test_calendar_bridge.py tests/test_web_chat_drawer.py tests/test_zendesk_readonly_guard.py -q` | **56 passed** |
| 7 | `python -m pytest tests/test_calendar_guru_chips.py -q` | **3 passed** headless (incl. the new `test_subtask_chip_carries_marker`); on the dev box 2 pre-existing reds appear when local settings carry `enablement.web_tabs=all` — check against YOUR baseline from §8.0 |
| 8 | `npm --prefix web run test` | **255 passed** (8 files) |
| 9 | `npm --prefix web run build` | exits 0; regenerates `src/ui/web/dist/index.html` |
| 10 | The gitignored WebEngine round-trip suites, **one pytest invocation per file** (they exist only where a prior session created them — skip absent ones): `tests/test_web_chat_drawer_local.py`, `tests/test_agent_web_local.py`, `tests/test_calendar_web_local.py`, `tests/test_zendesk_web_local.py`, … | each green singly; NEVER two `*_local.py` files in one invocation (exit-255 stacking); assert via `runJavaScript`, never screenshots |
| 11 | Migration smoke: launch the app once (or run any `empty_db`-fixture test — e.g. group 4) and confirm `PRAGMA table_info(enablement_tasks)` now lists `parent_task_ref` and `schema_migrations` records `056_subtask_promotion.sql` | covered by `test_migration_056_applied_and_rerun_is_noop` |

If a group's count comes in BELOW the number above and the missing tests are not in your §8.0 baseline, stop and diagnose before moving to the next workstream.

### 8.3 Manual smoke (optional but cheap, in the running app)

- Agent page: send a long prompt → Stop mid-stream → partial text stays with "— stopped"; a queued second message auto-runs after; `chat_messages` has exactly one `[Response stopped by the user before completion.]` row.
- Workbench drawer: same stop/queue behavior; native ChatPanel shows "Run stopped." once (never doubled).
- Task Manager / calendars: an assigned Asana subtask appears as `↳ `-prefixed chip on both calendars; hover shows "Subtask of <parent>"; the legend gained the `↳ Subtask` entry.
- Zendesk workspace: a PENDING revision's "Copy content" is enabled, pops the native byte-showing confirm, and lands the stored markdown on the clipboard.

---

## 9. Known limitations / follow-ups to carry (do NOT fix silently)

1. **POSIX kill is not a tree-kill yet.** `kill_process_tree` only walks the tree on win32 (`taskkill /T /F`); on macOS/Linux it is still the single `proc.kill()`, so an MCP child can outlive a Stop until the Mac follow-up lands: spawn the CLI with `start_new_session=True` and kill the process group (`os.killpg`). The function's docstring names this. If the target IS the Mac, port as-is and flag the follow-up to the owner — do not improvise a killpg implementation inside this port.
2. **The native fallback ChatPanel has no stop/queue affordance.** Only the web surfaces (Agent page, drawers) carry Stop and the queue badges. The Qt fallback keeps its old disabled-while-busy composer. Intentional this round.
3. **The 1200 ms post-stop catch-up window.** A tool row that commits during the catch-up delay while a NEW turn has already started is still swallowed by the busy(True) cursor re-baseline. Tiny accepted window — the tree-kill closed the main hazard (an MCP child outliving a Stop and committing rows nobody polls for). Pinned by `test_catchup_skips_when_a_new_turn_already_started`.
4. **Converter change rotates zendesk `draft_content_hash` values.** Every existing draft's recorded copy-review invalidates once after this port lands (the review-record gate correctly reports "changed since you reviewed it"). Expected — tell the operator to re-open the source view before their next copy. Not a bug; do not "fix" the hash.
5. **Parents with ≥ 100 subtasks never run the vanish-dismissal diff.** `len(subs) < _PAGE_SIZE` is the completeness proof; a full-page listing is indistinguishable from a truncation, so the diff is skipped (debug-logged) — fail-closed by design (`test_truncated_listing_skips_dismissal`). Their promoted rows still reconcile; only DELETION detection is deferred.
6. **A queued message is dropped on session switch by design** (FIX 1) — there is no re-queue affordance this round. The stale text referred to the old thread; silent drop was chosen over cross-session replay.
7. **A successful Stop still drains the queue** — intended (stop, then send corrected context). If the operator wants a stopped turn to also cancel their queued message, that is a future feature, not a defect.

*End of guide. Every file changed by `ae0392d` except the regenerated `src/ui/web/dist/index.html` is covered above: 29 production files (§3–§6), 11 test files (§7), 1 migration (§5.1).*
