"""
Alma Insights — Guru Knowledge Base Client (Phase 4)

REST client for the Guru API v1.  Supports article sync, search,
and gated content updates.

Auth: Basic ``base64(email:api_token)``  — same pattern as Zendesk.

Usage:
    client = GuruClient("user@company.com", "abc123-token")
    if client.test_connection():
        collections = client.list_collections()
        cards = client.list_cards(collections[0]["id"])
"""

import base64
import hashlib
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from src.data import pat_store

logger = logging.getLogger("alma.guru")

_REQUEST_TIMEOUT = 30  # seconds
_PAGE_SIZE = 100


class GuruAuthError(Exception):
    """Raised when Guru returns 401 Unauthorized."""


class GuruAPIError(Exception):
    """Raised for non-auth Guru API errors."""


class GuruClient:
    """Guru Knowledge Base API v1 client.

    Mirrors the ZendeskClient pattern: urllib for HTTP, pat_store for
    credential persistence, base64 Basic auth.
    """

    BASE = "https://api.getguru.com/api/v1"

    def __init__(self, email: str, api_token: str):
        self._email = email
        self._token = api_token
        self._auth_header = self._build_auth_header(email, api_token)

    # ── Public API ──────────────────────────────────────────────

    def test_connection(self) -> bool:
        """Verify credentials via ``GET /whoami``."""
        try:
            self._request("GET", "/whoami")
            return True
        except (GuruAuthError, GuruAPIError, Exception) as exc:
            logger.debug("Guru connection test failed: %s", exc)
            return False

    def list_collections(self) -> list[dict]:
        """List all Guru collections (folders).

        Returns list of dicts with keys: id, name, description, slug.
        """
        data = self._request("GET", "/collections")
        return [
            {
                "id": c.get("id", ""),
                "name": c.get("name", ""),
                "description": c.get("description", ""),
                "slug": c.get("slug", ""),
            }
            for c in (data if isinstance(data, list) else [])
        ]

    def list_cards(self, collection_id: str | None = None) -> list[dict]:
        """List cards, optionally filtered by collection.

        Returns list of dicts with keys: id, title, collection,
        content_hash, lastModified.
        """
        if collection_id:
            path = f"/collections/{collection_id}/cards"
        else:
            # Use search with empty query to get all cards
            return self.search_cards("")

        data = self._request("GET", path)
        return self._normalize_cards(data)

    def get_card(self, card_id: str) -> dict:
        """Fetch a single card with full content."""
        data = self._request("GET", f"/cards/{card_id}")
        if not isinstance(data, dict):
            return {}
        content = data.get("content", "")
        return {
            "id": data.get("id", ""),
            "title": data.get("preferredPhrase", ""),
            "content": content,
            "content_hash": hashlib.sha256(
                content.encode("utf-8")
            ).hexdigest(),
            "collection": data.get("collection", {}).get("name", ""),
            "collection_id": data.get("collection", {}).get("id", ""),
            "lastModified": data.get("lastModified", ""),
            "status": data.get("verificationState", ""),
        }

    def search_cards(self, query: str) -> list[dict]:
        """Search cards by text query.

        Uses the ``POST /search/cardmgr`` endpoint with a JSON body.
        """
        # NOTE: do NOT send an empty `query` grouping — Guru rejects it with
        # 400 "grouping expression requires one or more nestedExpressions".
        # A text search is just queryType + searchTerms (verified live).
        body: dict = {"queryType": "cards"}
        if query:
            body["searchTerms"] = query

        data = self._request("POST", "/search/cardmgr", body=body)
        results = data if isinstance(data, list) else []
        return self._normalize_cards(results)

    def list_folders(self, collection_id: str | None = None) -> list[dict]:
        """List Guru folders — the sub-folder model that replaced Boards (2022).

        ``GET /folders`` (optionally ``?collection={id}``). The folder whose
        ``home`` flag is set is the collection's root. Returns dicts with keys:
        id, title, slug, home, item_count, collection_id, collection_name.
        """
        path = "/folders"
        if collection_id:
            path += f"?collection={urllib.parse.quote(collection_id)}"
        data = self._request("GET", path)
        out = []
        for f in (data if isinstance(data, list) else []):
            if not isinstance(f, dict):
                continue
            coll = f.get("collection") or {}
            out.append({
                "id": f.get("id", ""),
                "title": f.get("title", ""),
                "slug": f.get("slug", ""),
                "home": bool(f.get("home")),
                "item_count": f.get("numberOfFacts", 0),
                "collection_id": coll.get("id", "") if isinstance(coll, dict) else "",
                "collection_name": coll.get("name", "") if isinstance(coll, dict) else "",
            })
        return out

    def get_folder_items(self, folder_id: str) -> list[dict]:
        """List a folder's items — cards AND nested sub-folders.

        ``GET /folders/{id}/items``. Returns dicts with keys: id, item_id,
        type ('card'|'folder'), title.
        """
        data = self._request("GET", f"/folders/{folder_id}/items")
        out = []
        for it in (data if isinstance(data, list) else []):
            if not isinstance(it, dict):
                continue
            out.append({
                "id": it.get("id", ""),
                "item_id": it.get("itemId", ""),
                "type": it.get("type", ""),
                "title": it.get("preferredPhrase") or it.get("title", ""),
            })
        return out

    def update_card(self, card_id: str, content: str,
                    title: str | None = None) -> dict:
        """Update card content.

        **HUMAN-GATED ONLY** — never called without explicit user
        approval in the UI.  The caller (GuruContentPipeline) must
        ensure a confirmation dialog has been shown.
        """
        # Fetch current card to get required fields
        current = self._request("GET", f"/cards/{card_id}")
        if not isinstance(current, dict):
            raise GuruAPIError(f"Card {card_id} not found")

        payload = {
            "preferredPhrase": title or current.get("preferredPhrase", ""),
            "content": content,
        }

        data = self._request("PUT", f"/cards/{card_id}", body=payload)
        logger.info("Updated Guru card %s", card_id)
        return data if isinstance(data, dict) else {}

    def create_card(self, collection_id: str, title: str,
                    content: str, folder_ids: list[str] | None = None) -> dict:
        """Create a new card in a Guru collection, optionally inside folders.

        **HUMAN-GATED ONLY** — never called without explicit user
        approval in the UI.

        When ``folder_ids`` is given the card is created via
        ``POST /cards/extended`` with a ``folderIds`` array, landing it directly
        in the chosen sub-folder(s) (Guru's current model: Collection → Folder →
        Card). With no folders it uses the plain ``POST /cards`` collection-root
        create, unchanged for existing callers.
        """
        payload = {
            "preferredPhrase": title,
            "content": content,
            "collection": {"id": collection_id},
            "shareStatus": "TEAM",
        }
        folder_ids = [f for f in (folder_ids or []) if f]
        if folder_ids:
            payload["folderIds"] = folder_ids
            data = self._request("POST", "/cards/extended", body=payload)
            logger.info("Created Guru card '%s' in collection %s folders %s",
                        title, collection_id, folder_ids)
        else:
            data = self._request("POST", "/cards", body=payload)
            logger.info("Created Guru card '%s' in collection %s", title, collection_id)
        return data if isinstance(data, dict) else {}

    # ── Credential Persistence ──────────────────────────────────

    @staticmethod
    def load_credentials() -> tuple[str, str]:
        """Load ``(email, api_token)`` from pat_store."""
        email = pat_store.load_setting("guru_email") or ""
        token = pat_store.load_setting("guru_api_token") or ""
        return email, token

    @staticmethod
    def save_credentials(email: str, api_token: str) -> bool:
        """Persist Guru credentials to pat_store."""
        ok_email = pat_store.save_setting("guru_email", email)
        ok_token = pat_store.save_setting("guru_api_token", api_token)
        return ok_email and ok_token

    @staticmethod
    def is_configured() -> bool:
        """Check if Guru credentials are saved."""
        email, token = GuruClient.load_credentials()
        return bool(email and token)

    # ── Analytics / comments / verification (2026-06 redesign) ──

    def get_team_id(self) -> str:
        """The team id for analytics endpoints (whoami → teams fallback)."""
        data = self._request("GET", "/whoami")
        if isinstance(data, dict):
            for container in (data, data.get("user") or {}):
                team = container.get("team") if isinstance(container, dict) else None
                if isinstance(team, dict) and team.get("id"):
                    return team["id"]
        teams = self._request("GET", "/teams")
        if isinstance(teams, list) and teams and isinstance(teams[0], dict):
            return teams[0].get("id", "")
        return ""

    def get_analytics(self, team_id: str, from_date: str | None = None,
                      to_date: str | None = None, *,
                      max_pages: int = 20) -> list[dict]:
        """Usage events (card-viewed, card-comment-created, card-verified…).

        ``GET /teams/{id}/analytics`` paginated via the Link header.
        Events carry {type, user, eventDate, properties.cardId}.
        """
        params = []
        if from_date:
            params.append(f"fromDate={urllib.parse.quote(from_date)}")
        if to_date:
            params.append(f"toDate={urllib.parse.quote(to_date)}")
        qs = ("?" + "&".join(params)) if params else ""
        rows = self._paged_get(
            f"/teams/{team_id}/analytics{qs}", max_pages=max_pages
        )
        return [r for r in rows if isinstance(r, dict)]

    def get_team_stats(self, team_id: str) -> dict:
        """Card totals per verification state (``GET /teams/{id}/stats``)."""
        data = self._request("GET", f"/teams/{team_id}/stats")
        return data if isinstance(data, dict) else {}

    def list_unverified_cards(self, *, max_pages: int = 4) -> list[dict]:
        """The verification-manager queue (``GET /cards/verificationmgr``).

        Cards carry verificationState / nextVerificationDate /
        verificationInterval / lastVerified / commentCount / collection.
        """
        rows = self._paged_get("/cards/verificationmgr", max_pages=max_pages)
        out = []
        for c in rows:
            if not isinstance(c, dict) or not c.get("id"):
                continue
            coll = c.get("collection") or {}
            out.append({
                "id": c.get("id", ""),
                "title": c.get("preferredPhrase", ""),
                "verification_state": c.get("verificationState", ""),
                "verification_reason": c.get("verificationReason", ""),
                "next_verification_date": c.get("nextVerificationDate", ""),
                "verification_interval": c.get("verificationInterval", ""),
                "last_verified": c.get("lastVerified", ""),
                "last_modified": c.get("lastModified", ""),
                "comment_count": c.get("commentCount", 0),
                "collection": coll.get("name", "") if isinstance(coll, dict) else "",
                "collection_id": coll.get("id", "") if isinstance(coll, dict) else "",
            })
        return out

    def verify_card(self, card_id: str) -> dict:
        data = self._request("PUT", f"/cards/{card_id}/verify")
        return data if isinstance(data, dict) else {}

    def unverify_card(self, card_id: str) -> dict:
        data = self._request("POST", f"/cards/{card_id}/unverify")
        return data if isinstance(data, dict) else {}

    def get_card_comments(self, card_id: str, *, status: str | None = None,
                          max_pages: int = 4) -> list[dict]:
        """Comments on a card (``GET /cards/{id}/comments``), Link-paged."""
        qs = f"?status={urllib.parse.quote(status)}" if status else ""
        rows = self._paged_get(f"/cards/{card_id}/comments{qs}",
                               max_pages=max_pages)
        out = []
        for c in rows:
            if not isinstance(c, dict) or not c.get("id"):
                continue
            owner = c.get("owner") or {}
            author = ""
            if isinstance(owner, dict):
                author = owner.get("email") or " ".join(
                    p for p in (owner.get("firstName"), owner.get("lastName")) if p
                )
            out.append({
                "id": c.get("id", ""),
                "content": c.get("content", ""),
                "author": author,
                "created_at": c.get("dateCreated", ""),
                "status": c.get("status", "OPEN"),
            })
        return out

    def create_card_comment(self, card_id: str, text: str) -> dict:
        data = self._request("POST", f"/cards/{card_id}/comments",
                             body={"content": text})
        return data if isinstance(data, dict) else {}

    def delete_card_comment(self, card_id: str, comment_id: str) -> dict:
        data = self._request("DELETE", f"/cards/{card_id}/comments/{comment_id}")
        return data if isinstance(data, dict) else {}

    # ── Internals ───────────────────────────────────────────────

    _LINK_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')

    def _paged_get(self, path: str, *, max_pages: int = 20) -> list:
        """GET a paginated collection, following RFC5988 Link rel="next".

        One simple retry on 429 per page; hard page cap so a runaway
        cursor can never spin.
        """
        out: list = []
        next_url: str | None = None
        for page in range(max_pages):
            try:
                parsed, headers = self._request_raw(
                    "GET", path if page == 0 else "", full_url=next_url
                )
            except GuruAPIError as exc:
                if "429" in str(exc):
                    time.sleep(2.0)
                    parsed, headers = self._request_raw(
                        "GET", path if page == 0 else "", full_url=next_url
                    )
                else:
                    raise
            if isinstance(parsed, list):
                out.extend(parsed)
            elif parsed:
                out.append(parsed)
            link = headers.get("link", "")
            m = self._LINK_NEXT.search(link)
            if not m:
                break
            next_url = m.group(1)
        return out

    @staticmethod
    def _build_auth_header(email: str, token: str) -> str:
        cred = f"{email}:{token}"
        encoded = base64.b64encode(cred.encode("utf-8")).decode("ascii")
        return f"Basic {encoded}"

    def _request(self, method: str, path: str,
                 body: dict | None = None) -> dict | list:
        """Execute an API request and return parsed JSON."""
        parsed, _headers = self._request_raw(method, path, body=body)
        return parsed

    def _request_raw(self, method: str, path: str, body: dict | None = None,
                     full_url: str | None = None) -> tuple:
        """Execute a request; return (parsed JSON, lower-cased headers).

        ``full_url`` overrides BASE+path — Link-header pagination hands
        back absolute next-page URLs.
        """
        url = full_url or f"{self.BASE}{path}"
        headers = {
            "Authorization": self._auth_header,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")

        req = urllib.request.Request(
            url, data=data, headers=headers, method=method
        )

        try:
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8")
                resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                if not raw.strip():
                    return {}, resp_headers
                return json.loads(raw), resp_headers
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise GuruAuthError("Invalid Guru credentials") from exc
            body_text = ""
            try:
                body_text = exc.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            raise GuruAPIError(
                f"Guru API error {exc.code}: {body_text}"
            ) from exc
        except urllib.error.URLError as exc:
            raise GuruAPIError(
                f"Guru connection error: {exc.reason}"
            ) from exc

    @staticmethod
    def _normalize_cards(cards_data) -> list[dict]:
        """Normalize raw card list to consistent dicts."""
        if not isinstance(cards_data, list):
            return []
        result = []
        for c in cards_data:
            if not isinstance(c, dict):
                continue
            content = c.get("content", "")
            result.append({
                "id": c.get("id", ""),
                "title": c.get("preferredPhrase", ""),
                "content": content,
                "content_hash": hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest() if content else "",
                "collection": c.get("collection", {}).get("name", "")
                              if isinstance(c.get("collection"), dict) else "",
                "collection_id": c.get("collection", {}).get("id", "")
                                 if isinstance(c.get("collection"), dict) else "",
                "lastModified": c.get("lastModified", ""),
            })
        return result
