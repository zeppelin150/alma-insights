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
import urllib.request
import urllib.error

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
        body = {"queryType": None}
        if query:
            body["queryType"] = "cards"
            body["query"] = {"nestedExpressions": [], "op": "AND",
                             "type": "grouping"}
            # Guru search API uses a searchTerms field
            body["searchTerms"] = query

        data = self._request("POST", "/search/cardmgr", body=body)
        results = data if isinstance(data, list) else []
        return self._normalize_cards(results)

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
                    content: str) -> dict:
        """Create a new card in a Guru collection.

        **HUMAN-GATED ONLY** — never called without explicit user
        approval in the UI.
        """
        payload = {
            "preferredPhrase": title,
            "content": content,
            "collection": {"id": collection_id},
            "shareStatus": "TEAM",
        }
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

    # ── Internals ───────────────────────────────────────────────

    @staticmethod
    def _build_auth_header(email: str, token: str) -> str:
        cred = f"{email}:{token}"
        encoded = base64.b64encode(cred.encode("utf-8")).decode("ascii")
        return f"Basic {encoded}"

    def _request(self, method: str, path: str,
                 body: dict | None = None) -> dict | list:
        """Execute an API request and return parsed JSON."""
        url = f"{self.BASE}{path}"
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
                if not raw.strip():
                    return {}
                return json.loads(raw)
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
