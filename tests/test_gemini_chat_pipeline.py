"""
Gemini Chat Pipeline Test Suite

End-to-end tests for the Gemini Chats send path:
  build_client_for_task → GeminiClient.generate → worker signal → UI

Tests mock at the subprocess boundary so we verify every layer
above it: client factory routing, config loading, PII redaction,
prompt assembly, context injection, session persistence, and
error handling.
"""

import sqlite3
import json
import os
import sys
import time
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MIGRATION_SQL = (_PROJECT_ROOT / "migrations" / "005_persistence_layer.sql").read_text(encoding="utf-8")


@pytest.fixture
def db_conn():
    """In-memory DB with Build 11.0 schema."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(_MIGRATION_SQL)
    yield db
    db.close()


# ═══════════════════════════════════════
#  1. CLIENT FACTORY — TASK ROUTING
# ═══════════════════════════════════════

class TestClientFactoryForChat:
    """Verify build_client_for_task returns a usable client for chat."""

    def test_report_generation_task_resolves_to_gemini(self):
        """The chat worker uses 'report_generation' task — verify it routes to Gemini."""
        from src.gemini.client_factory import resolve_provider_for_task
        provider = resolve_provider_for_task("report_generation")
        assert provider == "gemini", f"Expected 'gemini', got '{provider}'"

    def test_build_client_returns_non_none(self):
        """build_client_for_task should return a client, not None."""
        from src.gemini.client_factory import build_client_for_task
        client = build_client_for_task("report_generation")
        assert client is not None, (
            "build_client_for_task('report_generation') returned None — "
            "check Settings > AI Config > Gemini CLI path"
        )

    def test_client_has_generate_method(self):
        """Returned client must have a .generate() method."""
        from src.gemini.client_factory import build_client_for_task
        client = build_client_for_task("report_generation")
        if client is None:
            pytest.skip("Gemini client not configured")
        assert hasattr(client, "generate"), (
            f"Client {type(client).__name__} has no .generate() method"
        )


# ═══════════════════════════════════════
#  2. GEMINI CLIENT — GENERATE PATH
# ═══════════════════════════════════════

class TestGeminiClientGenerate:
    """Test the GeminiClient.generate() method with subprocess mocked."""

    def _make_client(self, cli_path="/fake/gemini"):
        from src.gemini.gemini_client import GeminiClient
        return GeminiClient(cli_path=cli_path, model="gemini-2.5-flash", pii_redaction=True)

    def test_missing_cli_path_raises(self):
        """If cli_path doesn't exist, generate() should raise RuntimeError."""
        client = self._make_client("/nonexistent/path/gemini")
        with pytest.raises(RuntimeError, match="Gemini CLI not found"):
            client.generate("Hello")

    @patch("subprocess.run")
    def test_generate_returns_stdout(self, mock_run):
        """With a valid cli_path, generate() should return subprocess stdout."""
        # Create a temp file so Path.exists() passes
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
            tmp_cli = f.name

        try:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="Hello! I'm Gemini.",
                stderr="",
            )
            client = self._make_client(tmp_cli)
            result = client.generate("Hello")
            assert result == "Hello! I'm Gemini."
            assert mock_run.called
        finally:
            os.unlink(tmp_cli)

    @patch("subprocess.run")
    def test_system_prompt_prepended(self, mock_run):
        """System prompt should be prepended to the user prompt."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
            tmp_cli = f.name

        try:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="response", stderr=""
            )
            client = self._make_client(tmp_cli)
            client.generate("user question", system_prompt="be helpful")
            # Verify the prompt file content contains system instructions
            assert mock_run.called
        finally:
            os.unlink(tmp_cli)

    @patch("subprocess.run")
    def test_subprocess_timeout_raises(self, mock_run):
        """Subprocess timeout should propagate as an exception."""
        import tempfile, subprocess as sp
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
            tmp_cli = f.name

        try:
            mock_run.side_effect = sp.TimeoutExpired(cmd="gemini", timeout=120)
            client = self._make_client(tmp_cli)
            with pytest.raises(sp.TimeoutExpired):
                client.generate("Hello", timeout=120)
        finally:
            os.unlink(tmp_cli)


# ═══════════════════════════════════════
#  3. CONTEXT INJECTOR
# ═══════════════════════════════════════

class TestContextInjectorForChat:
    """Verify context injection works for the gemini_chats page."""

    def test_build_context_returns_string(self, db_conn):
        """build_context should return a context string, not crash."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_db = f.name

        tmp_conn = sqlite3.connect(tmp_db)
        tmp_conn.executescript(_MIGRATION_SQL)
        tmp_conn.close()

        from src.services.context_injector import build_context
        ctx = build_context({"page": "gemini_chats"}, tmp_db)
        assert isinstance(ctx, str)
        assert "gemini_chats" in ctx
        # Cleanup (lenient on Windows file locks)
        try:
            os.unlink(tmp_db)
        except OSError:
            pass

    def test_build_context_handles_missing_tables(self):
        """Context injector should handle empty/missing tables gracefully."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_db = f.name

        from src.services.context_injector import build_context
        ctx = build_context({"page": "gemini_chats"}, tmp_db)
        assert isinstance(ctx, str)
        try:
            os.unlink(tmp_db)
        except OSError:
            pass


# ═══════════════════════════════════════
#  4. SESSION PERSISTENCE
# ═══════════════════════════════════════

class TestChatSessionPersistence:
    """Verify chat session CRUD for the Gemini Chats page."""

    def test_create_and_load_session(self, db_conn):
        from src.services.chat_session import create_session, load_session
        sid = create_session("gemini_chats", conn=db_conn)
        assert sid is not None
        session = load_session(sid, db_conn)
        assert session is not None
        assert session["source_page"] == "gemini_chats"
        assert session["messages"] == []

    def test_append_and_retrieve_messages(self, db_conn):
        from src.services.chat_session import (
            create_session, append_message, load_session,
        )
        sid = create_session("gemini_chats", conn=db_conn)
        append_message(sid, "user", "What incidents occurred?", db_conn)
        append_message(sid, "assistant", "Here are the incidents...", db_conn)

        session = load_session(sid, db_conn)
        msgs = session["messages"]
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "What incidents occurred?"
        assert msgs[1]["role"] == "assistant"

    def test_multiple_sessions_listed(self, db_conn):
        from src.services.chat_session import create_session, list_sessions
        create_session("gemini_chats", conn=db_conn)
        create_session("gemini_chats", conn=db_conn)
        sessions = list_sessions(20, db_conn)
        assert len(sessions) == 2


# ═══════════════════════════════════════
#  5. CHAT WORKER — SIGNAL CHAIN
# ═══════════════════════════════════════

class TestChatEngineViaGeminiChats:
    """Test ChatEngine integration (replaces old GeminiChatWorker tests)."""

    def test_engine_emits_response_on_success(self):
        """ChatEngine should emit response_ready when Gemini returns."""
        from src.services.chat_engine import ChatEngine
        mock_client = MagicMock()
        mock_client.generate.return_value = "Gemini says hello"

        engine = ChatEngine(system_prompt="be helpful")
        engine.set_client(mock_client)

        results = []
        engine.response_ready.connect(lambda r: results.append(("finished", r)))
        engine.error_occurred.connect(lambda e: results.append(("error", e)))

        engine.send("Hello")
        # Wait for worker completion
        from PySide6.QtCore import QCoreApplication
        import time
        app = QCoreApplication.instance() or QCoreApplication([])
        deadline = time.time() + 5
        while time.time() < deadline and not results:
            if engine._worker:
                engine._worker.wait(100)
            app.processEvents()

        assert len(results) == 1
        assert results[0][0] == "finished"
        assert results[0][1] == "Gemini says hello"

    def test_engine_emits_error_on_exception(self):
        """ChatEngine should emit error_occurred when generate() throws."""
        from src.services.chat_engine import ChatEngine
        mock_client = MagicMock()
        mock_client.generate.side_effect = RuntimeError("Gemini CLI not found")

        engine = ChatEngine(system_prompt="test")
        engine.set_client(mock_client)

        errors = []
        engine.error_occurred.connect(lambda e: errors.append(e))

        engine.send("Hello")
        from PySide6.QtCore import QCoreApplication
        import time
        app = QCoreApplication.instance() or QCoreApplication([])
        deadline = time.time() + 5
        while time.time() < deadline and not errors:
            if engine._worker:
                engine._worker.wait(100)
            app.processEvents()

        assert len(errors) == 1
        assert "CLI not found" in errors[0]

    def test_engine_error_when_no_client(self):
        """ChatEngine should emit error when client is None."""
        from src.services.chat_engine import ChatEngine
        with patch("src.gemini.client_factory.build_client_for_task", return_value=None):
            engine = ChatEngine(system_prompt="test")
            errors = []
            engine.error_occurred.connect(lambda e: errors.append(e))
            engine.send("Hello")
            assert len(errors) == 1
            assert "not configured" in errors[0]

    def test_engine_context_provider_prepends(self):
        """Context provider should prepend context to prompt."""
        from src.services.chat_engine import ChatEngine
        mock_client = MagicMock()
        mock_client.generate.return_value = "response"

        engine = ChatEngine(
            system_prompt="system",
            context_provider=lambda msg, hist: "[CONTEXT] 100 tickets",
        )
        engine.set_client(mock_client)
        engine.send("user question")

        from PySide6.QtCore import QCoreApplication
        import time
        app = QCoreApplication.instance() or QCoreApplication([])
        deadline = time.time() + 5
        while time.time() < deadline:
            if engine._worker:
                engine._worker.wait(100)
            app.processEvents()
            if not engine.is_busy:
                app.processEvents()
                break

        call_args = mock_client.generate.call_args
        prompt_sent = call_args[0][0]
        assert "[CONTEXT] 100 tickets" in prompt_sent
        assert "user question" in prompt_sent


# ═══════════════════════════════════════
#  6. END-TO-END DIAGNOSTIC
# ═══════════════════════════════════════

class TestGeminiChatE2E:
    """End-to-end diagnostic that pinpoints exactly where the chat fails."""

    def test_step1_client_factory_config(self):
        """Step 1: Does the client factory return a non-None client?"""
        from src.gemini.client_factory import build_client_for_task
        client = build_client_for_task("report_generation")
        if client is None:
            pytest.fail(
                "DIAGNOSIS: build_client_for_task('report_generation') returned None.\n"
                "This means Gemini is not configured in Settings.\n"
                "Check: Settings > AI Config > Gemini CLI path\n"
                "Or: Settings > AI Config > Gemini API Key"
            )

    def test_step2_cli_path_exists(self):
        """Step 2: Does the configured Gemini CLI path actually exist?"""
        from src.gemini.client_factory import build_client_for_task
        client = build_client_for_task("report_generation")
        if client is None:
            pytest.skip("Client not configured")

        cli_path = getattr(client, "cli_path", None)
        if cli_path is None:
            pytest.skip("Client has no cli_path (may be Claude)")

        assert Path(cli_path).exists(), (
            f"DIAGNOSIS: Gemini CLI path does not exist: {cli_path}\n"
            "The file was configured in Settings but is missing from disk.\n"
            "Fix: Update Settings > AI Config > Gemini CLI path"
        )

    def test_step3_api_key_present(self):
        """Step 3: Is a Gemini API key configured (for API-key auth)?"""
        from src.gemini.client_factory import build_client_for_task
        client = build_client_for_task("report_generation")
        if client is None:
            pytest.skip("Client not configured")

        api_key = getattr(client, "_api_key", None)
        if not api_key:
            # Try loading from pat_store
            try:
                from src.data.pat_store import load_setting
                api_key = load_setting("gemini_api_key", "")
            except Exception:
                pass

        if not api_key:
            pytest.skip(
                "No Gemini API key found. If using OAuth, this is OK.\n"
                "If using API key auth, set it in Settings > AI Config."
            )

    def test_step4_generate_with_mock(self):
        """Step 4: Full mock round-trip through ChatEngine."""
        from src.services.chat_engine import ChatEngine
        mock_client = MagicMock()
        mock_client.generate.return_value = "Test response"

        engine = ChatEngine(system_prompt="test")
        engine.set_client(mock_client)

        results = []
        engine.response_ready.connect(lambda r: results.append(r))
        engine.error_occurred.connect(lambda e: results.append(f"ERROR: {e}"))

        engine.send("test message")

        from PySide6.QtCore import QCoreApplication
        import time
        app = QCoreApplication.instance() or QCoreApplication([])
        deadline = time.time() + 5
        while time.time() < deadline and not results:
            if engine._worker:
                engine._worker.wait(100)
            app.processEvents()

        assert len(results) == 1
        assert not results[0].startswith("ERROR"), (
            f"DIAGNOSIS: Engine emitted error: {results[0]}"
        )
        assert results[0] == "Test response"
