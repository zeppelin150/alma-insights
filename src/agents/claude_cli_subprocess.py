"""
Alma Insights -- Claude CLI Subprocess Lifecycle

Single-responsibility wrapper around ``subprocess.Popen`` for the
``claude -p`` invocations used by ClaudeCliBridge. Handles:

  - Spawning the process with correct stdio config
  - Writing the prompt to stdin and closing it (text input mode)
  - Draining stderr in a background thread (prevents pipe-fill stalls)
  - Killing the process on timeout / abort
  - Exposing stdout as a line iterator and the final returncode

Kept deliberately small so the parser and the bridge can each test against
this in isolation without faking subprocess internals.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading

logger = logging.getLogger("alma.claude_cli_subprocess")


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


class CliSubprocess:
    """A spawned ``claude`` subprocess with stderr drain + line streaming."""

    _STDERR_RING_LIMIT = 200
    _STDERR_RING_TRIM = 100

    def __init__(self, cmd: list[str], env: dict, prompt: str,
                 cwd: str | None = None) -> None:
        self.cmd = cmd
        self.env = env
        self.prompt = prompt
        # The claude CLI discovers CLAUDE.md files by walking UP from its cwd
        # and injects them into the model context even under a custom
        # --system-prompt-file (live-verified on 2.1.216: ~14.6K tokens of
        # internal engineering directives per turn when cwd is the repo).
        # Callers pass a NEUTRAL cwd to keep persona sessions clean; None
        # inherits the parent's cwd (legacy behavior).
        self.cwd = cwd
        self.proc: subprocess.Popen | None = None
        self.stderr_lines: list[str] = []
        self._stderr_thread: threading.Thread | None = None

    def start(self) -> "CliSubprocess":
        """Spawn the subprocess and start the stderr drain thread.

        Returns self for fluent chaining.
        """
        self.proc = subprocess.Popen(
            self.cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=self.env,
            cwd=self.cwd,
            creationflags=(
                subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            ),
        )
        try:
            self.proc.stdin.write(self.prompt)
            self.proc.stdin.close()
        except Exception as e:
            logger.warning("CliSubprocess: stdin write failed: %s", e)

        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            name=f"claude-cli-stderr-{self.proc.pid}",
            daemon=True,
        )
        self._stderr_thread.start()
        return self

    def stdout_iter(self):
        """Iterate over stdout lines (newline-terminated strings)."""
        if not self.proc or not self.proc.stdout:
            return iter(())
        return iter(self.proc.stdout)

    def wait(self, timeout: float) -> int:
        """Block until the process exits. Raises subprocess.TimeoutExpired
        on timeout (caller should kill + retry/raise as appropriate)."""
        if not self.proc:
            return 0
        return self.proc.wait(timeout=timeout)

    def kill(self) -> None:
        """Force-kill the subprocess AND its children (the MCP server the CLI
        spawned) — see ``kill_process_tree``. Idempotent."""
        kill_process_tree(self.proc)

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def returncode(self) -> int | None:
        return self.proc.returncode if self.proc else None

    def stderr_tail(self, n: int = 5) -> str:
        """Last n stderr lines joined with '; ' for error message context."""
        return "; ".join(self.stderr_lines[-n:])

    def _drain_stderr(self) -> None:
        """Background-thread reader: keeps the stderr pipe from filling.

        Trims to a bounded ring buffer to avoid unbounded memory growth on
        chatty errors.
        """
        try:
            for line in self.proc.stderr:
                self.stderr_lines.append(line.rstrip())
                if len(self.stderr_lines) > self._STDERR_RING_LIMIT:
                    del self.stderr_lines[:self._STDERR_RING_TRIM]
        except Exception:
            pass
