"""GuruClient analytics endpoints — pagination, 429 backoff, shaping.

urlopen is faked at the urllib layer so the real request/auth/Link-header
plumbing in _request_raw/_paged_get is what's under test.
"""

import io
import json
import urllib.error
from email.message import Message

import pytest

from src.data.guru_client import GuruClient


class _FakeResp:
    def __init__(self, payload, headers=None):
        self._payload = json.dumps(payload).encode("utf-8")
        self.headers = Message()
        for k, v in (headers or {}).items():
            self.headers[k] = v

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture()
def client():
    return GuruClient("user@alma.com", "token-123")


def _install(monkeypatch, handler):
    import src.data.guru_client as gc
    monkeypatch.setattr(gc.urllib.request, "urlopen",
                        lambda req, timeout=0: handler(req))
    monkeypatch.setattr(gc.time, "sleep", lambda _s: None)


class TestPagination:
    def test_follows_link_next(self, monkeypatch, client):
        seen = []

        def handler(req):
            seen.append(req.full_url)
            if "page2" in req.full_url:
                return _FakeResp([{"type": "card-viewed", "eventDate": "d3"}])
            return _FakeResp(
                [{"type": "card-viewed", "eventDate": "d1"},
                 {"type": "card-copied", "eventDate": "d2"}],
                {"Link": '<https://api.getguru.com/api/v1/page2>; rel="next"'},
            )

        _install(monkeypatch, handler)
        events = client.get_analytics("team-1", from_date="2026-05-01")
        assert len(events) == 3
        assert seen[0].endswith("fromDate=2026-05-01") or "fromDate" in seen[0]
        assert seen[1] == "https://api.getguru.com/api/v1/page2"

    def test_page_cap(self, monkeypatch, client):
        def handler(req):
            return _FakeResp(
                [{"type": "t", "eventDate": "d"}],
                {"Link": '<https://api.getguru.com/api/v1/again>; rel="next"'},
            )

        _install(monkeypatch, handler)
        events = client.get_analytics("team-1", max_pages=3)
        assert len(events) == 3  # one per page, capped


class TestBackoff:
    def test_retries_once_on_429(self, monkeypatch, client):
        calls = {"n": 0}

        def handler(req):
            calls["n"] += 1
            if calls["n"] == 1:
                raise urllib.error.HTTPError(
                    req.full_url, 429, "rate limited", Message(),
                    io.BytesIO(b"slow down"),
                )
            return _FakeResp([{"type": "card-viewed", "eventDate": "d1"}])

        _install(monkeypatch, handler)
        events = client.get_analytics("team-1")
        assert len(events) == 1
        assert calls["n"] == 2


class TestShaping:
    def test_unverified_cards_shape(self, monkeypatch, client):
        payload = [{
            "id": "c1", "preferredPhrase": "SSO Setup",
            "verificationState": "NEEDS_VERIFICATION",
            "nextVerificationDate": "2026-06-20T00:00:00+0000",
            "verificationInterval": "90 days",
            "lastVerified": "2026-03-01T00:00:00+0000",
            "lastModified": "2026-05-01T00:00:00+0000",
            "commentCount": 2,
            "collection": {"id": "co1", "name": "Provider Enablement"},
        }]
        _install(monkeypatch, lambda req: _FakeResp(payload))
        cards = client.list_unverified_cards()
        assert cards == [{
            "id": "c1", "title": "SSO Setup",
            "verification_state": "NEEDS_VERIFICATION",
            "verification_reason": "",
            "next_verification_date": "2026-06-20T00:00:00+0000",
            "verification_interval": "90 days",
            "last_verified": "2026-03-01T00:00:00+0000",
            "last_modified": "2026-05-01T00:00:00+0000",
            "comment_count": 2,
            "collection": "Provider Enablement", "collection_id": "co1",
        }]

    def test_comments_shape(self, monkeypatch, client):
        payload = [{
            "id": "cm1", "content": "Stale screenshot",
            "owner": {"email": "j@alma.com", "firstName": "J", "lastName": "D"},
            "dateCreated": "2026-06-01T00:00:00+0000", "status": "OPEN",
        }, {
            "id": "cm2", "content": "No owner email",
            "owner": {"firstName": "Sam", "lastName": "Lee"},
            "dateCreated": "2026-06-02T00:00:00+0000", "status": "OPEN",
        }]
        _install(monkeypatch, lambda req: _FakeResp(payload))
        comments = client.get_card_comments("c1", status="OPEN")
        assert comments[0]["author"] == "j@alma.com"
        assert comments[1]["author"] == "Sam Lee"
        assert comments[0]["content"] == "Stale screenshot"

    def test_team_id_from_whoami(self, monkeypatch, client):
        _install(monkeypatch,
                 lambda req: _FakeResp({"team": {"id": "team-42"}}))
        assert client.get_team_id() == "team-42"
