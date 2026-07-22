"""Google Drive READ client (drive.readonly) for the enablement Drive monitor.

Reuses the service-account auth shape from src/export/gdrive_export.py but with
the broader READ scope and read methods: list files changed in a watched folder,
and export a document's plain text. The uploader keeps its narrow drive.file
scope — the two clients stay separate; we never widen one to do the other's job.

Going live needs drive.readonly + the folders shared to the service-account email
(an org/admin step). Until enablement.drive.read_enabled is set with a valid
credentials file, is_configured() is False and the monitor skips.

Optional deps: google-api-python-client + google-auth (+ pypdf / python-docx for
PDF/DOCX text). Degrades gracefully if absent (the gdrive_export precedent).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger("alma.drive_reader")

_READONLY_SCOPE = ["https://www.googleapis.com/auth/drive.readonly"]
_GOOGLE_DOC = "application/vnd.google-apps.document"

# Rate-limit / transient markers that warrant a retry (mirrors the write client's
# gdrive_export._is_rate_limit_error). Matched against str(exc) so we do not need
# googleapiclient's HttpError type imported here.
_RETRYABLE = ("rateLimitExceeded", "userRateLimitExceeded", "429",
              "500", "502", "503", "backendError", "internalError")


def _read_with_retry(request, *, max_retries: int = 5, base_delay: float = 0.5):
    """Execute a Drive READ request with exponential backoff on rate-limit / 5xx
    errors — the read counterpart to gdrive_export._throttled_execute, minus the
    write-pacing lock (reads are not the scarce quota, but a live search must not
    surface a transient 429 to the user). Non-retryable errors propagate at once.
    """
    delay = base_delay
    for attempt in range(max_retries):
        try:
            return request.execute()
        except Exception as exc:  # noqa: BLE001 — retry only transient classes
            if attempt == max_retries - 1 or not any(m in str(exc) for m in _RETRYABLE):
                raise
            time.sleep(delay)
            delay = min(delay * 2, 16)


def _q(value: str) -> str:
    """Quote + escape a value for a Drive `q` query string. The Drive query
    grammar escapes ' and \\ with a backslash; without this an LLM- or
    doc-supplied query (or a stray quote in a folder id) could break out of
    the quotes and drop the trashed/parents filters."""
    s = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{s}'"


# How many `<id> in parents` terms ride in one OR-group. Drive caps the q
# string length, not the term count; ~15 keeps a chunk of 33-char folder ids
# well under the limit while still covering a 29-folder corpus in 2 queries.
_PARENTS_PER_QUERY = 15

# Subtree enumerations memoized per sorted root-id tuple. One chat turn often
# runs several scoped searches back-to-back ("try SSO, then Okta, then…") in
# the SAME MCP subprocess — the tree walk is the expensive part, so cache it
# briefly. 60s is short enough that a folder created mid-conversation appears
# on the next turn (each turn is a fresh subprocess anyway).
_SUBTREE_TTL_S = 60.0
_SUBTREE_CACHE: dict[tuple, tuple[float, list[str]]] = {}


def _parents_clause(folder_ids: list[str]) -> str:
    """``'A' in parents`` for one id, ``('A' in parents or 'B' in parents)``
    for several — single ids stay unparenthesized so existing q-clause
    consumers (and test pins) see the familiar shape."""
    terms = [f"{_q(fid)} in parents" for fid in folder_ids]
    if len(terms) == 1:
        return terms[0]
    return "(" + " or ".join(terms) + ")"


class DriveReader:
    """Read-only Drive client: list changed files + export text + search."""

    def __init__(self, credentials_path: str = "", auth_type: str = "service_account"):
        self._credentials_path = credentials_path
        self._auth_type = auth_type
        self._service = None
        # Set by search_files: whether the last scoped search's subtree
        # enumeration was capped (query_business_drive reads it for the honest
        # scope block). Always present so a reader that never searched is safe.
        self._last_scope_truncated = False

    @classmethod
    def from_settings(cls) -> "DriveReader":
        from src.data.settings_manager import get_section
        drive = (get_section("enablement", {}) or {}).get("drive") or {}
        return cls(drive.get("credentials_path", ""),
                   auth_type=drive.get("auth_type", "service_account"))

    def is_configured(self) -> bool:
        from src.data.settings_manager import get_section
        drive = (get_section("enablement", {}) or {}).get("drive") or {}
        if not drive.get("read_enabled"):
            return False
        if self._auth_type == "oauth_user":
            # Disable-on-launch: only "configured" once the user has
            # explicitly reconnected this session, so the background monitor
            # never resumes Google calls on its own at boot.
            from src.data import google_oauth
            return google_oauth.is_active()
        return bool(self._credentials_path) and Path(self._credentials_path).is_file()

    def _build_service(self):
        if self._service:
            return self._service
        try:
            from googleapiclient.discovery import build
        except ImportError:
            raise ImportError(
                "Drive read requires: pip install google-api-python-client google-auth")
        if self._auth_type == "oauth_user":
            from src.data import google_oauth
            creds = google_oauth.load_active_credentials()
            if creds is None:
                raise RuntimeError(
                    "Google account not connected this session — open Settings "
                    "and click Reconnect to authorize Drive access.")
        else:
            from google.oauth2 import service_account
            creds = service_account.Credentials.from_service_account_file(
                self._credentials_path, scopes=_READONLY_SCOPE)
        self._service = build("drive", "v3", credentials=creds)
        return self._service

    def test_connection(self) -> tuple[bool, str]:
        if self._auth_type == "oauth_user":
            from src.data import google_oauth
            if not google_oauth.is_active():
                return False, "Google account not connected this session"
        elif not self._credentials_path or not Path(self._credentials_path).is_file():
            return False, "Service-account credentials path not set"
        try:
            self._build_service().files().list(pageSize=1, fields="files(id)").execute()
            return True, "Connected (drive.readonly)"
        except ImportError as e:
            return False, str(e)
        except Exception as e:  # noqa: BLE001
            return False, f"Connection failed: {e}"

    def list_drives(self) -> list[dict]:
        """Enumerate the Drives the connected account can browse.

        Returns a synthetic My Drive entry FIRST (id='root', the alias the
        Drive API accepts as a `parents` target for the user's personal drive),
        then one entry per Shared Drive via drives().list. Pages through
        nextPageToken. Honors the disable-on-launch gate: if Drive read is not
        configured/active this session, returns [] WITHOUT building a service —
        no Drive call at import/boot.
        """
        if not self.is_configured():
            return []
        svc = self._build_service()
        drives: list[dict] = [{"id": "root", "name": "My Drive", "is_my_drive": True}]
        page_token = None
        while True:
            resp = svc.drives().list(
                pageSize=100, pageToken=page_token, fields="nextPageToken, drives(id, name)",
            ).execute()
            for d in resp.get("drives", []):
                drives.append({"id": d.get("id"), "name": d.get("name"),
                               "is_my_drive": False})
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return drives

    def list_shared_roots(self) -> list[dict]:
        """Top-level folders shared TO this account (``sharedWithMe = true``).

        These are the entry points a service account actually has: an SA is its
        own Google identity, so folders shared with it live in "Shared with me",
        NOT under its (empty) My Drive and NOT in drives().list. A folder-picker
        tree built only from ``list_drives`` + ``list_folders('root')`` renders
        EMPTY for an SA even after the operator shares a folder — verified live
        2026-07-21. Only folders shared *directly* count (children of a shared
        folder inherit access but are not ``sharedWithMe`` themselves), which is
        exactly the set of tree roots wanted. Honors the disable-on-launch gate.
        """
        if not self.is_configured():
            return []
        svc = self._build_service()
        q = ("sharedWithMe = true "
             "and mimeType = 'application/vnd.google-apps.folder' "
             "and trashed = false")
        folders: list[dict] = []
        page_token = None
        while True:
            resp = svc.files().list(
                q=q, pageSize=100, pageToken=page_token,
                fields="nextPageToken, files(id, name, driveId)",
            ).execute()
            for f in resp.get("files", []):
                folders.append({"id": f.get("id"), "name": f.get("name"),
                                "drive_id": f.get("driveId"), "shared": True})
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return folders

    def list_picker_roots(self) -> list[dict]:
        """The roots a folder-picker tree should offer, auth-type-aware.

        * ``service_account`` — real Shared Drives (if the SA is a member) plus
          shared-with-me folders. The synthetic My Drive entry is DROPPED: an
          SA's own drive is structurally empty, and offering it invites another
          expand-to-nothing dead end.
        * ``oauth_user`` — My Drive + Shared Drives + shared-with-me folders
          (a user's shared-with-me folders were invisible here too).

        Every row is expandable via :meth:`list_folders` on its ``id``.
        """
        if not self.is_configured():
            return []
        drives = self.list_drives()
        if self._auth_type == "service_account":
            drives = [d for d in drives if not d.get("is_my_drive")]
        return drives + self.list_shared_roots()

    def list_folders(self, parent_id: str) -> list[dict]:
        """List immediate child FOLDERS of parent_id (id+name+driveId ONLY).

        parent_id may be 'root' (My Drive). corpora='allDrives' +
        includeItemsFromAllDrives + supportsAllDrives so Shared-Drive folders
        are visible. Returns no file bodies — only folder rows for lazy-tree
        navigation. Honors the disable-on-launch gate (returns [] without
        building a service when not configured/active).
        """
        if not self.is_configured():
            return []
        svc = self._build_service()
        q = (f"{_q(parent_id)} in parents "
             "and mimeType = 'application/vnd.google-apps.folder' "
             "and trashed = false")
        folders: list[dict] = []
        page_token = None
        while True:
            resp = svc.files().list(
                q=q, corpora="allDrives", includeItemsFromAllDrives=True,
                supportsAllDrives=True, pageSize=100, pageToken=page_token,
                fields="nextPageToken, files(id, name, driveId)",
            ).execute()
            for f in resp.get("files", []):
                folders.append({"id": f.get("id"), "name": f.get("name"),
                                "drive_id": f.get("driveId")})
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return folders

    def list_changed_files(self, folder_id: str, *, modified_after: str | None = None,
                           recursive: bool = True, mime_types: list[str] | None = None) -> list[dict]:
        """Files in a folder changed since modified_after (incremental watermark)."""
        svc = self._build_service()
        clauses = [f"{_q(folder_id)} in parents", "trashed = false",
                   "mimeType != 'application/vnd.google-apps.folder'"]
        if modified_after:
            clauses.append(f"modifiedTime > {_q(modified_after)}")
        if mime_types:
            clauses.append("(" + " or ".join(f"mimeType = {_q(m)}" for m in mime_types) + ")")
        q = " and ".join(clauses)
        files: list[dict] = []
        page_token = None
        while True:
            resp = svc.files().list(
                q=q, corpora="allDrives", includeItemsFromAllDrives=True,
                supportsAllDrives=True, pageSize=100, pageToken=page_token,
                orderBy="modifiedTime",
                fields="nextPageToken, files(id, name, mimeType, modifiedTime, webViewLink)",
            ).execute()
            files.extend(resp.get("files", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return files

    def get_file(self, file_id: str) -> dict:
        """Metadata for a single file (the import-by-URL Workbench path)."""
        svc = self._build_service()
        f = svc.files().get(
            fileId=file_id, supportsAllDrives=True,
            fields="id, name, mimeType, webViewLink, modifiedTime",
        ).execute()
        return {
            "id": f.get("id"), "name": f.get("name"),
            "mime_type": f.get("mimeType"), "url": f.get("webViewLink"),
            "modified_time": f.get("modifiedTime"),
        }

    def export_text(self, file_id: str, mime_type: str) -> str:
        """Plain text of a doc: native Google Docs via export, others via download."""
        svc = self._build_service()
        if mime_type == _GOOGLE_DOC:
            data = svc.files().export(fileId=file_id, mimeType="text/plain").execute()
            return data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
        raw = svc.files().get_media(fileId=file_id).execute()
        if not isinstance(raw, (bytes, bytearray)):
            return str(raw)
        return _extract_text(raw, mime_type)

    def list_subtree_folder_ids(self, root_ids, *, max_folders: int = 250,
                                max_depth: int = 10) -> tuple[list[str], bool]:
        """``(folder_ids, truncated)`` for the tree(s) under ``root_ids``.

        Drive's ``in parents`` is NOT recursive, so a folder-scoped search needs
        the subtree enumerated first. BFS level-by-level, batching each level's
        parents into chunked ``(P1 in parents or P2 in parents …)`` queries
        (:data:`_PARENTS_PER_QUERY` per request) so a 29-folder corpus costs a
        handful of calls, not one per folder.

        Caps: ``max_folders`` total and ``max_depth`` levels. ``truncated`` is
        True when EITHER cap clipped the walk — a clipped scope that searches as
        "no content" is the exact false negative this exists to kill, so the
        flag is both LOGGED and RETURNED (the caller threads it into the tool's
        scope block; callers before 2026-07-22 only logged it — invisible to
        the model). Note the exact-boundary case: hitting ``max_folders`` on the
        last id of a level leaves that level's children unexpanded, so we mark
        truncated whenever a non-empty frontier remains after the loop, not only
        on the mid-level overflow.

        Results are memoized for :data:`_SUBTREE_TTL_S` seconds so a multi-search
        turn ("try SSO, then Okta, then …") enumerates once. The cache key
        includes the caps AND the credential identity — a module-level cache is
        shared across DriveReader instances in the long-lived main process, so
        a differently-capped or different-account walk must not be served a
        stale tree.

        ``root_ids`` may be one id or a list (the active_folders union —
        overlapping trees dedupe naturally). Never raises on empty input.
        """
        roots = [str(r).strip() for r in
                 ([root_ids] if isinstance(root_ids, str) else (root_ids or []))
                 if str(r or "").strip()]
        if not roots:
            return [], False
        cache_key = (tuple(sorted(set(roots))), max_folders, max_depth,
                     self._auth_type, self._credentials_path)
        now = time.monotonic()
        hit = _SUBTREE_CACHE.get(cache_key)
        if hit and now - hit[0] < _SUBTREE_TTL_S:
            return list(hit[1]), hit[2]

        svc = self._build_service()
        seen: dict[str, None] = dict.fromkeys(roots)   # ordered de-dupe
        level = list(seen)
        depth = 0
        capped = False
        while level and depth < max_depth and not capped:
            next_level: list[str] = []
            for i in range(0, len(level), _PARENTS_PER_QUERY):
                if capped:
                    break   # cap already hit — don't fire zero-yield chunk calls
                chunk = level[i:i + _PARENTS_PER_QUERY]
                q = (f"{_parents_clause(chunk)} "
                     "and mimeType = 'application/vnd.google-apps.folder' "
                     "and trashed = false")
                page_token = None
                while True:
                    resp = _read_with_retry(svc.files().list(
                        q=q, corpora="allDrives", includeItemsFromAllDrives=True,
                        supportsAllDrives=True, pageSize=100, pageToken=page_token,
                        fields="nextPageToken, files(id)"))
                    for f in resp.get("files", []):
                        fid = f.get("id")
                        if fid and fid not in seen:
                            if len(seen) >= max_folders:
                                capped = True
                                break
                            seen[fid] = None
                            next_level.append(fid)
                    page_token = resp.get("nextPageToken")
                    if not page_token or capped:
                        break
            level = next_level
            depth += 1
        # Truncated if EITHER cap clipped the walk. A non-empty frontier means
        # folders whose children we never expanded remain (covers the depth cap
        # AND the exact-max_folders-at-a-level-boundary case the old mid-level-
        # only flag missed).
        truncated = bool(capped or level)
        if truncated:
            logger.warning(
                "Drive subtree enumeration truncated at %d folders / depth %d "
                "(roots=%s) — deeper content is OUT of this search scope",
                len(seen), depth, roots)
        ids = list(seen)
        _SUBTREE_CACHE[cache_key] = (now, ids, truncated)
        return ids, truncated

    def search_files(self, query: str, limit: int = 20, *,
                     folder_id=None) -> list[dict]:
        """Live full-text search across the account's Drive (Shared Drives too).

        Backs drive_query.query_business_drive's live_client seam. Pages through
        nextPageToken until `limit` results are collected — the sibling list_*
        methods already loop; this one used to read a SINGLE page, so recall was
        silently capped by the API page size rather than by the caller's limit.

        ``folder_id`` (one id or a list of roots) scopes the search to the WHOLE
        subtree under those folders: the tree is enumerated first
        (:meth:`list_subtree_folder_ids`) and the query runs as chunked
        ``in parents`` OR-groups. Scoping used to be direct-children-only —
        Drive's ``in parents`` is not recursive — which returned 0 for every
        query against a corpus whose documents live in nested subfolders (the
        2026-07-22 field trap).

        EVERY chunk is queried before ``limit`` is applied: a naive
        "return as soon as len(out) >= limit" walked chunks in shallow-first
        BFS order, so once shallow folders filled the limit the DEEPEST chunks
        were never queried at all — the exact deep-miss this recursion exists to
        prevent. We collect each chunk (each capped at ``limit`` rows so a huge
        corpus can't runaway), dedupe across chunks (a multi-parent file matches
        more than one group), then truncate the merged set to ``limit``. Ranking
        within the returned set is per-chunk, not global relevance — an accepted
        limitation of the API's per-query ordering. Sets
        ``self._last_scope_truncated`` so query_business_drive can report whether
        the subtree enumeration was capped. Returns lightweight file rows (id,
        name, url, mime_type, modified); no text export — call export_text.
        """
        svc = self._build_service()
        limit = max(1, int(limit))
        self._last_scope_truncated = False

        base = [f"fullText contains {_q(query)}"] if query else []
        if folder_id:
            parent_ids, truncated = self.list_subtree_folder_ids(folder_id)
            self._last_scope_truncated = truncated
            if not parent_ids:
                return []
            parent_chunks = [parent_ids[i:i + _PARENTS_PER_QUERY]
                             for i in range(0, len(parent_ids), _PARENTS_PER_QUERY)]
        else:
            parent_chunks = [None]

        out: list[dict] = []
        seen_ids: set[str] = set()
        for chunk in parent_chunks:
            clauses = list(base)
            if chunk is not None:
                clauses.append(_parents_clause(chunk))
            clauses.append("trashed = false")
            q = " and ".join(clauses)
            chunk_count = 0
            page_token = None
            while chunk_count < limit:
                resp = _read_with_retry(svc.files().list(
                    q=q, corpora="allDrives", includeItemsFromAllDrives=True,
                    supportsAllDrives=True, pageSize=min(limit - chunk_count, 100),
                    pageToken=page_token,
                    fields="nextPageToken, files(id, name, mimeType, webViewLink, modifiedTime)"))
                for f in resp.get("files", []):
                    chunk_count += 1
                    fid = f.get("id")
                    if fid in seen_ids:
                        continue
                    seen_ids.add(fid)
                    out.append({"name": f.get("name"), "id": fid,
                                "url": f.get("webViewLink"), "mime_type": f.get("mimeType"),
                                "modified": f.get("modifiedTime")})
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break
        return out[:limit]


def _extract_text(raw: bytes, mime_type: str) -> str:
    mt = mime_type or ""
    try:
        if "pdf" in mt:
            import io
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            return "\n".join((p.extract_text() or "") for p in reader.pages)
        if "presentationml" in mt:
            # WS2-M4: .pptx previously fell into the docx branch and silently
            # extracted '' — product guides are decks, so this was a real hole.
            from src.data.pptx_reader import pptx_to_markdown
            return pptx_to_markdown(raw)
        if "word" in mt or "officedocument" in mt:
            import io
            import docx
            doc = docx.Document(io.BytesIO(raw))
            return "\n".join(p.text for p in doc.paragraphs)
        return raw.decode("utf-8", errors="ignore")
    except Exception as exc:  # noqa: BLE001 — extraction is best-effort
        logger.debug("text extract failed (%s): %s", mime_type, exc)
        return ""
