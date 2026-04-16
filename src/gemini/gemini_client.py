"""
Alma Insights — Gemini Client
Wraps the Gemini CLI executable for AI synthesis.

HIPAA COMPLIANCE:
  Alma holds a BAA with Google for Gemini access. The CLI route has
  been tentatively approved by InfoSec. All text sent to Gemini passes
  through mandatory PII redaction (enabled by default, cannot be
  disabled without changing code — the Settings toggle controls an
  ADDITIONAL layer, not the base layer).

  Data flow:
  1. Analysis engine produces aggregated statistics (counts, averages,
     correlation coefficients, p-values, TRC codes, concept labels)
  2. PII redaction filter strips any residual identifiers
  3. Structured prompt is assembled from redacted statistics
  4. Prompt is sent to Gemini CLI as a subprocess argument
  5. Response text is returned to the UI

  What crosses the network: statistics, TRC codes, redacted concept
  labels, and structured analysis instructions.
  What NEVER crosses the network: full_thread text, patient names,
  email addresses, phone numbers, SSNs, member IDs, credit card numbers.
"""

from __future__ import annotations

import os
import subprocess
import re
import json
from pathlib import Path
from datetime import datetime
import logging

logger = logging.getLogger("alma.gemini")

# ── Shared redaction config (HIPAA A1 — single source of truth) ──
_REDACTION_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "redaction_patterns.json"
_redaction_cache = None


def _load_redaction_config():
    """Load redaction patterns from shared JSON config. Cached after first call."""
    global _redaction_cache
    if _redaction_cache is not None:
        return _redaction_cache
    try:
        with open(_REDACTION_CONFIG_PATH, encoding="utf-8") as f:
            _redaction_cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("Redaction config not found, using empty patterns")
        _redaction_cache = {"patterns": [], "aggressive_skip_terms": []}
    return _redaction_cache


class GeminiClient:
    """Subprocess-based Gemini CLI wrapper with mandatory PII redaction.

    Wraps the `gemini` CLI binary for one-shot prompt calls. The CLI
    path is auto-detected at init time if not provided. Every call
    applies PII/PHI redaction before sending — this cannot be disabled
    in production paths (HIPAA constraint).

    Duck-typed to match ClaudeClient via `.generate(prompt, system_prompt, timeout)`.
    See `src.gemini.client_factory.build_client_for_task()` for selecting
    between providers.
    """

    def __init__(self, cli_path: str = "", model: str = "gemini-2.5-flash",
                 temperature: float = 0.2, pii_redaction: bool = True) -> None:
        self.cli_path = cli_path or self._find_cli()
        self.model = model
        self.temperature = temperature
        self.pii_redaction = pii_redaction
        self._usage_tracker = None  # Optional UsageTracker for cost logging

    def _find_cli(self) -> str:
        import shutil
        try:
            from src.data.settings_manager import get_section
            gemini_cfg = get_section("gemini", {})
            path = gemini_cfg.get("cli_path", "")
            if path and Path(path).exists():
                return path
        except Exception:
            pass
        return shutil.which("gemini") or ""

    def is_available(self) -> bool:
        """
        Return True if Gemini is usable — either the CLI binary exists,
        or an API key has been saved (API key mode uses the CLI + env var).
        """
        cli_ok = bool(self.cli_path) and Path(self.cli_path).exists()
        if cli_ok:
            return True
        # API key mode: CLI still needed but key provides auth
        try:
            from src.data import pat_store
            if pat_store.load_setting("gemini_api_key"):
                # Re-check CLI path dynamically in case it was found after init
                return bool(self.cli_path) and Path(self.cli_path).exists()
        except Exception:
            pass
        return False

    def list_models(self, timeout: int = 15) -> list[str]:
        """Query Gemini CLI for available models.

        Returns list of model ID strings (e.g. ['gemini-2.5-flash', 'gemini-2.5-pro']).
        Caches result after first successful call.
        Falls back to built-in defaults on failure.
        """
        if hasattr(self, "_cached_models") and self._cached_models:
            return self._cached_models

        _FALLBACK = [
            "gemini-2.5-flash", "gemini-2.5-pro",
            "gemini-3-flash-preview", "gemini-3.1-pro-preview",
            "gemini-2.5-flash-lite",
        ]

        if not self.cli_path or not Path(self.cli_path).exists():
            return _FALLBACK

        env = os.environ.copy()
        api_key = self._get_api_key()
        if api_key:
            env["GEMINI_API_KEY"] = api_key

        # Try common CLI patterns for listing models
        for cmd in [
            [self.cli_path, "models", "list"],
            [self.cli_path, "--list-models"],
        ]:
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=timeout,
                    encoding="utf-8", errors="replace", env=env,
                )
                if result.returncode == 0 and result.stdout.strip():
                    models = self._parse_model_list(result.stdout)
                    if models:
                        self._cached_models = models
                        return models
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                continue

        return _FALLBACK

    @staticmethod
    def _parse_model_list(output: str) -> list[str]:
        """Extract model IDs from CLI output."""
        models = []
        for line in output.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            # Extract model ID — handle formats like "gemini-2.5-flash" or
            # "models/gemini-2.5-flash" or tabular output with model names
            token = line.split()[0].strip()
            if "/" in token:
                token = token.rsplit("/", 1)[-1]
            if token.startswith("gemini"):
                models.append(token)
        return models

    def _get_api_key(self) -> str:
        """Load the saved Gemini API key, or empty string if not set."""
        try:
            from src.data import pat_store
            return pat_store.load_setting("gemini_api_key") or ""
        except Exception:
            return ""

    def generate(self, prompt: str, system_prompt: str = "",
                 timeout: int = 120) -> str:
        """
        Send a prompt to Gemini CLI and return the response.

        IMPORTANT: The prompt is ALWAYS redacted before sending,
        regardless of the pii_redaction setting. The setting controls
        an additional aggressive redaction pass — the base redaction
        (emails, phones, SSNs, cards, member IDs) always runs.

        If a Gemini API key is saved (API key auth mode), it is injected
        as the GEMINI_API_KEY environment variable for the subprocess so
        the CLI authenticates without needing Google OAuth.
        """
        if not self.cli_path or not Path(self.cli_path).exists():
            raise RuntimeError("Gemini CLI not found. Configure in Settings.")

        # ── MANDATORY base redaction (always runs) ──
        prompt = self._redact_base(prompt)
        if system_prompt:
            system_prompt = self._redact_base(system_prompt)

        # ── Optional aggressive redaction (controlled by Settings toggle) ──
        if self.pii_redaction:
            prompt = self._redact_aggressive(prompt)
            if system_prompt:
                system_prompt = self._redact_aggressive(system_prompt)

        # ── Log what we're sending (for audit) ──
        self._log_outbound(prompt, system_prompt)

        # Build the full prompt — Gemini CLI has no --system or --temperature
        # flags, so we prepend the system prompt to the user prompt.
        full_prompt = prompt
        if system_prompt:
            full_prompt = (
                f"[SYSTEM INSTRUCTIONS]\n{system_prompt}\n"
                f"[END SYSTEM INSTRUCTIONS]\n\n{prompt}"
            )

        # The Gemini CLI's -p flag triggers non-interactive (headless) mode.
        # Its docs say the value is "appended to input on stdin (if any)".
        # We write the prompt to a temp file and use shell input redirection
        # to pipe it to stdin.  This avoids both Windows cmd.exe argument
        # quoting issues and command-line length limits.
        import sys
        import tempfile

        # ── Build subprocess environment ──
        env = os.environ.copy()
        api_key = self._get_api_key()
        if api_key:
            env["GEMINI_API_KEY"] = api_key

        # Write prompt to temp file for stdin redirection
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", encoding="utf-8",
                delete=False
            ) as tmp:
                tmp.write(full_prompt)
                tmp_path = tmp.name

            if sys.platform == "win32":
                # Shell redirect from temp file; -p "" activates headless mode
                shell_cmd = (
                    f'"{self.cli_path}" --model {self.model}'
                    f' -p "" < "{tmp_path}"'
                )
                result = subprocess.run(
                    shell_cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    env=env,
                    shell=True,
                )
            else:
                result = subprocess.run(
                    [self.cli_path, "--model", self.model, "-p", full_prompt],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    env=env,
                )
        finally:
            if tmp_path:
                try:
                    Path(tmp_path).unlink()
                except OSError:
                    pass

        if result.returncode != 0:
            raise RuntimeError(f"Gemini error: {result.stderr}")

        response_text = result.stdout.strip()

        # Log token usage if tracker configured (Pass 5.1)
        if self._usage_tracker:
            try:
                tok_in = self._usage_tracker.estimate_tokens(full_prompt)
                tok_out = self._usage_tracker.estimate_tokens(response_text)
                self._usage_tracker.log_call(
                    source="ai_report",
                    tokens_in=tok_in,
                    tokens_out=tok_out,
                    model=self.model,
                )
            except Exception as _e:
                logger.debug(f"Usage tracking failed: {_e}")

        return response_text

    def _redact_base(self, text: str) -> str:
        """
        Base PII redaction — ALWAYS runs, cannot be disabled.
        Loads patterns from config/redaction_patterns.json (HIPAA A1).
        """
        config = _load_redaction_config()
        for pat in config.get("patterns", []):
            if pat.get("conditional"):
                continue  # skip name_heuristic — handled in aggressive
            flags = re.IGNORECASE if pat.get("flags") == "i" else 0
            text = re.sub(pat["regex"], pat["replace"], text, flags=flags)
        return text

    def _redact_aggressive(self, text: str) -> str:
        """
        Aggressive redaction — removes names and other potential PHI.
        Controlled by the PII Redaction toggle in Settings (default ON).
        Skip terms loaded from config/redaction_patterns.json (HIPAA A1).
        """
        config = _load_redaction_config()
        skip_terms = set(config.get("aggressive_skip_terms", []))

        def _maybe_redact_name(match):
            word1 = match.group(1)
            word2 = match.group(2)
            if word1 in skip_terms or word2 in skip_terms:
                return match.group(0)
            return "[NAME]"

        text = re.sub(
            r'\b([A-Z][a-z]+)\s+([A-Z][a-z]+)\b',
            _maybe_redact_name,
            text
        )
        return text

    def _log_outbound(self, prompt: str, system_prompt: str):
        """
        Log the outbound prompt for audit trail.
        """
        logger.info(
            f"Gemini outbound | system_prompt_len={len(system_prompt or '')} "
            f"| prompt_len={len(prompt)} "
            f"| first_100_chars={prompt[:100]}..."
        )
