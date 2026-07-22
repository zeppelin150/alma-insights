"""
Alma Insights — Chat Engine (shared logic layer)

Pure-logic QObject powering all Gemini/Claude chat interactions.
NOT a widget — consumers own their UI and configure the engine
via callbacks and signals.

Two client strategies:
  - Build-per-message (default): fresh client via build_client_for_task()
    each send(). Simple, picks up settings changes, no stale refs.
  - Warm client (opt-in): consumer injects a pre-built client via
    set_client() to avoid bridge cold-start (Follow-Up Chat only).

Tool execution (opt-in):
  When tools_enabled=True, the engine parses Gemini responses for
  TOOL_CALL patterns, executes them against the local DB, and
  resubmits results in a second LLM call. Max 3 round-trips.

Telemetry (Build 12.0):
  Each response captures estimated tokens, wall-clock latency, model used,
  and passes them to append_message() for storage in chat_messages.
"""

import html
import json
import logging
import re
import sqlite3
import time
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal, QThread

from src.data.connection_factory import get_connection
from src.services import turn_grounding

logger = logging.getLogger("alma.chat_engine")

# Type aliases for consumer callbacks
ContextProvider = Callable[[str, list[dict]], str]
HistoryPacker = Callable[[str, list[dict]], str]
ResponseHandler = Callable[[str], str]

_MAX_TOOL_ROUNDS = 3
_TOOL_CALL_RE = re.compile(r"TOOL_CALL:\s*(\w+)\s+(\{.*?\})", re.DOTALL)

# Unexecuted-tool-call guard (bug 2026-07-21). A model that writes a tool
# invocation as PROSE never actually invokes it, so every "result" it goes
# on to report is unbacked. Detection is deliberately narrow: we match the
# INVOCATION shape only — a well-formed opening tag (anchored on its
# closing ">", so an unterminated "<invoke" mention cannot swallow prose
# until it happens to find a "name="), a closed call block, or a fenced
# block whose language IS a call dialect — and we blank out quoted regions
# first so Renn can still *talk about* the syntax without tripping this.
# The {0,400} bounds are load-bearing, not cosmetic: an unbounded [^>]*?
# rescans the whole remaining response from every "<invoke" mention when the
# text contains no ">", which is quadratic (seconds of main-thread freeze on
# a long doc dump). Real attribute lists are far below the bound.
_FABRICATED_INVOKE_RE = re.compile(
    r"<\s*(?:[A-Za-z][\w.\-]*:)?invoke\b[^>]{0,400}?\bname\s*=\s*[\"']?"
    r"([\w.\-]+)[^>]{0,400}>",
    re.IGNORECASE,
)
# Every dialect that means "a tool call" here: Anthropic XML, the
# <tool_use>/<tool_call> block forms, and this repo's own stream protocol
# (```tool_call / ```tool_code fences — see CLAUDE.md "Stream protocol").
_BLOCK_TAGS = ("function_calls", "function_call", "tool_use", "tool_call")
_BLOCK_OPEN_RES = {
    tag: re.compile(r"<\s*(?:[A-Za-z][\w.\-]*:)?" + tag + r"\b[^>]*>", re.IGNORECASE)
    for tag in _BLOCK_TAGS
}
_BLOCK_CLOSE_RES = {
    tag: re.compile(r"</\s*(?:[A-Za-z][\w.\-]*:)?" + tag + r"\s*>", re.IGNORECASE)
    for tag in _BLOCK_TAGS
}
_TOOL_FENCE_LANGS = frozenset(
    {"tool_call", "tool_code", "tool_use", "function_call", "function_calls"}
)
# Line-anchored so a mid-sentence mention of the directive is not a match.
# Unlike _TOOL_CALL_RE this does NOT require a {json} payload: a directive
# with no parsable args is never dispatched either, so it is just as fake.
_BARE_TOOL_CALL_RE = re.compile(r"(?m)^\s*\**TOOL_CALL:\s*[\"'`]?(\w+)")
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
# A captured attribute value is only worth showing the user if it looks like
# a tool identifier — otherwise we fall back to the unnamed wording.
_TOOL_NAME_RE = re.compile(r"^[A-Za-z][\w.\-]{2,63}$")

_UNEXECUTED_TOOL_CALL_NOTICE = (
    "This turn was discarded. The response contained a tool call{tool} "
    "written as text instead of being run, so no tool was executed and "
    "nothing was looked up — any results it went on to describe are not "
    "backed by real data. Send the request again."
)
# Used instead of the discard when (a) tools genuinely ran this turn, so
# "nothing was looked up" would be false and a real answer would be
# destroyed, or (b) the previous turn was already discarded for the same
# reason — re-sending is deterministic, so discarding again would livelock
# the conversation with no way for the user to see the text.
_UNEXECUTED_TOOL_CALL_BANNER = (
    "Warning: part of this answer wrote a tool call{tool} out as text rather "
    "than running it, so anything it reports from that call may not be backed "
    "by real data. The response follows unchanged."
)

# Adaptive bridge-recycle (F-9): phrases that strongly indicate a
# "tools aren't responding" response as opposed to a legitimate
# "filter matched nothing" response. Tuned to avoid false positives
# on the latter.
_DEGRADED_PHRASES = (
    "i encountered an issue retrieving",
    "i encountered an issue performing",
    "i encountered an issue with",
    "unable to retrieve",
    "was unable to retrieve",
    "encountered some connection issues with the tools",
)
_DEFAULT_RECYCLE_THRESHOLD = 3

# Tool prompt addendum — extracted to src/data/chat_tools/tool_prompts.py
from src.data.chat_tools.tool_prompts import TOOL_PROMPT_ADDENDUM as _TOOL_PROMPT_ADDENDUM


#: Live MCP tool names, memoized. Only consumer is turn_grounding's Tier A
#: drift guard, which auto-disables the "queued background job" rung if a tool
#: that can defer work past the turn ever registers.
_REGISTRY_NAMES: Optional[tuple] = None
#: An unreadable registry must DISABLE that rung rather than guess, so the
#: fallback deliberately carries a deferred-job marker.
_REGISTRY_UNREADABLE = ("__registry_unreadable__background__",)


def _live_tool_names() -> tuple:
    """Names of the tools the chat MCP server actually registers."""
    global _REGISTRY_NAMES
    if _REGISTRY_NAMES is None:
        try:
            from src.mcp.chat_mcp_server import TOOL_SCHEMAS
            _REGISTRY_NAMES = tuple(
                str(t.get("name", "")) for t in TOOL_SCHEMAS
            )
        except Exception as e:  # noqa: BLE001 — never break a turn
            logger.debug("Tool registry unreadable for grounding guard: %s", e)
            _REGISTRY_NAMES = _REGISTRY_UNREADABLE
    return _REGISTRY_NAMES


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars per token for English)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _strip_quoted_spans(text: str) -> tuple[str, bool]:
    """Blank out the regions where a model is QUOTING call syntax rather than
    emitting it — fenced code blocks, inline `code` spans and blockquote
    lines — and report whether a fence whose language is itself a call
    dialect (```tool_call, ```tool_code) was seen.

    Line-based on purpose: a `.*?`-under-DOTALL fence regex backtracks from
    every unclosed opener, which is quadratic on a long response that merely
    mentions one. This is a single linear pass."""
    out: list[str] = []
    in_fence = False
    fence_marker = ""
    tool_fence = False
    saw_tool_fence = False
    for line in text.split("\n"):
        stripped = line.lstrip()
        if in_fence:
            if stripped.startswith(fence_marker):
                in_fence = False
            out.append(line if tool_fence else "")
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence_marker = stripped[:3]
            tool_fence = stripped[3:].strip().lower() in _TOOL_FENCE_LANGS
            saw_tool_fence = saw_tool_fence or tool_fence
            in_fence = True
            out.append(line if tool_fence else "")
            continue
        # "> " only — a lone ">" is the terminator of a multi-line opening
        # tag, not a blockquote, and blanking it would hide the invocation.
        if stripped.startswith("> "):
            out.append("")
            continue
        out.append(_INLINE_CODE_RE.sub("", line))
    return "\n".join(out), saw_tool_fence


def _has_closed_call_block(text: str) -> bool:
    """True if `text` holds an opened-and-closed call block in any dialect.

    Two anchored searches per tag (first opener, then a closer after it) —
    if any closer follows any opener, one follows the FIRST opener too, so
    this is linear and cannot backtrack across openers."""
    for tag in _BLOCK_TAGS:
        opener = _BLOCK_OPEN_RES[tag].search(text)
        if opener and _BLOCK_CLOSE_RES[tag].search(text, opener.end()):
            return True
    return False


def _detect_unexecuted_tool_call(text: str, mcp_mode: bool) -> Optional[str]:
    """Return the tool name if `text` contains a tool call that was written
    out as text instead of executed, "" if the invocation is unnamed, or
    None if the text is clean. Callers MUST test `is not None`.

    Only the XML/fence/`TOOL_CALL:` dialects are recognised. A plain-English
    announcement ("let me search your Drive…") is NOT detected — no shape
    separates it from a model correctly narrating what it is about to do.

    `mcp_mode` is kept for call-site clarity but no longer gates anything:
    reaching this point means the legacy text loop did not dispatch, so an
    unexecuted directive is unexecuted in either mode."""
    if not text:
        return None
    # Entity-escaped markup still renders as a call in the markdown pipeline.
    scan, tool_fence = _strip_quoted_spans(html.unescape(text))
    m = _FABRICATED_INVOKE_RE.search(scan)
    if m:
        return m.group(1)
    if _has_closed_call_block(scan) or tool_fence:
        return ""
    # A literal TOOL_CALL: directive is dispatched only by the legacy text
    # loop above; reaching here means it was never executed, in either mode.
    m = _BARE_TOOL_CALL_RE.search(scan)
    if m:
        return m.group(1)
    return None


# ═══════════════════════════════════════════════════════════
#  Internal worker (private — consumers never touch this)
# ═══════════════════════════════════════════════════════════

class _ChatWorker(QThread):
    """Internal worker thread that runs a single LLM .generate() call.

    Emits `finished(response_text, telemetry_dict)` on success or
    `error(message)` on failure. Consumers should not instantiate
    this directly — use ChatEngine.send() instead.
    """

    # Emits (response_text, telemetry_dict)
    finished = Signal(str, dict)
    error = Signal(str)
    token_emitted = Signal(str)   # per-token text delta (only when streaming)

    def __init__(self, client, prompt: str, system_prompt: str, timeout: int = 180,
                 stream: bool = False):
        super().__init__()
        self._client = client
        self._prompt = prompt
        self._system_prompt = system_prompt
        self._timeout = timeout
        self._stream = stream

    def run(self):
        try:
            start_ms = time.perf_counter()
            result = self._invoke_client()
            elapsed_ms = int((time.perf_counter() - start_ms) * 1000)

            model_used = getattr(self._client, "model", None)
            tokens_in = _estimate_tokens(self._prompt + self._system_prompt)
            tokens_out = _estimate_tokens(result)

            # Capture MCP tool calls if the client tracks them
            tool_calls = getattr(self._client, "_last_tool_calls", [])

            telemetry = {
                "model_used": model_used,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "latency_ms": elapsed_ms,
                "tool_calls": len(tool_calls),
                "tool_names": [t.get("name", "") for t in tool_calls],
            }
            self.finished.emit(result, telemetry)
        except Exception as e:
            self.error.emit(str(e))

    def _invoke_client(self) -> str:
        """Call the client's ``.generate()``. When streaming is enabled AND the
        client exposes an ``on_token`` parameter, forward per-token deltas via
        ``token_emitted`` (a queued cross-thread signal — safe). Otherwise this
        is the exact blocking call as before, so non-streaming clients (and the
        whole-message contract) are unchanged."""
        if self._stream and self._client_supports_on_token():
            return self._client.generate(
                self._prompt, system_prompt=self._system_prompt,
                timeout=self._timeout, on_token=self.token_emitted.emit,
            )
        return self._client.generate(
            self._prompt, system_prompt=self._system_prompt, timeout=self._timeout,
        )

    def _client_supports_on_token(self) -> bool:
        try:
            import inspect
            return "on_token" in inspect.signature(self._client.generate).parameters
        except (TypeError, ValueError):  # builtin/uninspectable — assume no
            return False


# ═══════════════════════════════════════════════════════════
#  Chat Engine
# ═══════════════════════════════════════════════════════════

class ChatEngine(QObject):
    """Shared chat logic layer.

    Consumers configure via constructor args and connect to signals.
    The engine handles: client lifecycle, history, worker threads,
    conversation formatting, and optional tool execution.
    """

    response_ready = Signal(str)
    error_occurred = Signal(str)
    busy_changed = Signal(bool)
    status_update = Signal(str)
    token_streamed = Signal(str)   # per-token text delta while a turn streams
    # Adaptive bridge-recycle (F-9, bug-bash 2026-04-23):
    # fires when N consecutive responses look degraded. Consumers that
    # own a warm client should shut it down and set_client() a fresh one.
    bridge_recycle_requested = Signal()

    def __init__(
        self,
        system_prompt: str,
        task_type: str = "report_generation",
        context_provider: Optional[ContextProvider] = None,
        history_packer: Optional[HistoryPacker] = None,
        response_handler: Optional[ResponseHandler] = None,
        tools_enabled: bool = False,
        use_mcp_tools: bool = False,
        db_path: Optional[str] = None,
        recycle_threshold: int = _DEFAULT_RECYCLE_THRESHOLD,
        stream: bool = False,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._system_prompt = system_prompt
        self._task_type = task_type
        self._context_provider = context_provider
        self._history_packer = history_packer
        self._response_handler = response_handler
        self._tools_enabled = tools_enabled
        self._use_mcp_tools = use_mcp_tools
        self._db_path = db_path
        # Per-token streaming (off by default → whole-message, unchanged for
        # existing consumers). The Agent page opts in; the worker only streams
        # if the active client also exposes an ``on_token`` callback.
        self._stream = stream

        self._history: list[dict] = []
        self._warm_client = None
        self._model_override: Optional[str] = None
        self._worker: Optional[_ChatWorker] = None
        self._tool_rounds = 0
        self._session_filters: dict = {}

        # Adaptive bridge-recycle state (F-9)
        self._recycle_threshold = max(1, int(recycle_threshold))
        self._degraded_streak = 0

        # Unexecuted-tool-call guard: suppress-once, so a retry that trips
        # the same deterministic trigger is not eaten a second time.
        self._last_turn_fabricated = False

        # Layer-2 grounding (TEL-SG). `_turn_evidence` holds THIS turn's
        # captured bytes plus the send-time ledger watermark; it is created in
        # send() and NOT re-created by a legacy tool round-trip, so a follow-up
        # round still carries the turn's original context.
        self._turn_evidence = None
        # Set once a transcript is restored: the `[SYSTEM: operator confirmed …]`
        # pseudo-turns are never persisted to chat_messages, so a reloaded
        # session cannot prove a confirmation happened and Tier A-write must
        # self-disable rather than accuse a real, confirmed write.
        self._history_restored = False

        # Telemetry callback — set by page to persist to chat_messages
        self._telemetry_callback: Optional[Callable] = None

        # Session ID for telemetry association
        self._session_id: Optional[str] = None

    def set_session_id(self, session_id: str | None):
        """Set the active session ID for telemetry association."""
        self._session_id = session_id

    def set_telemetry_callback(self, callback: Callable):
        """Register a callback for persisting response telemetry.

        Callback signature: callback(role, content, telemetry_dict)
        """
        self._telemetry_callback = callback

    def set_session_filters(self, filters: dict):
        """Set session-scoped filters applied to all tool calls."""
        self._session_filters = dict(filters) if filters else {}

    @property
    def session_filters(self) -> dict:
        return dict(self._session_filters)

    # ── Properties ────────────────────────────────────────

    @property
    def history(self) -> list[dict]:
        return list(self._history)

    @property
    def is_busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    # ── History management ────────────────────────────────

    def set_history(self, history: list[dict]):
        self._history = list(history)
        self._last_turn_fabricated = False
        self._history_restored = True

    def append_to_history(self, role: str, content: str):
        self._history.append({"role": role, "content": content})

    def clear_history(self):
        self._history.clear()
        self._last_turn_fabricated = False
        # A NEW chat is not a restored one. Without this, loading any past
        # transcript killed Tier A-write for the whole process — agent_chat's
        # new_session() calls clear_history(), so load-old-chat -> new-chat
        # left the rung permanently inert.
        self._history_restored = False

    # ── Client lifecycle ──────────────────────────────────

    def set_client(self, client):
        """Inject a pre-built client (warm path for Follow-Up Chat)."""
        self._warm_client = client

    def set_model(self, model_id: str):
        """Override model for subsequent send() calls."""
        self._model_override = model_id

    def _build_client(self):
        """Build a fresh client per message (default path)."""
        from src.gemini.client_factory import build_client_for_task
        client = build_client_for_task(self._task_type)
        if client and self._model_override:
            client.model = self._model_override
        return client

    # ── Core: send message ────────────────────────────────

    def send(self, user_message: str):
        """Send a user message. Returns immediately; results via signals."""
        if self.is_busy:
            return

        # Adaptive bridge-recycle (F-9). If the last N responses looked
        # degraded (tools not producing data), drop the warm client and
        # ask the consumer to wire a fresh one. We emit the signal FIRST
        # so the consumer can rebuild before we try to use the client.
        if (
            self._warm_client is not None
            and self._degraded_streak >= self._recycle_threshold
        ):
            self.status_update.emit(
                "Refreshing the analysis bridge to improve answer quality — "
                "this takes a few extra seconds…"
            )
            self._warm_client = None
            self._degraded_streak = 0
            self.bridge_recycle_requested.emit()

        self._history.append({"role": "user", "content": user_message})
        self._tool_rounds = 0

        # Build client
        if self._warm_client is not None:
            client = self._warm_client
            if self._model_override:
                client.model = self._model_override
        else:
            client = self._build_client()

        if client is None:
            self._history.pop()
            self.error_occurred.emit(
                "LLM client not configured. Check Settings > AI Config."
            )
            return

        # Pack prompt
        if self._history_packer:
            prompt = self._history_packer(user_message, self._history)
        else:
            prompt = self._default_pack(user_message)

        # Prepend context
        if self._context_provider:
            ctx = self._context_provider(user_message, self._history)
            if ctx:
                prompt = ctx + "\n\n" + prompt

        # Build system prompt (add tool instructions if enabled)
        # MCP mode: tools are declared via MCP schema — no text addendum needed.
        # Legacy mode: inject TOOL_CALL format instructions into prompt.
        system = self._system_prompt
        if self._tools_enabled and not self._use_mcp_tools:
            system = system + "\n" + _TOOL_PROMPT_ADDENDUM

        # Layer-2 provenance capture (TEL-SG), plus the send-time ledger
        # watermark. CAPTURE `prompt`, NOT `ctx`: `prompt` already carries the
        # injected context, the FULL replayed history (including the
        # `[SYSTEM: operator confirmed …]` pseudo-turns) and this turn's user
        # message, so the corpus survives the ONE-SHOT `[TODAY'S PLAN]` block
        # on turn 2+. A ctx-only capture goes blind there and would recant a
        # TRUE morning briefing — the exact incident this guard exists to
        # prevent.
        self._turn_evidence = self._capture_turn_evidence(prompt, system)

        # Log prompt size for debugging
        total_chars = len(prompt) + len(system)
        logger.debug("Sending prompt: %d chars (prompt=%d, system=%d)",
                      total_chars, len(prompt), len(system))

        # Launch worker
        self.busy_changed.emit(True)
        self.status_update.emit("Gemini is thinking...")
        self._launch_worker(client, prompt, system)

    def _capture_turn_evidence(self, prompt: str, system: str):
        """Build this turn's ``TurnEvidence`` and take the ledger watermark.

        NEVER raises: a capture failure must not break a send, and an absent
        capture makes ``assess`` abstain (it returns UNKNOWN on an empty
        prompt), which is the fail-open direction.
        """
        try:
            # S0 surface gate. The legacy in-process text loop writes ZERO
            # ledger rows (its factory connection trips the chat_messages FK
            # on message_id=''), so judging there would accuse every genuine
            # tool-backed answer on the legacy chat surfaces.
            surface_ok = bool(self._use_mcp_tools and self._db_path)
            evidence = turn_grounding.TurnEvidence(
                surface_ok=surface_ok,
                history_restored=self._history_restored,
            )
            evidence.add("prompt", prompt)
            evidence.add("system", system)
            if surface_ok:
                # DELIBERATELY NOT gated on enablement.turn_grounding.mode.
                # LAYER 1 consumes this watermark too and has no mode key —
                # turning layer 2 off must not silently put layer 1 back on the
                # telemetry counter that is structurally always 0 on the Claude
                # leg. Cost is one read-only connection open (~14 ms on the
                # 47 MB warehouse) on the send path; the chat bridge already
                # opens one every 300 ms on the same thread.
                evidence.mark = turn_grounding.snapshot_tool_ledger(self._db_path)
            return evidence
        except Exception as e:  # noqa: BLE001 — capture is never load-bearing
            logger.debug("Turn-evidence capture failed: %s", e)
            return None

    def _launch_worker(self, client, prompt: str, system_prompt: str):
        """Start a _ChatWorker with the given prompt."""
        self._current_client = client
        self._current_system = system_prompt
        # Disconnect old worker signals to avoid duplicate delivery
        if self._worker is not None:
            try:
                self._worker.finished.disconnect(self._on_worker_finished)
                self._worker.error.disconnect(self._on_worker_error)
                self._worker.token_emitted.disconnect(self._on_worker_token)
            except (RuntimeError, TypeError):
                pass
        self._worker = _ChatWorker(client, prompt, system_prompt, timeout=180,
                                   stream=self._stream)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.error.connect(self._on_worker_error)
        self._worker.token_emitted.connect(self._on_worker_token)
        self._worker.start()

    def _on_worker_token(self, delta: str):
        """Relay a worker token delta out as ``token_streamed`` (main thread)."""
        self.token_streamed.emit(delta)

    # ── Default history packing ───────────────────────────

    def _default_pack(self, user_message: str) -> str:
        parts = []
        for msg in self._history[:-1]:
            role = "User" if msg["role"] == "user" else "Assistant"
            parts.append(f"{role}: {msg['content']}")
        parts.append(f"User: {user_message}")
        return "\n\n".join(parts)

    # ── Worker signal handlers ────────────────────────────

    def _on_worker_finished(self, raw_response: str, telemetry: dict = None):
        # Normalized ONCE, up front: two `{**telemetry, ...}` splats downstream
        # would raise TypeError on a non-mapping, and one of them now runs on
        # every turn rather than only on a fabricated one.
        telemetry = dict(telemetry) if isinstance(telemetry, dict) else {}

        # Check for tool calls if enabled (legacy text-based mode only).
        # MCP mode: tools are handled natively by ACP — no regex parsing.
        if (self._tools_enabled and not self._use_mcp_tools
                and self._tool_rounds < _MAX_TOOL_ROUNDS):
            match = _TOOL_CALL_RE.search(raw_response)
            if match:
                self._handle_tool_call(raw_response, match)
                return

        # Normal completion path
        processed = raw_response
        if self._response_handler:
            processed = self._response_handler(raw_response)

        grounding_opts = turn_grounding.load_options()

        # Unexecuted-tool-call guard: a tool call written as text never ran,
        # so everything after it is unbacked. Normally the whole turn is
        # discarded — the prose is not salvageable, it IS the fabrication.
        fabricated = _detect_unexecuted_tool_call(processed, self._use_mcp_tools)

        # LEDGER-PRIMARY tool ground truth, resolved once and shared by both
        # layers. telemetry["tool_calls"] is structurally ALWAYS 0 on the
        # Claude leg (`_last_tool_calls` is set only by report_bridge_client)
        # and drops ~70 of the 86 registered MCP tools on the Gemini leg
        # (acp_bridge._MCP_TOOL_NAMES is a hardcoded 16-name frozenset), so a
        # telemetry zero is meaningless on BOTH providers. chat_tool_executions
        # is provider-symmetric — both legs wire the identical
        # `-m src.mcp.chat_mcp_server` subprocess — and every row for the turn
        # is durable before this text exists (the persist commits inside
        # dispatch_tool's `finally`, i.e. before the model sees the result).
        tool_evidence = None
        if fabricated is not None or grounding_opts.mode != turn_grounding.MODE_OFF:
            tool_evidence = self._resolve_turn_tools(telemetry)

        answer_text = processed      # the model's own text, what layer 2 judges
        layer1_banner = False
        discarded = False

        if fabricated is not None:
            # If tools genuinely ran, a real tool-backed answer is in here and
            # "nothing was looked up" would be a lie. And if the previous turn
            # was already discarded for this reason, discarding again livelocks
            # the conversation ("send it again" is deterministic). Both cases
            # warn and keep the text instead.
            try:
                tools_ran = int(telemetry.get("tool_calls") or 0)
            except (TypeError, ValueError):
                tools_ran = 0
            ledger_state = (
                tool_evidence.state if tool_evidence is not None
                else turn_grounding.UNKNOWN
            )
            # Strict SUPERSET of the previous rule: the ledger can now say
            # "tools ran" where telemetry structurally cannot, and telemetry is
            # still honoured positive-only. On UNKNOWN evidence (no db_path,
            # legacy text loop, snapshot failed) this is exactly the old
            # `bool(tools_ran) or self._last_turn_fabricated` — unchanged.
            keep_text = (
                ledger_state == turn_grounding.CONFIRMED_RAN
                or bool(tools_ran)
                or self._last_turn_fabricated
            )
            # Body stays out of WARNING: chat responses can carry ticket text,
            # and nothing else in this module logs response bodies.
            logger.warning(
                "Unexecuted tool call in response (tool=%s, chars=%d, "
                "tools_ran=%d, ledger=%s, kept=%s)",
                fabricated or "unnamed", len(processed), tools_ran,
                ledger_state, keep_text,
            )
            logger.debug("Unexecuted-tool-call response body: %s", processed)
            label = f" ({fabricated})" if _TOOL_NAME_RE.match(fabricated or "") else ""
            if keep_text:
                processed = (
                    _UNEXECUTED_TOOL_CALL_BANNER.format(tool=label)
                    + "\n\n" + processed
                )
                layer1_banner = True
            else:
                processed = _UNEXECUTED_TOOL_CALL_NOTICE.format(tool=label)
                discarded = True
            telemetry = {**telemetry, "unexecuted_tool_call": True}
        self._last_turn_fabricated = fabricated is not None

        # Layer 2 — semantic grounding (TEL-SG). Warn-only by construction and
        # SHADOW by default: mode=telemetry records the verdict and changes
        # nothing the user sees. Skipped when layer 1 already discarded (there
        # is no model text left to judge).
        if not discarded and grounding_opts.mode != turn_grounding.MODE_OFF:
            verdict = self._assess_turn_grounding(
                answer_text, tool_evidence, grounding_opts,
            )
            # An S0/off abstain means this surface is EXCLUDED — the legacy
            # text loop, the AI-Reports drilldown chat. Don't decorate a
            # payload the design says we do not touch.
            if verdict is not None and not (
                verdict.state == turn_grounding.STATE_UNKNOWN
                and verdict.gate in ("S0", "off")
            ):
                grounding_telemetry = verdict.as_telemetry()
                show_banner = bool(verdict.banner) and verdict.action == "banner"
                if show_banner and layer1_banner:
                    # Two warnings about one turn and the operator trusts
                    # neither: one banner, both reasons in telemetry.
                    grounding_telemetry = {
                        **grounding_telemetry, "banner_suppressed": "layer1",
                    }
                    show_banner = False
                telemetry = {**telemetry, "turn_grounding": grounding_telemetry}
                if verdict.flagged:
                    # The reason string is a structured <tier>:<subject>:<detail>
                    # audit tag built from closed constant vocabularies — no
                    # corpus text, no response body, ever.
                    logger.warning(
                        "Turn grounding flagged (%s, atoms=%d, ungrounded=%d, "
                        "banner=%s)",
                        verdict.reason, verdict.atom_count,
                        verdict.ungrounded_count, show_banner,
                    )
                if show_banner:
                    processed = verdict.banner + "\n\n" + processed

        self._history.append({"role": "assistant", "content": processed})

        # Adaptive bridge-recycle: update degraded-streak counter based
        # on this response. Streak resets on any clean response. A fabricated
        # call deliberately does NOT feed the streak: recycling the bridge
        # cannot stop a model narrating tool calls, and the post-recycle turn
        # runs on a factory client with no MCP config wired — the very state
        # that provokes the narration.
        if self._is_degraded_response(processed):
            self._degraded_streak += 1
            logger.debug(
                "Degraded response detected; streak=%d/%d",
                self._degraded_streak, self._recycle_threshold,
            )
        else:
            self._degraded_streak = 0

        # Fire telemetry callback so the page can persist to chat_messages
        if self._telemetry_callback:
            try:
                self._telemetry_callback("assistant", processed, telemetry)
            except Exception as e:
                logger.debug("Telemetry callback failed: %s", e)

        self.busy_changed.emit(False)
        self.status_update.emit("")
        self.response_ready.emit(processed)

    # ── Turn grounding (layers 1 + 2 evidence) ───────────────

    def _resolve_turn_tools(self, telemetry: dict):
        """Post-turn ledger probe. Fresh read-only connection, opened and
        closed inside the helper; never cached, never inside a transaction.
        Returns None only if the module itself failed (it never raises)."""
        try:
            evidence = self._turn_evidence
            return turn_grounding.resolve_tool_evidence(
                self._db_path,
                getattr(evidence, "mark", None) if evidence is not None else None,
                telemetry,
                use_mcp=bool(self._use_mcp_tools and self._db_path),
            )
        except Exception as e:  # noqa: BLE001 — fail open to UNKNOWN
            logger.debug("Tool-evidence resolve failed: %s", e)
            return None

    def _assess_turn_grounding(self, text: str, tool_evidence, opts):
        """Run the layer-2 verdict. Returns None when there is nothing to
        judge. The corpus NEVER reaches a log line, a telemetry field or an
        LLM call — it can contain PHI."""
        try:
            evidence = self._turn_evidence
            if evidence is None:
                return None
            evidence.apply_tool_evidence(tool_evidence)
            history_text = "\n".join(
                str(m.get("content") or "") for m in self._history
            )
            return turn_grounding.assess(
                text, evidence, history_text, _live_tool_names(), opts,
            )
        except Exception as e:  # noqa: BLE001 — warn-only check, never fatal
            logger.debug("Turn grounding assess failed: %s", e)
            return None

    def _on_worker_error(self, error_text: str):
        self.busy_changed.emit(False)
        self.status_update.emit("")
        self.error_occurred.emit(error_text)
        # Worker-level errors (bridge crashed, timeout, etc.) count as
        # degraded — they're the strongest signal that the warm bridge
        # needs to be recycled.
        self._degraded_streak += 1

    # ── Adaptive recycle helpers ─────────────────────────────

    @staticmethod
    def _is_degraded_response(text: str) -> bool:
        """Detect a 'tools not working' response.

        True only for phrases that specifically indicate tool-infrastructure
        failure, NOT for legitimate 'no matches found' or 'please clarify'
        responses. Tuned conservatively to avoid false-positive recycles
        on genuinely empty queries (like out-of-scope payer names).
        """
        if not text:
            return True
        lower = text.lower()
        return any(p in lower for p in _DEGRADED_PHRASES)

    @property
    def degraded_streak(self) -> int:
        """Number of consecutive degraded responses (F-9 observability)."""
        return self._degraded_streak

    def reset_degraded_streak(self) -> None:
        """Explicit reset — used after a caller-driven recycle."""
        self._degraded_streak = 0

    # ── Tool execution loop ───────────────────────────────

    def _handle_tool_call(self, full_response: str, match: re.Match):
        """Parse tool call, execute, resubmit with results."""
        tool_name = match.group(1)
        args_str = match.group(2)
        self._tool_rounds += 1

        self.status_update.emit(f"Querying database ({tool_name})...")
        logger.info("Tool call #%d: %s %s", self._tool_rounds, tool_name, args_str)

        try:
            args = json.loads(args_str)
        except json.JSONDecodeError:
            args = {}

        result = self._execute_tool(tool_name, args)

        # Fold the tool's own output into the turn corpus. The evidence object
        # is deliberately NOT re-created here, so the follow-up round still
        # carries this turn's original context (reading self._worker._prompt
        # instead would silently lose it).
        if self._turn_evidence is not None:
            self._turn_evidence.add("tool_result", result)

        # Build follow-up prompt with tool results
        followup = (
            f"{full_response}\n\n"
            f"TOOL_RESULT ({tool_name}):\n{result}\n\n"
            f"Now continue your response using the data above. "
            f"Do not repeat the tool call."
        )

        self.status_update.emit("Gemini is analyzing results...")
        self._launch_worker(self._current_client, followup, self._current_system)

    def _execute_tool(self, tool_name: str, args: dict) -> str:
        """Execute a tool against the local DB. Returns JSON string."""
        if not self._db_path:
            return json.dumps({"error": "No database configured for tools"})

        try:
            conn = get_connection(self._db_path)
            # Use new unified dispatch (session_filters from session)
            from src.data.chat_tools.registry import dispatch_tool
            result = dispatch_tool(
                tool_name, args, conn,
                session_filters=getattr(self, "_session_filters", {}),
                session_id=self._session_id,
            )
            conn.close()
            return result
        except Exception as e:
            logger.warning("Tool %s failed: %s", tool_name, e)
            return json.dumps({"error": str(e)})


# ═══════════════════════════════════════════════════════════
#  Legacy tool dispatch removed — all tools now in
#  src/data/chat_tools/ (registry.py dispatches to
#  fast_path.py, thread_tools.py, report_tools.py)
# ═══════════════════════════════════════════════════════════
