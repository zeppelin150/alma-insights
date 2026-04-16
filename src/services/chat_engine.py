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

import json
import logging
import re
import sqlite3
import time
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal, QThread

from src.data.connection_factory import get_connection

logger = logging.getLogger("alma.chat_engine")

# Type aliases for consumer callbacks
ContextProvider = Callable[[str, list[dict]], str]
HistoryPacker = Callable[[str, list[dict]], str]
ResponseHandler = Callable[[str], str]

_MAX_TOOL_ROUNDS = 3
_TOOL_CALL_RE = re.compile(r"TOOL_CALL:\s*(\w+)\s+(\{.*?\})", re.DOTALL)

# Tool prompt addendum — extracted to src/data/chat_tools/tool_prompts.py
from src.data.chat_tools.tool_prompts import TOOL_PROMPT_ADDENDUM as _TOOL_PROMPT_ADDENDUM


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars per token for English)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


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

    def __init__(self, client, prompt: str, system_prompt: str, timeout: int = 180):
        super().__init__()
        self._client = client
        self._prompt = prompt
        self._system_prompt = system_prompt
        self._timeout = timeout

    def run(self):
        try:
            start_ms = time.perf_counter()
            result = self._client.generate(
                self._prompt, system_prompt=self._system_prompt,
                timeout=self._timeout,
            )
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

        self._history: list[dict] = []
        self._warm_client = None
        self._model_override: Optional[str] = None
        self._worker: Optional[_ChatWorker] = None
        self._tool_rounds = 0
        self._session_filters: dict = {}

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

    def append_to_history(self, role: str, content: str):
        self._history.append({"role": role, "content": content})

    def clear_history(self):
        self._history.clear()

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

        # Log prompt size for debugging
        total_chars = len(prompt) + len(system)
        logger.debug("Sending prompt: %d chars (prompt=%d, system=%d)",
                      total_chars, len(prompt), len(system))

        # Launch worker
        self.busy_changed.emit(True)
        self.status_update.emit("Gemini is thinking...")
        self._launch_worker(client, prompt, system)

    def _launch_worker(self, client, prompt: str, system_prompt: str):
        """Start a _ChatWorker with the given prompt."""
        self._current_client = client
        self._current_system = system_prompt
        # Disconnect old worker signals to avoid duplicate delivery
        if self._worker is not None:
            try:
                self._worker.finished.disconnect(self._on_worker_finished)
                self._worker.error.disconnect(self._on_worker_error)
            except (RuntimeError, TypeError):
                pass
        self._worker = _ChatWorker(client, prompt, system_prompt, timeout=180)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

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
        if telemetry is None:
            telemetry = {}

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
        self._history.append({"role": "assistant", "content": processed})

        # Fire telemetry callback so the page can persist to chat_messages
        if self._telemetry_callback:
            try:
                self._telemetry_callback("assistant", processed, telemetry)
            except Exception as e:
                logger.debug("Telemetry callback failed: %s", e)

        self.busy_changed.emit(False)
        self.status_update.emit("")
        self.response_ready.emit(processed)

    def _on_worker_error(self, error_text: str):
        self.busy_changed.emit(False)
        self.status_update.emit("")
        self.error_occurred.emit(error_text)

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
