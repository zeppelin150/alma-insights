"""
Alma Insights — Zendesk Client (Phase 3)

Incremental ticket export via Zendesk API v2.  Supports cursor-based
pagination for efficient polling.  Cursor is persisted in the credentials
store so it survives restarts.

Auth: Basic ``base64(email/token:{api_key})``.

Usage:
    client = ZendeskClient("mycompany", "user@co.com", "abc123")
    tickets, new_cursor = client.fetch_incremental()
"""

import base64
import json
import logging
import urllib.request
import urllib.error
from datetime import datetime, timezone

from src.data.source_types import SourceClient

logger = logging.getLogger("alma.zendesk")

_REQUEST_TIMEOUT = 30  # seconds
_PAGE_SIZE = 100  # Zendesk default for incremental exports


class ZendeskAuthError(Exception):
    """401 Unauthorized — bad credentials."""


class ZendeskRateLimitError(Exception):
    """429 Too Many Requests."""
    def __init__(self, retry_after: int = 60):
        self.retry_after = retry_after
        super().__init__(f"Rate limited — retry after {retry_after}s")


class ZendeskClient(SourceClient):
    """Zendesk Support API v2 client for ticket ingestion.

    Implements the SourceClient ABC for source-agnostic warehouse
    and watchlist integration.
    """

    def __init__(self, subdomain: str, email: str, api_key: str,
                 view_id: str = ""):
        self._subdomain = subdomain.strip()
        self._email = email.strip()
        self._api_key = api_key.strip()
        self._view_id = str(view_id).strip()
        self._base = f"https://{self._subdomain}.zendesk.com/api/v2"

    # ── public ──────────────────────────────────────────────

    @property
    def is_configured(self) -> bool:
        return bool(self._subdomain and self._email and self._api_key)

    @property
    def source_name(self) -> str:
        return "zendesk"

    @property
    def view_id(self) -> str:
        return self._view_id

    def test_connection(self) -> bool:
        """Verify credentials by calling the /users/me endpoint.

        Returns True if authenticated, False otherwise.
        """
        if not self.is_configured:
            return False
        try:
            data = self._get("/users/me.json")
            return "user" in data
        except (ZendeskAuthError, ZendeskRateLimitError):
            return False
        except Exception as exc:
            logger.warning("Zendesk connection test failed: %s", exc)
            return False

    def fetch_incremental(self, cursor: str | None = None,
                          start_time: int | None = None
                          ) -> tuple[list[dict], str]:
        """Fetch tickets incrementally.

        Args:
            cursor: Opaque cursor from previous call.  If None, uses
                    start_time or defaults to 24h ago.
            start_time: Unix epoch to start from (only used when cursor
                        is None).

        Returns:
            (tickets, next_cursor) — list of ticket dicts and the cursor
            for the next call.  If ``next_cursor`` is empty, there are no
            more pages.
        """
        if cursor:
            url = (f"{self._base}/incremental/tickets/cursor.json"
                   f"?cursor={cursor}")
        else:
            if start_time is None:
                # Default: 24 hours ago
                start_time = int(datetime.now(timezone.utc).timestamp()) - 86400
            url = (f"{self._base}/incremental/tickets/cursor.json"
                   f"?start_time={start_time}")

        all_tickets: list[dict] = []
        next_cursor = ""

        # Paginate through all available pages
        while url:
            data = self._get(url)
            tickets = data.get("tickets", [])
            all_tickets.extend(tickets)

            after_cursor = data.get("after_cursor", "")
            end_of_stream = data.get("end_of_stream", True)

            if end_of_stream or not after_cursor:
                next_cursor = after_cursor
                break

            # Next page
            next_cursor = after_cursor
            url = (f"{self._base}/incremental/tickets/cursor.json"
                   f"?cursor={after_cursor}")

        logger.info(
            "Fetched %d tickets from Zendesk (cursor=%s)",
            len(all_tickets), next_cursor[:12] if next_cursor else "none"
        )
        return all_tickets, next_cursor

    def fetch_view_tickets(self, view_id: str | None = None,
                           max_pages: int = 1) -> list[dict]:
        """Fetch tickets from a specific Zendesk view.

        Views are pre-filtered collections of tickets configured in
        Zendesk (e.g. "All unsolved tickets", "Escalated issues").
        Using a view scopes monitoring to exactly the tickets that
        matter instead of the full incremental firehose.

        Args:
            view_id: Numeric view ID.  Falls back to ``self._view_id``.
            max_pages: Maximum pages to fetch (default 1 = ~100 tickets).
                       Set to 0 for unlimited.

        Returns:
            List of ticket dicts from the view.
        """
        vid = view_id or self._view_id
        if not vid:
            logger.warning("fetch_view_tickets called without a view_id")
            return []

        url = f"{self._base}/views/{vid}/tickets.json"
        all_tickets: list[dict] = []
        pages_fetched = 0

        while url:
            data = self._get(url)
            all_tickets.extend(data.get("tickets", []))
            pages_fetched += 1

            next_page = data.get("next_page")
            if max_pages and pages_fetched >= max_pages:
                break
            url = next_page

        logger.info(
            "Fetched %d tickets from Zendesk view %s (pages=%d)",
            len(all_tickets), vid, pages_fetched
        )
        return all_tickets

    def fetch_ticket(self, ticket_id: int | str) -> dict | None:
        """Fetch a single ticket by ID."""
        try:
            data = self._get(f"/tickets/{ticket_id}.json")
            return data.get("ticket")
        except Exception as exc:
            logger.warning("Failed to fetch ticket %s: %s", ticket_id, exc)
            return None

    def fetch_ticket_fields(self) -> list[dict]:
        """Fetch all ticket field definitions from Zendesk.

        Returns a list of field dicts, each containing at minimum:
            id, title, type, active, custom_field_options (for dropdowns)

        Useful for discovering which custom fields are available
        to map to TRC codes in the spike detection system.
        """
        try:
            data = self._get("/ticket_fields.json")
            fields = data.get("ticket_fields", [])
            logger.info("Fetched %d ticket fields from Zendesk", len(fields))
            return fields
        except Exception as exc:
            logger.warning("Failed to fetch ticket fields: %s", exc)
            return []

    # ── TRC field extraction ─────────────────────────────────

    @staticmethod
    def extract_trc(ticket: dict, trc_field: str = "subject") -> str:
        """Extract the TRC value from a ticket using the configured field mapping.

        Args:
            ticket: Zendesk ticket dict.
            trc_field: One of:
                - ``"subject"`` — ticket subject (default)
                - ``"tags"`` — all tags comma-joined
                - ``"tag:<prefix>"`` — first tag matching prefix
                  (e.g. ``"tag:category"`` extracts ``"category_billing"``)
                - ``"type"`` — Zendesk ticket type (incident/problem/question/task)
                - ``"priority"`` — ticket priority
                - ``"status"`` — ticket status
                - ``"custom_field:<id>"`` — value of custom field with given ID

        Returns:
            Extracted TRC string, or ``"unknown"`` if not found.
        """
        if not trc_field or trc_field == "subject":
            return ticket.get("subject", "unknown") or "unknown"

        if trc_field == "tags":
            tags = ticket.get("tags", [])
            if isinstance(tags, list) and tags:
                return ", ".join(str(t) for t in tags)
            return "untagged"

        if trc_field.startswith("tag:"):
            # Filter to first tag matching the given prefix
            prefix = trc_field.split(":", 1)[1].strip().lower()
            tags = ticket.get("tags", [])
            if isinstance(tags, list):
                for tag in tags:
                    tag_str = str(tag).lower()
                    if tag_str.startswith(prefix):
                        return str(tag)
            # No matching tag — return "untagged"
            return "untagged"

        if trc_field in ("type", "priority", "status"):
            return str(ticket.get(trc_field, "unknown") or "unknown")

        if trc_field.startswith("custom_field:"):
            field_id = trc_field.split(":", 1)[1].strip()
            custom_fields = ticket.get("custom_fields", [])
            if isinstance(custom_fields, list):
                for cf in custom_fields:
                    if str(cf.get("id", "")) == field_id:
                        val = cf.get("value")
                        if val is not None and val != "":
                            return str(val)
            return "unset"

        # Fallback: try as a direct key
        return str(ticket.get(trc_field, "unknown") or "unknown")

    # ── internal ────────────────────────────────────────────

    def _get(self, url_or_path: str) -> dict:
        """Perform an authenticated GET request."""
        if url_or_path.startswith("http"):
            url = url_or_path
        else:
            url = f"{self._base}{url_or_path}"

        # Basic auth: email/token:{api_key}
        auth_str = f"{self._email}/token:{self._api_key}"
        auth_b64 = base64.b64encode(auth_str.encode()).decode()

        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Basic {auth_b64}",
                "Accept": "application/json",
                "User-Agent": "AlmaInsights/1.0",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))

        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise ZendeskAuthError("Invalid Zendesk credentials") from exc
            if exc.code == 429:
                retry = int(exc.headers.get("Retry-After", "60"))
                raise ZendeskRateLimitError(retry) from exc
            raise

    def _write(self, method: str, path: str, body: dict) -> dict:
        """Authenticated POST/PUT with a JSON body (Help Center + macros)."""
        url = f"{self._base}{path}"
        auth_str = f"{self._email}/token:{self._api_key}"
        auth_b64 = base64.b64encode(auth_str.encode()).decode()
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={
                "Authorization": f"Basic {auth_b64}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "AlmaInsights/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise ZendeskAuthError("Invalid Zendesk credentials") from exc
            if exc.code == 429:
                retry = int(exc.headers.get("Retry-After", "60"))
                raise ZendeskRateLimitError(retry) from exc
            raise

    # ── Help Center articles ────────────────────────────────
    # Base path under self._base (…/api/v2): /help_center/...

    def get_articles(self, *, locale: str = "en-us", per_page: int = 100) -> list[dict]:
        data = self._get(f"/help_center/{locale}/articles.json?per_page={per_page}")
        return data.get("articles", []) if isinstance(data, dict) else []

    def get_article(self, article_id, *, locale: str = "en-us") -> dict:
        data = self._get(f"/help_center/{locale}/articles/{article_id}.json")
        return data.get("article", {}) if isinstance(data, dict) else {}

    def search_articles(self, query: str) -> list[dict]:
        from urllib.parse import quote
        data = self._get(f"/help_center/articles/search.json?query={quote(query)}")
        return data.get("results", []) if isinstance(data, dict) else []

    def create_article(self, section_id, title: str, body: str, *,
                       locale: str = "en-us") -> dict:
        data = self._write(
            "POST", f"/help_center/{locale}/sections/{section_id}/articles.json",
            {"article": {"title": title, "body": body, "locale": locale}})
        return data.get("article", {}) if isinstance(data, dict) else {}

    def update_article(self, article_id, *, title: str | None = None,
                       body: str | None = None, locale: str = "en-us") -> dict:
        translation = {}
        if title is not None:
            translation["title"] = title
        if body is not None:
            translation["body"] = body
        data = self._write(
            "PUT", f"/help_center/articles/{article_id}/translations/{locale}.json",
            {"translation": translation})
        return data.get("translation", {}) if isinstance(data, dict) else {}

    # ── Macros ──────────────────────────────────────────────

    def list_macros(self, *, per_page: int = 100) -> list[dict]:
        data = self._get(f"/macros.json?per_page={per_page}")
        return data.get("macros", []) if isinstance(data, dict) else []

    def get_macro(self, macro_id) -> dict:
        data = self._get(f"/macros/{macro_id}.json")
        return data.get("macro", {}) if isinstance(data, dict) else {}

    def create_macro(self, name: str, actions: list[dict], *,
                     description: str | None = None) -> dict:
        macro = {"title": name, "actions": actions}
        if description:
            macro["description"] = description
        data = self._write("POST", "/macros.json", {"macro": macro})
        return data.get("macro", {}) if isinstance(data, dict) else {}

    def update_macro(self, macro_id, *, name: str | None = None,
                     actions: list[dict] | None = None) -> dict:
        macro = {}
        if name is not None:
            macro["title"] = name
        if actions is not None:
            macro["actions"] = actions
        data = self._write("PUT", f"/macros/{macro_id}.json", {"macro": macro})
        return data.get("macro", {}) if isinstance(data, dict) else {}

    @staticmethod
    def from_settings() -> "ZendeskClient | None":
        sub, email, key, view = ZendeskClient.load_credentials()
        if not (sub and email and key):
            return None
        return ZendeskClient(sub, email, key, view)

    # ── cursor persistence helpers ──────────────────────────

    @staticmethod
    def save_cursor(cursor: str) -> bool:
        """Persist the incremental export cursor."""
        from src.data.pat_store import save_setting
        return save_setting("zendesk_cursor", cursor)

    @staticmethod
    def load_cursor() -> str:
        """Load the persisted cursor, or empty string."""
        from src.data.pat_store import load_setting
        return load_setting("zendesk_cursor", "")

    @staticmethod
    def load_credentials() -> tuple[str, str, str, str]:
        """Load (subdomain, email, api_key, view_id) from pat_store."""
        from src.data.pat_store import load_setting
        return (
            load_setting("zendesk_subdomain", ""),
            load_setting("zendesk_email", ""),
            load_setting("zendesk_api_key", ""),
            load_setting("zendesk_view_id", ""),
        )

    @staticmethod
    def save_credentials(subdomain: str, email: str, api_key: str,
                         view_id: str = "") -> bool:
        """Persist Zendesk credentials."""
        from src.data.pat_store import save_setting
        ok1 = save_setting("zendesk_subdomain", subdomain)
        ok2 = save_setting("zendesk_email", email)
        ok3 = save_setting("zendesk_api_key", api_key)
        ok4 = save_setting("zendesk_view_id", view_id)
        return ok1 and ok2 and ok3 and ok4

    @staticmethod
    def load_trc_field() -> str:
        """Load the configured TRC field mapping, default 'subject'."""
        from src.data.pat_store import load_setting
        return load_setting("zendesk_trc_field", "subject")

    @staticmethod
    def save_trc_field(field: str) -> bool:
        """Persist the TRC field mapping."""
        from src.data.pat_store import save_setting
        return save_setting("zendesk_trc_field", field)
