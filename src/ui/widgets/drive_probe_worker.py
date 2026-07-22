"""Off-thread service-account Drive probe for the credentials card.

The Google Drive card used to show only the OAuth status line, which on a
build with no bundled GCP client reads "No OAuth client — add your own GCP
client below first." forever. That line sat directly beneath a
*service-account* key path that was working fine, with no success indicator
anywhere near it — so a healthy service account looked broken, and an empty
share looked identical to a broken credential.

This worker answers the two questions that card could not:
  1. does the service-account key actually authenticate, and
  2. how many files can it actually SEE?

(2) is the one that matters in practice. A service account is its own Google
identity and sees only what has been explicitly shared with it, so "0 files"
is the normal state of a fresh key and is NOT an error — but it is
indistinguishable from a broken setup unless we say so and name the address
folders have to be shared to.

``DriveReader.test_connection`` and ``files().list`` are blocking network
calls, so they must not run on the Qt main thread.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

# A listing page big enough to be a useful signal without being a full crawl;
# the label renders "99+" past this rather than paging the whole corpus.
_PROBE_PAGE_SIZE = 100


class DriveProbeWorker(QThread):
    """Probe the configured service account: auth + visible-file count.

    Emits ``finished`` with a plain dict (never raises into Qt):
        ok            bool  — the key authenticated
        count         int   — files visible to it (capped at _PROBE_PAGE_SIZE)
        capped        bool  — the count hit the page cap
        account       str   — the service-account email (NOT a secret; it is
                              the address folders get shared to)
        detail        str   — failure reason when ok is False
    """

    finished = Signal(dict)

    def run(self):
        try:
            self.finished.emit(self._probe())
        except Exception as exc:  # noqa: BLE001 — a probe must never kill the thread
            self.finished.emit(
                {"ok": False, "count": 0, "capped": False, "account": "",
                 "detail": f"{type(exc).__name__}: {exc}"[:160]})

    def _probe(self) -> dict:
        from src.data.drive_reader import DriveReader

        reader = DriveReader.from_settings()
        # Only the service-account branch is this card's business; the OAuth
        # branch has its own status label right below it.
        if getattr(reader, "_auth_type", "") != "service_account":
            return {"ok": False, "count": 0, "capped": False, "account": "",
                    "detail": ""}
        if not reader.is_configured():
            return {"ok": False, "count": 0, "capped": False, "account": "",
                    "detail": "no service-account key set"}

        ok, msg = reader.test_connection()
        if not ok:
            return {"ok": False, "count": 0, "capped": False,
                    "account": self._account(reader), "detail": str(msg)[:160]}

        service = reader._build_service()
        res = service.files().list(
            pageSize=_PROBE_PAGE_SIZE, fields="files(id)").execute()
        files = res.get("files", []) or []
        return {"ok": True, "count": len(files),
                "capped": len(files) >= _PROBE_PAGE_SIZE,
                "account": self._account(reader), "detail": ""}

    @staticmethod
    def _account(reader) -> str:
        """The client_email out of the key file — the address the operator must
        share folders to. Delegates to ``google_access.service_account_email``,
        the one place allowed to open the key file (client_email only, never the
        private key); the picker dialog's empty-state uses the same helper so
        every surface names the same address."""
        from src.data.google_access import service_account_email
        return service_account_email()
