"""Zendesk transport is one-way (import-only) — locked policy.

The Zendesk Guide instance is a public domain and its content is not
restorable the way the internal tools are, so ``ZendeskClient`` must be
structurally incapable of writing: no write methods exist at all, and the
single request-building choke point refuses anything that is not a GET.

These tests are the enforcement.  If one fails, a write path was
reintroduced — fix the caller, do not relax the assertion.
"""

import base64
import inspect
import json

import pytest

import src.data.zendesk_client as zc
from src.data.zendesk_client import ZendeskClient, ZendeskWriteBlocked


SUBDOMAIN = "acme"
EMAIL = "agent@acme.com"
API_KEY = "key123"

# Every method removed under the one-way policy.
_FORBIDDEN_ATTRS = ("_write", "create_article", "update_article",
                    "create_macro", "update_macro")

_SENTINEL = object()


class _Resp:
    """Minimal urlopen context-manager stand-in."""

    def __init__(self, payload):
        self._p = json.dumps(payload).encode()

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# A payload that satisfies every read method's key lookup at once, so one
# handler can serve the whole surface.
_ANY_PAYLOAD = {
    "user": {"id": 1},
    "tickets": [],
    "end_of_stream": True,
    "after_cursor": "",
    "ticket": {"id": 1},
    "ticket_fields": [],
    "articles": [],
    "article": {"id": 1},
    "results": [],
    "sections": [],
    "categories": [],
    "macros": [],
    "macro": {"id": 1},
    "count": 0,
    "next_page": None,
}


@pytest.fixture()
def client():
    return ZendeskClient(SUBDOMAIN, EMAIL, API_KEY)


@pytest.fixture()
def seen(monkeypatch):
    """Capture every urllib Request the client hands to urlopen."""
    captured = []

    def _urlopen(req, timeout=0):
        captured.append(req)
        return _Resp(_ANY_PAYLOAD)

    monkeypatch.setattr(zc.urllib.request, "urlopen", _urlopen)
    return captured


# ── the write surface is gone, not stubbed ───────────────────────────

class TestWriteMethodsDeleted:
    def test_class_has_no_write_attributes(self):
        for name in _FORBIDDEN_ATTRS:
            assert getattr(ZendeskClient, name, _SENTINEL) is _SENTINEL, (
                f"ZendeskClient.{name} exists — the Zendesk write path was "
                f"reintroduced")

    def test_instance_has_no_write_attributes(self, client):
        for name in _FORBIDDEN_ATTRS:
            assert getattr(client, name, _SENTINEL) is _SENTINEL
            assert not hasattr(client, name)

    def test_write_names_absent_from_module_source(self):
        src = inspect.getsource(zc)
        # Names may only survive inside prose/comments explaining the ban;
        # they must not appear as definitions.
        for name in _FORBIDDEN_ATTRS:
            assert f"def {name}(" not in src, (
                f"a definition of {name} came back")

    def test_module_never_sends_a_request_body(self):
        src = inspect.getsource(zc)
        assert "data=data" not in src
        assert "json.dumps" not in src, (
            "a JSON request body implies a write path")

    def test_no_non_get_method_literal_is_passed_to_urllib(self):
        src = inspect.getsource(zc)
        for verb in ("POST", "PUT", "PATCH", "DELETE"):
            assert f'method="{verb}"' not in src
            assert f"method='{verb}'" not in src


# ── the choke point ──────────────────────────────────────────────────

class TestRequestGuard:
    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE",
                                        "HEAD", "OPTIONS"])
    def test_guard_raises_on_non_get(self, client, method):
        with pytest.raises(ZendeskWriteBlocked):
            client._build_request("/macros.json", method=method)

    @pytest.mark.parametrize("method", ["post", "Put", "dElEtE"])
    def test_guard_is_case_insensitive(self, client, method):
        with pytest.raises(ZendeskWriteBlocked):
            client._build_request("/macros.json", method=method)

    def test_guard_message_names_method_and_host(self, client):
        with pytest.raises(ZendeskWriteBlocked) as exc:
            client._build_request("/macros.json", method="post")
        msg = str(exc.value)
        assert "POST" in msg
        assert f"{SUBDOMAIN}.zendesk.com" in msg
        assert exc.value.method == "POST"

    def test_guard_rejects_absolute_url_writes_too(self, client):
        with pytest.raises(ZendeskWriteBlocked):
            client._build_request(
                f"https://{SUBDOMAIN}.zendesk.com/api/v2/macros.json",
                method="PUT")

    def test_blocked_is_a_runtime_error(self):
        assert issubclass(ZendeskWriteBlocked, RuntimeError)

    def test_guard_never_reaches_the_network(self, client, seen):
        with pytest.raises(ZendeskWriteBlocked):
            client._build_request("/macros.json", method="POST")
        assert seen == []


class TestGetStillBuildsCorrectly:
    def test_get_builds_an_authenticated_bodyless_get(self, client):
        req = client._build_request("/tickets/9.json")
        assert req.get_method() == "GET"
        assert req.data is None
        assert req.full_url == (
            f"https://{SUBDOMAIN}.zendesk.com/api/v2/tickets/9.json")

        expected = base64.b64encode(
            f"{EMAIL}/token:{API_KEY}".encode()).decode()
        assert req.get_header("Authorization") == f"Basic {expected}"
        assert req.get_header("Accept") == "application/json"

    def test_absolute_urls_pass_through_unchanged(self, client):
        url = f"https://{SUBDOMAIN}.zendesk.com/api/v2/macros.json?page=2"
        assert client._build_request(url).full_url == url

    def test_get_parses_the_response(self, client, seen):
        assert client.get_macro(7) == {"id": 1}
        assert len(seen) == 1


# ── every reachable call is a GET ────────────────────────────────────

def _exercise_every_read(client):
    """Call every read the client exposes."""
    client.test_connection()
    client.fetch_incremental()
    client.fetch_incremental(cursor="abc")
    client.fetch_incremental(start_time=1700000000)
    client.fetch_view_tickets(view_id="55")
    client.fetch_ticket(9)
    client.fetch_ticket_fields()
    client.get_articles()
    client.get_articles_paged()
    client.get_article(3)
    client.search_articles("sso reset")
    client.get_sections()
    client.get_sections_paged()
    client.get_categories()
    client.get_categories_paged()
    client.list_macros()
    client.list_macros_paged()
    client.get_macro(11)


class TestEveryRequestIsAGet:
    def test_all_reads_issue_only_get_requests(self, client, seen):
        _exercise_every_read(client)

        assert seen, "no requests were captured — the harness is broken"
        for req in seen:
            assert req.get_method() == "GET", (
                f"non-GET request to {req.full_url}")
            assert req.data is None, (
                f"request body sent to {req.full_url}")

    def test_all_reads_stay_on_the_configured_subdomain(self, client, seen):
        _exercise_every_read(client)
        for req in seen:
            assert req.full_url.startswith(
                f"https://{SUBDOMAIN}.zendesk.com/")

    def test_no_public_method_can_be_coerced_into_a_write(self, client,
                                                          monkeypatch):
        """If the guard is the only gate, forcing it shut kills every call."""
        def _boom(*a, **k):
            raise ZendeskWriteBlocked("POST", "https://x.zendesk.com/")

        monkeypatch.setattr(ZendeskClient, "_build_request", _boom)

        # Reads that swallow exceptions degrade; reads that do not raise.
        assert client.test_connection() is False
        assert client.fetch_ticket(1) is None
        assert client.fetch_ticket_fields() == []
        with pytest.raises(ZendeskWriteBlocked):
            client.get_articles()
        with pytest.raises(ZendeskWriteBlocked):
            client.list_macros()


# ── credential statics untouched ─────────────────────────────────────

class TestCredentialStaticsSurvive:
    def test_statics_are_still_present(self):
        for name in ("from_settings", "save_cursor", "load_cursor",
                     "load_credentials", "save_credentials",
                     "load_trc_field", "save_trc_field", "extract_trc"):
            assert callable(getattr(ZendeskClient, name))

    def test_is_configured_still_reports_credential_state(self, client):
        assert client.is_configured is True
        assert ZendeskClient("", "", "").is_configured is False
        assert client.source_name == "zendesk"
