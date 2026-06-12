"""Off-thread Google OAuth flow runner.

``google_oauth.run_interactive_flow()`` and ``reconnect()`` block (the
interactive one opens a browser + serves a localhost callback), so they
must run off the Qt main thread. The worker emits the minimal record (or
None for a silent reconnect) on success and a redacted message on error;
the panel stores the record + refreshes on the main thread.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal


def _redact(msg: str) -> str:
    """Strip anything token-shaped from an error string before display.

    Character classes include URL/base64/JWT separators (/ . + = %) so a
    multi-segment access token or URL-encoded refresh token is fully masked
    rather than truncated at the first separator; also covers the GCP
    client_secret (GOCSPX-) and a generic client_secret=… echo."""
    import re
    msg = re.sub(r"1//[A-Za-z0-9_./+=%\-]+", "1//<redacted>", msg)
    msg = re.sub(r"ya29\.[A-Za-z0-9_./+=%\-]+", "ya29.<redacted>", msg)
    msg = re.sub(r"eyJ[A-Za-z0-9_=]+\.[A-Za-z0-9_=]+\.[A-Za-z0-9_./+=%\-]+",
                 "<jwt-redacted>", msg)
    msg = re.sub(r"GOCSPX-[A-Za-z0-9_\-]+", "GOCSPX-<redacted>", msg)
    msg = re.sub(r"(client_secret['\"]?\s*[=:]\s*['\"]?)[^\s'\"]+",
                 r"\1<redacted>", msg, flags=re.IGNORECASE)
    return msg[:200]


class GoogleOAuthWorker(QThread):
    """Runs the interactive consent flow (mode='connect') or a silent
    refresh (mode='reconnect') off the UI thread."""

    # connect → emits the minimal record dict; reconnect → emits {} on success
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, mode: str = "connect", parent=None):
        super().__init__(parent)
        self._mode = mode

    def run(self):
        from src.data import google_oauth
        try:
            if self._mode == "reconnect":
                creds = google_oauth.reconnect()
                if creds is None:
                    self.error.emit("Could not reconnect — please connect again.")
                else:
                    self.finished.emit({})
            else:
                record = google_oauth.run_interactive_flow()
                self.finished.emit(record)
        except Exception as exc:  # noqa: BLE001 — surface redacted
            self.error.emit(_redact(str(exc)))
