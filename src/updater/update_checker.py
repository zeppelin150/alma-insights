"""
Alma Insights — Update Checker

Checks a GitHub release feed for a newer version of the app. Three
auth modes are supported:

    disabled     — no check runs; every call emits up_to_date.
    pat          — fine-grained PAT pulled from the OS keyring.
    github_app   — JWT-minted installation token (Phase 3, optional).

Repo and auth_mode are read from settings.yaml `updates.*`. The secret
token is read from the OS keyring under the key `github_update_token`.
Legacy settings — `github_pat` under `updates`, or a plaintext PAT in
`~/.alma-insights/credentials.json` — are honoured as a fallback.

Usage:
    checker = build_default_update_checker(parent=self)
    checker.update_available.connect(...)
    checker.check()                         # runs in background thread
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from threading import Thread
from typing import Literal

from PySide6.QtCore import QObject, Signal

from src import VERSION

logger = logging.getLogger("alma.updater")

AuthMode = Literal["disabled", "pat", "github_app"]

# Default repo kept for backwards compatibility with the existing
# Settings → Updates tab. Real users override this in settings.yaml.
DEFAULT_OWNER = "alma-health"
DEFAULT_REPO = "alma-insights"
DEFAULT_TOKEN_KEY = "github_update_token"  # matches pat_store._SECRET_KEYS

_CHECK_TIMEOUT = 15  # seconds


# ──────────────────────────────────────────────────────────────────
# Version comparison helpers (public for Settings tab + tests)
# ──────────────────────────────────────────────────────────────────

def _parse_version(tag: str) -> tuple:
    """Parse a semver tag like 'v1.2.3' or '1.2.3-rc1' into a tuple."""
    tag = tag.lstrip("vV").strip()
    if "-" in tag:
        tag = tag.split("-", 1)[0]
    try:
        return tuple(int(p) for p in tag.split("."))
    except (ValueError, TypeError):
        return (0, 0, 0)


def _is_newer(current: str, candidate: str) -> bool:
    """True iff candidate parses to a strictly newer version than current."""
    return _parse_version(candidate) > _parse_version(current)


def normalize_github_repo(raw: str) -> str:
    """Normalize a repo identifier to ``owner/repo``.

    The Settings field is labelled "REPO URL", so users routinely paste a full
    ``https://github.com/owner/repo`` URL (or an SSH ``git@github.com:owner/repo.git``
    form, or a link with a trailing ``/tree/main`` path). Without this the API
    URL becomes ``.../repos/https://github.com/owner/repo/releases/latest`` and
    every check 404s ("Release repo not found"). Accepts a bare ``owner/repo``
    unchanged. Returns ``""`` for empty input.
    """
    s = (raw or "").strip()
    if not s:
        return ""
    # strip an optional scheme + optional www + the github.com host and its
    # separator ('/' for https, ':' for ssh).
    s = re.sub(r"^(?:git@|https?://)?(?:www\.)?github\.com[:/]+", "", s, flags=re.IGNORECASE)
    s = s.strip().strip("/")
    if s.lower().endswith(".git"):
        s = s[:-4]
    parts = [p for p in s.split("/") if p]
    return f"{parts[0]}/{parts[1]}" if len(parts) >= 2 else s


# ──────────────────────────────────────────────────────────────────
# UpdateChecker
# ──────────────────────────────────────────────────────────────────

class UpdateChecker(QObject):
    """Polls a GitHub release feed and emits one of three signals."""

    update_available = Signal(str, str, str)  # (current, new, html_url)
    up_to_date = Signal()
    check_failed = Signal(str)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        releases_url: str | None = None,
        github_pat: str | None = None,
        auth_mode: AuthMode = "pat",
    ) -> None:
        super().__init__(parent)
        self._url = releases_url or _default_releases_url()
        self._pat = github_pat or ""
        self._auth_mode: AuthMode = auth_mode
        # Last successful release JSON's assets array — populated only
        # after `update_available` has fired. Consumers (Settings UI's
        # Install handler, splash) read this to feed the manifest fetcher
        # without re-hitting the GitHub API. None until the first check
        # succeeds.
        self.last_assets: list[dict] | None = None
        self.last_token: str = ""    # so consumers can authenticate the manifest fetch

    # ── public ──────────────────────────────────────────────────

    def check(self) -> None:
        """Launch the check in a background thread."""
        if self._auth_mode == "disabled":
            logger.info("Update check skipped — auth_mode=disabled")
            self.up_to_date.emit()
            return
        Thread(target=self._do_check, daemon=True).start()

    # ── internal ────────────────────────────────────────────────

    def _do_check(self) -> None:
        try:
            response_json = self._fetch_latest_release()
        except _CheckerError as exc:
            self.check_failed.emit(str(exc))
            return

        tag = response_json.get("tag_name") or ""
        if not tag:
            self.check_failed.emit("No tag_name in GitHub release response")
            return

        # Stash the assets list + token so the install path can resolve
        # the manifest without a second GitHub round-trip. Set BEFORE
        # emitting so synchronous slots can read it.
        self.last_assets = response_json.get("assets") or []
        self.last_token = self._pat

        if _is_newer(VERSION, tag):
            html_url = response_json.get("html_url", "")
            logger.info("Update available: %s -> %s", VERSION, tag)
            self.update_available.emit(VERSION, tag.lstrip("vV"), html_url)
        else:
            logger.info("Up to date (current=%s, latest=%s)", VERSION, tag)
            self.up_to_date.emit()

    def _fetch_latest_release(self) -> dict:
        """Do the HTTP call with mode-appropriate auth. Raises _CheckerError."""
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": f"AlmaInsights/{VERSION}",
        }
        if self._pat:
            headers["Authorization"] = f"Bearer {self._pat}"

        req = urllib.request.Request(self._url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=_CHECK_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                # Ambiguous on its own — disambiguate before reporting.
                raise _CheckerError(self._diagnose_404(headers)) from exc
            raise _CheckerError(_http_error_message(exc)) from exc
        except urllib.error.URLError as exc:
            raise _CheckerError(f"Network error: {exc.reason}") from exc
        except (ValueError, OSError) as exc:  # json decode / truncated read
            raise _CheckerError(f"Unexpected response: {exc}") from exc

    def _diagnose_404(self, headers: dict) -> str:
        """Explain WHICH 404 this is.

        GitHub returns 404 from ``/releases/latest`` in two very different
        situations: the repo has no published releases yet, OR the caller cannot
        see the repo at all (private repos 404 rather than 403, so existence is
        not leaked). The old message only ever blamed ``updates.github_repo``,
        which sends users hunting a settings bug when the real answer is usually
        "you haven't published a release yet". Probe the repo itself to tell them.
        """
        repo_url = self._url.split("/releases/")[0]
        try:
            req = urllib.request.Request(repo_url, headers=headers)
            with urllib.request.urlopen(req, timeout=_CHECK_TIMEOUT):
                pass
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return (f"GitHub rejected the update token (HTTP {exc.code}) — "
                        "check the token's permissions.")
            return ("Repo not found, or the token cannot see it. Check "
                    "updates.github_repo — and note a fine-grained PAT must "
                    "explicitly grant Contents: Read on THIS repository.")
        except Exception:  # noqa: BLE001 — fall back to the generic message
            return "Release repo not found — check updates.github_repo in settings"
        return ("Repo reached, but it has no published Releases yet. The updater "
                "only sees published Releases — push a v* tag to run the release "
                "workflow, then check again.")


# ──────────────────────────────────────────────────────────────────
# Factories (what the startup check and Settings tab use)
# ──────────────────────────────────────────────────────────────────

def build_default_update_checker(parent: QObject | None = None) -> UpdateChecker:
    """Build an UpdateChecker from settings.yaml + the OS keyring."""
    auth_mode, releases_url, token = _resolve_config_and_token()
    return UpdateChecker(
        parent=parent,
        releases_url=releases_url,
        github_pat=token,
        auth_mode=auth_mode,
    )


def _resolve_config_and_token() -> tuple[AuthMode, str, str]:
    """Read `updates.*` from settings and the token from the keyring."""
    try:
        from src.data.settings_manager import get_section
        cfg = get_section("updates", {}) or {}
    except Exception:  # noqa: BLE001 — settings must never block updates logic
        cfg = {}

    # 2026-05-07: default flipped from "disabled" to "pat" once the bundled-
    # token loader landed. With a bundled token an out-of-the-box install
    # polls for updates — the security boundary is the token's GitHub
    # permissions (Contents: Read-only on a single repo), not its presence.
    # Existing installs with `auth_mode: disabled` explicitly set keep that
    # value; the change only affects fresh installs / wiped settings.
    mode_raw = str(cfg.get("auth_mode", "pat")).lower().strip()
    mode: AuthMode = mode_raw if mode_raw in ("disabled", "pat", "github_app") else "disabled"

    # Defence in depth: normalize here too, so a full-URL value already sitting
    # in settings.yaml (saved before the input was normalized) still resolves.
    repo = normalize_github_repo(cfg.get("github_repo", "")) or f"{DEFAULT_OWNER}/{DEFAULT_REPO}"
    releases_url = f"https://api.github.com/repos/{repo}/releases/latest"

    token = _load_token(mode, cfg)
    return mode, releases_url, token


def _load_token(mode: AuthMode, cfg: dict) -> str:
    """Load the auth token appropriate for the current mode."""
    if mode == "disabled":
        return ""

    if mode == "github_app":
        try:
            from src.updater.github_app_auth import mint_installation_token
            return mint_installation_token(cfg) or ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("GitHub App auth unavailable: %s", exc)
            return ""

    # mode == "pat" — three-tier fallback:
    #   1) keyring under DEFAULT_TOKEN_KEY (admin/user override pasted in
    #      Settings → Updates → Advanced)
    #   2) legacy keyring under "github_pat" (pre-Phase-3 Settings UI)
    #   3) bundled token shipped with the build (zero-friction for end
    #      users). The bundled token is the lowest priority so a paste
    #      override always wins.
    return _load_pat_token()


def _load_pat_token() -> str:
    """Three-tier PAT lookup. Pure function pulled out for testability.

    Each tier is its own try/except so a transient failure on one (e.g.
    the keyring is locked) doesn't block the next.
    """
    # Tier 1 + 2: keyring
    try:
        from src.data import pat_store
        token = pat_store.load_setting(DEFAULT_TOKEN_KEY)
        if token:
            _log_token_source("keyring", token)
            return token
        legacy = pat_store.load_setting("github_pat")
        if legacy:
            _log_token_source("keyring (legacy)", legacy)
            return legacy
    except Exception as exc:  # noqa: BLE001
        logger.warning("Keyring token lookup failed: %s", exc)

    # Tier 3: bundled token
    try:
        from src.updater._bundled_token import get_bundled_token
        bundled = get_bundled_token()
        if bundled:
            _log_token_source("bundled", bundled)
            return bundled
    except Exception as exc:  # noqa: BLE001
        logger.warning("Bundled token lookup failed: %s", exc)

    return ""


def _log_token_source(source: str, token: str) -> None:
    """One-line INFO log identifying which token tier won.

    Logs only the safe fingerprint, never the token itself. Lets
    support engineers answer "which release PAT is this install using?"
    from the user's log file without asking them to dig in keyrings.
    """
    try:
        from src.updater._token_obfuscation import fingerprint
        logger.info("Update auth: using %s token (%s)", source, fingerprint(token))
    except Exception:  # noqa: BLE001 — logging must never raise
        pass


def _default_releases_url() -> str:
    return f"https://api.github.com/repos/{DEFAULT_OWNER}/{DEFAULT_REPO}/releases/latest"


# ──────────────────────────────────────────────────────────────────
# Error helpers
# ──────────────────────────────────────────────────────────────────

class _CheckerError(Exception):
    """Internal marker for errors already translated to a user-facing string."""


def _http_error_message(exc: urllib.error.HTTPError) -> str:
    if exc.code == 401:
        return "GitHub authentication failed — check your update token"
    if exc.code == 403:
        return "GitHub API rate limit or permission denied"
    if exc.code == 404:
        return "Release repo not found — check updates.github_repo in settings"
    return f"GitHub API error: {exc.code}"
